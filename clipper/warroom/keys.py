"""The Keys window: serve's own API keys (names, where each comes from, the last check), the
streams the worker holds for a rejected key, and Check, one free request a provider
(clipper/keys.py). No value ever reaches the page."""
from __future__ import annotations

from ..errors import ClipperError


def keys_get(cfg: dict) -> dict:
    from .. import keys

    return keys.status(cfg)


def keys_check(cfg: dict, body: dict) -> dict:
    """{name?}: ask that key's provider, or every one's without a name."""
    from .. import keys

    name = str(body.get("name") or "")
    if name and name not in {k.name for k in keys.specs(cfg)}:
        raise ClipperError(f"keys: no key {name!r}")
    return keys.status(cfg, run_checks=name or True)
