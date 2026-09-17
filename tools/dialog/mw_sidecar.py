"""
Restage a TES3 plugin's MorrowindRuntime sidecar without re-running import.

The sidecar -- dialogue, the actor index, and the result-script compiler's
tables -- is built from the EXPORT alone, so when only the sidecar's contents
change there is no reason to pay for a full `--import-only`.

    python -m tools.dialog.mw_sidecar --plugin TR_Mainland.esm

Prints what it staged and where; install that folder as usual.
"""

import argparse
import os
import sys

from asset_convert.sources import source_registry
from tes5_import.dialogue.morrowind_sidecar import (SIDECAR_DIR, plugin_stem,
                                                    write_morrowind_sidecar)


def main() -> int:
    """CLI: stage one plugin's sidecar into its output folder."""
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--plugin', required=True,
                    help='the TES3 plugin, e.g. TR_Mainland.esm')
    ap.add_argument('--export-root', default='export')
    ap.add_argument('--output-root', default='output')
    args = ap.parse_args()

    record_dir = str(source_registry.record_dir(args.export_root, args.plugin))
    if not os.path.isdir(record_dir):
        print(f'no export for {args.plugin} at {record_dir}')
        return 1
    group = source_registry.asset_root_name(args.export_root, args.plugin)
    output_path = os.path.join(args.output_root, group, args.plugin)
    staged = write_morrowind_sidecar(record_dir, output_path, args.plugin)
    out_dir = os.path.join(os.path.dirname(output_path), SIDECAR_DIR,
                           plugin_stem(args.plugin))
    print(f'staged {staged} file(s) -> {out_dir}')
    for name in sorted(os.listdir(out_dir)) if staged else []:
        size = os.path.getsize(os.path.join(out_dir, name))
        print(f'  {name}  {size:,} bytes')
    return 0 if staged else 1


if __name__ == '__main__':
    sys.exit(main())
