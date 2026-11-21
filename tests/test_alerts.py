"""Discord DM alerts (clippa/alerts.py): the [notify] time strings the bot's loop reads
every minute are refused with a message, never a traceback that kills the loop."""
import pytest

from clippa import alerts
from clippa.errors import ClipperError


def test_a_bad_notify_time_is_a_clear_error_in_the_loop():
    with pytest.raises(ClipperError, match="isn't HH:MM"):
        alerts._hm("9:00pm")
    assert alerts._hm(" 07:30 ") == (7, 30)


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
