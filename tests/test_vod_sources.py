"""Twitch and YouTube VOD ingest: url parsing, chat rows, and cmd_fetch's choice of source.
Offline: no network."""
import json

import pytest

from clipper import twitch, ytvod
from clipper.errors import ClipperError


def test_twitch_parse_url():
    assert twitch.parse_url("https://www.twitch.tv/videos/2928192564?t=1h") == "2928192564"
    assert twitch.parse_url("twitch.tv/vanillamace/video/55") == "55"
    with pytest.raises(ClipperError):
        twitch.parse_url("https://kick.com/x/videos/1")


def test_twitch_comment_row():
    node = {"id": "a", "contentOffsetSeconds": 22, "commenter": {"login": "bob", "displayName": "Bob"},
            "message": {"fragments": [{"text": "hi "}, {"text": "there"}]}}
    row = twitch.comment_row(node, 1000.0)
    assert row == {"id": "a", "ts": 1022.0, "user": "bob", "type": "message", "content": "hi there", "t": 22.0}


def test_youtube_parse_url():
    for u in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ?t=5",
              "https://www.youtube.com/live/dQw4w9WgXcQ", "https://www.youtube.com/watch?x=1&v=dQw4w9WgXcQ"):
        assert ytvod.parse_url(u) == "dQw4w9WgXcQ"
    with pytest.raises(ClipperError):
        ytvod.parse_url("https://www.youtube.com/@name")


def test_youtube_chat_rows():
    def line(i, ms, runs):
        r = {"id": i, "authorName": {"simpleText": "ann"}, "message": {"runs": runs}}
        return json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(ms), "actions": [
            {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": r}}}]}})

    lines = [line("b", 5000, [{"text": "lol "}, {"emoji": {"shortcuts": [":face:"]}}]), "not json",
             line("a", 1500, [{"text": "hey"}]), json.dumps({"replayChatItemAction": {"actions": [{"x": 1}]}})]
    rows = ytvod.chat_rows(lines, 100.0)
    assert [r["id"] for r in rows] == ["a", "b"]
    assert rows[1]["content"] == "lol :face:" and rows[1]["t"] == 5.0 and rows[1]["ts"] == 105.0


def test_fetch_picks_the_source(defaults, monkeypatch):
    from types import SimpleNamespace

    from clipper import cli, kick

    seen = []
    monkeypatch.setattr(twitch, "fetch", lambda *a: seen.append(("twitch", a[1])))
    monkeypatch.setattr(ytvod, "fetch", lambda *a: seen.append(("youtube", a[1])))
    monkeypatch.setattr(kick, "resolve", lambda *a: seen.append("kick") or (_ for _ in ()).throw(ClipperError("stop")))
    for url in ("https://www.twitch.tv/videos/1", "https://youtu.be/dQw4w9WgXcQ"):
        cli.cmd_fetch(defaults, SimpleNamespace(url=url, no_video=True, no_chat=True))
    assert [s[0] for s in seen] == ["twitch", "youtube"]
    with pytest.raises(ClipperError):
        cli.cmd_fetch(defaults, SimpleNamespace(url="https://kick.com/a/videos/b", no_video=True, no_chat=True))


def test_twitch_ytdlp_gets_a_private_netscape_copy(tmp_path, monkeypatch):
    from pathlib import Path

    from clipper import ytdl

    jar = tmp_path / "twitch_cookies.txt"
    jar.write_text(json.dumps([{"domain": ".twitch.tv", "path": "/", "secure": True, "name": "auth-token",
                                "value": "x", "expirationDate": 4102444800}]), encoding="utf-8")
    seen = {}

    def run(*args):
        seen["args"] = args
        seen["text"] = Path(args[1]).read_text(encoding="utf-8")

    monkeypatch.setattr(ytdl, "run", run)
    twitch._ytdlp({"paths": {"twitch_cookies": jar}}, "-J", "u")
    assert seen["args"][0] == "--cookies" and seen["args"][1] != str(jar) and seen["args"][2:] == ("-J", "u")
    assert "auth-token" in seen["text"] and not seen["text"].lstrip().startswith("[")
    twitch._ytdlp({"paths": {"twitch_cookies": tmp_path / "none.txt"}}, "-J", "u")
    assert seen["args"] == ("-J", "u")
