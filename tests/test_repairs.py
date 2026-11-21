"""`clipper checkup --fix` (#287): capped repairs from an allowlist, would-fix until enabled."""
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from clipper import checkup, cli, jsonstate, repairs
from clipper.errors import ClipperError

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def iso(delta: timedelta) -> str:
    return (NOW + delta).isoformat(timespec="seconds")


def in_tmp(cfg: dict, tmp_path) -> dict:
    cfg["paths"]["streams"] = tmp_path / "streams"
    cfg["paths"]["schedule"] = tmp_path / "schedule.json"
    cfg["paths"]["streams"].mkdir()
    cfg["checkup"]["gap_hours"] = 0
    return cfg


def failed(cfg: dict, name: str = "kick-1", error: str = "transcribe exited 1: httpx.ReadTimeout: timed out",
           ago: timedelta = timedelta(days=2), **extra) -> object:
    d = cfg["paths"]["streams"] / name
    d.mkdir()
    jsonstate.write(d / "run.json", {"status": "failed", "attempts": cfg["worker"]["max_attempts"],
                                     "error": error, "updated": iso(-ago), "stages": {}, **extra})
    return d


def streams_findings(cfg: dict, fix: bool = False, now: datetime = NOW) -> list[dict]:
    return checkup.run(cfg, only=["streams"], now=now, fix=fix)["findings"]


def test_passing_cause_is_whole_words_any_case():
    pats = ["timed out", "Server Error", "Error code: 429"]
    assert repairs.passing_cause(pats, "requests: 503 Server Error: Service Unavailable") == "Server Error"
    assert repairs.passing_cause(pats, "anthropic: Error code: 429 - rate") == "Error code: 429"
    assert repairs.passing_cause(pats, "ffmpeg: Invalid data found") == ""
    assert repairs.passing_cause(["Timeout"], "modal FunctionTimeoutError") == ""   # not inside a word


def test_a_run_out_of_retries_on_a_passing_error_is_would_fix_until_enabled(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    d = failed(cfg)
    (f,) = streams_findings(cfg)
    assert f["key"] == "failed:kick-1"
    assert f["repair"]["state"] == "would" and "'timed out'" in f["repair"]["text"]
    assert not repairs.ledger_path(cfg).exists()      # without --fix nothing is written
    (f,) = streams_findings(cfg, fix=True)            # --fix, but [checkup] fix is empty
    assert f["repair"]["state"] == "would"
    assert jsonstate.load(d / "run.json")["attempts"] == cfg["worker"]["max_attempts"]   # untouched
    (e,) = repairs.ledger(cfg)
    assert e["key"] == "failed:kick-1" and e["did"] is False
    streams_findings(cfg, fix=True, now=NOW + timedelta(hours=1))
    assert len(repairs.ledger(cfg)) == 1              # a would-fix is logged once a day
    streams_findings(cfg, fix=True, now=NOW + timedelta(hours=25))
    assert len(repairs.ledger(cfg)) == 2


def test_retry_gives_one_attempt_back_and_the_worker_takes_it(defaults, tmp_path):
    from clipper import worker

    cfg = in_tmp(defaults, tmp_path)
    cfg["checkup"]["fix"] = ["retry"]
    d = failed(cfg)
    (d / "live").mkdir()
    jsonstate.write(d / "live" / "state.json", {"status": "done", "channel": "x"})
    assert d not in worker.pending(cfg)
    (f,) = streams_findings(cfg, fix=True)
    assert f["repair"]["state"] == "done" and "one attempt back" in f["repair"]["text"]
    run = jsonstate.load(d / "run.json")
    assert run["attempts"] == cfg["worker"]["max_attempts"] - 1 and run["checkup_retries"] == 1
    assert d in worker.pending(cfg)
    assert "failed:kick-1" not in {f["key"] for f in streams_findings(cfg)}   # gone with the retry left


def test_retry_waits_retry_after_hours_and_skips_other_errors(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    cfg["checkup"]["fix"] = ["retry"]
    soon = failed(cfg, "kick-1", ago=timedelta(hours=1))
    bug = failed(cfg, "kick-2", error="cut exited 1: KeyError: 'start'")
    by = {f["stream"]: f for f in streams_findings(cfg, fix=True)}
    assert by["kick-1"]["repair"]["state"] == "waiting" and "after 11-" in by["kick-1"]["repair"]["text"]
    assert "repair" not in by["kick-2"]               # not a passing cause: it needs you
    assert jsonstate.load(soon / "run.json")["attempts"] == jsonstate.load(bug / "run.json")["attempts"] == 3
    assert repairs.ledger(cfg) == []


def test_retry_is_capped_per_finding_key(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    cfg["checkup"]["fix"] = ["retry"]
    d = failed(cfg)
    for i in range(cfg["checkup"]["max_repairs"]):
        (f,) = streams_findings(cfg, fix=True, now=NOW + timedelta(hours=i))
        assert f["repair"]["state"] == "done"
        run = jsonstate.load(d / "run.json")          # the worker tried again and it failed again
        jsonstate.write(d / "run.json", {**run, "attempts": cfg["worker"]["max_attempts"]})
    (f,) = streams_findings(cfg, fix=True, now=NOW + timedelta(hours=5))
    assert f["repair"]["state"] == "capped" and "needs you" in f["repair"]["text"]
    assert jsonstate.load(d / "run.json")["attempts"] == cfg["worker"]["max_attempts"]
    assert repairs.tries(repairs.ledger(cfg), "failed:kick-1", NOW) == cfg["checkup"]["max_repairs"]


def test_retry_leaves_a_stream_a_worker_holds(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    cfg["checkup"]["fix"] = ["retry"]
    d = failed(cfg)
    (d / ".run.lock").write_text("other:1", encoding="utf-8")
    (f,) = streams_findings(cfg, fix=True)
    assert f["repair"]["state"] == "failed" and "a worker holds it" in f["repair"]["text"]
    assert jsonstate.load(d / "run.json")["attempts"] == 3
    (e,) = repairs.ledger(cfg)
    assert e["did"] is True and e["ok"] is False      # a failed try counts toward the cap


def test_a_key_rejected_run_is_not_retried(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    cfg["checkup"]["fix"] = ["retry"]
    failed(cfg, key_rejected="abc")
    (f,) = streams_findings(cfg, fix=True)
    assert f["key"] == "key:kick-1" and "repair" not in f


def old_tmp(path, age_s: float = 2 * 86400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    t = NOW.timestamp() - age_s
    os.utime(path, (t, t))


def test_tmp_finds_old_temp_files_and_deletes_them_when_enabled(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    root = cfg["paths"]["streams"]
    old = [root / "kick-1" / "run.json.123.456.tmp", root / "kick-1" / "metadata" / "segments" / "s01.json.1.2.tmp",
           tmp_path / "schedule.json.9.9.tmp"]
    for p in old:
        old_tmp(p)
    old_tmp(root / "kick-1" / "review.json.7.8.tmp", age_s=60)    # a write in flight
    old_tmp(root / "kick-1" / "notes.tmp")                        # not jsonstate's
    report = checkup.run(cfg, only=["housekeeping"], now=NOW)
    (f,) = report["findings"]
    assert f["key"] == "tmp:leftover" and f["text"].startswith("3 temp files")
    assert f["repair"]["state"] == "would"
    cfg["checkup"]["fix"] = ["tmp"]
    (f,) = checkup.run(cfg, only=["housekeeping"], now=NOW, fix=True)["findings"]
    assert f["repair"] == {"kind": "tmp", "state": "done", "text": "deleted 3 leftover temp files"}
    assert not any(p.exists() for p in old)
    assert (root / "kick-1" / "review.json.7.8.tmp").exists() and (root / "kick-1" / "notes.tmp").exists()
    assert checkup.run(cfg, only=["housekeeping"], now=NOW)["findings"] == []


def test_an_unknown_repair_in_the_config_is_an_error(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    cfg["checkup"]["fix"] = ["retry", "restart"]
    failed(cfg)
    with pytest.raises(ClipperError, match="restart"):
        streams_findings(cfg, fix=True)
    assert streams_findings(cfg)[0]["repair"]["state"] == "would"   # a plain checkup still runs


def test_lines_say_what_was_fixed_and_what_needs_you(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    cfg["checkup"]["fix"] = ["retry"]
    failed(cfg, "kick-1")
    failed(cfg, "kick-2", error="cut exited 1: KeyError")
    report = checkup.run(cfg, only=["streams"], now=NOW, fix=True)
    text = "\n".join(checkup.lines(report, checkup.ZoneInfo("UTC")))
    assert "2 findings; fixed 1, need you 1" in text
    assert "fix:  done: kick-1: one attempt back" in text
    assert "repairs in the last 24 h: fixed 1" in text and "retry failed:kick-1" in text
    # the next morning's report, the run since fixed: all good, plus what was done
    jsonstate.write(cfg["paths"]["streams"] / "kick-2" / "run.json", {"status": "done"})
    jsonstate.write(cfg["paths"]["streams"] / "kick-1" / "run.json", {"status": "done"})
    later = checkup.run(cfg, only=["streams"], now=NOW + timedelta(hours=8))
    assert checkup.lines(later, checkup.ZoneInfo("UTC"))[:2] == [
        "checkup 11-20 20:00: all good (streams)", "repairs in the last 24 h: fixed 1"]


def test_fix_exits_0_on_findings_and_1_when_a_repair_failed(defaults, tmp_path, monkeypatch):
    cfg = in_tmp(defaults, tmp_path)
    cfg["checkup"]["fix"] = ["retry"]
    monkeypatch.setattr(checkup, "CHECKS", {"streams": checkup.check_streams})
    d = failed(cfg, "kick-2", error="cut exited 1: KeyError")
    args = cli.build_parser().parse_args(["checkup", "--fix"])
    with pytest.raises(SystemExit) as out:
        cli.cmd_checkup(cfg, args)
    assert out.value.code == 0                       # a finding alone: the nightly report's job
    jsonstate.write(d / "run.json", {**jsonstate.load(d / "run.json"), "error": "x: timed out"})
    (d / ".run.lock").write_text("w:1", encoding="utf-8")
    with pytest.raises(SystemExit) as out:
        cli.cmd_checkup(cfg, args)
    assert out.value.code == 1


def test_the_cap_is_per_week_so_a_new_leftover_later_is_fixed(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    cfg["checkup"]["fix"] = ["tmp"]
    p = cfg["paths"]["streams"] / "run.json.1.1.tmp"
    for day in (0, 1):
        old_tmp(p)
        (f,) = checkup.run(cfg, only=["housekeeping"], now=NOW + timedelta(days=day), fix=True)["findings"]
        assert f["repair"]["state"] == "done"
    old_tmp(p)
    (f,) = checkup.run(cfg, only=["housekeeping"], now=NOW + timedelta(days=2), fix=True)["findings"]
    assert f["repair"]["state"] == "capped" and p.exists()
    (f,) = checkup.run(cfg, only=["housekeeping"], now=NOW + timedelta(days=repairs.CAP_DAYS + 1),
                       fix=True)["findings"]
    assert f["repair"]["state"] == "done" and not p.exists()


def test_without_fix_an_enabled_repair_is_due_and_nothing_changes(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    cfg["checkup"]["fix"] = ["retry"]
    d = failed(cfg)
    (f,) = streams_findings(cfg)
    assert f["repair"]["state"] == "due" and "serve's next checkup --fix" in f["repair"]["text"]
    assert repairs.summary([f]) == "will fix 1"
    assert jsonstate.load(d / "run.json")["attempts"] == 3 and not repairs.ledger_path(cfg).exists()


def test_a_ledger_entry_without_a_time_is_skipped(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    failed(cfg)
    path = repairs.ledger_path(cfg)
    path.parent.mkdir(parents=True)
    jsonstate.write(path, {"log": [{"key": "failed:kick-1", "did": False}, {"key": "failed:kick-1", "at": "x"}]})
    (f,) = streams_findings(cfg, fix=True)
    assert f["repair"]["state"] == "would" and len(repairs.ledger(cfg)) == 3


def test_serve_runs_only_the_checks_with_repairs_and_the_sign_ins(defaults, tmp_path):
    from clipper import serve

    assert repairs.checks() == ["streams", "housekeeping"]
    assert set(repairs.checks()) | set(checkup.ALERT_CHECKS) <= set(checkup.CHECKS)
    cfg = in_tmp(defaults, tmp_path)
    s = serve.Serve(cfg)
    started = []
    s.run_clipper = lambda name, args, streak=True: (name, args)
    s.loop = SimpleNamespace(create_task=started.append)
    (job,) = [j for j in s.jobs() if j.name == "checkup"]
    job.start()
    assert job.every_s == 3600
    assert started == [("checkup", ["checkup", "--fix", "--alert", "--only", "streams", "--only", "housekeeping",
                                    "--only", "signins"])]   # --alert: a gone sign-in can't wait for the morning


def test_only_offers_every_check():
    p = cli.build_parser()
    sub = next(a for a in p._subparsers._group_actions if a.dest)
    only = next(a for a in sub.choices["checkup"]._actions if a.dest == "only")
    assert set(only.choices) == set(checkup.CHECKS)


def test_a_retry_that_is_off_is_would_even_while_it_waits(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    cfg["schedule"]["timezone"] = "America/Los_Angeles"
    failed(cfg, ago=timedelta(hours=1))
    (f,) = streams_findings(cfg, fix=True)
    assert f["repair"]["state"] == "would" and repairs.summary([f]) == "would fix 1"
    assert "after 11-20 06:00" in f["repair"]["text"]          # 13:00 UTC, in the schedule's zone
    assert repairs.ledger(cfg) == []                           # not ready: nothing it would have done yet
    cfg["checkup"]["fix"] = ["retry"]
    (f,) = streams_findings(cfg, fix=True)
    assert f["repair"]["state"] == "waiting" and repairs.summary([f]) == "will fix 1"


def test_a_run_without_updated_waits_from_when_run_json_was_written(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    cfg["checkup"]["fix"] = ["retry"]
    d = failed(cfg)
    run = jsonstate.load(d / "run.json")
    del run["updated"]
    jsonstate.write(d / "run.json", run)
    t = NOW.timestamp() - 4 * 3600
    os.utime(d / "run.json", (t, t))
    (f,) = streams_findings(cfg, fix=True)
    assert f["repair"]["state"] == "done"


def test_a_temp_file_that_wont_delete_is_a_failed_repair(defaults, tmp_path, monkeypatch):
    from pathlib import Path

    cfg = in_tmp(defaults, tmp_path)
    cfg["checkup"]["fix"] = ["tmp"]
    old_tmp(cfg["paths"]["streams"] / "run.json.1.1.tmp")
    old_tmp(cfg["paths"]["streams"] / "kick-1" / "run.json.2.2.tmp")
    real = Path.unlink

    def busy(self, missing_ok=False):
        if self.name == "run.json.2.2.tmp":
            raise PermissionError(13, "Permission denied")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", busy)
    (f,) = checkup.run(cfg, only=["housekeeping"], now=NOW, fix=True)["findings"]
    assert f["repair"]["state"] == "failed"
    assert "deleted 1 of 2" in f["repair"]["text"] and "Permission denied" in f["repair"]["text"]
    (e,) = repairs.ledger(cfg)
    assert e["did"] is True and e["ok"] is False
