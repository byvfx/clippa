# Clippa handoff (current state, rewritten 2026-11-08)

This file is the **current state** between sessions: what runs where, what the user decided,
what is waiting on them, open work, recipes, gotchas. Keep it short (under ~300 lines): update
the sections in place, don't append diaries here. The session-by-session diary is
**`docs/log/2026-11.md`** (one file per month, October in 2026-10.md; `grep "^## " docs/log/2026-1*.md`
lists every entry by date; grep the log, don't read it whole). At the end of a session: fix what
changed here, then add a dated entry at the end of the log.

Stream clipper for Kino Casino (Kick channel `kinocasinogaming`), Channel 404 News and ChudLogic → YouTube
channel **Lolcow Cinema**. Repo `C:\clipper`, remote https://github.com/byvfx/clipper (public, on purpose:
the user said so 2026-11-10; every tracked file, issue and PR comment is readable by anyone).
Python package `clipper/` (`clipper/defaults.toml` is every setting, documented; prompts in `clipper/prompts/`),
config `clipper.toml` (gitignored, on the NAS; `clipper.example.toml` is a tracked starter). The user docs are
the GitHub wiki, built from `docs/wiki/` (`.github/workflows/wiki.yml` pushes it on every push to main);
README.md and SETUP.md are short pointers; CLAUDE.md the conventions; `reports/` the audits.
Project memory (`~/.claude/projects/C--clipper/memory/`, MEMORY.md first) holds durable preferences.
- Plan doc: https://claude.ai/code/artifact/a9bb9e38-ced0-4455-8d8e-18a78122f45f
- Walkthrough (usage, for presenting; Claude Docs, 2026-10-27): https://claude.ai/code/artifact/0d0e04da-54f6-4f15-bad0-2df72a286383
- Case study (2026-11-11, refreshed 11-13): https://byvfx.github.io/clippa/ (`clippa/index.html` in
  byvfx/byvfx.github.io, Pages from main). Its source is the private artifact "Clippa Overview"
  (https://claude.ai/artifact/HTF3noAawBG1M1JNYsLAMS): edit that, then splice the content from `<div class="figures">`
  to `<footer` into the site page, which keeps its own head, nav and footer links. The README's hero
  (`docs/readme/hero-*.png`, #237) is a screenshot of its header; the figures (347 videos, 799 tests) are dated
  11-13 and go stale: the 11-11 log entry has the re-render recipe. The page has no show names; its documentary
  case study names no subject, channel or title, and should stay that way.
- Interactive demo (HTML, numbers as of 2026-10-27): https://claude.ai/artifact/ASL1grYiajRAELyoqTGitN
- Keys are Windows **User** env vars (ANTHROPIC_API_KEY, KITSU_TOKEN, ...); agent shells must load them:
  `export KITSU_TOKEN="$(powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('KITSU_TOKEN','User')" | tr -d '\r')"`

## Pipeline (one stream = `streams/<id>/`, test stream `kick-131151926`)
`clipper fetch <kick, twitch or youtube url>` → `signals` (chat/loudness heat) → `transcribe` (WhisperX on Modal, diarized) →
`score` (Haiku Pass A + transcript scan, Sonnet Pass B) → `segments` (Opus topic split + compilations) →
`cut` (segments lossless w/ keyframe starts, compilations joined, picks re-encoded; slurs+"fuck" bleeped) →
`meta` (SEO title/description/chapters/tags + .srt) → `thumbs` (Enquirer tabloid covers) →
`review publish` (Kitsu on NAS 192.168.0.155:8090 + Discord cards) → `review sync` / Discord bot →
`schedule` → `upload` (YouTube). Always on, as NAS containers since 11-05: `clipper serve` (the war room, hourly
replan + upload, pre-show grabs, retakes, daily jobs), the Discord bot (`clipper bot`) and the worker.

Around it (each a subcommand; details in the wiki pages and the log): `record` (NAS Docker recorder),
`worker` (each finished recording: fast lane clips/Shorts first, then faces, cast, segments), `shorts`
(vertical, scene-aware layouts, 15-59 s), `retake` (Kitsu notes → re-cut/re-cover automatically), `preshow`
(Kino's YouTube pre-show whole; also one-offs with `--from/--to/--about`), `pick` (Shorts from a stretch you
name), `weekly` (ten-page tabloid issue, Saturdays), `receipts` (old clips of an archive subject for a
stream's stories, Mother Moo voice-over), `archive` (back catalogs on Modal: Jer complete, FTS index, `ask`),
`investigate` (an archive subject's own videos → verified facts → leads + coverage check), `story` (a brief →
the swarm, `clipper/swarm.py`: planner → Haiku workers over the web, YouTube, Kick, our streams → dossier →
narrated catch-up cut), `people`, `faces`, `cast`, `speakers`, `narrate` / `perform` (Mother Moo), `tiktok`, `x`,
`rumble`, `analytics`, `costs`, `compare` / `models` (other providers), `status`, `services`, `alert`, `doctor`.

## User decisions (keep)
- The NAS hub's YouTube cookies come from the laptop (11-09, user: "lets just setup that task but note it that it
  prevents full offline autonomous usage"): the `clipper-cookies` task runs `clipper cookies export` every 4 h.
  The NAS isn't laptop-free for signed-in yt-dlp retries: with the laptop off for days, pre-show grabs get stuck.
- The app is **Clippa** (2026-11-09, branding only): war room, Discord status card, wiki Home/sidebar/banner,
  README. The `clipper` command, package, `CLIPPER_*` env, config files, containers, Modal apps, the
  `clipper:` button ids and the `[clipper]` Kitsu note prefix keep the old name on purpose (old cards and
  notes depend on the last two). A full rename needs both prefixes accepted and the old compose project stopped first.
- Titles/descriptions/cut points approved as-is; fun descriptions are liked. Long video per recurring topic
  (compilations) plus segments plus short picks. `third_party_audio = "allow_reactions"`,
  `audience_knowledge = "fans"`, Victor/blackjack segments stay; Kino's gambling is commentary, not play
  (`[scoring] gambling = "commentary"` for kino and c404, prompt v7, 11-05).
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
- Uploads: automatic since 2026-10-20 (serve: `clipper schedule` then `clipper upload` hourly), private with
  publishAt, `[youtube] max_per_24h` 15 (channel allows 100; the user leaves room), shows take turns
  (`mix_shows`). `manual_publish = false`: **"asap" items (pre-shows, one-offs, stories) publish public the
  moment they upload.** Every upload joins its show's playlist; source links are added at upload and
  backfilled hourly. Stay current, no backlog (10-29): `stale_days = 3`, items let go stay approved in Kitsu
  (war room filter "Not going out"; pin with "Publish at" to send one anyway).
- X (@lolcowcinema): `clipper x` posts Shorts as native video, never a link (X bills $0.20 a link post).
  **Auto on since 2026-11-01** (`[x] auto_post_minutes = 60`, `max_per_24h` 6, weekly thread posts count),
  hook first + up to 3 hashtags ending with the show's, a last line crediting the footage's owners
  (`[shows.<key>.x] mentions`, 11-03). `clipper x --issue` posts the weekly as a thread after the user OKs
  the text. The app browser pane is signed in to X.
- TikTok: **parked (2026-11-01)**; the user posts by hand, never direct posting (#62, #116 closed). Don't send
  drafts unless asked (`clipper tiktok --items ... [--resend]`).
- Transcription on Modal (L40S, ~$0.34 per 8 h); Kino and Channel 404 diarized (~$0.30 more; Don enrolled
  11-04). Cut runs on the NAS (`[cut] backend = "nas"`). Whisper writes "Bearing" as "Baring": fix it.
- Weekly magazine (`clipper weekly`, Saturdays): cover full tabloid, inside pages modern, articles in the
  user's own voice (writer pass, `weekly_article_v1`); `clipper weekly --discord` puts pages + captions in
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
- Model calls are logged to `logs/costs.jsonl` (`clipper costs`); say what a rerun costs before doing it
  (`--dry-run` prices score/segments/meta/thumbs). The taste layer is the asset: see "Back up".
- Hand-over (2026-11-08 audits, `reports/code-audit-2026-11-08.md` §7): build as a public package but this
  deployment first, little tech debt, simple for the user; `studio/` holds mockups, research notes and media
  reports, out of the package; serve and the bot are compose-only, the Windows task scripts stay for a GPU
  worker only. Merge policy (11-10): a code PR merges once CI is green on both OSes and the review of its
  head commit is in and each finding is fixed or answered, then delete the branch; green CI alone isn't
  enough. Reviews since 11-13 (user; Grok's `review` workflow removed, ~25 min a PR was too slow): Claude
  runs the `code-review` skill locally before the first push and fixes what it finds, then Copilot reviews
  when the PR opens (repo ruleset "Copilot reviews new PRs", not on later pushes: Claude reviews those, or
  `gh pr edit N --add-reviewer @copilot`). Wiki Developing#code-review-on-prs. Grok is the backup (user,
  11-18): when Copilot's review isn't in once CI is green, offer `clipper codereview --pr N` (OpenRouter,
  ~$0.80) and run it on a yes; its review then stands in for Copilot's. Code means a PR touching `clipper/`, `tests/`,
  `deploy/`, `.github/` or `pyproject.toml`: any other PR (the wiki, `uv.lock` alone,
  `clipper.example.toml`, `streamers.json`) has CI but no review, and a notes-only one (e.g. HANDOFF and the
  log) has neither since 11-10 (`check.yml` paths); both merge on the user's word, the first once CI is green.
  A fork's PR: read the diff by hand, then the user's word. The repo secret `OPENROUTER_API_KEY` is unused now.

## State now (2026-11-08)
- **Hub on the NAS** since 2026-11-05 (wiki NAS-hub): compose project `clipper-hub` in `~/clipper-hub`, containers
  `serve` (war room on 127.0.0.1:8765, on the tailnet as https://clipper.taile415cd.ts.net/; heartbeat
  `streams/.serve/status.json`), `bot` (`streams/.bot/status.json`) and `worker` (cuts with QSV on the iGPU).
  Code changes go out with `bash deploy/hub/deploy.sh` from a main checkout; `clipper services status|restart ...`
  runs docker compose there (`[serve] hub`). Last deploy 2026-11-16 00:05: main as of #281 (Keys window, held streams). **The config and
  all state are on the NAS:** `//192.168.0.155/docker/clipper` (`/data`) holds `clipper.toml` (the one copy;
  `CLIPPER_CONFIG` on the laptop points at it), `schedule.json`, secrets/, backups/, logs/, faces/, voices/,
  news/, weekly/, archive/, layouts/, people.json, fonts/. The laptop's old copies: `backups/laptop-state-2026-11-05/`.
  The laptop's scheduled tasks are disabled, not removed (rollback: wiki NAS-hub).
- **NAS** (192.168.0.155) also runs the recorder container (`deploy/record`, redeployed 2026-11-17 at 28bc0ab,
  watching kinocasinogaming + donflurgundy + chudlogic), Kitsu :8090, the cut container, the streams and photos
  shares. `kick-138291641-2` is Kino 11-17 after the show ended early (Andy playing a game): recorded only, held
  from the worker by `run.json` `{"status": "skip"}`; delete it whenever you like. The `claude` container (`deploy/claude`, wiki Claude-on-the-NAS) drives clipper from the phone.
- **Mac `lofn`** (Apple Silicon, setup's `control` role, 11-07): a dev/control station that runs nothing
  unattended. Clone `~/clipper`; `CLIPPER_CONFIG=/Volumes/docker/clipper/clipper.toml` and
  `CLIPPER_LOCAL_CONFIG=~/clipper/clipper.local.toml` in `~/.zshrc` (mount the share in Finder first); ssh
  alias `nas` (user **`Brandon`**, capital B). `doctor --role control` clean; Homebrew ffmpeg has no libass.
- **Shows:** kino and c404 live through review → schedule; `chud` (ChudLogic, since 11-04: daily ~10 h from
  ~00:00-02:00 PT, budget segment 3 / short 5 per 8 h, 8-Bit Guy moments ranked up, cam panels `focus = "face"`
  since 11-07). His 10-18 VOD `kick-132975332` is fetched and transcribed only: its timestamps jump back
  821.7 s before 7:28, so don't `clipper run` it until fetch is fixed. dsp and blade are config-only tests;
  `trish` is a placeholder for the 2026-10-27 one-off; trish and blade are off the war room (`[warroom] hide_shows`).
- **Schedule** (October check-in, 11-02): `stream_budget = { segment = 10, short = 10, compilation = 0 }` per
  8 h, Channel 404 `segment = 6`; slots a day: 9 Shorts (09/11/13/15/16/18/19/20/21), 6 segments
  (08/10/12/14/17/22), compilation 23:00 for big-story nights only (`docs/big-story.toml` into the stream's
  config.toml). If good items keep going stale while streams overlap, try `stale_days = 4`.
- **Chud overnight test (11-12 to 11-20, user):** chud's segment budget is 0 since 11-10 (the user stopped
  recording him and gave his slots to Kino and Don), which left 71 approved segments out. All 71 are pinned in Kitsu hourly
  23:00-07:00, Fri 11-13 00:00 through Fri 11-20 07:00 (9 a night, no compilations); the hub uploads them 48 h
  ahead. For it, in `clipper.toml`: `[schedule] max_per_day` 24 (was 15), `[youtube] max_per_24h` 26 (was 15),
  `[shows.chud.youtube] captions = false` (quota: captions are 400 units, ~7 extra a day fit). **After 11-20**
  (the scheduled task `chud-overnight-wrapup` does this Fri 11-20 09:00 and reports; it stops if a pin isn't
  uploaded; the pin list is `backups/chud_overnight_pins_2026-11-12.json`): put both caps back to 15 unless the user keeps them, remove the chud youtube table, set
  `captions_error = "deferred"` on those 71 schedule.json entries (back it up) so the hourly backfill adds the
  captions from spare quota, restart serve. Backup: `backups/clipper.toml.before_chud_overnight_2026-11-12`.
- **Archive:** Jer (The Quartering) 11,571 items / 2,732 h transcribed on Modal, FTS index built, `archive ask`
  works; link checks built but not yet run against the real services. #44 Jerry Banfield, #45 Papa Meat and
  #47 Ben Avery wait on the desktop install and permissions.
- **Stories:** the user's catch-up format for any drama: `story new` → `story run` → `story plan` →
  `story cut --no-review` (never sent to review, so never uploads), a <30 MB 720p copy to the phone; narrated by
  Eric (`[story] voice_id`). First: kevin-langue-show-drama (Kino show, 5:28). Check lead-ins against the
  dossier (one stated a self-claimed role as fact). The DSP story (11-04) went to Kitsu for review instead.
- **Receipts for Don:** first video private at https://youtu.be/q6gemx2kKMs (2026-10-26); sending it is the
  user's call. On-screen source links would need a re-cut.
- **Weekly:** issue 04 (Nov 1-7, Jer cover) in `weekly/2026-11-07/`. Issue 05 (Nov 8-14, "Lindsay's Cruise
  Hell!", the user's pick) built 2026-11-15 in `weekly/2026-11-14/`, $0.78; the cover is a real teary
  close-up of her from Wed's stream (the user wanted "very unflattering", crying), pinned by hand (log
  11-15). Posted to YouTube 2026-11-15 as ONE image post of all ten square pages (Posts take 10 images now;
  https://www.youtube.com/post/Ugkxr4rSrdhyE8_O5oKIz9pBoc-0k5t12ADH, the two captions joined). TikTok and the X
  thread not yet. The faces stage still mislabels
  (her auto pick was a Disney performer from her video). Issue 06 is Saturday 2026-11-21.
- **The Udder Truth:** storyboard complete, voice and pipeline chosen; next the newsroom set (Brass Eye refs in
  studio/mockups/udder_truth/refs/), new desk plates, then Moo's WAN renders (~1,550 Runway credits).
- **Costs and analytics:** October ~$200 in model/GPU spend, ~$184 of it the Jer archive; Haiku scoring
  $0.67 in Oct-Nov, the spend is segments, thumbs, meta. `clipper costs`, war room Costs, `--providers`
  (Claude's own count needs `ANTHROPIC_ADMIN_KEY`, not set). Monthly analytics check-in task on the 1st at
  09:00; October's (`reports/analytics/2026-10-checkin.md`): 1-4 taken, 5 skipped, its Decisions section is what
  November's follows up. October: 12.7k views, 68.5 h watch, +28 subs, Shorts 80% of views.
- **Model providers (#72, closed 11-09; its edit-plan proof moved to #67):** the baseline stays Haiku + Sonnet. Tools: `clipper compare score <stream> --model
  openrouter:<vendor>/<model> --dry-run`, `clipper models test <model>` (conformance), `clipper models gold
  propose|confirm|run` (the user's 66-window answer key, `logs/model_tests/gold.json`); OpenRouter key in
  secrets/openrouter_secret.txt, no OpenAI/Gemini key, no Ollama. Findings (wiki Model-providers, log 11-04/05):
  no model near the baseline; on the gold set v7 Haiku 39/66, DeepSeek V4 Pro 48/66 (27 cents a run, no image
  input), Qwen 3.8 Flash 40/66; Haiku over-calls needs_context, every model keeps 5-11 of the 21 non-keeps.
  Moving a stage (e.g. Pass A to DeepSeek V4 Pro) is a config change whenever the user wants it.

## Waiting on the user
- **Deploy #299** (Clippa chat: several items on one card, undo, streams by show and day; merged 11-18 after Grok's
  review): the hub runs the old chat until `bash deploy/hub/deploy.sh`. Then send a few real messages ("approve all
  comps", "approve s02 from kino 11-14", "undo that", "whats waiting"): chat prompt v3 has only been tried against a
  scripted stand-in for Haiku, not the model itself (about $0.0005 a message).
- A cloud environment named "clipper" (11-18) holds an OpenRouter key so cloud sessions can run Grok reviews; it
  was made for a Haiku test that cloud sessions can't do (Anthropic keys can't be saved there). Keep or delete it.
- **Deploy #293** (`clipper fetch` of Twitch and YouTube VODs, merged 11-16 at the user's word, no Copilot review): the
  hub containers run the old code until `bash deploy/hub/deploy.sh`. Still untested: a subscriber-only Twitch
  VOD (the cookies in `secrets/twitch_cookies.txt` are the user's personal account `gadnuck`, a tier 1 sub of
  `nmnader`, whose last stream was 4 April so no VODs are left), a full YouTube video download, and `clipper run`
  on a `twitch-` or `youtube-` stream. A show claims one by listing the channel (`vanillamace`, `donflurgundy`) in `channels`.
- **`clipper streams prune`** stays by hand until you set `[streams] keep_source_days` in clipper.toml
  (111 GB would go at 30 days). The test stream is in `[streams] never_prune` (set 11-13).
- `secrets/youtube_cookies.netscape.txt` on the share is unused since PR #195 and can be deleted.
- Close Fusion and merge the `mockups/udder_truth/` leftovers into `studio/mockups/` (ignored by `/mockups/`).
- `C:\clipper` is back on main and clean (11-14). The fanmag work is on `ccr-8319354b-axsr9v` (pushed,
  "fan-mag paused for the user's next tweaks"). Still check `git branch --show-current` there before branching.
- Branches and worktrees were cleaned up again 11-13: everything merged is gone, plus the old `ccr-*` and
  `claude/clipper-investigate-command-*` branches and the swarm worktree, all at the user's word. Kept on
  purpose: `story-dig` and `jev-lab` (`C:\clipper-jevlab`; it holds `jev-gate`'s commit), both pushed and
  unmerged. `.claude/worktrees/instagram` (`archive/instagram-source`) holds the Instagram-source work
  (`clipper/instagram.py`, its test, 14 files), committed and pushed 11-13 (b270e5c), unmerged; its base is
  main as of #215, so rebase before a PR. `.claude/worktrees/elated-snyder-52f189` is clean (detached on main) and can go once no
  session uses it.
- The DSP story video is in Kitsu for review (Kino Casino / "2026-11-04 story-dsp-credit-score-590", 3:14).
- TikTok drafts to post by hand: the 10/25 batch (kick-133940023 v04_t04, v05_t05, v07_t14, v08_t18) would
  need `--resend` once there is room.
- Approved-and-scheduled Shorts that a scene re-render would change (v06_w11, v03_w06, v03_t23) are left
  alone until the user says. The four Shorts from kick-136292920 went up with Chud's old cam crop; they stay
  that way unless deleted and re-uploaded.
- Content ID: check new pre-show/one-off uploads in Studio (video → Copyright; the API only sees region blocks).
- Shorts "related video" (Studio only): a weekly scheduled Claude task sets them Sundays 10:00 while the app
  is open (`clipper-weekly-related-videos`; 11-15: 12 set by hand in the session, 0 left due; the pane's Google
  sign-in opens Studio on the byvfx channel: switch to Lolcow Cinema or every Short's page errors; the task
  sets each Short with one in-page script and stops on a sign-in page or the wrong channel). `clipper upload --related` lists
  what's due and records what you set; six Shorts match no scheduled segment (pick one by hand or leave them).
  Verify whether `thumbnails.set` works on Shorts before turning on `[youtube] short_thumbnails`.
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
- `clipper setup` (#78) phase 1 passed in Windows Sandbox (2026-10-28). Next: the real install on the desktop
  (#46; clone into a short folder like `C:\clipper`, run `deploy\bootstrap.ps1`), then the MacBook as #78's join-mode test (#7 closed into #78, 11-13).
- Weekly issue 04: post it by hand from Discord `#weekly`; the X thread goes out by the scheduled task
  (State now). The bot has full permissions since 11-02 (could be cut back to Send Messages + Attach Files).
- `ANTHROPIC_ADMIN_KEY` for Claude's own usage count on Costs, if wanted.

## Open work
**Priority labels (user, 2026-11-13):** every open issue carries one: `p1-now` (built 11-13, one PR each, all merged
and deployed 11-13: #199 PR #247, #204 PR #248, #228 PR #249,
#205 PR #250; #201, #203 and #206 closed; 11-13 also closed #7 into #78 and #57 as built), `p2-dated` (#192 after 11-16), `p3-track` (one big track at a time, the user picks: desktop install #46 → #44/#47/#45/#78;
Udder Truth #58-#60; archive tooling #221-#227 before the next mini-doc; b-roll #229), `waiting` (#80, #48, #45),
`parked` (the rest). `gh issue list --label p1-now` is the queue. **Milestones (user, 11-16)** group the issues
that move together, a finish line each: "Runs itself" (#259, #258, #287; due 12-06, a first guess), "Second
channel (Sloth Beat)" (#46, #78, #44, #47, #45), "The Udder Truth: episode 1" (#58-#60), "Next mini-doc toolkit"
(#221-#227, #229, #254); only the track in play gets a date; parked and waiting issues have none, nor do #111,
#158, #192, #279, #286. A new issue gets a priority label, and a milestone when it belongs to one. 11-16 labels:
#259 and #258 `p2-dated`, #254 `p3-track`, #111 parked → `p3-track`, #158 parked → `p2-dated` (both in work). 11-13: #13 folded into #158, #200 closed (its
setup/doctor bug moved to #78), #70 narrowed to punch-ins/emphasis (tighten built). 11-15 (user): #41 and #55 closed as built, #67-#71
folded into #66 (each a section there).
Tracked as GitHub issues (byvfx/clipper); roadmaps are issues only: (NAS hub #14 and self-checks #185
closed 11-09; checkup's turn-on steps are under Checkup below),
Posts blind items #36 (the rest of the YouTube Posts roadmap #30 closed 11-09), Instagram Reels #39, lore-order playlists #43,
archive fun #63, transcript-framed edits #66 (one issue since 11-15, sections #67-#71; 11-09 the user wasn't sure #67 is needed, so #70's tighten
came first: built 11-09, PR #191, `[edit] tighten`. The user found it "better than I thought" on the
test stream but wants it **off** until it clearly helps; turn it on per show (`[shows.<key>.edit]`)
only at their word. #67's model edit plan waits until edited Shorts prove themselves), crisis mode #73, setup #78,
live clips #81, blind items #111 (steps 1-2 of 4 built and live 11-15/16, below), investigate anyone #138, agent exploration #158 (11-15 spec comment: the Clippa
messenger. v1 merged (PR #285) and deployed to the hub 11-16: `/chat` on serve's page, wiki
Clippa-messenger, `clipper/catalog.py` + `clipper/chat.py`, `[chat]`, Haiku ~$0.0005 a message, ledger `chat`; chats in
`[paths] chats` on the share; four looks in `web/themes/`, `[chat] theme`. In the war room (PR #290, merged and deployed 11-17):
`/chat` docked at the right in a frame (Chat button / M, a Chat tab on a phone) and the looks dress the whole war
room, one pick for both (`theme.js`; `themes/warroom.css` is the default and the only home of the war room's colours).
Renaming a video that's up (user, 11-16: Clippa "really need[s] to be the point of control"): PR #291 (merged and deployed 11-17): `youtube.retitle`
behind `actions.edit_meta`, so every title edit of a video that's up (war room, bot, chat, CLI, undo) renames it on
YouTube; chat prompt v2; YouTube's other edits (description, cover, privacy) still Kitsu-only / Studio. PR #299 (merged
11-18, not deployed) after "approve all comps" came back as a list: the `bulk` tool (several items on one card, one
undo entry), `undo`, streams named "kino 11-14" / "the kino one" / "from last night" (`catalog.stream_named`,
`stream_span`, `ShowNames`), "what's waiting" listing items, a typed yes/no for the one fresh write card, prompt v3.
Next, a week of phone use; #300 (access levels and a local Jev-like classifier in front of the model, a lab first);
then a small `clipper mcp`; #13 may fold into it), Modal ideas #53, #56,
Udder Truth #58/#59/#60, P&L #50 (revenue once monetized). Write "#77 L6" rather than "fix #77" in commit
messages: GitHub closes the issue on "fix".
- **War room heat graph (11-16, user asked "can we see the heat graph of a stream in the war room?"):** PR #294,
  merged 11-16 after Grok's review, **not deployed** (it needs `deploy/hub/deploy.sh` from a checkout of main). 🔥 heat on a stream (Pipeline panel) or in an item's
  window: the signals curve, the windows, and each cut item where it sits, coloured by review state, plus the twelve
  hottest windows and what was cut from each (`/api/heat`, `clipper/warroom/heat.py`, `web/js/heat.js`, wiki
  War-room#heat). It is also the demo video's "how it finds moments" shot. Not yet seen on the live page or on a real phone.
  Copilot has reviewed no PR since #285 (11-16; a request by hand leaves none): Grok is the backup (CLAUDE.md,
  11-18): offer `clipper codereview --pr N` (about $0.80) per PR as soon as CI is green.
- **Review pre-flight checks and approval by silence (#259, 11-14):** step A, `clipper review check` (wiki
  Review#pre-flight-checks), is PR #263, merged and deployed 11-14; the webcam panels are marked `cam = true`
  in clipper.toml (backup `backups/clipper.toml.before_cam_marks_2026-11-14`). Step B, `clipper review auto`
  (`clipper/review_auto.py`, serve's `auto` job every 10 min, wiki Review#approval-by-silence), is PR #265,
  merged and deployed 11-14 (Copilot's 9 findings fixed, and 5 of a second local review's 7; Copilot
  didn't see the two fix commits). Stale findings (PR #270, deployed 11-15): the auto job re-checks, at
  most hourly per stream (`recheck.json`), items held only by their findings. Hardening (11-15, PR #273,
  merged and deployed 11-15): the approval's Kitsu calls run under a 25 s deadline
  (`review.kitsu_deadline`), the re-read after the lock; a lost answer Kitsu took counts as automatic
  (`claimed_at`); every check run but a publish's writes the Kitsu Checks column (never read as a
  person's edit); shows without auto get the hourly recheck too (vision only when an item's inputs
  changed); items gone from review aren't re-asked. Left on purpose: a response that keeps trickling
  isn't cut by the deadline, and a person approving in Kitsu's own page right after a lost answer
  would count as automatic. **On for Kino since 11-15** (user): `[shows.kino.review] auto_approve = true` in the
  NAS clipper.toml (backup `backups/clipper.toml.before_kino_auto_approve_2026-11-15`), serve restarted, its
  `auto` job in the heartbeat; at turn-on Kino had no open Shorts (11 segments, 5 compilations, which wait).
  Next, after a few days of Kino with nothing undone (checkup's `auto` check, `clipper review auto --dry-run`):
  Channel 404's Shorts, then close #259. Segments: #258 is merged and deployed (PRs #260-#262, the worker
  scores each stream's segments). Segment floor backtest (11-15, no model calls): 527 of 529 reviewed
  segments were approved, so the floor is set by 7-day views instead. Scores 1-3 clearly
  underperform (10 of 40 uploads above the show's median views), 4-5 are a coin flip, 6+ clearly do
  better. Suggested `auto_min_segment = 4`: on Kino it holds 63 of 183 scored segments (~6 a stream), on
  Channel 404 23 of 106 (~3). Not set: after the Shorts, at the user's word. Shorts backtest: 105 of 181 approved Shorts would have approved
  themselves, and 1 of 4 rejected ones ("too much Jer", a taste call the window is for).
- **Demo videos (11-16, user):** a portfolio piece first (about 5:40, real shows on screen, none named in the
  narration, the user reads it live; it goes on the byvfx site, linked from GitHub), then an install tutorial.
  The script (ten takes, not locked) and the capture checklist are one private page:
  https://claude.ai/artifact/W48qW95ZPYkA7AE16F4TEX (ticks kept with the page; its Install day tab is the
  install checklist with each step tied to a tutorial chapter). The "how it finds moments" shot is the war
  room's heat graph (above; merged, wants the hub deploy). Build the piece with `clipper doc`; PR #289
  (merged 11-16 after Grok's review, no deploy needed: `doc` runs on the laptop, pull main there) lets
  `doc voice` take a recording per take from the piece's `vo/read/`. Not tried on a real spoken take or in
  Resolve: the demo is the first. Tutorial (user, 11-16): recorded during the desktop clean install (#46/#78),
  one continuous capture with the microphone on; published after #207.
  **Its intro (11-17, 25 s under the first take):** a console TV in a dark room comes on, static for a second,
  channels change faster and faster while the camera pushes in, black comes up everywhere but the letters of
  "Clippa" (12-15 s), the set cuts off at 0:20 (user: keep it there), plain letters to 0:25. Rough 5 is the
  locked cut. The user finishes it in Fusion and Resolve on a generated moving plate: Resolve project
  `clippa_intro` (24 fps, 1080p), media in `demo-intro/` on the NAS share (`W:\clipper\demo-intro`), bins
  Plate/Screen/Sound/Reference, timeline `intro_v1` (the elements stacked, beat markers) and `intro_v2_comp`,
  where plate and screen clip are one Fusion clip: the picture pinned into the tube on a fitted path, matted
  by the plate's own static (keyed, then grown a few pixels onto the tube's dark edge), the room lit by the picture's mean, a CRT look (soften, phosphor stripes and
  scanlines that grow with the push to about 10 px a cell, a vignette, the tube's own shading, bloom), a finish on everything (`FinalTint`, `FinalTone`,
  `FinalVignette`, `FinalGrain`: cool shadows, less colour, dark corners, light grain), and rough 5's push and fade: the plate retimed and pushed so the
  picture fills the frame at 12.5 s, black up around the letters over the same picture (12-15 s), the letters
  filled with static from 19.5 s, folding to a line at 20 s under the plain letters coming up. The
  sound is the seven generated set sounds cut to the same plan (`stem_set_real_24.wav` and the channels alone, `stem_clips_24_v3.wav`, from `work/sound7.py`; `set_sound_cues.txt` says what is where), placed by measurement and never heard. Picture and letters carry a barrel bulge; from 20.4 to 21.9 s the letters' bulge, soft
  edge, scanlines and glow ease to nothing, leaving flat clean text. Built by script and checked on stills
  only, not played. The sources are in `mockups/demo-intro/` on the laptop (gitignored): `kit/` (the chosen plate
  `plate_move_720_take2_antenna.mp4`, 15 s 720p 24 fps, its still and the tube's track; the channels as a flat
  4:3 clip, the light they throw, the title matte, sound stems, seven set sounds, times; at 24 and 30 fps),
  `work/` (clips, stills and the scripts that re-make the kit: `kit5.py`), `wall.py`, `measure_plate.py`.
  Open: the last 1.78x of the push is a blow-up of the 720p plate (soft bezel just before the picture fills
  the frame); the look's and the finish's amounts are first guesses for the user to judge; nobody has played it or heard the sound; 1080p waits until the 720p cut is right.
  The set is redone (11-17) and the user took the still: `mockups/demo-intro/tv/set_spot_shag_endless.png` (no cigarettes; ordinary green shag, the pile only brushed toward the set; a flat matted sat-in patch; an overhead spotlight; no walls, the shag fades into black; same set and framing as before). Stills are made whole in one pass, never joined from layers (the user saw it at once). Its passes: `tv/aov/endless_spot.png` (tube dead: the opening beat, before the set comes on), `endless_tv.png` (over-adds with the spot pass: mix from the spot pass to the full picture on the set's on-curve instead), `endless_nohaze.png` (`work/aov_test.py`, sheet `work/aov_endless_sheet.jpg`; same scene within 2 px, not pixel-exact). Haze, by the user's rule: its own element over everything, tube picture included, on the no-haze plate, never baked in; `work/haze_dial.py` wrote a still one (`tv/aov/endless_haze_element.png`, previews at 15/30/50%), the level is the user's pick and a moving one built in Fusion is the better end. Mattes: Segment Anything with the user's ComfyUI (`~/Documents/ComfyUI`; `work/matte_test.py`, run with ComfyUI's Python) gives clean tube and cabinet, a rough antenna, no sat-in patch. Depth: nothing installed, a model download waits on the user's OK. The new move is made from the no-haze pass: `kit/plate_move_720_take3_spot.mp4` (15 s, 720p) with its tube track `kit/plate_take3_track_24.csv` (`work/track_take.py`); clean, but a shorter push than take 2 (tube 36% of the width at 12.5 s against 43%), so the comp's path must scale more. The user's one fault with it, the carpet dancing, is fixed by `work/carpet_lock.py`: the still's carpet projected on the floor plane and pinned to the cabinet, with the take's light (`kit/plate_move_720_take3_spot_locked.mp4`, side by side `work/take3_carpet_lock_sbs.mp4`). With the user for a yes. Next, on that yes: `intro_v2_comp` rebuilt on take 3 (path from the new track, the tube key re-fitted), the room gain split into a spot share and a set share with the spot on first, the haze element on top at the user's level, a spotlight sound cue, then the shoot page.
- **Documentaries (11-13, PR #233, merged and deployed):** `clipper doc
  new|voice|build|master|leak|kit` (wiki Documentaries; `clipper/doc.py`, `doc_resolve.py`), the reusable form
  of the first mini-documentary's one-off Resolve scripts. A piece is `[paths] documentaries/<slug>/` on the
  share (plan.toml, banned.txt, media), never the repo. Tried: take processing, word timing, the take
  split and mastering on real files; `build`, the render and `master` against live Resolve 21.2 with a
  synthetic piece (its Resolve project is deleted; the folder `C:\clipper-doc-test` on the laptop is the
  user's to delete). Not tried, because they cost: `voice` against ElevenLabs, `leak` on Modal; `--dry-run`
  first: `voice` prints the characters it would buy (not a price: work it out from the ElevenLabs plan),
  `leak` prints a dollar estimate. Reels at another frame rate than the timeline are untried too. On the
  first real piece, listen to every take and watch the first built timeline. `[story] lufs = -14` is new: a story cut before it is
  rendered again, and its cover picked again, on its next cut.
- **B-roll library #229 (11-12, steps 1-2 of 4, PR #230):** `clipper broll find|add|list|keep|drop`
  (wiki B-roll): NASA, DVIDS (the B-Roll category; keys in `secrets/dvids_secret.txt`, public key first,
  added by the user 11-12), Prelinger and Commons; public domain / CC0 only, no sound by default (NASA
  music). Pixabay stock video too (11-14, branch `broll-pixabay`; same key as clip art, live footage only,
  its licence kept by default). Next: the National Archives (key by email from NARA), `[story] broll` in story cuts, then a war
  room window. The library is `[paths] broll` on the share.
- **Clip art library (11-14, PR #255, merged; no deploy needed):** `clipper clipart find|add|list|keep|drop`
  (wiki Clip-art): Commons (SVG drawings first, photos only when a find wants more), the Met, Cleveland (CC0),
  Iconify emoji/icons (logo sets never), Pixabay (key in `secrets/pixabay_secret.txt` since 11-14, tried:
  works). `add` takes a folder (Kenney packs, `--licence CC0`). The library is `[paths] clipart` on the
  share: 10 popcorn pictures from the first test (the four off-target ones dropped), plus a "Popcorn function"
  maths plot (`commons-c2e98328`) worth dropping. Not yet used by covers or the weekly. Left from review:
  a search for scans ("engraving") gets SVG drawings first; Commons metadata parsing is in broll, cast and
  clipart. Openclipart (account key) and the Library of Congress (20 requests/min) could be sources later.
- **Usability review (11-10):** built and merged 11-13: `clipper status` with "needs you" first, 60 s to 12 s
  on the share (#199, PR #247); `clipper streams list|prune` and `clipper backup`, both also serve jobs (#204,
  PR #248); Modal per-call overhead in the archive/story estimates (#228, PR #249); CLI polish: `latest`,
  `latest:<show>` and id prefixes, `--json` on the read commands, `config diff`, `completion bash|powershell`
  (#205, PR #250; `--quiet`/`--verbose` left out on purpose). War room #201-#203 and #206 closed (#206's
  closing comment lists the three items left on purpose); #200 closed. Still open: new users and the public
  repo #207 (this install's values in tracked files, packaging).
- **Checkup (#185 closed 11-09, the rest is tracked here):** `clipper checkup` (services, streams, schedule,
  lint; `[checkup]`; wiki Checkup) and `clipper alert send` are merged (#189). The nightly run
  (`deploy/claude/checkup.sh`, prompt `checkup.md`, both baked into the image) is not on yet. Left:
  1. The user puts `CHECKUP_AT=08:30` in `deploy/claude/env.local` and redeploys the claude container.
  2. First run on the NAS: check that headless `claude -p` runs on the Remote Control sign-in, and that the
     `--allowedTools` rules work (`checkup.sh --dry-run` first).
  3. A week of nightly reports, then decide on hourly runs and PRs (#158's "act within limits").
  **Self-maintenance (#287, 11-16, `p1-now`, nothing built):** `clipper checkup --fix`, repairs from a short
  allowlist with no model (capped per finding key, a ledger, "fixed 2, need you 1" in the DM), a week of
  would-fix reports before `[checkup] fix = true`; then the planned checks (yt-dlp, cookies and tokens, repeat
  log errors), then PRs from the nightly run. Step 1 is the turn-on above. The worker already takes over a
  stale lock and retries to `max_attempts`, so the repairs start past that.
- **Bootstrap follow-up (11-07):** `deploy/bootstrap.sh` installs Homebrew `ffmpeg`, which has no `subtitles`
  (libass) filter, so a Mac worker fails Short captions; `ffmpeg-full` has it but is keg-only. Pick one:
  install `ffmpeg-full` and point clipper at it, or document it. `deploy/hub/deploy.sh` from `lofn` needs
  `deploy/hub/env.local` (it overwrites the NAS `.env`).
- **Investigate anyone (#138; part 1 `clipper people` merged 11-04, people.json is the record, 23 people
  imported).** Next, in the issue's order: speaker attribution (needs #53 embeddings, new facts prompt) →
  `investigate start` (swarm finds a person's channels and X handles, the user approves, a sample is
  transcribed) → X reads (check the pay-per-use read price first) → Obsidian vault → watchlist → public
  records (CourtListener token) → `investigate eval` on known stories (Huck, DSP) → Good/Noise buttons +
  `tune`. Also a review gate before a lead becomes content, Wayback snapshots, a never-read list (Kiwi Farms).
- **Story/swarm leftovers:** `investigate check` on the swarm (branch `investigate-swarm`, unmerged: the old
  four searches seed a small swarm per lead, then his own titles). YouTube downloads in a story hit the bot
  check from this IP: a fresh cookie export or the Firefox profile. Modal cold starts dominate a story's few
  items (`[story] parallel` 2).
- **Code audits:** 10-28 (maintainability) and 11-03 (correctness, `reports/code-audit-2026-11-03.md`) done
  and merged; still open from 11-03: #15 (example toml commented), #24/#25 (lock edges), #29-#35, #37, #41,
  #46-#50 (lows), #42's lock part. 11-08 (hand-over, `reports/code-audit-2026-11-08.md`): all 20 plan items
  done the same day (PRs #170-#182 and #184; each change is a CHANGELOG.md 11-08 bullet, the log has the PR
  list) and deployed 21:51. Left for later: `tests/test_modules_have_tests.py` lists 13 untested modules (the
  list may only shrink); 17 functions over McCabe 15 carry `# noqa: C901`, split when touched.
- **Weekly and monthly audit (11-10, wiki Audit):** `clipper audit` (no model) DMs how the week went and saves
  `streams/.audit/weekly-<date>.json`; on the 1st a read-only `claude -p` reads the saved weeks, traces repeat
  problems and leaves `deep-<YYYY-MM>.md`. The claude container runs both after the checkup (`audit.sh`), so it
  needs `bash deploy/claude/deploy.sh` once. Since PR #248 the backup settings are `[backup]` (folder
  `backups/taste`, every 7 days by serve), and the digest and doctor read the newest zip there. Dependabot opens grouped weekly PRs (ruff ignored; no review, no secrets). Its security
  alerts are a separate repo switch, off as of 11-10.
- **Blind items #111 (wiki Blind-items), steps 1-2 live since 11-16:** `clipper blinds` collects gossip
  blinds, reads their clues (Haiku, never a name), pairs twins across sources, and you guess first: in the
  war room's **Blinds** window, in Discord, or `clipper blinds answer`. Step 1 PR #277 (store, clue pass,
  pairing), the window PR #278, step 2 PR #280 (serve's check, the bot's questions). The NAS clipper.toml has
  one source, CDAN (Blind Gossip's site answered 500 on 11-15: add it when it's back), `[blinds]
  check_minutes = 30` and `sensitive_alerts = true` ("no censoring for me": the user filters with `hide`);
  backups `backups/clipper.toml.before_blinds_sources_2026-11-15` and `..._alerts_2026-11-16`. The bot made
  a private `#blinds`; `alert = "digest"`: a thread a day at 20:00, plus an alert of its own for each that
  could be about someone followed. Store 11-16: 722 CDAN posts, 551 waiting, 14 unread (the clue pass skips
  some; after 3 tries they wait without clues). Costs: ~$0.0002 a blind. Not built: typed answers in the thread
  (no Message Content intent), a Hide this kind button. **Next: step 3**, investigate after the answer (the
  user's name first, the swarm, the judge's score), then step 4, reveals and the scorecard. The flow
  artifact: https://claude.ai/artifact/1m3obUigUkkA5ZVdmmiLWn
- **News watcher #110:** off since 2026-11-03 at the user's word (`[news] check_minutes = 0`, no terms); only
  `[news] watch` / `[shows.<key>.news] watch` terms are watched, `auto_watch = true` brings back the
  cast/figures/library list; `#news` exists in Discord.
- **Older notes not on an issue:** republish the 2026-10-19 re-cut streams to Kitsu (user said not yet); Blade
  compilation's Discord card never posted (`review notify kick-131151926 --keys comp-blade-in-vegas`); a daily
  "post kit" folder; thumbs: reject a picked frame with no face, `--frame-at <s>`; Discord excerpts for one-offs
  could start after the intro; pick/preshow/retake prompts don't read speaker tags yet.

## Recipes (how we did it last time; details in the log)
- **Restart serve / the bot / the worker:** `clipper services restart serve|bot|worker` (docker compose on the
  NAS over ssh); code changes: `bash deploy/hub/deploy.sh [serve|bot|worker]` from a main checkout (Git Bash).
  A serve restart or deploy kills a running upload: `clipper services status serve` lists its jobs; wait until
  none is running, and until no show is live if the worker is busy.
- **What is going on:** `clipper status` (serve's, the bot's and the recorder's heartbeats, the worker's
  streams, review counts, the next releases, this month's spend; `--sync` reads Kitsu first).
- **Before a model rerun:** `clipper score|segments|meta|thumbs <stream> --dry-run` prices it from the prompts
  it would send; `score` refuses a rerun without `--force`.
- **Upload one item now:** approve in Kitsu, `clipper schedule` (back up schedule.json first), then
  `clipper upload --max 1`; asap items go public at once (serve's hourly runs log to `logs/serve.log`).
- **One-off from a YouTube video:** `clipper --show <key> preshow fetch <url> --from H:MM:SS --about "..."`
  (add a `[shows.<key>]` table with a placeholder channel if the show isn't on Kick), then `clipper run yt-<id>`.
- **Drama catch-up:** `clipper story new "<brief>" [--slug x]` (every step; `story run x --from <step>` redoes
  one) → `story plan x` → `story cut x --no-review`; edit `streams/story-x/story.json` by hand and cut again.
- **Fix a cover by hand:** edit the entry in `thumbnails.json` (frame is 1-based; `t`, `face`, `headline`,
  `text`, inset, band), back it up, `clipper thumbs <id> --ids sNN --compose-only` (attaches in Kitsu);
  `youtube.set_thumbnail` if it's already up. The war room's cover editor does the same.
- **Hand picks:** `clipper pick <id> --find WORD`, then `--from/--to` or `--at` with `--about`, `--dry-run` first.
- **Retake without changing anything:** `clipper retake <id> --item c03 --comment "..." --dry-run`.
- **Related video in Studio by hand:** the field is `#linked-video-editor-link`; click its trigger, search the
  segment's video id, pick the one result, Save; then `clipper upload --related <keys or links>` records it.
- **TikTok by hand:** `clipper tiktok --items <keys> [--resend]`; captions land in Discord #tiktok.
- **NAS recorder deploy:** `bash deploy/record/deploy.sh`, only when nothing is live (it refuses otherwise).
- **Per-stream overrides** (a big night): `streams/<id>/config.toml` (`[schedule] priority = true`,
  `kick_first_hours = 0`, more picks). `CLIPPER_SCHEDULE_START=<date> clipper schedule --reshuffle` refills today.
- **Checks:** `clipper doctor --role gpu --offline`, `uvx ruff@0.17.3 check clipper tests`,
  `.venv/Scripts/python.exe -m pytest -q`, test stream `kick-131151926`.
- **Clean-Windows test of the install (#78):** `git archive HEAD` into a folder, a `.wsb` that maps it
  read-only plus an answers file and a writable out folder, and a LogonCommand script that copies the code
  to `C:\clipper`, runs `deploy\bootstrap.ps1 --answers ... --offline`, then doctor and `setup --check`
  into the out folder. No keys go in. Launch the `.wsb`, wait for a DONE file (~1 min), close the Sandbox.
- **Clean-Linux test of bootstrap.sh:** Docker Desktop on the laptop (start it; restart it if the engine
  returns 500s), `ubuntu:24.04` with the archive, answers and out folder mounted; as root install sudo +
  curl, `rm -rf /var/lib/apt/lists/*`, add a sudo user, then `su - user -c` a script that runs
  `bash deploy/bootstrap.sh --answers ... --offline` and `bash -ic 'clipper doctor ...'` (a new terminal).
  ~5 min (ffmpeg from apt). The font EULA goes unanswered there. Then the by-hand wiki Install-checklist.

## Back up the taste layer (user, 2026-10-24)
The Python is replaceable; the months of editorial judgement are in prompts and config. Treat them
like footage. `clipper backup` (PR #248, #204; wiki Backups) zips the list below into `backups/taste/`
(about 17 MB, 390 files; the newest 8 kept; secrets only with `--with-secrets`), and serve makes one every 7 days. The first was made by hand 11-13 (`backups/taste/taste-2026-11-13-1707.zip`,
no secrets). Copy one off the NAS now and then. Not in it: `deploy/*/env.local` and the Kitsu volumes.
- **In git (push it):** `clipper/prompts/` (every version, never edit one that produced reviewed output),
  `clipper.example.toml`, `studio/`, `reports/`, this file.
- **Not in git, copy by hand or script** (all in the NAS folder since 2026-11-05): `clipper.toml` (show tables,
  house_style, stream_context, scoring rules) and `backups/` (`clipper.before_*`, `schedule.before_*`: hand-edit
  backups go there, not the repo root), each `streams/<id>/config.toml` (lost with the stream folder), `secrets/`
  (YouTube/TikTok tokens are a re-consent to recreate), `faces/known.json` (enrolled faces), the photos share
  (`photos/`, cast.json), `layouts/` (Shorts layout references and per-show `enquirer.toml`), `people.json`,
  `schedule.json`, `deploy/*/env.local`, and the Kitsu volumes on the NAS (every review decision and note).
- Also the project memory `~/.claude/projects/C--clipper/memory/` on the laptop: it holds decisions
  that never made it into HANDOFF.

## Gotchas learned
- Deploys from PowerShell: plain `bash` is WSL's (broken: "getpwuid(0) failed", "Failed to translate"). Use
  `& "C:\Program Files\Git\bin\bash.exe" deploy/hub/deploy.sh`; the user forgets, so always give that form.
- The claude container: its clone sits on a mount where every file is executable, so ruff there needs
  `--extend-ignore EXE002`; its GitHub token can't read checks or Actions (403), so the user confirms CI on the
  PR. Relative config paths outside `[paths]` resolve against the config's folder (hub /data, container /app).
- New Windows installs: keep the folder short (anthropic's files sit ~160 characters deep in .venv; past 260
  imports fail with long paths off); `tzdata` must be a direct dependency (Windows has no zone database).
- Kick: yt-dlp VOD extractor 404s; resolve via the channel videos API (kick.py). Chat history API ~2 req/s,
  429 with Retry-After: 0 → adaptive limiter.
- Stream-copy cuts must start on a keyframe (every ~4 s) or audio drifts; `.ts` seeking for re-encodes needs
  keyframe seek + output trim. ffmpeg backs input seeks off by 3/23 s when video has B-frames: `.ts` lands on
  the keyframe at/after the target, `.mkv` (NAS recorder) at/before it, so `copy_cut` seeks k+0.005 for .ts
  and k+0.15 otherwise, and fails the cut on a sync gap or wrong length. Each compilation part ends with a
  concat `outpoint` where the next file begins, or joins replay 1-5 s (the H3 video, 10-28).
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
  Testing limit; the user signed in again 11-02, so the token shouldn't lapse). `clipper doctor` (online)
  refreshes it to test it; `upload --sign-in` keeps the analytics scope.
- YouTube bot-checks this IP for yt-dlp: pre-show, one-off and story grabs retry signed in through the
  dedicated Firefox profile (`[youtube] cookies_from_browser = "firefox:clipper"`, doctor checks it is signed
  in) or secrets/youtube_cookies.txt (browsers rotate exports: re-export when "cookies are no longer valid").
  A pre-show stuck on the bot check DMs once per video. A PO-token provider doesn't get past it. The hub
  containers have no browser (compose empties the setting), so since the 11-05 move they read the file; the
  laptop's `clipper-cookies` task refreshes it (wiki Archives#keeping-the-nas-hubs-cookies-fresh).
- Kino's pre-shows are on the main channel @TheKinoCasino since 11-04 (`clipper.toml` since 11-06). That
  feed's RSS 404s from the NAS, so the check reads yt-dlp's undated listing: entries below a grabbed pre-show
  count as older (#162). After a channel switch, grab the latest by hand.
- A cut killed half way can leave `clipper-cut-<id>` running on the NAS: `ssh nas docker rm -f clipper-cut-<id>`.
- `uv sync` can't replace a running clipper.exe (`uv pip install` instead, and include `--extra modal`
  later). yt-dlp needs deno for YouTube.
- The worker runs the code of the last hub deploy, not the checkout: a merged change reaches the containers
  only with `bash deploy/hub/deploy.sh`.
- Modal per-run cost estimates overcount parallel archive runs; trust the billing line in `clipper modal`.
