"""
Morrowind BSA reading.

The TES3 archive predates every later BSA and shares nothing with them: the
magic is 0x00000100 rather than "BSA\\0", there are no folder records (each
entry carries one flat path), and nothing is ever compressed.

    header (12):  version(4) hashTableOffset(4) fileCount(4)
    sizes/offsets (fileCount x 8):  size(4) offset(4)
    name offsets  (fileCount x 4):  offset into the name block
    name block:   null-terminated paths
    hash table    (fileCount x 8)
    file data

Offsets in the size/offset table are relative to the end of the hash table.

See: docs/commentary/tes4_export_morrowind.md#tes3-bsa
"""

import shutil
import struct
from pathlib import Path

from .morrowind_sound_scope import normalize

#: Version field standing where later archives put the "BSA\0" magic.
TES3_BSA_MAGIC = 0x00000100

_HEADER_SIZE = 12

#: Loose sound subfolder holding Morrowind's voice acting, which is not converted.
_VOICE_DIR = 'vo'


def copy_loose_sounds(data_dir, asset_dir, owned) -> int:
    """Copy the sounds in `owned` from `<data_dir>/Sound`; how many were new.

    Morrowind ships its sounds loose rather than in the archive, in a Data
    folder its expansions and every installed mod share, so `owned` -- the
    files this plugin names that no master does -- decides what comes across.
    Files already present are left alone, and the voice folder is skipped.
    See: docs/commentary/tes4_export_morrowind.md#which-sounds-a-plugin-ships
    """
    source = Path(data_dir) / 'Sound'
    if not source.is_dir() or not owned:
        return 0
    copied = 0
    for path in source.rglob('*'):
        relative = path.relative_to(source)
        if not path.is_file() or relative.parts[0].lower() == _VOICE_DIR:
            continue
        if normalize(str(relative)) not in owned:
            continue
        dest = Path(asset_dir) / 'sound' / relative
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        copied += 1
    return copied


def is_morrowind_bsa(bsa_path) -> bool:
    """True when this archive is Morrowind's format rather than a later one."""
    try:
        with open(bsa_path, 'rb') as fh:
            head = fh.read(4)
    except OSError:
        return False
    return (len(head) == 4
            and struct.unpack('<I', head)[0] == TES3_BSA_MAGIC)


def _tables(head: bytes, bsa_path) -> list:
    """[(stored path, absolute start, size)] from the archive's leading bytes."""
    version, hash_offset, count = struct.unpack_from('<III', head, 0)
    if version != TES3_BSA_MAGIC:
        raise ValueError(f'Not a Morrowind BSA: {bsa_path}')
    names = _read_names(head, count)
    data_start = _HEADER_SIZE + hash_offset + count * 8
    out = []
    for index, name in enumerate(names):
        size, offset = struct.unpack_from('<II', head, _HEADER_SIZE + index * 8)
        out.append((name, data_start + offset, size))
    return out


def read_index(bsa_path) -> dict:
    """{lower-case stored path: (absolute start, size)} without reading the data."""
    with open(bsa_path, 'rb') as fh:
        head = fh.read(_HEADER_SIZE)
        hash_offset = struct.unpack_from('<I', head, 4)[0]
        head += fh.read(hash_offset)
    return {name.lower(): (start, size)
            for name, start, size in _tables(head, bsa_path)}


def read_entry(bsa_path, start: int, size: int) -> bytes:
    """One file's bytes, located by `read_index`."""
    with open(bsa_path, 'rb') as fh:
        fh.seek(start)
        return fh.read(size)


def iter_bsa(bsa_path):
    """Yield (filepath_str, data_bytes) for every file, as the Oblivion reader does."""
    data = Path(bsa_path).read_bytes()
    for name, start, size in _tables(data, bsa_path):
        yield name, data[start:start + size]


def _read_names(data: bytes, count: int) -> list:
    """Every stored path, in the order the size/offset table uses."""
    name_offsets_at = _HEADER_SIZE + count * 8
    block_at = name_offsets_at + count * 4
    names = []
    for index in range(count):
        offset = struct.unpack_from('<I', data, name_offsets_at + index * 4)[0]
        start = block_at + offset
        end = data.index(b'\x00', start)
        names.append(data[start:end].decode('cp1252', errors='replace'))
    return names
