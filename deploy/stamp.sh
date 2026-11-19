#!/usr/bin/env bash
# Sourced by the deploy scripts that build an image from a copy of the code (deploy/hub,
# deploy/record): `stamp_build <repo root> <staged src dir>` writes <src>/clipper/_build.txt,
# `git describe` of the checkout being deployed, since the image has no .git. clipper/buildinfo.py
# reads it, so `clipper --version`, doctor and the services' heartbeats say which commit a
# container runs ("0.2.0+3.g570bb87", "...dirty" when the checkout had uncommitted changes).
# No git here, or no commit: no file, and the image reports the plain release number.
stamp_build() {
    local root=$1 src=$2
    rm -f "$src/clipper/_build.txt"
    git -C "$root" describe --tags --always --dirty --match 'v[0-9]*' > "$src/clipper/_build.txt" 2>/dev/null \
        || rm -f "$src/clipper/_build.txt"
    if [ -s "$src/clipper/_build.txt" ]; then
        echo "build: $(cat "$src/clipper/_build.txt")"
    fi
}
