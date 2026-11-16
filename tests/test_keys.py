"""clipper/keys.py and the war room's Keys window: where each key comes from, the provider's
answer, the streams held for a rejected key, and never a value in what comes back."""
import json

import pytest

from clipper import jsonstate, keys, warroom
from clipper.errors import ClipperError

SECRET = "sk-test-do-not-show-0123456789"


@pytest.fixture
def cfg(defaults, tmp_path, monkeypatch):
    for k in keys.specs(defaults):
        monkeypatch.delenv(k.env, raising=False)
    streams = tmp_path / "streams"
    streams.mkdir()
    return {**defaults, "paths": {**defaults["paths"], "streams": streams,
                                  "openai_key": tmp_path / "openai.txt"}}


class Answer:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


def test_classify_reads_a_providers_answer():
    assert keys.classify(200, "")[0] is True
    assert keys.classify(401, "invalid x-api-key") == (False, "rejected (HTTP 401)")
    assert keys.classify(403, "")[0] is False
    assert keys.classify(400, "API key not valid. Please pass a valid API key.")[0] is False
    assert keys.classify(400, "bad request")[0] is None
    assert keys.classify(503, "")[0] is None


def test_a_key_comes_from_its_env_var_before_its_file(cfg, monkeypatch):
    openai = next(k for k in keys.specs(cfg) if k.name == "OpenAI")
    assert keys.value(cfg, openai) == ("", "")
    cfg["paths"]["openai_key"].write_text(SECRET + "\n")
    assert keys.value(cfg, openai) == (SECRET, "file")
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert keys.value(cfg, openai) == ("from-env", "env")


def test_status_checks_each_set_key_once_and_never_returns_a_value(cfg, monkeypatch):
    import requests

    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("BRAVE_API_KEY", SECRET)          # set, but never asked
    asked = []

    def get(url, headers, timeout):
        asked.append((url, headers))
        return Answer(401, "invalid x-api-key")
    monkeypatch.setattr(requests, "get", get)
    d = cfg["paths"]["streams"] / "kick-1"
    d.mkdir()
    jsonstate.write(d / "run.json", {"status": "failed", "key_rejected": "abc", "error": "score exited 4: x"})

    report = warroom.keys_check(cfg, {})
    assert [u for u, _ in asked] == ["https://api.anthropic.com/v1/models?limit=1"]   # only the set, checkable one
    assert asked[0][1]["x-api-key"] == SECRET                                          # sent to its provider...
    assert SECRET not in json.dumps(report)                                            # ...and nowhere else
    row = {r["name"]: r for r in report["keys"]}
    assert row["Anthropic"]["source"] == "$ANTHROPIC_API_KEY" and row["Anthropic"]["last"]["ok"] is False
    assert row["Brave"]["set"] and not row["Brave"]["checkable"] and row["Brave"]["last"]["note"].startswith("not checked")
    assert not row["OpenAI"]["set"] and row["OpenAI"]["last"]["note"] == "not set"
    assert report["held"][0]["stream"] == "kick-1"
    saved = jsonstate.read_or(keys.store(cfg), {})
    import socket
    assert saved[socket.gethostname()]["Anthropic"]["ok"] is False and SECRET not in json.dumps(saved)
    # The window opens on the last answers, without asking again.
    asked.clear()
    assert {r["name"]: r for r in warroom.keys_get(cfg)["keys"]}["Anthropic"]["last"]["ok"] is False
    assert asked == []


def test_one_key_can_be_checked_alone_and_a_wrong_name_is_refused(cfg, monkeypatch):
    import requests

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "b")
    monkeypatch.setattr(requests, "get", lambda url, headers, timeout: Answer(200))
    r = {x["name"]: x for x in warroom.keys_check(cfg, {"name": "OpenAI"})["keys"]}
    assert r["OpenAI"]["last"]["ok"] is True and r["Anthropic"]["last"] is None
    with pytest.raises(ClipperError):
        warroom.keys_check(cfg, {"name": "Nope"})
    assert "/api/keys" in warroom.GET_API and "/api/keys/check" in warroom.POST_API


def test_an_unreachable_provider_is_no_answer_not_a_rejection(cfg, monkeypatch):
    import requests

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")

    def down(url, headers, timeout):
        raise requests.ConnectionError("no route")
    monkeypatch.setattr(requests, "get", down)
    anth = next(k for k in keys.specs(cfg) if k.name == "Anthropic")
    got = keys.check(cfg, anth)
    assert got["ok"] is None and "couldn't reach" in got["note"]


def test_an_answer_about_a_replaced_key_isnt_shown_for_the_new_one(cfg, monkeypatch):
    import requests

    monkeypatch.setenv("ANTHROPIC_API_KEY", "old")
    monkeypatch.setattr(requests, "get", lambda url, headers, timeout: Answer(401, "invalid x-api-key"))
    warroom.keys_check(cfg, {"name": "Anthropic"})
    row = lambda: {r["name"]: r for r in warroom.keys_get(cfg)["keys"]}["Anthropic"]
    assert row()["last"]["ok"] is False and "of" not in row()["last"]   # the tag stays on the server
    monkeypatch.setenv("ANTHROPIC_API_KEY", "new")                       # redeployed with a fixed key
    assert row()["last"] is None


def test_a_scoped_key_without_the_permission_isnt_called_rejected():
    ok, note = keys.classify(401, '{"detail": {"status": "missing_permissions", "message": "lacks permission models_read"}}')
    assert ok is None and "may still work" in note


def test_a_malformed_key_never_reaches_the_note(cfg, monkeypatch):
    """Copilot on #281: requests' InvalidHeader (a ValueError too) quotes the header value."""
    import requests

    bad = SECRET + "\nX"
    monkeypatch.setenv("ANTHROPIC_API_KEY", bad)

    def invalid(url, headers, timeout):
        raise requests.exceptions.InvalidHeader(f"Invalid leading whitespace in header value {headers['x-api-key']!r}")
    monkeypatch.setattr(requests, "get", invalid)
    report = warroom.keys_check(cfg, {"name": "Anthropic"})
    assert SECRET not in json.dumps(report) and SECRET not in keys.store(cfg).read_text()


def test_a_not_set_answer_goes_once_the_key_is_there(cfg, monkeypatch):
    warroom.keys_check(cfg, {"name": "OpenAI"})                 # not set: answered "not set"
    cfg["paths"]["openai_key"].write_text("now-there")
    assert {r["name"]: r for r in warroom.keys_get(cfg)["keys"]}["OpenAI"]["last"] is None


def test_checking_one_key_keeps_the_others_saved_answers(cfg, monkeypatch):
    import requests

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "b")
    monkeypatch.setattr(requests, "get", lambda url, headers, timeout: Answer(200))
    warroom.keys_check(cfg, {"name": "Anthropic"})
    warroom.keys_check(cfg, {"name": "OpenAI"})
    rows = {r["name"]: r for r in warroom.keys_get(cfg)["keys"]}
    assert rows["Anthropic"]["last"]["ok"] is True and rows["OpenAI"]["last"]["ok"] is True
