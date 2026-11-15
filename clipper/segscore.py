"""A model score for each topic segment (#258 B): how well it would do as its own upload,
judged from the segment (title, summary, topic, kind, how much is someone else's video) and
the show's track record per topic (lift.topic_records), optionally with its transcript.

Usage:
    clipper segments <stream> --score       score the stream's cut segments now (again);
                                            [segments] score = true does it after each split
    clipper segments --backtest [<stream>] [--transcript] [--force] [--dry-run]
        every past stream with uploaded segments that have 7-day numbers, scored as of its
        start: the track record holds only uploads whose 7 days were over by then, so nothing
        learned later leaks in. Scores go to segment_scores_backtest.json beside segments.json,
        one entry per variant ("summary", "transcript"), never into segments.json; `clipper
        analytics --lift` reads them as the bt and bt_tx rankers.

A stream's cut segments are scored in one call, so they're ranked against each other. The
score is 1-10 against the show's typical upload (5); the why is one sentence of evidence.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from . import costs, jsonstate, lift
from .config import base, for_stream
from .errors import ClipperError
from .llm import Claude, estimate_lines, preflight, tokens
from .prompts import Prompts, version_of
from .timefmt import hms

BACKTEST = "segment_scores_backtest.json"
VARIANTS = {False: "summary", True: "transcript"}
MAX_TOPICS = 60          # the track record's topics, best median first
OUT_PER_SEGMENT = 70     # output tokens a segment's score and why take, for estimates
# The 2026-11-14 backtest's logged input was 1.6-1.8x llm.tokens' four-characters-a-token count,
# summaries and transcripts alike (4,108 vs 2,302; 175,527 vs 102,918), so estimates scale by it.
IN_FACTOR = 1.75

SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "score": {"type": "integer"}, "why": {"type": "string"}},
                "required": ["id", "score", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scores"],
    "additionalProperties": False,
}


# ---------- pure ----------

def record_block(rows: list[dict], show: str, before: datetime | None) -> str:
    """<track_record>: the show's segment uploads known by `before` (lift.known_by), per topic,
    plus the median of all of them and of a topic's first upload."""
    segs = [r for r in rows if r["show"] == show and r["kind"] == "segment"
            and (before is None or lift.known_by(r, before))]
    as_of = before.date().isoformat() if before else "now"
    if not segs:
        return (f'<track_record as_of="{as_of}">\nNo segment upload of this show has 7-day numbers yet: '
                "judge from the segments alone.\n</track_record>")
    first: dict[str, dict] = {}
    for r in sorted(segs, key=lambda r: r["published"]):
        if lift.topic_key(r["topic_name"]):
            first.setdefault(lift.topic_key(r["topic_name"]), r)
    recs = lift.topic_records(segs, show)
    head = (f"This show's segment uploads and their first 7 days: {len(segs)} uploads, median "
            f"{lift.median([r['views'] for r in segs]):g} views. A topic's first upload: median "
            f"{lift.median([r['views'] for r in first.values()]):g} views ({len(first)} topics). "
            "Per topic: uploads, median and best views, the best one's title.")
    lines = [f"- {t['topic']}: {t['n']}, median {t['median']:g}, best {t['best']} ({t['best_title'][:100]})"
             for t in recs[:MAX_TOPICS]]
    more = [f"(and {len(recs) - MAX_TOPICS} more topics with fewer views)"] if len(recs) > MAX_TOPICS else []
    return "\n".join([f'<track_record as_of="{as_of}">', head, *lines, *more, "</track_record>"])


def to_score(segs: list[dict]) -> list[dict]:
    """The segments a score is for: the ones cut (the others never become uploads)."""
    return [s for s in segs if s.get("cut")]


def segment_block(segs: list[dict], words: list[dict] | None = None) -> str:
    """One <segment> per segment; with words, its transcript too."""
    from .transcript import transcript_lines

    out = []
    for s in segs:
        attrs = (f'id="{s["id"]}" starts="{hms(s["start_s"])}" minutes="{s["duration_s"] / 60:.0f}" '
                 f'kind="{s["kind"]}" others_video="{s["source_share"]}" topic="{s["topic"]}"')
        body = [f"title: {s['title']}", f"figures: {', '.join(s.get('figures') or []) or '-'}",
                f"summary: {s['summary']}"]
        if words is not None:
            body += ["transcript:", *transcript_lines(words, s["start_s"], s["end_s"])]
        out.append(f"<segment {attrs}>\n" + "\n".join(body) + "\n</segment>")
    return "\n\n".join(out)


def message(rows: list[dict], show: str, before: datetime | None, segs: list[dict],
            words: list[dict] | None = None) -> str:
    return (record_block(rows, show, before) + f"\n\nThe stream's {len(segs)} segments, in order:\n\n"
            + segment_block(segs, words))


def checked(out: dict, ids: list[str]) -> dict[str, dict]:
    """{id: {score, why}} for the asked ids the answer has, scores held to 1-10; anything else
    in it is dropped."""
    return {s["id"]: {"score": min(10, max(1, int(s["score"]))), "why": s["why"].strip()}
            for s in out.get("scores", []) if s.get("id") in ids}


# ---------- calls ----------

def _words(sdir: Path, transcript: bool) -> list[dict] | None:
    if not transcript:
        return None
    from .transcript import load_words

    if not (sdir / "words.jsonl").exists():
        raise ClipperError(f"{sdir.name}: no words.jsonl for the transcript variant")
    return load_words(sdir)


def score(sdir: Path, cfg: dict, segs: list[dict], rows: list[dict], before: datetime | None,
          transcript: bool, claude: Claude) -> dict[str, dict]:
    """{id: {score, why}} for `segs` as of `before`, in one call. The stream's own uploads are
    never in its track record (a rescore of an old stream would otherwise read its own results).
    An answer that leaves segments out is asked once more; what it still leaves out stays
    unscored and is named (the model sometimes passes over a segment on a touchy subject)."""
    cfg = for_stream(cfg, sdir)
    sc = cfg["segments"]
    rows = [r for r in rows if r["stream"] != sdir.name]
    text = message(rows, cfg["show"]["key"], before, segs, _words(sdir, transcript))
    ids = [s["id"] for s in segs]
    system = Prompts(cfg).get("segment_score")
    got: dict[str, dict] = {}
    for _ in range(2):
        out, meta = claude.json_call(sc["score_model"], system, text, SCHEMA, sc["score_effort"],
                                     fallback=True, stream=True)
        if out is None:
            if got:  # a refused retry keeps the first answer (Copilot, #261)
                break
            raise ClipperError(f"segment score: model refused ({meta.get('refusal')})")
        new = checked(out, ids)
        if len(new) > len(got):  # the fuller answer, never a worse second one
            got = new
        if len(got) == len(ids):
            break
    missing = [i for i in ids if i not in got]
    if missing:
        print(f"  {sdir.name}: no score for {', '.join(missing)} (left out twice)")
    return got


def estimate_row(sdir: Path, cfg: dict, segs: list[dict], rows: list[dict], before: datetime | None,
                 transcript: bool) -> tuple[str, str, int, int, int]:
    cfg = for_stream(cfg, sdir)
    rows = [r for r in rows if r["stream"] != sdir.name]
    text = message(rows, cfg["show"]["key"], before, segs, _words(sdir, transcript))
    return (f"score {sdir.name}", cfg["segments"]["score_model"], 1,
            round(tokens(Prompts(cfg).get("segment_score") + text) * IN_FACTOR), OUT_PER_SEGMENT * len(segs))


def score_stream(sdir: Path, cfg: dict, data: dict, claude: Claude, force: bool = False) -> int:
    """[segments] score: fill segments.json's cut segments with score and score_why, as of now,
    all in one call so they're ranked together. Once a call has covered the stream's cut
    segments it isn't made again (a segment the model passed over stays unscored rather than
    paying for the whole call on every rerun) unless force or the cut set changed. Returns how
    many were scored; writes nothing: the caller saves data."""
    sc = for_stream(cfg, sdir)["segments"]
    segs = to_score(data["segments"])
    asked = sorted(s["id"] for s in segs)
    covered = (data.get("score") or {}).get("ids") == asked or all("score" in s for s in segs)
    if not segs or (not force and covered):
        return 0
    spent = claude.cost
    got = score(sdir, cfg, segs, lift.rows(cfg, heat=False), datetime.now(UTC), sc["score_transcript"], claude)
    for s in segs:
        if s["id"] in got:
            s["score"], s["score_why"] = got[s["id"]]["score"], got[s["id"]]["why"]
        else:  # left out: no stale score from an earlier run either
            s.pop("score", None)
            s.pop("score_why", None)
    data["score"] = {"model": sc["score_model"], "prompt_version": version_of(for_stream(cfg, sdir), "segment_score"),
                     "transcript": sc["score_transcript"], "at": datetime.now(UTC).isoformat(timespec="seconds"),
                     "usd": round(claude.cost - spent, 4), "ids": asked,
                     "left_out": [i for i in asked if i not in got]}
    return len(got)


def backtest(cfg: dict, stream: str = "", transcript: bool = False, force: bool = False,
             dry_run: bool = False) -> dict:
    """Score past streams as of their start (see the module doc). Returns {stream: n scored}."""
    cfg = base(cfg)
    streams_dir = Path(cfg["paths"]["streams"])
    rows = lift.rows(cfg, heat=False)
    starts = {r["stream"]: r["decided_at"] for r in rows if r["kind"] == "segment" and r["decided_at"]}
    if stream:
        if stream not in starts:
            raise ClipperError(f"{stream}: no uploaded segment with 7-day numbers and a known start")
        starts = {stream: starts[stream]}
    variant = VARIANTS[transcript]
    plan = []
    for sid in sorted(starts, key=lambda s: starts[s]):
        sdir = streams_dir / sid
        if (jsonstate.read(sdir / "meta.json", {}) or {}).get("kind") in ("preshow", "story"):
            print(f"  {sid}: a pre-show or story is one whole video, nothing to rank; skipped")
            continue
        data = jsonstate.read(sdir / "segments.json", {}) or {}
        segs = to_score(data.get("segments", []))
        if not segs:
            print(f"  {sid}: no cut segments in segments.json, skipped")
            continue
        if transcript and not (sdir / "words.jsonl").exists():
            print(f"  {sid}: no words.jsonl, skipped")
            continue
        try:
            scored = variant in (jsonstate.read(sdir / BACKTEST, {}) or {})
        except ValueError as e:  # a broken file: score it again (update rewrites it whole)
            print(f"  {sid}: {BACKTEST} unreadable ({e}), scoring again")
            if not dry_run:  # a dry run leaves every file as it found it (Copilot, #261)
                jsonstate.write(sdir / BACKTEST, {})
            scored = False
        if not force and scored:
            print(f"  {sid}: {variant} already scored (--force to score again)")
            continue
        plan.append((sid, sdir, segs))
    if dry_run:
        est = [estimate_row(sdir, cfg, segs, rows, starts[sid], transcript) for sid, sdir, segs in plan]
        print("\n".join([f"estimate, {variant} variant, {len(plan)} streams:", *estimate_lines(cfg, est)]))
        return {}
    if not plan:
        print("nothing to score")
        return {}
    # One client per show's resolved config, so a show's own provider and model are the ones
    # checked and used (Copilot, #261); costs add up over them.
    clients: dict[str, Claude] = {}
    done = {}
    for sid, sdir, segs in plan:
        costs.set_context("segments backtest", sid)
        scfg = for_stream(cfg, sdir)
        sc = scfg["segments"]
        claude = clients.get(scfg["show"]["key"])
        if claude is None:
            claude = clients[scfg["show"]["key"]] = Claude(scfg)
            preflight(claude, sc["score_model"])
        spent = claude.cost
        got = score(sdir, cfg, segs, rows, starts[sid], transcript, claude)
        with jsonstate.update(sdir / BACKTEST, {}) as bt:
            bt[variant] = {"model": sc["score_model"],
                           "prompt_version": version_of(for_stream(cfg, sdir), "segment_score"),
                           "as_of": starts[sid].isoformat(), "at": datetime.now(UTC).isoformat(timespec="seconds"),
                           "usd": round(claude.cost - spent, 4), "scores": got}
        done[sid] = len(got)
        print(f"  {sid}: {len(got)} segments as of {starts[sid]:%Y-%m-%d}, ${claude.cost - spent:.3f}")
    print(f"{variant}: {len(done)} streams, ${sum(c.cost for c in clients.values()):.2f}")
    return done
