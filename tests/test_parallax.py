"""Oblivion parallax -> Skyrim height map.

The three things that must hold, in order of how much damage they do when they
break:

  1. NOTHING happens unless the opt-in is on.  A parallax shape renders WRONG
     under vanilla SSE (verified in game -- the surface swims), so a default-on
     conversion would be worse than no conversion at all.
  2. A flagged shape whose texture holds NO height data is left alone.  Only
     44 of Nehrim's 130 flagged textures carry one; writing an empty height
     map for the rest produces exactly that swimming surface.
  3. When both conditions hold, the shape comes out as Skyrim builds one:
     shader type 3, SLSF1_Parallax, height in slot 3, vertex colors present.
"""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.texture import parallax
from asset_convert.texture.parallax import (classify_alpha,
                                    decode_alpha_plane,
                                    encode_bc4_dds, height_path)


# ---------------------------------------------------------------------------
# Synthetic DDS builders — the classifier reads real block data, so the tests
# ---------------------------------------------------------------------------

def _dds_header(w, h, fourcc, mips=1):
    hdr = bytearray(128)
    hdr[0:4] = b'DDS '
    struct.pack_into('<I', hdr, 4, 124)
    struct.pack_into('<I', hdr, 8, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000)
    struct.pack_into('<I', hdr, 12, h)
    struct.pack_into('<I', hdr, 16, w)
    struct.pack_into('<I', hdr, 28, mips)
    struct.pack_into('<I', hdr, 76, 32)
    struct.pack_into('<I', hdr, 80, 0x4)          # DDPF_FOURCC
    hdr[84:88] = fourcc
    return bytes(hdr)


def _dxt5(w, h, a0, a1, indices):
    """A DXT5 image whose every block uses the same alpha endpoints/indices."""
    bits = 0
    for i, idx in enumerate(indices):
        bits |= (idx & 0x7) << (i * 3)
    block = bytes((a0, a1)) + bits.to_bytes(6, 'little') + b'\x00' * 8
    nblocks = ((w + 3) // 4) * ((h + 3) // 4)
    return _dds_header(w, h, b'DXT5') + block * nblocks


def _dxt5_varied(w, h, endpoints):
    """DXT5 where each block gets its own endpoint pair, cycling `endpoints`.

    One block can express at most 8 values, so a texture with a genuinely wide
    range of heights needs blocks that differ — which is what a real gradient
    looks like and what the level count has to see.
    """
    nblocks = ((w + 3) // 4) * ((h + 3) // 4)
    bits = 0
    for i in range(16):
        bits |= (i % 8) << (i * 3)
    out = bytearray(_dds_header(w, h, b'DXT5'))
    for b in range(nblocks):
        a0, a1 = endpoints[b % len(endpoints)]
        out += bytes((a0, a1)) + bits.to_bytes(6, 'little') + b'\x00' * 8
    return bytes(out)


def _dxt3(w, h, nibbles):
    """DXT3: 4-bit explicit alpha, 16 nibbles per block — 16 values at most."""
    alpha = bytearray()
    for i in range(0, 16, 2):
        alpha.append((nibbles[i] & 0xF) | ((nibbles[i + 1] & 0xF) << 4))
    block = bytes(alpha) + b'\x00' * 8
    nblocks = ((w + 3) // 4) * ((h + 3) // 4)
    return _dds_header(w, h, b'DXT3') + block * nblocks


def _bc4_planes(blob):
    """Decode a BC4 DDS's top mip back to one byte per texel.

    A BC4 block is byte-for-byte a DXT5 alpha block, which is the whole reason
    the encoder needs no external tool -- so the test decodes it the same way.
    """
    h = struct.unpack_from('<I', blob, 12)[0]
    w = struct.unpack_from('<I', blob, 16)[0]
    pos = 148                                     # 128 header + 20 DX10
    out = bytearray(w * h)
    for byi in range((h + 3) // 4):
        for bxi in range((w + 3) // 4):
            a0, a1 = blob[pos], blob[pos + 1]
            if a0 > a1:
                pal = (a0, a1,
                       (6 * a0 + a1) // 7, (5 * a0 + 2 * a1) // 7,
                       (4 * a0 + 3 * a1) // 7, (3 * a0 + 4 * a1) // 7,
                       (2 * a0 + 5 * a1) // 7, (a0 + 6 * a1) // 7)
            else:
                pal = (a0, a1,
                       (4 * a0 + a1) // 5, (3 * a0 + 2 * a1) // 5,
                       (2 * a0 + 3 * a1) // 5, (a0 + 4 * a1) // 5, 0, 255)
            bits = int.from_bytes(blob[pos + 2:pos + 8], 'little')
            for ty in range(4):
                y = byi * 4 + ty
                if y >= h:
                    break
                for tx in range(4):
                    x = bxi * 4 + tx
                    if x < w:
                        out[y * w + x] = pal[(bits >> ((ty * 4 + tx) * 3)) & 7]
            pos += 8                              # BC4: one channel, 8 bytes
    return w, h, out


# A real height field: 64 blocks with differing endpoints, so the decoded
# plane carries hundreds of distinct values between 50 and 200.
HEIGHT_DDS = _dxt5_varied(32, 32, [(200 - i, 50 + i) for i in range(64)])
EMPTY_DDS = _dxt5(8, 8, 255, 255, [0] * 16)
BINARY_DDS = _dxt5(8, 8, 255, 0, [0, 1] * 8)
# Mostly extremes with a thin transition: passes the mid-tone test but is still
# a cutout mask (vegetation, splatter), so it must NOT convert.
BIMODAL_DDS = _dxt5(8, 8, 255, 0, [0] * 7 + [1] * 6 + [2, 3, 4])
DXT1_DDS = _dds_header(8, 8, b'DXT1') + b'\x00' * (4 * 8)
# Height-SHAPED but 4-bit: smooth mid-tone spread, only ten distinct values.
# This is what Oblivion's beach rocks are, and under a parallax shader it
# renders as terracing rather than depth.
DXT3_COARSE_DDS = _dxt3(8, 8, [3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
                               3, 5, 7, 9, 11, 4])
# The same failure in DXT5, to prove the test is about RESOLUTION and not
# about the FourCC: one endpoint pair everywhere means eight values total.
DXT5_COARSE_DDS = _dxt5(8, 8, 200, 50, list(range(8)) * 2)


def _flat_share(a, band=None):
    """Share of the surface within +/-band of its own median, counted the slow
    honest way — the module computes it from a histogram instead."""
    band = parallax.FLAT_BAND if band is None else band
    med = sorted(a)[len(a) // 2]
    return sum(1 for v in a if abs(v - med) <= band) / len(a)


class TestAlphaClassification:
    """Only kind == 'height' may be converted, and the CATEGORY is the point:
    the build log has to be able to say why a shape was skipped."""

    def test_smooth_gradient_is_a_height_field(self):
        assert classify_alpha(HEIGHT_DDS).kind == 'height'

    def test_flat_alpha_is_empty(self):
        info = classify_alpha(EMPTY_DDS)
        assert info.kind == 'empty'
        # Measured on Nehrim: flat alpha reads WHITE, not black.  Anything that
        # assumes an unused channel is 0 gets these backwards.
        assert info.mean == 255

    def test_two_level_alpha_is_a_cutout_mask(self):
        assert classify_alpha(BINARY_DDS).kind == 'binary'

    def test_soft_edged_mask_is_bimodal(self):
        assert classify_alpha(BIMODAL_DDS).kind == 'bimodal'

    def test_dxt1_has_no_alpha_to_read(self):
        # 67 of the 130 flagged textures are this: vanilla Oblivion sets the
        # flag and ships no data, so Oblivion renders no parallax there either.
        assert classify_alpha(DXT1_DDS).kind == 'no_alpha'

    def test_garbage_is_unreadable_not_height(self):
        assert classify_alpha(b'not a dds').kind == 'unreadable'

    def test_only_height_is_usable(self):
        assert classify_alpha(HEIGHT_DDS).usable
        for blob in (EMPTY_DDS, BINARY_DDS, BIMODAL_DDS, DXT1_DDS,
                     DXT3_COARSE_DDS, DXT5_COARSE_DDS):
            assert not classify_alpha(blob).usable

    def test_four_bit_alpha_is_too_coarse_to_be_a_surface(self):
        """The beach-rock defect, found in game.

        DXT3 keeps 4-bit explicit alpha: at most 16 values however it was
        authored. RockBeach04 measured SEVEN levels across a range of 102 --
        ~15 units per step -- and a parallax shader offsets by that, so the
        surface terraced instead of gaining depth. Range and distribution all
        looked healthy, which is why the first classifier accepted it.
        """
        info = classify_alpha(DXT3_COARSE_DDS)
        assert info.kind == 'quantised'
        assert info.fmt == 'dxt3'
        assert info.levels <= 16
        # It got that far: it is not flat, not a mask.
        assert info.rng >= 30 and info.mid_ratio >= 0.15

    def test_coarse_dxt5_is_rejected_too(self):
        """Counting LEVELS, not testing the FourCC. A DXT5 alpha that happens
        to be an eight-step staircase is just as unusable as a DXT3 one."""
        info = classify_alpha(DXT5_COARSE_DDS)
        assert info.kind == 'quantised'
        assert info.fmt == 'dxt5'
        assert info.levels == 8

    def test_a_real_height_field_has_many_levels(self):
        # Measured on Nehrim: every usable texture had 147-256 distinct
        # values, every rejected one 7-16. Two clusters, nothing between.
        info = classify_alpha(HEIGHT_DDS)
        assert info.kind == 'height'
        assert info.levels >= 64

    def test_dxt5_palette_is_interpolated_not_sampled(self):
        """Reading only the two endpoints misreads every smooth gradient as
        binary, because a gentle block's endpoints sit far apart while all six
        interpolated values between them are mid-tone."""
        info = classify_alpha(HEIGHT_DDS)
        assert info.mid_ratio == 1.0
        assert info.edge_ratio == 0.0


class TestHeightMapEncoding:

    def test_alpha_plane_decodes_to_one_byte_per_texel(self):
        w, h, plane = decode_alpha_plane(HEIGHT_DDS)
        assert (w, h) == (32, 32)
        assert len(plane) == 32 * 32
        assert min(plane) >= 50 and max(plane) <= 200

    def test_bc4_output_is_a_bc4_dds_with_a_full_mip_chain(self):
        w, h, plane = decode_alpha_plane(HEIGHT_DDS)
        blob = encode_bc4_dds(w, h, plane)
        assert blob[:4] == b'DDS '
        assert blob[84:88] == b'DX10'
        assert struct.unpack_from('<I', blob, 128)[0] == 80   # DXGI BC4_UNORM
        # 32x32 down to 1x1 is six levels; a truncated chain shimmers at range.
        assert struct.unpack_from('<I', blob, 28)[0] == 6

    def test_bc4_round_trip_preserves_the_height_field(self):
        w, h, plane = decode_alpha_plane(HEIGHT_DDS)
        blob = encode_bc4_dds(w, h, plane, mipmaps=False)
        rw, rh, back = _bc4_planes(blob)
        assert (rw, rh) == (w, h)
        # Same block layout in and out, so this is near-lossless; a wrong
        # endpoint order or index packing shows up immediately.
        assert max(abs(a - b) for a, b in zip(plane, back)) <= 2

    def test_height_path_suffix(self):
        assert height_path('Textures\\tes4\\x\\stone.dds') == \
            'Textures\\tes4\\x\\stone_p.dds'
        assert height_path('Textures\\tes4\\x\\stone.DDS') == \
            'Textures\\tes4\\x\\stone_p.dds'

    def test_amplitude_cap_leaves_a_good_map_alone(self):
        """The property a mod's own maps depend on.

        Calibrated on 56 PAIRS of the same texture — hand-tuned on one side
        (works in Oblivion AND Skyrim), Nehrim's original on the other. The
        hand-tuned set spans amplitude 130..148, Nehrim's 95..255, so a cap of
        the threshold keeps 100% of the good maps bit-identical while still
        correcting the over-deep ones. Measured over the WHOLE reference folder:
        1738 height fields, max amplitude 148, zero touched.
        """
        good = bytearray(60 + (v * 145) // 255 for v in range(256))
        assert max(good) - min(good) <= 150
        out = parallax.normalize_height(good)
        assert bytes(out) == bytes(good), 'a well-made map was modified'

    def test_amplitude_cap_compresses_an_over_deep_map(self):
        deep = bytearray(range(256))                     # amplitude 255
        out = parallax.normalize_height(deep)
        assert max(out) - min(out) == parallax.DEFAULT_MAX_RANGE

    def test_a_corrected_map_is_moved_onto_the_target_median(self):
        """Measured target: the hand-tuned medians sit in a tight band around
        117 while Nehrim's scatter 0..216."""
        # full 0..255 spread but body sitting low, like Nehrim's
        deep = bytearray([v % 61 for v in range(200)]
                         + [195 + v % 61 for v in range(56)])
        assert max(deep) - min(deep) > 150 and sorted(deep)[128] < 60
        out = parallax.normalize_height(deep)
        assert abs(sorted(out)[len(out) // 2] - parallax.TARGET_MEDIAN) <= 1

    def test_centering_never_clips(self):
        """A shift that would push texels past 0 or 255 is limited instead —
        clipping would flatten real relief into a plateau."""
        high = bytearray([195 + v % 61 for v in range(200)]
                         + [v % 61 for v in range(56)])
        assert max(high) - min(high) > 150 and sorted(high)[128] > 195
        out = parallax.normalize_height(high)
        assert min(out) >= 0 and max(out) <= 255
        # nothing piled up on a boundary
        assert list(out).count(0) <= list(high).count(0)
        assert list(out).count(255) <= list(high).count(255)
        # the relief survives: same number of distinct steps, give or take
        # what the compression itself merges
        assert len(set(out)) > 1

    def test_a_good_map_keeps_its_median_too(self):
        """Centering is a CORRECTION, not a detector — it must never reach a
        map the amplitude cap let through."""
        good = bytearray(20 + (v * 140) // 255 for v in range(256))
        assert max(good) - min(good) <= 150
        out = parallax.normalize_height(good)
        assert bytes(out) == bytes(good)
        assert sorted(out)[len(out) // 2] != parallax.TARGET_MEDIAN

    @pytest.mark.parametrize('target', [0, 120, 100, 80])
    def test_lowering_the_target_never_reaches_a_good_map(self, target):
        """The property the whole design exists for.

        Detection and correction depth are two separate numbers, so the depth
        can be dialled to whatever looks right in game without ever touching a
        map a modder built on purpose. The hand-tuned reference set spans
        amplitude 130..148 — measured over the WHOLE reference folder, 1738
        height fields, max 148 — and detection sits at 160 above all of them.
        """
        cap = parallax.DEFAULT_MAX_RANGE
        # two whole reference folders were measured: max amplitude 148 and 156
        assert cap > 156, 'detection must clear BOTH hand-tuned ceilings'
        good = bytearray(20 + (v * 156) // 255 for v in range(256))
        deep = bytearray([v % 61 for v in range(200)]
                         + [195 + v % 61 for v in range(56)])
        g = parallax.normalize_height(good, target_range=target)
        assert bytes(g) == bytes(good), 'a hand-tuned map was damaged'
        d = parallax.normalize_height(deep, target_range=target)
        assert max(d) - min(d) == (target or cap)

    def test_the_measure_ignores_outliers(self):
        """The defect that forced this rebuild, as a fixture.

        The old objective was the share of texels in the bottom third of
        min..max. Its threshold comes from the EXTREMES, so two bright texels
        stretch the range and drag the whole surface into the "deep" band:
        `leyawiinmetalstrip03` — a flat plate with two rivets — scored 94.2%
        deep while 93.7% of its area sits within +/-20 of its median.

        The band measure has to call that plate what it is: flat.
        """
        plate = bytearray([59 + (v % 9) for v in range(4094)]) + \
            bytearray([33, 179])                     # the two rivets
        lo, hi = min(plate), max(plate)
        deep = sum(1 for v in plate if v < lo + (hi - lo) / 3) / len(plate)
        assert deep > 0.90, 'fixture must reproduce the old measure being wrong'
        assert _flat_share(plate) > 0.90, 'a flat plate read as restless'

    def test_the_fast_share_agrees_with_counting_texels(self):
        """`_flat_share_at` predicts the post-curve share from a histogram
        instead of applying the curve, by inverting it. That inversion is the
        subtlest step in the module, so it is checked against brute force."""
        a = bytearray([(v * 7) % 256 for v in range(4096)])
        cum, total = parallax._cumulative(a)
        med = parallax._median_from(cum, total)
        assert med == sorted(a)[len(a) // 2]
        for p in (1.0, 1.3, 1.9, 2.6, 4.0):
            predicted = parallax._flat_share_at(cum, total, min(a), max(a),
                                                med, 30, p)
            applied = parallax._flatten(a, min(a), max(a), med, p)
            actual = sum(1 for v in applied if abs(v - med) <= 30) / len(a)
            assert abs(predicted - actual) < 0.01, \
                f'p={p}: predicted {predicted:.3f}, actual {actual:.3f}'

    def test_the_curve_is_monotone_in_its_exponent(self):
        """What makes the bisection valid — and what the old family lacked.

        `x**g` compresses one END of the range, so the share inside a band
        around the median is not monotone in g: measured over the 38 shipped
        maps, 21 DIP before they rise as g falls. This curve moves every texel
        weakly CLOSER to the median as p grows, so the share can only rise.
        """
        a = bytearray([(v * 13) % 256 for v in range(4096)])
        lo, hi = min(a), max(a)
        med = sorted(a)[len(a) // 2]
        shares = [_flat_share(parallax._flatten(a, lo, hi, med, p))
                  for p in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0)]
        assert shares == sorted(shares), shares
        assert shares[-1] > shares[0]

    def test_the_tone_curve_concentrates_the_body_around_its_median(self):
        """The finding that explains the eye, restated outlier-proof.

        Over the 56 pairs, 63.2% of a hand-tuned map sits within +/-20 of its
        median against 36.6% of Nehrim's: the hand-tuned wall is a flat face
        with narrow mortar grooves, ours is restless everywhere. That is a
        SHAPE difference, and shifting cannot touch it.
        """
        # a broad restless body with grooves and highlights, like Nehrim's
        restless = bytearray()
        for v in range(256):
            restless += bytes([v]) * (4 if 60 <= v <= 190 else 1)
        before = _flat_share(restless)
        assert before < 0.35, before
        out = parallax.normalize_height(restless, target_range=140)
        after = _flat_share(out)
        assert after > before * 1.5, f'{before:.2f} -> {after:.2f}'

    def test_a_field_parked_at_the_bottom_is_recentered(self):
        """`durchgangD`, and why the band measure does not cover it.

        A black wall — median 17, amplitude 158 — reads 89.2% FLAT on the band
        measure, correctly: it is flat, just parked at the channel bottom. A
        parallax shader offsets along the view vector, so a surface near 0
        renders not as depth but as a constant view-dependent UV shift: the
        texture slides as the camera moves. Amplitude misses it (158 < 163).
        """
        wall = bytearray([(v % 40) for v in range(4000)] + [158])
        assert max(wall) - min(wall) < parallax.DEFAULT_MAX_RANGE
        assert sorted(wall)[len(wall) // 2] < parallax.MIN_MEDIAN
        out = parallax.normalize_height(wall)
        assert sorted(out)[len(out) // 2] > parallax.MIN_MEDIAN, 'not lifted'

    def test_recentering_alone_cannot_damage_relief(self):
        """What a map caught ONLY by the median floor is allowed to receive.

        A pure translation, and nothing else: no compression, no tone curve.
        That is what makes the second detector safe to add — every level, every
        gradient and every gap survives a shift unchanged.
        """
        wall = bytearray([(v % 40) for v in range(4000)] + [158])
        out = parallax.normalize_height(wall)
        assert max(out) - min(out) == max(wall) - min(wall), 'amplitude moved'
        assert len(set(out)) == len(set(wall)), 'levels lost'
        offs = {o - i for i, o in zip(wall, out)}
        assert len(offs) == 1, f'not a pure translation: {sorted(offs)[:5]}'

    def test_the_median_floor_never_reaches_the_reference_population(self):
        """Centering was rejected once and needed new evidence to come back.

        The old refutation tested a TOLERANCE AROUND MID-GREY on 56 pairs,
        where the medians overlap hopelessly. This is a one-sided FLOOR, and
        the evidence is the whole 3631-map reference corpus: its median level
        runs 52..179, so nothing the author shipped is darker than 52. The
        floor sits in the empty gap between the darkest Nehrim case it must
        catch (36) and that 52.
        """
        assert parallax.MIN_MEDIAN < 52, 'would touch hand-tuned maps'
        assert parallax.MIN_MEDIAN > 36, 'would miss durchgangD and siblings'
        # a reference-like map: amplitude inside the cap, median well above
        good = bytearray(52 + (v * 140) // 255 for v in range(256))
        assert sorted(good)[128] >= 52
        assert bytes(parallax.normalize_height(good)) == bytes(good)

    def test_the_curve_leaves_relief_inside_the_body(self):
        """The ceiling on the exponent, and why it is derived rather than set.

        Pressing the body flat enough to hit the target on a genuinely
        restless texture would leave a face with no relief at all — as wrong
        as the restlessness it fixes. So the fit stops where the band would
        drop below `_MIN_BODY_LEVELS` distinct output levels, and a partial
        correction is accepted instead.
        """
        restless = bytearray()
        for v in range(256):
            restless += bytes([v]) * (4 if 60 <= v <= 190 else 1)
        out = parallax.normalize_height(restless, target_range=140)
        med = sorted(out)[len(out) // 2]
        body = {v for v in out if abs(v - med) <= parallax.FLAT_BAND}
        assert len(body) >= parallax._MIN_BODY_LEVELS, \
            f'body pressed flat: {len(body)} levels'

    def test_the_tone_curve_cannot_posterise(self):
        """`x**g` has unbounded slope at 0, so on a field reaching down to 0
        an unrestrained fit shreds it: cave04 came out of the first version
        with 45 of 256 levels and a 96-level hole, flat plateaus with black
        punched through. The gamma floor is what stops that, and it must hold
        even when the deep target is unreachable.
        """
        # CONTINUOUS source — every level 0..255 present, dark ones four times
        # as common. A fixture with its own holes would only prove the curve
        # inherits them.
        dark = bytearray(list(range(256)) + [v for v in range(90)] * 4)
        src = sorted(set(dark))
        assert min(dark) == 0
        assert max(src[i + 1] - src[i] for i in range(len(src) - 1)) == 1
        out = parallax.normalize_height(dark, target_range=140)
        used = sorted(set(out))
        gap = max(used[i + 1] - used[i] for i in range(len(used) - 1))
        assert gap <= 12, f'curve opened a {gap}-level hole'
        assert len(used) > 60, 'most of the range must survive'

    def test_the_tone_curve_never_reaches_a_good_map(self):
        good = bytearray(20 + (v * 156) // 255 for v in range(256))
        assert bytes(parallax.normalize_height(good)) == bytes(good)

    def test_cap_can_be_disabled(self):
        deep = bytearray(range(256))
        assert bytes(parallax.normalize_height(deep, max_range=0)) == \
            bytes(deep)

    def test_strength_is_an_extra_factor_on_top(self):
        mild = bytearray(60 + (v * 100) // 255 for v in range(256))
        assert bytes(parallax.normalize_height(mild)) == bytes(mild)
        out = parallax.normalize_height(mild, strength=0.5)
        assert abs((max(out) - min(out)) - 50) <= 1

    def test_build_height_map_writes_the_file(self, tmp_path):
        src = tmp_path / 'diffuse.dds'
        src.write_bytes(HEIGHT_DDS)
        out = tmp_path / 'sub' / 'diffuse_p.dds'
        assert parallax.build_height_map(str(src), str(out))
        assert out.is_file()
        assert out.read_bytes()[84:88] == b'DX10'
        # No temp file left behind by the atomic write.
        assert list(out.parent.glob('*.tmp')) == []

    def test_build_height_map_refuses_a_texture_with_no_alpha(self, tmp_path):
        src = tmp_path / 'diffuse.dds'
        src.write_bytes(DXT1_DDS)
        out = tmp_path / 'diffuse_p.dds'
        assert not parallax.build_height_map(str(src), str(out))
        assert not out.exists()


# ---------------------------------------------------------------------------
# The converter side
# ---------------------------------------------------------------------------

@pytest.fixture
def nif():
    import time
    if not hasattr(time, 'clock'):
        time.clock = time.perf_counter
    from pyffi.formats.nif import NifFormat
    return NifFormat


def _tree(tmp_path, dds_bytes, rel='rocks\\stone.dds'):
    """A minimal source tree: <tmp>/meshes/a.nif beside <tmp>/textures/...

    resolve_source_texture maps the rewritten path back through exactly this
    layout, so the test has to reproduce it rather than hand over a bare file.
    """
    tex = tmp_path / 'textures' / Path(rel.replace('\\', '/'))
    tex.parent.mkdir(parents=True, exist_ok=True)
    tex.write_bytes(dds_bytes)
    (tmp_path / 'meshes').mkdir(parents=True, exist_ok=True)
    return str(tmp_path / 'meshes' / 'a.nif')


def _shape(NifFormat, apply_mode, tex_rel='rocks\\stone.dds',
           vertex_colors=True, nverts=4):
    shape = NifFormat.NiTriShape()
    shape.data = NifFormat.NiTriShapeData()
    shape.data.num_vertices = nverts
    shape.data.has_vertices = True
    shape.data.vertices.update_size()
    if vertex_colors:
        shape.data.has_vertex_colors = True
        shape.data.vertex_colors.update_size()
        for c in shape.data.vertex_colors:
            c.r = c.g = c.b = c.a = 0.5

    texprop = NifFormat.NiTexturingProperty()
    texprop.apply_mode = apply_mode
    texprop.has_base_texture = True
    src = NifFormat.NiSourceTexture()
    src.file_name = ('textures\\' + tex_rel).encode()
    texprop.base_texture.source = src

    shape.num_properties = 1
    shape.properties.update_size()
    shape.properties[0] = texprop
    return shape


def _convert(shape, src_nif, parallax_on):
    """Convert one shape, with the per-process alpha cache cleared first."""
    from asset_convert.nif import nif_converter as nc
    from asset_convert.nif import shaders
    shaders._PARALLAX_ALPHA_CACHE.clear()
    stats = {'_src_path': src_nif, '_parallax': parallax_on}
    ts = nc.process_geometry(shape, fix_textures=True, stats=stats)
    return ts, stats


class TestParallaxIsOptIn:
    """The most important behaviour in this file.  A correct parallax shape
    SWIMS under vanilla SSE, so the default must be a no-op."""

    def test_flagged_shape_is_untouched_without_the_switch(self, nif, tmp_path):
        src = _tree(tmp_path, HEIGHT_DDS)
        ts, stats = _convert(_shape(nif, parallax.APPLY_HILIGHT2), src, False)
        shader = ts.bs_properties[0]
        assert shader.skyrim_shader_type == 0
        assert int(shader.shader_flags_1.slsf_1_parallax) == 0
        assert bytes(shader.texture_set.textures[3]) == b''
        assert '_parallax_maps' not in stats

    @pytest.mark.parametrize('name', ['wall_far.nif', 'wall_far8.nif',
                                      'wall_far16.nif'])
    def test_a_distant_lod_tier_mesh_is_never_converted(self, nif, tmp_path,
                                                        name):
        """Found by `parallax_check.py verify`: 60 malformed shapes, every one
        of them in a `_far`/`_far8`/`_far16` mesh, all "no vertex colors".

        The LOD stage regenerates those from the full model and knows nothing
        about parallax, so it drops the vertex colors the heightmap shader
        needs while leaving shader type 3 behind. It also made the result
        depend on whether meshes or LOD ran last. A height offset at LOD
        distance is invisible anyway.
        """
        from asset_convert.nif import nif_converter as nc
        from asset_convert.nif import shaders
        src = _tree(tmp_path, HEIGHT_DDS)
        far = str(Path(src).with_name(name))
        shaders._PARALLAX_ALPHA_CACHE.clear()
        stats = {'_src_path': far, '_parallax': True}
        ts = nc.process_geometry(_shape(nif, parallax.APPLY_HILIGHT2),
                                  fix_textures=True, stats=stats)
        shader = ts.bs_properties[0]
        assert shader.skyrim_shader_type == 0
        assert bytes(shader.texture_set.textures[3]) == b''
        assert stats['parallax_skipped_lod_tier'] == 1
        assert '_parallax_maps' not in stats

    def test_a_normal_mesh_named_like_a_farm_still_converts(self, nif,
                                                            tmp_path):
        """The suffix test must not swallow ordinary names ending in 'far'."""
        from asset_convert.nif import shaders
        assert not shaders._is_lod_tier_mesh('meshes\\clutter\\farm.nif')
        assert not shaders._is_lod_tier_mesh('meshes\\x\\barnfar.nif')
        assert shaders._is_lod_tier_mesh('meshes\\x\\wall_far.nif')
        assert shaders._is_lod_tier_mesh('meshes\\x\\WALL_FAR16.NIF')

    def test_unflagged_shape_is_untouched_with_the_switch(self, nif, tmp_path):
        """The mesh flag is the AUTHORED intent and is never guessed at: a
        usable height texture on a MODULATE shape converts nothing."""
        src = _tree(tmp_path, HEIGHT_DDS)
        ts, stats = _convert(_shape(nif, 2), src, True)     # APPLY_MODULATE
        shader = ts.bs_properties[0]
        assert shader.skyrim_shader_type == 0
        assert bytes(shader.texture_set.textures[3]) == b''


class TestLodMeshesNeverCarryParallax:
    """Two independent routes produce a `_far.nif`, and BOTH must come out
    without parallax — skipping it at conversion covers only the first."""

    def test_a_derived_lod_tier_is_stripped(self, nif):
        """`_decimate_and_write` reduces the FULL model in place and copies its
        shader verbatim, so a parallax source hands shader type 3, the parallax
        flag and the slot-3 height map straight to the LOD tier — while the
        decimation rebuilds the geometry and drops the vertex colors that
        shader needs. That is what rendered unlit-black."""
        from asset_convert.lod.lod_far_gen import strip_parallax

        texset = nif.BSShaderTextureSet()
        texset.num_textures = 9
        texset.textures.update_size()
        texset.textures[0] = b'textures\\tes4\\lazeon\\wandb.dds'
        texset.textures[3] = b'Textures\\tes4\\lazeon\\wandb_p.dds'
        shader = nif.BSLightingShaderProperty()
        shader.texture_set = texset
        shader.skyrim_shader_type = parallax.SHADER_TYPE_HEIGHTMAP
        shader.shader_flags_1.slsf_1_parallax = 1

        class _Doc:
            blocks = [shader]

        assert strip_parallax(_Doc()) == 1
        assert int(shader.skyrim_shader_type) == 0
        assert int(shader.shader_flags_1.slsf_1_parallax) == 0
        assert bytes(texset.textures[3]) == b''
        # the diffuse is untouched — this clears parallax, not the material
        assert bytes(texset.textures[0]) == \
            b'textures\\tes4\\lazeon\\wandb.dds'

    def test_a_plain_lod_mesh_is_left_alone(self, nif):
        from asset_convert.lod.lod_far_gen import strip_parallax

        texset = nif.BSShaderTextureSet()
        texset.num_textures = 9
        texset.textures.update_size()
        texset.textures[0] = b'textures\\tes4\\rocks\\stone.dds'
        shader = nif.BSLightingShaderProperty()
        shader.texture_set = texset

        class _Doc:
            blocks = [shader]

        assert strip_parallax(_Doc()) == 0
        assert bytes(texset.textures[0]) == b'textures\\tes4\\rocks\\stone.dds'


class TestParallaxNeedsRealHeightData:

    @pytest.mark.parametrize('blob,kind', [
        (EMPTY_DDS, 'empty'),
        (BINARY_DDS, 'binary'),
        (BIMODAL_DDS, 'bimodal'),
        (DXT1_DDS, 'no_alpha'),
        (DXT3_COARSE_DDS, 'quantised'),
        (DXT5_COARSE_DDS, 'quantised'),
    ])
    def test_flagged_but_empty_texture_is_left_flat(self, nif, tmp_path,
                                                    blob, kind):
        src = _tree(tmp_path, blob)
        ts, stats = _convert(_shape(nif, parallax.APPLY_HILIGHT2), src, True)
        shader = ts.bs_properties[0]
        assert shader.skyrim_shader_type == 0, \
            f'{kind} texture was converted -- that shape will swim in game'
        assert bytes(shader.texture_set.textures[3]) == b''
        # Named per category so the build log gives the next reader a lead.
        assert stats[f'parallax_skipped_{kind}'] == 1

    def test_unresolvable_texture_is_counted_not_crashed(self, nif, tmp_path):
        src = _tree(tmp_path, HEIGHT_DDS)
        shape = _shape(nif, parallax.APPLY_HILIGHT2, tex_rel='rocks\\gone.dds')
        ts, stats = _convert(shape, src, True)
        assert ts.bs_properties[0].skyrim_shader_type == 0
        assert stats['parallax_texture_unresolved'] == 1


class TestParallaxShapeConstruction:
    """What the in-game test confirmed renders correctly under Community
    Shaders: type 3, SLSF1_Parallax, height in slot 3, vertex colors."""

    def test_shader_is_rebuilt_for_the_heightmap_path(self, nif, tmp_path):
        src = _tree(tmp_path, HEIGHT_DDS)
        ts, stats = _convert(_shape(nif, parallax.APPLY_HILIGHT2), src, True)
        shader = ts.bs_properties[0]
        assert shader.skyrim_shader_type == parallax.SHADER_TYPE_HEIGHTMAP
        assert int(shader.shader_flags_1.slsf_1_parallax) == 1
        assert bytes(shader.texture_set.textures[3]) == \
            b'Textures\\tes4\\rocks\\stone_p.dds'
        assert stats['parallax_shapes'] == 1

    def test_env_and_glow_are_cleared(self, nif, tmp_path):
        """Both are mutually exclusive with the height path -- the shader has
        one auxiliary slot and type 3 claims it."""
        src = _tree(tmp_path, HEIGHT_DDS)
        ts, _ = _convert(_shape(nif, parallax.APPLY_HILIGHT2), src, True)
        shader = ts.bs_properties[0]
        assert int(shader.shader_flags_1.slsf_1_environment_mapping) == 0
        assert int(shader.shader_flags_2.slsf_2_glow_map) == 0

    def test_missing_vertex_colors_are_added_as_white(self, nif, tmp_path):
        """Skyrim's heightmap shader needs them present; 848 of the 1551
        converted shapes have none of their own and would render unlit-black
        without."""
        src = _tree(tmp_path, HEIGHT_DDS)
        shape = _shape(nif, parallax.APPLY_HILIGHT2, vertex_colors=False)
        ts, stats = _convert(shape, src, True)
        assert ts.data.has_vertex_colors
        assert len(ts.data.vertex_colors) == 4
        for c in ts.data.vertex_colors:
            assert (c.r, c.g, c.b, c.a) == (1.0, 1.0, 1.0, 1.0)
        assert int(ts.bs_properties[0].shader_flags_2.slsf_2_vertex_colors) == 1
        assert stats['parallax_vertex_colors_added'] == 1

    def test_existing_vertex_colors_are_kept(self, nif, tmp_path):
        src = _tree(tmp_path, HEIGHT_DDS)
        ts, stats = _convert(_shape(nif, parallax.APPLY_HILIGHT2), src, True)
        assert ts.data.vertex_colors[0].r == 0.5, 'authored colors overwritten'
        assert 'parallax_vertex_colors_added' not in stats

    def test_build_job_points_at_the_source_texture(self, nif, tmp_path):
        src = _tree(tmp_path, HEIGHT_DDS)
        _, stats = _convert(_shape(nif, parallax.APPLY_HILIGHT2), src, True)
        jobs = list(stats['_parallax_maps'].values())
        assert len(jobs) == 1
        assert jobs[0]['height_rel'] == 'Textures\\tes4\\rocks\\stone_p.dds'
        assert Path(jobs[0]['src']).read_bytes() == HEIGHT_DDS


# ---------------------------------------------------------------------------
# Output conditioning: Mitchell halve -> Gaussian blur -> tone curve -> BC4.
# ---------------------------------------------------------------------------

def _bc1_palette(c0, c1, four_color):
    """The four RGB triples a BC1 block names, in index order."""
    def rgb(c):
        return (((c >> 11) & 0x1F) * 255 // 31,
                ((c >> 5) & 0x3F) * 255 // 63,
                (c & 0x1F) * 255 // 31)
    a, b = rgb(c0), rgb(c1)
    if four_color:
        return [a, b,
                tuple((2 * a[i] + b[i]) // 3 for i in range(3)),
                tuple((a[i] + 2 * b[i]) // 3 for i in range(3))]
    return [a, b, tuple((a[i] + b[i]) // 2 for i in range(3)), None]


def _decode_color_block(blk, always_four_color):
    """The 16 texels of one 8-byte BC1 / DXT5-color block."""
    c0, c1, idx = struct.unpack('<HHI', blk)
    pal = _bc1_palette(c0, c1, always_four_color or c0 > c1)
    return [pal[(idx >> (2 * i)) & 3] for i in range(16)]


def _dxt5_with_color_blocks(w, h, color_blocks):
    """DXT5 whose color halves are given verbatim; alpha is a real gradient."""
    abits = 0
    for i in range(16):
        abits |= (i % 8) << (i * 3)
    alpha = bytes((250, 10)) + abits.to_bytes(6, 'little')
    n = ((w + 3) // 4) * ((h + 3) // 4)
    out = bytearray(_dds_header(w, h, b'DXT5'))
    for i in range(n):
        out += alpha + color_blocks[i % len(color_blocks)]
    return bytes(out)


class TestHalveAndBlur:

    def test_mitchell_weights_sum_to_one(self):
        assert parallax._MITCHELL_WEIGHTS.sum() == pytest.approx(1.0, abs=1e-6)

    def test_halving_gives_half_dimensions(self):
        w, h, plane = parallax.mitchell_halve(64, 32, bytearray(64 * 32))
        assert (w, h) == (32, 16)
        assert len(plane) == 32 * 16

    def test_a_flat_field_survives_halving_unchanged(self):
        flat = bytearray([137]) * (32 * 32)
        _, _, out = parallax.mitchell_halve(32, 32, flat)
        assert set(out) == {137}, 'Mitchell rang on a constant field'

    def test_a_flat_field_survives_the_blur_unchanged(self):
        flat = bytearray([90]) * (32 * 32)
        out = parallax.gaussian_blur(32, 32, flat, 4.0)
        assert set(out) == {90}, 'blur leaked at the clamped edges'

    def test_the_blur_reduces_variation(self):
        checker = bytearray(
            (0 if (x // 2 + y // 2) % 2 else 255)
            for y in range(32) for x in range(32))
        before = max(checker) - min(checker)
        out = parallax.gaussian_blur(32, 32, checker, 4.0)
        assert max(out) - min(out) < before

    def test_blur_radius_scales_with_width(self):
        assert parallax.blur_radius_for(1000) == pytest.approx(
            parallax.BLUR_RADIUS_PER_1000)
        assert (parallax.blur_radius_for(2048)
                == pytest.approx(2 * parallax.blur_radius_for(1024)))

    def test_a_tiny_radius_is_a_no_op(self):
        plane = bytearray(range(64)) * 16
        assert parallax.gaussian_blur(64, 16, plane, 0.2) is plane

    def test_the_height_map_ships_at_half_the_diffuse_size(self, tmp_path):
        src = tmp_path / 'stone.dds'
        src.write_bytes(HEIGHT_DDS)
        out = tmp_path / 'stone_p.dds'
        assert parallax.build_height_map(str(src), str(out))
        blob = out.read_bytes()
        assert (struct.unpack_from('<I', blob, 16)[0]
                == struct.unpack_from('<I', HEIGHT_DDS, 16)[0] // 2)
        assert (struct.unpack_from('<I', blob, 12)[0]
                == struct.unpack_from('<I', HEIGHT_DDS, 12)[0] // 2)


class TestGlobalDepthScale:
    """scale_depth: one affine map, the SAME one for every texture.

    128 is the neutral plane in Community Shaders -- it pivots the height on
    0.5 in AdjustDisplacementNormalized and starts the POM ray at half of
    maxHeight -- so compressing toward 128 reduces displacement in both
    directions without turning a groove into a bump.
    """

    def test_the_neutral_level_is_a_fixed_point(self):
        out = parallax.scale_depth(bytearray([parallax.NEUTRAL_LEVEL]), 0.5)
        assert out[0] == parallax.NEUTRAL_LEVEL

    def test_excursion_is_scaled_by_the_factor(self):
        a = bytearray([28, 128, 228])
        out = parallax.scale_depth(a, 0.5)
        assert list(out) == [78, 128, 178]

    def test_relative_depth_between_maps_survives(self):
        # the whole reason this is global: a plaster wall must stay flatter
        # than a cave wall
        mild = bytearray([118, 128, 138])
        deep = bytearray([68, 128, 188])
        m = parallax.scale_depth(mild, 0.5)
        d = parallax.scale_depth(deep, 0.5)
        before = (max(deep) - min(deep)) / (max(mild) - min(mild))
        after = (max(d) - min(d)) / (max(m) - min(m))
        assert after == pytest.approx(before, abs=0.01)

    def test_relative_structure_within_a_map_survives(self):
        a = bytearray([0, 50, 100, 150, 200, 250])
        out = parallax.scale_depth(a, 0.4)
        gaps = [out[i + 1] - out[i] for i in range(len(out) - 1)]
        assert max(gaps) - min(gaps) <= 1, gaps

    def test_grooves_stay_grooves(self):
        # below neutral must stay below, above must stay above
        a = bytearray([26, 179])
        out = parallax.scale_depth(a, 0.5)
        assert out[0] < parallax.NEUTRAL_LEVEL < out[1]

    def test_factor_one_is_a_no_op(self):
        a = bytearray([10, 20, 30])
        assert parallax.scale_depth(a, 1.0) is a

    def test_the_authored_band_comes_down(self):
        # the author's TES4N2HGenerator clamps to 26..179 and the shipped pack
        # measures 30..179; halving must land it well inside that
        a = bytearray([26, 179])
        out = parallax.scale_depth(a, 0.5)
        assert max(out) - min(out) < (179 - 26)


class TestDiffuseAlphaStrip:

    #: c0 > c1 already, so DXT1 reads the block exactly as DXT5 did
    FOUR_COLOR = struct.pack('<HHI', 0xF800, 0x001F, 0x1B1B1B1B)

    def test_four_color_blocks_survive_texel_for_texel(self):
        dds = _dxt5_with_color_blocks(4, 4, [self.FOUR_COLOR])
        out = parallax.strip_alpha_to_bc1(dds)
        assert out[84:88] == b'DXT1'
        assert (_decode_color_block(out[128:136], False)
                == _decode_color_block(self.FOUR_COLOR, True))

    def test_a_swapped_block_decodes_to_the_same_texels(self):
        # c0 < c1 would mean 3-color + TRANSPARENT in DXT1; the repair swaps
        # the endpoints and flips every index, and must be exact
        blk = struct.pack('<HHI', 0x001F, 0xF800, 0x0000E41B)
        dds = _dxt5_with_color_blocks(4, 4, [blk])
        out = parallax.strip_alpha_to_bc1(dds)
        c0, c1, _ = struct.unpack('<HHI', out[128:136])
        assert c0 > c1, 'left in DXT1 3-color mode'
        assert (_decode_color_block(out[128:136], False)
                == _decode_color_block(blk, True))

    def test_a_solid_block_never_becomes_transparent(self):
        # c0 == c1: every palette entry is that color, but DXT1 index 3 would
        # be transparent black, so the indices must be zeroed
        blk = struct.pack('<HHI', 0x07E0, 0x07E0, 0xFFFFFFFF)
        dds = _dxt5_with_color_blocks(4, 4, [blk])
        out = parallax.strip_alpha_to_bc1(dds)
        assert struct.unpack_from('<I', out, 132)[0] == 0
        assert (_decode_color_block(out[128:136], False)
                == _decode_color_block(blk, True))

    def test_the_payload_halves(self):
        dds = _dxt5_with_color_blocks(16, 16, [self.FOUR_COLOR])
        out = parallax.strip_alpha_to_bc1(dds)
        assert len(out) - 128 == (len(dds) - 128) // 2

    def test_the_whole_mip_chain_comes_across(self):
        # 8x8 -> 4x4 -> 2x2 -> 1x1 is one block each below the top's four
        dds = bytearray(_dds_header(8, 8, b'DXT5', mips=4))
        for _ in range(4 + 1 + 1 + 1):
            dds += b'\x00' * 8 + self.FOUR_COLOR
        out = parallax.strip_alpha_to_bc1(bytes(dds))
        assert struct.unpack_from('<I', out, 28)[0] == 4
        assert len(out) == 128 + (4 + 1 + 1 + 1) * 8

    def test_a_dxt1_input_is_left_alone(self):
        dds = _dds_header(4, 4, b'DXT1') + b'\x00' * 8
        assert parallax.strip_alpha_to_bc1(dds) is None

    def test_only_diffuses_with_a_height_map_are_stripped(self, tmp_path):
        dxt5 = _dxt5_with_color_blocks(4, 4, [self.FOUR_COLOR])
        (tmp_path / 'carried.dds').write_bytes(dxt5)
        (tmp_path / 'carried_p.dds').write_bytes(b'placeholder')
        (tmp_path / 'plain.dds').write_bytes(dxt5)

        converted, skipped, kept, saved = parallax.strip_diffuse_alpha(
            str(tmp_path))

        assert converted == 1 and saved > 0
        assert (tmp_path / 'carried.dds').read_bytes()[84:88] == b'DXT1'
        assert (tmp_path / 'plain.dds').read_bytes() == dxt5, \
            'stripped a diffuse that never carried height'
        assert (tmp_path / 'carried_p.dds').read_bytes() == b'placeholder'

    def test_running_it_twice_changes_nothing(self, tmp_path):
        (tmp_path / 'x.dds').write_bytes(
            _dxt5_with_color_blocks(4, 4, [self.FOUR_COLOR]))
        (tmp_path / 'x_p.dds').write_bytes(b'p')
        parallax.strip_diffuse_alpha(str(tmp_path))
        once = (tmp_path / 'x.dds').read_bytes()
        converted, skipped, _, _ = parallax.strip_diffuse_alpha(
            str(tmp_path))
        assert converted == 0 and skipped == 1
        assert (tmp_path / 'x.dds').read_bytes() == once


# ---------------------------------------------------------------------------
# --textures-only: PGPatcher patches the meshes across the player's whole load
# ---------------------------------------------------------------------------

def _source_nif(nif, tmp_path, apply_mode):
    """A real on-disk Oblivion NIF with one shape, beside its texture tree."""
    src = _tree(tmp_path / 'src', HEIGHT_DDS)
    data = nif.Data(version=0x14000005, user_version=11, user_version_2=11)
    # pyffi defaults this to 0 = ENDIAN_BIG, and then nothing can read the file
    # back: every field after the header byte comes out byte-swapped.
    data.header.endian_type = 1
    root = nif.NiNode()
    root.name = b'Scene Root'
    shape = _shape(nif, apply_mode)
    root.num_children = 1
    root.children.update_size()
    root.children[0] = shape
    data.roots = [root]
    with open(src, 'wb') as f:
        data.write(f)
    return src


class TestTexturesOnly:

    def _run(self, nif, tmp_path, textures_only):
        from asset_convert.nif import nif_converter as nc
        from asset_convert.nif import shaders
        shaders._PARALLAX_ALPHA_CACHE.clear()
        src = _source_nif(nif, tmp_path, parallax.APPLY_HILIGHT2)
        dst = tmp_path / 'out' / 'meshes' / 'tes4' / 'a.nif'
        r = nc.convert_nif(str(src), str(dst), parallax=True,
                           textures_only=textures_only)
        height = (tmp_path / 'out' / 'textures' / 'tes4' / 'rocks'
                  / 'stone_p.dds')
        return r, dst, height

    def test_the_height_map_is_still_written(self, nif, tmp_path):
        r, _, height = self._run(nif, tmp_path, True)
        assert not r['error'], r['error']
        assert height.is_file(), 'the one thing this mode exists to produce'
        assert height.read_bytes()[:4] == b'DDS '

    def test_no_mesh_is_written(self, nif, tmp_path):
        _, dst, _ = self._run(nif, tmp_path, True)
        assert not dst.exists(), 'shipped a mesh in textures-only mode'

    def test_the_mesh_is_still_counted_as_converted(self, nif, tmp_path):
        # batch_convert's accounting must not silently read zero
        r, _, _ = self._run(nif, tmp_path, True)
        assert r['converted'] is True
        assert r['parallax']['parallax_shapes'] == 1

    def test_the_default_still_writes_both(self, nif, tmp_path):
        r, dst, height = self._run(nif, tmp_path, False)
        assert not r['error'], r['error']
        assert dst.is_file(), 'the normal path stopped shipping meshes'
        assert height.is_file()
