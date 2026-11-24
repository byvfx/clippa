# The NAS agent container (deploy/agent): the hub image's base (Debian 13, ffmpeg 7, Intel's
# media driver for QSV) plus git, gh and a pinned OpenCode V2 CLI. The code isn't baked in:
# it is the clone at /app, which the entrypoint makes on first start and syncs a venv for.
# deploy/claude stays as it is so the two containers can run side by side during burn-in.
FROM python:3.12-slim-trixie

RUN sed -i 's/^Components: main$/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata fontconfig \
        fonts-dejavu-core intel-media-va-driver-non-free libvpl2 libmfx-gen1.2 vainfo \
        git gh openssh-client less procps ripgrep jq curl bash \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
COPY --from=docker.io/tailscale/tailscale:v1.104.4 /usr/local/bin/tailscale /usr/local/bin/tailscale

# OpenCode V2, pinned. The install script's --version is the npm package version. The image
# never updates itself (OPENCODE_DISABLE_AUTOUPDATE, and "update": "disable" in the headless
# config). deploy.sh passes OPENCODE_BUILD so a rebuild picks up a changed pin.
ARG OPENCODE_VERSION=2.4.1
ARG OPENCODE_BUILD=0
WORKDIR /tmp
RUN echo "opencode build $OPENCODE_BUILD version $OPENCODE_VERSION" \
    && curl -fsSL https://opencode.ai/v2/install | HOME=/opt/opencode bash -s -- \
        --version "$OPENCODE_VERSION" --no-modify-path \
    && chmod -R a+rX /opt/opencode \
    && ln -sf "$(readlink -f /opt/opencode/.opencode/bin/opencode)" /usr/local/bin/opencode \
    && opencode --version

COPY entrypoint.sh /usr/local/bin/clippa-agent
COPY checkup.sh /usr/local/bin/clippa-checkup
COPY checkup-git.sh /usr/local/bin/checkup-git
COPY checkup-pr.sh /usr/local/bin/checkup-pr
COPY checkup-clippa.sh /usr/local/bin/checkup-clippa
COPY agent-run.sh /usr/local/bin/agent-run
COPY checkup.md /usr/local/share/clippa/checkup.md
COPY audit.sh /usr/local/bin/clippa-audit
COPY audit-deep.md /usr/local/share/clippa/audit-deep.md
RUN chmod 755 /usr/local/bin/clippa-agent /usr/local/bin/clippa-checkup /usr/local/bin/checkup-git \
        /usr/local/bin/checkup-pr /usr/local/bin/checkup-clippa /usr/local/bin/agent-run \
        /usr/local/bin/clippa-audit \
    && chmod 644 /usr/local/share/clippa/checkup.md /usr/local/share/clippa/audit-deep.md \
    && mkdir -p /etc/opencode \
    && printf '%s\n' '{"update":"disable","mcp":{"servers":{}}}' > /etc/opencode/opencode.json

# /app is the clone, /home/agent OpenCode's home (both volumes). The config and every relative
# [paths] entry live in /data, as in the hub. The OpenRouter key is a compose secret, not an
# environment variable: CLIPPA_PATHS_OPENROUTER_KEY points at the file.
ENV HOME=/home/agent \
    PATH=/app/.venv/bin:$PATH \
    CLIPPA_CONFIG=/data/clipper.toml \
    CLIPPER_CONFIG=/data/clipper.toml \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    OPENCODE_DISABLE_AUTOUPDATE=1 \
    LIBVA_DRIVER_NAME=iHD \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8
WORKDIR /app

ENTRYPOINT ["clippa-agent"]
