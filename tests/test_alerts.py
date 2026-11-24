"""Discord DM alerts (clippa/alerts.py): the [notify] time strings the bot's loop reads
every minute are refused with a message, never a traceback that kills the loop."""
import pytest

from clippa import alerts
from clippa.errors import ClipperError


def test_a_bad_notify_time_is_a_clear_error_in_the_loop():
    with pytest.raises(ClipperError, match="isn't HH:MM"):
        alerts._hm("9:00pm")
    assert alerts._hm(" 07:30 ") == (7, 30)


def test_ready_spend_and_errors_dm_as_embeds(defaults, tmp_path, monkeypatch):
    defaults["paths"]["streams"] = tmp_path
    defaults["notify"] = {**defaults["notify"], "quiet_hours": "", "disk_free_gb": 0,
                          "digest_time": "", "kinds": ["ready", "spend", "failure", "digest"]}
    monkeypatch.setattr(alerts, "checks", lambda *a, **k: None)
    alerts.send(defaults, "ready", "Stream A finished processing. 7 cuts in review.",
                title="Stream A", short="kick-1")
    alerts.send(defaults, "spend", "Modal this month: $4.12 metered, $4.00 billed after $0 credit")
    alerts.send(defaults, "failure", "Review check did not finish on Cut 07. Try again.", title="Cut 07")
    alerts.send(defaults, "digest", "Today: 3 in review.")
    payloads, files, _ = alerts.pending(defaults, link="https://war.example")
    assert len(files) == 4
    embeds = [p["embed"] for p in payloads if p.get("embed")]
    assert [e["title"] for e in embeds] == [
        "📋 In review · Stream A", "💸 Cost · Modal", "❌ Error · Cut 07"]
    assert embeds[0]["color"] == 0x7EB8C9 and "7 cuts in review" in embeds[0]["description"]
    assert embeds[0]["footer"]["text"].startswith("clippa · kick-1 · ")
    assert embeds[1]["color"] == 0xD4A054 and "$4.12" in embeds[1]["description"]
    assert embeds[2]["color"] == 0xD07070
    assert all("components" not in e for e in embeds)
    text = next(p for p in payloads if p.get("content") and "Today" in p["content"])
    assert "War room: https://war.example" in payloads[-1]["content"] or "War room:" in text["content"]
    assert "we" not in " ".join(e["title"] + e["description"] for e in embeds).lower()


def test_a_digest_that_could_not_be_queued_is_tried_again(defaults, monkeypatch):
    from datetime import datetime

    cfg = {**defaults, "notify": {**defaults["notify"], "digest_time": "20:00", "disk_free_gb": 0, "kinds": []}}
    queued = iter([False, True])
    monkeypatch.setattr(alerts, "send", lambda *a, **k: next(queued))
    st, now = {"disk_at": 0, "digest": ""}, datetime(2026, 11, 21, 20, 5)
    alerts.checks(cfg, st, now, digest=lambda cfg, now: "today")
    assert st["digest"] == ""  # the outbox write failed: not marked sent
    alerts.checks(cfg, st, now, digest=lambda cfg, now: "today")
    assert st["digest"] == "2026-11-21"


def test_the_war_room_link_never_cuts_a_full_message():
    full = [{"content": "x" * 1890}]
    alerts._attach_link(full, "https://war.example")
    assert full[0]["content"] == "x" * 1890 and full[1] == {"content": "War room: https://war.example"}
    room = [{"embed": {}}, {"content": "short"}]
    alerts._attach_link(room, "https://war.example")
    assert room[-1]["content"] == "short\nWar room: https://war.example" and len(room) == 2
    alerts._attach_link(room, "https://war.example")   # once
    assert room[-1]["content"].count("War room:") == 1


def test_a_held_embed_says_when_it_happened(defaults, tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from clippa import jsonstate

    defaults["paths"]["streams"] = tmp_path
    defaults["notify"] = {**defaults["notify"], "quiet_hours": "", "disk_free_gb": 0,
                          "digest_time": "", "kinds": ["failure"]}
    monkeypatch.setattr(alerts, "checks", lambda *a, **k: None)
    alerts.send(defaults, "failure", "Review check did not finish on Cut 07.", title="Cut 07")
    f = next(alerts.outbox(defaults).glob("*.json"))
    ev = jsonstate.load(f)
    ev["at"] = (datetime.now().astimezone() - timedelta(hours=3)).isoformat(timespec="seconds")
    jsonstate.write(f, ev)
    payloads, _, _ = alerts.pending(defaults)
    assert payloads[0]["embed"]["description"].startswith("[")
    assert "Review check did not finish" in payloads[0]["embed"]["description"]


def test_an_outbox_file_without_a_time_is_still_sent(defaults, tmp_path, monkeypatch):
    from clippa import jsonstate

    defaults["paths"]["streams"] = tmp_path
    defaults["notify"] = {**defaults["notify"], "quiet_hours": "", "disk_free_gb": 0,
                          "digest_time": "", "kinds": ["failure"]}
    monkeypatch.setattr(alerts, "checks", lambda *a, **k: None)
    alerts.send(defaults, "failure", "Cut 07 failed.", key="a", title="Cut 07")
    alerts.send(defaults, "failure", "Cut 08 failed.", key="b", title="Cut 08")
    first = sorted(alerts.outbox(defaults).glob("*.json"))[0]
    ev = jsonstate.load(first)
    ev["at"] = ""
    jsonstate.write(first, ev)
    payloads, files, _ = alerts.pending(defaults)
    assert len(files) == 2 and len([p for p in payloads if p.get("embed")]) == 2
