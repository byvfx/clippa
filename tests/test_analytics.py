"""`clipper analytics`: a passing Google error skips one video instead of failing the run."""
import json
from datetime import UTC, datetime

import httplib2
import pytest
from googleapiclient.errors import HttpError

from clipper import analytics, config, jsonstate, youtube
from clipper.errors import ClipperError

NOW = datetime(2026, 11, 20, 8, tzinfo=UTC)


def http_error(status, reason="backendError"):
    body = {"error": {"code": status, "message": "Internal error encountered.",
                      "errors": [{"message": "Internal error encountered.", "reason": reason}]}}
    return HttpError(httplib2.Response({"status": str(status)}), json.dumps(body).encode())


class Request:
    def __init__(self, answer):
        self.answer = answer

    def execute(self, num_retries=0):
        assert num_retries == analytics.RETRIES  # every call retries a passing error itself
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class FakeAnalytics:
    """reports().query(...): the error set for a video, else one row of 7-day numbers."""
    def __init__(self, errors=None):
        self.errors = errors or {}
        self.asked = []

    def reports(self):
        return self

    def query(self, **kw):
        if "filters" not in kw:  # fetch_channel's: daily totals and traffic sources
            return Request({"columnHeaders": [], "rows": []})
        vid = kw["filters"].removeprefix("video==")
        self.asked.append(vid)
        if vid in self.errors:
            return Request(self.errors[vid])
        return Request({"columnHeaders": [{"name": "views"}], "rows": [[40]]})


def rec(published="2026-11-01T10:00:00-08:00"):
    return {"publish_at": published}


def test_transient():
    assert analytics.transient(http_error(500)) and analytics.transient(http_error(503))
    assert analytics.transient(http_error(429, "rateLimitExceeded"))
    assert analytics.transient(TimeoutError()) and analytics.transient(ConnectionResetError())
    assert not analytics.transient(http_error(403, "forbidden"))
    assert not analytics.transient(KeyError("rows"))


def test_one_video_failing_skips_it_and_keeps_the_rest():
    data = {"a": rec(), "b": rec(), "new": rec("2026-11-18T10:00:00-08:00")}
    ya = FakeAnalytics({"a": http_error(500)})
    skipped = analytics.fill_d7(data, dict.fromkeys(data), ya, NOW)
    assert skipped == {"a": "HttpError 500 Internal error encountered."}
    assert data["a"]["d7_errors"] == 1 and "d7" not in data["a"]
    assert data["b"]["d7"] == {"views": 40}
    assert ya.asked == ["a", "b"]  # "new" isn't a week old yet

    ya.errors = {}  # the next run: Google answers again
    assert analytics.fill_d7(data, dict.fromkeys(data), ya, NOW) == {}
    assert data["a"]["d7"] == {"views": 40} and "d7_errors" not in data["a"]
    assert ya.asked[-1] == "a"  # b has its numbers: not asked again


def test_a_lasting_error_still_raises():
    data = {"a": rec()}
    with pytest.raises(HttpError):
        analytics.fill_d7(data, {"a": {}}, FakeAnalytics({"a": http_error(403, "forbidden")}), NOW)


class FakeData:
    def videos(self):
        return self

    def list(self, part, id):
        return Request({"items": [{"id": v, "snippet": {"title": v.upper()}, "statistics": {"viewCount": "7"}}
                                  for v in id.split(",")]})


@pytest.fixture
def share(tmp_path, monkeypatch):
    (tmp_path / "clipper.toml").write_text(
        f'[paths]\nstreams = "{(tmp_path / "streams").as_posix()}"\n'
        f'schedule = "{(tmp_path / "schedule.json").as_posix()}"\n', encoding="utf-8")
    jsonstate.write(tmp_path / "schedule.json", {"entries": [
        {"key": f"kick-1/s0{i}_x", "stream": "kick-1", "kind": "segment", "status": "uploaded",
         "video_id": vid, "publish_at": "2026-11-01T10:00:00-08:00"} for i, vid in enumerate(["a", "b"])]})
    ya = FakeAnalytics({"a": http_error(500)})
    monkeypatch.setattr(analytics, "credentials", lambda cfg, sign_in=False: (None, True))
    monkeypatch.setattr(youtube, "service", lambda paths, creds=None: FakeData())
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: ya)
    cfg = config.load(tmp_path / "clipper.toml", use_local=False)
    return cfg, ya


def test_run_saves_what_came_in_and_gives_up_after_a_few_runs(share, capsys):
    cfg, ya = share
    for _ in range(analytics.GIVE_UP - 1):  # skipped, the run still succeeds
        data = analytics.run(cfg)
        assert "skipped after retries (HttpError 500" in capsys.readouterr().out
    saved = jsonstate.load(analytics.path_of(cfg))
    assert saved == data and saved["b"]["d7"] == {"views": 40} and saved["a"]["views"] == 7
    assert saved["a"]["d7_errors"] == analytics.GIVE_UP - 1

    with pytest.raises(ClipperError, match=f"failed {analytics.GIVE_UP}\\+ runs in a row .*first: a"):
        analytics.run(cfg)  # it keeps failing: serve DMs about it
    assert jsonstate.load(analytics.path_of(cfg))["a"]["d7_errors"] == analytics.GIVE_UP

    ya.errors = {}
    assert analytics.run(cfg)["a"]["d7"] == {"views": 40}


def test_run_saves_before_a_lasting_error(share):
    cfg, ya = share
    ya.errors = {"a": http_error(403, "forbidden")}
    with pytest.raises(HttpError):
        analytics.run(cfg)
    assert jsonstate.load(analytics.path_of(cfg))["b"]["views"] == 7  # the counts still landed
