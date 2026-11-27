"""Discord review cards: a thread (or forum post) per stream in [discord]
review_channel_id, and one message per item with a small preview, its title, hook and
Kitsu link, plus buttons that `clippa bot` answers (Approve, Reject, Changes, Hold,
Reopen, Pin, Retitle, Thumbnail text, and Re-split on its own row when a segment is over
[segments] split_over_s). The YouTube thumbnail (the .jpg beside the video)
is attached next to the preview when there is one. Every button writes to Kitsu (clippa/actions.py), so Kitsu stays
the record.

Posting uses Discord's REST API with the bot token, so `clippa review publish` posts
without the bot running; the buttons only respond while `clippa bot` is up.

Previews are re-rendered for Discord's upload limit (max_upload_mb, 10 MB unless the
server is boosted): 480p, bitrate sized to fit. Items too long to fit at min_video_kbps
(most segments) get their first excerpt_s seconds, and the card says the full preview is
in Kitsu. Posted message ids live in <stream>/discord.json, so reruns only post items
with a new Kitsu revision.

Token: $DISCORD_BOT_TOKEN ([discord] token_env), else the file at paths.discord_token.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import requests

from . import jsonstate, media
from .config import for_stream
from .errors import ClipperError
from .timefmt import length

API = "https://discord.com/api/v10"
# Dark Night Watch tokens (brand/tokens/nightwatch.json). Discord gets one side color.
# --nw-approve is the same hex as the logo mark, so a card never uses that color alone:
# the title always carries an emoji and a word.
COLORS = {"pending": 0x6B8FD4, "changes_requested": 0xD4A054, "approved": 0x5EA88A,
          "rejected": 0xD07070, "held": 0xD4A054, "finding": 0xD07070, "resplit": 0xA992D4,
          "accent": 0x7EB8C9}
# (emoji, word) for the title prefix "{emoji} {word} · ".
MARK = {"held": ("⏸", "Held"), "finding": ("⚠", "Finding"), "auto": ("⏱", "Auto-approve"),
        "approved": ("✓", "Approved"), "auto_approved": ("✓", "Auto-approved"),
        "resplit": ("✂", "Re-split"), "rejected": ("✕", "Rejected"),
        "changes_requested": ("✎", "Changes"), "pending": ("⏳", "Pending"),
        "watch": ("📡", "On watch"), "review": ("📋", "In review"),
        "cost": ("💸", "Cost"), "error": ("❌", "Error")}
COLOR_OF = {"held": "held", "finding": "finding", "auto": "pending", "approved": "approved",
            "auto_approved": "approved", "resplit": "resplit", "rejected": "rejected",
            "changes_requested": "changes_requested", "pending": "pending"}
KIND_LABEL = {"segment": "Segment", "pick": "Clip", "compilation": "Compilation",
              "short": "Short"}
TAGLINE = "Clippa stays up. You stay in charge."
# (action, label, style): 1 primary, 2 secondary, 3 success, 4 danger. "…" opens a form.
# Row 2 is full once the link buttons are added. Re-split is a third row, and only on a
# message the bot sends (a webhook post is content only and cannot carry a button).
BUTTONS = [[("approve", "Approve", 3), ("reject", "Reject…", 4), ("changes", "Changes…", 2),
            ("hold", "Hold / unhold", 2), ("reopen", "Reopen", 2)],
           [("pin", "Pin time…", 2), ("retitle", "Retitle…", 2),
            ("thumbtext", "Thumbnail text…", 2)]]
MEDIA = {".mp4": "video/mp4", ".jpg": "image/jpeg"}
MAX_CONTENT = 2000  # characters in a Discord message
ISSUE_CLAIM_S = 30 * 60  # a `weekly --discord` run that died holding its issue lets go after this


def token(cfg: dict) -> str:
    dc = cfg["discord"]
    tok = os.environ.get(dc["token_env"], "").strip()
    path: Path = cfg["paths"]["discord_token"]
    if not tok and path.exists():
        tok = path.read_text(encoding="utf-8-sig").strip()
    if not tok:
        raise ClipperError(f"no Discord bot token: set ${dc['token_env']} or put it in {path}")
    if tok.count(".") != 2:
        raise ClipperError("that isn't a bot token (Developer Portal > your app > Bot > Reset "
                         "Token; the OAuth2 client secret won't work)")
    return tok


def user_ids(dc: dict) -> set[int]:
    ids = dc["allowed_user_ids"]
    if isinstance(ids, str):  # from CLIPPER_DISCORD_ALLOWED_USER_IDS="1,2"
        ids = ids.replace(",", " ").split()
    return {int(i) for i in ids}


class Missing(ClipperError):
    """Discord says the channel, thread or message isn't there (404): deleted, safe to remake.
    Any other failure may be passing, so the ids are kept."""


class Rest:
    def __init__(self, cfg: dict):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bot {token(cfg)}",
                               "User-Agent": "DiscordBot (Clippa, 0.1)"})

    def __call__(self, method: str, path: str, payload: dict | None = None,
                 files: list[Path] | None = None) -> dict:
        """files: uploaded as files[0..n], matching payload attachments ids 0..n."""
        for _ in range(5):
            if files:
                with contextlib.ExitStack() as stack:
                    parts = {f"files[{i}]": (p.name, stack.enter_context(open(p, "rb")),
                                             MEDIA.get(p.suffix.lower(), "application/octet-stream"))
                             for i, p in enumerate(files)}
                    r = self.s.request(method, API + path,
                                       data={"payload_json": json.dumps(payload)},
                                       files=parts, timeout=300)
            else:
                r = self.s.request(method, API + path, json=payload, timeout=60)
            if r.status_code == 429:
                time.sleep(float(r.json().get("retry_after", 1)) + 0.2)
                continue
            if r.status_code == 404:
                raise Missing(f"Discord {method} {path}: 404 {r.text[:300]}")
            if r.status_code >= 400:
                raise ClipperError(f"Discord {method} {path}: {r.status_code} {r.text[:300]}")
            return r.json() if r.content else {}
        raise ClipperError(f"Discord {method} {path}: still rate limited")


# ---------- cards ----------

def cut_label(short: str) -> str:
    """'kick-1/c04' or 's06' as 'Cut 04'. The tail's digits are the cut number."""
    tail = (short or "").rsplit("/", 1)[-1]
    digits = "".join(ch for ch in tail if ch.isdigit())
    return f"Cut {int(digits):02d}" if digits else (tail or "Cut")


def over_phrase(limit_s: float) -> str:
    """[segments] split_over_s as a sentence uses it: '50 minutes', or seconds."""
    limit_s = float(limit_s or 0)
    if limit_s and limit_s % 60 == 0:
        n = int(limit_s // 60)
        return f"{n} minute" if n == 1 else f"{n} minutes"
    n = int(round(limit_s))
    return f"{n} second" if n == 1 else f"{n} seconds"


def footer_text(short: str = "") -> str:
    """'clippa · {short} · Clippa stays up. You stay in charge.' when it fits Discord's footer."""
    base = f"clippa · {short}" if short else "clippa"
    full = f"{base} · {TAGLINE}"
    return full if len(full) <= 2048 else base[:2048]


def is_resplit(kind: str, key: str, duration, limit: float, flagged: set[str]) -> bool:
    """A segment card is a re-split when the setting is on and segments.json flagged its id,
    or its duration is over that limit. 0 (the default) is off."""
    if kind != "segment" or not limit or limit <= 0:
        return False
    sid = (key or "").split("_", 1)[0]
    if sid and sid in flagged:
        return True
    try:
        return float(duration) > limit
    except (TypeError, ValueError):
        return False


def unix_of(iso: str | None) -> int | None:
    """An ISO timestamp as Discord's <t:UNIX:R> seconds, or None."""
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return int(t.timestamp())


def card_status(r: dict) -> str:
    """Which Night Watch status the card leads with. Hold wins, then the decision, then a
    finding, a re-split flag, and an auto-approve that is still waiting."""
    if r.get("hold"):
        return "held"
    decision = r.get("decision") or "pending"
    if decision in ("rejected", "changes_requested"):
        return decision
    if decision == "approved":
        auto = r.get("auto") or ""
        if r.get("auto_state") == "approved" or "approved itself" in auto or "approved automatically" in auto:
            return "auto_approved"
        return "approved"
    if r.get("checks"):
        return "finding"
    if r.get("resplit"):
        return "resplit"
    if will_auto_approve(r):
        return "auto"
    return "pending"


def will_auto_approve(r: dict) -> bool:
    """The item approves itself unless someone acts: review_auto's "waiting" or "due". Not
    "person" (it waits for you, and review_auto.note still gives it a line) nor "off". A row
    from before auto_state was kept goes by its note's words."""
    state = r.get("auto_state")
    if state:
        return state in ("waiting", "due")
    return (r.get("decision") or "pending") == "pending" and "approves itself" in (r.get("auto") or "")


def status_line(r: dict) -> str:
    """decision_text, plus findings and auto-approve when those apply (the side color is not enough)."""
    from .actions import decision_text

    out = decision_text(r)
    n = len(r.get("checks") or [])
    if n:
        out += ", 1 finding" if n == 1 else f", {n} findings"
    state = r.get("auto_state") or ""
    if state == "approved" or card_status(r) == "auto_approved":
        if "auto-approved" not in out:
            out += ", auto-approved"
    elif will_auto_approve(r):
        if "auto-approve" not in out and "auto-approved" not in out:
            out += ", auto-approve"
    return out


def lead_line(r: dict, now: float | None = None) -> str:
    """The VOICE.md Discord line for this card, or '' when that moment has none."""
    status = card_status(r)
    cut = cut_label(r.get("short") or "")
    if status == "held":
        return _hold_lead(r, cut)
    if status == "auto_approved":
        return f"{cut} auto-approved. No objection in the grace window."
    if status == "approved":
        return f"{cut} approved."
    if status == "finding":
        text = str((r.get("checks") or [""])[0]).rstrip(".")
        return f"Finding on {cut}: {text}."
    if status == "resplit":
        return f"{cut} is over {over_phrase(r.get('resplit_limit_s') or 0)}. Flagged for re-split."
    if status == "auto":
        return _auto_lead(r, cut, now)
    return ""


def _hold_lead(r: dict, cut: str) -> str:
    n = len(r.get("checks") or [])
    if r.get("other_voices"):
        return f"{cut} held for other voices. It stays off publish until someone releases it."
    if n:
        kind = KIND_LABEL.get(r.get("kind"), "item")
        word = "finding" if n == 1 else "findings"
        return f"{cut} held. {n} {word} on this {kind}."
    return f"{cut} held."


def _auto_lead(r: dict, cut: str, now: float | None) -> str:
    unix = unix_of(r.get("auto_at"))
    now = time.time() if now is None else now
    if r.get("auto_state") == "due" or (unix is not None and unix <= now):
        return f"{cut} auto-approves at the next pass unless held."
    if unix is not None:
        stamp = f"<t:{unix}:R>"
        if unix - now >= 3600:
            return f"No objection yet. {cut} auto-approves {stamp} unless held."
        return f"{cut} auto-approves {stamp} unless held."
    return r.get("auto") or ""


def auto_field(r: dict, now: float | None = None) -> str:
    """The Auto-approve field. A waiting item uses Discord's relative timestamp so the
    countdown stays current without a card edit every minute."""
    state = r.get("auto_state") or ""
    if state == "approved" or card_status(r) == "auto_approved":
        return "auto-approved; no objection in the grace window"
    unix = unix_of(r.get("auto_at"))
    now = time.time() if now is None else now
    if state in ("waiting", "due") and (state == "due" or (unix is not None and unix <= now)):
        return "approves at the next pass unless held"
    if state == "waiting" and unix is not None:
        return f"auto-approves <t:{unix}:R> unless held"
    return (r.get("auto") or "")[:200]


def _bits(r: dict) -> str:
    """Kind, duration and score. Times and scores are inline code (a mono face)."""
    kind = r.get("kind") or ""
    bits = [KIND_LABEL.get(kind, kind), f"`{length(r.get('duration_s'))}`"]
    if kind in ("pick", "short"):
        if r.get("score"):
            bits.append(f"score `{r['score']}`")
        if r.get("category"):
            bits.append(str(r["category"]))
        if r.get("found_by"):
            bits.append(f"found by {r['found_by']}")
    elif r.get("topic"):
        bits.append(str(r["topic"]))
    return " · ".join(b for b in bits if b)


def _fields(r: dict, now: float | None) -> list[dict]:
    fields = [{"name": "Status", "value": status_line(r)[:1024], "inline": True}]
    if r.get("scheduled"):
        when = str(r["scheduled"])[:16].replace("T", " ")
        fields.append({"name": "Uploaded" if r.get("uploaded") else "Scheduled",
                       "value": when, "inline": True})
    if r.get("thumb_text"):
        fields.append({"name": "Thumbnail", "value": str(r["thumb_text"])[:1024], "inline": True})
    if r.get("other_voices"):
        fields.append({"name": "Held: other voices" if r.get("hold") else "Other voices",
                       "value": r["other_voices"] + (". Release the hold if the show carries it."
                                                     if r.get("hold") else ""), "inline": False})
    auto = auto_field(r, now)
    if auto:
        fields.append({"name": "Auto-approve", "value": auto[:1024], "inline": False})
    if r.get("checks"):
        fields.append({"name": "Findings", "value": "\n".join(f"⚠ {c}" for c in r["checks"])[:1000],
                       "inline": False})
    if r.get("resplit"):
        fields.append({"name": "Re-split",
                       "value": "Flagged for re-split. Over the `[segments] split_over_s` limit.",
                       "inline": False})
    if r.get("comment"):
        fields.append({"name": "Note", "value": str(r["comment"])[:1000], "inline": False})
    return fields[:25]


def _rows(r: dict) -> list[dict]:
    short = r.get("short") or ""
    rows = [{"type": 1, "components": [
        {"type": 2, "style": style, "label": label, "custom_id": f"clippa:{action}:{short}"}
        for action, label, style in row]} for row in BUTTONS]
    links = [{"type": 2, "style": 5, "label": "Open in Kitsu", "url": r.get("kitsu_url") or ""}]
    if r.get("youtube_url"):
        links.append({"type": 2, "style": 5, "label": "YouTube", "url": r["youtube_url"]})
    rows[1]["components"] += links
    if r.get("resplit"):  # row 2 is already at five components
        rows.append({"type": 1, "components": [
            {"type": 2, "style": 2, "label": "Re-split", "custom_id": f"clippa:resplit:{short}"}]})
    return rows


def card(r: dict, now: float | None = None) -> tuple[dict, list[dict]]:
    """Embed + button rows for one item (actions.describe row). Used by notify and the bot.
    now: the clock for the auto-approve lead (tests); the field itself uses <t:UNIX:R>."""
    status = card_status(r)
    emoji, word = MARK[status]
    title = f"{emoji} {word} · {r.get('title') or ''}"[:256]
    desc = []
    lead = lead_line(r, now)
    if lead:
        desc.append(lead)
    if r.get("hook"):
        desc.append(f"**Hook:** {r['hook']}")
    bits = _bits(r)
    if bits:
        desc.append(bits)
    embed = {"title": title, "description": "\n".join(desc)[:4096],
             "color": COLORS[COLOR_OF[status]], "fields": _fields(r, now),
             "footer": {"text": footer_text(r.get("short") or "")}}
    if r.get("kitsu_url"):
        embed["url"] = r["kitsu_url"]
    return embed, _rows(r)


def in_review_embed(show: str, n: int, n_seg: int, n_clip: int, episode_url: str, short: str,
                    finished: bool = True) -> dict:
    """The thread starter: a stream that has finished processing, or (finished=False, after the
    fast lane) one whose clips and Shorts are in review with its segments still to come."""
    show = show or "Stream"
    head = f"{show} finished processing." if finished else f"{show}: clips and Shorts are in review."
    desc = f"{head} {n} cuts in review." if n else head
    if not finished:
        desc += " Segments next."
    if n_seg or n_clip:
        desc += f"\n{n_seg} segments and {n_clip} clips."
    emoji, word = MARK["review"]
    embed = {"title": f"{emoji} {word} · {show}"[:256], "description": desc[:4096],
             "color": COLORS["accent"], "footer": {"text": footer_text(short)}}
    if episode_url:
        embed["url"] = episode_url
    return embed


def dm_embed(kind: str, text: str, title: str = "", short: str = "") -> dict | None:
    """A Night Watch embed for a ready, spend or error DM. None for a kind that stays text
    (a digest, a test). No buttons: a DM of this shape is also what a webhook cannot add."""
    if kind == "ready":
        emoji, word = MARK["review"]
        subject, color = title or "Stream", COLORS["accent"]
    elif kind == "spend":
        emoji, word = MARK["cost"]
        subject, color = "Modal", COLORS["held"]
    elif kind in ("failure", "upload", "recorder", "disk", "checkup"):
        emoji, word = MARK["error"]
        subject = title or {"failure": "Worker", "upload": "Upload", "recorder": "Recorder",
                            "disk": "Disk", "checkup": "Checkup"}[kind]
        color = COLORS["finding"]
    else:
        return None
    return {"title": f"{emoji} {word} · {subject}"[:256], "description": (text or "")[:4096],
            "color": color, "footer": {"text": footer_text(short)}}


def resplit_notice(seg: dict, stream: str, limit: float) -> tuple[dict, list[dict]]:
    """A card for a segment flagged for re-split that is not in review (it was not cut).
    One button, on its own row. Approve and the rest need a Kitsu task."""
    sid = seg.get("id") or "segment"
    name = seg.get("title") or seg.get("topic") or sid
    short = f"{stream}/{sid}"
    emoji, word = MARK["resplit"]
    embed = {"title": f"{emoji} {word} · {name}"[:256],
             "description": (f"{cut_label(short)} is over {over_phrase(limit)}. Flagged for re-split.")[:4096],
             "color": COLORS["resplit"],
             "fields": [{"name": "Status", "value": "re-split", "inline": True},
                        {"name": "Re-split",
                         "value": "Flagged for re-split. Over the `[segments] split_over_s` limit.",
                         "inline": False}],
             "footer": {"text": footer_text(short)}}
    rows = [{"type": 1, "components": [
        {"type": 2, "style": 2, "label": "Re-split", "custom_id": f"clippa:resplit:{short}"}]}]
    return embed, rows


def presence_line(in_review: int, held: int, watching: list[str]) -> str:
    """One bot status line. Counts use numerals. Never we, us, or our."""
    if in_review and held:
        return f"{in_review} in review, {held} held."
    if in_review:
        return f"{in_review} in review."
    if held:
        return "Waiting on you."
    names = [n for n in watching if n]
    if len(names) == 1:
        return f"Watching {names[0]}."[:128]
    if names:
        return "On watch."
    return "Quiet board."


def presence_for(cfg: dict) -> str:
    """The line for `clippa bot`: open review, holds, or the show being recorded or run."""
    from .actions import review_dirs, stream_counts
    from .config import for_stream
    from .streams import LIVE
    from .timefmt import age_s

    root = Path(cfg["paths"]["streams"])
    in_review = held = 0
    try:
        dirs = review_dirs(cfg)
    except OSError:
        return "On watch."
    for d in dirs:
        try:
            counts = stream_counts(cfg, d)
        except (OSError, ValueError, KeyError):
            continue
        in_review += int(counts.get("open") or 0)
        held += int(counts.get("held") or 0)
    watching: list[str] = []
    if root.is_dir():
        for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            run = jsonstate.read_or(d / "run.json", {}) or {}
            live = jsonstate.read_or(d / "live" / "state.json", {}) or {}
            active = live.get("status") in LIVE or (
                run.get("status") == "running" and age_s(run.get("heartbeat")) < 15 * 60)
            if not active:
                continue
            try:
                name = for_stream(cfg, d)["meta"]["show_name"] or d.name
            except (OSError, KeyError, TypeError):
                name = d.name
            if name and name not in watching:
                watching.append(name)
    return presence_line(in_review, held, watching)


# ---------- previews ----------

def discord_preview(r: dict, dc: dict) -> tuple[Path, bool]:
    """A preview under the upload limit; True when it's only the opening excerpt."""
    src = r["preview"] if r["preview"].exists() else r["file"]
    dst = r["preview"].parent / "discord" / f"{r['key']}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration = r["duration_s"] or media.duration(src)
    limit = dc["max_upload_mb"] * 1e6 * 0.97
    budget_kbit = limit * 0.93 * 8 / 1000  # leave room for container overhead
    audio = 64
    span = duration
    video = budget_kbit / span - audio
    if video < dc["min_video_kbps"]:
        span = min(duration, dc["excerpt_s"])
        video = budget_kbit / span - audio
    video = min(video, dc["max_video_kbps"])
    excerpt = span < duration - 1
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime and dst.stat().st_size <= limit:
        return dst, excerpt
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    media.drop_output_link(tmp, dst.parent.name)
    for _ in range(3):
        v = int(video)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-protocol_whitelist", "file", "-i", str(src),
                        "-t", f"{span:.2f}", "-vf", f"scale=-2:{dc['preview_height']}", "-r", "30",
                        "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{v}k",
                        "-maxrate", f"{v}k", "-bufsize", f"{2 * v}k", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", f"{audio}k", "-ac", "2",
                        "-movflags", "+faststart", str(tmp)], check=True)
        if tmp.stat().st_size <= limit:
            tmp.replace(dst)
            return dst, excerpt
        video *= 0.8
    raise ClipperError(f"{r['key']}: preview still over {dc['max_upload_mb']} MB")


# ---------- posting ----------

def replace_thumb(api: Rest, channel_id: str, message_id: str, r: dict) -> None:
    """Swap a posted card's .jpg for the current thumbnail, keeping its preview video."""
    msg = api("GET", f"/channels/{channel_id}/messages/{message_id}")
    keep = [{"id": a["id"]} for a in msg.get("attachments", [])
            if not a["filename"].lower().endswith(".jpg")]
    api("PATCH", f"/channels/{channel_id}/messages/{message_id}",
        {"attachments": keep + [{"id": 0, "filename": r["thumbnail"].name}]},
        files=[r["thumbnail"]])


def save_state(dpath: Path, dstate: dict, key: str | None = None) -> None:
    """discord.json, re-read under its lock (a retake may post a card at the same time):
    a new thread replaces it, otherwise only card `key`'s message is written."""
    with jsonstate.update(dpath, {}) as fresh:
        if fresh.get("thread_id") != dstate.get("thread_id"):
            fresh.clear()
            fresh.update(dstate)
            return
        if key:
            fresh.setdefault("messages", {})[key] = dstate["messages"][key]
        if dstate.get("starter"):  # the thread's first message, edited as the counts change
            fresh["starter"] = dstate["starter"]


def ensure_thread(api: Rest, cfg: dict, sdir: Path, dstate: dict, rows: list[dict]) -> str:
    """The stream's review thread, made on the first post. Its starter says how many cuts are
    in review and whether the stream is finished; a later post (the segments after the fast
    lane's clips) edits it, so it doesn't stay at the first post's counts."""
    cfg = for_stream(cfg, sdir)
    dc = cfg["discord"]
    channel_id = str(dc["review_channel_id"])
    show = cfg["meta"]["show_name"] or cfg["review"]["project"] or "Stream"
    n_seg = sum(r["kind"] == "segment" for r in rows)
    n_clip = len(rows) - n_seg
    episode = rows[0]["episode_url"] if rows else ""
    finished = segments_in(sdir)
    embed = in_review_embed(show, len(rows), n_seg, n_clip, episode, sdir.name, finished=finished)
    if dstate.get("thread_id") and dstate.get("channel_id") == channel_id:
        try:
            api("GET", f"/channels/{dstate['thread_id']}")
        except ClipperError:
            pass  # deleted: make a new one
        else:
            refresh_starter(api, dstate, embed)
            return dstate["thread_id"]
    meta = jsonstate.load(sdir / "meta.json")
    name = dc["thread_name"].format(show=show, date=meta.get("started_at", "")[:10],
                                    stream_id=sdir.name, title=meta.get("title", ""))[:100]
    thread_id, starter = open_thread(api, channel_id, name, {"embeds": [embed]})
    dstate.update({"channel_id": channel_id, "thread_id": thread_id, "messages": {},
                   "starter": {**starter, "description": embed["description"]}})
    return thread_id


def segments_in(sdir: Path) -> bool:
    """False while the fast lane's clips are in review and the slow lane's review (the
    segments) hasn't started: run.json's status is "running" in either lane, so it can't say.
    True with no fast lane (one review posts everything), for a run that is done, and for a
    stream run by hand (no run.json)."""
    data = jsonstate.read_or(sdir / "run.json", {}) or {}
    stages = data.get("stages") or {}
    if data.get("status") == "done" or "clips_review" not in stages:
        return True
    return (stages.get("review") or {}).get("status") in ("running", "done", "skipped")


def refresh_starter(api: Rest, dstate: dict, embed: dict) -> None:
    """Edit the thread's first message when its text is out of date. A thread made before the
    starter was kept is left as it is; a failed edit only leaves the old counts."""
    starter = dstate.get("starter")
    if not isinstance(starter, dict) or starter.get("description") == embed["description"]:
        return
    try:
        api("PATCH", f"/channels/{starter['channel']}/messages/{starter['id']}", {"embeds": [embed]})
    except (ClipperError, KeyError):
        return
    starter["description"] = embed["description"]


def open_thread(api: Rest, channel_id: str, name: str, header: dict) -> tuple[str, dict]:
    """A new thread under header in a text channel, or a post in a forum: (its id, where its
    starter message is: {"channel", "id"})."""
    channel = api("GET", f"/channels/{channel_id}")
    if channel["type"] == 15:  # forum: the header is the post's first message, id = the thread's
        thread = api("POST", f"/channels/{channel_id}/threads",
                     {"name": name, "auto_archive_duration": 10080, "message": header})
        return thread["id"], {"channel": thread["id"], "id": thread["id"]}
    msg = api("POST", f"/channels/{channel_id}/messages", header)
    thread = api("POST", f"/channels/{channel_id}/messages/{msg['id']}/threads",
                 {"name": name, "auto_archive_duration": 10080})
    return thread["id"], {"channel": channel_id, "id": msg["id"]}


def post(sdir: Path, cfg: dict, keys: list[str] | None = None, force: bool = False,
         note: str = "") -> int:
    """Post (or repost, for a new Kitsu revision) review cards for a stream's items.
    keys: item keys or their short names (c01, s02)."""
    cfg = for_stream(cfg, sdir)
    from . import actions, review

    dc = cfg["discord"]
    if not dc["review_channel_id"]:
        raise ClipperError("set [discord] review_channel_id in clipper.toml")
    review.sync(sdir, cfg, quiet=True)
    rows = list(actions.describe(cfg, sdir).values())
    # Every post, keys or not: publish (the worker, the CLI) posts with keys, and the notices
    # are only for segments that have no card. Each is posted once (discord.json keeps it).
    notices = unreviewed_resplits(sdir, cfg, rows)
    if not rows and not notices:
        print("nothing published to Kitsu for this stream yet")
        return 0
    want = set(keys or [])
    todo = [r for r in rows if not want or r["key"] in want or r["key"].split("_")[0] in want]
    api = Rest(cfg)
    dpath = sdir / "discord.json"
    dstate = jsonstate.read(dpath, {})
    thread_id = ensure_thread(api, cfg, sdir, dstate, rows)
    save_state(dpath, dstate)
    posted = 0
    for r in todo:
        prev = dstate["messages"].get(r["key"])
        if prev and prev.get("revision") == r["revision"] and not force:
            continue
        t = time.time()
        file, excerpt = discord_preview(r, dc)
        embed, components = card(r)
        content = (f"First {int(dc['excerpt_s'])} s of {length(r['duration_s'])}; "
                   f"full preview in Kitsu." if excerpt else None)
        if note:
            content = note + (f"\n{content}" if content else "")
        files = [file] + ([r["thumbnail"]] if r["thumbnail"] else [])
        msg = api("POST", f"/channels/{thread_id}/messages",
                  {"content": content, "embeds": [embed], "components": components,
                   "attachments": [{"id": i, "filename": f.name} for i, f in enumerate(files)]},
                  files=files)
        dstate["messages"][r["key"]] = {"id": msg["id"], "revision": r["revision"],
                                        "thumb": r["thumb_mtime"]}
        save_state(dpath, dstate, r["key"])
        posted += 1
        print(f"discord {r['short']}: {file.stat().st_size / 1e6:.1f} MB"
              f"{' excerpt' if excerpt else ''} ({time.time() - t:.0f}s)", flush=True)
    posted += post_resplit_notices(api, thread_id, sdir.name, notices, dstate, dpath)
    print(f"discord: {posted} cards posted to thread {thread_id}")
    return posted


def unreviewed_resplits(sdir: Path, cfg: dict, rows: list[dict]) -> list[tuple[dict, float]]:
    """Segments flagged for a re-split that have no review card yet, with the limit in seconds."""
    from .segments import _split_limit

    limit = _split_limit(cfg.get("segments") or {})
    if not limit:
        return []
    data = jsonstate.read_or(sdir / "segments.json", {}) or {}
    have = {r["key"].split("_", 1)[0] for r in rows}
    return [(s, limit) for s in data.get("segments") or []
            if s.get("resplit") and s.get("id") and s["id"] not in have]


def post_resplit_notices(api: Rest, thread_id: str, stream: str, notices: list[tuple[dict, float]],
                         dstate: dict, dpath: Path) -> int:
    """Post one Re-split card per flagged segment that is not in review. The bot token sends
    it, so the button is allowed. A webhook post cannot carry one."""
    posted = 0
    for seg, limit in notices:
        key = f"resplit:{seg['id']}"
        if dstate.get("messages", {}).get(key):
            continue
        embed, components = resplit_notice(seg, stream, limit)
        msg = api("POST", f"/channels/{thread_id}/messages",
                  {"embeds": [embed], "components": components})
        dstate.setdefault("messages", {})[key] = {"id": msg["id"], "revision": None}
        save_state(dpath, dstate, key)
        posted += 1
        print(f"discord {stream}/{seg['id']}: re-split notice", flush=True)
    return posted


# ---------- weekly issue ----------

def issue_messages(issue: dict, here: Path, pages: list[Path]) -> list[tuple[str, str, list[Path]]]:
    """(key, text, files) per message: the ten 3:4 pages for the TikTok carousel, then the
    square pages as the two YouTube Posts with their captions, when youtube/ has all ten."""
    post = issue.get("post") or {}
    head = f"**Lolcow Cinema Weekly · issue {int(issue.get('issue') or 0):02d}** · {issue.get('week') or here.name}"
    out = [("tiktok", f"{head}\nTikTok carousel: pages 01-10 (1080x1440)", pages)]
    square = [here / "youtube" / p.name for p in pages]
    if all(p.exists() for p in square):
        out += [("youtube1", f"YouTube Post part 1 (square 01-05):\n{post.get('part1', '')}", square[:5]),
                ("youtube2", f"YouTube Post part 2 (square 06-10):\n{post.get('part2', '')}", square[5:])]
    out = [(k, t.strip(), f) for k, t, f in out]
    long = [k for k, t, _ in out if len(t) > MAX_CONTENT]
    if long:
        raise ClipperError(f"over Discord's {MAX_CONTENT} characters: {', '.join(long)}; "
                           f"shorten issue.json post.part1/part2")
    return out


def weekly_channel(api: Rest, dc: dict) -> str:
    """The id of the text channel named [discord] weekly_channel in guild_id, made if missing."""
    if not str(dc["weekly_channel"]).strip() or not dc["guild_id"]:
        raise ClipperError("set [discord] guild_id and weekly_channel in clipper.toml")
    return named_channel(api, dc, dc["weekly_channel"], "Weekly issues' pages, to save on a phone")


def named_channel(api: Rest, dc: dict, name: str, topic: str, kinds: tuple[int, ...] = (0,)) -> str:
    """The id of guild_id's channel called name of one of kinds (0 text, 15 forum), or a new
    text channel with that name and topic."""
    name = str(name).strip().lstrip("#").lower()
    if not name or not dc["guild_id"]:
        raise ClipperError("set [discord] guild_id and the channel's name in clipper.toml")
    for ch in api("GET", f"/guilds/{dc['guild_id']}/channels"):
        if ch["type"] in kinds and ch["name"] == name:
            return ch["id"]
    try:
        ch = api("POST", f"/guilds/{dc['guild_id']}/channels", {"name": name, "type": 0, "topic": topic[:1024]})
    except ClipperError as e:
        raise ClipperError(f"couldn't make #{name} ({e}); give the bot Manage Channels "
                           f"or make the channel by hand") from e
    print(f"discord: made #{name}")
    return ch["id"]


def claim_issue(dpath: Path, now: datetime | None = None) -> bool:
    """Take the issue for this run under discord.json's lock, so two runs can't post it twice;
    False while another run holds it (a claim older than ISSUE_CLAIM_S is taken over)."""
    now = now or datetime.now(UTC)
    with jsonstate.update(dpath, {}) as state:
        c = state.get("claim")
        if c and (now - datetime.fromisoformat(c)).total_seconds() < ISSUE_CLAIM_S:
            return False
        state["claim"] = now.isoformat(timespec="seconds")
    return True


def release_issue(dpath: Path) -> None:
    with jsonstate.update(dpath, {}) as state:
        state.pop("claim", None)


def publish_and_post(stream_dir: Path, cfg: dict, force: bool = False, only: list[str] | None = None,
                     post_cards: bool = True) -> list[str]:
    """`clippa review publish`, then a card for each item it published. The CLI, `pick`
    and `story cut` go through here so review.py itself never knows about Discord.
    post_cards=False (`--no-notify`, the worker without [worker] notify) publishes only.
    Returns the published keys."""
    from . import review

    cfg = for_stream(cfg, stream_dir)  # the stream's own [discord] enabled, as publish and post see it
    published = review.publish(stream_dir, cfg, force=force, only=only)
    rc = cfg.get("review") or {}
    if published and rc.get("auto_approve") and rc.get("auto_preshow") == "now":
        from . import review_auto

        if review_auto.stream_kind(stream_dir) == "preshow":   # it goes public at upload: no waiting
            try:
                review_auto.run(cfg, stream=stream_dir.name)
            except Exception as e:  # noqa: BLE001  # serve's next pass tries again
                print(f"auto-approve: {type(e).__name__}: {e}")
    if post_cards and published and cfg["discord"]["enabled"]:
        post(stream_dir, cfg, keys=published)
    return published


def post_issue(cfg: dict, day: str | None = None) -> int:
    """Post a weekly issue's pages to [discord] weekly_channel, to save them on a phone for
    posting by hand. <issue>/discord.json keeps each message, so a rerun posts only what's missing."""
    from .weekly import issue_folder, issue_pages

    dc = cfg["discord"]
    here = issue_folder(cfg, day)
    issue = jsonstate.load(here / "issue.json")
    messages = issue_messages(issue, here, issue_pages(here))
    limit = dc["max_upload_mb"] * 1e6
    big = [p.name for _, _, files in messages for p in files if p.stat().st_size > limit]
    if big:
        raise ClipperError(f"over [discord] max_upload_mb: {', '.join(big)}")
    api = Rest(cfg)
    channel_id = weekly_channel(api, dc)
    dpath = here / "discord.json"
    if not claim_issue(dpath):
        raise ClipperError(f"issue {here.name} is being posted to Discord by another run")
    posted = 0
    try:
        for key, text, files in messages:
            if jsonstate.read(dpath, {}).get("messages", {}).get(key, {}).get("channel_id") == channel_id:
                continue
            msg = api("POST", f"/channels/{channel_id}/messages",
                      {"content": text,
                       "attachments": [{"id": i, "filename": f.name} for i, f in enumerate(files)]},
                      files=files)
            with jsonstate.update(dpath, {}) as state:
                state.setdefault("messages", {})[key] = {"id": msg["id"], "channel_id": channel_id}
            posted += 1
            print(f"discord {key}: {len(files)} pages")
    finally:
        release_issue(dpath)
    print(f"discord: issue {here.name}, {posted} of {len(messages)} messages posted"
          f"{'' if posted == len(messages) else ' (the rest were already there)'}")
    return posted


def invite(cfg: dict, user_id: str) -> tuple[list[str], bool]:
    """`clippa review invite <discord user id>`: a reviewer onto [discord] allowed_user_ids in
    clipper.toml (the bot reads it at start, so restart it). The file's own list is extended, not
    the resolved one; when clipper.local.toml, a show or an env var sets the list too, that layer
    would win and the invite is refused with where to add the id. (the ids after, whether new)."""
    from .config import base, explain
    from .setup import toml_set

    uid = str(user_id).strip()
    if not uid.isdigit() or not 15 <= len(uid) <= 22:
        raise ClipperError(f"{user_id!r} isn't a Discord user id (17-20 digits: Settings > Advanced > "
                           "Developer Mode, then right-click the person > Copy User ID)")
    path = base(cfg).get("_file")
    if not path:
        raise ClipperError("no clipper.toml to write to: run clippa setup first")
    path = Path(path)
    above = [name for name, _ in explain(cfg, "discord.allowed_user_ids")["layers"]
             if name not in ("default", path.name)]
    if above:
        raise ClipperError(f"[discord] allowed_user_ids is also set in {', '.join(above)}, which wins over "
                           f"{path.name}: add {uid} there")
    ids = [str(x) for x in tomllib.loads(path.read_text(encoding="utf-8")).get("discord", {}).get("allowed_user_ids", [])]
    if uid in ids:
        return ids, False
    ids.append(uid)
    toml_set(path, "discord", "allowed_user_ids", ids)
    return ids, True
