"""Regression tests for the vectorised terrain-LOD block encoders and fills.

encode_dxt1_quality, encode_bc4_channel and fill_missing were rewritten from
per-block / per-cell Python loops into whole-array NumPy (5-13x faster; the DXT1
encoder alone was ~33% of all terrain-LOD tile time).  The rewrites are meant to
be BIT-EXACT, not merely "close enough" — terrain LOD .dds files are compared
byte-for-byte when checking conversion determinism, so any drift here shows up
as spurious diffs across the whole worldspace.

Each test keeps the original scalar algorithm inline as the oracle and asserts
the fast path reproduces it exactly.
"""

import struct

import numpy as np
import pytest

from asset_convert.texture.dds_codec import (c565_to_rgb, encode_bc4_channel,
                                         encode_dxt1_quality, rgb_to_565)
from asset_convert.lod.terrain_lod import fill_missing


def _dxt1_reference(img: np.ndarray) -> bytes:
    """The original per-4x4-block DXT1 encoder (pre-vectorisation)."""
    h, w = img.shape[:2]
    ph = (h + 3) & ~3
    pw = (w + 3) & ~3
    padded = np.zeros((ph, pw, 3), dtype=np.uint8)
    padded[:h, :w] = img

    out = bytearray()
    for by in range(0, ph, 4):
        for bx in range(0, pw, 4):
            block = padded[by:by + 4, bx:bx + 4].reshape(16, 3).astype(np.int32)
            cmin = block.min(axis=0)
            cmax = block.max(axis=0)
            c0 = rgb_to_565(cmax.astype(np.uint8))
            c1 = rgb_to_565(cmin.astype(np.uint8))
            if c0 < c1:
                c0, c1 = c1, c0
            elif c0 == c1:
                if c0 == 0:
                    c0 = 1
                else:
                    c1 = c0 - 1
            palette = np.array([
                c565_to_rgb(c0),
                c565_to_rgb(c1),
                ((2 * c565_to_rgb(c0).astype(np.int32)
                  + c565_to_rgb(c1).astype(np.int32)) // 3).astype(np.uint8),
                ((c565_to_rgb(c0).astype(np.int32)
                  + 2 * c565_to_rgb(c1).astype(np.int32)) // 3).astype(np.uint8),
            ], dtype=np.int32)
            diffs = block[:, None, :] - palette[None, :, :]
            codes = (diffs * diffs).sum(axis=2).argmin(axis=1)
            packed = 0
            for i, code in enumerate(codes):
                packed |= (int(code) & 3) << (i * 2)
            out += struct.pack('<HHI', c0, c1, packed)
    return bytes(out)


def _bc4_block_reference(vals16: np.ndarray) -> bytes:
    """The original single-block BC4 encoder (pre-vectorisation)."""
    v = vals16.astype(np.int32)
    r0 = int(v.max())
    r1 = int(v.min())
    if r0 == r1:
        return struct.pack('BB', r0, r1) + b'\x00' * 6
    palette = [r0, r1] + [((7 - i) * r0 + i * r1) // 7 for i in range(1, 7)]
    palette = np.array(palette, dtype=np.int32)
    idx = np.abs(v[:, None] - palette[None, :]).argmin(axis=1)
    bits = 0
    for i, code in enumerate(idx):
        bits |= (int(code) & 7) << (3 * i)
    return struct.pack('BB', r0, r1) + bits.to_bytes(6, 'little')


def _fill_missing_reference(h: np.ndarray, c: np.ndarray):
    """The original row/column-loop NaN fill (pre-vectorisation)."""
    tv = h.shape[0]
    for col in range(tv):
        col_h = h[:, col]
        valid = ~np.isnan(col_h)
        if valid.all() or not valid.any():
            continue
        last_val = last_col_c = None
        for row in range(tv):
            if valid[row]:
                last_val = col_h[row]
                last_col_c = c[row, col].copy()
            elif last_val is not None:
                h[row, col] = last_val
                c[row, col] = last_col_c
        first_val = first_col_c = None
        for row in range(tv - 1, -1, -1):
            if not np.isnan(h[row, col]):
                first_val = h[row, col]
                first_col_c = c[row, col].copy()
            elif first_val is not None:
                h[row, col] = first_val
                c[row, col] = first_col_c
    for col in range(tv):
        if not np.any(np.isnan(h[:, col])):
            continue
        src = None
        for d in range(1, tv):
            if col - d >= 0 and not np.any(np.isnan(h[:, col - d])):
                src = col - d
                break
            if col + d < tv and not np.any(np.isnan(h[:, col + d])):
                src = col + d
                break
        if src is not None:
            nan_rows = np.isnan(h[:, col])
            h[nan_rows, col] = h[nan_rows, src]
            c[nan_rows, col] = c[nan_rows, src]
    nan_mask = np.isnan(h)
    if nan_mask.any():
        h[nan_mask] = 0.0


class TestDXT1Encoder:
    @pytest.mark.parametrize('size', [4, 16, 64, 128])
    def test_matches_per_block_reference(self, size):
        rng = np.random.default_rng(size)
        img = rng.integers(0, 256, (size, size, 3), dtype=np.uint8)
        assert encode_dxt1_quality(img) == _dxt1_reference(img)

    def test_flat_block_endpoints(self):
        """A uniform block hits the c0 == c1 branch; both endpoints must still
        encode a valid opaque (4-color) block."""
        for value in (0, 1, 128, 255):
            img = np.full((4, 4, 3), value, dtype=np.uint8)
            assert encode_dxt1_quality(img) == _dxt1_reference(img)

    def test_non_multiple_of_four_is_padded(self):
        rng = np.random.default_rng(0)
        img = rng.integers(0, 256, (13, 7, 3), dtype=np.uint8)
        out = encode_dxt1_quality(img)
        assert out == _dxt1_reference(img)
        assert len(out) == (16 // 4) * (8 // 4) * 8   # padded to 16x8, 8B/block


class TestBC4Encoder:
    @pytest.mark.parametrize('size', [4, 16, 64, 128])
    def test_matches_per_block_reference(self, size):
        rng = np.random.default_rng(size + 1)
        chan = rng.integers(0, 256, (size, size), dtype=np.uint8)
        expected = bytearray()
        for by in range(0, size, 4):
            for bx in range(0, size, 4):
                expected += _bc4_block_reference(chan[by:by + 4, bx:bx + 4].reshape(16))
        assert encode_bc4_channel(chan).tobytes() == bytes(expected)

    def test_flat_channel_uses_zero_indices(self):
        """All-equal blocks take the r0 == r1 path: indices must be all zero."""
        chan = np.full((8, 8), 200, dtype=np.uint8)
        out = encode_bc4_channel(chan)
        assert out.shape == (4, 8)
        for blk in out:
            assert blk[0] == 200 and blk[1] == 200
            assert bytes(blk[2:]) == b'\x00' * 6


class TestFillMissing:
    @pytest.mark.parametrize('tv,frac', [(33, 0.1), (129, 0.3), (129, 0.9)])
    def test_matches_loop_reference(self, tv, frac):
        rng = np.random.default_rng(tv)
        h = (rng.random((tv, tv)).astype(np.float32) * 1000)
        c = rng.integers(0, 256, (tv, tv, 3), dtype=np.uint8)
        h[rng.random((tv, tv)) < frac] = np.nan

        h_new, c_new = h.copy(), c.copy()
        h_ref, c_ref = h.copy(), c.copy()
        fill_missing(h_new, c_new)
        _fill_missing_reference(h_ref, c_ref)

        assert np.array_equal(h_new, h_ref)
        assert np.array_equal(c_new, c_ref)
        assert not np.isnan(h_new).any()

    def test_all_nan_grid_becomes_zero(self):
        h = np.full((33, 33), np.nan, dtype=np.float32)
        c = np.zeros((33, 33, 3), dtype=np.uint8)
        fill_missing(h, c)
        assert (h == 0.0).all()

    def test_fully_valid_grid_is_untouched(self):
        rng = np.random.default_rng(5)
        h = rng.random((33, 33)).astype(np.float32)
        c = rng.integers(0, 256, (33, 33, 3), dtype=np.uint8)
        h0, c0 = h.copy(), c.copy()
        fill_missing(h, c)
        assert np.array_equal(h, h0) and np.array_equal(c, c0)

    def test_empty_column_takes_nearest_valid(self):
        """A wholly-NaN column copies the nearest valid column (ties go left)."""
        h = np.zeros((8, 8), dtype=np.float32)
        for col in range(8):
            h[:, col] = col
        h[:, 3] = np.nan
        c = np.zeros((8, 8, 3), dtype=np.uint8)
        c[:, :, 0] = np.arange(8, dtype=np.uint8)[None, :]
        fill_missing(h, c)
        assert (h[:, 3] == 2.0).all()          # column 2, not 4


class TestTerrainLodNormalLayout:
    """Vanilla terrain-LOD normals are R = east, G = up, B = north.

    Measured on tamriel.4.0.0: Skyrim.esm LAND slopes against the vanilla
    _n.dds. The .btr has no vertex normals, so a map missing its up axis lit
    every hill like a wall.
    """

    def _rgb(self, heights):
        from asset_convert.lod.terrain_lod import _heightmap_normal_rgb
        return _heightmap_normal_rgb(heights.astype(np.float32), 64).astype(int)

    def test_flat_ground_points_up_in_green(self):
        rgb = self._rgb(np.zeros((33, 33)))
        assert (rgb[..., 1] >= 254).all()
        assert (abs(rgb[..., 0] - 128) <= 1).all() and (abs(rgb[..., 2] - 128) <= 1).all()

    def test_ground_rising_east_tilts_red_down(self):
        east = np.tile(np.arange(33) * 64.0, (33, 1))     # LAND: column = east
        r, g, b = self._rgb(east)[16, 16]
        assert r < 110 and g > 200 and abs(b - 128) <= 2

    def test_ground_rising_north_tilts_blue_down(self):
        north = np.tile((np.arange(33) * 64.0)[:, None], (1, 33))   # row 0 = south
        r, g, b = self._rgb(north)[16, 16]
        assert b < 110 and g > 200 and abs(r - 128) <= 2

    def test_the_map_keeps_all_three_channels(self, tmp_path):
        from asset_convert.texture.dds_codec import write_normal_dds
        from PIL import Image
        out = tmp_path / 'n.dds'
        write_normal_dds(self._rgb(np.zeros((33, 33))).astype(np.uint8), out)
        assert out.read_bytes()[84:88] == b'DXT1'
        assert np.asarray(Image.open(out).convert('RGB'))[..., 1].min() > 240
