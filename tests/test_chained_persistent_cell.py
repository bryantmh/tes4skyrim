"""ChainedMasterIndex.persistent_cell restates the answer into the child's id space.

See: docs/commentary/tes5_import_override.md#full-lod-refs
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import.overrides.master_index import ChainedMasterIndex


class _Fake:
    """A converted master: its masters, its own records and its persistent cells."""

    def __init__(self, path, masters, recs, pcell):
        """Records and persistent cells in this file's OWN id space."""
        self.path, self.masters, self.own_index = path, masters, len(masters)
        self._r, self._p = recs, pcell

    def signature(self, fid):
        """The record's signature, or b''."""
        return self._r.get(fid, b'')

    def __contains__(self, fid):
        return fid in self._r

    def persistent_cell(self, wrld):
        """This file's persistent cell for `wrld`, or 0."""
        return self._p.get(wrld, 0)

    def group_path(self, fid):
        """No nesting needed here."""
        return ()


def _chain(c_pcell):
    """Child masters [skyrim, a, b, c]; c lists its own masters as [skyrim, b, a]."""
    a = _Fake('/x/a.esm', ['skyrim.esm'], {0x01000100: b'WRLD'},
              {0x01000100: 0x01000200})
    b = _Fake('/x/b.esm', ['skyrim.esm'], {}, {})
    c = _Fake('/x/c.esp', ['skyrim.esm', 'b.esm', 'a.esm'],
              {0x01000100: b'WRLD'}, c_pcell)
    return ChainedMasterIndex([a, b, c], base_slot=1,
                              child_masters=['skyrim.esm', 'a.esm', 'b.esm',
                                             'c.esp'])


def test_a_master_of_masters_cell_is_restated():
    """c names a.esm's cell under ITS slot 2; the child numbers a.esm at 1."""
    assert _chain({0x01000100: 0x02000200}).persistent_cell(0x01000100) == 0x01000200


def test_the_answering_files_own_cell_moves_to_its_slot():
    """c's own cell (own index 3) sits at the child's slot 3."""
    assert _chain({0x01000100: 0x03000300}).persistent_cell(0x01000100) == 0x03000300


def test_the_owner_answers_when_the_override_has_none():
    """c has no persistent cell: a.esm's own 01000200 is already in the child's slot 1."""
    assert _chain({}).persistent_cell(0x01000100) == 0x01000200
