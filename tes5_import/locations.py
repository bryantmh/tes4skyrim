"""Map-marker Locations (LCTN) — the record type that makes Skyrim's map work.

Oblivion has no Location records.  Its engine discovers a map marker with a
hardcoded proximity check against the marker REFR itself, so a marker is all
Oblivion needs.  Skyrim removed that: a map marker is revealed only when the
player discovers the *Location* the marker belongs to.  Convert the markers
alone and every one of them stays hidden forever, which is exactly what "I am
unable to discover any locations" looks like in game.

So for each converted map marker we synthesize the Location that Skyrim expects,
wiring up the three-way contract vanilla uses (verified against all 397 map
markers and 638 LCTN records in Skyrim.esm):

    REFR (the marker)          LCTN (the location)
      NAME = MapMarker STAT      MNAM = the marker REFR   <- reveal this marker
      XMRK/FNAM/FULL/TNAM        LCEC = worldspace + the exterior cells that,
      XLRT = MapMarkerRefType           when entered, discover the location
      persistent                 RNAM = discovery radius

Interior cells that belong to the location point back at it with XLCN, so
walking into the dungeon discovers it too — that is how vanilla reveals a cave
whose marker you never walked over.
"""

import struct
from collections import defaultdict

from .text_reader import get_float, get_formid, get_str
from .writer import (
    pack_float_subrecord,
    pack_formid_subrecord,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
)

# Exterior cells are 4096 game units square in both TES4 and TES5.
CELL_SIZE = 4096.0

# LCTN.RNAM — "World Location Radius".  The engine treats the marker as
# discovered once the player comes within this distance of it, which is what
# makes a marker findable by walking near it rather than only by entering its
# cell.  Vanilla's discoverable exterior markers cluster around 1000-2000 units;
# 2000 keeps Oblivion's landmarks findable from the road without letting a
# marker pop while the player is still a cell away.
DEFAULT_LOCATION_RADIUS = 2000.0

# A teleport door this close to a marker is taken to be that marker's entrance,
# so the interior it leads to gets XLCN'd to the marker's location.  Oblivion
# places dungeon markers essentially on top of their doors.
DOOR_LINK_RADIUS = 1500.0


def _grid(pos: float) -> int:
    """Exterior cell grid coordinate containing a world-space ordinate."""
    return int(pos // CELL_SIZE)


def _pack_lcec(world_fid: int, cells: list) -> bytes:
    """LCEC — 'Master Worldspace Cells': World(4) + [GridY(i16), GridX(i16)]...

    Entering any listed cell discovers the location.
    """
    data = struct.pack('<I', world_fid)
    for gy, gx in cells:
        data += struct.pack('<hh', gy, gx)
    return pack_subrecord('LCEC', data)


def _marker_cells(gx: int, gy: int) -> list:
    """Cells whose entry should discover a marker: its own, plus its neighbours.

    A marker sitting near a cell boundary would otherwise only be discovered
    from the one side.  Vanilla does the same thing by hand — its multi-cell
    LCECs are the marker's cell plus the ones it spills into.  Sorted so output
    is deterministic.
    """
    return sorted(
        (gy + dy, gx + dx)
        for dy in (-1, 0, 1)
        for dx in (-1, 0, 1)
    )


def build_marker_locations(by_type: dict, writer) -> dict:
    """Create one LCTN per map marker and return {cell_fid: lctn_fid}.

    The returned map is consumed by the CELL converter, which writes XLCN so
    that entering an interior discovers the location it belongs to.

    Must run before the CELL/WRLD groups are built, since those need the XLCN
    targets, and it allocates its FormIDs up front so output stays
    deterministic.
    """
    refrs = by_type.get('REFR', [])
    markers = [r for r in refrs if get_str(r, 'MapMarker') == '1']
    if not markers:
        return {}

    # Teleport doors, bucketed by worldspace, so each marker can find the
    # entrance it fronts and claim the interior behind it.
    doors_by_world = defaultdict(list)
    for rec in refrs:
        if not get_formid(rec, 'XTEL.Door'):
            continue
        world = get_formid(rec, 'ParentWRLD')
        if world:
            doors_by_world[world].append(rec)

    # Which cell does a teleport door land you in?  XTEL names the *destination
    # door*, and that door's parent cell is the interior being entered.
    cell_of_door = {}
    for rec in refrs:
        fid = get_formid(rec, 'FormID')
        if fid:
            cell_of_door[fid] = get_formid(rec, 'ParentCELL')

    cell_to_location = {}
    count = 0

    for rec in sorted(markers, key=lambda r: get_formid(r, 'FormID')):
        marker_fid = get_formid(rec, 'FormID')
        if not marker_fid:
            continue

        name = get_str(rec, 'MapMarker.FULL')
        lctn_fid = writer.alloc_formid()

        edid_base = ''.join(c for c in name if c.isalnum()) or f'{marker_fid:08X}'
        subs = pack_string_subrecord('EDID', f'TES4{edid_base}Location')

        world_fid = get_formid(rec, 'ParentWRLD')
        if world_fid:
            gx = _grid(get_float(rec, 'PosX'))
            gy = _grid(get_float(rec, 'PosY'))
            subs += _pack_lcec(world_fid, _marker_cells(gx, gy))

        if name:
            subs += pack_string_subrecord('FULL', name)

        # MNAM — "World Location Marker Ref".  This is the link the engine
        # follows to reveal the marker once the location is discovered.
        subs += pack_formid_subrecord('MNAM', marker_fid)
        subs += pack_float_subrecord('RNAM', DEFAULT_LOCATION_RADIUS)

        writer.add_record('LCTN', pack_record('LCTN', lctn_fid, 0, subs))
        count += 1

        # Claim the interior behind the nearest teleport door, so entering the
        # dungeon discovers it even if the player never crossed the marker.
        interior = _interior_for_marker(rec, doors_by_world, cell_of_door)
        if interior and interior not in cell_to_location:
            cell_to_location[interior] = lctn_fid

    print(f"  Created {count} LCTN map-marker locations "
          f"({len(cell_to_location)} interiors linked via XLCN)")
    return cell_to_location


def _interior_for_marker(rec: dict, doors_by_world: dict,
                         cell_of_door: dict) -> int:
    """Parent cell of the interior reached by the teleport door nearest a marker.

    Returns 0 when the marker fronts no door (open-air camps, Oblivion gates),
    in which case the marker is discovered purely by its LCEC cells.
    """
    world = get_formid(rec, 'ParentWRLD')
    doors = doors_by_world.get(world)
    if not doors:
        return 0

    mx = get_float(rec, 'PosX')
    my = get_float(rec, 'PosY')
    limit = DOOR_LINK_RADIUS ** 2

    best_fid = 0
    best_dist = limit
    for door in doors:
        dx = get_float(door, 'PosX') - mx
        dy = get_float(door, 'PosY') - my
        dist = dx * dx + dy * dy
        if dist < best_dist:
            best_dist = dist
            best_fid = get_formid(door, 'XTEL.Door')

    if not best_fid:
        return 0
    # The destination door sits in the interior cell we want.
    return cell_of_door.get(best_fid, 0)
