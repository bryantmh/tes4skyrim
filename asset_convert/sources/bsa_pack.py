"""Pack converted output assets into Skyrim SE BSA archives.

Writes into the plugin's output folder, `<stem>` from `archive_stem`, every
packed folder in path order, split at the size limit:
  <stem>.bsa             the first archive
  <stem> - Part N.bsa    each next one, N from 2

A repack copies every unchanged file's stored bytes from the previous archives
and compresses only new or changed files (`bsa_write`).
See: docs/commentary/asset_convert_bsa.md#pack-layout
"""
import glob as _glob
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from core.plugin_masters import masters_from_export_header
from output_layout import record_dir
from asset_convert import paths
from asset_convert.game_paths import DEFAULT_NAMESPACE, HYBRID_NAMESPACE, namespace_for
from asset_convert.sources import source_registry
from asset_convert.sources import output_files
from asset_convert.sources.bsa_write import (
    archive_path, long_path, split, stage, stored_index, write_bsa)
from asset_convert.texture import texture_prune

# ---------------------------------------------------------------------------
# Limits and names
# ---------------------------------------------------------------------------

#: The largest archive written, in bytes: file-data offsets are 32-bit and the game reads them signed.
BSA_HARD_LIMIT = 2_147_483_648
#: Default cap on one archive's exact size.
BSA_SIZE_LIMIT = BSA_HARD_LIMIT

#: Lowercased OS-generated file names (Explorer/Finder metadata) never packed.
OS_JUNK_NAMES = frozenset(
    ('thumbs.db', 'ehthumbs.db', 'ehthumbs_vista.db', 'desktop.ini', '.ds_store'))

#: Loose-only: SKSE sees no archived file, nor does BinkOpen. See: docs/reference/tes_runtime_fragments.md#never-packed
LOOSE_ONLY_DIRS: frozenset = frozenset(['skse', 'video'])

#: Per-folder record of what the archives hold, for incremental repacks.
MANIFEST_NAME = output_files.MANIFEST_NAME
#: Scratch folder for compressed chunks, inside the output folder so moves stay on one drive.
WORK_DIR = '_bsa_work'
#: Bump when the stored payload or archive layout changes, so no older archive is reused.
SCHEME = 1
#: Suffixes of archives older builds wrote beside `<stem>.bsa`: reused as payload sources, then swept.
LEGACY_SUFFIXES = ('Textures',)

_DEFAULT_EXPORT = paths.EXPORT


def _archive_name(stem: str, suffix: str) -> str:
    """`<stem> - <suffix>.bsa`, or `<stem>.bsa` for the main archive."""
    return f"{stem} - {suffix}.bsa" if suffix else f"{stem}.bsa"


def part_name(stem: str, number: int) -> str:
    """The Nth overflow archive's name (N starts at 2)."""
    return _archive_name(stem, f'Part {number}')


# ---------------------------------------------------------------------------
# Collecting files
# ---------------------------------------------------------------------------

def storable(path: str) -> bool:
    """Whether an archive path fits the format: one-byte name lengths and one byte per character."""
    try:
        path.encode('latin-1')
    except UnicodeEncodeError:
        return False
    return len(path) < 255


def _walk(root: str):
    """Yield every file DirEntry under `root`, depth first."""
    for entry in os.scandir(root):
        if entry.is_dir(follow_symlinks=False):
            yield from _walk(entry.path)
        elif entry.is_file():
            yield entry


def _entries(plugin_dir: Path, subdir_names: 'list[str]'):
    """Yield (archive path, DirEntry) for every file under plugin_dir/<subdir>/."""
    for name in subdir_names:
        src = long_path((plugin_dir / name).resolve())
        if os.path.isdir(src):
            for entry in _walk(src):
                yield archive_path(name + entry.path[len(src):]), entry


def _unshipped(rel: str, entry) -> bool:
    """Whether the archives never take this file: OS junk, or a texture `texture_prune` excludes."""
    return entry.name.lower() in OS_JUNK_NAMES or _pruned(rel)


def _collect_files(plugin_dir: Path, subdir_names: 'list[str]') -> list:
    """(archive path, source, size, mtime_ns) for every file under plugin_dir/<subdir>/, sorted by path.

    `_unshipped` files are left out; `drop_loose` deletes them.
    """
    out = []
    for rel, entry in _entries(plugin_dir, subdir_names):
        if _unshipped(rel, entry):
            continue
        if not storable(rel):
            print(f"  WARN  {rel}: name cannot be stored in a BSA; left out")
            continue
        st = entry.stat()
        out.append((rel, entry.path, st.st_size, st.st_mtime_ns))
    out.sort(key=lambda t: t[0])
    return out


def _pruned(rel: str) -> bool:
    """Whether `texture_prune` keeps this archive path out of the archives."""
    top, _, rest = rel.partition('\\')
    return top == 'textures' and texture_prune.is_excluded(rest.replace('\\', '/'))


def packed_dirs(plugin_dir: Path) -> list:
    """Every top folder the archives hold: all but `LOOSE_ONLY_DIRS` and the packer's scratch.

    See: docs/commentary/asset_convert_bsa.md#pack-layout
    """
    return sorted(d.name for d in plugin_dir.iterdir()
                  if d.is_dir() and d.name.lower() not in LOOSE_ONLY_DIRS
                  and not d.name.startswith('_bsa_'))


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def _load_manifest(plugin_dir: Path) -> dict:
    """The last pack's manifest, or an empty one when absent, unreadable or of another scheme."""
    try:
        data = json.loads((plugin_dir / MANIFEST_NAME).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'archives': {}, 'files': {}}
    if data.get('scheme') != SCHEME:
        return {'archives': {}, 'files': {}}
    return data


def _stamp(path: Path) -> list:
    """[size, mtime_ns] of a file on disk, or None."""
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def _trusted(plugin_dir: Path, manifest: dict) -> list:
    """Archives the manifest recorded that are still exactly as this packer wrote them."""
    return [plugin_dir / name for name, rec in manifest['archives'].items()
            if _stamp(plugin_dir / name) == rec['stamp']]


def _digest(items: list, sigs: dict) -> str:
    """Identity of one archive's planned contents: every path with its source signature."""
    h = hashlib.sha1(str(SCHEME).encode())
    for path, _span, compressed in items:
        h.update(f'{path}|{sigs[path]}|{compressed}\n'.encode('latin-1'))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------

def pack_bsas(source_file: str, output_dir: str = 'output', size_limit: int = BSA_SIZE_LIMIT,
              export_root: str = None) -> dict:
    """Pack a plugin's converted assets into Skyrim SE BSA archives.

    Writes `<stem>.bsa` and `<stem> - Part N.bsa` overflow into the plugin's
    output folder, each at most `size_limit` bytes;
    `export_root` is the export ROOT the folder resolves from. Returns
    {'packed', 'kept', 'skipped', 'errors'} (kept: unchanged, not rewritten).
    See: docs/commentary/asset_convert_bsa.md#pack-layout
    """
    results: dict = {'packed': [], 'kept': [], 'skipped': [], 'errors': []}
    root = export_root or _DEFAULT_EXPORT
    source_name = Path(source_file).name
    plugin_dir = _out_root(Path(output_dir).resolve(), source_name, root)
    if not plugin_dir.is_dir():
        results['errors'].append(f"Plugin output directory not found: {plugin_dir}")
        print(f"  ERROR: {results['errors'][-1]}")
        return results
    stem = archive_stem(source_name, root)
    if not ships_plugin(plugin_dir, stem):
        print(f"  SKIP  {plugin_dir.name}: no {stem}.esm/.esp/.esl in the folder, so its assets stay loose")
        results['skipped'].append(plugin_dir.name)
        _sweep_stale(plugin_dir, stem, [], results)
        return results
    started = time.perf_counter()
    others = _stale_stems(source_name, root, stem)
    try:
        _pack(plugin_dir, stem, size_limit, results, others, _foreign_namespaces(root, source_name))
    except (OSError, ValueError) as exc:
        results['errors'].append(f"{stem}: {exc}")
        print(f"  ERROR {stem}: {exc}")
    _sweep_stale(plugin_dir, stem, others, results)
    print(f"  Packed in {time.perf_counter() - started:.1f} s")
    return results


def ships_plugin(plugin_dir: Path, stem: str) -> bool:
    """Whether the folder holds the plugin its archives would be named for; only such outputs are packed.

    See: docs/commentary/asset_convert_bsa.md#pack-layout
    """
    return any((plugin_dir / f'{stem}{ext}').is_file() for ext in ('.esm', '.esp', '.esl'))


def _foreign_namespaces(export_root, plugin: str) -> frozenset:
    """Asset namespaces the plugin's mod folder never writes: other families' and the hybrid shared mod's.

    See: docs/commentary/asset_convert_bsa.md#repacking-over-deleted-loose-files
    """
    def ns(name):
        """`name`'s namespace."""
        return namespace_for(record_dir(export_root, name))
    known = {ns(p) for p in source_registry.plugins(export_root)} | {DEFAULT_NAMESPACE, HYBRID_NAMESPACE}
    return frozenset(known - {ns(m) for m in source_registry.group_members(export_root, plugin)})


def _namespace_of(path: str) -> str:
    """The namespace folder of archive path `path` (`meshes\\<ns>\\...`, `meshes\\actors\\<ns>\\...`), or ''."""
    parts = path.split('\\')
    at = 2 if parts[:2] == ['meshes', 'actors'] else 1
    return parts[at] if len(parts) > at + 1 else ''


def _pack(plugin_dir: Path, stem: str, limit: int, results: dict, other_stems=(), foreign=frozenset()) -> None:
    """Plan, write and swap in the folder's archives, then record them in the manifest.

    A compressed file is reused from a trusted old archive when its source's
    size and mtime match the manifest's; a file with no loose copy is kept from
    the old archives (`_carried`), including `other_stems`' that this set replaces,
    unless it sits under one of the `foreign` namespaces.
    See: docs/commentary/asset_convert_bsa.md#incremental-repack
    """
    manifest = _load_manifest(plugin_dir)
    files = _collect_files(plugin_dir, packed_dirs(plugin_dir))
    sigs = {f[0]: [f[2], f[3]] for f in files}
    stored = stored_index(_archives_of(plugin_dir, [stem, *other_stems]))
    trusted = {str(p) for p in _trusted(plugin_dir, manifest)}
    reusable = {path: span for path, (span, compressed) in stored.items()
                if compressed and span[0] in trusted and manifest['files'].get(path) == sigs.get(path)}
    carried = {path: entry for path, entry in _carried(plugin_dir, stored, sigs, manifest).items()
               if _namespace_of(path) not in foreign}
    for path, (span, compressed) in carried.items():
        sigs[path] = manifest['files'].get(path) or [span[2], 0]
        if compressed:
            reusable[path] = span
        files.append((path, None if compressed else span, span[2], 0))
    files.sort(key=lambda f: f[0])
    if not files:
        results['skipped'].append(_archive_name(stem, ''))
    work = plugin_dir / WORK_DIR
    shutil.rmtree(long_path(work), ignore_errors=True)
    work.mkdir()
    try:
        plan = _plan(stem, files, limit, reusable, str(work))
        records = _write_plan(plugin_dir, plan, sigs, manifest, results)
    finally:
        shutil.rmtree(long_path(work), ignore_errors=True)
    _write_manifest(plugin_dir, {'scheme': SCHEME, 'archives': records, 'files': sigs,
                                 'gone': sorted(carried)})
    (plugin_dir / output_files.DISCARDED_NAME).unlink(missing_ok=True)
    print(f"  {len(reusable)} compressed file(s) reused from the previous archives"
          + (f"; {len(carried)} kept from them with no loose copy" if carried else ""))


def _write_manifest(plugin_dir: Path, manifest: dict) -> None:
    """Write the folder's manifest and drop the output layer's cached view of it."""
    (plugin_dir / MANIFEST_NAME).write_text(json.dumps(manifest), encoding='utf-8')
    output_files.forget(plugin_dir)


def _carried(plugin_dir: Path, stored: dict, loose: dict, manifest: dict) -> dict:
    """{path: (span, compressed)} of archived files to keep that have no loose copy.

    The archive is the source of truth for a file the run did not write. One
    is dropped only when `output_files.discard` marked it, the prune now
    rejects it, or the last pack read it loose (`gone` lacks it) and a step
    has since deleted it.
    See: docs/commentary/asset_convert_bsa.md#repacking-over-deleted-loose-files
    """
    gone = set(manifest.get('gone', ()))
    dropped = output_files.discarded(plugin_dir)
    return {path: entry for path, entry in stored.items()
            if path not in loose and path not in dropped and not _pruned(path)
            and path.split('\\', 1)[0] not in LOOSE_ONLY_DIRS
            and (path in gone or path not in manifest['files'])}


def _plan(stem: str, files: list, limit: int, reusable: dict, work: str) -> list:
    """[(archive name, staged items)]: `<stem>.bsa`, then `Part 2`, `Part 3`, ... in path order."""
    bins = split(stage([f[:3] for f in files], reusable, work), limit) if files else []
    return [(part_name(stem, n) if n > 1 else _archive_name(stem, ''), items)
            for n, items in enumerate(bins, 1)]


def _write_plan(plugin_dir: Path, plan: list, sigs: dict, manifest: dict, results: dict) -> dict:
    """Write every changed archive to a temp file, then swap them all in; returns the manifest's archive records.

    An archive whose planned contents match its manifest digest and that is
    still on disk as recorded is kept untouched.
    """
    trusted = {p.name for p in _trusted(plugin_dir, manifest)}
    records, temps = {}, []
    for name, items in plan:
        digest = _digest(items, sigs)
        if name in trusted and manifest['archives'][name]['digest'] == digest:
            records[name] = manifest['archives'][name]
            results['kept'].append(str(plugin_dir / name))
            print(f"  KEEP  {name}  ({len(items)} files, unchanged)")
            continue
        temp = plugin_dir / f'{name}.tmp'
        write_bsa(temp, items)
        temps.append((temp, plugin_dir / name, digest, len(items)))
    for temp, dest, digest, count in temps:
        os.replace(temp, dest)
        records[dest.name] = {'stamp': _stamp(dest), 'digest': digest}
        results['packed'].append(str(dest))
        print(f"  OK    {dest.name}  ({count} files, {dest.stat().st_size / 1_048_576:.1f} MB)")
    return records


# ---------------------------------------------------------------------------
# Archive names and stale files
# ---------------------------------------------------------------------------

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


def archive_stem(plugin: str, export_root) -> str:
    """The stem a plugin's archives are named for: the mod member it builds on.

    Follows the plugin's masters while they belong to its own imported mod; the
    member reached masters no other member, so it is loaded whenever any of
    them is. A plugin that masters no member is its own stem.
    See: docs/commentary/asset_convert_bsa.md#one-archive-set-per-mod
    """
    members = {m.lower(): m for m in
               source_registry.group_members(export_root, plugin)}
    cur, seen = plugin, set()
    while cur.lower() not in seen:
        seen.add(cur.lower())
        up = [members[m.lower()] for m in masters_from_export_header(
            str(record_dir(export_root, cur))) if m.lower() in members]
        if not up:
            break
        cur = up[0]
    return Path(cur).stem


def _stale_stems(plugin: str, export_root, stem: str) -> list:
    """Stems of the mod's other plugins whose archives `stem`'s set now replaces."""
    return sorted({Path(m).stem for m in source_registry.group_members(
        export_root, plugin) if archive_stem(m, export_root) == stem} - {stem})


def _archives_of(plugin_dir: Path, stems: list) -> list:
    """Every archive on disk named for one of `stems`: main, Part N and `LEGACY_SUFFIXES`."""
    found = set()
    for name in stems:
        found |= set(plugin_dir.glob(f'{_glob.escape(name)} - Part *.bsa'))
        found |= {plugin_dir / _archive_name(name, suffix) for suffix in ('', *LEGACY_SUFFIXES)}
    return sorted(p for p in found if p.is_file())


def _stale_candidates(plugin_dir: Path, stem: str, other_stems: list) -> list:
    """Archives, loader plugins and scratch files in `plugin_dir` a run may have left behind.

    Covers older builds' `_loader` ESL+BSA pairs and BSArch staging folders.
    """
    found = set(plugin_dir.glob('oblivion_loader*'))
    found |= set(plugin_dir.glob('*.bsa.tmp')) | set(plugin_dir.glob('_bsa_staging_*'))
    for name in [stem] + other_stems:
        esc = _glob.escape(name)
        found |= set(plugin_dir.glob(f'{esc}_loader*')) | set(plugin_dir.glob(f'{esc} - Part *.bsa'))
        found |= {plugin_dir / _archive_name(name, suffix) for suffix in ('', *LEGACY_SUFFIXES)}
    return sorted(p for p in found if p.exists())


def _sweep_stale(plugin_dir: Path, stem: str, other_stems: list, results: dict) -> None:
    """Delete archives, loaders and scratch files this run did not write or keep.

    See: docs/commentary/asset_convert_bsa.md#pack-layout
    """
    current = {Path(p).name.lower() for p in results['packed'] + results['kept']}
    for stale in _stale_candidates(plugin_dir, stem, other_stems):
        if stale.name.lower() in current:
            continue
        if stale.is_dir():
            shutil.rmtree(long_path(stale), ignore_errors=True)
        else:
            stale.unlink()
        print(f"  CLEAN {stale.name}  (no longer needed)")


# ---------------------------------------------------------------------------
# Dropping the loose copies
# ---------------------------------------------------------------------------

def delete_packed_loose(source_file: str, output_dir: str = 'output', export_root: str = None) -> tuple:
    """`drop_loose` on the plugin's output folder; returns (files, bytes) freed."""
    root = export_root or _DEFAULT_EXPORT
    return drop_loose(_out_root(Path(output_dir).resolve(), Path(source_file).name, root))


def drop_loose(plugin_dir: Path) -> tuple:
    """Delete the loose files the folder's archives now store, and the `_unshipped` ones; returns (files, bytes).

    A stored file is removed only while its size and mtime still equal what
    the last pack read, and only while every recorded archive is on disk as
    written. Plugins and `LOOSE_ONLY_DIRS` stay loose.
    See: docs/commentary/asset_convert_bsa.md#archive-only-output
    """
    manifest = _load_manifest(plugin_dir)
    if not manifest['archives'] or len(_trusted(plugin_dir, manifest)) != len(manifest['archives']):
        return 0, 0
    gone = set(manifest.get('gone', ()))
    doomed = [(path, sig[0]) for path, sig in manifest['files'].items() if path not in gone
              and _stamp(Path(long_path(plugin_dir.joinpath(*path.split('\\'))))) == sig]
    gone.update(path for path, _size in doomed)
    doomed += [(rel, entry.stat().st_size) for rel, entry in _entries(plugin_dir, packed_dirs(plugin_dir))
               if _unshipped(rel, entry)]
    emptied = set()
    for path, _size in doomed:
        loose = plugin_dir.joinpath(*path.split('\\'))
        os.remove(long_path(loose))
        emptied.add(loose.parent)
    count, freed = len(doomed), sum(size for _path, size in doomed)
    _remove_empty(plugin_dir, emptied)
    manifest['gone'] = sorted(gone)
    _write_manifest(plugin_dir, manifest)
    return count, freed


def _remove_empty(plugin_dir: Path, folders: set) -> None:
    """Remove each folder in `folders` and its parents below `plugin_dir` once empty, deepest first."""
    chain = {p for f in folders for p in [f, *f.parents] if plugin_dir in p.parents}
    for folder in sorted(chain, key=lambda p: len(p.parts), reverse=True):
        try:
            os.rmdir(long_path(folder))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Pack output assets into Skyrim SE BSA archives')
    parser.add_argument('source_file', help='Plugin filename (e.g. Oblivion.esm)')
    parser.add_argument('--output-dir', default='output', help='Root output directory (default: output)')
    parser.add_argument('--size-limit', type=int, default=BSA_SIZE_LIMIT, metavar='BYTES',
                        help=f'Max bytes per archive (default: {BSA_SIZE_LIMIT})')
    parser.add_argument('--export-root', default=None, metavar='DIR',
                        help='The export ROOT (default: the repo export/). Resolves which output '
                             'folder the plugin converts into for an imported mod.')
    a = parser.parse_args()
    r = pack_bsas(a.source_file, output_dir=a.output_dir, size_limit=a.size_limit,
                  export_root=a.export_root)
    print(f"\nPacked: {len(r['packed'])}  Kept: {len(r['kept'])}  Skipped: {len(r['skipped'])}  "
          f"Errors: {len(r['errors'])}")


