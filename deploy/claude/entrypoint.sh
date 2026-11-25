#!/bin/sh
# The claude container's start (deploy/claude): clone the repo on the first start, keep its
# venv in step with uv.lock, set up git and gh, wait for a claude.ai sign-in, then run
# `claude remote-control` in /app.
set -eu

# Remote Control refuses API-key auth; clippa reads its key from [paths] anthropic_key.
unset ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN

# gh reads GH_TOKEN itself; this makes git use it too (the first clone of the private repo,
# then pushes).
if [ -n "${GH_TOKEN:-}" ]; then
    gh auth setup-git >/dev/null 2>&1 || echo "gh auth setup-git failed: git will ask for a login" >&2
fi

if [ ! -d /app/.git ]; then
    REPO=${CLIPPA_REPO:-${CLIPPER_REPO:-}}   # the old spelling, from an env.local before #286 pass 3
    [ -n "$REPO" ] || { echo "CLIPPA_REPO is empty: set it in deploy/claude/env.local" >&2; exit 1; }
    echo "cloning $REPO into /app"
    GIT_TERMINAL_PROMPT=0 git clone "$REPO" /app \
        || { echo "clone failed: a private repo needs GH_TOKEN in deploy/claude/env.local" >&2; exit 1; }
fi

if [ -n "${GIT_USER_NAME:-}" ]; then git config --global user.name "$GIT_USER_NAME"; fi
if [ -n "${GIT_USER_EMAIL:-}" ]; then git config --global user.email "$GIT_USER_EMAIL"; fi

# A broken lock on a branch Claude left mid-change mustn't keep the session from starting.
cd /app
uv sync --frozen --extra modal --extra faces --extra discord \
    || echo "uv sync failed: clippa may be out of date in /app/.venv" >&2

if ! claude auth status >/dev/null 2>&1; then
    echo "Claude Code isn't signed in. On the laptop:"
    echo "  ssh -t nas 'cd clippa-claude && docker compose exec claude claude auth login'"
    echo "waiting for the sign-in..."
    until claude auth status >/dev/null 2>&1; do sleep 10; done
    echo "signed in"
fi

# Memory (#435): the Hindsight bank the laptop's Claude uses, through the same plugin. Its
# settings are all environment (compose.yaml, .env); installing puts its hooks in
# ~/.claude/settings.json and its MCP server in ~/.claude.json, from the image's pinned copy.
# Without HINDSIGHT_API_URL memory is off, and hooks from an earlier start stay quiet.
if [ -n "${HINDSIGHT_API_URL:-}" ]; then
    node /opt/hindsight-coding-agents/dist/installer.js install claude-code </dev/null >/tmp/hindsight-install.log 2>&1 \
        || echo "Hindsight plugin install failed, see /tmp/hindsight-install.log" >&2
    hindsight-check >&2 || true
else
    export HINDSIGHT_DISABLED=1
fi

# The nightly checkup (#185) at CHECKUP_AT (HH:MM, the container's TZ; "" = off), in the
# background beside the session: checkup.sh runs `clippa checkup` and, when it finds
# something, a read-only `claude -p` that DMs one report.
if [ -n "${CHECKUP_AT:-}" ]; then
    (
        while :; do
            now=$(date +%s)
            next=$(date -d "today $CHECKUP_AT" +%s) || { echo "CHECKUP_AT=$CHECKUP_AT isn't HH:MM: no checkup" >&2; exit 0; }
            [ "$next" -gt "$now" ] || next=$(date -d "tomorrow $CHECKUP_AT" +%s)
            sleep $((next - now))
            clippa-checkup >>/tmp/checkup.log 2>&1 || echo "checkup failed, see /tmp/checkup.log" >&2
            clippa-audit >>/tmp/audit.log 2>&1 || echo "audit failed, see /tmp/audit.log" >&2   # its days only
        done
    ) &
fi

# The first start asks to enable Remote Control and to trust /app: answer once with
# `docker attach clippa-claude` (detach with Ctrl-P Ctrl-Q). Both answers are kept in home/.
exec claude remote-control --name "${CLAUDE_SESSION_NAME:-Clippa on the NAS}" \
    --spawn same-dir --permission-mode "${CLAUDE_PERMISSION_MODE:-default}"
