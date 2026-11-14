"""What predicts views (#258 A): each ranker clipper has for an upload, checked against how
it did in its first 7 days. No model call.

Usage:
    clipper analytics --lift [--show KEY] [--kind segment] [--json]

The rankers, one row each per show and kind where there are values:
    score   the Kitsu Score column (Pass B's score on clips and Shorts; segments get one in #258 B)
    heat    mean chat heat over a segment's span (signals.parquet), the mean of its segments'
            for a compilation: what `clipper schedule` ranks segments by today
    topic   the median 7-day views of the show's earlier uploads with the same topic, counting
            only those whose 7 days were over when the stream began (the record the scheduler
            or a prompt could have had then)

For each: how many uploads have a value, Spearman's rank correlation against 7-day views, and,
inside each stream with 4+ ranked uploads, the 7-day views of its top half against its bottom
half (the scheduler only ever ranks within a stream). Only uploads have views, so whatever the
budget dropped is never in it: a ranker is checked on what went out, which flatters none of
them in particular but is all there is.

`topic_record` is the same track record for a prompt (#258 B). The monthly report
(`clipper analytics --report`) ends with the lift table.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import jsonstate
from .config import base
from .states import split_key

KINDS = ("short", "clip", "segment", "compilation")
RANKERS = ("score", "heat", "topic")
D7_DAYS = 8            # analytics.run fetches a video's 7-day numbers once it is 8 days old
MIN_SPEARMAN = 5       # fewer values than this: no correlation shown
MIN_HALVES = 4         # ranked uploads a stream needs before its halves count


# ---------- pure ----------

def ranks(xs: list[float]) -> list[float]:
    """1-based ranks, ties sharing their average rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman's rank correlation, or None with fewer than MIN_SPEARMAN pairs or no spread."""
    if len(xs) != len(ys) or len(xs) < MIN_SPEARMAN:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    return None if not sxx or not syy else round(sxy / (sxx * syy) ** 0.5, 2)


def median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    return 0.0 if not n else xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def halves(rows: list[dict], ranker: str) -> dict:
    """Inside each stream with MIN_HALVES+ ranked uploads: the views of the better-ranked half
    against the other half (the middle one left out of an odd count), summed, and in how many
    streams the top half won."""
    by_stream: dict[str, list[dict]] = {}
    for r in rows:
        if r.get(ranker) is not None:
            by_stream.setdefault(r["stream"], []).append(r)
    out = {"streams": 0, "top": 0, "bottom": 0, "top_won": 0}
    for group in by_stream.values():
        if len(group) < MIN_HALVES:
            continue
        group = sorted(group, key=lambda r: (-r[ranker], r["key"]))
        h = len(group) // 2
        top, bottom = sum(r["views"] for r in group[:h]), sum(r["views"] for r in group[-h:])
        out["streams"] += 1
        out["top"] += top
        out["bottom"] += bottom
        out["top_won"] += top > bottom
    return out


def by_value(rows: list[dict], ranker: str) -> list[dict]:
    """Median views per whole value of the ranker (for a score), lowest value first."""
    groups: dict[int, list[int]] = {}
    for r in rows:
        if r.get(ranker) is not None:
            groups.setdefault(round(r[ranker]), []).append(r["views"])
    return [{"value": v, "n": len(g), "median": median(g)} for v, g in sorted(groups.items())]


def topic_key(topic: str) -> str:
    return " ".join((topic or "").split()).casefold()


def known_by(r: dict, when: datetime) -> bool:
    """An upload's 7-day views were in by `when`."""
    return r["published"] + timedelta(days=D7_DAYS) <= when


def topic_history(rows: list[dict]) -> None:
    """Sets each row's "topic": the median views of the same show's earlier uploads on its
    topic that were known_by its stream's start, or None (no topic, or no such upload)."""
    by_topic: dict[tuple, list[dict]] = {}
    for r in rows:
        if topic_key(r.get("topic_name", "")):
            by_topic.setdefault((r["show"], topic_key(r["topic_name"])), []).append(r)
    for r in rows:
        prior = [p["views"] for p in by_topic.get((r["show"], topic_key(r.get("topic_name", ""))), [])
                 if p is not r and known_by(p, r["decided_at"])]
        r["topic"] = median(prior) if prior else None


def topic_records(rows: list[dict], show: str, before: datetime | None = None) -> list[dict]:
    """The show's track record per topic: uploads, median and best 7-day views, the best one's
    title, counting only uploads known_by `before` (all of them when None). Best median first."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r["show"] == show and topic_key(r.get("topic_name", "")) and (before is None or known_by(r, before)):
            groups.setdefault(topic_key(r["topic_name"]), []).append(r)
    out = []
    for g in groups.values():
        best = max(g, key=lambda r: r["views"])
        latest = max(g, key=lambda r: r["published"])
        out.append({"topic": latest["topic_name"], "n": len(g), "median": median([r["views"] for r in g]),
                    "best": best["views"], "best_title": best["title"]})
    return sorted(out, key=lambda t: (-t["median"], -t["n"], t["topic"]))


def table(rows: list[dict], show: str | None = None, kind: str | None = None) -> list[dict]:
    """One group per (show, kind), plus "all" shows per kind when there are several; each with
    its uploads, median views and a line per ranker that has any values."""
    rows = [r for r in rows if (not show or r["show"] == show) and (not kind or r["kind"] == kind)]
    shows = sorted({r["show"] for r in rows})
    groups = [(s, k) for k in KINDS for s in ([*shows, "all"] if len(shows) > 1 else shows)]
    out = []
    for s, k in groups:
        g = [r for r in rows if r["kind"] == k and (s == "all" or r["show"] == s)]
        if not g:
            continue
        line = {"show": s, "kind": k, "uploads": len(g), "median_views": median([r["views"] for r in g]),
                "rankers": []}
        for name in RANKERS:
            have = [r for r in g if r.get(name) is not None]
            if not have:
                continue
            line["rankers"].append({
                "ranker": name, "n": len(have),
                "spearman": spearman([r[name] for r in have], [r["views"] for r in have]),
                "halves": halves(have, name),
                **({"by_value": by_value(have, name)} if name == "score" else {})})
        out.append(line)
    return out


def markdown(groups: list[dict]) -> str:
    """The table for a terminal or the monthly report."""
    if not groups:
        return "No uploads with 7-day numbers yet: run clipper analytics."
    lines = ["| show | kind | uploads | median | ranker | n | Spearman | top half vs bottom (streams, top won) |",
             "|---|---|---:|---:|---|---:|---:|---|"]
    scores = []
    for g in groups:
        first = True
        for r in g["rankers"] or [None]:
            head = (f"| {g['show']} | {g['kind']} | {g['uploads']} | {g['median_views']:g} " if first
                    else "| | | | ")
            first = False
            if r is None:
                lines.append(head + "| (none) | | | |")
                continue
            h = r["halves"]
            half = f"{h['top']:,} vs {h['bottom']:,} ({h['streams']}, {h['top_won']})" if h["streams"] else ""
            rho = "" if r["spearman"] is None else f"{r['spearman']:+.2f}"
            lines.append(head + f"| {r['ranker']} | {r['n']} | {rho} | {half} |")
            if r.get("by_value") and g["show"] != "all":
                scores.append(f"- {g['show']} {g['kind']}: " + ", ".join(
                    f"{v['value']} → {v['median']:g} (n={v['n']})" for v in r["by_value"]))
    out = ["\n".join(lines)]
    if scores:
        out.append("Median 7-day views by score:\n" + "\n".join(scores))
    out.append("Spearman +1: the ranker orders uploads exactly as views do; 0: no relation. Under "
               f"{MIN_SPEARMAN} values none is shown. Only what went out has views, so this checks each "
               "ranker on what it let through.")
    return "\n\n".join(out)


# ---------- reading the share ----------

def when(text: str) -> datetime:
    """An ISO time; one without a zone is taken as UTC, so every time here compares."""
    t = datetime.fromisoformat(text)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def stream_facts(sdir: Path, heat: bool = True) -> dict:
    """What lift needs from a stream folder: when it began, its segments' chat heat and its
    compilations' segments. Empty for a stream that's gone (pruned or never here)."""
    from .schedule import segment_heat

    meta = jsonstate.read(sdir / "meta.json", {}) or {}
    started = meta.get("started_at")
    seg = (jsonstate.read(sdir / "segments.json", {}) or {}) if heat else {}
    try:
        values = segment_heat(sdir, seg.get("segments", [])) if heat else {}
    except (OSError, ValueError):  # a signals.parquet that can't be read: no heat, the rest stands
        values = {}
    return {"started": when(started) if started else None, "heat": values,
            "comps": {c["file"].removesuffix(".mp4"): c["segments"] for c in seg.get("compilations", [])}}


def item_heat(kind: str, key: str, facts: dict) -> float | None:
    heat = facts["heat"]
    if kind == "segment":
        return heat.get(key.split("_")[0])
    if kind == "compilation" and key.startswith("comp-"):
        vals = [heat[s] for s in facts["comps"].get(key.removeprefix("comp-"), []) if s in heat]
        return sum(vals) / len(vals) if vals else None
    return None


def rows(cfg: dict, heat: bool = True) -> list[dict]:
    """Every upload with 7-day numbers in analytics.json, with its rankers' values (heat:
    read each stream's signals.parquet for it; the topic record alone doesn't need to)."""
    from .analytics import load

    cfg = base(cfg)
    streams = Path(cfg["paths"]["streams"])
    facts: dict[str, dict] = {}
    out = []
    for r in load(cfg).values():
        d7 = r.get("d7") or {}
        if not d7 or r.get("missing") or r.get("kind") not in KINDS:
            continue
        sid, key = split_key(r["key"])
        f = facts.get(sid) or facts.setdefault(sid, stream_facts(streams / sid, heat))
        published = when(r["publish_at"])
        try:
            score = float(r["score"]) if r.get("score") not in (None, "") else None
        except (TypeError, ValueError):
            score = None
        out.append({"key": r["key"], "stream": sid, "show": r.get("show") or "?", "kind": r["kind"],
                    "title": r.get("title", ""), "views": int(d7.get("views") or 0), "published": published,
                    "decided_at": f["started"] or published, "topic_name": r.get("topic") or "",
                    "score": score, "heat": item_heat(r["kind"], key, f)})
    topic_history(out)
    return out


def topic_record(cfg: dict, show: str, before: datetime | None = None) -> list[dict]:
    """topic_records over analytics.json: the show's per-topic track record as of `before`."""
    return topic_records(rows(cfg, heat=False), show, before)
