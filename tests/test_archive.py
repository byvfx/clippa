"""`clippa archive add` of a YouTube channel: listed with approximate dates. Offline: yt-dlp
is a stand-in."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from clippa import archive, config
from clippa.errors import ClipperError


def test_youtube_listing_carries_approximate_dates(monkeypatch):
    calls = []

    class Done:
        returncode, stderr = 0, ""
        stdout = json.dumps({"channel": "Some Channel", "entries": [
            {"id": "abcdefghijk", "title": "new", "duration": 600, "timestamp": 1762819200},
            {"id": "bcdefghijkl", "title": "undated", "duration": 700}]})

    monkeypatch.setattr(archive.ytdl, "run", lambda *a: calls.append(a) or Done())
    items = archive.list_youtube("https://www.youtube.com/@x/videos")
    assert "youtubetab:approximate_date" in calls[0]
    assert [(i["id"], i["date"]) for i in items] == [("yt-abcdefghijk", "2025-11-11"), ("yt-bcdefghijkl", None)]


def test_an_unavailable_video_in_the_listing_is_skipped(monkeypatch):
    class Done:
        returncode, stderr = 0, ""
        stdout = json.dumps({"channel": "C", "entries": [None, {"id": "abcdefghijk", "title": "kept"}, {"title": "no id"}]})

    monkeypatch.setattr(archive.ytdl, "run", lambda *a: Done())
    assert [i["id"] for i in archive.list_youtube("https://www.youtube.com/@x/videos")] == ["yt-abcdefghijk"]


def test_one_of_our_streams_is_never_transcribed_again(tmp_path, monkeypatch):
    """A `clippa story` item with a `dir` is transcribed by the pipeline there (item_dir)."""
    (tmp_path / "clipper.toml").write_text(f'[paths]\narchive = "{(tmp_path / "archive").as_posix()}"\n',
                                           encoding="utf-8")
    cfg = config.load(tmp_path / "clipper.toml", use_local=False)
    d = archive.subject_dir(cfg, "s")
    d.mkdir(parents=True)
    stream = tmp_path / "streams" / "kick-1"
    stream.mkdir(parents=True)
    (stream / "words.jsonl").write_text("{}\n", encoding="utf-8")
    archive.save_catalog(d, {"kick-1": {"id": "kick-1", "source": "kick", "duration_s": 60.0, "dir": str(stream)},
                             "yt-a": {"id": "yt-a", "source": "youtube", "duration_s": 30.0}})
    seen = []
    monkeypatch.setattr(archive, "transcribe_estimate", lambda cfg, s, todo, gpu, par: seen.append(todo) or "")
    archive.run_transcribe(cfg, "s", None, False, "L4", 1, dry_run=True)
    assert [it["id"] for it in seen[0]] == ["yt-a"]
    # status and the link list find that transcript too, on a dead link
    archive.save_json(d, "links.json", {"kick-1": {"status": "dead", "since": "2026-11-01T00:00:00", "reason": "gone"}})
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        archive.status(cfg, "s")
        archive.list_links(cfg, "s", None)
    assert "1/2 transcribed" in out.getvalue() and "(1 of them transcribed)" in out.getvalue()
    assert "transcribed  kick-1" in out.getvalue()


@pytest.mark.parametrize("kept", [None, b""])
def test_a_short_item_with_no_kept_video_goes_from_the_catalog(tmp_path, kept):
    """The item is too short once a listing fills in its length, and nothing was made of it: it
    goes from the catalog, whether there is no video at all or an empty one a failure left before
    a download's first byte."""
    d = tmp_path / "archive" / "s"
    idir = d / "kick-1"
    idir.mkdir(parents=True)
    if kept is not None:
        (idir / "source.mp4").write_bytes(kept)
    cat = {"kick-1": {"id": "kick-1", "source": "kick", "title": "t"}}
    listed = [{"id": "kick-1", "source": "kick", "title": "t", "duration_s": 30.0}]
    assert "kick-1" not in archive._merge_listing(d, cat, listed, 60.0)


def test_a_short_item_with_a_kept_video_the_share_won_t_stat_stays(tmp_path, monkeypatch):
    """#444 review (Grok): kept_video is _real_copy alone, and _real_copy reads a refused stat as
    absent. A real, non-empty source.mp4 the share won't answer for then looked like no video
    kept: the item was deleted from the catalog, and a short item is filtered out of the listing,
    so nothing added it back and its file was orphaned. Only a name the share answers for drops
    the item."""
    d = tmp_path / "archive" / "s"
    idir = d / "kick-1"
    idir.mkdir(parents=True)
    (idir / "source.mp4").write_bytes(b"kept")
    cat = {"kick-1": {"id": "kick-1", "source": "kick", "title": "t"}}
    listed = [{"id": "kick-1", "source": "kick", "title": "t", "duration_s": 30.0}]
    real_lstat = Path.lstat

    def blind_lstat(self):
        if self.name == "source.mp4":
            raise PermissionError(13, "Access is denied")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", blind_lstat)
    merged = archive._merge_listing(d, cat, listed, 60.0)
    assert merged["kick-1"]["duration_s"] == 30.0  # kept, and the length it now knows
    assert (idir / "source.mp4").read_bytes() == b"kept"


@pytest.fixture
def kick_cfg(defaults, tmp_path):
    return {**defaults, "paths": {**defaults["paths"], "modal_runs": tmp_path / "modal_runs.jsonl"}}


def _item():
    return {"id": "kick-1", "source": "kick", "m3u8": "https://example.invalid/master.m3u8",
            "duration_s": 33011.2}


def _downloads(monkeypatch, idir, runs, seen, wav_s=33833.0, container_s=33011.2):
    """yt-dlp, the wav ffmpeg would make, and the two numbers transcribe's guard compares: `runs`
    counts the downloads, `seen` names the source each extract read."""
    class Done:
        returncode, stderr = 0, ""

    def ytdl_run(*a):
        runs.append(a)
        (idir / "source.ts").write_bytes(b"downloaded")
        return Done()

    monkeypatch.setattr(archive.ytdl, "run", ytdl_run)
    monkeypatch.setattr(archive.transcribe, "modal_settings", lambda *a, **k: {})
    monkeypatch.setattr(archive.transcribe, "modal_worker", lambda *a: object())

    def extract(stream_dir, *, trust_mtime=True):
        assert trust_mtime is False  # #448: an archive item's wav needs its stamp
        seen.append(archive.transcribe.source_file(stream_dir).name)
        (stream_dir / "audio.wav").write_bytes(b"wav")
        return stream_dir / "audio.wav"

    monkeypatch.setattr(archive.transcribe, "extract_audio", extract)
    monkeypatch.setattr(archive.transcribe, "try_duration", lambda path: wav_s)
    monkeypatch.setattr(archive.transcribe.timeline, "probe",
                        lambda path: {"start_time": 62.0, "duration": container_s})


def _transcribes(monkeypatch):
    """Modal's answer and the flac encode, for an item that gets that far."""
    class Worker:
        class transcribe:
            @staticmethod
            def remote(data, suffix, settings):
                return {"dur": 33011.2, "segments": [
                    {"text": "hi", "words": [{"word": "hi", "start": 0.0, "end": 1.0, "score": 0.9}]}]}

    monkeypatch.setattr(archive.transcribe, "modal_worker", lambda *a: Worker)
    monkeypatch.setattr(archive.transcribe, "modal_cost", lambda gpu, wall, out: {"call_s": 1.0, "est_usd": 0.0})

    def run(args, **kw):  # the flac encode writes its file, as ffmpeg does
        Path(args[-1]).write_bytes(b"flac")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(archive.subprocess, "run", run)


def test_a_refused_download_is_kept_aside_not_dropped(kick_cfg, tmp_path, monkeypatch):
    """#444: a source the timeline check refuses is the only complete copy there is (a Kick VOD's
    m3u8 is never refreshed, so a second download that fails leaves nothing), so it is renamed
    aside rather than left as source.ts for the next run to take for a partial and unlink."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    with pytest.raises(ClipperError,
                       match=r"audio disagree.*\(kept as source\.refused\.ts\)$"):
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert len(runs) == 1 and seen == ["source.ts"]
    assert not (idir / "source.ts").exists() and not (idir / "source.ts.ytdl").exists()
    assert (idir / "source.refused.ts").read_bytes() == b"downloaded"
    # the rename is the record when it works: no marker written
    assert not (idir / ".refused").exists()
    assert not (idir / "words.jsonl").exists() and not (idir / "transcript.json").exists()


def test_a_refused_item_keeps_the_numbers_and_no_url(kick_cfg, tmp_path, monkeypatch):
    """#444: `archive` writes this exception into error.txt, so the fixup command (`clippa fetch
    <url>`) must not be in it: the reason's own numbers are."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text(json.dumps({
        "stream_id": "kick-1", "platform": "kick",
        "url": "https://kick.com/kino/videos/019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"}), encoding="utf-8")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    with pytest.raises(ClipperError) as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    msg = str(err.value)
    assert "jumps back 821.8 s" in msg and "(kept as source.refused.ts)" in msg
    assert "http" not in msg and "clippa fetch" not in msg
    assert (idir / "source.refused.ts").read_bytes() == b"downloaded"


def test_a_jumping_download_with_a_broken_meta_json_is_still_kept_aside(kick_cfg, tmp_path, monkeypatch):
    """#444: the refusal's advice comes from meta.json, but a corrupt one must not escape as a
    ValueError/AttributeError — `archive` catches only ClipperError, so the download would stay at
    source.ts with nothing recorded and the next run would drop it as a partial."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text("{", encoding="utf-8")  # a half-written file
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    with pytest.raises(ClipperError, match=r"audio disagree.*\(kept as source\.refused\.ts\)$") as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert "821.8 s" in str(err.value) and "clippa fetch" not in str(err.value)
    assert len(runs) == 1 and seen == ["source.ts"]
    assert not (idir / "source.ts").exists()
    assert (idir / "source.refused.ts").read_bytes() == b"downloaded"
    assert (idir / "meta.json").read_text(encoding="utf-8") == "{"


def test_a_second_run_reuses_the_refused_source_without_downloading(kick_cfg, tmp_path, monkeypatch):
    """The refused name is the run's source, so a rerun reads it (extract_audio's `source.*`
    glob finds it), is refused again, and never asks Kick for a copy it can't give."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    with pytest.raises(ClipperError):
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    runs.clear()
    seen.clear()
    with pytest.raises(ClipperError, match=r"audio disagree") as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert runs == [] and seen == ["source.refused.ts"]  # the name sorts before source.ts
    assert str(err.value).endswith("(kept as source.refused.ts)")  # the copy is here, and said so
    assert (idir / "source.refused.ts").read_bytes() == b"downloaded"
    assert not (idir / "source.ts").exists()


def test_a_read_failure_from_the_timeline_check_is_not_set_aside(kick_cfg, tmp_path, monkeypatch):
    """#444 review: only a jump is the content's own property, so only it is renamed aside and
    reused. A probe that couldn't read the fresh download (or gave no container duration) is a read
    failure a retry may replace, so the run fails with the download still at source.ts: renamed
    aside, it would be reused forever and the item would never transcribe."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)

    def unreadable(stream_dir):
        raise ClipperError("kick-1: ffprobe couldn't read it (exit 1)")

    monkeypatch.setattr(archive.transcribe, "check_audio_timeline", unreadable)
    with pytest.raises(ClipperError, match=r"ffprobe couldn't read it"):
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert len(runs) == 1 and seen == ["source.ts"]
    assert (idir / "source.ts").read_bytes() == b"downloaded"


def test_a_download_marks_any_wav_beside_it_stale(kick_cfg, tmp_path, monkeypatch):
    """#444 review (Grok): the archive download goes through yt-dlp too, which stamps source.ts
    with the VOD's Last-Modified — older than an audio.wav a failed earlier run left behind. The
    mtime rule would then serve that file's audio, and where the two lengths agree the timeline
    check can't tell, so the download is the moment to say the wav is stale."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / "audio.wav").write_bytes(b"the previous run's wav")  # left by a run that failed later
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    marked = []
    real_mark = archive.transcribe.mark_stale

    def record(stream_dir):
        marked.append(stream_dir)
        real_mark(stream_dir)

    monkeypatch.setattr(archive.transcribe, "mark_stale", record)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert marked == [idir] and len(runs) == 1


def test_a_download_whose_stale_mark_fails_is_kept_for_the_next_run(
        kick_cfg, tmp_path, monkeypatch):
    """#444 review (Grok): the stale mark is written right after a finished download, and its
    ClipperError used to end the run with nothing recorded. The next run then saw no kept video, no
    aside file and no marker, took source.ts for a partial and unlinked it before fetching again
    (a Kick VOD's m3u8 is never refreshed, so a re-download that fails leaves nothing). The
    refusal marker keeps the download, and the next run reuses it."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    real_touch = Path.touch

    def denied(self, *a, **kw):  # the stale mark's own write, after the download
        if self.name == "audio.wav.stale":
            raise PermissionError(13, "Access is denied")
        return real_touch(self, *a, **kw)

    monkeypatch.setattr(Path, "touch", denied)
    with pytest.raises(ClipperError, match=r"couldn't record that its audio\.wav is stale"):
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert len(runs) == 1 and seen == []  # nothing was read: the run stopped at the mark
    assert (idir / "source.ts").read_bytes() == b"downloaded"  # the finished download stays
    assert (idir / ".refused").is_file()
    assert "couldn't record that its audio.wav is stale" in (idir / ".refused").read_text(encoding="utf-8")

    monkeypatch.setattr(Path, "touch", real_touch)
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)  # a guard that passes this time
    _transcribes(monkeypatch)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert len(runs) == 1 and seen == ["source.ts"]  # the marked download reused, not fetched again
    assert not (idir / ".refused").exists()  # through: the stale marker goes
    assert (idir / "words.jsonl").exists() and (idir / "transcript.json").exists()


def test_a_stale_mark_that_cannot_be_marked_says_a_later_run_may_replace_it(
        kick_cfg, tmp_path, monkeypatch):
    """#444 review (Grok): where the stale mark fails and the marker can't be written either,
    nothing durable records the finished download: the line says it is still source.ts and that a
    later run may replace it, rather than reading as if it were kept."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    real_touch = Path.touch

    def denied(self, *a, **kw):
        if self.name == "audio.wav.stale":
            raise PermissionError(13, "Access is denied")
        return real_touch(self, *a, **kw)

    def no_write(*a, **kw):  # the marker's own write, after the stale mark has failed
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "touch", denied)
    monkeypatch.setattr(archive, "open", no_write, raising=False)
    with pytest.raises(ClipperError) as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    msg = str(err.value)
    assert "couldn't record that its audio.wav is stale" in msg
    assert "still source.ts" in msg and "a later run may replace it" in msg
    assert str(idir) not in msg and "/" not in msg and "\\" not in msg
    assert len(runs) == 1 and seen == []
    assert (idir / "source.ts").read_bytes() == b"downloaded"
    assert not (idir / ".refused").exists()


def test_a_kept_video_marks_nothing_stale(kick_cfg, tmp_path, monkeypatch):
    """Nothing was downloaded, so nothing beside the audio changed: the wav is the one this
    folder's source made, and marking it would cost a full decode for no reason."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / "source.mp4").write_bytes(b"kept")
    (idir / "audio.wav").write_bytes(b"wav of the kept source")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    marked = []
    monkeypatch.setattr(archive.transcribe, "mark_stale", lambda d: marked.append(d))
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert marked == [] and runs == [] and seen == ["source.mp4"]
    assert not (idir / "source.refused.ts").exists() and not (idir / ".refused").exists()


def test_a_download_that_transcribes_is_unlinked_as_before(kick_cfg, tmp_path, monkeypatch):
    """A source that passes the guard is this run's alone: it goes once its audio is out."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    cost = archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert len(runs) == 1 and seen == ["source.ts"]
    assert not (idir / "source.ts").exists() and not (idir / "source.refused.ts").exists()
    assert (idir / "words.jsonl").exists() and (idir / "transcript.json").exists()
    assert cost["duration_s"] == 33011.2


def test_a_kept_video_is_used_and_never_unlinked(kick_cfg, tmp_path, monkeypatch):
    """`archive video` saved source.mp4: it is the source, nothing is downloaded, and it stays
    (the refused name never comes into it)."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / "source.mp4").write_bytes(b"kept")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    assert archive.kept_video(idir) == idir / "source.mp4"  # a real one is kept
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert runs == [] and seen == ["source.mp4"]
    assert (idir / "source.mp4").read_bytes() == b"kept"
    assert not (idir / "source.ts").exists() and not (idir / "source.refused.ts").exists()


def test_a_link_at_the_kept_video_is_no_kept_video(kick_cfg, tmp_path, monkeypatch, link_or_skip):
    """#444: a link at source.mp4 is not a video kept here, and extract_audio refuses a linked
    source, so counting it as kept stopped the item for good (nothing downloaded, nothing read).
    Treated as absent, the item downloads; the link is dropped, never what it names."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.mp4"
    elsewhere.write_bytes(b"someone else's")
    link_or_skip(elsewhere, idir / "source.mp4")
    assert (idir / "source.mp4").is_symlink()
    assert archive.kept_video(idir) is None
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert len(runs) == 1 and seen == ["source.ts"]
    assert not (idir / "source.mp4").is_symlink() and not (idir / "source.mp4").exists()
    assert elsewhere.read_bytes() == b"someone else's"
    assert (idir / "words.jsonl").exists() and (idir / "transcript.json").exists()


def test_an_empty_kept_video_is_no_kept_video(kick_cfg, tmp_path, monkeypatch):
    """#444: a zero-byte source.mp4 is what a failure leaves before a download's first byte, and
    ffmpeg can't read it: counted as kept, the item never downloaded and never advanced. It is
    no copy, so it goes and the item downloads."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / "source.mp4").write_bytes(b"")
    assert archive.kept_video(idir) is None
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert len(runs) == 1 and seen == ["source.ts"]
    assert not (idir / "source.mp4").exists()
    assert (idir / "words.jsonl").exists() and (idir / "transcript.json").exists()


@pytest.mark.parametrize("maker", ["link_or_skip", "junction_or_skip"])
def test_the_kept_video_name_is_clear_before_the_download(
        kick_cfg, tmp_path, monkeypatch, request, maker):
    """#444 review (Grok): a link at source.mp4 was only dropped after the download, so one the
    share wouldn't clear failed the run with a finished source.ts beside it, which the next run
    took for a partial and unlinked before fetching again. It goes before the download now."""
    make = request.getfixturevalue(maker)
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"someone else's")
    make(elsewhere, idir / "source.mp4", directory=True)
    assert archive.kept_video(idir) is None
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    fetch = archive.ytdl.run

    def checked(*a):
        assert not (idir / "source.mp4").exists()  # no shadowing name for this run to read
        return fetch(*a)

    monkeypatch.setattr(archive.ytdl, "run", checked)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert len(runs) == 1 and seen == ["source.ts"]
    assert (elsewhere / "keep.txt").read_bytes() == b"someone else's"


@pytest.mark.parametrize("maker", ["link_or_skip", "junction_or_skip"])
def test_a_kept_video_the_share_will_not_clear_costs_no_download(
        kick_cfg, tmp_path, monkeypatch, request, maker):
    """#444 review (Grok): the same drop failing must stop the run *before* the download is spent
    and before a finished source.ts is taken for a partial, so the copy the last run completed is
    still here and nothing is fetched."""
    make = request.getfixturevalue(maker)
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / "source.ts").write_bytes(b"the last run's finished download")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"someone else's")
    make(elsewhere, idir / "source.mp4", directory=True)
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)

    def denied(path):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(archive, "remove_link", denied)
    with pytest.raises(ClipperError) as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert str(err.value) == "kick-1: couldn't remove the link at source.mp4 (Access is denied)"
    assert runs == [] and seen == []
    assert (idir / "source.ts").read_bytes() == b"the last run's finished download"
    assert (elsewhere / "keep.txt").read_bytes() == b"someone else's"


def test_a_kept_video_the_timeline_refuses_is_left_as_it_is(kick_cfg, tmp_path, monkeypatch):
    """A kept video is not this run's to drop: refused, it stays at source.mp4 (under neither
    name the download path moves), nothing else is fetched, and no `(kept as ...)` claims it was."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / "source.mp4").write_bytes(b"kept")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    with pytest.raises(ClipperError, match=r"timeline and its audio disagree") as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert runs == [] and seen == ["source.mp4"]
    assert (idir / "source.mp4").read_bytes() == b"kept"
    assert not (idir / "source.ts").exists() and not (idir / "source.refused.ts").exists()
    assert "(kept as" not in str(err.value)


def test_a_set_aside_source_that_passes_is_kept_not_unlinked(kick_cfg, tmp_path, monkeypatch):
    """A refused source that is now in order transcribes and stays at source.refused.ts: the
    success path unlinks only what this run downloaded (`fresh`), never a source already here."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / "source.refused.ts").write_bytes(b"aside")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert runs == [] and seen == ["source.refused.ts"]
    assert (idir / "source.refused.ts").read_bytes() == b"aside"
    assert not (idir / "source.ts").exists()
    assert (idir / "words.jsonl").exists() and (idir / "transcript.json").exists()


def test_a_link_at_the_refused_name_is_removed_and_the_item_downloads(
        kick_cfg, tmp_path, monkeypatch, capsys, link_or_skip):
    """#444: a link under the refused name is not the copy kept here, and left there it sorts
    before source.ts in extract_audio's glob and is refused as a link: every run downloaded and
    none advanced, the next dropping the copy the run before had made. It is removed, so the
    item downloads (and a refused download is set aside) as it would with nothing there; the
    link's target keeps its bytes, and the next run reuses the copy instead of downloading."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.ts"
    elsewhere.write_bytes(b"someone else's")
    link_or_skip(elsewhere, idir / "source.refused.ts")
    assert (idir / "source.refused.ts").is_symlink()
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    with pytest.raises(ClipperError, match=r"audio disagree.*\(kept as source\.refused\.ts\)$"):
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert "kick-1: removed a link at source.refused.ts" in capsys.readouterr().out
    assert len(runs) == 1 and seen == ["source.ts"]
    assert not (idir / "source.refused.ts").is_symlink()
    assert (idir / "source.refused.ts").read_bytes() == b"downloaded"
    assert elsewhere.read_bytes() == b"someone else's"
    runs.clear()
    seen.clear()
    with pytest.raises(ClipperError, match=r"timeline and its audio disagree"):
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert runs == [] and seen == ["source.refused.ts"]  # one download in all
    assert "removed a link" not in capsys.readouterr().out  # nothing left to remove


def test_a_junction_at_the_refused_name_is_removed_and_the_item_downloads(
        kick_cfg, tmp_path, monkeypatch, capsys, junction_or_skip):
    """#444: a junction is a folder to Path.unlink, which raises on Windows, so it would stop every
    run before the download. It is rmdir'ed instead (the link alone), so the item downloads and is
    set aside as usual, and the folder it named keeps its contents."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"someone else's")
    junction_or_skip(elsewhere, idir / "source.refused.ts")
    assert os.path.isjunction(idir / "source.refused.ts")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    with pytest.raises(ClipperError, match=r"audio disagree.*\(kept as source\.refused\.ts\)$"):
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert "kick-1: removed a link at source.refused.ts" in capsys.readouterr().out
    assert len(runs) == 1 and seen == ["source.ts"]
    assert not os.path.isjunction(idir / "source.refused.ts")
    assert (idir / "source.refused.ts").read_bytes() == b"downloaded"
    assert (elsewhere / "keep.txt").read_bytes() == b"someone else's"


@pytest.mark.parametrize("maker", ["link_or_skip", "junction_or_skip"])
def test_a_link_planted_at_the_refused_name_during_the_download_is_dropped(
        kick_cfg, tmp_path, monkeypatch, capsys, request, maker):
    """#444 review: the drop before the download covers a link already there, but the download is
    the long part. One planted at source.refused.ts while it runs sorts before source.ts in
    extract_audio's glob, so the item would be refused as a linked source — and the refusal's
    rename cannot replace a link, so it would stick with both the link and the finished source.ts,
    and every later run would refuse it again. Dropped after the download too (the link alone), the
    item reads source.ts and transcribes, and what the link named keeps its contents."""
    make = request.getfixturevalue(maker)
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"someone else's")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)

    class Done:
        returncode, stderr = 0, ""

    def ytdl_run(*a):
        runs.append(a)
        make(elsewhere, idir / "source.refused.ts", directory=True)  # planted while it runs
        assert (idir / "source.refused.ts").is_dir()
        (idir / "source.ts").write_bytes(b"downloaded")
        return Done()

    monkeypatch.setattr(archive.ytdl, "run", ytdl_run)
    _transcribes(monkeypatch)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert "kick-1: removed a link at source.refused.ts" in capsys.readouterr().out
    assert len(runs) == 1 and seen == ["source.ts"]
    assert not (idir / "source.refused.ts").exists()  # the link went, not its target
    assert (elsewhere / "keep.txt").read_bytes() == b"someone else's"
    assert not (idir / "source.ts").exists()  # this run's download, transcribed
    assert (idir / "words.jsonl").exists() and (idir / "transcript.json").exists()


@pytest.mark.parametrize("maker", ["link_or_skip", "junction_or_skip"])
def test_a_drop_that_fails_after_the_download_marks_it_for_the_next_run(
        kick_cfg, tmp_path, monkeypatch, capsys, request, maker):
    """#444 review (Grok): the post-download drop can fail with a finished source.ts beside the
    name (one planted while the long download ran). Raised alone, the next run sees no kept video,
    no aside file and no marker, takes that download for a partial and unlinks it before fetching
    again. The marker makes it the copy this run left: the next run downloads nothing, reads
    source.ts, and a guard that passes transcribes it and clears the marker."""
    make = request.getfixturevalue(maker)
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"someone else's")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)

    class Done:
        returncode, stderr = 0, ""

    def ytdl_run(*a):
        runs.append(a)
        make(elsewhere, idir / "source.refused.ts", directory=True)  # planted while it runs
        (idir / "source.ts").write_bytes(b"downloaded")
        return Done()

    monkeypatch.setattr(archive.ytdl, "run", ytdl_run)
    real_remove_link = archive.remove_link

    def denied(path):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(archive, "remove_link", denied)
    with pytest.raises(ClipperError) as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert "couldn't remove the link at source.refused.ts" in str(err.value)
    assert len(runs) == 1 and seen == []  # the drop failed before any audio was read
    assert (idir / "source.ts").read_bytes() == b"downloaded"  # the finished download stays
    assert (idir / ".refused").is_file()
    assert "couldn't remove the link at source.refused.ts" in (idir / ".refused").read_text(encoding="utf-8")
    assert (elsewhere / "keep.txt").read_bytes() == b"someone else's"  # the target keeps its bytes

    monkeypatch.setattr(archive, "remove_link", real_remove_link)
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)  # no plant this time, and it passes
    _transcribes(monkeypatch)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert "kick-1: removed a link at source.refused.ts" in capsys.readouterr().out
    assert len(runs) == 1 and seen == ["source.ts"]  # the marked download reused, not fetched again
    assert (idir / "source.ts").read_bytes() == b"downloaded"  # not this run's to drop
    assert not (idir / ".refused").exists()  # through: the stale marker goes
    assert (idir / "words.jsonl").exists() and (idir / "transcript.json").exists()


@pytest.mark.parametrize("maker", ["link_or_skip", "junction_or_skip"])
def test_a_drop_that_fails_and_cannot_be_marked_says_a_later_run_may_replace_it(
        kick_cfg, tmp_path, monkeypatch, request, maker):
    """#444 review (Grok): where the post-download drop fails and the marker can't be written
    either, nothing durable records the finished download: the line says it is still source.ts
    and that a later run may replace it, rather than reading as if it were kept."""
    make = request.getfixturevalue(maker)
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"someone else's")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)

    class Done:
        returncode, stderr = 0, ""

    def ytdl_run(*a):
        runs.append(a)
        make(elsewhere, idir / "source.refused.ts", directory=True)  # planted while it runs
        (idir / "source.ts").write_bytes(b"downloaded")
        return Done()

    def denied(path):
        raise PermissionError(13, "Access is denied")

    def no_write(*a, **kw):  # the marker's own write, after the drop has failed
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(archive.ytdl, "run", ytdl_run)
    monkeypatch.setattr(archive, "remove_link", denied)
    monkeypatch.setattr(archive, "open", no_write, raising=False)
    with pytest.raises(ClipperError) as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    msg = str(err.value)
    assert "couldn't remove the link at source.refused.ts" in msg
    assert "still source.ts" in msg and "a later run may replace it" in msg
    assert str(idir) not in msg and "/" not in msg and "\\" not in msg
    assert len(runs) == 1 and seen == []
    assert (idir / "source.ts").read_bytes() == b"downloaded"
    assert not (idir / ".refused").exists()
    assert (elsewhere / "keep.txt").read_bytes() == b"someone else's"  # the link left, its target whole


def test_a_link_the_share_will_not_remove_stops_before_downloading(
        kick_cfg, tmp_path, monkeypatch, link_or_skip):
    """A link that can't be removed would bring the loop back, so the run says so in one line
    and downloads nothing over it (a failed download there leaves the item where it was, 33 GB
    the poorer)."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.ts"
    elsewhere.write_bytes(b"someone else's")
    link_or_skip(elsewhere, idir / "source.refused.ts")
    assert (idir / "source.refused.ts").is_symlink()
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)

    def denied(self, missing_ok=False):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(ClipperError) as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    msg = str(err.value)
    assert msg == ("kick-1: couldn't remove the link at source.refused.ts (Access is denied)")
    assert runs == [] and seen == []
    assert elsewhere.read_bytes() == b"someone else's"


def test_a_refused_rename_is_one_line_and_the_download_stays(kick_cfg, tmp_path, monkeypatch):
    """#444: a share that refuses the rename would otherwise replace the timeline's reason with a
    traceback (and the next run would take source.ts for a partial): it stays one line naming
    the download, with the system's reason and no path."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)

    def denied(self, target):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(ClipperError) as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    msg = str(err.value)
    assert "timeline and its audio disagree" in msg and "still source.ts" in msg
    assert "Access is denied" in msg
    assert str(idir) not in msg and "/" not in msg and "\\" not in msg
    assert (idir / "source.ts").read_bytes() == b"downloaded"
    assert not (idir / "source.refused.ts").exists()


def test_a_refused_rename_leaves_a_marker_and_the_next_run_keeps_the_download(
        kick_cfg, tmp_path, monkeypatch):
    """#444 review: the marker is written once the rename has failed, so a share that refuses the
    rename still leaves the refusal behind. Without it the next run sees no source.refused.ts, takes
    the complete download for a partial and unlinks it (a Kick VOD's m3u8 is never refreshed, so a
    re-download that then fails leaves nothing): with it the item refuses source.ts again instead."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)

    def denied(self, target):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(ClipperError, match=r"still source\.ts"):
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert (idir / "source.ts").read_bytes() == b"downloaded"
    assert (idir / ".refused").is_file() and not (idir / "source.refused.ts").exists()
    runs.clear()
    seen.clear()
    with pytest.raises(ClipperError, match=r"audio disagree") as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert runs == [] and seen == ["source.ts"]  # no download, and nothing dropped
    assert (idir / "source.ts").read_bytes() == b"downloaded"
    assert str(err.value).endswith("(kept as source.ts)")  # the marked download, and where it is
    assert (idir / ".refused").is_file()


def test_a_marker_with_no_source_is_dropped_and_the_item_downloads(
        kick_cfg, tmp_path, monkeypatch, capsys):
    """#444 review: the marker counts only while the download it stands for is still here. A
    source deleted and its marker left would otherwise never be downloaded again: extract_audio
    serves the audio.wav beside it and the timeline check has nothing to compare with, so the item
    would transcribe the refused times and then clear the marker. Dropped, it downloads first."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / ".refused").write_text("timeline and its audio disagree\n", encoding="utf-8")
    (idir / "audio.wav").write_bytes(b"refused audio")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert "kick-1: removed the refusal marker, no source left to refuse" in capsys.readouterr().out
    # the download came first, and extract read the new copy (the helper reads source_file, which
    # raises with no source): the wav left beside it is never what is transcribed
    assert len(runs) == 1 and seen == ["source.ts"]
    assert not (idir / ".refused").exists()
    assert (idir / "words.jsonl").exists() and (idir / "transcript.json").exists()


def test_a_marker_alone_is_dropped_and_the_item_downloads(kick_cfg, tmp_path, monkeypatch, capsys):
    """The same with nothing beside it: a marker whose folder holds no source at all is no refusal,
    so the item is downloaded and transcribed as any other."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / ".refused").write_text("timeline and its audio disagree\n", encoding="utf-8")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert "kick-1: removed the refusal marker, no source left to refuse" in capsys.readouterr().out
    assert len(runs) == 1 and seen == ["source.ts"]
    assert not (idir / ".refused").exists()
    assert (idir / "words.jsonl").exists() and (idir / "transcript.json").exists()


def test_a_real_copy_the_share_won_t_stat_is_absent_not_an_error(tmp_path, monkeypatch):
    """#444 review (Grok): on Python 3.12 `is_symlink`, `is_file` and `stat` re-raise a stat the
    share refuses (only ENOENT is swallowed), so testing a name outside the try killed the run with
    a bare PermissionError — before item_source's decision, and with no one-line error to read. A
    name that can't be stat'ed is no copy, as the docstring already promised."""
    p = tmp_path / "source.ts"
    p.write_bytes(b"downloaded")
    assert archive._real_copy(p) is True
    real_lstat = Path.lstat

    def blind_lstat(self):  # the share won't answer for this name
        if self.name == "source.ts":
            raise PermissionError(13, "Access is denied")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", blind_lstat)
    assert archive._real_copy(p) is False  # not a raise: absent, as a share that won't say


def test_a_name_the_share_won_t_stat_is_one_line_before_a_download_starts(
        tmp_path, monkeypatch):
    """#444 review: the drop that runs before a download tests each name with is_link, which
    re-raises the share's refusal on 3.12. That must be one line naming the stream (so nothing is
    fetched behind a name nobody could read), not a traceback out of the stage."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / "source.refused.ts").write_bytes(b"i have no idea")
    real_lstat = Path.lstat

    def blind_lstat(self):
        if self.name == "source.refused.ts":
            raise PermissionError(13, "Access is denied")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", blind_lstat)
    with pytest.raises(ClipperError) as dropped:
        archive._drop_shadowing(idir, idir / "source.mp4", idir / "source.refused.ts")
    assert str(dropped.value) == ("kick-1: couldn't tell whether source.refused.ts is a link "
                                  "(Access is denied)")
    with pytest.raises(ClipperError) as emptied:
        archive._drop_empty(idir / "source.refused.ts", idir.name)
    assert str(emptied.value) == "kick-1: couldn't tell what source.refused.ts is (Access is denied)"
    assert str(idir) not in str(dropped.value) and str(idir) not in str(emptied.value)


def test_a_marker_beside_a_source_the_share_won_t_stat_is_not_dropped(
        kick_cfg, tmp_path, monkeypatch, capsys):
    """#444 review: _real_copy reads a failed stat as an absent file, so a marker beside a source.ts
    the share won't stat looked like one standing for nothing: cleared, the only complete copy was
    unlinked as a partial before a download that may fail, leaving neither. The share can't say the
    file is gone, so the marker stays and the copy is read (and refused) as it is.

    Grok: patching the name's is_symlink hid the real path — 3.12 re-raises the refused stat from
    it (and from is_file), so the run died with that PermissionError before _unreadable was ever
    consulted. Only lstat is patched now: the real is_symlink raises from it, and _real_copy has to
    turn that into an absence rather than a traceback."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / ".refused").write_text("timeline and its audio disagree\n", encoding="utf-8")
    (idir / "source.ts").write_bytes(b"downloaded")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    real_lstat = Path.lstat

    def blind_lstat(self):
        if self.name == "source.ts":
            raise PermissionError(13, "Access is denied")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", blind_lstat)
    with pytest.raises(ClipperError, match=r"audio disagree") as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert runs == [] and seen == ["source.ts"]  # nothing downloaded over it, and it was read
    assert str(err.value).endswith("(kept as source.ts)")
    assert "removed the refusal marker" not in capsys.readouterr().out
    assert (idir / "source.ts").read_bytes() == b"downloaded"
    assert (idir / ".refused").is_file()


def test_a_marker_the_share_won_t_stat_is_still_a_marker(kick_cfg, tmp_path, monkeypatch, capsys):
    """#444 review (Grok): the same name, the other way round. `Path.exists()` raises where the
    share won't stat a name (or, read as absence, says there is no marker), and a readable source.ts
    beside it made the item "fresh": _drop_partial unlinked the only complete copy before a
    download that may fail. Unstattable counts as there, so the marker stands and nothing goes."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / ".refused").write_text("timeline and its audio disagree\n", encoding="utf-8")
    (idir / "source.ts").write_bytes(b"downloaded")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    real_lstat, real_stat = Path.lstat, Path.stat

    def blind_lstat(self):  # the share won't say whether the marker is there
        if self.name == ".refused":
            raise PermissionError(13, "Access is denied")
        return real_lstat(self)

    def blind_stat(self, **kw):  # nor whether its target is
        if self.name == ".refused":
            raise PermissionError(13, "Access is denied")
        return real_stat(self, **kw)

    monkeypatch.setattr(Path, "lstat", blind_lstat)
    monkeypatch.setattr(Path, "stat", blind_stat)
    kept, src, fresh = archive.item_source(idir)
    assert kept is None and src == idir / "source.ts" and fresh is False
    with pytest.raises(ClipperError, match=r"audio disagree") as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert str(err.value).endswith("(kept as source.ts)")
    assert runs == [] and seen == ["source.ts"]  # no download, and the copy was not unlinked
    assert "removed the refusal marker" not in capsys.readouterr().out
    assert (idir / "source.ts").read_bytes() == b"downloaded"
    assert "timeline and its audio disagree" in (idir / ".refused").read_text(encoding="utf-8")


def test_a_marker_beside_an_empty_download_is_dropped_and_the_item_downloads(
        kick_cfg, tmp_path, monkeypatch, capsys):
    """#444 review: a zero-byte file is no download either. A marker beside an empty source.ts
    would otherwise stand for a refusal with nothing to reuse: no download, no source to read,
    and the stale audio.wav beside it transcribed. The empty file counts as absent, so the marker
    goes and the item fetches a real copy."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / ".refused").write_text("timeline and its audio disagree\n", encoding="utf-8")
    (idir / "source.ts").write_bytes(b"")
    (idir / "audio.wav").write_bytes(b"refused audio")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert "kick-1: removed the refusal marker, no source left to refuse" in capsys.readouterr().out
    assert len(runs) == 1 and seen == ["source.ts"]  # the new copy, not the empty file
    assert not (idir / ".refused").exists()
    assert (idir / "words.jsonl").exists() and (idir / "transcript.json").exists()


def test_an_empty_refused_source_is_not_reused_and_the_item_downloads(
        kick_cfg, tmp_path, monkeypatch, capsys):
    """#444 review: a zero-byte source.refused.ts holds nothing (a failure before the first byte,
    or a share that made the file first), so it is no copy to reuse: left there it sorts before
    source.ts in extract_audio's glob and is read in place of the download. Removed, the item
    downloads and reads the new copy."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / "source.refused.ts").write_bytes(b"")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert "kick-1: removed an empty source.refused.ts" in capsys.readouterr().out
    assert len(runs) == 1 and seen == ["source.ts"]
    assert not (idir / "source.refused.ts").exists()
    assert (idir / "words.jsonl").exists() and (idir / "transcript.json").exists()


def test_a_refused_rename_that_cannot_be_marked_says_a_later_run_may_replace_it(
        kick_cfg, tmp_path, monkeypatch):
    """#444 review: the marker is written only once the rename has failed. Where both fail, nothing
    durable can be written in the folder, so the line says the download is still source.ts, that it
    could not be set aside or marked, and that a later run may replace it: the honest outcome, and
    not the marker's own error (which would hide the reason the item was refused)."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)

    def denied(self, *a, **kw):
        raise PermissionError(13, "Access is denied")

    def no_write(*a, **kw):  # the marker's own write (`open`, after the rename has failed)
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "replace", denied)
    monkeypatch.setattr(archive, "open", no_write, raising=False)
    with pytest.raises(ClipperError) as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    msg = str(err.value)
    assert "timeline and its audio disagree" in msg and "still source.ts" in msg
    assert "couldn't be set aside or marked" in msg and "a later run may replace it" in msg
    assert "Access is denied" in msg
    assert str(idir) not in msg and "/" not in msg and "\\" not in msg
    assert (idir / "source.ts").read_bytes() == b"downloaded"
    assert not (idir / ".refused").exists() and not (idir / "source.refused.ts").exists()


def test_a_link_at_the_marker_is_removed_before_a_refusal_could_write_through_it(
        kick_cfg, tmp_path, monkeypatch, capsys, link_or_skip):
    """#444 review: the marker is a dotfile in the item's folder, written when a share refuses the
    rename, and a link planted there would be written through (outside the folder): it is removed
    before the download, and the item is set aside as usual."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_bytes(b"someone else's")
    link_or_skip(elsewhere, idir / ".refused")
    assert (idir / ".refused").is_symlink()
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    with pytest.raises(ClipperError, match=r"audio disagree.*\(kept as source\.refused\.ts\)$"):
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert "kick-1: removed a link at .refused" in capsys.readouterr().out
    assert not (idir / ".refused").is_symlink() and not (idir / ".refused").exists()
    assert (idir / "source.refused.ts").read_bytes() == b"downloaded"
    assert elsewhere.read_bytes() == b"someone else's"


def test_a_link_planted_at_the_marker_during_the_download_is_never_written_through(
        kick_cfg, tmp_path, monkeypatch, capsys, link_or_skip):
    """#444 review: the marker is dropped before the download, but the download is the long part.
    A link planted at `.refused` while it runs is dropped again immediately before the write, so
    the refusal lands in the folder as a regular file and the bytes the link names are untouched."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_bytes(b"someone else's")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)

    class Done:
        returncode, stderr = 0, ""

    def ytdl_run(*a):
        runs.append(a)
        link_or_skip(elsewhere, idir / ".refused")  # planted while the download runs
        assert (idir / ".refused").is_symlink()
        (idir / "source.ts").write_bytes(b"downloaded")
        return Done()

    monkeypatch.setattr(archive.ytdl, "run", ytdl_run)

    def denied(self, target):  # the share refuses the rename: the marker is the record
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(ClipperError, match=r"still source\.ts"):
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert "kick-1: removed a link at .refused" in capsys.readouterr().out
    assert not (idir / ".refused").is_symlink() and (idir / ".refused").is_file()
    assert "timeline and its audio disagree" in (idir / ".refused").read_text(encoding="utf-8")
    assert elsewhere.read_bytes() == b"someone else's"
    assert (idir / "source.ts").read_bytes() == b"downloaded"
    assert not (idir / "source.refused.ts").exists()
    assert len(runs) == 1 and seen == ["source.ts"]


def test_a_successful_run_clears_a_stale_marker(kick_cfg, tmp_path, monkeypatch):
    """A marker left by a refused rename is taken away by the run that gets the item through:
    left there, the next run would treat the folder as refused and find no source at all."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    (idir / "source.ts").write_bytes(b"downloaded")
    (idir / ".refused").write_text("timeline and its audio disagree\n", encoding="utf-8")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert runs == [] and seen == ["source.ts"]
    assert not (idir / ".refused").exists()
    assert (idir / "source.ts").read_bytes() == b"downloaded"  # not this run's to drop
    assert (idir / "words.jsonl").exists() and (idir / "transcript.json").exists()


def test_an_hei_item_keeps_a_refused_download_and_never_fetches_it_again(kick_cfg, tmp_path, monkeypatch):
    """An HEI source is `--abort-on-unavailable-fragments`, so one that completed is the VOD's
    own content: a refused one is kept as source.refused.ts and the next run reads it rather
    than asking HEI for the same deterministic jump again."""
    idir = tmp_path / "archive" / "s" / "hei-1"
    idir.mkdir(parents=True)
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen)
    monkeypatch.setattr(archive.hei, "fresh_m3u8", lambda *a: "https://example.invalid/master.m3u8")
    it = {**_item(), "id": "hei-1", "source": "hei"}
    with pytest.raises(ClipperError, match=r"\(kept as source\.refused\.ts\)$"):
        archive.transcribe_item(kick_cfg, idir, it, "L4")
    assert len(runs) == 1 and seen == ["source.ts"]
    assert (idir / "source.refused.ts").read_bytes() == b"downloaded"
    assert not (idir / "source.ts").exists()
    runs.clear()
    seen.clear()
    monkeypatch.setattr(archive.hei, "fresh_m3u8", lambda *a: pytest.fail("refreshed the m3u8"))
    monkeypatch.setattr(archive.ytdl, "run", lambda *a: pytest.fail("downloaded it again"))
    with pytest.raises(ClipperError, match=r"timeline and its audio disagree") as err:
        archive.transcribe_item(kick_cfg, idir, it, "L4")
    assert runs == [] and seen == ["source.refused.ts"]
    assert str(err.value).endswith("(kept as source.refused.ts)")  # reused, and it says where
    assert (idir / "source.refused.ts").read_bytes() == b"downloaded"



def _flac_target(tmp_path, maker):
    """What a link at audio.flac names: a file for a symlink, a folder holding one for a junction
    (a junction can only name a folder). Returns (the link's target, the file to keep)."""
    if maker == "link_or_skip":
        keep = tmp_path / "someone_elses.flac"
        keep.write_bytes(b"someone else's")
        return keep, keep
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / "keep.txt").write_bytes(b"someone else's")
    return folder, folder / "keep.txt"


@pytest.mark.parametrize("maker", ["link_or_skip", "junction_or_skip"])
def test_the_flac_encode_drops_a_link_at_audio_flac_first(kick_cfg, tmp_path, monkeypatch, request, maker):
    """#447: ffmpeg's `-y` follows a link at audio.flac and truncates what it names (the unlink
    after the upload takes only the link), so the link goes before the encode, its target intact.
    The encode reads only local files: `-protocol_whitelist file` comes before its input."""
    make = request.getfixturevalue(maker)
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    target, keep = _flac_target(tmp_path, maker)
    make(target, idir / "audio.flac", directory=maker != "link_or_skip")
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    encodes = []

    def run(args, **kw):
        flac = Path(args[-1])
        assert not archive.is_link(flac) and not flac.exists()  # the link went, nothing in its place
        encodes.append(list(args))
        flac.write_bytes(b"flac")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(archive.subprocess, "run", run)
    archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert len(encodes) == 1
    args = encodes[0]
    assert args[args.index("-i") - 2:args.index("-i")] == ["-protocol_whitelist", "file"]
    assert keep.read_bytes() == b"someone else's"
    assert not (idir / "audio.flac").exists()  # the upload's copy is gone after it, as before


def test_a_link_at_audio_flac_the_share_will_not_drop_stops_before_the_encode(
        kick_cfg, tmp_path, monkeypatch, junction_or_skip):
    """#447: a link that can't be removed is one line naming the stream, and ffmpeg never writes
    through it."""
    idir = tmp_path / "archive" / "s" / "kick-1"
    idir.mkdir(parents=True)
    target, keep = _flac_target(tmp_path, "junction_or_skip")
    junction_or_skip(target, idir / "audio.flac", directory=True)
    runs, seen = [], []
    _downloads(monkeypatch, idir, runs, seen, wav_s=33011.2)
    _transcribes(monkeypatch)
    monkeypatch.setattr(archive.subprocess, "run", lambda *a, **k: pytest.fail("ffmpeg ran"))

    def denied(path):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(archive, "remove_link", denied)
    with pytest.raises(ClipperError) as err:
        archive.transcribe_item(kick_cfg, idir, _item(), "L4")
    assert str(err.value) == "kick-1: couldn't remove the link at audio.flac (Access is denied)"
    assert keep.read_bytes() == b"someone else's"
