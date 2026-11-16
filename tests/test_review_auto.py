"""Approval by silence (#259 B): review_auto's rules, its run, and where it shows."""
from datetime import UTC, datetime, timedelta

import pytest

from clipper import actions, jsonstate, review_auto
from clipper.errors import ClipperError

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


def test_an_older_publish_not_seen_yet_has_no_deadline(cfg):
    st = short()
    del st["published_at"]
    got = v(cfg, st=st)
    assert got.state == "waiting" and got.at is None
    assert review_auto.note(got, "UTC") == "approves itself unless you act (its grace starts at the next pass)"


def test_a_floor_written_as_text_still_counts(cfg):
    cfg["review"]["auto_min_short"] = "6"
    assert review_auto.floor_of(cfg["review"], "short") == 6.0
    assert v(cfg, st=short("5")).why == "score 5 under 6"
    cfg["review"]["auto_min_short"] = "six"
    assert review_auto.floor_of(cfg["review"], "short") == 0.0


def test_a_new_revision_takes_an_automatic_approval_back(tmp_path):
    jsonstate.write(review_auto.path(tmp_path), {"v01": {"approved_at": T0.isoformat()}, "v02": {"out": "hold"}})
    jsonstate.write(tmp_path / "review.json", {"v01": {"decision": "approved", "status": "done"},
                                               "v02": {"decision": "approved", "status": "done"}})
    review_auto.republished(tmp_path, "v01")
    review_auto.republished(tmp_path, "v02")
    assert review_auto.load(tmp_path) == {"v01": {"was_approved_at": T0.isoformat()}, "v02": {"out": "hold"}}
    rv = jsonstate.read(tmp_path / "review.json")
    assert rv["v01"] == {"decision": "pending", "status": "wfa"}       # not read as a person's approval
    assert rv["v02"]["decision"] == "approved"                         # a person's stands


def test_an_action_refused_for_bad_input_doesnt_count(tmp_path):
    item = actions.Item(tmp_path, "v01_t1", {})
    with pytest.raises(ClipperError):
        actions.reject({}, item, "  ", who="Brandon")
    assert review_auto.load(tmp_path) == {}


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
    monkeypatch.setattr(actions, "approve", lambda c, item, comment, who, **k: approved.append((item.key, comment, who)))
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


def test_a_person_acting_mid_run_wins(cfg, tmp_path, monkeypatch):
    from clipper import review, review_check

    sd = fake(cfg, tmp_path)
    monkeypatch.setattr(review_auto, "for_stream", lambda c, d: cfg)

    def sync_then_a_reject(*a, **k):   # the user rejects v01 just after the sync read Kitsu
        review_auto.touch(sd, "v01_t1", "reject", "Brandon")
    monkeypatch.setattr(review, "sync", sync_then_a_reject)
    monkeypatch.setattr(review_check, "run", lambda *a, **k: {})
    approved = []
    monkeypatch.setattr(actions, "approve", lambda c, item, comment, who, **k: approved.append(item.key))
    assert review_auto.approve_due(cfg, sd, ["v01_t1"], say=lambda *_: None) == [] and approved == []


def test_history_and_the_checkup_finding(cfg, tmp_path):
    from clipper import checkup

    sd = fake(cfg, tmp_path)
    at = datetime.now(UTC) - timedelta(days=1)
    jsonstate.write(review_auto.path(sd), {k: {"approved_at": at.isoformat()} for k in ("v01_t1", "v02_t2", "v04_t4")})
    with jsonstate.update(sd / "review.json", {}) as rv:
        rv["v01_t1"]["decision"] = "approved"
    hist = {h["key"]: h["undone"] for h in review_auto.history(cfg)}
    assert hist == {"v01_t1": "", "v02_t2": "reopened", "v04_t4": "rejected"}
    with jsonstate.update(sd / "review.json", {}) as rv:
        rv["v01_t1"]["fields"] = {"Hold": "true"}                           # held in Kitsu
    with jsonstate.update(review_auto.path(sd), {}) as auto:
        auto["v03_t3"] = {"was_approved_at": at.isoformat()}                # re-rendered since
    hist = {h["key"]: h["undone"] for h in review_auto.history(cfg)}
    assert hist["v01_t1"] == "held" and hist["v03_t3"] == ""
    found = checkup.check_auto(cfg, datetime.now(UTC))
    assert [f["key"] for f in found] == ["auto:kick-9/v01_t1", "auto:kick-9/v02_t2", "auto:kick-9/v04_t4"]
    assert "was then rejected" in found[2]["text"]


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


def stub(cfg, tmp_path, monkeypatch, approve=None):
    from clipper import review, review_check, schedule

    sd = fake(cfg, tmp_path)
    monkeypatch.setattr(review_auto, "for_stream", lambda c, d: cfg)
    monkeypatch.setattr(review, "sync", lambda *a, **k: {})
    monkeypatch.setattr(review, "set_checks", lambda *a, **k: [])
    monkeypatch.setattr(review_check, "run", lambda *a, **k: {})
    approved, replans = [], []
    monkeypatch.setattr(actions, "approve", approve or (lambda c, item, comment, who, **k: approved.append(item.key)))
    monkeypatch.setattr(actions, "refresh", lambda c, item: {})
    monkeypatch.setattr(schedule, "run", lambda c, sync_first: replans.append(sync_first))
    return sd, approved, replans


def test_a_broken_auto_json_skips_the_stream(cfg, tmp_path, monkeypatch):
    sd, approved, replans = stub(cfg, tmp_path, monkeypatch)
    review_auto.path(sd).write_text("{not json", encoding="utf-8")
    said = []
    review_auto.run(cfg, say=said.append)
    assert approved == [] and replans == [] and any("kick-9: skipped" in s for s in said)
    assert review_auto.run(cfg, apply=False, say=said.append)["kick-9"]         # a dry run still lists


def test_findings_are_checked_again_so_stale_ones_dont_hold_an_item_for_good(cfg, tmp_path, monkeypatch):
    # kick-137715636: findings from the first version of the checks kept every item out of the window
    from clipper import review_check

    sd, approved, _ = stub(cfg, tmp_path, monkeypatch)
    jsonstate.write(sd / "checks.json", {"v01_t1": {"sig": "old", "findings": [{"text": "the title names Kino"}]}})
    asked = []

    def rerun(sdir, c, keys=None, **k):
        asked.append(keys)
        with jsonstate.update(review_check.path(sdir), {}) as st:
            st.update({key: CLEAN for key in keys})
        return {key: [] for key in keys}
    monkeypatch.setattr(review_check, "run", rerun)
    with jsonstate.update(sd / "review.json", {}) as rv:
        rv["v02_t2"]["fields"] = {"Hold": "true"}                                # waits for a person anyway
    with jsonstate.update(sd / "checks.json", {}) as st:
        st["v02_t2"] = {"sig": "old", "findings": [{"text": "x"}]}
    found = review_auto.run(cfg, apply=False, say=lambda *_: None)                # a dry run doesn't check
    assert asked == [] and found["kick-9"][0]["why"] == "1 check finding(s)"
    review_auto.run(cfg, say=lambda *_: None)
    assert asked[0] == ["v01_t1"] and approved == ["v01_t1"]  # not v03, never checked: a person's
    assert asked[1:] == [["v01_t1"]]                                            # approve_due's own run
    assert review_auto.verdicts(cfg, sd)["v03_t3"].why == "not checked yet"


def test_a_recheck_is_hourly_per_stream_and_off_with_the_checks(cfg, tmp_path, monkeypatch):
    from clipper import review_check

    sd, approved, _ = stub(cfg, tmp_path, monkeypatch)
    jsonstate.write(sd / "checks.json", {"v01_t1": {"sig": "s", "findings": [{"text": "a real one"}]}})
    asked = []
    monkeypatch.setattr(review_check, "run", lambda sdir, c, keys=None, **k:
                        asked.append(keys) or {key: [{"text": "a real one"}] for key in keys})
    t = datetime.now(UTC)
    for minutes in (0, 10, 50, 61):
        review_auto.run(cfg, say=lambda *_: None, now=t + timedelta(minutes=minutes))
    assert asked == [["v01_t1"], ["v01_t1"]] and approved == []
    # on disk, not in memory: serve runs every pass as a new `clipper review auto` process
    assert jsonstate.read(sd / review_auto.RECHECK, {})["at"] == review_auto.now_iso(t + timedelta(minutes=61))
    (sd / review_auto.RECHECK).unlink()
    cfg["review"]["checks"] = False
    review_auto.run(cfg, say=lambda *_: None)
    assert len(asked) == 2


def test_a_check_that_fails_again_doesnt_stop_the_due_items(cfg, tmp_path, monkeypatch):
    from clipper import review_check

    sd, approved, _ = stub(cfg, tmp_path, monkeypatch)
    with jsonstate.update(sd / "checks.json", {}) as st:
        st["v03_t3"] = {"sig": "old", "findings": [{"text": "x"}]}

    def boom(sdir, c, keys=None, **k):
        if keys == ["v03_t3"]:
            raise RuntimeError("people.json unreadable")
        return {}
    monkeypatch.setattr(review_check, "run", boom)
    review_auto.run(cfg, say=lambda *_: None)
    assert approved == ["v01_t1"]


def test_a_hold_seen_in_kitsu_keeps_it_out_after_its_release(cfg, tmp_path, monkeypatch):
    sd, approved, _ = stub(cfg, tmp_path, monkeypatch)
    with jsonstate.update(sd / "review.json", {}) as rv:
        rv["v01_t1"]["fields"] = {"Hold": "true"}
    review_auto.run(cfg, say=lambda *_: None)
    assert review_auto.load(sd)["v01_t1"]["out"] == "held"
    with jsonstate.update(sd / "review.json", {}) as rv:
        rv["v01_t1"]["fields"] = {}                                             # released
    review_auto.run(cfg, say=lambda *_: None)
    assert approved == []


def test_one_failed_approval_doesnt_lose_the_others(cfg, tmp_path, monkeypatch):
    approved = []

    def approve(c, item, comment, who, **k):
        if item.key == "v01_t1":
            raise RuntimeError("Kitsu said no")
        approved.append(item.key)
    sd, _, replans = stub(cfg, tmp_path, monkeypatch, approve)
    with jsonstate.update(sd / "review_state.json", {}) as st:
        st["v02_t2"]["fields"]["Score"] = "8"
    said = []
    review_auto.run(cfg, say=said.append)
    assert approved == ["v02_t2"] and replans == [False]
    assert "approved_at" not in review_auto.load(sd).get("v01_t1", {})
    assert any("v01_t1: not approved: RuntimeError" in s for s in said)


def test_a_preshow_is_approved_at_publish(cfg, tmp_path, monkeypatch):
    from clipper import notify, review

    sd = tmp_path / "pre-1"
    sd.mkdir()
    jsonstate.write(sd / "meta.json", {"kind": "preshow"})
    monkeypatch.setattr(notify, "for_stream", lambda c, d: cfg)
    monkeypatch.setattr(review, "publish", lambda *a, **k: ["v01_t1"])
    runs = []
    monkeypatch.setattr(review_auto, "run", lambda c, stream: runs.append(stream))
    cfg["discord"]["enabled"] = False
    notify.publish_and_post(sd, cfg)
    assert runs == ["pre-1"]
    cfg["review"]["auto_preshow"] = "grace"
    notify.publish_and_post(sd, cfg)
    assert runs == ["pre-1"]


def test_the_approval_runs_under_auto_jsons_lock(cfg, tmp_path, monkeypatch):
    held = []

    def approve(c, item, comment, who, **k):   # the file lock other processes (the bot's touch) wait on
        held.append(review_auto.path(item.stream_dir).with_name("auto.json.lock").exists())
    stub(cfg, tmp_path, monkeypatch, approve)
    review_auto.run(cfg, say=lambda *_: None)
    assert held == [True]


def test_clippers_own_hold_doesnt_last(cfg):
    st = {**short(), "fields": {"Score": "7", "Hold": "true"}}                  # proposed held: other voices
    got = v(cfg, st=st, r={"decision": "pending", "fields": {"Hold": "true"}})
    assert got.state == "person" and got.why == "held for other voices" and not got.lasting
    assert v(cfg, r={"decision": "pending", "fields": {"Hold": "true"}}).lasting   # a person's hold


def test_the_item_is_re_read_after_the_lock_is_let_go(cfg, tmp_path, monkeypatch):
    sd, approved, _ = stub(cfg, tmp_path, monkeypatch)
    lock = review_auto.path(sd).with_name("auto.json.lock")
    reread = []
    monkeypatch.setattr(actions, "refresh", lambda c, item: reread.append(lock.exists()) or {})
    review_auto.run(cfg, say=lambda *_: None)
    assert approved == ["v01_t1"] and reread == [False]
    assert "claimed_at" not in review_auto.load(sd)["v01_t1"]


def test_an_approval_whose_answer_was_lost_counts_as_automatic_once_kitsu_shows_it(cfg, tmp_path, monkeypatch):
    def lost(c, item, comment, who, **k):
        raise TimeoutError("Kitsu took over 25 s")
    sd, _, replans = stub(cfg, tmp_path, monkeypatch, lost)
    said = []
    review_auto.run(cfg, say=said.append)
    e = review_auto.load(sd)["v01_t1"]
    assert e["claimed_at"] and "approved_at" not in e and replans == []
    assert any("v01_t1: not approved: TimeoutError" in s for s in said)
    with jsonstate.update(sd / "review.json", {}) as rv:                       # Kitsu took it after all
        rv["v01_t1"]["decision"] = "approved"
    assert review_auto.verdicts(cfg, sd)["v01_t1"].state == "approved"
    review_auto.run(cfg, say=lambda *_: None)
    e = review_auto.load(sd)["v01_t1"]
    assert e["approved_at"] and "claimed_at" not in e and "out" not in e      # not "approved by a person"
    assert {h["key"] for h in review_auto.history(cfg)} == {"v01_t1"}


def test_a_lost_answer_then_a_persons_decision_is_theirs(cfg, tmp_path, monkeypatch):
    sd, _, _ = stub(cfg, tmp_path, monkeypatch)
    jsonstate.write(review_auto.path(sd), {"v01_t1": {"claimed_at": T0.isoformat()}})
    with jsonstate.update(sd / "review.json", {}) as rv:
        rv["v01_t1"]["decision"] = "rejected"
    review_auto.run(cfg, say=lambda *_: None)
    assert review_auto.load(sd)["v01_t1"]["out"] == "rejected by a person"
    jsonstate.write(review_auto.path(sd), {"v02_t2": {"claimed_at": T0.isoformat(), "out": "approve by Brandon"}})
    with jsonstate.update(sd / "review.json", {}) as rv:
        rv["v02_t2"]["decision"] = "approved"
    assert review_auto.verdicts(cfg, sd)["v02_t2"].state == "decided"


def test_a_show_without_auto_still_has_its_findings_checked_again(cfg, tmp_path, monkeypatch):
    from clipper import review_check

    sd, approved, _ = stub(cfg, tmp_path, monkeypatch)
    cfg["review"]["auto_approve"] = False
    jsonstate.write(sd / "checks.json", {"v01_t1": {"sig": "old", "findings": [{"text": "stale"}]},
                                         "v02_t2": CLEAN, "v04_t4": {"sig": "", "findings": [{"text": "x"}]}})
    asked = []
    monkeypatch.setattr(review_check, "run", lambda sdir, c, keys=None, kitsu=False, **k:
                        asked.append((keys, kitsu)) or {key: [] for key in keys})
    assert review_auto.run(cfg, say=lambda *_: None) == {}
    assert asked == [(["v01_t1"], True)] and approved == []                    # v04 is rejected; the column follows
    assert review_auto.flagged(sd) == ["v01_t1"]                               # the fake run wrote nothing


def test_a_check_run_brings_the_kitsu_column_in_line(defaults, tmp_path, monkeypatch):
    from clipper import review, review_check

    sd = tmp_path / "kick-9"
    sd.mkdir()
    it = {"key": "v01_t1", "kind": "short"}
    monkeypatch.setattr(review_check, "select", lambda *a: [it])
    monkeypatch.setattr(review_check, "context", lambda *a: {})
    monkeypatch.setattr(review_check, "signature", lambda *a: "s")
    monkeypatch.setattr(review_check, "check_item", lambda *a: ([{"text": "face cut off"}], None))
    columns = []
    monkeypatch.setattr(review, "set_checks", lambda sdir, c, summaries: columns.append(summaries) or [])
    said = []
    review_check.run(sd, defaults, say=said.append)
    assert columns == []                                                       # a publish writes its own
    review_check.run(sd, defaults, say=said.append, kitsu=True, force=True)
    assert columns == [{"v01_t1": "face cut off"}]
    monkeypatch.setattr(review, "set_checks", lambda *a: (_ for _ in ()).throw(OSError("Kitsu down")))
    review_check.run(sd, defaults, say=said.append, kitsu=True)
    assert said[-1] == "  Kitsu Checks column not updated: OSError: Kitsu down"
    passed = []
    review_auto._column_trouble(sd, passed.append)(said[-1])
    review_auto._column_trouble(sd, passed.append)("  v01_t1: face cut off")
    assert passed == ["  kick-9: Kitsu Checks column not updated: OSError: Kitsu down"]


def test_a_failed_re_read_still_lets_the_replan_place_the_item(cfg, tmp_path, monkeypatch):
    sd, approved, _ = stub(cfg, tmp_path, monkeypatch)
    monkeypatch.setattr(actions, "refresh", lambda c, item: (_ for _ in ()).throw(TimeoutError("slow")))
    review_auto.run(cfg, say=lambda *_: None)
    assert approved == ["v01_t1"] and jsonstate.read(sd / "review.json")["v01_t1"]["decision"] == "approved"


def test_an_item_gone_from_review_isnt_asked_for_every_hour(cfg, tmp_path, monkeypatch):
    from clipper import review_check

    sd, _, _ = stub(cfg, tmp_path, monkeypatch)
    jsonstate.write(sd / "checks.json", {"v01_t1": {"sig": "s", "at": "a1", "findings": [{"text": "x"}]},
                                         "v02_t2": {"sig": "s", "at": "a1", "findings": [{"text": "y"}]}})
    asked = []
    monkeypatch.setattr(review_check, "run", lambda sdir, c, keys=None, **k:   # v02 isn't a review item now
                        asked.append(keys) or {key: [{"text": "x"}] for key in keys if key != "v02_t2"})
    with jsonstate.update(sd / "review_state.json", {}) as st:
        st["v02_t2"]["fields"]["Score"] = "8"
    t = datetime.now(UTC)
    review_auto.run(cfg, say=lambda *_: None, now=t)
    review_auto.run(cfg, say=lambda *_: None, now=t + timedelta(minutes=61))
    assert asked == [["v01_t1", "v02_t2"], ["v01_t1"]]
    with jsonstate.update(sd / "checks.json", {}) as ch:                       # republished, checked anew
        ch["v02_t2"]["at"] = "a2"
    review_auto.run(cfg, say=lambda *_: None, now=t + timedelta(minutes=122))
    assert asked[-1] == ["v01_t1", "v02_t2"]
