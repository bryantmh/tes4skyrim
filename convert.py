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
  modify-body-meshes  Write the body-slot patch over a Skyrim load order
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

import io
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

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


from output_layout import (BODY_SLOTS_PATCH, finished_dir, plugin_out_root,
                           record_dir, tree_members, write_mod_zip)
from papyrus_compile import phase_compile
from tes4_export.tes3_reader import is_tes3
from core.plugin_masters import (get_masters_from_binary, is_master_export,
                                 topological_order)
import core.run_log as run_log


# Papyrus batch compilation (see phase_compile).  An error line from
# papyrus.exe looks like:  <path>\Foo.psc:12:3: Checker error: <message>
# The compiler aborts the whole batch on the first bad file, so each failing
# script is quarantined and the batch retried; this bounds that loop.

from core.subprocess_flags import (POPEN_FLAGS as _POPEN_FLAGS,
                              configure_multiprocessing)
from core.process_job import create_pool_job, describe_limit
from core.collision_options import WINDING_FIX_ENV_VAR, default_for_plugin

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

from source_paths import (get_paths, is_asset_only,
                          load_config, resolve_plugin_path)
from asset_convert.sources import source_registry
from convert_cli import build_parser, selected_steps
import preflight
import version as _version


# ---------------------------------------------------------------------------
#  Source management: --list-mods, --import-mod, --remove-mod
# ---------------------------------------------------------------------------

def _list_sources(export_dir: str) -> int:
    """--list-mods: Data folders, every plugin with several copies, then imported mods.

    See: docs/commentary/asset_convert_mod_ingest.md#same-named-plugins
    """
    _list_directories(export_dir)
    groups = source_registry.groups(export_dir)
    if not groups:
        print("No imported mods. Add one with:\n"
              "  python convert.py --import-mod <archive|folder>")
        return 0
    print(f"Imported mods ({len(groups)}):")
    for _gid, label, plugs in groups:
        first = source_registry.get(export_dir, plugs[0]) if plugs else {}
        total = sum((first or {}).get("counts", {}).values())
        print(f"  {label}" + (f"  ({total} files)" if total else ""))
        for name in plugs:
            print(f"    - {name}{_mod_plugin_note(export_dir, name)}")
    return 0


def _list_directories(export_dir: str) -> None:
    """Each registered Data folder, then each plugin found in more than one of them."""
    dirs = source_registry.directories(export_dir)
    print(f"Data folders ({len(dirs)}):")
    names = set()
    for row in dirs:
        print(f"  {row['label']}: {row['path']}")
        if os.path.isdir(row['path']):
            names.update(n for n in os.listdir(row['path'])
                         if n.lower().endswith((".esm", ".esp", ".esl")))
    shared = [(n, source_registry.copies(export_dir, n))
              for n in sorted(names, key=str.lower)]
    shared = [(n, rows) for n, rows in shared if len(rows) > 1]
    if shared:
        print("Plugins in more than one Data folder "
              "(pass --data-dir to convert a copy):")
    for name, rows in shared:
        print(f"  {name}")
        for path, folder in rows:
            print(f"    {path} -> export/{folder}")
    print()


def _mod_plugin_note(export_dir: str, name: str) -> str:
    """What --list-mods adds after an imported plugin: its kinds, or a missing binary."""
    entry = source_registry.get(export_dir, name) or {}
    if not entry.get("plugin"):
        kinds = ", ".join(k for k in ("meshes", "textures", "sound", "trees")
                          if (entry.get("capabilities") or {}).get(k))
        return "  (no plugin" + (f"; {kinds}" if kinds else "") + ")"
    if source_registry.plugin_binary(export_dir, name):
        return ""
    return "   [binary MISSING]"


def _mod_commands(args, export_dir: str, tes4_data: str) -> int:
    """Handle --import-mod / --remove-mod, then exit.

    Several sources import IN ORDER into one asset tree, later ones winning,
    as a mod manager resolves them. A source may also name an existing export
    tree, which is how the base game joins the stack.
    """
    from asset_convert.sources import mod_ingest

    if args.remove_mod:
        if mod_ingest.remove(args.remove_mod, export_dir):
            print(f"Removed {args.remove_mod}")
            return 0
        print(f"{args.remove_mod!r} is not an imported mod. "
              f"See --list-mods.")
        return 1

    sources = args.import_mod
    if isinstance(sources, str):
        sources = [sources]
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
    return _import_single(sources[0], args, export_dir, tes4_data, mod_ingest)


def _import_single(src, args, export_dir: str, tes4_data: str, mod_ingest) -> int:
    """Import one archive or mod folder, then say how to convert it."""
    try:
        manifest = mod_ingest.inspect(src)
        _print_manifest(manifest, mod_ingest)
        results = mod_ingest.ingest(
            src, export_dir,
            plugin_members=args.plugin_member,
            keep_archive=not args.no_keep_archive,
            manifest=manifest)
    except mod_ingest.IngestError as exc:
        print(f"ERROR: {exc}")
        return 1
    _warn_missing_masters(_missing_master_exports(results, export_dir, tes4_data))
    first = sorted(results)[0]
    _write_base_plugins(export_dir,
                        source_registry.asset_root_name(export_dir, first),
                        args.base)
    _print_next_step(first, results, mod_ingest)
    return 0


def _print_manifest(manifest, mod_ingest) -> None:
    """What an import found in the archive before it is ingested."""
    print(f"Archive : {manifest.path.name}")
    print(f"Layout  : {mod_ingest.layout_description(manifest.payload_root)}")
    print(f"Contents: {manifest.summary()}")
    if manifest.ambiguous_data:
        print("WARNING: several equally-shallow Data folders "
              f"({', '.join(manifest.ambiguous_data)}); using the first.")
    if manifest.bsas:
        print(f"BSAs    : {len(manifest.bsas)}")
    if manifest.nested:
        print(f"Nested  : {len(manifest.nested)} archive(s)")
    print(f"Plugins : {', '.join(manifest.plugins)}")


def _warn_missing_masters(missing) -> None:
    """Name every master with no export yet; importing before them resolves to nothing."""
    if not missing:
        return
    print()
    print("WARNING: these masters have no export yet:")
    for master, users in sorted(missing.items()):
        print(f"  {master}  (needed by {', '.join(sorted(users))})")
    print("Convert them FIRST, or the import will resolve their records "
          "to nothing:")
    print(f"  python convert.py -f {sorted(missing)[0]}")


def _print_next_step(first, results, mod_ingest) -> None:
    """The command that converts what was just imported; an asset-only mod gets only its asset steps."""
    quoted = f'"{first}"' if ' ' in first else first
    caps = (results[first] or {}).get('capabilities') or {}
    print()
    if caps.get('plugin', True):
        print("Imported. Convert it with:")
        print(f"  python convert.py -f {quoted}")
    else:
        steps = sorted(mod_ingest.available_steps(caps)
                       & {'meshes', 'speedtrees', 'sounds'})
        flags = ' '.join(f'--{s.replace("_", "-")}-only' for s in steps)
        print("Imported (asset-only mod -- no plugin to export or import).")
        print("Convert its assets with:")
        print(f"  python convert.py -f {quoted} {flags}".rstrip())


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
    from asset_convert.nif.shaders import BASE_PLUGINS_FILE
    d = Path(export_dir) / name / '_source'
    d.mkdir(parents=True, exist_ok=True)
    (d / BASE_PLUGINS_FILE).write_text('\n'.join(bases) + '\n',
                                       encoding='utf-8')
    print(f"  Base: {', '.join(bases)} (textures resolve through these)")


def _missing_master_exports(results, export_dir: str, tes4_data: str) -> dict:
    """{master_name: {plugins needing it}} for masters lacking an export dir.

    Resolved through the registry, never by joining the name onto `export/`:
    an imported mod's plugins live inside their mod's shared folder.

    See: docs/commentary/tes5_import_mod_merge.md#master-export-resolution
    """

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


# ===========================================================================
# Morroblivion compatibility patch
# ===========================================================================

def _build_morrowind_patch(data_dir: str, export_dir: str,
                           output_dir: str) -> int:
    """Build the Morroblivion compatibility patch, then exit.

    The same one action the GUI menu runs. It is the ONLY way to produce a
    plugin every Morroblivion-mode conversion declares as a master, so a
    GUI-only door left CLI users with a refusal naming a menu they never open.
    See: docs/commentary/tes4_export_morrowind.md#the-patch-builds-its-own-plugin
    """
    from tes4_export.export_morrowind import morroblivion_exports
    from tes4_export.morrowind_patch import build_patch

    exports = morroblivion_exports(export_dir)
    print("Building the Morroblivion compatibility patch")
    print(f"  Source : {data_dir}")
    if exports:
        print(f"  Against: {', '.join(exports)}")
    result = build_patch(data_dir, export_dir, exports, out_root=output_dir)
    if not result["ok"]:
        print(f"ERROR: {result['error']}")
        return 1
    print(f"Done in {result['seconds']:.1f}s -- {result['records']} records, "
          f"{result['assets']} assets.")
    print(f"  {result['plugin']}")
    return 0


# ===========================================================================
# Phase 1: Export TES4 RECORDS
# ===========================================================================

def _plugins_to_convert(args, config: dict, tes4_data: str,
                        export_dir: str) -> list:
    """The plugins to convert, masters first.

    Files always come from -f/--files, which is also how the GUI passes the
    selected plugins; `config["files"]` is a legacy fallback only. A mod's
    folder or label is refused when the mod ships plugins.

    See: docs/reference/pipeline.md#-f-takes-the-plugin
    """
    files = args.files or config.get("files", [])
    for name in files:
        shipped = source_registry.mod_plugins(export_dir, name)
        if shipped:
            raise SystemExit(
                f'ERROR: "{name}" is a mod, not a plugin. Convert its '
                f'plugin(s) instead: -f {" ".join(shipped)}')
    return topological_order(
        files, lambda name: resolve_plugin_path(name, tes4_data, export_dir))


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

    if is_tes3(source):
        from tes4_export.export_morrowind import run_export
        return run_export(file_name, source, export_dir, config)

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

def _stage_morrowind_voices(file_name: str, extract_dir: str) -> None:
    """Copy each exported bark's voice file into the TES4 voice layout.

    Runs for BOTH extract paths: a mod archive carries no Sound tree of its
    own, but its barks still play Morrowind's audio.
    See: docs/commentary/asset_convert_audio.md#morrowind-barks
    """
    from asset_convert.audio.morrowind_voice import find_sound_dir
    from asset_convert.sources.bsa_extract_morrowind_sounds import (
        stage_voices)
    from output_layout import asset_root, record_dir
    own = os.path.join(str(record_dir(extract_dir, file_name)), 'INFO.txt')
    sound_dir = find_sound_dir(extract_dir) if os.path.isfile(own) else None
    if not sound_dir:
        return
    staged = stage_voices(extract_dir, file_name, sound_dir,
                          asset_root(extract_dir, file_name))
    print(f"[{file_name}] Bark and Say recordings staged: {staged}")


def phase_extract(file_name: str, tes4_data: str, config: dict,
                  output_dir: str = None):
    """Get a plugin's assets into export/<name>/.

    Two sources, one output shape:
      * a plugin imported from a mod archive re-runs its ingest (which already
        produced the same tree the BSA extractor would have);
      * everything else extracts the BSAs sitting beside the plugin, in
        whichever registered Data directory holds it.
    """
    extract_dir = str(SCRIPT_DIR / "export")

    if source_registry.get(extract_dir, file_name):
        from asset_convert.sources import mod_ingest
        print(f"[{file_name}] Re-importing mod archive...")
        try:
            mod_ingest.reingest(file_name, extract_dir)
        except mod_ingest.IngestError as exc:
            print(f"[{file_name}] ERROR: {exc}")
            return False
        _stage_morrowind_voices(file_name, extract_dir)
        return True

    from asset_convert.asset_pipeline import extract_bsas

    print(f"[{file_name}] Extracting BSA archives...")
    extract_bsas(
        source_file=file_name,
        data_path=_plugin_data_dir(file_name, tes4_data, extract_dir),
        extract_dir=extract_dir,
    )
    return True


def _plugin_data_dir(file_name: str, tes4_data: str, export_dir: str) -> str:
    """The Data directory holding this plugin, and therefore its archives.

    A plugin from a registered install -- Morrowind, say -- keeps its BSAs
    beside itself, not in the Oblivion Data directory.
    """
    source = resolve_plugin_path(file_name, tes4_data, export_dir)
    if os.path.isfile(source):
        return os.path.dirname(source)
    return tes4_data

def _use_plugin_namespace(file_name: str) -> str:
    """Install `file_name`'s asset namespace for the phase about to run.

    Every phase runs in its own process, and only the ones calling into
    asset_pipeline set this, so the rest wrote the default namespace whatever
    plugin they were handed.
    See: docs/commentary/asset_convert_texture.md#per-game-asset-namespace
    """
    from asset_convert.game_paths import namespace_for, set_namespace
    ns = namespace_for(record_dir(str(SCRIPT_DIR / "export"), file_name))
    set_namespace(ns)
    return ns


# ===========================================================================
# Phase 3: CONVERT MESHES AND TEXTURES
# ===========================================================================

def phase_assets(file_name: str, config: dict, output_dir: str = None,
                 mesh_subdirs=None, winding_fix=None, parallax=False,
                 textures_only=False, skip_hair=False):
    """Convert extracted NIF assets and copy textures to output (meshes only).

    `winding_fix` tri-states the collision winding repair: True/False force it,
    None takes the per-plugin default for `file_name`.  The decision is pinned
    into the environment because the repair runs inside multiprocessing mesh
    workers, which inherit the environment but not this call's arguments.
    """
    _use_plugin_namespace(file_name)
    from asset_convert.asset_pipeline import convert_meshes

    extract_dir = str(SCRIPT_DIR / "export")
    out_dir     = output_dir or str(SCRIPT_DIR / "output")

    if winding_fix is None:
        winding_fix = default_for_plugin(file_name)
        origin = "plugin default"
    else:
        origin = "requested"
    os.environ[WINDING_FIX_ENV_VAR] = "1" if winding_fix else "0"
    print(f"[{file_name}] Collision winding fix: "
          f"{'on' if winding_fix else 'off'} ({origin})")

    print(f"[{file_name}] Converting meshes (NIFs + textures)...")
    stats = convert_meshes(
        source_file=file_name,
        extract_dir=extract_dir,
        output_dir=out_dir,
        mesh_subdirs=mesh_subdirs,
        parallax=parallax,
        textures_only=textures_only,
        skip_hair=skip_hair,
    )
    total = sum(v for v in stats.values() if isinstance(v, int))
    print(f"[{file_name}] Meshes complete ({total} items processed)")

    # Book inventory-art: bake each distinct BOOK model's textures onto the
    # vanilla Skyrim reading rigs (see asset_convert/ui/book_inam.py); the import
    # phase points each BOOK's INAM at meshes\tes4\clutter\books\inv\<base>.nif
    if textures_only:
        print(f"[{file_name}] Textures only: no meshes, no book art "
              f"(PGPatcher patches the meshes in the load order)")
        return True

    from asset_convert.ui.book_inam import generate_book_inams

    _, tes5_data = get_paths(config)
    print(f"[{file_name}] Generating book inventory-art meshes...")
    bstats = generate_book_inams(
        source_file=file_name,
        extract_dir=extract_dir,
        output_dir=out_dir,
        skyrim_data=tes5_data or None,
    )
    print(f"[{file_name}] Book INAM complete: ok={bstats['ok']} "
          f"note={bstats['note']} skip={bstats['skip']} fail={bstats['fail']}")
    return True

# ===========================================================================
# Phase 4: CONVERT SPEEDTREES
# ===========================================================================

def phase_speedtrees(file_name: str, config: dict, output_dir: str = None):
    """Convert SpeedTree `.spt` files into NIFs (separate step)."""
    _use_plugin_namespace(file_name)
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
    _use_plugin_namespace(file_name)
    from asset_convert.havok.creature_pipeline import convert_creatures

    export_root = str(SCRIPT_DIR / "export")
    export_subdir = str(record_dir(export_root, file_name))
    if not os.path.isdir(export_subdir):
        print(f"[{file_name}] No export directory, skipping creatures")
        return False
    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    out_meshes = str(plugin_out_root(out_root, file_name, export_root)
                     / "meshes")

    print(f"[{file_name}] Converting creatures (behavior projects + meshes)...")
    res = convert_creatures(export_subdir, out_meshes)
    print(f"[{file_name}] Creatures complete "
          f"({len(res['projects'])} projects, {len(res['errors'])} errors)")
    return not res['errors']

# ===========================================================================
# Phase 6: BUILD TES5 PLUGIN
# ===========================================================================

def phase_import(file_name: str, tes4_data: str, tes5_data: str,
                 export_dir: str, config: dict, output_dir: str = None):
    """Import using the Python tes5_import package."""
    _use_plugin_namespace(file_name)
    from tes5_import.pipeline import import_plugin
    from tes5_import.overrides.master_index import MissingMasterOutputError
    from tes5_import.base.artifact_schema import StaleArtifactError

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

    source = resolve_plugin_path(file_name, tes4_data, export_dir)
    tes4_masters = get_masters_from_binary(source) if os.path.isfile(source) else []
    masters = ['Skyrim.esm'] + tes4_masters

    is_esm = is_master_export(export_subdir)

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
    _use_plugin_namespace(file_name)
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
    from asset_convert.audio.music_convert import convert_music
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
    _use_plugin_namespace(file_name)
    from script_convert.pipeline import convert_all_scripts
    from tes5_import.base.artifact_schema import StaleArtifactError

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



# ===========================================================================
# Phase 10: PATCH SKYRIM (BODY SLOTS)
# ===========================================================================

def phase_modify_body_meshes(tes5_data: str = None, plugins: list = None,
                             output_dir: str = None):
    """Write the body-slot patch over the selected Skyrim load order, as one zip.

    The zip holds the patch plugin (tools/creature/patch_body_slots.py) and
    the split skin meshes it points the vanilla skin addons at. `plugins`
    defaults to Skyrim.esm; the GUI passes the whole selected load order, and
    a plugin missing from tes5_data is skipped with a warning. It patches the
    load order, not a conversion, so the zip goes into Finished Mods.
    See: docs/commentary/asset_convert_armor.md#body-slot-layout
    """
    if not tes5_data:
        print("WARNING: Skyrim data path not found - body-slot patch not generated")
        return True
    plugin_paths = []
    for name in plugins or ["Skyrim.esm"]:
        if (Path(tes5_data) / name).exists():
            plugin_paths.append(str(Path(tes5_data) / name))
        else:
            print(f"WARNING: {name} not found - skipping")
    if not plugin_paths:
        print("WARNING: none of the selected plugins were found - body-slot patch not generated")
        return True
    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    stage = out_root / f"_{BODY_SLOTS_PATCH}"
    shutil.rmtree(stage, ignore_errors=True)
    ret = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "tools" / "creature" / "patch_body_slots.py"),
         *plugin_paths, "-o", str(stage / f"{BODY_SLOTS_PATCH}.esp"),
         "--meshes-root", str(stage / "meshes")],
        cwd=str(SCRIPT_DIR), capture_output=True, text=True, **_POPEN_FLAGS)
    print(ret.stdout + ret.stderr, end="")
    if ret.returncode == 0:
        write_mod_zip(finished_dir(out_root) / f"{BODY_SLOTS_PATCH}.zip",
                      tree_members(stage))
    shutil.rmtree(stage, ignore_errors=True)
    return ret.returncode == 0


# ===========================================================================
# Phase 11: PACK BSA ARCHIVES
# ===========================================================================

def phase_pack(file_name: str, config: dict, output_dir: str = None):
    """Pack converted output assets into Skyrim SE BSA archives.

    Textures nothing references are filtered out as the archive is staged, so
    output/ keeps the full loose tree for testing (see bsa_pack).
    """
    from asset_convert.sources.bsa_pack import pack_bsas

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
    """Zip the converted plugin (.esm/.esl/.esp), .bsa and loose-only files.

    The zip lands in output_dir/"Finished Mods"/ — with every other installable
    artefact — and is named "<file_name>.zip". `bsa_pack.LOOSE_ONLY_DIRS` are
    kept loose in the archive, at their paths relative to the mod root.
    See: docs/reference/tes_runtime_fragments.md#never-packed
    """
    from asset_convert.sources.bsa_pack import LOOSE_ONLY_DIRS

    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    src_root = plugin_out_root(out_root, file_name,
                               str(SCRIPT_DIR / "export"))
    if not src_root.is_dir():
        print(f"[{file_name}] Source not found: {src_root}, skipping zip pack")
        return False

    def _loose_files():
        """Every file under a directory the BSA never packs, kept loose.

        Matched against the tree's OWN casing, so the arcname keeps the case
        a case-sensitive extractor needs.
        """
        for d in sorted(src_root.iterdir()):
            if d.is_dir() and d.name.lower() in LOOSE_ONLY_DIRS:
                for src in sorted(d.rglob("*")):
                    if src.is_file():
                        yield src

    # ONE mod in, ONE mod out: the folder holds every plugin of an imported
    # mod, so the zip is named for the MOD. Naming it after whichever plugin
    # happened to be the -f argument produced three identical archives under
    # three different names for a three-plugin pack.
    zip_path = finished_dir(out_root) / f"{src_root.name}.zip"

    members = [(src.name, src)
               for ext in ("*.esm", "*.esl", "*.esp", "*.bsa")
               for src in sorted(src_root.glob(ext))]
    members += [(str(src.relative_to(src_root)), src)
                for src in _loose_files()]
    if not members:
        zip_path.unlink(missing_ok=True)
        print(f"[{file_name}] No plugin/BSA files found, skipping zip pack")
        return False

    packed = write_mod_zip(zip_path, members)
    print(f"[{file_name}] Zip pack complete -> {zip_path} ({packed} files)")
    return True


# ===========================================================================
# Main
# ===========================================================================

def _run_pipeline():
    """Parse the command line, then run each selected step over every plugin."""
    args = build_parser().parse_args()
    config = load_config(args.config)
    if args.no_engine_branches:
        config["speedtreeEngineBranches"] = False
    tes4_data, tes5_data = get_paths(config)
    tes4_data = args.data_dir or tes4_data
    source_registry.select_directory(tes4_data)
    output_dir = args.output_dir or config.get("outputDir") or str(SCRIPT_DIR / "output")
    export_dir = str(SCRIPT_DIR / "export")
    os.makedirs(export_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    if args.build_morrowind_patch:
        return _build_morrowind_patch(args.build_morrowind_patch,
                                      export_dir, output_dir)
    if args.list_mods:
        return _list_sources(export_dir)
    if args.import_mod or args.remove_mod:
        return _mod_commands(args, export_dir, tes4_data)

    _print_run_banner(tes4_data, tes5_data, output_dir)
    order = _plugins_to_convert(args, config, tes4_data, export_dir)
    if not order and not args.modify_body_meshes:
        print("No files to process.")
        return 0
    _announce(order, export_dir)
    steps = selected_steps(args)
    missing = _preflight(steps)
    if missing is not None:
        return missing
    run = SimpleNamespace(args=args, config=config, tes4_data=tes4_data,
                          tes5_data=tes5_data, export_dir=export_dir,
                          output_dir=output_dir)
    step_ok, success = _run_steps(steps, order, run)
    _record_versions(step_ok, tes4_data)
    return _report(step_ok, success)


def _announce(order, export_dir) -> None:
    """Pin each plugin's home Data folder, then name the plugins and where each is read from.

    See: docs/commentary/asset_convert_mod_ingest.md#same-named-plugins
    """
    for fn in order:
        source_registry.claim_home(export_dir, fn)
    if _owned_by_a_parent_run():
        return
    print(f"  Files: {', '.join(order) if order else '(none needed)'}")
    for fn in order:
        here = source_registry.directory_for(export_dir, fn)
        if here:
            print(f"    {fn}: {here} -> export/"
                  f"{source_registry.asset_root_name(export_dir, fn)}")
    print()


def _preflight(steps):
    """RC_MISSING_DEP after reporting the first step whose tool is missing, else None.

    Every step is checked before any runs: a missing tool does not fail loudly
    on its own (Sounds without ffmpeg ships a mute plugin).
    """
    warning = preflight.python_version_warning()
    if warning:
        print(preflight.format_python_warning(warning))
    failed = preflight.check_phases(steps)
    if failed is None:
        return None
    phase, missing = failed
    print(preflight.format_report(phase, missing, steps[steps.index(phase) + 1:]))
    return preflight.RC_MISSING_DEP


#: step -> (banner, version-record key, scope). 'plugin' skips asset-only mods; 'global' runs once.
_PHASE_INFO = {
    'export': ("Phase 1: EXPORT TES4 RECORDS", 'export', 'plugin'),
    'extract': ("Phase 2: EXTRACT TES4 ARCHIVES", 'extract', 'all'),
    'meshes': ("Phase 3: CONVERT MESHES AND TEXTURES", 'meshes', 'all'),
    'speedtrees': ("Phase 4: CONVERT SPEEDTREES", 'speedtrees', 'all'),
    'creatures': ("Phase 5: CONVERT CREATURES", 'creatures', 'plugin'),
    'import': ("Phase 6: BUILD TES5 PLUGIN", 'import_', 'plugin'),
    'sounds': ("Phase 7: CONVERT SOUNDS", 'sounds', 'all'),
    'scripts': ("Phase 8: CONVERT SCRIPTS", 'scripts', 'plugin'),
    'lod': ("GENERATE LOD", 'create_lod', 'global'),
    'skyrim_patch': ("Phase 10: PATCH SKYRIM (BODY SLOTS)", 'modify_body_meshes',
                     'global'),
    'pack_bsa': ("Phase 11: PACK BSA ARCHIVES", 'pack', 'all'),
    'pack_zip': ("Phase 12: PACK ZIP ARCHIVES", 'pack_zip', 'all'),
}


def _phase_runners(run) -> dict:
    """step -> callable(plugin) -> ok, bound to this run's paths and options."""
    a, cfg, out = run.args, run.config, run.output_dir
    return {
        'export': lambda fn: phase_export(fn, run.tes4_data, run.export_dir, cfg),
        'extract': lambda fn: phase_extract(fn, run.tes4_data, cfg),
        'meshes': lambda fn: phase_assets(
            fn, cfg, output_dir=out, mesh_subdirs=a.mesh_subdirs,
            winding_fix=a.collision_winding_fix, parallax=a.parallax,
            textures_only=a.textures_only, skip_hair=a.skip_hair),
        'speedtrees': lambda fn: phase_speedtrees(fn, cfg, output_dir=out),
        'creatures': lambda fn: phase_creatures(fn, run.tes5_data, cfg,
                                                output_dir=out),
        'import': lambda fn: phase_import(fn, run.tes4_data, run.tes5_data,
                                          run.export_dir, cfg, output_dir=out),
        'sounds': lambda fn: phase_sounds(fn, cfg, output_dir=out),
        'scripts': lambda fn: (phase_scripts(fn, cfg, output_dir=out)
                               and phase_compile(fn, cfg, output_dir=out)),
        'lod': lambda _fn: _create_lod(out),
        'skyrim_patch': lambda _fn: phase_modify_body_meshes(
            run.tes5_data, plugins=a.patch_plugins, output_dir=out),
        'pack_bsa': lambda fn: phase_pack(fn, cfg, output_dir=out),
        'pack_zip': lambda fn: phase_pack_zip(fn, cfg, output_dir=out),
    }


def _phase_targets(scope, order, asset_only) -> list:
    """The plugins one step runs over; a global step runs once under the shared key."""
    if scope == 'global':
        return [_version.GLOBAL_PLUGIN_KEY]
    if scope == 'plugin':
        return [fn for fn in order if fn not in asset_only]
    return list(order)


def _run_steps(steps, order, run) -> tuple:
    """Run each step over its plugins: ({record key: {plugin: ok}}, all succeeded).

    A filtered mesh run converts only some subfolders, so it never certifies
    the Meshes step as rebuilt at this version.
    """
    asset_only = {fn for fn in order if is_asset_only(fn, run.export_dir)}
    if asset_only:
        print(f"  Asset-only (no plugin): {', '.join(sorted(asset_only))}")
        print("    -> skipping Export/Import/Scripts/Creatures for these")
        print()
    runners = _phase_runners(run)
    step_ok, success = {}, True
    for step in steps:
        title, key, scope = _PHASE_INFO[step]
        targets = _phase_targets(scope, order, asset_only)
        if not targets:
            continue
        print("=" * 54 + f"\n  {title}\n" + "=" * 54)
        for fn in targets:
            ok = bool(runners[step](fn))
            success = success and ok
            if not (step == 'meshes' and run.args.mesh_subdirs):
                slot = step_ok.setdefault(key, {})
                slot[fn] = slot.get(fn, True) and ok
        print()
    return step_ok, success


def _create_lod(output_dir) -> bool:
    """Bake LOD ONCE for the whole load order into AutoConvertLOD, never per plugin.

    Tiles sit on a fixed grid keyed by worldspace and coordinate, so `-f`
    cannot narrow the bake to one plugin.
    """
    cmd = [sys.executable, "-u",
           str(SCRIPT_DIR / "tools" / "release" / "create_lod.py")]
    if output_dir:
        cmd += ["--output-dir", str(output_dir)]
    return subprocess.call(cmd, **_POPEN_FLAGS) == 0


def _record_versions(step_ok, tes4_data) -> None:
    """Stamp the version on every step that completed; bookkeeping never fails a run."""
    try:
        for step_key, per_file in step_ok.items():
            for fn, ok in per_file.items():
                if ok:
                    _version.record_step_run(step_key, fn, data_path=tes4_data)
    except Exception as exc:
        print(f"Note: could not record conversion state ({exc}).")


def _report(step_ok, success) -> int:
    """Print the verdict, restating each failed step beside it; the exit code."""
    if success:
        if not _owned_by_a_parent_run():
            print("Pipeline complete.")
        return 0
    failed = [(step_key, fn)
              for step_key, per_file in step_ok.items()
              for fn, ok in per_file.items() if not ok]
    print()
    print("-" * 54)
    if failed:
        print(f"  ERROR SUMMARY ({len(failed)} failed step"
              f"{'' if len(failed) == 1 else 's'}):")
        for step_key, fn in failed:
            where = "all plugins" if fn == _version.GLOBAL_PLUGIN_KEY else fn
            print(f"    - {step_key}: FAILED for {where}")
    else:
        print("  ERROR SUMMARY: a stage reported failure; see the stage "
              "output above for details.")
    print("-" * 54)
    print("Pipeline completed with errors.")
    return 1


def _owned_by_a_parent_run() -> bool:
    """Whether a run owner (the GUI) launched us as one step of its run."""
    return bool(os.environ.get(run_log.RUN_LOG_ENV_VAR))


def _print_run_banner(tes4_data, tes5_data, output_dir) -> None:
    """Print the run's identity and settings.

    A GUI run is one process per step, so the settings -- which cannot change
    between steps -- print only for the process that owns the whole run.
    """
    if _owned_by_a_parent_run():
        return
    print("=" * 54)
    print("  TES4 -> TES5 Conversion Pipeline")
    print("=" * 54)
    print(f"  Oblivion data : {tes4_data or '(not found)'}")
    print(f"  Skyrim SE data: {tes5_data or '(not found)'}")
    print(f"  Output dir    : {output_dir}")
    print(f"  {describe_limit()}")
    print()


def main():
    """Own the run log for a standalone CLI run, then run the pipeline.

    Only a run's OWNER opens a log.  When the GUI launched us it has already
    opened one for the whole run (several convert.py invocations, one per step)
    and set TESCONV_RUN_LOG, so `start_cli_run` returns None here and we
    neither prune nor write -- otherwise a 7-step run would leave seven logs
    holding one step each.
    """
    try:
        config = load_config(_config_path_from_argv())
    except Exception:
        config = {}
    header = {
        "Version": _version_string(),
        "Command": " ".join(["convert.py"] + sys.argv[1:]),
    }
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


#: Flags that print and exit; logging one would evict a real run's log.
_INFORMATIONAL_FLAGS = {"-h", "--help", "--list-mods"}


def _is_informational_argv() -> bool:
    """Whether argv only asks for information, so no run log is opened."""
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
