"""
Where Divine Intervention lands in Skyrim's own worlds, and which worldspace
each child worldspace belongs to.

A temple is a location Skyrim.esm tags `LocTypeTemple`. Its interior cells
name that location, and each of their doors leads out to an authored arrival
point: that is the landing spot. A walled city is a child worldspace sharing
its parent's coordinates, so the runtime searches a city and its parent as one.

See: docs/commentary/morrowind_runtime.md#divine-intervention-in-any-world
"""

import functools
import math
import os
import struct

from asset_convert.sources.skyrim_assets import find_skyrim_data

from ..base.tes5_reader import GRP_TOP, read_record, walk

#: The base game, whose temples every TES3 conversion loads.
SKYRIM = 'Skyrim.esm'

#: The keyword Skyrim.esm tags each temple location with.
_TEMPLE_KEYWORD = 'LocTypeTemple'

#: XTEL: destination door, then its arrival position and rotation.
_XTEL = struct.Struct('<I3f3f')


def _top_is(name: bytes):
    """A walk `prune` skipping the top-level group of `name`."""
    return lambda group, _stack: group.type == GRP_TOP and group.label == name


def _temple_cells(raw) -> set:
    """Interior cells whose location carries the temple keyword."""
    keyword, locations, cells = None, {}, {}
    for rec, stack in walk(raw, b'KYWD', b'LCTN', b'CELL', prune=_top_is(b'WRLD')):
        if rec.sig == b'KYWD' and rec.string(b'EDID') == _TEMPLE_KEYWORD:
            keyword = rec.form_id
        elif rec.sig == b'LCTN':
            kwda = rec.sub(b'KWDA') or b''
            locations[rec.form_id] = set(struct.unpack(f'<{len(kwda) // 4}I', kwda))
        elif rec.sig == b'CELL' and stack.top == b'CELL' and rec.sub(b'XLCN'):
            cells[rec.form_id] = struct.unpack('<I', rec.sub(b'XLCN')[:4])[0]
    return {cell for cell, loc in cells.items() if keyword in locations.get(loc, ())}


def _exits(raw, cells: set) -> dict:
    """{destination door: (x, y, z, z rotation in degrees)} for each door out of `cells`."""
    out = {}
    for ref, stack in walk(raw, b'REFR', bodies=(), prune=_top_is(b'WRLD')):
        if stack.cell not in cells:
            continue
        xtel = read_record(raw, ref.offset)[0].sub(b'XTEL')
        if xtel and len(xtel) >= _XTEL.size:
            door, x, y, z, _rx, _ry, rz = _XTEL.unpack_from(xtel)
            out[door] = (x, y, z, math.degrees(rz))
    return out


def _worlds(raw, doors) -> tuple:
    """({door: its worldspace}, {child worldspace: parent}) over the world tree."""
    placed, parents = {}, {}
    for rec, stack in walk(raw, b'REFR', b'WRLD', bodies=(b'WRLD',), prune=_top_is(b'CELL')):
        if rec.sig == b'WRLD' and rec.sub(b'WNAM'):
            parents[rec.form_id] = struct.unpack('<I', rec.sub(b'WNAM')[:4])[0]
        elif rec.form_id in doors and stack.worldspace:
            placed[rec.form_id] = stack.worldspace
    return placed, parents


@functools.lru_cache(maxsize=1)
def skyrim_rows() -> tuple:
    """(marker rows, world rows) for Skyrim.esm; both empty when it cannot be found."""
    data = find_skyrim_data()
    path = os.path.join(data, SKYRIM) if data else ''
    if not os.path.isfile(path):
        return (), ()
    with open(path, 'rb') as handle:
        raw = handle.read()
    exits = _exits(raw, _temple_cells(raw))
    placed, parents = _worlds(raw, exits)
    markers = tuple(f'{SKYRIM}|{door:08X}=divine|{SKYRIM}|{placed[door]:08X}|'
                    + '|'.join(f'{v:g}' for v in spot)
                    for door, spot in sorted(exits.items()) if door in placed)
    worlds = tuple(f'{SKYRIM}|{child:08X}={SKYRIM}|{parent:08X}'
                   for child, parent in sorted(parents.items()))
    return markers, worlds
