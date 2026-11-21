"""The Clippa messenger's routes (#158): the /chat page's poll, its sends and taps, and the buddy
list. The chat itself is clippa/chat.py; this is only the HTTP side."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .. import catalog, chat
from ..errors import ClipperError

THEMES = Path(__file__).resolve().parents[1] / "web" / "themes"   # one .css per look
BUDDIES_TTL_S = 20   # the buddy list reads every stream's run.json over the share: not every poll
_BUDDIES: dict = {"at": 0.0, "rows": []}
_BUDDIES_LOCK = threading.Lock()


def chat_get(cfg: dict, since: str) -> dict:
    return chat.state(cfg, since)


def themes() -> list[str]:
    """The looks the page can wear: every file in web/themes/, so a new look needs no code."""
    return sorted(p.stem for p in THEMES.glob("*.css"))


def chat_themes(cfg: dict) -> dict:
    """The looks, and [chat] theme as the default (the first one when it names none of them)."""
    names = themes()
    want = cfg["chat"]["theme"]
    return {"themes": names, "default": want if want in names else (names[0] if names else "")}


def chat_buddies(cfg: dict) -> dict:
    with _BUDDIES_LOCK:
        if time.monotonic() - _BUDDIES["at"] > BUDDIES_TTL_S:
            _BUDDIES.update(at=time.monotonic(), rows=catalog.buddies(cfg))
        return {"buddies": _BUDDIES["rows"]}


def _text(body: dict, key: str) -> str:
    v = body.get(key, "")
    if not isinstance(v, str):
        raise ClipperError(f"{key} should be text")
    return v


def chat_send(cfg: dict, body: dict) -> dict:
    return chat.handle(cfg, _text(body, "text"))


def chat_card(cfg: dict, body: dict) -> dict:
    return chat.tap(cfg, _text(body, "id"), body.get("yes") is True)


def chat_pick(cfg: dict, body: dict) -> dict:
    return chat.pick(cfg, _text(body, "msg"), _text(body, "ref"))
