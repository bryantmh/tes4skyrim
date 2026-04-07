"""python -m tes5_import — Run the TES4-to-TES5 import pipeline."""

import argparse
import json
import sys

from .import_main import import_plugin


def main():
    parser = argparse.ArgumentParser(
        description='Convert TES4 text exports to TES5 binary ESM/ESP')
    parser.add_argument('export_dir',
                        help='Directory containing per-type .txt export files')
    parser.add_argument('-o', '--output', required=True,
                        help='Output .esm/.esp file path')
    parser.add_argument('-m', '--masters', nargs='+', default=['Skyrim.esm'],
                        help='Master file names (default: Skyrim.esm)')
    parser.add_argument('--esp', action='store_true',
                        help='Create ESP instead of ESM')
    parser.add_argument('--skip-types', nargs='*', default=[],
                        help='Additional record types to skip')
    args = parser.parse_args()

    # Load skip types from config if available
    skip_types = set(args.skip_types)
    try:
        with open('conversion_config.json') as f:
            config = json.load(f)
            skip_types |= set(config.get('skipTypes', []))
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    converted, errors = import_plugin(
        args.export_dir,
        args.output,
        masters=args.masters,
        is_esm=not args.esp,
        skip_types=skip_types,
    )

    if errors:
        print(f"\nCompleted with {errors} errors.")
        sys.exit(1)


if __name__ == '__main__':
    main()
