"""
TES4-to-TES5 Conversion Pipeline

Pipeline steps (each runnable via --<step>-only):
  export          Parse TES4 binary → key/value text cache
  import          Build TES5 binary ESM/ESP from text cache
  assets          Extract BSAs, convert NIFs/SPTs, copy textures
  lod             Generate object & terrain LOD meshes
  modify-body-meshes  Add greaves partition to character body NIFs
  verify-plugin   Run integrity checks on output plugin(s)

Usage:
  python convert.py                          # full pipeline (export + import + assets)
  python convert.py -f Oblivion.esm          # single file, full pipeline
  python convert.py -f Oblivion.esm --export-only
  python convert.py -f Oblivion.esm --import-only
  python convert.py -f Oblivion.esm --assets-only
  python convert.py -f Oblivion.esm --lod-only
  python convert.py --modify-body-meshes
  python convert.py -f Oblivion.esm --verify-plugin
  python convert.py --test
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure stdout/stderr can handle Unicode on Windows consoles (cp1252 → utf-8)
if sys.stdout and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).parent.resolve()  # TESConversion root


def load_config(config_path: str = None) -> dict:
    path = Path(config_path) if config_path else SCRIPT_DIR / "conversion_config.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_game_path(game: str) -> str:
    """Auto-detect game data path from registry."""
    try:
        import winreg
        keys = {
            "oblivion": [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Bethesda Softworks\Oblivion"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Bethesda Softworks\Oblivion"),
            ],
            "skyrimse": [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Bethesda Softworks\Skyrim Special Edition"),
            ],
        }
        for hkey, subkey in keys.get(game, []):
            try:
                with winreg.OpenKey(hkey, subkey) as key:
                    path, _ = winreg.QueryValueEx(key, "Installed Path")
                    data = os.path.join(path, "Data")
                    if os.path.isdir(data):
                        return data
            except (FileNotFoundError, OSError):
                continue
    except ImportError:
        pass  # Not on Windows
    return ""


def get_paths(config: dict) -> tuple:
    """Get TES4 and TES5 data paths."""
    tes4 = config.get("tes4DataPath", "") or find_game_path("oblivion")
    tes5 = config.get("tes5DataPath", "") or find_game_path("skyrimse")
    return tes4, tes5


def get_masters_from_binary(filepath: str) -> list:
    """Read master list from a TES4 binary file header."""
    import struct as st
    masters = []
    with open(filepath, 'rb') as f:
        sig = f.read(4)
        if sig != b'TES4':
            return masters
        data_size = st.unpack('<I', f.read(4))[0]
        f.read(12)  # flags + formID + vc
        data = f.read(data_size)
        pos = 0
        while pos + 6 <= len(data):
            sub_sig = data[pos:pos+4].decode('ascii', errors='replace')
            sub_size = st.unpack_from('<H', data, pos+4)[0]
            pos += 6
            if pos + sub_size > len(data):
                break
            if sub_sig == 'MAST':
                masters.append(data[pos:pos+sub_size].decode('latin-1').rstrip('\0'))
            pos += sub_size
    return masters


def topological_order(files: list, tes4_data: str) -> list:
    """Sort files in dependency order (masters first)."""
    # Files can be strings or dicts with 'name' key
    file_names = []
    for f in files:
        if isinstance(f, str):
            file_names.append(f)
        else:
            file_names.append(f['name'])

    # Build dependency graph from binary headers
    deps = {}
    for name in file_names:
        source = os.path.join(tes4_data, name)
        if os.path.isfile(source):
            deps[name] = get_masters_from_binary(source)
        else:
            deps[name] = []

    visited = {}
    order = []

    def visit(name):
        if name in visited:
            return
        visited[name] = True
        for master in deps.get(name, []):
            if master in deps:  # Only visit if it's in our file list
                visit(master)
        order.append(name)

    for name in file_names:
        visit(name)
    return order



# ===========================================================================
# Phase 1: Export
# ===========================================================================

def phase_export(file_name: str, tes4_data: str, export_dir: str,
                 config: dict):
    """Export TES4 records using the Python binary reader."""
    from tes4_export.tes4_reader import read_file
    from tes4_export.export import export_file, export_header

    out_dir = os.path.join(export_dir, file_name)

    # Find the source file
    source = os.path.join(tes4_data, file_name)
    if not os.path.isfile(source):
        print(f"[{file_name}] ERROR: Source file not found: {source}")
        return False

    print(f"[{file_name}] Exporting...")
    t0 = time.time()

    header, all_records = read_file(source)

    t1 = time.time()
    print(f"  Parsed {len(all_records)} records in {t1-t0:.2f}s")

    os.makedirs(out_dir, exist_ok=True)
    export_header(header, out_dir)

    # For dependent plugins, only export records owned by this file
    # (determined by the load-order index in the FormID's top byte)
    # Auto-detect masters from the binary header
    masters = get_masters_from_binary(source)
    source_index = None
    if masters:
        # The file's own records have load-order index = number of masters
        source_index = f"{len(masters):02X}"

    type_filter = None  # Export all types; skip types are handled by import

    export_file(all_records, out_dir, type_filter=type_filter,
                source_filter=source_index)

    t2 = time.time()
    print(f"[{file_name}] Export complete in {t2-t0:.2f}s")

    return True

# ===========================================================================
# Phase 2: Import
# ===========================================================================

def phase_import(file_name: str, tes4_data: str, tes5_data: str,
                        export_dir: str, config: dict):
    """Import using the Python tes5_import package."""
    from tes5_import.import_main import import_plugin

    export_subdir = os.path.join(export_dir, file_name)
    if not os.path.isdir(export_subdir):
        print(f"[{file_name}] No export directory, skipping import")
        return False

    output_dir = str(SCRIPT_DIR / "output")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, file_name)
    # If a directory with the same name exists (e.g. from mesh pipeline output),
    # write the .esm inside it rather than conflicting with the directory.
    if os.path.isdir(output_path):
        output_path = os.path.join(output_path, file_name)

    # Auto-detect masters from binary, prepend Skyrim.esm
    source = os.path.join(tes4_data, file_name)
    tes4_masters = get_masters_from_binary(source) if os.path.isfile(source) else []
    masters = ['Skyrim.esm'] + tes4_masters

    is_esm = file_name.lower().endswith('.esm')

    print(f"[{file_name}] Importing...")
    print(f"  Masters: {', '.join(masters)}")
    converted, errors = import_plugin(
        export_dir=export_subdir,
        output_path=output_path,
        masters=masters,
        is_esm=is_esm,
    )

    return errors == 0


# ===========================================================================
# Phase 3: Assets
# ===========================================================================

def phase_assets(file_name: str, tes4_data: str, config: dict):
    """Extract BSA archives and convert NIF/SPT assets for a plugin."""
    from asset_convert.asset_pipeline import convert_assets

    extract_dir = str(SCRIPT_DIR / "export")
    output_dir  = str(SCRIPT_DIR / "output")

    print(f"[{file_name}] Converting assets...")
    stats = convert_assets(
        source_file=file_name,
        data_path=tes4_data,
        extract_dir=extract_dir,
        output_dir=output_dir,
    )
    total = sum(v for v in stats.values() if isinstance(v, int))
    print(f"[{file_name}] Assets complete ({total} items processed)")
    return True


# ===========================================================================
# Phase 4: LOD generation
# ===========================================================================

def phase_lod(file_name: str, tes5_data: str, config: dict):
    """Generate object LOD and terrain LOD for the converted plugin."""
    from asset_convert.lod_gen import generate_lod
    from asset_convert.terrain_lod import generate_terrain_lod

    output_root = SCRIPT_DIR / "output"
    output_dir  = output_root / file_name
    if not output_dir.is_dir():
        print(f"[{file_name}] No output directory found, skipping LOD")
        return False

    esm_path = output_dir / file_name
    if not esm_path.exists():
        print(f"[{file_name}] ESM not found at {esm_path}, skipping LOD")
        return False

    stem = Path(file_name).stem
    if stem.lower() == 'oblivion':
        worldspace_edid = 'TES4Tamriel'
    else:
        worldspace_edid = config.get('worldspaceEditorID', stem)

    print(f"[{file_name}] Generating LOD (worldspace: {worldspace_edid})...")
    ok = generate_lod(
        esm_path=esm_path,
        output_dir=output_dir,
        worldspace_edid=worldspace_edid,
    )

    print(f"[{file_name}] Generating terrain LOD...")
    generate_terrain_lod(
        esm_path=esm_path,
        output_dir=output_dir,
        worldspace_edid=worldspace_edid,
    )

    return ok


# ===========================================================================
# Phase 5: Modify body meshes
# ===========================================================================

def phase_modify_body_meshes():
    """Add greaves partition to vanilla Skyrim character body NIFs."""
    script = SCRIPT_DIR / "asset_convert" / "modify_body_meshes.py"
    if not script.exists():
        print("ERROR: asset_convert/modify_body_meshes.py not found")
        return False
    ret = subprocess.run([sys.executable, str(script)], cwd=str(SCRIPT_DIR))
    return ret.returncode == 0


# ===========================================================================
# Utility: verify output plugin
# ===========================================================================

def phase_verify(file_name: str):
    """Run verify_plugin.py integrity checks on an output plugin."""
    verify = SCRIPT_DIR / "tools" / "verify_plugin.py"
    if not verify.exists():
        print("  verify_plugin.py not found")
        return False

    # Output may be nested: output/Oblivion.esm/Oblivion.esm (alongside assets)
    nested = SCRIPT_DIR / "output" / file_name / file_name
    simple = SCRIPT_DIR / "output" / file_name
    plugin = nested if nested.exists() else simple
    if not plugin.exists():
        print(f"  [{file_name}] Output plugin not found — skipping verify")
        return False

    print(f"[{file_name}] Verifying: {plugin}")
    ret = subprocess.run([sys.executable, str(verify), str(plugin), "--check"],
                         cwd=str(SCRIPT_DIR))
    return ret.returncode == 0


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="TES4-to-TES5 Conversion Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Default pipeline (no --*-only): export + import + assets\n"
            "Each --*-only flag runs exactly that step and nothing else."
        ),
    )
    parser.add_argument("-f", "--files", nargs="+", metavar="FILE",
                        help="Plugin filename(s) to process (default: all from config)")
    parser.add_argument("--config", metavar="PATH",
                        help="Path to conversion_config.json")
    parser.add_argument("--export-only",        action="store_true",
                        help="Export TES4 binary → key/value text cache")
    parser.add_argument("--import-only",        action="store_true",
                        help="Convert text cache → TES5 binary ESM/ESP")
    parser.add_argument("--assets-only",        action="store_true",
                        help="Extract BSAs, convert NIFs/SPTs, copy textures")
    parser.add_argument("--lod-only",           action="store_true",
                        help="Generate object & terrain LOD meshes")
    parser.add_argument("--modify-body-meshes", action="store_true",
                        help="Add greaves partition to character body NIFs")
    parser.add_argument("--verify-plugin",      action="store_true",
                        help="Run integrity checks on output plugin(s)")
    parser.add_argument("--test",               action="store_true",
                        help="Run pytest test suite")

    args = parser.parse_args()

    if args.test:
        ret = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-v"],
                             cwd=str(SCRIPT_DIR))
        return ret.returncode

    config    = load_config(args.config)
    tes4_data, tes5_data = get_paths(config)

    export_dir = str(SCRIPT_DIR / "export")
    os.makedirs(export_dir, exist_ok=True)
    os.makedirs(os.path.join(export_dir, "mappings"), exist_ok=True)

    print("=" * 54)
    print("  TES4 → TES5 Conversion Pipeline")
    print("=" * 54)
    print(f"  Oblivion data : {tes4_data or '(not found)'}")
    print(f"  Skyrim SE data: {tes5_data or '(not found)'}")
    print()

    order = topological_order(config.get("files", []), tes4_data)
    if args.files:
        low = {f.lower() for f in args.files}
        order = [f for f in order if f.lower() in low]
    if not order:
        print("No files to process.")
        return 0
    print(f"  Files: {', '.join(order)}")
    print()

    # ── Determine which steps to run ──────────────────────────────────────
    _any_only = any([
        args.export_only, args.import_only, args.assets_only,
        args.lod_only, args.modify_body_meshes, args.verify_plugin,
    ])
    if _any_only:
        do_export = args.export_only
        do_import = args.import_only
        do_assets = args.assets_only
        do_lod    = args.lod_only
        do_body   = args.modify_body_meshes
        do_verify = args.verify_plugin
    else:
        # Default: export + import + assets (LOD is expensive; must be explicit)
        do_export = do_import = do_assets = True
        do_lod = do_body = do_verify = False

    success = True

    if do_export:
        print("=" * 54)
        print("  Phase 1: EXPORT")
        print("=" * 54)
        for fn in order:
            if not phase_export(fn, tes4_data, export_dir, config):
                success = False
        print()

    if do_import:
        print("=" * 54)
        print("  Phase 2: IMPORT")
        print("=" * 54)
        for fn in order:
            if not phase_import(fn, tes4_data, tes5_data, export_dir, config):
                success = False
        print()

    if do_assets:
        print("=" * 54)
        print("  Phase 3: ASSETS")
        print("=" * 54)
        for fn in order:
            if not phase_assets(fn, tes4_data, config):
                success = False
        print()

    if do_lod:
        print("=" * 54)
        print("  Phase 4: LOD GENERATION")
        print("=" * 54)
        for fn in order:
            phase_lod(fn, tes5_data, config)
        print()

    if do_body:
        print("=" * 54)
        print("  Phase 5: MODIFY BODY MESHES")
        print("=" * 54)
        if not phase_modify_body_meshes():
            success = False
        print()

    if do_verify:
        print("=" * 54)
        print("  Verify Output Plugins")
        print("=" * 54)
        for fn in order:
            phase_verify(fn)
        print()

    print("Pipeline complete." if success else "Pipeline completed with errors.")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
