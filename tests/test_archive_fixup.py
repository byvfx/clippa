"""`clippa archive fixup` (#446): an archive item whose timeline check refused its source is
repaired in place, under the name the next run reads, and the chat offers it as a card."""
import os
import re

import pytest
from test_kick import BACKWARD, IN_ORDER, REPAIRED, FakeFf

from clippa import archive, catalog, jsonstate
from clippa.errors import ClipperError

QUIET = {"up": True, "live": [], "error": "", "age_s": 1.0}


@pytest.fixture
def acfg(defaults, tmp_path, monkeypatch):
    defaults["paths"]["archive"] = tmp_path / "archive"
    d = tmp_path / "archive" / "s"
    (d / "kick-1").mkdir(parents=True)
    jsonstate.write(d / "catalog.json", {"kick-1": {"id": "kick-1", "source": "kick", "duration_s": 33011.2}})
    monkeypatch.setattr("clippa.services.recorder", lambda cfg: dict(QUIET))
    return defaults


def idir(cfg):
    return cfg["paths"]["archive"] / "s" / "kick-1"


def test_a_refused_copy_is_repaired_where_the_next_run_reads_it(acfg, monkeypatch, capsys):
    """The remux replaces source.refused.ts itself: renamed back to source.ts it would be taken for a
    partial and unlinked. The wav of the old copy goes, with its stamp and the marker."""
    d = idir(acfg)
    (d / "source.refused.ts").write_bytes(b"broken")
    (d / "audio.wav").write_bytes(b"wav of the jumping copy")
    (d / "audio.wav.src").write_text('{"size": 6, "mtime_ns": 1}', encoding="utf-8")
    monkeypatch.setattr(archive.subprocess, "run", FakeFf([BACKWARD, REPAIRED]))
    assert archive.fixup_item(acfg, "s", "kick-1") == 821.7
    assert (d / "source.refused.ts").read_bytes() == b"remuxed"
    assert not (d / "source.ts").exists()
    assert not (d / "audio.wav").exists() and not (d / "audio.wav.src").exists()
    assert not (d / "audio.wav.stale").exists()
    assert "`clippa archive transcribe s` finishes it now" in capsys.readouterr().out
    _kept, src, fresh = archive.item_source(d)  # what the next run reads: the repaired copy, no download
    assert src == d / "source.refused.ts" and not fresh


def test_a_marked_download_is_repaired_in_place_and_stays_marked(acfg, monkeypatch):
    """Where the share wouldn't rename the refusal, source.ts and `.refused` are the record: the
    remux replaces source.ts and the marker stays, so the next run reuses it, not downloads over it."""
    d = idir(acfg)
    (d / "source.ts").write_bytes(b"broken")
    (d / ".refused").write_text("the timeline jumps\n", encoding="utf-8")
    monkeypatch.setattr(archive.subprocess, "run", FakeFf([BACKWARD, REPAIRED]))
    archive.fixup_item(acfg, "s", "kick-1")
    assert (d / "source.ts").read_bytes() == b"remuxed" and (d / ".refused").exists()
    assert archive.item_source(d)[2] is False


def test_an_in_order_copy_is_refused_and_its_wav_kept(acfg, monkeypatch, capsys):
    """Nothing to remux: the wav here was decoded from these very bytes, so dropping it would only
    buy a decode that is refused again. It says so instead of claiming the next run finishes."""
    d = idir(acfg)
    (d / "source.refused.ts").write_bytes(b"in order")
    (d / "audio.wav").write_bytes(b"wav")
    st = (d / "source.refused.ts").stat()  # its stamp names this copy: the next run would reuse it
    jsonstate.write(d / "audio.wav.src", {"size": st.st_size, "mtime_ns": st.st_mtime_ns})
    monkeypatch.setattr(archive.subprocess, "run", FakeFf([REPAIRED, REPAIRED], packets=IN_ORDER, wav_s=33900.0))
    with pytest.raises(ClipperError, match="already in order, so a remux has nothing to take out; its audio.wav runs 67.3 s past"):
        archive.fixup_item(acfg, "s", "kick-1")
    assert (d / "source.refused.ts").read_bytes() == b"in order" and (d / "audio.wav").exists()
    assert "finishes it now" not in capsys.readouterr().out


def test_an_in_order_copy_behind_an_empty_name_is_judged_by_its_own_wav(acfg, monkeypatch):
    """Grok on #453: an empty source.mp4 sorts before source.refused.ts in the `source.*` glob, but
    the next run drops it before it reads; the overrun of the wav stamped for this copy is final."""
    d = idir(acfg)
    (d / "source.refused.ts").write_bytes(b"in order")
    (d / "source.mp4").write_bytes(b"")
    (d / "audio.wav").write_bytes(b"wav")
    st = (d / "source.refused.ts").stat()
    jsonstate.write(d / "audio.wav.src", {"size": st.st_size, "mtime_ns": st.st_mtime_ns})
    monkeypatch.setattr(archive.subprocess, "run", FakeFf([REPAIRED, REPAIRED], packets=IN_ORDER, wav_s=33900.0))
    with pytest.raises(ClipperError, match="its audio.wav runs 67.3 s past"):
        archive.fixup_item(acfg, "s", "kick-1")


def test_an_in_order_copy_kept_for_a_failed_stale_mark_is_sent_to_transcribe(acfg, monkeypatch):
    """Grok on #453: `.refused` also marks a finished download whose stale mark couldn't be written;
    that copy is fine, so the fixup says transcribe reads it as it is, not that it can't be fixed."""
    d = idir(acfg)
    (d / "source.ts").write_bytes(b"in order")
    (d / ".refused").write_text("couldn't mark audio.wav stale\n", encoding="utf-8")
    monkeypatch.setattr(archive.subprocess, "run", FakeFf([REPAIRED, REPAIRED], packets=IN_ORDER))
    with pytest.raises(ClipperError, match=r"`clippa archive transcribe s` reads it as it is") as err:
        archive.fixup_item(acfg, "s", "kick-1")
    assert "can't fix" not in str(err.value)
    assert (d / "source.ts").read_bytes() == b"in order"


@pytest.mark.parametrize("stamp", [None, {"size": 1, "mtime_ns": 1}], ids=["no-stamp", "another-files-stamp"])
def test_an_overrunning_wav_the_next_run_would_decode_again_is_not_final(acfg, monkeypatch, stamp):
    """Grok on #453: a wav whose stamp doesn't name this copy (an archive item trusts no mtime) is
    decoded again by the next run, so its overrun says nothing about this copy."""
    d = idir(acfg)
    (d / "source.ts").write_bytes(b"in order")
    (d / ".refused").write_text("couldn't mark audio.wav stale\n", encoding="utf-8")
    (d / "audio.wav").write_bytes(b"wav of an earlier, jumping decode")
    if stamp:
        jsonstate.write(d / "audio.wav.src", stamp)
    monkeypatch.setattr(archive.subprocess, "run", FakeFf([REPAIRED, REPAIRED], packets=IN_ORDER, wav_s=33900.0))
    with pytest.raises(ClipperError, match=r"`clippa archive transcribe s` reads it as it is") as err:
        archive.fixup_item(acfg, "s", "kick-1")
    assert "can't fix" not in str(err.value)


def test_a_copy_changed_during_the_remux_is_not_replaced(acfg, monkeypatch):
    """Another clippa moved or rewrote the copy while it was remuxed: what was verified is no longer
    the file at that name, so nothing is put in its place and the wav stays."""
    d = idir(acfg)
    (d / "source.refused.ts").write_bytes(b"broken")
    ff = FakeFf([BACKWARD, REPAIRED])

    def run(cmd, *a, **kw):
        out = ff(cmd, *a, **kw)
        if cmd[0] == "ffmpeg":  # the remux: another run rewrites the copy meanwhile
            (d / "source.refused.ts").write_bytes(b"BROKEN")  # same size, a new time
            os.utime(d / "source.refused.ts", ns=(10**18, 10**18))
        return out

    monkeypatch.setattr(archive.subprocess, "run", run)
    with pytest.raises(ClipperError, match="changed while it was remuxed"):
        archive.fixup_item(acfg, "s", "kick-1")
    assert (d / "source.refused.ts").read_bytes() == b"BROKEN"
    assert not list(d.glob(".timeline-*"))


def test_a_stream_of_ours_is_pointed_at_its_own_fixup(acfg):
    d = acfg["paths"]["archive"] / "s"
    jsonstate.write(d / "catalog.json", {"kick-9": {"id": "kick-9", "source": "kick", "dir": "streams/kick-9",
                                                    "url": "https://kick.com/x/videos/c7ab4ab0-9c62-47a7-81d6-7f5ec581b091"}})
    with pytest.raises(ClipperError, match=r"`clippa fetch https://kick.com/x/videos/c7ab4ab0-9c62-47a7-81d6-7f5ec581b091 --fixup`"):
        archive.fixup_item(acfg, "s", "kick-9")
    p = catalog.prepare(acfg, "archive_fixup", {"subject": "s", "item": "kick-9"})
    assert p.kind == "answer" and "clippa fetch https://kick.com/x/videos/c7ab4ab0-9c62-47a7-81d6-7f5ec581b091 --fixup" in p.text


def test_the_card_leaves_the_folder_as_it_is(acfg):
    """A preview reads; item_source's tidying (a marker with no source left) is the run's to do."""
    (idir(acfg) / ".refused").write_text("x", encoding="utf-8")
    p = catalog.prepare(acfg, "archive_fixup", {"subject": "s", "item": "kick-1"})
    assert p.kind == "answer" and "nothing set aside" in p.text
    assert (idir(acfg) / ".refused").exists()


@pytest.mark.parametrize("setup, msg", [
    ("kept", "the kept source.mp4, which --fixup can't remux; move source.mp4 out of the item folder (keep it: "
             "a VOD that is gone can't be downloaded again) and the next `clippa archive transcribe` downloads"),
    ("kept+aside", "move source.mp4 out of the item folder (keep it) and `clippa archive fixup s kick-1` repairs "
                   "the source.refused.ts beside it"),
    ("nothing", "nothing set aside to repair"),
], ids=["kept-video", "kept-video-and-aside", "nothing"])
def test_what_it_cannot_repair_is_one_line_and_nothing_runs(acfg, monkeypatch, setup, msg):
    """Grok on #453: a kept video is never to be deleted, as it may be the only copy left."""
    d = idir(acfg)
    if setup.startswith("kept"):
        (d / "source.mp4").write_bytes(b"kept video")
    if setup == "kept+aside":
        (d / "source.refused.ts").write_bytes(b"broken")
    monkeypatch.setattr(archive.subprocess, "run", lambda *a, **kw: pytest.fail("probed"))
    with pytest.raises(ClipperError, match=re.escape(msg)):
        archive.fixup_item(acfg, "s", "kick-1")


def test_a_kept_video_on_a_dead_link_is_told_to_stay(acfg, monkeypatch):
    """Grok on #453: with the link dead no .ts is ever downloaded (transcribe skips a dead link with
    nothing set aside), so moving the video out would leave the item with nothing to read: the
    advice keeps it, and never says to move it or that a download will come."""
    d = idir(acfg)
    jsonstate.write(d.parent / "links.json", {"kick-1": {"status": "dead"}})
    (d / "source.mp4").write_bytes(b"kept video")
    monkeypatch.setattr(archive.subprocess, "run", lambda *a, **kw: pytest.fail("probed"))
    with pytest.raises(ClipperError) as err:
        archive.fixup_item(acfg, "s", "kick-1")
    msg = str(err.value)
    assert "its link is dead, so no .ts will be downloaded" in msg
    assert "keep source.mp4 where it is (it is the only copy)" in msg
    assert "move source.mp4 out" not in msg and "downloads a .ts" not in msg
    p = catalog.prepare(acfg, "archive_fixup", {"subject": "s", "item": "kick-1"})
    assert p.kind == "answer" and "its link is dead" in p.text
    assert (d / "source.mp4").read_bytes() == b"kept video"


def test_an_unreadable_links_file_gives_the_usual_advice(acfg, monkeypatch):
    """The dead-link check is advice only: a links.json it can't read says nothing either way."""
    d = idir(acfg)
    (d.parent / "links.json").write_text("not json", encoding="utf-8")
    (d / "source.mp4").write_bytes(b"kept video")
    monkeypatch.setattr(archive.subprocess, "run", lambda *a, **kw: pytest.fail("probed"))
    with pytest.raises(ClipperError, match="the next `clippa archive transcribe` downloads"):
        archive.fixup_item(acfg, "s", "kick-1")


def test_an_unknown_item_is_one_line(acfg):
    with pytest.raises(ClipperError, match=r"s: no item 'nope' in its catalog"):
        archive.fixup_item(acfg, "s", "nope")


@pytest.mark.parametrize("rec, msg", [
    ({**QUIET, "live": ["kinocasinogaming"]}, "kinocasinogaming is live"),
    ({**QUIET, "error": "OSError: gone"}, "can't tell whether a show is live"),
], ids=["live", "unknown"])
def test_no_repair_while_a_show_is_live_unless_forced(acfg, monkeypatch, rec, msg):
    """A repair reads and rewrites a whole source on the share the recorder writes to."""
    d = idir(acfg)
    (d / "source.refused.ts").write_bytes(b"broken")
    monkeypatch.setattr("clippa.services.recorder", lambda cfg: rec)
    monkeypatch.setattr(archive.subprocess, "run", lambda *a, **kw: pytest.fail("probed while live"))
    with pytest.raises(ClipperError, match=msg):
        archive.fixup_item(acfg, "s", "kick-1")
    monkeypatch.setattr(archive.subprocess, "run", FakeFf([BACKWARD, REPAIRED]))
    archive.fixup_item(acfg, "s", "kick-1", force=True)
    assert (d / "source.refused.ts").read_bytes() == b"remuxed"


def test_the_chat_offers_the_repair_as_a_spend_card(acfg):
    """Agent-first (#365): the repair is a catalog tool, a card with what it costs (disk, not money)
    and the argv a tap runs; `--` keeps an id that starts with a dash an id."""
    (idir(acfg) / "source.refused.ts").write_bytes(b"x" * 1000)
    p = catalog.prepare(acfg, "archive_fixup", {"subject": "s", "item": "kick-1"})
    assert p.kind == "card" and p.risk == catalog.SPEND and p.tool == "archive_fixup"
    assert p.argv == ["archive", "fixup", "--", "s", "kick-1"]
    assert "no model or Modal cost" in p.estimate
    assert any("source.refused.ts remuxed" in line for line in p.lines)


@pytest.mark.parametrize("args, setup, says", [
    ({"subject": "s", "item": "kick-1"}, "live", "is live"),
    ({"subject": "s", "item": "kick-1"}, "kept", "can't be remuxed"),
    ({"subject": "s", "item": "kick-1"}, "nothing", "nothing set aside"),
    ({"subject": "s", "item": "nope"}, "", "no archive item"),
    ({"subject": "..", "item": "kick-1"}, "", "isn't an archive item"),
    ({"subject": "s"}, "", "Which item?"),
], ids=["live", "kept", "nothing", "unknown", "traversal", "missing"])
def test_the_chat_answers_instead_of_carding_what_it_cannot_repair(acfg, monkeypatch, args, setup, says):
    d = idir(acfg)
    if setup == "live":
        (d / "source.refused.ts").write_bytes(b"x")
        monkeypatch.setattr("clippa.services.recorder", lambda cfg: {**QUIET, "live": ["kino"]})
    elif setup == "kept":
        (d / "source.mp4").write_bytes(b"x")
    p = catalog.prepare(acfg, "archive_fixup", args)
    assert p.kind == "answer" and says in p.text


def test_the_cards_argv_parses_as_meant():
    from clippa.cli import build_parser

    a = build_parser().parse_args(["archive", "fixup", "--", "s", "-dashed1"])
    assert (a.action, a.subject, a.item, a.force) == ("fixup", "s", "-dashed1", False)


def test_a_youtube_stream_of_ours_is_not_pointed_at_a_fetch_that_refuses_it(acfg):
    """Grok on #453: `fetch --fixup` takes a canonical Kick VOD url only."""
    d = acfg["paths"]["archive"] / "s"
    jsonstate.write(d / "catalog.json", {"yt-9": {"id": "yt-9", "source": "youtube", "dir": "streams/yt-9",
                                                  "url": "https://www.youtube.com/watch?v=abc"}})
    with pytest.raises(ClipperError) as err:
        archive.fixup_item(acfg, "s", "yt-9")
    assert "youtube.com" not in str(err.value) and "has no remux repair" in str(err.value)


@pytest.mark.parametrize("copy, todo", [("source.refused.ts", ["kick-1"]), (None, [])], ids=["kept", "none"])
def test_a_dead_link_with_a_copy_kept_here_is_still_transcribed(acfg, monkeypatch, capsys, copy, todo):
    """Grok on #453: a repaired copy of a VOD that is gone is the case the repair is for, so the dead
    link only skips an item with nothing here to read."""
    d = acfg["paths"]["archive"] / "s"
    if copy:
        (d / "kick-1" / copy).write_bytes(b"repaired")
    archive.save_json(d, "links.json", {"kick-1": {"status": "dead", "since": "2026-11-01T00:00:00", "reason": "gone"}})
    seen = []
    monkeypatch.setattr(archive, "transcribe_estimate", lambda cfg, s, todo, gpu, par: seen.append(todo) or "")
    archive.run_transcribe(acfg, "s", None, False, "L4", 1, dry_run=True)
    assert [it["id"] for it in seen[0]] == todo
    assert ("1 skipped, source link dead" in capsys.readouterr().out) is (copy is None)
