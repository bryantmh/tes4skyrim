"""Create LOD selection contracts: worldspace ownership and master dependency.

The Create LOD dialog and the run both hang off two questions:

  * Which plugin's records is a worldspace baked FROM? (`worldspace_owner`)
  * Which plugins rest on a given plugin? (`dependents_of`)

The second one decides two visible behaviours: unticking a plugin greys out
everything mastered on it, and a plugin that does not depend on a worldspace's
owner is never overlaid onto it — the gate that keeps standalone Nehrim.esm out
of Oblivion's TES4Tamriel.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.lod import sibling_lod
from asset_convert.lod.sibling_lod import (dependents_of, worldspace_owner,
                                       merge_worldspaces, master_chain)


@pytest.fixture
def masters(monkeypatch):
    """Stub the master lists, keyed by export dir name."""
    table: dict[str, list[str]] = {}

    def _fake(export_dir):
        return table.get(Path(export_dir).name, [])

    monkeypatch.setattr(sibling_lod, 'master_names', _fake)
    return table


@pytest.fixture
def shipped(monkeypatch):
    """Stub which worldspaces each plugin shipped LOD for."""
    table: dict[str, list[str]] = {}

    def _fake(export_dir):
        return [(e, 0) for e in table.get(Path(export_dir).name, [])]

    monkeypatch.setattr(sibling_lod, 'shipped_lod_worldspaces', _fake)
    return table


class TestWorldspaceOwner:
    def test_first_in_load_order_owns_it(self, shipped):
        shipped['Oblivion.esm'] = ['TES4Tamriel']
        shipped['Tamriel.esp'] = ['TES4Tamriel']
        order = ['Oblivion.esm', 'Tamriel.esp']
        assert worldspace_owner('TES4Tamriel', order, Path('.')) == \
            'Oblivion.esm'

    def test_owner_follows_the_given_order(self, shipped):
        """Ownership is positional, so reordering changes the record source."""
        shipped['Oblivion.esm'] = ['TES4Tamriel']
        shipped['Tamriel.esp'] = ['TES4Tamriel']
        order = ['Tamriel.esp', 'Oblivion.esm']
        assert worldspace_owner('TES4Tamriel', order, Path('.')) == \
            'Tamriel.esp'

    def test_unowned_worldspace_is_none(self, shipped):
        shipped['Oblivion.esm'] = ['TES4Tamriel']
        assert worldspace_owner('NoSuchWorld', ['Oblivion.esm'],
                                Path('.')) is None

    def test_a_plugin_that_only_overrides_does_not_own(self, shipped):
        """Shipping no LOD assets means not owning, however many cells it edits.

        Morrowind_ob.esm overrides thousands of TES4Tamriel cells without
        shipping a single TES4Tamriel LOD asset; sourcing records from it would
        drop everything Oblivion.esm holds.
        """
        shipped['Oblivion.esm'] = ['TES4Tamriel']
        shipped['Morrowind_ob.esm'] = ['WrldMorrowind']
        order = ['Morrowind_ob.esm', 'Oblivion.esm']
        assert worldspace_owner('TES4Tamriel', order, Path('.')) == \
            'Oblivion.esm'


class TestDependents:
    def test_direct_dependent(self, masters):
        masters['Tamriel.esp'] = ['Oblivion.esm']
        deps = dependents_of(['Oblivion.esm', 'Tamriel.esp'], Path('.'))
        assert deps['Oblivion.esm'] == {'Tamriel.esp'}
        assert deps['Tamriel.esp'] == set()

    def test_transitive_dependent(self, masters):
        """Dropping Nehrim must grey Translation, which never names Oblivion."""
        masters['Nehrim.esm'] = []
        masters['Translation.esp'] = ['Nehrim.esm']
        masters['Patch.esp'] = ['Translation.esp']
        deps = dependents_of(['Nehrim.esm', 'Translation.esp', 'Patch.esp'],
                             Path('.'))
        assert deps['Nehrim.esm'] == {'Translation.esp', 'Patch.esp'}
        assert deps['Translation.esp'] == {'Patch.esp'}

    def test_unrelated_plugins_do_not_depend(self, masters):
        masters['Nehrim.esm'] = []
        masters['Oblivion.esm'] = []
        deps = dependents_of(['Nehrim.esm', 'Oblivion.esm'], Path('.'))
        assert deps['Nehrim.esm'] == set()
        assert deps['Oblivion.esm'] == set()

    def test_a_master_cycle_terminates(self, masters):
        masters['A.esp'] = ['B.esp']
        masters['B.esp'] = ['A.esp']
        deps = dependents_of(['A.esp', 'B.esp'], Path('.'))
        assert deps['A.esp'] == {'B.esp'}
        assert deps['B.esp'] == {'A.esp'}

    def test_masters_outside_the_selection_are_ignored(self, masters):
        masters['Solo.esp'] = ['NotSelected.esm']
        deps = dependents_of(['Solo.esp'], Path('.'))
        assert deps['Solo.esp'] == set()


class TestOverlayGate:
    """A plugin may only overlay a worldspace whose owner it depends on."""

    def test_standalone_plugin_is_not_an_overlay(self, masters):
        # The real defect this prevents: Nehrim.esm is a different game with
        # its own worldspace, and overlaying it onto Oblivion's TES4Tamriel
        # would merge two unrelated mods' references into one set of tiles.
        masters['Tamriel.esp'] = ['Oblivion.esm']
        masters['Nehrim.esm'] = []
        names = ['Oblivion.esm', 'Tamriel.esp', 'Nehrim.esm']
        assert 'Oblivion.esm' in master_chain('Tamriel.esp', Path('.'), names)
        assert 'Oblivion.esm' not in master_chain('Nehrim.esm', Path('.'),
                                                   names)

    def test_transitive_dependent_is_an_overlay(self, masters):
        masters['Tamriel.esp'] = ['Oblivion.esm']
        masters['Patch.esp'] = ['Tamriel.esp']
        names = ['Oblivion.esm', 'Tamriel.esp', 'Patch.esp']
        assert 'Oblivion.esm' in master_chain('Patch.esp', Path('.'), names)


class TestMergeWorldspaces:
    """The dialog re-flattens this on every plugin toggle, so it must be pure."""

    def test_union_in_first_appearance_order(self):
        by = {'A.esp': ['W1', 'W2'], 'B.esp': ['W2', 'W3']}
        assert merge_worldspaces(['A.esp', 'B.esp'], by) == ['W1', 'W2', 'W3']

    def test_dropping_a_plugin_drops_its_exclusive_worldspace(self):
        by = {'Oblivion.esm': ['TES4Tamriel'], 'Nehrim.esm': ['NehrimWorld']}
        assert merge_worldspaces(['Oblivion.esm'], by) == ['TES4Tamriel']

    def test_a_shared_worldspace_survives_dropping_one_owner(self):
        by = {'A.esp': ['W1'], 'B.esp': ['W1']}
        assert merge_worldspaces(['B.esp'], by) == ['W1']

    def test_unknown_plugin_contributes_nothing(self):
        assert merge_worldspaces(['Ghost.esp'], {'A.esp': ['W1']}) == []


class TestModAddedWorldspacesAreOffered:
    """Shipped LOD assets are NOT the authority on what deserves LOD.

    Only Bethesda baked LOD offline. A third-party landmass ships none, so a
    shipped-asset scan returned zero worldspaces for exactly the plugins that
    most need LOD generated — the dialog showed nothing and the bake skipped
    them with "no selected plugin ships LOD for it".
    """

    def test_terrain_without_shipped_lod_is_still_offered(self, monkeypatch,
                                                          tmp_path):
        """The reported bug: a mod-added worldspace must appear."""
        from asset_convert.lod import terrain_lod

        d = tmp_path / 'ElsweyrAnequina.esp'
        d.mkdir()
        out = tmp_path / 'out'
        (out / 'ElsweyrAnequina.esp').mkdir(parents=True)
        (out / 'ElsweyrAnequina.esp' / 'ElsweyrAnequina.esp').write_bytes(b'x')

        monkeypatch.setattr(terrain_lod, 'shipped_lod_worldspaces',
                            lambda _d: [])
        monkeypatch.setattr(terrain_lod, 'detect_terrain_worldspaces',
                            lambda _e: [(1879, 0x3C, 'ANQWorld')])

        # Terrain alone is NOT a reason to bake LOD. An ESP that extends a
        # master's landmass ships LOD for the MASTER's worldspace, and a city
        # world renders on its parent's grid -- so "has terrain, shipped
        # nothing" means covered elsewhere, not missed.
        ws, why = terrain_lod.lod_capable_worldspaces(d, out)
        assert ws == []
        assert why and 'ships no distant LOD' in why

    def test_shipped_ranks_before_generated_and_dedupes(self, monkeypatch,
                                                        tmp_path):
        from asset_convert.lod import terrain_lod

        d = tmp_path / 'Oblivion.esm'
        d.mkdir()
        out = tmp_path / 'out'
        (out / 'Oblivion.esm').mkdir(parents=True)
        (out / 'Oblivion.esm' / 'Oblivion.esm').write_bytes(b'x')

        monkeypatch.setattr(terrain_lod, 'shipped_lod_worldspaces',
                            lambda _d: [('TES4Tamriel', 0x3C)])
        monkeypatch.setattr(
            terrain_lod, 'detect_terrain_worldspaces',
            lambda _e: [(9, 0x3C, 'TES4Tamriel'), (5, 0x99, 'Toddland')])

        # Only the shipped list survives; Toddland has terrain but the game
        # never baked LOD for it, which is the authored answer we trust.
        ws, _why = terrain_lod.lod_capable_worldspaces(d, out)
        assert [e for e, _f in ws] == ['TES4Tamriel']

    def test_a_plugin_with_no_worldspaces_is_explained(self, monkeypatch,
                                                       tmp_path):
        """A quest-only ESP defines none, so there is nothing to offer.

        With shipped LOD as the authority, "offers nothing" is the normal
        answer for most plugins, so the reason string carries the whole
        diagnostic burden -- it must tell a deleted export apart from a plugin
        that legitimately ships no LOD of its own.
        """
        from asset_convert.lod import terrain_lod

        d = tmp_path / 'Quest.esp'
        d.mkdir()
        out = tmp_path / 'out'
        (out / 'Quest.esp').mkdir(parents=True)
        (out / 'Quest.esp' / 'Quest.esp').write_bytes(b'x')

        monkeypatch.setattr(terrain_lod, 'shipped_lod_worldspaces',
                            lambda _d: [])
        monkeypatch.setattr(terrain_lod, 'detect_terrain_worldspaces',
                            lambda _e: [])

        ws, why = terrain_lod.lod_capable_worldspaces(d, out)
        assert ws == []
        assert why and 'no landscape-LOD folders' in why
        # The warning must name the recovery, because a DELETED export looks
        # exactly like a plugin that never had LOD.
        assert '--extract-only' in why

    def test_owner_falls_back_to_terrain_when_nobody_shipped(self, shipped,
                                                             tmp_path):
        """The bake must not skip a mod-added worldspace."""
        from asset_convert.lod import sibling_lod
        from asset_convert.lod import terrain_lod

        out = tmp_path / 'out'
        (out / 'Mod.esp').mkdir(parents=True)
        (out / 'Mod.esp' / 'Mod.esp').write_bytes(b'x')
        terrain_lod.detect_terrain_worldspaces = \
            lambda _e: [(400, 0x7, 'ModWorld')]
        try:
            assert sibling_lod.worldspace_owner(
                'ModWorld', ['Mod.esp'], Path('.'), out) == 'Mod.esp'
        finally:
            del terrain_lod.detect_terrain_worldspaces

    def test_shipped_owner_still_wins_over_a_terrain_extender(self, shipped,
                                                             tmp_path):
        """Oblivion.esm must keep TES4Tamriel, not lose it to Tamriel.esp."""
        shipped['Oblivion.esm'] = ['TES4Tamriel']
        order = ['Tamriel.esp', 'Oblivion.esm']
        assert worldspace_owner('TES4Tamriel', order, Path('.'),
                                tmp_path) == 'Oblivion.esm'

    def test_unexported_plugin_says_export(self, tmp_path):
        from asset_convert.lod.terrain_lod import lod_capable_worldspaces
        ws, why = lod_capable_worldspaces(tmp_path / 'Ghost.esp')
        assert ws == []
        assert 'no export folder' in why and '--export-only' in why

    def test_reason_map_only_covers_empty_plugins(self, monkeypatch, tmp_path):
        """A plugin that resolved worldspaces contributes no reason."""
        from asset_convert.lod import terrain_lod
        from asset_convert.lod.sibling_lod import worldspaces_by_plugin_diagnosed

        def _fake(export_dir, out_root=None, plugin=None):
            # Identify by the PLUGIN argument, not the folder name: a
            # single-plugin imported mod's records live in the mod's folder.
            if (plugin or Path(export_dir).name) == 'Good.esm':
                return [('W1', 1)], None
            return [], 'Bad.esp: nope.'

        monkeypatch.setattr(terrain_lod, 'lod_capable_worldspaces', _fake)
        by, why = worldspaces_by_plugin_diagnosed(
            ['Good.esm', 'Bad.esp'], tmp_path)
        assert by == {'Good.esm': ['W1'], 'Bad.esp': []}
        assert list(why) == ['Bad.esp']

    def test_a_plugin_editing_a_qualified_worldspace_joins_it(
            self, monkeypatch, tmp_path):
        """Morrowind-native content ships no LOD but fills the same grid.

        Tamriel Rebuilt overrides Morroblivion's WrldMorrowind and ships no
        Oblivion-format LOD assets, so it contributed nothing and its land had
        no distant terrain.
        See: docs/commentary/asset_convert_terrain.md#lod-for-plugins-that-only-edit
        """
        from asset_convert.lod import terrain_lod
        from asset_convert.lod.sibling_lod import worldspaces_by_plugin_diagnosed

        def _fake(export_dir, out_root=None, plugin=None):
            """Only the Oblivion-format plugin ships LOD assets."""
            if (plugin or Path(export_dir).name) == 'Morrowind_ob.esm':
                return [('WrldMorrowind', 0x380000)], None
            return [], 'TR.esm: ships no distant LOD of its own.'

        monkeypatch.setattr(terrain_lod, 'lod_capable_worldspaces', _fake)
        monkeypatch.setattr(terrain_lod, 'worldspace_edids',
                            lambda d: {0x380000: 'WrldMorrowind'})
        (tmp_path / 'TR.esm').mkdir()
        (tmp_path / 'TR.esm' / 'CELL.txt').write_text(
            'ParentWRLD=00380000\n', encoding='utf-8')

        by, why = worldspaces_by_plugin_diagnosed(
            ['Morrowind_ob.esm', 'TR.esm'], tmp_path)

        assert by['TR.esm'] == ['WrldMorrowind'], (
            'a plugin adding cells to a qualified worldspace must join it')
        assert 'TR.esm' not in why, 'it no longer has nothing to offer'

    def test_editing_alone_never_qualifies_a_worldspace(self, monkeypatch,
                                                        tmp_path):
        """The rule widens an established grid; it never invents one.

        Otherwise every debug worldspace with terrain comes back, which is the
        false-positive `lod_capable_worldspaces` exists to avoid.
        """
        from asset_convert.lod import terrain_lod
        from asset_convert.lod.sibling_lod import worldspaces_by_plugin_diagnosed

        monkeypatch.setattr(terrain_lod, 'lod_capable_worldspaces',
                            lambda d, out_root=None, plugin=None: ([], 'none'))
        monkeypatch.setattr(terrain_lod, 'worldspace_edids',
                            lambda d: {0x380000: 'WrldMorrowind'})
        (tmp_path / 'TR.esm').mkdir()
        (tmp_path / 'TR.esm' / 'CELL.txt').write_text(
            'ParentWRLD=00380000\n', encoding='utf-8')

        by, _why = worldspaces_by_plugin_diagnosed(['TR.esm'], tmp_path)
        assert by['TR.esm'] == []

    def test_a_raising_scan_is_reported_not_swallowed(self, monkeypatch,
                                                     tmp_path):
        from asset_convert.lod import terrain_lod
        from asset_convert.lod.sibling_lod import worldspaces_by_plugin_diagnosed

        def _boom(export_dir, out_root=None, plugin=None):
            raise OSError('disk gone')

        monkeypatch.setattr(terrain_lod, 'lod_capable_worldspaces', _boom)
        by, why = worldspaces_by_plugin_diagnosed(['A.esp'], tmp_path)
        assert by == {'A.esp': []}
        assert 'disk gone' in why['A.esp']


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])


class TestChildWorldspaceLod:
    """See docs/commentary/asset_convert_terrain.md#child-worldspaces-with-their-own-lod."""

    class _Rec:
        """A WRLD record stub exposing only the PNAM subrecord."""

        def __init__(self, pnam):
            """Hold the raw PNAM bytes, or None for a PNAM-less record."""
            self._pnam = pnam

        def sub(self, sig):
            """The subrecord bytes for `sig`; only PNAM is modelled."""
            return self._pnam if sig == b'PNAM' else None

    def test_map_only_child_keeps_its_own_lod(self):
        """PNAM 0x04 (map only) means the child renders its own LOD."""
        from asset_convert.lod.terrain_lod import _borrows_parent_lod
        assert _borrows_parent_lod(self._Rec(b'\x04\x00')) is False

    def test_use_lod_data_child_borrows(self):
        """PNAM with Use LOD Data set renders inside the parent's grid."""
        from asset_convert.lod.terrain_lod import _borrows_parent_lod
        assert _borrows_parent_lod(self._Rec(b'\x06\x00')) is True

    def test_pnam_less_esm_keeps_the_old_reading(self):
        """A WRLD written before PNAM was emitted still counts as borrowing."""
        from asset_convert.lod.terrain_lod import _borrows_parent_lod
        assert _borrows_parent_lod(self._Rec(None)) is True


class TestPluginNamesCompareCaseBlind:
    """A header's `oblivion.esm` names the selection's `Oblivion.esm`.

    Old: the lowercase header spelling matched nothing in the selection.
    """

    NAMES = ('Oblivion.esm', 'Tamriel.esp')

    def test_master_chain(self, masters):
        """The chain answers in the selection's spelling."""
        masters['Tamriel.esp'] = ['oblivion.esm']
        assert master_chain('Tamriel.esp', Path('.'), self.NAMES) == {
            'Oblivion.esm'}

    def test_dependents_of(self, masters):
        """Deselecting the master greys its dependent."""
        masters['Tamriel.esp'] = ['oblivion.esm']
        assert dependents_of(self.NAMES, Path('.'))['Oblivion.esm'] == {
            'Tamriel.esp'}

    def test_master_rank(self, masters):
        """The master sorts first."""
        from asset_convert.lod.sibling_lod import _master_rank
        masters['Tamriel.esp'] = ['oblivion.esm']
        assert _master_rank(self.NAMES, Path('.')) == {'Oblivion.esm': 0,
                                                       'Tamriel.esp': 1}

    def test_plugins_argument(self):
        """`--plugins oblivion.esm` selects the converted `Oblivion.esm`."""
        from types import SimpleNamespace
        from tools.release.create_lod import _select_plugins
        args = SimpleNamespace(plugins=['oblivion.esm'])
        assert _select_plugins(args, ['Oblivion.esm'], Path('.'),
                               None) == ['Oblivion.esm']
