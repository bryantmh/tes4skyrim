"""`lands` is shared with the tile workers, never copied per worker.

`generate_terrain_lod` used to hand `lands` to the pool through `initializer=`,
which pickles it once PER WORKER. On Windows (spawn) that is a private copy in
every process, and the data is large: ~24 KB per cell, dominated by the 17x17
float32 opacity grids rather than the heights. Tamriel's 14,686 LAND records
pack to 329 MB, so 29 workers held ~9.5 GB of byte-identical data.

Measured on the user's 31 GB machine mid-run: 25 python processes, 22.9 GB
resident, 1.2 GB free, 13.4 GB of pagefile. The box was swapping — which is why
the symptom was LOW cpu with the stage crawling, not high cpu.

The fix publishes one copy through multiprocessing.shared_memory and rebuilds
numpy VIEWS over it in each worker. Same worker count, ~1/29th the memory:
measured 6.7 GB peak across 30 processes with 14.9 GB still free.

The contract these tests pin down is that sharing is LOSSLESS. The packed form
must reproduce every array bit-for-bit, including the alpha layer ORDER — the
layer index is dropped by `decode_land_layers` after sorting, so order is the
only thing carrying it, and a packer that rebuilt the list from a dict would
silently reorder the texture blend.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.lod.terrain_lod import (pack_lands, SharedLands, VERTS_SIDE)

QUAD_VERTS = 17


def _cell(seed: int, n_alpha=(2, 1, 0, 3)):
    """One `lands` entry shaped exactly like `decode_land` returns."""
    rng = np.random.default_rng(seed)
    alpha = {}
    for quad, count in enumerate(n_alpha):
        if not count:
            continue
        # (ltex_fid, grid) — the layer index is already applied as ORDER.
        alpha[quad] = [
            (0x1000 + quad * 10 + i,
             rng.random((QUAD_VERTS, QUAD_VERTS)).astype(np.float32))
            for i in range(count)
        ]
    return {
        'heights': rng.random((VERTS_SIDE, VERTS_SIDE)).astype(np.float32),
        'colors': rng.integers(0, 256, (VERTS_SIDE, VERTS_SIDE, 3),
                               dtype=np.uint8),
        'layers': {'base': {q: 0x2000 + q for q in range(4)}, 'alpha': alpha},
    }


@pytest.fixture
def lands():
    # Negative cell coords are the normal case in a worldspace, so they are
    # used as keys — but the seed must stay non-negative.
    return {(x, y): _cell((x + 2) * 100 + (y + 2))
            for x in range(-2, 3) for y in range(-2, 3)}


def _shared(lands):
    buf, index = pack_lands(lands)
    return SharedLands(memoryview(bytes(buf)), index), buf


def test_every_array_survives_bit_for_bit(lands):
    sh, _ = _shared(lands)
    assert len(sh) == len(lands)
    for key, want in lands.items():
        got = sh[key]
        assert np.array_equal(got['heights'], want['heights'])
        assert np.array_equal(got['colors'], want['colors'])
        assert got['layers']['base'] == want['layers']['base']


def test_alpha_layer_ORDER_is_preserved(lands):
    """The layer index lives only in list order — reordering reblends terrain."""
    sh, _ = _shared(lands)
    for key, want in lands.items():
        w_alpha = want['layers']['alpha']
        g_alpha = sh[key]['layers']['alpha']
        assert set(g_alpha) == set(w_alpha)
        for quad, entries in w_alpha.items():
            assert [fid for fid, _g in g_alpha[quad]] == \
                   [fid for fid, _g in entries], 'alpha order changed'
            for (_f1, g1), (_f2, g2) in zip(g_alpha[quad], entries):
                assert np.array_equal(g1, g2)


def test_dtypes_and_shapes_are_unchanged(lands):
    """Workers index these arrays directly; a widened dtype would change output."""
    sh, _ = _shared(lands)
    got = sh[(0, 0)]
    assert got['heights'].dtype == np.float32
    assert got['heights'].shape == (VERTS_SIDE, VERTS_SIDE)
    assert got['colors'].dtype == np.uint8
    assert got['colors'].shape == (VERTS_SIDE, VERTS_SIDE, 3)
    for entries in got['layers']['alpha'].values():
        for _fid, grid in entries:
            assert grid.dtype == np.float32
            assert grid.shape == (QUAD_VERTS, QUAD_VERTS)


def test_arrays_are_views_not_copies(lands):
    """The whole point: a worker must not materialise its own copy."""
    sh, _ = _shared(lands)
    got = sh[(1, 1)]
    # A view over the shared buffer does not own its data.
    assert got['heights'].base is not None
    assert not got['heights'].flags['OWNDATA']


def test_missing_keys_behave_like_a_dict(lands):
    """`_assemble_tile` probes with `in` and `.get` for cells at world edges."""
    sh, _ = _shared(lands)
    assert (0, 0) in sh
    assert (99, 99) not in sh
    assert sh.get((99, 99)) is None
    assert sh.get((0, 0)) is not None
    assert set(sh.keys()) == set(lands)


def test_packing_is_far_smaller_than_a_per_worker_copy(lands):
    """One shared buffer replaces N pickled copies."""
    import pickle
    _sh, buf = _shared(lands)
    per_worker = len(pickle.dumps(lands, -1))
    # The buffer holds the same arrays once, so it is the same order of size as
    # ONE copy — the saving is that 29 workers now share it instead of each
    # holding their own.
    assert len(buf) <= per_worker * 1.2


def test_direct_write_matches_the_reference_packer(lands):
    """The pool fills shared memory directly; it must agree byte-for-byte.

    `pack_lands` builds a bytearray first, which would hold a SECOND full copy
    in the parent (1.2 GB on Tamriel-with-overlays) exactly while the workers
    spawn. The pool therefore sizes the block with `lands_layout` and fills it
    in place — a separate code path, so it is pinned to the reference here.
    """
    from asset_convert.lod.terrain_lod import (pack_lands, lands_layout,
                                           write_lands)
    ref_buf, ref_index = pack_lands(lands)
    size, plan = lands_layout(lands)
    mv = memoryview(bytearray(size))
    index = write_lands(lands, plan, mv)

    assert size == len(ref_buf)
    assert bytes(mv) == bytes(ref_buf)
    assert index == ref_index
