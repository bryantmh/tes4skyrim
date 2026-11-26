"""Uninstalling a mod Vortex has deployed: its files leave a fake Data folder and both manifests, exactly.

Every folder is a temporary one; Vortex is never started (its command line is replaced).
See: docs/commentary/core_gui.md#vortex-deployed-uninstall
"""

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.gui import install, vortex_deploy
from core.gui import install_mods as mods
from core.gui.vortex_deploy import BINARY_BACKUP, MANIFEST

GAME = install.VORTEX_GAME


def _write(path, data=b"x"):
    """Write one file, making its folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _entry(data, rel, source):
    """A manifest entry for a file already in Data, timed as Vortex times it (ms)."""
    return {"relPath": rel.replace("/", "\\"), "source": source, "target": "",
            "time": (data / rel).stat().st_mtime_ns // 1_000_000}


def _link(src, dst):
    """Hardlink `src` at `dst`, as Vortex's hardlink deployment does."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.link(src, dst)


def _deployed(tmp_path, method="hardlink_activator"):
    """Output, staging and Data with our mod "Ours" installed and deployed beside another mod.

    Ours has: a plain file, one another mod also has (ours won), one the user replaced in Data, and one
    over a game file Vortex moved aside. The other mod has its own deployed file.
    """
    out, staging, data = tmp_path / "output", tmp_path / "staging", tmp_path / "Game" / "Data"
    for rel in ("Ours.esp", "meshes/a.nif", "textures/t.dds", "scripts/s.pex", "interface/x.swf"):
        _write(out / "Ours" / rel, rel.encode())
    mod = mods.Mod("Ours", out / "Ours", out / "Finished Mods" / "Ours.zip")
    install.install_all(staging, [(mod, mods.files(mod))], out, lambda _line: None)
    _write(staging / "Other" / "textures" / "T.dds", b"other")
    _write(staging / "Other" / "other.nif", b"other")
    _write(data / "interface" / ("x.swf" + vortex_deploy.BACKUP_TAG), b"game file")
    for rel in ("Ours.esp", "meshes/a.nif", "textures/t.dds", "scripts/s.pex", "interface/x.swf"):
        _link(staging / "Ours" / rel, data / rel)
    (data / "scripts" / "s.pex").unlink()
    _write(data / "scripts" / "s.pex", b"user's own fix")
    _link(staging / "Other" / "other.nif", data / "other.nif")
    files = [_entry(data, r, "Ours") for r in ("Ours.esp", "meshes/a.nif", "textures/t.dds", "scripts/s.pex",
                                               "interface/x.swf")]
    files.append(_entry(data, "other.nif", "Other"))
    manifest = {"version": 1, "instance": "abc", "deploymentMethod": method, "gameId": GAME,
                "deploymentTime": 1, "stagingPath": str(staging), "targetPath": str(data), "files": files}
    (data / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _write(staging / BINARY_BACKUP, vortex_deploy.msgpack(manifest))
    return out, staging, data


def test_undeploy_removes_exactly_our_files_and_entries(tmp_path):
    """Our linked files go, the user's replacement and the other mod's file stay, the game file comes back."""
    out, staging, data = _deployed(tmp_path)
    done = vortex_deploy.undeploy(data, staging, "Ours", GAME)
    assert done.removed == 4 and done.kept == ["scripts\\s.pex"] and done.redeploy == ["textures/t.dds"]
    for rel in ("Ours.esp", "meshes/a.nif", "textures/t.dds"):
        assert not (data / rel).exists() and (out / "Ours" / rel).is_file()
    assert (data / "interface" / "x.swf").read_bytes() == b"game file"
    assert (data / "scripts" / "s.pex").read_bytes() == b"user's own fix"
    assert (data / "other.nif").read_bytes() == b"other"
    manifest = json.loads((data / MANIFEST).read_text(encoding="utf-8"))
    assert [f["source"] for f in manifest["files"]] == ["Other"] and manifest["instance"] == "abc"
    assert (staging / BINARY_BACKUP).read_bytes() == vortex_deploy.msgpack(manifest)


def test_a_mod_that_is_not_deployed_changes_nothing(tmp_path):
    """Neither Data nor the manifests are touched for a mod the manifest does not list."""
    _out, staging, data = _deployed(tmp_path)
    before = (data / MANIFEST).read_bytes(), (staging / BINARY_BACKUP).read_bytes()
    done = vortex_deploy.undeploy(data, staging, "NotDeployed", GAME)
    assert done.removed == 0 and not done.redeploy
    assert ((data / MANIFEST).read_bytes(), (staging / BINARY_BACKUP).read_bytes()) == before


def test_a_manifest_of_another_staging_folder_is_left_alone(tmp_path):
    """Data deployed from elsewhere (another Vortex setup) is not ours to change."""
    _out, staging, data = _deployed(tmp_path)
    before = (data / MANIFEST).read_bytes()
    assert vortex_deploy.undeploy(data, tmp_path / "elsewhere", "Ours", GAME).removed == 0
    assert (data / MANIFEST).read_bytes() == before and (data / "Ours.esp").is_file()


def test_an_unknown_method_refuses_before_changing_anything(tmp_path):
    """A deployment whose files cannot be told apart is left for Vortex's own purge."""
    _out, staging, data = _deployed(tmp_path, method="some_new_activator")
    before = (data / MANIFEST).read_bytes()
    with pytest.raises(RuntimeError, match="purge it in Vortex"):
        vortex_deploy.undeploy(data, staging, "Ours", GAME)
    assert (data / MANIFEST).read_bytes() == before and (data / "Ours.esp").is_file()


def test_the_last_deployed_mod_removes_both_manifests(tmp_path):
    """As Vortex's saveActivation does when nothing is deployed: no manifest, no binary copy."""
    _out, staging, data = _deployed(tmp_path)
    vortex_deploy.undeploy(data, staging, "Ours", GAME)
    vortex_deploy.undeploy(data, staging, "Other", GAME)
    assert not (data / MANIFEST).exists() and not (staging / BINARY_BACKUP).exists()
    assert not (data / "other.nif").exists()


def test_the_move_method_takes_the_file_and_its_link_note(tmp_path):
    """Vortex's move method: the file sits in Data, a .vortex_lnk beside the staging path names it."""
    staging, data = tmp_path / "staging", tmp_path / "Data"
    moved = _write(data / "meshes" / "a.nif")
    lnk = _write(staging / "Ours" / ("meshes/a.nif" + vortex_deploy.LNK_EXT),
                 json.dumps({"target": str(moved)}).encode())
    manifest = {"deploymentMethod": "move_activator", "gameId": GAME, "stagingPath": str(staging),
                "files": [_entry(data, "meshes/a.nif", "Ours")]}
    (data / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    assert vortex_deploy.undeploy(data, staging, "Ours", GAME).removed == 1
    assert not moved.exists() and not lnk.exists()


def test_the_move_method_keeps_a_file_changed_since(tmp_path):
    """A moved file whose time differs from the entry's was changed by the user: it stays."""
    staging, data = tmp_path / "staging", tmp_path / "Data"
    moved = _write(data / "a.nif")
    _write(staging / "Ours" / ("a.nif" + vortex_deploy.LNK_EXT), json.dumps({"target": str(moved)}).encode())
    entry = _entry(data, "a.nif", "Ours")
    later = time.time() + 60
    os.utime(moved, (later, later))
    (data / MANIFEST).write_text(json.dumps({"deploymentMethod": "move_activator", "files": [entry]}),
                                 encoding="utf-8")
    assert vortex_deploy.undeploy(data, staging, "Ours", GAME).kept == ["a.nif"] and moved.is_file()


def test_vortex_uninstall_unregisters_and_asks_for_a_deploy_only_on_a_conflict(tmp_path, monkeypatch):
    """The whole per-mod uninstall: Data, staging, Vortex's entry, and the deploy flag for the shadowed file."""
    _out, staging, data = _deployed(tmp_path)
    calls = []
    monkeypatch.setattr(install, "running", lambda _image: False)
    monkeypatch.setattr(install, "vortex_cli", lambda _exe, args, _log: calls.append(args))
    notice = install.vortex_uninstall("V.exe", str(staging), str(data), "Ours", lambda _line: None)
    assert not (staging / "Ours").exists() and (staging / "Other" / "other.nif").is_file()
    assert calls == [["--del", "persistent.mods.skyrimse.Ours"],
                     ["-s", "persistent.deployment.needToDeploy.skyrimse=true"]]
    assert "deploy to put them back" in notice and "textures/t.dds" in notice and "scripts\\s.pex" in notice
    calls.clear()
    assert install.vortex_uninstall("V.exe", str(staging), str(data), "Other", lambda _line: None) == ""
    assert calls == [["--del", "persistent.mods.skyrimse.Other"]]


def test_vortex_uninstall_refuses_while_vortex_runs(tmp_path, monkeypatch):
    """Vortex holds its state and manifest open: nothing is touched while it runs."""
    _out, staging, data = _deployed(tmp_path)
    monkeypatch.setattr(install, "running", lambda _image: True)
    with pytest.raises(RuntimeError, match="Close Vortex"):
        install.vortex_uninstall("V.exe", str(staging), str(data), "Ours", lambda _line: None)
    assert (data / "Ours.esp").is_file() and (staging / "Ours" / "Ours.esp").is_file()


def test_msgpack_matches_the_format_spec():
    """Byte forms from the msgpack spec, as @msgpack/msgpack reads them."""
    assert vortex_deploy.msgpack({"a": 1}) == b"\x81\xa1a\x01"
    assert vortex_deploy.msgpack([None, True, False, -1, -33, 300]) == b"\x96\xc0\xc3\xc2\xff\xd0\xdf\xcd\x01\x2c"
    assert vortex_deploy.msgpack(1_700_000_000_000) == b"\xcf" + (1_700_000_000_000).to_bytes(8, "big")
    assert vortex_deploy.msgpack(1.5) == b"\xcb\x3f\xf8" + b"\x00" * 6
    assert vortex_deploy.msgpack("x" * 40) == b"\xd9\x28" + b"x" * 40
    assert vortex_deploy.msgpack(list(range(16)))[:3] == b"\xdc\x00\x10"
