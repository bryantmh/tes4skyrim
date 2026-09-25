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
  python tools/release/package_runtime_dll.py --mod HavokWorldSize   # standalone
  python tools/release/package_runtime_dll.py --mod CreatureRuntime  # standalone
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

CREATURE_RUNTIME = ((DIST_DIR / "CreatureRuntime.dll", PLUGINS / "CreatureRuntime.dll"),)

HAVOK_WORLD_SIZE = (
    (DIST_DIR / "HavokWorldSize.dll", PLUGINS / "HavokWorldSize.dll"),
    (SRC_DIR / "havok_world_size" / "HavokWorldSize.ini",
     PLUGINS / "HavokWorldSize.ini"),
)

#: Mod name -> (required files, optional files), each file a (source, archive path) pair.
MODS = {
    MOD_NAME: (
        ((DIST_DIR / "TESRuntime.dll", PLUGINS / "TESRuntime.dll"),),
        (*CREATURE_RUNTIME,
         (DIST_DIR / "FalloutRuntime.dll", PLUGINS / "FalloutRuntime.dll"),
         *HAVOK_WORLD_SIZE,
         (DIST_DIR / "MorrowindRuntime.dll", PLUGINS / "MorrowindRuntime.dll"),
         (SRC_DIR / "morrowind" / "interface" / "morrowind_dialogue.swf",
          Path("Interface") / "morrowind_dialogue.swf")),
    ),
    "CreatureRuntime": (CREATURE_RUNTIME, ()),
    "HavokWorldSize": (HAVOK_WORLD_SIZE, ()),
}


def package(out_root: Path, mod_name: str = MOD_NAME) -> int:
    """Zip `mod_name`'s files into <out_root>/Finished Mods/<mod_name>.zip.

    Every runtime is its own DLL, so a fault in one cannot take the others
    down; MorrowindRuntime links GPL-3.0 OpenMW, which stays out of the MIT
    runtimes' binaries. Missing optional files are skipped.
    See: docs/commentary/morrowind_runtime.md#licensing
    """
    required, optional = MODS[mod_name]
    missing = [src for src, _ in required if not src.is_file()]
    if missing:
        print(f"ERROR: {missing[0]} not found — build it first with "
              f"tes_runtime\\build.bat.")
        return 1

    zip_path = finished_dir(out_root) / f"{mod_name}.zip"

    print("=" * 54)
    print("  PACKAGE RUNTIME DLL")
    print("=" * 54)
    print(f"  Source: {DIST_DIR}")
    print(f"  Output: {zip_path}")
    print()

    members = [(str(arc), src) for src, arc in required]
    for src, arc in optional:
        if src.is_file():
            members.append((str(arc), src))
        else:
            print(f"  - {arc} (not built, skipped)")
    write_mod_zip(zip_path, members, lambda _i, arc: print(f"  + {arc}"))

    size = zip_path.stat().st_size
    print()
    print(f"Packaged -> {zip_path} ({size:,} bytes)")
    print("Install it like any other converted mod: the archive root is the "
          "Data folder.")
    return 0


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description="Package the runtime DLLs as one standalone SKSE mod.")
    ap.add_argument("--output-dir", metavar="PATH",
                    help="Output directory (default: output/ in project root)")
    ap.add_argument("--mod", choices=sorted(MODS), default=MOD_NAME,
                    help=f"Which archive to build (default: {MOD_NAME})")
    args = ap.parse_args()
    out_root = (Path(args.output_dir) if args.output_dir
                else SCRIPT_DIR / "output")
    return package(out_root, args.mod)


if __name__ == "__main__":
    sys.exit(main())
