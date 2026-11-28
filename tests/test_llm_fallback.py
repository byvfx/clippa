"""Anthropic out of credits (#454): OutOfCredits, or [llm] fallback = "openrouter" sends the call to
OpenRouter's copy of the model. Offline: a fake Anthropic client and a fake chat/completions server."""
from __future__ import annotations

import copy
import time
from types import SimpleNamespace

import anthropic
import pytest

from clippa import config, costs, doctor, jsonstate, llm, worker
from clippa.errors import (
    OUT_OF_CREDITS,
    ClipperError,
    KeyRejected,
    OpenRouterLimit,
    OutOfCredits,
    stops_run,
)

LISTED = {"anthropic/claude-haiku-5.5": (0.1, 0.5), "anthropic/claude-sonnet-5.5": (2.0, 10.0)}
CREDITS = "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing."


def api_error(cls, status: int, message: str):
    response = SimpleNamespace(status_code=status, headers={}, request=None)
    return cls(message, response=response, body={"error": {"type": "invalid_request_error", "message": message}})


def billing() -> anthropic.BadRequestError:
    return api_error(anthropic.BadRequestError, 400, CREDITS)


class FakeResponse:
    def __init__(self, data: dict, status: int = 200):
        self.status_code, self.ok, self._data, self.headers, self.text = status, status < 400, data, {}, str(data)

    def json(self) -> dict:
        return self._data


def answer(content: str) -> FakeResponse:
    return FakeResponse({"id": "r1", "model": "anthropic/claude-haiku-5.5",
                         "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.001}})


class FakeAnthropic:
    """client.messages.create / .stream that raise `error` (or answer "OK" when it is None)."""

    def __init__(self, error=None):
        self.error, self.calls = error, 0
        self.messages = self
        self.beta = SimpleNamespace(messages=self)

    def create(self, **kw):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(model=kw["model"], stop_reason="end_turn", stop_details=None,
                               usage=SimpleNamespace(input_tokens=5, output_tokens=1),
                               content=[SimpleNamespace(type="text", text='{"ok": true}')])

    def stream(self, **kw):
        self.calls += 1
        raise self.error


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """No earlier refusal in this process, OpenRouter's list as LISTED, costs not written."""
    monkeypatch.setitem(llm._CREDITS, "out_at", 0.0)
    monkeypatch.setitem(llm._CREDITS, "said", False)
    monkeypatch.setattr(llm, "openrouter_prices", lambda: LISTED)
    monkeypatch.setattr(costs, "llm_call", lambda *a, **k: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")


@pytest.fixture
def posted(monkeypatch):
    import requests

    sent, replies = [], []

    def post(url, json, headers, timeout):
        sent.append({"url": url, "body": copy.deepcopy(json)})
        return replies.pop(0)
    monkeypatch.setattr(requests, "post", post)
    return sent, replies


def client(defaults, fallback: str, error=None) -> tuple[llm.LLM, FakeAnthropic]:
    cfg = {**defaults, "llm": {**defaults["llm"], "fallback": fallback}}
    c = llm.LLM(cfg)
    c._client = FakeAnthropic(error)
    return c, c._client


def test_openrouter_name_maps_a_claude_model_and_checks_the_list(monkeypatch):
    assert llm.openrouter_name("claude-haiku-5-5") == "anthropic/claude-haiku-5.5"
    assert llm.openrouter_name("anthropic:claude-sonnet-5-5-20260601") == "anthropic/claude-sonnet-5.5"
    assert llm.openrouter_name("claude-opus-5-5") is None          # not in the list
    assert llm.openrouter_name("claude-instant") is None           # another shape
    assert llm.openrouter_name("openrouter:anthropic/claude-haiku-5.5") is None  # not an Anthropic name
    monkeypatch.setattr(llm, "openrouter_prices", lambda: {})       # the list couldn't be fetched
    assert llm.openrouter_name("claude-opus-5-5") == "anthropic/claude-opus-5.5"


def test_only_a_billing_refusal_counts():
    assert llm.billing_refusal(billing())
    assert llm.billing_refusal(api_error(anthropic.APIStatusError, 402, "payment required"))
    assert llm.billing_refusal(api_error(anthropic.PermissionDeniedError, 403, "Billing is not set up"))
    assert not llm.billing_refusal(api_error(anthropic.BadRequestError, 400, "max_tokens: too large"))
    assert not llm.billing_refusal(api_error(anthropic.AuthenticationError, 401, "invalid x-api-key"))
    assert not llm.billing_refusal(RuntimeError(CREDITS))


def test_fallback_off_raises_out_of_credits(defaults):
    c, fake = client(defaults, "", billing())
    with pytest.raises(OutOfCredits) as e:
        c.json_call("claude-haiku-5-5", "sys", "hi", {"type": "object"}, "low")
    assert e.value.status == OUT_OF_CREDITS and "fallback is off" in str(e.value)
    assert stops_run(e.value) and isinstance(e.value, ClipperError)
    with pytest.raises(OutOfCredits):
        c.preflight("claude-haiku-5-5")


def test_fallback_resends_through_openrouter_and_later_calls_skip_anthropic(defaults, posted, capsys, caplog):
    sent, replies = posted
    c, fake = client(defaults, "openrouter", billing())
    replies += [answer('{"ok": true}'), answer("fine")]
    out, meta = c.json_call("claude-haiku-5-5", "sys", "hi", {"type": "object"}, "low", fallback=True)
    assert out == {"ok": True} and meta["model"].startswith("openrouter:")
    assert sent[0]["url"].startswith("https://openrouter.ai/") and sent[0]["body"]["model"] == "anthropic/claude-haiku-5.5"
    assert "betas" not in sent[0]["body"] and sent[0]["body"]["reasoning"] == {"effort": "low"}
    text, _ = c.text_call("claude-haiku-5-5", "sys", "hi", "low")   # straight to OpenRouter
    assert text == "fine" and fake.calls == 1
    said = capsys.readouterr().out + caplog.text  # printed by a stage, logged by a service
    assert said.count("Anthropic is out of credits") == 1  # once per process


def test_the_switch_holds_for_every_client_in_the_process(defaults, posted):
    sent, replies = posted
    first, _ = client(defaults, "openrouter", billing())
    replies += [answer("OK"), answer("OK")]
    first.preflight("claude-haiku-5-5")
    second, fake = client(defaults, "openrouter", billing())
    second.preflight("claude-sonnet-5-5")
    assert fake.calls == 0 and sent[1]["body"]["model"] == "anthropic/claude-sonnet-5.5"


def test_anthropic_is_tried_again_after_an_hour(defaults, monkeypatch):
    monkeypatch.setitem(llm._CREDITS, "out_at", time.monotonic() - llm._RECHECK_S - 1)
    c, fake = client(defaults, "openrouter")
    out, meta = c.json_call("claude-haiku-5-5", "sys", "hi", {"type": "object"}, "low")
    assert out == {"ok": True} and fake.calls == 1 and not meta["model"].startswith("openrouter:")


def test_an_auth_error_or_other_bad_request_never_falls_back(defaults, posted):
    c, _ = client(defaults, "openrouter", api_error(anthropic.AuthenticationError, 401, "invalid x-api-key"))
    with pytest.raises(KeyRejected):
        c.preflight("claude-haiku-5-5")
    c, _ = client(defaults, "openrouter", api_error(anthropic.BadRequestError, 400, "messages: empty"))
    with pytest.raises(anthropic.BadRequestError):
        c.text_call("claude-haiku-5-5", "sys", "hi", "low")
    assert posted[0] == [] and llm._CREDITS["out_at"] == 0.0


def test_a_stream_call_falls_back_too(defaults, posted):
    sent, replies = posted
    c, _ = client(defaults, "openrouter", billing())
    replies.append(answer('{"a": 1}'))
    out, _ = c.json_text("claude-sonnet-5-5", "sys", "hi", "high")
    assert out == {"a": 1} and sent[0]["body"]["model"] == "anthropic/claude-sonnet-5.5"


def test_no_fallback_without_an_openrouter_key_or_a_listed_model(defaults, monkeypatch):
    c, _ = client(defaults, "openrouter", billing())
    with pytest.raises(OutOfCredits, match="doesn't list"):
        c.text_call("claude-opus-5-5", "sys", "hi", "low")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(OutOfCredits, match="OPENROUTER_API_KEY"):
        c.text_call("claude-haiku-5-5", "sys", "hi", "low")
    assert llm._CREDITS["out_at"] == 0.0


def test_stops_run_covers_the_three_run_wide_errors():
    assert stops_run(KeyRejected("x")) and stops_run(OpenRouterLimit("y", "openrouter_credits"))
    assert stops_run(OutOfCredits("z")) and not stops_run(ClipperError("one item"))


def test_fallback_is_a_checked_choice(defaults):
    config.check_choices({**defaults, "llm": {**defaults["llm"], "fallback": "openrouter"}})
    with pytest.raises(ClipperError, match="fallback"):
        config.check_choices({**defaults, "llm": {**defaults["llm"], "fallback": "OpenRouter"}})


def test_doctor_wants_the_openrouter_key_with_the_fallback_on(defaults, monkeypatch):
    said = []
    monkeypatch.setattr(doctor, "ok", lambda area, what, hint="": said.append(("ok", what)))
    need = lambda area, what, hint="": said.append(("need", what))
    cfg = {**defaults, "llm": {**defaults["llm"], "fallback": "openrouter"}}
    doctor.check_llm_fallback(cfg, need)
    assert said[-1][0] == "ok" and "then OpenRouter" in said[-1][1]
    monkeypatch.delenv("OPENROUTER_API_KEY")
    cfg["paths"] = {**cfg["paths"], "openrouter_key": None}
    doctor.check_llm_fallback(cfg, need)
    assert said[-1][0] == "need" and "OPENROUTER_API_KEY" in said[-1][1]


def test_the_worker_holds_a_stream_out_of_credits_without_spending_attempts(tmp_path, defaults, monkeypatch):
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LOCAL_LLM_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "old")
    cfg = {**defaults, "shows": {}, "paths": {**defaults["paths"], "streams": tmp_path}}
    d = tmp_path / "kick-1"
    (d / "live").mkdir(parents=True)
    jsonstate.write(d / "live" / "state.json", {"status": "done", "channel": "x"})

    def refused(cmd, stdout=None, **kw):
        stdout.write("Anthropic is out of credits (claude-haiku-5-5, HTTP 400; [llm] fallback is off)\n")
        return OUT_OF_CREDITS
    monkeypatch.setattr(worker.subprocess, "call", refused)
    monkeypatch.setattr(worker, "keep_awake", lambda on: None)
    for _ in range(cfg["worker"]["max_attempts"] + 1):   # more holds than it has attempts
        assert worker.run_stream(cfg, d, stages=["score"], force=True) is False
    run = jsonstate.read_or(d / "run.json", {})
    assert run["status"] == "failed" and run["attempts"] == 0 and run["out_of_credits"]
    assert worker.pending(cfg) == []                     # held: retry_minutes haven't passed
    run["updated"] = "2000-01-01T00:00:00+00:00"
    jsonstate.write(d / "run.json", run)
    assert worker.pending(cfg) == [d]                    # time to see if it was topped up
    run["updated"] = worker.now_iso()
    jsonstate.write(d / "run.json", run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "new")
    assert worker.pending(cfg) == [d]                    # a key changed: at once

    monkeypatch.setattr(worker.subprocess, "call", lambda cmd, **kw: 0)
    assert worker.run_stream(cfg, d, stages=["score"], force=True)
    assert "out_of_credits" not in jsonstate.read_or(d / "run.json", {})


def test_a_thumbs_pick_out_of_credits_stops_the_rest():
    """Review on #454: thumbs' per-cover catch passes a run-wide stop through, so the stage exits 5
    (held) instead of failing cover by cover and spending the stream's attempts."""
    from clippa.thumbs import stage

    kept = []

    def safe(x):
        if x["id"] == "b":
            raise OutOfCredits("Anthropic is out of credits")
        return x["id"], {"ok": True}
    with pytest.raises(OutOfCredits):
        stage.pick_all([{"id": "a"}, {"id": "b"}], safe, 1, lambda k, e: kept.append(k))
    assert kept == ["a"]   # what finished first is kept (paid for)


def test_the_notice_comes_back_for_a_new_fallback_period(defaults, posted, caplog, monkeypatch):
    sent, replies = posted
    c, fake = client(defaults, "openrouter", billing())
    replies += [answer("one"), answer("two")]
    c.text_call("claude-haiku-5-5", "sys", "hi", "low")
    monkeypatch.setitem(llm._CREDITS, "out_at", time.monotonic() - llm._RECHECK_S - 1)
    c.text_call("claude-haiku-5-5", "sys", "hi", "low")   # Anthropic again, still out: falls back again
    assert fake.calls == 2 and caplog.text.count("Anthropic is out of credits") == 2
