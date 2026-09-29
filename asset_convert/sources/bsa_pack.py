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
from asset_convert import case_paths, paths
from asset_convert.sources import base_plugins
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


def _collect_files(plugin_dir: Path, subdir_names: 'list[str]',
                   keep=frozenset()) -> 'list[tuple[Path, Path, int]]':
    """(source, archive path, size) for every file under plugin_dir/<subdir>/, sorted.

    Every case spelling of a top folder (`Textures/` beside `textures/`) is
    collected under its lowercase name.  Anything under textures/ that
    `texture_prune.is_excluded` rejects, unless `keep` names it, is left out.
    This is the ONLY place the prune applies: it never deletes from output/.
    See: docs/commentary/asset_convert_paths.md#pack-gate
    """
    out: 'list[tuple[Path, Path, int]]' = []
    for name in sorted({n.lower() for n in subdir_names}):
        for src in _top_variants(plugin_dir, name):
            out += _collect_tree(src, name, keep)
    out.sort(key=lambda t: str(t[1]).lower())
    return out


def _top_variants(plugin_dir: Path, name: str) -> 'list[Path]':
    """Every folder directly in `plugin_dir` spelling `name` in any case, sorted."""
    try:
        found = [d for d in plugin_dir.iterdir()
                 if d.name.lower() == name and d.is_dir()]
    except OSError:
        return []
    return sorted(found)


def _collect_tree(src: Path, name: str,
                  keep=frozenset()) -> 'list[tuple[Path, Path, int]]':
    """(file, `name`/relative archive path, size) for every packable file under `src`."""
    out = []
    is_textures = name == 'textures'
    for f in src.rglob('*'):
        if not f.is_file() or f.name.lower() in OS_JUNK_NAMES:
            continue
        if is_textures and texture_prune.is_excluded(
                f.relative_to(src).as_posix().lower(), keep):
            continue
        try:
            size = f.stat().st_size
        except OSError:
            continue
        out.append((f, Path(name) / f.relative_to(src), size))
    return out


def case_gate(plugin_dir: Path, collected, results: dict) -> bool:
    """False, with an error in `results`, when two packed files share one archive path.

    `collected` holds `_collect_files` lists, so collisions are judged on
    what would be packed (after the texture prune).  Folder twins only warn:
    BSArch lowercases every name, so they merge in the archive.
    See: docs/commentary/asset_convert_paths.md#pack-gate
    """
    entries = [e for files in collected for e in files]
    keyed = {}
    for src, rel, _size in entries:
        keyed.setdefault(rel.as_posix().lower(), []).append(str(src))
    clashes = case_paths.collisions(keyed)
    found = case_paths.census(plugin_dir, sorted({e[1].parts[0] for e in entries}))
    print('  ' + case_paths.census_line(
        plugin_dir.name, found._replace(file_collisions=clashes)))
    if not clashes:
        return True
    msg = (f"{len(clashes)} archive path(s) are held by two files that differ "
           f"only by case; nothing packed until one of each is removed")
    print(f"  ERROR {msg}")
    for group in clashes[:20]:
        print('        ' + '  |  '.join(group))
    results['errors'].append(msg)
    return False


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


def _misc_dirs(plugin_dir: Path) -> 'list[str]':
    """Lowercase names of the non-empty folders no explicit spec packs, one per case spelling."""
    return sorted({
        d.name.lower() for d in plugin_dir.iterdir()
        if d.is_dir()
        and d.name.lower() not in _KNOWN_DIRS
        and not d.name.startswith('_bsa_staging_')
        and any(d.rglob('*'))
    })


def _remove_stale_overflow(plugin_dir: Path, stem: str, results: dict) -> None:
    """Delete overflow archives and loaders a previous, larger run left behind.

    A later run needing the loader slot again would otherwise re-create
    `<stem>_loader.esl` over a stale `<stem>_loader.bsa` and serve the old
    conversion's assets.  Pre-rename `oblivion_loader*` files are swept too,
    and the stem is glob-escaped so a `[` in it cannot hide the files.
    """
    written = {Path(p).name.lower() for p in results['packed']}
    loaders = {Path(p).name for p in results['loaders']}
    stale_candidates = sorted(
        set(plugin_dir.glob(f'{_glob.escape(stem)}_loader*'))
        | set(plugin_dir.glob('oblivion_loader*'))
    )
    for stale in stale_candidates:
        suffix = stale.suffix.lower()
        if suffix not in ('.bsa', '.esl'):
            continue
        keep = (stale.name in loaders if suffix == '.esl'
                else stale.name.lower() in written)
        if not keep:
            stale.unlink()
            print(f"  CLEAN {stale.name}  (no longer needed)")


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


def _find_bsarch(bsarch_path, results: dict):
    """The BSArch executable to run, or None with the error in `results`."""
    bsarch = bsarch_path or str(paths.BSARCH)
    if Path(bsarch).is_file():
        print(f"  BSArch: {bsarch}")
        return bsarch
    msg = (
        "BSArch.exe not found.  Place BSArch.exe in external/bsarch/BSArch.exe "
        "under the project root, or set bsarchPath in conversion_config.json, or "
        "add BSArch.exe to the system PATH."
    )
    print(f"  ERROR: {msg}")
    results['errors'].append(msg)
    return None


def _plugin_dir(output_dir, source_name: str, export_root, results: dict):
    """The plugin's output folder, or None with the error in `results`.

    Resolved from the export ROOT (the repo's own export/ when none is given):
    an imported mod's plugins convert into their MOD's folder.
    See: docs/commentary/asset_convert_bsa.md#pack-bsas
    """
    plugin_dir = _out_root(Path(output_dir).resolve(), source_name,
                           export_root or _DEFAULT_EXPORT)
    if plugin_dir.is_dir():
        return plugin_dir
    msg = f"Plugin output directory not found: {plugin_dir}"
    print(f"  ERROR: {msg}")
    results['errors'].append(msg)
    return None


def _pack_specs(plugin_dir: Path, compress_textures: bool) -> list:
    """(subdir names, archive suffix, compress) per archive; textures spec first."""
    specs = [(dirs, suffix, compress or (compress_textures and suffix == 'Textures'))
             for dirs, suffix, compress in _BSA_SPECS]
    specs.append((['meshes'] + _misc_dirs(plugin_dir), '', False))
    return specs


def _pruned_keep(plugin_dir, export_dir, manifest_dir, results: dict):
    """Manifest keys in a pruned dir, or None (error in `results`) when the manifest is missing.

    A manifest is expected when an `export_dir` is given and the plugin has
    converted meshes; otherwise nothing is exempted.
    See: docs/commentary/asset_convert_texture.md#pruned-dir-references
    """
    if not (export_dir and _top_variants(plugin_dir, 'meshes')):
        return frozenset()
    name = texture_prune.MANIFEST_NAME
    if not (manifest_dir and (Path(manifest_dir) / name).is_file()):
        msg = (f"texture manifest {name} not found in {manifest_dir}; refusing "
               f"to pack without it (run the mesh stage first)")
        print(f"  ERROR {msg}")
        results['errors'].append(msg)
        return None
    return texture_prune.pruned_refs(texture_prune.read_manifest(manifest_dir))


def _carry_from_masters(keep, textures, export_dir, output_dir, export_root):
    """(entries, found-nowhere keys) for `keep` textures the plugin's own tree lacks.

    Each is looked up case-blind in its masters' output textures trees,
    nearest first (`base_plugins.names_for`: the LAST header master first, as
    load order lets it win); the dependent ships the texture it references.
    See: docs/commentary/asset_convert_texture.md#pruned-dir-references
    """
    have = {e[1].relative_to('textures').as_posix().lower() for e in textures}
    missing = sorted(keep - have)
    if not missing:
        return [], []
    out = Path(output_dir).resolve()
    roots = [top for m in base_plugins.names_for(export_dir)
             for top in _top_variants(_out_root(out, m, export_root), 'textures')]
    entries, nowhere = [], []
    for key in missing:
        hit = case_paths.resolve(roots, key, 'bsa_pack.pruned_ref')
        if hit is None:
            nowhere.append(key)
        else:
            entries.append((hit, Path('textures') / key, hit.stat().st_size))
    return entries, nowhere


def _exempt_pruned(collected, keep, pack_ctx):
    """Add master-carried `keep` textures to the textures list and print the tally.

    See: docs/commentary/asset_convert_texture.md#pruned-dir-references
    """
    carried, nowhere = _carry_from_masters(keep, collected[0], *pack_ctx)
    collected[0] = sorted(collected[0] + carried,
                          key=lambda t: str(t[1]).lower())
    print(f"  pruned-dir exempted: {len(keep)}  (own {len(keep) - len(carried) - len(nowhere)}"
          f", carried from masters {len(carried)}, found nowhere {len(nowhere)})")
    for key in nowhere:
        print(f"  WARN  referenced pruned-dir texture found in no tree: {key}")


def _pack_spec(plugin_dir, stem, spec, files, bsarch, size_limit, results):
    """Pack one spec's files into its archive(s); the loader slots it needs.

    Bin 0 keeps the name the plugin auto-mounts; overflow bin N lands in the
    archive `<stem>_loader[_N]` whose ESL mounts it.
    See: docs/commentary/asset_convert_bsa.md#pack-bsas
    """
    subdir_names, bsa_suffix, compress = spec
    base_name = f"{stem} - {bsa_suffix}.bsa" if bsa_suffix else f"{stem}.bsa"
    if not files:
        print(f"  SKIP  {base_name} (no source content)")
        results['skipped'].append(base_name)
        return 0
    bins = bin_files(files, size_limit)
    if len(bins) > 1:
        print(f"  SPLIT {base_name}: {sum(f[2] for f in files) / 1_048_576:.1f} MB of "
              f"{', '.join(subdir_names)} exceeds the "
              f"{size_limit / 1_048_576:.0f} MB per-archive budget "
              f"-> {len(bins)} archives")
    for bin_idx, entries in enumerate(bins):
        lstem = loader_stem(stem, bin_idx - 1) if bin_idx else stem
        bsa_path = plugin_dir / (
            f"{lstem} - {bsa_suffix}.bsa" if bsa_suffix else f"{lstem}.bsa")
        _pack_bin(plugin_dir, bsa_path, entries, spec, bsarch, size_limit,
                  results, bin_idx)
    return len(bins) - 1


def _pack_bin(plugin_dir, bsa_path, entries, spec, bsarch, size_limit,
              results, bin_idx):
    """Stage one bin as hardlinks, run BSArch on it, then remove the staging."""
    subdir_names, bsa_suffix, compress = spec
    bin_size = sum(e[2] for e in entries)
    if bin_size > size_limit and len(entries) == 1:
        print(f"  WARN  {entries[0][1]} is {bin_size / 1_048_576:.1f} MB, "
              f"larger than a whole BSA — it cannot be split")
    stage_root = plugin_dir / (
        f"_bsa_staging_{(bsa_suffix or 'main').lower()}_{bin_idx}")
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


def _write_loaders(plugin_dir, stem, source_name, loaders_needed, results):
    """One record-free ESL per overflow slot, so the game mounts those archives."""
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
    if loaders_needed:
        print(f"\n  NOTE: {loaders_needed} loader plugin(s) generated. "
              f"They must be enabled in the load order (after {source_name}) "
              f"for the overflow BSAs to be mounted.")


def pack_bsas(
    source_file: str,
    output_dir: str = 'output',
    bsarch_path: str = None,
    compress_textures: bool = False,
    size_limit: int = BSA_SIZE_LIMIT,
    export_dir: str = None,
    export_root: str = None,
    manifest_dir: str = None,
) -> dict:
    """Pack the plugin's output folder into Skyrim SE BSAs; the results dict.

    `<stem>.bsa` takes meshes/ plus the misc dirs and `<stem> - Textures.bsa`
    takes textures/, overflow spilling into loader-mounted archives.
    `export_dir` is the RECORD dir (it names the masters), `manifest_dir`
    holds `textures_used.txt`, and `export_root` resolves the output folder.
    Given an `export_dir` and meshes but no manifest, it refuses. Returns packed,
    skipped, errors and loaders lists.
    See: docs/commentary/asset_convert_bsa.md#pack-bsas
    """
    results: dict = {'packed': [], 'skipped': [], 'errors': [], 'loaders': []}
    source_name = Path(source_file).name
    bsarch = _find_bsarch(bsarch_path, results)
    plugin_dir = bsarch and _plugin_dir(output_dir, source_name, export_root,
                                        results)
    keep = plugin_dir and _pruned_keep(plugin_dir, export_dir, manifest_dir,
                                       results)
    if keep is None or not plugin_dir:
        return results
    specs = _pack_specs(plugin_dir, compress_textures)
    collected = [_collect_files(plugin_dir, spec[0], keep) for spec in specs]
    if keep:
        _exempt_pruned(collected, keep, (export_dir, output_dir, export_root))
    if not case_gate(plugin_dir, collected, results):
        return results
    stem = Path(source_name).stem
    loaders_needed = max(
        [_pack_spec(plugin_dir, stem, spec, files, bsarch, size_limit, results)
         for spec, files in zip(specs, collected)] + [0])
    _write_loaders(plugin_dir, stem, source_name, loaders_needed, results)
    _remove_stale_overflow(plugin_dir, stem, results)
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
                        help='The plugin RECORD dir (e.g. export/Oblivion.esm). '
                             'Names the masters and requires --manifest-dir; '
                             'without it no pruned-dir texture is exempted.')
    parser.add_argument('--manifest-dir', default=None, metavar='DIR',
                        help='The folder holding textures_used.txt (the '
                             'plugin ASSET dir, export/<mod group>/).')
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
                  export_root=a.export_root,
                  manifest_dir=a.manifest_dir)
    print(f"\nPacked: {len(r['packed'])}  Skipped: {len(r['skipped'])}  "
          f"Loaders: {len(r['loaders'])}  Errors: {len(r['errors'])}")
