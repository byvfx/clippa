"""`clippa archive`: other people's back catalogs, listed and transcribed on Modal.

Usage:
    clippa archive add <subject> <url>...     list a source's long-form videos into the catalog
    clippa archive transcribe <subject> [--limit N] [--oldest] [--gpu L4] [--parallel N]
                                         [--dry-run]
    clippa archive status [<subject>]
    clippa archive check <subject> [--items ID...] [--all] [--limit N]
    clippa archive links <subject> [--status dead|restricted|alive|all]

A subject (e.g. "jer", issue #48) is paths.archive/<subject>/: catalog.json (every item
found: source, url, title, duration) and one folder per item with meta.json and the same
transcript.json / words.jsonl a stream gets, so later work (#53 search, #48 receipts) reads
them like streams. It is kept apart from paths.streams, so the worker, the bot and uploads
never see archive items.

Sources:
    YouTube channel urls (a channel, or its /videos or /streams tab; never /shorts): listed
        with yt-dlp; each item's audio is downloaded here as YouTube serves it and uploaded to
        Modal untouched (YouTube blocks downloads from datacenter IPs).
    Odysee channels (https://odysee.com/@name): listed with the Odysee API; Modal fetches the
        media itself (WhisperX.transcribe_url), so nothing passes through this machine.
    Kick VODs (a `clippa story` item, #84, with its HLS `m3u8`): the smallest variant
        ([archive] kick_format) is downloaded here, its audio sent as flac.
Items shorter than [archive] min_duration_s are skipped. Catalog order is newest first.
`transcribe` logs progress and failures to <subject>/transcribe.log as well as the console,
and a failed item leaves <subject>/<id>/error.txt until a rerun succeeds.

Link checks (`check`) ask whether each item's source url still plays, without downloading
anything: YouTube's oEmbed endpoint (no key, no quota) and Odysee's resolve. Results go in
<subject>/links.json: status alive | restricted (private, embedding off, or blocked) | dead,
the reason, when it was checked and since when it has had that status. A link that cannot be
checked (network, rate limit) keeps its last result. Items not checked within
[archive] link_recheck_days are checked again; `transcribe` skips items whose link is dead.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from . import hei, jsonstate, speakers, transcribe, ytdl
from .errors import ClipperError
from .media import is_link, remove_link
from .modalmon import head_record, tracked

log = logging.getLogger(__name__)

ODYSEE_API = "https://api.na-backend.odysee.com/api/v1/proxy"
YT_OEMBED = "https://www.youtube.com/oembed"


def subject_dir(cfg: dict, subject: str) -> Path:
    return cfg["paths"]["archive"] / subject


def load_catalog(d: Path) -> dict[str, dict]:
    p = d / "catalog.json"
    return jsonstate.read(p, {})


def save_json(d: Path, name: str, data: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{name}.tmp"
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / name)


def save_catalog(d: Path, cat: dict[str, dict]) -> None:
    """Under the catalog's lock, which add and a running transcribe take too."""
    d.mkdir(parents=True, exist_ok=True)
    with jsonstate.locked(d / "catalog.json"):
        jsonstate.write(d / "catalog.json", cat, indent=1)


def item_dir(d: Path, it: dict) -> Path:
    """Where an item's transcript is: its own folder, or `dir` for one transcribed elsewhere
    (a `clippa story` item that is one of our own streams)."""
    return Path(it["dir"]) if it.get("dir") else d / it["id"]


def item_cfg(cfg: dict, idir: Path) -> dict:
    """An item's config for `speakers`: named against its subject's own voices
    (voices/<subject>.json), as a stream is against its show's, never the base show's."""
    return {**cfg, "show": {**cfg["show"], "key": idir.parent.name}}


def is_item(cfg: dict, d: Path) -> bool:
    return d.resolve().parent.parent == Path(cfg["paths"]["archive"]).resolve()


def load_links(d: Path) -> dict[str, dict]:
    p = d / "links.json"
    return jsonstate.read(p, {})


# ---------- listing ----------

def list_youtube(url: str) -> list[dict]:
    url = url.rstrip("/")
    tabs = [url] if url.endswith(("/videos", "/streams")) else [f"{url}/videos", f"{url}/streams"]
    items = []
    for tab in tabs:
        # approximate_date: "3 years ago" as a date, enough to order a catalog of several sources
        r = ytdl.run("--flat-playlist", "-J", "--extractor-args", "youtubetab:approximate_date", tab)
        if r.returncode and len(tabs) > 1 and "does not have" in r.stderr:
            continue  # e.g. no streams tab
        if r.returncode:
            raise ClipperError(f"yt-dlp {tab}: {ytdl.last_error(r)}")
        data = json.loads(r.stdout)
        for e in data.get("entries") or []:
            if not (e and e.get("id")):
                continue  # yt-dlp lists a deleted or unavailable video as None
            items.append({"id": f"yt-{e['id']}", "source": "youtube",
                          "url": f"https://www.youtube.com/watch?v={e['id']}",
                          "title": e.get("title") or "", "duration_s": float(e.get("duration") or 0),
                          "channel": data.get("channel") or "", "kind": tab.rsplit("/", 1)[1],
                          "date": yt_date(e)})
    return items


def yt_date(e: dict) -> str | None:
    """A flat listing entry's day: upload_date if given, else the approximate timestamp."""
    if d := e.get("upload_date"):
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    if ts := e.get("timestamp") or e.get("release_timestamp"):
        return datetime.fromtimestamp(int(ts), UTC).date().isoformat()
    return None


def odysee(method: str, params: dict) -> dict:
    r = requests.post(f"{ODYSEE_API}?m={method}", timeout=60,
                      json={"jsonrpc": "2.0", "method": method, "params": params})
    r.raise_for_status()
    return r.json()["result"]


def list_odysee(url: str, limit: int | None = None, pace=None) -> list[dict]:
    """A channel's videos, newest first; limit: stop after about that many (a page of 50).
    pace(), when given, is called before each request (the swarm's per-source rate)."""
    pace = pace or (lambda: None)
    name = "@" + url.split("@", 1)[1].split("/")[0].split(":")[0]
    pace()
    ch = odysee("resolve", {"urls": [f"lbry://{name}"]})[f"lbry://{name}"]
    if "error" in ch:
        raise ClipperError(f"odysee {name}: {ch['error'].get('name')}")
    items, page = [], 1
    while True:
        pace()
        r = odysee("claim_search", {"channel_ids": [ch["claim_id"]], "page": page, "page_size": 50,
                                    "order_by": ["release_time"], "stream_types": ["video"]})
        for c in r["items"]:
            v = c.get("value", {})
            items.append({"id": f"od-{c['claim_id']}", "source": "odysee",
                          "url": f"lbry://{c['name']}#{c['claim_id']}", "title": v.get("title") or "",
                          "duration_s": float((v.get("video") or {}).get("duration") or 0),
                          "channel": (ch.get("value") or {}).get("title") or name,
                          "date": datetime.fromtimestamp(int(v.get("release_time") or c["timestamp"]),
                                                         UTC).date().isoformat()})
        if page >= r.get("total_pages", 1) or (limit and len(items) >= limit):
            return items[:limit] if limit else items
        page += 1


def list_source(cfg: dict, url: str) -> list[dict]:
    if hei.is_url(url):
        return hei.list_items(cfg["paths"]["hei_cookies"], cfg["archive"]["hei_pace_s"])
    if "odysee.com" in url or url.startswith("lbry://"):
        return list_odysee(url)
    return list_youtube(url)


def add(cfg: dict, subject: str, urls: list[str], match: str | None = None) -> None:
    """match: keep only items whose title matches this regex (case-insensitive), for a big
    channel with a few of the subject's videos in it (Adult Swim's On Cinema)."""
    d = subject_dir(cfg, subject)
    min_s = cfg["archive"]["min_duration_s"]
    try:
        pattern = re.compile(match, re.I) if match else None
    except re.error as e:
        raise ClipperError(f"--match {match!r} isn't a valid regular expression: {e}") from None
    listings = []  # listed first (minutes for HEI), then merged into the catalog as it is by then
    for url in urls:
        found = list_source(cfg, url)
        listings.append((url, [it for it in found if pattern.search(it["title"])] if pattern else found))
    d.mkdir(parents=True, exist_ok=True)
    # under the lock a running transcribe takes to fill in a length, so neither loses the other's
    with jsonstate.update(d / "catalog.json", {}, indent=1) as saved:
        cat = dict(saved)
        for url, found in listings:
            cat = _merge_listing(d, cat, found, min_s)
            new = [it for it in found if it["id"] in cat and cat[it["id"]] is it]
            skipped = sum(1 for it in found if 0 < it["duration_s"] < min_s)
            matching = f" matching {match!r}" if match else ""
            print(f"{url}: {len(found)} found{matching}, {skipped} under {min_s} s skipped, "
                  f"{len(new)} new ({sum(i['duration_s'] for i in new) / 3600:.0f} h)")
        saved.clear()
        saved.update(newest_first(cat))
    print(f"{subject}: {len(cat)} items, {sum(i['duration_s'] for i in cat.values()) / 3600:.0f} h")


def _merge_listing(d: Path, cat: dict[str, dict], found: list[dict], min_s: float) -> dict[str, dict]:
    """One source's listing into the catalog: its new items ahead of what was there (a source
    lists newest first, so one source stays newest first); an old item is kept as it is, but
    gains what an earlier listing lacked (a date, a length whose probe failed)."""
    new = [it for it in found if not 0 < it["duration_s"] < min_s and it["id"] not in cat]
    for it in found:
        old = cat.get(it["id"])
        if old is None:
            continue
        if it.get("date") and not old.get("date"):
            old["date"] = it["date"]
        if it["duration_s"] and not old.get("duration_s"):
            idir = item_dir(d, old)
            # Only a kept video the share positively says is not a copy drops the item: kept_video
            # reads a stat it refuses as absent, and dropping the item there orphans a real
            # source.mp4 that no listing adds back (a short item is filtered out).
            if (it["duration_s"] < min_s and not (idir / "words.jsonl").exists()
                    and not kept_video(idir) and not _unreadable(idir / "source.mp4")):
                del cat[it["id"]]  # too short after all, and nothing made of it yet
            else:
                old["duration_s"] = it["duration_s"]
    return {**{it["id"]: it for it in new}, **cat}


def newest_first(cat: dict[str, dict]) -> dict[str, dict]:
    """The catalog by date, newest first, when every item has one: several sources then
    interleave, and video/transcribe --limit/--oldest follow the date. Otherwise as it is."""
    if not all(it.get("date") for it in cat.values()):
        return cat
    return dict(sorted(cat.items(), key=lambda kv: kv[1]["date"], reverse=True))


# ---------- transcribing ----------

# Billed seconds a call costs beyond its audio at the measured speed: the download into the
# container, model load, and the 30 s idle transcribe.modal_cost adds (#228). Measured from
# modal_runs.jsonl on 2026-11-13: Jer's long videos 37 s a call, 759 short Instagram clips 58 s,
# HEI items 224 s (big downloads). A subject with enough runs of its own uses its own mean.
CALL_OVERHEAD_S = 60.0
OWN_RUNS = 20   # runs of a subject before its own overhead is trusted


def estimate_usd(hours: float, gpu: str, diarize: bool = False, calls: int = 0,
                 overhead_s: float = CALL_OVERHEAD_S) -> float:
    """GPU time for `hours` of audio at the measured speed, plus `overhead_s` for each of `calls`."""
    from .modal_app import GPU_PRICES

    return (gpu_hours(hours, gpu, diarize) + calls * overhead_s / 3600) * GPU_PRICES.get(gpu, 0.0)


def gpu_hours(hours: float, gpu: str, diarize: bool = False) -> float:
    """GPU hours the work itself takes for `hours` of audio, at the measured speeds."""
    from .modal_app import DIARIZE_SPEED, SPEED

    speed = SPEED.get(gpu, 30.0)
    return hours / speed + (hours / (DIARIZE_SPEED * speed / SPEED["L40S"]) if diarize else 0)


def gpu_sku(run: dict) -> str:
    """The Modal GPU a logged run asked for: its "sku" (logged since #228's review), else its
    device name matched to a priced GPU ("NVIDIA L4" -> L4, "NVIDIA A100-SXM4-40GB" -> A100,
    "NVIDIA H100 80GB HBM3" -> H100); "" when none matches."""
    from .modal_app import GPU_PRICES

    if run.get("sku"):
        return str(run["sku"])
    tokens = str(run.get("gpu") or "").upper().replace("-", " ").split()
    return next((k for k in GPU_PRICES if k.upper() in tokens), "")


def call_overhead_s(cfg: dict, subject: str, gpu: str) -> tuple[float, str]:
    """(seconds a call costs beyond its audio, where that figure comes from): the subject's own
    runs on this GPU in modal_runs.jsonl when it has OWN_RUNS of them, else CALL_OVERHEAD_S. Each
    run's work is taken off as it was done (with speaker turns if it has diarize_s). A failed
    call logs no time, so it can't count."""
    from .modalmon import read_runs

    runs = [r for r in read_runs(cfg["paths"]["modal_runs"])
            if r.get("event") == "end" and r.get("ok") and r.get("call_s") is not None
            and str(r.get("what", "")).startswith(f"{subject}/") and gpu_sku(r) == gpu]
    # the newest by time: a backfill appends old runs after new ones
    runs = sorted(runs, key=lambda r: str(r.get("at", "")))[-500:]
    if len(runs) < OWN_RUNS:
        return CALL_OVERHEAD_S, "typical"
    over = sum(r["call_s"] + 30 - gpu_hours(r["audio_s"], gpu, bool(r.get("diarize_s"))) for r in runs) / len(runs)
    return max(over, 0.0), f"{subject}'s last {len(runs)} runs on {gpu}"


def transcribe_item(cfg: dict, idir: Path, it: dict, gpu: str, retry_on: tuple[str, ...] = ytdl.AGE) -> dict:
    """One item: words.jsonl and transcript.json in idir; returns the cost record. retry_on: the
    YouTube errors retried signed in (ytdl.AGE; a story adds ytdl.BOT when [story] allows it)."""
    tc = dict(cfg["transcribe"], backend="modal", diarize=cfg["archive"]["diarize"])
    settings = transcribe.modal_settings(tc, diarize=tc["diarize"])
    idir.mkdir(parents=True, exist_ok=True)
    meta = dict(it)
    t0 = time.time()
    worker = transcribe.modal_worker(tc, gpu)
    signed_in = False

    def call(fn, *args) -> tuple[dict, dict]:  # logged for `clippa modal` and the war room
        with tracked(cfg["paths"]["modal_runs"], "archive", f"{idir.parent.name}/{it['id']}", gpu,
                     meta.get("duration_s") or 0) as rec:
            t1 = time.time()
            out = fn.remote(*args)
            cost = transcribe.modal_cost(gpu, time.time() - t1, out)
            rec.update(cost, audio_s=round(out["dur"], 1))
        return out, cost

    if it["source"] == "youtube":
        args = ["-f", cfg["archive"]["youtube_format"], "--write-info-json", "--no-playlist", "--no-mtime",
                "-o", str(idir / "audio.%(ext)s"), it["url"]]
        r, signed_in = ytdl.run_retrying(cfg, *args, retry_on=retry_on)  # see ytdl's docstring
        # A finished download left by a run that died later is kept (yt-dlp calls it downloaded).
        # This run's file is the one its info names; beside a leftover in another format and
        # with no ext in the info, the newest (--no-mtime: written now, not the upload's date).
        audio = [p for p in idir.glob("audio.*") if p.suffix not in (".json", ".part", ".ytdl")]
        if r.returncode or not audio:
            raise RuntimeError(f"yt-dlp: {ytdl.last_error(r)}")
        info_path = idir / "audio.info.json"
        info = jsonstate.load(info_path)
        info_path.unlink()
        named = idir / f"audio.{info['ext']}" if info.get("ext") else None
        mine = named if named in audio else max(audio, key=lambda p: p.stat().st_mtime)
        ts = info.get("timestamp") or info.get("release_timestamp")
        meta.update(title=info.get("title") or it["title"], description=info.get("description") or "",
                    channel=info.get("channel") or info.get("uploader") or it.get("channel") or "",
                    duration_s=float(info.get("duration") or it["duration_s"]),
                    date=datetime.fromtimestamp(ts, UTC).date().isoformat() if ts else None)
        data = mine.read_bytes()
        out, cost = call(worker.transcribe, data, mine.suffix, settings)
        for p in idir.glob("audio.*"):  # transcribed: this one and every leftover (other formats, resume notes) go
            p.unlink()
    elif it["source"] in ("kick", "hei"):
        # The video `archive video` kept, else the stream's smallest download: Kick's HLS
        # variants carry video and audio together (no audio-only format), HEI's has an audio
        # rendition. Its audio goes to Modal as flac, as a stream's does. item_source says which
        # copy that is, and whether this run has to fetch it.
        _kept, src, fresh = item_source(idir)
        refused = idir / "source.refused.ts"
        marker = idir / ".refused"
        kept_name = idir / "source.mp4"
        if fresh:
            # Left there, a link under that name sorts before source.ts in extract_audio's
            # `source.*` glob and is refused as a link, so no run ever advanced and the next one
            # dropped the download this one made. Removing it (never its target) is what lets the
            # item finish; one that can't be removed stops the run before a download is spent on
            # nothing.
            _drop_link(refused, idir.name)
            _drop_link(marker, idir.name)  # likewise: the refusal below would be written through it
            # And the kept video's name: kept_video found no real copy there, so a link or an
            # empty file is all that is left. Dropped here, before the download, not only after
            # it (below): one the share won't clear would otherwise fail the run *with* a
            # finished source.ts beside it, and the next run takes that for a partial and
            # unlinks it before downloading its replacement
            _drop_shadowing(idir, kept_name)
            fmt, referer = ((cfg["archive"]["kick_format"], "https://kick.com/") if it["source"] == "kick"
                            else (cfg["archive"]["hei_audio_format"], hei.SITE + "/"))
            m3u8 = hei.fresh_m3u8(cfg["paths"]["hei_cookies"], it) if it["source"] == "hei" else it["m3u8"]
            # a missing HEI segment fails the item (retried next run) rather than leaving a gap
            whole = ["--abort-on-unavailable-fragments"] if it["source"] == "hei" else []
            # source.ts is only ever this run's: with --no-part a failed run leaves it partial, and
            # yt-dlp would take that as already downloaded
            _drop_partial(src)
            r = ytdl.run("--referer", referer, "-f", fmt, "--hls-use-mpegts", *whole,
                         "--no-part", "-N", "4", "-o", str(src), m3u8)
            if r.returncode or not src.exists():
                _drop_partial(src)
                raise RuntimeError(hei.scrub(f"yt-dlp: {ytdl.last_error(r)}"))
            # yt-dlp stamps source.ts with the VOD's Last-Modified (there is no --no-mtime here),
            # which can be older than an audio.wav a failed earlier run left behind: the mtime rule
            # would then serve that file's audio, and where the two lengths agree the timeline
            # check can't see the difference (#444 review). The marker is the record; one that
            # can't be written stops the run, since a wrong transcript outlives the run.
            _mark_stale_or_refuse(idir, src)
        # #444: a link or an empty file at either name extract_audio's `source.*` glob meets before
        # source.ts is no copy kept here (kept_video and _real_copy say so, and this run downloads),
        # but it would shadow the copy just read: dropped (the link alone, never what it names).
        # After the download, so one planted while it ran is caught too, and unconditionally: a
        # link at source.refused.ts on a non-fresh run would otherwise meet the refusal's rename
        # (which cannot replace a link) and stick the item with both it and its finished source.ts
        _drop_shadowing_or_mark(idir, src, fresh)
        # trust_mtime False: a wav here is only ever a failed run's leftover, and one with no stamp
        # beside it (#448) can't say which download it came from, so it is decoded again
        wav = transcribe.extract_audio(idir, trust_mtime=False)
        # #440: a source whose timestamps jump back would have every time after the jump wrong,
        # so it is refused; a download it refuses is renamed aside as source.refused.ts (the only
        # complete copy) and waits there until that file is rebuilt
        try:
            transcribe.check_audio_timeline(idir)
        except transcribe.TimelineJump as e:
            # #444 review: only a jump is a property of the content, so only it is set aside;
            # a probe that couldn't read a fresh download (or gave no container duration) is a
            # read failure a retry may replace, so it propagates and the next run downloads again.
            # #444: error.txt and the log line carry this exception, so the advice (a `clippa
            # fetch <url> --fixup` command) is kept out of it; `.reason` has the same numbers
            why = e.reason
            if not fresh:  # a kept video, or a source already set aside: unchanged
                # the copy is kept here, so the line says which name it is under (never a path or
                # a url): the aside file when that is the one read, the download when it is marked
                where = "" if _kept else f" (kept as {src.name})"
                raise ClipperError(f"{why}{where}") from None
            try:
                src.replace(refused)  # renamed, not copied: it is the only complete copy
            except OSError as err:  # a share that won't rename it must not hide the guard's line
                reason = err.strerror or "the system refused it"
                try:
                    # only now, with the rename failed, is the marker the record of the refusal:
                    # a rename that worked leaves source.refused.ts as its own record
                    _write_marker(idir, why)
                except ClipperError:
                    # nothing durable can be written here: say what is true, not that it is kept
                    raise ClipperError(
                        f"{why} (the download is still {src.name}: {reason}; it couldn't be set "
                        "aside or marked, so a later run may replace it)") from None
                raise ClipperError(f"{why} (the download is still {src.name}: {reason})") from None
            _clear_marker(idir)  # a marker from an earlier rename must not outlive this one
            raise ClipperError(f"{why} (kept as {refused.name})") from None
        if fresh:
            src.unlink()
        enc = idir / "audio.flac"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "flac",
                        str(enc)], check=True)
        wav.unlink()
        transcribe.forget_stamp(idir)
        out, cost = call(worker.transcribe, enc.read_bytes(), ".flac", settings)
        enc.unlink()
    else:
        media = odysee("get", {"uri": it["url"]})["streaming_url"]
        out, cost = call(worker.transcribe_url, media, settings)
    if not meta.get("duration_s"):  # the listing's probe failed: the audio says how long it is
        meta["duration_s"] = round(float(out["dur"]), 1)
    meta["transcribed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    (idir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    transcribe.write(idir, tc, out["segments"], out["dur"], t0, {"modal": cost})
    if out.get("diarization"):
        transcribe.write_diarization(idir, out["diarization"], cost)
        speakers.apply(idir, item_cfg(cfg, idir))
    (idir / "error.txt").unlink(missing_ok=True)
    _clear_marker(idir)  # the item is through: a stale refusal must not hold it back next run
    return {**cost, "signed_in": signed_in, "duration_s": meta["duration_s"]}


def transcribe_estimate(cfg: dict, subject: str, todo: list[dict], gpu: str, parallel: int) -> str:
    """The dry-run line: hours and price, a per-call overhead included (#228), and how many items
    have no length yet (an Instagram listing has none before the download), which aren't priced."""
    diarize = bool(cfg["archive"]["diarize"])
    known = [it for it in todo if it.get("duration_s")]
    unknown = len(todo) - len(known)
    hours = sum(it["duration_s"] for it in known) / 3600
    over, source = call_overhead_s(cfg, subject, gpu)
    usd = estimate_usd(hours, gpu, diarize, calls=len(known), overhead_s=over)
    head = (f"{subject}: {len(todo)} to transcribe{' with speaker turns' if diarize else ''}, on {gpu} x{parallel}: ")
    each = f"{over:.0f} s a call beyond the audio ({source})"
    if not known:
        return head + (f"lengths unknown until each is downloaded, so no price; at least "
                       f"~${estimate_usd(0, gpu, diarize, calls=unknown, overhead_s=over):.2f} for the calls alone ({each})"
                       if unknown else "nothing")
    return (head + f"{hours:.1f} h, ~${usd:.2f} ({each})"
            + (f"; {unknown} more of unknown length, not priced" if unknown else ""))


def run_transcribe(cfg: dict, subject: str, limit: int | None, oldest: bool, gpu: str,
                   parallel: int, dry_run: bool) -> None:
    d = subject_dir(cfg, subject)
    cat = load_catalog(d)
    if not cat:
        raise ClipperError(f"no catalog for {subject!r}: clippa archive add {subject} <url>")
    # An item with a `dir` is one of our own streams (a `clippa story` item): the pipeline
    # transcribes it there (item_dir), so it is never paid for again here.
    todo = [it for it in cat.values() if not it.get("dir") and not (d / it["id"] / "words.jsonl").exists()]
    links = load_links(d)
    dead = {i for i, lk in links.items() if lk["status"] == "dead"}
    if skipped := sum(1 for it in todo if it["id"] in dead):
        print(f"{subject}: {skipped} skipped, source link dead (clippa archive links {subject})")
        todo = [it for it in todo if it["id"] not in dead]
    if oldest:
        todo.reverse()
    todo = todo[:limit] if limit else todo
    print(transcribe_estimate(cfg, subject, todo, gpu, parallel))
    if dry_run or not todo:
        return
    # Our own log next to the data: a detached run's shell redirect once wrote nothing (2026-10-23).
    fh = logging.FileHandler(d / "transcribe.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(fh)
    try:
        _transcribe_all(cfg, d, subject, todo, gpu, parallel)
    finally:
        log.removeHandler(fh)
        fh.close()


def _transcribe_all(cfg: dict, d: Path, subject: str, todo: list[dict], gpu: str, parallel: int,
                    retry_on: tuple[str, ...] = ytdl.AGE) -> None:
    log.info("%s: started, %d items on %s x%d", subject, len(todo), gpu, parallel)
    spent, done, failed, streak, signed_in = 0.0, 0, 0, 0, 0
    stop_after = cfg["archive"]["stop_after_failures"]
    with ThreadPoolExecutor(parallel) as pool:
        jobs = {pool.submit(transcribe_item, cfg, d / it["id"], it, gpu, retry_on): it for it in todo}
        for job in as_completed(jobs):
            it = jobs[job]
            if job.cancelled():
                continue
            try:
                res = job.result()
                if not it.get("duration_s") and res.get("duration_s"):  # status and estimates read it
                    with jsonstate.update(d / "catalog.json", {}, indent=1) as cat:
                        if it["id"] in cat:
                            cat[it["id"]]["duration_s"] = res["duration_s"]
                spent += res["est_usd"]
                signed_in += res["signed_in"]
                done += 1
                streak = 0
            except Exception as e:  # noqa: BLE001
                failed += 1
                streak += 1
                (d / it["id"]).mkdir(parents=True, exist_ok=True)
                (d / it["id"] / "error.txt").write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
                log.warning("%s failed: %s: %s", it["id"], type(e).__name__, str(e)[:200])
            log.info("[%d/%d] %s %r  (spent ~$%.2f)", done + failed, len(todo), it["id"],
                     it["title"][:50], spent)
            if stop_after and streak >= stop_after:
                # e.g. YouTube's bot check on this IP: stop before it also blocks pre-show grabs
                log.warning("%d failures in a row: stopping (rerun to resume)", streak)
                pool.shutdown(wait=True, cancel_futures=True)
                break
    log.info("%s: %d transcribed (%d signed in), %d failed, ~$%.2f",
             subject, done, signed_in, failed, spent)


# ---------- keeping videos ----------

VIDEO_SOURCES = ("youtube", "hei")


def _drop_link(path: Path, label: str) -> None:
    """Remove a link (a symlink, or on Windows a junction) sitting where this run must have a real
    file, saying so in one line. Only the link goes, never what it names: a junction is a folder
    to `unlink`, so `remove_link` rmdirs it. One that can't be removed is a one-line error
    naming it and the system's reason, so nothing is downloaded over it (no path: label is the
    stream). A share that won't say whether the name is a link is one line too: a drop that
    guessed would either act on what it names or download behind a name nobody could read."""
    try:
        linked = is_link(path)
    except OSError as err:
        raise ClipperError(f"{label}: couldn't tell whether {path.name} is a link "
                           f"({err.strerror or 'the system refused it'})") from None
    if not linked:
        return
    try:
        remove_link(path)
    except OSError as err:
        raise ClipperError(f"{label}: couldn't remove the link at {path.name} "
                           f"({err.strerror or 'the system refused it'})") from None
    print(f"{label}: removed a link at {path.name}", flush=True)


def _drop_partial(src: Path) -> None:
    """A download's file and yt-dlp's resume note for it, left by a run that failed."""
    src.unlink(missing_ok=True)
    src.with_name(src.name + ".ytdl").unlink(missing_ok=True)


def _write_marker(idir: Path, why: str) -> None:
    """Record in the item's folder that the source at source.ts is a refusal kept here (or a
    finished download this run could not get out of the way): the marker item_source reads so the
    next run reuses it rather than taking it for a partial and unlinking it. A link at the name is
    dropped first, never written through: the download is the long part, so one planted since the
    folder was last checked would otherwise take the write outside it. "x" refuses a file already
    there (a stale marker, or what a journal made of the name) rather than appending to it. One
    that can't be written is a one-line error naming the stream, so the caller says what is true
    instead."""
    marker = idir / ".refused"
    try:
        _drop_link(marker, idir.name)
        with open(marker, "x", encoding="utf-8") as f:
            f.write(f"{why}\n")
    except OSError as err:
        raise ClipperError(f"{idir.name}: couldn't write the refusal marker (.refused) "
                           f"({err.strerror or 'the system refused it'})") from None


def _clear_marker(idir: Path) -> None:
    """#444: the refusal marker goes once it stops standing for anything (the rename happened, the
    source is now in order, or there is no source left to refuse): left there, the next run would
    treat the folder as refused and find no source at all. Only the marker goes, never what a link
    at that name points at, and a share that won't take it away is one printed line rather than a
    failure of a finished item."""
    marker = idir / ".refused"
    try:
        if is_link(marker):
            remove_link(marker)
        else:
            marker.unlink(missing_ok=True)
    except OSError as err:
        print(f"{idir.name}: couldn't remove the refusal marker (.refused): "
              f"{err.strerror or 'the system refused it'}", flush=True)


def _unreadable(path: Path) -> bool:
    """Whether the share won't say whether `path` is there: an lstat that fails for any reason but
    the file being absent. `_real_copy` reads a failed stat as absent, which is right for a name
    nothing is under and wrong for a share that won't answer: a marker beside an unstattable
    source.ts is no marker standing for nothing, and that file may be the only complete copy."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return False


def _real_copy(path: Path) -> bool:
    """Whether `path` is a copy kept here — the kept video (source.mp4) or a source the timeline
    refused (source.refused.ts): a regular, non-link file that has something in it. A zero-byte one
    is what a failure leaves before a download's first byte, and no run can read it (the timeline
    check has nothing to compare and extract_audio gets no audio), so it counts as absent. A stat
    that fails (a share that won't answer) counts as absent too, whichever test of it fails: on
    Python 3.12 `is_symlink` and `is_file` re-raise a refused stat rather than answer it, so each
    sits in the same try as the size."""
    try:
        return not is_link(path) and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _there(path: Path) -> bool:
    """Whether the name is there — or the share wouldn't say, which counts as there: an unstattable
    name is never taken for one nothing is under (a marker a share won't answer for is still a
    marker, and dropping it can lose the only copy left). `exists` re-raises a refused stat on
    Python 3.12, as `is_file` and `is_symlink` do: only the errnos it reads as absence are one."""
    try:
        return path.exists()
    except OSError:
        return True


def _link(path: Path) -> bool:
    """`is_link` for a caller testing a name: a share that won't stat it is answered False, since
    an unreadable name is no link to drop. `_drop_link` keeps the strict test: there a name it
    can't tell about must stop the run before anything is written over it."""
    try:
        return is_link(path)
    except OSError:
        return False


def _drop_empty(path: Path, label: str) -> None:
    """Remove an empty file left at a name this run must have a real copy under, saying so in one
    line. source.refused.ts sorts before source.ts in extract_audio's `source.*` glob, so an empty
    one there would be read in place of the download and the item would never advance. Only an
    empty regular file goes, never a link or what it names, and one that can't be removed is a
    one-line error naming it, so nothing is downloaded to sit behind it. A share that won't say
    what the name is is one line as well: left there, it would be read instead of the download."""
    try:
        if is_link(path) or not path.is_file():
            return
    except OSError as err:
        raise ClipperError(f"{label}: couldn't tell what {path.name} is "
                           f"({err.strerror or 'the system refused it'})") from None
    try:
        if path.stat().st_size:
            return
    except OSError:
        return
    try:
        path.unlink()
    except OSError as err:
        raise ClipperError(f"{label}: couldn't remove the empty {path.name} "
                           f"({err.strerror or 'the system refused it'})") from None
    print(f"{label}: removed an empty {path.name}", flush=True)


def _drop_shadowing(idir: Path, *names: Path) -> None:
    """A link or an empty file at any of `names` would be the source extract_audio reads: each of
    them sorts before source.ts in its `source.*` glob, and a link there is refused as one. Each
    goes (the link alone, never what it names), so a name nobody can read can't stall the item."""
    for name in names:
        _drop_link(name, idir.name)
        _drop_empty(name, idir.name)


def _drop_shadowing_or_mark(idir: Path, src: Path, fresh: bool) -> None:
    """`_drop_shadowing` after the download, recording a download it could not get out of the way.

    Dropped after a long download, it can fail with a finished source.ts already in the folder (a
    name planted while the download ran). Raised alone, the next run sees no kept video, no aside
    file and no marker, so item_source says fresh and _drop_partial unlinks that download before
    fetching another (a Kick m3u8 is never refreshed, so a re-download that then fails leaves
    nothing at all). Where this run made the source, the marker makes it the copy it left: the
    next run reuses it as a source, the timeline guard decides what it is, and a good one
    transcribes and clears the marker. A copy already kept here (not fresh) is nothing this run
    made, so its error is the run's to report as it is."""
    try:
        _drop_shadowing(idir, idir / "source.mp4", idir / "source.refused.ts")
    except ClipperError as err:
        if not fresh:  # the copy is already kept here: nothing this run made is at risk
            raise
        try:
            _write_marker(idir, str(err))
        except ClipperError:
            # nothing durable can be written either: say what is true, not that it is kept
            raise ClipperError(
                f"{err} (the download is still {src.name}: it couldn't be marked, so a later run "
                "may replace it)") from None
        raise


def _mark_stale_or_refuse(idir: Path, src: Path) -> None:
    """`transcribe.mark_stale` after a download, recording a download whose stale mark it could not
    write.

    Raised alone, the stale mark's error leaves the finished source.ts with nothing said about it:
    the next run sees no kept video, no aside file and no marker, takes it for a partial and
    unlinks it before fetching another (a Kick m3u8 is never refreshed, so a re-download that then
    fails leaves nothing at all). The refusal marker makes the download the copy this run left: the
    next run reuses it as a source, the timeline guard decides what it is, and a good one
    transcribes and clears the marker."""
    try:
        transcribe.mark_stale(idir)
    except ClipperError as err:
        try:
            _write_marker(idir, str(err))
        except ClipperError:
            # nothing durable can be written either: say what is true, not that it is kept
            raise ClipperError(
                f"{err} (the download is still {src.name}: it couldn't be marked, so a later run "
                "may replace it)") from None
        raise


def kept_video(idir: Path) -> Path | None:
    """The video `archive video` kept for an item, if any (written under another name and
    renamed when complete, so this is never a partial download). Only a real copy counts: a link
    at that name is not one kept here, nor can it be read (extract_audio refuses a linked source),
    and a zero-byte or unstattable one has no video in it, so each is absent and the item
    downloads rather than stalling on a name that will never advance."""
    p = idir / "source.mp4"
    return p if _real_copy(p) else None


def item_source(idir: Path) -> tuple[Path | None, Path, bool]:
    """(the kept video, the source a kick/hei item reads, whether this run downloads it).

    A source the timeline check refused is renamed aside and reused, never re-downloaded over and
    never dropped: it is the only complete copy (a Kick VOD's m3u8 is never refreshed, so a
    download that fails after leaving nothing leaves nothing at all). The name matters three ways:
    source.* matches media.source_file's glob, so extract_audio reads it; _drop_partial unlinks
    only source.ts, so it is never taken for a partial; and it sorts before source.ts (and after a
    kept source.mp4), so a leftover can't be bypassed and a kept video still wins. Reused rather
    than re-fetched on purpose: an HEI download uses --abort-on-unavailable-fragments, so a file
    that completed is the VOD's own content, and a deterministic jump would otherwise cost a full
    re-download every run. The copy that is here is the one read: source.refused.ts before
    source.ts.

    Where the share refuses that rename the refusal is marked in a dotfile as well, so the next
    run doesn't take the complete download for a partial and unlink it. The marker counts only
    while the download it stands for is still here: with no source.ts (deleted, say) there is
    nothing to refuse, and trusting the marker alone would serve a stale audio.wav and transcribe
    the very times the guard keeps out. A link under any of these names (a symlink, or on Windows
    a junction, which is_symlink misses) is not a copy or a marker kept here, nor one the rename
    writes through: it is treated as absent, so the download path runs (and removes it there —
    source.mp4 too, since a linked one would shadow what that download leaves). An empty file is no
    copy either: an empty source.refused.ts or source.mp4 would otherwise be read in place of
    the download, and an empty source.ts would hold a marker that stands for nothing, so each is
    treated as absent and the empty one is removed.

    #444 review: a share that won't stat a name is neither absent nor a copy, so it is told apart
    from one nothing is under (_unreadable). A marker beside such a source.ts is not cleared: the
    file may be the only complete copy, and clearing it lets _drop_partial unlink it before a
    download that may fail, leaving neither the copy nor the refusal. The same holds for the
    marker itself: one the share won't stat is still a marker (_there), never an absent one, and a
    stat that fails on the kept video, a source or a link test reads as the absence of the thing
    tested (_real_copy, _link) rather than stopping the run before the decision is made."""
    kept = kept_video(idir)
    refused = idir / "source.refused.ts"
    marker = idir / ".refused"
    downloaded = idir / "source.ts"
    have_download = _real_copy(downloaded)
    have_aside = _real_copy(refused)
    if not have_aside and not kept:
        _drop_empty(refused, idir.name)  # left there, it shadows this run's download
    marked = _there(marker) and not _link(marker)
    unreadable = _unreadable(downloaded)  # the share won't say: not the same as gone
    if marked and not have_aside and not have_download and not unreadable:
        # the source it stood for is gone: dropped, the item downloads a replacement as if it had
        # never been refused (and one that stays is still not trusted, `marked` goes false anyway)
        print(f"{idir.name}: removed the refusal marker, no source left to refuse", flush=True)
        _clear_marker(idir)
        marked = False
    refused_here = have_aside or (marked and (have_download or unreadable))
    return kept, kept or (refused if have_aside else downloaded), not kept and not refused_here


def keep_video(cfg: dict, idir: Path, it: dict) -> Path:
    """An item's best video as source.mp4 in idir, its caption tracks as captions.<lang>.<ext>
    (yt-dlp puts the language in each subtitle's name). Only the tracks someone made: YouTube's
    automatic ones come in every language it translates to, and `transcribe` makes our own."""
    idir.mkdir(parents=True, exist_ok=True)
    # merged or single-file, always an mp4; a missing segment fails it (fetched again next run)
    args = ["-f", cfg["archive"]["video_format"], "--merge-output-format", "mp4", "--remux-video", "mp4",
            "--abort-on-unavailable-fragments", "--no-playlist", "-N", "4", "--write-subs", "--sub-langs", "all,-live_chat",
            "-o", str(idir / "download.%(ext)s"), "-o", f"subtitle:{idir / 'captions'}.%(ext)s"]
    if it["source"] == "hei":
        r = ytdl.run("--referer", hei.SITE + "/", *args, hei.fresh_m3u8(cfg["paths"]["hei_cookies"], it))
    else:
        r, _ = ytdl.run_retrying(cfg, *args, it["url"], retry_on=ytdl.AGE)
    out = idir / "download.mp4"
    if r.returncode or not out.is_file():
        raise RuntimeError(hei.scrub(f"yt-dlp: {ytdl.last_error(r)}"))
    out.replace(idir / "source.mp4")
    for leftover in idir.glob("download.*"):
        leftover.unlink(missing_ok=True)
    (idir / "video_error.txt").unlink(missing_ok=True)
    return idir / "source.mp4"


def videos_todo(d: Path, cat: dict[str, dict], ids: list[str] | None = None) -> list[dict]:
    """Catalog items whose video can be kept and isn't yet, minus dead links (catalog order)."""
    dead = {i for i, lk in load_links(d).items() if lk["status"] == "dead"}
    return [it for it in cat.values()
            if it["source"] in VIDEO_SOURCES and not kept_video(d / it["id"]) and it["id"] not in dead
            and (ids is None or it["id"] in ids)]


def run_videos(cfg: dict, subject: str, ids: list[str] | None, limit: int | None, oldest: bool,
               parallel: int, dry_run: bool) -> None:
    d = subject_dir(cfg, subject)
    cat = load_catalog(d)
    if not cat:
        raise ClipperError(f"no catalog for {subject!r}: clippa archive add {subject} <url>")
    todo = videos_todo(d, cat, ids)  # catalog order: newest first (`add` puts new items ahead)
    if oldest:
        todo.reverse()
    todo = todo[:limit] if limit else todo
    hours = sum(it["duration_s"] for it in todo) / 3600
    print(f"{subject}: {len(todo)} videos to keep, {hours:.1f} h, "
          f"~{hours * cfg['archive']['video_gb_per_hour']:.0f} GB, {parallel} at once (no model cost)")
    if dry_run or not todo:
        return
    fh = logging.FileHandler(d / "video.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(fh)
    done = failed = 0
    try:
        with ThreadPoolExecutor(parallel) as pool:
            jobs = {pool.submit(keep_video, cfg, d / it["id"], it): it for it in todo}
            for job in as_completed(jobs):
                it = jobs[job]
                try:
                    size = job.result().stat().st_size
                    done += 1
                    log.info("[%d/%d] %s %r  %.2f GB", done + failed, len(todo), it["id"], it["title"][:50],
                             size / 1e9)
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    (d / it["id"]).mkdir(parents=True, exist_ok=True)
                    (d / it["id"] / "video_error.txt").write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
                    log.warning("[%d/%d] %s failed: %s", done + failed, len(todo), it["id"], str(e)[:200])
        log.info("%s: %d videos kept, %d failed", subject, done, failed)
    finally:
        log.removeHandler(fh)
        fh.close()


# ---------- speakers ----------

def relabel(cfg: dict, subject: str) -> None:
    """Name every diarized item's speakers again against the subject's enrolled voices (after
    `clippa speakers <item folder> --name SPEAKER_00=Name --enroll`), and say who talks how much."""
    d = subject_dir(cfg, subject)
    items = sorted(p.parent for p in d.glob("*/diarization.json"))
    if not items:
        raise ClipperError(f"no diarized items in {subject}: clippa archive transcribe {subject} --diarize")
    talk: dict[str, float] = {}
    seen: dict[str, set] = {}  # the items each name speaks in (one item can split a voice in two)
    unnamed = 0.0
    for idir in items:
        for e in speakers.apply(idir, item_cfg(cfg, idir)).values():
            if name := e.get("name"):
                talk[name] = talk.get(name, 0.0) + e["talk_s"]
                seen.setdefault(name, set()).add(idir.name)
            else:
                unnamed += e["talk_s"]
    print(f"{subject}: {len(items)} items named again")
    for name, s in sorted(talk.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<28} {s / 3600:6.1f} h in {len(seen[name])} items")
    print(f"  {'(not matched)':<28} {unnamed / 3600:6.1f} h")


# ---------- posts ----------

def sheet_csv_url(url: str) -> str:
    """A Google Sheet's CSV export url (the tab in the link's gid, else the first tab)."""
    m = re.search(r"/spreadsheets/d/([\w-]+)", url)
    if not m:
        raise ClipperError(f"{url}: not a Google Sheets link")
    gid = re.search(r"[#&?]gid=(\d+)", url)
    return f"https://docs.google.com/spreadsheets/d/{m[1]}/export?format=csv" + (f"&gid={gid[1]}" if gid else "")


def save_posts(cfg: dict, subject: str, url: str) -> None:
    """A public sheet of the subject's posts (On Cinema's: every tweet and Bluesky post by Tim,
    Gregg and the show, kept by the fans) as posts.csv as exported and posts.json, one row per
    post keyed by the sheet's header. Rerun to refresh: the sheet is kept up to date."""
    r = requests.get(sheet_csv_url(url), timeout=120)
    r.raise_for_status()
    text = r.content.decode("utf-8-sig")
    if "csv" not in r.headers.get("content-type", "") or text.lstrip().startswith("<"):
        # a sign-in page or Drive's interstitial: keep the posts saved last time
        raise ClipperError(f"{url}: Google sent a page, not CSV (is the sheet still shared publicly?)")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ClipperError(f"{url}: no rows (is the sheet shared publicly?)")
    d = subject_dir(cfg, subject)
    d.mkdir(parents=True, exist_ok=True)
    jsonstate.write_text(d / "posts.csv", text)
    jsonstate.write(d / "posts.json", {"source": url, "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                       "posts": rows}, indent=1)
    first = next(iter(rows[0]))
    by = {}
    for row in rows:
        by[row[first]] = by.get(row[first], 0) + 1
    top = ", ".join(f"{k or '(blank)'} {n}" for k, n in sorted(by.items(), key=lambda kv: -kv[1])[:6])
    print(f"{subject}: {len(rows)} posts saved to {d / 'posts.json'} ({top})")


def status(cfg: dict, subject: str | None) -> None:
    root = cfg["paths"]["archive"]
    subjects = [subject] if subject else sorted(p.name for p in root.glob("*") if (p / "catalog.json").exists())
    for s in subjects:
        d = subject_dir(cfg, s)
        cat = load_catalog(d)
        done = [it for it in cat.values() if (item_dir(d, it) / "words.jsonl").exists()]
        errors = [it for it in cat.values() if (d / it["id"] / "error.txt").exists()]
        cost = 0.0
        for it in done:  # the cost record heads the file: skip reading the segments
            cost += (head_record(item_dir(d, it) / "transcript.json") or {}).get("est_usd", 0.0)
        hours = sum(i["duration_s"] for i in done) / 3600
        total = sum(i["duration_s"] for i in cat.values()) / 3600
        print(f"{s}: {len(done)}/{len(cat)} transcribed ({hours:.0f}/{total:.0f} h), "
              f"{len(errors)} errors, ~${cost:.2f} spent")
        links = load_links(d)
        if links:
            n = {k: sum(1 for i in cat if links.get(i, {}).get("status") == k)
                 for k in ("alive", "restricted", "dead")}
            kept = sum(1 for i, it in cat.items() if links.get(i, {}).get("status") == "dead"
                       and (item_dir(d, it) / "words.jsonl").exists())
            print(f"  links: {n['alive']} alive, {n['restricted']} restricted, {n['dead']} dead "
                  f"({kept} of them transcribed), {len(cat) - sum(n.values())} unchecked")


# ---------- link checks ----------

def check_youtube(url: str, session: requests.Session) -> tuple[str, str]:
    """(status, reason) from YouTube's oEmbed endpoint; unknown when it can't be asked."""
    reason = ""
    for attempt in range(3):
        try:
            r = session.get(YT_OEMBED, params={"url": url, "format": "json"}, timeout=30)
        except requests.RequestException as e:
            reason = type(e).__name__
        else:
            if r.status_code == 200:
                return "alive", ""
            if r.status_code in (401, 403):  # private, or embedding turned off (still plays on YouTube)
                return "restricted", f"http {r.status_code}: private or embedding off"
            if r.status_code in (400, 404, 410):  # deleted, removed, or the channel terminated
                return "dead", f"http {r.status_code}"
            reason = f"http {r.status_code}"  # 429 and 5xx: back off and retry
        time.sleep(5 * 2 ** attempt)
    return "unknown", reason


def check_odysee(urls: list[str]) -> dict[str, tuple[str, str]]:
    try:
        res = odysee("resolve", {"urls": urls})
    except (requests.RequestException, KeyError, ValueError) as e:
        return {u: ("unknown", type(e).__name__) for u in urls}
    out = {}
    for u in urls:
        c = res.get(u)
        err = (c or {}).get("error")
        if c is None:
            out[u] = ("unknown", "not in resolve result")
        elif not err:
            out[u] = ("alive", "")
        elif err.get("name") == "NOT_FOUND":
            out[u] = ("dead", "NOT_FOUND")
        else:  # e.g. BLOCKED (filtered by Odysee)
            out[u] = ("restricted", err.get("name") or "error")
    return out


def check_links(cfg: dict, subject: str, ids: list[str] | None, everything: bool,  # noqa: C901
                limit: int | None) -> None:
    d = subject_dir(cfg, subject)
    cat = load_catalog(d)
    if not cat:
        raise ClipperError(f"no catalog for {subject!r}: clippa archive add {subject} <url>")
    ac = cfg["archive"]
    links = load_links(d)
    if ids:
        missing = [i for i in ids if i not in cat]
        if missing:
            raise ClipperError(f"not in {subject}'s catalog: {' '.join(missing)}")
        todo = [cat[i] for i in ids]
    else:
        cutoff = datetime.now(UTC) - timedelta(days=ac["link_recheck_days"])
        todo = [it for it in cat.values() if everything or it["id"] not in links
                or datetime.fromisoformat(links[it["id"]]["checked_at"]) < cutoff]
    todo = todo[:limit] if limit else todo
    print(f"{subject}: checking {len(todo)} links")
    counts = {"alive": 0, "restricted": 0, "dead": 0, "unknown": 0}
    changed: list[tuple[dict, str, str]] = []
    streak = 0
    stop_after = ac["stop_after_failures"]

    def record(it: dict, status: str, reason: str) -> None:
        nonlocal streak
        counts[status] += 1
        if status == "unknown":  # keep the last result; it is checked again next time
            streak += 1
            log.warning("%s: could not check (%s)", it["id"], reason)
            return
        streak = 0
        now = datetime.now(UTC).isoformat(timespec="seconds")
        old = links.get(it["id"], {})
        if old and old["status"] != status:
            changed.append((it, old["status"], status))
        links[it["id"]] = {"status": status, "reason": reason, "checked_at": now,
                           "since": old["since"] if old.get("status") == status else now}

    def progress() -> None:
        n = sum(counts.values())
        if n % 200 == 0 or n == len(todo):
            save_json(d, "links.json", links)
            print(f"[{n}/{len(todo)}] {counts}", flush=True)

    od = [it for it in todo if it["source"] == "odysee"]
    for i in range(0, len(od), 50):
        batch = od[i:i + 50]
        res = check_odysee([it["url"] for it in batch])
        for it in batch:
            record(it, *res[it["url"]])
            progress()

    yt = [it for it in todo if it["source"] == "youtube"]
    local = threading.local()

    def one(it: dict) -> tuple[str, str]:
        if not hasattr(local, "session"):
            local.session = requests.Session()
        return check_youtube(it["url"], local.session)

    with ThreadPoolExecutor(ac["link_parallel"]) as pool:
        jobs = {pool.submit(one, it): it for it in yt}
        for job in as_completed(jobs):
            if job.cancelled():
                continue
            record(jobs[job], *job.result())
            progress()
            if stop_after and streak >= stop_after:
                # YouTube refusing this IP: stop before it also blocks pre-show grabs
                log.warning("%d links in a row could not be checked: stopping (rerun to resume)", streak)
                pool.shutdown(wait=True, cancel_futures=True)
                break
    save_json(d, "links.json", links)
    print(f"{subject}: {counts['alive']} alive, {counts['restricted']} restricted, "
          f"{counts['dead']} dead, {counts['unknown']} could not be checked")
    for it, was, now in changed:
        kept = " (transcript kept)" if (item_dir(d, it) / "words.jsonl").exists() else ""
        print(f"  {was} -> {now}{kept}: {it['id']} {it['title'][:60]!r} {it['url']}")
    if ids:
        for it in todo:
            lk = links.get(it["id"], {"status": "unknown", "reason": "could not be checked"})
            print(f"  {lk['status']:<10} {it['id']} {it['title'][:60]!r} {it['url']} {lk['reason']}")
    elif counts["dead"] + counts["restricted"]:
        print(f"list them: clippa archive links {subject}")


def list_links(cfg: dict, subject: str, status: str | None) -> None:
    """Items by link status, most recent change first; default every link that isn't alive."""
    d = subject_dir(cfg, subject)
    cat, links = load_catalog(d), load_links(d)
    if not links:
        raise ClipperError(f"no link checks for {subject!r} yet: clippa archive check {subject}")
    rows = [(i, lk) for i, lk in links.items()
            if (lk["status"] != "alive" if status is None else status in ("all", lk["status"]))]
    for i, lk in sorted(rows, key=lambda r: r[1]["since"], reverse=True):
        it = cat.get(i, {})
        kept = "transcribed" if (item_dir(d, it) if it else d / i).joinpath("words.jsonl").exists() else ""
        print(f"{lk['status']:<10} since {lk['since'][:10]}  {kept:<11}  {i}  "
              f"{it.get('title', '')[:60]!r}  {it.get('url', '')}  {lk['reason']}")
    print(f"{len(rows)} items")
