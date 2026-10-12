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
import pytest

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BS = chr(92)


def test_whole_mod_buttons_follow_source_selection(monkeypatch):
    import tkinter as tk
    from tkinter import ttk
    from types import SimpleNamespace
    import core.gui.app as gui
    import core.gui.selection as sel

    root = tk.Tk()
    root.withdraw()
    try:
        app = SimpleNamespace(
            scope_var=tk.StringVar(root, 'game'),
            file_var=tk.StringVar(root, 'Oblivion.esm'),
            file_combo=ttk.Combobox(root), all_plugins=[],
            scope_rows={
                'game': {'kind': 'directory', 'path': 'Data'},
                'mod': {'kind': 'mod', 'plugins': ['A.esm', 'B.esp']},
                'textures': {'kind': 'mod', 'plugins': []}},
            run_clicked=lambda: None, run_mod_clicked=lambda: None,
            rebuild_mod_clicked=lambda: None, clear_log=lambda: None,
            cancel_clicked=lambda: None)
        gui.build_run_buttons(app, root, 6)
        monkeypatch.setattr(sel, '_scope_plugins',
                            lambda _app, row, _save: row.get('plugins', []) if row else [])
        buttons = (app.run_mod_btn, app.rebuild_mod_btn)
        for source in ('game', 'mod', 'game', 'mod', 'textures', 'missing'):
            app.scope_var.set(source)
            sel.apply_scope(app, lambda: None, [None], [False])
            if source == 'mod':
                assert all(b.winfo_manager() == 'pack' for b in buttons)
                assert app.run_btn.master.pack_slaves() == [
                    app.run_btn, *buttons, app.cancel_btn.master]
            else:
                assert all(b.winfo_manager() == '' for b in buttons)
    finally:
        root.destroy()


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


def _headers(exp, masters_of):
    """Write `_HEADER.txt` for each {plugin: [masters]} at its record dir."""
    from output_layout import record_dir
    for plugin, masters in masters_of.items():
        rec = record_dir(exp, plugin)
        rec.mkdir(parents=True, exist_ok=True)
        (rec / '_HEADER.txt').write_text(''.join(
            f'Master[{i}]={m}\n' for i, m in enumerate(masters)),
            encoding='utf-8')


def test_one_archive_stem_for_every_plugin_of_a_mod(tmp_path):
    """Packing Morrowind_ob.esp overwrote the ESM's same-named archives.

    A member that masters no other member keeps its own name, even beside a
    registered one that was never converted (Aesthesia's grass plugins).
    """
    from asset_convert.sources.bsa_pack import archive_stem

    exp = _fake_group(tmp_path, ['Mod.esm', 'Mod.esp', 'Addon.esp',
                                 'Solo.esp', 'Unconverted.esp'])
    _headers(exp, {'Mod.esm': ['Oblivion.esm'],
                   'Mod.esp': ['Oblivion.esm', 'Mod.esm'],
                   'Addon.esp': ['Mod.esm'],
                   'Solo.esp': ['Oblivion.esm']})
    assert {archive_stem(p, exp)
            for p in ('Mod.esm', 'Mod.esp', 'Addon.esp')} == {'Mod'}
    assert archive_stem('Solo.esp', exp) == 'Solo'
    assert archive_stem('Oblivion.esm', exp) == 'Oblivion'


def test_pack_sweeps_a_sibling_stems_old_archives(tmp_path):
    """An older build packed each plugin under its own stem; those copies go."""
    from asset_convert.sources.bsa_pack import _sweep_stale

    out = tmp_path / 'My Pack'
    out.mkdir()
    for name in ('Mod.bsa', 'Addon.bsa', 'Addon - Textures.bsa',
                 'Addon_loader.esl', 'Other.bsa'):
        (out / name).write_bytes(b'x')
    _sweep_stale(out, 'Mod', ['Addon'],
                 {'packed': [str(out / 'Mod.bsa')], 'loaders': []})
    assert sorted(p.name for p in out.iterdir()) == ['Mod.bsa', 'Other.bsa']


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
    from core.plugin_masters import export_root, master_export_dir

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


def test_script_stage_finds_a_master_outside_the_mod_folder(tmp_path):
    """Morrowind_ob.esm imported as a mod lost Oblivion.esm: 939 compile errors.

    The script stage joined the master's name onto the record dir's PARENT,
    which for a mod is the mod folder, and skipped the miss silently.
    """
    from script_convert.cross_ref import _export_dirs_with_masters
    from core.plugin_masters import master_chain

    exp = _fake_group(tmp_path, ['Mod.esm', 'Mod.esp'])
    _headers(exp, {'Oblivion.esm': [], 'Mod.esm': ['Oblivion.esm'],
                   'Mod.esp': ['Oblivion.esm', 'Mod.esm']})
    esp = str(exp / 'My Pack' / 'Mod.esp')
    assert master_chain(esp) == ['Oblivion.esm', 'Mod.esm']
    assert _export_dirs_with_masters(esp) == [
        str(exp / 'Oblivion.esm'), str(exp / 'My Pack' / 'Mod.esm'), esp]


def test_plugin_with_armor_keeps_meshes_ticked(tmp_path, monkeypatch):
    """A sibling carrying wearables must not inherit the group's meshes stamp.

    Its mesh-variant plan extends the base one, so skipping its Meshes run
    would silently drop its variants. Unknown binaries fail toward running:
    a wasted reconversion beats silently broken armor.
    """
    from types import SimpleNamespace

    import core.gui.selection as sel

    blob = tmp_path / "Fix.esp"
    blob.write_bytes(b"x")
    sel._sig_cache.clear()
    monkeypatch.setattr("source_paths.resolve_plugin_path",
                        lambda *a: str(blob))
    monkeypatch.setattr("tes4_export.tes4_reader.read_file",
                        lambda *a, **k: (None, [SimpleNamespace(type="ARMO")]))
    app = SimpleNamespace(tes4_var=SimpleNamespace(get=lambda: "D"))
    assert sel.plugin_adds_wearables(app, "Fix.esp") is True

    sel._sig_cache.clear()
    monkeypatch.setattr("tes4_export.tes4_reader.read_file",
                        lambda *a, **k: (None, [SimpleNamespace(type="CELL")]))
    assert sel.plugin_adds_wearables(app, "Fix.esp") is False

    sel._sig_cache.clear()
    monkeypatch.setattr("source_paths.resolve_plugin_path",
                        lambda *a: None)
    assert sel.plugin_adds_wearables(app, "Fix.esp") is True


def test_plugin_with_creatures_keeps_creatures_ticked(tmp_path, monkeypatch):
    """Same guard shape as wearables, for the creature-folder selection."""
    from types import SimpleNamespace

    import core.gui.selection as sel

    blob = tmp_path / "Fix.esp"
    blob.write_bytes(b"x")
    sel._sig_cache.clear()
    monkeypatch.setattr("source_paths.resolve_plugin_path",
                        lambda *a: str(blob))
    monkeypatch.setattr("tes4_export.tes4_reader.read_file",
                        lambda *a, **k: (None, [SimpleNamespace(type="CREA")]))
    app = SimpleNamespace(tes4_var=SimpleNamespace(get=lambda: "D"))
    assert sel.plugin_adds_records(app, "Fix.esp", sel._CREATURE_SIGS) is True

    sel._sig_cache.clear()
    monkeypatch.setattr("tes4_export.tes4_reader.read_file",
                        lambda *a, **k: (None, [SimpleNamespace(type="CELL")]))
    assert sel.plugin_adds_records(app, "Fix.esp", sel._CREATURE_SIGS) is False


def test_group_members_do_not_pack_by_default(tmp_path, monkeypatch):
    """Pack steps cover the shared folder, so they are not pre-ticked.

    Ticked per member they would pack the folder once per plugin: one zip
    overwritten uselessly and per-stem BSAs duplicated under new names.
    """
    from types import SimpleNamespace

    import core.gui.selection as sel

    _fake_group(tmp_path, ['A.esm', 'B.esp'])
    monkeypatch.setattr(sel, 'EXPORT_DIR',
                        tmp_path / 'export')

    class Var:
        def __init__(self):
            self.v = True

        def set(self, x):
            self.v = x

        def get(self):
            return self.v

    app = SimpleNamespace(
        step_vars={k: Var() for k in (
            'export', 'extract', 'meshes', 'speedtrees', 'creatures',
            'import', 'sounds', 'scripts', 'pack', 'pack_zip')},
        upgrade_btn=SimpleNamespace(configure=lambda **k: None),
        set_upgrade_tip=lambda *a: None,
        update_run_btn=lambda *a: None,
        tes4_var=SimpleNamespace(get=lambda: 'D'),
        plan_applied=set())
    sel._apply_plan_state(app, {'never_run': True, 'steps': [],
                                'current': 'x'}, 'B.esp', True)
    assert app.step_vars['pack'].get() is False
    assert app.step_vars['pack_zip'].get() is False
    assert app.step_vars['export'].get() is True


@pytest.mark.parametrize('signature,stamp,meshes,creatures', [
    ('CELL', 'current', False, False),
    ('ARMO', 'current', True, False),
    ('CLOT', 'current', True, False),
    ('HAIR', 'current', True, False),
    ('CREA', 'current', True, True),
    ('ACRE', 'current', False, True),
    ('STAT', 'current', True, False),
    ('REFR', 'current', True, False),
    (None, 'current', True, True),
    ('CELL', '0.001', True, True),
])
def test_next_plugin_selects_only_needed_steps(tmp_path, monkeypatch,
                                             signature, stamp, meshes,
                                             creatures):
    """Selection reuses completed assets while retaining a sibling's additions."""
    import struct
    from types import SimpleNamespace

    import core.gui.selection as sel
    import version as v

    exp = _fake_group(tmp_path, ['A.esm', 'B.esp'])
    monkeypatch.setattr(v, 'SCRIPT_DIR', tmp_path)
    monkeypatch.setattr(v, 'STATE_FILE', tmp_path / '.conversion_state.json')
    monkeypatch.setattr(sel, 'EXPORT_DIR', exp)
    at = v.current_version() if stamp == 'current' else stamp
    for step in ('extract', 'meshes', 'speedtrees', 'creatures'):
        v.record_step_run(step, 'A.esm', version=at)

    source = tmp_path / 'B.esp'
    if signature:
        sig = signature.encode('ascii')
        header = b'TES4' + struct.pack('<IIII', 18, 0, 0, 0)
        header += b'HEDR' + struct.pack('<H', 12) + bytes(12)
        group = b'GRUP' + struct.pack('<I', 40) + sig + bytes(8)
        record = sig + struct.pack('<IIII', 0, 0, 1, 0)
        source.write_bytes(header + group + record)
    else:
        source.write_bytes(b'unreadable')
    monkeypatch.setattr('source_paths.resolve_plugin_path', lambda *a: str(source))
    monkeypatch.setattr(sel, '_sig_cache', {})

    state = {k: True for k in ('export', 'extract', 'meshes', 'speedtrees',
                              'creatures', 'import_', 'sounds', 'scripts',
                              'pack', 'pack_zip')}
    variables = {k: SimpleNamespace(get=lambda k=k: state[k],
                                   set=lambda value, k=k: state.__setitem__(k, value))
                 for k in state}
    app = SimpleNamespace(step_vars=variables,
                          tes4_var=SimpleNamespace(get=lambda: ''),
                          upgrade_btn=SimpleNamespace(configure=lambda **k: None),
                          set_upgrade_tip=lambda *a: None,
                          update_run_btn=lambda: None, log=lambda *a: None)
    sel._apply_plan_state(app, {'never_run': True}, 'B.esp', True)

    assert state['meshes'] is meshes
    assert state['creatures'] is creatures
    assert state['extract'] is (stamp != 'current')
    assert state['speedtrees'] is (stamp != 'current')
    assert state['pack'] is False and state['pack_zip'] is False
    assert all(state[k] for k in ('export', 'import_', 'sounds', 'scripts'))


def test_import_main_master_dirs_match_load_master_export(tmp_path):
    """`master_export_dirs` exists to mirror `load_master_export` exactly.

    Its own docstring says so, and it stopped being true: it returned [] for
    every grouped plugin, so the voice-type adoption loop never ran and every
    actor fell through to the Imperial default.
    """
    from tes5_import.pipeline import master_export_dirs
    from core.plugin_masters import export_root, master_export_dir

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


def test_imported_optimization_opt_out_keeps_default_checkboxes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import core.gui.selection as sel
    exp = _fake_group(tmp_path, ['A.esm', 'B.esp'])
    monkeypatch.setattr(sel, 'EXPORT_DIR', exp)
    state = {key: True for key in ('meshes', 'creatures', 'pack', 'pack_zip')}
    app = SimpleNamespace(
        step_vars={key: SimpleNamespace(get=lambda key=key: state[key],
                   set=lambda value, key=key: state.__setitem__(key, value))
                   for key in state},
        imported_mod_optimizations_var=SimpleNamespace(get=lambda: False),
        upgrade_btn=SimpleNamespace(configure=lambda **kw: None),
        set_upgrade_tip=lambda *args: None)
    sel._apply_plan_state(app, {'never_run': True}, 'B.esp', True)
    assert all(state.values())


def test_imported_single_plugin_preserves_steps_and_parallax(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import core.gui.runner as runner
    from convert_cli import build_parser
    exp = _fake_group(tmp_path, ['A.esm', 'B.esp'])
    monkeypatch.setattr(runner, 'EXPORT_DIR', exp)
    monkeypatch.setattr(runner, 'navmesh_pins_dir', lambda: 'pins')
    enabled = [True]
    app = SimpleNamespace(
        pack_default_var=SimpleNamespace(get=lambda: False),
        imported_mod_optimizations_var=SimpleNamespace(get=lambda: enabled[0]),
        navmesh_gen_var=SimpleNamespace(get=lambda: 'corridor'),
        tes4_encoding_var=SimpleNamespace(get=lambda: 'auto'),
        winding_on=lambda: False,
        parallax_var=SimpleNamespace(get=lambda: True),
        tex_only_var=SimpleNamespace(get=lambda: True))
    steps = ['export', 'extract', 'meshes', 'speedtrees', 'creatures',
             'import_', 'sounds', 'scripts']
    cmds = runner.pipeline_argv(app, 'B.esp', 'out', steps, None)
    args = [build_parser().parse_args(cmd[3:]) for cmd in cmds]
    assert len(args) == len(steps)
    assert args[2].parallax is True and args[2].textures_only is True
    enabled[0] = False
    # This is upstream's original combined command for the default selection.
    legacy = runner.pipeline_argv(app, 'B.esp', 'out', steps, None)
    assert len(legacy) == 1
    parsed = build_parser().parse_args(legacy[0][3:])
    from convert_cli import selected_steps
    assert selected_steps(parsed) == [key.rstrip('_') for key in steps]


def test_mod_run_plans_shared_steps_once(tmp_path, monkeypatch):
    """One mod-wide run converts the shared tree once, not per plugin.

    The first plugin keeps the shared steps, later siblings drop the ones
    the mod already ran; the pack runs once at the end under the ESM.
    Per-plugin record steps are untouched.
    """
    from types import SimpleNamespace

    import version as v
    import core.gui.selection as sel

    exp = _fake_group(tmp_path, ['A.esm', 'B.esp'])
    monkeypatch.setattr(v, 'SCRIPT_DIR', tmp_path)
    monkeypatch.setattr(v, 'STATE_FILE',
                        tmp_path / '.conversion_state.json')
    monkeypatch.setattr(sel, 'EXPORT_DIR', exp)
    monkeypatch.setattr(sel, 'plugin_adds_records',
                        lambda app, name, sigs: name == 'A.esm')
    from asset_convert.sources import source_registry
    for name in ['A.esm', 'B.esp']:
        records = source_registry.record_dir(exp, name)
        records.mkdir(parents=True)
        (records / '_HEADER.txt').write_text('Flags=0\n', encoding='utf-8')
    v.record_step_run('meshes', 'A.esm', version=v.current_version())
    v.record_step_run('extract', 'A.esm', version=v.current_version())

    app = SimpleNamespace(tes4_var=SimpleNamespace(get=lambda: 'D'),
                          pack_default_var=SimpleNamespace(get=lambda: True),
                          step_widgets={})
    runs, pack_with, pack_steps = sel.plan_mod_run(app, ['B.esp', 'A.esm'])
    by_name = dict(runs)
    assert 'meshes' in by_name['A.esm']
    assert 'meshes' not in by_name['B.esp']
    assert 'extract' not in by_name['B.esp']
    assert 'export' in by_name['B.esp'] and 'import_' in by_name['B.esp']
    assert 'pack' not in by_name['A.esm']
    assert pack_with == 'A.esm'
    assert pack_steps == ['pack', 'pack_zip']


def test_rebuild_mod_ignores_history_and_selected_member_limits(tmp_path, monkeypatch):
    """Rebuild a current master and patch even when the selected patch is empty."""
    import struct
    from types import SimpleNamespace

    import version as v
    import core.gui.selection as sel
    from core.gui.config import STEPS

    exp = _fake_group(tmp_path, ['A.esm', 'B.esp'])
    data = tmp_path / 'Data'
    data.mkdir()
    hedr = b'HEDR' + struct.pack('<HfII', 12, 1.0, 0, 0)
    for name, masters in [('A.esm', []), ('B.esp', ['A.esm'])]:
        payload = hedr
        for master in masters:
            raw = master.encode('ascii') + b'\0'
            payload += b'MAST' + struct.pack('<H', len(raw)) + raw
        (data / name).write_bytes(
            b'TES4' + struct.pack('<I', len(payload)) + bytes(16) + payload)
        from asset_convert.sources import source_registry
        retained = source_registry.source_dir(exp, name) / name
        retained.parent.mkdir(parents=True, exist_ok=True)
        retained.write_bytes((data / name).read_bytes())
    monkeypatch.setattr(v, 'SCRIPT_DIR', tmp_path)
    monkeypatch.setattr(v, 'STATE_FILE', tmp_path / '.conversion_state.json')
    monkeypatch.setattr(sel, 'EXPORT_DIR', exp)
    monkeypatch.setattr(sel, 'plugin_adds_records',
                        lambda app, name, sigs: name == 'A.esm')
    for name in ['A.esm', 'B.esp']:
        for key, *_ in STEPS:
            v.record_step_run(key, name, version=v.current_version())
    disabled = SimpleNamespace(cget=lambda key: 'disabled')
    app = SimpleNamespace(tes4_var=SimpleNamespace(get=lambda: str(data)),
                          pack_default_var=SimpleNamespace(get=lambda: True),
                          step_widgets={'import_': (disabled,)})

    ordinary_runs, _, _ = sel.plan_mod_run(app, ['B.esp', 'A.esm'])
    assert all(not {'export', 'import_', 'scripts'}.intersection(steps)
               for name, steps in ordinary_runs)
    runs, pack_with, pack_steps = sel.plan_mod_run(
        app, ['B.esp', 'A.esm'], rebuild=True)
    assert [name for name, steps in runs] == ['A.esm', 'B.esp']
    assert dict(runs)['A.esm'] == [key for key, *_ in STEPS
                                 if key not in ('pack', 'pack_zip')]
    assert dict(runs)['B.esp'] == ['export', 'import_', 'sounds', 'scripts']
    assert (pack_with, pack_steps) == ('A.esm', ['pack', 'pack_zip'])
    app.pack_default_var = SimpleNamespace(get=lambda: False)
    assert sel.plan_mod_run(app, ['B.esp', 'A.esm'], rebuild=True)[1:] == (None, [])


def test_rebuild_mod_commands_export_first_and_keep_normal_import_options(monkeypatch):
    """Rebuild changes scheduling, while Import keeps its usual cache handling."""
    from types import SimpleNamespace
    import core.gui.runner as runner

    monkeypatch.setattr(runner, 'navmesh_pins_dir', lambda: 'pins')
    app = SimpleNamespace(navmesh_gen_var=SimpleNamespace(get=lambda: 'corridor'),
                          tes4_encoding_var=SimpleNamespace(get=lambda: 'cp1251'),
                          winding_on=lambda: True,
                          parallax_var=SimpleNamespace(get=lambda: False))
    runs = [('A.esm', ['export', 'meshes', 'import_', 'scripts']),
            ('B.esp', ['export', 'import_', 'scripts'])]
    cmds = runner.mod_run_argv(app, runs, 'A.esm', ['pack', 'pack_zip'],
                               'output', rebuild=True)
    jobs = [(cmd[3], cmd[cmd.index('-f') + 1]) for cmd in cmds]
    assert jobs == [('--export-only', 'A.esm'), ('--export-only', 'B.esp'),
                    ('--meshes-only', 'A.esm'),
                    ('--meshes-only', 'A.esm'),
                    ('--import-only', 'A.esm'), ('--import-only', 'B.esp'),
                    ('--scripts-only', 'A.esm'), ('--scripts-only', 'B.esp'),
                    ('--pack-only', 'A.esm'), ('--pack-zip-only', 'A.esm')]
    for name in ['A.esm', 'B.esp']:
        assert runner.build_cmd(app, 'import_', name, 'output') in cmds
    from convert_cli import build_parser
    assert build_parser().parse_args(cmds[2][3:]).plugin_assets_only is True
    assert build_parser().parse_args(cmds[3][3:]).plugin_assets_only is False
    ordinary = runner.mod_run_argv(app, runs, None, [], 'output')
    assert [(cmd[3], cmd[cmd.index('-f') + 1]) for cmd in ordinary] == [
        ('--export-only', 'A.esm'), ('--export-only', 'B.esp'),
        ('--meshes-only', 'A.esm'),
        ('--meshes-only', 'A.esm'),
        ('--import-only', 'A.esm'), ('--scripts-only', 'A.esm'),
        ('--import-only', 'B.esp'),
        ('--scripts-only', 'B.esp')]

def test_mod_runs_finalize_textures_once_after_all_meshes_with_parallax(monkeypatch):
    from types import SimpleNamespace
    from convert_cli import build_parser
    import core.gui.runner as runner

    monkeypatch.setattr(runner, 'navmesh_pins_dir', lambda: 'pins')
    app = SimpleNamespace(navmesh_gen_var=SimpleNamespace(get=lambda: 'corridor'),
                          tes4_encoding_var=SimpleNamespace(get=lambda: 'cp1251'),
                          winding_on=lambda: False,
                          parallax_var=SimpleNamespace(get=lambda: True),
                          tex_only_var=SimpleNamespace(get=lambda: True))
    runs = [('A.esm', ['meshes', 'import_']), ('B.esp', ['meshes', 'import_'])]
    previous_token = None
    for rebuild in (False, True):
        cmds = runner.mod_run_argv(app, runs, 'A.esm', ['pack'], 'output',
                                   rebuild=rebuild,
                                   texture_plugins=['A.esm', 'B.esp', 'C.esp'])
        args = [build_parser().parse_args(cmd[3:]) for cmd in cmds]
        finalizers = [i for i, a in enumerate(args) if a.shared_texture_plugins]
        mesh_jobs = [i for i, a in enumerate(args) if a.defer_textures]
        assert len(finalizers) == 1 and len(mesh_jobs) == 2
        final = finalizers[0]
        assert max(mesh_jobs) < final < len(args) - 1
        assert all(i > final for i, cmd in enumerate(cmds) if '--import-only' in cmd)
        assert args[final].shared_texture_plugins == ['A.esm', 'B.esp', 'C.esp']
        for i in mesh_jobs + finalizers:
            assert args[i].parallax and args[i].textures_only
        assert all(args[i].plugin_assets_only for i in mesh_jobs)
        tokens = {args[i].mesh_reuse_token for i in mesh_jobs}
        assert len(tokens) == 1 and None not in tokens
        token = tokens.pop()
        assert token != previous_token
        previous_token = token

def test_mod_runs_process_shared_effects_once_and_keep_each_plugins_voices(monkeypatch):
    from types import SimpleNamespace
    from convert_cli import build_parser
    import core.gui.runner as runner

    monkeypatch.setattr(runner, 'navmesh_pins_dir', lambda: 'pins')
    app = SimpleNamespace(navmesh_gen_var=SimpleNamespace(get=lambda: 'corridor'),
                          tes4_encoding_var=SimpleNamespace(get=lambda: 'cp1251'))
    runs = [('A.esm', ['sounds']), ('B.esp', ['sounds']), ('Fix.esp', ['sounds'])]
    for rebuild in (False, True):
        args = [build_parser().parse_args(cmd[3:])
                for cmd in runner.mod_run_argv(app, runs, None, [], 'output',
                                               rebuild=rebuild)]
        assert [a.files for a in args] == [['A.esm'], ['B.esp'], ['Fix.esp']]
        assert [a.skip_shared_sounds for a in args] == [False, True, True]




def test_deleted_mod_members_disappear_from_selection_and_stale_run_plan(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from asset_convert.sources import source_registry as registry
    import core.gui.selection as sel

    exp = _fake_group(tmp_path, ['A.esm', 'Deleted.esp', 'Cached.esp'])
    binary = registry.source_dir(exp, 'A.esm') / 'A.esm'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'x')
    cached = registry.record_dir(exp, 'Cached.esp')
    cached.mkdir(parents=True)
    (cached / '_HEADER.txt').write_text('Flags=0\n', encoding='utf-8')
    # A directory left by a failed Import is not an exported plugin.
    registry.record_dir(exp, 'Deleted.esp').mkdir()
    assert registry.all_sources(exp)[0]['plugins'] == ['A.esm', 'Cached.esp']
    monkeypatch.setattr(sel, 'EXPORT_DIR', exp)
    monkeypatch.setattr(sel, 'plugin_adds_records', lambda *a: False)
    monkeypatch.setattr(sel.version_info, 'steps_run_at', lambda *a, **k: {})
    app = SimpleNamespace(tes4_var=SimpleNamespace(get=lambda: ''),
                          pack_default_var=SimpleNamespace(get=lambda: True),
                          step_widgets={})
    for rebuild in (False, True):
        runs, owner, _ = sel.plan_mod_run(
            app, ['A.esm', 'Deleted.esp', 'Cached.esp'], rebuild=rebuild)
        assert 'Deleted.esp' not in dict(runs)
        assert owner != 'Deleted.esp'
    # Removal is reversible: restoring a source makes it available again.
    (binary.parent / 'Deleted.esp').write_bytes(b'x')
    assert registry.all_sources(exp)[0]['plugins'] == [
        'A.esm', 'Cached.esp', 'Deleted.esp']


@pytest.mark.parametrize('rebuild', [False, True])
@pytest.mark.parametrize('optimized', [False, True])
def test_failed_export_skips_only_failed_plugin_and_dependents(tmp_path, monkeypatch,
                                                            rebuild, optimized):
    import queue
    import struct
    import threading
    from types import SimpleNamespace
    from asset_convert.sources import source_registry as registry
    import core.gui.runner as runner

    names = ['A.esm', 'Bad.esp', 'Dependent.esp', 'Good.esp']
    exp = _fake_group(tmp_path, names)
    for name in names:
        masters = ['Bad.esp'] if name == 'Dependent.esp' else []
        payload = b'HEDR' + struct.pack('<HfII', 12, 1.0, 0, 0)
        for master in masters:
            raw = master.encode('ascii') + b'\0'
            payload += b'MAST' + struct.pack('<H', len(raw)) + raw
        binary = registry.source_dir(exp, name) / name
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b'TES4' + struct.pack('<I', len(payload)) + bytes(16) + payload)
    monkeypatch.setattr(runner, 'EXPORT_DIR', exp)
    monkeypatch.setattr(runner, 'navmesh_pins_dir', lambda: 'pins')
    app = SimpleNamespace(
        cancel_evt=threading.Event(), tes4_var=SimpleNamespace(get=lambda: ''),
        imported_mod_optimizations_var=SimpleNamespace(get=lambda: optimized),
        navmesh_gen_var=SimpleNamespace(get=lambda: 'corridor'),
        tes4_encoding_var=SimpleNamespace(get=lambda: 'auto'),
        winding_on=lambda: False, parallax_var=SimpleNamespace(get=lambda: False))
    out = tmp_path / 'output'
    out.mkdir()
    executed = []

    def convert(cmd, *args, **kwargs):
        name = cmd[cmd.index('-f') + 1]
        executed.append(cmd)
        if '--export-only' in cmd and name == 'Bad.esp':
            return 1
        if '--import-only' in cmd:
            (out / name).write_bytes(b'converted')
        return 0

    monkeypatch.setattr(runner, 'run_process', convert)
    runs = [(name, ['export', 'meshes', 'import_', 'sounds', 'scripts']) for name in names]
    cmds = runner.mod_run_argv(app, runs, 'A.esm', ['pack', 'pack_zip'],
                               str(out), rebuild=rebuild)
    assert runner.run_commands(app, cmds, queue.Queue(), {}, 99) == 1
    assert sorted(p.name for p in out.iterdir()) == ['A.esm', 'Good.esp']
    assert not any('--pack-only' in c or '--pack-zip-only' in c for c in executed)
    for cmd in executed:
        name = cmd[cmd.index('-f') + 1]
        assert name != 'Dependent.esp'
        assert name != 'Bad.esp' or '--export-only' in cmd
        if '--shared-textures-only' in cmd:
            assert cmd[cmd.index('--shared-textures-only') + 1:] == ['A.esm', 'Good.esp']


