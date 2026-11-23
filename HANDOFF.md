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
the GitHub wiki, built from `docs/wiki/` (`.github/workflows/wiki.yml` pushes it on every push to main);
README.md and SETUP.md are short pointers; CLAUDE.md the conventions; `reports/` the monthly analytics; `docs/archive/` the old audits.
Project memory (`~/.claude/projects/C--clipper/memory/`, MEMORY.md first) holds durable preferences.
- Plan doc: https://claude.ai/code/artifact/a9bb9e38-ced0-4455-8d8e-18a78122f45f; usage walkthrough (2026-10-27):
  https://claude.ai/code/artifact/0d0e04da-54f6-4f15-bad0-2df72a286383; interactive demo: https://claude.ai/artifact/ASL1grYiajRAELyoqTGitN
- Case study (11-11): https://byvfx.github.io/clippa/ (`clippa/index.html` in byvfx/byvfx.github.io), from the private artifact "Clippa
  Overview" (https://claude.ai/artifact/HTF3noAawBG1M1JNYsLAMS): edit that, splice `<div class="figures">` to `<footer` into the
  site page, and carry the text to the wiki's `Overview.md` too. No show names on the page; its documentary case study names no
  subject, channel or title. The figures (347 videos, 799 tests) are dated 11-13; the 11-11 log has the re-render recipe.
  Docs site (11-23, draft, not live): MkDocs Material from `docs/wiki/` (`mkdocs.yml`). Do not enable Pages on
  `byvfx/clippa` until `clippa/` in `byvfx/byvfx.github.io` is renamed. A project site at that URL replaces the case
  study; GitHub does not merge the two. Go-live steps are on the docs-site PR.
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
  bot are compose-only. **Merge policy and reviews are in CLAUDE.md (Checks)** and wiki Developing#code-review-on-prs: a code PR
  (`clippa/`, `tests/`, `deploy/`, `.github/`, `pyproject.toml`) gets the local `code-review` skill before the first push, then
  Copilot, or Grok's `review` workflow while Copilot is out of credits (back 11-20 "for a few more days"; delete the workflow once
  Copilot reviews again; its repo secret `OPENROUTER_API_KEY` stays); merge once the review is in, each finding answered, CI green.
  Other PRs (wiki, `uv.lock`, `clipper.example.toml`, `streamers.json`) have CI but no review, notes-only ones neither; both merge on
  the user's word. A fork's PR: read the diff by hand, then the user's word.

## State now (2026-11-20)
- **Hub on the NAS** since 2026-11-05 (wiki NAS-hub): compose project `clipper-hub` in `~/clipper-hub`, containers
  `serve` (war room on 127.0.0.1:8765, on the tailnet as https://clipper.taile415cd.ts.net/; heartbeat
  `streams/.serve/status.json`), `bot` (`streams/.bot/status.json`) and `worker` (cuts with QSV on the iGPU).
  Code changes go out with `bash deploy/hub/deploy.sh` from a main checkout; `clippa services status|restart ...`
  runs docker compose there (`[serve] hub`). Last deploy 2026-11-20 ~09:00 (user): main as of #336, the analytics fix (serve, bot and worker report `0.2.0` plus the commit; the recorder is older). **The config and
  all state are on the NAS:** `//192.168.0.155/docker/clipper` (`/data`) holds `clipper.toml` (the one copy;
  `CLIPPER_CONFIG` on the laptop points at it), `schedule.json`, secrets/, backups/, logs/, faces/, voices/,
  news/, weekly/, archive/, layouts/, people.json, fonts/. The laptop's old copies: `backups/laptop-state-2026-11-05/`.
  The laptop's scheduled tasks are disabled, not removed (rollback: wiki NAS-hub).
- **NAS** (192.168.0.155) also runs the recorder container (`deploy/record`, redeployed 2026-11-20 at 74be7d2,
  watching kinocasinogaming + donflurgundy + chudlogic), Kitsu :8090, the cut container, the streams and photos
  shares. `kick-138291641-2` is Kino 11-17 after the show ended early (Andy playing a game): recorded only, held
  from the worker by `run.json` `{"status": "skip"}`; delete it whenever you like. The `claude` container (`deploy/claude`, wiki Claude-on-the-NAS) drives Clippa from the phone.
- **Mac `lofn`** (Apple Silicon, setup's `control` role, 11-07): a dev/control station that runs nothing
  unattended. Clone `~/clipper`; `CLIPPER_CONFIG=/Volumes/docker/clipper/clipper.toml` and
  `CLIPPER_LOCAL_CONFIG=~/clipper/clipper.local.toml` in `~/.zshrc` (mount the share in Finder first); ssh
  alias `nas` (user **`Brandon`**, capital B). `doctor --role control` clean; Homebrew ffmpeg has no libass.
- **Shows:** kino and c404 live through review → schedule; `chud` (ChudLogic, since 11-04: daily ~10 h from
  ~00:00-02:00 PT, budget segment 3 / short 5 per 8 h, 8-Bit Guy moments ranked up, cam panels `focus = "face"`
  since 11-07). His 10-18 VOD `kick-132975332` is fetched and transcribed only: its timestamps jump back
  821.7 s before 7:28, so don't `clippa run` it until fetch is fixed. dsp and blade are config-only tests;
  `trish` is a placeholder for the 2026-10-27 one-off; trish and blade are off the war room (`[warroom] hide_shows`).
- **Schedule** (October check-in, 11-02): `stream_budget = { segment = 10, short = 10, compilation = 0 }` per
  8 h, Channel 404 `segment = 6`; slots a day: 9 Shorts (09/11/13/15/16/18/19/20/21), 6 segments
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
- **Model providers (#72, closed 11-09):** the baseline stays Haiku + Sonnet; no other model came near it on the user's 66-window
  gold set (wiki Model-providers, log 11-04/05). Tools: `clippa compare score`, `clippa models test|gold`; OpenRouter key in
  secrets/openrouter_secret.txt. Moving a stage to another model is a config change whenever the user wants it.

## Waiting on the user
- v0.3.0 is released (PR #372, tag 11-21, the user's word: "as is now"; the Release is titled "Clippa 0.3.0"). The hub was
  deployed at the tag 11-21 (serve and the bot report `0.3.0`). The recorder is still at 74be7d2 (`0.2.0+59`, 11-20): redeploy it
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
- Content ID: check new pre-show/one-off uploads in Studio (video → Copyright; the API only sees region blocks).
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
- **Long segments (config-7 prep, not in production):** `[segments] split_over_s` defaults to 0
  (off). A negative value is off too; a non-number is a one-line error. The NAS `clipper.toml` does
  not set it, and `max_s` is still 3600. Turn it on (3000, the upcoming segments prompt's 50 minutes)
  only together with that prompt.

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
  **On for Kino since 11-15 and Channel 404 since 11-20** (`[shows.<key>.review] auto_approve = true`, Shorts only: the
  segment and compilation floors are 0; backups `backups/clipper.toml.before_kino_auto_approve_2026-11-15`,
  `...before_c404_auto_approve_2026-11-20`). Next: a few days of Don's Shorts with nothing undone (checkup's `auto` check),
  then close #259. Segments (#258 A-C) are merged and deployed (PRs #260-#262). Segment floor, from 7-day views: scores 1-3
  underperform, 4-5 are a coin flip, 6+ do better; suggested `auto_min_segment = 4` (Kino holds 63 of 183 scored, Channel 404 23
  of 106), **not set**: after the Shorts, at the user's word (a checkbox on #259). #258 stays open for step D only,
  `[segments] min_score` (not cutting low segments at all), after two weeks of `lift` tables (~11-29), at the user's word. Shorts backtest: 105 of 181 approved Shorts would have approved themselves.
- **Demo videos (11-16, user):** a portfolio piece first (about 5:40, real shows, none named in the narration, the user reads it live;
  on the byvfx site), then an install tutorial recorded during the desktop clean install (#46/#78). Script and checklist:
  https://claude.ai/artifact/W48qW95ZPYkA7AE16F4TEX; build with `clippa doc`. **Intro (25 s):** Resolve project `clippa_intro`, media
  `demo-intro/` on the share, sources `mockups/demo-intro/` on the laptop; latest full render `renders/intro_v3_stage_pop3.mp4`; the
  user heard the sound ("good"); still theirs to judge: the haze, the spot's flicker, the last 1.78x blow-up. Build notes: log 11-17/18.
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
  Not built: the Discord follow-up, the war room scorecard, `calibrate`, step 3 (user: eventually check all data it can grab, not
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
