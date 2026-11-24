#!/bin/sh
# The nightly checkup, run by the agent container at CHECKUP_AT (entrypoint.sh).
# `clippa checkup` first, no model: nothing found = a one-line "all good" DM and no model run;
# findings = a headless `agent-run --role checkup` that reads the logs and DMs one report.
# That run may edit only checkup-pr's worktree. The tools it may use are the OpenCode rules
# agent-run writes, and the commands checkup-clippa allows.
#
# It runs CHECKUP_REF's code (default main) from its own detached worktree, updated each run.
# Until this branch is on main, set CHECKUP_REF to the burn-in branch: agent-config has to be
# in that tree. PYTHONPATH puts the worktree's clippa ahead of /app's in the shared venv.
#
# By hand: docker compose exec agent clippa-checkup [--dry-run]
#   --dry-run prints the DM instead of sending it (the model still runs when there are findings).
set -u
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY=1
exec 9>/tmp/checkup.lock
flock -n 9 || { echo "a checkup is already running" >&2; exit 0; }
[ -n "$DRY" ] && export CHECKUP_DRY=1 CHECKUP_PR_DIR="${CHECKUP_PR_DIR:-$HOME/checkup-fix}-dry"
FIX=${CHECKUP_PR_DIR:-$HOME/checkup-fix}
rm -f "$FIX.branch"
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
mkdir -p "$HOME/.checkup-bin"
printf '#!/bin/sh\nexec python -m clippa.cli "$@"\n' > "$HOME/.checkup-bin/clippa.$$"
chmod 755 "$HOME/.checkup-bin/clippa.$$" && mv -f "$HOME/.checkup-bin/clippa.$$" "$HOME/.checkup-bin/clippa"
export PATH="$HOME/.checkup-bin:$PATH"
cd "$WT"

if ! clippa checkup -h >/dev/null 2>&1; then
    echo "Checkup $day: origin/$REF has no \`clippa checkup\` yet, so nothing was checked." | dm
    exit 0
fi
mark=--mark
[ -n "$DRY" ] && mark=""
report=$(clippa checkup --json $mark 2>/tmp/checkup.err)
code=$?
if [ "$code" -eq 0 ]; then
    { echo "Checkup $day: all good."; clippa checkup 2>/dev/null | tail -n +2; } | dm
    exit 0
fi
if [ "$code" -ne 1 ] || ! printf '%s' "$report" | grep -q '"findings"'; then
    echo "Checkup $day: \`clippa checkup\` exited $code: $(tail -n 3 /tmp/checkup.err)" | dm
    exit 0
fi
printf '%s\n' "$report" > /tmp/checkup.json

prompt=$(cat /usr/local/share/clippa/checkup.md)
[ -n "$DRY" ] && prompt="$prompt

DRY RUN: do not call \`checkup-clippa alert send\`. Print the message as your final answer."
if ! printf '%s\n' "$prompt" | agent-run --role checkup --fix-dir "$FIX" ${DRY:+--dry-run}; then
    clippa checkup 2>&1 | dm
fi
