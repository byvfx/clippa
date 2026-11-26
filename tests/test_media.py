"""media: the probe facts it gives, its source lookup, and what counts as a link (#444)."""
import os
import subprocess

import pytest

from clippa import media
from clippa.errors import ClipperError
from clippa.media import source_file


def test_a_symlink_is_a_link(tmp_path, link_or_skip):
    target = tmp_path / "a.ts"
    target.write_bytes(b"x")
    link = tmp_path / "link.ts"
    link_or_skip(target, link)
    assert media.is_link(link)


def test_an_ordinary_file_is_no_link(tmp_path):
    plain = tmp_path / "source.ts"
    plain.write_bytes(b"x")
    assert not media.is_link(plain)


def test_a_junction_is_a_link_even_though_is_symlink_misses_it(tmp_path, junction_or_skip):
    """#444: on Windows a junction needs no privilege and Path.is_symlink() is false for it, so a
    guard built on is_symlink alone would let one through. This is why the guards ask is_link."""
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    junction = tmp_path / "source.ts"
    junction_or_skip(folder, junction)
    assert not junction.is_symlink() and os.path.isjunction(junction)
    assert media.is_link(junction)


def test_source_file_skips_downloads_in_progress(tmp_path):
    with pytest.raises(ClipperError):
        source_file(tmp_path)
    for name in ("source.mp4.part", "source.f137.mp4.ytdl", "source.mp4-Frag12"):
        (tmp_path / name).touch()
    with pytest.raises(ClipperError):
        source_file(tmp_path)
    (tmp_path / "source.mkv").touch()
    assert source_file(tmp_path).name == "source.mkv"


def test_ffprobe_is_kept_to_local_files(tmp_path, monkeypatch):
    """#440: transcribe's wav check takes its length from try_duration, so an `audio.wav` that is
    really an HLS playlist must not send ffprobe to a url: -protocol_whitelist file before the
    input, on both the format and the stream probe."""
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "3600.0\n", "")

    monkeypatch.setattr(media.subprocess, "run", run)
    assert media.try_duration(tmp_path / "audio.wav") == 3600.0
    assert media.try_duration(tmp_path / "source.ts", video=True) == 3600.0
    assert seen == [
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries",
         "format=duration", "-of", "csv=p=0", str(tmp_path / "audio.wav")],
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", "v:0",
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(tmp_path / "source.ts")],
    ]


def test_a_duration_with_a_field_after_it_is_still_a_duration(tmp_path, monkeypatch):
    """#440: only the first CSV field is the value. ffprobe can append a side-data field to a
    line (`3600.0,`), and reading the whole line would turn every length into no length."""
    monkeypatch.setattr(media.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "3600.0,\n", ""))
    assert media.try_duration(tmp_path / "audio.wav") == 3600.0
    assert media.try_duration(tmp_path / "source.ts", video=True) == 3600.0
