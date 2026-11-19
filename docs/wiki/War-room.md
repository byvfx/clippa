A web page for running the review and the schedule. `clipper serve` serves it together
with the background jobs (`http://localhost:8765`, `[warroom] port`); `clipper warroom
--open` runs the page on its own. It
shows what is live, where each stream is in the pipeline, the review queue and the upload
schedule. Press <kbd>?</kbd> on the page for every keyboard shortcut.

## Pages and the top bar

The page bar under the top bar has eight pages, one on screen at a time: **Review** (the queue,
with the item open beside it; the page opens here), **Schedule** (Next up, the Week and the tray of items with no slot),
**Pipeline** (the streams and Health), **Money** (Costs, Modal and API usage), **Stats** (the
analytics), **Library** (the cast library, and where faces are named), **Research** (News, Blinds
and Investigations) and **Settings** (the keys, the keyboard shortcuts and the look). The browser
remembers the page. Review counts what waits for you; Schedule counts the approved items with no
slot. Library, Research and Settings open today's windows from a tile each for now.

The top bar is one row: the live shows, four widgets, **Search**, **Chat** and **⋯**.

- **Uploads**: the last 24 hours against `max_per_24h`, as a meter. Opens Schedule.
- **Spend**: this month's model and Modal spend, the Costs panel's total. Opens Money.
- **Pipeline**: how many streams are recording (● live) or running, or idle. Opens Pipeline.
- **The health light**: green when serve and the bot are up and the numbers are fresh, amber past
  45 s or when a job failed, red past 2 min, when serve or the bot is down or the Kitsu sync
  failed. Hover for what's wrong and how old the numbers are. Opens Pipeline.
- **⋯** has the actions that change things: Sync Kitsu, Replan, Undo…, the log, the keyboard shortcuts.

## Search and the G keys

<kbd>Ctrl</kbd> <kbd>K</kbd> (<kbd>Cmd</kbd> <kbd>K</kbd> on a Mac), or **Search** in the top bar, opens
one box that reaches everything: the eight pages, an item by its title, show, kind or name
(`s07`), the ⋯ actions, the Cast, News, Blinds, Investigations and Keys windows, the other look,
the Clippa chat, a live show and the queue tabs. Type a few letters, pick with <kbd>↑</kbd>
<kbd>↓</kbd>, <kbd>Enter</kbd> runs it, <kbd>Esc</kbd> closes it. It shows the best 8 items; the
queue's own search box finds the rest. On a phone it is **Search…** under **More** in the bottom bar.

<kbd>G</kbd> then a letter jumps to a page from anywhere but a box: <kbd>R</kbd> Review,
<kbd>S</kbd> Schedule, <kbd>P</kbd> Pipeline, <kbd>M</kbd> Money, <kbd>T</kbd> Stats,
<kbd>L</kbd> Library, <kbd>F</kbd> Research, <kbd>,</kbd> Settings. An item open on top of the
page closes first (it asks about unsaved edits). The number keys still switch the queue tabs.

The page used to have a Layout button for dragging panels between columns and saving layouts
(`[paths] warroom_layouts`); the fixed pages replace it (2026-11-18), and a saved-layouts file
left on the share is no longer read.

## The chat at the side

The [Clippa messenger](Clippa-messenger) sits at the right of the page: tell Clippa what to do
without leaving the war room. It is the `/chat` page itself in a frame, so it is the same chat
(the same topics, history and cards) wherever you open it. **Chat** in the header, or
<kbd>M</kbd>, shows or hides it, and the browser remembers which; <kbd>M</kbd> also puts the
cursor in its box, and <kbd>Esc</kbd> there hands the keys back to the war room. **↗** opens
`/chat` on a page of its own. While it is hidden, the Chat button counts Clippa's new messages.
On a wide screen the page makes room for it; narrower, it lies over the right edge and the live
player moves left of it. On a phone it is the **Chat** tab at the bottom instead, so it never
covers the review cards.

## Looks

The war room and its chat wear one look, picked on the Settings page (or **Look** in the
chat's buddy list): `warroom`, the default, or `millennium`, the alternate, the same two as the
[Clippa messenger](Clippa-messenger#looks) (`retro` and `pager` were retired on 2026-11-18; a
browser that had one picked goes back to the default). The browser remembers the pick and its
other tabs follow it, `/chat` included. A look is one file in `clipper/web/themes/`; see
[Looks](Clippa-messenger#looks) for what goes in one.

`warroom` looks like a video editor: dark panels edge to edge with flat title bars, one blue
accent for what is selected, a colour per show, and green, amber and red for status only. The
interface is set in Inter and the numbers, times and ids in JetBrains Mono, both shipped in
`clipper/web/fonts/` (SIL Open Font License, the licence files beside them), so the page loads
nothing from outside. The icons are one line set, drawn in the page itself. While a panel waits
for its first numbers it shows a grey outline of its rows.

## Live shows

Click a live show's chip to watch it in a player on the page or on kick.com in a new window;
the chip also says whether the NAS is recording it.
Shows in `[warroom] hide_shows` (e.g. `["trish", "blade"]`) are left off these chips and the
show filter; their streams and items still appear.

## Review queue

Click a card and the item opens in a pane beside the grid, about 40% of the width; drag the
pane's left edge to make it wider (the browser remembers). <kbd>J</kbd> / <kbd>K</kbd> move it
along the queue, opening it if it was closed, and <kbd>Esc</kbd> or ✕ closes it. Below 1100 px,
on a phone, and from any other page (a schedule slot, say) the item opens on top instead.

Each card shows its show as a small tag in that show's colour, the same colour on every page
(`--show-1` to `--show-6` in each look, in `[shows]` order; a show not in `[shows]` is grey), and
the title without its "SHOW NAME |" prefix, which the tag already says (hover for the full title).

The tabs along the top are Waiting, Changes, Approved, Uploaded, Rejected, All and **Retakes**:
the items with a retake, newest first, each card with its last note, when it ran, what it cost and
what it did (the Retakes panel before 2026-11-18). When Waiting is empty it says "Nothing
waiting" and when the next upload goes out.

It works from the keyboard:

| Key | Does |
|---|---|
| <kbd>J</kbd> / <kbd>K</kbd> | next / previous item |
| <kbd>A</kbd> | approve |
| <kbd>R</kbd> | reject |
| <kbd>C</kbd> | retake note |
| <kbd>H</kbd> | hold |
| <kbd>Space</kbd>, arrows | play, seek the video |
| <kbd>O</kbd> | pop the video out into its own window |
| <kbd>B</kbd> | Who's this? (a face on the paused video) |
| <kbd>M</kbd> | show or hide the [chat](#the-chat-at-the-side) |
| <kbd>1</kbd>–<kbd>7</kbd> | the queue tabs (7 is Retakes) |
| <kbd>/</kbd> | the search box |
| <kbd>Ctrl</kbd> <kbd>K</kbd> | [search everything](#search-and-the-g-keys) |
| <kbd>G</kbd> then a letter | [jump to a page](#search-and-the-g-keys) |
| <kbd>Esc</kbd> | close the item |

<kbd>Tab</kbd> reaches the card titles, schedule slots and live chips too; <kbd>Enter</kbd> or
<kbd>Space</kbd> opens them. The card buttons name their key on hover.

The search box looks through every stream's titles, descriptions, topics and hashtags, and
what is said in each video (its captions). Show, kind, status, order and dates sit behind
**Filters**; each one that is on shows as a chip beside it (× clears it, "Clear all" clears them
all), and they combine with the search. The queue is newest stream first; the order filter sorts
by score, length or kind instead. The view,
order included, is in the page's URL, so it can be bookmarked.

Tick items (shift-click for a range, "Fill the gaps" on a phone, or "Select shown") to approve, reject, hold, release,
pin, reopen or send the same retake note to all of them, or to find and replace in their
titles, descriptions, hashtags, tags or playlists: the dry run lists every line that would
change before anything is written. A selection survives a change of tab or filter, but bulk
actions only touch what is on screen: the bar says how many picks are left out.

The health light in the top bar shows the age of the page's numbers (amber past 45 s, red past
2 min). When a refresh fails it says Offline there (hover for why), with one
toast when it starts and one when serve is back; before the first answer the panels say
Loading… or why they are empty. A tab in the background stops polling and catches up when
you come back. A page whose sign-in has gone says Signed out there and asks for the `?key=` link again.

A new title for an item that's already on YouTube renames the video there too
([Scheduling and uploads](Scheduling-and-uploads#renaming-a-video-thats-up)): the item window
says so above the title box and asks before it saves, and find and replace's preview marks
which items it would rename. Its description and hashtags stay Kitsu-only.

Decisions, hold, pin, title/description/hashtag edits, find and replace and thumbnail words
can be undone from their toast for 10 s or from "Undo…" under **⋯** (the last 20). Undo goes through
Kitsu too, and an item already on YouTube keeps its live text (change it in Studio), except its
title: undoing a title change made while it was up renames the video back on YouTube (one made
before the upload is undone in Kitsu alone). Not on
the list: the cover editor, faces, cast, and a retake already started (undo puts the item's
status back; the retake still runs).

What costs money says so first. A retake note says what a retake costs (the average of the
recent ones), a note sent to several items asks before it starts that many, and the Retakes
tab shows each one's cost and this month's count and total. Cast's Find line says what its
labels can cost.

A button sends once: a double tap is one request, and Sync Kitsu and Replan (under **⋯**) stay greyed out
until they finish. Closing an item, opening another card beside it, or moving on with J/K, a swipe or Approve, asks
before throwing away a title, description, note or release time you typed but didn't save.

Titles, descriptions, hashtags and cover words are checked as you type ("hosts", unmasked
swearing, YouTube's length limits with the show prefix), and cards with a problem carry a ⚠
badge. The badge counts the [pre-flight checks](Review#pre-flight-checks)' findings too (a face cut
off, the wrong person on the cover), and the item's window lists them under **Checks**.

## Cover editor

"Edit cover" in an item's window opens it: pick the main frame (shift-click for the inset)
from the candidate frames, drag and resize the face boxes, choose a photo or teaser photo
from `[thumbnail] photos` (a cast library person by label too: "Jer · angry"), change the words. Preview renders without saving; Save re-renders
with no model calls (every cover of the stream when the teaser changes) and re-attaches it in
Kitsu.

## Faces

Who is who on screen (#54), kept in `[paths] faces/known.json` for every show.

- **A stream's groups:** "Faces" in an item's window, or "faces" on a scanned stream in
  the pipeline panel. Each group of faces `clipper faces` found has its contact sheet (first
  row its clearest faces, second a spread over the stream), its name, and whether that came by
  hand or by face (with the match, 0.7+ names a group). Type a name and press Name (this stream
  only) or Name + enroll (every stream; the stream's look-alike groups are named at once).
  "✓ Right" keeps a by-face name as a hand one; Clear removes a name. Filter by named/unnamed.
- **Who's this? (B):** pause the video on a face, press B or the button, and drag a box round
  one face. clipper takes that frame at full size, finds the face in the box (small ones too)
  and says who it looks like; Keep this face adds it to the known faces and names the stream's
  look-alike groups. For a face too rare or small to make a group: a guest, a costume, a
  picture-in-picture. Not on a phone (no inline video there).
- The × on a known face forgets that person (every sample). Hand names stay.

The command line does the same: `clipper faces <stream> --name F03=Jer --enroll`,
`clipper faces <stream> --at 1:23:45 --box x,y,w,h --person Jer`, `clipper faces --forget Jer`.

## Heat

**heat** on a stream in the Pipeline panel, or beside the stream's name in an item's window,
opens that stream's heat graph: the curve the [signals stage](Pipeline) makes from chat and
loudness, over the whole stream. The picker at the top changes the stream.

- The shaded bands are the windows clipper looked at (`windows.json`), each with a dot at its
  peak; the dashed line is the least heat a window needs (`[signals] min_heat`). A grey stretch
  had no chat recorded.
- Under the curve, each cut segment, Short and clip sits where it came from, coloured by where
  it is: not in review yet, waiting, changes asked, approved, scheduled, uploaded or rejected.
  The legend counts them. Opened from an item, that item is outlined.
- Point at the graph (or tap it) for the time, the heat, the window's chat, laughing, "clip"
  and loudness scores, and the items there. Click an item to open it; Tab reaches them too.
- The table lists the twelve hottest windows with what was said at the peak (when the transcript
  was there as signals ran) and what was cut from each, or "nothing cut here".

A long stream is wider than a phone's screen: the graph scrolls sideways. It shows the hottest
second of each stretch (about 1,200 across the stream), so a short peak isn't averaged away.
Nothing here costs anything or changes anything.

## News

Headlines about the shows' people (`clipper news`, #110). **News** on the Research page opens it.

- **Stories** from the last 1, 3, 7 or 30 days, newest first: the headline the judge wrote, strong
  or maybe, who and which show, why it matters, and the outlets' links. "posted" means the bot
  put it in Discord (`clipper serve` finds them, the bot posts them); "in context" that it was
  added to a show's next stream.
- **Tonight's context…** shows the text the stream would get and the show it goes to (change it
  in the menu); **Add to its next stream** queues it. **Dismiss** hides a story and the bot never
  posts it.
- **Search** asks Google News now (the last 7 days) and adds the stored headlines that match.
- The header line says how often serve checks and how many sources are failing (hover for why).

## Blinds

Gossip blind items with their clues read (`clipper blinds`, #111). **Blinds** on the Research page
opens it: each blind's text, its typed clues, the claim, and a box for your guess, which you give before
clipper looks into anything. The whole of it is on [Blind items](Blind-items#the-war-room).

## Keys

The API keys serve itself has, which on the hub are the ones serve, the bot and the worker use.
**Keys** on the Settings page opens it. Each key shows its env var, where it was found (`$ANTHROPIC_API_KEY`,
or its file in `secrets/`), what uses it, and the last answer from its provider: ✓ works,
✗ rejected, or ? (no answer about the key, or not reachable). **Check** asks one provider again,
and **Check all** asks every one that's set. Each check is one free request (a model list or a
"who am I"); Brave, Pixabay, DVIDS and sync.so aren't asked, since there's no free way to. The
answers are kept in `streams/.serve/keys.json`. Streams the worker holds for a rejected key
([How the processes talk](How-the-processes-talk#child-processes)) are listed at the top.
No key's value ever reaches the page. To change one, edit `deploy/hub/env.local` (or its file in
`secrets/`), then run `deploy.sh`, which checks it against the laptop's own first
([NAS-hub](NAS-hub#deploy)). `clipper keys` prints the same table for the machine it runs on
(`--check` asks, `--check Anthropic` asks one; exit 1 on a rejected key).

## Cast

The cast library (#41): a few head-and-shoulders photos of each regular in `[thumbnail]
photos/<Person>/`, for covers and the weekly. **Cast** on the Library page opens it.

- **The list** is everyone with photos or an enrolled face, with how many are kept and new;
  "!" marks fewer than `[cast] min_photos`. "Someone new" adds a person.
- **A person:** other names and channels (YouTube `@name` or Kick addresses), saved with Save;
  the channels are what Find > Channels reads.
- **Find:** Commons (Wikimedia, freely licensed), Channels (their avatar and recent video
  thumbnails), Image search (Brave; needs `$BRAVE_API_KEY`), a photo's web address, or a stream
  id (that stream's sharpest faces of the person, named in its Faces window). One runs at a
  time in the background; the window shows its progress and fills in. Each photo is checked
  against the person's known face, so other people are left out, and labelled by a small model.
- **New photos** come first, blue-edged; ⚠ is the labeller's doubt (a designed thumbnail with
  text, a hidden face). Keep or Drop each; fix a label with its menu. Drop asks first: the photo is
  deleted and its source never fetched again, with no undo. Kept photos are what covers use: the cover model picks
  the label that fits the headline (angry for a meltdown, laughing for a gloat), else neutral;
  a stream, Commons or channel photo before an image search one. The weekly draft does the
  same for people it pictures from a photo, and everyone with a kept photo joins the prompts'
  known figures.

Photos also arrive by themselves (the worker after each stream, serve's daily `cast auto`);
the sure ones are already kept (not from image search, an enrolled face matched well, a label
with no doubt), so
look over Kept too and drop what's wrong.

The command line: `clipper cast list|grab|find|add|keep|drop|label|set|auto` ([Cast library](Cast-library)).

## Schedule and uploads

The Schedule page answers "what goes out next". **Next up** across the top shows the next five
uploads with their covers and how long until each (a red edge: its last upload failed). Under it,
**Week** shows seven days from today, hours down; the arrows page a week at a time out to the
scheduler's `horizon_days`. Each clip is a block in its show's colour with a stripe for its kind:
filled once it is uploaded (private until it goes public), outlined until then, faded once its time
has passed, with a pin when it is pinned. Each day's head shows its count against `max_per_day`, a
red line marks now, and the open slots are dashed. Hover a block for the whole title and its
state; click it to open the item. **List** shows the days one under the other instead, as before
(a phone always gets the List and the next three).

**Ready to slot**, beside it, lists the approved items with no slot that can still go out, newest
stream first: the same number as on the Schedule tab. The ones the scheduler let go (over
their stream's budget or stale, see [Staying current](Scheduling-and-uploads#staying-current))
are one link under it, "N not going out", which opens them in the queue; a pin there (Pin or
Next open slot in the item) sends one anyway. Drag an item or a block onto an open slot (or a
day's head) to pin it, or onto "unpin"; the page warns before a drop breaks the spacing rules.

Or let clipper pick the slot: the arrow-to-a-line button beside an item (or **Next open slot** in its window) finds
the first open slot of its kind that the scheduler's rules allow: the slot times, `max_per_day`,
`min_gap_minutes`, `kick_first_hours` after the stream and `[youtube] min_lead_minutes` from
now. **Segments to open slots…** does that for every approved segment without a slot, but
only into a slot before its `stale_days` cutoff, as the scheduler would. Both show a preview
first and pin through Kitsu ("Publish at"), like the Pin button, then replan; Undo takes the
pins back. A pin counts against its stream's `stream_budget` first, so pinning a segment from a
stream whose budget is full bumps that stream's weakest automatic one: the preview names it.
The bulk button leaves those streams out, since a pin there would only swap one for another.
Raise `stream_budget` to send more of a stream.

The Uploads panel under the tray shows uploads in the last 24 h against the cap, the last automatic
run and its errors, and what goes next. Uploaded slots show views and a YouTube link.

## The log

**Log** on the Health panel (or **⋯** > the log) shows the end of the serve or bot log (`[paths] serve_log`,
`bot_log`), with a word filter and an "errors and warnings" switch. An error toast that says
"details in the serve log" has a Log button that opens it filtered to errors. Values of secret
environment variables and the page's key are blanked before the lines leave serve. A log set
to `""` (console only) says to use `docker compose logs` instead.

## Modal, costs and analytics

The Money page has the first three, the Stats page the analytics.

- **Modal**: the month's Modal bill, the spend rate, GPUs up, transcriptions
  running and failed, and each archive's progress. `clipper modal` prints the same.
- **Costs**: the month added up (the Spend widget shows its total). Every model call (logged as it is made, with its
  stage and stream), Modal runs, and `[costs] monthly` fixed costs, by stage and show.
  `clipper costs` prints the same; revenue joins it once the channel is monetized.
- **API usage**: the month by the Claude API's and OpenRouter's own count,
  today, by model, OpenRouter's week, limit and balance, each next to what our log recorded.
  Claude's needs an Admin API key ([Costs](Costs#the-providers-own-numbers));
  `clipper costs --providers` prints the same.
- **Analytics**: a month of YouTube. Views, watch time and
  subscribers against the month before, views per day, where views came from (Shorts feed,
  search, ...), and the first-7-day numbers of that month's uploads by show, kind, weekday and
  hour, with the best and weakest. `clipper analytics --report` writes the same as
  `reports/analytics/<month>.md` plus a .csv of every upload, and ends with
  [what predicts views](Scheduling-and-uploads#what-predicts-views), which the panel leaves out.

## On a phone

The page bar moves to the bottom: Review, Schedule, Pipeline, Chat and More, which lists the other
pages. The top bar keeps the Uploads widget, the health light and **⋯**.
Items open full screen with the decisions at the bottom and swipe to the next one, and videos
play from a 360p copy (`lowres/` beside each preview, about a quarter of the data). It can be
added to the home screen. Away from home it goes through Tailscale: `clipper warroom --tailnet
on` ([Phone access](Phone-access)).

## For developers

The page is in `clipper/web/` (`warroom.html` and what it loads). `warroom.css` is structure
only: every colour and the shape of windows, title bars, buttons and fields comes from the look's
file in `web/themes/` (`themes/warroom.css` is the one place the default's colours live, and
declares its fonts from `web/fonts/`), loaded by `theme.js`, which the chat page shares. The icons
are `<symbol>`s in a sprite at the top of `warroom.html`, each with the id `i-` and its name;
markup points an `<svg class="ic">`'s `<use>` at one and scripts call `ic("name")` (`js/core.js`), and a test
checks that every name asked for is in the sprite and that no emoji stands in for one. `js/chatdock.js` shows and hides the chat frame. The server is the `clipper/warroom/`
package. `httpd.py` holds the route tables `GET_API` and `POST_API`, which map each `/api/...` path to
a function; `data.py` builds the page's data, `review.py` the decisions (through `clipper/actions.py`),
and each panel has a module of its own.
