"""Tests for the grass shader profile and landscape normal-map fixes."""
import os
import struct
from pathlib import Path

import pytest

from asset_convert.nif import grass_profile
from asset_convert.texture import landscape_normals
from asset_convert.nif.flipbook import decode_dxt
from asset_convert.nif.nif_converter import NifFormat, convert_nif

EXPORT_MESHES = Path('export/Oblivion.esm/meshes')

# A known Oblivion grass model (GRAS record TES4GCLongGrass01)
_GRASS_SAMPLE = 'plants/gclonggrass01.nif'

#: An FNV grass model whose diffuse lives on TallGrassShaderProperty.file_name.
_FNV_GRASS = Path('export/FalloutNV.esm/meshes/landscape/grass/nvgreengrass01.nif')


def _grass_world_bounds(data):
    """(minZ, maxZ) of all geometry in root space — a rotation/flatten-invariant
    check that the vertex bake preserved world-space geometry."""
    root = data.roots[0]
    zs = []
    for blk in root.tree():
        if isinstance(blk, (NifFormat.NiTriShape, NifFormat.NiTriStrips)) and blk.data:
            m = blk.get_transform(root)
            for v in blk.data.vertices:
                zs.append((v * m).z)
    return (min(zs), max(zs))


# ---------------------------------------------------------------------------
# grass_profile
# ---------------------------------------------------------------------------

class TestGrassProfile:
    def test_load_grass_model_paths(self, tmp_path):
        (tmp_path / 'GRAS.txt').write_text(
            '---RECORD_BEGIN---\n'
            'Signature=GRAS\n'
            'Model.MODL=Plants\\\\GCLongGrass01.NIF\n'
            '---RECORD_END---\n'
            '---RECORD_BEGIN---\n'
            'Signature=GRAS\n'
            'Model.MODL=Plants\\\\Dementia\\\\DementiaGrass03.NIF\n'
            '---RECORD_END---\n')
        paths = grass_profile.load_grass_model_paths(tmp_path)
        assert paths == {'plants\\gclonggrass01.nif',
                         'plants\\dementia\\dementiagrass03.nif'}

    def test_load_missing_gras_txt(self, tmp_path):
        assert grass_profile.load_grass_model_paths(tmp_path) == set()

    def test_grass_model_dest(self):
        # Working GRAS records keep models under landscape\grass (45/45
        # surveyed across vanilla + grass mods); tree is flattened.
        assert (grass_profile.grass_model_dest('Plants\\GCLongGrass01.NIF')
                == 'landscape\\grass\\tes4_gclonggrass01.nif')
        assert (grass_profile.grass_model_dest('Plants\\Dementia\\DSeaGrass01.NIF')
                == 'landscape\\grass\\tes4_dseagrass01.nif')

    def test_convert_gras_record_invariants(self):
        """GRAS records: zero OBND, MODT stub, landscape\\grass MODL."""
        from tes5_import.record_types.items import convert_GRAS
        rec = {
            'Signature': 'GRAS', 'FormID': '00050AA0', 'RecordFlags': '0',
            'EditorID': 'DGrass03',
            'Model.MODL': 'Plants\\Dementia\\DementiaGrass03.NIF',
            'DATA.Density': '40', 'DATA.MinSlope': '0', 'DATA.MaxSlope': '45',
            'DATA.UnitFromWaterAmount': '0', 'DATA.UnitFromWaterType': '0',
            'DATA.PositionRange': '40.0', 'DATA.HeightRange': '0.3',
            'DATA.ColorRange': '0.3', 'DATA.WavePeriod': '10.0',
            'DATA.Flags': '6',
        }
        data = convert_GRAS(rec)
        obnd_at = data.index(b'OBND')
        assert data[obnd_at + 6:obnd_at + 18] == b'\x00' * 12  # all-zero bounds
        assert b'landscape\\grass\\tes4_dementiagrass03.nif\x00' in data
        modt_at = data.index(b'MODT')
        assert data[modt_at + 6:modt_at + 18] == struct.pack('<III', 2, 0, 0)

    def test_convert_gras_placement_parity(self):
        """Source planters step at max(PositionRange, 80) and boost density by
        80/PositionRange, so Density 40 at PositionRange 40 ships as 80 at 80."""
        from tes5_import.record_types.items import convert_GRAS
        rec = {'Signature': 'GRAS', 'FormID': '00050AA0', 'RecordFlags': '0',
               'DATA.Density': '40', 'DATA.PositionRange': '40.0',
               'DATA.HeightRange': '0.3', 'DATA.ColorRange': '0.3',
               'DATA.WavePeriod': '10.0', 'DATA.Flags': '6'}
        dat = convert_GRAS(rec)
        dat = dat[dat.index(b'DATA') + 6:]
        assert dat[0] == 80
        assert struct.unpack_from('<f', dat, 12)[0] == 80.0

    def test_convert_gras_density_saturates(self):
        """FNV NVGreenGrass02: 35% at PositionRange 17 boosts to 100%, not 165%."""
        from tes5_import.record_types.items import convert_GRAS
        rec = {'Signature': 'GRAS', 'FormID': '0016ACCB', 'RecordFlags': '0',
               'DATA.Density': '35', 'DATA.PositionRange': '17.0',
               'DATA.HeightRange': '0.3', 'DATA.ColorRange': '0.25',
               'DATA.WavePeriod': '30.0', 'DATA.Flags': '6'}
        dat = convert_GRAS(rec)
        dat = dat[dat.index(b'DATA') + 6:]
        assert dat[0] == 100
        assert struct.unpack_from('<f', dat, 12)[0] == 80.0

    def test_convert_gras_wide_range_untouched(self):
        """PositionRange above 80 and its density pass through unchanged."""
        from tes5_import.record_types.items import convert_GRAS
        rec = {'Signature': 'GRAS', 'FormID': '00000001', 'RecordFlags': '0',
               'DATA.Density': '30', 'DATA.PositionRange': '90.0',
               'DATA.HeightRange': '0.3', 'DATA.ColorRange': '0.3',
               'DATA.WavePeriod': '10.0', 'DATA.Flags': '6'}
        dat = convert_GRAS(rec)
        dat = dat[dat.index(b'DATA') + 6:]
        assert dat[0] == 30
        assert struct.unpack_from('<f', dat, 12)[0] == 90.0

    @pytest.mark.skipif(not _FNV_GRASS.exists(), reason='FNV export not available')
    def test_tallgrass_shader_diffuse(self, tmp_path):
        """FNV grass carries its diffuse on TallGrassShaderProperty.file_name."""
        dst = tmp_path / 'fnvgrass.nif'
        result = convert_nif(str(_FNV_GRASS), str(dst))
        assert result['converted'], f"Conversion failed: {result.get('error')}"
        data = NifFormat.Data()
        with open(dst, 'rb') as f:
            data.read(f)
        sets = [b for b in data.blocks if isinstance(b, NifFormat.BSShaderTextureSet)]
        assert sets
        assert sets[0].textures[0].lower() == b'textures\\tes4\\landscape\\grass\\nvgreengrass.dds'

    @pytest.mark.skipif(not (EXPORT_MESHES / _GRASS_SAMPLE).exists(),
                        reason='Export meshes not available')
    def test_apply_grass_profile(self, tmp_path):
        dst = tmp_path / 'grass.nif'
        result = convert_nif(str(EXPORT_MESHES / _GRASS_SAMPLE), str(dst))
        assert result['converted'], f"Conversion failed: {result.get('error')}"

        assert grass_profile.apply_grass_profile(dst) is True

        data = NifFormat.Data()
        with open(dst, 'rb') as f:
            data.read(f)
        shaders = [b for b in data.blocks
                   if isinstance(b, NifFormat.BSLightingShaderProperty)]
        alphas = [b for b in data.blocks
                  if isinstance(b, NifFormat.NiAlphaProperty)]
        assert shaders and alphas
        for sh in shaders:
            sf1 = sh.shader_flags_1
            assert sf1.slsf_1_own_emit == 1
            assert sf1.slsf_1_vertex_alpha == 1
            assert sf1.slsf_1_specular == 0
            assert sh.glossiness == grass_profile.GRASS_GLOSSINESS
            assert sh.emissive_multiple == grass_profile.GRASS_EMISSIVE_MULT
            assert sh.texture_clamp_mode == grass_profile.GRASS_TEXTURE_CLAMP
        for ap in alphas:
            # Alpha testing only — blend bit must be clear
            assert int(ap.flags) & grass_profile.ALPHA_BLEND_BIT == 0
            assert ap.threshold <= grass_profile.GRASS_MAX_ALPHA_THRESHOLD

        # Second pass is a no-op
        assert grass_profile.apply_grass_profile(dst) is False

    @pytest.mark.skipif(not (EXPORT_MESHES / 'plants/bwcattail02.nif').exists(),
                        reason='Export meshes not available')
    def test_grass_flattens_rotated_root(self, tmp_path):
        """A source whose root carries a non-identity rotation is wrapped in an
        inner NiNode by the generic converter (Pass-6c).  Skyrim's grass
        instancer CTDs on that nesting, so apply_grass_profile must collapse it
        back to BSFadeNode -> geometry (TES4 BWCattail CTD, 2026-07-10)."""
        dst = tmp_path / 'cattail.nif'
        result = convert_nif(str(EXPORT_MESHES / 'plants/bwcattail02.nif'), str(dst))
        assert result['converted'], f"Conversion failed: {result.get('error')}"

        # Before profiling: converter wraps geometry in an inner NiNode.
        data = NifFormat.Data()
        with open(dst, 'rb') as f:
            data.read(f)
        root = data.roots[0]
        assert isinstance(root, NifFormat.BSFadeNode)
        assert any(type(c).__name__ == 'NiNode' for c in root.children), \
            "expected an inner NiNode wrapper on the rotated source"

        world_before = _grass_world_bounds(data)

        assert grass_profile.apply_grass_profile(dst) is True

        # After: geometry sits directly under the fade-node root, no NiNode.
        data = NifFormat.Data()
        with open(dst, 'rb') as f:
            data.read(f)
        root = data.roots[0]
        assert isinstance(root, NifFormat.BSFadeNode)
        for c in root.children:
            assert isinstance(c, (NifFormat.NiTriShape, NifFormat.NiTriStrips)), \
                f"grass root still has a non-geometry child: {type(c).__name__}"

        # World-space geometry must be preserved by the vertex bake.
        world_after = _grass_world_bounds(data)
        for a, b in zip(world_before, world_after):
            assert abs(a - b) < 1e-2, f"geometry bounds shifted: {world_before} -> {world_after}"

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel', [
        'plants/groundcovermediumgrass01.nif',   # has_triangles=False, shared verts
        'plants/groundcoverlonggrass01.nif',     # has_triangles=False + match groups
        'plants/groundcoverpineappleweed02.nif',  # has_triangles=False, sequential
        'plants/jmmediumgrasssnow01.nif',        # match groups only
    ])
    def test_triangle_reconstruction(self, rel, tmp_path):
        """Oblivion grass meshes shipping without triangle arrays (or with
        legacy match groups) must come out of conversion with real, sane
        triangles — the Skyrim grass planter CTDs on either defect."""
        src = EXPORT_MESHES / rel
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted'], f"Conversion failed: {result.get('error')}"
        data = NifFormat.Data()
        with open(dst, 'rb') as f:
            data.read(f)
        for b in data.blocks:
            if type(b).__name__ != 'NiTriShapeData':
                continue
            assert b.has_triangles and b.num_triangles
            assert b.num_match_groups == 0
            tris = b.get_triangles()
            nv = b.num_vertices
            assert all(i < nv for t in tris for i in t)
            vs = [(v.x, v.y, v.z) for v in b.vertices]
            for a, bb, c in tris:
                e1 = [vs[bb][k] - vs[a][k] for k in range(3)]
                e2 = [vs[c][k] - vs[a][k] for k in range(3)]
                cx = (e1[1] * e2[2] - e1[2] * e2[1],
                      e1[2] * e2[0] - e1[0] * e2[2],
                      e1[0] * e2[1] - e1[1] * e2[0])
                area_sq = sum(x * x for x in cx)
                assert area_sq > 1.0, 'degenerate reconstructed blade'

    def test_run_places_copy_under_landscape_grass(self, tmp_path):
        (tmp_path / 'export').mkdir()
        (tmp_path / 'export' / 'GRAS.txt').write_text(
            '---RECORD_BEGIN---\n'
            'Model.MODL=Plants\\\\GCLongGrass01.NIF\n'
            '---RECORD_END---\n')
        src_dir = tmp_path / 'meshes' / 'tes4' / 'plants'
        src_dir.mkdir(parents=True)
        src = EXPORT_MESHES / _GRASS_SAMPLE
        if not src.exists():
            pytest.skip('Export meshes not available')
        result = convert_nif(str(src), str(src_dir / 'gclonggrass01.nif'))
        assert result['converted']

        processed, modified, missing = grass_profile.run(
            tmp_path / 'export', tmp_path / 'meshes')
        assert (processed, missing) == (1, 0)
        assert (tmp_path / 'meshes' / 'landscape' / 'grass'
                / 'tes4_gclonggrass01.nif').exists()


# ---------------------------------------------------------------------------
# landscape_normals
# ---------------------------------------------------------------------------

def _make_dxt1_dds(width, height, mip_count, blocks_per_mip):
    """Build a minimal DXT1 DDS from pre-encoded 8-byte blocks per mip."""
    hdr = bytearray(128)
    hdr[0:4] = b'DDS '
    struct.pack_into('<I', hdr, 4, 124)
    struct.pack_into('<I', hdr, 12, height)
    struct.pack_into('<I', hdr, 16, width)
    struct.pack_into('<I', hdr, 28, mip_count)
    struct.pack_into('<I', hdr, 76, 32)      # pixel format size
    struct.pack_into('<I', hdr, 80, 0x4)     # DDPF_FOURCC
    hdr[84:88] = b'DXT1'
    return bytes(hdr) + b''.join(b''.join(m) for m in blocks_per_mip)


def _opaque_block(c0, c1, indices):
    assert c0 > c1
    return struct.pack('<HHI', c0, c1, indices)


def _three_color_block(c0, c1, indices):
    assert c0 <= c1
    return struct.pack('<HHI', c0, c1, indices)


class TestLandscapeNormals:
    def test_dxt1_to_dxt5_preserves_rgb(self, tmp_path):
        # 8x8 top mip (4 blocks, one in 3-color mode using only indices
        # 0/1, which survive the endpoint swap exactly), plus a 4x4 mip.
        red, blue = 0xF800, 0x001F
        top = [
            _opaque_block(red, blue, 0x00000000),
            _opaque_block(red, blue, 0x55555555),
            _three_color_block(blue, red, 0x50505050),  # indices 0/1 only
            _opaque_block(red, blue, 0xAAAAAAAA),
        ]
        mip1 = [_opaque_block(red, blue, 0x00000000)]
        path = tmp_path / 'test_n.dds'
        path.write_bytes(_make_dxt1_dds(8, 8, 2, [top, mip1]))

        before = decode_dxt(path.read_bytes()[128:128 + 32], 8, 8, 'DXT1')
        assert landscape_normals.run(tmp_path) == (1, 1)

        data = path.read_bytes()
        assert data[84:88] == b'DXT5'
        after = decode_dxt(data[128:128 + 64], 8, 8, 'DXT5')
        for i in range(0, len(before), 4):
            assert before[i:i + 3] == after[i:i + 3], f'RGB mismatch at texel {i // 4}'
            assert after[i + 3] == landscape_normals.SPECULAR_ALPHA

        # Mip chain length: 4 DXT5 blocks (top) + 1 (mip1) = 80 bytes
        assert len(data) == 128 + 5 * 16

    def test_bright_dxt5_mask_is_dimmed(self, tmp_path):
        """A DXT5 landscape normal with a bright mask gets the constant too.

        Morroblivion's terrain normals carry a generated height field in
        alpha (median 102/255 against Oblivion's 16); TES4 never read it, so
        it is not a specular mask.
        """
        blk = bytes([200, 200]) + b'\x00' * 6 + _opaque_block(0xF800, 0x001F, 0)
        path = tmp_path / 'bright_n.dds'
        path.write_bytes(_make_dds(b'DXT5', 4, 4, 1, [[blk]]))
        assert landscape_normals.run(tmp_path) == (1, 1)
        data = path.read_bytes()
        assert data[128] == landscape_normals.SPECULAR_ALPHA
        assert data[128 + 8:128 + 16] == blk[8:]

    def test_idempotent(self, tmp_path):
        top = [_opaque_block(0xF800, 0x001F, 0)]
        path = tmp_path / 'idem_n.dds'
        path.write_bytes(_make_dxt1_dds(4, 4, 1, [top]))
        assert landscape_normals.run(tmp_path) == (1, 1)
        assert landscape_normals.run(tmp_path) == (1, 0)

    def test_ensure_ltex_normals_writes_only_missing(self, tmp_path):
        """A land texture whose normal any plugin ships is left alone; one
        shipping none gets a flat normal under this plugin's tree."""
        from asset_convert.game_paths import set_namespace
        set_namespace('tes4')
        export = tmp_path / 'export'
        export.mkdir()
        (export / 'LTEX.txt').write_text(
            '---RECORD_BEGIN---\nICON=textures\\\\Tx_has.dds\n---RECORD_END---\n'
            '---RECORD_BEGIN---\nICON=textures\\\\Tx_none.dds\n---RECORD_END---\n'
            '---RECORD_BEGIN---\nICON=dirt02.dds\n---RECORD_END---\n')
        out = tmp_path / 'output'
        master = out / 'Master.esm' / 'textures' / 'tes4'
        master.mkdir(parents=True)
        (master / 'tx_has_n.dds').write_bytes(b'DDS ')
        mine = out / 'Plugin.esp' / 'textures'
        checked, written = landscape_normals.ensure_ltex_normals(
            export, mine, out)
        assert (checked, written) == (3, 2)
        assert (mine / 'tes4' / 'tx_none_n.dds').is_file()
        flat = (mine / 'tes4' / 'landscape' / 'dirt02_n.dds').read_bytes()
        assert flat[84:88] == b'DXT5' and flat[128] == landscape_normals.SPECULAR_ALPHA
        assert not (mine / 'tes4' / 'Tx_has_n.dds').exists()
        assert landscape_normals.ensure_ltex_normals(export, mine, out) == (3, 0)

    def test_ensure_ltex_normals_reuses_the_existing_folder_spelling(self, tmp_path):
        """An ICON naming `Oblivion\\` writes into the existing `oblivion/`, no twin."""
        from asset_convert.game_paths import set_namespace
        set_namespace('tes4')
        export = tmp_path / 'export'
        export.mkdir()
        (export / 'LTEX.txt').write_text(
            '---RECORD_BEGIN---\nICON=Oblivion\\\\Evil01.dds\n---RECORD_END---\n')
        out = tmp_path / 'output'
        land = out / 'Plugin.esp' / 'textures' / 'tes4' / 'landscape'
        (land / 'oblivion').mkdir(parents=True)
        (land / 'oblivion' / 'evil01.dds').write_bytes(b'DDS ')
        landscape_normals.ensure_ltex_normals(export, out / 'Plugin.esp' / 'textures', out)
        assert sorted(os.listdir(land)) == ['oblivion']
        assert (land / 'oblivion' / 'evil01_n.dds').is_file()


def _make_dds(fourcc, width, height, mip_count, blocks_per_mip):
    """Minimal DDS around pre-encoded blocks, for DXT3/DXT5 as well as DXT1."""
    hdr = bytearray(128)
    hdr[0:4] = b'DDS '
    struct.pack_into('<I', hdr, 4, 124)
    struct.pack_into('<I', hdr, 12, height)
    struct.pack_into('<I', hdr, 16, width)
    struct.pack_into('<I', hdr, 28, mip_count)
    struct.pack_into('<I', hdr, 76, 32)
    struct.pack_into('<I', hdr, 80, 0x4)
    hdr[84:88] = fourcc
    return bytes(hdr) + b''.join(b''.join(m) for m in blocks_per_mip)


class TestConstantSpecularAlpha:
    """`set_constant_alpha` gives a maskless normal map a usable one.

    The property that matters is NOT the alpha -- it is that the RGB normal
    survives untouched.  A wrong specular value is a cosmetic error; a damaged
    normal map is a broken surface.
    """

    RED, BLUE = 0xF800, 0x001F

    def _dxt3(self, alpha_nibbles=0xFF):
        """One DXT3 block: 8 bytes of 4-bit alpha, then the color block."""
        return bytes([alpha_nibbles] * 8) + _opaque_block(
            self.RED, self.BLUE, 0x1B1B1B1B)

    def _dxt5(self, a0, a1):
        return (bytes([a0, a1]) + b'\x00' * 6
                + _opaque_block(self.RED, self.BLUE, 0x1B1B1B1B))

    def test_dxt3_becomes_dxt5_with_exact_alpha(self, tmp_path):
        """DXT3's nibbles cannot hold 64 (multiples of 17), so it must convert.

        This is Nehrim's poster case: 33 sign/poster normals ship as DXT3 with
        a constant 255 alpha, which Skyrim reads as full specular on flat
        signage.  The alpha is a format artefact, not authored intent.
        """
        path = tmp_path / 'poster_n.dds'
        path.write_bytes(_make_dds(b'DXT3', 4, 4, 1, [[self._dxt3(0xFF)]]))
        color_before = path.read_bytes()[128 + 8:128 + 16]

        assert landscape_normals.set_constant_alpha(path, 64) is True
        data = path.read_bytes()
        assert data[84:88] == b'DXT5'
        assert data[128] == 64 and data[129] == 64
        assert data[130:136] == b'\x00' * 6, 'alpha indices must select alpha0'
        assert data[128 + 8:128 + 16] == color_before, 'RGB was modified'
        assert len(data) == 128 + 16, 'DXT3 and DXT5 are both 16 bytes/block'

    def test_dxt5_alpha_replaced_color_kept(self, tmp_path):
        """A DXT5 block gets the constant alpha while its RGB is untouched."""
        path = tmp_path / 'flat_n.dds'
        path.write_bytes(_make_dds(b'DXT5', 4, 4, 1, [[self._dxt5(25, 25)]]))
        color_before = path.read_bytes()[128 + 8:128 + 16]

        assert landscape_normals.set_constant_alpha(path, 64) is True
        data = path.read_bytes()
        assert data[128] == 64 and data[129] == 64
        assert data[128 + 8:128 + 16] == color_before

    def test_mip_chain_is_walked(self, tmp_path):
        """Every mip must be rewritten, not just the top one.

        A half-converted file still decodes, so a wrong offset would show up
        only as distant surfaces keeping the old alpha.
        """
        path = tmp_path / 'mips_n.dds'
        path.write_bytes(_make_dds(b'DXT5', 8, 8, 2,
                                   [[self._dxt5(200, 200)] * 4,
                                    [self._dxt5(200, 200)]]))
        assert landscape_normals.set_constant_alpha(path, 64) is True
        data = path.read_bytes()
        assert len(data) == 128 + 5 * 16
        for blk in range(5):
            off = 128 + blk * 16
            assert data[off] == 64, f'mip block {blk} kept its old alpha'

    def test_idempotent(self, tmp_path):
        path = tmp_path / 'twice_n.dds'
        path.write_bytes(_make_dds(b'DXT5', 4, 4, 1, [[self._dxt5(25, 25)]]))
        landscape_normals.set_constant_alpha(path, 64)
        once = path.read_bytes()
        landscape_normals.set_constant_alpha(path, 64)
        assert path.read_bytes() == once

    def test_uncompressed_is_refused(self, tmp_path):
        """Only the block formats are understood; anything else stays put."""
        hdr = bytearray(128)
        hdr[0:4] = b'DDS '
        hdr[84:88] = b'\x00' * 4
        path = tmp_path / 'raw_n.dds'
        path.write_bytes(bytes(hdr) + b'\xff' * 64)
        assert landscape_normals.set_constant_alpha(path, 64) is False

    def test_default_normal_is_flat_and_masked(self, tmp_path):
        """The shared stand-in must classify as a real DXT5 with our alpha."""
        from asset_convert.texture import parallax
        landscape_normals.write_default_normal(tmp_path)
        dest = tmp_path / 'tes4' / 'default_n.dds'
        assert dest.is_file()
        info = parallax.classify_alpha(dest.read_bytes())
        assert info.fmt == 'dxt5'
        assert info.mean == pytest.approx(landscape_normals.DEFAULT_MASK_ALPHA)

    def _dxt5_varied(self):
        """A block that really modulates: 8 interpolated levels across texels.

        Endpoints alone are not enough -- alpha0/alpha1 with all-zero INDICES
        gives every texel alpha0, i.e. ONE level, which is exactly the
        constant case `spec_mask` rejects (and the mistake this test made on
        its first writing).  The variation lives in the 3-bit-per-texel index
        block, not in the endpoints.
        """
        bits = 0
        for i in range(16):
            bits |= (i % 8) << (3 * i)
        return (bytes([255, 0]) + bits.to_bytes(6, 'little')
                + _opaque_block(self.RED, self.BLUE, 0x1B1B1B1B))

    def test_authored_mask_is_left_alone(self, tmp_path):
        """The sweep must never touch a real per-texel mask."""
        path = tmp_path / 'authored_n.dds'
        path.write_bytes(_make_dds(b'DXT5', 4, 4, 1, [[self._dxt5_varied()]]))
        before = path.read_bytes()
        checked, fixed, kinds = landscape_normals.normalize_specular_alpha(
            tmp_path)
        assert checked == 1
        assert fixed == 0, f'an authored mask was rewritten ({kinds})'
        assert path.read_bytes() == before
