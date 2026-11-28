# Fetching VODs

`clippa fetch <url>` turns a finished broadcast into a stream, whatever the platform; the
rest of the pipeline (`clippa run`) is the same.

| URL | Stream id | Video | Chat |
|---|---|---|---|
| `kick.com/<channel>/videos/<id>` | `kick-<livestream id>` | HLS, `[kick] max_height` | chat history |
| `twitch.tv/videos/<id>` | `twitch-<id>` | yt-dlp, `[twitch] max_height` | chat replay |
| `youtube.com/watch?v=<id>`, `youtu.be/<id>`, `youtube.com/live/<id>` | `youtube-<id>` | yt-dlp, `[youtube] fetch_max_height` | live-chat replay, if the video has one |

`--no-video` pulls the details and chat only; `--no-chat` the video only. A rerun keeps what
is already there.

## A file you already have

`clippa fetch <path>` takes a video already on disk (mp4, mkv, mov, m4v, ts, webm, flv, avi,
mpg, wmv) and makes it a stream: `local-<file name>-<hash>`, with the video as `source.<ext>`
and a `meta.json`. There's no chat, so picks lean on the transcript and the audio.

```bash
clippa --show kino fetch "D:/vods/Kino Night 12.mkv" --started 2026-11-24T20:00
clippa run local-kino-night-12-1c32e3
```

| Flag | What it does |
|---|---|
| `--show <key>` (before `fetch`) | the show it belongs to: its first channel goes in `meta.json`, so every later command uses that show's settings. Without it, the base config applies |
| `--link` / `--move` | hardlink or move the file into the stream folder instead of copying it. A hardlink needs the same drive as the streams folder |
| `--title` | the title (default: the file name) |
| `--started` | when it was streamed, an ISO date or time, in the show's `[schedule] timezone` unless it says otherwise (default: the video's own creation date, else the file's) |

The id's hash comes from the file's size and its first and last MiB, so fetching the same file
again, even renamed, says which stream it already is and does nothing, or puts the video back if
that stream's was deleted or pruned. One file at a time for now; folders are next (#434). A file
already inside the streams folder is refused, except a stopped import's own `source.<ext>`: if a
`--move` was interrupted before the stream was finished (no `meta.json` in its folder), fetching
that file finishes it.

## Without Kitsu

With `[review] host` empty, `clippa run` skips the two review stages (`clips_review`, `review`)
and finishes with the clips, segments and Shorts in the stream's folder (`clips/`, `segments/`,
`shorts/`). `run.json` says why they were skipped. Once Kitsu is set up, `clippa run <stream>`
sends them to review; nothing else is redone. `clippa doctor` warns about the missing host instead of failing,
except for the roles that review through Kitsu (serve and the bot).

## Which show it belongs to

The stream's `channel` (a Twitch login, a YouTube handle without the `@`, a Kick slug) must be
in a show's `channels` list in `clipper.toml`, or the stream belongs to no show. Add it first
([Adding a show](Adding-a-show)).

## Notes

- **Twitch chat** takes about 5 minutes for a 3-hour VOD: Twitch serves a minute or so of
  messages per request. A subscriber-only VOD needs the cookies of a signed-in browser as a
  Netscape `cookies.txt`; save it as `secrets/twitch_cookies.txt` (or set `[paths] twitch_cookies`); no file means
  none. `clippa doctor` checks it. Never commit it. The chat comes from Twitch's own web
  query, which Twitch can change (a burst of more than one page of messages in a single
  second keeps only its first page); a pull that stops says so and a rerun resumes.
- **YouTube** age and bot checks retry once signed in, as in [YouTube sign-in](YouTube-sign-in).
  A normal upload has no chat; the stream then has none, and the chat signals simply have
  nothing to score.
- Live recording is Kick only ([NAS recorder](NAS-recorder)).
