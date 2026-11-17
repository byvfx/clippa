"""Twitch ingest: resolve a VOD, download it, and pull its chat replay.

`clipper fetch https://www.twitch.tv/videos/<id>` leaves streams/twitch-<id>/ with the same
files a Kick VOD does (meta.json, source.mp4, chat.jsonl). yt-dlp reads the VOD's details and
downloads the video; chat comes from Twitch's own web API (the comments of a video, paged by
cursor), since yt-dlp has no Twitch chat. A subscriber-only VOD needs [twitch] cookies.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from . import jsonstate, ytdl
from .errors import ClipperError

GQL = "https://gql.twitch.tv/gql"
# Twitch's own web player sends this id and this persisted query for chat replay.
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
COMMENTS_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"
PAGE_RETRIES = 6


def parse_url(url: str) -> str:
    m = re.search(r"twitch\.tv/(?:[\w-]+/)?videos?/(\d+)", url)
    if not m:
        raise ClipperError(f"not a Twitch VOD url: {url} (https://www.twitch.tv/videos/<id>)")
    return m.group(1)


def _cookie_args(cfg: dict) -> list[str]:
    path = cfg["twitch"]["cookies"]
    if not path:
        return []
    p = Path(path)
    if not p.is_absolute():
        p = cfg["paths"]["root"] / p
    if not p.exists():
        raise ClipperError(f"[twitch] cookies {p} doesn't exist")
    return ["--cookies", str(p)]


def resolve(url: str, cfg: dict) -> dict:
    vid = parse_url(url)
    r = ytdl.run(*_cookie_args(cfg), "-J", "--no-playlist", f"https://www.twitch.tv/videos/{vid}")
    if r.returncode:
        err = ytdl.last_error(r)
        hint = " (a subscriber-only VOD needs [twitch] cookies)" if re.search(r"subscri|sign in|login", err, re.I) else ""
        raise ClipperError(f"yt-dlp couldn't read Twitch VOD {vid}: {err}{hint}")
    v = json.loads(r.stdout)
    started = datetime.fromtimestamp(v.get("timestamp") or time.time(), UTC)
    channel = (v.get("uploader_id") or v.get("uploader") or "").lower()
    return {
        "stream_id": f"twitch-{vid}",
        "platform": "twitch",
        "channel": channel,
        "video_id": vid,
        "url": f"https://www.twitch.tv/videos/{vid}",
        "title": v.get("title") or "",
        "started_at": started.isoformat(),
        "duration_s": float(v.get("duration") or 0),
        "source": v.get("webpage_url") or f"https://www.twitch.tv/videos/{vid}",
    }


def fetch_video(stream_dir: Path, meta: dict, cfg: dict) -> Path:
    out = stream_dir / "source.mp4"
    if out.exists():
        return out
    tc = cfg["twitch"]
    r = ytdl.run(*_cookie_args(cfg), "--no-playlist", "-f", f"best[height<={tc['max_height']}]/best",
                 "-N", str(tc["download_fragments"]), "--merge-output-format", "mp4",
                 "--remux-video", "mp4", "-o", str(stream_dir / "source.%(ext)s"), meta["url"])
    if r.returncode or not out.exists():
        raise ClipperError(f"yt-dlp download of {meta['url']}: {ytdl.last_error(r)}")
    return out


def _post(body: list[dict], headers: dict) -> list[dict]:
    req = urllib.request.Request(GQL, json.dumps(body).encode(), headers=headers)
    for attempt in range(PAGE_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise ClipperError(f"Twitch chat: HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            pass
        time.sleep(min(2 ** attempt, 30))
    raise ClipperError("Twitch chat: giving up after repeated errors")


def comment_row(node: dict, t0: float) -> dict:
    offset = float(node.get("contentOffsetSeconds") or 0)
    text = "".join(f.get("text") or "" for f in (node.get("message") or {}).get("fragments") or [])
    who = node.get("commenter") or {}
    return {"id": node["id"], "ts": t0 + offset, "user": who.get("login") or who.get("displayName"),
            "type": "message", "content": text, "t": round(offset, 3)}


def fetch_chat(stream_dir: Path, meta: dict, cfg: dict) -> Path:
    out = stream_dir / "chat.jsonl"
    if out.exists():
        return out
    t0 = datetime.fromisoformat(meta["started_at"]).timestamp()
    headers = {"Client-ID": CLIENT_ID, "Content-Type": "application/json"}
    rows: dict[str, dict] = {}
    # Cursor pages need Twitch's integrity token, so page by offset: each request starts at the
    # last second seen (rows are de-duplicated by id), a second further when nothing new came.
    offset = 0
    end = float(meta.get("duration_s") or 0)
    while offset <= end:
        data = _post([{"operationName": "VideoCommentsByOffsetOrCursor",
                       "variables": {"videoID": meta["video_id"], "contentOffsetSeconds": offset},
                       "extensions": {"persistedQuery": {"version": 1, "sha256Hash": COMMENTS_HASH}}}],
                     headers)[0]
        comments = ((data.get("data") or {}).get("video") or {}).get("comments")
        if comments is None:
            raise ClipperError(f"Twitch chat stopped at {offset}s of VOD {meta['video_id']}: "
                               f"{data.get('errors') or data}")
        before = len(rows)
        last = offset
        for edge in comments.get("edges") or []:
            if edge.get("node"):
                row = comment_row(edge["node"], t0)
                rows[row["id"]] = row
                last = max(last, int(row["t"]))
        offset = last if len(rows) > before else offset + 60  # nothing new: a quiet stretch, jump on
        if len(rows) // 5000 != before // 5000:
            print(f"chat: {len(rows)} msgs ({offset / end:.0%})" if end else f"chat: {len(rows)} msgs", flush=True)
    ordered = sorted(rows.values(), key=lambda m: (m["ts"], m["id"]))
    tmp = out.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in ordered), encoding="utf-8")
    tmp.replace(out)
    print(f"{len(ordered)} chat messages -> {out}")
    return out


def fetch(cfg: dict, url: str, no_video: bool = False, no_chat: bool = False) -> Path:
    meta = resolve(url, cfg)
    d = cfg["paths"]["streams"] / meta["stream_id"]
    d.mkdir(parents=True, exist_ok=True)
    jsonstate.write(d / "meta.json", meta)
    print(f"{meta['stream_id']}: {meta['title']} ({meta['duration_s'] / 3600:.2f} h)")
    if not no_chat:
        fetch_chat(d, meta, cfg)
    if not no_video:
        fetch_video(d, meta, cfg)
    return d
