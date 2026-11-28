"""#434: `clippa fetch <path>`, a video already on disk as a stream (clippa/local.py)."""
import argparse
import os

import pytest

from clippa import cli, config, jsonstate, local, media
from clippa.errors import ClipperError


@pytest.fixture
def cfg(defaults, tmp_path, monkeypatch):
    """No ffprobe: every file is a 2 h video with a creation date."""
    defaults["paths"]["streams"] = tmp_path / "streams"
    defaults["paths"]["streams"].mkdir()
    monkeypatch.setattr(media, "size", lambda p: (1920, 1080))
    monkeypatch.setattr(media, "duration", lambda p: 7200.0)
    monkeypatch.setattr(media, "creation_time", lambda p: "2026-11-24T03:00:00.000000Z")
    return defaults


def video(tmp_path, name="Kino Night #12.mkv", data=b"x" * 5000):
    p = tmp_path / "vods" / name
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(data)
    return p


def fetch(cfg, arg, **kw):
    ns = dict(url=str(arg), no_video=False, no_chat=False, fixup=False, link=False, move=False,
              title=None, started=None)
    cli.cmd_fetch(cfg, argparse.Namespace(**{**ns, **kw}))


def test_a_path_is_told_from_a_url(tmp_path):
    f = video(tmp_path)
    assert local.is_path(str(f)) and local.is_path(str(f.parent))
    assert not local.is_path("https://kick.com/kino/videos/abc")
    assert not local.is_path("kick.com/kino/videos/abc")  # no such file here: a url
    assert not local.is_path(f"file://{f}") and not local.is_path(f"file:{f}")


def test_a_file_becomes_a_stream_with_meta(cfg, tmp_path, capsys):
    f = video(tmp_path)
    fetch(cfg, f)
    (d,) = (cfg["paths"]["streams"]).iterdir()
    assert d.name.startswith("local-kino-night-12-") and len(d.name) == len("local-kino-night-12-") + 6
    assert (d / "source.mkv").read_bytes() == f.read_bytes() and f.exists()  # copied, original kept
    assert not list(d.glob("*.part"))
    meta = jsonstate.read(d / "meta.json")
    assert meta["platform"] == "local" and meta["channel"] == "local"
    assert meta["title"] == "Kino Night #12" and meta["duration_s"] == 7200.0
    assert meta["started_at"] == "2026-11-24T03:00:00+00:00"
    assert meta["source"] == f.name  # the name, never the path
    assert media.source_file(d) == d / "source.mkv"
    assert f"clippa run {d.name}" in capsys.readouterr().out


def test_the_same_file_again_under_any_name_is_a_no_op(cfg, tmp_path, capsys):
    f = video(tmp_path)
    fetch(cfg, f)
    (d,) = cfg["paths"]["streams"].iterdir()
    capsys.readouterr()
    fetch(cfg, f.rename(f.with_name("renamed.mkv")))
    assert list(cfg["paths"]["streams"].iterdir()) == [d]
    assert "already imported" in capsys.readouterr().out
    fetch(cfg, video(tmp_path, "other.mkv", b"y" * 5000))  # other bytes: a stream of its own
    assert len(list(cfg["paths"]["streams"].iterdir())) == 2


def test_the_fingerprint_reads_both_ends_of_a_big_file(tmp_path):
    big = bytearray(3 * local._EDGE)
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(bytes(big))
    big[-1] = 1  # only the last byte differs
    b.write_bytes(bytes(big))
    assert local.fingerprint(a) != local.fingerprint(b)


def test_a_small_file_is_hashed_whole(tmp_path):
    # review: 1-2 MiB files differing only after the first MiB had one fingerprint
    size = local._EDGE + local._EDGE // 2
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"\0" * size)
    b.write_bytes(b"\0" * (size - 1) + b"\1")
    assert local.fingerprint(a) != local.fingerprint(b)


def test_started_is_in_the_shows_timezone(cfg, tmp_path):
    # review: a NAS container runs in UTC; "20:00" means the show's evening
    cfg["schedule"]["timezone"] = "America/Los_Angeles"
    fetch(cfg, video(tmp_path), started="2026-11-24T20:00")
    (d,) = cfg["paths"]["streams"].iterdir()
    assert jsonstate.read(d / "meta.json")["started_at"] == "2026-11-24T20:00:00-08:00"


def test_another_files_stream_with_the_same_short_id_is_left_alone(cfg, tmp_path):
    f = video(tmp_path)
    d = cfg["paths"]["streams"] / f"local-{local.slug(f.stem)}-{local.fingerprint(f)[:6]}"
    d.mkdir()
    jsonstate.write(d / "meta.json", {"fingerprint": "someone else's"})
    with pytest.raises(ClipperError, match="another file's"):
        fetch(cfg, f)
    assert jsonstate.read(d / "meta.json") == {"fingerprint": "someone else's"}


def test_a_show_without_channels_is_refused(cfg, tmp_path):
    # review: for_show would hand back the base [kick] channel, which may be another show's
    pinned = config.pin_show({**cfg, "shows": {"pod": {"channels": [], "overrides": {}}}, "_resolved": {}}, "pod")
    with pytest.raises(ClipperError, match=r"\[shows.pod\] has no channels"):
        fetch(pinned, video(tmp_path))
    assert not list(cfg["paths"]["streams"].iterdir())


@pytest.mark.parametrize("maker", ["link_or_skip", "junction_or_skip"])
def test_youtube_fetch_refuses_a_linked_stream_folder(cfg, tmp_path, monkeypatch, request, maker):
    link_or_skip = request.getfixturevalue(maker)
    from clippa import ytvod

    monkeypatch.setattr(ytvod, "resolve", lambda url, c: {"stream_id": "youtube-abc", "title": "t",
                                                          "duration_s": 1.0})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link_or_skip(elsewhere, cfg["paths"]["streams"] / "youtube-abc", directory=True)
    with pytest.raises(ClipperError, match="is a link or outside"):
        ytvod.fetch(cfg, "https://www.youtube.com/watch?v=abcdefghijk", no_video=True, no_chat=True)
    assert not list(elsewhere.iterdir())


def test_title_started_and_show_flags(cfg, tmp_path):
    shows = {"kino": {"channels": ["kinocasino", "kino2"], "overrides": {}}}
    pinned = config.pin_show({**cfg, "shows": shows, "_resolved": {}}, "kino")
    f = video(tmp_path)
    fetch(pinned, f, title="Night 12", started="2026-11-24T20:00")
    (d,) = cfg["paths"]["streams"].iterdir()
    meta = jsonstate.read(d / "meta.json")
    assert meta["channel"] == "kinocasino" and meta["title"] == "Night 12"
    assert meta["started_at"].startswith("2026-11-24T20:00") and meta["started_at"][16:] != ""  # tz-aware
    with pytest.raises(ClipperError, match="--started"):
        fetch(cfg, video(tmp_path, "x.mp4", b"z" * 10), started="last tuesday")


def test_move_and_link(cfg, tmp_path):
    f = video(tmp_path)
    fetch(cfg, f, move=True)
    (d,) = cfg["paths"]["streams"].iterdir()
    assert not f.exists() and (d / "source.mkv").exists()
    g = video(tmp_path, "two.mp4", b"q" * 100)
    fetch(cfg, g, link=True)
    linked = next(p for p in cfg["paths"]["streams"].iterdir() if p != d) / "source.mp4"
    assert g.exists() and os.path.samefile(g, linked)


def test_a_finished_copy_without_meta_is_kept(cfg, tmp_path, monkeypatch):
    f = video(tmp_path)
    fetch(cfg, f)
    (d,) = cfg["paths"]["streams"].iterdir()
    (d / "meta.json").unlink()  # the import stopped after the copy
    monkeypatch.setattr(local, "_place", lambda *a: pytest.fail("copied again"))
    fetch(cfg, f, move=True)
    assert (d / "meta.json").exists() and (d / "source.mkv").exists() and not f.exists()


def test_a_file_in_the_streams_folder_is_refused(cfg, tmp_path):
    f = video(tmp_path)
    fetch(cfg, f)
    (d,) = cfg["paths"]["streams"].iterdir()
    with pytest.raises(ClipperError, match="already in the streams folder"):
        fetch(cfg, d / "source.mkv", move=True)  # its own source, given back: never moved out
    assert (d / "source.mkv").exists() and list(cfg["paths"]["streams"].iterdir()) == [d]


def test_what_a_file_refuses(cfg, tmp_path):
    with pytest.raises(ClipperError, match="not a video file"):
        fetch(cfg, video(tmp_path, "notes.txt"))
    with pytest.raises(ClipperError, match="a folder"):
        fetch(cfg, tmp_path / "vods")
    with pytest.raises(ClipperError, match="are for urls"):
        fetch(cfg, video(tmp_path), fixup=True)
    with pytest.raises(ClipperError, match="are for a file"):
        fetch(cfg, "https://www.youtube.com/watch?v=abcdefghijk", title="x")
    assert not list(cfg["paths"]["streams"].iterdir())


@pytest.mark.parametrize("maker", ["link_or_skip", "junction_or_skip"])
def test_a_link_at_the_stream_folder_is_refused(cfg, tmp_path, request, maker):
    link_or_skip = request.getfixturevalue(maker)
    f = video(tmp_path)
    sid = f"local-{local.slug(f.stem)}-{local.fingerprint(f)[:6]}"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link_or_skip(elsewhere, cfg["paths"]["streams"] / sid, directory=True)
    with pytest.raises(ClipperError, match="is a link or outside"):
        fetch(cfg, f)
    assert not list(elsewhere.iterdir())
