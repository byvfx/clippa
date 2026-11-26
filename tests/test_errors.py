"""Library code raises ClipperError; only cli.main turns it into an exit."""
import re
import sys

import pytest
from conftest import ROOT

from clippa import cli, setup
from clippa.errors import ClipperError, exit_code


def run_cli(monkeypatch, tmp_path, *argv):
    monkeypatch.setattr(sys, "argv", ["clipper", "--config", str(tmp_path / "clipper.toml"), *argv])
    with pytest.raises(SystemExit) as e:
        cli.main()
    return e.value.code


@pytest.mark.parametrize("raw, signed", [
    (1, 1),  # an ordinary failure: unchanged
    (-9, -9),  # already signed (POSIX): unchanged
    (3199971767, -1094995529),  # 0xBEBEBE37, Windows' unsigned form of ffmpeg's negative errno
    (4294967295, -1),  # 0xFFFFFFFF: ffmpeg's "no code" as the shell shows it
], ids=["small", "already-signed", "windows-ffmpeg", "all-bits"])
def test_exit_code_reads_what_windows_hands_back_as_signed(raw, signed):
    """A tool's negative errno comes back unsigned from Windows (0xBEBEBE37, 3199971767): a
    message must say -1094995529, the signed 32-bit value a person's shell prints, and leave a
    small or already-signed code alone."""
    assert exit_code(raw) == signed


def test_clipper_error_exits_1_with_its_message(tmp_path, monkeypatch, capsys):
    assert run_cli(monkeypatch, tmp_path, "--show", "nope", "shows") == 1
    assert "no [shows.nope]" in capsys.readouterr().err


def test_exit_status_passes_through(tmp_path, monkeypatch, capsys):
    """setup's EXIT_SYNC (3) tells deploy/bootstrap to install packages and rerun setup;
    doctor and setup return 0 on success."""
    def needs_sync(*a, **k):
        raise ClipperError(status=setup.EXIT_SYNC)
    monkeypatch.setattr(setup, "main", needs_sync)
    assert run_cli(monkeypatch, tmp_path, "setup") == 3
    assert capsys.readouterr().err == ""

    monkeypatch.setattr(setup, "main", lambda *a, **k: 0)
    assert run_cli(monkeypatch, tmp_path, "setup") == 0


def test_library_raises_no_system_exit():
    """`raise SystemExit` outside cli.py stops the bot or the war room instead of failing one
    action. cli.py is the boundary: it exits with a status."""
    found = []
    for path in sorted((ROOT / "clippa").rglob("*.py")):
        if path.name == "cli.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"raise SystemExit\b|sys\.exit\(", line):
                found.append(f"{path.name}:{n}")
    assert not found, "raise ClipperError instead: " + ", ".join(found)


def test_no_status_wrapped_as_a_message():
    """ClipperError's first argument is a message; an exit status goes in status=."""
    found = []
    for path in sorted((ROOT / "clippa").rglob("*.py")):
        if path.name == "errors.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"ClipperError\(([^)]*)", line)
            if m and m.group(1) and not re.match(r"""\s*(f?["']|\(|str\(|status=|$)""", m.group(1)):
                found.append(f"{path.name}:{n}")
    assert not found, ", ".join(found)
