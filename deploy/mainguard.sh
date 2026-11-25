# Sourced by deploy/hub/deploy.sh and deploy/claude/deploy.sh: require_main <repo root> <script> [force].
#
# A deploy ships what this checkout has (the hub's code, the claude container's Dockerfile and
# scripts), so one from a branch behind main puts old files back on the NAS. 2026-11-25: a
# claude deploy from a pre-#437 branch rebuilt the image without nodejs while the home volume
# kept the Hindsight plugin's hooks, and every hook failed with `node: not found`.
#
# require_main fetches origin/main, then refuses (exit 1) when HEAD lacks commits of it, naming
# the branch, the commit and how far behind. Offline (the fetch fails) it checks against the last
# fetched origin/main and says so; with no origin/main at all, or no git, it warns and goes on.
# force (the scripts' --force) turns the refusal into a warning. Commits HEAD has that main
# hasn't, and uncommitted changes, are only noted: deploying a PR's branch on purpose is fine.

require_main() {
    local root=$1 script=$2 force=${3:-} branch head main behind ahead
    if ! head=$(git -C "$root" rev-parse --short HEAD 2>/dev/null); then
        echo "warning: $root isn't a git checkout: can't tell whether it is up to date with origin/main" >&2
        return 0
    fi
    branch=$(git -C "$root" symbolic-ref --short -q HEAD || echo "a detached HEAD")
    local t=()
    command -v timeout >/dev/null 2>&1 && t=(timeout 60)
    if ! GIT_TERMINAL_PROMPT=0 "${t[@]}" git -C "$root" fetch --quiet origin main 2>/dev/null; then
        echo "warning: couldn't fetch origin/main (offline?): checking against the last one fetched" >&2
    fi
    if ! main=$(git -C "$root" rev-parse --short --verify -q refs/remotes/origin/main); then
        echo "warning: no origin/main in $root: can't tell whether $branch is up to date with it" >&2
        return 0
    fi
    behind=$(git -C "$root" rev-list --count HEAD..refs/remotes/origin/main)
    ahead=$(git -C "$root" rev-list --count refs/remotes/origin/main..HEAD)
    if [ "$behind" -gt 0 ]; then
        echo "this checkout is $branch at $head, $behind commit(s) behind origin/main ($main):" \
             "a deploy from it ships older files than main's" >&2
        if [ -z "$force" ]; then
            echo "deploy from main (git switch main && git pull), or from a worktree of origin/main" \
                 "with --env pointing at this checkout's env.local; to deploy this anyway: bash $script --force" >&2
            exit 1
        fi
        echo "--force: deploying it anyway" >&2
    fi
    if [ "$ahead" -gt 0 ]; then
        echo "note: $branch at $head has $ahead commit(s) that aren't on origin/main: they ship too" >&2
    fi
    if [ -n "$(git -C "$root" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
        echo "note: $root has uncommitted changes: they ship too" >&2
    fi
    return 0
}
