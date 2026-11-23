"""The MkDocs site is built from docs/wiki/. The wiki files stay GitHub-flavored."""

import re
from pathlib import Path

from conftest import ROOT
from test_wiki import _prose

from docs_site.convert import TITLES, convert_page, generate, github_slugify, slug_for

WIKI = ROOT / "docs" / "wiki"
LINK = re.compile(r"\]\(([^)\s]+)\)")
ITEM = re.compile(r"^ +- (.+): ([a-z0-9-]+)\.md\s*$", re.M)
GROUP = re.compile(r"^ +- ([^:\n]+):\s*$", re.M)
ADMONITION = re.compile(r'^!!! (\w+) "([^"]+)"$', re.M)
GROUPS = (
    "Start here",
    "On watch",
    "Connect",
    "Cuts and covers",
    "Research",
    "Working on Clippa",
)


def _pages():
    return {path.stem: path.read_text(encoding="utf-8") for path in WIKI.glob("*.md")}


def _diagram() -> str:
    return (ROOT / "docs-site" / "assets" / "pipeline-diagram.html").read_text(encoding="utf-8")


def test_slugify_matches_github_heading_ids():
    samples = [
        "Cut on the NAS (#51)",
        "The walkthrough: `clippa setup`",
        "The providers' own numbers",
        "1. State files and their locks (`clippa/jsonstate.py`)",
        "2026-11-09: prompt v8 on Haiku",
        "Something broke?",
    ]
    for heading in samples:
        github = re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")
        assert github_slugify(heading, "-") == github
        assert github_slugify(heading.replace("`", ""), "-") == github


def test_titles_cover_every_wiki_page():
    pages = {stem for stem in _pages() if not stem.startswith("_")}
    assert pages == set(TITLES)
    assert len({slug_for(stem) for stem in pages}) == len(pages)


def test_nav_lists_every_page_in_the_six_groups():
    text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    nav = text.split("nav:", 1)[1].split("plugins:", 1)[0]
    assert tuple(GROUP.findall(nav)) == GROUPS
    items = ITEM.findall(nav)
    assert len(items) == len(TITLES)
    by_slug = {slug_for(stem): (stem, title) for stem, title in TITLES.items()}
    for label, slug in items:
        stem, title = by_slug[slug]
        assert label == title, stem


def test_callout_keeps_its_label_and_a_blank_line_inside():
    text = "> [!NOTE]\n> One\n>\n> Two\n\nAfter\n"
    out = convert_page("Install", text, {"Install": text}, "")
    assert '!!! note "Note"' in out
    assert "\n    One\n\n    Two\n" in out
    assert out.endswith("\nAfter\n") or "\nAfter\n" in out
    assert out.startswith("# Install\n")


def test_code_external_and_anchor_links_stay():
    pages = {"Faces": "x", "Home": "x"}
    text = (
        "See `[Faces](Faces)` and [Faces](Faces#the-face) and [site](https://byvfx.github.io/clippa/) "
        "and [here](#local).\n"
    )
    out = convert_page("Home", text, pages, "")
    assert "`[Faces](Faces)`" in out
    assert "](faces.md#the-face)" in out
    assert "https://byvfx.github.io/clippa/" in out
    assert "](#local)" in out
    assert "template: home.html" in out


def test_home_drops_the_banner_and_uses_the_pipeline_figure():
    pages = _pages()
    out = convert_page("Home", pages["Home"], pages, _diagram())
    assert "banner-dark.png" not in out
    assert 'class="nw-pipeline"' in out
    assert "```mermaid" not in out
    assert "template: home.html" in out
    assert "\nhide:\n  - toc\n" in out
    assert '<table markdown="block">' in out
    body = out.split("---", 2)[-1]
    assert not body.lstrip().startswith("# ")


def test_overview_picture_follows_the_theme_toggle():
    pages = _pages()
    out = convert_page("Overview", pages["Overview"], pages, _diagram())
    assert 'class="nw-img-dark"' in out
    assert 'class="nw-img-light"' in out
    assert "overview-hero-dark.png" in out
    assert "overview-hero-light.png" in out
    assert "<picture>" not in out


def test_every_wiki_page_converts_without_losing_a_link():
    pages = _pages()
    diagram = _diagram()
    slugs = {slug_for(stem) for stem in pages if not stem.startswith("_")}
    for stem, text in pages.items():
        if stem.startswith("_"):
            continue
        out = convert_page(stem, text, pages, diagram)
        prose = _prose(out)
        assert "> [!" not in prose, stem
        for line in ADMONITION.findall(out):
            kind, label = line
            assert kind in {"note", "tip", "important", "warning", "caution"}, (stem, line)
            assert label, stem
        for target in LINK.findall(prose):
            if re.match(r"(https?|mailto):", target) or target.startswith("#"):
                continue
            assert re.fullmatch(r"[a-z0-9-]+\.md(#[\w-]*)?", target), f"{stem}: {target}"
            assert target.split("#", 1)[0][:-3] in slugs, f"{stem}: {target}"
        if stem != "Home":
            assert out.startswith(f"# {TITLES[stem]}\n") or out.startswith("# Fetching VODs\n")


def test_generate_writes_pages_and_leaves_the_wiki_alone(tmp_path: Path):
    wiki = tmp_path / "wiki"
    docs = tmp_path / "docs"
    wiki.mkdir()
    (docs / "assets").mkdir(parents=True)
    (wiki / "Home.md").write_text("Hello [Install](Install)\n\n> [!TIP]\n> Go.\n", encoding="utf-8")
    (wiki / "Install.md").write_text("Do the install.\n", encoding="utf-8")
    (wiki / "_Sidebar.md").write_text("- [Home](Home)\n", encoding="utf-8")
    (docs / "assets" / "pipeline-diagram.html").write_text(
        '<div class="nw-pipeline">x</div>\n', encoding="utf-8"
    )
    before = {path.name: path.read_bytes() for path in wiki.iterdir()}
    assert generate(docs, wiki) == 2
    assert {path.name: path.read_bytes() for path in wiki.iterdir()} == before
    assert (docs / "index.md").is_file()
    assert (docs / "install.md").is_file()
    assert not (docs / "_sidebar.md").exists()
    assert "install.md" in (docs / "index.md").read_text(encoding="utf-8")
    # A second build replaces generated pages instead of keeping a stale one.
    (wiki / "Install.md").unlink()
    assert generate(docs, wiki) == 1
    assert not (docs / "install.md").exists()
