# Claude Code on the NAS (deploy/claude): the hub image's base (Debian 13, ffmpeg 7, Intel's
# media driver for QSV) plus git, gh and Claude Code. The code isn't baked in: it is the
# clone at /app, which the entrypoint makes on first start and syncs a venv for.
FROM python:3.12-slim-trixie

RUN sed -i 's/^Components: main$/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata fontconfig \
        fonts-dejavu-core intel-media-va-driver-non-free libvpl2 libmfx-gen1.2 vainfo \
        git gh openssh-client less procps ripgrep jq curl nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
COPY --from=docker.io/tailscale/tailscale:v1.104.4 /usr/local/bin/tailscale /usr/local/bin/tailscale

# Claude Code's native build, inside the image (a session reads its binary from disk, which
# mustn't be the NAS volume). deploy.sh passes the day as CLAUDE_CODE_BUILD, so a deploy picks
# up a new release at most once a day; the container never updates itself.
ARG CLAUDE_CODE_VERSION=latest
ARG CLAUDE_CODE_BUILD=0
WORKDIR /tmp
RUN echo "claude code build $CLAUDE_CODE_BUILD" \
    && curl -fsSL https://claude.ai/install.sh | HOME=/opt/claude bash -s "$CLAUDE_CODE_VERSION" \
    && chmod -R a+rX /opt/claude \
    && ln -sf "$(readlink -f /opt/claude/.local/bin/claude)" /usr/local/bin/claude \
    && claude --version

# The Hindsight coding-agents plugin (#435), the one the laptop's Claude uses for memory: the
# entrypoint installs it into the home volume from this copy when HINDSIGHT_API_URL is set.
# Pinned with npm's checksum (sha512, hex); bump the two together.
ARG HINDSIGHT_PLUGIN_VERSION=0.8.8
ARG HINDSIGHT_PLUGIN_SHA512=36a1caeb8cfd801adb94ea5a55268078768b41cc4f0a9ef08a563a5f833a93806f8113b957050d63f70c4c966dd02d1d264a84965358dcde80d9fc72ded95f36
RUN curl -fsSL -o /tmp/hindsight.tgz \
        "https://registry.npmjs.org/@vectorize-io/hindsight-coding-agents/-/hindsight-coding-agents-$HINDSIGHT_PLUGIN_VERSION.tgz" \
    && echo "$HINDSIGHT_PLUGIN_SHA512  /tmp/hindsight.tgz" | sha512sum -c - \
    && mkdir -p /opt/hindsight-coding-agents \
    && tar -xzf /tmp/hindsight.tgz -C /opt/hindsight-coding-agents --strip-components=1 \
    && rm /tmp/hindsight.tgz && chmod -R a+rX /opt/hindsight-coding-agents \
    && test -f /opt/hindsight-coding-agents/dist/installer.js && node --version

COPY entrypoint.sh /usr/local/bin/clippa-claude
COPY hindsight-check.sh /usr/local/bin/hindsight-check
# The nightly checkup (#185): the script and its prompt as deployed, not the clone's (a phone
# session may have /app on any branch).
COPY checkup.sh /usr/local/bin/clippa-checkup
COPY checkup-git.sh /usr/local/bin/checkup-git
COPY checkup-pr.sh /usr/local/bin/checkup-pr
COPY checkup.md /usr/local/share/clippa/checkup.md
COPY audit.sh /usr/local/bin/clippa-audit
COPY audit-deep.md /usr/local/share/clippa/audit-deep.md
# The old names (clipper-checkup, clipper-audit) answer too until #286's last pass.
RUN chmod 755 /usr/local/bin/clippa-claude /usr/local/bin/hindsight-check /usr/local/bin/clippa-checkup /usr/local/bin/checkup-git /usr/local/bin/checkup-pr \
        /usr/local/bin/clippa-audit \
    && chmod 644 /usr/local/share/clippa/checkup.md /usr/local/share/clippa/audit-deep.md \
    && ln -s clippa-checkup /usr/local/bin/clipper-checkup && ln -s clippa-audit /usr/local/bin/clipper-audit

# /app is the clone, /home/claude Claude Code's home (both volumes). The config and every
# relative [paths] entry live in /data, as in the hub; prompts come with the clone. Both spellings
# of the config variable: the clone may be on a branch from before #286 pass 1, which reads only CLIPPER_*.
ENV HOME=/home/claude \
    PATH=/app/.venv/bin:$PATH \
    CLIPPA_CONFIG=/data/clipper.toml \
    CLIPPER_CONFIG=/data/clipper.toml \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_UPDATES=1 \
    LIBVA_DRIVER_NAME=iHD \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8
WORKDIR /app

ENTRYPOINT ["clippa-claude"]
