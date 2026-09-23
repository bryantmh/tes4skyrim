"""Mod-archive ingest: layout rule, path safety, routing parity, idempotence.

Everything here builds synthetic archives in a tmp_path, so the whole module
runs in a couple of seconds and needs no real mod on disk.
"""
import os
import struct
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.sources import archive
from asset_convert.sources import bsa_extract
from asset_convert.sources import mod_ingest
from asset_convert.sources import source_registry


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _tes4_plugin_bytes(masters=()):
    """A minimal but structurally real TES4 plugin header.

    get_masters_from_binary parses this, so the master-detection test needs it
    to be genuine rather than a stub string.
    """
    sub = b''
    sub += b'HEDR' + struct.pack('<H', 12) + struct.pack('<fiI', 1.0, 0, 1)
    for m in masters:
        raw = m.encode('ascii') + b'\x00'
        sub += b'MAST' + struct.pack('<H', len(raw)) + raw
        sub += b'DATA' + struct.pack('<H', 8) + struct.pack('<Q', 0)
    return b'TES4' + struct.pack('<I', len(sub)) + b'\x00' * 12 + sub


def _zip(path, entries):
    """entries: {member_path: bytes}"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _mod_entries(prefix=''):
    """A typical small mod payload under an optional prefix."""
    p = prefix
    return {
        f'{p}MyMod.esp': _tes4_plugin_bytes(),
        f'{p}Meshes/clutter/thing.nif': b'NIF-DATA',
        f'{p}Textures/clutter/thing.dds': b'DDS-DATA',
        f'{p}Sound/fx/noise.wav': b'WAV-DATA',
        f'{p}DistantLOD/Tamriel_0_0.lod': b'LOD-DATA',
        f'{p}readme.txt': b'hello',
    }


# ---------------------------------------------------------------------------
#  1. Layout rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('prefix,expect_root', [
    ('', []),                                   # Elsweyr: payload at root
    ('MyMod/Data/', ['MyMod/Data']),            # TWMP: nested Data
    ('A/B/Data/', ['A/B/Data']),                # deeply nested Data
    ('Data/', ['Data']),                        # Data at top level
])
def test_layout_rule(tmp_path, prefix, expect_root):
    arc = _zip(tmp_path / 'mod.zip', _mod_entries(prefix))
    man = mod_ingest.inspect(arc)
    assert man.payload_root == expect_root
    assert man.plugins == ['MyMod.esp']
    assert man.counts.get('meshes') == 1
    assert man.counts.get('textures') == 1


def test_shallowest_data_folder_wins(tmp_path):
    entries = _mod_entries('Data/')
    entries['Docs/Data/notes.txt'] = b'x'
    arc = _zip(tmp_path / 'mod.zip', entries)
    man = mod_ingest.inspect(arc)
    assert man.payload_root == ['Data']
    assert man.ambiguous_data == []


def test_bain_subpackages_are_all_payload_roots(tmp_path):
    """A complex BAIN archive installs every sub-package, in name order.

    Tamriel Data (HD) ships `00 Data Files` and `01 Data Files - Normal Maps`
    and no folder named Data; treating it as archive-root put all 46,863 files
    under `misc/` where no asset stage could see them.
    See: docs/commentary/asset_convert_mod_ingest.md#payload-roots
    """
    arc = _zip(tmp_path / 'mod.zip', {
        '00 Core/MyMod.esp': _tes4_plugin_bytes(),
        '00 Core/Meshes/a.nif': b'N',
        '01 Textures - 4X/Textures/b.dds': b'D',
        'ReadMe-Quests-Guide/readme.txt': b'x',
    })
    man = mod_ingest.inspect(arc)
    assert man.payload_root == ['00 Core', '01 Textures - 4X']
    assert man.plugins == ['MyMod.esp']
    assert man.counts.get('meshes') == 1
    assert man.counts.get('textures') == 1


def test_numbers_do_not_make_a_bain_subpackage(tmp_path):
    """BAIN never parses the numeric prefix; structure alone decides.

    A numbered folder holding only docs is not a sub-package, and an
    unnumbered folder holding a data dir is.
    """
    arc = _zip(tmp_path / 'mod.zip', {
        'Core Files/Meshes/a.nif': b'N',
        '99 Documentation/manual.txt': b'x',
    })
    assert mod_ingest.inspect(arc).payload_root == ['Core Files']


def test_loose_data_at_root_beats_subpackages(tmp_path):
    """A simple package stays simple even beside a would-be sub-package."""
    arc = _zip(tmp_path / 'mod.zip', {
        'MyMod.esp': _tes4_plugin_bytes(),
        'Meshes/a.nif': b'N',
        '01 Optional/Textures/b.dds': b'D',
    })
    assert mod_ingest.inspect(arc).payload_root == []


def test_equal_depth_data_folders_reported_ambiguous(tmp_path):
    entries = {
        'A/Data/MyMod.esp': _tes4_plugin_bytes(),
        'A/Data/Meshes/a.nif': b'N',
        'B/Data/Other.esp': _tes4_plugin_bytes(),
    }
    arc = _zip(tmp_path / 'mod.zip', entries)
    man = mod_ingest.inspect(arc)
    assert len(man.ambiguous_data) == 2


def test_archive_without_plugin_is_accepted_as_asset_only(tmp_path):
    """A texture replacer has no plugin and is still perfectly convertible."""
    arc = _zip(tmp_path / 'tex.zip', {'Textures/a.dds': b'D'})
    man = mod_ingest.inspect(arc)
    assert man.plugins == []
    assert man.asset_only is True
    assert man.counts.get('textures') == 1


# ---------------------------------------------------------------------------
#  2. Path traversal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('bad', [
    '../../evil.dll',
    'foo/../../evil.dll',
    '/abs/evil.dll',
    'C:/evil.dll',
    'C:evil.dll',
    '//server/share/evil.dll',
    '..\\..\\evil.dll',
])
def test_unsafe_paths_rejected(bad):
    with pytest.raises(archive.UnsafeMemberError):
        archive.safe_relpath(bad)


@pytest.mark.parametrize('good,want', [
    ('Data/meshes/a.nif', 'Data/meshes/a.nif'),
    ('a\\b\\c.nif', 'a/b/c.nif'),
    ('./x/./y.nif', 'x/y.nif'),
    ('a/b/../c.nif', 'a/c.nif'),
])
def test_safe_paths_normalized(good, want):
    """safe_relpath returns the forward-slashed, dot-collapsed relative path."""
    assert archive.safe_relpath(good) == want


def test_safe_join_stays_inside_destination(tmp_path):
    with pytest.raises(archive.UnsafeMemberError):
        archive.safe_join(tmp_path, '../escape.txt')
    inside = archive.safe_join(tmp_path, 'a/b.txt')
    assert tmp_path.resolve() in inside.parents


def test_traversal_member_never_written(tmp_path):
    """A hostile member must not appear anywhere, inside or outside dest."""
    arc = tmp_path / 'evil.zip'
    with zipfile.ZipFile(arc, 'w') as zf:
        zf.writestr('MyMod.esp', _tes4_plugin_bytes())
        zf.writestr('Meshes/ok.nif', b'OK')
        zf.writestr('../../evil.dll', b'PWNED')
    dest = tmp_path / 'out'
    archive.extract_all(arc, dest)
    assert not (tmp_path.parent / 'evil.dll').exists()
    assert not list(dest.rglob('evil.dll'))
    assert (dest / 'Meshes' / 'ok.nif').is_file()


# ---------------------------------------------------------------------------
#  3. Category routing parity with the BSA extractor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('path,expect', [
    (r'meshes\architecture\a.nif', 'meshes/architecture/a.nif'),
    (r'Meshes\Landscape\LOD\60.nif', 'meshes/Landscape/LOD/60.nif'),
    (r'MESHES\x.nif', 'meshes/x.nif'),
    (r'Textures\LandscapeLOD\g.dds', 'textures/LandscapeLOD/g.dds'),
    (r'sound\voice\a.mp3', 'sound/voice/a.mp3'),
    (r'trees\tree.spt', 'trees/tree.spt'),
    (r'DistantLOD\Tamriel_0_0.lod', 'misc/DistantLOD/Tamriel_0_0.lod'),
    (r'meshesnotacat\x.nif', 'misc/meshesnotacat/x.nif'),
])
def test_categorize_matches_bsa_layout(path, expect):
    assert bsa_extract.categorize(path) == expect


def test_lip_files_excluded_like_bsa(tmp_path, capsys):
    entries = _mod_entries()
    entries['Sound/voice/line.lip'] = b'LIP'
    arc = _zip(tmp_path / 'mod.zip', entries)
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)
    assert not list(source_registry.asset_root(export, 'MyMod.esp').rglob('*.lip'))
    assert (source_registry.asset_root(export, 'MyMod.esp')
            / 'sound' / 'fx' / 'noise.wav').is_file()


# ---------------------------------------------------------------------------
#  4. Ingest end to end
# ---------------------------------------------------------------------------

def test_ingest_produces_bsa_shaped_tree(tmp_path):
    arc = _zip(tmp_path / 'mod.zip', _mod_entries('MyMod/Data/'))
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)

    root = source_registry.asset_root(export, 'MyMod.esp')
    assert (root / 'meshes' / 'clutter' / 'thing.nif').read_bytes() == b'NIF-DATA'
    assert (root / 'textures' / 'clutter' / 'thing.dds').read_bytes() == b'DDS-DATA'
    assert (root / 'sound' / 'fx' / 'noise.wav').is_file()
    assert (root / 'misc' / 'DistantLOD' / 'Tamriel_0_0.lod').is_file()
    # The plugin binary is kept out of the asset tree.
    assert (root / '_source' / 'MyMod.esp').is_file()
    assert not (root / 'meshes' / 'MyMod.esp').exists()


def test_registry_records_and_resolves(tmp_path):
    arc = _zip(tmp_path / 'mod.zip', _mod_entries())
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)

    entry = source_registry.get(export, 'MyMod.esp')
    assert entry and entry['kind'] == 'archive'
    assert entry['payload_root'] == []
    binary = source_registry.plugin_binary(export, 'MyMod.esp')
    assert binary and binary.is_file()
    # Case-insensitive lookup: the CLI's -f and the GUI's combo differ in case.
    assert source_registry.get(export, 'mymod.esp') is not None
    assert source_registry.plugins(export) == ['MyMod.esp']


def test_unregistered_plugin_resolves_to_none(tmp_path):
    """The additive guarantee: an empty registry changes nothing."""
    export = tmp_path / 'export'
    export.mkdir()
    assert source_registry.get(export, 'Oblivion.esm') is None
    assert source_registry.plugin_binary(export, 'Oblivion.esm') is None
    assert source_registry.plugins(export) == []


def test_ingest_is_idempotent(tmp_path):
    arc = _zip(tmp_path / 'mod.zip', _mod_entries())
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)
    nif = (source_registry.asset_root(export, 'MyMod.esp')
           / 'meshes' / 'clutter' / 'thing.nif')
    stamp = nif.stat().st_mtime_ns

    res = mod_ingest.ingest(arc, export, log=lambda *a: None)
    assert res['MyMod.esp']['cached'] is True
    assert nif.stat().st_mtime_ns == stamp


def test_multiple_plugins_share_one_payload(tmp_path):
    entries = _mod_entries('TWMP/Data/')
    entries['TWMP/Data/MapMarkers.esp'] = _tes4_plugin_bytes()
    arc = _zip(tmp_path / 'twmp.zip', entries)
    export = tmp_path / 'export'
    res = mod_ingest.ingest(arc, export, log=lambda *a: None)

    assert set(res) == {'MyMod.esp', 'MapMarkers.esp'}

    # ONE asset tree, shared -- not a copy or a hard-link farm per plugin.
    roots = {source_registry.asset_root(export, n)
             for n in ('MyMod.esp', 'MapMarkers.esp')}
    assert len(roots) == 1
    root = roots.pop()
    assert (root / 'meshes' / 'clutter' / 'thing.nif').is_file()

    # The payload exists exactly once on disk.
    assert len(list(export.rglob('meshes/clutter/thing.nif'))) == 1

    # Both binaries live in that one tree's _source/, and each plugin keeps
    # its own identity.
    for name in ('MyMod.esp', 'MapMarkers.esp'):
        assert (root / '_source' / name).is_file()
        assert source_registry.record_dir(export, name) == root / name

    # Both share one group so the GUI can offer them as one source.
    gids = {source_registry.get(export, n)['group_id']
            for n in ('MyMod.esp', 'MapMarkers.esp')}
    assert len(gids) == 1

    # group_plugins records the whole archive, so a later partial import
    # cannot silently disagree with a full one.
    for name in ('MyMod.esp', 'MapMarkers.esp'):
        assert set(source_registry.get(export, name)['group_plugins']) == {
            'MyMod.esp', 'MapMarkers.esp'}


@pytest.mark.parametrize('spelling', [
    'MyMod.esp',                       # payload-relative (what inspect lists)
    'TWMP/Data/MyMod.esp',             # full archive path (what a user types)
    'TWMP\\Data\\MyMod.esp',           # ...with Windows separators
    'mymod.esp',                       # bare name, wrong case
])
def test_plugin_member_accepts_any_spelling(tmp_path, spelling):
    """--plugin-member must not fail on a technicality: the path a listing
    prints and the path inspect stores differ by the payload root."""
    entries = _mod_entries('TWMP/Data/')
    entries['TWMP/Data/Other.esp'] = _tes4_plugin_bytes()
    arc = _zip(tmp_path / 'twmp.zip', entries)
    export = tmp_path / 'export'
    res = mod_ingest.ingest(arc, export, plugin_members=[spelling],
                            log=lambda *a: None)
    assert set(res) == {'MyMod.esp'}


def test_unknown_plugin_member_lists_alternatives(tmp_path):
    arc = _zip(tmp_path / 'mod.zip', _mod_entries())
    with pytest.raises(mod_ingest.IngestError, match='available: MyMod.esp'):
        mod_ingest.ingest(arc, tmp_path / 'export',
                          plugin_members=['NotHere.esp'],
                          log=lambda *a: None)


def test_loose_files_win_over_bsa(tmp_path):
    """The engine's own precedence rule."""
    bsa = _make_bsa(tmp_path / 'MyMod.bsa',
                    {r'meshes\clutter\thing.nif': b'FROM-BSA',
                     r'meshes\only\in\bsa.nif': b'BSA-ONLY'})
    entries = {
        'MyMod.esp': _tes4_plugin_bytes(),
        'MyMod.bsa': bsa.read_bytes(),
        'Meshes/clutter/thing.nif': b'FROM-LOOSE',
    }
    arc = _zip(tmp_path / 'mod.zip', entries)
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)

    root = source_registry.asset_root(export, 'MyMod.esp')
    assert (root / 'meshes' / 'clutter' / 'thing.nif').read_bytes() == b'FROM-LOOSE'
    # ...and BSA-only content still survives.
    assert (root / 'meshes' / 'only' / 'in' / 'bsa.nif').read_bytes() == b'BSA-ONLY'


def test_nested_archive_is_ingested(tmp_path):
    inner = _zip(tmp_path / 'inner.zip',
                 {'Meshes/nested/deep.nif': b'NESTED'})
    entries = _mod_entries()
    entries['inner.zip'] = inner.read_bytes()
    arc = _zip(tmp_path / 'outer.zip', entries)
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)

    root = source_registry.asset_root(export, 'MyMod.esp')
    assert (root / 'meshes' / 'nested' / 'deep.nif').read_bytes() == b'NESTED'
    assert (root / 'meshes' / 'clutter' / 'thing.nif').is_file()
    assert not list(root.rglob('inner.zip'))


def test_outer_archive_wins_over_nested(tmp_path):
    inner = _zip(tmp_path / 'inner.zip',
                 {'Meshes/clutter/thing.nif': b'FROM-NESTED'})
    entries = _mod_entries()
    entries['inner.zip'] = inner.read_bytes()
    arc = _zip(tmp_path / 'outer.zip', entries)
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)
    thing = (source_registry.asset_root(export, 'MyMod.esp')
             / 'meshes' / 'clutter' / 'thing.nif')
    assert thing.read_bytes() == b'NIF-DATA'


def test_folder_import(tmp_path):
    src = tmp_path / 'ExtractedMod'
    for name, data in _mod_entries('Data/').items():
        p = src / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    export = tmp_path / 'export'
    mod_ingest.ingest(src, export, log=lambda *a: None)

    root = source_registry.asset_root(export, 'MyMod.esp')
    assert (root / 'meshes' / 'clutter' / 'thing.nif').is_file()
    assert source_registry.get(export, 'MyMod.esp')['kind'] == 'folder'


def test_reingest_uses_retained_archive(tmp_path):
    arc = _zip(tmp_path / 'mod.zip', _mod_entries())
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)

    # The original download disappears; re-import must still work.
    arc.unlink()
    assert source_registry.retained_archive(export, 'MyMod.esp') is not None
    res = mod_ingest.reingest('MyMod.esp', export, log=lambda *a: None,
                              force=True)
    assert res['MyMod.esp']['cached'] is False
    assert (source_registry.asset_root(export, 'MyMod.esp')
            / 'meshes' / 'clutter' / 'thing.nif').is_file()


def test_secondary_plugin_can_reingest(tmp_path):
    """Only ONE archive copy is kept, under the primary plugin -- a secondary
    plugin must still resolve it, or re-running it reports the archive gone."""
    entries = _mod_entries('TWMP/Data/')
    entries['TWMP/Data/MapMarkers.esp'] = _tes4_plugin_bytes()
    arc = _zip(tmp_path / 'twmp.zip', entries)
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)
    arc.unlink()

    for name in ('MyMod.esp', 'MapMarkers.esp'):
        assert source_registry.retained_archive(export, name) is not None
    res = mod_ingest.reingest('MapMarkers.esp', export,
                              log=lambda *a: None, force=True)
    assert set(res) == {'MyMod.esp', 'MapMarkers.esp'}


def test_label_prefers_folder_when_filename_uninformative(tmp_path):
    """'Skyrim esp-40005-0-1.rar' must not become the label 'Skyrim esp'."""
    arc = _zip(tmp_path / 'Skyrim esp-40005-0-1.zip',
               _mod_entries('TWMP_Skyrim/Data/'))
    assert mod_ingest.inspect(arc).label == 'TWMP_Skyrim'

    named = _zip(tmp_path / 'Elsweyr Anequina-25023-March-2014-15617.zip',
                 _mod_entries())
    assert mod_ingest.inspect(named).label == 'Elsweyr Anequina'


def test_remove_deletes_tree_and_entry(tmp_path):
    arc = _zip(tmp_path / 'mod.zip', _mod_entries())
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)
    root = source_registry.asset_root(export, 'MyMod.esp')
    assert mod_ingest.remove('MyMod.esp', export, log=lambda *a: None)
    assert not root.exists()
    assert source_registry.get(export, 'MyMod.esp') is None


def test_masters_detected_from_imported_plugin(tmp_path):
    """A mod mastering Oblivion.esm must be reported, so the import can block
    on a missing master export rather than silently converting to nothing."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from convert import get_masters_from_binary

    entries = _mod_entries()
    entries['MyMod.esp'] = _tes4_plugin_bytes(masters=['Oblivion.esm'])
    arc = _zip(tmp_path / 'mod.zip', entries)
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)

    binary = source_registry.plugin_binary(export, 'MyMod.esp')
    assert get_masters_from_binary(str(binary)) == ['Oblivion.esm']


# ---------------------------------------------------------------------------
#  Asset-only mods (texture/mesh replacers, resource packs -- no plugin)
# ---------------------------------------------------------------------------

def _asset_only_entries(prefix=''):
    return {
        f'{prefix}Meshes/clutter/thing.nif': b'NIF',
        f'{prefix}Textures/clutter/thing.dds': b'DDS',
        f'{prefix}Trees/pine.spt': b'SPT',
        f'{prefix}readme.txt': b'hi',
    }


def test_asset_only_archive_imports(tmp_path):
    """A mod with no plugin is still a mod (Tamriel Landscape Pack = one BSA
    of 2,018 meshes/textures/trees, zero plugins)."""
    arc = _zip(tmp_path / 'Landscape Pack.zip', _asset_only_entries('data/'))
    export = tmp_path / 'export'
    res = mod_ingest.ingest(arc, export, log=lambda *a: None)

    name = next(iter(res))
    assert (export / name / 'meshes' / 'clutter' / 'thing.nif').is_file()
    assert (export / name / 'textures' / 'clutter' / 'thing.dds').is_file()
    assert (export / name / 'trees' / 'pine.spt').is_file()

    entry = source_registry.get(export, name)
    assert entry['plugin'] == ''
    assert entry['group_plugins'] == []
    caps = entry['capabilities']
    assert caps['plugin'] is False
    assert caps['meshes'] and caps['textures'] and caps['trees']
    assert caps['sound'] is False


def test_asset_only_bsa_only_archive(tmp_path):
    """The real shape: a `data/` folder holding nothing but a BSA."""
    bsa = _make_bsa(tmp_path / 'Pack.bsa',
                    {r'meshes\rock.nif': b'NIF',
                     r'textures\rock.dds': b'DDS'})
    arc = _zip(tmp_path / 'pack.zip', {'data/Pack.bsa': bsa.read_bytes()})
    export = tmp_path / 'export'
    res = mod_ingest.ingest(arc, export, log=lambda *a: None)

    name = next(iter(res))
    assert (export / name / 'meshes' / 'rock.nif').read_bytes() == b'NIF'
    assert (export / name / 'textures' / 'rock.dds').read_bytes() == b'DDS'
    # Counts come from the finished tree, so BSA content is included.
    assert sum(source_registry.get(export, name)['counts'].values()) == 2


def test_asset_only_available_steps(tmp_path):
    """Export/Import/Scripts/Creatures need a plugin; asset steps do not."""
    arc = _zip(tmp_path / 'pack.zip', _asset_only_entries())
    export = tmp_path / 'export'
    res = mod_ingest.ingest(arc, export, log=lambda *a: None)
    caps = res[next(iter(res))]['capabilities']

    usable = mod_ingest.available_steps(caps)
    for step in ('export', 'import_', 'scripts', 'creatures', 'sounds'):
        assert step not in usable, step
    for step in ('meshes', 'speedtrees', 'pack', 'pack_zip'):
        assert step in usable, step


def test_capabilities_recomputed_for_legacy_entry(tmp_path):
    """A mod imported before capabilities were recorded must still be gated:
    treating a missing field as 'allow everything' offered steps it cannot
    run (a real case -- an earlier import kept showing Extract)."""
    arc = _zip(tmp_path / 'mod.zip', _asset_only_entries())
    export = tmp_path / 'export'
    res = mod_ingest.ingest(arc, export, log=lambda *a: None)
    name = next(iter(res))

    # Strip the field, as an entry written by the older code would be.
    entry = source_registry.get(export, name)
    entry.pop('capabilities', None)
    source_registry.put(export, name, entry)
    assert source_registry.get(export, name).get('capabilities') is None

    caps = mod_ingest.capabilities_for(export / name,
                                       has_plugin=bool(entry.get('plugin')))
    assert caps['plugin'] is False
    assert caps['meshes'] is True
    assert 'export' not in mod_ingest.available_steps(caps)


def test_extract_never_offered_for_an_imported_mod(tmp_path):
    """Ingest already unpacked the mod's BSAs into export/, so Extract would
    only re-check its cache and print 'already imported, skipping'."""
    arc = _zip(tmp_path / 'mod.zip', _mod_entries())
    export = tmp_path / 'export'
    res = mod_ingest.ingest(arc, export, log=lambda *a: None)
    caps = res['MyMod.esp']['capabilities']
    assert 'extract' not in mod_ingest.available_steps(caps)
    # ...even though everything else this mod can do is still offered.
    assert 'export' in mod_ingest.available_steps(caps)


def test_normal_mod_keeps_every_step(tmp_path):
    arc = _zip(tmp_path / 'mod.zip', _mod_entries())
    export = tmp_path / 'export'
    res = mod_ingest.ingest(arc, export, log=lambda *a: None)
    caps = res['MyMod.esp']['capabilities']
    usable = mod_ingest.available_steps(caps)
    assert {'export', 'import_', 'scripts', 'creatures', 'meshes'} <= usable


def test_asset_only_reingest(tmp_path):
    arc = _zip(tmp_path / 'pack.zip', _asset_only_entries())
    export = tmp_path / 'export'
    res = mod_ingest.ingest(arc, export, log=lambda *a: None)
    name = next(iter(res))
    arc.unlink()
    again = mod_ingest.reingest(name, export, log=lambda *a: None, force=True)
    assert name in again
    assert (export / name / 'meshes' / 'clutter' / 'thing.nif').is_file()


def test_truly_empty_archive_still_rejected(tmp_path):
    arc = _zip(tmp_path / 'empty.zip', {'readme.txt': b'nothing here'})
    with pytest.raises(mod_ingest.IngestError, match='no plugin and no assets'):
        mod_ingest.inspect(arc)


def test_asset_only_flagged_in_source_list(tmp_path):
    arc = _zip(tmp_path / 'pack.zip', _asset_only_entries())
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)
    row = next(r for r in source_registry.all_sources(export)
               if r['kind'] == 'mod')
    assert row['asset_only'] is True


def test_label_drops_packaging_suffix(tmp_path):
    """'Tamriel Landscape Pack bsa -36887-1.rar' -> 'Tamriel Landscape Pack'."""
    arc = _zip(tmp_path / 'Tamriel Landscape Pack bsa -36887-1.zip',
               _asset_only_entries('data/'))
    assert mod_ingest.inspect(arc).label == 'Tamriel Landscape Pack'


# ---------------------------------------------------------------------------
#  Unified sources: game folders and imported mods are the same concept
# ---------------------------------------------------------------------------

def _fake_install(root, name, plugins):
    d = root / name / 'Data'
    d.mkdir(parents=True, exist_ok=True)
    for p in plugins:
        (d / p).write_bytes(_tes4_plugin_bytes())
    return d


def test_directory_label_names_the_install_not_data(tmp_path):
    """Two folders both called 'Data' must not both read as 'Data'."""
    assert source_registry.label_for_directory(
        "D:/Other Games/Nehrim At Fate's Edge/Data") == "Nehrim At Fate's Edge"
    assert source_registry.label_for_directory(
        'C:/Steam/common/Oblivion/Data') == 'Oblivion'
    assert source_registry.label_for_directory(
        'D:/Games/Oblivion/Data/') == 'Oblivion'


def test_add_remove_directory_sources(tmp_path):
    export = tmp_path / 'export'
    ob = _fake_install(tmp_path, 'Oblivion', ['Oblivion.esm'])
    ne = _fake_install(tmp_path, 'Nehrim', ['Nehrim.esm'])

    assert source_registry.add_directory(export, str(ob)) is True
    assert source_registry.add_directory(export, str(ne)) is True
    # Re-adding is a no-op rather than a duplicate row.
    assert source_registry.add_directory(export, str(ob)) is False
    assert [r['label'] for r in source_registry.directories(export)] == [
        'Oblivion', 'Nehrim']

    assert source_registry.remove_directory(export, str(ob)) is True
    assert [r['label'] for r in source_registry.directories(export)] == ['Nehrim']
    # Removing only unregisters -- the folder and its plugins stay on disk.
    assert (ob / 'Oblivion.esm').is_file()


def test_directory_for_resolves_plugin_to_its_own_install(tmp_path):
    """The bug this replaced: whichever path was in the box got recorded, so
    Oblivion.esm ended up stamped against the Nehrim folder."""
    export = tmp_path / 'export'
    ob = _fake_install(tmp_path, 'Oblivion', ['Oblivion.esm', 'Shared.esp'])
    ne = _fake_install(tmp_path, 'Nehrim', ['Nehrim.esm'])
    source_registry.add_directory(export, str(ob))
    source_registry.add_directory(export, str(ne))

    assert source_registry.directory_for(export, 'Oblivion.esm') == str(ob)
    assert source_registry.directory_for(export, 'Nehrim.esm') == str(ne)
    assert source_registry.directory_for(export, 'Missing.esp') is None


def test_all_sources_lists_directories_then_mods(tmp_path):
    export = tmp_path / 'export'
    ob = _fake_install(tmp_path, 'Oblivion', ['Oblivion.esm'])
    source_registry.add_directory(export, str(ob))
    arc = _zip(tmp_path / 'mod.zip', _mod_entries())
    mod_ingest.ingest(arc, export, log=lambda *a: None)

    rows = source_registry.all_sources(export)
    assert [r['kind'] for r in rows] == ['directory', 'mod']
    assert rows[0]['label'] == 'Oblivion'
    assert rows[1]['plugins'] == ['MyMod.esp']


def test_all_sources_includes_unregistered_configured_dir(tmp_path):
    """A fresh install with an empty registry still shows its game folder."""
    export = tmp_path / 'export'
    ob = _fake_install(tmp_path, 'Oblivion', ['Oblivion.esm'])
    rows = source_registry.all_sources(export, extra_dirs=[str(ob)])
    assert [r['label'] for r in rows] == ['Oblivion']
    # ...and is not duplicated once it is also registered.
    source_registry.add_directory(export, str(ob))
    rows = source_registry.all_sources(export, extra_dirs=[str(ob)])
    assert len(rows) == 1


def test_migrate_skips_missing_and_pluginless_dirs(tmp_path):
    export = tmp_path / 'export'
    ob = _fake_install(tmp_path, 'Oblivion', ['Oblivion.esm'])
    empty = tmp_path / 'Empty' / 'Data'
    empty.mkdir(parents=True)
    added = source_registry.migrate_known_directories(
        export, extra_dirs=[str(ob), str(empty),
                            str(tmp_path / 'Gone' / 'Data')],
        include_history=False)
    assert added == 1
    assert [r['label'] for r in source_registry.directories(export)] == ['Oblivion']


# ---------------------------------------------------------------------------
#  BSA builder (mirrors the layout bsa_extract._iter_bsa reads)
# ---------------------------------------------------------------------------

def _bsa_hash(name):
    """Bethesda's BSA name hash. Only uniqueness matters to the reader here."""
    name = name.lower()
    root, ext = os.path.splitext(name)
    if not root:
        root, ext = ext, ''
    low = bytes(root, 'latin-1')
    h1 = 0
    if low:
        h1 = (low[-1] | ((low[-2] << 8) if len(low) > 2 else 0)
              | (len(low) << 16) | (low[0] << 24))
    h2 = 0
    for c in low[1:-2] if len(low) > 3 else b'':
        h2 = (h2 * 0x1003F + c) & 0xFFFFFFFF
    for c in bytes(ext, 'latin-1'):
        h2 = (h2 * 0x1003F + c) & 0xFFFFFFFF
    return ((h2 & 0xFFFFFFFF) << 32) | (h1 & 0xFFFFFFFF)


def _make_bsa(path, files):
    """Write an uncompressed Oblivion-format BSA containing `files`."""
    by_folder = {}
    for full, data in files.items():
        folder, _, fname = full.replace('/', '\\').rpartition('\\')
        by_folder.setdefault(folder, []).append((fname, data))

    folders = sorted(by_folder)
    file_count = sum(len(v) for v in by_folder.values())
    total_folder_name_len = sum(len(f) + 1 for f in folders)
    name_block = b''.join(
        bytes(fn, 'latin-1') + b'\x00'
        for f in folders for fn, _ in sorted(by_folder[f]))

    header_size = 36
    folder_records = len(folders) * 16
    folder_blocks = sum(1 + len(f) + 1 + len(by_folder[f]) * 16
                        for f in folders)
    data_start = (header_size + folder_records + folder_blocks
                  + len(name_block))

    out = bytearray()
    out += b'BSA\x00' + struct.pack('<I', 103)
    out += struct.pack('<I', header_size)          # dir offset
    out += struct.pack('<I', 0x0003)               # names present, no compress
    out += struct.pack('<I', len(folders))
    out += struct.pack('<I', file_count)
    out += struct.pack('<I', total_folder_name_len)
    out += struct.pack('<I', len(name_block))
    out += struct.pack('<I', 0)                    # file flags

    offset = data_start
    folder_block = bytearray()
    for f in folders:
        raw = bytes(f, 'latin-1') + b'\x00'
        folder_block += bytes([len(raw)]) + raw
        for fn, data in sorted(by_folder[f]):
            folder_block += struct.pack('<QII', _bsa_hash(fn), len(data),
                                        offset)
            offset += len(data)

    for f in folders:
        out += struct.pack('<QII', _bsa_hash(f), len(by_folder[f]), 0)
    out += folder_block
    out += name_block
    for f in folders:
        for _fn, data in sorted(by_folder[f]):
            out += data

    path.write_bytes(bytes(out))
    return path


# ---------------------------------------------------------------------------
#  Group folders and the output-side scanners
# ---------------------------------------------------------------------------

def test_converted_plugins_finds_plugins_inside_a_group_folder(tmp_path):
    """A mod folder is named for the MOD, so `<folder>/<folder>` misses it.

    Both scanners key off `<plugin>.manifest.json` instead. Getting this wrong
    drops the plugin out of load-order resolution silently.
    """
    from asset_convert.lod.sibling_lod import converted_plugins

    out = tmp_path / 'output'
    # A single-plugin conversion: folder named for the plugin.
    solo = out / 'Oblivion.esm'
    solo.mkdir(parents=True)
    (solo / 'Oblivion.esm').write_bytes(b'x')

    # A mod group: folder named for the mod, three plugins inside.
    grp = out / 'Tamriel Resource Pack Full 2.0'
    grp.mkdir(parents=True)
    for n in ('TamRes.esm', 'TamRes.esp', 'TamRes_LandscapeResource.esm'):
        (grp / n).write_bytes(b'x')
        (grp / f'{n}.manifest.json').write_text('{}', encoding='utf-8')

    # A manifest with no plugin beside it is NOT a converted plugin.
    (grp / 'Ghost.esp.manifest.json').write_text('{}', encoding='utf-8')

    assert converted_plugins(out) == [
        'Oblivion.esm', 'TamRes.esm', 'TamRes.esp',
        'TamRes_LandscapeResource.esm']


def test_gui_scan_converted_finds_group_members(tmp_path):
    import json as _json
    import gui

    out = tmp_path / 'output'
    grp = out / 'Some Mod 1.0'
    grp.mkdir(parents=True)
    for n in ('A.esp', 'B.esp'):
        (grp / n).write_bytes(b'x')
        (grp / f'{n}.manifest.json').write_text(
            _json.dumps({'source': n}), encoding='utf-8')

    assert gui.scan_converted(str(out)) == ['A.esp', 'B.esp']


def test_removing_one_plugin_keeps_the_shared_assets(tmp_path):
    """Deleting one of three plugins must not delete the other two's meshes."""
    entries = _mod_entries('TWMP/Data/')
    entries['TWMP/Data/MapMarkers.esp'] = _tes4_plugin_bytes()
    arc = _zip(tmp_path / 'twmp.zip', entries)
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)
    root = source_registry.asset_root(export, 'MyMod.esp')

    assert mod_ingest.remove('MapMarkers.esp', export, log=lambda *a: None)
    assert (root / 'meshes' / 'clutter' / 'thing.nif').is_file()
    assert (root / '_source' / 'MyMod.esp').is_file()
    assert not (root / '_source' / 'MapMarkers.esp').exists()
    assert source_registry.get(export, 'MyMod.esp') is not None

    # The last one out takes the tree with it.
    assert mod_ingest.remove('MyMod.esp', export, log=lambda *a: None)
    assert not root.exists()


# ---------------------------------------------------------------------------
#  The migration tool agrees with the resolver
# ---------------------------------------------------------------------------

def _legacy_layout(tmp_path, label, plugs):
    """An export/ tree in the OLD one-folder-per-plugin shape."""
    import json as _json
    exp = tmp_path / 'export'
    for n in plugs:
        (exp / n / 'meshes').mkdir(parents=True)
        (exp / n / '_source').mkdir(parents=True)
        (exp / n / 'meshes' / 'm.nif').write_bytes(b'M')
        (exp / n / '_source' / n).write_bytes(b'P')
        (exp / n / 'STAT.txt').write_text('recs ' + n, encoding='utf-8')
    reg = {'version': 1, 'sources': {
        n: {'kind': 'archive', 'plugin': n, 'group_id': 'g',
            'group_label': label, 'group_plugins': list(plugs)} for n in plugs}}
    (exp / 'sources.json').write_text(_json.dumps(reg), encoding='utf-8')
    return exp


@pytest.mark.parametrize('label,plugs', [
    ('My Pack', ('A.esm', 'B.esp')),   # ordinary multi-plugin mod
    ('A.esm',   ('A.esm', 'B.esp')),   # label == a member's name (A.esm.zip)
    ('Solo',    ('A.esm',)),           # one plugin, label differs
    ('A.esm',   ('A.esm',)),           # one plugin, label == its name
])
def test_migration_puts_records_where_the_resolver_looks(tmp_path, label, plugs):
    """The migration and `record_dir` must agree on the nesting rule.

    They disagreed twice: the tool nested unconditionally while `record_dir`
    nests only for multi-plugin mods, and a self-named group folder left one
    member's records loose in the root. Either way the records end up
    somewhere the pipeline never looks and the export silently reads as
    missing -- no error, just an empty export.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.esm.migrate_group_layout import migrate

    exp = _legacy_layout(tmp_path, label, plugs)
    migrate(exp, apply=True, log=lambda *a: None)

    for n in plugs:
        rec = source_registry.record_dir(exp, n)
        assert (rec / 'STAT.txt').is_file(), f'{n}: records not at {rec}'
        assert (rec / 'STAT.txt').read_text(encoding='utf-8') == 'recs ' + n

    # One shared payload, and running it again changes nothing.
    assert len(list(exp.rglob('meshes/m.nif'))) == 1
    migrate(exp, apply=True, log=lambda *a: None)
    for n in plugs:
        assert (source_registry.record_dir(exp, n) / 'STAT.txt').is_file()


def test_shared_counts_land_on_every_group_member(tmp_path):
    """`counts` describes the shared tree, so a partial import must not leave
    siblings reading zero -- that made --list-mods show one plugin's files."""
    entries = _mod_entries('TWMP/Data/')
    entries['TWMP/Data/MapMarkers.esp'] = _tes4_plugin_bytes()
    arc = _zip(tmp_path / 'twmp.zip', entries)
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)

    totals = {n: sum((source_registry.get(export, n) or {}).get('counts', {})
                     .values())
              for n in ('MyMod.esp', 'MapMarkers.esp')}
    assert totals['MyMod.esp'] > 0
    assert totals['MyMod.esp'] == totals['MapMarkers.esp']


def test_capabilities_are_shared_across_group_members(tmp_path):
    """Every plugin from one archive draws on the SAME meshes/textures, so the
    GUI must offer the same steps for all of them.

    Two separate defects made a resource pack look empty: siblings imported
    before the field existed stored `capabilities: None`, and the GUI's
    fallback measured `export/<plugin>/` -- a path that stops existing once the
    mod shares one group folder -- so it reported no meshes at all.
    """
    entries = _mod_entries('TWMP/Data/')
    entries['TWMP/Data/MapMarkers.esp'] = _tes4_plugin_bytes()
    arc = _zip(tmp_path / 'twmp.zip', entries)
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)

    caps = {n: (source_registry.get(export, n) or {}).get('capabilities')
            for n in ('MyMod.esp', 'MapMarkers.esp')}
    assert caps['MyMod.esp'] == caps['MapMarkers.esp']
    assert caps['MyMod.esp']['meshes'] is True
    assert caps['MyMod.esp']['textures'] is True

    steps = {n: mod_ingest.available_steps(c) for n, c in caps.items()}
    assert steps['MyMod.esp'] == steps['MapMarkers.esp']
    assert 'meshes' in steps['MyMod.esp']

    # The measured-on-demand path (an entry with no stored capabilities) must
    # look at the shared tree, not the vanished per-plugin folder.
    measured = mod_ingest.capabilities_for(
        source_registry.asset_root(export, 'MapMarkers.esp'), has_plugin=True)
    assert measured['meshes'] is True
    assert measured['textures'] is True


def test_a_master_inside_a_group_folder_still_resolves(tmp_path):
    """A plugin can master an imported mod's ESM.

    Masters used to resolve as sibling directories under export/. Once an
    imported mod's plugins moved into their mod's shared folder, every such
    master read as MISSING -- the importer warned "master export not found" and
    diffed the overrides against nothing, which is this project's classic
    silent-failure mode.
    """
    import os
    from tes5_import.overrides.nested import export_root, master_export_dir

    # A two-plugin mod, so its members nest inside the group folder.
    entries = _mod_entries('TWMP/Data/')
    entries['TWMP/Data/MapMarkers.esp'] = _tes4_plugin_bytes()
    arc = _zip(tmp_path / 'twmp.zip', entries)
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)

    # Give the master a record dump, as an export would.
    rec = source_registry.record_dir(export, 'MyMod.esp')
    rec.mkdir(parents=True, exist_ok=True)
    (rec / '_HEADER.txt').write_text('', encoding='utf-8')

    # From a top-level plugin's record dir AND from a nested one, the master
    # must be found.
    for start in (export / 'Oblivion.esm',
                  source_registry.record_dir(export, 'MapMarkers.esp')):
        root = export_root(str(start))
        assert os.path.isfile(os.path.join(root, 'sources.json')), start
        found = master_export_dir(root, 'MyMod.esp')
        assert os.path.isdir(found), f'{start}: master not found at {found}'


def test_missing_master_check_sees_group_folder_masters(tmp_path, monkeypatch):
    """convert._missing_master_exports must not report a converted master."""
    import convert

    entries = _mod_entries('TWMP/Data/')
    entries['TWMP/Data/MapMarkers.esp'] = _tes4_plugin_bytes()
    arc = _zip(tmp_path / 'twmp.zip', entries)
    export = tmp_path / 'export'
    mod_ingest.ingest(arc, export, log=lambda *a: None)

    rec = source_registry.record_dir(export, 'MyMod.esp')
    rec.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(convert, 'get_masters_from_binary',
                        lambda _p: ['MyMod.esp'])
    missing = convert._missing_master_exports(
        ['MapMarkers.esp'], str(export), '')
    assert missing == {}, f'falsely reported missing: {missing}'


def test_mod_name_is_refused_as_a_plugin(tmp_path):
    """-f with a mod's folder name builds a phantom plugin, so it is refused.

    See: docs/reference/pipeline.md#-f-takes-the-plugin
    """
    import convert

    export = tmp_path / 'export'
    source_registry.put(export, 'TR_Mainland.esm', {
        'plugin': 'TR_Mainland.esm', 'group_dir': 'Tamriel Rebuilt 25.08.12',
        'group_label': 'Tamriel Rebuilt 25.08.12'})
    source_registry.put(export, 'Tamriel Landscape Pack', {
        'group_dir': 'Tamriel Landscape Pack',
        'group_label': 'Tamriel Landscape Pack'})

    assert source_registry.mod_plugins(
        export, 'tamriel rebuilt 25.08.12') == ['TR_Mainland.esm']
    assert source_registry.mod_plugins(export, 'TR_Mainland.esm') == []
    assert source_registry.mod_plugins(export, 'Tamriel Landscape Pack') == []

    args = type('Args', (), {'files': ['Tamriel Rebuilt 25.08.12']})()
    with pytest.raises(SystemExit, match='-f TR_Mainland.esm'):
        convert._plugins_to_convert(args, {}, '', str(export))
