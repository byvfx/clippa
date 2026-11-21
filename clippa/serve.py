"""`clippa serve`: the always-on process. It serves the war room (the clippa/warroom package) and
runs the background jobs that used to ride inside the Discord bot, so the bot is only
Discord and this can move to the NAS on its own (#12, #64).

    clippa serve [--port 8765]

Jobs (`jobs()`; each runs `clippa ...` as a child process; an interval of 0 turns one off):
    upload     every [youtube] auto_upload_minutes: `clippa schedule`, then `clippa upload`
    preshow    every [preshow] poll_minutes: `clippa preshow check --run`, for shows with a
               pre-show channel
    retake     with [retake] auto: every 10 min when review.json (as last synced) has retake
               notes not acted on, and every 30 min regardless, since `clippa retake` syncs
               Kitsu itself and finds notes nothing else synced; at once when the war room or
               the bot asks (POST /api/retake, which runs even with auto off)
    auto       every 10 min while a show has [review] auto_approve: `clippa review auto` (#259), which
               approves what nobody acted on in the grace window and replans
    tiktok     every [tiktok] auto_send_minutes: `clippa tiktok --auto` (when signed in)
    x          every [x] auto_post_minutes: `clippa x --auto` (when signed in)
    analytics  every [analytics] auto_hours, once something is uploaded
    cast       every [cast] auto_hours: `clippa cast auto` (needs OpenCV and [thumbnail] photos)
    news       every [news] check_minutes: `clippa news check` (the bot posts what it finds)
    blinds     every [blinds] check_minutes: `clippa blinds check` (the bot asks you about them)
    backup     `clippa backup` when the newest zip is [backup] every_days old (looked at hourly)
    prune      daily with [streams] keep_source_days: `clippa streams prune --yes --older-than N`
               (and `--while-free-below P` with [streams] keep_free_pct)
    checkup    every [checkup] fix_minutes: `clippa checkup --fix --alert` (#287) over the checks
               that have repairs (the ones [checkup] fix lists act, the others are logged as
               would-fix) and signins: a sign-in that is gone is DMed once, not left for the morning
A job that exits non-zero is queued as a DM (clippa/alerts.py; the bot sends it). One marked
retry (analytics, prune: free, and a day apart) that fails runs again in RETRY_FAILED_S, doubling
with each failure in a row, up to its own interval; the others wait their interval.

Every minute it writes <streams>/.serve/status.json (clippa/daemon.py): what ran last,
what is running, what is due next. `clippa services status`, the war room's Health panel
and `clippa doctor` read it from any machine. One serve per streams folder (.serve.lock).
It logs to [paths] serve_log; deploy/hub runs it as a container.

The bot reaches it at [serve] url ("" = http://127.0.0.1:<[warroom] port>/) with the war
room token (the same [warroom] token or secrets file on both machines when they differ):
`replan` after a decision, `start_retake` after "Changes...", `health` for the tailnet link.
On different machines the bot still reads schedule.json and the news database that serve
writes, so [paths] schedule and news must be on the streams share (`clippa doctor --role
bot` checks).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import NamedTuple

import requests

from . import (
    actions,
    alerts,
    checkup,
    daemon,
    news,
    preshow,
    repairs,
    retake,
    review_auto,
    warroom,
)
from .config import base
from .errors import ClipperError

log = logging.getLogger("clippa.serve")

RETAKE_EVERY_S = 600   # with [retake] auto: act on notes review.json already has this often
RETAKE_SYNC_S = 1800   # ...and run `clippa retake` anyway this often: it syncs Kitsu first
FIRST_PASS_S = 30      # settle (the war room warms up) before the first job
RETRY_FAILED_S = 1800  # a retry job that failed: again this soon, then 1 h, 2 h, ... up to its interval


def backup_due(cfg: dict, now: float | None = None) -> bool:
    """No backup zip yet, or the newest is [backup] every_days old."""
    from . import backup

    p = backup.newest(cfg)
    return p is None or (now or time.time()) - p.stat().st_mtime >= cfg["backup"]["every_days"] * 86400


def checkup_args() -> list[str]:
    """The checkup job's command: the checks that have a repair (--fix, #287) and the ones whose
    bad findings can't wait for the morning report (--alert: a sign-in that is gone)."""
    names = dict.fromkeys([*repairs.checks(), *checkup.ALERT_CHECKS])
    return ["checkup", "--fix", "--alert", *(a for c in names for a in ("--only", c))]


def has_opencv() -> bool:
    """`cast auto` checks faces with OpenCV (the faces extra)."""
    import importlib.util

    return importlib.util.find_spec("cv2") is not None


class Job(NamedTuple):
    """A background job: started when its interval has passed and ready() says so."""
    name: str
    every_s: float                 # 0 or less: never on its own (POST /api/retake still may)
    ready: Callable[[], bool]
    start: Callable[[], object]
    # After a failure, sooner than every_s (interval()). Only for a job that is free to run and
    # safe to repeat: a failing cast auto retried every half hour would spend each time.
    retry: bool = False


def interval(job: Job, fails: int) -> float:
    """How long after its last start the job is due: every_s, or for a retry job that failed
    the last `fails` runs in a row, RETRY_FAILED_S doubled per failure after the first, never
    more than every_s (so one that keeps failing settles back to its own pace)."""
    if not (job.retry and fails > 0 and job.every_s > 0):
        return job.every_s
    return min(job.every_s, RETRY_FAILED_S * 2 ** min(fails - 1, 20))


def due(jobs: list[Job], last: dict[str, float], now: float, fails: dict[str, int] | None = None) -> list[str]:
    """Names of the jobs whose interval has passed since they last started (an interval of
    0 or less: never). fails: job -> failed runs in a row, for interval()."""
    fails = fails or {}
    return [j.name for j in jobs
            if j.every_s > 0 and now - last.get(j.name, float("-inf")) >= interval(j, fails.get(j.name, 0))]


class Serve:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.procs: dict[str, asyncio.subprocess.Process] = {}
        self.last: dict[str, float] = {}   # job -> monotonic time it last started
        self.state: dict[str, dict] = {}   # job -> running, started, last_at, last_exit, last_line
        self.retake_again = False
        self.started_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.loop: asyncio.AbstractEventLoop | None = None

    def running(self, name: str) -> bool:
        proc = self.procs.get(name)
        return bool(proc and proc.returncode is None)

    def since(self, name: str) -> float:
        """Seconds since the job last started (infinite if never)."""
        return time.monotonic() - self.last.get(name, float("-inf"))

    def jobs(self) -> list[Job]:
        """The background jobs, each with its interval, its ready() check and its start()."""
        cfg, paths = self.cfg, self.cfg["paths"]
        has_upload = lambda: paths["schedule"].exists() \
            and '"uploaded"' in paths["schedule"].read_text(encoding="utf-8")
        task = lambda coro: self.loop.create_task(coro)
        return [
            Job("upload", cfg["youtube"]["auto_upload_minutes"] * 60,
             lambda: not self.running("upload"), lambda: task(self.auto_upload())),
            Job("preshow", cfg["preshow"]["poll_minutes"] * 60,
             lambda: not self.running("preshow") and bool(preshow.watched(cfg)),
             lambda: task(self.run_clipper("preshow", ["preshow", "check", "--run"]))),
            # retake notes made in Kitsu: as review.json has them (the bot's card refresh and the
            # replan sync it), and every RETAKE_SYNC_S the command's own sync, with or without a bot
            Job("retake", RETAKE_EVERY_S if cfg["retake"]["auto"] else 0,
             lambda: bool(retake.pending(cfg)) or (self.since("retake") >= RETAKE_SYNC_S
                                                   and bool(actions.review_dirs(cfg))),
             self.start_retake),
            # #259 B: approval by silence; the command syncs Kitsu for what is due before it acts
            Job("auto", review_auto.EVERY_S if review_auto.enabled_anywhere(cfg) else 0,
             lambda: not self.running("auto") and bool(actions.review_dirs(cfg)),
             lambda: task(self.run_clipper("auto", ["review", "auto"]))),
            Job("tiktok", cfg["tiktok"]["auto_send_minutes"] * 60,
             lambda: paths["tiktok_token"].exists() and not self.running("tiktok"),
             lambda: task(self.run_clipper("tiktok", ["tiktok", "--auto"]))),
            Job("x", cfg["x"]["auto_post_minutes"] * 60,
             lambda: paths["x_token"].exists() and not self.running("x"),
             lambda: task(self.run_clipper("x", ["x", "--auto"]))),
            Job("analytics", cfg["analytics"]["auto_hours"] * 3600,
             lambda: paths["youtube_token"].exists() and has_upload() and not self.running("analytics"),
             lambda: task(self.run_clipper("analytics", ["analytics"])), retry=True),
            Job("cast", cfg["cast"]["auto_hours"] * 3600,  # #41: photos for anyone short of them
             lambda: bool(cfg["thumbnail"]["photos"]) and has_opencv() and not self.running("cast"),
             lambda: task(self.run_clipper("cast", ["cast", "auto"]))),
            Job("news", cfg["news"]["check_minutes"] * 60,  # #110: headlines about the watched terms
             lambda: not self.running("news") and bool(news.watchlist(cfg)),  # no terms: nothing to do
             lambda: task(self.run_clipper("news", ["news", "check"]))),
            Job("blinds", cfg["blinds"]["check_minutes"] * 60,  # #111: gossip blind items, the clues read
             lambda: not self.running("blinds") and bool(cfg["blinds"]["sources"]),
             lambda: task(self.run_clipper("blinds", ["blinds", "check"]))),
            # #204: looks hourly, makes one when the newest is every_days old (a restart makes none)
            Job("backup", 3600 if cfg["backup"]["every_days"] and cfg["backup"]["folder"] else 0,
             lambda: not self.running("backup") and backup_due(cfg),
             lambda: task(self.run_clipper("backup", ["backup"]))),
            Job("prune", 86400 if cfg["streams"]["keep_source_days"] else 0,  # #204: source and audio
             lambda: not self.running("prune"),
             lambda: task(self.run_clipper("prune", [
                 "streams", "prune", "--yes", "--older-than", str(cfg["streams"]["keep_source_days"]),
                 *(["--while-free-below", str(cfg["streams"]["keep_free_pct"])]
                   if cfg["streams"]["keep_free_pct"] else [])])),
             retry=True),
            Job("checkup", cfg["checkup"]["fix_minutes"] * 60,  # #287: no model; repairs are capped
             lambda: not self.running("checkup"),
             lambda: task(self.run_clipper("checkup", checkup_args()))),
        ]

    async def run_clipper(self, name: str, args: list[str], streak: bool = True) -> int | None:
        """Run `clippa <args>` as a child process, its output into the serve log. streak:
        this one call is the job's run, so it counts toward its failures in a row (a job of
        several commands passes False and calls count_run itself, once)."""
        cmd = actions.clipper_cmd(self.cfg)
        log.info(f"{name}: starting")
        st = self.state.setdefault(name, {})
        st.update(running=True, started=datetime.now(UTC).isoformat(timespec="seconds"), args=args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
            self.procs[name] = proc
            out, _ = await proc.communicate()
            lines = [x for x in out.decode("utf-8", "replace").splitlines()
                     if x.strip() and not x.startswith("[")]  # skip ffmpeg decoder noise
            for line in lines:
                log.info(f"{name}: {line}")
            log.info(f"{name}: done (exit {proc.returncode})")
            st.update(running=False, last_at=datetime.now(UTC).isoformat(timespec="seconds"),
                      last_exit=proc.returncode, last_line=(lines[-1] if lines else "")[:200])
            if streak:
                self.count_run(name, ok=not proc.returncode)
            if proc.returncode:  # #22: a background job failing is worth a DM (once per cooldown)
                last = lines[-1].strip() if lines else ""
                # Keyed on the last line too, verbatim (item keys carry their digits), so a
                # different item failing the next hour isn't swallowed by this one's cooldown.
                alerts.send(self.cfg, "upload" if name in ("upload", "tiktok", "x") else "failure",
                            f"`clippa {' '.join(args)}` failed (exit {proc.returncode}): {last[:400]}",
                            key=f"bg:{' '.join(args)}:{last[:160]}")
            return proc.returncode
        except Exception as e:
            log.exception(f"{name} run failed")
            st.update(running=False, last_at=datetime.now(UTC).isoformat(timespec="seconds"),
                      last_exit=None, last_line=f"{type(e).__name__}: {e}"[:200])
            if streak:
                self.count_run(name, ok=False)
            return None

    def count_run(self, name: str, ok: bool) -> None:
        """One run of the job ended: its failures in a row go up by one, or back to 0."""
        st = self.state.setdefault(name, {})
        st["fails"] = 0 if ok else st.get("fails", 0) + 1

    async def auto_upload(self) -> None:
        """Replan first (slots that passed get new ones), then upload what's due. A failed
        replan (Kitsu down) is DMed on its own and the upload still runs on the last plan,
        so one outage doesn't miss every slot that hour."""
        replan = await self.run_clipper("upload", ["schedule"], streak=False)
        up = await self.run_clipper("upload", ["upload"], streak=False)
        self.count_run("upload", ok=replan == 0 and up == 0)  # one run of the job, not two

    def start_retake(self) -> None:
        """`clippa retake` in the background: act on retake notes, republish for review.
        A request while one runs makes it run once more when it ends. The timer and the
        bot check [retake] auto; an explicit POST /api/retake runs either way."""
        if self.running("retake"):
            self.retake_again = True
            return
        self.last["retake"] = time.monotonic()
        self.loop.create_task(self._retake())

    async def _retake(self) -> None:
        await self.run_clipper("retake", ["retake"])
        if self.retake_again:
            self.retake_again = False
            self.start_retake()

    async def jobs_loop(self) -> None:
        """Start each job when it's due and ready, once a minute."""
        await asyncio.sleep(FIRST_PASS_S)
        while True:
            jobs = self.jobs()
            now_due = set(due(jobs, self.last, time.monotonic(), self.fails()))
            for job in jobs:
                if job.name not in now_due:
                    continue
                try:
                    if not await asyncio.to_thread(job.ready):
                        continue
                    self.last[job.name] = time.monotonic()
                    job.start()
                except (Exception, SystemExit):  # SystemExit would stop the event loop
                    log.exception(f"{job.name} check failed")
            await asyncio.sleep(60)

    def fails(self) -> dict[str, int]:
        """Job -> how many of its last runs failed in a row (0 after a run that exits 0)."""
        return {name: st.get("fails", 0) for name, st in self.state.items()}

    def status(self) -> dict:
        """Each job's state for the heartbeat: running, last run, next due."""
        now, out, fails = time.monotonic(), {}, self.fails()
        for job in self.jobs():
            name, every = job.name, job.every_s
            st = dict(self.state.get(name, {}))
            st["every_s"] = every
            if every <= 0:
                st["next_in_s"] = None
            elif name in self.last:
                st["next_in_s"] = max(0.0, interval(job, fails.get(name, 0)) - (now - self.last[name]))
            else:
                st["next_in_s"] = 0.0
            out[name] = st
        return out

    async def beat_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(daemon.beat, self.cfg, "serve", self.started_at, jobs=self.status(),
                                        warroom=warroom.url(self.cfg, with_key=False),
                                        tailnet=warroom.TAILNET["served"])
            except Exception:
                log.exception("heartbeat failed")
            await asyncio.sleep(daemon.BEAT_S)

    async def main(self) -> None:
        self.loop = asyncio.get_running_loop()
        # The war room's "changes" decision and POST /api/retake start a retake here
        # (one runner, so a second request while one runs queues one more).
        warroom.HOOKS["retake"] = lambda: self.loop.call_soon_threadsafe(self.start_retake)
        await asyncio.gather(self.jobs_loop(), self.beat_loop())


def run(cfg: dict, port: int | None = None) -> None:
    daemon.setup_logging(cfg["paths"]["serve_log"])
    try:
        lock = daemon.single_instance(cfg, "serve")
    except ClipperError as e:
        log.error(str(e))
        raise
    log.info(f"clippa serve starting (pid {os.getpid()})")
    try:
        srv = warroom.server(cfg, port)
    except OSError as e:
        raise ClipperError(f"war room: port {port or base(cfg)['warroom']['port']} is busy ({e}); is "
                           "`clippa warroom`, or a `clippa bot` from before the split, still serving it?") from None
    threading.Thread(target=srv.serve_forever, daemon=True, name="warroom").start()
    log.info(f"war room on {warroom.url(cfg, port, with_key=False)}")  # no token in the log
    try:
        asyncio.run(Serve(cfg).main())
    except KeyboardInterrupt:
        pass
    except BaseException:
        log.exception("clippa serve stopped")
        raise
    finally:
        srv.shutdown()
        lock.close()
    log.info("clippa serve stopped")


# ---------- the client: the bot (and doctor) talking to a running serve ----------

def url(cfg: dict) -> str:
    """[serve] url, else the war room on this machine. Always ends in /."""
    root = base(cfg)
    u = (root["serve"]["url"] or "").strip() or f"http://127.0.0.1:{root['warroom']['port']}/"
    return u.rstrip("/") + "/"


def call(cfg: dict, path: str, body: dict | None = None, timeout: float = 60) -> dict:
    """GET (body None) or POST one of the war room's /api routes on the running serve.
    ClipperError when it isn't up or refuses."""
    u = url(cfg) + path.lstrip("/")
    headers = {"X-Key": warroom.token(cfg)}
    try:
        if body is None:
            r = requests.get(u, headers=headers, timeout=timeout)
        else:
            r = requests.post(u, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        raise ClipperError(f"clippa serve isn't answering at {url(cfg)} ({type(e).__name__})") from None
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
        why = data.get("error", "") if isinstance(data, dict) else ""
        raise ClipperError(f"clippa serve {path}: {why or 'refused'} (HTTP {r.status_code})")
    return data


def health(cfg: dict) -> dict:
    return call(cfg, "/api/health", timeout=15)


def tailnet_link(cfg: dict) -> str:
    """The war room's https tailnet URL, or "" (serve not up, or not on the tailnet)."""
    try:
        return str(health(cfg).get("tailnet") or "")
    except ClipperError:
        return ""


def replan(cfg: dict, sync: bool = False) -> dict:
    """`clippa schedule` on the serve process (it owns schedule.json); sync: read Kitsu first."""
    return call(cfg, "/api/replan", {"sync": sync}, timeout=600)


def start_retake(cfg: dict) -> dict:
    return call(cfg, "/api/retake", {}, timeout=15)
