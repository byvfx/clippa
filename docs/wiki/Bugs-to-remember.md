Bugs whose symptom points away from the cause, or that will come back with new hardware, a recorder
or an ffmpeg, written down so the next person or agent recognises them in minutes instead of
starting from the log. One entry per bug: the **symptom** (what you see), the **cause**, **how to
tell** (the check), the **band-aid** that keeps the pipeline moving, and the **fix**. The dated
entries at the top are newest first; below them the older ones are grouped by what they have in
common. The `bug-to-remember` label marks an issue as a candidate; the fix's PR adds its entry here.

Every band-aid on this page is for a person to run, and these are this page's own rules (they win
over any how-to they link): no deploy or service restart without the user's word; never while a
show is live or a job or upload is running; never `clippa upload` or `clippa review publish` by
hand ([Scheduling and uploads](Scheduling-and-uploads#automatic-uploads)); never a second
`clippa serve`, `clippa bot` or `clippa worker` on the laptop beside the hub containers
([NAS hub](NAS-hub#day-to-day)); and a deploy is from a checkout of main, never a feature branch.
A band-aid's "restart" means the hub container, `clippa services restart <name>`; the mechanics
are in [Roles and services](Roles-and-services#services), and [NAS hub](NAS-hub#deploy) says what
a deploy stops (a running stage or upload). A recorder's deploy must never run while a show is
live ([NAS recorder](NAS-recorder)); never `--force` it — the page documents `--force` as the
override this rule forbids.

### Every Hindsight hook says `node: not found` after a deploy (2026-11-25)

**Symptom.** Right after a `deploy/claude/deploy.sh`, every Hindsight hook in the NAS `claude`
container fails with `node: not found` (session start, each prompt, session end), and the
Hindsight MCP server doesn't start; the container itself comes up and Remote Control works. A
deploy minutes earlier had worked.

**Cause.** The deploy ran from a checkout of an old branch (one without
[#437](https://github.com/byvfx/clippa/pull/437)). `deploy.sh` copies `deploy/claude/*` from
whatever checkout runs it, so Docker rebuilt the image from the old Dockerfile, which has no
nodejs (its cached layers made it quick). The home volume kept what the newer image's entrypoint
had installed: the plugin, its hooks in `settings.json` and its MCP entry, all of which call
`node`. The hub's deploy has the same trap with the code: old code goes back on the NAS.

**How to tell.** `git -C <the checkout you deployed from> log -1 --oneline` and
`git rev-list --count HEAD..origin/main`: behind main. In the container,
`command -v node` prints nothing.

**Band-aid.** Deploy again from main (`git switch main && git pull`, or a worktree of
`origin/main` with `--env` at the main checkout's `env.local`): the image gets nodejs back and the
hooks work at once, nothing in `home/` to clean up. Not while a nightly checkup runs (it ends the
open session).

**Fix.** `deploy/hub/deploy.sh` and `deploy/claude/deploy.sh` fetch `origin/main` and refuse
(exit 1) from a checkout behind it, naming the branch, the commit and how many commits behind
(`deploy/mainguard.sh`); `--force` deploys it anyway. Offline they check the last fetched main
and say so. ([Claude on the NAS](Claude-on-the-NAS#deploy).)

### A long stream's transcribe dies with SIGTERM (2026-11-24)

**Symptom.** `run.json` says `transcribe killed by SIGTERM (-15)` (`exited -15` on a worker from
before the fix), `run_transcribe.log` stops after `encoded N MB flac`, `modal_runs.jsonl` has a
`start` and no `end`; nothing runs on Modal, and no other error appears.

**Cause.** Memory on the worker's machine. The audio goes to Modal as one `bytes` argument, so the
file sits in memory and the client copies it: `.remote(bytes)` peaks at 12–17× the payload (600 MB
used 7.5 GB, 900 MB used 15.5 GB; that is the size test,
[#425](https://github.com/byvfx/clippa/issues/425)), and from 1 GiB it uploads in parts all at once.
An 18.3 h stream's 1.3 GB FLAC needs 20+ GB on a NAS with about 20 GB free, so an out-of-memory
daemon (earlyoom, the likely sender per [PR #431](https://github.com/byvfx/clippa/pull/431); the
journal line needs sudo and isn't confirmed) sent SIGTERM. The biggest FLAC known to go up fine is
884 MB (12.2 h).

**How to tell.** The stream is long (over ~12 h; `meta.json` `duration_s`) and the exit is -15 (-9
is the kernel's own OOM killer), right after the encode line; `journalctl -u earlyoom` names the
kill (needs sudo).

**Band-aid.** On a worker from before PR #431, or for a stream already set to it:
`[transcribe] modal_audio = "opus"` in the stream's own `config.toml` (~400 MB for 18 h), and the
worker's next attempt works. Opus encoding is slow (8 min for 18 h on the NAS, against 30 s for
FLAC) and the call cost about $1.70. On a worker with #431 no per-stream setting is needed for this
failure: a FLAC over 700 MB switches to opus automatically.

**Fix.** PR #431 does two things: a FLAC bigger than `[transcribe] modal_opus_over_mb` (700 MB) is
encoded again as opus and sent instead (0 = never), and the worker names the signal in `run.json`
and its log instead of `exited -15` ([#422](https://github.com/byvfx/clippa/issues/422)). Still
open: stream the file to a Modal Volume so memory stays flat at any length, then re-run the size
test (#425). Taking the per-stream override out of a stream's `config.toml` is housekeeping once a
worker with #431 is deployed, not part of the Volume work; the settings are in
[Transcribe on Modal](Transcribe-on-Modal#troubleshooting).

## Timing and sync

They look like an editing mistake; they are the pipeline's clock.

### Transcript runs early against the video after a gap (2026-10-21)

**Symptom.** Captions drift ahead of the mouths, and every cut from a point on starts ~47 s early
against the picture; `kick-132733064` was 46.91 s out from about 0.7 h in, found when a reviewer
rejected two Shorts with "captions dont match".

**Cause.** The old recorder dropped 31 HLS segments (63.8 s) in the first 43 min. `source.mkv`
keeps the timestamp jumps, but a WAV has no timestamps, so `extract_audio` wrote the samples closed
up over each gap: `audio.wav` 28394.4 s against a 28441.3 s source.

**How to tell.** `ffprobe` the duration of `audio.wav` and of the source: a shorter WAV means every
transcript time after the gap is early by the difference.

**Band-aid.** Cross-correlate the two, add the shift to the stream's times and re-cut (the 10-21
clock fix; backups in the stream's `clockfix_backup/`).

**Fix.** `extract_audio` fills each gap with silence (`aresample=async=1:first_pts=0`,
`clippa/transcribe.py`): 28441.31 s, aligned at every probe. **Can come back** with a new recorder:
check its gaps against the source length. (HANDOFF; `docs/log/2026-10.md`.)

### A VOD whose timestamps jump backwards (kick-132975332, 2026-10-18)

**Symptom.** A transcript a quarter of an hour late, captions to match, and a seek near the end of
the source fails or lands elsewhere.

**Cause.** The VOD's timestamps jump back 821.7 s just before 7:28, so after that point transcript
time = source time + 821.7 s (`ffprobe` 33011 s of container against 33833 s of audio). Kick's open
GOPs also drop frames after an input seek.

**How to tell.** `ffprobe` the source's format duration against its audio stream's: 33011 s against
33833 s is this stream's signature, and a backwards `pts` jump sits just before a round time.

**Band-aid.** `clippa transcribe` refuses such a source instead of writing a transcript that
would be wrong after the jump: when `audio.wav` is more than 1 s longer than the source's
container it says so, naming the stream and both lengths, and prints `clippa fetch <url> --fixup`
when meta.json holds a Kick VOD's url (a recording's channel link has nothing to fetch).
`--fixup` remuxes the source with its timestamps regenerated — or the video it has just
downloaded, so a fresh download can be repaired in one go — and keeps the jump it found as
`timeline_overlap_s` in meta.json. It rewrites the whole file, so it never runs by itself:
without it a download is taken as it comes, which is right for every file but this one. A source
that is still downloading (yt-dlp's `source.ts.ytdl` resume note beside it) is never remuxed as a
partial: `--fixup --no-video`, and a repair with no metadata, say so and stop since they can't
download (rewriting the partial in place would leave the note for the next fetch to resume into),
while a plain `clippa fetch` resumes from the note, and a `--fixup` that runs the video resumes
first and then repairs the whole file. An
audio *shorter* than its container only warns.

**Fix.** `clippa transcribe` checks the timeline after `extract_audio` and before it uses any
times (`check_audio_timeline`, `clippa/transcribe.py`), `clippa speakers <stream> --diarize` runs
the same check after its audio extraction, and `clippa archive transcribe` makes the check while
the item's source is still on disk: a refused download is renamed `source.refused.ts` (or, where the
share refuses that rename, remembered by a `.refused` marker beside `source.ts`, written only then
and counted only while `source.ts` is still there — one left with no source is dropped and the item
downloads again, since a marker trusted alone would serve a stale `audio.wav`), reused by
the next run instead of downloaded again (`_drop_partial` only ever unlinks `source.ts`, so it
can't take the kept file), and the item waits for its jump to be fixed. Where neither the rename
nor the marker can be written the item says the download is still `source.ts` and a later run may
replace it; a re-refused item's line names the copy it is kept under. A kept `source.mp4` gets the
same treatment as the other two names: a link or an empty one is not a kept video and is dropped
before the audio is read, so a bogus name cannot stall the item. A link or an empty file at
`source.mp4` or `source.refused.ts` is dropped after the download, before the audio is read
(`source.refused.ts` also before it, since a download would otherwise be shadowed): one
planted while yt-dlp runs would otherwise be the name `extract_audio` reads (and the refusal's
rename cannot replace a link), so the item would stick with both it and its finished `source.ts`.
A `--fixup` that rewrites the source marks `audio.wav.stale` before the replace and drops the
`audio.wav` decoded from the old one, so the transcribe after it reads the repaired file (its
samples differ: `aresample` stretched across the jump); the marker is the durable half, written
before anything is replaced, because a fast remux can leave the wav looking current and after a
repair the two lengths agree, so the timeline check can't tell them apart. A plain `clippa fetch`, and `clippa archive transcribe`'s own download, mark a wav beside a source
they downloaded stale too, since yt-dlp stamps the VOD's `Last-Modified`, older than the wav. `extract_audio` decodes again a marked wav whatever else says, then goes by
`audio.wav.src`, the stamp each decode writes beside the wav: the source's size and mtime when the decode
began ([#448](https://github.com/byvfx/clippa/issues/448)). A wav is reused only when the stamp still
names the source (the size exactly, the mtime within the 2 s a FAT/SMB share rounds timestamps by, since
the laptop over SMB and a container on the NAS read one file's time at different precisions), so a
source moved in by hand or put back from a backup never reads a wav made from another file, whatever
its date; a stamp that is a link or isn't one is a no. A stream folder copied without its times kept (a plain copy, `rsync` without
`-t`) no longer matches its stamps, so each wav is decoded once again: copy with the times kept. A wav from before the stamps falls back to the
mtime rule: any `audio.wav` older than its source by more than those 2 s is decoded again (a source
stamped in the future is no exemption, since only the two files' own times are compared), except in an
archive item, where a wav is only ever a failed run's leftover and one with no stamp is always decoded
again. A `--fixup` on a source already in order rewrites nothing, but drops an `audio.wav` that runs
past the container by more than the guard's 1 s (it was decoded from an earlier file), so the stream
is never left refusing until the wav is deleted by hand. A decode that outlives a
replacement of its source is not installed at all: the source's own size and time are read before
and after it, and the run asks to be repeated, since the check above can't see the difference once
the lengths agree. A link or junction sitting at `audio.wav`, `audio.tmp.wav` or
`audio.modal*` is removed (the link alone, never what it names) before ffmpeg is given that name to
write, so ffmpeg can't truncate what the link names; one at `source.refused.ts` or `.refused` is
removed for the same reason (the marker's is never written through), so it can't stop every run. A
link at a stream folder, or at its `source.ts`, is refused instead, naming the stream, before
anything reads or writes through it. The remux is
`clippa/timeline.py`: a `-fflags +genpts` copy into a fresh temp file beside the source, its
length probed and its own packet timestamps checked for a backwards step — the length alone can't
tell a repaired file from a remux that changed nothing — and only a result that lost nothing and
no longer jumps goes in place; the temp goes on every exit. The stream's run lock is taken for a
moment before the remux and again around the replace (a 33 GB remux outlasts a lock), so a worker
that starts meanwhile still sees the old source and is stopped by the transcribe check, not
raced. A source it can't compare (a `.wav` source, no source at all, or an `audio.wav` whose
length ffprobe can't give) is passed over, not refused; a source container with no duration at
all is an error, since then there is nothing to compare it with. ([#440](https://github.com/byvfx/clippa/issues/440),
filed 2026-11-25, labelled `bug-to-remember`; `docs/log/2026-11.md`, 2026-11-04.)

### Audio drifts on a stream-copy cut (2026-10-28)

**Symptom.** At a compilation's join a speaker repeats himself — the H3 video at 2:18:42
(`kick-133940023`, the s14 → s15 join) — and a stream-copy cut can sit a frame or more off.

**Cause.** A stream copy must start on a keyframe (every ~4 s), so each segment file carries up to
4 s of lead-in; concatenating whole files replayed that lead-in at every back-to-back join (13 of
15 in the H3 video, 0.9–4.7 s; also Quartering, DSP, Angry Joe).

**How to tell.** Match the compilation's audio against the source's second by second: the same
words twice at a join is this, not a duplicate segment.

**Band-aid.** None for a video already out (the user's call was not to re-cut what is published).

**Fix.** `copy_cut` starts on the keyframe at or before the start and seeks `k+0.005` for a `.ts`
and `k+0.15` otherwise, because ffmpeg backs an input seek off by 3/23 s with B-frames; it fails
the cut on a sync gap over 0.25 s or a video more than 0.5 s off its length. Each compilation part
ends with a concat `outpoint` where the next file begins (`compilation_parts`) — both in
`clippa/cut.py`. (HANDOFF; `docs/log/2026-10.md`.)

### Tightened Shorts drift 35–155 ms (2026-11-09)

**Symptom.** A Short with pauses cut out has its sound behind the picture by a few dozen
milliseconds, worse the more pauses were cut.

**Cause.** `select` and `aselect` rounded to different frames, so the two clocks parted a little at
each pause over many cuts.

**How to tell.** Line the rendered Short's audio up against its picture at its last pause cut: the
offset grows with the number of cuts.

**Band-aid.** Re-render it untightened — `clippa shorts <stream> --items v03 --no-tighten` keeps
its pauses.

**Fix.** [PR #191](https://github.com/byvfx/clippa/pull/191) cuts on whole frames and the sound in
`1/fps` frames (`asetnsamples=n={per_frame}`), with `STARTPTS+N` and `asetpts=STARTPTS+N/SR/TB` in
`clippa/shorts.py` keeping the first-frame offset: measured 0 ms after. The same PR stopped a plain
run marking every Short after the first "keep pauses": the override is read per Short now
(`cut_tight = cfg["edit"]["tighten"] and prev.get("tighten") is not False`).

## Shared state over the NAS

Another process holds the state — a file on the share, an OS lock, a container — so the failure
depends on who else is there.

### A JSON state file read mid-replace (2026-10-28)

**Symptom.** `PermissionError` on `review.json`, or a JSON parse error, while nothing else is
wrong; one hit the analytics DM at 14:57 (`kick-131151926`).

**Cause.** Writers replace state files through a temp file, so a reader that opens one at the wrong
moment finds it mid-replace (over SMB) or locked for replacement (Windows).

**How to tell.** The traceback names `json.loads(path.read_text())` or a bare `read_text`, and the
file is fine a second later.

**Band-aid.** Re-run the command.

**Fix.** `jsonstate.load` and `read` make ten attempts with a growing pause between them (0.2 s,
0.4 s … 1.8 s, about 9 s in all), and a test fails on a new raw `json.loads(path.read_text())`
([PR #79](https://github.com/byvfx/clippa/pull/79)). Heartbeats also go through
`daemon.read_heartbeat`, which reads a missing or unreadable file again (three reads, 0.3 s apart)
and treats one still unreadable as unknown, not absent: `clippa record status` exits 1 instead of
saying "nothing live" ([PR #183](https://github.com/byvfx/clippa/pull/183); see
[How the processes talk](How-the-processes-talk)).

### Two of the same service, and neither knows

**Symptom.** Everything runs twice: two uploads, two answers to one Discord button, two replans;
both machines' logs show the same jobs.

**Cause.** `daemon.single_instance` holds an OS byte lock on `<streams>/.<name>.lock` (`msvcrt` on
Windows, `fcntl` elsewhere) for the process's life. Two machines running the same service against
the share do not see each other's lock, so each thinks it is alone.

**How to tell.** `clippa services status`, or the war room's Health panel, names the `host` of each
heartbeat: two hosts with a fresh time for one service, or a service on a host you didn't mean.

**Band-aid.** Stop the stray copy where you can reach it; the heartbeat says which host it is on.

**Fix.** A rule, not code: serve, the bot and the worker run in one place only, the hub containers
since 2026-11-05 (AGENTS.md, "Running things here"; [NAS hub](NAS-hub)).

### A killed cut holds the lock

**Symptom.** The next cut of a stream stops at once, or waits, though nothing seems to be running;
`clippa cut` says "a cut of `<id>` is still going".

**Cause.** With `[cut] backend = "nas"` the cut runs in a `clipper-cut-<id>` container and that
name is the lock, so a cut killed half way (a lost ssh, a `Ctrl-C`) leaves the container behind and
the next run refuses.

**How to tell.** `clippa services status` lists NAS cut containers, and `ssh <nas> docker ps -a`
with `--filter name=clipper-cut-` shows the one that is left.

**Band-aid.** On the NAS, `docker rm -f clipper-cut-<id>`: the error prints the whole ssh command
for it, and `clippa services status` prints the remove it found. Only for a cut that has ended —
`rm -f` stops a live encode too — and keep the exact name, which is the lock.

**Fix.** The name is deliberately the lock old and new code share (it keeps the old spelling through
the #286 rename), so two cuts can't work on one stream: `clippa/cut.py` checks `docker ps` for it
before starting and names the way out. (HANDOFF.)

## Services that look fine but aren't

From the 2026-11-03 code audit: what you can see says the service is up, while the process has
died, or one of its jobs has died or gone into a loop; and the one you could not miss, the
recorder, does the opposite — a crashed job restarted every poll until the channel floods.

### The bot died and Health said it was running (2026-11-03)

**Symptom.** The war room's Health panel said "Discord bot: running / quiet N min" for days, with no
DMs and no review cards; `clippa services status` agreed.

**Cause.** The bot opened `streams/.bot.lock` with `a+` for its life and never unlinked it, and
Health read `exists()`: the file outlived the process, so a dead bot looked alive.

**How to tell.** The heartbeat `streams/.bot/status.json` stops while `.bot.lock` is still there.

**Band-aid.** Read the heartbeat's `at` (or `clippa services status`'s "N min ago"), not the file.

**Fix.** Health, `clippa services status` and `clippa doctor` read the heartbeat only
(`daemon.read_heartbeat`, 180 s without one = down); the lock is an OS lock, and a file left behind
means nothing ([PR #128](https://github.com/byvfx/clippa/pull/128); audit #3,
`docs/archive/code-audit-2026-11-03.md`).

### The bot stops DMing, and nothing says so (2026-11-03)

**Symptom.** No DMs for days: no failure, digest or review alerts, while Discord still shows the bot
online and Health still says running.

**Cause.** Two ways. A `[notify]` typo (`quiet_hours` with an en dash, `digest_time = "9:00pm"`) was
parsed every minute and never validated, so `alerts.pending` raised, "notify pass failed" went to
the log every minute and no DM ever left. And the notify loop called the tailnet lookup before its
own `while/try`: a wedged `tailscale` timed out and killed the task, leaving only an asyncio "never
retrieved" line at garbage collection.

**How to tell.** The bot's log has "notify pass failed" once a minute, or a task exception at
start-up.

**Band-aid.** Two causes, two first steps: for a bad `[notify]` value, correct the string and
restart the bot, and nothing else is needed; for the wedged lookup, get `tailscale status`
answering again, then restart the bot. Either way that is the hub bot only (`clippa services
restart bot`): not Tailscale, serve or the recorder, no `docker rm -f` of the Tailscale container
and no `tailscale up` (that is [Phone access](Phone-access#on-the-nas)'s recovery, a different
job); if `tailscale status` stays wedged, stop and ask.

**Fix.** `config.problems` pattern-checks both times at load (a warning at load, doctor fails) and
`alerts._hm` raises a `ClipperError` naming `[notify]` (audit #4,
[PR #129](https://github.com/byvfx/clippa/pull/129)); the lookup moved inside the loop's `try`,
which catches `Exception` and `SystemExit` (audit #2,
[PR #128](https://github.com/byvfx/clippa/pull/128)).

### The recorder restarts its broken video thread every poll (2026-11-03)

**Symptom.** A new recording part appears every poll (30 s) and each one posts to Discord, for the
rest of the show; the disk fills and the channel floods.

**Cause.** A crashed video thread (a full disk, a permission change on the share) marked the
recording ended; the watcher saw the stream still live and restarted it at the next poll, straight
away, every time.

**How to tell.** The recorder log has "live again, resuming" over and over with a part counter that
keeps climbing.

**Band-aid.** Keep that channel's recorder from starting again until the disk or the permission is
fixed.

**Fix.** A crash now backs off before each restart, and the wait grows: `self.crashes` counts them
and `retry_at = now + min(30 * 2 ** n, 600)` gives 60 s, 120 s, 240 s, 480 s, then 10 min, with no
restart before `retry_at` (`clippa/record/watch.py:166-176`); the first restart after a crash still
comes at the next 30 s poll (audit #5; [PR #129](https://github.com/byvfx/clippa/pull/129)).

## Tools that surprise

They do what their rules say; this repo's rules are not what you would assume.

### `gh pr merge --auto` merges at once (2026-11-20)

**Symptom.** `gh pr merge --auto` returns and the PR is merged there and then, before CI
finishes and before the review the process waits for: asking for "merge when green" merged a PR
whose runs were still red. Seen on [PR #353](https://github.com/byvfx/clippa/pull/353), a
notes-only wiki PR that merged the moment it opened.

**Cause.** `--auto` waits for the branch's *required* checks, and main has none. `gh api
repos/byvfx/clippa/rulesets` lists one ruleset, "Copilot reviews new PRs", whose only rule is
`copilot_code_review`; there is no `required_status_checks` rule, and
`repos/byvfx/clippa/branches/main/protection` answers 404, "Branch not protected" (checked
2026-11-25). Nothing to wait for, so GitHub merges at once.

**How to tell.** `gh pr view --json state` says MERGED a second after the command while the
runs for that commit are still queued or going.

**Band-aid.** None once it merged: the PR is in main, so a red run is fixed forward.

**Fix.** A rule: wait for the runs (`gh run watch <run-id>`) and the review, then
`gh pr merge --merge` (HANDOFF gotchas).

### `bash deploy/...` from PowerShell is WSL's bash

**Symptom.** `bash deploy/hub/deploy.sh` from PowerShell fails at once with
`getpwuid(0) failed` or `Failed to translate`, and nothing is deployed; the same command works
from Git Bash's own window.

**Cause.** The `bash` on Windows' PATH is WSL's, not Git's: it has no such user and no such
path into the checkout, so it cannot even start the script.

**How to tell.** `Get-Command bash` names `C:\Windows\System32\bash.exe` (WSL) rather than
Git's `bash.exe` under `C:\Program Files\Git`.

**Band-aid.** Nothing is half-deployed when it fails: the script never started, so re-run it with
the full path below, or from Git Bash's own window. That deploy rebuilds from the checkout and
`up -d`s every hub service, so not while `clippa services status` shows a job, the worker is busy
or a show is live, and not from a feature branch.

**Fix.** A rule: always call Git Bash by its full path in PowerShell, and give it that form
every time:

```powershell
& "C:\Program Files\Git\bin\bash.exe" deploy/hub/deploy.sh
```

The recorder's deploy (`deploy/record/deploy.sh`) has the same Git-Bash path problem; it must
never run while a show is live, and never with `--force`. (HANDOFF gotchas.)

### A restart that killed its own chain (2026-10-30)

**Symptom.** `clippa services restart bot` (or a worker task script) stopped the service and nothing
came back; its log ends mid-restart.

**Cause.** `deploy/common.ps1` found a service by matching its command line, and `clippa services
restart bot`'s own command line also ends in "bot": the stop took the restart's own process chain
with it, before the task could start again.

**How to tell.** The restart's log stops right after stopping, and the service's heartbeat never
comes back.

**Band-aid.** Start the hub service again from a fresh shell (`clippa services restart bot`); never
`clippa bot` or `clippa serve` on the laptop beside the hub, and not `clippa services restart all`
or the recorder.

**Fix.** `Get-ClipperProcesses` walks its own parent PIDs from `$PID` into `$mine` and leaves those
out when it picks the task's `clipper.exe` process. Keep that exclusion: without it the bug is
back. (HANDOFF "keep that"; 2026-10-30.)

### A pull leaves a bare war room (2026-10-30)

**Symptom.** The war room opens as plain unstyled text — no colours, no panels, no live numbers —
while `/` itself answers 200; the page's stylesheet and scripts 404.

**Cause.** The markup, stylesheet and scripts are files: `clippa/web/warroom.html` asks for
`warroom.css`, `theme.js`, `themes/*.css` and `js/*.js`, and `warroom/httpd.static_file` serves
whatever `.css`/`.js` sits under `clippa/web/`, from disk and `no-cache` (`httpd.py:58-89`). What a
running serve does not pick up is its own code and its route table: `GET_API` and `POST_API` are
dicts the started process holds (`httpd.py:108-169`). A pull under a serve from before the change
can hand out markup with nothing behind it — on 10-30 the split into `warroom.css` and `js/*.js`
(#91) left the old bot serving the new page, and that process had no route for the assets, so the
page was bare until the restart.

**How to tell.** The assets 404 in the browser's network panel, or a panel's fetch 404s on a route
the running serve doesn't know, while the page itself loads.

**Band-aid.** Restart the hub's serve, and only once `clippa services status serve` shows no job:
`clippa services restart serve`; don't start `clippa serve` on the laptop beside it.

**Fix.** A rule: after a pull that changes the page or the war room's routes, deploy rather than
only pull, with the intro's no-go list — the user's word, serve idle, worker not busy, no show
live, a checkout of main. Use `bash deploy/hub/deploy.sh serve`, which restarts serve alone, except
on the deploy that retires an old compose project: that one starts every service whatever is
named, so it needs the same care as a no-argument deploy. A serve someone started from a checkout
by hand beside the hub should be stopped, not restarted. (HANDOFF gotchas;
`docs/log/2026-10.md`, 2026-10-30.)

### A `--show` swallowed by the global `--show` (#61)

**Symptom.** `clippa analytics --show` and `clippa schedule --show` exit with `no
[shows.True] in the config`; the other way round, `clippa --show kino schedule` prints the
whole plan for every show and never says it ignored `--show kino`.

**Cause.** Both subcommand flags meant "print the list, change nothing" and were
`action="store_true"`, while the top-level parser's `--show <key>` picked a `[shows.<key>]`
profile. All three wrote the same `args.show`, so `True` reached `config.for_show` on one path
and the subcommand's `False` default wiped the global key on the other.

**How to tell.** `--help` on either subcommand shows two meanings for one name, and the
traceback names `for_show`.

**Band-aid.** Use the global flag only (`clippa --show kino schedule`), and the subcommand's
listing by another route.

**Fix.** [#61](https://github.com/byvfx/clippa/issues/61) renamed both to `--list` with their own
`dest="list_only"` (`cli.py:1575`, `cli.py:1653`; read at `cli.py:790` and `cli.py:1075`), and the
later per-command `--show` flags carry their own dest too (`lift_show`, `context_show`;
`cli.py:1660`, `cli.py:2148`). Keep the rule: no subcommand flag shares a `dest` with a global one.
(Found 2026-10-22, closed 2026-10-23.)

### A Windows install too deep, or without `tzdata`

**Symptom.** Two failures that look like broken code on a new Windows machine: an import dies
on a path inside `.venv`, or the first thing that reads a time fails with
`ZoneInfoNotFoundError`.

**Cause.** (a) With Windows long paths off (the default), a path past 260 characters can't be
opened; the `anthropic` package's own files already sit ~160 characters deep inside `.venv`, so
the checkout folder's length is the rest of the budget. (b) Windows ships no time zone
database, and `[schedule] timezone` is read through `ZoneInfo` all over the CLI (for example
`cli.py:740`, `doctor.py:1543`): without the `tzdata` wheel that raises. `pyproject.toml:18`
lists `tzdata` for `win32` only, for exactly this.

**How to tell.** The traceback names a deep `.venv\Lib\site-packages\...` path for (a); for
(b) `python -c "from zoneinfo import ZoneInfo; ZoneInfo('UTC')"` raises on that machine.

**Band-aid.** Move the checkout somewhere short and re-sync; `uv sync` installs `tzdata` from
`pyproject.toml` on Windows.

**Fix.** A rule: keep the folder short (`C:\clipper` is fine; the bootstrap refuses a longer
one and doctor warns, [Install](Install#requirements)) and keep `tzdata` a direct dependency,
never a transitive hope. (HANDOFF gotchas.)

## Review surfaces that mislead

A card shows less than the item, a retake note is read as an edit it is not, and a tap does more
than the one action on screen.

### A Discord reject may be about the excerpt

**Symptom.** A reject that makes no sense: the item plays fine in Kitsu, or the note asks for an
ending that the full preview already has.

**Cause.** The card's preview is the whole item only when a full-length encode can stay watchable.
`discord_preview` works out the video bitrate it would need (the upload limit at 8 bits a byte,
spread over the item's length, less 64 kbps of audio) and, when that falls under
`[discord] min_video_kbps` (350), keeps only the first `[discord] excerpt_s` (60) seconds so the
bitrate can climb back over the floor (`notify.py:487-517`). The card then says "First 60 s of ..."
and points at the full preview in Kitsu (`notify.py:646-649`), the only place the whole cut plays.

**How to tell.** The card's text starts "First 60 s of ...": the reject is about the first minute,
and the rest of the item went unreviewed.

**Band-aid.** Read the full preview in Kitsu before acting on that reject, and ask the reviewer
what they watched.

**Fix.** A rule (HANDOFF gotchas; [Reviewing](Reviewing#the-card-discord)). A server with a bigger
upload limit (`[discord] max_upload_mb`, 10) or a lower bitrate floor (`min_video_kbps`) gets the
whole item more often.

### The retake planner reads a cover-layout note as an edit (2026-10-28)

**Symptom.** A note about the cover's boxes — how many issues, where they sit — comes back as
"Retake not applied: unsupported", or worse, is misread as an edit it can make: "single issue"
became a 63 s trim of the video.

**Cause.** The planner's answer is a fixed schema: `trim`, `layout`, `title`, `description`,
`hashtags`, `thumbnail`, `unsupported` (`retake.py:66-90`). `layout` is only a Short's layout
*name* out of the show's `[shorts] layouts` (`retake.py:185-188`, `shorts.layouts_of`), and
applying it re-renders the Short with that layout (`retake.apply`, `retake.py:385-390`). The cover's
panel boxes are config it cannot write, so a layout rule in a note either comes back `unsupported`
or lands on whichever supported edit the model thinks is closest — the 10-28 case was a trim.

**How to tell.** `clippa retake <id> --item <key> --comment "..." --dry-run` prints the plan
before anything renders: a `trim`, `layout`, retitle or thumbnail change the note never asked for
is this.

**Band-aid.** `--dry-run` is the check before anything renders; undo a wrong plan with a hand plan
through `retake.apply`, then `publish_retake` to redo the Kitsu revision and the Discord card, but
only when the user asked to undo that retake, since it posts both (`retake.py:355`,
`retake.py:483`).

**Fix.** A rule: a layout rule belongs in the show's `[shorts.layouts.<name>]` config, and a
note should say what to cut, soften or retitle (HANDOFF gotchas; 2026-10-28).

### War room double sends, lost edits and bulk actions on hidden items (2026-11-10)

**Symptom.** Three things from one usability review: a phone double tap on Reject, Send retake
note, Save or Re-render goes twice (a retake note is two model calls and two entries in
`retakes.json`); closing the item window, J/K or a swipe throws away a typed title with no
warning; and a selection made under one filter still approves items not on screen.

**Cause.** None of those buttons had a busy state while their request ran; the item window had
no unsaved-changes guard; and the bulk path checked the picked refs against every loaded item
rather than the visible list ([#202](https://github.com/byvfx/clippa/issues/202), from reading
the code, not from trying it). A stale overview answer could also land after a newer one and
show an approve as undone.

**How to tell.** One tap leaves two retakes in Kitsu for the same card, or two retake notes in
`retakes.json`; or the selection bar says "N selected" while fewer items are on screen.

**Band-aid.** None: reload the page and act once.

**Fix.** [PR #211](https://github.com/byvfx/clippa/pull/211) did the busy states on the item
buttons and the visible-list selection; [PR #215](https://github.com/byvfx/clippa/pull/215) did
busy Sync and Replan, the unsaved-edits guard, a failed note going back into its box, and
dropping an older overview. A selection still survives a filter change, but bulk actions only
touch what is on screen and the bar says how many are left out ([War room](War-room)).
Closed 2026-11-11.

## Limits and estimates that mislead

Fine until a job is long or holds too many spans, a file is big, or eight of them run at once.

### Bleeps break on a stream with many of them

**Symptom.** `clippa cut` fails on a long, sweary segment and works on the same stream's quiet
ones, or (older code) a segment renders with words still audible while its short neighbours are
fine.

**Cause.** Every span went into one `volume` expression, one `between(t,…)` term each, and
ffmpeg's parser breaks on one expression past a few dozen spans — so it is the number of hits,
not the length, that decides.

**How to tell.** The cut's own line prints the count (`N bleeps`, `cut.py:184`; compilations
too, `cut.py:308`): dozens on the segment that fails, a handful on the ones that pass.

**Band-aid.** None per segment: the spans are the same in every mode, so a cut of that segment
fails until the code below is in place.

**Fix.** The spans go to a command file and ffmpeg reads them from it: `Bleeper.filter_script`
writes one command line per span to a `.cmd` file beside the output (`<key>.bleep.cmd`), and
the graph is a single `asendcmd=f='<file>',volume@dry=1` with the tone gated by `volume@tone`
(`bleep.py:61-88`). `ffmpeg_cut` passes that one-line graph inline rather than as
`-/filter_complex <file>`, because the NAS image's ffmpeg 5.1 lacks it (`cut.py:83-98`); the
spans are always the `.cmd` file's job. `Bleeper.intervals` drops a span that ends before the
cut point and clamps one that starts before it (`bleep.py:47-59`), so a cut never seeks past its
own bleeps. **Can come back** if someone folds the spans back into one expression: the graph is
one short line, and that is on purpose (HANDOFF; `docs/log/2026-10.md`, 2026-10-26).

### Kitsu won't take a big preview

**Symptom.** `clippa review publish` fails only on a stream's longest items with an error from
Kitsu's proxy that says nothing about size; every shorter item published.

**Cause.** Kitsu's proxy rejects an upload over `[review] max_upload_mb` (480), and what comes
back is a proxy/HTTP error rather than "too big", so the message points at the network or the
token.

**How to tell.** `<stream>/review/<key>.mp4` is at or over 480 MB, and the publish works for
every item under it.

**Band-aid.** None: the re-encode below is automatic.

**Fix.** After rendering, `review.publish` re-encodes any preview over `max_upload_mb * 1e6`
through `render_to_size` (`review.py:378-398`, called at `review.py:550`): 540p at the bitrate
that lands ~90% of the limit, kept as `<key>.small.mp4` and reused while it is newer than the
full preview. Lowering `[review] max_upload_mb` makes it kick in sooner. (HANDOFF.)

### Per-run Modal estimates add up to more than the bill (2026-10-23)

**Symptom.** The war room's Modal panel, or a sum of `est_usd` over `logs/modal_runs.jsonl`,
is well above what Modal charges; the Jer archive's estimates summed to ~$182 against a real
bill under that.

**Cause.** Each end line's `est_usd` is an upper bound for one call: `(wall + 30) / 3600 × the
GPU's hourly price`, where `wall` is the client's own clock for the call — upload and cold start
included — plus 30 s of scaledown (`transcribe.modal_cost`, `transcribe.py:246-254`). Their sum is
only another upper bound, not the bill: thousands of them, eight containers at a time, came to
~$182 for the Jer archive while Modal billed less (the gap is on parallel archive runs, 10-23).

**How to tell.** `clippa modal` prints both in one output: "Modal (…) this month: $X metered,
$Y billed after $Z credit" from Modal's own billing, then "our estimates: $A this month"
(`modalmon.runs_summary`, `modalmon.py:105-106`). If the estimates line is higher, it is the
estimates that are wrong.

**Band-aid.** Read the billing line, never the sum, and say so when a spend figure is quoted.

**Fix.** A rule: Modal's `Workspace.billing` is the number for money spent; the per-run lines are
for one run's look and for `archive status`'s pace and ETA (HANDOFF; `docs/log/2026-10.md`,
2026-10-23 night; [Transcribe on Modal](Transcribe-on-Modal#watching-modal)).
