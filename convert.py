"""
TES4-to-TES5 Conversion Pipeline

Pipeline steps (each runnable via --<step>-only):
  export          Parse TES4 binary -> key/value text cache
  import          Build TES5 binary ESM/ESP from text cache
  extract         Pull assets from BSA archives into export/<name>/
  meshes          Convert NIFs and copy textures
  speedtrees      Convert SPT files
  sounds          Convert sound files to XWM
  scripts         Convert TES4 scripts to Papyrus .psc and compile to .pex
  lod             Generate object & terrain LOD meshes
  pack            Pack assets into Skyrim SE BSA archives
  modify-body-meshes  Add greaves partition to character body NIFs

Usage:
  python convert.py                               # full pipeline (export+import+extract+assets)
  python convert.py -f Oblivion.esm               # single file, full pipeline
  python convert.py -f Oblivion.esm --export-only
  python convert.py -f Oblivion.esm --import-only
  python convert.py -f Oblivion.esm --extract-only
  python convert.py -f Oblivion.esm --meshes-only
  python convert.py -f Oblivion.esm --speedtrees-only
  python convert.py -f Oblivion.esm --sounds-only
  python convert.py -f Oblivion.esm --scripts-only
  python convert.py -f Oblivion.esm --lod-only
  python convert.py -f Oblivion.esm --pack-only
  python convert.py --modify-body-meshes
  python convert.py --output-dir /path/to/output -f Oblivion.esm
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
# Ensure stdout/stderr can handle Unicode on Windows consoles (cp1252 → utf-8)
# and make sure they are line-buffered so output flushes promptly when
# the process is not attached to a TTY (important for GUI piping).
if sys.stdout and hasattr(sys.stdout, "buffer"):
    try:
        # Preferred: reconfigure existing TextIOWrapper (Python 3.7+)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
if sys.stderr and hasattr(sys.stderr, "buffer"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

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
                 export_dir: str, config: dict, output_dir: str = None):
    """Import using the Python tes5_import package."""
    from tes5_import.import_main import import_plugin

    export_subdir = os.path.join(export_dir, file_name)
    if not os.path.isdir(export_subdir):
        print(f"[{file_name}] No export directory, skipping import")
        return False

    out_root = output_dir or str(SCRIPT_DIR / "output")
    os.makedirs(out_root, exist_ok=True)
    output_path = os.path.join(out_root, file_name)
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
# Phase 3: Extract BSA assets
# ===========================================================================

def phase_extract(file_name: str, tes4_data: str, config: dict,
                  output_dir: str = None):
    """Extract BSA archives for a plugin into export/<name>/."""
    from asset_convert.asset_pipeline import extract_bsas

    extract_dir = str(SCRIPT_DIR / "export")

    print(f"[{file_name}] Extracting BSA archives...")
    extract_bsas(
        source_file=file_name,
        data_path=tes4_data,
        extract_dir=extract_dir,
    )
    return True


# ===========================================================================
# Phase 4: Convert assets
# ===========================================================================

def phase_assets(file_name: str, config: dict, output_dir: str = None):
    """Convert extracted NIF assets and copy textures to output (meshes only)."""
    from asset_convert.asset_pipeline import convert_meshes

    extract_dir = str(SCRIPT_DIR / "export")
    out_dir     = output_dir or str(SCRIPT_DIR / "output")

    print(f"[{file_name}] Converting meshes (NIFs + textures)...")
    stats = convert_meshes(
        source_file=file_name,
        extract_dir=extract_dir,
        output_dir=out_dir,
    )
    total = sum(v for v in stats.values() if isinstance(v, int))
    print(f"[{file_name}] Meshes complete ({total} items processed)")
    return True


def phase_speedtrees(file_name: str, config: dict, output_dir: str = None):
    """Convert SpeedTree `.spt` files into NIFs (separate step)."""
    from asset_convert.asset_pipeline import convert_speedtrees

    extract_dir = str(SCRIPT_DIR / "export")
    out_dir     = output_dir or str(SCRIPT_DIR / "output")

    print(f"[{file_name}] Converting SpeedTrees (SPTs)...")
    stats = convert_speedtrees(
        source_file=file_name,
        extract_dir=extract_dir,
        output_dir=out_dir,
    )
    s = stats.get('spt_conversion', {})
    print(f"[{file_name}] SpeedTrees complete: ok={s.get('ok',0)} fail={s.get('fail',0)} skip={s.get('skip',0)}")
    return True


# ===========================================================================
# Phase 5: Sound copy
# ===========================================================================

def phase_sounds(file_name: str, config: dict, output_dir: str = None):
    """Convert extracted sound files from BSA to XWM format in output."""
    from asset_convert.asset_pipeline import convert_sounds

    extract_dir = str(SCRIPT_DIR / "export")
    out_dir     = output_dir or str(SCRIPT_DIR / "output")

    print(f"[{file_name}] Converting sounds to XWM...")
    stats = convert_sounds(
        source_file=file_name,
        extract_dir=extract_dir,
        output_dir=out_dir,
    )
    converted = stats.get('converted', 0)
    copied    = stats.get('copied', 0)
    failed    = stats.get('failed', 0)
    print(f"[{file_name}] Sounds complete "
          f"({converted} converted to XWM, {copied} copied, {failed} failed)")
    return True


# ===========================================================================
# Phase 7: Script conversion
# ===========================================================================

def phase_scripts(file_name: str, config: dict, output_dir: str = None):
    """Convert TES4 scripts to Papyrus .psc source files."""
    from tools.oblivion_to_papyrus import convert_all_scripts

    export_subdir = str(SCRIPT_DIR / "export" / file_name)
    if not os.path.isdir(export_subdir):
        print(f"[{file_name}] No export directory, skipping scripts")
        return False

    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    script_dir = out_root / file_name / "scripts" / "source"

    print(f"[{file_name}] Converting scripts to Papyrus...")
    stats = convert_all_scripts(export_subdir, str(script_dir))
    errs = stats['scpt_err'] + stats['info_err'] + stats['qust_err']
    return errs == 0

def phase_compile(file_name: str, config: dict, output_dir: str = None):
    """Compile converted Papyrus .psc scripts to .pex using papyrus compiler.

    Attempts batch compilation first.  If the batch fails (e.g. parser error
    in one script stops the whole run), falls back to per-file compilation so
    valid scripts still produce .pex output.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    script_src = out_root / file_name / "scripts" / "source"
    script_out = out_root / file_name / "scripts"

    if not script_src.is_dir() or not any(script_src.glob("*.psc")):
        print(f"[{file_name}] No .psc scripts found, skipping compile")
        return False

    # Find the compiler
    compiler = SCRIPT_DIR / "tools" / "papyrus-compiler" / "papyrus.exe"
    if not compiler.is_file():
        print(f"[{file_name}] ERROR: papyrus compiler not found at {compiler}")
        return False

    # Find Skyrim source headers (Data\Source\Scripts has native type defs)
    skyrim_headers = _find_skyrim_source_scripts()
    if not skyrim_headers:
        print(f"[{file_name}] ERROR: Skyrim Papyrus source headers not found")
        print("  Expected at: <Skyrim SE>\\Data\\Source\\Scripts\\")
        return False

    script_out.mkdir(parents=True, exist_ok=True)

    psc_files = sorted(script_src.glob("*.psc"))
    psc_count = len(psc_files)
    print(f"[{file_name}] Compiling {psc_count} Papyrus scripts...")

    workers = max(1, (os.cpu_count() or 4) - 1)
    ok_count = 0
    err_count = 0
    err_samples: list = []

    def _compile_one(psc: Path) -> tuple:
        pex_name = psc.stem + ".pex"
        pex_path = script_out / pex_name
        c = [
            str(compiler), "compile",
            "-i", str(psc),
            "-o", str(script_out),
            "-h", str(skyrim_headers),
            "-h", str(script_src),   # other scripts as headers
        ]
        try:
            r = subprocess.run(c, capture_output=True, text=True,
                               timeout=60, cwd=str(SCRIPT_DIR))
            if r.returncode == 0 and pex_path.is_file():
                return (True, "")
            # Extract first error line
            combined = (r.stdout or "") + (r.stderr or "")
            for line in combined.splitlines():
                if "error" in line.lower():
                    return (False, line.strip())
            return (False, f"exit code {r.returncode}")
        except Exception as e:
            return (False, str(e))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_compile_one, psc): psc for psc in psc_files}
        for fut in as_completed(futures):
            success_f, msg = fut.result()
            if success_f:
                ok_count += 1
            else:
                err_count += 1
                if len(err_samples) < 10:
                    err_samples.append(f"  {futures[fut].name}: {msg}")

    print(f"[{file_name}] Compilation: {ok_count}/{psc_count} succeeded, "
          f"{err_count} failed")
    for sample in err_samples:
        print(sample)
    if err_count > 10:
        print(f"  ... and {err_count - 10} more failures")
    return ok_count > 0


def _find_skyrim_source_scripts() -> str:
    """Find Skyrim Papyrus source scripts directory (contains Debug.psc etc.)."""
    # Try from game path
    sse_data = find_game_path("skyrimse")
    if sse_data:
        source_dir = Path(sse_data) / "Source" / "Scripts"
        if source_dir.is_dir() and (source_dir / "Debug.psc").is_file():
            return str(source_dir)
    return ""


# ===========================================================================
# Phase 8: LOD generation
# ===========================================================================

def phase_lod(file_name: str, tes5_data: str, config: dict,
              output_dir: str = None):
    """Generate object LOD and terrain LOD for the converted plugin."""
    from asset_convert.lod_gen import generate_lod
    from asset_convert.terrain_lod import generate_terrain_lod

    out_root   = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    output_dir = out_root / file_name
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
# Phase 9: Pack BSA archives
# ===========================================================================

def phase_pack(file_name: str, config: dict, output_dir: str = None):
    """Pack converted output assets into Skyrim SE BSA archives."""
    from asset_convert.bsa_pack import pack_bsas

    out_dir = output_dir or str(SCRIPT_DIR / "output")
    bsarch  = config.get("bsarchPath") or None

    print(f"[{file_name}] Packing BSAs...")
    results = pack_bsas(
        source_file=file_name,
        output_dir=out_dir,
        bsarch_path=bsarch,
    )
    packed  = len(results['packed'])
    skipped = len(results['skipped'])
    errors  = len(results['errors'])
    print(f"[{file_name}] BSA pack complete: {packed} packed, {skipped} skipped, {errors} errors")
    return errors == 0


# ===========================================================================
# Phase 10: Modify body meshes
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
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="TES4-to-TES5 Conversion Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Default pipeline (no --*-only): export + import + extract + assets\n"
            "Each --*-only flag runs exactly that step and nothing else."
        ),
    )
    parser.add_argument("-f", "--files", nargs="+", metavar="FILE",
                        help="Plugin filename(s) to process (default: all from config)")
    parser.add_argument("--config", metavar="PATH",
                        help="Path to conversion_config.json")
    parser.add_argument("--output-dir", metavar="PATH",
                        help="Output directory (default: output/ in project root)")
    parser.add_argument("--export-only",         action="store_true",
                        help="Parse TES4 binary -> key/value text cache")
    parser.add_argument("--import-only",         action="store_true",
                        help="Convert text cache -> TES5 binary ESM/ESP")
    parser.add_argument("--extract-only",        action="store_true",
                        help="Extract BSA archives into export/<name>/")
    parser.add_argument("--meshes-only",         action="store_true",
                        help="Convert NIFs and copy textures only")
    parser.add_argument("--speedtrees-only",     action="store_true",
                        help="Convert SPT (SpeedTree) files only")
    parser.add_argument("--sounds-only",         action="store_true",
                        help="Copy extracted sound files to output")
    parser.add_argument("--lod-only",            action="store_true",
                        help="Generate object & terrain LOD meshes")
    parser.add_argument("--modify-body-meshes",  action="store_true",
                        help="Add greaves partition to character body NIFs")
    parser.add_argument("--scripts-only",        action="store_true",
                        help="Convert TES4 scripts to Papyrus .psc source")
    parser.add_argument("--pack-only",           action="store_true",
                        help="Pack output assets into Skyrim SE BSA archives")

    args = parser.parse_args()

    config       = load_config(args.config)
    tes4_data, tes5_data = get_paths(config)
    output_dir   = args.output_dir or config.get("outputDir") or str(SCRIPT_DIR / "output")
    export_dir   = str(SCRIPT_DIR / "export")

    os.makedirs(export_dir, exist_ok=True)
    os.makedirs(os.path.join(export_dir, "mappings"), exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    print("=" * 54)
    print("  TES4 -> TES5 Conversion Pipeline")
    print("=" * 54)
    print(f"  Oblivion data : {tes4_data or '(not found)'}")
    print(f"  Skyrim SE data: {tes5_data or '(not found)'}")
    print(f"  Output dir    : {output_dir}")
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
        args.export_only, args.import_only, args.extract_only,
        args.meshes_only, args.speedtrees_only, args.sounds_only,
        args.lod_only, args.modify_body_meshes, args.scripts_only,
        args.pack_only,
    ])
    if _any_only:
        do_export     = args.export_only
        do_import     = args.import_only
        do_extract    = args.extract_only
        do_meshes     = args.meshes_only
        do_speedtrees = args.speedtrees_only
        do_sounds     = args.sounds_only
        do_lod        = args.lod_only
        do_body       = args.modify_body_meshes
        do_scripts    = args.scripts_only
        do_pack       = args.pack_only
    else:
        # Default: export + import + extract + meshes + speedtrees + sounds + scripts
        do_export = do_import = do_extract = True
        do_meshes = do_speedtrees = do_sounds = do_scripts = True
        do_lod = do_body = do_pack = False

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
            if not phase_import(fn, tes4_data, tes5_data, export_dir, config,
                                output_dir=output_dir):
                success = False
        print()

    if do_extract:
        print("=" * 54)
        print("  Phase 3: EXTRACT BSA ARCHIVES")
        print("=" * 54)
        for fn in order:
            if not phase_extract(fn, tes4_data, config):
                success = False
        print()

    if do_meshes:
        print("=" * 54)
        print("  Phase 4: MESH & TEXTURE CONVERSION")
        print("=" * 54)
        for fn in order:
            if not phase_assets(fn, config, output_dir=output_dir):
                success = False
        print()

    if do_speedtrees:
        print("=" * 54)
        print("  Phase 5: SPEEDTREE CONVERSION")
        print("=" * 54)
        for fn in order:
            if not phase_speedtrees(fn, config, output_dir=output_dir):
                success = False
        print()

    if do_sounds:
        print("=" * 54)
        print("  Phase 6: SOUND CONVERSION")
        print("=" * 54)
        for fn in order:
            if not phase_sounds(fn, config, output_dir=output_dir):
                success = False
        print()

    if do_scripts:
        print("=" * 54)
        print("  Phase 7: SCRIPT CONVERSION")
        print("=" * 54)
        for fn in order:
            if not phase_scripts(fn, config, output_dir=output_dir):
                success = False
            if success and not phase_compile(fn, config, output_dir=output_dir):
                success = False
        print()

    if do_lod:
        print("=" * 54)
        print("  Phase 8: LOD GENERATION")
        print("=" * 54)
        for fn in order:
            phase_lod(fn, tes5_data, config, output_dir=output_dir)
        print()

    if do_pack:
        print("=" * 54)
        print("  Phase 9: PACK BSA ARCHIVES")
        print("=" * 54)
        for fn in order:
            if not phase_pack(fn, config, output_dir=output_dir):
                success = False
        print()

    if do_body:
        print("=" * 54)
        print("  Phase 10: MODIFY BODY MESHES")
        print("=" * 54)
        if not phase_modify_body_meshes():
            success = False
        print()

    print("Pipeline complete." if success else "Pipeline completed with errors.")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
