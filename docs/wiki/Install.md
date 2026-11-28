Installing Clippa on a machine for one or more of its [roles](Roles-and-services). After any
setup, run `clippa doctor` (add `--role gpu`, `--role serve`, `--role bot`, `--role nas` or `--role control`): it
checks everything on these pages and prints a fix for each problem.

Per platform: [Windows](Install-Windows) (GPU worker, serve, bot), [macOS](Install-macOS)
(Apple Silicon), [Transcribe on Modal](Transcribe-on-Modal) (no local GPU), the
[NAS recorder](NAS-recorder). Then [add a show](Adding-a-show) and sign in to the
[YouTube channel](YouTube-channel). Optional pieces: [Model providers](Model-providers),
[Phone access](Phone-access), [Speakers](Speakers), [Faces](Faces), [Cast library](Cast-library),
[Character pieces](Character-pieces) (narration voice), [X posts](X-posts),
[News watcher](News-watcher).

## A release on a new machine

Check out a tagged release, not main's tip: each is on the
[Releases](https://github.com/byvfx/clippa/releases) page with what changed. You need git
(`winget install Git.Git` on a Windows machine without it), then:

```powershell
git clone https://github.com/byvfx/clippa.git C:\clipper
git -C C:\clipper checkout (git -C C:\clipper describe --tags --abbrev=0 --match 'v[0-9]*')
powershell -ExecutionPolicy Bypass -File C:\clipper\deploy\bootstrap.ps1
```

```bash
git clone https://github.com/byvfx/clippa.git ~/clipper && cd ~/clipper
git checkout "$(git describe --tags --abbrev=0 --match 'v[0-9]*')"
bash deploy/bootstrap.sh
```

`describe` names the newest release; to pin one instead, `git checkout v0.2.0`. If it prints
`No names found`, nothing has been tagged yet: stay on main. `clippa --version` says what the
machine has (`0.2.0` on a release, `0.2.0+3.g570bb87` past one). The bootstrap carries on from
[the walkthrough below](#the-walkthrough-clippa-setup-78).

To move a machine to a newer release: stop what runs from the folder (the bot, a worker task),
`git fetch --tags`, `git checkout <tag>`, then `uv sync` with the extras its roles need, the
list `clippa setup --sync-args` prints (the bootstrap leaves an existing `.venv` as it is).
The NAS containers aren't updated this way: they get the code with `bash deploy/hub/deploy.sh`,
which stamps the image with the commit, and `clippa services status` shows each one's version.

## The walkthrough: `clippa setup` (#78)

Get the code onto the machine into a short path ([a release](#a-release-on-a-new-machine), a
clone, or a copy of the folder without `streams/`, `.venv/`, `logs/`), then from that folder:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\bootstrap.ps1     # Windows
bash deploy/bootstrap.sh                                         # macOS / Linux
```

The bootstrap installs uv (which brings Python 3.12) and ffmpeg if they're missing (winget,
else uv's installer and BtbN's ffmpeg build; Homebrew or apt on macOS/Linux), syncs the
packages, adds `clippa` and `clipper` commands to `~/.local/bin` (on PATH; they run this folder's
`.venv`, so either works in any folder and new terminal; `clippa` is the old name, #286), then
runs `clippa setup`:

1. **Join or start fresh.** Join takes the main machine's `clipper.toml` from the share or a path
   (`~/Downloads/clipper.toml`, the folder that holds it, or the file dragged into the terminal).
   On the share (the [hub](NAS-hub) setup) it is used in place: setup saves `CLIPPER_CONFIG`
   pointing at it and `CLIPPER_LOCAL_CONFIG` pointing at this checkout's `clipper.local.toml`, so
   every machine reads one config and this machine's own values stay off the share. A copy (and,
   if you like, the `secrets/` beside it) is the other answer; new writes a short starter (only what differs from the defaults; clippa/defaults.toml lists every setting).
2. **Roles.** Worker, serve, bot, recorder (the recorder is the NAS's Docker container; setup only
   points at `deploy/record/deploy.sh`), control (a dev/control station that runs nothing
   unattended: no extra packages, no encoder, no Modal or Discord token; the streams folder, the
   Anthropic key and the Kitsu token are optional (Kitsu works in the browser without one); doctor checks the keys, the war room and ssh to the [hub](NAS-hub)). It looks at the GPU and offers WhisperX (NVIDIA), mlx
   (Apple Silicon) or Modal, keeping the shared choice when this machine can run it. If the
   roles need packages that aren't installed, it exits with code 3 and the bootstrap runs
   `uv sync --extra ...` and starts setup again.
3. **Folders and encoder.** The streams folder (checked writable), the time zone (new installs
   only), and the encoder: the shared `[cut] clip_codec` if it encodes here, else the first of
   NVENC, QSV, VideoToolbox, libx264 that does.
4. **Keys.** Anthropic, the Kitsu address and bot token, Modal (when it transcribes there), the
   Discord bot token (bot role, or `[discord] enabled`). Typed hidden, checked against the
   service, saved as Windows user env vars / in your shell profile / in `secrets/`. Enter skips
   one, except the Kitsu address and token on serve and the bot: the war room and the bot review
   in Kitsu and doctor fails without them, so setup asks again (Ctrl+C stops; it resumes there).
   Elsewhere Enter at the Kitsu address means no Kitsu: runs leave the videos in the stream
   folder ([Fetching VODs](Fetching-VODs#without-kitsu)).
5. **Check.** `clippa doctor` for the chosen roles, then what to run next.

On Linux the bootstrap also installs the fonts covers and captions use, which Windows and macOS
come with: Impact, Georgia and Arial (`ttf-mscorefonts-installer`, which asks you to accept
Microsoft's license; if you decline, `sudo dpkg-reconfigure ttf-mscorefonts-installer` asks
again) and Roboto Condensed.

Values that only this machine needs go in **`clipper.local.toml`** beside clipper.toml
(gitignored), written only where they differ from the shared file, so clipper.toml can stay
identical everywhere. It wins over clipper.toml and its shows; `CLIPPER_*` env vars still win
over it. Progress is in `setup_state.json`; `clippa setup --step <name>` redoes a step,
`clippa setup --check` shows each step's checks without asking anything, and
`clippa setup --answers answers.toml` runs it without questions (keys never go in that file:
they come from the environment):

```toml
mode = "new"                 # or "join", with from = "path/to/clipper.toml", copy_secrets = true
roles = ["worker", "bot"]
transcribe = "modal"         # whisperx | mlx | modal
streams = "streams"
timezone = "America/Los_Angeles"
kitsu = "http://<nas>:8090"
```

> [!IMPORTANT]
> **Windows path length.** With Windows long paths off (the default), keep the checkout folder
> under ~80 characters (`C:\clipper` is fine): the anthropic package has files ~160 characters
> deep inside `.venv`, and past 260 in total Python can't import them. The bootstrap refuses a
> longer folder and doctor warns.

After a new install, the [Install checklist](Install-checklist) is a by-hand pass over what the
sandbox runs can't reach (the prompts, real keys, online checks, first real work).

Not in the walkthrough (each has its page): [Kitsu](Kitsu) and its accounts, the
[Discord app](Discord-app) and its ids, [YouTube sign-in](YouTube-sign-in), the first show
([Adding a show](Adding-a-show)), the Modal deploy ([Transcribe on Modal](Transcribe-on-Modal)),
the unattended services ([Roles and services](Roles-and-services#services)), and the second
person's view ([Reviewing](Reviewing)).

## Requirements

Python 3.12, [uv](https://docs.astral.sh/uv/), ffmpeg with libass (the "full" builds), an
Anthropic API key (`ANTHROPIC_API_KEY`; the defaults are Claude), a Kitsu instance, and a
Discord bot if you want cards in Discord. Any stage can run on another model provider
(OpenAI, Gemini, OpenRouter, or a local OpenAI-compatible server such as Ollama) by
prefixing its model key: [Model providers](Model-providers). OpenRouter for every Claude
stage is the opt-in overlay
[`docs/openrouter-claude.toml`](https://github.com/byvfx/clippa/blob/main/docs/openrouter-claude.toml),
which needs `OPENROUTER_API_KEY` or `[paths] openrouter_key`.
YouTube downloads (pre-shows) need a JavaScript runtime; `uv sync` installs deno for yt-dlp.
