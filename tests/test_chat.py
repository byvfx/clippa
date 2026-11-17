"""The Clippa messenger (#158): local matching, topics and their receipts, card expiry and taps,
jobs, the model's one call, and the page's routes."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from clipper import catalog, chat, costs, llm
from clipper.errors import ClipperError
from clipper.warroom import chat as routes

TZ = ZoneInfo("America/Los_Angeles")
MON = datetime(2026, 11, 16, 10, 0, tzinfo=TZ)


@pytest.fixture
def cfg(defaults, tmp_path):
    defaults["paths"].update(streams=tmp_path / "streams", schedule=tmp_path / "schedule.json",
                             costs=tmp_path / "costs.jsonl", chats=tmp_path / "chats")
    defaults["schedule"]["timezone"] = "America/Los_Angeles"
    (tmp_path / "streams").mkdir()
    return defaults


@pytest.fixture
def no_model(monkeypatch):
    """The model is never called unless a test says what it answers."""
    def ask(cfg, chat_, topic):
        raise AssertionError("model called")
    monkeypatch.setattr(chat, "ask_model", ask)


def card_plan(**over) -> catalog.Plan:
    p = catalog.Plan("card", text="Approve kick-1/s02?", tool="approve", risk=catalog.WRITE,
                     ref="kick-1/s02_blade", snapshot={"decision": "pending"},
                     args={"verb": "approve", "text": "", "on": None})
    for k, v in over.items():
        setattr(p, k, v)
    return p


def test_parse_when():
    assert chat.parse_when("tomorrow 3pm", MON) == "2026-11-17 15:00"
    assert chat.parse_when("fri 15:00", MON) == "2026-11-20 15:00"
    assert chat.parse_when("friday at 9:30 am", MON) == "2026-11-20 09:30"
    assert chat.parse_when("monday 12am", MON) == "2026-11-23 00:00"     # a weekday is the next one
    assert chat.parse_when("2026-11-30 9:05", MON) == "2026-11-30 09:05"
    assert chat.parse_when("2026-11-30", MON) == "2026-11-30"
    assert chat.parse_when("today at 21:00", MON) == "2026-11-16 21:00"
    assert chat.parse_when("tonight 9", MON) == "2026-11-16 21:00"
    assert chat.parse_when("mon 15:00", MON) == "2026-11-16 15:00"      # today's weekday, later today
    assert chat.parse_when("monday", MON) == "2026-11-16"
    for bad in ("someday", "tonight", "fri 25:00", "2026-13-01", "frisbee 3pm",
                "fri 3", "tomorrow 9"):                                   # a bare 1-11: am or pm?
        assert chat.parse_when(bad, MON) is None, bad


@pytest.mark.parametrize("text,want", [
    ("what goes out tomorrow?", ("releases", {"day": "tomorrow"})),
    ("What's going out on Friday", ("releases", {"day": "friday"})),
    ("schedule", ("releases", {"day": "today"})),
    ("what's waiting", ("items", {})),
    ("unslotted", ("unslotted", {})),
    ("retakes", ("retakes", {})),
    ("health", ("health", {})),
    ("costs this month", ("costs", {"period": "month"})),
    ("approve s02", ("approve", {"item": "s02"})),
    ("approve kick-1/s02 lovely", ("approve", {"item": "kick-1/s02", "note": "lovely"})),
    ("reject c03 too long", ("reject", {"item": "c03", "reason": "too long"})),
    ("reject c03", ("reject", {"item": "c03"})),
    ("release s04", ("hold", {"item": "s04", "release": True})),
    ("pin c03 kick-131151926 fri 15:00", ("pin", {"item": "c03 kick-131151926", "when": "2026-11-20 15:00"})),
    ("approve s02 from kick-131151926", ("approve", {"item": "s02 kick-131151926"})),
    ("reject s02 on kick-131151926 too long", ("reject", {"item": "s02 kick-131151926", "reason": "too long"})),
    ("unpin c03", ("pin", {"item": "c03", "unpin": True})),
    ("slot v05", ("slot", {"item": "v05"})),
    ("retitle s02 A Better Title", ("retitle", {"item": "s02", "title": "A Better Title"})),
    ("new cover s02", ("cover_redo", {"item": "s02"})),
    ("story: the streamer banned from Kick after the call", ("story", {"brief": "the streamer banned from Kick after the call"})),
    ("s02", ("item", {"item": "s02"})),
])
def test_local_matches(text, want):
    assert chat.match(text, MON) == want


@pytest.mark.parametrize("text", ["approve the blade one", "pin c03 someday", "hold s02 please",
                                  "put the blade segment on friday at 3", "upload everything now", "hi"])
def test_the_rest_goes_to_the_model(text):
    assert chat.match(text, MON) is None


def test_a_read_answers_at_once(cfg, no_model):
    v = chat.handle(cfg, "what goes out tomorrow")
    assert [m["who"] for m in v["messages"]] == ["me", "clippa"]
    assert v["messages"][1]["text"] == "Nothing goes out tomorrow."
    assert v["full"] and v["topic"]


def test_topics_new_and_receipts(cfg, no_model):
    chat.handle(cfg, "health")
    first = chat.load(cfg)["topics"][-1]["id"]
    v = chat.handle(cfg, "/new")
    div = [m for m in v["messages"] if m["kind"] == "divider"]
    assert div and div[0]["receipt"] == {"messages": 2, "actions": 0, "usd": 0.0}
    c = chat.load(cfg)
    assert c["topics"][0]["id"] == first and c["topics"][0]["ended_at"] and not c["topics"][-1].get("ended_at")
    chat.handle(cfg, "/bye")
    assert chat.current_topic(chat.load(cfg)) is None
    assert "no topic open" in chat.handle(cfg, "/bye")["messages"][-1]["text"]


def _age(cfg, hours: float) -> None:
    """Make every stored message (and topic start) `hours` older."""
    with chat._update(cfg) as c:
        for m in c["messages"]:
            m["at"] = (datetime.fromisoformat(m["at"]) - timedelta(hours=hours)).isoformat()
        for t in c["topics"]:
            t["started_at"] = (datetime.fromisoformat(t["started_at"]) - timedelta(hours=hours)).isoformat()


def test_quiet_hours_start_a_topic_and_expire_cards(cfg, monkeypatch, no_model):
    monkeypatch.setattr(catalog, "prepare", lambda cfg, name, args: card_plan())
    applied = []
    monkeypatch.setattr(catalog, "apply", lambda cfg, card: applied.append(card) or ("done", True))
    v = chat.handle(cfg, "approve s02")
    card = v["messages"][-1]["card"]
    assert card["state"] == "open" and card["risk"] == "write"
    _age(cfg, 3)
    v = chat.state(cfg, v["last"])            # the poll ends the quiet topic
    assert v["full"] and v["cards"][card["id"]] == "expired" and not v["topic"]
    v = chat.tap(cfg, card["id"], True)
    assert "expired" in v["messages"][-1]["text"] and applied == []


def test_a_tap_acts_once(cfg, monkeypatch, no_model):
    monkeypatch.setattr(catalog, "prepare", lambda cfg, name, args: card_plan())
    applied = []
    monkeypatch.setattr(catalog, "apply", lambda cfg, card: applied.append(card["ref"]) or ("Approved: s02.", True))
    card = chat.handle(cfg, "approve s02")["messages"][-1]["card"]
    v = chat.tap(cfg, card["id"], True)
    assert applied == ["kick-1/s02_blade"] and v["messages"][-1]["kind"] == "report"
    assert v["cards"][card["id"]] == "done"
    v = chat.tap(cfg, card["id"], True)
    assert applied == ["kick-1/s02_blade"] and "is done" in v["messages"][-1]["text"]
    assert chat.receipt(chat.load(cfg), chat.current_topic(chat.load(cfg)))["actions"] == 1


def test_a_stale_card_does_nothing(cfg, monkeypatch, no_model):
    monkeypatch.setattr(catalog, "prepare", lambda cfg, name, args: card_plan())
    monkeypatch.setattr(catalog, "apply", lambda cfg, card: ("s02 changed since I asked: nothing done.", False))
    card = chat.handle(cfg, "approve s02")["messages"][-1]["card"]
    assert chat.tap(cfg, card["id"], True)["cards"][card["id"]] == "stale"


def test_no_closes_the_card(cfg, monkeypatch, no_model):
    monkeypatch.setattr(catalog, "prepare", lambda cfg, name, args: card_plan())
    monkeypatch.setattr(catalog, "apply", lambda cfg, card: pytest.fail("acted on a No"))
    card = chat.handle(cfg, "approve s02")["messages"][-1]["card"]
    assert chat.tap(cfg, card["id"], False)["cards"][card["id"]] == "cancelled"
    with pytest.raises(ClipperError, match="no such card"):
        chat.tap(cfg, "k0", True)


def test_clear_archives(cfg, no_model):
    chat.handle(cfg, "health")
    v = chat.handle(cfg, "/clear")
    arch = list((cfg["paths"]["chats"] / "archive").glob("*-clear.json"))
    assert len(arch) == 1
    old = json.loads(arch[0].read_text(encoding="utf-8"))
    assert [m["text"] for m in old["messages"] if m["who"] == "me"] == ["health"]
    c = chat.load(cfg)
    assert [m["kind"] for m in c["messages"]] == ["divider"] and len(c["topics"]) == 1
    assert "archive/" in v["messages"][-1]["text"]


def test_the_model_sees_only_this_topic_and_picks_a_tool(cfg, monkeypatch):
    chat.handle(cfg, "health")                 # local: no model
    chat.handle(cfg, "/new")
    seen = {}

    def ask(cfg_, c, topic):
        seen["history"] = chat.history(c, topic, 20)
        return "On it.", {"name": "pin", "input": {"item": "blade", "when": "2026-11-20 15:00"}}, 0.0021
    monkeypatch.setattr(chat, "ask_model", ask)
    monkeypatch.setattr(catalog, "prepare", lambda cfg, name, args: card_plan(text=f"Pin {args['item']}?"))
    v = chat.handle(cfg, "put the blade segment out friday at 3")
    assert seen["history"] == [{"role": "user", "content": "put the blade segment out friday at 3"}]
    said, card = v["messages"][-2], v["messages"][-1]
    assert said["text"] == "On it." and said["usd"] == 0.0021 and card["card"]["text"] == "Pin blade?"


def test_a_model_pick_outside_the_catalog_is_refused(cfg, monkeypatch):
    monkeypatch.setattr(chat, "ask_model", lambda c, ch, t: ("", {"name": "upload", "input": {}}, 0.001))
    v = chat.handle(cfg, "upload everything")
    assert "isn't something I can do" in v["messages"][-1]["text"]
    assert v["messages"][-1].get("card") is None


def test_model_outage_and_budget(cfg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("overloaded")
    monkeypatch.setattr(llm.LLM, "tool_call", boom)
    assert "couldn't think" in chat.handle(cfg, "hello there")["messages"][-1]["text"]
    today = datetime.now(UTC).isoformat()
    cfg["paths"]["costs"].write_text(json.dumps({"ts": today, "kind": "llm", "stage": "chat", "usd": 0.6}))
    assert "daily_usd" in chat.handle(cfg, "hello again")["messages"][-1]["text"]   # no call made
    cfg["chat"]["model"] = ""
    assert "model is off" in chat.handle(cfg, "and again")["messages"][-1]["text"]


def test_ask_options_run_the_same_tool(cfg, monkeypatch, no_model):
    calls = []

    def prepare(cfg, name, args):
        calls.append((name, args))
        if args["item"] == "s0":
            return catalog.Plan("ask", text="Which one?", options=[{"ref": "kick-1/s02_a", "title": "A"},
                                                                    {"ref": "kick-1/s07_b", "title": "B"}])
        return card_plan()
    monkeypatch.setattr(catalog, "prepare", prepare)
    m = chat.handle(cfg, "pin s0 fri 15:00")["messages"][-1]
    assert m["ask"] == {"tool": "pin", "args": {"when": "2026-11-20 15:00"}}
    v = chat.pick(cfg, m["id"], "kick-1/s07_b")
    assert calls[-1] == ("pin", {"when": "2026-11-20 15:00", "item": "kick-1/s07_b"})
    assert v["messages"][-1]["card"]
    with pytest.raises(ClipperError):
        chat.pick(cfg, m["id"], "kick-9/x")


def test_jobs_report_into_the_open_topic_or_wait_while_away(cfg, monkeypatch, no_model):
    started = []
    monkeypatch.setattr(chat.threading.Thread, "start", lambda self: started.append(self.name))
    plan = card_plan(tool="story", risk=catalog.SPEND, argv=["story", "new", "a brief"], estimate="~$0.30",
                     ref="", snapshot={})   # a story has no item to check
    monkeypatch.setattr(catalog, "prepare", lambda cfg, name, args: plan)
    card = chat.handle(cfg, "story: something happened to someone")["messages"][-1]["card"]
    assert card["estimate"] == "~$0.30"
    v = chat.tap(cfg, card["id"], True)
    assert started and "Started `clipper story new`" in v["messages"][-1]["text"]
    job = chat.load(cfg)["jobs"][-1]
    assert job["state"] == "running" and job["argv"] == ["story", "new", "a brief"]
    assert chat.state(cfg)["jobs"][0]["tool"] == "story"
    # a second one while it runs is refused
    card2 = chat.handle(cfg, "story: something else happened again")["messages"][-1]["card"]
    assert "running already" in chat.tap(cfg, card2["id"], True)["messages"][-1]["text"]
    chat.handle(cfg, "/away")
    chat.finish(cfg, job["id"], 0, ["dossier written"])
    assert not any(m["kind"] == "report" and "is done" in m["text"] for m in chat.load(cfg)["messages"])
    v = chat.handle(cfg, "/back")
    assert any("`clipper story new` is done." in m["text"] and "dossier written" in m["text"] for m in v["messages"])
    assert chat.load(cfg)["jobs"][-1]["state"] == "done"


def test_a_forbidden_job_never_runs(cfg):
    with pytest.raises(ClipperError, match="isn't a job"):
        chat.start_job(cfg, {"tool": "story", "argv": ["upload"], "id": "k1"})
    with pytest.raises(ClipperError, match="isn't a job"):
        chat.start_job(cfg, {"tool": "approve", "argv": ["story", "new", "x"], "id": "k1"})


def test_lost_jobs_after_a_restart(cfg, no_model):
    with chat._update(cfg) as c:
        c["jobs"] = [{"id": "j1", "tool": "story", "argv": ["story", "new"], "state": "running",
                      "started_at": chat.now_iso(), "host": chat.socket.gethostname(), "boot": "old"},
                     {"id": "j2", "tool": "story", "argv": ["story", "new"], "state": "running",
                      "started_at": chat.now_iso(), "host": "another-machine", "boot": "old"}]
    assert chat.lost_jobs(cfg) == 1
    assert [j["state"] for j in chat.load(cfg)["jobs"]] == ["lost", "running"]


def test_archives_never_share_a_name(cfg):
    a = chat._archive(cfg, {"messages": [1]}, "overflow")
    b = chat._archive(cfg, {"messages": [2]}, "overflow")     # the same second
    assert a != b and len(list(a.parent.glob("*.json"))) == 2


def test_the_daily_budget_counts_calls_in_flight(cfg, monkeypatch):
    monkeypatch.setattr(chat, "_BUDGET", {"day": "", "spent": 0.0, "held": 0.0})
    cfg["chat"]["daily_usd"] = 0.01
    assert chat._reserve(cfg, 0.006)
    assert not chat._reserve(cfg, 0.006)          # the first is still in flight
    chat._settle(0.006, 0.002)                    # it cost less than held
    assert chat._reserve(cfg, 0.006)
    chat._settle(0.006, 0.008)                    # now at the cap
    assert not chat._reserve(cfg, 0.0)            # a model with no price still stops there
    cfg["chat"]["daily_usd"] = 0                  # 0: no cap
    assert chat._reserve(cfg, 5.0)


def test_an_answer_after_its_topic_ended_is_no_card(cfg, monkeypatch):
    def ask(cfg_, seen, topic):
        chat.handle(cfg_, "/new")                 # another device starts a topic meanwhile
        return "Here you go.", {"name": "approve", "input": {"item": "s02"}}, 0.001
    monkeypatch.setattr(chat, "ask_model", ask)
    monkeypatch.setattr(catalog, "prepare", lambda *a: card_plan())
    chat.handle(cfg, "approve the blade one please")
    c = chat.load(cfg)
    last = c["messages"][-1]
    assert not last.get("card") and "topic ended before I answered" in last["text"]
    first = c["topics"][0]
    assert last["topic"] == first["id"] and first["receipt"]["usd"] == 0.001


def test_a_card_cut_off_by_a_restart_fails(cfg, no_model):
    with chat._update(cfg) as c:
        chat.ensure_topic(c, cfg)
        m = chat.say(c, card_plan())
        m["card"].update(state="working", boot="old")
    assert chat.lost_jobs(cfg) == 1
    c = chat.load(cfg)
    assert chat.find_card(c, m["card"]["id"])["card"]["state"] == "failed"
    assert "restart cut off" in c["messages"][-1]["text"]


def test_a_tool_error_is_a_chat_line(cfg, monkeypatch, no_model):
    def boom(cfg_, args):
        raise ValueError("bad input")
    monkeypatch.setattr(catalog, "prepare", lambda cfg_, name, args: boom(cfg_, args))
    v = chat.handle(cfg, "approve s02")
    assert [m["who"] for m in v["messages"]] == ["me", "clippa"]
    assert "ValueError" in v["messages"][-1]["text"]


def test_trim_keeps_the_open_topic(cfg, monkeypatch, no_model):
    monkeypatch.setattr(chat, "KEEP", 4)
    with chat._update(cfg) as c:
        chat.ensure_topic(c, cfg)
        for i in range(3):
            chat._post(c, "me", f"old {i}")
        chat.end_topic(c, "/new")
        chat.open_topic(c)
        card = chat.say(c, card_plan())
        for i in range(3):
            chat._post(c, "me", f"new {i}")
        chat._trim(cfg, c)
        assert chat.find_card(c, card["card"]["id"])               # the open topic's card stays
        assert not any(m["text"].startswith("old") for m in c["messages"])
    assert list((cfg["paths"]["chats"] / "archive").glob("*-overflow.json"))


def test_box_commands(cfg, no_model):
    assert "approve s02" in chat.handle(cfg, "/help")["messages"][-1]["text"]
    assert "This topic: $0.000" in chat.handle(cfg, "/cost")["messages"][-1]["text"]
    assert "No earlier topics" in chat.handle(cfg, "/history")["messages"][-1]["text"]
    chat.handle(cfg, "/new")
    assert "Earlier topics" in chat.handle(cfg, "/history")["messages"][-1]["text"]
    assert "I know" in chat.handle(cfg, "/dance")["messages"][-1]["text"]
    with pytest.raises(ClipperError):
        chat.handle(cfg, "   ")
    with pytest.raises(ClipperError):
        chat.handle(cfg, "x" * (chat.MAX_TEXT + 1))


def test_poll_since(cfg, no_model):
    v = chat.handle(cfg, "health")
    assert chat.state(cfg, v["last"])["messages"] == [] and not chat.state(cfg, v["last"])["full"]
    assert chat.state(cfg, "m-unknown")["full"]


def test_system_prompt_has_the_date_and_shows(cfg):
    cfg["shows"] = {"kino": {"overrides": {"meta": {"show_name": "Kino Casino", "cast": ["Andy", "PPP"]}}}}
    text = chat.system_prompt(cfg, MON)
    assert "Monday 2026-11-16 10:00" in text and "kino: Kino Casino (cast: Andy, PPP)" in text
    assert "{" not in text


def test_routes_check_their_inputs(cfg, no_model):
    assert routes.chat_send(cfg, {"text": "health"})["messages"][-1]["who"] == "clippa"
    with pytest.raises(ClipperError, match="should be text"):
        routes.chat_send(cfg, {"text": ["x"]})
    with pytest.raises(ClipperError, match="no such card"):
        routes.chat_card(cfg, {"id": "nope", "yes": True})
    assert routes.chat_get(cfg, "")["messages"]


def test_tool_call_on_claude(defaults, monkeypatch):
    monkeypatch.setattr(costs, "llm_call", lambda *a, **k: None)
    sent = {}
    resp = SimpleNamespace(
        model="claude-haiku-5-5", stop_reason="tool_use", usage=SimpleNamespace(input_tokens=900, output_tokens=40),
        content=[SimpleNamespace(type="text", text="Sure."),
                 SimpleNamespace(type="tool_use", name="pin", input={"item": "s02", "when": "2026-11-20 15:00"})])
    client = llm.LLM(defaults, label="chat")
    client._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: sent.update(kw) or resp))
    out, meta = client.tool_call("claude-haiku-5-5", "sys", [{"role": "user", "content": "pin s02 fri 3"}],
                                 [{"name": "pin", "description": "d", "input_schema": {"type": "object"}}])
    assert out == {"text": "Sure.", "tool": {"name": "pin", "input": {"item": "s02", "when": "2026-11-20 15:00"}}}
    assert sent["tool_choice"] == {"type": "auto"} and sent["tools"][0]["name"] == "pin"
    assert meta["usd"] > 0


def test_tool_call_cut_off_is_an_error(defaults, monkeypatch):
    monkeypatch.setattr(costs, "llm_call", lambda *a, **k: None)
    resp = SimpleNamespace(
        model="claude-haiku-5-5", stop_reason="max_tokens", usage=SimpleNamespace(input_tokens=900, output_tokens=1024),
        content=[SimpleNamespace(type="tool_use", name="retitle", input={"item": "s02"})])
    client = llm.LLM(defaults, label="chat")
    client._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))
    with pytest.raises(RuntimeError, match="max_tokens"):
        client.tool_call("claude-haiku-5-5", "sys", [{"role": "user", "content": "q"}],
                         [{"name": "retitle", "description": "d", "input_schema": {"type": "object"}}])


def test_tool_call_on_an_openai_compatible_server(defaults, monkeypatch):
    import requests

    monkeypatch.setattr(costs, "llm_call", lambda *a, **k: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    sent = []
    data = {"id": "r", "model": "x", "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"function": {"name": "releases", "arguments": '{"day": "tomorrow"}'}}]}}]}
    monkeypatch.setattr(requests, "post", lambda url, json, headers, timeout: sent.append(json) or SimpleNamespace(
        status_code=200, ok=True, headers={}, text="", json=lambda: data))
    out, _ = llm.LLM(defaults).tool_call("openrouter:x/y", "sys", [{"role": "user", "content": "q"}],
                                         [{"name": "releases", "description": "d", "input_schema": {"type": "object"}}])
    assert out == {"text": "", "tool": {"name": "releases", "input": {"day": "tomorrow"}}}
    assert sent[0]["tools"][0]["function"]["name"] == "releases" and sent[0]["messages"][0]["role"] == "system"


def test_the_ledger_takes_the_clients_label(tmp_path, monkeypatch):
    rows = []
    monkeypatch.setattr(costs, "record", lambda path, rec: rows.append(rec))
    costs.set_context("serve", "kick-1")
    costs.llm_call(tmp_path / "c.jsonl", "anthropic", "claude-haiku-5-5", 1, 1, 0.1, stage="chat")
    costs.llm_call(tmp_path / "c.jsonl", "anthropic", "claude-haiku-5-5", 1, 1, 0.1)
    assert (rows[0]["stage"], rows[0]["stream"]) == ("chat", "")
    assert (rows[1]["stage"], rows[1]["stream"]) == ("serve", "kick-1")
    costs.set_context("", "")


# ---------- the looks (web/themes/): one pick dresses the war room and the chat ----------

WEB = Path(routes.__file__).resolve().parents[1] / "web"
STRUCTURE = ("warroom.css", "chat.css")   # layout only: the look's custom properties fill them in


def _defined(css: str) -> dict[str, str]:
    """--name: value pairs of a stylesheet's first :root block."""
    block = re.search(r":root\s*\{(.*?)\n\}", css, re.S).group(1)
    return {m[0]: " ".join(m[1].split()) for m in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", block)}


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def theme_variables() -> set[str]:
    """What a look must define: every property the two pages' structure reads, but those the
    structure sets itself (the chat dock's width) and those the war room's script sets (layouts)."""
    used, own = set(), set()
    for name in STRUCTURE:
        css = _read(name)
        used |= set(re.findall(r"var\(--([\w-]+)", css))
        own |= set(re.findall(r"(?<![\w-])--([\w-]+)\s*:", css))
    scripts = " ".join(p.read_text(encoding="utf-8") for p in (WEB / "js").glob("*.js"))
    used |= set(re.findall(r"var\(--([\w-]+)", scripts))   # the war room's inline styles
    own |= set(re.findall(r"setProperty\(\"--([\w-]+)\"", scripts))
    labels = {f"label-{n}" for n in re.findall(r'data-label="([\w-]+)"', _read("chat.html"))}
    assert labels == {"label-buddies", "label-chat"}
    return (used - own) | labels


def test_every_theme_defines_every_variable():
    want = theme_variables()
    assert {"desk", "win", "tb", "btn", "panel2", "accent", "field", "sel"} <= want   # both pages' vocabulary
    assert {"millennium", "pager", "retro", "warroom"} <= set(routes.themes())   # a new look is just a file
    for name in routes.themes():
        have = set(_defined((WEB / "themes" / f"{name}.css").read_text(encoding="utf-8")))
        assert not want - have, (name, sorted(want - have))


# The colours warroom.css may still spell out: black behind video and covers, the dark badges
# over them and their text, the same in every look. Anything else belongs in a theme.
THEME_FREE = {"#000", "#05070a", "#000c", "#0007", "#000b", "#fff", "#111", "#e6e9ee"}


def test_the_warroom_theme_is_the_one_source():
    """The war room's colours live in themes/warroom.css only: its structure defines no palette
    and spells out no colour a look could want to change."""
    for name in STRUCTURE:
        assert ":root" not in _read(name), name
    room = re.sub(r"/\*.*?\*/", "", _read("warroom.css"), flags=re.S)
    literal = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", room)) | set(re.findall(r"\brgba?\([^)]*\)", room))
    assert literal <= THEME_FREE, sorted(literal - THEME_FREE)
    palette = _defined((WEB / "themes" / "warroom.css").read_text(encoding="utf-8"))
    assert (palette["bg"], palette["panel"], palette["accent"]) == ("#0b0d10", "#12161b", "#ffd400")   # the war room as it was


def test_both_pages_wear_a_look_through_theme_js():
    """The look's file comes after the page's structure (so it wins) and theme.js right after it
    (so a pick is on before the first paint); the page's default is a look that exists."""
    for page, at in (("warroom.html", ""), ("chat.html", "/")):
        html = _read(page)
        m = re.search(r'<html lang="en" data-theme="([\w-]+)">', html)
        assert m and m[1] in routes.themes(), page
        link = f'<link rel="stylesheet" href="{at}themes/{m[1]}.css" id="theme">'
        structure = f'<link rel="stylesheet" href="{at}{page.replace(".html", ".css")}">'
        assert html.index(structure) < html.index(link) < html.index(f'<script src="{at}theme.js"') < html.index("</head>"), page
    assert _read("warroom.html").count('data-theme="warroom"') == 1   # the war room as it was until a pick


def test_the_default_theme_comes_from_config(cfg):
    assert routes.chat_themes(cfg)["default"] == "retro"
    cfg["chat"]["theme"] = "pager"
    assert routes.chat_themes(cfg)["default"] == "pager"
    cfg["chat"]["theme"] = "no-such-look"
    assert routes.chat_themes(cfg)["default"] == routes.themes()[0]


def test_the_page_names_no_real_messenger():
    """Era homage only: the repo is public."""
    files = [WEB / "chat.html", WEB / "chat.css", WEB / "chat.js", WEB / "theme.js", *(WEB / "themes").glob("*.css"),
             WEB / "warroom.html", WEB / "warroom.css", WEB / "js" / "chatdock.js"]
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"\bAIM\b", text), f.name                 # the word "aim" is fine
        assert not re.search(r"\b(icq|aol|msn|yahoo|trillian|messenger\.com)\b", text.lower()), f.name


@pytest.mark.parametrize("text,want", [
    ("approve all comps", ("bulk", {"verb": "approve", "kind": "compilation"})),
    ("approve comps", ("bulk", {"verb": "approve", "kind": "compilation"})),
    ("Approve all the shorts waiting", ("bulk", {"verb": "approve", "kind": "short"})),
    ("hold every segment from kino 11-14", ("bulk", {"verb": "hold", "kind": "segment", "stream": "kino 11-14"})),
    ("release all comps on kick-131151926", ("bulk", {"verb": "release", "kind": "compilation",
                                                      "stream": "kick-131151926"})),
    ("approve all comps from last night", ("bulk", {"verb": "approve", "kind": "compilation", "stream": "last"})),
    ("approve everything from the kino one", ("bulk", {"verb": "approve", "stream": "kino"})),
    ("reject all clips from c404 too long", ("bulk", {"verb": "reject", "kind": "pick", "stream": "c404",
                                                      "reason": "too long"})),
    ("approve c01 and c02", ("bulk", {"verb": "approve", "items": ["c01", "c02"]})),
    ("approve c01, c02 & comp-dsp", ("bulk", {"verb": "approve", "items": ["c01", "c02", "comp-dsp"]})),
    ("approve s07 and s09 from the kino one pls", ("bulk", {"verb": "approve", "items": ["s07", "s09"],
                                                            "stream": "kino"})),
    ("reject c01 and c02 boring", ("bulk", {"verb": "reject", "items": ["c01", "c02"], "reason": "boring"})),
    ("approve s02 from kino 11-14", ("approve", {"item": "s02 kino 11-14"})),
    ("pin s01 from kino 11-14 for fri 9am", ("pin", {"item": "s01 kino 11-14", "when": "2026-11-20 09:00"})),
    ("reject c01 dupe of c02", ("reject", {"item": "c01", "reason": "dupe of c02"})),   # a reason may name one
    ("pin s01 on friday 9am", ("pin", {"item": "s01", "when": "2026-11-20 09:00"})),   # a day, not a show
    ("approve s02 on fire", ("approve", {"item": "s02", "note": "on fire"})),           # a note, not a show
    ("approve s02 on the kino one", ("approve", {"item": "s02 kino"})),
    ("undo that", ("undo", {})),
    ("undo", ("undo", {})),
    ("whats waiting", ("items", {})),
    ("whats unslotted", ("unslotted", {})),
])
def test_several_at_once_and_looser_phrases(text, want):
    assert chat.match(text, MON) == want


@pytest.mark.parametrize("text", [
    "approve s01 and s02 then pin them",   # words after the names: not a note, the model's turn
    "approve s01 dupe of s02",             # a second name in an approve's note
    "hold the blade comps",                # title words: the model's
    "approve comp",                        # one, not a kind: not a bulk ask
])
def test_unclear_bulk_goes_to_the_model(text):
    assert chat.match(text, MON) is None


def test_a_typed_yes_answers_the_one_open_card(cfg, monkeypatch, no_model):
    monkeypatch.setattr(catalog, "prepare", lambda cfg, name, args: card_plan())
    applied = []
    monkeypatch.setattr(catalog, "apply", lambda cfg, card: applied.append(card["id"]) or ("Approved.", True))
    chat.handle(cfg, "approve s02")
    v = chat.handle(cfg, "ok do it")
    assert len(applied) == 1 and [m["who"] for m in v["messages"]] == ["me", "clippa"]
    assert v["messages"][-1]["text"] == "Approved."
    # two open cards: a "yes" could mean either, so it goes to the model
    chat.handle(cfg, "approve s02")
    chat.handle(cfg, "approve s03")
    monkeypatch.setattr(chat, "ask_model", lambda cfg, c, t: ("Which one?", None, 0.0))
    assert chat.handle(cfg, "yes")["messages"][-1]["text"] == "Which one?"
    assert len(applied) == 1
    # a no closes the one open card
    chat.handle(cfg, "/new")
    chat.handle(cfg, "approve s02")
    assert chat.handle(cfg, "nope")["messages"][-1]["text"] == "OK, left alone."
    assert len(applied) == 1
    # Clippa said something after the card: an "ok" answers that, the card waits for its button
    chat.handle(cfg, "/new")
    chat.handle(cfg, "approve s02")
    monkeypatch.setattr(catalog, "prepare", lambda cfg, name, args: catalog.Plan("answer", text="Nothing goes out."))
    chat.handle(cfg, "what goes out tomorrow")
    monkeypatch.setattr(chat, "ask_model", lambda cfg, c, t: ("Cool.", None, 0.0))
    assert chat.handle(cfg, "ok")["messages"][-1]["text"] == "Cool." and len(applied) == 1
    # a spend is never typed into: its button says what it costs
    chat.handle(cfg, "/new")
    monkeypatch.setattr(catalog, "prepare", lambda cfg, name, args: card_plan(risk=catalog.SPEND, tool="story"))
    chat.handle(cfg, "approve s02")
    assert chat.handle(cfg, "yes")["messages"][-1]["text"] == "Cool." and len(applied) == 1
