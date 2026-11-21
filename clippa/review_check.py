"""`clippa review check` (#259 step A): look at what the reviewer looks at, before they do.

Usage:
    clippa review check <stream> [--items v04,s02] [--force] [--no-vision] [--json]

Each review item (review.items) gets a list of findings, in checkup's finding shape:

    text      the [checkup] words, YouTube's limits and an unmasked [bleep] word in the title,
              description, hashtags, tags and thumbnail text (checkup.lint_text and
              actions.style_issues, the war room's warnings)
    picture   a Short: [review] check_frames frames of the rendered Short through the face
              detector (`clippa faces`' models); a webcam panel with no face in most frames,
              or one whose face is against the panel's edge (cut off). The webcam panels are
              the ones marked `cam = true` in the show's layouts, or focus = "face"; a layout
              with neither isn't checked
    cover     the face on the cover (thumbnails.json frame and face box) against the person it
              is meant to show (main_person), by the known faces; and one vision call
              ([review] check_model) on the finished .jpg: is the named person the one shown, is
              a face cut off, does the text read
    subject   people named in the title whom the transcript around the item never names (and
              its segment's topic and figures don't list); a segment or compilation whose
              subject isn't named in its first [review] open_minutes

The findings go in the stream's checks.json ({key: {sig, at, findings, vision}}), keyed by a
signature of what was checked: an unchanged item is not checked again (no second vision call)
unless --force. `clippa review publish` runs this first ([review] checks) and puts a one-line
summary in Kitsu's Checks column; the Discord card, the war room's ⚠ badge and `clippa
status` read checks.json. An item with findings is one that needs a person: step B's automatic
approval (#259) leaves it alone. A check that fails is a finding too, so a broken check never
passes an item.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import json
import re
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from . import jsonstate
from .checkup import BAD, WARN, finding, lint_text
from .config import for_stream
from .errors import ClipperError, key_rejected

CHECKS_FILE = "checks.json"
VERSION = 1            # bump when a check changes: every item is checked again
EDGE_X, EDGE_Y = 0.01, 0.004   # an eye, the nose or the mouth this close to a panel's side / top or bottom: cut off
MIN_FACE_PX = 30       # smaller faces in a rendered Short are someone in the video, not a cam
SUBJECT_PAD_S = 120    # a pick or Short's transcript is read this far either side for names
COLUMN_CHARS = 480     # the Kitsu Checks column


def path(sdir: Path) -> Path:
    return sdir / CHECKS_FILE


def load(sdir: Path) -> dict:
    """{key: {sig, at, findings, vision}}; {} when nothing was checked."""
    return jsonstate.read_or(path(sdir), {})


def findings_of(checks: dict, key: str) -> list[dict]:
    return (checks.get(key) or {}).get("findings") or []


def summary(found: list[dict]) -> str:
    """The Kitsu Checks column: every finding's text on one line, "" when clean."""
    out = "; ".join(f["text"] for f in found)
    return out if len(out) <= COLUMN_CHARS else out[:COLUMN_CHARS - 1] + "…"


# ---------- text ----------

LABELS = {"title": "title", "description": "description", "hashtags": "hashtags", "tags": "tags",
          "thumb_text": "thumbnail text"}
HOST_WORD = re.compile(r"\bhosts?\b", re.IGNORECASE)   # style_issues' own "host" rule

def text_findings(cfg: dict, it: dict) -> list[tuple[str, str]]:
    from .actions import style_issues

    f = it["fields"]
    values = {"title": it["title"], "description": it["description"],
              "hashtags": f.get("Hashtags") or "", "tags": f.get("Tags") or "",
              "thumb_text": f.get("Thumbnail text") or ""}
    out, linted = [], set()
    for field, text in values.items():
        said = lint_text(cfg["checkup"]["words"], {LABELS[field]: text})
        out += [(WARN, x) for x in said]
        linted |= {field} if said else set()
    for x in style_issues(cfg, values):
        if x["field"] in linted and HOST_WORD.search(x["msg"]):
            continue   # the [checkup] words already said it for this field
        out.append((WARN, x["msg"]))
    if not (it["description"] or "").strip():
        out.append((WARN, "no description"))
    return out


# ---------- a Short's picture ----------

def panel_slots(layout: dict, out_h: int) -> list[tuple[int, int]]:
    """Each panel's rows of the rendered Short, top to bottom: (y0, y1) in pixels."""
    from .shorts import panel_heights

    out, y = [], 0
    for h in panel_heights(layout, out_h):
        out.append((y, y + h))
        y += h
    return out


def cam_panels(layout: dict) -> list[int]:
    """The layout's webcam panels: `cam = true` (or false) on the panel says so; a panel with
    focus = "face" is one unless it says otherwise."""
    return [i for i, p in enumerate(layout.get("panels", []))
            if p.get("cam", p.get("focus") == "face")]


def frame_verdicts(found: list[dict], slots: list[tuple[int, int]], out_h: int,
                   cams: list[int]) -> dict[int, str]:
    """Per webcam panel of one rendered frame: "ok", "edge" (an eye, the nose or a mouth
    corner of its face is outside the panel or on its edge: the face is cut off, where a chin
    or forehead past the edge is only someone leaning in) or "none". found: detect()'s faces,
    {"box": [x, y, w, h], "marks": [[x, y] * 5]}, 0-1 of the frame."""
    out = {}
    for i in cams:
        y0, y1 = slots[i][0] / out_h, slots[i][1] / out_h
        inside = [f for f in found if y0 <= f["box"][1] + f["box"][3] / 2 < y1]
        if not inside:
            out[i] = "none"
            continue
        face = max(inside, key=lambda f: f["box"][2] * f["box"][3])
        cut = any(not (EDGE_X < x < 1 - EDGE_X and y0 + EDGE_Y < y < y1 - EDGE_Y) for x, y in face["marks"])
        out[i] = "edge" if cut else "ok"
    return out


def picture_summary(seen: dict[tuple[str, int], list[str]], n_panels: dict[str, int]) -> list[tuple[str, str]]:
    """Findings from every frame's verdicts, per (layout, panel): no face in half or more of the
    frames that panel is on screen, or a face against its edge in a third or more."""
    out = []
    for (name, i), vs in sorted(seen.items()):
        if len(vs) < 3:
            continue
        where = f"the {name} layout's panel {i + 1} of {n_panels[name]}"
        none, edge = vs.count("none"), vs.count("edge")
        if none * 2 >= len(vs):
            out.append((BAD, f"no face in {where} (a webcam) in {none} of {len(vs)} frames"))
        elif edge * 3 >= len(vs):
            out.append((BAD, f"a face is cut off at the edge of {where} in {edge} of {len(vs)} frames"))
    return out


def short_scenes(side: dict, layouts: dict) -> list[list]:
    """[start, end, layout] in the rendered Short's own time (tightened pauses taken out)."""
    from . import tighten

    dur = side["out_s"] - side["in_s"]
    scenes = side.get("scenes") or [[0.0, dur, side.get("layout")]]
    pauses = (side.get("tightened") or {}).get("pauses") or []
    if pauses:
        scenes = tighten.Timeline(pauses, dur).scenes(scenes)
    return [s for s in scenes if s[2] in layouts and s[1] > s[0]]


def detect(img_path: Path, cfg: dict) -> list[dict]:
    """Faces in one picture by the faces stage's detector: {"box": [x, y, w, h], "marks": the
    eyes, nose tip and mouth corners as [x, y]}, 0-1 of the image and not clamped to it."""
    import cv2

    from . import faces

    img = cv2.imread(str(img_path))
    if img is None:
        raise ClipperError(f"can't read {img_path.name}")
    h, w = img.shape[:2]
    with faces._PICK:
        models = str(faces.ensure_models(cfg))
        if faces._ready != models:
            faces._init(models, 0)
            faces._ready = models
        faces._det.setInputSize((w, h))
        _, rows = faces._det.detect(img)
    out = []
    for r in rows if rows is not None else []:
        if float(r[3]) < MIN_FACE_PX:
            continue
        out.append({"box": [float(r[0]) / w, float(r[1]) / h, float(r[2]) / w, float(r[3]) / h],
                    "marks": [[float(r[k]) / w, float(r[k + 1]) / h] for k in range(4, 14, 2)]})
    return out


def picture_findings(sdir: Path, cfg: dict, it: dict, ctx: dict) -> list[tuple[str, str]]:
    from . import faces, shorts

    side = it["meta"]
    layouts = shorts.layouts_of(cfg["shorts"])
    scenes = short_scenes(side, layouts)
    if not scenes:
        return [(WARN, f"the Short's layout {side.get('layout')!r} isn't one this show has")]
    out_h = cfg["shorts"]["height"]
    plan = {n: (panel_slots(layouts[n], out_h), cam_panels(layouts[n])) for _, _, n in scenes}
    if not any(c for _, c in plan.values()):
        return []
    length = side.get("duration_s") or scenes[-1][1]
    n = max(1, cfg["review"]["check_frames"])
    seen: dict[tuple[str, int], list[str]] = {}
    with tempfile.TemporaryDirectory(prefix="clipper-check-") as tmp:
        for k in range(n):
            t = length * (k + 0.5) / n
            scene = next((s for s in scenes if s[0] + 0.5 <= t < s[1] - 0.5), None)
            if scene is None:   # too near a layout switch to say which panels are on screen
                continue
            slots, cams = plan[scene[2]]
            if not cams:
                continue
            boxes = detect(faces.grab(it["file"], t, Path(tmp) / f"{k}.jpg"), cfg)
            for i, v in frame_verdicts(boxes, slots, out_h, cams).items():
                seen.setdefault((scene[2], i), []).append(v)
    return picture_summary(seen, {name: len(layouts[name]["panels"]) for name in plan})


# ---------- the cover ----------

def same_person(cfg: dict, a: str, b: str, people: dict) -> bool:
    """Two names for one person: the same words, one name's words inside the other's (the known
    face "Jerry" is "Jerry Banfield"), a spelling a letter or two off ("Russel Brand"), or the
    same people.json record. Loose on purpose: a miss here is a false alarm on a good cover."""
    from difflib import SequenceMatcher

    from .people import find

    pa, pb = plain(a), plain(b)
    ta, tb = set(pa.split()), set(pb.split())
    if pa == pb or (ta and tb and (ta <= tb or tb <= ta)) or SequenceMatcher(None, pa, pb).ratio() >= 0.85:
        return True
    try:
        fa, fb = find(cfg, a, people), find(cfg, b, people)
    except ClipperError:   # a name two records answer to
        return False
    return bool(fa and fb and fa[0] == fb[0])


def cover_face_finding(cfg: dict, who: str, match: float, meant: str, people: dict) -> tuple[str, str] | None:
    """The cover's face looks like someone known (who, at similarity match) other than the
    person the cover is meant to show: a finding. A face nobody matches says nothing (a
    costume, a guest), and so does a meant name people.json doesn't know ("Jeremy Hambly",
    "Kino's chef"): main_person is free text, and the vision call judges those covers."""
    from .people import find

    if not who or not meant or match < cfg["faces"]["match"] or same_person(cfg, who, meant, people):
        return None
    try:
        fw, fm = find(cfg, who, people), find(cfg, meant, people)
    except ClipperError:   # a name two records answer to
        return None
    if not fw or not fm or fw[0] == fm[0]:
        return None
    return (BAD, f"the cover's face looks like {who} ({match:.2f}), not {meant}")


def cover_entry(it: dict, ctx: dict) -> dict | None:
    """The item's thumbnails.json choice, with "_key" (its frames folder under thumbs/). A
    Short's 9:16 cover is drawn from its clip's choice."""
    key = it["thumb_key"]
    if key not in ctx["thumbs"] and it["kind"] == "short" and it["meta"].get("clip"):
        key = f"clips/{Path(it['meta']['clip']).stem}"
    e = ctx["thumbs"].get(key)
    return {**e, "_key": key} if e else None


def cover_findings(sdir: Path, cfg: dict, it: dict, ctx: dict, claude) -> tuple[list[tuple[str, str]], dict]:
    """(findings, the vision call's answer or {})."""
    from . import faces

    jpg = it["file"].with_suffix(".jpg")
    e = cover_entry(it, ctx)
    if not jpg.exists() or not e:
        return [], {}
    out = []
    meant = e.get("photo") or e.get("main_person") or ""
    frames = e.get("frames") or []
    if not e.get("photo") and e.get("face") and e.get("frame") and len(frames) >= e["frame"]:
        frame = sdir / "thumbs" / e["_key"] / frames[e["frame"] - 1]["path"]
        if frame.exists():
            box = e["face"]
            try:
                got = faces.pick(frame, [box["x"], box["y"], box["width"], box["height"]], cfg)
            except ClipperError:
                got = None   # no face in the box: the vision call looks at the cover
            if got:
                f = cover_face_finding(cfg, got["closest"], got["match"], meant, ctx["people"])
                if f:
                    out.append(f)
    vision = {}
    if claude is not None and cfg["review"]["check_model"]:
        vision = vision_check(cfg, it, e, jpg, claude)
        out += vision_findings(vision)
    return out, vision


VISION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["person_shown", "face_cut_off", "text_reads", "problems"],
    "properties": {
        "person_shown": {"type": "string", "enum": ["yes", "no", "unsure"]},
        "face_cut_off": {"type": "boolean"},
        "text_reads": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"}},
    },
}


IMAGE_PX = 1568   # the longest side sent to the model; bigger photos (a Commons original) are shrunk


def image_block(p: Path) -> dict:
    """The picture as a JPEG content block, no longer than IMAGE_PX on its long side: a cast
    photo straight from Commons can be over the API's size limit for one image."""
    import io

    from PIL import Image

    with Image.open(p) as im:
        im = im.convert("RGB")
        im.thumbnail((IMAGE_PX, IMAGE_PX))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


def reference_photo(cfg: dict, name: str) -> Path | None:
    """A kept cast-library photo of the person, to tell the model what they look like."""
    if not name:
        return None
    try:
        from .cast import best_photo, person_dir

        d = person_dir(cfg, name)
        return best_photo(d) if d.is_dir() else None
    except (ClipperError, OSError, ValueError):
        return None


def vision_check(cfg: dict, it: dict, e: dict, jpg: Path, claude) -> dict:
    from .prompts import Prompts

    rc = cfg["review"]
    meant = e.get("photo") or e.get("main_person") or ""
    ref = reference_photo(cfg, meant)
    content = [image_block(jpg)]
    if ref:
        content.append(image_block(ref))
    content.append({"type": "text", "text": json.dumps({
        "title": it["title"], "cover_text": e.get("text") or it["fields"].get("Thumbnail text") or "",
        "person": meant, "reference_photo": bool(ref), "kind": it["kind"]}, ensure_ascii=False)})
    out, _ = claude.json_call(rc["check_model"], Prompts(cfg).get("review_check"), content,
                              VISION_SCHEMA, rc["check_effort"])
    return out or {"refused": True}


def vision_findings(v: dict) -> list[tuple[str, str]]:
    if not v:
        return []
    if v.get("refused"):
        return [(WARN, "the cover check model wouldn't look at the cover")]
    out = []
    if v.get("person_shown") == "no":
        out.append((BAD, "the cover doesn't show the person it names"))
    if v.get("face_cut_off"):
        out.append((BAD, "a face on the cover is cut off"))
    if v.get("text_reads") is False:
        out.append((WARN, "the cover's words are hard to read"))
    for p in v.get("problems") or []:
        p = " ".join(str(p).split())[:160]
        if p:
            out.append((WARN, f"cover: {p}"))
    return out


# ---------- the subject ----------

def plain(text: str) -> str:
    """Text as canon() keys word by word, apostrophes dropped first, so "don't" is "dont" and
    never Don; a possessive goes ("PPP's" is "ppp")."""
    from .people import canon

    text = re.sub(r"['\u2019]s\b", "", text or "", flags=re.IGNORECASE)
    return canon(re.sub(r"['\u2019]", "", text))


def name_index(people: dict) -> dict[str, set[str]]:
    """{person id: every name they answer to}, worked out once per run (people.names is a
    handful of regexes a record)."""
    from .people import names

    return {pid: names(rec, pid) for pid, rec in people.items()}


def title_people(title: str, people: dict, exclude: set[str], index: dict | None = None) -> dict[str, str]:
    """{person id: the name the title calls them} for everyone the title names, but the ids in
    exclude (the show's own people). The longest name wins where two overlap."""
    index = index if index is not None else name_index(people)
    words = f" {plain(title)} "
    found: dict[str, str] = {}
    taken: list[tuple[int, int]] = []
    cands = sorted(((n, pid) for pid in people if pid not in exclude for n in index[pid]),
                   key=lambda x: -len(x[0]))
    for n, pid in cands:
        i = words.find(f" {n} ")
        if i < 0 or pid in found or any(a < i + len(n) + 1 and i + 1 < b for a, b in taken):
            continue
        found[pid] = n
        taken.append((i + 1, i + 1 + len(n)))
    return found


@functools.lru_cache(maxsize=8)
def grams(text: str) -> Counter:
    """Every run of 1 to 4 words in text (already plain), counted."""
    toks = text.split()
    return Counter(" ".join(toks[i:i + k]) for k in range(1, 5) for i in range(len(toks) - k + 1))


def mentions(text: str, names: set[str]) -> int:
    """How often any of names (canon keys, up to four words) is said in text (already plain)."""
    g = grams(text)
    return sum(g[n] for n in names)


def stretch_text(words: list[dict], a: float, b: float) -> str:
    return plain(" ".join(w.get("word", "") for w in words if w["end"] > a and w["start"] < b))


def own_people(cfg: dict, people: dict) -> set[str]:
    """The show's own people ([meta] cast, and records whose cast is this show): on camera, so
    a title may name them though nobody says their name."""
    from .people import find

    show = (cfg.get("show") or {}).get("key") or ""
    out = {pid for pid, rec in people.items() if show and show in (rec.get("cast") or [])}
    for n in [*cfg["meta"]["cast"], cfg["meta"]["show_name"] or ""]:
        if not n:
            continue
        try:
            hit = find(cfg, n, people)
        except ClipperError:
            hit = None
        if hit:
            out.add(hit[0])
    return out


def item_spans(it: dict, segs: dict[str, dict]) -> list[tuple[float, float]]:
    """The stretches of stream time an item plays."""
    m = it["meta"]
    if it["kind"] in ("pick", "short"):
        return [(m["in_s"], m["out_s"])]
    if it["kind"] == "segment":
        return [(m.get("cut_start_s", m["start_s"]), m["end_s"])]
    return [(s.get("cut_start_s", s["start_s"]), s["end_s"]) for sid in it.get("segments", [])
            if (s := segs.get(sid))]


def segment_names(seg: dict | None, people: dict, cfg: dict) -> set[str]:
    """Every name of the people a segment is about (its topic and figures): the name as
    written, its last word (Angry Joe is "Joe" on air) and people.json's names for them."""
    from .people import canon, find, names

    if not seg:
        return set()
    out = set()
    for n in [seg.get("topic") or "", *(seg.get("figures") or [])]:
        if not n:
            continue
        out |= {canon(n), plain(n).rsplit(" ", 1)[-1]}
        try:
            hit = find(cfg, n, people)
        except ClipperError:
            hit = None
        if hit:
            out |= names(hit[1], hit[0])
    return {n for n in out if len(n) >= 3}


def subject_findings(sdir: Path, cfg: dict, it: dict, ctx: dict) -> list[tuple[str, str]]:
    people, index, words, segs = ctx["people"], ctx["names"], ctx["words"](), ctx["segments"]
    if not words:
        return []
    spans = item_spans(it, segs)
    if not spans:
        return []
    own = ctx["own"]
    pad = SUBJECT_PAD_S if it["kind"] in ("pick", "short") else 0
    text = " ".join(stretch_text(words, a - pad, b + pad) for a, b in spans)
    seg = ctx["segment_at"](spans[0][0]) if it["kind"] in ("pick", "short") else (
        segs.get(it["meta"].get("id")) if it["kind"] == "segment" else None)
    about = segment_names(seg, people, cfg)
    if it["kind"] == "compilation":
        for sid in it.get("segments", []):
            about |= segment_names(segs.get(sid), people, cfg)
    out = []
    from .review import bare_title

    named = title_people(bare_title(cfg, it["title"]), people, own, index)
    for pid in named:
        ns = index[pid]
        if mentions(text, ns) or ns & about:
            continue
        others = sorted(((mentions(text, index[q]), r["name"]) for q, r in people.items()
                         if q not in own and q != pid), reverse=True)
        also = f"; it names {others[0][1]} {others[0][0]} times" if others and others[0][0] >= 2 else ""
        out.append((BAD, f"the title names {people[pid]['name']}, but the transcript never does"
                         f" and its segment isn't about them{also}"))
    if it["kind"] in ("segment", "compilation"):
        subj = set(about)
        for pid in named:
            subj |= index[pid]
        f = opening_finding(cfg, words, spans, subj, people, own, index)
        if f:
            out.append(f)
    return out


OPENING_OTHER = 3   # an opening that names someone else this often, and the subject never


def opening(spans: list[tuple[float, float]], seconds: float) -> list[tuple[float, float]]:
    """The stream stretches of an item's first seconds of playback: a compilation plays its
    segments one after another, the stream time between them isn't in it."""
    out, left = [], seconds
    for a, b in spans:
        if left <= 0:
            break
        out.append((a, min(b, a + left)))
        left -= b - a
    return out


def opening_finding(cfg: dict, words: list[dict], spans: list[tuple[float, float]], subj: set[str],
                    people: dict, own: set[str], index: dict | None = None) -> tuple[str, str] | None:
    """A long item whose first [review] open_minutes never name its subject but name someone
    else OPENING_OTHER times or more: the cut may start on another story (the comp-angry-joe
    reject, 11-14: "the first 20 mins is just phil"). spans: the stream stretches it plays, in order."""
    m = cfg["review"]["open_minutes"]
    if not subj or not m or sum(b - a for a, b in spans) < 1.5 * m * 60:
        return None
    text = " ".join(stretch_text(words, a, b) for a, b in opening(spans, m * 60))
    if mentions(text, subj):
        return None
    index = index if index is not None else name_index(people)
    counts = sorted(((mentions(text, index[pid]), rec["name"]) for pid, rec in people.items()
                     if pid not in own and not (index[pid] & subj)), reverse=True)
    if not counts or counts[0][0] < OPENING_OTHER:
        return None
    n, who = counts[0]
    return (WARN, f"its first {m:g} minutes name {who} {n} times and its subject never: "
                  "the cut may start on another story")


# ---------- running them ----------

def signature(cfg: dict, it: dict, ctx: dict, vision: bool) -> str:
    """What the checks read, so an unchanged item isn't checked (or paid for) again."""
    f = it["file"]
    jpg = f.with_suffix(".jpg")
    cover = cover_entry(it, ctx) or {}
    rc = cfg["review"]
    segs = ctx["segments"]
    about = [segs.get(sid, {}) for sid in [it["meta"].get("id"), *it.get("segments", [])]]
    if it["kind"] in ("pick", "short"):
        about = [ctx["segment_at"](it["meta"]["in_s"]) or {}]
    parts = [VERSION, ctx.get("inputs"), [(s.get("topic"), s.get("figures")) for s in about],
             cfg["shorts"].get("layouts") if it["kind"] == "short" else None,
             f.name, f.stat().st_mtime if f.exists() else 0,
             jpg.stat().st_mtime if jpg.exists() else 0, it["text"], it["fields"].get("Thumbnail text"),
             it["fields"].get("Tags"), {k: cover.get(k) for k in ("frame", "face", "photo", "main_person", "text")},
             it["meta"].get("layout"), it["meta"].get("scenes"), cfg["checkup"]["words"],
             cfg["meta"].get("cast"), cfg["meta"].get("show_name"), cfg.get("bleep"),
             rc["check_frames"], rc["open_minutes"], rc["check_model"] if vision else "",
             rc["check_prompt_version"] if vision else ""]
    return hashlib.sha1(json.dumps(parts, default=str, sort_keys=True).encode()).hexdigest()[:16]


def inputs(sdir: Path, cfg: dict) -> list:
    """What every item's checks also read, for the signature: when people.json, the known
    faces or the transcript change, or [faces] match does, everything is checked again."""
    from . import faces, people

    def mtime(p: Path):
        try:
            return p.stat().st_mtime
        except OSError:
            return 0
    return [mtime(people.path(cfg)), mtime(faces.known_path(cfg)), mtime(sdir / "words.jsonl"),
            cfg["faces"]["match"]]


def context(sdir: Path, cfg: dict) -> dict:
    """What every item of a stream reads, loaded once (words only when asked)."""
    import bisect

    from . import people as people_mod

    people = people_mod.load(cfg)
    segdata = jsonstate.read_or(sdir / "segments.json", {})
    segs = {s["id"]: s for s in segdata.get("segments", [])}
    cut = sorted((s for s in segs.values() if s.get("cut")), key=lambda s: s["start_s"])
    starts = [s["start_s"] for s in cut]
    cache: dict = {}

    def words() -> list[dict]:
        if "w" not in cache:
            from .transcript import load_words

            try:
                cache["w"] = load_words(sdir)
            except FileNotFoundError:
                cache["w"] = []
        return cache["w"]

    def segment_at(t: float) -> dict | None:
        i = bisect.bisect_right(starts, t) - 1
        return cut[i] if i >= 0 and t < cut[i]["end_s"] else None

    return {"people": people, "names": name_index(people), "own": own_people(cfg, people),
            "segments": segs, "words": words, "inputs": inputs(sdir, cfg),
            "segment_at": segment_at, "thumbs": jsonstate.read_or(sdir / "thumbnails.json", {})}


CHECKS = ("text", "picture", "cover", "subject")


def check_item(sdir: Path, cfg: dict, it: dict, ctx: dict, claude) -> tuple[list[dict], dict]:
    """(findings, the vision answer). A check that crashes is a finding, so the rest still run
    and the item never passes on a broken check."""
    found: list[tuple[str, str, str]] = []
    vision: dict = {}
    for name in CHECKS:
        try:
            if name == "text":
                got = text_findings(cfg, it)
            elif name == "picture":
                got = picture_findings(sdir, cfg, it, ctx) if it["kind"] == "short" else []
            elif name == "cover":
                got, vision = cover_findings(sdir, cfg, it, ctx, claude)
            else:
                got = subject_findings(sdir, cfg, it, ctx)
        except Exception as e:  # one broken check mustn't pass the item
            if key_rejected(e):
                raise  # not the item's fault: exit KEY_REJECTED, so the stream is held (#281)
            got = [(BAD, f"the {name} check failed: {type(e).__name__}: {str(e)[:200]}")]
        found += [(name, level, text) for level, text in got]
    return [finding(f"{name}:{it['key']}:{n}", name, level, text, stream=sdir.name)
            for n, (name, level, text) in enumerate(found)], vision


def run(sdir: Path, cfg: dict, todo: list[dict] | None = None, keys: list[str] | None = None,
        force: bool = False, vision: bool | None = None, say=print, save: bool = True,
        kitsu: bool = False) -> dict[str, list[dict]]:
    """Check a stream's items (todo: review.items rows, else every one of [review] include, or
    those whose key starts with one of keys); checks.json keeps the results (save=False: a
    backtest, nothing written). kitsu: also bring each published item's Kitsu Checks column in
    line (every run but a publish's, which writes the column with the rest). Returns {key:
    findings} for the items checked or kept."""
    cfg = for_stream(cfg, sdir)
    rc = cfg["review"]
    todo = select(sdir, cfg, todo, keys)
    vision = bool(rc["check_model"]) if vision is None else (vision and bool(rc["check_model"]))
    started = datetime.now(UTC).isoformat(timespec="seconds")
    ctx = context(sdir, cfg)
    have = load(sdir)
    done: dict[str, dict] = {}
    fresh: dict[str, dict] = {}
    claude = None
    for it in todo:
        sig = signature(cfg, it, ctx, vision)
        old = have.get(it["key"])
        if old and old.get("sig") == sig and not force:
            done[it["key"]] = old
            continue
        if vision and claude is None:
            from .llm import Claude

            claude = Claude(cfg)
        found, v = check_item(sdir, cfg, it, ctx, claude if vision else None)
        done[it["key"]] = fresh[it["key"]] = {
            "sig": sig, "at": datetime.now(UTC).isoformat(timespec="seconds"), "kind": it["kind"],
            "findings": found, **({"vision": v} if v else {})}
        say(f"  {it['key']}: " + ("; ".join(f["text"] for f in found) if found else "clean"))
    if fresh and save:
        # only what this run checked, and not over a result another run (a publish and a hand
        # `review check` may overlap) wrote after this one started: that one read newer files
        with jsonstate.update(path(sdir), {}) as state:
            state.update({k: v for k, v in fresh.items() if (state.get(k) or {}).get("at", "") <= started})
    if kitsu and save and done:
        from . import review

        try:   # what checks.json kept: a newer run's result where one finished first (merge above)
            kept = load(sdir)
            review.set_checks(sdir, cfg, {k: summary((kept.get(k) or v).get("findings") or [])
                                          for k, v in done.items()})
        except Exception as e:  # noqa: BLE001  # checks.json has them; the next publish writes the column
            say(f"  Kitsu Checks column not updated: {type(e).__name__}: {e}")
    return {k: v["findings"] for k, v in done.items()}


def record_failure(sdir: Path, keys: list[str], why: str) -> None:
    """A run that failed outright: each item gets a finding with no signature, so it is
    checked again next time and nothing reads it as clean meanwhile."""
    at = datetime.now(UTC).isoformat(timespec="seconds")
    with jsonstate.update(path(sdir), {}) as state:
        for k in keys:
            state[k] = {"sig": "", "at": at,
                        "findings": [finding(f"run:{k}:0", "run", BAD, why, stream=sdir.name)]}


def select(sdir: Path, cfg: dict, todo: list[dict] | None, keys: list[str] | None) -> list[dict]:
    """todo, else every item of [review] include; with keys, those whose key (or its first
    part: v04, s02) is one of them. cfg: the stream's."""
    from . import review

    if todo is None:
        todo = review.items(sdir, cfg["review"]["include"], cfg)
    if keys:
        todo = [it for it in todo if any(it["key"] == k or it["key"].split("_")[0] == k for k in keys)]
    return as_edited(sdir, todo)


EDITABLE = ("YouTube title", "YouTube description", "Hashtags", "Tags", "Thumbnail text")


def as_edited(sdir: Path, todo: list[dict]) -> list[dict]:
    """The items as they'll upload: a title, description, hashtags or tags a reviewer changed in
    Kitsu (review.json, as of the last sync) over the draft's. Copies; todo is left alone."""
    fields = {k: (r.get("fields") or {}) for k, r in jsonstate.read_or(sdir / "review.json", {}).items()}
    out = []
    for it in todo:
        got = {n: v for n in EDITABLE if isinstance(v := fields.get(it["key"], {}).get(n), str) and v.strip()}
        if not got:
            out.append(it)
            continue
        it = {**it, "fields": {**it["fields"], **got}}
        it["title"] = got.get("YouTube title", it["title"])
        it["description"] = got.get("YouTube description", it["description"])
        tags = it["fields"].get("Hashtags")
        it["text"] = f"{it['title']}\n\n{it['description']}" + (f"\n\n{tags}" if tags else "")
        out.append(it)
    return out


def estimate(sdir: Path, cfg: dict, todo: list[dict] | None = None,
             keys: list[str] | None = None, force: bool = False) -> tuple[int, float]:
    """(vision calls a run would make, their rough cost in USD): items with a cover whose
    signature changed (every one with force)."""
    from . import llm

    cfg = for_stream(cfg, sdir)
    rc = cfg["review"]
    if not rc["check_model"]:
        return 0, 0.0
    todo = select(sdir, cfg, todo, keys)
    ctx = context(sdir, cfg)
    have = load(sdir)
    n = sum(1 for it in todo if it["file"].with_suffix(".jpg").exists()
            and (force or (have.get(it["key"]) or {}).get("sig") != signature(cfg, it, ctx, True)))
    # two images (~1,600 tokens each) and the prompt in, a short answer out
    return n, n * llm.estimate(cfg, rc["check_model"], 4000, 300)


def lines(results: dict[str, list[dict]]) -> list[str]:
    flagged = {k: v for k, v in results.items() if v}
    out = [f"{len(results)} items checked, {len(flagged)} with findings"]
    for k, fs in flagged.items():
        out.append(f"  {k}")
        out += [f"    {'BAD ' if f['level'] == BAD else 'warn'} {f['check']:<8} {f['text']}" for f in fs]
    return out


def flagged_keys(sdir: Path) -> set[str]:
    """Items checks.json has findings for."""
    return {k for k, v in load(sdir).items() if v.get("findings")}

