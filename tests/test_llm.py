"""The provider routing in clippa/llm.py (#72), against a fake chat/completions server."""
from __future__ import annotations

import copy

import pytest

from clippa import config, costs, doctor, llm
from clippa.errors import ClipperError


class FakeResponse:
    def __init__(self, data: dict, status: int = 200):
        self.status_code, self.ok, self._data, self.headers = status, status < 400, data, {}
        self.text = str(data)

    def json(self) -> dict:
        return self._data


def answer(content: str, usage: dict | None = None) -> dict:
    return {"id": "r1", "model": "openai/gpt-5-mini",
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 100}}


@pytest.fixture
def posted(monkeypatch):
    """Every request the client sends, answered from `replies` in order."""
    import requests

    sent, replies = [], []
    monkeypatch.setattr(costs, "llm_call", lambda *a, **k: None)

    def post(url, json, headers, timeout):
        sent.append({"url": url, "body": copy.deepcopy(json), "headers": dict(headers)})
        return replies.pop(0)
    monkeypatch.setattr(requests, "post", post)
    return sent, replies


def test_split_knows_openrouter_and_keeps_its_vendor_path():
    assert llm.split("openrouter:openai/gpt-5-mini") == ("openrouter", "openai/gpt-5-mini")
    assert llm.split("claude-haiku-5-5") == ("anthropic", "claude-haiku-5-5")
    assert llm.split("anthropic:claude-haiku-5-5") == ("anthropic", "claude-haiku-5-5")


def _model_strings(node, key=""):
    """Chat-model strings in a config tree: an is_llm value, a claude- name, or a provider prefix."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _model_strings(v, k)
    elif isinstance(node, str) and node and (
            llm.is_llm(key, node) or node.startswith(("claude-", "anthropic:", "openai:", "gemini:",
                                                       "openrouter:", "local:"))):
        yield key, node


def test_every_default_model_string_routes_to_its_provider(defaults):
    """defaults.toml's model strings (bare claude- names, and any provider prefix) each go to
    the provider llm.split names. No network."""
    expect = {"anthropic": ("claude-", "anthropic:"), "openai": ("openai:",), "gemini": ("gemini:",),
              "openrouter": ("openrouter:",), "local": ("local:",)}
    found = list(_model_strings(defaults))
    assert found
    providers = set()
    for key, model in found:
        prov, name = llm.split(model)
        assert prov in expect and model.startswith(expect[prov]), (key, model, prov)
        assert name
        providers.add(prov)
    assert {"anthropic", "openrouter"} <= providers   # the two prefixes defaults.toml uses today


def test_an_all_openrouter_config_loads_and_routes(tmp_path, monkeypatch, posted, defaults):
    """Every stage model swapped to openrouter:<vendor>/<model> still loads, and a call goes
    to the OpenRouter client, not Claude's."""
    lines = []
    for section, sec in defaults.items():
        if not isinstance(sec, dict):
            continue
        keys = [k for k, v in sec.items() if llm.is_llm(k, v)]
        if not keys:
            continue
        lines.append(f"[{section}]")
        for k in keys:
            lines.append(f'{k} = "openrouter:vendor/{section}-{k}"')
    path = tmp_path / "clipper.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cfg = config.load(path, use_local=False)
    models = llm.configured(cfg)
    assert models and all(llm.split(m) == ("openrouter", m.removeprefix("openrouter:")) for m in models.values())
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    sent, replies = posted
    replies.append(FakeResponse(answer("ok")))
    client = llm.LLM(cfg)
    model = cfg["segments"]["model"]
    text, meta = client.text_call(model, "sys", "hi", "low")
    assert text == "ok" and client._client is None
    assert sent[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert sent[0]["body"]["model"] == model.removeprefix("openrouter:")
    assert sent[0]["headers"]["Authorization"] == "Bearer k"
    assert meta["model"].startswith("openrouter:")


def test_openrouter_sends_reasoning_and_charges_its_reported_cost(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    replies.append(FakeResponse(answer("hi", {"prompt_tokens": 1000, "completion_tokens": 100, "cost": 0.0123})))
    client = llm.LLM(defaults)
    text, meta = client.text_call("openrouter:openai/gpt-5-mini", "sys", "hello", "xhigh")
    assert text == "hi" and meta["model"] == "openrouter:openai/gpt-5-mini"
    req = sent[0]
    assert req["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert req["body"]["model"] == "openai/gpt-5-mini"
    assert req["body"]["reasoning"] == {"effort": "high"} and "reasoning_effort" not in req["body"]
    assert req["body"]["usage"] == {"include": True}
    assert req["headers"]["Authorization"] == "Bearer k"
    assert client.cost == pytest.approx(0.0123)  # not in [llm] prices, so the table would say $0


def test_a_model_without_reasoning_is_asked_again_without_it(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    replies += [FakeResponse({"error": "reasoning is not supported for this model"}, 400),
                FakeResponse(answer("ok"))]
    text, _ = llm.LLM(defaults).text_call("openrouter:meta/llama-4", "sys", "hi", "medium")
    assert text == "ok"
    assert "reasoning" in sent[0]["body"] and "reasoning" not in sent[1]["body"]


def test_openai_keeps_reasoning_effort_and_the_price_table(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    defaults["llm"]["prices"]["openai:gpt-5-mini"] = [1.0, 10.0]
    replies.append(FakeResponse(answer('{"a": 1}')))
    client = llm.LLM(defaults)
    out, _ = client.json_call("openai:gpt-5-mini", "sys", "hi",
                              {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}, "low")
    assert out == {"a": 1}
    assert sent[0]["body"]["reasoning_effort"] == "low" and "usage" not in sent[0]["body"]
    assert client.cost == pytest.approx((1000 * 1.0 + 100 * 10.0) / 1e6)


def test_claude_model_finds_a_claude_stage_or_none(defaults):
    assert llm.claude_model(defaults).startswith("claude-")
    for sec in defaults.values():
        if isinstance(sec, dict):
            for k, v in list(sec.items()):
                if llm.is_llm(k, v):
                    sec[k] = "openrouter:openai/gpt-5-mini"
    assert llm.claude_model(defaults) == ""


def test_doctor_names_the_openrouter_key(defaults, monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    defaults["paths"]["openrouter_key"] = tmp_path / "none.txt"
    defaults["scoring"]["model_a"] = "openrouter:openai/gpt-5-mini"
    (status, _, what, _), = doctor.collect(doctor.check_llm_keys, defaults)
    assert status == "FAIL" and "OPENROUTER_API_KEY" in what and "scoring.model_a" in what


def test_an_openrouter_model_is_priced_from_its_list(defaults, monkeypatch):
    monkeypatch.setattr(llm, "_OPENROUTER_PRICES", {"openai/gpt-6-luna": (0.1, 0.5)})
    assert llm.known_price(defaults, "openrouter:openai/gpt-6-luna") == (0.1, 0.5)
    assert llm.estimate(defaults, "openrouter:openai/gpt-6-luna", 1e6, 1e6) == pytest.approx(0.6)
    assert llm.known_price(defaults, "openrouter:nobody/none") is None
    assert llm.known_price(defaults, "openai:gpt-6-luna") is None  # only OpenRouter's own models


def test_doctor_wants_no_price_for_openrouter(defaults):
    defaults["scoring"]["model_a"] = "openrouter:openai/gpt-6-luna"
    (status, _, what, _), = doctor.collect(doctor.check_llm_prices, defaults)
    assert status == "OK", what


def test_a_provider_key_from_env_or_the_secrets_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    paths = {"openrouter_key": tmp_path / "openrouter_secret.txt"}
    assert llm.provider_key("openrouter", paths) == ""
    paths["openrouter_key"].write_text("sk-file\n", encoding="utf-8")
    assert llm.provider_key("openrouter", paths) == "sk-file"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    assert llm.provider_key("openrouter", paths) == "sk-env"


def test_the_client_sends_the_file_key(posted, monkeypatch, defaults, tmp_path):
    sent, replies = posted
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    defaults["paths"]["openrouter_key"] = tmp_path / "k.txt"
    defaults["paths"]["openrouter_key"].write_text("sk-file", encoding="utf-8")
    replies.append(FakeResponse(answer("hi")))
    llm.LLM(defaults).text_call("openrouter:openai/gpt-6-luna", "sys", "hello", "low")
    assert sent[0]["headers"]["Authorization"] == "Bearer sk-file"


def test_preflight_takes_a_reasoning_model_running_out_of_tokens(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    out = answer("")
    out["choices"][0]["finish_reason"] = "length"
    replies.append(FakeResponse(out))
    llm.LLM(defaults).preflight("openrouter:deepseek/deepseek-v4.1-flash")  # no raise
    assert "reasoning" not in sent[0]["body"]


def test_prefixed_claude_models_count_as_claude(defaults):
    for sec in defaults.values():
        if isinstance(sec, dict):
            for k, v in list(sec.items()):
                if llm.is_llm(k, v):
                    sec[k] = "openrouter:openai/gpt-5-mini"
    defaults["segments"]["model"] = "anthropic:claude-opus-5-5"
    assert llm.claude_model(defaults) == "claude-opus-5-5"


def test_doctor_checks_the_openrouter_key_itself(defaults, monkeypatch):
    import requests

    monkeypatch.setenv("OPENROUTER_API_KEY", "bad")
    defaults["scoring"]["model_a"] = "openrouter:openai/gpt-5-mini"
    got = []

    def get(url, timeout, headers):
        got.append(url)
        return FakeResponse({"data": [{"id": "openai/gpt-5-mini"}]} if url.endswith("/models") else {}, 
                            200 if url.endswith("/models") else 401)
    monkeypatch.setattr(requests, "get", get)
    results = doctor.collect(doctor.check_llm_online, defaults)
    assert any(r[0] == "FAIL" and "refused the key" in r[2] for r in results)
    assert got[-1].endswith("/key")


def test_effort_none_turns_reasoning_off(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    replies += [FakeResponse(answer("a")), FakeResponse(answer("b"))]
    client = llm.LLM(defaults)
    client.text_call("openrouter:deepseek/deepseek-v4.1-flash", "sys", "hi", "none")
    client.text_call("openai:gpt-6-luna", "sys", "hi", "none")
    assert sent[0]["body"]["reasoning"] == {"effort": "none"}
    assert sent[1]["body"]["reasoning_effort"] == "none"
    assert llm.claude_effort("none") == "low" and llm.claude_effort("high") == "high"


def test_reasoning_gets_room_to_think(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    replies += [FakeResponse(answer("a")), FakeResponse(answer("b"))]
    client = llm.LLM(defaults)
    client.text_call("openrouter:deepseek/deepseek-v4-pro-0813", "sys", "hi", "high", max_tokens=4000)
    client.text_call("openrouter:deepseek/deepseek-v4-pro-0813", "sys", "hi", "none", max_tokens=4000)
    assert sent[0]["body"]["max_tokens"] == defaults["llm"]["thinking_max_tokens"]
    assert sent[1]["body"]["max_tokens"] == 4000


def test_room_to_think_stays_within_what_the_model_writes(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setitem(llm._OPENROUTER_MAX_OUT, "openai/gpt-4.1", 32768)
    replies.append(FakeResponse(answer("a")))
    llm.LLM(defaults).text_call("openrouter:openai/gpt-4.1", "sys", "hi", "medium", max_tokens=4000)
    assert sent[0]["body"]["max_tokens"] == 32768


def test_a_model_without_reasoning_gets_back_the_tokens_asked_for(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    replies += [FakeResponse({"error": "reasoning_effort is not supported"}, 400), FakeResponse(answer("a")),
                FakeResponse(answer("b"))]
    client = llm.LLM(defaults)
    client.text_call("openai:gpt-4.1", "sys", "hi", "medium", max_tokens=4000)
    client.text_call("openai:gpt-4.1", "sys", "hi", "medium", max_tokens=4000)
    assert sent[0]["body"]["max_completion_tokens"] == defaults["llm"]["thinking_max_tokens"]
    assert sent[1]["body"]["max_completion_tokens"] == 4000 and "reasoning_effort" not in sent[1]["body"]
    assert sent[2]["body"]["max_completion_tokens"] == 4000  # remembered: no reasoning, no extra room


def test_too_many_output_tokens_falls_back_to_what_was_asked(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    replies += [FakeResponse({"error": "max_completion_tokens is too large: 64000 > 32768"}, 400),
                FakeResponse(answer("a"))]
    text, _ = llm.LLM(defaults).text_call("openai:o5-mini", "sys", "hi", "medium", max_tokens=4000)
    assert text == "a" and sent[1]["body"]["max_completion_tokens"] == 4000
    assert sent[1]["body"]["reasoning_effort"] == "medium"


def test_the_anthropic_key_from_a_file_when_the_env_has_none(tmp_path, monkeypatch, defaults):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    key = tmp_path / "anthropic_secret.txt"
    key.write_text("sk-ant-file\n", encoding="utf-8")
    defaults["paths"]["anthropic_key"] = key
    assert llm.provider_key("anthropic", defaults["paths"]) == "sk-ant-file"
    assert llm.LLM(defaults).client.api_key == "sk-ant-file"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert llm.provider_key("anthropic", defaults["paths"]) == "sk-ant-env"


def test_prices_match_the_longest_listed_prefix():
    prices = {"claude-sonnet-5-5": [2.0, 10.0], "claude-sonnet-5-5-fast": [4.0, 20.0], "gpt-5": [1.0, 2.0]}
    assert llm.price_of(prices, "claude-sonnet-5-5") == (2.0, 10.0)
    assert llm.price_of(prices, "claude-sonnet-5-5-20260601") == (2.0, 10.0)
    assert llm.price_of(prices, "claude-sonnet-5-5-fast-1") == (4.0, 20.0)
    assert llm.price_of(prices, "openai:gpt-5-mini") == (1.0, 2.0)
    assert llm.price_of(prices, "claude-opus-5-5") is None
    assert llm.price_of({"openai:gpt-5-mini": [3.0, 4.0]}, "openai:gpt-5-mini-20260601") == (3.0, 4.0)


def test_a_401_is_a_rejected_key_not_a_retry(posted, monkeypatch, defaults):
    """A provider's 401 raises KeyRejected (exit KEY_REJECTED, which the worker holds a
    stream on) after one request; other errors stay RuntimeError."""
    from clippa.errors import KEY_REJECTED, KeyRejected

    sent, replies = posted
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    replies.append(FakeResponse({"error": {"message": "No auth credentials found"}}, status=401))
    with pytest.raises(KeyRejected, match=r"OPENROUTER_API_KEY \(or \[paths\] openrouter_key\) was rejected") as e:
        llm.LLM(defaults).preflight("openrouter:openai/gpt-5-mini")
    assert e.value.status == KEY_REJECTED and len(sent) == 1
    replies.append(FakeResponse({"error": {"message": "flagged"}}, status=403))
    with pytest.raises(ClipperError) as e:
        llm.LLM(defaults).preflight("openrouter:openai/gpt-5-mini")
    assert not isinstance(e.value, KeyRejected)


def test_the_keys_fingerprint_changes_with_a_key_and_hides_it(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-one")
    a = llm.keys_fingerprint({})
    assert "sk-one" not in a and len(a) == 12
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-two")
    assert llm.keys_fingerprint({}) != a
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / "openai.txt").write_text("from-a-file")
    b = llm.keys_fingerprint({})
    assert llm.keys_fingerprint({"openai_key": tmp_path / "openai.txt"}) != b


def test_geminis_400_for_a_bad_key_is_a_rejected_key(posted, monkeypatch, defaults):
    from clippa.errors import KeyRejected

    sent, replies = posted
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    replies.append(FakeResponse({"error": {"message": "API key not valid. Please pass a valid API key."}}, status=400))
    with pytest.raises(KeyRejected, match="GEMINI_API_KEY"):
        llm.LLM(defaults).preflight("gemini:gemini-3-flash")


def test_a_key_file_that_cant_be_read_raises_rather_than_seeming_to_change(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / "openai_dir").mkdir()  # there, but reading it fails
    with pytest.raises(OSError):
        llm.keys_fingerprint({"openai_key": tmp_path / "openai_dir"})


def test_cli_exits_key_rejected_on_a_claude_auth_error_past_the_preflight(monkeypatch, capsys):
    from clippa import cli
    from clippa.errors import KEY_REJECTED

    auth = type("AuthenticationError", (Exception,), {"__module__": "anthropic._exceptions"})
    monkeypatch.setattr(cli, "run_command", lambda args: (_ for _ in ()).throw(auth("invalid x-api-key")))
    monkeypatch.setattr("sys.argv", ["clipper", "status"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == KEY_REJECTED and "rejected the key" in capsys.readouterr().err
    monkeypatch.setattr(cli, "run_command", lambda args: (_ for _ in ()).throw(KeyError("other")))
    with pytest.raises(KeyError):
        cli.main()
