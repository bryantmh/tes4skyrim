"""Tests for the grass shader profile and landscape normal-map fixes."""
import struct
from pathlib import Path

import pytest

from asset_convert import grass_profile, landscape_normals
from asset_convert.flipbook import _decode_dxt
from asset_convert.nif_converter import NifFormat, convert_nif

EXPORT_MESHES = Path('export/Oblivion.esm/meshes')

# A known Oblivion grass model (GRAS record TES4GCLongGrass01)
_GRASS_SAMPLE = 'plants/gclonggrass01.nif'


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

        before = _decode_dxt(path.read_bytes()[128:128 + 32], 8, 8, 'DXT1')
        assert landscape_normals.fix_normal_specular(path) is True

        data = path.read_bytes()
        assert data[84:88] == b'DXT5'
        after = _decode_dxt(data[128:128 + 64], 8, 8, 'DXT5')
        for i in range(0, len(before), 4):
            assert before[i:i + 3] == after[i:i + 3], f'RGB mismatch at texel {i // 4}'
            assert after[i + 3] == landscape_normals.SPECULAR_ALPHA

        # Mip chain length: 4 DXT5 blocks (top) + 1 (mip1) = 80 bytes
        assert len(data) == 128 + 5 * 16

    def test_dxt5_left_untouched(self, tmp_path):
        hdr = bytearray(128)
        hdr[0:4] = b'DDS '
        hdr[84:88] = b'DXT5'
        path = tmp_path / 'already_n.dds'
        path.write_bytes(bytes(hdr) + b'\x00' * 16)
        assert landscape_normals.fix_normal_specular(path) is False

    def test_idempotent(self, tmp_path):
        top = [_opaque_block(0xF800, 0x001F, 0)]
        path = tmp_path / 'idem_n.dds'
        path.write_bytes(_make_dxt1_dds(4, 4, 1, [top]))
        assert landscape_normals.fix_normal_specular(path) is True
        assert landscape_normals.fix_normal_specular(path) is False
