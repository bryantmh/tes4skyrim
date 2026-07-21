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
  modify-body-meshes  Add greaves partition to character body NIFs
  pack            Pack assets into Skyrim SE BSA archives
  pack-zip        Zip converted plugin/BSA files for distribution

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
  python convert.py -f Oblivion.esm --pack-zip-only
  python convert.py --modify-body-meshes
  python convert.py --modify-body-meshes --patch-plugins Skyrim.esm Dawnguard.esm Dragonborn.esm
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

# Suppress console windows when spawned from a console-less parent (pythonw/.pyw)
from subprocess_flags import POPEN_FLAGS as _POPEN_FLAGS, configure_multiprocessing

# multiprocessing.Pool workers (nif/lod conversion) must also inherit a hidden
# console — configure before any pool is created.
configure_multiprocessing()


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
# Phase 1: Export TES4 RECORDS
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

    # Header-only scan: format worker processes re-read record data from
    # their own mmap of the source file (see tes4_export.export).
    header, all_records = read_file(source, parse_subs=False)

    t1 = time.time()
    print(f"  Scanned {len(all_records)} records in {t1-t0:.2f}s")

    os.makedirs(out_dir, exist_ok=True)
    export_header(header, out_dir)

    # Export EVERY record in the file. Records carrying a master's load-order
    # index are overrides of that master and belong to this plugin just as much
    # as its new records (a translation plugin is ~100% overrides) — the import
    # remaps them onto the converted master rather than duplicating it.
    # Auto-detect masters from the binary header for override reporting only.
    masters = get_masters_from_binary(source)

    type_filter = None  # Export all types; skip types are handled by import

    export_file(all_records, out_dir, type_filter=type_filter,
                source_path=source, own_index=len(masters))

    t2 = time.time()
    print(f"[{file_name}] Export complete in {t2-t0:.2f}s")

    return True

# ===========================================================================
# Phase 2: EXTRACT TES4 ARCHIVES
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
# Phase 3: CONVERT MESHES AND TEXTURES
# ===========================================================================

def phase_assets(file_name: str, config: dict, output_dir: str = None,
                 mesh_subdirs=None):
    """Convert extracted NIF assets and copy textures to output (meshes only)."""
    from asset_convert.asset_pipeline import convert_meshes

    extract_dir = str(SCRIPT_DIR / "export")
    out_dir     = output_dir or str(SCRIPT_DIR / "output")

    print(f"[{file_name}] Converting meshes (NIFs + textures)...")
    stats = convert_meshes(
        source_file=file_name,
        extract_dir=extract_dir,
        output_dir=out_dir,
        mesh_subdirs=mesh_subdirs,
    )
    total = sum(v for v in stats.values() if isinstance(v, int))
    print(f"[{file_name}] Meshes complete ({total} items processed)")

    # Book inventory-art: bake each distinct BOOK model's textures onto the
    # vanilla Skyrim reading rigs (see asset_convert/book_inam.py); the import
    # phase points each BOOK's INAM at meshes\tes4\clutter\books\inv\<base>.nif
    from asset_convert.book_inam import generate_book_inams

    _, tes5_data = get_paths(config)
    print(f"[{file_name}] Generating book inventory-art meshes...")
    bstats = generate_book_inams(
        source_file=file_name,
        extract_dir=extract_dir,
        output_dir=out_dir,
        skyrim_data=tes5_data or None,
    )
    print(f"[{file_name}] Book INAM complete: ok={bstats['ok']} "
          f"skip={bstats['skip']} fail={bstats['fail']}")
    return True

# ===========================================================================
# Phase 4: CONVERT SPEEDTREES
# ===========================================================================

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
# Phase 5: CONVERT CREATURES
# ===========================================================================

def phase_creatures(file_name: str, tes5_data: str, config: dict,
                    output_dir: str = None):
    """Convert creatures: generated behavior projects (skeleton.hkx,
    animations, behavior graph), skeleton/body NIF conversion, and
    registration in the merged animation singlefiles.

    Must run BEFORE import: Phase 0f of the importer reads
    export/<name>/creature_projects.json to generate RACE/ARMA/ARMO chains.
    NPC_ humanoids are unaffected (they keep the Skyrim race overrides).
    """
    from asset_convert.creature_pipeline import convert_creatures

    export_subdir = str(SCRIPT_DIR / "export" / file_name)
    if not os.path.isdir(export_subdir):
        print(f"[{file_name}] No export directory, skipping creatures")
        return False
    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    out_meshes = str(out_root / file_name / "meshes")

    print(f"[{file_name}] Converting creatures (behavior projects + meshes)...")
    res = convert_creatures(export_subdir, out_meshes,
                            skyrim_data_path=tes5_data)
    print(f"[{file_name}] Creatures complete "
          f"({len(res['projects'])} projects, {len(res['errors'])} errors)")
    return not res['errors']

# ===========================================================================
# Phase 6: BUILD TES5 PLUGIN
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
    # Every plugin gets its own output folder (output/<plugin>/<plugin>), which
    # is also where the asset/mesh pipeline writes. Create it unconditionally:
    # relying on the folder already existing left plugins with no asset phase
    # (e.g. Translation.esp) written as a bare file in output/, with their
    # voicemap/liptext companions loose in the output root.
    plugin_dir = os.path.join(out_root, file_name)
    os.makedirs(plugin_dir, exist_ok=True)
    output_path = os.path.join(plugin_dir, file_name)

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
# Phase 7: CONVERT SOUNDS
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
# Phase 8: CONVERT SCRIPTS
# ===========================================================================

def phase_scripts(file_name: str, config: dict, output_dir: str = None):
    """Convert TES4 scripts to Papyrus .psc source files."""
    from script_convert.pipeline import convert_all_scripts

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
    compiler = SCRIPT_DIR / "external" / "papyrus-compiler" / "papyrus.exe"
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
            # papyrus.exe keys its cache on the SOURCE only, not the output path,
            # so an unchanged .psc is "already compiled": it exits 0 and writes no
            # .pex at all.  Scripts whose text never varies between runs (the
            # static TES4_ShowBarterMenu / TES4_ShowTrainingMenu / TES4Polyfill)
            # therefore silently produced no .pex and were reported as
            # "exit code 0" failures.  Always ignore the cache.
            "-nocache",
            "-i", str(psc),
            "-o", str(script_out),
            "-h", str(skyrim_headers),
            "-h", str(script_src),   # other scripts as headers
        ]
        try:
            r = subprocess.run(c, capture_output=True, text=True,
                               timeout=60, cwd=str(SCRIPT_DIR), **_POPEN_FLAGS)
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
# Phase 9: GENERATE LOD
# ===========================================================================

def phase_lod(file_name: str, tes5_data: str, config: dict,
              output_dir: str = None):
    """Generate object LOD and terrain LOD for the converted plugin.

    LOD is generated for exactly the worldspaces the SOURCE game shipped distant
    LOD for (detected from the extracted meshes\\landscape\\lod +
    textures\\landscapelod assets — see terrain_lod.shipped_lod_worldspaces).
    That mirrors vanilla precisely and skips child worldspaces (Anvil, Bravil,
    the IC districts, …) which render inside their parent's LOD grid.
    """
    from asset_convert.lod_gen import generate_lod
    from asset_convert.terrain_lod import (generate_terrain_lod,
                                           shipped_lod_worldspaces,
                                           detect_terrain_worldspaces)

    out_root   = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    output_dir = out_root / file_name
    if not output_dir.is_dir():
        print(f"[{file_name}] No output directory found, skipping LOD")
        return False

    esm_path = output_dir / file_name
    if not esm_path.exists():
        print(f"[{file_name}] ESM not found at {esm_path}, skipping LOD")
        return False

    # Which worldspaces get LOD?
    #   1. explicit config override wins (single worldspace),
    #   2. otherwise the worldspaces the source shipped LOD for (the authority),
    #   3. otherwise fall back to auto-detecting the largest root worldspace.
    override = config.get('worldspaceEditorID')
    if override:
        worldspaces = [override]
    else:
        export_dir = SCRIPT_DIR / "export" / file_name
        shipped = shipped_lod_worldspaces(export_dir)
        if shipped:
            worldspaces = [edid for edid, _fid in shipped]
            print(f"[{file_name}] Source shipped LOD for {len(worldspaces)} "
                  f"worldspace(s): {', '.join(worldspaces)}")
        else:
            ranked = detect_terrain_worldspaces(esm_path)
            if ranked:
                worldspaces = [ranked[0][2]]
                print(f"[{file_name}] No shipped LOD found; falling back to "
                      f"largest root worldspace '{ranked[0][2]}' "
                      f"({ranked[0][0]} LAND records)")
            else:
                print(f"[{file_name}] No shipped LOD and no LAND records; "
                      f"skipping LOD")
                return True

    all_ok = True
    for worldspace_edid in worldspaces:
        print(f"[{file_name}] Generating object LOD "
              f"(worldspace: {worldspace_edid})...")
        ok = generate_lod(
            esm_path=esm_path,
            output_dir=output_dir,
            worldspace_edid=worldspace_edid,
        )

        # Terrain LOD: heightmap .btr tiles + composited landscape-texture
        # diffuse (real LTEX textures blended per LAND alpha layers) + normals.
        print(f"[{file_name}] Generating terrain LOD "
              f"(worldspace: {worldspace_edid})...")
        ok_terrain = generate_terrain_lod(
            esm_path=esm_path,
            output_dir=output_dir,
            worldspace_edid=worldspace_edid,
        )
        all_ok = all_ok and ok and ok_terrain

    return all_ok

# ===========================================================================
# Phase 10: PATCH SKYRIM (SLOT 44 BODY MESHES)
# ===========================================================================

def phase_modify_body_meshes(tes5_data: str = None, plugins: list = None,
                             output_dir: str = None):
    """Add greaves partition to vanilla Skyrim character body NIFs, then
    generate ONE merged companion slot-44 patch covering `plugins`.

    The patch (tools/patch_body_slots.py) is mandatory alongside the split
    body meshes: without slot 44 on the NakedTorso ARMA the new lower-body
    skin partition never renders and naked thighs are invisible.

    `plugins` defaults to just Skyrim.esm; the GUI passes the user's whole
    selected load order (Skyrim.esm + DLCs + Update.esm + any chosen mods)
    so every installed armor mod is folded into the same "Slot44 Patch.esp",
    with unused masters cleaned once across the merged result. Each plugin
    not present in tes5_data is skipped with a warning rather than failing
    the whole step.
    """
    if not tes5_data:
        print("WARNING: Skyrim data path not found - slot-44 patch not "
              "generated (run tools/patch_body_slots.py manually)")
        return True

    plugins = plugins or ["Skyrim.esm"]
    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    out_path = out_root / "Oblivion.esm" / "Slot44 Patch.esp"

    plugin_paths = []
    for name in plugins:
        plugin_path = Path(tes5_data) / name
        if not plugin_path.exists():
            print(f"WARNING: {name} not found - skipping")
            continue
        plugin_paths.append(str(plugin_path))
    if not plugin_paths:
        print("WARNING: none of the selected plugins were found - slot-44 "
              "patch not generated")
        return True

    patch_script = SCRIPT_DIR / "tools" / "patch_body_slots.py"
    ret = subprocess.run(
        [sys.executable, str(patch_script), *plugin_paths, "-o", str(out_path)],
        cwd=str(SCRIPT_DIR), capture_output=True, text=True, **_POPEN_FLAGS)
    if ret.stdout:
        print(ret.stdout, end="")
    if ret.stderr:
        print(ret.stderr, end="")
    return ret.returncode == 0


# ===========================================================================
# Phase 11a: PRUNE UNREFERENCED TEXTURES
# ===========================================================================

def phase_prune_textures(file_name: str, config: dict, output_dir: str = None,
                         dry_run: bool = False):
    """Delete output textures no shipped mesh or record references.

    Runs after LOD/speedtree/terrain generation (the last producers of meshes
    that can name a texture) and before packing, so the BSAs never carry the
    face/body/eye art of the character meshes the conversion skips.
    """
    from asset_convert import texture_prune

    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    plugin_dir = out_root / file_name
    export_dir = SCRIPT_DIR / "export" / file_name
    if not plugin_dir.is_dir():
        print(f"[{file_name}] No output directory found, skipping texture prune")
        return False

    print(f"[{file_name}] Pruning unreferenced textures"
          + (" (dry run)" if dry_run else "") + "...")
    try:
        kept, removed, freed = texture_prune.prune(plugin_dir, export_dir,
                                                   dry_run=dry_run)
    except RuntimeError as e:
        print(f"[{file_name}] SKIPPED: {e}")
        return False
    verb = "would remove" if dry_run else "removed"
    print(f"[{file_name}] Textures: {kept} kept, {removed} {verb} "
          f"({freed / 1e6:.0f} MB freed)")
    return True


# ===========================================================================
# Phase 11: PACK BSA ARCHIVES
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
# Phase 12: PACK ZIP ARCHIVES
# ===========================================================================

def phase_pack_zip(file_name: str, config: dict, output_dir: str = None):
    """Zip the converted plugin (.esm/.esl/.esp) and .bsa files for distribution.

    The zip is placed adjacent to the per-file output folder (i.e. inside
    output_dir, alongside output_dir/file_name/) and named "<file_name>.zip".
    """
    import zipfile

    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    src_root = out_root / file_name
    if not src_root.is_dir():
        print(f"[{file_name}] Source not found: {src_root}, skipping zip pack")
        return False

    zip_path = out_root / f"{file_name}.zip"

    packed = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for ext in ("*.esm", "*.esl", "*.esp", "*.bsa"):
            for src in sorted(src_root.glob(ext)):
                zf.write(src, arcname=src.name)
                packed += 1

    if packed == 0:
        zip_path.unlink(missing_ok=True)
        print(f"[{file_name}] No plugin/BSA files found, skipping zip pack")
        return False

    print(f"[{file_name}] Zip pack complete -> {zip_path} ({packed} files)")
    return True


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
    parser.add_argument("--creatures-only",      action="store_true",
                        help="Convert creatures (behavior projects, "
                             "skeleton/body meshes, animation registration)")
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
    parser.add_argument("--pack-zip-only",       action="store_true",
                        help="Zip converted plugin/BSA files for distribution")
    parser.add_argument("--prune-textures-only", action="store_true",
                        help="Delete output textures no mesh or record "
                             "references (run after LOD, before packing)")
    parser.add_argument("--dry-run",             action="store_true",
                        help="With --prune-textures-only: report what would be "
                             "deleted without deleting it")
    parser.add_argument("--mesh-subdirs",        nargs="+", metavar="SUBDIR",
                        help="Limit mesh conversion to these root subfolders "
                             "(e.g. architecture clutter). Default: all.")
    parser.add_argument("--patch-plugins",       nargs="+", metavar="PLUGIN",
                        help="Skyrim plugin filenames to generate a slot-44 "
                             "patch for (e.g. Skyrim.esm Dawnguard.esm). "
                             "Default: Skyrim.esm only.")

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

    # Files to process always come from -f/--files (CLI) or the GUI, which
    # passes the selected plugins via -f. conversion_config.json no longer
    # carries a "files" list; config.get("files") is only a legacy fallback.
    order = topological_order(args.files or config.get("files", []), tes4_data)
    if not order:
        print("No files to process.")
        return 0
    print(f"  Files: {', '.join(order)}")
    print()

    # ── Determine which steps to run ──────────────────────────────────────
    _any_only = any([
        args.export_only, args.import_only, args.extract_only,
        args.meshes_only, args.speedtrees_only, args.creatures_only,
        args.sounds_only,
        args.lod_only, args.modify_body_meshes, args.scripts_only,
        args.pack_only, args.pack_zip_only, args.prune_textures_only,
    ])
    if _any_only:
        do_export       = args.export_only
        do_import       = args.import_only
        do_extract      = args.extract_only
        do_meshes       = args.meshes_only
        do_speedtrees   = args.speedtrees_only
        do_creatures    = args.creatures_only
        do_sounds       = args.sounds_only
        do_lod          = args.lod_only
        do_skyrim_patch = args.modify_body_meshes
        do_scripts      = args.scripts_only
        do_pack_bsa     = args.pack_only
        do_pack_zip     = args.pack_zip_only
        do_prune        = args.prune_textures_only
    else:
        # Default
        do_export = do_extract = do_meshes = do_speedtrees = True
        do_creatures = do_import = do_sounds = do_scripts = True
        do_lod = do_skyrim_patch = do_pack_bsa = do_prune = True
        do_pack_zip = False

    success = True

    if do_export:
        print("=" * 54)
        print("  Phase 1: EXPORT TES4 RECORDS")
        print("=" * 54)
        for fn in order:
            if not phase_export(fn, tes4_data, export_dir, config):
                success = False
        print()

    if do_extract:
        print("=" * 54)
        print("  Phase 2: EXTRACT TES4 ARCHIVES")
        print("=" * 54)
        for fn in order:
            if not phase_extract(fn, tes4_data, config):
                success = False
        print()

    if do_meshes:
        print("=" * 54)
        print("  Phase 3: CONVERT MESHES AND TEXTURES")
        print("=" * 54)
        for fn in order:
            if not phase_assets(fn, config, output_dir=output_dir,
                                mesh_subdirs=getattr(args, 'mesh_subdirs', None)):
                success = False
        print()

    if do_speedtrees:
        print("=" * 54)
        print("  Phase 4: CONVERT SPEEDTREES")
        print("=" * 54)
        for fn in order:
            if not phase_speedtrees(fn, config, output_dir=output_dir):
                success = False
        print()

    if do_creatures:
        print("=" * 54)
        print("  Phase 5: CONVERT CREATURES")
        print("=" * 54)
        for fn in order:
            if not phase_creatures(fn, tes5_data, config,
                                   output_dir=output_dir):
                success = False
        print()

    if do_import:
        print("=" * 54)
        print("  Phase 6: BUILD TES5 PLUGIN")
        print("=" * 54)
        for fn in order:
            if not phase_import(fn, tes4_data, tes5_data, export_dir, config,
                                output_dir=output_dir):
                success = False
        print()

    if do_sounds:
        print("=" * 54)
        print("  Phase 7: CONVERT SOUNDS")
        print("=" * 54)
        for fn in order:
            if not phase_sounds(fn, config, output_dir=output_dir):
                success = False
        print()

    if do_scripts:
        print("=" * 54)
        print("  Phase 8: CONVERT SCRIPTS")
        print("=" * 54)
        for fn in order:
            if not phase_scripts(fn, config, output_dir=output_dir):
                success = False
            if success and not phase_compile(fn, config, output_dir=output_dir):
                success = False
        print()

    if do_lod:
        print("=" * 54)
        print("  Phase 9: GENERATE LOD")
        print("=" * 54)
        for fn in order:
            phase_lod(fn, tes5_data, config, output_dir=output_dir)
        print()

    if do_skyrim_patch:
        print("=" * 54)
        print("  Phase 10: PATCH SKYRIM (SLOT 44 BODY MESHES)")
        print("=" * 54)
        if not phase_modify_body_meshes(
                tes5_data, plugins=getattr(args, 'patch_plugins', None),
                output_dir=output_dir):
            success = False
        print()

    if do_prune:
        # TODO: This should be part of packing BSA
        print("=" * 54)
        print("  Phase 11a: PRUNE UNREFERENCED TEXTURES")
        print("=" * 54)
        for fn in order:
            phase_prune_textures(fn, config, output_dir=output_dir,
                                 dry_run=args.dry_run)
        print()

    if do_pack_bsa:
        print("=" * 54)
        print("  Phase 11: PACK BSA ARCHIVES")
        print("=" * 54)
        for fn in order:
            if not phase_pack(fn, config, output_dir=output_dir):
                success = False
        print()

    if do_pack_zip:
        print("=" * 54)
        print("  Phase 12: PACK ZIP ARCHIVES")
        print("=" * 54)
        for fn in order:
            if not phase_pack_zip(fn, config, output_dir=output_dir):
                success = False
        print()

    print("Pipeline complete." if success else "Pipeline completed with errors.")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
