"""#111 step 1: `clipper blinds`: sources, storage, the clue pass, pairing, the user's answer."""
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from clipper import blinds, cli, config, news, people
from clipper.errors import ClipperError

ROOT = Path(__file__).resolve().parents[1]

TOML = '''
[paths]
news = "news"
streams = "streams"

[[blinds.sources]]
name = "cdan"
feed = "https://cdan.example/feeds/posts/default"
kind = "blogger"
blind = "^Blind Item #"
reveal = "^Blind Items? Revealed"

[[blinds.sources]]
name = "bg"
feed = "https://bg.example/feed/"
kind = "wordpress"
'''


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "clipper.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "streams").mkdir()
    monkeypatch.setattr(blinds, "followed", lambda cfg: ["Jane Streamer"])
    return config.load(tmp_path / "clipper.toml", use_local=False)


def iso_days_ago(d: float, offset: str = "-08:00") -> str:
    t = datetime.now(UTC) - timedelta(days=d)
    return t.strftime("%Y-%m-%dT%H:%M:%S") + offset


def atom(*entries) -> bytes:
    """(title, published, html) entries in Blogger's shape."""
    body = "".join(
        f"<entry><id>tag:blogger.com,1999:blog-1.post-{i}</id><published>{p}</published>"
        f"<title type='text'>{t}</title><content type='html'>{escape(h)}</content>"
        f"<link rel='alternate' type='text/html' href='https://cdan.example/p/{i}.html'/></entry>"
        for i, (t, p, h) in enumerate(entries))
    return f"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>{body}</feed>".encode()


def rss(*items) -> bytes:
    """(title, pubDate, html) items in WordPress's shape."""
    body = "".join(
        f"<item><title>{t}</title><link>https://bg.example/{i}/</link><guid>https://bg.example/?p={i}</guid>"
        f"<pubDate>{p}</pubDate><content:encoded><![CDATA[{h}]]></content:encoded></item>"
        for i, (t, p, h) in enumerate(items))
    return ("<?xml version='1.0'?><rss xmlns:content='http://purl.org/rss/1.0/modules/content/'><channel>"
            f"{body}</channel></rss>").encode()


def rfc(d: float) -> str:
    return (datetime.now(UTC) - timedelta(days=d)).strftime("%a, %d %b %Y %H:%M:%S +0000")


class Fake:
    """llm.Claude's json_call: answers in order, records each request."""

    def __init__(self, *answers):
        self.answers, self.calls, self.cost = list(answers), [], 0.001

    def json_call(self, model, system, user, schema, effort, **k):
        self.calls.append({"model": model, "system": system, "user": user, "schema": schema})
        a = self.answers.pop(0) if self.answers else {"blinds": []}
        return (a(user) if callable(a) else a), {}


def clue_answer(n: int, **over) -> dict:
    return {"blinds": [{"i": i, "clues": [{"type": "profession", "text": "a streamer"},
                                          {"type": "event", "text": "after the lawsuit"}],
                        "claim": f"The blind claims thing {i}.", "fields": ["Streaming"],
                        "could_be_ours": i == 1, "sensitive": [], "private": False, **over}
                       for i in range(1, n + 1)]}


def feeds(monkeypatch, by_url: dict):
    seen = []

    def fetch(url, etag=None, modified=None):
        seen.append((url, etag))
        for pat, body in by_url.items():
            if pat in url:
                return (body(url) if callable(body) else body), "etag-1", "mod-1"
        raise AssertionError(url)
    monkeypatch.setattr(news, "fetch", fetch)
    return seen


TODAY = iso_days_ago(0)


def cdan_feed(day: str = TODAY):
    return atom(("Blind Item #16", day, "<p>This streamer was seen&nbsp;crying.</p><p>Line two.</p>"),
                ("Blind Items Revealed #4", day, "Blind #2 from May was Someone."),
                ("Your Turn - Sponsored", day, "comments"),
                ("Blind Item #2", day, "<b>A podcaster</b><br>quit his show."))


# ---------- sources and storage ----------

def test_blogger_entries_become_blinds_and_reveals_by_day_and_number(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": rss()})
    n = blinds.check(cfg, clues=False)
    assert n["new"] == 3  # two blinds and a reveal; "Your Turn" isn't one
    rows = {b["id"]: b for b in blinds.listing(cfg, everything=True)}
    day = TODAY[:10]
    assert set(rows) == {f"cdan-{day}-b16", f"cdan-{day}-b2", f"cdan-{day}-r4"}
    assert rows[f"cdan-{day}-r4"]["status"] == "reveal" and rows[f"cdan-{day}-b16"]["status"] == "unread"
    assert rows[f"cdan-{day}-b16"]["text"] == "This streamer was seen crying.\nLine two."
    assert rows[f"cdan-{day}-b2"]["text"] == "A podcaster\nquit his show."
    # The same feed again: nothing new. A blind whose text changed is updated, not added.
    assert blinds.check(cfg, clues=False)["new"] == 0
    feeds(monkeypatch, {"cdan.example": atom(("Blind Item #16", TODAY, "This streamer was seen crying. Answer: X")),
                        "bg.example": rss()})
    n = blinds.check(cfg, clues=False)
    assert (n["new"], n["edited"]) == (0, 1)
    assert "Answer: X" in blinds.one(cfg, f"cdan-{day}-b16")["text"]


def test_wordpress_ids_hash_the_guid(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": atom(), "bg.example": rss(("Blind Gossip: the singer", rfc(0), "<p>A singer...</p>"))})
    blinds.check(cfg, clues=False)
    (b,) = blinds.listing(cfg)
    assert re.fullmatch(r"bg-[0-9a-f]{10}", b["id"]) and b["source"] == "bg" and b["text"] == "A singer..."


def test_page_urls():
    feed = "https://cdan.example/feeds/posts/default"
    # Blogger ignores max-results without a start-index, so both are always there, ours.
    assert blinds.page_url(feed, "blogger", 0) == feed + "?max-results=150&start-index=1"
    assert blinds.page_url(feed, "blogger", 2) == feed + "?max-results=150&start-index=301"
    assert blinds.page_url(feed + "?alt=rss&max-results=50", "blogger", 1) == (
        feed + "?alt=rss&max-results=150&start-index=151")
    assert blinds.page_url("https://bg.example/feed/", "wordpress", 0) == "https://bg.example/feed/"
    assert blinds.page_url("https://bg.example/feed/", "wordpress", 1) == "https://bg.example/feed/?paged=2"
    assert blinds.page_url("https://x.example/rss", "rss", 0) == "https://x.example/rss"
    with pytest.raises(ClipperError, match="no pages"):
        blinds.page_url("https://x.example/rss", "rss", 1)


def test_backfill_pages_back_and_skips_the_etag(cfg, monkeypatch):
    def cdan(url):
        if "start-index=1&" in url or url.endswith("start-index=1"):
            return atom(("Blind Item #1", iso_days_ago(1), "new one"))
        if "start-index=151" in url:
            return atom(("Blind Item #1", iso_days_ago(10), "older"), ("Blind Item #2", iso_days_ago(40), "too old"))
        raise AssertionError("paged past the cutoff")

    seen = feeds(monkeypatch, {"cdan.example": cdan, "bg.example": rss()})
    n = blinds.check(cfg, backfill_days=30, clues=False)
    assert n["new"] == 2  # the 40-day-old one is outside the backfill
    assert all(etag is None for _, etag in seen)
    seen.clear()
    blinds.check(cfg, clues=False)  # a normal check sends the stored ETag, first page only
    assert [e for u, e in seen if "cdan" in u] == ["etag-1"] and len([u for u, _ in seen if "cdan" in u]) == 1


def test_a_source_that_fails_is_recorded_and_the_rest_go_on(cfg, monkeypatch):
    def broken(url):
        raise OSError("HTTP 500")

    feeds(monkeypatch, {"cdan.example": broken, "bg.example": rss(("Blind: x", rfc(0), "text"))})
    lines = []
    assert blinds.check(cfg, clues=False, say=lines.append)["new"] == 1
    report = {s["name"]: s for s in blinds.sources_report(cfg)}
    assert "HTTP 500" in report["cdan"]["error"] and report["bg"]["items"] == 1 and not report["bg"]["error"]


@pytest.mark.parametrize("entry, err", [
    ({"name": "Bad Name", "feed": "https://x"}, "lowercase"),
    ({"name": "hand", "feed": "https://x"}, "kept for blinds pasted by hand"),
    ({"name": "x", "feed": "ftp://x"}, "must be a URL"),
    ({"name": "x", "feed": "https://x", "kind": "atom"}, "kind must be"),
    ({"name": "x", "feed": "https://x", "blind": "("}, "bad pattern"),
])
def test_sources_are_checked(cfg, entry, err):
    cfg["blinds"]["sources"] = [entry]
    with pytest.raises(ClipperError, match=err):
        blinds.source_list(cfg)


def test_two_sources_with_one_name_are_refused(cfg):
    cfg["blinds"]["sources"] = [{"name": "x", "feed": "https://a"}, {"name": "x", "feed": "https://b"}]
    with pytest.raises(ClipperError, match="share a name"):
        blinds.source_list(cfg)


def test_no_sources_says_how_to_add_one(cfg):
    cfg["blinds"]["sources"] = []
    lines = []
    assert blinds.check(cfg, clues=False, say=lines.append)["new"] == 0
    assert "[[blinds.sources]]" in lines[0]


# ---------- the clue pass ----------

def test_the_clue_pass_reads_types_weights_and_flags(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": rss()})
    cfg["blinds"]["weights"] = {"event": 5}  # replaces the table: the rest keep the defaults
    ans = clue_answer(2)
    ans["blinds"][0]["clues"].append({"type": "made-up", "text": "dropped"})
    ans["blinds"][0]["sensitive"] = ["health", "nonsense"]
    ans["blinds"][1]["private"] = True
    fake = Fake(ans)
    n = blinds.check(cfg, claude=fake)
    assert n["read"] == 2 and n["paired"] == 0  # one source with blinds: nothing to pair
    (call,) = fake.calls
    assert call["model"] == "claude-haiku-5-5" and "Jane Streamer" in call["user"]
    assert "Blind Items Revealed" not in call["user"]  # reveals aren't clue-read
    first, second = sorted(blinds.listing(cfg, everything=True, status="waiting"), key=lambda b: b["id"], reverse=True)
    assert [c["type"] for c in first["clues"]] == ["profession", "event"]
    assert [c["weight"] for c in first["clues"]] == [1, 5]
    assert first["sensitive"] == ["health"] and first["ours"] and first["fields"] == ["streaming"]
    assert second["private"] and not second["ours"]
    assert {"sensitive", "sensitive:health", "ours", "source:cdan", "field:streaming"} <= set(first["tags"])


def test_a_refusal_or_a_blind_left_out_stays_unread(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": rss()})
    blinds.check(cfg, claude=Fake(None))
    assert {b["status"] for b in blinds.listing(cfg)} == {"unread"}
    blinds.check(cfg, claude=Fake({"blinds": [clue_answer(1)["blinds"][0]]}))
    assert sorted(b["status"] for b in blinds.listing(cfg)) == ["unread", "waiting"]
    db = blinds.connect(cfg)
    assert db.execute("SELECT COUNT(*) FROM blinds WHERE claim_token IS NOT NULL").fetchone()[0] == 0
    db.close()


def test_the_clue_prompt_never_asks_for_a_name(cfg):
    from clipper.prompts import Prompts

    text = Prompts(cfg).get("blinds_clues")
    assert "never name" in text.lower()
    assert "name" not in json.dumps(blinds.CLUES_SCHEMA["properties"]["blinds"]["items"]["required"])
    assert Prompts(cfg).get("blinds_pair")


def test_max_clues_caps_a_check(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": rss()})
    cfg["blinds"]["max_clues"] = 1
    fake = Fake(clue_answer(1))
    assert blinds.check(cfg, claude=fake)["read"] == 1
    assert len(fake.calls) == 1 and fake.calls[0]["user"].count("\n[") == 1


# ---------- pairing ----------

def test_twins_from_two_sources_become_one_event(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": atom(("Blind Item #3", iso_days_ago(2), "The singer and the yacht.")),
                        "bg.example": rss()})
    blinds.check(cfg, claude=Fake(clue_answer(1)))
    older = blinds.listing(cfg)[0]["id"]
    feeds(monkeypatch, {"cdan.example": atom(),
                        "bg.example": rss(("Blind: yacht", rfc(0), "That singer on the yacht again."))})
    fake = Fake(clue_answer(1), lambda user: {"pairs": [{"i": 1, "id": older, "why": "same yacht"},
                                                        {"i": 1, "id": "nope", "why": "x"}]})
    n = blinds.check(cfg, claude=fake)
    assert n["paired"] == 1
    assert f"<{older}>" in fake.calls[1]["user"]
    rows = blinds.listing(cfg)
    assert {b["pair"] for b in rows} == {f"e-{older}"}
    newer = next(b for b in rows if b["id"] != older)
    assert blinds.one(cfg, newer["id"])["twin"]["id"] == older


# ---------- by hand ----------

def test_a_blind_by_hand_gets_a_daily_number_and_its_clues(cfg):
    fake = Fake(clue_answer(1), clue_answer(1))
    a = blinds.add_by_hand(cfg, "Which streamer\nwas banned?", claude=fake)
    b = blinds.add_by_hand(cfg, "Another one", claude=fake)
    day = datetime.now(UTC).date().isoformat()
    assert (a, b) == (f"hand-{day}-1", f"hand-{day}-2")
    assert blinds.one(cfg, a)["text"] == "Which streamer\nwas banned?"
    assert blinds.one(cfg, a)["status"] == "waiting"
    with pytest.raises(ClipperError, match="empty"):
        blinds.add_by_hand(cfg, "  <p></p> ", clues=False)


# ---------- the answer ----------

def test_an_answer_matches_people_and_replaces_the_last(cfg, monkeypatch):
    bid = blinds.add_by_hand(cfg, "Which streamer?", clues=False)
    monkeypatch.setattr(people, "find", lambda cfg, name: ("jane", {"name": "Jane Streamer"}) if name == "jane" else None)
    b = blinds.answer(cfg, bid, "jane", why="the Vegas trip", who="cli")
    assert b["status"] == "answered"
    assert b["guesses"][0] | {"at": ""} == {"name": "Jane Streamer", "person": "jane", "as_written": "jane",
                                            "why": "the Vegas trip", "who": "cli", "at": ""}
    b = blinds.answer(cfg, bid, "Somebody Else")
    assert b["guesses"][0]["name"] == "Somebody Else" and b["guesses"][0]["person"] == ""
    assert [h["what"] for h in b["history"]] == ["answered", "replaced", "answered"]
    assert b["history"][1]["guesses"][0]["person"] == "jane"
    assert blinds.answer(cfg, bid, none=True)["status"] == "no_idea"
    b = blinds.answer(cfg, bid, not_ours=True)
    assert b["status"] == "not_ours" and b["guesses"] == []


def test_an_ambiguous_name_is_kept_as_written(cfg, monkeypatch):
    bid = blinds.add_by_hand(cfg, "Which one?", clues=False)

    def find(cfg, name):
        raise ClipperError("'Don' could be don, don-2: use the id")
    monkeypatch.setattr(people, "find", find)
    g = blinds.answer(cfg, bid, "Don")["guesses"][0]
    assert g["name"] == "Don" and g["person"] == "" and "could be" in g["note"]


def test_an_answer_needs_exactly_one_kind_and_a_blind(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": rss()})
    blinds.check(cfg, clues=False)
    bid = blinds.listing(cfg)[0]["id"]
    for kw in ({}, {"name": "x", "none": True}, {"none": True, "not_ours": True}):
        with pytest.raises(ClipperError, match="one of them"):
            blinds.answer(cfg, bid, **kw)
    reveal = blinds.listing(cfg, status="reveal")[0]["id"]
    with pytest.raises(ClipperError, match="a reveal"):
        blinds.answer(cfg, reveal, none=True)
    with pytest.raises(ClipperError, match="no blind"):
        blinds.answer(cfg, "nope", none=True)


# ---------- reading ----------

def test_hide_and_the_listing_filters(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": rss()})
    ans = clue_answer(2)
    ans["blinds"][0]["sensitive"] = ["health"]
    blinds.check(cfg, claude=Fake(ans))
    assert len(blinds.listing(cfg)) == 2  # reveals are left out of the default listing
    cfg["blinds"]["hide"] = ["Sensitive:Health"]
    rows = blinds.listing(cfg)
    assert len(rows) == 1 and not rows[0]["hidden"]
    assert len(blinds.listing(cfg, everything=True)) == 3
    blinds.answer(cfg, rows[0]["id"], not_ours=True)
    assert blinds.listing(cfg) == []
    assert len(blinds.listing(cfg, status="not_ours")) == 1
    with pytest.raises(ClipperError, match="status is one of"):
        blinds.listing(cfg, status="maybe")


def test_prune_forgets_old_blinds_but_keeps_answered_ones(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": atom(("Blind Item #1", iso_days_ago(400), "old"),
                                             ("Blind Item #2", iso_days_ago(400), "old, answered")),
                        "bg.example": rss()})
    db = blinds.connect(cfg)
    blinds.store(db, blinds.source_list(cfg)[0], blinds.entries(atom(
        ("Blind Item #1", iso_days_ago(400), "old"), ("Blind Item #2", iso_days_ago(400), "old, answered"))))
    db.commit()
    db.close()
    kept = next(b["id"] for b in blinds.listing(cfg, days=500) if b["id"].endswith("b2"))
    blinds.answer(cfg, kept, "Someone")
    blinds.check(cfg, clues=False)
    assert [b["id"] for b in blinds.listing(cfg, days=500, everything=True)] == [kept]


# ---------- the command ----------

def test_the_command_routes_and_dry_run_spends_nothing(cfg, monkeypatch, capsys):
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": rss()})
    args = cli.build_parser().parse_args(["blinds", "check", "--backfill", "30", "--dry-run"])
    args.fn(cfg, args)
    out = capsys.readouterr().out
    assert "2 blinds to read" in out and "claude-haiku-5-5" in out
    assert blinds.listing(cfg, everything=True) == []  # a dry run stores nothing
    args = cli.build_parser().parse_args(["blinds", "guess", "A singer?", "--no-clues"])
    args.fn(cfg, args)
    bid = blinds.listing(cfg)[0]["id"]
    args = cli.build_parser().parse_args(["blinds", "answer", bid, "--none"])
    args.fn(cfg, args)
    assert "no_idea" in capsys.readouterr().out


def test_blinds_never_reach_anything_published():
    """Guesses about real people are research only: no module but the command and the module
    itself reads them (step 2 adds the bot, serve and the war room, which only show them)."""
    allowed = {"blinds.py", "cli.py"}
    readers = sorted(p.name for p in (ROOT / "clipper").rglob("*.py")
                     if p.name not in allowed and re.search(r"\bblinds\b\s*(import|\.)|import blinds|blinds\.sqlite",
                                                            p.read_text(encoding="utf-8")))
    assert readers == []
