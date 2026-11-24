"""[agent] translation, the headless wrappers, and doctor --role agent.

The shell tests drive deploy/agent/*.sh with a stub `opencode` on PATH. They are skipped
where there is no POSIX shell. Nothing here calls a model or the network.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ROOT

from clippa import agent, config, doctor, keys, setup, usage
from clippa.errors import ClipperError

AGENT = ROOT / "deploy" / "agent"
SHELL = pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="POSIX sh")


def _no_claude(cfg: dict) -> dict:
    """Every direct Claude model becomes an OpenRouter name, so the Anthropic key is unused."""
    for sec in cfg.values():
        if not isinstance(sec, dict):
            continue
        for key, value in list(sec.items()):
            if isinstance(value, str) and value.startswith(("claude-", "anthropic:")):
                sec[key] = "openrouter:vendor/test"
    return cfg


def glob_match(pattern: str, text: str) -> bool:
    """OpenCode's whole-value wildcard: `*` matches anything, including slashes."""
    parts = []
    for ch in pattern:
        parts.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
    return re.fullmatch("".join(parts), text) is not None


def effect(rules: list[dict], action: str, resource: str) -> str | None:
    hit = None
    for rule in rules:
        if glob_match(rule["action"], action) and glob_match(rule["resource"], resource):
            hit = rule["effect"]
    return hit


def test_defaults_translate_for_opencode(defaults):
    checkup = agent.resolve(defaults, "checkup")
    assert checkup["cli"] == "opencode"
    assert checkup["model"] == "openrouter/anthropic/claude-sonnet-5.5"
    assert checkup["effort"] == "" and checkup["max_turns"] == 40 and checkup["timeout_s"] == 900
    audit = agent.resolve(defaults, "audit")
    assert audit["model"] == "openrouter/anthropic/claude-sonnet-5.5#high"
    assert audit["effort"] == "high" and audit["max_turns"] == 80 and audit["timeout_s"] == 1500


def test_env_overrides_the_agent_model(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPA_AGENT_MODEL", "openrouter:anthropic/claude-haiku-5.5")
    monkeypatch.setenv("CLIPPA_AGENT_CHECKUP_EFFORT", "low")
    monkeypatch.setenv("CLIPPA_AGENT_AUDIT_MODEL", "openrouter:anthropic/claude-opus-5.5")
    cfg = config.load(tmp_path / "clipper.toml", use_local=False)
    assert agent.resolve(cfg, "checkup")["model"] == "openrouter/anthropic/claude-haiku-5.5#low"
    assert agent.resolve(cfg, "audit")["model"] == "openrouter/anthropic/claude-opus-5.5#high"


def test_claude_cli_strips_anthropic_and_refuses_openrouter(defaults):
    defaults["agent"] = {**defaults["agent"], "cli": "claude", "model": "anthropic:claude-sonnet-5-5"}
    got = agent.resolve(defaults, "audit")
    assert got["model"] == "claude-sonnet-5-5" and got["effort"] == "high"
    assert "--effort" in " ".join(agent.claude_argv(got, "prompt", "/tmp/fix"))
    defaults["agent"]["model"] = "claude-haiku-5-5"
    assert agent.resolve(defaults, "checkup")["model"] == "claude-haiku-5-5"
    defaults["agent"]["model"] = "openrouter:anthropic/claude-sonnet-5.5"
    with pytest.raises(ClipperError, match="cannot run openrouter:"):
        agent.resolve(defaults, "checkup")


def test_empty_effort_loads_and_a_bad_cli_does_not(tmp_path):
    path = tmp_path / "clipper.toml"
    path.write_text('[agent]\ncheckup_effort = ""\n', encoding="utf-8")
    assert config.load(path, use_local=False)["agent"]["checkup_effort"] == ""
    path.write_text('[agent]\ncli = "nope"\n', encoding="utf-8")
    with pytest.raises(ClipperError, match=r"\[agent\] cli"):
        config.load(path, use_local=False)


def test_opencode_json_has_interactive_agents_and_a_deny_fallback():
    data = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
    for name in ("build", "plan", "explore", "reviewer", "oss-reviewer", "checkup", "audit"):
        assert name in data["agents"]
    for name in ("build", "plan", "explore", "reviewer"):
        assert data["agents"][name]["model"].startswith("openrouter/anthropic/claude-")
    assert data["agents"]["checkup"]["permissions"][0] == {"action": "*", "resource": "*", "effect": "deny"}
    assert data["agents"]["audit"]["permissions"][-1]["action"] == "multiedit"
    assert data["mcp"]["servers"] == {}


def test_permission_order_and_write_boundary():
    fix = "/home/agent/checkup-fix"
    for role, turns in (("checkup", 40), ("audit", 80)):
        doc = agent.opencode_document(role, turns, fix)
        rules = doc["permissions"]
        assert rules[0] == {"action": "*", "resource": "*", "effect": "deny"}
        assert rules == doc["agents"][role]["permissions"]
        assert doc["agents"][role]["steps"] == turns
        assert doc["mcp"] == {"servers": {}}
        assert doc["providers"]["openrouter"]["settings"]["apiKey"] == "{file:/run/secrets/openrouter_key}"
        allows = [i for i, r in enumerate(rules) if r["effect"] == "allow"]
        denies = [i for i, r in enumerate(rules) if r["resource"] in agent.SECRET_RESOURCES]
        assert allows and denies and max(allows) < min(denies)
        # Secret denies and shell metacharacters are after every allow. Checkup's edit allow
        # is before those denies; a secret path still loses because the deny is later.
        assert all(rules.index(r) > max(allows) for r in rules if r["resource"] in agent.SHELL_META)
        assert effect(rules, "read", "/data/streams/kick-1/run.json") == "allow"
        assert effect(rules, "read", "/tmp/checkup.json") == "allow"
        assert effect(rules, "external_directory", "/data/streams/a") == "allow"
        assert effect(rules, "external_directory", "/tmp/x") == "allow"
        assert effect(rules, "read", "/data/secrets/openrouter_secret.txt") == "deny"
        assert effect(rules, "read", "/run/secrets/openrouter_key") == "deny"
        assert effect(rules, "*", "/home/agent/.env") == "deny"
        assert effect(rules, "shell", "checkup-clippa status") == "allow"
        assert effect(rules, "shell", "checkup-clippa alert send --kind checkup hi && rm -rf /") == "deny"
        assert effect(rules, "shell", "checkup-git log --oneline") == "allow"
        for meta in ("&&", "||", ";", "|", ">", "<", "$(", "`", "\n"):
            assert effect(rules, "shell", f"checkup-clippa status {meta} id") == "deny"
    audit = agent.permissions("audit", fix)
    checkup = agent.permissions("checkup", fix)
    assert effect(audit, "edit", f"{fix}/clippa/x.py") == "deny"
    assert effect(audit, "write", "/tmp/pwned") == "deny"
    assert effect(audit, "patch", f"{fix}/a.py") == "deny"
    assert effect(audit, "multiedit", f"{fix}/a.py") == "deny"
    assert effect(audit, "shell", "checkup-pr start slug") == "deny"
    assert effect(checkup, "edit", f"{fix}/clippa/x.py") == "allow"
    assert effect(checkup, "write", f"{fix}/tests/test_x.py") == "allow"
    assert effect(checkup, "edit", "/app/clippa/x.py") == "deny"
    assert effect(checkup, "write", "/data/clipper.toml") == "deny"
    assert effect(checkup, "external_directory", f"{fix}/a.py") == "allow"
    assert effect(audit, "external_directory", f"{fix}/a.py") == "deny"
    assert effect(checkup, "shell", "checkup-pr start slug") == "allow"
    assert any(r["resource"] == "*\n*" for r in audit)


def test_extracts_the_final_plain_text_and_hides_errors():
    raw = "\n".join([
        '{"type":"tool","text":"/run/secrets/openrouter_key"}',
        '{"type":"text","role":"assistant","text":"draft"}',
        '{"type":"text","role":"assistant","text":"Final answer."}',
    ])
    assert agent.extract_assistant(raw) == ("Final answer.", "")
    denied = '{"type":"error","error":{"name":"DeniedError","message":"/run/secrets/openrouter_key"}}'
    text, problem = agent.extract_assistant(denied)
    assert text == "" and problem == "DeniedError"
    assert agent.extract_assistant('{"type":"text","role":"assistant","text":"  "}')[1] == "empty output"
    assert "openrouter_key" not in agent.redact("see /run/secrets/openrouter_key now")


def test_a_prompt_file_must_be_mode_600(tmp_path):
    path = tmp_path / "prompt"
    path.write_text("hello", encoding="utf-8")
    if os.name != "posix":
        assert agent.read_prompt(path) == "hello"
        return
    path.chmod(0o644)
    with pytest.raises(ClipperError, match="600"):
        agent.read_prompt(path)
    path.chmod(0o600)
    assert agent.read_prompt(path) == "hello"


def _agent_ready(defaults, tmp_path, monkeypatch):
    streams = tmp_path / "streams"
    streams.mkdir()
    defaults["paths"]["streams"] = streams
    defaults["paths"]["openrouter_key"] = tmp_path / "openrouter_key"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "opencode"
    stub.write_text("#!/bin/sh\necho 2.4.1-test\n", encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    for name in ("OPENROUTER_API_KEY", "OPENCODE_PASSWORD", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return defaults


def _write_key(cfg, text: str = "fixture-key\n") -> Path:
    path = cfg["paths"]["openrouter_key"]
    path.write_text(text, encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def test_doctor_agent_accepts_a_key_file(defaults, tmp_path, monkeypatch):
    cfg = _agent_ready(defaults, tmp_path, monkeypatch)
    _write_key(cfg)
    lines = doctor.collect(doctor.check_agent, cfg)
    text = " ".join(w for _, _, w, _ in lines)
    assert any(s == "OK" and "readable" in w for s, _, w, _ in lines)
    assert "fixture-key" not in text
    assert not any(s == "FAIL" and "OpenRouter key" in w for s, _, w, _ in lines)


def test_doctor_agent_fails_without_the_key_file(defaults, tmp_path, monkeypatch):
    cfg = _agent_ready(defaults, tmp_path, monkeypatch)
    lines = doctor.collect(doctor.check_agent, cfg)
    assert any(s == "FAIL" and "missing or empty" in w for s, _, w, _ in lines)


def test_doctor_agent_fails_when_a_secret_is_in_the_environment(defaults, tmp_path, monkeypatch):
    cfg = _agent_ready(defaults, tmp_path, monkeypatch)
    _write_key(cfg)
    leaked = "fixture-a"
    for name in ("OPENROUTER_API_KEY", "OPENCODE_PASSWORD", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, leaked)
        lines = doctor.collect(doctor.check_agent, cfg)
        assert any(s == "FAIL" and name in w for s, _, w, _ in lines)
        assert leaked not in " ".join(w for _, _, w, _ in lines)
        monkeypatch.delenv(name)


@pytest.mark.skipif(os.name != "posix", reason="Unix mode bits")
def test_doctor_agent_rejects_a_key_file_that_is_not_mode_600(defaults, tmp_path, monkeypatch):
    cfg = _agent_ready(defaults, tmp_path, monkeypatch)
    path = _write_key(cfg, "fixture-key\n")
    path.chmod(0o644)
    lines = doctor.collect(doctor.check_agent, cfg)
    assert any(s == "FAIL" and "not mode 600" in w for s, _, w, _ in lines)
    assert "fixture-key" not in " ".join(w for _, _, w, _ in lines)


def test_doctor_claude_role_is_the_agent_check(monkeypatch, defaults):
    seen = []
    monkeypatch.setattr(doctor, "check_agent", lambda cfg: seen.append(1))

    def quiet(*_a, **_k):
        return None

    for name in ("check_python", "check_version", "check_command", "check_config", "check_role_settings",
                 "check_prompts", "check_ffmpeg", "check_fonts", "check_photos", "check_faces",
                 "check_review_cams", "check_gpu", "check_keys", "check_youtube_cookies",
                 "check_hei_cookies", "check_twitch_cookies", "check_narrate", "check_broll",
                 "check_clipart", "check_doc"):
        monkeypatch.setattr(doctor, name, quiet)
    monkeypatch.setattr(doctor, "report", lambda results: {"OK": 0, "WARN": 0, "FAIL": 0})
    for role in ("agent", "claude"):
        doctor.main(defaults, offline=True, role=role)
    assert seen == [1, 1]


def test_anthropic_keys_are_skipped_when_nothing_uses_claude(defaults, monkeypatch):
    import requests

    _no_claude(defaults)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_ADMIN_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    defaults["paths"]["anthropic_key"] = defaults["paths"]["people"].parent / "no-anthropic"
    defaults["paths"]["anthropic_admin_key"] = defaults["paths"]["people"].parent / "no-admin"

    def boom(*_a, **_k):
        raise AssertionError("no provider should be asked")

    monkeypatch.setattr(requests, "get", boom)
    report = keys.status(defaults, run_checks=True, remember=False)
    rows = {r["name"]: r for r in report["keys"]}
    for name in ("Anthropic", "Anthropic admin"):
        assert rows[name]["optional"] is True
        assert "skipped" in rows[name]["last"]["note"]
        assert rows[name]["last"]["ok"] is None
    good, note = setup.check_key("ANTHROPIC_API_KEY", defaults)
    assert good and "skipped" in note
    asked = []
    monkeypatch.setattr(setup, "ensure_env_key", lambda *a, **k: asked.append(a[2]))
    setup.step_keys(defaults, {"roles": ["worker"]}, setup.Ask({}))
    assert "ANTHROPIC_API_KEY" not in asked


def test_usage_does_not_call_anthropic_without_an_admin_key(defaults, monkeypatch):
    import requests

    monkeypatch.delenv(usage.ADMIN_ENV, raising=False)
    defaults["paths"]["anthropic_admin_key"] = defaults["paths"]["people"].parent / "no-admin"
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("called")))
    usage._LAST.clear()
    assert usage.claude(defaults) == {"configured": False, "missing": "not configured"}
    usage._LAST.clear()


def _sh(script: Path, args: list[str], stdin: str, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(script), *args], input=stdin, capture_output=True, text=True,
                          env=env, cwd=cwd, check=False)


def _wrapper_env(tmp_path: Path, mode: str) -> tuple[dict, Path]:
    log = tmp_path / "log"
    log.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "timeout").write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$STUB_LOG/timeout\"\n"
        "[ \"$STUB_MODE\" = sleep ] && exit 124\nshift\nexec \"$@\"\n", encoding="utf-8")
    (bindir / "opencode").write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$STUB_LOG/argv\"\n"
        "printf '%s' \"$OPENCODE_CONFIG_CONTENT\" > \"$STUB_LOG/config.json\"\n"
        "env > \"$STUB_LOG/env\"\n"
        "case \"$STUB_MODE\" in\n"
        "text)\n"
        "  printf '%s\\n' '{\"type\":\"tool\",\"text\":\"/run/secrets/openrouter_key\"}'\n"
        "  printf '%s\\n' '{\"type\":\"text\",\"role\":\"assistant\",\"text\":\"draft\"}'\n"
        "  printf '%s\\n' '{\"type\":\"text\",\"role\":\"assistant\",\"text\":\"Final answer.\"}'\n"
        "  ;;\n"
        "deny)\n"
        "  printf '%s\\n' '{\"type\":\"error\",\"error\":{\"name\":\"DeniedError\","
        "\"message\":\"/run/secrets/openrouter_key\"}}'\n"
        "  exit 1 ;;\n"
        "empty) printf '%s\\n' '{\"type\":\"text\",\"role\":\"assistant\",\"text\":\"  \"}' ;;\n"
        "esac\n", encoding="utf-8")
    (bindir / "claude").write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$STUB_LOG/claude\"\nprintf '%s\\n' 'claude said'\n",
        encoding="utf-8")
    for path in bindir.iterdir():
        path.chmod(0o755)
    venv = ROOT / ".venv" / "bin"
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{venv}{os.pathsep}{env.get('PATH', '')}"
    env["STUB_LOG"] = str(log)
    env["STUB_MODE"] = mode
    env["PYTHONPATH"] = str(ROOT)
    for name in agent.SECRET_ENV:
        env.pop(name, None)
    return env, log


@SHELL
def test_checkup_clippa_allowlist(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    seen = tmp_path / "seen"
    (bindir / "clippa").write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {seen}\n", encoding="utf-8")
    (bindir / "clippa").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
    script = AGENT / "checkup-clippa.sh"
    allowed = [
        ["audit"], ["audit", "--all"], ["audit", "--brief"], ["costs"], ["checkup"],
        ["checkup", "--json"], ["status"], ["doctor", "--offline"],
        ["doctor", "--role", "agent", "--offline"], ["record", "status"], ["worker", "--status"],
    ]
    for args in allowed:
        r = _sh(script, args, "", env, tmp_path)
        assert r.returncode == 0, (args, r.stderr)
        assert seen.read_text(encoding="utf-8").splitlines() == args
    r = _sh(script, ["alert", "send", "--kind", "checkup", "--line", "all good"], "", env, tmp_path)
    assert r.returncode == 0, r.stderr
    assert seen.read_text(encoding="utf-8").splitlines() == ["alert", "send", "--kind", "checkup", "all good"]
    r = _sh(script, ["--dry-run", "status"], "", env, tmp_path)
    assert r.returncode == 0 and seen.read_text(encoding="utf-8").strip() == "status"
    rejected = (
        ["upload"], ["audit", "--save"], ["status", "--sync"], ["doctor", "--role", "claude", "--offline"],
        ["checkup", "--json", "--mark"], ["alert", "send", "--kind", "other", "--line", "x"],
        ["alert", "send", "/tmp/out"], ["alert", "send", "--kind", "../secret", "--line", "x"],
        ["record", "status", "--json"],
        ["alert", "send", "--line", "ok; id"],
        ["alert", "send", "--line", "ok && id"],
        ["alert", "send", "--line", "ok | id"],
        ["alert", "send", "--line", "ok > out"],
        ["alert", "send", "--line", "ok < in"],
        ["alert", "send", "--line", "ok `id`"],
        ["alert", "send", "--line", "ok $(id)"],
        ["alert", "send", "--line", "-n"],
        ["alert", "send", "--line", "one\ntwo"],
    )
    for args in rejected:
        r = _sh(script, args, "", env, tmp_path)
        assert r.returncode == 2, args
    r = _sh(script, ["--dry-run", "alert", "send", "--kind", "checkup", "--line", "nope"], "", env, tmp_path)
    assert r.returncode == 2 and "blocked" in r.stderr
    env["CHECKUP_DRY"] = "1"
    r = _sh(script, ["alert", "send", "--line", "nope"], "", env, tmp_path)
    assert r.returncode == 2 and "blocked" in r.stderr


@SHELL
def test_agent_run_config_boundary_and_output(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    env, log = _wrapper_env(tmp_path, "text")
    parent_router, parent_gate, parent_anthropic = "fixture-b", "fixture-c", "fixture-d"
    env["OPENROUTER_API_KEY"] = parent_router
    env["OPENCODE_PASSWORD"] = parent_gate
    env["ANTHROPIC_API_KEY"] = parent_anthropic
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "keep").write_text("ok\n", encoding="utf-8")
    subprocess.run(["git", "add", "keep"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    fix = tmp_path / "checkup-fix"
    prompt = "please write /app/clippa/x.py and $(touch pwned)\n"
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    env["TMPDIR"] = str(prompts)
    r = _sh(AGENT / "agent-run.sh", ["--role", "audit"], prompt, env, repo)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "Final answer.\n"
    assert "/run/secrets" not in r.stdout and "tool" not in r.stdout
    assert not (repo / "pwned").exists()
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout == ""
    doc = json.loads((log / "config.json").read_text(encoding="utf-8"))
    rules = doc["permissions"]
    assert rules[0]["effect"] == "deny" and rules[0]["resource"] == "*"
    assert doc["mcp"]["servers"] == {}
    assert doc["agents"]["audit"]["steps"] == 80
    assert doc["providers"]["openrouter"]["settings"]["apiKey"] == "{file:/run/secrets/openrouter_key}"
    assert effect(rules, "edit", str(fix / "a.py")) == "deny"
    assert effect(rules, "write", "/data/streams/a") == "deny"
    child = (log / "env").read_text(encoding="utf-8")
    for secret in (parent_router, parent_gate, parent_anthropic, "OPENROUTER_API_KEY=", "OPENCODE_PASSWORD="):
        assert secret not in child
    argv = (log / "argv").read_text(encoding="utf-8")
    assert "--format\njson" in argv and "--standalone" in argv and "--archived" in argv
    assert "openrouter/anthropic/claude-sonnet-5.5#high" in argv
    assert prompt.strip() in argv
    assert list(prompts.iterdir()) == []

    env["STUB_MODE"] = "text"
    r = _sh(AGENT / "agent-run.sh", ["--role", "checkup", "--fix-dir", str(fix)], "ok\n", env, repo)
    assert r.returncode == 0, r.stderr
    rules = json.loads((log / "config.json").read_text(encoding="utf-8"))["permissions"]
    assert effect(rules, "edit", str(fix.resolve() / "a.py")) == "allow"
    assert effect(rules, "edit", "/app/clippa/x.py") == "deny"
    assert effect(rules, "write", "/data/clipper.toml") == "deny"

    env["STUB_MODE"] = "deny"
    r = _sh(AGENT / "agent-run.sh", ["--role", "audit"], "no\n", env, repo)
    assert r.returncode != 0
    assert "openrouter_key" not in r.stdout and "{" not in r.stdout
    assert "DeniedError" in r.stderr
    assert list(prompts.iterdir()) == []

    env["STUB_MODE"] = "empty"
    r = _sh(AGENT / "agent-run.sh", ["--role", "audit"], "no\n", env, repo)
    assert r.returncode != 0 and r.stdout == ""

    env["STUB_MODE"] = "sleep"
    r = _sh(AGENT / "agent-run.sh", ["--role", "audit"], "no\n", env, repo)
    assert r.returncode == 124 and "timed out" in r.stderr


@SHELL
def test_agent_run_claude_uses_the_resolved_model(tmp_path):
    env, log = _wrapper_env(tmp_path, "text")
    env["CLIPPA_AGENT_CLI"] = "claude"
    env["CLIPPA_AGENT_MODEL"] = "anthropic:claude-sonnet-5-5"
    env["CLIPPA_AGENT_AUDIT_EFFORT"] = "high"
    r = _sh(AGENT / "agent-run.sh", ["--role", "audit"], "hello\n", env, tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "claude said"
    argv = (log / "claude").read_text(encoding="utf-8")
    assert "claude-sonnet-5-5" in argv and "--effort\nhigh" in argv
    assert not (log / "config.json").exists()
    env["CLIPPA_AGENT_MODEL"] = "openrouter:anthropic/claude-sonnet-5.5"
    r = _sh(AGENT / "agent-run.sh", ["--role", "audit"], "hello\n", env, tmp_path)
    assert r.returncode != 0 and "cannot run" in r.stderr
    assert (log / "claude").read_text(encoding="utf-8").count("claude-sonnet") == 1


def _documented_alerts() -> list[str]:
    found = []
    for name in ("checkup.md", "audit-deep.md"):
        for line in (AGENT / name).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("checkup-clippa alert send"):
                found.append(line)
    assert len(found) == 2, found
    return found


def test_documented_alert_commands_pass_opencode_shell_rules():
    """The DM the prompts tell the model to send is allowed, and an injection on it is not."""
    fix = "/home/agent/checkup-fix"
    commands = _documented_alerts()
    for role in ("checkup", "audit"):
        rules = agent.permissions(role, fix)
        for cmd in commands:
            assert "\n" not in cmd and "..." in cmd
            assert effect(rules, "shell", cmd) == "allow", (role, cmd)
            for extra in ("; id", "| id", "> out", "< in", " && id", " `id`", " $(id)"):
                assert effect(rules, "shell", cmd + extra) == "deny", (role, extra)


@SHELL
def test_documented_alert_commands_send_a_multiline_message(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    seen = tmp_path / "seen.json"
    clippa = bindir / "clippa"
    clippa.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"json.dump(sys.argv[1:], open({str(seen)!r}, 'w'))\n",
        encoding="utf-8")
    clippa.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
    script = AGENT / "checkup-clippa.sh"
    for cmd in _documented_alerts():
        args = shlex.split(cmd)[1:]
        r = _sh(script, args, "", env, tmp_path)
        assert r.returncode == 0, (cmd, r.stderr)
        got = json.loads(seen.read_text(encoding="utf-8"))
        assert got[:4] == ["alert", "send", "--kind", "checkup"]
        assert got[4].count("\n") == 2 and "..." in got[4]


@SHELL
def test_checkup_git_allows_reads_and_refuses_writes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.txt").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    script = AGENT / "checkup-git.sh"
    env = os.environ.copy()
    for args in (["log", "--oneline"], ["show", "HEAD"], ["diff", "HEAD"]):
        r = _sh(script, args, "", env, repo)
        assert r.returncode == 0, (args, r.stderr)
    for args in (["push"], ["diff", "--output=out.patch"], ["status"]):
        r = _sh(script, args, "", env, repo)
        assert r.returncode == 2, (args, r.stderr)
    assert not (repo / "out.patch").exists()
