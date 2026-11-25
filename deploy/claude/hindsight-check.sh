#!/bin/sh
# Is the Hindsight memory bank reachable with this container's key? (#435) Prints nothing and
# exits 0 when it is, or when memory is off (no HINDSIGHT_API_URL); otherwise prints one line
# with the fix and exits 1. The entrypoint runs it at start, checkup.sh each night.
set -u
URL=${HINDSIGHT_API_URL:-}
[ -n "$URL" ] || exit 0
URL=${URL%/}
if ! curl -fsS -m 10 -o /dev/null "$URL/health" 2>/dev/null; then
    echo "Hindsight doesn't answer at $URL/health: is its container up on the NAS?"
    exit 1
fi
code=$(curl -sS -m 10 -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${HINDSIGHT_API_TOKEN:-}" "$URL/v1/default/banks" 2>/dev/null)
case "$code" in
    2??) exit 0 ;;
    401|403) echo "Hindsight refused the key: set HINDSIGHT_API_TOKEN in deploy/claude/env.local (the laptop's apiToken in ~/.hindsight/coding-agent.json) and redeploy" ;;
    *) echo "Hindsight's /v1/default/banks answered HTTP ${code:-nothing}" ;;
esac
exit 1
