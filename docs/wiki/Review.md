Nothing goes out before a person approves it. Review happens in
[Kitsu](https://www.cg-wire.com/kitsu), the record for every decision, with one production per
show and one episode per stream ([Kitsu](Kitsu) sets it up). The Discord bot mirrors each
item as a card with buttons, and the [war room](War-room) (a web page `clippa serve` serves)
shows what is live, where each stream is in the pipeline, the review queue and the upload
schedule, with bulk edits, undo, a cover editor and a drag-and-drop schedule. A second person
reviewing from a phone starts at [Reviewing](Reviewing).

## Decisions

Approve, reject, hold, or leave a note asking for a change. A note becomes a retake: the item
is re-cut or re-thumbnailed and comes back for review. A note to start or end a compilation
earlier or later trims its first or last segment, which comes back for review too; the middle
of a compilation can't be changed by a note. Approved items are scheduled and
uploaded ([Scheduling and uploads](Scheduling-and-uploads)). `clippa review status` shows the
decisions and what's scheduled.

## Pre-flight checks

Before an item reaches you, `clippa review publish` looks at it the way you would
(`clippa review check <stream>` by hand; `[review] checks = false` turns it off):

- **Text**: the `[checkup] words` ("hosts" and the like), YouTube's limits, a word `[bleep]`
  would mask, no description.
- **A Short's webcams**: frames of the rendered Short through the face detector. A webcam panel
  with no face in most frames, or a face cut off at the panel's edge (an eye, the nose or the
  mouth past it; a chin past the edge is only someone leaning in), is a finding. The webcam
  panels are the ones marked `cam = true` in the show's layouts (or `focus = "face"`); a layout
  with neither isn't checked. The mark doesn't change the picture, so adding it renders nothing again.
- **The cover**: the face on it against the person the cover is meant to show, by the known
  faces ([Faces](Faces)), and one vision call (`[review] check_model`, about a cent a cover) on
  the finished cover: the right person, a face cut off, words that don't read.
- **The subject**: a person the title names whom the transcript around the item never names
  (and its segment isn't about), and a segment or compilation whose first `[review]
  open_minutes` (15) name someone else three times or more and its subject never: the cut may
  start on another story.

The text checks read the title, description, hashtags and tags as they'll upload: a change you
made in Kitsu (as of the bot's last sync) over the draft's. What it finds goes in the stream's `checks.json`, the Kitsu **Checks** column, a Findings field
on the Discord card, the war room's ⚠ badge and item window, and `clippa status` ("2 with check
findings"). An item is checked again only when something it reads changed (`--force` to check
anyway; `--dry-run` prices the vision calls; `--no-vision` skips them). A check that fails is a
finding too; one that failed on OpenRouter's in-flight budget (a moment's crowding) is checked
again on the next run. A missing OpenCV is the exception: a cover face match, or a Short frame that
actually goes through the webcam check, needs it (`uv sync --extra faces`, the same extra as
[Faces](Faces)). Without it the run stops after the other items have been saved, instead of
recording a bad cover; those items are saved with their text findings and a `not fully
checked` finding that names the fix, and no signature, so the next run checks them again
without `--force`. An OpenRouter spending limit stops a run the same way: what was checked is
kept, and the rest get a `not checked` finding. The review stage of a publish doesn't stop on
either: the items go to Kitsu with that finding in the **Checks** column, and the results that
still match their files are left as they are. One saved from that failure before (a cover check that
failed on the import) is checked again without `--force` too. Findings don't stop anything: they say
where to look, and an item with any waits for you rather than
[approving itself](#approval-by-silence).

## Approval by silence

A show can let its clean items approve themselves: `[shows.<key>.review] auto_approve = true`
(off by default). Every 10 minutes serve runs `clippa review auto`, which approves an item when:

- nobody has acted on it: no decision, hold, note, edit or reopen, from Discord, the war room,
  the command line or Kitsu itself. Anything you do leaves it to you for good (a hold Clippa
  set itself, for [other voices](#shorts-held-for-other-voices), only while it's on);
- its [pre-flight checks](#pre-flight-checks) found nothing (run again just before, so an item
  re-rendered since is checked anew). Once an hour the job also checks again the items held only
  by their findings (or by a check run that failed), so a stale finding clears itself once what
  it read or the checks' version changes; one that still holds costs no vision call. That updates
  `checks.json` (the war room, the card, `clippa status`) and the Kitsu Checks column (Clippa's
  own column: a change there never counts as your edit). Shows without auto-approval get the same
  hourly recheck of their pending items with findings, so their ⚠ badges don't go stale, as long as
  one show has it on (serve's job runs only then). An item that is no longer one of the stream's
  review items is left out until its checks change. An item never checked still waits for you;
- its score is at least its kind's floor (`auto_min_short = 6`; picks, segments and compilations
  are 0, which leaves them to you);
- it has been in review `auto_grace_minutes` (120).

The approval is an ordinary Kitsu decision with the rule in its comment ("Approved automatically:
no action in 120 min, checks clean, score 7"), and the item is scheduled at once. Until then its
Discord card and war room window say "approves itself Sat 14:30 unless you act", or why it waits
for you ("waits for you: score 5 under 6"). `clippa review auto --dry-run` lists the same for
every open item without changing anything. To take one back, reopen it or reject it: an upload
stays private until its publish time.

A **pre-show** approves itself at publish when its checks are clean (`auto_preshow = "now"`;
`"grace"` gives it the window, `"off"` leaves it to you). Pre-shows go public the moment they
upload, so nobody will have seen one before it is public. Stories and one-offs always wait for you.

The approval holds the stream's `auto.json` lock only for the Kitsu decision itself, and gives
Kitsu 25 s for it, so your own action at the same moment (which waits up to 60 s for that lock)
always lands after it and wins. If Kitsu takes the approval but its answer is lost, the next pass
sees it in Kitsu and counts it as automatic, not as yours.

`clippa doctor` (role serve) says which shows have it on and warns when serve, which does the
approving, isn't running. A new revision of an item (a re-render) takes an automatic approval
back: the item is judged again. What happened is kept in the stream's `auto.json`. The weekly [audit](Audit) counts the items that
approved themselves, and the [checkup](Checkup) names any that a person then rejected, reopened,
held or sent back: the rule let through something it shouldn't have. On the past Shorts (11-14), the
rule at 6 would have approved 105 of 181 approved Shorts by itself and one of four rejected ones
("too much Jer", a taste call the window is for).

## Shorts held for other voices

A Short whose voices other than the show's own people (`[meta] cast`) run over `[scoring]
max_source_audio_s` (5 s) arrives held: the speaker tags count the seconds of `[?]` voices
(usually a video being played) and other names, and the Kitsu "Other voices" column and the
Discord card show them ("22 s of other voices (limit 5 s)"). Pass B is meant to drop those, but
some got through. Watch it: if the show's commentary carries the moment, release the hold and
it schedules as usual; otherwise reject it or ask for a trim. A hold you release isn't put back,
and a re-cut under the limit comes off hold by itself. It needs speaker names
([Speakers](Speakers)); a show without them is not checked. `[shorts] hold_source_audio = false`
turns it off.

## Alerts

The bot also DMs you when something needs you (`[notify]`): a stream ready for review, an
upload failing, a stage failing after its retries, recorder trouble, the streams share
filling up, a daily digest at 09:00, and the morning [checkup](Checkup) report. Quiet hours
(00:00-08:00) hold them until morning. `clippa alert test` sends a test DM; `clippa alert
digest` prints the digest; `clippa alert send "text"` queues one of your own.
