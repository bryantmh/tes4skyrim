"""Shared loader for the audit-index navmesh tools (render/sweep/compare).

Every diagnostic that regenerates a cell's navmesh in-process needs the same
four things: the collision cache, the door-center cache, the audit index
(`export/<plugin>/audit_index3.pkl`, built by tools/navmesh/audit.py), and a
way to turn a cell EditorID into the exact argument tuple `build_navmesh`
receives from the real pipeline.  Keeping that in ONE place is what stops a
diagnostic from quietly disagreeing with production about, say, whether door
bases are excluded from blocking collision — a disagreement that makes every
number the tool prints a lie.

    from tools.navmesh.index import NavIndex
    idx = NavIndex('export/Oblivion.esm')
    cell = idx.cell('ImperialDungeon01')
    verts, tris = cell.build()
"""

import contextlib
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from asset_convert.collision import collision_extract as ce
from tes5_import.navmesh import build
from tes5_import.navmesh.from_pgrd import (
    _cell_graph, collect_doors, load_door_centroids,
)
from tes5_import.base.text_reader import parse_export_file
from tes5_import.record_types.items import load_furniture_models
from tools.navmesh.audit import build_index
from tes5_import.overrides.nested import (
    export_master_names, export_root, master_export_dir,
)
from output_layout import assets_for

DEFAULT_EXPORT = 'export/Oblivion.esm'


def master_export_dirs_of(export):
    """Each TES4 master's export directory, in _HEADER.txt order."""
    root = export_root(export)
    return [master_export_dir(root, n) for n in export_master_names(export)]


def load_origin_shifts(export, quiet=True):
    """Build the furniture origin-shift table the pipeline builds.

    Without it a diagnostic gathers collision at the RAW PosZ while the real
    pipeline lowers those refs, so the tool shows furniture floating by up to
    61 units and disagrees with the navmesh it is meant to explain.

    See: docs/commentary/asset_convert_nif.md#furniture-shift-third-consumer
    """
    by_type = {}
    for sig in ('FURN', 'STAT'):
        path = os.path.join(export, sig + '.txt')
        by_type[sig] = parse_export_file(path) if os.path.isfile(path) else []
    if quiet:
        with open(os.devnull, 'w') as null:
            with contextlib.redirect_stdout(null):
                return load_furniture_models(os.path.join(export, 'meshes'),
                                             by_type)
    return load_furniture_models(os.path.join(export, 'meshes'), by_type)


class CellCtx(object):
    """One cell, ready to regenerate."""

    def __init__(self, index, rec):
        self.index = index
        self.rec = rec
        self.name = rec.get('EditorID') or ''
        self.fid = (rec.get('FormID') or '').upper()
        pg = index.pgrd_by_cell.get(self.fid)
        graph = (_cell_graph(pg, rec) if pg is not None
                 else (None, None, 0.0, 0.0, False))
        nodes, edges, self.origin_x, self.origin_y, self.exterior = graph
        self.nodes, self.edges = nodes or [], edges or []
        self.refrs = index.refr_by_cell.get(self.fid, [])
        self.doors = collect_doors(self.refrs, index.door_fids)
        self.land = index.land_by_cell.get(self.fid) if self.exterior else None

    @property
    def has_pathgrid(self):
        return bool(self.nodes)

    def build(self, ledges_out=None):
        """Regenerate this cell's navmesh exactly as the pipeline would.

        `ledges_out` collects `(upper_tri, lower_tri, drop)` drop-down links,
        which production returns out-of-band so `(verts, tris)` stays intact.
        """
        verts, tris = build.build_navmesh(
            self.refrs, self.index.base_model, ce.get_collision,
            self.nodes, self.edges, land_rec=self.land,
            origin_x=self.origin_x, origin_y=self.origin_y,
            doors=[(x, y, z, r, tp, w)
                   for (x, y, z, r, _f, tp, w) in self.doors],
            door_bases=set(self.index.door_fids.keys()),
            ledges_out=ledges_out)
        return verts, [tuple(int(i) for i in tri[:3]) for tri in tris]

    def collision(self):
        """(walkable, blocking) placed collision triangles for this cell.

        The blocking set is what makes a render readable: the walls are the
        reason a corridor stops where it does.
        """
        from tes5_import.navmesh import world
        return world.gather_cell_geometry(
            self.refrs, self.index.base_model, ce.get_collision,
            land_rec=self.land,
            origin_x=self.origin_x, origin_y=self.origin_y,
            skip_bases=set(self.index.door_fids.keys()))

    def collision_sources(self):
        """[(model path, walkable tris, blocking tris)] per placed REFR.

        The renderer labels each triangle with the mesh it came from, so a
        surface that should not be there can be named instead of guessed at.
        """
        from tes5_import.navmesh import world
        skip = set(self.index.door_fids.keys())
        out = []
        for refr in self.refrs:
            name = refr.get('NAME')
            try:
                base_low = int(name, 16) & 0x00FFFFFF if name else None
            except ValueError:
                continue
            if base_low is None or base_low in skip:
                continue
            w, b = world._placed_soup(refr, self.index.base_model,
                                      ce.get_collision)
            if (w is None or not len(w)) and (b is None or not len(b)):
                continue
            out.append((self.index.base_model.get(base_low) or '?', w, b))
        return out

    def walked_samples(self, step=16.0):
        """Yield (x, y, z) points along every pathgrid edge.

        The pathgrid is the authored ground truth: a point here with no navmesh
        under it is always a generation failure, never a false positive.
        """
        for (a, b) in self.edges:
            pa, pb = self.nodes[a], self.nodes[b]
            n = max(2, int(math.dist(pa[:2], pb[:2]) / step) + 1)
            for i in range(n + 1):
                f = i / n
                yield (pa[0] + (pb[0] - pa[0]) * f,
                       pa[1] + (pb[1] - pa[1]) * f,
                       pa[2] + (pb[2] - pa[2]) * f)


class NavIndex(object):
    #: Export whose tables are currently armed in the shared module globals.
    _armed = None

    def __init__(self, export=DEFAULT_EXPORT, quiet=True):
        self.export = export
        self._quiet = quiet
        self.arm()
        (self.base_model, self.refr_by_cell, self.pgrd_by_cell,
         self.land_by_cell, self.door_fids, self.cells) = build_index(export)
        self._by_name = {}
        for c in self.cells:
            eid = (c.get('EditorID') or '').lower()
            if eid:
                self._by_name.setdefault(eid, c)
        self._by_fid = {(c.get('FormID') or '').upper(): c for c in self.cells}

    def arm(self):
        """Point the shared collision/door globals at THIS export's tables.

        `ce.load_collision` and `load_door_centroids` write module globals, so
        two NavIndex objects in one process share one table and the last one
        built wins -- every cell of the other export then finds no collision.

        See: docs/commentary/tes5_import_navmesh.md#navindex-arms-shared-tables
        """
        key = os.path.normcase(os.path.normpath(self.export))
        if NavIndex._armed == key:
            return
        ce.load_collision(self.collision_caches(), quiet=self._quiet)
        load_door_centroids(
            os.path.join(str(assets_for(self.export)),
                         'door_centers_cache.json'),
            quiet=self._quiet)
        load_origin_shifts(self.export, quiet=self._quiet)
        NavIndex._armed = key

    def collision_caches(self):
        """Every collision cache this export needs, MASTERS FIRST.

        A child plugin caches only the meshes it ships, so loading its own
        cache alone leaves every master-owned static uncarved.

        See: docs/commentary/tes5_import_navmesh.md#cellview-master-owned-cells
        """
        out = []
        for d in master_export_dirs_of(self.export) + [self.export]:
            path = os.path.join(str(assets_for(d)), 'collision_cache.bin')
            if path not in out:
                out.append(path)
        return out

    def cell(self, name_or_fid):
        """Look up by EditorID (case-insensitive) or by FormID hex."""
        self.arm()
        rec = self._by_name.get(str(name_or_fid).lower())
        if rec is None:
            rec = self._by_fid.get(str(name_or_fid).upper().lstrip('0X').rjust(8, '0'))
        if rec is None:
            rec = self._by_fid.get(str(name_or_fid).upper())
        if rec is None:
            return None
        return CellCtx(self, rec)

    def cell_of_ref(self, refid):
        """Find the cell containing a placed reference (FormID hex).

        Lets a bug report phrased as "the area around 1a01fc1e is mangled" be
        turned straight into a cell + a bounding box, with no manual hunting.
        """
        key = str(refid).upper().lstrip('0X').rjust(8, '0')
        for fid, refrs in self.refr_by_cell.items():
            for r in refrs:
                if (r.get('FormID') or '').upper().lstrip('0X').rjust(8, '0') == key:
                    rec = self._by_fid.get(fid)
                    if rec is None:
                        return None, r
                    return CellCtx(self, rec), r
        return None, None
