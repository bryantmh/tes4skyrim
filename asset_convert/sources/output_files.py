"""Read converted output whether a file is loose or stored in its folder's archives.

A packed output folder (one holding `bsa_pack`'s manifest) may keep a file only
inside one of its archives (`<stem>.bsa`, `<stem> - Part N.bsa`). Every
call takes an absolute path inside an output folder and looks loose first, then
in that folder's archives, ignoring case. Each archive's directory is read once
per process; `forget` drops a folder after its archives change.
See: docs/commentary/asset_convert_bsa.md#archive-only-output
"""
import io
import json
import os
import shutil
import struct
import zlib
from collections import namedtuple
from pathlib import Path

import lz4.frame

from asset_convert.sources import bsa_extract

# ---------------------------------------------------------------------------
# Names shared with the packer
# ---------------------------------------------------------------------------

#: Per-folder record of what the archives hold; its presence marks a packed folder.
MANIFEST_NAME = '.bsa_pack_manifest.json'
#: Archive paths a step removed since the last pack, one per line; the next pack drops them.
DISCARDED_NAME = '.bsa_pack_discarded.txt'

#: Archive flag (v104+): each payload starts with its own path.
_EMBED_NAMES = 0x0100

#: One stored file: the archive, where its payload starts, its stored length, how it is stored.
Member = namedtuple('Member', 'bsa offset size compressed embedded version')


def rel_key(rel) -> str:
    """An archive path as stored: lower case, backslash-separated, no leading slash."""
    return str(rel).replace('/', '\\').strip('\\').lower()


# ---------------------------------------------------------------------------
# One packed folder
# ---------------------------------------------------------------------------

class _Folder:
    """A packed output folder: its archives' members and the manifest's file signatures."""

    def __init__(self, root: str):
        """Read every archive's directory at `root`, less what `discard` marked, and the manifest's signatures."""
        self.root = root
        self.members = {}
        for name in sorted(os.listdir(root)):
            if name.lower().endswith('.bsa'):
                self.members.update(_read_members(os.path.join(root, name)))
        for rel in discarded(root):
            self.members.pop(rel, None)
        try:
            with open(os.path.join(root, MANIFEST_NAME), encoding='utf-8') as fh:
                self.sigs = json.load(fh).get('files', {})
        except (OSError, ValueError):
            self.sigs = {}
        self.dirs = {k.rpartition('\\')[0] for k in self.members}
        self.dirs |= {d.rsplit('\\', i)[0] for d in self.dirs for i in range(1, d.count('\\') + 1)}


def _read_members(bsa: str) -> dict:
    """{archive path: Member} for one archive; an unreadable one holds nothing."""
    try:
        with open(bsa, 'rb') as fh:
            version, flags, entries = bsa_extract.read_bsa_directory(fh)
    except (OSError, ValueError, struct.error, IndexError):
        return {}
    embedded = bool(version >= 104 and flags & _EMBED_NAMES)
    return {path.lower(): Member(bsa, offset, size, compressed, embedded, version)
            for path, size, offset, compressed in entries}


#: {normcased directory: _Folder, or None when it is not a packed folder}.
_FOLDERS = {}


def _norm(path) -> str:
    """`path` absolute and normcased, the cache key of a folder."""
    return os.path.normcase(os.path.abspath(str(path)))


def _folder(root: str):
    """The cached `_Folder` at `root`, or None when it holds no manifest."""
    key = _norm(root)
    if key not in _FOLDERS:
        packed = os.path.isfile(os.path.join(root, MANIFEST_NAME))
        _FOLDERS[key] = _Folder(os.path.abspath(root)) if packed else None
    return _FOLDERS[key]


def _locate(path):
    """(packed folder holding `path`, its archive path), or (None, None)."""
    path = os.path.abspath(str(path))
    cur = os.path.dirname(path)
    while True:
        folder = _folder(cur)
        if folder is not None:
            return folder, rel_key(os.path.relpath(path, cur))
        parent = os.path.dirname(cur)
        if parent == cur:
            return None, None
        cur = parent


def forget(root=None) -> None:
    """Drop the cached archives of the folder at `root` (every folder when None)."""
    if root is None:
        _FOLDERS.clear()
    else:
        _FOLDERS.pop(_norm(root), None)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def read_member(m: Member) -> bytes:
    """The original bytes of one stored file."""
    with open(m.bsa, 'rb') as fh:
        fh.seek(m.offset)
        size = m.size
        if m.embedded:
            skip = fh.read(1)[0]
            fh.seek(skip, 1)
            size -= 1 + skip
        data = fh.read(size)
    if not m.compressed:
        return data
    return lz4.frame.decompress(data[4:]) if m.version >= 105 else zlib.decompress(data[4:])


def member(path):
    """The archive Member storing `path`, or None (a loose copy is not consulted)."""
    folder, rel = _locate(path)
    return folder.members.get(rel) if folder is not None else None


def source(path):
    """`path` as a str when loose, its Member when only archived, else None; both picklable."""
    if os.path.isfile(path):
        return str(path)
    return member(path)


def read_source(src) -> bytes:
    """The bytes of a `source` result."""
    if isinstance(src, Member):
        return read_member(src)
    with open(src, 'rb') as fh:
        return fh.read()


def exists(path) -> bool:
    """Whether `path` is a loose file or stored in its folder's archives."""
    return os.path.isfile(path) or member(path) is not None


def is_dir(path) -> bool:
    """Whether `path` is a loose folder or a folder inside its folder's archives."""
    if os.path.isdir(path):
        return True
    folder, rel = _locate(os.path.join(str(path), '_'))
    return folder is not None and rel.rpartition('\\')[0] in folder.dirs


def read_bytes(path) -> bytes:
    """The bytes of `path`, loose or archived; FileNotFoundError when neither."""
    src = source(path)
    if src is None:
        raise FileNotFoundError(str(path))
    return read_source(src)


def open_binary(path):
    """A readable binary file object for `path`, loose or archived."""
    if os.path.isfile(path):
        return open(path, 'rb')
    return io.BytesIO(read_bytes(path))


def signature(path):
    """[size, mtime_ns] of `path`: the loose file's, else the one the last pack recorded; None if absent."""
    if os.path.isfile(path):
        st = os.stat(path)
        return [st.st_size, st.st_mtime_ns]
    folder, rel = _locate(path)
    if folder is None or rel not in folder.members:
        return None
    return folder.sigs.get(rel)


def copy_to(path, dest) -> None:
    """Copy `path` (loose or archived) to the real file `dest`, creating its folder."""
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    if os.path.isfile(path):
        shutil.copy2(path, dest)
        return
    with open(dest, 'wb') as fh:
        fh.write(read_bytes(path))


def mirror_archived(directory, dest, suffix: str = '') -> int:
    """Make `dest` hold exactly the files under `directory` that only its archives store; returns how many.

    Each copy keeps the size and mtime the pack recorded, so one already
    matching is not rewritten; anything else in `dest` is removed.
    """
    dest = Path(dest)
    wanted = _archived_only(directory, suffix)
    if dest.is_dir():
        for old in [p for p in dest.rglob('*') if p.is_file()]:
            if rel_key(old.relative_to(dest)) not in wanted:
                old.unlink()
    for rel, m in wanted.items():
        _restore(os.path.join(str(directory), rel), m, dest.joinpath(*rel.split('\\')))
    return len(wanted)


def unpack(directory, suffix: str = '') -> int:
    """Write every file under `directory` that only its archives store back to its loose path; returns how many.

    Each keeps the size and mtime the pack recorded, so the next pack reuses it
    unchanged and drops the loose copy again. For tools that read a folder loose.
    """
    wanted = _archived_only(directory, suffix)
    for rel, m in wanted.items():
        path = os.path.join(str(directory), *rel.split('\\'))
        _restore(path, m, path)
    return len(wanted)


def _archived_only(directory, suffix: str) -> dict:
    """{path relative to `directory`: Member} for each file under it with no loose copy."""
    return {rel: m for rel, m in files_under(directory, suffix).items() if isinstance(m, Member)}


def _restore(path, m: Member, dest) -> None:
    """Write archived `path` to `dest` with the pack's recorded size and mtime, unless `dest` already matches."""
    sig = signature(path) or [m.size, 0]
    if os.path.isfile(dest) and [os.stat(dest).st_size, os.stat(dest).st_mtime_ns] == sig:
        return
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    with open(dest, 'wb') as fh:
        fh.write(read_member(m))
    os.utime(dest, ns=(sig[1], sig[1]))


# ---------------------------------------------------------------------------
# Listing and removing
# ---------------------------------------------------------------------------

def files_under(directory, suffix: str = '') -> dict:
    """{path relative to `directory`, lowercase with backslashes: `source`} for every file under it.

    Loose files win over archived ones; `suffix` filters by lowercase ending.
    """
    out = {}
    folder, rel = _locate(os.path.join(str(directory), '_'))
    if folder is not None:
        prefix = rel.rpartition('\\')[0]
        lead = prefix + '\\' if prefix else ''
        for key, m in folder.members.items():
            if key.startswith(lead) and key.endswith(suffix):
                out[key[len(lead):]] = m
    base = str(directory)
    for root, _dirs, files in os.walk(base):
        for name in files:
            full = os.path.join(root, name)
            key = rel_key(os.path.relpath(full, base))
            if key.endswith(suffix):
                out[key] = full
    return out


def discard(path) -> bool:
    """Remove `path` loose and, when archived, mark it for the next pack to drop; True if anything went."""
    gone = os.path.isfile(path)
    if gone:
        os.remove(path)
    return discard_archived([path]) > 0 or gone


def discard_archived(paths) -> int:
    """Mark each path's archived copy for the next pack to drop, leaving loose copies alone; returns how many."""
    marks = {}
    for path in paths:
        folder, rel = _locate(path)
        if folder is not None and rel in folder.members:
            del folder.members[rel]
            marks.setdefault(folder.root, []).append(rel)
    for root, rels in marks.items():
        with open(os.path.join(root, DISCARDED_NAME), 'a', encoding='utf-8') as fh:
            fh.write(''.join(rel + '\n' for rel in rels))
    return sum(len(rels) for rels in marks.values())


def discarded(root) -> set:
    """The archive paths `discard` marked in the folder at `root`."""
    try:
        with open(os.path.join(str(root), DISCARDED_NAME), encoding='utf-8') as fh:
            return {line.strip() for line in fh if line.strip()}
    except OSError:
        return set()
