"""The import's navmesh phase as one call: generate every navmesh, link the exterior seams, split the
interiors into one NAVM per walkable piece.

The import and cellview both run it, so what cellview shows is what the plugin ships.
See: docs/commentary/tes5_import_pipeline.md#phase-4-navmesh-post-passes
"""

import itertools
import os
import pickle
import time

from core.navmesh_options import set_navmesh_plugin
from output_layout import assets_for
from ..base.master_export import MasterExport
from ..base.navmesh_pins import use_plugin
from ..base.text_reader import get_formid
from . import pool
from .edge_links import build_edge_links
from .lattice.build import set_activators
from .split import split_disconnected_interiors


def _dump(cache) -> None:
    """Write the generated cache to TESCONV_DUMP_NAVM_CACHE when it names a file (a debug hook)."""
    path = os.environ.get('TESCONV_DUMP_NAVM_CACHE', '').strip()
    if path:
        with open(path, 'wb') as fh:
            pickle.dump(cache, fh, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"  [debug] navm_cache dumped to {path} ({len(cache)} entries)")


def _link(cache, master_index, master_navms) -> list:
    """Stitch the built exterior navmeshes across cell seams, masters' included; the relinked masters."""
    t0 = time.time()
    relinked = []
    build_edge_links(cache, master_index=master_index, master_navms=master_navms, relinked_masters=relinked)
    print(f"    Edge links: {time.time() - t0:.1f}s")
    return relinked


def _split(cache, writer) -> None:
    """Split each disconnected interior navmesh into one NAVM per piece."""
    t0 = time.time()
    n_split = split_disconnected_interiors(cache, writer) if cache else 0
    print(f"    Interior split: {time.time() - t0:.1f}s")
    if n_split:
        print(f"  Navmesh split: {n_split} interior meshes split into per-component NAVMs")


def _kept(rec, here) -> bool:
    """Whether `rec` can change a build whose placed FormIDs are `here`."""
    return not rec.get('ParentCELL') or bool(rec.get('XTEL.Door')) or get_formid(rec, 'FormID') in here


def _only_interiors(by_type, master_export, interiors) -> tuple:
    """(records, master export) cut to what building only `interiors` (CELL FormIDs) reads: every record
    placed in another cell goes, except a teleport door, whose traveller may land in one of them, and a
    pathgrid (whether the plugin has any decides whether it is navmeshed at all).  A FormID with any
    version placed in them keeps every version, so an override moving a master's ref out still wins.
    The lists are fresh, so the build's additions never reach the caller's."""
    masters = list(master_export.items()) if master_export else []
    here = {get_formid(r, 'FormID') for r in itertools.chain(
        (r for recs in by_type.values() for r in recs), (r for _k, r in masters))
            if r.get('ParentCELL') and get_formid(r, 'ParentCELL') in interiors}
    own = {sig: [r for r in recs if _kept(r, here)] for sig, recs in by_type.items()}
    own['CELL'] = [c for c in by_type.get('CELL', []) if get_formid(c, 'FormID') in interiors]
    own['PGRD'] = list(by_type.get('PGRD', ()))
    if master_export:
        master_export = MasterExport((k, r) for k, r in masters if _kept(r, here))
    return own, master_export


def build_navmeshes(by_type, writer, ctx, export_dir, plugin, interiors=None) -> tuple:
    """(navm_cache, base_model_by_fid, door_fids, relinked master navmeshes) for one plugin.

    `ctx` is the import's override context or None; `plugin` names the pin file the build reads ('' for
    none).  `interiors`, a set of interior CELL FormIDs, builds only those and leaves the geometry cache
    unstamped.
    """
    master_index = getattr(ctx, 'master_index', None) if ctx else None
    master_export = getattr(ctx, 'master_export', None) if ctx else None
    if interiors is not None:
        by_type, master_export = _only_interiors(by_type, master_export, interiors)
    base_model = pool.build_base_model_index(by_type, master_export)
    door_fids = pool.build_door_fid_set(by_type, master_export)
    acti_fids = pool.build_activator_fid_set(by_type, master_export)
    set_activators(acti_fids)
    master_navms = pool.master_navm_grid(master_export, master_index)
    if plugin:
        use_plugin(plugin, os.path.basename(str(assets_for(export_dir))))
    else:
        set_navmesh_plugin('')
    cache = pool.precompute_navmeshes(
        by_type, writer, base_model, door_fids, collision_cache=pool.collision_cache_chain(export_dir),
        master_index=master_index, master_export=master_export, activator_fids=acti_fids,
        master_navms=master_navms, stamp=interiors is None)
    _dump(cache)
    relinked = _link(cache, master_index, master_navms if cache else {})
    _split(cache, writer)
    return cache, base_model, door_fids, relinked
