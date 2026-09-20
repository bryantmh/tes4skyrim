"""Gather, schedule and cache the plugin's PGRD->NAVM navmesh builds.

The parent-side orchestration around `navm_worker`: which cells become jobs,
the base-model/door indexes their carving needs, the on-disk geometry cache's
tag, and the process pool that runs them.  Geometry itself lives in
`build`/`corridor`; nothing here shapes a triangle.

See: docs/commentary/tes5_import_navmesh.md#pool-orchestration
"""

from asset_convert.game_paths import current_namespace
from output_layout import asset_cache_chain
import glob
import hashlib
import os
import struct
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

from core.worker_budget import worker_count
from . import cache_audit as navm_verify, worker as navm_worker
from ..overrides.nested import DELETED_FLAG
from ..record_types.navm_falloutnv import precompute_fallout_navmeshes
from ..base.text_reader import (get_float, get_formid, get_formid_index_offset,
                           get_injected_formids, get_int, get_str)

#: Base record types whose placed footprint carves holes in a navmesh.
_BLOCKING_BASE_TYPES = frozenset({'STAT', 'CONT', 'FURN', 'ACTI', 'TREE'})

#: Side of one exterior cell, in game units.
_CELL_SIZE = 4096.0

#: RecordFlags bit marking a worldspace's persistent (dummy) cell.
_PERSISTENT_FLAG = 0x400

#: Jobs pickled and queued at once, as a multiple of workers * chunksize.
_BUFFER_FACTOR = 4

#: Tasks a pool worker runs before it is recycled.
_TASKS_PER_CHILD = 500


# ---------------------------------------------------------------------------
# Grid helpers
# ---------------------------------------------------------------------------


def navm_worker_count(job_count: int) -> int:
    """Pick a worker count bounded by the pipeline budget and job count."""
    return min(worker_count(), max(1, job_count))


def grid_sort_key(label: bytes):
    """Exterior GRUP label -> vanilla's unsigned (X, Y) order, X major.

    See: docs/commentary/tes5_import_navmesh.md#exterior-block-ordering
    """
    y, x = struct.unpack('<HH', label)
    return (x, y)


def ensure_cell_grid(cell: dict) -> None:
    """Stamp XCLC=(0,0) on an exterior CELL that omitted it.  Mutates in place.

    See: docs/commentary/tes5_import_navmesh.md#exterior-block-ordering
    """
    if get_str(cell, 'XCLC.X'):
        return
    cell['XCLC.X'] = '0'
    cell['XCLC.Y'] = '0'


def _model_key(model: str) -> str:
    """Normalize a TES4 model path to the mesh_bounds cache key.

    Lowercase, forward slashes, game-namespace prefix, '.nif' suffix --
    e.g. 'Furniture\\ChairNoble01.NIF' -> 'tes4/furniture/chairnoble01.nif'.
    A TREE's '.spt' resolves to '<ns>/speedtrees/<name>.nif', the path the
    speedtree stage writes.
    See: docs/commentary/tes5_import_navmesh.md#speedtree-model-keys
    """
    p = model.lower().replace('\\', '/').lstrip('/')
    if p.startswith('textures/'):
        p = p[len('textures/'):]
    ns = current_namespace() + '/'
    if p.endswith('.spt'):
        return '%sspeedtrees/%s.nif' % (ns, os.path.basename(p)[:-4])
    if not p.startswith(ns):
        p = ns + p
    if not p.endswith('.nif'):
        p += '.nif'
    return p


def _records_of(by_type: dict, master_export: dict, sigs) -> list:
    """This plugin's records of *sigs*, with the MASTERS' listed FIRST.

    Masters first so an override in this plugin wins the key.
    """
    out = []
    if master_export:
        out.append(r for r in master_export.values()
                   if r.get('Signature') in sigs)
    out.append(r for sig in sigs for r in by_type.get(sig, []))
    return [r for src in out for r in src]


def _low_fid(rec: dict):
    """A record's low-24 FormID, or None when it has none / is unparsable."""
    fid_str = rec.get('FormID')
    if not fid_str:
        return None
    try:
        return int(fid_str, 16) & 0x00FFFFFF
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Carving indexes
# ---------------------------------------------------------------------------


def build_base_model_index(by_type: dict, master_export: dict = None) -> dict:
    """Map raw low-24 base-object FormID -> normalized model key.

    Only blocking base types are indexed, so carving never removes triangles
    under doors, lights, markers or actors.  `master_export` is REQUIRED for a
    plugin with masters.

    See: docs/commentary/tes5_import_navmesh.md#pool-orchestration
    """
    index = {}
    for rec in _records_of(by_type, master_export, _BLOCKING_BASE_TYPES):
        model = get_str(rec, 'Model.MODL') or get_str(rec, 'MODL')
        low = _low_fid(rec) if model else None
        if low is not None:
            index[low] = _model_key(model)
    return index


def build_base_radius_index(by_type: dict, master_export: dict = None) -> dict:
    """Map raw low-24 base FormID -> the model's bounding radius (MODB).

    Only bases that carve are indexed, and only those declaring a radius, so
    the overhang index never widens a cell's gather for a marker or a light.
    `master_export` is REQUIRED for a plugin with masters, exactly as for
    `build_base_model_index`.
    """
    index = {}
    for rec in _records_of(by_type, master_export, _BLOCKING_BASE_TYPES):
        low = _low_fid(rec)
        if low is None:
            continue
        radius = get_float(rec, 'Model.MODB', 0.0)
        if radius > 0.0:
            index[low] = radius
    return index


def build_door_fid_set(by_type: dict, master_export: dict = None) -> dict:
    """Map raw low-24 DOOR base FormID -> normalized model key (or None).

    The key matches door_centers_cache so `_collect_doors` can panel-center each
    door.  Membership of the map doubles as the "is this a DOOR base" test, so
    `master_export` is REQUIRED for a plugin with masters.

    See: docs/commentary/tes5_import_navmesh.md#pool-orchestration
    """
    out = {}
    for rec in _records_of(by_type, master_export, ('DOOR',)):
        base = _low_fid(rec)
        if base is None:
            continue
        model = rec.get('Model.MODL') or rec.get('MODL')
        out[base] = _model_key(model) if model else None
    return out


def build_teleport_grid(by_type: dict, master_export: dict = None):
    """(occupied exterior grid squares, teleport-door placements).

    Feeds `record_types.world.set_teleport_grid`, which rejects an XTEL naming a
    grid square holding no cell -- a null the CK dereferences unchecked.  The
    MASTERS' cells and doors are indexed too: a door missing from these maps is
    left alone, so master blindness would silently disable the check.
    """
    grid_cells = set()
    for cell in _records_of(by_type, master_export, ('CELL',)):
        wrld = get_formid(cell, 'ParentWRLD')
        if wrld and not get_int(cell, 'RecordFlags') & _PERSISTENT_FLAG:
            grid_cells.add((wrld, get_int(cell, 'XCLC.X'),
                            get_int(cell, 'XCLC.Y')))

    placement = {}
    for ref in _records_of(by_type, master_export, ('REFR',)):
        if ref.get('XTEL.Door'):
            placement[get_formid(ref, 'FormID')] = (
                get_formid(ref, 'ParentWRLD'),
                get_float(ref, 'PosX'), get_float(ref, 'PosY'),
                get_float(ref, 'PosZ'))
    return grid_cells, placement


# ---------------------------------------------------------------------------
# Job gathering
# ---------------------------------------------------------------------------


def _by_parent_cell(recs) -> dict:
    """Bucket records by their ParentCELL FormID."""
    out = defaultdict(list)
    for rec in recs:
        out[get_formid(rec, 'ParentCELL')].append(rec)
    return out


def _merge_master_cell_records(by_type: dict, master_export: dict,
                               sig: str) -> list:
    """`sig` records the plugin navmeshes with: the masters' plus its own.

    A child plugin re-states only the references it edits, so navmeshing from
    `by_type` alone carves a cell the masters furnished as if it were bare.
    The masters' records are the baseline; the plugin's own override them by
    FormID, and one the plugin flags deleted drops out entirely.

    See: docs/commentary/tes5_import_navmesh.md#master-owned-cells
    """
    own = by_type.get(sig, [])
    if not master_export:
        return own
    own_fids = {get_formid(rec, 'FormID') for rec in own}
    merged = [rec for key, rec in master_export.items()
              if rec.get('Signature') == sig and int(key, 16) not in own_fids]
    merged.extend(rec for rec in own
                  if not (get_int(rec, 'RecordFlags') & DELETED_FLAG))
    return merged


def _is_door_ref(rec: dict, door_fids) -> bool:
    """Is this REFR a teleport door, or a placement of a DOOR base?"""
    if rec.get('XTEL.Door'):
        return True
    name = rec.get('NAME')
    if not name:
        return False
    try:
        return (int(name, 16) & 0xFFFFFF) in door_fids
    except ValueError:
        return False


def _persistent_doors_by_grid(cells, refr_by_cell, door_fids) -> dict:
    """Worldspace persistent door refs, bucketed by the grid square they sit in.

    See: docs/commentary/tes5_import_navmesh.md#pool-orchestration
    """
    out = defaultdict(list)
    for cell in cells:
        wrld = get_formid(cell, 'ParentWRLD')
        if not wrld or not (get_int(cell, 'RecordFlags') & _PERSISTENT_FLAG):
            continue
        for rec in refr_by_cell.get(get_formid(cell, 'FormID'), []):
            if not _is_door_ref(rec, door_fids):
                continue
            try:
                x = float(rec.get('PosX', ''))
                y = float(rec.get('PosY', ''))
            except (TypeError, ValueError):
                continue
            out[(wrld, int(x // _CELL_SIZE), int(y // _CELL_SIZE))].append(rec)
    return out


def _interior_blocks(cells) -> dict:
    """Interior cells bucketed block -> sub-block, as _build_cell_groups does."""
    blocks = defaultdict(lambda: defaultdict(list))
    for cell in cells:
        if get_formid(cell, 'ParentWRLD'):
            continue
        object_id = get_formid(cell, 'FormID') & 0xFFFFFF
        blocks[object_id % 10][(object_id // 10) % 10].append(cell)
    return blocks


def _exterior_blocks(cells) -> dict:
    """Exterior cells bucketed block -> sub-block, as _build_world_groups does."""
    blocks = defaultdict(lambda: defaultdict(list))
    for cell in cells:
        grid_x = get_int(cell, 'XCLC.X')
        grid_y = get_int(cell, 'XCLC.Y')
        block = struct.pack('<hh', grid_y // 32, grid_x // 32)
        sub = struct.pack('<hh', grid_y // 8, grid_x // 8)
        blocks[block][sub].append(cell)
    return blocks


def _refr_reach(rec, radius_by_base) -> float:
    """How far this placement's geometry extends from its own position.

    The base model's bounding radius, scaled by the REFR.  0.0 when the base
    has no radius, which drops the ref from the overhang index entirely.
    """
    name = rec.get('NAME')
    if not name:
        return 0.0
    try:
        radius = radius_by_base.get(int(name, 16) & 0x00FFFFFF, 0.0)
    except ValueError:
        return 0.0
    return radius * (get_float(rec, 'XSCL.Scale', 1.0) or 1.0)


def _foreign_squares(rec, gx, gy, radius_by_base):
    """Grid squares this ref's geometry covers, EXCLUDING its own (gx, gy)."""
    reach = _refr_reach(rec, radius_by_base)
    if reach <= 0.0:
        return ()
    try:
        x, y = float(rec['PosX']), float(rec['PosY'])
    except (KeyError, TypeError, ValueError):
        return ()
    return [(nx, ny)
            for nx in range(int((x - reach) // _CELL_SIZE),
                            int((x + reach) // _CELL_SIZE) + 1)
            for ny in range(int((y - reach) // _CELL_SIZE),
                            int((y + reach) // _CELL_SIZE) + 1)
            if nx != gx or ny != gy]


def _overhang_index(cells, refr_by_cell, radius_by_base) -> dict:
    """Map (wrld, gx, gy) -> refs from OTHER cells whose geometry reaches in.

    27% of Oblivion's exterior placements extend past their own cell square,
    and a cell gathered from its ParentCELL list alone sees none of them -- a
    pier, wall or rock owned next door carves nothing and renders as a hole.

    See: docs/commentary/tes5_import_navmesh.md#refs-overhang-their-cell
    """
    out = defaultdict(list)
    for cell in cells:
        wrld = get_formid(cell, 'ParentWRLD')
        if not wrld or (get_int(cell, 'RecordFlags') & _PERSISTENT_FLAG):
            continue
        gx, gy = get_int(cell, 'XCLC.X'), get_int(cell, 'XCLC.Y')
        for rec in refr_by_cell.get(get_formid(cell, 'FormID'), []):
            for square in _foreign_squares(rec, gx, gy, radius_by_base):
                out[(wrld,) + square].append(rec)
    return out


def _emit_jobs(jobs, cell_rec, land_rec, refr_by_cell, pgrd_by_cell,
               extra_door_refrs=None, extra_refrs=None) -> None:
    """Append one job per PGRD in this cell."""
    cell_fid = get_formid(cell_rec, 'FormID')
    cell_refrs = refr_by_cell.get(cell_fid, [])
    if extra_refrs:
        cell_refrs = cell_refrs + list(extra_refrs)
    for pgrd_rec in pgrd_by_cell.get(cell_fid, []):
        jobs.append({
            'key': (cell_fid, get_formid(pgrd_rec, 'FormID')),
            'pgrd_rec': pgrd_rec,
            'land_rec': land_rec,
            'cell_rec': cell_rec,
            'refr_recs': cell_refrs,
            'extra_door_refrs': extra_door_refrs or [],
        })


def _gather_exteriors(jobs, by_type, cells, indexes, pers_doors,
                      overhang=None) -> None:
    """Append exterior jobs, per worldspace, in _build_world_groups order."""
    refr_by_cell, land_by_cell, pgrd_by_cell = indexes
    overhang = overhang or {}
    ext_by_wrld = defaultdict(list)
    for cell in cells:
        wrld_fid = get_formid(cell, 'ParentWRLD')
        if wrld_fid:
            ext_by_wrld[wrld_fid].append(cell)

    worlds = sorted(by_type.get('WRLD', []),
                    key=lambda w: get_formid(w, 'FormID'))
    for wrld_rec in worlds:
        wrld_fid = get_formid(wrld_rec, 'FormID')
        exterior = [c for c in ext_by_wrld.get(wrld_fid, [])
                    if not (get_int(c, 'RecordFlags') & _PERSISTENT_FLAG)]
        for cell in exterior:
            ensure_cell_grid(cell)
        blocks = _exterior_blocks(exterior)
        for block in sorted(blocks, key=grid_sort_key):
            for sub in sorted(blocks[block], key=grid_sort_key):
                for cell_rec in sorted(
                        blocks[block][sub],
                        key=lambda c: (get_int(c, 'XCLC.Y'),
                                       get_int(c, 'XCLC.X'))):
                    lands = land_by_cell.get(get_formid(cell_rec, 'FormID'), [])
                    square = (wrld_fid, get_int(cell_rec, 'XCLC.X'),
                              get_int(cell_rec, 'XCLC.Y'))
                    _emit_jobs(jobs, cell_rec, lands[0] if lands else None,
                               refr_by_cell, pgrd_by_cell,
                               pers_doors.get(square, []),
                               overhang.get(square, []))


def gather_navm_jobs(by_type: dict, door_fids: set = None,
                     master_export: dict = None) -> list:
    """Enumerate PGRD->NAVM jobs in the order the group builders visit them.

    Interiors first (block/sub-block), then exteriors per worldspace, matching
    the builders so allocated FormIDs match the serial path.  Geometry inputs
    merge the masters in; the jobs themselves stay driven by the plugin's OWN
    pathgrids, so no id moves.

    See: docs/commentary/tes5_import_navmesh.md#pool-orchestration
    """
    cells = by_type.get('CELL', [])
    refr_by_cell = _by_parent_cell(
        _merge_master_cell_records(by_type, master_export, 'REFR'))
    land_by_cell = _by_parent_cell(
        _merge_master_cell_records(by_type, master_export, 'LAND'))
    pgrd_by_cell = _by_parent_cell(by_type.get('PGRD', []))
    indexes = (refr_by_cell, land_by_cell, pgrd_by_cell)
    pers_doors = _persistent_doors_by_grid(cells, refr_by_cell,
                                           door_fids or set())
    overhang = _overhang_index(
        cells, refr_by_cell, build_base_radius_index(by_type, master_export))

    jobs = []
    blocks = _interior_blocks(cells)
    for block in sorted(blocks):
        for sub in sorted(blocks[block]):
            for cell_rec in blocks[block][sub]:
                _emit_jobs(jobs, cell_rec, None, refr_by_cell, pgrd_by_cell)
    _gather_exteriors(jobs, by_type, cells, indexes, pers_doors, overhang)
    return jobs


# ---------------------------------------------------------------------------
# Geometry cache tag
# ---------------------------------------------------------------------------


#: Runs AFTER geometry leaves the cache, so it cannot invalidate an entry.
_TAG_EXCLUDE = frozenset({'edge_links.py'})

#: Native SOURCES deciding cell geometry; hashed with the Python, never the .pyd.
_TAG_NATIVE = ('navgrow/grow.cpp',)


def _native_tag_sources() -> list:
    """Each `_TAG_NATIVE` file's bytes, newline-normalized; missing ones drop.

    Anchored on the REPO, not on this module's `__file__`: the tag tests copy
    the navmesh package to a temp dir, and a path derived from the copy would
    find no native tree and void the whole tag.

    See: docs/commentary/tes5_import_navmesh.md#the-tag-covers-the-native-march
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    out = []
    for name in _TAG_NATIVE:
        path = os.path.join(root, 'native', 'src',
                            name.replace('/', os.sep))
        try:
            with open(path, 'rb') as fh:
                out.append(fh.read().replace(b'\r\n', b'\n'))
        except OSError:
            continue
    return out


def master_navm_grid(master_export: dict, master_index) -> dict:
    """{(wrld_fid, grid_x, grid_y): navm_fid} for the MASTERS' exterior cells.

    Edge links are matched by grid position, but the master index keys its
    navmeshes by CELL FormID, so the masters' own CELL records supply XCLC.
    A split cell contributes its FIRST navmesh, matching the id an override
    of that cell adopts.

    See: docs/commentary/tes5_import_navmesh.md#cross-plugin-edge-links
    """
    if not master_export or master_index is None:
        return {}
    out = {}
    ext = no_navm = 0
    for rec in master_export.values():
        if rec.get('Signature') != 'CELL':
            continue
        wrld = get_formid(rec, 'ParentWRLD')
        if not wrld or get_int(rec, 'RecordFlags') & _PERSISTENT_FLAG:
            continue
        ext += 1
        fids = master_index.navms(get_formid(rec, 'FormID'))
        if fids:
            out[(wrld, get_int(rec, 'XCLC.X'),
                 get_int(rec, 'XCLC.Y'))] = fids[0]
        else:
            no_navm += 1
    print(f"    Master navmesh grid: {len(out)} cells mapped, {no_navm} of "
          f"{ext} exterior cells have no NAVM in the master index")
    return out


def collision_cache_chain(export_dir: str) -> tuple:
    """Every collision cache this plugin needs, MASTERS FIRST.

    See: docs/commentary/tes5_import_navmesh.md#master-owned-cells
    """
    return asset_cache_chain(export_dir, 'collision_cache.bin')


def navmesh_geom_cache(collision_cache: str):
    """(cache_dir, tag) for the on-disk navmesh geometry cache, or None.

    The tag hashes the navmesh generator SOURCES, so editing any navmesh code
    (params and the native march included) invalidates every entry
    automatically.  Collision enters per-cell via `from_pgrd._geom_hash`, never
    here.  Newlines are normalized to LF so the tag is a property of the
    CONTENT, not of the checkout's line-ending mode.

    See: docs/commentary/tes5_import_navmesh.md#pool-orchestration
    """
    if not collision_cache or not os.path.exists(collision_cache):
        return None
    h = hashlib.sha1()
    here = os.path.dirname(os.path.abspath(__file__))
    srcs = sorted(s for s in glob.glob(os.path.join(here, '*.py'))
                  if os.path.basename(s) not in _TAG_EXCLUDE)
    for src in srcs:
        try:
            with open(src, 'rb') as fh:
                h.update(fh.read().replace(b'\r\n', b'\n'))
        except OSError:
            return None
    for body in _native_tag_sources():
        h.update(body)
    cache_dir = os.path.join(os.path.dirname(collision_cache),
                             'navmesh_geom_cache')
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir, h.hexdigest()


def stamp_navmesh_cache_tag(geom_cache) -> None:
    """Record the tag the entries were just BUILT with, in CACHE_TAG.

    Called only AFTER a generation pass, never from `navmesh_geom_cache`.

    See: docs/commentary/tes5_import_navmesh.md#pool-orchestration
    """
    if not geom_cache:
        return
    cache_dir, tag = geom_cache
    try:
        with open(os.path.join(cache_dir, 'CACHE_TAG'), 'w') as fh:
            fh.write(tag)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Running the jobs
# ---------------------------------------------------------------------------


def _progress(done, total, t0):
    """Print one `done/total` line with the rate so far."""
    rate = done / max(time.time() - t0, 1e-6)
    print(f"    {done}/{total} navmeshes ({rate:.1f}/s)", flush=True)


def _progress_every(total) -> int:
    """Jobs between progress lines: about 40 lines, whatever the plugin size."""
    return max(1, total // 40)


def _run_inline(jobs) -> dict:
    """Convert every job in this process; one tiny job is not worth a pool."""
    cache = {}
    total, every, t0 = len(jobs), _progress_every(len(jobs)), time.time()
    for done, job in enumerate(jobs, 1):
        key, result = navm_worker.run_job(job)
        cache[key] = result
        if done % every == 0 or done == total:
            _progress(done, total, t0)
    return cache


def _map_kwargs(n_jobs, n_workers) -> dict:
    """chunksize/buffersize for a run_job map.

    `buffersize` bounds how many jobs are pickled and queued at once; it only
    exists from Python 3.14, and omitting it is the pre-3.14 behaviour.
    """
    chunksize = max(1, n_jobs // (n_workers * 8))
    kwargs = {'chunksize': chunksize}
    if sys.version_info >= (3, 14):
        kwargs['buffersize'] = n_workers * chunksize * _BUFFER_FACTOR
    return kwargs


def _pool(initargs, n_workers) -> ProcessPoolExecutor:
    """A run_job pool whose workers carry this plugin's context."""
    return ProcessPoolExecutor(max_workers=n_workers,
                               initializer=navm_worker.init_worker,
                               initargs=initargs,
                               max_tasks_per_child=_TASKS_PER_CHILD)


def _run_pooled(jobs, initargs, n_workers) -> dict:
    """Convert every job across a process pool."""
    cache = {}
    total, every, t0 = len(jobs), _progress_every(len(jobs)), time.time()
    with _pool(initargs, n_workers) as ex:
        for done, (key, result) in enumerate(
                ex.map(navm_worker.run_job, jobs,
                       **_map_kwargs(len(jobs), n_workers)), 1):
            cache[key] = result
            if done % every == 0 or done == total:
                _progress(done, total, t0)
    return cache


def pooled_prover(initargs, n_workers):
    """A `rebuild` callable for cache_audit.prove_cache, backed by the pool.

    Yields `(key, meta)` in SUBMISSION order, so the first mismatch a caller
    sees does not depend on which worker finished first.

    See: docs/commentary/tes5_import_navmesh.md#proving-runs-on-the-pool
    """
    def _rebuild(jobs):
        """Rebuild `jobs` across the pool, yielding results in order."""
        if len(jobs) == 1 or n_workers == 1:
            for job in jobs:
                yield navm_worker.run_job(job)
            return
        workers = min(n_workers, len(jobs))
        with _pool(initargs, workers) as ex:
            yield from ex.map(navm_worker.run_job, jobs,
                              **_map_kwargs(len(jobs), workers))
    return _rebuild


def _adopt_master_navm_fids(jobs: list, master_index) -> int:
    """Point jobs at a MASTER's cell at that cell's existing NAVM ids.

    Returns the number of jobs re-pointed.  Every job keeps the id
    `derive_formid` handed it unless the master already navmeshed its cell:
    skipping the call would change the allocator's taken-set and move
    unrelated derived ids.

    See: docs/commentary/tes5_import_navmesh.md#master-owned-cells
    """
    if master_index is None:
        return 0
    by_cell = defaultdict(list)
    for job in jobs:
        by_cell[job['key'][0]].append(job)

    adopted = 0
    for cell_fid, cell_jobs in by_cell.items():
        master_fids = master_index.navms(cell_fid)
        if not master_fids:
            continue
        for job, fid in zip(cell_jobs, master_fids):
            job['navm_fid'] = fid
            adopted += 1
    return adopted


def precompute_navmeshes(by_type: dict, writer, base_model_by_fid: dict,
                         door_fids: set, collision_cache: str = '',
                         master_index=None, master_export: dict = None) -> dict:
    """Run every PGRD->NAVM conversion in parallel; return {key: (bytes, meta)}.

    FormIDs are pre-allocated serially in builder-visit order, so results are
    byte-identical to the single-threaded path.  The worker context is
    initialized HERE because `navm_verify.prepare` re-keys entries from it.
    `collision_cache` is a path or a masters-first chain whose LAST entry is
    this plugin's own and sites the derived geom/door caches.

    See: docs/commentary/tes5_import_navmesh.md#pool-orchestration
    """
    fallout = precompute_fallout_navmeshes(by_type, writer)
    if fallout is not None:
        return fallout

    jobs = gather_navm_jobs(by_type, door_fids, master_export)
    if not jobs:
        return {}

    formid_offset = get_formid_index_offset()
    for job in jobs:
        job['navm_fid'] = writer.derive_formid('NAVM', job['key'])
    adopted = _adopt_master_navm_fids(jobs, master_index)
    if adopted:
        print(f"    {adopted} navmeshes override a master's own "
              f"(of {len(jobs)})")

    n_workers = navm_worker_count(len(jobs))
    own_cache = (collision_cache if isinstance(collision_cache, str)
                 else (collision_cache[-1] if collision_cache else ''))
    geom_cache = navmesh_geom_cache(own_cache)
    door_centers = navm_verify.door_centers_cache_path(collision_cache)
    initargs = (base_model_by_fid, door_fids, collision_cache, formid_offset,
                geom_cache, get_injected_formids(), True, door_centers)
    navm_verify.init_context(base_model_by_fid, door_fids, collision_cache,
                             formid_offset, geom_cache,
                             get_injected_formids(), door_centers)
    navm_verify.prepare(jobs, geom_cache,
                        pooled_prover(initargs, n_workers))

    print(f"  Generating {len(jobs)} navmeshes (PGRD->NAVM) "
          f"across {n_workers} processes...")
    t0 = time.time()
    if len(jobs) == 1 or n_workers == 1:
        cache = _run_inline(jobs)
    else:
        cache = _run_pooled(jobs, initargs, n_workers)

    hits = sum(1 for (_b, m) in cache.values() if m and m.get('geom_cached'))
    print(f"    Navmesh generation: {len(jobs)} cells in "
          f"{time.time() - t0:.2f}s ({n_workers} workers, "
          f"{hits} geometry-cache hits)")

    navm_verify.report_verification(cache, geom_cache)
    if not navm_verify.report_failures(cache):
        stamp_navmesh_cache_tag(geom_cache)
    return cache
