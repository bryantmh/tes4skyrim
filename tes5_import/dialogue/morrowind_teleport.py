"""Where Divine and Almsivi Intervention send the player.

MorrowindRuntime picks the nearest marker when the effect lands, as OpenMW's
`World::getClosestMarker` does, from the tables staged here: every Divine and
Temple marker the loaded plugins place, the places a restored Intervention's
replaced script sent the player, Skyrim's temples, each child worldspace's
parent, and where each interior opens onto the world, which the crime pass
already walked through the doors.

See: docs/commentary/morrowind_runtime.md#teleport-effects
"""

import math
import os
import struct

from core.plugin_masters import masters_from_export_header
from tes4_export.record_types.morrowind_magic import effect_editor_id

from .morrowind_placements import MARKER_KINDS, export_records, folder_tables
from ..base.tes5_reader import read_record
from ..record_types.magic_morrowind import (MW_ACROBATICS, MW_ACROBATICS_EFFECTS,
                                            MW_ATTRIBUTE_EFFECTS, MW_JUMP,
                                            MW_RUNTIME_EFFECTS, TES4_ACROBATICS,
                                            TES4_ATTRIBUTE_EFFECTS)
from ..record_types.magic_variants import av_variant_editor_id, copy_editor_ids, known_effects

#: `plugin|MGEF=TES3 effect index`, every effect record the runtime acts on (the name predates non-teleports).
TELEPORTS_TABLE = 'teleports_formid.txt'

#: `name=plugin|FormID`, the conversion-owned records runtime effects act through (the Sanctuary faction).
EFFECT_FORMS_TABLE = 'effects_formid.txt'

#: `plugin|marker=kind|place plugin|place|x|y|z|zRot degrees`, one row per marker.
MARKERS_TABLE = 'markers_formid.txt'

#: `cell plugin|cell=world plugin|world|x|y`, one row per interior with a way out.
ANCHORS_TABLE = 'anchors_formid.txt'

#: `plugin|child worldspace=plugin|parent worldspace`, one row per child.
WORLDS_TABLE = 'worlds_formid.txt'

#: Every TES3 effect index listed whole: the runtime-carried ones, and Jump.
_LISTED_EFFECTS = MW_RUNTIME_EFFECTS | {MW_JUMP}

_WORLD_KEYS = ('FormID', 'WNAM.Parent')

#: The exports a restored Intervention names its replaced script's destinations in.
_TARGET_EXPORTS = ('SPEL.txt', 'ENCH.txt', 'ALCH.txt')
_TARGET_KEYS = ('InterventionKind', 'InterventionTargets')

#: The placed-reference types a destination may be, and GRUP types labelled by world or cell.
_PLACED = (b'REFR', b'ACHR')
_WORLD_CHILDREN = 1
_CELL_CHILDREN = 6


def _raw(rec: dict, key: str) -> int:
    """An export FormID field as an int, 0 when absent or malformed."""
    try:
        return int(rec.get(key) or '0', 16)
    except ValueError:
        return 0


def _owner(raw: int, masters: list, plugin: str) -> str:
    """The plugin a FormID's index byte names, from the exporting plugin's header."""
    index = raw >> 24
    return masters[index] if index < len(masters) else plugin


def _folder_markers(folder: str, plugin: str, own: int) -> list:
    """This export's own marker rows; a marker in a cell it does not export is skipped."""
    masters = masters_from_export_header(folder)
    tables = folder_tables(folder)
    cells = {_raw(rec, 'FormID'): _raw(rec, 'ParentWRLD') for rec in tables['cells']}
    rows = []
    for rec in tables['markers']:
        kind = MARKER_KINDS[_raw(rec, 'NAME')]
        cell = _raw(rec, 'ParentCELL')
        if _raw(rec, 'FormID') >> 24 != own or cell not in cells:
            continue
        place = cells[cell] or cell
        spot = '|'.join(f"{float(rec.get(axis) or 0):g}"
                        for axis in ('PosX', 'PosY', 'PosZ'))
        rows.append(f"{plugin}|{rec['FormID']}={kind}|"
                    f"{_owner(place, masters, plugin)}|{place:08X}|{spot}|"
                    f"{math.degrees(float(rec.get('RotZ') or 0)):g}")
    return rows


def _copy_values() -> dict:
    """{EditorID, lowercase: table value} of every record a runtime effect is copied to.

    A runtime effect's delivery and Ability clones are `index`; an attribute
    effect's per-attribute variant and its clones, a Morrowind one's or a TES4
    one's (`TES4FOATStrength`), are `index:attribute`; an Acrobatics skill
    effect's variant and its clones are `index:s20`.
    """
    values = {name.lower(): str(index) for index in _LISTED_EFFECTS
              for name in copy_editor_ids(effect_editor_id(index))}
    for index in MW_ACROBATICS_EFFECTS:
        edid = av_variant_editor_id(effect_editor_id(index), TES4_ACROBATICS)
        values.update({name.lower(): f'{index}:s{MW_ACROBATICS}'
                       for name in [edid] + copy_editor_ids(edid)})
    codes = [(effect_editor_id(index), index) for index in MW_ATTRIBUTE_EFFECTS]
    for code, index in codes + list(TES4_ATTRIBUTE_EFFECTS.items()):
        for attribute in range(8):
            edid = av_variant_editor_id(code, attribute)
            values.update({name.lower(): f'{index}:{attribute}'
                           for name in [edid] + copy_editor_ids(edid)})
    return values


def copy_rows(plugin: str, own: int) -> list:
    """`plugin|FormID=value` for every variant and clone of a runtime effect this
    plugin's conversion made, whose index byte is `own`."""
    copies = _copy_values()
    return [f'{plugin}|{fid:08X}={copies[edid.lower()]}'
            for fid, edid in sorted(known_effects().items())
            if fid >> 24 == own and edid.lower() in copies]


def teleport_lines(effect_lines: list, plugin: str, own: int) -> list:
    """Every effect record a runtime-carried effect lands as.

    The chain's own `MW_RUNTIME_EFFECTS` come from `effect_lines` (MGEF.txt's
    `index=plugin|FormID|name` rows); the variants and clones this plugin's
    conversion made are added (`copy_rows`).
    See: docs/commentary/morrowind_runtime.md#adding-a-runtime-effect
    """
    rows = []
    for line in effect_lines:
        index, _, value = line.partition('=')
        if int(index) in _LISTED_EFFECTS:
            rows.append(f"{'|'.join(value.split('|')[:2])}={index}")
    return rows + copy_rows(plugin, own)


def marker_lines(dirs: list) -> list:
    """Every Divine and Temple marker the `(folder, plugin, own)` exports place.

    A master that stages no Morrowind sidecar of its own (Morrowind_ob) still
    has its markers found, because each dependent stages its whole chain.
    """
    rows = []
    for folder, plugin, own in dirs:
        rows.extend(_folder_markers(folder, plugin, own))
    return rows


def _placed_spot(master_index, fid: int) -> tuple:
    """(place FormID, `x|y|z|zRot degrees`) of a converted master's placed reference, or (0, '')."""
    rec = read_record(master_index.record(fid), 0)[0]
    data = rec.sub(b'DATA') if rec else None
    labels = dict(master_index.group_path(fid))
    label = labels.get(_WORLD_CHILDREN) or labels.get(_CELL_CHILDREN)
    if not data or len(data) < 24 or not label:
        return 0, ''
    x, y, z, _rx, _ry, rz = struct.unpack_from('<6f', data)
    return struct.unpack('<I', label)[0], f'{x:g}|{y:g}|{z:g}|{math.degrees(rz):g}'


def target_lines(folder: str, master_index, masters: list) -> list:
    """Marker rows for where each restored Intervention's replaced script sent
    the player: its `InterventionTargets`, resolved in the converted masters."""
    rows = []
    for name in _TARGET_EXPORTS:
        for rec in export_records(os.path.join(folder, name), _TARGET_KEYS):
            for edid in filter(None, (rec.get('InterventionTargets') or '').split(',')):
                fid = next((f for sig in _PLACED for f in master_index.find_all_by_edid(sig, edid)), 0)
                place, spot = _placed_spot(master_index, fid) if fid else (0, '')
                if place:
                    rows.append(f"{masters[fid >> 24]}|{fid:08X}={rec['InterventionKind']}|"
                                f"{masters[place >> 24]}|{place:08X}|{spot}")
    return rows


def world_lines(dirs: list) -> list:
    """`child=parent` for every child worldspace the `(folder, plugin, own)` exports define."""
    rows = []
    for folder, plugin, _own in dirs:
        masters = masters_from_export_header(folder)
        for rec in export_records(os.path.join(folder, 'WRLD.txt'), _WORLD_KEYS):
            child, parent = _raw(rec, 'FormID'), _raw(rec, 'WNAM.Parent')
            if child and parent:
                rows.append(f'{_owner(child, masters, plugin)}|{child:08X}='
                            f'{_owner(parent, masters, plugin)}|{parent:08X}')
    return rows
