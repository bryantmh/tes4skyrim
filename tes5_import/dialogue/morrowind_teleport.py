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

from ..base.tes5_reader import read_record
from ..record_types.magic_morrowind import MW_RUNTIME_EFFECTS
from ..record_types.magic_variants import delivery_editor_ids, known_effects

#: `plugin|MGEF=TES3 effect index`, every effect record the runtime acts on (the name predates non-teleports).
TELEPORTS_TABLE = 'teleports_formid.txt'

#: `plugin|marker=kind|place plugin|place|x|y|z|zRot degrees`, one row per marker.
MARKERS_TABLE = 'markers_formid.txt'

#: `cell plugin|cell=world plugin|world|x|y`, one row per interior with a way out.
ANCHORS_TABLE = 'anchors_formid.txt'

#: `plugin|child worldspace=plugin|parent worldspace`, one row per child.
WORLDS_TABLE = 'worlds_formid.txt'

#: TES4 marker base -> the kind the runtime keys on; the TES3 export writes the same ids.
_MARKER_KINDS = {0x00000005: 'divine', 0x00000006: 'temple'}

_REF_KEYS = ('FormID', 'NAME', 'ParentCELL', 'PosX', 'PosY', 'PosZ', 'RotZ')
_CELL_KEYS = ('FormID', 'ParentWRLD')
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


def _folder_markers(folder: str, plugin: str, own: int, read_records) -> list:
    """This export's own marker rows; a marker in a cell it does not export is skipped."""
    masters = masters_from_export_header(folder)
    cells = {_raw(rec, 'FormID'): _raw(rec, 'ParentWRLD')
             for rec in read_records(os.path.join(folder, 'CELL.txt'), _CELL_KEYS)}
    rows = []
    for rec in read_records(os.path.join(folder, 'REFR.txt'), _REF_KEYS):
        kind = _MARKER_KINDS.get(_raw(rec, 'NAME'))
        cell = _raw(rec, 'ParentCELL')
        if not kind or _raw(rec, 'FormID') >> 24 != own or cell not in cells:
            continue
        place = cells[cell] or cell
        spot = '|'.join(f"{float(rec.get(axis) or 0):g}"
                        for axis in ('PosX', 'PosY', 'PosZ'))
        rows.append(f"{plugin}|{rec['FormID']}={kind}|"
                    f"{_owner(place, masters, plugin)}|{place:08X}|{spot}|"
                    f"{math.degrees(float(rec.get('RotZ') or 0)):g}")
    return rows


def teleport_lines(effect_lines: list, plugin: str, own: int) -> list:
    """Every effect record a runtime-carried effect (`MW_RUNTIME_EFFECTS`) lands as.

    The chain's own TES3 effects come from `effect_lines` (MGEF.txt's
    `index=plugin|FormID|name` rows); the delivery clones this plugin's
    conversion made of them, whose index byte is `own`, are added.
    See: docs/commentary/morrowind_runtime.md#adding-a-runtime-effect
    """
    rows = []
    for line in effect_lines:
        index, _, value = line.partition('=')
        if int(index) in MW_RUNTIME_EFFECTS:
            rows.append(f"{'|'.join(value.split('|')[:2])}={index}")
    clones = {name.lower(): index for index in MW_RUNTIME_EFFECTS
              for name in delivery_editor_ids(effect_editor_id(index))[1:]}
    rows += [f'{plugin}|{fid:08X}={clones[edid.lower()]}'
             for fid, edid in sorted(known_effects().items())
             if fid >> 24 == own and edid.lower() in clones]
    return rows


def marker_lines(dirs: list, read_records) -> list:
    """Every Divine and Temple marker the `(folder, plugin, own)` exports place.

    A master that stages no Morrowind sidecar of its own (Morrowind_ob) still
    has its markers found, because each dependent stages its whole chain.
    `read_records` is the sidecar's export reader, which imports this module.
    """
    rows = []
    for folder, plugin, own in dirs:
        rows.extend(_folder_markers(folder, plugin, own, read_records))
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


def target_lines(folder: str, read_records, master_index, masters: list) -> list:
    """Marker rows for where each restored Intervention's replaced script sent
    the player: its `InterventionTargets`, resolved in the converted masters."""
    rows = []
    for name in _TARGET_EXPORTS:
        for rec in read_records(os.path.join(folder, name), _TARGET_KEYS):
            for edid in filter(None, (rec.get('InterventionTargets') or '').split(',')):
                fid = next((f for sig in _PLACED for f in master_index.find_all_by_edid(sig, edid)), 0)
                place, spot = _placed_spot(master_index, fid) if fid else (0, '')
                if place:
                    rows.append(f"{masters[fid >> 24]}|{fid:08X}={rec['InterventionKind']}|"
                                f"{masters[place >> 24]}|{place:08X}|{spot}")
    return rows


def world_lines(dirs: list, read_records) -> list:
    """`child=parent` for every child worldspace the `(folder, plugin, own)` exports define."""
    rows = []
    for folder, plugin, _own in dirs:
        masters = masters_from_export_header(folder)
        for rec in read_records(os.path.join(folder, 'WRLD.txt'), _WORLD_KEYS):
            child, parent = _raw(rec, 'FormID'), _raw(rec, 'WNAM.Parent')
            if child and parent:
                rows.append(f'{_owner(child, masters, plugin)}|{child:08X}='
                            f'{_owner(parent, masters, plugin)}|{parent:08X}')
    return rows
