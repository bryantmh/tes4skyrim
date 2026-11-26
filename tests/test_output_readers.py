"""Readers of converted output find files that live only in a packed folder's archives.

See: docs/commentary/asset_convert_bsa.md#archive-only-output
"""
import json

import pytest

from asset_convert.sources import bsa_pack, output_files
from tests.test_death_pile_bounds import _write_pile

GHOST = {'dissolves_on_death': True, 'death_pile': 'ghostdeathpile.nif',
         'body_dir': 'Actors\\tes4\\oblivion\\ghost'}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _pack_and_drop(root):
    """Pack the folder as stem 'Mod' and delete its loose copies."""
    (root / 'Mod.esm').write_bytes(b'TES4')
    results = {'packed': [], 'kept': [], 'skipped': [], 'errors': []}
    bsa_pack._pack(root, 'Mod', bsa_pack.BSA_SIZE_LIMIT, results)
    assert not results['errors']
    bsa_pack.drop_loose(root)


@pytest.fixture
def out_root(tmp_path):
    """An output root whose 'Mod' folder holds one NIF, packed with no loose copy left."""
    output_files.forget()
    root = tmp_path / 'output'
    _write_pile(root / 'Mod' / 'meshes' / 'actors' / 'tes4' / 'oblivion' / 'ghost' / 'ghostdeathpile.nif')
    _pack_and_drop(root / 'Mod')
    assert not (root / 'Mod' / 'meshes').exists()
    yield root
    output_files.forget()


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------

def test_mesh_scan_parses_archived_nifs(out_root):
    """The bounds/collision scan lists an archived NIF and parses it from the archive."""
    from asset_convert.collision import collision_extract
    nifs = collision_extract._list_nifs(str(out_root / 'Mod' / 'meshes'))
    assert [key for _src, key in nifs] == ['actors/tes4/oblivion/ghost/ghostdeathpile.nif']
    assert collision_extract.read_nif_data(nifs[0][0]).roots


def test_mesh_cache_rescan_sees_an_archive_only_meshes_folder(out_root):
    """`rescan_mesh_caches` no longer skips a meshes folder that has no loose copy."""
    assert output_files.is_dir(out_root / 'Mod' / 'meshes')


def test_death_pile_bounds_from_an_archived_mesh(out_root):
    """The pile's OBND is read from the archive when no loose copy is left."""
    from tes5_import.actors import creature_races
    assert creature_races._pile_mesh_bounds(GHOST, 'ghostdeathpile.nif', str(out_root)) == (
        [-10, -20, 0], [10, 20, 5])


# ---------------------------------------------------------------------------
# Asset stages
# ---------------------------------------------------------------------------

def test_creature_manifests_are_read_from_the_archive(tmp_path, monkeypatch):
    """A project manifest only the archive holds still registers in the fragment."""
    from asset_convert.havok import creature_pipeline
    output_files.forget()
    monkeypatch.setattr(creature_pipeline, 'current_namespace', lambda: 'tes4')
    root = tmp_path / 'Mod'
    manifest = root / 'meshes' / 'actors' / 'tes4' / 'mod' / 'rat' / 'project_manifest.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'project_txt': 'TES4ModRatProject.txt', 'name': 'rat'}))
    _pack_and_drop(root)
    assert not manifest.exists()
    assert list(creature_pipeline.manifests_under(str(root / 'meshes'))) == ['tes4modratproject.txt']


def test_voice_prune_discards_an_archived_stale_file(tmp_path):
    """A stale voice file only the archive holds is marked for the next pack to drop."""
    from asset_convert.audio.audio_converter import prune_stale_voice_files
    output_files.forget()
    root = tmp_path / 'Mod'
    voice = root / 'sound' / 'voice' / 'mod.esm' / 'maleeven'
    voice.mkdir(parents=True)
    (voice / 'keep_1.fuz').write_bytes(b'k')
    (voice / 'dead_1.fuz').write_bytes(b'd')
    _pack_and_drop(root)
    removed = prune_stale_voice_files(set(), {(voice / 'keep_1.fuz').resolve()},
                                      {root / 'sound' / 'voice' / 'mod.esm'})
    assert [f.name for f in removed] == ['dead_1.fuz']
    assert output_files.discarded(root) == {'sound\\voice\\mod.esm\\maleeven\\dead_1.fuz'}


def test_grass_source_found_in_an_archived_master(tmp_path, monkeypatch):
    """A master's grass mesh that only its archive holds is found, and not owned by the child."""
    from asset_convert.nif import grass_profile
    output_files.forget()
    monkeypatch.setattr(grass_profile, 'current_namespace', lambda: 'tes4')
    master = tmp_path / 'Mod'
    (master / 'meshes' / 'tes4' / 'grass').mkdir(parents=True)
    (master / 'meshes' / 'tes4' / 'grass' / 'g.nif').write_bytes(b'nif')
    _pack_and_drop(master)
    nif, owned = grass_profile._grass_source('grass\\g.nif', tmp_path / 'Child' / 'meshes',
                                             [master / 'meshes'])
    assert nif is not None and not owned
    grass_profile.output_files.copy_to(nif, tmp_path / 'copy.nif')
    assert (tmp_path / 'copy.nif').read_bytes() == b'nif'


# ---------------------------------------------------------------------------
# LOD
# ---------------------------------------------------------------------------

def _write_skyrim_nif(path):
    """A Skyrim-version (20.2.0.7, 12/83) NIF at `path` whose root is a bare NiNode."""
    from pyffi.formats.nif import NifFormat
    data = NifFormat.Data(version=0x14020007, user_version=12, user_version_2=83)
    data.roots = [NifFormat.NiNode()]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        data.write(f)


def test_lod_uses_an_archived_master_mesh_without_staging_it(out_root, tmp_path):
    """An archived supplier mesh is screened from the archive, not copied, and its archives are listed."""
    from asset_convert.lod import lod_gen
    output_files.forget()
    _write_skyrim_nif(out_root / 'Sup' / 'meshes' / 'actors' / 'tes4' / 'oblivion' / 'ghost' / 'ghostdeathpile.nif')
    (out_root / 'Sup' / 'meshes' / 'keep.txt').write_text('x')
    results = {'packed': [], 'kept': [], 'skipped': [], 'errors': []}
    bsa_pack._pack(out_root / 'Sup', 'Sup', bsa_pack.BSA_SIZE_LIMIT, results)
    bsa_pack.drop_loose(out_root / 'Sup')
    bake = tmp_path / 'AutoConvertLOD' / 'meshes'
    rel = 'meshes\\actors\\tes4\\oblivion\\ghost\\ghostdeathpile.nif'
    supplier = out_root / 'Sup' / 'meshes'
    assert lod_gen._import_master_mesh(rel, bake, [supplier])
    assert not bake.exists()
    assert lod_gen._lod_mesh_is_safe(rel, bake, [supplier])
    assert lod_gen._archive_resources([supplier]) == [
        f"Resource={(out_root / 'Sup' / 'Sup.bsa').resolve()}"]


def test_lod_resources_list_later_suppliers_first(tmp_path):
    """LODGen's later-archive-wins order is the reverse of the first-hit supplier order."""
    from asset_convert.lod import lod_gen
    output_files.forget()
    dirs = []
    for name in ('A', 'B'):
        root = tmp_path / name
        (root / 'meshes').mkdir(parents=True)
        (root / 'meshes' / 'x.nif').write_bytes(name.encode())
        _pack_and_drop(root)
        dirs.append(root / 'meshes')
    assert [line.split('\\')[-2] for line in lod_gen._archive_resources(dirs)] == ['B', 'A']


# ---------------------------------------------------------------------------
# Test instances
# ---------------------------------------------------------------------------

def test_a_stale_link_shadowing_an_archived_file_is_removed(tmp_path):
    """A loose hard link an earlier build left in Data, at a path the archive now holds, goes."""
    from tools.live import test_instance
    output_files.forget()
    root = tmp_path / 'Mod'
    (root / 'meshes').mkdir(parents=True)
    (root / 'meshes' / 'a.nif').write_bytes(b'a')
    data = tmp_path / 'Data'
    test_instance.link(str(root / 'meshes' / 'a.nif'), str(data / 'meshes' / 'a.nif'))
    _pack_and_drop(root)
    (data / 'other.nif').write_bytes(b'o')
    assert test_instance.drop_shadowing_links(str(root), str(data), set()) == 1
    assert not (data / 'meshes' / 'a.nif').exists() and (data / 'other.nif').exists()
