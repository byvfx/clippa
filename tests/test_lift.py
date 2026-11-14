"""#258 A: `clipper analytics --lift`, what predicts 7-day views."""
import json
from datetime import UTC, datetime, timedelta

import pytest

from clipper import analytics, cli, config, jsonstate, lift
from clipper.errors import ClipperError

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def row(key, views, show="kino", kind="segment", days=0, stream=None, topic="", score=None, heat=None,
        decided=None):
    published = T0 + timedelta(days=days)
    return {"key": key, "stream": stream or key.split("/")[0], "show": show, "kind": kind, "title": key,
            "views": views, "published": published, "decided_at": decided or published,
            "topic_name": topic, "score": score, "heat": heat}


def test_ranks_share_ties():
    assert lift.ranks([10, 20, 20, 5]) == [2, 3.5, 3.5, 1]


def test_spearman():
    assert lift.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == 1.0
    assert lift.spearman([1, 2, 3, 4, 5], [50, 40, 30, 20, 10]) == -1.0
    assert lift.spearman([1, 2, 3, 4], [1, 2, 3, 4]) is None        # too few to say
    assert lift.spearman([3, 3, 3, 3, 3], [1, 2, 3, 4, 5]) is None  # a ranker with no spread


def test_halves_rank_inside_each_stream():
    rows = [row(f"a/s0{i}", v, heat=h) for i, (h, v) in enumerate([(9, 10), (8, 20), (2, 300), (1, 400), (5, 7)])]
    rows += [row("b/s01", 5, heat=1), row("b/s02", 9, heat=2)]  # under MIN_HALVES: left out
    h = lift.halves(rows, "heat")
    # a by heat: 10, 20 | (7, the middle) | 300, 400; the hotter half lost
    assert h == {"streams": 1, "top": 30, "bottom": 700, "top_won": 0}


def test_by_value_medians_per_whole_score():
    rows = [row(f"a/v{i}", v, kind="short", score=s) for i, (s, v) in enumerate([(6, 10), (6, 30), (7, 900), (6.6, 50)])]
    assert lift.by_value(rows, "score") == [{"value": 6, "n": 2, "median": 20.0},
                                            {"value": 7, "n": 2, "median": 475.0}]


def test_topic_history_counts_only_what_was_known_when_the_stream_began():
    rows = [row("a/s01", 100, topic="DSP", days=0),
            row("b/s01", 300, topic="dsp ", days=5),            # same topic, other spelling
            row("c/s01", 50, topic="DSP", days=7, decided=T0 + timedelta(days=7)),
            row("d/s01", 9, topic="DSP", days=20, decided=T0 + timedelta(days=20)),
            row("e/s01", 9, topic="DSP", show="c404", days=30, decided=T0 + timedelta(days=30)),
            row("f/s01", 9, topic="", days=30, decided=T0 + timedelta(days=30))]
    lift.topic_history(rows)
    by = {r["key"]: r["topic"] for r in rows}
    assert by["a/s01"] is None and by["b/s01"] is None   # nothing 8 days old yet
    assert by["c/s01"] is None                            # a's 7 days end on day 8
    assert by["d/s01"] == 100                             # a, b and c known by day 20: median of 100, 300, 50
    assert by["e/s01"] is None                            # another show's record doesn't count
    assert by["f/s01"] is None


def test_topic_history_median_of_the_known():
    rows = [row("a/s01", 100, topic="DSP"), row("b/s01", 300, topic="DSP", days=1),
            row("c/s01", 50, topic="DSP", days=2), row("d/s01", 1, topic="DSP", days=20)]
    lift.topic_history(rows)
    assert rows[3]["topic"] == 100


def test_topic_records():
    rows = [row("a/s01", 100, topic="DSP"), row("b/s01", 300, topic="DSP", days=1),
            row("c/s01", 5, topic="Blade", days=2), row("d/s01", 7000, topic="DSP", days=30),
            row("e/s01", 1, topic="DSP", show="c404")]
    recs = lift.topic_records(rows, "kino", before=T0 + timedelta(days=12))
    assert recs == [{"topic": "DSP", "n": 2, "median": 200.0, "best": 300, "best_title": "b/s01"},
                    {"topic": "Blade", "n": 1, "median": 5, "best": 5, "best_title": "c/s01"}]
    assert lift.topic_records(rows, "kino")[0]["n"] == 3   # no cutoff: d counts too


def test_table_groups_and_rankers():
    rows = [row(f"a/v{i}", v, kind="short", score=s) for i, (s, v) in enumerate([(5, 1), (6, 2), (7, 3), (8, 4), (9, 5)])]
    rows += [row("x/s01", 4, show="c404"), row("y/s01", 8, show="c404", heat=3.0)]
    groups = lift.table(rows)
    shorts = next(g for g in groups if g["show"] == "kino" and g["kind"] == "short")
    assert shorts["uploads"] == 5 and shorts["rankers"][0]["ranker"] == "score"
    assert shorts["rankers"][0]["spearman"] == 1.0
    seg = next(g for g in groups if g["show"] == "c404")
    assert [r["ranker"] for r in seg["rankers"]] == ["heat"] and seg["rankers"][0]["n"] == 1
    assert {g["show"] for g in groups} == {"kino", "c404", "all"}
    assert [g["show"] for g in lift.table(rows, show="c404")] == ["c404"]
    assert {g["kind"] for g in lift.table(rows, kind="short")} == {"short"}
    md = lift.markdown(groups)
    assert "| kino | short | 5 |" in md and "+1.00" in md and "5 → 1 (n=1)" in md
    assert lift.markdown([]).startswith("No uploads")


@pytest.fixture
def share(tmp_path):
    """analytics.json and two stream folders, one with signals and a compilation."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    streams = tmp_path / "streams"
    (tmp_path / "clipper.toml").write_text(
        f'[paths]\nstreams = "{streams.as_posix()}"\nschedule = "{(tmp_path / "schedule.json").as_posix()}"\n',
        encoding="utf-8")
    a = streams / "kick-1"
    a.mkdir(parents=True)
    jsonstate.write(a / "meta.json", {"started_at": "2026-10-20T03:00:00Z"})
    jsonstate.write(a / "segments.json", {
        "segments": [{"id": "s01", "start_s": 0, "end_s": 10}, {"id": "s02", "start_s": 10, "end_s": 20}],
        "compilations": [{"topic": "DSP", "file": "dsp.mp4", "segments": ["s01", "s02"]}]})
    pq.write_table(pa.table({"heat": [1.0] * 10 + [3.0] * 10}), a / "signals.parquet")
    d7 = {"views": 0}
    jsonstate.write(tmp_path / "analytics.json", {
        "v1": {"key": "kick-1/s01_dsp", "kind": "segment", "show": "kino", "title": "S1", "topic": "DSP",
               "score": "", "publish_at": "2026-10-21T10:00:00-07:00", "d7": {**d7, "views": 40}},
        "v2": {"key": "kick-1/s02_dsp", "kind": "segment", "show": "kino", "title": "S2", "topic": "DSP",
               "score": "", "publish_at": "2026-10-21T12:00:00-07:00", "d7": {**d7, "views": 60}},
        "v3": {"key": "kick-1/comp-dsp", "kind": "compilation", "show": "kino", "title": "C", "topic": "",
               "score": "", "publish_at": "2026-10-22T23:00:00-07:00", "d7": {**d7, "views": 7}},
        "v4": {"key": "kick-1/v01_t03", "kind": "short", "show": "kino", "title": "V", "score": "7",
               "publish_at": "2026-10-21T09:00:00-07:00", "d7": {**d7, "views": 900}},
        "v5": {"key": "gone-2/s01_dsp", "kind": "segment", "show": "kino", "title": "Old", "topic": "DSP",
               "score": "", "publish_at": "2026-11-05T10:00:00", "d7": {**d7, "views": 3}},
        "v6": {"key": "kick-1/s03_x", "kind": "segment", "show": "kino", "title": "Too new", "topic": "DSP",
               "score": "", "publish_at": "2026-11-12T10:00:00-07:00"},              # no 7-day numbers yet
        "v7": {"key": "kick-1/s04_x", "kind": "segment", "show": "kino", "title": "Deleted", "missing": True,
               "score": "", "publish_at": "2026-10-21T10:00:00-07:00", "d7": {**d7, "views": 1}}})
    data = jsonstate.load(tmp_path / "analytics.json")
    for vid, r in data.items():  # what analytics.run always writes beside these
        r.update(url=f"https://youtu.be/{vid}", views=(r.get("d7") or {}).get("views", 0))
    jsonstate.write(tmp_path / "analytics.json", data)
    return config.load(tmp_path / "clipper.toml", use_local=False)


def test_rows_from_the_share(share):
    rows = {r["key"]: r for r in lift.rows(share)}
    assert set(rows) == {"kick-1/s01_dsp", "kick-1/s02_dsp", "kick-1/comp-dsp", "kick-1/v01_t03", "gone-2/s01_dsp"}
    assert rows["kick-1/s01_dsp"]["heat"] == 1.0 and rows["kick-1/s02_dsp"]["heat"] == 3.0
    assert rows["kick-1/comp-dsp"]["heat"] == 2.0                         # its segments' mean
    assert rows["kick-1/v01_t03"]["score"] == 7.0 and rows["kick-1/v01_t03"]["heat"] is None
    assert rows["kick-1/s01_dsp"]["decided_at"] == datetime(2026, 10, 20, 3, tzinfo=UTC)
    old = rows["gone-2/s01_dsp"]                    # a pruned stream: no heat, its publish time as the decision
    assert old["heat"] is None and old["decided_at"].tzinfo is not None
    assert old["topic"] == 50                        # kick-1's two DSP segments were known by 11-05
    assert lift.topic_record(share, "kino")[0] == {"topic": "DSP", "n": 3, "median": 40, "best": 60,
                                                   "best_title": "S2"}


def test_cli_lift(share, capsys):
    cli.cmd_analytics(share, cli.build_parser().parse_args(["analytics", "--lift", "--kind", "segment", "--json"]))
    groups = json.loads(capsys.readouterr().out)
    assert [g["kind"] for g in groups] == ["segment"] and groups[0]["uploads"] == 3
    args = cli.build_parser().parse_args(["--show", "c404", "analytics", "--lift"])
    assert args.show == "c404"                       # the global --show isn't overwritten by the sub-flag
    cli.cmd_analytics(share, cli.build_parser().parse_args(["analytics", "--lift", "--show", "kino"]))
    assert "| kino | short | 1 |" in capsys.readouterr().out
    with pytest.raises(ClipperError, match="go with --lift"):
        cli.cmd_analytics(share, cli.build_parser().parse_args(["analytics", "--json"]))


def test_report_ends_with_lift_only_when_asked(share):
    assert analytics.report(share, "2026-10")["lift"] == []
    rep = analytics.report(share, "2026-10", lift=True)
    assert rep["lift"] and "## What predicts views" in analytics.report_markdown(rep)
    assert "## What predicts views" not in analytics.report_markdown(analytics.report(share, "2026-10"))
