"""Shared Oblivion -> Skyrim furniture marker conversion logic.

Oblivion BSFurnitureMarker positions are ENTRY POINTS: where the NPC stands
on the floor (~50-95 units away from the furniture) before the sit/sleep
animation carries them onto it.  One marker exists per approach direction
(a single chair has up to 4).  Skyrim BSFurnitureMarkerNode positions are
the actual SIT/SLEEP spots (hip position), one per physical seat.

This module is the single source of truth for deriving seats from entries.
It is used by TWO consumers that MUST agree on seat clustering and order:

  - asset_convert.nif_converter writes the BSFurnitureMarkerNode positions
    into the converted NIF (one per seat, in cluster order).
  - tes5_import.record_types.items.convert_FURN writes the FURN record's
    MNAM active-marker bitmask and FNPR entry-point structs.  MNAM bit i
    enables NIF position i, so both sides must produce the same seat list.
    (TES4 MNAM bit i enabled ENTRY i in the Oblivion NIF; each seat here
    records which TES4 entry indices feed it so per-record entry
    restrictions like SEChair01F/R/L survive the conversion.)

Data-verified relations (100% consistent across all 48 marker-bearing
Oblivion.esm furniture NIFs, plus the marker files in dungeons/architecture):
  - position_ref 1-10 = sleep entries, 11-19 = sit entries
  - orientation (milliradians) is the approach walk direction (0 = +Y)
  - occupant facing (Skyrim heading; for sleep = head-to-feet direction)
    = orientation + offset by ref:
      left side {1, 3, 11}: -pi/2      right side {2, 12}: +pi/2
      behind occupant / beyond head {4, 13}: 0
      in front {14}: +pi  (NPC approaches facing the seat, turns, sits)
    (ref 3 = mat side entry, ref 4 = mat head-end crawl entry -- verified
     against sleepingmat01's pillow bump, calibrated on Skyrim bedroll01)
  - entries stand on the floor: mesh-space floor z = entry z
  - Skyrim marker z above floor: 34.0 for sit (vanilla chairs/benches),
    37.09 for sleep (vanilla beds; all 24 Oblivion bed mattress surfaces
    lie 36.5-42 above their entry z, so this matches them too)

Seat recovery:
  - SIT entries stand a fixed distance from their seat: 51.5 units for
    side entries, 55.0 for front/behind (measured spread across all
    chairs/benches/stools: 51.3-51.8 and 54.5-55.9).  Walking that far
    along the approach direction lands on the seat.  A bench's side entry
    is 51.5 from the END seat, so it merges with the correct cluster, and
    curved benches (anviltreebenchseat01) get seats on the arc rather
    than at the geometry centre.
  - SLEEP entry distances vary per bed (67-106) but always point across
    the hip line, so the geometry centre projected onto the approach ray
    recovers the hip position.
Candidates are then clustered: a chair's 3-4 entries converge on one
seat; a bench's front/behind entry pairs form one cluster per physical
seat (matching vanilla commonbench01's 3 positions).

ORIGIN SHIFT (the floating-sit fix, verified in-game 2026-07):
The Skyrim engine anchors the seated actor's root to the furniture
REFERENCE's Z (measured: seated getpos z == REFR z) and the sit/sleep
animation supplies the fixed ~34-unit hip rise; the marker's Z does NOT
set the actor height.  Every vanilla furniture mesh has its origin at the
mesh BOTTOM (floor plane), so root = floor and the hip lands on the seat.
Oblivion furniture origins are at mid-height, which floated actors by
exactly the origin-to-floor distance (~16 on stools/benches = 1 game
foot, ~34 on chairs).  Fix: re-origin converted furniture to the vanilla
convention — the NIF converter wraps the model in an inner NiNode
translated by `origin_shift` (= -floor_z = -min entry z, entries stand on
the floor) and shifts the marker offsets the same way, while the importer
subtracts the same shift from every placed reference of every base record
using the model (FURN and STAT share these meshes).  World-space visuals
are unchanged; the REFR now sits at the floor like vanilla.
"""

import math

REF_HEADING = {
    1: -math.pi / 2, 2: math.pi / 2, 3: -math.pi / 2, 4: 0.0,  # bed (sleep)
    11: -math.pi / 2, 12: math.pi / 2, 13: 0.0, 14: math.pi,   # chair (sit)
}
SIT_SIDE_DIST = 51.5    # entry-to-seat travel, side sit entries (11/12)
SIT_FRONT_DIST = 55.0   # entry-to-seat travel, front/behind sit entries (13/14)
SIT_HEIGHT = 34.0       # vanilla commonchair01 marker z (floor-relative)
SLEEP_HEIGHT = 37.0931  # vanilla commonbed01 marker z (floor-relative)
CLUSTER_RADIUS = 20.0   # bench seats are ~43 apart; same-seat entries land within ~2

# Entry-direction flag bits.  Same encoding in the NIF's FurnitureEntryPoints
# bitfield and the FURN record's FNPR/NAM0 U16 flags.
ENTRY_FRONT = 0x01
ENTRY_BEHIND = 0x02
ENTRY_RIGHT = 0x04
ENTRY_LEFT = 0x08


def extract_entries(marker_blocks):
    """Flatten Oblivion BSFurnitureMarker blocks into entry dicts.

    'index' is the running position index across all blocks -- the index
    TES4 FURN MNAM bits refer to.
    """
    entries = []
    for ed in marker_blocks:
        for pi in range(ed.num_positions):
            fp = ed.positions[pi]
            theta = fp.orientation / 1000.0  # milliradians -> radians
            ref = fp.position_ref_1
            entries.append({
                'index': len(entries),
                'p': (fp.offset.x, fp.offset.y, fp.offset.z),
                'd': (math.sin(theta), math.cos(theta)),  # approach direction
                'heading': (theta + REF_HEADING.get(ref, math.pi)) % (2 * math.pi),
                'sleep': 1 <= ref <= 10,
                'ref': ref,
            })
    return entries


def geometry_center_xy(root):
    """World-space XY centre of the geometry bounding box under a PyFFI root
    node (all local transforms applied, including the root's own)."""
    import numpy as np
    lo = [np.inf, np.inf]
    hi = [-np.inf, -np.inf]

    def walk(block, M):
        if block is None:
            return
        if hasattr(block, 'translation') and hasattr(block.translation, 'x'):
            r = block.rotation
            L = np.eye(4)
            L[0, :3] = [r.m_11, r.m_12, r.m_13]
            L[1, :3] = [r.m_21, r.m_22, r.m_23]
            L[2, :3] = [r.m_31, r.m_32, r.m_33]
            L[:3, :3] *= block.scale
            L[3, :3] = [block.translation.x, block.translation.y, block.translation.z]
            M = L @ M  # NIF row-vector convention: child-local applied first
        d = getattr(block, 'data', None)
        if d is not None and hasattr(d, 'vertices') and getattr(d, 'num_vertices', 0) > 0:
            verts = np.array([[v.x, v.y, v.z] for v in d.vertices])
            world = verts @ M[:3, :3] + M[3, :3]
            for ax in range(2):
                lo[ax] = min(lo[ax], world[:, ax].min())
                hi[ax] = max(hi[ax], world[:, ax].max())
        if hasattr(block, 'children'):
            for c in block.children:
                walk(c, M)

    walk(root, np.eye(4))
    if not np.isfinite(lo).all():
        return 0.0, 0.0
    return (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0


def _entry_flag(entry, seat_x, seat_y, heading):
    """Which side of the seat this entry point lies on, relative to the
    occupant's facing direction."""
    vx, vy = entry['p'][0] - seat_x, entry['p'][1] - seat_y
    norm = math.hypot(vx, vy)
    if norm < 1e-3:
        return ENTRY_FRONT
    vx, vy = vx / norm, vy / norm
    fwd = (math.sin(heading), math.cos(heading))
    right = (fwd[1], -fwd[0])
    along = vx * fwd[0] + vy * fwd[1]
    if along > 0.5:
        return ENTRY_FRONT
    if along < -0.5:
        return ENTRY_BEHIND
    if vx * right[0] + vy * right[1] > 0:
        return ENTRY_RIGHT
    return ENTRY_LEFT


def cluster_seats(entries, center_fn):
    """Convert entry points into seats.

    center_fn: zero-arg callable returning the geometry (cx, cy) -- only
    invoked if a sleep entry is present.

    Returns a list of seat dicts, in a deterministic order both the NIF
    converter and the FURN importer reproduce:
      {'x','y','z','heading','sleep','entry_flags',
       'members': [(tes4_entry_index, entry_flag_bit), ...]}
    """
    if not entries:
        return []

    center = None
    for e in entries:
        if e['sleep']:
            # Hip position: geometry centre projected onto the approach ray
            if center is None:
                center = center_fn()
            t = max(0.0, (center[0] - e['p'][0]) * e['d'][0] +
                    (center[1] - e['p'][1]) * e['d'][1])
        else:
            # Seat: fixed travel distance along the approach direction
            t = SIT_SIDE_DIST if e['ref'] in (11, 12) else SIT_FRONT_DIST
        e['seat'] = (e['p'][0] + t * e['d'][0], e['p'][1] + t * e['d'][1])

    clusters = []
    for e in entries:
        for cluster in clusters:
            sx = sum(m['seat'][0] for m in cluster) / len(cluster)
            sy = sum(m['seat'][1] for m in cluster) / len(cluster)
            if math.hypot(e['seat'][0] - sx, e['seat'][1] - sy) < CLUSTER_RADIUS:
                cluster.append(e)
                break
        else:
            clusters.append([e])

    seats = []
    for cluster in clusters:
        sx = sum(m['seat'][0] for m in cluster) / len(cluster)
        sy = sum(m['seat'][1] for m in cluster) / len(cluster)
        sleep = any(m['sleep'] for m in cluster)
        floor_z = min(m['p'][2] for m in cluster)
        # Circular mean of the entry-derived headings (they agree in practice);
        # atan2 already yields (-pi, pi] like vanilla marker headings
        heading = math.atan2(sum(math.sin(m['heading']) for m in cluster),
                             sum(math.cos(m['heading']) for m in cluster))
        members = [(m['index'], _entry_flag(m, sx, sy, heading)) for m in cluster]
        flags = 0
        for _idx, f in members:
            flags |= f
        seats.append({
            'x': sx,
            'y': sy,
            'z': floor_z + (SLEEP_HEIGHT if sleep else SIT_HEIGHT),
            'heading': heading,
            'sleep': sleep,
            'entry_flags': flags,
            'members': members,
        })
    return seats


def origin_shift(entries):
    """Model-space z translation that moves the furniture's floor plane to
    z=0 (the vanilla origin convention).  Entries stand on the floor, so
    the floor plane = the lowest entry z."""
    if not entries:
        return 0.0
    return -min(e['p'][2] for e in entries)


def furniture_model_info(nif_path):
    """Parse an Oblivion NIF and return its furniture conversion data:

      {'seats': [...see cluster_seats; z already in re-origined coords...],
       'origin_shift': float}

    Returns {'seats': [], 'origin_shift': 0.0} when the NIF has no
    furniture markers; raises on read errors.
    """
    import time
    if not hasattr(time, 'clock'):
        time.clock = time.perf_counter  # PyFFI 2.2.3 uses the removed time.clock
    from pyffi.formats.nif import NifFormat

    data = NifFormat.Data()
    with open(nif_path, 'rb') as fh:
        data.inspect(fh)
        data.read(fh)

    marker_blocks = []
    roots = list(data.roots)
    for root in roots:
        for ed in getattr(root, 'extra_data_list', []) or []:
            if (isinstance(ed, NifFormat.BSFurnitureMarker)
                    and not isinstance(ed, NifFormat.BSFurnitureMarkerNode)):
                marker_blocks.append(ed)
    entries = extract_entries(marker_blocks)
    if not entries:
        return {'seats': [], 'origin_shift': 0.0}
    shift = origin_shift(entries)
    seats = cluster_seats(entries, lambda: geometry_center_xy(roots[0]))
    for s in seats:
        s['z'] += shift
    return {'seats': seats, 'origin_shift': shift}


def seats_from_nif(nif_path):
    """Back-compat helper: seat list only (z in re-origined coords)."""
    return furniture_model_info(nif_path)['seats']


def _has_marker_header(path):
    """True if the NIF's header block-type table names BSFurnitureMarker."""
    try:
        with open(path, 'rb') as fh:
            return b'BSFurnitureMarker' in fh.read(8192)
    except OSError:
        return False


def scan_marker_nifs(meshes_dir):
    """Return the set of relative paths (lowercase, forward slashes) of all
    NIFs under meshes_dir containing a BSFurnitureMarker block.

    Cheap per file: block type names are plaintext in the NIF header, which
    sits in the first few KB — no PyFFI parse needed. But there are ~10k NIFs
    to probe, so the 8 KB header reads run across a thread pool (file I/O
    releases the GIL; processes would only add spawn cost here).
    """
    import os
    from concurrent.futures import ThreadPoolExecutor

    paths = []
    for root, _dirs, files in os.walk(meshes_dir):
        for fname in files:
            if fname.lower().endswith('.nif'):
                paths.append(os.path.join(root, fname))

    found = set()
    if not paths:
        return found
    with ThreadPoolExecutor(max_workers=min(32, max(4, len(paths) // 64))) as ex:
        for path, has_marker in zip(paths, ex.map(_has_marker_header, paths)):
            if has_marker:
                rel = os.path.relpath(path, meshes_dir)
                found.add(rel.lower().replace('\\', '/'))
    return found


def furniture_model_info_job(args):
    """(key, info, error) for one NIF — module-level so it is picklable for
    ProcessPoolExecutor (PyFFI parsing is CPU-bound; processes scale it)."""
    key, nif_path = args
    try:
        return key, furniture_model_info(nif_path), None
    except Exception as exc:  # noqa: BLE001 — caller reports and continues
        return key, None, f'{type(exc).__name__}: {exc}'
