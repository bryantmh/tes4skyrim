"""The import's navmesh phase as one call: generate every navmesh, link the exterior seams, split the
interiors into one NAVM per walkable piece.

The import and cellview both run it, so what cellview shows is what the plugin ships.
See: docs/commentary/tes5_import_pipeline.md#phase-4-navmesh-post-passes
"""

import os
import pickle
import time

from core.navmesh_options import set_navmesh_plugin
from output_layout import assets_for
from ..base.navmesh_pins import use_plugin
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


def build_navmeshes(by_type, writer, ctx, export_dir, plugin) -> tuple:
    """(navm_cache, base_model_by_fid, door_fids, relinked master navmeshes) for one plugin.

    `ctx` is the import's override context or None; `plugin` names the pin file the build reads ('' for
    none).
    """
    master_index = getattr(ctx, 'master_index', None) if ctx else None
    master_export = getattr(ctx, 'master_export', None) if ctx else None
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
        master_navms=master_navms)
    _dump(cache)
    relinked = _link(cache, master_index, master_navms if cache else {})
    _split(cache, writer)
    return cache, base_model, door_fids, relinked
