"""actions.decide / replan: the one path the bot's buttons and the war room take."""
from types import SimpleNamespace

import pytest

from clippa import actions, jsonstate, schedule
from clippa.errors import ClipperError

ITEM = SimpleNamespace(stream_dir="s", key="k")


@pytest.fixture
def calls(monkeypatch):
    seen = []
    for name in ("approve", "reject", "changes", "reopen", "pin", "thumbtext"):
        monkeypatch.setattr(actions, name, lambda cfg, item, *a, _n=name: seen.append((_n, a)) or {})
    monkeypatch.setattr(actions, "hold", lambda cfg, item, on, who: seen.append(("hold", (on, who))) or {})
    monkeypatch.setattr(actions, "edit_meta", lambda cfg, item, v, who, rename_live=False:
                        seen.append(("edit", (v, who))) or {} if rename_live else pytest.fail("a person's edit"))
    monkeypatch.setattr(actions, "refresh", lambda cfg, item: {"hold": True})
    return seen


def test_decide_dispatch(calls):
    actions.decide({}, ITEM, "approve", "me", text="nice")
    actions.decide({}, ITEM, "reopen", "me")
    actions.decide({}, ITEM, "edit", "me", values={"Tags": "a"})
    actions.decide({}, ITEM, "hold", "me")               # flips: it was held
    actions.decide({}, ITEM, "hold", "me", on=True)
    actions.decide({}, ITEM, "retitle", "me", text="New title")   # the CLI's column verbs
    actions.decide({}, ITEM, "playlist", "me", text="Kino Casino")
    assert calls == [("approve", ("nice", "me")), ("reopen", ("me",)),
                     ("edit", ({"Tags": "a"}, "me")), ("hold", (False, "me")), ("hold", (True, "me")),
                     ("edit", ({"YouTube title": "New title"}, "me")),
                     ("edit", ({"Playlist": "Kino Casino"}, "me"))]
    with pytest.raises(ClipperError, match="unknown action"):
        actions.decide({}, ITEM, "publish", "me")


def test_resplit_refuses_a_segment_under_the_limit(monkeypatch):
    monkeypatch.setattr(actions, "for_stream", lambda cfg, d: cfg)
    monkeypatch.setattr(actions, "describe", lambda cfg, sdir: {"k": {"resplit": False, "short": "kick-1/s01"}})
    with pytest.raises(ClipperError, match="split_over_s"):
        actions.resplit({}, ITEM, "me")


def test_ask_resplit_records_the_ask_and_does_not_say_we(defaults, tmp_path):
    sdir = tmp_path / "kick-1"
    sdir.mkdir()
    defaults["paths"]["streams"] = tmp_path
    defaults["segments"]["split_over_s"] = 3000
    msg = actions.ask_resplit(defaults, "kick-1/s06", "Brandon")
    saved = jsonstate.load(sdir / "resplit_asks.json")
    assert saved["s06"]["who"] == "Brandon"
    assert "Cut 06" in msg and "50 minutes" in msg and "not in review" in msg
    assert "we" not in msg.lower() and "segments" in msg


def test_replan_only_for_schedule_moves(monkeypatch):
    ran = []
    monkeypatch.setattr(schedule, "run", lambda cfg, sync_first: ran.append(sync_first))
    rows = iter([{"scheduled": "2026-11-03T16:00:00-08:00", "decision": "approved", "hold": False},
                 {"scheduled": "", "decision": "approved", "hold": True},
                 {"scheduled": "", "decision": "approved", "hold": False, "dropped": "stale"}])
    monkeypatch.setattr(actions, "describe", lambda cfg, sdir: {"k": next(rows)})
    cfg = {"discord": {"replan": True}}
    assert actions.replan(cfg, ITEM, "thumbtext", {"x": 1}) == ({"x": 1}, "")   # no replan
    assert actions.replan(cfg, ITEM, "approve", {})[1] == "scheduled 2026-11-03 16:00"
    assert actions.replan(cfg, ITEM, "hold", {})[1] == "held, not scheduled"
    assert actions.replan(cfg, ITEM, "approve", {})[1] == "not going out: stale"
    assert ran == [False, False, False]
    assert actions.replan({"discord": {"replan": False}}, ITEM, "approve", {}) == ({}, "")
