"""`clipper checkup` (#185): the problems alerts miss, found from the files alone."""
from datetime import UTC, datetime, timedelta

import pytest

from clippa import checkup, cli, daemon, jsonstate

NOW = datetime(2026, 11, 9, 20, 0, tzinfo=UTC)


def iso(delta: timedelta) -> str:
    return (NOW + delta).isoformat(timespec="seconds")


def in_tmp(cfg: dict, tmp_path) -> dict:
    cfg["paths"]["streams"] = tmp_path / "streams"
    cfg["paths"]["schedule"] = tmp_path / "schedule.json"
    cfg["paths"]["streams"].mkdir()
    return cfg


def stream(cfg: dict, name: str, run: dict | None = None, files: dict | None = None) -> object:
    d = cfg["paths"]["streams"] / name
    d.mkdir()
    if run is not None:
        jsonstate.write(d / "run.json", run)
    for rel, data in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        jsonstate.write(p, data)
    return d


def keys(found: list[dict]) -> set[str]:
    return {f["key"] for f in found}


def test_services_without_heartbeats_and_with_fresh_ones(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    found = checkup.check_services(cfg, NOW)
    assert keys(found) == {"service:serve", "service:bot", "service:recorder"}
    assert "none arrive" in next(f["text"] for f in found if f["key"] == "service:bot")
    for name in ("serve", "bot", "record"):
        p = cfg["paths"]["streams"] / f".{name}" / "status.json"
        p.parent.mkdir()
        jsonstate.write(p, {"at": iso(-timedelta(seconds=30)), "host": "nas"})
    assert checkup.check_services(cfg, NOW) == []
    jsonstate.write(daemon.heartbeat_path(cfg, "serve"), {"at": iso(-timedelta(minutes=20))})
    assert keys(checkup.check_services(cfg, NOW)) == {"service:serve"}


def test_a_stage_running_too_long_is_stuck_and_a_silent_worker_is_an_orphan(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    beat = iso(-timedelta(minutes=1))
    stream(cfg, "kick-1", {"status": "running", "heartbeat": beat, "stages": {
        "score": {"status": "done"}, "cut": {"status": "running", "started": iso(-timedelta(hours=7)),
                                              "log": "run_cut.log"}}})
    stream(cfg, "kick-2", {"status": "running", "heartbeat": beat, "stages": {
        "cut": {"status": "running", "started": iso(-timedelta(hours=2))}}})
    stream(cfg, "kick-3", {"status": "running", "heartbeat": iso(-timedelta(hours=1)), "stages": {}})
    found = checkup.check_streams(cfg, NOW)
    assert keys(found) == {"stuck:kick-1:cut", "orphan:kick-3"}
    stuck = next(f for f in found if f["key"] == "stuck:kick-1:cut")
    assert "7.0 h" in stuck["text"] and stuck["look"] == "streams/kick-1/run_cut.log"


def test_failed_with_no_retries_left(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    stream(cfg, "kick-1", {"status": "failed", "attempts": 3, "error": "segments exited 1: boom",
                           "updated": iso(-timedelta(hours=1))})
    stream(cfg, "kick-2", {"status": "failed", "attempts": 1, "updated": iso(-timedelta(minutes=5))})
    found = checkup.check_streams(cfg, NOW)
    assert keys(found) == {"failed:kick-1"} and "boom" in found[0]["text"]


def test_a_finished_recording_nobody_picked_up(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    # worker.pending compares against the real clock, so the recording ends relative to it
    real = datetime.now(UTC)
    ended = (real - timedelta(hours=5)).isoformat(timespec="seconds")
    stream(cfg, "kick-1", None, {"live/state.json": {"status": "done", "channel": "kino"},
                                  "meta.json": {"ended_at": ended}})
    found = checkup.check_streams(cfg, real)
    assert keys(found) == {"queued:kick-1"} and "not started" in found[0]["text"]
    # a stream the worker is busy with: the queue behind it is expected
    stream(cfg, "kick-2", {"status": "running", "heartbeat": real.isoformat(timespec="seconds"),
                           "stages": {"cut": {"status": "running", "started": real.isoformat()}}})
    assert checkup.check_streams(cfg, real) == []


def test_schedule_late_uploads_and_gaps(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    e = lambda key, delta, status="scheduled": {"key": key, "stream": key.split("/")[0], "kind": "segment",
                                                "title": key, "publish_at": iso(delta), "status": status}
    jsonstate.write(cfg["paths"]["schedule"], {"entries": [
        e("kick-1/s01", timedelta(minutes=30)), e("kick-1/s02", -timedelta(hours=2)),
        e("kick-1/s03", timedelta(minutes=10), "uploaded"), e("kick-1/s04", timedelta(hours=5))]})
    found = checkup.check_schedule(cfg, NOW)
    assert keys(found) == {"late:kick-1/s01", "late:kick-1/s02"}
    assert "releases in 30 min" in found[0]["text"] and "was due 2.0 h ago" in found[1]["text"]

    jsonstate.write(cfg["paths"]["schedule"], {"entries": [e("kick-1/s05", timedelta(days=3))]})
    found = checkup.check_schedule(cfg, NOW)
    assert keys(found) == {"schedule:gap"} and found[0]["level"] == checkup.WARN
    cfg["checkup"]["gap_hours"] = 0
    assert checkup.check_schedule(cfg, NOW) == []


def test_lint_text_matches_whole_words_only():
    words = ["hosts", "co-host", "co-hosts"]
    assert checkup.lint_text(words, {"title": "The Hosts lose it"}) == ["title says 'Hosts'"]
    assert checkup.lint_text(words, {"t": "Andy's co-host quits"}) == ["t says 'co-host'"]
    assert checkup.lint_text(words, {"t": "Ghosts, a host, hostess, co-hosted"}) == []
    assert checkup.lint_text([], {"t": "hosts"}) == []


def test_lint_reads_what_upload_would_send(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    stream(cfg, "kick-1", None, {"review.json": {
        "s01_x": {"fields": {"YouTube title": "The hosts lose it", "YouTube description": "",
                             "Thumbnail text": "CO-HOST QUITS"}},
        "s02_y": {"fields": {"YouTube title": "Fine " * 30, "YouTube description": "All good here."}},
        "s03_z": {"fields": {"YouTube title": "Fine", "YouTube description": "All good here."}}}})
    entry = lambda k, status="scheduled": {"key": f"kick-1/{k}", "stream": "kick-1", "kind": "segment", "title": k,
                                           "file": f"kick-1/segments/{k}.mp4", "status": status,
                                           "publish_at": iso(timedelta(hours=5))}
    jsonstate.write(cfg["paths"]["schedule"], {"entries": [
        entry("s01_x"), entry("s02_y"), entry("s03_z"), {**entry("s01_x"), "key": "kick-1/s01_x", "status": "uploaded"}]})
    found = {f["key"]: f["text"] for f in checkup.check_lint(cfg, NOW)}
    assert set(found) == {"lint:kick-1/s01_x", "lint:kick-1/s02_y"}
    one = found["lint:kick-1/s01_x"]
    assert "title says 'hosts'" in one and "thumbnail text says 'CO-HOST'" in one and "no description" in one
    assert "title is over YouTube's limit" in found["lint:kick-1/s02_y"]


def test_mark_remembers_first_seen_and_forgets_what_cleared(defaults, tmp_path, monkeypatch):
    cfg = in_tmp(defaults, tmp_path)
    now = {"found": [checkup.finding("a:1", "streams", checkup.BAD, "one"),
                     checkup.finding("b:2", "streams", checkup.BAD, "two")]}
    monkeypatch.setattr(checkup, "CHECKS", {"streams": lambda c, t: list(map(dict, now["found"])),
                                            "lint": lambda c, t: [checkup.finding("l:1", "lint", "warn", "x")]})
    first = checkup.run(cfg, mark=True, now=NOW)
    assert all(f["new"] for f in first["findings"])
    now["found"] = now["found"][:1]
    later = NOW + timedelta(hours=1)
    second = checkup.run(cfg, only=["streams"], mark=True, now=later)
    assert [(f["key"], f["new"], f["first_seen"]) for f in second["findings"]] == [("a:1", False, first["at"])]
    seen = jsonstate.load(cfg["paths"]["streams"] / ".checkup" / "state.json")["seen"]
    assert set(seen) == {"a:1", "l:1"}   # b:2 cleared; lint wasn't run, so its finding stays
    now["found"].append(checkup.finding("b:2", "streams", checkup.BAD, "two"))
    assert [f["new"] for f in checkup.run(cfg, only=["streams"], now=later)["findings"]] == [False, True]


def test_a_check_that_crashes_is_a_finding_and_the_rest_still_run(defaults, tmp_path, monkeypatch):
    cfg = in_tmp(defaults, tmp_path)

    def boom(c, t):
        raise RuntimeError("broken")

    monkeypatch.setattr(checkup, "CHECKS", {"lint": boom, "schedule": checkup.check_schedule})
    cfg["checkup"]["gap_hours"] = 0
    report = checkup.run(cfg, now=NOW)
    assert [f["key"] for f in report["findings"]] == ["check:lint"]
    assert "RuntimeError: broken" in report["findings"][0]["text"]
    text = "\n".join(checkup.lines(report, checkup.ZoneInfo("UTC")))
    assert "1 finding" in text and "BAD  lint" in text


def test_lines_when_all_is_good():
    report = {"at": NOW.isoformat(), "checks": ["services", "lint"], "findings": []}
    assert checkup.lines(report, checkup.ZoneInfo("UTC")) == ["checkup 11-09 20:00: all good (services, lint)"]


def test_alert_send_queues_a_checkup_dm(defaults, tmp_path, capsys):
    from clippa import alerts

    cfg = in_tmp(defaults, tmp_path)
    args = cli.build_parser().parse_args(["alert", "send", "Checkup: kick-1 is stuck in cut"])
    cli.cmd_alert(cfg, args)
    (f,) = alerts.outbox(cfg).glob("*.json")
    ev = jsonstate.load(f)
    assert ev["kind"] == "checkup" and ev["text"] == "Checkup: kick-1 is stuck in cut"
    assert "queued; the bot DMs it" in capsys.readouterr().out


def test_a_streams_own_config_can_allow_a_longer_stage(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    d = stream(cfg, "kick-1", {"status": "running", "heartbeat": iso(-timedelta(minutes=1)), "stages": {
        "cut": {"status": "running", "started": iso(-timedelta(hours=7))}}})
    (d / "config.toml").write_text("[checkup]\nstuck_hours = 12\n", encoding="utf-8")
    assert checkup.check_streams(cfg, NOW) == []


def test_alert_send_reads_cut_utf8_from_stdin_and_trims_to_discord(defaults, tmp_path, monkeypatch, capsys):
    import io
    import sys

    from clippa import alerts

    cfg = in_tmp(defaults, tmp_path)
    raw = ("é" * 1950).encode() + "—".encode()[:2]   # over 1900 characters, cut mid-character
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
    cli.cmd_alert(cfg, cli.build_parser().parse_args(["alert", "send", "-"]))
    (f,) = alerts.outbox(cfg).glob("*.json")
    text = jsonstate.load(f)["text"]
    assert text.startswith("é" * 10) and len(text) <= 1900
    assert "rest is cut" in capsys.readouterr().err


def test_the_disk_check_is_off_by_default_and_warns_under_keep_free_pct(defaults, tmp_path, monkeypatch):
    from clippa import streams
    cfg = in_tmp(defaults, tmp_path)
    monkeypatch.setattr(streams, "free_pct", lambda cfg, plus=0: 4.0)
    assert checkup.check_disk(cfg) == []                       # keep_free_pct = 0
    cfg["streams"]["keep_free_pct"] = 15
    cfg["streams"]["keep_source_days"] = 60
    (f,) = checkup.check_disk(cfg)
    assert f["key"] == "disk:free" and f["level"] == checkup.BAD and "4.0%" in f["text"] and "60 days" in f["look"]
    monkeypatch.setattr(streams, "free_pct", lambda cfg, plus=0: 12.0)
    assert checkup.check_disk(cfg)[0]["level"] == checkup.WARN
    monkeypatch.setattr(streams, "free_pct", lambda cfg, plus=0: 40.0)
    assert checkup.check_disk(cfg) == []
    assert keys(checkup.check_housekeeping(cfg, NOW)) == set()


def test_tools_warns_on_an_old_yt_dlp(defaults):
    assert checkup.check_tools(defaults, NOW, version="2026.11.04.232659.dev0") == []
    (f,) = checkup.check_tools(defaults, NOW, version="2026.08.01")
    assert f["key"] == "tools:yt-dlp" and "100 days old" in f["text"]
    assert checkup.check_tools(defaults, NOW, version="") == []
    defaults["checkup"]["ytdlp_days"] = 0
    assert checkup.check_tools(defaults, NOW, version="2020.01.01") == []


def _netscape(path, rows):
    path.write_text("# Netscape HTTP Cookie File\n" + "".join(
        f"{d}\tTRUE\t/\tTRUE\t{int(exp)}\t{name}\tv\n" for d, name, exp in rows), encoding="utf-8")


def test_signins_from_cookies_files_and_tokens(defaults, tmp_path):
    import os

    cfg = in_tmp(defaults, tmp_path)
    ts = NOW.timestamp()
    yt = tmp_path / "youtube_cookies.txt"
    cfg["paths"]["youtube_cookies"] = yt
    cfg["youtube"]["cookies_from_browser"] = ""
    _netscape(yt, [(".youtube.com", "SID", ts + 200 * 86400), (".youtube.com", "VISITOR", ts - 10)])
    os.utime(yt, (ts, ts))
    tw = tmp_path / "twitch.txt"
    cfg["paths"]["twitch_cookies"] = tw
    _netscape(tw, [(".twitch.tv", "auth-token", ts + 2 * 86400)])
    cfg["paths"]["hei_cookies"] = tmp_path / "none.txt"
    cfg["paths"]["tiktok_token"] = tmp_path / "tt.json"
    jsonstate.write(cfg["paths"]["tiktok_token"], {"refresh_expires_at": ts - 5})
    cfg["paths"]["x_token"] = tmp_path / "x.json"   # no file: nothing
    found = {f["key"]: f for f in checkup.check_signins(cfg, NOW)}
    assert set(found) == {"signin:twitch", "signin:tiktok-token"}
    assert found["signin:twitch"]["level"] == checkup.WARN and "expires 11-11" in found["signin:twitch"]["text"]
    assert found["signin:tiktok-token"]["level"] == checkup.BAD
    _netscape(yt, [(".youtube.com", "SID", ts - 1)])
    os.utime(yt, (ts, ts))
    assert checkup.check_signins(cfg, NOW)[0]["key"] == "signin:youtube"
    _netscape(yt, [(".youtube.com", "SID", ts + 200 * 86400)])
    os.utime(yt, (ts - 30 * 3600, ts - 30 * 3600))
    assert "signin:youtube-export" in keys(checkup.check_signins(cfg, NOW))
    cfg["youtube"]["cookies_from_browser"] = "firefox:clipper"   # doctor checks the profile instead
    assert "signin:youtube-export" not in keys(checkup.check_signins(cfg, NOW))
    cfg["checkup"]["signin_days"] = 0
    assert checkup.check_signins(cfg, NOW) == []


def test_alert_dms_a_gone_sign_in_once_and_again_when_it_comes_back(defaults, tmp_path):
    from clippa import alerts

    cfg = in_tmp(defaults, tmp_path)
    gone = checkup.finding("signin:youtube", "signins", checkup.BAD, "YouTube cookies y.txt hold no live sign-in",
                           look="export it")
    soon = checkup.finding("signin:twitch", "signins", checkup.WARN, "Twitch cookies t.txt: the sign-in expires 11-11")
    stuck = checkup.finding("stuck:kick-1:cut", "streams", checkup.BAD, "kick-1 is stuck in cut")
    report = {"at": NOW.isoformat(), "checks": ["streams", "signins"], "findings": [gone, soon, stuck]}
    assert checkup.alert(cfg, {**report, "findings": [soon, stuck]}) == []
    assert not (cfg["paths"]["streams"] / ".checkup").exists()        # the usual hour writes nothing
    assert keys(checkup.alert(cfg, report)) == {"signin:youtube"}     # not the warning, not another check's
    (f,) = alerts.outbox(cfg).glob("*.json")
    ev = jsonstate.load(f)
    assert ev["kind"] == "checkup"
    assert ev["text"] == "YouTube cookies y.txt hold no live sign-in\nlook: export it"
    assert checkup.alert(cfg, report) == []                           # still gone an hour later: said once
    assert checkup.alert(cfg, {**report, "checks": ["streams"], "findings": [stuck]}) == []
    assert checkup.alert(cfg, report) == []                           # a run without signins forgot nothing
    assert checkup.alert(cfg, {**report, "findings": [soon]}) == []   # signed in again: forgotten
    later = {**report, "at": (NOW + timedelta(hours=1)).isoformat()}
    assert keys(checkup.alert(cfg, later)) == {"signin:youtube"}      # gone again: a new DM
    sent = [jsonstate.load(f)["key"] for f in alerts.outbox(cfg).glob("*.json")]
    assert len(set(sent)) == 2    # a key of its own each time: the bot's cooldown mustn't swallow the second


def test_alert_keeps_what_it_knows_through_a_crashed_check_a_broken_file_and_a_dm_not_queued(
        defaults, tmp_path, monkeypatch):
    from clippa import alerts

    cfg = in_tmp(defaults, tmp_path)
    gone = checkup.finding("signin:youtube", "signins", checkup.BAD, "YouTube cookies y.txt hold no live sign-in")
    crash = checkup.finding("check:signins", "signins", checkup.BAD, "the signins check failed: OSError: boom")
    report = {"at": NOW.isoformat(), "checks": ["signins"], "findings": [gone]}
    monkeypatch.setattr(alerts, "send", lambda *a, **k: False)
    assert checkup.alert(cfg, report) == []                           # not queued: not remembered
    monkeypatch.undo()
    assert keys(checkup.alert(cfg, report)) == {"signin:youtube"}     # ...so the next hour tries again
    assert keys(checkup.alert(cfg, {**report, "findings": [crash]})) == {"check:signins"}
    assert checkup.alert(cfg, report) == []                           # the crash forgot nothing: no second DM
    path = cfg["paths"]["streams"] / ".checkup" / "alerted.json"
    assert set(jsonstate.load(path)["sent"]) == {"signin:youtube"}    # the crash cleared, and was forgotten
    jsonstate.write(path, {"sent": {"signin:youtube": "2026-11-09", "signin:x-token": {"check": "signins"}}})
    assert keys(checkup.alert(cfg, report)) == {"signin:youtube"}     # an entry that isn't one: as if unseen
    jsonstate.write(path, ["not", "a", "dict"])
    assert keys(checkup.alert(cfg, report)) == {"signin:youtube"}


def test_checkup_alert_queues_the_dm_and_a_finding_alone_never_fails_serves_job(defaults, tmp_path, capsys):
    from clippa import alerts

    cfg = in_tmp(defaults, tmp_path)
    cfg["paths"]["tiktok_token"] = tmp_path / "tt.json"
    jsonstate.write(cfg["paths"]["tiktok_token"], {"refresh_expires_at": 1})
    args = cli.build_parser().parse_args(["checkup", "--fix", "--alert", "--only", "signins"])
    with pytest.raises(SystemExit) as e:
        cli.cmd_checkup(cfg, args)
    assert e.value.code == 0
    assert "DM queued: TikTok sign-in expired" in capsys.readouterr().out
    (f,) = alerts.outbox(cfg).glob("*.json")
    assert jsonstate.load(f)["text"].endswith("look: clipper tiktok --sign-in")
    args = cli.build_parser().parse_args(["checkup", "--only", "signins"])   # by hand: no DM, exit 1
    with pytest.raises(SystemExit) as e:
        cli.cmd_checkup(cfg, args)
    assert e.value.code == 1 and len(list(alerts.outbox(cfg).glob("*.json"))) == 1


def test_repeated_errors_group_lines_that_differ_only_in_ids():
    tz = checkup.ZoneInfo("America/Los_Angeles")
    since = datetime(2026, 11, 9, 0, 0, tzinfo=tz)
    text = "\n".join([
        *(f"2026-11-09 1{i}:00:00 ERROR   clipper.serve: upload kick-13{i}/v0{i}_t1 failed: 'quota {i}'"
          for i in range(5)),
        "2026-11-08 10:00:00 ERROR   clipper.serve: upload kick-1/v01 failed: 'old'",
        "2026-11-09 12:00:00 WARNING clipper.serve: upload kick-1/v01 failed: 'a warning'",
        "2026-11-09T12:00:00 ERROR worker: once"])
    (row,) = checkup.repeated_errors(text, since, 5, tz)
    assert row[1] == 5 and row[2].endswith("'quota 4'")
    assert checkup.repeated_errors(text, since, 6, tz) == []
    assert checkup.repeated_errors(text, since + timedelta(hours=11), 1, tz)[0][1] == 4   # 11:00 is before


def test_logs_check_reads_serves_and_the_bots_log(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    now = datetime.now(UTC)
    t = now.astimezone(checkup.ZoneInfo(cfg["schedule"]["timezone"])).replace(tzinfo=None) - timedelta(minutes=5)
    cfg["paths"]["serve_log"] = tmp_path / "serve.log"
    cfg["paths"]["bot_log"] = tmp_path / "missing.log"
    (tmp_path / "serve.log").write_text("".join(
        f"{t:%Y-%m-%d %H:%M:%S} ERROR   clipper.serve: job x failed with {i}\n" for i in range(6)), encoding="utf-8")
    (f,) = checkup.check_logs(cfg, now)
    assert f["check"] == "logs" and "6 times" in f["text"]
    cfg["checkup"]["repeat_errors"] = 0
    assert checkup.check_logs(cfg, now) == []


def test_approvals_finds_an_item_the_plan_never_saw(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    cfg["youtube"]["auto_upload_minutes"] = 60
    jsonstate.write(cfg["paths"]["schedule"], {
        "generated_at": iso(-timedelta(minutes=30)), "entries": [{"key": "kick-1/v01"}],
        "unscheduled": [{"key": "kick-1/s02", "reason": "hold"}]})
    old, new = iso(-timedelta(hours=5)), iso(-timedelta(minutes=10))
    stream(cfg, "kick-1", files={"review.json": {
        "v01": {"status": "approved", "updated_at": old}, "s02": {"status": "approved", "updated_at": old},
        "v03": {"status": "approved", "updated_at": old}, "v04": {"status": "approved", "updated_at": new},
        "v05": {"status": "wfa", "updated_at": old}, "v06": {"status": "approved"}}})
    (f,) = checkup.check_approvals(cfg, NOW)
    assert f["key"] == "approvals:unplanned" and f["text"].endswith("kick-1/v03")
    jsonstate.write(cfg["paths"]["schedule"], {"generated_at": iso(-timedelta(hours=4)), "entries": []})
    assert keys(checkup.check_approvals(cfg, NOW)) == {"approvals:plan", "approvals:unplanned"}
    cfg["paths"]["schedule"].unlink()   # no plan at all: the approvals are still found
    assert keys(checkup.check_approvals(cfg, NOW)) == {"approvals:unplanned"}
    cfg["checkup"]["approved_hours"] = 0
    assert checkup.check_approvals(cfg, NOW) == []
