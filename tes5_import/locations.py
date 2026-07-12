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

Locations are also how Skyrim *names* an exterior cell.  Not one of Skyrim.esm's
16,978 exterior cells carries a FULL — the name on a load door, and in the
location-discovered popup, is read off the cell's XLCN.  A cell with no XLCN
falls back to the engine's generic "Wilderness" string (or, in a child
worldspace, to that worldspace's own name).  Oblivion names exteriors the
opposite way: its cells have no names either, but its engine derives the label
from the worldspace, so the raw conversion inherits whatever junk sits in the
TES4 WRLD FULL — including Bethesda's shipped dev name "TestEndGame" on
AnvilCastleCourtyardWorld.

So every exterior cell gets an XLCN here: the marker location covering its grid
square when there is one, and otherwise a per-worldspace location standing in
for "the wilds of <worldspace>".
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

# Oblivion's root worldspace has no FULL of its own — the TES4 engine hardcoded
# the label, so there is nothing in the data to derive it from.  Every other
# worldspace name is read out of the plugin.  Matched on the low-24 FormID bits
# because get_formid() has already folded in the load-order offset by the time
# we see it (Tamriel comes through as 0x0100003C, not 0x3C).
TAMRIEL_WRLD = 0x0000003C
TAMRIEL_NAME = 'Cyrodiil'

# {world_fid: resolved player-facing name}, filled in as the worldspace
# locations are built.  The WRLD converter reads it so the worldspace and its
# location always agree on the name, and neither ships a dev placeholder.
WORLD_NAMES: dict = {}


def _is_dev_name(name: str) -> bool:
    """True for the placeholder names Bethesda left in the shipping data.

    AnvilCastleCourtyardWorld really is called "TestEndGame" in Oblivion.esm —
    the TES4 engine never showed a worldspace name on a load door, so nobody
    noticed.  Skyrim does show it, so these have to be caught.

    Matched on the *unspaced* CamelCase shape a dev name has ("TestEndGame",
    "TestDementiaRegionGen"), which is what keeps this from eating real names
    that merely begin with the word test — Dream World's "Test of Resolve" is a
    quest title, not a placeholder.
    """
    head = name.split()[0].lower() if name.split() else ''
    if head in ('test', 'zz'):  # "Test of Resolve" -> a real, spaced-out name
        return False
    lowered = name.lower()
    return lowered.startswith('test') or lowered.startswith('zz')


def _worldspace_name(rec: dict, marker_names: dict = None) -> str:
    """Player-facing name for a worldspace.

    Prefers the worldspace's own FULL, but falls back to the name of the map
    marker inside it when that FULL is missing or a dev placeholder.  A
    worldspace holding exactly one marker *is* that place — the Anvil castle
    courtyard contains only the "Castle Anvil" marker — so the marker names it,
    and we never have to hardcode a name that was already in the data.

    Returns '' when nothing usable is available (the unreachable test worlds),
    in which case the worldspace gets no location and no name.
    """
    full = get_str(rec, 'FULL')
    if full and not _is_dev_name(full):
        return full

    if (get_formid(rec, 'FormID') & 0x00FFFFFF) == TAMRIEL_WRLD:
        return TAMRIEL_NAME

    # Only an unambiguous marker can stand in for the worldspace name; a world
    # with several of them (a city, or Tamriel itself) is not named by any one.
    names = (marker_names or {}).get(get_formid(rec, 'FormID')) or []
    unique = sorted(set(names))
    if len(unique) == 1 and not _is_dev_name(unique[0]):
        return unique[0]
    return ''


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


def _marker_names_by_world(markers: list) -> dict:
    """{world_fid: [marker name, ...]} — lets a marker name its worldspace."""
    names = defaultdict(list)
    for rec in markers:
        world = get_formid(rec, 'ParentWRLD')
        name = get_str(rec, 'MapMarker.FULL')
        if world and name:
            names[world].append(name)
    return names


def _build_worldspace_locations(by_type: dict, marker_names: dict,
                                writer) -> dict:
    """One LCTN per named worldspace; returns {world_fid: lctn_fid}.

    This is what an exterior cell falls back to when no map marker covers it.
    Without it the engine has nothing to read a name from and calls every cell
    in Tamriel "Wilderness".  These locations carry no MNAM — they name a region
    of the map, they do not reveal a marker — which is exactly how vanilla's
    hold locations (Whiterun Hold, the Pale, …) are built.
    """
    world_to_location = {}

    for rec in sorted(by_type.get('WRLD', []),
                      key=lambda r: get_formid(r, 'FormID')):
        world_fid = get_formid(rec, 'FormID')
        name = _worldspace_name(rec, marker_names)
        if not world_fid or not name:
            continue

        lctn_fid = writer.alloc_formid()
        edid_base = ''.join(c for c in name if c.isalnum())
        subs = pack_string_subrecord('EDID', f'TES4{edid_base}Location')
        subs += pack_string_subrecord('FULL', name)

        writer.add_record('LCTN', pack_record('LCTN', lctn_fid, 0, subs))
        world_to_location[world_fid] = lctn_fid
        WORLD_NAMES[world_fid] = name

    return world_to_location


def build_marker_locations(by_type: dict, writer) -> tuple:
    """Build the Locations and return (cell_to_location, grid_to_location).

    ``cell_to_location`` maps an interior CELL FormID to the Location it belongs
    to; ``grid_to_location`` maps an exterior ``(world_fid, gx, gy)`` cell square
    to the Location that names it.  Both are consumed by the CELL converter,
    which turns them into XLCN — the subrecord that both discovers a location
    and gives an exterior cell the name shown on a load door.

    Must run before the CELL/WRLD groups are built, since those need the XLCN
    targets, and it allocates its FormIDs up front so output stays
    deterministic.
    """
    refrs = by_type.get('REFR', [])
    markers = [r for r in refrs if get_str(r, 'MapMarker') == '1']

    # Worldspace locations come first so that markers can parent to them, and so
    # that FormID allocation stays in a stable, reproducible order.  They are
    # named from the markers, which is how a worldspace whose FULL is a dev name
    # ("TestEndGame") still ends up called what it should be ("Castle Anvil").
    world_to_location = _build_worldspace_locations(
        by_type, _marker_names_by_world(markers), writer)

    # Every exterior cell square owned by a worldspace location, so cells with no
    # marker of their own still get named.  Marker locations overwrite their own
    # squares below, since the specific place beats the surrounding wilds.
    grid_to_location = {}

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
        cells = []
        if world_fid:
            gx = _grid(get_float(rec, 'PosX'))
            gy = _grid(get_float(rec, 'PosY'))
            cells = _marker_cells(gx, gy)
            subs += _pack_lcec(world_fid, cells)

        if name:
            subs += pack_string_subrecord('FULL', name)

        # PNAM — parent location.  Vanilla nests every place inside the hold
        # that contains it, which is what lets "Whiterun Hold" name the ground
        # between its landmarks.  Per xEdit's LCTN def, PNAM precedes MNAM/RNAM.
        parent = world_to_location.get(world_fid)
        if parent:
            subs += pack_formid_subrecord('PNAM', parent)

        # MNAM — "World Location Marker Ref".  This is the link the engine
        # follows to reveal the marker once the location is discovered.
        subs += pack_formid_subrecord('MNAM', marker_fid)
        subs += pack_float_subrecord('RNAM', DEFAULT_LOCATION_RADIUS)

        writer.add_record('LCTN', pack_record('LCTN', lctn_fid, 0, subs))
        count += 1

        # The marker names the cells it sits in, beating the worldspace default.
        for gy, gx in cells:
            grid_to_location[(world_fid, gx, gy)] = lctn_fid

        # Claim the interior behind the nearest teleport door, so entering the
        # dungeon discovers it even if the player never crossed the marker.
        interior = _interior_for_marker(rec, doors_by_world, cell_of_door)
        if interior and interior not in cell_to_location:
            cell_to_location[interior] = lctn_fid

    print(f"  Created {len(world_to_location)} LCTN worldspace locations, "
          f"{count} LCTN map-marker locations "
          f"({len(cell_to_location)} interiors linked via XLCN)")
    return cell_to_location, grid_to_location, world_to_location


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
