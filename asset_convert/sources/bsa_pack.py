"""Pack converted output assets into Skyrim SE-compatible BSA archives.

Produces BSAs in ``output/<plugin>/``, alongside the converted ESM:
  Oblivion.bsa           meshes/ + misc directories (everything except textures)
  <stem> - Textures.bsa  textures/ sub-tree

Uses BSArch.exe (from xEdit / SSEEdit) for BSA5 (SSE) format creation.
BSArch is searched in common locations; pass ``bsarch_path`` to override.

Excluded textures
-----------------
Oblivion's BSAs carry textures for content the conversion never emits, so the
textures archive is filtered against ``texture_prune.is_excluded`` as it is
staged.  The filter runs HERE and nowhere else: packing is the only phase that
decides what ships, and a phase that deleted from ``output/`` instead would
fight the mesh phase (which re-copies the whole texture tree every run) and
would break loose-file testing.

Size limit / overflow
---------------------
The BSA format addresses file data with 32-bit offsets, so a single archive
cannot exceed 2 GiB (2,147,483,648 bytes).  Content that does not fit is split
across additional archives.  Skyrim only auto-mounts ``<PluginStem>.bsa`` and
``<PluginStem> - Textures.bsa`` for a plugin that is in the load order, so each
overflow archive is paired with a generated dummy ESL "loader" plugin whose
stem matches the archive name:

  <stem>_loader.esl     mounts <stem>_loader.bsa / <stem>_loader - Textures.bsa
  <stem>_loader_1.esl   mounts <stem>_loader_1.bsa / ...

The plugin stem is part of the loader name because loader stems are global to
the game's Data folder: a fixed name would make every converted mod that
overflows ship the same file, and installing two of them would silently
overwrite one mod's overflow archives with the other's.

Staging strategy: for each BSA a temporary directory is created inside
``output/<plugin>/_bsa_staging_<type>/`` containing only hardlinks to the
relevant files (near-instant on the same drive).  The directory is removed
after BSArch finishes.  Hardlinks fall back to full copies if the staging
directory is on a different drive.
"""
import glob as _glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

from core.subprocess_flags import POPEN_FLAGS, windows_cmd, to_wine_path
from tes5_import.base.writer import pack_tes4_header
from asset_convert import paths
from asset_convert.texture import texture_prune

# ---------------------------------------------------------------------------
# Size limits
# ---------------------------------------------------------------------------

# Hard engine limit: BSA file-data offsets are 32-bit.
BSA_HARD_LIMIT = 2_147_483_648

# BSArch writes a header, a folder table, a file table and two name tables in
# addition to the raw file bytes.  Budget for that overhead (plus per-file
# alignment slack) so the finished archive stays under the hard limit.
BSA_OVERHEAD_BUDGET = 64 * 1024 * 1024          # 64 MiB
BSA_SIZE_LIMIT = BSA_HARD_LIMIT - BSA_OVERHEAD_BUDGET   # ~2.0 GiB of payload

#: Lowercased OS-generated file names (Explorer/Finder metadata) never packed.
OS_JUNK_NAMES = frozenset(
    ('thumbs.db', 'ehthumbs.db', 'ehthumbs_vista.db', 'desktop.ini', '.ds_store'))


# ---------------------------------------------------------------------------
# Staging helpers
# ---------------------------------------------------------------------------

def long_path(path) -> str:
    """Render a path for Win32 APIs that would otherwise stop at MAX_PATH.

    Staged paths run `output/<plugin>/_bsa_staging_<type>/<subdir>/<rel>`, and
    a long plugin name plus creature animdata takes that past 260 characters:
    BSArch then reports `EAggregateException` under `-mt`, and "cannot find the
    path specified" without it.  Verified: with 381-character staged paths the
    prefix on BSArch's INPUT root packs the archive, and its absence fails.
    See: docs/commentary/asset_convert_bsa.md#staging-past-the-path-limit
    """
    s = str(path)
    if sys.platform != 'win32' or s.startswith('\\\\?\\') or not os.path.isabs(s):
        return s
    return '\\\\?\\' + os.path.normpath(s)


def _link_or_copy(src: Path, dst: Path) -> None:
    """Create a hardlink dst → src; fall back to copy on cross-device error."""
    long_dst = long_path(dst)
    try:
        os.link(long_path(src), long_dst)
    except OSError:
        shutil.copy2(long_path(src), long_dst)


def _collect_files(plugin_dir: Path, subdir_names: 'list[str]'
                   ) -> 'list[tuple[Path, Path, int]]':
    """Enumerate every file under plugin_dir/<subdir>/ for packing.

    Returns a list of (absolute_source, archive_relative_path, size_bytes),
    sorted by archive path so binning is deterministic across runs.

    Anything under textures/ that `texture_prune.is_excluded` rejects is left
    out of the archive.  This is the ONLY place the prune applies — it filters
    what gets packed and never deletes from output/, so loose-file testing
    keeps the full tree and re-running the pack is idempotent.
    """
    out: 'list[tuple[Path, Path, int]]' = []
    for name in subdir_names:
        src = plugin_dir / name
        if not src.is_dir():
            continue
        is_textures = name.lower() == 'textures'
        for f in src.rglob('*'):
            if not f.is_file() or f.name.lower() in OS_JUNK_NAMES:
                continue
            if is_textures and texture_prune.is_excluded(
                    f.relative_to(src).as_posix().lower()):
                continue
            # Archive path keeps the top-level dir (meshes/..., textures/...)
            rel = Path(name) / f.relative_to(src)
            try:
                size = f.stat().st_size
            except OSError:
                continue
            out.append((f, rel, size))
    out.sort(key=lambda t: str(t[1]).lower())
    return out


def bin_files(
    files: 'list[tuple[Path, Path, int]]',
    limit: int = BSA_SIZE_LIMIT,
) -> 'list[list[tuple[Path, Path, int]]]':
    """Split files into ordered bins, each with a total payload under `limit`.

    Greedy first-fit-decreasing is deliberately NOT used: keeping the natural
    path order groups related assets into the same archive, which makes the
    split reproducible and keeps a given directory mostly in one BSA.

    A single file larger than `limit` cannot be split; it gets a bin of its own
    and the caller is expected to warn about it.
    """
    bins: 'list[list[tuple[Path, Path, int]]]' = []
    current: 'list[tuple[Path, Path, int]]' = []
    current_size = 0

    for entry in files:
        size = entry[2]
        if current and current_size + size > limit:
            bins.append(current)
            current = []
            current_size = 0
        current.append(entry)
        current_size += size

    if current:
        bins.append(current)
    return bins


def _stage_bin(
    entries: 'list[tuple[Path, Path, int]]',
    stage_root: Path,
) -> int:
    """Hardlink one bin's files into stage_root, preserving archive paths.

    Directories are created through `long_path` for the same reason the links
    are: a staged path can exceed MAX_PATH even where its source does not.
    """
    count = 0
    for src, rel, _size in entries:
        dst = stage_root / rel
        os.makedirs(long_path(dst.parent), exist_ok=True)
        _link_or_copy(src, dst)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Dummy ESL loader plugins
# ---------------------------------------------------------------------------

ESL_FLAG = 0x0200   # "Light Master" (ESL) flag on the TES4 header record
ESM_FLAG = 0x0001


def write_loader_esl(path: Path, description: str = "") -> None:
    """Write a minimal, record-free ESL whose only job is to mount a BSA.

    Skyrim mounts ``<stem>.bsa`` and ``<stem> - Textures.bsa`` for every plugin
    in the load order.  An empty ESL is the cheapest way to get an extra BSA
    mounted: it holds no records, so it consumes no FormID space, and the ESL
    flag keeps it out of the 255-plugin limit.
    """
    header = pack_tes4_header(
        masters=[],
        num_records=0,
        next_object_id=0x800,
        description=description or "BSA loader (no records)",
        is_esm=True,
    )
    # pack_tes4_header only sets the ESM flag; add the ESL/light flag so the
    # plugin loads out of the ESL space and never eats a load-order slot.
    # TES4 header layout: sig[4] size[4] flags[4] ...
    flags = int.from_bytes(header[8:12], 'little') | ESM_FLAG | ESL_FLAG
    header = header[:8] + flags.to_bytes(4, 'little') + header[12:]
    path.write_bytes(header)


# ---------------------------------------------------------------------------
# Main packing logic
# ---------------------------------------------------------------------------

# (subdir_names_in_plugin, bsa_suffix, compress)
#   bsa_suffix '' means the plugin-stem archive (Oblivion.bsa)
_BSA_SPECS: 'list[tuple[list[str], str, bool]]' = [
    (['textures'], 'Textures', False),
]

#: Loose-only: SKSE sees no archived file. See: docs/reference/tes_runtime_fragments.md#never-packed
LOOSE_ONLY_DIRS: frozenset = frozenset(['skse'])

# Directory names already claimed by an explicit BSA spec, plus 'meshes' (which
# is added to the main spec by hand).  Everything else in the plugin output dir
# — sound/, scripts/, etc. — is auto-discovered as a misc dir and packed into
# the main archive alongside meshes.
_KNOWN_DIRS: frozenset = frozenset(
    n.lower()
    for spec in _BSA_SPECS
    for n in spec[0]
) | frozenset(['meshes']) | LOOSE_ONLY_DIRS


def loader_stem(plugin_stem: str, index: int) -> str:
    """Name of the Nth overflow loader plugin (0-based) for one plugin.

    Loader stems are GLOBAL to the game's Data folder even though they are
    generated per output folder, so the plugin stem has to be in the name: a
    fixed stem makes every converted mod that overflows ship a file with the
    same name, and installing two of them silently overwrites one mod's
    overflow archives with the other's.

    A stem containing ' - ' (e.g. 'Morrowind_ob - Chargen and Transport Mod')
    is safe.  Verified against SkyrimSE.exe at 0x140c64494: the engine locates
    the plugin's extension, overwrites it in place with '.bsa', and prepends
    'Data\\'.  It never parses backwards past the extension, so a separator
    earlier in the name cannot be mistaken for the ' - Textures' suffix.
    """
    base = f'{plugin_stem}_loader'
    return base if index == 0 else f'{base}_{index}'


def _run_bsarch(
    bsarch: str,
    stage_root: Path,
    bsa_path: Path,
    compress: bool,
    results: dict,
) -> bool:
    """Invoke BSArch on a staged directory.  Returns True on success.

    Both paths go through `to_wine_path`, because BSArch resolves a plain
    '/'-leading output path relative to the input directory rather than as
    absolute (verified under Wine 11.0: without it BSArch wrote
    "Z:<stage_root><bsa_path>" and failed "Path not found"); it no-ops on
    Windows.  They then go through `long_path`, which lifts the staging root
    past MAX_PATH on Windows and no-ops elsewhere.
    """
    bsa_name = bsa_path.name
    cmd = [bsarch, 'pack', long_path(to_wine_path(str(stage_root))),
           long_path(to_wine_path(str(bsa_path))), '-sse', '-mt']
    if compress:
        cmd.append('-z')

    try:
        completed = subprocess.run(
            windows_cmd(cmd),
            capture_output=True,
            text=True,
            timeout=1800,     # 30-minute cap for very large archives
            **POPEN_FLAGS,
        )
    except subprocess.TimeoutExpired:
        err_msg = f"{bsa_name}: BSArch timed out after 1800 s"
        print(f"  ERROR {err_msg}")
        results['errors'].append(err_msg)
        return False
    except Exception as exc:
        err_msg = f"{bsa_name}: {exc}"
        print(f"  ERROR {err_msg}")
        results['errors'].append(err_msg)
        return False

    if completed.returncode != 0:
        # BSArch may emit errors on stderr or stdout
        err_out = (completed.stderr or completed.stdout or '').strip()
        err_msg = f"{bsa_name}: BSArch exit {completed.returncode}: {err_out[:200]}"
        print(f"  ERROR {err_msg}")
        results['errors'].append(err_msg)
        if bsa_path.exists():
            bsa_path.unlink()   # remove partial archive
        return False

    size = bsa_path.stat().st_size if bsa_path.exists() else 0
    if size > BSA_HARD_LIMIT:
        err_msg = (
            f"{bsa_name}: archive is {size:,} bytes, over the "
            f"{BSA_HARD_LIMIT:,}-byte BSA limit — Skyrim cannot read it"
        )
        print(f"  ERROR {err_msg}")
        results['errors'].append(err_msg)
        return False

    print(f"  OK    {bsa_name}  ({size / 1_048_576:.1f} MB)")
    results['packed'].append(str(bsa_path))
    return True


_DEFAULT_EXPORT = paths.EXPORT


def _out_root(output_dir, plugin: str, export_root=None):
    """The plugin's output folder (its MOD's folder for an imported mod).

    `export_root` is the export ROOT (the folder holding sources.json), NOT a
    plugin's record directory. Handing it a record dir is how this resolved
    `output/<plugin>/` for a grouped plugin and aborted the pack with
    "output directory not found" -- the very failure it was added to fix.
    """
    try:
        from output_layout import plugin_out_root
        return plugin_out_root(output_dir, plugin,
                               str(export_root) if export_root else None)
    except ImportError:
        return Path(output_dir) / plugin


def pack_bsas(
    source_file: str,
    output_dir: str = 'output',
    bsarch_path: str = None,
    compress_textures: bool = False,
    size_limit: int = BSA_SIZE_LIMIT,
    export_dir: str = None,
    export_root: str = None,
) -> dict:
    """Pack converted assets into Skyrim SE BSA archives.

    Produces, inside ``output_dir/<source_name>/``:
      * ``Oblivion.bsa``          from meshes/ + remaining sub-directories
      * ``<stem> - Textures.bsa`` from textures/

    Content that would push an archive past the 2 GiB BSA limit spills into
    additional archives, each paired with a generated dummy ESL loader plugin
    (``<stem>_loader.esl``, ``<stem>_loader_1.esl``, …) so Skyrim mounts it.

    Texture categories Skyrim cannot load are left OUT of the textures archive
    (see ``texture_prune.is_excluded``).  This is a pack-time filter, not a
    delete: ``output/<plugin>/textures/`` keeps the full tree, so loose-file
    testing is unaffected and re-packing is idempotent.

    The source folder structure is NOT modified; original folders are left intact.

    Args:
        source_file:        Plugin filename (e.g. 'Oblivion.esm').
        output_dir:         Root output directory (default: 'output').
        bsarch_path:        Optional explicit path to BSArch.exe.
        compress_textures:  Compress the textures BSA (-z flag). Default False.
        size_limit:         Max payload bytes per archive (default ~2 GiB minus
                            BSA metadata overhead).
        export_dir:         This plugin's RECORD dir (e.g.
                            'export/Oblivion.esm', or
                            'export/<Mod>/<plugin>' for an imported mod).
                            Enables the texture keep-set; omit to pack every
                            texture on disk.
        export_root:        The export ROOT ('export/'), used only to resolve
                            which output folder this plugin converts into.
                            Distinct from `export_dir` on purpose: they are
                            the same folder for a game-Data plugin and two
                            different ones for an imported mod, and passing
                            the record dir here is what made the pack abort.

    Returns:
        dict with keys: packed (list of BSA paths), skipped (list),
        errors (list), loaders (list of generated .esl paths).
    """
    bsarch = bsarch_path or str(paths.BSARCH)
    if not Path(bsarch).is_file():
        msg = (
            "BSArch.exe not found.  Place BSArch.exe in external/bsarch/BSArch.exe "
            "under the project root, or set bsarchPath in conversion_config.json, or "
            "add BSArch.exe to the system PATH."
        )
        print(f"  ERROR: {msg}")
        return {'packed': [], 'skipped': [], 'errors': [msg], 'loaders': []}

    print(f"  BSArch: {bsarch}")

    source_name = Path(source_file).name
    # An imported mod's plugins all convert into their MOD's folder, so this
    # must be resolved rather than assumed -- the plain join names a folder
    # that does not exist and packing aborts with "output directory not found".
    # Resolved from the export ROOT: `export_dir` is a RECORD dir and holds no
    # sources.json, so the registry reads as empty and the resolver falls back
    # to the pre-group path. Fall back to the repo's own export/ so a caller
    # that passes neither still resolves an imported mod correctly.
    plugin_dir = _out_root(Path(output_dir).resolve(), source_name,
                           export_root or _DEFAULT_EXPORT)
    if not plugin_dir.is_dir():
        msg = f"Plugin output directory not found: {plugin_dir}"
        print(f"  ERROR: {msg}")
        return {'packed': [], 'skipped': [], 'errors': [msg], 'loaders': []}

    stem = Path(source_name).stem   # 'Oblivion'

    # Build the misc spec: any non-empty dirs not covered by the known specs
    misc_dirs = sorted(
        d.name for d in plugin_dir.iterdir()
        if d.is_dir()
        and d.name.lower() not in _KNOWN_DIRS
        and not d.name.startswith('_bsa_staging_')
        and any(d.rglob('*'))  # non-empty
    )

    specs = list(_BSA_SPECS)
    # Override compress for textures if requested
    if compress_textures:
        specs = [
            (dirs, suffix, True if suffix == 'Textures' else compress)
            for dirs, suffix, compress in specs
        ]

    # Combine meshes + misc into a single BSA named 'Oblivion.bsa'
    specs.append((['meshes'] + misc_dirs, '', False))

    results: dict = {'packed': [], 'skipped': [], 'errors': [], 'loaders': []}

    # Overflow archives are mounted by generated loader ESLs.  A loader plugin
    # mounts both '<stem>.bsa' and '<stem> - Textures.bsa', so each spec keeps
    # its own overflow counter and they share the loader plugins by index.
    loaders_needed = 0

    for subdir_names, bsa_suffix, compress in specs:
        base_name = f"{stem} - {bsa_suffix}.bsa" if bsa_suffix else f"{stem}.bsa"

        files = _collect_files(plugin_dir, subdir_names)
        if not files:
            print(f"  SKIP  {base_name} (no source content)")
            results['skipped'].append(base_name)
            continue

        bins = bin_files(files, size_limit)
        total = sum(f[2] for f in files)

        if len(bins) > 1:
            print(f"  SPLIT {base_name}: {total / 1_048_576:.1f} MB of "
                  f"{', '.join(subdir_names)} exceeds the "
                  f"{size_limit / 1_048_576:.0f} MB per-archive budget "
                  f"-> {len(bins)} archives")

        for bin_idx, entries in enumerate(bins):
            bin_size = sum(e[2] for e in entries)

            # Warn on a single file that cannot possibly fit.
            if bin_size > size_limit and len(entries) == 1:
                print(f"  WARN  {entries[0][1]} is {bin_size / 1_048_576:.1f} MB, "
                      f"larger than a whole BSA — it cannot be split")

            if bin_idx == 0:
                # First bin keeps the name the real plugin auto-mounts.
                bsa_path = plugin_dir / base_name
            else:
                # Overflow: mounted by <stem>_loader[_N].esl
                loader_idx = bin_idx - 1
                lstem = loader_stem(stem, loader_idx)
                loaders_needed = max(loaders_needed, loader_idx + 1)
                bsa_path = plugin_dir / (
                    f"{lstem} - {bsa_suffix}.bsa" if bsa_suffix else f"{lstem}.bsa"
                )

            stage_root = plugin_dir / (
                f"_bsa_staging_{(bsa_suffix or 'main').lower()}_{bin_idx}"
            )
            if stage_root.exists():
                shutil.rmtree(long_path(stage_root))
            stage_root.mkdir(parents=True)

            try:
                n_files = _stage_bin(entries, stage_root)
                print(f"  PACK  {bsa_path.name}  ({n_files} files, "
                      f"{bin_size / 1_048_576:.1f} MB "
                      f"from {', '.join(subdir_names)})")
                _run_bsarch(bsarch, stage_root, bsa_path, compress, results)
            except Exception as exc:
                err_msg = f"{bsa_path.name}: {exc}"
                print(f"  ERROR {err_msg}")
                results['errors'].append(err_msg)
            finally:
                if stage_root.exists():
                    shutil.rmtree(long_path(stage_root), ignore_errors=True)

    # Generate one dummy ESL per overflow slot so the game mounts those BSAs.
    for i in range(loaders_needed):
        esl_path = plugin_dir / f"{loader_stem(stem, i)}.esl"
        try:
            write_loader_esl(esl_path, description=f"BSA loader for {source_name}")
            print(f"  OK    {esl_path.name}  (BSA loader plugin)")
            results['loaders'].append(str(esl_path))
        except Exception as exc:
            err_msg = f"{esl_path.name}: {exc}"
            print(f"  ERROR {err_msg}")
            results['errors'].append(err_msg)

    # Remove stale overflow archives and loaders left by a previous, larger run.
    # This matters beyond tidiness: a later run that needs the loader slot again
    # would otherwise re-create <stem>_loader.esl on top of a stale
    # <stem>_loader.bsa, silently serving assets from the old conversion.
    # 'oblivion_loader*' is swept too: loaders used to be named that regardless
    # of the plugin, so an output folder built before the rename still holds
    # them and nothing else would ever clear them.
    # glob.escape: a plugin stem is arbitrary text and may hold '[' or '?',
    # which would make the pattern match nothing and silently strand the very
    # files this sweep exists to remove.
    written = {Path(p).name.lower() for p in results['packed']}
    stale_candidates = sorted(
        set(plugin_dir.glob(f'{_glob.escape(stem)}_loader*'))
        | set(plugin_dir.glob('oblivion_loader*'))
    )
    for stale in stale_candidates:
        if stale.suffix.lower() not in ('.bsa', '.esl'):
            continue
        if stale.suffix.lower() == '.esl':
            keep = stale.name in {Path(p).name for p in results['loaders']}
        else:
            keep = stale.name.lower() in written
        if not keep:
            stale.unlink()
            print(f"  CLEAN {stale.name}  (no longer needed)")

    if loaders_needed:
        print(f"\n  NOTE: {loaders_needed} loader plugin(s) generated. "
              f"They must be enabled in the load order (after {source_name}) "
              f"for the overflow BSAs to be mounted.")

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description='Pack output assets into Skyrim SE BSA archives',
    )
    parser.add_argument('source_file', help='Plugin filename (e.g. Oblivion.esm)')
    parser.add_argument('--output-dir', default='output',
                        help='Root output directory (default: output)')
    parser.add_argument('--bsarch', default=None, metavar='PATH',
                        help='Path to BSArch.exe (auto-detected by default)')
    parser.add_argument('--compress-textures', action='store_true',
                        help='Compress the textures BSA (-z flag)')
    parser.add_argument('--size-limit', type=int, default=BSA_SIZE_LIMIT,
                        metavar='BYTES',
                        help=f'Max payload bytes per BSA (default: {BSA_SIZE_LIMIT})')
    parser.add_argument('--export-dir', default=None, metavar='DIR',
                        help='Export text dir (e.g. export/Oblivion.esm). '
                             'Enables the texture keep-set; without it every '
                             'texture on disk is packed.')
    parser.add_argument('--export-root', default=None, metavar='DIR',
                        help='The export ROOT (default: the repo export/). '
                             'Resolves which output folder the plugin '
                             'converts into for an imported mod.')
    a = parser.parse_args()
    r = pack_bsas(a.source_file, output_dir=a.output_dir,
                  bsarch_path=a.bsarch,
                  compress_textures=a.compress_textures,
                  size_limit=a.size_limit,
                  export_dir=a.export_dir,
                  export_root=a.export_root)
    print(f"\nPacked: {len(r['packed'])}  Skipped: {len(r['skipped'])}  "
          f"Loaders: {len(r['loaders'])}  Errors: {len(r['errors'])}")
