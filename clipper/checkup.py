"""`clipper checkup` (#185): what the alerts don't catch, from the files the services write.

The alerts (clipper/alerts.py) DM single events: a stage failing after its retries, a crash,
the recorder stalling. A problem that never raises an error stays quiet there: a stage that
hangs, a service that stops beating, a recording the worker never starts, a release that
misses its upload, a title that breaks the house rules. This looks for those:

    services   serve's, the bot's and the recorder's heartbeats
    streams    a stage running past [checkup] stuck_hours; a run whose worker stopped
               beating; a finished recording waiting queue_hours while nothing runs; a run
               that failed with no retries left
    schedule   a release not uploaded late_hours before its slot (or past it); no release
               at all in the next gap_hours
    lint       what is about to go out: [checkup] words in a title, description or
               thumbnail text, no description, a title or description over YouTube's limit
    auto       an item that approved itself (#259) and that a person then undid
    housekeeping  temp files a killed write left behind; the share's free space
    tools      yt-dlp older than ytdlp_days
    signins    a cookies file's sign-in gone or expiring, a TikTok or X sign-in expiring
    logs       the same error repeating in serve's or the bot's log
    approvals  an approved item the plan never saw; a plan serve stopped replanning

--fix (#287, clipper/repairs.py) repairs what it can from a short allowlist and logs it; a run
without it still says, per finding, what a repair would do. No model, no network; nothing
written unless --mark or --fix.

Each finding has a stable key; --mark
keeps the keys in <streams>/.checkup/state.json, so the next run can say which are new (the
claude container's nightly run uses it, deploy/claude/checkup.sh). Exit 1 when it finds anything.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import daemon, jsonstate, repairs, services, streams, worker
from .config import for_stream
from .errors import ClipperError

BAD, WARN = "bad", "warn"


def finding(key: str, check: str, level: str, text: str, look: str = "", stream: str = "") -> dict:
    """look: where to read more, or the command that fixes it."""
    return {"key": key, "check": check, "level": level, "text": text, "look": look, "stream": stream}


def _when(iso: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(iso or "")
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()   # a naive time is this machine's


def _age_s(iso: str | None, now: datetime) -> float:
    t = _when(iso)
    return (now - t).total_seconds() if t else float("inf")


def _hours(s: float) -> str:
    return f"{s / 3600:.1f} h" if s >= 3600 else f"{s / 60:.0f} min"


# ---------- the checks ----------

def check_services(cfg: dict, now: datetime) -> list[dict]:
    out = []
    for name in ("serve", "bot"):
        hb = daemon.heartbeat(cfg, name, now)
        level, text = daemon.state(hb)
        if level != "ok":
            why = " (the bot sends the DMs: none arrive while it is down)" if name == "bot" else ""
            out.append(finding(f"service:{name}", "services", BAD, f"{name}: {text}{why}",
                               look="clipper services status (laptop); the container's logs"))
    rec = services.recorder(cfg, now)
    if rec.get("error") or rec["age_s"] is None or not rec["up"]:
        out.append(finding("service:recorder", "services", BAD, services.recorder_lines(rec)[0],
                           look="clipper record status; deploy/record (only when nothing is live)"))
    return out


def _running_stage(run: dict) -> tuple[str, dict]:
    return next(((s, st) for s, st in run.get("stages", {}).items() if st.get("status") == "running"),
                ("", {}))


def _waiting_since(d: Path, run: dict) -> str:
    """When a queued stream became ready: its run's last write, else the end of the recording."""
    if run.get("updated"):
        return run["updated"]
    meta = jsonstate.read_or(d / "meta.json", {})
    if meta.get("ended_at"):
        return meta["ended_at"]
    for f in (d / "live" / "state.json", d / "meta.json"):
        if f.exists():
            return datetime.fromtimestamp(f.stat().st_mtime, UTC).isoformat(timespec="seconds")
    return ""


def check_streams(cfg: dict, now: datetime) -> list[dict]:
    """[worker] from the base config, as the worker reads it; [checkup] per stream (a show's or the
    stream's own config.toml may allow a longer stage)."""
    wc = cfg["worker"]
    root: Path = cfg["paths"]["streams"]
    if not root.is_dir():
        return [finding("streams:unreadable", "streams", BAD, f"can't read the streams folder {root}")]
    out, busy = [], False
    for d in sorted(p for p in root.iterdir() if (p / "run.json").exists()):
        run = jsonstate.read_or(d / "run.json", {})
        ck = for_stream(cfg, d)["checkup"]
        status = run.get("status")
        if status == "running":
            beat = _age_s(run.get("heartbeat"), now)
            stage, st = _running_stage(run)
            if beat < wc["stale_minutes"] * 60:
                busy = True
                took = _age_s(st.get("started"), now)
                if stage and took != float("inf") and took > ck["stuck_hours"] * 3600:
                    out.append(finding(f"stuck:{d.name}:{stage}", "streams", BAD,
                                       f"{d.name}: {stage} has been running for {_hours(took)} "
                                       f"(stuck_hours {ck['stuck_hours']}); the worker is still beating",
                                       look=f"streams/{d.name}/{st.get('log') or f'run_{stage}.log'}",
                                       stream=d.name))
            elif beat > 2 * wc["stale_minutes"] * 60:
                last = f"{_hours(beat)} ago" if beat != float("inf") else "never"
                out.append(finding(f"orphan:{d.name}", "streams", BAD,
                                   f"{d.name}: run.json says running ({stage or 'no stage'}) but the "
                                   f"worker's last heartbeat was {last}: it stopped mid-run",
                                   look="the worker container's logs; it takes the stream over on its next pass",
                                   stream=d.name))
        elif status == "failed" and run.get("key_rejected"):
            out.append(finding(f"key:{d.name}", "streams", BAD,
                               f"{d.name}: held, an API key was rejected: {run.get('error', '')[:300]}",
                               look="the key in deploy/hub/env.local (then deploy.sh); the worker "
                                    "retries by itself once a key changes", stream=d.name))
        elif status == "failed" and run.get("attempts", 0) >= wc["max_attempts"]:
            out.append(finding(f"failed:{d.name}", "streams", BAD,
                               f"{d.name}: failed with no retries left: {run.get('error', '')[:300]}",
                               look=f"streams/{d.name}/run_*.log; then `clipper run {d.name}`",
                               stream=d.name))
    if busy:
        return out   # one worker runs one stream at a time: a queue behind it is expected
    for d in worker.pending(cfg):
        run = jsonstate.read_or(d / "run.json", {})
        waited = _age_s(_waiting_since(d, run), now)
        ck = for_stream(cfg, d)["checkup"]
        if waited != float("inf") and waited > ck["queue_hours"] * 3600:
            out.append(finding(f"queued:{d.name}", "streams", BAD,
                               f"{d.name}: ready for the worker for {_hours(waited)} "
                               f"({run.get('status') or 'not started'}) and nothing is running",
                               look="the worker container (clipper services status worker)", stream=d.name))
    return out


def _entries(cfg: dict) -> list[dict]:
    path: Path = cfg["paths"]["schedule"]
    return jsonstate.load(path).get("entries", []) if path.exists() else []


def check_schedule(cfg: dict, now: datetime) -> list[dict]:
    ck = cfg["checkup"]
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    entries = [(e, _when(e.get("publish_at"))) for e in _entries(cfg)]
    out = []
    for e, t in entries:
        if t is None or e.get("status") == "uploaded":
            continue
        left = (t - now).total_seconds()
        if left < ck["late_hours"] * 3600:
            when = f"was due {_hours(-left)} ago" if left < 0 else f"releases in {_hours(left)}"
            out.append(finding(f"late:{e['key']}", "schedule", BAD,
                               f"{e['key']} ({e.get('kind')}, {t.astimezone(tz):%a %H:%M}) {when} "
                               f"and isn't uploaded: {e.get('title', '')[:80]}",
                               look="serve's upload job (clipper status); `clipper upload --next`",
                               stream=e.get("stream", "")))
    if ck["gap_hours"] and not any(t and now <= t <= now + timedelta(hours=ck["gap_hours"])
                                   for _, t in entries):
        later = min((t for _, t in entries if t and t > now), default=None)
        nxt = f"; the next is {later.astimezone(tz):%a %m-%d %H:%M}" if later else "; none scheduled at all"
        out.append(finding("schedule:gap", "schedule", WARN,
                           f"nothing releases in the next {ck['gap_hours']} h{nxt}",
                           look="clipper schedule --list; review counts in clipper status"))
    return out


def lint_text(words: list[str], fields: dict[str, str]) -> list[str]:
    """What breaks the house rules in an item's text: [checkup] words (whole words, any case)."""
    if not words:
        return []
    rx = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(w) for w in words) + r")(?![\w-])", re.IGNORECASE)
    return [f"{name} says {m.group(0)!r}" for name, text in fields.items() if (m := rx.search(text or ""))]


def check_lint(cfg: dict, now: datetime) -> list[dict]:
    from . import youtube

    out = []
    for e in _entries(cfg):
        t = _when(e.get("publish_at"))
        if e.get("status") == "uploaded" or t is None or t < now - timedelta(days=1):
            continue
        try:
            m = youtube.metadata(e, cfg)
        except (ClipperError, OSError, ValueError, KeyError) as err:
            out.append(finding(f"lint:{e['key']}:unreadable", "lint", BAD,
                               f"{e['key']}: its title and description can't be read: {err}",
                               stream=e.get("stream", "")))
            continue
        words = for_stream(cfg, m["stream_dir"])["checkup"]["words"]
        problems = lint_text(words, {"title": m["title"], "description": m["description"],
                                     "thumbnail text": m["thumb_text"]})
        problems += [f"{n} is over YouTube's limit and will be cut" for n in m["cut"]]
        if not m["description"].strip():
            problems.append("no description")
        if problems:
            out.append(finding(f"lint:{e['key']}", "lint", WARN,
                               f"{e['key']} ({t:%m-%d}): " + "; ".join(problems) + f": {m['title'][:80]}",
                               look="edit it in Kitsu (or the war room) before it uploads",
                               stream=e.get("stream", "")))
    return out


AUTO_LOOKBACK_DAYS = 14


def check_auto(cfg: dict, now: datetime) -> list[dict]:
    """An item that approved itself (#259 B) and that a person then rejected, asked to change,
    held or reopened: the rule let through something it shouldn't have."""
    from . import review_auto

    out = []
    for h in review_auto.history(cfg):
        t = _when(h["at"])
        if not h["undone"] or t is None or t < now - timedelta(days=AUTO_LOOKBACK_DAYS):
            continue
        out.append(finding(f"auto:{h['stream']}/{h['key']}", "auto", WARN,
                           f"{h['stream']}/{h['key']} approved itself {t:%m-%d} and was then {h['undone']}",
                           look="is the rule right? [review] auto_min_* and auto_grace_minutes "
                                "(wiki Review#approval-by-silence)", stream=h["stream"]))
    return out


def check_disk(cfg: dict) -> list[dict]:
    """The share's free space under [streams] keep_free_pct (off at 0): serve's daily prune takes
    streams older than keep_source_days while it is, and this says so without scanning a stream."""
    want = cfg["streams"]["keep_free_pct"]
    free = streams.free_pct(cfg) if want else None
    if free is None or free >= want:
        return []
    days = cfg["streams"]["keep_source_days"]
    look = (f"serve's daily prune removes source and audio older than {days:g} days, oldest first; "
            "`clipper streams list` shows what that frees" if days else
            "[streams] keep_source_days is 0, so nothing prunes by itself: `clipper streams prune`")
    return [finding("disk:free", "housekeeping", BAD if free < want / 2 else WARN,
                    f"{free:.1f}% of the share is free, under [streams] keep_free_pct {want:g}%", look=look)]


def check_housekeeping(cfg: dict, now: datetime) -> list[dict]:
    """Temp files a killed write left beside a state file (repairs.leftover_tmp), and a share
    running low on space (check_disk)."""
    out = check_disk(cfg)
    left = repairs.leftover_tmp(cfg, now.timestamp())
    if not left:
        return out
    oldest = datetime.fromtimestamp(min(left.values()), UTC)
    return [*out, finding("tmp:leftover", "housekeeping", WARN,
                          f"{len(left)} temp file{'s' if len(left) != 1 else ''} left by killed writes "
                          f"(oldest {oldest:%m-%d}, e.g. {next(iter(left)).name})",
                          look="safe to delete; `clipper checkup --fix` does with [checkup] fix = [\"tmp\"]")]


def ytdlp_version() -> str:
    """yt-dlp's installed version ("" = not installed), from its metadata: no import of yt-dlp."""
    from importlib import metadata

    try:
        return metadata.version("yt-dlp")
    except metadata.PackageNotFoundError:
        return ""


def check_tools(cfg: dict, now: datetime, version: str | None = None) -> list[dict]:
    """yt-dlp older than [checkup] ytdlp_days (0 = off): its version is its release date, and an
    old one is how fetch, pre-shows and archives break when YouTube changes. It is this install's
    yt-dlp (the nightly run's: the claude container's venv; serve's: the hub image)."""
    days = cfg["checkup"]["ytdlp_days"]
    v = ytdlp_version() if version is None else version
    m = re.match(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", v)
    if not days or not m:
        return []
    released = datetime(*map(int, m.groups()), tzinfo=UTC)
    age = (now - released).days
    if age <= days:
        return []
    return [finding("tools:yt-dlp", "tools", WARN,
                    f"yt-dlp {v} in the install this checkup ran in is {age} days old (ytdlp_days {days}): "
                    "YouTube and Kick downloads break as the sites change", look="`uv lock --upgrade-package yt-dlp` in a PR, then deploy the hub")]


# Each cookies file with a sign-in: (label, its [paths] key; "youtube" = ytdl.cookies_file).
SIGNINS = (("YouTube", "youtube"), ("Twitch", "twitch_cookies"), ("HEI", "hei_cookies"))


def _signin_cookies(label: str, rows: list) -> list:
    from . import hei, ytdl

    if label == "YouTube":
        return [c for c in ytdl.youtube_only(rows) if c.name in ytdl.SESSION]
    if label == "HEI":
        return [c for c in hei.site_cookies(rows) if c.name.startswith(hei.SIGNED_IN)]
    return [c for c in rows if "twitch.tv" in c.domain and c.name == "auth-token"]


def _cookie_findings(cfg: dict, now: datetime, warn_s: float) -> list[dict]:
    from . import doctor, ytdl

    out, ts = [], now.timestamp()
    for label, key in SIGNINS:
        if key == "youtube" and ytdl.browser(cfg):
            continue   # signed in through a browser profile instead: doctor checks that one
        f = ytdl.cookies_file(cfg) if key == "youtube" else cfg["paths"][key]
        if not f.is_file():
            continue   # optional: no file, no signed-in source to check
        look = f"sign in again and export {f.name} (wiki: Archives, Fetching-VODs)"
        try:
            signin = _signin_cookies(label, ytdl.read_cookies(f))
        except (OSError, ValueError, KeyError) as e:
            out.append(finding(f"signin:{label.lower()}", "signins", BAD, f"{label} cookies {f.name} unreadable: {e}",
                               look=look))
            continue
        live = ytdl.live(signin, ts)
        dated = [c.expires for c in live if c.expires is not None]
        if not live:
            out.append(finding(f"signin:{label.lower()}", "signins", BAD,
                               f"{label} cookies {f.name} hold no live sign-in", look=look))
        elif dated and min(dated) - ts < warn_s:
            out.append(finding(f"signin:{label.lower()}", "signins", WARN,
                               f"{label} cookies {f.name}: the sign-in expires "
                               f"{datetime.fromtimestamp(min(dated), UTC):%m-%d}", look=look))
        elif key == "youtube" and (ts - f.stat().st_mtime) / 3600 > doctor.COOKIES_STALE_HOURS:
            out.append(finding("signin:youtube-export", "signins", WARN,
                               f"YouTube cookies {f.name} last exported "
                               f"{(ts - f.stat().st_mtime) / 3600:.0f} h ago: YouTube retires old ones",
                               look="is the laptop on and its clipper-cookies task running? "
                                    "deploy\\cookies\\cookies-task.ps1 -Status"))
    return out


def check_signins(cfg: dict, now: datetime) -> list[dict]:
    """Sign-ins that are about to stop working, from the files: a cookies file's sign-in cookie
    (YouTube, Twitch, HEI) gone or expiring within [checkup] signin_days, and the YouTube export
    going stale; a sign-in token (TikTok's, X's) whose refresh token expires within signin_days.
    YouTube's own token doesn't expire (a verified app); a revoked one shows up in the logs."""
    warn_s = cfg["checkup"]["signin_days"] * 86400
    if not warn_s:
        return []
    out = _cookie_findings(cfg, now, warn_s)
    for label, key, cmd in (("TikTok", "tiktok_token", "clipper tiktok --sign-in"),
                            ("X", "x_token", "clipper x --sign-in")):
        tok = jsonstate.read_or(cfg["paths"][key], {})
        until = tok.get("refresh_expires_at") if isinstance(tok, dict) else None
        if isinstance(until, int | float) and until - now.timestamp() < warn_s:
            gone = until <= now.timestamp()
            out.append(finding(f"signin:{label.lower()}-token", "signins", BAD if gone else WARN,
                               f"{label} sign-in {'expired' if gone else 'expires'} "
                               f"{datetime.fromtimestamp(until, UTC):%m-%d}", look=cmd))
    return out


LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)\S*\s+(ERROR|CRITICAL)\s+(.*)")
LOG_TAIL_BYTES = 4 << 20   # the end of each log is enough for a day; a rotated log is 10 MB at most


def error_shape(msg: str) -> str:
    """An error line with what changes between repeats (ids, numbers, quoted values) blanked, so
    the same error from different streams counts as one."""
    s = re.sub(r"'[^']*'|\"[^\"]*\"", "'…'", msg)
    s = re.sub(r"\b[0-9a-f]{8,}\b|\d+", "N", s)
    return s[:160]


def repeated_errors(text: str, since: datetime, least: int, tz: ZoneInfo) -> list[tuple[str, int, str]]:
    """[(shape, count, last line)] of the ERROR and CRITICAL lines after `since` that came at least
    `least` times, most first. A log's times have no zone: they are tz's (the services' TZ)."""
    seen: dict[str, list] = {}
    for line in text.splitlines():
        m = LOG_LINE.match(line)
        if not m:
            continue
        t = datetime.fromisoformat(m.group(1).replace("T", " ")).replace(tzinfo=tz)
        if t < since:
            continue
        shape = error_shape(m.group(3))
        seen.setdefault(shape, [0, ""])
        seen[shape][0] += 1
        seen[shape][1] = m.group(3)
    return sorted(((s, n, last) for s, (n, last) in seen.items() if n >= least), key=lambda x: -x[1])


def _tail(path: Path) -> str:
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - LOG_TAIL_BYTES))
        return f.read().decode("utf-8", "replace")


def check_logs(cfg: dict, now: datetime) -> list[dict]:
    """The same error, give or take its ids, at least [checkup] repeat_errors times in serve's or
    the bot's log in the last repeat_hours (0 = off): an alert fires once, a loop keeps failing.
    The logs' times are read in [schedule] timezone, the services' TZ in the hub's compose."""
    ck = cfg["checkup"]
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    if not ck["repeat_errors"]:
        return []
    since = now - timedelta(hours=ck["repeat_hours"])
    out = []
    for name in ("serve", "bot"):
        path = cfg["paths"].get(f"{name}_log")
        if not path or not Path(path).is_file():
            continue
        for shape, n, last in repeated_errors(_tail(Path(path)), since, ck["repeat_errors"], tz)[:3]:
            key = re.sub(r"\W+", "-", shape.lower())[:60].strip("-")
            out.append(finding(f"logs:{name}:{key}", "logs", WARN,
                               f"{name}.log: the same error {n} times in {ck['repeat_hours']} h: {last[:200]}",
                               look=f"logs/{Path(path).name}"))
    return out


def check_approvals(cfg: dict, now: datetime) -> list[dict]:
    """An item approved (review.json, which mirrors Kitsu) at least [checkup] approved_hours ago
    that the plan neither scheduled nor skipped with a reason: the replan never saw it. And a plan
    older than approved_hours while serve should replan hourly (0 = off)."""
    hours = cfg["checkup"]["approved_hours"]
    path: Path = cfg["paths"]["schedule"]
    if not hours or not path.exists():
        return []
    plan = jsonstate.load(path)
    out = []
    every = cfg["youtube"]["auto_upload_minutes"]
    made = _age_s(plan.get("generated_at"), now)
    if every and made > max(hours * 3600, 2 * every * 60):
        out.append(finding("approvals:plan", "approvals", BAD,
                           f"schedule.json was last planned {_hours(made)} ago; serve replans every {every} min",
                           look="serve's upload job (clipper status; logs/serve.log)"))
    known = {e.get("key") for e in plan.get("entries", [])} | {
        u.get("key") for u in plan.get("unscheduled", []) if isinstance(u, dict)}
    root: Path = cfg["paths"]["streams"]
    lost = []
    for f in sorted(root.glob("*/review.json")) if root.is_dir() else []:
        for key, it in jsonstate.read_or(f, {}).items():
            age = _age_s(it.get("updated_at"), now) if isinstance(it, dict) else 0
            if (isinstance(it, dict) and it.get("status") == "approved" and age != float("inf")
                    and f"{f.parent.name}/{key}" not in known and age > hours * 3600):
                lost.append(f"{f.parent.name}/{key}")
    if lost:
        out.append(finding("approvals:unplanned", "approvals", BAD,
                           f"{len(lost)} approved item{'s' if len(lost) != 1 else ''} the plan never saw "
                           f"(approved over {hours} h ago): " + ", ".join(lost[:5]) + (" ..." if len(lost) > 5 else ""),
                           look="serve's upload job replans hourly (logs/serve.log); `clipper schedule --list`"))
    return out


CHECKS: dict[str, Callable[[dict, datetime], list[dict]]] = {
    "services": check_services, "streams": check_streams, "schedule": check_schedule, "lint": check_lint,
    "auto": check_auto, "housekeeping": check_housekeeping, "tools": check_tools, "signins": check_signins,
    "logs": check_logs, "approvals": check_approvals}


# ---------- running them ----------

def _state_path(cfg: dict) -> Path:
    return cfg["paths"]["streams"] / ".checkup" / "state.json"


def run(cfg: dict, only: list[str] | None = None, mark: bool = False,
        now: datetime | None = None, fix: bool = False) -> dict:
    """{at, checks, findings, repairs}; each finding also says when it was first seen, whether it
    is new since the last --mark, and what a repair did or would do (repairs.apply; fix: act).
    repairs: the ledger's last 24 h. A check that crashes is a finding too, so the rest still run."""
    now = now or datetime.now(UTC)
    names = only or list(CHECKS)
    found = []
    for name in names:
        try:
            found += CHECKS[name](cfg, now)
        except Exception as e:  # noqa: BLE001  # one broken check mustn't hide the others' findings
            found.append(finding(f"check:{name}", name, BAD, f"the {name} check failed: {type(e).__name__}: {e}"))
    seen = jsonstate.read_or(_state_path(cfg), {}).get("seen", {})
    at = now.isoformat(timespec="seconds")
    for f in found:
        f["new"] = f["key"] not in seen
        f["first_seen"] = seen.get(f["key"], {}).get("first_seen", at)
    repairs.apply(cfg, found, now, fix=fix)
    if mark:
        # Only what is still wrong is kept, so a problem that clears and comes back is new
        # again; a run of some checks (--only) leaves the others' findings as they were.
        kept = {k: v for k, v in seen.items() if v.get("check") not in names}
        path = _state_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        jsonstate.write(path, {"at": at, "seen": {
            **kept, **{f["key"]: {"first_seen": f["first_seen"], "check": f["check"]} for f in found}}}, indent=1)
    return {"at": at, "checks": names, "findings": found, "repairs": repairs.recent(cfg, now)}


def repair_lines(entries: list[dict], tz: ZoneInfo, hours: int = 24) -> list[str]:
    """The ledger's recent entries: each repair made, then how many would-fixes were logged."""
    if not entries:
        return []
    did = [e for e in entries if e.get("did")]
    n = {"fixed": sum(1 for e in did if e.get("ok")), "failed": sum(1 for e in did if not e.get("ok")),
         "would have fixed": len(entries) - len(did)}
    head = [f"repairs in the last {hours} h: " + ", ".join(f"{k} {v}" for k, v in n.items() if v)]
    return head + [f"  {datetime.fromisoformat(e['at']).astimezone(tz):%m-%d %H:%M} {e['kind']} {e['key']}: "
                   f"{'' if e.get('ok') else 'FAILED '}{e['text']}" for e in did]


def lines(report: dict, tz: ZoneInfo) -> list[str]:
    fs = report["findings"]
    at = datetime.fromisoformat(report["at"]).astimezone(tz)
    tail = repair_lines(report.get("repairs", []), tz)
    if not fs:
        return [f"checkup {at:%m-%d %H:%M}: all good ({', '.join(report['checks'])})", *tail]
    new = sum(f["new"] for f in fs)
    out = [f"checkup {at:%m-%d %H:%M}: {len(fs)} finding{'s' if len(fs) != 1 else ''}"
           + (f" ({new} new)" if new != len(fs) else "") + f"; {repairs.summary(fs)}"]
    for f in sorted(fs, key=lambda f: (f["level"] != BAD, list(CHECKS).index(f["check"])
                                       if f["check"] in CHECKS else 99)):
        tag = "BAD " if f["level"] == BAD else "warn"
        since = "" if f["new"] else f" (since {datetime.fromisoformat(f['first_seen']).astimezone(tz):%m-%d %H:%M})"
        out.append(f"  {tag} {f['check']:<9} {f['text']}{since}")
        if f["look"]:
            out.append(f"       look: {f['look']}")
        if r := f.get("repair"):
            out.append(f"       fix:  {r['state']}: {r['text']}")
    return out + tail
