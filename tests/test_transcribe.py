"""The transcribe stage (clippa/transcribe.py): the pure fix-ups on WhisperX's words, the
timeline check that refuses a source whose audio runs past its container (#440), and the
Modal upload's format. The GPU, Modal and ffmpeg themselves are not run here."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from clippa import jsonstate, transcribe
from clippa.errors import ClipperError


def test_fill_missing_times_spreads_unaligned_words_between_their_neighbours():
    words = [{"word": "a", "start": 0.0, "end": 1.0}, {"word": "7"}, {"word": "%"},
             {"word": "b", "start": 4.0, "end": 5.0}]
    assert transcribe.fill_missing_times(words) == 2
    assert [(w["start"], w["end"]) for w in words[1:3]] == [(1.0, 2.5), (2.5, 4.0)]
    assert words[1]["interpolated"] is True and words[1]["score"] is None
    assert "interpolated" not in words[0]


def test_fill_missing_times_at_the_ends():
    tail = [{"word": "a", "start": 0.0, "end": 1.0}, {"word": "9"}]   # nothing after: a zero-length word
    assert transcribe.fill_missing_times(tail) == 1 and (tail[1]["start"], tail[1]["end"]) == (1.0, 1.0)
    head = [{"word": "#"}, {"word": "a", "start": 2.0, "end": 3.0}]   # nothing before: it sits where a starts
    assert transcribe.fill_missing_times(head) == 1 and (head[0]["start"], head[0]["end"]) == (2.0, 2.0)
    assert transcribe.fill_missing_times([]) == 0


def stream_dir(tmp_path, meta=None, source="source.ts"):
    """A stream folder as transcribe sees one: its source, audio.wav, and meta.json if given."""
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / source).write_bytes(b"source")
    (d / "audio.wav").write_bytes(b"wav")
    if meta is not None:
        jsonstate.write(d / "meta.json", meta)
    return d


def durations(monkeypatch, wav_s, container_s):
    """The two probes the timeline check makes, without ffprobe."""
    monkeypatch.setattr(transcribe, "try_duration", lambda path: wav_s)
    monkeypatch.setattr(transcribe.timeline, "probe", lambda path: {"start_time": 62.0, "duration": container_s})


def test_a_wav_older_than_its_source_is_extracted_again(tmp_path, monkeypatch):
    """A wav older than the source was decoded from a different file (a --fixup's replace, a
    hand-replaced source, a killed run, #446), so it must not be served for this one."""
    d = stream_dir(tmp_path)
    old = time.time_ns() - 10 * 10**9
    os.utime(d / "audio.wav", ns=(old, old))
    ran = []

    def run(cmd, check):
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"the wav of this source")

    monkeypatch.setattr(transcribe.subprocess, "run", run)
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert ran and ran[0][0] == "ffmpeg" and ran[0][-1].endswith("audio.tmp.wav")
    assert (d / "audio.wav").read_bytes() == b"the wav of this source"
    assert not (d / "audio.tmp.wav").exists()


def test_a_stale_wav_the_share_will_not_remove_is_one_line(tmp_path, monkeypatch):
    """The wav has to go before the source is decoded again, and a share that won't remove it
    is one line naming the stream, not a traceback (and never its path)."""
    d = stream_dir(tmp_path)
    old = time.time_ns() - 10 * 10**9
    os.utime(d / "audio.wav", ns=(old, old))

    def denied(self, *a, **kw):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(ClipperError) as err:
        transcribe.extract_audio(d)
    assert str(err.value) == "kick-1: couldn't drop the stale audio.wav (Access is denied)"
    assert str(d) not in str(err.value)


def test_a_wav_newer_than_its_source_is_reused(tmp_path, monkeypatch):
    """Nothing rewrote the source since the wav was decoded: it is not decoded again."""
    d = stream_dir(tmp_path)
    old = time.time_ns() - 10 * 10**9
    os.utime(d / "source.ts", ns=(old, old))
    monkeypatch.setattr(transcribe.subprocess, "run", lambda *a, **kw: pytest.fail("decoded again"))
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert (d / "audio.wav").read_bytes() == b"wav"


def test_a_marked_wav_is_extracted_again_whatever_its_mtime(tmp_path, monkeypatch):
    """A --fixup marks the wav stale before it replaces the source: a fast remux leaves the wav
    younger than the share rounds by, so its mtime can't say it was decoded from the old source."""
    d = stream_dir(tmp_path)
    transcribe.mark_stale(d)
    ran = []

    def run(cmd, check):
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"the wav of this source")

    monkeypatch.setattr(transcribe.subprocess, "run", run)
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert ran and (d / "audio.wav").read_bytes() == b"the wav of this source"
    assert not (d / "audio.wav.stale").exists()  # a marker must not hold a fresh wav back


def test_a_marked_wav_is_extracted_again_when_its_times_cannot_be_read(tmp_path, monkeypatch):
    """The marker is the one thing that knows, so a source the share won't stat must not make the
    wav it marks look like a cache of this source (an unreadable time says nothing either way)."""
    d = stream_dir(tmp_path)
    transcribe.mark_stale(d)
    real = Path.lstat

    def denied(self, *a, **kw):
        if self.name == "source.ts":
            raise PermissionError(13, "Access is denied")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "lstat", denied)
    monkeypatch.setattr(transcribe, "is_link", lambda p: False)  # the patched lstat is not ours to read
    monkeypatch.setattr(transcribe.subprocess, "run",
                        lambda cmd, check: Path(cmd[-1]).write_bytes(b"decoded again"))
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert (d / "audio.wav").read_bytes() == b"decoded again"
    assert not (d / "audio.wav.stale").exists()


def test_a_source_replaced_during_the_decode_installs_no_audio(tmp_path, monkeypatch):
    """A --fixup (or a re-download) can replace the source while a long decode is still on the old
    one. After a repair the two lengths agree, so the timeline check can't see it: the audio of the
    file that is gone must not be installed as this one's, and the marker that is beside it (the
    fixup's, about the wav here) must survive for the next run."""
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "source.ts").write_bytes(b"the source whose timeline jumps")
    transcribe.mark_stale(d)  # what a --fixup writes before it replaces a source

    def run(cmd, check):  # ffmpeg, while the source is replaced under it
        Path(cmd[-1]).write_bytes(b"the old source's audio")
        (d / "source.ts").write_bytes(b"the repaired source")
        os.utime(d / "source.ts", ns=(time.time_ns(), time.time_ns()))

    monkeypatch.setattr(transcribe.subprocess, "run", run)
    with pytest.raises(ClipperError) as err:
        transcribe.extract_audio(d)
    assert str(err.value) == ("kick-1: its source changed while its audio was being decoded; "
                              "run it again")
    assert not (d / "audio.wav").exists()  # the old source's audio is not this stream's
    assert not (d / "audio.tmp.wav").exists()  # nor left beside it
    assert (d / "audio.wav.stale").exists()  # still says the wav here is of another source
    assert not (d / "audio.wav.src").exists()  # and no stamp vouches for audio that wasn't installed


def test_a_wav_with_no_source_is_served_as_it_is(tmp_path, monkeypatch):
    """The source can be deleted after extraction: there is nothing the wav can be stale
    against, so today's behaviour (serve it) is kept."""
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "audio.wav").write_bytes(b"wav")
    monkeypatch.setattr(transcribe.subprocess, "run", lambda *a, **kw: pytest.fail("decoded"))
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert (d / "audio.wav").read_bytes() == b"wav"


def test_no_wav_is_extracted_from_the_source(tmp_path, monkeypatch):
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "source.ts").write_bytes(b"source")
    ran = []

    def run(cmd, check):
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"wav")

    monkeypatch.setattr(transcribe.subprocess, "run", run)
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert ran and str(d / "source.ts") in ran[0]
    # -protocol_whitelist file before the input: a source.ts that is really an HLS playlist
    # can't send ffmpeg to a url
    cmd = ran[0]
    at = cmd.index("-protocol_whitelist")
    assert cmd[at + 1] == "file" and at < cmd.index("-i")
    assert (d / "audio.wav").read_bytes() == b"wav"


def test_a_linked_wav_is_dropped_and_the_source_decoded(tmp_path, monkeypatch, link_or_skip):
    """A link named audio.wav is not a cache of ours: the link goes (never what it points at) and
    the source is decoded into a file of this stream's own."""
    d = stream_dir(tmp_path)
    elsewhere = tmp_path / "elsewhere.wav"
    elsewhere.write_bytes(b"someone else's wav")
    wav = d / "audio.wav"
    wav.unlink()
    link_or_skip(elsewhere, wav)
    ran = []

    def run(cmd, check):
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"the wav of this source")

    monkeypatch.setattr(transcribe.subprocess, "run", run)
    assert transcribe.extract_audio(d) == wav
    assert ran, "the link was served instead of the source being decoded"
    assert not wav.is_symlink() and wav.read_bytes() == b"the wav of this source"
    assert elsewhere.read_bytes() == b"someone else's wav"  # its target is untouched
    # ffmpeg is given the source, and the link's target names nothing in the argv
    cmd = ran[0]
    assert cmd[cmd.index("-i") + 1] == str(d / "source.ts")
    assert all(str(elsewhere) not in arg for arg in cmd)


def test_a_linked_wav_the_share_will_not_remove_is_one_line(tmp_path, monkeypatch, link_or_skip):
    """The link has to go before the source is decoded, and a share that won't unlink it is one
    line naming the stream, not a traceback: nothing is handed to ffmpeg and the link and its
    target stay as they were."""
    d = stream_dir(tmp_path)
    elsewhere = tmp_path / "elsewhere.wav"
    elsewhere.write_bytes(b"someone else's wav")
    wav = d / "audio.wav"
    wav.unlink()
    link_or_skip(elsewhere, wav)
    assert wav.is_symlink()

    def denied(self, *a, **kw):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "unlink", denied)
    monkeypatch.setattr(transcribe.subprocess, "run", lambda *a, **kw: pytest.fail("decoded"))
    with pytest.raises(ClipperError) as err:
        transcribe.extract_audio(d)
    assert str(err.value) == ("kick-1: couldn't drop the linked audio.wav (Access is denied)")
    assert str(tmp_path) not in str(err.value)
    assert wav.is_symlink() and elsewhere.read_bytes() == b"someone else's wav"


@pytest.mark.parametrize("wav", [True, False])
def test_a_linked_source_is_refused_without_its_path(tmp_path, monkeypatch, link_or_skip, wav):
    """ffmpeg must not be sent through a link to somewhere else: the source is refused in one
    line naming the stream, before any mtime comparison or decode. The link is left in place (its
    target with it), so a guard that removed the target and then raised reads as what it is."""
    d = tmp_path / "kick-1"
    d.mkdir()
    if wav:
        (d / "audio.wav").write_bytes(b"wav")
    elsewhere = tmp_path / "elsewhere.ts"
    elsewhere.write_bytes(b"source")
    link_or_skip(elsewhere, d / "source.ts")
    monkeypatch.setattr(transcribe.subprocess, "run", lambda *a, **kw: pytest.fail("read the link"))
    with pytest.raises(ClipperError) as err:
        transcribe.extract_audio(d)
    assert str(err.value) == ("kick-1: its source is a link, not a file; refusing to read it")
    assert str(tmp_path) not in str(err.value)
    assert (d / "source.ts").is_symlink() and elsewhere.read_bytes() == b"source"
    if wav:
        assert (d / "audio.wav").read_bytes() == b"wav"  # the cache is untouched


def test_a_junction_at_the_source_is_refused_as_a_link(tmp_path, monkeypatch, junction_or_skip):
    """A junction named source.ts would otherwise reach ffmpeg, which fails on it (and check=True
    would take the stage out as a traceback instead of the one line a guard gives)."""
    d = tmp_path / "kick-1"
    d.mkdir()
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    junction_or_skip(folder, d / "source.ts")
    monkeypatch.setattr(transcribe.subprocess, "run", lambda *a, **kw: pytest.fail("read the link"))
    with pytest.raises(ClipperError) as err:
        transcribe.extract_audio(d)
    assert str(err.value) == ("kick-1: its source is a link, not a file; refusing to read it")
    assert str(tmp_path) not in str(err.value)


def test_a_junction_at_the_wav_is_removed_and_the_source_decoded(tmp_path, monkeypatch, junction_or_skip):
    """#444: a junction is a folder to Path.unlink, which raises on Windows, so dropping a linked
    audio.wav would fail on every run. It is rmdir'ed instead — the link alone — and the source
    decoded into a file of this stream's own; the folder it named keeps its contents."""
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "source.ts").write_bytes(b"source")
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / "keep.txt").write_bytes(b"someone else's")
    junction_or_skip(folder, d / "audio.wav")
    assert os.path.isjunction(d / "audio.wav")
    ran = []

    def run(cmd, check):
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"the wav of this source")

    monkeypatch.setattr(transcribe.subprocess, "run", run)
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert ran, "the junction was served instead of the source being decoded"
    assert not (d / "audio.wav").is_dir() and (d / "audio.wav").read_bytes() == b"the wav of this source"
    assert (folder / "keep.txt").read_bytes() == b"someone else's"


def test_a_source_ffmpeg_cannot_decode_is_one_line(tmp_path, monkeypatch):
    """A wrong or truncated download is a file ffmpeg can't read: the stage says so in one line
    naming the stream and the exit code, not a CalledProcessError traceback. A decode that dies
    midway leaves nothing behind: the partial audio.tmp.wav goes with it."""
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "source.ts").write_bytes(b"not media at all")

    def failed(cmd, check):
        Path(cmd[-1]).write_bytes(b"half a wav")  # ffmpeg got this far before it gave up
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(transcribe.subprocess, "run", failed)
    with pytest.raises(ClipperError) as err:
        transcribe.extract_audio(d)
    assert str(err.value) == "kick-1: ffmpeg couldn't decode its source (exit 1)"
    assert str(tmp_path) not in str(err.value)
    assert not (d / "audio.wav").exists() and not (d / "audio.tmp.wav").exists()


def test_a_windows_unsigned_ffmpeg_exit_is_reported_signed(tmp_path, monkeypatch):
    """Windows hands back ffmpeg's negative errno as unsigned: 0xBEBEBE37 reads as 3199971767 there
    and -1094995529 in a shell. The one line says the signed form, as a person's shell shows it."""
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "source.ts").write_bytes(b"not media at all")

    def failed(cmd, check):
        raise subprocess.CalledProcessError(3199971767, cmd)  # 0xBEBEBE37, unsigned on Windows

    monkeypatch.setattr(transcribe.subprocess, "run", failed)
    with pytest.raises(ClipperError) as err:
        transcribe.extract_audio(d)
    assert str(err.value) == "kick-1: ffmpeg couldn't decode its source (exit -1094995529)"
    assert str(tmp_path) not in str(err.value)


@pytest.mark.parametrize("older_s", [0, 2], ids=["equal", "rounded"])
def test_a_wav_up_to_the_shares_rounding_older_than_its_source_is_reused(tmp_path, monkeypatch, older_s):
    """FAT and SMB round timestamps by up to 2 s, so a wav written just after its source can read
    as older by that much: that is rounding, and decoding the whole source again for it would be
    for nothing. Equal stamps are the common case."""
    d = stream_dir(tmp_path)
    now = time.time_ns()
    os.utime(d / "source.ts", ns=(now, now))
    os.utime(d / "audio.wav", ns=(now - older_s * 10**9, now - older_s * 10**9))
    monkeypatch.setattr(transcribe.subprocess, "run", lambda *a, **kw: pytest.fail("decoded again"))
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert (d / "audio.wav").read_bytes() == b"wav"


def test_a_wav_more_than_the_rounding_older_than_its_source_is_extracted_again(tmp_path, monkeypatch):
    """Past the 2 s a share rounds by, an older wav was decoded from a different file."""
    d = stream_dir(tmp_path)
    now = time.time_ns()
    os.utime(d / "source.ts", ns=(now, now))
    os.utime(d / "audio.wav", ns=(now - 3 * 10**9, now - 3 * 10**9))
    ran = []

    def run(cmd, check):
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"the wav of this source")

    monkeypatch.setattr(transcribe.subprocess, "run", run)
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert ran and (d / "audio.wav").read_bytes() == b"the wav of this source"


def stamp(d, size, mtime_ns):
    """audio.wav.src as extract_audio writes it (#448): the source the wav was decoded from."""
    jsonstate.write(d / "audio.wav.src", {"size": size, "mtime_ns": mtime_ns}, indent=None)


def decodes(monkeypatch, ran):
    """subprocess.run as ffmpeg: it records the call and writes the new wav."""
    def run(cmd, check):
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"the wav of this source")
    monkeypatch.setattr(transcribe.subprocess, "run", run)


def test_a_decode_records_which_source_its_wav_is_of(tmp_path, monkeypatch):
    """#448: the stamp is the source's own size and mtime, as its lstat read them for the decode."""
    d = stream_dir(tmp_path)
    (d / "audio.wav").unlink()
    decodes(monkeypatch, [])
    transcribe.extract_audio(d)
    st = (d / "source.ts").lstat()
    assert jsonstate.load(d / "audio.wav.src") == {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def test_a_stamp_of_another_source_decodes_again_though_the_wav_is_newer(tmp_path, monkeypatch):
    """#448: a source moved in by hand or put back from a backup can carry an mtime older than the
    wav, which the mtime rule alone would reuse: the stamp says the wav is of another file."""
    d = stream_dir(tmp_path)
    old = time.time_ns() - 3600 * 10**9  # the replacement's own stamp, an hour before the wav
    os.utime(d / "source.ts", ns=(old, old))
    stamp(d, size=999, mtime_ns=old)  # the file the wav was decoded from was another size
    ran = []
    decodes(monkeypatch, ran)
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert ran and (d / "audio.wav").read_bytes() == b"the wav of this source"
    st = (d / "source.ts").lstat()
    assert jsonstate.load(d / "audio.wav.src") == {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def test_a_stamp_of_this_source_keeps_the_wav_within_the_shares_rounding(tmp_path, monkeypatch):
    """The laptop over SMB and a container on the NAS's disk read one file's time at different
    precisions, so a stamp up to MTIME_ROUNDING_S off is still this source: no 33 GB decode for it."""
    d = stream_dir(tmp_path)
    st = (d / "source.ts").lstat()
    stamp(d, size=st.st_size, mtime_ns=st.st_mtime_ns - 1_500_000_000)
    monkeypatch.setattr(transcribe.subprocess, "run", lambda *a, **kw: pytest.fail("decoded again"))
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert (d / "audio.wav").read_bytes() == b"wav"


def test_a_matching_stamp_keeps_a_wav_the_mtime_rule_would_drop(tmp_path, monkeypatch):
    """The stamp is the record: a wav whose own mtime reads older (copied back with its folder, say)
    is still the audio of the file the stamp names."""
    d = stream_dir(tmp_path)
    old = time.time_ns() - 3600 * 10**9
    os.utime(d / "audio.wav", ns=(old, old))
    st = (d / "source.ts").lstat()
    stamp(d, size=st.st_size, mtime_ns=st.st_mtime_ns)
    monkeypatch.setattr(transcribe.subprocess, "run", lambda *a, **kw: pytest.fail("decoded again"))
    assert transcribe.extract_audio(d) == d / "audio.wav"


def test_the_marker_wins_over_a_matching_stamp(tmp_path, monkeypatch):
    """A download or a --fixup marks the wav stale before the source changes: what the stamp said
    then is not evidence now."""
    d = stream_dir(tmp_path)
    st = (d / "source.ts").lstat()
    stamp(d, size=st.st_size, mtime_ns=st.st_mtime_ns)
    transcribe.mark_stale(d)
    ran = []
    decodes(monkeypatch, ran)
    transcribe.extract_audio(d)
    assert ran and not (d / "audio.wav.stale").exists()


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'{"size": true, "mtime_ns": 1}',
                                  b'{"size": 6}'], ids=["garbage", "list", "bool", "partial"])
def test_a_stamp_that_is_not_one_decodes_again(tmp_path, monkeypatch, body):
    """A broken stamp can't vouch for the wav, and it is not the absence of one either (that would
    fall back to the mtime rule, which this wav, newer than its source, passes)."""
    d = stream_dir(tmp_path)
    old = time.time_ns() - 3600 * 10**9
    os.utime(d / "source.ts", ns=(old, old))
    (d / "audio.wav.src").write_bytes(body)
    ran = []
    decodes(monkeypatch, ran)
    transcribe.extract_audio(d)
    assert ran


def test_a_linked_stamp_is_not_read(tmp_path, monkeypatch, link_or_skip):
    """A link at the stamp's name says nothing about this wav, whatever the file it names holds."""
    d = stream_dir(tmp_path)
    st = (d / "source.ts").lstat()
    elsewhere = tmp_path / "elsewhere.json"
    jsonstate.write(elsewhere, {"size": st.st_size, "mtime_ns": st.st_mtime_ns})
    link_or_skip(elsewhere, d / "audio.wav.src")
    ran = []
    decodes(monkeypatch, ran)
    transcribe.extract_audio(d)
    assert ran
    assert jsonstate.load(elsewhere) == {"size": st.st_size, "mtime_ns": st.st_mtime_ns}  # untouched


@pytest.mark.parametrize("stamped", [False, True], ids=["no-stamp", "stamped"])
def test_an_archive_wav_with_no_stamp_is_decoded_again(tmp_path, monkeypatch, stamped):
    """trust_mtime False (archive): a wav with no stamp is a failed run's leftover of an unknown
    download, so it is decoded again; one stamped for this source is still reused."""
    d = stream_dir(tmp_path)
    old = time.time_ns() - 3600 * 10**9
    os.utime(d / "source.ts", ns=(old, old))  # yt-dlp's Last-Modified: the mtime rule would keep it
    if stamped:
        st = (d / "source.ts").lstat()
        stamp(d, size=st.st_size, mtime_ns=st.st_mtime_ns)
    ran = []
    decodes(monkeypatch, ran)
    transcribe.extract_audio(d, trust_mtime=False)
    assert bool(ran) is not stamped


def test_a_stale_wav_goes_with_its_stamp(tmp_path, monkeypatch):
    """The stamp names the file the dropped wav was of: it goes with it, so a decode that then fails
    leaves no stamp behind to vouch for nothing."""
    d = stream_dir(tmp_path)
    stamp(d, size=999, mtime_ns=1)
    transcribe.mark_stale(d)

    def failed(cmd, check):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(transcribe.subprocess, "run", failed)
    with pytest.raises(ClipperError):
        transcribe.extract_audio(d)
    assert not (d / "audio.wav").exists() and not (d / "audio.wav.src").exists()


def test_a_source_stamped_in_the_future_does_not_save_an_older_wav(tmp_path, monkeypatch):
    """#444: a source stamped ahead of this clock says nothing about which file the wav was made
    from. Here the wav is older than the source by more than a share's rounding (a repair whose
    wav unlink failed, a source rewritten with its future date kept), so it is decoded again:
    reusing it would transcript audio from the file before this one."""
    d = stream_dir(tmp_path)
    future = time.time_ns() + 3600 * 10**9
    os.utime(d / "source.ts", ns=(future, future))
    ran = []

    def run(cmd, check):
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"the wav of this source")

    monkeypatch.setattr(transcribe.subprocess, "run", run)
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert ran and str(d / "source.ts") in ran[0]
    assert (d / "audio.wav").read_bytes() == b"the wav of this source"


def test_an_audio_longer_than_its_container_is_refused_with_the_fixup_command(tmp_path, monkeypatch):
    """#440: 33833 s of audio in a 33011.2 s container is 821.8 s of timestamps off."""
    d = stream_dir(tmp_path, {"stream_id": "kick-1", "platform": "kick",
                              "url": "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"})
    durations(monkeypatch, 33833.0, 33011.2)
    with pytest.raises(ClipperError) as err:
        transcribe.check_audio_timeline(d)
    msg = str(err.value)
    assert msg.startswith("kick-1: the source's timeline jumps back 821.8 s")
    assert "33833.0 s" in msg and "33011.2 s" in msg
    assert msg.endswith("clippa fetch https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b --fixup")


@pytest.mark.parametrize("meta", [
    {"stream_id": "kick-1"},  # a recording: its meta.json has no url
    {"stream_id": "kick-1", "platform": "kick", "url": "https://kick.com/kino"},  # the channel
    {"stream_id": "kick-1", "platform": "kick", "url": "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b?x=1"},
    {"stream_id": "kick-1", "platform": "kick", "url": "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b#t=5"},
    {"stream_id": "kick-1", "platform": "twitch",  # not Kick: --fixup does nothing there
     "url": "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"},
])
def test_a_stream_a_fixup_could_not_repair_gets_no_fetch_line(tmp_path, monkeypatch, meta):
    """The message's command has to be one that would work: --fixup remuxes a Kick VOD, and a
    channel link or a query after the id is not the VOD."""
    d = stream_dir(tmp_path, meta)
    durations(monkeypatch, 33833.0, 33011.2)
    with pytest.raises(ClipperError, match=r"^kick-1: the source's timeline and its audio disagree "
                                          r"by 821\.8 s") as err:
        transcribe.check_audio_timeline(d)
    assert "clippa fetch" not in str(err.value)


def test_a_kick_vod_is_named_in_the_fixup_line_through_shlex(tmp_path, monkeypatch):
    """The url is quoted for the shell it is copied into: a channel with a bracket in it comes
    out quoted, and the command still names this VOD (VOD_URL accepts it unquoted)."""
    d = stream_dir(tmp_path, {"stream_id": "kick-1", "source": "kick",
                              "url": "https://kick.com/ki(no)/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"})
    durations(monkeypatch, 33833.0, 33011.2)
    with pytest.raises(ClipperError) as err:
        transcribe.check_audio_timeline(d)
    assert ("clippa fetch 'https://kick.com/ki(no)/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b' "
            "--fixup") in str(err.value)


def test_a_timeline_jump_is_a_clipper_error_whose_reason_has_no_command(tmp_path, monkeypatch):
    """#444: `archive` writes the exception into error.txt, so the advice is separable: the
    reason carries the numbers, and str() adds the fixup command only where one would work."""
    d = stream_dir(tmp_path, {"stream_id": "kick-1", "platform": "kick",
                              "url": "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"})
    durations(monkeypatch, 33833.0, 33011.2)
    with pytest.raises(transcribe.TimelineJump) as err:
        transcribe.check_audio_timeline(d)
    assert isinstance(err.value, ClipperError)
    assert err.value.reason.startswith("kick-1: the source's timeline jumps back 821.8 s")
    assert "clippa fetch" not in err.value.reason and "http" not in err.value.reason
    assert str(err.value) == (err.value.reason + ": clippa fetch "
                              "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b --fixup")


def test_a_jump_a_fixup_could_not_repair_carries_no_advice_at_all(tmp_path, monkeypatch):
    """A recording, or a channel link: --fixup would fetch the wrong thing, so no command is
    in str() either, and the reason is the whole message."""
    d = stream_dir(tmp_path, {"stream_id": "kick-1"})
    durations(monkeypatch, 33833.0, 33011.2)
    with pytest.raises(transcribe.TimelineJump) as err:
        transcribe.check_audio_timeline(d)
    assert "disagree by 821.8 s" in err.value.reason
    assert "clippa fetch" not in str(err.value) and "http" not in str(err.value)
    assert str(err.value) == err.value.reason


def test_a_broken_meta_json_leaves_the_refusal_as_it_is(tmp_path, monkeypatch):
    """#444/#440: the fixup command is advice, the refusal is not. A meta.json that is unreadable
    or not an object must not turn the TimelineJump into a ValueError or AttributeError, which
    `archive` doesn't catch: the download would go unkept. No advice is the whole message."""
    cases = []
    d = stream_dir(tmp_path, {"stream_id": "kick-1"})
    (d / "meta.json").write_text("{", encoding="utf-8")  # a half-written file
    cases.append(d)
    (tmp_path / "list").mkdir()
    d = stream_dir(tmp_path / "list", {"stream_id": "kick-1"})
    jsonstate.write(d / "meta.json", [{"platform": "kick"}])  # JSON, but not an object
    cases.append(d)
    (tmp_path / "directory").mkdir()
    d = stream_dir(tmp_path / "directory", {"stream_id": "kick-1"})
    (d / "meta.json").unlink()
    (d / "meta.json").mkdir()  # a directory in its place: reading it raises OSError
    cases.append(d)
    for d in cases:
        durations(monkeypatch, 33833.0, 33011.2)
        with pytest.raises(transcribe.TimelineJump) as err:
            transcribe.check_audio_timeline(d)
        assert "kick-1: the source's timeline and its audio disagree by 821.8 s" in err.value.reason
        assert "33833.0 s" in err.value.reason and "33011.2 s" in err.value.reason
        assert "clippa fetch" not in str(err.value) and "http" not in str(err.value)
        assert str(err.value) == err.value.reason


def test_a_source_without_a_container_duration_is_an_error(tmp_path, monkeypatch):
    """The container is what the audio is compared with: with no duration there is no check to
    make, so it is refused, not passed over (only a missing audio length is passed over)."""
    d = stream_dir(tmp_path, {"stream_id": "kick-1"})
    monkeypatch.setattr(transcribe, "try_duration", lambda path: 3600.0)
    monkeypatch.setattr(transcribe.timeline, "probe", lambda path: {"start_time": 62.0, "duration": None})
    with pytest.raises(ClipperError, match=r"^ffprobe gave no container duration: the timeline "
                                           r"can't be checked$"):
        transcribe.check_audio_timeline(d)


def test_diarize_refuses_a_jumping_source(tmp_path, monkeypatch):
    """`clippa speakers <stream> --diarize` extracts the audio and checks the timeline before it
    diarizes (#440): its speaker times would be off by the jump as a transcript's are."""
    d = stream_dir(tmp_path, {"stream_id": "kick-1", "platform": "kick",
                              "url": "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"})
    durations(monkeypatch, 33833.0, 33011.2)
    monkeypatch.setattr(transcribe, "transcribe_modal", lambda *a, **kw: pytest.fail("diarized"))
    with pytest.raises(ClipperError, match="the source's timeline jumps back 821.8 s"):
        transcribe.diarize(d, {"backend": "modal"})


def test_an_audio_shorter_than_its_container_only_warns(tmp_path, monkeypatch, capsys):
    d = stream_dir(tmp_path, {"stream_id": "kick-1"})
    durations(monkeypatch, 3595.0, 3600.0)
    transcribe.check_audio_timeline(d)
    assert "kick-1: warning: audio.wav is 5.0 s shorter than its source's container" in capsys.readouterr().out


def test_a_source_in_order_passes_quietly(tmp_path, monkeypatch, capsys):
    d = stream_dir(tmp_path, {"stream_id": "kick-1"})
    durations(monkeypatch, 3600.4, 3600.0)  # under the tolerance: ffprobe's rounding
    transcribe.check_audio_timeline(d)
    assert capsys.readouterr().out == ""


def test_a_wav_source_is_not_probed(tmp_path, monkeypatch):
    """The source is the audio itself: there is no container to compare it with."""
    d = stream_dir(tmp_path, {"stream_id": "kick-1"}, source="source.wav")
    monkeypatch.setattr(transcribe, "try_duration", lambda path: pytest.fail("probed the audio"))
    monkeypatch.setattr(transcribe.timeline, "probe", lambda path: pytest.fail("probed the source"))
    transcribe.check_audio_timeline(d)


def test_no_source_file_is_not_probed(tmp_path, monkeypatch):
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "audio.wav").write_bytes(b"wav")
    monkeypatch.setattr(transcribe, "try_duration", lambda path: pytest.fail("probed"))
    transcribe.check_audio_timeline(d)


def test_a_missing_wav_is_not_judged(tmp_path, monkeypatch):
    """try_duration can't tell how long the audio is: nothing to compare, so the source isn't
    even probed."""
    d = stream_dir(tmp_path, {"stream_id": "kick-1"}, source="source.ts")
    (d / "audio.wav").unlink()
    monkeypatch.setattr(transcribe, "try_duration", lambda path: None)
    monkeypatch.setattr(transcribe.timeline, "probe", lambda path: pytest.fail("probed the source"))
    transcribe.check_audio_timeline(d)


def test_run_checks_the_timeline_after_extracting_the_audio(tmp_path, monkeypatch, defaults):
    """The guard reads the audio.wav extract_audio made: moved before it, it would judge an old
    or a missing wav."""
    d = stream_dir(tmp_path, {"stream_id": "kick-1", "platform": "kick",
                              "url": "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"})
    (d / "audio.wav").unlink()
    seen = []

    def extract(stream_dir):
        seen.append("extract")
        (stream_dir / "audio.wav").write_bytes(b"wav")
        return stream_dir / "audio.wav"

    def wav_length(path):
        assert seen == ["extract"], "the guard ran before extract_audio: this isn't the audio"
        return 33833.0

    monkeypatch.setattr(transcribe, "extract_audio", extract)
    monkeypatch.setattr(transcribe, "try_duration", wav_length)
    monkeypatch.setattr(transcribe.timeline, "probe",
                        lambda path: {"start_time": 62.0, "duration": 33011.2})
    with pytest.raises(ClipperError, match="timeline jumps back"):
        transcribe.run(d, defaults["transcribe"])
    assert seen == ["extract"]


def test_a_modal_lookup_finds_the_old_app_until_the_new_one_is_deployed(monkeypatch):
    # #286 pass 3: clippa-whisperx is the default; until `clippa transcribe --deploy` makes
    # it, a lookup finds the old clipper-whisperx. Another missing app still fails.
    import types

    class NotFound(Exception):
        pass

    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace(exception=types.SimpleNamespace(NotFoundError=NotFound)))
    deployed = {"clipper-whisperx"}

    class Ref:
        def __init__(self, app, name):
            self.app, self.name = app, name

        def hydrate(self):
            if self.app not in deployed:
                raise NotFound(self.app)
            return self

    found, used = transcribe.modal_found(Ref, "clippa-whisperx", "WhisperX")
    assert (found.app, used) == ("clipper-whisperx", "clipper-whisperx")
    deployed.add("clippa-whisperx")
    assert transcribe.modal_found(Ref, "clippa-whisperx", "WhisperX")[1] == "clippa-whisperx"
    with pytest.raises(NotFound):
        transcribe.modal_found(Ref, "my-app", "WhisperX")


def test_a_flac_over_the_limit_goes_up_as_opus(tmp_path, defaults, monkeypatch):
    """#422: an 18 h stream's 1.3 GB FLAC took the NAS out of memory in Modal's multipart
    upload (1 GiB and up). Over [transcribe] modal_opus_over_mb the upload is opus instead."""
    tc = {**defaults["transcribe"], "modal_gpu": "L4"}
    assert tc["modal_audio"] == "flac" and tc["modal_opus_over_mb"] == 700
    assert not transcribe.too_big_for_flac(tc, "flac", 700_000_000)
    assert transcribe.too_big_for_flac(tc, "flac", 700_000_001)
    assert not transcribe.too_big_for_flac(tc, "opus", 2_000_000_000)    # already opus
    assert not transcribe.too_big_for_flac({**tc, "modal_opus_over_mb": 0}, "flac", 2_000_000_000)
    assert not transcribe.too_big_for_flac({**tc, "modal_opus_over_mb": -1}, "flac", 2_000_000_000)

    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"\0" * 1044)
    encoded, sent = [], []

    def encode(w, fmt):  # 200 bytes of FLAC, 50 of opus
        encoded.append(fmt)
        return b"x" * {"flac": 200, "opus": 50}[fmt], transcribe.MODAL_CODECS[fmt][0]

    class Worker:
        class transcribe:  # stands in for the Modal method
            @staticmethod
            def remote(data, suffix, settings):
                sent.append((suffix, len(data)))
                return {"gpu": "L4", "timings": {}, "dur": 1.0}

    monkeypatch.setattr(transcribe, "modal_encode", encode)
    monkeypatch.setattr(transcribe, "modal_worker", lambda tc, gpu: Worker)
    monkeypatch.setattr(transcribe, "modal_cost", lambda gpu, wall, out: {"call_s": 1.0, "est_usd": 0.0})  # CI has no modal
    transcribe.transcribe_modal(wav, {**tc, "modal_opus_over_mb": 100e-6})   # a 100-byte limit
    assert encoded == ["flac", "opus"] and sent == [(".ogg", 50)]
    encoded.clear(), sent.clear()
    transcribe.transcribe_modal(wav, {**tc, "modal_opus_over_mb": 200e-6})   # at the limit: lossless
    assert encoded == ["flac"] and sent == [(".flac", 200)]


def test_a_failed_modal_encode_leaves_no_audio_behind(tmp_path, monkeypatch):
    """A killed or failed ffmpeg mustn't leave a GB of audio.modal.flac in the stream folder."""
    wav = tmp_path / "audio.wav"

    def fail(cmd, check):
        (tmp_path / "audio.modal.flac").write_bytes(b"part")   # ffmpeg got partway
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(transcribe.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        transcribe.modal_encode(wav, "flac")
    assert not (tmp_path / "audio.modal.flac").exists()


def test_the_modal_encode_is_given_no_url(tmp_path, monkeypatch):
    """A cached audio.wav (or one beside no source) never went through extract_audio's whitelisted
    decode, so the encode keeps ffmpeg on local files too: a playlist under that name can't send
    it to a url. The encode still writes audio.modal<ext>, reads it back, and takes it away."""
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"wav")
    ran = []

    def run(cmd, check):  # ffmpeg writes the file it was given
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"encoded")

    monkeypatch.setattr(transcribe.subprocess, "run", run)
    assert transcribe.modal_encode(wav, "opus") == (b"encoded", ".ogg")
    cmd = ran[0]
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "file"
    assert cmd.index("-protocol_whitelist") < cmd.index("-i")
    assert cmd[cmd.index("-i") + 1] == str(wav)
    assert "-f" not in cmd  # the wav's own suffix decides, not a forced format
    assert not (tmp_path / "audio.modal.ogg").exists()


def test_a_link_at_the_decodes_temp_is_dropped_before_ffmpeg(tmp_path, monkeypatch, link_or_skip):
    """#444: ffmpeg opens its output with -y and the protocol whitelist doesn't stop a link, so a
    link at audio.tmp.wav would have the decode truncate what it names. The link goes first (the
    link alone), and the decode then writes its own temp as usual."""
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "source.ts").write_bytes(b"source")
    elsewhere = tmp_path / "someone.wav"
    elsewhere.write_bytes(b"someone else's audio")
    link_or_skip(elsewhere, d / "audio.tmp.wav")

    def run(cmd, check):
        assert not (d / "audio.tmp.wav").is_symlink()  # dropped before ffmpeg was given the name
        Path(cmd[-1]).write_bytes(b"decoded")

    monkeypatch.setattr(transcribe.subprocess, "run", run)
    assert transcribe.extract_audio(d) == d / "audio.wav"
    assert (d / "audio.wav").read_bytes() == b"decoded"
    assert elsewhere.read_bytes() == b"someone else's audio"
    assert not (d / "audio.tmp.wav").exists()


def test_a_link_at_the_modal_output_is_dropped_before_ffmpeg(tmp_path, monkeypatch, link_or_skip):
    """The encode's output is opened with -y too: a link there is the target it would truncate, so
    the link goes before ffmpeg runs and the encode still reads its bytes back."""
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"wav")
    elsewhere = tmp_path / "someone.ogg"
    elsewhere.write_bytes(b"someone else's audio")
    link_or_skip(elsewhere, tmp_path / "audio.modal.ogg")
    ran = []

    def run(cmd, check):
        assert not (tmp_path / "audio.modal.ogg").is_symlink()
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"encoded")

    monkeypatch.setattr(transcribe.subprocess, "run", run)
    assert transcribe.modal_encode(wav, "opus") == (b"encoded", ".ogg")
    assert len(ran) == 1
    assert elsewhere.read_bytes() == b"someone else's audio"
    assert not (tmp_path / "audio.modal.ogg").exists()


def test_a_cleanup_that_fails_does_not_mask_the_clipperror(tmp_path, monkeypatch):
    """A temp the share won't remove (a junction, say) must not turn ffmpeg's one-line refusal
    into an OSError out of the finally: the error that brought us there is the one reported."""
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "source.ts").write_bytes(b"source")

    def fail(cmd, check):
        Path(cmd[-1]).write_bytes(b"half")  # a decode that got partway
        raise subprocess.CalledProcessError(1, cmd)

    def denied(self, *a, **kw):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(transcribe.subprocess, "run", fail)
    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(ClipperError, match=r"^kick-1: ffmpeg couldn't decode its source \(exit 1\)$"):
        transcribe.extract_audio(d)

