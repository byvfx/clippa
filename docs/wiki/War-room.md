A web page for running the review and the schedule. `clipper serve` serves it together
with the background jobs (`http://localhost:8765`, `[warroom] port`); `clipper warroom
--open` runs the page on its own. It
shows what is live, where each stream is in the pipeline, the review queue and the upload
schedule. Press <kbd>?</kbd> on the page for every keyboard shortcut.

## Live shows

Click a live show's chip to watch it in a player on the page or on kick.com in a new window;
the chip also says whether the NAS is recording it.
Shows in `[warroom] hide_shows` (e.g. `["trish", "blade"]`) are left off these chips and the
show filter; their streams and items still appear.

## Review queue

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

<kbd>Tab</kbd> reaches the card titles, schedule slots and live chips too; <kbd>Enter</kbd> or
<kbd>Space</kbd> opens them. The card buttons name their key on hover.

The search box looks through every stream's titles, descriptions, topics and hashtags, and
what is said in each video (its captions). Status, date and show filters combine with it. The
queue is newest stream first; the order menu sorts by score, length or kind instead. The view,
order included, is in the page's URL, so it can be bookmarked.

Tick items (shift-click for a range, "⇕ Fill the gaps" on a phone, or "Select shown") to approve, reject, hold, release,
pin, reopen or send the same retake note to all of them, or to find and replace in their
titles, descriptions, hashtags, tags or playlists: the dry run lists every line that would
change before anything is written. A selection survives a change of tab or filter, but bulk
actions only touch what is on screen: the bar says how many picks are left out.

"Updated N s ago" in the header is the age of the page's numbers: amber past 45 s, red past
2 min. When a refresh fails it says "serve isn't answering" there (hover for why), with one
toast when it starts and one when serve is back; before the first answer the panels say
Loading… or why they are empty. A tab in the background stops polling and catches up when
you come back. A page whose sign-in has gone says so and asks for the `?key=` link again.

Decisions, hold, pin, title/description/hashtag edits, find and replace and thumbnail words
can be undone from their toast for 10 s or from "Undo…" (the last 20). Undo goes through
Kitsu too, and an item already on YouTube keeps its live text (change it in Studio). Not on
the list: the cover editor, faces, cast, and a retake already started (undo puts the item's
status back; the retake still runs).

What costs money says so first. A retake note says what a retake costs (the average of the
recent ones), a note sent to several items asks before it starts that many, and the Retakes
panel shows each one's cost and this month's count and total. Cast's Find line says what its
labels can cost.

A button sends once: a double tap is one request, and Sync Kitsu and Replan stay greyed out
until they finish. Closing an item's window, or moving on with J/K, a swipe or Approve, asks
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

- **A stream's groups:** "Faces" in an item's window, or "👤 faces" on a scanned stream in
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

**🔥 heat** on a stream in the Pipeline panel, or beside the stream's name in an item's window,
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

Headlines about the shows' people (`clipper news`, #110). **News** in the header opens it.

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

Gossip blind items with their clues read (`clipper blinds`, #111). **Blinds** in the header opens
it: each blind's text, its typed clues, the claim, and a box for your guess, which you give before
clipper looks into anything. The whole of it is on [Blind items](Blind-items#the-war-room).

## Keys

The API keys serve itself has, which on the hub are the ones serve, the bot and the worker use.
**Keys** in the header opens it. Each key shows its env var, where it was found (`$ANTHROPIC_API_KEY`,
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
photos/<Person>/`, for covers and the weekly. **Cast** in the header opens it.

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

The schedule shows the open slots of the next two weeks and the approved items with no slot,
newest stream first. The ones the scheduler let go are listed too, tagged "over budget" or
"stale" (see [Staying current](Scheduling-and-uploads#staying-current)): a pin sends one anyway.
Drag an item or a scheduled slot onto an open slot (or a day) to pin it, or onto "unpin"; the
page warns before a drop breaks the spacing rules.

Or let clipper pick the slot: ⇥ beside an item (or **⇥ Next open slot** in its window) finds
the first open slot of its kind that the scheduler's rules allow: the slot times, `max_per_day`,
`min_gap_minutes`, `kick_first_hours` after the stream and `[youtube] min_lead_minutes` from
now. **⇥ Segments to open slots…** does that for every approved segment without a slot, but
only into a slot before its `stale_days` cutoff, as the scheduler would. Both show a preview
first and pin through Kitsu ("Publish at"), like the Pin button, then replan; Undo takes the
pins back. A pin counts against its stream's `stream_budget` first, so pinning a segment from a
stream whose budget is full bumps that stream's weakest automatic one: the preview names it.
The bulk button leaves those streams out, since a pin there would only swap one for another.
Raise `stream_budget` to send more of a stream.

Above it, the upload panel shows uploads in the last 24 h against the cap, the last automatic
run and its errors, and what goes next. Uploaded slots show views and a YouTube link.

## The log

**Log** on the Health panel shows the end of the serve or bot log (`[paths] serve_log`,
`bot_log`), with a word filter and an "errors and warnings" switch. An error toast that says
"details in the serve log" has a Log button that opens it filtered to errors. Values of secret
environment variables and the page's key are blanked before the lines leave serve. A log set
to `""` (console only) says to use `docker compose logs` instead.

## Modal, costs and analytics

- **Modal** (under Health): the month's Modal bill, the spend rate, GPUs up, transcriptions
  running and failed, and each archive's progress. `clipper modal` prints the same.
- **Costs** (below it): the month added up. Every model call (logged as it is made, with its
  stage and stream), Modal runs, and `[costs] monthly` fixed costs, by stage and show.
  `clipper costs` prints the same; revenue joins it once the channel is monetized.
- **API usage** (below Costs): the month by the Claude API's and OpenRouter's own count,
  today, by model, OpenRouter's week, limit and balance, each next to what our log recorded.
  Claude's needs an Admin API key ([Costs](Costs#the-providers-own-numbers));
  `clipper costs --providers` prints the same.
- **Analytics** (full width, under the queue): a month of YouTube. Views, watch time and
  subscribers against the month before, views per day, where views came from (Shorts feed,
  search, ...), and the first-7-day numbers of that month's uploads by show, kind, weekday and
  hour, with the best and weakest. `clipper analytics --report` writes the same as
  `reports/analytics/<month>.md` plus a .csv of every upload, and ends with
  [what predicts views](Scheduling-and-uploads#what-predicts-views), which the panel leaves out.

## Layouts

On a desktop the panels can be moved: Layout > Arrange panels, then drag a panel by its title
into the top, left, middle, right or bottom zone, or into the hidden tray. Without a mouse,
focus a panel's title: ↑/↓ move it within its zone, ←/→ to the next zone, H hides it. Each
side column has a width. The browser keeps the arrangement; Layout > Save as names it in
`[paths] warroom_layouts`, where every machine can pick it.

## On a phone

It switches to one view at a time (Review, Schedule, Pipeline and Stats tabs at the bottom).
Items open full screen with the decisions at the bottom and swipe to the next one, and videos
play from a 360p copy (`lowres/` beside each preview, about a quarter of the data). It can be
added to the home screen. Away from home it goes through Tailscale: `clipper warroom --tailnet
on` ([Phone access](Phone-access)).

## For developers

The page is in `clipper/web/` (`warroom.html` and what it loads). The server is the `clipper/warroom/`
package. `httpd.py` holds the route tables `GET_API` and `POST_API`, which map each `/api/...` path to
a function; `data.py` builds the page's data, `review.py` the decisions (through `clipper/actions.py`),
and each panel has a module of its own.
