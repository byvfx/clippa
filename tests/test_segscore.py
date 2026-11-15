"""#258 B: the segment score (clipper/segscore.py) and its backtest."""
import json
from datetime import UTC, datetime, timedelta

import pytest

from clipper import cli, config, jsonstate, lift, segments, segscore
from clipper.errors import ClipperError

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def row(key, views, topic, days, show="kino", kind="segment"):
    return {"key": key, "stream": key.split("/")[0], "show": show, "kind": kind, "title": f"KINO | {key}",
            "views": views, "published": T0 + timedelta(days=days), "decided_at": T0 + timedelta(days=days),
            "topic_name": topic}


def seg(i, topic="DSP", cut=True, **kw):
    return {"id": f"s{i:02d}", "start_s": i * 600.0, "end_s": i * 600.0 + 600, "duration_s": 600.0,
            "title": f"Title {i}", "topic": topic, "figures": ["DSP"], "kind": "reaction",
            "source_share": "some", "standalone": True, "summary": f"Summary {i}", "description": f"About {i}", "cut": cut, **kw}


class FakeClaude:
    """Scores every segment it's sent 11 - its number, says why, and keeps what it was sent."""
    def __init__(self, leave_out=()):
        self.cost, self.calls, self.leave_out = 0.0, [], set(leave_out)

    def json_call(self, model, system, user, schema, effort, fallback=False, stream=False):
        if "scores" not in schema["properties"]:  # the compilation's title call
            return {"title": "T", "description": "D"}, {}
        self.calls.append({"model": model, "system": system, "user": user})
        self.cost += 0.05
        ids = [line.split('"')[1] for line in user.splitlines() if line.startswith("<segment id=")]
        return {"scores": [{"id": i, "score": 11 - int(i[1:]) if i != "s01" else 42, "why": f" because {i} "}
                           for i in ids if i not in self.leave_out]}, {}

    def preflight(self, model):
        pass


def test_record_block_holds_only_what_was_known_then():
    rows = [row("a/s01", 100, "DSP", 0), row("b/s01", 900, "Ethan Klein", 2), row("c/s01", 7, "DSP", 5),
            row("d/s01", 5000, "Ethan Klein", 20), row("e/s01", 1, "Blade", 0, show="c404"),
            row("f/v01", 999, "", 0, kind="short")]
    text = segscore.record_block(rows, "kino", T0 + timedelta(days=14))
    assert 'as_of="2026-10-15"' in text and "3 uploads, median 100 views" in text
    assert "- Ethan Klein: 1, median 900, best 900 (KINO | b/s01)" in text
    assert "- DSP: 2, median 53.5" in text
    assert "5000" not in text and "Blade" not in text and "999" not in text   # later, other show, a Short
    assert "first upload: median 500 views (2 topics)" in text
    empty = segscore.record_block(rows, "kino", T0)
    assert "No segment upload of this show has 7-day numbers yet" in empty


def test_segment_block_and_transcript():
    words = [{"start": 600.0, "end": 601.0, "word": "hello", "seg": 0}]
    text = segscore.segment_block([seg(1)], None)
    assert text.startswith('<segment id="s01" starts="0:10:00" minutes="10" kind="reaction" others_video="some"')
    assert "title: Title 1" in text and "summary: Summary 1" in text and "transcript:" not in text
    assert "transcript:" in segscore.segment_block([seg(1)], words)


def test_checked_clamps_and_drops_what_wasnt_asked():
    out = {"scores": [{"id": "s01", "score": 42, "why": " a "}, {"id": "s02", "score": -3, "why": "b"},
                      {"id": "s99", "score": 5, "why": "not asked"}]}
    assert segscore.checked(out, ["s01", "s02", "s03"]) == {"s01": {"score": 10, "why": "a"},
                                                           "s02": {"score": 1, "why": "b"}}


@pytest.fixture
def share(tmp_path):
    """Two Kino streams: kick-1 (10-01) whose DSP segment did well, kick-2 (10-20) to score."""
    streams = tmp_path / "streams"
    (tmp_path / "clipper.toml").write_text(
        f'[paths]\nstreams = "{streams.as_posix()}"\nschedule = "{(tmp_path / "schedule.json").as_posix()}"\n'
        '[shows.kino]\nchannels = ["kinocasino"]\n', encoding="utf-8")
    for sid, started, segs in [("kick-1", "2026-10-01T03:00:00Z", [seg(1)]),
                               ("kick-2", "2026-10-20T03:00:00Z", [seg(1), seg(2, "Blade"), seg(3, cut=False, kind="break")])]:
        (streams / sid).mkdir(parents=True)
        jsonstate.write(streams / sid / "meta.json", {"channel": "kinocasino", "started_at": started})
        jsonstate.write(streams / sid / "segments.json", {"segments": segs, "compilations": [], "gaps": []})
    d7 = {"views": 0}
    jsonstate.write(tmp_path / "analytics.json", {
        "v1": {"key": "kick-1/s01_dsp", "kind": "segment", "show": "kino", "title": "DSP did well", "topic": "DSP",
               "score": "", "url": "u", "views": 800, "publish_at": "2026-10-02T10:00:00Z", "d7": {**d7, "views": 800}},
        "v2": {"key": "kick-2/s01_dsp", "kind": "segment", "show": "kino", "title": "A", "topic": "DSP",
               "score": "", "url": "u", "views": 50, "publish_at": "2026-10-21T10:00:00Z", "d7": {**d7, "views": 50}},
        "v3": {"key": "kick-2/s02_blade", "kind": "segment", "show": "kino", "title": "B", "topic": "Blade",
               "score": "", "url": "u", "views": 5, "publish_at": "2026-10-21T12:00:00Z", "d7": {**d7, "views": 5}}})
    return config.load(tmp_path / "clipper.toml", use_local=False)


def test_backtest_scores_as_of_each_streams_start(share, monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(segscore, "Claude", lambda cfg: fake)
    done = segscore.backtest(share)
    assert done == {"kick-1": 1, "kick-2": 2}
    first, second = fake.calls
    assert "No segment upload of this show has 7-day numbers yet" in first["user"]   # kick-1: nothing known on 10-01
    assert "- DSP: 1, median 800" in second["user"]                                 # kick-2: kick-1's was in by 10-20
    assert 's03' not in second["user"]                                               # not cut: never an upload
    assert second["model"] == "claude-opus-5-5" and "Score every segment" in second["system"]
    bt = jsonstate.load(share["paths"]["streams"] / "kick-2" / segscore.BACKTEST)
    assert bt["summary"]["scores"] == {"s01": {"score": 10, "why": "because s01"}, "s02": {"score": 9, "why": "because s02"}}
    assert bt["summary"]["as_of"] == "2026-10-20T03:00:00+00:00" and bt["summary"]["prompt_version"] == "v1"
    assert "score" not in jsonstate.load(share["paths"]["streams"] / "kick-2" / "segments.json")["segments"][0]
    # done once: a second run calls nothing, --force scores again
    assert segscore.backtest(share) == {} and len(fake.calls) == 2
    assert segscore.backtest(share, stream="kick-2", force=True) == {"kick-2": 2} and len(fake.calls) == 3
    with pytest.raises(ClipperError, match="no uploaded segment"):
        segscore.backtest(share, stream="kick-9")
    # lift reads the backtest as the bt ranker, segments only
    rows = {r["key"]: r for r in lift.rows(share)}
    assert rows["kick-2/s01_dsp"]["bt"] == 10 and rows["kick-2/s02_blade"]["bt"] == 9
    assert rows["kick-2/s01_dsp"]["bt_tx"] is None and rows["kick-2/s01_dsp"]["model"] is None


def test_backtest_transcript_needs_words_and_dry_run_calls_nothing(share, monkeypatch, capsys):
    monkeypatch.setattr(segscore, "Claude", lambda cfg: pytest.fail("no call on a dry run"))
    segscore.backtest(share, dry_run=True)
    out = capsys.readouterr().out
    assert "estimate, summary variant, 2 streams" in out and "score kick-2" in out
    segscore.backtest(share, transcript=True, dry_run=True)
    assert "no words.jsonl, skipped" in capsys.readouterr().out


def test_a_segment_left_out_twice_stays_unscored(share, monkeypatch, capsys):
    fake = FakeClaude(leave_out={"s02"})
    monkeypatch.setattr(segscore, "Claude", lambda cfg: fake)
    assert segscore.backtest(share, stream="kick-2") == {"kick-2": 1}
    assert len(fake.calls) == 2 and "no score for s02 (left out twice)" in capsys.readouterr().out
    bt = jsonstate.load(share["paths"]["streams"] / "kick-2" / segscore.BACKTEST)
    assert list(bt["summary"]["scores"]) == ["s01"]
    rows = {r["key"]: r for r in lift.rows(share)}
    assert rows["kick-2/s02_blade"]["bt"] is None


def test_a_second_ask_that_scores_everything_is_kept(share, monkeypatch):
    fake = FakeClaude(leave_out={"s02"})
    real = fake.json_call

    def once(*a, **kw):
        out = real(*a, **kw)
        fake.leave_out = set()
        return out
    fake.json_call = once
    monkeypatch.setattr(segscore, "Claude", lambda cfg: fake)
    assert segscore.backtest(share, stream="kick-2") == {"kick-2": 2}


def test_segments_score_writes_segments_json(share, monkeypatch, capsys):
    fake = FakeClaude()
    monkeypatch.setattr(segments, "Claude", lambda cfg: fake)
    sdir = share["paths"]["streams"] / "kick-2"
    segments.run(sdir, share, score_now=True)
    data = jsonstate.load(sdir / "segments.json")
    assert [s.get("score") for s in data["segments"]] == [10, 9, None]
    assert data["segments"][1]["score_why"] == "because s02"
    assert data["score"]["model"] == "claude-opus-5-5" and data["score"]["transcript"] is False
    assert "- DSP: 1, median 800" in fake.calls[0]["user"]       # live: everything known now
    assert "[score 9: because s02]" in capsys.readouterr().out
    # [segments] score off and nothing forced: a rerun scores nothing more
    segments.run(sdir, share)
    assert len(fake.calls) == 1
    # with it on, a stream already covered isn't scored again
    share["segments"]["score"] = True
    segments.run(sdir, share)
    assert len(fake.calls) == 1
    rows = {r["key"]: r for r in lift.rows(share)}
    assert rows["kick-2/s02_blade"]["model"] == 9


def test_a_segment_passed_over_isnt_paid_for_on_every_rerun(share, monkeypatch):
    fake = FakeClaude(leave_out={"s02"})
    monkeypatch.setattr(segments, "Claude", lambda cfg: fake)
    sdir = share["paths"]["streams"] / "kick-2"
    share["segments"]["score"] = True
    segments.run(sdir, share)
    data = jsonstate.load(sdir / "segments.json")
    assert data["score"]["ids"] == ["s01", "s02"] and data["score"]["left_out"] == ["s02"]
    assert len(fake.calls) == 2                      # the one retry
    segments.run(sdir, share)
    assert len(fake.calls) == 2                      # covered: no new call
    segments.run(sdir, share, score_now=True)
    assert len(fake.calls) == 4                      # --score asks again


def test_a_worse_second_answer_doesnt_replace_the_first(share, monkeypatch):
    fake = FakeClaude(leave_out={"s02"})
    real = fake.json_call

    def worse(*a, **kw):
        out = real(*a, **kw)
        fake.leave_out = {"s01", "s02"}
        return out
    fake.json_call = worse
    monkeypatch.setattr(segscore, "Claude", lambda cfg: fake)
    segscore.backtest(share, stream="kick-2")
    bt = jsonstate.load(share["paths"]["streams"] / "kick-2" / segscore.BACKTEST)
    assert list(bt["summary"]["scores"]) == ["s01"]


def test_a_dry_run_leaves_a_broken_backtest_file_alone(share, capsys):
    path = share["paths"]["streams"] / "kick-2" / segscore.BACKTEST
    path.write_text("{nope", encoding="utf-8")
    segscore.backtest(share, stream="kick-2", dry_run=True)
    assert path.read_text(encoding="utf-8") == "{nope"


def test_a_refused_retry_keeps_the_first_answer(share, monkeypatch):
    fake = FakeClaude(leave_out={"s02"})
    real = fake.json_call
    calls = []

    def refuse_second(*a, **kw):
        calls.append(1)
        return real(*a, **kw) if len(calls) == 1 else (None, {"refusal": "no"})
    fake.json_call = refuse_second
    monkeypatch.setattr(segscore, "Claude", lambda cfg: fake)
    assert segscore.backtest(share, stream="kick-2") == {"kick-2": 1}


def test_the_backtest_uses_each_shows_own_config(share, monkeypatch):
    seen = []
    monkeypatch.setattr(segscore, "Claude", lambda cfg: seen.append(cfg["show"]["key"]) or FakeClaude())
    segscore.backtest(share)
    assert seen == ["kino"]          # one client per show, from the show's config, not the base


def test_a_story_is_never_scored(share, monkeypatch, capsys):
    sdir = share["paths"]["streams"] / "kick-2"
    meta = jsonstate.load(sdir / "meta.json")
    jsonstate.write(sdir / "meta.json", {**meta, "kind": "story"})
    monkeypatch.setattr(segments, "Claude", lambda cfg: FakeClaude())
    segments.run(sdir, share, score_now=True)
    assert "a story is one whole video" in capsys.readouterr().out
    assert "score" not in jsonstate.load(sdir / "segments.json")["segments"][0]


def test_a_broken_backtest_file_is_scored_again(share, monkeypatch, capsys):
    (share["paths"]["streams"] / "kick-2" / segscore.BACKTEST).write_text("{nope", encoding="utf-8")
    monkeypatch.setattr(segscore, "Claude", lambda cfg: FakeClaude())
    assert segscore.backtest(share, stream="kick-2") == {"kick-2": 2}
    assert "unreadable" in capsys.readouterr().out


def test_segments_dry_run_prices_the_score(share, monkeypatch, capsys):
    monkeypatch.setattr(segments, "Claude", lambda cfg: pytest.fail("no call on a dry run"))
    sdir = share["paths"]["streams"] / "kick-2"
    segments.run(sdir, share, dry_run=True, score_now=True)
    out = capsys.readouterr().out
    assert "segments.json is kept" in out and "score kick-2" in out and "total" in out
    (sdir / "words.jsonl").write_text(json.dumps({"word": "hi", "start": 0.0, "end": 1.0, "seg": 0}) + "\n",
                                      encoding="utf-8")                    # a re-split prices the transcript
    segments.run(sdir, share, dry_run=True, force=True, score_now=True)
    assert "plus one segment score call after the split" in capsys.readouterr().out
    segments.run(sdir, share, dry_run=True)
    assert "score kick-2" not in capsys.readouterr().out    # [segments] score off, no --score


def test_cli_flags(share, monkeypatch):
    calls = []
    monkeypatch.setattr(segscore, "backtest", lambda cfg, **kw: calls.append(kw))
    cli.cmd_segments(share, cli.build_parser().parse_args(["segments", "--backtest", "--transcript", "--dry-run"]))
    assert calls == [{"stream": "", "transcript": True, "force": False, "dry_run": True}]
    for argv, match in [(["segments"], "name a stream"),
                        (["segments", "kick-2", "--transcript"], "goes with --backtest"),
                        (["segments", "--backtest", "--score"], "past ones")]:
        with pytest.raises(ClipperError, match=match):
            cli.cmd_segments(share, cli.build_parser().parse_args(argv))


def test_the_prompt_fills(share):
    from clipper.prompts import Prompts

    text = Prompts(config.for_show(share, "kino")).get("segment_score")
    assert "Score every segment" in text and "{" not in text
    assert json.dumps(segscore.SCHEMA)  # plain JSON, as the API takes it
