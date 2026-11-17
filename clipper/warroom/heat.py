"""The heat window: one stream's heat curve (`clipper signals`), the windows picked from it and
where each cut item sits in the stream, in its review state."""
from __future__ import annotations

import math
from pathlib import Path

from .. import jsonstate, review
from ..config import for_stream
from .data import _cached, _mtime, _scan, stream_rows
from .faces import stream_path

POINTS = 1200   # the most points the page draws

WINDOW_KEYS = ("start_s", "end_s", "peak_s", "heat", "rank", "chat_z", "react_z", "clip_z", "loud_z")

SPAN_KINDS = {"segments", "picks", "shorts"}   # a compilation is its segments, already drawn


def buckets(heat, points: int = POINTS) -> tuple[int, list[float | None]]:
    """(seconds a bucket, each bucket's hottest second): a per-second curve cut down to at most
    `points` values. The hottest second, not the mean, so a peak a few seconds wide is still
    there; None for a bucket with no heat at all (no chat was recorded for those seconds)."""
    import numpy as np

    h = np.asarray(heat, dtype=float)
    if not h.size:
        return 1, []
    step = max(1, math.ceil(h.size / points))
    h = np.concatenate([h, np.full(-h.size % step, np.nan)]).reshape(-1, step)
    top = np.where(np.isfinite(h), h, -np.inf).max(axis=1)
    return step, [None if v == -np.inf else round(float(v), 2) for v in top]


def spans(cfg: dict, sdir: Path) -> list[dict]:
    """Where each cut item sits in the stream, by its review key: a segment from where its cut
    starts, a Short or clip from its in to its out point."""
    include = [k for k in for_stream(cfg, sdir)["review"]["include"] if k in SPAN_KINDS]
    try:
        items = review.items(sdir, include, cfg)
    except FileNotFoundError:   # no segments.json: nothing is cut yet
        return []
    out = []
    for it in items:
        d = it["meta"]
        start, end = ((d.get("cut_start_s", d.get("start_s")), d.get("end_s")) if it["kind"] == "segment"
                      else (d.get("in_s"), d.get("out_s")))
        if start is None or end is None:   # an old sidecar without its place in the stream: not drawn
            continue
        out.append({"key": it["key"], "kind": it["kind"], "title": it["title"],
                    "start_s": round(float(start), 1), "end_s": round(float(end), 1)})
    return sorted(out, key=lambda s: s["start_s"])


def state_of(row: dict | None) -> str:
    """One word for where an item is, for its colour: cut (not in review yet), waiting, changes,
    approved, scheduled, uploaded or rejected."""
    if not row:
        return "cut"
    if row.get("uploaded"):
        return "uploaded"
    if row.get("decision") == "rejected":
        return "rejected"
    if row.get("scheduled"):
        return "scheduled"
    return {"approved": "approved", "changes_requested": "changes"}.get(row.get("decision"), "waiting")


def curve(cfg: dict, sdir: Path) -> dict:
    """The parts that only change when the stream's files do: the curve, the windows, the spans."""
    meta = jsonstate.read_or(sdir / "meta.json", {})
    out = {"stream": sdir.name, "title": meta.get("title", ""), "started_at": meta.get("started_at", ""),
           "duration_s": meta.get("duration_s") or 0, "bucket_s": 1, "heat": [], "windows": [],
           "min_heat": None, "chat_coverage": None, "items": [], "error": ""}
    signals = sdir / "signals.parquet"
    if not signals.exists():
        return out
    try:
        import pyarrow.parquet as pq

        heat = pq.read_table(signals, columns=["heat"])["heat"].to_numpy(zero_copy_only=False)
    except Exception as e:  # noqa: BLE001 - a file half written or from an older stage: say so, draw nothing
        return {**out, "error": f"signals.parquet can't be read ({type(e).__name__}): run clipper signals again"}
    out["bucket_s"], out["heat"] = buckets(heat)
    out["duration_s"] = len(heat)   # the curve's own length: what the page's time axis must cover
    win = jsonstate.read_or(sdir / "windows.json", {}) or {}
    out["min_heat"] = (win.get("params") or {}).get("min_heat")
    out["chat_coverage"] = win.get("chat_coverage")
    out["windows"] = [{**{k: w.get(k) for k in WINDOW_KEYS}, "said": (w.get("transcript_peak") or "")[:160]}
                      for w in win.get("windows") or []]
    out["items"] = spans(cfg, sdir)
    return out


def heat_get(cfg: dict, stream: str) -> dict:
    """A stream's heat curve, its windows, and its cut items with their review state. A stream
    with no signals yet comes back with an empty curve."""
    sdir = stream_path(cfg, stream)
    names, sig = _scan(sdir)
    out = _cached(sdir, "heat", sig, lambda: curve(cfg, sdir))
    rows = {}
    if "review_state.json" in names:   # the same cached rows the queue is built from
        sched = _mtime(cfg["paths"]["schedule"])
        rows = {r["key"]: r for r in _cached(sdir, "rows", sig + (sched,), lambda: stream_rows(cfg, sdir))}
    items = []
    for s in out["items"]:
        r = rows.get(s["key"])
        items.append({**s, "state": state_of(r), "hold": bool(r and r.get("hold")),
                      # approved but left out of the schedule (over its stream's budget): the reason
                      "dropped": (r or {}).get("dropped") or "",
                      "ref": r["ref"] if r else "", "title": r["title"] if r else s["title"]})
    return {**out, "items": items}
