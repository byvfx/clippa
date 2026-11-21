"""`clipper archive add` of a YouTube channel: listed with approximate dates. Offline: yt-dlp
is a stand-in."""
import json

from clippa import archive


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
    """A `clipper story` item with a `dir` is transcribed by the pipeline there (item_dir)."""
    from clippa import config

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
