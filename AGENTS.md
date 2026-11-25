# clipper: notes for Claude Code

Read HANDOFF.md first: the current state between sessions (what runs where, what the user
decided, what is waiting on them, open work, recipes, gotchas), kept under ~300 lines. The
session diary is `docs/log/<YYYY-MM>.md`: grep it for history, don't read it whole. The user
docs are the site at <https://byvfx.github.io/clippa-docs/>, built from `docs/wiki/` (one flat page per
file, `Home.md` first, `_Sidebar.md` lists every page). The GitHub wiki mirrors those pages
(`.github/workflows/wiki.yml` on every push to main) and points readers at the site; edit the pages
in the repo, never in the wiki itself. A `<!-- wiki-only -->` ... `<!-- /wiki-only -->` block stays
on the wiki and is dropped by the site build (`docs_site/convert.py`; `mkdocs.yml`, `docs_site/`).
`mkdocs build --strict` is in CI. `byvfx/clippa-docs` publishes the site from this repo's main.
Don't edit a generated `docs-site/*.md`. Link pages wiki-style (`[Faces](Faces)`,
`[x](War-room#faces)`); `tests/test_wiki.py` checks the links and the sidebar. Messages link a page with
`wiki.page("Name", "heading")` (`clippa/wiki.py`; the test checks those too). README.md is a
short pointer and SETUP.md maps its old sections to wiki pages. At the end of a session update HANDOFF.md in place (no diaries there) and add a dated
entry at the end of the month's log; keep the wiki pages true whenever behaviour changes, and add a bullet to CHANGELOG.md's Unreleased for a change a user would notice (wiki Developing#changelog), in
the same PR as the change.

## Shape

- Every pipeline stage is a `clippa <stage>` subcommand, wired in `clippa/cli.py`,
  one module per stage in `clippa/`. New functionality is a subcommand or a flag on one,
  never a loose script. A new command's parser goes in its group's `add_<group>` in cli.py
  and its name in `COMMAND_GROUPS` (a test checks); a new pipeline stage is also a row in
  `clippa/stages.py`, which the worker and `clippa run` read.
- A stream is `streams/<id>/`; stages read and write files there and are idempotent, so
  `clippa run` can skip what is done. Review state is `review.json`, retakes `retakes.json`.
  JSON files go through `clippa/jsonstate.py`: `load`/`read` (retry a file mid-replace), `read_or`
  (a default for a missing or broken file: services and the page only),
  `write` (tmp + replace), `update`/`merge` (under a lock). A test fails on a raw
  `json.loads(path.read_text())`.
- Config: defaults in `clippa/defaults.toml` (commented; `config.py` loads it as `DEFAULTS`, the
  None defaults listed in `config.NONE_DEFAULTS`) < `clipper.toml` < `clipper.local.toml` (this machine's
  own values, written by `clippa setup`) < `CLIPPER_<SECTION>_<KEY>` env; the last two also win
  over show and stream overrides.
  `config.for_stream(cfg, sdir)` resolves the show from the stream's `meta.json` channel,
  then the stream's own `streams/<id>/config.toml` if any; use it rather than the base config
  whenever a stream is known. `config.base(cfg)` is the config before any override (the old `_root`).
  A two-letter section alias (`sc`, `rc`, `wc`) only in a module that reads one section; elsewhere
  spell the section out, since `sc` is scoring in one file and story or schedule in the next.
- Shows: `[shows.<key>]` tables; a show's sub-tables override base sections. Each show has
  its own Kitsu production. Everything uploads to one YouTube channel.
- Model calls go through `clippa/llm.py` (`llm.Claude` is an alias of `LLM`); the provider is the
  model string's prefix (`openai:`, `gemini:`, `local:`, bare = Claude). Don't call an SDK directly.
- Errors: library code raises `ClipperError` (`clippa/errors.py`) with a one-line message, never
  `SystemExit`; only `cli.main` turns it into an exit. The bot, war room and worker catch it
  like any exception. A test fails on a new `raise SystemExit` outside cli.py.
- Prompts are `clippa/prompts/<name>_<version>.md`, inside the package (`clippa/prompts/__init__.py` loads them;
  `[paths] prompts` adds a folder of your own, read first); the version in use is a config key, and
  `prompts.VERSIONS` says which one for each prompt (doctor and a test read it; a stage never assigns a version by
  hand). Add a new version rather than editing one that produced reviewed output.
- Stages print, services (serve, bot, worker, recorder) log through `logging`. How the processes share
  state (locks, heartbeats, the DM outbox, serve's API) is the wiki page How-the-processes-talk; the
  six-step checklist for a new command is in Developing.

## Rules

- No machine-specific values in `clippa/defaults.toml`: no IPs, hostnames, user ids, local
  paths, nothing of this install's (a test checks for its names). They go in `clipper.toml`
  (gitignored) or env vars, with a placeholder in `clipper.example.toml` (the starter).
  `clippa doctor` should fail with a fix when a required one is unset.
- Secrets are env vars (`ANTHROPIC_API_KEY`, `KITSU_TOKEN`, `DISCORD_BOT_TOKEN`,
  `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`, `ELEVENLABS_API_KEY`, `BRAVE_API_KEY` for cast image
  search, `DVIDS_API_KEY` for b-roll (or `secrets/dvids_secret.txt`, public key first), `PIXABAY_API_KEY` for clip art and b-roll (or `secrets/pixabay_secret.txt`), and `OPENAI_API_KEY` / `GEMINI_API_KEY` / `OPENROUTER_API_KEY` if a stage uses those providers) or files
  under `secrets/`. Never write them into tracked files, logs, or messages.
- Anything a new check or install step needs belongs in `clippa doctor` and its wiki page, not
  only in a chat answer (and in `clippa setup`, `clippa/setup.py`, when a new user would
  have to do it).
- Kitsu is the record for review decisions. Bot buttons and the war room act through
  `clippa/actions.py`; do not write decisions anywhere else.
- Uploads are automatic since 2026-10-20: `clippa serve` replans and runs `clippa upload` every
  `[youtube] auto_upload_minutes` (60 here; the default 0 means only by hand), private with publishAt, at most `max_per_24h`. Do not run
  `clippa upload` or `clippa review publish` by hand without being asked.
- Generated titles, descriptions and thumbnail text should avoid the word "hosts"; the
  user finds it reads as machine-written.

## Running things here

- Windows dev machine: the venv is `.venv` (uv). `clippa ...` works where the bootstrap's command
  in `~/.local/bin` is on PATH (a shell started before it may not have it); `.venv/Scripts/clipper.exe ...`
  always does. There is no `python` on PATH outside the venv.
- Keys are Windows *user* environment variables, which agent shells do not inherit. Load
  them first, for example in PowerShell:
  `$env:KITSU_TOKEN = [Environment]::GetEnvironmentVariable("KITSU_TOKEN","User")`.
- The config and all state live on the NAS since 2026-11-05 (wiki NAS-hub): `clipper.toml`,
  `schedule.json`, `secrets/`, `logs/`, `faces/` etc. are in `//192.168.0.155/docker/clipper`
  (`/data` in the containers), not in the repo. The user variable `CLIPPER_CONFIG` points there,
  and clipper reads it from the registry when a shell didn't inherit it; a `./clipper.toml` in
  the checkout would win, so don't make one.
- `clippa serve` (the war room and every background job), the Discord bot and the worker run
  as containers on the NAS (`deploy/hub`, compose project `clippa-hub`), not on the laptop.
  `clippa services status|stop|restart serve|bot|worker|all` runs `docker compose` there over
  ssh (`[serve] hub`). Code changes reach them with `bash deploy/hub/deploy.sh` from a checkout
  of main (Git Bash): that replaces restarting serve or the bot, and never touches the recorder.
  The laptop's worker task (`deploy/worker`) is disabled; never run serve, the bot or the worker
  on the laptop alongside the containers (their single-instance locks don't see each other). The
  serve and bot task scripts are gone (2026-11-08): compose (`deploy/hub`) is the one way to run
  them unattended, on any Docker host.
- The NAS recorder is deployed with `bash deploy/record/deploy.sh`, only when no show is live:
  the restart starts a new recording part. The script refuses while `clippa record status`
  shows a recording (exit 3); `--force` overrides it.
- Model calls cost money. Say what a rerun of `score`, `segments`, `meta` or `thumbs` will
  cost before doing it on a long stream. `score` refuses a rerun without `--force` (`clippa run
  --from` passes it), `cut` keeps picks whose in/out points are unchanged, `thumbs` keeps a cover
  whose cut is unchanged, and `weekly --render` never drafts.
- Before a hand edit of `schedule.json` or `clipper.toml` (both on the NAS share), copy it to
  `backups/<file>.before_<what>_<date>` beside it, not the repo root.

## Running on the NAS

When the working directory is `/app` on Linux you are the `claude` container on the NAS
(`deploy/claude`, wiki Claude-on-the-NAS), usually driven from the user's phone; the
Windows notes above don't apply.

- `clippa` is on PATH (the clone's venv, `/app/.venv`); no `.venv/Scripts`, no `uv run`. The
  keys are already in the environment; the config is `/data/clipper.toml` and the streams
  `/data/streams`, the same files serve, the bot and the worker use.
- `/app` is your own clone: work on a branch, commit, push and open PRs with `gh` as on the
  laptop. After pulling a change to `pyproject.toml` or `uv.lock`, run
  `uv sync --frozen --extra modal --extra faces --extra discord` (it is safe here).
- You can't reach Docker or ssh: no `clippa services`, no deploys. serve, the bot and the
  worker run the code of the last hub deploy, not your clone's; a merged change reaches them
  when the user runs `bash deploy/hub/deploy.sh` from the laptop. Say so when it matters.
- The worker is processing streams at the same time: leave a stream whose `.run.lock`
  heartbeat is fresh (`run.json`) alone. The NAS has 6 CPU threads shared with the recorder:
  transcription goes to Modal, encodes use QSV (`/dev/dri`), and nothing heavy runs while a
  show is live (`clippa record status`).
- The user reads on a phone: keep replies short, give the cost before any model rerun and
  wait for a yes, and never deploy or restart the recorder from here.
- Memory is the laptop's Hindsight bank, through the same plugin (wiki Claude-on-the-NAS#memory):
  search its pages before acting, as the session banner says; the session is written back by
  itself. Save a decision of the user's with `hindsight_ingest_document`, not in
  `~/.claude/projects/-app/memory` (kept as a fallback for when Hindsight is down).

## Checks

- `clippa doctor --role gpu --offline` after changing config, ffmpeg, fonts or dependencies.
- Try risky changes on the test stream `kick-131151926` before a real one.
- Before committing: `uvx ruff@0.17.3 check clippa tests docs_site` and `.venv/Scripts/python.exe -m pytest -q`.
  The rules are ruff's defaults plus the few in pyproject's `[tool.ruff.lint]` (import order,
  bugbear, pyupgrade, blind excepts, stale noqa, McCabe over 15), pinned to the version in
  `required-version`; bump the pin there and in the dev group together. Tests are offline and
  take about 25 s; new pure logic gets a test in `tests/`. CI runs both on every push, on Linux
  and Windows (`.github/workflows/check.yml`), except one that changes only notes no test reads
  (root `*.md` but for `CHANGELOG.md`, `docs/log/`, `docs/archive/`, `reports/`, `studio/` but for its `streamers.json`):
  that PR shows no checks.
  A PR that changes code (`clippa/`, `tests/`, `deploy/`, `.github/`, `pyproject.toml`) gets two
  reviews (user, 2026-11-13): yours first, the
  `code-review` skill at high effort on the branch against main before the first push, findings
  fixed before the PR opens; then Copilot's, which a repo ruleset requests when the PR opens.
  Copilot doesn't review later pushes on its own: run `code-review` again on what each push adds
  (or ask Copilot with `gh pr edit N --add-reviewer @copilot` for a big one). **While Copilot is
  out of credits (user, 2026-11-20: Grok the default "for a few more days"), the `review` workflow
  is back** (`.github/workflows/review.yml`): on each push it waits up to 600 s for Copilot, then
  runs Grok (`clippa codereview --pr N`, ~$0.80-1, ~25 min, the repo secret `OPENROUTER_API_KEY`)
  and posts on the PR; it skips a commit Copilot reviewed, so it steps aside when the credits are
  back. Wait for its comment instead of offering Grok by hand; delete the workflow once Copilot is
  reliable again. By hand (`clippa codereview --pr N`) only when the workflow's review failed or
  didn't run. Merge once Copilot's or Grok's review is in,
  each finding checked against the code, handled and shown to the user, and CI is green. Green CI
  alone isn't enough. A PR that touches none of those (the wiki, `uv.lock` alone,
  `clipper.example.toml`, `streamers.json`, notes) or comes from Dependabot needs no review: merge on
  green CI where it runs and the user's word. A fork's PR: read its diff yourself, tell the user
  what you found, and merge only on their word.
  Avoid `uv run`/`uv sync` while serve or the bot is up: they re-sync and cannot replace the running
  clipper.exe (`uv pip install --python .venv/Scripts/python.exe pytest` installs pytest alone).
