"""Night Watch Discord cards: palette, title, voice, footer, countdown, re-split button."""
import re
from datetime import UTC, datetime, timedelta

from clippa import notify

WE = re.compile(r"\b(we|us|our)\b", re.IGNORECASE)


def row(**kw):
    base = {
        "kind": "short", "duration_s": 93, "score": "7.8", "category": "joke", "found_by": "chat",
        "hook": "the line", "topic": "", "decision": "pending", "hold": False, "publish_at": "",
        "scheduled": "", "uploaded": False, "comment": "", "title": "Example Short title",
        "kitsu_url": "https://k.example/x", "youtube_url": "", "short": "stream-a/c04",
        "date": "2026-11-22", "revision": 2, "checks": [], "auto": "", "auto_state": "",
        "auto_at": "", "resplit": False, "resplit_limit_s": 0, "other_voices": "", "thumb_text": "",
    }
    base.update(kw)
    return base


def blob(embed, rows=()):
    parts = [embed.get("title") or "", embed.get("description") or "",
             (embed.get("footer") or {}).get("text") or ""]
    parts += [f"{f['name']} {f['value']}" for f in embed.get("fields") or []]
    parts += [c.get("label") or "" for row in rows for c in row.get("components") or []]
    return "\n".join(parts)


def field(embed, name):
    return next(f["value"] for f in embed["fields"] if f["name"] == name)


def test_palette_integers_match_the_dark_tokens():
    assert notify.COLORS["approved"] == 0x5EA88A == 6203530
    assert notify.COLORS["held"] == notify.COLORS["changes_requested"] == 0xD4A054 == 13934676
    assert notify.COLORS["finding"] == notify.COLORS["rejected"] == 0xD07070 == 13660272
    assert notify.COLORS["pending"] == 0x6B8FD4 == 7049172
    assert notify.COLORS["resplit"] == 0xA992D4 == 11113172
    assert notify.COLORS["accent"] == 0x7EB8C9 == 8304841


def test_approve_has_an_icon_and_a_word_because_it_shares_the_logo_color():
    human, _ = notify.card(row(decision="approved", short="stream-a/c03"))
    auto, _ = notify.card(row(decision="approved", auto_state="approved",
                              auto="approved itself Sat 14:30", short="stream-a/c05"))
    assert human["color"] == auto["color"] == notify.COLORS["approved"]
    assert human["title"] == "✓ Approved · Example Short title"
    assert auto["title"] == "✓ Auto-approved · Example Short title"
    assert "Cut 03 approved." in human["description"]
    assert "Cut 05 auto-approved. No objection in the grace window." in auto["description"]
    assert field(auto, "Auto-approve") == "auto-approved; no objection in the grace window"
    assert "auto-approved" in field(auto, "Status")


def test_hold_wins_and_names_findings_or_other_voices():
    held, _ = notify.card(row(hold=True, checks=["subject name does not match the source label"]))
    assert held["color"] == notify.COLORS["held"]
    assert held["title"].startswith("⏸ Held · ")
    assert "Cut 04 held. 1 finding on this Short." in held["description"]
    assert "1 finding" in field(held, "Status")
    assert field(held, "Findings").startswith("⚠ subject name")
    voices, _ = notify.card(row(
        hold=True, short="stream-a/c08", other_voices="22 s of other voices (limit 5 s)"))
    assert "Cut 08 held for other voices. It stays off publish until someone releases it." \
        in voices["description"]


def test_a_finding_and_a_rejection_use_the_finding_color_with_different_words():
    found, _ = notify.card(row(checks=["subject name does not match the source label"],
                               auto_state="person", auto="waits for you: 1 check finding(s)"))
    assert found["color"] == notify.COLORS["finding"]
    assert found["title"].startswith("⚠ Finding · ")
    assert "Finding on Cut 04: subject name does not match the source label." in found["description"]
    assert "1 finding" in field(found, "Status") and "auto-approve" in field(found, "Status")
    rejected, _ = notify.card(row(decision="rejected", comment="off topic"))
    assert rejected["color"] == notify.COLORS["finding"]
    assert rejected["title"].startswith("✕ Rejected · ")
    changes, _ = notify.card(row(decision="changes_requested"))
    assert changes["color"] == notify.COLORS["held"]
    assert changes["title"].startswith("✎ Changes · ")


def test_auto_approve_uses_a_relative_timestamp():
    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    far = now + timedelta(hours=2)
    near = now + timedelta(minutes=11, seconds=40)
    clock = now.timestamp()
    long, _ = notify.card(row(auto_state="waiting", auto_at=far.isoformat(),
                              auto="approves itself later", short="stream-a/c05"), now=clock)
    close, _ = notify.card(row(auto_state="waiting", auto_at=near.isoformat(),
                               auto="approves itself soon", short="stream-a/c05"), now=clock)
    stamp = f"<t:{int(far.timestamp())}:R>"
    assert long["color"] == notify.COLORS["pending"]
    assert long["title"].startswith("⏱ Auto-approve · ")
    assert f"No objection yet. Cut 05 auto-approves {stamp} unless held." in long["description"]
    assert field(long, "Auto-approve") == f"auto-approves {stamp} unless held"
    assert "No objection yet" not in close["description"]
    assert f"<t:{int(near.timestamp())}:R>" in close["description"]
    assert "auto-approve" in field(long, "Status")


def test_a_note_without_a_timestamp_still_shows():
    embed, _ = notify.card(row(auto="approves itself Sun 04:00 unless you act"))
    assert field(embed, "Auto-approve") == "approves itself Sun 04:00 unless you act"


def test_times_and_scores_are_mono_and_the_footer_carries_the_tagline():
    embed, _ = notify.card(row())
    assert "`1:33`" in embed["description"]
    assert "score `7.8`" in embed["description"]
    assert embed["footer"]["text"] == (
        "clippa · stream-a/c04 · Clippa stays up. You stay in charge.")
    assert "rev " not in embed["footer"]["text"]


def test_resplit_is_a_third_row_and_row_2_stays_at_five():
    plain, rows = notify.card(row())
    assert plain["title"].startswith("⏳ Pending · ")
    assert len(rows) == 2 and len(rows[1]["components"]) == 4  # three buttons + Kitsu
    embed, rows = notify.card(row(
        kind="segment", topic="the long one", score="", found_by="", category="",
        duration_s=4200, resplit=True, resplit_limit_s=3000, short="stream-a/s06",
        title="Long topic", youtube_url="https://youtu.be/x"))
    assert embed["color"] == notify.COLORS["resplit"]
    assert embed["title"] == "✂ Re-split · Long topic"
    assert "Cut 06 is over 50 minutes. Flagged for re-split." in embed["description"]
    assert "`1:10:00`" in embed["description"]
    assert "split_over_s" in field(embed, "Re-split")
    assert len(rows) == 3
    assert len(rows[1]["components"]) == 5  # three buttons + Kitsu + YouTube
    button = rows[2]["components"][0]
    assert button["label"] == "Re-split" and button["custom_id"] == "clippa:resplit:stream-a/s06"
    assert WE.search(blob(embed, rows)) is None


def test_a_resplit_notice_has_the_button_and_no_approve_row():
    embed, rows = notify.resplit_notice(
        {"id": "s06", "title": "The long one"}, "stream-a", 3000)
    assert embed["title"] == "✂ Re-split · The long one"
    assert embed["color"] == notify.COLORS["resplit"]
    assert len(rows) == 1 and rows[0]["components"][0]["label"] == "Re-split"
    assert "Approve" not in blob(embed, rows)


def test_dm_embeds_and_presence_lines():
    ready = notify.dm_embed("ready", "Stream A finished processing. 7 cuts in review.", "Stream A")
    cost = notify.dm_embed("spend", "Modal this month: $4.12 metered")
    err = notify.dm_embed("failure", "Review check did not finish on Cut 07. Try again.", "Cut 07")
    assert ready["title"] == "📋 In review · Stream A" and ready["color"] == notify.COLORS["accent"]
    assert cost["title"] == "💸 Cost · Modal" and cost["color"] == notify.COLORS["held"]
    assert err["title"] == "❌ Error · Cut 07" and err["color"] == notify.COLORS["finding"]
    assert notify.dm_embed("digest", "today") is None
    assert notify.dm_embed("test", "hi") is None
    thread = notify.in_review_embed("Stream A", 7, 2, 5, "https://k.example/e", "stream-a")
    assert thread["title"] == "📋 In review · Stream A"
    assert "Stream A finished processing. 7 cuts in review." in thread["description"]
    assert notify.presence_line(3, 1, []) == "3 in review, 1 held."
    assert notify.presence_line(2, 0, ["Stream A"]) == "2 in review."
    assert notify.presence_line(0, 1, []) == "Waiting on you."
    assert notify.presence_line(0, 0, ["Stream A"]) == "Watching Stream A."
    assert notify.presence_line(0, 0, ["Stream A", "Channel 404"]) == "On watch."
    assert notify.presence_line(0, 0, []) == "Quiet board."
    spoken = "\n".join([
        blob(ready), blob(cost), blob(err), blob(thread),
        notify.presence_line(3, 1, []), notify.presence_line(0, 0, ["Stream A"]),
    ])
    assert WE.search(spoken) is None


def test_resplit_flag_follows_the_setting_not_a_stale_mark():
    assert notify.is_resplit("segment", "s01_topic", 4000, 0, {"s01"}) is False
    assert notify.is_resplit("segment", "s01_topic", 100, 3000, {"s01"}) is True
    assert notify.is_resplit("segment", "s02_topic", 3001, 3000, set()) is True
    assert notify.is_resplit("segment", "s02_topic", 3000, 3000, set()) is False
    assert notify.is_resplit("short", "v01_t", 4000, 3000, set()) is False


def test_copy_has_no_we():
    samples = [
        row(hold=True, checks=["a name"]),
        row(hold=True, other_voices="22 s of other voices (limit 5 s)", short="stream-a/c08"),
        row(decision="approved", short="stream-a/c03"),
        row(decision="approved", auto_state="approved", short="stream-a/c05"),
        row(decision="rejected", comment="no"),
        row(resplit=True, resplit_limit_s=3000, kind="segment", short="stream-a/s06"),
        row(auto_state="waiting", auto_at="2026-11-23T18:00:00+00:00", short="stream-a/c05"),
    ]
    for r in samples:
        embed, rows = notify.card(r, now=1_763_900_000)
        assert WE.search(blob(embed, rows)) is None
