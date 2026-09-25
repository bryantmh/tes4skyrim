"""FO3/FNV body part data (BPTD) as a TES5 record.

The 84-byte BPND is byte-compatible between the two games except for the
part type: FNV's full body-location enum against TES5's six-value enum. The
TES5 engine stores a BPTD's parts in a SIX-slot array indexed by that type
and keys every runtime limb table on it (measured in SkyrimSE.exe), so a
TES5 record can only carry the Torso and Head parts. Every FNV limb part,
with its severable flag and limb replacement model, goes to the sidecar the
SKSE plugin severs from.

FNV's `DefaultBodyPartData` is FormID 0x1D — the same record every vanilla
Skyrim race points its GNAM at — so it is written as an OVERRIDE of
Skyrim.esm's record with its bone names mapped onto the Skyrim skeleton.
See: docs/commentary/asset_convert_falloutnv.md#dismemberment
"""

import json
import os
import struct

from asset_convert.character.skyrim_overrides_falloutnv import bone_map_for
from asset_convert.havok.hkx_skeleton import BONE_RENAMES

from ..base.text_reader import get_formid, get_int, get_str, remap_formid
from ..base.writer import (pack_obnd, pack_record, pack_string_subrecord,
                      pack_subrecord)
from .common import prefix_path
from .world_falloutnv import is_fallout_source

#: Skyrim.esm's DefaultBodyPartData; FNV's is the same id (0x1C is the player's).
DEFAULT_BODY_PART_DATA = 0x1D
HUMAN_BODY_PART_DATA = frozenset({0x1C, DEFAULT_BODY_PART_DATA})

#: FNV wbBodyLocationEnum values -> their names (-1 is None).
FNV_PART_TYPES = {
    0: 'Torso', 1: 'Head1', 2: 'Head2', 3: 'LeftArm1', 4: 'LeftArm2',
    5: 'RightArm1', 6: 'RightArm2', 7: 'LeftLeg1', 8: 'LeftLeg2',
    9: 'LeftLeg3', 10: 'RightLeg1', 11: 'RightLeg2', 12: 'RightLeg3',
    13: 'Brain', 14: 'Weapon',
}

#: The two FNV part types a TES5 record can hold (its Torso and Head slots).
RECORD_PART_TYPES = (0, 1)

#: BPND flag bits (shared by both games).
BPND_SEVERABLE, BPND_EXPLODABLE = 0x01, 0x08

_MODT = bytes.fromhex('020000000000000000000000')

#: (offset, reference type) of every FormID inside BPND.
_BPND_FORMIDS = ((12, 'DEBR'), (16, 'EXPL'), (32, 'DEBR'), (36, 'EXPL'),
                 (68, 'IPDS'), (72, 'IPDS'))

#: Where the SKSE plugin reads a plugin's limb data, under the plugin output.
SIDECAR_DIR = os.path.join('SKSE', 'Plugins', 'FalloutRuntime')


def translate_bpnd(raw: bytes, converted_types) -> bytes:
    """FNV BPND -> TES5 BPND: FormIDs remapped or nulled, gore bits cleared.

    A referenced record type this import does not convert is nulled rather
    than left dangling. Severable/Explodable bits are cleared: the engine's
    own gore path is keyed on its six part types and must never run on our
    parts; the SKSE plugin reads the authored bits from the sidecar.
    """
    b = bytearray(raw[:84].ljust(84, b'\0'))
    b[4] &= ~(BPND_SEVERABLE | BPND_EXPLODABLE) & 0xFF
    for off, kind in _BPND_FORMIDS:
        fid = struct.unpack_from('<I', b, off)[0]
        struct.pack_into('<I', b, off,
                         remap_formid(fid) if fid and kind in converted_types
                         else 0)
    return bytes(b)


def part_node_name(name: str, humanoid: bool) -> str:
    """An FNV bone name as the converted skeleton spells it."""
    if not name:
        return name
    if humanoid:
        return bone_map_for({name: None}).get(name, name)
    return BONE_RENAMES.get(name, name)


def bptd_parts(rec: dict) -> list:
    """The exported parts of a BPTD as dicts (fields as exported)."""
    out = []
    for i in range(get_int(rec, 'PartCount', 0)):
        p = {k: get_str(rec, f'Part[{i}].{k}')
             for k in ('Name', 'Node', 'Target', 'IKStart',
                       'LimbReplacementModel', 'GoreTargetBone')}
        p['BPND'] = bytes.fromhex(get_str(rec, f'Part[{i}].BPND'))
        out.append(p)
    return out


def record_formid(rec: dict) -> int:
    """The converted FormID: Skyrim's own 0x1D for DefaultBodyPartData."""
    raw_fid = int(rec.get('FormID', '0'), 16)
    return (DEFAULT_BODY_PART_DATA if raw_fid == DEFAULT_BODY_PART_DATA
            else get_formid(rec, 'FormID'))


def convert_BPTD(rec: dict, writer=None, converted_types=None) -> bytes:
    """FNV BPTD -> TES5 BPTD (EDID MODL MODT, then per part BPTN BPNN BPNT
    BPNI BPND NAM1 NAM4 NAM5), Torso and Head parts only.

    `converted_types` defaults to the dispatch table; BPND refs to
    unconverted DEBR/EXPL/IPDS are nulled. NAM1 is written empty -- the
    SKSE plugin spawns the limb from the sidecar.

    Registry import is function-local, breaking the cycle equipment ->
    equipment_falloutnv -> this -> registry -> equipment.
    """
    if converted_types is None:
        from ..registry import IMPORT_DISPATCH
        converted_types = IMPORT_DISPATCH
    raw_fid = int(rec.get('FormID', '0'), 16)
    humanoid = raw_fid in HUMAN_BODY_PART_DATA
    subs = pack_string_subrecord('EDID', get_str(rec, 'EditorID')
                                 or f'TES4BodyPart{raw_fid:06X}')
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', prefix_path(model))
        subs += pack_subrecord('MODT', _MODT)
    written = set()
    for p in bptd_parts(rec):
        fnv_type = struct.unpack_from('<b', p['BPND'], 5)[0]
        if fnv_type not in RECORD_PART_TYPES or fnv_type in written:
            continue
        written.add(fnv_type)
        subs += pack_string_subrecord('BPTN', p['Name'] or p['Node'])
        subs += pack_string_subrecord('BPNN', part_node_name(p['Node'],
                                                             humanoid))
        subs += pack_string_subrecord('BPNT', part_node_name(p['Target'],
                                                             humanoid))
        subs += pack_string_subrecord('BPNI', part_node_name(p['IKStart'],
                                                             humanoid))
        subs += pack_subrecord('BPND', translate_bpnd(p['BPND'],
                                                      converted_types))
        subs += pack_string_subrecord('NAM1', '')
        subs += pack_string_subrecord('NAM4', part_node_name(
            p['GoreTargetBone'], humanoid))
        subs += pack_subrecord('NAM5', b'')
    return pack_record('BPTD', record_formid(rec), 0, subs)


def limb_model(p: dict) -> str:
    """A part's severed-limb model as the converted mesh path ('' if none)."""
    return (prefix_path(p['LimbReplacementModel'])
            if p['LimbReplacementModel'] else '')


def limb_models(records: list) -> list:
    """Every distinct limb replacement model the BPTDs name, sorted."""
    return sorted({limb_model(p) for rec in records for p in bptd_parts(rec)
                   if p['LimbReplacementModel']}, key=str.lower)


def limb_static(writer, model: str) -> tuple:
    """(FormID, MSTT bytes) of the loose limb the SKSE plugin drops.

    A movable static carries the gore mesh's own havok, so PlaceAtMe gives a
    limb that falls and settles like any other physics object; its id is
    derived from the authored model path.
    """
    fid = writer.derive_formid('BPTD_LIMB', model.lower())
    stem = os.path.splitext(os.path.basename(model))[0]
    subs = pack_string_subrecord('EDID', f'TES4Limb{stem}')
    subs += pack_obnd()
    subs += pack_string_subrecord('MODL', model)
    subs += pack_subrecord('MODT', _MODT)
    subs += pack_subrecord('DATA', b'\0')
    return fid, pack_record('MSTT', fid, 0, subs)


def source_file(fid: int, plugin_name: str) -> str:
    """The file a converted FormID belongs to: the master for index 0."""
    return 'Skyrim.esm' if (fid >> 24) == 0 else os.path.basename(plugin_name)


def bptd_sidecar(rec: dict, plugin_name: str, limb_forms: dict) -> dict:
    """The authored gore data the SKSE plugin severs with, for one BPTD."""
    raw_fid = int(rec.get('FormID', '0'), 16)
    humanoid = raw_fid in HUMAN_BODY_PART_DATA
    parts = []
    for p in bptd_parts(rec):
        b = p['BPND']
        fnv_type = struct.unpack_from('<b', b, 5)[0]
        parts.append({
            'name': p['Name'], 'type': fnv_type,
            'type_name': FNV_PART_TYPES.get(fnv_type, 'None'),
            'node': part_node_name(p['Node'], humanoid),
            'target': part_node_name(p['Target'], humanoid),
            'severable': bool(b[4] & BPND_SEVERABLE),
            'explodable': bool(b[4] & BPND_EXPLODABLE),
            'health_percent': b[6],
            'limb_model': limb_model(p),
            'limb_local': limb_forms.get(limb_model(p), 0) & 0xFFFFFF,
            'limb_scale': struct.unpack_from('<f', b, 80)[0],
            'gore_bone': part_node_name(p['GoreTargetBone'], humanoid),
            'gore_offset': list(struct.unpack_from('<6f', b, 44)),
        })
    fid = record_formid(rec)
    return {'formid': f'{fid:08X}', 'local': fid & 0xFFFFFF,
            'file': source_file(fid, plugin_name),
            'edid': get_str(rec, 'EditorID'), 'humanoid': humanoid,
            'parts': parts}


def write_sidecar(records: list, plugin_out_dir: str, plugin_name: str,
                  limb_forms: dict = None) -> str:
    """Write every BPTD's gore data for the SKSE plugin; the file path.

    Records carry their plugin-local FormID and owning file; the plugin
    resolves them through the engine's own load order at DataLoaded.
    `limb_forms` maps limb model -> the MSTT FormID limb_static minted.
    """
    stem = os.path.splitext(os.path.basename(plugin_name))[0]
    out_dir = os.path.join(plugin_out_dir, SIDECAR_DIR)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f'{stem}.bodyparts.json')
    side = [bptd_sidecar(r, plugin_name, limb_forms or {}) for r in records]
    data = {'version': 2, 'source': os.path.basename(plugin_name),
            'limb_file': os.path.basename(plugin_name),
            'bodyparts': {s['formid']: s for s in side}}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1)
    return path


def write_falloutnv_sidecars(by_type: dict, writer, output_path: str) -> None:
    """FO3/FNV limb and gun data the TES5 records cannot hold, for FalloutRuntime.

    Mints one MSTT per gore model so a severed limb has something to place.
    See: docs/commentary/asset_convert_falloutnv.md#dismemberment
    """
    out_dir = os.path.dirname(output_path)
    plugin = os.path.basename(output_path)
    if by_type.get('BPTD'):
        limb_forms = {}
        for model in limb_models(by_type['BPTD']):
            fid, rec_bytes = limb_static(writer, model)
            writer.add_record('MSTT', rec_bytes)
            limb_forms[model] = fid
        side = write_sidecar(by_type['BPTD'], out_dir, plugin, limb_forms)
        print(f"  Body part sidecar: {len(by_type['BPTD'])} records, "
              f"{len(limb_forms)} limb statics -> "
              f"{os.path.relpath(side, out_dir)}")
    if by_type.get('WEAP') and is_fallout_source():
        from .equipment_falloutnv import write_gun_sidecar
        side = write_gun_sidecar(by_type['WEAP'], out_dir, plugin)
        if side:
            print(f"  Gun sidecar -> {os.path.relpath(side, out_dir)}")
