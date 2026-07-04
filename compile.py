"""
compile.py — Build a self-contained TESConverter.exe with PyInstaller.

Usage:
  python compile.py              # standard build
  python compile.py --onefile    # single .exe (slower startup)
  python compile.py --clean      # remove build/ and dist/ before building
"""

import argparse
import subprocess
import sys
import shutil
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

# ── PyInstaller spec settings ─────────────────────────────────────────────────

APP_NAME   = "TESConverter"
ENTRY      = str(SCRIPT_DIR / "gui.py")

# Data files to bundle: list of (src_path, dest_folder_in_bundle)
# These land at the root of the bundled app (accessible via SCRIPT_DIR in code)
DATA_FILES = [
    str(SCRIPT_DIR / "conversion_config.json"), ".",
]

# All sub-packages that PyInstaller can't see via static analysis
HIDDEN_IMPORTS = [
    "tes4_export",
    "tes4_export.export",
    "tes4_export.text_reader",
    "tes4_export.tes4_reader",
    "tes4_export.record_types",
    "tes5_import",
    "tes5_import.import_main",
    "tes5_import.writer",
    "tes5_import.text_reader",
    "tes5_import.constants",
    "tes5_import.record_types",
    "asset_convert",
    "asset_convert.asset_pipeline",
    "asset_convert.bsa_extract",
    "asset_convert.nif_converter",
    "asset_convert.skin_retarget",
    "asset_convert.skyrim_overrides",
    "asset_convert.spt_converter",
    "asset_convert.lod_gen",
    "asset_convert.terrain_lod",
    "asset_convert.modify_body_meshes",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "winreg",
    "concurrent.futures",
    "threading",
    "struct",
    "zlib",
    "numpy",
]

# Binary files to bundle
BINARIES = [
    # Havok MOPP/welding compiler bridge — required at runtime for
    # bhkCompressedMeshShape collision generation (cms_builder.py)
    (str(SCRIPT_DIR / "asset_convert" / "dovah_hkp_mesh_mopp_bridge.exe"),
     "asset_convert"),
]


def _check_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not found. Installing...")
        ret = subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=False,
        )
        if ret.returncode != 0:
            print("ERROR: Failed to install PyInstaller.")
            sys.exit(1)
        print("PyInstaller installed.")


def _build(onefile: bool, clean: bool):
    _check_pyinstaller()

    build_dir = SCRIPT_DIR / "build"
    dist_dir  = SCRIPT_DIR / "dist"

    if clean:
        for d in (build_dir, dist_dir):
            if d.exists():
                print(f"Removing {d}...")
                shutil.rmtree(d)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name",       APP_NAME,
        "--noconfirm",
        "--windowed",                       # no console window (GUI app)
        "--noupx",
    ]

    # Add all hidden imports
    for hi in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", hi]

    # Add binary files (dovah_hkp_mesh_mopp_bridge.exe)
    for src, dest in BINARIES:
        if Path(src).exists():
            cmd += ["--add-binary", f"{src}{';' if sys.platform == 'win32' else ':'}{dest}"]
        else:
            print(f"WARNING: binary not found, skipping: {src}")

    # Add data files
    sep = ";" if sys.platform == "win32" else ":"
    # Add conversion_config.json if it exists
    cfg_src = SCRIPT_DIR / "conversion_config.json"
    if cfg_src.exists():
        cmd += ["--add-data", f"{cfg_src}{sep}."]

    # generated/ folder (bone animation data)
    gen_dir = SCRIPT_DIR / "asset_convert" / "generated"
    if gen_dir.exists():
        cmd += ["--add-data", f"{gen_dir}{sep}asset_convert/generated"]

    # docs/ folder (nif.xml reference files used at runtime)
    docs_dir = SCRIPT_DIR / "docs"
    if docs_dir.exists():
        cmd += ["--add-data", f"{docs_dir}{sep}docs"]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    cmd += [
        "--distpath", str(dist_dir),
        "--workpath", str(build_dir),
        "--specpath", str(SCRIPT_DIR),
        ENTRY,
    ]

    print(f"\nRunning: {' '.join(cmd)}\n")
    ret = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    if ret.returncode != 0:
        print(f"\nERROR: PyInstaller exited with code {ret.returncode}")
        sys.exit(ret.returncode)

    if onefile:
        exe = dist_dir / f"{APP_NAME}.exe"
    else:
        exe = dist_dir / APP_NAME / f"{APP_NAME}.exe"

    if exe.exists():
        print(f"\nBuild successful: {exe}")
    else:
        print(f"\nBuild done (exe path: {exe})")


def main():
    parser = argparse.ArgumentParser(description="Build TESConverter.exe")
    parser.add_argument("--onefile", action="store_true",
                        help="Single .exe bundle (slower startup, larger file)")
    parser.add_argument("--clean",   action="store_true",
                        help="Remove build/ and dist/ before building")
    args = parser.parse_args()
    _build(onefile=args.onefile, clean=args.clean)


if __name__ == "__main__":
    main()
