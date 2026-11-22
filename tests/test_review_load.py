"""clippa review load: a read-only count of what a reviewer would see. Offline fixtures only."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from clippa import cli, jsonstate, review, review_load
from clippa.errors import ClipperError

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)
PUB = (NOW - timedelta(hours=3)).isoformat(timespec="seconds")
CLEAN = {"sig": "s", "findings": []}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    jsonstate.write(path, data)
    path.with_suffix(".mp4").write_bytes(b"")


def _segment(sid, topic, start, end, duration, cut=True):
    return {"id": sid, "start_s": start, "end_s": end, "duration_s": duration, "cut": cut,
            "topic": topic, "title": topic, "description": "About it.", "source_share": "none"}


def _short(key, seconds, score):
    return {"key": key, "id": key.split("_", 1)[1], "in_s": 10.0, "duration_s": 30.0,
            "title": key, "layout": "cams", "source_audio_s": seconds, "hook": "h",
            "score_a": score, "reason": "because"}


def _state(kind, score, hold=None, voices=None):
    fields = {"Score": score}
    if hold is not None:
        fields["Hold"] = hold
    if voices is not None:
        fields["Other voices"] = voices
    return {"kind": kind, "fields": fields, "published_at": PUB}


def _decision(score, hold=None, voices=None):
    return {"decision": "pending", "fields": _state("", score, hold, voices)["fields"]}


def arm(cfg, tmp_path):
    """Two streams and a costs file. kick-1 has every column; kick-2 is almost empty."""
    cfg["paths"]["streams"] = tmp_path / "streams"
    cfg["paths"]["streamers"] = tmp_path / "streamers.json"
    cfg["paths"]["archive"] = tmp_path / "archive"
    cfg["paths"]["people"] = tmp_path / "people.json"
    cfg["review"]["auto_approve"] = True
    root = tmp_path / "streams"
    sd = root / "kick-1"
    long = _segment("s01", "long", 0, 3600, 3600)
    exact = _segment("s02", "exact", 3600, 6600, 3000)  # 50 minutes on the nose: not over
    _write(sd / "segments.json", {"segments": [long, exact, _segment("s03", "short", 6600, 6700, 100)]})
    # segments.json is not a sidecar; the helper also dropped a stray mp4 beside it
    (sd / "segments.json").with_suffix(".mp4").unlink()
    _write(sd / "segments" / "s01_long.json", long)
    _write(sd / "clips" / "03_t12_pick.json",
           {"id": "t12", "in_s": 20.0, "out_s": 50.0, "title": "Pick", "reason": "because", "hook": "h"})
    for key, seconds, score in (("v01_t1", 0, 7), ("v02_t2", 1, 4), ("v03_t3", 22, 8), ("v04_t4", 22, 7)):
        _write(sd / "shorts" / f"{key}.json", _short(key, seconds, score))
    _write(sd / "compilations" / "angry.json",
           {"title": "Comp", "description": "A compilation.", "duration_s": 90, "topic": "angry",
            "segments": ["s01"]})
    (sd / "segments" / "s01_long.jpg").write_bytes(b"")  # one cover a check would look at
    state = {
        "v01_t1": _state("short", "7", "false", 0),
        "v02_t2": _state("short", "4", "false", 1),
        "v03_t3": _state("short", "8", "true", 22),   # Clippa's own other-voices hold
        "v04_t4": _state("short", "7", "true", 22),   # proposed held; the reviewer released it
        "s01_long": _state("segment", "5"),
    }
    decisions = {
        "v01_t1": _decision("7", "false", 0),
        "v02_t2": _decision("4", "false", 1),
        "v03_t3": _decision("8", "true", 22),
        "v04_t4": _decision("7", "false", 22),
        "s01_long": _decision("5"),
    }
    _write(sd / "review_state.json", state)
    (sd / "review_state.mp4").unlink()
    _write(sd / "review.json", decisions)
    (sd / "review.mp4").unlink()
    checks = {
        "v01_t1": CLEAN, "v04_t4": CLEAN,
        "v02_t2": {"sig": "s", "findings": [
            {"check": "picture", "level": "bad", "text": "no face in the cam"}]},
        "s01_long": {"sig": "old", "findings": [
            {"check": "text", "level": "warn", "text": "title says hosts"},
            {"check": "text", "level": "warn", "text": "no description"},  # same check and level: one
            {"check": "cover", "level": "bad", "text": "a face on the cover is cut off"}]},
    }
    _write(sd / "checks.json", checks)
    (sd / "checks.mp4").unlink()
    other = root / "kick-2"
    _write(other / "segments.json", {"segments": [_segment("s01", "bit", 0, 100, 100)]})
    (other / "segments.mp4").unlink()
    costs = tmp_path / "costs.jsonl"
    rows = [
        {"kind": "llm", "stage": "score", "stream": "kick-1", "usd": 0.2},
        {"kind": "llm", "stage": "segments", "stream": "kick-1", "usd": 0.8},
        {"kind": "llm", "stage": "score", "stream": "kick-2", "usd": 0.5},
        {"kind": "llm", "stage": "score", "stream": "other-arm", "usd": 9},
        {"kind": "llm", "stage": "meta", "stream": "kick-1", "usd": "nope"},
        "not json",
    ]
    costs.write_text("\n".join(json.dumps(r) if isinstance(r, dict) else r for r in rows) + "\n",
                     encoding="utf-8")
    return sd, other, costs


def _row(rep, stream):
    return next(r for r in rep["rows"] if r["stream"] == stream)


def test_each_column_on_the_fixture(defaults, tmp_path):
    sd, other, costs = arm(defaults, tmp_path)
    items = review.items(sd, review_load.INCLUDE, defaults)
    assert {it["kind"] for it in items} == {"short", "pick", "segment", "compilation"}
    assert sum(it["kind"] == "short" for it in items) == 4

    rep = review_load.collect(defaults, [str(sd), str(other)], costs, "fixture", now=NOW)
    row = _row(rep, "kick-1")
    assert row["arm"] == "fixture" and row["stream"] == "kick-1"
    assert row["config"] == str(defaults["_path"]) and row["costs_file"] == str(costs)
    assert row["items"] == {"short": 4, "pick": 1, "segment": 1, "compilation": 1}
    # two text warns on one item count once; picture bad is the other item
    assert row["findings"]["items"] == 2
    assert row["findings"]["by_check"] == {
        "text": {"bad": 0, "warn": 1}, "picture": {"bad": 1, "warn": 0},
        "cover": {"bad": 1, "warn": 0}, "subject": {"bad": 0, "warn": 0}}
    assert row["held_other_voices"] == 1          # v03; v04 was released
    assert row["other_voices_over_limit"] == 2
    assert "2 Shorts over the other-voices limit, 1 Short still held" in row["notes"]
    assert row["auto"]["would_approve"] == 2      # v01 and the released v04
    assert row["auto"]["waits"] == 5              # low score, the hold, the segment, the pick, the compilation
    assert row["auto"]["source"] == "published"
    assert row["auto"]["reasons"] == {
        "held for other voices": 1, "score 4 under 6": 1,
        "segments wait for a person ([review] auto_min_segment)": 1,
        # the pick and the compilation were never published, so they are judged from the cuts
        "picks wait for a person ([review] auto_min_pick)": 1,
        "compilations wait for a person ([review] auto_min_compilation)": 1}
    assert any("judged from the cuts" in n for n in row["notes"])
    assert row["vision"]["calls"] == 1 and row["vision"]["usd"] == pytest.approx(0.011)
    assert row["cost"]["usd"] == pytest.approx(1.0)
    assert row["cost"]["by_stage"] == {"score": pytest.approx(0.2), "segments": pytest.approx(0.8)}
    assert row["segments"]["longest_s"] == 3600
    assert row["segments"]["over_50min"] == 1     # 3600 counts; 3000 does not
    assert row["segments"]["n"] == 3

    quiet = _row(rep, "kick-2")
    assert quiet["items"] == {"short": 0, "pick": 0, "segment": 0, "compilation": 0}
    assert quiet["findings"]["items"] == 0 and quiet["held_other_voices"] == 0
    assert quiet["auto"]["would_approve"] == 0 and quiet["auto"]["waits"] == 0
    assert quiet["auto"]["source"] == "cuts (unpublished)" and quiet["vision"]["calls"] == 0
    assert quiet["cost"]["usd"] == pytest.approx(0.5)
    assert quiet["segments"] == {"longest_s": 100, "over_50min": 0, "n": 1, "over_s": 3000}
    assert any("no cut items" in n for n in quiet["notes"])

    total = rep["total"]
    assert total["stream"] == "total"
    assert total["items"]["short"] == 4 and total["items"]["compilation"] == 1
    assert total["findings"]["items"] == 2 and total["findings"]["by_check"]["cover"]["bad"] == 1
    assert total["held_other_voices"] == 1 and total["auto"]["would_approve"] == 2
    assert total["auto"]["waits"] == 5 and total["auto"]["source"] == "mixed"
    assert total["vision"]["calls"] == 1 and total["cost"]["usd"] == pytest.approx(1.5)
    assert total["segments"]["longest_s"] == 3600 and total["segments"]["over_50min"] == 1
    assert rep["read_only"] is True


def test_length_prefers_duration_and_counts_only_past_50_minutes(tmp_path):
    sd = tmp_path / "kick-x"
    sd.mkdir()
    jsonstate.write(sd / "segments.json", {"segments": [
        {"duration_s": 10, "start_s": 0, "end_s": 99999},  # duration_s wins
        {"start_s": 0, "end_s": 3001},
        {"duration_s": 3000},
        "not a segment",
    ]})
    notes = []
    got = review_load.segment_stats(sd, notes)
    assert got == {"longest_s": 3001, "over_50min": 1, "n": 3, "over_s": 3000}
    assert notes == []


def test_a_missing_segments_file_is_counted_from_the_mirror(defaults, tmp_path):
    sd = tmp_path / "streams" / "kick-9"
    sd.mkdir(parents=True)
    jsonstate.write(sd / "review_state.json", {
        "v01_t1": {"kind": "short"}, "s01_long": {"kind": "segment"}})
    defaults["paths"]["streams"] = sd.parent
    rep = review_load.collect(defaults, ["kick-9"], tmp_path / "missing.jsonl", "mirror", now=NOW)
    row = rep["rows"][0]
    assert row["items"] == {"short": 1, "pick": 0, "segment": 1, "compilation": 0}
    assert any("review_state.json" in n for n in row["notes"])
    assert row["cost"]["usd"] is None and any("no costs file" in n for n in row["notes"])
    assert row["auto"]["would_approve"] == 0 and row["auto"]["by_state"].get("off") == 2


def test_hold_off_and_auto_off_are_notes_not_approvals(defaults, tmp_path):
    sd, _, costs = arm(defaults, tmp_path)
    defaults["shorts"]["hold_source_audio"] = False
    defaults["review"]["auto_approve"] = False
    row = review_load.collect(defaults, [sd.name], costs, "off", now=NOW)["rows"][0]
    assert row["held_other_voices"] == 0 and row["other_voices_over_limit"] == 2
    assert any("hold_source_audio is off" in n for n in row["notes"])
    assert row["auto"]["would_approve"] == 0 and row["auto"]["waits"] == 0
    assert row["auto"]["by_state"]["off"] == 7  # the published five, plus the pick and compilation
    assert any("auto-approve is off" in n for n in row["notes"])


def test_the_report_writes_nothing_and_calls_no_model(defaults, tmp_path, monkeypatch):
    sd, other, costs = arm(defaults, tmp_path)

    def snapshot(root):
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in sorted(root.rglob("*")) if p.is_file()}

    before = snapshot(tmp_path)

    def boom(*_a, **_k):
        raise AssertionError("review load wrote a file")

    monkeypatch.setattr(jsonstate, "write", boom)
    monkeypatch.setattr(jsonstate, "write_text", boom)
    monkeypatch.setattr(jsonstate, "update", boom)

    class NoModel:
        def __init__(self, *_a, **_k):
            raise AssertionError("review load called a model")

    monkeypatch.setattr("clippa.llm.Claude", NoModel)
    review_load.collect(defaults, [str(sd), str(other)], costs, "fixture", now=NOW)
    assert snapshot(tmp_path) == before


def test_the_command_prints_a_table_and_json(defaults, tmp_path, capsys):
    sd, other, costs = arm(defaults, tmp_path)
    args = cli.build_parser().parse_args(
        ["review", "load", sd.name, other.name, "--arm", "fixture", "--costs", str(costs), "--json"])
    assert args.streams == [sd.name, other.name] and args.arm == "fixture" and args.json
    review_load.run(defaults, args, now=NOW)
    doc = json.loads(capsys.readouterr().out)
    assert [r["stream"] for r in doc["rows"]] == ["kick-1", "kick-2"]
    assert doc["rows"][0]["arm"] == "fixture" and doc["total"]["stream"] == "total"
    assert doc["rows"][0]["config"] and doc["read_only"] is True
    # two arms' rows are the same shape, so they concatenate
    assert set(doc["rows"][0]) == set(doc["rows"][1]) == set(doc["total"])

    args.json = False
    review_load.run(defaults, args, now=NOW)
    out = capsys.readouterr().out
    assert "read only: no files written" in out
    assert "kick-1" in out and "kick-2" in out and "total" in out
    assert "findings: text warn 1, picture bad 1, cover bad 1" in out
    assert "held for other voices (1)" in out
    assert "judged from the cuts" in out  # the pick and compilation were never published
    assert "cost by stage: score $0.20, segments $0.80" in out
    assert "1:00:00" in out  # the 3600 s segment


def test_the_config_flag_ignores_local_overrides(defaults, tmp_path, capsys, monkeypatch):
    _, _, costs = arm(defaults, tmp_path)
    path = tmp_path / "cfg7.toml"
    path.write_text('[paths]\nstreams = "streams"\n[review]\nauto_approve = true\n', encoding="utf-8")
    # This machine would turn auto-approve off and raise the floor past every Short. The arm file wins.
    (tmp_path / "clipper.local.toml").write_text(
        "[review]\nauto_approve = false\nauto_min_short = 99\n", encoding="utf-8")
    monkeypatch.setenv("CLIPPER_REVIEW_AUTO_APPROVE", "false")
    monkeypatch.setenv("CLIPPA_REVIEW_AUTO_MIN_SHORT", "99")
    args = cli.build_parser().parse_args(
        ["review", "load", "kick-1", "--config", str(path), "--costs", str(costs), "--json"])
    review_load.run(defaults, args, now=NOW)  # the passed cfg is replaced by cfg7.toml
    doc = json.loads(capsys.readouterr().out)
    assert doc["arm"] == "cfg7" and doc["config"] == str(path.resolve())
    assert doc["local_overrides"] is False
    assert doc["rows"][0]["stream"] == "kick-1" and doc["rows"][0]["items"]["short"] == 4
    assert doc["rows"][0]["auto"]["would_approve"] == 2  # floor stayed 6, auto stayed on
    args.json = False
    review_load.run(defaults, args, now=NOW)
    text = capsys.readouterr().out
    assert f"config {path.resolve()}: local overrides ignored" in text


def _cuts(cfg, directory: Path, meta: dict | None = None):
    """One stream of Shorts and no review_state.json: never published."""
    cfg["paths"]["streams"] = directory.parent
    cfg["paths"]["people"] = directory.parent / "people.json"
    cfg["review"]["auto_approve"] = True
    _write(directory / "segments.json", {"segments": [_segment("s01", "bit", 0, 600, 600)]})
    (directory / "segments.mp4").unlink()
    for key, seconds, score in (("v01_t1", 0, 7), ("v02_t2", 0, 4), ("v03_t3", 22, 8),
                                ("v04_t4", 0, 8), ("v05_t5", 0, 8), ("v06_t6", 0, 8)):
        _write(directory / "shorts" / f"{key}.json", _short(key, seconds, score))
    _write(directory / "checks.json", {
        "v01_t1": CLEAN, "v02_t2": CLEAN, "v03_t3": CLEAN,
        "v04_t4": {"sig": "s", "findings": [{"check": "text", "level": "warn", "text": "hosts"}]},
        "v06_t6": {"sig": "", "findings": [{"check": "run", "level": "bad", "text": "the checks didn't run"}]},
    })
    (directory / "checks.mp4").unlink()
    if meta:
        jsonstate.write(directory / "meta.json", meta)
    return directory


def test_unpublished_cuts_are_judged_without_a_grace_window(defaults, tmp_path):
    sd = _cuts(defaults, tmp_path / "streams" / "kick-c")
    row = review_load.collect(defaults, [str(sd)], None, "cfg7", now=NOW)["rows"][0]
    assert row["auto"]["source"] == "cuts (unpublished)"
    assert row["auto"]["would_approve"] == 1  # the clean Short; grace is not a wait
    assert row["auto"]["waits"] == 5
    assert row["auto"]["by_state"].get("waiting", 0) == 0
    assert row["auto"]["reasons"] == {
        "1 check finding(s)": 1,
        "held for other voices": 1,
        "its checks didn't run": 1,
        "not checked yet": 1,
        "score 4 under 6": 1,
    }
    assert any("from the cuts" in n and "no grace window" in n for n in row["notes"])
    assert "cuts (unpublished)" in " ".join(review_load.lines(
        review_load.collect(defaults, [str(sd)], None, "cfg7", now=NOW)))
    # one of them published: that one keeps the real verdict, the rest stay cuts
    jsonstate.write(sd / "review_state.json", {"v01_t1": _state("short", "7", "false", 0)})
    jsonstate.write(sd / "review.json", {"v01_t1": _decision("7", "false", 0)})
    mixed = review_load.collect(defaults, [str(sd)], None, "cfg7", now=NOW)["rows"][0]
    assert mixed["auto"]["source"] == "published"
    assert mixed["auto"]["would_approve"] == 1 and mixed["auto"]["waits"] == 5
    assert any("5 cut items not in review_state.json" in n for n in mixed["notes"])


def test_a_story_and_a_oneoff_wait_even_when_the_cuts_are_clean(defaults, tmp_path):
    root = tmp_path / "streams"
    stories = _cuts(defaults, root / "story", {"kind": "story", "started_at": "2026-11-22T00:00:00+00:00"})
    oneoff = _cuts(defaults, root / "oneoff", {"kind": "preshow", "window": {"from": 60},
                                               "started_at": "2026-11-22T00:00:00+00:00"})
    # _cuts points paths.streams at the directory's parent; point it at the shared root
    defaults["paths"]["streams"] = root
    story = review_load.collect(defaults, ["story"], None, "arm", now=NOW)["rows"][0]
    once = review_load.collect(defaults, ["oneoff"], None, "arm", now=NOW)["rows"][0]
    # the other-voice hold is decided before the story/one-off rule, same as review_auto.verdict
    assert story["auto"]["would_approve"] == 0 and story["auto"]["waits"] == 6
    assert story["auto"]["reasons"] == {"held for other voices": 1, "storys wait for a person": 5}
    assert once["auto"]["reasons"] == {"held for other voices": 1, "one-offs wait for a person": 5}
    assert stories.name == "story" and oneoff.name == "oneoff"


def test_split_over_s_sets_the_long_segment_limit(tmp_path):
    sd = tmp_path / "kick-l"
    sd.mkdir()
    jsonstate.write(sd / "segments.json", {"segments": [
        {"duration_s": 3000}, {"duration_s": 3001}, {"duration_s": 4000}]})
    bare = review_load.segment_stats(sd, [], {})
    assert bare["over_s"] == 3000 and bare["over_50min"] == 2  # 3000 s is not over 50 minutes
    wider = review_load.segment_stats(sd, [], {"segments": {"split_over_s": 4000}})
    assert wider["over_s"] == 4000 and wider["over_50min"] == 0
    tighter = review_load.segment_stats(sd, [], {"segments": {"split_over_s": "1000"}})
    assert tighter["over_s"] == 1000 and tighter["over_50min"] == 3
    bad = review_load.segment_stats(sd, [], {"segments": {"split_over_s": "nope"}})
    assert bad["over_s"] == 3000 and bad["over_50min"] == 2
    assert review_load.segment_stats(sd, [], {"segments": {"split_over_s": 0}})["over_s"] == 3000


def test_flags_and_a_missing_config(capsys):
    args = cli.build_parser().parse_args(
        ["--config", "global.toml", "review", "load", "kick-1", "kick-2",
         "--config", "arm.toml", "--costs", "c.jsonl", "--arm", "cfg7", "--json"])
    assert args.fn is cli.cmd_review_load
    assert args.config == "global.toml" and args.load_config == "arm.toml"
    assert args.streams == ["kick-1", "kick-2"] and args.costs == "c.jsonl" and args.arm == "cfg7"
    with pytest.raises(ClipperError, match="no config"):
        review_load.run(None, args)
