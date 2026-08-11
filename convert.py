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
  pack            Pack assets into Skyrim SE BSA archives (textures nothing
                  references are left out of the archive, never deleted)
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
import re
import shutil
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

# Papyrus batch compilation (see phase_compile).  An error line from
# papyrus.exe looks like:  <path>\Foo.psc:12:3: Checker error: <message>
# The compiler aborts the whole batch on the first bad file, so each failing
# script is quarantined and the batch retried; this bounds that loop.
_PSC_ERR_RE = re.compile(r'^.*?([^\\/:]+\.psc):\d+:\d+:\s*(.*)$')
_MAX_BATCH_RETRIES = 25

# Suppress console windows when spawned from a console-less parent (pythonw/.pyw)
from subprocess_flags import (POPEN_FLAGS as _POPEN_FLAGS,
                              configure_multiprocessing, windows_cmd)
from process_job import create_pool_job, describe_limit
from worker_budget import worker_count
from collision_options import (
    WINDING_FIX_DEFAULT_PLUGINS,
    WINDING_FIX_ENV_VAR,
    default_for_plugin,
)

# multiprocessing.Pool workers (nif/lod conversion) must also inherit a hidden
# console — configure before any pool is created.
configure_multiprocessing()

# Put this process (and therefore every pool worker and helper .exe it spawns)
# into a Windows Job Object. If this process dies WITHOUT cleanup — a crash, an
# external kill, the console closed — the kernel terminates the whole job, so no
# console-less pythonw.exe workers are left orphaned holding RAM and file
# handles. Also caps committed memory job-wide. Must run before any pool or
# subprocess is created; no-ops off Windows and never raises.
create_pool_job()


def load_config(config_path: str = None) -> dict:
    path = Path(config_path) if config_path else SCRIPT_DIR / "conversion_config.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_CONFIG_PATH_KEYS = {"oblivion": "tes4DataPath", "skyrimse": "tes5DataPath"}


def find_game_path(game: str, config: dict = None) -> str:
    """Auto-detect a game's Data path.

    Windows behaviour is unchanged: the registry is consulted and this is
    normally all that's needed. Off Windows `winreg` doesn't exist, so that
    lookup is a no-op there -- the equivalent is an explicit path in
    conversion_config.json's tes4DataPath/tes5DataPath, checked FIRST (this
    also lets a Windows user override a registry entry that points at the
    wrong install). Empty/absent by default, so on Windows this changes
    nothing.
    """
    if config:
        key = _CONFIG_PATH_KEYS.get(game)
        if key:
            configured = config.get(key, "") or ""
            if configured and os.path.isdir(configured):
                return configured
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
    tes4 = find_game_path("oblivion", config)
    tes5 = find_game_path("skyrimse", config)
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
                 mesh_subdirs=None, winding_fix=None):
    """Convert extracted NIF assets and copy textures to output (meshes only).

    `winding_fix` tri-states the collision winding repair: True/False force it,
    None takes the per-plugin default for `file_name`.  The decision is pinned
    into the environment because the repair runs inside multiprocessing mesh
    workers, which inherit the environment but not this call's arguments.
    """
    from asset_convert.asset_pipeline import convert_meshes

    extract_dir = str(SCRIPT_DIR / "export")
    out_dir     = output_dir or str(SCRIPT_DIR / "output")

    if winding_fix is None:
        winding_fix = default_for_plugin(file_name)
        origin = "plugin default"
    else:
        origin = "requested"
    os.environ[WINDING_FIX_ENV_VAR] = "1" if winding_fix else "0"
    print(f"[{file_name}] Collision winding repair: "
          f"{'ON' if winding_fix else 'OFF'} ({origin})")

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
    # A plugin places its MASTERS' book models too, and those meshes/textures
    # were extracted into the master's export dir only.
    from asset_convert.terrain_lod import _master_names as _tes4_masters
    bstats = generate_book_inams(
        source_file=file_name,
        extract_dir=extract_dir,
        output_dir=out_dir,
        skyrim_data=tes5_data or None,
        master_names=_tes4_masters(Path(extract_dir) / file_name),
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

    # The animation singlefiles are ONE shared file in Data. A child plugin
    # registers its creatures in its MASTER's copy rather than shipping a
    # rival copy of its own (see _shared_singlefile_dir).
    from asset_convert.terrain_lod import _master_names
    master_dirs = [out_root / m
                   for m in _master_names(Path(export_subdir))
                   if (out_root / m).is_dir()]

    print(f"[{file_name}] Converting creatures (behavior projects + meshes)...")
    res = convert_creatures(export_subdir, out_meshes,
                            skyrim_data_path=tes5_data,
                            master_dirs=master_dirs)
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
    from tes5_import.override_merge import MissingMasterOutputError

    export_subdir = os.path.join(export_dir, file_name)
    if not os.path.isdir(export_subdir):
        print(f"[{file_name}] No export directory, skipping import")
        return False

    # Navmesh generation is the slowest part of this phase, and a prebuilt
    # cache is published with each release.  Pick it up automatically -- from
    # navmesh_cache/ if the user dropped a zip there, else by downloading the
    # matching asset -- so nobody has to know a command exists.  Never fatal:
    # on any problem the navmesh just regenerates as it always did.
    # Opt out with TESCONV_NO_CACHE_DOWNLOAD=1 (metered connections).
    try:
        from tools.navmesh_cache import auto_install
        auto_install(file_name,
                     allow_download=os.environ.get(
                         'TESCONV_NO_CACHE_DOWNLOAD', '') not in ('1', 'true'))
    except Exception:
        pass

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
    try:
        converted, errors = import_plugin(
            export_dir=export_subdir,
            output_path=output_path,
            masters=masters,
            is_esm=is_esm,
            output_root=out_root,
        )
    except MissingMasterOutputError as e:
        print(f"[{file_name}] ERROR: {e}")
        return False

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
    skyrim_headers = _find_skyrim_source_scripts(config)
    if not skyrim_headers:
        print(f"[{file_name}] ERROR: Skyrim Papyrus source headers not found")
        print("  Expected at: <Skyrim SE>\\Data\\Source\\Scripts\\")
        return False

    script_out.mkdir(parents=True, exist_ok=True)

    # An override plugin's scripts declare properties typed as the MASTER's
    # converted scripts (`TES4_NQ16Script Property ...`), because the record
    # they name carries that master's SCRI.  Those .psc live in the master's
    # own output, so without them on the header path the compiler reports
    # "undefined type" — 198 of Translation.esp's scripts. They are headers
    # only: the master's own run compiles and ships the .pex.
    from script_convert.cross_ref import master_names
    master_src_dirs = []
    for m in master_names(SCRIPT_DIR / "export" / file_name):
        d = out_root / m / "scripts" / "source"
        if d.is_dir():
            master_src_dirs.append(d)
        else:
            print(f"[{file_name}] WARNING: master scripts not found ({d}); "
                  f"scripts referencing {m}'s script types will not compile")

    psc_files = sorted(script_src.glob("*.psc"))
    psc_count = len(psc_files)
    print(f"[{file_name}] Compiling {psc_count} Papyrus scripts...")

    workers = worker_count()
    ok_count = 0
    err_count = 0
    err_samples: list = []

    def _header_args() -> list:
        h = ["-h", str(skyrim_headers), "-h", str(script_src)]
        for d in master_src_dirs:
            h += ["-h", str(d)]
        return h

    def _compile_batch(quarantine: set) -> tuple:
        """Compile the whole source dir in ONE compiler process.

        papyrus.exe parses the ~3,000 Skyrim headers once per invocation, so
        compiling per-file paid that cost 15,961 times (~82 ms each = ~22 min
        of serial CPU, which is what a 4-core machine actually experiences).
        Batch mode is ~2.4 ms/script marginal — the whole plugin in ~40 s in a
        single process, with no dependence on the core count at all.

        The catch, and why this is not a plain swap: the compiler ABORTS the
        run on the first bad file and writes NO .pex at all (measured: 1 broken
        script of 201 -> 0 .pex).  So a failing file must be quarantined and
        the batch retried.  Scanner/parser errors surface one file at a time;
        checker errors surface for every bad file at once.  Either way each
        error line names its file, so `quarantine` grows by at least one entry
        per pass and the loop terminates.

        Returns (ok, errors, bad_files).
        """
        # A quarantined file must leave the input directory, so a failing batch
        # runs against a staging copy.  Built ONCE and then maintained
        # incrementally: re-copying ~16k scripts on every retry costs far more
        # than the compile itself, and each retry only ever removes files.
        if quarantine:
            stage = script_out / "_batch_src"
            if not stage.is_dir():
                stage.mkdir(parents=True, exist_ok=True)
                for p in psc_files:
                    shutil.copy2(p, stage / p.name)
            for name in quarantine:
                try:
                    (stage / name).unlink()
                except FileNotFoundError:
                    pass
            in_dir = stage
        else:
            in_dir = script_src

        c = [str(compiler), "compile", "-nocache",
             "-i", str(in_dir), "-o", str(script_out)] + _header_args()
        try:
            r = subprocess.run(windows_cmd(c), capture_output=True, text=True,
                               timeout=1800, cwd=str(SCRIPT_DIR), **_POPEN_FLAGS)
        except Exception as e:
            return (False, [f"batch: {e}"], set())

        combined = (r.stdout or "") + (r.stderr or "")
        bad: set = set()
        errors: list = []
        # Error lines look like: <path>\Foo.psc:12:3: Checker error: ...
        for line in combined.splitlines():
            m = _PSC_ERR_RE.match(line.strip())
            if m:
                bad.add(m.group(1))
                errors.append(f"{m.group(1)}: {m.group(2).strip()}")
        ok = not bad and "failed to compile" not in combined
        if not ok and not bad:
            # Failed without naming a file — cannot make progress by
            # quarantining, so let the caller fall back to per-file.
            errors.append("batch failed without naming a file")
        return (ok, errors, bad)

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
        for d in master_src_dirs:
            c += ["-h", str(d)]     # the masters' converted script types
        try:
            r = subprocess.run(windows_cmd(c), capture_output=True, text=True,
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

    all_errors: list[str] = []

    # ── Batch first ──────────────────────────────────────────────────────
    # One compiler process for the whole directory. Only the files it names as
    # broken fall back to a per-file compile, so a healthy build never spawns
    # 15,961 processes and a broken one still produces every good .pex.
    t_c = time.time()
    quarantine: set = set()
    batch_ok = False
    give_up = False
    for _attempt in range(_MAX_BATCH_RETRIES):
        ok, errs, bad = _compile_batch(quarantine)
        if ok:
            batch_ok = True
            break
        new_bad = bad - quarantine
        if not new_bad:
            # No progress possible (unnamed failure, or the same file again).
            give_up = True
            break
        quarantine |= new_bad
        print(f"  batch: quarantining {len(new_bad)} failing script(s), "
              f"retrying ({len(quarantine)} total)")
    else:
        give_up = True

    shutil.rmtree(script_out / "_batch_src", ignore_errors=True)

    if batch_ok or not give_up:
        # Every non-quarantined script compiled in the batch pass.
        ok_count = psc_count - len(quarantine)
        # Recheck the quarantined ones individually: a file can be dragged into
        # a batch failure by a *dependency* error, and compiles fine alone.
        if quarantine:
            print(f"  batch: {ok_count} compiled; re-checking "
                  f"{len(quarantine)} quarantined script(s) individually...")
            for name in sorted(quarantine):
                psc = script_src / name
                success_f, msg = _compile_one(psc)
                if success_f:
                    ok_count += 1
                else:
                    err_count += 1
                    all_errors.append(f"{name}: {msg}")
                    if len(err_samples) < 10:
                        err_samples.append(f"  {name}: {msg}")
        print(f"  Batch compile: {time.time() - t_c:.1f}s")
    else:
        # The batch could not be made to make progress — fall back to the
        # original per-file path so a pathological case still ships .pex files.
        print("  batch compile could not isolate the failure; "
              "falling back to per-file compilation")
        ok_count = err_count = 0
        err_samples.clear()
        all_errors.clear()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_compile_one, psc): psc for psc in psc_files}
            for fut in as_completed(futures):
                success_f, msg = fut.result()
                if success_f:
                    ok_count += 1
                else:
                    err_count += 1
                    all_errors.append(f"{futures[fut].name}: {msg}")
                    if len(err_samples) < 10:
                        err_samples.append(f"  {futures[fut].name}: {msg}")
        print(f"  Per-file compile: {time.time() - t_c:.1f}s")

    print(f"[{file_name}] Compilation: {ok_count}/{psc_count} succeeded, "
          f"{err_count} failed")
    for sample in err_samples:
        print(sample)
    if err_count > 10:
        print(f"  ... and {err_count - 10} more failures")
    # The console list is capped at 10, which hid the long tail of real compile
    # errors.  Always dump the complete list next to the scripts so a failing
    # build can be worked through in full instead of ten at a time.
    log_path = script_out / "compile_errors.log"
    if all_errors:
        try:
            log_path.write_text("\n".join(sorted(all_errors)) + "\n",
                                encoding="utf-8")
            print(f"  full error list: {log_path}")
        except OSError:
            pass
    else:
        # A clean build must REMOVE the previous run's log.  Leaving it behind
        # made a green build look red: the log outlives the failure it describes
        # and the next reader trusts it over the console summary.
        try:
            log_path.unlink(missing_ok=True)
        except OSError:
            pass
    return ok_count > 0


def _find_skyrim_source_scripts(config: dict = None) -> str:
    """Find Skyrim Papyrus source scripts directory (contains Debug.psc etc.)."""
    # Try from game path
    sse_data = find_game_path("skyrimse", config)
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
    from asset_convert.lod_gen import (generate_lod,
                                       _textures_root as _lod_textures_root)
    from asset_convert.terrain_lod import (generate_terrain_lod,
                                           shipped_lod_worldspaces,
                                           detect_terrain_worldspaces,
                                           changed_lod_cells,
                                           _master_names as _tes4_master_names,
                                           _parse_land_records as _terrain_parse_land,
                                           count_land_records as _terrain_count_land,
                                           _find_worldspace_fid)

    def _esm_defines_worldspace(path, edid):
        raw = path.read_bytes()
        return _find_worldspace_fid(raw, len(raw), edid) is not None

    def _worldspace_land_count(path, edid):
        """How many of this worldspace's LAND records the file actually holds.

        Containing a WRLD record is NOT the same as owning the worldspace: an
        override plugin ships a WRLD override (and DLCBattlehornCastle ships
        10 LAND overrides) while the other ~14,700 LAND records — and every
        landscape texture — live in the master. Routing on the WRLD record
        alone made the plugin its own record source, so terrain LOD was baked
        from 10 isolated cells with no surrounding terrain and no LTEX
        textures. The bulk of the terrain is what decides.

        Uses the COUNT-ONLY scan: this needs a number, and the full parse
        decodes VHGT/VCLR and the whole layer tree for every record before
        throwing it away (2.8 s wasted per worldspace on Tamriel, once for this
        plugin plus once per master, across all 18 worldspaces).
        """
        try:
            return _terrain_count_land(Path(path), edid)
        except Exception:
            return 0

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

    # A plugin can ship LOD assets for a worldspace defined by one of its
    # MASTERS rather than by itself — the GOTY DLCShiveringIsles.esp is an
    # 85-byte header-only stub whose BSA still carries every SEWorld tile
    # (all SI records were merged into Oblivion.esm). The generators read
    # WRLD/CELL/LAND records out of one ESM, so for those worldspaces point
    # them at the master's converted output, which is where the records are.
    # Only the RECORDS move; the assets and the generated LOD stay in this
    # plugin's own output dir, which is what it ships. The master's dir is
    # returned alongside so anything the master's own LOD run already covers
    # is skipped rather than duplicated here.
    def _records_esm(edid):
        own = (_worldspace_land_count(esm_path, edid)
               if _esm_defines_worldspace(esm_path, edid) else -1)
        best = None
        for master in _tes4_master_names(SCRIPT_DIR / "export" / file_name):
            m_dir = out_root / master
            m_esm = m_dir / master
            if not (m_esm.exists() and _esm_defines_worldspace(m_esm, edid)):
                continue
            n = _worldspace_land_count(m_esm, edid)
            if best is None or n > best[0]:
                best = (n, m_esm, m_dir, master)
        # Whoever holds the BULK of the terrain owns the records; this plugin
        # only wins when no master has more. An override plugin ships a WRLD
        # override and a handful of LAND records, so counting records rather
        # than presence is what keeps the master the source.
        if best is not None and best[0] > own:
            print(f"[{file_name}] Worldspace '{edid}': {best[3]} holds "
                  f"{best[0]} LAND records vs this plugin's {max(own, 0)}; "
                  f"sourcing records from the master")
            return best[1], [best[2]]
        if own >= 0:
            return esm_path, []
        return None, []

    # A plugin routinely places its MASTERS' models in its own worldspace
    # (Morrowind_ob uses Oblivion architecture), and those models — and their
    # textures and generated _far.nif LOD — were converted into the master's
    # output only. The .bto tiles this plugin bakes still reference them, so the
    # master's assets are a lookup fallback for every worldspace, feeding BOTH
    # master_mesh_dirs and master_texture_dirs. This is deliberately independent
    # of `_records_esm`, which reports a master only when the master also owns
    # the WORLDSPACE: a plugin that owns its own worldspace still borrows its
    # masters' models, so tying mesh reuse to record ownership made
    # ElsweyrAnequina re-derive 882 of Oblivion.esm's billboards and then drop
    # every one of them for having no full model in its own tree.
    master_asset_dirs = [out_root / m
                         for m in _tes4_master_names(SCRIPT_DIR / "export"
                                                     / file_name)
                         if (out_root / m).is_dir()]

    all_ok = True
    for worldspace_edid in worldspaces:
        rec_esm, extra_assets = _records_esm(worldspace_edid)
        if rec_esm is None:
            print(f"[{file_name}] Worldspace '{worldspace_edid}' not found in "
                  f"{esm_path.name} or any converted master; skipping its LOD")
            continue

        # When the worldspace belongs to a MASTER, rec_esm is the master's
        # output and this plugin's own records are an OVERLAY on top of it.
        # Without that overlay the plugin's authored terrain and references
        # never reach LOD: DLCBattlehornCastle regrades 10 Tamriel cells, and
        # building LOD from the master alone left distant terrain showing the
        # ORIGINAL ground beside the castle's new ground — one visibly ruined
        # quadrant. Only the tiles the plugin actually touches are rebuilt;
        # every other tile the master already generated is still correct.
        overlays = []
        only_cells = None
        if rec_esm != esm_path:
            overlays = [esm_path]
            only_cells = changed_lod_cells(esm_path, rec_esm, worldspace_edid)
            if not only_cells:
                print(f"[{file_name}] No LOD-affecting changes in "
                      f"'{worldspace_edid}'; master's LOD already correct")
                continue
            print(f"[{file_name}] {len(only_cells)} cell(s) changed in "
                  f"'{worldspace_edid}'; rebuilding only the tiles they touch")

        print(f"[{file_name}] Generating object LOD "
              f"(worldspace: {worldspace_edid})...")
        ok = generate_lod(
            esm_path=rec_esm,
            output_dir=output_dir,
            worldspace_edid=worldspace_edid,
            master_dirs=extra_assets,
            master_mesh_dirs=master_asset_dirs,
            master_texture_dirs=master_asset_dirs,
            overlay_paths=overlays,
            only_cells=only_cells,
        )

        # Terrain LOD: heightmap .btr tiles + composited landscape-texture
        # diffuse (real LTEX textures blended per LAND alpha layers) + normals.
        print(f"[{file_name}] Generating terrain LOD "
              f"(worldspace: {worldspace_edid})...")
        ok_terrain = generate_terrain_lod(
            esm_path=rec_esm,
            output_dir=output_dir,
            worldspace_edid=worldspace_edid,
            overlay_paths=overlays,
            only_cells=only_cells,
            # The master's landscape textures: an override plugin converts
            # none of them into its own output, and without this every
            # diffuse lookup misses and the tiles composite to flat grey.
            extra_texture_roots=[_lod_textures_root(Path(d))
                                 for d in master_asset_dirs],
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
    # Straight into output/, NOT into a per-plugin folder. This step takes no
    # `-f` and patches the vanilla Skyrim body records for the whole load
    # order, so it belongs to no single conversion. Hardcoding "Oblivion.esm"
    # put it somewhere `--pack-only -f <other plugin>` never looks: converting
    # Nehrim created an otherwise-empty output/Oblivion.esm/ holding just this
    # file, and it shipped with nothing.
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / "Slot44 Patch.esp"

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
# Phase 11: PACK BSA ARCHIVES
# ===========================================================================

def phase_pack(file_name: str, config: dict, output_dir: str = None):
    """Pack converted output assets into Skyrim SE BSA archives.

    Textures nothing references are filtered out as the archive is staged, so
    output/ keeps the full loose tree for testing (see bsa_pack).
    """
    from asset_convert.bsa_pack import pack_bsas

    out_dir = output_dir or str(SCRIPT_DIR / "output")
    bsarch  = config.get("bsarchPath") or None
    export_dir = SCRIPT_DIR / "export" / file_name

    print(f"[{file_name}] Packing BSAs...")
    results = pack_bsas(
        source_file=file_name,
        output_dir=out_dir,
        bsarch_path=bsarch,
        export_dir=str(export_dir) if export_dir.is_dir() else None,
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
    parser.add_argument("--mesh-subdirs",        nargs="+", metavar="SUBDIR",
                        help="Limit mesh conversion to these root subfolders "
                             "(e.g. architecture clutter). Default: all.")
    parser.add_argument("--patch-plugins",       nargs="+", metavar="PLUGIN",
                        help="Skyrim plugin filenames to generate a slot-44 "
                             "patch for (e.g. Skyrim.esm Dawnguard.esm). "
                             "Default: Skyrim.esm only.")
    # Collision winding repair (asset_convert/collision.py). Tri-state: the flag
    # forces it on, --no- forces it off, and unspecified (None) defers to the
    # per-plugin default in collision_options, resolved separately for each file.
    winding = parser.add_mutually_exclusive_group()
    winding.add_argument("--collision-winding-fix", dest="collision_winding_fix",
                         action="store_true", default=None,
                         help="Rewind reversed collision triangles (fall-through-"
                              "floor repair). Default: on only for "
                              + ", ".join(sorted(WINDING_FIX_DEFAULT_PLUGINS)))
    winding.add_argument("--no-collision-winding-fix", dest="collision_winding_fix",
                         action="store_false", default=None,
                         help="Disable the collision winding repair.")

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
    print(f"  {describe_limit()}")
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
        args.pack_only, args.pack_zip_only,
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
    else:
        # Default
        do_export = do_extract = do_meshes = do_speedtrees = True
        do_creatures = do_import = do_sounds = do_scripts = True
        do_lod = do_skyrim_patch = do_pack_bsa = True
        do_pack_zip = False

    # ── Dependency preflight ─────────────────────────────────────────────────
    # Check every selected phase BEFORE running any of them.  A phase whose
    # tool is missing does not fail loudly on its own -- Sounds without ffmpeg
    # just ships a mute plugin -- so the run stops at the first gap and prints
    # what to install at the very bottom of the console, where the GUI log
    # leaves it on screen.
    import preflight
    # A version mismatch is a warning, not a gate: the pipeline does run on
    # another 3.x once the navmesh extension is rebuilt.  Printed before the
    # dependency check so the two do not interleave.
    _pywarn = preflight.python_version_warning()
    if _pywarn:
        print(preflight.format_python_warning(_pywarn))
    _selected = [name for name, on in (
        ('export',       do_export),
        ('extract',      do_extract),
        ('meshes',       do_meshes),
        ('speedtrees',   do_speedtrees),
        ('creatures',    do_creatures),
        ('import',       do_import),
        ('sounds',       do_sounds),
        ('scripts',      do_scripts),
        ('lod',          do_lod),
        ('skyrim_patch', do_skyrim_patch),
        ('pack_bsa',     do_pack_bsa),
        ('pack_zip',     do_pack_zip),
    ) if on]
    _failed = preflight.check_phases(_selected)
    if _failed is not None:
        _phase, _missing = _failed
        _skipped = _selected[_selected.index(_phase) + 1:]
        print(preflight.format_report(_phase, _missing, _skipped))
        return preflight.RC_MISSING_DEP

    success = True

    # Per-step, per-plugin outcome, so a version stamp is only recorded for
    # what actually completed.  Marking a failed step "run at 0.59" would make
    # the next upgrade check report it clean and quietly ship stale output, so
    # a step is recorded only when every plugin in `order` succeeded.
    import version as _version
    _step_ok: dict[str, dict[str, bool]] = {}

    def _mark(step_key: str, fn: str, ok: bool) -> None:
        _step_ok.setdefault(step_key, {})[fn] = (
            _step_ok.get(step_key, {}).get(fn, True) and ok)

    if do_export:
        print("=" * 54)
        print("  Phase 1: EXPORT TES4 RECORDS")
        print("=" * 54)
        for fn in order:
            ok = phase_export(fn, tes4_data, export_dir, config)
            _mark('export', fn, ok)
            if not ok:
                success = False
        print()

    if do_extract:
        print("=" * 54)
        print("  Phase 2: EXTRACT TES4 ARCHIVES")
        print("=" * 54)
        for fn in order:
            ok = phase_extract(fn, tes4_data, config)
            _mark('extract', fn, ok)
            if not ok:
                success = False
        print()

    if do_meshes:
        print("=" * 54)
        print("  Phase 3: CONVERT MESHES AND TEXTURES")
        print("=" * 54)
        for fn in order:
            ok = phase_assets(fn, config, output_dir=output_dir,
                              mesh_subdirs=getattr(args, 'mesh_subdirs', None),
                              winding_fix=args.collision_winding_fix)
            # A filtered mesh run converts only some subfolders, so it must not
            # certify the Meshes step as fully rebuilt at this version.
            if not getattr(args, 'mesh_subdirs', None):
                _mark('meshes', fn, ok)
            if not ok:
                success = False
        print()

    if do_speedtrees:
        print("=" * 54)
        print("  Phase 4: CONVERT SPEEDTREES")
        print("=" * 54)
        for fn in order:
            ok = phase_speedtrees(fn, config, output_dir=output_dir)
            _mark('speedtrees', fn, ok)
            if not ok:
                success = False
        print()

    if do_creatures:
        print("=" * 54)
        print("  Phase 5: CONVERT CREATURES")
        print("=" * 54)
        for fn in order:
            ok = phase_creatures(fn, tes5_data, config, output_dir=output_dir)
            _mark('creatures', fn, ok)
            if not ok:
                success = False
        print()

    if do_import:
        print("=" * 54)
        print("  Phase 6: BUILD TES5 PLUGIN")
        print("=" * 54)
        for fn in order:
            ok = phase_import(fn, tes4_data, tes5_data, export_dir, config,
                              output_dir=output_dir)
            _mark('import_', fn, ok)
            if not ok:
                success = False
        print()

    if do_sounds:
        print("=" * 54)
        print("  Phase 7: CONVERT SOUNDS")
        print("=" * 54)
        for fn in order:
            ok = phase_sounds(fn, config, output_dir=output_dir)
            _mark('sounds', fn, ok)
            if not ok:
                success = False
        print()

    if do_scripts:
        print("=" * 54)
        print("  Phase 8: CONVERT SCRIPTS")
        print("=" * 54)
        for fn in order:
            ok = phase_scripts(fn, config, output_dir=output_dir)
            if not ok:
                success = False
            # Compilation is gated on `success` upstream; the step counts as
            # run only when transpile AND compile both land.
            compiled = False
            if success:
                compiled = phase_compile(fn, config, output_dir=output_dir)
                if not compiled:
                    success = False
            _mark('scripts', fn, ok and compiled)
        print()

    if do_lod:
        print("=" * 54)
        print("  Phase 9: GENERATE LOD")
        print("=" * 54)
        for fn in order:
            # phase_lod's result is advisory upstream (a worldspace with no LOD
            # is not an error), so the step is recorded as run regardless.
            phase_lod(fn, tes5_data, config, output_dir=output_dir)
            _mark('lod', fn, True)
        print()

    if do_skyrim_patch:
        print("=" * 54)
        print("  Phase 10: PATCH SKYRIM (SLOT 44 BODY MESHES)")
        print("=" * 54)
        ok = phase_modify_body_meshes(
            tes5_data, plugins=getattr(args, 'patch_plugins', None),
            output_dir=output_dir)
        # Patches the user's load order, not a converted plugin, so it is
        # recorded once against every plugin in the run.
        for fn in order:
            _mark('modify_body_meshes', fn, ok)
        if not ok:
            success = False
        print()

    if do_pack_bsa:
        print("=" * 54)
        print("  Phase 11: PACK BSA ARCHIVES")
        print("=" * 54)
        for fn in order:
            ok = phase_pack(fn, config, output_dir=output_dir)
            _mark('pack', fn, ok)
            if not ok:
                success = False
        print()

    if do_pack_zip:
        print("=" * 54)
        print("  Phase 12: PACK ZIP ARCHIVES")
        print("=" * 54)
        for fn in order:
            ok = phase_pack_zip(fn, config, output_dir=output_dir)
            _mark('pack_zip', fn, ok)
            if not ok:
                success = False
        print()

    # Stamp the version onto every step that completed for every plugin it ran
    # for.  This is what lets the next paste-over-the-top install work out that
    # e.g. only Meshes and Import are stale.  Never let bookkeeping fail a run
    # that otherwise succeeded.
    try:
        for step_key, per_file in _step_ok.items():
            for fn, ok in per_file.items():
                if ok:
                    _version.record_step_run(step_key, fn,
                                             data_path=tes4_data)
    except Exception as exc:
        print(f"Note: could not record conversion state ({exc}).")

    print("Pipeline complete." if success else "Pipeline completed with errors.")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
