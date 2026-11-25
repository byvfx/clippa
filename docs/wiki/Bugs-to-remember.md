Bugs whose symptom points away from the cause, or that will come back with new hardware, a recorder
or an ffmpeg, written down so the next person or agent recognises them in minutes instead of
starting from the log. One entry per bug, newest first: the **symptom** (what you see), the
**cause**, **how to tell** (the check), the **band-aid** that keeps the pipeline moving, and the
**fix**. The `bug-to-remember` label marks an issue as a candidate; the fix's PR adds its entry
here.

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
