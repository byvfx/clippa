"""`clippa streams list|prune` (#204): sizes per stream, and removing the source and audio of
streams that are done with, never one that still needs them."""
from datetime import UTC, datetime, timedelta

import pytest

from clippa import jsonstate, media, streams
from clippa.errors import ClipperError


@pytest.fixture
def cfg(defaults, tmp_path):
    defaults["paths"]["streams"] = tmp_path / "streams"
    defaults["paths"]["schedule"] = tmp_path / "schedule.json"
    defaults["paths"]["streams"].mkdir()
    defaults["schedule"]["stale_days"] = 3
    jsonstate.write(defaults["paths"]["schedule"], {"entries": []})
    return defaults


def make(cfg, sid: str, days_old: float, decisions: dict, files=("source.mkv", "audio.wav")) -> object:
    d = cfg["paths"]["streams"] / sid
    (d / "segments").mkdir(parents=True)
    (d / "review").mkdir()
    started = (datetime.now(UTC) - timedelta(days=days_old)).isoformat(timespec="seconds")
    jsonstate.write(d / "meta.json", {"started_at": started, "channel": "kinocasinogaming"})
    jsonstate.write(d / "review_state.json", {k: {"kind": "segment", "file": f"segments/{k}.mp4"} for k in decisions})
    jsonstate.write(d / "review.json", decisions)
    jsonstate.write(d / "transcript.json", {"segments": []})
    for f in files:
        (d / f).write_bytes(b"x" * 1000)
    for k in decisions:
        (d / "segments" / f"{k}.mp4").write_bytes(b"x" * 500)
        (d / "review" / f"{k}.mp4").write_bytes(b"x" * 100)
    return d


def schedule(cfg, *entries):
    jsonstate.write(cfg["paths"]["schedule"], {"entries": [
        {"key": key, "status": status, "publish_at": "2026-11-01T10:00:00+00:00"} for key, status in entries]})


def test_sizes_split_the_folder_into_parts(cfg):
    d = make(cfg, "kick-1", 40, {"s01": {"decision": "approved"}})
    z = streams.sizes(d)
    assert z["source"] == 1000 and z["audio"] == 1000 and z["cuts"] == 500 and z["previews"] == 100
    assert z["other"] > 0   # the JSON


def test_prune_only_says_without_yes_then_removes_and_keeps_the_json(cfg):
    d = make(cfg, "kick-1", 40, {"s01": {"decision": "approved"}, "s02": {"decision": "rejected"}})
    schedule(cfg, ("kick-1/s01", "uploaded"))
    out = streams.prune(cfg)
    assert "would remove" in out[0] and (d / "source.mkv").exists()
    out = streams.prune(cfg, yes=True)
    assert "removed" in out[0] and not (d / "source.mkv").exists() and not (d / "audio.wav").exists()
    assert (d / "transcript.json").exists() and (d / "review.json").exists() and (d / "segments" / "s01.mp4").exists()
    assert jsonstate.load(d / streams.PRUNED)["removed"] == ["audio.wav", "source.mkv"]
    with pytest.raises(ClipperError, match="clippa streams prune"):
        media.source_file(d)


def test_prune_leaves_alone_what_still_needs_its_source(cfg):
    cases = {
        "kick-young": (5, {"s01": {"decision": "approved"}}),
        "kick-pending": (40, {"s01": {"decision": "pending"}}),
        "kick-changes": (40, {"s01": {"decision": "changes_requested"}}),
        "kick-scheduled": (40, {"s01": {"decision": "approved"}}),
        "kick-test": (40, {"s01": {"decision": "rejected"}}),
    }
    for sid, (days, dec) in cases.items():
        make(cfg, sid, days, dec)
    schedule(cfg, ("kick-scheduled/s01", "scheduled"))
    cfg["streams"]["never_prune"] = ["kick-test"]
    rec = make(cfg, "kick-live", 40, {"s01": {"decision": "rejected"}})
    (rec / "live").mkdir()
    jsonstate.write(rec / "live" / "state.json", {"status": "recording"})
    unreviewed = cfg["paths"]["streams"] / "kick-new"
    unreviewed.mkdir()
    jsonstate.write(unreviewed / "meta.json", {"started_at": "2026-01-01T00:00:00+00:00"})
    (unreviewed / "source.mkv").write_bytes(b"x")
    out = streams.prune(cfg, only=[*cases, "kick-live", "kick-new"], yes=True)
    text = "\n".join(out)
    for why in ("5 days old", "1 waiting on a decision", "1 scheduled, not uploaded", "never_prune",
                "recording", "not in review yet"):
        assert why in text
    assert all((cfg["paths"]["streams"] / sid / "source.mkv").exists() for sid in [*cases, "kick-live", "kick-new"])
    assert out[-1] == "freed 0 MB"


def test_a_held_item_blocks_only_while_it_could_still_go_out(cfg):
    held = {"s01": {"decision": "approved", "fields": {"Hold": True}}}
    make(cfg, "kick-1", 40, held)
    assert streams.check(cfg, cfg["paths"]["streams"] / "kick-1", {}, 30)["ok"]   # past stale_days
    cfg["schedule"]["stale_days"] = 0                                             # never stale: waits
    cfg["_resolved"].clear()   # for_stream cached the stream's config with the old value
    assert streams.check(cfg, cfg["paths"]["streams"] / "kick-1", {}, 30)["why"] == "1 held"


def test_a_pinned_hold_or_a_show_that_never_goes_stale_keeps_the_source(cfg):
    """Copilot on #248: the scheduler's rules, per stream: a pin never goes stale, and a show's
    own stale_days = 0 keeps a held item waiting however old the stream."""
    make(cfg, "kick-1", 40, {"s01": {"decision": "approved", "fields": {"Hold": True, "Publish at": "2026-12-01"}}})
    assert streams.check(cfg, cfg["paths"]["streams"] / "kick-1", {}, 30)["why"] == "1 held"
    cfg["shows"] = {"kino": {"channels": ["kinocasinogaming"], "overrides": {"schedule": {"stale_days": 0}}}}
    cfg["_root"] = cfg
    make(cfg, "kick-2", 40, {"s01": {"decision": "approved", "fields": {"Hold": True}}})
    assert streams.check(cfg, cfg["paths"]["streams"] / "kick-2", {}, 30)["why"] == "1 held"


def test_cuts_go_only_for_uploaded_or_rejected_items(cfg):
    d = make(cfg, "kick-1", 40, {"s01": {"decision": "approved"}, "s02": {"decision": "rejected"},
                                 "s03": {"decision": "approved"}})
    schedule(cfg, ("kick-1/s01", "uploaded"))        # s03: let go by the scheduler, a pin may still send it
    (d / "review" / "s01.small.mp4").write_bytes(b"x" * 50)   # the copy a big preview uploads as
    streams.prune(cfg, cuts=True, yes=True)
    assert not (d / "segments" / "s01.mp4").exists() and not (d / "review" / "s01.mp4").exists()
    assert not (d / "review" / "s01.small.mp4").exists()
    assert not (d / "segments" / "s02.mp4").exists()
    assert (d / "segments" / "s03.mp4").exists() and (d / "review" / "s03.mp4").exists()


def test_sidecars_named_source_stay_and_unfinished_runs_are_left_alone(cfg):
    d = make(cfg, "yt-1", 40, {"s00": {"decision": "rejected"}}, files=("source.mp4", "source.info.json"))
    streams.prune(cfg, yes=True)
    assert not (d / "source.mp4").exists() and (d / "source.info.json").exists()
    p = make(cfg, "kick-partial", 40, {"s01": {"decision": "rejected"}})
    jsonstate.write(p / "run.json", {"status": "partial"})
    assert "its run is partial" in streams.check(cfg, p, {}, 30)["why"]


def test_an_unreadable_review_file_leaves_the_stream_alone(cfg):
    d = make(cfg, "kick-1", 40, {"s01": {"decision": "rejected"}})
    (d / "review.json").write_text("{broken", encoding="utf-8")
    c = streams.check(cfg, d, {}, 30)
    assert not c["ok"] and "can't read" in c["why"]


def test_listing_totals_and_hides_small_streams(cfg):
    make(cfg, "kick-1", 40, {"s01": {"decision": "rejected"}})
    make(cfg, "kick-2", 2, {"s01": {"decision": "rejected"}})     # small and too young: not shown
    s = streams.listing(cfg)
    assert [r["stream"] for r in s["streams"]] == ["kick-1"] and s["hidden"] == 1
    assert s["prunable"] == 2000 and s["total"] > 5200
    s = streams.listing(cfg, everything=True)
    assert s["streams"][0]["prunable"] and s["streams"][0]["show"] == ""
    assert "would free 2 MB" not in "\n".join(streams.listing_lines(s))   # under 0.1 GB: shown in MB
    assert "streams take" in streams.listing_lines(s)[-1]


def test_older_than_must_be_a_day(cfg):
    with pytest.raises(ClipperError, match="at least 1 day"):
        streams.prune(cfg, older_than_days=0)


def test_prune_while_free_below_goes_oldest_first_and_stops_once_there_is_room(cfg, monkeypatch):
    make(cfg, "kick-1", 90, {"s01": {"decision": "approved"}})
    make(cfg, "kick-2", 70, {"s01": {"decision": "approved"}})
    make(cfg, "kick-3", 50, {"s01": {"decision": "approved"}})
    free = iter([10.0, 25.0])   # under 20% before the first stream, over it before the second
    monkeypatch.setattr(streams, "free_pct", lambda cfg, plus=0: next(free))
    out = streams.prune(cfg, older_than_days=30, yes=True, while_free_below=20)
    root = cfg["paths"]["streams"]
    assert not (root / "kick-1" / "source.mkv").exists()
    assert (root / "kick-2" / "source.mkv").exists() and (root / "kick-3" / "source.mkv").exists()
    assert any("25.0% of the share is free" in line for line in out)


def test_prune_while_free_below_does_nothing_when_there_is_room_and_the_age_floor_still_holds(cfg, monkeypatch):
    d = make(cfg, "kick-1", 90, {"s01": {"decision": "approved"}})
    young = make(cfg, "kick-2", 10, {"s01": {"decision": "approved"}})
    monkeypatch.setattr(streams, "free_pct", lambda cfg, plus=0: 60.0)
    streams.prune(cfg, older_than_days=30, yes=True, while_free_below=20)
    assert (d / "source.mkv").exists()
    monkeypatch.setattr(streams, "free_pct", lambda cfg, plus=0: 5.0)
    streams.prune(cfg, older_than_days=30, yes=True, while_free_below=20)
    assert not (d / "source.mkv").exists() and (young / "source.mkv").exists()   # 10 days is under the floor


def test_a_dry_run_counts_what_it_would_free_when_it_checks_the_room(cfg, monkeypatch):
    make(cfg, "kick-1", 90, {"s01": {"decision": "approved"}})
    seen = []
    monkeypatch.setattr(streams, "free_pct", lambda cfg, plus=0: seen.append(plus) or 1.0)
    streams.prune(cfg, older_than_days=30, while_free_below=20)
    assert seen == [0]


def test_oldest_first_goes_by_the_start_time_not_the_day(cfg, monkeypatch):
    a = make(cfg, "kick-999", 60, {"s01": {"decision": "approved"}})   # sorts first by name
    b = make(cfg, "yt-abc", 60, {"s01": {"decision": "approved"}})
    base = datetime.now(UTC) - timedelta(days=60)
    jsonstate.write(a / "meta.json", {"started_at": (base.replace(hour=20)).isoformat(), "channel": "kinocasinogaming"})
    jsonstate.write(b / "meta.json", {"started_at": (base.replace(hour=1)).isoformat(), "channel": "kinocasinogaming"})
    free = iter([5.0, 50.0])
    monkeypatch.setattr(streams, "free_pct", lambda cfg, plus=0: next(free))
    streams.prune(cfg, older_than_days=30, yes=True, while_free_below=20)
    assert not (b / "source.mkv").exists() and (a / "source.mkv").exists()


def test_prune_checks_a_stream_again_just_before_removing_it(cfg, monkeypatch):
    first = make(cfg, "kick-1", 90, {"s01": {"decision": "approved"}})
    second = make(cfg, "kick-2", 91, {"s01": {"decision": "approved"}})
    real = streams.removable

    def removable(sdir, c, cuts):
        if sdir.name == "kick-1":   # a change request lands while prune is busy with the first stream
            jsonstate.write(second / "review.json", {"s01": {"decision": "changes_requested"}})
        return real(sdir, c, cuts)

    monkeypatch.setattr(streams, "removable", removable)
    streams.prune(cfg, older_than_days=30, yes=True)
    assert not (first / "source.mkv").exists() and (second / "source.mkv").exists()
