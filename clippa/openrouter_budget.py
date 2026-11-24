"""The war room's OpenRouter budget: the clippa key's limit, the account balance, and the
changes a management key can make.

The page asks ``GET /api/openrouter`` (cached for five minutes; ``?fresh=1`` or
``POST /api/openrouter/refresh`` asks again). The warning percent lives in
``<streams>/.serve/openrouter.json`` with the limit events. Nothing in that file or in the
JSON the page receives is a key or a key hash.

Changing the limit or disabling the key is ``PATCH /api/v1/keys/{hash}`` with
``$OPENROUTER_MANAGEMENT_KEY`` (or ``[paths] openrouter_management_key``). With only the
inference key the panel is read-only. Credits and auto top-up stay on OpenRouter.

A model call that OpenRouter answers with 402/403 is named here (`classify_payment`) and
recorded by `note_retry` / `note_stop`. ``llm.py`` does that for chat completions. A runner
that knows which stream was in the call can call `note_stop` again within ten minutes with
``show``, ``stage`` and ``detail``: the latest stop is updated rather than written twice, so
the banner can name the stream once that is known. The banner sentences are `COPY`.

``--budget`` on a run is Clippa's own guard for that one run. It is not a dollar figure this
module knows, and it is not the OpenRouter cap.
"""
from __future__ import annotations

import math
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .errors import ClipperError, OpenRouterLimit
from .timefmt import now_iso

BASE = "https://openrouter.ai/api/v1"
CREDITS_URL = "https://openrouter.ai/settings/credits"
MGMT_ENV = "OPENROUTER_MANAGEMENT_KEY"
WARN_DEFAULT = 75
WARN_MIN, WARN_MAX = 1, 99
LIMIT_MAX = 1_000_000
TTL = 300
TIMEOUT = 20
EVENTS_MAX = 40
RESETS = ("daily", "weekly", "monthly")
_LIMIT_WORDS = re.compile(
    r"key limit|spending limit|limit exceeded|insufficient credits|requires more credits|"
    r"not enough credits|payment required", re.I)
_INFLIGHT = re.compile(r"in[- ]flight", re.I)

# Banner and event sentences. Edit these once a real 402/403 shows what a run left on disk.
COPY = {
    "stop_title": "Stream stopped: the clippa key's limit is spent.",
    "stop_body": ("OpenRouter refused a call with {status} · {code}. {where} "
                  "Raise the limit or wait for the reset{when}, then resume it from Pipeline."),
    "stop_body_once": ("OpenRouter refused a call with {status} · {code}. {where} "
                       "Raise the limit, then resume it from Pipeline."),
    "stop_title_credits": "Stream stopped: the OpenRouter account is out of credits.",
    "stop_body_credits": ("OpenRouter refused a call with {status} · {code}. {where} "
                          "Buy credits on OpenRouter, then resume it from Pipeline."),
    "stop_where": "The run stopped at its next model call.",
    "warn_title": "{pct}% of the {period} cap is used.",
    "warn_body": "{left} left before OpenRouter stops the clippa key. It resets {when}.",
    "warn_body_once": "{left} left before OpenRouter stops the clippa key. This cap does not reset.",
    "retry_body": ("OpenRouter's in-flight budget is full for a moment ({status}). "
                   "Nothing to do; the stream carries on after the wait."),
    "disabled_title": "The clippa key is disabled.",
    "disabled_body": "Every model call Clippa makes fails until it's enabled again.",
    "disable_callout": ("Every model call Clippa makes fails while the key is off. "
                        "A stream that is running stops at its next call."),
    "enable_callout": ("Model calls work again once the key is on. "
                       "Resume a stream that already stopped from Pipeline."),
    "topup": "Buying credits and auto top-up are set on OpenRouter. Its API can't change them.",
    "run_budget": ("Clippa stops a single run once it reaches its own budget. "
                   "The OpenRouter cap covers all runs on the key."),
    "cap_help": "OpenRouter refuses the key's calls once this is spent.",
    "figures": "OpenRouter's own figures. Its days and weeks are UTC; weeks start Monday.",
    "mgmt_kept": "Kept in the War Room's secrets on the NAS. Never shown here.",
    "sent_with": "Sent with the management key from the NAS.",
    "not_deleted": "The key isn't deleted. Enable it again from this panel.",
    "left_estimate": ("Estimate: {limit} less the {used} used {window}. "
                      "OpenRouter's own figure shows after saving."),
    "passed": "The clippa key passed your {pct}% warning on its {limit} {period} cap ({at}).",
    "retried": ("OpenRouter's in-flight budget was full. Waited {seconds} s as its Retry-After said, "
                "then the call went through."),
    "retried_wait": "OpenRouter's in-flight budget is full. Waiting {seconds} s as its Retry-After said.",
    "retried_failed": ("OpenRouter's in-flight budget was full. Waited {seconds} s as its Retry-After said, "
                       "then the call failed."),
    "retried_done": "OpenRouter's in-flight budget was full. Waited {seconds} s as its Retry-After said.",
    "changed": "Limit {before} → {after}, resets {reset}. Set from the War Room.",
    "disabled_ev": "The clippa key was disabled from the War Room.",
    "enabled_ev": "The clippa key was enabled from the War Room.",
}

_PERIOD = {
    "daily": ("today", "daily", "today"),
    "weekly": ("this week", "weekly", "this week"),
    "monthly": ("this month", "monthly", "this month"),
    None: ("in all", "lifetime", "in all"),
}
_TONE = {"stopped": "bad", "passed": "warn", "retried": "info", "changed": "ok",
         "disabled": "bad", "enabled": "ok"}
_TITLE = {"stopped": "Stopped", "retried": "Retried", "changed": "Changed",
          "disabled": "Disabled", "enabled": "Enabled"}

_CACHE: dict[str, dict] = {}


class OpenRouterKeyLimit(OpenRouterLimit):
    """OpenRouter refused a model call because the clippa key's spending limit is spent.

    An OpenRouterLimit, so a stage that stops on any spending limit stops on this too."""

    def __init__(self, message: str, limit_source: str = "openrouter_key_limit"):
        super().__init__(message, limit_source)


def classify_payment(status: int, body: str, retry_after: str | None) -> str | None:
    """Clippa's name for an OpenRouter 402 or 403 on a model call.

    A 402 with a Retry-After of at most two minutes, and whose text does not say the key
    limit itself is spent, is ``openrouter_in_flight_budget``: wait, then send the call again.
    So is a 402 whose text says in-flight with no usable Retry-After (the caller fails that call
    rather than wait, and records no stop).
    Any other 402, or a 403 whose text says the limit or the credits are spent, is
    ``openrouter_key_limit``: the text alone can't tell the key's cap from the account's balance
    ("requires more credits" is either), so only ``limit_source`` names ``openrouter_credits``,
    and the panel judges a text-only stop on both figures. Any other status, including a 403
    about something else, is None.
    """
    if status not in (402, 403):
        return None
    text = body or ""
    wait = retry_seconds(retry_after)
    limit = bool(_LIMIT_WORDS.search(text))
    if status == 402 and wait is not None and (bool(_INFLIGHT.search(text)) or not limit):
        return "openrouter_in_flight_budget"
    if status == 402 and _INFLIGHT.search(text):  # says in-flight, no usable wait: not a spent limit
        return "openrouter_in_flight_budget"
    if status == 402 or (status == 403 and limit):
        return "openrouter_key_limit"
    return None


def retry_seconds(header: str | None) -> float | None:
    """Seconds from a Retry-After header, or None when it isn't a short wait (0 to 120)."""
    if header is None or str(header).strip() == "":
        return None
    try:
        n = float(str(header).strip())
    except ValueError:
        return None
    if not math.isfinite(n) or n < 0 or n > 120:
        return None
    return n


def mask_secret(value: str) -> str:
    """A key the way the panel shows one: the start, an ellipsis, the last three characters."""
    v = (value or "").strip()
    if not v:
        return ""
    if "…" in v or "..." in v:
        return v.replace("...", "…")
    if len(v) <= 14:
        return "••••"
    return f"{v[:10]}…{v[-3:]}"


def management_key(cfg: dict) -> str:
    """The OpenRouter management key: ``$OPENROUTER_MANAGEMENT_KEY``, else its file. ``""`` if neither."""
    key = os.environ.get(MGMT_ENV, "").strip()
    path = (cfg.get("paths") or {}).get("openrouter_management_key")
    if key or not path:
        return key
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def inference_key(cfg: dict) -> str:
    from . import llm

    return llm.provider_key("openrouter", cfg.get("paths"))


def parse_warn(value) -> int:
    """A warning percent, 1 to 99. Anything else is an error the page can show."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ClipperError("the warning should be a whole percent from 1 to 99") from None
    if isinstance(value, bool) or not WARN_MIN <= n <= WARN_MAX:
        raise ClipperError("the warning should be a whole percent from 1 to 99")
    return n


def parse_limit(value) -> float:
    """A spending limit in dollars, from 0 to 1,000,000, to the cent."""
    if isinstance(value, bool) or value is None or value == "":
        raise ClipperError("set a spending limit in dollars, 0 or more")
    try:
        n = round(float(value), 2)
    except (TypeError, ValueError):
        raise ClipperError("the spending limit should be a number of dollars") from None
    if not math.isfinite(n):  # json.loads takes NaN
        raise ClipperError("the spending limit should be a number of dollars")
    if n < 0 or n > LIMIT_MAX:
        raise ClipperError("the spending limit should be between $0 and $1,000,000")
    return n


def parse_reset(value) -> str | None:
    """``daily``, ``weekly``, ``monthly``, or None for a cap that does not reset."""
    if value in (None, "", "none", "null"):
        return None
    if value not in RESETS:
        raise ClipperError("reset should be daily, weekly, monthly, or none")
    return value


def parse_disabled(value) -> bool:
    if isinstance(value, bool):
        return value
    raise ClipperError("disabled should be true or false")


def next_reset(now: datetime, reset: str | None) -> datetime | None:
    """The next midnight UTC at which OpenRouter resets a limit of this cadence.

    Weeks are Monday through Sunday. ``now`` exactly on a reset returns that moment.
    """
    now = now.astimezone(UTC)
    if reset not in RESETS:
        return None
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if reset == "daily":
        return start if now == start else start + timedelta(days=1)
    if reset == "weekly":
        if now == start and start.weekday() == 0:
            return start
        days = (7 - start.weekday()) % 7
        return start + timedelta(days=days or 7)
    if now == start and start.day == 1:
        return start
    year, month = start.year, start.month + 1
    if month == 13:
        year, month = year + 1, 1
    return start.replace(year=year, month=month, day=1)


def usd(n) -> str:
    return f"${float(n):,.2f}"


def _money(v) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _tz(cfg: dict) -> ZoneInfo:
    name = ((cfg.get("schedule") or {}).get("timezone")) or "America/Los_Angeles"
    try:
        return ZoneInfo(str(name))
    except (KeyError, ValueError):
        return ZoneInfo("America/Los_Angeles")


def _clock(dt: datetime | None, tz: ZoneInfo) -> dict:
    if dt is None:
        return {"utc": "does not reset", "short": "does not reset", "local": "", "both": "does not reset",
                "note": ""}
    utc = dt.astimezone(UTC)
    loc = dt.astimezone(tz)
    hour = loc.strftime("%I").lstrip("0") or "12"
    local = f"{loc.strftime('%b')} {loc.day}, {hour}:{loc.strftime('%M %p %Z')}"
    utc_s = f"{utc.strftime('%b')} {utc.day}, {utc.strftime('%H:%M')} UTC"
    return {"utc": utc_s, "short": f"{utc.strftime('%b')} {utc.day}", "local": local,
            "both": f"{utc_s} ({local})", "note": f"{local} ({utc.strftime('%A')} 00:00 UTC)"}


def _window_usage(data: dict, reset: str | None) -> float:
    key = {"daily": "usage_daily", "weekly": "usage_weekly", "monthly": "usage_monthly"}.get(reset, "usage")
    return _money(data.get(key)) or 0.0


def _path(cfg: dict) -> Path | None:
    root = (cfg.get("paths") or {}).get("streams")
    if not root or not Path(root).is_dir():
        return None
    return Path(root) / ".serve" / "openrouter.json"


def _settings(cfg: dict) -> dict:
    from . import jsonstate

    path = _path(cfg)
    data = jsonstate.read_or(path, {}) if path and path.is_file() else {}
    if not isinstance(data, dict):
        data = {}
    try:
        pct = int(data.get("warn_pct", WARN_DEFAULT))
    except (TypeError, ValueError):
        pct = WARN_DEFAULT
    if isinstance(data.get("warn_pct"), bool) or not WARN_MIN <= pct <= WARN_MAX:
        pct = WARN_DEFAULT
    events = [e for e in (data.get("events") or []) if isinstance(e, dict)]
    stop = data.get("stop") if isinstance(data.get("stop"), dict) else None
    return {"warn_pct": pct, "events": events[-EVENTS_MAX:], "retry": data.get("retry") or None,
            "stop": stop, "warned_for": str(data.get("warned_for") or "")}


def _write(cfg: dict, fn) -> None:
    from . import jsonstate

    path = _path(cfg)
    if path is None:
        raise ClipperError("no streams folder, so the OpenRouter budget state can't be saved")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        cur = jsonstate.read_or(path, {})
        if not isinstance(cur, dict):
            path.unlink()
    with jsonstate.update(path, {}) as data:
        fn(data)


def _quietly(cfg: dict, fn) -> None:
    if _path(cfg) is None:
        return
    try:
        _write(cfg, fn)
    except (OSError, TimeoutError):
        return


def note_retry(cfg: dict, *, seconds: float, status: int = 402,
               code: str = "openrouter_in_flight_budget", rid: str = "") -> str:
    """Remember an in-flight 402: the banner counts down, and the events list gains a line.
    Returns the retry's id (``rid`` when given, a new one otherwise); the call that waited
    passes it to `clear_retry`, so another call's answer doesn't close this one."""
    import uuid

    rid = rid or uuid.uuid4().hex[:12]
    until = (datetime.now(UTC) + timedelta(seconds=float(seconds))).isoformat(timespec="seconds")

    def apply(d: dict) -> None:
        events = d.setdefault("events", [])
        mine = next((e for e in reversed(events) if isinstance(e, dict) and e.get("id") == rid), None)
        if mine is not None:  # the same call waiting again: one line, the latest wait
            mine.update(ts=now_iso(), seconds=seconds, status=status, pending=True, until=until)
        else:
            events.append({"ts": now_iso(), "kind": "retried", "code": code, "status": status,
                           "seconds": seconds, "pending": True, "id": rid, "until": until})
        d["events"] = events[-EVENTS_MAX:]
        d["retry"] = {"until": until, "seconds": seconds, "code": code, "status": status, "id": rid}

    _quietly(cfg, apply)
    return rid


def note_stop(cfg: dict, *, status: int = 402, code: str = "openrouter_key_limit",
              show: str = "", stage: str = "", detail: str = "", model: str = "") -> None:
    """Remember a key-limit refusal. A second call within ten minutes fills in the stream
    instead of adding another line."""
    def apply(d: dict) -> None:
        events = d.setdefault("events", [])
        last = events[-1] if events else None
        fresh = {"ts": now_iso(), "kind": "stopped", "code": code, "status": status,
                 "show": show, "stage": stage, "detail": detail, "model": model}
        if (isinstance(last, dict) and last.get("kind") == "stopped" and last.get("code") == code
                and _age(last.get("ts")) < 600):
            for k in ("show", "stage", "detail", "model", "status"):
                if fresh.get(k):
                    last[k] = fresh[k]
            last["ts"] = fresh["ts"]  # the ten minutes count from the latest refusal
            d["stop"] = _stop_of(last)
            return
        events.append(fresh)
        d["events"] = events[-EVENTS_MAX:]
        d["stop"] = _stop_of(fresh)

    _quietly(cfg, apply)


def clear_retry(cfg: dict, rid: str, *, ok: bool = True) -> None:
    """The call that waited under ``rid`` is over: close its line (went through, or failed).
    If the countdown was this call's, it goes to another call still waiting (the one that
    waits longest), or is dropped. Other calls' waits are left alone."""
    if not rid:
        return

    def apply(d: dict) -> None:
        for ev in d.get("events") or []:
            if isinstance(ev, dict) and ev.get("id") == rid and ev.get("pending"):
                ev["pending"] = False
                ev["outcome"] = "ok" if ok else "failed"
        if isinstance(d.get("retry"), dict) and d["retry"].get("id") == rid:
            d.pop("retry", None)
            now = datetime.now(UTC).isoformat(timespec="seconds")
            waiting = [e for e in d.get("events") or [] if isinstance(e, dict) and e.get("pending")
                       and e.get("kind") == "retried" and str(e.get("until") or "") > now]
            if waiting:
                other = max(waiting, key=lambda e: str(e["until"]))
                d["retry"] = {k: other.get(k) for k in ("until", "seconds", "code", "status", "id")}

    _quietly(cfg, apply)


def _stop_of(ev: dict) -> dict:
    return {"at": ev.get("ts"), "status": ev.get("status") or 402, "code": ev.get("code"),
            "show": ev.get("show") or "", "stage": ev.get("stage") or "",
            "detail": ev.get("detail") or "", "model": ev.get("model") or ""}


def _age(iso: str | None) -> float:
    if not iso:
        return float("inf")
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _where(ev: dict | None) -> str:
    if ev and ev.get("show") and ev.get("stage"):
        extra = f", {ev['detail']}" if ev.get("detail") else ""
        return f"{ev['show']} stopped at {ev['stage']}{extra}."
    return COPY["stop_where"]


def event_text(ev: dict) -> str:
    """The events-list sentence for one stored event. The code is separate, so the page can draw it."""
    kind = ev.get("kind")
    if kind == "stopped":
        return _where(ev)
    if kind == "passed":
        pct, limit = int(ev.get("pct") or WARN_DEFAULT), ev.get("limit")
        at = usd((limit or 0) * pct / 100) if limit is not None else ""
        return COPY["passed"].format(pct=pct, limit=usd(limit or 0), period=ev.get("period") or "monthly", at=at)
    if kind == "retried":
        n = ev.get("seconds") or 0
        n_s = int(n) if float(n) == int(float(n)) else n
        if ev.get("pending") and _age(ev.get("ts")) < 600:
            which = "retried_wait"
        else:  # no outcome: written before outcomes were kept, or a call that never reported back
            which = {"ok": "retried", "failed": "retried_failed"}.get(ev.get("outcome"), "retried_done")
        return COPY[which].format(seconds=n_s)
    if kind == "changed":
        word = ev.get("reset") or "never"
        before = "none" if ev.get("before_limit") is None else usd(ev.get("before_limit") or 0)
        return COPY["changed"].format(before=before, after=usd(ev.get("limit") or 0), reset=word)
    if kind == "disabled":
        return COPY["disabled_ev"]
    if kind == "enabled":
        return COPY["enabled_ev"]
    return str(ev.get("text") or "")


def _when_label(iso: str | None, tz: ZoneInfo) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso).astimezone(tz)
    except (TypeError, ValueError):
        return ""
    hour = dt.strftime("%I").lstrip("0") or "12"
    return f"{dt.strftime('%b')} {dt.day} · {hour}:{dt.strftime('%M %p')}"


def _event_row(ev: dict, tz: ZoneInfo) -> dict:
    kind = ev.get("kind") or ""
    title = _TITLE.get(kind, kind)
    if kind == "passed":
        title = f"Passed {int(ev.get('pct') or WARN_DEFAULT)}%"
    return {"ts": ev.get("ts"), "when": _when_label(ev.get("ts"), tz), "kind": kind,
            "tone": _TONE.get(kind, "info"), "title": title, "text": event_text(ev),
            "code": ev.get("code") or ""}


def _live_retry(settings: dict, now: datetime) -> dict | None:
    waiting = settings.get("retry")
    if not isinstance(waiting, dict) or not waiting.get("until"):
        return None
    try:
        until = datetime.fromisoformat(waiting["until"])
    except (TypeError, ValueError):
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    if until <= now:
        return None
    return waiting


def _header(level: str) -> dict:
    """A disabled key fails every call, so it lights the header like a spent cap."""
    out = level in ("stopped", "disabled")
    spend = "bad" if out else "warn" if level == "warn" else "ok"
    return {"spend": spend, "health": "stopped" if out else None,
            "badge": level in ("warn", "stopped", "disabled")}


def _numbers(data: dict, reset: str | None) -> tuple[float, float | None, float | None]:
    used = _window_usage(data, reset)
    limit = _money(data.get("limit"))
    left = _money(data.get("limit_remaining"))
    if left is None and limit is not None:
        left = round(max(0.0, limit - used), 2)
    return used, limit, left


def _stop_holds(stop: dict, events: list, left: float | None, balance: float | None) -> bool:
    """Whether a recorded refusal still stands. For ten minutes it does, unless the limit was
    changed or the key enabled from the War Room since: OpenRouter's figures here can be five
    minutes old (TTL), so they don't overrule a refusal that just happened. After that it stands
    while what ran out is still spent: the account's balance for a credits stop (a limit edit
    doesn't clear that one), the key's remaining limit or the balance for a key-limit stop."""
    at = str(stop.get("at") or "")
    credits = stop.get("code") == "openrouter_credits"
    if not credits and any(isinstance(e, dict) and e.get("kind") in ("changed", "enabled")
                           and str(e.get("ts") or "") >= at for e in events):  # >=: to the second
        return False  # a new cap or an enabled key; neither buys credits
    if _age(at) < 600:
        return True
    broke = balance is not None and balance <= 0.5
    if credits:  # no balance figure (the credits call failed): an hour, then the next refusal says
        return broke if balance is not None else _age(at) < 3600
    return broke or (left is not None and left <= 0.5)  # a text-only refusal may be either


def _cap_reached(limit: float | None, used: float, left: float | None) -> bool:
    """True when the cap has a cent or less left, or usage has met it."""
    if limit is None:
        return False
    if left is not None:
        return left <= 0.009
    return used + 0.009 >= limit


def view_from(cfg: dict, raw: dict, settings: dict, now: datetime | None = None) -> dict:
    """The panel's JSON from OpenRouter's answers and the saved settings. No network, no secrets."""
    now = now or datetime.now(UTC)
    tz = _tz(cfg)
    warn = settings["warn_pct"]
    key_data = raw.get("key") if isinstance(raw.get("key"), dict) else None
    listed = raw.get("listed") if isinstance(raw.get("listed"), dict) else None
    missing = raw.get("missing")
    base = _shell(cfg, raw, settings, warn)
    if missing or key_data is None:
        base["events"] = [_event_row(e, tz) for e in sorted(settings["events"], key=lambda e: e.get("ts") or "", reverse=True)]
        return base
    # The inference key's own /key answer is what refuses calls. The management list adds the
    # name and whether the key is disabled; it does not override the live limit or usage.
    overlay = {}
    if listed:
        if listed.get("name"):
            overlay["name"] = listed["name"]
        if "disabled" in listed:
            overlay["disabled"] = bool(listed["disabled"])
    merged = {**key_data, **overlay}
    reset = merged.get("limit_reset") if merged.get("limit_reset") in RESETS else None
    used, limit, left = _numbers(merged, reset)
    disabled = bool(merged.get("disabled"))
    reached = _cap_reached(limit, used, left)
    credits = raw.get("credits") if isinstance(raw.get("credits"), dict) else {}
    bought = _money(credits.get("total_credits")) if "total_credits" in credits else None
    balance = round(bought - (_money(credits.get("total_usage")) or 0), 2) if bought is not None else None
    stop = settings.get("stop")
    active_stop = stop if stop and _stop_holds(stop, settings["events"], left, balance) else None
    spent = reached or bool(active_stop)
    pct = round(100 * used / limit, 1) if limit else None
    level = _level(disabled, spent, active_stop, pct, warn)
    used_label, period_word, window = _PERIOD.get(reset, _PERIOD[None])
    clock = _clock(next_reset(now, reset), tz)
    name = (listed or {}).get("name") or "clippa key"
    label = mask_secret(str(merged.get("label") or ""))
    key = {
        "name": name, "label": label, "disabled": disabled, "limit_usd": limit, "limit_reset": reset,
        "reset_label": period_word if reset else "does not reset", "left_usd": left, "used_usd": used,
        "used_label": used_label, "today_usd": _money(merged.get("usage_daily")) or 0.0,
        "week_usd": _money(merged.get("usage_weekly")) or 0.0,
        "month_usd": _money(merged.get("usage_monthly")) or 0.0,
        "all_usd": _money(merged.get("usage")) or 0.0, "pct": pct,
        "meter": "over" if spent else "warn" if pct is not None and pct >= warn else "",
        "warn_pct": warn, "spent": spent,
        "reset_text": (f"resets {reset} · {clock['utc']}" if reset else "does not reset"),
        "reset_both": clock["both"] if reset else "", "reset_short": clock["short"] if reset else "",
    }
    current = {"daily": "Today", "weekly": "This week", "monthly": "This month"}.get(reset)
    periods = [{"label": "Today", "usd": key["today_usd"], "current": current == "Today"},
               {"label": "This week", "usd": key["week_usd"], "current": current == "This week"},
               {"label": "This month", "usd": key["month_usd"], "current": current == "This month"}]
    retry = None if level == "stopped" else _retry_view(_live_retry(settings, now))
    banner = _banner(level, key, active_stop, warn, period_word)
    period_key = f"{warn}:{reset or 'all'}:{_bucket(now, reset)}"
    base.update({
        "configured": True, "level": level, "header": _header(level), "banner": banner, "retry": retry,
        "key": key, "periods": periods, "account": {"balance_usd": balance, "bought_usd": bought},
        "run_budget": {"flag": "--budget", "per": "Per run", "text": COPY["run_budget"]},
        "can_edit": bool(raw.get("can_edit")), "edit_why": raw.get("edit_why") or "",
        "read_only": not raw.get("can_edit"), "period_key": period_key,
        "recovered": bool(stop) and not active_stop,
        "events": [_event_row(e, tz) for e in sorted(settings["events"], key=lambda e: e.get("ts") or "", reverse=True)],
    })
    return base


def _shell(cfg: dict, raw: dict, settings: dict, warn: int) -> dict:
    mgmt = _management_view(cfg, raw)
    missing = raw.get("missing")
    level = "missing" if missing else "error" if raw.get("error") and not raw.get("key") else "ok"
    return {
        "configured": False, "can_edit": False, "edit_why": raw.get("edit_why") or mgmt["detail"],
        "read_only": True, "level": level, "warn_pct": warn, "fetched_at": raw.get("fetched_at"),
        "stale": raw.get("stale"), "error": raw.get("error"), "missing": missing,
        "header": _header("ok"), "banner": None, "retry": None, "key": None, "periods": [],
        "account": {"balance_usd": None, "bought_usd": None}, "management": mgmt,
        "run_budget": {"flag": "--budget", "per": "Per run", "text": COPY["run_budget"]},
        "links": {"credits": CREDITS_URL}, "events": [], "figures": COPY["figures"],
        "cap_help": COPY["cap_help"], "topup": COPY["topup"], "period_key": "", "recovered": False,
    }


def _management_view(cfg: dict, raw: dict) -> dict:
    configured = bool(raw.get("management_configured"))
    expires = raw.get("management_expires")
    error = raw.get("management_error")
    if not configured:
        text, detail = "Management key: not set", _mgmt_missing(cfg)
    elif error:
        text, detail = "Management key: refused", error
    elif expires:
        text, detail = f"Management key: set, expires {expires}", COPY["mgmt_kept"]
    else:
        text, detail = "Management key: set, no expiry", COPY["mgmt_kept"]
    return {"configured": configured, "expires_at": raw.get("management_expires_at"),
            "expires_label": expires or "", "error": error, "text": text, "detail": detail}


def _mgmt_missing(cfg: dict) -> str:
    path = (cfg.get("paths") or {}).get("openrouter_management_key") or "secrets/openrouter_management_secret.txt"
    return (f"Set {MGMT_ENV}, or put the key in {path}, to change the limit from here. "
            "The panel can still read the clippa key.")


def _missing_inference(cfg: dict) -> str:
    from . import llm

    path = (cfg.get("paths") or {}).get("openrouter_key") or "secrets/openrouter_secret.txt"
    return (f"OpenRouter isn't configured. Set {llm.PROVIDERS['openrouter'][1]}, or put the key in {path}, "
            "to see the clippa key's limit here.")


def _level(disabled: bool, spent: bool, stop, pct: float | None, warn: int) -> str:
    if disabled:
        return "disabled"
    if spent or stop:
        return "stopped"
    if pct is not None and pct + 1e-9 >= warn:
        return "warn"
    return "ok"


def _banner(level: str, key: dict, stop, warn: int, period_word: str) -> dict | None:
    if level == "disabled":
        return {"kind": "bad", "title": COPY["disabled_title"], "body": COPY["disabled_body"]}
    if level == "stopped":
        status = (stop or {}).get("status") or 402
        code = (stop or {}).get("code") or "openrouter_key_limit"
        when = f" on {key['reset_both']}" if key.get("reset_both") else ""
        if code == "openrouter_credits":
            title, body_key = COPY["stop_title_credits"], "stop_body_credits"
        else:
            title, body_key = COPY["stop_title"], "stop_body" if key.get("limit_reset") else "stop_body_once"
        return {"kind": "bad", "title": title,
                "body": COPY[body_key].format(status=status, code=code, where=_where(stop), when=when)}
    if level == "warn" and key.get("pct") is not None:
        left = usd(key["left_usd"] or 0)
        if key.get("limit_reset"):
            body = COPY["warn_body"].format(left=left, when=key.get("reset_both") or "")
        else:
            body = COPY["warn_body_once"].format(left=left)
        return {"kind": "warn", "title": COPY["warn_title"].format(pct=round(key["pct"]), period=period_word),
                "body": body}
    return None


def _retry_view(raw: dict | None) -> dict | None:
    if not raw:
        return None
    status = raw.get("status") or 402
    return {"until": raw.get("until"), "seconds": raw.get("seconds"), "code": raw.get("code"),
            "status": status, "body": COPY["retry_body"].format(status=status)}


def _bucket(now: datetime, reset: str | None) -> str:
    now = now.astimezone(UTC)
    if reset == "daily":
        return now.strftime("%Y-%m-%d")
    if reset == "weekly":
        year, week, _ = now.isocalendar()
        return f"{year}-W{week:02d}"
    if reset == "monthly":
        return now.strftime("%Y-%m")
    return "all"


def _scrub(obj, secrets: list[str]):
    bad = [s for s in secrets if isinstance(s, str) and len(s) >= 12]

    def walk(v):
        if isinstance(v, str):
            for s in bad:
                if s in v:
                    v = v.replace(s, "••••")
            return v
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items() if k != "hash"}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return walk(obj)


def limit_preview(view: dict, limit: float, reset: str | None, now: datetime, tz: ZoneInfo) -> dict:
    """The confirm step: Now → After, and the PATCH body, without calling OpenRouter."""
    key = view.get("key") or {}
    name = key.get("name") or "clippa key"
    old_reset = key.get("limit_reset")
    old_limit = key.get("limit_usd")
    usages = {"usage_daily": key.get("today_usd"), "usage_weekly": key.get("week_usd"),
              "usage_monthly": key.get("month_usd"), "usage": key.get("all_usd")}
    new_used = _window_usage(usages, reset)
    left_after = round(max(0.0, limit - new_used), 2)
    old_clock = _clock(next_reset(now, old_reset) if old_limit is not None or old_reset else None, tz)
    if old_limit is None:
        old_clock = _clock(None, tz)
    new_clock = _clock(next_reset(now, reset), tz)
    window = _PERIOD.get(reset, _PERIOD[None])[2]
    same_limit = old_limit is not None and abs(old_limit - limit) < 0.001
    same_reset = old_reset == reset
    rows = [
        _row("Spending limit", "none" if old_limit is None else usd(old_limit), usd(limit), not same_limit),
        _row("Resets", _reset_word(old_reset) if old_limit is not None or old_reset else "none",
             _reset_word(reset), not same_reset),
        _row("Next reset", old_clock["short"] if old_reset else "does not reset",
             new_clock["utc"] if reset else "does not reset", not same_reset, new_clock["note"] if reset else ""),
        _row("Left", "no cap" if key.get("left_usd") is None else usd(key.get("left_usd") or 0),
             f"≈ {usd(left_after)}", True,
             COPY["left_estimate"].format(limit=usd(limit), used=usd(new_used), window=window)),
    ]
    return {"title": "Change the clippa key's limit", "unchanged": bool(same_limit and same_reset),
            "rows": rows, "request": _patch_request(name, {"limit": _json_num(limit), "limit_reset": reset}),
            "confirm_label": f"Set {usd(limit)} {_reset_word(reset)}", "note": COPY["sent_with"]}


def disabled_preview(view: dict, disabled: bool) -> dict:
    key = view.get("key") or {}
    name = key.get("name") or "clippa key"
    now = "disabled" if key.get("disabled") else "enabled"
    after = "disabled" if disabled else "enabled"
    return {"title": "Disable the clippa key?" if disabled else "Enable the clippa key?",
            "callout": COPY["disable_callout"] if disabled else COPY["enable_callout"],
            "callout_tone": "bad" if disabled else "info", "unchanged": now == after,
            "rows": [_row(name, now, after, now != after, bad=disabled)],
            "request": _patch_request(name, {"disabled": disabled}),
            "confirm_label": "Disable key" if disabled else "Enable key", "danger": disabled,
            "note": COPY["not_deleted"] if disabled else COPY["sent_with"]}


def _row(label: str, now: str, after: str, changed: bool, note: str = "", bad: bool = False) -> dict:
    return {"label": label, "now": now, "after": after, "changed": changed, "note": note, "bad": bad}


def _reset_word(reset: str | None) -> str:
    return reset or "with no reset"


def _json_num(n: float):
    return int(n) if float(n) == int(n) else n


def _patch_request(name: str, body: dict) -> dict:
    return {"method": "PATCH", "path": f"/api/v1/keys/{{{name} hash}}", "body": body}


def _msg(r) -> str:
    try:
        err = r.json().get("error", {})
        if isinstance(err, dict):
            err = err.get("message") or ""
        return str(err)[:200]
    except (ValueError, AttributeError):
        return (getattr(r, "text", "") or "")[:200]


def _request(method: str, url: str, key: str, body: dict | None = None):
    import requests

    headers = {"Authorization": f"Bearer {key}", "User-Agent": "clippa"}
    if method == "GET":
        return requests.get(url, headers=headers, timeout=TIMEOUT)
    headers["Content-Type"] = "application/json"
    return requests.patch(url, headers=headers, json=body, timeout=TIMEOUT)


def _expires_label(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def _match_listed(label: str, rows: list) -> dict | None:
    """The management list's row for the clippa key: the one row whose label is the key's own.
    Nothing else counts, not even a list of one: a PATCH goes to the row's hash, so a guess
    would change or disable some other key while the panel shows the clippa key."""
    def norm(s: str) -> str:
        return re.sub(r"\u2026+|[.\u00b7]{2,}", "...", (s or "").strip().lower())  # "\u2026" is "..."

    want = norm(label)
    if not want:
        return None
    hits = [r for r in rows if isinstance(r, dict) and norm(str(r.get("label") or "")) == want]
    return hits[0] if len(hits) == 1 else None


def _load_remote(cfg: dict) -> dict:
    """Ask OpenRouter. A network error on the inference key propagates; a refusal is a view."""
    inf, mgmt = inference_key(cfg), management_key(cfg)
    secrets = [inf, mgmt]
    fetched = now_iso()
    if not inf:
        return {"missing": _missing_inference(cfg), "secrets": secrets, "fetched_at": fetched,
                "management_configured": bool(mgmt), "can_edit": False, "edit_why": _mgmt_missing(cfg)}
    r = _request("GET", f"{BASE}/key", inf)
    if r.status_code in (401, 403):
        return {"error": f"OpenRouter refused the key (HTTP {r.status_code})", "secrets": secrets,
                "fetched_at": fetched, "management_configured": bool(mgmt), "can_edit": False,
                "edit_why": "OpenRouter refused the clippa key."}
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}: {_msg(r)}")
    key = (r.json() or {}).get("data") or {}
    if not isinstance(key, dict):
        key = {}
    credits = _optional_json(f"{BASE}/credits", inf) or (_optional_json(f"{BASE}/credits", mgmt) if mgmt else None)
    listed, mgmt_error, expires_at = _management_lookup(mgmt, str(key.get("label") or ""))
    if listed and listed.get("hash"):
        secrets.append(str(listed["hash"]))
    can_edit = bool(listed and listed.get("hash"))
    why = ""
    if not mgmt:
        why = _mgmt_missing(cfg)
    elif mgmt_error and not can_edit:
        why = mgmt_error
    elif not can_edit:
        why = "The management key can't see this clippa key, so the limit can't be changed from here."
    return {"key": key, "credits": (credits or {}).get("data") if isinstance(credits, dict) else None,
            "listed": listed, "secrets": secrets, "fetched_at": fetched,
            "management_configured": bool(mgmt), "management_error": mgmt_error if not can_edit else None,
            "management_expires_at": expires_at, "management_expires": _expires_label(expires_at),
            "can_edit": can_edit, "edit_why": "" if can_edit else why}


def _optional_json(url: str, key: str) -> dict | None:
    if not key:
        return None
    try:
        r = _request("GET", url, key)
    except OSError:
        return None
    if not r.ok:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _management_lookup(mgmt: str, label: str) -> tuple[dict | None, str | None, str | None]:
    """(matched key or None, error or None, management-key expiry or None)."""
    if not mgmt:
        return None, None, None
    info = _optional_json(f"{BASE}/key", mgmt)
    expires = None
    if isinstance(info, dict):
        data = info.get("data") if isinstance(info.get("data"), dict) else info
        expires = data.get("expires_at") if isinstance(data, dict) else None
    try:
        r = _request("GET", f"{BASE}/keys?include_disabled=true", mgmt)
    except OSError as e:  # the panel still shows the inference key
        return None, f"couldn't reach OpenRouter to list keys ({type(e).__name__})", expires
    if r.status_code in (401, 403):
        return None, f"OpenRouter refused the management key (HTTP {r.status_code})", expires
    if not r.ok:
        return None, f"OpenRouter's key list failed (HTTP {r.status_code})", expires
    try:
        rows = (r.json() or {}).get("data") or []
    except ValueError:
        return None, "OpenRouter's key list wasn't JSON", expires
    if not isinstance(rows, list):
        rows = []
    return _match_listed(label, rows), None, expires


def _cache_key(cfg: dict) -> str:
    return str((cfg.get("paths") or {}).get("streams") or "")


def _finish(cfg: dict, raw: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    settings = _settings(cfg)
    view = view_from(cfg, raw, settings, now)
    if view.get("recovered") or _retry_expired(settings, now):
        judged = (settings.get("stop") or {}).get("at")

        def clear(d: dict) -> None:
            # only the stop judged recovered: a refusal recorded since this poll read the file stays
            if view.get("recovered") and isinstance(d.get("stop"), dict) and d["stop"].get("at") == judged:
                d.pop("stop", None)
            waiting = d.get("retry")
            if isinstance(waiting, dict):
                try:
                    until = datetime.fromisoformat(waiting.get("until") or "")
                except ValueError:
                    until = None
                if until is not None and until.tzinfo is None:
                    until = until.replace(tzinfo=UTC)
                if until is None or until <= now:
                    d.pop("retry", None)
        if _path(cfg) is not None:
            _quietly(cfg, clear)
            settings = _settings(cfg)
            view = view_from(cfg, raw, settings, now)
    if (view.get("level") == "warn" and view.get("key") and view["key"].get("limit_usd") is not None
            and settings.get("warned_for") != view.get("period_key")):
        key = view["key"]
        def mark(d: dict) -> None:
            if d.get("warned_for") == view.get("period_key"):  # another poll got here first
                return
            d.setdefault("events", []).append({
                "ts": now_iso(), "kind": "passed", "pct": settings["warn_pct"],
                "limit": key["limit_usd"], "period": key.get("reset_label") or "monthly"})
            d["events"] = d["events"][-EVENTS_MAX:]
            d["warned_for"] = view.get("period_key")
        if _path(cfg) is not None:
            _quietly(cfg, mark)
            settings = _settings(cfg)
            view = view_from(cfg, raw, settings, now)
    return _scrub(view, list(raw.get("secrets") or []))


def _retry_expired(settings: dict, now: datetime) -> bool:
    waiting = settings.get("retry")
    if not isinstance(waiting, dict) or not waiting.get("until"):
        return False
    try:
        until = datetime.fromisoformat(waiting["until"])
    except ValueError:
        return True
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return until <= now


def status(cfg: dict, *, fresh: bool = False, now: datetime | None = None) -> dict:
    """The panel. Cached for ``TTL`` seconds, failures included; ``fresh`` asks OpenRouter again."""
    ck = _cache_key(cfg)
    hit = _CACHE.get(ck)
    if hit and not fresh and time.monotonic() - hit["at"] < TTL:
        return _finish(cfg, hit["raw"], now)
    try:
        raw = _load_remote(cfg)
    except Exception as e:  # noqa: BLE001  # a panel: keep the last good answer
        err = f"{type(e).__name__}: {e}"[:300]
        if hit and (hit["raw"].get("key") or hit["raw"].get("missing")):
            raw = {**hit["raw"], "stale": err[:200]}
        else:
            raw = {"error": err, "fetched_at": now_iso(), "secrets": [],
                   "management_configured": bool(management_key(cfg))}
    _CACHE[ck] = {"at": time.monotonic(), "raw": raw}
    return _finish(cfg, raw, now)


def set_warn(cfg: dict, pct) -> dict:
    """Save the warning percent (1–99) and return the panel with it applied."""
    n = parse_warn(pct)

    def apply(d: dict) -> None:
        d["warn_pct"] = n
        d.pop("warned_for", None)  # a new threshold is a new crossing

    _write(cfg, apply)
    return status(cfg)


def apply_limit(cfg: dict, limit, reset, *, confirm: bool = False, now: datetime | None = None) -> dict:
    """Preview or apply a new spending limit. ``confirm`` false does not call OpenRouter."""
    now = now or datetime.now(UTC)
    view = status(cfg, now=now)
    _need_edit(view)
    new_limit, new_reset = parse_limit(limit), parse_reset(reset)
    preview = limit_preview(view, new_limit, new_reset, now, _tz(cfg))
    if not confirm:
        return {"preview": preview}
    if preview["unchanged"]:
        raise ClipperError("that's already the limit")
    _patch_key(cfg, preview["request"]["body"])
    before = (view.get("key") or {}).get("limit_usd")
    before_reset = (view.get("key") or {}).get("limit_reset")

    def apply(d: dict) -> None:
        d.setdefault("events", []).append({
            "ts": now_iso(), "kind": "changed", "limit": new_limit, "reset": new_reset,
            "before_limit": before, "before_reset": before_reset})
        d["events"] = d["events"][-EVENTS_MAX:]

    _quietly(cfg, apply)
    _CACHE.pop(_cache_key(cfg), None)
    return status(cfg, fresh=True, now=now)


def apply_disabled(cfg: dict, disabled, *, confirm: bool = False) -> dict:
    """Preview or apply enabling or disabling the clippa key."""
    view = status(cfg)
    _need_edit(view)
    flag = parse_disabled(disabled)
    preview = disabled_preview(view, flag)
    if not confirm:
        return {"preview": preview}
    if preview["unchanged"]:
        raise ClipperError("the key is already " + ("disabled" if flag else "enabled"))
    _patch_key(cfg, preview["request"]["body"])

    def apply(d: dict) -> None:
        d.setdefault("events", []).append({"ts": now_iso(), "kind": "disabled" if flag else "enabled"})
        d["events"] = d["events"][-EVENTS_MAX:]

    _quietly(cfg, apply)
    _CACHE.pop(_cache_key(cfg), None)
    return status(cfg, fresh=True)


def _need_edit(view: dict) -> None:
    if view.get("can_edit"):
        return
    why = view.get("edit_why") or "set a management key to change the clippa key from here"
    raise ClipperError(f"{why}")


def _patch_key(cfg: dict, body: dict) -> None:
    listed = (((_CACHE.get(_cache_key(cfg)) or {}).get("raw") or {}).get("listed")) or {}
    digest = listed.get("hash") or ""
    key = management_key(cfg)
    if not key or not digest:
        raise ClipperError("set a management key that can see this clippa key before changing it")
    r = _request("PATCH", f"{BASE}/keys/{digest}", key, body)
    if not r.ok:
        msg = (_msg(r) or "no details").replace(digest, "<hash>")
        for secret in (key, inference_key(cfg)):
            if secret:
                msg = msg.replace(secret, "<key>")
        raise ClipperError(f"OpenRouter refused the change (HTTP {r.status_code}): {msg}")
