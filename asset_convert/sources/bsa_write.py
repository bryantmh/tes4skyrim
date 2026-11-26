"""Write Skyrim SE (v105) BSA archives.

`stage` turns each file into its stored payload (an LZ4 frame for the kinds
vanilla compresses, the raw bytes otherwise), reusing an older archive's stored
bytes for an unchanged file; `split` bins payloads under an exact archive-size
limit; `write_bsa` lays out one archive in the game's hash order.
See: docs/commentary/asset_convert_bsa.md#bsa-writer
"""
import os
import struct
import sys
from collections import deque
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import lz4.frame

from asset_convert.sources.bsa_extract import read_bsa_directory, tes4_hash
from core.worker_budget import worker_count

# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------

VERSION = 105
HEADER_SIZE = 36
FOLDER_RECORD = 24
FILE_RECORD = 16

ARCH_DIR_NAMES = 0x0001
ARCH_FILE_NAMES = 0x0002
ARCH_COMPRESSED = 0x0004
ARCH_RETAIN_FILE_NAMES = 0x0010
ARCH_STARTUP_STRINGS = 0x0080
ARCH_EMBED_NAMES = 0x0100

FILE_MESHES = 0x01
FILE_TEXTURES = 0x02
FILE_SOUNDS = 0x08
FILE_VOICES = 0x10
FILE_TREES = 0x40

#: Content-type flag per top folder (`sound\voice\` is FILE_VOICES, checked first).
ROOT_FLAGS = {'meshes': FILE_MESHES, 'lodsettings': FILE_MESHES, 'textures': FILE_TEXTURES,
              'sound': FILE_SOUNDS, 'trees': FILE_TREES}

#: File-size bit that inverts the archive's compression default for one file.
SIZE_TOGGLE = 0x40000000

#: Top folders whose files vanilla stores LZ4-compressed; every other folder is stored raw.
COMPRESSED_ROOTS = frozenset(('meshes', 'textures', 'lodsettings'))

#: LZ4 HC level used for every compressed payload. See: docs/commentary/asset_convert_bsa.md#bsa-writer
LZ4_LEVEL = 3

#: Most raw bytes per compression task; less work than this is compressed without a pool.
BATCH_BYTES = 32 * 1024 * 1024
#: Fewest raw bytes per compression task.
MIN_BATCH_BYTES = 1024 * 1024

#: How many files the raw-source reader threads may run ahead of the archive write.
READ_AHEAD = 256

#: Copy granularity when moving payloads into an archive.
COPY_PIECE = 8 * 1024 * 1024


def long_path(path) -> str:
    """Render an absolute Windows path with the `\\\\?\\` prefix so it opens past MAX_PATH.

    See: docs/commentary/asset_convert_bsa.md#staging-past-the-path-limit
    """
    s = str(path)
    if sys.platform != 'win32' or s.startswith('\\\\?\\') or not os.path.isabs(s):
        return s
    return '\\\\?\\' + os.path.normpath(s)


def archive_path(rel) -> str:
    """An archive path as stored: lower case, backslash-separated."""
    return str(rel).replace('/', '\\').lower()


def compresses(path: str) -> bool:
    """Whether vanilla stores files under this archive path's top folder compressed."""
    return path.split('\\', 1)[0] in COMPRESSED_ROOTS


def _kind(path: str) -> int:
    """The content-type flag of one archive path."""
    if path.startswith('sound\\voice\\'):
        return FILE_VOICES
    return ROOT_FLAGS.get(path.split('\\', 1)[0], 0)


def file_flags(paths) -> int:
    """The header's content-type flags for these archive paths (xEdit `DetectFlags`, SSE subset)."""
    flags = 0
    for path in paths:
        flags |= _kind(path)
    return flags


def archive_flags(paths, any_compressed: bool) -> int:
    """Archive flags for these paths, matching vanilla's per content kind.

    See: docs/commentary/asset_convert_bsa.md#bsa-writer
    """
    kinds = file_flags(paths)
    flags = ARCH_DIR_NAMES | ARCH_FILE_NAMES
    if any_compressed:
        flags |= ARCH_COMPRESSED
    if kinds & FILE_MESHES:
        flags |= ARCH_STARTUP_STRINGS
    if kinds & FILE_SOUNDS or any(p.startswith('scripts\\') for p in paths):
        flags |= ARCH_RETAIN_FILE_NAMES
    if kinds == FILE_TEXTURES:
        flags |= ARCH_EMBED_NAMES
    return flags


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------

def compress_bytes(raw: bytes) -> bytes:
    """One stored compressed payload: the u32 original size, then an LZ4 frame shaped like vanilla's."""
    frame = lz4.frame.compress(raw, compression_level=LZ4_LEVEL, block_size=lz4.frame.BLOCKSIZE_MAX4MB,
                               block_linked=False, store_size=False)
    return struct.pack('<I', len(raw)) + frame


def compress_batch(sources: list, chunk: str) -> list:
    """Compress each source file into the `chunk` file; returns each payload's (offset, length)."""
    spans = []
    with open(long_path(chunk), 'wb') as out:
        for src in sources:
            with open(long_path(src), 'rb') as fh:
                blob = compress_bytes(fh.read())
            spans.append((out.tell(), len(blob)))
            out.write(blob)
    return spans


def stored_index(archives) -> dict:
    """{archive path: ((bsa file, offset, length), compressed)} of every payload in `archives`.

    The span excludes an embedded name, so a payload moves between archives
    with and without names. A path in two archives keeps the later one; an
    archive that is not a readable BSA adds nothing.
    """
    index = {}
    for bsa in archives:
        with open(long_path(bsa), 'rb') as fh:
            try:
                _version, flags, entries = read_bsa_directory(fh)
            except (ValueError, struct.error, IndexError):
                continue
            for path, size, offset, compressed in entries:
                if flags & ARCH_EMBED_NAMES:
                    fh.seek(offset)
                    skip = 1 + fh.read(1)[0]
                    offset, size = offset + skip, size - skip
                index[path.lower()] = ((str(bsa), offset, size), compressed)
    return index


def reuse_index(archives) -> dict:
    """{archive path: (bsa file, offset, length)} of every compressed payload in `archives`."""
    return {path: span for path, (span, compressed) in stored_index(archives).items() if compressed}


def _batches(todo: list, size: int) -> list:
    """Group (path, src, size) into runs of about `size` raw bytes."""
    batches, cur, total = [], [], 0
    for item in todo:
        cur.append(item)
        total += item[2]
        if total >= size:
            batches.append(cur)
            cur, total = [], 0
    return batches + ([cur] if cur else [])


def _compress_all(todo: list, work_dir: str) -> dict:
    """{path: (chunk, offset, length)} for each (path, src, size), in a pool when the work is large.

    Batches shrink with the work so every worker gets about four.
    """
    workers = worker_count()
    total = sum(item[2] for item in todo)
    batches = _batches(todo, max(MIN_BATCH_BYTES, min(BATCH_BYTES, total // (workers * 4))))
    first = len(os.listdir(work_dir))
    chunks = [os.path.join(work_dir, f'{first + i}.bin') for i in range(len(batches))]
    jobs = [[src for _p, src, _s in b] for b in batches]
    if total > BATCH_BYTES:
        with ProcessPoolExecutor(max_workers=min(workers, len(batches))) as pool:
            results = list(pool.map(compress_batch, jobs, chunks))
    else:
        results = [compress_batch(job, chunk) for job, chunk in zip(jobs, chunks)]
    stored = {}
    for batch, chunk, spans in zip(batches, chunks, results):
        for (path, _src, _size), (offset, length) in zip(batch, spans):
            stored[path] = (chunk, offset, length)
    return stored


def stage(files: list, reusable: dict, work_dir: str) -> list:
    """(path, (file, offset, length), compressed) per (path, src, size), sorted by path.

    `files` hold lowercase archive paths. A compressed file whose path is in
    `reusable` takes that span; other compressed files are compressed into
    chunk files under `work_dir`; raw files point at their source, which is a
    whole file or, for a file only an old archive holds, a (file, offset,
    length) span.
    """
    todo = [f for f in files if compresses(f[0]) and f[0] not in reusable]
    fresh = _compress_all(todo, work_dir) if todo else {}
    out = []
    for path, src, size in files:
        if not compresses(path):
            out.append((path, src if isinstance(src, tuple) else (str(src), 0, size), False))
        else:
            out.append((path, reusable.get(path) or fresh[path], True))
    out.sort(key=lambda e: e[0])
    return out


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _cost(path: str, length: int, new_folder: bool) -> int:
    """Bytes one file adds to an archive, counting its folder's record when `new_folder`.

    A texture counts its embedded name, which it carries in an all-texture archive.
    """
    folder, _, name = path.rpartition('\\')
    embed = len(path) + 1 if _kind(path) == FILE_TEXTURES else 0
    cost = FILE_RECORD + len(name) + 1 + length + embed
    return cost + (FOLDER_RECORD + len(folder) + 2 if new_folder else 0)


def split(items: list, limit: int) -> list:
    """Bin staged items, in path order, so each archive's exact size stays within `limit`.

    A file too big for any archive gets one of its own.
    """
    bins, cur, folders, size = [], [], set(), HEADER_SIZE
    for item in items:
        folder = item[0].rpartition('\\')[0]
        cost = _cost(item[0], item[1][2], folder not in folders)
        if cur and size + cost > limit:
            bins.append(cur)
            cur, folders, size = [], set(), HEADER_SIZE
            cost = _cost(item[0], item[1][2], True)
        cur.append(item)
        folders.add(folder)
        size += cost
    return bins + ([cur] if cur else [])


def _encode(text: str) -> bytes:
    """A name as the archive stores it (one byte per character)."""
    return text.encode('latin-1')


def _folders(items: list) -> list:
    """[(folder hash, folder, [(file hash, name, item)])] in the game's hash order."""
    groups = {}
    for item in items:
        folder, _, name = item[0].rpartition('\\')
        groups.setdefault(folder, []).append((tes4_hash(name), name, item))
    out = []
    for folder, files in groups.items():
        files.sort(key=lambda f: f[0])
        if len({f[0] for f in files}) != len(files):
            raise ValueError(f'two file names in {folder!r} share a BSA hash')
        out.append((tes4_hash(folder, folder=True), folder, files))
    out.sort(key=lambda f: f[0])
    if len({f[0] for f in out}) != len(out):
        raise ValueError('two folder names share a BSA hash')
    return out


def _directory(folders: list, flags: int) -> tuple:
    """(directory bytes, staged items in data order) for an archive's sorted folders."""
    embed = bool(flags & ARCH_EMBED_NAMES)
    names_len = sum(len(name) + 1 for _h, _f, files in folders for _fh, name, _i in files)
    blocks_at = HEADER_SIZE + FOLDER_RECORD * len(folders)
    data_at = blocks_at + names_len + sum(len(f) + 2 + FILE_RECORD * len(files) for _h, f, files in folders)
    records, blocks, order = bytearray(), bytearray(), []
    for fhash, folder, files in folders:
        records += struct.pack('<QIIQ', fhash, len(files), 0, blocks_at + len(blocks) + names_len)
        blocks += bytes([len(folder) + 1]) + _encode(folder) + b'\0'
        for hash_, _name, item in files:
            size = item[1][2] + ((len(item[0]) + 1) if embed else 0)
            field = size | (SIZE_TOGGLE if item[2] != bool(flags & ARCH_COMPRESSED) else 0)
            blocks += struct.pack('<QII', hash_, field, data_at)
            order.append(item)
            data_at += size
    names = b''.join(_encode(name) + b'\0' for _h, _f, files in folders for _fh, name, _i in files)
    return bytes(records + blocks + names), order


def write_bsa(dest, items: list) -> None:
    """Write staged items as one v105 archive at `dest`.

    Folders and files are ordered by their TES4 hash, as the game's binary
    search requires.
    """
    paths = [item[0] for item in items]
    flags = archive_flags(paths, any(item[2] for item in items))
    folders = _folders(items)
    directory, order = _directory(folders, flags)
    names_len = sum(len(name) + 1 for _h, _f, files in folders for _fh, name, _i in files)
    header = struct.pack('<4s8I', b'BSA\0', VERSION, HEADER_SIZE, flags, len(folders), len(items),
                         sum(len(f) + 1 for _h, f, _fs in folders), names_len, file_flags(paths))
    handles = {}
    try:
        with open(long_path(dest), 'wb') as out:
            out.write(header + directory)
            for item, raw in _prefetched(order):
                if flags & ARCH_EMBED_NAMES:
                    out.write(bytes([len(item[0])]) + _encode(item[0]))
                if raw is not None:
                    out.write(raw.result())
                    continue
                if item[1][0] not in handles:
                    handles[item[1][0]] = open(long_path(item[1][0]), 'rb')
                _pipe(handles[item[1][0]], item[1][1], item[1][2], out)
    finally:
        for fh in handles.values():
            fh.close()


def _read_raw(span: tuple) -> bytes:
    """The bytes a raw item stores: a whole source file checked against its staged size, or an archive span."""
    with open(long_path(span[0]), 'rb') as fh:
        fh.seek(span[1])
        data = fh.read(span[2])
        grew = span[1] == 0 and fh.read(1)
    if len(data) != span[2] or grew:
        raise OSError(f'{span[0]} changed size while packing')
    return data


def _prefetched(order: list):
    """Yield (item, future of its raw bytes or None) in order, reading raw sources ahead on threads.

    Opening thousands of small files one by one dominated a pack.
    See: docs/commentary/asset_convert_bsa.md#bsa-writer
    """
    window = deque()
    with ThreadPoolExecutor(max_workers=worker_count()) as pool:
        for item in order:
            window.append((item, None if item[2] else pool.submit(_read_raw, item[1])))
            if len(window) > READ_AHEAD:
                yield window.popleft()
        while window:
            yield window.popleft()


def _pipe(fh, offset: int, length: int, out) -> None:
    """Copy `length` bytes from `offset` of `fh` to `out`."""
    fh.seek(offset)
    while length > 0:
        piece = fh.read(min(length, COPY_PIECE))
        if not piece:
            raise OSError(f'{fh.name} ended early')
        out.write(piece)
        length -= len(piece)
