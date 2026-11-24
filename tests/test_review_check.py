"""`clippa review check` (#259 step A): the pure parts, and a run over a small fake stream."""
from __future__ import annotations

import json

import pytest

from clippa import jsonstate, review_check, shorts
from clippa.checkup import BAD, WARN

WATCH = {"panels": [{"src": [0, 0.137, 0.74, 0.74], "h": 608},
                    {"src": [0.74, 0.21, 0.26, 0.31], "h": 656, "cam": True},
                    {"src": [0.74, 0.52, 0.26, 0.31], "h": 656, "cam": True}]}


def face(x, y, w, h, marks=None):
    """A detect() face; marks default to a face's eyes, nose and mouth well inside its box."""
    marks = marks or [[x + w * fx, y + h * fy] for fx, fy in
                      ((0.3, 0.35), (0.7, 0.35), (0.5, 0.55), (0.35, 0.75), (0.65, 0.75))]
    return {"box": [x, y, w, h], "marks": marks}


# ---------- a Short's picture ----------

def test_panel_slots_stack_the_layout_top_to_bottom():
    assert review_check.panel_slots(WATCH, 1920) == [(0, 608), (608, 1264), (1264, 1920)]
    assert shorts.panel_heights({"panels": [{"src": [0, 0, 1, 1]}, {"src": [0, 0, 1, 1]}]}, 1920) == [960, 960]


def test_cam_panels_are_marked_or_follow_the_face():
    assert review_check.cam_panels(WATCH) == [1, 2]
    chud = {"panels": [{"src": [0, 0, 1, 1]}, {"src": [0, 0, 1, 1], "focus": "face"}]}
    assert review_check.cam_panels(chud) == [1]
    assert review_check.cam_panels({"panels": [{"src": [0, 0, 1, 1], "focus": "face", "cam": False}]}) == []
    assert review_check.cam_panels({"panels": [{"src": [0, 0, 1, 1]}]}) == []   # unmarked: not checked


def test_frame_verdicts_by_the_eyes_nose_and_mouth():
    slots = review_check.panel_slots(WATCH, 1920)
    middle = face(0.4, 0.4, 0.2, 0.12)                     # inside panel 2
    # panel 3: leaning in, the chin past the frame's bottom, the mouth still in: fine
    leaning = face(0.4, 0.85, 0.25, 0.16, marks=[[0.47, 0.9], [0.58, 0.9], [0.52, 0.94], [0.48, 0.97], [0.57, 0.97]])
    assert review_check.frame_verdicts([middle, leaning], slots, 1920, [1, 2]) == {1: "ok", 2: "ok"}
    # an eye off the left side of the Short: cut off
    cut = face(-0.05, 0.4, 0.2, 0.12, marks=[[0.0, 0.44], [0.08, 0.44], [0.05, 0.47], [0.02, 0.5], [0.08, 0.5]])
    assert review_check.frame_verdicts([cut], slots, 1920, [1, 2]) == {1: "edge", 2: "none"}
    # the mouth below panel 2's bottom (in panel 3's rows): cut by the seam
    seam = face(0.4, 0.57, 0.2, 0.1, marks=[[0.45, 0.6], [0.55, 0.6], [0.5, 0.63], [0.46, 0.67], [0.54, 0.67]])
    assert review_check.frame_verdicts([seam], slots, 1920, [1])[1] == "edge"


def test_picture_summary_needs_enough_frames_and_a_share():
    n = {"watch": 3}
    assert review_check.picture_summary({("watch", 1): ["none", "none"]}, n) == []   # too few frames
    out = review_check.picture_summary({("watch", 1): ["none", "none", "ok", "ok"],
                                        ("watch", 2): ["edge", "ok", "ok", "ok", "ok", "ok"]}, n)
    assert out == [(BAD, "no face in the watch layout's panel 2 of 3 (a webcam) in 2 of 4 frames")]
    out = review_check.picture_summary({("watch", 2): ["edge", "edge", "ok", "ok", "ok", "ok"]}, n)
    assert out and "cut off at the edge of the watch layout's panel 3" in out[0][1]


def test_short_scenes_follow_the_tightened_cut():
    side = {"in_s": 100.0, "out_s": 140.0, "layout": "cams 0:00 > watch 0:20",
            "scenes": [[0.0, 20.0, "cams"], [20.0, 40.0, "watch"]],
            "tightened": {"pauses": [{"from": 5.0, "to": 8.0}]}}
    assert review_check.short_scenes(side, {"cams": {}, "watch": {}}) == [[0.0, 17.0, "cams"], [17.0, 37.0, "watch"]]
    one = {"in_s": 0.0, "out_s": 30.0, "layout": "watch"}
    assert review_check.short_scenes(one, {"watch": {}}) == [[0.0, 30.0, "watch"]]
    assert review_check.short_scenes(one, {"cams": {}}) == []   # a layout the show no longer has


def test_a_cam_mark_renders_nothing_again():
    sc = {"captions": True, "lufs": -14, "width": 1080, "height": 1920}
    bare = {"watch": {"panels": [{"src": [0, 0, 1, 1], "h": 1920}]}}
    marked = {"watch": {"panels": [{"src": [0, 0, 1, 1], "h": 1920, "cam": True}]}}
    scenes = [[0.0, 30.0, "watch"]]
    assert shorts.short_signature(sc, "libx264", 1, 31, scenes, bare) == \
        shorts.short_signature(sc, "libx264", 1, 31, scenes, marked)


# ---------- the subject ----------

PEOPLE = {
    "jer": {"name": "The Quartering (Jer)", "aliases": []},
    "jerry-banfield": {"name": "Jerry Banfield", "aliases": []},
    "don": {"name": "Channel 404 News (Don Flurgundy)", "aliases": ["Don"]},
    "dsp": {"name": "DSP (DarkSydePhil)", "aliases": []},
    "ppp": {"name": "PPP", "aliases": []},
}


def test_plain_drops_apostrophes_so_dont_is_never_don():
    assert review_check.plain("Don't you dare") == "dont you dare"
    assert review_check.plain("PPP's take on Jer’s show") == "ppp take on jer show"
    assert review_check.mentions(review_check.plain("I don't know, Don said"), {"don"}) == 1


def test_title_people_skips_the_shows_own_and_takes_the_longest_name():
    assert review_check.title_people("Jer roasts PPP", PEOPLE, {"ppp"}) == {"jer": "jer"}
    assert review_check.title_people("Jerry Banfield's comeback", PEOPLE, set()) == {"jerry-banfield": "jerry banfield"}
    assert review_check.title_people("Don't stop", PEOPLE, set()) == {}


def test_opening_finding_wants_someone_else_and_no_subject(defaults):
    words = [{"word": w, "start": i * 10.0, "end": i * 10.0 + 1} for i, w in
             enumerate(["DSP", "said", "DSP", "again", "and", "DSP", "Joe", "finally"] + ["x"] * 300)]
    defaults["review"]["open_minutes"] = 1
    f = review_check.opening_finding(defaults, words, [(0, 3000)], {"angry joe", "joe"}, PEOPLE, set())
    assert f and f[0] == WARN and "name DSP (DarkSydePhil) 3 times and its subject never" in f[1]
    # the subject named in the opening: fine
    assert review_check.opening_finding(defaults, words, [(0, 3000)], {"dsp"}, PEOPLE, set()) is None
    # too short to say
    assert review_check.opening_finding(defaults, words, [(0, 80)], {"joe"}, PEOPLE, set()) is None


def test_a_compilations_opening_is_playback_time(defaults):
    assert review_check.opening([(0, 30), (2000, 3000)], 60) == [(0, 30), (2000, 2030)]
    assert review_check.opening([(0, 100)], 60) == [(0, 60)]
    # DSP three times in the first cut; Joe only in stream time the compilation leaves out
    words = [{"word": w, "start": t, "end": t + 1} for w, t in
             [("DSP", 0), ("DSP", 10), ("DSP", 20), ("Joe", 40), ("x", 2010), ("x", 2900)]]
    defaults["review"]["open_minutes"] = 1
    f = review_check.opening_finding(defaults, words, [(0, 30), (2000, 3000)], {"joe"}, PEOPLE, set())
    assert f and "name DSP (DarkSydePhil) 3 times" in f[1]
    # two short cuts hours apart are a short compilation, not a long one
    assert review_check.opening_finding(defaults, words, [(0, 30), (9000, 9030)], {"joe"}, PEOPLE, set()) is None


def test_segment_names_take_the_topics_last_word():
    names = review_check.segment_names({"topic": "Angry Joe", "figures": ["DSP"]}, PEOPLE, {"paths": {}})
    assert {"angry joe", "joe", "dsp", "darksydephil"} <= names


# ---------- the cover ----------

def test_cover_face_finding_only_for_a_confident_someone_else(defaults):
    defaults["faces"]["match"] = 0.7
    people = {"jerry-banfield": {"name": "Jerry Banfield", "aliases": ["Jerry"]},
              "the-quartering": {"name": "The Quartering (Jer)"}, "ppp": {"name": "PPP"}}
    assert review_check.cover_face_finding(defaults, "Jerry", 0.82, "Jer", people) == \
        (BAD, "the cover's face looks like Jerry (0.82), not Jer")
    assert review_check.cover_face_finding(defaults, "Jerry", 0.5, "Jer", people) is None     # not sure who
    assert review_check.cover_face_finding(defaults, "PPP", 0.9, "ppp", people) is None       # the same
    assert review_check.cover_face_finding(defaults, "", 0.0, "Jer", people) is None
    # a meant name no record has (main_person is free text) is left to the vision call
    assert review_check.cover_face_finding(defaults, "Jer", 0.85, "Jeremy Hambly", people) is None
    assert review_check.cover_face_finding(defaults, "PPP", 0.81, "Heel vs Babyface", people) is None


def test_vision_findings():
    assert review_check.vision_findings({}) == []
    v = {"person_shown": "no", "face_cut_off": True, "text_reads": False, "problems": ["  a frozen   frame "]}
    assert review_check.vision_findings(v) == [
        (BAD, "the cover doesn't show the person it names"), (BAD, "a face on the cover is cut off"),
        (WARN, "the cover's words are hard to read"), (WARN, "cover: a frozen frame")]
    assert review_check.vision_findings({"person_shown": "unsure", "face_cut_off": False,
                                         "text_reads": True, "problems": []}) == []
    assert "wouldn't look" in review_check.vision_findings({"refused": True})[0][1]


def test_summary_fits_the_column():
    assert review_check.summary([]) == ""
    long = [{"text": "x" * 300}, {"text": "y" * 300}]
    out = review_check.summary(long)
    assert len(out) == review_check.COLUMN_CHARS and out.endswith("…")


# ---------- a run over a fake stream ----------

def fake_stream(cfg, tmp_path):
    cfg["paths"]["streams"] = tmp_path / "streams"
    cfg["paths"]["streamers"] = tmp_path / "streamers.json"
    cfg["paths"]["archive"] = tmp_path / "archive"
    jsonstate.write(cfg["paths"]["people"], {"people": {k: dict(v) for k, v in PEOPLE.items()}})
    sd = cfg["paths"]["streams"] / "kick-1"
    (sd / "segments").mkdir(parents=True)
    seg = {"id": "s01", "start_s": 0.0, "end_s": 3000.0, "topic": "Angry Joe", "figures": ["Angry Joe"],
           "title": "Angry Joe hosts a stream", "description": "", "source_share": "none", "cut": True}
    jsonstate.write(sd / "segments.json", {"segments": [seg]})
    jsonstate.write(sd / "segments" / "s01_angry-joe.json", seg)
    (sd / "segments" / "s01_angry-joe.mp4").write_bytes(b"")
    jsonstate.write(sd / "meta.json", {"started_at": "2026-11-14T03:00:00+00:00"})
    lines = [{"word": w, "start": i * 10.0, "end": i * 10.0 + 1} for i, w in
             enumerate(["DSP", "DSP", "DSP", "Jer"] + ["x"] * 296)]
    (sd / "words.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return sd


def test_run_checks_keeps_and_skips_the_unchanged(defaults, tmp_path):
    sd = fake_stream(defaults, tmp_path)
    defaults["review"]["check_model"] = ""      # offline: no vision call
    defaults["review"]["open_minutes"] = 5
    said = []
    found = review_check.run(sd, defaults, say=said.append)
    texts = [f["text"] for f in found["s01_angry-joe"]]
    assert any("says 'hosts'" in t for t in texts)             # [checkup] words
    assert "no description" in texts
    assert any("name DSP (DarkSydePhil) 3 times" in t for t in texts)
    assert all(f["stream"] == "kick-1" and f["check"] in review_check.CHECKS for f in found["s01_angry-joe"])
    assert review_check.flagged_keys(sd) == {"s01_angry-joe"}
    assert len(said) == 1
    review_check.run(sd, defaults, say=said.append)            # nothing changed: not checked again
    assert len(said) == 1
    review_check.run(sd, defaults, say=said.append, force=True)
    assert len(said) == 2
    side = sd / "segments" / "s01_angry-joe.json"
    jsonstate.write(side, {**jsonstate.load(side), "title": "Angry Joe returns", "description": "About Joe."})
    found = review_check.run(sd, defaults, say=said.append)
    assert len(said) == 3 and not any("hosts" in f["text"] for f in found["s01_angry-joe"])
    # a title a reviewer changed in Kitsu (review.json) is what's checked, and a change rechecks
    jsonstate.write(sd / "review.json", {"s01_angry-joe": {"fields": {"YouTube title": "The hosts give up"}}})
    found = review_check.run(sd, defaults, say=said.append)
    assert len(said) == 4 and any(f["text"] == "title says 'hosts'" for f in found["s01_angry-joe"])


def test_run_leaves_a_newer_result_alone(defaults, tmp_path, monkeypatch):
    sd = fake_stream(defaults, tmp_path)
    defaults["review"]["check_model"] = ""
    newer = {"sig": "other", "at": "2999-01-01T00:00:00+00:00", "findings": []}

    def meanwhile(sdir, cfg, it, ctx, claude):   # another run finishes while this one checks
        jsonstate.write(review_check.path(sdir), {it["key"]: newer})
        return [], {}
    monkeypatch.setattr(review_check, "check_item", meanwhile)
    review_check.run(sd, defaults, say=lambda *_: None)
    assert review_check.load(sd)["s01_angry-joe"] == newer


def test_record_failure_flags_the_items_until_checked_again(tmp_path):
    review_check.record_failure(tmp_path, ["v01_t1"], "the checks didn't run: OSError: x")
    assert review_check.flagged_keys(tmp_path) == {"v01_t1"}
    assert review_check.load(tmp_path)["v01_t1"]["sig"] == ""


def test_an_openrouter_limit_is_not_saved_as_a_bad_cover(defaults, tmp_path, monkeypatch):
    """A key or credit limit stops the run. It is not a BAD cover, and the next item is not called."""
    from clippa.errors import OpenRouterLimit

    sd = fake_stream(defaults, tmp_path)
    defaults["review"]["check_model"] = "openrouter:openai/gpt-5-mini"
    calls = []

    def vision(cfg, it, e, jpg, claude):
        calls.append(it["key"])
        raise OpenRouterLimit(
            "OpenRouter limit openrouter_key_limit (HTTP 402), not retried: spent",
            "openrouter_key_limit")

    monkeypatch.setattr(review_check, "vision_check", vision)

    def item(key):
        f = sd / "segments" / f"{key}.mp4"
        f.write_bytes(b"")
        f.with_suffix(".jpg").write_bytes(b"")
        return {"kind": "segment", "key": key, "file": f, "title": "A title", "description": "About it.",
                "text": "A title\n\nAbout it.", "fields": {}, "meta": {"id": "s01"},
                "thumb_key": f"segments/{key}"}

    jsonstate.write(sd / "thumbnails.json", {"segments/one": {"text": "HI"}, "segments/two": {"text": "HO"}})
    with pytest.raises(OpenRouterLimit):
        review_check.run(sd, defaults, todo=[item("one"), item("two")], say=lambda *_: None)
    assert calls == ["one"]
    assert review_check.load(sd) == {}
    assert not review_check.path(sd).exists()


def test_run_save_false_writes_nothing_and_a_broken_check_is_a_finding(defaults, tmp_path, monkeypatch):
    sd = fake_stream(defaults, tmp_path)
    defaults["review"]["check_model"] = ""

    def boom(*a, **k):
        raise ValueError("no words")
    monkeypatch.setattr(review_check, "subject_findings", boom)
    found = review_check.run(sd, defaults, save=False, say=lambda *_: None)
    assert not review_check.path(sd).exists()
    assert any(f["level"] == BAD and "the subject check failed: ValueError: no words" in f["text"]
               for f in found["s01_angry-joe"])


def face_cover(sd):
    """A cover whose face box is on disk: the cover check calls faces.pick, which imports cv2."""
    rel = "segments/s01_angry-joe"
    (sd / "segments" / "s01_angry-joe.jpg").write_bytes(b"jpg")
    frame = sd / "thumbs" / rel / "f1.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"frame")
    jsonstate.write(sd / "thumbnails.json", {rel: {
        "text": "JOE", "main_person": "Angry Joe", "frame": 1,
        "face": {"x": 0.2, "y": 0.1, "width": 0.4, "height": 0.4},
        "frames": [{"path": "f1.jpg"}]}})


def hide_cv2(monkeypatch):
    """`import cv2` fails the way a broken install does (sys.modules entry is None)."""
    import sys

    monkeypatch.setitem(sys.modules, "cv2", None)


def test_missing_opencv_raises_and_does_not_write_checks(defaults, tmp_path, monkeypatch):
    from clippa.errors import ClipperError

    sd = fake_stream(defaults, tmp_path)
    defaults["review"]["check_model"] = ""
    face_cover(sd)
    hide_cv2(monkeypatch)
    with pytest.raises(ClipperError, match="review check needs OpenCV: install opencv-python-headless"):
        review_check.run(sd, defaults, say=lambda *_: None)
    assert not review_check.path(sd).exists()


def test_missing_opencv_leaves_checks_json_and_is_not_skipped(defaults, tmp_path, monkeypatch):
    """A saved 'No module named cv2' matches the signature, so it used to be skipped for good."""
    from clippa import config, review
    from clippa.errors import ClipperError

    sd = fake_stream(defaults, tmp_path)
    defaults["review"]["check_model"] = ""
    face_cover(sd)
    cfg = config.for_stream(defaults, sd)
    it = review.items(sd, ["segments"], cfg)[0]
    sig = review_check.signature(cfg, it, review_check.context(sd, cfg), False)
    prior = {it["key"]: {"sig": sig, "at": "2026-11-20T00:00:00+00:00", "kind": "segment",
                         "findings": [{"text": "the cover check failed: ModuleNotFoundError: "
                                       "No module named 'cv2'"}]}}
    jsonstate.write(review_check.path(sd), prior)
    hide_cv2(monkeypatch)
    with pytest.raises(ClipperError, match="review check needs OpenCV"):
        review_check.run(sd, defaults, say=lambda *_: None)
    assert review_check.load(sd) == prior


def test_a_cover_is_still_checked_when_opencv_imports(defaults, tmp_path, monkeypatch):
    """The normal path: cv2 imports, a broken cover is a finding and checks.json is written."""
    import sys
    import types

    sd = fake_stream(defaults, tmp_path)
    defaults["review"]["check_model"] = ""
    face_cover(sd)
    try:
        import cv2  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "cv2", types.ModuleType("cv2"))
    found = review_check.run(sd, defaults, say=lambda *_: None)
    assert review_check.path(sd).exists()
    assert any(f["check"] == "cover" and "check failed" in f["text"] for f in found["s01_angry-joe"])


def test_a_short_webcam_imports_cv2_and_a_plain_segment_does_not(defaults, tmp_path):
    sd = fake_stream(defaults, tmp_path)
    defaults["shorts"]["layouts"] = {"watch": WATCH}
    short = {"kind": "short", "file": sd / "segments" / "s01_angry-joe.mp4", "thumb_key": "nope",
             "meta": {"in_s": 0.0, "out_s": 30.0, "layout": "watch"}}
    assert review_check.item_imports_cv2(sd, defaults, short, {"thumbs": {}})
    # panel 0 is a webcam too: a non-empty cam list, including [0]
    defaults["shorts"]["layouts"] = {"top": {"panels": [{"src": [0, 0, 1, 1], "cam": True}]}}
    short["meta"] = {**short["meta"], "layout": "top"}
    assert review_check.item_imports_cv2(sd, defaults, short, {"thumbs": {}})
    # a 1 s webcam scene is never sampled (0.5 s margin either side), so detect() is not called
    defaults["shorts"]["layouts"] = {"watch": WATCH}
    defaults["review"]["check_frames"] = 8
    short["meta"] = {"in_s": 0.0, "out_s": 20.0, "duration_s": 20.0, "layout": "watch",
                     "scenes": [[0.0, 1.0, "watch"]]}
    assert not review_check.item_imports_cv2(sd, defaults, short, {"thumbs": {}})
    plain = {"kind": "segment", "file": sd / "segments" / "s01_angry-joe.mp4", "thumb_key": "nope",
             "meta": {}}
    assert not review_check.item_imports_cv2(sd, defaults, plain, {"thumbs": {}})


def test_missing_opencv_still_saves_items_that_do_not_need_it(defaults, tmp_path, monkeypatch):
    """A sibling whose title changed is rechecked and saved. The cover that needs cv2 is not
    written as a bad cover, and a stale clean result does not stay in front of the new title."""
    from clippa.errors import ClipperError

    sd = fake_stream(defaults, tmp_path)
    defaults["review"]["check_model"] = ""
    side = sd / "segments" / "s01_angry-joe.json"
    old = jsonstate.load(side)
    jsonstate.write(side, {**old, "title": "Angry Joe returns", "description": "About Joe."})
    review_check.run(sd, defaults, say=lambda *_: None)
    assert not any("hosts" in f["text"] for f in review_check.load(sd)["s01_angry-joe"]["findings"])
    jsonstate.write(side, {**jsonstate.load(side), "title": "The hosts give up"})
    seg2 = {"id": "s02", "start_s": 3000.0, "end_s": 6000.0, "topic": "Don", "figures": ["Don"],
            "title": "Don talks", "description": "Don talks.", "source_share": "none", "cut": True}
    data = jsonstate.load(sd / "segments.json")
    data["segments"].append(seg2)
    jsonstate.write(sd / "segments.json", data)
    jsonstate.write(sd / "segments" / "s02_don.json", seg2)
    (sd / "segments" / "s02_don.mp4").write_bytes(b"")
    rel = "segments/s02_don"
    (sd / "segments" / "s02_don.jpg").write_bytes(b"jpg")
    frame = sd / "thumbs" / rel / "f1.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"frame")
    jsonstate.write(sd / "thumbnails.json", {rel: {
        "text": "DON", "main_person": "Don", "frame": 1,
        "face": {"x": 0.2, "y": 0.1, "width": 0.4, "height": 0.4},
        "frames": [{"path": "f1.jpg"}]}})
    hide_cv2(monkeypatch)
    with pytest.raises(ClipperError, match="review check needs OpenCV"):
        review_check.run(sd, defaults, say=lambda *_: None)
    saved = review_check.load(sd)
    assert any("hosts" in f["text"] for f in saved["s01_angry-joe"]["findings"])
    assert "s02_don" not in saved


def test_an_unsampled_webcam_scene_does_not_need_opencv(defaults, tmp_path, monkeypatch):
    sd = fake_stream(defaults, tmp_path)
    defaults["review"]["check_model"] = ""
    defaults["shorts"]["layouts"] = {"watch": WATCH}
    defaults["review"]["check_frames"] = 8
    hide_cv2(monkeypatch)
    short = {"kind": "short", "key": "v01_t1", "file": sd / "shorts" / "v01.mp4",
             "title": "Don talks", "description": "Don talks.", "text": "Don talks\n\nDon talks.",
             "fields": {}, "thumb_key": "shorts/v01",
             "meta": {"in_s": 0.0, "out_s": 20.0, "duration_s": 20.0, "layout": "watch",
                      "scenes": [[0.0, 1.0, "watch"]]}}
    found = review_check.run(sd, defaults, todo=[short], say=lambda *_: None)
    assert "v01_t1" in found
    assert not any("cv2" in f["text"] for f in found["v01_t1"])


def test_publish_does_not_save_a_missing_opencv(defaults, tmp_path, monkeypatch):
    from clippa import review
    from clippa.errors import ClipperError

    sd = fake_stream(defaults, tmp_path)
    defaults["review"]["check_model"] = ""
    face_cover(sd)
    hide_cv2(monkeypatch)
    todo = review.items(sd, ["segments"], defaults)
    with pytest.raises(ClipperError, match="review check needs OpenCV"):
        review.add_checks(sd, defaults, todo)
    assert not review_check.path(sd).exists()


def test_estimate_counts_covers_to_look_at(defaults, tmp_path):
    sd = fake_stream(defaults, tmp_path)
    assert review_check.estimate(sd, defaults)[0] == 0          # no cover .jpg
    (sd / "segments" / "s01_angry-joe.jpg").write_bytes(b"")
    n, usd = review_check.estimate(sd, defaults)
    assert n == 1 and usd > 0
    defaults["review"]["check_model"] = ""
    assert review_check.estimate(sd, defaults) == (0, 0.0)


def test_same_person_is_loose_about_spellings(defaults):
    same = lambda a, b: review_check.same_person(defaults, a, b, PEOPLE)
    assert same("Jerry", "Jerry Banfield") and same("Russel Brand", "Russell Brand")
    assert same("Jer", "The Quartering (Jer)")              # one people.json record
    assert not same("Jer", "Don Flurgundy") and not same("Jerry", "Jer")


# ---------- where the findings show ----------

def test_findings_reach_describe_counts_status_and_the_card(defaults, tmp_path, monkeypatch):
    from clippa import actions, notify, review, status

    sdir = tmp_path / "kick-3"
    sdir.mkdir()
    state = {k: {"kind": "short", "id": k, "file": f"shorts/{k}.mp4", "task_id": "T", "entity_id": "E",
                 "kitsu_url": "http://k/t", "episode_url": "http://k/e", "fields": {}} for k in ("v01_t1", "v02_t2")}
    jsonstate.write(sdir / "review_state.json", state)
    jsonstate.write(sdir / "review.json", {"v02_t2": {"decision": "approved", "fields": {}}})
    jsonstate.write(review_check.path(sdir), {
        "v01_t1": {"sig": "x", "findings": [{"text": "no face in the cams layout's panel 1 of 2 (a webcam)"}]},
        "v02_t2": {"sig": "y", "findings": [{"text": "a face on the cover is cut off"}]}})
    monkeypatch.setattr(actions, "for_stream", lambda cfg, d: cfg)
    monkeypatch.setattr(review, "ensure_links", lambda d, cfg, gazu=None: state)
    monkeypatch.setattr(review, "items", lambda d, include, cfg: [])
    monkeypatch.setattr(actions, "schedule_entries", lambda cfg: {})
    monkeypatch.setattr(actions, "schedule_dropped", lambda cfg: {})
    row = actions.describe(defaults, sdir)["v01_t1"]
    assert row["checks"] == ["no face in the cams layout's panel 1 of 2 (a webcam)"]
    counts = actions.stream_counts(defaults, sdir)
    assert counts["flagged"] == 1   # the approved one isn't waiting on anyone
    out = status.needs_lines({"needs_you": {"review": [counts], "findings": []}})
    assert "1 with check findings" in out[0]
    embed, _ = notify.card(row)
    f = next(f for f in embed["fields"] if f["name"] == "Findings")
    assert f["value"] == "⚠ no face in the cams layout's panel 1 of 2 (a webcam)"


def test_publish_fills_the_checks_column_and_goes_on_when_they_break(defaults, tmp_path, monkeypatch):
    from clippa import review

    todo = [{"key": "v01_t1", "fields": {}}, {"key": "v02_t2", "fields": {}}]
    monkeypatch.setattr(review_check, "run", lambda d, cfg, t: {
        "v01_t1": [{"text": "a face on the cover is cut off"}], "v02_t2": []})
    review.add_checks(tmp_path, defaults, todo)
    assert [it["fields"]["Checks"] for it in todo] == ["a face on the cover is cut off", ""]

    def boom(*a):
        raise OSError("share gone")
    monkeypatch.setattr(review_check, "run", boom)
    review.add_checks(tmp_path, defaults, todo)
    assert all(it["fields"]["Checks"] == "the checks didn't run: OSError: share gone" for it in todo)
    assert review_check.flagged_keys(tmp_path) == {"v01_t1", "v02_t2"}   # the card and status see it too


def test_text_dedupes_host_per_field(defaults):
    defaults["checkup"]["words"] = ["hosts"]
    it = {"title": "The hosts lose it", "description": "Our host says no.", "fields": {}}
    texts = [t for _, t in review_check.text_findings(defaults, it)]
    assert "title says 'hosts'" in texts
    assert sum("title says" in t for t in texts) == 1               # not again from style_issues
    assert any(t.startswith("description says \"host\"") for t in texts)   # the singular, another field


def test_signature_follows_what_the_checks_read(defaults, tmp_path):
    sd = fake_stream(defaults, tmp_path)
    from clippa import config, review

    cfg = config.for_stream(defaults, sd)
    it = review.items(sd, ["segments"], cfg)[0]
    one = review_check.signature(cfg, it, review_check.context(sd, cfg), False)
    jsonstate.write(cfg["paths"]["people"], {"people": {"dsp": {"name": "DSP", "aliases": ["Phil"]}}})
    import os
    import time
    os.utime(cfg["paths"]["people"], (time.time() + 5, time.time() + 5))
    two = review_check.signature(cfg, it, review_check.context(sd, cfg), False)
    assert two != one
    cfg["meta"]["cast"] = [*cfg["meta"]["cast"], "Jerry"]     # the show's own people
    assert review_check.signature(cfg, it, review_check.context(sd, cfg), False) != two


def test_the_kitsu_column_gets_what_checks_json_kept(defaults, tmp_path, monkeypatch):
    # an older run finishing after a newer one: checks.json keeps the newer result, and so must Kitsu
    from clippa import jsonstate, review

    sd = tmp_path / "kick-9"
    sd.mkdir()
    monkeypatch.setattr(review_check, "select", lambda *a: [{"key": "v01_t1", "kind": "short"}])
    monkeypatch.setattr(review_check, "context", lambda *a: {})
    monkeypatch.setattr(review_check, "signature", lambda *a: "s")

    def check_then_a_newer_run_lands(*a):
        jsonstate.write(review_check.path(sd), {"v01_t1": {"sig": "s", "at": "9999", "findings": []}})
        return [{"text": "old finding"}], None
    monkeypatch.setattr(review_check, "check_item", check_then_a_newer_run_lands)
    columns = []
    monkeypatch.setattr(review, "set_checks", lambda sdir, c, summaries: columns.append(summaries) or [])
    review_check.run(sd, defaults, say=lambda *_: None, kitsu=True)
    assert columns == [{"v01_t1": ""}]
