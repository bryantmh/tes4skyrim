"""Asset conversion pipeline: BSA extraction → mesh/texture conversion → output.

Orchestrates the full asset workflow:
1. Extract BSA archives for the source plugin
2. Convert NIF meshes (Oblivion → Skyrim format, strips→shape, texture paths, bones)
3. Copy textures to output (Oblivion DXT textures are Skyrim-compatible)
4. Copy sounds to output
5. Write converted files to the final output directory
"""
import os
import shutil
from pathlib import Path

from . import bsa_extract, nif_converter, spt_converter


def convert_assets(source_file, data_path, extract_dir='export',
                   output_dir='output', force_extract=False):
    """Run the full asset conversion pipeline for a source plugin.

    Args:
        source_file: Plugin filename (e.g. 'Oblivion.esm').
        data_path: Path to Oblivion Data directory.
        extract_dir: Directory for BSA extraction (intermediate).
        output_dir: Final output directory. Assets are placed under
                    output_dir/<source_name>/meshes/tes4/ and
                    output_dir/<source_name>/textures/tes4/ etc.
                    The converted plugin file should be written separately to
                    output_dir/<source_name>/<source_name>
                    (e.g. python -m tes5_import ... -o output/Oblivion.esm/Oblivion.esm).
        force_extract: Force BSA re-extraction.

    Returns:
        dict with pipeline stats.
    """
    extract_dir = Path(extract_dir)
    output_dir = Path(output_dir)
    source_name = Path(source_file).name  # e.g. 'Oblivion.esm'

    stats = {
        'bsa_extraction': {},
        'mesh_conversion': {},
        'spt_conversion': {},
        'textures_copied': 0,
        'sounds_copied': 0,
        'other_copied': 0,
    }

    # -----------------------------------------------------------------------
    # Phase 1: Extract BSA assets
    # -----------------------------------------------------------------------
    print("=" * 60)
    print("Phase 1: BSA Extraction")
    print("=" * 60)
    stats['bsa_extraction'] = bsa_extract.extract_assets_for_file(
        source_file, data_path, extract_dir, force=force_extract
    )

    # -----------------------------------------------------------------------
    # Phase 2: Convert NIF meshes
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Phase 2: NIF Mesh Conversion")
    print("=" * 60)

    mesh_src = extract_dir / source_name / 'meshes'
    if mesh_src.exists():
        # Output goes under output/{source_name}/meshes/tes4/ so that the
        # plugin file, meshes, and textures all live in one named subfolder.
        plugin_dir = output_dir / source_name
        mesh_dst = plugin_dir / 'meshes' / 'tes4'
        stats['mesh_conversion'] = nif_converter.batch_convert(
            str(mesh_src), output_dir=str(mesh_dst),
            fix_textures=True, remap_skeleton=None
        )
    else:
        print(f"No meshes found at {mesh_src}")
        stats['mesh_conversion'] = {'converted': 0, 'skipped': 0, 'errors': 0}

    # -----------------------------------------------------------------------
    # Phase 3: Copy converted assets to output directory
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Phase 3: Copy to Output")
    print("=" * 60)

    # Meshes are already written directly to output by batch_convert.
    # Copy textures and sounds, placing them under a tes4\ subfolder in
    # output/{source_name}/ alongside the converted plugin file.
    plugin_dir = output_dir / source_name

    tex_src = extract_dir / source_name / 'textures'
    if tex_src.exists():
        tex_dst = plugin_dir / 'textures' / 'tes4'
        stats['textures_copied'] = _copy_tree(tex_src, tex_dst)
        print(f"  Textures: {stats['textures_copied']} files → {tex_dst}")

    snd_src = extract_dir / source_name / 'sound'
    if snd_src.exists():
        snd_dst = plugin_dir / 'sound' / 'tes4'
        stats['sounds_copied'] = _copy_tree(snd_src, snd_dst)
        print(f"  Sounds: {stats['sounds_copied']} files → {snd_dst}")

    misc_src = extract_dir / source_name / 'misc'
    if misc_src.exists():
        misc_dst = plugin_dir / 'misc' / 'tes4'
        stats['other_copied'] = _copy_tree(misc_src, misc_dst)
        print(f"  Other: {stats['other_copied']} files → {misc_dst}")

    # -----------------------------------------------------------------------
    # Phase 4: Convert SpeedTree (.spt) → Skyrim NIF
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Phase 4: SpeedTree Conversion")
    print("=" * 60)

    spt_src = extract_dir / source_name / 'trees'
    if spt_src.exists():
        spt_dst = plugin_dir / 'meshes' / 'tes4' / 'speedtrees'
        stats['spt_conversion'] = spt_converter.convert_spt_directory(
            spt_src, spt_dst
        )
    else:
        print(f"  No trees/ directory found at {spt_src}")
        stats['spt_conversion'] = {'ok': 0, 'fail': 0, 'skip': 0}

    print("\n" + "=" * 60)
    print("Pipeline Complete")
    print("=" * 60)

    return stats


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
        description='Full asset conversion pipeline (BSA → convert → output)')
    parser.add_argument('source_file',
                        help='Plugin filename (e.g. Oblivion.esm)')
    parser.add_argument('--data-path', required=True,
                        help='Path to Oblivion Data directory')
    parser.add_argument('--extract-dir', default='export',
                        help='Intermediate extraction directory (default: export)')
    parser.add_argument('--output-dir', default='output',
                        help='Final output directory (default: output)')
    parser.add_argument('--force-extract', action='store_true',
                        help='Force BSA re-extraction')
    args = parser.parse_args()

    convert_assets(args.source_file, args.data_path,
                   extract_dir=args.extract_dir, output_dir=args.output_dir,
                   force_extract=args.force_extract)
