"""The war room's OpenRouter budget: limits, the warning percent, and 402/403 names.

The pipeline's fake-API run (what a 402 leaves on disk) is not here. This covers the panel's
routes, the saved warning, a missing or read-only key, and how a refusal is named and recorded.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from clippa import jsonstate, warroom
from clippa import openrouter_budget as ob
from clippa.errors import ClipperError

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
INF = "sk-or-v1-inference-secret-aaa"
MGMT = "sk-or-v1-management-secret-bbb"
HASH = "f01d52606dc8f0a8303a7b5cc3fa07109c2e346cec7c0a16b40de462992ce943"
LABEL = "sk-or-v1-3f2abc...a91"

KEY = {"label": LABEL, "limit": 60, "limit_remaining": 32.70, "limit_reset": "monthly",
       "usage": 100.5, "usage_daily": 3.10, "usage_weekly": 11.85, "usage_monthly": 27.30,
       "is_free_tier": False, "is_management_key": False}
LISTED = {"name": "clippa-prod", "hash": HASH, "label": LABEL, "disabled": False,
          "limit": 60, "limit_reset": "monthly"}
CREDITS = {"total_credits": 150, "total_usage": 65.80}
MGMT_KEY = {"is_management_key": True, "expires_at": "2027-02-28T00:00:00Z",
            "label": "sk-or-v1-mgmtsecret...zzz"}


@pytest.fixture(autouse=True)
def _clear_cache():
    ob._CACHE.clear()
    yield
    ob._CACHE.clear()


@pytest.fixture
def cfg(defaults, tmp_path):
    streams = tmp_path / "streams"
    streams.mkdir()
    return {**defaults, "paths": {**defaults["paths"], "streams": streams}}


class R:
    def __init__(self, code, payload, headers=None):
        self.status_code, self._payload, self.headers = code, payload, headers or {}
        self.ok = 200 <= code < 300
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


def _route(monkeypatch, answers):
    """answers: list of (method, url-contains, bearer-contains, response), consumed in order per match."""
    import requests

    seen = []

    def send(method, url, headers, **kwargs):
        bearer = headers.get("Authorization", "")
        seen.append({"method": method, "url": url, "bearer": bearer, "json": kwargs.get("json")})
        for i, (m, part, who, resp) in enumerate(answers):
            if m == method and part in url and who in bearer:
                answers.pop(i)
                return resp
        raise AssertionError(f"unexpected {method} {url} {bearer}")

    monkeypatch.setattr(requests, "get", lambda url, headers, timeout: send("GET", url, headers))
    monkeypatch.setattr(requests, "patch", lambda url, headers, json, timeout: send("PATCH", url, headers, json=json))
    return seen


def _live(monkeypatch, *, inf=INF, mgmt=MGMT, key=None, listed=None, credits=True, mgmt_info=True):
    monkeypatch.setenv("OPENROUTER_API_KEY", inf)
    if mgmt:
        monkeypatch.setenv(ob.MGMT_ENV, mgmt)
    else:
        monkeypatch.delenv(ob.MGMT_ENV, raising=False)
    answers = [("GET", "/api/v1/key", inf, R(200, {"data": key if key is not None else KEY}))]
    if credits:
        answers.append(("GET", "/credits", inf, R(200, {"data": CREDITS})))
    else:
        answers.append(("GET", "/credits", inf, R(404, {"error": {"message": "no"}})))
    if mgmt and mgmt_info:
        answers.append(("GET", "/api/v1/key", mgmt, R(200, {"data": MGMT_KEY})))
        answers.append(("GET", "/api/v1/keys", mgmt, R(200, {"data": [listed if listed is not None else LISTED]})))
    return answers


def test_classify_names_an_in_flight_wait_and_a_spent_key():
    assert ob.classify_payment(402, "in-flight budget is full", "6") == "openrouter_in_flight_budget"
    assert ob.classify_payment(402, "", "6") == "openrouter_in_flight_budget"
    assert ob.classify_payment(402, "Key limit exceeded", "6") == "openrouter_key_limit"
    # credits wording alone can't tell the key's cap from the account (limit_source can)
    assert ob.classify_payment(402, "This request requires more credits", None) == "openrouter_key_limit"
    assert ob.classify_payment(403, "Insufficient credits", None) == "openrouter_key_limit"
    assert ob.classify_payment(402, "", None) == "openrouter_key_limit"
    assert ob.classify_payment(403, "Key limit exceeded. Manage it", None) == "openrouter_key_limit"
    assert ob.classify_payment(403, "not allowed to use this model", None) is None
    assert ob.classify_payment(401, "nope", None) is None
    # says in-flight, but no wait it can honour: still not a spent limit (the call fails, no stop)
    assert ob.classify_payment(402, "in-flight budget", "500") == "openrouter_in_flight_budget"
    assert ob.classify_payment(402, "in-flight budget", None) == "openrouter_in_flight_budget"
    assert ob.retry_seconds("6") == 6
    assert ob.retry_seconds("0") == 0
    assert ob.retry_seconds("Wed, 21 Oct 2015 07:28:00 GMT") is None


def test_mask_keeps_a_prefix_and_a_suffix_and_an_already_redacted_label():
    assert ob.mask_secret("sk-or-v1-3f2abcDEFsecretKEY") == "sk-or-v1-3…KEY"
    assert ob.mask_secret(LABEL) == "sk-or-v1-3f2abc…a91"
    assert ob.mask_secret("short") == "••••"
    assert ob.mask_secret("") == ""


def test_next_reset_is_midnight_utc_monday_or_the_first():
    assert ob.next_reset(NOW, "daily") == datetime(2026, 11, 25, tzinfo=UTC)
    assert ob.next_reset(NOW, "weekly") == datetime(2026, 11, 30, tzinfo=UTC)  # Monday
    assert ob.next_reset(NOW, "monthly") == datetime(2026, 12, 1, tzinfo=UTC)
    assert ob.next_reset(NOW, None) is None
    monday = datetime(2026, 11, 30, 12, tzinfo=UTC)
    assert ob.next_reset(monday, "weekly") == datetime(2026, 12, 7, tzinfo=UTC)
    assert ob.next_reset(datetime(2026, 12, 1, tzinfo=UTC), "monthly") == datetime(2026, 12, 1, tzinfo=UTC)


def test_routes_are_registered():
    assert "/api/openrouter" in warroom.GET_API
    for path in ("/api/openrouter/refresh", "/api/openrouter/warn", "/api/openrouter/limit",
                 "/api/openrouter/disabled"):
        assert path in warroom.POST_API


def test_warn_percent_persists_and_rejects_out_of_range(cfg):
    view = warroom.POST_API["/api/openrouter/warn"](cfg, {"pct": 80})
    assert view["warn_pct"] == 80 and view["configured"] is False
    saved = jsonstate.read(ob._path(cfg))
    assert saved["warn_pct"] == 80
    with pytest.raises(ClipperError, match="1 to 99"):
        ob.set_warn(cfg, 0)
    with pytest.raises(ClipperError, match="1 to 99"):
        ob.set_warn(cfg, "nope")
    assert jsonstate.read(ob._path(cfg))["warn_pct"] == 80


def test_a_missing_key_is_an_empty_state_and_makes_no_request(cfg, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv(ob.MGMT_ENV, raising=False)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("asked OpenRouter"))
    view = ob.status(cfg, now=NOW)
    assert view["configured"] is False and view["level"] == "missing"
    assert "OPENROUTER_API_KEY" in view["missing"]
    assert view["key"] is None and view["header"]["badge"] is False
    assert view["run_budget"]["flag"] == "--budget" and "$" not in view["run_budget"]["text"]


def test_an_inference_key_alone_is_read_only(cfg, monkeypatch):
    answers = _live(monkeypatch, mgmt="")
    _route(monkeypatch, answers)
    view = ob.status(cfg, now=NOW)
    assert view["configured"] is True and view["can_edit"] is False and view["read_only"] is True
    assert "OPENROUTER_MANAGEMENT_KEY" in view["edit_why"]
    assert view["key"]["name"] == "clippa key"  # the list, which would name it, was not asked
    assert view["key"]["limit_usd"] == 60 and view["key"]["month_usd"] == 27.30
    assert view["level"] == "ok" and view["header"]["spend"] == "ok"
    assert view["account"]["balance_usd"] == 84.20 and view["account"]["bought_usd"] == 150
    assert view["links"]["credits"] == ob.CREDITS_URL
    assert not answers  # every prepared answer was used, and no /keys call was prepared


def test_status_reads_the_key_the_balance_and_the_management_expiry(cfg, monkeypatch):
    _route(monkeypatch, _live(monkeypatch))
    view = ob.status(cfg, now=NOW)
    assert view["can_edit"] is True and view["key"]["name"] == "clippa-prod"
    assert view["key"]["label"] == "sk-or-v1-3f2abc…a91"
    assert view["key"]["used_label"] == "this month" and view["key"]["pct"] == 45.5
    assert view["periods"][2]["current"] is True
    assert "Dec 1, 00:00 UTC" in view["key"]["reset_text"]
    assert view["management"]["text"] == "Management key: set, expires Feb 28, 2027"
    assert view["level"] == "ok" and view["banner"] is None and view["header"]["badge"] is False
    blob = json.dumps(view)
    assert INF not in blob and MGMT not in blob and HASH not in blob
    # cached: a second read does not ask again
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("asked again inside the cache"))
    assert ob.status(cfg, now=NOW)["key"]["month_usd"] == 27.30


def test_warning_and_stopped_levels_and_one_passed_event(cfg, monkeypatch):
    warned = {**KEY, "usage_monthly": 49.80, "limit_remaining": 10.20}
    _route(monkeypatch, _live(monkeypatch, key=warned))
    view = ob.status(cfg, now=NOW)
    assert view["level"] == "warn" and view["header"] == {"spend": "warn", "health": None, "badge": True}
    assert view["banner"]["kind"] == "warn" and view["banner"]["title"] == "83% of the monthly cap is used."
    assert "$10.20 left" in view["banner"]["body"] and "Dec 1, 00:00 UTC" in view["banner"]["body"]
    assert view["events"][0]["title"] == "Passed 75%"
    assert "$45.00" in view["events"][0]["text"]  # 75% of the $60 cap, not a fixed runner guard
    again = ob.status(cfg, now=NOW)  # inside the cache: the crossing is recorded once
    assert [e["kind"] for e in again["events"]] == ["passed"]

    ob._CACHE.clear()
    stopped = {**KEY, "usage_monthly": 60.0, "limit_remaining": 0}
    _route(monkeypatch, _live(monkeypatch, key=stopped))
    view = ob.status(cfg, fresh=True, now=NOW)
    assert view["level"] == "stopped"
    assert view["header"] == {"spend": "bad", "health": "stopped", "badge": True}
    assert view["banner"]["title"] == ob.COPY["stop_title"]
    assert "openrouter_key_limit" in view["banner"]["body"]
    assert "402" in view["banner"]["body"]


def test_a_recorded_stop_names_the_stream_and_a_later_call_fills_it_in(cfg):
    ob.note_stop(cfg, status=402, model="openrouter:openai/gpt-5-mini")
    ob.note_stop(cfg, status=402, show="My Show · Nov 22", stage="Scoring", detail="segment 41 of 63")
    saved = jsonstate.read(ob._path(cfg))
    assert len(saved["events"]) == 1
    assert saved["events"][0]["show"] == "My Show · Nov 22"
    assert saved["events"][0]["detail"] == "segment 41 of 63"
    raw = {"key": {**KEY, "usage_monthly": 60, "limit_remaining": 0}, "listed": LISTED,
           "credits": CREDITS, "can_edit": True, "management_configured": True,
           "management_expires": "Feb 28, 2027", "secrets": []}
    view = ob.view_from(cfg, raw, ob._settings(cfg), NOW)
    assert "My Show · Nov 22 stopped at Scoring, segment 41 of 63." in view["banner"]["body"]
    assert view["events"][0]["tone"] == "bad" and view["events"][0]["code"] == "openrouter_key_limit"


def test_an_in_flight_retry_is_a_countdown_and_clears_when_the_call_lands(cfg, monkeypatch):
    rid = ob.note_retry(cfg, seconds=6, status=402)
    raw = {"key": KEY, "listed": LISTED, "credits": CREDITS, "can_edit": True,
           "management_configured": True, "secrets": []}
    view = ob.view_from(cfg, raw, ob._settings(cfg), datetime.now(UTC))
    assert view["retry"]["code"] == "openrouter_in_flight_budget"
    assert "Nothing to do" in view["retry"]["body"]
    assert view["events"][0]["title"] == "Retried" and "Waiting 6 s" in view["events"][0]["text"]
    ob.clear_retry(cfg, rid)
    view = ob.view_from(cfg, raw, ob._settings(cfg), NOW)
    assert view["retry"] is None
    assert "then the call went through" in view["events"][0]["text"]


def test_another_calls_answer_leaves_a_waiting_retry_alone(cfg):
    """Several calls run at once: B landing doesn't close A's wait, and A failing says so."""
    a = ob.note_retry(cfg, seconds=6, status=402)
    b = ob.note_retry(cfg, seconds=4, status=402)
    ob.clear_retry(cfg, b)
    settings = ob._settings(cfg)
    by_id = {e["id"]: e for e in settings["events"]}
    assert by_id[a]["pending"] and not by_id[b]["pending"]
    ob.clear_retry(cfg, a, ok=False)
    text = {e["id"]: ob.event_text(e) for e in ob._settings(cfg)["events"]}
    assert "then the call failed" in text[a] and "went through" in text[b]
    # the same call waiting twice is one line
    c = ob.note_retry(cfg, seconds=2, status=402)
    ob.note_retry(cfg, seconds=3, status=402, rid=c)
    assert sum(e.get("id") == c for e in ob._settings(cfg)["events"]) == 1


def test_a_stop_holds_for_ten_minutes_even_when_the_cached_figure_shows_room(cfg):
    """OpenRouter's numbers can be five minutes old: a refusal just now outranks them, and
    only an edit from the War Room (or ten minutes and real room) clears it."""
    ob.note_stop(cfg, status=402, code="openrouter_key_limit", show="kino", stage="score")
    raw = {"key": KEY, "listed": LISTED, "credits": CREDITS, "can_edit": True,
           "management_configured": True, "secrets": []}
    view = ob._finish(cfg, raw)
    assert view["level"] == "stopped" and view["header"]["health"] == "stopped"
    assert ob._settings(cfg)["stop"]  # not erased by the poll
    ob._write(cfg, lambda d: d["events"].append({"ts": ob.now_iso(), "kind": "changed", "limit": 90}))
    view = ob._finish(cfg, raw)
    assert view["level"] != "stopped" and not ob._settings(cfg)["stop"]


def test_a_credits_stop_is_named_for_the_account(cfg):
    ob.note_stop(cfg, status=402, code="openrouter_credits")
    raw = {"key": KEY, "listed": LISTED, "credits": CREDITS, "can_edit": True,
           "management_configured": True, "secrets": []}
    view = ob.view_from(cfg, raw, ob._settings(cfg), datetime.now(UTC))
    assert view["level"] == "stopped" and "out of credits" in view["banner"]["title"]


def test_a_single_listed_key_with_another_label_is_not_the_clippa_key():
    """A PATCH goes to the matched row's hash, so a list of one other key must not match."""
    other = {"name": "other", "hash": "h-other", "label": "sk-or-v1-bbbb...yyy"}
    assert ob._match_listed("sk-or-v1-aaaa...zzz", [other]) is None
    assert ob._match_listed("", [other]) is None
    assert ob._match_listed("sk-or-v1-bbbb…yyy", [other]) is other


def test_a_nan_limit_is_a_clear_error():
    with pytest.raises(ClipperError, match="number of dollars"):
        ob.parse_limit(float("nan"))


def test_limit_preview_and_confirm_patches_and_records(cfg, monkeypatch):
    seen = _route(monkeypatch, _live(monkeypatch))
    preview = warroom.POST_API["/api/openrouter/limit"](cfg, {"limit": 80, "limit_reset": "weekly"})
    assert "preview" in preview and "level" not in preview
    rows = {r["label"]: r for r in preview["preview"]["rows"]}
    assert rows["Spending limit"]["now"] == "$60.00" and rows["Spending limit"]["after"] == "$80.00"
    assert rows["Resets"]["now"] == "monthly" and rows["Resets"]["after"] == "weekly"
    assert rows["Next reset"]["after"].startswith("Nov 30, 00:00 UTC")
    assert "Nov 29, 4:00 PM PST" in rows["Next reset"]["note"]
    assert rows["Left"]["after"] == "≈ $68.15"  # 80 minus the $11.85 used this week
    assert preview["preview"]["request"] == {
        "method": "PATCH", "path": "/api/v1/keys/{clippa-prod hash}",
        "body": {"limit": 80, "limit_reset": "weekly"}}
    assert HASH not in json.dumps(preview)
    answers = [("PATCH", "/api/v1/keys/", MGMT, R(200, {"data": {**LISTED, "limit": 80, "limit_reset": "weekly"}}))]
    answers += _live(monkeypatch, key={**KEY, "limit": 80, "limit_reset": "weekly", "limit_remaining": 68.15})
    seen = _route(monkeypatch, answers)
    view = ob.apply_limit(cfg, 80, "weekly", confirm=True, now=NOW)
    assert seen[0]["json"] == {"limit": 80, "limit_reset": "weekly"}
    assert HASH in seen[0]["url"] and HASH not in json.dumps(view)
    assert view["key"]["limit_reset"] == "weekly"
    changed = next(e for e in view["events"] if e["kind"] == "changed")
    assert "Limit $60.00 → $80.00, resets weekly" in changed["text"]
    with pytest.raises(ClipperError, match="already the limit"):
        ob.apply_limit(cfg, 80, "weekly", confirm=True, now=NOW)


def test_disable_preview_and_confirm(cfg, monkeypatch):
    _route(monkeypatch, _live(monkeypatch))
    preview = ob.apply_disabled(cfg, True, confirm=False)["preview"]
    assert preview["title"] == "Disable the clippa key?"
    assert preview["callout_tone"] == "bad" and "fails while the key is off" in preview["callout"]
    assert preview["rows"][0]["now"] == "enabled" and preview["rows"][0]["after"] == "disabled"
    assert preview["request"]["body"] == {"disabled": True}
    seen = []
    answers = [("PATCH", "/keys/", MGMT, R(200, {"data": {**LISTED, "disabled": True}}))]
    answers += _live(monkeypatch, listed={**LISTED, "disabled": True},
                     key={**KEY, "disabled": True})
    import requests

    def patch(url, headers, json, timeout):
        seen.append(json)
        return answers.pop(0)[3]
    monkeypatch.setattr(requests, "patch", patch)
    # the confirm's fresh read uses the remaining answers via get
    _route_get(monkeypatch, answers[1:])
    view = ob.apply_disabled(cfg, True, confirm=True)
    assert seen == [{"disabled": True}]
    assert view["level"] == "disabled" and view["header"]["health"] == "stopped"  # every call fails
    assert view["banner"]["title"] == ob.COPY["disabled_title"]
    assert any(e["kind"] == "disabled" for e in view["events"])


def _route_get(monkeypatch, answers):
    import requests

    def get(url, headers, timeout):
        bearer = headers.get("Authorization", "")
        for i, (m, part, who, resp) in enumerate(answers):
            if m == "GET" and part in url and who in bearer:
                answers.pop(i)
                return resp
        raise AssertionError(f"unexpected GET {url}")
    monkeypatch.setattr(requests, "get", get)


def test_a_refused_management_key_stays_read_only(cfg, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", INF)
    monkeypatch.setenv(ob.MGMT_ENV, MGMT)
    answers = [
        ("GET", "/api/v1/key", INF, R(200, {"data": KEY})),
        ("GET", "/credits", INF, R(200, {"data": CREDITS})),
        ("GET", "/api/v1/key", MGMT, R(401, {"error": {"message": "expired"}})),
        ("GET", "/api/v1/keys", MGMT, R(401, {"error": {"message": "API key expired"}})),
    ]
    _route(monkeypatch, answers)
    view = ob.status(cfg, now=NOW)
    assert view["can_edit"] is False and view["key"]["limit_usd"] == 60
    assert "refused the management key" in view["edit_why"]
    with pytest.raises(ClipperError, match="management key"):
        ob.apply_limit(cfg, 10, "daily", confirm=False)


def test_unreachable_openrouter_keeps_the_last_good_answer(cfg, monkeypatch):
    import requests
    _route(monkeypatch, _live(monkeypatch))
    assert ob.status(cfg, now=NOW)["key"]["month_usd"] == 27.30
    ob._CACHE[ob._cache_key(cfg)]["at"] = 0  # the TTL has passed

    def down(url, headers, timeout):
        raise requests.ConnectionError("no route")
    monkeypatch.setattr(requests, "get", down)
    view = ob.status(cfg, fresh=True, now=NOW)
    assert view["key"]["month_usd"] == 27.30 and "ConnectionError" in view["stale"]


def test_a_402_with_retry_after_is_sent_again_and_a_key_limit_stops(cfg, monkeypatch):
    import requests

    from clippa import costs, llm

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    sent, replies = [], []
    monkeypatch.setattr(costs, "llm_call", lambda *a, **k: None)

    def post(url, json, headers, timeout):
        sent.append(url)
        return replies.pop(0)

    monkeypatch.setattr(requests, "post", post)
    slept = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    waiting = R(402, {"error": {"message": "in-flight budget is full"}}, {"Retry-After": "0"})
    waiting.text = '{"error":{"message":"in-flight budget is full"}}'
    ok = R(200, {"id": "r1", "model": "openai/gpt-5-mini",
                 "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.01}})
    replies += [waiting, ok]
    text, _ = llm.LLM(cfg).text_call("openrouter:openai/gpt-5-mini", "sys", "hi", "low")
    assert text == "ok" and len(sent) == 2 and slept == [0]
    saved = jsonstate.read(ob._path(cfg))
    assert saved["events"][0]["kind"] == "retried" and saved["events"][0]["pending"] is False
    assert "retry" not in saved

    refused = R(402, {"error": {"message": "Key limit exceeded"}})
    refused.text = '{"error":{"message":"Key limit exceeded"}}'
    replies.append(refused)
    with pytest.raises(ob.OpenRouterKeyLimit, match="openrouter_key_limit"):
        llm.LLM(cfg).text_call("openrouter:openai/gpt-5-mini", "sys", "hi", "low")
    assert len(sent) == 3  # not sent again
    kinds = [e["kind"] for e in jsonstate.read(ob._path(cfg))["events"]]
    assert "stopped" in kinds


def test_run_budget_card_has_no_dollar_guard():
    assert "$" not in ob.COPY["run_budget"]
    assert "single run" in ob.COPY["run_budget"] and "OpenRouter cap" in ob.COPY["run_budget"]


def test_a_second_refusal_moves_the_stop_time(cfg, monkeypatch):
    """The ten minutes a stop holds count from the latest refusal, not the first."""
    times = iter(["2026-11-23T10:00:00+00:00", "2026-11-23T10:09:00+00:00"])
    monkeypatch.setattr(ob, "now_iso", lambda: next(times))
    monkeypatch.setattr(ob, "_age", lambda iso: 60)
    ob.note_stop(cfg, status=402, code="openrouter_key_limit", stage="score")
    ob.note_stop(cfg, status=402, code="openrouter_key_limit", stage="meta")
    settings = ob._settings(cfg)
    assert settings["stop"]["at"] == "2026-11-23T10:09:00+00:00" and settings["stop"]["stage"] == "meta"
    assert sum(e.get("kind") == "stopped" for e in settings["events"]) == 1


def test_a_limit_edit_does_not_clear_a_credits_stop(cfg):
    ob.note_stop(cfg, status=402, code="openrouter_credits")
    ob._write(cfg, lambda d: d["events"].append({"ts": ob.now_iso(), "kind": "changed", "limit": 90}))
    settings = ob._settings(cfg)
    assert ob._stop_holds(settings["stop"], settings["events"], left=50.0, balance=0.0)


def test_a_text_only_stop_holds_while_either_figure_is_spent():
    old = {"at": "2020-01-01T00:00:00+00:00", "code": "openrouter_key_limit"}
    assert ob._stop_holds(old, [], left=40.0, balance=0.1)    # the account ran out
    assert ob._stop_holds(old, [], left=0.2, balance=30.0)    # the key's cap did
    assert not ob._stop_holds(old, [], left=40.0, balance=30.0)


def test_the_poll_keeps_a_refusal_recorded_after_it_read_the_file(cfg, monkeypatch):
    """_finish judged the old stop recovered; a worker's new refusal lands before the write."""
    old = {"at": "2020-01-01T00:00:00+00:00", "code": "openrouter_key_limit", "status": 402}
    ob._write(cfg, lambda d: d.update(stop=old))
    raw = {"key": KEY, "listed": LISTED, "credits": CREDITS, "can_edit": True,
           "management_configured": True, "secrets": []}
    real = ob._quietly

    def race(cfg_, fn):
        ob._write(cfg_, lambda d: d.update(stop={**old, "at": ob.now_iso()}))   # the new refusal
        real(cfg_, fn)

    monkeypatch.setattr(ob, "_quietly", race)
    ob._finish(cfg, raw)
    assert ob._settings(cfg)["stop"]["at"] != old["at"]


def test_a_finished_wait_hands_the_countdown_to_a_call_still_waiting(cfg):
    a = ob.note_retry(cfg, seconds=120)
    b = ob.note_retry(cfg, seconds=30)
    assert ob._settings(cfg)["retry"]["id"] == b
    ob.clear_retry(cfg, b, ok=True)
    assert ob._settings(cfg)["retry"]["id"] == a     # A is still sleeping: its countdown stays
    ob.clear_retry(cfg, a, ok=True)
    assert not ob._settings(cfg).get("retry")


def test_a_credits_stop_with_no_balance_holds_an_hour(monkeypatch):
    stop = {"at": "2026-11-23T10:00:00+00:00", "code": "openrouter_credits"}
    monkeypatch.setattr(ob, "_age", lambda iso: 1800)
    assert ob._stop_holds(stop, [], left=40.0, balance=None)
    monkeypatch.setattr(ob, "_age", lambda iso: 4000)
    assert not ob._stop_holds(stop, [], left=40.0, balance=None)
