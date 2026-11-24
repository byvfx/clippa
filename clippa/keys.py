"""`clippa keys` and the war room's Keys window: which API keys this process has, where each one
comes from (its env var or its file in [paths]), what uses it, and, on asking, whether its
provider still takes it: one free request each (a model list, a "who am I"), never a paid call.

No value ever leaves this module but in the request to its own provider: what comes back is a
key's name, its env var, where it was found and the provider's answer. The war room keeps the
last answers in <streams>/.serve/keys.json, by host. Streams the worker holds for a rejected key
(run.json `key_rejected`, #281) are listed with them."""
from __future__ import annotations

import os
import socket
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import jsonstate
from .timefmt import now_iso

TIMEOUT_S = 15

# A check: (url, headers) for one GET, from the key's value and the config.
Check = Callable[[str, dict], tuple[str, dict]]


def _anthropic(v: str, cfg: dict) -> tuple[str, dict]:
    return "https://api.anthropic.com/v1/models?limit=1", {"x-api-key": v, "anthropic-version": "2023-06-01"}


def _anthropic_admin(v: str, cfg: dict) -> tuple[str, dict]:
    return ("https://api.anthropic.com/v1/organizations/users?limit=1",
            {"x-api-key": v, "anthropic-version": "2023-06-01"})


def _bearer(url: str) -> Check:
    return lambda v, cfg: (url, {"Authorization": f"Bearer {v}"})


def _gemini(v: str, cfg: dict) -> tuple[str, dict]:
    return "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1", {"x-goog-api-key": v}


class NotAsked(Exception):
    """A check that can't be made with this config; its message is ours, safe to show."""


def _elevenlabs(v: str, cfg: dict) -> tuple[str, dict]:
    return "https://api.elevenlabs.io/v1/models", {"xi-api-key": v}


def _kitsu(v: str, cfg: dict) -> tuple[str, dict]:
    host = (cfg["review"]["host"] or "").rstrip("/")
    if not host or "<" in host:
        raise NotAsked("no [review] host to ask")
    return f"{host}/auth/authenticated", {"Authorization": f"Bearer {v}"}


def _discord(v: str, cfg: dict) -> tuple[str, dict]:
    return "https://discord.com/api/v10/users/@me", {"Authorization": f"Bot {v}"}


@dataclass(frozen=True)
class Key:
    name: str
    env: str             # the env var; a config key names it where the config does
    path: str            # [paths] key of the file it falls back to, "" if none
    what: str
    check: Check | None = None
    why_not: str = ""    # why there's no check


def specs(cfg: dict) -> list[Key]:
    """Every key Clippa can use, model providers first."""
    return [
        Key("Anthropic", "ANTHROPIC_API_KEY", "anthropic_key", "Claude: scoring, segments, titles, covers, checks",
            _anthropic),
        Key("OpenAI", "OPENAI_API_KEY", "openai_key", "openai: models", _bearer("https://api.openai.com/v1/models")),
        Key("Gemini", "GEMINI_API_KEY", "gemini_key", "gemini: models", _gemini),
        Key("OpenRouter", "OPENROUTER_API_KEY", "openrouter_key", "openrouter: models",
            _bearer("https://openrouter.ai/api/v1/key")),
        Key("OpenRouter management", "OPENROUTER_MANAGEMENT_KEY", "openrouter_management_key",
            "war room: the OpenRouter key's limit",
            _bearer("https://openrouter.ai/api/v1/key")),
        Key("Anthropic admin", "ANTHROPIC_ADMIN_KEY", "anthropic_admin_key", "Claude's own bill in clippa costs",
            _anthropic_admin),
        Key("Kitsu", cfg["review"]["token_env"], "", "review: the Kitsu bot token", _kitsu),
        Key("Discord", cfg["discord"]["token_env"], "discord_token", "the bot, DMs and alerts", _discord),
        Key("ElevenLabs", cfg["narrate"]["key_env"], "elevenlabs_key", "narration and story voices", _elevenlabs),
        Key("Hugging Face", "HF_TOKEN", "", "speaker diarization models",
            _bearer("https://huggingface.co/api/whoami-v2")),
        Key("Modal", "MODAL_TOKEN_ID", "", "transcription on Modal (with MODAL_TOKEN_SECRET)",
            why_not="checked by the modal client itself (clippa modal)"),
        Key("Brave", "BRAVE_API_KEY", "brave_key", "cast image search",
            why_not="every call counts against its monthly plan"),
        Key("Pixabay", "PIXABAY_API_KEY", "pixabay_key", "clip art and b-roll",
            why_not="its API takes the key in the URL; not sent just to check"),
        Key("DVIDS", "DVIDS_API_KEY", "dvids_key", "b-roll", why_not="no free endpoint to ask"),
        Key("sync.so", cfg["perform"]["sync_key_env"], "sync_key", "lip-sync", why_not="no free endpoint to ask"),
    ]


def value(cfg: dict, key: Key) -> tuple[str, str]:
    """(the key, where it came from: "env" or "file"); ("", "") when there's none."""
    v = os.environ.get(key.env, "").strip()
    if v:
        return v, "env"
    p = cfg["paths"].get(key.path) if key.path else None
    if p and Path(p).is_file():
        try:
            v = Path(p).read_text(encoding="utf-8-sig").strip()
        except OSError:
            return "", ""
        if v:
            return v, "file"
    return "", ""


def classify(code: int, text: str) -> tuple[bool | None, str]:
    """A provider's answer: True (takes the key), False (refuses it), None (can't tell)."""
    t = text.lower()
    if 200 <= code < 300:
        return True, "works"
    if code in (401, 403) and "permission" in t:  # a scoped key (ElevenLabs) can't answer this one
        return None, f"the key isn't allowed to answer this check (HTTP {code}); it may still work"
    if code in (401, 403) or (code == 400 and "key" in t and ("invalid" in t or "not valid" in t)):
        return False, f"rejected (HTTP {code})"
    return None, f"no answer about the key (HTTP {code})"


def check(cfg: dict, key: Key) -> dict:
    """One free request to the key's provider. Never raises."""
    import requests

    v, _ = value(cfg, key)
    if not v:
        return {"ok": None, "note": "not set", "at": now_iso()}
    if not key.check:
        return {"ok": None, "note": f"not checked: {key.why_not}", "at": now_iso(), "of": tag(v)}
    try:
        url, headers = key.check(v, cfg)
        r = requests.get(url, headers=headers, timeout=TIMEOUT_S)
        ok, note = classify(r.status_code, r.text[:500])
    except NotAsked as e:
        ok, note = None, str(e)
    except requests.RequestException as e:  # never its message: an invalid header quotes the key
        ok, note = None, f"couldn't reach it ({type(e).__name__})"
    except Exception as e:  # noqa: BLE001  # one provider's surprise mustn't lose the others' answers
        ok, note = None, f"check failed ({type(e).__name__})"   # the type only, as above
    return {"ok": ok, "note": note, "at": now_iso(), "of": tag(v)}


def tag(v: str) -> str:
    """Which value an answer was about (a short hash, nothing of the key): an answer about a
    key since replaced isn't shown as the new one's."""
    import hashlib

    return hashlib.sha256(v.encode()).hexdigest()[:8]


def store(cfg: dict) -> Path:
    return cfg["paths"]["streams"] / ".serve" / "keys.json"


def held(cfg: dict) -> list[dict]:
    """Streams the worker holds because a key was rejected (#281)."""
    out = []
    root: Path = cfg["paths"]["streams"]
    for p in sorted(root.glob("*/run.json")) if root.is_dir() else []:
        run = jsonstate.read_or(p, {})
        if run.get("status") == "failed" and run.get("key_rejected"):
            out.append({"stream": p.parent.name, "error": str(run.get("error", ""))[:300],
                        "since": run.get("updated", "")})
    return out


def anthropic_unused(cfg: dict) -> bool:
    """No stage calls Anthropic itself, so its keys are optional and not probed."""
    from .llm import uses_bare_claude

    return not uses_bare_claude(cfg)


def status(cfg: dict, run_checks: bool | str = False, remember: bool = True) -> dict:
    """Every key's row; run_checks: True for all, a key's name for one. With remember, the
    answers go in keys.json (the war room's), and earlier ones fill the rows not checked now."""
    from .doctor import llm_providers

    ks, host = specs(cfg), socket.gethostname()
    # The streams folder is shared (a war room on the laptop sees the NAS's): each host's own answers.
    last = jsonstate.read_or(store(cfg), {}).get(host, {}) if remember else {}
    skip = {"Anthropic", "Anthropic admin"} if anthropic_unused(cfg) else set()
    want = [k for k in ks if k.name not in skip and (run_checks is True or run_checks == k.name)]
    if want:
        with ThreadPoolExecutor(max_workers=8) as ex:
            got = dict(zip([k.name for k in want], ex.map(lambda k: check(cfg, k), want), strict=True))
        if remember:  # only these answers, into what's there now: a check running beside it keeps its own
            store(cfg).parent.mkdir(parents=True, exist_ok=True)
            with jsonstate.update(store(cfg), {}) as saved:
                saved[host] = {**(saved.get(host) or {}), **got}
                last = dict(saved[host])
        else:
            last = {**last, **got}
    uses = llm_providers(cfg)
    rows = []
    for k in ks:
        v, source = value(cfg, k)
        where = f"${k.env}" if source == "env" else f"file {Path(cfg['paths'][k.path]).name}" if source == "file" else ""
        used = uses.get(k.name.lower(), [])
        answer = last.get(k.name)
        if answer and answer.get("of") != (tag(v) if v else None):
            answer = None  # about a key since replaced, removed or (a "not set") added: ask again
        if answer:
            answer = {x: answer[x] for x in ("ok", "note", "at") if x in answer}
        optional = k.name in skip
        if optional:
            answer = {"ok": None, "note": "skipped: optional, no stage uses a bare claude- model"}
        rows.append({"name": k.name, "env": k.env, "set": bool(v), "source": where,
                     "what": k.what + (f" ({len(used)} setting{'s' * (len(used) > 1)})" if used else ""),
                     "checkable": bool(k.check) and not optional, "optional": optional, "last": answer})
    return {"keys": rows, "held": held(cfg), "host": socket.gethostname()}


def lines(report: dict) -> list[str]:
    """`clippa keys`' table."""
    out = []
    for r in report["keys"]:
        last = r["last"] or {}
        mark = {True: "ok", False: "REJECTED", None: "?"}[last.get("ok")] if last else ""
        out.append(f"{r['name']:<16} {r['env']:<22} {(r['source'] or 'not set'):<32} "
                   f"{mark:<9} {last.get('note', '') if last else ''}".rstrip())
    for h in report["held"]:
        out.append(f"held: {h['stream']} since {h['since']}: {h['error']}")
    return out
