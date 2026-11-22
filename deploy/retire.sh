#!/usr/bin/env bash
# Sourced by the deploy scripts (deploy/hub, deploy/record, deploy/claude) for #286 pass 3,
# when the compose projects took the Clippa name.
#
#   data_dir <env.local>   the NAS data folder: CLIPPA_DATA, else the old CLIPPER_DATA, else
#                          the default (the folder keeps its name for now)
#   env_setting <env.local> <NAME>   a NAME=value line's value, either spelling (CLIPPA_X first)
#   retire_old <host> <old dir> <new dir> [sudo]
#       Before the renamed project starts: when the old compose folder is still on the NAS, stop
#       its project (`docker compose down`: containers and network go, named volumes stay)
#       and rename its compose.yaml to compose.yaml.retired-<date>, so nothing (`clippa
#       services`, a stale script) can start it again beside the new one. Only that file is
#       renamed: the folder and anything else in it stay where they are. Two projects at once would run two bots
#       and two workers, whose single-instance locks don't see each other. Does nothing when
#       the old folder is gone or is the folder in use. Exits on a failed stop. Sets RETIRED=1
#       when it stopped one, so a deploy of some services starts all of them instead.

env_setting() {
    local env_local=$1 name=$2 alt v n
    alt=${name/#CLIPPA_/CLIPPER_}
    for n in "$name" "$alt"; do
        v=$(grep -m1 "^$n=" "$env_local" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)
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
    local host=$1 old=$2 new=$3 sudo=${4:-} stamp
    [ "$old" != "$new" ] || return 0
    ssh "$host" "test -f '$old/compose.yaml'" || return 0
    stamp=$(date +%Y%m%d-%H%M)
    echo "retiring the old compose project in ~/$old (#286): docker compose down, then compose.yaml.retired-$stamp"
    ssh "$host" "cd '$old' && $sudo docker compose down --remove-orphans" \
        || { echo "couldn't stop the old project in ~/$old on $host: nothing started; stop it by hand first" >&2; exit 1; }
    ssh "$host" "mv '$old/compose.yaml' '$old/compose.yaml.retired-$stamp'"
    RETIRED=1
}
