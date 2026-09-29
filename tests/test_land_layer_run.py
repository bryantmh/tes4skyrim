"""The LAND texture-layer run keeps ATXT-only quadrants and at most 5 alpha layers.

Oblivion paints some quadrants with alpha layers and no BASE; the builder
emitted alpha layers only under a BTXT, so those quadrants lost all their
paint (1,463 in Tamriel). Vanilla ships 4,237 ATXT-only quadrants, layer
indices 0..n-1, each ATXT followed by its VTXT. The cap is 5 because the
landscape shader has six colour slots (base + 5): a sixth alpha layer was
silently dropped by the engine instead of chosen by coverage here.

See: docs/commentary/asset_convert_terrain.md#land-layer-run
"""

import struct

from asset_convert.lod.terrain_lod_textures import decode_land_layers
from tes5_import.base.text_reader import remap_formid
from tes5_import.record_types.world import build_land_layers


def _subs(blob):
    """[(tag, payload)] of a packed subrecord run."""
    out, off = [], 0
    while off + 6 <= len(blob):
        tag = blob[off:off + 4]
        size = struct.unpack_from('<H', blob, off + 4)[0]
        out.append((tag, blob[off + 6:off + 6 + size]))
        off += 6 + size
    return out


def _rec(layers):
    """An exported LAND dict from [(type, quad, tex, {pos: opacity})]."""
    rec = {'LayerCount': str(len(layers))}
    for i, (kind, quad, tex, vt) in enumerate(layers):
        p = f'Layer[{i}]'
        rec[f'{p}.Type'] = kind
        key = 'BTXT' if kind == 'BASE' else 'ATXT'
        rec[f'{p}.{key}.Texture'] = '%08X' % tex
        rec[f'{p}.{key}.Quadrant'] = str(quad)
        if kind == 'ALPHA':
            rec[f'{p}.ATXT.Layer'] = str(i)
            rec[f'{p}.VTXTCount'] = str(len(vt))
            for j, (pos, op) in enumerate(sorted(vt.items())):
                rec[f'{p}.VT[{j}].Pos'] = str(pos)
                rec[f'{p}.VT[{j}].Opacity'] = repr(op)
    return rec


def test_atxt_only_quadrant_is_emitted_in_vanilla_shape():
    """No BTXT, ATXT idx 0 then its VTXT, quadrants ascending."""
    rec = _rec([('BASE', 0, 0x1111, {}),
                ('ALPHA', 2, 0x2222, {5: 0.75}),
                ('ALPHA', 0, 0x3333, {1: 0.5})])

    subs = _subs(build_land_layers(rec))

    tags = [(t, p[4]) for t, p in subs if t in (b'BTXT', b'ATXT')]
    assert tags == [(b'BTXT', 0), (b'ATXT', 0), (b'ATXT', 2)]
    i = next(k for k, (t, p) in enumerate(subs) if t == b'ATXT' and p[4] == 2)
    tex, quad, _u, layer = struct.unpack('<IBBH', subs[i][1])
    assert (tex, quad, layer) == (remap_formid(0x2222), 2, 0)
    assert subs[i + 1][0] == b'VTXT'
    assert struct.unpack('<HHf', subs[i + 1][1]) == (5, 0, 0.75)


def test_atxt_only_quadrant_round_trips_through_the_lod_decoder():
    """decode_land_layers sees alpha with no base for that quadrant."""
    rec = _rec([('ALPHA', 3, 0x4444, {0: 1.0, 288: 0.25})])

    got = decode_land_layers(build_land_layers(rec))

    assert got['base'] == {}
    [(tex, grid)] = got['alpha'][3]
    assert tex == remap_formid(0x4444)
    assert grid[0, 0] == 1.0 and grid[16, 16] == 0.25


def test_at_most_five_alpha_layers_the_top_five_by_coverage():
    """Seven textures in a quadrant: the two with least coverage go."""
    cover = [0.9, 0.1, 0.8, 0.7, 0.05, 0.6, 0.5]
    rec = _rec([('BASE', 1, 0x1000, {})] + [
        ('ALPHA', 1, 0x2000 + i, {p: c for p in range(10)})
        for i, c in enumerate(cover)])

    subs = _subs(build_land_layers(rec))

    atxt = [struct.unpack('<IBBH', p) for t, p in subs if t == b'ATXT']
    assert len(atxt) == 5
    kept = {tex for tex, _q, _u, _l in atxt}
    assert kept == {remap_formid(0x2000 + i) for i in (0, 2, 3, 5, 6)}
    assert [layer for *_x, layer in atxt] == [0, 1, 2, 3, 4]


def test_repeated_texture_merges_by_max_opacity():
    """Two layers of one texture in a quadrant become one, max per vertex."""
    rec = _rec([('BASE', 0, 0x1, {}),
                ('ALPHA', 0, 0x5, {1: 0.2, 2: 0.9}),
                ('ALPHA', 0, 0x5, {1: 0.6})])

    [(tex, grid)] = decode_land_layers(build_land_layers(rec))['alpha'][0]

    assert grid[0, 1] == 0.6 and abs(grid[0, 2] - 0.9) < 1e-6
