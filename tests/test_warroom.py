import http.client
import re
import threading
from http.server import ThreadingHTTPServer

import pytest

from clippa import actions, warroom
from clippa.errors import ClipperError


@pytest.fixture
def serve(defaults):
    servers = []

    def start(token: str) -> int:
        cfg = {**defaults, "warroom": {**defaults["warroom"], "token": token}}
        srv = ThreadingHTTPServer(("127.0.0.1", 0), warroom.make_handler(cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv.server_address[1]
    yield start
    for srv in servers:
        srv.shutdown()


def status(port: int, path: str, host: str, key: str = "", method: str = "GET") -> int:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}
    if key:
        headers["X-Key"] = key
    c.request(method, path, body="{}" if method == "POST" else None, headers=headers)
    return c.getresponse().status


def test_without_a_token_only_local_hosts(serve):
    """B13: a DNS-rebinding page has Host: evil.example though it reaches 127.0.0.1."""
    port = serve("")
    assert status(port, "/api/nope", "evil.example") == 403
    assert status(port, "/api/nope", "evil.example", method="POST") == 403
    assert status(port, "/api/nope", f"localhost:{port}") == 404   # reached the router
    assert status(port, "/api/nope", "127.0.0.1") == 404


def test_with_a_token_any_host_with_the_key(serve):
    """The tailnet name (phone via tailscale serve) works once the key is right."""
    port = serve("t0ken")
    assert status(port, "/api/nope", "laptop.tail1234.ts.net", key="t0ken") == 404
    assert status(port, "/api/nope", "laptop.tail1234.ts.net") == 401


def test_routes_are_tables():
    assert "/api/overview" in warroom.GET_API and "/api/action" in warroom.POST_API
    assert set(warroom.POST_API) >= {"/api/undo", "/api/bulk", "/api/replan", "/api/retake"}


def test_a_refused_post_still_gets_its_answer(serve, monkeypatch):
    """The body is read before any refusal (an unread body resets the connection on
    Windows), and one past MAX_POST isn't read at all."""
    port = serve("")
    for _ in range(20):
        assert status(port, "/api/nope", "evil.example", method="POST") == 403
    monkeypatch.setattr(warroom.httpd, "MAX_POST", 1)
    assert status(port, "/api/nope", "localhost", method="POST") == 413


def test_page_code_is_served_without_the_key(serve):
    """The stylesheet and scripts hold no secrets; they're fetched fresh so a restart's
    page never runs old code. Nothing outside web/ and no other file type is served."""
    port = serve("t0ken")
    for path, ctype in (("/warroom.css", "text/css"), ("/js/app.js", "text/javascript")):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", path)
        r = c.getresponse()
        assert r.status == 200 and r.getheader("Content-Type").startswith(ctype)
        assert r.getheader("Cache-Control") == "no-cache"
    for path in ("/js/../../warroom.py", "/../config.py", "/js/nope.js", "/warroom.html"):
        assert status(port, path, "localhost") == 401   # not a page file: the key is still asked


def test_the_page_loads_every_script_app_last():
    html = warroom.PAGE.read_text(encoding="utf-8")
    scripts = re.findall(r'<script src="js/([\w-]+\.js)"></script>', html)
    assert sorted(scripts) == sorted(p.name for p in (warroom.WEB / "js").glob("*.js"))
    assert scripts[-1] == "app.js" and '<link rel="stylesheet" href="warroom.css">' in html
    assert "<script>" not in html and "<style>" not in html


def test_every_page_has_its_tab_and_its_place():
    """The page bar, app.js's PAGES and the page groups name the same eight pages, each once, and
    the CSS shows each one; every panel sits in a page, and every data-go names one."""
    html = warroom.PAGE.read_text(encoding="utf-8")
    js = (warroom.WEB / "js" / "app.js").read_text(encoding="utf-8")
    css = (warroom.WEB / "warroom.css").read_text(encoding="utf-8")
    pages = re.findall(r'"([a-z]+)"', re.search(r"const PAGES = \[(.*?)\]", js).group(1))
    assert len(pages) == len(set(pages)) == 8 and pages[0] == "queue"
    assert re.findall(r'<div class="page" data-page="([a-z]+)">', html) == pages
    assert [v for v in re.findall(r'data-view="([a-z]+)"', html) if v != "chat"] == pages
    for v in pages:
        assert f'body[data-view="{v}"] .page[data-page="{v}"]' in css
    assert set(re.findall(r'data-go="([a-z]+)"', html)) <= set(pages)
    main = html[html.index("<main>"):html.index("</main>")]
    assert html.count("data-panel=") == main.count("data-panel=") > 0
    # every page has its G key (palette.js), its name in the palette, and a row in the ? list
    pal = (warroom.WEB / "js" / "palette.js").read_text(encoding="utf-8")
    keys = dict(re.findall(r'"?([a-z,])"?: "([a-z]+)"', re.search(r"const GO_KEYS = \{(.*?)\};", pal).group(1)))
    names = dict(re.findall(r'([a-z]+): "([A-Z][a-z]+)"', re.search(r"const PAGE_NAME = \{(.*?)\};", pal, re.S).group(1)))
    assert sorted(keys.values()) == sorted(pages) == sorted(names)
    for k, v in keys.items():
        assert f'["G {k.upper()}", "{names[v]}"]' in js, (k, v)


def test_the_page_has_no_mis_encoded_text():
    """UTF-8 read as cp1252 and saved again: a middle dot became "Â·" in the Health panel (#206)."""
    broken = re.compile("[ÂÃ][\x80-\xbf]|â[€œš]")
    files = [warroom.PAGE, warroom.WEB / "warroom.css", *(warroom.WEB / "js").glob("*.js")]
    assert not [(p.name, m.group()) for p in files for m in broken.finditer(p.read_text(encoding="utf-8"))]


def test_faces_window(tmp_path, defaults, monkeypatch):
    import numpy as np

    from clippa import faces, jsonstate

    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path, "faces": str(tmp_path / "known")}}
    monkeypatch.setattr(warroom.faces, "for_stream", lambda c, sdir: cfg)
    sdir = tmp_path / "kick-1"
    (sdir / "faces").mkdir(parents=True)
    jsonstate.write(sdir / "meta.json", {"channel": "x"})
    with pytest.raises(ClipperError):
        warroom.faces_get(cfg, "kick-2")
    with pytest.raises(ClipperError):
        warroom.faces_get(cfg, "kick-1/c01")
    assert warroom.faces_get(cfg, "kick-1")["scanned"] is False
    rng = np.random.default_rng(0)
    emb = np.array([faces.unit(c + rng.normal(scale=0.2, size=128)) for c in rng.normal(size=(2, 128)) for _ in range(5)])
    rows = [{"t": float(k), "box": [0.4, 0.3, 0.1, 0.2], "px": 90, "sharp": 50.0, "frontal": 0.9} for k in range(10)]
    groups, fs = faces.build_groups(rows, emb, {"cluster": 0.4, "min_faces": 3})
    jsonstate.write(sdir / "faces.json", {"model": faces.MODEL, "frames": 10, "groups": groups, "faces": fs})
    (sdir / "faces" / "F01.jpg").write_bytes(b"jpg")
    got = warroom.faces_get(cfg, "kick-1")
    assert [g["key"] for g in got["groups"]] == ["F01"] and got["more"] == 1
    assert got["groups"][0]["sheet_url"].startswith("/media/kick-1/faces/F01.jpg?v=")
    with pytest.raises(ClipperError):
        warroom.faces_name(cfg, {"stream": "kick-1", "names": {"../x": "Jer"}})
    got = warroom.faces_name(cfg, {"stream": "kick-1", "names": {"F02": "Jer"}, "enroll": True})
    assert got["known"] == {"Jer": 1} and [g["key"] for g in got["groups"]] == ["F01", "F02"]
    assert got["groups"][1]["name"] == "Jer"
    got = warroom.faces_forget(cfg, {"stream": "kick-1", "name": "Jer"})
    assert got["known"] == {} and got["groups"][1]["by"] == "hand"  # a hand name stays
    for bad in ({}, {"x": 0, "y": 0, "width": 0, "height": 0.5}, {"x": 1.2, "y": 0, "width": 0.1, "height": 0.1}):
        with pytest.raises(ClipperError):
            warroom.checked_box(bad)
    assert warroom.checked_box({"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}) == [0.1, 0.2, 0.3, 0.4]


def test_face_pick_grabs_the_cut_or_a_compilations_preview(tmp_path, defaults, monkeypatch):
    from clippa import faces

    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path}}
    sdir = tmp_path / "kick-1"
    (sdir / "review").mkdir(parents=True)
    (sdir / "review" / "comp-x.mp4").write_bytes(b"")
    (sdir / "review" / "c01_t20.mp4").write_bytes(b"")
    states = {"c01_t20": {"kind": "pick", "file": r"clips\01_t20.mp4"},
              "comp-x": {"kind": "compilation", "file": r"compilations\x.mp4"}}
    monkeypatch.setattr(actions, "resolve", lambda c, ref: actions.Item(sdir, ref.split("/")[1], states[ref.split("/")[1]]))
    grabbed = []
    monkeypatch.setattr(faces, "grab", lambda video, t, dst: grabbed.append(video) or dst)
    monkeypatch.setattr(faces, "pick", lambda img, box, c: {"box": box, "px": 90, "embedding": [], "closest": "", "match": 0.0, "tile": b"jpg"})
    monkeypatch.setattr(warroom.faces, "for_stream", lambda c, d: cfg)
    box = {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}
    got = warroom.faces_pick(cfg, {"ref": "kick-1/c01_t20", "t": 3.0, "box": box})
    assert got["tile"].startswith("data:image/jpeg;base64,") and got["stream"] == "kick-1"
    warroom.faces_pick(cfg, {"ref": "kick-1/comp-x", "t": 3.0, "box": box})
    assert grabbed == [sdir / "clips/01_t20.mp4", sdir / "review" / "comp-x.mp4"]
    with pytest.raises(ClipperError):
        warroom.faces_pick(cfg, {"ref": "kick-1/c01_t20", "t": "soon", "box": box})


def test_investigations_list_newest_first_and_render_the_one_asked_for(defaults, tmp_path):
    from clippa import jsonstate

    defaults["paths"]["archive"] = tmp_path
    for subject, made in (("jer", "2026-11-03T00:00:00+00:00"), ("dsp", "2026-11-04T00:00:00+00:00")):
        (tmp_path / subject / "investigate").mkdir(parents=True)
        jsonstate.write(tmp_path / subject / "investigate" / "leads.json",
                        {"subject": subject, "about": "", "made_at": made, "model": "m", "candidates": 0, "leads": []})
    got = warroom.investigations_get(defaults)
    assert [s["subject"] for s in got["subjects"]] == ["dsp", "jer"] and got["subject"] == "dsp"
    assert warroom.investigations_get(defaults, "jer")["markdown"].startswith("# Leads: jer")
    assert warroom.investigations_get(defaults, "../etc")["subject"] == "dsp"  # only subjects on file
    gone = warroom.investigations_get(defaults, "jer", "20200101000000")
    assert gone["missing_run"] and gone["markdown"] == ""
    assert warroom.investigations_get(defaults, "jer", "20261103000000")["run"] == "20261103000000"


def test_next_slot_for_every_segment_leaves_out_a_full_budget(tmp_path, defaults, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from clippa import jsonstate, schedule

    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path, "schedule": tmp_path / "schedule.json"}}
    jsonstate.write(cfg["paths"]["schedule"], {"entries": []})
    row = lambda ref, **kw: {"ref": ref, "stream": ref.split("/")[0], "kind": "segment", "title": ref,
                             "decision": "approved", "hold": False, "scheduled": "", "uploaded": False, **kw}
    monkeypatch.setattr(warroom.review, "all_rows", lambda c: [row("a/x"), row("b/y"), row("b/held", hold=True),
                                                        row("b/no", decision="pending")])
    monkeypatch.setattr(schedule, "release_window", lambda c, sdir, meta: (datetime.now(UTC) - timedelta(days=1), None))
    auto = {"key": "a/auto", "title": "a/auto", "publish_at": "2026-11-09T16:00:00-08:00"}

    def with_pins(c, pins):  # stream a's budget is full: pinning a/x bumps a/auto
        if "a/x" in pins:
            return {}, [{"key": "a/auto", "reason": "over the stream's segment budget (1)"}]
        return {"a/auto": auto}, []
    monkeypatch.setattr(schedule, "with_pins", with_pins)

    out = warroom.slot_plan(cfg, kind="segment")
    assert [p["ref"] for p in out["plan"]] == ["b/y"] and out["bumped"] == []
    assert [(r["ref"], r["why"][:27]) for r in out["refused"]] == [("a/x", "its stream's budget is full")]

    out = warroom.slot_plan(cfg, ["a/x", "b/held", "b/no"])  # one you pick may swap
    assert [p["ref"] for p in out["plan"]] == ["a/x"]
    assert out["bumped"] == [{"ref": "a/auto", "title": "a/auto", "at": auto["publish_at"],
                              "why": "over the stream's segment budget (1)"}]
    assert {r["ref"]: r["why"] for r in out["refused"]} == {"b/held": "held", "b/no": "not approved"}

    shown = warroom.do_slot(cfg, {"kind": "segment", "dry_run": True})
    assert shown["plan"][0]["let_go"] == "" and shown["print"]
    moved = warroom.do_slot(cfg, {"kind": "segment", "print": "an older preview"})  # Apply pins nothing
    assert moved["changed"] and moved["done"] == 0 and moved["print"] == shown["print"]


def test_hidden_shows_leave_the_page(tmp_path):
    from clippa import config
    path = tmp_path / "clipper.toml"
    path.write_text('[warroom]\nhide_shows = ["trish"]\n[shows.kino]\nchannels = ["k"]\n'
                    '[shows.trish]\nchannels = ["t"]\n', encoding="utf-8")
    assert list(warroom.shown_shows(config.load(path))) == ["kino"]
    # a show's colour is its place in [shows]: hiding kino moves no other show's colour
    path.write_text(path.read_text(encoding="utf-8").replace('hide_shows = ["trish"]', 'hide_shows = ["kino"]'), encoding="utf-8")
    assert warroom.show_hues(config.load(path)) == {"kino": 1, "trish": 2}


def test_bad_headers_and_queries_are_bad_requests_not_crashes(serve, defaults, tmp_path):
    """A malformed Content-Length, Range or ?t= answers with a status, never a 500 (Copilot on #176)."""
    defaults["paths"]["streams"] = tmp_path / "streams"   # before serve(): the handler reads it then
    port = serve("")
    root = defaults["paths"]["streams"]
    (root / "kick-1").mkdir(parents=True)
    (root / "kick-1" / "c.mp4").write_bytes(b"0123456789")

    def get(path: str, **headers) -> tuple[int, bytes, dict]:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", path, headers={"Host": "localhost", **headers})
        r = c.getresponse()
        return r.status, r.read(), dict(r.getheaders())

    assert get("/media/kick-1/c.mp4", Range="bytes=2-4")[:2] == (206, b"234")
    assert get("/media/kick-1/c.mp4", Range="bytes=2-4")[2]["Content-Range"] == "bytes 2-4/10"
    assert get("/media/kick-1/c.mp4", Range="bytes=abc-def")[:2] == (200, b"0123456789")   # ignored
    code, body, hdrs = get("/media/kick-1/c.mp4", Range="bytes=100-")
    assert (code, body, hdrs["Content-Range"]) == (416, b"", "bytes */10")
    assert get("/watch?src=/media/kick-1/c.mp4&t=abc")[0] == 200   # from the start

    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest("POST", "/api/nope")
    c.putheader("Host", "localhost")
    c.putheader("Content-Length", "abc")
    c.endheaders()
    assert c.getresponse().status == 400


def test_tailscale_json_that_isnt_is_an_empty_status():
    from types import SimpleNamespace

    j = warroom.tailscale._json
    assert j(SimpleNamespace(stdout='{"BackendState": "Running"}')) == {"BackendState": "Running"}
    assert j(SimpleNamespace(stdout="Log in at https://login.tailscale.com/...")) == {}
    assert j(SimpleNamespace(stdout="")) == {} and j(SimpleNamespace(stdout="[1, 2]")) == {}


def test_sync_all_starts_one_sync_for_two_requests(defaults, monkeypatch):
    import time

    calls = []

    def slow_sync(d, cfg, quiet=False):
        calls.append(d)
        time.sleep(0.3)
    monkeypatch.setattr(warroom.review.review, "sync", slow_sync)
    monkeypatch.setattr(warroom.review.actions, "review_dirs", lambda cfg: [defaults["paths"]["streams"] / "kick-1"])
    warroom.SYNC.update(running=False, error="")
    warroom.sync_all(defaults)
    warroom.sync_all(defaults)   # the same instant: still one thread
    time.sleep(0.6)
    assert len(calls) == 1 and warroom.SYNC["running"] is False and warroom.SYNC["last"]


def test_signed_out_page_is_a_page(serve):
    """#206: an expired cookie on the page itself is a sign-in hint, not raw JSON; the API
    still answers JSON."""
    port = serve("t0ken")
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/", headers={"Host": "laptop.tail1234.ts.net"})
    r = c.getresponse()
    body = r.read().decode("utf-8")
    assert r.status == 401 and r.getheader("Content-Type").startswith("text/html")
    assert "?key=" in body and "t0ken" not in body
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/api/overview", headers={"Host": "laptop.tail1234.ts.net"})
    r = c.getresponse()
    assert r.status == 401 and r.getheader("Content-Type").startswith("application/json")


def test_retake_cost():
    """#203: the mean of recent retakes that recorded a cost, and this month's."""
    from clippa.warroom.data import retake_cost

    hs = [{"at": "2026-11-12T10:00:00+00:00", "cost_usd": 0.03}, {"at": "2026-11-01T10:00:00+00:00", "cost_usd": 0.01},
          {"at": "2026-11-01T09:00:00+00:00"}, {"at": "2026-10-30T10:00:00+00:00", "cost_usd": 0.02}]
    assert retake_cost(hs, "2026-11") == {"avg_usd": 0.02, "month_n": 3, "month_usd": 0.04}
    assert retake_cost([], "2026-11") == {"avg_usd": None, "month_n": 0, "month_usd": 0}
    # the month is the show's: 2026-12-01 03:00 UTC is still November in Los Angeles
    from zoneinfo import ZoneInfo
    late = [{"at": "2026-12-01T03:00:00+00:00", "cost_usd": 0.01}, {"at": "", "cost_usd": 0.01}]
    assert retake_cost(late, "2026-11", ZoneInfo("America/Los_Angeles"))["month_n"] == 1
    assert retake_cost(late, "2026-11")["month_n"] == 0


def test_cast_find_estimate(tmp_path, defaults):
    """#203: a Find's label cost before it runs, from past cast label calls in the costs log."""
    import json

    from clippa import costs
    from clippa.warroom.cast import find_estimate

    log = tmp_path / "costs.jsonl"
    cfg = {**defaults, "paths": {**defaults["paths"], "costs": log}}
    assert find_estimate(cfg)["label_usd"] is None   # no log yet
    log.write_text("\n".join(json.dumps(r) for r in (
        {"kind": "llm", "stage": "cast grab", "usd": 0.0003}, {"kind": "llm", "stage": "score", "usd": 0.5},
        {"kind": "llm", "stage": "cast auto", "usd": 0.0005}, {"kind": "modal", "stage": "cast", "usd": 9})) + "\n")
    assert find_estimate(cfg)["label_usd"] is None   # cached for a while: the log grows all the time
    costs._AVG.clear()
    est = find_estimate(cfg)
    assert est["per_source"] == defaults["cast"]["per_source"] and est["label_usd"] == pytest.approx(0.0004)
    cfg["cast"] = {**defaults["cast"], "label_model": ""}
    assert find_estimate(cfg)["label_usd"] == 0.0


def test_page_scripts_parse():
    """#206: a syntax error in one script blanks the page; CI's runners have node."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("no node on PATH")
    for p in sorted([*(warroom.WEB / "js").glob("*.js"), *warroom.WEB.glob("*.js")]):   # the war room's, the chat's, theme.js
        r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, f"{p.name}: {r.stderr[-500:]}"


def test_log_tail_filters_and_blanks_secrets(tmp_path, defaults, monkeypatch):
    from clippa.warroom import logview
    log = tmp_path / "serve.log"
    log.write_text("".join(f"2026-11-13 10:00:{k:02d} INFO    clipper.serve: job {k} ok\n" for k in range(50))
                   + "2026-11-13 10:01:00 WARNING clipper.warroom: kick-1: KeyError: 'x'\n"
                   + "2026-11-13 10:01:01 INFO    clipper.serve: thumbs exit 2\n"
                   + "2026-11-13 10:01:02 INFO    clipper.serve: called with sk-ant-0123456789abcdef\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-0123456789abcdef")
    cfg = {**defaults, "paths": {**defaults["paths"], "serve_log": log}}
    r = logview.log_tail(cfg, "serve", "5")
    assert len(r["lines"]) == 5 and r["lines"][-1].endswith("called with •••")
    assert not any("sk-ant" in x for x in r["lines"])
    bad = logview.log_tail(cfg, "serve", "100", bad="1")["lines"]
    assert [x.split(": ", 1)[1] for x in bad] == ["kick-1: KeyError: 'x'", "thumbs exit 2"]
    assert len(logview.log_tail(cfg, "serve", "100", grep="KeyError kick-1")["lines"]) == 1   # every word, any case
    assert logview.log_tail(cfg, "nope", "x")["which"] == "serve"   # unknown names and counts: the defaults
    none = logview.log_tail({**cfg, "paths": {**cfg["paths"], "bot_log": tmp_path}}, "bot")
    assert none["lines"] == [] and "console only" in none["note"]
    assert "No bot log" in logview.log_tail({**cfg, "paths": {**cfg["paths"], "bot_log": tmp_path / "bot.log"}}, "bot")["note"]


def test_log_tail_reads_only_the_end_of_a_big_file(tmp_path, monkeypatch):
    from clippa.warroom import logview
    monkeypatch.setattr(logview, "TAIL_BYTES", 100)
    p = tmp_path / "x.log"
    p.write_text("".join(f"line {k:04d}\n" for k in range(1000)), encoding="utf-8")
    got = logview.tail(p, 1000)
    assert got[-1] == "line 0999" and 0 < len(got) <= 10 and all(x.startswith("line ") and len(x) == 9 for x in got)


def test_log_scrub_covers_secret_files_and_url_keys(tmp_path, defaults, monkeypatch):
    from clippa.warroom import logview
    sec = tmp_path / "secrets"
    sec.mkdir()
    (sec / "dvids_secret.txt").write_text("pubkey-0123456789\nsecretkey-abcdefghij\n", encoding="utf-8")
    (sec / "x_token.json").write_text('{"access_token": "tok-abcdefghijkl", "scope": "tweet.write"}', encoding="utf-8")
    (sec / "youtube_cookies.txt").write_text("cookiejar-value-not-a-key\n", encoding="utf-8")
    log = tmp_path / "serve.log"
    log.write_text("used secretkey-abcdefghij and tok-abcdefghijkl, scope tweet.write\n"
                   "404 for url: https://api.example/search?q=moon&api_key=zzzzzzzz12345&page=2\n", encoding="utf-8")
    cfg = {**defaults, "paths": {**defaults["paths"], "serve_log": log, "x_token": sec / "x_token.json"}}
    a, b = logview.log_tail(cfg, "serve")["lines"]
    assert a == "used ••• and •••, scope tweet.write"
    assert b == "404 for url: https://api.example/search?q=moon&api_key=•••&page=2"
    assert "cookiejar-value-not-a-key" not in logview._secrets(cfg)
    assert logview.log_tail(cfg, "serve", bad="0")["lines"] == [a, b]   # "0" is off


def test_log_filters_see_only_scrubbed_text(tmp_path, defaults, monkeypatch):
    """Copilot on #244: a filter on the raw line would confirm a key's prefix; short keys count too."""
    from clippa.warroom import logview
    monkeypatch.setenv("SOME_API_KEY", "k3y9")
    log = tmp_path / "serve.log"
    log.write_text("ERROR called with k3y9 and sk-live-abcdef123456\n", encoding="utf-8")
    monkeypatch.setenv("OTHER_TOKEN", "sk-live-abcdef123456")
    cfg = {**defaults, "paths": {**defaults["paths"], "serve_log": log}}
    assert logview.log_tail(cfg)["lines"] == ["ERROR called with ••• and •••"]
    for probe in ("sk-live-a", "k3y", "abcdef"):
        assert logview.log_tail(cfg, grep=probe)["lines"] == []
        assert logview.log_tail(cfg, grep=probe, bad="1")["lines"] == []
    assert logview.log_tail(cfg, grep="•••")["lines"]   # what is shown can be searched


def test_the_chat_page_is_its_own(tmp_path, defaults):
    """/chat (#158): its own markup, style and script beside the war room's, served by the same
    server behind the same key. The war room shows that same page in a frame at its side, so the
    two share one code path; it never loads the chat's script or style itself."""
    from clippa.warroom import httpd

    html = httpd.PAGES["/chat"].read_text(encoding="utf-8")
    assert '<script src="/chat.js"></script>' in html and '<link rel="stylesheet" href="/chat.css">' in html
    room = warroom.PAGE.read_text(encoding="utf-8")
    assert "chat.js" not in room and "chat.css" not in room
    assert re.search(r'<iframe id="chatFrame" src="/chat\?dock"', room)
    assert httpd.static_file("/chat.js")[1].startswith("text/javascript")
    assert httpd.static_file("/chat.css")[1].startswith("text/css")
    broken = re.compile("[ÂÃ][\x80-\xbf]|â[€œš]")
    for name in ("chat.html", "chat.css", "chat.js", "theme.js", "js/chatdock.js", *(f"themes/{p.name}" for p in (warroom.WEB / "themes").glob("*.css"))):
        assert not broken.search((warroom.WEB / name).read_text(encoding="utf-8")), name
    assert "/api/chat" in httpd.GET_API and "/api/chat/send" in httpd.POST_API


def test_the_look_has_its_fonts_and_one_icon_set():
    """The default look's fonts ship in web/fonts/ and serve sends them; every icon the page and
    its scripts ask for is in the one sprite, and no emoji stands in for one."""
    from clippa.warroom import httpd

    theme = (warroom.WEB / "themes" / "warroom.css").read_text(encoding="utf-8")
    fonts = re.findall(r'url\("\.\./fonts/([\w.-]+)"\)', theme)
    assert len(fonts) == 3
    for f in fonts:
        assert (warroom.WEB / "fonts" / f).is_file(), f
        path, ctype, cache = httpd.static_file(f"/fonts/{f}")
        assert ctype == "font/woff2" and "max-age" in cache
    assert httpd.static_file("/fonts/Inter-OFL.txt") is None   # only what the page loads
    assert httpd.static_file("/fonts/../../warroom/httpd.py") is None
    page = re.sub(r"<!--.*?-->", "", warroom.PAGE.read_text(encoding="utf-8"), flags=re.S)
    have = set(re.findall(r'<symbol id="i-([\w-]+)"', page))
    scripts = " ".join(p.read_text(encoding="utf-8") for p in (warroom.WEB / "js").glob("*.js"))
    asked = set(re.findall(r'href="#i-([\w-]+)"', page)) | set(re.findall(r'\bic\("([\w-]+)"\)', scripts))
    asked |= {n for pair in re.findall(r'\bic\([^)]*\?\s*"([\w-]+)"\s*:\s*"([\w-]+)"\)', scripts) for n in pair}
    assert asked and not asked - have, sorted(asked - have)
    # a click on an icon lands on its button: handlers check e.target.id (Undo, Select shown, Log)
    css = (warroom.WEB / "warroom.css").read_text(encoding="utf-8")
    assert "pointer-events: none" in re.search(r"^\.ic \{[^}]*\}", css, re.M).group()
    # a rule sizing a panel's charts (".stats svg") would size its icons too: it says svg:not(.ic)
    assert not re.findall(r"[^,{}]*\bsvg\s*[{,]", css), "scope it: svg:not(.ic)"
    # emoji, and the symbols the page drew icons with before the sprite (the arrows but the plain
    # four, technical, shapes, supplemental arrows); a key name's or a sentence's arrow is fine
    emoji = re.compile("[\U0001F300-\U0001FAFF↕-⇿⌀-⏿■-◿☀-➿⟰-⟿⤀-⧿⬀-⯿]")
    assert not emoji.search(page + scripts), emoji.findall(page + scripts)


def test_heat_buckets_keep_the_hottest_second_and_the_gaps():
    from clippa.warroom import heat

    nan = float("nan")
    assert heat.buckets([]) == (1, [])
    assert heat.buckets([0.5, 2.0, nan]) == (1, [0.5, 2.0, None])   # short enough: second by second
    # 7 seconds into 3 points: 3 s a bucket, the peak kept, a bucket with no chat at all stays a gap
    assert heat.buckets([1, 9.126, 2, nan, nan, nan, 3], points=3) == (3, [9.13, None, 3.0])
    step, vals = heat.buckets([nan, 4, nan, nan] * 5000, points=1200)
    assert step == 17 and len(vals) == 1177 and set(vals) == {4.0}


def test_heat_windows_without_their_numbers_are_left_out():
    from clippa.warroom import heat

    w = {"start_s": 1, "end_s": 9, "peak_s": 5, "heat": 2.5}
    got = heat.windows([{**w, "heat": None}, {**w, "chat_z": "7", "transcript_peak": None}, {**w, "rank": 9, "end_s": float("nan")}])
    assert got == [{"start_s": 1, "end_s": 9, "peak_s": 5, "heat": 2.5, "rank": 2, "chat_z": None, "react_z": None,
                    "clip_z": None, "loud_z": None, "said": ""}]


def test_heat_state_is_one_word_for_where_an_item_is():
    from clippa.warroom import heat

    row = lambda **kw: {"decision": "pending", "scheduled": "", "uploaded": False, **kw}
    assert heat.state_of(None) == "cut"
    assert heat.state_of(row()) == "waiting"
    assert heat.state_of(row(decision="changes_requested")) == "changes"
    assert heat.state_of(row(decision="approved")) == "approved"
    assert heat.state_of(row(decision="approved", scheduled="2026-11-20T10:00:00-08:00")) == "scheduled"
    assert heat.state_of(row(decision="approved", scheduled="x", uploaded=True)) == "uploaded"
    assert heat.state_of(row(decision="rejected", scheduled="x")) == "rejected"


def test_heat_window_gives_the_curve_the_windows_and_where_each_item_sits(tmp_path, defaults, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from clippa import jsonstate

    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path, "schedule": tmp_path / "schedule.json"}}
    monkeypatch.setattr(warroom.heat, "for_stream", lambda c, sdir: cfg)
    warroom.CACHE.clear()
    sdir = tmp_path / "kick-1"
    sdir.mkdir()
    with pytest.raises(ClipperError):
        warroom.heat_get(cfg, "kick-1")   # no meta.json: not a stream
    with pytest.raises(ClipperError):
        warroom.heat_get(cfg, "../kick-1")
    jsonstate.write(sdir / "meta.json", {"title": "A show", "duration_s": 90})
    got = warroom.heat_get(cfg, "kick-1")
    assert got["heat"] == [] and got["items"] == [] and got["duration_s"] == 90 and not got["error"]

    (sdir / "signals.parquet").write_bytes(b"not a parquet file")
    assert "clipper signals" in warroom.heat_get(cfg, "kick-1")["error"]
    pq.write_table(pa.table({"t": list(range(100)), "heat": [float("nan")] * 10 + [1.0] * 89 + [7.5]}),
                   sdir / "signals.parquet")
    jsonstate.write(sdir / "windows.json", {"chat_coverage": 0.9, "params": {"min_heat": 1.0}, "windows": [
        {"start_s": 60, "end_s": 100, "peak_s": 99, "heat": 7.5, "rank": 1, "chat_z": 5.0, "react_z": 2.0,
         "clip_z": 0.0, "loud_z": 1.0, "transcript_peak": "x" * 300, "chat_sample": ["lol"]},
        {"start_s": 10, "end_s": 40, "peak_s": 30, "rank": 2},            # no heat: left out, not a crash
        {"start_s": 20, "end_s": 50, "peak_s": 45, "heat": "<b>"}, "not a window"]})
    items = [{"key": "v01_t02", "kind": "short", "title": "A Short", "meta": {"in_s": 70.04, "out_s": 95.0}},
             {"key": "s01_a-topic", "kind": "segment", "title": "A segment",
              "meta": {"start_s": 12.0, "cut_start_s": 10.0, "end_s": 80.0}},
             {"key": "v02_t09", "kind": "short", "title": "An old sidecar, no in and out", "meta": {}}]
    asked = []
    monkeypatch.setattr(warroom.heat.review, "items", lambda d, include, c: asked.append(include) or items)
    got = warroom.heat_get(cfg, "kick-1")
    assert got["duration_s"] == 100 and got["bucket_s"] == 1 and got["heat"][:11] == [None] * 10 + [1.0]
    assert got["heat"][-1] == 7.5 and got["min_heat"] == 1.0 and got["chat_coverage"] == 0.9
    assert got["windows"] == [{"start_s": 60, "end_s": 100, "peak_s": 99, "heat": 7.5, "rank": 1, "chat_z": 5.0,
                               "react_z": 2.0, "clip_z": 0.0, "loud_z": 1.0, "said": "x" * 160}]
    assert "compilations" not in asked[0] and "shorts" in asked[0]
    # not published: every item is "cut", with no window to open; a segment starts where its cut does
    assert [(i["key"], i["start_s"], i["end_s"], i["state"], i["ref"]) for i in got["items"]] == [
        ("s01_a-topic", 10.0, 80.0, "cut", ""), ("v01_t02", 70.0, 95.0, "cut", "")]

    jsonstate.write(sdir / "review_state.json", {})
    monkeypatch.setattr(warroom.heat, "stream_rows", lambda c, d: [
        {"key": "v01_t02", "ref": "kick-1/v01_t02", "title": "The title you edited", "decision": "approved",
         "scheduled": "2026-11-20T10:00:00-08:00", "uploaded": False, "hold": True},
        {"key": "s01_a-topic", "ref": "kick-1/s01_a-topic", "title": "A segment", "decision": "approved",
         "scheduled": "", "uploaded": False, "hold": False, "dropped": "over the stream's segment budget (1)"}])
    got = {i["key"]: i for i in warroom.heat_get(cfg, "kick-1")["items"]}
    assert got["v01_t02"] == {"key": "v01_t02", "kind": "short", "title": "The title you edited", "start_s": 70.0,
                              "end_s": 95.0, "state": "scheduled", "hold": True, "dropped": "", "ref": "kick-1/v01_t02"}
    assert (got["s01_a-topic"]["state"], got["s01_a-topic"]["dropped"]) == ("approved", "over the stream's segment budget (1)")
    assert len(asked) == 2   # the curve and the spans were read again only because a file was added

    monkeypatch.setattr(warroom.heat.review, "items", lambda d, include, c: (_ for _ in ()).throw(FileNotFoundError()))
    (sdir / "segments.json").write_text("{}", encoding="utf-8")   # a changed folder: read again
    assert warroom.heat_get(cfg, "kick-1")["items"] == []   # nothing cut yet


def test_a_stream_says_when_it_ended_and_when_its_items_may_go_out(tmp_path, defaults):
    """The Schedule's Week and Timeline: a finished stream's end, and kick_first_hours after it;
    a recording (no duration yet) or a hand-made meta.json has neither, and the page still loads."""
    from clippa import jsonstate

    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path}}
    done = tmp_path / "kick-1"
    done.mkdir()
    jsonstate.write(done / "meta.json", {"started_at": "2026-11-18T18:00:00-08:00", "duration_s": 7200})
    info = warroom.stream_info(cfg, done)
    assert info["ended_at"] == "2026-11-18T20:00:00-08:00"
    hours = defaults["schedule"]["kick_first_hours"]
    assert info["release_at"] == f"2026-11-{18 + (20 + hours) // 24:02d}T{(20 + hours) % 24:02d}:00:00-08:00"
    live = tmp_path / "kick-2"
    live.mkdir()
    jsonstate.write(live / "meta.json", {"started_at": "2026-11-19T18:00:00-08:00"})
    assert warroom.stream_info(cfg, live)["ended_at"] == warroom.stream_info(cfg, live)["release_at"] == ""
    odd = tmp_path / "kick-3"
    odd.mkdir()
    jsonstate.write(odd / "meta.json", {"started_at": "yesterday", "duration_s": 60})
    assert warroom.stream_info(cfg, odd)["ended_at"] == ""


def test_the_schedule_sends_upload_times_and_slots_to_the_horizon(tmp_path, defaults, monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from clippa import jsonstate

    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path, "schedule": tmp_path / "schedule.json"}}
    monkeypatch.setattr(warroom.review, "all_rows", lambda c: [])
    soon = (datetime.now(ZoneInfo(cfg["schedule"]["timezone"])) + timedelta(days=1)).isoformat(timespec="seconds")
    jsonstate.write(cfg["paths"]["schedule"], {"entries": [
        {"key": "kick-1/s01", "stream": "kick-1", "kind": "segment", "title": "t", "status": "uploaded",
         "publish_at": soon, "uploaded_at": "2026-11-18T10:00:00+00:00"}]})
    out = warroom.overview(cfg)
    assert out["schedule"][0]["uploaded_at"] == "2026-11-18T10:00:00+00:00"
    horizon = max(14, cfg["schedule"]["horizon_days"])
    assert out["rules"]["horizon_days"] == horizon
    last = max(datetime.fromisoformat(f["at"]) for f in out["free_slots"])
    assert last > datetime.fromisoformat(out["generated_at"]) + timedelta(days=horizon - 2)


def test_a_taken_slot_past_three_weeks_is_not_offered_again(tmp_path, defaults, monkeypatch):
    # PR #329 review: entries were cut at 21 days while the open slots went to horizon_days.
    from datetime import datetime, timedelta

    from clippa import jsonstate

    cfg = {**defaults, "schedule": {**defaults["schedule"], "horizon_days": 28},
           "paths": {**defaults["paths"], "streams": tmp_path, "schedule": tmp_path / "schedule.json"}}
    monkeypatch.setattr(warroom.review, "all_rows", lambda c: [])
    jsonstate.write(cfg["paths"]["schedule"], {"entries": []})
    out = warroom.overview(cfg)
    now = datetime.fromisoformat(out["generated_at"])
    late = next(f for f in out["free_slots"] if datetime.fromisoformat(f["at"]) > now + timedelta(days=24))
    jsonstate.write(cfg["paths"]["schedule"], {"entries": [
        {"key": "kick-1/s01", "stream": "kick-1", "kind": late["kind"], "title": "t", "status": "planned",
         "publish_at": late["at"]}]})
    out = warroom.overview(cfg)
    assert [e["key"] for e in out["schedule"]] == ["kick-1/s01"]
    assert late["at"][:16] not in {f["at"][:16] for f in out["free_slots"]}


def test_the_last_upload_run_reads_both_logger_names(defaults, tmp_path):
    """serve's logger is clippa.serve since #286 pass 2c; the log rotated before it says clipper.serve."""
    cfg = {**defaults, "paths": {**defaults["paths"], "serve_log": tmp_path / "serve.log"}}
    old = ["2026-11-21 09:00:00 INFO    clipper.serve: upload: starting",
           "2026-11-21 09:00:05 INFO    clipper.serve: upload: 1 due within 6 h",
           "2026-11-21 09:00:09 INFO    clipper.serve: upload: done (exit 0)"]
    new = ["2026-11-21 10:00:00 INFO    clippa.serve: upload: starting",
           "2026-11-21 10:00:05 INFO    clippa.serve: upload: 2 due within 6 h",
           "2026-11-21 10:00:06 INFO    clippa.serve: upload: uploading kick-1/v01",
           "2026-11-21 10:00:09 INFO    clippa.serve: upload: done (exit 1)"]
    (tmp_path / "serve.log.1").write_text("\n".join(old) + "\n", encoding="utf-8")
    (tmp_path / "serve.log").write_text("\n".join(new) + "\n", encoding="utf-8")
    from clippa.warroom import data

    run = data.last_upload_run(cfg)
    assert run["at"] == "2026-11-21 10:00:00" and run["exit"] == "1"
    assert run["lines"] == ["2 due within 6 h", "uploading kick-1/v01"]
    (tmp_path / "serve.log").write_text("", encoding="utf-8")
    assert data.last_upload_run(cfg)["at"] == "2026-11-21 09:00:00"
