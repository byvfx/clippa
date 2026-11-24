Settings come in layers: built-in defaults, then `clipper.toml`, then this machine's own
`clipper.local.toml` (written by `clippa setup`: streams path, encoder, transcribe backend),
then environment variables named `CLIPPER_<SECTION>_<KEY>` (for example
`CLIPPER_CUT_CLIP_CODEC=libx264`). That lets one shared `clipper.toml` serve every machine.
`clipper.local.toml` and env vars win over show and stream overrides too.

**The Clippa names (#286).** Every one of these also takes the new name: `clippa.toml` and
`clippa.local.toml` beside it (when both spellings of a file are there, the new one is read),
`CLIPPA_CONFIG`, `CLIPPA_LOCAL_CONFIG`, `CLIPPA_<SECTION>_<KEY>` and every other `CLIPPER_*`
variable clipper itself reads (when both spellings are set, `CLIPPA_*` wins), and a `clippa`
command in the venv beside `clipper`. The deploy scripts and compose files under `deploy/` took
the new names in the rename's deploy step (pass 3): `CLIPPA_DATA`, `CLIPPA_REPO`, `CLIPPA_TOML`
(an `env.local` that still says `CLIPPER_DATA` or `CLIPPER_REPO` is read the same), the compose
projects `clippa-hub`, `clippa-record` and `clippa-claude`, and the Modal apps `clippa-whisperx`
and `clippa-lipsync`. The NAS data folder (`/volume1/docker/clipper`) and `clipper.toml` keep their
names until the rename's last pass. The bootstrap puts both `clippa` and `clipper` on PATH. The old
names keep working for now; `clippa doctor` lists the old-spelling variables still set, so you
can see what is left to move.

Each Kick channel is a show: a `[shows.<key>]` table with its channels, show name, scoring
context and Shorts layouts. A show's sub-tables override the base sections for that show.
One stream can go further with its own `streams/<id>/config.toml` (same sections), for a big
night: more picks, the breaking story in `[scoring] stream_context`, and `[schedule]
priority = true` so its items go out ahead of the backlog. Unknown keys there are refused.
[`docs/big-story.toml`](https://github.com/byvfx/clippa/blob/main/docs/big-story.toml) is the ready-made one to copy: more picks and Shorts, priority, and the
night's compilation. A `[schedule] stream_budget` kind at 0 goes only when pinned, so
`compilation = 0` there keeps compilations for big-story nights.
`clippa/defaults.toml` documents every key with its default; `clipper.toml` holds only what
differs (`clippa setup` starts a new one that way, and `clipper.example.toml` is a starter to
copy). `clipper.toml` itself is gitignored because it holds your
Discord ids and Kitsu address.

`clippa config get meta.model` prints a setting's value and every layer that sets it
(add `--stream <id>` for one stream, or `--show <key>`). Settings with a fixed set of choices
(`[bleep] mode`, the backends, `[thumbnail] style`, ...) refuse anything else when the config
loads; an unknown setting or `CLIPPER_*` variable is a warning on every command, and
`clippa doctor` fails on it. `clippa --show <key>` applies that show to any stream.

`clippa config diff` lists every setting this install changes from `clippa/defaults.toml`,
by layer: `clipper.toml`, each show's table, `clipper.local.toml`, then `CLIPPER_*`
(or `CLIPPA_*` when that spelling is set) variables
(`clippa config diff kino` keeps one show's table; `--json` for a script). A value equal to
its default is omitted, so a `claude-` line that copies `defaults.toml` is absent here while
`rg` still finds it. A stream's `streams/<id>/config.toml` is outside this list. It answers
"what did I change" without opening the file on the share.

Secrets stay out of the config: API keys and tokens are environment variables, or a file
under `secrets/` (`[paths] anthropic_key` for `ANTHROPIC_API_KEY`, `[paths] openrouter_key`
for `OPENROUTER_API_KEY`, the YouTube OAuth files; gitignored). Sending every Claude stage
through OpenRouter is a paste of
[`docs/openrouter-claude.toml`](https://github.com/byvfx/clippa/blob/main/docs/openrouter-claude.toml)
into `clipper.toml` ([Model rollout](Model-rollout#moving-every-claude-model-to-openrouter)).
Nothing loads that file on its own.
