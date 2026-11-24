#!/usr/bin/env bash
# Deploy or update the agent container on the NAS (deploy/agent/compose.yaml): OpenCode,
# headless, in its own clone. Its own compose project: the recorder, the hub and
# clippa-claude are never restarted by this.
#
# Run from the repo root:  bash deploy/agent/deploy.sh
#   AGENT_SSH_HOST   ssh host alias (default: $HUB_SSH_HOST, else $RECORD_SSH_HOST, else nas)
#   AGENT_COMPOSE    compose dir on the NAS, relative to the ssh user's home (default: clippa-agent)
#   HUB_SUDO         prefix for docker commands if the ssh user needs it (e.g. "sudo")
#
# The image holds no clippa code: the entrypoint clones CLIPPA_REPO into AGENT_DIR/repo
# on first start. First run copies env.template to deploy/agent/env.local (gitignored).
# OPENROUTER_API_KEY is written to a compose secret and removed from the container's .env.
set -euo pipefail

HOST=${AGENT_SSH_HOST:-${HUB_SSH_HOST:-${RECORD_SSH_HOST:-nas}}}
DIR=${AGENT_COMPOSE:-clippa-agent}
HERE=$(cd "$(dirname "$0")" && pwd)
ENV_LOCAL=$HERE/env.local

if [ ! -f "$ENV_LOCAL" ]; then
    cp "$HERE/env.template" "$ENV_LOCAL"
    chmod 600 "$ENV_LOCAL"
    echo "created $ENV_LOCAL: fill in the keys, then run this again" >&2
    exit 1
fi
source "$HERE/../keys.sh"
check_keys "$ENV_LOCAL"   # by name, never a value; DEPLOY_KEYS=sync|keep (deploy/keys.sh)
# KEY's value in env.local, without the quotes a dotenv value may have (KEY="value")
setting() {
    grep "^$1=" "$ENV_LOCAL" | tail -1 | cut -d= -f2- | sed -E "s/^\"(.*)\"\$/\1/; s/^'(.*)'\$/\1/" || true
}
DATA=$(setting CLIPPA_DATA); DATA=${DATA:-$(setting CLIPPER_DATA)}; DATA=${DATA:-/volume1/docker/clipper}
ADIR=$(setting AGENT_DIR); ADIR=${ADIR:-/volume1/docker/clippa-agent}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/src"
cp "$HERE/Dockerfile" "$HERE/entrypoint.sh" "$HERE/checkup.sh" "$HERE/checkup-git.sh" \
    "$HERE/checkup-pr.sh" "$HERE/checkup-clippa.sh" "$HERE/agent-run.sh" "$HERE/checkup.md" \
    "$HERE/audit.sh" "$HERE/audit-deep.md" "$TMP/src/"
cp "$HERE/compose.yaml" "$TMP/"
cp "$ENV_LOCAL" "$TMP/.env"
printf '\nCLIPPA_DATA=%s\n' "$DATA" >> "$TMP/.env"
fill() {  # fill KEY's empty line in .env (or add one) with a value
    grep -v "^$1=" "$TMP/.env" > "$TMP/env.new" || true
    echo "$1=$2" >> "$TMP/env.new"
    mv "$TMP/env.new" "$TMP/.env"
}
[ -n "$(setting CLIPPA_REPO)$(setting CLIPPER_REPO)" ] || fill CLIPPA_REPO "$(git remote get-url origin | sed -E 's#^git@github.com:#https://github.com/#')"
[ -n "$(setting GIT_USER_NAME)" ] || fill GIT_USER_NAME "$(git config user.name || true)"
[ -n "$(setting GIT_USER_EMAIL)" ] || fill GIT_USER_EMAIL "$(git config user.email || true)"
# The OpenRouter key is a compose secret, never an environment variable. The same for the
# two names that must not ride along if someone pasted them into env.local.
KEY=$(setting OPENROUTER_API_KEY)
[ -n "$KEY" ] || { echo "OPENROUTER_API_KEY is empty in $ENV_LOCAL" >&2; exit 1; }
(umask 077 && printf '%s\n' "$KEY" > "$TMP/openrouter_key")
grep -vE '^(OPENROUTER_API_KEY|ANTHROPIC_API_KEY|OPENCODE_PASSWORD)=' "$TMP/.env" > "$TMP/env.new" || true
mv "$TMP/env.new" "$TMP/.env"

ssh "$HOST" "test -f '$DATA/clipper.toml'" \
    || { echo "no $DATA/clipper.toml on $HOST: the config lives there (see the NAS-hub wiki page)" >&2; exit 1; }
tar -C "$TMP" -czf - . | ssh "$HOST" "mkdir -p '$DIR' '$ADIR/repo' '$ADIR/home' && rm -rf '$DIR/src' \
    && tar -C '$DIR' -xzf - && chmod 600 '$DIR/.env' '$DIR/openrouter_key' && chmod 700 '$ADIR/home'"
ssh "$HOST" "cd '$DIR' && ${HUB_SUDO:-} docker compose build --build-arg OPENCODE_BUILD=$(date +%F)"
ssh "$HOST" "cd '$DIR' && ${HUB_SUDO:-} docker compose up -d --remove-orphans && ${HUB_SUDO:-} docker compose ps"
echo "logs: ssh $HOST 'cd $DIR && docker compose logs -f agent'"
echo "this does not restart clippa-claude. CHECKUP_AT is empty until you set it, so the clock stays dark."
echo "check: ssh $HOST 'cd $DIR && docker compose exec agent clippa doctor --role agent --offline'"
