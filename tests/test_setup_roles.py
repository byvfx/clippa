from clipper import setup


def test_control_station_roles():
    assert setup.doctor_role(["control"]) == "control"
    assert setup.doctor_role(["control", "recorder"]) == "control"
    assert setup.doctor_role(["control", "worker"]) == "gpu"  # the worker's checks cover control's
    assert setup.doctor_role(["control", "serve", "bot"]) == "all"
    assert setup.control_only(["control"]) and setup.control_only(["recorder", "control"])
    assert not setup.control_only(["control", "worker"]) and not setup.control_only(["recorder"])
    assert setup.extras_for(["control"], "modal") == []



def test_control_station_keys_only_warn(defaults, monkeypatch):
    from clipper import doctor

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv(defaults["review"]["token_env"], raising=False)
    defaults["paths"]["anthropic_key"] = defaults["paths"]["people"].parent / "no_key.txt"

    def levels(role):
        return {line[0] for line in doctor.collect(doctor.check_keys, defaults, role)
                if "ANTHROPIC" in line[2] or "Kitsu" in line[2] or "[review] host" in line[2]}

    assert levels("gpu") == {"FAIL"}
    assert levels("control") == {"WARN"}


def test_kitsu_required_where_doctor_fails_without_it():
    # #78: setup offered Enter to skip Kitsu, then doctor failed every role but control and nas
    assert setup.kitsu_required(["worker"]) and setup.kitsu_required(["serve", "bot"])
    assert setup.kitsu_required(["control", "worker"])
    assert not setup.kitsu_required(["control"]) and not setup.kitsu_required(["recorder", "control"])
    assert not setup.kitsu_required(["recorder"])
    assert setup.kitsu_host_error("", required=False) is None
    assert "needed here" in setup.kitsu_host_error("", required=True)
    assert setup.kitsu_host_error("http://nas.local:8090", required=True) is None
    assert "a URL" in setup.kitsu_host_error("nas.local", required=False)


def test_required_key_asks_again_instead_of_skipping(monkeypatch, capsys):
    ask = setup.Ask({})
    ask.interactive = True
    typed = iter(["", "tok"])
    monkeypatch.setattr(ask, "secret", lambda name: next(typed))
    monkeypatch.setattr(setup, "user_env", lambda name: "")
    monkeypatch.setattr(setup, "set_user_env", lambda name, value: "a user env var")
    monkeypatch.delenv("CLIPPER_TEST_TOKEN", raising=False)
    setup.ensure_env_key({}, ask, "CLIPPER_TEST_TOKEN", "a token", "somewhere", check=False, required="why")
    out = capsys.readouterr().out
    assert "is needed here: why" in out and "Saved as a user env var" in out
    monkeypatch.delenv("CLIPPER_TEST_TOKEN", raising=False)


def test_required_key_skip_says_doctor_fails_when_not_interactive(monkeypatch, capsys):
    monkeypatch.setattr(setup, "user_env", lambda name: "")
    setup.ensure_env_key({}, setup.Ask({}), "CLIPPER_TEST_TOKEN", "a token", "somewhere", check=False,
                         required="why")
    assert "`clipper doctor` fails until it is set (why)" in capsys.readouterr().out
