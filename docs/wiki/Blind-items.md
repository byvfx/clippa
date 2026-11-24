`clippa blinds` collects gossip **blind items**: short stories that leave the name out and the
clues in ("which former A-list actor..."). You say who you think each one is about first, and
only then does Clippa look into it. When a source reveals the answer, your guess and Clippa's
are both scored. It works for any gossip source and any kind of drama, not only the people this
install follows. It is research for you alone: nothing in it is ever published (#111).

It is built in steps. **In so far:** the sources, storage, the clue pass, pairing twins,
answering from the command line, the war room's Blinds window, serve's regular check and the
questions in [Discord](#discord), and [reveals](#reveals) matched to their blinds with your
guesses scored (step 4), with the reveal told to you in Discord and the war room once you've
answered. Still to come: the investigation after your answer (step 3).

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
  size given without one, so Clippa always sets both itself.
- **WordPress:** by `paged`.
- **rss:** only its one page.

Paging stops at the first page wholly older than the backfill's start, at `[blinds]
backfill_pages`, or at a page that holds nothing new. A backfill also reads the clues of
everything it brought in. An ordinary check reads at most `max_clues`.

`clippa doctor` (the all, serve and bot roles) fails on a bad source entry. Online, it warns
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
clippa blinds check --backfill 30 --dry-run   # fetch, store nothing, price reading the clues
clippa blinds check [--backfill DAYS] [--no-clues]
clippa blinds list [--days 14] [--status waiting] [--source cdan] [--all] [--json]
clippa blinds show <blind> [--json]
clippa blinds guess "<text>"                  # a blind from anywhere else (Instagram, X, a screenshot)
clippa blinds answer <blind> "<name>" [--why "..."]
clippa blinds answer <blind> --none           # no idea
clippa blinds answer <blind> --not-ours       # not one to look into
clippa blinds sources                         # each source: last fetch, error, how many stored
clippa blinds reveals [--json]                # match reveals to their blinds, score your guesses
```

A blind's status:

| Status | Meaning |
|---|---|
| unread | stored, clues not read yet |
| waiting | clues read, waiting for your guess |
| answered | you guessed |
| no_idea | you said you had no idea |
| not_ours | you said it isn't one to look into |
| reveal | a reveal post (see [Reveals](#reveals)) |

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
- **could be ours:** whether the clues could fit someone this install follows (`clippa people`
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
other half is forgotten (only when `keep_days` is set) is unpaired.

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

## Reveals

A reveal post quotes the blind it answers and adds the answer on a last line of its own:

| Line | What it is |
| --- | --- |
| `November 7, 2026` | the day the blind was posted |
| `The reality star with...` | the blind, as posted |
| `Name One/Name Two` | the answer |

Every `check` (and `clippa blinds reveals`) matches each new reveal to a stored blind, in code
with no model call. A blind matches when the reveal repeats at least 60% of its wording (word
triples), and at least 30 points more than any other blind posted within a day of the date line.
A reveal whose date line is missing or garbled is compared with every earlier blind of its
source. Anything less clear is left unmatched rather than guessed. On the 11-22 store, every
dated reveal whose blind was stored matched, and no blind was matched twice.

- **The match** puts the answer line on the blind (and its twin, if it has one), with the reveal's
  id and the day it was posted, and logs it in the blind's history. `show` prints it, labelled
  as the source's word. `list` only marks a revealed blind `[revealed]`, so browsing never
  spoils one you haven't guessed.
- **A corrected reveal** (the source edits the post) is matched again. The answer it gave before
  stays in the blind's history. A reveal longer than the stored text limit (6,000 characters) isn't
  matched, since its answer line may be cut off.
- **Discord** never asks about a blind that is already revealed: a guess then wouldn't count.
- **Unmatched reveals** are tried again on every check. Most are for blinds older than the store:
  `check --backfill DAYS` brings those blinds in, and the next check links them.
- **Your guess is scored** against the names in the answer line (split on `/`, notes in brackets
  left out). The guess scored is the one you had when the reveal was posted: changing it
  afterwards doesn't turn a miss into anything else. It is **right** when every word of it, or of
  the full name or an alias of its [People](People) record, is in one revealed name. One word fits
  only as a surname: "Example" and "Pat Example" fit "Pat Example", "Pat" doesn't. A guess whose
  only name came after the reveal is **late** and isn't counted. No idea isn't scored.
- **The war room** tags a revealed blind `revealed`, and shows who only on a blind you answered
  (a guess or No idea), with how your guess did. The window's header carries the scorecard: how
  many blinds are revealed, and your right and wrong guesses with the hit rate. A reveal post itself
  is in the All view, as the source wrote it.
- **Discord follows up** once a blind you answered is revealed: who the source says it is, and
  whether your guess was right, wrong or late. It replies to the blind's alert, or posts in its
  digest's thread, or in the channel for one you answered in the war room. A blind you never
  answered isn't followed up, so its answer stays out of Discord.
- **Right means matches the reveal, not true.** Sources sometimes take each other's stories.

`clippa blinds reveals` prints how many reveals matched, how many wait for older blinds, and
each scored guess with your hit rate.

## Discord

With `[blinds] check_minutes` set (30 here; 0, the default, is off), serve runs `clippa blinds
check` that often, and the [bot](Discord-app) asks you about what it stored. It shows the blind,
its source and link, its clues and its tags, and **never a guess of Clippa's**, so yours is your
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

`clippa doctor` checks `alert`, `digest_at` and the channel whenever `check_minutes` is on.

## The war room

**Blinds** on the [war room](War-room#pages-and-the-top-bar)'s Research page opens the window.

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
- **Paste a blind…** adds one from anywhere else, like `clippa blinds guess`. Its clues are
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
short and it is what the clue pass reads. Linked articles are never fetched.

**Nothing is forgotten by default** (`[blinds] keep_days = 0`): the store is research worth
keeping, and it is small (about 1 MB a month from one busy source). A source can take a post
down, and its text can't be fetched again. With `keep_days` set to a number of days, older blinds
are forgotten, except the ones you engaged with (answered, **no idea**), every **reveal**, and
every blind a reveal answered. A test checks that nothing but the command, the war room's window, the bot's
questions and the [taste backup](Backups) reads the blinds, so no title, description, cover, post
or weekly issue can pick one up.

**Custody.** `clippa backup` (and so serve's every-`every_days` run) copies the store in as
`blinds/blinds.sqlite`, a snapshot taken with SQLite's backup API and checked with
`integrity_check`. A store that fails the check still goes in, as `blinds.damaged.sqlite`, and
the backup's output says so: a damaged copy beats none for a repair, and the rest of the backup
isn't held up. That backup drops no older zip (`[backup] keep` waits), so the last good copy
survives. A store still locked by a long write after 30 s is left out of that one backup,
with a line saying so, rather than copied half-written, and that backup drops no older zip
either. `--dry-run` lists it without the snapshot. The zip is private, like the rest of the taste
layer: it is copied off the NAS by hand, never published.

When a source edits a post, the blind's text is updated and the text it had goes into its
history, so the wording the clues were read from is never lost.
