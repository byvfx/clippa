#!/usr/bin/env bash
# Deploy or update the live recorder (`clippa record watch`) on the NAS.
#
# Run from the repo root:  bash deploy/record/deploy.sh [--force]
#   Refused while a show is being recorded (`clippa record status` exits 3) and when the
#   recorder's state can't be read (exit 1): the restart starts a new recording part. --force
#   deploys anyway.
#   RECORD_SSH_HOST  ssh host alias (default: nas)
#   RECORD_DIR       compose dir on the NAS, relative to the ssh user's home (default: clippa-record;
#                    the first deploy after #286 pass 3 stops the old clipper-record project and
#                    retires its compose.yaml, deploy/retire.sh)
#   CLIPPA_TOML      a config to install as $CLIPPA_DATA/clipper.toml (the old CLIPPER_TOML too). Unset: the one there is
#                    kept (since 2026-11-05 it is the only copy, shared with deploy/hub; edit it
#                    there), and ./clipper.toml is copied only if there is none yet. The
#                    recorder reads [record] and [kick]; the image pins paths.streams to
#                    /data/streams (= $CLIPPA_DATA/streams), whatever the file says
#   RECORD_SUDO      prefix for docker commands if the ssh user needs it (e.g. "sudo")
#
# First run copies env.template to deploy/record/env.local (gitignored; alert webhook or
# bot token). Recordings in progress pause on the restart and resume, as a new part,
# when the new container starts.
set -euo pipefail

HOST=${RECORD_SSH_HOST:-nas}
DIR=${RECORD_DIR:-clippa-record}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
TOML=${CLIPPA_TOML:-${CLIPPER_TOML:-}}
source "$HERE/../retire.sh"
ENV_LOCAL=$HERE/env.local

if [ "${1:-}" != "--force" ]; then
    CLIPPA=
    for c in "$ROOT/.venv/Scripts/clippa.exe" "$ROOT/.venv/bin/clippa" "$ROOT/.venv/Scripts/clipper.exe" "$ROOT/.venv/bin/clipper"; do
        [ -x "$c" ] && { CLIPPA=$c; break; }
    done
    [ -n "$CLIPPA" ] || { echo "no clippa in $ROOT/.venv: run deploy/bootstrap, or deploy with --force" >&2; exit 1; }
    set +e
    (cd "$ROOT" && "$CLIPPA" record status)
    rc=$?
    set -e
    if [ $rc -eq 3 ]; then
        echo "a show is being recorded: deploy after it ends, or: bash deploy/record/deploy.sh --force" >&2
        exit 1
    elif [ $rc -ne 0 ]; then
        echo "couldn't read the recorder's state (exit $rc); to deploy anyway: bash deploy/record/deploy.sh --force" >&2
        exit 1
    fi
fi

if [ ! -f "$ENV_LOCAL" ]; then
    cp "$HERE/env.template" "$ENV_LOCAL"
    chmod 600 "$ENV_LOCAL"
    echo "created $ENV_LOCAL (add CLIPPA_ALERT_WEBHOOK for Discord alerts; an older env.local's CLIPPER_ALERT_WEBHOOK works too)"
fi
DATA=$(data_dir "$ENV_LOCAL")   # CLIPPA_DATA, or an older env.local's CLIPPER_DATA

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/src"
cp "$ROOT/pyproject.toml" "$ROOT/uv.lock" "$TMP/src/"
cp "$HERE/Dockerfile" "$TMP/src/"
cp -r "$ROOT/clippa" "$TMP/src/clippa"
find "$TMP/src" -name __pycache__ -prune -exec rm -rf {} +
source "$HERE/../stamp.sh"
stamp_build "$ROOT" "$TMP/src"   # which commit the image is: clippa --version, the heartbeat
cp "$HERE/compose.yaml" "$TMP/"
cp "$ENV_LOCAL" "$TMP/.env"
printf '\nCLIPPA_DATA=%s\n' "$DATA" >> "$TMP/.env"   # compose.yaml's mount reads this spelling

tar -C "$TMP" -czf - . | ssh "$HOST" "mkdir -p '$DIR' && rm -rf '$DIR/src' && tar -C '$DIR' -xzf - \
    && chmod 600 '$DIR/.env' && mkdir -p '$DATA/streams'"
if [ -n "$TOML" ]; then
    ssh "$HOST" "cat > '$DATA/clipper.toml'" < "$TOML"   # UGOS sftp is chrooted; plain ssh isn't
elif ! ssh "$HOST" "test -f '$DATA/clipper.toml'"; then
    ssh "$HOST" "cat > '$DATA/clipper.toml'" < "$ROOT/clipper.toml"
fi
ssh "$HOST" "cd '$DIR' && ${RECORD_SUDO:-} docker compose build"
retire_old "$HOST" clipper-record "$DIR" "${RECORD_SUDO:-}"   # after the build, so the old one records until then
ssh "$HOST" "cd '$DIR' && ${RECORD_SUDO:-} docker compose up -d --remove-orphans && ${RECORD_SUDO:-} docker compose ps"
echo "logs: ssh $HOST 'cd $DIR && docker compose logs -f'"
