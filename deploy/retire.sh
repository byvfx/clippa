#!/usr/bin/env bash
# Sourced by the deploy scripts (deploy/hub, deploy/record, deploy/claude) for #286 pass 3,
# when the compose projects took the Clippa name.
#
#   data_dir <env.local>   the NAS data folder: CLIPPA_DATA, else the old CLIPPER_DATA, else
#                          the default (the folder keeps its name for now)
#   env_setting <env.local> <NAME>   a NAME=value line's value, either spelling (CLIPPA_X
#                          first), without the quotes a dotenv value may have
#   retire_old <host> <old project> <new dir> [sudo]
#       Before the renamed project starts: when the old project (clipper-hub, ...) still has
#       containers on the NAS, stop it (`docker compose down`: containers and network go, named
#       volumes stay), from its own folder ~/<old project> when that is another folder, else by
#       name (`docker compose -p`). Then rename that folder's compose.yaml to
#       compose.yaml.retired-<date>, so nothing (`clippa services`, a stale script) can start it
#       again beside the new one; the folder and anything else in it stay. Two projects at once
#       would run two bots and two workers, whose single-instance locks don't see each other.
#       Exits when the NAS can't be asked or the stop fails, so nothing new starts. Sets
#       RETIRED=1 when it stopped one, so a deploy of some services starts all of them instead.

env_setting() {
    local env_local=$1 name=$2 alt v n
    alt=${name/#CLIPPA_/CLIPPER_}
    for n in "$name" "$alt"; do
        v=$(grep -m1 "^$n=" "$env_local" 2>/dev/null | cut -d= -f2- | tr -d '\r' \
            | sed -E "s/^\"(.*)\"\$/\1/; s/^'(.*)'\$/\1/" || true)
        if [ -n "$v" ]; then echo "$v"; return 0; fi
    done
    return 0
}

data_dir() {
    local d
    d=$(env_setting "$1" CLIPPA_DATA)
    echo "${d:-/volume1/docker/clipper}"
}

RETIRED=0
retire_old() {
    local host=$1 old=$2 new=$3 sudo=${4:-} state count file stamp
    # One line, "<containers> <yes|no>", or nothing when ssh or docker fails: a failure is
    # never read as "no old project".
    state=$(ssh "$host" "ids=\$($sudo docker ps -aq --filter label=com.docker.compose.project=$old) || exit 1; \
        f=no; [ '$old' = '$new' ] || [ ! -f '$old/compose.yaml' ] || f=yes; \
        echo \"\$(printf '%s\n' \"\$ids\" | grep -c .) \$f\"") || state=""
    case $state in
        [0-9]*" yes" | [0-9]*" no") ;;
        *) echo "couldn't ask $host whether the old $old project still runs: nothing started" >&2; exit 1 ;;
    esac
    count=${state%% *}; file=${state##* }
    stamp=$(date +%Y%m%d-%H%M)
    if [ "$count" -gt 0 ]; then
        echo "stopping the old compose project $old (#286): docker compose down"
        if [ "$file" = yes ]; then
            ssh "$host" "cd '$old' && $sudo docker compose down --remove-orphans"
        else
            ssh "$host" "$sudo docker compose -p '$old' down --remove-orphans"
        fi || { echo "couldn't stop the old project $old on $host: nothing started; stop it by hand first" >&2; exit 1; }
        RETIRED=1
    fi
    if [ "$file" = yes ]; then
        echo "retiring ~/$old/compose.yaml as compose.yaml.retired-$stamp"
        ssh "$host" "mv '$old/compose.yaml' '$old/compose.yaml.retired-$stamp'"
    fi
}
