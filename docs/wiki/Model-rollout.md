How to try config-7 on a live install, one stage at a time, and put the old file back.
The defaults stay as they are: `[segments] prompt_version` is `"v3"` until a config you load sets it.

## Config-7

Config-7 is an opt-in overlay, the same kind of file as
[`docs/big-story.toml`](https://github.com/byvfx/clippa/blob/main/docs/big-story.toml).
Nothing loads it on its own. Paste the tables for the stage you are turning on into
`clipper.toml` (every stream) or into one stream's `streams/<id>/config.toml` (that stream
only). The overlay is only those tables. Do not replace the live file with it: the live
file still holds the shows, paths and ids.

- [`docs/config-7-hybrid-segv4.toml`](https://github.com/byvfx/clippa/pull/377):
  Haiku for Pass A, Sonnet for Pass B, Opus for segments, and other models for meta, thumbnails and the cheaper checks.
- [`docs/config-7-hybrid-segv4-openrouter.toml`](https://github.com/byvfx/clippa/pull/377):
  the same mix, with every bare `claude-*` name sent through OpenRouter.

The config-7 overlays and the v4 prompts are on that PR's branch and not on `main` yet.

`clipper.local.toml` and `CLIPPER_<SECTION>_<KEY>` still win over both.
`clippa config get <section.key> --stream <id>` shows which layer set a value.

## Models

A model is a string in the config. The prefix picks the provider (`clippa/llm.py`):

| String | Provider |
|---|---|
| `claude-<model>`, or `anthropic:claude-<model>` | Claude (`ANTHROPIC_API_KEY`) |
| `openai:<model>` | OpenAI (`OPENAI_API_KEY`) |
| `gemini:<model>` | Gemini (`GEMINI_API_KEY`) |
| `openrouter:<vendor>/<model>` | OpenRouter (`OPENROUTER_API_KEY`) |
| `local:<model>` | the server at `[llm] local_base_url` |

The prefix is the part before the first colon. The rest is the model id, so a dotted
OpenRouter id is fine: `openrouter:anthropic/claude-opus-5.5`, and the same for
`anthropic/claude-sonnet-5.5` and `anthropic/claude-haiku-5.5`. The other prefixes are on
[Model providers](Model-providers).

The OpenRouter key is `OPENROUTER_API_KEY`, or the file at `[paths] openrouter_key`
(`secrets/openrouter_secret.txt` when that path is the default). A set environment variable
wins over the file.

## Moving every Claude model to OpenRouter

Move every model setting whose value is a bare `claude-...` name onto OpenRouter. Same
model, same effort. The name changes: `claude-sonnet-5-5` becomes
`openrouter:anthropic/claude-sonnet-5.5`, and the same for Haiku and Opus
(`openrouter:anthropic/claude-haiku-5.5`, `openrouter:anthropic/claude-opus-5.5`).

`clippa/defaults.toml` has 29 of these settings:

| Section | Settings |
|---|---|
| `[scoring]` | `model_a`, `model_b` |
| `[segments]` | `model`, `score_model` |
| `[meta]` | `model` |
| `[thumbnail]` | `model` |
| `[shorts]` | `layout_model` |
| `[review]` | `check_model` |
| `[preshow]` | `model` |
| `[chat]` | `model` |
| `[retake]` | `model` |
| `[pick]` | `model`, `locate_model` |
| `[cast]` | `label_model` |
| `[news]` | `judge_model` |
| `[blinds]` | `clues_model` |
| `[archive]` | `ask_model` |
| `[receipts]` | `model`, `script_model` |
| `[investigate]` | `facts_model`, `leads_model`, `check_model` |
| `[swarm]` | `planner_model`, `worker_model` |
| `[story]` | `triage_model`, `find_model`, `dossier_model`, `edit_model` |
| `[weekly]` | `model` |

Leave every setting that is not Claude as it is: Whisper (`[transcribe] model`,
`mlx_model`, `diarize_model`, `[doc] words_model`), ElevenLabs (`[narrate] model_id`,
`tts_model`), sync.so (`[perform] sync_model`), and `[paths]` entries. The `[llm.prices]`
table also names `claude-haiku-5-5`, `claude-sonnet-5-5` and `claude-opus-5-5`. Those
three rows are prices. OpenRouter reports each call's cost, so leave them.

[`docs/openrouter-claude.toml`](https://github.com/byvfx/clippa/blob/main/docs/openrouter-claude.toml)
is the all-OpenRouter file: those 29 settings, and nothing else. Effort stays whatever the
live config already has. Nothing loads it on its own.

Paste its tables into the live `clipper.toml`. Do not replace that file: it still holds
the shows, paths and ids. A second copy of a table is a TOML error, so put each key in
the section that is already there. Each pasted key overrides that one default. Every
other key stays. A `[shows.<key>]` table, a
stream's `streams/<id>/config.toml`, `clipper.local.toml` and `CLIPPER_<SECTION>_<KEY>`
(or `CLIPPA_<SECTION>_<KEY>`) still win over the pasted keys. A bare `claude-` name in
any of those keeps that call on Anthropic.

From the data folder that holds the live file (`/data` in the containers,
`/volume1/docker/clipper` on the NAS), search the files that are there:

```bash
rg -n 'claude-' clipper.toml clipper.local.toml streams/*/config.toml
```

Show tables are inside `clipper.toml`, so that file's hits cover them. When `clippa.toml`
or `clippa.local.toml` is the file in use, search that name too (the new spelling wins
when both are there). Leave out a path that has no file. `clippa config diff` lists
settings that differ from `clippa/defaults.toml`: `clipper.toml`, each show table,
`clipper.local.toml` and `CLIPPER_*` / `CLIPPA_*` variables. A value equal to the default
is omitted, so a live `model_a = "claude-haiku-5-5"` (the same string as `defaults.toml`)
is absent from the list. `rg` still prints that line. A stream's `config.toml` is absent
from `config diff` as well. The overlay's test checks `docs/openrouter-claude.toml`
against the defaults. It leaves the live `clipper.toml` unread.
The `rg` is what catches a `claude-` line that the test and `config diff` both skip.

Set `OPENROUTER_API_KEY` before the swap, or put the key in the file at `[paths]
openrouter_key` (`secrets/openrouter_secret.txt` when that path is the default). A set
environment variable wins over the file.

Do this between streams. `clippa worker --status` prints `run=running` while a stream's
pipeline holds its lock. Wait until `clippa worker --status` shows no `run=running`.

1. Copy the live file to `clipper.toml.<date>.bak` beside it (use the date you do it).
2. `clippa doctor`.
3. Paste the tables from `docs/openrouter-claude.toml` into the live `clipper.toml`. Do not
   replace that file. The dated copy stays beside it.
4. `clippa services restart all` restarts the worker, serve and the bot. It does not restart the recorder.
5. `clippa doctor` again.
6. After the first stream, read that stream's lines in `[paths] costs` (`logs/costs.jsonl`).
   A model call is one line: `kind` is `llm`, `stream` is the stream id, `provider` is who
   was billed, and `model` is the model string from the config. A call still billed
   straight to Anthropic has `provider` `anthropic` and a `model` without the
   `openrouter:` prefix.

`clippa doctor` checks that each provider in use has its key. A bare `claude-` name still
counts as Claude, and the Anthropic key satisfies that check. With that key still set, a
passing doctor says nothing about a leftover name. The cost log shows a call billed
straight to Anthropic.

These still reach Anthropic after the paste:

- A bare `claude-*` name, or `anthropic:claude-*`. That stage uses the Anthropic SDK.
  Those calls are the `kind` `llm` lines with `provider` `anthropic`.
- `[scoring] workspace_id` (or `ANTHROPIC_WORKSPACE_ID`) is a header on that SDK client.
  With no bare Claude name left, no stage builds the client, so the id sits unused until
  one comes back. It writes no cost line.
- The server-side refusal fallback (`json_call(..., fallback=True)`) runs on the Claude
  API only. On an `openrouter:` model the call still runs, and is logged as `provider`
  `openrouter`. The fallback does not run and writes no line of its own.
- `clippa setup`'s key check (`setup.py`) calls `models.retrieve` only while some stage
  still names Claude directly (a bare `claude-*` or `anthropic:`), and the retrieve uses
  that model (`claude_model(cfg)`). Once every stage is on another provider, setup skips
  the prompt and the probe and makes no Anthropic call. `clippa doctor` without
  `--offline` skips the Anthropic ping once `claude_model` is empty too. The probe writes
  no `llm` line.
- `clippa keys --check` (and the war room's **Check**) does not ask Anthropic when no
  stage uses a bare `claude-` or `anthropic:` model: those two rows are optional and
  skipped, even if the key is set. While a stage still names Claude, `--check` GETs
  `https://api.anthropic.com/v1/models?limit=1` when `ANTHROPIC_API_KEY` is set, and with
  no name also asks the Admin API when `ANTHROPIC_ADMIN_KEY` is set
  (`GET /v1/organizations/users?limit=1`). `--check Anthropic` asks only the inference
  key. Both are free reads. Neither writes an `llm` line.
- `clippa/usage.py` reads Claude's own bill from the Admin API (`ANTHROPIC_ADMIN_KEY`,
  or the file at `[paths] anthropic_admin_key`). With no admin key it says not configured
  and does not call Anthropic. That read writes no `llm` line. The normal
  `ANTHROPIC_API_KEY` cannot read the bill.

`ANTHROPIC_API_KEY` is optional once no stage names Claude. `clippa doctor` says so.
Keep the key when a stage might go back to a bare `claude-*` name: that name uses it
again. The NAS Claude container still keeps the key in a file and out of the environment
([Claude on the NAS](Claude-on-the-NAS)).

Step 6 is the stage calls. The workspace header, the fallback, the setup probe,
`clippa keys --check` and the Admin API bill are absent from those lines as `provider`
`anthropic`.

A `--dry-run` prices an `openrouter:` name with `known_price`: the `[llm] prices` table,
then OpenRouter's public `/models` list. The built-in rows `claude-haiku-5-5`,
`claude-sonnet-5-5` and `claude-opus-5-5` are separate from the dotted OpenRouter ids
(`anthropic/claude-haiku-5.5`, and the same for Sonnet and Opus), so the list is what
prices the overlay. The list is fetched once per process (15 second timeout). A failed
fetch is cached empty and is left alone after that, and it does not raise. A model still
missing prints `(no [llm] price: counted $0)`. The overlay adds no price rows. A real
call uses the cost OpenRouter puts on the answer.

Rollback is [Putting it back](#putting-it-back): between streams, put the dated copy
back at the live path, then `clippa services restart all` and `clippa doctor`.

The NAS Claude container (`deploy/claude`, Claude Code) is separate and this swap does not cover it.

## Before a stage moves

`[segments] prompt_version = "v4"` reads `clippa/prompts/segments_v4.md` and
`clippa/prompts/compilation_v4.md`. Both use that one setting. Deploy the code that ships
those files before any config sets `"v4"`. The containers read the prompts in the image;
until then the stage looks for files it does not have.

The 50-minute cap in the v4 prompt is text. `[segments] max_s` stays `3600`. When you turn
v4 segments on, also set `[segments] split_over_s = 3000` (50 minutes). The default is `0`,
which is off, and a long segment is still dropped for length. At `3000`, a segment longer
than that is not cut: the skip reason is `re-split`, the segment keeps `resplit`, and the
stage log gets a `RESPLIT` line. A segment of exactly 3000 seconds is not flagged. The
overlay does not set `split_over_s`.

## One stage at a time

Do this between streams. `clippa worker --status` prints `run=running` while a stream's
pipeline holds its lock.

1. Meta and thumbnails: `[meta] model` and `[thumbnail] model`.
2. Segments v4: `[segments] model`, `prompt_version = "v4"`, and `split_over_s = 3000`.
3. Pass B (`[scoring] model_b`) and the rest of the overlay's tables.

Leave `[review] auto_approve` at `false`. See below.

## Putting it on

Wait until `clippa worker --status` shows no `run=running`. Then, beside the live `clipper.toml`:

1. Copy it to `clipper.toml.2026-11-22.bak` (use the date you do it).
2. `clippa doctor`. `clipper doctor` is the same program.
3. `clippa review check <stream> --dry-run`. It needs a stream id. It prints how many cover
   vision calls the check would make and what they would cost, and it does not write `checks.json`.
4. Build the new file from the live one, with this stage's tables pasted in, and put it at
   the live path. The dated copy stays beside it.
5. `clippa services restart all` restarts the worker, serve and the bot. It does not restart the recorder.
6. `clippa doctor` again.

## Putting it back

The same steps, between streams. Put the dated copy back at the live path, then restart and
run `clippa doctor` again.

## Auto-approve

Do not set `[review] auto_approve = true` in any file `clippa serve` reads: `clipper.toml`,
`clipper.local.toml`, a show's table, or a stream's `config.toml`. The default is `false`.
While any show has it on, serve runs `clippa review auto` every 10 minutes and approves what
is due.

A shadow test is `clippa --config <copy> review auto --dry-run`. `--dry-run` says what would
approve and changes nothing. Point `--config` at a copy, not at the file serve is using.

## Comparing two arms

`clippa review load` counts what a reviewer would see from a finished run, next to that run's
cost. It writes nothing and calls no model.

```bash
clippa review load <stream...> [--config PATH] [--costs PATH] [--arm NAME] [--json]
```

`--config` on this command is that arm's `clipper.toml` alone. `clipper.local.toml` and
`CLIPPER_*` / `CLIPPA_*` are not applied, and the header says so. `--costs` defaults to
`[paths] costs`. `--arm` defaults to the config file's name. `--json` prints one document;
concatenate the rows and drop each `total` to put several arms in one table.
