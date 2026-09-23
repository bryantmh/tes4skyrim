"""The group-layout resolvers must be REACHED, not merely exist.

`test_plugin_path_resolution.py` is a static ban on building a plugin's folder
by joining its name onto a root. It cannot see the other half of the problem:
a call site that holds the right value and passes the WRONG ONE -- a RECORD dir
where an ASSET root was meant, or a record dir where the export ROOT was meant.
Every defect pinned here was exactly that shape, found by review after the
group layout and its lint had both landed, and each one failed silently.

Fixtures build a registry by hand rather than running an import: the rule under
test is how a path is DERIVED from the registry, so writing the registry
directly is both faster and a sharper statement of the contract.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BS = chr(92)


def _fake_group(tmp_path, plugins, label='My Pack'):
    """An export root holding one registered mod. Returns the root."""
    exp = tmp_path / 'export'
    exp.mkdir(exist_ok=True, parents=True)
    (exp / 'sources.json').write_text(json.dumps({
        'version': 1,
        'sources': {n: {'kind': 'archive', 'plugin': n, 'group_id': 'g1',
                        'group_label': label,
                        'group_plugins': list(plugins)} for n in plugins},
    }), encoding='utf-8')
    return exp


# ---------------------------------------------------------------------------
#  The nesting rule must be STABLE
# ---------------------------------------------------------------------------

def test_record_dir_does_not_move_when_a_sibling_is_imported(tmp_path):
    """The nesting rule reads the ARCHIVE's plugin list, not the registry.

    Keying it on how many members happened to be REGISTERED meant importing a
    second plugin from the same archive silently relocated the first one's
    record directory. Its already-exported .txt dump was left behind, and
    every later stage then reported "No export directory" and skipped.
    """
    from asset_convert.sources import source_registry

    full = _fake_group(tmp_path / 'full', ['A.esp', 'B.esp'])
    # The same archive with only A.esp registered so far. `group_plugins`
    # still names both, because it records what the ARCHIVE ships.
    partial = _fake_group(tmp_path / 'part', ['A.esp'])
    reg = json.loads((partial / 'sources.json').read_text(encoding='utf-8'))
    reg['sources']['A.esp']['group_plugins'] = ['A.esp', 'B.esp']
    (partial / 'sources.json').write_text(json.dumps(reg), encoding='utf-8')

    assert source_registry.record_dir(full, 'A.esp').name == 'A.esp'
    assert source_registry.record_dir(partial, 'A.esp').name == 'A.esp'


def test_a_one_plugin_mod_keeps_its_records_in_the_group_root(tmp_path):
    """Nesting a lone plugin would move every single-plugin mod on disk."""
    from asset_convert.sources import source_registry
    exp = _fake_group(tmp_path, ['Solo.esp'], label='Black Marsh')
    assert source_registry.record_dir(exp, 'Solo.esp').name == 'Black Marsh'


# ---------------------------------------------------------------------------
#  Ingest
# ---------------------------------------------------------------------------

def test_asset_only_mod_is_not_reimported_every_run(tmp_path):
    """The idempotence gate must not demand a plugin an asset-only mod lacks.

    Such a mod registers under its own LABEL, which is not a file and never
    lands in `_source/` (only the retained archive does), so requiring it
    there could never be satisfied and every run re-unpacked the archive.
    """
    import zipfile
    from asset_convert.sources import mod_ingest

    arc = tmp_path / 'MyAssetPack.zip'
    with zipfile.ZipFile(arc, 'w') as z:
        z.writestr('meshes/a.nif', 'x' * 16)
    exp = tmp_path / 'export'
    exp.mkdir()

    mod_ingest.ingest(arc, exp, log=lambda *a: None)
    again = mod_ingest.ingest(arc, exp, log=lambda *a: None)
    assert all(v.get('cached') for v in again.values())


def test_adding_a_plugin_member_still_reimports(tmp_path):
    """Caching must not swallow a run that would ADD a plugin to the group."""
    import struct
    import zipfile
    from asset_convert.sources import mod_ingest

    def _esp():
        return b'TES4' + struct.pack('<IIIII', 0, 0, 0, 0, 0) + b'\x00' * 4

    arc = tmp_path / 'Pack.zip'
    with zipfile.ZipFile(arc, 'w') as z:
        z.writestr('meshes/a.nif', 'x' * 16)
        z.writestr('A.esp', _esp())
        z.writestr('B.esp', _esp())
    exp = tmp_path / 'export'
    exp.mkdir()

    mod_ingest.ingest(arc, exp, plugin_members=['A.esp'], log=lambda *a: None)
    added = mod_ingest.ingest(arc, exp, plugin_members=['B.esp'],
                              log=lambda *a: None)
    assert not any(v.get('cached') for v in added.values())

    both = mod_ingest.ingest(arc, exp, plugin_members=['A.esp', 'B.esp'],
                             log=lambda *a: None)
    assert all(v.get('cached') for v in both.values())


# ---------------------------------------------------------------------------
#  Output-side resolution
# ---------------------------------------------------------------------------

def test_pack_bsas_resolves_the_output_folder_from_the_export_root(tmp_path):
    """`export_dir` is a RECORD dir and carries no registry.

    Feeding it to the output resolver made it fall back to `output/<plugin>/`
    and abort the pack with "output directory not found" for every plugin of
    a multi-plugin mod -- the very failure the resolver was added to prevent.
    """
    from asset_convert.sources.bsa_pack import _out_root

    exp = _fake_group(tmp_path, ['A.esm', 'B.esp'])
    out = tmp_path / 'output'
    assert _out_root(out, 'A.esm', exp).name == 'My Pack'
    # A record dir must NOT be accepted as the root it is not.
    assert _out_root(out, 'A.esm', exp / 'My Pack' / 'A.esm').name != 'My Pack'


# ---------------------------------------------------------------------------
#  Assets vs records
# ---------------------------------------------------------------------------

def test_book_ownership_is_decided_on_the_asset_root(tmp_path):
    """A grouped mod's own books must not be deferred to a master.

    Ownership follows the SOURCE MESH, and a record dir holds no meshes -- so
    passing one made the own-mesh probe always miss AND promoted the plugin's
    own asset root to a "master" root (it no longer equalled the dir being
    compared). Every book a grouped mod ships was deferred to a master that
    never bakes it, reported only as "N model(s) left to the master".
    """
    from asset_convert.ui.book_inam import split_master_owned

    mod = tmp_path / 'export' / 'My Pack'
    (mod / 'meshes' / 'clutter' / 'books').mkdir(parents=True)
    (mod / 'meshes' / 'clutter' / 'books' / 'mine.nif').write_bytes(b'x')
    mine = BS.join(['clutter', 'books', 'mine.nif'])

    own, deferred = split_master_owned([mine], str(mod), [str(mod)], set())
    assert deferred == 0 and len(own) == 1

    # A master's book is still correctly deferred to that master.
    master = tmp_path / 'export' / 'Master.esm'
    (master / 'meshes' / 'clutter' / 'books').mkdir(parents=True)
    (master / 'meshes' / 'clutter' / 'books' / 'theirs.nif').write_bytes(b'x')
    theirs = BS.join(['clutter', 'books', 'theirs.nif'])
    own2, deferred2 = split_master_owned([theirs], str(mod),
                                          [str(mod), str(master)], {theirs})
    assert deferred2 == 1 and own2 == []


def test_a_master_mesh_no_master_book_uses_is_baked_here(tmp_path):
    """A master bakes only its own BOOKs' models, so an unused mesh it ships is ours."""
    from asset_convert.ui.book_inam import split_master_owned

    mod = tmp_path / 'export' / 'My Pack'
    mod.mkdir(parents=True)
    master = tmp_path / 'export' / 'Master.esm'
    (master / 'meshes' / 'clutter' / 'books').mkdir(parents=True)
    (master / 'meshes' / 'clutter' / 'books' / 'theirs.nif').write_bytes(b'x')
    theirs = BS.join(['clutter', 'books', 'theirs.nif'])
    own, deferred = split_master_owned([theirs], str(mod),
                                       [str(mod), str(master)], set())
    assert deferred == 0 and own == [theirs]


def test_sound_source_dir_is_the_asset_root(tmp_path):
    """A directory-valued SOUN FNAM enumerates from the SHARED sound tree.

    Pointed at the record dir the listdir failed and the ANAM was written as a
    bare directory path with a trailing separator -- naming no playable file,
    and losing the random-variant behaviour entirely.
    """
    from tes5_import.record_types import sound as D

    mod = tmp_path / 'export' / 'My Pack'
    d = mod / 'sound' / 'fx' / 'critter'
    d.mkdir(parents=True)
    (d / 'a.wav').write_bytes(b'x')
    (d / 'b.wav').write_bytes(b'x')

    try:
        D.set_sound_source_dir(str(mod))
        got = D._sound_anam_paths(BS.join(['fx', 'critter']) + BS)
        assert len(got) == 2, got
        assert all(p.lower().endswith('.wav') for p in got)
    finally:
        D.set_sound_source_dir(None)


# ---------------------------------------------------------------------------
#  Master lookups: four modules, one answer
# ---------------------------------------------------------------------------

def test_master_lookups_all_agree_on_the_export_root(tmp_path):
    """A master's records resolve the same way from anywhere.

    Four modules answer this question and each was written separately; three
    of them still walked `dirname(export_dir)` after the fourth was fixed. A
    master that IS exported then read as missing and the feature died
    silently: dropped manifest entries, no voice-type adoption, no inherited
    creature projects -- each with at most a warning.
    """
    from tes5_import.overrides.nested import export_root, master_export_dir

    exp = _fake_group(tmp_path, ['A.esm', 'B.esp'])
    rec = exp / 'My Pack' / 'A.esm'
    rec.mkdir(parents=True)
    (exp / 'Oblivion.esm').mkdir()

    assert export_root(str(rec)) == str(exp)
    assert master_export_dir(export_root(str(rec)),
                              'Oblivion.esm') == str(exp / 'Oblivion.esm')

    # A plain (non-grouped) plugin is unchanged.
    plain = exp / 'Nehrim.esm'
    plain.mkdir()
    assert export_root(str(plain)) == str(exp)


def test_import_main_master_dirs_match_load_master_export(tmp_path):
    """`master_export_dirs` exists to mirror `load_master_export` exactly.

    Its own docstring says so, and it stopped being true: it returned [] for
    every grouped plugin, so the voice-type adoption loop never ran and every
    actor fell through to the Imperial default.
    """
    from tes5_import.pipeline import master_export_dirs
    from tes5_import.overrides.nested import export_root, master_export_dir

    exp = _fake_group(tmp_path, ['A.esm', 'B.esp'])
    rec = exp / 'My Pack' / 'A.esm'
    rec.mkdir(parents=True)
    (rec / '_HEADER.txt').write_text('Master[0]=Oblivion.esm' + chr(10),
                                     encoding='utf-8')
    (exp / 'Oblivion.esm').mkdir()

    class _Ctx:
        export_dir = str(rec)

    got = master_export_dirs(_Ctx())
    assert got == [master_export_dir(export_root(str(rec)), 'Oblivion.esm')]
    assert got != []


def test_creature_projects_are_inherited_from_a_master(tmp_path):
    """A grouped plugin must still inherit its master's creature projects.

    Without them the CREA records reusing a master's creature folders fall
    through to `resolve_creature_race` and ship as BASE SKYRIM creatures.
    """
    from tes5_import.actors.creature_projects import (
        folders_built_by_master, load_projects)

    exp = _fake_group(tmp_path, ['A.esm', 'B.esp'])
    rec = exp / 'My Pack' / 'A.esm'
    rec.mkdir(parents=True)
    (rec / '_HEADER.txt').write_text('Master[0]=Oblivion.esm' + chr(10),
                                     encoding='utf-8')
    master = exp / 'Oblivion.esm'
    master.mkdir()
    from tes5_import.base.artifact_schema import write_artifact
    write_artifact(str(master / 'creature_projects.json'), 'Oblivion.esm',
                   {'rat': {'project_hkx': 'Actors\TES4\rat\p.hkx',
                            'behavior_hkx': 'Actors\TES4\rat\b.hkx',
                            'body_dir': 'Actors\TES4\rat',
                            'skeleton_nif': 'actors\rat\skeleton.nif',
                            'bodies': ['rat.nif']}})

    got, owner_slot = load_projects(str(rec))
    assert 'rat' in got, got
    assert owner_slot == {'rat': 0}

    crea = {'Signature': 'CREA', 'Model.MODL': 'Creatures\\Rat\\Skeleton.NIF'}
    assert folders_built_by_master({'00012345': crea}, got,
                                   owner_slot) == {'rat'}
    assert folders_built_by_master({'01012345': crea}, got,
                                   owner_slot) == set()


# ---------------------------------------------------------------------------
#  CALL SITES, not just helpers
# ---------------------------------------------------------------------------

def test_pack_bsas_is_called_with_the_export_root_not_a_record_dir():
    """`phase_pack` holds both roots; it must pass each to its own slot."""
    import inspect
    import convert

    src = inspect.getsource(convert.phase_pack)
    assert 'export_root=export_root' in src, (
        'phase_pack no longer passes the export ROOT; the output folder '
        'will resolve from a record dir and the pack will abort')
    assert 'export_dir=str(export_dir)' in src, (
        'phase_pack must still pass the RECORD dir for the texture '
        'keep-set -- the two roots are not interchangeable')


def test_book_inam_passes_the_asset_root_to_the_ownership_split():
    """`generate_book_inams` holds both; ownership needs the ASSET root."""
    import inspect
    from asset_convert.ui import book_inam

    src = inspect.getsource(book_inam.generate_book_inams)
    assert 'split_master_owned(models, asset_subdir' in src, (
        'book ownership is being decided on the record dir again -- it '
        'holds no meshes, so every book defers to a master that never '
        'bakes it')


def test_import_main_points_the_soun_converter_at_the_asset_root():
    """The SOUN directory expansion reads `sound/`, which is asset-side."""
    import inspect
    from tes5_import import pipeline as import_main

    src = inspect.getsource(import_main)
    assert 'set_sound_source_dir(str(assets_for(export_dir)))' in src, (
        'set_sound_source_dir is being handed a record dir again -- every '
        'directory-valued SOUN ANAM becomes an unplayable bare path')
