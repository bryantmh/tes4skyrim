"""cellview's view of the navmesh phase: every NAVM the plugin ships, split interiors' pieces included."""

from tools.cellview.world import _navms


def test_split_interior_pieces_are_listed():
    """A split interior's sibling NAVMs (meta['extra_navms']) come after its root entry."""
    piece = (b'piece', {'fid': 2, 'cell_fid': 7})
    cache = {(7, 8): (b'root', {'fid': 1, 'cell_fid': 7, 'extra_navms': [piece]}),
             (9, 10): (b'other', {'fid': 3, 'cell_fid': 9})}
    assert [m['fid'] for _b, m in _navms(cache)] == [1, 2, 3]


def test_entry_without_meta_is_kept():
    """A failed build's entry (meta None) is still yielded."""
    assert list(_navms({(1, 2): (None, None)})) == [(None, None)]
