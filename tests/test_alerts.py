"""Discord DM alerts (clippa/alerts.py): the [notify] time strings the bot's loop reads
every minute are refused with a message, never a traceback that kills the loop."""
import pytest

from clippa import alerts
from clippa.errors import ClipperError


def test_a_bad_notify_time_is_a_clear_error_in_the_loop():
    with pytest.raises(ClipperError, match="isn't HH:MM"):
        alerts._hm("9:00pm")
    assert alerts._hm(" 07:30 ") == (7, 30)
