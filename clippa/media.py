"""Facts about media files, from ffprobe, a stream's source video, and the links no tool here may
write through."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import wiki
from .errors import ClipperError


def is_link(p: Path) -> bool:
    """A symlink, or on Windows a junction: `is_symlink()` is false for the latter and making one
    needs no privilege, so both are a link we did not write. A hard link is a regular file and is
    not detectable this way (it is only another name for the same bytes, so a guard can't refuse
    it); the timeline and duration checks are what catch a wrong file that got in as one."""
    return p.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(p))


def remove_link(p: Path) -> None:
    """Remove a link without touching what it names. A junction, and on Windows a symlink to a
    folder, are directories to `unlink`, which refuses them there: `rmdir` drops the link alone and
    the target keeps its contents. Any other link (a file, or a folder symlink on a platform whose
    `rmdir` won't take one) goes with `unlink`. An OSError goes to the caller, whose one line
    names the stream and the system's reason."""
    if hasattr(os.path, "isjunction") and os.path.isjunction(p):
        p.rmdir()
    elif os.name == "nt" and p.is_symlink() and p.is_dir():
        p.rmdir()
    else:
        p.unlink()


def _probe(path: Path, entries: str, stream: str | None = None) -> str:
    # -protocol_whitelist file: every path here is a local file, so one that is really an HLS
    # playlist can't send ffprobe to a url. `-f` isn't needed: ffprobe still reads the file.
    args = ["ffprobe", "-v", "error", "-protocol_whitelist", "file"] + (
        ["-select_streams", stream] if stream else [])
    try:
        out = subprocess.run(args + ["-show_entries", entries, "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise ClipperError("ffprobe not found: install ffmpeg and put it on PATH ("
                           + wiki.page("Install") + ")") from None
    return out.stdout.strip() if out.returncode == 0 else ""


def try_duration(path: Path, video: bool = False) -> float | None:
    """Length in seconds, or None if ffprobe can't tell. video: the first video stream's
    length rather than the container's (a download with a longer audio track). Only the first CSV
    field is read: an ffprobe that appends a stream's side data as another field does not turn
    the length into no length."""
    out = _probe(path, "stream=duration" if video else "format=duration", "v:0" if video else None)
    try:
        return float(out.splitlines()[0].split(",")[0])
    except (IndexError, ValueError):
        return None


def duration(path: Path) -> float:
    """Length in seconds; ClipperError if ffprobe can't tell."""
    d = try_duration(path)
    if d is None:
        raise ClipperError(f"ffprobe found no duration in {path}")
    return d


def size(path: Path) -> tuple[int, int]:
    """Width and height of the first video stream."""
    out = _probe(path, "stream=width,height", "v:0")
    try:
        w, h = out.split(",")[:2]
        return int(w), int(h)
    except ValueError:
        raise ClipperError(f"ffprobe found no video size in {path}") from None


def source_file(stream_dir: Path) -> Path:
    """The stream's source.* video, skipping downloads still in progress."""
    for p in sorted(stream_dir.glob("source.*")):
        if p.suffix not in (".part", ".ytdl") and "Frag" not in p.name:
            return p
    from .streams import pruned_note

    raise ClipperError(f"no source video in {stream_dir}{pruned_note(stream_dir)}")
