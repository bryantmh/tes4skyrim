"""Generate ALL distant LOD, ONCE, into a standalone AutoConvertLOD mod.

LOD used to be a per-plugin pipeline step (`convert.py --lod-only -f X`) that
wrote into `output/<plugin>/`, followed by a separate "Merge Sibling LOD" pass
that rebaked whatever two plugins both claimed. That is three bakes of the same
ground: LOD tiles are FILES on a fixed grid keyed only by worldspace and
coordinate (`meshes/terrain/<wrld>/Objects/<wrld>.4.-32.-32.bto`), so every
plugin editing a worldspace generates the SAME tile paths. Converting four
siblings produced four rival copies of each shared tile, the mod manager's
install order silently picked a winner, and the merge pass then threw all of
them away and baked a fifth.

There is no per-plugin LOD here. For each worldspace the bake reads records
from the plugin that OWNS it and applies every other selected plugin as an
overlay in load order — which is what the generators have always supported, and
what makes one tile correct for the whole load order. Each tile is written
exactly once, into one mod folder the user installs like any other.

Because the output is a single standalone mod, nothing overwrites anything:
there are no rival copies to resolve, so there is no contested-tile maths, no
`only_cells` restriction and no merge stage. Install it after the plugins it
covers.

Order is lowest priority FIRST — the last plugin listed wins any reference two
of them both change. The default is the user's plugins.txt order, with anything
it does not list appended at the bottom alphabetically but never before its own
masters (see sibling_lod.create_lod_order).

Usage:
  python tools/release/create_lod.py                                  # everything
  python tools/release/create_lod.py --plugins Oblivion.esm Tamriel.esp
  python tools/release/create_lod.py --worldspaces TES4Tamriel
  python tools/release/create_lod.py --dry-run                        # plan only
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from core.subprocess_flags import configure_multiprocessing
from core.process_job import create_pool_job
from core.heavy_lock import hold_heavy_lock
from output_layout import assets_for

configure_multiprocessing()
create_pool_job()


def _supplier_asset_dirs(names, out_root, export_root, _out_root) -> list:
    """Converted output roots holding meshes/textures a tile may need."""
    return [_out_root(out_root, n, export_root) for n in names
            if _out_root(out_root, n, export_root).is_dir()]


def _all_plugin_dirs(out_root: Path, lod_dir: Path) -> list:
    """Every converted output tree that ships textures, the LOD mod excluded.

    Texture lookup must not follow the dependency chain: a masterless patch
    ships the land textures another plugin's terrain names, and the owner's
    own MASTER ships the shared flat normal.
    See: docs/commentary/asset_convert_terrain.md#terrain-lod-texture-lookup
    """
    return sorted(
        d for d in Path(out_root).iterdir()
        if d.is_dir() and d != lod_dir
        and any((d / n).is_dir() for n in ('textures', 'Textures')))


def _supplier_overlay_dirs(names, out_root, export_root, _out_root,
                           record_dir) -> list:
    """Overlay-manifest dirs, index-aligned with `_supplier_asset_dirs`."""
    return [assets_for(record_dir(export_root, n)) for n in names
            if _out_root(out_root, n, export_root).is_dir()]


def _plan_jobs(wanted, owners, plugins, touched, out_root, export_root,
               _out_root, master_chain, _worldspace_fid) -> list:
    """One (edid, owner, esm, overlays, contributors, suppliers) job per world.

    `contributors` are overlaid as RECORDS and are scoped to plugins that
    actually place something here; `suppliers` is every dependency-legal
    plugin, because one that places nothing can still define the base objects
    another plugin's references point at.
    See: docs/commentary/asset_convert_terrain.md#lod-suppliers-vs-contributors
    """
    jobs = []
    for edid in wanted:
        owner = owners.get(edid)
        if owner is None:
            print(f"  '{edid}': no selected plugin supplies terrain for it; "
                  f"skipping")
            continue
        owner_esm = _out_root(out_root, owner, export_root) / owner
        if not owner_esm.is_file():
            print(f"  '{edid}': {owner} has no converted ESM; skipping")
            continue

        contributors = [n for n in plugins if n != owner
                        and owner in master_chain(n, export_root, plugins)]
        suppliers = list(contributors)

        wrld_fid = _worldspace_fid(owner_esm, edid)
        if wrld_fid is not None:
            scoped = [n for n in contributors
                      if touched.get(n) is None or wrld_fid in touched[n]]
            if len(scoped) != len(contributors):
                skipped = [n for n in contributors if n not in scoped]
                print(f"  '{edid}': {len(skipped)} dependent(s) place nothing "
                      f"here, not overlaid ({', '.join(skipped)})")
            contributors = scoped

        overlays = [_out_root(out_root, n, export_root) / n
                    for n in contributors
                    if (_out_root(out_root, n, export_root) / n).is_file()]
        jobs.append((edid, owner, owner_esm, overlays, contributors,
                     suppliers))
    return jobs


def _use_owner_namespace(export_root: str, owner: str) -> None:
    """Install `owner`'s asset namespace for the job about to bake."""
    from asset_convert.game_paths import namespace_for, set_namespace
    from output_layout import record_dir
    set_namespace(namespace_for(record_dir(export_root, owner)))


def _clear_stale_tiles(lod_dir, edid: str) -> int:
    """Delete this worldspace's tiles from a previous run.

    The bake writes only the tiles it produces THIS time, so a tile an earlier
    run emitted -- at coordinates this selection no longer covers, or from a
    plugin since deselected -- would otherwise survive as an orphan and still
    ship. Scoped to this worldspace's own tile names, so a run covering several
    never deletes a sibling's fresh output and the shared, coordinate-free
    object .nifs are untouched.
    """
    stale = 0
    for sub in ("meshes/terrain", "textures/terrain"):
        d = lod_dir / sub / edid
        if not d.is_dir():
            continue
        for f in d.rglob(f"{edid}.*"):
            if f.is_file():
                f.unlink()
                stale += 1
    return stale


def _bake_worldspace(job, ctx) -> bool:
    """Generate one worldspace's object and terrain LOD.

    `ctx` holds what every job shares: the output roots and the two
    generators. The namespace is installed here because ONE process bakes
    every plugin's worldspaces.
    See: docs/commentary/asset_convert_terrain.md#write-lodgen-input-master-modes
    """
    edid, owner, owner_esm, overlays, contributors, suppliers = job
    lod_dir, out_root, export_root = ctx['lod_dir'], ctx['out_root'], ctx['export_root']
    print("-" * 54)
    print(f"  {edid}  (records: {owner})")
    print("-" * 54)
    _use_owner_namespace(export_root, owner)

    stale = _clear_stale_tiles(lod_dir, edid)
    if stale:
        print(f"  Cleared {stale} tile(s) from a previous run")

    asset_dirs = ctx['supplier_asset_dirs']([owner] + suppliers)
    overlay_dirs = ctx['supplier_overlay_dirs']([owner] + suppliers)
    texture_dirs = _all_plugin_dirs(out_root, lod_dir)

    cloud_rel = ctx['merge_cloud_bank'](out_root, lod_dir, edid, owner,
                                        contributors, export_root)
    if cloud_rel:
        print(f"  World-map cloud bank -> {cloud_rel}")

    print("  Generating object LOD...")
    ok = ctx['generate_lod'](
        esm_path=owner_esm,
        output_dir=lod_dir,
        worldspace_edid=edid,
        master_dirs=None,
        master_mesh_dirs=asset_dirs,
        master_texture_dirs=texture_dirs,
        overlay_paths=overlays,
        only_cells=None,
        far_nif_dirs=asset_dirs,
        overlay_manifest_dirs=overlay_dirs,
    )

    print("  Generating terrain LOD...")
    ok_terrain = ctx['generate_terrain_lod'](
        esm_path=owner_esm,
        output_dir=lod_dir,
        worldspace_edid=edid,
        overlay_paths=overlays,
        only_cells=None,
        extra_texture_roots=[ctx['lod_textures_root'](Path(d))
                             for d in texture_dirs],
    )
    print()
    return bool(ok and ok_terrain)


def _parse_args():
    """Parse the command line for a create-LOD run."""
    ap = argparse.ArgumentParser(
        description="Generate every plugin's distant LOD once, into a "
                    "standalone AutoConvertLOD mod.")
    ap.add_argument("--output-dir", metavar="PATH",
                    help="Output directory (default: output/ in project root)")
    ap.add_argument("--plugins", nargs="+", metavar="PLUGIN",
                    help="Plugins to include, LOWEST PRIORITY FIRST. The last "
                         "one listed wins a contested reference. Default: "
                         "every converted plugin, in plugins.txt order with "
                         "unlisted plugins appended.")
    ap.add_argument("--worldspaces", nargs="+", metavar="EDID",
                    help="Only generate these worldspaces (default: every "
                         "worldspace the source shipped LOD for)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan and generate nothing")
    return ap.parse_args()


def _select_plugins(args, available, export_root, create_lod_order) -> list:
    """Return the selected plugins for this run, lowest priority first.

    An explicit `--plugins` order is honoured verbatim: it IS the conflict
    resolution, and the GUI dialog lets the user arrange it by hand. Names with
    no converted output are dropped rather than failing the run, so a stale
    saved selection never blocks the plugins that ARE built.
    """
    if not args.plugins:
        return create_lod_order(available, export_root)
    plugins = [p for p in args.plugins if p in available]
    missing = [p for p in args.plugins if p not in available]
    if missing:
        print(f"  Not converted, skipping: {', '.join(missing)}")
    return plugins


def _touched_worldspaces(plugins, out_root, export_root, _out_root,
                         touched_worldspace_fids) -> dict:
    """Map each plugin to the worldspace FormIDs it places records in.

    None means "unreadable, so never filter this plugin out". Owning a
    worldspace is not the same as editing it, and an overlay contributing
    nothing still costs a full ESM parse per worldspace in BOTH generators, so
    this is scanned once per plugin and reused for every job.
    """
    touched = {}
    for name in plugins:
        esm = _out_root(out_root, name, export_root) / name
        touched[name] = None
        if esm.is_file():
            try:
                touched[name] = touched_worldspace_fids(esm)
            except OSError:
                pass
    return touched


def _worldspace_fid_resolver(wanted, find_worldspace_fid, formid_remap_table):
    """Return a memoised (esm, edid) -> NORMALIZED WRLD FormID lookup.

    One owner's read resolves every worldspace in `wanted` while its bytes are
    in hand. See: docs/commentary/asset_convert_terrain.md#create-lod-run-planning
    """
    cache: dict = {}

    def _worldspace_fid(esm: Path, edid: str):
        key = (str(esm).lower(), edid.lower())
        if key in cache:
            return cache[key]
        gmap = formid_remap_table(esm)
        raw = esm.read_bytes()
        try:
            for w in wanted:
                k = (str(esm).lower(), w.lower())
                if k not in cache:
                    f = find_worldspace_fid(raw, len(raw), w)
                    cache[k] = (None if f is None
                                else gmap[f >> 24] | (f & 0x00FFFFFF))
        finally:
            del raw
        return cache.get(key)

    return _worldspace_fid


def main() -> int:
    """Bake every selected worldspace's LOD into the AutoConvertLOD mod.

    Returns 0 when every job succeeded or there was nothing to do, 1 when any
    bake reported an error. A real bake first waits for any other heavy job.
    See: docs/commentary/asset_convert_terrain.md#create-lod-run-planning
    """
    args = _parse_args()

    from asset_convert.lod.lod_gen import (generate_lod,
                                       textures_root as _lod_textures_root)
    from asset_convert.lod.terrain_lod import generate_terrain_lod
    from asset_convert.lod.sibling_lod import (_out_root, record_dir,
                                           converted_plugins, create_lod_order,
                                           lod_worldspaces, owner_map,
                                           merge_cloud_bank, master_chain,
                                           touched_worldspace_fids,
                                           drop_staged_meshes,
                                           LOD_DIR_NAME)
    from asset_convert.lod.terrain_lod import find_worldspace_fid
    from asset_convert.lod.esm_scan import formid_remap_table

    out_root = (Path(args.output_dir) if args.output_dir
                else SCRIPT_DIR / "output")
    export_root = SCRIPT_DIR / "export"
    lod_dir = out_root / LOD_DIR_NAME

    _print_header(out_root, lod_dir, drop_staged_meshes(lod_dir))
    plugins = _select_plugins(args, converted_plugins(out_root), export_root,
                              create_lod_order)
    if not plugins:
        print("  No converted plugin to generate LOD for.")
        return 0

    wanted = (list(args.worldspaces) if args.worldspaces
              else lod_worldspaces(plugins, export_root, out_root))
    _print_scope(plugins, wanted)

    touched = _touched_worldspaces(plugins, out_root, export_root, _out_root,
                                   touched_worldspace_fids)
    owners = owner_map(wanted, plugins, export_root, out_root)

    jobs = _plan_jobs(wanted, owners, plugins, touched, out_root, export_root,
                      _out_root, master_chain,
                      _worldspace_fid_resolver(wanted, find_worldspace_fid,
                                               formid_remap_table))

    if not jobs:
        print("Nothing to generate.")
        return 0

    _print_plan(jobs)
    if args.dry_run:
        print("Dry run - nothing generated.")
        return 0
    hold_heavy_lock("create_lod.py " + " ".join(sys.argv[1:]))

    ctx = {
        'lod_dir': lod_dir, 'out_root': out_root, 'export_root': export_root,
        'generate_lod': generate_lod,
        'generate_terrain_lod': generate_terrain_lod,
        'merge_cloud_bank': merge_cloud_bank,
        'lod_textures_root': _lod_textures_root,
        'supplier_asset_dirs': lambda names: _supplier_asset_dirs(
            names, out_root, export_root, _out_root),
        'supplier_overlay_dirs': lambda names: _supplier_overlay_dirs(
            names, out_root, export_root, _out_root, record_dir),
    }
    return _report(all([_bake_worldspace(job, ctx) for job in jobs]), lod_dir)


def _print_header(out_root, lod_dir, swept: int) -> None:
    """The run's banner, and how many stale staged meshes were swept."""
    print("=" * 54)
    print("  CREATE LOD")
    print("=" * 54)
    print(f"  Output dir: {out_root}")
    print(f"  LOD mod:    {lod_dir}")
    if swept:
        print(f"  Swept {swept} stale staged mesh file(s) from the LOD mod")


def _print_scope(plugins: list, wanted: list) -> None:
    """The plugins in priority order, then the worldspaces to bake."""
    print(f"  Plugins ({len(plugins)}, lowest priority first):")
    for i, name in enumerate(plugins, 1):
        win = "  <- wins contested references" if i == len(plugins) else ""
        print(f"    {i}. {name}{win}")
    print(f"  Worldspaces ({len(wanted)}): {', '.join(wanted) or '(none)'}")
    print()


def _print_plan(jobs: list) -> None:
    """Each worldspace's owner and the overlays baked on top of it."""
    print("  Plan:")
    for edid, owner, _esm, overlays, contributors, _suppliers in jobs:
        print(f"    {edid}: records from {owner}, "
              f"{len(overlays)} overlay(s) on top"
              + (f" ({', '.join(contributors)})" if contributors else ""))
    print()


def _report(ok_all: bool, lod_dir) -> int:
    """Print how the bake ended; 0 when every job succeeded, else 1."""
    print("-" * 54)
    if ok_all:
        print(f"LOD written to {lod_dir}")
        print("Install it AFTER the plugins it covers.")
    else:
        print("Create LOD finished with errors (see above).")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
