"""Parallel BSA extraction writes exactly what the serial reader would.

See: docs/commentary/asset_convert_bsa.md#parallel-extraction
"""
import struct
import zlib

import pytest

from asset_convert.sources import bsa_extract
from asset_convert.sources.bsa_extract import _ARCH_COMPRESS, extract_archives, iter_bsa

FILES = {
    'meshes\\a\\one.nif': b'one' * 50,
    'meshes\\a\\two.nif': b'two' * 70,
    'textures\\b\\tex.dds': bytes(range(256)) * 4,
    'sound\\fx\\hit.wav': b'RIFF' + b'w' * 40,
    'sound\\fx\\hit.lip': b'lip',
    'docs\\readme.txt': b'hello',
}


def _build_v104(path, files, compress):
    """Write a minimal v104 BSA holding `files`, zlib-compressed when `compress`."""
    folders = {}
    for name, data in files.items():
        folder, _, leaf = name.rpartition('\\')
        folders.setdefault(folder, []).append((leaf, data))
    blobs = {}
    for folder, items in folders.items():
        for leaf, data in items:
            blobs[folder, leaf] = struct.pack('<I', len(data)) + zlib.compress(data) if compress else data
    dir_size = 36 + 16 * len(folders)
    folder_blocks = sum(1 + len(f) + 1 + 16 * len(i) for f, i in folders.items())
    names = b''.join(leaf.encode() + b'\0' for items in folders.values() for leaf, _ in items)
    data_at = dir_size + folder_blocks + len(names)
    records, offset = b'', data_at
    folder_table, file_blocks = b'', b''
    for folder, items in folders.items():
        folder_table += struct.pack('<QIII', 0, len(items), 0, 0)[:16]
        file_blocks += bytes([len(folder) + 1]) + folder.encode() + b'\0'
        for leaf, _ in items:
            blob = blobs[folder, leaf]
            file_blocks += struct.pack('<QII', 0, len(blob), offset)
            records += blob
            offset += len(blob)
    flags = _ARCH_COMPRESS if compress else 0
    head = b'BSA\0' + struct.pack('<8I', 104, 36, flags, len(folders), len(files), 0, len(names), 0)
    path.write_bytes(head + folder_table + file_blocks + names + records)


def _tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob('*') if p.is_file() and p.name != bsa_extract.MANIFEST_NAME}


@pytest.mark.parametrize('compress', [False, True])
@pytest.mark.parametrize('pooled', [False, True])
def test_matches_serial_reader(tmp_path, monkeypatch, compress, pooled):
    """Every non-lip entry lands under its category with the reader's bytes, pool or not."""
    monkeypatch.setattr(bsa_extract, '_POOL_MIN_BYTES', 0 if pooled else 1 << 40)
    bsa = tmp_path / 'T.bsa'
    _build_v104(bsa, FILES, compress)
    stats = extract_archives([bsa], tmp_path / 'out', source_name='P')[0]
    want = {'/'.join(bsa_extract.categorize(p).split('/')).lower(): d
            for p, d in iter_bsa(bsa) if not p.endswith('.lip')}
    got = {k.lower(): v for k, v in _tree(tmp_path / 'out' / 'P').items()}
    assert got == want
    assert (stats['total_files'], stats['extracted'], stats['skipped']) == (6, 5, 1)


def test_later_archive_wins_a_path_clash(tmp_path, monkeypatch):
    """Two archives holding one path leave the later archive's bytes, whatever the worker order."""
    monkeypatch.setattr(bsa_extract, '_POOL_MIN_BYTES', 0)
    first, second = tmp_path / 'A.bsa', tmp_path / 'B.bsa'
    _build_v104(first, {'meshes\\x.nif': b'first'}, True)
    _build_v104(second, {'meshes\\x.nif': b'second'}, True)
    stats = extract_archives([first, second], tmp_path / 'out', source_name='P')
    assert (tmp_path / 'out' / 'P' / 'meshes' / 'x.nif').read_bytes() == b'second'
    assert [s['extracted'] for s in stats] == [1, 1]


def test_second_run_is_cached(tmp_path):
    """A recorded archive of the same size and path is skipped."""
    bsa = tmp_path / 'T.bsa'
    _build_v104(bsa, FILES, False)
    extract_archives([bsa], tmp_path / 'out', source_name='P')
    assert extract_archives([bsa], tmp_path / 'out', source_name='P')[0]['skipped_cached']
