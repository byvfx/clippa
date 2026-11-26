"""Shared fixtures. Tests are offline: no network, ffmpeg, GPU or model calls."""
from __future__ import annotations

import errno
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


@pytest.fixture
def link_or_skip():
    """A symlink, or a skip only where the OS demands a privilege this machine hasn't (Windows
    without Developer Mode: EPERM/EACCES, or WinError 1314). Anything else is a broken fixture and
    fails the test, so a Linux runner, where links always work, can't skip it quietly."""
    def make(target, link, *, directory=False):
        try:
            os.symlink(target, link, target_is_directory=directory)
        except NotImplementedError:  # a platform without symlinks
            pytest.skip("this platform has no symlinks")
        except OSError as e:
            if e.errno not in (errno.EPERM, errno.EACCES) and getattr(e, "winerror", None) != 1314:
                raise
            pytest.skip("a symlink needs Developer Mode or a privilege this machine hasn't")
    return make


@pytest.fixture
def junction_or_skip():
    """A Windows directory junction named `link` for the folder `target`, or a skip where the
    platform has none (no `os.path.isjunction` before 3.12, not Windows) or the filesystem won't
    make one. A junction needs no privilege, so on Windows it should run: a skip here is noted.
    `directory` is link_or_skip's argument and is always true here; it lets a test name either
    maker and its own kind of link the same way."""
    def make(target, link, *, directory=False):
        if not hasattr(os.path, "isjunction"):
            pytest.skip("this Python or platform has no junctions")
        try:
            import _winapi
        except ImportError:
            pytest.skip("junctions are Windows-only")
        if not hasattr(_winapi, "CreateJunction"):
            pytest.skip("this Python has no junction maker")
        try:
            _winapi.CreateJunction(str(target), str(link))
        except OSError:
            pytest.skip("this filesystem won't make a junction")
        assert os.path.isjunction(link), "the fixture did not make a junction"
    return make
