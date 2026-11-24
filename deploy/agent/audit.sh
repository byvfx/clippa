#!/bin/sh
# The weekly digest and the monthly deep audit, run by the agent container after the morning
# checkup (entrypoint.sh). Weekly on AUDIT_WEEKDAY: `clippa audit --save`, no model, DM'd as
# it is. Monthly on AUDIT_MONTHDAY: `clippa audit --brief` goes to `agent-run --role audit`,
# which is read-only. Either one empty = off.
#
# Like checkup.sh it runs CHECKUP_REF's code (default main) from the worktree checkup.sh keeps.
#
# By hand: docker compose exec agent clippa-audit [--weekly] [--deep] [--dry-run]
set -u
DRY="" WEEK="" DEEP=""
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        --weekly) WEEK=1 ;;
        --deep) DEEP=1 ;;
        *) echo "clippa-audit: [--weekly] [--deep] [--dry-run]" >&2; exit 2 ;;
    esac
done
if [ -z "$WEEK$DEEP" ]; then
    [ "$(date +%u)" = "${AUDIT_WEEKDAY-7}" ] && WEEK=1
    [ "$(date +%-d)" = "${AUDIT_MONTHDAY-1}" ] && DEEP=1
fi
[ -n "$WEEK$DEEP" ] || exit 0

REF=${CHECKUP_REF:-main}
WT=${CHECKUP_WORKTREE:-$HOME/checkup-src}
month=$(date +%Y-%m)

dm() {
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

if ! clippa audit -h >/dev/null 2>&1; then
    echo "origin/$REF has no \`clippa audit\` yet: nothing run" >&2
    exit 0
fi

weekly() {
    save=--save
    [ -n "$DRY" ] && save=""
    if out=$(clippa audit $save 2>/tmp/audit.err); then
        printf '%s\n' "$out" | dm
    else
        echo "Weekly audit: \`clippa audit\` failed: $(tail -n 3 /tmp/audit.err)" | dm
    fi
}

deep() {
    if ! clippa audit --brief >/tmp/audit-brief.md 2>/tmp/audit.err; then
        echo "Deep audit $month: \`clippa audit --brief\` failed: $(tail -n 3 /tmp/audit.err)" | dm
        return
    fi
    if ! grep -q '^# Deep audit brief' /tmp/audit-brief.md; then
        echo "Deep audit $month: no weekly digests saved yet, so nothing to go on." | dm
        return
    fi
    prompt=$(cat /usr/local/share/clippa/audit-deep.md)
    [ -n "$DRY" ] && prompt="$prompt

DRY RUN: do not call \`checkup-clippa alert send\`. Put the message at the top of your final answer."
    if ! printf '%s\n' "$prompt" | agent-run --role audit ${DRY:+--dry-run} >/tmp/audit-deep.md; then
        { echo "Deep audit $month: the model couldn't run, so here are the repeat problems only."
          sed -n '/^## Problems/,/^## Week by week/p' /tmp/audit-brief.md | grep '^- ' | cut -c1-300 | head -n 5
        } | dm
        return
    fi
    if [ -n "$DRY" ]; then
        cat /tmp/audit-deep.md
    elif ! clippa audit --keep-deep /tmp/audit-deep.md 2>/tmp/audit.err; then
        echo "Deep audit $month: the report wasn't saved: $(tail -n 3 /tmp/audit.err)" | dm
    fi
}

[ -n "$WEEK" ] && weekly
[ -n "$DEEP" ] && deep
exit 0
