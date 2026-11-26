"""Asset conversion pipeline: BSA extraction → mesh/texture conversion → output.

Three separate callable steps:

  extract_bsas(source_file, data_path, extract_dir, force)
      Pull all assets from BSA archives into extract_dir/<source_name>./

  convert_meshes(source_file, extract_dir, output_dir)
      Convert NIFs and copy textures into output/<source_name>/.

  convert_speedtrees(source_file, extract_dir, output_dir)
      Convert SpeedTree `.spt` files into NIFs under
      `output/<source_name>/meshes/tes4/speedtrees`.

  convert_sounds(source_file, extract_dir, output_dir)
      Convert sound files from extracted BSA into XWM and move into output_dir/<source_name>/sound/tes4/.
      Assumes extract_bsas has already been run."""
import os
import shutil
import tempfile
from pathlib import Path

from core.plugin_masters import masters_from_export_header
from asset_convert.game_paths import (ascii_dest, namespace_for,
                                       owns_namespace, remove_non_ascii,
                                       set_namespace)
from asset_convert.sources import bsa_extract, source_registry
from asset_convert.nif import grass_profile
from asset_convert.nif import morroblivion_textures
from asset_convert.character import hair_pipeline
from asset_convert.texture import landscape_normals
from asset_convert.collision import mesh_scan_fragments
from asset_convert.nif import magic_art, nif_batch
from asset_convert.nif.shaders import master_texture_roots
from asset_convert.speedtree import spt_converter
from asset_convert.texture import texture_prune
from asset_convert.character.morrowind_armor import assemble_armor
from asset_convert.character import wearable_plan
from asset_convert.collision import clutter_plan
from asset_convert.collision import resting_items_plan
from asset_convert.nif import door_plan
from asset_convert.nif import fixture_plan
from asset_convert.nif import furniture_travel
from asset_convert.hybrid_build import build_borrowed
from asset_convert.sources.hybrid_assets import MOD_NAME, is_hybrid
from asset_convert.texture.texture_repair import repair_texture_tree


# Shared-folder resolution lives in output_layout (one module, three
# resolvers, importable without the pipeline). An imported mod's plugins share
# ONE asset payload but keep their own record dumps; for a plugin that is not
# an imported mod both collapse to `export/<plugin>/`, the layout this module
# has always used.
from output_layout import (asset_root as _asset_root,
                           record_dir as record_dir,
                           plugin_out_root as _plugin_out_root)


def _out_root(output_dir, source_name, extract_dir=None):
    """Folder in output/ receiving this plugin's converted artefacts."""
    return _plugin_out_root(output_dir, source_name,
                            str(extract_dir) if extract_dir else None)


def extract_bsas(source_file, data_path, extract_dir='export', force=False):
    """Extract BSA archives for a plugin into extract_dir/<source_name>/.

    Args:
        source_file: Plugin filename (e.g. 'Oblivion.esm').
        data_path: Path to Oblivion Data directory.
        extract_dir: Root extraction directory (default: export).
        force: Force re-extraction even if already cached.

    Returns:
        dict from bsa_extract.extract_assets_for_file.
    """
    print("=" * 60)
    print("BSA Extraction")
    print("=" * 60)
    result = bsa_extract.extract_assets_for_file(
        source_file, data_path, Path(extract_dir), force=force
    )
    return result


_PARALLAX_NOTICE = """\
PARALLAX — READ THIS BEFORE PLAYING
===================================

This conversion was built with --parallax, so Oblivion's own parallax surfaces
(dungeon walls, rock, architecture) ship as Skyrim height maps.

YOU NEED ONE OF THESE INSTALLED:

  * Community Shaders  (verified in game with this conversion), or
  * ENB

WITHOUT ONE, THOSE SURFACES RENDER WRONG. Not flat -- wrong: the texture
visibly swims across the geometry as the camera moves. Tested on vanilla SSE,
and the "SSE Parallax Shader Fix" did NOT repair it. If you do not run
Community Shaders or an ENB, rebuild without --parallax; you lose the effect
and everything renders correctly.

WHAT WAS CONVERTED

Oblivion marks a surface for parallax per shape and keeps the height field in
the diffuse texture's alpha channel. Every shape carrying that mark whose
texture actually holds height data was rebuilt as a Skyrim heightmap shape,
with the height written out beside the diffuse as <name>_p.dds (BC4).

Shapes marked for parallax whose texture holds NO height data were left alone.
That is not a gap in the conversion: over half of Oblivion's flagged textures
ship no alpha channel at all, so Oblivion itself renders no parallax on them
either. Building a height map there would have invented data and produced the
swimming surface described above.
"""


def _write_parallax_notice(plugin_dir):
    """Ship the Community-Shaders requirement WITH the mod, not just in a repo
    README nobody downloading the output ever sees."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / 'PARALLAX-READ-ME.txt').write_text(
        _PARALLAX_NOTICE, encoding='utf-8')
    print('  Wrote PARALLAX-READ-ME.txt (Community Shaders / ENB required)')


# ---------------------------------------------------------------------------
# Asset namespace
# ---------------------------------------------------------------------------

def _activate_namespace(rec_dir) -> str:
    """Install this plugin's asset namespace before any path is rewritten.

    The Morroblivion texture map is armed here too: it rewrites the same paths,
    and like the namespace it must reach every spawned worker.
    See: docs/commentary/asset_convert_texture.md#per-game-asset-namespace
    See: docs/commentary/asset_convert_texture.md#morroblivion-texture-substitution
    """
    ns = namespace_for(rec_dir)
    set_namespace(ns)
    morroblivion_textures.activate(rec_dir)
    return ns


# ---------------------------------------------------------------------------
# Mesh and texture conversion
# ---------------------------------------------------------------------------

def _persist_mesh_manifests(mesh_stats, manifest_dir, partial: bool) -> None:
    """Persist the texture sets mesh conversion harvested, for later phases.

    The prune and the texture pass both run after this, possibly in a separate
    invocation. `partial` (a --mesh-subdirs run) MERGES rather than replaces:
    that run saw only part of the tree, and overwriting would tell the prune
    nothing outside those folders uses a texture, so it would delete the rest.

    See: docs/commentary/asset_convert_shader.md#detail-overlay-diffuses
    """
    for key, name in (('textures_used', texture_prune.MANIFEST_NAME),
                      ('overlay_diffuses',
                       texture_prune.OVERLAY_MANIFEST_NAME)):
        refs = set(mesh_stats.pop(key, set()))
        if partial:
            refs |= texture_prune.read_manifest(manifest_dir, name)
        texture_prune.write_manifest(manifest_dir, refs, name)


def _exported_members(export_root, plugin) -> list:
    """The exported plugins of `plugin`'s mod, which share one mesh tree.

    See: docs/commentary/asset_convert_nif.md#one-plan-per-mod
    """
    members = [m for m in source_registry.group_members(export_root, plugin)
               if (record_dir(export_root, m) / '_HEADER.txt').is_file()]
    return members or [plugin]


def _mesh_plan(rec_dirs: list) -> dict:
    """The per-mesh conversion plan the records in `rec_dirs` call for, together."""
    plan = {}
    for d in rec_dirs:
        wearable_plan.merge_sibling(plan, wearable_plan.build_plan(d))
    print(f"  Wearable variant plan: {len(plan)} meshes referenced by ARMO/CLOT")
    masses = {}
    for d in rec_dirs:
        for path, kg in clutter_plan.build_clutter_masses(d).items():
            masses[path] = max(masses.get(path, kg), kg)
    plan[clutter_plan.CLUTTER_KEY] = masses
    print(f"  Dynamic clutter plan: {len(masses)} item models")
    plan[door_plan.DOOR_KEY] = set().union(*map(door_plan.build_door_models, rec_dirs))
    plan[door_plan.REACH_KEY] = any(map(door_plan.places_morrowind_doors, rec_dirs))
    print(f"  Door animation plan: {len(plan[door_plan.DOOR_KEY])} door models"
          f"{', Morrowind-placed' if plan[door_plan.REACH_KEY] else ''}")
    plan[fixture_plan.FIXTURE_KEY] = set().union(
        *map(fixture_plan.build_fixture_models, rec_dirs))
    plan[fixture_plan.ANIMATED_KEY] = set().union(
        *map(fixture_plan.build_animated_models, rec_dirs))
    print(f"  Placed fixture plan: {len(plan[fixture_plan.FIXTURE_KEY])} scenery models")
    travels = {}
    for d in rec_dirs:
        for marker, travel in furniture_travel.sit_travels(d).items():
            travels.setdefault(marker, travel)
    plan[furniture_travel.SIT_TRAVEL_KEY] = travels
    print(f"  Sit travel plan: {len(travels)} marker ids with an authored enter animation")
    return plan


def mesh_plan(export_root, plugin) -> dict:
    """The conversion plan every member of `plugin`'s mod calls for, with its resting-items index."""
    members = _exported_members(export_root, plugin)
    if len(members) > 1:
        print(f"  Mesh plan from every member of the mod: {', '.join(members)}")
    plan = _mesh_plan([record_dir(export_root, m) for m in members])
    resting, stocked = resting_items_plan.write_index(export_root, members)
    plan[resting_items_plan.RESTING_KEY] = resting
    print(f"  Resting items plan: {stocked} fixture models share a cell "
          f"with an item")
    return plan


def _convert_mesh_tree(mesh_src, mesh_dst, asset_dir, plan,
                       mesh_subdirs, parallax, textures_only):
    """Run the NIF batch over `mesh_src`; return its stats dict.

    The wearable plan names which _0/_1/plain variants each mesh is actually
    referenced as -- without it the converter writes all three for every armor
    and clothing mesh and the plugin loads one or two.  `scan_dir` collects the
    bounds/collision entries the workers compute from the graphs they write;
    a previous run's fragments describe meshes this one replaces, so they are
    cleared first.
    See: docs/commentary/tes5_import_pipeline.md#producer-emitted-mesh-entries
    """
    mesh_scan_fragments.clear_fragments(asset_dir)
    return nif_batch.batch_convert(
        str(mesh_src), output_dir=str(mesh_dst),
        fix_textures=True, remap_skeleton=None,
        subdir_filter=mesh_subdirs,
        wearable_plan=plan,
        parallax=parallax,
        textures_only=textures_only,
        scan_dir=None if textures_only else str(asset_dir),
    )


def _convert_assembled(rec_dir, mesh_src: Path, mesh_dst: Path, plan: dict, options: tuple,
                       mesh_stats: dict) -> None:
    """Assemble Morrowind's worn armor in a temporary tree and convert it beside the plugin's meshes.

    `options` is (mesh_subdirs, parallax, textures_only); the result folds into `mesh_stats`.
    See: docs/commentary/asset_convert_armor.md#assembled-outside-the-export
    """
    mesh_subdirs, parallax, textures_only = options
    with tempfile.TemporaryDirectory(prefix='assembled_armor_') as tree:
        if not assemble_armor(rec_dir, mesh_src, tree):
            return
        result = nif_batch.batch_convert(
            tree, output_dir=str(mesh_dst), fix_textures=True, subdir_filter=mesh_subdirs, wearable_plan=plan,
            parallax=parallax, textures_only=textures_only,
            scan_dir=None if textures_only else str(mesh_src.parent),
            extra_textures=(str(mesh_src.parent / 'textures'), *master_texture_roots(mesh_src)))
    for key, value in result.items():
        if isinstance(value, int) and isinstance(mesh_stats.get(key, 0), int):
            mesh_stats[key] = mesh_stats.get(key, 0) + value
        elif isinstance(value, set):
            mesh_stats.setdefault(key, set()).update(value)


def convert_meshes(source_file, extract_dir='export', output_dir='output',
                   mesh_subdirs=None, parallax=False, textures_only=False,
                   skip_hair=False):
    """Convert extracted NIFs and copy textures into `output_dir/<source_name>/`.

    Needs extract_bsas run first. `mesh_subdirs` converts ONLY the NIFs under
    those meshes/ prefixes and skips every whole-tree pass and the texture
    copy. `parallax` ships height maps; `textures_only` ships only textures;
    `skip_hair` skips the hair pass. Assets come from the shared group tree,
    records from the plugin's own dump. Returns stats keyed
    'mesh_conversion', 'textures_copied', 'other_copied'.
    """
    extract_dir = Path(extract_dir)
    output_dir = Path(output_dir)
    source_name = Path(source_file).name
    asset_dir = _asset_root(extract_dir, source_name)
    rec_dir = record_dir(extract_dir, source_name)
    plugin_dir = _out_root(output_dir, source_name, extract_dir)
    ns = _activate_namespace(rec_dir)

    stats = {
        'mesh_conversion': {},
        'textures_copied': 0,
        'other_copied': 0,
    }

    # -----------------------------------------------------------------------
    # NIF Mesh Conversion
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("NIF Mesh Conversion")
    print("=" * 60)
    mesh_src = asset_dir / 'meshes'
    plan = mesh_plan(extract_dir, source_name)
    if mesh_src.exists():
        stats['mesh_conversion'] = _convert_mesh_tree(
            mesh_src, plugin_dir / 'meshes' / ns, asset_dir, plan,
            mesh_subdirs, parallax, textures_only)
        _convert_assembled(rec_dir, mesh_src, plugin_dir / 'meshes' / ns, plan,
                           (mesh_subdirs, parallax, textures_only), stats['mesh_conversion'])
        if parallax:
            _write_parallax_notice(plugin_dir)
        _persist_mesh_manifests(stats['mesh_conversion'], asset_dir,
                                bool(mesh_subdirs))
    else:
        print(f"  No meshes found at {mesh_src}")
        stats['mesh_conversion'] = {'converted': 0, 'skipped': 0, 'errors': 0}
    if mesh_subdirs:
        return stats
    stats['borrowed'] = build_borrowed(rec_dir, output_dir, plugin_dir, plan, parallax, textures_only)

    if not textures_only:
        _profile_hair_and_grass(
            rec_dir, plugin_dir, stats, skip_hair or not mesh_src.exists(),
            _master_meshes(rec_dir, output_dir, extract_dir))
    if mesh_src.exists() and not textures_only:
        _split_magic_art(rec_dir, mesh_src, plugin_dir / 'meshes' / ns, stats)

    # -----------------------------------------------------------------------
    # Copy Textures
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Copy Textures to Output")
    print("=" * 60)

    _copy_and_fix_textures(asset_dir, plugin_dir, ns, stats, rec_dir)
    _finish_output(rec_dir, plugin_dir, output_dir, ns, stats)
    return stats


def _finish_output(rec_dir, plugin_dir, output_dir, ns, stats):
    """Write the missing LTEX normals, then drop files an older build named non-ASCII."""
    checked, written = landscape_normals.ensure_ltex_normals(
        rec_dir, plugin_dir / 'textures', output_dir)
    stats['ltex_normals_written'] = written
    print(f"  LTEX normals: {checked} land textures, {written} flat normals "
          f"written for textures shipping none")
    stale = sum(remove_non_ascii(plugin_dir / sub / ns)
                for sub in ('meshes', 'textures'))
    print(f"  Non-ASCII names from an older build removed: {stale}")


def _master_meshes(rec_dir, output_dir, extract_dir) -> list:
    """Each direct master's converted meshes root, in header order."""
    return [_out_root(output_dir, name, extract_dir) / 'meshes'
            for name in masters_from_export_header(str(rec_dir))]


def _profile_hair_and_grass(rec_dir, plugin_dir, stats, skip_hair,
                            master_meshes=()):
    """Run the hair (unless `skip_hair`) and grass post-passes over the converted mesh tree.

    A grass model may be a master's converted mesh, found under `master_meshes`.
    """
    print("  Hair and grass...")
    if not skip_hair:
        stats['hair'] = hair_pipeline.run(rec_dir, plugin_dir / 'meshes')
    processed, modified, missing = grass_profile.run(
        rec_dir, plugin_dir / 'meshes', master_meshes,
        plugin_dir.parent / MOD_NAME / 'meshes' if is_hybrid(rec_dir) else None)
    stats['grass_profile'] = {
        'processed': processed, 'modified': modified, 'missing': missing}
    print(f"  Grass models: {processed} placed under landscape\\grass"
          + (f", {missing} missing" if missing else ""))


def _split_magic_art(rec_dir, mesh_src, mesh_dst, stats):
    """Derive the per-phase magic-effect meshes beside the converted tree."""
    stats['magic_art'] = magic_art.split_effect_meshes(rec_dir, mesh_src, mesh_dst)
    print(f"  Magic effect phase meshes: {stats['magic_art']['written']} written "
          f"from {stats['magic_art']['sources']} models "
          f"({stats['magic_art']['missing']} not converted)")


def _copy_and_fix_textures(asset_dir, plugin_dir, ns, stats, rec_dir):
    """Copy the texture tree, then repair what Skyrim reads differently.

    L8 glow maps become BGRA (Skyrim samples slot 2 as plain RGB, so an L8
    glow renders pure red), DXT1 landscape normals gain a real alpha mask,
    and a diffuse whose alpha became a `_p` height map drops to DXT1. Only the
    namespace's ROOT plugin writes the shared `default_n.dds`. Every pass runs
    AFTER the copy, so a re-copy cannot resurrect the originals and re-running
    is a no-op.
    See: docs/commentary/asset_convert_texture.md#landscape-normal-maps-dxt1-shiny
    """
    tex_src = asset_dir / 'textures'
    if not tex_src.exists():
        return
    tex_dst = plugin_dir / 'textures' / ns
    stats['textures_copied'] = _copy_tree(tex_src, tex_dst)
    print(f"  Textures: {stats['textures_copied']} files -> {tex_dst}")

    repair_texture_tree(tex_dst, stats, owns_namespace(rec_dir))


def convert_speedtrees(source_file, extract_dir='export', output_dir='output',
                       use_engine=True):
    """Convert SpeedTree `.spt` files into NIFs and place them under
    `output_dir/<source_name>/meshes/tes4/speedtrees`.

    `use_engine` (ON by default) takes branches from Oblivion's own SpeedTree
    code via the committed native harness; the Python generator is the
    per-tree FALLBACK when no Oblivion.exe is configured, the harness is
    missing, or a dump fails.  See asset_convert/speedtree/spt_engine_geom.py.
    """
    extract_dir = Path(extract_dir)
    output_dir = Path(output_dir)
    source_name = Path(source_file).name

    plugin_dir = _out_root(output_dir, source_name, extract_dir)

    spt_stats = {'spt_conversion': {'ok': 0, 'fail': 0, 'skip': 0}}
    spt_src = _asset_root(extract_dir, source_name) / 'trees'
    master_tree_dirs = [
        d for d in (_asset_root(extract_dir, name) / 'trees'
                    for name in masters_from_export_header(
                        str(record_dir(extract_dir, source_name))))
        if d.is_dir()]
    if spt_src.exists():
        ns = _activate_namespace(record_dir(extract_dir, source_name))
        spt_dst = plugin_dir / 'meshes' / ns / 'speedtrees'
        spt_stats['spt_conversion'] = spt_converter.convert_spt_directory(
            spt_src, spt_dst,
            export_dir=record_dir(extract_dir, source_name),
            master_tree_dirs=master_tree_dirs, use_engine=use_engine)
    else:
        print(f"  No trees/ directory found at {spt_src}")

    return spt_stats


def convert_sounds(source_file, extract_dir='export', output_dir='output',
                   ffmpeg_path='ffmpeg'):
    """Convert extracted sound files to XWM format.  Delegates to audio_converter.

    Args:
        source_file: Plugin filename (e.g. 'Oblivion.esm').
        extract_dir: Root extraction directory (default: export).
        output_dir:  Final output root.
        ffmpeg_path: Path to ffmpeg executable (default: 'ffmpeg' from PATH).

    Returns:
        dict with keys: converted, copied, failed, total.
    """
    from asset_convert.audio.audio_converter import convert_sounds as _ac_convert
    return _ac_convert(source_file, extract_dir=extract_dir,
                       output_dir=output_dir, ffmpeg_path=ffmpeg_path)


def _copy_tree(src, dst):
    """Copy a directory tree, returning file count."""
    count = 0
    for root, _dirs, files in os.walk(src):
        for fname in files:
            src_file = Path(root) / fname
            dst_file = ascii_dest(dst, src_file.relative_to(src))
            os.makedirs(dst_file.parent, exist_ok=True)
            shutil.copy2(str(src_file), str(dst_file))
            count += 1
    return count


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description='Asset pipeline: extract BSAs and/or convert assets')
    sub = parser.add_subparsers(dest='cmd')

    p_extract = sub.add_parser('extract', help='Extract BSA archives only')
    p_extract.add_argument('source_file')
    p_extract.add_argument('--data-path', required=True)
    p_extract.add_argument('--extract-dir', default='export')
    p_extract.add_argument('--force', action='store_true')

    p_convert = sub.add_parser('convert', help='Convert extracted assets (meshes + speedtrees)')
    p_convert.add_argument('source_file')
    p_convert.add_argument('--extract-dir', default='export')
    p_convert.add_argument('--output-dir', default='output')

    p_sounds = sub.add_parser('sounds', help='Copy sound files to output')
    p_sounds.add_argument('source_file')
    p_sounds.add_argument('--extract-dir', default='export')
    p_sounds.add_argument('--output-dir', default='output')

    args = parser.parse_args()
    if args.cmd == 'extract':
        extract_bsas(args.source_file, args.data_path,
                     extract_dir=args.extract_dir, force=args.force)
    elif args.cmd == 'convert':
        convert_meshes(args.source_file,
                       extract_dir=args.extract_dir, output_dir=args.output_dir)
        convert_speedtrees(args.source_file,
                           extract_dir=args.extract_dir, output_dir=args.output_dir)
    elif args.cmd == 'sounds':
        convert_sounds(args.source_file,
                    extract_dir=args.extract_dir, output_dir=args.output_dir)
    else:
        parser.print_help()

