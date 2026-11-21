"""#286 pass 1: every machine-facing "clipper" name also answers to "clippa", the new one
first, while clipper keeps writing the old ones (old Discord buttons and Kitsu notes stay live)."""
from pathlib import Path

import pytest

from clipper import alerts, config, review


def test_env_names_and_the_new_spelling_wins(monkeypatch):
    assert config.env_names("CLIPPER_CONFIG") == ["CLIPPA_CONFIG", "CLIPPER_CONFIG"]
    assert config.env_names("CLIPPA_DATA") == ["CLIPPA_DATA", "CLIPPER_DATA"]
    assert config.env_names("HF_TOKEN") == ["HF_TOKEN"]
    assert config.getenv("CLIPPER_X", "d") == "d"
    monkeypatch.setenv("CLIPPER_X", "old")
    assert config.getenv("CLIPPER_X") == "old" and config.getenv("CLIPPA_X") == "old"
    monkeypatch.setenv("CLIPPA_X", "new")
    assert config.env_lookup("CLIPPER_X") == ("CLIPPA_X", "new")
    assert config.old_env_in_use() == ["CLIPPER_X"]


def test_settings_overrides_take_either_prefix(tmp_path, monkeypatch):
    path = tmp_path / "clipper.toml"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("CLIPPER_CUT_CRF", "22")
    assert config.load(path, use_local=False)["cut"]["crf"] == 22
    monkeypatch.setenv("CLIPPA_CUT_CRF", "24")
    cfg = config.load(path, use_local=False)
    assert cfg["cut"]["crf"] == 24
    assert config.explain(cfg, "cut.crf")["layers"][-1] == ("$CLIPPA_CUT_CRF", "24")
    assert ("environment", "cut.crf", config.DEFAULTS["cut"]["crf"], 24) in config.diff(cfg)
    assert config.problems(cfg) == []


def test_problems_names_a_stray_variable_in_either_spelling(tmp_path, monkeypatch):
    path = tmp_path / "clipper.toml"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("CLIPPA_CUT_BAKEND", "x")
    monkeypatch.setenv("CLIPPA_DATA", "/volume1/docker/clippa")   # a special name, new spelling
    monkeypatch.setenv("CLIPPER_REPO", "https://example.invalid/r")
    assert config.problems(config.load(path, use_local=False)) == [
        "environment variable CLIPPA_CUT_BAKEND matches no setting"]


def test_find_takes_the_new_file_and_variable_names(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "user_env", lambda name: "")
    assert config.find() == Path("clipper.toml")   # nothing anywhere: the old name, as before
    (tmp_path / "clipper.toml").write_text("")
    assert config.find() == Path("clipper.toml")
    (tmp_path / "clippa.toml").write_text("")
    assert config.find() == Path("clippa.toml")    # both here: the new one
    monkeypatch.setenv("CLIPPER_CONFIG", "old.toml")
    assert config.find() == Path("old.toml")
    monkeypatch.setenv("CLIPPA_CONFIG", "new.toml")
    assert config.find() == Path("new.toml")


def test_the_local_file_beside_the_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "user_env", lambda name: "")
    old = tmp_path / "clipper.toml"
    old.write_text("")
    assert config.local_path(old) == tmp_path.resolve() / "clipper.local.toml"
    new = tmp_path / "clippa.toml"
    new.write_text("")
    assert config.local_path(new) == tmp_path.resolve() / "clippa.local.toml"
    (tmp_path / "clipper.local.toml").write_text("")
    assert config.local_path(new) == tmp_path.resolve() / "clipper.local.toml"   # the one that's there
    (tmp_path / "clippa.local.toml").write_text("")
    assert config.local_path(old) == tmp_path.resolve() / "clippa.local.toml"    # both: the new one
    monkeypatch.setenv("CLIPPA_LOCAL_CONFIG", str(tmp_path / "x.toml"))
    assert config.local_path(old) == tmp_path / "x.toml"


def test_a_clippa_toml_loads_with_its_local_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "user_env", lambda name: "")
    (tmp_path / "clippa.toml").write_text('[cut]\nbackend = "nas"\n', encoding="utf-8")
    (tmp_path / "clippa.local.toml").write_text('[cut]\nbackend = "local"\n', encoding="utf-8")
    assert config.load(tmp_path / "clippa.toml")["cut"]["backend"] == "local"


def test_the_alert_webhook_in_either_spelling(defaults, monkeypatch):
    monkeypatch.setenv("CLIPPER_ALERT_WEBHOOK", "https://old.invalid/hook")
    assert alerts.Alerts(defaults).webhook == "https://old.invalid/hook"
    monkeypatch.setenv("CLIPPA_ALERT_WEBHOOK", "https://new.invalid/hook")
    assert alerts.Alerts(defaults).webhook == "https://new.invalid/hook"


def test_bot_notes_in_either_prefix_are_not_reviewer_comments():
    task = {"task_status_id": "S"}
    comments = [{"id": "1", "text": "[clippa] Undo: back to wfa", "task_status_id": "S", "created_at": "3"},
                {"id": "2", "text": "[clipper] Retake applied", "task_status_id": "S", "created_at": "2"},
                {"id": "3", "text": "trim the end", "task_status_id": "S", "created_at": "1"}]
    gazu = type("G", (), {"task": type("T", (), {"all_comments_for_task": staticmethod(lambda t: comments)})})
    assert review.decision_comment_entry(gazu, task) == ("3", "trim the end")
    assert review.NOTE_PREFIX == "[clipper]"   # still written with the old one until pass 2


def test_card_buttons_in_either_prefix():
    pytest.importorskip("discord")
    from clipper import bot

    pat = bot.CardButton.__discord_ui_compiled_template__
    for cid in ("clipper:approve:kick-1/c01", "clippa:approve:kick-1/c01"):
        m = pat.fullmatch(cid)
        assert m and (m["action"], m["ref"]) == ("approve", "kick-1/c01")
    assert pat.fullmatch("clippy:approve:x") is None
