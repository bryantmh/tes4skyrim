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

from core.worldspace_names import converted_worldspace_name

from .cell_family import exterior_family_cells
from .text_reader import get_float, get_formid, get_int, get_str
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

    Order: a renamed worldspace's table name (`core/worldspace_names.py`), its
    own FULL, then its sole map marker's name when FULL is missing or a dev
    placeholder (the Anvil courtyard holds only "Castle Anvil").

    Returns '' when nothing usable is available (the unreachable test worlds),
    in which case the worldspace gets no location and no name.
    """
    full = converted_worldspace_name(get_str(rec, 'EditorID'),
                                     get_str(rec, 'FULL'))
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
                                writer, used_edids: set) -> dict:
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

        lctn_fid = writer.derive_formid('LCTN_WORLD', world_fid)
        edid_base = ''.join(c for c in name if c.isalnum())
        edid = f'TES4{edid_base}Location'
        # A worldspace named after its sole marker ("The Fringe") would
        # otherwise collide with that marker's own location EditorID.
        if edid in used_edids:
            edid = f'TES4{edid_base}World{world_fid & 0xFFFFFF:06X}Location'
        used_edids.add(edid)
        subs = pack_string_subrecord('EDID', edid)
        subs += pack_string_subrecord('FULL', name)

        writer.add_record('LCTN', pack_record('LCTN', lctn_fid, 0, subs))
        world_to_location[world_fid] = lctn_fid
        WORLD_NAMES[world_fid] = name

    return world_to_location


def _bucket_teleport_doors(refrs: list) -> tuple:
    """(doors_by_world, cell_of_door) for every teleport door in `refrs`.

    `cell_of_door` covers EVERY reference, not only the teleporting ones: XTEL
    names the destination DOOR, and that door's parent cell is the interior
    being entered.

    See: docs/commentary/tes5_import_world.md#teleport-doors-by-worldspace
    """
    doors_by_world = defaultdict(list)
    for rec in refrs:
        if not get_formid(rec, 'XTEL.Door'):
            continue
        world = get_formid(rec, 'ParentWRLD')
        if world:
            doors_by_world[world].append(rec)

    cell_of_door = {}
    for rec in refrs:
        fid = get_formid(rec, 'FormID')
        if fid:
            cell_of_door[fid] = get_formid(rec, 'ParentCELL')
    return doors_by_world, cell_of_door


def _propagate_nested_interiors(refrs: list, cell_of_door: dict,
                                interior_cells: set,
                                cell_to_location: dict) -> None:
    """Give every interior-only room the location of the interior it opens off.

    Builds the interior->interior door graph and propagates to a fixed point,
    MUTATING `cell_to_location`. First writer wins, so the marker and entrance
    links already established are never overwritten.

    See: docs/commentary/tes5_import_world.md#nested-interiors-inherit-location
    """
    interior_links = defaultdict(set)
    for rec in refrs:
        dest_door = get_formid(rec, 'XTEL.Door')
        if not dest_door or get_formid(rec, 'ParentWRLD'):
            continue
        src = get_formid(rec, 'ParentCELL')
        dest = cell_of_door.get(dest_door, 0)
        if src and dest in interior_cells and src != dest:
            interior_links[src].add(dest)

    changed = True
    while changed:
        changed = False
        for src in sorted(interior_links):
            loc = cell_to_location.get(src)
            if not loc:
                continue
            for dest in sorted(interior_links[src]):
                if dest not in cell_to_location:
                    cell_to_location[dest] = loc
                    changed = True


def _resolve_cell_ownership(markers: list) -> dict:
    """{marker FormID -> [(gy, gx), ...]} with EXCLUSIVE cell ownership.

    Two passes over markers sorted by FormID: a marker claims its own square
    first, then the surrounding ring fills only squares still unowned, so no
    exterior square is ever listed by two locations.

    See: docs/commentary/tes5_import_world.md#exclusive-lcec-cell-ownership
    """
    cell_owner = {}
    marker_cells = {}
    ordered = [r for r in sorted(markers, key=lambda r: get_formid(r, 'FormID'))
               if get_formid(r, 'FormID')]
    for pass_ring in (False, True):
        for rec in ordered:
            world_fid = get_formid(rec, 'ParentWRLD')
            if not world_fid:
                continue
            marker_fid = get_formid(rec, 'FormID')
            gx = _grid(get_float(rec, 'PosX'))
            gy = _grid(get_float(rec, 'PosY'))
            squares = (_marker_cells(gx, gy) if pass_ring else [(gy, gx)])
            for cgy, cgx in squares:
                key = (world_fid, cgx, cgy)
                if key not in cell_owner:
                    cell_owner[key] = marker_fid
                    marker_cells.setdefault(marker_fid, []).append((cgy, cgx))
    return marker_cells


class _LocationBuild:
    """The maps one build_marker_locations run fills, shared by its phases."""

    def __init__(self, by_type: dict, writer):
        """Index markers, interiors and doors; write the worldspace Locations."""
        refrs = by_type.get('REFR', [])
        self.writer = writer
        self.refrs = refrs
        self.markers = [r for r in refrs if get_str(r, 'MapMarker') == '1']
        self.interior_cells = {get_formid(rec, 'FormID')
                               for rec in by_type.get('CELL', [])
                               if get_int(rec, 'DATA.Flags') & 1}
        self.used_edids = set()
        self.world_to_location = _build_worldspace_locations(
            by_type, _marker_names_by_world(self.markers), writer,
            self.used_edids)
        self.lctn_meta = {lctn: (WORLD_NAMES.get(w, ''), 0)
                          for w, lctn in self.world_to_location.items()}
        self.grid_to_location = {}
        self.cell_to_location = {}
        self.doors_by_world, self.cell_of_door = _bucket_teleport_doors(refrs)
        self.family = exterior_family_cells()


def build_marker_locations(by_type: dict, writer) -> tuple:
    """Build the Locations; return (cell_to_location, grid_to_location, world_to_location).

    Interior CELL FormID -> Location, and exterior ``(world_fid, gx, gy)``
    square -> Location; the CELL converter writes both as XLCN (discovery and
    the load-door name).  Must run before the CELL/WRLD groups are built.
    """
    b = _LocationBuild(by_type, writer)
    count = _write_marker_locations(b)
    marker_linked = len(b.cell_to_location)
    _link_door_interiors(b)
    door_linked = len(b.cell_to_location) - marker_linked
    _propagate_nested_interiors(b.refrs, b.cell_of_door, b.interior_cells,
                                b.cell_to_location)
    _build_family_locations(b)
    nested_linked = len(b.cell_to_location) - marker_linked - door_linked
    print(f"  Created {len(b.world_to_location)} LCTN worldspace locations, "
          f"{count} LCTN map-marker locations "
          f"({marker_linked} interiors linked to a marker, "
          f"{door_linked} via their entrance door, "
          f"{nested_linked} nested interiors, "
          f"{len(b.family)} GetInCell-family exterior cells)")
    return b.cell_to_location, b.grid_to_location, b.world_to_location


def _marker_location_subrecords(b: _LocationBuild, rec: dict, marker_fid: int,
                                cells: list) -> bytes:
    """EDID, LCEC, FULL, PNAM, MNAM, RNAM of one map marker's Location.

    Marker names repeat ("A Gate to Oblivion" x50), so a duplicate EditorID
    is suffixed with the marker id.  LCEC lists only the squares this marker
    won, minus those a GetInCell-family cell Location claims.  PNAM nests the
    place in its worldspace's location, as vanilla nests places in holds; MNAM
    is the marker the engine reveals once the location is discovered.
    """
    name = get_str(rec, 'MapMarker.FULL')
    edid_base = ''.join(c for c in name if c.isalnum()) or f'{marker_fid:08X}'
    edid = f'TES4{edid_base}Location'
    if edid in b.used_edids:
        edid = f'TES4{edid_base}{marker_fid & 0xFFFFFF:06X}Location'
    b.used_edids.add(edid)
    subs = pack_string_subrecord('EDID', edid)
    world_fid = get_formid(rec, 'ParentWRLD')
    lcec = [c for c in cells if (world_fid, c[1], c[0]) not in b.family]
    if lcec:
        subs += _pack_lcec(world_fid, lcec)
    if name:
        subs += pack_string_subrecord('FULL', name)
    parent = b.world_to_location.get(world_fid)
    if parent:
        subs += pack_formid_subrecord('PNAM', parent)
    subs += pack_formid_subrecord('MNAM', marker_fid)
    return subs + pack_float_subrecord('RNAM', DEFAULT_LOCATION_RADIUS)


def _write_marker_locations(b: _LocationBuild) -> int:
    """One Location per map marker; returns how many were written.

    Each names the squares it OWNS (the exclusive set from
    _resolve_cell_ownership, so a cell's XLCN and its location's LCEC agree)
    and claims the interior behind the nearest teleport door, so entering the
    dungeon discovers it even if the player never crossed the marker.
    """
    marker_cells = _resolve_cell_ownership(b.markers)
    count = 0
    for rec in sorted(b.markers, key=lambda r: get_formid(r, 'FormID')):
        marker_fid = get_formid(rec, 'FormID')
        if not marker_fid:
            continue
        world_fid = get_formid(rec, 'ParentWRLD')
        cells = sorted(marker_cells.get(marker_fid, ())) if world_fid else []
        lctn_fid = b.writer.derive_formid('LCTN_MARKER', marker_fid)
        b.writer.add_record('LCTN', pack_record(
            'LCTN', lctn_fid, 0,
            _marker_location_subrecords(b, rec, marker_fid, cells)))
        count += 1
        b.lctn_meta[lctn_fid] = (get_str(rec, 'MapMarker.FULL'), marker_fid)
        for gy, gx in cells:
            b.grid_to_location[(world_fid, gx, gy)] = lctn_fid
        interior = _interior_for_marker(rec, b.doors_by_world, b.cell_of_door)
        if interior in b.interior_cells and interior not in b.cell_to_location:
            b.cell_to_location[interior] = lctn_fid
    return count


def _link_door_interiors(b: _LocationBuild) -> None:
    """Give every other teleport-reachable interior its entrance door's Location.

    Without an XLCN Skyrim cannot place a quest marker for a target inside
    (the marker system resolves an interior ref's map position through its
    cell's Location).  The door's grid square Location wins, then its
    worldspace location; first writer wins, so marker links stay.
    """
    for rec in sorted(b.refrs, key=lambda r: get_formid(r, 'FormID')):
        dest_door = get_formid(rec, 'XTEL.Door')
        interior = b.cell_of_door.get(dest_door, 0) if dest_door else 0
        world_fid = get_formid(rec, 'ParentWRLD')
        if (interior not in b.interior_cells or interior in b.cell_to_location
                or not world_fid):
            continue
        gx = _grid(get_float(rec, 'PosX'))
        gy = _grid(get_float(rec, 'PosY'))
        loc = (b.grid_to_location.get((world_fid, gx, gy))
               or b.world_to_location.get(world_fid))
        if loc:
            b.cell_to_location[interior] = loc


def _build_family_locations(b: _LocationBuild) -> None:
    """One Location per exterior cell of a GetInCell family, in the grid map.

    It is a child of the square's previous Location with that Location's name
    and marker (vanilla shares MNAM between a place and its child 24 times),
    owns the square's LCEC exclusively, and carries the family keywords
    `LocationHasKeyword` tests.  Runs after the door pass, so an interior
    keeps its entrance's ordinary Location.

    See: docs/commentary/tes5_import_conditions.md#getincell-prefix-family
    """
    for (world, gx, gy), (source, edid, kws) in sorted(b.family.items()):
        parent = (b.grid_to_location.get((world, gx, gy))
                  or b.world_to_location.get(world))
        name, marker = b.lctn_meta.get(parent, ('', 0))
        loc_edid = f'TES4{edid}CellLocation'
        if loc_edid in b.used_edids:
            loc_edid = f'TES4{edid}{source}CellLocation'
        b.used_edids.add(loc_edid)
        subs = pack_string_subrecord('EDID', loc_edid)
        subs += _pack_lcec(world, [(gy, gx)])
        if name:
            subs += pack_string_subrecord('FULL', name)
        subs += pack_subrecord('KSIZ', struct.pack('<I', len(kws)))
        subs += pack_subrecord('KWDA', struct.pack(f'<{len(kws)}I', *kws))
        if parent:
            subs += pack_formid_subrecord('PNAM', parent)
        if marker:
            subs += pack_formid_subrecord('MNAM', marker)
            subs += pack_float_subrecord('RNAM', DEFAULT_LOCATION_RADIUS)
        fid = b.writer.derive_formid('LCTN_CELL_FAMILY', source)
        b.writer.add_record('LCTN', pack_record('LCTN', fid, 0, subs))
        b.grid_to_location[(world, gx, gy)] = fid


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
