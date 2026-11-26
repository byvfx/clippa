"""Stage 2: transcribe a stream with WhisperX and write words.jsonl.

Usage:
    clippa transcribe <stream_id>     (settings from [transcribe] in clipper.toml)

Reads the first source.* file in the stream dir, extracts 16 kHz mono
audio.wav (skipped if present), transcribes it with word timings and writes:
    transcript.json  raw WhisperX segments (aligned)
    words.jsonl      one word per line: word, start, end, score, speaker, seg
    diarization.json with [transcribe] diarize: speaker turns and voice embeddings (pyannote,
                     issue #52); `clippa speakers` then names them and fills `speaker`

[transcribe] backend:
    whisperx  faster-whisper on CUDA + wav2vec2 word alignment (the GPU worker; `uv sync
              --extra gpu`)
    mlx       mlx-whisper on Apple Silicon (`uv sync --extra mac`), with its own word
              timestamps; when WhisperX is installed too (mlx_align), its alignment model
              refines them on the CPU
    modal     the same WhisperX on a Modal cloud GPU (`uv sync --extra modal`, issue #11);
              audio.wav goes up as FLAC, the aligned segments come back. Deploy the app
              first: `clippa transcribe --deploy` (clippa/modal_app.py)
"""

from __future__ import annotations

import gc
import json
import math
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import jsonstate, timeline, wiki
from .errors import ClipperError, exit_code
from .media import is_link, remove_link, source_file, try_duration


class TimelineJump(ClipperError):
    """#440: the source's timeline jumps back, so every time after it would be off. `.reason` is
    the message without the advice, for a caller whose copy must not carry a url or a command
    (`archive` writes the exception into error.txt); `str()` adds the `clippa fetch <url> --fixup`
    line when that would repair this source."""

    def __init__(self, reason: str, advice: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {advice}" if advice else reason)


# FAT and SMB round timestamps by as much as 2 s, so a wav written just after its source can read
# as older than it; that much is rounding. Anything older was decoded from a different file —
# whatever the source's own stamp says, and never compared with this clock (a source stamped in
# the future says nothing about which file the wav was made from).
MTIME_ROUNDING_S = 2.0

# Beside audio.wav: it was decoded from a source that is not the one here any more, so it has to be
# decoded again. An mtime can't say that on its own (a fast remux leaves the wav looking younger
# than the share rounds by), and the marker holds where the wav itself couldn't be dropped.
STALE_MARKER = "audio.wav.stale"

# Beside audio.wav (#448): which file it was decoded from, as that file's own lstat read when the
# decode began ({"size", "mtime_ns"}). An mtime alone is not an identity: a source put back from a
# backup, or moved in by hand, can carry any stamp, older than the wav included. The size must
# match exactly and the mtime within MTIME_ROUNDING_S, since the laptop over SMB and a container
# on the NAS's own disk read the same file's time at different precisions.
SRC_STAMP = "audio.wav.src"


def _drop_link(p: Path, label: str) -> None:
    """A link where ffmpeg is about to write with `-y`: ffmpeg follows it and would truncate what
    it names, so the link goes first (the link alone, never what it names). One that can't be
    removed is a one-line error naming the stream and the system's reason, with no path."""
    if not is_link(p):
        return
    try:
        remove_link(p)
    except OSError as e:
        raise ClipperError(f"{label}: couldn't drop the linked {p.name} "
                           f"({e.strerror or 'the system refused it'})") from None


def mark_stale(stream_dir: Path) -> None:
    """Record that this stream's `audio.wav` was decoded from another source, so the next
    transcribe decodes it again (a --fixup calls this before it replaces source.ts, which is why
    the fact outlives a drop of the wav the share refused). A marker that can't be written is one
    line: the caller keeps the source it was about to replace, which the wav still matches."""
    marker = stream_dir / STALE_MARKER
    _drop_link(marker, stream_dir.name)  # a link here is not a marker: the link goes, never its target
    try:
        marker.touch(exist_ok=True)
    except OSError as e:
        raise ClipperError(f"{stream_dir.name}: couldn't record that its audio.wav is stale "
                           f"({e.strerror or 'the system refused it'})") from None


def clear_stale(stream_dir: Path) -> None:
    """Best effort: a marker left beside a wav that was decoded again only costs one more decode,
    so failing to remove it must never turn a good decode or replace into an error."""
    try:
        (stream_dir / STALE_MARKER).unlink(missing_ok=True)
    except OSError:
        pass


def forget_stamp(stream_dir: Path) -> None:
    """Best effort, with the wav it describes gone: a stamp left behind only ever says the next
    wav is of another file (it was written for an earlier decode), which costs a decode, never a
    wrong reuse."""
    try:
        (stream_dir / SRC_STAMP).unlink(missing_ok=True)
    except OSError:
        pass


def _stamp_says(stream_dir: Path, src_st: os.stat_result | None) -> bool | None:
    """Whether audio.wav.src says the wav was decoded from the file `src_st` describes; None when
    there is no stamp (a wav decoded before #448, or one whose stamp couldn't be written), which the
    mtime rule decides. A stamp that is a link, can't be read or isn't one is a no: it is the
    record, and one this can't read can't vouch for the wav. A source the share won't stat
    matches nothing."""
    path = stream_dir / SRC_STAMP
    try:
        if is_link(path):
            return False
        stamp = jsonstate.load(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return False
    if src_st is None or not isinstance(stamp, dict):
        return False
    size, mtime = stamp.get("size"), stamp.get("mtime_ns")
    if type(size) is not int or type(mtime) is not int:  # bool is an int: not a size
        return False
    return size == src_st.st_size and abs(mtime - src_st.st_mtime_ns) <= int(MTIME_ROUNDING_S * 10**9)


def _write_stamp(stream_dir: Path, decoded: tuple[int, int] | None) -> None:
    """Best effort after a decode is installed: with no stamp the wav falls back to the mtime rule,
    which a wav written just now passes. A share that won't take it leaves an earlier stamp at
    worst (forget_stamp went with the earlier wav, so one here is a failed drop's), which names
    another file and costs one decode."""
    if decoded is None:  # the source's times couldn't be read: nothing to record
        forget_stamp(stream_dir)
        return
    try:
        jsonstate.write(stream_dir / SRC_STAMP, {"size": decoded[0], "mtime_ns": decoded[1]}, indent=None)
    except OSError:
        pass


def extract_audio(stream_dir: Path, *, trust_mtime: bool = True) -> Path:
    """The stream's audio.wav: the one already here when it was decoded from this source, else the
    source decoded again (gaps filled with silence, 16 kHz mono).

    Which file a cached wav came from is decided in this order: the stale marker (a --fixup or a
    download wrote it: decode again, whatever else says); then the stamp beside it, audio.wav.src
    (#448: reuse only when it names this source's size and mtime); then, for a wav decoded before
    there were stamps, its mtime against the source's. trust_mtime False drops that last rule (an
    archive item's wav is only ever a failed run's leftover, so one with no stamp is decoded
    again rather than trusted)."""
    wav = stream_dir / "audio.wav"
    marker = stream_dir / STALE_MARKER
    _drop_link(wav, stream_dir.name)  # a link is not a cache of ours: drop the link, never its target
    try:
        src = source_file(stream_dir)
    except ClipperError:
        if wav.exists():  # no source (any more): the wav is all there is to read
            return wav
        raise
    if is_link(src):  # ffmpeg must not be sent through a link to somewhere else
        raise ClipperError(f"{stream_dir.name}: its source is a link, not a file; "
                           f"refusing to read it")
    if wav.exists():
        try:  # lstat: only these two regular files' own times decide which is newer
            src_st = src.lstat()
        except OSError:  # can't be stat'ed: its times say nothing (the marker still does)
            src_st = None
        try:
            wav_st = wav.lstat()
        except OSError:
            wav_st = None
        stamp = _stamp_says(stream_dir, src_st)
        # With no stamp, a wav up to the share's rounding older than its source is not older:
        # keep it. Anything older was decoded from a different file, so it is dropped and the
        # source decoded again. The marker says the same outright, whatever the stamp or the
        # mtime reads: a fast remux can leave a wav from the old source looking younger than the
        # one that replaced it. It is dropped even when the times can't be compared, since it is
        # the one thing that knows.
        by_mtime = (trust_mtime and src_st is not None and wav_st is not None
                    and wav_st.st_mtime_ns >= src_st.st_mtime_ns - int(MTIME_ROUNDING_S * 10**9))
        if not marker.exists() and (stamp if stamp is not None else by_mtime):
            return wav
        try:
            wav.unlink()  # marked, or not this source's by its stamp or its mtime
        except OSError as e:
            raise ClipperError(f"{stream_dir.name}: couldn't drop the stale audio.wav "
                               f"({e.strerror or 'the system refused it'})") from None
        clear_stale(stream_dir)  # the wav it was about is gone
        forget_stamp(stream_dir)  # and so is the file it named
    tmp = wav.with_suffix(".tmp.wav")
    # Which file this decode reads, as its own lstat says: a --fixup or a re-download can replace the
    # source while a long decode is still on the old one, and audio of a file that is gone must not
    # be installed for this one (after a repair the two lengths agree, so the timeline check cannot
    # tell). A share that won't read its times gives nothing to compare, so the decode runs.
    try:
        st = src.lstat()
        decoding = (st.st_size, st.st_mtime_ns)
    except OSError:
        decoding = None
    # A recording with dropped segments has timestamp jumps; WAV has no timestamps, so
    # without aresample the samples close up over each gap and every transcript time
    # after it runs early against the video (kick-132733064 ended 47 s apart). async=1
    # fills each gap with silence, first_pts=0 keeps the start where the source starts.
    # -protocol_whitelist file: the source is a local file, so one that is really an HLS
    # playlist can't send ffmpeg to a url. The whitelist does not stop a link at the
    # output, though: ffmpeg follows it and would truncate the target, so it goes first.
    _drop_link(tmp, stream_dir.name)
    try:
        try:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file",
                 "-i", str(src), "-vn", "-af", "aresample=async=1:first_pts=0",
                 "-ac", "1", "-ar", "16000", str(tmp)],
                check=True,
            )
        except subprocess.CalledProcessError as e:  # a file ffmpeg can't read is one line, no stderr
            raise ClipperError(f"{stream_dir.name}: ffmpeg couldn't decode its source "
                               f"(exit {exit_code(e.returncode)})") from None
        try:
            st = src.lstat()
            decoded = (st.st_size, st.st_mtime_ns)
        except OSError:  # gone or unreadable: not the file this audio came from either
            decoded = None
        if decoding is not None and decoded != decoding:
            # This tmp goes with the finally below, the wav on disk stays as another run left it,
            # and so does a marker that run wrote: that one is about the wav, not this tmp.
            raise ClipperError(f"{stream_dir.name}: its source changed while its audio was being "
                               f"decoded; run it again")
        tmp.replace(wav)
        clear_stale(stream_dir)  # a marker from an earlier failed drop must not hold this wav back
        _write_stamp(stream_dir, decoding)  # which file this wav is of (#448); equal to decoded here
    finally:  # a decode that failed midway leaves no partial wav to be read as one
        try:  # best effort: the error that brought us here is the one to report
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return wav


def check_audio_timeline(stream_dir: Path) -> None:
    """#440: a source whose timestamps jump back decodes to an audio.wav longer than the source's
    own container (the second stretch's audio runs on past the container's end, which the
    container's duration doesn't show), and every transcript time after the jump would be off by
    it. Such a stream is refused until its source is rebuilt with `clippa fetch <url> --fixup`,
    which only a Kick VOD's source can be (the message names the command when meta.json says it
    would work); an audio shorter than the container is only a warning. The refusal is a
    TimelineJump, whose `.reason` is the message without that command. A .wav source is the
    audio itself, so there is nothing to compare and no probe is made."""
    lengths = audio_lengths(stream_dir)
    if lengths is None:
        return
    wav_s, container_s = lengths
    jump = wav_s - container_s
    if jump <= timeline.TOLERANCE_S:
        if jump < -timeline.TOLERANCE_S:
            print(f"{stream_dir.name}: warning: audio.wav is {abs(jump):.1f} s shorter than its "
                  f"source's container; the transcript may end early", flush=True)
        return
    # The fixup command is advice: a meta.json that is unreadable or not an object must not change
    # what is raised (a corrupt one would otherwise turn the refusal into a ValueError)
    try:
        meta = jsonstate.read(stream_dir / "meta.json", {})
    except (OSError, ValueError):
        meta = {}
    url = _fixup_command(meta)
    if url:
        raise TimelineJump(
            f"{stream_dir.name}: the source's timeline jumps back {jump:.1f} s (audio.wav, "
            f"{wav_s:.1f} s, runs past a container of {container_s:.1f} s), so the transcript "
            "would be off after it", url)
    raise TimelineJump(
        f"{stream_dir.name}: the source's timeline and its audio disagree by {jump:.1f} s "
        f"(audio.wav, {wav_s:.1f} s, runs past a container of {container_s:.1f} s), so the "
        f"transcript would be off after it")


def audio_lengths(stream_dir: Path) -> tuple[float, float] | None:
    """(audio.wav's length, its source's container length) in seconds, as the timeline guard
    compares them; None when there is nothing to compare: no source, a .wav source (the audio is
    the source), or a wav ffprobe can't time. A source ffprobe can't time is a ClipperError, as it
    is for the guard."""
    try:
        src = source_file(stream_dir)
    except ClipperError:  # no source to compare the audio with
        return None
    if src.suffix == ".wav":  # the audio is the source: nothing to compare it with
        return None
    wav_s = try_duration(stream_dir / "audio.wav")
    if wav_s is None or not math.isfinite(wav_s):  # ffprobe can't tell, or gave a nan
        return None
    return wav_s, timeline.container_duration(timeline.probe(src))


def _fixup_command(meta: object) -> str:
    """`clippa fetch <url> --fixup` for this stream's source, or "" when that would fetch the
    wrong thing or nothing: --fixup is Kick's only (its remux is an mpegts copy), a recording's
    meta names a channel rather than a VOD, and a query or fragment after a VOD's id is not the
    VOD."""
    if not isinstance(meta, dict):  # advice only: a list or a string names no source
        return ""
    if meta.get("platform") != "kick" and meta.get("source") != "kick":
        return ""
    from . import kick  # here only: kick brings the network stack

    url = kick.canonical_vod_url(meta.get("url"))
    return f"clippa fetch {shlex.quote(url)} --fixup" if url else ""


def fill_missing_times(words: list[dict]) -> int:
    """WhisperX can't align some tokens (digits, symbols). Give them times by
    spreading them evenly between their aligned neighbours."""
    missing = 0
    i = 0
    while i < len(words):
        if "start" in words[i]:
            i += 1
            continue
        j = i
        while j < len(words) and "start" not in words[j]:
            j += 1
        lo = words[i - 1]["end"] if i > 0 else words[j]["start"] if j < len(words) else 0.0
        hi = words[j]["start"] if j < len(words) else lo
        step = (hi - lo) / (j - i) if hi > lo else 0.0
        for k in range(i, j):
            words[k]["start"] = round(lo + step * (k - i), 3)
            words[k]["end"] = round(lo + step * (k - i + 1), 3)
            words[k]["score"] = None
            words[k]["interpolated"] = True
        missing += j - i
        i = j
    return missing


def transcribe_mlx(wav: Path, tc: dict) -> tuple[list[dict], float]:
    """Segments with words from mlx-whisper (Apple Silicon), optionally re-aligned."""
    import mlx_whisper

    t1 = time.time()
    result = mlx_whisper.transcribe(str(wav), path_or_hf_repo=tc["mlx_model"],
                                    language=tc["language"], word_timestamps=True,
                                    condition_on_previous_text=False)
    segments = [{"start": s["start"], "end": s["end"], "text": s["text"],
                 "words": [{"word": w["word"].strip(), "start": w["start"], "end": w["end"],
                            "score": w.get("probability")} for w in s.get("words", [])]}
                for s in result["segments"]]
    print(f"transcribed in {time.time() - t1:.0f}s (mlx {tc['mlx_model']})", flush=True)
    dur = segments[-1]["end"] if segments else 0.0
    if tc["mlx_align"]:
        try:
            import whisperx
        except ImportError:
            return segments, dur
        t2 = time.time()
        audio = whisperx.load_audio(str(wav))
        model, meta = whisperx.load_align_model(language_code=tc["language"], device="cpu")
        segments = whisperx.align(segments, model, meta, audio, "cpu",
                                  return_char_alignments=False)["segments"]
        dur = len(audio) / 16000
        print(f"aligned in {time.time() - t2:.0f}s (cpu)", flush=True)
    return segments, dur


MODAL_CODECS = {"flac": (".flac", ["-c:a", "flac"]),
                "opus": (".ogg", ["-c:a", "libopus", "-b:a", "48k"])}
MODAL_MULTIPART = 1024**3   # Modal's client uploads this and more in parts (modal/_utils/blob_utils.py)


def modal_encode(wav: Path, fmt: str) -> tuple[bytes, str]:
    """The wav encoded for the Modal upload ([transcribe] modal_audio): its bytes and suffix."""
    suffix, codec = MODAL_CODECS[fmt]
    enc = wav.with_name(f"audio.modal{suffix}")
    _drop_link(enc, wav.parent.name)  # a link the encode would follow and truncate what it names
    try:
        # -protocol_whitelist file: a cached audio.wav (or one beside no source) never went
        # through extract_audio's decode, so a playlist under that name must not send ffmpeg to a
        # url here either. No -f: the suffix is the wav's own.
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file",
                        "-i", str(wav), *codec, str(enc)], check=True)
        return enc.read_bytes(), suffix
    finally:  # never leave a GB of audio beside the stream, a failed encode included
        try:  # best effort: the error that brought us here is the one to report
            enc.unlink(missing_ok=True)
        except OSError:
            pass


def too_big_for_flac(tc: dict, fmt: str, size: int) -> bool:
    """A FLAC over [transcribe] modal_opus_over_mb goes up as opus instead (#422): from 1 GiB
    Modal's client uploads in parts all at once, and an 18 h stream's 1.3 GB took the NAS
    out of memory. 0 (or less) = never switch."""
    limit = tc["modal_opus_over_mb"]
    return fmt == "flac" and limit > 0 and size > limit * 1e6


def transcribe_modal(wav: Path, tc: dict, runs: Path | None = None, method: str = "transcribe") -> tuple[dict, dict]:
    """WhisperX on Modal: the call's result (segments, dur, diarization) plus what it cost (also
    logged to `runs`, [paths] modal_runs, for `clippa modal` and the war room). method
    "diarize" gets the speaker turns alone."""
    from .modalmon import tracked

    fmt = tc["modal_audio"]
    t1 = time.time()
    data, suffix = modal_encode(wav, fmt)
    print(f"encoded {len(data) / 1e6:.0f} MB {fmt} in {time.time() - t1:.0f}s", flush=True)
    if too_big_for_flac(tc, fmt, len(data)):
        fmt, data = "opus", b""   # let the FLAC go before the opus is read in
        t1 = time.time()
        data, suffix = modal_encode(wav, fmt)
        print(f"over [transcribe] modal_opus_over_mb ({tc['modal_opus_over_mb']}): "
              f"encoded {len(data) / 1e6:.0f} MB opus instead in {time.time() - t1:.0f}s", flush=True)
    if len(data) >= MODAL_MULTIPART:
        print(f"warning: {len(data) / 1e6:.0f} MB is past 1 GiB, where Modal uploads in parts all in "
              f"memory; if the run is killed: {wiki.page('Transcribe-on-Modal', 'troubleshooting')}", flush=True)

    with tracked(runs, "stream" if method == "transcribe" else method, wav.parent.name, tc["modal_gpu"],
                 max(0, wav.stat().st_size - 44) / 32000) as rec:  # 16 kHz mono s16
        t2 = time.time()
        worker = modal_worker(tc, tc["modal_gpu"])
        out = getattr(worker, method).remote(data, suffix, modal_settings(tc, diarize=tc["diarize"] or method == "diarize"))
        cost = modal_cost(tc["modal_gpu"], time.time() - t2, out)
        rec.update(cost, audio_s=round(out["dur"], 1))
    print(f"modal {out['gpu']}: {cost['call_s']:.0f}s call ({out['timings']}), ~${cost['est_usd']:.2f}",
          flush=True)
    return out, cost


# The Modal apps took the Clippa name (#286 pass 3). Until `clippa transcribe --deploy` (or
# `clippa perform lipsync --deploy`) has made the new one, a lookup finds the old app instead.
# #286's last pass drops this.
OLD_MODAL_APPS = {"clippa-whisperx": "clipper-whisperx", "clippa-lipsync": "clipper-lipsync"}


def modal_found(lookup, app: str, name: str) -> tuple:
    """(lookup(app, name) hydrated, the app name it was found under). lookup is
    modal.Cls.from_name or modal.Function.from_name; the old name (OLD_MODAL_APPS) when only
    that one is deployed."""
    import modal

    try:
        return lookup(app, name).hydrate(), app
    except modal.exception.NotFoundError:
        if not (old := OLD_MODAL_APPS.get(app)):
            raise
        return lookup(old, name).hydrate(), old


def modal_deployed(lookup, app: str, name: str):
    """modal_found's object alone."""
    return modal_found(lookup, app, name)[0]


def modal_worker(tc: dict, gpu: str):
    """An instance of the deployed WhisperX class, on this GPU ("" = the app's default)."""
    import modal

    cls = modal_deployed(modal.Cls.from_name, tc["modal_app"], "WhisperX")
    return (cls.with_options(gpu=gpu) if gpu else cls)()


def modal_settings(tc: dict, diarize: bool = False) -> dict:
    keys = ("model", "language", "compute_type", "batch_size")
    if diarize:
        keys += ("diarize", "diarize_model", "min_speakers", "max_speakers")
    return {k: tc[k] for k in keys}


def diarize_local(audio, tc: dict) -> dict:
    """The Modal app's _diarize on this machine's GPU (backend whisperx). Needs HF_TOKEN."""
    import os

    from whisperx.diarize import DiarizationPipeline

    pipe = DiarizationPipeline(model_name=tc["diarize_model"], token=os.environ.get("HF_TOKEN"),
                               device=tc["device"])
    df, emb = pipe(audio, min_speakers=tc["min_speakers"] or None, max_speakers=tc["max_speakers"] or None,
                   return_embeddings=True)
    return {"model": tc["diarize_model"], "embeddings": emb or {},
            "turns": [[round(a, 3), round(b, 3), spk] for a, b, spk in zip(df["start"], df["end"], df["speaker"])]}


def write_diarization(stream_dir: Path, diarization: dict, cost: dict | None = None) -> None:
    turns = diarization["turns"]
    jsonstate.write(stream_dir / "diarization.json", {**diarization, **({"modal": cost} if cost else {})},
                    indent=None)
    print(f"diarized: {len(turns)} turns, {len(diarization['embeddings'])} speakers", flush=True)


def diarize(stream_dir: Path, tc: dict, runs: Path | None = None) -> None:
    """Speaker turns for a stream already transcribed (no new transcript): diarization.json.
    The timeline guard runs after the audio is extracted, as in `run` (#440): `clippa speakers
    <stream> --diarize` can be the first stage on a source whose audio runs past its container,
    and its diarization times would be off by the jump as a transcript's are."""
    wav = extract_audio(stream_dir)
    check_audio_timeline(stream_dir)
    if tc["backend"] == "modal":
        out, cost = transcribe_modal(wav, tc, runs, method="diarize")
        return write_diarization(stream_dir, out["diarization"], cost)
    if tc["backend"] != "whisperx":
        raise ClipperError("diarization needs [transcribe] backend = whisperx or modal")
    import whisperx

    t0 = time.time()
    write_diarization(stream_dir, diarize_local(whisperx.load_audio(str(wav)), tc))
    print(f"in {time.time() - t0:.0f}s", flush=True)


def modal_cost(gpu: str, wall: float, out: dict) -> dict:
    """Billed time is the container's life: about the call's wall clock (upload and cold
    start included) plus scaledown_window idle, so this is an upper estimate."""
    from .modal_app import GPU_PRICES

    price = GPU_PRICES.get(gpu or "L4", 0.0)
    return {"gpu": out["gpu"], "sku": gpu or "L4", "call_s": round(wall, 1), **out["timings"],
            "est_usd": round((wall + 30) / 3600 * price, 3)}


def run(stream_dir: Path, tc: dict, runs: Path | None = None) -> None:
    t0 = time.time()
    wav = extract_audio(stream_dir)
    check_audio_timeline(stream_dir)
    if tc["backend"] == "mlx":
        if tc["diarize"]:
            print("[transcribe] diarize needs backend whisperx or modal; skipped on mlx", flush=True)
        segments, dur = transcribe_mlx(wav, tc)
        return write(stream_dir, tc, segments, dur, t0)
    if tc["backend"] == "modal":
        out, cost = transcribe_modal(wav, tc, runs)
        if out.get("diarization"):
            write_diarization(stream_dir, out["diarization"], cost)
        return write(stream_dir, tc, out["segments"], out["dur"], t0, {"modal": cost})
    if tc["backend"] != "whisperx":
        raise ClipperError(f"[transcribe] backend must be whisperx, mlx or modal, not {tc['backend']!r}")
    import torch
    import whisperx

    audio = whisperx.load_audio(str(wav))
    dur = len(audio) / 16000
    print(f"audio {dur / 60:.1f} min, extracted in {time.time() - t0:.0f}s", flush=True)

    t1 = time.time()
    model = whisperx.load_model(tc["model"], tc["device"],
                                compute_type=tc["compute_type"], language=tc["language"])
    result = model.transcribe(audio, batch_size=tc["batch_size"], language=tc["language"])
    del model
    gc.collect()
    torch.cuda.empty_cache()
    print(f"transcribed in {time.time() - t1:.0f}s", flush=True)

    t2 = time.time()
    align_model, meta = whisperx.load_align_model(language_code=tc["language"], device=tc["device"])
    result = whisperx.align(result["segments"], align_model, meta, audio, tc["device"],
                            return_char_alignments=False)
    del align_model
    gc.collect()
    torch.cuda.empty_cache()
    print(f"aligned in {time.time() - t2:.0f}s", flush=True)

    if tc["diarize"]:
        t3 = time.time()
        write_diarization(stream_dir, diarize_local(audio, tc))
        gc.collect()
        torch.cuda.empty_cache()
        print(f"diarized in {time.time() - t3:.0f}s", flush=True)

    write(stream_dir, tc, result["segments"], dur, t0)


def write(stream_dir: Path, tc: dict, segments: list[dict], dur: float, t0: float,
          extra: dict | None = None) -> None:
    from . import jsonstate

    # #75: both files appear whole or not at all; words.jsonl is the stage's done marker, so a
    # half-written one would pass for a finished transcript.
    jsonstate.write(stream_dir / "transcript.json",
                    {"model": tc["mlx_model"] if tc["backend"] == "mlx" else tc["model"],
                     "backend": tc["backend"], "language": tc["language"], **(extra or {}),
                     "segments": segments}, indent=None)

    words = []
    for si, seg in enumerate(segments):
        for w in seg.get("words", []):
            words.append({
                "word": w["word"],
                **({"start": round(w["start"], 3), "end": round(w["end"], 3)} if "start" in w else {}),
                "score": round(w["score"], 3) if "score" in w else None,
                "speaker": w.get("speaker"),
                "seg": si,
            })
    missing = fill_missing_times(words)
    jsonstate.write_text(stream_dir / "words.jsonl",
                         "".join(json.dumps(w, ensure_ascii=False) + "\n" for w in words))

    total = time.time() - t0
    print(f"{len(segments)} segments, {len(words)} words ({missing} interpolated); "
          f"total {total:.0f}s = {dur / max(total, 1e-9):.1f}x realtime")


def deploy() -> None:
    """Deploy (or update) the Modal app in clippa/modal_app.py."""
    from . import modal_app

    # UTF-8: modal prints a check mark, which cp1252 can't encode when output is piped.
    subprocess.run([sys.executable, "-m", "modal", "deploy", modal_app.__file__], check=True,
                   env={**os.environ, "PYTHONIOENCODING": "utf-8"})
