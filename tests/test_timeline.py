"""timeline: ffprobe's format as it's read, and the remux's temp-then-replace (#440): the temp
is a fresh file of its own, probed, and only a result that lost nothing goes in place of the
original. Offline: ffprobe and ffmpeg are stand-ins."""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from clippa import media, timeline
from clippa.errors import ClipperError


def probed(duration, start="62.016667"):
    """ffprobe's JSON as `probe` reads it: the container's start_time and duration (None where
    ffprobe left the value out)."""
    return {"format": {"start_time": start,
                       "duration": None if duration is None else str(duration)}}


JUMPS = "880.000000\n883.833333\n62.033333\n62.066667\n"  # #440's jump, as csv=p=0 line per packet
IN_ORDER = "62.033000,\n62.073000,\n62.113000,\n"  # ffprobe 8's real form: its MPEGTS side data field
STREAMS = json.dumps({"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]})


def ff(monkeypatch, probes, remux_code=0, packets=JUMPS, fixed=IN_ORDER):
    """subprocess.run as ffprobe (these answer, in turn), ffprobe's packet list (`packets` for the
    source and `fixed` for the remux's temp, which ffprobe 8 writes with its side-data field), its
    stream list (a video and an audio stream) and ffmpeg (this exit code, writing its temp when it
    succeeds); returns the commands run. The source jumps by default, so the remux is reached."""
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "ffprobe":
            if "packet=pts_time" in cmd:  # the list the monotonicity check reads
                temp = Path(cmd[-1]).name.startswith(".timeline-")
                return subprocess.CompletedProcess(cmd, 0, fixed if temp else packets, "")
            if "stream=codec_type" in cmd:  # what the container holds, for a missing stream's sake
                return subprocess.CompletedProcess(cmd, 0, STREAMS, "")
            return subprocess.CompletedProcess(cmd, 0, probes.pop(0), "")
        if not remux_code:
            Path(cmd[-1]).write_bytes(b"new")
        return subprocess.CompletedProcess(cmd, remux_code, "", "boom" if remux_code else "")

    monkeypatch.setattr(timeline.subprocess, "run", run)
    return calls


def source(tmp_path):
    src = tmp_path / "source.ts"
    src.write_bytes(b"old")
    return src


def temps(tmp_path):
    """The remux's temp files left in the stream's folder."""
    return sorted(tmp_path.glob(".timeline-*.part"))


def test_probe_asks_ffprobe_for_the_containers_own_timeline(monkeypatch, tmp_path):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps(probed(33011.216)), "")

    monkeypatch.setattr(timeline.subprocess, "run", run)
    assert timeline.probe(tmp_path / "source.ts") == {"start_time": pytest.approx(62.016667),
                                                      "duration": pytest.approx(33011.216)}
    # -protocol_whitelist file before the input: a .playlist named source.ts can't be fetched
    assert seen["cmd"] == ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries",
                           "format=start_time,duration", "-of", "json", str(tmp_path / "source.ts")]


def test_probe_keeps_a_value_ffprobe_left_out_as_none(monkeypatch, tmp_path):
    out = json.dumps({"format": {"start_time": "N/A"}})
    monkeypatch.setattr(timeline.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, out, ""))
    assert timeline.probe(tmp_path / "source.ts") == {"start_time": None, "duration": None}


def test_probe_on_what_isnt_json_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(timeline.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "<html></html>", ""))
    with pytest.raises(ClipperError, match=r"JSON for source\.ts didn't parse"):
        timeline.probe(tmp_path / "source.ts")


def test_probe_says_why_ffprobe_refused_without_echoing_it(monkeypatch, tmp_path):
    """#440: a tool's line is not spliced into our message, which is logged, said in a chat and
    stored in run.json: the exit code says ffprobe failed, and nothing of stderr (a path, a url a
    playlist named) is carried out of here. Windows hands ffprobe's negative errno back unsigned
    (0xBEBEBE37), so the message says the signed value a shell shows, not 3199971767."""
    monkeypatch.setattr(timeline.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3199971767, "",
                                                                      "Invalid data found\n"))
    with pytest.raises(ClipperError,
                       match=r"^source\.ts: ffprobe couldn't read it \(exit -1094995529\)$"):
        timeline.probe(tmp_path / "source.ts")


def test_a_path_or_url_ffprobe_echoes_never_reaches_the_message(monkeypatch, tmp_path):
    """A stderr byte for byte: the file's path, and an http url an HLS playlist would name, are
    both kept out of everything `probe` raises."""
    path = tmp_path / "source.ts"
    line = f"{path}: Invalid data found when processing input: https://example.test/master.m3u8\n"
    monkeypatch.setattr(timeline.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", line))
    with pytest.raises(ClipperError) as err:
        timeline.probe(path)
    for leak in (str(tmp_path), "\\", "/", "http"):
        assert leak not in str(err.value)


def test_ffprobe_and_ffmpeg_are_read_even_with_a_byte_their_encoding_lacks(monkeypatch, tmp_path):
    """A stderr byte no encoding spells is replaced, never a UnicodeDecodeError out of the stage
    (which would leave the remux's temp behind, #440)."""
    seen = {}

    def run(cmd, **kw):
        seen.update(kw)
        if cmd[0] == "ffprobe":
            if "packet=pts_time" in cmd:
                temp = Path(cmd[-1]).name.startswith(".timeline-")
                return subprocess.CompletedProcess(cmd, 0, IN_ORDER if temp else JUMPS, "")
            if "stream=codec_type" in cmd:
                return subprocess.CompletedProcess(cmd, 0, STREAMS, "")
            return subprocess.CompletedProcess(cmd, 0, json.dumps(probed(33011.0)), "")
        Path(cmd[-1]).write_bytes(b"new")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(timeline.subprocess, "run", run)
    timeline.remux_verified(source(tmp_path), "kick-1")
    assert seen["errors"] == "replace"



@pytest.mark.parametrize("duration", ["NaN", "inf", "-inf"])
def test_a_container_of_nan_or_inf_has_no_duration(monkeypatch, tmp_path, duration):
    """#440: nan is false against every comparison and inf passes any length check by accident:
    neither is a length, so the timeline can't be judged."""
    out = json.dumps({"format": {"start_time": "0", "duration": duration}})
    monkeypatch.setattr(timeline.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, out, ""))
    assert timeline.probe(tmp_path / "source.ts")["duration"] is None
    with pytest.raises(ClipperError, match="container duration"):
        timeline.container_duration(timeline.probe(tmp_path / "source.ts"))


@pytest.mark.parametrize("duration", ["NaN", "inf"])
def test_nothing_is_remuxed_or_replaced_when_the_probe_is_not_a_number(tmp_path, monkeypatch, duration):
    src = source(tmp_path)
    calls = ff(monkeypatch, [json.dumps(probed(duration))])
    with pytest.raises(ClipperError, match="container duration"):
        timeline.remux_verified(src, "kick-1")
    assert [c[0] for c in calls] == ["ffprobe"]
    assert src.read_bytes() == b"old"
    assert not temps(tmp_path)


def test_a_container_without_a_duration_cant_be_judged():
    assert timeline.container_duration({"start_time": 62.0, "duration": 33011.216}) == pytest.approx(33011.216)
    with pytest.raises(ClipperError, match="container duration"):
        timeline.container_duration({"start_time": 62.0, "duration": None})


def test_a_jumping_file_is_remuxed_and_its_growth_returned(tmp_path, monkeypatch, capsys):
    """kick-132975332: a container of 33011 s whose audio runs to 33833 s is 821.7 s of jump, and
    the genpts remux grows it by that much."""
    src = source(tmp_path)
    calls = ff(monkeypatch, [json.dumps(probed(33011.0)), json.dumps(probed(33832.7))])
    assert timeline.remux_verified(src, "kick-1") == 821.7
    # the probe, then the source's order check (its stream list, its v:0: that alone shows the
    # jump), then the remux
    assert [c[0] for c in calls[:4]] == ["ffprobe"] * 3 + ["ffmpeg"]
    assert "stream=codec_type" in calls[1]
    assert calls[2][calls[2].index("-select_streams") + 1] == "v:0"
    remux = calls[3]
    assert remux[:13] == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-fflags",
                          "+genpts", "-protocol_whitelist", "file", "-f", "mpegts", "-i", str(src)]
    assert remux[13:-1] == ["-c", "copy", "-f", "mpegts"]
    tmp = Path(remux[-1])
    assert tmp.parent == tmp_path and tmp.name.startswith(".timeline-") and tmp.suffix == ".part"
    assert Path(calls[4][-1]) == tmp  # the tmp's length was the one checked
    # both of the tmp's streams were read for a backwards step: #440's jump is in the audio too
    assert [Path(c[-1]) for c in calls[6:]] == [tmp, tmp]
    assert [c[c.index("-select_streams") + 1] for c in calls[6:]] == ["v:0", "a:0"]
    assert [c[0] for c in calls[4:]] == ["ffprobe"] * 4
    assert src.read_bytes() == b"new"
    assert not temps(tmp_path)
    out = capsys.readouterr().out
    assert "kick-1: the timeline jumps back 821.7 s; remuxed (33011.0 s -> 33832.7 s)" in out


def test_a_file_in_order_is_not_remuxed_and_its_bytes_are_untouched(tmp_path, monkeypatch, capsys):
    """#2: a 33 GB source that doesn't jump is not read and written for nothing: the order check
    is enough, ffmpeg is never run, and the file stays byte for byte as it was."""
    src = source(tmp_path)
    calls = ff(monkeypatch, [json.dumps(probed(3600.0))], packets=IN_ORDER)
    assert timeline.remux_verified(src, "kick-1") == 0.0
    # the probe, the container's stream list, then its v:0 and a:0: never ffmpeg
    assert [c[0] for c in calls] == ["ffprobe"] * 4
    assert src.read_bytes() == b"old"
    assert not temps(tmp_path)  # no temp was even made
    assert "kick-1: the timeline is already in order" in capsys.readouterr().out



def test_the_temp_is_never_taken_for_the_source(tmp_path, monkeypatch):
    """#440: the temp sits beside source.ts while ffmpeg writes it, and a check (transcribe's)
    may read the folder then: it is a `.timeline-*.part`, outside media.source_file's `source.*`
    glob, so the source is still the source."""
    src = source(tmp_path)
    seen = {}

    def run(cmd, **kw):
        if cmd[0] == "ffprobe":
            if "packet=pts_time" in cmd:
                temp = Path(cmd[-1]).name.startswith(".timeline-")
                return subprocess.CompletedProcess(cmd, 0, IN_ORDER if temp else JUMPS, "")
            if "stream=codec_type" in cmd:
                return subprocess.CompletedProcess(cmd, 0, STREAMS, "")
            return subprocess.CompletedProcess(cmd, 0, json.dumps(probed(33011.0)), "")
        Path(cmd[-1]).write_bytes(b"new")  # ffmpeg, mid-run: its temp is there
        seen["glob"] = sorted(p.name for p in tmp_path.glob("source.*"))
        seen["read"] = media.source_file(tmp_path)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(timeline.subprocess, "run", run)
    timeline.remux_verified(src, "kick-1")
    assert seen == {"glob": ["source.ts"], "read": src}


def test_a_file_already_at_an_old_temps_name_is_left_alone(tmp_path, monkeypatch):
    """Nothing that name is followed or overwritten: the remux makes a fresh temp of its own."""
    src = source(tmp_path)
    stale = tmp_path / "source.timeline.ts"
    stale.write_bytes(b"someone else's")
    ff(monkeypatch, [json.dumps(probed(33011.0)), json.dumps(probed(33832.7))])
    assert timeline.remux_verified(src, "kick-1") == 821.7
    assert src.read_bytes() == b"new"
    assert stale.read_bytes() == b"someone else's"
    assert not temps(tmp_path)


def test_a_shorter_result_is_refused_and_the_original_kept(tmp_path, monkeypatch):
    """A remux that lost its tail is not the file to keep: the original stays byte for byte."""
    src = source(tmp_path)
    ff(monkeypatch, [json.dumps(probed(33011.0)), json.dumps(probed(32011.0))])
    with pytest.raises(ClipperError, match=r"^kick-1: a remux of source\.ts came out 1000\.0 s shorter "
                                           r"\(source\.ts kept\)$"):
        timeline.remux_verified(src, "kick-1")
    assert src.read_bytes() == b"old"
    assert not temps(tmp_path)


def test_a_truncated_result_is_refused_and_the_original_kept(tmp_path, monkeypatch):
    """A stub a tenth of the source's size must not take the only copy's place, however long
    its stretched timestamps read: the remux has to have kept the bytes."""
    src = tmp_path / "source.ts"
    src.write_bytes(b"x" * (1024 * 1024))

    def run(cmd, **kw):
        if cmd[0] == "ffprobe":
            if "packet=pts_time" in cmd:
                temp = Path(cmd[-1]).name.startswith(".timeline-")
                return subprocess.CompletedProcess(cmd, 0, IN_ORDER if temp else JUMPS, "")
            if "stream=codec_type" in cmd:
                return subprocess.CompletedProcess(cmd, 0, STREAMS, "")
            return subprocess.CompletedProcess(cmd, 0, json.dumps(probed(33011.0)), "")
        Path(cmd[-1]).write_bytes(b"y" * 1024)  # 1 KB for the source's 1 MB
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(timeline.subprocess, "run", run)
    with pytest.raises(ClipperError, match=r"^kick-1: a remux of source\.ts came out 1024 bytes where "
                                           r"its source has 1048576 \(source\.ts kept\): truncated$"):
        timeline.remux_verified(src, "kick-1")
    assert len(src.read_bytes()) == 1024 * 1024
    assert not temps(tmp_path)


def test_a_result_a_little_shorter_is_ffprobes_rounding(tmp_path, monkeypatch):
    """A 0.5 s difference is not a jump either way: nothing is stored for it."""
    src = source(tmp_path)
    ff(monkeypatch, [json.dumps(probed(3600.0)), json.dumps(probed(3599.5))])
    assert timeline.remux_verified(src, "kick-1") == 0.0
    assert src.read_bytes() == b"new"


def test_a_fixed_file_with_no_growth_is_remuxed_and_returns_zero(tmp_path, monkeypatch, capsys):
    """The muxer can relinearise a jump without gaining a measurable length: the packets are in
    order now, so it goes in place, and there is no jump for the caller to store."""
    src = source(tmp_path)
    ff(monkeypatch, [json.dumps(probed(3600.0)), json.dumps(probed(3600.5))])
    assert timeline.remux_verified(src, "kick-1") == 0.0
    assert src.read_bytes() == b"new"
    out = capsys.readouterr().out
    assert "remuxed source.ts; its timeline is in order" in out
    assert "already in order" not in out



def test_a_failed_ffmpeg_is_one_line_and_leaves_the_original(tmp_path, monkeypatch):
    """ffmpeg's own stderr can name the whole path, or a url a playlist asked for: the message
    says which tool gave up and the exit code, and nothing of that line."""
    src = source(tmp_path)
    ff(monkeypatch, [json.dumps(probed(33011.0))], remux_code=1)
    with pytest.raises(ClipperError, match=r"^kick-1: a remux of source\.ts failed \(source\.ts kept\): "
                                           r"ffmpeg couldn't remux it \(exit 1\)$"):
        timeline.remux_verified(src, "kick-1")
    assert src.read_bytes() == b"old"
    assert not temps(tmp_path)


def test_an_ffmpeg_line_a_playlist_wrote_never_reaches_the_message(tmp_path, monkeypatch):
    src = source(tmp_path)
    line = f"{src}: Invalid data: https://example.test/live.m3u8\n"

    def run(cmd, **kw):
        if cmd[0] == "ffprobe":
            if "packet=pts_time" in cmd:
                temp = Path(cmd[-1]).name.startswith(".timeline-")
                return subprocess.CompletedProcess(cmd, 0, IN_ORDER if temp else JUMPS, "")
            if "stream=codec_type" in cmd:
                return subprocess.CompletedProcess(cmd, 0, STREAMS, "")
            return subprocess.CompletedProcess(cmd, 0, json.dumps(probed(33011.0)), "")
        return subprocess.CompletedProcess(cmd, 1, "", line)

    monkeypatch.setattr(timeline.subprocess, "run", run)
    with pytest.raises(ClipperError) as err:
        timeline.remux_verified(src, "kick-1")
    for leak in (str(tmp_path), "\\", "/", "http"):
        assert leak not in str(err.value)
    assert src.read_bytes() == b"old"
    assert not temps(tmp_path)


def test_a_remux_that_writes_nothing_leaves_the_original(tmp_path, monkeypatch):
    src = source(tmp_path)

    def run(cmd, **kw):  # the probe answers; ffmpeg exits 0 and writes nothing
        if cmd[0] == "ffprobe":
            if "packet=pts_time" in cmd:
                temp = Path(cmd[-1]).name.startswith(".timeline-")
                return subprocess.CompletedProcess(cmd, 0, IN_ORDER if temp else JUMPS, "")
            if "stream=codec_type" in cmd:
                return subprocess.CompletedProcess(cmd, 0, STREAMS, "")
            return subprocess.CompletedProcess(cmd, 0, json.dumps(probed(33011.0)), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(timeline.subprocess, "run", run)
    with pytest.raises(ClipperError, match=r"a remux of source\.ts failed \(source\.ts kept\): "
                                           r"ffmpeg wrote no output"):
        timeline.remux_verified(src, "kick-1")
    assert src.read_bytes() == b"old"
    assert not temps(tmp_path)


def test_a_temp_that_cant_be_probed_is_an_error_and_the_original_stays(tmp_path, monkeypatch):
    src = source(tmp_path)

    def run(cmd, **kw):  # the source probes, then ffmpeg writes, then its temp isn't JSON
        if cmd[0] == "ffprobe":
            if "packet=pts_time" in cmd:
                temp = Path(cmd[-1]).name.startswith(".timeline-")
                return subprocess.CompletedProcess(cmd, 0, IN_ORDER if temp else JUMPS, "")
            if "stream=codec_type" in cmd:
                return subprocess.CompletedProcess(cmd, 0, STREAMS, "")
            if not Path(cmd[-1]).name.startswith(".timeline-"):
                return subprocess.CompletedProcess(cmd, 0, json.dumps(probed(33011.0)), "")
        if cmd[0] == "ffmpeg":
            Path(cmd[-1]).write_bytes(b"new")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "<html></html>", "")

    monkeypatch.setattr(timeline.subprocess, "run", run)
    with pytest.raises(ClipperError, match=r"^kick-1: a remux of source\.ts failed \(source\.ts kept\): "
                                           r"ffprobe's JSON for \.timeline-\w+\.part didn't parse$"):
        timeline.remux_verified(src, "kick-1")
    assert src.read_bytes() == b"old"
    assert not temps(tmp_path)


def test_a_refused_replace_keeps_the_original_and_cleans_the_tmp(tmp_path, monkeypatch):
    """A verified tmp the share won't put in place (a reader on Windows, say). The message keeps
    the system's reason, not str(e): on Windows that carries the path."""
    src = source(tmp_path)
    ff(monkeypatch, [json.dumps(probed(33011.0)), json.dumps(probed(33832.7))])

    def refused(*a, **kw):
        raise PermissionError(13, "Access is denied", str(src))

    monkeypatch.setattr(timeline.os, "replace", refused)
    with pytest.raises(ClipperError, match=r"couldn't put the remuxed source\.ts in place "
                                           r"\(source\.ts kept\): Access is denied$") as err:
        timeline.remux_verified(src, "kick-1")
    assert str(tmp_path) not in str(err.value)
    assert src.read_bytes() == b"old"
    assert not temps(tmp_path)


def test_a_temp_swapped_for_a_link_to_somewhere_else_is_refused(tmp_path, monkeypatch, link_or_skip):
    """#440: between ffmpeg's last write and the replace, a temp that is no longer the regular
    file mkstemp made (here a symlink to a file of the same bytes) must not take the only copy's
    place: the link is dropped and the original kept."""
    src = source(tmp_path)
    elsewhere = tmp_path / "elsewhere.bin"
    elsewhere.write_bytes(b"old")  # the same bytes as the source: length and size alone pass it

    def run(cmd, **kw):
        if cmd[0] == "ffprobe":
            if "packet=pts_time" in cmd:
                temp = Path(cmd[-1]).name.startswith(".timeline-")
                return subprocess.CompletedProcess(cmd, 0, IN_ORDER if temp else JUMPS, "")
            if "stream=codec_type" in cmd:
                return subprocess.CompletedProcess(cmd, 0, STREAMS, "")
            out = json.dumps(probed(33011.0 if cmd[-1] == str(src) else 33832.7))
            return subprocess.CompletedProcess(cmd, 0, out, "")
        tmp = Path(cmd[-1])
        tmp.unlink()
        link_or_skip(elsewhere, tmp)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(timeline.subprocess, "run", run)
    monkeypatch.setattr(timeline.os, "replace", lambda *a: pytest.fail("a link went in place"))
    with pytest.raises(ClipperError, match=r"^kick-1: the remux's temp for source\.ts was swapped "
                                           r"for something else before it went in place "
                                           r"\(source\.ts kept\)$"):
        timeline.remux_verified(src, "kick-1")
    assert src.read_bytes() == b"old"
    assert elsewhere.read_bytes() == b"old"
    assert not temps(tmp_path)


def test_a_link_at_the_temps_name_is_refused_where_no_inode_is_there_to_compare(tmp_path, monkeypatch,
                                                                                link_or_skip):
    """#444: a share that reports inode 0 on both sides made the (st_dev, st_ino) comparison no
    evidence, and os.stat followed the link, so a temp swapped for a symlink to a same-size
    regular file passed as mkstemp's own. lstat sees the link itself: it is refused, and what it
    names keeps its bytes."""
    src = source(tmp_path)
    elsewhere = tmp_path / "elsewhere.bin"
    elsewhere.write_bytes(b"old")  # the same bytes as the source: length and size alone pass it
    tmp, made = timeline._temp(src)
    tmp.unlink()
    link_or_skip(elsewhere, tmp)
    no_inode = os.stat_result((made.st_mode, 0, 0, made.st_nlink, made.st_uid, made.st_gid,
                               made.st_size, made.st_atime, made.st_mtime, made.st_ctime))
    with pytest.raises(ClipperError, match=r"^kick-1: the remux's temp for source\.ts was swapped "
                                           r"for something else before it went in place "
                                           r"\(source\.ts kept\)$"):
        timeline._ours(tmp, no_inode, "kick-1", "source.ts")
    assert tmp.is_symlink() and elsewhere.read_bytes() == b"old"
    assert src.read_bytes() == b"old"


def test_the_temp_goes_even_when_something_else_fails(tmp_path, monkeypatch):
    """Not a ClipperError at all (a Ctrl-C, a bug): the temp is still not left behind."""
    src = source(tmp_path)

    def run(cmd, **kw):
        if cmd[0] == "ffprobe":
            if "packet=pts_time" in cmd:
                temp = Path(cmd[-1]).name.startswith(".timeline-")
                return subprocess.CompletedProcess(cmd, 0, IN_ORDER if temp else JUMPS, "")
            if "stream=codec_type" in cmd:
                return subprocess.CompletedProcess(cmd, 0, STREAMS, "")
            return subprocess.CompletedProcess(cmd, 0, json.dumps(probed(33011.0)), "")
        Path(cmd[-1]).write_bytes(b"half")
        raise KeyboardInterrupt

    monkeypatch.setattr(timeline.subprocess, "run", run)
    with pytest.raises(KeyboardInterrupt):
        timeline.remux_verified(src, "kick-1")
    assert not temps(tmp_path)
    assert src.read_bytes() == b"old"


def packet_probe(monkeypatch, out, code=0, stderr="", audio=None, types=("video", "audio")):
    """subprocess.run as `jumps_back`'s ffprobe: a container holding `types`' streams, this packet
    list for the video stream, `audio`'s for the audio one (this same list when None), this exit
    code for a packet list (`code` is not the stream listing's: that answers 0)."""
    seen = {"cmds": []}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        seen["cmds"].append(cmd)
        if "stream=codec_type" in cmd:  # what the container holds, before any stream is chosen
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps({"streams": [{"codec_type": t} for t in types]}), "")
        if audio is not None and cmd[cmd.index("-select_streams") + 1] == "a:0":
            return subprocess.CompletedProcess(cmd, code, audio, stderr)
        return subprocess.CompletedProcess(cmd, code, out, stderr)

    monkeypatch.setattr(timeline.subprocess, "run", run)
    return seen


def test_jumps_back_is_false_for_a_monotonic_packet_list(tmp_path, monkeypatch):
    seen = packet_probe(monkeypatch, "0.000000\n0.040000\n0.080000\n")
    assert timeline.jumps_back(tmp_path / "source.ts") is False
    # -protocol_whitelist file before the input, on both streams: a playlist named source.ts is
    # a file ffprobe can't read, not a url it fetches
    assert seen["cmd"] == ["ffprobe", "-v", "error", "-protocol_whitelist", "file",
                           "-select_streams", "a:0", "-show_entries", "packet=pts_time",
                           "-of", "csv=p=0", str(tmp_path / "source.ts")]
    assert [c[c.index("-select_streams") + 1] for c in seen["cmds"] if "-select_streams" in c] \
        == ["v:0", "a:0"]


@pytest.mark.parametrize("packets", ["62.033000,\n62.073000,\n",  # ffprobe 8: a trailing field
                                     "62.033,\n62.073,\n",  # and its absence (older ffprobe)
                                     "62.033\r\n62.073\r\n",  # a Windows line ending
                                     " 62.033000 ,\n 62.073000 ,\n"])  # and padded fields
def test_a_packet_time_is_the_lines_first_csv_field(tmp_path, monkeypatch, packets):
    """#440's blocker on real data: ffprobe 8.1.2 writes each packet with its MPEGTS Stream ID
    side data as a trailing field, `62.033000,`, so the whole line is not a time and every real
    Kick .ts was refused. Only the first field is the pts."""
    packet_probe(monkeypatch, packets)
    assert timeline.jumps_back(tmp_path / "source.ts") is False


def test_a_packet_with_an_empty_first_field_is_an_error(tmp_path, monkeypatch):
    """A line of nothing but the side data is a packet ffprobe gave no pts for: nothing there
    can vouch for the timeline, so it is refused like an N/A."""
    packet_probe(monkeypatch, "62.033000,\n,\n62.073000,\n")
    with pytest.raises(ClipperError, match=r"^source\.ts: a packet has no time ffprobe could read"):
        timeline.jumps_back(tmp_path / "source.ts")



def test_jumps_back_sees_a_step_backwards(tmp_path, monkeypatch):
    """#440: the second stretch begins at the file's own start, 883.83 s -> 62.03 s."""
    packet_probe(monkeypatch, "880.000000\n883.833333\n62.033333\n62.066667\n")
    assert timeline.jumps_back(tmp_path / "source.ts") is True


def test_jumps_back_sees_a_step_backwards_in_the_audio_alone(tmp_path, monkeypatch):
    """#440's jump is in the audio as often as in the video: a file whose video reads in order
    and whose audio steps back must not be called fixed."""
    packet_probe(monkeypatch, "0.000000\n0.040000\n0.080000\n",
                 audio="880.000000\n883.833333\n62.033333\n")
    assert timeline.jumps_back(tmp_path / "source.ts") is True


def test_a_forward_gap_is_not_a_step_backwards(tmp_path, monkeypatch):
    """A dropped segment leaves a gap forward, which extract_audio already fills with silence:
    only a step backwards breaks the timeline."""
    packet_probe(monkeypatch, "0.000000\n0.040000\n900.040000\n900.080000\n")
    assert timeline.jumps_back(tmp_path / "source.ts") is False


def test_a_frame_of_reordering_is_under_the_step_threshold(tmp_path, monkeypatch):
    """B-frames arrive a frame out of presentation order: 40 ms back is not a jump."""
    packet_probe(monkeypatch, "0.000000\n0.080000\n0.040000\n0.120000\n")
    assert timeline.jumps_back(tmp_path / "source.ts") is False


def test_jumps_back_on_a_list_it_cant_read_is_an_error(tmp_path, monkeypatch):
    packet_probe(monkeypatch, "0.000000\nnot-a-time\n")
    with pytest.raises(ClipperError, match=r"^source\.ts: a packet has no time ffprobe could read"):
        timeline.jumps_back(tmp_path / "source.ts")


def test_a_packet_ffprobe_leaves_out_is_an_error(tmp_path, monkeypatch):
    """Fail closed: an N/A is a packet whose step can't be judged, so the file can't be vouched
    for by calling it monotonic."""
    packet_probe(monkeypatch, "0.000000\nN/A\n62.000000\n")
    with pytest.raises(ClipperError, match=r"^source\.ts: a packet has no time ffprobe could read"):
        timeline.jumps_back(tmp_path / "source.ts")


def test_jumps_back_on_a_list_with_no_packets_is_an_error(tmp_path, monkeypatch):
    """Nothing read is nothing vouched for: a remux can't be called fixed on it."""
    packet_probe(monkeypatch, "")
    with pytest.raises(ClipperError, match=r"listed no packets for its v:0 stream"):
        timeline.jumps_back(tmp_path / "source.ts")


def test_a_stream_with_no_packets_is_an_error_not_a_pass(tmp_path, monkeypatch):
    """A container that does have an audio stream but reads as an empty list is not a timeline
    in order: only a stream type the file doesn't hold at all is skipped."""
    packet_probe(monkeypatch, "0.000000\n0.040000\n", audio="")
    with pytest.raises(ClipperError, match=r"listed no packets for its a:0 stream"):
        timeline.jumps_back(tmp_path / "source.ts")


def test_a_video_only_download_has_no_audio_timeline_to_check(tmp_path, monkeypatch):
    """#4: a .ts with no audio stream at all (or a container with no video) is skipped, not
    refused: there is no a:0 timeline there to be broken or fixed, so a --fixup must be able to
    run on it."""
    seen = packet_probe(monkeypatch, "0.000000\n0.040000\n", types=("video",))
    assert timeline.jumps_back(tmp_path / "source.ts") is False
    assert [c[c.index("-select_streams") + 1] for c in seen["cmds"] if "-select_streams" in c] == ["v:0"]


def test_a_video_only_download_still_sees_its_own_jump(tmp_path, monkeypatch):
    packet_probe(monkeypatch, JUMPS, types=("video",))
    assert timeline.jumps_back(tmp_path / "source.ts") is True


def test_a_container_with_no_video_or_audio_at_all_is_an_error(tmp_path, monkeypatch):
    """Nothing was checked, so nothing can be vouched for: a file with neither stream type is not
    called in order."""
    packet_probe(monkeypatch, "0.000000\n", types=("data",))
    with pytest.raises(ClipperError, match=r"no video or audio stream"):
        timeline.jumps_back(tmp_path / "source.ts")


def test_a_stream_list_ffprobe_wont_give_is_an_error(tmp_path, monkeypatch):
    """Which streams the container holds comes from ffprobe too: refused there, it is one line,
    and none of ffprobe's own words (a path, a url) travels with it."""
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", f"{tmp_path}: Invalid: https://x.test/a.m3u8\n")

    monkeypatch.setattr(timeline.subprocess, "run", run)
    with pytest.raises(ClipperError, match=r"^source\.ts: ffprobe couldn't list its streams "
                                           r"\(exit 1\)$") as err:
        timeline.jumps_back(tmp_path / "source.ts")
    for leak in (str(tmp_path), "\\", "/", "http"):
        assert leak not in str(err.value)


def test_jumps_back_says_ffprobe_refused_but_not_in_its_words(tmp_path, monkeypatch):
    packet_probe(monkeypatch, "", code=1,
                 stderr=f"{tmp_path}/source.ts: Invalid data: https://example.test/a.m3u8\n")
    with pytest.raises(ClipperError, match=r"^source\.ts: ffprobe couldn't list its packets "
                                           r"\(exit 1\)$") as err:
        timeline.jumps_back(tmp_path / "source.ts")
    for leak in (str(tmp_path), "\\", "/", "http"):
        assert leak not in str(err.value)


def test_a_remux_that_still_jumps_is_refused_and_the_original_kept(tmp_path, monkeypatch):
    """A remux can come out the right length and still hold the old jump (the overlap the muxer
    didn't relinearise): the length alone would pass it and clear the metadata, so the packet
    check is what keeps the original instead."""
    src = source(tmp_path)
    still = "0.000000\n821.700000\n0.000000\n"
    ff(monkeypatch, [json.dumps(probed(33011.0)), json.dumps(probed(33832.7))],
       packets=JUMPS, fixed=still)
    with pytest.raises(ClipperError, match=r"^kick-1: a remux of source\.ts didn't fix the jump "
                                           r"\(source\.ts kept\): its timestamps still step back$"):
        timeline.remux_verified(src, "kick-1")
    assert src.read_bytes() == b"old"
    assert not temps(tmp_path)


def test_a_remux_refuses_anything_but_a_ts_source(tmp_path, monkeypatch):
    """The copy is forced to mpegts, so an .mp4 put through it would be rewritten as a .ts under
    the wrong name: it is refused before anything is probed."""
    src = tmp_path / "source.mp4"
    src.write_bytes(b"mp4")
    monkeypatch.setattr(timeline.subprocess, "run", lambda *a, **kw: pytest.fail("ran a tool"))
    with pytest.raises(ClipperError, match=r"^kick-1: a remux only repairs a Kick \.ts source, "
                                           r"and source\.mp4 is not one$"):
        timeline.remux_verified(src, "kick-1")
    assert src.read_bytes() == b"mp4"
    assert not temps(tmp_path)


def test_a_folder_that_wont_take_the_temp_is_a_clear_error(tmp_path, monkeypatch):
    """A full disk or a refused folder is a ClipperError, not a traceback out of fetch; its
    reason is the system's, never str(e) with the path."""
    src = source(tmp_path)
    ff(monkeypatch, [json.dumps(probed(33011.0))])  # it jumps, so a temp is wanted

    def full(*a, **kw):
        raise OSError(28, "No space left on device", str(tmp_path))

    monkeypatch.setattr(timeline.tempfile, "mkstemp", full)
    with pytest.raises(ClipperError, match=r"^couldn't make a temp file beside source\.ts: "
                                           r"No space left on device$") as err:
        timeline.remux_verified(src, "kick-1")
    assert str(tmp_path) not in str(err.value)
    assert src.read_bytes() == b"old"


def test_a_tool_that_cant_run_is_a_clear_error(tmp_path, monkeypatch):
    src = source(tmp_path)

    def denied(*a, **kw):
        raise PermissionError(13, "Access is denied", "C:\\ffmpeg\\bin\\ffprobe.exe")

    monkeypatch.setattr(timeline.subprocess, "run", denied)
    with pytest.raises(ClipperError, match=r"^ffprobe couldn't run: Access is denied$") as err:
        timeline.remux_verified(src, "kick-1")
    assert "\\" not in str(err.value) and "/" not in str(err.value)
    assert src.read_bytes() == b"old"
    assert not temps(tmp_path)


def aged(path: Path, age_s: float) -> Path:
    """`path` touched, with an mtime this many seconds in the past."""
    path.write_bytes(b"part")
    then = time.time() - age_s
    os.utime(path, (then, then))
    return path


def test_sweep_removes_an_old_remux_temp_and_leaves_the_rest(tmp_path, capsys):
    """#5: a killed remux's multi-GB temp is picked up on the next --fixup; one that may be a
    remux running now, and anything else in the folder, is left alone."""
    old = aged(tmp_path / ".timeline-abc123.part", timeline.SWEEP_S + 60)
    fresh = tmp_path / ".timeline-def456.part"
    fresh.write_bytes(b"part")
    other = tmp_path / "source.ts"
    other.write_bytes(b"old")
    timeline.sweep(tmp_path)
    assert not old.exists()
    assert fresh.exists() and other.exists()
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and old.name in out and str(tmp_path) not in out


def test_sweep_of_a_clean_folder_says_nothing(tmp_path, capsys):
    (tmp_path / "source.ts").write_bytes(b"old")
    timeline.sweep(tmp_path)
    assert capsys.readouterr().out == ""


def test_sweep_says_a_temp_it_could_not_remove_and_carries_on(tmp_path, monkeypatch, capsys):
    """A share refusing the unlink is not why a fetch fails: one line, and the run goes on."""
    aged(tmp_path / ".timeline-abc123.part", timeline.SWEEP_S + 60)

    def denied(*a, **kw):
        raise PermissionError(13, "Access is denied", str(tmp_path))

    monkeypatch.setattr(Path, "unlink", denied)
    timeline.sweep(tmp_path)
    out = capsys.readouterr().out
    assert "couldn't remove the leftover remux temp .timeline-abc123.part: Access is denied" in out
    assert str(tmp_path) not in out

