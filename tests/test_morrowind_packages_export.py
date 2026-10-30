"""Morrowind AI package export: where a travel destination's marker goes."""

from types import SimpleNamespace

from tes4_export.record_types.morrowind_packages import package_records

#: An exterior cell id and an interior one, as the exporter's context would hold them.
EXTERIOR, INTERIOR = '00000E01', '00000A01'


def _ctx(cell: str, spot: tuple) -> SimpleNamespace:
    """A context with one actor placed in `cell` at `spot` and one queued travel to (100, 200, 0)."""
    return SimpleNamespace(
        placements={'actor': [('00000F01', cell, spot)]},
        known_grids={(0, 0)},
        exterior_cell_ids={EXTERIOR},
        index=SimpleNamespace(lookup_exterior=lambda grid: None),
        exterior_cell_id=lambda grid: EXTERIOR,
        derive=lambda key: '00000B01',
        pending_packages=[('actor', '00000C01', 'travel', {'pos': (100.0, 200.0, 0.0)})],
        unresolved={}, dropped_packages=set())


def _marker(ctx) -> dict:
    """The one marker REFR the queued travel emits, as a key->value dict."""
    _packs, markers = package_records(ctx)
    return dict(line.split('=', 1) for line in markers[0][1])


def test_indoor_actor_keeps_its_marker_indoors():
    """An indoor actor's marker stays in its cell though the point is a grid."""
    marker = _marker(_ctx(INTERIOR, (0.0, 0.0, 0.0)))
    assert marker['ParentCELL'] == INTERIOR
    assert (marker['PosX'], marker['PosY']) == ('100.0', '200.0')


def test_outdoor_actor_uses_the_grid():
    """An exterior actor's destination names the exterior grid it falls in."""
    assert _marker(_ctx(EXTERIOR, (0.0, 0.0, 0.0)))['ParentCELL'] == EXTERIOR


def test_point_beyond_reach_stays_on_the_actor():
    """A point beyond AiTravel's 7168 reach puts the marker on the actor."""
    marker = _marker(_ctx(INTERIOR, (9000.0, 0.0, 0.0)))
    assert (marker['ParentCELL'], marker['PosX'], marker['PosY']) == (INTERIOR, '9000.0', '0.0')
