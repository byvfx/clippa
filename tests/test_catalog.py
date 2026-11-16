"""The chat's command catalog (#158): risk tags, what it may never reach, cards and re-checks."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clipper import actions, catalog
from clipper.errors import ClipperError

TZ = "America/Los_Angeles"


@pytest.fixture
def cfg(defaults, tmp_path):
    defaults["paths"].update(streams=tmp_path / "streams", schedule=tmp_path / "schedule.json",
                             costs=tmp_path / "costs.jsonl", chats=tmp_path / "chats")
    defaults["schedule"]["timezone"] = TZ
    (tmp_path / "streams" / "kick-1").mkdir(parents=True)
    return defaults


def row(**over) -> dict:
    r = {"ref": "kick-1/s02_blade", "short": "kick-1/s02", "key": "s02_blade", "stream": "kick-1",
         "kind": "segment", "title": "Blade in Vegas", "topic": "", "hook": "", "decision": "pending",
         "hold": False, "publish_at": "", "thumb_text": "BLADE", "uploaded": False, "scheduled": "",
         "status": "wfa", "comment": "", "checks": [], "kitsu_url": "", "dropped": ""}
    return {**r, **over}


@pytest.fixture
def one(cfg, monkeypatch):
    """find_item and the item's row stubbed: the card logic alone."""
    item = actions.Item(cfg["paths"]["streams"] / "kick-1", "s02_blade", {"file": "segments/s02_blade.mp4"})
    state = {"row": row()}
    monkeypatch.setattr(catalog, "find_item", lambda cfg, text: (item, []))
    monkeypatch.setattr(catalog, "_row", lambda cfg, it: dict(state["row"]))
    return state


def test_every_tool_has_a_risk_and_nothing_forbidden():
    tools = catalog.catalog()
    assert {t.risk for t in tools.values()} <= set(catalog.RISKS)
    assert {"releases", "items", "approve", "pin", "story"} <= set(tools)
    # nothing that uploads, publishes, configures, deploys or touches services or the recorder
    banned = set(catalog.FORBIDDEN) | {"publish", "deploy", "services", "record", "config"}
    assert not banned & set(tools)
    for name in banned:
        with pytest.raises(ClipperError, match="isn't something I can do"):
            catalog.prepare({}, name, {})
    assert not set(catalog.FORBIDDEN_VERBS) & {v for v, _ in catalog.CLI_WRITES.values()}


def test_writes_are_built_from_the_review_parsers():
    """Descriptions are the `clipper review <verb>` help; the inputs come from its arguments."""
    tools = catalog.catalog()
    assert tools["approve"].description == "approve an item"
    assert tools["approve"].risk == catalog.WRITE
    assert set(tools["pin"].schema["properties"]) == {"item", "when", "unpin"}
    assert tools["pin"].schema["properties"]["unpin"]["type"] == "boolean"
    assert tools["reject"].schema["required"] == ["item", "reason"]
    story = tools["story"].schema
    assert story["properties"]["days"]["type"] == "integer"
    assert story["properties"]["people"]["type"] == "array"
    assert tools["story"].risk == catalog.SPEND
    assert tools["approve"].spec()["description"].startswith("[write] ")


def test_a_renamed_cli_argument_fails_loudly():
    with pytest.raises(ClipperError, match="no arguments for"):
        catalog.schema_from_parser(("review", "pin"), {"item": ("item", True, ""), "nope": ("x", False, "")})


def test_risk_rules(cfg, one):
    """A read answers; a write is a card with a snapshot and no estimate; nothing acts."""
    p = catalog.prepare(cfg, "approve", {"item": "s02"})
    assert p.kind == "card" and p.risk == catalog.WRITE and p.ref == "kick-1/s02_blade"
    assert p.snapshot["decision"] == "pending" and not p.estimate and not p.argv
    one["row"] = row(decision="approved")
    assert catalog.prepare(cfg, "approve", {"item": "s02"}).kind == "answer"   # already approved
    assert catalog.prepare(cfg, "reject", {"item": "s02"}).kind == "answer"    # no reason: asks
    card = catalog.prepare(cfg, "reject", {"item": "s02", "reason": "too long"})
    assert card.kind == "card" and card.args == {"verb": "reject", "text": "too long", "on": None}
    one["row"] = row(uploaded=True)
    assert "on YouTube" in catalog.prepare(cfg, "pin", {"item": "s02", "when": "2099-01-01 10:00"}).text


def test_pin_times(cfg, one):
    tz = ZoneInfo(TZ)
    soon = (datetime.now(tz) + timedelta(days=2)).strftime("%Y-%m-%d 15:00")
    p = catalog.prepare(cfg, "pin", {"item": "s02", "when": soon})
    assert p.kind == "card" and p.args["text"] == soon and "15:00?" in p.text
    assert "not approved yet" in " ".join(p.lines)
    assert "has passed" in catalog.prepare(cfg, "pin", {"item": "s02", "when": "2020-01-01 10:00"}).text
    assert "can't read" in catalog.prepare(cfg, "pin", {"item": "s02", "when": "someday"}).text
    assert "isn't pinned" in catalog.prepare(cfg, "pin", {"item": "s02", "unpin": True}).text
    one["row"] = row(publish_at="2099-01-01 10:00")
    assert catalog.prepare(cfg, "pin", {"item": "s02", "unpin": True}).args["text"] == ""


def test_hold_and_release(cfg, one):
    assert catalog.prepare(cfg, "hold", {"item": "s02"}).args["on"] is True
    assert "not held" in catalog.prepare(cfg, "hold", {"item": "s02", "release": True}).text
    one["row"] = row(hold=True)
    assert catalog.prepare(cfg, "hold", {"item": "s02", "release": True}).args["on"] is False


def test_retitle_warns_on_house_style(cfg, one):
    p = catalog.prepare(cfg, "retitle", {"item": "s02", "title": "The hosts lose it"})
    assert p.kind == "card" and any("hosts" in x for x in p.lines)


def test_ambiguous_item_asks(cfg, monkeypatch):
    rows = [row(), row(ref="kick-1/s07_blade", short="kick-1/s07", key="s07_blade")]
    monkeypatch.setattr(catalog, "find_item", lambda cfg, text: (None, rows))
    p = catalog.prepare(cfg, "pin", {"item": "blade", "when": "2099-01-01"})
    assert p.kind == "ask" and [o["ref"] for o in p.options] == ["kick-1/s02_blade", "kick-1/s07_blade"]
    monkeypatch.setattr(catalog, "find_item", lambda cfg, text: (None, []))
    assert "can't find" in catalog.prepare(cfg, "approve", {"item": "zzz"}).text


def test_match_rows_by_title_words():
    rows = [row(), row(ref="kick-1/s03_jer", title="The Quartering Runs the K-Cup Scam", key="s03_jer"),
            row(ref="kick-2/s01_x", stream="kick-2", title="Blade at the Casino", key="s01_x")]
    assert [r["ref"] for r in catalog.match_rows(rows, "the k-cup scam")] == ["kick-1/s03_jer"]
    assert len(catalog.match_rows(rows, "blade")) == 2
    assert catalog.match_rows(rows, "the a of") == []


def test_find_item_names_and_stream(cfg, monkeypatch):
    seen = []

    def resolve(cfg, ref, stream=None):
        seen.append((ref, stream))
        if ref == "s0":
            raise ClipperError("'s0' matches 2 items: ...")
        return actions.Item(cfg["paths"]["streams"] / "kick-1", "s02_blade", {})
    monkeypatch.setattr(actions, "resolve", resolve)
    (cfg["paths"]["streams"] / "kick-1" / "review_state.json").write_text("{}")
    monkeypatch.setattr(catalog, "_rows", lambda cfg, stream=None: [row(), row(ref="kick-1/s07_blade", key="s07_blade")])
    item, _ = catalog.find_item(cfg, "s02 kick-1")
    assert item.key == "s02_blade" and seen[-1] == ("s02", "kick-1")
    item, cands = catalog.find_item(cfg, "s0")
    assert item is None and len(cands) == 2
    item, cands = catalog.find_item(cfg, "blade in vegas")       # words: two segments
    assert item is None and len(cands) == 2


def test_recheck_and_apply(cfg, one, monkeypatch):
    """A tap re-reads the item; changed since the card: nothing done. Else decide + replan as Clippa chat."""
    card = catalog.prepare(cfg, "approve", {"item": "s02"}).as_dict()
    item = actions.Item(cfg["paths"]["streams"] / "kick-1", "s02_blade", {})
    monkeypatch.setattr(actions, "resolve", lambda cfg, ref: item)
    fresh = {"row": row()}
    monkeypatch.setattr(actions, "refresh", lambda cfg, it: dict(fresh["row"]))
    done = []
    monkeypatch.setattr(actions, "decide", lambda cfg, it, verb, who, text="", on=None: done.append((verb, who)) or row(decision="approved"))
    monkeypatch.setattr(actions, "replan", lambda cfg, it, verb, r: (r, "scheduled 2026-11-20 15:00"))
    from clipper.warroom import review as wr
    monkeypatch.setattr(wr, "_before", lambda cfg, it: {})
    monkeypatch.setattr(wr, "remember", lambda *a: "u1")
    fresh["row"] = row(decision="approved")     # approved from Discord meanwhile
    text, ok = catalog.apply(cfg, card)
    assert not ok and "changed since I asked" in text and "it is approved now" in text
    assert done == []
    fresh["row"] = row()
    text, ok = catalog.apply(cfg, card)
    assert ok and done == [("approve", "Clippa chat")] and "scheduled 2026-11-20 15:00" in text


def test_schedule_read(cfg):
    tz = ZoneInfo(TZ)
    tomorrow = datetime.now(tz).replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=1)
    entries = [{"key": "kick-1/s02", "kind": "segment", "title": "Blade in Vegas", "source": "auto",
                "publish_at": tomorrow.isoformat(), "status": "scheduled"},
               {"key": "kick-1/v01", "kind": "short", "title": "Later", "source": "pinned",
                "publish_at": (tomorrow + timedelta(days=3)).isoformat(), "status": "scheduled"}]
    cfg["paths"]["schedule"].write_text(json.dumps({"entries": entries, "unscheduled": [
        {"key": "kick-1/c02_t42", "reason": "stale: not out by 10-12 01:20"},
        {"key": "kick-1/c01", "reason": "rejected"}]}))
    p = catalog.prepare(cfg, "releases", {"day": "tomorrow"})
    assert p.kind == "answer" and "1 going out tomorrow" in p.text and "15:00 segment: Blade in Vegas" in p.text
    assert "4 going out" not in catalog.prepare(cfg, "releases", {"day": "week"}).text
    assert "2 going out" in catalog.prepare(cfg, "releases", {"day": "week"}).text
    assert "Which day" in catalog.prepare(cfg, "releases", {"day": "someday"}).text
    u = catalog.prepare(cfg, "unslotted", {})
    assert "1 approved items without a slot" in u.text and "stale: 1 (c02)" in u.text


def test_day_of():
    from datetime import date

    mon = date(2026, 11, 16)
    assert catalog.day_of("tomorrow", mon) == (date(2026, 11, 17), 1)
    assert catalog.day_of("fri", mon) == (date(2026, 11, 20), 1)
    assert catalog.day_of("monday", mon) == (mon, 1)
    assert catalog.day_of("week", mon) == (mon, 7)
    assert catalog.day_of("2026-12-01", mon) == (date(2026, 12, 1), 1)
    assert catalog.day_of("soon", mon) is None


def test_spend_cards_carry_an_estimate(cfg, monkeypatch):
    cfg["paths"]["costs"].write_text("\n".join(json.dumps(r) for r in [
        {"ts": "2026-11-01T00:00:00+00:00", "kind": "llm", "stage": "story new", "stream": "story-a", "usd": 0.4},
        {"ts": "2026-11-02T00:00:00+00:00", "kind": "llm", "stage": "story run", "stream": "story-b", "usd": 0.2}]))
    p = catalog.prepare(cfg, "story", {"brief": "the streamer banned from Kick after the call", "days": 3,
                                       "people": ["Jer"]})
    assert p.kind == "card" and p.risk == catalog.SPEND
    assert p.argv == ["story", "new", "--days=3", "--person=Jer", "--", "the streamer banned from Kick after the call"]
    assert "~$0.30" in p.estimate and "at most $" in p.estimate
    assert catalog.prepare(cfg, "story", {"brief": "short"}).kind == "answer"
    assert "isn't a link" in catalog.prepare(cfg, "story", {"brief": "a long enough brief", "urls": ["nope"]}).text
    # what the model may send instead: a string for a list, words for a number, a value like an option
    p = catalog.prepare(cfg, "story", {"brief": "--days=999", "people": "-x"})
    assert p.argv == ["story", "new", "--person=-x", "--", "--days=999"]
    assert "isn't a number" in catalog.prepare(cfg, "story", {"brief": "a long enough brief", "days": "a week"}).text


def test_story_argv_parses_as_meant():
    """A value that looks like an option stays a value for `clipper story new`."""
    from clipper.cli import build_parser

    a = build_parser().parse_args(["story", "new", "--days=3", "--person=-x", "--", "--url=https://a b"])
    assert (a.brief, a.days, a.person, a.url) == ("--url=https://a b", 3, ["-x"], [])


def test_spend_today_and_buddies(cfg, monkeypatch):
    from datetime import UTC

    today = datetime.now(UTC).strftime("%Y-%m-%dT01:00:00+00:00")
    cfg["paths"]["costs"].write_text("\n".join(json.dumps(r) for r in [
        {"ts": today, "kind": "llm", "stage": "chat", "usd": 0.01},
        {"ts": today, "kind": "llm", "stage": "score", "usd": 0.5},
        {"ts": "2020-01-01T00:00:00+00:00", "kind": "llm", "stage": "chat", "usd": 9}]))
    assert catalog.spend_today(cfg, "chat") == {"chat": pytest.approx(0.01)}
    assert sum(catalog.spend_today(cfg).values()) == pytest.approx(0.51)
    from clipper import services

    monkeypatch.setattr(services, "recorder", lambda cfg: {"up": True, "age_s": 5, "live": ["kinocasinogaming"],
                                                           "channels": {}, "error": ""})
    b = {x["name"]: x for x in catalog.buddies(cfg)}
    assert b["serve"]["state"] == "offline" and b["recorder"]["state"] == "away"
    assert "kinocasinogaming is live" in b["recorder"]["note"] and b["worker"]["state"] == "idle"
