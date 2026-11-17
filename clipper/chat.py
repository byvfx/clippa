"""The Clippa messenger (#158): one box where you tell clipper what to do, answered by Clippa.

A message goes, in order, to:
  1. a box command: /new, /bye, /clear, /history, /cost, /away, /back, /help
  2. a local match (match()): "what goes out tomorrow", "approve s02", "pin c03 fri 15:00",
     "story <brief>", an item's name alone... instant and free
  3. one model call ([chat] model, Haiku; llm.tool_call, ledger stage "chat") that can only
     pick a catalog tool (clipper/catalog.py) and fill in its inputs; past [chat] daily_usd it
     isn't made
The tool's risk decides the rest: a read answers inline, a write or a spend posts a confirm
card (a spend with its estimate), and nothing happens until the card is tapped. A tap checks
the item again (it may have been decided from Discord meanwhile) before acting.

Topics: the chat is one running conversation; a topic is a stretch of it, and the model sees
only the current topic's messages. A new topic comes from /new (or the page's button), /bye,
or by itself after [chat] quiet_hours without a message; the old one ends with a receipt
(messages, actions, model cost) and its open cards expire. /clear moves the chat to
<[paths] chats>/archive/ and starts an empty one; Kitsu keeps the decisions either way.

Spends run as `clipper <argv>` child processes; when one ends its report goes into whichever
topic is open then (or waits while you're /away). Stored by serve in [paths] chats/current.json
through jsonstate, so the phone and the laptop see the same chat.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import catalog, jsonstate
from .errors import ClipperError

log = logging.getLogger("clipper.chat")

MAX_TEXT = 2000          # characters of one message
KEEP = 600               # messages kept in current.json; older ones go to the archive
JOB_TIMEOUT_S = 4 * 3600
COMMANDS = ("/new", "/bye", "/clear", "/history", "/cost", "/away", "/back", "/help")
HELP = ("Tell me what you want done, in your own words. For example:\n"
        "what goes out tomorrow · what's waiting · unslotted · retakes · health · costs\n"
        "approve s02 · approve s02 from kino 11-14 · reject c03 too long · hold s04 · release s04 · "
        "pin c03 fri 15:00 · slot c03 · retitle s02 A Better Title\n"
        "approve all comps · approve s07 and s09 from kino · undo that\n"
        "story <who, what happened> · new cover s02\n"
        "/new a fresh topic · /bye end this one · /history · /cost · /away quiet mode · "
        "/back · /clear archive the chat\n"
        "Anything that changes something waits for your tap. I never upload, change config "
        "or code, deploy, or touch the recorder.")

_LOCK = threading.RLock()        # one change to the chat at a time in this process
_JOBS: dict[str, threading.Thread] = {}
BOOT = f"{os.getpid()}-{time.time_ns():x}"   # this process; a job an earlier serve started is lost
_CHECKED = {"lost": False}


# ---------- the store ----------

def folder(cfg: dict) -> Path:
    return Path(cfg["paths"]["chats"])


def path(cfg: dict) -> Path:
    return folder(cfg) / "current.json"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


_SEQ = {"n": 0}


def _id(prefix: str) -> str:
    _SEQ["n"] += 1
    return f"{prefix}{time.time_ns() // 1000:x}{_SEQ['n'] % 1000:03d}"


def empty() -> dict:
    return {"messages": [], "topics": [], "jobs": [], "away": False, "held": []}


def load(cfg: dict) -> dict:
    return {**empty(), **(jsonstate.read_or(path(cfg), {}) or {})}


@contextmanager
def _update(cfg: dict) -> Iterator[dict]:
    """The chat under its lock, changed in place and written back (nothing if the block raises)."""
    path(cfg).parent.mkdir(parents=True, exist_ok=True)
    with jsonstate.update(path(cfg), empty()) as chat:
        for k, v in empty().items():
            chat.setdefault(k, v)
        yield chat


def current_topic(chat: dict) -> dict | None:
    t = chat["topics"][-1] if chat["topics"] else None
    return t if t and not t.get("ended_at") else None


def last_at(chat: dict, topic: dict) -> str:
    """When the topic last had a message from you or Clippa (its start if none)."""
    mine = [m["at"] for m in chat["messages"] if m.get("topic") == topic["id"] and m["who"] != "system"]
    return max(mine) if mine else topic["started_at"]


def quiet(chat: dict, topic: dict, hours: float, now: datetime) -> bool:
    return now - datetime.fromisoformat(last_at(chat, topic)) >= timedelta(hours=hours)


def receipt(chat: dict, topic: dict) -> dict:
    """A topic's messages (yours and Clippa's), the actions its cards did, its model cost."""
    mine = [m for m in chat["messages"] if m.get("topic") == topic["id"]]
    done = sum(1 for m in mine if (m.get("card") or {}).get("state") == "done")
    return {"messages": sum(1 for m in mine if m["who"] in ("me", "clippa")), "actions": done,
            "usd": round(sum(float(m.get("usd") or 0) for m in mine), 4)}


def end_topic(chat: dict, why: str) -> dict | None:
    """End the open topic with a receipt divider (its open cards expire); None when none was open."""
    t = current_topic(chat)
    if not t:
        return None
    t["ended_at"], t["why"] = now_iso(), why
    t["receipt"] = r = receipt(chat, t)
    for m in chat["messages"]:
        if m.get("topic") == t["id"] and (m.get("card") or {}).get("state") == "open":
            m["card"]["state"] = "expired"
    text = (f"topic ended ({why}): {r['messages']} messages, {r['actions']} "
            f"action{'s' * (r['actions'] != 1)}, ${r['usd']:.3f}")
    return _post(chat, "system", text, kind="divider", topic=t["id"], receipt=r)


def open_topic(chat: dict, why: str = "") -> dict:
    t = {"id": _id("t"), "started_at": now_iso(), "why": why}
    chat["topics"] = [*chat["topics"][-200:], t]
    return t


def ensure_topic(chat: dict, cfg: dict, now: datetime | None = None) -> dict:
    """The open topic; after [chat] quiet_hours of quiet the old one ends and a new one opens."""
    t = current_topic(chat)
    if t and quiet(chat, t, cfg["chat"]["quiet_hours"], now or datetime.now(UTC)):
        end_topic(chat, f"{cfg['chat']['quiet_hours']:g} h quiet")
        t = None
    return t or open_topic(chat)


def _post(chat: dict, who: str, text: str, kind: str = "text", topic: str | None = None, **extra) -> dict:
    m = {"id": _id("m"), "at": now_iso(), "who": who, "text": text, "kind": kind,
         "topic": topic or (current_topic(chat) or {}).get("id"), **extra}
    chat["messages"].append(m)
    return m


def _archive(cfg: dict, data: dict, why: str) -> Path:
    d = folder(cfg) / "archive"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{datetime.now(UTC):%Y-%m-%dT%H%M%S}-{_id('a')}-{why}.json"   # never the same name twice
    jsonstate.write(p, data)
    return p


def _trim(cfg: dict, chat: dict) -> None:
    """current.json keeps the last KEEP messages; older ones go to the archive as they are. The
    open topic stays whole (its cards and questions can still be tapped) up to 2 x KEEP."""
    msgs = chat["messages"]
    if len(msgs) <= KEEP:
        return
    cut = len(msgs) - KEEP
    t = current_topic(chat)
    if t:
        first = next((i for i, m in enumerate(msgs) if m.get("topic") == t["id"]), len(msgs))
        cut = min(cut, first) if len(msgs) - first <= 2 * KEEP else cut
    if cut > 0:
        old, chat["messages"] = msgs[:cut], msgs[cut:]
        _archive(cfg, {"messages": old, "topics": [], "jobs": []}, "overflow")


def view(chat: dict, since: str = "") -> dict:
    """What the page shows: the messages after `since` (all of them for ""), the open topic,
    running jobs, away. full: the page should redraw (since was unknown, or "")."""
    msgs, full = chat["messages"], True
    if since:
        i = next((n for n, m in enumerate(msgs) if m["id"] == since), None)
        if i is not None:
            msgs, full = msgs[i + 1:], False
    t = current_topic(chat)
    cards = {m["card"]["id"]: m["card"]["state"] for m in chat["messages"][-200:] if m.get("card")}
    return {"messages": msgs, "full": full, "topic": t["id"] if t else "", "away": chat["away"], "cards": cards,
            "jobs": [{"id": j["id"], "tool": j["tool"], "started_at": j["started_at"]}
                     for j in chat["jobs"] if j["state"] == "running"],
            "last": chat["messages"][-1]["id"] if chat["messages"] else ""}


# ---------- local matching ----------

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAYS = (r"(today|tonight|tomorrow|this week|week|" + "|".join(WEEKDAYS) + "|"
        + "|".join(d[:3] for d in WEEKDAYS) + r"|\d{4}-\d{2}-\d{2})")


def parse_when(text: str, now: datetime) -> str | None:
    """"YYYY-MM-DD HH:MM" (or "YYYY-MM-DD", that day's first slot) from "tomorrow 3pm",
    "fri 15:00", "2026-11-20 9:30", "today at 21:00", in now's time zone; None otherwise."""
    t = " ".join(text.lower().replace(" at ", " ").split())
    t = re.sub(r"^(?:for|on|at) ", "", t)   # "pin s02 for fri 9am"
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2}|today|tonight|tomorrow|[a-z]{3,9})"
                     r"(?: (\d{1,2})(?::(\d{2}))? ?(am|pm)?)?", t)
    if not m:
        return None
    word, hh, mm, ampm = m.groups()
    short = [d[:3] for d in WEEKDAYS]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", word):
        try:
            day = datetime.strptime(word, "%Y-%m-%d").date()
        except ValueError:
            return None
    elif word in ("today", "tonight"):
        day = now.date()
    elif word == "tomorrow":
        day = now.date() + timedelta(days=1)
    elif word in WEEKDAYS or word in short:
        day = now.date() + timedelta(days=(short.index(word[:3]) - now.weekday()) % 7)
    else:
        return None
    if not hh:
        return None if word == "tonight" else f"{day:%Y-%m-%d}"
    h, mi = int(hh), int(mm or 0)
    if not ampm and not mm and word != "tonight" and 1 <= h <= 11:
        return None   # "fri 3": morning or afternoon? the model asks, or reads it from the chat
    if ampm == "pm" or (word == "tonight" and not ampm):
        h += 12 if h < 12 else 0
    elif ampm == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    if word in WEEKDAYS or word in short:   # today's weekday: today while that time is ahead
        if day == now.date() and (h, mi) <= (now.hour, now.minute):
            day += timedelta(days=7)
    return f"{day:%Y-%m-%d} {h:02d}:{mi:02d}"


VERBS = {  # first word(s) -> (tool, what the rest of the line is)
    "approve": ("approve", "note"),
    "reject": ("reject", "reason"),
    "retake": ("retake", "note"), "changes": ("retake", "note"),
    "reopen": ("reopen", ""),
    "hold": ("hold", ""), "release": ("hold", ""), "unhold": ("hold", ""),
    "pin": ("pin", "when"), "unpin": ("pin", ""),
    "slot": ("slot", ""),
    "retitle": ("retitle", "title"),
    "cover text": ("cover_text", "text"), "cover words": ("cover_text", "text"),
    "new cover": ("cover_redo", ""), "redo cover": ("cover_redo", ""),
    "show": ("item", ""),
}
NEEDS_TEXT = ("reject", "retake", "retitle", "cover_text")   # prepare() asks when it's missing
STREAM_ID = r"(?:kick-\d+|yt-[\w-]+|story-[\w-]+)"
ITEM_REF = re.compile(r"((?:[\w.-]+/)?(?:[a-z]{1,4}\d+[\w.-]*|comp-[\w.-]+))"
                      r"(?:\s+(" + STREAM_ID + r"))?\s*(.*)", re.IGNORECASE)
STREAM_IN = re.compile(r"(?:^|\s)(?:(?:from|on|in|of)\s+)?(" + STREAM_ID + r")(?=\s|$)", re.IGNORECASE)


REF_WORD = re.compile(r"\b(?:[\w.-]+/)?(?:[a-z]{1,4}\d+[\w.-]*|comp-[\w.-]+)", re.IGNORECASE)
# "from kino 11-14", "on the kino one": a stream the way people name it, resolved by catalog.find_item
# only "from <show>", "on the <show> one" or "<on|in|of> <show> 11-14": "on friday", "on fire" stay
# a note or a pin time
NOT_SHOW = r"(?!(?:today|tonight|tomorrow|" + "|".join(WEEKDAYS) + "|" + "|".join(d[:3] for d in WEEKDAYS) + r")\b)"
SHOW_IN = re.compile(r"(?:from\s+(?:the\s+)?" + NOT_SHOW + r"([a-z][\w-]*)(?:\s+(\d{1,2}[-/]\d{1,2}))?(?:\s+(?:one|stream|show))?"
                     r"|(?:on|in|of)\s+the\s+" + NOT_SHOW + r"([a-z][\w-]*)(?:\s+(\d{1,2}[-/]\d{1,2}))?\s+(?:one|stream|show)"
                     r"|(?:on|in|of)\s+" + NOT_SHOW + r"([a-z][\w-]*)\s+(\d{1,2}[-/]\d{1,2}))(?=\s|$)\s*(.*)", re.IGNORECASE)


def _verb(t: str, low: str, now: datetime) -> tuple[str, dict] | None:
    for words in sorted(VERBS, key=len, reverse=True):
        if not low.startswith(words + " "):
            continue
        tool, rest_name = VERBS[words]
        m = ITEM_REF.fullmatch(t[len(words) + 1:].strip())
        if not m:
            return None   # "approve the blade one": words, not a name; the model's turn
        stream, rest = m.group(2), m.group(3).strip()
        s = None if stream or "/" in m.group(1) else STREAM_IN.search(rest)
        if s:   # "approve s02 from kick-131151926": the stream, not a note
            stream, rest = s.group(1), (rest[:s.start()] + " " + rest[s.end():]).strip()
        elif not stream and "/" not in m.group(1) and (n := SHOW_IN.match(rest)):
            stream, rest = " ".join(x for x in n.groups()[:6] if x), n.group(7).strip()   # "from kino 11-14"
        if REF_WORD.search(rest) and words not in ("reject", "retake", "changes", "retitle", "cover text",
                                                   "cover words"):
            return None   # "approve s01 and s02 ...": a second name isn't a note; _bulk or the model
        args: dict = {"item": m.group(1) + (f" {stream}" if stream else "")}
        if words in ("release", "unhold"):
            args["release"] = True
        if words == "unpin":
            args["unpin"] = True
        if rest_name == "when":
            when = parse_when(rest, now) if rest else None
            if not when:
                return None
            args["when"] = when
        elif rest_name and rest:
            args[rest_name] = rest
        elif rest and not rest_name:
            return None
        return tool, args   # a write without its text (NEEDS_TEXT): prepare() asks for it
    return None


BULK_VERBS = {"approve": "approve", "reject": "reject", "hold": "hold", "release": "release",
              "unhold": "release", "reopen": "reopen"}
KIND_OF = {"comp": "compilation", "comps": "compilation", "compilation": "compilation",
           "compilations": "compilation", "segment": "segment", "segments": "segment", "seg": "segment",
           "segs": "segment", "short": "short", "shorts": "short", "clip": "pick", "clips": "pick",
           "pick": "pick", "picks": "pick"}
DAY = re.compile(r"\d{1,2}[-/]\d{1,2}")


def _bulk(t: str) -> tuple[str, dict] | None:
    """Several items on one card: "approve all comps", "hold the shorts from kino 11-14",
    "approve s07 and s09 from the kino one", "reject c01, c02 too long". None for the rest."""
    first, _, rest = t.partition(" ")
    verb = BULK_VERBS.get(first.lower())
    if not verb or not rest:
        return None
    words = re.sub(r"\s*[,&+]\s*", " , ", rest).split()
    low = [w.lower().rstrip(".!?") for w in words]
    i, refs, kind, every, plural = 0, [], "", False, False
    while i < len(low) and low[i] in ("all", "every", "everything", "both", "the", "of"):
        every |= low[i] in ("all", "every", "everything", "both")
        i += 1
    while i < len(low) and (low[i] in (",", "and") or REF_WORD.fullmatch(low[i])):
        if low[i] not in (",", "and"):
            refs.append(words[i].rstrip(".!?,"))
        i += 1
    if not refs and i < len(low) and low[i] in KIND_OF:
        kind, plural = KIND_OF[low[i]], low[i].endswith("s")
        i += 1
    while i < len(low) and low[i] in ("waiting", "pending", "left", "open"):
        i += 1
    stream = ""
    if i < len(low) and (re.fullmatch(STREAM_ID, low[i], re.IGNORECASE)
                         or (low[i] in ("from", "on", "in", "of") and i + 1 < len(low))):
        i += low[i] in ("from", "on", "in", "of")
        i += i < len(low) - 1 and low[i] == "the"
        stream, i = words[i].rstrip(".!?,"), i + 1
        if i < len(low) and DAY.fullmatch(low[i]):
            stream, i = f"{stream} {low[i]}", i + 1
        i += i < len(low) and low[i] in ("one", "stream", "show", "night")   # "from last night"
    left = re.sub(r"\b(?:pls|please|plz|thx|thanks|for me|now)\b", "", " ".join(words[i:])).strip(" ,.!")
    if len(refs) >= 2:
        args: dict = {"verb": verb, "items": refs}
    elif not refs and (every or plural) and (kind or stream or every):
        args = {"verb": verb, **({"kind": kind} if kind else {})}
    else:
        return None
    if stream:
        args["stream"] = stream
    if verb == "reject":
        if left:
            args["reason"] = left
    elif left:
        return None   # words we don't know: the model's turn
    return "bulk", args


def match(text: str, now: datetime) -> tuple[str, dict] | None:
    """(tool, args) for the phrases clipper knows without a model; None for the rest."""
    t = " ".join(text.strip().split())
    low = t.lower().rstrip("?!.")
    m = re.search(r"(?:goes|going|go)\s+out\s+(?:on\s+)?" + DAYS + "$", low) \
        or re.fullmatch(r"(?:schedule|releases)(?:\s+(?:for\s+|on\s+)?" + DAYS + ")?", low)
    if m:
        day = m.group(1) or "today"
        return "releases", {"day": "today" if day == "tonight" else day}
    if re.fullmatch(r"(?:what(?:'?s| is)\s+)?(?:unslotted|not scheduled|without a slot|not going out)", low):
        return "unslotted", {}
    if re.fullmatch(r"(?:what(?:'?s| is)\s+)?(?:waiting|pending|open items|items|to review|needs me)(?: on me)?", low):
        return "items", {}
    if re.fullmatch(r"(?:any\s+)?retakes?(?: waiting| pending)?", low):
        return "retakes", {}
    if re.fullmatch(r"(?:health|status|services|who(?:'?s| is) (?:up|online)|how(?:'?s| is) everything)", low):
        return "health", {}
    m = re.fullmatch(r"(?:costs?|spend|spent)(?:\s+(today|month|this month))?", low)
    if m:
        return "costs", {"period": "month" if m.group(1) and "month" in m.group(1) else "today"}
    if re.fullmatch(r"undo(?: (?:that|it|this|last|the last one|the last thing))?", low):
        return "undo", {}
    m = re.fullmatch(r"(?:story|new story)[: ]+(.{8,})", t, re.IGNORECASE)
    if m:
        return "story", {"brief": m.group(1).strip()}
    hit = _bulk(t) or _verb(t, low, now)
    if hit:
        return hit
    m = ITEM_REF.fullmatch(t)
    if m and not m.group(3):
        return "item", {"item": t}
    return None


# ---------- the model ----------

def system_prompt(cfg: dict, now: datetime) -> str:
    from .prompts import Prompts

    prompts = Prompts(cfg)
    shows = []
    for key, prof in (cfg.get("shows") or {}).items():
        meta = (prof.get("overrides") or {}).get("meta") or {}
        cast = ", ".join(meta.get("cast") or [])
        shows.append(f"- {key}: {meta.get('show_name') or key}" + (f" (cast: {cast})" if cast else ""))
    rules = "\n".join(f"- {r}" for r in prompts.house_style) or "- (none)"
    return prompts.get("chat", now=f"{now:%A %Y-%m-%d %H:%M} ({cfg['schedule']['timezone']})",
                       shows="\n".join(shows) or "- (none)", house_rules=rules)


def history(chat: dict, topic: dict, n: int) -> list[dict]:
    """The topic's last n messages as the model's turns, alternating, the first one the user's."""
    out: list[dict] = []
    mine = [m for m in chat["messages"] if m.get("topic") == topic["id"] and m["who"] in ("me", "clippa")]
    for m in mine[-n:]:
        role = "user" if m["who"] == "me" else "assistant"
        text = m["text"] + (f" [card {m['card']['state']}]" if m.get("card") else "")
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


# The day's model spend as this process knows it, beside the ledger (which another process may
# write too, and whose write is best effort): calls in flight hold their estimate, so two sends
# at once can't both slip under [chat] daily_usd.
_BUDGET = {"day": "", "spent": 0.0, "held": 0.0}
_BUDGET_LOCK = threading.Lock()


def _reserve(cfg: dict, usd: float) -> bool:
    """Hold usd of today's budget for a call; False when it doesn't fit."""
    cap = cfg["chat"]["daily_usd"]
    ledger = sum(catalog.spend_today(cfg, "chat").values()) if cap else 0.0
    with _BUDGET_LOCK:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        if _BUDGET["day"] != day:
            _BUDGET.update(day=day, spent=0.0, held=0.0)
        used = max(ledger, _BUDGET["spent"]) + _BUDGET["held"]
        if cap and (used >= cap or used + usd > cap):   # >=: a model with no price estimates $0
            return False
        _BUDGET["held"] += usd
        return True


def _settle(held: float, usd: float) -> None:
    """A call ended (or failed): its hold goes, what it cost stays."""
    with _BUDGET_LOCK:
        _BUDGET["held"] = max(0.0, _BUDGET["held"] - held)
        _BUDGET["spent"] += usd


def ask_model(cfg: dict, chat: dict, topic: dict) -> tuple[str, dict | None, float]:
    """(what it said, its pick {name, input} or None, $) from one call; no call past the budget."""
    from .llm import LLM, estimate, tokens

    if not cfg["chat"]["model"]:
        return "I only know the commands here ([chat] model is off): /help.", None, 0.0
    tools = [t.spec() for t in catalog.catalog().values()]
    now = datetime.now(ZoneInfo(cfg["schedule"]["timezone"]))
    system, turns = system_prompt(cfg, now), history(chat, topic, cfg["chat"]["history"])
    size = tokens(system + json.dumps(tools) + json.dumps(turns))
    held = estimate(cfg, cfg["chat"]["model"], size, 1024)   # the most this call can cost
    if not _reserve(cfg, held):
        return (f"I've used today's ${cfg['chat']['daily_usd']:.2f} of thinking ([chat] daily_usd); "
                "the commands still work: /help."), None, 0.0
    client = LLM(cfg, label="chat")
    try:
        out, meta = client.tool_call(cfg["chat"]["model"], system, turns, tools, cfg["chat"]["effort"])
    finally:
        _settle(held, client.cost)
    if meta.get("refusal") is not None:
        return "I can't help with that one.", None, client.cost
    return out["text"], out["tool"], client.cost


# ---------- handling a message ----------

def say(chat: dict, plan: catalog.Plan, usd: float = 0.0, preface: str = "") -> dict:
    """A plan into the chat: a card (after the model's words, if any) or an answer."""
    if plan.kind == "card":
        if preface:
            _post(chat, "clippa", preface, usd=usd)
            usd = 0.0
        return _post(chat, "clippa", plan.text, kind="card", usd=usd,
                     card={**plan.as_dict(), "state": "open", "id": _id("k")})
    extra = {"options": plan.options} if plan.options else {}
    return _post(chat, "clippa", (preface + "\n" if preface else "") + plan.text, usd=usd, **extra)


def run_tool(cfg: dict, chat: dict, name: str, args: dict, usd: float = 0.0, preface: str = "") -> dict:
    try:
        plan = catalog.prepare(cfg, name, args)
    except ClipperError as e:
        return _post(chat, "clippa", (preface + "\n" if preface else "") + f"Hmm: {e}", usd=usd)
    except Exception as e:  # noqa: BLE001 - a bad input from the model, Kitsu down: a line, not a lost message
        log.warning(f"chat tool {name}: {type(e).__name__}: {e}")
        return _post(chat, "clippa", (preface + "\n" if preface else "")
                     + f"Hmm, that didn't work ({type(e).__name__}; details in the serve log).", usd=usd)
    m = say(chat, plan, usd, preface)
    if plan.kind == "ask" and plan.options:   # a tap on an option runs the same tool on that item
        m["ask"] = {"tool": name, "args": {k: v for k, v in args.items() if k != "item"}}
    return m


def pick(cfg: dict, msg_id: str, ref: str) -> dict:
    """A tap on one of a "which one?" message's items: its tool again, on that item."""
    with _LOCK, _update(cfg) as chat:
        since = chat["messages"][-1]["id"] if chat["messages"] else ""
        m = next((x for x in chat["messages"] if x["id"] == msg_id), None)
        if not m or not m.get("ask") or ref not in [o["ref"] for o in m.get("options") or []]:
            raise ClipperError("that choice isn't there any more")
        t = ensure_topic(chat, cfg)
        if m["topic"] != t["id"]:
            _post(chat, "system", "that question is from an earlier topic: ask again")
            return view(chat, since)
        _post(chat, "me", ref)
        run_tool(cfg, chat, m["ask"]["tool"], {**m["ask"]["args"], "item": ref})
        return view(chat, since)


def command(cfg: dict, chat: dict, text: str) -> None:
    word = text.split()[0].lower()
    if word in ("/new", "/bye"):
        if not end_topic(chat, word[1:]) and word == "/bye":
            _post(chat, "system", "no topic open")
        if word == "/new":
            open_topic(chat, "/new")
        return
    if word == "/clear":
        end_topic(chat, "cleared")
        p = _archive(cfg, {k: chat[k] for k in ("messages", "topics", "jobs")}, "clear")
        running = [j for j in chat["jobs"] if j["state"] == "running"]
        away, held = chat["away"], chat["held"]
        chat.clear()
        chat.update(empty(), jobs=running, away=away, held=held)
        open_topic(chat, "/clear")
        _post(chat, "system", f"cleared: the old chat is in archive/{p.name}; Kitsu keeps every decision",
              kind="divider")
        return
    t = ensure_topic(chat, cfg)
    _post(chat, "me", text)
    if word == "/history":
        done = [x for x in chat["topics"] if x.get("ended_at")][-10:]
        arch = folder(cfg) / "archive"
        archived = len(list(arch.glob("*.json"))) if arch.is_dir() else 0
        lines = [f"{x['started_at'][:16].replace('T', ' ')} UTC ({x.get('why') or 'ended'}): "
                 f"{x['receipt']['messages']} messages, {x['receipt']['actions']} actions, "
                 f"${x['receipt']['usd']:.3f}" for x in done if x.get("receipt")]
        _post(chat, "clippa", ("Earlier topics:\n" + "\n".join(lines) if lines else "No earlier topics here.")
              + (f"\n{archived} archived chat{'s' * (archived != 1)} in chats/archive/." if archived else ""))
    elif word == "/cost":
        today = sum(catalog.spend_today(cfg, "chat").values())
        cap = cfg["chat"]["daily_usd"]
        _post(chat, "clippa", f"This topic: ${receipt(chat, t)['usd']:.3f}. My thinking today: ${today:.3f}"
              + (f" of ${cap:.2f}" if cap else "") + ". (\"costs\" for everything else.)")
    elif word == "/away":
        chat["away"] = True
        _post(chat, "clippa", "Away: no pings, and job reports wait until /back.")
    elif word == "/back":
        chat["away"] = False
        held, chat["held"] = chat["held"], []
        _post(chat, "clippa", "Welcome back." + (f" {len(held)} report{'s' * (len(held) != 1)} waited:"
                                                 if held else ""))
        for h in held:
            _post(chat, "clippa", h["text"], kind="report", job=h.get("job"))
    elif word == "/help":
        _post(chat, "clippa", HELP)
    else:
        _post(chat, "clippa", "I know " + " ".join(COMMANDS) + ".")


YES = {"yes", "yep", "yeah", "yup", "ok", "okay", "ok do it", "do it", "yes do it", "yes please", "go ahead"}
NO = {"no", "nope", "nah", "cancel", "never mind", "nevermind", "leave it", "no thanks"}


def _answer(chat: dict, text: str) -> tuple[str, bool] | None:
    """A typed yes or no right after a write card, with no other card open in the topic: (its
    id, yes). Never for a spend (its button says what it costs), nor a card Clippa has said
    something after: an "ok" then is an answer to that, not a tap."""
    word = " ".join(re.sub(r"[^\w' ]", " ", text.lower()).split())
    if word not in YES and word not in NO:
        return None
    t = current_topic(chat)
    mine = [m for m in chat["messages"] if t and m.get("topic") == t["id"] and m["who"] == "clippa"]
    open_ = [m for m in mine if (m.get("card") or {}).get("state") == "open"]
    if len(open_) != 1 or open_[0] is not mine[-1] or open_[0]["card"].get("risk") != catalog.WRITE:
        return None
    return open_[0]["card"]["id"], word in YES


def handle(cfg: dict, text: str) -> dict:
    """One message from the box; the page's view of what it added."""
    text = (text or "").strip()
    if not text:
        raise ClipperError("say something")
    if len(text) > MAX_TEXT:
        raise ClipperError(f"that's over {MAX_TEXT} characters")
    with _LOCK, _update(cfg) as chat:
        since = chat["messages"][-1]["id"] if chat["messages"] else ""
        if text.startswith("/"):
            command(cfg, chat, text)
            _trim(cfg, chat)
            return view(chat, since)
        topic = ensure_topic(chat, cfg)
        answer = _answer(chat, text)
        _post(chat, "me", text)
        if not answer:
            hit = match(text, datetime.now(ZoneInfo(cfg["schedule"]["timezone"])))
            if hit:
                run_tool(cfg, chat, *hit)
                _trim(cfg, chat)
                return view(chat, since)
        seen = {"messages": list(chat["messages"])}
    if answer:   # "ok do it" with one card open: the same as its button
        tap(cfg, *answer)
        return view(load(cfg), since)
    # the model call (a second or two) holds neither lock: other sends, taps and job reports go on
    try:
        said, pick, usd = ask_model(cfg, seen, topic)
    except Exception as e:  # noqa: BLE001 - a model outage is a chat line, not a broken page
        log.warning(f"chat model: {type(e).__name__}: {e}")
        said, pick, usd = (f"I couldn't think just now ({type(e).__name__}); the commands still "
                           "work: /help."), None, 0.0
    with _LOCK, _update(cfg) as chat:
        if (current_topic(chat) or {}).get("id") != topic["id"]:
            late(chat, topic, said, pick, usd)
        elif pick:
            run_tool(cfg, chat, pick["name"], pick["input"], usd, preface=said)
        else:
            _post(chat, "clippa", said or "Hmm, not sure what you mean. /help has examples.", usd=usd)
        _trim(cfg, chat)
        return view(chat, since)


def late(chat: dict, topic: dict, said: str, pick: dict | None, usd: float) -> dict:
    """An answer that came back after its topic ended (a /new, /bye or /clear meanwhile): it
    stays in that topic with its cost (the receipt too), and a pick becomes no card at all."""
    text = said or ("I had a card ready, but" if pick else "I had an answer, but")
    m = _post(chat, "clippa", f"{text} (that topic ended before I answered: ask again)",
              topic=topic["id"], usd=usd)
    t = next((x for x in chat["topics"] if x["id"] == topic["id"]), None)
    if t and t.get("receipt"):
        t["receipt"]["usd"] = round(t["receipt"]["usd"] + usd, 4)
    return m


# ---------- cards ----------

def find_card(chat: dict, card_id: str) -> dict | None:
    return next((m for m in chat["messages"] if (m.get("card") or {}).get("id") == card_id), None)


def tap(cfg: dict, card_id: str, yes: bool) -> dict:
    """A tap on a card: No closes it; Yes re-checks and acts (a write) or starts the job (a
    spend). A card from an ended topic has expired, and one already tapped is done with."""
    with _LOCK, _update(cfg) as chat:
        since = chat["messages"][-1]["id"] if chat["messages"] else ""
        m = find_card(chat, card_id)
        if not m:
            raise ClipperError("no such card")
        card = m["card"]
        topic = ensure_topic(chat, cfg)   # a topic gone quiet ends here and its cards expire
        if card["state"] == "open" and m["topic"] != topic["id"]:
            card["state"] = "expired"
        if card["state"] != "open":
            _post(chat, "system", f"that card is {card['state']}" + (": ask again" if card["state"] == "expired" else ""))
            return view(chat, since)
        if not yes:
            card["state"] = "cancelled"
            _post(chat, "clippa", "OK, left alone.")
            return view(chat, since)
        card.update(state="working", boot=BOOT)   # cut off by a restart: lost_jobs says so
        card = dict(card)
    try:   # the action (Kitsu, a replan) holds neither lock; "working" keeps a second tap out
        if card["risk"] == catalog.SPEND:
            stale = catalog.precheck(cfg, card)   # the item may have changed since the card
            reply, ok = (stale, False) if stale else start_job(cfg, card)
            state = "done" if ok else "stale" if stale else "failed"
        else:
            reply, ok = catalog.apply(cfg, card)
            state = "done" if ok else "stale"
    except ClipperError as e:
        reply, state = f"Couldn't: {e}", "failed"
    except Exception as e:  # noqa: BLE001 - Kitsu down: say so, keep the page
        log.warning(f"chat card {card['tool']}: {type(e).__name__}: {e}")
        reply, state = f"Couldn't ({type(e).__name__}; details in the serve log).", "failed"
    with _LOCK, _update(cfg) as chat:
        m = find_card(chat, card_id)
        if m:
            m["card"]["state"] = state
        _post(chat, "clippa", reply, kind="report" if state == "done" else "text")
        return view(chat, since)


# ---------- jobs: spend cards, in the background ----------

def start_job(cfg: dict, card: dict) -> tuple[str, bool]:
    """(what to say, whether it started): `clipper <argv>` in a thread of its own."""
    from .actions import clipper_cmd

    argv = [str(a) for a in card.get("argv") or []]
    if not argv or argv[0] in catalog.FORBIDDEN or card.get("tool") not in catalog.catalog() \
            or catalog.catalog()[card["tool"]].risk != catalog.SPEND:
        raise ClipperError("that isn't a job I can run")
    with _update(cfg) as chat:
        if any(j["state"] == "running" and j["tool"] == card["tool"] for j in chat["jobs"]):
            return f"A {card['tool']} job is running already; ask again when it's done.", False
        job = {"id": _id("j"), "tool": card["tool"], "argv": argv, "card": card["id"],
               "started_at": now_iso(), "state": "running", "host": socket.gethostname(), "boot": BOOT}
        chat["jobs"] = [*chat["jobs"][-30:], job]
    th = threading.Thread(target=_run_job, args=(cfg, job, [*clipper_cmd(cfg), *argv]),
                          name=f"chat-{job['id']}", daemon=True)
    _JOBS[job["id"]] = th
    th.start()
    return f"Started `clipper {' '.join(argv[:2])}`. I'll say here when it's done.", True


def _run_job(cfg: dict, job: dict, cmd: list[str]) -> None:
    log.info(f"chat job {job['id']}: clipper {' '.join(job['argv'])}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=JOB_TIMEOUT_S, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = [x for x in (r.stdout + r.stderr).splitlines() if x.strip() and not x.startswith("[")]
        code: int | None = r.returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        lines, code = [f"{type(e).__name__}: {e}"], None
    for line in lines[-40:]:
        log.info(f"chat job {job['id']}: {line}")
    try:
        finish(cfg, job["id"], code, lines)
    finally:
        _JOBS.pop(job["id"], None)


def finish(cfg: dict, job_id: str, code: int | None, lines: list[str]) -> None:
    """A job's report into the open topic (a new one if the last ended), or held while away."""
    tail = "\n".join(lines[-6:])[:1200]
    with _LOCK, _update(cfg) as chat:
        job = next((j for j in chat["jobs"] if j["id"] == job_id), None)
        if not job:
            return
        job.update(state="done" if code == 0 else "failed", exit=code, ended_at=now_iso())
        what = f"`clipper {' '.join(job['argv'][:2])}`"
        text = (f"{what} is done." if code == 0 else f"{what} failed (exit {code}).") + (f"\n{tail}" if tail else "")
        if chat["away"]:
            chat["held"].append({"text": text, "job": job_id})
            return
        ensure_topic(chat, cfg)
        _post(chat, "clippa", text, kind="report", job=job_id)


def lost_jobs(cfg: dict) -> int:
    """Jobs an earlier process on this machine started that a restart cut off: reported as lost.
    A job another machine's process runs is left alone. A card an earlier process left
    "working" (a restart mid-tap) is marked failed. The page's first poll calls it."""
    n = 0
    with _LOCK, _update(cfg) as chat:
        for m in chat["messages"]:
            card = m.get("card") or {}
            if card.get("state") == "working" and card.get("boot") != BOOT:
                card["state"] = "failed"
                ensure_topic(chat, cfg)
                _post(chat, "clippa", f"A restart cut off \"{card.get('text', '')}\": look at the item "
                      "before asking again.", kind="report")
                n += 1
        for j in chat["jobs"]:
            if j["state"] == "running" and j.get("host") == socket.gethostname() and j.get("boot") != BOOT:
                j.update(state="lost", ended_at=now_iso())
                ensure_topic(chat, cfg)
                _post(chat, "clippa", f"`clipper {' '.join(j['argv'][:2])}` was cut off by a restart; "
                      "its own status command says how far it got.", kind="report", job=j["id"])
                n += 1
    return n


def state(cfg: dict, since: str = "") -> dict:
    """The page's poll: the messages after `since`. A topic gone quiet ends here, so its cards
    turn grey without a message being sent."""
    if not _CHECKED["lost"]:
        _CHECKED["lost"] = True
        try:
            lost_jobs(cfg)
        except (OSError, TimeoutError) as e:
            log.warning(f"chat: lost jobs not checked ({e})")
    chat = load(cfg)
    t = current_topic(chat)
    if t and quiet(chat, t, cfg["chat"]["quiet_hours"], datetime.now(UTC)):
        with _LOCK, _update(cfg) as chat:
            t = current_topic(chat)
            if t and quiet(chat, t, cfg["chat"]["quiet_hours"], datetime.now(UTC)):
                end_topic(chat, f"{cfg['chat']['quiet_hours']:g} h quiet")
            return {**view(chat), "full": True}
    return view(chat, since)
