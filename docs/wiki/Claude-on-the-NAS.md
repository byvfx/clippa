Claude Code can live on the NAS beside the [hub](NAS-hub) and show up in the Claude app on
the phone (or claude.ai/code in any browser), so Clippa can be driven, debugged and changed
from anywhere while the laptop sleeps. It runs `claude remote-control` in a container with its
own clone of the repo. The NAS dials out to Anthropic: no port forwarding, no tailnet, nothing
exposed.

## What it's for

Ask it when something on the NAS looks wrong or you have a question about it, especially from
the phone: it sees the same streams, config and logs that serve, the bot and the worker use.
Its job is to find out what's wrong and write it up; the agents fix it.

- **Find the cause** of something broken, in the logs and state files.
- **Answer "is it working?"**: where a stream is, whether a retry went through, whether Modal
  is still running.
- **Test an idea cheaply**: reproduce a bug, measure something. It gives the cost first and
  waits for your yes.
- **Write tickets**: what it finds becomes a GitHub issue an agent can pick up without the
  chat (evidence, cause, a checklist and the scope).
- **Band-aids you ask for**: a one-stream setting (say a stream's own `config.toml`), so the
  pipeline keeps moving until the real fix lands.
- **Questions about how Clippa works**: config, the pipeline, why something was picked or
  skipped.

What it's not for: code fixes (those go to other agents through the tickets), deploys and
restarts (it can't reach Docker or ssh, and the recorder is off limits), uploading or
publishing unless you ask, and heavy work while a show is live.

## What it can do

- Run any stage on any stream, read logs and state in the NAS folder, edit `clipper.toml` (it
  backs it up first, as on the laptop).
- Work on a branch in its clone (`/app`) and commit notes. It doesn't write code fixes, but it
  writes notes and ticket write-ups, and pushes or opens a PR for those only when asked.

What it can't: restart or deploy containers (no Docker socket, no ssh). serve, the bot and the
worker keep running the code of the last hub deploy; a merged change reaches them when you run
`bash deploy/hub/deploy.sh` from the laptop.

It also runs the nightly [checkup](Checkup#the-nightly-report) at `CHECKUP_AT` (in
`env.local`; empty = off): `clippa checkup`, then, only if that finds something, a
`claude -p` that works out the causes and DMs you one report (read-only, but for one PR for a
[clear code fix](Checkup#a-pr-for-a-clear-code-fix)). After it, on their days, come the
weekly digest and the monthly deep [audit](Audit).

## Deploy

`bash deploy/claude/deploy.sh` from the repo root (Git Bash; ssh alias `nas`). Its own compose
project (`clippa-claude`; `clipper-claude` before the rename's pass 3): it never restarts the recorder or the hub, and a hub deploy never
ends a Claude session. Re-running it rebuilds the image (a newer Claude Code at most once a day)
and restarts the container, which ends the session open in the app; the clone and the sign-in
stay.

The first run copies `deploy/claude/env.template` to `deploy/claude/env.local` (gitignored) and
stops. Fill in:

- the same keys as `deploy/hub/env.local` (`ANTHROPIC_API_KEY`, `KITSU_TOKEN`, the Modal token,
  the alert webhook); the deploy checks them against this machine's own as the hub's does
  ([NAS-hub](NAS-hub#deploy));
- `GH_TOKEN`: a GitHub fine-grained token for this repo only, with Contents and Pull requests
  read and write, so it can push branches and open PRs (and clone the repo, if yours is private);
- `CLIPPA_REPO`, `GIT_USER_NAME`, `GIT_USER_EMAIL` can stay empty: the deploy fills them from
  the checkout it runs in.

On the NAS, `CLAUDE_DIR` (`/volume1/docker/clipper-claude`, which keeps its name for now) holds `repo/` (the clone, made on
the first start and left alone after that) and `home/` (Claude Code's sign-in, settings and
memory, and the uv cache).

Remote Control needs a claude.ai sign-in (Pro or Max), not an API key, and it refuses to start
where `ANTHROPIC_API_KEY` is set. So the deploy hands the key to Clippa as a file
(`/run/secrets/anthropic_key`, `[paths] anthropic_key`) and keeps it out of the container's
environment. Clippa's model calls still bill the API key as everywhere else while a stage
is on Claude; with
[`docs/openrouter-claude.toml`](https://github.com/byvfx/clippa/blob/main/docs/openrouter-claude.toml)
pasted, those calls use `OPENROUTER_API_KEY` (or `[paths] openrouter_key`) instead. Claude
itself uses the subscription.

## Sign in

Once, after the first deploy. The container waits until it is signed in:

1. `ssh -t nas 'cd clippa-claude && docker compose exec claude claude auth login'`. Open the
   link it prints on the laptop or phone, sign in to claude.ai, and paste the code it shows
   back into the terminal.
2. `ssh -t nas 'docker attach clippa-claude'`. The first start asks to enable Remote Control
   and to trust `/app`: answer `y` to both, then detach with Ctrl-P Ctrl-Q (Ctrl-C would stop it).
3. In the Claude app on the phone (or claude.ai/code), the session "Clippa on the NAS" is in
   the session list. Ask it to run something small, e.g. `clippa doctor --role claude`.

The sign-in and both answers are kept in `home/`, so later deploys and restarts come back by
themselves. A claude.ai sign-in expires: the session warns three days before, then stops
working until step 1 is done again. The container restarts on its own if the NAS is offline for
more than about ten minutes (Remote Control gives up and exits); the app shows a new session.

Each new session starts in `CLAUDE_PERMISSION_MODE` (`default`: it asks on the phone before
each command or edit); switch one to Accept edits or Auto in the app. `CLAUDE_SESSION_NAME`
names it.

Session transcripts are plaintext in `home/.claude/projects` on the NAS, like on the laptop.

## Check

```bash
ssh nas 'cd clippa-claude && docker compose exec claude clippa doctor --role claude'
```

It checks what a worker needs (keys, ffmpeg and QSV, Modal, Kitsu) plus Claude Code itself:
installed, signed in, the clone on a branch with nothing left uncommitted, and the streams
writable. Logs: `ssh nas 'cd clippa-claude && docker compose logs -f claude'`.

If the app can't reach it, `ssh nas` from the laptop still works, and the [war room](War-room)
is still on the phone through the tailnet ([Phone access](Phone-access)).
