"""Package the TESGameSelect starter mod as a distributable zip.

TESGameSelect ("Threads of Prophecy") is the new-game game selector: it
intercepts MQ101 so the player picks which converted world to start in. It is
not converted from a TES4 plugin — it is BUILT here, then zipped: the MQ101
override is spliced from the installed Skyrim.esm and the two scripts are
compiled, so packaging always ships what the current source produces rather
than a committed artifact that can fall behind it.

The archive mirrors what `convert.py --pack-zip-only` produces for a converted
plugin — output/Finished Mods/<name>.zip, contents rooted as a Data folder — so
a user installs it exactly the same way and a mod manager sees the same shape.

Usage:
  python tools/release/package_start_mod.py     # -> output/Finished Mods/TESGameSelect.zip
  python tools/release/package_start_mod.py --output-dir PATH
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from output_layout import finished_dir, tree_members, write_mod_zip
from tools.release.make_game_select_esp import build as build_start_mod

MOD_NAME = "TESGameSelect"


def package(out_root: Path) -> int:
    """Build the starter mod, then zip what the build produced.

    Archive paths are relative to the build root, so the archive root IS the
    Data folder: the .esp, scripts and seq all sit at top level.
    """
    build_dir = out_root / MOD_NAME

    print("=" * 54)
    print("  PACKAGE START MOD")
    print("=" * 54)
    if not build_start_mod(str(build_dir)):
        print("ERROR: the starter mod did not build — nothing to package.")
        return 1

    files = tree_members(build_dir)
    if not files:
        print(f"ERROR: {build_dir} is empty — nothing to package.")
        return 1

    zip_path = finished_dir(out_root) / f"{MOD_NAME}.zip"
    print()
    print(f"  Source: {build_dir}")
    print(f"  Output: {zip_path}")
    print()

    write_mod_zip(zip_path, files, lambda _i, arc: print(f"  + {arc}"))

    size = zip_path.stat().st_size
    print()
    print(f"Packaged {len(files)} file(s) -> {zip_path} ({size:,} bytes)")
    print("Install it like any other converted mod: the archive root is the "
          "Data folder.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Package the TESGameSelect starter mod as a zip.")
    ap.add_argument("--output-dir", metavar="PATH",
                    help="Output directory (default: output/ in project root)")
    args = ap.parse_args()
    out_root = (Path(args.output_dir) if args.output_dir
                else SCRIPT_DIR / "output")
    return package(out_root)


if __name__ == "__main__":
    sys.exit(main())
