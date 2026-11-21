A small chat page where you tell Clippa what you want done, in your own words, and Clippa
answers like a buddy in an old instant messenger. `clippa serve` serves it at `/chat`
(`http://localhost:8765/chat`, same token as the war room), and the [War room](War-room) shows
the same page at its side ([the chat at the side](War-room#the-chat-at-the-side)), so it is one
chat either way. It works on a phone: the buddy list folds away.

## What it can do

Clippa can only use the tools in its catalog (`clippa/catalog.py`), each with a risk:

| Risk | Tools | What happens |
|---|---|---|
| read | releases (what goes out on a day), unslotted, items, item, retakes, health, costs | answers at once |
| write | approve, reject, retake (a changes note), reopen, hold / release, pin / unpin, retitle, cover text, slot (next open slot), bulk (several items on one card), undo | a card; nothing changes until you tap **Yes** |
| spend | story (a new [story dossier](Story-dossiers)), cover redo (a vision call, a second if the first pick is unusable) | a card with the cost estimate first; **Yes** checks the item again and starts the job |

A write goes through the same path as the war room's buttons (`actions.decide`, so Kitsu is
the record), signed "Clippa chat", and is undoable from the war room's undo list, where it
shows as "(chat)". A pin or hold replans `schedule.json` straight away. A job posts its result
into the open chat when it ends.

It has no tool for uploads or publishing, config, code, deploys, `clippa services` or the
recorder; asked for any of those, it says it can't do that from here.

## Several at once

"approve all comps", "hold the shorts from kino 11-14", "approve s07 and s09 from the kino one"
or "reject all clips from c404 too long" make one card that lists every item it covers; **Yes**
does them all. It takes only the items the action fits (an approve skips what is decided
already, a release what isn't held; nothing that's on YouTube) and says how many it left. Up to
25 on a card: more, and it asks you to narrow it to a stream, a kind or words from the titles.
The tap checks each item again, does the ones that haven't changed, and names the ones it
left. The whole card is one entry in the war room's undo list.

"undo that" makes a card for the last action made here or in the war room (the war room's
**Undo**, newest first).

## Renaming a video that's up

"retitle s02 A Better Title" (or "rename the Blade one to ...") works on a video that's
already on YouTube too: the card says **Rename s02 on YouTube?**, links the video, and shows
the title as YouTube will show it (with the show name, as an upload adds it). **Yes** renames it
on YouTube first, then Kitsu's title column, so the two never disagree; the description, tags
and everything else stay as they are on YouTube. Each rename is 51 of the day's API quota
units, taken only from what the day's uploads don't need ([Scheduling and uploads](Scheduling-and-uploads#quota));
with none spare, Clippa says so instead of making a card. The war room's undo renames it back.
Asking for the title Kitsu has already still makes a card on a video that's up, in case it was
renamed in Studio. The war room, the bot and `clippa review retitle` rename the same way
([Scheduling and uploads](Scheduling-and-uploads#renaming-a-video-thats-up)).

Anything else about a video that's up (its cover, slot, privacy) is still done in YouTube Studio.

## How a message is read

1. A box command (below) or something Clippa can match locally: "what goes out tomorrow",
   "what's unslotted", "pin s03 kick-131151926 friday 9am", "approve all comps", "undo that",
   an item name alone. No model call. A bare hour
   ("fri 3") could be morning or afternoon, so that one goes to the model.
2. Anything else goes to the model once (`[chat] model`, Haiku by default), which may only
   pick one catalog tool or answer in a sentence. It costs about $0.0005 a message, written to
   the cost ledger as `chat` ([Costs](Costs)). Past `[chat] daily_usd` a day, only local
   matching works.

An item can be named by its id (`kick-131151926/s03`, `s03`, `c03_t01`) or by words from its
title. A stream can be named by its id, or the way you'd say it: a show (its key, channel or a
word of its name) and a month-day, "s02 from kino 11-14", the show alone for its latest stream
("from kino", "the kino one"), or "from yesterday" / "from last night". Replies name streams the same way ("Kino
Casino 11-14"). When more than one item fits, Clippa asks "Which one?" with a button for each,
and the stream beside any two that share a name.

Right after a write card (the only one open, nothing said since), typing "yes", "ok do it" or
"no" answers it, the same as its buttons. A spend card always waits for its button.

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

The page comes in two looks: `millennium` (beige windows, rounded blue title bars,
tooltip-yellow cards), its default, and `warroom` (the [War room](War-room#looks)'s dark editor
look). `retro` and `pager` were retired on 2026-11-18. Pick one
with **Look** under the buddy list (in the war room, also on the Settings page). One pick dresses the
war room and the chat together: the browser remembers it and its other tabs follow.
`[chat] theme` sets the one this page starts with in a browser that never picked one; the war
room starts as `warroom`. A look picked here before the war room had looks stays this page's
until you pick again.

A look is one file in `clippa/web/themes/`: the custom properties that `chat.css` and the war
room's `warroom.css` read (colours, fonts, borders, radii, the title bars and buttons, the window
titles as `--label-buddies` and `--label-chat`, `--scheme` for the chat's native controls) plus
any rules of its own, which name one page's own classes. `theme.js` loads it on both pages. Add
a file there and it shows up in both lists; a test checks that every look defines every
property, and that the war room's colours live in `themes/warroom.css` only.

## Settings

`[chat]` in the config: `model` (`""` for local matching only), `effort`, `prompt_version`
(`clippa/prompts/chat_<v>.md`; v3 knows bulk and undo, and says so when it can't do a thing
rather than answering with a list), `quiet_hours`, `history` (how many of the topic's messages the
model sees), `daily_usd` and `theme`. The chat is stored in `[paths] chats` (`chats/current.json` beside
the streams, on the share), written by serve only.
