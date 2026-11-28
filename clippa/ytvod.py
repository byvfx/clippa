"""YouTube ingest: a video (usually a past live stream) as a stream, with its chat replay.

`clippa fetch https://www.youtube.com/watch?v=<id>` leaves streams/youtube-<id>/ with
meta.json, source.mp4 and, when the video has a live-chat replay, chat.jsonl in the rows Kick
and Twitch chat use. A video without a replay (a normal upload) just has no chat. Age and bot
checks retry once signed in, as pre-shows do (ytdl.run_retrying).
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from . import jsonstate, media, ytdl
from .errors import ClipperError

URL = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|live/|shorts/)|youtu\.be/)([\w-]{11})")


def parse_url(url: str) -> str:
    m = URL.search(url)
    if not m:
        raise ClipperError(f"not a YouTube video url: {url} (https://www.youtube.com/watch?v=<id>)")
    return m.group(1)


def resolve(url: str, cfg: dict) -> dict:
    vid = parse_url(url)
    r, _ = ytdl.run_retrying(cfg, "-J", "--no-playlist", f"https://www.youtube.com/watch?v={vid}",
                             retry_on=ytdl.AGE + ytdl.BOT)
    if r.returncode:
        raise ClipperError(f"yt-dlp couldn't read YouTube video {vid}: {ytdl.last_error(r)}")
    v = json.loads(r.stdout)
    ts = v.get("release_timestamp") or v.get("timestamp") or datetime.now(UTC).timestamp()
    handle = (v.get("uploader_id") or v.get("channel") or "").lstrip("@").lower()
    return {
        "stream_id": f"youtube-{vid}",
        "platform": "youtube",
        "channel": handle,
        "video_id": vid,
        "url": v.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
        "title": v.get("title") or "",
        "youtube_channel": v.get("channel_id") or "",
        "started_at": datetime.fromtimestamp(ts, UTC).isoformat(),
        "duration_s": float(v.get("duration") or 0),
        "source": v.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
    }


def fetch_video(stream_dir: Path, meta: dict, cfg: dict) -> Path:
    out = stream_dir / "source.mp4"
    if out.exists():
        return out
    h = cfg["youtube"]["fetch_max_height"]
    r, _ = ytdl.run_retrying(
        cfg, "--no-playlist", "-f", f"bv*[height<={h}]+ba/b[height<={h}]/b",
        "--merge-output-format", "mp4", "--remux-video", "mp4", "-o", str(stream_dir / "source.%(ext)s"),
        meta["url"], retry_on=ytdl.AGE + ytdl.BOT)
    if r.returncode or not out.exists():
        raise ClipperError(f"yt-dlp download of {meta['url']}: {ytdl.last_error(r)}")
    return out


def chat_rows(lines: list[str], t0: float) -> list[dict]:
    """Chat rows out of a live_chat.json (one JSON replay action per line)."""
    rows: dict[str, dict] = {}
    for line in lines:
        try:
            replay = json.loads(line)["replayChatItemAction"]
        except (ValueError, KeyError):
            continue
        offset = int(replay.get("videoOffsetTimeMsec") or 0) / 1000
        for act in replay.get("actions") or []:
            item = (act.get("addChatItemAction") or {}).get("item") or {}
            r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
            if not r:
                continue
            runs = (r.get("message") or {}).get("runs") or []
            text = "".join(x.get("text") or (x.get("emoji") or {}).get("shortcuts", [""])[0] for x in runs)
            rows[r["id"]] = {"id": r["id"], "ts": t0 + offset, "user": (r.get("authorName") or {}).get("simpleText"),
                             "type": "message", "content": text, "t": round(offset, 3)}
    return sorted(rows.values(), key=lambda m: (m["ts"], m["id"]))


def fetch_chat(stream_dir: Path, meta: dict, cfg: dict) -> Path | None:
    out = stream_dir / "chat.jsonl"
    if out.exists():
        return out
    with tempfile.TemporaryDirectory(prefix="clipper-ytchat-") as tmp:
        r, _ = ytdl.run_retrying(cfg, "--skip-download", "--no-playlist", "--write-subs", "--sub-langs", "live_chat",
                                 "-o", str(Path(tmp) / "c.%(ext)s"), meta["url"], retry_on=ytdl.AGE + ytdl.BOT)
        found = list(Path(tmp).glob("*.live_chat.json"))
        if r.returncode and not found:
            raise ClipperError(f"yt-dlp couldn't get the chat of {meta['url']}: {ytdl.last_error(r)}")
        if not found:
            print(f"{meta['stream_id']}: no chat replay on this video")
            return None
        t0 = datetime.fromisoformat(meta["started_at"]).timestamp()
        rows = chat_rows(found[0].read_text(encoding="utf-8").splitlines(), t0)
    tmp_out = out.with_suffix(".tmp")
    tmp_out.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in rows), encoding="utf-8")
    tmp_out.replace(out)
    print(f"{len(rows)} chat messages -> {out}")
    return out


def fetch(cfg: dict, url: str, no_video: bool = False, no_chat: bool = False) -> Path:
    meta = resolve(url, cfg)
    d = cfg["paths"]["streams"] / meta["stream_id"]
    media.refuse_link_stream(cfg, d)  # #444: before mkdir takes a link for a folder and writes go through it
    d.mkdir(parents=True, exist_ok=True)
    jsonstate.write(d / "meta.json", meta)
    print(f"{meta['stream_id']}: {meta['title']} ({meta['duration_s'] / 3600:.2f} h)")
    if not no_chat:
        fetch_chat(d, meta, cfg)
    if not no_video:
        fetch_video(d, meta, cfg)
    return d
