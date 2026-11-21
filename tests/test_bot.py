"""The Discord bot (clippa/bot.py), the one process holding the token. Its handlers are thin:
resolve the item, decide through clippa/actions.py, replan through serve; the allowlist
refuses everyone else. Needs discord.py (the `discord` extra; CI installs it)."""
import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("discord")

from clippa import actions, bot, serve


def test_chunks_keeps_each_message_under_the_limit():
    out = bot.chunks(["x" * 1000] * 5, size=2500)
    assert len(out) == 3 and all(len(c) <= 2501 for c in out)   # each line ends with a newline
    assert bot.chunks([]) == []


def make_bot(defaults) -> bot.Bot:
    cfg = {**defaults, "discord": {**defaults["discord"], "allowed_user_ids": [7], "guild_id": "42"}}
    return bot.Bot(cfg)


def test_do_decides_through_actions_and_replans_through_serve(defaults, monkeypatch):
    b = make_bot(defaults)
    item = actions.Item("s", "c01", {"kind": "pick"})
    calls = []
    monkeypatch.setattr(actions, "resolve", lambda cfg, ref: calls.append(("resolve", ref)) or item)
    monkeypatch.setattr(actions, "decide", lambda cfg, it, action, who, text="", on=None, values=None:
                        calls.append(("decide", action, text, values)) or {"short": "s/c01"})
    monkeypatch.setattr(actions, "replan", lambda cfg, it, action, row, via=None:
                        calls.append(("replan", action, via is serve.replan)) or (row, "scheduled Fri 16:00"))
    row, note = b.do("approve", "s/c01", "", "Discord @me")
    assert row == {"short": "s/c01"} and note == "; scheduled Fri 16:00"
    assert calls == [("resolve", "s/c01"), ("decide", "approve", "", None), ("replan", "approve", True)]
    calls.clear()
    b.do("details", "s/c01", {"Tags": "a, b"}, "Discord @me")   # the Retitle form: columns as one edit
    assert calls[1] == ("decide", "edit", "", {"Tags": "a, b"})
    monkeypatch.setattr(actions, "replan", lambda cfg, it, action, row, via=None: (row, ""))
    assert b.do("hold", "s/c01", "", "Discord @me") == ({"short": "s/c01"}, "")   # no note when nothing moved


class FakeResponse:
    def __init__(self, done: bool):
        self.done, self.sent = done, []

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, msg: str, ephemeral: bool = False) -> None:
        self.sent.append(msg)


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, msg: str, ephemeral: bool = False) -> None:
        self.sent.append(msg)


def interaction(user_id: int, guild_id: int = 42, done: bool = False) -> SimpleNamespace:
    return SimpleNamespace(user=SimpleNamespace(id=user_id, name="who"), guild_id=guild_id,
                           response=FakeResponse(done), followup=FakeFollowup())


def test_check_refuses_anyone_off_the_allowlist_or_in_another_guild(defaults):
    b = make_bot(defaults)
    ok = interaction(7)
    assert asyncio.run(b.check(ok)) is True and ok.response.sent == []
    stranger = interaction(8)
    assert asyncio.run(b.check(stranger)) is False
    assert stranger.response.sent == ["You're not on this bot's allowlist."]
    elsewhere = interaction(7, guild_id=1, done=True)   # after a defer the answer goes by followup
    assert asyncio.run(b.check(elsewhere)) is False and elsewhere.followup.sent == ["You're not on this bot's allowlist."]


class FakeChannel:
    """A text channel (or thread): what was sent, each message able to start a thread."""

    def __init__(self, cid: int = 5):
        self.id, self.name, self.sent, self.threads = cid, "blinds", [], []

    async def send(self, text, view=None, allowed_mentions=None):
        self.sent.append((text, [c.custom_id for c in view.children] if view else []))
        msg = SimpleNamespace(id=100 + len(self.sent))

        async def create_thread(name, auto_archive_duration=None):
            t = FakeChannel(900 + len(self.threads))
            self.threads.append((name, t))
            return t
        msg.create_thread = create_thread
        return msg


def test_the_bot_posts_alerts_then_the_digest_in_a_thread(defaults, monkeypatch):
    from clippa import blinds

    b, chan, marks = make_bot(defaults), FakeChannel(), []
    own = [{"id": "cdan-1", "ours": True}]
    monkeypatch.setattr(blinds, "own_alerts", lambda cfg: own)
    monkeypatch.setattr(blinds, "digest_due", lambda cfg: "2026-11-15")
    monkeypatch.setattr(blinds, "start_digest", lambda cfg, day: ([{"id": "cdan-1"}, {"id": "cdan-2"}], 3))
    monkeypatch.setattr(blinds, "alert_text", lambda bl, n=0: f"{n}:{bl['id']}")
    monkeypatch.setattr(blinds, "mark_alerted", lambda cfg, bid, where: marks.append(("alerted", bid, where)))
    monkeypatch.setattr(blinds, "mark_digested", lambda cfg, bid, day, thread: marks.append(("digest", bid, thread)))

    async def channel():
        return chan
    monkeypatch.setattr(b, "blinds_channel", channel)
    asyncio.run(b.post_blinds())
    assert chan.sent[0] == ("0:cdan-1", ["clippa:blindguess:cdan-1", "clippa:blindnone:cdan-1",
                                         "clippa:blindnot:cdan-1"])
    assert "2 new" in chan.sent[1][0] and "3 older" in chan.sent[1][0] and chan.sent[1][1] == []
    name, thread = chan.threads[0]
    assert name == "Blinds 2026-11-15" and [t for t, _ in thread.sent] == ["1:cdan-1", "2:cdan-2"]
    assert marks == [("alerted", "cdan-1", "5/101"), ("digest", "cdan-1", "900"), ("digest", "cdan-2", "900")]
    own.clear()
    monkeypatch.setattr(blinds, "digest_due", lambda cfg: "")
    monkeypatch.setattr(b, "blinds_channel", lambda: (_ for _ in ()).throw(AssertionError("no channel needed")))
    asyncio.run(b.post_blinds())


def test_a_blind_button_answers_and_ends_the_message_with_it(defaults, monkeypatch):
    from clippa import blinds

    b, got, edits = make_bot(defaults), [], []
    monkeypatch.setattr(blinds, "answer", lambda cfg, bid, name, why, none, not_ours, who="":
                        got.append((bid, name, why, none, not_ours, who)) or
                        {"status": "no_idea" if none else "answered", "guesses": []})

    async def edit_message(content, allowed_mentions=None):
        edits.append(content)
    it = interaction(7)
    it.message = SimpleNamespace(content="1:cdan-1\n→ Your guess: **Old**")
    it.response.edit_message = edit_message
    asyncio.run(b.on_button(it, "blindnone", "cdan-1"))
    assert got == [("cdan-1", "", "", True, False, "Discord @who")] and edits == ["1:cdan-1\n→ No idea."]

    def refuse(*a, **k):
        raise bot.ClipperError("no blind 'x'")
    monkeypatch.setattr(blinds, "answer", refuse)
    asyncio.run(b.on_button(it, "blindnot", "x"))
    assert it.response.sent == ["Nothing changed: no blind 'x'"]
    modal = []
    it.response.send_modal = lambda m: modal.append(m) or asyncio.sleep(0)
    asyncio.run(b.on_button(it, "blindguess", "cdan-1"))
    assert isinstance(modal[0], bot.BlindGuess) and modal[0].bid == "cdan-1"


def test_a_bad_digest_time_or_no_threads_still_posts(defaults, monkeypatch):
    from clippa import blinds

    b, chan, posted = make_bot(defaults), FakeChannel(), []
    monkeypatch.setattr(blinds, "own_alerts", lambda cfg: [{"id": "a"}])
    monkeypatch.setattr(blinds, "alert_text", lambda bl, n=0: f"{n}:{bl['id']}")
    monkeypatch.setattr(blinds, "mark_alerted", lambda cfg, bid, where: posted.append(bid))

    def bad(cfg):
        raise bot.ClipperError("[blinds] digest_at '8pm': use HH:MM")
    monkeypatch.setattr(blinds, "digest_due", bad)

    async def channel():
        return chan
    monkeypatch.setattr(b, "blinds_channel", channel)
    with pytest.raises(bot.ClipperError):
        asyncio.run(b.post_blinds())
    assert posted == ["a"]                                       # the alert went out before the digest failed

    import discord
    monkeypatch.setattr(blinds, "own_alerts", lambda cfg: [])
    monkeypatch.setattr(blinds, "digest_due", lambda cfg: "2026-11-15")
    monkeypatch.setattr(blinds, "start_digest", lambda cfg, day: ([{"id": "c"}], 0))
    monkeypatch.setattr(blinds, "mark_digested", lambda cfg, bid, day, thread: posted.append((bid, thread)))
    chan.sent.clear()
    real_send = chan.send

    async def send(text, view=None, allowed_mentions=None):
        msg = await real_send(text, view, allowed_mentions)

        async def no_thread(name, auto_archive_duration=None):
            raise discord.HTTPException(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
        msg.create_thread = no_thread
        return msg
    chan.send = send
    asyncio.run(b.post_blinds())
    assert [t for t, _ in chan.sent][1] == "1:c" and posted[-1] == ("c", "5")   # in the channel itself


def test_a_form_edit_counts_paragraphs_not_wrapping():
    """Discord may re-wrap a field (#366 review): line breaks inside a paragraph don't make an edit,
    a blank line added or taken out does."""
    assert bot.same_text("Line one\nLine two", "Line one Line two")      # re-wrapped: no edit
    assert bot.same_text("a b\r\n\r\nc", "a  b\n\nc ")                     # CRLF and spaces: no edit
    assert not bot.same_text("Line one\n\nLine two", "Line one\nLine two")  # a paragraph break: an edit
    assert not bot.same_text("one two", "one three")
    assert bot.same_text("", "") and bot.same_text("\n\n", "")
