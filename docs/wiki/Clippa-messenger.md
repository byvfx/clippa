A small chat page where you tell Clippa what you want done, in your own words, and Clippa
answers like a buddy in an old instant messenger. `clipper serve` serves it beside the
[War room](War-room) at `/chat` (`http://localhost:8765/chat`, same token as the war room);
the war room itself is unchanged. It works on a phone: the buddy list folds away.

## What it can do

Clippa can only use the tools in its catalog (`clipper/catalog.py`), each with a risk:

| Risk | Tools | What happens |
|---|---|---|
| read | releases (what goes out on a day), unslotted, items, item, retakes, health, costs | answers at once |
| write | approve, reject, retake (a changes note), reopen, hold / release, pin / unpin, retitle, cover text, slot (next open slot) | a card; nothing changes until you tap **Yes** |
| spend | story (a new [story dossier](Story-dossiers)), cover redo (a vision call, a second if the first pick is unusable) | a card with the cost estimate first; **Yes** checks the item again and starts the job |

A write goes through the same path as the war room's buttons (`actions.decide`, so Kitsu is
the record), signed "Clippa chat", and is undoable from the war room's undo list, where it
shows as "(chat)". A pin or hold replans `schedule.json` straight away. A job posts its result
into the open chat when it ends.

It has no tool for uploads or publishing, config, code, deploys, `clipper services` or the
recorder; asked for any of those, it says it can't do that from here.

## Renaming a video that's up

"retitle s02 A Better Title" (or "rename the Blade one to ...") works on a video that's
already on YouTube too: the card says **Rename s02 on YouTube?**, links the video, and shows
the title as YouTube will show it (with the show name, as an upload adds it). **Yes** renames it
on YouTube first, then Kitsu's title column, so the two never disagree; the description, tags
and everything else stay as they are on YouTube. Each rename is 51 of the day's API quota
units, taken only from what the day's uploads don't need ([Scheduling and uploads](Scheduling-and-uploads#quota));
with none spare, Clippa says so instead of making a card. The war room's undo renames it back.
`clipper review retitle <item> <title>` does the same from a terminal.

Anything else about a video that's up (its cover, slot, privacy) is still done in YouTube Studio.

## How a message is read

1. A box command (below) or something Clippa can match locally: "what goes out tomorrow",
   "what's unslotted", "pin s03 kick-131151926 friday 9am", an item name alone. No model call. A bare hour
   ("fri 3") could be morning or afternoon, so that one goes to the model.
2. Anything else goes to the model once (`[chat] model`, Haiku by default), which may only
   pick one catalog tool or answer in a sentence. It costs about $0.0005 a message, written to
   the cost ledger as `chat` ([Costs](Costs)). Past `[chat] daily_usd` a day, only local
   matching works.

An item can be named by its id (`kick-131151926/s03`, `s03`, `c03_t01`) or by words from its
title; give the stream id too and it finds it faster. When more than one item fits, Clippa
asks "Which one?" with a button for each.

## Topics

The model only sees the current topic, never older ones. A new topic starts with
**New topic** (`/new`), with **×** (`/bye`), or by itself after `[chat] quiet_hours` (2) without a
message; each ends with a receipt line (messages, actions done, model cost). A card left
open when its topic ends expires, and every card checks its item again when you tap it: if
the item changed since (someone approved it in the war room, say) the card says so instead of
acting.

## Box commands

| Command | Does |
|---|---|
| `/new` | start a new topic |
| `/bye` | end the topic |
| `/clear` | move the whole chat to `chats/archive/` and start fresh |
| `/history` | the last ten topics and their receipts |
| `/cost` | this topic's model cost and the chat's spend today (ask "costs" for every stage) |
| `/away`, `/back` | hold job reports while you're away, then show them |
| `/help` | the commands and a few examples |

## The buddy list

Serve, the bot, the worker and the recorder show as buddies, from their heartbeats (the same
ones [How the processes talk](How-the-processes-talk) describes): busy (working on a job), idle,
away (the recorder while a show is live) or off (no fresh heartbeat).

## Looks

The page comes in four looks: `retro` (grey bevelled windows on a teal desk), `millennium`
(beige windows, rounded blue title bars, tooltip-yellow cards), `pager` (green title bars, the
sender's name on its own line) and `warroom` (the [War room](War-room)'s dark palette). Pick one
with **Look** under the buddy list; the page remembers it in that browser. `[chat] theme` sets the
one a new browser starts with.

A look is one file in `clipper/web/themes/`: the custom properties the page's `chat.css` reads
(colours, fonts, borders, radii, the window titles as `--label-buddies` and `--label-chat`) plus
any rules of its own. Add a file there and it shows up in the list; a test checks that every
look defines every property, and that `warroom` keeps the war room's colours.

## Settings

`[chat]` in the config: `model` (`""` for local matching only), `effort`, `prompt_version`
(`clipper/prompts/chat_<v>.md`), `quiet_hours`, `history` (how many of the topic's messages the
model sees), `daily_usd` and `theme`. The chat is stored in `[paths] chats` (`chats/current.json` beside
the streams, on the share), written by serve only.
