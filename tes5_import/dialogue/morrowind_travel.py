"""
The TES3 travel service: where each NPC can send the player.

A destination with no DNAM is outside, and is shown under the name of the
exterior cell its position falls in -- the cell's own name, else its region's
-- which is what OpenMW's TravelWindow prints. Each destination also names the
persistent marker the export minted for it, which is what the player is moved
to.

See: docs/commentary/morrowind_runtime.md#travel
"""

import math
import os
import struct

from tes4_export.morrowind_travel import destinations, travel_editor_id
from tes4_export.tes3_reader import get_string, get_subrecord

#: What the runtime reads: `npc id=name|interior|x|y|z|zRot degrees|marker;...`, the marker `Plugin|FormID` or empty.
TRAVEL_TABLE = 'NPC_travel.txt'

#: The export holding the markers.
_MARKER_EXPORT = 'REFR.txt'

#: A TES3 exterior cell is this many units across.
_CELL_SIZE = 8192

#: CELL DATA: flags, grid x, grid y; bit 0 of the flags marks an interior.
_CELL_DATA = '<Iii'
_INTERIOR = 0x1


def take_place(out: dict, rec) -> None:
    """Fold one CELL or REGN into `out['exteriors']` `{(x, y): (name,
    region id)}` and `out['regions']` `{lower id: display name}`."""
    if rec.type == 'REGN':
        name = get_subrecord(rec, 'FNAM')
        out['regions'][rec.record_id.lower()] = (
            get_string(name) if name else rec.record_id)
        return
    data = get_subrecord(rec, 'DATA')
    if data is None or len(data.data) < struct.calcsize(_CELL_DATA):
        return
    flags, grid_x, grid_y = struct.unpack_from(_CELL_DATA, data.data)
    if flags & _INTERIOR:
        return
    region = get_subrecord(rec, 'RGNN')
    out['exteriors'][(grid_x, grid_y)] = (
        rec.record_id, get_string(region).lower() if region else '')


def _exterior_name(out: dict, x: float, y: float) -> str:
    """The shown name of the exterior cell at a position, or ''."""
    grid = (math.floor(x / _CELL_SIZE), math.floor(y / _CELL_SIZE))
    name, region = out['exteriors'].get(grid, ('', ''))
    return name or out['regions'].get(region, '')


def marker_index(folders: list, read_records) -> dict:
    """`{lower EditorID: 'Plugin|FormID'}` for every travel marker the
    `(folder, plugin)` exports hold. `read_records` is the sidecar's export
    reader, passed in because that module imports this one."""
    found = {}
    for folder, plugin in folders:
        for rec in read_records(os.path.join(folder, _MARKER_EXPORT),
                                ('FormID', 'EditorID')):
            edid = rec.get('EditorID', '')
            if edid.startswith('TES3Travel') and rec.get('FormID'):
                found.setdefault(edid.lower(), f"{plugin}|{rec['FormID']}")
    return found


def _travel_line(rec, out: dict, markers: dict) -> str:
    """One NPC_'s table line, or '' when it offers no travel. A destination
    whose name cannot be resolved is dropped, as OpenMW drops it."""
    parts = []
    for index, ((x, y, z, _rx, _ry, rz), cell) in enumerate(destinations(rec)):
        name = cell or _exterior_name(out, x, y)
        if not name:
            continue
        marker = markers.get(travel_editor_id(rec.record_id, index).lower(), '')
        parts.append(f"{name.replace('|', ' ').replace(';', ' ')}|"
                     f"{1 if cell else 0}|{x:g}|{y:g}|{z:g}|"
                     f"{math.degrees(rz):g}|{marker.replace('|', '/')}")
    return f'{rec.record_id}=' + ';'.join(parts) if parts else ''


def travel_lines(gathered: dict, markers: dict) -> dict:
    """`{lower npc id: table line}` for every NPC_ `gather` read that offers
    travel."""
    lines = {key: _travel_line(rec, gathered, markers)
             for key, rec in gathered['npcs'].items()}
    return {key: line for key, line in lines.items() if line}
