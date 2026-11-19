"""Which copy of clipper this is, for `clipper --version`, `clipper doctor` and the heartbeats.

`__version__` (clipper/__init__.py, equal to pyproject.toml) is the number of the last release,
bumped by the release PR and made real by the tag: until `v<number>` exists a copy reports the
release before it, plus commits (tag right after that PR merges, before the deploy that should
carry the number). A copy says which commit it is, as a PEP 440 local version:

    0.2.0               a release (the checkout is exactly at tag v0.2.0)
    0.2.0+3.g570bb87    3 commits after v0.2.0, at 570bb87
    0.2.0+g570bb87      no release tag in reach: __version__ and the commit
    0.2.0+3.g570bb87.dirty   (any of these) with uncommitted changes to tracked files

The text comes from `git describe --tags --always --dirty --match "v[0-9]*"`, read from the first
of: clipper/_build.txt, which deploy/stamp.sh writes into the copy of the code that goes into a
container image (an image has no .git); that command run in the checkout this package is
imported from (a dev machine); nothing, for a copy installed from a wheel, which is then plainly
`__version__`.
"""

from __future__ import annotations

import functools
import re
import subprocess
from pathlib import Path

from . import __version__

DESCRIBE = ["git", "describe", "--tags", "--always", "--dirty", "--match", "v[0-9]*"]
STAMP = Path(__file__).with_name("_build.txt")

# a tag is a PEP 440 number after the v: 0.2.0, or a pre-release such as 0.3.0rc1
_TAG = re.compile(r"v(?P<tag>\d[\w.]*?)(?:-(?P<n>\d+)-g(?P<sha>[0-9a-f]+))?(?P<dirty>-dirty)?")
_SHA = re.compile(r"(?P<sha>[0-9a-f]{4,40})(?P<dirty>-dirty)?")


def normalize(raw: str, base: str = __version__) -> str:
    """`git describe` text as a version; "" when it isn't any of the shapes git prints."""
    raw = raw.strip()
    if m := _TAG.fullmatch(raw):
        tag, n, sha = m["tag"], m["n"], m["sha"]
        local = [f"{n}.g{sha}"] if n else []
    elif m := _SHA.fullmatch(raw):
        tag, local = base, [f"g{m['sha']}"]
    else:
        return ""
    if m["dirty"]:
        local.append("dirty")
    return tag + ("+" + ".".join(local) if local else "")


def _stamped() -> str:
    try:
        return STAMP.read_text(encoding="utf-8")
    except (OSError, ValueError):   # a file that isn't text is no stamp (UnicodeDecodeError is a ValueError)
        return ""


def _from_git(root: Path | None = None) -> str:
    root = root or Path(__file__).resolve().parent.parent
    if not (root / ".git").exists():   # a file, not a folder, in a worktree
        return ""
    try:
        r = subprocess.run(DESCRIBE, cwd=root, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


@functools.cache
def version() -> str:
    """This copy's version (see the module docstring). Never raises; at worst `__version__`."""
    return normalize(_stamped()) or normalize(_from_git()) or __version__
