"""How long a conversion will take: what a plugin holds, and the sub-step model scaled to a computer."""

import json
import re
import struct
from collections import Counter

import pytest

from core.gui import eta


@pytest.fixture(autouse=True)
def _no_learned_corrections(monkeypatch):
    """The model's own numbers, not this computer's learned corrections."""
    monkeypatch.setattr(eta, "corrections", dict)


def _tes3(records) -> bytes:
    """A Morrowind plugin: (type, body) records with 16-byte headers."""
    return b"".join(kind + struct.pack("<III", len(body), 0, 0) + body for kind, body in records)


def _sub(sig: bytes, data: bytes) -> bytes:
    """One Morrowind subrecord."""
    return sig + struct.pack("<I", len(data)) + data


def _tes4_group(label: bytes, body: bytes, header: int) -> bytes:
    """One top-level group of an Oblivion (20-byte) or New Vegas (24-byte) plugin."""
    return b"GRUP" + struct.pack("<I", header + len(body)) + label + b"\0" * (header - 12) + body


def test_a_morrowind_plugin_counts_its_records_and_voiced_lines(tmp_path):
    """Bytes by record type, and each INFO naming a voice file (SNAM) counted once."""
    path = tmp_path / "M.esm"
    path.write_bytes(_tes3([(b"TES3", b"HEDR" + b"\0" * 8), (b"INFO", b"NAME\0SNAM\0"), (b"INFO", b"NAME\0"),
                            (b"CELL", b"x" * 10)]))
    found = eta.records(path)
    assert eta.plugin_format(path) == "tes3"
    assert (found["INFO"], found["CELL"], found[eta.VOICED]) == (16 + 10 + 16 + 5, 26, 1)


def test_a_morrowind_plugin_counts_worn_models_lands_and_grass_placements(tmp_path):
    """Armor with body parts (INDX) is one worn mesh; an exterior cell's references are counted by base, and a
    static whose model sits under grass\\ is groundcover."""
    ref = _sub(b"FRMR", b"\1\0\0\0") + _sub(b"NAME", b"Grass_01\0") + _sub(b"DATA", b"\0" * 24)
    rock = _sub(b"FRMR", b"\2\0\0\0") + _sub(b"NAME", b"rock\0")
    exterior = _sub(b"NAME", b"\0") + _sub(b"DATA", struct.pack("<Iii", 0, 1, 2)) + ref + ref + rock
    path = tmp_path / "G.esp"
    path.write_bytes(_tes3([(b"TES3", b""), (b"ARMO", _sub(b"NAME", b"a\0") + _sub(b"INDX", b"\0")),
                            (b"ARMO", _sub(b"NAME", b"ring\0")),
                            (b"STAT", _sub(b"NAME", b"grass_01\0") + _sub(b"MODL", b"Grass\\tuft.nif\0")),
                            (b"LAND", b""), (b"LAND", b""), (b"CELL", exterior)]))
    bases, grass = Counter(), set()
    found = eta.records(path, bases, grass)
    assert (found[eta.WORN], found["count:LAND"], found[eta.REFS], found[eta.EXTERIOR_CELLS]) == (1, 2, 3, 1)
    assert grass == {b"grass_01"} and bases == {b"Grass_01": 2, b"rock": 1}


def _tes4_record(sig: bytes, body: bytes, header: int) -> bytes:
    """One record of an Oblivion (20-byte header) or New Vegas (24-byte) plugin."""
    return sig + struct.pack("<I", len(body)) + b"\0" * (header - 8) + body


def test_oblivion_and_new_vegas_plugins_count_their_top_groups_and_pathgrids(tmp_path):
    """HEDR's offset tells the header sizes apart; a group's size counts for its label; pathgrids under WRLD,
    and the cells there, are outdoor; hair is counted."""
    for header, fmt in ((20, "tes4"), (24, "fnv")):
        indoor = _tes4_record(b"CELL", b"y" * 10, header) + _tes4_record(b"PGRD", b"p", header)
        outdoor = _tes4_group(b"WRLD", _tes4_record(b"CELL", b"", header) + _tes4_record(b"PGRD", b"q", header),
                              header)
        path = tmp_path / f"{fmt}.esp"
        path.write_bytes(_tes4_record(b"TES4", b"HEDR", header) + _tes4_group(b"CELL", indoor, header) + outdoor
                         + _tes4_group(b"NPC_", _tes4_record(b"NPC_", b"z" * 7, header), header)
                         + _tes4_group(b"HAIR", _tes4_record(b"HAIR", b"", header) * 2, header))
        found = eta.records(path)
        assert eta.plugin_format(path) == fmt
        assert (found["CELL"], found["NPC_"], found["count:HAIR"]) == (header + len(indoor), 2 * header + 7, 2)
        assert (found[eta.PATHGRIDS], found[eta.OUTDOOR_PATHGRIDS], found[eta.EXTERIOR_CELLS]) == (2, 1, 1)


def test_assets_are_sorted_by_kind():
    """Creature meshes, animations, textures, voices and the rest each have their own rate."""
    kinds = [eta.asset_kind(p) for p in ("meshes\\creatures\\rat\\rat.nif", "meshes\\x.nif", "meshes\\r\\a.kf",
                                         "textures\\a.dds", "sound\\voice\\a\\b.mp3", "sound\\fx\\a.wav",
                                         "trees\\oak.spt", "music\\a.mp3", "menus\\a.xml")]
    assert kinds == ["creature_nif", "nif", "kf", "dds", "voice", "sound", "spt", "music", "other"]


#: A real log line for each sub-step that has a marker, as convert.py prints it.
LOG_LINES = {
    ("export", "Matching Morroblivion models"): "  Hybrid: matching Morroblivion models and ground...",
    ("export", "Listing Morroblivion assets"): "  Morroblivion ground: 127 land textures paired, 5008 lands repainted",
    ("export", "Writing records"): "  Morroblivion (hybrid): 1150 world and 202 worn models swapped",
    ("meshes", "Assembling Morrowind armor"): "  Morrowind armor: 268 worn meshes assembled",
    ("meshes", "Converting NIFs"): "Found 32675 NIF files in C:\\export\\Tamriel_Data.esm\\meshes",
    ("meshes", "Hair and grass"): "  Hair and grass...",
    ("meshes", "Copying textures"): "Copy Textures to Output",
    ("meshes", "Checking textures"): "  Textures: 9246 files -> C:\\output\\Tamriel Data (HD)\\textures\\morrowind",
    ("meshes", "Book icons"): "[Tamriel_Data.esm] Generating book inventory-art meshes...",
    ("creatures", "Converting creatures"): "  Converting 51 creatures (29 workers, namespace morrowind)...",
    ("creatures", "Gun animations"): "  Retargeting 497 _male gun clips for 157 guns (29 workers)...",
    ("import_", "Reading the export"): "Reading exports from: C:\\export\\Morrowind.esm",
    ("import_", "Converting records"): "Converting records...",
    ("import_", "Generating navmeshes"): "  Generating 1485 navmeshes (PGRD->NAVM, corridor generator) across 29 threads...",
    ("import_", "Writing the plugin"): "  Phase: navmesh generation (179.6s)",
    ("import_", "Morroblivion companion"): "[Morrowind - Morroblivion Hybrid.esp] Importing...",
    ("sounds", "Sounds"): "  [Non-voice sounds]",
    ("sounds", "Music"): "[Morrowind.esm] Converting music to xWMA...",
    ("scripts", "Compiling"): "[Morrowind.esm] Compiling 647 Papyrus scripts...",
}


def test_every_sub_step_starts_at_its_own_log_line_and_no_other():
    """Each marker finds the line convert.py prints at its sub-step's start, and no other sub-step's line."""
    for step, subs in eta.SUBSTEPS.items():
        assert subs[0][1] is None
        for name, marker in subs[1:]:
            assert re.search(marker, LOG_LINES[(step, name)]), (step, name)
            others = [line for (s, n), line in LOG_LINES.items() if (s, n) != (step, name)]
            assert not any(re.search(marker, line) for line in others), (step, name)


def _morrowind(vanilla: bool) -> dict:
    """Content like a Morrowind plugin's: records, lands, references, some textures and meshes."""
    return {"format": "tes3", "vanilla": vanilla, "master_mb": 80.0, "grass_refs": 0,
            "records": {"CELL": 20_000_000, "LAND": 30_000_000, "count:LAND": 1000, eta.REFS: 50_000},
            "assets": {"nif": [3000, 100_000_000], "dds": [2000, 50_000_000]}}


def test_hybrid_mode_adds_the_morroblivion_sub_steps_and_a_vanilla_master_repaints():
    """Only the hybrid mode matches Morroblivion's models; a vanilla master's lands make that longer."""
    ref = (1.0, eta.REF_PHYSICAL, eta.REF_LOGICAL)
    plain = dict(eta.substep_seconds("export", _morrowind(False), False, eta.REF_WORKERS, ref, hybrid=False))
    mod = dict(eta.substep_seconds("export", _morrowind(False), False, eta.REF_WORKERS, ref, hybrid=True))
    vanilla = dict(eta.substep_seconds("export", _morrowind(True), False, eta.REF_WORKERS, ref, hybrid=True))
    assert "Matching Morroblivion models" not in plain and "Matching Morroblivion models" in mod
    assert vanilla["Matching Morroblivion models"] > mod["Matching Morroblivion models"]
    assert plain["Exporting records"] == mod["Exporting records"]


def test_more_cores_shorten_only_the_multi_core_sub_steps():
    """On the profiling machine a sub-step is its fitted drivers summed; fewer workers stretch the NIF batch, not
    the single-core texture copy; a slower core stretches everything."""
    found = {"format": "tes4", "records": {}, "assets": {"nif": [10000, 0], "dds": [5000, 0]}, "master_mb": 0}
    ref = (1.0, eta.REF_PHYSICAL, eta.REF_LOGICAL)
    full = dict(eta.substep_seconds("meshes", found, False, eta.REF_WORKERS, ref))
    four = dict(eta.substep_seconds("meshes", found, False, 4, ref))
    slow = dict(eta.substep_seconds("meshes", found, False, eta.REF_WORKERS, (2.0, 16, 32)))
    coef, _hist = eta.MODEL[("tes4", "meshes", "Converting NIFs", None)]
    m = eta.measures(found)
    assert abs(full["Converting NIFs"] - sum(b * m[k] for k, b in coef.items())) < 1e-6
    assert full["Converting NIFs"] < four["Converting NIFs"]
    assert abs(four["Copying textures"] - full["Copying textures"]) < 0.05 * full["Copying textures"]
    assert all(abs(slow[k] - 2 * full[k]) < 1e-6 for k in full)
    assert eta.step_seconds("meshes", found, False, eta.REF_WORKERS, ref) == pytest.approx(sum(full.values()))


def test_a_sub_step_learns_this_computers_speed_halfway(tmp_path, monkeypatch):
    """Each finished sub-step moves its own correction halfway (in log terms) toward actual/predicted; a few
    seconds teach nothing."""
    saved = tmp_path / "step_speed.json"
    monkeypatch.setattr(eta, "CORRECTIONS_FILE", saved)
    monkeypatch.setattr(eta, "corrections", lambda: json.loads(saved.read_text()) if saved.is_file() else {})
    eta.learn("meshes", "Converting NIFs", 100.0, 400.0)
    eta.learn("meshes", "Planning", 1.0, 2.0)
    assert eta.corrections() == {"meshes/Converting NIFs": 2.0}


#: One plugin's Import lines as convert.py printed them (run 2026-11-24, Grass_Aes_TRv25_05_mowed.esp), in order.
IMPORT_RUN = (
    "[Grass_Aes_TRv25_05_mowed.esp] Importing...",
    "  Master: 1808138 converted records, 70871 manifest entries, 1740967 exported records (27.9s)",
    "Reading exports from: C:/export/Aesthesia groundcover/Grass_Aes_TRv25_05_mowed.esp",
    "  Phase: parse export text (29.0s)",
    "  Phase: pre-scans (53.0s)",
    "Converting records...",
    "  Generating 25 navmeshes (PGRD->NAVM, corridor generator) across 29 threads...",
    "  Phase: navmesh generation (68.6s)",
    "[Grass_Aes_TRv25_05_mowed - Morroblivion Hybrid.esp] Importing...",
)


def test_an_import_run_moves_through_every_sub_step_in_order():
    """Real Import lines carry the lane from Loading masters to the companion, one sub-step per marker."""
    from core.gui.run_view import Lane
    plugin = "Grass_Aes_TRv25_05_mowed.esp"
    lane = Lane("Import", "", 0, "import_", [plugin])
    lane.plan([(plugin, name, 1.0, marker) for name, marker in eta.SUBSTEPS["import_"]], 0.0)
    lane.begin(0.0)
    seen = []
    for line in IMPORT_RUN:
        lane.observe(line, 1.0)
        seen.append(lane.current().name)
    assert seen == ["Loading masters", "Loading masters", "Reading the export", "Reading the export",
                    "Reading the export", "Converting records", "Generating navmeshes", "Writing the plugin",
                    "Morroblivion companion"]
