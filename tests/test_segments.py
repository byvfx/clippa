"""The segments stage (clippa/segments.py): the pure parts around the model call."""
import json

import pytest

from clippa import jsonstate, segments
from clippa.errors import ClipperError


def test_slug_is_a_safe_file_name():
    assert segments.slug("The Quartering: Jer's Big Day!") == "the-quartering-jer-s-big-day"
    assert len(segments.slug("x" * 80)) == 50 and segments.slug("  --  ") == ""


def test_apply_filters_says_why_a_segment_is_skipped():
    cfg = {"source_share": ["none", "some"], "skip_kinds": ["ad"], "min_s": 120, "max_s": 3600}
    segs = [{"standalone": True, "kind": "topic", "source_share": "none", "duration_s": 600},
            {"standalone": False, "kind": "ad", "source_share": "most", "duration_s": 30},
            {"standalone": True, "kind": "topic", "source_share": "some", "duration_s": 4000}]
    segments.apply_filters(segs, cfg)
    assert segs[0]["cut"] and segs[0]["skip_reasons"] == []
    assert not segs[1]["cut"]
    assert segs[1]["skip_reasons"] == ["not standalone", "kind ad", "source_share most", "0 min"]
    assert not segs[2]["cut"] and segs[2]["skip_reasons"] == ["67 min"]
    assert "resplit" not in segs[2]   # [segments] split_over_s off: the length drop, unchanged


def test_split_over_s_flags_a_long_segment_for_a_resplit():
    """0 (the default, and a missing key) keeps the length drop. Set, a segment longer than
    it is flagged for a re-split instead, including one max_s would still have cut."""
    base = {"source_share": ["none", "some"], "skip_kinds": ["ad"], "min_s": 120, "max_s": 3600}
    long = {"standalone": True, "kind": "topic", "source_share": "none", "duration_s": 71 * 60,
            "resplit": True}
    segments.apply_filters([long], {**base, "split_over_s": 0})
    assert not long["cut"] and long["skip_reasons"] == ["71 min"] and "resplit" not in long
    kept = {"standalone": True, "kind": "topic", "source_share": "none", "duration_s": 55 * 60}
    over = {"standalone": True, "kind": "topic", "source_share": "none", "duration_s": 71 * 60}
    short = {"standalone": True, "kind": "topic", "source_share": "none", "duration_s": 600}
    ad = {"standalone": True, "kind": "ad", "source_share": "none", "duration_s": 71 * 60}
    segs = [kept, over, short, ad]
    segments.apply_filters(segs, {**base, "split_over_s": 50 * 60})
    assert not kept["cut"] and kept["resplit"] is True and kept["skip_reasons"] == ["re-split"]
    assert not over["cut"] and over["resplit"] is True and over["skip_reasons"] == ["re-split"]
    assert short["cut"] and short["skip_reasons"] == [] and "resplit" not in short
    assert not ad["cut"] and ad["skip_reasons"] == ["kind ad", "re-split"] and ad["resplit"] is True
    exact = {"standalone": True, "kind": "topic", "source_share": "none", "duration_s": 50 * 60}
    segments.apply_filters([exact], {**base, "split_over_s": 50 * 60})
    assert exact["cut"] and "resplit" not in exact   # longer than the limit, not equal to it


def test_negative_split_over_s_is_off(capsys):
    """Below 0 is off, the same as 0: min_s/max_s still decide the cut, nothing is re-split."""
    cfg = {"source_share": ["none", "some"], "skip_kinds": ["ad"], "min_s": 120, "max_s": 3600,
           "split_over_s": -1}
    in_range = {"id": "s01", "standalone": True, "kind": "topic", "source_share": "none",
                "duration_s": 600}
    too_long = {"id": "s02", "standalone": True, "kind": "topic", "source_share": "none",
                "duration_s": 71 * 60}
    too_short = {"id": "s03", "standalone": True, "kind": "topic", "source_share": "none",
                 "duration_s": 30}
    segs = [in_range, too_long, too_short]
    segments.apply_filters(segs, cfg)
    assert in_range["cut"] and in_range["skip_reasons"] == [] and "resplit" not in in_range
    assert not too_long["cut"] and too_long["skip_reasons"] == ["71 min"] and "resplit" not in too_long
    assert not too_short["cut"] and too_short["skip_reasons"] == ["0 min"] and "resplit" not in too_short
    segments.report_resplits(segs, segments._split_limit(cfg))
    assert "RESPLIT" not in capsys.readouterr().out


def test_text_split_over_s_is_a_clear_error():
    """A value float() cannot read is a ClipperError naming the setting, not a bare TypeError."""
    cfg = {"source_share": ["none"], "skip_kinds": [], "min_s": 120, "max_s": 3600,
           "split_over_s": "fifty"}
    segs = [{"standalone": True, "kind": "topic", "source_share": "none", "duration_s": 600}]
    with pytest.raises(ClipperError, match=r"\[segments\] split_over_s") as raised:
        segments.apply_filters(segs, cfg)
    assert "fifty" in str(raised.value)


def _words(*spans: tuple[float, float]) -> str:
    return "\n".join(json.dumps({"word": "w", "start": a, "end": b, "seg": i})
                     for i, (a, b) in enumerate(spans)) + "\n"


def test_a_71_minute_model_segment_is_flagged_not_dropped(defaults, tmp_path, monkeypatch, capsys):
    """Saved model output: one 71-minute segment and one ordinary one. With split_over_s at
    50 minutes the long one stays in segments.json, flagged, and the stage log says so."""
    sdir = tmp_path / "kick-1"
    sdir.mkdir()
    long_end = 71 * 60.0
    (sdir / "words.jsonl").write_text(
        _words((0.0, 1.0), (long_end - 1, long_end), (long_end, long_end + 1),
               (long_end + 599, long_end + 600)), encoding="utf-8")
    body = {"title": "A topic", "figures": [], "kind": "commentary", "source_share": "none",
            "standalone": True, "summary": "what happened", "description": "the whole thing"}
    model_out = {"segments": [
        {**body, "start_s": 0, "end_s": long_end, "topic": "drama", "title": "The long one"},
        {**body, "start_s": long_end, "end_s": long_end + 600, "topic": "other", "title": "The short one"},
    ]}

    class Fake:
        cost = 0.0
        calls = 0

        def json_call(self, model, system, user, schema, effort, fallback=False, stream=False):
            self.calls += 1
            assert schema["required"] == ["segments"]
            return model_out, {}

        def preflight(self, model):
            return None

    fake = Fake()
    monkeypatch.setattr(segments, "Claude", lambda cfg: fake)
    defaults["segments"]["split_over_s"] = 50 * 60
    segs = segments.run(sdir, defaults, force=True)
    assert fake.calls == 1   # the long one is not cut, so no compilation call
    assert segs[0]["duration_s"] == long_end and segs[0]["id"] == "s01"
    assert not segs[0]["cut"] and segs[0]["resplit"] is True
    assert segs[0]["skip_reasons"] == ["re-split"]
    assert segs[1]["cut"] and "resplit" not in segs[1]
    saved = jsonstate.load(sdir / "segments.json")["segments"]
    assert saved[0]["resplit"] is True and saved[1]["cut"]
    out = capsys.readouterr().out
    assert "RESPLIT s01 is 71 min, over [segments] split_over_s (50 min): flagged for a re-split" in out
    assert "re-split" in out
