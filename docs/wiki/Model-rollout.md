How to try config-7 on a live install, one stage at a time, and put the old file back.
The defaults stay as they are: `[segments] prompt_version` is `"v3"` until a config you load sets it.

## Config-7

Config-7 is an opt-in overlay, the same kind of file as
[`docs/big-story.toml`](https://github.com/byvfx/clipper/blob/main/docs/big-story.toml).
Nothing loads it on its own. Paste the tables for the stage you are turning on into
`clipper.toml` (every stream) or into one stream's `streams/<id>/config.toml` (that stream
only). The overlay is only those tables. Do not replace the live file with it: the live
file still holds the shows, paths and ids.

- [`docs/config-7-hybrid-segv4.toml`](https://github.com/byvfx/clipper/blob/main/docs/config-7-hybrid-segv4.toml):
  Haiku for Pass A, Sonnet for Pass B, Opus for segments, and other models for meta, thumbnails and the cheaper checks.
- [`docs/config-7-hybrid-segv4-openrouter.toml`](https://github.com/byvfx/clipper/blob/main/docs/config-7-hybrid-segv4-openrouter.toml):
  the same mix, with every bare `claude-*` name sent through OpenRouter.

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
