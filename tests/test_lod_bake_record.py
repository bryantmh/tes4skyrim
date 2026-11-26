"""The LOD bake record: what makes a worldspace's distant LOD read as out of date."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from asset_convert.lod.bake_record import BAKE_RECORD_NAME, bake_of, is_stale, read_bakes, record_bake
from core.gui.install_mods import SIDECAR_NAMES


def test_a_bake_is_recorded_forgotten_and_never_installed(tmp_path):
    """A clean bake is kept; forgetting it (a bake starting, or failing) removes it; the file never ships."""
    record_bake(tmp_path, "Tamriel", 100.0, ["Oblivion.esm", "Tamriel.esp"], ["Oblivion.esm"])
    record_bake(tmp_path, "Vvardenfell", 200.0, ["Morrowind_ob.esm"], ["Morrowind_ob.esm"])
    record_bake(tmp_path, "Vvardenfell", 300.0)
    assert read_bakes(tmp_path) == {"Tamriel": {"time": 100.0, "plugins": ["Oblivion.esm", "Tamriel.esp"],
                                                "used": ["Oblivion.esm"]}}
    assert BAKE_RECORD_NAME in SIDECAR_NAMES and read_bakes(tmp_path / "missing") == {}


def test_a_lod_mod_baked_before_records_is_dated_by_its_terrain_folder(tmp_path):
    """No record: a worldspace with a tile folder counts as baked then, in the current order; none is unbaked."""
    (tmp_path / "meshes" / "terrain" / "Tamriel").mkdir(parents=True)
    bake = bake_of(tmp_path, {}, "Tamriel", ["A.esm"])
    assert bake["plugins"] == ["A.esm"] and bake["time"] > 0
    assert bake_of(tmp_path, {}, "Elsewhere", ["A.esm"]) is None
    assert bake_of(tmp_path, {"Tamriel": {"time": 1}}, "Tamriel", []) == {"time": 1}


def test_stale_when_never_baked_a_plugin_dropped_rewritten_or_reordered():
    """Out of date: no bake, a plugin it read unticked, an owner added, reordered or newer than the bake."""
    bake = {"time": 100.0, "plugins": ["A.esm", "Other.esp", "B.esp"], "used": ["A.esm", "B.esp"]}
    times = {"A.esm": 50.0, "B.esp": 90.0}
    owners = ["A.esm", "B.esp"]
    assert is_stale(None, owners, ["A.esm", "B.esp"], times.get)
    assert not is_stale(bake, owners, ["A.esm", "B.esp", "Unrelated.esp"], times.get)
    assert is_stale(bake, ["A.esm"], ["A.esm"], times.get)
    assert is_stale(bake, ["B.esp", "A.esm"], ["B.esp", "A.esm"], times.get)
    assert is_stale(bake, owners + ["C.esp"], ["A.esm", "B.esp", "C.esp"], times.get)
    assert is_stale(bake, owners, ["A.esm", "B.esp"], {**times, "B.esp": 150.0}.get)
