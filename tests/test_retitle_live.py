"""Renaming a video that's up: youtube.retitle, edit_meta (every surface's title edit), Clippa's card, undo."""
from __future__ import annotations

import json

import pytest

from clippa import actions, catalog, jsonstate, youtube
from clippa.errors import ClipperError
from clippa.warroom import review as wr

KEY = "kick-1/s02_blade"


class Req:
    def __init__(self, out, log, what):
        self.out, self.log, self.what = out, log, what

    def execute(self):
        self.log.append(self.what)
        return self.out


class FakeYT:
    """videos().list / update, as the Data API client answers them."""

    def __init__(self, items):
        self.items, self.calls, self.bodies = items, [], []

    def videos(self):
        return self

    def list(self, part, id):
        return Req({"items": self.items}, self.calls, ("list", id))

    def update(self, part, body):
        self.bodies.append(body)
        return Req({}, self.calls, ("update", body["id"]))


@pytest.fixture
def cfg(defaults, tmp_path):
    defaults["paths"].update(streams=tmp_path / "streams", schedule=tmp_path / "schedule.json",
                             youtube_quota=tmp_path / "quota.json")
    defaults["meta"].update(show_name="Kino Casino", title_format="{SHOW} | {title}")
    (tmp_path / "streams" / "kick-1").mkdir(parents=True)
    return defaults


def sched(cfg, **over):
    e = {"key": KEY, "stream": "kick-1", "kind": "segment", "title": "Blade in Vegas", "status": "uploaded",
         "video_id": "abcdefghijk", "url": "https://youtu.be/abcdefghijk", "publish_at": "2026-11-10T15:00:00-08:00"}
    jsonstate.write(cfg["paths"]["schedule"], {"timezone": "UTC", "entries": [{**e, **over}]})


SNIPPET = {"title": "KINO CASINO | Blade in Vegas", "description": "the story\n\nsource: https://youtu.be/x",
           "tags": ["blade", "vegas"], "categoryId": "24", "defaultLanguage": "en", "thumbnails": {"x": 1},
           "channelTitle": "My Channel"}


def test_retitle_swaps_only_the_title_and_records_it(cfg):
    sched(cfg)
    yt = FakeYT([{"snippet": SNIPPET}])
    e = youtube.retitle(cfg, KEY, "Blade  Goes Bust", yt=yt)
    assert yt.calls == [("list", "abcdefghijk"), ("update", "abcdefghijk")]
    body = yt.bodies[0]["snippet"]
    assert body["title"] == "KINO CASINO | Blade Goes Bust"          # the show name, as an upload does
    assert {k: body[k] for k in ("description", "tags", "categoryId")} == \
        {k: SNIPPET[k] for k in ("description", "tags", "categoryId")}   # the rest as YouTube has it
    assert "thumbnails" not in body and "channelTitle" not in body       # read-only parts never go back
    saved = jsonstate.load(cfg["paths"]["schedule"])["entries"][0]
    assert saved["title"] == "Blade Goes Bust" and saved["youtube_title"] == body["title"]
    assert e["url"] == "https://youtu.be/abcdefghijk" and saved["retitled_at"]


def test_retitle_refuses_what_it_cant_rename(cfg):
    sched(cfg, status="scheduled")
    with pytest.raises(ClipperError, match="isn't on YouTube"):
        youtube.retitle(cfg, KEY, "x", yt=FakeYT([]))
    sched(cfg, gone_at="2026-11-12T00:00:00+00:00")
    with pytest.raises(ClipperError, match="deleted from YouTube"):
        youtube.retitle(cfg, KEY, "x", yt=FakeYT([]))
    sched(cfg)
    yt = FakeYT([])                                 # deleted in Studio since: noted, nothing written
    with pytest.raises(ClipperError, match="isn't on YouTube any more"):
        youtube.retitle(cfg, KEY, "x", yt=yt)
    assert yt.bodies == [] and jsonstate.load(cfg["paths"]["schedule"])["entries"][0]["gone_at"]


def test_retitle_only_from_spare_quota(cfg, monkeypatch):
    sched(cfg)
    monkeypatch.setattr(youtube, "spare", lambda cfg: youtube.RETITLE_UNITS - 1)
    yt = FakeYT([{"snippet": SNIPPET}])
    with pytest.raises(ClipperError, match="no spare YouTube quota"):
        youtube.retitle(cfg, KEY, "Blade Goes Bust", yt=yt)
    assert yt.calls == []
    # renamed already, Kitsu failed after: the retry finishes Kitsu's half without the quota
    sched(cfg, youtube_title="KINO CASINO | Blade Goes Bust")
    assert youtube.retitle(cfg, KEY, "Blade Goes Bust", yt=yt)["url"] == "https://youtu.be/abcdefghijk"
    assert yt.calls == []


# ---------- edit_meta: YouTube first, then Kitsu's columns, from every surface ----------

@pytest.fixture
def item(cfg):
    return actions.Item(cfg["paths"]["streams"] / "kick-1", "s02_blade", {})


@pytest.fixture
def kitsu(monkeypatch):
    """Kitsu's side of edit_meta stubbed: the columns and comment it would write."""
    order = []
    monkeypatch.setattr(actions, "_touched", lambda item, what, who: None)
    monkeypatch.setattr(actions, "_columns", lambda cfg, it, values, note: order.append(("kitsu", values, note)))
    monkeypatch.setattr(actions, "refresh", lambda cfg, it: {})
    return order


def test_a_title_not_up_yet_is_kitsu_only(cfg, item, kitsu, monkeypatch):
    sched(cfg, status="scheduled")
    monkeypatch.setattr(youtube, "retitle", lambda *a, **k: pytest.fail("not on YouTube"))
    actions.decide(cfg, item, "retitle", "me", text="New  title")
    assert kitsu == [("kitsu", {"YouTube title": "New title"}, "Retitled: New title (me)")]


def test_a_title_thats_up_is_renamed_on_youtube_first(cfg, item, kitsu, monkeypatch):
    """The CLI's retitle, the war room's and the bot's edits, find and replace, undo: all edit_meta."""
    sched(cfg)
    monkeypatch.setattr(youtube, "retitle", lambda cfg, key, text: kitsu.append(("youtube", key, text))
                        or {"url": "https://youtu.be/abcdefghijk"})
    actions.decide(cfg, item, "edit", "War room", values={"YouTube title": "New title", "Tags": "a, b"})
    assert kitsu == [("youtube", KEY, "New title"),
                     ("kitsu", {"YouTube title": "New title", "Tags": "a, b"},
                      "Retitled: New title; Tags: a, b; renamed on YouTube (War room)")]
    kitsu.clear()
    actions.decide(cfg, item, "edit", "War room", values={"Tags": "c"})   # no title: Kitsu only
    assert kitsu == [("kitsu", {"Tags": "c"}, "Tags: c (War room)")]

    def no_rename(*a, **k):
        raise ClipperError("no spare YouTube quota today")
    kitsu.clear()
    monkeypatch.setattr(youtube, "retitle", no_rename)        # a failed rename changes nothing
    with pytest.raises(ClipperError, match="quota"):
        actions.decide(cfg, item, "retitle", "me", text="New title")
    assert kitsu == []

    def kitsu_down(*a, **k):
        raise OSError("Kitsu down")
    monkeypatch.setattr(youtube, "retitle", lambda *a: {"url": "https://youtu.be/abcdefghijk"})
    monkeypatch.setattr(actions, "_columns", kitsu_down)     # the public part happened: said so
    with pytest.raises(ClipperError, match=r"renamed on YouTube \(https://youtu.be/abcdefghijk\), but Kitsu"):
        actions.decide(cfg, item, "retitle", "me", text="New title")


def test_a_video_deleted_since_is_kitsu_only(cfg, item, kitsu, monkeypatch):
    """An undo (or a CLI retitle) of a video deleted in Studio still puts Kitsu's column back."""
    sched(cfg, gone_at="2026-11-12T00:00:00+00:00")
    monkeypatch.setattr(youtube, "retitle", lambda *a, **k: pytest.fail("deleted"))
    actions.edit_meta(cfg, item, {"YouTube title": "Old title"}, "War room (undo)", rename_live=True)
    assert kitsu == [("kitsu", {"YouTube title": "Old title"}, "Retitled: Old title (War room (undo))")]


def test_a_retakes_planned_title_never_renames_a_video_thats_up(cfg, item, kitsu, monkeypatch):
    sched(cfg)
    monkeypatch.setattr(youtube, "retitle", lambda *a, **k: pytest.fail("nobody saw this title"))
    actions.edit_meta(cfg, item, {"YouTube title": "Model's idea"}, "retake")   # retake.py's call
    assert kitsu == [("kitsu", {"YouTube title": "Model's idea"}, "Retitled: Model's idea (retake)")]


def test_the_same_title_on_youtube_skips_the_write(cfg):
    sched(cfg)
    yt = FakeYT([{"snippet": SNIPPET}])
    youtube.retitle(cfg, KEY, "Blade in Vegas", yt=yt)            # what YouTube has already
    assert yt.calls == [("list", "abcdefghijk")] and yt.bodies == []


# ---------- Clippa's card, its tap and its undo ----------

def row(**over) -> dict:
    r = {"ref": KEY, "short": "kick-1/s02", "key": "s02_blade", "stream": "kick-1", "kind": "segment",
         "title": "Blade in Vegas", "decision": "approved", "hold": False, "publish_at": "", "thumb_text": "",
         "uploaded": True, "scheduled": "", "status": "done"}
    return {**r, **over}


@pytest.fixture
def card_row(cfg, item, monkeypatch):
    state = {"row": row()}
    monkeypatch.setattr(catalog, "find_item", lambda cfg, text: (item, []))
    monkeypatch.setattr(catalog, "_row", lambda cfg, it: dict(state["row"]))
    return state


def test_the_card_says_it_renames_the_video_on_youtube(cfg, card_row):
    sched(cfg)
    p = catalog.prepare(cfg, "retitle", {"item": "s02", "title": "Blade Goes Bust"})
    assert p.kind == "card" and p.text == "Rename kick-1/s02 on YouTube?" and p.args["youtube"] is True
    assert "YouTube shows: KINO CASINO | Blade Goes Bust" in p.lines
    assert any("https://youtu.be/abcdefghijk" in x and "for everyone" in x for x in p.lines)
    # Kitsu's title again: a card all the same, since Studio may have renamed it
    p = catalog.prepare(cfg, "retitle", {"item": "s02", "title": "Blade in Vegas"})
    assert p.kind == "card" and any("unless Studio changed it" in x for x in p.lines)
    sched(cfg, gone_at="2026-11-12T00:00:00+00:00")
    assert "deleted from YouTube" in catalog.prepare(cfg, "retitle", {"item": "s02", "title": "x"}).text
    card_row["row"] = row(uploaded=False)
    sched(cfg, status="scheduled")
    p = catalog.prepare(cfg, "retitle", {"item": "s02", "title": "Blade Goes Bust"})
    assert p.text == "Retitle kick-1/s02?" and p.args["youtube"] is False
    assert "called that already" in catalog.prepare(cfg, "retitle", {"item": "s02", "title": "Blade in Vegas"}).text


def test_no_spare_quota_is_an_answer_not_a_card(cfg, card_row, monkeypatch):
    sched(cfg)
    monkeypatch.setattr(youtube, "spare", lambda cfg: 0)
    p = catalog.prepare(cfg, "retitle", {"item": "s02", "title": "Blade Goes Bust"})
    assert p.kind == "answer" and "quota" in p.text


def test_the_tap_renames_and_its_undo_renames_back(cfg, card_row, item, monkeypatch):
    sched(cfg)
    card = catalog.prepare(cfg, "retitle", {"item": "s02", "title": "Blade Goes Bust"}).as_dict()
    monkeypatch.setattr(actions, "resolve", lambda cfg, ref: item)
    monkeypatch.setattr(actions, "refresh", lambda cfg, it: row())
    monkeypatch.setattr(actions, "decide", lambda *a, **k: row(title="Blade Goes Bust"))
    monkeypatch.setattr(actions, "replan", lambda cfg, it, verb, r: (r, ""))
    monkeypatch.setattr(wr, "_before", lambda cfg, it: {"title": "Blade in Vegas", "uploaded": True})
    kept = []
    monkeypatch.setattr(wr, "remember", lambda cfg, action, label, done: kept.append((action, done)) or "u1")
    text, ok = catalog.apply(cfg, card)
    assert ok and text.startswith("Renamed on YouTube:")
    action, done = kept[0]
    assert action == "edit" and done[0]["values"] == {"YouTube title": "Blade Goes Bust"}
    back = []
    monkeypatch.setattr(actions, "edit_meta", lambda cfg, it, values, who, rename_live=False:
                        back.append((values, rename_live)) or {})
    wr._inverse(cfg, item, "edit", done[0]["before"], done[0]["values"], "War room (undo)")
    assert back == [({"YouTube title": "Blade in Vegas"}, True)]      # edit_meta renames it back on YouTube
    back.clear()                                                # an edit from before the upload: Kitsu only
    wr._inverse(cfg, item, "edit", {**done[0]["before"], "uploaded": False}, done[0]["values"], "War room (undo)")
    assert back == [({"YouTube title": "Blade in Vegas"}, False)]
    entry = {"action": "edit", "items": done}
    assert wr._stuck(entry) == 0                                # a title-only edit: nothing left behind
    assert wr._stuck({**entry, "items": [{**done[0], "values": {"YouTube title": "x", "Tags": "y"}}]}) == 1
    assert wr._stuck({"action": "approve", "items": done}) == 1


def test_a_tap_after_the_video_was_deleted_does_nothing(cfg, card_row, item, monkeypatch):
    sched(cfg)
    card = catalog.prepare(cfg, "retitle", {"item": "s02", "title": "Blade Goes Bust"}).as_dict()
    sched(cfg, gone_at="2026-11-12T00:00:00+00:00")             # serve's backfill marked it since
    monkeypatch.setattr(actions, "resolve", lambda cfg, ref: item)
    monkeypatch.setattr(actions, "refresh", lambda cfg, it: row())
    monkeypatch.setattr(actions, "decide", lambda *a, **k: pytest.fail("acted on a deleted video"))
    text, ok = catalog.apply(cfg, card)
    assert not ok and "deleted from YouTube since I asked" in text


def test_the_undo_note_names_only_titles_renamed_back_on_youtube():
    up, down = {"uploaded": True}, {"uploaded": False}
    both = {"action": "replace", "items": [{"before": up, "values": {"YouTube description": "d"}},
                                           {"before": down, "values": {"YouTube title": "t"}}]}
    assert wr._undo_note(both) == "1 already on YouTube: change it in Studio too"
    mixed = {"action": "replace", "items": [{"before": up, "values": {"YouTube description": "d"}},
                                            {"before": up, "values": {"YouTube title": "t"}}]}
    assert wr._undo_note(mixed).endswith("(titles were renamed back there)")
    assert wr._undo_note({"action": "edit", "items": [{"before": up, "values": {"YouTube title": "t"}}]}) == ""


def test_the_tool_description_says_so():
    assert "renamed on YouTube" in catalog.catalog()["retitle"].description
    assert json.dumps(catalog.catalog()["retitle"].spec())
