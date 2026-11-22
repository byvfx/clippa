<p align="center">
  <a href="https://byvfx.github.io/clippa/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://github.com/byvfx/clippa/wiki/images/overview-hero-dark.png">
      <img src="https://github.com/byvfx/clippa/wiki/images/overview-hero-light.png" alt="Clippa case study: an eight-hour stream's heat map with the picks marked, and the four headline figures" width="100%">
    </picture>
  </a>
</p>

The tour of what Clippa is, why it is built the way it is, and what it has done so far. It is the
wiki edition of the [case study page](https://byvfx.github.io/clippa/), which has the same
content with a timeline and figures that a wiki can't draw. Each section links to the page that
has the detail. Figures are as of 14 November 2026, from the project's own upload, cost and test
logs; they go stale, and the case study page is the one that gets re-measured.

| | |
|---|---|
| **390** | videos uploaded to YouTube since mid-October: 253 segments, 122 Shorts, 11 compilations, 4 clips |
| **~$3.50** | for a full run on an 8-hour stream, transcription included (median of 27 streams) |
| **2,732 h** | of a video archive transcribed and indexed for full-text search |
| **975** | offline tests, run with lint in CI on Linux and Windows on every push |

## The problem

A live show runs four to ten hours, several nights a week. The good parts are scattered through
it: a two-minute rant, a twenty-minute topic, a ten-second line that works as a Short. Finding and
cutting them by hand takes longer than the show itself, and the channel needs a steady flow of
uploads that read as if a person wrote them.

Clippa does the finding, cutting and packaging automatically and leaves one decision to people:
whether each piece goes out. Every item arrives in review with its video, title, description,
chapters and cover. A reviewer approves it, rejects it, or leaves a note, and a note triggers a
re-cut.

## The pipeline

Every stage is a `clippa <stage>` subcommand that reads and writes files in the stream's folder.
The stages are idempotent, so `clippa run` resumes after a crash, skips finished work, and can
redo from any stage with `--from`. One table in the code lists the stages, and the worker, the CLI
and the war room all read from it. The detail is on [Pipeline](Pipeline).

| Stage | What it does | Runs on |
|---|---|---|
| record / fetch | A Docker recorder on the NAS watches channels and captures each live show with its chat; past broadcasts can be fetched instead ([NAS recorder](NAS-recorder), [Fetching VODs](Fetching-VODs)) | NAS |
| transcribe | WhisperX with word timings and speaker labels, on a cloud GPU, about $0.45 a stream ([Transcribe on Modal](Transcribe-on-Modal)) | Modal L40S |
| signals | Chat rate and audio loudness over time: a heat map of where the audience reacted | Worker |
| score | Two passes: a fast model reads the whole transcript with the heat map, a stronger model judges only the shortlist | LLM |
| segments | Splits the stream into topics, and groups recurring topics across nights into compilations | LLM |
| cut | Lossless segment cuts on keyframes, re-encoded picks, and automatic bleeps for listed words in audio and captions | NAS ffmpeg |
| meta | Search-friendly title, description, chapters, tags and an .srt caption file, following a house style | LLM |
| thumbs | Cover art in each show's style; faces are placed so they never sit under overlays ([Covers](Covers)) | LLM + Pillow |
| shorts | Vertical 15 to 59 s versions; a vision model picks a layout for each scene and switches at every cut; dead air between words is trimmed only where the audio is quiet too, so a laugh stays | Worker |
| faces | Who is on screen, with local CPU face detection and recognition (YuNet + SFace) ([Faces](Faces)) | Worker |
| review | Publishes everything to Kitsu, the review system of record, and posts cards with approve buttons to Discord | Kitsu, Discord |
| schedule | Fills daily slots by type, mixes shows, drops anything stale, and respects the per-day upload limit ([Scheduling and uploads](Scheduling-and-uploads)) | serve |
| upload | Private with a publish time, then playlists and source links; runs every hour without anyone starting it | YouTube API |

The **fast lane** is transcribe through shorts: picks and Shorts reach review hours before the
long segments are cut.

## Architecture

The work goes where it is cheapest. Everything that runs all the time lives on a home NAS in
Docker, next to the footage, and video is cut and encoded there with the NAS's own Intel GPU, so
hours of footage never cross the network. Only GPU transcription goes out, to Modal, billed by the
second.

The config and all state sit in one folder on the NAS, so there is a single copy. Every service
writes a heartbeat file, and the war room reaches a phone over Tailscale. No other computer has to
be on. The same services can also run on a Windows machine as scheduled tasks, and the old laptop
setup is kept, switched off, as a rollback.

A new machine is set up by role: GPU worker, serve, bot, recorder, Claude, or a control station
that runs nothing unattended. `clippa setup` writes that machine's config, and
`clippa doctor --role ...` checks exactly what that role needs. A Mac on Apple Silicon is set up
as a control station. See [Roles and services](Roles-and-services) and [NAS hub](NAS-hub).

| Where | What lives there |
|---|---|
| **NAS, Docker: capture, review and operator** | The recorder (watches live channels, records video and chat, alerts Discord when a show starts or a recording fails, refuses a redeploy while a show is live); Kitsu, the production tracker used as the review record, one production per show; the shared folder with streams, config, schedule, logs, faces and archives; and a Claude Code container with its own clone of the repo, driven from the Claude app on a phone. It can run stages, read logs, and change code on a branch and open a PR. It can't restart or deploy the services ([Claude on the NAS](Claude-on-the-NAS)) |
| **NAS, clippa-hub containers: always-on services** | **serve** (the war room, hourly replan and upload, daily jobs, retakes), **bot** (Discord review cards, buttons and DMs; asks serve to replan after a decision), **worker** (runs every stage on each finished recording, fast lane first, with Intel Quick Sync encoding), **tailscale** (the war room on a private network for phone access, no ports opened to the internet) |
| **Cloud APIs, paid by use** | Modal (WhisperX on an L40S for live shows, cheaper L4s for archive bulk jobs); LLMs (Claude Haiku, Sonnet and Opus by stage, other providers through one interface, see [Model providers](Model-providers)); YouTube, Discord and ElevenLabs |

The main flows:

- The recorder finishes a show, and the worker runs the stages.
- Items are ready for review, and they appear in Kitsu and as Discord cards.
- A reviewer approves or leaves a note, and serve replans, or re-cuts on a note.
- Each hour, the schedule is replanned and the uploads go to YouTube.

How the processes share state is [How the processes talk](How-the-processes-talk).

## Review

Nothing goes out until a person says so. Kitsu holds every decision, with one production per show
and one episode per stream. The Discord bot mirrors each item as a card with buttons, so a reviewer
can approve from a phone. The [war room](War-room) is the full review desk. All three act through
the same code path, so a click in one place shows up in the other two.

1. **Arrives:** video, title, description, chapters and cover, already checked, in Kitsu, as a
   Discord card, and in the war room queue.
2. **Decide:** approve, reject, hold or leave a note, from any of the three, with undo.
3. **A note becomes a retake:** re-cut, re-trimmed or redrawn automatically, then back to review.
4. **Approved:** scheduled and uploaded in the next free slot on YouTube, and on X while the topic
   is hot.

More in [Review](Review) and [Reviewing](Reviewing).

### Guardrails before review

Some items arrive already flagged. A Short with more than five seconds of voices that aren't the
show's own (usually a video playing on stream) is held, and its card shows the seconds counted from
the speaker labels. The reviewer releases it when the show's commentary carries the moment.

Every item also goes through pre-flight checks before it is published for review: the text against
the house style, a Short's webcam panels for a face cut off at the edge, the cover for the wrong
person, and the subject for a name in the title that the video never says. Findings show as a
column in Kitsu, a line on the Discord card and a badge in the war room.

The bot sends a DM when something needs a person: a stream ready for review, a failed upload, a
stage that failed after its retries, recorder trouble, a filling disk. Quiet hours hold these until
morning, and a daily digest arrives at 09:00.

> [!NOTE]
> **Catch what a reviewer would reject, before they see it.** The checks were built from the items
> reviewers had actually rejected. A Short's webcam panels are sampled at eight frames and run
> through local face detection; a face counts as cut off only when an eye, the nose or the mouth
> crosses the panel edge, since a box touching the edge was usually someone leaning in. The cover
> check first compares the recognised face with the person the cover is about, then makes one
> vision-model call. Results are keyed to what was read, so nothing is checked or paid for twice.
>
> A backtest over 808 past covers cost $8.89. It caught both covers reviewers had rejected and
> flagged 67 of 780 approved ones, among them black frames and the same wrong side photo reused on
> several covers. Most of the rest were a cast member in a costume. A prompt line to allow costumes
> cleared those, but its first wording also hid a real wrong photo in every run, so it was narrowed
> until the photo was caught again.

### The war room

A web page served by the always-on service, and installable on a phone over Tailscale. It shows
what is live and whether it is recording, where each stream is in the pipeline, the review queue
and the upload schedule, plus panels for GPU jobs, costs and analytics. It also opens the cast
library, the news stories and the research dossiers, so all of the tools here have one place to
work from. Detail on [War room](War-room).

- **Keyboard:** <kbd>J</kbd> <kbd>K</kbd> <kbd>A</kbd> <kbd>R</kbd> <kbd>C</kbd> <kbd>H</kbd> are
  next, previous, approve, reject, retake note and hold. Space and the arrow keys play and seek.
- **Search everything:** titles, descriptions, topics, hashtags and what is said in each video.
  Filters live in the URL, so a view can be bookmarked.
- **Checked as you type:** length limits, unmasked swearing and words the channel avoids. Cards
  with a problem get a warning badge.
- **Bulk actions:** approve, hold, pin, reopen or send one retake note to a whole selection.
- **Find and replace:** across titles, descriptions, tags and playlists, with a dry run that lists
  every line it would change.
- **Undo:** any action, single or bulk, from its toast for 10 s or from the last 20. Undo goes
  through Kitsu too.
- **Cover editor:** pick the frame and the inset, drag face boxes, swap in a cast photo by
  expression, change the words. Saving re-renders locally and re-attaches the cover in Kitsu.
- **Who's this?:** pause on a face, draw a box, and it says who it looks like. One click enrolls
  the face and names every look-alike in the stream.
- **Schedule:** drag an item onto an open slot to pin it. The page warns before a drop breaks the
  spacing rules.

A usability pass made it safe to use quickly: a bulk approve touches only the items on screen
(approved items upload on their own), a double tap sends once, Sync and Replan stay greyed out
until the work is really done, closing a window with typed edits asks first, and the header turns
amber, then red, when its data goes stale.

When something fails, its error toast opens a log window with the service's own log, filterable by
word or errors only. Keys and tokens are blanked on the server before any filter runs, so a search
can't be used to guess one a character at a time.

### Distribution on X

Approved Shorts go to X through the API as native video. Posting runs every hour and takes the
newest streams from the last 72 hours first, up to six posts a day. It doesn't wait for YouTube,
because a clip is worth most on X while the topic is still being talked about.

X charges about $0.015 for a post and $0.20 for a post with a link, so Clippa never includes a
link. The post text leads with the hook, since the feed shows only the first words. It then adds up
to three hashtags and a credit line that tags whose footage it is. Tall videos are re-encoded to
X's recommended 720x1280. The weekly issue goes out as a thread, four pages per post, and a thread
that fails halfway picks up as replies to the last post. Each post's cost is logged with the model
calls. See [X posts](X-posts).

## Design decisions

What makes it hold up unattended.

**People decide, in one place.** Every review action, whether a Discord button, a war room click or
a Kitsu status, goes through one module and lands in Kitsu. There is no second database to fall out
of sync. A note on an item ("start two seconds later", "wrong face on the cover") becomes an
automatic retake: Clippa re-cuts or redraws that item and sends it back for review.

**Spend money only where it changes the answer.** A cheap model reads everything and a strong model
judges the shortlist. Paid stages refuse to rerun without `--force`. Cut keeps picks whose in and
out points haven't changed, and covers are kept while their cut is unchanged. Every paid stage
takes `--dry-run`, which builds the prompts it would send and prints the estimated cost without
calling anything. Every model call is logged with its token counts and price. `clippa costs` and
the war room break spend down by stage, stream and provider ([Costs](Costs)).

**Models and prompts are swappable and versioned.** All model calls go through one layer, and the
provider comes from the model string's prefix (Claude, OpenAI, Gemini, OpenRouter or local).
Prompts are versioned files. A version that produced reviewed output is never edited; changes go
into a new version. `clippa compare` runs candidate models against a gold set of past reviewer
decisions before a stage switches models.

**Measure before switching models.** `clippa compare` runs a candidate model or prompt on streams
that people have already reviewed, and counts how many of the approved moments it finds and how
many windows it keeps. Running the same prompt twice first sets a noise floor, so a change is
adopted only when it beats that. Twelve models were tried through OpenRouter, with reasoning on and
off. A newer restructured prompt found 8 of 18 approved moments against 6 for the previous one, but
the gap is within the noise, so it stays a candidate. Every trial and its cost is on
[Model trials](Model-trials).

**One install, many shows.** Config is layered: code defaults, a shared file, this machine's own
file, then environment variables. Each show gets its own table with cast, style, budgets and
layouts, and a single stream can override anything. Adding a show is a config table and a Kitsu
production, with no code changes ([Adding a show](Adding-a-show), [Configuration](Configuration)).
`clippa doctor` checks every dependency, key and service and prints the fix for each failure.

**State that survives crashes.** JSON state is written to a temp file, then swapped in. Updates
take a lock, and readers retry when they catch a file mid-replace. Library code raises one error
type with a one-line message, and only the CLI turns it into an exit. Tests enforce the
conventions: a test fails on a raw JSON read or a stray `SystemExit`, on a broken docs link, or on
a CLI command missing from its group.

**A schedule that stays current.** Uploads fill fixed daily slots by type (Shorts, segments,
compilations), alternate between shows, and stay under the channel's daily limit. Approved items
older than a few days drop out, so the channel never falls weeks behind the shows. The first
version queued everything, and the newest stream ended up 15 days behind. Switching to "stay
current" was a product decision, made from the data.

**It checks on itself.** `clippa checkup` reads only the files the services write: stale
heartbeats, a stage stuck for hours, a recording nobody picked up, an upload late for its slot, a
title over YouTube's limits. `clippa status` opens with what needs a person, then puts services,
recent streams, review counts, the next releases and the month's spend on one screen. Housekeeping
runs on its own too: `clippa streams prune` clears the source video of streams that are finished,
never anything still waiting, scheduled or pinned, and `clippa backup` zips the parts made by hand
(config, schedule, enrolled faces, voices, layouts, cast photos, review state) every week, without
secrets. A nightly report is built on top: when nothing is wrong it sends a one-line DM with no
model call; when something is wrong, Claude Code runs headless with read-only tools and sends one DM
with its diagnosis; if that fails, the raw findings go out instead. A weekly digest covers runs,
uploads, spend and review decisions, and once a month a read-only Claude pass reads those weeks to
find the problems that keep coming back. See [Checkup](Checkup), [Audit](Audit) and
[Backups](Backups).

> [!NOTE]
> **A bug worth telling: the compilation that repeated itself.** A reviewer noticed a speaker
> "repeating himself" two hours into a compilation. Lossless cuts have to start on a keyframe, so
> each segment file began up to five seconds before its real start. Joined back to back, every join
> replayed the tail of the previous part: 13 of 15 joins in that video. The fix ends each part with
> a concat `outpoint` exactly where the next file begins. The cuts stay lossless and the joins are
> now frame-accurate. A regression test covers it.

## Research and context

Clips are only as good as the context behind them. Four tools around the pipeline track the people
in the shows, the news about them, and the stories the shows cover. They follow one rule: a model
can find, sort and summarize, but every claim it makes has to point back to a real source. That
means a quote that appears word for word in a transcript, a headline with its link, or a search
result the code actually fetched. Output that can't be traced back is rejected.

**The shared engine is the [search swarm](Search-swarm): many small searches that follow their own
leads.** A planner model turns a goal (a story brief or a lead) into up to 16 first searches.
Cheaper worker models run eight at a time, each using one tool: YouTube, Kick or Odysee channels,
Reddit and the web, past transcripts, or Clippa's own segments. Each worker marks the results that
fit and suggests up to three follow-ups. A coordinator merges duplicates, queues the new follow-ups,
and stops at a round, task, budget or time limit. The whole task tree is saved so a run can resume.
A model never fetches anything directly: a finding has to be one of the tool's own results, a
follow-up has to parse as a valid query or channel, and any search for a person's whereabouts or
private details is dropped. In one 30-task run it found 109 results (44 strong) across five
platforms for about $0.03.

**Cast library** (`clippa cast`, [Cast library](Cast-library)). Keeps a set of head-and-shoulders
photos of each regular for cover art. Photos come from the streams themselves, Wikimedia Commons,
the person's own channels and image search. It fills itself: each new stream adds the sharpest
frames of anyone still short of photos, and a daily job searches the other sources. Each photo has
to match the person's enrolled face before it is kept, then gets a 4:5 crop and an expression label
("neutral", "laughing", "angry") for about $0.0002. Photos are kept automatically only from sources
with a known origin, never from image search or photo agencies. Covers pick the photo whose
expression fits the story.

**News watcher** (`clippa news`, [News watcher](News-watcher)). Watches headlines for the names
you give it and posts new stories to Discord. One button adds a story to the context for the show's
next stream, so scoring knows what the audience already knows. An hour before a show usually
starts, it suggests that day's stories. A cheap model checks each headline: is it really about this
person and not someone with the same name, is it new, and is it about public conduct. Stories about
private people or anyone's children are never posted. Five outlets on one event become one story.
Only headlines and links are stored, never the articles. A check costs well under a cent.

**Investigations** (`clippa investigate`, [Investigations](Investigations)). Reads a public
figure's own back catalog and comes back with leads: claims that changed over time, numbers that
moved, things promised and never delivered, topics that suddenly took over. Then it checks whether
anyone else has covered each lead. A model pulls checkable statements from each video, and a
statement is kept only if its quote is in the transcript with the numbers and any "not" unchanged.
Patterns are found in code with no model call, and a stronger model writes up or rejects each one.
Rejections are kept with the reason. Every receipt links to the exact moment. Leads are research
for a person, never posted automatically. A full multi-year catalog costs about $14, and later runs
read only new uploads.

**Story dossiers** (`clippa story`, [Story dossiers](Story-dossiers)). Give it a one-line brief
and a time window. The search swarm finds the videos, triage picks which ones are worth
transcribing within an hour budget, and the result is a research dossier: summary, timeline, who
said what, conflicts, gaps and angles. From there it plans an edit with chapters, lower thirds and
every source credited, ready for review or as a narrated catch-up to watch yourself. The timeline
marks each event as shown, reported or speculation. Every line cites a moment or a source. A
citation that doesn't resolve is sent back once, and if it still doesn't resolve, the step fails.
When a re-upload repeats an earlier part, the duplicate is dropped from the edit.

Also built on these:

- **Archive search** ([Archives](Archives)). Thousands of hours of a back catalog transcribed in
  bulk on Modal and indexed with SQLite full-text search, so you can ask questions across years of
  video.
- **Weekly issue** ([Weekly issue](Weekly-issue)). A ten-page magazine of the week's stories, laid
  out and rendered automatically for social carousels.
- **Narrated pieces** ([Documentaries](Documentaries)). Scripts read by an ElevenLabs voice, cut
  together with source clips, and finished in DaVinci Resolve through its scripting API. The case
  study below is one of them.
- **B-roll library** ([B-roll](B-roll)). Free footage for those cuts, found on NASA, DVIDS, the
  Prelinger Archives, Pixabay and Wikimedia Commons. A clip is kept only when its licence is on an
  allow list (public domain, CC0 or Pixabay's) and its credits name no outside owner. Sound is off
  by default, since agency videos often carry licensed music. For a long film, only the part asked
  for is downloaded.
- **Clip art library** ([Clip art](Clip-art)). Pictures for covers and overlays from museum
  open-access collections (the Met, the Cleveland Museum of Art), Commons drawings, open-licence
  icon sets and Pixabay, under the same allow-list rule. Each source's terms were read first; sites
  whose licences couldn't be checked, or that block scripts, were left out.

## Case study: an 11-minute documentary

The same tools made a short documentary about a person viewers kept asking about, someone with real
problems rather than a public figure. Research, edit, sound and the publishing kit were all built
in code. The finished cut went through 19 versions, and each one was rebuilt from a plan and the
director's notes on the last.

That first film took about 145 one-off Resolve scripts, each patched from the last. The same day it
was published, the method became a command, `clippa doc`, so the next film starts from a plan file
instead of a pile of scripts. How to use it is on [Documentaries](Documentaries).

- **Research with limits.** The dossier draws only on what the person posts publicly, plus what had
  already been said about them on air. Rules set before any writing kept out their name, where they
  stay, and the people around them, and the script describes what they say and do rather than
  naming a diagnosis. The script was also rewritten to a neutral, journalistic voice, with every
  claim attributed to its source.
- **An edit built from a plan.** DaVinci Resolve is driven through its Python API. A plan file
  lists every shot, narration line, overlay and fade, and a script lays out the timeline from it.
  Cover shots cut on the narration's word timings, taken from a local transcription of the
  voice-over. Clip and narration overlap at every join, with the picture running under the first
  words and the sound crossfading, so there is no dead air. Before each rebuild, the timeline is
  compared with the plan, so hand edits made in Resolve aren't lost.
- **Sound.** The narration is an ElevenLabs clone of the director's own voice. Lines that sit next
  to each other are voiced in one take and split at word timings, because separate takes sounded
  different side by side. The score was generated to the exact length of each section and
  crossfades at section changes, over a CC0 room tone. Every clip was levelled to a common
  loudness, since the sources ranged from -12 to -31 LUFS. The master is -14 LUFS for YouTube.
- **A plan file in, a mastered film out.** A hand-written TOML plan lists the sections, title
  cards, clips and narration lines, with each cover shot cued on a word of its line. The plan is
  checked when it is read, so a bad cue fails before any narration is paid for. The steps are
  `new`, `voice`, `build`, `master` and `kit`, and each one can be rerun on its own. Word times come
  from a local Whisper model aligned to the script, rather than searched for in what it heard, so a
  number read aloud ("800") can't break a cue. 96% of the script's words were matched on a real
  take.
- **Resolve only places; the work happens before it.** Loudness for every clip, take, music cue and
  room tone is worked out before Resolve opens. The Resolve step only places items, sets gains and
  fades, and reads every one back to check it. The master is a gain and a limiter set 0.8 dB under
  the peak ceiling, because at the ceiling itself the AAC encode peaked 0.6 dB over. A test run
  against live Resolve found that a still image ignores the length it is given and always lands at
  five seconds, pushing everything after it along. Stills are now turned into movie clips of the
  right length before placing. On that run the render came out at exactly the planned 839 frames,
  mastered from -16.0 to -14.0 LUFS.
- **The leak check, built in.** `master` runs the leak check on every render. It transcribes the
  render on a cloud GPU and searches the transcript and the publishing kit for every banned term. It
  catches exact matches, words run together or split apart, close misspellings, plurals and
  hyphenated forms. A known false alarm can be marked so it doesn't fire again.
- **A publishing kit with the cut.** The title, description, chapters taken from the plan's section
  markers, credits, disclosure lines for the AI voice, generated music and public-domain footage,
  and a pinned comment are all delivered with the final render. A person does the upload and ticks
  YouTube's altered or synthetic content box by hand.

> [!NOTE]
> **Search the render, not the script.** The script had been checked, but the final film also
> contains the person's own clips. Before publishing, the rendered video was transcribed and
> searched for every detail on the banned list. That found a place name spoken inside one of their
> clips, at version 18, which no check of the script could have caught. The same search was then
> run over the channel's older uploads, titles and replies, and it found the name in an earlier
> clip's title and in a reply, so those could be fixed too.

## What it is built with

| | |
|---|---|
| **Core** | Python 3, an argparse CLI (58 commands in 5 groups, with did-you-mean suggestions, shell completion, `--json` output and `latest` in place of a stream id), uv, ruff (extra rules and a complexity cap), pytest, GitHub Actions on Linux and Windows |
| **Media** | ffmpeg, WhisperX, OpenCV (YuNet + SFace face recognition), Pillow, Playwright for rendered layouts |
| **AI** | Claude Haiku, Sonnet and Opus, OpenRouter, ElevenLabs, versioned prompt files |
| **Infra** | Docker Compose on a NAS (Debian image, ffmpeg 7.1, Intel Quick Sync), Modal (GPU), Tailscale |
| **Services** | Kitsu (review), discord.py (review bot), YouTube Data and Analytics APIs, X API |
| **Docs** | This wiki, built from the repo on every push, with a test that checks its links; it includes install checklists, a page on how the processes talk to each other, and the model trials. A changelog too |

## How it was built

One person, with an AI pair programmer. Brandon Young designed the system and made the product
calls: what gets automated, where a person stays in the loop, what each stage may spend, and how
the schedule behaves. It was built with Claude Code as a pair programmer. The project's conventions
are written down in the repo ([Developing](Developing)), and every pull request that changes code
gets two reviews before merge. Claude Code reviews the branch locally and its findings are fixed
before the PR opens; then Copilot reviews it on GitHub, and each comment is checked against the
code and answered. For a while a third reviewer, Grok through OpenRouter, ran on every PR. It was
chosen after 13 models reviewed the same real PR, three of which made the same false claim about an
ffmpeg filter that a test disproved. It found real bugs but took about 25 minutes a PR, so it is now
a by-hand second opinion. Dependabot keeps the dependencies current.

About 42,000 lines across 122 modules and some 790 commits in under four weeks, running every day
since the first week.

Once it was running, the code was audited as if handing it to another team, and the findings were
worked through in tiers:

- Broke five import cycles.
- Split the war room into a package.
- Moved prompt versions into one registry that the stages, doctor and a test all read.
- Moved the defaults into a single file.
- Shipped the prompts inside the package.
- Added CI on Windows alongside Linux.
- Added tests where the risk was.

The source is this repository, MIT licensed; the command is `clippa`.
