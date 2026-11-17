"""The command catalog (#158): everything a chat window may ask clipper to do, each with a risk tag.

    read    answers at once: the schedule, items, retakes, health, costs
    write   a review decision through actions.decide (who "Clippa chat", so Kitsu stays the
            record), after a tap on a confirm card; the tap checks the item again first.
            A retitle of a video that's up renames it on YouTube too: the one YouTube write
            here, and its card says so
    spend   a `clipper` command that costs money, run in the background after a tap; the
            card shows its estimate (llm.estimate and the ledger) before anything is spent

The writes and the story spend are built from cli.py's own parsers (`clipper review <verb>`,
`clipper story new`): their help is the tool's description and their arguments its inputs,
through an allowlist (CLI_TOOLS) that names the arguments a chat may fill. The reads, the slot
and the cover redo are wrapped by hand. Never in it, by design and by test: uploads and
publishing (serve's timer does those), config and code, deploys, `clipper services`, the
recorder (FORBIDDEN).

A tool's `prepare(cfg, args)` turns a request into a Plan: an answer (a read), a card (a write
or a spend, waiting for the tap), or a question (which of these items?). `apply(cfg, card)`
does a write card's action; a spend card's argv is run by the chat (clipper/chat.py).
"""
from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import actions, costs, jsonstate, llm
from .config import for_stream
from .errors import ClipperError

READ, WRITE, SPEND = "read", "write", "spend"
RISKS = (READ, WRITE, SPEND)
WHO = "Clippa chat"

# `clipper <first word>` commands no chat may ever run or reach (tests/test_catalog.py checks).
FORBIDDEN = ("upload", "schedule", "config", "setup", "services", "record", "serve", "bot", "worker",
             "warroom", "doctor", "codereview", "cookies", "streams", "backup", "completion", "alert",
             "tiktok", "x", "rumble")
FORBIDDEN_VERBS = ("publish", "invite", "auto", "sync")  # `clipper review <verb>` a chat never runs

# Fields a card snapshots when it is made; a tap refuses when any of them changed meanwhile.
SNAPSHOT = ("decision", "hold", "publish_at", "title", "thumb_text", "uploaded")


@dataclass
class Plan:
    """What a request comes to. kind: "answer" (text), "card" (a write or spend waiting for a
    tap), "ask" (text plus options: items it could mean)."""
    kind: str
    text: str = ""
    tool: str = ""
    risk: str = READ
    args: dict = field(default_factory=dict)
    ref: str = ""                 # the item a write card acts on
    snapshot: dict = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)   # the card's details
    estimate: str = ""            # a spend card: what it will cost
    argv: list[str] = field(default_factory=list)    # a spend card: the clipper command
    options: list[dict] = field(default_factory=list)  # "ask": [{ref, title}]

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in ("", [], {})}


@dataclass(frozen=True)
class Tool:
    name: str
    risk: str
    description: str
    schema: dict
    prepare: Callable[[dict, dict], Plan]

    def spec(self) -> dict:
        """The tool as the model sees it."""
        return {"name": self.name, "description": f"[{self.risk}] {self.description}",
                "input_schema": self.schema}


# ---------- argparse -> a tool's input schema ----------

def _subparser(parser: argparse.ArgumentParser, argv: tuple[str, ...]) -> tuple[argparse.ArgumentParser, str]:
    """The parser for `clipper <argv...>` and its help line."""
    p, help_ = parser, ""
    for word in argv:
        sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        help_ = next((c.help for c in sub._choices_actions if c.dest == word), "") or ""
        p = sub.choices[word]
    return p, help_


def schema_from_parser(argv: tuple[str, ...], params: dict[str, tuple[str, bool, str]]) -> tuple[dict, str]:
    """(input schema, description) for `clipper <argv>`, its arguments limited to `params`:
    {argparse dest: (input name, required, a description in place of the help, or "")}."""
    from .cli import build_parser

    p, help_ = _subparser(build_parser(), argv)
    props, required = {}, []
    for a in p._actions:
        if a.dest not in params:
            continue
        name, req, desc = params[a.dest]
        if isinstance(a, argparse._StoreTrueAction):
            prop: dict = {"type": "boolean"}
        elif isinstance(a, argparse._AppendAction) or a.nargs == "+":
            prop = {"type": "array", "items": {"type": "string"}}
        elif a.type is int:
            prop = {"type": "integer"}
        elif a.type is float:
            prop = {"type": "number"}
        else:
            prop = {"type": "string"}
        if a.choices:
            prop["enum"] = list(a.choices)
        text = desc or (a.help if a.help != argparse.SUPPRESS else "") or ""
        if text:
            prop["description"] = text.replace("%%", "%")
        props[name] = prop
        if req:
            required.append(name)
    missing = set(n for n, *_ in params.values()) - set(props)
    if missing:  # a renamed CLI argument must not silently drop a tool's input
        raise ClipperError(f"clipper {' '.join(argv)} has no arguments for {sorted(missing)}")
    return {"type": "object", "properties": props, "required": required}, help_


# ---------- items ----------

ITEM = {"type": "string", "description": "the item: its name (kick-131151926/s02, s02, c03) or "
                                         "words from its title, with the stream if you know it"}


def _rows(cfg: dict, stream: str | None = None) -> list[dict]:
    """Every published item's row (the war room's cache), or one stream's alone: a replan
    invalidates that cache, and rebuilding it reads every stream over the share."""
    if stream:
        return list(actions.describe(cfg, cfg["paths"]["streams"] / stream, links=False).values())
    from .warroom.data import all_rows

    return all_rows(cfg)


WORD = re.compile(r"[a-z0-9]+")
STOP = {"the", "a", "an", "of", "from", "on", "in", "to", "and", "for", "at", "with", "one",
        "segment", "short", "clip", "item", "video", "stream", "about", "that", "this"}


def match_rows(rows: list[dict], text: str, limit: int = 6) -> list[dict]:
    """Rows whose name, title, topic or hook hold the most of the text's words; only the best
    score, at least half the words."""
    words = [w for w in WORD.findall(text.lower()) if w not in STOP]
    if not words:
        return []
    scored = []
    for r in rows:
        hay = " ".join(str(r.get(k) or "") for k in ("ref", "title", "topic", "hook", "stream")).lower()
        hay_words = set(WORD.findall(hay))
        score = sum(1 for w in words if w in hay_words or (len(w) > 3 and w in hay))
        if score * 2 >= len(words):
            scored.append((score, r))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [r for s, r in scored if s == best][:limit]


REF_LIKE = re.compile(r"(?:[\w.-]+/)?(?:[a-z]{1,4}\d+[\w.-]*|comp-[\w.-]+)", re.IGNORECASE)
STREAM_LIKE = re.compile(r"[a-z]+-[\w-]+", re.IGNORECASE)   # kick-131151926, yt-<id>, story-<slug>


def find_item(cfg: dict, text: str) -> tuple[actions.Item | None, list[dict]]:
    """(the item, []) when the text names one; (None, rows it could mean) otherwise, [] for none.
    An item name in it first (s02, c03_t01, kick-131151926/s02, with a stream id beside it or
    not; actions.resolve), else the words of titles, within the stream it names if any."""
    words = [w.strip(".,;:!?()'\"") for w in (text or "").split()]
    streams = cfg["paths"]["streams"]
    stream = next((w for w in words if STREAM_LIKE.fullmatch(w) and not REF_LIKE.fullmatch(w)
                   and (streams / w / "review_state.json").exists()), None)
    ref = next((w for w in words if w != stream and REF_LIKE.fullmatch(w) and ".." not in w), "")
    if ref:
        try:
            return actions.resolve(cfg, ref, None if "/" in ref else stream), []
        except ClipperError as e:
            if "matches" in str(e):
                key = ref.split("/")[-1]
                within = ref.split("/")[0] if "/" in ref else stream
                return None, [r for r in _rows(cfg, within) if r["key"].startswith(key)][:6]
            if len(words) == 1:
                return None, []
    found = match_rows(_rows(cfg, stream), " ".join(w for w in words if w != stream))
    if len(found) == 1:
        return actions.resolve(cfg, found[0]["ref"]), []
    return None, found


def _which(text: str, rows: list[dict], verb: str) -> Plan:
    if not rows:
        return Plan("answer", text=f"I can't find an item that matches {text!r}.")
    return Plan("ask", text=f"Which one do you want to {verb}?",
                options=[{"ref": r["ref"], "title": r["title"]} for r in rows])


def _row(cfg: dict, item: actions.Item) -> dict:
    return actions.describe(cfg, item.stream_dir, links=False)[item.key]


def snapshot(row: dict) -> dict:
    return {k: row.get(k) for k in SNAPSHOT}


def item_line(row: dict) -> str:
    return f"{row['short']} ({row['kind']}): {row['title']}"


def when_text(iso: str, tz: ZoneInfo) -> str:
    try:
        return f"{datetime.fromisoformat(iso).astimezone(tz):%a %m-%d %H:%M}"
    except (TypeError, ValueError):
        return iso


# ---------- writes: `clipper review <verb>`, through actions.decide ----------

# tool name -> (review verb, {argparse dest: (input, required, description)}, what decide gets)
CLI_WRITES: dict[str, tuple[str, dict]] = {
    "approve": ("approve", {"text": ("note", False, "an optional comment for Kitsu")}),
    "reject": ("reject", {"text": ("reason", True, "")}),
    "retake": ("changes", {"text": ("note", True, "what to change; the retake planner acts on it")}),
    "reopen": ("reopen", {}),
    "hold": ("hold", {"off": ("release", False, "true: release the hold instead")}),
    "pin": ("pin", {"text": ("when", False, "YYYY-MM-DD HH:MM in the schedule's time zone, or "
                                           "YYYY-MM-DD for that day's first slot"),
                    "clear": ("unpin", False, "true: take the pin off instead (when can be empty)")}),
    "retitle": ("retitle", {"text": ("title", True, "")}),
    "cover_text": ("thumbtext", {"text": ("text", True, "")}),
}

VERB_WORD = {"approve": "approve", "reject": "reject", "changes": "send back for a retake",
             "reopen": "reopen", "hold": "hold", "pin": "pin", "retitle": "retitle",
             "thumbtext": "change the cover words of"}


class _No(Exception):
    """A request that comes to an answer, not a card (already approved, a time that passed)."""


def _approve(cfg, row, args, tz, lines):
    if row["decision"] == "approved":
        raise _No(f"{row['short']} is already approved.")
    return f"Approve {row['short']}?", (args.get("note") or "").strip(), None


def _reject(cfg, row, args, tz, lines, verb="reject"):
    text = (args.get("reason") or args.get("note") or "").strip()
    if not text:
        raise _No("Say why: " + ("the reason becomes a scoring example." if verb == "reject"
                                  else "what should change?"))
    lines.append(f"note: {text}")
    what = f"Reject {row['short']}?" if verb == "reject" else f"Send {row['short']} back for a retake?"
    return what, text, None


def _changes(cfg, row, args, tz, lines):
    return _reject(cfg, row, args, tz, lines, verb="changes")


def _reopen(cfg, row, args, tz, lines):
    return f"Reopen {row['short']} for review?", "", None


def _hold(cfg, row, args, tz, lines):
    on = not args.get("release")
    if row["hold"] == on:
        raise _No(f"{row['short']} is {'already held' if on else 'not held'}.")
    return f"{'Hold' if on else 'Release'} {row['short']}?", "", on


def _pin(cfg, row, args, tz, lines):
    from .schedule import parse_when

    if args.get("unpin"):
        if not row["publish_at"]:
            raise _No(f"{row['short']} isn't pinned.")
        return f"Unpin {row['short']} (pinned {row['publish_at']})?", "", None
    text = " ".join(str(args.get("when") or "").split())
    if not text:
        raise _No("Pin it when? (YYYY-MM-DD HH:MM)")
    at = parse_when(text, tz, "00:00")
    if not at:
        raise _No(f"I can't read {text!r} as a time: try YYYY-MM-DD HH:MM.")
    if at.date() < datetime.now(tz).date() or (":" in text and at < datetime.now(tz)):
        raise _No(f"{at:%a %m-%d %H:%M} has passed.")
    if row["decision"] != "approved":
        lines.append("not approved yet: the pin waits for the approval")
    return f"Pin {row['short']} for {at:%a %m-%d}" + (f" {at:%H:%M}?" if ":" in text else "'s first slot?"), text, None


def _retitle(cfg, row, args, tz, lines):
    from . import youtube

    text = actions.check_title(args.get("title") or "")
    if text == row["title"]:
        raise _No(f"{row['short']} is called that already.")
    lines += [f"now: {row['title']}", f"new: {text}"]
    lines += [f"⚠ {i['msg']}" for i in actions.style_issues(cfg, {"title": text})]
    live = youtube.on_youtube(cfg, row["ref"]) if row["uploaded"] else None
    if not live:
        return f"Retitle {row['short']}?", text, None
    if live.get("gone_at"):
        raise _No(f"{row['short']} was deleted from YouTube ({live.get('url')}).")
    if youtube.spare(cfg) < youtube.RETITLE_UNITS:
        raise _No("No spare YouTube quota left today (it's kept for uploads): ask again tomorrow.")
    shown = youtube.live_title(cfg, text)
    lines += [*([f"YouTube shows: {shown}"] if shown != text else []),
              f"it's up: {live.get('url')}. The tap renames it on YouTube, for everyone"]
    return f"Rename {row['short']} on YouTube?", text, True


def _thumbtext(cfg, row, args, tz, lines):
    from .thumbs import check_text

    text = check_text(cfg, " ".join((args.get("text") or "").split()))
    return f"Change {row['short']}'s cover words to {text!r}?", text, None


ASK = {"approve": _approve, "reject": _reject, "changes": _changes, "reopen": _reopen, "hold": _hold,
       "pin": _pin, "retitle": _retitle, "thumbtext": _thumbtext}
ON_YOUTUBE = ("pin", "reopen", "reject", "changes", "hold")   # pointless once it's up


def _write_prepare(name: str, verb: str) -> Callable[[dict, dict], Plan]:
    def prepare(cfg: dict, args: dict) -> Plan:
        item, rows = find_item(cfg, args.get("item", ""))
        if not item:
            return _which(args.get("item", ""), rows, VERB_WORD[verb])
        row = _row(cfg, item)
        scfg = for_stream(cfg, item.stream_dir)
        tz = ZoneInfo(scfg["schedule"]["timezone"])
        if row["uploaded"] and verb in ON_YOUTUBE:
            return Plan("answer", text=f"{row['short']} is already on YouTube: change it in Studio.")
        lines = [item_line(row)]
        try:
            what, text, on = ASK[verb](scfg, row, args, tz, lines)
        except _No as e:
            return Plan("answer", text=str(e))
        if row["scheduled"]:
            lines.append(f"scheduled {when_text(row['scheduled'], tz)}")
        args = {"verb": verb, "text": text, "on": on}
        if verb == "retitle":   # its "on": the video is up, and the tap renames it on YouTube
            args.update(on=None, youtube=bool(on))
        return Plan("card", text=what, tool=name, risk=WRITE, ref=item.ref, snapshot=snapshot(row),
                    lines=lines, args=args)
    return prepare


def recheck(cfg: dict, card: dict) -> tuple[actions.Item, dict, str]:
    """(item, its row as Kitsu has it now, "" or why the card is stale): a tap reads the item
    again before acting (it may have been approved from Discord meanwhile)."""
    item = actions.resolve(cfg, card["ref"])
    row = actions.refresh(cfg, item)   # one Kitsu read of this item
    now = snapshot(row)
    changed = [k for k in SNAPSHOT if k in card.get("snapshot", {}) and card["snapshot"][k] != now[k]]
    if not changed:
        return item, row, ""
    what = {"decision": f"it is {row['decision'].replace('_', ' ')} now",
            "hold": "its hold changed", "publish_at": "its pin changed", "title": "its title changed",
            "thumb_text": "its cover words changed", "uploaded": "it went up to YouTube"}
    return item, row, f"{row['short']} changed since I asked ({', '.join(what[k] for k in changed)}): nothing done."


def apply(cfg: dict, card: dict) -> tuple[str, bool]:
    """Do a write card's action (re-checked first); its outcome in a line."""
    from .warroom.review import _before, remember, start_retake
    from .warroom.state import LOCK

    if card["tool"] == "slot":
        return _slot_apply(cfg, card)
    a = card["args"]
    with LOCK:
        item, _, stale = recheck(cfg, card)
        if stale:
            return stale, False
        before = _before(cfg, item)
        row = actions.decide(cfg, item, a["verb"], WHO, text=a.get("text") or "", on=a.get("on"))
        row, note = actions.replan(cfg, item, a["verb"], row)
    # a retitle is the war room's "edit" of the title column, so its undo puts the old title back;
    # one on YouTube undoes as a retitle, which renames it back there too
    action, values = ((("retitle" if a.get("youtube") else "edit"), {"YouTube title": a["text"]})
                      if a["verb"] == "retitle" else (a["verb"], {}))
    remember(cfg, action, f"{a['verb']} {item.short} (chat)", [{"ref": item.ref, "before": before, "values": values}])
    if a["verb"] == "changes" and cfg["retake"]["auto"]:
        note = "the retake is on its way" if start_retake(cfg) else "a retake run is going; this one is next"
    done = {"approve": "Approved", "reject": "Rejected", "changes": "Sent back for a retake",
            "reopen": "Reopened", "hold": "Held" if a.get("on") else "Released",
            "pin": f"Pinned for {a.get('text')}" if a.get("text") else "Unpinned",
            "retitle": "Renamed on YouTube" if a.get("youtube") else "Retitled",
            "thumbtext": "Cover words changed"}[a["verb"]]
    return f"{done}: {item_line(row)}" + (f"; {note}" if note else "") + ".", True


# ---------- the next open slot (the war room's Slot, one item) ----------

def _slot_prepare(cfg: dict, args: dict) -> Plan:
    from .warroom.review import slot_plan, slot_print

    item, rows = find_item(cfg, args.get("item", ""))
    if not item:
        return _which(args.get("item", ""), rows, "slot")
    out = slot_plan(cfg, [item.ref])
    if not out["plan"]:
        why = out["refused"][0]["why"] if out["refused"] else "no open slot"
        return Plan("answer", text=f"{item.short} can't be slotted: {why}.")
    p = out["plan"][0]
    lines = [f"{p['title']}"] + [f"bumps {b['ref'].split('/')[-1]} ({b['title'][:50]})" for b in out["bumped"]]
    if p["let_go"]:
        lines.append(f"the scheduler had let it go: {p['let_go']}")
    return Plan("card", text=f"Pin {item.short} to the next open {p['kind']} slot, {p['text']}?",
                tool="slot", risk=WRITE, ref=item.ref, snapshot=snapshot(_row(cfg, item)), lines=lines,
                args={"verb": "pin", "text": p["text"], "print": slot_print(out)})


def _slot_apply(cfg: dict, card: dict) -> tuple[str, bool]:
    from .warroom.review import do_slot

    item, _, stale = recheck(cfg, card)
    if stale:
        return stale, False
    out = do_slot(cfg, {"refs": [item.ref], "print": card["args"]["print"]}, who=WHO, tag="(chat)")
    if out.get("changed"):
        p = out["plan"][0]["text"] if out["plan"] else "none"
        return f"The schedule moved since I asked (the next slot is {p} now): nothing done. Ask again.", False
    if out["failed"]:
        raise ClipperError(f"couldn't pin {item.short}: {out['failed'][0]['error']}")
    return f"Pinned {item.short} for {card['args']['text']}.", True


# ---------- spends: `clipper <command>` in the background ----------

def _story_estimate(cfg: dict) -> str:
    """What a story has cost before (the ledger, model spend per story-* stream), and the caps."""
    per: dict[str, float] = {}
    for r in costs.read(cfg["paths"]["costs"]) if cfg["paths"].get("costs") else []:
        if str(r.get("stream", "")).startswith("story-") and isinstance(r.get("usd"), int | float):
            per[r["stream"]] = per.get(r["stream"], 0.0) + r["usd"]
    caps = cfg["story"]["budget_usd"] + cfg["swarm"]["budget_usd"]
    seen = sorted(per.values())[-10:]
    typical = (f"~${sum(seen) / len(seen):.2f} in models (the last {len(seen)} stories)" if seen
               else "models ~$0.20-0.60")
    return f"{typical}, at most ${caps:.2f} ([story] and [swarm] budgets), plus Modal for the transcripts"


def _strings(v) -> list[str]:
    """A list input as the model may send it: a list, one string, or nothing."""
    items = v if isinstance(v, list) else [v] if v else []
    return [t for t in (" ".join(str(x).split()) for x in items) if t]


def _story_prepare(cfg: dict, args: dict) -> Plan:
    brief = " ".join(str(args.get("brief") or "").split())
    if len(brief) < 8:
        return Plan("answer", text="What's the story? A sentence: who, what happened.")
    argv, lines = ["story", "new"], [f"brief: {brief}"]   # --opt=value and "--": no value reads as an option
    if args.get("days"):
        try:
            days = int(args["days"])
        except (TypeError, ValueError):
            return Plan("answer", text=f"How many days back? ({args['days']!r} isn't a number.)")
        argv.append(f"--days={days}")
        lines.append(f"the last {days} days")
    for p in _strings(args.get("people")):
        argv.append(f"--person={p}")
        lines.append(f"person: {p}")
    for u in _strings(args.get("urls")):
        if not re.match(r"https?://", u):
            return Plan("answer", text=f"{u!r} isn't a link.")
        argv.append(f"--url={u}")
        lines.append(f"video: {u}")
    argv += ["--", brief]
    lines.append("then `story plan` and `story cut --no-review` are yours (it never goes to review)")
    return Plan("card", text="Start a story?", tool="story", risk=SPEND, lines=lines,
                estimate=_story_estimate(cfg), argv=argv, args={"brief": brief})


def _cover_prepare(cfg: dict, args: dict) -> Plan:
    from .meta import load_assets
    from .thumbs.stage import FRAME_TOKENS, cover_prompts

    item, rows = find_item(cfg, args.get("item", ""))
    if not item:
        return _which(args.get("item", ""), rows, "make a new cover for")
    scfg = for_stream(cfg, item.stream_dir)
    file = item.state["file"].replace("\\", "/")
    asset = next((a for a in load_assets(item.stream_dir)
                  if a["video"].relative_to(item.stream_dir).as_posix() == file), None)
    if not asset:
        return Plan("answer", text=f"{item.short} has no cut video to pick a cover from.")
    thumbnail = scfg["thumbnail"]
    model = thumbnail["model"]
    if not model:
        return Plan("answer", text="[thumbnail] model is off: no cover picks.")
    _, system = cover_prompts(scfg, thumbnail, thumbnail["style"])
    frames = (thumbnail["clip_frames"] if asset["kind"] == "clip"
              else thumbnail["even_frames"] + thumbnail["heat_frames"])
    if thumbnail["style"] == "enquirer":   # thumbs adds face frames of the people seen most
        frames += thumbnail["face_people"] * thumbnail["face_frames"]
    one = llm.estimate(scfg, model, llm.tokens(system) + 300 + frames * (FRAME_TOKENS + 20), 200)
    check = llm.estimate(scfg, model, 20, 16)   # thumbs' key check before the pick
    row = _row(cfg, item)
    lines = [item_line(row), f"up to {frames} frames to {model}"]
    if row["uploaded"]:
        lines.append("already on YouTube: the new cover goes to Kitsu, Studio keeps the old one")
    return Plan("card", text=f"Pick a new cover for {item.short}?", tool="cover_redo", risk=SPEND,
                ref=item.ref, snapshot=snapshot(row), lines=lines,
                estimate=(f"~${one + check:.3f} for one {model} pick; up to ~${2 * one + check:.3f} "
                          "if the first is unusable and it asks once more"),
                args={"file": file, "asset": asset["id"]},
                argv=["thumbs", item.stream_dir.name, "--items", asset["id"], "--force"])


def precheck(cfg: dict, card: dict) -> str:
    """Before a spend card's job starts: "" to go ahead, or why not. A card about an item reads
    it again (decision, texts, upload) and checks its cut is the one the card was made for; a
    story has no item."""
    if not card.get("ref"):
        return ""
    item, _, stale = recheck(cfg, card)
    if stale:
        return stale
    want = (card.get("args") or {}).get("file")
    if want and item.state.get("file", "").replace("\\", "/") != want:
        return f"{item.short} was cut again since I asked: nothing spent. Ask again."
    return ""


# ---------- reads ----------

def _tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg["schedule"]["timezone"])


def day_of(text: str, today: date) -> tuple[date, int] | None:
    """(first day, how many days) for "today", "tomorrow", "week", a weekday or YYYY-MM-DD."""
    t = (text or "today").strip().lower()
    if t in ("", "today", "now"):
        return today, 1
    if t == "tomorrow":
        return today + timedelta(days=1), 1
    if t in ("week", "this week", "next 7 days"):
        return today, 7
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, d in enumerate(days):
        if t in (d, d[:3]):
            return today + timedelta(days=(i - today.weekday()) % 7), 1
    try:
        return date.fromisoformat(t), 1
    except ValueError:
        return None


def _schedule(cfg: dict, args: dict) -> Plan:
    tz = _tz(cfg)
    now = datetime.now(tz)
    span = day_of(str(args.get("day") or "today"), now.date())
    if not span:
        return Plan("answer", text="Which day? today, tomorrow, a weekday, week or YYYY-MM-DD.")
    first, n = span
    entries = (jsonstate.read_or(cfg["paths"]["schedule"], {}) or {}).get("entries", [])
    rows = sorted((e for e in entries
                   if first <= datetime.fromisoformat(e["publish_at"]).astimezone(tz).date() < first + timedelta(days=n)),
                  key=lambda e: e["publish_at"])
    label = ("today" if (first, n) == (now.date(), 1) else "tomorrow" if n == 1 and first == now.date() + timedelta(days=1)
             else f"{first:%a %m-%d}" if n == 1 else f"{first:%m-%d} to {first + timedelta(days=n - 1):%m-%d}")
    if not rows:
        return Plan("answer", text=f"Nothing goes out {label}.")
    out = []
    for e in rows:
        t = datetime.fromisoformat(e["publish_at"]).astimezone(tz)
        state = " ✓ up" if e.get("status") == "uploaded" else " (pinned)" if e.get("source") == "pinned" else ""
        out.append(f"{t:%a %H:%M} {e['kind']}: {e['title'][:80]}{state}")
    return Plan("answer", text=f"{len(rows)} going out {label}:\n" + "\n".join(out))


def _unslotted(cfg: dict, args: dict) -> Plan:
    data = jsonstate.read_or(cfg["paths"]["schedule"], {}) or {}
    rows = [u for u in data.get("unscheduled", []) if u["reason"] not in ("rejected", "not approved", "pending")]
    if not rows:
        return Plan("answer", text="Every approved item has a slot.")
    by: dict[str, list[str]] = {}
    for u in rows:
        by.setdefault(u["reason"].split(":")[0], []).append(u["key"])
    lines = [f"{reason}: {len(keys)} ({', '.join(k.split('/')[-1].split('_')[0] for k in keys[:6])}"
             + (", ..." if len(keys) > 6 else "") + ")" for reason, keys in sorted(by.items(), key=lambda x: -len(x[1]))]
    return Plan("answer", text=f"{len(rows)} approved items without a slot:\n" + "\n".join(lines))


def _items(cfg: dict, args: dict) -> Plan:
    query = " ".join(str(args.get("query") or "").split())
    if query:
        rows = match_rows(_rows(cfg), query, limit=8)
        if not rows:
            return Plan("answer", text=f"No item matches {query!r}.")
        return Plan("answer", text="\n".join(f"{item_line(r)} [{actions.decision_text(r)}]" for r in rows))
    s = actions.status(cfg, links=False)
    rows = [r for r in s["streams"] if r["open"] or r["held"]]
    if not rows:
        return Plan("answer", text="Nothing is waiting on you.")
    lines = []
    for r in rows:
        c = r["counts"]
        parts = [f"{c[k]} {k.replace('_', ' ')}" for k in actions.OPEN if c.get(k)]
        if r["held"]:
            parts.append(f"{r['held']} held")
        lines.append(f"{r['stream']} ({r['date']}): {', '.join(parts)}")
    return Plan("answer", text="Waiting on you:\n" + "\n".join(lines))


def _item(cfg: dict, args: dict) -> Plan:
    item, rows = find_item(cfg, args.get("item", ""))
    if not item:
        return _which(args.get("item", ""), rows, "see")
    r = _row(cfg, item)
    tz = _tz(cfg)
    lines = [item_line(r), f"{actions.decision_text(r)}"]
    if r["scheduled"]:
        lines.append(f"{'uploaded' if r['uploaded'] else 'scheduled'} {when_text(r['scheduled'], tz)}")
    elif r.get("dropped"):
        lines.append(f"not going out: {r['dropped']}")
    if r["comment"]:
        lines.append(f"note: {r['comment'][:200]}")
    if r["checks"]:
        lines.append("checks: " + "; ".join(r["checks"][:3]))
    if r["kitsu_url"]:
        lines.append(r["kitsu_url"])
    return Plan("answer", text="\n".join(lines))


def _retakes(cfg: dict, args: dict) -> Plan:
    from .retake import pending

    running = (cfg["paths"]["streams"] / ".retake.lock").exists()
    rows = pending(cfg)
    head = "A retake run is going. " if running else ""
    if not rows:
        return Plan("answer", text=head + "No retake notes waiting.")
    lines = [f"{d.name}/{k.split('_')[0]}: {r.get('comment', '')[:100]}" for d, k, r in rows[:10]]
    return Plan("answer", text=head + f"{len(rows)} retake notes waiting:\n" + "\n".join(lines))


def _health(cfg: dict, args: dict) -> Plan:
    return Plan("answer", text="\n".join(f"{b['name']}: {b['state']}" + (f", {b['note']}" if b["note"] else "")
                                         for b in buddies(cfg)))


def _costs(cfg: dict, args: dict) -> Plan:
    period = "month" if str(args.get("period") or "").lower().startswith("m") else "today"
    if period == "month":
        rep = costs.report(cfg)
        top = ", ".join(f"{k} ${v:.2f}" for k, v in list(rep["by_stage"].items())[:5])
        return Plan("answer", text=f"{rep['month']}: ${rep['net_usd']:.2f} so far" + (f" ({top})" if top else ""))
    by = spend_today(cfg)
    if not by:
        return Plan("answer", text="Nothing spent today (UTC).")
    total = sum(by.values())
    top = ", ".join(f"{k} ${v:.2f}" for k, v in sorted(by.items(), key=lambda x: -x[1])[:6])
    return Plan("answer", text=f"Today (UTC): ${total:.2f} ({top})")


def spend_today(cfg: dict, stage: str | None = None) -> dict[str, float]:
    """Today's (UTC) spend by stage from the ledgers; stage: only that one."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    out: dict[str, float] = {}
    for r in costs.entries(cfg):
        if str(r.get("ts", "")).startswith(today) and (stage is None or r.get("stage") == stage):
            k = r.get("stage") or "other"
            out[k] = out.get(k, 0.0) + float(r.get("usd") or 0)
    return out


def buddies(cfg: dict) -> list[dict]:
    """The buddy list: serve, the bot, the worker and the recorder from their heartbeats.
    state: online, idle, away (the recorder while a show is live), offline, or unknown."""
    from . import daemon, services, worker

    out = []
    for name, label in (("serve", "serve"), ("bot", "bot")):
        hb = daemon.heartbeat(cfg, name)
        jobs = sorted(j for j, st in (hb["data"].get("jobs") or {}).items() if st.get("running"))
        if hb.get("error"):
            out.append({"name": label, "state": "unknown", "note": "heartbeat unreadable"})
        elif not hb["up"]:
            note = "no heartbeat" if hb["age_s"] is None else f"last seen {hb['age_s'] / 60:.0f} min ago"
            out.append({"name": label, "state": "offline", "note": note})
        else:
            out.append({"name": label, "state": "online" if jobs else "idle",
                        "note": f"running {', '.join(jobs)}" if jobs else ""})
    busy = [r for r in worker.stream_rows(cfg, 1) if r["run"] == "running"]
    out.append({"name": "worker", "state": "online" if busy else "idle",
                "note": f"on {', '.join(r['stream'] for r in busy[:2])}" if busy else ""})
    rec = services.recorder(cfg)
    if rec.get("error"):
        out.append({"name": "recorder", "state": "unknown", "note": "heartbeat unreadable"})
    elif not rec["up"]:
        out.append({"name": "recorder", "state": "offline",
                    "note": "no heartbeat" if rec["age_s"] is None else f"last seen {rec['age_s'] / 60:.0f} min ago"})
    elif rec["live"]:
        out.append({"name": "recorder", "state": "away", "note": f"{', '.join(rec['live'])} is live"})
    else:
        out.append({"name": "recorder", "state": "idle", "note": "nothing live"})
    return out


# ---------- the catalog ----------

def _obj(props: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": props, "required": list(required)}


def _write_tool(name: str) -> Tool:
    verb, params = CLI_WRITES[name]
    schema, help_ = schema_from_parser(("review", verb), {"item": ("item", True, ITEM["description"]), **params})
    return Tool(name, WRITE, help_, schema, _write_prepare(name, verb))


def build() -> dict[str, Tool]:
    story_schema, story_help = schema_from_parser(("story", "new"), {
        "brief": ("brief", True, ""), "days": ("days", False, ""),
        "person": ("people", False, "people the brief doesn't name"),
        "url": ("urls", False, "videos to transcribe whatever triage says")})
    tools = [
        Tool("releases", READ, "what goes out (or went out) on a day, from schedule.json",
             _obj({"day": {"type": "string", "description": "today, tomorrow, a weekday, week, or YYYY-MM-DD"}}),
             _schedule),
        Tool("unslotted", READ, "approved items without a release slot, and why", _obj({}), _unslotted),
        Tool("items", READ, "what is waiting on a decision; with query, items whose title or name match",
             _obj({"query": {"type": "string"}}), _items),
        Tool("item", READ, "one item: its decision, slot, notes, checks and Kitsu link",
             _obj({"item": ITEM}, ("item",)), _item),
        Tool("retakes", READ, "retake notes not acted on yet, and whether a retake run is going", _obj({}), _retakes),
        Tool("health", READ, "serve, the bot, the worker and the recorder, from their heartbeats", _obj({}), _health),
        Tool("costs", READ, "what was spent today (UTC) or this month, by stage",
             _obj({"period": {"type": "string", "enum": ["today", "month"]}}), _costs),
        *(_write_tool(n) for n in CLI_WRITES),
        Tool("slot", WRITE, "pin an approved item to the next open release slot of its kind",
             _obj({"item": ITEM}, ("item",)), _slot_prepare),
        Tool("story", SPEND, story_help, story_schema, _story_prepare),
        Tool("cover_redo", SPEND, "pick a new cover for an item (a vision call, maybe a second), attached in Kitsu",
             _obj({"item": ITEM}, ("item",)), _cover_prepare),
    ]
    return {t.name: t for t in tools}


_CATALOG: dict[str, Tool] | None = None


def catalog() -> dict[str, Tool]:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = build()
    return _CATALOG


def prepare(cfg: dict, name: str, args: dict) -> Plan:
    """The one way into a tool: an unknown name is refused, never guessed at."""
    tool = catalog().get(name)
    if tool is None:
        raise ClipperError(f"{name!r} isn't something I can do from here")
    if not isinstance(args, dict):
        raise ClipperError("bad arguments")
    plan = tool.prepare(cfg, args)
    if plan.kind == "card" and tool.risk == READ:   # a read never waits for a tap, nor acts
        raise ClipperError(f"{name} is a read")
    plan.tool, plan.risk = plan.tool or name, tool.risk if plan.kind == "card" else plan.risk
    return plan
