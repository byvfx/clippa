`clipper checkup` looks for the problems the [alerts](Review#alerts) can't see, because nothing
ever raised an error: a stage that hangs instead of failing, a service that stops beating, a
recording the worker never starts, a release that misses its upload, a title that breaks the
house rules. It reads the files the services write, the same ones `clipper status` reads. It
makes no model call, touches no network and runs nothing. It prints what it found and exits 1
when it finds anything.

```bash
clipper checkup                  # every check
clipper checkup --only streams   # one check (repeatable)
clipper checkup --json           # the report as JSON, for a script or Claude
clipper checkup --fix            # make the repairs [checkup] fix allows (serve does, hourly)
```

## What it checks

| Check | Finding | Setting |
|---|---|---|
| services | serve's, the bot's or the recorder's heartbeat is stale or missing. While the bot is down no DM arrives, so the report is the only place this shows up | |
| streams | a stage is still running after `stuck_hours`, but the worker is still beating | `stuck_hours` (6) |
| streams | run.json says running but the worker stopped beating: it died mid-run | `[worker] stale_minutes` × 2 |
| streams | a finished recording has waited `queue_hours` for the worker while nothing runs | `queue_hours` (3) |
| streams | a run failed with no retries left (also DM'd once, when it ran out) | `[worker] max_attempts` |
| streams | a run held because a provider rejected an API key (DM'd at once; retried when a key changes) | - |
| schedule | a release isn't uploaded `late_hours` before its slot, or its slot has passed | `late_hours` (1) |
| schedule | nothing at all releases in the next `gap_hours` | `gap_hours` (24; 0 = off) |
| lint | a title, description or thumbnail text about to go out has one of `words` in it, is over YouTube's limit, or has no description. It checks what `clipper upload` would send (Kitsu's columns, then the draft) | `words` |
| auto | an item that [approved itself](Review#approval-by-silence) in the last 14 days and that a person then rejected, reopened, held or sent back for changes | `[review] auto_min_*`, `auto_grace_minutes` |
| housekeeping | temp files (`<file>.<pid>.<thread>.tmp`) a killed write left beside a state file, a day old or more | - |

The settings are `[checkup]` in the config; a show's table or a stream's own `config.toml` can
override them for its streams and items. A check that crashes is reported as a finding of
its own, so the other checks still run.

Each finding has a stable key (`stuck:kick-1:cut`, `late:kick-1/s04_x`). `--mark` saves the
keys in `streams/.checkup/state.json`, so the next run can mark which findings are new and
which it saw before ("since 11-08 08:30"). Only the scheduled run uses `--mark`. A run by hand
doesn't, so it never hides anything from the next scheduled run.

## Repairs

Some findings have a fixed repair, from a short list, with no model call (`clipper/repairs.py`,
#287). Every checkup says, under such a finding, what its repair did or would do (`fix: would:
one more attempt ...`), and the first line counts them: `3 findings; fixed 1, would fix 1, need
you 1`. "Will fix" is a repair that is on and either waiting for its time or due on serve's next
run (a checkup without `--fix`, like the nightly one, changes nothing).

| Repair | For | What it does |
|---|---|---|
| `retry` | a run that failed with no retries left, when its error is a passing one: a rate limit, a dropped connection, a remote server's 5xx (`passing`, whole words) | `retry_after_hours` (3) after the run failed, gives back one attempt in `run.json`; the worker runs it again on its next pass. Never while a worker holds the stream, nor for a run held on a rejected key |
| `tmp` | the housekeeping finding | deletes those temp files |

serve runs `clipper checkup --fix` every `fix_minutes` (60), over the checks that have repairs
(streams and housekeeping). Only the kinds listed in `fix` act; the
others are **would-fix**: what they would have done goes in the ledger
(`streams/.checkup/repairs.json`), once a day per finding, and nothing else changes. `fix` is
empty to start, so the first week only reports. Read it with the nightly reports, then turn the
repairs on one at a time in clipper.toml, for example `[checkup] fix = ["tmp"]`. A name that isn't a
repair stops `--fix` with an error.

Every repair is safe to repeat and capped: at most `max_repairs` (2) tries per finding key in 7
days, counted in the ledger, a failed try included. After that the finding says "tried 2 times in
7 days: it needs you" and stays for you. `--fix` exits 1 only when a repair failed (serve DMs
that); a finding alone is the nightly report's business. No repair ever uploads or publishes,
makes a Kitsu decision, touches the recorder or source footage, edits the config or viewer-facing
text, or restarts anything. No repair calls a model itself, but a run given one more attempt
reruns its failed stage, which calls models or Modal as it always does (score, segments,
transcription): at most twice a week per stream.

Checked and left out, because a service already does them: a late release (serve's upload job
replans every hour), a stale run lock (the worker takes it over), a stream queued while nothing
runs (the worker polls every `poll_s`; when it doesn't, the fix is its container, which nothing
here reaches).

## The nightly report

The [claude container](Claude-on-the-NAS) runs the checkup every morning at `CHECKUP_AT`
(`deploy/claude/env.local`, e.g. `08:30`, after quiet hours; empty = off) and DMs you once:

- **Nothing found:** a one-line "Checkup 11-09: all good.", with no model call, plus what the
  repairs did or would have done in the last 24 hours, when they did anything.
- **Findings:** a headless `claude -p` run (prompt: `deploy/claude/checkup.md`) reads the
  report, the stage logs, `logs/` and recent commits, and sends one DM with each problem, its
  likely cause and the next step. It runs on the Claude subscription, not the API key.
- **Claude can't run** (signed out, usage limit): the plain findings go out instead.

That run is read-only. It may use the read tools and a fixed list of commands, each exactly as
written (`clipper checkup`, `checkup --json`, `status`, `doctor --offline`, `record status`,
`worker --status`), `alert send`, and git through `checkup-git log|show|diff`, which refuses the
options that write (`--output`). Nothing else: no stage, no model call, no upload, no schedule,
config or Kitsu change, no commit, no PR. When a fix needs one of those, the DM says what to run.

It runs `main`'s code from its own worktree (`~/checkup-src` in the container, updated every
run), not the clone, which a phone session may have on any branch; `CHECKUP_REF` in
`env.local` picks another branch. Run it by hand with
`ssh nas 'cd clipper-claude && docker compose exec claude clipper-checkup --dry-run'`
(`--dry-run` prints the DM instead of sending it and doesn't `--mark`); the timer's output is in
`/tmp/checkup.log` in the container. The bot sends `checkup` DMs only once the hub runs code
that has the kind (deployed after 2026-11-09).

The DM is kind `checkup` in `[notify] kinds`, and `clipper alert send` is how any script queues
one: `clipper alert send --kind checkup "text"`, or `-` to read stdin.

Planned (#287): more checks (yt-dlp out of date or failing, cookies and tokens about to expire,
repeated errors in `logs/`, Kitsu approvals that never reached the schedule), and then PRs for
clear code fixes from the nightly run.
