"""Asset conversion pipeline: BSA extraction → mesh/texture conversion → output.

Four separate callable steps:

  extract_bsas(source_file, data_path, extract_dir, force)
      Pull all assets from BSA archives into extract_dir/<source_name>/.

  convert_assets(source_file, extract_dir, output_dir)
      Convert NIFs, copy textures, convert SpeedTree files.
      Assumes extract_bsas has already been run.

  convert_sounds(source_file, extract_dir, output_dir)
      Convert sound files from extracted BSA into XWM and move into output_dir/<source_name>/sound/tes4/.
      Assumes extract_bsas has already been run."""
import os
import shutil
from pathlib import Path

from . import bsa_extract, nif_converter, spt_converter


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


def convert_assets(source_file, extract_dir='export', output_dir='output'):
    """Convert extracted assets and copy them to output_dir.

    Assumes BSA extraction has already been run (extract_bsas).

    Args:
        source_file: Plugin filename (e.g. 'Oblivion.esm').
        extract_dir: Root extraction directory (default: export).
        output_dir:  Final output root (files placed under output_dir/<source_name>/).

    Returns:
        dict with pipeline stats.
    """
    extract_dir = Path(extract_dir)
    output_dir  = Path(output_dir)
    source_name = Path(source_file).name

    stats = {
        'mesh_conversion': {},
        'spt_conversion':  {},
        'textures_copied': 0,
        'other_copied':    0,
    }

    plugin_dir = output_dir / source_name

    # -----------------------------------------------------------------------
    # NIF Mesh Conversion
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("NIF Mesh Conversion")
    print("=" * 60)
    mesh_src = extract_dir / source_name / 'meshes'
    if mesh_src.exists():
        mesh_dst = plugin_dir / 'meshes' / 'tes4'
        stats['mesh_conversion'] = nif_converter.batch_convert(
            str(mesh_src), output_dir=str(mesh_dst),
            fix_textures=True, remap_skeleton=None
        )
    else:
        print(f"  No meshes found at {mesh_src}")
        stats['mesh_conversion'] = {'converted': 0, 'skipped': 0, 'errors': 0}

    # -----------------------------------------------------------------------
    # Copy Textures
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Copy Assets to Output")
    print("=" * 60)

    tex_src = extract_dir / source_name / 'textures'
    if tex_src.exists():
        tex_dst = plugin_dir / 'textures' / 'tes4'
        stats['textures_copied'] = _copy_tree(tex_src, tex_dst)
        print(f"  Textures: {stats['textures_copied']} files -> {tex_dst}")


    # -----------------------------------------------------------------------
    # SpeedTree Conversion
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("SpeedTree Conversion")
    print("=" * 60)

    spt_src = extract_dir / source_name / 'trees'
    if spt_src.exists():
        spt_dst = plugin_dir / 'meshes' / 'tes4' / 'speedtrees'
        stats['spt_conversion'] = spt_converter.convert_spt_directory(spt_src, spt_dst)
    else:
        print(f"  No trees/ directory found at {spt_src}")
        stats['spt_conversion'] = {'ok': 0, 'fail': 0, 'skip': 0}

    print("\n" + "=" * 60)
    print("Asset Conversion Complete")
    print("=" * 60)

    return stats


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
    from .audio_converter import convert_sounds as _ac_convert
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

    p_convert = sub.add_parser('convert', help='Convert extracted assets only')
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
        convert_assets(args.source_file,
                       extract_dir=args.extract_dir, output_dir=args.output_dir)
    elif args.cmd == 'sounds':
        convert_sounds(args.source_file,
                    extract_dir=args.extract_dir, output_dir=args.output_dir)
    else:
        parser.print_help()

