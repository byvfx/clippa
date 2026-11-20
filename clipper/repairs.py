"""`clipper checkup --fix` (#287): fixed repairs for some checkup findings, from a short allowlist.

Detection is checkup's (clipper/checkup.py); this closes the loop for the fixes that are safe to
make without asking. Each repair belongs to findings by their key, and plans before it acts:

    retry   failed:<stream>   a run out of retries ([worker] max_attempts) whose error is a
                              passing one ([checkup] passing: a rate limit, a dropped connection,
                              a remote 5xx): one attempt back, retry_after_hours after it failed,
                              so the worker runs it again on its next pass
    tmp     tmp:leftover      temp files a killed write left beside a state file (jsonstate's
                              `<file>.<pid>.<thread>.tmp`), a day old or more: deleted

Checked and left out, because a service already does it: a late release (serve replans every
upload run), a stale run lock (the worker takes it over), a queued stream (the worker polls; when
it doesn't, the fix is the container, which nothing here can reach).

Every repair is safe to repeat and capped: at most [checkup] max_repairs tries per finding key in
CAP_DAYS, counted in the ledger (<streams>/.checkup/repairs.json), then the finding waits for a
person. Only the kinds listed in [checkup] fix act; the others are would-fix: what they would have
done goes in the ledger (once a day per finding), so a week of reports shows what turning one on
would do. A checkup without --fix (the nightly one) changes nothing and says "due" for a kind
that is on: serve's next --fix makes it.
Never, whatever is listed: an upload or publish, a Kitsu decision, the recorder, source footage,
the config, viewer-facing text, a restart. No repair calls a model itself, but a retried run
reruns its failed stage, which may (score, segments, transcription on Modal): the cap bounds it.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import jsonstate
from .config import for_stream
from .errors import ClipperError

KEEP = 1000            # ledger entries kept (the cap reads only the last CAP_DAYS)
CAP_DAYS = 7           # max_repairs is per finding key in this window: a new leftover next month is new
WOULD_EVERY_S = 86400  # a would-fix is logged once a day per finding
TMP_AGE_S = 86400      # a temp file this old can't belong to a write in flight
TMP_RX = re.compile(r"\.\d+\.\d+\.tmp$")
# Where the state files are, not every folder: a stream's video folders are big over the share.
TMP_GLOBS = ("*.tmp", "*/*.tmp", "*/live/*.tmp", "*/metadata/*/*.tmp")


@dataclass
class Plan:
    """What a repair would do for one finding. ready: it may act now (else `text` says when);
    act: does it and returns what it did, raising when it couldn't."""
    kind: str
    ready: bool
    text: str
    act: Callable[[], str] | None = None


def _when(iso: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(iso or "")
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()


# ---------- retry: a run out of retries on a passing cause ----------

def passing_cause(patterns: list[str], error: str) -> str:
    """The first of `patterns` in the error (whole words, any case), or ""."""
    for p in patterns:
        if re.search(r"(?<![\w-])" + re.escape(p) + r"(?![\w-])", error or "", re.IGNORECASE):
            return p
    return ""


def _give_back(cfg: dict, d: Path) -> str:
    """One attempt back in run.json, under the worker's takeover lock, and only while no worker
    holds the stream and it is still failed with no retries left (the worker or a person may
    have moved it on since the plan)."""
    wc = cfg["worker"]
    with jsonstate.locked(d / ".run.lock"):
        if (d / ".run.lock").exists():
            raise ClipperError(f"{d.name}: a worker holds it now; left alone")
        run = jsonstate.load(d / "run.json")
        if run.get("status") != "failed" or run.get("key_rejected") \
                or run.get("attempts", 0) < wc["max_attempts"]:
            raise ClipperError(f"{d.name}: no longer failed with no retries left; left alone")
        run["attempts"] = wc["max_attempts"] - 1
        run["checkup_retries"] = run.get("checkup_retries", 0) + 1
        jsonstate.write(d / "run.json", run)
    return f"{d.name}: one attempt back; the worker runs it on its next pass"


def _mtime(path: Path) -> datetime | None:
    """When run.json was last written: the failure's time when it has no `updated`."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return None


def plan_retry(cfg: dict, f: dict, now: datetime) -> Plan | None:
    d = cfg["paths"]["streams"] / f["stream"]
    run = jsonstate.read_or(d / "run.json", {})
    ck = for_stream(cfg, d)["checkup"]
    cause = passing_cause(ck["passing"], run.get("error", ""))
    if not cause:
        return None   # not a passing error: a person reads the log
    failed = _when(run.get("updated")) or _mtime(d / "run.json") or now
    after = failed + timedelta(hours=ck["retry_after_hours"])
    if now < after:
        tz = ZoneInfo(cfg["schedule"]["timezone"])
        return Plan("retry", False, f"one more attempt after {after.astimezone(tz):%m-%d %H:%M} "
                                    f"(the error says {cause!r})")
    return Plan("retry", True, f"one more attempt (the error says {cause!r})", lambda: _give_back(cfg, d))


# ---------- tmp: temp files a killed write left ----------

_scanned: dict[tuple, dict[Path, float]] = {}   # the last scan, so the check and the repair share it


def leftover_tmp(cfg: dict, now_s: float | None = None) -> dict[Path, float]:
    """jsonstate temp files TMP_AGE_S old -> their mtime: in the streams folder, a stream's folder
    (with live/ and metadata/<folder>/ in it), and beside schedule.json. One scan per now_s."""
    root: Path = cfg["paths"]["streams"]
    now_s = now_s or time.time()
    key = (str(root), str(cfg["paths"]["schedule"].parent), now_s)
    if key in _scanned:
        return dict(_scanned[key])
    found: dict[Path, float] = {}
    for base in {root, cfg["paths"]["schedule"].parent}:
        if not base.is_dir():
            continue
        for pattern in TMP_GLOBS if base == root else ("*.tmp",):
            for p in base.glob(pattern):
                try:
                    if TMP_RX.search(p.name) and p.is_file() and now_s - (t := p.stat().st_mtime) >= TMP_AGE_S:
                        found[p] = t
                except OSError:
                    continue
    _scanned.clear()
    _scanned[key] = dict(sorted(found.items()))
    return dict(_scanned[key])


def _delete(paths: list[Path]) -> str:
    _scanned.clear()
    errors = []
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError as e:
            errors.append(f"{p.name}: {e.strerror or e}")
    if errors:   # a failed try: it counts toward the cap, and serve DMs it
        raise ClipperError(f"deleted {len(paths) - len(errors)} of {len(paths)} leftover temp files; "
                           f"{len(errors)} couldn't be: {errors[0]}")
    return f"deleted {len(paths)} leftover temp files"


def plan_tmp(cfg: dict, f: dict, now: datetime) -> Plan | None:
    paths = leftover_tmp(cfg, now.timestamp())
    if not paths:
        return None
    return Plan("tmp", True, f"delete {len(paths)} leftover temp files", lambda: _delete(list(paths)))


# ---------- the allowlist ----------

# kind -> (the finding keys it repairs, by prefix; the checkup check that finds them; its planner)
REPAIRS: dict[str, tuple[str, str, Callable[[dict, dict, datetime], Plan | None]]] = {
    "retry": ("failed:", "streams", plan_retry),
    "tmp": ("tmp:", "housekeeping", plan_tmp),
}


def checks() -> list[str]:
    """The checkup checks whose findings have a repair: all serve's --fix job needs to run."""
    return list(dict.fromkeys(check for _, check, _ in REPAIRS.values()))


def enabled(cfg: dict) -> set[str]:
    """[checkup] fix: the repair kinds that may act."""
    names = set(cfg["checkup"]["fix"] or [])
    unknown = names - set(REPAIRS)
    if unknown:
        raise ClipperError(f"[checkup] fix names {', '.join(sorted(unknown))}: the repairs are "
                           f"{', '.join(REPAIRS)}")
    return names


def ledger_path(cfg: dict) -> Path:
    return cfg["paths"]["streams"] / ".checkup" / "repairs.json"


def ledger(cfg: dict) -> list[dict]:
    return jsonstate.read_or(ledger_path(cfg), {}).get("log", [])


def tries(log: list[dict], key: str, now: datetime) -> int:
    """Repairs made (or tried) for this finding key in the last CAP_DAYS."""
    since = now - timedelta(days=CAP_DAYS)
    return sum(1 for e in log if e.get("key") == key and e.get("did")
               and (t := _when(e.get("at"))) and t >= since)


def _plan(cfg: dict, f: dict, now: datetime) -> Plan | None:
    for prefix, _, planner in REPAIRS.values():
        if f["key"].startswith(prefix):
            return planner(cfg, f, now)
    return None


def apply(cfg: dict, findings: list[dict], now: datetime, fix: bool = False) -> list[dict]:
    """Give each finding a `repair`: {kind, state, text}, state one of
        done     fixed this run            would    a kind not in [checkup] fix: what it would do
        due      on and ready: serve's next --fix makes it (a run without --fix)
        waiting  on, fixes later (text: when)  capped   max_repairs used up: it needs a person
        failed   the repair raised (text: why)
    (no `repair`: nothing here can fix it). fix: act and write the ledger; without it nothing
    is written. Returns the new ledger entries."""
    try:
        on = enabled(cfg)
    except ClipperError:
        if fix:
            raise
        on = set()   # a plain checkup still reports; --fix says what is wrong with the list
    cap = cfg["checkup"]["max_repairs"]
    log = ledger(cfg)
    at = now.isoformat(timespec="seconds")
    new: list[dict] = []
    for f in findings:
        try:
            plan = _plan(cfg, f, now)
        except Exception as e:  # noqa: BLE001  # a broken planner mustn't stop the others or the report
            f["repair"] = {"kind": "", "state": "failed", "text": f"{type(e).__name__}: {e}"}
            continue
        if plan is None:
            continue
        rep = f["repair"] = {"kind": plan.kind, "state": "", "text": plan.text}
        if tries(log, f["key"], now) >= cap:
            rep.update(state="capped", text=f"tried {cap} times in {CAP_DAYS} days ({plan.text}): it needs you")
        elif plan.kind not in on:
            rep["state"] = "would"   # (a would-fix that isn't ready yet isn't logged: it did nothing yet)
            last = max((t for e in log if e.get("key") == f["key"] and not e.get("did")
                        and (t := _when(e.get("at")))), default=None)
            if fix and plan.ready and (last is None or (now - last).total_seconds() >= WOULD_EVERY_S):
                new.append({"at": at, "key": f["key"], "kind": plan.kind, "did": False, "text": plan.text})
        elif not plan.ready:
            rep["state"] = "waiting"
        elif not fix:
            rep.update(state="due", text=f"{plan.text} (serve's next checkup --fix)")
        else:
            try:
                rep.update(state="done", text=plan.act())
            except Exception as e:  # noqa: BLE001  # reported, and the try still counts toward the cap
                rep.update(state="failed", text=f"{type(e).__name__}: {e}")
            new.append({"at": at, "key": f["key"], "kind": plan.kind, "did": True,
                        "ok": rep["state"] == "done", "text": rep["text"]})
    if new:
        path = ledger_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        with jsonstate.update(path, {}) as data:
            data["log"] = (data.get("log", []) + new)[-KEEP:]
    return new


def recent(cfg: dict, now: datetime, hours: float = 24) -> list[dict]:
    """The ledger's entries from the last `hours`: what was fixed, or would have been."""
    since = now - timedelta(hours=hours)
    return [e for e in ledger(cfg) if (t := _when(e.get("at"))) and t >= since]


def counts(findings: list[dict]) -> dict[str, int]:
    """fixed / will fix / would fix / need you over a run's findings."""
    out = {"fixed": 0, "will fix": 0, "would fix": 0, "need you": 0}
    for f in findings:
        state = (f.get("repair") or {}).get("state")
        out[{"done": "fixed", "due": "will fix", "waiting": "will fix", "would": "would fix"}
            .get(state, "need you")] += 1
    return out


def summary(findings: list[dict]) -> str:
    """"fixed 1, would fix 2, need you 1": the counts that aren't 0."""
    return ", ".join(f"{k} {n}" for k, n in counts(findings).items() if n)
