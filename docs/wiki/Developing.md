Working on Clippa itself. `CLAUDE.md` in the repo has the full set of rules for code sessions;
this page is the short version.

## Layout

```
clippa/              the package, one module per stage, cli.py wires them
clippa/prompts/      the prompt files, versioned, shipped inside the package; the version in use is set in clipper.toml
deploy/               bootstrap (new machine); compose stacks for the NAS hub (serve, bot, worker), the recorder, Kitsu and Claude; Windows task scripts for a GPU worker
streams/<id>/         one folder per stream: source, transcript, cuts, metadata, review state
clippa/defaults.toml every setting with its default, documented; config.py loads it (clipper.example.toml is a starter clipper.toml)
tests/                offline tests (pytest); CI runs them and ruff on every push
docs/wiki/            these pages; pushed to the wiki on every push to main
HANDOFF.md            current state between work sessions (short); docs/log/ has the dated diary
studio/               the studio's working files: mockups, research notes, media-heavy reports (not the package)
reports/              the monthly analytics reports (the old audits are in docs/archive/)
```

## Shape

- Every pipeline stage is a `clippa <stage>` subcommand, wired in `clippa/cli.py`, one module
  per stage. A new pipeline stage is also a row in `clippa/stages.py`, which the worker and
  `clippa run` read.
- Stages read and write files in `streams/<id>/` and are idempotent. JSON state goes through
  `clippa/jsonstate.py`.
- Config: defaults in `clippa/config.py` < `clipper.toml` < `clipper.local.toml` < `CLIPPER_*`
  env ([Configuration](Configuration)). Use `config.for_stream(cfg, sdir)` whenever a stream is known; `config.base(cfg)` is the config
  before any override. A two-letter section alias (`sc`, `rc`, `wc`) only in a module that reads
  one section; elsewhere spell the section out.
- Model calls go through `clippa/llm.py`; the provider is the model string's prefix
  ([Model providers](Model-providers)).
- Library code raises `ClipperError` (`clippa/errors.py`); only `cli.main` exits.
- Prompts are `clippa/prompts/<name>_<version>.md`, inside the package (`[paths] prompts` adds a
  folder of your own, read first); `prompts.VERSIONS` names the config key that holds each one's
  version (doctor and a test read it). Add a new version rather than editing one that produced
  reviewed output.
- Kitsu is the record for review decisions; the bot and the war room act through
  `clippa/actions.py`.
- Stages print, services log. A `clippa <stage>` prints its progress (the worker and serve copy
  a child's output into `run_<stage>.log` and `serve.log`); serve, the bot, the worker and the
  recorder log through `logging`, never `print`. The processes share state through the streams
  folder: [How the processes talk](How-the-processes-talk) lists the locks, heartbeats, the DM
  outbox and serve's API, and which log answers which question.

## Adding a command or a stage

Each step is checked by a test where it can be, so a missing one fails `pytest`, not a user:

1. The parser and `cmd_<name>` in `clippa/cli.py`, in its group's `add_<group>`, and the name in
   `COMMAND_GROUPS` (test).
2. Its settings in `clippa/defaults.toml`, a comment on each (that file is the documentation;
   `config.DEFAULTS` is it loaded; a None default is a commented line there plus
   `config.NONE_DEFAULTS`). Nothing machine- or show-specific in a default (test).
3. A pipeline stage is also a row in `clippa/stages.py` (test).
4. A `clippa doctor` check when it needs a key, a service or a file, with the fix in the message.
5. A wiki page or section, in the sidebar (test), and `wiki.page(...)` links in its messages (test).
6. Tests for its pure logic in `tests/`, and the cost printed before any model call it makes. A
   new module needs a test that names it (`tests/test_modules_have_tests.py` fails otherwise).

## Checks

```bash
uvx ruff@0.17.3 check clippa tests
.venv/Scripts/python.exe -m pytest -q
```

Tests are offline and take about 25 s; new pure logic gets a test. CI runs both on every
push, on Linux and Windows (`.github/workflows/check.yml`), except a push or PR that changes only
notes no test reads: the root `*.md` files (not `CHANGELOG.md`), `LICENSE`, `docs/log/`, `docs/archive/`,
`reports/` and `studio/`. Such a PR shows no checks at all; that is expected, not a stuck run. A change
under `docs/wiki/` still runs them, since a test checks the links and the sidebar, and so does one to
`CHANGELOG.md` (`tests/test_changelog.py`) or `studio/research_notes/streamers.json`, which a test reads. The ruff rules are the defaults plus
the few selected in `pyproject.toml` (`[tool.ruff.lint]`). Try risky changes on a short test stream
first. Before merging a PR that changes code, wait for the reviews below and handle each
finding. A PR outside those paths, such as a wiki page or `uv.lock` alone, needs no review.

## Changelog

`CHANGELOG.md` lists the changes someone would notice, newest first: a new command or flag, a
behaviour that changed, a new setting, something the war room or the bot now does. A PR that makes
one adds its bullet in that same PR (with the wiki pages it changes), under `## Unreleased` in a
`### YYYY-MM` block, newest at the top:

```
- 11-18: what changed, from the user's side, in a sentence or two (#319): the command or setting in
  backticks, and the wiki page when there is one.
```

Not for refactors, tests, dependency bumps or notes: a PR that only does those has no bullet. The
dated diary of a session is `docs/log/<YYYY-MM>.md` and the current state is `HANDOFF.md`; the
changelog is what a person updating Clippa would want to read. When a release is made, Unreleased
becomes the release's section ([Releases](#releases)). `tests/test_changelog.py` keeps its shape.

## Releases

A release is a git tag `vX.Y.Z` on main. Pushing the tag makes the GitHub Release
(`.github/workflows/release.yml`): the wheel and sdist, with the **lead** of that version's
section of `CHANGELOG.md` (its lines before the first `###`: a sentence on what the release is
and a short list of highlights) as the notes, and a link to the whole file for the detail by date.
The number is the last release's, in `clippa/__init__.py`,
`pyproject.toml` and the `clippa` entry of `uv.lock`; a test keeps the first two equal and
`uv lock --check` the third. Between releases a copy says which commit it is
(`clippa/buildinfo.py`): `0.2.0` on the tag, `0.2.0+3.g570bb87` three commits past it, `.dirty`
with uncommitted changes. The NAS containers have no `.git`, so `deploy/hub/deploy.sh` and
`deploy/record/deploy.sh` write the checkout's `git describe` into the image
(`deploy/stamp.sh`); serve, the bot and the recorder put the result in their heartbeats and
`clippa services status` prints it next to this checkout's.

1. In a PR: bump `__version__` and `version`, `uv lock`, rename the changelog's "Unreleased"
   to "X.Y.Z (date)", open a new empty one above it, and write the new section's lead: what
   the release is, and a handful of highlights (not the whole list; keep it under 40 lines).
   `tests/test_changelog.py` fails until the section and its lead are there, and the workflow
   refuses a tag without them.
2. After it merges, from a clean checkout of main:

   ```bash
   git tag -a v0.3.0 -m "Clippa 0.3.0" && git push origin v0.3.0
   ```

   Tag right after the merge, before the deploy that should carry the number: until the tag
   exists a copy reports the release before it, plus commits. The workflow refuses a tag that
   disagrees with those files, has no changelog section with a lead, isn't on main, or isn't
   `vX.Y.Z` (`vX.Y.ZrcN` is published as a pre-release).
3. `bash deploy/hub/deploy.sh` as usual; the hub then reports the tag's version, or the commit
   past it.
4. Close the version's milestone, if it has one (`gh api -X PATCH repos/{owner}/{repo}/milestones/N
   -f state=closed`), moving any issue still open on it to the next one first.

Milestones are versions (#370): each is named `vX.Y.0 <what it is>` with a due date, and when its
issues close, that minor version is released and the milestone closed. A patch release (`v0.4.1`)
is a fix between milestones and has none. A new issue's milestone names the version it is meant
for; `parked` and `waiting` issues get none.

The release job runs ruff and the tests again on the tagged commit (Linux only) before it builds
or publishes anything; it deploys nothing. Tags are `vX.Y.Z`, or `v0.3.0rc1` for a pre-release
(a number PEP 440 accepts, since `pyproject.toml` must say the same).

## Code review on PRs

A PR that changes code (`clippa/`, `tests/`, `deploy/`, `.github/`, `pyproject.toml`) gets two
reviews (since 2026-11-13):

1. **Claude, locally, before the first push.** Claude Code runs its `code-review` skill on the
   branch against main and fixes what it finds before the PR opens. It is part of the Claude
   Code session, so it costs nothing extra and takes a few minutes.
2. **Copilot, on GitHub.** A repository rule (Settings, Rules, "Copilot reviews new PRs")
   requests it when the PR opens.

Copilot doesn't review later pushes on its own, so Claude reviews what each later push adds;
for a big one, ask Copilot again with `gh pr edit N --add-reviewer @copilot`.

When Copilot's review doesn't come (out of credits; a request by hand then leaves none either),
Grok is the backup: Claude says so once CI is green and offers
[`clippa codereview --pr N`](#clippa-codereview-by-hand) (about $0.80-1 and 25 minutes), and
runs it on your yes. Merge once Copilot's or Grok's review is in, each finding is fixed or
answered, and CI is green on both systems.

**While Copilot is out of credits (since 2026-11-20, "for a few more days"), Grok is the default
on GitHub:** the `review` workflow (`.github/workflows/review.yml`) runs on every push to a PR that
changes code. It waits up to 10 minutes for Copilot, then runs [`clippa codereview
--pr N`](#clippa-codereview-by-hand) with the repo secret `OPENROUTER_API_KEY` and posts what it
finds; a commit Copilot reviewed is skipped, so it stands aside by itself once Copilot is back. A
failed review never fails the PR's checks. It ran this way until 2026-11-13 too, removed then as
too slow (about 25 minutes a PR); delete the file again once Copilot is reliable.

### `clippa codereview`, by hand

A second opinion from another model when you want one. It sends the diff against main and the changed files in full to
`[codereview] reviewer` in one model call at high effort: Grok 4.7 through OpenRouter, about $1
and 25 minutes for a PR that changes 9 files. It posts the correctness bugs it finds as one
comment on the PR, and the same review on every issue the PR links (`Closes #70`, or `#70` in the PR's title or body). Running it again on a
later push edits each comment in place, never adds another. A rerun that finds the PR's own code changes as they were
(a docs-only push, or merging main into the branch) doesn't review again: the comment carries a
digest of the PR's added and removed lines (a binary file's new contents, a file's mode), without
the context and positions a merge moves. Such
a push still copies the review to a linked issue that doesn't have it yet (a failed post, a
`Closes #N` added later), and a push that reverts all the PR's code changes replaces the findings
with a note that none stand. Nothing runs it on its own, and it doesn't
skip Copilot-reviewed commits unless `[codereview] copilot_wait_s` is over 0. A failed review (no key, the model down, an answer with neither findings nor "No correctness
bugs found", a 30-minute timeout, a dropped
connection or a gateway's 408/502/504, never retried since each try may be paid for) says so in the comment and keeps the last good review below
it. The review is printed before it is posted, so a failed post loses nothing.

It needs `OPENROUTER_API_KEY` in the environment. `clippa codereview` prints the review and posts
nothing; `clippa codereview --dry-run` shows what would be sent and its cost; `--pr N` posts it
(with `GITHUB_TOKEN` set). To try another model, set `CLIPPER_CODEREVIEW_REVIEWER` (e.g.
`openrouter:openai/gpt-5.6-sol-pro`). Check each finding against the code.

Why Grok: on 2026-11-09, 13 models reviewed the same commit of PR #191 (about 88k tokens in).
The real findings were minor ones (loudness measured before the pauses were cut, a raw
subprocess error, a flag dropped for uploaded Shorts):

| Model (OpenRouter) | Effort | Real findings | False ones | Cost | Time |
|---|---|---|---|---|---|
| `openai/gpt-5.6-sol-pro` | high | 2 | 0 | $3.12 | 3.5 min |
| `x-ai/grok-4.7` | high | 2 | 0 | $0.87 | 25 min |
| `google/gemini-4-argon`, `moonshotai/kimi-k3.1-pro`, `z-ai/glm-5.3-prime` | high | 0 | 0 | $0.38–0.79 | 6–7 min |
| `deepseek/deepseek-v4-pro` | high | 0 (one guess) | 0 | $0.18 | 20 min |
| `z-ai/glm-5.3`, `moonshotai/kimi-k2.7-code`, `deepseek/deepseek-v4-flash` | medium | 0 | 0–1 | $0.02–0.15 | 3–20 min |
| `qwen/qwen3.8-max-0902`, `qwen/qwen3.8-flash`, `google/gemini-3.8-flash` | medium | 0–1 | 1 | $0.03–0.32 | 4–7 min |

The false one, from all three in the last row, was that `asetpts=N/SR/TB` collapses the audio:
for audio ffmpeg's `N` counts samples, so it is right. `qwen/qwen3.8-max-prime` used its whole
output on thinking and wrote nothing, as `deepseek/deepseek-v4-pro` did at medium effort before
`[codereview] thinking_tokens` gave it room.

## Editing these docs

> [!IMPORTANT]
> The wiki is built from `docs/wiki/` in the repo: edit the pages there, in the same PR as the
> code they describe. Edits made in the wiki's web editor are overwritten by the next push.

The `wiki` workflow (`.github/workflows/wiki.yml`) pushes the pages to the wiki when a change
lands on main. Images go in `docs/wiki/images/` and are linked by their full wiki address
(`https://github.com/byvfx/clippa/wiki/images/<file>`). Callouts (`> [!TIP]`, `> [!WARNING]`),
Mermaid diagrams and `<kbd>` keys all render.

- One file per page, flat: `Install-Windows.md` is the page "Install Windows". `_Sidebar.md`
  and `_Footer.md` are on every page.
- Link pages wiki-style, without `.md`: `[Faces](Faces)`, `[a section](War-room#faces)`. Link
  repo files with their full GitHub URL.
- `tests/test_wiki.py` checks that every page link resolves and that the sidebar lists every
  page.
- Keep the look plain: no emoji in headings or the sidebar, and no colours of our own in the
  Mermaid diagrams (GitHub's theme handles light and dark). The tabloid style belongs to the
  covers and the weekly, not to these pages.
