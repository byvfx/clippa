"""`clippa backup` (#204): one zip of the taste layer; secrets only when asked, never the
recordings, and old zips dropped past [backup] keep."""
import os
import time
import zipfile

import pytest

from clippa import backup, config, jsonstate, serve
from clippa.errors import ClipperError


@pytest.fixture
def cfg(defaults, tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    defaults["_dir"] = root
    (root / "clipper.toml").write_text("[meta]\n", encoding="utf-8")
    defaults["_file"] = root / "clipper.toml"
    defaults["_local_file"] = None
    for key, name in (("streams", "streams"), ("schedule", "schedule.json"), ("people", "people.json"),
                      ("costs", "logs/costs.jsonl"), ("faces", "faces"), ("voices", "voices"),
                      ("layouts", "layouts")):
        defaults["paths"][key] = root / name
    defaults["thumbnail"]["photos"] = str(root / "photos")
    jsonstate.write(root / "schedule.json", {"entries": []})
    (root / "faces" / "models").mkdir(parents=True)
    (root / "faces" / "models" / "big.onnx").write_bytes(b"x" * 100)
    jsonstate.write(root / "faces" / "known.json", {})
    (root / "photos" / "Andy").mkdir(parents=True)
    jsonstate.write(root / "photos" / "Andy" / "cast.json", {})
    (root / "secrets").mkdir()
    (root / "secrets" / "token.json").write_text("{}", encoding="utf-8")
    (root / "backups").mkdir()
    (root / "backups" / "clipper.toml.before_x").write_text("", encoding="utf-8")
    s = root / "streams" / "kick-1"
    s.mkdir(parents=True)
    for name in ("config.toml", "review.json", "transcript.json"):
        (s / name).write_text("{}", encoding="utf-8")
    (s / "source.mkv").write_bytes(b"x" * 100)
    return defaults


def names(path) -> set[str]:
    with zipfile.ZipFile(path) as z:
        return set(z.namelist())


def test_the_zip_holds_the_taste_layer_and_nothing_heavy(cfg):
    out = backup.run(cfg)
    zips = list((cfg["_dir"] / "backups" / "taste").glob("taste-*.zip"))
    assert len(zips) == 1 and "no secrets" in out[0]
    got = names(zips[0])
    assert {"clipper.toml", "schedule.json", "faces/known.json", "photos/Andy/cast.json",
            "backups/clipper.toml.before_x", "streams/kick-1/config.toml", "streams/kick-1/review.json"} <= got
    assert not any(n.startswith(("secrets/", "faces/models/", "backups/taste/")) for n in got)
    assert "streams/kick-1/source.mkv" not in got and "streams/kick-1/transcript.json" not in got


def test_secrets_only_when_asked(cfg):
    backup.run(cfg, with_secrets=True)
    (z,) = (cfg["_dir"] / "backups" / "taste").glob("taste-*.zip")
    assert "secrets/token.json" in names(z)


def test_a_dry_run_writes_nothing(cfg):
    out = backup.run(cfg, dry_run=True)
    assert out[0].startswith("would write") and not (cfg["_dir"] / "backups" / "taste").exists()


def test_old_zips_go_past_keep(cfg):
    dest = cfg["_dir"] / "backups" / "taste"
    dest.mkdir(parents=True)
    for day in ("01", "02", "03"):
        (dest / f"taste-2026-11-{day}-0900.zip").write_bytes(b"")
    (dest / "taste-2026-11-03-1000.zip.part").write_bytes(b"")   # a run killed mid-write
    cfg["backup"]["keep"] = 2
    out = backup.run(cfg)
    assert len(list(dest.glob("taste-*.zip"))) == 2 and "removed 2 older backups" in out[-1]
    assert not list(dest.glob("*.part"))


def test_no_folder_is_an_error_and_newest_is_none(cfg):
    cfg["backup"]["folder"] = ""
    with pytest.raises(ClipperError, match="no backup folder"):
        backup.run(cfg)
    assert backup.newest(cfg) is None


def test_serve_makes_one_only_when_the_newest_is_old(cfg):
    assert serve.backup_due(cfg)
    backup.run(cfg)
    assert not serve.backup_due(cfg)
    p = backup.newest(cfg)
    old = time.time() - 8 * 86400
    os.utime(p, (old, old))
    assert serve.backup_due(cfg)


def test_a_backup_elsewhere_never_takes_the_zips_in_backups(cfg, tmp_path):
    """Copilot on #248: --to another folder still leaves out the taste zips (one may hold secrets)."""
    backup.run(cfg, with_secrets=True)
    backup.run(cfg, to=tmp_path / "offsite")
    (z,) = (tmp_path / "offsite").glob("taste-*.zip")
    assert not any(n.endswith(".zip") for n in names(z))


def test_a_show_pinned_config_backs_up_the_install(cfg):
    """`clippa --show kino backup`: a show's config has no _dir; the install's is used."""
    cfg["shows"] = {"kino": {"channels": ["k"], "overrides": {"thumbnail": {"photos": str(cfg["_dir"] / "kino_photos")}}}}
    cfg["_root"] = cfg
    (cfg["_dir"] / "kino_photos" / "PPP").mkdir(parents=True)
    jsonstate.write(cfg["_dir"] / "kino_photos" / "PPP" / "cast.json", {})
    pinned = {k: v for k, v in config.pin_show(cfg, "kino").items() if k not in ("_dir", "_file", "_local_file")}
    backup.run(pinned)
    (z,) = (cfg["_dir"] / "backups" / "taste").glob("taste-*.zip")
    got = names(z)
    assert "photos/Andy/cast.json" in got and "kino_photos/PPP/cast.json" in got   # every show's photos


def test_one_backup_at_a_time(cfg):
    dest = cfg["_dir"] / "backups" / "taste"
    dest.mkdir(parents=True)
    (dest / "backup.lock").write_text("someone else\n", encoding="utf-8")   # fresh: not stale
    with pytest.raises(ClipperError, match="another backup is writing"):
        backup.run(cfg)


def test_the_blinds_store_goes_in_as_a_checked_snapshot(cfg, tmp_path):
    import sqlite3

    from clippa import blinds

    cfg["paths"]["news"] = cfg["_dir"] / "news"
    db = blinds.connect(cfg)
    db.execute("INSERT INTO blinds (id, status, title) VALUES ('cdan-x', 'answered', 'Blind Item #9')")
    db.commit()
    db.close()
    assert any(line.strip().startswith("blinds") for line in backup.run(cfg, dry_run=True))
    backup.run(cfg)
    (z,) = (cfg["_dir"] / "backups" / "taste").glob("taste-*.zip")
    assert "blinds/blinds.sqlite" in names(z)
    copy = tmp_path / "restored.sqlite"
    with zipfile.ZipFile(z) as zf:
        copy.write_bytes(zf.read("blinds/blinds.sqlite"))
    with sqlite3.connect(copy) as c:
        assert c.execute("SELECT status FROM blinds WHERE id = 'cdan-x'").fetchone() == ("answered",)


def test_a_damaged_blinds_store_goes_in_under_its_own_name_and_the_rest_still_backs_up(cfg):
    from clippa import blinds

    cfg["paths"]["news"] = cfg["_dir"] / "news"
    store = blinds.store_path(cfg)
    store.parent.mkdir(parents=True)
    store.write_bytes(b"not a database at all")
    out = backup.run(cfg)
    (z,) = (cfg["_dir"] / "backups" / "taste").glob("taste-*.zip")
    got = names(z)
    assert "blinds/blinds.damaged.sqlite" in got and "blinds/blinds.sqlite" not in got
    assert "clipper.toml" in got and out[-1].startswith("WARNING: the blinds store")


def test_a_busy_blinds_store_is_left_out_and_the_rest_backs_up(cfg, monkeypatch):
    from clippa import blinds

    def busy(cfg, dest):
        raise blinds.BlindsBusy("database is locked")
    monkeypatch.setattr(blinds, "snapshot", busy)
    out = backup.run(cfg)
    (z,) = (cfg["_dir"] / "backups" / "taste").glob("taste-*.zip")
    assert not any(n.startswith("blinds/") for n in names(z)) and "clipper.toml" in names(z)
    assert "the blinds store was busy" in out[-1]


def test_a_damaged_blinds_store_keeps_every_older_zip(cfg):
    from clippa import blinds

    cfg["backup"]["keep"] = 1
    backup.run(cfg)  # a good one first
    cfg["paths"]["news"] = cfg["_dir"] / "news"
    store = blinds.store_path(cfg)
    store.parent.mkdir(parents=True)
    store.write_bytes(b"not a database at all")
    dest = cfg["_dir"] / "backups" / "taste"
    (old,) = dest.glob("taste-*.zip")
    old.rename(dest / "taste-2000-01-01-0000.zip")  # older by name, as a real earlier run's
    out = backup.run(cfg)
    assert len(list(dest.glob("taste-*.zip"))) == 2 and any("kept every older backup" in x for x in out)


def test_a_busy_blinds_store_keeps_every_older_zip(cfg, monkeypatch):
    """Grok on #384: the zip that still holds the store must survive a run that left it out."""
    from clippa import blinds

    cfg["backup"]["keep"] = 1
    dest = cfg["_dir"] / "backups" / "taste"
    dest.mkdir(parents=True)
    (dest / "taste-2000-01-01-0000.zip").write_bytes(b"")   # an earlier run's, with the store

    def busy(cfg, dest):
        raise blinds.BlindsBusy("database is locked")
    monkeypatch.setattr(blinds, "snapshot", busy)
    out = backup.run(cfg)
    assert len(list(dest.glob("taste-*.zip"))) == 2
    assert any("kept every older backup: the blinds store was left out" in x for x in out)
