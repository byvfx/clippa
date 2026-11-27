# Clippa handoff (current state, trimmed 2026-11-20)

This file is the **current state** between sessions: what runs where, what the user decided,
what is waiting on them, open work, recipes, gotchas. Keep it short (under ~300 lines): update
the sections in place, don't append diaries here. The session-by-session diary is
**`docs/log/2026-11.md`** (one file per month, October in 2026-10.md; `grep "^## " docs/log/2026-1*.md`
lists every entry by date; grep the log, don't read it whole). At the end of a session: fix what
changed here, then add a dated entry at the end of the log.

Stream Clippa for Kino Casino (Kick channel `kinocasinogaming`), Channel 404 News and ChudLogic → YouTube
channel **Lolcow Cinema**. Repo `C:\clipper`, remote https://github.com/byvfx/clippa (public, on purpose:
the user said so 2026-11-10; every tracked file, issue and PR comment is readable by anyone).
Python package `clippa/` (`clippa/defaults.toml` is every setting, documented; prompts in `clippa/prompts/`),
config `clipper.toml` (gitignored, on the NAS; `clipper.example.toml` is a tracked starter). The user docs are
the site at https://byvfx.github.io/clippa-docs/ (MkDocs from `docs/wiki/`). The GitHub wiki mirrors those
pages (`.github/workflows/wiki.yml` on every push to main) and points readers at the site.
README.md and SETUP.md are short pointers; AGENTS.md the conventions; `reports/` the monthly analytics; `docs/archive/` the old audits.
Project memory (`~/.claude/projects/C--clipper/memory/`, MEMORY.md first) holds durable preferences.
- Plan doc: https://claude.ai/code/artifact/a9bb9e38-ced0-4455-8d8e-18a78122f45f; usage walkthrough (2026-10-27):
  https://claude.ai/code/artifact/0d0e04da-54f6-4f15-bad0-2df72a286383; interactive demo: https://claude.ai/artifact/ASL1grYiajRAELyoqTGitN
- Case study (11-11): https://byvfx.github.io/clippa/ (`clippa/index.html` in byvfx/byvfx.github.io), from the private artifact "Clippa
  Overview" (https://claude.ai/artifact/HTF3noAawBG1M1JNYsLAMS): edit that, splice `<div class="figures">` to `<footer` into the
  site page, and carry the text to the wiki's `Overview.md` too. No show names on the page; its documentary case study names no
  subject, channel or title. The figures (347 videos, 799 tests) are dated 11-13; the 11-11 log has the re-render recipe.
  Docs site (11-23, live at https://byvfx.github.io/clippa-docs/): MkDocs Material from `docs/wiki/`
  (`mkdocs.yml`), published from `byvfx/clippa-docs`. The GitHub wiki mirrors the same pages. Home has a
  note under the banner, and the sidebar and footer link to the site. A `<!-- wiki-only -->` block is
  dropped by `docs_site/convert.py`, so that note is not on the site. The case study stays at
  https://byvfx.github.io/clippa/. Do not enable Pages on `byvfx/clippa`, and do not edit
  `byvfx/byvfx.github.io`. Repo About > Website still needs setting to the docs URL (the gear next to About).
- The wiki's **Bugs-to-remember** page (11-25, #426) collects the bugs whose symptom points away from the cause
  or comes back with new hardware, a recorder or an ffmpeg: symptom, cause, how to tell, band-aid, fix. First
  entry: the 18 h stream's transcribe killed by SIGTERM during the 1.3 GB Modal upload (#422, fixed by
  `[transcribe] modal_opus_over_mb = 700` in PR #431). #427/#428 added 22 more under six groups (timing and
  sync, shared state over the NAS, services, tools, review surfaces, limits and estimates), each checked against
  the code; the page states its own safety rules (deploy/restart only on the user's word, never during a show or
  a job, no by-hand upload/publish, nothing twice on the laptop). A closed issue labelled `bug-to-remember` gets
  its entry in the fix's PR (AGENTS.md Rules); #440 (the 821.7 s VOD) is one, fixed 11-25 by the transcribe
  check and `clippa fetch --fixup` (below).
- Keys are Windows **User** env vars (ANTHROPIC_API_KEY, KITSU_TOKEN, ...); agent shells must load them:
  `export KITSU_TOKEN="$(powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('KITSU_TOKEN','User')" | tr -d '\r')"`

## Pipeline (one stream = `streams/<id>/`, test stream `kick-131151926`)
`clippa fetch <kick, twitch or youtube url>` → `signals` (chat/loudness heat) → `transcribe` (WhisperX on Modal, diarized) →
`score` (Haiku Pass A + transcript scan, Sonnet Pass B) → `segments` (Opus topic split + compilations) →
`cut` (segments lossless w/ keyframe starts, compilations joined, picks re-encoded; slurs+"fuck" bleeped) →
`meta` (SEO title/description/chapters/tags + .srt) → `thumbs` (Enquirer tabloid covers) →
`review publish` (Kitsu on NAS 192.168.0.155:8090 + Discord cards) → `review sync` / Discord bot →
`schedule` → `upload` (YouTube). Always on, as NAS containers since 11-05: `clippa serve` (the war room, hourly
replan + upload, pre-show grabs, retakes, daily jobs), the Discord bot (`clippa bot`) and the worker.

Around it (each a subcommand; details in the wiki and the log): `record`, `worker`, `shorts`, `retake`, `preshow`, `pick`, `weekly`,
`receipts`, `archive`, `investigate`, `story` (the swarm, `clippa/swarm.py`), `people`, `faces`, `cast`, `speakers`, `narrate` /
`perform` (Mother Moo), `tiktok`, `x`, `rumble`, `analytics`, `costs`, `compare` / `models`, `status`, `services`, `alert`, `doctor`.

Timeline check (#440, 11-25, ten review rounds to 11-26): `transcribe` (and `speakers --diarize`, and `archive transcribe` before it drops the
source) refuses a source whose `audio.wav` runs >1 s past its container - the source's timestamps jump back
and a transcript would be off after it. It names the stream, both lengths and, for a Kick VOD,
`clippa fetch <url> --fixup`; `--fixup` remuxes `source.ts` with fresh timestamps (`clippa/timeline.py`,
Kick only, `.ts` only) into a fresh `.timeline-*.part`, and replaces the original only when the probe, the
packet order (both streams) and a 90% size floor all pass. The repair writes `audio.wav.stale` before the
replace and drops the wav decoded from the old source (`extract_audio` honours the marker whatever else
says, then reuses a wav only when `audio.wav.src`, the size+mtime stamp each decode writes (#448), still
names the source; a wav from before the stamps falls back to the 2 s mtime rule, except in an archive item;
it refuses to install audio whose source changed under the decode); every download (`fetch`, `archive
transcribe`) marks a wav beside it stale, since yt-dlp stamps the VOD's Last-Modified. An in-order
`--fixup` drops a wav that runs past the container. So re-fetching is not needed and
a re-transcribe decodes the fixed file: `clippa fetch <url> --fixup` then `clippa transcribe <id> --force`
when it was transcribed before (the fixup now says so). `kick-132975332` was repaired that way on 11-26.
The follow-ups from #444's review are all merged and deployed (11-26): #450 (#448, the `audio.wav.src` stamp),
#451 (#449, `media.link_or_raise`: a share that won't stat is one line), #452 (#447, `-protocol_whitelist file`
on every local ffmpeg/ffprobe input, an AST-scan test; the recorder's concat is allow-listed until it is tried
on its bookworm ffmpeg 5.1), #453 (#446, `clippa archive fixup <subject> <item>`: remuxes a refused item's
kept copy in place, waits while live unless `--force`, a SPEND card `archive_fixup`), #455 (a fresh Kick/HEI
download is kept until its transcript is written and reused after a failed encode or Modal call) and #457 (a
kept `source.mp4` on a dead link is told to stay in place: no `.ts` will come, it needs a hand repair). Grok
said three times that a refused replace in `archive fixup` is a traceback; it isn't (`remux_verified` wraps
the callback's OSError), so don't chase it again.


## User decisions (keep)
- The NAS hub's YouTube cookies come from the laptop (11-09, user: "lets just setup that task but note it that it
  prevents full offline autonomous usage"): the `clipper-cookies` task runs `clippa cookies export` every 4 h.
  The NAS isn't laptop-free for signed-in yt-dlp retries: with the laptop off for days, pre-show grabs get stuck.
- The app is **Clippa** (2026-11-09, branding only): war room, Discord status card, wiki Home/sidebar/banner,
  README. **The full rename (#286) is under way, in passes:** pass 1 (PR #363, deployed 11-21) reads both
  names everywhere clipper reads one (`CLIPPA_*` first, `clippa.toml`, a `clippa` command); pass 2a
  writes `clippa:` button ids and `[clippa]` Kitsu notes (old cards and notes still answer to both);
  2b moves the package to `clippa/` (project `clippa`; a venv made before it needs `uv pip uninstall
  clipper` + `uv pip install --no-deps -e .` once; the claude container needs a redeploy for
  checkup-pr's allowed paths). 2c-1 renames the logger labels to `clippa.*` (warroom/data.py and
  checkup read both) and has the bootstrap add a `clippa` command to ~/.local/bin; 2c-2 the text:
  messages, help, the war room, the wiki and notes say `clippa <cmd>` and Clippa (not the versioned
  prompts, `deploy/`, CI, history, or any machine name: `clipper.toml`, `CLIPPER_*`, containers, the
  tailnet name, the `firefox:clipper` profile). **Pass 3** (PR #375, deployed 11-22 03:36-03:38, all
  six steps): compose projects `clippa-hub`/`clippa-record`/`clippa-claude` (the first deploy of each
  stops the old `clipper-*` project and renames its compose.yaml, `deploy/retire.sh`; tailscale keeps
  `clipper-hub_tailscale-state`), Modal apps `clippa-whisperx`/`clippa-lipsync` (lookups fall back to
  the old app until deployed), `CLIPPA_DATA`/`CLIPPA_REPO`/`CLIPPA_TOML` with the old names read.
  The old `clipper-*` folders on the NAS keep a `compose.yaml.retired-20261122-*`; `clipper-whisperx`
  is stopped on Modal, `clipper-lipsync` still deployed (stop it once `clippa-lipsync` is, by
  hand); clipper.toml's backup is `backups/clipper.toml.before_hub_rename_2026-11-22`. Kept for the last pass (user, 11-22: "get rid of all of
  clipper eventually"): the data folder `/volume1/docker/clipper` and `clipper.toml`, CLAUDE_DIR,
  the tailscale volume and `TS_HOSTNAME=clipper`, the Kitsu deploy's `env.clipper`, the Windows tasks,
  the NAS cut container name `clipper-cut-<id>` (the lock old and new code share) and the claude container's
  `CLIPPER_*` twins of its `CLIPPA_*` settings (for a session on a pre-rename branch).
  **Pass 4** (11-22): the user renamed the repo to `byvfx/clippa` (GitHub redirects the old name;
  never make a new `byvfx/clipper`, which would end the redirect); the laptop checkout and the NAS
  claude clone point at it, and the links, docs and user agents name it. Next, 5: drop the fallbacks
  once `clippa doctor` shows no old names in use.
- Titles/descriptions/cut points approved as-is; fun descriptions are liked. Long video per recurring topic
  (compilations) plus segments plus short picks. `third_party_audio = "allow_reactions"`,
  `audience_knowledge = "fans"`, Victor/blackjack segments stay; Kino's gambling is commentary, not play
  (`[scoring] gambling = "commentary"` for kino and c404; prompt v7 from 11-05, v8 on kino from 11-09 and on c404 from 11-18; v9 tried 11-20 and no better, v8 kept on both (#192 closed): a plain v8 rerun picks 10 of the 21 approved clips, so v7's and v9's 9 were rerun noise and review bias, wiki Model-trials).
- Never use the word "hosts" in viewer-facing text → `[scoring] house_style` rules, appended to every
  viewer-facing prompt; put future style notes there. The cast is **Andy (Warski)** and **PPP** (never any other
  name for PPP; old drafts that say "Ashton" stay). Channel 404 = Don, a solo show. Cast per show in `[meta] cast`.
- Kino's segment is **"WOAH Buddies"** (WOAH in capitals; user, 11-16), never the transcript's "Woe" or
  "Whoa": a `house_style` rule since 11-16. A word Whisper mishears gets a rule there the same way.
- Thumbnails: `[thumbnail] style = "enquirer"` (1990s tabloid: show masthead, no roundel, no barcode, small
  "AS SEEN ON LOLCOW CINEMA" badge), `enquirer_prompt_version = "v6"` (face = headline subject, cast photos
  picked by label, faces.json names trusted). Pre-show covers are one story, no side boxes (`[preshow]
  teasers = false`). The main face must never sit under the reaction inset or badge (`face_crop(avoid=)`).
- Bleep: `[bleep] mode = "bleep"`, `profanity = true` (all forms of "fuck"), slurs always. Captions/titles
  masked; `youtube.metadata()` masks again at upload.
- Shorts: 15-59 s hot takes; horizontal clips are cut and drafted but never scheduled (2026-10-21). A Short
  switches layout at each scene cut. Shorts over `max_source_audio_s` of other voices arrive **held** in review
  with the seconds on the card (`[shorts] hold_source_audio`, 11-04); release when the show carries it.
  Stories retry YouTube's bot check signed in, like pre-shows (`[story] youtube_bot_retry = true`, 11-03).
- Kitsu is the record for review/edits (no separate DB; Kitsu-optional is not a goal). Discord approvals must
  keep working: the war room is the review surface, cards approve from the phone.
- Uploads: automatic since 2026-10-20 (serve: `clippa schedule` then `clippa upload` hourly), private with
  publishAt, `[youtube] max_per_24h` 15 (channel allows 100; the user leaves room), shows take turns
  (`mix_shows`). `manual_publish = false`: **"asap" items (pre-shows, one-offs, stories) publish public the
  moment they upload.** Every upload joins its show's playlist; source links are added at upload and
  backfilled hourly. Stay current, no backlog (10-29): `stale_days = 3`, items let go stay approved in Kitsu
  (war room filter "Not going out"; pin with "Publish at" to send one anyway).
- X (@lolcowcinema): `clippa x` posts Shorts as native video, never a link (X bills $0.20 a link post).
  **Auto on since 2026-11-01** (`[x] auto_post_minutes = 60`, `max_per_24h` 6, weekly thread posts count),
  hook first + up to 3 hashtags ending with the show's, a last line crediting the footage's owners
  (`[shows.<key>.x] mentions`, 11-03). `clippa x --issue` posts the weekly as a thread after the user OKs
  the text. The app browser pane is signed in to X.
- TikTok: **parked (2026-11-01)**; the user posts by hand, never direct posting (#62, #116 closed). Don't send
  drafts unless asked (`clippa tiktok --items ... [--resend]`).
- Transcription on Modal (L40S, ~$0.34 per 8 h); Kino and Channel 404 diarized (~$0.30 more; Don enrolled
  11-04). Cut runs on the NAS (`[cut] backend = "nas"`). Whisper writes "Bearing" as "Baring": fix it.
- Weekly magazine (`clippa weekly`, Saturdays): cover full tabloid, inside pages modern, articles in the
  user's own voice (writer pass, `weekly_article_v1`); `clippa weekly --discord` puts pages + captions in
  Discord `#weekly`; the user posts TikTok carousel + two YouTube Posts by hand.
- Investigations (#108): leads go to Discord `#investigations`, a thread per `leads` run, one short message
  per lead edited in place, linking to the war room's Investigations window. Leave Jer's old videos alone
  (`investigate run jer` reads only new ones). Whether a lead may ever become content is open.
- Mother Moo (The Udder Truth, #58): British, voice "Harriet" (ElevenLabs), "perform to playback", WAN 3
  Prime on Runway for the picture, Sync for lip touch-ups, finish in DaVinci Resolve. Rupert is recurring and
  voiced by the user. Quality over cost. ElevenLabs reads are lossless WAV (pcm_48000).
- Second channel **Sloth Beat** (#46: Papa Meat, Ben Avery, On Cinema; teen-fan-magazine look). Runs from the
  desktop as the first clean install. Permissions open.
- Recorder alerts go to both the webhook channel and DMs (warnings only), twice on purpose (2026-10-28).
- Model calls are logged to `logs/costs.jsonl` (`clippa costs`); say what a rerun costs before doing it
  (`--dry-run` prices score/segments/meta/thumbs). The taste layer is the asset: see "Back up".
- Hand-over (2026-11-08 audits, `docs/archive/code-audit-2026-11-08.md` §7): build as a public package but this deployment first,
  little tech debt, simple for the user; `studio/` holds mockups, research notes and media reports, out of the package; serve and the
  bot are compose-only. **Merge policy and reviews are in AGENTS.md (Checks)** and wiki Developing#code-review-on-prs: a code PR
  (`clippa/`, `tests/`, `deploy/`, `.github/`, `pyproject.toml`) gets the local `code-review` skill before the first push, then
  Copilot, or Grok's `review` workflow while Copilot is out of credits (back 11-20 "for a few more days"; delete the workflow once
  Copilot reviews again; its repo secret `OPENROUTER_API_KEY` stays); merge once the review is in, each finding answered, CI green.
  Other PRs (wiki, `uv.lock`, `clipper.example.toml`, `streamers.json`) have CI but no review, notes-only ones neither; both merge on
  the user's word. A fork's PR: read the diff by hand, then the user's word.

## State now (2026-11-26)
- **Hub on the NAS** since 2026-11-05 (wiki NAS-hub): compose project `clippa-hub` in `~/clippa-hub` (since pass 3, 11-22), containers
  `serve` (war room on 127.0.0.1:8765, on the tailnet as https://clipper.taile415cd.ts.net/; heartbeat
  `streams/.serve/status.json`), `bot` (`streams/.bot/status.json`) and `worker` (cuts with QSV on the iGPU).
  Code changes go out with `bash deploy/hub/deploy.sh` from a main checkout (since 11-25 every deploy script refuses from a
  checkout behind origin/main, `--force` overrides, `--any-branch` on the recorder; `--env PATH` deploys from a worktree of main with `C:\clipper`'s env.local); `clippa services status|restart ...`
  runs docker compose there (`[serve] hub`). Last deploy 2026-11-26 21:49 PT: all three at a1e7cbb (`clippa
  0.4.0+107.ga1e7cbb`, #457 on top of #452/#453/#455), no restarts, the recorder untouched. **The config and
  all state are on the NAS:** `//192.168.0.155/docker/clipper` (`/data`) holds `clipper.toml` (the one copy;
  `CLIPPER_CONFIG` on the laptop points at it), `schedule.json`, secrets/, backups/, logs/, faces/, voices/,
  news/, weekly/, archive/, layouts/, people.json, fonts/. The laptop's old copies: `backups/laptop-state-2026-11-05/`.
  The laptop's scheduled tasks are disabled, not removed (rollback: wiki NAS-hub).
- **NAS** (192.168.0.155) also runs the recorder container (`deploy/record`, `clippa-record`, redeployed 11-22 at 6ac5d37,
  watching kinocasinogaming + donflurgundy; chudlogic off since 11-10, user: catch up on Kino and Don), Kitsu :8090, the cut container, the streams and photos
  shares. `kick-138291641-2` is Kino 11-17 after the show ended early (Andy playing a game): recorded only, held
  from the worker by `run.json` `{"status": "skip"}`; delete it whenever you like. The `claude` container (`deploy/claude`, wiki Claude-on-the-NAS) drives Clippa from the phone. **Its role is in `AGENTS.md` since #424 (11-24): investigate, test and file tickets, not fix code** — no fix branches or PRs unless asked, cheap tests with the cost stated first and the user's yes, band-aids (a stream's `config.toml`) when asked; a ticket must stand alone (evidence, cause, checklist, scope). Asked what it can do, it gives the answer written there word for word. Its `gh` token creates issues but can't comment on or edit them.
- **Mac `lofn`** (Apple Silicon, setup's `control` role, 11-07): a dev/control station that runs nothing
  unattended. Clone `~/clipper`; `CLIPPER_CONFIG=/Volumes/docker/clipper/clipper.toml` and
  `CLIPPER_LOCAL_CONFIG=~/clipper/clipper.local.toml` in `~/.zshrc` (mount the share in Finder first); ssh
  alias `nas` (user **`Brandon`**, capital B). `doctor --role control` clean; Homebrew ffmpeg has no libass.
- **Shows:** kino and c404 live through review → schedule; `chud` (ChudLogic, since 11-04: daily ~10 h from
  ~00:00-02:00 PT, budget segment 3 / short 5 per 8 h, 8-Bit Guy moments ranked up, cam panels `focus = "face"`
  since 11-07). His 10-18 VOD `kick-132975332` is fetched and transcribed only: its 821.8 s jump back was
  remuxed out on 11-26 and it was transcribed again (33833 s, 78,623 words); nothing else run on it yet. dsp and blade are config-only tests;
  `trish` is a placeholder for the 2026-10-27 one-off; trish and blade are off the war room (`[warroom] hide_shows`).
- **Schedule** (October check-in, 11-02): `stream_budget = { segment = 10, short = 10, compilation = 0 }` per
  8 h, Channel 404 `segment = 9` (6 until the 11-22 check-in; Chud stays off); slots a day: 9 Shorts (09/11/13/15/16/18/19/20/21), 6 segments
  (08/10/12/14/17/22), compilation 23:00 for big-story nights only (`docs/big-story.toml` into the stream's
  config.toml). If good items keep going stale while streams overlap, try `stale_days = 4`.
- **Chud overnight test: over (11-20).** All 71 chud segments pinned 23:00-07:00 went out on time with no `uploadLimitExceeded`
  (peak 7,203 units a day); caps back to 15 (`backups/clipper.toml.before_chud_overnight_end_2026-11-20`); more than 15 is the user's
  call. The 71 carry `captions_error = "deferred: ..."` for the hourly backfill from spare quota (400 units each, a week or two); drop
  this bullet once all have `captions_set`.
- **Archive:** Jer (The Quartering) 11,571 items / 2,732 h transcribed on Modal, FTS index built, `archive ask`
  works; link checks built but not yet run against the real services. #44 Jerry Banfield, #45 Papa Meat and
  #47 Ben Avery wait on the desktop install and permissions.
- **Stories:** the user's catch-up format for any drama: `story new` → `story run` → `story plan` →
  `story cut --no-review` (never sent to review, so never uploads), a <30 MB 720p copy to the phone; narrated by
  Eric (`[story] voice_id`). First: kevin-langue-show-drama (Kino show, 5:28). Check lead-ins against the
  dossier (one stated a self-claimed role as fact). The DSP story (11-04) went to Kitsu for review instead.
- **Weekly:** issue 05 (Nov 8-14) built 11-15 in `weekly/2026-11-14/` ($0.78), posted to YouTube as one 10-image post; TikTok and
  the X thread not yet. The faces stage still mislabels (the cover's auto pick was a Disney performer). Issue 06 is Saturday 2026-11-21.
- **The Udder Truth:** storyboard complete, voice and pipeline chosen; next the newsroom set (Brass Eye refs in
  studio/mockups/udder_truth/refs/), new desk plates, then Moo's WAN renders (~1,550 Runway credits).
- **Costs and analytics:** October ~$200 in model/GPU spend, ~$184 of it the Jer archive. `clippa costs`, war room Costs,
  `--providers` (Claude's own count needs `ANTHROPIC_ADMIN_KEY`, not set). Monthly analytics check-in task on the 1st at 09:00
  (October's: `reports/analytics/2026-10-checkin.md`; its Decisions section is what November's follows up). Serve's `clippa
  analytics` retries Google's passing errors and skips a video still failing (#335, #336); a failed analytics or prune run comes
  back in 30 min, doubling per failure (`RETRY_FAILED_S`, #338). Details: log 11-20.
- **Model providers (#72, closed 11-09):** production still runs Haiku and Sonnet. Config-7 is being evaluated through the opt-in overlays `docs/config-7-hybrid-segv4.toml` and `docs/config-7-hybrid-segv4-openrouter.toml` (wiki Model-rollout); the defaults stay. Tools: `clippa compare score`, `clippa models test|gold`; OpenRouter key in `secrets/openrouter_secret.txt`.
- **OpenRouter budget panel (merged, not deployed, 11-24):** Money page, beside API usage. Reads the clippa key server-side. Changing the limit or disabling the key needs `OPENROUTER_MANAGEMENT_KEY` or `secrets/openrouter_management_secret.txt` (never in the page). Warning percent defaults to 75, saved in `streams/.serve/openrouter.json`. Do not touch production `clipper.toml` for this. Reaches serve only after `bash deploy/hub/deploy.sh` from a main checkout once it is merged.

## Waiting on the user
- **Every stage model is on OpenRouter since 2026-11-26** (Anthropic credits ran out; Clippa has no
  Anthropic-to-OpenRouter failover). The 29 keys in the NAS `clipper.toml` read
  `openrouter:anthropic/claude-*-5.5`, each tagged `# OpenRouter stopgap 2026-11-26, was claude-...`
  (five sections added at the end: cast, chat, pick, archive, swarm). Backup:
  `backups/clipper.toml.before_openrouter_stopgap_2026-11-26`. Switch back once the user tops up
  Anthropic by undoing the tagged lines. Don't restore that backup: it predates the 11-27 `[segments] max_s` change. Stages pick it
  up on their next run; serve and the bot were restarted on it (11-26 16:17 PT). The real fix is #454.
- `kick-140303366` has a band-aid `config.toml` (`[shorts] codec = "libx264"`, #429 hit again on
  v05_t51, 11-26); remove it, and `kick-139915441`'s, once #429 is deployed.
- Draft: the NAS agent container (`deploy/agent`, OpenCode + OpenRouter) beside `clippa-claude`.
  It comes up dark (`CHECKUP_AT` empty). Phone bridge is still Brandon's call: Option A keeps
  `claude remote-control` on `clippa-claude`; Option B would be a loopback OpenCode server on
  127.0.0.1:4096 with no password in the environment and no published port. Do not merge until
  that call, and do not edit production `clipper.toml`. `deploy/claude` stays for the burn-in.
- v0.4.0 is released (PR #395, tag 11-23 on 7f6f4ed, the user: "can we tag a v0.4.0 tonight?"; Release "Clippa 0.4.0";
  #259 and #258 step D moved to v0.5.0, the v0.4.0 milestone closed). serve and the bot report `0.4.0` (deployed 11-23
  07:35 UTC); the worker follows once Don's 11-23 run finishes (a background deploy from `C:\clippa-pr378`, detached at
  the tag; remove that worktree after). v0.3.0 was PR #372, 11-21. The recorder is at 6ac5d37 (`0.3.0+13`, 11-22): redeploy it
  when no show is live. The claude container was deployed 11-20 at bd1369b (#356); the nightly checkup is on (`CHECKUP_AT=08:30`).
- Clippa chat works on the phone (user, 11-20). `[chat] model` is Haiku (`claude-haiku-5-5`) by default; any provider's model string
  works there, so BYOK is `[chat] model = "openrouter:<vendor>/<model>"` with `OPENROUTER_API_KEY` (wiki Model-providers); the
  Discord bot doesn't use the chat. The cloud environment named "clipper" (11-18) holds an OpenRouter key for cloud Grok reviews: kept (user).
- **#293** (`clippa fetch` of Twitch and YouTube VODs, deployed 11-19; #292 closed). YouTube fetch checked 11-20: a 12-minute Kino
  video downloaded at 360p (a 4K remaster of another video got a 403 on its data). Still untested: a subscriber-only Twitch VOD (the
  cookies in `secrets/twitch_cookies.txt` are the user's `gadnuck`, a tier 1 sub of `nmnader`, who has no VODs left) and `clippa run`
  on a `twitch-` or `youtube-` stream (it spends: Modal transcription and the models). A show claims one by listing its channel in `channels`.
- **`clippa streams prune`:** `[streams] keep_source_days = 60` since 11-20 (user: keep at least two months; backup
  `backups/clipper.toml.before_keep_source_60_2026-11-20`), so serve's daily `prune` job runs once serve restarts (the next hub
  deploy). Nothing is that old yet: 0 MB would go at 60 days (streams take 2.5 TB, 23 TB free, about 70 GB a day).
  `[streams] keep_free_pct = 15` since 11-20 (PR #347, deployed at f70e26b, serve restarted; backup
  `backups/clipper.toml.before_keep_free_pct_2026-11-20`): the daily prune takes streams older than 60 days only while the share has
  less than 15% free, oldest first (48% free now, so nothing goes), and the checkup warns under 15%. The test stream is in `never_prune`.
- Branches and worktrees (11-20 cleanup, at the user's word): origin keeps only `main` and four unmerged branches on purpose:
  `archive/instagram-source` (the Instagram-source work, base is main as of #215, rebase before a PR), `story-dig`, `jev-lab`
  (`C:\clipper-jevlab`) and `ccr-8319354b-axsr9v` (fan-mag, paused for the user's next tweaks). Local `feat/twitch-youtube-fetch`
  was never pushed and is probably covered by #293: diff against main, then delete. Check `git branch --show-current` before branching.
- The DSP story video is in Kitsu for review (Kino Casino / "2026-11-04 story-dsp-credit-score-590", 3:14).
- TikTok drafts to post by hand: the 10/25 batch (kick-133940023 v04_t04, v05_t05, v07_t14, v08_t18) would
  need `--resend` once there is room.
- Approved-and-scheduled Shorts that a scene re-render would change (v06_w11, v03_w06, v03_t23) are left
  alone until the user says. The four Shorts from kick-136292920 went up with Chud's old cam crop; they stay
  that way unless deleted and re-uploaded.
- Content ID (11-23): Studio's claims read through the pane (in-page fetch of `list_creator_received_claims`, recipe in the log):
  37 of 505 videos claimed, Free Bird in 13; the map to schedule keys is `claims/studio_2026-11-23.json` on the NAS. The 4 blocked
  ones: Erase song applied to 3 (Lost in America and Kashmir released; Maria También and No More Tears were still processing,
  re-check in Studio); **Hellraiser III (`kick-136479997/s16_hellraiser`) is the user's to trim**. Check new pre-show and one-off
  uploads in Studio until #396 lands.
- Shorts "related video" (Studio only): a weekly scheduled Claude task (`clipper-weekly-related-videos`, Sundays 10:00 while the app
  is open; switch the pane's Studio to Lolcow Cinema or every Short's page errors) sets them; `clippa upload --related` lists what's
  due and records what you set; six Shorts match no scheduled segment. Verify whether `thumbnails.set` works on Shorts before turning
  on `[youtube] short_thumbnails`.
- Old approved Short titles/descriptions that still say "hosts" (Kitsu text): a war-room find-and-replace dry
  run found 14; not applied.
- Decisions on #66 (transcript-framed edits): which item kinds, replace or sit beside the plain cut, sound
  effects, order after the edit plan (#66's #67 section). Sloth Beat handle check (@slothbeat).
- Cast (#41 closed 11-15): it fills itself; look over Kept and New in the war room's Cast window (drop wrong or costume
  photos; image search photos always wait for you). Check Jer f868aa21db (auto-kept at 0.73 from a costume
  night). Candice Owens has the alias "Candace Owens" (Commons' spelling, 11-13).
- Faces: enroll PPP in his maple-leaf cap from the first non-costume stream (October was costumes every night).
- Permissions: Papa Meat (#45), MikeyNohMore (#80). Ben Avery archive pull (#47); Jerry Banfield pull waits on
  desktop/storage (#44).
- `clippa setup` (#78) phase 1 passed in Windows Sandbox (2026-10-28). Next: the real install on the desktop
  (#46; clone into a short folder like `C:\clipper`, run `deploy\bootstrap.ps1`), then the MacBook as #78's join-mode test (#7 closed into #78, 11-13).
  PR #352 (11-20): setup no longer lets a worker, serve or bot machine skip the Kitsu address and token (doctor failed
  without them); merged 11-20, reaches the hub with the next deploy.
- `ANTHROPIC_ADMIN_KEY` for Claude's own usage count on Costs, if wanted.

## Open work
- **Hold the hub deploy for the review-fix PR (11-23):** the Cursor agent PRs merged since
  v0.4.0 (#398 cost estimates, #400 / #414 OpenRouter limits, #415 budget panel) had Grok
  findings nobody handled, and a review found more. Branch `fix/cursor-pr-review` fixes them
  (log 2026-11-23 "Review fixes for the Cursor PRs"). Deploy only once it is merged; #388 (the
  Discord cards) is already on the hub, so its fixes reach the bot with that deploy.
- **OpenRouter spending limits (not deployed):** a key limit or empty credits stops the model
  call at once (`OpenRouterLimit`); an in-flight budget waits for `Retry-After` (up to two
  minutes a try) and retries; when it can't, only that call fails. A segments run that stops
  this way does not write a partial `segments.json`. Not on the hub until
  `deploy/hub/deploy.sh` runs. Production `clipper.toml` was not touched.
- **OpenRouter Claude overlay (not applied):** `docs/openrouter-claude.toml` is every Claude
  stage model at the same tier and effort, as `openrouter:anthropic/claude-*.5`. No price
  rows: a `--dry-run` reads OpenRouter's public list once per process. Paste into
  `clipper.toml`; it needs `OPENROUTER_API_KEY`. The defaults stay on Claude. Direct Claude
  access ends around 2026-12-22.
- **Long segments (config-7 prep, not in production):** `[segments] split_over_s` defaults to 0
  (off). A negative value is off too; a non-number is a one-line error. The NAS `clipper.toml` does
  not set it. **`[segments] max_s = 5400` (90 min) on the NAS since 2026-11-27** (user, #430 closed: 3 of 4 hour-plus
  topics on `kick-139915441` were approved cut whole; backup `backups/clipper.toml.before_segments_max_s_90_2026-11-27`);
  `defaults.toml` keeps 3600. Turn it on (3000, the upcoming segments prompt's 50 minutes)
  only together with that prompt. Discord cards can show the flag and a Re-split button once it is
  on; that bot change is a draft and is not deployed. Do not set 3000 in production `clipper.toml`
  until the segments prompt that goes with it is in.

- **Music and the editor (planned 11-23, build on hold until the user says go):** v0.5.0's key feature is #396 (the music check:
  `[music] strip = off | all | identified`, default handling per span, claims import, a Music list in the item window; step 1 is
  `audioedit.py`, bleeps moved onto it). The editor moved to v0.6.0 as #408: one reusable Descript-style editor for one video,
  span kinds as plugins, `spans.py`, `/api/edit/<ref>`. Under #401 in build order: #408, #309 (in/out and cut-outs, the first new
  kinds), #403 Find a quote, #402 stream library, #404 custom cut, #405 Ask for a moment and marks, #406 GIFs, #407 paste a link.
  Each action is also a catalog tool (#365's rules: agents propose, a person saves). Every feature gets an Options table with safe
  defaults and a no-cost path (user: "options options options"). YouTube has no API for claims or Erase song: assisted only.

**Priority labels (user, 2026-11-13):** every open issue carries one: `p1-now` (the queue, `gh issue list --label p1-now`),
`p2-dated` (#258, #259, #158), `p3-track` (one big track at a time, the user picks: desktop install #46 →
#44/#47/#45/#78; Udder Truth #58-#60; archive tooling #221-#227 before the next mini-doc; b-roll #229), `waiting` (#80, #48,
#45), `parked` (the rest). **Milestones are versions (#370, set up 11-21):** v0.4.0 Runs itself (12-06), v0.5.0 Review and retakes (12-20), v0.6.0
Agent-first (01-15), v0.7.0 Second channel (02-05), v0.8.0 Next mini-doc toolkit (02-26), v0.9.0 The Udder Truth (03-31), v1.0.0
Clippa 1.0 (05-31); #370 lists the issues. A release closes its milestone (wiki Developing#releases). A new issue gets a priority
label, and the milestone of the version it is meant for (none for `parked` and `waiting`).
Write "#77 L6" rather than "fix #77" in commit messages: GitHub closes the issue on "fix". Roadmaps are issues only (byvfx/clippa):
Instagram Reels #39, lore-order playlists #43, archive fun #63, crisis mode #73, setup #78, live clips #81,
blind items #111, investigate anyone #138, meaning search #53, P&L revenue #50 (the cost ledger is built). Old-issue triage
11-20: #36 closed (its Posts parents #30/#32 went 11-09), #56 folded into #48 as a checkbox; the rest checked and kept. **Transcript-framed edits #66** (one issue; sections
#67-#71): #70's tighten is built (`[edit] tighten`, PR #191) but the user wants it **off** until it clearly helps (turn it on per
show in `[shows.<key>.edit]` only at their word); #67's model edit plan waits until edited Shorts prove themselves.
**Agent exploration #158 (Clippa messenger):** v1 (PR #285), in the war room (PR #290), `youtube.retitle` behind
`actions.edit_meta` (PR #291) and the `bulk`/`undo`/stream-name tools (PR #299) are all live (wiki Clippa-messenger); next, a week
of phone use; #300 (access levels, a local Jev-like classifier in front of the model, a lab first); then a small `clippa mcp`.

- **War room redesign (11-18):** all 8 steps live (PRs #314-#333; #324 done, PR #339); plan https://claude.ai/artifact/2Ny9Ad8bfWpjhyvtpx87eW,
  mockup https://claude.ai/artifact/H8uGqKzPmXJcXnAAUcNK4Z. Left: Library/Research/Settings still open the old windows from tiles; follow-ups #325-#328.
- **War room heat graph (PR #294, live 11-19):** 🔥 on a stream or item (`/api/heat`, wiki War-room#heat); not yet seen on a real phone.
- **Review pre-flight checks and approval by silence (#259):** steps A (`clippa review check`, PR #263) and B (`clippa review
  auto`, PR #265, serve's `auto` job every 10 min), stale-finding rechecks (#270) and hardening (#273) are live (wiki Review).
  A missing OpenCV stops the run after saving items that don't need it, and does not cache a bad cover for the ones
  that do; one already cached that way is checked again without `--force` once `uv sync --extra faces` is in.
  **On for Kino since 11-15 and Channel 404 since 11-20** (`[shows.<key>.review] auto_approve = true`, Shorts 6+, segments
  4+ since 11-23; the compilation floor is 0; backups `backups/clipper.toml.before_kino_auto_approve_2026-11-15`,
  `...before_c404_auto_approve_2026-11-20`). Don's Shorts clean since 11-20 (checkup `auto` all good 11-23). Segments (#258 A-C)
  are merged and deployed (PRs #260-#262). **Segment floor set 11-23 (user: "Yes, set 4"):** `auto_min_segment = 4` for kino
  and c404 (from 7-day views: 1-3 underperform, 4-5 a coin flip, 6+ better; backup
  `backups/clipper.toml.before_auto_min_segment_2026-11-23`); first applies to Channel 404's 11-23 stream. Next: a few days of
  auto-approved segments with nothing undone, then close #259 (v0.4.0). #258 stays open for step D only,
  `[segments] min_score` (not cutting low segments at all), after two weeks of `lift` tables (~11-29), at the user's word. Shorts backtest: 105 of 181 approved Shorts would have approved themselves.
- **Demo videos (11-16, user):** a portfolio piece first (about 5:40, real shows, none named in the narration, the user reads it live;
  on the byvfx site), then an install tutorial recorded during the desktop clean install (#46/#78). Script and checklist:
  https://claude.ai/artifact/W48qW95ZPYkA7AE16F4TEX; build with `clippa doc`. **Intro (25 s):** Resolve project `clippa_intro`, media
  `demo-intro/` on the share, sources `mockups/demo-intro/` on the laptop; the user heard the sound ("good"); still theirs to judge:
  the haze, the spot's flicker. **Brand title (11-23):** timeline `intro_v4_lockup` (a copy of `intro_v3_stage`, which keeps the old
  Segoe "Clippa") has the kit's lockup (In-Out C + lowercase `clippa`, Plex Sans Medium) as the title matte, the mark `#5EA88A`
  and word `#E8EEF6` at the end, the black easing to navy `#0B1220` over 484-496; render `renders/intro_v4_lockup.mp4` (frames
  0-279 from `pop3`). Mattes and scripts in `mockups/demo-intro/work/lockup/`. **Then the reveal (11-23, user's
  pick):** `intro_v5_reveal` (copy of v4): the clips play through a big centred C (12-20 s), the line folds inside
  it, then the C rasters back in green (option 4) and slides left, shrinking, as `clippa` slides out (option 1's
  move); render `renders/intro_v5_reveal.mp4`. The 12-15 s cross-fade replaced three ways, each a matte sequence
  in `TitleFile`: `intro_v6_scan` (continuous scanlines), `intro_v7_boxes` (the user's box iris, middle punches
  open), `intro_v8_iris` (iris hugging the C); renders `renders/intro_v6_scan|v7_boxes|v8_iris.mp4`. The user
  liked the box and iris but wanted them much slower: `intro_v9_boxes_slow` and `intro_v10_iris_slow` build from
  12 s to 17.8 s (`kit/win_boxes2|win_iris2/`), renders `renders/intro_v9_boxes_slow.mp4` / `intro_v10_iris_slow.mp4`,
  then, on the user's notes, both dragged out further and softened: `intro_v11_boxes_soft` (sides together, then top
  and bottom together, S-curves) and `intro_v12_iris_soft`, the C whole at 18.8 s, edges soft until it sharpens over
  17.8-19.5 s; both with the thicker raster reveal (`kit/reveal_seq_v3/`), the motion-blurred switch-on (media pool
  clip `kit/screen_content_24_v4.mp4`) and a very light chromatic aberration at the end (`EndCA`); renders
  `renders/intro_v11_boxes_soft.mp4` and, for the iris, `intro_v14_iris.mp4` (v12's timeline, its matte with the CRT
  bulge baked in, `kit/win_irisp4/`, so its edge doesn't pop at the frame's corners); the user keeps both (11-23, "perfect"). The pop at 4.2 s (channel 1 arriving shoved down the tube) is gone: the media pool's screen
  clip is now `kit/screen_content_24_v3.mp4` (`work/fix_roll.py` wrote v2, lossless, which Resolve decodes blown out;
  v3 is it at CRF 4, High profile; frames 100-101 changed; every timeline uses it). v9/v10's `TitleBulge` samples with
  duplicated edges (`getr1d`): with `b` the all-white matte bulged black corners in from frame 0 (v6-v8 have them at 280-287), and
  splices take frames 96-107 from `renders/intro_popfix_096.mp4` (`work/reveal/splice.sh`). Don't touch Resolve timelines while a render runs (it hung on 11-23). This reveal is the baseline; the user wants to explore the raster further
  after (memory `clippa-logo-reveal`; first idea: a continuous scanline build-up). Options and scripts:
  `mockups/demo-intro/work/reveal/`. Build notes: log 11-17/18/23.
- **Documentaries (PR #233, live 11-13):** `clippa doc new|voice|build|master|leak|kit` (wiki Documentaries). Untried because they
  cost: `voice` against ElevenLabs, `leak` on Modal (`--dry-run` first); reels at another frame rate than the timeline. On the
  first real piece, listen to every take and watch the first built timeline. `[story] lufs = -14` is new (a story cut before it is
  re-rendered on its next cut).
- **B-roll #229 (steps 1-2 of 4) and clip art (PR #255):** `clippa broll ...` (NASA, DVIDS, Prelinger, Commons, Pixabay) and
  `clippa clipart ...` (wiki B-roll, Clip-art); libraries `[paths] broll` / `clipart` on the share. Next for b-roll: the National
  Archives (key by email from NARA), `[story] broll` in story cuts, a war room window. Not yet used by covers or the weekly. Drop the
  "Popcorn function" maths plot (`commons-c2e98328`) from the clip art.
- **Usability review (11-10):** built and merged 11-13 (#199, #204, #228, #205). Still open: new users and the public repo #207
  (this install's values in tracked files, packaging; its Getting-started scheduled-task line fixed in PR #353, 11-20).
- **Checkup (wiki Checkup):** the nightly run (`deploy/claude/checkup.sh`) is on since 11-20 (08:30; a dry run printed "all
  good", so the headless `claude -p` path and its `--allowedTools` rules are still untried: check the first report with
  findings, and the first `checkup/` PR if one comes). **#287 is closed (11-20):**
  `clippa checkup --fix` (PR #341; repairs `retry`, `tmp`) with `[checkup] fix = ["tmp"]` on the NAS since 11-20 (user; backup
  `backups/clipper.toml.before_checkup_fix_tmp_2026-11-20`); `"retry"` stays would-fix until the user says (its rerun may call
  models). PR #356: checks `tools` (yt-dlp age), `signins` (cookies and TikTok/X tokens), `logs` (repeating errors), `approvals`
  (approved items the plan never saw), and the nightly run may open one PR a run through `checkup-pr` (own worktree, tests
  without credentials, never merges). Watch the first week of nightly reports and any `checkup/` PR.
  **Sign-ins hourly (PR #359, merged 11-21; not deployed yet):** serve's checkup job becomes
  `checkup --fix --alert` and reads `signins` too: a sign-in that is gone (bad, not the expiring warnings) is DMed once, when it
  first shows (`streams/.checkup/alerted.json`), and `clippa status` lists signins findings under "needs you". Reaches
  serve with the next hub deploy; `--alert` has run in tests only (the live sign-in check was clean).
- **Bootstrap follow-up (11-07):** `deploy/bootstrap.sh` installs Homebrew `ffmpeg`, which has no libass (`subtitles`) filter, so a
  Mac worker fails Short captions; `ffmpeg-full` has it but is keg-only: install it and point Clippa at it, or document it.
  `deploy/hub/deploy.sh` from `lofn` needs `deploy/hub/env.local` (it overwrites the NAS `.env`).
- **Investigate anyone (#138; `clippa people` merged 11-04):** next, in the issue's order: speaker attribution (needs #53) →
  `investigate start` → X reads (check the price) → Obsidian vault → watchlist → public records → `investigate eval` → Good/Noise + `tune`.
- **Story/swarm leftovers:** `investigate check` on the swarm (branch `investigate-swarm`, unmerged). YouTube downloads in a story
  hit the bot check from this IP: a fresh cookie export or the Firefox profile. Modal cold starts dominate a story's few items.
- **Code audits (10-28, 11-03, 11-08, `docs/archive/`):** all plan items done. Still open from 11-03: #15, #24/#25, #29-#35, #37, #41,
  #46-#50 (lows), #42's lock part. `tests/test_modules_have_tests.py` lists 13 untested modules (the list may only shrink); 17
  functions over McCabe 15 carry `# noqa: C901`, split when touched.
- **Weekly and monthly audit (wiki Audit):** `clippa audit` DMs how the week went; on the 1st a read-only `claude -p` leaves
  `deep-<YYYY-MM>.md`; the claude container runs both after the checkup (`audit.sh`). Backups are `[backup]` (`backups/taste`, every 7
  days by serve). Dependabot opens grouped weekly PRs (ruff, torch, torchaudio and torchvision ignored; no review, no secrets); its
  security alerts are a separate repo switch, off as of 11-10.
- **Blind items #111 (wiki Blind-items), steps 1-2 live since 11-16:** the NAS clipper.toml has one source, CDAN (add Blind Gossip's
  site when it's back), `[blinds] check_minutes = 30`, `sensitive_alerts = true`, a private `#blinds` with `alert = "digest"`. Not
  built: typed answers in the thread, a Hide this kind button. **Next: step 3**, investigate after the answer; step 4, reveals and the
  scorecard. **Custody and step 4 part 1 merged 11-22 (#384; follow-up #387 for Grok's last two findings), deploy pending:**
  the store is in `clippa backup` as a checked snapshot (a damaged one goes in as `blinds.damaged.sqlite`; a damaged or busy store
  drops no older zip), nothing is forgotten (user: "def keep all this data": `keep_days` default 0), an edited post's old text goes
  into its history. Every check matches reveals to blinds in code (3-gram overlap >= 0.6, 0.3 ahead of the next, within a day of the
  date line) and stores the answer; `clippa blinds reveals` scores your guesses (right / wrong / late); `list` and the war room say
  [revealed] without the name. **Backfilled 11-22** in serve (`check --backfill 326 --no-clues`, backups in `/data/backups/blinds.sqlite.before_*`):
  6,144 posts back to 2026-01-01. On a copy with the new code: 1,572 of 1,852 reveals match, 0 doubles; 193 wait for blinds before
  2026 (143 from 2025), 48 quote a blind the feed never had, 35 are 2012-13 "Revealed <date>" posts. **Deploy before 2027-01-02:**
  the deployed code still forgets non-answered blinds after 365 days. After the deploy, backfill further back (needs
  `CLIPPER_BLINDS_BACKFILL_PAGES`, ~16 pages a quarter). Clues for the backfill (~$0.70) are read by serve's checks, 100 a pass.
  **Reveal follow-up (11-23, branch `blinds/reveal-followup`):** a blind you answered, once revealed, gets a Discord
  reply (its alert, its digest's thread, or the channel) with who and right/wrong/late; the war room shows who only on a blind
  you answered, with the grade, and the scorecard in the window's header. Not built: `calibrate`, step 3 (user: eventually check all data it can grab, not
  only the cast). Copy the newest taste zip off the NAS by hand. Flow: https://claude.ai/artifact/1m3obUigUkkA5ZVdmmiLWn
- **News watcher #110:** off since 2026-11-03 at the user's word (`[news] check_minutes = 0`, no terms); `auto_watch = true` brings back
  the cast/figures/library list; `#news` exists in Discord.
- **Older notes not on an issue:** republish the 2026-10-19 re-cut streams to Kitsu (user said not yet); Blade compilation's Discord
  card never posted (`review notify kick-131151926 --keys comp-blade-in-vegas`); a daily "post kit" folder; thumbs: reject a picked
  frame with no face, `--frame-at <s>`; Discord excerpts for one-offs could start after the intro; pick/preshow/retake prompts don't
  read speaker tags yet.

## Recipes (how we did it last time; details in the log)
- **Restart serve / the bot / the worker:** `clippa services restart serve|bot|worker` (docker compose on the
  NAS over ssh); code changes: `bash deploy/hub/deploy.sh [serve|bot|worker]` from a main checkout (Git Bash).
  A serve restart or deploy kills a running upload: `clippa services status serve` lists its jobs; wait until
  none is running, and until no show is live if the worker is busy.
- **What is going on:** `clippa status` (serve's, the bot's and the recorder's heartbeats, the worker's
  streams, review counts, the next releases, this month's spend; `--sync` reads Kitsu first).
- **Before a model rerun:** `clippa score|segments|meta|thumbs <stream> --dry-run` prices it from the prompts
  it would send; `score` refuses a rerun without `--force`.
- **Upload one item now:** approve in Kitsu, `clippa schedule` (back up schedule.json first), then
  `clippa upload --max 1`; asap items go public at once (serve's hourly runs log to `logs/serve.log`).
- **One-off from a YouTube video:** `clippa --show <key> preshow fetch <url> --from H:MM:SS --about "..."`
  (add a `[shows.<key>]` table with a placeholder channel if the show isn't on Kick), then `clippa run yt-<id>`.
- **Drama catch-up:** `clippa story new "<brief>" [--slug x]` (every step; `story run x --from <step>` redoes
  one) → `story plan x` → `story cut x --no-review`; edit `streams/story-x/story.json` by hand and cut again.
- **Fix a cover by hand:** edit the entry in `thumbnails.json` (frame is 1-based; `t`, `face`, `headline`,
  `text`, inset, band), back it up, `clippa thumbs <id> --ids sNN --compose-only` (attaches in Kitsu);
  `youtube.set_thumbnail` if it's already up. The war room's cover editor does the same.
- **Hand picks:** `clippa pick <id> --find WORD`, then `--from/--to` or `--at` with `--about`, `--dry-run` first.
- **Retake without changing anything:** `clippa retake <id> --item c03 --comment "..." --dry-run`.
- **Related video in Studio by hand:** the field is `#linked-video-editor-link`; click its trigger, search the
  segment's video id, pick the one result, Save; then `clippa upload --related <keys or links>` records it.
- **TikTok by hand:** `clippa tiktok --items <keys> [--resend]`; captions land in Discord #tiktok.
- **NAS recorder deploy:** `bash deploy/record/deploy.sh`, only when nothing is live (it refuses otherwise).
- **Per-stream overrides** (a big night): `streams/<id>/config.toml` (`[schedule] priority = true`,
  `kick_first_hours = 0`, more picks). `CLIPPER_SCHEDULE_START=<date> clippa schedule --reshuffle` refills today.
- **Checks:** `clippa doctor --role gpu --offline`, `uvx ruff@0.17.3 check clippa tests`,
  `.venv/Scripts/python.exe -m pytest -q`, test stream `kick-131151926`.
- **Clean installs (#78):** Windows via a Sandbox `.wsb` running `deploy\bootstrap.ps1 --answers ... --offline`, Linux via Docker
  `ubuntu:24.04` running `bash deploy/bootstrap.sh --answers ... --offline` (~5 min); no keys go in; then the wiki Install-checklist.
  The full steps are in the log (11-04 to 11-07 entries).

## Back up the taste layer (user, 2026-10-24)
The Python is replaceable; the months of editorial judgement are in prompts and config. Treat them
like footage. `clippa backup` (PR #248, #204; wiki Backups) zips the list below into `backups/taste/`
(about 17 MB, 390 files; the newest 8 kept; secrets only with `--with-secrets`), and serve makes one every 7 days. The first was made by hand 11-13 (`backups/taste/taste-2026-11-13-1707.zip`,
no secrets). Copy one off the NAS now and then. Not in it: `deploy/*/env.local` and the Kitsu volumes.
- **In git:** `clippa/prompts/` (never edit a version that produced reviewed output), `clipper.example.toml`, `studio/`, `reports/`, this file.
- **Not in git (all in the NAS folder):** `clipper.toml`, `backups/`, each `streams/<id>/config.toml`, `secrets/` (tokens are a
  re-consent to recreate), `faces/known.json`, `photos/` + cast.json, `layouts/`, `people.json`, `schedule.json`, `deploy/*/env.local`,
  and the Kitsu volumes (every review decision and note).
- Also the project memory `~/.claude/projects/C--clipper/memory/` on the laptop: it holds decisions
  that never made it into HANDOFF.

## Gotchas learned
- `gh pr merge --auto` merges at once here: main's ruleset requires no checks, so there is nothing to wait for (PR #353,
  11-20). "Merge when green" means waiting for the runs (`gh run watch`) and the review, then `gh pr merge --merge`.
- Deploys from PowerShell: plain `bash` is WSL's (broken: "getpwuid(0) failed", "Failed to translate"). Use
  `& "C:\Program Files\Git\bin\bash.exe" deploy/hub/deploy.sh`; the user forgets, so always give that form.
- The claude container: its clone sits on a mount where every file is executable, so ruff there needs
  `--extend-ignore EXE002`; its GitHub token can't read checks or Actions (403), so the user confirms CI on the
  PR. Relative config paths outside `[paths]` resolve against the config's folder (hub /data, container /app).
- New Windows installs: keep the folder short (anthropic's files sit ~160 characters deep in .venv; past 260
  imports fail with long paths off); `tzdata` must be a direct dependency (Windows has no zone database).
- Kick: yt-dlp VOD extractor 404s; resolve via the channel videos API (kick.py). Chat history API ~2 req/s,
  429 with Retry-After: 0 → adaptive limiter.
- Stream-copy cuts must start on a keyframe (every ~4 s) or audio drifts; `copy_cut` seeks k+0.005 for `.ts` and k+0.15 otherwise
  (ffmpeg backs input seeks off by 3/23 s with B-frames) and fails the cut on a sync gap or wrong length. Each compilation part ends
  with a concat `outpoint` where the next file begins, or joins replay 1-5 s (the H3 video, 10-28).
- Reading a shared JSON file on the NAS while another process replaces it raises PermissionError on Windows
  and lands mid-replace over SMB. Read state files with `jsonstate.read`, which retries for ~9 s like `write`
  does, never `json.loads(path.read_text())`; heartbeats go through `daemon.read_heartbeat` (three reads, and
  an unreadable file is unknown, not absent: `record status` exits 1, deploy refuses).
- ffmpeg 8.1 NVENC needs NVIDIA driver 610+ (laptop has 596.72) → use `h264_qsv` locally.
- Bleeping uses asendcmd + a .cmd file (one big volume expression breaks past ~dozens of spans); spans
  ending before the cut point must be dropped.
- Kitsu's proxy rejects big uploads; previews over `[review] max_upload_mb` (480) re-encode to fit.
- review.py reads every `*.json` beside cut videos as sidecars; metadata lives in `metadata/<folder>/`.
- Windows: a file open in another process can't be replaced (fork sessions render previews from cuts).
- `deploy/common.ps1` (the worker task scripts) finds a service as a clipper.exe command line ending in its
  name and once killed its own `services restart` chain; it skips its own process chain now, keep that.
- The war room page loads `warroom.css`, `theme.js`, `themes/*.css` and `js/*.js` from disk: a pull that changes the page before serve
  restarts leaves a bare, unstyled page. Deploy or restart serve.
- serve runs `schedule.run` in-process (the war room's and the bot's replans go through its /api/replan):
  scheduler changes need a serve restart. A bot whose serve is down still writes the decision to Kitsu and
  says "not replanned"; the next hourly run places it.
- Retake planner can't edit cover layout; a note about the cover's boxes comes back "unsupported", or gets
  read as an edit it can make ("single issue" became a 63 s trim, 10-28). A layout rule belongs in config;
  undo a bad retake with `retake.apply` + `publish_retake` and a hand plan.
- A recording with dropped HLS segments used to shift the transcript clock; `extract_audio` fills gaps with
  silence. Check new recorders' gaps against the source length.
- Discord review cards carry only the first 60 s of a segment; the full preview is in Kitsu. A reject from
  the card can be about the excerpt, not the cut.
- YouTube sign-in: the OAuth app is In production since 2026-10-30 (tokens from before kept the 7-day
  Testing limit; the user signed in again 11-02, so the token shouldn't lapse). `clippa doctor` (online)
  refreshes it to test it; `upload --sign-in` keeps the analytics scope.
- YouTube bot-checks this IP for yt-dlp: pre-show, one-off and story grabs retry signed in through the dedicated Firefox profile
  (`[youtube] cookies_from_browser = "firefox:clipper"`) or secrets/youtube_cookies.txt (re-export when "cookies are no longer
  valid"). A stuck pre-show DMs once per video; a PO-token provider doesn't help. The hub containers have no browser, so they read
  the file, which the laptop's `clipper-cookies` task refreshes (wiki Archives#keeping-the-nas-hubs-cookies-fresh).
- Kino's pre-shows are on the main channel @TheKinoCasino since 11-04 (`clipper.toml` since 11-06). That
  feed's RSS 404s from the NAS, so the check reads yt-dlp's undated listing: entries below a grabbed pre-show
  count as older (#162). After a channel switch, grab the latest by hand.
- A cut killed half way can leave `clipper-cut-<id>` running on the NAS (the name stays until #286's last pass: it is the lock
  old and new code share): `ssh nas docker rm -f clipper-cut-<id>`.
- `uv sync` can't replace a running clipper.exe (`uv pip install` instead, and include `--extra modal`
  later). yt-dlp needs deno for YouTube.
- The worker runs the code of the last hub deploy, not the checkout: a merged change reaches the containers
  only with `bash deploy/hub/deploy.sh`.
- Modal per-run cost estimates overcount parallel archive runs; trust the billing line in `clippa modal`.
