`clipper blinds` collects gossip **blind items**: short stories that leave the name out and the
clues in ("which former A-list actor..."). You say who you think each one is about first, and
only then does clipper look into it. When a source reveals the answer, your guess and clipper's
are both scored. It works for any gossip source and any kind of drama, not only the people this
install follows. It is research for you alone: nothing in it is ever published (#111).

It is built in steps. **Step 1, this page, is in:** the sources, storage, the clue pass, pairing
twins, and answering from the command line. Still to come: the Discord digest and the war
room's Blinds window (step 2), the investigation after your answer (step 3), and reveals with
the scorecard (step 4).

## Sources

There are none by default. Each source is a table in clipper.toml (`clipper.example.toml` has
two, commented out):

```toml
[[blinds.sources]]
name = "cdan"                        # lowercase; starts each blind's id
feed = "https://www.crazydaysandnights.net/feeds/posts/default"
kind = "blogger"                     # the parser: blogger, wordpress or rss
blind = "^Blind Item #"              # titles that are blinds (a regex)
reveal = "^Blind Items? Revealed"    # titles that are reveals (checked first)
```

Anything else in the feed is skipped. `kind` also sets how `--backfill` pages back: Blogger by
`start-index` (150 posts a page), WordPress by `paged`. An `rss` source has only its one page.
Paging stops at the backfill's start, at `[blinds] backfill_pages`, or at a page that holds
nothing new.

A Blogger blog numbers its blinds afresh each day, so such a blind's id is its source, date and
number: `cdan-2026-11-15-b16` is that day's Blind Item #16, and `cdan-2026-11-15-r4` is its Blind
Items Revealed #4. Other sources' ids are the source plus a hash of the post's id. When a post's
text changes, the blind is updated rather than added again, since an answer added to the
original post is how some sources reveal.

Crazy Days and Nights' Atom feed works (checked 2026-11-15, about 20 blinds and 4 reveal posts a
day). Blind Gossip's whole site answered HTTP 500 that day, so its entry is untested.

## Commands

```bash
clipper blinds check --backfill 30 --dry-run   # fetch, store nothing, price reading the clues
clipper blinds check [--backfill DAYS] [--no-clues]
clipper blinds list [--days 14] [--status waiting] [--source cdan] [--all] [--json]
clipper blinds show <blind> [--json]
clipper blinds guess "<text>"                  # a blind from anywhere else (Instagram, X, a screenshot)
clipper blinds answer <blind> "<name>" [--why "..."]
clipper blinds answer <blind> --none           # no idea
clipper blinds answer <blind> --not-ours       # not one to look into
clipper blinds sources                         # each source: last fetch, error, how many stored
```

A blind's status:

| Status | Meaning |
|---|---|
| unread | stored, clues not read yet |
| waiting | clues read, waiting for your guess |
| answered | you guessed |
| no_idea | you said you had no idea |
| not_ours | you said it isn't one to look into |
| reveal | a reveal post, kept for step 4 |

`list` leaves out not-ours blinds, reveals and hidden blinds unless you pass `--all` or ask for
that status. In its list, `*` marks a blind that could be about someone you follow, `!` a
sensitive one and `p` a private person. A blind pasted with `guess` is `hand-<date>-<n>`.

## The clue pass

`[blinds] clues_model` (Haiku, prompt `blinds_clues_v1`) reads each unread blind, ten to a call,
newest first, at most `max_clues` per check. It returns:

- **the clues**, each one typed (profession, platform, fame, relationship, when, where, event,
  hint, other) and weighted by `[blinds] weights`
- **the claim** in one neutral sentence
- **the fields** the blind is about (film, music, streaming...)
- **could be ours:** whether the clues could fit someone this install follows (`clipper people`
  and the [news watcher](News-watcher)'s list)
- **sensitive:** crime, health, sexuality, abuse or minors
- **private:** whether it points at a private person

It **never names or hints at who it might be**, so your guess is your own. A blind the model
refused, or left out of its answer, stays unread for the next check. Reading costs about $0.0002
a blind: a month of one busy source is well under a dollar. `check --dry-run` prices it first.

**Twins.** Different sources often blind the same event. A newly read blind is compared with
other sources' unpaired blinds from the last `pair_days` (prompt `blinds_pair_v1`, the same
model). A pair becomes one event: asked once, and answered by either source's reveal. With only
one source, no pairing call is made.

## Tags and hiding

Every blind carries tags:
- `source:<name>` and `kind:blind`
- `field:<field>` for each field
- `sensitive` plus `sensitive:<kind>` for each kind of sensitive claim
- `private`
- `ours`

`[blinds] hide = ["sensitive:health", "field:sports"]` leaves those out of the list, and out of
the alerts once there are any. A hidden blind is kept, and `--all` shows it.

## Your answer

`answer` takes a name, `--none` or `--not-ours`. A name is matched to [People](People), and its
record's id is kept. A name nobody answers to, or one that matches two records, is kept as you
wrote it. Answering again replaces your last answer; the old one stays in the blind's history.
In step 2, Discord and the war room answer through the same function.

## Where it lives

Everything is in `[paths] news/blinds/blinds.sqlite`. The blind's text is kept, because it is
short and it is what the clue pass reads. Linked articles are never fetched. Blinds older than
`keep_days` (365) are forgotten, except ones you answered. A test checks that nothing but the
command reads the blinds, so no title, description, cover, post or weekly issue can pick one up.
