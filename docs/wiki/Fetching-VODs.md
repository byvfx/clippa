# Fetching VODs

`clipper fetch <url>` turns a finished broadcast into a stream, whatever the platform; the
rest of the pipeline (`clipper run`) is the same.

| URL | Stream id | Video | Chat |
|---|---|---|---|
| `kick.com/<channel>/videos/<id>` | `kick-<livestream id>` | HLS, `[kick] max_height` | chat history |
| `twitch.tv/videos/<id>` | `twitch-<id>` | yt-dlp, `[twitch] max_height` | chat replay |
| `youtube.com/watch?v=<id>`, `youtu.be/<id>`, `youtube.com/live/<id>` | `youtube-<id>` | yt-dlp, `[youtube] fetch_max_height` | live-chat replay, if the video has one |

`--no-video` pulls the details and chat only; `--no-chat` the video only. A rerun keeps what
is already there.

## Which show it belongs to

The stream's `channel` (a Twitch login, a YouTube handle without the `@`, a Kick slug) must be
in a show's `channels` list in `clipper.toml`, or the stream belongs to no show. Add it first
([Adding a show](Adding-a-show)).

## Notes

- **Twitch chat** takes about 5 minutes for a 3-hour VOD: Twitch serves a minute or so of
  messages per request. A subscriber-only VOD needs the cookies of a signed-in browser as a
  Netscape `cookies.txt`; put its path in `[twitch] cookies` (for example
  `secrets/twitch_cookies.txt`). Never commit it.
- **YouTube** age and bot checks retry once signed in, as in [YouTube sign-in](YouTube-sign-in).
  A normal upload has no chat; the stream then has none, and the chat signals simply have
  nothing to score.
- Live recording is Kick only ([NAS recorder](NAS-recorder)).
