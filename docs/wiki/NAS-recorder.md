The recorder is `clippa record watch` in a Docker container on the NAS: it records each show
live, with chat, into the streams share, and the worker takes each recording from there.

> [!WARNING]
> Deploy only when no show is live: a restart starts a new recording part.

## Deploy

`bash deploy/record/deploy.sh` from the repo root (ssh alias `nas`). The recorder reads
`[record]` and `[kick]` from the `clipper.toml` in the NAS folder, the one copy since
2026-11-05 ([NAS hub](NAS-hub)): the script copies `./clipper.toml` there only if there is none
(or `CLIPPA_TOML=<file>` to replace it). Re-run it after changing `[record] channels`. It refuses while a show is being recorded (the restart would start a
new recording part) and when the recorder's state can't be read: `clippa record status` shows
what the recorder is doing, from the heartbeat it writes to the share (exit 3 while live, 1 when
the heartbeat is unreadable), and `deploy.sh --force` deploys anyway.

It also refuses from a checkout behind `origin/main`, which it fetches first, since the image is
built from this checkout's code. Here the override is `--any-branch`, not `--force`, so neither
override skips the other check. `--env PATH` deploys another checkout's `env.local` from a
worktree of main, and the live check then runs the `clippa` on PATH or the main checkout's
`.venv` ([Claude on the NAS](Claude-on-the-NAS#deploy) has the worktree recipe).
`clippa services stop|restart recorder` stops or restarts it over ssh without a new build,
with the same check.

## Cut on the NAS (#51)

The same image cuts segments and compilations beside the source,
instead of the laptop pulling and pushing every byte over the share (it writes at ~23 MB/s
on Wi-Fi). In clipper.toml: `[cut] backend = "nas"`, `nas_host` (the ssh alias, key login),
`nas_data` (the recorder's `CLIPPA_DATA`, e.g. `/volume1/docker/clipper`) and `nas_user`
(its `PUID:PGID`, e.g. `1000:10`). Each `clippa cut` then sends this checkout's `clippa/`
package and clipper.toml over ssh and runs in a one-off container (`clipper-cut-<stream>`),
so the recorder needs no redeploy for code changes, only for a new image. Picks still cut
here (they re-encode). `clippa doctor --role gpu` checks the ssh login and the image.
A cut stopped half way can leave its container running: `ssh nas docker rm -f clipper-cut-<id>`.
