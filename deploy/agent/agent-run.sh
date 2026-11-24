#!/bin/sh
# Headless checkup or audit. The prompt is stdin, written to a 0600 file and removed on
# exit, then passed as one argument: the text is never expanded by a shell.
#
#   agent-run --role <audit|checkup> [--fix-dir <path>] [--dry-run] < prompt.md
set -eu
ROLE=""
FIX=""
DRY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --role)
            [ $# -ge 2 ] || { echo "agent-run: --role needs audit or checkup" >&2; exit 2; }
            ROLE=$2
            shift 2
            ;;
        --fix-dir)
            [ $# -ge 2 ] || { echo "agent-run: --fix-dir needs a path" >&2; exit 2; }
            FIX=$2
            shift 2
            ;;
        --dry-run)
            DRY=1
            shift
            ;;
        *)
            echo "agent-run: unknown argument: $1" >&2
            exit 2
            ;;
    esac
done
case "$ROLE" in
    audit|checkup) ;;
    *) echo "agent-run: --role audit|checkup" >&2; exit 2 ;;
esac

umask 077
prompt=$(mktemp)
trap 'rm -f "$prompt"' EXIT
cat > "$prompt"

json=$(clippa agent-config --role "$ROLE" --json) || exit $?
set -- python3 -c 'from clippa.agent import main; raise SystemExit(main())' run --json "$json" --prompt-file "$prompt"
if [ -n "$FIX" ]; then
    set -- "$@" --fix-dir "$FIX"
fi
if [ "$DRY" -eq 1 ]; then
    set -- "$@" --dry-run
fi
# The model process must not inherit a key. agent-config has already read the config.
unset OPENROUTER_API_KEY ANTHROPIC_API_KEY OPENCODE_PASSWORD CLAUDE_CODE_OAUTH_TOKEN
# No exec: the EXIT trap has to run and delete the prompt file. The child status is kept.
rc=0
"$@" || rc=$?
exit "$rc"
