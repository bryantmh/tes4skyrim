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


# Shared-folder resolution. Plugins imported together from one mod archive
# share ONE asset tree, so a plugin name maps to a folder through these
# resolvers and never by joining a name onto a root by hand -- that is the only
# reason export/ and output/ agree. `output_layout` imports nothing but
# pathlib, so this is safe at module scope despite convert.py being the entry
# point every package imports from.
from output_layout import record_dir, plugin_out_root
import run_log


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


def resolve_plugin_path(file_name: str, tes4_data: str,
                        export_dir: str = None) -> str:
    """Absolute path to a plugin's TES4 binary.

    A plugin imported from a mod archive (see `asset_convert/mod_ingest.py`)
    keeps its binary at `export/<plugin>/_source/<plugin>` and is registered in
    `export/sources.json`; anything else lives in the Oblivion Data directory
    exactly as it always has.

    EVERY place that used to build `os.path.join(tes4_data, name)` must go
    through here -- one missed call site and an imported mod half-works
    (exported but not extracted, or listed but not convertible).
    """
    export_dir = export_dir or str(SCRIPT_DIR / "export")
    try:
        from asset_convert import source_registry
        imported = source_registry.plugin_binary(export_dir, file_name)
        if imported:
            return str(imported)
    except Exception:
        # A broken/absent registry must never stop a normal Data-directory
        # conversion -- that is the whole additive guarantee.
        pass
    return os.path.join(tes4_data or "", file_name)


def _mod_commands(args, export_dir: str, tes4_data: str) -> int:
    """Handle --list-mods / --import-mod / --remove-mod, then exit.

    These manage conversion SOURCES rather than converting anything, so they
    never touch the pipeline.
    """
    from asset_convert import mod_ingest, source_registry

    if args.list_mods:
        groups = source_registry.groups(export_dir)
        if not groups:
            print("No imported mods. Add one with:\n"
                  "  python convert.py --import-mod <archive|folder>")
            return 0
        print(f"Imported mods ({len(groups)}):")
        for _gid, label, plugs in groups:
            # One shared asset tree per mod, so the file count belongs on the
            # mod's line -- printing it per plugin implied three payloads.
            _first = source_registry.get(export_dir, plugs[0]) if plugs else {}
            _total = sum((_first or {}).get("counts", {}).values())
            print(f"  {label}" + (f"  ({_total} files)" if _total else ""))
            for name in plugs:
                entry = source_registry.get(export_dir, name) or {}
                if not entry.get("plugin"):
                    # Asset-only mod: no plugin is the normal state, not a
                    # missing file.
                    kinds = ", ".join(
                        k for k in ("meshes", "textures", "sound", "trees")
                        if (entry.get("capabilities") or {}).get(k))
                    print(f"    - {name}  (no plugin"
                          + (f"; {kinds}" if kinds else "") + ")")
                    continue
                binary = source_registry.plugin_binary(export_dir, name)
                mark = "" if binary else "   [binary MISSING]"
                print(f"    - {name}" + mark)
        return 0

    if args.remove_mod:
        if mod_ingest.remove(args.remove_mod, export_dir):
            print(f"Removed {args.remove_mod}")
            return 0
        print(f"{args.remove_mod!r} is not an imported mod. "
              f"See --list-mods.")
        return 1

    # --import-mod.  One or more sources, applied IN ORDER: later sources
    # overwrite earlier ones in the shared asset tree, exactly as a mod manager
    # would resolve them.  Doing it here rather than at convert time means every
    # later stage keeps seeing a single coherent tree and needs no changes.
    sources = args.import_mod
    if isinstance(sources, str):
        sources = [sources]
    # A source may be an archive, a mod folder, or the NAME of an export tree
    # that already exists -- which is how the base game joins the stack rather
    # than sitting beside it.
    missing_src = [x for x in sources
                   if not os.path.exists(x)
                   and not (Path(export_dir) / x).is_dir()]
    if missing_src:
        for x in missing_src:
            print(f"ERROR: not found: {x}")
        return 1
    if len(sources) > 1 and not args.merge_as:
        print("ERROR: several sources need --as NAME to say which asset tree "
              "they merge into.")
        print("  python convert.py --import-mod A B C --as \"My Overhaul\"")
        return 1

    if len(sources) > 1:
        return _import_ordered(sources, args, export_dir, tes4_data,
                               mod_ingest)

    src = sources[0]
    try:
        manifest = mod_ingest.inspect(src)
    except mod_ingest.IngestError as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"Archive : {manifest.path.name}")
    print(f"Layout  : "
          + (f"Data folder '{manifest.payload_root}'" if manifest.payload_root
             else "archive root"))
    print(f"Contents: {manifest.summary()}")
    if manifest.ambiguous_data:
        print("WARNING: several equally-shallow Data folders "
              f"({', '.join(manifest.ambiguous_data)}); using the first.")
    if manifest.bsas:
        print(f"BSAs    : {len(manifest.bsas)}")
    if manifest.nested:
        print(f"Nested  : {len(manifest.nested)} archive(s)")
    print(f"Plugins : {', '.join(manifest.plugins)}")

    try:
        results = mod_ingest.ingest(
            src, export_dir,
            plugin_members=args.plugin_member,
            keep_archive=not args.no_keep_archive,
            manifest=manifest)
    except mod_ingest.IngestError as exc:
        print(f"ERROR: {exc}")
        return 1

    # Masters must already be converted or the import silently produces a
    # broken plugin -- warn loudly rather than letting it fail deep in import.
    missing = _missing_master_exports(results, export_dir, tes4_data)
    if missing:
        print()
        print("WARNING: these masters have no export yet:")
        for master, users in sorted(missing.items()):
            print(f"  {master}  (needed by {', '.join(sorted(users))})")
        print("Convert them FIRST, or the import will resolve their records "
              "to nothing:")
        print(f"  python convert.py -f {sorted(missing)[0]}")

    first = sorted(results)[0]
    quoted = f'"{first}"' if ' ' in first else first
    caps = (results[first] or {}).get('capabilities') or {}
    # The ASSET tree, not the plugin name.  `results` is keyed by plugin,
    # but a mod's assets land in its GROUP folder (named for the mod's
    # label), and those differ whenever the archive is not named for the
    # esp inside -- the normal case.  Writing `.base_plugins` under the
    # plugin name put it in a directory holding no meshes, so the texture
    # fallback never saw it and --base silently did nothing for any mod
    # that ships a plugin.  asset_root_name reads the registry entry the
    # ingest above just wrote, so it is right on the cached path too.
    from asset_convert import source_registry as _sr
    _asset_tree = _sr.asset_root_name(export_dir, first)
    _write_base_plugins(export_dir, _asset_tree, args.base)
    print()
    if caps.get('plugin', True):
        print("Imported. Convert it with:")
        print(f"  python convert.py -f {quoted}")
    else:
        # No plugin means no export/import/scripts -- naming those steps here
        # would send the user straight into a no-op run.
        steps = sorted(mod_ingest.available_steps(caps)
                       & {'meshes', 'speedtrees', 'sounds'})
        flags = ' '.join(f'--{s.replace("_", "-")}-only' for s in steps)
        print("Imported (asset-only mod -- no plugin to export or import).")
        print("Convert its assets with:")
        print(f"  python convert.py -f {quoted} {flags}".rstrip())
    return 0


def _is_asset_only(file_name: str, export_dir: str) -> bool:
    """True for an imported mod that ships assets but NO plugin.

    Texture/mesh replacers and resource packs are legitimate mods with nothing
    to export or import (e.g. "Tamriel Landscape Pack" is one BSA of 2,018
    meshes/textures/trees). Their asset phases run normally; the record phases
    have no binary to read and are skipped rather than failed.
    """
    try:
        from asset_convert import source_registry
        entry = source_registry.get(export_dir, file_name)
    except Exception:
        return False
    if not entry:
        return False
    caps = entry.get('capabilities')
    if isinstance(caps, dict) and 'plugin' in caps:
        return not caps['plugin']
    return not entry.get('plugin')


def _import_ordered(sources, args, export_dir, tes4_data, mod_ingest):
    """Import several sources IN ORDER into one shared asset tree.

    This is the conflict resolution a mod manager does, moved to import time.
    It has to happen here because the conversion's decisions are cross-mod: a
    shape's specular comes from whichever normal map WINS, its parallax from
    whichever diffuse wins.  Convert each mod on its own and it decides against
    textures the player will never see -- measured on the author's own stack,
    a single forgotten folder left 5139 shapes without a height map.

    Plugins are NOT pooled: each still registers under its own name, so the
    same list yields both orderings by projection -- assets from the entries
    that ship assets, plugins from the entries that ship plugins.
    """
    target = args.merge_as
    index = mod_ingest.new_index()
    tgt_dir = Path(export_dir) / target

    if args.fresh and tgt_dir.is_dir():
        n = sum(1 for p in tgt_dir.rglob('*') if p.is_file())
        print(f"--fresh: clearing the previous '{target}' assets ({n} files)")
        for cat in mod_ingest.ASSET_DIRS:
            d = tgt_dir / cat
            if d.is_dir():
                shutil.rmtree(d)
    elif tgt_dir.is_dir():
        print(f"NOTE: '{target}' already exists and sources are layered ON "
              f"TOP of it.\n      Files from an earlier import survive; use "
              f"--fresh to start clean.\n")

    print(f"Merging {len(sources)} source(s) into '{target}', in order:\n")

    results = {}
    for n, src in enumerate(sources, 1):
        print(f"[{n}/{len(sources)}] {src}")
        if not os.path.exists(src) and (Path(export_dir) / src).is_dir():
            # An export tree that already exists: this is the base game
            # joining the stack instead of sitting beside it, so its own
            # meshes are converted against the retextures that will win.
            try:
                mod_ingest.seed_from_export(export_dir, src, target,
                                            index=index)
            except mod_ingest.IngestError as exc:
                print(f"  ERROR: {exc}")
                return 1
            print()
            continue
        try:
            man = mod_ingest.inspect(src)
        except mod_ingest.IngestError as exc:
            print(f"  ERROR: {exc}")
            return 1
        print(f"  {man.summary()}"
              + (f"; plugins: {', '.join(man.plugins)}" if man.plugins
                 else "; no plugin"))
        try:
            # force=True: a merge is an explicit request, and the idempotence
            # cache is keyed per source, so it cannot see that the SHARED tree
            # still needs this source re-applied on top of the others.
            res = mod_ingest.ingest(
                src, export_dir,
                plugin_members=args.plugin_member,
                keep_archive=not args.no_keep_archive,
                manifest=man, force=True,
                asset_target=target, index=index)
        except mod_ingest.IngestError as exc:
            print(f"  ERROR: {exc}")
            return 1
        results.update(res)
        print()

    print("=" * 60)
    print(f"Asset index for '{target}'")
    print("=" * 60)
    total = len(index['files'])
    for label, placed in index['per_source'].items():
        won = sum(1 for v in index['files'].values() if v == label)
        share = won * 100.0 / total if total else 0.0
        note = '  <- contributed nothing that survived' if not won else ''
        print(f"  {label:<44} {placed:>6} placed, {won:>6} winning "
              f"({share:5.1f}%){note}")
    print(f"  {'TOTAL':<44} {total:>6} files")

    if index['overwrites']:
        print(f"\n{len(index['overwrites'])} file(s) overwritten by a later "
              f"source:")
        for rel, prev, now in index['overwrites'][:15]:
            print(f"  {rel}\n      {prev}  ->  {now}")
        if len(index['overwrites']) > 15:
            print(f"  ... ({len(index['overwrites']) - 15} more)")

    missing = _missing_master_exports(results, export_dir, tes4_data)
    if missing:
        print("\nWARNING: these masters have no export yet:")
        for master, users in sorted(missing.items()):
            print(f"  {master}  (needed by {', '.join(sorted(users))})")

    quoted = f'"{target}"' if ' ' in target else target
    _write_base_plugins(export_dir, target, args.base)
    print(f"\nMerged. Convert it with:\n  python convert.py -f {quoted}")
    return 0


def _write_base_plugins(export_dir, name, bases):
    """Record which plugins a mod builds on, for the texture fallback.

    An asset-only mod declares no master -- it has no plugin and so no
    `_HEADER.txt` -- but its meshes still reference the base game's textures.
    Without this the converter cannot resolve them: measured on the author's
    parallax mod, 1602 of 3357 referenced texture paths existed ONLY in the
    base export.  See nif_converter.master_texture_roots.
    """
    if not bases:
        return
    from asset_convert.nif_converter import BASE_PLUGINS_FILE
    d = Path(export_dir) / name / '_source'
    d.mkdir(parents=True, exist_ok=True)
    (d / BASE_PLUGINS_FILE).write_text('\n'.join(bases) + '\n',
                                       encoding='utf-8')
    print(f"  Base: {', '.join(bases)} (textures resolve through these)")


def _missing_master_exports(results, export_dir: str, tes4_data: str) -> dict:
    """{master_name: {plugins needing it}} for masters lacking an export dir.

    A plugin's masters resolve through `record_dir` (see
    tes5_import/overrides.py:load_master_export), so a missing one is the
    project's classic silent-failure mode.

    Resolved through the registry rather than by joining the name onto
    `export/`: an imported mod's plugins live inside their mod's shared folder,
    so a plugin mastering a resource pack's ESM reported every one of its
    masters as missing while they sat converted one level down.
    """
    from asset_convert import source_registry

    missing = {}
    for name in results:
        binary = source_registry.plugin_binary(export_dir, name)
        if not binary:
            continue
        for master in get_masters_from_binary(str(binary)):
            if os.path.isdir(record_dir(export_dir, master)):
                continue
            missing.setdefault(master, set()).add(name)
    return missing


def get_masters_from_binary(filepath: str) -> list:
    """Read the master list from a TES4/FO3/FNV binary file header.

    FO3/FNV carry 4 more header bytes than TES4; HEDR marks the boundary.
    """
    import struct as st
    masters = []
    with open(filepath, 'rb') as f:
        sig = f.read(4)
        if sig != b'TES4':
            return masters
        data_size = st.unpack('<I', f.read(4))[0]
        f.seek(20 if f.read(16)[12:16] == b'HEDR' else 24)
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
        source = resolve_plugin_path(name, tes4_data)
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

    out_dir = str(record_dir(export_dir, file_name))

    # Find the source file -- the Oblivion Data directory, or an imported mod's
    # retained binary under export/<plugin>/_source/.
    source = resolve_plugin_path(file_name, tes4_data, export_dir)
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
    """Get a plugin's assets into export/<name>/.

    Two sources, one output shape:
      * a plugin imported from a mod archive re-runs its ingest (which already
        produced the same tree the BSA extractor would have);
      * everything else extracts the BSAs beside it in the Oblivion Data dir,
        exactly as before.
    """
    extract_dir = str(SCRIPT_DIR / "export")

    from asset_convert import source_registry
    if source_registry.get(extract_dir, file_name):
        from asset_convert import mod_ingest
        print(f"[{file_name}] Re-importing mod archive...")
        try:
            mod_ingest.reingest(file_name, extract_dir)
        except mod_ingest.IngestError as exc:
            print(f"[{file_name}] ERROR: {exc}")
            return False
        return True

    from asset_convert.asset_pipeline import extract_bsas

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
                 mesh_subdirs=None, winding_fix=None, parallax=False,
                 textures_only=False):
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
    # The authored-normal repair always runs; only the inferred steps are
    # switchable, so say which one this line is about.
    print(f"[{file_name}] Inferred collision winding steps: "
          f"{'ON' if winding_fix else 'OFF'} ({origin}); "
          f"authored-normal repair: always on")

    print(f"[{file_name}] Converting meshes (NIFs + textures)...")
    stats = convert_meshes(
        source_file=file_name,
        extract_dir=extract_dir,
        output_dir=out_dir,
        mesh_subdirs=mesh_subdirs,
        parallax=parallax,
        textures_only=textures_only,
    )
    total = sum(v for v in stats.values() if isinstance(v, int))
    print(f"[{file_name}] Meshes complete ({total} items processed)")

    # Book inventory-art: bake each distinct BOOK model's textures onto the
    # vanilla Skyrim reading rigs (see asset_convert/book_inam.py); the import
    # phase points each BOOK's INAM at meshes\tes4\clutter\books\inv\<base>.nif
    if textures_only:
        print(f"[{file_name}] Textures only: no meshes, no book art "
              f"(PGPatcher patches the meshes in the load order)")
        return True

    from asset_convert.book_inam import generate_book_inams

    _, tes5_data = get_paths(config)
    print(f"[{file_name}] Generating book inventory-art meshes...")
    # A plugin places its MASTERS' book models too, and those meshes/textures
    # were extracted into the master's export dir only.
    # base_plugins, not terrain_lod's _master_names: the latter reads only
    # _HEADER.txt, which an asset-only merge does not have, so its books
    # would find no BOOK records and ship no inventory art at all.
    from asset_convert import base_plugins as _bp
    bstats = generate_book_inams(
        source_file=file_name,
        extract_dir=extract_dir,
        output_dir=out_dir,
        skyrim_data=tes5_data or None,
        master_names=_bp.names_for(record_dir(extract_dir, file_name)),
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

    # Branches come from Oblivion's own SpeedTree code by DEFAULT.  It needs a
    # configured Oblivion.exe plus the committed native/dist harness; when
    # either is missing, or a dump fails, conversion falls back PER TREE to the
    # pure-Python generator, which needs no executable.  Set
    # "speedtreeEngineBranches": false (or pass --no-engine-branches) to force
    # the Python generator everywhere.
    use_engine = bool(config.get("speedtreeEngineBranches", True))
    if not use_engine:
        print(f"[{file_name}]   engine branches DISABLED -- using the Python "
              f"generator for every tree")
    print(f"[{file_name}] Converting SpeedTrees (SPTs)...")
    stats = convert_speedtrees(
        source_file=file_name,
        extract_dir=extract_dir,
        output_dir=out_dir,
        use_engine=use_engine,
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

    export_root = str(SCRIPT_DIR / "export")
    export_subdir = str(record_dir(export_root, file_name))
    if not os.path.isdir(export_subdir):
        print(f"[{file_name}] No export directory, skipping creatures")
        return False
    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    out_meshes = str(plugin_out_root(out_root, file_name, export_root)
                     / "meshes")

    # The animation singlefiles are ONE shared file in Data. A child plugin
    # registers its creatures in its MASTER's copy rather than shipping a
    # rival copy of its own (see _shared_singlefile_dir).
    from asset_convert.terrain_lod import _master_names
    # A master's OUTPUT folder is its mod's folder for an imported mod, so
    # resolve it the same way the export side does.
    master_dirs = [plugin_out_root(out_root, m, export_root)
                   for m in _master_names(Path(export_subdir))
                   if plugin_out_root(out_root, m, export_root).is_dir()]

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
    from tes5_import.artifact_schema import StaleArtifactError

    export_subdir = str(record_dir(export_dir, file_name))
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
        from tools.navmesh.navmesh_cache import auto_install, NO_DOWNLOAD_ENV_VAR
        auto_install(file_name,
                     allow_download=os.environ.get(
                         NO_DOWNLOAD_ENV_VAR, '').strip().lower()
                     not in ('1', 'true'))
    except Exception as exc:
        # Never fatal -- but never silent either.  A bare `pass` here meant an
        # import error or a broken tools/ path made the cache vanish with no
        # trace, which is exactly what "the download does not work" looked like
        # from the user's side.
        print(f"  Navmesh cache: unavailable ({exc}); generating normally.")

    out_root = output_dir or str(SCRIPT_DIR / "output")
    os.makedirs(out_root, exist_ok=True)
    # Every plugin gets its own output folder (output/<plugin>/<plugin>), which
    # is also where the asset/mesh pipeline writes. Create it unconditionally:
    # relying on the folder already existing left plugins with no asset phase
    # (e.g. Translation.esp) written as a bare file in output/, with their
    # voicemap/liptext companions loose in the output root.
    # An imported mod's plugins all land in their mod's folder, so this must
    # agree with where the asset phases write -- otherwise the ESM and its
    # meshes end up in two different mods.
    plugin_dir = str(plugin_out_root(out_root, file_name, export_dir))
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
    except (MissingMasterOutputError, StaleArtifactError) as e:
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

    # Music rides the sound phase: same encoders (ffmpeg + xWMAEncode), so a
    # single --sounds-only rebuilds both.  It writes music_tracks.json, which
    # the importer reads to build MUST/MUSC, so it must run before --import-only
    # for the records to name real files.
    from asset_convert.music_convert import convert_music
    print(f"[{file_name}] Converting music to xWMA...")
    mstats = convert_music(
        source_file=file_name,
        extract_dir=extract_dir,
        output_dir=out_dir,
    )
    print(f"[{file_name}] Music complete "
          f"({mstats.get('converted', 0)} converted, "
          f"{mstats.get('cached', 0)} cached, "
          f"{mstats.get('failed', 0)} failed, "
          f"{mstats.get('tracks', 0)} tracks)")
    return True


# ===========================================================================
# Phase 8: CONVERT SCRIPTS
# ===========================================================================

def phase_scripts(file_name: str, config: dict, output_dir: str = None):
    """Convert TES4 scripts to Papyrus .psc source files."""
    from script_convert.pipeline import convert_all_scripts
    from tes5_import.artifact_schema import StaleArtifactError

    export_root = str(SCRIPT_DIR / "export")
    export_subdir = str(record_dir(export_root, file_name))
    if not os.path.isdir(export_subdir):
        print(f"[{file_name}] No export directory, skipping scripts")
        return False

    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    script_dir = (plugin_out_root(out_root, file_name, export_root)
                  / "scripts" / "source")

    print(f"[{file_name}] Converting scripts to Papyrus...")
    try:
        stats = convert_all_scripts(export_subdir, str(script_dir))
    except StaleArtifactError as e:
        # Scripts read music_tracks.json to bind StreamMusic properties; a
        # stale one is actionable, so print the instruction rather than a
        # traceback (same contract as phase_import).
        print(f"[{file_name}] ERROR: {e}")
        return False
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
    _pout = plugin_out_root(out_root, file_name, str(SCRIPT_DIR / "export"))
    script_src = _pout / "scripts" / "source"
    script_out = _pout / "scripts"

    # Nothing to compile is SUCCESS, not failure.  A plugin can legitimately
    # convert zero scripts -- the merged-BSA DLC plugins export zero records
    # because their content lives in the master's export, so every phase finds
    # an empty workload.  Every other phase already reports that as success
    # ("No meshes found", "No sound directory found"); returning False here
    # flipped the whole run to "Pipeline completed with errors" and showed the
    # user a FAILED banner for a run in which nothing had gone wrong.  Only a
    # missing compiler, missing headers, or a real compile error fails below.
    if not script_src.is_dir() or not any(script_src.glob("*.psc")):
        print(f"[{file_name}] No .psc scripts found, skipping compile")
        return True

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

    # The CK supplies vanilla declarations, but converted OBSE calls may target
    # SKSE64 natives. Augmented base headers are compile-only and never ship.
    from script_convert.skse_headers import prepare_skse_headers
    skse_headers = prepare_skse_headers(
        skyrim_headers, script_out / "_skse_headers")
    static_headers = SCRIPT_DIR / "script_convert" / "static_scripts"

    # An override plugin's scripts declare properties typed as the MASTER's
    # converted scripts (`TES4_NQ16Script Property ...`), because the record
    # they name carries that master's SCRI.  Those .psc live in the master's
    # own output, so without them on the header path the compiler reports
    # "undefined type" — 198 of Translation.esp's scripts. They are headers
    # only: the master's own run compiles and ships the .pex.
    from script_convert.cross_ref import master_names
    master_src_dirs = []
    _exp = str(SCRIPT_DIR / "export")
    for m in master_names(record_dir(_exp, file_name)):
        d = plugin_out_root(out_root, m, _exp) / "scripts" / "source"
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
        # papyrus.exe searches repeated -h arguments in reverse order.
        h += ["-h", str(static_headers), "-h", str(skse_headers)]
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
        c += ["-h", str(static_headers), "-h", str(skse_headers)]
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
    shutil.rmtree(skse_headers, ignore_errors=True)
    return ok_count > 0


# The CK ships the vanilla sources in one of two loose layouts, or not at all
# (only Data/Scripts.zip).  Checked in this order; Data/Source/Scripts is both
# the modern layout and where a zip extraction lands.
_HEADER_DIRS = (("Source", "Scripts"), ("Scripts", "Source"))


def _is_header_dir(d: Path) -> bool:
    """A directory holding the vanilla headers, identified by Debug.psc."""
    return d.is_dir() and (d / "Debug.psc").is_file()


def _extract_scripts_zip(zip_path: Path, data_dir: Path) -> str:
    """Unpack the Papyrus sources out of Data/Scripts.zip, in place.

    Newer Creation Kit builds ship the vanilla sources ONLY as
    Data/Scripts.zip and never unpack them, so an install with a perfectly good
    CK still has no Data/Source/Scripts for the compiler's ``-h`` path.

    The archive's own entries are already rooted at ``Source/Scripts/``, so
    extracting relative to Data puts every header exactly where the CK itself
    would have put it -- which is where this project, the CK, and every other
    Papyrus tool on the machine already look.  Only the compiler's inputs are
    taken (``.psc`` plus ``TESV_Papyrus_Flags.flg``); the archive also holds
    DialogueViews XML we have no use for.  Existing files are never
    overwritten, so a user's own edited header survives.

    Returns the header directory on success, "" on failure.
    """
    import zipfile
    dest = data_dir / "Source" / "Scripts"
    try:
        with zipfile.ZipFile(zip_path) as z:
            members = [n for n in z.namelist()
                       if n.lower().endswith((".psc", ".flg"))]
            if not members:
                return ""
            dest.mkdir(parents=True, exist_ok=True)
            for name in members:
                # Flatten to a basename under dest: the compiler wants a flat
                # header dir, and this also makes the extraction immune to a
                # zip rooted differently, and to any "../" traversal entry.
                base = os.path.basename(name.replace("\\", "/"))
                if not base or base.startswith("."):
                    continue
                out = dest / base
                if out.exists():
                    continue
                with z.open(name) as src, open(out, "wb") as fh:
                    shutil.copyfileobj(src, fh)
    except (OSError, zipfile.BadZipFile) as e:
        # A read-only or UAC-protected install (Program Files) is the likely
        # cause; say so rather than failing the phase with a bare "not found".
        print(f"  WARNING: could not unpack {zip_path}: {e}")
        return ""
    if not _is_header_dir(dest):
        return ""
    return str(dest)


def _find_skyrim_source_scripts(config: dict = None) -> str:
    """Find Skyrim Papyrus source scripts directory (contains Debug.psc etc.).

    Order: the loose CK layouts, then unpacking Data/Scripts.zip in place.
    Every caller (the compile phase, preflight, the compile-check tools) goes
    through here, so the dependency check and the phase can never disagree
    about whether the headers are available.
    """
    sse_data = find_game_path("skyrimse", config)
    if not sse_data:
        return ""
    data = Path(sse_data)
    for parts in _HEADER_DIRS:
        source_dir = data.joinpath(*parts)
        if _is_header_dir(source_dir):
            return str(source_dir)

    zip_path = data / "Scripts.zip"
    if zip_path.is_file():
        print(f"  Papyrus headers not unpacked; extracting {zip_path}...")
        found = _extract_scripts_zip(zip_path, data)
        if found:
            n = len(list(Path(found).glob("*.psc")))
            print(f"  Extracted {n} vanilla .psc headers to {found}")
            return found
    return ""


# ===========================================================================
# Phase 10: PATCH SKYRIM (SLOT 44 BODY MESHES)
# ===========================================================================

def phase_modify_body_meshes(tes5_data: str = None, plugins: list = None,
                             output_dir: str = None):
    """Add greaves partition to vanilla Skyrim character body NIFs, then
    generate ONE merged companion slot-44 patch covering `plugins`.

    The patch (tools/creature/patch_body_slots.py) is mandatory alongside the split
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
              "generated (run tools/creature/patch_body_slots.py manually)")
        return True

    plugins = plugins or ["Skyrim.esm"]
    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    # Into "Finished Mods", NOT a per-plugin folder. This step takes no `-f` and
    # patches the vanilla Skyrim body records for the whole load order, so it
    # belongs to no single conversion. Hardcoding "Oblivion.esm" put it
    # somewhere `--pack-only -f <other plugin>` never looks: converting Nehrim
    # created an otherwise-empty output/Oblivion.esm/ holding just this file,
    # and it shipped with nothing. It is installed loose rather than zipped —
    # one plugin with no assets is not worth an archive — so it sits beside the
    # zips as a finished artefact in its own right.
    from output_layout import finished_dir
    out_path = finished_dir(out_root) / "Slot44 Patch.esp"

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

    patch_script = SCRIPT_DIR / "tools" / "creature" / "patch_body_slots.py"
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
    export_root = str(SCRIPT_DIR / "export")
    export_dir = record_dir(export_root, file_name)

    print(f"[{file_name}] Packing BSAs...")
    results = pack_bsas(
        source_file=file_name,
        output_dir=out_dir,
        bsarch_path=bsarch,
        # Two different roots, and they must not be confused: the RECORD dir
        # drives the texture keep-set, the export ROOT resolves which output
        # folder this plugin converted into.
        export_dir=str(export_dir) if export_dir.is_dir() else None,
        export_root=export_root,
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

    The zip lands in output_dir/"Finished Mods"/ — with every other installable
    artefact — and is named "<file_name>.zip".
    """
    import zipfile
    from output_layout import finished_dir

    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    src_root = plugin_out_root(out_root, file_name,
                               str(SCRIPT_DIR / "export"))
    if not src_root.is_dir():
        print(f"[{file_name}] Source not found: {src_root}, skipping zip pack")
        return False

    # ONE mod in, ONE mod out: the folder holds every plugin of an imported
    # mod, so the zip is named for the MOD. Naming it after whichever plugin
    # happened to be the -f argument produced three identical archives under
    # three different names for a three-plugin pack.
    zip_path = finished_dir(out_root) / f"{src_root.name}.zip"

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

def _run_pipeline():
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
    parser.add_argument("--no-engine-branches", action="store_true",
                        help="Force the pure-Python SpeedTree generator. "
                             "Engine branches (from the game's own code) are "
                             "the DEFAULT and already fall back to Python per "
                             "tree when no Oblivion.exe is configured or the "
                             "native harness is missing.")
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
    # ── Mod archive import ───────────────────────────────────────────────────
    # Importing is not a pipeline phase: it registers a NEW conversion source
    # and exits, after which `-f <plugin>` converts it like any other plugin.
    parser.add_argument("--import-mod",          metavar="ARCHIVE",
                        nargs="+",
                        help="Import a mod archive (.zip/.7z/.rar) or an "
                             "already-extracted mod folder as a conversion "
                             "source, then exit")
    # Several sources import IN ORDER into one asset tree, later ones
    # overwriting earlier -- the precedence a mod manager applies, resolved
    # once at import time so the converter sees one coherent stack. Without
    # it each mod converts blind to the others: a mesh-fix mod cannot see
    # the retexture that will win beside it, and decides specular and
    # parallax against the wrong textures.
    # An asset-only mod declares no master -- it has no plugin and so no
    # _HEADER.txt -- but its meshes still reference the base game's
    # textures. Recording the base here is what lets the converter resolve
    # them; see nif_converter.master_texture_roots.
    parser.add_argument("--fresh",                action="store_true",
                        help="With several --import-mod sources: clear the "
                             "target asset tree first. A merge is defined "
                             "by its FULL source list, so re-importing a "
                             "different list without this leaves the "
                             "dropped mod's files behind and the index "
                             "reports something that is no longer true.")
    parser.add_argument("--base",                nargs="+", metavar="PLUGIN",
                        help="With --import-mod: the plugin(s) this mod "
                             "builds on (e.g. Nehrim.esm), so its meshes "
                             "can resolve textures it does not ship.")
    parser.add_argument("--as",                  dest="merge_as",
                        metavar="NAME",
                        help="With several --import-mod sources: the name "
                             "of the merged asset tree. Required for a "
                             "multi-source import.")
    parser.add_argument("--plugin-member",       nargs="+", metavar="PATH",
                        help="With --import-mod: which plugin(s) inside the "
                             "archive to register (default: all found)")
    parser.add_argument("--no-keep-archive",     action="store_true",
                        help="With --import-mod: do not retain a copy of the "
                             "archive (re-importing then needs the original)")
    parser.add_argument("--list-mods",           action="store_true",
                        help="List imported mod archives and exit")
    parser.add_argument("--remove-mod",          metavar="PLUGIN",
                        help="Remove an imported mod (deletes its export "
                             "folder and registry entry), then exit")
    parser.add_argument("--mesh-subdirs",        nargs="+", metavar="SUBDIR",
                        help="Limit mesh conversion to relative folder prefixes "
                             "(e.g. architecture morro/d). Default: all.")
    parser.add_argument("--patch-plugins",       nargs="+", metavar="PLUGIN",
                        help="Skyrim plugin filenames to generate a slot-44 "
                             "patch for (e.g. Skyrim.esm Dawnguard.esm). "
                             "Default: Skyrim.esm only.")
    # The INFERRED collision winding steps (asset_convert/collision.py steps
    # 1-3). The authored-normal repair (step 0) is always on and this flag does
    # not touch it. Tri-state: the flag forces the inferred steps on, --no-
    # forces them off, and unspecified (None) defers to the per-plugin default
    # in collision_options, resolved separately for each file.
    winding = parser.add_mutually_exclusive_group()
    winding.add_argument("--collision-winding-fix", dest="collision_winding_fix",
                         action="store_true", default=None,
                         help="Also INFER collision winding from adjacency, "
                              "enclosed volume and the render mesh, on top of "
                              "the always-on authored-normal repair. Guesses, "
                              "so it can invert correct geometry -- only for "
                              "plugins whose exporter destroyed the normals. "
                              "Default: on only for "
                              + ", ".join(sorted(WINDING_FIX_DEFAULT_PLUGINS)))
    winding.add_argument("--no-collision-winding-fix", dest="collision_winding_fix",
                         action="store_false", default=None,
                         help="Disable the inferred winding steps (the "
                              "authored-normal repair still runs).")
    # Parallax (asset_convert/parallax.py). Deliberately opt-in and NOT a
    # per-plugin default: a correct parallax shape renders wrong under vanilla
    # SSE, and the converter cannot tell what the player will run it under.
    parser.add_argument("--parallax", action="store_true",
                        help="Carry Oblivion's parallax across as Skyrim "
                             "height maps. REQUIRES Community Shaders or ENB "
                             "in the player's setup -- under vanilla SSE the "
                             "affected surfaces render wrong. Off by default.")
    # Meant to pair with --parallax: PGPatcher (ParallaxGen) patches meshes
    # across the player's whole load order and can also upgrade them to ENB's
    # complex-material system, which we cannot see from here. Then the only
    # thing left for us is recovering the height field out of Oblivion's
    # diffuse alpha -- so analyse every mesh, ship none of them.
    parser.add_argument("--textures-only", action="store_true",
                        help="Mesh stage: read and analyse every NIF but write "
                             "none. Ships textures only (with their _p height "
                             "maps), for use with PGPatcher. Pair with "
                             "--parallax.")

    args = parser.parse_args()

    config       = load_config(args.config)
    # CLI flag overrides the config key; without it the config value stands.
    if args.no_engine_branches:
        config["speedtreeEngineBranches"] = False
    tes4_data, tes5_data = get_paths(config)
    output_dir   = args.output_dir or config.get("outputDir") or str(SCRIPT_DIR / "output")
    export_dir   = str(SCRIPT_DIR / "export")

    os.makedirs(export_dir, exist_ok=True)
    os.makedirs(os.path.join(export_dir, "mappings"), exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # ── Mod-archive management ───────────────────────────────────────────────
    # These register/inspect conversion SOURCES and exit; they convert nothing,
    # so they run before any pipeline setup.
    if args.list_mods or args.import_mod or args.remove_mod:
        return _mod_commands(args, export_dir, tes4_data)

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
    if not order and not args.modify_body_meshes:
        # "10. Patch Skyrim" is the one step that converts no plugin: it patches
        # the user's SKYRIM load order and writes a single shared
        # `Slot44 Patch.esp`, so the GUI runs it with no `-f` at all.  Bailing
        # here left it silently not running -- and therefore never recorded --
        # for anyone whose config lacks the legacy "files" list, which is every
        # end user (nothing writes that key any more; this repo only still has
        # one by hand).  The GUI then re-ticked the box on every check.
        print("No files to process.")
        return 0
    print(f"  Files: {', '.join(order) if order else '(none needed)'}")
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

    # An asset-only mod (a texture/mesh replacer imported from an archive) has
    # no plugin binary, so the record phases have nothing to read. Drop those
    # files from the plugin-only phases rather than letting each one fail on a
    # missing source; their asset phases still run normally.
    _asset_only = {fn for fn in order if _is_asset_only(fn, export_dir)}
    order_with_plugin = [fn for fn in order if fn not in _asset_only]
    if _asset_only:
        print(f"  Asset-only (no plugin): {', '.join(sorted(_asset_only))}")
        print("    -> skipping Export/Import/Scripts/Creatures for these")
        print()

    if do_export and order_with_plugin:
        print("=" * 54)
        print("  Phase 1: EXPORT TES4 RECORDS")
        print("=" * 54)
        for fn in order_with_plugin:
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
                              winding_fix=args.collision_winding_fix,
                              parallax=args.parallax,
                              textures_only=args.textures_only)
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

    if do_creatures and order_with_plugin:
        print("=" * 54)
        print("  Phase 5: CONVERT CREATURES")
        print("=" * 54)
        for fn in order_with_plugin:
            ok = phase_creatures(fn, tes5_data, config, output_dir=output_dir)
            _mark('creatures', fn, ok)
            if not ok:
                success = False
        print()

    if do_import and order_with_plugin:
        print("=" * 54)
        print("  Phase 6: BUILD TES5 PLUGIN")
        print("=" * 54)
        for fn in order_with_plugin:
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

    if do_scripts and order_with_plugin:
        print("=" * 54)
        print("  Phase 8: CONVERT SCRIPTS")
        print("=" * 54)
        for fn in order_with_plugin:
            ok = phase_scripts(fn, config, output_dir=output_dir)
            # Compile only when THIS plugin transpiled cleanly.  This used to
            # gate on the global `success`, so one earlier plugin's failure
            # silently skipped compilation for every plugin after it -- and
            # then marked their `scripts` step not-run, though it had never
            # been attempted.  The step counts as run only when transpile AND
            # compile both land for this plugin.
            compiled = False
            if ok:
                compiled = phase_compile(fn, config, output_dir=output_dir)
            if not (ok and compiled):
                success = False
            _mark('scripts', fn, ok and compiled)
        print()

    if do_lod:
        print("=" * 54)
        print("  GENERATE LOD")
        print("=" * 54)
        # Delegated to tools/release/create_lod.py, NOT looped per plugin.
        #
        # LOD tiles are files on a fixed grid keyed only by worldspace and
        # coordinate, so every plugin editing a worldspace writes the same
        # paths. Baking once per plugin into output/<plugin>/ produced rival
        # copies of each shared tile whose winner the mod manager picked by
        # install order. The bake now happens ONCE for the whole load order,
        # into the standalone AutoConvertLOD mod. `-f` therefore does not
        # narrow it to one plugin: there is one shared artefact, and building
        # it from a single plugin would be building it wrong.
        _cmd = [sys.executable, "-u",
                str(SCRIPT_DIR / "tools" / "release" / "create_lod.py")]
        if output_dir:
            _cmd += ["--output-dir", str(output_dir)]
        ok = subprocess.call(_cmd) == 0
        if not ok:
            success = False
        # Recorded once, under the shared key: one artefact covers every
        # plugin, so stamping it per plugin would mark the step outstanding
        # for whichever plugins this run did not name.
        _mark('create_lod', _version.GLOBAL_PLUGIN_KEY, ok)
        print()

    if do_skyrim_patch:
        print("=" * 54)
        print("  Phase 10: PATCH SKYRIM (SLOT 44 BODY MESHES)")
        print("=" * 54)
        ok = phase_modify_body_meshes(
            tes5_data, plugins=getattr(args, 'patch_plugins', None),
            output_dir=output_dir)
        # Patches the user's load order, not a converted plugin, so it is
        # recorded ONCE under the shared key rather than stamped onto whichever
        # plugins this run happened to include.  Recording it per-plugin left
        # every other plugin looking like it had never run the step, so the GUI
        # re-ticked "10. Patch Skyrim" forever even though the one shared
        # `Slot44 Patch.esp` already existed.
        _mark('modify_body_meshes', _version.GLOBAL_PLUGIN_KEY, ok)
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

    if success:
        print("Pipeline complete.")
        return 0

    # A failed run ends with thousands of lines of stage output above it, so
    # restate WHICH steps failed right next to the verdict.  `_step_ok` is the
    # authoritative record -- every phase stamps it -- so this reports what
    # actually failed rather than scraping the log for the word "error".
    failed = [(step_key, fn)
              for step_key, per_file in _step_ok.items()
              for fn, ok in per_file.items() if not ok]
    print()
    print("-" * 54)
    if failed:
        print(f"  ERROR SUMMARY ({len(failed)} failed step"
              f"{'' if len(failed) == 1 else 's'}):")
        for step_key, fn in failed:
            where = ("all plugins" if fn == _version.GLOBAL_PLUGIN_KEY
                     else fn)
            print(f"    - {step_key}: FAILED for {where}")
    else:
        # A step that returned False without being stamped, or a failure
        # raised outside the per-step marks.  Say so rather than printing an
        # empty summary that reads like nothing went wrong.
        print("  ERROR SUMMARY: a stage reported failure; see the stage "
              "output above for details.")
    print("-" * 54)
    print("Pipeline completed with errors.")
    return 1


def main():
    """Own the run log for a standalone CLI run, then run the pipeline.

    Only a run's OWNER rotates.  When the GUI launched us it has already
    opened the log for the whole run (several convert.py invocations, one per
    step) and set TESCONV_RUN_LOG, so `start_cli_run` returns None here and we
    neither rotate nor write -- otherwise a 7-step run would rotate 7 times and
    the retained logs would be the last 3 STEPS of one run.
    """
    try:
        config = load_config(_config_path_from_argv())
    except Exception:
        config = {}
    header = {
        "Version": _version_string(),
        "Command": " ".join(["convert.py"] + sys.argv[1:]),
    }
    # `--help`/`--list-mods`-style invocations convert nothing; letting them
    # rotate would push a real run's log out of the retained set for free.
    log = (None if _is_informational_argv()
           else run_log.start_cli_run(SCRIPT_DIR / "logs", config, header))
    code = 1
    try:
        code = _run_pipeline()
        return code
    except SystemExit as exc:
        # argparse exits this way for --help and for a bad flag.  Record the
        # REAL status rather than the "unset" 1, which read as a failed run.
        code = exc.code if isinstance(exc.code, int) else 0
        raise
    finally:
        run_log.finish_cli_run(log, f"EXIT: {code}")


# Flags that print something and exit without converting anything.  A run log
# exists to explain a CONVERSION; spending a rotation slot on `--help` would
# evict a real run's log.
_INFORMATIONAL_FLAGS = {"-h", "--help", "--list-mods"}


def _is_informational_argv() -> bool:
    return any(a in _INFORMATIONAL_FLAGS for a in sys.argv[1:])


def _config_path_from_argv() -> str | None:
    """Read --config out of argv before argparse runs.

    The run log is opened BEFORE _run_pipeline so the header, and any failure
    inside argument parsing, are captured -- but the config that carries
    `logRunsKept` is only located by --config. Scanning argv is the cheapest
    way to honour it without splitting the parser in two.
    """
    argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        if arg == "--config" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--config="):
            return arg.split("=", 1)[1]
    return None


def _version_string() -> str:
    try:
        import version as _v
        return _v.current_version()
    except Exception:
        return ""


if __name__ == "__main__":
    sys.exit(main())
