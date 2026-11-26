"""Archive-only output: reading packed output, dropping loose copies, and repacking over them.

See: docs/commentary/asset_convert_bsa.md#archive-only-output
"""
import json
import os
import random

import pytest

from asset_convert.sources import bsa_pack, bsa_write, output_files
from asset_convert.sources.bsa_extract import iter_bsa

FILES = {
    'meshes/clutter/Bowl.nif': 3000,
    'meshes/actors/rat/skeleton.nif': 5000,
    'textures/clutter/bowl.dds': 6000,
    'sound/fx/door.wav': 4000,
    'sound/voice/mod.esm/maleeven/q_t_00001234_1.fuz': 2500,
    'scripts/foo.pex': 700,
    'scripts/source/foo.psc': 300,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write(root, rel, size, seed):
    """Write a file that both compresses and differs per seed; returns its bytes."""
    rng = random.Random(seed)
    body = bytes(rng.randrange(256) for _ in range(size // 4)) * 3
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body + bytes(size - len(body)))
    return path.read_bytes()


def _pack(root):
    """Pack `root` as stem 'Mod'; returns the results."""
    results = {'packed': [], 'kept': [], 'skipped': [], 'errors': []}
    bsa_pack._pack(root, 'Mod', bsa_pack.BSA_SIZE_LIMIT, results)
    bsa_pack._sweep_stale(root, 'Mod', [], results)
    return results


def _contents(root):
    """{archive path: bytes} over every archive in the folder."""
    return {p: d for bsa in sorted(root.glob('*.bsa')) for p, d in iter_bsa(bsa)}


def _stored(want):
    """`want` keyed by archive path."""
    return {rel.replace('/', '\\').lower(): d for rel, d in want.items()}


@pytest.fixture
def packed(tmp_path):
    """A packed output folder (loose copies still there), plus {rel: bytes} of what it held."""
    output_files.forget()
    root = tmp_path / 'Mod'
    want = {rel: _write(root, rel, size, i) for i, (rel, size) in enumerate(FILES.items())}
    (root / 'Mod.esm').write_bytes(b'TES4')
    (root / 'SKSE').mkdir()
    (root / 'SKSE' / 'loose.json').write_text('{}')
    _pack(root)
    yield root, want
    output_files.forget()


# ---------------------------------------------------------------------------
# Dropping loose copies
# ---------------------------------------------------------------------------

def test_drop_loose_keeps_plugins_and_skse(packed):
    """Every packed file goes, script sources included; the plugin and SKSE/ stay, and empty folders vanish."""
    root, want = packed
    count, freed = bsa_pack.drop_loose(root)
    assert count == len(FILES)
    assert freed == sum(len(d) for d in want.values())
    assert (root / 'Mod.esm').is_file() and (root / 'SKSE' / 'loose.json').is_file()
    assert not (root / 'scripts').exists()
    assert not (root / 'meshes').exists() and not (root / 'sound').exists()


def test_drop_loose_deletes_what_the_archives_never_take(packed):
    """OS junk and pruned textures are never packed, so they go too and nothing stays loose."""
    root, _want = packed
    for rel in ('textures/tes4/faces/mod.esm/0001_0.dds', 'textures/clutter/thumbs.db',
                'textures/clutter/notes.txt'):
        _write(root, rel, 100, 7)
    _pack(root)
    count, _freed = bsa_pack.drop_loose(root)
    assert count == len(FILES) + 3
    assert not (root / 'textures').exists()


def test_a_file_changed_since_the_pack_is_not_dropped(packed):
    """A loose file rewritten after the pack is newer than its archived copy, so it stays."""
    root, _want = packed
    _write(root, 'meshes/clutter/Bowl.nif', 3100, 99)
    bsa_pack.drop_loose(root)
    assert (root / 'meshes' / 'clutter' / 'Bowl.nif').is_file()


def test_nothing_is_dropped_when_an_archive_was_replaced(packed):
    """An archive not as the manifest recorded it is not trusted to hold the files."""
    root, _want = packed
    (root / 'Mod.bsa').write_bytes((root / 'Mod.bsa').read_bytes() + b'x')
    assert bsa_pack.drop_loose(root) == (0, 0)


# ---------------------------------------------------------------------------
# Reading through the layer
# ---------------------------------------------------------------------------

def test_every_dropped_file_reads_back_through_the_layer(packed):
    """Compressed meshes, embedded-name textures and raw sound read back byte-identical, any case."""
    root, want = packed
    bsa_pack.drop_loose(root)
    for rel, data in want.items():
        path = root / rel.upper()
        assert output_files.exists(path)
        assert output_files.read_bytes(path) == data
        with output_files.open_binary(path) as fh:
            assert fh.read() == data
    assert not output_files.exists(root / 'meshes' / 'nothing.nif')


def test_listing_and_folders_cover_archived_files(packed):
    """`files_under` lists archived and loose files alike; `is_dir` sees archived folders."""
    root, _want = packed
    bsa_pack.drop_loose(root)
    _write(root, 'meshes/clutter/new.nif', 100, 7)
    assert sorted(output_files.files_under(root / 'meshes', '.nif')) == [
        'actors\\rat\\skeleton.nif', 'clutter\\bowl.nif', 'clutter\\new.nif']
    assert isinstance(output_files.files_under(root / 'meshes')['clutter\\new.nif'], str)
    assert output_files.is_dir(root / 'meshes' / 'actors')
    assert not output_files.is_dir(root / 'meshes' / 'nothing')


def test_signature_and_copy_of_an_archived_file(packed, tmp_path):
    """An archived file answers the size/mtime the pack recorded, and copies out to a real file."""
    root, want = packed
    sig = bsa_pack._load_manifest(root)['files']['sound\\fx\\door.wav']
    bsa_pack.drop_loose(root)
    assert output_files.signature(root / 'sound' / 'fx' / 'door.wav') == sig
    output_files.copy_to(root / 'sound' / 'fx' / 'door.wav', tmp_path / 'x' / 'door.wav')
    assert (tmp_path / 'x' / 'door.wav').read_bytes() == want['sound/fx/door.wav']


def test_archived_script_sources_mirror_into_an_import_folder(packed, tmp_path):
    """Only archive-only `.psc` are copied, with the recorded mtime; a second call rewrites nothing."""
    root, want = packed
    sig = bsa_pack._load_manifest(root)['files']['scripts\\source\\foo.psc']
    src = root / 'scripts' / 'source'
    mirror = tmp_path / 'imports'
    (mirror / 'old.psc').parent.mkdir(parents=True)
    (mirror / 'old.psc').write_text('stale')
    assert output_files.mirror_archived(src, mirror, '.psc') == 0
    assert not (mirror / 'old.psc').exists()
    bsa_pack.drop_loose(root)
    assert output_files.mirror_archived(src, mirror, '.psc') == 1
    copy = mirror / 'foo.psc'
    assert copy.read_bytes() == want['scripts/source/foo.psc']
    assert [copy.stat().st_size, copy.stat().st_mtime_ns] == sig
    before = copy.stat().st_ctime_ns
    output_files.mirror_archived(src, mirror, '.psc')
    assert copy.stat().st_ctime_ns == before


def test_unpacked_files_repack_unchanged(packed):
    """`unpack` restores archive-only files loose with their recorded signature, so a repack keeps the archive."""
    root, want = packed
    bsa_pack.drop_loose(root)
    assert output_files.unpack(root / 'scripts') == 2
    assert (root / 'scripts' / 'source' / 'foo.psc').read_bytes() == want['scripts/source/foo.psc']
    results = _pack(root)
    assert results['packed'] == [] and len(results['kept']) == 1
    assert bsa_pack.drop_loose(root)[0] == 2


def test_a_discard_survives_a_fresh_process(packed):
    """A reader that loads the folder after `discard_archived` no longer sees the marked file."""
    root, _want = packed
    bsa_pack.drop_loose(root)
    assert output_files.discard_archived([root / 'scripts' / 'foo.pex']) == 1
    output_files.forget()
    assert not output_files.exists(root / 'scripts' / 'foo.pex')
    assert output_files.exists(root / 'scripts' / 'source' / 'foo.psc')


def test_an_unpacked_folder_reads_loose_only(tmp_path):
    """A folder with no pack manifest is plain loose files."""
    output_files.forget()
    _write(tmp_path / 'Plain', 'meshes/a.nif', 50, 1)
    assert output_files.exists(tmp_path / 'Plain' / 'meshes' / 'a.nif')
    assert output_files.member(tmp_path / 'Plain' / 'meshes' / 'a.nif') is None
    assert list(output_files.files_under(tmp_path / 'Plain')) == ['meshes\\a.nif']


# ---------------------------------------------------------------------------
# Repacking over dropped files
# ---------------------------------------------------------------------------

def test_repack_over_dropped_files_keeps_everything_untouched(packed):
    """With no loose copies the archives are the source: a repack keeps the archive as it is."""
    root, want = packed
    before = {p.name: p.stat().st_mtime_ns for p in root.glob('*.bsa')}
    bsa_pack.drop_loose(root)
    results = _pack(root)
    assert results['packed'] == [] and len(results['kept']) == 1
    assert {p.name: p.stat().st_mtime_ns for p in root.glob('*.bsa')} == before
    assert _contents(root) == _stored(want)


def test_repack_with_one_rewritten_file_keeps_the_rest(packed, monkeypatch):
    """A step rewrites one mesh loose: only it is compressed; raw sound is copied from the old archive."""
    root, want = packed
    bsa_pack.drop_loose(root)
    want['meshes/clutter/Bowl.nif'] = _write(root, 'meshes/clutter/Bowl.nif', 3100, 99)
    compressed = []
    real = bsa_write.compress_batch
    monkeypatch.setattr(bsa_write, 'compress_batch',
                        lambda sources, chunk: compressed.extend(sources) or real(sources, chunk))
    _pack(root)
    assert [os.path.basename(s) for s in compressed] == ['Bowl.nif']
    assert _contents(root) == _stored(want)
    assert bsa_pack.drop_loose(root)[0] == 1


def test_a_discarded_archived_file_leaves_the_next_pack(packed):
    """`discard` on an archive-only file marks it; the next pack drops it and clears the mark."""
    root, want = packed
    bsa_pack.drop_loose(root)
    assert output_files.discard(root / 'sound' / 'fx' / 'door.wav')
    assert not output_files.exists(root / 'sound' / 'fx' / 'door.wav')
    _pack(root)
    assert sorted(_contents(root)) == sorted(set(_stored(want)) - {'sound\\fx\\door.wav'})
    assert not (root / output_files.DISCARDED_NAME).exists()


def test_a_file_a_step_deleted_while_loose_leaves_the_archive(packed):
    """Packed with loose copies kept, then a step removes one: the next pack drops it."""
    root, _want = packed
    os.remove(root / 'meshes' / 'actors' / 'rat' / 'skeleton.nif')
    _pack(root)
    assert 'meshes\\actors\\rat\\skeleton.nif' not in _contents(root)
    manifest = json.loads((root / output_files.MANIFEST_NAME).read_text())
    assert 'meshes\\actors\\rat\\skeleton.nif' not in manifest['files']


def test_archived_files_under_a_foreign_namespace_leave_the_next_pack(tmp_path):
    """A plugin moved to another namespace: its old namespace's archive-only copies are not carried."""
    output_files.forget()
    root = tmp_path / 'Mod'
    (root / 'Mod.esm').parent.mkdir(parents=True)
    (root / 'Mod.esm').write_bytes(b'TES4')
    for rel in ('meshes/tes4/a.nif', 'meshes/actors/tes4/b.nif', 'meshes/morrowind/a.nif', 'meshes/tes4.nif'):
        _write(root, rel, 400, len(rel))
    _pack(root)
    bsa_pack.drop_loose(root)
    results = {'packed': [], 'kept': [], 'skipped': [], 'errors': []}
    bsa_pack._pack(root, 'Mod', bsa_pack.BSA_SIZE_LIMIT, results, foreign=frozenset({'tes4'}))
    assert sorted(_contents(root)) == ['meshes\\morrowind\\a.nif', 'meshes\\tes4.nif']
    output_files.forget()


def test_carried_files_survive_two_repacks(packed):
    """Repacking twice over dropped files never loses one."""
    root, want = packed
    bsa_pack.drop_loose(root)
    want['meshes/clutter/extra.nif'] = _write(root, 'meshes/clutter/extra.nif', 200, 5)
    _pack(root)
    bsa_pack.drop_loose(root)
    _pack(root)
    assert _contents(root) == _stored(want)
