"""clipper/serve.py: the jobs table, its heartbeat, and the bot's client to it."""
import threading
from http.server import ThreadingHTTPServer

import pytest

from clipper import actions, schedule, serve, warroom
from clipper.errors import ClipperError


def cfg_in(defaults, tmp_path, **sections) -> dict:
    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path}}
    for name, values in sections.items():
        cfg[name] = {**cfg[name], **values}
    return cfg


def test_due_skips_jobs_that_are_off_or_ran_recently():
    jobs = [serve.Job("upload", 3600, None, None), serve.Job("tiktok", 0, None, None),
            serve.Job("news", 600, None, None)]
    assert serve.due(jobs, {}, now=1000.0) == ["upload", "news"]
    assert serve.due(jobs, {"upload": 1000.0, "news": 500.0}, now=4000.0) == ["news"]
    assert serve.due(jobs, {"upload": 1000.0, "news": 3500.0}, now=4000.0) == []


def test_a_retry_job_that_failed_comes_back_sooner_then_settles():
    daily = serve.Job("analytics", 86400, None, None, retry=True)
    plain = serve.Job("cast", 86400, None, None)
    r = serve.RETRY_FAILED_S
    assert [serve.interval(daily, n) for n in range(8)] == [86400, r, 2 * r, 4 * r, 8 * r, 16 * r, 32 * r, 86400]
    assert serve.interval(daily, 10_000) == 86400           # no overflow on a long streak
    assert serve.interval(plain, 3) == 86400                # not marked retry: its own pace
    assert serve.interval(serve.Job("x", 0, None, None, retry=True), 2) == 0  # off stays off
    last = {"analytics": 0.0, "cast": 0.0}
    assert serve.due([daily, plain], last, now=r, fails={"analytics": 1, "cast": 1}) == ["analytics"]
    assert serve.due([daily, plain], last, now=r, fails={"analytics": 2}) == []


def test_run_counts_failures_in_a_row(defaults, tmp_path, monkeypatch):
    import asyncio
    import sys

    cfg = cfg_in(defaults, tmp_path)
    s = serve.Serve(cfg)
    monkeypatch.setattr(serve.actions, "clipper_cmd", lambda cfg: [sys.executable, "-c"])
    monkeypatch.setattr(serve.alerts, "send", lambda *a, **kw: None)
    fail, ok = ["import sys; print('boom'); sys.exit(2)"], ["print('fine')"]
    for args, want in [(fail, 1), (fail, 2), (ok, 0), (fail, 1)]:
        asyncio.run(s.run_clipper("analytics", args))
        assert s.fails()["analytics"] == want
    monkeypatch.setattr(serve.actions, "clipper_cmd", lambda cfg: ["no-such-program-anywhere"])
    asyncio.run(s.run_clipper("analytics", ok))  # couldn't start at all: a failure too
    assert s.fails()["analytics"] == 2 and s.state["analytics"]["last_exit"] is None


def test_the_jobs_table_follows_the_config(defaults, tmp_path):
    cfg = cfg_in(defaults, tmp_path, youtube={"auto_upload_minutes": 60}, retake={"auto": True},
                 tiktok={"auto_send_minutes": 0})
    every = {name: every for name, every, *_ in serve.Serve(cfg).jobs()}
    assert set(every) == {"upload", "preshow", "retake", "auto", "tiktok", "x", "analytics", "cast", "news", "blinds",
                          "backup", "prune"}
    assert every["backup"] == 3600 and every["prune"] == 0      # prune: only with keep_source_days
    assert every["auto"] == 0                                   # no show approves by silence
    assert every["upload"] == 3600 and every["retake"] == serve.RETAKE_EVERY_S and every["tiktok"] == 0
    assert every["preshow"] == defaults["preshow"]["poll_minutes"] * 60
    # only free, repeatable daily jobs retry sooner; cast auto spends on each run
    assert {j.name for j in serve.Serve(cfg).jobs() if j.retry} == {"analytics", "prune"}


def test_status_says_when_each_job_runs_next(defaults, tmp_path, monkeypatch):
    cfg = cfg_in(defaults, tmp_path, youtube={"auto_upload_minutes": 60}, tiktok={"auto_send_minutes": 0})
    s = serve.Serve(cfg)
    monkeypatch.setattr(serve.time, "monotonic", lambda: 10_000.0)
    s.last["upload"] = 10_000.0 - 1800
    s.state["upload"] = {"running": False, "last_at": "2026-11-03T10:00:00+00:00", "last_exit": 0}
    st = s.status()
    assert st["upload"]["next_in_s"] == 1800 and st["upload"]["last_exit"] == 0
    assert st["cast"]["next_in_s"] == 0.0   # never ran: due on the next pass
    assert st["tiktok"]["next_in_s"] is None  # off
    s.last["analytics"] = 10_000.0 - 600
    s.state["analytics"] = {"last_exit": 1, "fails": 1}
    assert s.status()["analytics"]["next_in_s"] == serve.RETRY_FAILED_S - 600  # not the 24 h


def test_url_is_the_war_room_here_unless_set(defaults):
    assert serve.url(defaults) == f"http://127.0.0.1:{defaults['warroom']['port']}/"
    assert serve.url({**defaults, "serve": {"url": "http://nas:8765"}}) == "http://nas:8765/"


def test_a_serve_that_is_down_is_one_clear_error(defaults):
    cfg = {**defaults, "serve": {"url": "http://127.0.0.1:1/"}}  # nothing listens on port 1
    with pytest.raises(ClipperError, match="isn't answering"):
        serve.replan(cfg)
    assert serve.tailnet_link(cfg) == ""


def test_a_decision_still_lands_when_serve_is_down(defaults):
    """The bot's replan goes through serve; without it the row comes back with a note."""
    def down(cfg):
        raise ClipperError("clipper serve isn't answering at http://127.0.0.1:8765/")
    row, note = actions.replan(defaults, None, "approve", {"short": "s01"}, via=down)
    assert row == {"short": "s01"} and "next hourly run" in note


def test_the_war_room_starts_retakes_on_serves_runner(defaults, tmp_path):
    calls = []
    warroom.HOOKS["retake"] = lambda: calls.append(1)
    try:
        assert warroom.start_retake(cfg_in(defaults, tmp_path)) and calls == [1]
    finally:
        warroom.HOOKS["retake"] = None


@pytest.fixture
def running(defaults, tmp_path):
    """A war room on a free port with a token, as serve runs it; the client cfg points at it."""
    cfg = cfg_in(defaults, tmp_path, warroom={"token": "k"})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), warroom.make_handler(cfg))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg["serve"] = {"url": f"http://127.0.0.1:{srv.server_address[1]}/"}
    yield cfg
    srv.shutdown()


def test_the_client_passes_the_war_rooms_checks(running, monkeypatch):
    """Token in a header, no browser headers: the handler's same-origin and key checks let it in."""
    h = serve.health(running)
    assert h["services"]["serve"]["level"] == "bad"  # no heartbeat in this tmp folder
    ran = []
    monkeypatch.setattr(schedule, "run", lambda cfg, sync_first: ran.append(sync_first))
    assert serve.replan(running, sync=True) == {"ok": True} and ran == [True]
    with pytest.raises(ClipperError, match="HTTP 401"):
        serve.call({**running, "warroom": {**running["warroom"], "token": "wrong"}}, "/api/health")


def test_retake_runs_on_notes_or_on_its_sync_timer(defaults, tmp_path, monkeypatch):
    """`clipper retake` syncs Kitsu itself, so without a bot (no card refresh) it still runs
    every RETAKE_SYNC_S; an explicit request runs even with [retake] auto off."""
    cfg = cfg_in(defaults, tmp_path, retake={"auto": True})
    s = serve.Serve(cfg)
    ready = next(j.ready for j in s.jobs() if j.name == "retake")
    monkeypatch.setattr(serve.retake, "pending", lambda cfg: [])
    monkeypatch.setattr(serve.actions, "review_dirs", lambda cfg: [tmp_path])
    monkeypatch.setattr(serve.time, "monotonic", lambda: 10_000.0)
    assert ready()  # never ran: the sync run is due
    s.last["retake"] = 10_000.0 - 60
    assert not ready()
    monkeypatch.setattr(serve.retake, "pending", lambda cfg: [("d", "k", {})])
    assert ready()
    off = serve.Serve(cfg_in(defaults, tmp_path, retake={"auto": False}))
    assert next(e for name, e, *_ in off.jobs() if name == "retake") == 0
    started = []
    off.loop = type("L", (), {"create_task": lambda self, c: (started.append(c), c.close())})()
    off.start_retake()
    assert started  # POST /api/retake works with auto off


def test_doctor_wants_schedule_and_news_shared_when_serve_is_elsewhere(defaults, tmp_path):
    from clipper import doctor

    cfg = {**defaults, "serve": {"url": "http://nas:8765/"},
           "paths": {**defaults["paths"], "streams": tmp_path / "streams", "schedule": tmp_path / "schedule.json",
                     "news": tmp_path / "streams" / "news"}}
    results = doctor.collect(doctor.check_shared_with_serve, cfg)
    assert [(r[0], r[2].split()[1]) for r in results] == [("FAIL", "schedule"), ("OK", "news")]
    assert doctor.collect(doctor.check_shared_with_serve, {**cfg, "serve": {"url": ""}}) == []
