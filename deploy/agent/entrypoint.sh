#!/bin/sh
# The agent container's start (deploy/agent): clone the repo on the first start, keep its
# venv in step with uv.lock, set up git and gh, then wait. The checkup clock runs only when
# CHECKUP_AT is set. The default is empty, so a first boot is dark and does not overlap
# clippa-claude.
set -eu

# The key is a file ([paths] openrouter_key). A password or an API key in the environment
# would show up in `docker inspect` and in logs. Drop them even if a deploy missed one.
unset OPENROUTER_API_KEY ANTHROPIC_API_KEY OPENCODE_PASSWORD CLAUDE_CODE_OAUTH_TOKEN

# gh reads GH_TOKEN itself; this makes git use it too (the first clone, then pushes).
if [ -n "${GH_TOKEN:-}" ]; then
    gh auth setup-git >/dev/null 2>&1 || echo "gh auth setup-git failed: git will ask for a login" >&2
fi

if [ ! -d /app/.git ]; then
    REPO=${CLIPPA_REPO:-${CLIPPER_REPO:-}}
    [ -n "$REPO" ] || { echo "CLIPPA_REPO is empty: set it in deploy/agent/env.local" >&2; exit 1; }
    echo "cloning $REPO into /app"
    GIT_TERMINAL_PROMPT=0 git clone "$REPO" /app \
        || { echo "clone failed: a private repo needs GH_TOKEN in deploy/agent/env.local" >&2; exit 1; }
fi

if [ -n "${GIT_USER_NAME:-}" ]; then git config --global user.name "$GIT_USER_NAME"; fi
if [ -n "${GIT_USER_EMAIL:-}" ]; then git config --global user.email "$GIT_USER_EMAIL"; fi

cd /app
uv sync --frozen --extra modal --extra faces --extra discord \
    || echo "uv sync failed: clippa may be out of date in /app/.venv" >&2

# Phone bridge. Pick one in review; the default is Option A and this file does not start
# the other. Do not enable both, and do not publish a host port in compose.yaml.
#
# Option A (default): no phone UI in this container. The phone stays on clippa-claude
# (`claude remote-control` in deploy/claude). This process runs the checkup clock when
# CHECKUP_AT is set, and otherwise waits. Nothing listens.
#
# Option B (not enabled): an OpenCode server on loopback only, for a phone that is already
# on the NAS. If it is turned on later it must bind 127.0.0.1:4096 and no other address,
# publish no host port, and never set OPENCODE_PASSWORD (it would be in the environment
# and in the logs). Leave the line below commented until that choice is made.
#
# opencode serve --hostname 127.0.0.1 --port 4096

if [ -z "${CHECKUP_AT:-}" ]; then
    echo "clippa-agent idle: CHECKUP_AT is empty (burn-in, no overlap with clippa-claude)"
    while :; do sleep 3600; done
fi

echo "clippa-agent checkup at $CHECKUP_AT"
while :; do
    now=$(date +%s)
    next=$(date -d "today $CHECKUP_AT" +%s) || { echo "CHECKUP_AT=$CHECKUP_AT isn't HH:MM: idle" >&2; while :; do sleep 3600; done; }
    [ "$next" -gt "$now" ] || next=$(date -d "tomorrow $CHECKUP_AT" +%s)
    sleep $((next - now))
    clippa-checkup >>/tmp/checkup.log 2>&1 || echo "checkup failed, see /tmp/checkup.log" >&2
    clippa-audit >>/tmp/audit.log 2>&1 || echo "audit failed, see /tmp/audit.log" >&2
done
