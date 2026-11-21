"""The blinds window (#111): gossip blind items, your guess first (`clippa blinds`).

It shows the blinds and writes only through blinds.answer / blinds.add_by_hand, like the CLI;
"Check now" runs `blinds.check` in the background, one at a time."""
from __future__ import annotations

import threading

from ..errors import ClipperError

BLINDS_JOB: dict = {"running": False, "log": [], "error": "", "result": None, "at": ""}

BLINDS_LOCK = threading.Lock()

SHOWN = 300  # cards in one view: a year of a busy source is thousands

VIEWS = {"waiting": "waiting", "answered": "answered", "no_idea": "no_idea", "not_ours": "not_ours",
         "reveal": "reveal", "unread": "unread", "all": ""}


def _days(days) -> float:
    try:
        return min(max(float(days or 14), 1), 400)
    except (TypeError, ValueError):
        return 14


def blinds_get(cfg: dict, days: str | float = "", view: str = "", source: str = "", focus: str = "") -> dict:
    """The blinds of the last `days` for one view (waiting on you first, at most SHOWN), counts
    per status, the sources, the people a guess can match, and the background check. [blinds]
    hide leaves blinds out of every view but All, and out of the counts but All's. `focus` is a
    blind to jump to (a twin, a paste): it is added first when the view, range or cap left it out."""
    from .. import blinds

    d, view = _days(days), view if view in VIEWS else "waiting"
    every = blinds.listing(cfg, d, "", source, everything=True)
    rows = [b for b in every if (view == "all" or (b["status"] == VIEWS[view] and not b["hidden"]))]
    counts = {s: 0 for s in blinds.STATUSES} | {"all": len(every)}
    for b in every:
        counts[b["status"]] += not b["hidden"]
    more, rows = max(len(rows) - SHOWN, 0), rows[:SHOWN]
    if focus and focus not in {b["id"] for b in rows}:
        try:
            rows.insert(0, blinds.one(cfg, focus))
        except ClipperError:
            pass  # gone (pruned): the window just doesn't jump
    twins = blinds.twins(cfg, {b["pair"] for b in rows if b["pair"]})  # whatever the filters left out
    for b in rows:
        b.pop("twin", None)
        b["twin_id"] = next((i for i in twins.get(b["pair"] or "", []) if i != b["id"]), "")
    try:
        srcs, src_error = blinds.sources_report(cfg), ""
    except ClipperError as e:
        srcs, src_error = [], str(e)
    return {"blinds": rows, "more": more, "counts": counts, "hidden": sum(1 for b in every if b["hidden"]),
            "view": view, "days": d, "source": source, "sources": srcs, "sources_error": src_error,
            "people": blinds.followed(cfg), "hide": cfg["blinds"]["hide"],
            "clues_model": cfg["blinds"]["clues_model"],
            "job": {k: BLINDS_JOB[k] for k in ("running", "error", "result", "at")} | {"log": BLINDS_JOB["log"][-30:]}}


def _id(body: dict) -> str:
    bid = body.get("id")
    if not isinstance(bid, str) or not bid.strip():
        raise ClipperError("blinds: id is the blind's id")
    return bid.strip()


def blinds_answer(cfg: dict, body: dict) -> dict:
    """{id, name?, why?, none?, not_ours?} -> the blind as it is now."""
    from .. import blinds

    name = str(body.get("name") or "")[:120]
    b = blinds.answer(cfg, _id(body), name, str(body.get("why") or "")[:500], bool(body.get("none")),
                      bool(body.get("not_ours")), who="war room")
    return {"blind": {**b, "tags": blinds.tags(b), "hidden": blinds.hidden(cfg, b)}}


def blinds_paste(cfg: dict, body: dict) -> dict:
    """{text}: a blind from anywhere else; its clues read now (a fraction of a cent)."""
    from .. import blinds, costs

    text = str(body.get("text") or "")
    if len(text) > blinds.TEXT_MAX:
        raise ClipperError(f"blinds: at most {blinds.TEXT_MAX} characters")
    costs.set_context("blinds guess (war room)")
    bid = blinds.add_by_hand(cfg, text, say=lambda m: None)
    return {"id": bid, "blind": blinds.one(cfg, bid)}


def blinds_check(cfg: dict, body: dict) -> dict:
    """Fetch the sources and read the new blinds' clues, in the background (one at a time)."""
    from .. import blinds, costs, news

    with BLINDS_LOCK:
        if BLINDS_JOB["running"]:
            raise ClipperError("blinds: a check is already running")
        BLINDS_JOB.update(running=True, log=[], error="", result=None, at=news.iso(news.now()))

    def say(msg: str) -> None:
        BLINDS_JOB["log"] = [*BLINDS_JOB["log"][-99:], str(msg)]

    def run() -> None:
        try:
            costs.set_context("blinds check (war room)")
            BLINDS_JOB["result"] = blinds.check(cfg, say=say)
        except ClipperError as e:
            BLINDS_JOB["error"] = str(e)
        except Exception as e:  # noqa: BLE001  # shown in the window
            BLINDS_JOB["error"] = f"{type(e).__name__}: {e}"[:300]
        BLINDS_JOB["running"] = False

    threading.Thread(target=run, name="warroom-blinds", daemon=True).start()
    return blinds_get(cfg, body.get("days") or "", str(body.get("view") or ""), str(body.get("source") or ""))
