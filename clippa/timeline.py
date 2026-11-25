"""A source video's timeline, and the remux that puts a jumping one back in order (#440).

A download whose segments restart their timestamps (Kick does it) holds a second stretch that
begins at the file's own start, so ffprobe reads a container no longer than either stretch and
the jump shows in no probe: kick-132975332's source.ts is 33011 s where its audio track is
33833 s, and that 821.7 s is the jump. The disagreement is visible at transcribe, where audio.wav
(decoded from all of the container's audio) runs past the container's own duration, so the check
lives there; the repair is an explicit `clippa fetch --fixup`, which runs the remux here. A remux
with the timestamps regenerated (`-fflags +genpts`) and the codecs copied writes the file afresh
with one timeline, no re-encode. (`-fflags +genpts` alone is not what fixes it: ffmpeg's muxer
relinearises the non-monotonic DTS as it writes. So the result is not just probed for its length:
it must weigh at least MIN_SIZE of the original, and both its video's and its audio's packets
are checked for a backwards step, and only then does it replace the original. A remux that
changed nothing would keep the broken timeline while reading as long as before, and a truncated
or stretched-PTS stub can read as long too; without the length, the size and the packet order,
a hostile or corrupt file could have an ffmpeg of ours put it where the only copy was. The file
is checked for the jump before any of that: one already in order is not rewritten at all, since
a 33 GB read and write to fix nothing is risk for no gain. (Only each packet line's first CSV
field is its time: ffprobe 8 appends the packet's `MPEGTS Stream ID` side data as a field after
it.)

Neither tool is let open anything but a local file, either: a `source.ts` that is really an HLS
master playlist can't send them to a url. Just before the replace the temp is confirmed to still
be the regular file `mkstemp` made, read with `lstat` so that a link is never it whatever it
names (its device and inode compared too, where the filesystem reports them at all), so a temp
swapped for a link to somewhere else doesn't go in place; a writer with access to the stream
folder while the remux runs is, though, outside what this can stop.
"""

from __future__ import annotations

import json
import math
import os
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import wiki
from .errors import ClipperError, exit_code

TOLERANCE_S = 1.0  # a difference this small is ffprobe's rounding, not a jump
PACKET_JUMP_S = 0.5  # a backwards PTS step larger than this is a jump, not B-frame reordering
STREAMS = ("v:0", "a:0")  # both are read: #440's jump is in the audio as often as in the video
TYPES = {"v": "video", "a": "audio"}  # what a select_streams name needs the container to hold
MIN_SIZE = 0.9  # an mpegts remux of a whole file comes out a little bigger, never this much less
SWEEP_S = 3600  # a `.timeline-*.part` this old is a killed remux's, not one running now


def _run(cmd: list[str], tool: str) -> subprocess.CompletedProcess:
    """subprocess.run on ffprobe or ffmpeg; a tool that is missing or can't start is one line
    naming it, not a traceback out of a stage (cli.main re-raises anything but a ClipperError)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace", check=False)
    except FileNotFoundError:
        raise ClipperError(f"{tool} not found: install ffmpeg and put it on PATH ("
                           + wiki.page("Install") + ")") from None
    except OSError as e:
        raise ClipperError(f"{tool} couldn't run: {_reason(e)}") from None


def _reason(e: OSError) -> str:
    """Why an OSError gave up, without its filename: str(e) carries the path on Windows, and a
    message like this one is logged, said in a chat and stored in run.json, so it says why the
    system refused, never where the file is."""
    return e.strerror or "the system refused it"


def probe(path: Path) -> dict[str, Any]:
    """The container's own `start_time` and `duration` as ffprobe gives them, as numbers (None
    where ffprobe has none, or gave a nan or an inf). ClipperError if ffprobe can't run or can't
    read `path`; ffprobe is kept to local files, so a source that is really a playlist is a
    file it can't read rather than a url it fetches."""
    cmd = ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries",
           "format=start_time,duration", "-of", "json", str(path)]
    out = _run(cmd, "ffprobe")
    if out.returncode != 0:
        raise ClipperError(f"{path.name}: ffprobe couldn't read it (exit {exit_code(out.returncode)})")
    try:
        data = json.loads(out.stdout)
    except ValueError:
        raise ClipperError(f"ffprobe's JSON for {path.name} didn't parse") from None
    if not isinstance(data, dict):
        raise ClipperError(f"ffprobe's JSON for {path.name} isn't an object")
    fmt = data.get("format") or {}
    return {"start_time": _seconds(fmt.get("start_time"), None),
            "duration": _seconds(fmt.get("duration"), None)}


def jumps_back(path: Path) -> bool:
    """True when either `path`'s first video stream or its first audio stream steps backwards in
    time by more than PACKET_JUMP_S anywhere in its packet list: the one thing a remux has to
    have fixed. The audio is checked because #440's jump is in the audio as often as in the video
    (the download loses the same stretch from both). A repair keeps every packet and can come out
    as long as the broken file was, so its length alone can't tell a relinearised mux from a
    remux that changed nothing; this reads the file's timestamps and says.

    A stream type the container doesn't have at all (`a:0` of a video-only download) is skipped:
    there is no timeline there to be broken or fixed. A stream the container does have but
    ffprobe lists no packets for is refused, as is a packet with no usable pts_time (missing,
    N/A, unparseable) and a container with neither a video nor an audio stream: nothing here can
    then vouch for the file."""
    kinds = set(_stream_types(path))
    wanted = [stream for stream in STREAMS if TYPES[stream[0]] in kinds]
    if not wanted:
        raise ClipperError(f"{path.name}: ffprobe found no video or audio stream, so the "
                           f"timeline can't be checked")
    return any(not _in_order(_packet_times(path, stream)) for stream in wanted)


def _stream_types(path: Path) -> list[str]:
    """The codec types (`video`, `audio`, ...) the container holds, one per stream. ClipperError
    if ffprobe can't run or refuses the file, or if its JSON doesn't parse: until this is known
    a stream type can't be told from a missing stream."""
    cmd = ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries",
           "stream=codec_type", "-of", "json", str(path)]
    out = _run(cmd, "ffprobe")
    if out.returncode != 0:
        raise ClipperError(f"{path.name}: ffprobe couldn't list its streams (exit {exit_code(out.returncode)})")
    try:
        data = json.loads(out.stdout)
    except ValueError:
        raise ClipperError(f"ffprobe's JSON for {path.name} didn't parse") from None
    if not isinstance(data, dict):
        raise ClipperError(f"ffprobe's JSON for {path.name} isn't an object")
    return [s.get("codec_type") for s in data.get("streams") or [] if isinstance(s, dict)]


def _packet_times(path: Path, stream: str) -> list[float]:
    """`stream`'s packets' timestamps in file order, as numbers. Only each line's first CSV field
    is the pts: ffprobe 8 appends a packet's `MPEGTS Stream ID` side data as another field
    (`62.033000,`), and reading the whole line as the time would refuse every real Kick .ts.
    ClipperError if ffprobe can't run or refuses the file, if the stream has no packets, or if a
    packet's first field is no time at all (empty, N/A, unparseable): each is a timeline nothing
    here can check."""
    cmd = ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", stream,
           "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)]
    out = _run(cmd, "ffprobe")
    if out.returncode != 0:
        raise ClipperError(f"{path.name}: ffprobe couldn't list its packets (exit {exit_code(out.returncode)})")
    times: list[float] = []
    for line in out.stdout.splitlines():  # one packet per line, "N/A" when it has no pts
        t = _seconds(line.split(",", 1)[0].strip(), None)
        if t is None:
            raise ClipperError(f"{path.name}: a packet has no time ffprobe could read, so the "
                               f"timeline can't be checked")
        times.append(t)
    if not times:
        raise ClipperError(f"{path.name}: ffprobe listed no packets for its {stream} stream, so "
                           f"the timeline can't be checked")
    return times


def _in_order(times: list[float]) -> bool:
    """Whether no step in `times` goes backwards by more than PACKET_JUMP_S: B-frames arrive a
    frame back, a jump goes back by a stretch."""
    return all(t >= prev - PACKET_JUMP_S for prev, t in zip(times, times[1:], strict=False))


def _seconds(value: Any, default: float | None) -> float | None:
    """A number ffprobe wrote as a string; "N/A", None, "", a nan and an inf give `default`
    (none of them is a length)."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def container_duration(probed: dict) -> float:
    """The container's own length in seconds; ClipperError if ffprobe gave none, or gave a nan
    or an inf, which would pass (or fail) every comparison below by accident."""
    total = probed.get("duration")
    if total is None or not math.isfinite(total):
        raise ClipperError("ffprobe gave no container duration: the timeline can't be checked")
    return float(total)


def _temp(path: Path) -> tuple[Path, os.stat_result]:
    """A fresh file for the remux, beside `path`, and what `mkstemp` made of it (its stat, read
    from the descriptor): its name is made new, so anything already at it, a symlink included, is
    neither followed nor overwritten. It stays out of `media.source_file`'s `source.*` glob, so a
    remux in flight is never read as the source. A folder that can't take it (permissions, a full
    disk) is a ClipperError."""
    try:
        fd, name = tempfile.mkstemp(prefix=".timeline-", suffix=".part", dir=path.parent)
    except OSError as e:
        raise ClipperError(f"couldn't make a temp file beside {path.name}: {_reason(e)}") from None
    try:
        made = os.fstat(fd)  # the file we made, before anything else can be put at that name
    finally:
        os.close(fd)  # made by us; ffmpeg opens it by name
    return Path(name), made


def _ours(tmp: Path, made: os.stat_result, label: str, name: str) -> None:
    """ClipperError unless `tmp` is still the regular file `mkstemp` made: `os.lstat` follows no
    link, so one now at that name is a link (never the file we made) and the remux cannot take the
    original's place with it, whatever it names. Its (device, inode) is compared too where the
    filesystem reports both; some report 0 on both sides over a share, and then the regular-file
    and link checks alone have to do."""
    try:
        now = os.lstat(tmp)
    except OSError:
        raise ClipperError(f"{label}: the remux's temp for {name} vanished before it could go in "
                           f"place ({name} kept)") from None
    same = (stat.S_ISREG(made.st_mode) and stat.S_ISREG(now.st_mode) and not stat.S_ISLNK(now.st_mode)
            # a link's own mode is S_IFLNK, so S_ISREG already says no: both are asked, so the
            # refusal doesn't rest on one lstat's reading of the type
            and now.st_nlink >= 1
            and (not (made.st_ino and now.st_ino)
                 or (made.st_dev, made.st_ino) == (now.st_dev, now.st_ino)))
    if not same:
        raise ClipperError(f"{label}: the remux's temp for {name} was swapped for something else "
                           f"before it went in place ({name} kept)")


def _discard(tmp: Path) -> None:
    """The remux's temp removed, best effort: whatever failed, and whatever is at the name now, a
    failure to remove it must not hide the ClipperError already on its way out."""
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass


def remux_verified(path: Path, label: str, put_in_place: Callable[[Path, Path], None] | None = None) -> float:
    """`path` checked for a jump first, and only a jumping one written again with its timestamps
    regenerated and the codecs copied, into a fresh temp file, which must pass three checks
    before it replaces the original: its probed length is no less than the original's (within
    TOLERANCE_S), its size is at least MIN_SIZE of the original's (an mpegts remux of a whole
    file comes out a little bigger, never this much less, which is what a truncated or
    stretched-PTS stub fails), and neither its video's nor its audio's packets step backwards. A
    hostile or corrupt file has to pass all three before it can take the place of the only copy;
    a run that lost content, was killed or fixed nothing leaves the original byte for byte as it
    was. The temp goes on every exit, whatever failed.

    A file already in order is not remuxed at all: nothing is written and its bytes are never
    touched, since a 33 GB read and write to fix nothing is a lot of risk for no gain. So a
    --fixup on a jumping file reads it three times (the order check, the remux, the check on
    the new file), and on a clean one once.

    Returns how much longer the remux is in seconds, rounded: the jump (a genpts remux of an
    overlapping timeline is longer by the overlap), or 0.0 when the length didn't change, either
    because the file was already in order or because a difference within TOLERANCE_S is
    ffprobe's rounding, not a jump to store. `put_in_place(tmp, path)` is how the caller replaces
    the file (kick does it under the stream's short run lock); os.replace when None. A
    ClipperError names `label` (the stream's folder) and the reason."""
    if path.suffix != ".ts":  # the only input this command's mpegts copy is right for (#440)
        raise ClipperError(f"{label}: a remux only repairs a Kick .ts source, and {path.name} is not one")
    old = container_duration(probe(path))
    try:  # before anything is written: a file in order is left alone, not rewritten to nothing
        jumping = jumps_back(path)
    except ClipperError as why:
        raise ClipperError(f"{label}: {why}") from None
    if not jumping:
        print(f"{label}: the timeline is already in order")
        return 0.0
    tmp, made = _temp(path)
    try:
        try:
            _remux(path, tmp)
            new = container_duration(probe(tmp))
            jumping = jumps_back(tmp)
        except ClipperError as why:  # a tool's fixed reason: nothing has been replaced
            raise ClipperError(f"{label}: a remux of {path.name} failed ({path.name} kept): "
                               f"{why}") from None
        if new < old - TOLERANCE_S:  # the remux lost its tail: the original is the better file
            raise ClipperError(f"{label}: a remux of {path.name} came out {old - new:.1f} s shorter "
                               f"({path.name} kept)")
        if jumping:  # the length can be right and the timeline still broken: this is the proof
            raise ClipperError(f"{label}: a remux of {path.name} didn't fix the jump "
                               f"({path.name} kept): its timestamps still step back")
        new_size, old_size = _sizes(tmp, path, label)
        if new_size < MIN_SIZE * old_size:  # a stub, not a remux of the whole file: refused
            raise ClipperError(f"{label}: a remux of {path.name} came out {new_size} bytes where "
                               f"its source has {old_size} ({path.name} kept): truncated")
        _ours(tmp, made, label, path.name)  # still the file we made, not a link put in its place
        try:
            (put_in_place or os.replace)(tmp, path)
        except OSError as e:  # the share refused it: the original is still whole, the tmp is no use
            raise ClipperError(f"{label}: couldn't put the remuxed {path.name} in place "
                               f"({path.name} kept): {_reason(e)}") from None
        tmp = None  # it is `path` now
    finally:
        if tmp is not None:
            _discard(tmp)
    growth = round(new - old, 1)
    if growth <= TOLERANCE_S:  # fixed, but the muxer gained no measurable length
        growth = 0.0
        print(f"{label}: remuxed {path.name}; its timeline is in order")
    else:
        print(f"{label}: the timeline jumps back {growth} s; remuxed ({old:.1f} s -> {new:.1f} s)")
    return growth


def sweep(stream_dir: Path) -> None:
    """A remux killed mid-write leaves its multi-GB `.timeline-*.part` beside the source, and
    nothing else ever looks at that name again: each older than SWEEP_S is removed, one line per
    file. A younger one may be a remux running right now, and anything else in the folder is left
    alone. A file the share won't remove is said and the run carries on: this is housekeeping,
    not a check, and must never be why a fetch fails."""
    now = time.time()
    for leftover in sorted(stream_dir.glob(".timeline-*.part")):
        try:
            if now - leftover.stat().st_mtime < SWEEP_S:
                continue
            leftover.unlink()
        except OSError as e:
            print(f"{stream_dir.name}: couldn't remove the leftover remux temp {leftover.name}: "
                  f"{_reason(e)}")
            continue
        print(f"{stream_dir.name}: removed a leftover remux temp ({leftover.name})")


def _sizes(tmp: Path, path: Path, label: str) -> tuple[int, int]:
    """The tmp's and the original's sizes in bytes. A stat that fails is a ClipperError: without
    both there is nothing to weigh the tmp against, and it doesn't go in place."""
    try:
        return tmp.stat().st_size, path.stat().st_size
    except OSError as e:
        raise ClipperError(f"{label}: couldn't weigh a remux of {path.name} against it "
                           f"({path.name} kept): {_reason(e)}") from None


def _remux(path: Path, tmp: Path) -> None:
    """`path`'s codecs copied into `tmp` with its timestamps regenerated. `-f mpegts` says what
    the input is instead of letting ffmpeg probe it into something else, and
    `-protocol_whitelist file` keeps both ends on local files: a source.ts that is really an HLS
    master playlist can't send ffmpeg to a url. ClipperError if ffmpeg can't run or fails (its
    tmp is the caller's to remove)."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-fflags", "+genpts",
           "-protocol_whitelist", "file", "-f", "mpegts", "-i", str(path),
           "-c", "copy", "-f", "mpegts", str(tmp)]
    out = _run(cmd, "ffmpeg")
    try:
        wrote = tmp.stat().st_size > 0  # the temp is made empty before ffmpeg opens it
    except OSError:  # gone under us
        wrote = False
    if out.returncode != 0:
        raise ClipperError(f"ffmpeg couldn't remux it (exit {exit_code(out.returncode)})")
    if not wrote:
        raise ClipperError("ffmpeg wrote no output")

