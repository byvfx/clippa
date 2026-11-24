Every model call (#50) appends its cost to `[paths] costs` (`logs/costs.jsonl`) with the
command and stream it ran for; `clippa costs` and the [war room](War-room#modal-costs-and-analytics)'s
Costs panel add that to the Modal run log and your fixed costs: put plans and subscriptions in
`[costs] monthly` (`{"Runway Max" = 95}`) and a provider's monthly credit in `[costs] credits`
(`{"modal" = 30}`, shown as credit, not hidden). `clippa costs --backfill` once reads back
model spend earlier runs wrote into their output files (score, segments, receipts, weekly).

```bash
clippa costs [--month 2026-10]     # month's spend: models + Modal + fixed, by stage/show/stream
clippa costs --providers           # the Claude API's and OpenRouter's own count, next to Clippa's log
clippa modal                       # GPUs up, runs, spend, archive ETA
```

## The providers' own numbers

Clippa's log prices each call from `[llm] prices` (OpenRouter reports its own cost). `clippa costs
--providers` and the war room's [API usage panel](War-room#modal-costs-and-analytics) ask the
providers instead, so spend the log missed (another tool on the same key, the Console's
workbench, a crash before the line was written) shows up too. Each is asked at most every 5
minutes.

- **Claude API**: the Admin API's cost report, month to date by model. It needs an Admin API
  key (`sk-ant-admin...`, Console > Settings > Admin keys; Console organizations only, not
  individual accounts): set `ANTHROPIC_ADMIN_KEY`, or put it in
  `secrets/anthropic_admin_secret.txt` (`[paths] anthropic_admin_key`). The normal
  `ANTHROPIC_API_KEY` can't read it. Costs appear about 5 minutes after a call. Without the key
  the panel shows Clippa's log for Claude and says what to set; `clippa doctor` warns.
- **OpenRouter**: the key Clippa uses (`OPENROUTER_API_KEY`, or the file at
  `[paths] openrouter_key`, `secrets/openrouter_secret.txt` by default): today, this week,
  this month, all time, what's left of the key's limit if it has one, and the account
  balance when the key may read it.

## The OpenRouter budget

The war room's Money page has an **OpenRouter budget** panel ahead of Costs, Modal and
API usage ([War room](War-room#modal-costs-and-analytics)). It shows the clippa key's cap, what's left,
today / this week / this month, and the account balance. The warning percent (default 75) is
saved in `<streams>/.serve/openrouter.json` with the limit events. At or past the warning,
Spend turns amber and Money shows **!**. A spent cap (`openrouter_key_limit`) turns Spend
red and the health light to **Stopped**. The banner says the run stopped at its next model
call. A 402 whose Retry-After is at most two minutes is the in-flight budget
(`openrouter_in_flight_budget`) when the text says in-flight, or when it says nothing
about the key limit or the credits being spent. The call waits and is sent again, and
the panel counts down. Any other 402, and a 403 whose text says the limit or the credits
are spent, is the cap (`openrouter_key_limit`).

**Top up on OpenRouter** is a link to the credits page. Buying credits and auto top-up stay
on OpenRouter; its API cannot change them.

Changing the limit or disabling the key needs a management key, which never reaches the
browser: `OPENROUTER_MANAGEMENT_KEY`, or the file at `[paths] openrouter_management_key`
(`secrets/openrouter_management_secret.txt`). With only the inference key the panel still
reads the clippa key and the buttons stay disabled. With a management key that can see
the clippa key, **Edit limit…** (new limit, then a Now → After confirm) and **Disable
key…** / **Enable key…** send `PATCH /api/v1/keys/{hash}`. The hash
stays on the server. `--budget` on a run is Clippa's own guard for that one run; the panel
only explains it. The OpenRouter cap covers every run on the key.

Modal's own billing is the real number for transcription ([Transcribe on Modal](Transcribe-on-Modal#watching-modal));
the bot DMs each time the month passes a multiple of `[modal] alert_every_usd`.

## What things cost

> [!NOTE]
> Rough figures measured on this install's runs; `--dry-run` gives the real estimate where a
> command offers it.

| Job | Cost |
|---|---|
| Transcribe an 8 h stream on Modal | ~$0.34 (L40S), ~$0.26 (L4) |
| Weekly issue draft + three articles | ~$0.35 + ~$0.10 each |
| `archive ask` | ~1c |
| `receipts find` / `script` | ~$0.20 / ~$0.02 |
| `investigate facts` | ~$0.20 for 43 h; the whole Jer catalog ~$14 once |
| Search swarm, per lead | ~$0.01-0.03 |
| News check (Haiku) | well under a cent |
| Cast photo label | ~$0.0002 a photo; Brave image search ~$5 per 1,000 |
| X post | ~$0.015 (a link post $0.20, so Clippa never adds one) |

### One stream, stage by stage

Median spend per stream over this install's logged runs (27 streams, most 7-9 h, October to
November 2026; `clippa costs` has the live numbers):

| Stage | Median | Upper quartile |
|---|---|---|
| `transcribe` (Modal) | $0.45 | $0.60 |
| `--diarize`, on top | $0.16 | $0.18 |
| `score` (both passes) | $0.18 | $0.21 |
| `segments` (one call over the whole transcript) | $0.81 | $0.92 |
| `meta` | $0.88 | $1.05 |
| `thumbs` (a pick per video, frames as images) | $1.01 | $1.36 |
| `shorts`, `x` | about a cent | ~$0.04 |

So a full run on an evening show is about $3.50. A rerun of `score`, `segments`, `meta` or
`thumbs` pays again (`score` refuses without `--force`); each of the four prints what a run
would cost with `--dry-run`, from the prompts it would send (four characters a token, times
`[llm] prices`, or OpenRouter's published list for an `openrouter:` name). A model with no
price prints `(no [llm] price: counted $0)`. The command calls nothing. `clippa status` ends with the month's spend by stage.
