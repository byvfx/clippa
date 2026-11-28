"""A video already on disk as a stream (#434): `clippa fetch <path>`.

`clippa fetch D:/vods/show.mkv` leaves streams/local-<name>-<hash>/ with source.<ext> (a copy by
default; --link hardlinks it, --move moves it) and meta.json, and no chat. Then `clippa run
<id>` as for any fetched VOD. The show is `clippa --show <key> fetch ...` (written to meta.json as
that show's first channel, so every later command finds it); without it the base config applies.

The id is the file name's slug plus a short hash of the file's size and its first and last MiB:
quick on a share where reading a whole 10 GB file is not. A second import of the same file, under
any name, finds the stream it made before (meta.json's fingerprint) and does nothing, or puts the
video back when that stream's is gone (deleted, pruned). meta.json is written last, so a stream
folder without one is an import that didn't finish: fetching the file again finishes it, and after
a --move that stopped there, so does fetching the folder's own source.<ext>.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, jsonstate, media
from .errors import ClipperError

PREFIX = "local-"
# What ffmpeg reads and the source.* glob keeps (media.source_file skips .part and .ytdl)
VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".m4v", ".ts", ".webm", ".flv", ".avi", ".mpg", ".mpeg", ".wmv")
_EDGE = 1 << 20  # the fingerprint reads this much from each end


def is_path(arg: str) -> bool:
    """A fetch argument that names something on this machine rather than a url: a path that
    exists. A url (anything with "://", a `file:` url included) is never one."""
    if "://" in arg or arg.lower().startswith("file:"):
        return False
    try:
        return Path(arg).exists()
    except (OSError, ValueError):  # a name the system can't take (a url-ish string on Windows)
        return False


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40].strip("-") or "video"


def fingerprint(path: Path) -> str:
    """sha1 of the file's size and its first and last MiB (all of it up to 2 MiB), as hex."""
    n = path.stat().st_size
    h = hashlib.sha1(str(n).encode())
    with open(path, "rb") as f:
        if n <= 2 * _EDGE:
            h.update(f.read())
        else:
            h.update(f.read(_EDGE))
            f.seek(n - _EDGE)
            h.update(f.read(_EDGE))
    return h.hexdigest()


def started_at(path: Path, given: str = "", tz: str = "") -> str:
    """ISO start time: `given` (a date or a datetime, in `tz`, the show's [schedule] timezone,
    unless it says otherwise), the container's creation_time, or the file's mtime."""
    if given:
        try:
            t = datetime.fromisoformat(given)
        except ValueError:
            raise ClipperError(f"--started {given!r}: an ISO date or time, e.g. 2026-11-24 or "
                               f"2026-11-24T20:00") from None
        if not t.tzinfo:  # the show's own clock, not this machine's (a NAS container runs in UTC)
            t = t.replace(tzinfo=ZoneInfo(tz)) if tz else t.astimezone()
        return t.isoformat()
    tag = media.creation_time(path)
    if tag:
        try:
            t = datetime.fromisoformat(tag.replace("Z", "+00:00"))
            if t.year > 1970:  # a muxer that writes 0 for "unknown"
                return (t if t.tzinfo else t.replace(tzinfo=UTC)).isoformat()
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()


def find(streams: Path, fp: str) -> Path | None:
    """The stream an earlier import of this file made, by its fingerprint."""
    for m in sorted(streams.glob(f"{PREFIX}*/meta.json")):
        try:
            if jsonstate.read(m, {}).get("fingerprint") == fp:
                return m.parent
        except (OSError, ValueError):  # one unreadable meta.json doesn't hide the others
            continue
    return None


def _place(src: Path, dest: Path, how: str, label: str) -> None:
    """src at dest: copied or hardlinked through dest.part (so a stopped copy is never taken for
    the source), or moved straight onto dest. A move on one drive is a single rename, so there is
    never half a file; across drives shutil.move removes the original only once the copy is whole,
    so a stopped one leaves the original and a short dest that the next fetch redoes."""
    part = dest.with_name(dest.name + ".part")
    media.drop_output_link(part, label)  # a link there would send the copy where it points
    media.drop_output_link(dest, label)
    try:
        if how == "move":
            shutil.move(src, dest)
            return
        if how == "link":
            part.unlink(missing_ok=True)
            os.link(src, part)
        else:
            print(f"{label}: copying {src.stat().st_size / 1e9:.1f} GB", flush=True)
            shutil.copyfile(src, part)
        os.replace(part, dest)
    except OSError as e:
        if how != "move":
            part.unlink(missing_ok=True)
        hint = ("; a hardlink needs the file on the same drive as the streams folder, drop --link to copy"
                if how == "link" else "")
        raise ClipperError(f"{label}: couldn't {how} the video into the stream folder "
                           f"({e.strerror or e}){hint}") from None


def _channel(cfg: dict) -> str:
    """The channel meta.json gets: the pinned show's first (`clippa --show <key>`), so for_stream
    finds that show from it later, or "local" for the base config. A show with no channel of its
    own is refused: for_show would hand back the base [kick] channel, which may be another show's."""
    if not cfg.get("_pinned"):
        return "local"
    key = cfg["show"]["key"]
    channels = config.base(cfg)["shows"][key]["channels"]
    if not channels:
        raise ClipperError(f"[shows.{key}] has no channels; add one so its streams can be found as that "
                           f"show's (it needn't be a real Kick channel)")
    return channels[0]


def fetch(cfg: dict, path: str, how: str = "copy", title: str = "", started: str = "") -> Path:
    """Import one video file; returns its stream folder. how: copy, link or move."""
    src = Path(path)
    if src.is_dir():
        raise ClipperError(f"{src.name}: a folder; give one video file for now (folders: #434)")
    if how == "move" and media.link_or_raise(src, src.name, "the file"):
        # a rename moves the link itself, whose (relative) target then resolves from the stream folder
        raise ClipperError(f"{src.name}: a link; --move would move the link, not the video. Drop "
                           f"--move to copy it, or move the file it points to")
    ext = src.suffix.lower()
    if ext not in VIDEO_EXTS:
        raise ClipperError(f"{src.name}: not a video file clippa takes ({', '.join(VIDEO_EXTS)})")
    streams = Path(cfg["paths"]["streams"])
    resume = _unfinished(streams, src)  # this is a stopped import's own source: finish that one
    channel = _channel(cfg)
    media.size(src)  # a video stream, or ClipperError: Shorts, faces and covers need one
    duration = media.duration(src)
    fp = fingerprint(src)
    old = None if resume else find(streams, fp)
    if old and _has_source(old):
        print(f"{old.name}: already imported; `clippa run {old.name}` runs it")
        return old
    d = resume or old or _new_folder(streams, src, fp)
    sid = d.name
    media.refuse_link_stream(cfg, d)  # before mkdir takes a link for a folder and writes go through it
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"source{ext}"
    start = started_at(src, started, cfg["schedule"]["timezone"])  # before the copy: a copy's mtime is now
    if not resume:
        # A copy that finished before meta.json was written is kept if it is this file's bytes
        if not (dest.exists() and not media.is_link(dest) and fingerprint(dest) == fp):
            _place(src, dest, how, sid)
        elif how == "move":  # (src is outside the streams folder: never the stream's own source)
            src.unlink()
    if old:  # its video was gone (deleted, or pruned): back now, and its meta.json stays as it was
        print(f"{sid}: video restored; `clippa run {sid}` runs it")
        return d
    meta = {
        "stream_id": sid,
        "platform": "local",
        "channel": channel,
        "title": title or (sid if resume else src.stem),  # a resumed import's own name is gone
        "started_at": start,
        "duration_s": round(duration, 3),
        "source": "" if resume else src.name,  # the name only: meta.json reaches messages and the war room
        "fingerprint": fp,
    }
    jsonstate.write(d / "meta.json", meta)
    print(f"{sid}: {meta['title']} ({duration / 3600:.2f} h); `clippa run {sid}` runs it")
    return d


def _has_source(d: Path) -> bool:
    try:
        media.source_file(d)
        return True
    except ClipperError:
        return False


def _unfinished(streams: Path, src: Path) -> Path | None:
    """The stream folder when `src` is a stopped import's own `source.*` (a local stream with no
    meta.json yet: a --move killed between the rename and meta.json), so fetching it finishes that
    import. Anything else inside the streams folder is refused: importing it would make a second
    stream of a video that is already one."""
    try:
        inside = src.resolve().is_relative_to(streams.resolve())
    except (OSError, RuntimeError):  # a share that won't resolve: it can't be compared, so not refused
        return None
    if not inside:
        return None
    d = src.parent
    if (src.stem == "source" and d.name.startswith(PREFIX) and d.parent.resolve() == streams.resolve()
            and not (d / "meta.json").exists()):
        return d
    raise ClipperError(f"{src.name}: already in the streams folder; `clippa run <stream>` runs a stream")


def _new_folder(streams: Path, src: Path, fp: str) -> Path:
    """streams/local-<slug>-<6 hex> for a file no stream has yet, refused when it is another
    file's stream (find() didn't match it, so the same short id is someone else's)."""
    d = streams / f"{PREFIX}{slug(src.stem)}-{fp[:6]}"
    try:
        other = jsonstate.read(d / "meta.json", {}).get("fingerprint")
    except (OSError, ValueError):
        other = "unreadable"
    if other:
        raise ClipperError(f"{d.name}: that stream is another file's (same name, same short hash); "
                           f"rename this file and fetch it again")
    return d
