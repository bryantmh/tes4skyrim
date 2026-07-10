"""Process-pool worker for parallel PGRD→NAVM navmesh generation.

This is a DELIBERATELY light module.  On Windows the process pool uses `spawn`,
so every worker re-imports the worker's module at start-up.  Importing the full
`import_main` (which pulls in the dialogue/creature/script/asset pipelines) in
each child cost several GB of RSS per process and exhausted RAM, killing workers
with BrokenProcessPool.  Depending only on `pgrd_to_navm` (which itself imports
just text_reader/writer, and loads scipy/shapely lazily) keeps each child small.

FormIDs are pre-assigned in the parent (job['navm_fid']) so no PluginWriter — an
unpicklable, shared-state object — has to cross the process boundary.

The mesh-footprint silhouette cache is a module global in `mesh_footprints`; a
spawned child does NOT inherit the parent's loaded copy, so each worker loads it
once from disk in the pool initializer.  Without this, obstacle carving would
silently no-op in the children.
"""

from .pgrd_to_navm import convert_PGRD

# Per-worker read-only carving context, populated by _init in each child.
_BASE_MODEL_BY_FID: dict = {}
_DOOR_FIDS: set = set()


def init_worker(base_model_by_fid: dict, door_fids: set, footprints_cache: str,
                bounds_cache: str = '', formid_offset: int = 0):
    """ProcessPool initializer: stash carving context; load carving caches.

    Runs once per worker process.  A spawned child does NOT inherit the parent's
    module-global state, so everything convert_PGRD relies on must be rebuilt
    here in each child:

      - `text_reader._formid_index_offset` — the load-order master-index shift
        get_formid() applies (e.g. +1 for Oblivion.esm behind Skyrim.esm).  If
        this is left at its default 0, every FormID convert_PGRD reads
        (PathingCell ParentCELL/ParentWRLD, door REFR links, ONAM base objects)
        keeps master index 0x00 instead of the plugin's real index.  The engine
        then can't resolve the navmesh's parent cell at load and null-derefs in
        Hook_NavMeshLoad.  MUST be set before any get_formid() call.
      - `mesh_footprints._FOOTPRINTS` (object silhouettes)   → footprints_cache
      - `mesh_bounds._MESH_BOUNDS`   (object AABBs)           → bounds_cache
        `_build_exclusion_zones` calls get_mesh_obnd() as its size/height gate
        AND footprint fallback, so if bounds are missing it returns [] for every
        ref and NO obstacles (furniture, pillars, WALLS) get carved.
    """
    global _BASE_MODEL_BY_FID, _DOOR_FIDS
    _BASE_MODEL_BY_FID = base_model_by_fid
    _DOOR_FIDS = door_fids
    from .text_reader import set_formid_index_offset
    set_formid_index_offset(formid_offset)
    if footprints_cache:
        from .mesh_footprints import load_mesh_footprints
        load_mesh_footprints(footprints_cache, quiet=True)
    if bounds_cache:
        from .mesh_bounds import load_mesh_bounds
        load_mesh_bounds(bounds_cache, quiet=True)


def run_job(job: dict):
    """ProcessPool task: convert one PGRD to (navm_bytes, meta).

    Errors are swallowed to (None, None) so a single bad cell can't abort the
    whole ex.map batch (the message is prefixed so it's greppable in logs).
    """
    try:
        return job['key'], convert_PGRD(
            job['pgrd_rec'],
            land_rec=job['land_rec'],
            cell_rec=job['cell_rec'],
            refr_recs=job['refr_recs'],
            base_model_by_fid=_BASE_MODEL_BY_FID,
            door_fids=_DOOR_FIDS,
            navm_fid=job['navm_fid'],
        )
    except Exception as e:  # noqa: BLE001 — must not kill the pool
        print(f"  ERROR generating navmesh for cell {job['key'][0]:08X}: {e}")
        return job['key'], (None, None)
