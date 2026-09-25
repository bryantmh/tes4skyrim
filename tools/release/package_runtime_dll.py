"""Package the converter's SKSE DLLs as one distributable mod.

A DLL is not a plugin asset: one copy serves every converted mod, so they
ship together rather than beside any plugin's meshes. Each converted mod
contributes only its own data -- the animation cache fragment under
SKSE/Plugins/CreatureRuntime/animation, the FO3/FNV guns/bodyparts sidecars
under SKSE/Plugins/FalloutRuntime, the crime sidecar under
SKSE/Plugins/TESRuntime, and a Morrowind plugin's dialogue under
SKSE/Plugins/MorrowindRuntime -- which these DLLs read at load.

The archive mirrors what `convert.py --pack-zip-only` produces -- output/
Finished Mods/<name>.zip, contents rooted as a Data folder -- so a user
installs it exactly like any converted plugin.

Usage:
  python tools/release/package_runtime_dll.py   # -> output/Finished Mods/TESRuntime.zip
  python tools/release/package_runtime_dll.py --output-dir PATH
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from output_layout import finished_dir, write_mod_zip

MOD_NAME = "TESRuntime"

SRC_DIR = SCRIPT_DIR / "tes_runtime"

#: Where tes_runtime/build.bat puts every finished DLL.
DIST_DIR = SRC_DIR / "dist"

PLUGINS = Path("SKSE") / "Plugins"

#: The one DLL every converted game needs; without it the archive has no reason to exist.
REQUIRED = ((DIST_DIR / "TESRuntime.dll", PLUGINS / "TESRuntime.dll"),)

OPTIONAL = (
    (DIST_DIR / "CreatureRuntime.dll", PLUGINS / "CreatureRuntime.dll"),
    (DIST_DIR / "FalloutRuntime.dll", PLUGINS / "FalloutRuntime.dll"),
    (DIST_DIR / "HavokWorldSize.dll", PLUGINS / "HavokWorldSize.dll"),
    (SRC_DIR / "havok_world_size" / "HavokWorldSize.ini",
     PLUGINS / "HavokWorldSize.ini"),
    (DIST_DIR / "MorrowindRuntime.dll", PLUGINS / "MorrowindRuntime.dll"),
    (SRC_DIR / "morrowind" / "interface" / "morrowind_dialogue.swf",
     Path("Interface") / "morrowind_dialogue.swf"),
)


def package(out_root: Path) -> int:
    """Zip the built DLLs into <out_root>/Finished Mods/TESRuntime.zip.

    Every runtime is its own DLL, so a fault in one cannot take the others
    down, and MorrowindRuntime links GPL-3.0 OpenMW that must stay out of the
    others' binaries. Missing optional files are skipped.
    See: docs/commentary/morrowind_runtime.md#licensing
    """
    missing = [src for src, _ in REQUIRED if not src.is_file()]
    if missing:
        print(f"ERROR: {missing[0]} not found — build it first with "
              f"tes_runtime\\build.bat.")
        return 1

    zip_path = finished_dir(out_root) / f"{MOD_NAME}.zip"

    print("=" * 54)
    print("  PACKAGE RUNTIME DLL")
    print("=" * 54)
    print(f"  Source: {DIST_DIR}")
    print(f"  Output: {zip_path}")
    print()

    members = [(str(arc), src) for src, arc in REQUIRED]
    for src, arc in OPTIONAL:
        if src.is_file():
            members.append((str(arc), src))
        else:
            print(f"  - {arc} (not built, skipped)")
    write_mod_zip(zip_path, members, lambda _i, arc: print(f"  + {arc}"))

    size = zip_path.stat().st_size
    print()
    print(f"Packaged -> {zip_path} ({size:,} bytes)")
    print("Install it like any other converted mod: the archive root is the "
          "Data folder. The runtimes need SKSE and the Address Library; "
          "HavokWorldSize needs only SKSE.")
    return 0


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description="Package the runtime DLLs as one standalone SKSE mod.")
    ap.add_argument("--output-dir", metavar="PATH",
                    help="Output directory (default: output/ in project root)")
    args = ap.parse_args()
    out_root = (Path(args.output_dir) if args.output_dir
                else SCRIPT_DIR / "output")
    return package(out_root)


if __name__ == "__main__":
    sys.exit(main())
