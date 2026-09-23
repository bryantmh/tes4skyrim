"""Oblivion hair -> Skyrim HDPT: the FaceGen .tri codec and the length bake.

The interesting invariant is that NPC_.LNAM survives as GEOMETRY.  Oblivion
blends the hair mesh toward its .tri's HairMorph by a per-NPC float; Skyrim has
no such field, so the blend is baked per variant and each variant gets its own
HDPT.  These tests pin the codec round-trip and the direction/monotonicity of
that bake.
"""

import os
import struct

import pytest

from asset_convert.character.facegen_tri import (MAGIC, SKYRIM_HAIR_MORPH, TriError,
                                       TriFile, build_skyrim_hair_tri)
from asset_convert.character.hair_pipeline import (LENGTH_BUCKETS, bucket_weight,
                                         collect_hair_usage, output_model_path,
                                         output_tri_path, quantize_length,
                                         variant_edid, variant_stem)

_HAIR_DIR = os.path.join('export', 'Oblivion.esm', 'meshes', 'characters', 'hair')
_HAS_SOURCE = os.path.isdir(_HAIR_DIR)
_needs_source = pytest.mark.skipif(not _HAS_SOURCE,
                                   reason='Oblivion hair export not present')


def _tri(name):
    return TriFile.from_file(os.path.join(_HAIR_DIR, name + '.tri'))


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------

def test_roundtrip_synthetic_preserves_geometry_exactly():
    verts = [(0.0, 1.0, 2.0), (-3.5, 4.25, 5.125), (7.0, -8.0, 9.5)]
    faces = [(0, 1, 2)]
    uvs = [(0.0, 0.5), (0.25, 0.75), (1.0, 1.0)]
    blob = build_skyrim_hair_tri(verts, faces, uvs)

    assert blob[:8] == MAGIC
    back = TriFile.from_bytes(blob)
    # f32 in, f32 out: vertices must be bit-identical, not merely close.
    assert back.vertices == verts
    assert back.faces == faces
    assert list(back.morphs) == [SKYRIM_HAIR_MORPH]
    # The emitted morph is a present-but-inert slot: length is already baked.
    assert all(d == (0.0, 0.0, 0.0) for d in back.morphs[SKYRIM_HAIR_MORPH])


def test_rejects_non_tri():
    with pytest.raises(TriError):
        TriFile.from_bytes(b'NOTATRI\0' + b'\0' * 64)


def test_rejects_truncated_vertex_block():
    header = MAGIC + struct.pack('<10I', 100, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    header += b'\0' * 16
    with pytest.raises(TriError):
        TriFile.from_bytes(header)          # promises 100 verts, supplies none


@_needs_source
def test_roundtrip_every_vanilla_hair_tri():
    """Parse + re-serialize every shipped hair .tri without drift."""
    names = [f[:-4] for f in os.listdir(_HAIR_DIR) if f.lower().endswith('.tri')]
    assert names, 'no hair .tri files found'
    for name in names:
        src = _tri(name)
        back = TriFile.from_bytes(src.to_bytes())
        assert back.vertices == src.vertices, name
        assert back.faces == src.faces, name
        assert set(back.morphs) == set(src.morphs), name
        for morph, deltas in src.morphs.items():
            peak = max((abs(c) for d in deltas for c in d), default=0.0)
            tol = max(peak / 32767.0 * 1.5, 1e-6)
            for a, b in zip(deltas, back.morphs[morph]):
                for x, y in zip(a, b):
                    assert abs(x - y) <= tol, (name, morph)


# ---------------------------------------------------------------------------
# The morph IS hair length
# ---------------------------------------------------------------------------

@_needs_source
def test_hair_morph_only_ever_lengthens():
    """HairMorph extends hair DOWNWARD, never upward.

    This is the measurement the whole bake rests on: across the shipped hair
    set the morph lowers the mesh's z-minimum (or leaves it alone for the
    short/spiky styles that have no length to add) and never raises it.
    """
    names = [f[:-4] for f in os.listdir(_HAIR_DIR) if f.lower().endswith('.tri')]
    grew_down = 0
    for name in names:
        tri = _tri(name)
        deltas = tri.hair_morph()
        if deltas is None:
            continue
        base_zmin = min(v[2] for v in tri.vertices)
        morphed_zmin = min(v[2] for v in tri.morphed_vertices(deltas, 1.0))
        # Never shorter than the base mesh.
        assert morphed_zmin <= base_zmin + 1e-3, name
        if morphed_zmin < base_zmin - 0.01:
            grew_down += 1
    assert grew_down > len(names) // 2, 'expected most hairs to lengthen'


@_needs_source
def test_bake_is_monotonic_in_length():
    tri = _tri('bretonmaletonsure')
    deltas = tri.hair_morph()
    assert deltas is not None
    zmins = [min(v[2] for v in tri.morphed_vertices(deltas, bucket_weight(b)))
             for b in range(LENGTH_BUCKETS + 1)]
    for lo, hi in zip(zmins, zmins[1:]):
        assert hi <= lo + 1e-4, zmins
    assert zmins[-1] < zmins[0]          # bucket 0 really is the shortest


@_needs_source
def test_weight_zero_is_the_untouched_base_mesh():
    tri = _tri('style03')
    assert tri.morphed_vertices(tri.hair_morph(), 0.0) == tri.vertices


# ---------------------------------------------------------------------------
# Bucketing, naming, and the FormID key
# ---------------------------------------------------------------------------

def test_quantize_clamps_and_rounds():
    assert quantize_length(0.0) == 0
    assert quantize_length(1.0) == LENGTH_BUCKETS
    assert quantize_length(-5.0) == 0            # below the morph's domain
    assert quantize_length(99.0) == LENGTH_BUCKETS
    assert quantize_length(float('nan')) == 0
    assert quantize_length('nonsense') == 0
    assert 0 < quantize_length(0.5) < LENGTH_BUCKETS


def test_bucket_zero_keeps_the_plain_filename():
    """Bucket 0 IS the unmorphed mesh, so it must not gain a suffix."""
    assert variant_stem('style03', 0) == 'style03'
    assert variant_stem('style03', 4) != 'style03'
    assert variant_stem('style03', 4) != variant_stem('style03', 5)


def test_variant_paths_agree_between_record_and_asset_sides():
    model = 'Characters\\Hair\\Style03.NIF'
    for bucket in (0, 3, LENGTH_BUCKETS):
        nif = output_model_path(model, bucket)
        tri = output_tri_path(model, bucket)
        assert nif.lower().endswith('.nif')
        assert tri == os.path.splitext(nif)[0] + '.tri'
        assert 'characters' in nif.lower() and 'hair' in nif.lower()
    assert output_model_path(model, 0) != output_model_path(model, 4)


def test_variant_edids_are_distinct_per_bucket():
    seen = {variant_edid('MediumLength', b) for b in range(LENGTH_BUCKETS + 1)}
    assert len(seen) == LENGTH_BUCKETS + 1


def test_collect_usage_buckets_by_authored_length():
    npcs = [
        {'HNAM.Hair': '00027FF2', 'LNAM.HairLength': '0.0'},
        {'HNAM.Hair': '00027FF2', 'LNAM.HairLength': '1.0'},
        {'HNAM.Hair': '00027FF2', 'LNAM.HairLength': '1.0'},
        {'HNAM.Hair': '00064213', 'LNAM.HairLength': '0.5'},
        {'LNAM.HairLength': '0.5'},                      # no hair -> ignored
    ]
    usage = collect_hair_usage(npcs)
    assert usage[0x00027FF2] == {0, LENGTH_BUCKETS}
    assert usage[0x00064213] == {quantize_length(0.5)}
    assert len(usage) == 2


def test_lookup_ignores_the_load_order_index_byte():
    """A FormID's index byte is assigned per run, so it is not identity.

    The export dumps HAIR 000C4821, but the record reaching convert_HAIR
    carries 010C4821.  Keying the bucket index on the raw value made every
    lookup miss SILENTLY — no error, every NPC just fell back to the
    bucket-0 mesh and the whole length feature evaporated.
    """
    from tes5_import.actors import hair_variants as hair_variants

    hair_variants._BUCKETS.clear()
    hair_variants._HAS_TRI.clear()
    hair_variants._BUCKETS[0x000C4821] = (0, 3, LENGTH_BUCKETS)
    hair_variants._HAS_TRI[0x000C4821] = True
    try:
        raw = hair_variants.hair_buckets_for(0x000C4821)
        for index_byte in (0x01, 0x05, 0xFF):
            indexed = (index_byte << 24) | 0x000C4821
            assert hair_variants.hair_buckets_for(indexed) == raw
            assert hair_variants.hair_has_tri(indexed)
    finally:
        hair_variants._BUCKETS.clear()
        hair_variants._HAS_TRI.clear()


# ---------------------------------------------------------------------------
# Hair color: the Hair Tint shader and the generated CLFM palette
# ---------------------------------------------------------------------------

@_needs_source
def test_converted_hair_uses_the_hair_tint_shader():
    """Type 6 is what makes the tint apply at all.

    nif.xml gates the Hair Tint Color field on `Shader Type == 6`, so hair left
    at type 0 (Default) renders its raw grey source texture no matter what the
    wearer's HCLF says — which is exactly how it shipped before this.
    """
    from asset_convert.character import hair_pipeline
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat

    built = os.path.join('output', 'Oblivion.esm', 'meshes', 'tes4',
                         'characters', 'hair')
    if not os.path.isdir(built):
        pytest.skip('converted hair not built')
    nifs = [f for f in os.listdir(built) if f.lower().endswith('.nif')]
    if not nifs:
        pytest.skip('no converted hair meshes')

    checked = 0
    for name in sorted(nifs)[:12]:
        data = NifFormat.Data()
        with open(os.path.join(built, name), 'rb') as fh:
            data.read(fh)
        for block in data.blocks:
            if not isinstance(block, NifFormat.BSLightingShaderProperty):
                continue
            assert block.skyrim_shader_type == \
                hair_pipeline.SHADER_TYPE_HAIR_TINT, name
            assert block.shader_flags_1.slsf_1_hair_soft_lighting, name
            checked += 1
    assert checked, 'no hair shaders inspected'


def test_apply_hair_shader_sets_the_vanilla_shape():
    from asset_convert.character.hair_pipeline import (SHADER_TYPE_HAIR_TINT,
                                             apply_hair_shader)
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat

    shader = NifFormat.BSLightingShaderProperty()

    class _Data:
        blocks = [shader]

    assert apply_hair_shader(_Data()) == 1
    assert shader.skyrim_shader_type == SHADER_TYPE_HAIR_TINT
    assert shader.shader_flags_1.slsf_1_hair_soft_lighting
    assert shader.shader_flags_1.slsf_1_own_emit
    assert shader.shader_flags_2.slsf_2_assume_shadowmask
    # Vanilla hair is glossy; a zero here reads as flat matte under the tint.
    assert shader.glossiness == pytest.approx(10.0)
    assert shader.specular_strength == pytest.approx(0.9, abs=1e-3)


class _FakeWriter:
    """Just enough writer for the CLFM generator."""

    def __init__(self):
        self.records = []

    def derive_formid(self, site, key):
        import hashlib
        digest = hashlib.md5(f'{site}\x00{key!r}'.encode()).digest()
        return 0x01000000 | (struct.unpack('<I', digest[:4])[0] & 0xFFFFFF)

    def add_record(self, sig, blob):
        self.records.append((sig, blob))


def _subrecords(blob):
    out, off = {}, 24
    while off + 6 <= len(blob):
        sig = blob[off:off + 4].decode('ascii', 'replace')
        ln = struct.unpack_from('<H', blob, off + 4)[0]
        out[sig] = blob[off + 6:off + 6 + ln]
        off += 6 + ln
    return out


def test_generated_clfm_carries_the_authored_rgb_exactly():
    """The whole point: Oblivion's authored color survives verbatim.

    Snapping to one of vanilla's 15 dark swatches measured a mean RGB error of
    26.9 (max 274.5) across Oblivion.esm's 2,482 haired NPCs.
    """
    from tes5_import.record_types.npc import hair_color_formid

    writer = _FakeWriter()
    hair_color_formid(writer, 75, 50, 25)
    assert len(writer.records) == 1
    sig, blob = writer.records[0]
    assert sig == 'CLFM'
    subs = _subrecords(blob)
    assert tuple(subs['CNAM']) == (75, 50, 25, 0)
    assert struct.unpack('<I', subs['FNAM'])[0] == 1


def test_clfm_is_generated_once_per_distinct_color():
    from tes5_import.record_types.npc import hair_color_formid

    writer = _FakeWriter()
    first = hair_color_formid(writer, 75, 50, 25)
    again = hair_color_formid(writer, 75, 50, 25)
    other = hair_color_formid(writer, 33, 31, 31)

    assert first == again           # same color -> same id
    assert first != other
    assert len(writer.records) == 2  # and only one record per color


def test_clfm_ids_are_a_pure_function_of_the_color():
    """Authored RGB is the key, so ids are stable across runs and machines."""
    from tes5_import.record_types.npc import hair_color_formid

    a, b = _FakeWriter(), _FakeWriter()
    assert (hair_color_formid(a, 12, 34, 56)
            == hair_color_formid(b, 12, 34, 56))


def test_clfm_clamps_out_of_range_channels():
    from tes5_import.record_types.npc import hair_color_formid

    writer = _FakeWriter()
    hair_color_formid(writer, -20, 300, 128)
    subs = _subrecords(writer.records[0][1])
    assert tuple(subs['CNAM']) == (0, 255, 128, 0)


def test_usage_is_keyed_on_authored_values_only():
    """The FormID key must be (source hair FormID, authored-length bucket).

    Both halves are authored TES4 data, which is what keeps generated HDPT
    ids stable across machines and builds.
    """
    a = collect_hair_usage([{'HNAM.Hair': '00027FF2',
                             'LNAM.HairLength': '0.58'}])
    b = collect_hair_usage([{'HNAM.Hair': '00027FF2',
                             'LNAM.HairLength': '0.58'}])
    assert a == b


# ---------------------------------------------------------------------------
# In-game defects found on the first playable build
# ---------------------------------------------------------------------------

_BUILT = os.path.join('output', 'Oblivion.esm', 'meshes', 'tes4',
                      'characters', 'hair')
_BS = chr(92)


def _mesh_bounds(path):
    """(min, max) per axis over every vertex in a NIF."""
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    data = NifFormat.Data()
    with open(path, 'rb') as fh:
        data.read(fh)
    lo = [1e9] * 3
    hi = [-1e9] * 3
    for block in data.blocks:
        if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            continue
        if block.data is None:
            continue
        for v in block.data.vertices:
            for i, c in enumerate((v.x, v.y, v.z)):
                lo[i] = min(lo[i], c)
                hi[i] = max(hi[i], c)
    return lo, hi


def test_head_fit_preserves_authored_clearance():
    """THE core invariant: gear keeps its authored distance from the skin.

    head_fit maps head gear from Oblivion face space into Skyrim head space
    so that each vertex's signed clearance against the Skyrim head equals its
    authored clearance against the Oblivion head+ears.  That single rule is
    both fixes at once: hair authored flush on the scalp stays FLUSH on the
    Skyrim scalp, and a helmet authored N units off the skull stays exactly
    N units off -- so the skull can never poke through anything that covered
    it in Oblivion (the old PRN-helmet bug).

    Bounds are ~2x the values measured when the mechanism was built
    (2026-08-23: flush mean 0.10-0.16, p95 0.19-0.47 across tonsure/style03/
    iron helmet, both genders) so real regressions fail loudly without the
    test being brittle to a field rebuild.
    """
    pytest.importorskip('scipy')
    import numpy as np
    from scipy.spatial import cKDTree
    from asset_convert.character import head_fit

    if not head_fit.fit_available(False):
        pytest.skip('head-fit data not built')

    sources = [
        ('hair', os.path.join(_HAIR_DIR, 'bretonmaletonsure.nif')),
        ('helmet', os.path.join('export', 'Oblivion.esm', 'meshes', 'armor',
                                'iron', 'm', 'helmet.nif')),
    ]
    sources = [(k, p) for k, p in sources if os.path.isfile(p)]
    if not sources:
        pytest.skip('source meshes not exported')

    def exact_signed(P, V, T, tree, tn):
        _, tri = tree.query(P, k=1)
        a = V[T[tri, 0]][:, None, :]
        b = V[T[tri, 1]][:, None, :]
        c = V[T[tri, 2]][:, None, :]
        cp = head_fit._closest_point_on_triangles(P[:, None, :], a, b, c)[:, 0, :]
        off = P - cp
        d = np.linalg.norm(off, axis=1)
        sgn = np.where(np.einsum('pi,pi->p', off, tn[tri]) >= 0, 1.0, -1.0)
        return sgn * d, cp

    for female in (False, True):
        fit = head_fit._get(female)
        assert fit is not None
        pack = fit.human
        src_tree = cKDTree(pack.v[pack.t].mean(axis=1))
        src_n = head_fit._tri_normals(pack.v, pack.t)
        sk_tree = cKDTree(fit.sk_v[fit.sk_t].mean(axis=1))
        sk_n = head_fit._tri_normals(fit.sk_v, fit.sk_t)
        for kind, path in sources:
            shapes = _load_nif_shapes(path)
            fitted = head_fit.fit_head_gear(shapes, female)
            assert fitted is not None, (kind, female)
            v, _t = shapes[0]
            out = fitted[0]
            c0, _ = exact_signed(v, pack.v, pack.t, src_tree, src_n)
            c1, _cp1 = exact_signed(out, fit.sk_v, fit.sk_t, sk_tree, sk_n)
            # scalp region (head-local), above the neck/jaw rims where the
            # clearance contract is fully enforced
            scalp = v[:, 2] > -3.0
            err = np.abs(c1 - c0)
            flush = scalp & (np.abs(c0) < 0.5)
            band = scalp & (c0 >= 0.5) & (c0 < 4.0)
            # v3 field sampling preserves signed clearance by construction;
            # measured flush err mean 0.04-0.08 / p95 0.09-0.23 across the
            # sample set — the gates are ~3x that, failing loudly on a real
            # regression without being brittle to a field rebuild.
            # p95 gates leave room for a couple of ear-transition outliers
            # (typical post-refinement flush err is 0.003-0.04)
            for name, m, lim_mean, lim_p95 in (
                    ('flush', flush, 0.25, 1.0),
                    ('standoff', band, 0.5, 1.5)):
                if not m.any():
                    continue
                assert err[m].mean() < lim_mean, (kind, female, name,
                                                  err[m].mean())
                assert np.percentile(err[m], 95) < lim_p95, (kind, female,
                                                             name)


def _load_nif_shapes(path):
    """[(verts, tris)] for every trishape in a NIF (authored coords)."""
    import numpy as np
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    data = NifFormat.Data()
    with open(path, 'rb') as fh:
        data.read(fh)
    out = []
    for block in data.roots[0].tree():
        if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            continue
        if block.data is None or block.data.num_vertices == 0:
            continue
        v = np.array([[q.x, q.y, q.z] for q in block.data.vertices])
        try:
            t = np.array([tuple(x) for x in block.data.get_triangles()], int)
        except Exception:
            t = np.zeros((0, 3), int)
        out.append((v, t))
    return out


def test_generic_hair_is_baked_per_race_group():
    """Generic hair conforms to each race GROUP's actual in-game head.

    Every in-game head is the base mesh PLUS the wearer race's races-tri
    morph; the elf occiput sits 1.67 further FORWARD than base, so a
    base-only fit floated 2.2+ units behind a High Elf's head (in-game
    2026-08-24).  A races.tri on the hair HDPT was tried first and the
    ENGINE DOES NOT APPLY IT to type-3 parts — per-group meshes (vanilla
    hair's own architecture) are baked instead.  This gate fits a hair with
    the elf group and checks the occiput clearance against the elf-morphed
    head matches what the base fit achieves on the base head.
    """
    pytest.importorskip('scipy')
    import numpy as np
    from scipy.spatial import cKDTree
    from asset_convert.character import head_fit
    from asset_convert.sources.skyrim_assets import get_asset_bytes

    if not head_fit.fit_available(False):
        pytest.skip('head-fit data not built')
    fit = head_fit._get(False)
    if 'elves' not in fit.groups:
        pytest.skip('group fields not built')

    path = os.path.join(_HAIR_DIR, 'style07.nif')
    if not os.path.isfile(path):
        pytest.skip('source hair not exported')
    sep = chr(92)
    raw = get_asset_bytes(sep.join(
        ['meshes', 'actors', 'character', 'character assets',
         'maleheadraces.tri']))
    if raw is None:
        pytest.skip('vanilla races tri unavailable')
    he = np.asarray(TriFile.from_bytes(raw).morphs['HighElfRace'], float)

    shapes = _load_nif_shapes(path)
    V0 = np.vstack([v for v, _t in shapes])
    base = np.vstack(head_fit.fit_head_gear(shapes, False, cover_ears=True))
    elf = np.vstack(head_fit.fit_head_gear(shapes, False, group='elves',
                                           cover_ears=True))

    # the elf head = the raw head verts + the HighElfRace morph
    skv = fit.sk_v.copy()
    skv[:len(he)] += he       # head rows come first on the extended surface

    def signed(P, V_, T_):
        tn = head_fit._tri_normals(V_, T_)
        tree = cKDTree(V_[T_].mean(axis=1))
        _, tri = tree.query(P, k=1)
        a = V_[T_[tri, 0]][:, None, :]
        b = V_[T_[tri, 1]][:, None, :]
        c = V_[T_[tri, 2]][:, None, :]
        cp = head_fit._closest_point_on_triangles(
            P[:, None, :], a, b, c)[:, 0, :]
        off = P - cp
        d = np.linalg.norm(off, axis=1)
        return np.where(np.einsum('pi,pi->p', off, tn[tri]) >= 0, 1, -1) * d

    occ = (np.abs(V0[:, 0]) < 2.5) & (V0[:, 1] < -2) & (V0[:, 2] > 0) \
        & (V0[:, 2] < 9)
    assert occ.any()
    c_base_on_elf = signed(base[occ], skv, fit.sk_t).mean()
    c_elf_on_elf = signed(elf[occ], skv, fit.sk_t).mean()
    c_base_on_base = signed(base[occ], fit.sk_v, fit.sk_t).mean()
    # measured 2026-08-24: base-on-elf +2.24 (the in-game float), elf-on-elf
    # +0.51 == base-on-base +0.51 (the hair's own authored standoff there)
    assert c_base_on_elf > c_elf_on_elf + 0.8, (c_base_on_elf, c_elf_on_elf)
    assert abs(c_elf_on_elf - c_base_on_base) < 0.5, (c_elf_on_elf,
                                                      c_base_on_base)


def test_built_hair_is_fitted_not_a_passthrough():
    """The written meshes really carry the head fit, per gender.

    The male and female Skyrim skulls differ (max 1.23 units over the scalp),
    so the two gendered outputs of a unisex hair must differ from the source
    AND from each other.
    """
    if not (os.path.isdir(_BUILT) and os.path.isdir(_HAIR_DIR)):
        pytest.skip('converted hair not built')

    checked = 0
    for stem in ('bretonmaletonsure', 'style03'):     # unisex styles
        src = os.path.join(_HAIR_DIR, stem + '.nif')
        out_m = os.path.join(_BUILT, stem + '.nif')
        out_f = os.path.join(_BUILT, stem + '__f.nif')
        if not all(os.path.isfile(p) for p in (src, out_m, out_f)):
            continue
        src_lo, src_hi = _mesh_bounds(src)
        m_lo, m_hi = _mesh_bounds(out_m)
        f_lo, f_hi = _mesh_bounds(out_f)
        # fitted: moved from the source, but still recognisably the same
        # hairstyle (no uniform blow-up -- the old guessed-scale failure)
        for lo, hi in ((m_lo, m_hi), (f_lo, f_hi)):
            moved = max(abs(o - s) for o, s in zip(lo + hi, src_lo + src_hi))
            assert moved > 0.5, stem
            for axis in range(3):
                r = (hi[axis] - lo[axis]) / (src_hi[axis] - src_lo[axis])
                assert 0.8 < r < 1.35, (stem, axis, r)
        # gendered: the two fits differ
        gender_delta = max(abs(a - b) for a, b in zip(m_lo + m_hi,
                                                      f_lo + f_hi))
        assert gender_delta > 0.05, stem
        checked += 1
    assert checked, 'no hair triples compared'


def test_gendered_naming_and_paths():
    """Male bucket-0 keeps the bare stem; female meshes carry __f; the
    record and asset sides agree on every (bucket, gender) path."""
    assert variant_stem('style03', 0) == 'style03'
    assert variant_stem('style03', 0, female=True) == 'style03__f'
    assert variant_stem('style03', 4, female=True) == 'style03__f__l04'

    model = 'Characters\\Hair\\Style03.NIF'
    seen = set()
    for female in (False, True):
        for bucket in (0, 3, LENGTH_BUCKETS):
            nif = output_model_path(model, bucket, female)
            tri = output_tri_path(model, bucket, female)
            assert tri == os.path.splitext(nif)[0] + '.tri'
            seen.add(nif.lower())
    assert len(seen) == 6                     # all six variants distinct

    edids = {variant_edid('MediumLength', b, f)
             for b in range(LENGTH_BUCKETS + 1) for f in (False, True)}
    assert len(edids) == 2 * (LENGTH_BUCKETS + 1)


def test_hair_genders_honour_the_authored_restriction():
    from asset_convert.character.hair_pipeline import hair_genders
    assert hair_genders(0x00) == (False, True)      # unisex
    assert hair_genders(0x01) == (False, True)      # playable-only bit
    assert hair_genders(0x02) == (True,)            # NotMale -> female only
    assert hair_genders(0x04) == (False,)           # NotFemale -> male only
    assert hair_genders(0x06) == (False, True)      # authored nonsense: both


def test_hair_variant_formid_contract():
    """Base (bucket 0, base gender) keeps the SOURCE FormID; every other
    variant derives from (masked id, bucket[, 'F']) so male ids predating
    the gender split never move, and female ids can never collide."""
    from tes5_import.record_types.npc import hair_variant_formid

    w = _FakeWriter()
    src = 0x010C4821

    # unisex hair: base gender male
    assert hair_variant_formid(w, src, 0, False, base_female=False) == src
    m3 = hair_variant_formid(w, src, 3, False, base_female=False)
    f0 = hair_variant_formid(w, src, 0, True, base_female=False)
    f3 = hair_variant_formid(w, src, 3, True, base_female=False)
    assert len({src, m3, f0, f3}) == 4
    # male keys are exactly the pre-split key shape
    assert m3 == w.derive_formid('HDPT_HAIR', (0x000C4821, 3))
    assert f3 == w.derive_formid('HDPT_HAIR', (0x000C4821, 3, 'F'))

    # female-only hair: the female bucket-0 IS the source record
    assert hair_variant_formid(w, src, 0, True, base_female=True) == src


def test_race_hair_is_fitted_to_its_own_heads():
    """Beast/orc hair measures clearance against ITS race's head pair.

    Oblivion authors headkhajiit/headargonian/headorc, and Skyrim ships its
    own khajiit/argonian head meshes (every other race shares malehead/
    femalehead — censused over all 766 vanilla HDPTs).  Fitting khajiit hair
    through the human pair left it mean 1.1 / p95 1.9 off the khajiit scalp
    (argonian 1.4 / 3.6); through its own pair it measures like human hair.
    """
    pytest.importorskip('scipy')
    import numpy as np
    from asset_convert.character import head_fit

    if not head_fit.fit_available(False):
        pytest.skip('head-fit data not built')
    fit = head_fit._get(False)
    if not fit.races:
        pytest.skip('race packs not built')
    assert head_fit.fit_race_for_hair('KhajiitMane') == 'khajiit'
    assert head_fit.fit_race_for_hair('ArgonianSpikes') == 'argonian'
    # orc deliberately has NO pack: Skyrim orcs share the human head and the
    # OB orc scalp is within 0.36 mean of the human one, while an orc pack
    # measured the worst distortion of any race — see head_fit._RACE_PACKS.
    assert head_fit.fit_race_for_hair('OrcMaleStubs') is None
    assert head_fit.fit_race_for_hair('MediumLength') is None

    from scipy.spatial import cKDTree

    def exact_signed(P, V, T):
        tn = head_fit._tri_normals(V, T)
        tree = cKDTree(V[T].mean(axis=1))
        _, tri = tree.query(P, k=1)
        a = V[T[tri, 0]][:, None, :]
        b = V[T[tri, 1]][:, None, :]
        c = V[T[tri, 2]][:, None, :]
        cp = head_fit._closest_point_on_triangles(P[:, None, :], a, b, c)[:, 0, :]
        off = P - cp
        d = np.linalg.norm(off, axis=1)
        return np.where(np.einsum('pi,pi->p', off, tn[tri]) >= 0, 1, -1) * d

    checked = 0
    for stem, race in (('khajiitmane', 'khajiit'),
                       ('argonianspikes', 'argonian')):
        path = os.path.join(_HAIR_DIR, stem + '.nif')
        if not os.path.isfile(path) or race not in fit.races:
            continue
        shapes = _load_nif_shapes(path)
        out = head_fit.fit_head_gear(shapes, False, race=race)
        assert out is not None
        v, _t = shapes[0]
        src = fit.races[race]
        sk_v, sk_t = fit.races_sk[race]
        c0 = exact_signed(v, src.v, src.t)
        c1 = exact_signed(out[0], sk_v, sk_t)
        flush = (v[:, 2] > -3.0) & (np.abs(c0) < 0.5)
        if not flush.any():
            continue
        err = np.abs(c1 - c0)
        assert err[flush].mean() < 0.4, (stem, err[flush].mean())
        assert np.percentile(err[flush], 95) < 1.0, stem
        checked += 1
    assert checked, 'no race hair checked'


def test_head_fit_keeps_the_mesh_intact_and_unsized():
    """THE in-game regression gate: intact geometry, no oversizing.

    The first shipped fit oversized every mesh (an affine carrier measured
    in world frames claimed the SK head was 18% wider / 24% taller) and let
    inner and outer surfaces move independently (mangled triangles).  v3's
    contract: per-edge length change stays small in ABSOLUTE units (field
    sampling measured p99 0.23-0.84 / max <=1.1 across the sample set), and
    extents never grow beyond the real skull differences — x by at most the
    jaw/cheek widening (SK jaw is 1-1.6 wider per side, measured on the raw
    heads), y by at most the occiput/nape-back delta, z only DOWN.
    """
    pytest.importorskip('scipy')
    import numpy as np
    from asset_convert.character import head_fit

    if not head_fit.fit_available(False):
        pytest.skip('head-fit data not built')

    checked = 0
    for stem, female in (('bretonmaletonsure', False), ('style03', False),
                         ('nordfemalebunches', False),
                         ('woodelffemalepony', True), ('khajiitmane', False)):
        path = os.path.join(_HAIR_DIR, stem + '.nif')
        if not os.path.isfile(path):
            continue
        shapes = _load_nif_shapes(path)
        race = head_fit.fit_race_for_hair(stem)
        fitted = head_fit.fit_head_gear(shapes, female, race=race)
        assert fitted is not None, stem
        for (v, t), out in zip(shapes, fitted):
            if not len(t):
                continue
            e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
            l0 = np.linalg.norm(v[e[:, 0]] - v[e[:, 1]], axis=1)
            l1 = np.linalg.norm(out[e[:, 0]] - out[e[:, 1]], axis=1)
            dd = np.abs(l1 - l0)
            p99_lim = 1.4 if race else 0.9   # race skulls genuinely differ
            assert np.percentile(dd, 99) < p99_lim, (stem,
                                                     np.percentile(dd, 99))
            assert dd.max() < 1.8, (stem, dd.max())
            grow = [(out[:, ax].max() - out[:, ax].min())
                    - (v[:, ax].max() - v[:, ax].min()) for ax in range(3)]
            # human SCALPS have equal widths but the SK jaw/cheek is 1-1.6
            # wider per side, so jaw-reaching styles may widen up to ~2;
            # the SK khajiit skull is wider overall
            x_lim = 2.8 if race == 'khajiit' else 2.0
            assert grow[0] < x_lim, (stem, 'x', grow)
            assert grow[1] < 3.5, (stem, 'y', grow)   # occiput/nape back 2.2
            # crown moves DOWN on the shared human skull.  The KHAJIIT skull
            # is taller and its ears sit on TOP of it, so its hair genuinely
            # grows in z: the 1.2 bound was measured while the ear box was
            # eating the crown (|x| > 2.5 captured 120 verts spanning z
            # 9.09-14.85 and flattened 54 real skull verts by up to 24.9),
            # which is the defect that shipped khajiit hair floating off the
            # head.  With the box narrowed to the real ears (|x| > 4.0) the
            # crown survives and khajiitmane measures 1.21.
            z_lim = 1.6 if race == 'khajiit' else 1.2
            assert grow[2] < z_lim, (stem, 'z', grow)
        checked += 1
    assert checked >= 3, 'too few meshes checked'


def test_converted_helmet_is_fitted_not_scaled():
    """The full convert_nif path must reach the head fit for Prn helmets.

    _fit_prn_head_blocks once looked the Prn blocks up in data.blocks, which
    is STALE after the strips->shape conversion — the lookup silently matched
    nothing and every helmet fell back to the legacy ARMOR_PIECE_OFFSETS_PRN
    scale table (sy 1.165: 'extremely oversized and stretched wide' in game).
    The fitted result changes each extent by well under a unit; the legacy
    scale grows y by +3.2.
    """
    pytest.importorskip('scipy')
    import numpy as np
    import tempfile
    import shutil
    from asset_convert.character import head_fit
    from asset_convert.nif.nif_converter import convert_nif
    from asset_convert.character import wearable_plan as wp

    src = os.path.join('export', 'Oblivion.esm', 'meshes', 'armor', 'iron',
                       'm', 'helmet.nif')
    if not os.path.isfile(src) or not head_fit.fit_available(False):
        pytest.skip('source or fit data missing')
    plan = wp.build_plan(os.path.join('export', 'Oblivion.esm'))

    tmp = tempfile.mkdtemp(prefix='tes4helmfit_')
    try:
        dst = os.path.join(tmp, 'helmet.nif')
        r = convert_nif(src, dst, src_meshes_dir=os.path.join(
            'export', 'Oblivion.esm', 'meshes'), wearable_plan=plan)
        assert not r.get('error'), r.get('error')
        out_path = dst if os.path.isfile(dst) else os.path.join(
            tmp, 'helmet_0.nif')
        sv = _load_nif_shapes(src)[0][0]
        ov = _load_nif_shapes(out_path)[0][0]
        assert len(sv) == len(ov)
        # v3-measured shipped extents: x +2.25 (real SK jaw/cheek widening,
        # 1-1.6 per side), y +2.73 (occiput/nape back), z -1.39 (crown down).
        # The legacy scale table instead grew y by +3.2 with x +1.7.
        for ax, lim in ((0, 2.7), (1, 3.2), (2, 0.5)):
            g = ((ov[:, ax].max() - ov[:, ax].min())
                 - (sv[:, ax].max() - sv[:, ax].min()))
            assert g < lim, (ax, g)
        # and it really was fitted, not passed through
        moved = np.linalg.norm(ov - sv, axis=1)
        assert moved.mean() > 0.3
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hanging_hair_keeps_its_authored_length():
    """Ponytails and morphed-long hair must follow the head, not its SCALE.

    The fit's affine carrier holds the skull's real growth (sx 1.18,
    sz 1.24); applied to a hanging tail that would be pure elongation
    (+18-20% z-extent, measured before the harmonic far-field landed).  Far
    geometry instead diffuses its delta harmonically from the near-skin
    verts, so a tail translates with its attachment and keeps its length:
    the fully-morphed style01 tail measures 0.0% mean / 0.1% p95 edge
    stretch among far verts.
    """
    pytest.importorskip('scipy')
    import numpy as np
    from asset_convert.character import head_fit

    if not head_fit.fit_available(False):
        pytest.skip('head-fit data not built')
    nif = os.path.join(_HAIR_DIR, 'style01.nif')
    tri = os.path.join(_HAIR_DIR, 'style01.tri')
    if not (os.path.isfile(nif) and os.path.isfile(tri)):
        pytest.skip('style01 not exported')

    shapes = _load_nif_shapes(nif)
    deltas = np.array(TriFile.from_file(tri).hair_morph())
    v, t = shapes[0]
    assert len(v) == len(deltas)
    v = v + deltas                              # fully lengthened variant
    out = head_fit.fit_head_gear([(v, t)], False)[0]

    fit = head_fit._get(False)
    dd, _ = fit.human.tree.query(v)
    far = dd > 5
    assert far.sum() > 50                       # the tail really hangs
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    fe = far[e].all(axis=1)
    l0 = np.linalg.norm(v[e[fe, 0]] - v[e[fe, 1]], axis=1)
    l1 = np.linalg.norm(out[e[fe, 0]] - out[e[fe, 1]], axis=1)
    ok = l0 > 1e-6
    r = np.abs(l1[ok] / l0[ok] - 1)
    assert r.mean() < 0.02, r.mean()
    assert np.percentile(r, 95) < 0.05


def test_master_owned_hair_resolves_to_the_base_id():
    """A dependent plugin cannot mint a MASTER's variant ids.

    The master's baked variants live in the master's converted plugin under
    the master's derived FormIDs; deriving the same key here lands in this
    plugin's id space, where no such record exists — a dangling PNAM.  A
    master-owned hair therefore resolves to its base FormID, which the
    load-order remap rewrites like any other cross-plugin reference.
    """
    from tes5_import.actors import hair_variants as hair_variants
    from tes5_import.actors.npc_face_mapper import _resolve_hair_part

    w = _FakeWriter()
    fid = 0x010C4821
    hair_variants._BUCKETS[0x000C4821] = (0, 3)
    hair_variants._GENDERS[0x000C4821] = (False, True)
    hair_variants._OWN.clear()                      # NOT ours -> base id
    try:
        rec = {'LNAM.HairLength': '0.4'}
        assert _resolve_hair_part(rec, fid, 'Nord', 'Female', w) == fid
        # the same hair defined by THIS plugin derives normally
        hair_variants._OWN.add(0x000C4821)
        got = _resolve_hair_part(rec, fid, 'Nord', 'Female', w)
        assert got != fid
    finally:
        hair_variants._BUCKETS.clear()
        hair_variants._GENDERS.clear()
        hair_variants._OWN.clear()


def test_hair_alpha_tests_rather_than_blends():
    """Oblivion ships hair as alpha BLEND at threshold 0; Skyrim needs a TEST.

    With threshold 0 the test rejects nothing, so every semi-transparent strand
    pixel is depth-sorted per frame -- in game the whole hairstyle smears as the
    camera rotates.  All 211 vanilla Skyrim hair meshes alpha-test (threshold
    128 x92, 100 x57, and a scatter of others).
    """
    from asset_convert.character.hair_pipeline import (HAIR_ALPHA_FLAGS,
                                             HAIR_ALPHA_THRESHOLD)
    assert HAIR_ALPHA_THRESHOLD > 0
    # bit 0 = alpha blend, bit 9 = alpha test.  Vanilla hair: test on, blend off.
    assert not HAIR_ALPHA_FLAGS & 0x01
    assert HAIR_ALPHA_FLAGS & 0x200

    if not os.path.isdir(_BUILT):
        pytest.skip('converted hair not built')

    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat

    checked = 0
    for name in sorted(os.listdir(_BUILT))[:40]:
        if not name.lower().endswith('.nif'):
            continue
        data = NifFormat.Data()
        with open(os.path.join(_BUILT, name), 'rb') as fh:
            data.read(fh)
        for block in data.blocks:
            if isinstance(block, NifFormat.NiAlphaProperty):
                assert block.threshold == HAIR_ALPHA_THRESHOLD, name
                assert int(block.flags) == HAIR_ALPHA_FLAGS, name
                checked += 1
    assert checked, 'no alpha properties inspected'


def test_tint_compensates_for_a_biased_texture():
    """Skyrim MULTIPLIES the tint by the diffuse, so a warm texture skews it.

    mane.dds and argonian.dds are strongly orange-brown (channel ratios
    1.43:1.03:0.54 and 1.40:0.97:0.63), so the same authored hair color renders
    orange on a Khajiit mane and neutral on grey.dds unless the bias is divided
    back out.
    """
    from asset_convert.character import hair_pipeline

    neutral = hair_pipeline._VANILLA_HAIR_TINT
    original = hair_pipeline._texture_bias
    try:
        # A neutral texture must leave the tint alone.
        hair_pipeline._texture_bias = lambda _p: (1.0, 1.0, 1.0)
        assert hair_pipeline.hair_tint_for_texture('x.dds') == \
            pytest.approx(neutral)

        # A strongly orange texture must have its blue channel pushed UP to
        # compensate, and red pulled down.
        hair_pipeline._texture_bias = lambda _p: (1.432, 1.032, 0.536)
        warm = hair_pipeline.hair_tint_for_texture('mane.dds')
        assert warm[2] > neutral[2]
        assert warm[0] < neutral[0]
        assert all(0.0 <= c <= 1.0 for c in warm)
    finally:
        hair_pipeline._texture_bias = original

    # An unreadable texture falls back rather than raising.
    assert hair_pipeline.hair_tint_for_texture(None) == pytest.approx(neutral)


def test_broken_source_texture_paths_are_repaired():
    """Two hair diffuses are authored wrong in Oblivion itself.

    Grey_Mane.dds is named by 3 meshes but never shipped (Mane.dds is meant),
    and 5 meshes name a bare Grey.dds with no folder, which lands outside the
    hair directory.  Both render untextured if passed through.
    """
    from asset_convert.character.hair_pipeline import resolve_hair_texture

    tex_root = os.path.join('export', 'Oblivion.esm', 'textures')
    if not os.path.isdir(tex_root):
        pytest.skip('Oblivion textures not extracted')

    fixed = resolve_hair_texture(
        _BS.join(['textures', 'characters', 'hair', 'Grey_Mane.dds']), tex_root)
    assert fixed and fixed.lower().endswith('mane.dds')

    fixed = resolve_hair_texture('Grey.dds', tex_root)
    assert fixed and 'hair' in fixed.lower()

    # A path that already resolves must be left alone.
    assert resolve_hair_texture(
        _BS.join(['characters', 'hair', 'Grey.dds']), tex_root) is None


def test_race_lists_route_beast_hair_to_beast_races():
    """RNAM decides which races see a head part in the race menu.

    Every hair was pointed at HeadPartsHumansandVampires (000A8023) -- humans
    only, despite the constant's old name -- so Argonian/Khajiit/Orc/Elf hair
    was invisible for those races and only the human races saw new hairstyles.
    """
    from tes5_import.record_types.npc import (HDPT_RNAM_ALL_MINUS_BEAST, HDPT_RNAM_ARGONIAN, HDPT_RNAM_DREMORA, HDPT_RNAM_ELVES, HDPT_RNAM_HUMANS, HDPT_RNAM_KHAJIIT, HDPT_RNAM_ORC, HDPT_RNAM_REDGUARD, _hdpt_valid_races)

    assert _hdpt_valid_races('ArgonianSpikes') == HDPT_RNAM_ARGONIAN
    assert _hdpt_valid_races('KhajiitMane') == HDPT_RNAM_KHAJIIT
    assert _hdpt_valid_races('OrcTopknot') == HDPT_RNAM_ORC
    assert _hdpt_valid_races('dremoraHair') == HDPT_RNAM_DREMORA
    assert _hdpt_valid_races('RedguardCoil') == HDPT_RNAM_REDGUARD
    # 'DarkElf'/'HighElf'/'WoodElf' must not fall through to a human list.
    for edid in ('DarkElfMane', 'HighElfBun', 'WoodElfPony', 'ElfBraid'):
        assert _hdpt_valid_races(edid) == HDPT_RNAM_ELVES, edid
    assert _hdpt_valid_races('NordBaldPony') == HDPT_RNAM_HUMANS
    # Styles Oblivion did not name for a race stay available to everyone.
    for edid in ('Ponytail', 'MediumLength', 'Cropped', 'Blindfold'):
        assert _hdpt_valid_races(edid) == HDPT_RNAM_ALL_MINUS_BEAST, edid


# ---------------------------------------------------------------------------
# Head map: scalp-relative fitting of head gear
# ---------------------------------------------------------------------------

def test_head_fit_reaches_the_skyrim_crown():
    """The head group's fit must cover the Skyrim skull, crown included.

    The Oblivion skull is 19.2 units tall, the Skyrim one 22.5, so a fit
    seeded from the FK pose has nothing to project onto the extra height: it
    converged with the crown at z 130.8 (real 131.85) and the upper-back
    skull up to 5.2 units inside -- exactly where hair and helmets sit, which
    is why the back of the head poked through them.  Seeding that fit from
    the UV correspondence closes it to 1.8 max / 0.89 at the back.
    """
    pytest.importorskip('scipy')
    import numpy as np
    from pathlib import Path
    from scipy.spatial import cKDTree
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components

    npz = Path('asset_convert/generated/body_wrap_male.npz')
    if not npz.exists():
        pytest.skip('wrap field not built')
    with np.load(npz) as z:
        if not (z['has_head'][0] if 'has_head' in z else 0):
            pytest.skip('field has no head group')
        dst0 = z['dst0'].astype(float)
        tris = z['tris'].astype(int)

    n = len(dst0)
    r = np.concatenate([tris[:, 0], tris[:, 1], tris[:, 2]])
    c = np.concatenate([tris[:, 1], tris[:, 2], tris[:, 0]])
    _ncc, lab = connected_components(
        sp.coo_matrix((np.ones(len(r)), (r, c)), shape=(n, n)),
        directed=False)
    # the OB head mesh is a single 1275-vertex component
    head = None
    for i in range(_ncc):
        if (lab == i).sum() == 1275:
            head = lab == i
            break
    if head is None:
        pytest.skip('head component not identifiable')

    from asset_convert.character.body_wrap_build import head_uv_geometry, SK_HEAD_SETS
    from asset_convert.sources.skyrim_assets import get_body_nif_bytes
    raw = get_body_nif_bytes(SK_HEAD_SETS['male'])
    if raw is None:
        pytest.skip('vanilla malehead.nif unavailable')
    skv = head_uv_geometry(raw)[0]

    # the EAR region is deliberately not covered (ears are flattened out of
    # the correspondence — gear ignores them, like vanilla SK hair does)
    from asset_convert.character.head_fit import SK_EAR_BOXES
    o_sk = np.array([0.0, -1.548, 120.344])
    xmin, ymin, ymax, zmin, zmax = SK_EAR_BOXES['human']
    lc = skv - o_sk
    ear = ((np.abs(lc[:, 0]) > xmin) & (lc[:, 1] > ymin) & (lc[:, 1] < ymax)
           & (lc[:, 2] > zmin) & (lc[:, 2] < zmax))
    # ...nor is the SK neck below the OB head's bottom rim: the OB-shaped
    # fitted head simply ends there (measured: the OB mesh has NO geometry
    # below local z -3.5 on the BACK of the neck, z -5.6 in front), and the
    # wrap's clearance for that region comes from the BODY group's neck.
    neck = (lc[:, 2] < -5.4) | ((lc[:, 2] < -3.4) & (lc[:, 1] < 0.0))
    d, _ = cKDTree(dst0[head]).query(skv[~ear & ~neck])
    # every other Skyrim head vertex must be covered by the fitted surface
    assert d.max() < 2.5, f'skull uncovered by up to {d.max():.2f} units'
    assert dst0[head][:, 2].max() > 131.0, 'fit does not reach the crown'


def test_uv_head_seed_is_not_applied_to_geometry():
    """The UV correspondence SEEDS the head fit; it must never be a field.

    The two heads correspond topologically but are wildly non-isometric
    locally -- the raw map stretches the head's own edges by up to 2045% --
    so a correction field built from it mangles whatever rides it (measured
    on converted hair: 34% of edges distorted >15%).  Guard the seam: no
    runtime head-map API may exist for callers to reach for.
    """
    import asset_convert.character.body_wrap as bw
    assert not hasattr(bw, 'HeadMap')
    assert not hasattr(bw, 'get_head_map')
    assert not hasattr(bw, 'head_map_available')


def test_dynamic_trishape_uvs_come_from_the_skin_partition():
    """malehead.nif is a BSDynamicTriShape: verts inline, UVs in the skin
    partition.  Returning the inline buffer's None dropped the head's UVs
    and normals entirely, which is what starved the head fit."""
    pytest.importorskip('scipy')
    from asset_convert.character.body_wrap_build import head_uv_geometry, SK_HEAD_SETS
    from asset_convert.sources.skyrim_assets import get_body_nif_bytes
    raw = get_body_nif_bytes(SK_HEAD_SETS['male'])
    if raw is None:
        pytest.skip('vanilla malehead.nif unavailable')
    got = head_uv_geometry(raw)
    assert got is not None, 'head yielded no UV geometry'
    verts, tris, uvs = got
    assert len(uvs) == len(verts) and len(uvs) > 0
    assert 0.0 <= uvs.min() and uvs.max() <= 1.0


def test_head_bones_cover_both_naming_conventions():
    """Converted hair ships skinned to the RENAMED Skyrim head bone, so
    matching only 'Bip01 Head' classified every hair mesh as non-head."""
    from asset_convert.character.body_wrap import HEAD_BONES
    assert 'Bip01 Head' in HEAD_BONES
    assert 'NPC Head [Head]' in HEAD_BONES
