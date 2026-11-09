"""Process-pool worker for parallel PGRD→NAVM navmesh generation.

This is a DELIBERATELY light module.  On Windows the process pool uses `spawn`,
so every worker re-imports the worker's module at start-up.  Importing the full
`import_main` (which pulls in the dialogue/creature/script/asset pipelines) in
each child cost several GB of RSS per process and exhausted RAM, killing workers
with BrokenProcessPool.  Depending only on `pgrd_to_navm` (which itself imports
just text_reader/writer, and loads scipy/shapely lazily) keeps each child small.

FormIDs are pre-assigned in the parent (job['navm_fid']) so no PluginWriter — an
unpicklable, shared-state object — has to cross the process boundary.

The Havok collision cache is a module global in `asset_convert.collision.collision_extract`;
a spawned child does NOT inherit the parent's loaded copy, so each worker loads it
once from disk in the pool initializer.  Without this, every cell would voxelize
an empty world and emit no navmesh at all.
"""

import math
import os

from asset_convert.collision.collision_extract import get_collision

from .door_reach import flood_graph, join_missing_doors
from .edge_links import CELL_SIZE
from .from_pgrd import cached_geometry, collect_doors, convert_PGRD, geom_cache_path, geom_equal, record_graph
from .lattice.build import set_activators
from .seeding import seeded_graph

#: A landing this close in plan to a door with no Door Triangle seeds that door's flood and stands in for it.
DOOR_LANDING_REACH = 512.0

# Per-worker read-only carving context, populated by _init in each child.
_BASE_MODEL_BY_FID: dict = {}
_DOOR_FIDS: set = set()
_GEOM_CACHE: tuple = None


def init_worker(base_model_by_fid: dict, door_fids: set, collision_cache: str,
                formid_offset: int = 0, geom_cache: tuple = None,
                injected_formids: dict = None, disable_gc: bool = True,
                door_centers_cache: str = None, activator_fids: frozenset = None):
    """ProcessPool initializer: stash context; load the collision cache.

    Runs once per worker process.  A spawned child inherits no module-global
    state, so the FormID index offset, collision cache, door caches and
    containment job are all rebuilt here.  The offset MUST be set before any
    get_formid() call.
    See: docs/commentary/tes5_import_navmesh.md#navmesh-worker-rebuilt-globals
    """
    from core.process_job import join_pool_job
    join_pool_job()

    global _BASE_MODEL_BY_FID, _DOOR_FIDS, _GEOM_CACHE
    _BASE_MODEL_BY_FID = base_model_by_fid
    _DOOR_FIDS = door_fids
    _GEOM_CACHE = geom_cache
    from ..base.text_reader import set_formid_index_offset, set_injected_formids
    set_formid_index_offset(formid_offset)
    set_injected_formids(injected_formids or {})
    if collision_cache:
        from asset_convert.collision.collision_extract import load_collision
        load_collision(collision_cache, quiet=True)

    if door_centers_cache:
        from .from_pgrd import load_door_centroids
        load_door_centroids(door_centers_cache, quiet=True)
    if activator_fids is not None:
        set_activators(activator_fids)

    # Generational GC is pure overhead in this worker and costs ~2x wall-clock.
    #
    # Voxelizing one cell allocates a Heightfield of ~22k empty column lists.
    # In isolation that takes 0.001s; inside the worker it took 0.356s, because
    # each burst of container allocations trips a generational collection that
    # then traverses every live object -- and by that point the child holds the
    # collision cache and the cell's records, ~1.16M tracked objects.  The
    # collector walks all of them to find nothing.
    #
    # Safe because this workload creates NO reference cycles: measured over 24
    # consecutive cell builds with the collector off, gc.collect() reported 0
    # uncollectable objects and tracemalloc stayed flat at 12.7 MB.  Refcounting
    # alone reclaims everything, so the collector has nothing to do.
    #
    # Scoped to the worker process on purpose.  import_main also calls this
    # initializer INLINE (single-job runs skip the pool), and the parent goes on
    # to convert every other record type -- so that path passes disable_gc=False
    # to keep the collector on in the parent.
    if disable_gc:
        import gc
        gc.disable()


def _verify_against_cache(job: dict, result: tuple) -> tuple:
    """Rebuild this cell and compare; on a mismatch RETURN THE FRESH BUILD.

    The tag hashes sources, so it cannot see a shapely/GEOS or `.pyd` change.
    This is the only check that catches a cache stale in a way the hash agrees
    with -- and keeping the fresh result means a bad cache costs accuracy in no
    cell at all, not even the ones sampled before the mismatch was known.

    See: docs/commentary/tes5_import_navmesh.md#verifying-a-cache-against-fresh-geometry
    """
    navm_bytes, meta = result
    stored = cached_geometry(_GEOM_CACHE, *job['key'])
    if stored is None:
        return result
    fresh_bytes, fresh_meta = _convert(job, None)
    meta['verified'] = True
    fresh = fresh_meta.get('geometry') if fresh_meta else None
    if fresh is None or geom_equal(stored, fresh):
        return result
    fresh_meta['verified'] = True
    fresh_meta['verify_mismatch'] = True
    return fresh_bytes, fresh_meta


def convert_job(job: dict, base_model_by_fid: dict, door_fids, geom_cache=None):
    """(navm_bytes, meta) for one gathered job; a seeded job floods its pathgrid first.

    An interior's door seeds join walkable paths from its doors into the graph.
    See: docs/commentary/tes5_import_navmesh.md#seeded-cells
    See: docs/commentary/tes5_import_navmesh.md#door-reach
    """
    graph = None
    if job.get('seeds'):
        graph = seeded_graph(job, base_model_by_fid, door_fids, get_collision)
        if graph is None:
            return None, None
    if job.get('door_seeds'):
        graph = flood_graph(job, base_model_by_fid, door_fids, get_collision)
        if not graph[1]:
            return None, None
    args = (job, base_model_by_fid, door_fids, geom_cache)
    built = _build(*args, graph)
    missing = _doors_without_triangle(job, built[1], door_fids)
    base = graph or record_graph(job['pgrd_rec'])
    joined = (join_missing_doors(job, base, missing, base_model_by_fid, door_fids, get_collision)
              if missing and base[0] is not None else None)
    if joined:
        rebuilt = _build(*args, joined)
        if _linked(rebuilt) > _linked(built):
            built, graph = rebuilt, joined
    return _at_landings(args, built, graph)


def _at_landings(args, built, graph):
    """`built`, or its rebuild with each door still lacking a Door Triangle linked at its nearest landing
    when that links more doors: the landing is where the game puts an actor coming through it.
    See: docs/commentary/tes5_import_navmesh.md#door-reach
    """
    spots = {}
    for x, y, _z, fid in _unlinked_doors(args[0], built[1], args[2]):
        near = [p for p in args[0].get('landings') or () if math.dist(p[:2], (x, y)) <= DOOR_LANDING_REACH]
        if near:
            spots[fid] = tuple(min(near, key=lambda p: math.dist(p[:2], (x, y))))
    if not spots:
        return built
    moved = _build(*args, graph, spots)
    return moved if _linked(moved) > _linked(built) else built


def _linked(result) -> int:
    """How many doors a build gave a Door Triangle."""
    return len((result[1] or {}).get('door_refs') or ()) if result else 0


def _build(job, base_model_by_fid, door_fids, geom_cache, graph, door_spots=None):
    """(navm_bytes, meta) for one job over `graph` (None: its pathgrid record's own graph)."""
    return convert_PGRD(
        job['pgrd_rec'],
        land_rec=job['land_rec'],
        cell_rec=job['cell_rec'],
        refr_recs=job['refr_recs'],
        base_model_by_fid=base_model_by_fid,
        door_fids=door_fids,
        navm_fid=job['navm_fid'],
        geom_cache=geom_cache,
        extra_door_refrs=job.get('extra_door_refrs'),
        spawns=job.get('spawns'),
        obstacles=job.get('obstacles'),
        graph=graph,
        door_spots=door_spots,
    )


def _doors_without_triangle(job, meta, door_fids) -> list:
    """Seeds for the job's teleport doors, standing in its own cell, that the built mesh gave no Door Triangle:
    each door's point, and per landing within DOOR_LANDING_REACH of it (authored on the floor) the landing
    and the door at the landing's height, since a flipped or hung door's origin stands far above its floor.
    """
    doors = [d[:3] for d in _unlinked_doors(job, meta, door_fids)]
    seeds = list(doors)
    for d in doors:
        for p in job.get('landings') or ():
            if math.dist(p[:2], d[:2]) <= DOOR_LANDING_REACH:
                seeds += [tuple(p), (d[0], d[1], p[2])]
    return seeds


def _unlinked_doors(job, meta, door_fids) -> list:
    """(x, y, z, ref) of the job's teleport doors, standing in its own cell, that `meta` gave no Door Triangle."""
    have = set((meta or {}).get('door_refs') or ())
    cell = job['cell_rec']
    gx, gy = cell.get('XCLC.X'), cell.get('XCLC.Y')
    rows = collect_doors((job['refr_recs'] or []) + (job.get('extra_door_refrs') or []), door_fids)
    return [(x, y, z, fid) for (x, y, z, _r, fid, teleport, _w) in rows
            if teleport and fid not in have
            and (gx in (None, '') or (int(x // CELL_SIZE), int(y // CELL_SIZE)) == (int(gx), int(gy)))]


def _convert(job: dict, geom_cache):
    """(navm_bytes, meta) for one job, built against `geom_cache` (None: no cache)."""
    return convert_job(job, _BASE_MODEL_BY_FID, _DOOR_FIDS, geom_cache)


def _attempt(job: dict, geom_cache) -> tuple:
    """(navm_bytes, meta) for one job; an exception comes back as meta['error']."""
    try:
        navm_bytes, meta = _convert(job, geom_cache)
        if job.get('verify') and meta and meta.get('geom_cached'):
            return _verify_against_cache(job, (navm_bytes, meta))
        return navm_bytes, meta
    except Exception as e:
        import traceback
        return None, {'error': f'{type(e).__name__}: {e}',
                      'traceback': traceback.format_exc()}


def _failed(result: tuple) -> bool:
    """True when an attempt raised rather than built (or found nothing to build)."""
    return result[0] is None and bool((result[1] or {}).get('error'))


def _drop_entry(job: dict, geom_cache) -> None:
    """Delete this cell's cache entry, so geometry that failed is never served or adopted."""
    try:
        os.remove(geom_cache_path(geom_cache, *job['key']))
    except OSError:
        pass


def run_job(job: dict):
    """ProcessPool task: convert one PGRD to (navm_bytes, meta).

    Exceptions are RETURNED in meta['error'], never printed: workers run under
    pythonw.exe.  A failed cell drops its cache entry and is built once more,
    so a bad entry never outlives the run.  job['verify'] double-builds and
    compares (the PARENT picks which cells); job['prove'] builds with NO cache.
    See: docs/commentary/tes5_import_navmesh.md#a-failed-cell-keeps-no-cache-entry
    """
    cache = None if job.get('prove') else _GEOM_CACHE
    result = _attempt(job, cache)
    if cache and _failed(result):
        _drop_entry(job, cache)
        result = _attempt(job, cache)
        if _failed(result):
            _drop_entry(job, cache)
    return job['key'], result
