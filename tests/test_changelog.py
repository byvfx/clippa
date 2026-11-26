"""CHANGELOG.md keeps the shape the release workflow (.github/workflows/release.yml) and the wiki's
Developing#changelog rely on: Unreleased first, then each release newest first with its date, the
current version's section on top with a lead (the release's notes) before its first ### block."""
import re

from conftest import ROOT

from clippa import __version__


def sections() -> list[tuple[str, str]]:
    """[(heading, body)] for each `## ` section; a `### ` line doesn't start one."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return [(head, body) for head, _, body in (p.partition("\n") for p in re.split(r"^## ", text, flags=re.M)[1:])]


def test_unreleased_first_then_releases_with_dates():
    heads = [h for h, _ in sections()]
    assert heads[0] == "Unreleased"
    assert all(re.fullmatch(r"\d+\.\d+\.\d+\S* \(\d{4}-\d\d-\d\d\)", h) for h in heads[1:]), heads


def test_the_current_version_is_the_newest_release():
    """`__version__` is the last release's number: bumping it needs its section, and the other way."""
    heads = [h for h, _ in sections()]
    assert heads[1].split()[0] == __version__, f"CHANGELOG.md's newest release is {heads[1]!r}, __version__ is {__version__}"


def test_the_current_release_has_notes_before_its_first_month_block():
    """What the GitHub Release shows: the lines before the first `###` of the section."""
    body = sections()[1][1]
    lead = body.split("\n### ", 1)[0]
    assert lead.strip(), "write the release's notes (a short lead and highlights) before its first ### block"
    assert len(lead.splitlines()) < 40, "the lead is the release page: keep it short, detail goes under ###"


def test_no_merge_conflict_marker_is_left_in_it():
    """A rebase that resolved this file by hand can leave a `<<<<<<<` behind, and the release
    workflow and every reader then see it (#440 shipped one)."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    markers = [f"{n}: {line}" for n, line in enumerate(text.splitlines(), 1)
               if re.match(r"^(<{7}|={7}|>{7})( |$)", line)]
    assert not markers, markers

