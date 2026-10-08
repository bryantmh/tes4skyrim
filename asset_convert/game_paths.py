"""Resolving Bethesda-format relative paths against a real filesystem root.

Model, texture and BSA-internal paths that come out of the game's own binary
formats (MODL/ICON/TX00 subrecords, NIF texture strings, BSA folder+file
records) are ALWAYS backslash-separated, because that is the format's own
convention -- it has nothing to do with the OS the converter runs on.

`root / rel` (pathlib) and `os.path.join(root, rel)` only split on the HOST's
separator.  On Windows that happens to be a backslash, so those work by
coincidence; on Linux/Mac the whole `rel` survives as ONE filename with literal
embedded backslashes, and every lookup silently misses or every write lands in a
single flat file.  `win_join` splits explicitly instead, so a multi-segment
relative path becomes real nested directories on any platform.

This lives in its own module rather than in `lod_gen` so that the terrain-LOD,
grass and _far.nif code can share one implementation without importing a heavy
sibling module for a three-line path helper.
"""
import os
from pathlib import Path

from core.plugin_masters import master_dir, masters_from_export_header

__all__ = ["win_join", "find_nocase", "folder_names", "DEFAULT_NAMESPACE",
           "namespace_for", "set_namespace", "current_namespace",
           "owns_namespace"]

#: Namespace for Oblivion and everything mastered on it, and the fallback.
DEFAULT_NAMESPACE = 'tes4'

#: Masterless plugins that are a generated COMPANION to a family, not a game root.
COMPANION_ROOTS = ('morrowind-morroblivion-compatibility',)

#: Carries the namespace into spawned workers and child processes.
NAMESPACE_ENV = 'TESCONV_ASSET_NAMESPACE'

_ACTIVE = {'ns': (os.environ.get(NAMESPACE_ENV) or DEFAULT_NAMESPACE).lower()}


def _chain_root(export_dir: Path) -> Path:
    """The masterless plugin at the root of this plugin's master chain.

    See: docs/reference/pipeline.md#master-resolution
    """
    seen = set()
    cur = export_dir
    while cur is not None and cur.name.lower() not in seen:
        seen.add(cur.name.lower())
        masters = masters_from_export_header(str(cur))
        if not masters:
            break
        dirs = [Path(master_dir(cur, m)) for m in masters]
        nxt = next((d for d in dirs
                    if d.is_dir() and d.name.lower() not in seen), None)
        if nxt is None:
            return dirs[0]
        cur = nxt
    return cur


def namespace_for(export_dir) -> str:
    """The asset namespace a plugin's converted files belong under.

    Named after the masterless plugin rooting the master chain, so one game's
    family shares a namespace and unrelated games cannot collide. Oblivion
    keeps `tes4` so its existing output stays valid, and a `COMPANION_ROOTS`
    plugin joins that family rather than claiming one of its own.
    See: docs/commentary/asset_convert_texture.md#per-game-asset-namespace
    """
    cur = _chain_root(Path(export_dir))
    stem = Path(cur.name).stem.lower() if cur is not None else ''
    if not stem or stem.startswith('oblivion') or stem in COMPANION_ROOTS:
        return DEFAULT_NAMESPACE
    return ''.join(c for c in stem if c.isalnum()) or DEFAULT_NAMESPACE


def owns_namespace(export_dir) -> bool:
    """True when this plugin roots the master chain its namespace is named for.

    See: docs/commentary/asset_convert_texture.md#per-game-asset-namespace
    """
    export_dir = Path(export_dir)
    root = _chain_root(export_dir)
    if root is not None and root.name.lower() != export_dir.name.lower():
        return False
    stem = Path(export_dir.name).stem.lower()
    return namespace_for(export_dir) != DEFAULT_NAMESPACE \
        or stem.startswith('oblivion')


def set_namespace(ns: str) -> None:
    """Make `ns` active here and in every process spawned from here on.

    See: docs/commentary/asset_convert_texture.md#namespace-crosses-process-boundaries
    """
    _ACTIVE['ns'] = (ns or DEFAULT_NAMESPACE).lower()
    os.environ[NAMESPACE_ENV] = _ACTIVE['ns']


def current_namespace() -> str:
    """The namespace in force for the plugin being converted."""
    return _ACTIVE['ns']


def win_join(root, rel: str) -> Path:
    """Join a backslash-form (game-format) relative path onto `root`.

    Accepts either separator in `rel` and treats both as path separators, which
    is what the game's own loaders do.  Empty segments (a leading separator, or
    a doubled one) are dropped, so `rel` can never escape `root` the way
    `Path(root) / '\\abs.nif'` would.
    """
    parts = _segments(rel)
    return Path(root).joinpath(*parts)


def _segments(rel) -> list:
    """`rel` split on either separator, empty segments dropped."""
    return [p for p in str(rel).replace('/', '\\').split('\\') if p]


def folder_names(folder) -> dict:
    """`{lowercase name: [names]}` for `folder`'s entries; empty when unreadable."""
    found = {}
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return found
    for name in names:
        found.setdefault(name.lower(), []).append(name)
    return found


def find_nocase(root, *parts, kind=os.path.isfile, listing=folder_names):
    """The file `win_join(root, parts)` names, matched ignoring case, or None.

    The exact join is probed first and returned as is: an exact hit lists
    nothing, so where the filesystem ignores case a file that exists is found
    as it always was. After a miss each segment is matched ignoring case,
    through every folder that spells it, one listing per folder. `kind` is the
    probe: `os.path.isdir` finds a folder. A `listing` that remembers must
    forget a folder the run writes into.
    See: docs/commentary/asset_convert_texture.md#case-blind-lookups
    """
    segs = _segments('\\'.join(parts))
    exact = Path(root).joinpath(*segs)
    if kind(exact):
        return exact
    found = [str(Path(root))]
    for seg in segs:
        found = [os.path.join(folder, name) for folder in found
                 for name in listing(folder).get(seg.lower(), ())]
    return next((Path(p) for p in found if kind(p)), None)
