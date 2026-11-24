"""The MkDocs site is built from docs/wiki/. The wiki files stay GitHub-flavored."""

import re
from pathlib import Path

import pytest
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
    # The ids GitHub gives these headings, written out, not a copy of the rule.
    samples = {
        "Cut on the NAS (#51)": "cut-on-the-nas-51",
        "The walkthrough: `clippa setup`": "the-walkthrough-clippa-setup",
        "The providers' own numbers": "the-providers-own-numbers",
        "1. State files and their locks (`clippa/jsonstate.py`)":
            "1-state-files-and-their-locks-clippajsonstatepy",
        "2026-11-09: prompt v8 on Haiku": "2026-11-09-prompt-v8-on-haiku",
        "Something broke?": "something-broke",
        "Faces & covers": "faces--covers",
        "Ask a question \u2014 any time": "ask-a-question--any-time",
        "snake_case_key": "snake_case_key",
    }
    for heading, github in samples.items():
        assert github_slugify(heading, "-") == github, heading
        assert github_slugify(heading.replace("`", ""), "-") == github, heading


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
    # The GitHub-wiki note under the banner is marked wiki-only and does not ship.
    assert "wiki-only" not in out
    assert "This wiki mirrors" not in out
    assert "byvfx.github.io/clippa-docs" not in out
    assert '!!! tip "Tip"' in out


def test_wiki_only_block_is_dropped_and_a_mention_in_code_stays():
    text = (
        "See `<!-- wiki-only -->` in the source.\n\n"
        "<!-- wiki-only -->\n"
        "> [!NOTE]\n"
        "> Only on the wiki.\n"
        "<!-- /wiki-only -->\n\n"
        "After [Install](Install)\n"
    )
    out = convert_page("Install", text, {"Install": text}, "")
    assert "Only on the wiki" not in out
    assert "> [!" not in out
    assert "`<!-- wiki-only -->`" in out
    assert "\nAfter " in out
    assert "](install.md)" in out
    assert out.startswith("# Install\n")


def test_unclosed_wiki_only_block_fails():
    pages = {"Install": ""}
    with pytest.raises(RuntimeError, match="wiki-only"):
        convert_page("Install", "<!-- wiki-only -->\n> [!NOTE]\n> Hi\n", pages, "")
    with pytest.raises(RuntimeError, match="wiki-only"):
        convert_page("Install", "Before\n<!-- /wiki-only -->\n", pages, "")


def test_wiki_only_markers_inside_a_fence_stay():
    text = "```\n<!-- wiki-only -->\nkeep\n<!-- /wiki-only -->\n```\n\nAfter\n"
    out = convert_page("Install", text, {"Install": text}, "")
    assert "<!-- wiki-only -->" in out
    assert "keep" in out
    assert "\nAfter\n" in out


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


def test_generate_leaves_an_unchanged_page_alone(tmp_path: Path):
    """mkdocs serve watches docs-site/: rewriting every page on each build rebuilt forever."""
    import os

    wiki, docs = tmp_path / "wiki", tmp_path / "docs"
    wiki.mkdir()
    (docs / "assets").mkdir(parents=True)
    (docs / "assets" / "pipeline-diagram.html").write_text("x\n", encoding="utf-8")
    (wiki / "Home.md").write_text("Hello\n", encoding="utf-8")
    (wiki / "Install.md").write_text("Do the install.\n", encoding="utf-8")
    generate(docs, wiki)
    for page in docs.glob("*.md"):
        os.utime(page, ns=(1_000_000_000, 1_000_000_000))
    (wiki / "Install.md").write_text("Do it again.\n", encoding="utf-8")
    assert generate(docs, wiki) == 2
    assert (docs / "index.md").stat().st_mtime_ns == 1_000_000_000
    assert (docs / "install.md").stat().st_mtime_ns != 1_000_000_000


def test_a_tilde_fence_is_left_as_code():
    text = "~~~\n[Faces](Faces)\n<!-- wiki-only -->\n~~~\n"
    out = convert_page("Install", text, {"Install": text, "Faces": "x"}, "")
    assert "[Faces](Faces)" in out and "<!-- wiki-only -->" in out
