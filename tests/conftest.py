"""Shared fixtures. Tests are offline: no network, ffmpeg, GPU or model calls."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from clippa import config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No CLIPPER_* or CLIPPA_* setting from this machine leaks into a test, nor a stream's channel
    cached by an earlier one."""
    for name in list(os.environ):
        if name.startswith(("CLIPPER_", "CLIPPA_")):
            monkeypatch.delenv(name)
    config._CHANNELS.clear()
    monkeypatch.setattr(config, "_registry", lambda name: "")   # nor this user's saved Windows variables
    # OpenRouter's public model list stays unfetched: tests are offline.
    from clippa import llm

    monkeypatch.setattr(llm, "_OPENROUTER_PRICES", {})
    monkeypatch.setattr(llm, "_OPENROUTER_CONTEXT", {})
    monkeypatch.setattr(llm, "_OPENROUTER_MAX_OUT", {})
    monkeypatch.setattr(llm, "_OPENROUTER_FAILED_AT", 0.0)


@pytest.fixture
def defaults(tmp_path):
    """The built-in defaults, as `clippa` loads them with no clipper.toml; people.json (which
    story and investigate read) in tmp_path, never this checkout's own."""
    cfg = config.load(tmp_path / "clipper.toml", use_local=False)
    cfg["paths"]["people"] = tmp_path / "people.json"
    return cfg
