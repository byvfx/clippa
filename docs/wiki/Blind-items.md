`clipper blinds` collects gossip **blind items**: short stories that leave the name out and the
clues in ("which former A-list actor..."). You say who you think each one is about first, and
only then does clipper look into it. When a source reveals the answer, your guess and clipper's
are both scored. It works for any gossip source and any kind of drama, not only the people this
install follows. It is research for you alone: nothing in it is ever published (#111).

It is built in steps. **In so far:** the sources, storage, the clue pass, pairing twins,
answering from the command line, the war room's Blinds window, serve's regular check and the
questions in [Discord](#discord). Still to come: the investigation after your answer (step 3),
and reveals with the scorecard (step 4).

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

Anything else in the feed is skipped. `kind` also sets how `--backfill` pages back:
- **Blogger:** newest-published first, 150 posts a page, by `start-index`. Blogger ignores a page
  size given without one, so clipper always sets both itself.
- **WordPress:** by `paged`.
- **rss:** only its one page.

Paging stops at the first page wholly older than the backfill's start, at `[blinds]
backfill_pages`, or at a page that holds nothing new. A backfill also reads the clues of
everything it brought in. An ordinary check reads at most `max_clues`.

`clipper doctor` (the all, serve and bot roles) fails on a bad source entry. Online, it warns
when a feed doesn't answer.

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

`[blinds] clues_model` (Haiku, prompt `blinds_clues_v1`) reads each blind whose clues are still
unread, ten to a call, newest first, at most `max_clues` per check. That includes a blind you
answered before they were read, but not one you said is not ours. A blind pasted with `guess` is
read on its own, straight away. It returns:

- **the clues**, each one typed (profession, platform, fame, relationship, when, where, event,
  hint, other) and weighted by `[blinds] weights`
- **the claim** in one neutral sentence
- **the fields** the blind is about (film, music, streaming...)
- **could be ours:** whether the clues could fit someone this install follows (`clipper people`
  and the [news watcher](News-watcher)'s list)
- **sensitive:** crime, health, sexuality, abuse or minors
- **private:** whether it points at a private person

It **never names or hints at who it might be**, so your guess is your own.

When a call is refused, its ten blinds are asked one at a time, so one blind the model won't read
doesn't hold up the others. A blind still refused, or left out of the answer, is tried again on
the next check (a backfill goes on to the older ones meanwhile). After three tries it waits for your guess without clues (its claim says the clue
pass wouldn't read it) instead of coming back every check. Reading costs about $0.0002
a blind: a month of one busy source is well under a dollar. `check --dry-run` prices it first.

**Twins.** Different sources often blind the same event. Each newly read blind is compared, once,
with other sources' unpaired blinds published within `pair_days` of it (prompt `blinds_pair_v1`,
the same model), so a backfill pairs old twins too. A pair becomes one event: asked once, and
answered by either source's reveal. A pairing call that fails is made again on the next check.
With only one feed source, no pairing call is made. Blinds pasted by hand aren't paired yet. A pair whose
other half is forgotten (`keep_days`) is unpaired.

## Tags and hiding

Every blind carries tags:
- `source:<name>` and `kind:blind`
- `field:<field>` for each field
- `sensitive` plus `sensitive:<kind>` for each kind of sensitive claim
- `private`
- `ours`

`[blinds] hide = ["sensitive:health", "field:sports"]` leaves those out of the list and out of
Discord. A hidden blind is kept, and `--all` shows it.

## Your answer

`answer` takes a name, `--none` or `--not-ours`. A name is matched to [People](People), and its
record's id is kept. A name nobody answers to, or one that matches two records, is kept as you
wrote it. Answering again replaces your last answer; the old one stays in the blind's history.
The war room and Discord answer through the same function.

## Discord

With `[blinds] check_minutes` set (30 here; 0, the default, is off), serve runs `clipper blinds
check` that often, and the [bot](Discord-app) asks you about what it stored. It shows the blind,
its source and link, its clues and its tags, and **never a guess of clipper's**, so yours is your
own. Each blind has three buttons:

- **I think it's…** opens a form: the name, and why if you like ("he said he was in Vegas that
  week").
- **No idea**
- **Not ours**: nothing more is spent on it.

The answer goes at the end of the message. The buttons stay, so you can change it.

`[blinds] alert` decides what is posted:

| alert | What you get |
|---|---|
| `digest` (the default) | One thread a day at `digest_at` (20:00, `[schedule] timezone`) with every new blind, numbered. A blind that could be about someone you follow also gets an alert of its own as soon as its clues are read. |
| `all` | An alert of its own for every blind. |
| `ours` | Alerts only for blinds that could be about someone you follow. |
| `off` | Nothing in Discord: the war room only. |

**What is never posted:**
- a blind about a private person
- a hidden one (`[blinds] hide`)
- one you already answered, or one whose clues aren't read yet (it comes once they are)
- one published more than two days ago, so a backfill doesn't flood the channel
- a twin whose other half was already posted or answered (anywhere): a pair is asked once, the twin shown with
  it when it could be posted itself

Sensitive blinds (crime, health, sexuality, abuse, minors) are posted only with `[blinds]
sensitive_alerts = true`. This install has it on; the default is off. To mute one kind, add its
tag to `hide`.

A digest shows the newest 40 blinds; the war room has the rest. If the bot stops partway through,
the blinds it didn't post come in the next day's digest. Typing an answer in the thread isn't
read: the bot has no Message Content intent, so answer with the buttons.

**The channel** is `[blinds] channel` (`blinds`), or `channel_id`. The bot makes the channel if it
is missing, **private**: only the bot and `[discord] allowed_user_ids` can see it. That needs
Manage Channels. If the channel is one @everyone can see, the bot still uses it, but DMs you a
warning (at most once per `[notify] cooldown_hours`).

`clipper doctor` checks `alert`, `digest_at` and the channel whenever `check_minutes` is on.

## The war room

**Blinds** in the [war room](War-room)'s header opens the window.

- **Tabs:** Waiting on you (the default), Answered, No idea, Not ours, Reveals, Unread and All,
  each with its count. Next to them are how far back to look (1 to 365 days, 14 by default) and
  which source.
- **Each blind:**
  - its title, id, day and status, and a link to the post
  - tags for could be ours, each sensitive kind, private and hidden, plus its fields
  - the text (**more** opens a long one) and the claim
  - the clues, each labelled with its type (hover for its weight)
  - its twin, if it has one: click to jump to it
  - your current guess
- **Answering:** type a name (the people this install follows are suggested) and, if you like,
  why, then **That's my guess** or Enter. **No idea** and **Not ours** are the other two answers.
  An answered blind leaves the Waiting tab. A guess can be changed from any tab.
- **Paste a blind…** adds one from anywhere else, like `clipper blinds guess`. Its clues are
  read straight away.
- **Check now** fetches the sources and reads the new blinds' clues in the background, one check
  at a time. The window shows its log until it is done.
- **The header line** says when each source was last fetched (a failing one is red; hover for
  why), which model reads the clues, and how many blinds `[blinds] hide` is hiding. Hidden blinds
  are left out of every tab and count except All.
- A tab shows the newest 300 blinds at most. Fewer days or one source shows the rest.

The window only shows the blinds and answers them. A test checks that nothing else in the war room
reads them.

## Where it lives

Everything is in `[paths] news/blinds/blinds.sqlite`. The blind's text is kept, because it is
short and it is what the clue pass reads. Linked articles are never fetched. Blinds older than
`keep_days` (365) are forgotten, except ones you answered. A test checks that nothing but the
command, the war room's window and the bot's questions reads the blinds, so no title,
description, cover, post or weekly issue can pick one up.
