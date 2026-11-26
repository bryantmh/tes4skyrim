"""The Choose screen's games: which plugins are base game, which start ticked, and clearing one.

See: docs/commentary/core_gui.md#games
"""

import struct
import json

import pytest

import version
from asset_convert.sources import bsa_write
from core.gui import games, runner
from source_paths import is_source_plugin


def _header(path, short: bool, form_version: int = 0, hedr: float = 1.0):
    """Write a TES4 header: Oblivion's 20-byte one, else a 24-byte one with `form_version` and HEDR `hedr`."""
    head = b"TES4" + bytes(16) + (b"" if short else struct.pack("<HH", form_version, 0))
    path.write_bytes(head + b"HEDR" + struct.pack("<Hf", 12, hedr) + bytes(8))
    return str(path)


@pytest.mark.parametrize("short, form_version, hedr, wanted", [
    (True, 0, 1.0, True),       # Oblivion / Nehrim
    (False, 15, 0.94, True),    # Fallout 3
    (False, 15, 1.34, True),    # New Vegas
    (False, 44, 1.71, False),   # Skyrim SE, or a converted copy
    (False, 43, 0.94, False),   # Skyrim LE
    (False, 131, 1.0, False),   # Fallout 4
    (False, 0, 0.95, False),    # Fallout 4 plugin named after a Fallout 3 master
])
def test_only_source_formats_count_as_installed_games(tmp_path, short, form_version, hedr, wanted):
    """Detection keeps the games the converter reads; same-named Skyrim or Fallout 4 files do not count."""
    assert is_source_plugin(_header(tmp_path / "x.esm", short, form_version, hedr)) is wanted


def test_morrowind_counts_as_installed_game(tmp_path):
    """A TES3 file is a source master."""
    (tmp_path / "Morrowind.esm").write_bytes(b"TES3" + bytes(40))
    assert is_source_plugin(str(tmp_path / "Morrowind.esm"))


def test_forget_plugin_drops_only_that_plugins_records(tmp_path, monkeypatch):
    """A cleared plugin reads as never converted; every other plugin's records stay as written."""
    state = tmp_path / "state.json"
    monkeypatch.setattr(version, "STATE_FILE", state)
    version.record_step_run("meshes", "Oblivion.esm", "0.55", data_path="C:/Oblivion/Data")
    version.record_step_run("meshes", "Knights.esp", "0.55", data_path="C:/Oblivion/Data")
    assert version.forget_plugin("KNIGHTS.ESP")
    assert version.steps_run_at("Knights.esp") == {}
    assert version.steps_run_at("Oblivion.esm") == {"meshes": "0.55"}
    assert list(json.loads(state.read_text())["sources"]) == ["oblivion.esm"]
    assert not version.forget_plugin("Knights.esp")


class _Session:
    """The one session call group_plugins makes."""

    def winding_on(self, _plugin):
        """No collision winding fix for any plugin."""
        return False


def test_masters_run_before_the_plugins_that_need_them():
    """Bloodmoon and Tribunal owe more steps than Morrowind: Morrowind still runs first, alone."""
    owed = {"Bloodmoon.esm": ["creatures", "sounds"], "Morrowind.esm": ["creatures"],
            "Tribunal.esm": ["creatures", "sounds"]}
    masters = {"Bloodmoon.esm": ["Morrowind.esm"], "Tribunal.esm": ["MORROWIND.ESM"], "Morrowind.esm": []}
    ordered = games.masters_first(owed, masters)
    assert list(ordered) == ["Morrowind.esm", "Bloodmoon.esm", "Tribunal.esm"]
    groups = runner.group_plugins(_Session(), ordered, masters)
    assert [g[0] for g in groups] == [["Morrowind.esm"], ["Bloodmoon.esm", "Tribunal.esm"]]


def test_a_plugin_never_joins_a_group_that_runs_before_its_master():
    """A shares steps with C, but C's master B runs after A's group: C waits for B."""
    owed = {"A.esm": ["import_"], "B.esm": ["export"], "C.esp": ["import_"]}
    groups = runner.group_plugins(_Session(), owed, {"C.esp": ["B.esm"]})
    assert [g[0] for g in groups] == [["A.esm"], ["B.esm"], ["C.esp"]]


def test_base_game_is_the_official_list_not_masterless():
    """A masterless mod is not base game; every official DLC is."""
    assert "bettermenucontrols.esp" not in games.BASE_PLUGINS
    assert {"knights.esp", "dlcvilelair.esp", "tribunal.esm", "tribalpack.esm", "zeta.esm",
            "translation.esp"} <= games.BASE_PLUGINS


def test_each_folder_has_its_own_root():
    """Oblivion, standalone Fallout 3, and a Tale of Two Wastelands New Vegas folder each start with one root."""
    assert games.roots(["Oblivion.esm", "Knights.esp", "BetterMenuControls.esp"]) == {"oblivion.esm"}
    assert games.roots(["Fallout3.esm", "Anchorage.esm"]) == {"fallout3.esm"}
    ttw = ["FalloutNV.esm", "DeadMoney.esm", "Fallout3.esm", "Anchorage.esm", "TaleOfTwoWastelands.esm"]
    assert games.roots(ttw) == {"falloutnv.esm"}
    assert games.roots(["Cobl Main.esm", "Unknown.esp"]) == set()


def test_clear_converted_deletes_files_and_records(tmp_path, monkeypatch):
    """The export folder, output folder and finished zip go, then the plugin's records."""
    export, out = tmp_path / "export", tmp_path / "output"
    monkeypatch.setattr(games, "EXPORT_DIR", export)
    forgotten = []
    monkeypatch.setattr(games.version_info, "forget_plugin", forgotten.append)
    for path in (export / "Old.esp" / "x.txt", out / "Old.esp" / "Old.esp", out / "Finished Mods" / "Old.esp.zip",
                 export / "Keep.esp" / "x.txt"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    log = []
    games.clear_converted("Old.esp", out, log.append)
    assert not (export / "Old.esp").exists() and not (out / "Old.esp").exists()
    assert not (out / "Finished Mods" / "Old.esp.zip").exists()
    assert (export / "Keep.esp" / "x.txt").exists()
    assert forgotten == ["Old.esp"]


def _rows(monkeypatch, owed: dict, data_folder: list, added: list, tes3: bool = False) -> dict:
    """{name: row} for a game whose Data folder holds `data_folder` and one added mod holding `added`."""
    monkeypatch.setattr(games, "_plan_status", lambda name: ("out", owed[name]) if owed.get(name) else ("ok", []))
    monkeypatch.setattr(games, "idle_steps", lambda _name: set())
    monkeypatch.setattr(games, "scan_plugins", lambda _path: list(data_folder))
    monkeypatch.setattr(games.mods, "plugin_paths", lambda mod: [f"C:/mod/{p}" for p in mod["plugins"]])
    monkeypatch.setattr(games.mods, "plugin_masters", lambda _path: ["Morrowind.esm"])
    mod = {"plugins": added, "label": "A mod", "asset_only": False, "entry": {"capabilities": {"plugin": True}}}
    game = {"path": "C:/Game/Data", "tes3": tes3, "mod": None}
    return {r["name"] + "|" + r["origin"]: r for r in games.plugin_rows(game, [mod])}


def test_a_plugin_owing_only_steps_it_never_runs_is_up_to_date(monkeypatch):
    """Morrowind-format plugins have no scripts stage: owing only that is not out of date."""
    rows = _rows(monkeypatch, {"Morrowind.esm": ["scripts"], "Tribunal.esm": ["sounds", "scripts"]},
                 ["Morrowind.esm", "Tribunal.esm"], [], tes3=True)
    assert rows["Morrowind.esm|Data folder"]["status"] == "ok"
    assert rows["Tribunal.esm|Data folder"]["status"] == "out"


def test_a_plugin_an_added_mod_provides_is_listed_once_as_that_mod(monkeypatch):
    """A copy of an added mod's plugin in the Data folder does not make a second row."""
    rows = _rows(monkeypatch, {}, ["Morrowind.esm", "TR_Mainland.esm"], ["TR_Mainland.esm"])
    assert sorted(rows) == ["Morrowind.esm|Data folder", "TR_Mainland.esm|A mod"]


def test_clear_also_removes_an_unowned_stem_folder(tmp_path, monkeypatch):
    """Output left under the plugin's bare name (no registry entry owns it) goes too."""
    export, out = tmp_path / "export", tmp_path / "output"
    export.mkdir()
    monkeypatch.setattr(games, "EXPORT_DIR", export)
    monkeypatch.setattr(games.version_info, "forget_plugin", lambda _p: None)
    (out / "thelostspires" / "scripts").mkdir(parents=True)
    (out / "thelostspires" / "scripts" / "a.pex").write_text("x")
    (out / "Other").mkdir()
    games.clear_converted("TheLostSpires.esp", out, lambda _l: None)
    assert not (out / "thelostspires").exists() and (out / "Other").exists()


def test_remove_mod_deletes_its_output_but_not_a_folder_siblings_use(tmp_path, monkeypatch):
    """The mod's output folder goes with the mod, unless another plugin of the mod stays."""
    export, out = tmp_path / "export", tmp_path / "output"
    export.mkdir()
    monkeypatch.setattr(games, "EXPORT_DIR", export)
    monkeypatch.setattr(games.mods, "remove", lambda *_a: None)
    monkeypatch.setattr(games.version_info, "forget_plugin", lambda _p: None)
    members = {"A.esp": ["A.esp"], "B.esp": ["B.esp", "C.esp"]}
    monkeypatch.setattr(games.source_registry, "group_members", lambda _e, name: members[name])
    for name in ("A.esp", "B.esp"):
        (out / name).mkdir(parents=True)
    games.remove_mod(["A.esp", "B.esp"], out, lambda _l: None)
    assert not (out / "A.esp").exists() and (out / "B.esp").exists()


def _tes4_plugin(path, groups) -> None:
    """An Oblivion plugin (20-byte headers): a TES4 record, then one top group per (label, body size)."""
    head = b"TES4" + struct.pack("<I", 0) + b"\0" * 12
    path.write_bytes(head + b"".join(b"GRUP" + struct.pack("<I", 20 + n) + label + b"\0" * 8 + b"x" * n
                                     for label, n in groups))


def test_idle_steps_read_only_group_headers_and_archive_folders(tmp_path, monkeypatch):
    """No trees, creatures or sounds: all three are idle; an empty group counts as absent; a creature folder in the
    plugin's archive or a DIAL group keeps its step."""
    bare, rich = tmp_path / "Bare.esp", tmp_path / "Rich.esp"
    _tes4_plugin(bare, [(b"WRLD", 4), (b"CREA", 0)])
    _tes4_plugin(rich, [(b"DIAL", 4)])
    bsa = tmp_path / "Rich.bsa"
    src = tmp_path / "a.nif"
    src.write_bytes(b"nif")
    work = tmp_path / "work"
    work.mkdir()
    bsa_write.write_bsa(bsa, bsa_write.stage([("meshes\\creatures\\rat\\a.nif", str(src), 3)], {}, str(work)))
    monkeypatch.setattr(games.eta, "plugin_path", lambda p: {"Bare.esp": bare, "Rich.esp": rich}.get(p))
    monkeypatch.setattr(games.source_registry, "directory_for", lambda _e, _p: str(tmp_path))
    monkeypatch.setattr(games.source_registry, "get", lambda _e, _p: None)
    monkeypatch.setattr(games, "get_bsa_files", lambda _d, p: [bsa] if p == "Rich.esp" else [])
    assert games.idle_steps("Bare.esp") == {"speedtrees", "creatures", "sounds"}
    assert games.idle_steps("Rich.esp") == {"speedtrees"}
    assert games.idle_steps("Missing.esp") == set()


