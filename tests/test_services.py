import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from clippa import daemon, jsonstate, services
from clippa.errors import ClipperError

NOW = datetime(2026, 10, 30, 20, 0, tzinfo=UTC)


def cfg_with(tmp_path, beat: dict | None) -> dict:
    if beat is not None:
        (tmp_path / ".record").mkdir()
        (tmp_path / ".record" / "status.json").write_text(json.dumps(beat), encoding="utf-8")
    return {"paths": {"streams": tmp_path}, "cut": {"nas_host": ""}}


def beat(ago_s: float, **channels) -> dict:
    return {"at": (NOW - timedelta(seconds=ago_s)).isoformat(), "channels": channels}


def test_recorder_from_its_heartbeat(tmp_path):
    rec = services.recorder(cfg_with(tmp_path, beat(3, kino="kick-1 recording", don="offline")), NOW)
    assert rec["up"] and rec["live"] == ["kino"]
    assert "RECORDING kino" in services.recorder_lines(rec)[0]


def test_a_stale_heartbeat_is_down_and_not_live(tmp_path):
    """A recorder that died mid-show isn't recording: deploying it is what's needed."""
    rec = services.recorder(cfg_with(tmp_path, beat(600, kino="kick-1 recording")), NOW)
    assert not rec["up"] and rec["live"] == []
    assert "DOWN" in services.recorder_lines(rec)[0]


def test_no_heartbeat(tmp_path):
    rec = services.recorder(cfg_with(tmp_path, None), NOW)
    assert rec["age_s"] is None and not rec["live"]
    assert "no heartbeat" in services.recorder_lines(rec)[0]


def test_record_status_exits_3_while_live(tmp_path):
    """deploy/record/deploy.sh refuses on exit 3."""
    with pytest.raises(ClipperError) as e:
        services.record_status(cfg_with(tmp_path, beat(1, kino="kick-1 paused")), NOW)
    assert e.value.status == 3


def test_recorder_restart_refused_while_live(tmp_path, monkeypatch):
    cfg = cfg_with(tmp_path, {})
    monkeypatch.setattr(services, "recorder", lambda cfg: {"live": ["kino"]})
    called = []
    monkeypatch.setattr(services, "recorder_compose", lambda cfg, action: called.append(action) or 0)
    with pytest.raises(ClipperError, match="recording kino now"):
        services.run(cfg, "restart", "recorder")
    assert services.run(cfg, "restart", "recorder", force=True) == 0 and called == ["restart"]


def test_all_never_touches_the_recorder(tmp_path, monkeypatch):
    done = []
    monkeypatch.setattr(services, "run_task", lambda name, action, cfg=None: done.append((name, action)) or 0)
    monkeypatch.setattr(services, "recorder_compose", lambda cfg, action: pytest.fail("recorder touched"))
    services.run(cfg_with(tmp_path, {}), "stop", "all")
    assert done == [("worker", "stop"), ("serve", "stop"), ("bot", "stop")]


def test_status_shows_serve_and_bot_heartbeats(tmp_path, monkeypatch, capsys):
    """From the share, so it's right whichever machine they run on."""
    from clippa import daemon

    monkeypatch.setattr(services, "run_task", lambda name, action, cfg=None: 0)
    cfg = cfg_with(tmp_path, None)
    daemon.beat(cfg, "serve", "2026-11-03T10:00:00+00:00", jobs={"upload": {"running": True, "started": "2026-11-03T10:05:00+00:00"}})
    services.run(cfg, "status", "all")
    out = capsys.readouterr().out
    assert "serve: up on" in out and "upload: running since" in out
    assert "bot: no heartbeat" in out


def test_hub_services_go_over_ssh(defaults, monkeypatch):
    # [serve] hub: serve, the bot and the worker are containers on the NAS (deploy/hub).
    cfg = {**defaults, "serve": {**defaults["serve"], "hub": "clippa-hub"}}
    sent = []
    monkeypatch.setattr(services, "ssh", lambda host, cmd, timeout=20: sent.append((host, cmd))
                        or subprocess.CompletedProcess([host], 0, "ok", ""))
    assert services.run_task("bot", "restart", cfg) == 0
    host, cmd = sent[0]
    assert host == "nas" and cmd.startswith("{ if [ -f clipper-hub/compose.yaml ] || { [ -d clipper-hub ] && [ ! -d clippa-hub ]; }; then cd clipper-hub; else cd clippa-hub; fi; } && ")
    assert "docker compose restart bot" in cmd


def test_an_old_compose_folder_reaches_the_renamed_one(defaults, monkeypatch):
    # #286 pass 3: a config that still says clipper-hub (or clipper-record) cds into the new
    # folder, or the old one where the new deploy hasn't run yet.
    cfg = {**defaults, "serve": {**defaults["serve"], "hub": "clipper-hub"},
           "record": {**defaults["record"], "compose_dir": "clipper-record"}}
    sent = []
    monkeypatch.setattr(services, "ssh", lambda host, cmd, timeout=20: sent.append(cmd)
                        or subprocess.CompletedProcess([host], 0, "ok", ""))
    services.run_task("bot", "restart", cfg)
    services.recorder_compose(cfg, "restart")
    assert sent[0].startswith("{ if [ -f clipper-hub/compose.yaml ] || { [ -d clipper-hub ] && [ ! -d clippa-hub ]; }; then cd clipper-hub; else cd clippa-hub; fi; } && ")
    assert sent[1].startswith(services.cd_compose("clippa-record") + " && ")
    assert services.cd_compose("my hub") == "cd 'my hub'"
    # the new default too, before deploy/record/deploy.sh has made its folder
    assert services.cd_compose("clippa-record") == services.cd_compose("clipper-record")


def test_serve_and_bot_have_no_task_without_a_hub(defaults, capsys):
    """Without [serve] hub only the worker has a scheduled task: serve and the bot run under
    compose (deploy/hub) or by hand, and stop/restart say so instead of looking for a script."""
    cfg = {**defaults, "serve": {**defaults["serve"], "hub": ""}}
    assert services.run_task("serve", "restart", cfg) == 1
    assert "deploy/hub" in capsys.readouterr().out
    assert services.run_task("bot", "status", cfg) == 0


def test_record_status_stays_live_across_a_read_mid_replace(tmp_path, monkeypatch):
    """One missed read made it say "no heartbeat" (exit 0) mid-show on 2026-11-08, which would have
    let deploy/record/deploy.sh restart the recorder: the heartbeat is read again."""
    cfg = cfg_with(tmp_path, beat(1, kino="kick-1 recording"))
    real, misses = jsonstate.read, [None]
    monkeypatch.setattr(daemon.jsonstate, "read", lambda p, d=None: misses.pop() if misses else real(p, d))
    monkeypatch.setattr(daemon.time, "sleep", lambda s: None)
    with pytest.raises(ClipperError) as e:
        services.record_status(cfg, NOW)
    assert e.value.status == 3


def test_record_status_refuses_when_the_heartbeat_cannot_be_read(tmp_path, monkeypatch):
    """Unknown isn't quiet: exit 1, which deploy/record/deploy.sh refuses on too, and the stop/restart
    guard holds (Copilot on #183)."""
    monkeypatch.setattr(daemon.time, "sleep", lambda s: None)
    cfg = cfg_with(tmp_path, {})
    (tmp_path / ".record" / "status.json").write_text("{", encoding="utf-8")
    rec = services.recorder(cfg, NOW)
    assert rec["error"] and rec["live"] == [] and "unreadable" in services.recorder_lines(rec)[0]
    with pytest.raises(ClipperError, match="can't tell") as e:
        services.record_status(cfg, NOW)
    assert e.value.status == 1
    monkeypatch.setattr(services, "recorder_compose", lambda cfg, action: pytest.fail("recorder touched"))
    with pytest.raises(ClipperError, match="can't tell"):
        services.run(cfg, "restart", "recorder")


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("powershell.exe") is not None,
                    reason="needs bash, and reads Windows user variables where powershell.exe is")
def test_deploy_keys_stops_on_a_key_that_differs_and_never_prints_it(tmp_path):
    """deploy/keys.sh (2026-11-16, an old ANTHROPIC_API_KEY shipped to the hub): a key in
    env.local that differs from this machine's stops the deploy by name; sync copies it in."""
    keys = Path(__file__).resolve().parent.parent / "deploy" / "keys.sh"
    env_local = tmp_path / "env.local"
    env_local.write_text('FOO_KEY="old-secret"\nGH_TOKEN=only-here\nTS_HOSTNAME=x\n')
    script = f'source "{keys.as_posix()}"; check_keys "{env_local.as_posix()}"; echo deployed'
    env = {"PATH": os.environ["PATH"], "FOO_KEY": "new-secret"}
    r = subprocess.run(["bash", "-c", script], env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True)
    assert r.returncode == 1 and "FOO_KEY" in r.stderr and "GH_TOKEN" not in r.stderr
    assert "secret" not in r.stderr + r.stdout
    r = subprocess.run(["bash", "-c", script], env={**env, "DEPLOY_KEYS": "sync"},
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert r.returncode == 0 and "deployed" in r.stdout and "secret" not in r.stderr
    assert env_local.read_text().splitlines() == ["FOO_KEY=new-secret", "GH_TOKEN=only-here", "TS_HOSTNAME=x"]
    r = subprocess.run(["bash", "-c", script], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert r.returncode == 0 and r.stderr == ""      # matching now: nothing to say


@pytest.mark.skipif(shutil.which("bash") is None or sys.platform == "win32",
                    reason="needs bash (on Windows `bash` may be WSL's, which reads no C:/ paths)")
def test_retire_old_stops_the_old_compose_project_before_the_new_one_starts(tmp_path):
    """deploy/retire.sh (#286 pass 3): the old clipper-* project is stopped (from its folder, or
    by name when the folder is the one in use) and its compose.yaml retired; a NAS that can't be
    asked, or a stop that fails, stops the deploy, so two bots never run at once."""
    retire = Path(__file__).resolve().parent.parent / "deploy" / "retire.sh"
    log = tmp_path / "ssh.log"

    def run(state, new="clippa-hub", down_fails=""):
        log.write_text("")
        script = (f'source "{retire.as_posix()}"\n'
                  f'ssh() {{ printf "%s\\n" "$2" >> "{log.as_posix()}"; case "$2" in\n'
                  '  ids=*) [ -n "$STATE" ] && echo "$STATE" || return 255 ;;\n'
                  '  *down*|*"docker stop"*) [ -z "$DOWN_FAILS" ] ;; esac; }\n'
                  f'retire_old nas clipper-hub {new} ""; echo "retired=$RETIRED"')
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           env={"PATH": os.environ["PATH"], "STATE": state, "DOWN_FAILS": down_fails})
        return r, log.read_text().splitlines()[1:]   # the commands after the state query

    r, cmds = run("3 yes")
    assert "retired=1" in r.stdout and "cd 'clipper-hub' && " in cmds[0] and "down" in cmds[0]
    assert cmds[1].startswith("mv 'clipper-hub/compose.yaml' 'clipper-hub/compose.yaml.retired-")
    r, cmds = run("2 no", new="clipper-hub")     # HUB_DIR=clipper-hub: the folder in use, so by name
    assert "retired=1" in r.stdout and len(cmds) == 1
    assert "label=com.docker.compose.project=clipper-hub" in cmds[0]   # no compose file needed
    assert cmds[0].index("docker stop -t 60") < cmds[0].index("docker rm")
    r, cmds = run("0 yes")                        # stopped already: only the file goes
    assert "retired=0" in r.stdout and len(cmds) == 1 and cmds[0].startswith("mv ")
    r, cmds = run("")                             # ssh failed: never read as "nothing there"
    assert r.returncode == 1 and "nothing started" in r.stderr and cmds == []
    r, cmds = run("1 yes", down_fails="1")
    assert r.returncode == 1 and not any(c.startswith("mv ") for c in cmds)


@pytest.mark.skipif(shutil.which("bash") is None or sys.platform == "win32",
                    reason="needs bash (on Windows `bash` may be WSL's, which reads no C:/ paths)")
def test_deploy_env_settings_read_either_spelling_without_quotes(tmp_path):
    retire = Path(__file__).resolve().parent.parent / "deploy" / "retire.sh"
    env_local = tmp_path / "env.local"

    def data(text):
        env_local.write_text(text)
        return subprocess.run(["bash", "-c", f'source "{retire.as_posix()}"; data_dir "{env_local.as_posix()}"'],
                              capture_output=True, text=True).stdout.strip()

    assert data('CLIPPER_DATA="/srv/clipper"\r\n') == "/srv/clipper"
    assert data("CLIPPA_DATA=/srv/new\nCLIPPER_DATA=/srv/old\n") == "/srv/new"
    assert data("CLIPPA_DATA=\nCLIPPER_DATA='/srv/old'") == "/srv/old"
    assert data("PUID=1000\n") == "/volume1/docker/clipper"


@pytest.mark.skipif(shutil.which("bash") is None or sys.platform == "win32",
                    reason="needs bash (on Windows `bash` may be WSL's, which reads no C:/ paths)")
def test_cd_compose_picks_the_live_folder(tmp_path):
    """The old folder while its compose.yaml is there (also while a deploy builds the new one),
    the new one once retire.sh has renamed it, or when there never was an old one."""
    def where(*files):
        d = tmp_path / str(len(list(tmp_path.iterdir())))
        for f in files:
            (d / f).parent.mkdir(parents=True, exist_ok=True)
            (d / f).write_text("")
        d.mkdir(exist_ok=True)
        r = subprocess.run(["bash", "-c", services.cd_compose("clipper-hub") + " && basename \"$PWD\""],
                           cwd=d, capture_output=True, text=True)
        return r.stdout.strip() or "fails"

    assert where("clipper-hub/compose.yaml") == "clipper-hub"                             # before the deploy
    assert where("clipper-hub/compose.yaml", "clippa-hub/compose.yaml") == "clipper-hub"  # mid-deploy: still live
    assert where("clipper-hub/compose.yaml.retired-1", "clippa-hub/compose.yaml") == "clippa-hub"
    assert where("clippa-hub/compose.yaml") == "clippa-hub"                               # a new install
    assert where() == "fails"


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None or sys.platform == "win32",
                    reason="needs bash and git (on Windows `bash` may be WSL's, which reads no C:/ paths)")
def test_require_main_refuses_a_checkout_behind_origin_main(tmp_path):
    """deploy/mainguard.sh (2026-11-25: a claude deploy from a pre-#437 branch shipped an image
    without nodejs): behind origin/main stops the deploy, naming the branch and how far; --force,
    a branch at or past main, and an origin that can't be fetched let it go on."""
    guard = Path(__file__).resolve().parent.parent / "deploy" / "mainguard.sh"
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}

    def git(cwd, *args):
        subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)

    origin, work = tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(work))
    git(work, "commit", "-q", "--allow-empty", "-m", "one")
    git(work, "push", "-q", "origin", "HEAD:main")
    git(work, "switch", "-q", "-c", "old")
    git(work, "switch", "-q", "main")
    git(work, "commit", "-q", "--allow-empty", "-m", "two")
    git(work, "push", "-q", "origin", "main")
    git(work, "reset", "-q", "--hard", "HEAD~1")    # main locally behind too; the fetch must see it
    git(work, "update-ref", "refs/remotes/origin/main", "HEAD")

    def run(force=""):
        script = f'source "{guard.as_posix()}"; require_main "{work.as_posix()}" deploy/x.sh "{force}"; echo go'
        return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    r = run()
    assert r.returncode == 1 and "go" not in r.stdout
    assert "main at " in r.stderr and "1 commit(s) behind origin/main" in r.stderr
    assert "bash deploy/x.sh --force" in r.stderr
    r = run(force="1")
    assert r.returncode == 0 and "go" in r.stdout and "--force" in r.stderr
    git(work, "pull", "-q", "--ff-only")
    r = run()
    assert r.returncode == 0 and "go" in r.stdout and r.stderr == ""
    git(work, "switch", "-q", "old")
    r = run()
    assert r.returncode == 1 and "old at " in r.stderr
    git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))   # offline: last fetched main
    r = run()
    assert r.returncode == 1 and "couldn't fetch origin/main" in r.stderr
    git(work, "switch", "-q", "main")
    git(work, "commit", "-q", "--allow-empty", "-m", "three")
    r = run()
    assert r.returncode == 0 and "1 commit(s) that aren't on origin/main" in r.stderr
