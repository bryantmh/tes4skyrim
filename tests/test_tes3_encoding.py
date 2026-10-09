"""TES3 codecs: binary detection, localized identities and export round trips."""

import struct

import pytest

from core.plugin_masters import export_encoding
from core.tes4_encoding import ENCODING_ENV_VAR, choice, current
from tes4_export import tes3_reader as reader
from tes4_export.export_morrowind import MorrowindContext, export_plugin, run_export
from tes4_export.morroblivion_pairs import _vanilla_holders
from tes4_export.morrowind_travel import destinations
from tes4_export.record_types.morrowind import emit_inventory
from tes4_export.record_types.morrowind_packages import _packages
from tes4_export.record_types.morrowind_scripts import export_SCPT


@pytest.fixture(autouse=True)
def _codec(monkeypatch):
    """Restore the environment after every detector or export call."""
    monkeypatch.setenv(ENCODING_ENV_VAR, "auto")


def _sub(sig, data):
    """A binary TES3 subrecord."""
    return sig.encode("ascii") + struct.pack("<I", len(data)) + data


def _record(sig, *subs):
    """A binary TES3 record."""
    body = b"".join(subs)
    return sig.encode("ascii") + struct.pack("<III", len(body), 0, 0) + body


def _plugin(tmp_path, codec="cp1251", name="Аргонианин", masters=()):
    """A localized object, eight display names, a script and its placement."""
    def text(value):
        return value.encode(codec) + b"\x00"

    header = _record("TES3", *(_sub("MAST", text(m)) for m in masters))
    objects = [_record("ACTI", _sub("NAME", text(name)),
                       _sub("FNAM", text(name)), _sub("SCRI", text("Скрипт" if codec == "cp1251" else "script")))]
    objects += [_record("ACTI", _sub("NAME", text(f"object{i}")),
                        _sub("FNAM", text(name))) for i in range(7)]
    script_name = "Скрипт" if codec == "cp1251" else "script"
    script = _record("SCPT", _sub("SCHD", text(script_name).ljust(52, b"\x00")),
                     _sub("SCTX", text(f"Begin {script_name}\nEnd")),
                     _sub("SCVR", text(name)))
    cell = _record("CELL", _sub("NAME", text("room")),
                   _sub("DATA", struct.pack("<iii", 1, 0, 0)),
                   _sub("FRMR", struct.pack("<I", 1)),
                   _sub("NAME", text(name)),
                   _sub("DATA", struct.pack("<6f", 10, 20, 30, 0, 0, 0)))
    path = tmp_path / "sample.esp"
    path.write_bytes(header + b"".join(objects) + script + cell)
    return path


@pytest.mark.parametrize("codec,name,detected", [
    ("cp1251", "Аргонианин", "cp1251"),
    ("cp1252", "Argonian", "cp1252"),
])
def test_detect_binary(tmp_path, codec, name, detected):
    """Auto distinguishes a Cyrillic plugin from an ASCII Western one."""
    source = _plugin(tmp_path, codec, name)
    _, records = reader.read_file(str(source))
    assert reader.detect_codec(str(source), records) == detected


def test_detection_repairs_record_and_script_ids(tmp_path):
    """IDs parsed with the fallback codec are refreshed before registration."""
    source = _plugin(tmp_path)
    _, records = reader.read_file(str(source))
    assert records[0].record_id != "Аргонианин"
    assert reader.configure_for_source(str(source), records) == "cp1251"
    assert records[0].record_id == "Аргонианин"
    assert next(r for r in records if r.type == "SCPT").record_id == "Скрипт"


@pytest.mark.parametrize("codec,name", [
    ("cp1251", "Аргонианин"), ("cp1252", "Grüße"), ("cp1250", "Łódź"),
])
def test_explicit_codec_bypasses_detection(tmp_path, monkeypatch, codec, name):
    """Every selectable codepage wins over the Russian auto heuristic."""
    source = _plugin(tmp_path, codec, name)
    _, records = reader.read_file(str(source))
    monkeypatch.setenv(ENCODING_ENV_VAR, codec)
    monkeypatch.setattr(reader, "detect_codec", lambda *_: pytest.fail("unexpected detection"))
    assert reader.configure_for_source(str(source), records) == codec
    assert records[0].record_id == name


def test_auto_export_preserves_links_and_import_codec(tmp_path):
    """Localized object/script references resolve, and import gets cp1251."""
    source = _plugin(tmp_path)
    result = export_plugin(str(source), str(tmp_path / "export"))
    output = tmp_path / "export" / "sample.esp"
    assert result["dropped"] == 0
    assert export_encoding(str(output)) == "cp1251"
    objects = (output / "ACTI.txt").read_text(encoding="utf-8")
    script = (output / "SCPT.txt").read_text(encoding="utf-8")
    refs = (output / "REFR.txt").read_text(encoding="utf-8")
    ctx = MorrowindContext()
    ctx.register_own("Аргонианин", "ACTI")
    ctx.register_own("Скрипт", "SCPT")
    assert "FULL=Аргонианин" in objects
    assert "SCRI=" + ctx.resolve("Скрипт", "SCPT") in objects
    assert "Variable[0].Name=Аргонианин" in script
    assert "NAME=" + ctx.resolve("Аргонианин", "ACTI") in refs


@pytest.mark.parametrize("selected", ["auto", "cp1250", "cp1252"])
def test_saved_choice_reaches_morrowind_export(tmp_path, selected):
    """Direct export honors the same saved setting as the GUI/CLI route."""
    codec = "cp1251" if selected == "auto" else selected
    name = {"cp1251": "Аргонианин", "cp1250": "Łódź", "cp1252": "Grüße"}[codec]
    source = _plugin(tmp_path, codec, name)
    assert run_export(source.name, str(source), str(tmp_path / "export"),
                      {"tes4Encoding": selected})
    assert export_encoding(str(tmp_path / "export" / source.name)) == codec


def test_explicit_choice_overrides_saved_setting(tmp_path, monkeypatch):
    """A per-run cp1251 selection wins over a saved Western setting."""
    source = _plugin(tmp_path)
    monkeypatch.setenv(ENCODING_ENV_VAR, "cp1251")
    assert run_export(source.name, str(source), str(tmp_path / "export"),
                      {"tes4Encoding": "cp1252"})
    assert current() == "cp1251"


def test_localized_master_filename_is_resolved_after_detection(tmp_path):
    """Auto resolves a Cyrillic MAST filename before checking its export."""
    source = _plugin(tmp_path, masters=["База.esm"])
    root = tmp_path / "export"
    master = root / "База.esm"
    master.mkdir(parents=True)
    (master / "_HEADER.txt").write_text("Flags=1\nENCODING=cp1251\n", encoding="utf-8")
    assert run_export(source.name, str(source), str(root))
    assert "Master[0]=База.esm" in (root / source.name / "_HEADER.txt").read_text(encoding="utf-8")


def test_fixed_fields_share_the_selected_codec(tmp_path, monkeypatch):
    """Inventory, AI targets, travel and locals use the selected codec too."""
    monkeypatch.setenv(ENCODING_ENV_VAR, "cp1251")
    raw = "Предмет".encode("cp1251")
    sub = reader.Subrecord
    npc = reader.Tes3Record("NPC_", 0, [
        sub("NAME", b"holder\x00"),
        sub("NPCO", struct.pack("<i", 2) + raw.ljust(32, b"\x00")),
        sub("AI_A", struct.pack("<32sB", raw, 0)),
        sub("DODT", struct.pack("<6f", 0, 0, 0, 0, 0, 0)),
        sub("DNAM", raw + b"\x00"),
        sub("SCVR", raw + b"\x00"),
    ], "holder")
    ctx = MorrowindContext()
    ctx.register_own("Предмет", "ACTI")
    lines = []
    emit_inventory(lines, npc, ctx)
    assert "Item[0].FormID=" + ctx.resolve("Предмет") in lines
    assert _packages(npc)[0][1]["target"] == "Предмет"
    assert destinations(npc)[0][1] == "Предмет"
    assert "Variable[0].Name=Предмет" in export_SCPT(npc, ctx)
    path = tmp_path / "holders.esm"
    path.write_bytes(_record("TES3") + _record("NPC_", *(_sub(s.type, s.data) for s in npc.subrecords)))
    assert _vanilla_holders([str(path)], {"предмет"}) == {("NPC_", "holder"): {"предмет"}}


def test_detector_skips_synthetic_records(tmp_path):
    """An offset-less synthesized record cannot read an unrelated header."""
    path = tmp_path / "empty.esm"
    path.write_bytes(_record("TES3"))
    assert reader.detect_codec(str(path), [reader.Tes3Record("ACTI", 0)]) == "cp1252"


def test_signal_byte_decides_from_one_tes3_field(tmp_path):
    """A cp1252-undefined byte triggers Russian detection in a tiny plugin."""
    path = tmp_path / "signal.esp"
    path.write_bytes(_record("TES3") + _record("ACTI", _sub("FNAM", b"\x81")))
    assert reader.detect_codec(str(path), reader.read_file(str(path))[1]) == "cp1251"


def test_patch_detects_before_comparing_vanilla_ids(tmp_path, monkeypatch):
    """A supplied Cyrillic ID cannot be mistaken for a missing vanilla object."""
    from tes4_export import morrowind_patch as patch
    from tes4_export.morrowind_ids import IdIndex, encode_editor_id

    path = _plugin(tmp_path)
    index = IdIndex()
    names = ["Аргонианин"] + [f"object{i}" for i in range(7)] + ["Скрипт"]
    for i, name in enumerate(names):
        index.add(encode_editor_id(name), f"{0x01400001 + i:08X}", "ACTI")
    monkeypatch.setattr(patch, "source_paths", lambda *_: ([str(path)], []))
    monkeypatch.setattr(patch, "supplied_index", lambda *_: index)
    monkeypatch.setattr(patch, "blacklisted_bases", lambda *_: set())
    result = patch.export_patch(str(tmp_path), str(tmp_path / "export"), ["Morrowind_ob.esm"])
    assert result["ok"] and result["records"] == 0
    assert current() == "cp1251"


def test_phase_export_applies_saved_codec_to_patch(tmp_path, monkeypatch):
    """The patch branch receives saved settings before it starts exporting."""
    import convert

    monkeypatch.setattr(convert, "run_patch_export", lambda _: choice() == "cp1250")
    assert convert.phase_export(convert.PATCH_NAME, "", str(tmp_path), {"tes4Encoding": "cp1250"})
