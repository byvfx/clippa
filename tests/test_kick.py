"""Kick: a VOD download's missing segments are counted, a timeline that jumps back is remuxed,
and a web page where the API's JSON should be is a clear error. Offline: yt-dlp, Kick and
ffprobe are stand-ins."""
import argparse
import io
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from clippa import cli, kick, timeline, transcribe
from clippa.errors import ClipperError


class FakeYtdlp:
    """subprocess.Popen as yt-dlp: these output lines (stdout and stderr together), then this
    exit code."""

    def __init__(self, lines, code=0):
        self.lines, self.code = lines, code

    def __call__(self, args, **kw):
        self.args, self.kw = args, kw
        self.stdout = io.StringIO("".join(self.lines))
        self.returncode = self.code
        return self

    def wait(self):
        return self.code

    def poll(self):
        return None if getattr(self, "killed", None) is False else self.code

    def kill(self):
        self.killed = True


def probe_json(duration, start="62.016667"):
    """ffprobe's JSON as timeline.probe reads it: the container's own start_time and duration."""
    return json.dumps({"format": {"start_time": start, "duration": str(duration)}})


JUMPS = "880.000000\n883.833333\n62.033333\n62.066667\n"  # #440's jump, a line per packet
IN_ORDER = "62.033000,\n62.073000,\n"  # ffprobe 8's real form: its MPEGTS side data field after
STREAMS = json.dumps({"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]})


class FakeFf:
    """subprocess.run as ffprobe (these format answers, in turn), ffprobe's packet list (`packets`
    for the source, `fixed` for a remux's `.timeline-*.part`, which ffprobe 8 writes with a
    side-data field), its stream list (a video and an audio stream) and ffmpeg (this exit code and
    stderr; on success it writes its output file, as ffmpeg does).

    `by_bytes` makes the probe read the file it is given where the file's bytes are one of its
    keys, and answer that entry's `(format JSON, packet list)` instead: a source is then probed as
    what is actually on disk, so a version that reads the partial download and one that reads the
    finished file get different timelines, as they would from ffprobe."""

    def __init__(self, probes, remux_code=0, remux_stderr="", packets=JUMPS, fixed=IN_ORDER,
                 by_bytes=None):
        self.probes, self.remux_code, self.remux_stderr = list(probes), remux_code, remux_stderr
        self.packets, self.fixed, self.by_bytes = packets, fixed, by_bytes or {}
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append(args)
        if args[0] == "ffprobe":
            path = Path(args[-1])
            seen = self.by_bytes.get(path.read_bytes()) if path.is_file() else None
            if "packet=pts_time" in args:
                if seen:
                    return subprocess.CompletedProcess(args, 0, seen[1], "")
                temp = path.name.startswith(".timeline-")
                return subprocess.CompletedProcess(args, 0, self.fixed if temp else self.packets, "")
            if "stream=codec_type" in args:
                return subprocess.CompletedProcess(args, 0, STREAMS, "")
            return subprocess.CompletedProcess(args, 0, seen[0] if seen else self.probes.pop(0), "")
        assert args[0] == "ffmpeg", args
        if not self.remux_code:
            Path(args[-1]).write_bytes(b"remuxed")
        return subprocess.CompletedProcess(args, self.remux_code, "", self.remux_stderr)

    def remuxed(self):
        return [c for c in self.calls if c[0] == "ffmpeg"]


def downloads(tmp_path: Path, body: bytes = b"broken", lines=None):
    """Popen as yt-dlp: it writes source.ts with `body`, prints `lines` (a download's output),
    then finishes."""
    def run(args, **kw):
        (tmp_path / "source.ts").write_bytes(body)
        return FakeYtdlp(["[download] 100%\n"] if lines is None else lines)(args, **kw)
    return run


def temps(tmp_path: Path):
    """The remux's temp files left in the stream's folder."""
    return sorted(tmp_path.glob(".timeline-*.part"))


BACKWARD = probe_json(33011.0)  # the container, as ffprobe reads a file whose timeline jumps back
REPAIRED = probe_json(33832.7)  # the genpts remux: longer by the 821.7 s jump


def test_fetch_video_counts_skipped_segments(defaults, tmp_path, monkeypatch, capsys):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    # yt-dlp's to_screen: on stdout, so the two streams are read together
    fake = FakeYtdlp(["[download] fragment not found; Skipping fragment 12 ...\n",
                      "[download] fragment not found; Skipping fragment 13 ...\n",
                      "WARNING: something else\n"])
    monkeypatch.setattr(kick.subprocess, "Popen", fake)
    kick.fetch_video(tmp_path, {"source": "https://example.invalid/master.m3u8"}, defaults)
    assert fake.kw["stderr"] == subprocess.STDOUT
    assert "--logtostderr" not in fake.args  # the Python API's only: the command line refuses it
    assert "2 video segments were missing" in capsys.readouterr().out
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"stream_id": "kick-1", "skipped_fragments": 2}


def test_fetch_video_without_gaps_or_meta_writes_none(defaults, tmp_path, monkeypatch):
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["[download] 100%\n"]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert not (tmp_path / "meta.json").exists()


def test_a_clean_download_clears_an_old_gap_count(defaults, tmp_path, monkeypatch):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1", "skipped_fragments": 3}),
                                        encoding="utf-8")
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["[download] 100%\n"]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8")) == {"stream_id": "kick-1"}


def test_fetch_video_failure_still_raises(defaults, tmp_path, monkeypatch):
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["ERROR: 403\n"], code=1))
    with pytest.raises(subprocess.CalledProcessError):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults)


def test_a_resumed_download_keeps_the_failed_runs_count(defaults, tmp_path, monkeypatch, capsys):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    (tmp_path / "source.ts").write_bytes(b"ts")
    (tmp_path / "source.ts.ytdl").write_text("{}", encoding="utf-8")  # yt-dlp's resume point
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(
        ["Skipping fragment 12\n", "Skipping fragment 13\n", "ERROR: connection reset\n"], code=1))
    with pytest.raises(subprocess.CalledProcessError):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert (tmp_path / "source.ts.skipped").read_text(encoding="utf-8") == "2"
    # the retry resumes after them, so doesn't print them again
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["Skipping fragment 40\n", "[download] 100%\n"]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))["skipped_fragments"] == 3
    assert not (tmp_path / "source.ts.skipped").exists()


def test_a_fresh_download_ignores_an_old_tally(defaults, tmp_path, monkeypatch):
    (tmp_path / "source.ts.skipped").write_text("5", encoding="utf-8")  # no .ytdl: not a resume
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["[download] 100%\n"]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert not (tmp_path / "source.ts.skipped").exists()
    assert not (tmp_path / "meta.json").exists()


def test_a_failed_fresh_download_drops_an_old_tally_before_the_retry(defaults, tmp_path, monkeypatch):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    (tmp_path / "source.ts.skipped").write_text("5", encoding="utf-8")  # an earlier attempt's
    fail = FakeYtdlp(["ERROR: connection reset\n"], code=1)

    def starts_then_fails(args, **kw):  # yt-dlp creates its resume point, then drops, no skips
        (tmp_path / "source.ts.ytdl").write_text("{}", encoding="utf-8")
        return fail(args, **kw)

    monkeypatch.setattr(kick.subprocess, "Popen", starts_then_fails)
    with pytest.raises(subprocess.CalledProcessError):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert not (tmp_path / "source.ts.skipped").exists()
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["Skipping fragment 9\n", "[download] 100%\n"]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)  # resumes from .ytdl: its own skip only
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))["skipped_fragments"] == 1


def test_yt_dlp_is_killed_when_passing_its_output_through_fails(defaults, tmp_path, monkeypatch):
    fake = FakeYtdlp(["Skipping fragment 1\n", "[download] 5%\n"])
    fake.killed = False  # still running until killed
    monkeypatch.setattr(kick.subprocess, "Popen", fake)

    def broken(line):
        raise BrokenPipeError

    monkeypatch.setattr(kick.sys.stdout, "write", broken)
    with pytest.raises(BrokenPipeError):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert fake.killed is True
    assert (tmp_path / "source.ts.skipped").read_text(encoding="utf-8") == "1"


def test_a_web_page_instead_of_json_is_a_clear_error(monkeypatch):
    class Page(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(kick.time, "sleep", lambda s: None)
    monkeypatch.setattr(kick.urllib.request, "urlopen",
                        lambda req, timeout: Page(b"<!DOCTYPE html><title>Just a moment...</title>"))
    with pytest.raises(ClipperError, match="web page, not data"):
        kick._get("https://kick.com/api/v2/channels/x", "ua")

    # a brief check (one page, then the data) is retried through
    bodies = iter([b"<!DOCTYPE html>", b'{"ok": 1}'])
    monkeypatch.setattr(kick.urllib.request, "urlopen", lambda req, timeout: Page(next(bodies)))
    assert kick._get("https://kick.com/api/v2/channels/x", "ua") == {"ok": 1}


def test_a_line_the_console_cant_encode_doesnt_stop_the_download(monkeypatch):
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="\n")
    monkeypatch.setattr(kick.sys, "stdout", out)
    kick._echo("[download] Destination: \U0001f37f.ts\n")
    out.flush()
    assert out.buffer.getvalue() == b"[download] Destination: ?.ts\n"


def test_an_unwritable_tally_keeps_the_downloads_own_error(defaults, tmp_path, monkeypatch):
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["Skipping fragment 3\n", "ERROR: x\n"], code=1))

    def gone(*a, **k):
        raise OSError("share unreachable")

    monkeypatch.setattr(kick.Path, "write_text", gone)
    with pytest.raises(subprocess.CalledProcessError):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults)


def test_a_second_fetch_keeps_the_gap_count_while_the_video_stays(defaults, tmp_path, monkeypatch):
    defaults["paths"]["streams"] = tmp_path
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"stream_id": "kick-1", "skipped_fragments": 3}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick, "resolve", lambda url, cfg: {"stream_id": "kick-1", "title": "t", "duration_s": 3600})
    monkeypatch.setattr(kick, "fetch_chat", lambda *a: None)
    monkeypatch.setattr(kick, "fetch_video", lambda *a: None)  # source.ts is there: not fetched again
    args = argparse.Namespace(url="u", no_chat=False, no_video=False, fixup=False)
    cli.cmd_fetch(defaults, args)
    assert json.loads((d / "meta.json").read_text(encoding="utf-8"))["skipped_fragments"] == 3
    (d / "source.ts").unlink()  # gone: downloaded again and counted afresh
    cli.cmd_fetch(defaults, args)
    assert "skipped_fragments" not in json.loads((d / "meta.json").read_text(encoding="utf-8"))


def test_fixup_remuxes_a_fresh_download_and_stores_the_jump(defaults, tmp_path, monkeypatch, capsys):
    """#440: a download is taken as it comes unless asked; with --fixup the genpts remux grows the
    file by the jump and that is kept in meta.json."""
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    monkeypatch.setattr(kick.subprocess, "Popen", downloads(tmp_path))
    ff = FakeFf([BACKWARD, REPAIRED])
    monkeypatch.setattr(kick.subprocess, "run", ff)
    kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    remux = ff.remuxed()
    assert len(remux) == 1
    assert remux[0][:13] == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-fflags",
                             "+genpts", "-protocol_whitelist", "file", "-f", "mpegts", "-i",
                             str(tmp_path / "source.ts")]
    assert remux[0][13:-1] == ["-c", "copy", "-f", "mpegts"]
    assert Path(remux[0][-1]).name.startswith(".timeline-")  # a temp of its own, then replaced
    assert (tmp_path / "source.ts").read_bytes() == b"remuxed"
    assert not temps(tmp_path)
    # the probe, the container's stream list, its v:0 (that alone shows the jump), the remux, the
    # tmp probed, its stream list and both its streams' packets
    assert len(ff.calls) == 8
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"stream_id": "kick-1", "timeline_overlap_s": 821.7}
    assert "jumps back 821.7 s" in capsys.readouterr().out


def test_a_fresh_download_is_taken_as_it_comes_and_clears_an_old_jump(defaults, tmp_path, monkeypatch,
                                                                      capsys):
    """No probe, no remux: the timeline is transcribe's to check (#440). The key went with the
    old file."""
    (tmp_path / "meta.json").write_text(
        json.dumps({"stream_id": "kick-1", "timeline_overlap_s": 821.7}), encoding="utf-8")
    monkeypatch.setattr(kick.subprocess, "Popen", downloads(tmp_path, b"ts"))
    monkeypatch.setattr(kick.subprocess, "run", lambda *a, **kw: pytest.fail("probed"))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8")) == {"stream_id": "kick-1"}
    assert "timeline" not in capsys.readouterr().out


def test_fixup_rewrites_an_existing_source_without_downloading(defaults, tmp_path, monkeypatch, capsys):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    (tmp_path / "source.ts").write_bytes(b"broken")
    monkeypatch.setattr(kick.subprocess, "Popen", lambda *a, **kw: pytest.fail("downloaded again"))
    ff = FakeFf([BACKWARD, REPAIRED])
    monkeypatch.setattr(kick.subprocess, "run", ff)
    assert kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True) == tmp_path / "source.ts"
    assert len(ff.remuxed()) == 1
    assert (tmp_path / "source.ts").read_bytes() == b"remuxed"
    assert "jumps back 821.7 s" in capsys.readouterr().out
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))["timeline_overlap_s"] == 821.7


def test_fixup_on_a_file_already_in_order_clears_the_jump(defaults, tmp_path, monkeypatch, capsys):
    """#2: a source that doesn't jump is not remuxed at all — a 33 GB read and write to fix
    nothing — and the jump an earlier run stored goes, since this file has none."""
    (tmp_path / "meta.json").write_text(
        json.dumps({"stream_id": "kick-1", "timeline_overlap_s": 821.7}), encoding="utf-8")
    (tmp_path / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick.subprocess, "Popen", lambda *a, **kw: pytest.fail("downloaded again"))
    ff = FakeFf([REPAIRED], packets=IN_ORDER)
    monkeypatch.setattr(kick.subprocess, "run", ff)
    kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert not ff.remuxed()
    assert (tmp_path / "source.ts").read_bytes() == b"ts"  # byte for byte, untouched
    assert "already in order" in capsys.readouterr().out
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8")) == {"stream_id": "kick-1"}


def test_a_sub_tolerance_growth_stores_no_jump(defaults, tmp_path, monkeypatch, capsys):
    """A remux 0.5 s longer is ffprobe's rounding, not a jump: nothing goes in meta.json (the
    transcribe guard only reads more than TOLERANCE_S anyway)."""
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    (tmp_path / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick.subprocess, "run", FakeFf([BACKWARD, probe_json(33011.5)]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8")) == {"stream_id": "kick-1"}
    assert "its timeline is in order" in capsys.readouterr().out


@pytest.mark.parametrize("jump, stored", [(0.0, {}), (0.5, {}), (1.0, {}), (1.1, {"timeline_overlap_s": 1.1})])
def test_only_a_jump_bigger_than_the_tolerance_is_stored(tmp_path, jump, stored):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    kick._store_jump(tmp_path, jump)
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8")) == {"stream_id": "kick-1", **stored}


def test_a_failed_remux_keeps_the_source_and_says_so(defaults, tmp_path, monkeypatch):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    monkeypatch.setattr(kick.subprocess, "Popen", downloads(tmp_path))
    ff = FakeFf([BACKWARD], remux_code=1, remux_stderr="Conversion failed!\n")
    monkeypatch.setattr(kick.subprocess, "run", ff)
    with pytest.raises(ClipperError, match=r"a remux of source\.ts failed \(source\.ts kept\): "
                                           r"ffmpeg couldn't remux it \(exit 1\)$"):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert (tmp_path / "source.ts").read_bytes() == b"broken"
    assert not temps(tmp_path)
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8")) == {"stream_id": "kick-1"}


def test_a_failed_fixup_keeps_the_downloads_skip_count(defaults, tmp_path, monkeypatch):
    """#440: the skip count is written before the remux, since the tally it came from goes with
    the download: a fixup that fails must not lose it. The old jump is not kept: it described the
    file the download replaced, and this one's timeline is unjudged until a fixup says."""
    (tmp_path / "meta.json").write_text(
        json.dumps({"stream_id": "kick-1", "timeline_overlap_s": 821.7}), encoding="utf-8")
    monkeypatch.setattr(kick.subprocess, "Popen",
                        downloads(tmp_path, lines=["Skipping fragment 7\n", "Skipping fragment 8\n",
                                                   "[download] 100%\n"]))
    monkeypatch.setattr(kick.subprocess, "run",
                        FakeFf([BACKWARD], remux_code=1, remux_stderr="Conversion failed!\n"))
    with pytest.raises(ClipperError, match="a remux of source\\.ts failed"):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert not (tmp_path / "source.ts.skipped").exists()
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"stream_id": "kick-1", "skipped_fragments": 2}  # the old jump went with the file


def test_a_worker_running_the_stream_is_not_remuxed_under(defaults, tmp_path, monkeypatch):
    """The busy check takes the stream's short run lock (jsonstate.locked on .run.lock, as
    repairs.py does) and refuses a worker that is on it before the source is even probed."""
    from datetime import UTC, datetime

    (tmp_path / "source.ts").write_bytes(b"ts")
    (tmp_path / ".run.lock").write_text("nas:42", encoding="utf-8")
    (tmp_path / "run.json").write_text(json.dumps(
        {"worker": "nas:42", "heartbeat": datetime.now(UTC).isoformat()}), encoding="utf-8")
    monkeypatch.setattr(kick.subprocess, "run", lambda *a, **kw: pytest.fail("probed"))
    with pytest.raises(ClipperError, match=r"nas:42 is running it; --fixup when that is done$"):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert (tmp_path / "source.ts").read_bytes() == b"ts"
    assert (tmp_path / ".run.lock").read_text(encoding="utf-8") == "nas:42"  # still theirs


def test_the_run_lock_is_taken_only_briefly_not_across_the_remux(defaults, tmp_path, monkeypatch):
    """A 33 GB remux outlasts a lock (jsonstate's 120 s stale rule), so one held across it would
    be taken over anyway: it is taken for the busy check and the replace only. This is structure,
    not timing: the takeover lock (.run.lock.lock) is absent while ffmpeg writes, and there for
    the rename."""
    (tmp_path / "source.ts").write_bytes(b"broken")
    held = {}
    ff = FakeFf([BACKWARD, REPAIRED])
    inner = ff.__call__

    def run(args, **kw):
        if args[0] == "ffmpeg":  # mid-remux: nobody holds the stream's lock for us
            held["remux"] = (tmp_path / ".run.lock.lock").exists()
        return inner(args, **kw)

    real_replace = kick.os.replace

    def replace(tmp, path):
        if Path(path).name == "source.ts":  # not jsonstate's own meta.json replace
            held["replace"] = (tmp_path / ".run.lock.lock").exists()
        real_replace(tmp, path)

    monkeypatch.setattr(kick.subprocess, "run", run)
    monkeypatch.setattr(kick.os, "replace", replace)
    kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert held == {"remux": False, "replace": True}
    assert (tmp_path / "source.ts").read_bytes() == b"remuxed"


def test_a_worker_that_starts_during_the_remux_keeps_the_original(defaults, tmp_path, monkeypatch):
    """The replace re-checks: a worker on the stream by then is refused, the temp never goes in
    (it reads the old source until transcribe's guard stops it), and its wav is still the right
    one, so a refusal must not drop it."""
    from datetime import UTC, datetime

    (tmp_path / "source.ts").write_bytes(b"broken")
    (tmp_path / "audio.wav").write_bytes(b"wav of the old source")
    ff = FakeFf([BACKWARD, REPAIRED])
    inner = ff.__call__

    def run(args, **kw):
        if args[0] == "ffmpeg":  # a worker takes the stream while ffmpeg is writing
            (tmp_path / ".run.lock").write_text("nas:7", encoding="utf-8")
            (tmp_path / "run.json").write_text(json.dumps(
                {"worker": "nas:7", "heartbeat": datetime.now(UTC).isoformat()}), encoding="utf-8")
        return inner(args, **kw)

    monkeypatch.setattr(kick.subprocess, "run", run)
    with pytest.raises(ClipperError, match=r"nas:7 is running it; --fixup when that is done$"):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert (tmp_path / "source.ts").read_bytes() == b"broken"
    assert (tmp_path / "audio.wav").read_bytes() == b"wav of the old source"
    assert not temps(tmp_path)


def test_a_lock_a_killed_worker_left_behind_does_not_block_a_fixup(defaults, tmp_path, monkeypatch):
    """Its heartbeat says nobody is on the stream, so the dead lock is taken over like a new
    worker would take it (not forever refused)."""
    (tmp_path / "source.ts").write_bytes(b"broken")
    (tmp_path / ".run.lock").write_text("nas:42", encoding="utf-8")
    (tmp_path / "run.json").write_text(json.dumps(
        {"worker": "nas:42", "heartbeat": "2020-01-01T00:00:00+00:00"}), encoding="utf-8")
    ff = FakeFf([BACKWARD, REPAIRED])
    monkeypatch.setattr(kick.subprocess, "run", ff)
    kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert ff.remuxed()
    assert (tmp_path / "source.ts").read_bytes() == b"remuxed"


def test_a_lock_that_never_comes_free_is_one_line_not_a_traceback(defaults, tmp_path, monkeypatch):
    """#3: a run lock another process holds past jsonstate's 60 s wait is a ClipperError naming
    the stream, not a bare TimeoutError out of cmd_fetch (which two fixups hours apart reach)."""
    from contextlib import contextmanager

    (tmp_path / "source.ts").write_bytes(b"broken")

    @contextmanager
    def held(*a, **kw):
        raise TimeoutError("the lock is held by another process for over 60 s")
        yield  # pragma: no cover

    monkeypatch.setattr(kick.jsonstate, "locked", held)
    monkeypatch.setattr(kick.subprocess, "run", lambda *a, **kw: pytest.fail("probed"))
    with pytest.raises(ClipperError, match=r": another clippa is working on this stream "
                                           r"\(or the lock couldn't be taken\): the lock wasn't "
                                           r"free after 60 s$") as err:
        kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert str(tmp_path) not in str(err.value) and "held by another process" not in str(err.value)
    assert (tmp_path / "source.ts").read_bytes() == b"broken"


def test_a_lock_the_share_wont_let_be_made_is_one_line_too(defaults, tmp_path, monkeypatch):
    """A folder that refuses the lock file (a read-only share) is the same line, with the
    system's reason, and its own path is never carried out of here."""
    from contextlib import contextmanager

    (tmp_path / "source.ts").write_bytes(b"broken")

    @contextmanager
    def refused(*a, **kw):
        raise PermissionError(13, "Access is denied", str(tmp_path / ".run.lock.lock"))
        yield  # pragma: no cover

    monkeypatch.setattr(kick.jsonstate, "locked", refused)
    with pytest.raises(ClipperError, match=r"another clippa is working on this stream \(or the "
                                           r"lock couldn't be taken\): Access is denied$") as err:
        kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert str(tmp_path) not in str(err.value)


def test_a_fixup_sweeps_a_killed_remuxs_temp(defaults, tmp_path, monkeypatch, capsys):
    """#5: a killed remux's multi-GB temp beside the source is removed when the next --fixup
    starts; one young enough to be a remux running now is left."""
    (tmp_path / "source.ts").write_bytes(b"broken")
    old = tmp_path / ".timeline-dead.part"
    old.write_bytes(b"half a remux")
    then = time.time() - 2 * timeline.SWEEP_S
    os.utime(old, (then, then))
    fresh = tmp_path / ".timeline-live.part"
    fresh.write_bytes(b"half a remux")
    monkeypatch.setattr(kick.subprocess, "run", FakeFf([BACKWARD, REPAIRED]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert not old.exists() and fresh.exists()
    assert "removed a leftover remux temp (.timeline-dead.part)" in capsys.readouterr().out


def test_a_shorter_remux_is_refused_and_the_source_kept(defaults, tmp_path, monkeypatch):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    monkeypatch.setattr(kick.subprocess, "Popen", downloads(tmp_path))
    monkeypatch.setattr(kick.subprocess, "run", FakeFf([BACKWARD, probe_json(32011.0)]))
    with pytest.raises(ClipperError, match=r"a remux of source\.ts came out 1000\.0 s shorter "
                                           r"\(source\.ts kept\)$"):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert (tmp_path / "source.ts").read_bytes() == b"broken"  # the tmp never went in
    assert not temps(tmp_path)
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8")) == {"stream_id": "kick-1"}


def test_a_fixup_that_replaces_the_source_drops_the_old_wav(defaults, tmp_path, monkeypatch):
    """#444: audio.wav was decoded from the broken source (aresample stretches across the jump),
    so a repaired source decodes to different samples: the wav that is here no longer matches
    what the next transcribe would read, and has to go."""
    (tmp_path / "source.ts").write_bytes(b"broken")
    (tmp_path / "audio.wav").write_bytes(b"wav of the old source")
    monkeypatch.setattr(kick.subprocess, "run", FakeFf([BACKWARD, REPAIRED]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert (tmp_path / "source.ts").read_bytes() == b"remuxed"
    assert not (tmp_path / "audio.wav").exists()
    assert not (tmp_path / "audio.wav.stale").exists()  # the wav went, so the marker did too


def test_an_in_order_fixup_leaves_the_wav_alone(defaults, tmp_path, monkeypatch):
    """Nothing was rewritten, so the wav still describes the source: it is not thrown away."""
    (tmp_path / "source.ts").write_bytes(b"broken")
    (tmp_path / "audio.wav").write_bytes(b"wav")
    monkeypatch.setattr(kick.subprocess, "run", FakeFf([REPAIRED], packets=IN_ORDER))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert (tmp_path / "source.ts").read_bytes() == b"broken"  # untouched
    assert (tmp_path / "audio.wav").read_bytes() == b"wav"
    assert not (tmp_path / "audio.wav.stale").exists()  # nothing was replaced


def test_a_redownload_marks_the_wav_it_replaces_stale(defaults, tmp_path, monkeypatch):
    """#444: yt-dlp stamps source.ts with the VOD's own Last-Modified (there is no --no-mtime), and
    that can be older than an audio.wav already here, so the mtime rule would serve the previous
    file's audio for this source: the download says its wav is stale. A transcribe then decodes it
    again whatever the two times read."""
    (tmp_path / "audio.wav").write_bytes(b"wav of the source before this download")
    monkeypatch.setattr(kick.subprocess, "Popen", downloads(tmp_path, b"ts"))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert (tmp_path / "audio.wav.stale").exists()
    old = time.time_ns() - 3600 * 10**9  # the VOD's Last-Modified, hours before the wav was made
    os.utime(tmp_path / "source.ts", ns=(old, old))
    os.utime(tmp_path / "audio.wav", ns=(time.time_ns(), time.time_ns()))  # younger: kept, unmarked
    monkeypatch.setattr(kick.subprocess, "run",
                        lambda cmd, check: Path(cmd[-1]).write_bytes(b"the new source's audio"))
    assert transcribe.extract_audio(tmp_path) == tmp_path / "audio.wav"
    assert (tmp_path / "audio.wav").read_bytes() == b"the new source's audio"
    assert not (tmp_path / "audio.wav.stale").exists()


def test_a_failed_download_writes_no_stale_marker(defaults, tmp_path, monkeypatch):
    """Only a download that finished and left source.ts has a wav to mark: one that failed must not
    leave a marker for the next transcribe to obey."""
    (tmp_path / "audio.wav").write_bytes(b"wav")
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["ERROR: 403\n"], code=1))
    with pytest.raises(subprocess.CalledProcessError):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert not (tmp_path / "audio.wav.stale").exists()


def test_a_marker_the_share_will_not_write_does_not_fail_the_download(defaults, tmp_path, monkeypatch,
                                                                    capsys):
    """The marker only saves the next transcribe a decode (and a share that refuses it most likely
    keeps its times too), so it is one line on stderr and the download that worked stands."""
    (tmp_path / "audio.wav").write_bytes(b"wav of the previous source")
    monkeypatch.setattr(kick.subprocess, "Popen", downloads(tmp_path, b"ts"))
    real_touch = Path.touch

    def denied(self, *a, **kw):
        if self.name == "audio.wav.stale":
            raise PermissionError(13, "Access is denied")
        return real_touch(self, *a, **kw)

    monkeypatch.setattr(Path, "touch", denied)
    assert kick.fetch_video(tmp_path, {"source": "u"}, defaults) == tmp_path / "source.ts"
    assert (tmp_path / "source.ts").read_bytes() == b"ts"
    assert not (tmp_path / "audio.wav.stale").exists()
    assert (f"{tmp_path.name}: couldn't record that its audio.wav is stale; a transcribe may reuse "
            "it" in capsys.readouterr().err)


def test_a_refused_fixup_leaves_the_wav_alone(defaults, tmp_path, monkeypatch):
    """The remux was refused and the source kept, so its wav is still the right one."""
    (tmp_path / "source.ts").write_bytes(b"broken")
    (tmp_path / "audio.wav").write_bytes(b"wav")
    monkeypatch.setattr(kick.subprocess, "run", FakeFf([BACKWARD, probe_json(32011.0)]))
    with pytest.raises(ClipperError, match=r"shorter"):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert (tmp_path / "source.ts").read_bytes() == b"broken"
    assert (tmp_path / "audio.wav").read_bytes() == b"wav"


def test_a_repair_within_tolerance_still_drops_the_old_wav(defaults, tmp_path, monkeypatch):
    """#444 measured this: a 90.67 s excerpt remuxed to 91.11 s, a real rewrite whose growth
    (0.4 s) is inside TOLERANCE_S, so remux_verified reports 0.0 and a drop keyed on it would
    keep a wav decoded from the old source."""
    (tmp_path / "source.ts").write_bytes(b"broken")
    (tmp_path / "audio.wav").write_bytes(b"wav of the old source")
    monkeypatch.setattr(kick.subprocess, "run", FakeFf([BACKWARD, probe_json(33011.5)]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert (tmp_path / "source.ts").read_bytes() == b"remuxed"
    assert not (tmp_path / "audio.wav").exists()


def test_a_wav_the_share_will_not_remove_is_one_line_and_the_fixup_finishes(
        defaults, tmp_path, monkeypatch, capsys):
    """The source is replaced already when the wav is dropped: a share that refuses it must not
    raise (the jump still has to be stored), just say so once. The marker written before the
    replace is what keeps the stale wav from being read as a fresh one."""
    (tmp_path / "source.ts").write_bytes(b"broken")
    (tmp_path / "audio.wav").write_bytes(b"wav")
    monkeypatch.setattr(kick.subprocess, "run", FakeFf([BACKWARD, REPAIRED]))
    real_unlink = Path.unlink

    def denied(self, *a, **kw):
        if self.name == "audio.wav":
            raise PermissionError(13, "Access is denied")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", denied)
    kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert (tmp_path / "source.ts").read_bytes() == b"remuxed"
    assert (tmp_path / "audio.wav").read_bytes() == b"wav"
    assert (tmp_path / "audio.wav.stale").exists()
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))["timeline_overlap_s"] == 821.7
    assert (f"{tmp_path.name}: the old audio.wav couldn't be removed (Access is denied); "
            "it is marked stale, so the next transcribe decodes it again") in capsys.readouterr().out


def test_a_stale_marker_the_share_will_not_write_keeps_the_source(defaults, tmp_path, monkeypatch):
    """The marker is what tells transcribe a fast remux's wav is from the old source, so it is
    written before the replace: one that can't be is a refusal with the original still in place,
    and its wav, which still matches it, untouched."""
    (tmp_path / "source.ts").write_bytes(b"broken")
    (tmp_path / "audio.wav").write_bytes(b"wav of the old source")
    monkeypatch.setattr(kick.subprocess, "run", FakeFf([BACKWARD, REPAIRED]))
    real_touch = Path.touch

    def denied(self, *a, **kw):
        if self.name == "audio.wav.stale":
            raise PermissionError(13, "Access is denied")
        return real_touch(self, *a, **kw)

    replaces = []
    real_replace = kick.os.replace

    def replace(tmp, path):
        replaces.append(Path(path).name)
        real_replace(tmp, path)

    monkeypatch.setattr(Path, "touch", denied)
    monkeypatch.setattr(kick.os, "replace", replace)
    with pytest.raises(ClipperError, match=r"couldn't record that its audio\.wav is stale "
                                          r"\(Access is denied\); the source is not replaced$"):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert not replaces, "the source was replaced without the marker that says its wav is stale"
    assert (tmp_path / "source.ts").read_bytes() == b"broken"
    assert (tmp_path / "audio.wav").read_bytes() == b"wav of the old source"
    assert not temps(tmp_path)
    assert not (tmp_path / "meta.json").exists()


def test_a_resumed_download_keeps_an_old_jump_count(defaults, tmp_path, monkeypatch):
    (tmp_path / "meta.json").write_text(
        json.dumps({"stream_id": "kick-1", "timeline_overlap_s": 821.7}), encoding="utf-8")
    (tmp_path / "source.ts").write_bytes(b"partial")
    (tmp_path / "source.ts.ytdl").write_text("{}", encoding="utf-8")  # yt-dlp's resume point
    monkeypatch.setattr(kick.subprocess, "Popen", downloads(tmp_path, b"whole"))
    monkeypatch.setattr(kick.subprocess, "run", lambda *a, **kw: pytest.fail("probed"))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))["timeline_overlap_s"] == 821.7


def test_a_fixup_on_a_resume_writes_what_it_found(defaults, tmp_path, monkeypatch):
    """A --fixup run is the one checking this file: in order clears an earlier jump, whatever
    the resume rule would keep. It finishes the download first (the note is the resume point), so
    the remux repairs a whole file, never the partial that was here."""
    (tmp_path / "meta.json").write_text(
        json.dumps({"stream_id": "kick-1", "timeline_overlap_s": 821.7}), encoding="utf-8")
    (tmp_path / "source.ts").write_bytes(b"partial")
    (tmp_path / "source.ts.ytdl").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(kick.subprocess, "Popen", downloads(tmp_path, b"whole"))
    fake = FakeFf([REPAIRED], by_bytes={
        b"partial": (BACKWARD, JUMPS),  # what a download that stopped mid-file holds
        b"whole": (probe_json(33011.0), IN_ORDER),  # the finished file: one timeline, in order
    })
    monkeypatch.setattr(kick.subprocess, "run", fake)
    kick.fetch_video(tmp_path, {"source": "u"}, defaults, fixup=True)
    assert (tmp_path / "source.ts").read_bytes() == b"whole"  # the download finished, not the partial
    assert fake.remuxed() == []  # and what it left is in order: nothing to rewrite
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8")) == {"stream_id": "kick-1"}


def test_an_existing_source_is_not_even_probed_without_fixup(defaults, tmp_path, monkeypatch):
    (tmp_path / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick.subprocess, "Popen", lambda *a, **kw: pytest.fail("downloaded again"))
    monkeypatch.setattr(kick.subprocess, "run", lambda *a, **kw: pytest.fail("probed"))
    assert kick.fetch_video(tmp_path, {"source": "u"}, defaults) == tmp_path / "source.ts"


def test_fixup_with_no_video_repairs_the_source_already_here(defaults, tmp_path, monkeypatch):
    """--fixup --no-video downloads nothing but still repairs the source.ts in the folder: it
    used to be a silent no-op."""
    defaults["paths"]["streams"] = tmp_path
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"broken")
    monkeypatch.setattr(kick, "resolve",
                        lambda url, cfg: {"stream_id": "kick-1", "title": "t", "duration_s": 3600})
    monkeypatch.setattr(kick.subprocess, "Popen", lambda *a, **kw: pytest.fail("downloaded"))
    monkeypatch.setattr(kick.subprocess, "run", FakeFf([BACKWARD, REPAIRED]))
    url = "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=True, no_video=True, fixup=True))
    assert (d / "source.ts").read_bytes() == b"remuxed"
    assert json.loads((d / "meta.json").read_text(encoding="utf-8"))["timeline_overlap_s"] == 821.7


def test_fixup_refuses_a_source_that_is_still_downloading(defaults, tmp_path, monkeypatch):
    """#444 review: --fixup --no-video (and the api-down fallback) calls this, and it only checked
    that a source was there: a partial download with yt-dlp's resume note beside it was rewritten
    in place, the note left behind, and the next fetch resumed into the remuxed file. It refuses
    in one line instead; the plain fetch finishes the download, and --fixup then repairs it."""
    (tmp_path / "source.ts").write_bytes(b"partial")
    (tmp_path / "source.ts.ytdl").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(kick.subprocess, "Popen", lambda *a, **kw: pytest.fail("downloaded"))
    monkeypatch.setattr(kick.subprocess, "run", lambda *a, **kw: pytest.fail("probed or remuxed"))
    with pytest.raises(ClipperError) as err:
        kick.fixup(tmp_path, defaults)
    assert str(err.value).endswith(": its source is still downloading (source.ts.ytdl); "
                                   "let the fetch finish, then --fixup")
    assert str(tmp_path) not in str(err.value)
    assert (tmp_path / "source.ts").read_bytes() == b"partial"
    assert (tmp_path / "source.ts.ytdl").is_file()
    assert not temps(tmp_path)


def test_fixup_with_no_video_and_no_source_says_so(defaults, tmp_path, monkeypatch):
    """There is nothing to repair: a fixup doesn't fetch, so it says that rather than nothing."""
    defaults["paths"]["streams"] = tmp_path
    monkeypatch.setattr(kick, "resolve",
                        lambda url, cfg: {"stream_id": "kick-1", "title": "t", "duration_s": 3600})
    url = "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    with pytest.raises(ClipperError, match=r"kick-1: --fixup --no-video repairs the source\.ts "
                                           r"already here, and there is none"):
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=True, no_video=True, fixup=True))


def _api_refuses(*a, **kw):
    """kick.resolve when the VOD left the channel's recent list, or Kick throttles us."""
    raise ClipperError("019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b not in kino's recent videos (0 listed)")


@pytest.mark.parametrize("meta", [
    {"video_uuid": "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"},
    {"url": "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"},  # no uuid: its url
])
def test_a_fixup_repairs_what_is_on_disk_when_the_api_cannot_answer(defaults, tmp_path, monkeypatch,
                                                                    capsys, meta):
    """#444: transcribe's refusal prints `clippa fetch <url> --fixup`, and resolve is the one part
    that can fail for a VOD whose source is still here: a repair needs no metadata, so it falls
    back to the stream folder the url names (by its uuid, else by its url)."""
    defaults["paths"]["streams"] = tmp_path
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"stream_id": "kick-1", **meta}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    repaired = []
    monkeypatch.setattr(kick, "fixup", lambda path, cfg, **kw: repaired.append(path))
    monkeypatch.setattr(kick, "fetch_chat", lambda *a: pytest.fail("fetched the chat"))
    monkeypatch.setattr(kick, "fetch_video", lambda *a: pytest.fail("downloaded"))
    url = "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    # the refusal prints no --no-video, so neither does this run: the fallback repairs instead
    cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=True))
    assert repaired == [d]
    assert "kick-1: Kick's api couldn't resolve this video; repairing the source already here" \
        in capsys.readouterr().out


def test_a_fixup_fallback_with_no_source_says_the_api_is_the_problem(
        defaults, tmp_path, monkeypatch, capsys):
    """#444 review: the fallback runs kick.fixup, whose line told the user to drop --no-video: an
    option this run didn't pass, for a download it can't make because the api just failed. With no
    source.ts there is nothing to repair, so it says that instead."""
    defaults["paths"]["streams"] = tmp_path
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps(
        {"stream_id": "kick-1", "video_uuid": "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"}),
        encoding="utf-8")
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    monkeypatch.setattr(kick, "fetch_chat", lambda *a: pytest.fail("fetched the chat"))
    monkeypatch.setattr(kick, "fetch_video", lambda *a: pytest.fail("downloaded"))
    url = "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    with pytest.raises(ClipperError) as err:
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=True))
    msg = str(err.value)
    assert msg.startswith("kick-1: Kick's api couldn't resolve this video and there is no source.ts ")
    assert "try again when the api is back" in msg
    assert "--no-video" not in msg and str(tmp_path) not in msg and "http" not in msg
    assert "Kick's api couldn't resolve this video; repairing the source already here" \
        in capsys.readouterr().out


@pytest.mark.parametrize("match", ["uuid", "url"])
def test_a_fixup_refuses_to_guess_between_two_folders(defaults, tmp_path, monkeypatch, match):
    """#444: two folders carrying the same video (the same uuid, or the same stored url) mean
    repairing one and leaving the other broken, so it names them and asks for the extra copy to be
    moved away (never the url)."""
    defaults["paths"]["streams"] = tmp_path
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    url = f"https://kick.com/kino/videos/{uuid}"
    meta = {"video_uuid": uuid} if match == "uuid" else {"video_uuid": "another video", "url": url}
    for name in ("kick-1", "kick-2"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (tmp_path / name / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    monkeypatch.setattr(kick, "fixup", lambda *a: pytest.fail("repaired one of two"))
    with pytest.raises(ClipperError, match=r"^kick-1, kick-2: more than one folder holds this "
                                           r"video; move the extra copy away and try again$") as err:
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=True, fixup=True))
    assert uuid not in str(err.value)


def test_a_meta_that_is_not_an_object_is_skipped(defaults, tmp_path, monkeypatch):
    """A meta.json that is a list (an interrupted write, say) is no match, not a crash that hides
    the stream which does hold this video."""
    defaults["paths"]["streams"] = tmp_path
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    (tmp_path / "kick-0").mkdir()
    (tmp_path / "kick-0" / "meta.json").write_text("[]", encoding="utf-8")
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"video_uuid": uuid}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    repaired = []
    monkeypatch.setattr(kick, "fixup", lambda path, cfg, **kw: repaired.append(path))
    url = f"https://kick.com/kino/videos/{uuid}"
    cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=True, fixup=True))
    assert repaired == [d]


@pytest.mark.parametrize("meta", [
    {"url": "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b?autoplay=1"},
    {"video_uuid": "another video",
     "url": "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b?autoplay=1"},
])
def test_a_folder_that_only_nearly_matches_is_not_repaired(defaults, tmp_path, monkeypatch, meta):
    """#444: a query after the id is not a VOD url fetch can be pointed at (it could carry
    anything), so a stored url that only nearly matches is no match."""
    defaults["paths"]["streams"] = tmp_path
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"stream_id": "kick-1", **meta}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    monkeypatch.setattr(kick, "fixup", lambda *a: pytest.fail("repaired a folder that only nearly matches"))
    url = "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    with pytest.raises(ClipperError, match=r"not in kino's recent videos \(0 listed\)$") as err:
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=True, fixup=True))
    assert "autoplay" not in str(err.value)


def test_a_v7_url_reaches_a_folder_whose_stored_url_is_the_one_given(defaults, tmp_path, monkeypatch):
    """#444 review: `kick.resolve` matches a v7 url by the start time its id encodes and stores
    the video's own uuid, so for those the folder's uuid is not the one the url names. The url it
    was fetched from is (that is the one transcribe's printed command carries): repairing by it
    finds the folder where the uuid cannot."""
    defaults["paths"]["streams"] = tmp_path
    url = "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"video_uuid": "0123abcd-0000-7000-8000-000000000000",
                                             "url": url}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    repaired = []
    monkeypatch.setattr(kick, "fixup", lambda path, cfg, **kw: repaired.append(path))
    cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=True, fixup=True))
    assert repaired == [d]


def test_a_folder_with_a_matching_uuid_and_another_url_is_still_repaired(defaults, tmp_path, monkeypatch):
    """The two matches are alternatives: a folder whose uuid is the video's is repaired even where
    its stored url is not the one given (a url form the canonical check rejects, say)."""
    defaults["paths"]["streams"] = tmp_path
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"video_uuid": uuid,
                                             "url": "https://kick.com/kino/videos/something-else"}),
                                 encoding="utf-8")
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    repaired = []
    monkeypatch.setattr(kick, "fixup", lambda path, cfg, **kw: repaired.append(path))
    cli.cmd_fetch(defaults, argparse.Namespace(url=f"https://kick.com/kino/videos/{uuid}", no_chat=False,
                                               no_video=True, fixup=True))
    assert repaired == [d]


def test_a_source_url_with_a_query_is_never_matched_by_url(defaults, tmp_path, monkeypatch):
    """The url given is compared in its canonical form and the raw string never is: an url
    carrying a query has no canonical form, so a folder is not repaired by it."""
    defaults["paths"]["streams"] = tmp_path
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({
        "url": "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    monkeypatch.setattr(kick, "fixup", lambda *a: pytest.fail("repaired by a url with a query"))
    with pytest.raises(ClipperError, match=r"not in kino's recent videos \(0 listed\)$") as err:
        cli.cmd_fetch(defaults, argparse.Namespace(
            url="https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b?autoplay=1",
            no_chat=False, no_video=True, fixup=True))
    assert "autoplay" not in str(err.value)


def test_without_fixup_a_resolve_failure_is_not_repaired_anyway(defaults, tmp_path, monkeypatch):
    """The fallback is --fixup's: a plain fetch whose api call failed reports that, it doesn't
    quietly repair a folder instead of downloading."""
    defaults["paths"]["streams"] = tmp_path
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"video_uuid": uuid}), encoding="utf-8")
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    monkeypatch.setattr(kick, "fixup", lambda *a: pytest.fail("repaired without --fixup"))
    monkeypatch.setattr(kick, "fetch_video", lambda *a: pytest.fail("downloaded"))
    url = f"https://kick.com/kino/videos/{uuid}"
    with pytest.raises(ClipperError, match=r"not in kino's recent videos \(0 listed\)$"):
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=True, fixup=False))


def test_a_fixup_with_no_folder_on_disk_raises_what_the_api_said(defaults, tmp_path, monkeypatch):
    """Nothing here to repair: the api's own error is the one worth showing, not a new one."""
    streams = tmp_path / "streams"
    (streams / "kick-9").mkdir(parents=True)
    (streams / "kick-9" / "meta.json").write_text(json.dumps({"video_uuid": "someone else"}), encoding="utf-8")
    defaults["paths"]["streams"] = streams
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    monkeypatch.setattr(kick, "fixup", lambda *a: pytest.fail("repaired the wrong stream"))
    url = "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    with pytest.raises(ClipperError, match=r"not in kino's recent videos \(0 listed\)$"):
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=True, fixup=True))
    defaults["paths"]["streams"] = tmp_path / "no-such-folder"  # an install with no streams yet
    with pytest.raises(ClipperError, match=r"not in kino's recent videos \(0 listed\)$"):
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=True, fixup=True))


def test_a_fixup_on_an_url_that_names_no_vod_still_says_so(defaults, tmp_path, monkeypatch):
    """The fallback reads the uuid out of the url, so a Kick url without one is refused by
    parse_url rather than repaired by a guess, and the api's own error is not the one shown."""
    defaults["paths"]["streams"] = tmp_path
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"stream_id": "kick-1", "url": "https://kick.com/kino"}),
                                 encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    monkeypatch.setattr(kick, "fixup", lambda *a: pytest.fail("repaired a url that names no video"))
    with pytest.raises(ClipperError, match=r"^not a Kick VOD url: https://kick\.com/kino "
                                           r"\(https://kick\.com/") as err:
        cli.cmd_fetch(defaults, argparse.Namespace(url="https://kick.com/kino", no_chat=False,
                                                   no_video=False, fixup=True))
    assert "recent videos" not in str(err.value)  # parse_url's refusal, not the api's
    assert (d / "source.ts").read_bytes() == b"ts"


def test_a_truncated_meta_json_is_no_match_and_the_other_folder_is_repaired(
        defaults, tmp_path, monkeypatch):
    """A meta.json caught mid-write (`{`) is no match, not a crash that hides the stream which
    does hold this video."""
    defaults["paths"]["streams"] = tmp_path
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    (tmp_path / "kick-0").mkdir()
    (tmp_path / "kick-0" / "meta.json").write_text("{", encoding="utf-8")
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"video_uuid": uuid}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    repaired = []
    monkeypatch.setattr(kick, "fixup", lambda path, cfg, **kw: repaired.append(path))
    url = f"https://kick.com/kino/videos/{uuid}"
    cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=True, fixup=True))
    assert repaired == [d]


def test_a_streams_root_that_will_not_resolve_is_no_match(defaults, tmp_path, monkeypatch):
    """#444: a root that is a link loop (or swapped into one) makes resolve raise RuntimeError,
    not OSError: that is no match, so the api's own error is raised rather than a traceback out
    of cmd_fetch. The folder and its source stay as they are."""
    defaults["paths"]["streams"] = tmp_path
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"video_uuid": uuid}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    monkeypatch.setattr(kick, "fixup", lambda *a: pytest.fail("repaired a root that won't resolve"))
    real = Path.resolve

    def loop(self, *a, **kw):
        if self == tmp_path:
            raise RuntimeError("Symlink loop from 'streams' to 'streams'")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "resolve", loop)
    url = f"https://kick.com/kino/videos/{uuid}"
    assert cli._stream_for_vod(defaults, url) is None
    with pytest.raises(ClipperError, match=r"not in kino's recent videos \(0 listed\)$"):
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=True))
    assert (d / "source.ts").read_bytes() == b"ts"


def test_a_symlinked_folder_is_not_a_stream_to_repair(defaults, tmp_path, monkeypatch, link_or_skip):
    """#444: a link planted in the streams folder is not a stream folder: repairing through it
    would rewrite source.ts and unlink audio.wav outside the streams tree instead."""
    streams = tmp_path / "streams"
    streams.mkdir()
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "meta.json").write_text(json.dumps({"video_uuid": uuid}), encoding="utf-8")
    (elsewhere / "source.ts").write_bytes(b"ts")
    link_or_skip(elsewhere, streams / "kick-1", directory=True)
    assert (streams / "kick-1").is_symlink()
    defaults["paths"]["streams"] = streams
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    monkeypatch.setattr(kick, "fixup", lambda *a: pytest.fail("repaired through a link"))
    url = f"https://kick.com/kino/videos/{uuid}"
    with pytest.raises(ClipperError, match=r"not in kino's recent videos \(0 listed\)$"):
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=True))
    assert (elsewhere / "source.ts").read_bytes() == b"ts"


def test_a_real_folder_beside_a_linked_one_is_still_repaired(defaults, tmp_path, monkeypatch, link_or_skip):
    """Skipping the link must not skip the stream that is really here."""
    streams = tmp_path / "streams"
    streams.mkdir()
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "meta.json").write_text(json.dumps({"video_uuid": "someone else"}), encoding="utf-8")
    link_or_skip(elsewhere, streams / "kick-0", directory=True)
    assert (streams / "kick-0").is_symlink()
    d = streams / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"video_uuid": uuid}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    defaults["paths"]["streams"] = streams
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    repaired = []
    monkeypatch.setattr(kick, "fixup", lambda path, cfg, **kw: repaired.append(path))
    url = f"https://kick.com/kino/videos/{uuid}"
    cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=True, fixup=True))
    assert repaired == [d]


def test_a_link_inside_the_tree_is_not_a_second_stream(defaults, tmp_path, monkeypatch, link_or_skip):
    """#444: a link is not a stream folder even when it points inside the tree, where the
    is_relative_to check would accept it: repairing both would rewrite one folder's source.ts
    twice and drop its audio.wav. Only the real folder is repaired, and its files are untouched."""
    streams = tmp_path / "streams"
    d = streams / "kick-1"
    d.mkdir(parents=True)
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    (d / "meta.json").write_text(json.dumps({"video_uuid": uuid}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    (d / "audio.wav").write_bytes(b"wav")
    link_or_skip(d, streams / "kick-0", directory=True)
    assert (streams / "kick-0").is_symlink()
    defaults["paths"]["streams"] = streams
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    repaired = []
    monkeypatch.setattr(kick, "fixup", lambda path, cfg, **kw: repaired.append(path))
    url = f"https://kick.com/kino/videos/{uuid}"
    cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=True, fixup=True))
    assert [p.name for p in repaired] == ["kick-1"]
    assert (d / "source.ts").read_bytes() == b"ts" and (d / "audio.wav").read_bytes() == b"wav"


@pytest.mark.parametrize("boom", [RuntimeError("Symlink loop"), OSError("can't read")])
def test_a_candidate_whose_resolve_raises_is_skipped(defaults, tmp_path, monkeypatch, boom):
    """#444: a folder whose own resolve raises (a link loop inside it, a path the share can't
    read) is not one of ours: it is skipped, so the folder that is really here is still repaired
    rather than the whole lookup coming back empty."""
    streams = tmp_path / "streams"
    streams.mkdir()
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    for name in ("kick-0", "kick-1"):
        (streams / name).mkdir()
        (streams / name / "meta.json").write_text(json.dumps({"video_uuid": uuid}), encoding="utf-8")
        (streams / name / "source.ts").write_bytes(b"ts")
    defaults["paths"]["streams"] = streams
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    repaired = []
    monkeypatch.setattr(kick, "fixup", lambda path, cfg, **kw: repaired.append(path))
    real = Path.resolve

    def resolve(self, *a, **kw):
        if self == streams / "kick-0":
            raise boom
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "resolve", resolve)
    url = f"https://kick.com/kino/videos/{uuid}"
    cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=True, fixup=True))
    assert [p.name for p in repaired] == ["kick-1"]
    assert (streams / "kick-0" / "source.ts").read_bytes() == b"ts"


def test_an_entry_the_share_won_t_stat_is_skipped_and_the_others_are_searched(
        defaults, tmp_path, monkeypatch):
    """#444 review (Grok): the candidate list tested every entry inside one try, and on 3.12
    `is_link` and `is_dir` re-raise a refused stat: one entry nobody can read took the whole
    lookup with it, so the api-down fallback found nothing and gave up before the folder that
    holds the VOD."""
    streams = tmp_path / "streams"
    d = streams / "kick-1"
    d.mkdir(parents=True)
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    (d / "meta.json").write_text(json.dumps({"video_uuid": uuid}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    (streams / "kick-0").mkdir()  # an entry the share won't answer for
    defaults["paths"]["streams"] = streams
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    repaired = []
    monkeypatch.setattr(kick, "fixup", lambda path, cfg, **kw: repaired.append(path))
    real_lstat = Path.lstat

    def blind_lstat(self):
        if self == streams / "kick-0":
            raise PermissionError(13, "Access is denied")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", blind_lstat)
    url = f"https://kick.com/kino/videos/{uuid}"
    cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=True, fixup=True))
    assert repaired == [d]


def test_a_streams_folder_that_cannot_be_listed_is_no_match(defaults, tmp_path, monkeypatch):
    """#444 review: the folder itself is listed in its own try, so a share that won't list it is
    no match (the api's error is raised) rather than a traceback out of the fallback."""
    streams = tmp_path / "streams"
    defaults["paths"]["streams"] = streams
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    monkeypatch.setattr(kick, "fixup", lambda *a: pytest.fail("repaired with no streams folder"))
    real_iterdir = Path.iterdir

    def denied(self):
        if self == streams:
            raise PermissionError(13, "Access is denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", denied)
    url = "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    assert cli._stream_for_vod(defaults, url) is None
    with pytest.raises(ClipperError, match=r"not in kino's recent videos \(0 listed\)$"):
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=True))


def test_a_folder_that_resolves_outside_is_not_repaired(defaults, tmp_path, monkeypatch):
    """#444: a real folder whose resolve lands outside the streams tree (a mount, a share that
    answers with another path) is not one of ours: nothing is repaired and the api's error is the
    one raised."""
    streams = tmp_path / "streams"
    d = streams / "kick-1"
    d.mkdir(parents=True)
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    (d / "meta.json").write_text(json.dumps({"video_uuid": uuid}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    assert not d.is_symlink()  # a real folder: only the is_relative_to guard can drop it
    defaults["paths"]["streams"] = streams
    monkeypatch.setattr(kick, "resolve", _api_refuses)
    monkeypatch.setattr(kick, "fixup", lambda *a: pytest.fail("repaired a folder outside the tree"))
    real = Path.resolve

    def outside(self, *a, **kw):
        if self == d:
            return tmp_path / "elsewhere" / "kick-1"
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "resolve", outside)
    url = f"https://kick.com/kino/videos/{uuid}"
    with pytest.raises(ClipperError, match=r"not in kino's recent videos \(0 listed\)$"):
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=True))
    assert (d / "source.ts").read_bytes() == b"ts"


def test_cmd_fetch_passes_fixup_through_and_keeps_the_old_jump(defaults, tmp_path, monkeypatch):
    defaults["paths"]["streams"] = tmp_path
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(
        json.dumps({"stream_id": "kick-1", "timeline_overlap_s": 821.7}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick, "resolve", lambda url, cfg: {"stream_id": "kick-1", "title": "t", "duration_s": 3600})
    monkeypatch.setattr(kick, "fetch_chat", lambda *a: None)
    seen = []
    monkeypatch.setattr(kick, "fetch_video", lambda *a: seen.append(a))
    url = "https://kick.com/kino/videos/abc123"
    cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=True))
    # a --fixup is the fourth argument, and is off unless asked for
    assert seen == [(d, {"stream_id": "kick-1", "title": "t", "duration_s": 3600,
                         "timeline_overlap_s": 821.7}, defaults, True)]
    cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=False))
    assert seen[-1][3] is False
    assert json.loads((d / "meta.json").read_text(encoding="utf-8"))["timeline_overlap_s"] == 821.7


@pytest.mark.parametrize("no_video", [True, False])
def test_a_chat_that_cannot_be_fetched_does_not_stop_a_fixup(defaults, tmp_path, monkeypatch, capsys,
                                                             no_video):
    """#444 review: the command transcribe's refusal prints carries no --no-chat, so a 429 or a
    dead chat endpoint would raise before the repair ran. With --fixup it is said in one line and
    the repair goes ahead (the chat is not what is being repaired)."""
    defaults["paths"]["streams"] = tmp_path
    d = tmp_path / "kick-1"
    d.mkdir()
    monkeypatch.setattr(kick, "resolve",
                        lambda url, cfg: {"stream_id": "kick-1", "title": "t", "duration_s": 3600})

    def no_chat(*a):
        raise ClipperError("Kick wouldn't answer for the chat (429)")

    monkeypatch.setattr(kick, "fetch_chat", no_chat)
    done = []
    monkeypatch.setattr(kick, "fixup", lambda path, cfg, **kw: done.append(("fixup", path)))
    monkeypatch.setattr(kick, "fetch_video", lambda path, m, c, f: done.append(("video", path, f)))
    url = "https://kick.com/kino/videos/abc123"
    cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=no_video, fixup=True))
    assert done == ([("fixup", d)] if no_video else [("video", d, True)])
    assert "kick-1: the chat couldn't be fetched; repairing the source anyway" in capsys.readouterr().out


def test_a_chat_that_cannot_be_fetched_still_fails_a_fetch_without_fixup(defaults, tmp_path, monkeypatch):
    """Nothing follows a failed chat without --fixup, so its error is the one to show (and nothing
    is repaired behind it)."""
    defaults["paths"]["streams"] = tmp_path
    (tmp_path / "kick-1").mkdir()
    monkeypatch.setattr(kick, "resolve",
                        lambda url, cfg: {"stream_id": "kick-1", "title": "t", "duration_s": 3600})

    def no_chat(*a):
        raise ClipperError("Kick wouldn't answer for the chat (429)")

    monkeypatch.setattr(kick, "fetch_chat", no_chat)
    monkeypatch.setattr(kick, "fixup", lambda *a: pytest.fail("repaired without --fixup"))
    monkeypatch.setattr(kick, "fetch_video", lambda *a: pytest.fail("downloaded after a failed chat"))
    url = "https://kick.com/kino/videos/abc123"
    with pytest.raises(ClipperError, match=r"wouldn't answer for the chat"):
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=False))


def test_a_linked_stream_folder_is_refused_even_when_the_api_resolves(defaults, tmp_path, monkeypatch,
                                                                     link_or_skip):
    """#444: a successful resolve names the folder to fetch into, and mkdir(exist_ok=True) takes a
    link as one: yt-dlp's download, the remux's replace and the wav's removal would all land where
    it points. Nothing is written through it."""
    streams = tmp_path / "streams"
    streams.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link_or_skip(elsewhere, streams / "kick-1", directory=True)
    defaults["paths"]["streams"] = streams
    monkeypatch.setattr(kick, "resolve",
                        lambda url, cfg: {"stream_id": "kick-1", "title": "t", "duration_s": 3600})
    monkeypatch.setattr(kick, "fetch_chat", lambda *a: pytest.fail("chat through a link"))
    monkeypatch.setattr(kick, "fetch_video", lambda *a: pytest.fail("downloaded through a link"))
    url = "https://kick.com/kino/videos/abc123"
    with pytest.raises(ClipperError) as err:
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=False))
    assert str(err.value) == ("kick-1: that stream folder is a link or outside the streams folder; "
                              "refusing to fetch into it")
    assert str(tmp_path) not in str(err.value)
    assert list(elsewhere.iterdir()) == []


def test_a_junction_at_the_stream_folder_is_refused(defaults, tmp_path, monkeypatch, junction_or_skip):
    """A junction needs no privilege on Windows and is no symlink: mkdir(exist_ok=True) takes it as
    a folder, so the refusal has to be is_link's, not is_symlink's."""
    streams = tmp_path / "streams"
    streams.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    junction_or_skip(elsewhere, streams / "kick-1")
    defaults["paths"]["streams"] = streams
    monkeypatch.setattr(kick, "resolve",
                        lambda url, cfg: {"stream_id": "kick-1", "title": "t", "duration_s": 3600})
    monkeypatch.setattr(kick, "fetch_video", lambda *a: pytest.fail("downloaded through a junction"))
    url = "https://kick.com/kino/videos/abc123"
    with pytest.raises(ClipperError, match="that stream folder is a link or outside"):
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=True, no_video=False, fixup=False))
    assert list(elsewhere.iterdir()) == []


def test_a_stream_folder_that_resolves_outside_is_refused(defaults, tmp_path, monkeypatch):
    """A real folder whose resolve lands outside the streams tree (a mount, a share answering with
    another path) is not one of ours: the fetch is refused before the folder is written into."""
    streams = tmp_path / "streams"
    d = streams / "kick-1"
    d.mkdir(parents=True)
    defaults["paths"]["streams"] = streams
    monkeypatch.setattr(kick, "resolve",
                        lambda url, cfg: {"stream_id": "kick-1", "title": "t", "duration_s": 3600})
    monkeypatch.setattr(kick, "fetch_chat", lambda *a: pytest.fail("chat into a folder up a mount"))
    monkeypatch.setattr(kick, "fetch_video", lambda *a: pytest.fail("downloaded up a mount"))
    real = Path.resolve

    def resolve(self, *a, **kw):
        if self == d:
            return tmp_path / "elsewhere" / "kick-1"
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "resolve", resolve)
    url = "https://kick.com/kino/videos/abc123"
    with pytest.raises(ClipperError, match="that stream folder is a link or outside"):
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=False))
    assert list(d.iterdir()) == []


@pytest.mark.parametrize("maker", ["link_or_skip", "junction_or_skip"])
def test_fixup_refuses_a_linked_stream_folder(defaults, tmp_path, monkeypatch, request, maker):
    """#444: a caller reaches kick.fixup directly (cmd_fetch's fallback, a script), so the guard is
    there too, and it comes before the sweep that would unlink inside the target."""
    make = request.getfixturevalue(maker)
    streams = tmp_path / "streams"
    streams.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "source.ts").write_bytes(b"ts")
    make(elsewhere, streams / "kick-1", directory=True)
    monkeypatch.setattr(kick.subprocess, "run", lambda *a, **kw: pytest.fail("probed through a link"))
    with pytest.raises(ClipperError) as err:
        kick.fixup(streams / "kick-1", defaults)
    assert str(err.value) == ("kick-1: the stream folder is a link; refusing to write its source "
                              "through it")
    assert str(tmp_path) not in str(err.value)
    assert (elsewhere / "source.ts").read_bytes() == b"ts"


@pytest.mark.parametrize("maker", ["link_or_skip", "junction_or_skip"])
def test_fetch_video_refuses_a_linked_stream_folder(defaults, tmp_path, monkeypatch, request, maker):
    """A download through a link is refused before yt-dlp runs and before anything is swept, so
    the folder it names is left as it was."""
    make = request.getfixturevalue(maker)
    streams = tmp_path / "streams"
    streams.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    old = (elsewhere / ".timeline-killed.part")
    old.write_bytes(b"a killed remux's temp")
    stamp = time.time() - 2 * timeline.SWEEP_S
    os.utime(old, (stamp, stamp))  # old enough for the sweep to take it
    make(elsewhere, streams / "kick-1", directory=True)
    monkeypatch.setattr(kick.subprocess, "Popen", lambda *a, **kw: pytest.fail("downloaded through a link"))
    with pytest.raises(ClipperError, match="the stream folder is a link"):
        kick.fetch_video(streams / "kick-1", {"source": "u"}, defaults)
    assert old.exists()  # the sweep never ran inside the target
    assert old.read_bytes() == b"a killed remux's temp"


@pytest.mark.parametrize("repair", ["fixup", "fetch_video"])
def test_a_linked_source_is_refused_before_anything_reads_it(defaults, tmp_path, monkeypatch,
                                                             link_or_skip, repair):
    """#444: fixup only asked exists() (which follows a link) and remux_verified never checked, so
    ffprobe and the remux's replace would work on whatever the link names. The guard comes first:
    no tool runs and the target keeps its bytes."""
    d = tmp_path / "kick-1"
    d.mkdir()
    elsewhere = tmp_path / "elsewhere.ts"
    elsewhere.write_bytes(b"someone else's video")
    link_or_skip(elsewhere, d / "source.ts")
    monkeypatch.setattr(kick.subprocess, "run", lambda *a, **kw: pytest.fail("probed the link"))
    monkeypatch.setattr(kick.subprocess, "Popen", lambda *a, **kw: pytest.fail("downloaded over a link"))
    with pytest.raises(ClipperError) as err:
        if repair == "fixup":
            kick.fixup(d, defaults)
        else:
            kick.fetch_video(d, {"source": "u"}, defaults)
    assert str(err.value) == "kick-1: its source.ts is a link, not a file; refusing to read it"
    assert str(tmp_path) not in str(err.value)
    assert (d / "source.ts").is_symlink() and elsewhere.read_bytes() == b"someone else's video"


@pytest.mark.parametrize("url, site", [
    ("https://www.twitch.tv/videos/123", "Twitch"),
    ("https://youtu.be/abc", "YouTube"),
    ("https://www.youtube.com/watch?v=abc", "YouTube"),
    ("https://vimeo.com/123", "vimeo.com"),
])
def test_fixup_is_refused_for_a_source_it_could_not_repair(defaults, monkeypatch, url, site):
    """#440: --fixup's advice is only printed for Kick, so it is refused elsewhere rather than
    quietly doing nothing (and no download is started to find out)."""
    monkeypatch.setattr(kick, "resolve", lambda *a: pytest.fail("downloaded anyway"))
    with pytest.raises(ClipperError, match=rf"^--fixup is for Kick sources \(the remux is an mpegts "
                                           rf"copy\); {re.escape(site)} sources are not repaired "
                                           rf"this way$"):
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=True))


@pytest.mark.parametrize("url", [
    r"\\server\share\kick.com\x",  # a UNC path
    "file:///C:/streams/x",  # a local path as a url
    "?token=x",  # a query on its own
])
def test_a_fixup_refusal_never_echoes_what_it_was_given(defaults, monkeypatch, url):
    """#440: what `_fetch_host` reads out of an url can be a path, a share or a query, and the
    message is logged, said in a chat and stored in run.json: a string that names no plain
    hostname is "that url", and none of its characters come back."""
    monkeypatch.setattr(kick, "resolve", lambda *a: pytest.fail("downloaded anyway"))
    with pytest.raises(ClipperError) as err:
        cli.cmd_fetch(defaults, argparse.Namespace(url=url, no_chat=False, no_video=False, fixup=True))
    assert str(err.value) == ("--fixup is for Kick sources (the remux is an mpegts copy); "
                              "that url is not repaired this way")
    for leak in ("\\", "/", "?", "#", "token", "server", "share", "C:"):
        assert leak not in str(err.value)


def test_a_vod_url_with_a_backslash_for_a_channel_is_not_canonical():
    """#440: `..\\..\\secrets` full-matched and would have reached transcribe's message and
    run.json; only a plain channel slug is a VOD url `clippa fetch` should be pointed at."""
    uuid = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
    assert kick.canonical_vod_url(f"https://kick.com/..\\..\\secrets/videos/{uuid}") == ""
    assert kick.canonical_vod_url(f"https://kick.com/../secrets/videos/{uuid}") == ""
    good = f"https://kick.com/kino/videos/{uuid}"
    assert kick.canonical_vod_url(good) == good

