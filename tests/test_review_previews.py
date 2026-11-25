"""Kitsu previews that fit its upload limit, and a stage that goes on when one doesn't."""
from types import SimpleNamespace

from clippa import review


def test_fit_settings_keeps_540p_for_an_hour_and_shrinks_a_five_hour_compilation():
    h, fps, v, a = review.fit_settings(480, 3600, 30)
    assert (h, fps, a) == (540, 30, 64) and 800 < v < 900
    # comp-gta-6 on kick-139915441: 18,804 s, which the old 150 kbps floor sent over 480 MB
    h, fps, v, a = review.fit_settings(480, 18804, 30)
    assert (h, fps, a) == (240, 15, 32)
    assert (v + a) * 18804 / 8e3 <= 480 * 0.9 + 1
    assert review.fit_settings(480, 7000, 30)[:2] == (360, 30)
    assert review.fit_settings(480, 10 ** 6, 30)[2] == 40   # never asks for nothing
    assert review.fit_settings(480, 0, 30)[0] == 540         # no duration: no crash


def _fake_ffmpeg(monkeypatch, sizes):
    """ffmpeg that writes its output file at the next of `sizes`; returns the calls."""
    calls = []

    def run(cmd, check):
        calls.append(cmd)
        with open(cmd[-1], "wb") as f:
            f.truncate(sizes[len(calls) - 1])

    monkeypatch.setattr(review.subprocess, "run", run)
    return calls


def test_render_to_size_tries_again_lower_when_the_encoder_overshoots(tmp_path, monkeypatch):
    rc = {"max_upload_mb": 1, "preview_fps": 30}
    big = tmp_path / "comp.mp4"
    big.write_bytes(b"x" * 2_000_000)
    calls = _fake_ffmpeg(monkeypatch, [1_500_000, 800_000])
    dst = review.render_to_size(big, big, 10, rc, "h264_qsv")
    assert dst.name == "comp.small.mp4" and dst.stat().st_size == 800_000
    first, second = (int(c[c.index("-b:v") + 1].rstrip("k")) for c in calls)
    audio = 64 * 1e3 / 8 * 10   # the fixed audio isn't part of what the video can give up
    assert second == int(first * (0.9e6 - audio) / (1.5e6 - audio))
    assert not (tmp_path / "comp.small.over").exists()
    # reused while it fits
    assert review.render_to_size(big, big, 10, rc, "h264_qsv") == dst and len(calls) == 2


def test_render_to_size_redoes_a_cached_copy_that_does_not_fit(tmp_path, monkeypatch):
    rc = {"max_upload_mb": 1, "preview_fps": 30}
    big = tmp_path / "comp.mp4"
    big.write_bytes(b"x" * 2_000_000)
    (tmp_path / "comp.small.mp4").write_bytes(b"x" * 1_200_000)   # what the old floor left
    calls = _fake_ffmpeg(monkeypatch, [900_000])
    assert review.render_to_size(big, big, 10, rc, "h264_qsv").stat().st_size == 900_000
    assert len(calls) == 1


def test_render_to_size_keeps_its_best_when_both_passes_miss(tmp_path, monkeypatch):
    """Not encoded again on every publish (hours for a 5 h compilation): marked .over."""
    rc = {"max_upload_mb": 1, "preview_fps": 30}
    big = tmp_path / "comp.mp4"
    big.write_bytes(b"x" * 2_000_000)
    calls = _fake_ffmpeg(monkeypatch, [1_500_000, 1_100_000])
    dst = review.render_to_size(big, big, 10, rc, "h264_qsv")
    assert dst.stat().st_size == 1_100_000 and (tmp_path / "comp.small.over").exists()
    assert review.render_to_size(big, big, 10, rc, "h264_qsv") == dst and len(calls) == 2


def test_push_previews_skips_one_too_big_and_publishes_the_rest(tmp_path, capsys,
                                                                 monkeypatch):
    published = []

    def publish_preview(task, status, comment, preview_file_path, **kw):
        published.append(preview_file_path)
        return None, {"id": "p1", "revision": 1}

    gazu = SimpleNamespace(
        shot=SimpleNamespace(get_sequence_by_name=lambda *a, **k: {"id": "seq"}),
        task=SimpleNamespace(get_task=lambda *a: None, publish_preview=publish_preview))
    monkeypatch.setattr(review, "ensure_task", lambda gazu, entity, ttype: {"id": "t1"})
    items = []
    for key, size in (("comp-gta-6", 2_000_000), ("comp-h3", 500_000)):
        f = tmp_path / f"{key}.mp4"
        f.write_bytes(b"x" * size)
        items.append({"key": key, "kind": "compilation", "seq": key, "seq_title": key,
                      "preview": f, "file": f, "duration_s": 60, "text": "",
                      "type": "Compilation", "meta": {"id": key}})
    rc = {"max_upload_mb": 1, "preview_fps": 30, "normalize": False,
          "host": "http://kitsu.test/api"}
    _, keys, skipped = review.push_previews(gazu, tmp_path, rc, {"id": "pr"}, {"id": "ep"},
                                            {"Compilation": {"id": "tt"}}, {"id": "wfa"},
                                            items, force=False)
    assert keys == ["comp-h3"] and skipped == ["comp-gta-6"] and len(published) == 1
    assert "skipped comp-gta-6: preview is 2 MB" in capsys.readouterr().out


def test_a_skipped_recut_sends_its_old_revision_back_for_review_once(tmp_path, monkeypatch):
    """Grok on #436: Kitsu keeps the previous cut, so its approval mustn't cover the new one."""
    from clippa import jsonstate, review_auto

    comments, again = [], []
    gazu = SimpleNamespace(
        shot=SimpleNamespace(get_sequence_by_name=lambda *a, **k: {"id": "seq"}),
        task=SimpleNamespace(add_comment=lambda task, status, text: comments.append(
            (task["id"], status["id"], text))))
    monkeypatch.setattr(review_auto, "republished", lambda sdir, key: again.append(key))
    f = tmp_path / "comp-dsp.mp4"
    f.write_bytes(b"x" * 2_000_000)
    jsonstate.write(tmp_path / "review_state.json",
                    {"comp-dsp": {"task_id": "t9", "preview_mtime": 1.0, "fields": {"a": "b"}}})
    it = {"key": "comp-dsp", "kind": "compilation", "seq": "dsp", "seq_title": "DSP",
          "preview": f, "file": f, "duration_s": 60, "text": "", "type": "Compilation",
          "meta": {"id": "comp-dsp"}}
    rc = {"max_upload_mb": 1, "preview_fps": 30, "normalize": False,
          "host": "http://kitsu.test/api"}
    for _ in range(2):
        state, keys, skipped = review.push_previews(gazu, tmp_path, rc, {"id": "pr"},
                                                    {"id": "ep"}, {}, {"id": "wfa"}, [it],
                                                    force=False)
    assert keys == [] and skipped == ["comp-dsp"]
    assert len(comments) == 1 and comments[0][:2] == ("t9", "wfa")
    assert "previous cut" in comments[0][2] and again == ["comp-dsp"]
    saved = jsonstate.load(tmp_path / "review_state.json")["comp-dsp"]
    assert saved["fields"] == {"a": "b"} and saved["over_mtime"] == f.stat().st_mtime
