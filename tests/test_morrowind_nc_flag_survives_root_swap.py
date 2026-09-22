"""Morrowind's no-collision root flags are READ before the root swap.

`collision_source` needs `NC`/`NCC`/`NCO` and `MRK`, which live on the source
NiNode; the NiNode->BSFadeNode swap rebuilds the root and carries extra data
selectively, so the flags were lost and all 510 NC-flagged Arktwend meshes
collided with their own render geometry -- a light shaft became a wall.

They are latched off the source root, never copied onto the new one: Morrowind
identifies these by VALUE with an empty `name`, while Skyrim identifies an
extra by `name`, so shipping one adds a block the engine cannot read.

Honoring the flag is also only right for scenery. 20 of those meshes are
APPA/MISC/INGR items, which need a body or Skyrim cannot pick them up.

`apply_patches` runs before the import because PyFFI 2.2.3 calls the removed
`time.clock` while reading nif.xml.

See: docs/commentary/asset_convert_nif.md#morrowind-collision
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()

from asset_convert.nif import nif_converter_morrowind as mw


class _StringExtra(object):
    """A NiStringExtraData carrying one text value under an empty name."""

    def __init__(self, text):
        """Hold the text as pyffi would, in bytes."""
        self.name = b''
        self.string_data = text


class _Node(object):
    """A root node exposing extra data the way pyffi does."""

    def __init__(self, extras=()):
        """Start with the given extras attached."""
        self.extra_data_list = list(extras)
        self.num_extra_data_list = len(self.extra_data_list)
        self.children = []

    def get_extra_datas(self):
        """Every attached extra-data block."""
        return [e for e in self.extra_data_list if e is not None]


def _flags(*texts):
    """The flag texts read off a root carrying `texts`."""
    return mw.root_flag_extras(_Node([_StringExtra(t) for t in texts]))


def test_every_no_collision_spelling_is_read():
    """NC, NCC and NCO all register as the no-collision flag."""
    for text in (b'NC', b'NCC', b'NCO'):
        assert _flags(text) == [text.decode().lower()], text


def test_marker_extra_is_read():
    """MRK gates editor-marker shapes out of the collision geometry."""
    assert _flags(b'MRK') == ['mrk']


def test_unrelated_extras_are_ignored():
    """Only the collision flags are collected."""
    assert _flags(b'AnimatedObject', b'BodyPart', b'Prn') == []


def test_latched_flags_outlive_the_root(monkeypatch):
    """The flag still reaches collision_source after the swap drops it."""
    monkeypatch.setattr(mw, 'mesh_is_fixture', lambda: True)
    mw.latch_root_flags(_Node([_StringExtra(b'NCO')]))
    assert mw.collision_source(_Node()) == (None, False)


def test_item_keeps_collision_despite_the_flag(monkeypatch):
    """An item needs a body: Skyrim picks up through the collision."""
    monkeypatch.setattr(mw, 'mesh_is_fixture', lambda: False)
    mw.latch_root_flags(_Node([_StringExtra(b'NCO')]))
    swapped = _Node()
    assert mw.collision_source(swapped) == (swapped, True)


def test_unflagged_mesh_still_collides(monkeypatch):
    """A fixture with no flag generates collision from its render mesh."""
    monkeypatch.setattr(mw, 'mesh_is_fixture', lambda: True)
    mw.latch_root_flags(_Node())
    swapped = _Node()
    assert mw.collision_source(swapped) == (swapped, True)
