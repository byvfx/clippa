#!/bin/sh
# `checkup-pr start|check|open` for the nightly checkup's Claude run (checkup.sh, #287 step 6): a
# clear code fix becomes at most one PR a run, from its own worktree and branch off origin/main.
# It never merges, never deploys, and never touches the clone a phone session works in.
#
#   checkup-pr start <slug>     a worktree at $CHECKUP_PR_DIR (~/checkup-fix) on checkup/<YYYYMMDD-HHMM>-<slug>
#   checkup-pr check            ruff and the tests there, as CI runs them
#   checkup-pr open <title> -   commit, push and open the PR; its body is stdin
#
# Claude may Edit/Write only inside that worktree (checkup.sh's rules). open refuses a change
# outside clippa/, tests/, docs/wiki/ and CHANGELOG.md, or over MAX_LINES changed lines, and
# anything in a dry run.
set -eu
WT=${CHECKUP_PR_DIR:-$HOME/checkup-fix}
MAX_LINES=${CHECKUP_PR_MAX_LINES:-200}
MARK="$WT.branch"   # this run's branch: checkup.sh clears it before each run (and holds a lock)
die() { echo "checkup-pr: $*" >&2; exit 2; }

case "${1:-}" in
start)
    slug=${2:-}
    printf '%s' "$slug" | grep -Eq '^[a-z0-9][a-z0-9-]{0,39}$' || die "the slug is a-z, 0-9 and -, at most 40"
    [ -s "$MARK" ] && die "this run already has a fix ($(cat "$MARK")): one PR a run"
    branch="checkup/$(date +%Y%m%d-%H%M)-$slug"   # never one an earlier run left behind
    git -C /app fetch -q origin main
    git -C /app worktree remove --force "$WT" 2>/dev/null || rm -rf "$WT"
    git -C /app worktree prune
    git -C /app worktree add -q -b "$branch" "$WT" origin/main || die "can't make $WT on $branch"
    echo "$branch" > "$MARK"
    echo "$WT on $branch (from origin/main): edit there, then checkup-pr check"
    ;;
check)
    [ -s "$MARK" ] || die "no fix started: checkup-pr start <slug>"
    cd "$WT"
    # The tests are code this run wrote: they run with no token, key or credential (no GH_TOKEN,
    # no gh or git login in a throwaway HOME), so a test can't push, merge or spend.
    jail=$(mktemp -d)
    trap 'rm -rf "$jail"' EXIT
    clean() { env -i PATH="/app/.venv/bin:/usr/local/bin:/usr/bin:/bin" HOME="$jail" LANG=C.UTF-8 \
        UV_CACHE_DIR="$jail/uv" "$@"; }
    clean uv tool run ruff@0.17.3 check clipper tests   # the image has uv, not uvx
    clean /app/.venv/bin/python -m pytest -q -p no:cacheprovider
    ;;
open)
    title=${2:-}
    [ -n "$title" ] && [ "${3:-}" = "-" ] || die "usage: checkup-pr open <title> - (the body on stdin)"
    [ -n "${CHECKUP_DRY:-}" ] && die "dry run: no PR (say in the DM what it would have been)"
    [ -s "$MARK" ] || die "no fix started: checkup-pr start <slug>"
    branch=$(cat "$MARK")
    cd "$WT"
    git add -A
    # Against origin/main, index included: a retry after a commit (a failed push or gh) checks
    # the same change and goes on to push and open it.
    git diff --cached --quiet origin/main && die "nothing changed in $WT"
    bad=$(git diff --cached --name-only origin/main | grep -Ev '^(clippa/|tests/|docs/wiki/|CHANGELOG\.md$)' || true)
    [ -z "$bad" ] || die "only clippa/, tests/, docs/wiki/ and CHANGELOG.md: not $bad"
    n=$(git diff --cached --numstat origin/main | awk '{s += $1 + $2} END {print s + 0}')
    [ "$n" -le "$MAX_LINES" ] || die "$n changed lines, over $MAX_LINES: describe it in the DM instead"
    body=$(cat)
    git diff --cached --quiet || git commit -q -m "$title" \
        -m "From the nightly checkup (#287); not yet reviewed by a person." \
        -m "Co-Authored-By: Claude <noreply@anthropic.com>"
    git push -q -u origin "$branch"
    if url=$(gh pr view "$branch" --json url --jq .url 2>/dev/null); then
        echo "$url (already open)"
        exit 0
    fi
    printf '%s\n\nFrom the nightly checkup (deploy/claude/checkup-pr.sh, #287). It never merges or deploys:\nreview it as any PR, then merge and deploy by hand.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n' "$body" \
        | gh pr create --base main --head "$branch" --title "$title" --body-file -
    ;;
*)
    die "start <slug> | check | open <title> -"
    ;;
esac
