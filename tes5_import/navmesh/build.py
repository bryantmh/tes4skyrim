"""Navmesh build entry point.

`build_navmesh` runs the generator `core.navmesh_options.navmesh_generator`
names: the pathgrid CORRIDOR ribbons (default; corridor.py,
docs/commentary/tes5_import_navmesh.md), or the experimental LATTICE
(lattice/, docs/plans/navmesh_lattice.md), which meshes every floor the
pathgrid can walk to from collision columns.
Cross-cell edge links and NAVI are downstream of either.

Returns (verts, tris) in world space.  The caller (pgrd_to_navm) owns the
NVNM/NAVM binary packing, validated byte-exact against Skyrim.esm — do not
change it.
"""

import logging
import math

from core.navmesh_options import CORRIDOR, navmesh_generator

from . import corridor
from . import world
from .lattice.build import build_lattice
from .obstacle_bridge import bridge_obstacles, cell_ground

_log = logging.getLogger(__name__)


def teleport_door_positions(refr_recs):
    """(x, y, z, rot_z, True, 0.0) of every teleport-door REFR (XTEL) in the cell.

    The FALLBACK door list, used only when the caller supplies none.
    See: docs/commentary/tes5_import_navmesh.md#teleport-doors-are-barriers-and-anchors
    """
    out = []
    for refr in refr_recs or ():
        if refr.get('XTEL.Door'):
            try:
                x, y = float(refr.get('PosX')), float(refr.get('PosY'))
                z = float(refr.get('PosZ'))
                rz = float(refr.get('RotZ') or 0.0)
            except (TypeError, ValueError):
                continue
            # float() happily parses NaN and 8.9e17, so range-check as well --
            # see navmesh/world.py _MAX_PLACEMENT for why these exist.
            if not all(math.isfinite(v) for v in (x, y, z, rz)):
                continue
            if max(abs(x), abs(y), abs(z)) > world.MAX_PLACEMENT:
                continue
            # Width 0: no measured doorway span for a bare-XTEL fallback door;
            # corridor_doors falls back to its constant half-width.
            out.append((x, y, z, rz, True, 0.0))
    return out


def build_navmesh(refr_recs, base_model_by_fid, get_collision, nodes, edges,
                  land_rec=None, origin_x=0.0, origin_y=0.0, budget=None,
                  doors=None, ledges_out=None, door_bases=None, lone=(), water_z=None,
                  obstacles=()):
    """Build a navmesh for one cell.  Returns (verts3d, tris) or ([], []).

    doors: [(x, y, z, rot_z, is_teleport, width), ...] door REFRs (teleport AND
    interior).  When None, teleport doors are recovered from XTEL alone.
    door_bases: low-24 DOOR base FormIDs, whose panel collision is EXCLUDED.
    lone: edgeless nodes that still get a disc.  water_z: the cell's water
    height, or None.  obstacles: (placement, model key) pairs `edges` is
    bridged across.  Drop-down (Ledge Up/Down) pairs go to `ledges_out`.
    See: docs/commentary/tes5_import_navmesh.md#ledges-are-returned-out-of-band
    """
    if not nodes:
        return [], []
    if doors is None:
        doors = teleport_door_positions(refr_recs)
    if navmesh_generator() == CORRIDOR:
        verts, tris, ledges = corridor.build_corridors(
            refr_recs, base_model_by_fid, get_collision, nodes, edges,
            land_rec=land_rec, origin_x=origin_x, origin_y=origin_y, doors=doors,
            door_bases=door_bases, lone=lone, water_z=water_z, obstacles=obstacles)
    else:
        bridge_obstacles(nodes, edges, obstacles, lambda: cell_ground(
            refr_recs, base_model_by_fid, get_collision, land_rec,
            origin_x, origin_y, door_bases, water_z), get_collision)
        verts, tris, ledges = build_lattice(
            refr_recs, base_model_by_fid, get_collision, nodes, edges,
            land_rec=land_rec, origin_x=origin_x, origin_y=origin_y, doors=doors,
            door_bases=door_bases, water_z=water_z)
    if ledges_out is not None:
        ledges_out.extend(ledges)
    return verts, tris
