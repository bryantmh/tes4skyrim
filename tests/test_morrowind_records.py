"""
Morrowind (TES3) export: the TES4 vocabulary, placements and teleport doors.

Every test builds its records by hand, so none needs Morrowind installed.
The invariants are recorded in docs/commentary/tes4_export_morrowind.md.
"""

import os
import struct

from core.plugin_masters import is_master_export
from tes4_export import tes3_reader as reader
from tes4_export.export_morrowind import (MorrowindContext, convert_plugin,
                                          converted_master_dirs, load_context,
                                          run_export, write_export,
                                          write_header)
from tes4_export.record_types.morrowind import (export_ARMO, export_DOOR,
                                                export_LIGH,
                                                export_SOUN, export_WEAP,
                                                tes4_signature)
from tes4_export.record_types.morrowind_actors import export_LEVI, export_NPC_
from tes4_export.record_types.morrowind_scripts import export_SCPT
from tes4_export.morroblivion import MorroblivionModels
from tes4_export.morrowind_cell import parse_cell as _parse
from tes4_export.morrowind_pathgrid import pathgrid_records
from tes4_export.morrowind_patch import PATCH_NAME, build_patch, patch_formid

#: Struct layouts of the fixed subrecords the tests author.
_WPDT = '<fihHffH6Bi'
_NPDT_FULL = '<h8B27BxHHHBBBxi'
_NPDT_AUTO = '<hBBB3xi'
_AIDT = '<HBBB3xi'


def _sub(sig: str, data: bytes):
    """One TES3 subrecord."""
    return reader.Subrecord(type=sig, data=data)


def _text(sig: str, value: str):
    """A NUL-padded cp1252 string subrecord."""
    return _sub(sig, value.encode('cp1252') + b'\x00')


def _rec(sig: str, record_id: str, *subs) -> reader.Tes3Record:
    """A TES3 record whose NAME is `record_id`."""
    rec = reader.Tes3Record(type=sig, flags=0, record_id=record_id)
    rec.subrecords = [_text('NAME', record_id)] + list(subs)
    return rec


def _placement(x: float, y: float, z: float) -> bytes:
    """A 24-byte position + rotation payload."""
    return struct.pack('<6f', x, y, z, 0.0, 0.0, 0.0)


def _cell(name: str, *refs) -> reader.Tes3Record:
    """An interior cell holding the given (record_id, extra subrecords)."""
    subs = [_sub('DATA', struct.pack('<iii', 1, 0, 0))]
    for index, (record_id, extra) in enumerate(refs):
        subs.append(_sub('FRMR', struct.pack('<I', index + 1)))
        subs.append(_text('NAME', record_id))
        subs.append(_sub('DATA', _placement(10.0, 20.0, 30.0)))
        subs.extend(extra)
    return _rec('CELL', name, *subs)


def _value(lines: list, key: str) -> str:
    """The value of the one line carrying `key`."""
    return next(line for line in lines if line.startswith(key + '=')).split('=', 1)[1]


def test_item_exporters_speak_the_tes4_vocabulary():
    """Stats reach the importer only under TES4's own key names.

    See: docs/commentary/tes4_export_morrowind.md#tes4-vocabulary
    """
    ctx = MorrowindContext()
    wpdt = struct.pack(_WPDT, 12.0, 48, 3, 1800, 1.3, 1.0, 500,
                       3, 14, 2, 9, 1, 6, 0)
    mace = _rec('WEAP', 'steel mace', _text('FNAM', 'Steel Mace'),
                _sub('WPDT', wpdt))
    lines = export_WEAP(mace, ctx)
    assert 'DATA.Type=2' in lines and 'DATA.Damage=14' in lines
    assert 'DATA.Weight=12.0' in lines and 'DATA.Health=1800' in lines
    assert tes4_signature(mace) == 'WEAP'

    arrow = _rec('WEAP', 'iron arrow', _sub('WPDT', struct.pack(
        _WPDT, 0.1, 1, 12, 0, 1.0, 0.0, 0, 1, 3, 1, 3, 1, 3, 0)))
    assert tes4_signature(arrow) == 'AMMO'
    assert not any(l.startswith('DATA.Type=') for l in export_WEAP(arrow, ctx))

    light = _rec('LIGH', 'lamp', _sub('LHDT', struct.pack(
        '<fiiiIi', 0.0, 0, -1, 256, 0x00FF8040, 0x08)))
    lines = export_LIGH(light, ctx)
    assert _value(lines, 'DATA.Color.R') == '64'
    assert _value(lines, 'DATA.Color.G') == '128'
    assert _value(lines, 'DATA.Color.B') == '255'
    assert 'DATA.Radius=256' in lines and 'DATA.Flags=8' in lines


def test_key_flag_and_tools_change_signature():
    """A flagged MISC is a KEYM; repair tools are MISC the importer can take."""
    key = _rec('MISC', 'key_1', _sub('MCDT', struct.pack('<fii', 0.0, 10, 1)))
    cup = _rec('MISC', 'cup', _sub('MCDT', struct.pack('<fii', 1.0, 2, 0)))
    assert tes4_signature(key) == 'KEYM'
    assert tes4_signature(cup) == 'MISC'
    assert tes4_signature(_rec('REPA', 'hammer')) == 'MISC'
    assert tes4_signature(_rec('LEVI', 'l_items')) == 'LVLI'
    assert tes4_signature(_rec('LEVC', 'l_crea')) == 'LVLC'


def test_leveled_item_flags_swap_bits():
    """Morrowind's Each/AllLevels bits are TES4's the other way round.

    See: docs/commentary/tes4_export_morrowind.md#leveled-lists
    """
    ctx = MorrowindContext()
    ctx.register_own('gold_001', 'MISC')
    rec = _rec('LEVI', 'l_gold', _sub('DATA', struct.pack('<i', 1)),
               _sub('NNAM', b'\x19'), _sub('INDX', struct.pack('<I', 2)),
               _text('INAM', 'gold_001'), _sub('INTV', struct.pack('<H', 3)),
               _text('INAM', 'not_converted'),
               _sub('INTV', struct.pack('<H', 1)))
    lines = export_LEVI(rec, ctx)
    assert 'LVLF.Flags=2' in lines and 'LVLD.ChanceNone=25' in lines
    assert 'EntryCount=1' in lines and 'Entry[0].Level=3' in lines


def test_sound_ranges_and_volume_scale_to_tes4():
    """Zero ranges take Morrowind's defaults; volume becomes attenuation.

    See: docs/commentary/tes4_export_morrowind.md#sounds
    """
    ctx = MorrowindContext()
    loud = _rec('SOUN', 'Door', _text('FNAM', 'Fx/door.wav'),
                _sub('DATA', bytes((255, 0, 0))))
    lines = export_SOUN(loud, ctx)
    assert _value(lines, 'FNAM.Filename') == 'Fx' + chr(92) * 2 + 'door.wav'
    assert 'SNDX.MinAttDist=20' in lines and 'SNDX.MaxAttDist=20' in lines
    assert 'SNDX.StaticAttenuation=0' in lines
    quiet = _rec('SOUN', 'Hum', _sub('DATA', bytes((128, 10, 20))))
    lines = export_SOUN(quiet, ctx)
    assert 'SNDX.MinAttDist=40' in lines and 'SNDX.MaxAttDist=10' in lines
    assert 590 < int(_value(lines, 'SNDX.StaticAttenuation')) < 610


def test_npc_exports_in_the_tes4_actor_vocabulary():
    """Race by Oblivion FormID, flags remapped, skills folded onto TES4's 21.

    See: docs/commentary/tes4_export_morrowind.md#actors-and-placements
    """
    ctx = MorrowindContext()
    ctx.register_own('Mages Guild', 'FACT')
    ctx.register_own('Mage', 'CLAS')
    skills = [10] * 27
    skills[6] = 70
    skills[4] = 40
    npdt = struct.pack(_NPDT_FULL, 12, *([50] * 8), *skills,
                       120, 80, 200, 50, 0, 3, 75)
    rec = _rec('NPC_', 'fargoth', _text('FNAM', 'Fargoth'),
               _text('RNAM', 'Wood Elf'), _text('CNAM', 'Mage'),
               _text('ANAM', 'Mages Guild'), _sub('NPDT', npdt),
               _sub('FLAG', struct.pack('<I', 0x04 | 0x02)),
               _sub('AIDT', struct.pack(_AIDT, 0, 30, 20, 90, 0x8021)))
    lines = export_NPC_(rec, ctx)
    assert 'RNAM.Race=000223C8' in lines
    assert 'ACBS.Flags=10' in lines and 'ACBS.Level=12' in lines
    assert 'DATA.Health=120' in lines and 'DATA.Blunt=70' in lines
    assert 'Faction[0].Rank=3' in lines and 'FactionCount=1' in lines
    assert 'AIDT.Aggression=30' in lines and 'AIDT.Confidence=80' in lines
    assert 'AIDT.Services=1' in lines and 'AIPackageCount=0' in lines


def test_actor_placements_are_achr_and_acre():
    """A placed NPC is an ACHR, a creature an ACRE, a static a REFR.

    See: docs/commentary/tes4_export_morrowind.md#actors-and-placements
    """
    npc = _rec('NPC_', 'guard', _sub('FLAG', struct.pack('<I', 0)),
               _sub('NPDT', struct.pack(_NPDT_AUTO, 1, 50, 0, 0, 0)))
    rat = _rec('CREA', 'rat', _sub('FLAG', struct.pack('<I', 0)))
    rock = _rec('STAT', 'rock')
    cell = _cell('Test Cell', ('guard', ()), ('rat', ()), ('rock', ()))
    out = convert_plugin([npc, rat, rock, cell], MorrowindContext())
    assert [len(out[s]) for s in ('ACHR', 'ACRE', 'REFR')] == [1, 1, 1]
    assert out['ACHR'][0][1][0] == 'NAME=' + out['NPC_'][0][0]


def test_teleport_door_names_the_door_it_arrives_beside():
    """A load door's XTEL names the authored door at its destination.

    Vanilla points 1,703 of 1,722 XTELs at another DOOR reference and never
    once at an XMarker, which an earlier pass minted for every door.
    See: docs/commentary/tes4_export_morrowind.md#teleport-doors
    """
    door = _rec('DOOR', 'door1', _text('FNAM', 'Door'))
    dest = _sub('DODT', _placement(10.0, 20.0, 30.0))
    linked = _cell('Cell A', ('door1', (dest, _text('DNAM', 'Cell B'))))
    orphan = _cell('Cell C', ('door1', (dest, _text('DNAM', 'Nowhere'))))
    target = _cell('Cell B', ('door1', ()))

    ctx = MorrowindContext()
    out = convert_plugin([door, linked, orphan, target], ctx)

    doors = [(fid, lines) for fid, lines in out['REFR']
             if any(l.startswith('XTEL.Door=') for l in lines)]
    assert len(doors) == 1, 'only the door with a converted destination links'
    assert not any('NAME=00000001' in lines for _, lines in out['REFR']),         'no XMarker is minted for a teleport'

    partner = _value(doors[0][1], 'XTEL.Door')
    target_refs = {fid for fid, lines in out['REFR']
                   if _value(lines, 'ParentCELL')
                   == ctx.interior_cell_id('Cell B')}
    assert partner in target_refs, 'the target stands in the destination cell'
    assert 'XTEL.PosZ=30.0' in doors[0][1]
    assert 'RecordFlags=1024' in doors[0][1]
    assert ctx.unlinked_doors == 1


def _export(tmp_path, name: str, records, master_dirs=()) -> str:
    """Convert `records` as plugin `name` into tmp_path, borrowing from masters."""
    masters = [(os.path.basename(d), d) for d in master_dirs]
    ctx = load_context(str(tmp_path), masters)
    out_dir = os.path.join(str(tmp_path), name)
    counts = write_export(convert_plugin(records, ctx), out_dir)
    write_header(out_dir, [n for n, _d in masters], sum(counts.values()),
                 'fixture', flags=1)
    return out_dir


def test_a_tes3_export_without_dialogue_is_still_tes3(tmp_path):
    """The header, not a dialogue file, marks the source; the patch opts out.

    See: docs/commentary/tes5_import_world.md#tes3-dont-havok-settle
    """
    from tes5_import.dialogue.morrowind_sidecar import is_tes3_export
    plugin = _export(tmp_path, 'Groundcover.esp', [_rec('STAT', 'grass')])
    assert not os.path.isfile(os.path.join(plugin, 'MWDI.txt'))
    assert is_tes3_export(plugin)
    patch = tmp_path / 'Patch'
    patch.mkdir()
    write_header(str(patch), [], 0, 'fixture', flags=1, source='')
    assert not is_tes3_export(str(patch))


def test_a_plugin_ships_only_the_sounds_no_master_names(tmp_path):
    """The shared Data folder means ownership is per FILE, not per plugin.

    See: docs/commentary/tes4_export_morrowind.md#which-sounds-a-plugin-ships
    """
    from asset_convert.sources.morrowind_sound_scope import owned_files
    shared = _rec('SOUN', 'Door Open', _text('FNAM', 'Fx/door.wav'))
    master = _export(tmp_path, 'Master.esm', [shared])
    mine = _rec('SOUN', 'Bell', _text('FNAM', 'Fx/bell.wav'))
    plugin = _export(tmp_path, 'Mine.esp', [shared, mine], [master])
    door, bell = 'fx' + chr(92) + 'door.wav', 'fx' + chr(92) + 'bell.wav'

    assert owned_files(master, []) == {door}
    assert owned_files(plugin, [master]) == {bell},         'the master already ships the door, so this plugin must not'

    alone = _export(tmp_path, 'Alone.esp', [shared, mine])
    assert owned_files(alone, []) == {door, bell},         'with no converted master a plugin ships every sound it names'


def _form_ids(path: str) -> set:
    """Every FormID= value in one export file."""
    with open(path, encoding='utf-8') as fh:
        return {line.split('=', 1)[1].strip() for line in fh
                if line.startswith('FormID=')}


def test_dependent_plugin_borrows_its_masters_records(tmp_path):
    """A master's object resolves to the master's FormID or is dropped, never minted.

    See: docs/commentary/tes4_export_morrowind.md#masters
    """
    rock, hut = _rec('STAT', 'rock'), _rec('STAT', 'hut')
    master = _export(tmp_path, 'Master.esm',
                     [rock, hut, _cell('Master Hall', ('rock', ()))])
    master_ids = _form_ids(os.path.join(master, 'STAT.txt'))
    assert master_ids and all(fid.startswith('00') for fid in master_ids)

    ctx = load_context(str(tmp_path), [(os.path.basename(master), master)])
    assert ctx.own_index == 1
    assert ctx.resolve('HUT') in master_ids
    assert ctx.resolve('unknown_master_object') == ''
    door = _rec('DOOR', 'gate')
    dest = _sub('DODT', _placement(1.0, 2.0, 3.0))
    child_cell = _cell('Child Hall', ('rock', ()),
                       ('gate', (dest, _text('DNAM', 'Master Hall'))))
    out = convert_plugin([door, child_cell], ctx)
    assert not out.get('STAT')
    assert all(fid.startswith('01') for fid, _ in out['REFR'])
    assert out['REFR'][0][1][0] == 'NAME=' + ctx.resolve('rock')
    assert ctx.interior_cell_id('Master Hall').startswith('00')
    assert ctx.unlinked_doors == 1,         'the master cell holds no door of ours to arrive beside'


def test_wearable_names_its_body_parts():
    """A piece with parts names a synthetic worn model, lists its BODY parts and its type.

    A pauldron has no Oblivion slot but is worn all the same.
    See: docs/commentary/tes4_export_morrowind.md#worn-models
    """
    ctx = MorrowindContext()
    ctx.body_models = {'a_iron_cuirass': 'a' + chr(92) + 'A_Iron_skinned.NIF'}
    aodt = struct.pack('<ifiiii', 1, 30.0, 100, 500, 0, 15)
    cuirass = _rec('ARMO', 'iron cuirass', _sub('AODT', aodt),
                   _sub('INDX', b'\x03'), _text('BNAM', 'a_iron_cuirass'))
    lines = export_ARMO(cuirass, ctx)
    assert _value(lines, 'Male.BipedModel.MODL') == (
        'armor' + chr(92) * 2 + 'morrowind' + chr(92) * 2 + 'm'
        + chr(92) * 2 + '0ironscuirass.nif')
    assert 'MorrowindPartCount=1' in lines and 'MorrowindPart[0].Slot=3' in lines
    assert 'Female.BipedModel.MODL' not in ''.join(lines)
    assert 'MorrowindWearableType=1' in lines
    pauldron = _rec('ARMO', 'iron pauldron', _sub('AODT', struct.pack(
        '<ifiiii', 2, 5.0, 10, 100, 0, 5)), _sub('INDX', b'\x0d'),
        _text('BNAM', 'a_iron_cuirass'))
    lines = export_ARMO(pauldron, ctx)
    assert any(l.startswith('Male.BipedModel') for l in lines)
    assert 'BMDT.BipedFlags=0' in lines and 'MorrowindWearableType=2' in lines


def test_helmet_hides_the_head_only_when_it_fills_the_head_part():
    """An open helm (Hair part only) keeps the face; a closed helm (Head part) hides it.

    See: docs/commentary/tes4_export_morrowind.md#equipment-slots
    """
    ctx = MorrowindContext()
    ctx.body_models = {'helm': 'a' + chr(92) + 'helm.nif'}
    aodt = _sub('AODT', struct.pack('<ifiiii', 0, 5.0, 10, 100, 0, 5))
    for part, flags in ((b'\x01', '2'), (b'\x00', '3')):
        helm = _rec('ARMO', 'helm', aodt, _sub('INDX', part), _text('BNAM', 'helm'))
        assert _value(export_ARMO(helm, ctx), 'BMDT.BipedFlags') == flags


def test_ai_packages_become_pack_records():
    """Inline AI_* subrecords become listed PACKs; travel gets a marker.

    See: docs/commentary/tes4_export_morrowind.md#ai-packages
    """
    wander = _sub('AI_W', struct.pack('<hhB8BB', 500, 2, 0, *([0] * 8), 0))
    travel = _sub('AI_T', struct.pack('<fffi', 100.0, 200.0, 300.0, 0))
    npc = _rec('NPC_', 'walker', _sub('FLAG', struct.pack('<I', 0)),
               _sub('NPDT', struct.pack(_NPDT_AUTO, 1, 50, 0, 0, 0)),
               wander, travel)
    ctx = MorrowindContext()
    out = convert_plugin([npc, _cell('Hall', ('walker', ()))], ctx)
    npc_lines = out['NPC_'][0][1]
    assert 'AIPackageCount=2' in npc_lines
    packs = dict(out['PACK'])
    assert _value(npc_lines, 'AIPackage[0]') in packs
    wander_lines = packs[_value(npc_lines, 'AIPackage[0]')]
    assert 'PKDT.Type=5' in wander_lines and 'PLDT.Radius=500' in wander_lines
    assert 'PSDT.Duration=2' in wander_lines
    travel_lines = packs[_value(npc_lines, 'AIPackage[1]')]
    marker = _value(travel_lines, 'PLDT.Location')
    marker_lines = next(l for fid, l in out['REFR'] if fid == marker)
    assert 'NAME=0000003B' in marker_lines and 'PosZ=300.0' in marker_lines
    assert _value(marker_lines, 'ParentCELL') == ctx.interior_cell_id('Hall')


def test_creature_owned_from_source_archive_before_extraction(tmp_path, monkeypatch):
    """A first GUI run exports BEFORE extracting, so ownership reads the archives.

    See: docs/commentary/tes4_export_morrowind.md#who-owns-a-mesh
    """
    from tes4_export import morroblivion
    monkeypatch.setattr(morroblivion, 'source_meshes',
                        lambda _p: frozenset({'r' + chr(92) + 'guar.nif'}))
    ctx = MorrowindContext()
    ctx.morroblivion = MorroblivionModels(str(tmp_path), [],
                                          str(tmp_path / 'none.esm'),
                                          tmp_path / 'not-extracted-yet')
    guar = _rec('CREA', 'guar', _text('MODL', 'r/Guar.NIF'),
                _sub('FLAG', struct.pack('<I', 0)))
    lines = convert_plugin([guar], ctx)['CREA'][0][1]
    assert _value(lines, 'MorrowindModel') == 'r' + chr(92) * 2 + 'Guar.NIF'


def test_creature_names_its_split_folder_and_sounds(tmp_path):
    """A creature points at the split folder and carries its SNDG slots.

    See: docs/commentary/tes4_export_morrowind.md#creatures
    """
    roar = _rec('SOUN', 'guar roar', _text('FNAM', 'Cr/guar/roar.wav'))
    sndg = _rec('SNDG', 'guar roar gen', _sub('DATA', struct.pack('<i', 5)),
                _text('SNAM', 'guar roar'), _text('CNAM', 'guar'))
    guar = _rec('CREA', 'guar', _text('MODL', 'r/Guar.NIF'),
                _sub('FLAG', struct.pack('<I', 0)))
    ctx = MorrowindContext()
    meshes = tmp_path / 'meshes'
    (meshes / 'r').mkdir(parents=True)
    (meshes / 'r' / 'Guar.NIF').write_bytes(b'')
    ctx.morroblivion = MorroblivionModels(str(tmp_path), [],
                                          str(tmp_path / 'none.esm'), meshes)
    out = convert_plugin([roar, sndg, guar], ctx)
    lines = out['CREA'][0][1]
    assert _value(lines, 'Model.MODL') == 'r' + chr(92) * 2 + 'guar' + chr(92) * 2 + 'skeleton.nif'
    assert 'NIFZ[0]=guar.nif' in lines
    assert _value(lines, 'MorrowindModel') == 'r' + chr(92) * 2 + 'Guar.NIF'
    assert 'SoundType[0].Type=6' in lines
    assert _value(lines, 'SoundType[0].Sound') == ctx.resolve('guar roar')


def test_moan_becomes_aware_not_idle(tmp_path):
    """Moan is the vocal the graph annotates; CSDT 4 is deliberately silent.

    See: docs/commentary/tes4_export_morrowind.md#creature-sound-generators
    """
    moan = _rec('SOUN', 'guar moan', _text('FNAM', 'Cr/guar/moan.wav'))
    land = _rec('SOUN', 'guar land', _text('FNAM', 'Cr/guar/land.wav'))
    gens = [_rec('SNDG', 'guar moan gen', _sub('DATA', struct.pack('<i', 4)),
                 _text('SNAM', 'guar moan'), _text('CNAM', 'guar')),
            _rec('SNDG', 'guar land gen', _sub('DATA', struct.pack('<i', 7)),
                 _text('SNAM', 'guar land'), _text('CNAM', 'guar'))]
    guar = _rec('CREA', 'guar', _text('MODL', 'r/Guar.NIF'),
                _sub('FLAG', struct.pack('<I', 0)))
    ctx = MorrowindContext()
    meshes = tmp_path / 'meshes'
    (meshes / 'r').mkdir(parents=True)
    (meshes / 'r' / 'Guar.NIF').write_bytes(b'')
    ctx.morroblivion = MorroblivionModels(str(tmp_path), [],
                                          str(tmp_path / 'none.esm'), meshes)
    lines = convert_plugin([moan, land] + gens + [guar], ctx)['CREA'][0][1]
    slots = {_value(lines, f'SoundType[{i}].Type') for i in range(2)}
    assert slots == {'5', '8'}, 'Moan lands on Aware (5), Land on Death (8)'
    assert '4' not in slots, 'CSDT 4 loops the idle clip and is never annotated'


def test_creature_on_a_vanilla_mesh_binds_to_morroblivions(tmp_path):
    """A vanilla mesh the plugin does not ship takes Morroblivion's model, unsplit.

    See: docs/commentary/tes4_export_morrowind.md#morroblivion-creatures
    """
    ctx = MorrowindContext()
    ctx.morroblivion = MorroblivionModels(str(tmp_path), [], str(tmp_path / 'none.esm'))
    ctx.morroblivion.creatures = {'0kwamawarrior': (
        'Morroblivion' + chr(92) + 'KwamaWarrior' + chr(92) + 'skeleton.nif', ['mesh.nif'])}
    crea = _rec('CREA', 'kwama warrior', _text('MODL', 'r/kwama warior.nif'),
                _sub('FLAG', struct.pack('<I', 0)))
    lines = convert_plugin([crea], ctx)['CREA'][0][1]
    assert _value(lines, 'Model.MODL') == (
        'Morroblivion' + chr(92) * 2 + 'KwamaWarrior' + chr(92) * 2 + 'skeleton.nif')
    assert 'NIFZ[0]=mesh.nif' in lines and not any(
        l.startswith('MorrowindModel') for l in lines)


def test_script_exports_its_source_and_attaches_by_scri():
    """SCPT is named by SCHD, exports SCTX and SCVR; SCRI links to it.

    See: docs/commentary/tes4_export_morrowind.md#scripts
    """
    schd = struct.pack('<32sIIIII', b'ajiraScript', 2, 0, 0, 0, 0)
    script = reader.Tes3Record(type='SCPT', flags=0)
    script.subrecords = [_sub('SCHD', schd), _sub('SCVR', b'doOnce\x00count\x00'),
                         _text('SCTX', 'Begin ajiraScript\r\nshort doOnce\r\nEnd')]
    script.record_id = reader.script_name(script)
    assert script.record_id == 'ajiraScript'
    ctx = MorrowindContext()
    lines = export_SCPT(script, ctx)
    assert 'SCHR.Type=0' in lines and 'VariableCount=2' in lines
    assert 'Variable[1].Name=count' in lines and 'Variable[1].Index=2' in lines
    assert _value(lines, 'SCTX').startswith('Begin ajiraScript')
    lever = _rec('ACTI', 'lever', _text('SCRI', 'ajiraScript'))
    out = convert_plugin([script, lever], ctx)
    assert _value(out['ACTI'][0][1], 'SCRI') == ctx.resolve('ajiraScript')


def _tes3_plugin(path, masters=()) -> str:
    """A minimal TES3 file on disk declaring `masters`; its path."""
    subs = b''
    for name in masters:
        payload = name.encode('cp1252') + b'\x00'
        subs += b'MAST' + struct.pack('<I', len(payload)) + payload
        subs += b'DATA' + struct.pack('<I', 8) + struct.pack('<Q', 0)
    header = b'TES3' + struct.pack('<I', len(subs)) + b'\x00' * 8 + subs
    path.write_bytes(header)
    return str(path)


def test_export_refuses_a_master_that_is_not_converted(tmp_path, capsys):
    """An unconverted master must stop the export, not renumber this plugin.

    The master list fixes the plugin's own load-order byte, so exporting
    without one silently moves every record into a master's id space.
    See: docs/commentary/tes4_export_morrowind.md#masters
    """
    export = tmp_path / 'export'
    export.mkdir()
    source = _tes3_plugin(tmp_path / 'Child.esm', ['Base.esm', 'Extra.esm'])

    found, missing = converted_master_dirs(str(export), 'Child.esm', source)
    assert found == [] and missing == ['Base.esm', 'Extra.esm']
    assert run_export('Child.esm', source, str(export)) is False
    assert not (export / 'Child.esm').exists()
    out = capsys.readouterr().out
    assert 'Base.esm' in out and 'Extra.esm' in out

    _export(export, 'Base.esm', [_rec('STAT', 'rock')])
    _export(export, 'Extra.esm', [_rec('STAT', 'hut')])
    found, missing = converted_master_dirs(str(export), 'Child.esm', source)
    assert missing == [] and len(found) == 2
    assert run_export('Child.esm', source, str(export)) is True
    assert load_context(str(export), found).own_index == 2


def _tes3_records(path, records, masters=(), file_type=None) -> str:
    """Write `records` as a real TES3 file declaring `masters`; its path."""
    subs = b''
    if file_type is not None:
        hedr = struct.pack('<fi32s256si', 1.3, file_type, b'', b'', 0)
        subs += b'HEDR' + struct.pack('<I', len(hedr)) + hedr
    for name in masters:
        payload = name.encode('cp1252') + b'\x00'
        subs += b'MAST' + struct.pack('<I', len(payload)) + payload
        subs += b'DATA' + struct.pack('<I', 8) + struct.pack('<Q', 0)
    blob = b'TES3' + struct.pack('<I', len(subs)) + b'\x00' * 8 + subs
    for rec in records:
        body = b''
        for sub in rec.subrecords:
            body += (sub.type.encode('ascii')
                     + struct.pack('<I', len(sub.data)) + sub.data)
        blob += (rec.type.encode('ascii') + struct.pack('<I', len(body))
                 + b'\x00' * 8 + body)
    path.write_bytes(blob)
    return str(path)


def test_export_carries_the_source_esm_flag_not_the_extension(tmp_path):
    """HEDR type 1 exports as a master and 0 as a plugin, whatever the name."""
    export = tmp_path / 'export'
    export.mkdir()
    for name, file_type in (('Flagged.esp', 1), ('Plain.esm', 0)):
        source = _tes3_records(tmp_path / name, [_rec('STAT', 'rock')],
                               file_type=file_type)
        assert run_export(name, source, str(export)) is True
        assert is_master_export(str(export / name)) == bool(file_type)


#: A Morroblivion stand-in named so no stage resolves the REAL converted plugin.
_FIXTURE_MASTER = 'Morrowind_ob_fixture.esm'


def _convert_morroblivion(export, out_root) -> None:
    """Import the stand-in Morroblivion export, which the patch masters."""
    from tes5_import.pipeline import import_plugin
    dest = out_root / _FIXTURE_MASTER / _FIXTURE_MASTER
    dest.parent.mkdir(parents=True)
    import_plugin(export_dir=str(export / _FIXTURE_MASTER),
                  output_path=str(dest), masters=['Skyrim.esm'],
                  output_root=str(out_root))


def test_gap_patch_holds_what_morroblivion_lacks(tmp_path):
    """The patch fills exactly the objects the converted index cannot supply.

    The vanilla master defines two objects and Morroblivion converted only
    one, so the other is exactly the gap. The fill is keyed on the authored
    string, so every plugin needing it names one record rather than minting
    a rival copy.
    See: docs/commentary/tes4_export_morrowind.md#morroblivion-gap-patch
    """
    export = tmp_path / 'export'
    export.mkdir()
    data = tmp_path / 'Data Files'
    data.mkdir()
    for name in ('Morrowind.esm', 'Tribunal.esm', 'Bloodmoon.esm'):
        _tes3_records(data / name, [_rec('STAT', 'ex_scrapwood01'),
                                    _rec('STAT', 'covered_rock')])
    _export(export, _FIXTURE_MASTER, [_rec('STAT', 'covered_rock')])
    _convert_morroblivion(export, tmp_path / 'output')

    result = build_patch(str(data), str(export), [_FIXTURE_MASTER],
                         progress=lambda *_: None,
                         out_root=tmp_path / 'output')

    assert result['ok'], result.get('error')
    assert result['records'] == 1, 'only the object Morroblivion lacks'
    body = (export / PATCH_NAME / 'STAT.txt').read_text(encoding='utf-8')
    assert 'EditorID=ex_scrapwood01' in body
    fid = patch_formid(('STAT', 'ex_scrapwood01'), 1)
    assert f'FormID={fid}' in body
    assert fid.startswith('01'), 'the byte after its one Morroblivion master'
    assert fid[2:] == patch_formid(('STAT', 'ex_scrapwood01'))[2:], (
        'the master count moves the index byte and never the hashed id')
    assert patch_formid(('STAT', 'EX_ScrapWood01'), 1) == fid, (
        'ids are case-insensitive')
    assert patch_formid(('SOUN', 'ex_scrapwood01'), 1) != fid, (
        'one id under two types is two records')


def test_gap_patch_builds_the_plugin_itself(tmp_path):
    """A build reports success only when the installable plugin EXISTS.

    The build wrote an export and an asset tree, reported success, and never
    ran the import -- so `output/` held meshes and textures but no plugin,
    while every Morroblivion-mode conversion still declared it as a master.
    See: docs/commentary/tes4_export_morrowind.md#the-patch-builds-its-own-plugin
    """
    from asset_convert.lod.sibling_lod import converted_plugins
    from tools.esm.make_master import read_header

    export = tmp_path / 'export'
    export.mkdir()
    data = tmp_path / 'Data Files'
    data.mkdir()
    for name in ('Morrowind.esm', 'Tribunal.esm', 'Bloodmoon.esm'):
        _tes3_records(data / name, [_rec('STAT', 'ex_scrapwood01'),
                                    _rec('STAT', 'covered_rock')])
    _export(export, _FIXTURE_MASTER, [_rec('STAT', 'covered_rock')])
    out_root = tmp_path / 'output'
    _convert_morroblivion(export, out_root)

    result = build_patch(str(data), str(export), [_FIXTURE_MASTER],
                         progress=lambda *_: None, out_root=out_root)

    assert result['ok'], result.get('error')
    plugin = out_root / PATCH_NAME / PATCH_NAME
    assert plugin.is_file(), 'the build must leave an installable plugin'
    assert result['plugin'] == str(plugin), 'the caller is told where it went'
    assert plugin.read_bytes()[:4] == b'TES4', 'a real plugin, not a stub'

    flags, masters = read_header(str(plugin))
    assert masters == ['Skyrim.esm', _FIXTURE_MASTER], (
        'it names what Morroblivion holds, so it masters Morroblivion')
    assert flags & 0x1, 'ESM-flagged: dependent plugins declare it a master'
    assert PATCH_NAME.lower().endswith('.esp'), 'the .esp extension is kept'
    assert is_master_export(str(export / PATCH_NAME)), (
        'a later -f import of the patch must keep the ESM flag too')
    assert PATCH_NAME in converted_plugins(out_root), (
        'the patch registers as a converted plugin like any other')


def test_morroblivion_mode_refuses_without_the_patch(tmp_path):
    """The patch is a master like any other: missing means refuse, never mint.

    See: docs/commentary/tes4_export_morrowind.md#morroblivion-gap-patch
    """
    from tes4_export.export_morrowind import (SOURCE_MORROBLIVION,
                                              converted_master_dirs)

    export = tmp_path / 'export'
    export.mkdir()
    _export(export, 'Morrowind_ob.esm', [_rec('STAT', 'covered_rock')])
    child = tmp_path / 'Child.esm'
    _tes3_records(child, [_rec('STAT', 'own')], masters=['Morrowind.esm'])

    _, missing = converted_master_dirs(str(export), 'Child.esm', str(child),
                                       SOURCE_MORROBLIVION)
    assert PATCH_NAME in missing, 'an unbuilt patch blocks the conversion'

    (export / PATCH_NAME).mkdir()
    (export / PATCH_NAME / '_HEADER.txt').write_text('Flags=1',
                                                     encoding='utf-8')
    found, missing = converted_master_dirs(str(export), 'Child.esm',
                                           str(child), SOURCE_MORROBLIVION)
    assert not missing
    assert any(n == PATCH_NAME for n, _d in found), 'the patch is borrowed from'


def test_actor_drops_the_reference_to_a_package_that_never_emitted():
    """An unresolvable AI package must not leave the actor naming a missing PACK.

    A travel package whose destination has no cell cannot be written, and an
    actor pointing at the PACK anyway makes the engine resolve a form that
    does not exist -- it warns per actor for the whole load.
    See: docs/commentary/tes4_export_morrowind.md#ai-packages
    """
    wander = _sub('AI_W', struct.pack('<hhB8BB', 500, 3, 0, *([0] * 8), 0))
    travel = _sub('AI_T', struct.pack('<fffi', 1.0e6, 2.0e6, 30.0, 0))
    npc = _rec('NPC_', 'guard', wander, travel)

    ctx = MorrowindContext()
    out = convert_plugin([npc], ctx)

    emitted = {form_id for form_id, _ in out.get('PACK', [])}
    assert len(emitted) == 1, 'the travel package cannot resolve a destination'

    lines = out['NPC_'][0][1]
    named = [ln.split('=', 1)[1] for ln in lines if ln.startswith('AIPackage[')]
    assert named == sorted(emitted), 'the actor names only packages that exist'
    assert 'AIPackageCount=1' in lines, 'the count follows the surviving list'
    assert 'AIPackage[0]=' + named[0] in lines, 'indices stay contiguous'

def test_exterior_teleport_doors_move_to_the_worldspace_persistent_cell():
    """A persistent reference in an exterior GRID cell is never drawn.

    A worldspace loads its persistent references by worldspace, out of one
    dummy cell; Skyrim.esm files all 746 of its exterior teleport doors there
    and none in a grid cell. Marking a door persistent while leaving it in the
    grid cell it stands in removed it from the world entirely.
    See: docs/commentary/tes4_export_morrowind.md#teleport-doors
    """
    door = _rec('DOOR', 'door1', _text('FNAM', 'Door'))
    inside = _cell('Cell A',
                   ('door1', (_sub('DODT', _placement(10.0, 20.0, 30.0)),)))
    outward = _rec('CELL', 'Wilderness',
                   _sub('DATA', struct.pack('<iii', 0, 0, 0)),
                   _sub('FRMR', struct.pack('<I', 1)),
                   _text('NAME', 'door1'),
                   _sub('DATA', _placement(10.0, 20.0, 30.0)),
                   _sub('DODT', _placement(10.0, 20.0, 30.0)),
                   _text('DNAM', 'Cell A'))

    ctx = MorrowindContext()
    out = convert_plugin([door, inside, outward], ctx)

    persistent = [(fid, lines) for fid, lines in out['CELL']
                  if 'RecordFlags=1024' in lines]
    assert len(persistent) == 1, 'the worldspace gets exactly one dummy cell'
    dummy_fid, dummy_lines = persistent[0]
    assert not any(l.startswith('XCLC.') for l in dummy_lines),         'the dummy cell carries no grid, so it stays out of the block tree'
    assert _value(dummy_lines, 'ParentWRLD') == ctx.worldspace_id()

    for fid, lines in out['REFR']:
        if 'RecordFlags=1024' not in lines:
            continue
        parent = _value(lines, 'ParentCELL')
        assert parent not in ctx.exterior_cell_ids,             'no persistent reference may stay in an exterior grid cell'

    homed = [lines for _, lines in out['REFR']
             if _value(lines, 'ParentCELL') == dummy_fid
             and 'MapMarker=1' not in lines]
    assert len(homed) == 1, 'the exterior teleport door moves out of the grid'

def test_door_carries_the_flags_subrecord_tes5_requires():
    """A door with no FNAM is not activated as a load door.

    TES3 has no door-flags field -- its own FNAM is the display name -- so
    nothing carries over and the importer wrote no FNAM at all. xEdit marks
    the TES5 field required and all 235 vanilla doors have one.
    See: docs/commentary/tes4_export_morrowind.md#teleport-doors
    """
    door = _rec('DOOR', 'door1', _text('FNAM', 'Hut Door'))
    lines = export_DOOR(door, MorrowindContext())
    assert 'FNAM.Flags=0' in lines, 'the flags subrecord is always written'
    assert _value(lines, 'FULL') == 'Hut Door',         "TES3's FNAM is the name, and stays the name"


def _pathgrid(grid, points, edges, name=''):
    """A TES3 PGRD: S32 point coords, U32 PGRC targets grouped by degree."""
    degrees = [sum(1 for a, b in edges if a == i) for i in range(len(points))]
    pgrp = b''.join(
        struct.pack('<iiiBBxx', int(x), int(y), int(z), 0, degrees[i])
        for i, (x, y, z) in enumerate(points))
    flat = [b for i in range(len(points)) for a, b in edges if a == i]
    rec = reader.Tes3Record(type='PGRD', flags=0, record_id=name)
    rec.subrecords = [
        _sub('DATA', struct.pack('<iiHH', grid[0], grid[1], 1024,
                                 len(points))),
        _text('NAME', name),
        _sub('PGRP', pgrp),
        _sub('PGRC', struct.pack('<%dI' % len(flat), *flat))]
    return rec


def test_interior_pathgrid_keeps_its_graph_whole():
    """An interior never splits, so every edge stays an edge.

    TES3 stores an edge directed and often only from one end; TES4 reads the
    graph undirected, so each pair has to reappear on BOTH points.
    """
    ctx = MorrowindContext()
    ctx.note_cell(_parse(_cell('Hut')))
    grid = _pathgrid((0, 0), [(0, 0, 8), (100, 0, 8), (100, 100, 8)],
                     [(0, 1), (1, 2)], name='Hut')
    records = pathgrid_records([grid], ctx)

    assert len(records) == 1, 'one interior cell, one pathgrid'
    lines = records[0][1]
    assert _value(lines, 'DATA.PointCount') == '3'
    assert _value(lines, 'ParentCELL') == ctx.interior_cell_id('Hut')
    assert not any(l.startswith('ParentWRLD') for l in lines),         'an interior names no worldspace, which is how the importer knows'
    assert _value(lines, 'Point[0].X') == '0.0', 'S32 coords become floats'
    assert _value(lines, 'Point[1].Connections') == '2',         'the middle point is reached from both sides'
    assert _value(lines, 'Point[0].Edge[0]') == '1'
    assert _value(lines, 'Point[2].Edge[0]') == '1',         'the edge authored only as 1->2 still appears on point 2'


def test_exterior_pathgrid_splits_into_quadrants():
    """One Morrowind cell's grid is shared out over the four cells it covers.

    Positions are unscaled, so a point lands in the quadrant its own world
    position falls in -- the same rule the references follow.
    """
    ctx = MorrowindContext()
    points = [(100.0, 100.0, 8.0), (200.0, 200.0, 8.0),
              (5000.0, 100.0, 8.0), (5100.0, 200.0, 8.0)]
    grid = _pathgrid((0, 0), points, [(0, 1), (2, 3), (1, 2)])
    records = pathgrid_records([grid], ctx)

    assert len(records) == 2, 'points fall in two of the four quadrants'
    by_cell = {_value(lines, 'ParentCELL'): lines for _, lines in records}
    left = by_cell[ctx.exterior_cell_id((0, 0))]
    right = by_cell[ctx.exterior_cell_id((1, 0))]

    assert _value(left, 'DATA.PointCount') == '2'
    assert _value(right, 'DATA.PointCount') == '2'
    assert _value(left, 'ParentWRLD') == ctx.worldspace_id()
    assert _value(left, 'Point[0].X') == '100.0',         'world positions carry across unscaled'


def test_edge_crossing_a_quadrant_becomes_an_intercell_exit():
    """An edge whose ends land in different cells cannot stay an edge.

    TES4 carries a crossing as a PGRI entry naming the local point and the
    FOREIGN node's world position, which is what stitches the navmesh
    corridors of neighbouring cells together.
    """
    ctx = MorrowindContext()
    points = [(100.0, 100.0, 8.0), (5000.0, 100.0, 8.0)]
    grid = _pathgrid((0, 0), points, [(0, 1)])
    records = pathgrid_records([grid], ctx)

    by_cell = {_value(lines, 'ParentCELL'): lines for _, lines in records}
    left = by_cell[ctx.exterior_cell_id((0, 0))]

    assert _value(left, 'InterCellCount') == '1'
    assert _value(left, 'InterCell[0].LocalPoint') == '0'
    assert _value(left, 'InterCell[0].X') == '5000.0',         'the entry names where the edge goes, not where it starts'
    assert not any(l.startswith('Point[0].Edge') for l in left),         'the crossing is no longer an ordinary edge'


def test_pathgrid_survives_garbage_the_construction_set_leaves():
    """A self-edge or an out-of-range target is dropped, not propagated.

    Both appear in shipped Morrowind data and an out-of-range target would
    index off the end of the TES4 point array.
    """
    ctx = MorrowindContext()
    ctx.note_cell(_parse(_cell('Hut')))
    grid = _pathgrid((0, 0), [(0, 0, 8), (100, 0, 8)],
                     [(0, 0), (0, 99), (0, 1)], name='Hut')
    lines = pathgrid_records([grid], ctx)[0][1]

    assert _value(lines, 'Point[0].Connections') == '1',         'only the one real edge is counted'
    assert _value(lines, 'Point[0].Edge[0]') == '1'


def test_named_exterior_pathgrid_is_not_mistaken_for_an_interior():
    """An exterior pathgrid carries a NAME too, so the name cannot route it.

    All 3436 of TR_Mainland.esm's pathgrids are named -- an exterior's NAME is
    its region or cell name -- so routing on "has a NAME" sent every one down
    the interior path and produced zero exteriors.
    """
    ctx = MorrowindContext()
    ctx.note_cell(_parse(_rec(
        'CELL', 'Anashbibi',
        _sub('DATA', struct.pack('<iii', 0, -21, -25)))))
    grid = _pathgrid((-21, -25),
                     [(-21 * 8192.0 + 10, -25 * 8192.0 + 10, 8.0),
                      (-21 * 8192.0 + 20, -25 * 8192.0 + 20, 8.0)],
                     [(0, 1)], name='Anashbibi')
    records = pathgrid_records([grid], ctx)

    assert records, 'the pathgrid converts'
    for _fid, lines in records:
        assert 'ParentWRLD' in '\n'.join(lines),             'a pathgrid over a named EXTERIOR cell still belongs to the world'


def test_quadrant_leads_with_a_point_that_kept_an_edge():
    """Point 0 must carry an edge or the importer discards the whole topology.

    from_pgrd.py probes Point[0].Edge[0] alone and falls back to
    nearest-neighbour edges for the ENTIRE record when it is missing --
    which splitting a cell causes whenever point 0's every edge became a
    crossing. Here point 0 leaves the quadrant and points 1 and 2 do not.
    """
    ctx = MorrowindContext()
    points = [(100.0, 100.0, 8.0), (300.0, 300.0, 8.0), (400.0, 400.0, 8.0),
              (5000.0, 100.0, 8.0)]
    grid = _pathgrid((0, 0), points, [(0, 3), (1, 2)])
    records = pathgrid_records([grid], ctx)

    by_cell = {_value(lines, 'ParentCELL'): lines for _, lines in records}
    left = by_cell[ctx.exterior_cell_id((0, 0))]

    assert 'Point[0].Edge[0]' in '\n'.join(left),         'the record leads with a point whose edge survived the split'
    assert _value(left, 'InterCellCount') == '1',         'the crossing is still carried, just not on point 0'


def test_exterior_points_are_lifted_out_of_the_cells_own_frame():
    """An exterior pathgrid stores points relative to its own cell origin.

    Measured on TR_Mainland.esm, every exterior point falls in 0..8192
    whatever the cell's grid. Bucketing those raw values put every exterior
    in quadrant (0,0): 4 FormIDs repeated ~280 times each, and 1188 of 4125
    records lost to the collision.
    """
    ctx = MorrowindContext()
    grid = _pathgrid((-21, -25), [(100.0, 200.0, 8.0), (300.0, 400.0, 8.0)],
                     [(0, 1)], name='Velothi Mountains Region')
    records = pathgrid_records([grid], ctx)

    assert len(records) == 1, 'both points sit in one quadrant'
    _fid, lines = records[0]
    assert _value(lines, 'ParentCELL') == ctx.exterior_cell_id((-42, -50)),         'the cell is the quadrant the SHIFTED point falls in'
    assert _value(lines, 'Point[0].X') == str(-21 * 8192.0 + 100.0),         'the cell origin is added, so the point is in world space'


def test_two_exterior_cells_never_share_a_pathgrid_formid():
    """Distinct cells must land on distinct quadrants, hence distinct ids.

    The FormID is keyed on the quadrant, so leaving points in the cell's own
    frame collapsed every cell onto the same quadrant and the same id.
    """
    ctx = MorrowindContext()
    points = [(100.0, 100.0, 8.0), (200.0, 200.0, 8.0)]
    first = pathgrid_records(
        [_pathgrid((-21, -25), points, [(0, 1)])], ctx)
    second = pathgrid_records([_pathgrid((7, 3), points, [(0, 1)])], ctx)

    assert first and second
    assert first[0][0] != second[0][0],         'two different Morrowind cells get two different pathgrid FormIDs'


def test_two_cells_reaching_one_quadrant_merge_into_one_record():
    """A quadrant holds ONE pathgrid however many cells reach into it.

    Morrowind tolerates a point lying outside its own cell, so two cells can
    both land points in one Oblivion quadrant. Emitting a record each gave
    them the same quadrant-keyed FormID -- 63 collisions over TR_Mainland --
    and a cell can hold only one navmesh anyway.
    """
    ctx = MorrowindContext()
    here = [(100.0, 100.0, 8.0), (200.0, 200.0, 8.0)]
    stray = [(-8092.0, -8092.0, 8.0), (-7992.0, -7992.0, 8.0)]
    records = pathgrid_records(
        [_pathgrid((0, 0), here, [(0, 1)]),
         _pathgrid((1, 1), stray, [(0, 1)])], ctx)

    quad = ctx.exterior_cell_id((0, 0))
    mine = [lines for _fid, lines in records
            if _value(lines, 'ParentCELL') == quad]
    assert len(mine) == 1, 'the quadrant gets exactly one pathgrid'
    assert _value(mine[0], 'DATA.PointCount') == '4',         'both cells contributed their points to it'
    assert len({fid for fid, _lines in records}) == len(records),         'no two records may share a FormID'
