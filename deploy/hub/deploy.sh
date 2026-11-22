#!/usr/bin/env bash
# Deploy or update Clippa on the NAS: serve (war room + jobs), the bot, the worker and
# tailscale (deploy/hub/compose.yaml). Its own compose project: the recorder (deploy/record)
# is never restarted by this.
#
# Run from the repo root:  bash deploy/hub/deploy.sh [service ...]
#   No services = all of them. The image is rebuilt from this checkout (the code, prompts included).
#   HUB_SSH_HOST  ssh host alias (default: $RECORD_SSH_HOST, else nas)
#   HUB_DIR       compose dir on the NAS, relative to the ssh user's home (default: clippa-hub;
#                 the first deploy after #286 pass 3 stops the old clipper-hub project and
#                 retires its compose.yaml, deploy/retire.sh)
#   HUB_SUDO      prefix for docker commands if the ssh user needs it (e.g. "sudo")
#
# clipper.toml is NOT copied: the config and all state live in CLIPPA_DATA on the NAS
# (/data in the containers), edited there (over the share from the laptop). First run
# copies env.template to deploy/hub/env.local (gitignored): the keys, as KEY=value lines.
# A worker stage or upload running at the time is stopped; the worker resumes a stream at
# its first unfinished stage, and an upload that didn't finish goes again on the next run.
set -euo pipefail

HOST=${HUB_SSH_HOST:-${RECORD_SSH_HOST:-nas}}
DIR=${HUB_DIR:-clippa-hub}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
ENV_LOCAL=$HERE/env.local

if [ ! -f "$ENV_LOCAL" ]; then
    cp "$HERE/env.template" "$ENV_LOCAL"
    chmod 600 "$ENV_LOCAL"
    echo "created $ENV_LOCAL: fill in the keys, then run this again" >&2
    exit 1
fi

source "$HERE/../keys.sh"
source "$HERE/../retire.sh"
check_keys "$ENV_LOCAL"   # by name, never a value; DEPLOY_KEYS=sync|keep (deploy/keys.sh)

DATA=$(data_dir "$ENV_LOCAL")   # CLIPPA_DATA, or an older env.local's CLIPPER_DATA

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/src"
cp "$ROOT/pyproject.toml" "$ROOT/uv.lock" "$HERE/Dockerfile" "$TMP/src/"
cp -r "$ROOT/clippa" "$TMP/src/clippa"
find "$TMP/src" -name __pycache__ -prune -exec rm -rf {} +
source "$HERE/../stamp.sh"
stamp_build "$ROOT" "$TMP/src"   # which commit the image is: clippa --version, the heartbeats
cp "$HERE/compose.yaml" "$HERE/tailscale-serve.json" "$TMP/"
cp "$ENV_LOCAL" "$TMP/.env"
printf '\nCLIPPA_DATA=%s\n' "$DATA" >> "$TMP/.env"   # compose.yaml's mounts read this spelling

tar -C "$TMP" -czf - . | ssh "$HOST" "mkdir -p '$DIR' && rm -rf '$DIR/src' && tar -C '$DIR' -xzf - \
    && chmod 600 '$DIR/.env' && mkdir -p '$DATA/fonts' '$DATA/logs'"
ssh "$HOST" "test -f '$DATA/clipper.toml'" \
    || { echo "no $DATA/clipper.toml on $HOST: the config lives there (see the NAS-hub wiki page)" >&2; exit 1; }
ssh "$HOST" "cd '$DIR' && ${HUB_SUDO:-} docker compose build serve"
retire_old "$HOST" clipper-hub "$DIR" "${HUB_SUDO:-}"   # after the build, so the old one runs until then
if [ "$RETIRED" = 1 ] && [ $# -gt 0 ]; then
    echo "the old project is down, so every service starts, not only: $*"
    set --
fi
ssh "$HOST" "cd '$DIR' && ${HUB_SUDO:-} docker compose up -d --remove-orphans $* && ${HUB_SUDO:-} docker compose ps"
echo "logs: ssh $HOST 'cd $DIR && docker compose logs -f serve bot worker'"
