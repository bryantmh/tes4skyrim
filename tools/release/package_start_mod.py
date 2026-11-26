"""Build the TESGameSelect starter mod as a mod folder.

TESGameSelect ("Threads of Prophecy") is the new-game game selector: it
intercepts MQ101 so the player picks which converted world to start in. It is
not converted from a TES4 plugin — it is BUILT here: the MQ101
override is spliced from the installed Skyrim.esm and the two scripts are
compiled, so packaging always ships what the current source produces rather
than a committed artifact that can fall behind it.

The folder, output/TESGameSelect/, is rooted as a Data folder, so Install &
play installs it like any converted plugin (and zips it for a manual install).

Usage:
  python tools/release/package_start_mod.py     # -> output/TESGameSelect/
  python tools/release/package_start_mod.py --output-dir PATH
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from output_layout import DATA_ROOT, FINISHED_DIR_NAME
from tools.release.make_game_select_esp import build as build_start_mod

MOD_NAME = "TESGameSelect"


def package(out_root: Path) -> int:
    """Build the starter mod into output/TESGameSelect, rooted at the Data folder.

    The zip an older build made in Finished Mods is deleted: Manual install zips the folder.
    """
    build_dir = out_root / MOD_NAME

    print("=" * 54)
    print("  PACKAGE START MOD")
    print("=" * 54)
    if not build_start_mod(str(build_dir)):
        print("ERROR: the starter mod did not build — nothing to package.")
        return 1

    (out_root / FINISHED_DIR_NAME / f"{MOD_NAME}.zip").unlink(missing_ok=True)
    print()
    print(f"Built -> {build_dir}")
    print("Install it on Install & play like any other converted mod.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build the TESGameSelect starter mod as a mod folder.")
    ap.add_argument("--output-dir", metavar="PATH",
                    help="Output directory (default: output/ in project root)")
    args = ap.parse_args()
    out_root = (Path(args.output_dir) if args.output_dir
                else DATA_ROOT / "output")
    return package(out_root)


if __name__ == "__main__":
    sys.exit(main())
