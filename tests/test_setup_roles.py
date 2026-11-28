from clippa import setup


def test_control_station_roles():
    assert setup.doctor_role(["control"]) == "control"
    assert setup.doctor_role(["control", "recorder"]) == "control"
    assert setup.doctor_role(["control", "worker"]) == "gpu"  # the worker's checks cover control's
    assert setup.doctor_role(["control", "serve", "bot"]) == "all"
    assert setup.control_only(["control"]) and setup.control_only(["recorder", "control"])
    assert not setup.control_only(["control", "worker"]) and not setup.control_only(["recorder"])
    assert setup.extras_for(["control"], "modal") == []



def test_control_station_keys_only_warn(defaults, monkeypatch):
    from clippa import doctor

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv(defaults["review"]["token_env"], raising=False)
    defaults["paths"]["anthropic_key"] = defaults["paths"]["people"].parent / "no_key.txt"

    def levels(role, what=("ANTHROPIC", "Kitsu", "[review] host")):
        return {line[0] for line in doctor.collect(doctor.check_keys, defaults, role)
                if any(w in line[2] for w in what)}

    assert levels("gpu", ("ANTHROPIC",)) == {"FAIL"}
    assert levels("control") == {"WARN"}


def test_no_kitsu_only_fails_the_roles_that_review_through_it(defaults, monkeypatch):
    # #434: no [review] host is a setup of its own: runs skip review, doctor warns
    from clippa import doctor

    monkeypatch.delenv(defaults["review"]["token_env"], raising=False)
    defaults["review"]["host"] = ""

    def kitsu(role):
        return [(line[0], line[2]) for line in doctor.collect(doctor.check_kitsu_keys, defaults, role, doctor.fail)]

    assert kitsu("gpu") == [("WARN", "[review] host not set: no review; `clippa run` leaves the videos "
                                     "in the stream folder")]  # and no token line: nothing to use it on
    assert {lv for lv, _ in kitsu("serve")} == {"FAIL"} and {lv for lv, _ in kitsu("bot")} == {"FAIL"}
    defaults["review"]["host"] = "<your kitsu>/api"  # a placeholder is a host someone meant to fill in
    assert {lv for lv, _ in kitsu("gpu")} == {"FAIL"}


def test_kitsu_required_where_doctor_fails_without_it():
    # #78: setup offered Enter to skip Kitsu, then doctor failed every role but control and nas;
    # #434: only serve and the bot review through it now
    assert setup.kitsu_required(["serve", "bot"]) and setup.kitsu_required(["worker", "serve"])
    assert setup.kitsu_required(["bot"])
    assert not setup.kitsu_required(["worker"]) and not setup.kitsu_required(["control", "worker"])
    assert not setup.kitsu_required(["control"]) and not setup.kitsu_required(["recorder", "control"])
    assert not setup.kitsu_required(["recorder"])
    assert setup.kitsu_host_error("", required=False) is None
    assert "needed here" in setup.kitsu_host_error("", required=True)
    assert setup.kitsu_host_error("http://nas.local:8090", required=True) is None
    assert "a URL" in setup.kitsu_host_error("nas.local", required=False)
    assert "a URL" in setup.kitsu_host_error("http://", required=True)  # no host: was saved as http:/api


def test_required_key_asks_again_instead_of_skipping(monkeypatch, capsys):
    ask = setup.Ask({})
    ask.interactive = True
    typed = iter(["", "tok"])
    hints = []
    monkeypatch.setattr(ask, "secret", lambda name, skip=True: hints.append(skip) or next(typed))
    monkeypatch.setattr(setup, "user_env", lambda name: "")
    monkeypatch.setattr(setup, "set_user_env", lambda name, value: "a user env var")
    monkeypatch.delenv("CLIPPER_TEST_TOKEN", raising=False)
    setup.ensure_env_key({}, ask, "CLIPPER_TEST_TOKEN", "a token", "somewhere", check=False, required="why")
    out = capsys.readouterr().out
    assert "is needed here: why" in out and "Saved as a user env var" in out
    assert hints == [False, False]  # the prompt doesn't offer "Enter to skip" for a required key
    monkeypatch.delenv("CLIPPER_TEST_TOKEN", raising=False)


def test_required_key_skip_says_doctor_fails_when_not_interactive(monkeypatch, capsys):
    monkeypatch.setattr(setup, "user_env", lambda name: "")
    setup.ensure_env_key({}, setup.Ask({}), "CLIPPER_TEST_TOKEN", "a token", "somewhere", check=False,
                         required="why")
    assert "`clippa doctor` fails until it is set (why)" in capsys.readouterr().out


def test_ctrl_c_in_a_done_step_clears_it_so_setup_asks_again(monkeypatch):
    st = {"done": {"mode": 1, "roles": 1, "machine": 1, "keys": 1}, "roles": ["worker"]}
    saved = []
    monkeypatch.setattr(setup.config, "load", lambda path=None: {})
    monkeypatch.setattr(setup, "load_state", lambda cfg: st)
    monkeypatch.setattr(setup, "load_user_env", lambda cfg: None)
    monkeypatch.setattr(setup, "save_state", lambda cfg, state: saved.append(dict(state["done"])))

    def interrupted(cfg, state, ask):
        raise KeyboardInterrupt
    monkeypatch.setattr(setup, "step_keys", interrupted)
    assert setup.main(None, step="keys") == 130
    assert "keys" not in st["done"] and saved and "keys" not in saved[-1]


def test_a_refused_required_key_lets_enter_skip(monkeypatch, capsys):
    # #352 review: a wrong saved Kitsu address fails every token, so Enter must not trap the user
    ask = setup.Ask({})
    ask.interactive = True
    typed = iter(["wrong", ""])
    hints = []
    monkeypatch.setattr(ask, "secret", lambda name, skip=True: hints.append(skip) or next(typed))
    monkeypatch.setattr(setup, "user_env", lambda name: "")
    monkeypatch.setattr(setup, "check_key", lambda name, cfg: (False, "Kitsu refused it"))
    setup.ensure_env_key({}, ask, "CLIPPER_TEST_TOKEN", "a token", "somewhere", required="why",
                         if_refused="If the address is what's wrong, fix it")
    out = capsys.readouterr().out
    assert hints == [False, True]
    assert "Enter skips it" in out and "fix it" in out and "`clippa doctor` fails" in out
    assert "CLIPPER_TEST_TOKEN" not in __import__("os").environ
