"""#258 C: the scheduler ranks a stream's segments by their model score, heat breaking ties."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clippa import jsonstate, review, schedule


def seg(i, score=None, cut=True):
    s = {"id": f"s{i:02d}", "start_s": i * 100.0, "end_s": i * 100.0 + 100, "cut": cut, "topic": "T",
         "title": f"T{i}", "description": "d", "source_share": "some", "file": f"s{i:02d}_t.mp4"}
    return {**s, "score": score} if score is not None else s


def test_heat_alone_without_a_score():
    heat = {"s01": 1.0, "s02": 3.0}
    assert schedule.segment_rank({"segments": [seg(1), seg(2)]}, heat) == (heat, False)


def test_score_first_heat_breaks_ties():
    data = {"segments": [seg(1, 7), seg(2, 7), seg(3, 9), seg(4, 2), seg(5, cut=False)]}
    rank, by_score = schedule.segment_rank(data, {"s01": 5.0, "s02": 1.0, "s03": 0.0, "s04": 99.0})
    assert by_score and set(rank) == {"s01", "s02", "s03", "s04"}       # cut ones only
    assert sorted(rank, key=rank.get, reverse=True) == ["s03", "s01", "s02", "s04"]
    assert rank["s04"] < 3                                              # all the heat never lifts a whole point
    assert 7 < rank["s01"] < 7.5 and rank["s02"] > 7


def test_one_ranking_never_mixes_score_and_heat():
    data = {"segments": [seg(1, 7), seg(2)]}                            # s02 cut after the score ran
    assert schedule.segment_rank(data, {"s01": 1.0, "s02": 9.0})[1] is False


def test_a_segment_the_model_passed_over_gets_the_median():
    data = {"segments": [seg(1, 8), seg(2), seg(3, 2), seg(4, 6)],
            "score": {"ids": ["s01", "s02", "s03", "s04"], "left_out": ["s02"]}}
    rank, by_score = schedule.segment_rank(data, {})
    assert by_score and int(rank["s02"]) == 6


@pytest.fixture
def stream(defaults, tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    defaults["paths"]["streams"] = tmp_path
    defaults["schedule"]["stream_budget"] = {"segment": 10, "compilation": 1}   # ranking needs a budget
    sdir = tmp_path / "kick-1"
    (sdir / "segments").mkdir(parents=True)
    started = datetime.now(UTC) - timedelta(hours=10)
    jsonstate.write(sdir / "meta.json", {"channel": "x", "started_at": started.isoformat(), "duration_s": 3 * 3600})
    segs = [seg(1, 3), seg(2, 9), seg(3, 9)]
    jsonstate.write(sdir / "segments.json", {"segments": segs, "compilations": [
        {"topic": "T", "file": "t.mp4", "segments": ["s01", "s02"], "title": "C"}]})
    pq.write_table(pa.table({"heat": [9.0] * 200 + [1.0] * 100 + [5.0] * 300}), sdir / "signals.parquet")
    jsonstate.write(sdir / "review.json", {
        s["file"].removesuffix(".mp4"): {"kind": "segment", "id": s["id"], "file": f"segments/{s['file']}",
                                         "decision": "approved", "fields": {}} for s in segs})
    jsonstate.write(sdir / "review_state.json", {s["file"].removesuffix(".mp4"): {"entity_id": f"e{s['id']}"}
                                                 for s in segs})
    (sdir / "compilations").mkdir()
    (sdir / "compilations" / "t.mp4").write_bytes(b"")
    return defaults, sdir


def test_candidates_rank_by_the_score(stream):
    cfg, sdir = stream
    items, _ = schedule.candidates(cfg, sdir.parent, cfg["schedule"], ZoneInfo("UTC"))
    score = {it["key"].split("/")[1]: it["score"] for it in items}
    # s01 has the most chat (9.0) but the lowest score; s03 out-heats s02 at the same 9
    assert score["s03_t"] > score["s02_t"] > score["s01_t"]
    assert int(score["s01_t"]) == 3
    assert score["comp-t"] == score["s02_t"]                 # a compilation: its best segment's


def test_a_compilation_with_its_own_kitsu_item_takes_its_best_segment(stream):
    cfg, sdir = stream
    rv = jsonstate.load(sdir / "review.json")
    rv["comp-t"] = {"kind": "compilation", "id": "T", "file": "compilations/t.mp4", "decision": "approved",
                    "fields": {}}
    jsonstate.write(sdir / "review.json", rv)
    st = jsonstate.load(sdir / "review_state.json")
    st["comp-t"] = {"entity_id": "ec"}
    jsonstate.write(sdir / "review_state.json", st)
    items, _ = schedule.candidates(cfg, sdir.parent, cfg["schedule"], ZoneInfo("UTC"))
    score = {it["key"].split("/")[1]: it["score"] for it in items}
    assert score["comp-t"] == score["s02_t"] and [k for k in score if k == "comp-t"] == ["comp-t"]


def test_candidates_keep_heat_without_scores(stream):
    cfg, sdir = stream
    data = jsonstate.load(sdir / "segments.json")
    for s in data["segments"]:
        s.pop("score")
    jsonstate.write(sdir / "segments.json", data)
    items, _ = schedule.candidates(cfg, sdir.parent, cfg["schedule"], ZoneInfo("UTC"))
    score = {it["key"].split("/")[1]: it["score"] for it in items}
    assert score["s01_t"] == 9.0 and score["s02_t"] == 1.0 and score["comp-t"] == 5.0   # mean heat, as before


def test_kitsu_shows_the_score_from_segments_json(stream):
    cfg, sdir = stream
    for s in jsonstate.load(sdir / "segments.json")["segments"]:
        side = {k: v for k, v in s.items() if k != "score"}           # cut before the score ran
        jsonstate.write(sdir / "segments" / s["file"].replace(".mp4", ".json"), {**side, "end_s": s["end_s"]})
        (sdir / "segments" / s["file"]).write_bytes(b"")              # items only lists cut videos
    got = {it["key"]: it["fields"].get("Score") for it in review.items(sdir, ["segments"])}
    assert got == {"s01_t": 3, "s02_t": 9, "s03_t": 9}


def test_each_item_says_why_it_was_picked(stream):
    """#324: the card's one line. A segment's score_why (segments.json's, newer than its sidecar's),
    a pick's or a Short's Pass B reason; nothing when there is none."""
    cfg, sdir = stream
    data = jsonstate.load(sdir / "segments.json")
    data["segments"][1]["score_why"] = "the strongest take"
    jsonstate.write(sdir / "segments.json", data)
    for s in data["segments"]:
        jsonstate.write(sdir / "segments" / s["file"].replace(".mp4", ".json"), {**s, "score_why": "old"})
        (sdir / "segments" / s["file"]).write_bytes(b"")
    (sdir / "clips").mkdir()
    jsonstate.write(sdir / "clips" / "01_t20_x.json", {"id": "t20", "title": "T", "hook": "h", "reason": "Don's own joke",
                                                       "in_s": 150.0, "out_s": 180.0, "score_a": 7})
    (sdir / "clips" / "01_t20_x.mp4").write_bytes(b"")
    got = {it["key"]: it["why"] for it in review.items(sdir, ["segments", "picks"])}
    assert got == {"s01_t": "old", "s02_t": "the strongest take", "s03_t": "old", "c01_t20": "Don's own joke"}
