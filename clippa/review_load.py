"""`clippa review load`: what a reviewer would see from a pipeline run, read only.

Usage:
    clippa review load <stream...> [--config PATH] [--costs PATH] [--arm NAME] [--json]

Nothing is written. Kitsu is not asked, checks are not run, and no model or vision call is
made. `review_auto.verdicts` is the dry-run view (`clippa review auto --dry-run`); `run` is
never called, because that one approves. Each column is a local file:

    items        the cut files review.items would list for segments, picks, shorts and
                 compilations (a compilation counts even when [review] include leaves it as
                 a playlist). No segments.json: the kinds in review_state.json, and the row
                 says so
    findings     checks.json: items with findings, and how many of those items have a bad or
                 a warn for each check (text, picture, cover, subject)
    held         Shorts still held for other voices (wiki Review): over
                 [scoring] max_source_audio_s while [shorts] hold_source_audio is on, and the
                 Hold a person sees (review.json, else the published proposal, else the
                 proposal on the cut) is still on. A released hold is not counted
    auto         review_auto.verdict, never run(). Published items (review_state.json) use
                 the dry-run rules, grace window included. A stream that was never published,
                 and any cut item missing from review_state.json, is judged as if it had just
                 been published and nobody had touched it: the same rules (findings, a missing
                 or failed check, the kind's score floor, a Short held for other voices,
                 stories and one-offs), with no grace window and no Kitsu. source is
                 "published" or "cuts (unpublished)"
    vision       review_check.estimate: cover vision calls a check run would make, and their
                 price. Nothing is called
    cost         the --costs jsonl (or [paths] costs): this stream's lines, total and by stage
    segments     segments.json: the longest, and how many run over [segments] split_over_s
                 (3000 s, 50 minutes, when that setting is absent)

--json is one document. Every row carries arm, stream and config, so several arms'
rows concatenate into one comparison (drop each document's total, or keep stream "total").
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from . import config, costs, jsonstate, review, review_auto, review_check, wiki
from .actions import is_held, other_voices
from .checkup import BAD, WARN
from .config import for_stream
from .errors import ClipperError
from .timefmt import length

# The four kinds review.items knows, and the include list that asks for every one of them.
# [review] include often drops compilations (they ship as playlists); the count is still the
# cut the run produced, which is what a person comparing arms wants to see.
KINDS = review_auto.KINDS
INCLUDE = ["segments", "picks", "shorts", "compilations"]
LONG_S = 3000  # 50 minutes: a segment longer than this is its own column
CHECKS = review_check.CHECKS
PAGE = wiki.page("Review", "review-load")

HEAD = ["stream", "short", "pick", "segment", "comp", "findings", "held",
        "approve", "waits", "vision", "cost", "longest", ">50m"]


def _config_path(cfg: dict) -> str:
    root = config.base(cfg)
    path = root.get("_path") or root.get("_file") or ""
    return str(path) if path else ""


def _arm_name(cfg: dict, arm: str) -> str:
    if arm:
        return arm
    path = _config_path(cfg)
    return Path(path).stem if path else "default"


def resolve(cfg: dict, refs: list[str]) -> list[Path]:
    """Stream ids or directories, in order, each once."""
    out, seen = [], set()
    for ref in refs:
        directory = config.find_stream(cfg, ref).resolve()
        if directory in seen:
            continue
        seen.add(directory)
        out.append(directory)
    if not out:
        raise ClipperError("review load needs a stream")
    return out


def _count(items: list[dict]) -> dict[str, int]:
    return {kind: sum(it.get("kind") == kind for it in items) for kind in KINDS}


def _count_state(state: dict) -> dict[str, int]:
    counts = dict.fromkeys(KINDS, 0)
    for entry in state.values():
        if isinstance(entry, dict) and entry.get("kind") in counts:
            counts[entry["kind"]] += 1
    return counts


def load_items(sdir: Path, cfg: dict) -> tuple[list[dict], dict[str, int], str]:
    """(review items, counts by kind, note). A stream with no segments.json is counted from
    review_state.json, the mirror of what was published."""
    state = jsonstate.read_or(sdir / "review_state.json", {})
    if not (sdir / "segments.json").exists():
        return [], _count_state(state), "no segments.json; item counts are from review_state.json"
    try:
        items = review.items(sdir, INCLUDE, cfg)
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [], _count_state(state), (
            f"review items unreadable ({type(e).__name__}); item counts are from review_state.json")
    return items, _count(items), ""


def _seconds(key: str, meta: dict, proposed: dict, decisions: dict, state: dict):
    """The Short's other-voice seconds: the cut's source_audio_s, else the column."""
    if meta.get("source_audio_s") is not None:
        return meta["source_audio_s"]
    for fields in ((decisions.get(key) or {}).get("fields") or {},
                   (state.get(key) or {}).get("fields") or {}, proposed):
        if fields.get("Other voices") not in (None, ""):
            return fields["Other voices"]
    return None


def _hold_on(key: str, proposed: dict, decisions: dict, state: dict) -> bool:
    """The Hold the reviewer has now. A synced column wins (a release sticks); else what was
    published; else the proposal on the cut."""
    synced = (decisions.get(key) or {}).get("fields") or {}
    if "Hold" in synced:
        return is_held(synced)
    published = (state.get(key) or {}).get("fields") or {}
    if "Hold" in published:
        return is_held(published)
    return is_held(proposed)


def shorts_held(cfg: dict, items: list[dict], decisions: dict, state: dict) -> tuple[int, int]:
    """(still held for other voices, shorts over the limit). The feature has to be on for the
    first number; the second is the measurement either way."""
    limit = cfg["scoring"]["max_source_audio_s"]
    rows = [(it["key"], it.get("meta") or {}, it.get("fields") or {})
            for it in items if it.get("kind") == "short"]
    seen = {key for key, _, _ in rows}
    for key, entry in state.items():
        if isinstance(entry, dict) and entry.get("kind") == "short" and key not in seen:
            rows.append((key, {}, entry.get("fields") or {}))
    held = over = 0
    for key, meta, proposed in rows:
        if not other_voices(_seconds(key, meta, proposed, decisions, state), limit):
            continue
        over += 1
        if cfg["shorts"]["hold_source_audio"] and _hold_on(key, proposed, decisions, state):
            held += 1
    return held, over


def _hold_note(notes: list[str], cfg: dict, held: int, over: int) -> None:
    if not over:
        return
    if not cfg["shorts"]["hold_source_audio"]:
        notes.append(f"[shorts] hold_source_audio is off; {_shorts(over)} over the other-voices limit")
        return
    if over != held:
        notes.append(f"{_shorts(over)} over the other-voices limit, {_shorts(held)} still held")


def _shorts(n: int) -> str:
    return f"{n} Short" if n == 1 else f"{n} Shorts"


def _empty_findings() -> dict:
    return {"items": 0, "by_check": {check: {"bad": 0, "warn": 0} for check in CHECKS}, "other": {}}


def findings_summary(sdir: Path, notes: list[str]) -> dict:
    """Items checks.json has findings for. One item counts once per check and level, so two
    text warns are one text warn."""
    path = review_check.path(sdir)
    if not path.exists():
        return _empty_findings()
    try:
        checks = jsonstate.load(path)
    except (OSError, ValueError) as e:
        notes.append(f"checks.json unreadable: {type(e).__name__}")
        return _empty_findings()
    if not isinstance(checks, dict):
        notes.append("checks.json is not an object")
        return _empty_findings()
    out = _empty_findings()
    for entry in checks.values():
        found = entry.get("findings") if isinstance(entry, dict) else None
        if not isinstance(found, list) or not found:
            continue
        out["items"] += 1
        seen = set()
        for finding in found:
            if not isinstance(finding, dict):
                continue
            check = str(finding.get("check") or "other")
            level = finding.get("level")
            if level not in (BAD, WARN):
                level = WARN
            if (check, level) in seen:
                continue
            seen.add((check, level))
            bucket = out["by_check"] if check in out["by_check"] else out["other"]
            levels = bucket.setdefault(check, {"bad": 0, "warn": 0})
            levels[level] += 1
    return out


def _published_state(sdir: Path, notes: list[str]) -> dict:
    """review_state.json, or {} when it isn't there. A broken file is said, and read as none."""
    path = sdir / "review_state.json"
    if not path.exists():
        return {}
    try:
        state = jsonstate.load(path)
    except (OSError, ValueError) as e:
        notes.append(f"review_state.json unreadable ({type(e).__name__}); judging the cuts")
        return {}
    return state if isinstance(state, dict) else {}


def _cut_fields(it: dict) -> dict:
    """Score and Hold as the cut proposes them. A person's Kitsu edit is not on an unpublished item."""
    fields = it.get("fields") or {}
    return {name: fields[name] for name in ("Score", "Hold", "Other voices")
            if name in fields and fields[name] not in (None, "")}


def _cut_verdict(cfg: dict, kind: str, it: dict, checks: dict, now: datetime) -> review_auto.Verdict:
    """review_auto.verdict as if this cut had just been published and nobody had touched it.
    No grace window: a clean item that would wait out the clock counts as due."""
    fields = _cut_fields(it)
    state = {"kind": it.get("kind"), "fields": dict(fields)}
    decision = {"decision": "pending", "fields": dict(fields)}
    got = review_auto.verdict(cfg, kind, it["key"], state, decision, checks.get(it["key"]),
                              {}, False, now)
    if got.state in ("due", "waiting"):
        return review_auto.Verdict(got.key, "due")
    return got


def _tally(verdicts: list[review_auto.Verdict]) -> dict:
    by_state = dict(Counter(v.state for v in verdicts))
    reasons = dict(sorted(
        Counter(v.why or "(no reason)" for v in verdicts if v.state == "person").items(),
        key=lambda kv: (-kv[1], kv[0])))
    return {"would_approve": by_state.get("due", 0) + by_state.get("waiting", 0),
            "waits": by_state.get("person", 0), "reasons": reasons, "by_state": by_state}


def auto_summary(cfg: dict, sdir: Path, items: list[dict], now, notes: list[str]) -> dict:
    """Would-approve against waits-for-a-person. Published items: review_auto.verdicts (the
    dry-run, grace included). Anything else on the cuts: verdict() with no grace and no Kitsu.
    run() is never called."""
    now = now or datetime.now(UTC)
    state = _published_state(sdir, notes)
    published = review_auto.verdicts(cfg, sdir, now=now) if state else {}
    checks = review_check.load(sdir)
    kind = review_auto.stream_kind(sdir)
    cuts = [_cut_verdict(cfg, kind, it, checks, now) for it in items if it["key"] not in published]
    verdicts = [*published.values(), *cuts]
    if not verdicts:
        notes.append("no published items and no cut items to judge")
        return {**_tally([]), "source": "cuts (unpublished)"}
    tally = _tally(verdicts)
    if published and cuts:
        tally["source"] = "published"
        n = len(cuts)
        word = "cut item" if n == 1 else "cut items"
        notes.append(f"{n} {word} not in review_state.json, judged from the cuts "
                     "(no grace window, not a Kitsu publish)")
    elif published:
        tally["source"] = "published"
    else:
        tally["source"] = "cuts (unpublished)"
        notes.append("auto verdicts are from the cuts (unpublished), as if just published and "
                     "untouched (no grace window, not a Kitsu publish)")
    if tally["by_state"].get("off"):
        notes.append(f"auto-approve is off ({tally['by_state']['off']} item(s) left to a person)")
    return tally


def vision_summary(sdir: Path, cfg: dict, notes: list[str]) -> dict:
    """review_check.estimate. It prices covers; it does not call the model. No segments.json
    means there are no review items to price (load_items already says so)."""
    if not (sdir / "segments.json").exists():
        return {"calls": None, "usd": None}
    try:
        calls, usd = review_check.estimate(sdir, cfg)
    except (OSError, ValueError, KeyError, TypeError, ClipperError) as e:
        notes.append(f"vision estimate unavailable: {type(e).__name__}: {e}")
        return {"calls": None, "usd": None}
    return {"calls": calls, "usd": round(usd, 6)}


def cost_summary(path: Path | None, stream: str, notes: list[str]) -> dict:
    """This stream's lines in the costs jsonl. Other streams in the file are not this arm's."""
    if path is None or not Path(path).exists():
        where = f" ({path})" if path else ""
        notes.append(f"no costs file{where}")
        return {"usd": None, "by_stage": {}}
    by: dict[str, float] = {}
    for row in costs.read(Path(path)):
        if row.get("stream") != stream or not isinstance(row.get("usd"), int | float):
            continue
        stage = str(row.get("stage") or "other")
        by[stage] = by.get(stage, 0.0) + float(row["usd"])
    by = {stage: round(usd, 6) for stage, usd in sorted(by.items())}
    return {"usd": round(sum(by.values()), 6), "by_stage": by}


def _seg_len(seg: dict) -> float | None:
    if isinstance(seg.get("duration_s"), int | float):
        return float(seg["duration_s"])
    if isinstance(seg.get("end_s"), int | float) and isinstance(seg.get("start_s"), int | float):
        return float(seg["end_s"]) - float(seg["start_s"])
    return None


def over_limit_s(cfg: dict | None) -> float:
    """[segments] split_over_s when the config has it (added with the re-split), else 50 minutes.
    A bad value keeps the 50 minutes, rather than counting every segment or none."""
    raw = ((cfg or {}).get("segments") or {}).get("split_over_s", LONG_S)
    try:
        limit = float(raw)
    except (TypeError, ValueError):
        return float(LONG_S)
    return limit if limit > 0 else float(LONG_S)


def segment_stats(sdir: Path, notes: list[str], cfg: dict | None = None) -> dict:
    """Longest segment and how many run over the split limit, from segments.json."""
    limit = over_limit_s(cfg)
    blank = {"longest_s": None, "over_50min": None, "n": 0, "over_s": limit}
    path = sdir / "segments.json"
    if not path.exists():
        # load_items already says so when the row is built from a stream with no segments.json
        if not any(n.startswith("no segments.json") for n in notes):
            notes.append("no segments.json")
        return blank
    try:
        data = jsonstate.load(path)
    except (OSError, ValueError) as e:
        notes.append(f"segments.json unreadable: {type(e).__name__}")
        return blank
    if not isinstance(data, dict):
        notes.append("segments.json is not an object")
        return blank
    lengths = [got for seg in data.get("segments") or [] if isinstance(seg, dict)
               and (got := _seg_len(seg)) is not None]
    if not lengths:
        notes.append("segments.json has no lengths")
        return {**blank, "over_50min": 0}
    return {"longest_s": max(lengths), "over_50min": sum(got > limit for got in lengths),
            "n": len(lengths), "over_s": limit}


def one(cfg: dict, sdir: Path, costs_path: Path | None, arm: str, config_path: str, now) -> dict:
    """One stream's row. cfg: any (the stream's own is resolved here)."""
    scfg = for_stream(cfg, sdir)
    notes: list[str] = []
    items, counts, item_note = load_items(sdir, scfg)
    if item_note:
        notes.append(item_note)
    decisions = jsonstate.read_or(sdir / "review.json", {})
    state = jsonstate.read_or(sdir / "review_state.json", {})
    if items:
        have = {it["key"] for it in items}
        gone = [key for key in state if key not in have]
        if gone:
            notes.append(f"{len(gone)} published item(s) have no cut file; not in the item counts")
    held, over = shorts_held(scfg, items, decisions, state)
    _hold_note(notes, scfg, held, over)
    return {
        "arm": arm, "stream": sdir.name, "config": config_path,
        "costs_file": "" if costs_path is None else str(costs_path),
        "items": counts,
        "findings": findings_summary(sdir, notes),
        "held_other_voices": held, "other_voices_over_limit": over,
        "auto": auto_summary(scfg, sdir, items, now, notes),
        "vision": vision_summary(sdir, scfg, notes),
        "cost": cost_summary(costs_path, sdir.name, notes),
        "segments": segment_stats(sdir, notes, scfg),
        "notes": notes,
    }


def _sum_int(vals: list) -> int | None:
    if all(v is None for v in vals):
        return None
    return sum(v or 0 for v in vals)


def _sum_usd(vals: list) -> float | None:
    if all(v is None for v in vals):
        return None
    return round(sum(v or 0 for v in vals), 6)


def _sum_findings(rows: list[dict]) -> dict:
    out = _empty_findings()
    out["items"] = _sum_int([row["findings"]["items"] for row in rows]) or 0
    for row in rows:
        for check, levels in {**row["findings"]["by_check"], **row["findings"]["other"]}.items():
            bucket = out["by_check"] if check in out["by_check"] else out["other"]
            got = bucket.setdefault(check, {"bad": 0, "warn": 0})
            got["bad"] += levels.get("bad") or 0
            got["warn"] += levels.get("warn") or 0
    return out


def _sum_named(rows: list[dict], section: str, key: str) -> dict:
    counts: Counter = Counter()
    for row in rows:
        counts.update(row[section][key])
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def total_row(rows: list[dict], arm: str, config_path: str, costs_path: Path | None) -> dict:
    """The same shape as a stream row. Longest is the max; everything else adds."""
    longs = [row["segments"]["longest_s"] for row in rows if row["segments"]["longest_s"] is not None]
    limits = {row["segments"].get("over_s") for row in rows}
    sources = {row["auto"].get("source") for row in rows}
    stages: dict[str, float] = {}
    for row in rows:
        for stage, usd in row["cost"]["by_stage"].items():
            stages[stage] = round(stages.get(stage, 0.0) + usd, 6)
    return {
        "arm": arm, "stream": "total", "config": config_path,
        "costs_file": "" if costs_path is None else str(costs_path),
        "items": {kind: sum(row["items"][kind] for row in rows) for kind in KINDS},
        "findings": _sum_findings(rows),
        "held_other_voices": sum(row["held_other_voices"] for row in rows),
        "other_voices_over_limit": sum(row["other_voices_over_limit"] for row in rows),
        "auto": {"would_approve": _sum_int([row["auto"]["would_approve"] for row in rows]),
                 "waits": _sum_int([row["auto"]["waits"] for row in rows]),
                 "reasons": _sum_named(rows, "auto", "reasons"),
                 "by_state": _sum_named(rows, "auto", "by_state"),
                 "source": sources.pop() if len(sources) == 1 else "mixed"},
        "vision": {"calls": _sum_int([row["vision"]["calls"] for row in rows]),
                   "usd": _sum_usd([row["vision"]["usd"] for row in rows])},
        "cost": {"usd": _sum_usd([row["cost"]["usd"] for row in rows]),
                 "by_stage": dict(sorted(stages.items()))},
        "segments": {"longest_s": max(longs) if longs else None,
                     "over_50min": _sum_int([row["segments"]["over_50min"] for row in rows]),
                     "n": sum(row["segments"]["n"] for row in rows),
                     "over_s": limits.pop() if len(limits) == 1 else None},
        "notes": [],
    }


def collect(cfg: dict, refs: list[str], costs_path: Path | None, arm: str = "", now=None,
            local_overrides: bool = True) -> dict:
    """Every stream, plus a total. Read only. local_overrides: False when --config loaded the
    arm file with clipper.local.toml and CLIPPER_*/CLIPPA_* set aside."""
    arm = _arm_name(cfg, arm)
    config_path = _config_path(cfg)
    path = None if costs_path is None else Path(costs_path)
    rows = [one(cfg, directory, path, arm, config_path, now) for directory in resolve(cfg, refs)]
    return {"arm": arm, "config": config_path, "costs_file": "" if path is None else str(path),
            "read_only": True, "local_overrides": local_overrides, "rows": rows,
            "total": total_row(rows, arm, config_path, path)}


def money(value) -> str:
    """A dollar amount for the table. None is a blank cell; under a dime keeps the extra digits
    (a cover's vision call is about a cent)."""
    if value is None:
        return "-"
    if value == 0:
        return "$0"
    if abs(value) >= 0.1:
        return f"${value:.2f}"
    return f"${value:.4f}".rstrip("0").rstrip(".")


def _num(value) -> str:
    return "-" if value is None else str(value)


def _cells(row: dict) -> list[str]:
    items = row["items"]
    vision = row["vision"]
    vision_cell = "-" if vision["calls"] is None else f"{vision['calls']} {money(vision['usd'])}"
    longest = row["segments"]["longest_s"]
    return [row["stream"], _num(items["short"]), _num(items["pick"]), _num(items["segment"]),
            _num(items["compilation"]), _num(row["findings"]["items"]), _num(row["held_other_voices"]),
            _num(row["auto"]["would_approve"]), _num(row["auto"]["waits"]), vision_cell,
            money(row["cost"]["usd"]), "-" if longest is None else length(longest),
            _num(row["segments"]["over_50min"])]


def _table(rows: list[dict]) -> list[str]:
    body = [HEAD, *[_cells(row) for row in rows]]
    widths = [max(len(line[i]) for line in body) for i in range(len(HEAD))]
    out = []
    for n, line in enumerate(body):
        cells = [cell.ljust(w) if i == 0 else cell.rjust(w) for i, (cell, w) in enumerate(zip(line, widths))]
        out.append("  ".join(cells))
        if n == 0:
            out.append("  ".join("-" * w for w in widths))
    return out


def _finding_bits(row: dict) -> list[str]:
    parts = []
    checks = {**row["findings"]["by_check"], **row["findings"]["other"]}
    for check in (*CHECKS, *(c for c in checks if c not in CHECKS)):
        levels = checks.get(check) or {}
        if levels.get("bad"):
            parts.append(f"{check} bad {levels['bad']}")
        if levels.get("warn"):
            parts.append(f"{check} warn {levels['warn']}")
    return parts


def _details(rows: list[dict]) -> list[str]:
    out = []
    for row in rows:
        bits = []
        found = _finding_bits(row)
        if found:
            bits.append("findings: " + ", ".join(found))
        if row["auto"]["reasons"]:
            bits.append("waits: " + ", ".join(f"{why} ({n})" for why, n in row["auto"]["reasons"].items()))
        if row["cost"]["by_stage"]:
            bits.append("cost by stage: " + ", ".join(
                f"{stage} {money(usd)}" for stage, usd in row["cost"]["by_stage"].items()))
        if row["notes"]:
            bits.append("; ".join(row["notes"]))
        if bits:
            out.append(f"{row['stream']}: " + " | ".join(bits))
    return out


def lines(rep: dict) -> list[str]:
    """The plain-text report: a header, one row per stream, a total, then the splits."""
    head = [
        f"review load  arm={rep['arm']}  config={rep['config'] or '(none)'}  "
        f"costs={rep['costs_file'] or '(none)'}",
    ]
    if rep.get("local_overrides") is False:
        head.append(f"config {rep['config']}: local overrides ignored")
    head += [
        f"read only: no files written, no Kitsu, no model or vision calls  {PAGE}",
        "",
    ]
    detail = _details(rep["rows"])
    return head + _table([*rep["rows"], rep["total"]]) + ([""] + detail if detail else [])


def _without_machine_env(fn):
    """CLIPPER_* and CLIPPA_* are this machine. Hold them out of fn, then put them back."""
    saved = {key: os.environ.pop(key) for key in list(os.environ)
             if key.startswith(("CLIPPER_", "CLIPPA_"))}
    try:
        return fn()
    finally:
        os.environ.update(saved)


def run(cfg: dict, args, now=None) -> dict:
    """The command. --config reloads that arm's clipper.toml, with local overrides off
    for the whole report (a stream config.toml must not pick the machine's env back up).
    Prints the table, or JSON."""
    def report(loaded: dict, bare: bool) -> dict:
        costs_path = Path(args.costs) if getattr(args, "costs", None) else Path(loaded["paths"]["costs"])
        rep = collect(loaded, list(args.streams), costs_path, getattr(args, "arm", "") or "",
                      now=now, local_overrides=not bare)
        if getattr(args, "json", False):
            print(json.dumps(rep, indent=1, default=str, ensure_ascii=False))
        else:
            print("\n".join(lines(rep)))
        return rep

    if not getattr(args, "load_config", None):
        return report(cfg, False)
    path = Path(args.load_config)
    if not path.is_file():
        raise ClipperError(f"no config {path}")

    def load_and_report() -> dict:
        loaded = config.load(path, use_local=False)
        if getattr(args, "show", None):
            loaded = config.pin_show(loaded, args.show)
        return report(loaded, True)

    return _without_machine_env(load_and_report)
