"""Plugins sharing one `scripts/` folder must not wipe each other's scripts.

See: docs/commentary/script_convert.md#wipe-output-dir
"""
from script_convert.context_setup import deploy_static_scripts, prepare_output_dir
from script_convert.ownership import read_owned, sibling_owned, write_owned


def _scripts(tmp_path, names):
    """A `scripts/source` tree holding `names` as .psc with matching .pex."""
    src = tmp_path / 'scripts' / 'source'
    src.mkdir(parents=True)
    for n in names:
        (src / f'{n}.psc').write_text('x', encoding='utf-8')
        (src.parent / f'{n}.pex').write_bytes(b'x')
    return src


def test_a_shared_folder_keeps_the_siblings_scripts(tmp_path):
    """Converting the ESP after the ESM deleted every one of the ESM's scripts."""
    src = _scripts(tmp_path, ['EsmOnly', 'EspOld', 'Both'])
    write_owned(src, 'Mod.esm', ['EsmOnly', 'Both'])
    write_owned(src, 'Mod.esp', ['EspOld', 'Both'])

    shared = prepare_output_dir(str(src), 'Mod.esp')

    assert shared == {'EsmOnly', 'Both'}
    assert sorted(p.stem for p in src.glob('*.psc')) == ['Both', 'EsmOnly']
    assert sorted(p.stem for p in src.parent.glob('*.pex')) == ['Both', 'EsmOnly']


def test_an_unshared_folder_is_still_wiped_whole(tmp_path):
    """Stale scripts a plugin stopped generating must not survive."""
    src = _scripts(tmp_path, ['Stale', 'Unlisted'])
    write_owned(src, 'Oblivion.esm', ['Stale'])

    assert prepare_output_dir(str(src), 'Oblivion.esm') == set()
    assert not list(src.glob('*.psc'))
    assert not list(src.parent.glob('*.pex'))


def test_owned_lists_round_trip(tmp_path):
    """A written list reads back, and a sibling sees it."""
    src = _scripts(tmp_path, [])
    write_owned(src, 'A.esm', ['X', 'Y', 'X'])
    assert read_owned(src, 'A.esm') == {'X', 'Y'}
    assert sibling_owned(src, 'B.esp') == {'X', 'Y'}
    assert sibling_owned(src, 'A.esm') == set()


def _imported_group(tmp_path, kind='folder', online=True):
    from asset_convert.sources import source_registry as registry

    exp = tmp_path / 'export'
    original = tmp_path / 'original'
    if online:
        original.mkdir()
        for name in ('A.esm', 'B.esp'):
            (original / name).write_bytes(b'x')
    for name in ('A.esm', 'B.esp', 'Deleted.esp'):
        registry.put(exp, name, {
            'kind': kind, 'plugin': name, 'group_id': 'pack',
            'group_label': 'Pack', 'group_plugins': ['A.esm', 'B.esp', 'Deleted.esp'],
            'archive_original': str(original), 'plugin_member': name,
        })
        records = registry.record_dir(exp, name)
        records.mkdir(parents=True)
        (records / '_HEADER.txt').write_text('Master[0]=Oblivion.esm\n', encoding='utf-8')
        binary = registry.source_dir(exp, name) / name
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b'x')
    return registry.record_dir(exp, 'A.esm')


def test_removed_members_cannot_keep_stale_scripts_or_master_polyfill(tmp_path):
    records = _imported_group(tmp_path)
    src = _scripts(tmp_path, ['OwnOld', 'Live', 'Shared', 'DeadOnly',
                             'UnregisteredOnly', 'TES4Polyfill'])
    write_owned(src, 'A.esm', ['OwnOld'])
    write_owned(src, 'B.esp', ['Live', 'Shared'])
    write_owned(src, 'Deleted.esp', ['DeadOnly', 'Shared', 'TES4Polyfill'])
    # A re-import can also remove the old member from the registry entirely.
    write_owned(src, 'OldUnregistered.esp', ['UnregisteredOnly'])

    shared = prepare_output_dir(str(src), 'A.esm', export_dir=str(records))
    assert shared == {'Live', 'Shared'}
    assert deploy_static_scripts(str(records), str(src), shared) == []
    assert sorted(p.stem for p in src.glob('*.psc')) == ['Live', 'Shared']
    assert sorted(p.stem for p in src.parent.glob('*.pex')) == ['Live', 'Shared']
    assert sorted(p.name for p in src.parent.glob('*.owned.txt')) == [
        'A.esm.owned.txt', 'B.esp.owned.txt']


def test_archive_and_offline_members_keep_their_owned_scripts(tmp_path):
    for kind, online in (('archive', True), ('folder', False)):
        root = tmp_path / kind
        root.mkdir()
        records = _imported_group(root, kind=kind, online=online)
        src = _scripts(root, ['Live', 'Cached', 'TES4Polyfill'])
        write_owned(src, 'A.esm', [])
        write_owned(src, 'B.esp', ['Live'])
        write_owned(src, 'Deleted.esp', ['Cached', 'TES4Polyfill'])

        shared = prepare_output_dir(str(src), 'A.esm', export_dir=str(records))
        assert shared == {'Live', 'Cached', 'TES4Polyfill'}
        assert deploy_static_scripts(str(records), str(src), shared) == []
        assert sorted(p.stem for p in src.glob('*.psc')) == ['Cached', 'Live', 'TES4Polyfill']
        assert sorted(p.stem for p in src.parent.glob('*.pex')) == ['Cached', 'Live', 'TES4Polyfill']
        assert (src.parent / 'Deleted.esp.owned.txt').is_file()
