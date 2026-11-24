#!/bin/sh
# Read-only clippa commands for the headless agent. The command is the argument list, never
# a shell string. Anything that is not an exact allowlisted command exits 2: unknown
# subcommands, unknown flags, extra arguments, and output paths.
#
# A Discord DM is several --line arguments, each one line of the message. This script joins
# them with newlines and passes one argument to `clippa alert send`. The shell command the
# model types stays one line, so a newline never has to appear in it.
set -eu
DRY=0
# CHECKUP_DRY is what checkup.sh sets for a dry run, so a model that forgets the flag
# still cannot send. The flag is the same switch for a direct call.
if [ "${CHECKUP_DRY:-}" = 1 ]; then
    DRY=1
fi
if [ "${1:-}" = "--dry-run" ]; then
    DRY=1
    shift
fi

die() { echo "checkup-clippa: $*" >&2; exit 2; }

[ $# -ge 1 ] || die "missing command"

for a in "$@"; do
    printf '%s' "$a" | awk 'NR>1 { exit 1 }' || die "metacharacters are not allowed"
    case "$a" in
        *'&&'*|*'||'*|*';'*|*'|'*|*'>'*|*'<'*|*'$('*|*'`'*) die "metacharacters are not allowed" ;;
    esac
done

case "$1" in
    audit)
        [ $# -eq 1 ] && exec clippa audit
        [ $# -eq 2 ] && [ "$2" = "--all" ] && exec clippa audit --all
        [ $# -eq 2 ] && [ "$2" = "--brief" ] && exec clippa audit --brief
        die "audit only as: audit | audit --all | audit --brief"
        ;;
    costs)
        [ $# -eq 1 ] || die "costs takes no arguments"
        exec clippa costs
        ;;
    checkup)
        [ $# -eq 1 ] && exec clippa checkup
        [ $# -eq 2 ] && [ "$2" = "--json" ] && exec clippa checkup --json
        die "checkup only as: checkup | checkup --json"
        ;;
    status)
        [ $# -eq 1 ] || die "status takes no arguments"
        exec clippa status
        ;;
    doctor)
        if [ $# -eq 2 ] && [ "$2" = "--offline" ]; then exec clippa doctor --offline; fi
        if [ $# -eq 4 ] && [ "$2" = "--role" ] && [ "$3" = "agent" ] && [ "$4" = "--offline" ]; then
            exec clippa doctor --role agent --offline
        fi
        die "doctor only as: doctor --offline | doctor --role agent --offline"
        ;;
    record)
        [ $# -eq 2 ] && [ "$2" = "status" ] || die "record only as: record status"
        exec clippa record status
        ;;
    worker)
        [ $# -eq 2 ] && [ "$2" = "--status" ] || die "worker only as: worker --status"
        exec clippa worker --status
        ;;
    alert)
        [ "$DRY" -eq 0 ] || die "alert send is blocked with --dry-run"
        [ "${2:-}" = "send" ] || die "alert only as: alert send --kind checkup --line TEXT"
        shift 2
        kind=""
        msg=""
        count=0
        nl=$(printf '\n.')
        nl=${nl%.}
        while [ $# -gt 0 ]; do
            case "$1" in
                --kind)
                    [ $# -ge 2 ] || die "--kind needs checkup"
                    case "$2" in
                        checkup) kind=checkup ;;
                        /*|*..*) die "output paths are not allowed" ;;
                        *) die "kind must be checkup" ;;
                    esac
                    shift 2
                    ;;
                --line)
                    [ $# -ge 2 ] || die "--line needs text"
                    case "$2" in
                        -*) die "a line must not be a flag" ;;
                        "") die "--line needs text" ;;
                    esac
                    if [ "$count" -eq 0 ]; then
                        msg=$2
                    else
                        msg="$msg$nl$2"
                    fi
                    count=$((count + 1))
                    shift 2
                    ;;
                /*|*..*)
                    die "output paths are not allowed"
                    ;;
                *)
                    die "unknown alert argument: $1"
                    ;;
            esac
        done
        [ "$count" -ge 1 ] || die "alert send needs at least one --line"
        if [ -n "$kind" ]; then
            exec clippa alert send --kind "$kind" "$msg"
        fi
        exec clippa alert send "$msg"
        ;;
    *)
        die "unknown command: $1"
        ;;
esac
