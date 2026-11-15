"""Approval by silence (#259 B): review_auto's rules, its run, and where it shows."""
from datetime import UTC, datetime, timedelta

import pytest

from clipper import actions, jsonstate, review_auto

T0 = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def cfg(defaults):
    defaults["review"]["auto_approve"] = True
    return defaults


def short(score="7", **more):
    return {"kind": "short", "fields": {"Score": score}, "published_at": T0.isoformat(), **more}


CLEAN = {"sig": "abc", "findings": []}


def v(cfg, st=None, r=None, check=CLEAN, auto=None, retaken=False, now=T0 + timedelta(hours=3), kind="stream"):
    return review_auto.verdict(cfg, kind, "v01_t1", st or short(), r or {"decision": "pending", "fields": {}},
                               check, auto or {}, retaken, now)


def test_a_clean_short_over_the_floor_waits_out_the_grace_then_is_due(cfg):
    w = v(cfg, now=T0 + timedelta(minutes=30))
    assert w.state == "waiting" and w.at == T0 + timedelta(minutes=120)
    assert v(cfg, now=T0 + timedelta(minutes=120)).state == "due"


@pytest.mark.parametrize("kw, why", [
    ({"st": short("5")}, "score 5 under 6"),
    ({"st": short("")}, "no score"),
    ({"st": {**short(), "kind": "segment"}}, "segments wait for a person"),
    ({"r": {"decision": "pending", "fields": {"Hold": "true"}}}, "held"),
    ({"r": {"decision": "pending", "fields": {}, "edited": ["YouTube title"]}}, "edited in Kitsu: YouTube title"),
    ({"retaken": True}, "re-cut after a note"),
    ({"check": None}, "not checked yet"),
    ({"check": {"sig": "", "findings": [{"text": "the checks didn't run"}]}}, "its checks didn't run"),
    ({"check": {"sig": "abc", "findings": [{"text": "a face on the cover is cut off"}]}}, "1 check finding(s)"),
    ({"auto": {"out": "reopen by Brandon"}}, "reopen by Brandon"),
    ({"kind": "story"}, "storys wait for a person"),
    ({"kind": "oneoff"}, "one-offs wait for a person"),
])
def test_what_leaves_an_item_to_a_person(cfg, kw, why):
    got = v(cfg, **kw)
    assert got.state == "person" and why in got.why


def test_decided_approved_and_off(cfg):
    assert v(cfg, r={"decision": "rejected"}).state == "decided"
    got = v(cfg, auto={"approved_at": T0.isoformat()})
    assert got.state == "approved" and got.at == T0
    cfg["review"]["auto_approve"] = False
    assert v(cfg).state == "off"


def test_a_preshow_goes_at_publish_whatever_its_score(cfg):
    seg = {"kind": "segment", "fields": {}, "published_at": T0.isoformat()}
    assert v(cfg, st=seg, kind="preshow", now=T0).state == "due"
    cfg["review"]["auto_preshow"] = "grace"
    assert v(cfg, st=seg, kind="preshow", now=T0).state == "waiting"
    cfg["review"]["auto_preshow"] = "off"
    assert v(cfg, st=seg, kind="preshow").state == "person"


def test_an_older_publish_counts_from_when_it_was_first_seen(cfg):
    st = short()
    del st["published_at"]
    got = v(cfg, st=st, auto={"seen": (T0 + timedelta(hours=2)).isoformat()})
    assert got.state == "waiting" and got.at == T0 + timedelta(hours=4)


def test_touch_takes_it_out_for_good_and_marks_an_undone_approval(tmp_path):
    review_auto.touch(tmp_path, "v01", "approve", review_auto.WHO)   # its own approval
    assert review_auto.load(tmp_path) == {}
    review_auto.touch(tmp_path, "v01", "hold", "Brandon")
    review_auto.touch(tmp_path, "v01", "reopen", "Brandon")
    assert review_auto.load(tmp_path)["v01"]["out"] == "hold by Brandon"     # the first reason stays
    jsonstate.write(review_auto.path(tmp_path), {"v02": {"approved_at": T0.isoformat()}})
    review_auto.touch(tmp_path, "v02", "reject", "Brandon")
    assert review_auto.load(tmp_path)["v02"]["undone"] == "reject by Brandon"


def test_a_person_action_goes_through_touch(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "_status", lambda *a: None)
    monkeypatch.setattr(actions, "refresh", lambda cfg, item: {})
    item = actions.Item(tmp_path, "v01_t1", {})
    actions.reopen({}, item, who="Brandon")
    assert review_auto.load(tmp_path)["v01_t1"]["out"] == "reopen by Brandon"
    actions.approve({}, actions.Item(tmp_path, "v02_t2", {}), "Approved automatically", who=review_auto.WHO)
    assert "v02_t2" not in review_auto.load(tmp_path)


def test_note_reads_like_a_card_line():
    tz = "America/Los_Angeles"
    assert review_auto.note({"state": "waiting", "at": T0.isoformat()}, tz) == "approves itself Sun 04:00 unless you act"
    assert review_auto.note({"state": "person", "why": "held"}, tz) == "waits for you: held"
    assert review_auto.note({"state": "decided"}, tz) == "" and review_auto.note(None, tz) == ""


def test_stream_kind(tmp_path):
    assert review_auto.stream_kind(tmp_path) == "stream"
    jsonstate.write(tmp_path / "meta.json", {"kind": "preshow"})
    assert review_auto.stream_kind(tmp_path) == "preshow"
    jsonstate.write(tmp_path / "meta.json", {"kind": "preshow", "window": {"from": 60}})
    assert review_auto.stream_kind(tmp_path) == "oneoff"


# ---------- a run over a fake stream ----------

def fake(cfg, tmp_path):
    cfg["paths"]["streams"] = tmp_path / "streams"
    sd = tmp_path / "streams" / "kick-9"
    sd.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(hours=3)).isoformat(timespec="seconds")
    state = {"v01_t1": {**short(), "published_at": old, "task_id": "T1"},
             "v02_t2": {**short("4"), "published_at": old, "task_id": "T2"},
             "v03_t3": {"kind": "short", "fields": {"Score": "7"}, "task_id": "T3"},   # an older publish
             "v04_t4": {**short(), "published_at": old, "task_id": "T4"}}
    jsonstate.write(sd / "review_state.json", state)
    jsonstate.write(sd / "review.json", {k: {"decision": "pending", "fields": {}} for k in state}
                    | {"v04_t4": {"decision": "rejected", "fields": {}}})
    jsonstate.write(sd / "checks.json", {k: CLEAN for k in state})
    return sd


def test_run_approves_whats_due_and_replans_once(cfg, tmp_path, monkeypatch):
    from clipper import review, review_check, schedule

    sd = fake(cfg, tmp_path)
    monkeypatch.setattr(review_auto, "for_stream", lambda c, d: cfg)
    monkeypatch.setattr(review, "sync", lambda *a, **k: {})
    monkeypatch.setattr(review_check, "run", lambda *a, **k: {})
    approved, replans = [], []
    monkeypatch.setattr(actions, "approve", lambda c, item, comment, who: approved.append((item.key, comment, who)))
    monkeypatch.setattr(schedule, "run", lambda c, sync_first: replans.append(sync_first))

    said = []
    found = review_auto.run(cfg, apply=False, say=said.append)               # --dry-run
    assert approved == [] and not review_auto.path(sd).exists()
    assert [r["state"] for r in found["kick-9"]] == ["due", "person", "waiting"]

    review_auto.run(cfg, say=said.append)
    assert [a[0] for a in approved] == ["v01_t1"] and approved[0][2] == review_auto.WHO
    assert "Approved automatically: no action in 120 min, checks clean, score 7" in approved[0][1]
    assert replans == [False]
    auto = review_auto.load(sd)
    assert auto["v01_t1"]["approved_at"] and auto["v03_t3"]["seen"]           # its grace starts now
    assert auto["v04_t4"]["out"] == "rejected by a person"                    # a reopen won't auto-approve it
    review_auto.run(cfg, say=said.append)
    assert len(approved) == 1 and replans == [False]                          # nothing new


def test_history_and_the_checkup_finding(cfg, tmp_path):
    from clipper import checkup

    sd = fake(cfg, tmp_path)
    at = datetime.now(UTC) - timedelta(days=1)
    jsonstate.write(review_auto.path(sd), {k: {"approved_at": at.isoformat()} for k in ("v01_t1", "v02_t2", "v04_t4")})
    with jsonstate.update(sd / "review.json", {}) as rv:
        rv["v01_t1"]["decision"] = "approved"
    hist = {h["key"]: h["undone"] for h in review_auto.history(cfg)}
    assert hist == {"v01_t1": "", "v02_t2": "reopened", "v04_t4": "rejected"}
    found = checkup.check_auto(cfg, datetime.now(UTC))
    assert [f["key"] for f in found] == ["auto:kick-9/v02_t2", "auto:kick-9/v04_t4"]
    assert "was then rejected" in found[1]["text"]


def test_the_card_says_when_it_approves_itself():
    from clipper import notify

    row = {"kind": "short", "duration_s": 30, "score": "7", "category": "", "found_by": "", "hook": "",
           "decision": "pending", "hold": False, "publish_at": "", "scheduled": "", "uploaded": False,
           "comment": "", "title": "t", "kitsu_url": "http://k", "short": "kick-9/v01", "date": "",
           "revision": 1, "youtube_url": "", "auto": "approves itself Sun 04:00 unless you act"}
    embed, _ = notify.card(row)
    assert {"name": "Auto-approve", "value": "approves itself Sun 04:00 unless you act", "inline": False} \
        in embed["fields"]


def test_enabled_anywhere_follows_the_shows(defaults):
    assert not review_auto.enabled_anywhere(defaults)
    defaults["review"]["auto_approve"] = True
    assert review_auto.enabled_anywhere(defaults)
