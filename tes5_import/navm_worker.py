"""Process-pool worker for parallel PGRD→NAVM navmesh generation.

This is a DELIBERATELY light module.  On Windows the process pool uses `spawn`,
so every worker re-imports the worker's module at start-up.  Importing the full
`import_main` (which pulls in the dialogue/creature/script/asset pipelines) in
each child cost several GB of RSS per process and exhausted RAM, killing workers
with BrokenProcessPool.  Depending only on `pgrd_to_navm` (which itself imports
just text_reader/writer, and loads scipy/shapely lazily) keeps each child small.

FormIDs are pre-assigned in the parent (job['navm_fid']) so no PluginWriter — an
unpicklable, shared-state object — has to cross the process boundary.

The Havok collision cache is a module global in `asset_convert.collision_extract`;
a spawned child does NOT inherit the parent's loaded copy, so each worker loads it
once from disk in the pool initializer.  Without this, every cell would voxelize
an empty world and emit no navmesh at all.
"""

from .pgrd_to_navm import convert_PGRD

# Per-worker read-only carving context, populated by _init in each child.
_BASE_MODEL_BY_FID: dict = {}
_DOOR_FIDS: set = set()
_GEOM_CACHE: tuple = None


def init_worker(base_model_by_fid: dict, door_fids: set, collision_cache: str,
                formid_offset: int = 0, geom_cache: tuple = None,
                injected_formids: dict = None):
    """ProcessPool initializer: stash context; load the collision cache.

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
      - `collision_extract._COLLISION` — the per-mesh Havok collision soups the
        navmesh is voxelized from.  Without it every cell has no geometry and
        produces no navmesh at all.
    """
    global _BASE_MODEL_BY_FID, _DOOR_FIDS, _GEOM_CACHE
    _BASE_MODEL_BY_FID = base_model_by_fid
    _DOOR_FIDS = door_fids
    _GEOM_CACHE = geom_cache
    from .text_reader import set_formid_index_offset, set_injected_formids
    set_formid_index_offset(formid_offset)
    set_injected_formids(injected_formids or {})
    if collision_cache:
        from asset_convert.collision_extract import load_collision
        load_collision(collision_cache, quiet=True)


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
            geom_cache=_GEOM_CACHE,
            extra_door_refrs=job.get('extra_door_refrs'),
        )
    except Exception as e:  # noqa: BLE001 — must not kill the pool
        print(f"  ERROR generating navmesh for cell {job['key'][0]:08X}: {e}")
        return job['key'], (None, None)
