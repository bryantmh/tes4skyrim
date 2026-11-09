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
import struct

from tes4_export.morrowind_travel import destinations, travel_editor_id
from tes4_export.tes3_reader import get_string, get_subrecord

from .morrowind_placements import folder_tables

#: What the runtime reads: `npc id=name|interior|x|y|z|zRot degrees|marker;...`, the marker `Plugin|FormID` or empty.
TRAVEL_TABLE = 'NPC_travel.txt'

#: What GetPCCell reads outdoors, where Skyrim keeps no CELL name: `TES3 grid x|grid y=name`.
EXTERIOR_NAMES_TABLE = 'exterior_names.txt'

#: A TES3 exterior cell is this many units across.
_CELL_SIZE = 8192

#: What ChangeWeather and ModRegion read: `grid x|grid y=region id` outdoors, `cell=region id` in a quasi exterior.
CELL_REGIONS_TABLE = 'cell_regions.txt'

#: CELL DATA: flags, grid x, grid y; flag bit 0 marks an interior, bit 7 one that behaves as an exterior.
_CELL_DATA = '<Iii'
_INTERIOR = 0x1
_QUASI_EXTERIOR = 0x80


def take_place(out: dict, rec) -> None:
    """Fold one CELL or REGN into `out['exteriors']` `{(x, y): (name,
    region id)}`, `out['quasi_exteriors']` `{lower name: region id}` and
    `out['regions']` `{lower id: display name}`."""
    if rec.type == 'REGN':
        name = get_subrecord(rec, 'FNAM')
        out['regions'][rec.record_id.lower()] = (
            get_string(name) if name else rec.record_id)
        return
    data = get_subrecord(rec, 'DATA')
    if data is None or len(data.data) < struct.calcsize(_CELL_DATA):
        return
    flags, grid_x, grid_y = struct.unpack_from(_CELL_DATA, data.data)
    region = get_subrecord(rec, 'RGNN')
    region = get_string(region).lower() if region else ''
    if flags & _INTERIOR:
        if region and flags & _QUASI_EXTERIOR:
            out['quasi_exteriors'][rec.record_id.lower()] = region
            out['own']['quasi_exteriors'].add(rec.record_id.lower())
        return
    out['exteriors'][(grid_x, grid_y)] = (rec.record_id, region)
    out['own']['exteriors'].add((grid_x, grid_y))


def _grid_name(out: dict, grid: tuple) -> str:
    """The shown name of the exterior cell at a grid, or ''."""
    name, region = out['exteriors'].get(grid, ('', ''))
    return name or out['regions'].get(region, '')


def _exterior_name(out: dict, x: float, y: float) -> str:
    """The shown name of the exterior cell at a position, or ''."""
    return _grid_name(out, (math.floor(x / _CELL_SIZE), math.floor(y / _CELL_SIZE)))


def _own_grid_names(gathered: dict):
    """`((x, y), shown name)` for each exterior grid the plugin authors, in grid order."""
    return ((grid, _grid_name(gathered, grid))
            for grid in sorted(gathered.get('own', {}).get('exteriors', ())))


def exterior_name_lines(gathered: dict) -> list:
    """`x|y=name` for each named exterior grid the plugin authors."""
    return [f'{x}|{y}={name}' for (x, y), name in _own_grid_names(gathered) if name]


def shown_exterior_names(gathered: dict) -> set:
    """The names the plugin's own exteriors show: a cell's own, else its region's."""
    return {name for _grid, name in _own_grid_names(gathered) if name}


def cell_region_lines(gathered: dict) -> list:
    """`x|y=region` per own exterior grid and `cell=region` per own quasi exterior that names a region.

    See: docs/commentary/morrowind_runtime.md#region-weather
    """
    own = gathered.get('own', {})
    exteriors = gathered.get('exteriors', {})
    rows = [f'{x}|{y}={exteriors[(x, y)][1]}'
            for x, y in sorted(own.get('exteriors', ())) if exteriors[(x, y)][1]]
    quasi = gathered.get('quasi_exteriors', {})
    return rows + [f'{name}={quasi[name]}'
                   for name in sorted(own.get('quasi_exteriors', ()))]


def marker_index(folders: list) -> dict:
    """`{lower EditorID: 'Plugin|FormID'}` for every travel marker the
    `(folder, plugin)` exports hold, the nearest plugin's winning."""
    found = {}
    for folder, plugin in folders:
        for edid, formid in folder_tables(folder)['travel'].items():
            found.setdefault(edid, f'{plugin}|{formid}')
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
