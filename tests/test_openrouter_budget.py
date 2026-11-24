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
    assert ob.classify_payment(402, "This request requires more credits", None) == "openrouter_key_limit"
    assert ob.classify_payment(402, "", None) == "openrouter_key_limit"
    assert ob.classify_payment(403, "Key limit exceeded. Manage it", None) == "openrouter_key_limit"
    assert ob.classify_payment(403, "not allowed to use this model", None) is None
    assert ob.classify_payment(401, "nope", None) is None
    assert ob.classify_payment(402, "in-flight budget", "500") == "openrouter_key_limit"
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
    ob.note_retry(cfg, seconds=6, status=402)
    raw = {"key": KEY, "listed": LISTED, "credits": CREDITS, "can_edit": True,
           "management_configured": True, "secrets": []}
    view = ob.view_from(cfg, raw, ob._settings(cfg), datetime.now(UTC))
    assert view["retry"]["code"] == "openrouter_in_flight_budget"
    assert "Nothing to do" in view["retry"]["body"]
    assert view["events"][0]["title"] == "Retried" and "Waiting 6 s" in view["events"][0]["text"]
    ob.clear_retry(cfg)
    view = ob.view_from(cfg, raw, ob._settings(cfg), NOW)
    assert view["retry"] is None
    assert "then the call went through" in view["events"][0]["text"]


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
    assert view["level"] == "disabled" and view["header"]["health"] is None
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
