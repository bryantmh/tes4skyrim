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
from pathlib import Path

from asset_convert.game_paths import (namespace_for, owns_namespace,
                                       set_namespace)
from asset_convert.sources import bsa_extract
from asset_convert.nif import grass_profile
from asset_convert.character import hair_pipeline
from asset_convert.texture import landscape_normals
from asset_convert.texture import luminance_textures
from asset_convert.collision import mesh_scan_fragments
from asset_convert.nif import nif_batch
from asset_convert.speedtree import spt_converter
from asset_convert.texture import texture_prune
from asset_convert.character.morrowind_armor import assemble_armor
from asset_convert.character import wearable_plan
from asset_convert.collision import clutter_plan


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

    See: docs/commentary/asset_convert_texture.md#per-game-asset-namespace
    """
    ns = namespace_for(rec_dir)
    set_namespace(ns)
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


def _convert_mesh_tree(mesh_src, mesh_dst, asset_dir, rec_dir, mesh_subdirs,
                       parallax, textures_only):
    """Run the NIF batch over `mesh_src`; return its stats dict.

    The wearable plan names which _0/_1/plain variants each mesh is actually
    referenced as -- without it the converter writes all three for every armor
    and clothing mesh and the plugin loads one or two.  `scan_dir` collects the
    bounds/collision entries the workers compute from the graphs they write;
    a previous run's fragments describe meshes this one replaces, so they are
    cleared first.
    See: docs/commentary/tes5_import_pipeline.md#producer-emitted-mesh-entries
    """
    plan = wearable_plan.build_plan(rec_dir)
    print(f"  Wearable variant plan: {len(plan)} meshes referenced by "
          f"ARMO/CLOT")
    masses = clutter_plan.build_clutter_masses(rec_dir)
    plan[clutter_plan.CLUTTER_KEY] = masses
    print(f"  Dynamic clutter plan: {len(masses)} item models")
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


def convert_meshes(source_file, extract_dir='export', output_dir='output',
                   mesh_subdirs=None, parallax=False, textures_only=False):
    """Convert extracted NIFs and copy textures into `output_dir/<source_name>/`.
    Assumes BSA extraction has already been run (extract_bsas).

    Args:
        source_file:  Plugin filename (e.g. 'Oblivion.esm').
        extract_dir:  Root extraction directory (default: export).
        output_dir:   Final output root (files placed under output_dir/<source_name>/).
        mesh_subdirs: Optional list of root mesh subfolders to include (e.g.
                      ['architecture', 'clutter']). None means all subfolders.
        parallax:     Carry Oblivion's parallax across as Skyrim height maps.
                      Off by default — see asset_convert/texture/parallax.py; the
                      output needs Community Shaders or ENB.
        textures_only: Read and analyse the meshes, ship none of them; only the
                      textures (with their `_p` height maps) go to output.  For
                      PGPatcher, which patches meshes across the player's whole
                      load order — see nif_batch.batch_convert.

    Returns a dict with keys: 'mesh_conversion', 'textures_copied', 'other_copied'.
    """
    extract_dir = Path(extract_dir)
    output_dir = Path(output_dir)
    source_name = Path(source_file).name
    # Plugins imported together from one archive share ONE asset tree, so the
    # meshes/textures come from the group folder while the record dump stays
    # per plugin (source_registry.asset_root / record_dir).
    asset_dir = _asset_root(extract_dir, source_name)
    rec_dir = record_dir(extract_dir, source_name)
    plugin_dir = _out_root(output_dir, source_name, extract_dir)
    ns = _activate_namespace(rec_dir)

    stats = {
        'mesh_conversion': {},
        'textures_copied': 0,
        'other_copied': 0,
    }

    # Build bookkeeping, not a shipped asset -- see texture_prune.MANIFEST_NAME.
    # Tracks the SHARED asset tree, so it belongs beside the assets.
    mesh_manifest_dir = asset_dir

    # -----------------------------------------------------------------------
    # NIF Mesh Conversion
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("NIF Mesh Conversion")
    print("=" * 60)
    mesh_src = asset_dir / 'meshes'
    assemble_armor(rec_dir, mesh_src)
    if mesh_src.exists():
        stats['mesh_conversion'] = _convert_mesh_tree(
            mesh_src, plugin_dir / 'meshes' / ns, asset_dir, rec_dir,
            mesh_subdirs, parallax, textures_only)
        if parallax:
            _write_parallax_notice(plugin_dir)
        _persist_mesh_manifests(stats['mesh_conversion'], mesh_manifest_dir,
                                bool(mesh_subdirs))
    else:
        print(f"  No meshes found at {mesh_src}")
        stats['mesh_conversion'] = {'converted': 0, 'skipped': 0, 'errors': 0}

    if mesh_src.exists() and not textures_only:
        _profile_hair_and_grass(rec_dir, plugin_dir, stats)

    # -----------------------------------------------------------------------
    # Copy Textures
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Copy Textures to Output")
    print("=" * 60)

    _copy_and_fix_textures(asset_dir, plugin_dir, ns, stats, rec_dir)
    checked, written = landscape_normals.ensure_ltex_normals(
        rec_dir, plugin_dir / 'textures', output_dir)
    stats['ltex_normals_written'] = written
    print(f"  LTEX normals: {checked} land textures, {written} flat normals "
          f"written for textures shipping none")
    return stats


def _profile_hair_and_grass(rec_dir, plugin_dir, stats):
    """Run the hair and grass post-passes over the converted mesh tree."""
    stats['hair'] = hair_pipeline.run(rec_dir, plugin_dir / 'meshes')
    processed, modified, missing = grass_profile.run(
        rec_dir, plugin_dir / 'meshes')
    stats['grass_profile'] = {
        'processed': processed, 'modified': modified, 'missing': missing}
    print(f"  Grass models: {processed} placed under landscape\\grass"
          + (f", {missing} missing" if missing else ""))


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
    from asset_convert.texture import parallax as _parallax
    tex_src = asset_dir / 'textures'
    if not tex_src.exists():
        return
    tex_dst = plugin_dir / 'textures' / ns
    stats['textures_copied'] = _copy_tree(tex_src, tex_dst)
    print(f"  Textures: {stats['textures_copied']} files -> {tex_dst}")

    lum_checked, lum_fixed = luminance_textures.run(tex_dst)
    stats['luminance_textures_fixed'] = lum_fixed
    print(f"  Luminance textures: {lum_checked} L8 found, "
          f"{lum_fixed} expanded to BGRA")

    checked, fixed = landscape_normals.run(tex_dst / 'landscape')
    stats['landscape_normals_fixed'] = fixed
    print(f"  Landscape normals: {checked} checked, {fixed} given the mask")

    n_checked, n_fixed, n_kinds = landscape_normals.normalize_specular_alpha(
        tex_dst, skip=(os.sep + 'landscape' + os.sep,))
    if owns_namespace(rec_dir):
        landscape_normals.write_default_normal(tex_dst.parent)
    stats['spec_alpha_fixed'] = n_fixed
    print(f"  Specular masks: {n_checked} normal maps checked, {n_fixed} "
          f"given a constant mask "
          f"(alpha {landscape_normals.DEFAULT_MASK_ALPHA}/255)")
    if n_kinds:
        print('    ' + ', '.join(f'{k}={v}'
                                 for k, v in sorted(n_kinds.items())))

    _n, _skip, _kept, _saved = _parallax.strip_diffuse_alpha(
        tex_dst, keep=stats.get('mesh_conversion', {}).get(
            'alpha_opacity_diffuse', ()))
    if _n or _skip or _kept:
        stats['parallax_diffuse_bc1'] = _n
        print(f"  Parallax diffuse: {_n} DXT5->DXT1 "
              f"({_saved / (1024 * 1024):.1f} MB saved)"
              + (f", {_kept} kept (read as opacity)" if _kept else "")
              + (f", {_skip} already stripped" if _skip else ""))


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
    # A dependent plugin authors TREE records for art its MASTER ships, so the
    # masters' trees/ dirs are searched for any .spt this export lacks. Read
    # from the export header rather than a fixed list -- the chain differs per
    # plugin (Valenwood: Oblivion, Tamriel, Anequina).
    master_tree_dirs = []
    header = record_dir(extract_dir, source_name) / '_HEADER.txt'
    if header.is_file():
        for line in open(header, encoding='utf-8', errors='replace'):
            if line.startswith('Master['):
                name = line.partition('=')[2].strip()
                d = _asset_root(extract_dir, name) / 'trees'
                if d.is_dir():
                    master_tree_dirs.append(d)
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
            rel = src_file.relative_to(src)
            dst_file = dst / rel
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

