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

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from asset_convert.collision import collision_extract as ce
from asset_convert.game_paths import namespace_for, set_namespace
from tes5_import.navmesh import corridor
from tes5_import.navmesh.lattice.build import build_lattice
from tes5_import.navmesh.from_pgrd import (
    _cell_graph, collect_doors, load_door_centroids,
)
from tes5_import.base.text_reader import get_formid, get_int, parse_export_file
from tes5_import.navmesh.pool import navmesh_land_at, with_water
from tes5_import.navmesh.world import open_water_z
from tes5_import.record_types.items import load_furniture_models
from tools.navmesh.audit import cell_index, master_dirs
from tools.navmesh.cell_index import index_map, shift_fid
from core.plugin_masters import master_dir, masters_from_export_header
from output_layout import assets_for

DEFAULT_EXPORT = 'export/Oblivion.esm'

#: RecordFlags bit marking a worldspace's persistent (dummy) cell, which has no land.
_PERSISTENT_FLAG = 0x400


def master_export_dirs_of(export):
    """Each TES4 master's export directory, in _HEADER.txt order."""
    return [master_dir(export, n) for n in masters_from_export_header(export)]


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
    return load_furniture_models(os.path.join(export, 'meshes'), by_type,
                                 quiet=quiet, record_dir=export)


def wrld_records(export):
    """One export's WRLD records, read straight from its WRLD.txt.

    See: docs/commentary/tes5_import_navmesh.md#cellview-open-is-cached
    """
    path = os.path.join(export, 'WRLD.txt')
    return parse_export_file(path) if os.path.isfile(path) else []


def _acti_fids(export):
    """ACTI FormIDs in one export's own numbering, from its ACTI.txt."""
    path = os.path.join(export, 'ACTI.txt')
    if not os.path.isfile(path):
        return []
    return [get_formid(r, 'FormID') for r in parse_export_file(path)]


class CellCtx(object):
    """One cell, ready to regenerate."""

    def __init__(self, index, rec):
        """`rec`'s refs, pathgrid graph, doors and walked-on land, read from `index`."""
        self.index = index
        self.rec = rec
        self.name = rec.get('EditorID') or ''
        self.fid = (rec.get('FormID') or '').upper()
        self.refrs, pg, land = index.of_cell(self.fid)
        graph = (_cell_graph(pg, rec) if pg is not None
                 else (None, None, 0.0, 0.0, False))
        nodes, edges, self.origin_x, self.origin_y, self.exterior = graph
        if not self.exterior and rec.get('XCLC.X') not in (None, ''):
            self.exterior = True
            self.origin_x, self.origin_y = float(rec['XCLC.X']) * 4096.0, float(rec['XCLC.Y']) * 4096.0
        self.nodes, self.edges = nodes or [], edges or []
        self.doors = collect_doors(self.refrs, index.door_fids)
        self.land = index.land_for(rec, land) if self.exterior else None

    @property
    def has_pathgrid(self):
        return bool(self.nodes)

    def build(self, ledges_out=None, lattice=False, obstacles=(), water_z=None, edges=None):
        """Regenerate this cell's RAW navmesh exactly as the pipeline's generator would.

        `ledges_out` collects `(upper_tri, lower_tri, drop)` drop-down links.
        Hand corrections apply after this (`navmesh_pins.apply_hand_edits`).
        `lattice` picks the lattice generator, else the corridor one.  `obstacles`
        (placement, model key) pairs are bridged across on a copy of `edges`
        (default: the pathgrid's).  `water_z` defaults to the import's for an exterior.
        """
        if lattice:
            return self._build_lattice(ledges_out)
        if water_z is None and self.exterior:
            water_z = self.index.water_z(self.rec)
        verts, tris, ledges = corridor.build_corridors(
            self.refrs, self.index.base_model, ce.get_collision,
            self.nodes, list(self.edges if edges is None else edges), land_rec=self.land,
            origin_x=self.origin_x, origin_y=self.origin_y,
            doors=[(x, y, z, r, tp, w)
                   for (x, y, z, r, _f, tp, w) in self.doors],
            door_bases=set(self.index.door_fids.keys()),
            obstacles=obstacles, water_z=water_z, flood=((),) if self.exterior else ())
        if ledges_out is not None:
            ledges_out.extend(ledges)
        return verts, [tuple(int(i) for i in tri[:3]) for tri in tris]

    def _build_lattice(self, ledges_out):
        """This cell through the prototype lattice generator, same return shape."""
        verts, tris, ledges = build_lattice(
            self.refrs, self.index.base_model, ce.get_collision,
            self.nodes, self.edges, land_rec=self.land,
            origin_x=self.origin_x, origin_y=self.origin_y,
            doors=[(x, y, z, r, tp, w)
                   for (x, y, z, r, _f, tp, w) in self.doors],
            door_bases=set(self.index.door_fids.keys()),
            activators=self.index.activator_fids)
        if ledges_out is not None:
            ledges_out.extend(ledges)
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
        Keyed by `base_fid`, the SAME full FormID `_placed_soup` resolves, so
        a plugin with masters does not name every mesh '?'.

        See: docs/commentary/tes5_import_navmesh.md#base-ids-carry-their-plugin-index
        """
        from tes5_import.navmesh import world
        skip = set(self.index.door_fids.keys())
        out = []
        for refr in self.refrs:
            base = world.base_fid(refr)
            if base is None or base in skip:
                continue
            w, b = world._placed_soup(refr, self.index.base_model,
                                      ce.get_collision)
            if (w is None or not len(w)) and (b is None or not len(b)):
                continue
            out.append((self.index.base_model.get(base) or '?', w, b))
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
        """Open `export`'s cell index and arm its collision and door tables."""
        self.export = export
        self._quiet = quiet
        self.arm()
        self._idx = cell_index(export)
        self._lookup = None
        self._acti = None
        self._land_at = None
        self._worlds = None

    def water_z(self, rec):
        """Exterior cell `rec`'s open water height as the import builds it (pool.with_water), or None."""
        if self._worlds is None:
            dirs = list(reversed(master_dirs(self.export))) + [self.export]
            self._worlds = {get_formid(w, 'FormID'): w for d in dirs for w in wrld_records(d)}
        wrld = self._worlds.get(get_formid(rec, 'ParentWRLD'))
        return open_water_z(with_water(rec, wrld) if wrld else rec, True)

    def land_for(self, rec, own):
        """The LAND the game draws under exterior cell `rec`; `own` is its own LAND.

        A child worldspace drawing its parent's land walks on the parent's LAND
        at the same square, resolved by the pipeline's own `navmesh_land_at`.

        See: docs/commentary/tes5_import_navmesh.md#child-worldspaces-walk-the-parents-land
        """
        if self._land_at is None:
            dirs = list(reversed(master_dirs(self.export))) + [self.export]
            stubs = [{'FormID': c.get('FormID'), 'ParentCELL': c.get('FormID')}
                     for c in self.cells]
            self._land_at = navmesh_land_at({
                'WRLD': [w for d in dirs for w in wrld_records(d)],
                'CELL': self.cells, 'LAND': stubs})
        got = self._land_at((get_formid(rec, 'ParentWRLD'),
                             get_int(rec, 'XCLC.X'), get_int(rec, 'XCLC.Y')))
        if (got is None or got['FormID'] == rec.get('FormID')
                or get_int(rec, 'RecordFlags') & _PERSISTENT_FLAG):
            return own
        return self.of_cell(got['FormID'].upper())[2] or own

    @property
    def base_model(self):
        """Base FormID -> model key, across the master chain."""
        return self._idx.base_model

    @property
    def door_fids(self):
        """DOOR base FormIDs, across the master chain."""
        return self._idx.door_fids

    @property
    def cells(self):
        """Every CELL record, across the master chain."""
        return self._idx.cells

    @property
    def activator_fids(self):
        """ACTI base FormIDs across the master chain, in THIS plugin's numbering.

        See: docs/commentary/tes5_import_navmesh.md#cellview-master-numbering
        """
        if self._acti is None:
            self._acti = set(_acti_fids(self.export))
            for d in master_dirs(self.export):
                imap = index_map(self.export, d)
                self._acti.update(shift_fid(f, imap) for f in _acti_fids(d))
            self._acti.discard(None)
        return self._acti

    def _lookups(self):
        """`(by EditorID, by FormID)` over the chain's cells, built once.

        Deferred: indexing 61,181 records is wasted on a caller that only
        opens one cell by FormID.

        See: docs/commentary/tes5_import_navmesh.md#cellview-cell-index
        """
        if self._lookup is None:
            by_name, by_fid = {}, {}
            for c in self.cells:
                eid = (c.get('EditorID') or '').lower()
                if eid:
                    by_name.setdefault(eid, c)
                by_fid[(c.get('FormID') or '').upper()] = c
            self._lookup = (by_name, by_fid)
        return self._lookup

    def of_cell(self, fid):
        """`(refrs, pgrd, land)` for one cell, read on demand.

        See: docs/commentary/tes5_import_navmesh.md#cellview-cell-index
        """
        return self._idx.of_cell(fid)

    def pathgrid_fids(self):
        """Cell FormIDs that have a pathgrid."""
        return self._idx.pathgrid_fids()

    def arm(self):
        """Point the shared collision/door globals at THIS export's tables.

        `ce.load_collision` and `load_door_centroids` write module globals, so
        two NavIndex objects in one process share one table and the last one
        built wins -- every cell of the other export then finds no collision.

        The asset namespace is one of those globals: `model_key` prefixes
        every model path with it, so without this a non-Oblivion export looks
        its meshes up under `tes4/` and finds no collision at all.

        See: docs/commentary/tes5_import_navmesh.md#navindex-arms-shared-tables
        """
        key = os.path.normcase(os.path.normpath(self.export))
        if NavIndex._armed == key:
            return
        set_namespace(namespace_for(self.export))
        ce.load_collision(self.collision_caches(), quiet=self._quiet)
        load_door_centroids(
            os.path.join(str(assets_for(self.export)),
                         'door_centers_cache.json'),
            quiet=self._quiet)
        load_origin_shifts(self.export, quiet=self._quiet)
        NavIndex._armed = key

    @classmethod
    def disarm(cls):
        """Forget which export's tables are live, so the next `arm` reloads them from disk."""
        cls._armed = None

    def collision_caches(self):
        """Every collision cache this export needs, MASTERS FIRST, whole chain.

        A child plugin caches only the meshes it ships, so loading its own
        cache alone leaves every master-owned static uncarved -- and a master's
        base may use ITS master's mesh (Morrowind_ob placing Oblivion clutter).

        See: docs/commentary/tes5_import_navmesh.md#cellview-master-owned-cells
        """
        out = []
        direct = master_export_dirs_of(self.export)
        deeper = [d for d in master_dirs(self.export) if d not in direct]
        for d in deeper + direct + [self.export]:
            path = os.path.join(str(assets_for(d)), 'collision_cache.bin')
            if path not in out:
                out.append(path)
        return out

    def cell(self, name_or_fid):
        """Look up by EditorID (case-insensitive) or by FormID hex."""
        self.arm()
        by_name, by_fid = self._lookups()
        rec = by_name.get(str(name_or_fid).lower())
        if rec is None:
            rec = by_fid.get(str(name_or_fid).upper().lstrip('0X').rjust(8, '0'))
        if rec is None:
            rec = by_fid.get(str(name_or_fid).upper())
        if rec is None:
            return None
        return CellCtx(self, rec)

    def cell_of_ref(self, refid):
        """Find the cell containing a placed reference (FormID hex).

        Lets a bug report phrased as "the area around 1a01fc1e is mangled" be
        turned straight into a cell + a bounding box, with no manual hunting.
        """
        key = str(refid).upper().lstrip('0X').rjust(8, '0')
        for fid, refrs, _pg, _ld in self._idx.iter_cells():
            for r in refrs:
                if (r.get('FormID') or '').upper().lstrip('0X').rjust(8, '0') == key:
                    rec = self._lookups()[1].get(fid)
                    if rec is None:
                        return None, r
                    return CellCtx(self, rec), r
        return None, None
