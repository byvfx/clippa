"""Kick ingest: resolve a VOD, download its HLS source, and pull chat history.

yt-dlp's Kick VOD extractor 404s on new VOD URLs, so we resolve the VOD through
the channel's video list and hand the master.m3u8 to yt-dlp's generic HLS path.

New VOD URLs carry a UUIDv7 whose first 48 bits are the stream start in epoch
ms; older URLs carry the video uuid. Both are matched against the list.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import jsonstate, timeline, transcribe
from .errors import ClipperError
from .media import link_or_raise

API = "https://kick.com/api/v2"
PAGE_RETRIES = 8
CHAT_CHUNK_S = 900  # chat_parts/<epoch start>.jsonl each cover this many seconds


class RateLimiter:
    """Spaces requests across all threads, adapting to the server's limit:
    a 429 halves the rate and pauses everyone (the limit is per client, not
    per thread); each success creeps the rate back up toward `max_rps`."""

    def __init__(self, max_rps: float, min_rps: float = 0.2):
        self.max_rps, self.min_rps = max_rps, min_rps
        self.rps = max_rps
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + 1.0 / self.rps
        time.sleep(max(0.0, at - now))

    def ok(self) -> None:
        with self.lock:
            self.rps = min(self.max_rps, self.rps + 0.02)

    def throttled(self, pause: float) -> None:
        with self.lock:
            self.rps = max(self.min_rps, self.rps / 2)
            self.next_at = max(self.next_at, time.monotonic() + pause)


THROTTLED_MAX_S = 900  # one request throttled this long is a blocked IP


def _get(url: str, ua: str, limiter: RateLimiter | None = None) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    errors = 0
    page = None  # the last web page Kick sent where its JSON should be
    t0 = time.monotonic()
    while True:
        if limiter:
            limiter.wait()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
            try:
                data = json.loads(body)
            except ValueError:  # Cloudflare's challenge page, a maintenance page: often brief, so retried
                page = body[:80]
            else:
                if limiter:
                    limiter.ok()
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Kick sends Retry-After: 0, so pause at least a second.
                pause = max(float(e.headers.get("Retry-After") or 0), 1.0)
                if limiter:
                    limiter.throttled(pause)
                else:
                    time.sleep(pause)
                if time.monotonic() - t0 > THROTTLED_MAX_S:  # #77 L8: a ban, not throttling
                    raise ClipperError(f"Kick kept answering 429 for {THROTTLED_MAX_S // 60} min: {url}") from None
                continue  # throttling is expected; retry
            if e.code not in (500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError):
            pass
        errors += 1
        if errors >= PAGE_RETRIES:
            if page is not None:
                raise ClipperError(f"Kick sent a web page, not data, for {url} (a Cloudflare check?): {page!r}")
            raise ClipperError(f"giving up on {url} after {errors} errors")
        time.sleep(min(2 ** errors, 60))


def _parse_ts(s: str) -> datetime:
    # API uses both "2026-10-08 00:28:50" and ISO-8601 with Z; all UTC.
    dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_url(url: str) -> tuple[str, str]:
    m = re.search(r"kick\.com/([^/]+)/videos/([0-9a-f-]{36})", url)
    if not m:
        raise ClipperError(f"not a Kick VOD url: {url} (https://kick.com/<channel>/videos/<id>)")
    return m.group(1), m.group(2)


# A VOD page exactly as `clippa fetch` writes it down: a plain channel slug (no `\`, `/`, space
# or `?`), no query and no fragment after the id, so a command built from it fetches this video
# and not something else.
VOD_URL = re.compile(r"https://kick\.com/[A-Za-z0-9_.()-]+/videos/[0-9a-f-]{36}")


def canonical_vod_url(url: Any) -> str:
    """`url` if it is a canonical Kick VOD url, else "": a recording's channel link, a search
    page or an url with a `?` after it is not something `clippa fetch` should be pointed at."""
    return url if isinstance(url, str) and VOD_URL.fullmatch(url) else ""


def resolve(url: str, cfg: dict) -> dict:
    channel, vid = parse_url(url)
    ua = cfg["kick"]["user_agent"]
    videos = _get(f"{API}/channels/{channel}/videos", ua)
    v7_start = int(vid.replace("-", "")[:12], 16) / 1000
    for v in videos:
        start = _parse_ts(v["start_time"]).timestamp()
        if v["video"]["uuid"] == vid or abs(start - v7_start) < 2:
            return {
                "stream_id": f"kick-{v['id']}",
                "platform": "kick",
                "channel": channel,
                "channel_id": v["channel_id"],
                "livestream_id": v["id"],
                "video_uuid": v["video"]["uuid"],
                "url": url,
                "title": v["session_title"],
                "started_at": _parse_ts(v["start_time"]).isoformat(),
                "duration_s": v["duration"] / 1000,
                "source": v["source"],
            }
    raise ClipperError(f"{vid} not in {channel}'s recent videos ({len(videos)} listed)")


def list_videos(channel: str, cfg: dict, limiter: RateLimiter | None = None) -> list[dict]:
    """A Kick channel's past broadcasts, newest first, as archive items (#84, #109): id,
    source, url (the VOD page), title, duration_s, channel, date, plus started_at and the
    HLS master m3u8 (`m3u8`). limiter spaces each attempt, retries included."""
    out = []
    for v in _get(f"{API}/channels/{channel}/videos", cfg["kick"]["user_agent"], limiter) or []:
        uuid = (v.get("video") or {}).get("uuid")
        if not uuid or not v.get("start_time"):
            continue
        start = _parse_ts(v["start_time"])
        out.append({"id": f"kick-{v['id']}", "source": "kick", "url": f"https://kick.com/{channel}/videos/{uuid}",
                    "title": v.get("session_title") or "", "duration_s": (v.get("duration") or 0) / 1000,
                    "channel": channel, "date": start.date().isoformat(), "started_at": start.isoformat(),
                    "m3u8": v.get("source") or ""})
    out.sort(key=lambda it: it["started_at"], reverse=True)
    return out


SKIPPED = re.compile(r"Skipping fragment", re.I)


def _refuse_link(stream_dir: Path) -> None:
    """A stream folder that is a link (a symlink, or on Windows a junction) is not a folder of
    ours: a download into it, the remux's replace and the wav's removal would all happen where it
    points. Only the link is refused, not where the folder is (cmd_fetch checks the streams tree;
    archive may repair an item folder anywhere, #446), but no caller can skip this."""
    if link_or_raise(stream_dir, stream_dir.name, "the stream folder"):
        raise ClipperError(f"{stream_dir.name}: the stream folder is a link; refusing to write "
                           f"its source through it")


def _refuse_link_source(stream_dir: Path) -> None:
    """A link at the source.ts this command downloads or repairs is refused before anything reads
    or writes it: ffprobe and ffmpeg follow a link, and a remux's replace would take what it names
    for the stream's own source."""
    if link_or_raise(stream_dir / "source.ts", stream_dir.name):
        raise ClipperError(f"{stream_dir.name}: its source.ts is a link, not a file; refusing to "
                           f"read it")


def _refuse_downloading(stream_dir: Path) -> None:
    """A source with yt-dlp's resume note beside it never finished downloading (`fetch_video`'s
    own rule, `resuming`): remuxing it in place would rewrite the partial, leave the note there, and
    the next fetch would resume into the repaired file. Only the path that can't download refuses
    (`fixup`, `--fixup --no-video` and the api-down fallback), in one line naming the stream: a
    plain fetch finishes the download, and --fixup then repairs what it left."""
    try:  # a share that won't stat the note is one line too (#449): pathlib re-raises EACCES
        downloading = (stream_dir / "source.ts.ytdl").exists()
    except OSError as e:
        raise ClipperError(f"{stream_dir.name}: couldn't tell whether its source is still downloading "
                           f"({e.strerror or 'the system refused it'})") from None
    if downloading:
        raise ClipperError(f"{stream_dir.name}: its source is still downloading (source.ts.ytdl); "
                           f"let the fetch finish, then --fixup")


def fetch_video(stream_dir: Path, meta: dict, cfg: dict, fixup: bool = False) -> Path:
    """The VOD as source.ts. yt-dlp skips a segment Kick no longer has and carries on; that is
    kept (a VOD can lose segments for good, and a gap beats no video), but counted: printed and
    stored as meta.json's skipped_fragments, since every clip over the gap jumps there. A failed
    run leaves its count in source.ts.skipped: the retry resumes from source.ts.ytdl and doesn't
    print those segments again.

    fixup (a stream the transcribe check flagged, #440): remux the video with its timestamps
    regenerated, whether it was just downloaded or was already here, and store the jump it found
    as meta.json's timeline_overlap_s. It rewrites the whole file, so it is only run on request;
    without it a download is taken as it comes. A source still downloading (yt-dlp's resume note
    beside it) is finished first: the download resumes from the note, and the remux then repairs
    what it left, so a partial is never rewritten. A fixup also sweeps what a killed earlier remux
    left beside the source (`timeline.sweep`)."""
    _refuse_link(stream_dir)  # before the sweep unlinks and yt-dlp or the remux writes there
    _refuse_link_source(stream_dir)  # before anything probes or replaces it
    if fixup:
        timeline.sweep(stream_dir)  # a killed remux's temp, before this run makes its own
    out = stream_dir / "source.ts"
    resuming = (stream_dir / "source.ts.ytdl").exists()
    if out.exists() and not resuming:
        if fixup:
            _store_jump(stream_dir, _remux_guarded(stream_dir, out, cfg))
        return out
    tally = stream_dir / "source.ts.skipped"
    if not resuming:  # a fresh start: an old run's tally isn't this download's, nor a retry's of it
        tally.unlink(missing_ok=True)
    skipped = _earlier_skips(tally)
    k = cfg["kick"]
    proc = subprocess.Popen(
        [sys.executable, "-m", "yt_dlp", "--referer", "https://kick.com/",
         "-f", f"best[height<={k['max_height']}]", "-N", str(k["download_fragments"]),
         "--hls-use-mpegts", "--no-part", "--newline", "--progress-delta", "30",
         "-o", str(out), meta["source"]],
        # "Skipping fragment" is on stdout (to_screen), its reason on stderr: both, read here.
        # (--logtostderr is only the Python API's, the command line refuses it.)
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},  # what's read above, whatever the locale
    )
    ok = False
    try:
        for line in proc.stdout:  # passed through as it comes
            skipped += bool(SKIPPED.search(line))
            _echo(line)
        ok = proc.wait() == 0
    finally:
        if proc.poll() is None:  # stopped above (a closed pipe, Ctrl-C): never leave it writing source.ts
            proc.kill()
            proc.wait()
        if not ok and skipped:
            try:
                tally.write_text(str(skipped), encoding="utf-8")
            except OSError as e:  # the share gone too: say so, but keep the download's own error
                print(f"{stream_dir.name}: couldn't keep the skip count ({skipped}): {e}", file=sys.stderr)
    if not ok:
        raise subprocess.CalledProcessError(proc.returncode, proc.args)
    tally.unlink(missing_ok=True)
    # yt-dlp stamps source.ts with the VOD's own Last-Modified (there is no --no-mtime here), which
    # can be older than an audio.wav still beside it: the mtime rule would then serve the previous
    # file's audio for this source. The marker is what says otherwise, and the next transcribe
    # decodes the wav anyway unless the share rounds its times into place, so one that can't be
    # written is a line, never a failed download.
    if out.exists():  # what a finished download left; a wav here is the previous file's audio now
        try:
            transcribe.mark_stale(stream_dir)
        except ClipperError as why:  # its own reason: the marker's write, or a link test refused (#449)
            print(f"{why}; a transcribe may reuse it", file=sys.stderr)
    if skipped:
        print(f"{stream_dir.name}: warning: {skipped} video segments were missing on Kick; "
              f"the source has gaps there")
    # This download's own findings go in before the remux: the tally they came from is gone, so a
    # --fixup that fails must not lose them. A fresh file's timeline is blank until a fixup says
    # otherwise; a resume keeps whatever the earlier part had.
    found = {"skipped_fragments": skipped} if skipped else {}
    keys = ["skipped_fragments"] if resuming else ["skipped_fragments", "timeline_overlap_s"]
    if skipped or (stream_dir / "meta.json").exists():  # no meta yet and nothing to say
        jsonstate.merge(stream_dir / "meta.json", found, keys)
    if fixup and out.exists():
        _store_jump(stream_dir, _remux_guarded(stream_dir, out, cfg))
    return out


def fixup(stream_dir: Path, cfg: dict, *, api_down: bool = False) -> None:
    """`clippa fetch <url> --fixup --no-video`: the source.ts already in the folder remuxed, with
    nothing downloaded. A fixup repairs a file, it can't fetch one, so no source.ts here is a
    ClipperError rather than a silent no-op, and neither is one that never finished downloading
    (yt-dlp's resume note beside it): a plain fetch finishes that, then --fixup repairs it. What a
    killed earlier remux left beside the source is swept first (`timeline.sweep`).

    api_down is the `clippa fetch --fixup` fallback for a url Kick's api couldn't resolve: with no
    source.ts there is nothing to repair and no download this command could make, so it says that
    rather than advice about a download the run didn't ask for and can't do."""
    _refuse_downloading(stream_dir)  # before the sweep or the remux touches a partial download
    _refuse_link(stream_dir)  # before the sweep unlinks and the remux replaces inside it
    _refuse_link_source(stream_dir)  # before anything probes or replaces it
    timeline.sweep(stream_dir)
    out = stream_dir / "source.ts"
    if not out.exists():
        if api_down:
            raise ClipperError(f"{stream_dir.name}: Kick's api couldn't resolve this video and "
                               "there is no source.ts here to repair; try again when the api is back")
        raise ClipperError(f"{stream_dir.name}: --fixup --no-video repairs the source.ts already "
                           f"here, and there is none (drop --no-video to download one)")
    _store_jump(stream_dir, _remux_guarded(stream_dir, out, cfg))


def _remux_guarded(stream_dir: Path, path: Path, cfg: dict) -> float:
    """timeline.remux_verified with the stream's run lock taken only where it must be: for the
    moment the busy check takes (as repairs.py does) and again around the replace. Holding it
    across the remux was no use — a 33 GB file takes longer than jsonstate's 60 s lock wait is
    honoured — and blocked a worker that should be refused, not waited for. A worker that starts
    *during* the remux reads the old source and is stopped by transcribe's timeline guard
    (check_audio_timeline): the guard, not the lock, is what keeps its times honest. One that is
    on it when the replace is due is refused there and the original is kept.

    A source this rewrites has its `audio.wav` dropped in the same lock as the replace, as it is
    already the repaired file when the lock goes: the wav was decoded from the old one
    (`aresample=async=1` reads a jumping source to different samples than the repaired file's),
    and after a repair the two lengths agree within timeline.TOLERANCE_S, so the growth this
    returns can be 0.0 and transcribe's guard alone cannot catch the stale wav. The marker
    (transcribe.mark_stale) is the durable half of that: an mtime cannot tell a fast remux's stale
    wav from a fresh one, and a drop the share refuses leaves the marker saying it is stale.

    #448: a source already in order is not rewritten, but a wav beside it that runs past its
    container by more than timeline.TOLERANCE_S (the guard's own numbers) was decoded from an
    earlier file, and every transcribe would refuse on it: it is dropped the same way, so a fixup
    always leaves the stream transcribable."""
    from .pick import worker_busy  # here only: pick brings the model stack

    def refuse_busy() -> None:
        busy = worker_busy(stream_dir, cfg)
        if busy:
            raise ClipperError(f"{stream_dir.name}: {busy} is running it; --fixup when that is done")

    with _run_lock(stream_dir):
        refuse_busy()

    replaced = False

    def put_in_place(tmp: Path, path: Path) -> None:
        nonlocal replaced
        with _run_lock(stream_dir):  # short: the check and the rename only
            refuse_busy()
            # The marker goes in before the replace: one that can't be written stops it here, and
            # the original (which the wav still matches) is kept. After it, a failed one would
            # leave a stale wav nobody could tell from a fresh one.
            try:
                transcribe.mark_stale(stream_dir)
            except ClipperError as why:
                raise ClipperError(f"{why}; the source is not replaced") from None
            os.replace(tmp, path)
            replaced = True
            # The wav beside it was decoded from the old source, and the growth remux_verified
            # returns can be 0.0 after a real rewrite (the lengths agree within tolerance): it
            # goes with the replace, which is the one moment the source is known to have changed.
            _drop_wav(stream_dir)

    growth = timeline.remux_verified(path, stream_dir.name, put_in_place)
    if not replaced:
        _drop_overrunning_wav(stream_dir, refuse_busy)
    return growth


def _drop_overrunning_wav(stream_dir: Path, refuse_busy: Callable[[], None]) -> None:
    """#448, for a source already in order: a wav that runs past its container by more than
    timeline.TOLERANCE_S is marked stale and dropped under the run lock. Best effort: the fixup
    itself worked, so a busy worker, a lock that isn't free or a marker the share won't write is a
    line, never an error that would skip the caller's _store_jump. That includes the check itself:
    a wav the share won't stat (pathlib re-raises EACCES) or a source ffprobe can't time leaves the
    wav as it is, with a line, and transcribe's guard still judges it."""
    try:
        if not (stream_dir / "audio.wav").exists():
            return
        lengths = transcribe.audio_lengths(stream_dir)
    except (OSError, ClipperError) as e:
        why = (e.strerror or "the system refused it") if isinstance(e, OSError) else str(e)
        print(f"{stream_dir.name}: couldn't compare audio.wav with the source ({why}); it is left "
              f"as it is")
        return
    if not lengths or lengths[0] - lengths[1] <= timeline.TOLERANCE_S:
        return
    over = lengths[0] - lengths[1]
    try:
        with _run_lock(stream_dir):
            refuse_busy()
            transcribe.mark_stale(stream_dir)
            print(f"{stream_dir.name}: audio.wav runs {over:.1f} s past the source's container; "
                  f"dropped, so the next transcribe decodes the source again")
            _drop_wav(stream_dir)
    except (OSError, ClipperError) as e:  # an OSError: a busy check the share wouldn't answer
        why = f"{stream_dir.name}: {e.strerror or 'the system refused it'}" if isinstance(e, OSError) else str(e)
        print(f"{why} (audio.wav runs {over:.1f} s past the container and is left; delete it, or "
              f"--fixup again)")


def _drop_wav(stream_dir: Path) -> None:
    """audio.wav and its stamp gone, under the run lock, after transcribe.mark_stale: a wav the share
    won't remove is a line, as the marker beside it already says it is stale."""
    try:
        (stream_dir / "audio.wav").unlink(missing_ok=True)
    except OSError as e:  # the source may be replaced already: _store_jump still has to run
        print(f"{stream_dir.name}: the old audio.wav couldn't be removed "
              f"({e.strerror or 'the system refused it'}); it is marked stale, so the "
              f"next transcribe decodes it again")
    else:
        transcribe.clear_stale(stream_dir)
        transcribe.forget_stamp(stream_dir)


@contextmanager
def _run_lock(stream_dir: Path) -> Iterator[None]:
    """The stream's run lock, a lock that is another process's for the whole of jsonstate's
    60 s wait (or one the share won't let be made) turned into one line naming the stream: two
    fixups hours apart would otherwise raise a bare TimeoutError out of cmd_fetch."""
    lock = jsonstate.locked(stream_dir / ".run.lock")
    try:
        lock.__enter__()  # only the acquiring is wrapped: a body's OSError is its own
    except OSError as e:  # TimeoutError is one: another clippa holds it
        why = (f"the lock wasn't free after {jsonstate.WAIT_S} s" if isinstance(e, TimeoutError)
               else e.strerror or "the system refused it")
        raise ClipperError(f"{stream_dir.name}: another clippa is working on this stream (or the "
                           f"lock couldn't be taken): {why}") from None
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def _store_jump(stream_dir: Path, jump: float) -> None:
    """meta.json's timeline_overlap_s: the jump a --fixup remux found, or the key cleared when it
    found none, so it always says what the file's timeline is. A difference within
    timeline.TOLERANCE_S is not a jump (ffprobe's rounding) and is stored as none."""
    jump = jump if jump > timeline.TOLERANCE_S else 0.0
    jsonstate.merge(stream_dir / "meta.json", {"timeline_overlap_s": jump} if jump else {},
                    ["timeline_overlap_s"])


def _echo(line: str) -> None:
    """A yt-dlp line on our stdout; a character its encoding lacks (cp1252 when redirected on
    Windows) is replaced rather than stopping the download."""
    try:
        sys.stdout.write(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        sys.stdout.write(line.encode(enc, "replace").decode(enc))


def _earlier_skips(tally: Path) -> int:
    """The segments a failed run of this download skipped (0 if none, or unreadable)."""
    try:
        return int(tally.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _page_chunk(channel_id: int, start: float, end: float, ua: str,
                limiter: RateLimiter | None = None) -> list[dict]:
    """Page backwards from `end` until messages are older than `start`."""
    cursor = int(end * 1e6)
    out: list[dict] = []
    while True:
        data = _get(f"{API}/channels/{channel_id}/messages?cursor={cursor}", ua, limiter)["data"]
        msgs = data.get("messages") or []
        for m in msgs:
            t = _parse_ts(m["created_at"]).timestamp()
            if start <= t < end:
                out.append({
                    "id": m["id"],
                    "ts": t,
                    "user": (m.get("sender") or {}).get("username"),
                    "type": m.get("type"),
                    "content": m.get("content", ""),
                })
        nxt = int(data.get("cursor") or 0)
        if not msgs or nxt >= cursor or nxt / 1e6 < start:
            return out
        cursor = nxt


def fetch_chat(stream_dir: Path, meta: dict, cfg: dict, chunk_s: int = CHAT_CHUNK_S) -> Path:
    out = stream_dir / "chat.jsonl"
    if out.exists():
        return out
    t0 = _parse_ts(meta["started_at"]).timestamp()
    t1 = t0 + meta["duration_s"]
    chunks = [(s, min(s + chunk_s, t1)) for s in range(int(t0), int(t1) + 1, chunk_s)]
    ua = cfg["kick"]["user_agent"]
    limiter = RateLimiter(cfg["kick"]["chat_rps"])
    parts = stream_dir / "chat_parts"
    parts.mkdir(exist_ok=True)
    done = 0
    lock = threading.Lock()

    def run(c: tuple[float, float]) -> list[dict]:
        nonlocal done
        part = parts / f"{c[0]}.jsonl"
        if part.exists():
            msgs = [json.loads(line) for line in part.read_text(encoding="utf-8").splitlines()]
        else:
            msgs = _page_chunk(meta["channel_id"], c[0], c[1], ua, limiter)
            tmp = part.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs),
                           encoding="utf-8")
            tmp.replace(part)
        with lock:
            done += 1
            print(f"chat chunk {done}/{len(chunks)}: {len(msgs)} msgs "
                  f"(rate {limiter.rps:.2f}/s)", flush=True)
        return msgs

    with ThreadPoolExecutor(cfg["kick"]["chat_workers"]) as pool:
        seen: dict[str, dict] = {}
        for msgs in pool.map(run, chunks):
            for m in msgs:
                seen[m["id"]] = m
    rows = sorted(seen.values(), key=lambda m: (m["ts"], m["id"]))
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for m in rows:
            m["t"] = round(m["ts"] - t0, 3)
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    tmp.replace(out)
    print(f"{len(rows)} chat messages -> {out}")
    return out
