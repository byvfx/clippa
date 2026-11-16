"""`clipper blinds` (#111, step 1): collect gossip blind items, read their clues, pair twins.

    clipper blinds check [--backfill DAYS] [--no-clues] [--dry-run]
                                    fetch each source, store new blinds and reveals, read the
                                    clues of what is unread, pair twins across sources
    clipper blinds list [--days 14] [--status S] [--source NAME] [--all] [--json]
    clipper blinds show <blind> [--json]
    clipper blinds guess "<text>" [--no-clues]   a blind from anywhere else, by hand
    clipper blinds answer <blind> "<name>" [--why "..."] | --none | --not-ours
    clipper blinds sources          each source: last fetch, error, how many stored

A blind is a gossip item with the name left out and clues left in. The user is asked first
who it is about (an answer here; Discord and the war room in step 2), and only then does
clipper investigate (step 3); reveals score both guesses (step 4). This step collects them.

Sources are [[blinds.sources]] entries, none by default: name, feed, kind (the parser:
"blogger", "wordpress", "rss"), and the title patterns that pick blinds (`blind`) and reveals
(`reveal`) out of the feed; anything else in the feed is skipped. A Blogger blog numbers its
blinds afresh each day ("Blind Item #16"), so such a blind's id is its source, date and number.
`--backfill DAYS` pages back through a feed (Blogger's start-index, WordPress's paged).

The clue pass ([blinds] clues_model, prompts/blinds_clues_<v>.md) reads each unread blind
and returns its clues (each typed, weighted by [blinds] weights), the claim, the fields it
points at, whether it could be about someone this install follows (`clipper people` and
the news watchlist), and flags for a sensitive claim or a private person. It never guesses
a name: the user's guess comes first and must be their own. Tags (source, field, sensitive,
private, ours) are what [blinds] hide filters on. Twins from different sources within
pair_days are paired (prompts/blinds_pair_<v>.md) so they are asked, and later answered, as
one event.

Everything is in [paths] news/blinds/blinds.sqlite. The blind's text is kept (it is short
and it is the input); linked articles are never fetched, and nothing here is ever published.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import news
from .errors import ClipperError

KINDS = ("blogger", "wordpress", "rss")
CLUE_TYPES = ("profession", "platform", "fame", "relationship", "when", "where", "event", "hint", "other")
SENSITIVE = ("crime", "health", "sexuality", "abuse", "minors")
STATUSES = ("unread", "waiting", "answered", "no_idea", "not_ours", "reveal")
TEXT_MAX = 6000
PAGE = 150            # Blogger's largest page
CLUE_BATCH = 10
MAX_TRIES = 3         # a blind the clue pass refuses or skips this often is asked about without clues
PAIR_POOL = 60
A = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"

SCHEMA = """
CREATE TABLE IF NOT EXISTS blinds (id TEXT PRIMARY KEY, source TEXT, kind TEXT, title TEXT, url TEXT,
    published TEXT, day TEXT, seen_at TEXT, text TEXT, edited_at TEXT, status TEXT,
    clues TEXT, claim TEXT, fields TEXT, ours INTEGER, sensitive TEXT, private INTEGER, clued_at TEXT,
    clue_model TEXT, clue_tries INTEGER DEFAULT 0, pair TEXT, pair_why TEXT, guesses TEXT, history TEXT, claim_token TEXT, claimed_at TEXT,
    paired_at TEXT);
CREATE INDEX IF NOT EXISTS blinds_status ON blinds(status);
CREATE INDEX IF NOT EXISTS blinds_published ON blinds(published);
CREATE TABLE IF NOT EXISTS sources (name TEXT PRIMARY KEY, etag TEXT, modified TEXT, fetched_at TEXT,
    error TEXT, items INTEGER DEFAULT 0);
"""

CLUES_SCHEMA = {
    "type": "object",
    "properties": {"blinds": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "i": {"type": "integer"},
            "clues": {"type": "array", "items": {
                "type": "object",
                "properties": {"type": {"type": "string", "enum": list(CLUE_TYPES)}, "text": {"type": "string"}},
                "required": ["type", "text"], "additionalProperties": False}},
            "claim": {"type": "string"},
            "fields": {"type": "array", "items": {"type": "string"}},
            "could_be_ours": {"type": "boolean"},
            "sensitive": {"type": "array", "items": {"type": "string", "enum": list(SENSITIVE)}},
            "private": {"type": "boolean"},
        },
        "required": ["i", "clues", "claim", "fields", "could_be_ours", "sensitive", "private"],
        "additionalProperties": False}}},
    "required": ["blinds"],
    "additionalProperties": False,
}

PAIR_SCHEMA = {
    "type": "object",
    "properties": {"pairs": {"type": "array", "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"}, "id": {"type": "string"}, "why": {"type": "string"}},
        "required": ["i", "id", "why"], "additionalProperties": False}}},
    "required": ["pairs"],
    "additionalProperties": False,
}


# ---------- storage ----------

def home(cfg: dict) -> Path:
    d = news.home(cfg) / "blinds"
    d.mkdir(parents=True, exist_ok=True)
    return d


def connect(cfg: dict) -> sqlite3.Connection:
    db = sqlite3.connect(home(cfg) / "blinds.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    if "paired_at" not in {r["name"] for r in db.execute("PRAGMA table_info(blinds)")}:  # a store from before it
        db.execute("ALTER TABLE blinds ADD COLUMN paired_at TEXT")
        db.commit()
    return db


def as_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in ("clues", "fields", "sensitive", "guesses", "history"):
        d[k] = json.loads(d[k]) if d.get(k) else []
    d["ours"], d["private"] = bool(d["ours"]), bool(d["private"])
    for k in ("claim_token", "claimed_at", "clue_tries", "paired_at"):
        d.pop(k, None)
    return d


def get(db: sqlite3.Connection, bid: str) -> dict:
    row = db.execute("SELECT * FROM blinds WHERE id = ?", (bid,)).fetchone()
    if row is None:
        hits = db.execute("SELECT id FROM blinds WHERE id LIKE ? ORDER BY id LIMIT 3", (f"%{bid}%",)).fetchall()
        more = f" (did you mean {', '.join(h['id'] for h in hits)}?)" if hits else ""
        raise ClipperError(f"no blind {bid!r}{more}")
    return as_dict(row)


# ---------- sources ----------

def source_list(cfg: dict) -> list[dict]:
    """The configured sources, checked: a name, a feed, a known kind, patterns that compile."""
    out = []
    for s in cfg["blinds"]["sources"]:
        name, feed, kind = s.get("name", ""), s.get("feed", ""), s.get("kind", "rss")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name or ""):
            raise ClipperError(f"[[blinds.sources]] name {name!r}: lowercase letters, digits, - and _ (it starts each id)")
        if name == "hand":
            raise ClipperError("[[blinds.sources]] name 'hand' is kept for blinds pasted by hand")
        if not feed.startswith(("http://", "https://")):
            raise ClipperError(f"[[blinds.sources]] {name}: feed must be a URL")
        if kind not in KINDS:
            raise ClipperError(f"[[blinds.sources]] {name}: kind must be one of {', '.join(KINDS)}")
        try:
            blind, reveal = re.compile(s.get("blind") or "(?i)blind"), re.compile(s.get("reveal") or "(?i)reveal")
        except re.error as e:
            raise ClipperError(f"[[blinds.sources]] {name}: bad pattern ({e})") from None
        out.append({"name": name, "feed": feed, "kind": kind, "blind": blind, "reveal": reveal})
    if len({s["name"] for s in out}) != len(out):
        raise ClipperError("[[blinds.sources]]: two sources share a name")
    return out


def page_url(feed: str, kind: str, page: int) -> str:
    """The feed's page-th page, 0 first. Blogger newest-published first at its largest page size
    with start-index, all set here over any in the feed's own query (Blogger ignores max-results without a
    start-index: 25 posts, then the second page would skip the rest); WordPress by paged; an
    rss source has only its one page."""
    parts = urlsplit(feed)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if kind == "blogger":
        query = [(k, v) for k, v in query if k not in ("max-results", "start-index")]
        query = [(k, v) for k, v in query if k != "orderby"]
        query += [("orderby", "published"), ("max-results", str(PAGE)), ("start-index", str(1 + PAGE * page))]
    elif page and kind == "wordpress":
        query = [(k, v) for k, v in query if k != "paged"] + [("paged", str(page + 1))]
    elif page:
        raise ClipperError("an rss source has no pages to go back through")
    return urlunsplit(parts._replace(query=urlencode(query)))


def pages(src: dict, since: datetime | None, max_pages: int, etag: str | None = None,
          modified: str | None = None):
    """(page, entries, etag, modified) per fetched page: the first only, or with since back to
    then (at most max_pages). The first page is fetched with etag/modified, so an unchanged
    feed yields nothing, and paging stops at a page wholly older than since, or with nothing
    not seen on an earlier one (a feed past its end may repeat its last page)."""
    seen: set[str] = set()
    for page in range(max_pages if since else 1):
        body, et, mod = news.fetch(page_url(src["feed"], src["kind"], page),
                                   *((etag, modified) if page == 0 else (None, None)))
        if body is None:
            return
        items = entries(body)
        uids = {e["uid"] or e["url"] for e in items}
        if page and not uids - seen:
            return
        seen |= uids
        yield page, items, et, mod
        # Past the start once a whole page is older (robust to a post edited out of order).
        newest = max((w for w in (news.when(e["published"]) for e in items) if w), default="")
        if not since or not items or src["kind"] == "rss" or (newest and newest < news.iso(since)):
            return


def entries(body: bytes) -> list[dict]:
    """RSS 2.0 or Atom -> [{uid, url, title, published (as written), html}], full content kept."""
    root = ET.fromstring(body)
    out = []
    for it in root.iter("item"):
        out.append({"uid": (it.findtext("guid") or it.findtext("link") or "").strip(),
                    "url": (it.findtext("link") or "").strip(),
                    "title": news.clean(it.findtext("title"), 300),
                    "published": (it.findtext("pubDate") or "").strip(),
                    "html": it.findtext(CONTENT) or it.findtext("description") or ""})
    for e in root.iter(f"{A}entry"):
        link = next((x.get("href") for x in e.findall(f"{A}link") if x.get("rel", "alternate") == "alternate"), "")
        content = e.find(f"{A}content")
        summary = e.find(f"{A}summary")
        out.append({"uid": (e.findtext(f"{A}id") or link or "").strip(), "url": (link or "").strip(),
                    "title": news.clean(e.findtext(f"{A}title"), 300),
                    "published": (e.findtext(f"{A}published") or e.findtext(f"{A}updated") or "").strip(),
                    "html": "".join(content.itertext()) if content is not None
                    else "".join(summary.itertext()) if summary is not None else ""})
    return [x for x in out if x["title"] and (x["uid"] or x["url"])]


def text_of(html_text: str) -> str:
    """The post's words: tags out, entities read, paragraphs kept as line breaks."""
    t = re.sub(r"(?i)<\s*(br|/p|/div|/li)\b[^>]*>", "\n", html_text or "")
    t = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(t)))
    lines = [" ".join(x.split()) for x in t.splitlines()]
    return "\n".join(x for x in lines if x).strip()[:TEXT_MAX]


def day_of(published: str) -> str:
    """The post's own calendar day, as the source wrote it (Blogger's numbering restarts on it)."""
    m = re.match(r"\d{4}-\d{2}-\d{2}", published or "")
    if m:
        return m.group(0)
    iso_t = news.when(published)
    return iso_t[:10] if iso_t else news.now().date().isoformat()


def classify(src: dict, title: str) -> str | None:
    """"reveal", "blind" or None (anything else in the feed). Reveals first: "Blind Items
    Revealed" matches both."""
    if src["reveal"].search(title):
        return "reveal"
    if src["blind"].search(title):
        return "blind"
    return None


def blind_id(src: dict, kind: str, e: dict) -> str:
    """Blogger: source-day-b16 / -r4 (numbers restart each day, blinds and reveals apart);
    else the source and a hash of the entry's id."""
    m = re.search(r"#\s*(\d+)", e["title"])
    if src["kind"] == "blogger" and m:
        return f"{src['name']}-{day_of(e['published'])}-{kind[0]}{int(m.group(1))}"
    return f"{src['name']}-{hashlib.sha1((e['uid'] or e['url']).encode()).hexdigest()[:10]}"


def store(db: sqlite3.Connection, src: dict, items: list[dict], since: datetime | None = None) -> tuple[int, int]:
    """New blinds and reveals from one source -> (new, edited). An entry seen before whose text
    changed is updated (an answer added to a blind later is how some sources reveal)."""
    t, new, edited = news.iso(news.now()), 0, 0
    for e in items:
        kind = classify(src, e["title"])
        if kind is None:
            continue
        published = news.when(e["published"]) or t
        if since and datetime.fromisoformat(published) < since:
            continue
        bid, text = blind_id(src, kind, e), text_of(e["html"])
        if not text:
            continue
        # OR IGNORE: a check by hand and serve's can store the same post at once.
        if db.execute("INSERT OR IGNORE INTO blinds (id, source, kind, title, url, published, day, seen_at, text, "
                      "status, guesses, history) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]')",
                      (bid, src["name"], kind, e["title"], e["url"], published, day_of(e["published"]), t, text,
                       "reveal" if kind == "reveal" else "unread")).rowcount:
            new += 1
        elif db.execute("UPDATE blinds SET text = ?, edited_at = ? WHERE id = ? AND text != ?",
                        (text, t, bid, text)).rowcount:
            edited += 1
    return new, edited


def fetch_source(db: sqlite3.Connection, src: dict, backfill_days: float = 0, max_pages: int = 20,
                 say=print) -> tuple[int, int]:
    """One source's feed (with ETag), or with backfill_days its pages back to then."""
    since = news.now() - timedelta(days=backfill_days) if backfill_days else None
    row = db.execute("SELECT * FROM sources WHERE name = ?", (src["name"],)).fetchone()
    new = edited = 0
    try:
        tags = (row["etag"], row["modified"]) if row and not since else (None, None)
        for page, items, etag, modified in pages(src, since, max_pages, *tags):
            n, ed = store(db, src, items, since)
            new, edited = new + n, edited + ed
            if page == 0:
                db.execute("INSERT INTO sources (name, etag, modified) VALUES (?, ?, ?) ON CONFLICT(name) DO "
                           "UPDATE SET etag = excluded.etag, modified = excluded.modified", (src["name"], etag, modified))
            db.commit()  # before the next page is fetched: no write lock held over the network
        error = ""
    except Exception as e:  # noqa: BLE001  one source down never stops the others
        error = f"{type(e).__name__}: {e}"[:300]
        say(f"  {src['name']}: {error}")
    count = db.execute("SELECT COUNT(*) FROM blinds WHERE source = ?", (src["name"],)).fetchone()[0]
    db.execute("INSERT INTO sources (name, fetched_at, error, items) VALUES (?, ?, ?, ?) ON CONFLICT(name) DO "
               "UPDATE SET fetched_at = excluded.fetched_at, error = excluded.error, items = excluded.items",
               (src["name"], news.iso(news.now()), error, count))
    db.commit()
    return new, edited


# ---------- the clue pass ----------

def followed(cfg: dict) -> list[str]:
    """Who this install follows, by name: `clipper people` and the news watchlist."""
    from . import people

    out: dict[str, str] = {}
    try:
        for rec in people.load(cfg).values():
            out.setdefault(rec["name"].lower(), rec["name"])
    except (OSError, ValueError):  # a machine without the share
        pass
    for w in news.watchlist(cfg).values():
        out.setdefault(w.name.lower(), w.name)
    return sorted(out.values(), key=str.lower)


def weights(cfg: dict) -> dict[str, float]:
    """[blinds] weights over the defaults (a section's table replaces the whole default)."""
    from .config import DEFAULTS

    return {**DEFAULTS["blinds"]["weights"], **cfg["blinds"]["weights"]}


def clues_request(rows: list, names: list[str]) -> str:
    who = "\n".join(f"- {n}" for n in names) or "(nobody: could_be_ours is always false)"
    items = "\n\n".join(f"[{i}] {r['title']} ({r['source']}, {r['day']})\n{r['text']}" for i, r in enumerate(rows, 1))
    return f"<followed>\n{who}\n</followed>\n\n<blinds>\n{items}\n</blinds>"


def apply_clues(db: sqlite3.Connection, rows: list, out: dict, w: dict[str, float], model: str) -> list[str]:
    """The clue pass's answer for rows -> the ids it covered (a blind it left out stays unread)."""
    t, done = news.iso(news.now()), []
    by_i = {b["i"]: b for b in out.get("blinds", []) if isinstance(b.get("i"), int)}
    for i, r in enumerate(rows, 1):
        b = by_i.get(i)
        if b is None:
            continue
        clues = [{"type": c["type"], "text": c["text"].strip(), "weight": w.get(c["type"], 1.0)}
                 for c in b.get("clues", []) if c.get("type") in CLUE_TYPES and (c.get("text") or "").strip()]
        db.execute("UPDATE blinds SET clues = ?, claim = ?, fields = ?, ours = ?, sensitive = ?, private = ?, "
                   "clued_at = ?, clue_model = ?, status = CASE WHEN status = 'unread' THEN 'waiting' ELSE status END "
                   "WHERE id = ?",
                   (json.dumps(clues), (b.get("claim") or "").strip()[:500],
                    json.dumps(sorted({f.strip().lower() for f in b.get("fields", []) if f.strip()})[:6]),
                    int(bool(b.get("could_be_ours"))),
                    json.dumps(sorted({s for s in b.get("sensitive", []) if s in SENSITIVE})),
                    int(bool(b.get("private"))), t, model, r["id"]))
        done.append(r["id"])
    return done


# Blinds whose clues are still to read: unread, or answered before the clue pass got to them (not
# ones you said aren't ours). The clue pass and the dry run's price both use it.
TO_READ = "kind = 'blind' AND clued_at IS NULL AND status IN ('unread', 'answered', 'no_idea')"


def claim_unread(db: sqlite3.Connection, limit: int, only: list[str] | None = None,
                 skip: set[str] | None = None) -> tuple[str, list]:
    """Claim up to limit blinds still to read for this pass (a claim over half an hour old was
    left by a pass that died), so a check by hand while serve's runs reads other ones. `only`
    keeps to those ids; `skip` leaves out ones this check has already tried."""
    token, t = f"{os.getpid()}-{time.monotonic_ns()}", news.now()
    db.execute("UPDATE blinds SET claim_token = NULL WHERE claim_token IS NOT NULL AND claimed_at < ?",
               (news.iso(t - timedelta(minutes=30)),))
    db.execute(f"UPDATE blinds SET claim_token = ?, claimed_at = ? WHERE id IN (SELECT id FROM blinds WHERE {TO_READ} "
               "AND claim_token IS NULL AND (? IS NULL OR id IN (SELECT value FROM json_each(?))) "
               "AND id NOT IN (SELECT value FROM json_each(?)) ORDER BY published DESC LIMIT ?)",
               (token, news.iso(t), None if only is None else 1, json.dumps(only or []), json.dumps(sorted(skip or ())),
                limit))
    db.commit()
    return token, db.execute("SELECT * FROM blinds WHERE claim_token = ? ORDER BY published DESC", (token,)).fetchall()


def read_clues(cfg: dict, db: sqlite3.Connection, claude=None, say=print, only: list[str] | None = None,
               tried: set[str] | None = None) -> list[str]:
    """The clue pass over blinds still to read, newest first, at most [blinds] max_clues a pass.
    Returns the ids it read; every id it asked about is added to `tried`, which later passes of
    the same check leave out (a refusal waits for the next check)."""
    bc = cfg["blinds"]
    token, todo = claim_unread(db, bc["max_clues"], only, tried)
    if tried is not None:
        tried.update(r["id"] for r in todo)
    if not todo:
        return []
    try:
        from .prompts import Prompts

        if claude is None:
            from .llm import Claude, preflight

            claude = Claude(cfg)
            preflight(claude, bc["clues_model"])
        system, names, w, done = Prompts(cfg).get("blinds_clues"), followed(cfg), weights(cfg), []

        def ask(rows: list) -> list[str] | None:
            out, _meta = claude.json_call(bc["clues_model"], system, clues_request(rows, names), CLUES_SCHEMA,
                                          bc["clues_effort"])
            if out is None:
                return None
            got = apply_clues(db, rows, out, w, bc["clues_model"])
            db.commit()
            return got

        for b in range(0, len(todo), CLUE_BATCH):
            batch = todo[b:b + CLUE_BATCH]
            got = ask(batch)
            if got is None and len(batch) > 1:  # one blind it won't read refuses the call: ask one by one
                say(f"  the clue pass refused a batch of {len(batch)}; asking one at a time")
                got = [i for r in batch for i in (ask([r]) or [])]
            done += got or []
            missed(db, [r for r in batch if r["id"] not in set(got or [])], say)
        say(f"read the clues of {len(done)} blind(s)" + (f", ${claude.cost:.3f}" if getattr(claude, "cost", 0) else ""))
        return done
    finally:
        db.execute("UPDATE blinds SET claim_token = NULL WHERE claim_token = ?", (token,))
        db.commit()


def missed(db: sqlite3.Connection, rows: list, say=print) -> None:
    """Blinds the clue pass refused or left out: tried again next check, and after MAX_TRIES
    asked about with no clues (so one it won't read doesn't come back every check)."""
    t = news.iso(news.now())
    for r in rows:
        tries = (r["clue_tries"] or 0) + 1
        if tries < MAX_TRIES:
            db.execute("UPDATE blinds SET clue_tries = ? WHERE id = ?", (tries, r["id"]))
            continue
        db.execute("UPDATE blinds SET clue_tries = ?, clues = '[]', claim = ?, clued_at = ?, clue_model = 'refused', "
                   "status = CASE WHEN status = 'unread' THEN 'waiting' ELSE status END WHERE id = ?",
                   (tries, "(the clue pass wouldn't read this one)", t, r["id"]))
        say(f"  {r['id']}: not read after {tries} tries; it waits for you without clues")
    db.commit()


# ---------- pairing ----------

def pair_request(new: list, pool: list) -> str:
    def line(r) -> str:
        gist = r["text"][:400] if r["clue_model"] == "refused" else r["claim"] or r["text"][:400]  # not the placeholder
        return f"{gist} | clues: " + "; ".join(c["text"] for c in json.loads(r["clues"] or "[]"))

    a = "\n".join(f"[{i}] ({r['source']}, {r['day']}) {line(r)}" for i, r in enumerate(new, 1))
    b = "\n".join(f"<{r['id']}> ({r['source']}, {r['day']}) {line(r)}" for r in pool)
    return f"<new>\n{a}\n</new>\n\n<other_sources>\n{b}\n</other_sources>"


def pair(cfg: dict, db: sqlite3.Connection, claude=None, say=print) -> int:
    """Pair each clued blind not yet compared (paired_at unset) with an unpaired twin from another
    source published within pair_days of it (feed sources only: a blind pasted by hand isn't
    paired). A batch is marked compared once its call returns, so one that fails (an error
    stops the check) is asked again next check. With one feed source there is nothing to compare: all are marked."""
    bc = cfg["blinds"]
    days = timedelta(days=bc["pair_days"])
    todo = "kind = 'blind' AND source != 'hand' AND clued_at IS NOT NULL AND pair IS NULL AND paired_at IS NULL"
    if db.execute("SELECT COUNT(DISTINCT source) FROM blinds WHERE kind = 'blind' AND source != 'hand'").fetchone()[0] < 2:
        db.execute(f"UPDATE blinds SET paired_at = ? WHERE {todo}", (news.iso(news.now()),))
        db.commit()
        return 0
    from .prompts import Prompts

    system, made = None, 0
    for (sid,) in db.execute(f"SELECT DISTINCT source FROM blinds WHERE {todo} ORDER BY source").fetchall():
        new = db.execute(f"SELECT * FROM blinds WHERE {todo} AND source = ? ORDER BY published", (sid,)).fetchall()
        for b in range(0, len(new), CLUE_BATCH):
            batch = new[b:b + CLUE_BATCH]
            lo = news.iso(datetime.fromisoformat(batch[0]["published"]) - days)
            hi = news.iso(datetime.fromisoformat(batch[-1]["published"]) + days)
            pool = db.execute("SELECT * FROM blinds WHERE source NOT IN (?, 'hand') AND kind = 'blind' AND pair IS NULL "
                              "AND clued_at IS NOT NULL AND published BETWEEN ? AND ? ORDER BY published DESC LIMIT ?",
                              (sid, lo, hi, PAIR_POOL)).fetchall()
            if pool:
                if claude is None:
                    from .llm import Claude, preflight

                    claude = Claude(cfg)
                    preflight(claude, bc["clues_model"])
                system = system or Prompts(cfg).get("blinds_pair")
                out, _meta = claude.json_call(bc["clues_model"], system, pair_request(batch, pool), PAIR_SCHEMA,
                                              bc["clues_effort"])
                made += apply_pairs(db, batch, {r["id"]: r for r in pool}, (out or {}).get("pairs", []), days)
            db.executemany("UPDATE blinds SET paired_at = ? WHERE id = ?",
                           [(news.iso(news.now()), r["id"]) for r in batch])
            db.commit()
    if made:
        say(f"paired {made} blind(s) with a twin from another source")
    return made


def apply_pairs(db: sqlite3.Connection, batch: list, pool: dict, pairs: list[dict],
                within: timedelta | None = None) -> int:
    """Each pair links a new blind and a pool blind as one event, "e-" + the older id, when they
    were published within `within` of each other. A blind already paired (by an earlier pair in
    this list, or by another check meanwhile) keeps its first twin: the update only takes two
    unpaired blinds, so two checks can't make a group of three."""
    made = 0
    for p in pairs:
        i, other = p.get("i"), p.get("id", "")
        if not isinstance(i, int) or not 1 <= i <= len(batch) or other not in pool:
            continue
        mine = batch[i - 1]
        a, b = datetime.fromisoformat(mine["published"]), datetime.fromisoformat(pool[other]["published"])
        if within is not None and abs(a - b) > within:
            continue
        older = min((a, mine["id"]), (b, other))[1]
        cur = db.execute("UPDATE blinds SET pair = ?, pair_why = ? WHERE id IN (?, ?) AND pair IS NULL AND "
                         "(SELECT COUNT(*) FROM blinds WHERE id IN (?, ?) AND pair IS NULL) = 2",
                         (f"e-{older}", (p.get("why") or "")[:300], mine["id"], other, mine["id"], other))
        made += cur.rowcount == 2
    return made


# ---------- check ----------

def check(cfg: dict, backfill_days: float = 0, clues: bool = True, claude=None, say=print) -> dict:
    """Fetch every source, read the clues of what is unread (at most max_clues, or with a
    backfill all of them), pair twins. Returns counts."""
    srcs = source_list(cfg)
    if not srcs:
        say("no sources: add [[blinds.sources]] entries to clipper.toml (clipper.example.toml has two)")
    db = connect(cfg)
    try:
        new = edited = 0
        for src in srcs:
            n, ed = fetch_source(db, src, backfill_days, cfg["blinds"]["backfill_pages"], say)
            say(f"  {src['name']}: {n} new" + (f", {ed} edited" if ed else ""))
            new, edited = new + n, edited + ed
        prune(cfg, db)
        read, paired, tried = [], 0, set()
        if clues and cfg["blinds"]["clues_model"]:
            while True:  # max_clues a pass; a backfill reads everything it brought in, past refusals
                before = len(tried)
                read += read_clues(cfg, db, claude, say, tried=tried)
                if not backfill_days or len(tried) == before:
                    break
            paired = pair(cfg, db, claude, say)
        return {"new": new, "edited": edited, "read": len(read), "paired": paired}
    finally:
        db.close()


def prune(cfg: dict, db: sqlite3.Connection) -> int:
    """Forget what is older than keep_days, except blinds the user answered (the scorecard's)."""
    cut = news.iso(news.now() - timedelta(days=cfg["blinds"]["keep_days"]))
    n = db.execute("DELETE FROM blinds WHERE published < ? AND status != 'answered'", (cut,)).rowcount
    if n:  # a twin left on its own is unpaired
        db.execute("UPDATE blinds SET pair = NULL, pair_why = NULL WHERE pair IS NOT NULL AND pair IN "
                   "(SELECT pair FROM blinds WHERE pair IS NOT NULL GROUP BY pair HAVING COUNT(*) = 1)")
    db.commit()
    return n


def estimate(cfg: dict, backfill_days: float = 0, say=print) -> list[str]:
    """--dry-run: fetch (nothing stored) and price the clue pass for what would be new."""
    from .llm import estimate_lines, tokens
    from .prompts import Prompts

    srcs = source_list(cfg)
    db = connect(cfg)
    try:
        texts, since = [], news.now() - timedelta(days=backfill_days) if backfill_days else None
        for src in srcs:
            for page, items, _, _ in pages(src, since, cfg["blinds"]["backfill_pages"]):
                fresh = [e for e in items if classify(src, e["title"]) == "blind"
                         and (not since or (news.when(e["published"]) or news.iso(news.now())) >= news.iso(since))
                         and not db.execute("SELECT 1 FROM blinds WHERE id = ?", (blind_id(src, "blind", e),)).fetchone()]
                texts += [text_of(e["html"]) for e in fresh]
                say(f"  {src['name']} page {page + 1}: {len(items)} entries, {len(fresh)} new blinds")
        texts += [r["text"] for r in db.execute(f"SELECT text FROM blinds WHERE {TO_READ}")]
        calls = -(-len(texts) // CLUE_BATCH)
        system = tokens(Prompts(cfg).get("blinds_clues")) + tokens("\n".join(followed(cfg)))
        tin = calls * system + sum(tokens(t) + 30 for t in texts)
        tout = len(texts) * 220
        note = "" if backfill_days or len(texts) <= cfg["blinds"]["max_clues"] else (
            f" (one check reads {cfg['blinds']['max_clues']}, [blinds] max_clues; --backfill reads them all)")
        return [f"{len(texts)} blinds to read{note}:", *estimate_lines(cfg, [("clues", cfg["blinds"]["clues_model"],
                                                                         calls, tin, tout)])]
    finally:
        db.close()


# ---------- by hand ----------

def add_by_hand(cfg: dict, text: str, clues: bool = True, claude=None, say=print) -> str:
    """A blind pasted from anywhere else -> its id (hand-<day>-<n>); its clues read now."""
    text = text_of(text.replace("\n", "<br>"))
    if not text:
        raise ClipperError("an empty blind")
    db = connect(cfg)
    try:
        day, t = news.now().date().isoformat(), news.iso(news.now())
        taken = {r["id"] for r in db.execute("SELECT id FROM blinds WHERE source = 'hand' AND day = ?", (day,))}
        n = 1
        while True:  # the next free number; again if another paste took it meanwhile
            while f"hand-{day}-{n}" in taken:
                n += 1
            bid = f"hand-{day}-{n}"
            if db.execute("INSERT OR IGNORE INTO blinds (id, source, kind, title, url, published, day, seen_at, text, "
                          "status, guesses, history) VALUES (?, 'hand', 'blind', 'By hand', '', ?, ?, ?, ?, 'unread', "
                          "'[]', '[]')", (bid, t, day, t, text)).rowcount:
                break
            taken.add(bid)
        db.commit()
        if clues and cfg["blinds"]["clues_model"]:
            read_clues(cfg, db, claude, say, only=[bid])
        return bid
    finally:
        db.close()


# ---------- the user's answer ----------

def answer(cfg: dict, bid: str, name: str = "", why: str = "", none: bool = False, not_ours: bool = False,
           who: str = "") -> dict:
    """The user's guess (a name, optionally why), No idea, or Not ours. Discord, the war room and
    the CLI all answer through this. A name is matched to `clipper people` (its id kept); one
    nobody answers to is kept as written. A new answer replaces the last one until a result is in
    (step 3), and the replaced one stays in the history."""
    from . import people

    if sum((bool(name.strip()), none, not_ours)) != 1:
        raise ClipperError("answer with a name, --none or --not-ours (one of them)")
    db = connect(cfg)
    try:
        db.execute("BEGIN IMMEDIATE")  # two answers at once: the second reads the first, and keeps it in history
        b = get(db, bid)
        if b["kind"] == "reveal":
            raise ClipperError(f"{bid} is a reveal, not a blind")
        t = news.iso(news.now())
        history = b["history"]
        if b["guesses"]:
            history.append({"at": t, "what": "replaced", "guesses": b["guesses"]})
        guesses, status = [], "no_idea" if none else "not_ours" if not_ours else "answered"
        if name.strip():
            try:
                found = people.find(cfg, name.strip())
            except ClipperError as e:  # two records answer to it: keep the name as written
                found, note = None, str(e)
            else:
                note = ""
            guesses = [{"name": found[1]["name"] if found else name.strip(), "person": found[0] if found else "",
                        "as_written": name.strip(), "why": why.strip(), "who": who, "at": t,
                        **({"note": note} if note else {})}]
        history.append({"at": t, "what": status, "who": who})
        db.execute("UPDATE blinds SET guesses = ?, status = ?, history = ? WHERE id = ?",
                   (json.dumps(guesses), status, json.dumps(history), b["id"]))
        db.commit()
        return get(db, b["id"])
    finally:
        db.close()


# ---------- reading ----------

def tags(b: dict) -> list[str]:
    """What [blinds] hide filters on."""
    out = [f"source:{b['source']}", f"kind:{b['kind']}", *(f"field:{f}" for f in b["fields"]),
           *(f"sensitive:{s}" for s in b["sensitive"])]
    if b["sensitive"]:
        out.append("sensitive")
    if b["private"]:
        out.append("private")
    if b["ours"]:
        out.append("ours")
    return out


def hidden(cfg: dict, b: dict) -> bool:
    hide = {h.lower() for h in cfg["blinds"]["hide"]}
    return bool(hide & {t.lower() for t in tags(b)})


def listing(cfg: dict, days: float = 14, status: str = "", source: str = "", everything: bool = False) -> list[dict]:
    """Blinds newest first: the last `days`, by status and source; hidden, not-ours and reveals
    only with everything (or when asked for by status)."""
    if status and status not in STATUSES:
        raise ClipperError(f"status is one of {', '.join(STATUSES)}")
    db = connect(cfg)
    try:
        q, args = "SELECT * FROM blinds WHERE published > ?", [news.iso(news.now() - timedelta(days=days))]
        if status:
            q, args = q + " AND status = ?", [*args, status]
        elif not everything:
            q += " AND status NOT IN ('not_ours', 'reveal')"
        if source:
            q, args = q + " AND source = ?", [*args, source]
        rows = [as_dict(r) for r in db.execute(q + " ORDER BY published DESC, id", args)]
    finally:
        db.close()
    for r in rows:
        r["tags"], r["hidden"] = tags(r), hidden(cfg, r)
    return [r for r in rows if everything or status or not r["hidden"]]


def one(cfg: dict, bid: str) -> dict:
    db = connect(cfg)
    try:
        b = get(db, bid)
        if b["pair"]:
            b["twin"] = next((as_dict(r) for r in db.execute("SELECT * FROM blinds WHERE pair = ? AND id != ?",
                                                              (b["pair"], b["id"]))), None)
    finally:
        db.close()
    b["tags"], b["hidden"] = tags(b), hidden(cfg, b)
    return b


def twins(cfg: dict, pairs: set[str]) -> dict[str, list[str]]:
    """The ids in each of these pairs, from the whole store (a list's source and date filters
    leave out the other half)."""
    if not pairs:
        return {}
    db = connect(cfg)
    try:
        out: dict[str, list[str]] = {}
        for r in db.execute("SELECT id, pair FROM blinds WHERE pair IN (SELECT value FROM json_each(?)) ORDER BY id",
                            (json.dumps(sorted(pairs)),)):
            out.setdefault(r["pair"], []).append(r["id"])
        return out
    finally:
        db.close()


def mark_of(b: dict) -> str:
    return ("*" if b["ours"] else " ") + ("!" if b["sensitive"] else " ") + ("p" if b["private"] else " ")


def line(b: dict) -> str:
    gist = b["claim"] or b["text"].replace("\n", " ")
    guess = f"  -> {b['guesses'][0]['name']}" if b["guesses"] else ""
    return f"{b['id']:<26} {mark_of(b)} {b['status']:<9} {gist[:90]}{guess}"


def show_text(b: dict) -> str:
    out = [f"{b['id']}  ({b['source']}, {b['day']}, {b['status']})", b["url"] or "", "", b["text"], ""]
    if b["clues"]:
        out += ["clues:", *(f"  {c['type']:<12} {c['weight']:>3g}  {c['text']}" for c in b["clues"])]
    if b["claim"]:
        out.append(f"claim: {b['claim']}")
    out.append("tags: " + ", ".join(b["tags"]) + ("  (hidden by [blinds] hide)" if b["hidden"] else ""))
    if b.get("twin"):
        out.append(f"twin: {b['twin']['id']} ({b['pair_why']})")
    for g in b["guesses"]:
        out.append(f"your guess: {g['name']}" + (f" ({g['person']})" if g["person"] else " (not in clipper people)")
                   + (f", because {g['why']}" if g["why"] else "") + (f"  [{g['note']}]" if g.get("note") else ""))
    return "\n".join(x for x in out if x is not None)


def sources_report(cfg: dict) -> list[dict]:
    db = connect(cfg)
    try:
        rows = {r["name"]: dict(r) for r in db.execute("SELECT * FROM sources")}
    finally:
        db.close()
    return [{"name": s["name"], "feed": s["feed"], "kind": s["kind"], **{k: rows.get(s["name"], {}).get(k)
                                                                          for k in ("fetched_at", "error", "items")}}
            for s in source_list(cfg)]
