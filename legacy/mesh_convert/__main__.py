"""CLI entry point: python -m mesh_convert"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog='mesh_convert',
        description='TES4→TES5 asset conversion pipeline')
    sub = parser.add_subparsers(dest='command', help='Sub-command')

    # --- nif: convert NIF meshes ---
    nif_parser = sub.add_parser('nif', help='Convert NIF meshes for Skyrim SE')
    nif_parser.add_argument('path', help='NIF file or directory')
    nif_parser.add_argument('--dry-run', action='store_true')
    nif_parser.add_argument('-o', '--output', help='Output path (single file)',
                            required=False)
    nif_parser.add_argument('-d', '--output-dir', default=None,
                            help='Output directory for batch conversion (required)')
    nif_parser.add_argument('--no-textures', action='store_true')
    nif_parser.add_argument('--no-bones', action='store_true')
    nif_parser.add_argument('-j', '--workers', type=int, default=None,
                            help='Number of parallel workers (default: cpu_count)')

    # --- extract: extract BSA archives ---
    ext_parser = sub.add_parser('extract', help='Extract assets from BSA archives')
    ext_parser.add_argument('source_file', help='Plugin filename (e.g. Oblivion.esm)')
    ext_parser.add_argument('--data-path', required=True,
                           help='Path to Oblivion Data directory')
    ext_parser.add_argument('--extract-dir', default='export')
    ext_parser.add_argument('--force', action='store_true')

    # --- pipeline: full asset pipeline ---
    pipe_parser = sub.add_parser('pipeline',
                                help='Full pipeline: extract → convert → output')
    pipe_parser.add_argument('source_file', help='Plugin filename')
    pipe_parser.add_argument('--data-path', required=True,
                            help='Path to Oblivion Data directory')
    pipe_parser.add_argument('--extract-dir', default='export')
    pipe_parser.add_argument('--output-dir', default='output')
    pipe_parser.add_argument('--force-extract', action='store_true')

    args = parser.parse_args()

    if args.command == 'nif':
        import os

        from .nif_converter import batch_convert, convert_nif
        if os.path.isfile(args.path):
            out = args.output
            if not out:
                print("Error: output path required (-o)")
                sys.exit(1)
            r = convert_nif(args.path, out,
                           fix_textures=not args.no_textures,
                           remap_skeleton=None if not args.no_bones else False)
            if r['converted']:
                print(f"Converted: {args.path}")
            elif r['error']:
                print(f"Error: {args.path}: {r['error']}")
            else:
                print(f"Skipped (already Skyrim format): {args.path}")
        elif os.path.isdir(args.path):
            out_dir = args.output_dir
            if not out_dir:
                print("Error: output directory required (-d)")
                sys.exit(1)
            batch_convert(args.path, output_dir=out_dir,
                         dry_run=args.dry_run,
                         fix_textures=not args.no_textures,
                         remap_skeleton=None if not args.no_bones else False,
                         workers=args.workers)
        else:
            print(f"Path not found: {args.path}")
            sys.exit(1)

    elif args.command == 'extract':
        from .bsa_extract import extract_assets_for_file
        extract_assets_for_file(args.source_file, args.data_path,
                               args.extract_dir, force=args.force)

    elif args.command == 'pipeline':
        from .asset_pipeline import convert_assets
        convert_assets(args.source_file, args.data_path,
                      extract_dir=args.extract_dir,
                      output_dir=args.output_dir,
                      force_extract=args.force_extract)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
