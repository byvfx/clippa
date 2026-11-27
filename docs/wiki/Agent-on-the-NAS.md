OpenCode can live on the NAS beside the [hub](NAS-hub) and beside [Claude on the NAS](Claude-on-the-NAS).
This container is the burn-in copy: it does not replace `clippa-claude`, and it comes up with the
checkup clock off so the two do not both send a morning report.

It runs headless checkup and audit through OpenCode and OpenRouter. The phone still uses
`clippa-claude` until you choose otherwise (below). Nothing is published: no host port, no
password in the environment.

## What it uses

`[agent]` in `clippa/defaults.toml` is the only model config. A model is Clippa's
`provider:model` form (`openrouter:anthropic/claude-sonnet-5.5`). `clippa agent-config --role
checkup|audit` turns the first `:` into `/` and, when the effort is set, appends `#effort`
(`openrouter/anthropic/claude-sonnet-5.5#high`). `cli = "claude"` takes a bare `claude-*` name
(or strips `anthropic:`) and refuses an `openrouter:` model. Override it in `clipper.toml`,
`clipper.local.toml`, or `CLIPPA_AGENT_<KEY>`. There is no second set of `AGENT_MODEL` variables.

The OpenRouter key is a file. `deploy/agent/deploy.sh` reads `OPENROUTER_API_KEY` from
`env.local`, writes `openrouter_key` (mode 600), and strips `OPENROUTER_API_KEY`,
`ANTHROPIC_API_KEY`, and `OPENCODE_PASSWORD` from the `.env` the container sees. Compose mounts
the file at `/run/secrets/openrouter_key` and sets `[paths] openrouter_key` to that path.

## Deploy

`bash deploy/agent/deploy.sh` from the repo root (Git Bash; ssh alias `nas`). Its own compose
project is `clippa-agent`. It never restarts the recorder, the hub, or `clippa-claude`.
Like the claude deploy, it refuses from a checkout behind `origin/main` (`--force` overrides
that) and takes `--env PATH` for a worktree of main
([Claude on the NAS](Claude-on-the-NAS#deploy)).

The first run copies `deploy/agent/env.template` to `deploy/agent/env.local` (gitignored) and
stops. Fill in `OPENROUTER_API_KEY` and the same other keys the hub uses (`KITSU_TOKEN`, the
Modal token, the alert webhook, `GH_TOKEN`). Leave `CHECKUP_AT` empty until `clippa-claude`'s
checkup is turned off. `AGENT_DIR` (`/volume1/docker/clippa-agent`) holds `repo/` and `home/`,
separate from Claude's clone.

Until this is on main, set `CHECKUP_REF` in `env.local` to the branch you are burning in.
`clippa-checkup` runs that ref's code, and `clippa agent-config` has to be in it.

## Check

```bash
ssh nas 'cd clippa-agent && docker compose exec agent clippa doctor --role agent --offline'
```

`--role claude` is the same check. It checks `[agent]` and the translation, that `opencode` (or
`claude`, when that is the CLI) is installed, that the OpenRouter key file is readable and
non-empty, that `OPENROUTER_API_KEY` and `OPENCODE_PASSWORD` are not in the environment, that
`opencode.json` is valid JSON, that `/app` is a git clone, and that the streams folder is writable.

## Phone bridge

Two ways to reach it from a phone. Pick one. The container ships on the first.

- **Option A.** Leave the phone on `clippa-claude` (`claude remote-control`). This container
  only runs the checkup clock, and only after `CHECKUP_AT` is set. Nothing listens.
- **Option B.** An OpenCode server on `127.0.0.1:4096` and no other address. Do not publish a
  host port. Do not set `OPENCODE_PASSWORD`: it would be in the environment and in the logs.
  The line in `deploy/agent/entrypoint.sh` stays commented until this is the choice.

## Headless runs

`agent-run --role checkup|audit` reads the prompt on stdin into a mode-600 file and asks
OpenCode with a permission list that denies everything first, then allows reads under `/data`
and `/tmp` and a fixed set of commands (`checkup-clippa`, `checkup-git`, and for checkup only
`checkup-pr` plus edits inside `~/checkup-fix`). The last rules deny secret paths, shell
metacharacters (`&&`, pipes, redirects, heredocs), and, for audit, every edit. The DM is one
command, one `--line` per line of the message, not a heredoc.

`checkup-clippa` accepts only those read-only commands, plus `alert send` when it is not a
`--dry-run`. Anything else exits 2.
