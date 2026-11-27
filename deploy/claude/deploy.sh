#!/usr/bin/env bash
# Deploy or update Claude Code on the NAS (deploy/claude/compose.yaml): a `claude
# remote-control` session in its own clone of the repo. Its own compose project: the recorder
# and the hub are never restarted by this. Restarting it ends the session that is open in the
# Claude app (the clone and Claude's sign-in stay).
#
# Run from the repo root:  bash deploy/claude/deploy.sh [--force] [--env PATH]
#   Refused when this checkout is behind origin/main (deploy/mainguard.sh): the image is built
#   from this checkout's deploy/claude files. --force deploys it anyway.
#   --env PATH       the env.local to deploy (default: deploy/claude/env.local here), so a
#                    worktree of main can deploy with the main checkout's env.local
#   CLAUDE_SSH_HOST  ssh host alias (default: $HUB_SSH_HOST, else $RECORD_SSH_HOST, else nas)
#   CLAUDE_COMPOSE   compose dir on the NAS, relative to the ssh user's home (default: clippa-claude;
#                    the first deploy after #286 pass 3 stops the old clipper-claude project and
#                    retires its compose.yaml, deploy/retire.sh; CLAUDE_DIR stays as it is)
#   HUB_SUDO         prefix for docker commands if the ssh user needs it (e.g. "sudo")
#
# The image holds no clippa code: the entrypoint clones CLIPPA_REPO into CLAUDE_DIR/repo
# on first start and leaves it alone after that (Claude pulls, branches and pushes there).
# First run copies env.template to deploy/claude/env.local (gitignored): the keys.
set -euo pipefail

HOST=${CLAUDE_SSH_HOST:-${HUB_SSH_HOST:-${RECORD_SSH_HOST:-nas}}}
DIR=${CLAUDE_COMPOSE:-clippa-claude}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
ENV_LOCAL=$HERE/env.local
FORCE=
while [ $# -gt 0 ]; do
    case $1 in
        --force) FORCE=1 ;;
        --env) [ $# -ge 2 ] || { echo "--env needs a path" >&2; exit 2; }; ENV_LOCAL=$2; shift ;;
        --env=*) ENV_LOCAL=${1#--env=} ;;
        *) echo "usage: bash deploy/claude/deploy.sh [--force] [--env PATH]" >&2; exit 2 ;;
    esac
    shift
done
source "$HERE/../mainguard.sh"
require_main "$ROOT" deploy/claude/deploy.sh "$FORCE"

if [ "$ENV_LOCAL" != "$HERE/env.local" ] && [ ! -f "$ENV_LOCAL" ]; then
    echo "no $ENV_LOCAL (--env)" >&2
    exit 1
fi
need_env "$ROOT" "$ENV_LOCAL" deploy/claude/env.local   # a worktree of main: the main checkout's, by --env
if [ ! -f "$ENV_LOCAL" ]; then
    cp "$HERE/env.template" "$ENV_LOCAL"
    chmod 600 "$ENV_LOCAL"
    echo "created $ENV_LOCAL: fill in the keys, then run this again" >&2
    exit 1
fi
source "$HERE/../keys.sh"
source "$HERE/../retire.sh"
check_keys "$ENV_LOCAL"   # by name, never a value; DEPLOY_KEYS=sync|keep (deploy/keys.sh)
# KEY's value in env.local, without the quotes a dotenv value may have (KEY="value")
setting() {
    grep "^$1=" "$ENV_LOCAL" | tail -1 | cut -d= -f2- | sed -E "s/^\"(.*)\"\$/\1/; s/^'(.*)'\$/\1/" || true
}
DATA=$(setting CLIPPA_DATA); DATA=${DATA:-$(setting CLIPPER_DATA)}; DATA=${DATA:-/volume1/docker/clipper}
CDIR=$(setting CLAUDE_DIR); CDIR=${CDIR:-/volume1/docker/clipper-claude}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/src"
cp "$HERE/Dockerfile" "$HERE/entrypoint.sh" "$HERE/hindsight-check.sh" "$HERE/checkup.sh" "$HERE/checkup-git.sh" "$HERE/checkup-pr.sh" "$HERE/checkup.md" \
    "$HERE/audit.sh" "$HERE/audit-deep.md" "$TMP/src/"
cp "$HERE/compose.yaml" "$TMP/"
cp "$ENV_LOCAL" "$TMP/.env"
printf '\nCLIPPA_DATA=%s\n' "$DATA" >> "$TMP/.env"   # compose.yaml's mounts read this spelling
# The repo to clone and who commits there default to this checkout's.
fill() {  # fill KEY's empty line in .env (or add one) with a value
    grep -v "^$1=" "$TMP/.env" > "$TMP/env.new" || true
    echo "$1=$2" >> "$TMP/env.new"
    mv "$TMP/env.new" "$TMP/.env"
}
[ -n "$(setting CLIPPA_REPO)$(setting CLIPPER_REPO)" ] || fill CLIPPA_REPO "$(git -C "$ROOT" remote get-url origin | sed -E 's#^git@github.com:#https://github.com/#')"
[ -n "$(setting GIT_USER_NAME)" ] || fill GIT_USER_NAME "$(git -C "$ROOT" config user.name || true)"
[ -n "$(setting GIT_USER_EMAIL)" ] || fill GIT_USER_EMAIL "$(git -C "$ROOT" config user.email || true)"
# Memory (#435): the laptop's Hindsight server, key and bank, wherever env.local leaves one empty,
# so both Claudes share one bank. HINDSIGHT_API_URL=off turns it off.
HSCFG=${HINDSIGHT_CONFIG:-$HOME/.hindsight/coding-agent.json}
hs_conf() {  # a string field of the laptop's Hindsight config (one JSON file), or nothing
    [ -f "$HSCFG" ] || return 0
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$HSCFG" | head -1
}
HS_URL=$(setting HINDSIGHT_API_URL)
if [ "$HS_URL" = off ]; then
    fill HINDSIGHT_API_URL ""
elif [ -z "$HS_URL" ]; then
    # The laptop's server, and its mode with it.
    HS_URL=$(hs_conf apiUrl)
    case "$(hs_conf serverMode):$HS_URL" in
        daemon:*) HS_URL=""; echo "note: the laptop's Hindsight is a local daemon the NAS can't reach: memory stays off there" >&2 ;;
        *://localhost*|*://127.*|*://\[::1\]*)
            HS_URL=""; echo "note: the laptop's Hindsight is on the laptop itself ($(hs_conf apiUrl)): set HINDSIGHT_API_URL in $ENV_LOCAL to an address the NAS can reach, and HINDSIGHT_API_TOKEN if it takes a key" >&2 ;;
    esac
    fill HINDSIGHT_API_URL "$HS_URL"
    if [ -n "$HS_URL" ]; then
        [ -n "$(setting HINDSIGHT_SERVER_MODE)" ] || fill HINDSIGHT_SERVER_MODE "$(hs_conf serverMode | sed 's/^$/self-hosted/')"
    fi
fi
if [ -n "$HS_URL" ] && [ "$HS_URL" != off ]; then
    [ -n "$(setting HINDSIGHT_SERVER_MODE)" ] || grep -q '^HINDSIGHT_SERVER_MODE=.' "$TMP/.env" || fill HINDSIGHT_SERVER_MODE self-hosted
    # The laptop's key goes only with the laptop's server; any other server's comes from env.local.
    if [ -z "$(setting HINDSIGHT_API_TOKEN)" ]; then
        LAPTOP_URL=$(hs_conf apiUrl)
        if [ "${HS_URL%/}" = "${LAPTOP_URL%/}" ]; then
            fill HINDSIGHT_API_TOKEN "$(hs_conf apiToken)"
        elif [ -n "$(hs_conf apiToken)" ]; then
            echo "note: HINDSIGHT_API_TOKEN is empty in $ENV_LOCAL and $HS_URL isn't the laptop's server ($LAPTOP_URL): set it there if that server takes a key" >&2
        fi
    fi
    # The plugin's own default, coding-agent::<the main checkout's folder>, unless it names a bank
    BANK=$(hs_conf bankId)
    [ -n "$BANK" ] || BANK="coding-agent::$(basename "$(dirname "$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)")")"
    [ -n "$(setting HINDSIGHT_BANK_ID)" ] || fill HINDSIGHT_BANK_ID "$BANK"
fi
# The Anthropic key goes in as a file (a compose secret), never in the environment: Remote
# Control refuses to start where ANTHROPIC_API_KEY is set.
KEY=$(setting ANTHROPIC_API_KEY)
[ -n "$KEY" ] || { echo "ANTHROPIC_API_KEY is empty in $ENV_LOCAL (copy it from deploy/hub/env.local)" >&2; exit 1; }
(umask 077 && printf '%s\n' "$KEY" > "$TMP/anthropic_key")
grep -v '^ANTHROPIC_API_KEY=' "$TMP/.env" > "$TMP/env.new" || true
mv "$TMP/env.new" "$TMP/.env"

ssh "$HOST" "test -f '$DATA/clipper.toml'" \
    || { echo "no $DATA/clipper.toml on $HOST: the config lives there (see the NAS-hub wiki page)" >&2; exit 1; }
tar -C "$TMP" -czf - . | ssh "$HOST" "mkdir -p '$DIR' '$CDIR/repo' '$CDIR/home' && rm -rf '$DIR/src' \
    && tar -C '$DIR' -xzf - && chmod 600 '$DIR/.env' '$DIR/anthropic_key' && chmod 700 '$CDIR/home'"
ssh "$HOST" "cd '$DIR' && ${HUB_SUDO:-} docker compose build --build-arg CLAUDE_CODE_BUILD=$(date +%F)"
retire_old "$HOST" clipper-claude "$DIR" "${HUB_SUDO:-}"   # after the build; this ends the open session
ssh "$HOST" "cd '$DIR' && ${HUB_SUDO:-} docker compose up -d --remove-orphans && ${HUB_SUDO:-} docker compose ps"
echo "logs: ssh $HOST 'cd $DIR && docker compose logs -f claude'"
echo "first start: sign in with  ssh -t $HOST 'cd $DIR && docker compose exec claude claude auth login'"
echo "then answer its prompts once with  ssh -t $HOST 'docker attach clippa-claude'  (Ctrl-P Ctrl-Q detaches)"
