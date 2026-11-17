Approved items get release slots (`clipper schedule`, written to `schedule.json`) and go to
YouTube as private videos with a publish time (`clipper upload`), or stay private for you to
publish by hand with `[youtube] manual_publish`. Everything uploads to one YouTube channel;
bringing a new one up is [YouTube channel](YouTube-channel).

## Automatic uploads

> [!NOTE]
> Uploads are automatic: don't run `clipper upload` or `clipper review publish` by hand
> unless you mean to.

`clipper serve` replans and uploads every `[youtube] auto_upload_minutes` (the default 0 means
only by hand, and `clipper doctor` says so on a serve machine), at most `[youtube] max_per_24h` in any 24 hours, so hourly runs stay under the
channel's daily upload limit. Pre-shows and stories set to "asap" go out on the next run,
outside the release slots.

`clipper status` is the one screen for all of this: the services' heartbeats and the recorder,
the worker's streams, review counts, the next releases and the month's spend (`--days N` looks
further ahead; `--sync` reads Kitsu first).

## Staying current

To stay current rather than build a backlog, `[schedule] stream_budget` caps each stream's
releases per kind (scaled by its length; the best Shorts by score, segments by their
[segment score](Pipeline#segment-scores) with chat heat breaking ties, or by heat where a
stream has no score, and compilations by their best segment) and `stale_days` lets go of whatever isn't out that many days after it could
first go. Items let go stay approved in Kitsu (war room filter "Not going out"); pin one with
"Publish at" to send it anyway. A `stream_budget` kind at 0 goes only when pinned. A big night
can jump the queue with `[schedule] priority = true` in its stream config
([Configuration](Configuration)).

In the [war room](War-room) the schedule shows the open slots of the next two weeks; drag an
item onto a slot to pin it.

## What predicts views

The budget is only as good as what it ranks by. `clipper analytics --lift` checks each ranker
against how the uploads did in their first 7 days (no model call; it reads `analytics.json`, so
run `clipper analytics` first if it's stale):

```bash
clipper analytics --lift                      # every show and kind
clipper analytics --lift --show kino --kind segment
clipper analytics --lift --json
```

The rankers are the Score column (Shorts and clips), the chat heat segments and compilations
are ranked by, the topic's track record (the median 7-day views of the show's earlier uploads
on the same topic, counting only those whose 7 days were over when the stream began), and the
[segment score](Pipeline#segment-scores): `model` from segments.json, `bt` and `bt_tx` from the
backtest (summaries, and with the transcript).
For each, per show and kind: how many uploads have a value, Spearman's rank correlation with
7-day views (+1 orders them exactly as views do, 0 is no relation, nothing under 5 values),
and, inside each stream with 4 or more, the views per upload of the uploads ranked above the
middle against those below it (uploads tied across the middle count in neither), with the
number of streams where the top half won. Median views per score follow the table. Each show
stands alone: Kino's uploads draw far more than Channel 404's, so a row pooling them would
mostly measure the show.

Only what went out has views, so a ranker is checked on what it let through, not on what the
budget dropped. `clipper analytics --report` ends with the same table, over every upload so far.

## Past the daily limit

Upload the file in Studio and `clipper upload --attach <item> <link>` fills in the rest (title,
description, tags, thumbnail, captions, playlist, Kitsu link). `clipper upload --next 5` lists
what goes out next with each file.

## Source links

Descriptions link to the longer video each one comes from (a clip to its segment at the clip's
offset, a segment to its compilation) once that video is public; every upload run adds the
link to videos already up when their longer video goes public (`[youtube] source_links`).

A Short's "related video" (the link under it to its segment) can only be set in Studio:
`clipper upload --related` lists the uploaded Shorts whose segment is out, with the Studio link
and the segment id to search for, and `clipper upload --related <keys or links>` records the
ones you set. The daily digest lists them too.

## Renaming a video that's up

`clipper review retitle <item> <new title>`, or a retitle from the
[Clippa messenger](Clippa-messenger#renaming-a-video-thats-up), renames a video already on
YouTube: the title gets the show name and bleeps as an upload's does, the rest of the video's
details stay as YouTube has them, and Kitsu's title column changes after YouTube's. 51 quota
units each, from the spare only.

## Quota

Every YouTube API call is counted per Pacific day in `logs/youtube_quota.json`; the link
updates and retries of captions that failed to go up (`clipper upload --captions`) only spend
what is left after room for the day's remaining uploads (`[youtube] daily_quota`,
`upload_units`). Details in [YouTube channel](YouTube-channel), "API quota".
