"""Committable navmesh pins: the store, the cell key, and weld application."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tes5_import.base import navmesh_pins as pins
from tes5_import.navmesh.corridor_clean import apply_welds

SQUARE = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0),
          (0.0, 10.0, 0.0)]


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def _store(tmp_path, monkeypatch):
    """Point the pin store at a temp dir and clear its cache."""
    monkeypatch.setattr(pins, 'PINS', str(tmp_path))
    monkeypatch.setattr(pins, '_CACHE', {})


def test_pins_round_trip_at_hundredths(tmp_path, monkeypatch):
    """Positions come back rounded to 0.01u, which is what gets committed."""
    _store(tmp_path, monkeypatch)
    pins.save('Oblivion.esm', 'Cell', [(1.5, 2.25, 3.125)])
    assert pins.pins_for('Oblivion.esm', 'Cell') == [(1.5, 2.25, 3.12)]


def test_cell_name_matches_case_insensitively(tmp_path, monkeypatch):
    """A human types the cell name; its case is not theirs to get right."""
    _store(tmp_path, monkeypatch)
    pins.save('Oblivion.esm', 'ImperialDungeon02', [(1.0, 2.0, 3.0)])
    assert pins.pins_for('Oblivion.esm', 'imperialDUNGEON02')


def test_saving_welds_keeps_the_floor_pins(tmp_path, monkeypatch):
    """The two sections are independent; writing one must not clear the other."""
    _store(tmp_path, monkeypatch)
    pins.save('Oblivion.esm', 'Cell', [(1.0, 2.0, 3.0)],
              [((4.0, 5.0, 6.0), (7.0, 8.0, 9.0))])
    assert pins.pins_for('Oblivion.esm', 'Cell') == [(1.0, 2.0, 3.0)]
    assert pins.welds_for('Oblivion.esm', 'Cell') == [
        ((4.0, 5.0, 6.0), (7.0, 8.0, 9.0))]


def test_empty_save_clears_that_cell(tmp_path, monkeypatch):
    """Pinning nothing removes the entry rather than storing an empty list."""
    _store(tmp_path, monkeypatch)
    pins.save('Oblivion.esm', 'Cell', [(1.0, 2.0, 3.0)])
    pins.save('Oblivion.esm', 'Cell', [])
    assert pins.pins_for('Oblivion.esm', 'Cell') == []


def test_a_missing_file_is_empty_not_an_error(tmp_path, monkeypatch):
    """A conversion must never abort because nobody has pinned anything."""
    _store(tmp_path, monkeypatch)
    assert pins.pins_for('Nope.esm', 'Cell') == []
    assert pins.welds_for('Nope.esm', 'Cell') == []


def test_unpinned_cell_has_an_empty_digest(tmp_path, monkeypatch):
    """This is what keeps every unpinned cell's geometry hash unchanged."""
    _store(tmp_path, monkeypatch)
    assert pins.digest('Oblivion.esm', 'Cell') == ''
    pins.save('Oblivion.esm', 'Cell', [(1.0, 2.0, 3.0)])
    assert pins.digest('Oblivion.esm', 'Cell')


def test_a_weld_alone_still_moves_the_digest(tmp_path, monkeypatch):
    """A cell corrected only by a weld must still restage its cached geometry."""
    _store(tmp_path, monkeypatch)
    pins.save('Oblivion.esm', 'Cell', [],
              [((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))])
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


def test_plugin_is_read_off_the_geometry_cache_path():
    """Deriving it keeps pins out of every navmesh worker's signature."""
    assert pins.plugin_of('export/Oblivion.esm/navmesh_geom_cache') == \
        'Oblivion.esm'


# ---------------------------------------------------------------------------
# Applying a weld
# ---------------------------------------------------------------------------

def test_a_weld_merges_two_indices_into_one():
    """A crack closes: the two triangles end up SHARING a vertex index.

    Vertex 4 sits 2u from vertex 2 -- near enough to be the same corner, far
    enough that nothing upstream fuses them -- which is exactly a crack.
    """
    verts = SQUARE + [(10.0, 12.0, 0.0)]
    tris = [(0, 1, 2), (0, 4, 3)]
    _v, out = apply_welds(verts, tris,
                          [((10.0, 12.0, 0.0), (10.0, 10.0, 0.0))], 8.0)
    assert out == [(0, 1, 2), (0, 2, 3)]
    assert 4 not in {i for t in out for i in t}


def test_a_weld_drops_the_triangles_it_degenerates():
    """A triangle whose two corners merge has no area and must go."""
    verts = SQUARE
    tris = [(0, 1, 2), (1, 2, 3)]
    _v, out = apply_welds(verts, tris, [((10.0, 0.0, 0.0),
                                         (10.0, 10.0, 0.0))], 8.0)
    assert (0, 1, 2) not in out and (1, 2, 3) not in out


def test_a_weld_whose_endpoint_drifted_is_skipped():
    """Out of tolerance matches nothing, rather than welding the wrong vertex."""
    _v, out = apply_welds(SQUARE, [(0, 1, 2)],
                          [((500.0, 500.0, 0.0), (0.0, 0.0, 0.0))], 8.0)
    assert out == [(0, 1, 2)]


def test_a_weld_records_where_the_GENERATOR_puts_both_ends():
    """Both endpoints come from the pre-replay mesh, target included.

    Reading the target after replay records where the human dragged it, which
    a fresh build never reproduces -- measured on Morrowind_ob.esm, such a
    target landed 59u from the nearest generated vertex and never welded.
    """
    from tools.cellview.bake import weld_pairs
    verts = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (99.0, 99.0, 0.0)]
    ops = [{'op': 'move_vert', 'v': 1, 'to': [55.0, 55.0, 0.0]},
           {'op': 'snap_vert', 'v': 2, 'to_v': 1}]
    assert weld_pairs(verts, ops) == [((99.0, 99.0, 0.0), (10.0, 0.0, 0.0))]


def test_no_welds_leaves_the_mesh_untouched():
    """The unpinned path must be exactly what it was before welds existed."""
    tris = [(0, 1, 2), (0, 2, 3)]
    verts, out = apply_welds(SQUARE, tris, None, 8.0)
    assert out is tris and verts is SQUARE
