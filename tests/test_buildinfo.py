"""clipper/buildinfo.py: the version a copy reports (`clipper --version`, doctor, heartbeats)."""
import json
import re
import shlex
import shutil
import subprocess
from datetime import UTC, datetime

import pytest
from conftest import ROOT

from clipper import __version__, buildinfo, daemon, services


@pytest.fixture(autouse=True)
def fresh():
    buildinfo.version.cache_clear()
    yield
    buildinfo.version.cache_clear()


@pytest.mark.parametrize("raw, want", [
    ("v0.2.0", "0.2.0"),                                  # exactly at the release tag
    ("v0.2.0\n", "0.2.0"),                                # the stamp file ends in a newline
    ("v0.2.0-dirty", "0.2.0+dirty"),
    ("v0.2.0-3-g570bb87", "0.2.0+3.g570bb87"),            # three commits past it
    ("v0.2.0-3-g570bb87-dirty", "0.2.0+3.g570bb87.dirty"),
    ("v1.10", "1.10"),
    ("v0.3.0rc1-2-gabc1234", "0.3.0rc1+2.gabc1234"),      # a PEP 440 pre-release tag
    ("570bb87", "9.9.9+g570bb87"),                        # no release tag in reach: the base + the commit
    ("570bb87-dirty", "9.9.9+g570bb87.dirty"),
    ("", ""), ("fatal: not a git repository", ""), ("v0.2.0-x-gzz", ""),
])
def test_normalize(raw, want):
    assert buildinfo.normalize(raw, base="9.9.9") == want


def test_a_stamp_file_wins_and_is_read_once(tmp_path, monkeypatch):
    """An image has no .git: deploy/stamp.sh wrote what `git describe` said into the package."""
    stamp = tmp_path / "_build.txt"
    stamp.write_text("v0.2.0-5-gabc1234\n", encoding="utf-8")
    monkeypatch.setattr(buildinfo, "STAMP", stamp)
    monkeypatch.setattr(buildinfo, "_from_git", lambda: pytest.fail("git is asked only without a stamp"))
    assert buildinfo.version() == "0.2.0+5.gabc1234"
    stamp.write_text("v9.9.9", encoding="utf-8")
    assert buildinfo.version() == "0.2.0+5.gabc1234"      # cached for the life of the process


def test_without_a_stamp_or_git_it_is_the_plain_version(tmp_path, monkeypatch):
    """A copy installed from a wheel has neither."""
    monkeypatch.setattr(buildinfo, "STAMP", tmp_path / "missing.txt")
    monkeypatch.setattr(buildinfo, "_from_git", lambda: "")
    assert buildinfo.version() == __version__


def test_a_garbled_stamp_falls_through_to_git(tmp_path, monkeypatch):
    stamp = tmp_path / "_build.txt"
    stamp.write_text("warning: something else\n", encoding="utf-8")
    monkeypatch.setattr(buildinfo, "STAMP", stamp)
    monkeypatch.setattr(buildinfo, "_from_git", lambda: "v0.2.0\n")
    assert buildinfo.version() == "0.2.0"


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                    *args], cwd=repo, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_checkout_is_read_from_git(tmp_path):
    """The dev machine's path: no stamp, `git describe` of the folder the package is in."""
    assert buildinfo._from_git(tmp_path) == ""            # no .git: nothing to ask
    git(tmp_path, "init", "-q")
    (tmp_path / "f.txt").write_text("one", encoding="utf-8")
    git(tmp_path, "add", "f.txt")
    git(tmp_path, "commit", "-q", "-m", "first")
    assert re.fullmatch(r"[0-9a-f]+", buildinfo._from_git(tmp_path).strip())           # no tag yet: a commit
    git(tmp_path, "tag", "-a", "v0.2.0", "-m", "release")
    assert buildinfo.normalize(buildinfo._from_git(tmp_path)) == "0.2.0"
    (tmp_path / "f.txt").write_text("two", encoding="utf-8")
    git(tmp_path, "commit", "-q", "-am", "second")
    assert re.fullmatch(r"0\.2\.0\+1\.g[0-9a-f]+", buildinfo.normalize(buildinfo._from_git(tmp_path)))
    (tmp_path / "f.txt").write_text("three", encoding="utf-8")
    assert buildinfo.normalize(buildinfo._from_git(tmp_path)).endswith(".dirty")
    git(tmp_path, "tag", "-a", "other", "-m", "not a release")                          # --match skips it
    assert buildinfo.normalize(buildinfo._from_git(tmp_path)).startswith("0.2.0+1.g")


def test_the_deploy_stamp_asks_git_the_same_question():
    """deploy/stamp.sh writes the file buildinfo reads: one `git describe`, spelled twice."""
    sh = (ROOT / "deploy" / "stamp.sh").read_text(encoding="utf-8")
    asked = "describe " + " ".join(shlex.quote(a) for a in buildinfo.DESCRIBE[2:])
    assert asked in sh, f"deploy/stamp.sh must run `git {asked}`, as buildinfo.DESCRIBE does"


def test_the_heartbeat_carries_the_version(tmp_path, monkeypatch):
    monkeypatch.setattr(buildinfo, "STAMP", tmp_path / "_build.txt")
    (tmp_path / "_build.txt").write_text("v0.2.0-2-gdeadbee", encoding="utf-8")
    cfg = {"paths": {"streams": tmp_path}}
    daemon.beat(cfg, "serve", "2026-11-18T10:00:00+00:00")
    hb = daemon.heartbeat(cfg, "serve")
    assert hb["data"]["version"] == "0.2.0+2.gdeadbee"
    assert "version 0.2.0+2.gdeadbee, pid" in daemon.heartbeat_lines("serve", hb)[0]


def test_an_old_heartbeat_without_a_version_still_reads(tmp_path):
    """The hub runs the old code until the next deploy: its heartbeat has no version."""
    cfg = {"paths": {"streams": tmp_path}}
    daemon.beat(cfg, "bot", "2026-11-18T10:00:00+00:00")
    path = daemon.heartbeat_path(cfg, "bot")
    data = daemon.read_heartbeat(path)
    del data["version"]
    path.write_text(json.dumps(data), encoding="utf-8")
    line = daemon.heartbeat_lines("bot", daemon.heartbeat(cfg, "bot"))[0]
    assert line.startswith("bot: up on") and "version" not in line


def test_the_recorder_reports_its_version(tmp_path):
    cfg = {"paths": {"streams": tmp_path}}
    path = tmp_path / ".record" / "status.json"
    path.parent.mkdir()
    at = datetime.now(UTC).isoformat(timespec="seconds")
    path.write_text(f'{{"at": "{at}", "version": "0.2.0", "channels": {{"kino": "offline"}}}}', encoding="utf-8")
    rec = services.recorder(cfg)
    assert rec["version"] == "0.2.0"
    assert "version 0.2.0," in services.recorder_lines(rec)[0]
