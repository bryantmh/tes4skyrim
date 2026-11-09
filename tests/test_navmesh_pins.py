"""Committable navmesh pins: the store, the cell key, and cuts."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.navmesh_options import NAVMESH_PLUGIN_ENV_VAR, navmesh_plugin
from tes5_import.base import navmesh_pins as pins

SQUARE = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0),
          (0.0, 10.0, 0.0)]

#: One frozen triangle, enough to exercise the store.
TRI = [((1.5, 2.25, 3.125), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0))]


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def _store(tmp_path, monkeypatch):
    """Point the pin store at a temp dir and clear its cache."""
    monkeypatch.setattr(pins, 'PINS', str(tmp_path))
    monkeypatch.setattr(pins, '_CACHE', {})
    monkeypatch.delenv('TESCONV_NAVMESH_PINS', raising=False)


def test_patches_round_trip_at_hundredths(tmp_path, monkeypatch):
    """Positions come back rounded to 0.01u, which is what gets committed."""
    _store(tmp_path, monkeypatch)
    pins.save('Oblivion.esm', 'Cell', frozen=TRI)
    got = pins.tris_for('Oblivion.esm', 'frozen', 'Cell')
    assert got[0][0] == (1.5, 2.25, 3.12)


def test_cell_name_matches_case_insensitively(tmp_path, monkeypatch):
    """A human types the cell name; its case is not theirs to get right."""
    _store(tmp_path, monkeypatch)
    pins.save('Oblivion.esm', 'ImperialDungeon02', frozen=TRI)
    assert pins.tris_for('Oblivion.esm', 'frozen', 'imperialDUNGEON02')


def test_empty_save_clears_that_cell(tmp_path, monkeypatch):
    """Saving nothing removes the entry rather than storing an empty list."""
    _store(tmp_path, monkeypatch)
    pins.save('Oblivion.esm', 'Cell', frozen=TRI)
    pins.save('Oblivion.esm', 'Cell', frozen=[])
    assert 'Cell' not in pins.load('Oblivion.esm')['frozen']


def test_a_missing_file_is_empty_not_an_error(tmp_path, monkeypatch):
    """A conversion must never abort because nobody has pinned anything."""
    _store(tmp_path, monkeypatch)
    assert pins.hand_edits_for('Nope.esm', 'Cell') == {
        'cuts': [], 'frozen': [], 'voids': [], 'links': []}


def test_unpinned_cell_has_an_empty_digest(tmp_path, monkeypatch):
    """This is what keeps every unpinned cell's geometry hash unchanged."""
    _store(tmp_path, monkeypatch)
    assert pins.digest('Oblivion.esm', 'Cell') == ''
    pins.save('Oblivion.esm', 'Cell', voids=TRI)
    assert pins.digest('Oblivion.esm', 'Cell')


# ---------------------------------------------------------------------------
# Naming a cell
# ---------------------------------------------------------------------------

def test_an_interior_is_keyed_by_editor_id():
    """An interior CELL has a name, so that is the key."""
    assert pins.cell_key({'EditorID': 'Foo'}) == 'Foo'


def test_an_exterior_is_keyed_by_worldspace_and_grid():
    """An exterior CELL has no EditorID, so its grid reference names it."""
    assert pins.cell_key({}, 0x0100003C, (4, 12)) == 'wrld:00003C 4 12'


def test_the_exterior_key_ignores_the_load_order_index():
    """Masking to low-24 keeps the key the same whatever the master order."""
    assert (pins.cell_key({}, 0x0100003C, (1, 2)) ==
            pins.cell_key({}, 0x0500003C, (1, 2)))


def test_pins_keyed_by_the_mod_folder_move_to_the_plugin(tmp_path, monkeypatch):
    """A single-plugin mod's folder-named pin file is renamed to the plugin's."""
    _store(tmp_path, monkeypatch)
    monkeypatch.delenv(NAVMESH_PLUGIN_ENV_VAR, raising=False)
    (tmp_path / 'Mod 1.2.json').write_text('{"frozen": {"Cell": []}}')
    pins.use_plugin('Mod.esm', 'Mod 1.2')
    assert navmesh_plugin() == 'Mod.esm'
    assert sorted(os.listdir(tmp_path)) == ['Mod.esm.json']
    assert pins.load('Mod.esm')['frozen'] == {'Cell': []}


# ---------------------------------------------------------------------------
# Applying a cut
# ---------------------------------------------------------------------------

#: Two floors: a ground square at Z 0 (tris 0, 1) and a deck square at Z 200 (tris 2, 3).
TWO_FLOORS = SQUARE + [(x, y, 200.0) for (x, y, _z) in SQUARE]
TWO_FLOOR_TRIS = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)]

#: A cut over the whole square, ground band only.
GROUND_CUT = [(-50.0, 50.0, [(-1.0, -1.0), (11.0, -1.0), (11.0, 11.0), (-1.0, 11.0)])]


def test_a_cut_removes_only_the_floor_inside_its_band():
    """The ground under the cut goes; the deck above the band stays, reindexed.

    See: docs/commentary/tes5_import_navmesh.md#cut-pins
    """
    verts, tris, _l = pins.apply_cuts(TWO_FLOORS, TWO_FLOOR_TRIS, [], GROUND_CUT)
    assert verts == TWO_FLOORS[4:]
    assert tris == [(0, 1, 2), (0, 2, 3)]


def test_a_cut_drops_ledges_to_removed_triangles_and_renumbers_the_rest():
    """A ledge naming a cut triangle goes; a surviving ledge follows the new indices."""
    ledges = [(2, 0, 200.0), (3, 2, 0.0)]
    _v, _t, out = pins.apply_cuts(TWO_FLOORS, TWO_FLOOR_TRIS, ledges, GROUND_CUT)
    assert out == [(1, 0, 0.0)]


def test_no_cuts_leaves_the_mesh_untouched():
    """An uncut cell must be exactly what it was before cuts existed."""
    verts, tris, _l = pins.apply_cuts(TWO_FLOORS, TWO_FLOOR_TRIS, [], [])
    assert verts is TWO_FLOORS and tris is TWO_FLOOR_TRIS


def test_cuts_parse_survive_a_pin_save_and_move_the_digest(tmp_path, monkeypatch):
    """A cut row reads back as a polygon, a later pin save keeps it, and it restages the cell."""
    _store(tmp_path, monkeypatch)
    (tmp_path / 'Oblivion.esm.json').write_text(
        '{"cuts": {"Cell": [[-50, 50, 0, 0, 10, 0, 10, 10]]}}', encoding='utf-8')
    assert pins.cuts_for('Oblivion.esm', 'cell') == [
        (-50.0, 50.0, [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)])]
    assert pins.digest('Oblivion.esm', 'Cell')
    pins.save('Oblivion.esm', 'Cell', frozen=TRI)
    assert len(pins.cuts_for('Oblivion.esm', 'Cell')) == 1
    assert pins.digest('Oblivion.esm', 'Other') == ''
