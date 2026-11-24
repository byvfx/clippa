"""A spending limit stops a retake run instead of firing the rest of the queue."""
import pytest

from clippa import jsonstate, retake, review
from clippa.errors import OpenRouterLimit


def test_an_openrouter_limit_stops_before_the_next_retake(defaults, tmp_path, monkeypatch):
    root = tmp_path / "streams"
    defaults["paths"]["streams"] = root
    for name in ("kick-a", "kick-b"):
        d = root / name
        d.mkdir(parents=True)
        jsonstate.write(d / "review_state.json", {"s01": {}})
        jsonstate.write(d / "review.json", {"s01": {
            "decision": "changes_requested", "comment": "trim the start", "comment_id": name}})
    seen = []

    def process(cfg, sdir, key, note, comment_id, dry_run=False):
        seen.append(sdir.name)
        raise OpenRouterLimit(
            "OpenRouter limit openrouter_key_limit (HTTP 402), not retried: spent",
            "openrouter_key_limit")

    monkeypatch.setattr(retake, "process", process)
    monkeypatch.setattr(review, "sync", lambda *a, **k: None)
    with pytest.raises(OpenRouterLimit):
        retake.run(defaults, sync=True)
    assert seen == ["kick-a"]
