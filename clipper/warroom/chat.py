"""The Clippa messenger's routes (#158): the /chat page's poll, its sends and taps, and the buddy
list. The chat itself is clipper/chat.py; this is only the HTTP side."""
from __future__ import annotations

import threading
import time

from .. import catalog, chat
from ..errors import ClipperError

BUDDIES_TTL_S = 20   # the buddy list reads every stream's run.json over the share: not every poll
_BUDDIES: dict = {"at": 0.0, "rows": []}
_BUDDIES_LOCK = threading.Lock()


def chat_get(cfg: dict, since: str) -> dict:
    return chat.state(cfg, since)


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
