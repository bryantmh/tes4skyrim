"""Our SSE v105 BSA writer and the incremental pack built on it.

Every archive is read back through `bsa_extract`'s own readers.
See: docs/commentary/asset_convert_bsa.md#bsa-writer
"""
import os
import random
import shutil
import struct

import pytest

from asset_convert.sources import bsa_pack, bsa_write
from asset_convert.sources.bsa_extract import iter_bsa, read_bsa_directory, tes4_hash

FILES = {
    'meshes/clutter/bowl.nif': 3000,
    'meshes/clutter/Cup01.NIF': 2000,
    'meshes/actors/rat/skeleton.nif': 5000,
    'meshes/actors/rat/idle.kf': 800,
    'textures/clutter/bowl.dds': 6000,
    'textures/clutter/bowl_n.dds': 6000,
    'sound/fx/door.wav': 4000,
    'sound/voice/mod.esm/maleeven/q_t_00001234_1.fuz': 2500,
    'music/explore/a.xwm': 1500,
    'scripts/foo.pex': 700,
}


def _write(root, rel, size, seed):
    """Write a part-repetitive, part-random file so it both compresses and differs per seed."""
    rng = random.Random(seed)
    body = bytes(rng.randrange(256) for _ in range(size // 4)) * 3
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body + bytes(size - len(body)))
    return path.read_bytes()


@pytest.fixture
def mod(tmp_path):
    """An output folder with every content kind, plus the expected {archive path: bytes}."""
    root = tmp_path / 'Mod'
    want = {bsa_write.archive_path(rel): _write(root, rel, size, i)
            for i, (rel, size) in enumerate(FILES.items())}
    (root / 'SKSE').mkdir()
    (root / 'SKSE' / 'loose.json').write_text('{}')
    return root, want


def _pack(root, limit=bsa_pack.BSA_SIZE_LIMIT):
    """Run the pack on a folder; returns its results."""
    results = {'packed': [], 'kept': [], 'skipped': [], 'errors': []}
    bsa_pack._pack(root, 'Mod', limit, results)
    bsa_pack._sweep_stale(root, 'Mod', [], results)
    return results


def _contents(root):
    """{archive path: bytes} over every archive in the folder, failing on a duplicate."""
    out = {}
    for bsa in sorted(root.glob('*.bsa')):
        for path, data in iter_bsa(bsa):
            assert path not in out
            out[path] = data
    return out


def test_round_trip_every_byte(mod):
    """Every packed file reads back byte-identical, and loose-only folders stay out."""
    root, want = mod
    _pack(root)
    assert sorted(p.name for p in root.glob('*.bsa')) == ['Mod.bsa']
    assert _contents(root) == want


def test_flags_follow_vanilla(mod):
    """Meshes and textures compressed; sound, music and scripts raw; no embedded names in a mixed archive."""
    root, _want = mod
    _pack(root)
    with open(root / 'Mod.bsa', 'rb') as fh:
        version, flags, entries = read_bsa_directory(fh)
    assert version == 105 and flags == 0x97
    assert {p: c for p, _s, _o, c in entries} == {
        bsa_write.archive_path(r): r.startswith(('meshes', 'textures')) for r in FILES}
    head = (root / 'Mod.bsa').read_bytes()[:36]
    assert struct.unpack_from('<I', head, 32)[0] == (
        bsa_write.FILE_MESHES | bsa_write.FILE_TEXTURES | bsa_write.FILE_SOUNDS | bsa_write.FILE_VOICES)


def test_an_all_texture_archive_embeds_names_within_the_limit(mod):
    """A Part archive holding only textures gets vanilla's textures flags and still fits the limit."""
    root, want = mod
    _pack(root, limit=9000)
    texture_flags = []
    for bsa in sorted(root.glob('*.bsa')):
        with open(bsa, 'rb') as fh:
            _v, flags, entries = read_bsa_directory(fh)
        if all(e[0].startswith('textures') for e in entries):
            texture_flags.append(flags)
        assert bsa.stat().st_size <= 9000
    assert texture_flags and set(texture_flags) == {0x107}
    assert _contents(root) == want


def test_records_are_in_hash_order(mod):
    """Folder records, and file records within a folder, are sorted by their stored TES4 hash."""
    root, _want = mod
    _pack(root)
    with open(root / 'Mod.bsa', 'rb') as fh:
        _magic, _v, dir_at, _f, folders, _files, _fnl, names_len, _ff = struct.unpack('<4s8I', fh.read(36))
        fh.seek(dir_at)
        records = [struct.unpack('<QIIQ', fh.read(24)) for _ in range(folders)]
        assert [r[0] for r in records] == sorted(r[0] for r in records)
        for fhash, count, _pad, offset in records:
            assert fh.tell() + names_len == offset
            name = fh.read(fh.read(1)[0]).rstrip(b'\0').decode()
            assert tes4_hash(name, folder=True) == fhash
            hashes = [struct.unpack('<QII', fh.read(16))[0] for _ in range(count)]
            assert hashes == sorted(hashes)


def test_compressed_payload_is_a_vanilla_shaped_lz4_frame():
    """u32 original size, then an LZ4 frame with independent blocks and no content size."""
    blob = bsa_write.compress_bytes(b'abc' * 1000)
    assert struct.unpack_from('<I', blob)[0] == 3000
    assert blob[4:8] == b'\x04\x22\x4d\x18' and blob[8] == 0x60


def test_repack_reuses_unchanged_and_compresses_only_new(mod, monkeypatch):
    """One mesh changed, one added, one removed: only two files are compressed again."""
    root, want = mod
    _pack(root)
    compressed = []
    real = bsa_write.compress_batch
    monkeypatch.setattr(bsa_write, 'compress_batch',
                        lambda sources, chunk: compressed.extend(sources) or real(sources, chunk))
    want['meshes\\clutter\\bowl.nif'] = _write(root, 'meshes/clutter/bowl.nif', 3100, 99)
    want['meshes\\clutter\\new.nif'] = _write(root, 'meshes/clutter/new.nif', 900, 98)
    os.remove(root / 'meshes/actors/rat/idle.kf')
    del want['meshes\\actors\\rat\\idle.kf']
    results = _pack(root)
    assert sorted(os.path.basename(s).lower() for s in compressed) == ['bowl.nif', 'new.nif']
    assert results['kept'] == [] and [os.path.basename(p) for p in results['packed']] == ['Mod.bsa']
    assert _contents(root) == want


def test_repack_with_nothing_changed_rewrites_nothing(mod):
    """An unchanged folder keeps every archive file as it is."""
    root, _want = mod
    _pack(root)
    before = {p.name: p.stat().st_mtime_ns for p in root.glob('*.bsa')}
    results = _pack(root)
    assert results['packed'] == [] and len(results['kept']) == 1
    assert {p.name: p.stat().st_mtime_ns for p in root.glob('*.bsa')} == before


def test_a_replaced_archive_is_never_reused(mod, monkeypatch):
    """An archive not as the manifest recorded it (another tool rewrote it) is ignored as a source."""
    root, want = mod
    _pack(root)
    (root / 'Mod.bsa').write_bytes((root / 'Mod.bsa').read_bytes() + b'x')
    compressed = []
    real = bsa_write.compress_batch
    monkeypatch.setattr(bsa_write, 'compress_batch',
                        lambda sources, chunk: compressed.extend(sources) or real(sources, chunk))
    _pack(root)
    assert len(compressed) == 6
    assert _contents(root) == want


def test_split_at_a_small_limit_keeps_every_file_once(mod):
    """Overflow goes to Part 2, 3, ...; no archive exceeds the limit; nothing lost or doubled."""
    root, want = mod
    _pack(root, limit=9000)
    names = sorted(p.name for p in root.glob('*.bsa'))
    assert names[-1] == 'Mod.bsa' and names[:-1] == [f'Mod - Part {n}.bsa' for n in range(2, len(names) + 1)]
    assert len(names) > 2 and all((root / n).stat().st_size <= 9000 for n in names)
    assert _contents(root) == want
    _pack(root)
    assert sorted(p.name for p in root.glob('*.bsa')) == ['Mod.bsa']
    assert _contents(root) == want


def test_old_loaders_and_scratch_are_swept(mod):
    """Older builds' loader ESL/BSA pairs, BSArch staging and temp archives are removed."""
    root, _want = mod
    for name in ('Mod_loader.esl', 'Mod_loader.bsa', 'Mod_loader_1 - Textures.bsa',
                 'oblivion_loader.esl', 'Mod.bsa.tmp', 'Mod - Part 7.bsa'):
        (root / name).write_bytes(b'x')
    (root / '_bsa_staging_main_0' / 'meshes').mkdir(parents=True)
    _pack(root)
    assert sorted(p.name for p in root.iterdir() if p.name != bsa_pack.MANIFEST_NAME) == [
        'Mod.bsa', 'SKSE', 'meshes', 'music', 'scripts', 'sound', 'textures']


def test_a_folder_without_its_plugin_stays_loose(mod, monkeypatch):
    """LOD, asset-only and other pluginless outputs are never packed; a plugin of the stem's name is."""
    root, want = mod
    monkeypatch.setattr(bsa_pack, '_out_root', lambda *_a: root)
    monkeypatch.setattr(bsa_pack, 'archive_stem', lambda *_a: 'Mod')
    monkeypatch.setattr(bsa_pack, '_stale_stems', lambda *_a: [])
    (root / 'Mod.bsa').write_bytes(b'stale')
    (root / 'Other.esp').write_bytes(b'TES4')
    results = bsa_pack.pack_bsas('Mod', output_dir=str(root.parent))
    assert results['skipped'] == ['Mod'] and not list(root.glob('*.bsa'))
    (root / 'Mod.esp').write_bytes(b'TES4')
    results = bsa_pack.pack_bsas('Mod.esp', output_dir=str(root.parent))
    assert len(results['packed']) == 1 and _contents(root) == want


def test_os_junk_files_are_not_packed(tmp_path):
    """Thumbs.db/desktop.ini are skipped; every other file still packs."""
    meshes = tmp_path / 'meshes' / 'sub'
    meshes.mkdir(parents=True)
    for name in ('a.nif', 'Thumbs.db', 'desktop.ini', 'notes.txt'):
        (meshes / name).write_bytes(b'x')
    packed = {f[0] for f in bsa_pack._collect_files(tmp_path, ['meshes'])}
    assert packed == {'meshes\\sub\\a.nif', 'meshes\\sub\\notes.txt'}


def test_an_unstorable_name_is_left_out(mod):
    """A path too long for the one-byte length field is skipped, not written corrupt."""
    root, want = mod
    _write(root, 'meshes/' + 'd' * 250 + '/x.nif', 100, 5)
    _pack(root)
    assert _contents(root) == want


def _legacy_pack(root):
    """Lay the folder out as older builds did, `Mod - Textures.bsa` beside `Mod.bsa`, both recorded as trusted."""
    files = bsa_pack._collect_files(root, bsa_pack.packed_dirs(root))
    work = root / bsa_pack.WORK_DIR
    work.mkdir()
    records = {}
    for name, textures in (('Mod - Textures.bsa', True), ('Mod.bsa', False)):
        items = bsa_write.stage([f[:3] for f in files if f[0].startswith('textures') == textures], {}, str(work))
        bsa_write.write_bsa(root / name, items)
        records[name] = {'stamp': bsa_pack._stamp(root / name), 'digest': 'old'}
    bsa_pack._write_manifest(root, {'scheme': bsa_pack.SCHEME, 'archives': records,
                                    'files': {f[0]: [f[2], f[3]] for f in files}, 'gone': []})
    shutil.rmtree(work)


def test_a_legacy_textures_archive_is_reused_then_swept(mod, monkeypatch):
    """An archive-only output with `Mod - Textures.bsa` repacks into `Mod.bsa`, compressing nothing."""
    root, want = mod
    _legacy_pack(root)
    assert bsa_pack.drop_loose(root)[0] == len(FILES)
    compressed = []
    real = bsa_write.compress_batch
    monkeypatch.setattr(bsa_write, 'compress_batch',
                        lambda sources, chunk: compressed.extend(sources) or real(sources, chunk))
    _pack(root)
    assert compressed == []
    assert sorted(p.name for p in root.glob('*.bsa')) == ['Mod.bsa']
    assert _contents(root) == want
