#!/bin/sh
# The nightly checkup (#185), run by the claude container at CHECKUP_AT (entrypoint.sh).
# `clippa checkup` first, no model: nothing found = a one-line "all good" DM and no Claude run;
# findings = a headless `claude -p` (checkup.md) that reads the logs and DMs one report. That
# run is read-only but for one thing: a clear code fix may become one PR, edited only in its own
# worktree and opened through checkup-pr (#287 step 6). The tools below are all it may use.
#
# It runs CHECKUP_REF's code (default main) from its own detached worktree, updated each run,
# never the clone a phone session has on some branch: PYTHONPATH puts the worktree's clippa
# ahead of /app's in the shared venv.
#
# By hand: docker compose exec claude clippa-checkup [--dry-run]
#   --dry-run prints the DM instead of sending it (Claude still runs when there are findings).
set -u
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY=1
# One checkup at a time: a dry run by hand during the nightly one would otherwise share its PR
# worktree. A dry run also gets a worktree of its own, and checkup-pr open refuses in it.
exec 9>/tmp/checkup.lock
flock -n 9 || { echo "a checkup is already running" >&2; exit 0; }
[ -n "$DRY" ] && export CHECKUP_DRY=1 CHECKUP_PR_DIR="${CHECKUP_PR_DIR:-$HOME/checkup-fix}-dry"
FIX=${CHECKUP_PR_DIR:-$HOME/checkup-fix}
rm -f "$FIX.branch"   # checkup-pr: one PR a run
REF=${CHECKUP_REF:-main}
WT=${CHECKUP_WORKTREE:-$HOME/checkup-src}
day=$(date +%m-%d)

dm() {  # the DM: stdin
    if [ -n "$DRY" ]; then echo "--- DM (dry run, not sent):"; cat; else clippa alert send --kind checkup -; fi
}

if [ ! -d "$WT/.git" ] && [ ! -f "$WT/.git" ]; then
    git -C /app worktree prune
    git -C /app worktree add -q --detach "$WT" "origin/$REF" || { echo "can't make $WT" >&2; exit 1; }
fi
git -C "$WT" fetch -q origin "$REF" && git -C "$WT" checkout -q --detach FETCH_HEAD \
    || echo "couldn't update $WT to origin/$REF: running what it has ($(git -C "$WT" log --oneline -1))" >&2
export PYTHONPATH="$WT"
cd "$WT"

if ! clippa checkup -h >/dev/null 2>&1; then
    echo "Checkup $day: origin/$REF has no \`clippa checkup\` yet (#185 not merged?), so nothing was checked." | dm
    exit 0
fi
mark=--mark
[ -n "$DRY" ] && mark=""   # a test run mustn't use up what the next real one reports as new
report=$(clippa checkup --json $mark 2>/tmp/checkup.err)
code=$?
if [ "$code" -eq 0 ]; then
    # and what serve's `checkup --fix` repaired (or would have) in the last 24 h, when anything (#287)
    { echo "Checkup $day: all good."; clippa checkup 2>/dev/null | tail -n +2; } | dm
    exit 0
fi
# Exit 1 is findings only with a report; without one (a CLI error, --mark's write failing) the
# checkup itself broke: say so without a model run.
if [ "$code" -ne 1 ] || ! printf '%s' "$report" | grep -q '"findings"'; then
    echo "Checkup $day: \`clippa checkup\` exited $code: $(tail -n 3 /tmp/checkup.err)" | dm
    exit 0
fi
printf '%s\n' "$report" > /tmp/checkup.json

prompt=$(cat /usr/local/share/clippa/checkup.md)
[ -n "$DRY" ] && prompt="$prompt

DRY RUN: don't run \`clippa alert send\`; print the message as your final answer instead."
# One argument per rule (a rule has spaces in it); in -p mode anything else is refused. Exact
# commands, not prefixes, wherever an extra flag could write (`status --sync`, `checkup --mark`);
# git goes through checkup-git, which refuses the options that write. Logs are read with Read.
# Edit and Write only inside checkup-pr's worktree ("//" = an absolute path in a rule), and git's
# writes only through checkup-pr.
set -- Read Grep Glob "Edit(/$FIX/**)" "Write(/$FIX/**)" "Bash(checkup-pr:*)" \
    "Bash(clippa checkup)" "Bash(clippa checkup --json)" "Bash(clippa status)" \
    "Bash(clippa doctor --offline)" "Bash(clippa doctor --offline --role claude)" \
    "Bash(clippa record status)" "Bash(clippa worker --status)" "Bash(clippa alert send:*)" \
    "Bash(checkup-git:*)"
if ! claude -p "$prompt" --max-turns 40 \
        --disallowedTools NotebookEdit WebFetch WebSearch \
        --allowedTools "$@" </dev/null; then
    # No diagnosis (signed out, usage limit): the findings alone still go out (alert send cuts
    # them to Discord's length by characters).
    clippa checkup 2>&1 | dm
fi
