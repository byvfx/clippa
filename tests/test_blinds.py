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
        f"<entry><id>tag:blogger.com,1999:blog-1.post-{abs(hash((t, p))) % 10**9}</id><published>{p}</published>"
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
    # Blogger ignores max-results without a start-index, so both are always there, ours, and
    # newest-published first (its default order can be by last update).
    q = "orderby=published&max-results=150&start-index="
    assert blinds.page_url(feed, "blogger", 0) == f"{feed}?{q}1"
    assert blinds.page_url(feed, "blogger", 2) == f"{feed}?{q}301"
    assert blinds.page_url(feed + "?alt=rss&max-results=50&orderby=updated", "blogger", 1) == f"{feed}?alt=rss&{q}151"
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


def test_paging_goes_on_past_an_old_post_until_a_page_is_wholly_older(cfg, monkeypatch):
    pages = {"start-index=1&": atom(("Blind Item #1", iso_days_ago(1), "new"), ("Blind Item #9", iso_days_ago(300), "edited old")),
             "start-index=151": atom(("Blind Item #2", iso_days_ago(20), "still inside")),
             "start-index=301": atom(("Blind Item #3", iso_days_ago(50), "older")),
             "start-index=451": AssertionError}

    def cdan(url):
        for k, body in pages.items():
            if k in url + "&":
                if body is AssertionError:
                    raise AssertionError("paged past a wholly older page")
                return body
        raise AssertionError(url)

    feeds(monkeypatch, {"cdan.example": cdan, "bg.example": rss()})
    assert blinds.check(cfg, backfill_days=30, clues=False)["new"] == 2


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


def test_a_refused_batch_is_asked_one_by_one_and_gives_up_after_three_tries(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": rss()})

    def picky(user):  # refuses any call that holds the crying blind
        return None if "crying" in user else clue_answer(1)
    fake = Fake(*[picky] * 20)
    lines = []
    assert blinds.check(cfg, claude=fake, say=lines.append)["read"] == 1
    assert len(fake.calls) == 3  # the batch, then each blind alone
    statuses = {b["id"][-3:]: b["status"] for b in blinds.listing(cfg)}
    assert statuses == {"b16": "unread", "-b2": "waiting"}
    for _ in range(2):
        blinds.check(cfg, claude=fake, say=lines.append)
    (b,) = [b for b in blinds.listing(cfg) if b["id"].endswith("b16")]
    assert b["status"] == "waiting" and b["clues"] == [] and b["clue_model"] == "refused"
    assert any("not read after 3 tries" in x for x in lines)
    calls = len(fake.calls)
    blinds.check(cfg, claude=fake)
    assert len(fake.calls) == calls  # given up on: not asked again


def test_a_backfill_reads_past_max_clues(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": rss()})
    cfg["blinds"]["max_clues"] = 1
    fake = Fake(clue_answer(1), clue_answer(1))
    assert blinds.check(cfg, backfill_days=30, claude=fake)["read"] == 2


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
    # The older twin pruned: the newer one is unpaired, not left pointing at nothing.
    db = blinds.connect(cfg)
    db.execute("UPDATE blinds SET published = '2020-01-01T00:00:00+00:00' WHERE id = ?", (older,))
    db.commit()
    blinds.prune(cfg, db)
    db.close()
    assert blinds.one(cfg, newer["id"])["pair"] is None


def test_pairing_that_fails_is_asked_again_and_a_backfill_pairs_old_twins(cfg, monkeypatch):
    # Twins from 20 days ago, read by a backfill: compared around their own dates, not today's.
    feeds(monkeypatch, {"cdan.example": atom(("Blind Item #3", iso_days_ago(20), "The singer and the yacht.")),
                        "bg.example": rss(("Blind: yacht", rfc(20), "That singer on the yacht again."))})

    def down(user):
        raise RuntimeError("API down")
    with pytest.raises(RuntimeError):  # the clues are read and kept; pairing fails
        blinds.check(cfg, backfill_days=30, claude=Fake(clue_answer(2), down))
    rows = blinds.listing(cfg, days=30)
    assert {b["status"] for b in rows} == {"waiting"} and {b["pair"] for b in rows} == {None}
    cdan = next(b["id"] for b in rows if b["source"] == "cdan")
    fake = Fake(lambda user: {"pairs": [{"i": 1, "id": cdan, "why": "same yacht"}]})
    assert blinds.check(cfg, claude=fake)["paired"] == 1  # asked again, with no clue call
    assert len(fake.calls) == 1 and f"<{cdan}>" in fake.calls[0]["user"]
    assert blinds.check(cfg, claude=Fake())["paired"] == 0  # compared: not asked a third time


def test_apply_pairs_takes_only_two_unpaired_blinds_close_in_time(cfg):
    db = blinds.connect(cfg)
    for bid, days in (("a", 0), ("b", 1), ("c", 40), ("d", 2)):
        db.execute("INSERT INTO blinds (id, source, kind, published, status) VALUES (?, 'x', 'blind', ?, 'waiting')",
                   (bid, news.iso(news.now() - timedelta(days=days))))
    db.execute("UPDATE blinds SET pair = 'e-zz' WHERE id = 'd'")  # paired by another check meanwhile
    rows = {r["id"]: r for r in db.execute("SELECT * FROM blinds")}
    two_weeks = timedelta(days=14)
    assert blinds.apply_pairs(db, [rows["a"]], {"c": rows["c"], "d": rows["d"]},
                              [{"i": 1, "id": "c"}, {"i": 1, "id": "d"}], two_weeks) == 0  # too far apart; taken
    assert blinds.apply_pairs(db, [rows["a"]], {"b": rows["b"]}, [{"i": 1, "id": "b"}], two_weeks) == 1
    assert [r[0] for r in db.execute("SELECT pair FROM blinds WHERE id IN ('a', 'b', 'd') ORDER BY id")] == [
        "e-b", "e-b", "e-zz"]
    db.close()


def test_an_older_store_gets_the_paired_at_column(cfg):
    import sqlite3

    old = sqlite3.connect(blinds.home(cfg) / "blinds.sqlite")
    old.executescript(blinds.SCHEMA.replace(",\n    paired_at TEXT", ""))
    old.close()
    db = blinds.connect(cfg)
    assert "paired_at" in {r["name"] for r in db.execute("PRAGMA table_info(blinds)")}
    db.close()


def test_a_blind_answered_before_its_clues_still_gets_them(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": rss()})
    blinds.check(cfg, clues=False)
    first, second = (b["id"] for b in blinds.listing(cfg, status="unread"))
    blinds.answer(cfg, first, none=True)
    blinds.answer(cfg, second, not_ours=True)  # not one to look into: its clues aren't read
    fake = Fake(clue_answer(1))
    assert blinds.check(cfg, claude=fake)["read"] == 1
    b = blinds.one(cfg, first)
    assert b["status"] == "no_idea" and b["clues"] and blinds.one(cfg, second)["clued_at"] is None


def test_a_backfill_goes_on_past_a_refusal_and_tries_it_once(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": rss()})
    cfg["blinds"]["max_clues"] = 1
    fake = Fake(*[lambda user: None if "crying" in user else clue_answer(1)] * 5)
    assert blinds.check(cfg, backfill_days=30, claude=fake)["read"] == 1
    assert len(fake.calls) == 2  # each blind once: the refused one waits for the next check
    db = blinds.connect(cfg)
    assert db.execute("SELECT clue_tries FROM blinds WHERE text LIKE '%crying%'").fetchone()[0] == 1
    db.close()


def test_a_blind_by_hand_reads_only_itself(cfg, monkeypatch):
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": rss()})
    blinds.check(cfg, clues=False)
    fake = Fake(clue_answer(1))
    bid = blinds.add_by_hand(cfg, "Which singer was crying on the yacht?", claude=fake)
    assert len(fake.calls) == 1 and fake.calls[0]["user"].count("[") == 1 and "yacht" in fake.calls[0]["user"]
    assert blinds.one(cfg, bid)["status"] == "waiting" and len(blinds.listing(cfg, status="unread")) == 2


def test_a_blind_by_hand_is_never_paired_or_a_reason_to_pair(cfg, monkeypatch):
    blinds.add_by_hand(cfg, "The singer and the yacht.", claude=Fake(clue_answer(1)))
    feeds(monkeypatch, {"cdan.example": atom(("Blind Item #3", TODAY, "That singer on the yacht.")), "bg.example": rss()})
    fake = Fake(clue_answer(1))
    assert blinds.check(cfg, claude=fake)["paired"] == 0
    assert len(fake.calls) == 1  # the clue pass only: no pairing call


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
    db = blinds.connect(cfg)  # a gap in the numbers (or a paste taken meanwhile) skips to a free one
    db.execute("UPDATE blinds SET id = ? WHERE id = ?", (f"hand-{day}-5", a))
    db.execute("INSERT INTO blinds (id, source, kind, day, text, status) VALUES (?, 'hand', 'blind', 'other', 'x', 'unread')",
               (f"hand-{day}-1",))
    db.commit()
    db.close()
    assert blinds.add_by_hand(cfg, "Third", clues=False) == f"hand-{day}-3"


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
    allowed = {"blinds.py", "cli.py", "doctor.py"}  # doctor reads the source settings only
    readers = sorted(p.name for p in (ROOT / "clipper").rglob("*.py")
                     if p.name not in allowed and re.search(r"\bblinds\b\s*(import|\.)|import blinds|blinds\.sqlite",
                                                            p.read_text(encoding="utf-8")))
    assert readers == []


def test_doctor_checks_the_sources(cfg, monkeypatch):
    from clipper import doctor

    seen = []
    monkeypatch.setattr(doctor, "ok", lambda area, what, hint="": seen.append(("ok", area, what)))
    monkeypatch.setattr(doctor, "fail", lambda area, what, hint="": seen.append(("fail", area, what)))
    monkeypatch.setattr(doctor, "warn", lambda area, what, hint="": seen.append(("warn", area, what)))
    doctor.check_blinds(cfg, offline=True)
    assert seen == [("ok", "blinds", "2 source(s): cdan, bg")]
    seen.clear()
    feeds(monkeypatch, {"cdan.example": cdan_feed(), "bg.example": lambda url: b"not xml"})
    doctor.check_blinds(cfg, offline=False)
    assert seen[0][0] == "warn" and "bg: ParseError" in seen[0][2]
    seen.clear()
    cfg["blinds"]["sources"] = [{"name": "x", "feed": "https://x", "kind": "atom"}]
    doctor.check_blinds(cfg, offline=True)
    assert seen[0][0] == "fail" and "kind must be" in seen[0][2]
    seen.clear()
    cfg["blinds"]["sources"] = []
    doctor.check_blinds(cfg, offline=True)
    assert seen == []
