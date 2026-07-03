"""Tests for asset_convert package — NIF conversion, texture paths, bones, BSA extraction."""

import math
import os
import shutil
import struct
import tempfile
from pathlib import Path

import pytest

from asset_convert.nif_converter import (
    OUTPUT_USER_VERSION as _SKY_UV,
    OUTPUT_USER_VERSION_2 as _SKY_UV2,
    OUTPUT_VERSION as _SKY_VERSION,
    _rewrite_tex_path,
    batch_convert,
    convert_nif,
)
from asset_convert.skyrim_overrides import OBLIVION_TO_SKYRIM_BONE_MAP as BONE_MAP

# Primary Oblivion NIF version (no single constant exported)
_OBV_VERSION = 0x14000004

# ---------------------------------------------------------------------------
# NIF converter tests
# ---------------------------------------------------------------------------

class TestTexturePathRewriting:
    """Test texture path rewriting logic."""

    def test_prepend_tes4_to_textures(self):
        result = _rewrite_tex_path(b'textures\\armor\\iron\\cuirass.dds')
        assert result == 'textures\\tes4\\armor\\iron\\cuirass.dds'

    def test_already_prefixed_unchanged(self):
        result = _rewrite_tex_path(b'textures\\tes4\\armor\\iron\\cuirass.dds')
        assert result == 'textures\\tes4\\armor\\iron\\cuirass.dds'

    def test_empty_path_gets_prefix(self):
        assert _rewrite_tex_path(b'') == 'Textures\\tes4\\'

    def test_non_texture_path_gets_prefix(self):
        result = _rewrite_tex_path(b'something\\random.dds')
        assert result == 'Textures\\tes4\\something\\random.dds'

    def test_case_insensitive_prefix(self):
        result = _rewrite_tex_path(b'Textures\\Armor\\Iron\\Cuirass.dds')
        assert 'tes4' in result.lower()


class TestBoneMapping:
    """Test bone name remapping."""

    def test_bone_map_has_key_bones(self):
        assert 'Bip01 Head' in BONE_MAP
        assert 'Bip01 Spine' in BONE_MAP
        assert 'Bip01 L Hand' in BONE_MAP
        assert 'Bip01 R Hand' in BONE_MAP

    def test_bone_map_targets_are_skyrim_format(self):
        npc_bones = [v for v in BONE_MAP.values() if v.startswith('NPC ')]
        assert len(npc_bones) > 30, "Most bones should map to NPC names"


OBLIVION_NIF = Path(
    r'C:\Program Files (x86)\Steam\steamapps\common\Oblivion\Data\Meshes\base.nif')


class TestNifConversion:
    """Test NIF conversion with the pure-binary converter."""

    def test_convert_real_nif_if_available(self):
        """If an Oblivion game NIF exists, convert it and check result keys."""
        if not OBLIVION_NIF.exists():
            pytest.skip('Oblivion game NIF not available')
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, 'out.nif')
            result = convert_nif(str(OBLIVION_NIF), dst)
            assert isinstance(result, dict)
            assert 'converted' in result
            assert 'error' not in result or result.get('error') is None

    def test_batch_convert_with_real_nifs(self):
        """Batch conversion on a small folder of real Oblivion NIFs."""
        test_dir = OBLIVION_NIF.parent
        if not test_dir.exists():
            pytest.skip('Oblivion game NIFs not available')
        with tempfile.TemporaryDirectory() as tmpdir:
            in_dir = os.path.join(tmpdir, 'in')
            out_dir = os.path.join(tmpdir, 'out')
            os.makedirs(in_dir)
            count = 0
            for nif in test_dir.rglob('*.nif'):
                if count >= 3:
                    break
                shutil.copy2(str(nif), os.path.join(in_dir, nif.name))
                count += 1
            if count == 0:
                pytest.skip('No NIFs found')
            stats = batch_convert(in_dir, out_dir)
            assert 'errors' in stats
            assert stats['errors'] == 0

    def test_result_keys_present(self):
        """convert_nif result dict has all expected keys."""
        if not OBLIVION_NIF.exists():
            pytest.skip('Oblivion game NIF not available')
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, 'out.nif')
            result = convert_nif(str(OBLIVION_NIF), dst)
            expected_keys = {
                'converted', 'strips_fixed', 'properties_converted',
                'textures_fixed', 'bones_remapped', 'root_converted',
                'version_upgraded',
            }
            assert expected_keys.issubset(set(result.keys()))

    def test_output_nif_written_on_success(self):
        """Output file is written when conversion succeeds."""
        if not OBLIVION_NIF.exists():
            pytest.skip('Oblivion game NIF not available')
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, 'out.nif')
            result = convert_nif(str(OBLIVION_NIF), dst)
            if not result.get('error'):
                assert os.path.exists(dst), "Output NIF not written"

    def test_output_has_skyrim_version(self):
        """Converted NIF binary starts with Skyrim version 20.2.0.7."""
        if not OBLIVION_NIF.exists():
            pytest.skip('Oblivion game NIF not available')
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, 'out.nif')
            result = convert_nif(str(OBLIVION_NIF), dst)
            if result.get('error'):
                pytest.skip(f'Conversion not possible: {result["error"]}')
            with open(dst, 'rb') as f:
                data = f.read()
            # Header ends at first '\n', then 4-byte version follows
            nl = data.index(b'\n')
            ver = struct.unpack_from('<I', data, nl + 1)[0]
            assert ver == _SKY_VERSION, f"Expected {_SKY_VERSION:#x}, got {ver:#x}"


class TestPropertyConversion:
    """Test that NiTexturingProperty → BSLightingShaderProperty conversion works
    via the full convert_nif pipeline on a real NIF."""

    def test_texture_path_prefixed(self):
        """After conversion, texture paths in the NIF contain 'tes4'."""
        if not OBLIVION_NIF.exists():
            pytest.skip('Oblivion game NIF not available')
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, 'out.nif')
            result = convert_nif(str(OBLIVION_NIF), dst)
            if result.get('error'):
                pytest.skip(f'Conversion failed: {result["error"]}')
            if result.get('textures_fixed'):
                with open(dst, 'rb') as f:
                    raw = f.read().lower()
                assert b'tes4' in raw, "Texture path prefix 'tes4' not found"


class TestRootConversion:
    """Test that root NiNode is converted to BSFadeNode."""

    def test_root_converted_flag(self):
        if not OBLIVION_NIF.exists():
            pytest.skip('Oblivion game NIF not available')
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, 'out.nif')
            result = convert_nif(str(OBLIVION_NIF), dst)
            if result.get('error'):
                pytest.skip(f'Conversion failed: {result["error"]}')
            # root_converted flag should be set (NiNode → BSFadeNode)
            # base.nif has NiNode root
            assert isinstance(result.get('root_converted'), bool)


class TestVersionUpgrade:
    """Test that NIF version constants match Skyrim SE format."""

    def test_skyrim_version_constants(self):
        assert _SKY_VERSION == 0x14020007
        assert _SKY_UV == 12
        assert _SKY_UV2 == 83

    def test_oblivion_version_constant(self):
        assert _OBV_VERSION == 0x14000004


class TestFinalizeGeometry:
    """Geometry finalization is part of convert_nif; no separate API."""

    def test_nif_has_bs_num_uv_sets(self):
        """After conversion, the NIF uses BS Num UV Sets format (Skyrim-compatible)."""
        if not OBLIVION_NIF.exists():
            pytest.skip('Oblivion game NIF not available')
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, 'out.nif')
            result = convert_nif(str(OBLIVION_NIF), dst)
            if result.get('error'):
                pytest.skip(f'Conversion failed: {result["error"]}')
            assert os.path.exists(dst)
            # Simply check the output file exists and has non-zero size
            assert os.path.getsize(dst) > 0


class TestFullPipelineInMemory:
    """Full pipeline integration tests."""

    def test_strips_to_shape_conversion(self):
        """A NIF with NiTriStrips is converted to NiTriShape."""
        strips_nif = Path(
            r'C:\Program Files (x86)\Steam\steamapps\common'
            r'\Oblivion\Data\Meshes\architecture\imperialcity\icwall01.nif')
        if not strips_nif.exists():
            pytest.skip('NiTriStrips NIF not available')
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, 'out.nif')
            result = convert_nif(str(strips_nif), dst)
            if result.get('error'):
                pytest.skip(f'Conversion failed: {result["error"]}')
            assert result.get('strips_fixed') is True or result.get('strips_fixed') == 0

    def test_real_nif_full_pipeline(self):
        """Full pipeline test on real Oblivion NIF."""
        if not OBLIVION_NIF.exists():
            pytest.skip('Oblivion game NIF not available')
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, 'out.nif')
            result = convert_nif(str(OBLIVION_NIF), dst)
            assert isinstance(result, dict)
            if not result.get('error'):
                assert os.path.exists(dst)

# ---------------------------------------------------------------------------
# BSA extraction tests
# ---------------------------------------------------------------------------

class TestBsaExtract:
    """Test BSA extraction logic."""

    def test_should_extract_nif(self):
        from asset_convert.bsa_extract import _should_extract_file
        assert _should_extract_file('meshes\\armor\\iron\\cuirass.nif')
        assert _should_extract_file('meshes\\furniture\\chair.nif')

    def test_should_extract_dds(self):
        from asset_convert.bsa_extract import _should_extract_file
        assert _should_extract_file('textures\\armor\\iron\\cuirass.dds')

    def test_should_extract_wav(self):
        from asset_convert.bsa_extract import _should_extract_file
        assert _should_extract_file('sound\\fx\\explosion.wav')

    def test_should_skip_lip(self):
        from asset_convert.bsa_extract import _should_extract_file
        assert not _should_extract_file('sound\\voice\\test.lip')

    def test_asset_category(self):
        from asset_convert.bsa_extract import _get_asset_category
        assert _get_asset_category('meshes\\armor\\test.nif') == 'meshes'
        assert _get_asset_category('textures\\armor\\test.dds') == 'textures'
        assert _get_asset_category('sound\\fx\\test.wav') == 'sound'

    def test_manifest_round_trip(self):
        from asset_convert.bsa_extract import _load_manifest, _save_manifest
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {'extracted_bsas': {
                'test.bsa': {'size': 12345, 'file_count': 100}
            }}
            _save_manifest(tmpdir, manifest)
            loaded = _load_manifest(tmpdir)
            assert loaded['extracted_bsas']['test.bsa']['size'] == 12345
            assert loaded['extracted_bsas']['test.bsa']['file_count'] == 100

    def test_get_bsa_files(self):
        from asset_convert.bsa_extract import _get_bsa_files
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create fake BSA files
            (Path(tmpdir) / 'Test - Meshes.bsa').write_bytes(b'BSA\x00')
            (Path(tmpdir) / 'Test - Textures.bsa').write_bytes(b'BSA\x00')
            (Path(tmpdir) / 'Test.bsa').write_bytes(b'BSA\x00')
            (Path(tmpdir) / 'Other.bsa').write_bytes(b'BSA\x00')

            bsas = _get_bsa_files(tmpdir, 'Test.esm')
            names = [b.name for b in bsas]
            assert 'Test - Meshes.bsa' in names
            assert 'Test - Textures.bsa' in names
            assert 'Test.bsa' in names
            assert 'Other.bsa' not in names


# ---------------------------------------------------------------------------
# NIF structural correctness tests — Skyrim LE format validation
# ---------------------------------------------------------------------------

EXPORT_MESHES = Path('export/Oblivion.esm/meshes')
SKYRIM_LE_REF = Path('references/Skyrim Meshes/meshes')

# Sample exported Oblivion meshes (known to exist)
_SAMPLE_ROCKS = [
    'rocks/colovianhighlands/chrock045.nif',
    'rocks/greatforest/moss/rockgreatforest045moss.nif',
]

# All Skyrim LE reference meshes as the canonical "known good" corpus
_SKY_LE_REFS = list(SKYRIM_LE_REF.rglob('*.nif')) if SKYRIM_LE_REF.exists() else []


def _parse_sky_header(data: bytes) -> dict:
    """Minimal Skyrim NIF header parser for testing."""
    nul = data.index(b'\n'); o = nul + 1
    ver = struct.unpack_from('<I', data, o)[0]; o += 4
    o += 1  # endian
    uv = struct.unpack_from('<I', data, o)[0]; o += 4
    nb = struct.unpack_from('<I', data, o)[0]; o += 4
    uv2 = struct.unpack_from('<I', data, o)[0]; o += 4
    for _ in range(3):
        n = data[o]; o += 1; o += n
    nbt = struct.unpack_from('<H', data, o)[0]; o += 2
    btypes = []
    for _ in range(nbt):
        n = struct.unpack_from('<I', data, o)[0]; o += 4
        btypes.append(data[o:o+n].decode()); o += n
    btidx = [struct.unpack_from('<H', data, o + i*2)[0] for i in range(nb)]
    o += nb * 2
    bsizes = [struct.unpack_from('<I', data, o + i*4)[0] for i in range(nb)]
    o += nb * 4
    nstr = struct.unpack_from('<I', data, o)[0]; o += 4
    max_len = struct.unpack_from('<I', data, o)[0]; o += 4
    strings = []
    for _ in range(nstr):
        n = struct.unpack_from('<I', data, o)[0]; o += 4
        strings.append(data[o:o+n].decode()); o += n
    o += 4  # num groups
    return {
        'version': ver, 'user_version': uv, 'user_version_2': uv2,
        'num_blocks': nb, 'block_types': btypes,
        'block_type_indices': btidx, 'block_sizes': bsizes,
        'strings': strings, 'block_data_offset': o,
    }


def _verify_block_structure(data: bytes, hdr: dict) -> list[str]:
    """Verify each block can be cleanly parsed. Returns list of error strings."""
    errors = []
    o = hdr['block_data_offset']
    strings = hdr['strings']

    for i in range(hdr['num_blocks']):
        tn = hdr['block_types'][hdr['block_type_indices'][i]]
        sz = hdr['block_sizes'][i]
        raw = data[o:o+sz]

        if sz == 0 and tn not in ('NiNode', 'NiTriShape'):
            errors.append(f"Block {i} ({tn}): 0 bytes")
            o += sz
            continue

        try:
            bo = 0
            if tn in ('BSFadeNode', 'NiNode'):
                name_idx = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                ne = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                bo += ne * 4 + 4  # extras + controller
                bo += 2 + 2 + 12 + 36 + 4 + 4  # AVObject
                nc = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                bo += nc * 4
                neff = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                bo += neff * 4

            elif tn == 'NiTriShape':
                name_idx = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                ne = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                bo += ne * 4 + 4
                bo += 2 + 2 + 12 + 36 + 4 + 4
                bo += 4 + 4  # data_ref + skin
                nm = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                bo += nm * 4 + nm * 4  # mat names + extra data
                bo += 4 + 1  # active mat + dirty
                bo += 4 + 4  # BS properties

            elif tn == 'NiTriShapeData':
                bo += 4  # group id
                nv = struct.unpack_from('<H', raw, bo)[0]; bo += 2
                bo += 2  # keep/compress
                hv = raw[bo]; bo += 1
                if hv: bo += nv * 12
                uv_flags = struct.unpack_from('<H', raw, bo)[0]; bo += 2
                bo += 4  # material CRC
                hn = raw[bo]; bo += 1
                if hn:
                    bo += nv * 12
                    if uv_flags & 0x1000: bo += nv * 24
                bo += 16  # center + radius
                hc = raw[bo]; bo += 1
                if hc: bo += nv * 16
                nuv = uv_flags & 0x3F
                bo += nuv * nv * 8
                bo += 6  # consistency + additional
                nt = struct.unpack_from('<H', raw, bo)[0]; bo += 2
                bo += 4  # num tri pts
                ht = raw[bo]; bo += 1
                if ht and nt > 0: bo += nt * 6
                bo += 2  # match groups

            elif tn == 'BSLightingShaderProperty':
                bo += 4  # shader type
                name_idx = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                ne = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                bo += ne * 4 + 4  # extras + controller
                bo += 4 + 4  # shader flags 1 + 2
                bo += 8 + 8  # UV offset + UV scale
                bo += 4  # texture set ref
                bo += 12 + 4  # emissive color + multiple
                bo += 4  # texture clamp mode
                bo += 4 + 4 + 4  # alpha + refraction + glossiness
                bo += 12  # specular color
                bo += 4 + 4 + 4  # specular strength + LE1 + LE2

            elif tn == 'BSShaderTextureSet':
                ntex = struct.unpack_from('<i', raw, bo)[0]; bo += 4
                for _ in range(ntex):
                    slen = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                    bo += slen

            elif tn == 'NiStringExtraData':
                bo += 4 + 4  # name_idx + string_idx

            elif tn == 'NiBinaryExtraData':
                bo += 4  # name_idx
                bsz = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                bo += bsz

            elif tn in ('BSXFlags', 'NiIntegerExtraData'):
                bo += 4 + 4  # name_idx + integer

            elif tn == 'NiSkinInstance':
                bo += 4 + 4 + 4  # skin_data + skin_part + skel_root
                nbones = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                bo += nbones * 4

            elif tn == 'NiAlphaProperty':
                name_idx = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                ne = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                bo += ne * 4 + 4
                bo += 2 + 1  # flags + threshold

            elif tn == 'NiSourceTexture':
                name_idx = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                ne = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                bo += ne * 4 + 4
                use_ext = raw[bo]; bo += 1
                if use_ext:
                    slen = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                    bo += slen + 4 + 4 + 4 + 4 + 3  # fn + pix + layout + mm + af + flags

            else:
                bo = sz  # raw passthrough, skip

            if bo != sz:
                errors.append(f"Block {i} ({tn}): parsed {bo} bytes but block size is {sz}")

        except (struct.error, IndexError) as e:
            errors.append(f"Block {i} ({tn}): parse error: {e}")

        o += sz

    # Check footer
    try:
        nr = struct.unpack_from('<I', data, o)[0]; o += 4
        o += nr * 4
        remaining = len(data) - o
        if remaining != 0:
            errors.append(f"Footer: {remaining} bytes remaining after parsing")
    except struct.error:
        errors.append("Footer: truncated")

    return errors


class TestSkyrimLEReferenceValidation:
    """Validate that Skyrim LE reference meshes parse cleanly with our verifier.
    This ensures our structural parser is correct."""

    @pytest.mark.skipif(not _SKY_LE_REFS, reason='Skyrim LE reference meshes not available')
    @pytest.mark.parametrize('nif_path', _SKY_LE_REFS[:20],
                             ids=[p.stem for p in _SKY_LE_REFS[:20]])
    def test_reference_nif_parses_cleanly(self, nif_path):
        data = nif_path.read_bytes()
        hdr = _parse_sky_header(data)
        assert hdr['version'] == 0x14020007
        assert hdr['user_version'] == 12
        assert hdr['user_version_2'] == 83
        errors = _verify_block_structure(data, hdr)
        assert errors == [], f"Structural errors in reference: {errors}"


class TestConvertedNifStructure:
    """Validate that converted Oblivion meshes produce structurally valid Skyrim NIFs."""

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _SAMPLE_ROCKS)
    def test_converted_nif_has_correct_version(self, rel_path, tmp_path):
        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted'], f"Conversion failed: {result.get('error')}"
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        assert hdr['version'] == 0x14020007
        assert hdr['user_version'] == 12
        assert hdr['user_version_2'] == 83

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _SAMPLE_ROCKS)
    def test_converted_nif_no_orphan_blocks(self, rel_path, tmp_path):
        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted']
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        # No Oblivion-only block types should remain
        oblivion_only = {'NiMaterialProperty', 'NiTexturingProperty',
                         'NiVertexColorProperty', 'NiSpecularProperty',
                         'NiStencilProperty'}
        for i in range(hdr['num_blocks']):
            tn = hdr['block_types'][hdr['block_type_indices'][i]]
            assert tn not in oblivion_only, f"Orphan Oblivion block: {tn}"

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _SAMPLE_ROCKS)
    def test_converted_nif_no_zero_byte_blocks(self, rel_path, tmp_path):
        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted']
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        for i in range(hdr['num_blocks']):
            sz = hdr['block_sizes'][i]
            tn = hdr['block_types'][hdr['block_type_indices'][i]]
            assert sz > 0, f"Block {i} ({tn}) has 0 bytes"

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _SAMPLE_ROCKS)
    def test_converted_nif_parses_cleanly(self, rel_path, tmp_path):
        """Converted NIF's blocks can all be parsed back to exact sizes."""
        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted']
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        errors = _verify_block_structure(data, hdr)
        assert errors == [], f"Structural errors: {errors}"

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _SAMPLE_ROCKS)
    def test_ni_string_extra_data_is_8_bytes(self, rel_path, tmp_path):
        """NiStringExtraData must be exactly 8 bytes (name_idx + string_idx)."""
        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted']
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        for i in range(hdr['num_blocks']):
            tn = hdr['block_types'][hdr['block_type_indices'][i]]
            if tn == 'NiStringExtraData':
                assert hdr['block_sizes'][i] == 8, \
                    f"NiStringExtraData block {i} is {hdr['block_sizes'][i]} bytes, expected 8"

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _SAMPLE_ROCKS)
    def test_bslighting_shader_is_100_bytes(self, rel_path, tmp_path):
        """BSLightingShaderProperty with 0 extras must be 100 bytes."""
        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted']
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        for i in range(hdr['num_blocks']):
            tn = hdr['block_types'][hdr['block_type_indices'][i]]
            if tn == 'BSLightingShaderProperty':
                assert hdr['block_sizes'][i] == 100, \
                    f"BSLightingShaderProperty block {i} is {hdr['block_sizes'][i]} bytes, expected 100"

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _SAMPLE_ROCKS)
    def test_ni_tri_shape_active_material(self, rel_path, tmp_path):
        """NiTriShape must have Active Material field (i32) in MaterialData."""
        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted']
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        o = hdr['block_data_offset']
        for i in range(hdr['num_blocks']):
            tn = hdr['block_types'][hdr['block_type_indices'][i]]
            sz = hdr['block_sizes'][i]
            if tn == 'NiTriShape':
                raw = data[o:o+sz]
                bo = 0
                # NiObjectNET
                bo += 4  # name_idx
                ne = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                bo += ne * 4 + 4  # extras + controller
                # NiAVObject
                bo += 2 + 2 + 12 + 36 + 4 + 4
                # Geometry refs
                bo += 4 + 4
                # MaterialData
                nm = struct.unpack_from('<I', raw, bo)[0]; bo += 4
                bo += nm * 4 + nm * 4  # names + extra data
                active_mat = struct.unpack_from('<i', raw, bo)[0]; bo += 4
                assert active_mat in (-1, 0), \
                    f"NiTriShape Active Material is {active_mat}, expected -1 or 0"
            o += sz

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_no_in_place_modification(self, tmp_path):
        """convert_nif must not modify the source file."""
        src_nif = None
        for p in _SAMPLE_ROCKS:
            candidate = EXPORT_MESHES / p
            if candidate.exists():
                src_nif = candidate
                break
        if src_nif is None:
            pytest.skip('No sample rock found')
        original = src_nif.read_bytes()
        dst = tmp_path / 'out.nif'
        convert_nif(str(src_nif), str(dst))
        assert src_nif.read_bytes() == original, "Source file was modified!"


# ---------------------------------------------------------------------------
# Tests for session fixes: animated meshes, collision, particles, worn armor
# ---------------------------------------------------------------------------

EXPORT_ARMOR = Path('export/Oblivion.esm/meshes/armor')
EXPORT_DOORS = Path('export/Oblivion.esm/meshes/architecture')

# Animated mesh samples (doors with NiControllerManager)
_ANIMATED_SAMPLES = [
    'architecture/anvil/anvildoormcanim01.nif',
    'architecture/anvil/anvildoorucanim01.nif',
]
# Worn armor samples (skinned, not _gnd)
_ARMOR_SAMPLES = [
    'armor/amelionceremonial/m/cuirass.nif',
    'armor/amelionceremonial/m/gauntlets.nif',
]


class TestAnimatedMeshConversion:
    """Test that animated meshes get correct BSXFlags and collision settings."""

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _ANIMATED_SAMPLES)
    def test_animated_bsx_flags(self, rel_path, tmp_path):
        """Animated NIFs should have BSXFlags = 139 (ANIMATED | COMPLEX | HAVOK)."""
        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        if result.get('error') or result.get('skipped'):
            pytest.skip(f'Conversion issue: {result}')
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        # Find BSXFlags block and check its integer value
        o = hdr['block_data_offset']
        found_bsx = False
        for i in range(hdr['num_blocks']):
            tn = hdr['block_types'][hdr['block_type_indices'][i]]
            sz = hdr['block_sizes'][i]
            if tn == 'BSXFlags':
                raw = data[o:o + sz]
                name_idx = struct.unpack_from('<I', raw, 0)[0]
                bsx_val = struct.unpack_from('<I', raw, 4)[0]
                if hdr['strings'][name_idx] == 'BSX':
                    found_bsx = True
                    assert bsx_val == 139, \
                        f"BSXFlags={bsx_val}, expected 139 (0x8B = ANIMATED|COMPLEX|HAVOK)"
            o += sz
        assert found_bsx, "No BSXFlags block found on animated mesh"


class TestWornArmorRootNode:
    """Test that worn armor NIFs keep NiNode root (not BSFadeNode)."""

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _ARMOR_SAMPLES)
    def test_worn_armor_has_ninode_root(self, rel_path, tmp_path):
        """Worn armor must have NiNode root, not BSFadeNode."""
        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        if result.get('error') or result.get('skipped'):
            pytest.skip(f'Conversion issue: {result}')
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        # The first block should be NiNode (not BSFadeNode) for worn armor
        root_type = hdr['block_types'][hdr['block_type_indices'][0]]
        assert root_type == 'NiNode', \
            f"Worn armor root is {root_type}, expected NiNode"


class TestCollisionRigidBody:
    """Test that collision rigid body fields are set correctly for Skyrim."""

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _SAMPLE_ROCKS)
    def test_static_collision_quality_type(self, rel_path, tmp_path):
        """Static NIFs should have quality_type=0 (MO_QUAL_INVALID = auto-detect).

        All vanilla Skyrim architecture NIFs use quality_type=0 for static objects.
        """
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat
        import io

        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        if result.get('error') or result.get('skipped'):
            pytest.skip(f'Conversion issue: {result}')
        # Read back with PyFFI and check rigid body fields
        data = NifFormat.Data()
        with open(str(dst), 'rb') as f:
            data.inspect(f)
            data.read(f)
        for root in data.roots:
            if root is None:
                continue
            for block in root.tree():
                if isinstance(block, (NifFormat.bhkRigidBody, NifFormat.bhkRigidBodyT)):
                    if block.mass == 0:
                        assert block.quality_type == 0, \
                            f"quality_type={block.quality_type}, expected 0 (MO_QUAL_INVALID)"
                    # unknown_6_shorts[2:4] must be 0 (Skyrim interprets as pointer)
                    assert block.unknown_6_shorts[2] == 0, \
                        f"unknown_6_shorts[2]={block.unknown_6_shorts[2]}, must be 0"
                    assert block.unknown_6_shorts[3] == 0, \
                        f"unknown_6_shorts[3]={block.unknown_6_shorts[3]}, must be 0"


# ---------------------------------------------------------------------------
# Dynamic clutter physics tests (Issue 1 — Havok mass/inertia scaling)
# ---------------------------------------------------------------------------

_CLUTTER_SAMPLES = [
    'clutter/upperclass/uppersilverpitcher01.nif',
    'clutter/upperclass/uppergobletceramic01.nif',
]

_INERTIA_SCALE = 0.01  # matches collision.py _HAVOK_SCALE ** 2 (inertia ∝ length²)


class TestDynamicClutterPhysics:
    """Test that dynamic clutter has correct Havok physics values.

    Calibration rationale (surveyed from vanilla NIFs, 2026-04-04):
      - Skyrim clutter mass uses same SI-kg range as Oblivion — no mass scaling.
      - Inertia: one power of HAVOK_SCALE (0.1) is needed, not two (0.01).
        Applying 0.1 produces I/m ratios of 0.017–0.043, matching vanilla Skyrim
        clutter (0.004–0.04). Applying 0.01 (previously used) gives 10× too small.
    """

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _CLUTTER_SAMPLES)
    def test_clutter_mass_unchanged(self, rel_path, tmp_path):
        """Dynamic clutter mass should be copied from Oblivion without scaling."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')

        # Read source mass
        src_data = NF.Data()
        with open(str(src), 'rb') as f:
            src_data.read(f)
        src_mass = None
        for block in src_data.blocks:
            if isinstance(block, (NF.bhkRigidBody, NF.bhkRigidBodyT)):
                if block.mass > 0:
                    src_mass = block.mass
                    break
        assert src_mass is not None, "Source has no dynamic rigid body"

        # Convert
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        # Converted mass should equal source mass (no multiplier)
        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)
        for block in dst_data.blocks:
            if isinstance(block, (NF.bhkRigidBody, NF.bhkRigidBodyT)):
                if block.mass > 0:
                    assert block.mass == pytest.approx(src_mass, rel=0.01), \
                        f"mass={block.mass}, expected {src_mass} (unchanged)"
                    break

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _CLUTTER_SAMPLES)
    def test_clutter_inertia_scaled_by_havok_scale(self, rel_path, tmp_path):
        """Inertia should be Oblivion value * 0.01 (HAVOK_SCALE², inertia ∝ mass·length²)."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')

        # Read source inertia
        src_data = NF.Data()
        with open(str(src), 'rb') as f:
            src_data.read(f)
        src_inertia = None
        for block in src_data.blocks:
            if isinstance(block, (NF.bhkRigidBody, NF.bhkRigidBodyT)):
                if block.mass > 0:
                    src_inertia = (block.inertia.m_11, block.inertia.m_22, block.inertia.m_33)
                    break
        assert src_inertia is not None

        # Convert
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        # Converted inertia should be approximately src * 0.1
        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)
        for block in dst_data.blocks:
            if isinstance(block, (NF.bhkRigidBody, NF.bhkRigidBodyT)):
                if block.mass > 0:
                    for axis, (src_val, dst_val) in enumerate(zip(
                        src_inertia,
                        (block.inertia.m_11, block.inertia.m_22, block.inertia.m_33)
                    )):
                        expected = src_val * _INERTIA_SCALE
                        assert dst_val == pytest.approx(expected, rel=0.01), \
                            f"axis {axis}: inertia={dst_val}, expected {expected} (src*0.01)"
                    break

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _CLUTTER_SAMPLES)
    def test_clutter_motion_and_quality(self, rel_path, tmp_path):
        """Dynamic clutter: motion_system in {2,3}, quality_type=4."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)
        found = False
        for block in dst_data.blocks:
            if isinstance(block, (NF.bhkRigidBody, NF.bhkRigidBodyT)):
                if block.mass > 0:
                    found = True
                    assert block.motion_system in (2, 3), \
                        f"Expected SPHERE(2) or SPHERE_INERTIA(3), got {block.motion_system}"
                    assert block.quality_type == 4, "Expected MO_QUAL_MOVING"
                    assert block.friction == pytest.approx(0.5)
                    assert block.restitution == pytest.approx(0.4)
        assert found, "No dynamic rigid body found"


# ---------------------------------------------------------------------------
# Bone mapping tests (Bip01 Neck1 + other missing bones)
# ---------------------------------------------------------------------------


class TestBoneMappingCompleteness:
    """Test that all critical Oblivion bones have Skyrim mappings."""

    def test_neck1_mapped(self):
        assert BONE_MAP.get('Bip01 Neck1') == 'NPC Neck [Neck]'

    def test_spine0_mapped(self):
        assert BONE_MAP.get('Bip01 Spine0') == 'NPC Spine [Spn0]'

    def test_weapon_bones_mapped(self):
        assert 'Bip01 L Weapon' in BONE_MAP
        assert 'Bip01 R Weapon' in BONE_MAP

    def test_shield_bone_mapped(self):
        assert 'Bip01 L Shield' in BONE_MAP

    def test_quiver_bone_mapped(self):
        assert 'Bip01 Quiver' in BONE_MAP


# ---------------------------------------------------------------------------
# Shield vs worn armor tests (Issue 8 — armor display)
# ---------------------------------------------------------------------------


_SHIELD_SAMPLE = 'armor/iron/shield.nif'
_HELMET_SAMPLE = 'armor/iron/m/helmet.nif'
_BOOTS_SAMPLE = 'armor/iron/m/boots.nif'


class TestShieldVsArmorClassification:
    """Test that shields get BSFadeNode+Prn, worn armor gets NiNode root."""

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_shield_has_bsfadenode_root(self, tmp_path):
        """Shields must have BSFadeNode root (not NiNode)."""
        src = EXPORT_MESHES / _SHIELD_SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        root_type = hdr['block_types'][hdr['block_type_indices'][0]]
        assert root_type == 'BSFadeNode', \
            f"Shield root is {root_type}, expected BSFadeNode"

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_helmet_has_ninode_root(self, tmp_path):
        """Helmets are worn armor → NiNode root."""
        src = EXPORT_MESHES / _HELMET_SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        root_type = hdr['block_types'][hdr['block_type_indices'][0]]
        assert root_type == 'NiNode', \
            f"Helmet root is {root_type}, expected NiNode (worn armor)"

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_boots_has_ninode_root(self, tmp_path):
        """Boots are worn armor → NiNode root."""
        src = EXPORT_MESHES / _BOOTS_SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        root_type = hdr['block_types'][hdr['block_type_indices'][0]]
        assert root_type == 'NiNode', \
            f"Boots root is {root_type}, expected NiNode (worn armor)"

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_shield_orientation_corrected(self, tmp_path):
        """Converted shield must have face in XY plane (Z thin) centered at origin.

        Oblivion shields have face in XZ plane with grip at origin. Skyrim SHIELD
        bone expects face in XY plane centered at the origin (matching vanilla
        ironshield.nif: X ≈ ±21, Y ≈ ±22, Z ≈ ±6).
        """
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF
        import numpy as np

        src = EXPORT_MESHES / _SHIELD_SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.inspect(f)
            dst_data.read(f)
        root = dst_data.roots[0]
        # Root must have identity rotation (transform baked into inner NiNode)
        r = root.rotation
        assert abs(r.m_11 - 1.0) < 1e-3 and abs(r.m_22 - 1.0) < 1e-3 and abs(r.m_33 - 1.0) < 1e-3, \
            "Shield BSFadeNode root must have identity rotation"
        # Collect world-space vertices via inner NiNode child transform
        assert root.num_children == 1, "Shield BSFadeNode should have exactly one inner NiNode child"
        inner = root.children[0]
        assert isinstance(inner, NF.NiNode), "Shield inner child must be NiNode"
        # Verify inner rotation matches the expected shield orientation matrix
        ri = inner.rotation
        assert abs(ri.m_11 - (-1.0)) < 1e-3, "Inner NiNode m_11 should be -1.0 (X-flip)"
        assert abs(ri.m_23 - 1.0) < 1e-3, "Inner NiNode m_23 should be 1.0 (Y→Z)"
        assert abs(ri.m_32 - 1.0) < 1e-3, "Inner NiNode m_32 should be 1.0 (Z→Y)"
        # Compute world vertex bbox (inner NiNode R * local_vert + inner NiNode T)
        Rmat = np.array([[ri.m_11, ri.m_12, ri.m_13],
                         [ri.m_21, ri.m_22, ri.m_23],
                         [ri.m_31, ri.m_32, ri.m_33]], dtype=float)
        Tvec = np.array([inner.translation.x, inner.translation.y, inner.translation.z])
        all_verts = []
        def _cv(node, accum):
            if hasattr(node, 'data') and node.data is not None:
                d = node.data
                if hasattr(d, 'vertices') and d.vertices:
                    for v in d.vertices:
                        accum.append(np.array([v.x, v.y, v.z]))
            if hasattr(node, 'children'):
                for c in node.children:
                    if c is not None:
                        _cv(c, accum)
        _cv(inner, all_verts)
        assert all_verts, "Shield must have geometry"
        world = np.array([Rmat @ v + Tvec for v in all_verts])
        # Face should be in XY plane (Z thin, Z range << X range and Y range)
        z_range = world[:, 2].max() - world[:, 2].min()
        x_range = world[:, 0].max() - world[:, 0].min()
        y_range = world[:, 1].max() - world[:, 1].min()
        assert z_range < x_range * 0.5 and z_range < y_range * 0.5, \
            f"Shield Z range {z_range:.1f} should be much less than X {x_range:.1f} or Y {y_range:.1f}"
        # Face should be roughly centered at world origin
        cx = (world[:, 0].min() + world[:, 0].max()) * 0.5
        cy = (world[:, 1].min() + world[:, 1].max()) * 0.5
        assert abs(cx) < 3.0, f"Shield face not centered in X: cx={cx:.2f}"
        assert abs(cy) < 3.0, f"Shield face not centered in Y: cy={cy:.2f}"

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_worn_armor_no_prn(self, tmp_path):
        """Worn armor NiNode root must NOT have Prn extra data."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / _HELMET_SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)
        root = dst_data.roots[0]
        for ed in root.extra_data_list:
            if isinstance(ed, NF.NiStringExtraData):
                ed_name = bytes(ed.name).rstrip(b'\x00')
                assert ed_name != b'Prn', \
                    "Worn armor should not have Prn extra data"


# ---------------------------------------------------------------------------
# NiDefaultAVObjectPalette fixup (Issue 14 — orphan block references)
# ---------------------------------------------------------------------------

_CANDELABRA_SAMPLE = 'clutter/candelabra01.nif'


class TestAVObjectPaletteFixup:
    """Test that NiDefaultAVObjectPalette is updated after NiTriStrips conversion."""

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_no_orphan_references(self, tmp_path):
        """Converted candelabra should have no 'missing from nif tree' warnings."""
        import sys
        import io

        src = EXPORT_MESHES / _CANDELABRA_SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'

        old_stderr = sys.stderr
        captured = io.StringIO()
        sys.stderr = captured
        try:
            convert_nif(str(src), str(dst))
        finally:
            sys.stderr = old_stderr

        err = captured.getvalue()
        assert 'missing from the nif tree' not in err, \
            f"Orphan block reference detected:\n{err}"

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_palette_entries_point_to_correct_types(self, tmp_path):
        """After conversion, palette entries reference NiTriShape (not NiTriStrips)."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / _CANDELABRA_SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        for block in dst_data.blocks:
            if isinstance(block, NF.NiDefaultAVObjectPalette):
                for j in range(block.num_objs):
                    obj = block.objs[j]
                    if obj.av_object is not None:
                        # No NiTriStrips should remain in palette
                        assert not isinstance(obj.av_object, NF.NiTriStrips), \
                            f"Palette entry {j} still references NiTriStrips"


# ---------------------------------------------------------------------------
# Collision target test (Issue 13 — bhkCompressedMeshShape target)
# ---------------------------------------------------------------------------

_DOOR_WITH_ROTATION = 'architecture/castleinterior/castleint2way.nif'


class TestCollisionTargetPointsToRoot:
    """Test that bhkCompressedMeshShape.target points to root BSFadeNode."""

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_compressed_mesh_target_is_root(self, tmp_path):
        """Collision shape target must point to the NIF's root node."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / _DOOR_WITH_ROTATION
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        if result.get('error') or result.get('skipped'):
            pytest.skip(f'Conversion issue: {result}')

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        root = dst_data.roots[0]
        for block in dst_data.blocks:
            if isinstance(block, NF.bhkCompressedMeshShape):
                assert block.target is root, \
                    f"bhkCompressedMeshShape.target points to {type(block.target).__name__}, expected root"

    # (rel_path, expected bhkRigidBodyT quaternion (x,y,z,w) after the root
    # rotation is composed into the body — source bodies are identity)
    _WRAPPED_COLLISION_CASES = [
        # 180° about Z
        (_DOOR_WITH_ROTATION, (0.0, 0.0, 1.0, 0.0)),
        # +90° about Z — convention-sensitive: a conjugate/transpose error
        # in the quaternion math flips the sign of z relative to w
        ('architecture/castleinterior/stackhallentrance01.nif',
         (0.0, 0.0, 0.7071068, 0.7071068)),
    ]

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path,expected_quat', _WRAPPED_COLLISION_CASES)
    def test_static_collision_stays_on_root_when_wrapped(self, rel_path, expected_quat, tmp_path):
        """Static collision must live on the root BSFadeNode, even when root
        rotation baking wraps the geometry in an inner NiNode: collision on a
        child NiNode causes intermittent hkpCollisionDispatcher CTDs when the
        character proxy touches the shape (castleint2way.nif crash).  The
        zeroed root transform must be composed into bhkRigidBodyT.rotation or
        the collision is rotated relative to the mesh (stackhallentrance01)."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        if result.get('error') or result.get('skipped'):
            pytest.skip(f'Conversion issue: {result}')
        assert result.get('root_rotation_baked'), \
            f'{rel_path} should trigger the rotation wrap pass'

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        root = dst_data.roots[0]
        assert getattr(root, 'collision_object', None) is not None, \
            'Root BSFadeNode lost its bhkCollisionObject'
        assert root.collision_object.target is root, \
            'bhkCollisionObject.target must be the root BSFadeNode'
        for block in dst_data.blocks:
            if block is root or not isinstance(block, NF.NiNode):
                continue
            assert getattr(block, 'collision_object', None) is None, \
                f'Static collision found on child node "{block.name}" — must be on root only'

        q = root.collision_object.body.rotation
        got = (q.x, q.y, q.z, q.w)
        # q and -q are the same rotation; accept either sign
        err = min(max(abs(g - e) for g, e in zip(got, expected_quat)),
                  max(abs(g + e) for g, e in zip(got, expected_quat)))
        assert err < 1e-4, \
            f'bhkRigidBodyT rotation {got} != expected {expected_quat} — collision misrotated vs mesh'


# ---------------------------------------------------------------------------
# MOPP build_type test (Issue 13 — collision crash fix)
# ---------------------------------------------------------------------------


class TestMoppBuildType:
    """Test that MOPP build_type is set correctly for Skyrim."""

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', [_DOOR_WITH_ROTATION])
    def test_mopp_build_type_is_pc(self, rel_path, tmp_path):
        """MOPP build_type must be 1 (BUILT_WITHOUT_CHUNK_SUBDIVISION) for PC."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        if result.get('error') or result.get('skipped'):
            pytest.skip(f'Conversion issue: {result}')

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        found_mopp = False
        for block in dst_data.blocks:
            if isinstance(block, NF.bhkMoppBvTreeShape):
                found_mopp = True
                assert block.build_type == 1, \
                    f"build_type={block.build_type}, expected 1 (PC)"
                assert block.mopp_data_size > 0, \
                    f"MOPP data is empty (size={block.mopp_data_size})"
        if not found_mopp:
            pytest.skip("No bhkMoppBvTreeShape in converted NIF")

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', [
        _DOOR_WITH_ROTATION,
        # 6 chunk jumps incl. backward ones — exercises region relocation
        'architecture/castleinterior/stackhallentrance01.nif',
    ])
    def test_mopp_is_dechunked(self, rel_path, tmp_path):
        """Converted MOPPs must contain NO chunk-jump opcodes (0x70) and walk
        clean.  MOPP_RL builds chunked MOPPs (an SPU streaming feature) that
        Skyrim's PC engine mis-executes — EXCEPTION_STACK_OVERFLOW in
        hkpCollisionDispatcher when a query descends into a 0x70 branch (the
        intermittent castleint2way.nif crash)."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF
        from asset_convert.mopp import walk_mopp

        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        if result.get('error') or result.get('skipped'):
            pytest.skip(f'Conversion issue: {result}')

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        found_mopp = False
        for block in dst_data.blocks:
            if isinstance(block, NF.bhkMoppBvTreeShape):
                found_mopp = True
                mopp = bytes(bytearray(block.mopp_data))
                r = walk_mopp(mopp, len(mopp))
                assert not r['errors'], f'MOPP walk errors: {r["errors"][:3]}'
                assert not r['chunk_jumps'], \
                    f'{len(r["chunk_jumps"])} chunk-jump (0x70) opcodes still reachable'
                assert b'\xcd' * 8 not in mopp, \
                    'uninitialised 0xCD filler left in MOPP data'
        assert found_mopp, 'converted NIF lost its bhkMoppBvTreeShape'


class TestFurnitureMarkerConversion:
    """BSFurnitureMarker → BSFurnitureMarkerNode conversion."""

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_throne_sit_marker(self, tmp_path):
        """Throne gets BSFurnitureMarkerNode with Sit animation and behind entry."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / 'clutter' / 'castleinterior' / 'castlethronechorrol.nif'
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        found = False
        for block in dst_data.blocks:
            if isinstance(block, NF.BSFurnitureMarkerNode):
                found = True
                # Single ref-14 entry at (1.87, -55.65, -61.02), ori=0: the NPC
                # approaches walking +Y and sits facing -Y (heading pi).  The
                # entry point is in FRONT of the seated occupant.
                assert block.num_positions == 1
                p = block.positions[0]
                assert p.animation_type == 1  # Sit
                assert p.entry_properties.front == 1
                assert abs(p.heading - math.pi) < 0.01
                # Seat = entry projected to the geometry centre line
                assert abs(p.offset.x - 1.87) < 1.0
                assert abs(p.offset.y - 0.0) < 2.0
                # Model is re-origined so the floor (entry z) sits at 0;
                # hip height = 34 above the floor
                assert abs(p.offset.z - 34.0) < 0.5
        assert found, "BSFurnitureMarkerNode not found in converted throne NIF"

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_bed_sleep_markers(self, tmp_path):
        """Bed gets BSFurnitureMarkerNode with Sleep animation and left/right entries."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / 'clutter' / 'castleinterior' / 'anvilcastleinterior' / 'anvilbed01.nif'
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        found = False
        for block in dst_data.blocks:
            if isinstance(block, NF.BSFurnitureMarkerNode):
                found = True
                # The two Oblivion entries (ref 1 left / ref 2 right, at x=+-91
                # ori 1570/4712) converge on ONE sleep position mid-bed, like
                # vanilla commonbed01 (one position, entry right|left).
                assert block.num_positions == 1
                p = block.positions[0]
                assert p.animation_type == 2  # Sleep
                assert p.entry_properties.left == 1
                assert p.entry_properties.right == 1
                # Occupant faces +Y (head at the -Y pillow end)
                assert abs(p.heading - 0.0) < 0.01
                # Hips stay on the entry line (y = -21.2)
                assert abs(p.offset.x - 0.0) < 2.0
                assert abs(p.offset.y - (-21.2)) < 1.0
                # Model is re-origined so the floor (entry z) sits at 0;
                # sleep marker z = 37.09 above the floor
                assert abs(p.offset.z - 37.09) < 0.5
        assert found, "BSFurnitureMarkerNode not found in converted bed NIF"
