"""The NAS agent container's model, translated for OpenCode or Claude Code.

`[agent]` in the config (defaults, clipper.toml, clipper.local.toml, CLIPPA_AGENT_*) is the
one source. `clippa agent-config --role checkup|audit --json` prints what a headless run
should call. OpenCode takes ``provider/model`` and an optional ``#effort``; Claude Code
takes a bare ``claude-*`` name and an ``--effort`` flag.

``python -m clippa.agent run`` is what ``deploy/agent/agent-run.sh`` execs after it has
parked the prompt in a 0600 file. It does not interpolate that text into a shell.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

from .errors import ClipperError

ROLES = ("checkup", "audit")
CLIS = ("opencode", "claude")
EFFORTS = ("", "low", "medium", "high")
SECRET_ENV = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENCODE_PASSWORD", "CLAUDE_CODE_OAUTH_TOKEN")
KEY_FILE = "{file:/run/secrets/openrouter_key}"
SECRET_RESOURCES = (
    "/data/secrets/**",
    "/run/secrets/**",
    "**/*secret*",
    "**/.env*",
    "**/*.key",
    "**/*auth.json",
)
SHELL_META = ("*&&*", "*||*", "*;*", "*|*", "*>*", "*<*", "*$(*", "*`*", "*\n*")
READS = ("read", "glob", "grep")
AUDIT_WRITES = ("edit", "write", "patch", "multiedit")
_SECRET = re.compile(
    r"/run/secrets/\S*|/data/secrets/\S*|\S*auth\.json|\S+\.key\b|\S*/\.env\S*",
    re.IGNORECASE,
)
_SKIP_TYPES = {"tool", "tool_use", "tool_result", "step_start", "step_finish", "step", "reasoning", "patch"}


def translate(cli: str, model: str, effort: str) -> str:
    """The CLI's own model string. OpenCode: first ``:`` becomes ``/``, then ``#effort``.

    Claude Code: a bare ``claude-*`` name, or the same with ``anthropic:`` stripped.
    ``openrouter:`` with ``cli = "claude"`` is an error, not a silent mis-route."""
    model, effort = (model or "").strip(), (effort or "").strip()
    if effort not in EFFORTS:
        raise ClipperError(f"[agent] effort {effort!r} is not one of: {', '.join(EFFORTS)}")
    if cli == "opencode":
        if ":" in model:
            provider, _, rest = model.partition(":")
            if not provider or not rest:
                raise ClipperError(f"[agent] model {model!r} is not provider:model")
            model = f"{provider}/{rest}"
        return f"{model}#{effort}" if effort else model
    if cli == "claude":
        if model.startswith("openrouter:"):
            raise ClipperError(
                f'[agent] cli = "claude" cannot run {model}: OpenRouter models need '
                '[agent] cli = "opencode" (it turns the first ":" into "/")')
        if model.startswith("anthropic:"):
            model = model.split(":", 1)[1]
        if not model.startswith("claude-"):
            raise ClipperError(
                f"[agent] cli = \"claude\" needs a bare claude-* model (or anthropic:claude-*), not {model!r}")
        return model
    raise ClipperError(f"[agent] cli = {cli!r} is not opencode or claude")


def resolve(cfg: dict, role: str) -> dict:
    """One role's translated settings. Empty checkup/audit model inherits ``[agent] model``."""
    if role not in ROLES:
        raise ClipperError(f"[agent] role {role!r} is not checkup or audit")
    section = cfg["agent"]
    cli = section["cli"]
    if cli not in CLIS:
        raise ClipperError(f"[agent] cli = {cli!r} is not opencode or claude")
    source = (section[f"{role}_model"] or section["model"]).strip()
    if not source:
        raise ClipperError(f"[agent] {role} has no model")
    effort = section[f"{role}_effort"]
    turns, timeout = section[f"{role}_max_turns"], section[f"{role}_timeout_s"]
    if not isinstance(turns, int) or isinstance(turns, bool) or turns < 1:
        raise ClipperError(f"[agent] {role}_max_turns = {turns!r} must be a positive integer")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        raise ClipperError(f"[agent] {role}_timeout_s = {timeout!r} must be a positive integer")
    return {"role": role, "cli": cli, "model": translate(cli, source, str(effort or "")),
            "effort": effort or "", "max_turns": turns, "timeout_s": timeout, "source_model": source}


def _rule(action: str, resource: str, effect: str) -> dict:
    return {"action": action, "resource": resource, "effect": effect}


def permissions(role: str, fix_dir: str) -> list[dict]:
    """OpenCode rules, last match wins. The first rule denies everything. The last rules
    deny secrets, shell metacharacters, and (audit only) every write, so an earlier allow
    cannot open them."""
    if role not in ROLES:
        raise ClipperError(f"[agent] role {role!r} is not checkup or audit")
    rules = [_rule("*", "*", "deny")]
    rules += [_rule(action, "*", "allow") for action in READS]
    for resource in ("/data/**", "/tmp/**"):
        rules.append(_rule("external_directory", resource, "allow"))
    if role == "checkup":
        rules.append(_rule("external_directory", f"{fix_dir}/**", "allow"))
    rules.append(_rule("shell", "checkup-clippa *", "allow"))
    rules.append(_rule("shell", "checkup-git *", "allow"))
    if role == "checkup":
        rules.append(_rule("shell", "checkup-pr *", "allow"))
        rules.append(_rule("edit", f"{fix_dir}/**", "allow"))
        rules.append(_rule("write", f"{fix_dir}/**", "allow"))
    rules += [_rule("*", resource, "deny") for resource in SECRET_RESOURCES]
    rules += [_rule("shell", resource, "deny") for resource in SHELL_META]
    if role == "audit":
        rules += [_rule(action, "*", "deny") for action in AUDIT_WRITES]
    return rules


def fix_dir_for(role: str, given: str | None) -> str:
    """Checkup may write only under this directory. Audit has no write directory."""
    if role != "checkup":
        return ""
    raw = given or str(Path.home() / "checkup-fix")
    return str(Path(raw).expanduser().resolve())


def opencode_document(role: str, max_turns: int, fix_dir: str) -> dict:
    """The ``OPENCODE_CONFIG_CONTENT`` document for one headless run."""
    rules = permissions(role, fix_dir)
    return {
        "update": "disable",
        "permissions": rules,
        "mcp": {"servers": {}},
        "agents": {role: {"steps": max_turns, "permissions": rules}},
        "providers": {"openrouter": {"settings": {"apiKey": KEY_FILE}}},
    }


def child_env(base: dict, extra: dict | None = None) -> dict:
    """The model process's environment: no API key and no OpenCode password in the env."""
    env = {k: v for k, v in base.items() if k not in SECRET_ENV}
    env.update(extra or {})
    for name in SECRET_ENV:
        env.pop(name, None)
    return env


def redact(text: str) -> str:
    """Drop secret paths before anything from a run is printed."""
    return _SECRET.sub("[redacted]", text or "")


def parse_events(raw: str) -> list[dict] | None:
    """One JSON value, a JSON array, or newline-delimited JSON. None when it is not JSON."""
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    else:
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list) and all(isinstance(item, dict) for item in data):
            return list(data)
        return None
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        events.append(obj)
    return events or None


def event_problem(event: dict) -> str:
    """``DeniedError`` or another error event's name; "" when the event is ordinary."""
    typ = str(event.get("type") or "")
    name = str(event.get("name") or "")
    err = event.get("error")
    err_name = ""
    if isinstance(err, dict):
        err_name = str(err.get("name") or err.get("type") or "error")
    elif err:
        err_name = "error"
    if "DeniedError" in (typ, name, err_name):
        return "DeniedError"
    if typ in ("error", "exception") or err_name:
        return err_name or "error"
    return ""


def assistant_text(event: dict) -> str:
    """Plain text from an assistant message. Tool and step events contribute nothing."""
    typ = str(event.get("type") or "")
    if typ in _SKIP_TYPES or str(event.get("role") or "assistant") != "assistant":
        return ""
    if typ not in ("", "text", "message", "content"):
        return ""
    found: list[str] = []
    if isinstance(event.get("text"), str):
        found.append(event["text"])
    part = event.get("part")
    if (isinstance(part, dict) and str(part.get("type") or "text") == "text"
            and str(part.get("role") or "assistant") == "assistant" and isinstance(part.get("text"), str)):
        found.append(part["text"])
    msg = event.get("message")
    if isinstance(msg, dict) and str(msg.get("role") or "") == "assistant":
        content = msg.get("content")
        if isinstance(content, str):
            found.append(content)
        elif isinstance(content, list):
            found += [b["text"] for b in content if isinstance(b, dict) and str(b.get("type") or "text") == "text"
                      and isinstance(b.get("text"), str)]
    return found[-1] if found else ""


def extract_assistant(raw: str) -> tuple[str, str]:
    """(the assistant's final plain text, ""). On a problem, ("", a short reason).

    The reason never includes the raw JSON or a secret path."""
    events = parse_events(raw)
    if events is None:
        return "", "output was not json"
    problem = ""
    last = ""
    for event in events:
        err = event_problem(event)
        if err:
            problem = err
            continue
        text = assistant_text(event)
        if text.strip():
            last = text
    if problem:
        return "", problem
    cleaned = redact(last).strip()
    return (cleaned, "") if cleaned else ("", "empty output")


def _bash(commands: tuple[str, ...]) -> list[str]:
    return [f"Bash({cmd})" for cmd in commands]


_READ_COMMANDS = (
    "checkup-clippa audit", "checkup-clippa audit --all", "checkup-clippa audit --brief",
    "checkup-clippa costs", "checkup-clippa checkup", "checkup-clippa checkup --json",
    "checkup-clippa status", "checkup-clippa doctor --offline",
    "checkup-clippa doctor --role agent --offline", "checkup-clippa record status",
    "checkup-clippa worker --status", "checkup-clippa alert send:*", "checkup-git:*",
)


def claude_argv(resolved: dict, prompt: str, fix_dir: str) -> list[str]:
    """``claude -p`` for this role. Audit cannot edit. Checkup edits only ``fix_dir``."""
    cmd = ["claude", "-p", prompt, "--model", resolved["model"], "--max-turns", str(resolved["max_turns"])]
    if resolved["effort"]:
        cmd += ["--effort", resolved["effort"]]
    blocked = ["NotebookEdit", "WebFetch", "WebSearch"]
    allowed = ["Read", "Grep", "Glob", *_bash(_READ_COMMANDS)]
    if resolved["role"] == "audit":
        blocked = ["Edit", "Write", *blocked]
    else:
        allowed += [f"Edit(/{fix_dir}/**)", f"Write(/{fix_dir}/**)", "Bash(checkup-pr:*)"]
    cmd += ["--disallowedTools", *blocked]
    for tool in allowed:
        cmd += ["--allowedTools", tool]
    return cmd


def opencode_argv(resolved: dict, prompt: str) -> list[str]:
    return ["opencode", "run", "--standalone", "--archived", "--agent", resolved["role"],
            "-m", resolved["model"], "--format", "json", prompt]


def read_prompt(path: Path) -> str:
    """The prompt file agent-run created. On POSIX it must be mode 0600.

    Windows reports no Unix mode (a chmod to 0600 still reads back as 0666), and the
    headless run is the Linux container, so the check is POSIX only."""
    if os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode != 0o600:
            raise ClipperError(f"agent-run: prompt file is mode {mode:o}, want 600")
    return path.read_text(encoding="utf-8")


def _run(cmd: list[str], env: dict, timeout_s: int) -> subprocess.CompletedProcess:
    return subprocess.run(["timeout", str(timeout_s), *cmd], env=env, capture_output=True, text=True,
                          check=False)


def _finish(proc: subprocess.CompletedProcess, text: str, problem: str) -> int:
    if proc.returncode == 124:
        problem = "timed out"
    elif proc.returncode and not problem:
        problem = f"exited {proc.returncode}"
    if problem or not text.strip():
        sys.stderr.write(f"agent-run: {problem or 'empty output'}\n")
        err = redact(proc.stderr or "").strip()
        if err:
            sys.stderr.write(err[-2000:] + "\n")
        return proc.returncode or 1
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def execute(resolved: dict, prompt: str, fix_dir: str, dry: bool) -> int:
    """Run the resolved CLI. OpenCode's stdout is reduced to the assistant's final text."""
    env = child_env(os.environ, {"CHECKUP_DRY": "1"} if dry else None)
    if not dry:
        env.pop("CHECKUP_DRY", None)
    if resolved["cli"] == "opencode":
        folder = fix_dir_for(resolved["role"], fix_dir or None)
        doc = opencode_document(resolved["role"], resolved["max_turns"], folder)
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(doc)
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
        env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
        proc = _run(opencode_argv(resolved, prompt), env, resolved["timeout_s"])
        text, problem = extract_assistant(proc.stdout or "")
        return _finish(proc, text, problem)
    if resolved["cli"] != "claude":
        raise ClipperError(f"[agent] cli = {resolved['cli']!r} is not opencode or claude")
    folder = fix_dir_for("checkup", fix_dir or None)
    proc = _run(claude_argv(resolved, prompt, folder), env, resolved["timeout_s"])
    text = redact(proc.stdout or "").strip()
    problem = "" if proc.returncode == 0 and text else ("" if text else "empty output")
    return _finish(proc, text, problem)


def main(argv: list[str] | None = None) -> int:
    """``run --json <resolved> --prompt-file <path> [--fix-dir <path>] [--dry-run]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "run":
        sys.stderr.write("usage: python -m clippa.agent run --json <json> --prompt-file <path> "
                         "[--fix-dir <path>] [--dry-run]\n")
        return 2
    payload, prompt_path, fix_dir, dry = "", "", "", False
    rest = args[1:]
    while rest:
        flag, rest = rest[0], rest[1:]
        if flag == "--dry-run":
            dry = True
            continue
        if flag not in ("--json", "--prompt-file", "--fix-dir") or not rest:
            sys.stderr.write(f"agent-run: unknown argument {flag}\n")
            return 2
        value, rest = rest[0], rest[1:]
        if flag == "--json":
            payload = value
        elif flag == "--prompt-file":
            prompt_path = value
        else:
            fix_dir = value
    if not payload or not prompt_path:
        sys.stderr.write("agent-run: --json and --prompt-file are required\n")
        return 2
    try:
        resolved = json.loads(payload)
        prompt = read_prompt(Path(prompt_path))
        return execute(resolved, prompt, fix_dir, dry)
    except ClipperError as e:
        sys.stderr.write(str(e) + "\n")
        return 1
    except (OSError, json.JSONDecodeError) as e:
        sys.stderr.write(f"agent-run: {type(e).__name__}: {e}\n")
        return 1
