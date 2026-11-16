# Sourced by deploy/hub/deploy.sh and deploy/claude/deploy.sh: check_keys <env.local>.
#
# An env.local is a copy of keys this machine also holds (Windows user variables, or the
# shell's environment elsewhere). A key changed here but not there ships the old one, and every
# model call on the NAS fails (2026-11-16: a 401 on the hub and in the claude container).
# check_keys compares each *_KEY, *_TOKEN, *_TOKEN_ID, *_SECRET and *_WEBHOOK line with this
# machine's value of the same name, by name only: no value is ever printed. A name this machine
# has no value for isn't compared (GH_TOKEN, the Modal token: only env.local has them).
#   DEPLOY_KEYS=sync  copy this machine's values into env.local, then deploy
#   DEPLOY_KEYS=keep  deploy env.local as it is
#   unset             ask (at a terminal), else stop and say which keys differ

own_keys() {  # NAME<tab>VALUE for each name in $1 this machine has a non-empty value for
    if command -v powershell.exe >/dev/null 2>&1; then
        NAMES="$1" powershell.exe -NoProfile -Command '$env:NAMES -split "\s+" | Where-Object { $_ } |
            ForEach-Object { $v = [Environment]::GetEnvironmentVariable($_, "User"); if ($v) { "$_`t$v" } }' \
            | tr -d '\r'
    else
        local n
        for n in $1; do [ -n "${!n:-}" ] && printf '%s\t%s\n' "$n" "${!n}"; done
    fi
    return 0
}

check_keys() {
    local env_local=$1 names own differ="" name mine theirs choice yn
    names=$(grep -oE '^[A-Z0-9_]+(_KEY|_TOKEN|_TOKEN_ID|_SECRET|_WEBHOOK)=' "$env_local" | tr -d '=' | tr '\n' ' ' || true)
    own=$(own_keys "$names" || true)
    while IFS=$'\t' read -r name mine; do
        [ -n "$name" ] || continue
        theirs=$(grep -m1 "^$name=" "$env_local" | cut -d= -f2- | tr -d '\r')
        theirs=${theirs#\"}; theirs=${theirs%\"}; theirs=${theirs#\'}; theirs=${theirs%\'}
        [ "$theirs" = "$mine" ] || differ="$differ $name"
    done <<< "$own"
    [ -n "$differ" ] || return 0
    echo "$env_local differs from this machine's own:$differ" >&2
    choice=${DEPLOY_KEYS:-}
    if [ -z "$choice" ] && [ -t 0 ]; then
        read -r -p "copy this machine's values into it first? [y/N] " yn
        case $yn in [yY]*) choice=sync ;; *) choice=keep ;; esac
    fi
    case $choice in
        sync)
            for name in $differ; do
                mine=$(printf '%s\n' "$own" | grep -m1 "^$name"$'\t' | cut -f2-)
                # umask 077: the copy holds every key, so it's private from its first byte
                (umask 077 && K="$name" V="$mine" awk 'BEGIN { k = ENVIRON["K"]; v = ENVIRON["V"] }
                    index($0, k "=") == 1 { print k "=" v; next } { print }' "$env_local" > "$env_local.tmp")
                mv "$env_local.tmp" "$env_local"
            done
            chmod 600 "$env_local"
            echo "copied:$differ" >&2 ;;
        keep) echo "deploying it as it is" >&2 ;;
        *) echo "DEPLOY_KEYS=sync copies this machine's values, DEPLOY_KEYS=keep deploys it as is" >&2
           exit 1 ;;
    esac
}
