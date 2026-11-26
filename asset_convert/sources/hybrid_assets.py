"""
The Morroblivion files hybrid-mode plugins borrow, and the one shared mod that ships them.

A hybrid export never copies an asset: it lists each Morroblivion file it uses
beside its records. Each hybrid plugin's Meshes step converts its list into
`Morroblivion Hybrid Assets`, under its own namespace, and every record and
mesh naming a borrowed file points there.

See: docs/commentary/tes4_export_morrowind.md#hybrid-shared-assets
"""

import json
import os
from pathlib import Path

from asset_convert.sources.morrowind_assets import CACHE_DIR
from core.plugin_masters import export_root, master_dir, master_chain
from output_layout import assets_for, record_dir as record_dir_of

#: The borrowed files of a hybrid export, `<rel>\t<owner plugin>` per line, in its record dir.
MANIFEST = 'morroblivion_hybrid_assets.txt'

#: The two-handed items a hybrid export splits into halves, in its record dir.
SPLITS = 'morroblivion_hybrid_splits.json'

#: The shared mod's folder in the output root; it ships loose, with no plugin.
MOD_NAME = 'Morroblivion Hybrid Assets'

#: Morroblivion LOD meshes a kept vanilla mesh takes, `<plugin rel>\t<owner>\t<owner rel>` per line.
LODS = 'morroblivion_hybrid_lods.txt'

#: The owner a split half lists: no tree holds it, the shared mod generates it.
SPLIT_OWNER = '*split'

#: The asset namespace every borrowed file ships under.
NAMESPACE = 'morroblivion'

#: The image extensions a borrowed texture may be named with; it ships as .dds.
_IMAGE_EXTS = ('.tga', '.bmp', '.dds')


def write_borrowed(record_dir, rows: dict, splits: list, lods=None) -> None:
    """Write {rel under the owner's asset root: owner plugin}, the split items and the kept meshes' LODs.

    `lods` is {plugin meshes rel: (owner plugin, owner meshes rel)}.
    """
    folder = Path(record_dir)
    folder.mkdir(parents=True, exist_ok=True)
    text = ''.join(f'{rel}\t{owner}\n' for rel, owner in sorted(rows.items()))
    (folder / MANIFEST).write_text(text, encoding='utf-8')
    for name in (SPLITS, LODS):
        (folder / name).unlink(missing_ok=True)
    if splits:
        (folder / SPLITS).write_text(json.dumps(splits, indent=1, sort_keys=True), encoding='utf-8')
    if lods:
        text = ''.join(f'{rel}\t{owner}\t{src}\n' for rel, (owner, src) in sorted(lods.items()))
        (folder / LODS).write_text(text, encoding='utf-8')


def read_lods(record_dir) -> dict:
    """{plugin meshes rel: (owner plugin, owner meshes rel)} of the Morroblivion LODs a hybrid export takes."""
    path = Path(record_dir) / LODS
    rows = path.read_text(encoding='utf-8').splitlines() if path.is_file() else ()
    return {r[0]: (r[1], r[2]) for r in (line.split('\t') for line in rows) if len(r) == 3}


def read_borrowed(record_dir) -> dict:
    """{rel: owner plugin} a hybrid export borrows; {} when it borrows nothing (an old copy list reads empty)."""
    path = Path(record_dir) / MANIFEST
    rows = path.read_text(encoding='utf-8').splitlines() if path.is_file() else ()
    return dict(line.split('\t', 1) for line in rows if '\t' in line)


def read_splits(record_dir) -> list:
    """The two-handed items a hybrid export splits: [{'models': {export key: path}, 'owners': [plugin]}]."""
    path = Path(record_dir) / SPLITS
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else []


def is_hybrid(record_dir) -> bool:
    """Whether `record_dir` was exported in hybrid mode (it writes a borrowed list, even an empty one)."""
    return (Path(record_dir) / MANIFEST).is_file()


def chain_dirs(record_dir) -> list:
    """The record dirs of `record_dir`'s master chain that were exported in hybrid mode, then its own."""
    dirs = [master_dir(record_dir, n) for n in master_chain(record_dir)] + [str(record_dir)]
    return [d for d in dirs if is_hybrid(d)]


def borrowed_keys(record_dir) -> frozenset:
    """Every path the chain borrows, as `asset_key` gives it."""
    return frozenset(asset_key(rel.split('/', 1)[1]) for d in chain_dirs(record_dir)
                     for rel in read_borrowed(d) if '/' in rel)


def asset_key(rel: str) -> str:
    """A meshes- or textures-relative path as one key: lower case, backslashes, images as .dds."""
    key = rel.replace('/', chr(92)).strip(chr(92)).lower()
    stem, ext = os.path.splitext(key)
    return stem + '.dds' if ext in _IMAGE_EXTS else key


def cache_dir(record_dir) -> Path:
    """The shared mod's caches (bounds, collision, scan fragments, generated split halves)."""
    return Path(export_root(record_dir)) / CACHE_DIR / 'hybrid'


def cache_chain(record_dir, filename: str) -> list:
    """The shared mod's copy of cache `filename` when `record_dir`'s chain is hybrid, else []."""
    return [str(cache_dir(record_dir) / filename)] if chain_dirs(record_dir) else []


def owner_dirs(record_dir) -> list:
    """The record dirs of every owner the chain borrows from."""
    root = export_root(record_dir)
    owners = sorted({o for d in chain_dirs(record_dir) for o in read_borrowed(d).values()} - {SPLIT_OWNER})
    return [record_dir_of(root, o) for o in owners]


def owner_roots(record_dir, part: str) -> list:
    """`part` ('meshes' or 'textures') of every owner the chain borrows from, for lookups."""
    return [assets_for(d) / part for d in owner_dirs(record_dir)]
