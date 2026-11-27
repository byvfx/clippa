# Changelog

Notable changes, newest first. A change you'd notice gets a bullet here in the PR that makes it (wiki
Developing#changelog). The dated diary is `docs/log/<YYYY-MM>.md`; `HANDOFF.md` is the current state.
The version is the last release's number (`clippa/__init__.py` and `pyproject.toml`, which a test
keeps equal) and, past a release, the commit: `clippa --version` prints `0.3.0` on the tag and
`0.3.0+3.g570bb87` after it. A release is a git tag `vX.Y.Z`; its GitHub Release shows the lead of
that version's section (the lines before its first `###`) and a link to this file. The steps are wiki
Developing#releases.

## Unreleased

### 2026-11

- 11-26: a `clippa fetch --fixup` that rewrites a source already transcribed (one done before #440's
  check) now says its `words.jsonl` has the old timeline's times and names `clippa transcribe <id>
  --force`, since a plain transcribe keeps it as done.
- 11-26: an archive item whose source `clippa archive transcribe` refused for a jumping timeline
  can now be repaired (#446): `clippa archive fixup <subject> <item>` remuxes the kept copy in place
  (Kick and HEI alike, no new download), drops the audio decoded from the old one and says to run
  `archive transcribe` again. The refusal's line names that command; a kept `source.mp4` can't be
  remuxed, so its line says to move it out of the folder (kept, never deleted: a gone VOD can't
  come back) and let the next run download a `.ts`; when the item's link is dead, where no `.ts`
  will come, it says to leave the video in place and repair it by hand. It waits while a show
  is live (`--force` overrides), and the chat offers it as a card. `archive transcribe` no longer
  skips a dead link whose item keeps a copy here (a video, or a source set aside): it reads it.
  An `archive transcribe` whose source a fixup replaces while it reads the audio transcribes nothing,
  and the next run reads the repaired copy.
- 11-26: every stage now reads media as local files only (#447): cut, Shorts, previews, thumbnails,
  faces, the war room's low-res copies, the archive's flac and the rest pass `-protocol_whitelist file`
  to ffmpeg and ffprobe, so a file that is really a playlist (or a concat list naming an address) fails
  instead of making them open urls. B-roll keeps fetching its addresses as before. A link at a name
  ffmpeg writes (the archive's `audio.flac`, the `.tmp.mp4` files) is removed first, never what it
  points at. A test fails on a new ffmpeg call that skips it. The recorder's own join is left as
  it was for now.
- 11-26: a share that won't say whether a file is a link (a refused stat, a stale handle) is now one
  line naming the stream from `clippa fetch`, `--fixup` and `transcribe`, not a traceback (#449).
- 11-26: `audio.wav` now says which file it was decoded from (#448): `audio.wav.src` beside it
  holds the source's size and mtime, and a wav is reused only when they still match (the mtime within
  a share's 2 s), so a source put back from a backup or moved in by hand, whatever its date, gets its
  own audio (a stream folder copied without its times kept decodes once again). A wav decoded before this keeps the old rule (reused unless older than its source), except
  in an archive item, where it is decoded again. `clippa fetch <url> --fixup` on a source already in
  order now drops an `audio.wav` that runs past the container, so the next transcribe isn't refused
  until the wav is deleted by hand.
- 11-24: a stream longer than `[schedule] budget_hours` (8 h) gets more chat windows and more
  Pass B picks, in proportion: `[signals] top_n` and `[scoring] max_picks` times hours / budget_hours,
  as the release budget already scaled. An 18 h stream had got the same 30 windows and about as many
  clips as a 6 h night (#430). Pass B is told a long stream's length; shorter streams are unchanged.
  `[scoring] scale_with_length = false` turns it off.
- 11-24: Claude on the NAS shares the laptop's Hindsight memory bank (#435): the container runs
  the same plugin, and `deploy/claude/deploy.sh` copies the laptop's server, key and bank into
  `env.local` where they're empty. The nightly checkup DMs when Hindsight is down or refuses the
  key. Wiki: Claude-on-the-NAS (Memory).
- 11-25: the hub, claude, agent and recorder deploys (`deploy/*/deploy.sh`) refuse to deploy from
  a checkout behind `origin/main` (they fetch it first), saying the branch, commit and how far behind;
  `--force` deploys anyway (the recorder's is `--any-branch`, since its `--force` already means
  "while a show is live"), and offline they only warn. `--env PATH` deploys another checkout's
  `env.local`, so a worktree of main can deploy. A claude deploy from an old branch had shipped an
  image without nodejs and every Hindsight hook failed with `node: not found`. Wiki:
  Claude-on-the-NAS (Deploy), NAS-recorder, Bugs-to-remember.
- 11-25: a long compilation's Kitsu preview now fits `[review] max_upload_mb`: it drops to 360p or
  240p (15 fps) instead of stopping at 150 kbps, and encodes once more when the encoder overshoots.
  One that still won't fit is skipped with a line in the log, so the rest of the stream still
  publishes; one already in Kitsu goes back to waiting for approval with a comment that the
  video there is the previous cut. A 5.2 h GTA 6 compilation's 762 MB preview ended the review stage halfway.
- 11-25: a Kitsu upload that fails no longer leaves an empty revision on the item (its comment is
  removed again), and a preview Windows can't read off the NAS share in one go (`OSError: [Errno
  22] Invalid argument`, a review run from the laptop) is uploaded from a local copy instead.
- 11-25: `clippa transcribe` refuses a source whose timestamps jump backwards (#440): when
  `audio.wav` runs more than 1 s past the container it names the stream and both lengths, and
  prints `clippa fetch <url> --fixup` for a Kick VOD. `--fixup` remuxes an existing or just
  downloaded Kick source with fresh timestamps, checks the result no longer steps back before it
  replaces the source, and keeps the jump as `timeline_overlap_s`; a source already in order is
  left byte for byte as it was, and a source it rewrites has its stale `audio.wav` dropped so the
  next transcribe decodes the repaired file (as it does any `audio.wav` older than its source by
  more than the 2 s a FAT/SMB share rounds timestamps by; a source stamped in the future is no
  exemption). A decode that outlives a replacement of its source installs nothing (the source's own
  size and time are read either side of it; a repair can leave the two lengths agreeing, so the
  timeline check can't see the difference), and a download marks any `audio.wav` beside it stale,
  since yt-dlp stamps the VOD's `Last-Modified` and the mtime rule would otherwise serve the
  previous file's audio. With `--no-video` it repairs the `source.ts` already
  there; a source that is still downloading (yt-dlp's `source.ts.ytdl` beside it) is refused in one
  line by `--fixup --no-video`, and by a repair with no metadata, since those can't download: a
  plain fetch resumes from the note, and a `--fixup` that runs the video resumes first and then
  repairs the whole file. A source whose audio is *shorter* than its container only warns. `clippa archive transcribe`
  checks while the item's source is still on disk and keeps a refused download as
  `source.refused.ts` (or, where the share refuses that rename, with a `.refused` marker beside it),
  so the next run reuses it rather than downloading it again or dropping it; the marker is written
  only once the rename has failed, and counts only while `source.ts` is there (one left with no
  source is dropped, and the item downloads again), and where neither can be written the line says
  the download is still `source.ts` and a later run may replace it (as does a drop that fails after
  the download, which marks the finished one so the next run reuses it); a re-refused item names
  the copy it is kept under;
  a link or junction at `audio.wav`, `source.refused.ts` or `.refused` is removed (the link alone,
  never its target) so it can't block a run, and a `source.mp4` or `source.refused.ts` that is a
  link or empty is not a copy kept here: dropped before the audio is read, after any download, so
  one planted while yt-dlp runs can't stick the item.
  With `--fixup` and Kick's api down, a stream already on disk is repaired from its folder, and a
  chat that can't be fetched is said in one line rather than stopping the repair.

- 11-24: a Modal transcription whose FLAC would be over `[transcribe] modal_opus_over_mb` (700 MB,
  about a 10 h stream) uploads opus instead. An 18 h stream's 1.3 GB upload ran the NAS out of
  memory (#422). A stage stopped by a signal now says so in run.json and the worker log
  (`transcribe killed by SIGTERM (-15)`), not a bare `exited -15`.
- 11-24: `clippa doctor --role agent` no longer crashes on Windows when OpenCode came from npm
  (`opencode.cmd`): it runs the CLI by the path it found. A CLI that won't start, doesn't answer
  in 30 s, or fails its `--version` is a FAIL line that says which.
- 11-24: `deploy/agent` is a second NAS container (`clippa-agent`) for headless checkup and
  audit through OpenCode and OpenRouter. It stays dark (`CHECKUP_AT` empty) so it does not
  overlap `clippa-claude`. `[agent]` picks the CLI and the models; `clippa agent-config`
  prints the translated model. `clippa doctor --role agent` checks that container
  (`--role claude` is the same check). With no stage on a bare `claude-` model, the
  Anthropic key is optional, and the API usage panel says Claude is not configured when
  the admin key is unset. Not deployed. `deploy/claude` is unchanged.
- 11-24: the war room's Money page has an OpenRouter budget panel (the API usage panel stays):
  the clippa key's cap, a 75% warning you can change, and Spend / Money reacting when the cap
  is close or spent. Top up is a link to OpenRouter. Editing the limit or disabling the key
  confirms first and needs `OPENROUTER_MANAGEMENT_KEY` (or `secrets/openrouter_management_secret.txt`);
  with only the inference key the panel is read-only. A 402 with Retry-After waits and retries;
  a spent key limit stops the call. Wiki: War room (Modal, costs and analytics), Costs
  (The OpenRouter budget).
- 11-23: fixes to the last few days' OpenRouter, Discord and review-check changes, before they
  deploy. An in-flight 402 waits up to two minutes per try and, when it runs out of tries or has
  no Retry-After, fails only that call instead of stopping the run. A credits stop has its own
  banner. The budget panel keeps a stop for ten minutes from the latest refusal despite
  five-minute-old figures, shows one countdown per waiting call and whether it went through,
  lights Stopped for a disabled key, and matches the clippa key by its exact label only. A
  spending limit mid-run keeps what was paid for: the covers that finished (`thumbs`, which
  starts no more), the facts read and the leads checked (`investigate`, which marks a cut-short
  check unchecked instead of "nothing found"), and the review checks done. Without OpenCV or
  past a limit, `review check` saves the items it couldn't check with a finding, and a publish
  goes on with that finding in Kitsu. Discord:
  the thread's first message says "segments next" after the fast lane and is edited as cards
  come in, the ready DM no longer says "finished processing" early, an item waiting for a person
  isn't shown as approving itself, a second Re-split press adds no second Kitsu comment, held
  error DMs keep their time, and the war room link is never cut off. `mkdocs serve` no longer
  rebuilds in a loop. Wiki: Costs (The OpenRouter budget), Model providers, Review, Reviewing.
- 11-23: an OpenRouter key limit or an empty balance stops the stage at once
  (`openrouter_key_limit` on HTTP 402 or 403, `openrouter_credits` on 402) instead of being
  sent again; an in-flight budget (`openrouter_in_flight_budget`) waits for `Retry-After` and
  tries again. A stop during `segments` writes no partial `segments.json` (Model providers,
  Pipeline).
- 11-23: `docs/openrouter-claude.toml` is an opt-in overlay that sends every Claude stage
  model through OpenRouter at the same tier and effort. Paste it into `clipper.toml`; it
  needs `OPENROUTER_API_KEY`. The defaults stay on Claude. A `--dry-run` prices an
  `openrouter:` model from OpenRouter's public list when `[llm.prices]` has no row (one
  fetch per process; a failed fetch counts as $0 and is not tried again).
- 11-23: `clippa review check` stops when OpenCV isn't installed, instead of saving a bad-cover
  finding for every item (a later run then skipped them). Items that don't need it are still
  checked. `uv sync --extra faces` installs it.

## 0.4.0 (2026-11-23)

The rename reaches the NAS and the repo, the docs get a site of their own, and blind items learn
their answers. The containers run as `clippa-hub`, `clippa-record` and `clippa-claude`, and the
repo is `byvfx/clippa` (the old links redirect). The `clipper` command, `clipper.toml` and the
`CLIPPER_*` variables still work until the rename's last pass (#286).

- **The rename, passes 3-4:** compose projects and Modal apps named `clippa-*` (the first deploy
  stops the old project, so there are never two bots or workers at once), `CLIPPA_*` in `deploy/`,
  and the repo moved to `byvfx/clippa`.
- **Docs site:** the wiki pages also build as an MkDocs Material site at
  <https://byvfx.github.io/clippa-docs/>, checked in CI; the GitHub wiki points at it.
- **Blind items:** reveals are matched to the blinds they answer and your guesses scored
  (`clippa blinds reveals`); Discord and the war room show who it was and how you did; the
  blinds store is in `clippa backup`.
- **Review:** Discord cards in the Night Watch look (a status icon and word in the title,
  Findings, a live grace countdown, a Re-split button), and the war room's show chips say which
  shows approve their clips by themselves (AUTO or manual, with the score floors on hover).
- **Running it:** the checkup stops counting the bot's Discord reconnects as a repeated error,
  the bot shows as down when it can't get back on Discord, and a too-long segment is flagged for
  a re-split instead of dropped (`[segments] split_over_s`, off by default).

### 2026-11

- 11-23: Discord review cards use the Night Watch palette and voice. The title leads with a status
  icon and a word (approve green matches the logo, so it is never color alone). The Checks field
  is now Findings, the grace countdown is a live Discord timestamp, and a segment over
  `[segments] split_over_s` shows as Re-split with a button on its own row. Stream-ready, Modal
  spend and error DMs are embeds. The bot sets a status line. Its name, avatar and about line
  are unchanged (those are set in the Discord Developer Portal).
- 11-23: Dependencies: anthropic 2.10, numpy 2.5.5, opencv-python-headless 5.1 and the latest
  yt-dlp (#345).
- 11-23: The conventions file for contributors and coding agents is `AGENTS.md`; `CLAUDE.md`
  imports it, so either name finds the same rules.
- 11-23: Blind items: when a blind you answered is revealed, Discord replies with who the source says it
  is and how your guess did, and the war room's Blinds window shows it on the card, with the scorecard in its header.
- 11-23: Wiki voice. The pages no longer say we, us or our. A reviewer's note is a retake;
  re-split stays the flag for a segment that needs splitting again.
- 11-23: The GitHub wiki points at the docs site. Home has a note under the banner, and the
  sidebar and footer link to <https://byvfx.github.io/clippa-docs/>. That note is left out of
  the docs site.
- 11-23: The docs site is an MkDocs Material build of the wiki pages (`mkdocs build --strict` in CI).
  It is published from `byvfx/clippa-docs` at <https://byvfx.github.io/clippa-docs/>. The case study
  stays at <https://byvfx.github.io/clippa/>. The wiki workflow is unchanged.
- 11-22: The blind items' store (your answers, the reveals) goes into `clippa backup` as a checked snapshot, and no
  blind is forgotten any more by default (`[blinds] keep_days = 0`); a number of days still forgets old untouched ones.
  A backup whose blinds store is damaged or was busy keeps every older zip and says so.
- 11-22: Blind items: each check matches reveal posts to the blinds they answer, in code, and scores your guess
  against the answer; `clippa blinds reveals` prints the scorecard.
- 11-22: The war room's show chips say whether each show approves its clips by itself: **AUTO**
  when `[review] auto_approve` is on for it, *manual* when not; hover the chip for the kinds and score floors.
- 11-22: The checkup no longer reports the bot's Discord reconnects after a short network drop as
  "the same error 6 times": discord.py's own "Attempting a reconnect" lines are left out of the logs
  check (a bot that stays down still shows in the services check). A card refresh that finds Kitsu
  down says so once and tries again on the next pass, instead of a warning for every stream. The
  bot's heartbeat now pauses while it is off Discord, so one that can't reconnect shows as down in
  Health and the checkup after 3 minutes.
- 11-22: The repo is `byvfx/clippa` (#286 pass 4). GitHub redirects the old `byvfx/clipper` links,
  clones and wiki pages, so nothing breaks; message links, the docs and the image-search user agents
  name the new one. Point an existing clone at it with `git remote set-url origin
  https://github.com/byvfx/clippa.git`.
- 11-22: A segment longer than `[segments] split_over_s` (0, off, so a config that doesn't set
  it is unchanged) is flagged for a re-split instead of being dropped for length. A time Clippa
  can't read, such as `1:xx`, is a one-line error, the same as any other bad time.
- 11-22: `[segments] split_over_s` below 0 is off, the same as 0. A value that is not a number,
  such as `fifty`, is a one-line error naming the setting.
- 11-22: The rename's deploy step (#286 pass 3): the NAS compose projects are `clippa-hub`, `clippa-record`
  and `clippa-claude`, and the Modal apps `clippa-whisperx` and `clippa-lipsync`. The first deploy
  of each stops the old `clipper-*` project before the new one starts (never two bots or workers
  at once); tailscale keeps its sign-in. Transcription uses the old Modal app until `clippa
  transcribe --deploy` makes the new one, and `clippa doctor` says so. The `deploy/` scripts read
  `CLIPPA_DATA`, `CLIPPA_REPO` and `CLIPPA_TOML`, and an older `env.local`'s `CLIPPER_*` the same;
  the alert webhook is `CLIPPA_ALERT_WEBHOOK` (`[record] alert_webhook_env`), the old name still read.
  Set `[serve] hub = "clippa-hub"` after the hub deploy (`clippa services` copes until then).
  A NAS cut (`[cut] backend = "nas"`) reads the config it sends, as before, and keeps running as
  `clipper-cut-<id>` until the last pass: that name is the lock old and new code share. A second cut
  of a stream that is still being cut there says to wait for it.

## 0.3.0 (2026-11-21)

The app starts answering to its new name, Clippa, and looks after more of itself. The `clippa`
command, package and texts are in; the `clipper` command, `clipper.toml` and the `CLIPPER_*`
variables still work, and the containers keep their names (`clipper-hub`), until the rename's
deploy step (#286). After pulling, reinstall the project in your venv once (the "package folder is `clippa/`" note below says how).

- **The rename, passes 1-2:** a `clippa` command beside `clipper`, `clippa.toml` and `CLIPPA_*`
  accepted, the package is `clippa/`, and messages, `--help`, the war room, the bot and the wiki say
  Clippa. Services log as `clippa.serve`, `clippa.bot` and so on.
- **Self-maintenance:** `clippa checkup --fix` retries a run that failed on a passing error and
  clears leftover temp files; new `tools`, `signins`, `logs` and `approvals` checks; a sign-in that
  is gone is DMed within the hour (#287).
- **War room:** the Schedule works on a phone (an agenda, tap to pin), Ctrl+K searches pages,
  actions and items, each Review card says why the item was picked, and the live-show chips stop
  spilling over the top bar.
- **Running it:** a disk-aware prune (`[streams] keep_free_pct`), analytics that ride out Google's
  passing errors, serve retrying a failed job in 30 minutes, and eight old bugs fixed (#366).

### 2026-11

- Messages, `--help`, the war room, the Discord bot's texts and the wiki say `clippa <command>` and
  Clippa now (#286, pass 2c-2); `clippa --version` prints `clippa 0.3.0`. The `clipper` command still
  works: use it where `clippa` isn't found yet (rerun the bootstrap to add it, or `uv sync` an older
  venv). Tab completion answers to both once you make its script again (`clippa completion bash` or
  `powershell`, wherever your profile loads it). File, setting and container names (`clipper.toml`,
  `CLIPPER_*`, `clipper-hub`, the `firefox:clipper` profile) are unchanged until the rename's deploy step.
- The services log as `clippa.serve`, `clippa.bot`, `clippa.worker`, `clippa.record` and so on
  (#286, pass 2c): grep for `clipp` to catch lines from before and after. The war room's last-upload
  box and checkup's repeated-error count read both. The bootstrap now adds a `clippa` command to
  `~/.local/bin` beside `clipper` (run it again to get one), and doctor's `command` line checks both.
- Eight old bugs Grok found while reviewing the rename (#366): a video YouTube lists again after a
  spell as private counts in Pass B's feedback and the monthly report again; the report's weakest
  list no longer repeats videos from its best list (9-12 videos); `clipper archive add` skips an
  unavailable video in a channel listing instead of crashing; `clipper archive transcribe` no longer
  pays to transcribe a story item that is one of our own streams; a paragraph break added or
  removed in the Discord Retitle form's description now counts as an edit; a daily digest the bot
  couldn't queue (a share blip) is tried again the next minute instead of skipped until tomorrow;
  an archive YouTube retry keeps a download an earlier run finished (the file its info names) and
  clears every leftover once it is transcribed; and `archive status` / `archive links` count a
  dead link's transcript where a story item keeps it.
- The package folder is `clippa/` now (#286, pass 2b), and the project is named `clippa`. Imports,
  `python -m clippa.cli` and the deploy files follow; the `clipper` and `clippa` commands both still
  work, as do `clipper.toml` and the log labels (`clipper.serve: ...`). **After pulling this, reinstall
  the project in your venv once**, or the `clipper` command fails to import:
  `uv pip uninstall clipper`, then `uv pip install --no-deps -e .` (or a `uv sync` with your extras).
- New Discord review buttons are sent as `clippa:...` and new Kitsu bot notes start with `[clippa]`
  (#286, pass 2a). Cards and notes from before still work: the bot answers both button prefixes and
  sync still tells both kinds of note from a reviewer's comment.
- The Clippa names work beside the old ones (#286, the first step of the rename): a `clippa`
  command in the venv, `clippa.toml` and `clippa.local.toml`, and `CLIPPA_*` for every `CLIPPER_*`
  variable clipper reads (the new one wins when both are set; the deploy scripts under `deploy/`
  still read only the old names). Nothing is renamed yet and the old names keep working;
  `clipper doctor` lists the old-spelling variables still set.
- War room: the live-show chips no longer wrap inside the top bar and spill over it on a laptop-wide
  window (four shows need about 1,500 px). They move to a row of their own when they don't fit, so
  the buttons stop jumping when a show goes live or the Health age ticks over.
- A sign-in that is gone (a cookies file with no live sign-in, an expired TikTok or X sign-in) is
  DMed within the hour, once, not only in the morning checkup: serve's hourly checkup reads the
  `signins` check too (`clipper checkup --alert`). `clipper status` lists sign-in findings under
  "needs you".
- `clipper checkup` has four more checks: `tools` (yt-dlp over `ytdlp_days` old), `signins` (a
  cookies file's sign-in gone or expiring, the YouTube export stale, a TikTok or X sign-in
  expiring), `logs` (the same error repeating in serve's or the bot's log) and `approvals` (an
  approved item the plan never saw, or a plan serve stopped replanning). The nightly checkup may
  now open one PR for a clear code fix, through `checkup-pr`; it never merges or deploys (#287).
- `clipper setup` no longer lets you skip the Kitsu address and token on a worker, serve or bot
  machine (only a control station may): `clipper doctor` failed without them, so a finished setup
  looked broken (#78).
- `[streams] keep_free_pct` makes the daily source-and-audio prune disk-aware: with it set (say 15), streams older
  than `keep_source_days` go oldest first only while the share has less than that percent free, so recordings
  stay as long as there's room. The checkup warns when free space is under it. `clipper streams prune
  --while-free-below PCT` does the same by hand. Off at 0, as before.
- `clipper checkup --fix` (serve runs it hourly) repairs what it can without asking: a run that
  failed on a rate limit or a dropped connection gets one more attempt, and temp files a killed
  write left are deleted. Each repair stays would-fix (reported, nothing changed) until you list it in
  `[checkup] fix`, and stops after two tries at the same problem. Every checkup now says what was fixed
  and what needs you.
- War room: each Review card says in one line why the item was picked (the open item shows it in full
  under Why), beside its score.
- `clipper analytics` rides out Google's passing errors: each call retries a 500 or a rate limit
  itself, a video whose 7-day numbers still fail is skipped and asked again next run, and what came in
  is saved either way. Only the same video failing three runs in a row fails the job (and DMs you).
- serve tries a failed analytics or streams prune again in 30 minutes instead of a day later, then
  less often (1 h, 2 h, 4 h, ...) while it keeps failing; `clipper services status` shows the streak.
- War room on a phone: the Schedule is an agenda (what is still to come by day, the earlier ones folded
  into one row, no empty days), and you pin by tapping an item in Ready to slot and then an open slot
  or a day; that works on a desktop too. The queue's Filters open as a sheet from the bottom.
- **War room:** Ctrl+K searches pages, actions and items, and G then a letter jumps to a page.
- **War room:** the Schedule page gets Next up (the next five uploads), a Week view of blocks in each
  show's colour with open slots to drop onto, and a Ready to slot tray that counts what the Schedule
  tab counts; stale and over-budget items are one link to the queue. The day list stays, as List.
- **War room:** the Schedule's Timeline view: a lane per show over nine days, the recordings with
  the Kick-first wait after each, the uploads against the 24 h cap, and the open slots.

## 0.2.0 (2026-11-18)

The first release, as the app stood on 2026-11-18. Clipper takes long streams (Kick, and now Twitch
and YouTube VODs) through transcription, scoring, cutting, titles and covers to a review in Kitsu,
Discord and the war room, then schedules and uploads what you approve to YouTube.

- **Review:** pre-flight checks and approval by silence; a redesigned war room (fixed pages, a Review
  page that docks the open item, a new look); the Clippa messenger (`/chat`), which acts on several items at once;
  titles that rename videos already on YouTube.
- **Fetching:** `clipper fetch` takes Twitch and YouTube VODs, with their chat replay.
- **Scheduling:** segments ranked by a model score, and a stream's heat graph in the war room.
- **Research and extras:** blind items (`clipper blinds`), documentaries (`clipper doc`, with your own
  voice takes), b-roll and clip art libraries.
- **Running it:** a rejected API key holds the stream and sends one DM; `clipper status` and `streams
  list|prune`, `clipper backup`, command-line polish; and versions: `clipper --version` and the
  heartbeats say which commit runs, and a release can be checked out on a new machine.

### 2026-11

- 11-18: versions. `clipper --version` and `clipper doctor` say which copy this is, `0.2.0` on a
  release and `0.2.0+3.g570bb87` past it (`.dirty` with uncommitted changes), and serve, the bot and
  the recorder put it in their heartbeats, so `clipper services status` shows what each service
  runs next to this checkout's. The hub and recorder deploy scripts stamp the image with the
  checkout's `git describe` (`deploy/stamp.sh`, `clipper/buildinfo.py`). Pushing a `v*` tag
  makes a GitHub Release with the wheel, the sdist and the section's lead as the notes
  (`.github/workflows/release.yml`); the wiki's Install page has the steps to check out a release
  on a new machine, and Developing#changelog says when a change gets a bullet here.
- 11-18: war room redesign, step 4, the look (#318): a quiet editor palette as the default look
  (`themes/warroom.css`; Inter and JetBrains Mono bundled in `clipper/web/fonts/`), one line-icon set in
  place of every emoji and symbol that stood in for an icon, skeleton cards while a panel loads, and
  an accent focus ring. `warroom` and `millennium` stay as the two looks; `retro` and `pager` are
  retired (a browser that had picked one goes back to the default), and the chat page's default is
  `millennium`. Wiki War-room and Clippa-messenger.
- 11-18: war room redesign, steps 2 and 3 (#314, #316): fixed pages under a page bar and four top-bar
  widgets replace the movable layout (the layout editor and `/api/layouts` are gone). The Review page
  docks the open item beside the grid (J and K open and step through items, Esc closes, the edge drags
  and is remembered); show, kind, status, order and dates sit behind one Filters button with chips for
  what is on; cards carry the show as a coloured tag; Retakes is a tab of the queue; an empty Waiting
  tab says "Nothing waiting" with the next upload; Undo moved under ⋯.
- 11-17: Clippa chat (#299): "approve all comps" acts on several items at once (the `bulk` tool: one
  card, one undo entry), `undo` takes the last chat change back, streams can be named "kino 11-14", "the
  kino one" or "from last night", "what's waiting" lists the items, and the one fresh write card takes
  a typed yes or no. Chat prompt v3.
- 11-16: the Clippa messenger (#285, #290): `/chat` on serve's page, a retro instant messenger where you
  tell Clippa what to do. It reads (releases, items, retakes, health, costs) and writes through the same
  actions as the war room (approve, reject, retake, reopen, hold, pin, retitle, cover text and slot;
  each lands in the war room's undo list as "(chat)"). Haiku, about $0.0005 a message, ledger `chat`
  (`clipper/catalog.py`, `clipper/chat.py`, `[chat]`). Docked at the right of the war room (Chat
  button or M; a Chat tab on a phone), with four looks (`[chat] theme`, `web/themes/`) that dress the
  whole war room.
- 11-16: title edits rename videos that are already on YouTube (#291): a new title for an uploaded item
  renames the video (`youtube.retitle`, the show name and bleeps applied as at upload) from the war
  room, the bot, Clippa and the CLI; before, only Kitsu's title changed.
- 11-16: `clipper fetch` takes Twitch (`twitch.tv/videos/<id>`) and YouTube (`watch?v=`, `youtu.be`,
  `/live/`) URLs as well as Kick, with the chat replay, leaving the same stream files (#293). Ids are
  `twitch-<id>` and `youtube-<id>`; the channel must be in a show's `channels`; a subscriber-only
  Twitch VOD reads `secrets/twitch_cookies.txt` (`[paths] twitch_cookies`, which doctor checks). Wiki
  Fetching-VODs.
- 11-16: a rejected API key (a provider's 401) no longer looks like an outage (#281): the worker holds
  the stream and sends one DM for each set of keys, instead of retrying three times an hour apart, and
  takes the stream again as soon as the keys change; `clipper checkup` lists held streams; deploys
  check the keys for drift (`deploy/keys.sh`); the war room has a Keys window.
- 11-16: the war room draws a stream's heat: **🔥 heat** on a stream or in an item's window opens
  the curve from `clipper signals`, the windows picked from it, and each cut segment, Short and clip
  where it sits in the stream, coloured by its review state, with the twelve hottest windows and
  what was cut from each (`/api/heat`, `clipper/warroom/heat.py`).
- 11-16: `clipper doc voice` takes your own recording of a take: a file named for the take in the
  piece's `vo/read/` (`open.wav`, also .flac, .mp3, .m4a ...) is used instead of a bought read and
  costs nothing, with the same compressor, pause cap and word timing; a piece can mix the two.
  `--dry-run` lists which takes are yours and any file that matches no take, and a read that
  strays from its script is reported on every run. When the last build bought no take, the kit's
  disclosure lines are `[doc] own_voice_disclosures` (none) in place of `[doc] disclosures` (the
  AI-voice line), which is unchanged.
- 11-15: blind items (#277, #278, #280): `clipper blinds` collects gossip blind items from the feeds
  you list (`[[blinds.sources]]`: blogger, wordpress or rss; none by default), a Haiku pass reads each
  one's clues without naming anyone, twins across sources are paired, and you give your guess first
  (a name from `clipper people`, no idea, or not ours). The war room has a Blinds window; serve checks
  the sources on a timer (`[blinds] check_minutes`, off by default) and the bot asks in a private
  `#blinds` channel, as a daily digest or an alert for each, never with a guess of clipper's. Wiki
  Blind-items.
- 11-15: approval by silence is firmer (#270, #273): items held only by stale findings are checked
  again hourly, the approval's Kitsu calls run under a 25 s deadline (`review.kitsu_deadline`), and the
  Kitsu Checks column is kept current without counting as a person's edit.
- 11-14: review pre-flight checks and approval by silence (#259, #263, #265): `clipper review publish`
  looks at an item before it reaches you (`clipper review check`; `[review] checks`): the text against
  `[checkup] words`, YouTube's limits and the bleep list, a Short's webcam panels through the face
  detector (the panels marked `cam = true` in the show's layouts), and the cover's face against the
  person it should show (one vision call, about a cent). A show can let its clean items approve
  themselves, `[shows.<key>.review] auto_approve = true` (off by default): serve's `auto` job, every 10
  minutes (`clipper review auto`, `clipper/review_auto.py`), approves an item nobody has acted on whose
  checks found nothing. On for Kino in this install's config. Wiki Review#pre-flight-checks and
  Review#approval-by-silence.
- 11-14: the schedule's budget ranks a stream's segments by their model score, chat heat breaking
  ties, where a score covered the stream (else heat alone, as before); a compilation takes its best
  segment's score; Kitsu's Score column shows a segment's score (#258 C). Turned on for Kino and
  Channel 404 (`[segments] score = true` in this install's config).
- 11-14: a model score for topic segments (#258 B, `clipper/segscore.py`, prompt
  `segment_score_v1`): one call per stream scores each cut segment 1-10 against the show's typical
  upload, from the segment and the show's per-topic track record. `clipper segments <stream> --score`,
  `[segments] score` (off) after each split, and `clipper segments --backtest [--transcript]` over
  past streams as of their start (segment_scores_backtest.json), which `clipper analytics --lift`
  reads as the bt and bt_tx rankers.
- 11-14: `clipper analytics --lift [--show] [--kind] [--json]` checks what clipper ranks uploads by
  against their 7-day views (#258 A, no model): the Score column, the segments' chat heat and the
  topic's earlier uploads, as Spearman's correlation and each stream's top half against its bottom
  half. `clipper analytics --report` ends with the same table. `lift.topic_record` is the per-topic
  track record a segment score prompt will read (#258 B).
- 11-14: `clipper clipart find|add|list|keep|drop`, a clip art library beside the b-roll one:
  Wikimedia Commons drawings and pictures, The Met's and the Cleveland Museum of Art's CC0 works,
  emoji and icons through Iconify (Fluent Emoji, Noto, Tabler, Phosphor; logo sets never), and
  Pixabay vectors with a free key. Public domain, CC0, permissive and Pixabay's licence by default;
  each file keeps its source, credit and licence. `add` takes a folder too (a Kenney pack). API keys
  for DVIDS, Brave and Pixabay are read by one `config.key`.
- 11-13: command-line polish (#205): a stream argument takes `latest`, `latest:<show>` or a unique start
  of an id, and a mistyped one names the nearest; `--json` on `review list`, `schedule --list` and
  `shows`; `clipper config diff` lists what this install changes from the defaults, by layer; `clipper
  completion bash|powershell` prints a tab-completion script; `run --only` suggests the nearest stage;
  `clipper run` logs "stage N of M".
- 11-13: the `archive transcribe` and `investigate facts` dry runs count what every call costs
  beyond its content (a minute of GPU a transcribe call, from the subject's own Modal runs when it has
  20; the prompt, schema and header a facts call repeats). Items with no length yet are said, not
  priced. Back-tested on the 759-clip run: $10.87 vs $10.88 logged (was $0.01), facts $0.27 vs $0.26
  (was $0.11) (#228).
- 11-13: `clipper backup` zips the taste layer (config, hand-edit backups, schedule, people, faces,
  voices, layouts, cast photos, each stream's config and review state; secrets only when asked) into
  `[backup] folder`, and serve makes one every `[backup] every_days` (7). `clipper streams list` shows
  what each stream takes; `clipper streams prune` removes the source and audio of streams done with
  (`--cuts` also the long cuts of uploaded or rejected items), and serve does it daily with
  `[streams] keep_source_days`. `[audit] backup_folder` and `backup_max_days` moved to `[backup]`
  (#204; wiki Backups).
- 11-13: `clipper status` opens with what needs you (items waiting or held, checkup's findings on
  services, runs and late releases), shows each stream's show and date, leaves out week-old streams
  with nothing waiting (`--all`), and has `--json`. It counts reviews from review.json instead of
  every item's sidecar: 15 s from the laptop, was 60 s (#199).
- 11-13: the `review` workflow (Grok on every commit Copilot hadn't reviewed) is gone: too slow at ~25 min
  a PR. Code PRs now get a local Claude Code review before the first push and Copilot's when the PR opens;
  `clipper codereview` stays for a by-hand second opinion (wiki Developing).
- 11-10: `clipper audit`: a weekly digest of how the week went (no model), saved each week, and a monthly
  read-only deep audit that traces what keeps coming back (wiki Audit). Dependabot opens grouped weekly
  update PRs.
- 11-10: the `review` workflow switches between reviewers by itself: a commit Copilot reviewed gets no
  Grok review, and Copilot's out-of-credits notice, or no request or review from it, runs Grok
  (`[codereview] copilot_wait_s`; 0 = always Grok).
- 11-10: war room fixes from the usability review (#201, #202, #206): bulk actions only touch what is on
  screen and the bar counts the picks left out; a double tap can't send an action twice; bulk reject; the
  header says how old the data is; a hidden tab stops polling; `?key=` leaves the address bar once the
  cookie is set; the Health panel's mis-encoded dot, with a test for mis-encoded text in `clipper/web/`.
- 11-10: the `check` workflow skips a push or PR that changes only notes no test reads (root `*.md`,
  `LICENSE`, `docs/log/`, `docs/archive/`, `reports/`, `studio/`). `docs/wiki/` still runs it, and so
  does `studio/research_notes/streamers.json` (a test reads it).
- 11-10: `clipper --help` and every command with actions no longer print the whole list in braces, and a
  mistyped command or `--from` stage says "did you mean ...?" (#205).
- 11-09: `clipper codereview` and the `review` workflow: every code PR gets a second review in one
  OpenRouter call (`[codereview] reviewer`, Grok 4.7 at high effort, about $1 a PR), posted on the PR
  and on the issues it links, one comment each, edited in place. Needs the repo secret
  `OPENROUTER_API_KEY`. Copilot's review quota had run out (PR #191). wiki Developing has the 13-model
  comparison behind the choice.
- 11-09: `[edit] tighten` (#70, the first piece of the edits roadmap #66; off by default): `clipper
  shorts` cuts pauses over `max_gap_s` down to `keep_gap_s` (`keep_turn_s` at a change of voice),
  only where the audio is quiet too, never under `[shorts] min_s`; captions and scene changes move
  with the cuts, which sit on whole frames so the picture and sound stay together
  (`clipper/tighten.py`). Kitsu column "Tightened", `pauses_cut` in shorts.json for
  retakes, `clipper shorts --no-tighten/--tighten` per Short. Kitsu columns no longer get an empty
  value written onto an empty column on every publish.
- 11-08: the prompt files ship inside the package: `prompts/` moved to `clipper/prompts/`, beside the
  loader (`clipper/prompts/__init__.py`, which finds them with `importlib.resources`), so a wheel and
  the hub image carry them without a copy step. `[paths] prompts` is optional now (`""`, the default,
  means none): a folder of your own, read before the packaged files; doctor warns when it names a
  folder that isn't there. The Dockerfiles and `deploy/hub/deploy.sh` no longer copy `prompts/`.
- 11-08: a heartbeat read that lands while its writer replaces the file no longer counts as "no
  heartbeat": `daemon.read_heartbeat` reads a missing or unreadable heartbeat again (three reads,
  0.3 s apart) when its folder exists. `clipper record status` (exit 3 while a show is recorded,
  which `deploy/record/deploy.sh` trusts), `clipper status`, doctor and the war room read through it.
  A heartbeat that is there but still unreadable after that is unknown, not absent: Health and the
  status lines say "unreadable" with the reason, `record status` exits 1, and the recorder's stop,
  restart and deploy refuse without `--force`.
- 11-08: `clipper/defaults.toml` is the one source of the defaults, every setting with a comment
  (`config.py` loads it; a None default is a commented line there plus `config.NONE_DEFAULTS`);
  config.py is 900 lines shorter, and `clipper.example.toml` is a starter clipper.toml with the
  few values a new install changes and a show table.
- 11-08: pages for what `clipper setup` can't do: Kitsu (with `deploy/kitsu/local.sh` for one
  machine without ssh, and the bot and reviewer accounts), the Discord app and its ids, YouTube
  sign-in, and Reviewing for the second person; `clipper review invite <discord user id>` puts a
  reviewer on the bot's allowlist. One way to run each role unattended: compose (`deploy/hub`,
  any Docker host) for serve, the bot and the worker, the Windows task for a GPU worker only; the
  serve and bot task scripts are gone. The product pages no longer name this install's test
  stream, NAS address or channel.
- 11-08: `clipper status`: one screen with the services' and the recorder's heartbeats, the
  worker's streams, review counts, the next releases and the month's spend. `score`, `segments`,
  `meta` and `thumbs` print an estimate with `--dry-run` (the prompts they would send, four
  characters a token, times `[llm] prices`) and call nothing; Costs has the measured per-stream
  figures.
- 11-08: every flag and positional has help (`clipper <command> -h`; a test keeps it so), `run -h`
  explains the fast lane's twin stages, the wiki stops calling `--limit` and `--from` universal,
  and `clipper doctor` warns on `[youtube] auto_upload_minutes = 0` (serve) and on a bot machine
  with `[discord] enabled = false`.
- 11-08: the defaults and `clipper.example.toml` describe the product, not this install:
  `[receipts]` and `[weekly] masthead`/`byline` are empty until set (`clipper receipts` and
  `clipper weekly` refuse with the fix; `clipper doctor` warns), the example's show is
  `[shows.myshow]`, and a test keeps this install's names out of `config.py`, the example and
  `clipper setup`.
- 11-08: `studio/` holds the mockups, the research notes and the media-heavy reports; `[paths]
  streamers` now defaults to `studio/research_notes/streamers.json` (set it if you keep that
  file elsewhere; it is read with a default when absent).
- 11-08: hand-over audit (`reports/code-audit-2026-11-08.md`) and its tier 0: the test suite green
  on Windows and run by CI on Linux and Windows; ruff rules widened (import order, bugbear,
  pyupgrade, blind excepts, stale noqa, McCabe over 15); `clipper --version`; stale docs retired;
  tests filed by subject; this file.
- 11-07: a Mac as a `control` role (`clipper setup`); Linux bootstrap installs the fonts; Shorts
  panels can focus on a face.
- 11-05: serve, the bot and the worker as containers on the NAS (`deploy/hub`), with the config
  and all state on the NAS share; Claude Code in a container on the NAS; scoring prompt v7 with
  `[scoring] gambling`; narrated story videos.
- 11-04: `clipper story` plan and cut; `clipper people`; `clipper compare` and OpenRouter models;
  ChudLogic as a show; YouTube cookies from a browser profile; the wiki as the docs.
- 11-03: `clipper serve` split out of the bot; `clipper investigate` (#108) and the search swarm
  (#109); the 11-03 audit's fixes (quiet failures, cost guards, scheduler consistency).
- 11-02: cast library (`clipper cast`) and the `cast` worker stage; news watcher; weekly pages
  in Discord.
- 11-01: first X post (`clipper x`) and auto-posting; TikTok parked; faces on screen
  (`clipper faces`).

### 2026-10

- 10-31: `clipper x` (X API).
- 10-28 to 10-30: the maintainability audit: tests and CI, `ClipperError`, `jsonstate`
  everywhere, the stages table, `clipper services`, the record and thumbs packages;
  `clipper setup` phase 1; analytics; the cover editor.
- 10-25 to 10-27: `clipper receipts`; speaker labels; cut on the NAS; the cost ledger
  (`clipper costs`); the LLM layer.
- 10-23 to 10-24: war room on the phone (Tailscale); Discord DMs; source links in descriptions;
  `clipper weekly`; Modal monitoring.
- 10-20 to 10-22: automatic uploads; Shorts with scene-cut layouts; the worker's fast lane;
  TikTok drafts; hand picks (`clipper pick`).
- 10-18 to 10-19: multi-show; pre-shows; Channel 404 News; the backlog push.
