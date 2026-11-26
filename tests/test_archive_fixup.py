"""`clippa archive fixup` (#446): an archive item whose timeline check refused its source is
repaired in place, under the name the next run reads, and the chat offers it as a card."""
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


def test_an_in_order_copy_is_left_but_a_wav_past_it_goes(acfg, monkeypatch, capsys):
    """Nothing to remux, but the wav its run decoded runs past the container (the guard's refusal):
    it is dropped so the next run decodes the copy again, and the copy keeps its bytes."""
    d = idir(acfg)
    (d / "source.refused.ts").write_bytes(b"in order")
    (d / "audio.wav").write_bytes(b"wav")
    monkeypatch.setattr(archive.subprocess, "run", FakeFf([REPAIRED, REPAIRED], packets=IN_ORDER, wav_s=33900.0))
    assert archive.fixup_item(acfg, "s", "kick-1") == 0.0
    assert (d / "source.refused.ts").read_bytes() == b"in order"
    assert not (d / "audio.wav").exists()
    assert "audio.wav runs 67.3 s past its source; dropped" in capsys.readouterr().out


@pytest.mark.parametrize("setup, msg", [
    ("kept", "its source is the kept source.mp4, which --fixup can't remux; delete it"),
    ("nothing", "nothing set aside to repair"),
], ids=["kept-video", "nothing"])
def test_what_it_cannot_repair_is_one_line_and_nothing_runs(acfg, monkeypatch, setup, msg):
    d = idir(acfg)
    if setup == "kept":
        (d / "source.mp4").write_bytes(b"kept video")
        (d / "source.refused.ts").write_bytes(b"broken")
    monkeypatch.setattr(archive.subprocess, "run", lambda *a, **kw: pytest.fail("probed"))
    with pytest.raises(ClipperError, match=msg):
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
