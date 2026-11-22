import pytest
from pytest import approx

from clippa import cut, jsonstate
from clippa.cut import compilation_parts
from clippa.errors import ClipperError


def seg(id, start, end, cut_start=None):
    s = {"id": id, "title": id.upper(), "start_s": start, "end_s": end}
    if cut_start is not None:
        s["cut_start_s"] = cut_start
    return s


def test_back_to_back_part_ends_where_the_next_file_begins():
    # b's keyframe lead-in starts at 195, inside a: a stops there instead of at 200.
    segs = [seg("a", 100, 200, 98), seg("b", 200, 300, 195), seg("c", 500, 600, 497)]
    spans, chapters, total = compilation_parts(segs)
    assert spans == [(98, 195), (195, 300), (497, 600)]
    assert [c["start_s"] for c in chapters] == [0, 97, 202]
    assert [c["title"] for c in chapters] == ["A", "B", "C"]
    assert total == approx(305)


def test_parts_out_of_stream_order_keep_their_own_ends():
    segs = [seg("late", 500, 600, 497), seg("early", 100, 200)]
    spans, chapters, total = compilation_parts(segs)
    assert spans == [(497, 600), (100, 200)]
    assert [c["start_s"] for c in chapters] == [0, 103]
    assert total == approx(203)


def test_part_offsets_are_exact():
    from clippa.cut import part_offsets
    spans = [(98.0, 195.04), (195.04, 300.0), (497.0, 600.0)]
    assert part_offsets(spans) == [[98.0, 195.04, 0.0], [195.04, 300.0, 97.04], [497.0, 600.0, 202.0]]


def test_an_unchanged_pick_is_not_cut_again(tmp_path):
    dst = tmp_path / "03_t03_x.mp4"
    p, c = {"id": "t03"}, {"in_s": 10.0, "out_s": 40.0}
    assert not cut.unchanged_pick(dst, p, c)  # nothing cut yet
    dst.write_bytes(b"x")
    jsonstate.write(dst.with_suffix(".json"), {"id": "t03", "in_s": 10.0, "out_s": 40.0})
    assert cut.unchanged_pick(dst, p, c)
    assert not cut.unchanged_pick(dst, p, {"in_s": 12.0, "out_s": 40.0})  # a re-score moved it


def test_a_nas_cut_runs_the_package_it_sends(tmp_path, monkeypatch):
    """#286: `python -m clippa.cli` from /code, not the image's entry point, which in an image built
    before the rename imports its own older copy; the tarball holds clippa/ and the config."""
    import io
    import tarfile
    from types import SimpleNamespace

    from clippa import config

    streams = tmp_path / "streams"
    (streams / "kick-1").mkdir(parents=True)
    toml = tmp_path / "clipper.toml"
    toml.write_text(f'[paths]\nstreams = "{streams.as_posix()}"\n'
                    '[cut]\nbackend = "nas"\nnas_host = "nas"\nnas_data = "/volume1/x"\n', encoding="utf-8")
    cfg = config.load(toml, use_local=False)
    seen = {}

    def run(cmd, input=None, check=False):
        seen["cmd"], seen["tar"] = cmd, input
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cut.subprocess, "run", run)
    cut.on_nas(streams / "kick-1", cfg, ["--only", "c01"])
    remote = seen["cmd"][-1]
    assert f"--entrypoint python {cfg['cut']['nas_image']} -m clippa.cli cut kick-1 --only c01" in remote
    # #286 pass 3: the new spelling, which wins over the image's own CLIPPA_CONFIG, and a cut
    # still running under the old container name holds the stream
    assert "-e CLIPPA_CONFIG=/code/clipper.toml" in remote and "CLIPPER_CONFIG" not in remote
    assert "--filter 'name=^clipper-cut-kick-1$'" in remote and remote.index("exit 75") < remote.index("docker run")
    monkeypatch.setattr(cut.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=cut.OLD_CUT_RUNNING))
    with pytest.raises(ClipperError, match="docker rm -f clipper-cut-kick-1"):
        cut.on_nas(streams / "kick-1", cfg, [])
    names = tarfile.open(fileobj=io.BytesIO(seen["tar"]), mode="r:gz").getnames()
    assert "clippa/cut.py" in names and "clipper.toml" in names and not any(n.startswith("clipper/") for n in names)
