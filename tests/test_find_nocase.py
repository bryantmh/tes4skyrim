"""`find_nocase`: `win_join`, then the lookup a case-sensitive filesystem needs.

`_Tree` answers by exact string, so the case-blind branch is exercised on
every host, including one whose real filesystem ignores case. The `tmp_path`
tests then pin the same answers on whatever filesystem the suite runs on.

See: docs/commentary/asset_convert_texture.md#case-blind-lookups
"""

import os
from pathlib import Path

from asset_convert.game_paths import find_nocase, folder_names


def _at(rel):
    """`rel` under the in-memory tree's root, in the host's own spelling."""
    return os.path.join('root', *rel.split('/'))


class _Tree:
    """A case-sensitive file tree held in memory, rooted at `root`."""

    def __init__(self, *files):
        self.files = {_at(f) for f in files}
        self.listed = []

    def is_file(self, path):
        """True only for the exact spelling of a file."""
        return str(path) in self.files

    def is_file_any_case(self, path):
        """True for any spelling, as a case-insensitive filesystem answers."""
        return str(path).lower() in {f.lower() for f in self.files}

    def names(self, folder):
        """`folder_names` for this tree, recording each folder it lists."""
        self.listed.append(folder)
        found = {}
        for path in sorted(self.files):
            if path.startswith(folder + os.sep):
                name = path[len(folder) + 1:].split(os.sep)[0]
                if name not in found.setdefault(name.lower(), []):
                    found[name.lower()].append(name)
        return found

    def find(self, *parts, kind=None):
        """`find_nocase` under `root` against this tree, as a string."""
        got = find_nocase('root', *parts, kind=kind or self.is_file,
                          listing=self.names)
        return got and str(got)


def test_an_exact_path_is_returned_untouched_and_nothing_is_listed():
    """The exact join answers first, in the spelling it was asked for."""
    tree = _Tree('meshes/Clutter/Book.NIF')
    assert tree.find('meshes', 'Clutter\\Book.NIF') == _at('meshes/Clutter/Book.NIF')
    assert tree.listed == []


def test_each_segment_is_matched_ignoring_case():
    """A miss on the exact join falls back to the spelling the tree has."""
    tree = _Tree('meshes/clutter/books/mybook.nif')
    got = tree.find('Meshes', 'Clutter\\Books\\MyBook.NIF')
    assert got == _at('meshes/clutter/books/mybook.nif')
    assert tree.find('Meshes/Clutter', '\\Books\\\\MyBook.NIF') == got


def test_of_two_spellings_the_exact_one_wins():
    """Each twin answers to its own spelling; a third spelling gets the first."""
    tree = _Tree('meshes/A.nif', 'meshes/a.nif')
    assert tree.find('meshes', 'a.nif') == _at('meshes/a.nif')
    assert tree.find('meshes', 'A.nif') == _at('meshes/A.nif')
    assert tree.find('meshes', 'a.NIF') == _at('meshes/A.nif')


def test_a_case_twin_folder_cannot_hide_the_file():
    """Every folder spelling a segment is searched, not only the first."""
    tree = _Tree('Clutter/readme.txt', 'clutter/books/b.nif')
    assert tree.find('CLUTTER', 'Books', 'B.nif') == _at('clutter/books/b.nif')


def test_a_name_differing_beyond_case_is_a_miss():
    """Only case is ignored: a different name is still absent."""
    tree = _Tree('textures/rock/stone.dds')
    assert tree.find('textures', 'Rock\\Stone_n.dds') is None
    assert tree.find('textures', 'Rock') is None


def test_where_case_is_ignored_an_existing_file_lists_nothing():
    """Where the probe ignores case, the answer is the exact join itself."""
    tree = _Tree('textures/rock/stone.dds')
    got = tree.find('Textures', 'Rock\\Stone.DDS', kind=tree.is_file_any_case)
    assert got == _at('Textures/Rock/Stone.DDS')
    assert tree.listed == []


def test_a_miss_lists_each_folder_on_the_path_whatever_the_filesystem():
    """An absent file is looked for case-blind even where the probe ignores case."""
    tree = _Tree('textures/rock/stone.dds')
    got = tree.find('textures', 'rock\\absent.dds', kind=tree.is_file_any_case)
    assert got is None
    assert tree.listed == ['root', _at('textures'), _at('textures/rock')]


def test_on_disk_a_differently_cased_file_is_found(tmp_path):
    """The real filesystem agrees, whether or not it ignores case itself."""
    real = tmp_path / 'meshes' / 'clutter' / 'mybook.nif'
    real.parent.mkdir(parents=True)
    real.write_bytes(b'')
    got = find_nocase(tmp_path, 'Meshes', 'Clutter\\MyBook.NIF')
    assert got is not None and os.path.samefile(got, real)
    assert find_nocase(tmp_path, 'Meshes', 'Clutter\\Other.NIF') is None
    assert find_nocase(tmp_path, 'meshes', 'clutter\\mybook.nif') == Path(real)


def test_on_disk_a_folder_is_found_only_when_asked_for(tmp_path):
    """`kind` picks what counts: a file by default, a folder on request."""
    real = tmp_path / 'Sound' / 'Voice'
    real.mkdir(parents=True)
    assert find_nocase(tmp_path, 'sound', 'voice') is None
    got = find_nocase(tmp_path, 'sound', 'voice', kind=os.path.isdir)
    assert got is not None and os.path.samefile(got, real)


def test_a_folder_that_cannot_be_listed_has_no_names(tmp_path):
    """A missing folder lists as empty rather than raising."""
    (tmp_path / 'Readme.TXT').write_bytes(b'')
    assert folder_names(tmp_path) == {'readme.txt': ['Readme.TXT']}
    assert folder_names(tmp_path / 'absent') == {}


def test_a_listing_keeps_every_spelling_in_sorted_order(monkeypatch):
    """Twins share one key, and their order does not depend on the filesystem's."""
    monkeypatch.setattr(os, 'listdir', lambda folder: ['b', 'B', 'a'])
    assert folder_names('anywhere') == {'b': ['B', 'b'], 'a': ['a']}


def test_a_listing_is_read_again_on_every_call(tmp_path):
    """A file written between two lookups is seen by the second."""
    assert find_nocase(tmp_path, 'New.TXT') is None
    (tmp_path / 'new.txt').write_bytes(b'')
    got = find_nocase(tmp_path, 'New.TXT')
    assert got is not None and os.path.samefile(got, tmp_path / 'new.txt')
