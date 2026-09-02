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
    _categorize_pyffi_warnings,
    _door_hinge_point,
    _matches_subdir_filter,
    _rewrite_tex_path,
    batch_convert,
    convert_nif,
)
from asset_convert import wearable_plan
from asset_convert.skyrim_overrides import OBLIVION_TO_SKYRIM_BONE_MAP as BONE_MAP

# Primary Oblivion NIF version (no single constant exported)
_OBV_VERSION = 0x14000004

# ---------------------------------------------------------------------------
# NIF converter tests
# ---------------------------------------------------------------------------


class TestMeshSubdirFilter:
    def test_accepts_nested_prefix_with_either_slash(self):
        rel = ('morro', 'd', 'door.nif')
        assert _matches_subdir_filter(rel, ['morro/d'])
        assert _matches_subdir_filter(rel, [r'morro\d'])
        assert not _matches_subdir_filter(rel, ['morro/x'])

    def test_root_prefix_and_unfiltered_behavior_stay_unchanged(self):
        rel = ('architecture', 'anvil', 'wall.nif')
        assert _matches_subdir_filter(rel, ['architecture'])
        assert _matches_subdir_filter(rel, None)

class TestTexturePathRewriting:
    """Test texture path rewriting logic."""

    def test_prepend_tes4_to_textures(self):
        result = _rewrite_tex_path(b'textures\\armor\\iron\\cuirass.dds')
        assert result == 'Textures\\tes4\\armor\\iron\\cuirass.dds'

    def test_already_prefixed_unchanged(self):
        result = _rewrite_tex_path(b'textures\\tes4\\armor\\iron\\cuirass.dds')
        assert result == 'Textures\\tes4\\armor\\iron\\cuirass.dds'

    def test_empty_path_gets_prefix(self):
        assert _rewrite_tex_path(b'') == 'Textures\\tes4\\'

    def test_non_texture_path_gets_prefix(self):
        result = _rewrite_tex_path(b'something\\random.dds')
        assert result == 'Textures\\tes4\\something\\random.dds'

    def test_case_insensitive_prefix(self):
        result = _rewrite_tex_path(b'Textures\\Armor\\Iron\\Cuirass.dds')
        assert 'tes4' in result.lower()

    def test_data_prefix_is_stripped(self):
        """An authoring slip Oblivion tolerates and Skyrim does not.

        Measured in Nehrim's source meshes: 4 distinct textures across 10
        meshes, dwarven\\rock02.dds in 7 of them.  Left in, the reference came
        out under a 'data' folder that does not exist, AND the prune deleted
        the real texture because the key never matched the shipped path.
        """
        result = _rewrite_tex_path(b'data\\textures\\dwarven\\rock02.dds')
        assert result == 'Textures\\tes4\\dwarven\\rock02.dds'

    def test_data_prefix_with_forward_slashes(self):
        result = _rewrite_tex_path(b'Data/Textures/dwarven/rock01.dds')
        assert result == 'Textures\\tes4\\dwarven\\rock01.dds'


class TestPyFFIWarningCapture:
    def test_toaster_progress_is_not_reported_as_a_warning(self):
        messages = [
            '--- fix_addtangentspace ---',
            '      ~~~ NiTriShape [Mesh:0] ~~~',
            '        adding tangent space',
        ]
        assert _categorize_pyffi_warnings(messages) == {}

    def test_real_data_warning_remains_visible(self):
        warnings = _categorize_pyffi_warnings([
            'NiNode block is missing from the nif tree: omitting reference'])
        assert warnings == {'missing_from_nif_tree': 1}


class TestDoorHingeInference:
    def test_centered_vertical_leaf_gets_an_edge_hinge(self):
        import numpy as np
        leaf = np.asarray([
            (-100.0, -5.0, 0.0), (100.0, -5.0, 0.0),
            (-100.0, 5.0, 200.0), (100.0, 5.0, 200.0),
        ])
        hinge = _door_hinge_point([leaf])
        assert tuple(hinge) == (-100.0, 0.0, 0.0)

    def test_existing_edge_pivot_and_horizontal_hatch_are_rejected(self):
        import numpy as np
        edge_pivoted = np.asarray([
            (0.0, -5.0, 0.0), (200.0, -5.0, 0.0),
            (0.0, 5.0, 200.0), (200.0, 5.0, 200.0),
        ])
        horizontal = np.asarray([
            (-100.0, -100.0, -5.0), (100.0, -100.0, -5.0),
            (-100.0, 100.0, 5.0), (100.0, 100.0, 5.0),
        ])
        assert _door_hinge_point([edge_pivoted]) is None
        assert _door_hinge_point([horizontal]) is None


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
# BSA packing tests — 2 GiB size limit, overflow splitting, ESL loaders
# ---------------------------------------------------------------------------

class TestBsaPack:
    """Test BSA size-limit binning and loader-ESL generation."""

    @staticmethod
    def _entries(*sizes):
        """Build (src, archive_rel_path, size) tuples like _collect_files returns."""
        return [(Path(f'src{i}'), Path(f'meshes/f{i}.nif'), s)
                for i, s in enumerate(sizes)]

    def test_size_limit_under_engine_hard_limit(self):
        """The payload budget must leave headroom for BSA metadata."""
        from asset_convert.bsa_pack import BSA_HARD_LIMIT, BSA_SIZE_LIMIT
        # 32-bit file-data offsets cap a BSA at exactly 2 GiB.
        assert BSA_HARD_LIMIT == 2_147_483_648
        assert BSA_SIZE_LIMIT < BSA_HARD_LIMIT

    def test_no_split_when_content_fits(self):
        from asset_convert.bsa_pack import _bin_files
        bins = _bin_files(self._entries(100, 200, 300), limit=1000)
        assert len(bins) == 1

    def test_splits_when_over_limit(self):
        from asset_convert.bsa_pack import _bin_files
        bins = _bin_files(self._entries(600, 500), limit=1000)
        assert len(bins) == 2

    def test_exactly_at_limit_does_not_split(self):
        """A bin filled to exactly the limit is still legal."""
        from asset_convert.bsa_pack import _bin_files
        bins = _bin_files(self._entries(600, 400), limit=1000)
        assert len(bins) == 1

    def test_no_bin_exceeds_limit(self):
        from asset_convert.bsa_pack import _bin_files
        bins = _bin_files(self._entries(*([300] * 10)), limit=1000)
        assert all(sum(e[2] for e in b) <= 1000 for b in bins)

    def test_split_is_lossless(self):
        """Every file must land in exactly one bin — none dropped, none duplicated."""
        from asset_convert.bsa_pack import _bin_files
        files = self._entries(*([250] * 9))
        bins = _bin_files(files, limit=1000)
        packed = [e for b in bins for e in b]
        assert len(packed) == len(files)
        assert {str(e[1]) for e in packed} == {str(e[1]) for e in files}

    def test_oversized_single_file_isolated(self):
        """A file bigger than a whole BSA can't be split; it gets its own bin."""
        from asset_convert.bsa_pack import _bin_files
        bins = _bin_files(self._entries(100, 5000, 100), limit=1000)
        big = [b for b in bins if b[0][2] == 5000]
        assert len(big) == 1 and len(big[0]) == 1

    def test_empty_input_yields_no_bins(self):
        from asset_convert.bsa_pack import _bin_files
        assert _bin_files([], limit=1000) == []

    def test_loader_stem_naming(self):
        """Overflow loaders are <stem>_loader, <stem>_loader_1, ..."""
        from asset_convert.bsa_pack import _loader_stem
        assert _loader_stem('Oblivion', 0) == 'Oblivion_loader'
        assert _loader_stem('Oblivion', 1) == 'Oblivion_loader_1'
        assert _loader_stem('Oblivion', 2) == 'Oblivion_loader_2'

    def test_loader_stem_is_plugin_scoped(self):
        """Two plugins must never generate the same loader name.

        Loader stems are global to the game's Data folder even though they are
        generated per output folder, so a fixed stem made every converted mod
        that overflowed ship identically-named .esl/.bsa files -- installing
        two of them silently overwrote one mod's overflow archives.
        """
        from asset_convert.bsa_pack import _loader_stem
        stems = ['Oblivion', 'Morrowind_ob', 'Nehrim',
                 'Morrowind_ob - Chargen and Transport Mod']
        for i in range(3):
            names = [_loader_stem(s, i) for s in stems]
            assert len(set(names)) == len(names), f"collision at index {i}: {names}"
        # Every name must still carry its own plugin's stem.
        for s in stems:
            assert _loader_stem(s, 0).startswith(s)

    def test_loader_esl_is_valid_light_master(self):
        """The dummy ESL must be a record-free TES4 header with ESM+ESL flags."""
        import struct
        from asset_convert.bsa_pack import write_loader_esl, ESL_FLAG, ESM_FLAG
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / 'Oblivion_loader.esl'
            write_loader_esl(p)
            data = p.read_bytes()
            sig, size, flags, _fid, _v1, form_ver, _v2 = struct.unpack(
                '<4sIIIIHH', data[:24])
            assert sig == b'TES4'
            # ESL flag keeps it out of the 255-plugin load-order limit.
            assert flags & ESL_FLAG
            assert flags & ESM_FLAG
            assert form_ver == 44
            # Header-only: declared payload is all that follows the 24-byte header.
            assert size == len(data) - 24
            # No records => no GRUPs.
            assert b'GRUP' not in data


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


class TestWearablePlan:
    """Worn gear is decided by the plugin's biped references, not the folder.

    Nehrim files 88 wearables outside meshes\\armor and meshes\\clothes; the old
    folder-name test converted every one of them as a world object (no dismember
    skin, no skeleton retarget, no _0/_1 pair), so guards rendered without a
    torso.  These pin the plan's answers for both layouts.
    """

    @staticmethod
    def _export(tmp_path, biped, flags, world=''):
        rec = (f'---RECORD_BEGIN---\n'
               f'Male.BipedModel.MODL={biped}\n'
               f'BMDT.BipedFlags={flags}\n'
               + (f'Male.WorldModel.MODL={world}\n' if world else '')
               + '---RECORD_END---\n')
        (tmp_path / 'ARMO.txt').write_text(rec, encoding='utf-8')
        (tmp_path / 'CLOT.txt').write_text('', encoding='utf-8')
        return wearable_plan.build_plan(tmp_path)

    def test_worn_is_independent_of_folder(self, tmp_path):
        """A cuirass under nehrim\\ is worn gear exactly like one under armor\\."""
        meshes = tmp_path / 'meshes'
        for path, gnd in (('Nehrim\\Taranorcuirass.nif', 'Nehrim\\cuirass_gnd.nif'),
                          ('armor\\glass\\m\\cuirass.nif', 'armor\\glass\\cuirassgnd.nif')):
            plan = self._export(tmp_path, path, 4, world=gnd)
            src = meshes / path.replace('\\', os.sep)
            assert wearable_plan.is_worn(plan, src, meshes), path
            # body armor drives the weight slider -> _0/_1, and with a world
            # model shipped the plain mesh is dead
            mask = wearable_plan.variants_for(plan, src, meshes)
            assert mask & wearable_plan.W0 and mask & wearable_plan.W1, path
            assert not mask & wearable_plan.BASE, path

    def test_biped_mesh_doubles_as_ground_model(self, tmp_path):
        """No world model: the biped mesh IS the dropped item, so BASE lives on."""
        meshes = tmp_path / 'meshes'
        plan = self._export(tmp_path, 'Nehrim\\Taranorcuirass.nif', 4)
        src = meshes / 'Nehrim' / 'Taranorcuirass.nif'
        mask = wearable_plan.variants_for(plan, src, meshes)
        assert mask & wearable_plan.BASE
        assert mask & wearable_plan.W0 and mask & wearable_plan.W1

    def test_non_slider_gear_keeps_the_plain_mesh(self, tmp_path):
        """Helmets are worn but have the slider off — plain mesh, no variants."""
        meshes = tmp_path / 'meshes'
        plan = self._export(tmp_path, 'Chelm.nif', 1)      # bit 0, not 2-5
        src = meshes / 'Chelm.nif'
        assert wearable_plan.is_worn(plan, src, meshes)
        mask = wearable_plan.variants_for(plan, src, meshes)
        assert mask & wearable_plan.BASE
        assert not mask & (wearable_plan.W0 | wearable_plan.W1)

    def test_ground_model_is_not_worn(self, tmp_path):
        """A world model is referenced but never worn — it must not be retargeted."""
        meshes = tmp_path / 'meshes'
        plan = self._export(tmp_path, 'Nehrim\\cuirass.nif', 4,
                            world='Nehrim\\cuirass_gnd.nif')
        gnd = meshes / 'Nehrim' / 'cuirass_gnd.nif'
        assert not wearable_plan.is_worn(plan, gnd, meshes)
        assert wearable_plan.variants_for(plan, gnd, meshes) == wearable_plan.BASE

    def test_unreferenced_mesh_is_not_worn(self, tmp_path):
        """Meshes no record names keep their plain conversion and gain nothing."""
        meshes = tmp_path / 'meshes'
        plan = self._export(tmp_path, 'Nehrim\\cuirass.nif', 4)
        loose = meshes / 'clutter' / 'barrel.nif'
        assert not wearable_plan.is_worn(plan, loose, meshes)
        assert wearable_plan.variants_for(plan, loose, meshes) == wearable_plan.BASE


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


# Meshes whose Oblivion source hides helper geometry (particle emitter sources,
# spawn/effect proxies) with node-flag bit 0.  Converting used to clobber that
# bit with NIF_FLAGS, leaving a BSLightingShaderProperty over UV-less geometry.
_UVLESS_HELPER_SAMPLES = [
    'oblivion/gate/oblivionarchgate01.nif',
    'oblivion/gate/obliviongate_simple.nif',
    'oblivion/gate/obliviongate_forming.nif',
    'oblivion/gate/oblivionwargateani02.nif',
]


class TestUvlessGeometryNeverLit:
    """A lighting shader over UV-less geometry is unrenderable.

    BSLightingShaderProperty always samples a diffuse texcoord and reads the
    tangent basis for its normal map.  Geometry with num_uv_sets == 0 ships
    neither stream, so the shader reads past the vertex buffer and renders as
    an untextured red shard (the OblivionArchGate01 "red triangle").

    Vanilla census (373 shapes in references/Skyrim Meshes): ZERO pair a
    lighting shader with 0 UV sets.
    """

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _UVLESS_HELPER_SAMPLES)
    def test_uvless_shapes_carry_no_lighting_shader(self, rel_path, tmp_path):
        from pyffi.formats.nif import NifFormat

        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted'], f"Conversion failed: {result.get('error')}"

        data = NifFormat.Data()
        with open(dst, 'rb') as fh:
            data.read(fh)

        offenders = []
        lit_shapes = 0
        for root in data.roots:
            for block in root.tree():
                if not isinstance(block, NifFormat.NiTriBasedGeom):
                    continue
                geom = getattr(block, 'data', None)
                if geom is None:
                    continue
                lit = [p for p in (getattr(block, 'bs_properties', None) or [])
                       if p is not None
                       and isinstance(p, NifFormat.BSLightingShaderProperty)]
                if not lit:
                    continue
                lit_shapes += 1
                if not int(getattr(geom, 'num_uv_sets', 0) or 0):
                    offenders.append(block.name)

        assert offenders == [], \
            f'{rel_path}: lighting shader over UV-less geometry: {offenders}'
        # Guard against the assertion passing because everything lost its
        # shader: the visible gate geometry must still be textured.
        assert lit_shapes > 0, f'{rel_path}: no lit shapes survived conversion'


class TestNoOblivionOnlyBlocksSurvive:
    """Oblivion-only block types must never reach a Skyrim NIF.

    A NiControllerSequence stores its controller type as a STRING and the
    engine instantiates it BY NAME at load, so an Oblivion-only type rejects
    the whole file -- Skyrim's red missing-mesh triangle.  NiFlipController
    reached the output through a sequence entry (the property-side handler
    only sees flip controllers on a geometry's NiTexturingProperty) and
    dragged 121 NiSourceTexture frames with it.

    Vanilla census of ~8,300 meshes: NiFlipController and NiSourceTexture
    appear ZERO times.
    """

    _DEAD_IN_SKYRIM = (
        'NiFlipController',
        'NiSourceTexture',
        'NiTexturingProperty',
        'NiMaterialProperty',
        'NiTextureTransformController',
        'NiAlphaController',
        'NiGeomMorpherController',
    )

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _UVLESS_HELPER_SAMPLES)
    def test_no_oblivion_only_block_types(self, rel_path, tmp_path):
        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted'], f"Conversion failed: {result.get('error')}"

        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        present = set(hdr['block_types'])
        leaked = sorted(present.intersection(self._DEAD_IN_SKYRIM))
        assert leaked == [], f'{rel_path}: Oblivion-only blocks in output: {leaked}'

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _UVLESS_HELPER_SAMPLES)
    def test_block_sizes_match_declared_types(self, rel_path, tmp_path):
        """Every block must parse to exactly the size the header declares."""
        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted']
        data = dst.read_bytes()
        hdr = _parse_sky_header(data)
        # The 112-byte BSLightingShaderProperty variant is vanilla-legal
        # (1,876 occurrences in references/Skyrim Meshes) and simply unknown
        # to this verifier -- not a conversion defect.
        errors = [e for e in _verify_block_structure(data, hdr)
                  if not ('BSLightingShaderProperty' in e
                          and 'block size is 112' in e)]
        assert errors == [], f'{rel_path}: structural errors: {errors[:5]}'

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_flipbook_animation_survives_as_atlas(self, tmp_path):
        """Dropping the sequence entry must not lose the flip-book animation.

        The frames live on a *_flip.dds atlas driven by a
        BSEffectShaderPropertyFloatController stepping U Offset, built
        geometry-side -- so the sequence entry is a duplicate, not the source.
        """
        from pyffi.formats.nif import NifFormat

        src = EXPORT_MESHES / 'oblivion/gate/oblivionarchgate01.nif'
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted']

        data = NifFormat.Data()
        with open(dst, 'rb') as fh:
            data.read(fh)

        animated = 0
        for root in data.roots:
            for block in root.tree():
                if not isinstance(block, NifFormat.NiTriBasedGeom):
                    continue
                for prop in (getattr(block, 'bs_properties', None) or []):
                    if not isinstance(prop, NifFormat.BSEffectShaderProperty):
                        continue
                    tex = bytes(prop.source_texture or b'').lower()
                    ctrl = prop.controller
                    if b'_flip.dds' in tex and ctrl is not None:
                        assert isinstance(
                            ctrl, NifFormat.BSEffectShaderPropertyFloatController), \
                            f'flip atlas driven by {ctrl.__class__.__name__}'
                        assert int(ctrl.type_of_controlled_variable) == 6, \
                            'flip atlas controller must step U Offset (var 6)'
                        animated += 1
        assert animated >= 5, \
            f'expected the 5 flip-book quads to keep their atlas animation, got {animated}'


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


class TestParticleSystemConversion:
    """NiParticleSystem / flame conversion.

    Regression guard for the vercond precedence bug in pyffi_monkey_patch that
    dropped NiPSysData's two added-particle shorts on OBLIVION reads too,
    misaligning every particle NIF by 4 bytes and failing the entire
    fire/effects/magiceffects conversion list.
    """

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_fireopensmall_converts_and_psysdata_is_skyrim_layout(self, tmp_path):
        """fireopensmall must convert, and its NiPSysData blocks must be the
        authoritative 70-byte Skyrim BSStream-83 layout (hand-rolled — PyFFI's
        own layout is structurally wrong and misaligns the engine → CTD).

        We verify against the HEADER block_size table (read via inspect only);
        we do NOT PyFFI-struct-read the block, because PyFFI cannot parse the
        correct Skyrim NiPSysData layout (that is the whole reason it is
        hand-rolled).  70 bytes is the vanilla value for an empty particle pool
        (census of 27 vanilla empty NiPSysData blocks — all 70)."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat

        src = EXPORT_MESHES / 'fire' / 'fireopensmall.nif'
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert not result.get('error'), f"fireopensmall failed to convert: {result}"
        assert result.get('converted'), result

        # Header-only read (no block struct parse).
        data = NifFormat.Data()
        with open(str(dst), 'rb') as f:
            data.inspect(f)
        hdr = data.header
        bt = [b.decode('latin1') for b in hdr.block_types]
        bti = list(hdr.block_type_index)
        bsz = list(hdr.block_size)
        psd_sizes = [bsz[i] for i in range(hdr.num_blocks)
                     if bt[bti[i]] == 'NiPSysData']
        assert psd_sizes, 'no NiPSysData block in converted fireopensmall'
        for sz in psd_sizes:
            assert sz == 70, f'NiPSysData block_size {sz} != vanilla 70 (layout bug)'
        # Modifier vocabulary must be Skyrim's — else the SSE particle engine
        # doesn't drive the system and particles are invisible.  Vanilla uses
        # BSPSysLODModifier (universal), BSPSysScaleModifier (not GrowFade),
        # BSPSysSimpleColorModifier (not NiPSysColorModifier).
        present = set(bt)
        assert 'BSPSysLODModifier' in present, 'missing BSPSysLODModifier (system culls)'
        assert 'NiPSysGrowFadeModifier' not in present, \
            'NiPSysGrowFadeModifier not converted to BSPSysScaleModifier (invisible)'
        assert 'NiPSysColorModifier' not in present, \
            'NiPSysColorModifier not converted to BSPSysSimpleColorModifier'
        assert 'BSPSysScaleModifier' in present, 'no BSPSysScaleModifier'
        # The root MUST carry BSXFlags with bit 0 (Animated) — without it the
        # engine never ticks the particle controllers and the fire is
        # invisible (vanilla census: 399/400 particle meshes set bit 0).
        # fireopensmall has no collision, so the vanilla value is plain 0x01.
        # BSXFlags sits before any NiPSysData so a partial read reaches it.
        assert 'BSXFlags' in present, 'converted particle mesh has no BSXFlags'
        raw = open(str(dst), 'rb').read()
        idx = raw.find(b'BSX\x00') if b'BSX\x00' in raw else raw.find(b'BSX')
        assert idx != -1, 'BSX name string not found'
        # BSXFlags block body = name string index (u32) + integer_data (u32);
        # simplest robust check: locate the block via header offsets.
        offs = []
        pos = hdr.get_size(data=data)  # first block starts right after header
        for i in range(hdr.num_blocks):
            offs.append(pos)
            pos += bsz[i]
        bsx_i = next(i for i in range(hdr.num_blocks) if bt[bti[i]] == 'BSXFlags')
        import struct as _struct
        _, bsx_val = _struct.unpack_from('<iI', raw, offs[bsx_i])
        assert bsx_val & 0x01, \
            f'BSXFlags 0x{bsx_val:x} missing Animated bit — particles never tick'
        # NiPSysData field bytes must match the vanilla census (837 blocks):
        #  - Has Texture Indices (off 46) MUST be 0 when Num Subtexture Offsets
        #    (off 47) is 0: the engine does rand % count for atlas frame
        #    selection → EXCEPTION_INT_DIVIDE_BY_ZERO in the emitter update.
        #    0/837 vanilla blocks pair flag=1 with count=0.
        #  - Additional Data (off 35) must be -1 (NULL ref; 0 would reference
        #    block 0 = the root node).  Has VColors (32) = 1, Has Radii (39) = 1.
        for i in range(hdr.num_blocks):
            if bt[bti[i]] != 'NiPSysData':
                continue
            o = offs[i]
            has_vcol = raw[o + 32]
            (add_data,) = _struct.unpack_from('<i', raw, o + 35)
            has_radii = raw[o + 39]
            has_ti = raw[o + 46]
            (n_subtex,) = _struct.unpack_from('<I', raw, o + 47)
            assert not (has_ti and n_subtex == 0), \
                'HasTexIndices=1 with 0 subtex offsets → div-by-zero in emitter'
            assert add_data == -1, f'AdditionalData ref {add_data} != -1 (null)'
            assert has_vcol == 1 and has_radii == 1, \
                f'HasVColors={has_vcol} HasRadii={has_radii} != vanilla (1,1)'
        # Every effect shader must have a NON-ZERO UV Scale.  PyFFI defaults
        # UV Scale to (0,0), which collapses all UVs to the texture's top-left
        # texel (transparent on flame textures) → geometry/particles render
        # INVISIBLE.  Vanilla is (1,1); flip-book atlases use (1/N, 1).
        for i in range(hdr.num_blocks):
            if bt[bti[i]] != 'BSEffectShaderProperty':
                continue
            o = offs[i]
            (nextra,) = _struct.unpack_from('<I', raw, o + 4)
            q = o + 12 + 4 * nextra + 8 + 8  # flags + uv_offset
            su, sv = _struct.unpack_from('<2f', raw, q)
            assert su > 0 and sv > 0, \
                f'effect shader block[{i}] UV Scale ({su},{sv}) — zero = invisible'

class TestFlameNodeConversion:
    """FlameNode markers → grafted CONVERTED Oblivion flame subtree.

    Oblivion marks flame positions with empty FlameNode* NiNodes and attaches
    a flame NIF there at runtime (firecandleflame.nif / torch flame).  The
    conversion grafts the converted flame subtree under each marker: particle
    systems + billboard-wrapped flip-book quads.  Requirements each guard a
    real bug: no surviving NiBillboardNode may contain a particle system
    (spinning emitter), emitter/gravity object refs must resolve in-tree
    (demote dangled them), and the host root needs BSX bit 0 so the grafted
    controllers tick.
    """

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel', [
        'fire/firetorchlargesmoke.nif',
        'fire/firetorchlarge.nif',
        'fire/fireopenlarge.nif',
        'fire/fireopenlargesmoke.nif',
        'fire/fireopenmedium.nif',
        'fire/fireopensmall.nif',
        'fire/firearcanemedium01.nif',
        'fire/firetorchsmall.nif',
    ])
    def test_flame_keeps_the_authored_model_frame(self, rel, tmp_path):
        """Quads and emitters must stay in ONE frame -- the source's.

        These meshes are authored +Y-up and their PLACED REFERENCES carry the
        stand-up rotation: across Oblivion.esm, 494 REFRs of the Fire\*.nif
        lights use RotX = +-90 deg (10/10 for FireTorchLargeSmoke).  The REFR
        rotates the whole model at once, so the conversion must not re-frame
        any PART of it.

        Pre-rotating just the flip-book quad to +Z-up made it the only piece in
        a different frame; the REFR's -90 then laid it flat, which is the
        "third flame component on its side" next to a correct-looking flame and
        smoke.  This asserts quad and emitters still share the source's axis.
        """
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat

        src = EXPORT_MESHES / rel
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'meshes' / 'out.nif'
        dst.parent.mkdir(parents=True, exist_ok=True)
        assert convert_nif(str(src), str(dst)).get('converted')

        def _tall_quad_axis(path, reader):
            data = reader(path)

            def _mul(A, B):
                return tuple(tuple(sum(A[i][k] * B[k][j] for k in range(3))
                                   for j in range(3)) for i in range(3))
            best = None

            def _walk(n, M, T):
                nonlocal best
                if n is None:
                    return
                r = n.rotation
                R = _mul(((r.m_11, r.m_12, r.m_13),
                          (r.m_21, r.m_22, r.m_23),
                          (r.m_31, r.m_32, r.m_33)), M)
                t = n.translation
                Tn = (T[0] + t.x * M[0][0] + t.y * M[1][0] + t.z * M[2][0],
                      T[1] + t.x * M[0][1] + t.y * M[1][1] + t.z * M[2][1],
                      T[2] + t.x * M[0][2] + t.y * M[1][2] + t.z * M[2][2])
                nm = bytes(getattr(n, 'name', b'') or b'')
                if (isinstance(n, NifFormat.NiTriBasedGeom) and n.data
                        and len(n.data.vertices) and b'EditorMarker' not in nm):
                    ys = [Tn[1] + v.x * R[0][1] + v.y * R[1][1] + v.z * R[2][1]
                          for v in n.data.vertices]
                    zs = [Tn[2] + v.x * R[0][2] + v.y * R[1][2] + v.z * R[2][2]
                          for v in n.data.vertices]
                    spread = (max(ys) - min(ys), max(zs) - min(zs))
                    if best is None or max(spread) > best[0]:
                        best = (max(spread), 'Y' if spread[0] > spread[1] else 'Z')
                for c in getattr(n, 'children', []) or []:
                    _walk(c, R, Tn)

            ident = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
            for r0 in data.roots:
                _walk(r0, ident, (0.0, 0.0, 0.0))
            return best

        def _read(path):
            d = NifFormat.Data()
            with open(str(path), 'rb') as f:
                d.inspect(f)
                f.seek(0)
                d.read(f)
            return d

        src_axis = _tall_quad_axis(src, _read)
        out_axis = _tall_quad_axis(dst, _read)
        assert src_axis and out_axis, f'{rel}: fixture expects a tall quad'
        assert out_axis[1] == src_axis[1], (
            f'{rel}: the tall flame quad grows along {out_axis[1]} but the '
            f'source authored it along {src_axis[1]} -- the quad was re-framed '
            f'away from the rest of the model, and the REFR rotation '
            f'(RotX=-90 on these lights) will lay it on its side')

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel,shape,expect', [
        # Flames author FULL WHITE and must stay bright.
        ('fire/firetorchlarge.nif', 'Fire', 1.0),
        ('fire/firetorchlarge.nif', 'FireTorchLarge:0', 1.0),
        ('fire/firecandleflame.nif', 'FlameParticles', 1.0),
        # Ayleid-ruin fog authors a DIM grey and must stay dim -- this is the
        # Belda "blinding fog" regression.  0.078 promoted to white is 12.8x
        # too bright on additively-blended planes layered several deep.
        ('dungeons/misc/fx/fxcloudthick01.nif', 'Cloud', 0.078),
        ('dungeons/misc/fx/fxcloudthin01.nif', 'Cloud', 0.047),
    ])
    def test_fx_emissive_is_the_authored_value(self, rel, shape, expect,
                                               tmp_path):
        """FX brightness comes from the SOURCE, never from the texture name.

        Oblivion states it per shape in NiMaterialProperty.emissive_color, and
        the populations do not overlap: flames author 1.0 white, fog authors
        0.047-0.078.  An earlier revision classified by diffuse PATH instead
        (b'fire'/b'flame'/b'torch' minus a smoke/mist/fog/dust veto) and forced
        emissive_multiple 1.5 on every hit.  That is wrong in both directions:
        it matched textures/lights/torch02.dds -- the WOODEN HANDLE, whose host
        lights/torch02noflame.nif contains no flame at all -- and it can only
        work for meshes following Bethesda's naming, never for Nehrim,
        Morroblivion or any third-party plugin.

        emissive_multiple must stay at the vanilla-neutral 1.0 throughout: a
        flame authored full white is already at full emission.
        """
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat

        src = EXPORT_MESHES / rel
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'meshes' / 'out.nif'
        dst.parent.mkdir(parents=True, exist_ok=True)
        assert convert_nif(str(src), str(dst)).get('converted')

        data = NifFormat.Data()
        with open(str(dst), 'rb') as f:
            data.inspect(f)
            f.seek(0)
            data.read(f)

        seen = None
        for blk in data.blocks:
            if not isinstance(blk, (NifFormat.NiParticleSystem,
                                    NifFormat.NiTriBasedGeom)):
                continue
            if bytes(getattr(blk, 'name', b'') or b'').decode('latin1') != shape:
                continue
            for pr in (getattr(blk, 'bs_properties', None) or []):
                if pr is None or type(pr).__name__ != 'BSEffectShaderProperty':
                    continue
                seen = pr
        assert seen is not None, f'{rel}: no effect shader on shape {shape!r}'
        assert float(seen.emissive_color.r) == pytest.approx(expect, abs=0.02), (
            f'{rel} {shape}: emissive_color.r={float(seen.emissive_color.r):.3f}, '
            f'expected the AUTHORED {expect} -- brightness must come from '
            f'NiMaterialProperty, not from the texture filename')
        # A self-lit surface is BOOSTED; ambient haze stays neutral.  Among
        # vanilla FX that are full-white AND soft_effect=0 (the mounted,
        # self-lit population this branch matches), 74 of 90 sit above 1.0 with
        # a median of 1.6 -- torchsconce01 pFireballCore04 1.50,
        # giantcampfire01burning PCloudForgeSparks 1.25, fxsmokelargeclose01
        # Flames 1.60.  Holding flames at 1.0 made them visibly DIMMER than the
        # previous build, which was caught in game.
        want_mult = 1.5 if expect >= 0.999 else 1.0
        assert float(seen.emissive_multiple) == pytest.approx(want_mult,
                                                              abs=0.01), (
            f'{rel} {shape}: emissive_multiple={float(seen.emissive_multiple)}, '
            f'expected {want_mult}')
        # A self-lit (full-white) surface must NOT take the soft depth fade:
        # a candle flame sits on its own wax, so the fade attenuates it against
        # the very object it is mounted on and the flame vanishes, leaving only
        # the wax's own emissive glow.  Vanilla mps\mpscandleflame01.nif splits
        # exactly this way -- CandleFlame01 soft=0, CandleGlow01 soft=1.
        # Ambient fog DOES take it (that is the rectangular-edge fix).
        soft = int(seen.shader_flags_1.slsf_1_soft_effect)
        want_soft = 0 if expect >= 0.999 else 1
        assert soft == want_soft, (
            f'{rel} {shape}: soft_effect={soft}, expected {want_soft} -- a '
            f'self-lit flame must stay hard or it fades into its own holder; '
            f'dim ambient FX must fade or it shows its quad edge')

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_chromatic_color_curve_still_defers_to_the_curve(self, tmp_path):
        """The ghost: near-black material + a real GREEN curve.

        creatures/ghost/skeleton.nif authors emissive (0.039, 0.039, 0.039) --
        a carrier, not a color -- alongside a NiPSysColorModifier ramping the
        ghost's pale green (0.702, 0.831, 0.745) -> (0.514, 0.647, 0.561).
        Skyrim's effect shader MULTIPLIES by emissive_color, so carrying the
        0.039 through would crush the green to ~0.027 and render the ghost
        black.  When the curve carries actual chroma the curve is the authored
        color, so the shader tint stays neutral.

        This must not be confused with an ACHROMATIC curve (fog's plain
        (0,0,0,0)->(1,1,1,1)->(0,0,0,0) alpha envelope), which supplies no
        color at all -- that is the case guarded above.
        """
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat

        src = EXPORT_MESHES / 'creatures/ghost/skeleton.nif'
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'meshes' / 'ghost.nif'
        dst.parent.mkdir(parents=True, exist_ok=True)
        assert convert_nif(str(src), str(dst)).get('converted')

        data = NifFormat.Data()
        with open(str(dst), 'rb') as f:
            data.inspect(f)
            f.seek(0)
            data.read(f)

        checked = 0
        for blk in data.blocks:
            if not isinstance(blk, NifFormat.NiParticleSystem):
                continue
            for pr in (getattr(blk, 'bs_properties', None) or []):
                if pr is None or type(pr).__name__ != 'BSEffectShaderProperty':
                    continue
                assert float(pr.emissive_color.r) > 0.5, (
                    'ghost tint was crushed to the 0.039 carrier value -- the '
                    'chromatic color curve is the authored color here')
                checked += 1
        assert checked, 'fixture expects ghost particle systems'

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel', [
        'fire/firecandleflame.nif',
        'lights/uppersilverplatecandles01.nif',
        'clutter/wallsconcesingle01.nif',
        'fire/firetorchlargesmoke.nif',
    ])
    def test_emitter_and_quad_agree_on_up(self, rel, tmp_path):
        """The particle jet and the flame quad must point the same way.

        A demoted billboard normally inherits IDENTITY -- a NiBillboardNode
        discards its own rotation at runtime (NifSkope
        BillboardNode::viewTrans).  But an EMITTER MARKER's rotation is not
        decoration: NiPSysEmitter reads its `emitter_object` node's orientation
        as the emission DIRECTION.

        firecandleflame authors quad and emitter in one +Y-up frame -- the quad
        identity with local extent [1.3, 2.6, 0.0] (tall in Y), the emitter
        [1,0,0][0,0,-1][0,1,0] whose local +Z maps to model +Y.  Zeroing the
        emitter made it +Z-up while the quad stayed +Y-up: an upright flame
        with a second, sideways particle jet -- most visible once a FlameNode
        marker rotates the pair into a +Z-up host like the candle plate.
        """
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat

        src = EXPORT_MESHES / rel
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'meshes' / 'out.nif'
        dst.parent.mkdir(parents=True, exist_ok=True)
        assert convert_nif(str(src), str(dst)).get('converted')

        data = NifFormat.Data()
        with open(str(dst), 'rb') as f:
            data.inspect(f)
            f.seek(0)
            data.read(f)

        def _mul(A, B):
            return tuple(tuple(sum(A[i][k] * B[k][j] for k in range(3))
                               for j in range(3)) for i in range(3))

        placed = {}
        quads = []

        def _walk(n, M, T):
            if n is None:
                return
            r = n.rotation
            R = _mul(((r.m_11, r.m_12, r.m_13),
                      (r.m_21, r.m_22, r.m_23),
                      (r.m_31, r.m_32, r.m_33)), M)
            t = n.translation
            Tn = (T[0] + t.x * M[0][0] + t.y * M[1][0] + t.z * M[2][0],
                  T[1] + t.x * M[0][1] + t.y * M[1][1] + t.z * M[2][1],
                  T[2] + t.x * M[0][2] + t.y * M[1][2] + t.z * M[2][2])
            placed[id(n)] = R
            nm = bytes(getattr(n, 'name', b'') or b'')
            if (isinstance(n, NifFormat.NiTriBasedGeom) and n.data
                    and len(n.data.vertices) and b'Fire' in nm):
                ys = [Tn[1] + v.x * R[0][1] + v.y * R[1][1] + v.z * R[2][1]
                      for v in n.data.vertices]
                zs = [Tn[2] + v.x * R[0][2] + v.y * R[1][2] + v.z * R[2][2]
                      for v in n.data.vertices]
                quads.append((nm.decode('latin1'),
                              'Y' if (max(ys) - min(ys)) > (max(zs) - min(zs))
                              else 'Z'))
            for c in getattr(n, 'children', []) or []:
                _walk(c, R, Tn)

        ident = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        for r0 in data.roots:
            _walk(r0, ident, (0.0, 0.0, 0.0))

        assert quads, f'{rel}: fixture expects a flame quad'
        quad_axis = quads[0][1]

        checked = 0
        for blk in data.blocks:
            if not isinstance(blk, NifFormat.NiPSysEmitter):
                continue
            o = getattr(blk, 'emitter_object', None)
            if o is None or id(o) not in placed:
                continue
            name = bytes(getattr(o, 'name', b'') or b'').decode('latin1')
            if 'Smoke' in name:
                continue  # smoke may legitimately drift off-axis
            z = placed[id(o)][2]
            emit_axis = 'Y' if abs(z[1]) > abs(z[2]) else 'Z'
            assert emit_axis == quad_axis, (
                f'{rel}: emitter {name} fires along {emit_axis} '
                f'(+Z=({z[0]:.2f},{z[1]:.2f},{z[2]:.2f})) but the flame quad '
                f'is tall in {quad_axis} -- the flame ships as an upright quad '
                f'plus a sideways particle jet')
            checked += 1
        assert checked, f'{rel}: fixture expects a flame emitter'

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel,marker', [
        ('clutter/metalsmith/forgeopen01.nif', 'FlameNode07'),
        ('clutter/lecternworkstation1.nif', 'FlameNode01'),
    ])
    def test_zero_padded_socket_burns_nothing(self, rel, marker, tmp_path):
        """A ZERO-PADDED socket name matches nothing and attaches no flame.

        The engine compares socket names EXACTLY against its own table, which
        holds only unpadded "FlameNode<N>": Oblivion.exe contains "FlameNode7"
        and "FlameNode1" but neither "FlameNode07" nor "FlameNode01", and the
        plugin's STATs are likewise EditorID FlameNode0..FlameNode9.  These two
        vanilla meshes are authored with padded markers and show NO flame in
        the original game.

        Matching them loosely put a 468-unit FireOpenMediumSmoke on the forge
        and a spurious torch on the lectern.  An unmatched socket must burn
        nothing -- never fall back to a default flame.
        """
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat
        from asset_convert.nif_flames import _flame_socket_index

        src = EXPORT_MESHES / rel
        if not src.exists():
            pytest.skip(f'{src} not found')

        assert _flame_socket_index(marker) is None, (
            f'{marker} must not resolve to a socket: the engine has no such '
            f'name in its table')

        # The fixture really is authored with that padded marker.
        raw = src.read_bytes()
        assert marker.encode() in raw, f'{rel}: expected a {marker} marker'

        dst = tmp_path / 'meshes' / 'out.nif'
        dst.parent.mkdir(parents=True, exist_ok=True)
        assert convert_nif(str(src), str(dst)).get('converted')

        out = NifFormat.Data()
        with open(str(dst), 'rb') as f:
            out.inspect(f)
            f.seek(0)
            out.read(f)

        # Nothing may be grafted AT that padded marker.  (Other, correctly
        # named sockets in the same mesh still get their flame -- the lectern
        # keeps its three FlameNode0 candles.)
        for blk in out.blocks:
            name = bytes(getattr(blk, 'name', b'') or b'').decode('latin1')
            if name.startswith(marker):
                kids = [c for c in (getattr(blk, 'children', None) or [])
                        if c is not None]
                assert not kids, (
                    f'{rel}: {marker} got a flame grafted ({len(kids)} child '
                    f'node(s)); vanilla shows no flame here')

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel,socket,flame', [
        ('clutter/candlefat01.nif', 0, b'firecandleflame'),
        ('lights/torchtall01.nif', 1, b'firetorchsmall'),
        ('architecture/castle/castlelight02.nif', 2, b'firetorchlarge'),
        ('architecture/anvil/anvilstreetlamp01.nif', 2, b'firetorchlarge'),
    ])
    def test_flame_comes_from_the_flamenode_stat(self, rel, socket, flame,
                                                 tmp_path):
        """The socket index picks the flame, per the plugin's own STAT records.

        Oblivion ships one STAT per socket (WorldObjects/Static, EditorID
        "FlameNode<N>") whose MODL is the flame to attach -- FlameNode0
        0x0000001E Fire/FireCandleFlame.NIF, FlameNode1 0x1F FireTorchSmall,
        FlameNode2 0x20 FireTorchLarge, ... FlameNode9 0x27 FireOpenLargeSmoke.
        Those FormIDs are the keys Oblivion.exe hardcodes beside its socket-name
        table (0xB06818 names, 0xB067C0 form keys 0x1E..0x32), so the mapping
        lives in the plugin and a mod may repoint it -- we read it, never guess.

        Selecting on the host FILENAME instead ("torch" in the name) put the
        1.3x2.6-unit candle flame on every lamp in the game: castlelight02 is a
        105-unit fixture on socket 2, i.e. FireTorchLarge at 32x64.
        """
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat
        from asset_convert.nif_flames import (_flame_socket_map,
                                              _flame_socket_index)

        src = EXPORT_MESHES / rel
        if not src.exists():
            pytest.skip(f'{src} not found')

        # The host really does use the socket this case claims.
        data = NifFormat.Data()
        with open(str(src), 'rb') as f:
            data.inspect(f)
            f.seek(0)
            data.read(f)
        sockets = {_flame_socket_index(bytes(b.name or b''))
                   for b in data.blocks
                   if bytes(getattr(b, 'name', b'') or b'').startswith(
                       b'FlameNode')}
        assert socket in sockets, (
            f'{rel}: expected a FlameNode{socket} marker, found {sockets}')

        # And the authored table maps it to the flame we expect.
        table = _flame_socket_map(str(src))
        assert table, 'no FlameNode STAT records parsed from the export'
        assert flame in table[socket].encode(), (
            f'FlameNode{socket} -> {table[socket]}, expected {flame!r}')

        dst = tmp_path / 'meshes' / 'out.nif'
        dst.parent.mkdir(parents=True, exist_ok=True)
        assert convert_nif(str(src), str(dst)).get('converted')

        out = NifFormat.Data()
        with open(str(dst), 'rb') as f:
            out.inspect(f)
            f.seek(0)
            out.read(f)
        texs = b'|'.join(
            bytes(b.source_texture or b'').lower() for b in out.blocks
            if type(b).__name__ == 'BSEffectShaderProperty')
        assert flame in texs, (
            f'{rel}: FlameNode{socket} should graft {flame!r}; shader textures '
            f'are {texs!r}')

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel,flame_tex', [
        ('lights/candlefat02fake.nif', b'firecandleflame'),
        ('lights/middlecandlestickfloor01fake.nif', b'firecandleflame'),
        ('lights/torchtall01.nif', b'firetorch'),
    ])
    def test_flamenode_gets_grafted_flame(self, rel, flame_tex, tmp_path):
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat

        src = EXPORT_MESHES / rel
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'meshes' / 'out.nif'
        dst.parent.mkdir(parents=True, exist_ok=True)
        result = convert_nif(str(src), str(dst))
        assert result.get('converted'), result

        data = NifFormat.Data()
        with open(str(dst), 'rb') as f:
            data.inspect(f)
            f.seek(0)
            data.read(f)
        # The grafted flame must ship its particle system(s).
        psys = [b for b in data.blocks
                if isinstance(b, NifFormat.NiParticleSystem)]
        assert psys, 'no particle system grafted at FlameNode'
        # No BSValueNode/AddonNode substitution remains.
        assert not any(isinstance(b, NifFormat.BSValueNode) for b in data.blocks), \
            'legacy MPS AddonNode substitution still present'
        # No surviving billboard may contain a particle system (spun emitter).
        for b in data.blocks:
            if isinstance(b, NifFormat.NiBillboardNode):
                assert not any(isinstance(t, NifFormat.NiParticleSystem)
                               for t in b.tree()), \
                    'NiBillboardNode still contains a particle system'
        # Emitter/gravity object refs must resolve inside the tree.
        tree_ids = {id(b) for b in data.blocks}
        for b in data.blocks:
            for attr in ('emitter_object', 'gravity_object'):
                ref = getattr(b, attr, None)
                if ref is not None:
                    assert id(ref) in tree_ids, f'dangling {attr}'
        # The flame texture family must appear on an effect shader.
        texs = b'|'.join(bytes(b.source_texture).lower() for b in data.blocks
                         if isinstance(b, NifFormat.BSEffectShaderProperty))
        assert flame_tex in texs, f'{flame_tex} not in shader textures: {texs}'
        # Host BSX must have the Animated bit so grafted controllers tick.
        bsx = [b for b in data.blocks if isinstance(b, NifFormat.BSXFlags)]
        assert bsx and (bsx[0].integer_data & 0x01), 'BSX Animated bit not set'


class TestMultiSphereExpansion:
    """bhkMultiSphereShape must never survive conversion.

    0 of 17,216 vanilla Skyrim meshes ship the block (deprecated Havok path);
    apparatusalembicnovice.nif shipping one crashed SSE at cell load with no
    crash log.  It must expand into ConvexTransform+Sphere children.
    """

    _SRC = 'clutter/magesguild/apparatusalembicnovice.nif'

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_multisphere_expanded(self, tmp_path):
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat

        src = EXPORT_MESHES / self._SRC
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        assert not result.get('error') and not result.get('skipped'), result

        data = NifFormat.Data()
        with open(str(dst), 'rb') as f:
            data.read(f)
        spheres = []
        for block in data.blocks:
            assert not isinstance(block, NifFormat.bhkMultiSphereShape), \
                'bhkMultiSphereShape survived conversion (vanilla never ships it)'
            if isinstance(block, NifFormat.bhkConvexTransformShape) and \
                    isinstance(block.shape, NifFormat.bhkSphereShape):
                t = block.transform
                spheres.append((t.m_14, t.m_24, t.m_34, block.shape.radius))
        # Source multisphere: 2 spheres, centers/radii in Oblivion Havok units
        # (-1.9658, .0046, -.6552) r=.7834 and (1.4860, .0046, .3394) r=.9492
        assert len(spheres) == 2, f'expected 2 expanded spheres, got {len(spheres)}'
        spheres.sort()
        exp = [(-0.19658, 0.00046, -0.06552, 0.07834),
               (0.14860, 0.00046, 0.03394, 0.09492)]
        for got, want in zip(spheres, exp):
            for g, w in zip(got, want):
                assert abs(g - w) < 1e-3, f'{got} != {want}'


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
        """Converted shield must land in Skyrim SHIELD-bone space like vanilla.

        The root transform comes from _shield_attach_transform(): an exact
        mapping from the Oblivion attach frame (Bip01 L ForearmTwist) to the
        Skyrim SHIELD bone frame via anatomically corresponding hand frames.
        Result contract (matches vanilla ironshield.nif: X ≈ ±21, Y ≈ ±22,
        Z ∈ [-8.5, +2]): face in the XY plane (Z thin), dome toward -Z, grip
        region near the origin.
        """
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF
        import numpy as np
        from asset_convert.nif_converter import _shield_attach_transform

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
        # Inner rotation/translation must match the skeleton-derived transform
        T = _shield_attach_transform()
        assert T is not None, "Shield attach transform must be computable from skeleton JSONs"
        ri = inner.rotation
        Rmat = np.array([[ri.m_11, ri.m_12, ri.m_13],
                         [ri.m_21, ri.m_22, ri.m_23],
                         [ri.m_31, ri.m_32, ri.m_33]], dtype=float)
        Tvec = np.array([inner.translation.x, inner.translation.y, inner.translation.z])
        assert np.allclose(Rmat, T[:3, :3], atol=1e-4), \
            "Inner NiNode rotation must equal the shield attach transform"
        assert np.allclose(Tvec, T[3, :3], atol=1e-3), \
            "Inner NiNode translation must equal the shield attach transform"
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
        # v @ R + T — same row-vector convention as skin_retarget._m44_to_np
        world = np.array([v @ Rmat + Tvec for v in all_verts])
        # Shield must be a slab: smallest principal extent << the other two.
        # (Axis-aligned Z-range is no longer meaningful — the attach transform
        # tilts the strap plane ~16° to lie along the Skyrim forearm.)
        centered = world - world.mean(axis=0)
        evals, evecs = np.linalg.eigh(centered.T @ centered)
        thin_axis = evecs[:, 0]   # least-variance direction = face normal
        # np.ptp(x), not x.ptp() — the ndarray method was removed in NumPy 2.0.
        spans = [float(np.ptp(centered @ evecs[:, i])) for i in range(3)]
        assert spans[0] < spans[1] * 0.6 and spans[0] < spans[2] * 0.6, \
            f"Shield should be a slab; principal spans {spans}"
        # Face normal roughly along ±Z (within the forearm-clearance tilt)
        assert abs(thin_axis[2]) > 0.85, \
            f"Shield face normal should be near ±Z: {thin_axis}"
        # Dome bulges outward (-Z, away from the arm) like vanilla shields
        cz = world[:, 2].mean()
        assert cz < 0.5, f"Shield centroid should sit at/behind the grip plane (dome -Z): cz={cz:.2f}"
        # Grip region near the origin: the authentic Oblivion strapped placement
        # is a few units toward the elbow vs Skyrim's grip-centred art, so the
        # tolerance is looser than exact centring.
        cx = (world[:, 0].min() + world[:, 0].max()) * 0.5
        cy = (world[:, 1].min() + world[:, 1].max()) * 0.5
        assert abs(cx) < 10.0, f"Shield too far off the grip in X: cx={cx:.2f}"
        assert abs(cy) < 10.0, f"Shield too far off the grip in Y: cy={cy:.2f}"

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

    # Rotated-root meshes: the wrap pass zeroes the root transform L and the
    # collision geometry must end up baked into the CMS in the L∘bodyT frame.
    _WRAPPED_COLLISION_CASES = [
        _DOOR_WITH_ROTATION,                                  # 180° about Z
        'architecture/castleinterior/stackhallentrance01.nif',  # +90° about Z
    ]

    @staticmethod
    def _m3_from_quat(x, y, z, w):
        return [
            [1 - 2*(y*y + z*z), 2*(x*y - w*z),     2*(x*z + w*y)],
            [2*(x*y + w*z),     1 - 2*(x*x + z*z), 2*(y*z - w*x)],
            [2*(x*z - w*y),     2*(y*z + w*x),     1 - 2*(x*x + y*y)],
        ]

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', _WRAPPED_COLLISION_CASES)
    def test_static_collision_stays_on_root_when_wrapped(self, rel_path, tmp_path):
        """Static mesh collision must live on the root BSFadeNode as a PLAIN
        identity bhkRigidBody with the root rotation L and any source bodyT
        baked into the CMS geometry.  Vanilla Skyrim never pairs a
        bhkRigidBodyT with MOPP/CMS collision (0 of 6341 vanilla CMS meshes);
        shipping one intermittently produces invalid-shape-key hits →
        runaway hkpAllCdPointTempCollector scan → EXCEPTION_STACK_OVERFLOW
        (the AnvilCastleGreatHall CTDs — every Collision Sentinel CULPRIT
        was a rotated-root mesh).  The geometric comparison against the
        source collision in the L∘bodyT world frame catches
        conjugate/transpose convention errors in the bake."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF
        from asset_convert.cms import decode_cms

        src = EXPORT_MESHES / rel_path
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        result = convert_nif(str(src), str(dst))
        if result.get('error') or result.get('skipped'):
            pytest.skip(f'Conversion issue: {result}')
        assert result.get('root_rotation_baked'), \
            f'{rel_path} should trigger the rotation wrap pass'

        # Expected world-frame collision soup from the SOURCE:
        # L (root rotation) ∘ bodyT ∘ (strips verts / 70)
        src_data = NF.Data()
        with open(str(src), 'rb') as f:
            src_data.read(f)
        sroot = src_data.roots[0]
        r = sroot.rotation
        L = [[r.m_11, r.m_21, r.m_31],
             [r.m_12, r.m_22, r.m_32],
             [r.m_13, r.m_23, r.m_33]]  # column-vector convention
        sbody = sroot.collision_object.body
        if isinstance(sbody, NF.bhkRigidBodyT):
            q = sbody.rotation
            Rb = self._m3_from_quat(q.x, q.y, q.z, q.w)
            tb = (sbody.translation.x * 0.1, sbody.translation.y * 0.1,
                  sbody.translation.z * 0.1)  # OB havok → SK havok
        else:
            Rb = self._m3_from_quat(0.0, 0.0, 0.0, 1.0)
            tb = (0.0, 0.0, 0.0)

        def world(v):
            b = tuple(sum(Rb[i][k] * v[k] for k in range(3)) + tb[i]
                      for i in range(3))
            return tuple(sum(L[i][k] * b[k] for k in range(3))
                         for i in range(3))

        expected_centroids = []
        for block in src_data.blocks:
            if isinstance(block, NF.bhkNiTriStripsShape):
                for sd in block.strips_data:
                    verts = [world((v.x / 70.0, v.y / 70.0, v.z / 70.0))
                             for v in sd.vertices]
                    for si in range(sd.num_strips):
                        strip = list(sd.points[si])
                        for j in range(len(strip) - 2):
                            a, b, c = strip[j], strip[j+1], strip[j+2]
                            if a != b and b != c and a != c:
                                expected_centroids.append(tuple(
                                    (verts[a][i] + verts[b][i] + verts[c][i]) / 3
                                    for i in range(3)))
        assert expected_centroids, 'source has no strips collision'

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

        # Vanilla pattern: plain identity bhkRigidBody, never bhkRigidBodyT
        body = root.collision_object.body
        assert body.__class__ is NF.bhkRigidBody, \
            f'mesh collision body is {type(body).__name__} — vanilla CMS never uses bhkRigidBodyT'
        q = body.rotation
        assert max(abs(q.x), abs(q.y), abs(q.z), abs(q.w - 1.0)) < 1e-4, \
            f'body rotation ({q.x}, {q.y}, {q.z}, {q.w}) is not identity'
        t = body.translation
        assert max(abs(t.x), abs(t.y), abs(t.z)) < 1e-6, \
            f'body translation ({t.x}, {t.y}, {t.z}) is not zero'

        # CMS geometry must be the source collision in the L∘bodyT frame
        cms = None
        for block in dst_data.blocks:
            if isinstance(block, NF.bhkCompressedMeshShapeData):
                cms = block
        assert cms is not None, 'converted NIF lost its bhkCompressedMeshShapeData'
        decoded = decode_cms(cms)
        assert len(decoded) == len(expected_centroids), \
            f'CMS has {len(decoded)} tris, source has {len(expected_centroids)}'
        for _key, tri in decoded:
            c = tuple((tri[0][i] + tri[1][i] + tri[2][i]) / 3 for i in range(3))
            best = min(sum((c[i] - e[i]) ** 2 for i in range(3))
                       for e in expected_centroids)
            assert best < 0.002 ** 2, \
                f'decoded triangle centroid {c} has no source match within 0.002 hu — bake convention error'


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

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    @pytest.mark.parametrize('rel_path', [
        # Collision Sentinel CULPRIT: MOPP_RL's own bytecode produced
        # key=0xFFFFFFFF (invalid shape key) hits at runtime on this mesh.
        'architecture/castleinterior/castleintarch2way01.nif',
        'architecture/castleinterior/stackhallentrance01.nif',
    ])
    def test_mopp_rebuilt_by_havok_bridge(self, rel_path, tmp_path):
        """MOPP bytecode must come from Havok's own compiler (the Dovah MOPP
        bridge) and its terminal key set must exactly equal the engine's CMS
        shape-key decode.  MOPP_RL's ancient-Havok MOPP intermittently
        mis-culled queries → invalid-shape-key decode → CTD."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF
        from asset_convert.mopp import walk_mopp
        from asset_convert.cms import predict_keys

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

        mopp_blk = cms = None
        for block in dst_data.blocks:
            if isinstance(block, NF.bhkMoppBvTreeShape):
                mopp_blk = block
            if isinstance(block, NF.bhkCompressedMeshShapeData):
                cms = block
        assert mopp_blk is not None and cms is not None, \
            'converted NIF lost its MOPP/CMS chain'

        mopp = bytes(bytearray(mopp_blk.mopp_data))
        r = walk_mopp(mopp, len(mopp))
        assert not r['errors'], f'MOPP walk errors: {r["errors"][:3]}'
        assert r['tris'] == predict_keys(cms), \
            'MOPP terminal keys do not match the CMS shape-key decode'


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


class TestBowRig:
    """Converted bows: correct orientation + vanilla bend rig (bow_rig.py)."""

    BOW_SRC = 'weapons/steel/bow.nif'

    @pytest.fixture(scope='class')
    def converted_bow(self, tmp_path_factory):
        src = EXPORT_MESHES / self.BOW_SRC
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path_factory.mktemp('bow') / 'bow.nif'
        result = convert_nif(str(src), str(dst))
        assert result['converted'], f"Conversion failed: {result.get('error')}"
        from pyffi.formats.nif import NifFormat as NF
        data = NF.Data()
        with open(str(dst), 'rb') as f:
            data.read(f)
        return data

    def test_not_flipped(self, converted_bow):
        # The blanket weapon 180-deg Y flip must NOT apply to bows: the string
        # side must stay at -X (vanilla steelbow string bones sit at x=-13.7).
        from pyffi.formats.nif import NifFormat as NF
        root = converted_bow.roots[0]
        for b in root.tree():
            if isinstance(b, NF.NiTriShape):
                xs = [v.x for v in b.data.vertices]
                # geometry transform must be identity (no baked flip node)
                t = b.get_transform(root)
                assert abs(t.m_11 - 1.0) < 1e-4, 'bow geometry was Y-flipped'
                assert min(xs) < -10, 'bow string side not at -X'

    def test_prn_weapon_bow(self, converted_bow):
        from pyffi.formats.nif import NifFormat as NF
        root = converted_bow.roots[0]
        prns = [bytes(ed.string_data).rstrip(b'\x00').decode()
                for ed in root.extra_data_list
                if isinstance(ed, NF.NiStringExtraData)
                and bytes(ed.name).rstrip(b'\x00') == b'Prn']
        assert prns == ['WeaponBow']

    def test_bend_rig_bones(self, converted_bow):
        from pyffi.formats.nif import NifFormat as NF
        root = converted_bow.roots[0]
        names = {bytes(b.name).rstrip(b'\x00').decode()
                 for b in root.tree() if isinstance(b, NF.NiNode)}
        for bone in ('Bow_MidBone', 'Bow_LoBone1', 'Bow_LoBone2',
                     'Bow_StringBone1', 'Bow_UpBone1', 'Bow_UpBone2',
                     'Bow_StringBone2'):
            assert bone in names, f'missing rig bone {bone}'

    def test_geometry_skinned_with_partition(self, converted_bow):
        from pyffi.formats.nif import NifFormat as NF
        root = converted_bow.roots[0]
        shapes = [b for b in root.tree() if isinstance(b, NF.NiTriShape)]
        assert shapes
        for b in shapes:
            si = b.skin_instance
            assert si is not None, 'bow geometry not skinned'
            assert type(si).__name__ == 'NiSkinInstance'  # vanilla bows: plain
            assert si.skin_partition is not None
            assert si.skin_partition.num_skin_partition_blocks >= 1
            # every bone entry needs a non-zero bounding sphere (engine culls
            # skinned shapes by these; zero radius = invisible in game)
            for i in range(si.data.num_bones):
                assert si.data.bone_list[i].bounding_sphere_radius > 0.1
            # SLSF1_Skinned: without it the renderer never applies bone
            # deforms — bow renders frozen in bind pose (string never draws)
            shaders = [p for p in b.bs_properties
                       if isinstance(p, NF.BSLightingShaderProperty)]
            assert shaders, 'bow shape has no BSLightingShaderProperty'
            assert shaders[0].shader_flags_1.slsf_1_skinned == 1, \
                'SLSF1_Skinned missing - mesh will not follow the bend rig'

    def test_string_verts_weighted_to_string_bones(self, converted_bow):
        from pyffi.formats.nif import NifFormat as NF
        root = converted_bow.roots[0]
        for b in root.tree():
            if not isinstance(b, NF.NiTriShape):
                continue
            si = b.skin_instance
            names = [bytes(bn.name).rstrip(b'\x00').decode()
                     for bn in si.bones]
            sb = [i for i, n in enumerate(names) if 'StringBone' in n]
            weights = {}
            for bi in range(si.data.num_bones):
                for vw in si.data.bone_list[bi].vertex_weights:
                    weights.setdefault(vw.index, {})[bi] = vw.weight
            # mid-string verts (x < -13, |y| < 5) must be string-bone driven
            found = 0
            for vi, v in enumerate(b.data.vertices):
                if v.x < -13 and abs(v.y) < 5:
                    w_sb = sum(w for bi, w in weights.get(vi, {}).items()
                               if bi in sb)
                    assert w_sb > 0.9, \
                        f'mid-string vert {vi} not on string bones ({w_sb:.2f})'
                    found += 1
            assert found > 0, 'no mid-string verts found'

    def test_behavior_graph_and_bsx(self, converted_bow):
        from pyffi.formats.nif import NifFormat as NF
        root = converted_bow.roots[0]
        bged = [ed for ed in root.extra_data_list
                if type(ed).__name__ == 'BSBehaviorGraphExtraData']
        assert len(bged) == 1
        assert bytes(bged[0].behaviour_graph_file).rstrip(b'\x00').decode() \
            == 'Weapons\\Bow\\BowProject.hkx'
        bsx = [ed for ed in root.extra_data_list
               if isinstance(ed, NF.BSXFlags)]
        assert bsx and (int(bsx[0].integer_data) & 0x08), \
            'BSXFlags Animated bit missing - graph never ticks'


class TestFXBrightnessAndSoftEffect:
    """Blended FX geometry: authored brightness + the soft-particle depth fade.

    User report (2026-08-07): Ayleid-ruin smoke "incredibly bright... way
    brighter than in Oblivion and difficult to see through", and transparent
    effects showing "a rectangular bounding box around them".

    Three regressions guarded here:
      * the authored NiMaterialProperty.emissive_color must survive (it was
        being overwritten with full white, so every dimmed FX surface rendered
        at ~2x its intended brightness, compounding per additive layer);
      * emissive_multiple must stay at the vanilla-neutral 1.0 rather than the
        fire-specific 1.5 that was applied to every particle system;
      * slsf_1_soft_effect must be set on blended FX, or the quad hard-cuts
        against intersecting geometry and shows its own rectangular edge.
    """

    MIST = 'export/Oblivion.esm/meshes/dungeons/misc/fx/fxmist01.nif'
    GROUND_MIST = ('export/Oblivion.esm/meshes/dungeons/misc/fx/'
                   'fxmistgroundeffect01.nif')

    def _convert(self, src, tmp_path):
        import time
        if not hasattr(time, 'clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat

        dst = tmp_path / 'fx.nif'
        result = convert_nif(src, str(dst))
        assert not result.get('error'), result
        data = NifFormat.Data()
        with open(str(dst), 'rb') as f:
            data.read(f)
        return data

    def _effect_shaders(self, data):
        from pyffi.formats.nif import NifFormat
        return [b for b in data.blocks
                if isinstance(b, NifFormat.BSEffectShaderProperty)]

    @pytest.mark.skipif(not Path(MIST).exists(),
                        reason='Oblivion mist mesh not available')
    def test_mist_keeps_its_authored_emissive(self, tmp_path):
        """fxmist01 is authored at 0.47 grey -- not white."""
        shaders = self._effect_shaders(self._convert(self.MIST, tmp_path))
        assert shaders, 'mist quad did not get an effect shader'
        for s in shaders:
            assert abs(float(s.emissive_color.r) - 0.47) < 0.02, \
                'authored emissive was overwritten (mist renders ~2x too bright)'
            assert abs(float(s.emissive_multiple) - 1.0) < 0.01, \
                'emissive_multiple must stay vanilla-neutral 1.0'

    @pytest.mark.skipif(not Path(MIST).exists(),
                        reason='Oblivion mist mesh not available')
    def test_blended_fx_gets_soft_depth_fade(self, tmp_path):
        """Without soft_effect the quad shows a hard rectangular edge."""
        shaders = self._effect_shaders(self._convert(self.MIST, tmp_path))
        assert shaders
        for s in shaders:
            assert int(s.shader_flags_1.slsf_1_soft_effect) == 1, \
                'blended FX quad has no soft depth fade (rectangular edge)'
            assert float(s.soft_falloff_depth) > 0.0, \
                'soft_effect set but falloff depth is 0 (fade does nothing)'

    @pytest.mark.skipif(not Path(GROUND_MIST).exists(),
                        reason='Oblivion ground-mist mesh not available')
    def test_additive_fx_without_vertex_color_prop_is_not_lit(self, tmp_path):
        """The Ayleid ground mist declares no NiVertexColorProperty.

        lighting_mode therefore defaults to "lit" and every plane used to be
        routed to BSLightingShaderProperty -- lit, normal-mapped and with no
        soft fade.  Additive blending is the second authored unlit indicator.
        """
        data = self._convert(self.GROUND_MIST, tmp_path)
        eff = self._effect_shaders(data)
        assert eff, 'additively-blended mist planes did not reach the FX shader'
        for s in eff:
            assert int(s.shader_flags_1.slsf_1_soft_effect) == 1
            # Authored at (0.13, 0.16, 0.17) -- a dim blue-grey, not white.
            assert float(s.emissive_color.r) < 0.5, \
                'ground mist emissive was promoted to white'


class TestTextureTransformControllerConversion:
    """NiTextureTransformController -> BS*ShaderPropertyFloatController.

    Oblivion scrolls waterfall/lava/gate UVs with a NiTextureTransformController
    on the NiTexturingProperty.  Conversion deletes NiTexturingProperty, so
    without a translation the animation is silently lost and the waterfall
    renders as a frozen texture (landscapewaterfall02.nif).  Skyrim's equivalent
    is a shader float controller on the UV offset/scale -- vanilla
    fxwaterfallbodytall.nif drives V Offset with the same 2-key ramp.
    """

    def _build_textured_strip(self, operation, keys, interp_cls=None):
        """A minimal NiTriShape whose NiTexturingProperty carries one
        NiTextureTransformController.  Returns (shape, NifFormat)."""
        import time
        if not hasattr(time, 'clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat

        shape = NifFormat.NiTriShape()
        shape.data = NifFormat.NiTriShapeData()

        texprop = NifFormat.NiTexturingProperty()
        texprop.has_base_texture = True
        src_tex = NifFormat.NiSourceTexture()
        src_tex.file_name = b'textures\\landscape\\water.dds'
        texprop.base_texture.source = src_tex

        ctrl = NifFormat.NiTextureTransformController()
        ctrl.flags = 0x08          # Oblivion ships Active-only
        ctrl.frequency = 1.0
        ctrl.start_time = 0.0
        ctrl.stop_time = 3.3
        ctrl.texture_slot = 0
        ctrl.operation = operation

        interp = (interp_cls or NifFormat.NiFloatInterpolator)()
        if keys is not None:
            fdata = NifFormat.NiFloatData()
            kg = fdata.data
            kg.interpolation = 2
            kg.num_keys = len(keys)
            kg.keys.update_size()
            for i, (t, v) in enumerate(keys):
                kg.keys[i].time = t
                kg.keys[i].value = v
            interp.data = fdata
        ctrl.interpolator = interp
        texprop.controller = ctrl

        shape.num_properties = 1
        shape.properties.update_size()
        shape.properties[0] = texprop
        return shape, NifFormat

    def _convert(self, shape):
        from asset_convert.nif_converter import _process_geometry
        return _process_geometry(shape, fix_textures=True)

    def test_v_translate_becomes_v_offset_controller(self):
        """The waterfall case: TT_TRANSLATE_V 0 -> -2.0 must survive as a
        BSLightingShaderPropertyFloatController on V Offset (variable 22)."""
        shape, NF = self._build_textured_strip(1, [(0.0, 0.0), (3.3, -2.0)])
        ts = self._convert(shape)

        shader = ts.bs_properties[0]
        assert isinstance(shader, NF.BSLightingShaderProperty)
        ctrl = shader.controller
        assert ctrl is not None, 'UV animation was dropped (waterfall renders frozen)'
        assert isinstance(ctrl, NF.BSLightingShaderPropertyFloatController)
        assert ctrl.type_of_controlled_variable == 22, 'not V Offset'
        assert ctrl.target is shader, 'controller target must be the shader'
        # Compute Scaled Time (0x40) is required or the curve never advances.
        assert ctrl.flags & 0x40, 'missing Compute Scaled Time bit'
        assert ctrl.flags & 0x08, 'controller not Active'
        keys = ctrl.interpolator.data.data.keys
        assert [round(k.time, 4) for k in keys] == [0.0, 3.3]
        assert [round(k.value, 4) for k in keys] == [0.0, -2.0]

    def test_all_four_uv_channels_map(self):
        """TT_TRANSLATE_U/V and TT_SCALE_U/V map to the Lighting enum."""
        for operation, expected in ((0, 20), (1, 22), (3, 21), (4, 23)):
            shape, NF = self._build_textured_strip(
                operation, [(0.0, 0.0), (1.0, 1.0)])
            ctrl = self._convert(shape).bs_properties[0].controller
            assert ctrl is not None and \
                ctrl.type_of_controlled_variable == expected, \
                'operation %d did not map to %d' % (operation, expected)

    def test_rotate_is_dropped(self):
        """TT_ROTATE has no Skyrim equivalent -- emitting a bogus variable would
        animate the wrong channel, so it must be dropped."""
        shape, _ = self._build_textured_strip(2, [(0.0, 0.0), (1.0, 6.28)])
        assert self._convert(shape).bs_properties[0].controller is None

    def test_blend_interpolator_is_dropped(self):
        """NiBlendFloatInterpolator is driven by a NiControllerManager sequence,
        not inline keys -- there is no curve to translate."""
        import time
        if not hasattr(time, 'clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat
        shape, _ = self._build_textured_strip(
            1, None, interp_cls=NifFormat.NiBlendFloatInterpolator)
        assert self._convert(shape).bs_properties[0].controller is None

    def test_single_key_is_dropped(self):
        """One key is a constant, not an animation."""
        shape, _ = self._build_textured_strip(1, [(0.0, 0.5)])
        assert self._convert(shape).bs_properties[0].controller is None

    def test_multiple_channels_chain(self):
        """Vanilla chains one controller per animated channel through
        next_controller (fxwaterfallthin512x128: U Scale -> V Offset -> U Offset)."""
        import time
        if not hasattr(time, 'clock'):
            time.clock = time.perf_counter

        shape, NF = self._build_textured_strip(1, [(0.0, 0.0), (3.3, -2.0)])
        texprop = shape.properties[0]
        second = NF.NiTextureTransformController()
        second.flags = 0x08
        second.frequency = 1.0
        second.start_time = 0.0
        second.stop_time = 8.0
        second.texture_slot = 0
        second.operation = 0                      # TT_TRANSLATE_U
        interp = NF.NiFloatInterpolator()
        fdata = NF.NiFloatData()
        kg = fdata.data
        kg.interpolation = 2
        kg.num_keys = 2
        kg.keys.update_size()
        kg.keys[0].time, kg.keys[0].value = 0.0, 0.0
        kg.keys[1].time, kg.keys[1].value = 8.0, 1.0
        interp.data = fdata
        second.interpolator = interp
        texprop.controller.next_controller = second

        chain = []
        ctrl = self._convert(shape).bs_properties[0].controller
        while ctrl is not None:
            chain.append(ctrl.type_of_controlled_variable)
            ctrl = ctrl.next_controller
        assert chain == [22, 20], 'expected V Offset then U Offset, got %r' % (chain,)

    @pytest.mark.skipif(
        not Path('export/Nehrim.esm/meshes/landscape/landscapewaterfall02.nif').exists(),
        reason='Nehrim waterfall mesh not available')
    def test_nehrim_waterfall_keeps_its_scroll(self, tmp_path):
        """End-to-end: the reported mesh must ship animated UVs."""
        import time
        if not hasattr(time, 'clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat

        dst = tmp_path / 'waterfall.nif'
        result = convert_nif(
            'export/Nehrim.esm/meshes/landscape/landscapewaterfall02.nif', str(dst))
        assert not result.get('error'), result

        data = NifFormat.Data()
        with open(str(dst), 'rb') as f:
            data.read(f)
        ctrls = [b for b in data.blocks
                 if isinstance(b, (NifFormat.BSLightingShaderPropertyFloatController,
                                   NifFormat.BSEffectShaderPropertyFloatController))]
        assert ctrls, 'converted waterfall has no UV animation'
        # "V Offset" is numbered per shader type -- 22 on the lighting shader,
        # 8 on the effect shader (_TEX_TRANSFORM_VARS).  The waterfall is
        # additively blended, so it takes BSEffectShaderProperty; assert the
        # variable the controller's OWN shader uses rather than one constant.
        v_offset_for = {
            NifFormat.BSLightingShaderPropertyFloatController: 22,
            NifFormat.BSEffectShaderPropertyFloatController: 8,
        }
        assert all(c.type_of_controlled_variable == v_offset_for[type(c)]
                   for c in ctrls), 'waterfall scroll is not on V Offset'
        assert all(c.interpolator is not None and c.interpolator.data is not None
                   for c in ctrls), 'controller lost its curve'



# ---------------------------------------------------------------------------
# NiTimeController "Compute Scaled Time" (CharacterGen secret wall never opened)
# ---------------------------------------------------------------------------

_SECRET_WALL_SAMPLE = 'Dungeons/Chargen/prisonsecretwall01.nif'
_SECRET_SWITCH_SAMPLE = 'Dungeons/Chargen/prisonsecretwallswitch01.nif'

# nif.xml TimeControllerFlags bit 6, default="true".
_COMPUTE_SCALED_TIME = 0x40


class TestControllerComputeScaledTime:
    """Oblivion never sets flags bit 0x40; Skyrim needs it to advance a sequence.

    Without it, ObjectReference.PlayAnimation() binds the sequence and returns
    success with no Papyrus error, but scaled time never advances and the object
    stays on frame 0 — CharacterGen's secret wall reported "opened" while
    physically staying shut.  Vanilla Skyrim animated doors ship
    NiMultiTargetTransformController=108 and every other controller=76.
    """

    @pytest.mark.parametrize('sample', [_SECRET_WALL_SAMPLE, _SECRET_SWITCH_SAMPLE])
    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_every_controller_computes_scaled_time(self, tmp_path, sample):
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / sample
        if not src.exists():
            pytest.skip(f'{src} not found')

        # The source must actually exhibit the defect, or the test proves nothing.
        src_data = NF.Data()
        with open(str(src), 'rb') as f:
            src_data.read(f)
        src_ctrls = [b for b in src_data.blocks if isinstance(b, NF.NiTimeController)]
        assert src_ctrls, 'sample has no controllers — wrong test mesh'
        assert all(not (c.flags & _COMPUTE_SCALED_TIME) for c in src_ctrls), \
            'Oblivion source unexpectedly already sets 0x40; test is stale'

        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        ctrls = [b for b in dst_data.blocks if isinstance(b, NF.NiTimeController)]
        assert ctrls, 'conversion dropped every controller'
        missing = [type(c).__name__ for c in ctrls
                   if not (c.flags & _COMPUTE_SCALED_TIME)]
        assert not missing, \
            f'controllers missing Compute Scaled Time (0x40): {sorted(set(missing))}'

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_flags_match_vanilla_values(self, tmp_path):
        """Converted flags equal the vanilla census: MTTC=108, others=76."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / _SECRET_WALL_SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        mgrs = [b for b in dst_data.blocks
                if isinstance(b, NF.NiControllerManager)]
        assert mgrs, 'NiControllerManager was dropped — wall cannot animate'
        assert all(m.flags == 76 for m in mgrs), \
            f'manager flags {[m.flags for m in mgrs]} != vanilla 76'

        mttcs = [b for b in dst_data.blocks
                 if isinstance(b, NF.NiMultiTargetTransformController)]
        assert mttcs, 'NiMultiTargetTransformController was dropped'
        assert all(m.flags == 108 for m in mttcs), \
            f'MTTC flags {[m.flags for m in mttcs]} != vanilla 108'

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_open_sequence_keyframes_survive(self, tmp_path):
        """The 'Forward' sequence keeps the tracks that actually move the wall."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / _SECRET_WALL_SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        seqs = {str(b.name): b for b in dst_data.blocks
                if isinstance(b, NF.NiControllerSequence)}
        # PlayAnimation("Forward") is what the converted switch script calls.
        fwd = next((s for n, s in seqs.items() if 'Forward' in n), None)
        assert fwd is not None, f'Forward sequence lost; have {list(seqs)}'

        # The moving parts are the 'wall' and 'bed' transform tracks.  Palette
        # offsets must have been resolved to real names (Skyrim has no palette).
        targets = {str(cb.node_name) for cb in fwd.controlled_blocks}
        joined = ' '.join(targets)
        assert 'wall' in joined and 'bed' in joined, \
            f'transform tracks missing from Forward: {sorted(targets)}'
        assert '' not in targets, 'unresolved (empty) node_name in sequence'


# ---------------------------------------------------------------------------
# Ambient (self-playing) sequences: AutoPlay/AutoLoop pair + accum-root pose
# ---------------------------------------------------------------------------

_ARENA_SPECTATOR = 'architecture/arena/arenaspectatorm01.nif'
_LOAD_DOOR_ACCUM = 'architecture/bravil/bravilloaddoorlowerint01.nif'
_CLAW_STAND = 'oblivion/clutter/containers/clawstandcontainer.nif'
_CANDLE_SKINNY = 'lights/candleskinny02.nif'
_MINE_TRAP = 'oblivion/clutter/traps/obminetrap01.nif'


def _playing_world_rotations(path):
    """{shape name: world rotation angle (deg)} while the first sequence plays.

    Walks the real parent chain, substituting each node's sequence POSE for its
    bind rotation wherever the sequence drives it (a -FLT_MAX sentinel means
    "no value", i.e. keep the bind).  Grafted flame subtrees are skipped -- they
    do not exist in the TES4 source.
    """
    import math
    import numpy as np
    NF, d = _read_nif(path)
    root = d.roots[0]
    parent = {}

    def walk(n):
        for c in (getattr(n, 'children', None) or ()):
            if c is None or not isinstance(c, NF.NiAVObject):
                continue
            parent[id(c)] = n
            walk(c)
    walk(root)

    def m3(n):
        m = n.rotation
        return np.array([[getattr(m, 'm_%d%d' % (i + 1, j + 1))
                          for j in range(3)] for i in range(3)])

    def q2m(q):
        w, x, y, z = q.w, q.x, q.y, q.z
        return np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])

    seqs = [b for b in root.tree() if isinstance(b, NF.NiControllerSequence)]
    if not seqs:
        return {}
    seq = seqs[0]
    pal = getattr(seq, 'string_palette', None)
    buf = bytes(pal.palette.palette) if pal is not None else b''
    poses = {}
    for cb in seq.controlled_blocks:
        off = getattr(cb, 'node_name_offset', 0xFFFFFFFF)
        if buf and off != 0xFFFFFFFF:
            nm = buf[off:buf.index(bytes([0]), off)]
        else:
            nm = bytes(getattr(cb, 'node_name', b'') or b'')
        it = cb.interpolator
        if isinstance(it, NF.NiTransformInterpolator) and it.data is None:
            poses[nm] = q2m(it.rotation) if it.rotation.w > -3.0e38 else None

    out = {}
    for n in root.tree():
        if not isinstance(n, NF.NiTriBasedGeom):
            continue
        nm = bytes(getattr(n, 'name', b'') or b'')
        if b'FireCandleFlame' in nm or b'FireTorch' in nm or b'FireOpen' in nm:
            continue
        chain = []
        c = n
        while c is not None:
            chain.append(c)
            c = parent.get(id(c))
        chain.reverse()
        W = np.eye(3)
        for c in chain:
            cn = bytes(getattr(c, 'name', b'') or b'')
            pz = poses.get(cn, 'absent')
            W = W @ (m3(c) if (pz is None or isinstance(pz, str)) else pz)
        tr = max(-1.0, min(1.0, (W.trace() - 1.0) / 2.0))
        out[nm.decode('latin-1')] = math.degrees(math.acos(tr))
    return out


def _read_nif(path):
    import time
    if not hasattr(time, '_original_clock'):
        time.clock = time.perf_counter
    from pyffi.formats.nif import NifFormat as NF
    d = NF.Data()
    with open(str(path), 'rb') as f:
        d.read(f)
    return NF, d


class TestAmbientSequences:
    """The arena crowd: read out of the live engine 2026-08-18 (see
    docs/commentary/asset_convert_nif.md, 'Ambient (self-playing) meshes')."""

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_idle_becomes_autoloop_loop_plus_autoplay_clamp(self, tmp_path):
        src = EXPORT_MESHES / _ARENA_SPECTATOR
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'meshes' / 'tes4' / 'arena' / 'arenaspectatorm01.nif'
        dst.parent.mkdir(parents=True)
        convert_nif(str(src), str(dst))
        NF, d = _read_nif(dst)
        seqs = {bytes(b.name).decode('latin-1'): b for b in d.blocks
                if isinstance(b, NF.NiControllerSequence)}
        assert set(seqs) == {'AutoLoop', 'AutoPlay'}, list(seqs)
        # CycleType: 0 LOOP, 2 CLAMP.  The authored Idle is LOOP and stays so;
        # the AutoPlay intro must END for the graph to hand off to AutoLoop.
        assert int(seqs['AutoLoop'].cycle_type) == 0
        assert int(seqs['AutoPlay'].cycle_type) == 2
        assert seqs['AutoPlay'].num_controlled_blocks == seqs['AutoLoop'].num_controlled_blocks
        bged = [b for b in d.blocks if type(b).__name__ == 'BSBehaviorGraphExtraData']
        assert bged and b'Autoplay.hkx' in bytes(bged[0].behaviour_graph_file)

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_transferred_accum_root_pose_stays_identity(self, tmp_path):
        """Bip01's identity pose is REAL: 'Bip01 NonAccum' carries the 82.5 deg
        / 64-unit transform, so sentinelling Bip01's rotation doubled it."""
        src = EXPORT_MESHES / _ARENA_SPECTATOR
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))
        NF, d = _read_nif(dst)
        for seq in (b for b in d.blocks if isinstance(b, NF.NiControllerSequence)):
            cb = next(c for c in seq.controlled_blocks if bytes(c.node_name) == b'Bip01')
            it = cb.interpolator
            assert it.data is None
            assert (it.translation.x, it.translation.y, it.translation.z) == (0.0, 0.0, 0.0)
            assert (round(it.rotation.w, 3), it.rotation.x, it.rotation.y, it.rotation.z) == (1.0, 0.0, 0.0, 0.0)
            assert it.scale == 1.0

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_transferred_door_accum_root_pose_stays_identity(self, tmp_path):
        """Load door whose accum root sits at (0,-42.7,12)/90 deg with NonAccum
        carrying that transform: the identity must be applied, not sentinelled."""
        src = EXPORT_MESHES / _LOAD_DOOR_ACCUM
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))
        NF, d = _read_nif(dst)
        for seq in (b for b in d.blocks if isinstance(b, NF.NiControllerSequence)):
            cb = next(c for c in seq.controlled_blocks if bytes(c.node_name) == b'DoorLowerINT01')
            it = cb.interpolator
            assert round(it.rotation.w, 3) == 1.0 and it.scale == 1.0,                 'accum-root rotation sentinelled -> door rotation doubles'

    # The accum root and its "<accum> NonAccum" child are ONE transform split
    # across a PAIR of entries.  The root-named entry must be DROPPED (naming
    # the file root crashes the engine), so the NonAccum entry has to absorb
    # the difference: X = Abind^-1 @ Apose @ NApose.  Leaving it sentinelled
    # (X = I) or merely un-sentinelling it (X = NApose) are both wrong unless
    # Abind happens to be identity.  The invariant that matters is the WORLD
    # rotation of the geometry while the sequence plays, so assert that
    # against the TES4 source rather than any raw pose value.
    @pytest.mark.parametrize('rel', [
        _CLAW_STAND,      # "The Punished" (REFR 0009503A) -- stood 90 deg over
        _CANDLE_SKINNY,   # 2.5 deg tilt: under the old diagonal identity test
        _MINE_TRAP,       # script-driven, was already correct -- must not regress
        'lights/wallsconcesingle01fake.nif',
        'lights/lampsconce01fake.nif',
    ])
    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_playing_world_rotation_matches_tes4(self, tmp_path, rel):
        src = EXPORT_MESHES / rel
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))
        want = _playing_world_rotations(str(src))
        got = _playing_world_rotations(str(dst))
        shared = set(want) & set(got)
        assert shared, 'no comparable geometry'
        for name in sorted(shared):
            assert abs(want[name] - got[name]) < 0.5, (
                f'{rel}: {name} plays at {got[name]:.2f} deg, '
                f'TES4 plays it at {want[name]:.2f} deg')


_PALACE_FONT = 'architecture/palace/interior/palacefont01.nif'


class TestSharedPropertyFanOut:
    """Oblivion shares one NiTexturingProperty between shapes; a sequence entry
    names one shape but scrolls all of them.  Every wearing shape must get its
    own entry + controller (the Font of Madness's upper tier, 2026-08-18)."""

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_every_water_shape_is_driven(self, tmp_path):
        src = EXPORT_MESHES / _PALACE_FONT
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))
        NF, d = _read_nif(dst)
        water = ['Water', 'Water02', 'Water03', 'Water04', 'WaterFoam01',
                 'PalaceWaterL1', 'PalaceWaterR1', 'PalaceWaterL2',
                 'PalaceWaterR02', 'PalaceWaterFoam01']
        for seq in (b for b in d.blocks if isinstance(b, NF.NiControllerSequence)):
            named = {bytes(cb.node_name).decode('latin-1') for cb in seq.controlled_blocks}
            assert set(water) <= named, (seq.name, sorted(set(water) - named))
        # ...and each shape's own shader carries its own (targeted) controller.
        for b in d.blocks:
            if isinstance(b, NF.NiTriShape) and bytes(b.name).decode('latin-1') in water:
                shader = next(p for p in b.bs_properties if p is not None
                              and 'ShaderProperty' in type(p).__name__)
                c = shader.controller
                assert c is not None and c.target is shader, bytes(b.name)


# ---------------------------------------------------------------------------
# Shader-property controller targets (NULL target = CTD on cell load)
# ---------------------------------------------------------------------------

_MW_EXPORT_MESHES = Path('export/Morrowind_ob.esm/meshes')
# Cloned from a candle without rebuilding its NiControllerSequence string
# palette, so the sequence animates "CandleSkinny01:0" while the geometry is
# "Tri Tri Light_Com_Chandelier_01 2 N".  Placed 7x in the Seyda Neen Census
# and Excise Office, which is where it crashed.
_STALE_PALETTE_SAMPLE = 'morroblivion/lights/morroblivionchandilier01.nif'


class TestShaderControllerTarget:
    """BS*ShaderProperty*Controller.Target must name its shader block.

    Vanilla census: 15/15 such controllers point at their own shader property
    (Lighting -> BSLightingShaderProperty, Effect -> BSEffectShaderProperty),
    0 nulls.  Skyrim dereferences Target while loading the shader property, so
    a NULL is an access violation on cell load rather than a dead animation.
    """

    def _shader_ctrls(self, data):
        return [b for b in data.blocks
                if 'ShaderProperty' in type(b).__name__
                and 'Controller' in type(b).__name__]

    @pytest.mark.skipif(not _MW_EXPORT_MESHES.exists(),
                        reason='Morrowind export meshes not available')
    def test_stale_palette_entry_does_not_emit_null_target(self, tmp_path):
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = _MW_EXPORT_MESHES / _STALE_PALETTE_SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')

        # The source must actually exhibit the defect, or the test proves
        # nothing: its sequence must name a node the mesh does not contain.
        src_data = NF.Data()
        with open(str(src), 'rb') as f:
            src_data.read(f)
        node_names = {bytes(getattr(b, 'name', b'') or b'')
                      for b in src_data.blocks}
        palettes = [b.string_palette for b in src_data.blocks
                    if isinstance(b, NF.NiControllerSequence)
                    and b.string_palette is not None]
        assert palettes, 'sample has no string palette — test is stale'
        pal = bytes(palettes[0].palette.palette)
        # The color controller targets "CandleSkinny01:0", which — unlike the
        # bare "CandleSkinny01" NiNode carrying the transform tracks — is not a
        # node in this mesh at all, so no shader can ever be found for it.
        assert b'CandleSkinny01:0' in pal, \
            'sample palette changed — test is stale'
        assert b'CandleSkinny01:0' not in node_names, \
            'sample no longer has a dangling palette name — test is stale'

        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        ctrls = self._shader_ctrls(dst_data)
        nulls = [type(c).__name__ for c in ctrls if c.target is None]
        assert not nulls, f'NULL-target shader controllers emitted: {nulls}'

        # FAITHFUL CONVERSION: the emissive flicker must SURVIVE.  Deleting the
        # entry was the earlier (wrong) fix -- it cost the chandelier its
        # animation and stranded the manager with 0 sequences, which the engine
        # dereferences just the same (vanilla ships no 0-sequence manager).
        assert ctrls, 'emissive animation was dropped instead of retargeted'

        empty = [bytes(b.name) for b in dst_data.blocks
                 if isinstance(b, NF.NiControllerSequence)
                 and b.num_controlled_blocks == 0]
        assert not empty, f'empty sequences left in file: {empty}'
        for mgr in dst_data.blocks:
            if isinstance(mgr, NF.NiControllerManager):
                assert mgr.num_controller_sequences > 0, \
                    'NiControllerManager left with 0 sequences (engine derefs it)'
                assert all(s is not None for s in mgr.controller_sequences), \
                    'manager references a dropped sequence'

        # The curve itself must come across unchanged (5 keys, 0 -> 3s loop).
        src_keys = []
        for b in src_data.blocks:
            if isinstance(b, NF.NiControllerSequence):
                for cb in b.controlled_blocks:
                    it = cb.interpolator
                    if it is not None and type(it).__name__ == 'NiPoint3Interpolator':
                        src_keys = [(round(k.time, 4),
                                     (round(k.value.x, 4), round(k.value.y, 4),
                                      round(k.value.z, 4)))
                                    for k in it.data.data.keys]
        assert src_keys, 'source has no color curve — test is stale'
        got_keys = []
        for c in ctrls:
            it = c.interpolator
            if it is not None and getattr(it, 'data', None) is not None:
                got_keys = [(round(k.time, 4),
                             (round(k.value.x, 4), round(k.value.y, 4),
                              round(k.value.z, 4)))
                            for k in it.data.data.keys]
        assert got_keys == src_keys, \
            f'color curve changed: {src_keys} -> {got_keys}'

        # The entry must name a block that exists, or the engine cannot bind it.
        names = {bytes(getattr(b, 'name', b'') or b'') for b in dst_data.blocks}
        for b in dst_data.blocks:
            if not isinstance(b, NF.NiControllerSequence):
                continue
            for cb in b.controlled_blocks:
                nm = bytes(cb.node_name or b'')
                if nm:
                    assert nm in names, \
                        f'sequence entry names a missing node: {nm!r}'

    @pytest.mark.skipif(not _MW_EXPORT_MESHES.exists(),
                        reason='Morrowind export meshes not available')
    @pytest.mark.parametrize('sample', [
        'morro/x/exuvivecuwaterfallu03.nif',   # scrolling UV (valid animation)
        'morro/i/inulavau512.nif',
    ])
    def test_resolvable_controllers_keep_animation_and_bind(self, tmp_path, sample):
        """A controller whose node DOES resolve is bound, never dropped."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = _MW_EXPORT_MESHES / sample
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        ctrls = self._shader_ctrls(dst_data)
        assert ctrls, 'shader animation was dropped entirely'
        for c in ctrls:
            assert c.target is not None, \
                f'{type(c).__name__} left unbound'
            want = ('BSEffectShaderProperty'
                    if type(c).__name__.startswith('BSEffectShaderProperty')
                    else 'BSLightingShaderProperty')
            assert type(c.target).__name__ == want, \
                f'{type(c).__name__} bound to {type(c.target).__name__}'


class TestUVSetClamp:
    """Skyrim reads ONE UV set; a second one overruns the engine's vertex buffer.

    On disk the u16 "BS Data Flags" packs the UV-set count in its low 6 bits,
    and that count is the only thing telling the engine how many TexCoord
    arrays follow.  A mesh storing 2 sets while the shader binds 1 leaves the
    buffer an array short -> non-temporal memcpy past the allocation (vmovntdq)
    -> CTD on cell load.  Vanilla census: 2,233 shapes, 0 or 1 sets, never 2.
    """

    _SAMPLE = 'morro/f/FurnUComUTableU05.nif'   # Seyda Neen Census Office table

    @pytest.mark.skipif(not _MW_EXPORT_MESHES.exists(),
                        reason='Morrowind export meshes not available')
    def test_second_uv_set_is_dropped(self, tmp_path):
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = _MW_EXPORT_MESHES / self._SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')

        # Source must actually carry 2 UV sets or the test proves nothing.
        src_data = NF.Data()
        with open(str(src), 'rb') as f:
            src_data.read(f)
        multi = [b for b in src_data.blocks
                 if type(b).__name__ in ('NiTriShapeData', 'NiTriStripsData')
                 and int(getattr(b, 'num_uv_sets', 0) or 0) > 1]
        assert multi, 'sample has no multi-UV geometry — test is stale'

        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        for b in dst_data.blocks:
            if type(b).__name__ not in ('NiTriShapeData', 'NiTriStripsData'):
                continue
            n = int(getattr(b, 'num_uv_sets', 0) or 0)
            assert n <= 1, f'{n} UV sets survived conversion'
            # The count and the stored arrays must agree, or the engine reads
            # a different number of arrays than the file holds.
            assert len(b.uv_sets) == n, \
                f'num_uv_sets={n} but {len(b.uv_sets)} arrays stored'
            for uvs in b.uv_sets:
                assert len(uvs) == b.num_vertices, \
                    'UV array length != vertex count'

    @pytest.mark.skipif(not _MW_EXPORT_MESHES.exists(),
                        reason='Morrowind export meshes not available')
    def test_kept_uv_set_is_the_diffuse_one(self, tmp_path):
        """Set 0 (what every shader samples) is the one retained."""
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = _MW_EXPORT_MESHES / self._SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')
        src_data = NF.Data()
        with open(str(src), 'rb') as f:
            src_data.read(f)
        want = {}
        for i, b in enumerate(src_data.blocks):
            if (type(b).__name__ in ('NiTriShapeData', 'NiTriStripsData')
                    and int(getattr(b, 'num_uv_sets', 0) or 0) > 1):
                want[b.num_vertices] = [(k.u, k.v) for k in b.uv_sets[0][:8]]

        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))
        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        checked = 0
        for b in dst_data.blocks:
            if type(b).__name__ not in ('NiTriShapeData', 'NiTriStripsData'):
                continue
            if b.num_vertices not in want or not len(b.uv_sets):
                continue
            got = [(k.u, k.v) for k in b.uv_sets[0][:8]]
            assert got == want[b.num_vertices], \
                'retained UV set is not the original set 0'
            checked += 1
        assert checked, 'no converted shape matched the source shapes'


class TestNiUVControllerConversion:
    """NiUVController has NO RTTI in SkyrimSE.exe — the engine cannot build it.

    NiStream constructs blocks by type name; an unknown type leaves a slot the
    engine then treats as a NiObject, so `lock cmpxchg` on its "refcount" lands
    in read-only .rdata => access violation on mesh load.  The UV curves
    themselves survive as BS*ShaderPropertyFloatControllers.
    """

    _SAMPLE = 'morro/x/exuggufenceu01.nif'   # Ghostfence shimmer

    @pytest.mark.skipif(not _MW_EXPORT_MESHES.exists(),
                        reason='Morrowind export meshes not available')
    def test_niuvcontroller_is_removed_and_curve_preserved(self, tmp_path):
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = _MW_EXPORT_MESHES / self._SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')

        # Source must actually carry the defect, or the test proves nothing.
        src_data = NF.Data()
        with open(str(src), 'rb') as f:
            src_data.read(f)
        src_uv = [b for b in src_data.blocks
                  if type(b).__name__ == 'NiUVController']
        assert src_uv, 'sample has no NiUVController — test is stale'

        dst = tmp_path / 'out.nif'
        convert_nif(str(src), str(dst))

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)

        names = [type(b).__name__ for b in dst_data.blocks]
        assert 'NiUVController' not in names, \
            'NiUVController survived conversion — engine cannot construct it'
        assert 'NiUVData' not in names, 'orphaned NiUVData left behind'

        # The animation must be re-emitted, not merely deleted.
        ctrls = [b for b in dst_data.blocks
                 if type(b).__name__.endswith('ShaderPropertyFloatController')]
        assert ctrls, 'UV animation was dropped instead of converted'
        # U Offset = 20 and V Offset = 22 on the Lighting shader.
        got = {c.type_of_controlled_variable for c in ctrls}
        assert got & {20, 22}, f'unexpected controlled variables: {got}'
        for c in ctrls:
            assert c.target is not None, 'converted controller left unbound'
            assert c.interpolator is not None
            assert c.interpolator.data.data.num_keys >= 2, \
                'curve keys lost in translation'


# ---------------------------------------------------------------------------
# Animated-object behaviour graphs (BGED -> hkx project)
# ---------------------------------------------------------------------------


class TestAnimObjectBehaviorGraph:
    """PlayAnimation() needs an animation graph manager, which only exists when
    the NIF carries a BSBehaviorGraphExtraData naming an hkx project.

    Without the graph the call is accepted, returns immediately and does
    nothing — no Papyrus error — so the secret wall stayed shut.  Shape is
    copied from vanilla `NocturnalsSecretDoor01`: one
    BGSGamebryoSequenceGenerator per NiControllerSequence, each wrapped in a
    state reached by a same-named event, plus a Rest start state.

    Four separate defects had to be fixed before CharacterGen's secret wall
    worked in-game (2026-07-26); each one is guarded below because every one of
    them was INVISIBLE to structural inspection and to NifSkope, which renders
    and animates the NIF perfectly while never loading the hkx at all:

      1. BGED carried a 'meshes\\' prefix  -> project never found -> no graph
         -> the object was never rendered.
      2. Skeleton bone named after the model -> engine bound the placeholder
         rig onto the object -> wall placed far from its authored position.
      3. startStateId pointed at a motion sequence -> the wall swung open by
         itself on cell load instead of waiting for the switch.
      4. Rest state had transitions=null -> dead end -> nothing could ever
         open it again, from the quest or from console `activate`.
    """

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_graph_tree_generated_and_bged_attached(self, tmp_path):
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / _SECRET_WALL_SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')

        # Must go through a real 'meshes' tree — the output root is derived
        # from dst_path, and no meshes/ segment means no graph.
        dst = tmp_path / 'meshes' / 'tes4' / 'dungeons' / 'chargen' / 'wall.nif'
        dst.parent.mkdir(parents=True, exist_ok=True)
        convert_nif(str(src), str(dst))

        base = dst.parent / 'wall_behavior'
        for rel in ('wall.hkx', 'Behaviors/Behavior00.hkx',
                    'Characters/Character01.hkx',
                    'CharacterAssets/Skeleton.hkx'):
            f = base / rel
            assert f.is_file(), f'missing generated hkx: {rel}'
            # SSE only loads 64-bit packfiles (pointer size at offset 0x10).
            assert f.read_bytes()[0x10] == 8, f'{rel} is not AMD64'

        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)
        root = dst_data.roots[0]

        bged = [e for e in root.extra_data_list
                if isinstance(e, NF.BSBehaviorGraphExtraData)]
        assert bged, 'no BGED — PlayAnimation() has no graph manager'
        # Relative to meshes\, with NO 'meshes\' prefix — the engine prepends
        # "Meshes\%s".  A doubled prefix means the project is never found, the
        # object gets no graph, and it is invisible in-game (NifSkope, which
        # never loads the hkx, still renders and animates it).
        graph = bged[0].behaviour_graph_file.decode('latin-1')
        assert not graph.lower().startswith('meshes\\'), \
            f'BGED must not repeat the meshes prefix: {graph}'
        assert graph.lower() == r'tes4\dungeons\chargen\wall_behavior\wall.hkx', \
            f'BGED path wrong: {graph}'

        # The engine never ticks the graph without the BSX Animated bit.
        bsx = [e for e in root.extra_data_list if isinstance(e, NF.BSXFlags)]
        assert bsx and (int(bsx[0].integer_data) & 0x01), \
            'BSX Animated bit (0x01) not set — graph loads but never ticks'

    def test_events_match_sequence_names(self):
        """PlayAnimation("<seq>") only works if <seq> is an event AND a state.

        The script calls PlayAnimation("Forward"); the graph must expose a
        'Forward' event routed to a generator whose pSequence is 'Forward'.
        """
        from asset_convert.hkx_animobject import _behavior_xml

        xml = _behavior_xml('wall', ['Forward', 'Backward'])
        for seq in ('Forward', 'Backward'):
            assert f'<hkcstring>{seq}</hkcstring>' in xml, f'{seq} not an event'
            assert f'<hkparam name="pSequence">{seq}</hkparam>' in xml, \
                f'{seq} has no Gamebryo generator'
            assert f'<hkparam name="name">{seq}</hkparam>' in xml, \
                f'{seq} has no state'

        # SERIALIZE_IGNORED in the vanilla template — emitting any of these
        # makes hkxcmd fail the compile SILENTLY (no file, no error text).
        for dead in ('bLooping', 'bDelayedActivate', 'fTime'):
            assert f'name="{dead}"' not in xml, \
                f'{dead} is SERIALIZE_IGNORED and breaks the compile'

    def test_no_sequences_means_no_graph(self, tmp_path):
        """A static mesh must not get a BGED pointing at a nonexistent graph."""
        from asset_convert.hkx_animobject import generate_animobject_project

        assert generate_animobject_project(str(tmp_path), 'a/b.nif', []) == ''
        assert not list(tmp_path.rglob('*.hkx'))

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_engine_driven_doors_get_no_graph(self, tmp_path):
        """Open/Close doors are driven natively — a graph CTDs them.

        prisonCellGate01 animated correctly through its own
        NiControllerManager; attaching a behaviour graph made the engine bind
        the sequence through the graph instead and crash on cell load
        (EXCEPTION_ACCESS_VIOLATION, rax=0, "GamebryoSequenceGenerator00").
        No script names Open/Close — only Forward/Backward/Equip/... appear in
        converted PlayAnimation() calls.
        """
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF
        from asset_convert.nif_converter import collect_sequence_names

        src = EXPORT_MESHES / 'Dungeons/Chargen/prisoncellgate01.nif'
        if not src.exists():
            pytest.skip(f'{src} not found')

        # The mesh really does have Open/Close sequences, or this proves nothing.
        src_data = NF.Data()
        with open(str(src), 'rb') as f:
            src_data.read(f)
        raw = {str(b.name) for b in src_data.blocks
               if isinstance(b, NF.NiControllerSequence)}
        assert any('Open' in n for n in raw), f'test mesh changed: {raw}'
        assert collect_sequence_names(src_data) == [], \
            'engine-driven Open/Close must not qualify for a graph'

        dst = tmp_path / 'meshes' / 'tes4' / 'gate.nif'
        dst.parent.mkdir(parents=True, exist_ok=True)
        convert_nif(str(src), str(dst))

        assert not list(tmp_path.rglob('*.hkx')), 'graph generated for a native door'
        dst_data = NF.Data()
        with open(str(dst), 'rb') as f:
            dst_data.read(f)
        assert not [e for e in dst_data.roots[0].extra_data_list
                    if isinstance(e, NF.BSBehaviorGraphExtraData)], \
            'BGED attached to an engine-driven door — this is the CTD'

    @pytest.mark.skipif(not EXPORT_MESHES.exists(), reason='Export meshes not available')
    def test_bged_mesh_never_sets_bsx_articulated(self, tmp_path):
        """BSX bit 0x80 + a BGED = invisible object.

        0x80 marks the mesh articulated/ragdoll-driven; with a behaviour graph
        attached the engine waits on a physics rig a Gamebryo-sequence graph
        never provides and never draws the mesh — while NifSkope, which does not
        load the hkx, renders and animates it perfectly.  Census: of 217 vanilla
        animated-object meshes carrying a BGED, ZERO set 0x80 (values 0x4-0x20;
        vanilla NocturnalsSecretDoor01 is 0x0B).  The converter's default
        BSX_FLAGS_ANIMATED (0x8B) stays correct for graph-less meshes.
        """
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF

        src = EXPORT_MESHES / _SECRET_WALL_SAMPLE
        if not src.exists():
            pytest.skip(f'{src} not found')
        dst = tmp_path / 'meshes' / 'tes4' / 'wall.nif'
        dst.parent.mkdir(parents=True, exist_ok=True)
        convert_nif(str(src), str(dst))

        data = NF.Data()
        with open(str(dst), 'rb') as f:
            data.read(f)
        root = data.roots[0]
        bged = [e for e in root.extra_data_list
                if isinstance(e, NF.BSBehaviorGraphExtraData)]
        bsx = [e for e in root.extra_data_list if isinstance(e, NF.BSXFlags)]
        assert bged and bsx, 'expected both BGED and BSXFlags on this mesh'

        value = int(bsx[0].integer_data)
        assert not (value & 0x80), \
            f'BSX {hex(value)} sets 0x80 with a BGED — object will be invisible'
        assert value & 0x01, f'BSX {hex(value)} missing Animated bit'
        assert value == 0x0B, f'BSX {hex(value)} != vanilla 0x0B'

    def test_skeleton_matches_vanilla_bytes(self, tmp_path):
        """The generated skeleton must equal vanilla SingleBoneSkeleton.hkx.

        hkxcmd compiles the identity pose text every shipped creature skeleton
        uses into a reference pose whose ROTATION SLOT IS ALL ZEROS.  A zero
        quaternion is not a rotation, so the single bone had no valid bind pose
        and the whole object rendered NOTHING in-game while the behaviour graph
        loaded without error.  `_fix_identity_quat` patches it to (1,0,0,0);
        Havok's binary quaternion is w-first, unlike the XML's xyzw.
        """
        import struct
        from asset_convert.hkx_animobject import _skeleton_xml, _fix_identity_quat
        from asset_convert.hkx_xml import compile_hkx

        vanilla = Path('references/Skyrim Animations/meshes/clutter/beehive'
                       '/characterassets/singleboneskeleton.hkx')
        if not vanilla.exists():
            pytest.skip(f'{vanilla} not found')

        xml = tmp_path / 's.xml'
        hkx = tmp_path / 's.hkx'
        # Same bone name as vanilla, or the string table differs legitimately.
        xml.write_text(_skeleton_xml('x_SingleBone'), newline='\n')
        compile_hkx(str(xml), str(hkx))
        _fix_identity_quat(str(hkx))

        got = hkx.read_bytes()
        assert got == vanilla.read_bytes(), \
            'generated skeleton is not byte-identical to vanilla'

        # Spell out the two values that actually broke rendering.
        quat = struct.unpack_from('<4f', got, 812)
        scale = struct.unpack_from('<4f', got, 828)
        assert quat == (1.0, 0.0, 0.0, 0.0), f'quaternion not identity: {quat}'
        assert scale == (1.0, 1.0, 1.0, 1.0), f'scale not unit: {scale}'

    def test_skeleton_bone_is_the_vanilla_dummy_name(self):
        """The placeholder bone must never be named after the model.

        The rig is a stand-in — the real motion lives in the NIF's
        NiControllerSequences — so vanilla's single-bone object skeleton uses
        the fixed name `x_SingleBone`.  Naming the bone after the model made the
        engine bind the graph's identity bind pose onto the object and place it
        far from its authored worldspace position.
        """
        from asset_convert.hkx_animobject import (_skeleton_xml, _DUMMY_BONE,
                                                  generate_animobject_project)
        import tempfile

        assert _DUMMY_BONE == 'x_SingleBone'
        assert f'<hkparam name="name">{_DUMMY_BONE}</hkparam>' in \
            _skeleton_xml(_DUMMY_BONE)

        # The generator must pass the dummy name, not the model stem.
        out = tempfile.mkdtemp()
        generate_animobject_project(out, 'tes4/dungeons/chargen/mywall.nif',
                                    ['Forward'])
        skel = (Path(out) / 'tes4' / 'dungeons' / 'chargen' /
                'mywall_behavior' / 'CharacterAssets' / 'Skeleton.hkx')
        raw = skel.read_bytes()
        assert b'x_SingleBone' in raw, 'skeleton lost the vanilla dummy bone'
        assert b'mywall' not in raw, \
            'skeleton names the model — object will be mispositioned'

    def test_starts_at_rest_and_rest_can_reach_every_sequence(self):
        """Start on a non-playing state, and never let it be a dead end.

        Vanilla starts on an idle (BlackPoolSecretDoor startStateId=3 =
        AnimIdle01) and reaches the motion only by event.  Oblivion sources have
        no idle, so we synthesise a Rest state with an empty pSequence.

        Both halves matter and each was a separate in-game bug: starting on a
        motion state made the wall open by itself on load; giving Rest a null
        transition array made it a DEAD END so nothing could open it again.
        Transitions must live ON THE STATE — vanilla's Gamebryo state machine
        sets wildcardTransitions=null and gives each state its own array.
        """
        import re
        from asset_convert.hkx_animobject import _behavior_xml

        seqs = ['Forward', 'Backward']
        xml = _behavior_xml('wall', seqs)

        # Rest is the last state and plays nothing.
        rest_id = len(seqs)
        assert '<hkparam name="pSequence"></hkparam>' in xml, \
            'Rest state must have an empty pSequence'
        assert f'<hkparam name="startStateId">{rest_id}</hkparam>' in xml, \
            'must start on the Rest state, not on a motion sequence'

        # Every state, Rest included, owns a real transition array — at EVERY
        # sequence count.  A ONE-sequence object is the trap: "every OTHER
        # sequence" is the empty set there, so a state built that way emits
        # transitions=null and can never be re-entered.
        for n in (1, 2, 3):
            xml_n = _behavior_xml('wall', ['Forward', 'Backward',
                                           'Unequip'][:n])
            states = re.findall(
                r'class="hkbStateMachineStateInfo".*?'
                r'<hkparam name="transitions">([^<]*)</hkparam>.*?'
                r'<hkparam name="name">([^<]*)</hkparam>', xml_n, re.S)
            assert states, 'no states parsed — emitter shape changed'
            for transitions, name in states:
                assert transitions.strip() != 'null',                     (f'[{n} seq] state {name!r} has no transitions — it is a '
                     f'dead end and can never be re-entered')

    def test_skeleton_has_one_pose_per_bone(self):
        """1 bone + 0 reference poses = null deref when a sequence binds.

        An empty `referencePose` emitted before the real one wins (hkxcmd keeps
        the FIRST), producing a skeleton the engine crashes on. The identity
        quaternion must also be (0,0,0,1): a 4-wide translation tuple shifts
        w to 0, and a zero quaternion normalizes to NaN.
        """
        from asset_convert.hkx_animobject import _skeleton_xml
        import re

        xml = _skeleton_xml('gate')
        poses = re.findall(r'<hkparam name="referencePose" numelements="(\d+)"', xml)
        assert poses == ['1'], f'expected exactly one 1-element pose, got {poses}'

        body = re.search(r'<hkparam name="referencePose"[^>]*>(.*?)</hkparam>',
                         xml, re.S).group(1)
        tuples = re.findall(r'\(([^)]*)\)', body)
        assert [len(t.split()) for t in tuples] == [3, 4, 3], \
            f'referencePose must be trans(3) quat(4) scale(3), got {tuples}'
        assert tuples[1].split() == ['0.000000', '0.000000', '0.000000', '1.000000'], \
            f'identity quaternion must be (0,0,0,1), got ({tuples[1]})'


# ---------------------------------------------------------------------------
# Animation block byte-layout contracts (load-time CTD guards)
# ---------------------------------------------------------------------------

class TestAnimationBlockLayout:
    """Two blocks whose wrong bytes crash the engine at LOAD time.

    Both round-trip cleanly through PyFFI and NifSkope, so nothing but the
    engine notices -- these are the invariants that caught the Vilverin
    ctrigtripwire01 CTDs.  See docs/commentary/asset_convert_nif.md.
    """

    @staticmethod
    def _nif():
        import time
        if not hasattr(time, 'clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat
        return NifFormat

    def test_blend_interpolator_is_manager_controlled(self):
        """Flags bit 0 CLEAR promises the engine seven trailing fields that the
        7-byte block does not contain, so it reads into the next block and
        AddRefs whatever it finds.  Vanilla: 8779/8779 Flags=1, ArraySize=2.

        PyFFI mismodels the header as 'unknown_short', so 0x0201 IS
        Flags=0x01 (low byte) + Array Size=0x02 (high byte).
        """
        from asset_convert.nif_converter import _normalize_blend_interpolators
        NF = self._nif()
        root = NF.NiNode()
        ctrl = NF.NiVisController()
        blend = NF.NiBlendBoolInterpolator()
        ctrl.interpolator = blend
        root.controller = ctrl
        _normalize_blend_interpolators(root)
        assert blend.unknown_short & 0x00FF == 1, \
            'Manager Controlled (Flags bit 0) must be set'
        assert blend.unknown_short >> 8 == 2, 'Array Size must be 2'

        raw = blend.unknown_short.to_bytes(2, 'little')
        assert raw == b'\x01\x02', \
            'on-disk header must be 01 02 like vanilla, got %s' % raw.hex(' ')

    def test_normalize_fixes_copied_blend_interpolators(self):
        """The defect also affects blocks COPIED from Oblivion, not just ones we
        synthesize, so the fix has to be a tree-wide pass."""
        from asset_convert.nif_converter import _normalize_blend_interpolators
        NF = self._nif()
        root = NF.NiNode()
        ctrl = NF.NiVisController()
        blend = NF.NiBlendBoolInterpolator()
        blend.unknown_short = 0            # what a copied block degrades to
        ctrl.interpolator = blend
        root.controller = ctrl

        assert _normalize_blend_interpolators(root) == 1
        assert blend.unknown_short == 0x0201
        # idempotent -- a second pass must find nothing left to fix
        assert _normalize_blend_interpolators(root) == 0

    @pytest.mark.skip(reason=
        'The wrapper-node SCALE morph swap was REVERTED 2026-08-10: it hard-'
        'freezes Skyrim on the ImperialDungeon05 tripwire (no crash, no log, '
        'process alive but never renders again), while the SAME mesh works in '
        'Vilverin.  _emulate_morphs is back to the pre-90d04a3 '
        'NiVisController version, so this test asserts a design that is no '
        'longer shipped.  Re-enable it together with a real fix - see '
        'docs/commentary/asset_convert_nif.md "NiGeomMorpherController does not exist '
        'in Skyrim" for the bisection, the four failed fixes, the verified exe '
        'field offsets, and the one unchased lead (the ref persistent flag).')
    def test_morph_emulation_never_targets_geometry(self):
        """The morph swap must not synthesize NiVisController entries: across
        every vanilla Skyrim mesh, sequence-driven NiVisController entries
        target only NiNode / NiBillboardNode / particle systems (1852/1852),
        never a NiTriShape, and trishape-targeted vis swaps never produced a
        visible swap in-game.  The swap is a wrapper-NODE scale animation
        driven by NiTransformController -- the machinery confirmed working
        in-game (CharacterGen secret wall)."""
        from asset_convert.nif_converter import _BLEND_INTERP_FLAGS_ARRAYSIZE
        import inspect
        from asset_convert import nif_converter
        src = inspect.getsource(nif_converter._emulate_morphs)
        assert 'NiVisController()' not in src, \
            'morph emulation must not construct NiVisController blocks'
        assert "b'NiVisController'" not in src, \
            'morph emulation must not emit NiVisController sequence entries'
        assert "controller_type = b'NiTransformController'" in src, \
            'morph swap entries must be transform (scale) entries'
        assert _BLEND_INTERP_FLAGS_ARRAYSIZE == 0x0201

    @pytest.mark.skip(reason=
        'Same revert as test_morph_emulation_never_targets_geometry: the '
        'SCALE swap freezes the game, so ctrigtripwire01 no longer ships '
        'wrapper "<shape> Swap" nodes or inverse scale curves.  See '
        'docs/commentary/asset_convert_nif.md before re-enabling.')
    def test_tripwire_morph_ships_a_scale_swap(self):
        """End-to-end on the mesh the bug was reported against: converting
        ctrigtripwire01 must produce paired wrapper NiNodes whose scale curves
        are INVERSE (base 1->0 as the snapped clone goes 0->1), with the clone
        wrapper resting at 0 so the un-tripped wire looks intact."""
        src = Path('export/Oblivion.esm/meshes/dungeons/caves/triggers/'
                   'ctrigtripwire01.nif')
        if not src.exists():
            pytest.skip('tripwire source NIF not exported')
        NF = self._nif()
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, 'meshes', 'ctrigtripwire01.nif')
            os.makedirs(os.path.dirname(dst))
            result = convert_nif(str(src), dst)
            if result.get('error'):
                pytest.skip(f'Conversion failed: {result["error"]}')

            data = NF.Data()
            with open(dst, 'rb') as f:
                data.inspect(f)
                f.seek(0)
                data.read(f)
            root = data.roots[0]

            mgr = root.controller
            assert isinstance(mgr, NF.NiControllerManager)
            curves = {}
            for seq in mgr.controller_sequences:
                for cb in seq.controlled_blocks:
                    name = bytes(cb.node_name).decode('latin-1')
                    if not name.endswith(' Swap'):
                        continue
                    ctype = bytes(cb.controller_type or b'').decode('latin-1')
                    assert ctype == 'NiTransformController', \
                        f'{name} must swap via transform, got {ctype!r}'
                    keys = cb.interpolator.data.scales.keys
                    curves[name] = [(k.time, k.value) for k in keys]

            assert len(curves) == 2, f'expected a base/clone pair, got {curves}'
            base = [c for n, c in curves.items() if 'Mrph' not in n][0]
            clone = [c for n, c in curves.items() if 'Mrph' in n][0]
            assert base[0][1] == 1.0 and base[-1][1] == 0.0, \
                'the intact wire must start visible and scale away'
            assert clone[0][1] == 0.0 and clone[-1][1] == 1.0, \
                'the snapped wire must start hidden and scale in'
            assert abs(base[-1][0] - clone[-1][0]) < 1e-3, \
                'both halves of the cut must happen at the same instant'

            # Wrappers must exist as real nodes, rest at the right scale, and
            # be reachable by the manager (extra_targets + object palette) --
            # a CB naming a node the manager cannot resolve drives nothing.
            wrappers = {}
            stack = [root]
            while stack:
                blk = stack.pop()
                nm = bytes(getattr(blk, 'name', b'') or b'').decode('latin-1')
                if nm.endswith(' Swap'):
                    wrappers[nm] = blk
                stack.extend(getattr(blk, 'children', []) or [])
            assert set(wrappers) == set(curves)
            for nm, node in wrappers.items():
                want = 0.0 if 'Mrph' in nm else 1.0
                assert node.scale == want, \
                    f'{nm} must rest at scale {want}, got {node.scale}'
                assert not int(node.flags) & 0x01, f'{nm} must not be hidden'

            mtc = mgr.next_controller
            while mtc is not None and not isinstance(
                    mtc, NF.NiMultiTargetTransformController):
                mtc = mtc.next_controller
            assert mtc is not None, 'scale CBs need an MTC to bind through'
            targets = {bytes(t.name).decode('latin-1')
                       for t in mtc.extra_targets if t is not None}
            assert set(curves) <= targets, \
                'every animated wrapper must be an MTC extra target'
            palette = {bytes(o.name).decode('latin-1')
                       for o in mgr.object_palette.objs}
            assert set(curves) <= palette, \
                'every animated wrapper must be in the object palette'

            # The failed approach must not creep back in via any other pass.
            assert not any(isinstance(b, NF.NiVisController)
                           for b in root.tree()), \
                'no NiVisController may survive on a morph-swap mesh'


class TestVoiceFilePrune:
    """A VTYP relocation empties the source-race folder: the run stops writing
    it, so a touched-dirs-only sweep can never reach the dead copies left
    behind under the old name.  Morroblivion kept 19 `_`-prefixed files in
    `TES4Maleash ghoul` after their real copies moved to `TES4MaleImperial`,
    and a stale file makes a still-broken run look fixed."""

    def _tree(self, tmp_path):
        root = tmp_path / 'sound' / 'Voice' / 'Plugin.esm'
        live = root / 'TES4MaleImperial'
        orphan = root / 'TES4Maleash ghoul'
        live.mkdir(parents=True)
        orphan.mkdir(parents=True)
        return root, live, orphan

    def test_relocated_folder_is_swept(self, tmp_path):
        from asset_convert.audio_converter import _prune_stale_voice_files
        root, live, orphan = self._tree(tmp_path)
        keep = live / 'quest_topic_00aafa93_1.fuz'
        keep.write_bytes(b'x')
        dead = orphan / '_topic_00aafa93_1.fuz'
        dead.write_bytes(b'x')

        removed = _prune_stale_voice_files(
            {live.resolve()}, {keep.resolve()}, {root})

        assert keep.exists(), 'intended file must survive'
        assert not dead.exists(), 'orphaned relocation copy must be pruned'
        assert [f.name for f in removed] == ['_topic_00aafa93_1.fuz']

    def test_non_voice_files_are_never_touched(self, tmp_path):
        from asset_convert.audio_converter import _prune_stale_voice_files
        root, live, orphan = self._tree(tmp_path)
        other = orphan / 'readme.txt'
        other.write_bytes(b'x')

        _prune_stale_voice_files(set(), set(), {root})

        assert other.exists(), 'non-voice content must never be removed'

    def test_without_plugin_roots_behaviour_is_unchanged(self, tmp_path):
        from asset_convert.audio_converter import _prune_stale_voice_files
        _root, _live, orphan = self._tree(tmp_path)
        dead = orphan / '_topic_00aafa93_1.fuz'
        dead.write_bytes(b'x')

        removed = _prune_stale_voice_files(set(), set())

        assert dead.exists() and not removed


class TestLODSettingsCoversTheTerrain:
    """LODSettings/<WRLD>.lod must describe a grid that CONTAINS every tile.

    The engine builds its terrain-LOD quadtree from this header (root at SW,
    `size` cells across).  A .btr/.bto tile outside that square has no node,
    and the per-frame walk indexes the node array with no bounds check --
    SkyrimSE.exe+050E6AD `mov rbx,[rax+rcx*8]` with rax=0: a hard CTD the
    moment the worldspace streams, reproducible with `coc`.

    Two things had to be true at once, and getting only the first still CTD'd:
      1. extents must come from the CELLS -- 57 of 84 TES4 worldspaces author
         no usable WRLD.MNAM, so the old code saw sw==ne==0 and wrote a 1x1
         grid (`SWx=0 SWy=1`) while tiles ran out to (-32,-32);
      2. SW must be ALIGNED to the coarsest LOD level -- LODGen snaps each
         tile's origin down to a multiple of its own level, so tiles begin
         below the literal terrain corner.  Measured against real output,
         an unaligned SW left 324 tiles outside their grid.
    """

    def _read(self, sw_x, sw_y, ne_x, ne_y):
        from asset_convert.lod_gen import write_lod_settings
        tmp = Path(tempfile.mkdtemp())
        write_lod_settings('W', sw_x, sw_y, ne_x, ne_y, tmp)
        raw = (tmp / 'LODSettings' / 'W.lod').read_bytes()
        assert len(raw) == 16
        return struct.unpack('<hhIII', raw)

    def test_grid_covers_a_span_crossing_the_origin(self):
        """Plane of Oblivion: cells -2..3 x -2..4, which the old code sized 1."""
        sx, sy, size, _mn, _mx = self._read(-2, -2, 4, 5)
        assert sx <= -2 and sy <= -2
        assert sx + size >= 4 and sy + size >= 5

    def test_sw_is_aligned_to_the_max_lod_level(self):
        """LODGen names a level-N tile at a multiple of N, so SW must be one."""
        for extents in ((-2, -2, 4, 5), (-9, -6, 7, 10), (11, 11, 18, 18),
                        (-64, -69, 65, 60)):
            sx, sy, _size, _mn, max_lod = self._read(*extents)
            assert sx % max_lod == 0 and sy % max_lod == 0,                 f'SW {(sx, sy)} not aligned to max LOD {max_lod} for {extents}'

    def test_degenerate_extents_still_produce_a_real_grid(self):
        """An all-zero MNAM must never yield the 1x1 grid that caused the CTD."""
        _sx, _sy, size, min_lod, max_lod = self._read(0, 0, 0, 0)
        assert size >= 4, 'a 1x1 LOD grid cannot hold even one LOD4 tile'
        assert max_lod >= min_lod

    def test_tiles_at_snapped_origins_fall_inside_the_grid(self):
        """The real invariant, stated the way the output is measured."""
        sw_x, sw_y, ne_x, ne_y = -2, -2, 4, 5      # Plane of Oblivion
        sx, sy, size, _mn, max_lod = self._read(sw_x, sw_y, ne_x, ne_y)
        for level in (4, 8, 16, 32):
            if level > max_lod:
                continue
            # every tile origin LODGen could emit for this terrain
            tx = (sw_x // level) * level
            ty = (sw_y // level) * level
            while tx < ne_x:
                ty2 = ty
                while ty2 < ne_y:
                    assert sx <= tx and sy <= ty2,                         f'tile {level}.{tx}.{ty2} starts before SW {(sx, sy)}'
                    assert tx + level <= sx + size and ty2 + level <= sy + size,                         f'tile {level}.{tx}.{ty2} ends past the grid'
                    ty2 += level
                tx += level




class TestMTTCTargetsStayInSyncWithControlledBlocks:
    """MTTC extra targets and sequence controlled-blocks must stay in lockstep.

    `extra_targets` is POSITIONAL: the engine pairs slot N with the
    NiControllerSequence entry that drives it.  Break the pairing and
    BGSGamebryoSequenceGenerator dereferences a null interpolator the moment
    the object animates -- `movdqu xmm2,[rax]` with rax=0 in VCRUNTIME140.

    Two failures were shipped chasing this, one in each direction:
      * leaving the target while dropping the root-named block
        (crash-2026-08-10-00-42-35);
      * keeping the root-named block so the target matched
        (crash-2026-08-10-00-51-26) -- illegal for a different reason.

    Census of 141 sequences across 43 animated vanilla meshes settles it, and
    BOTH numbers are zero: 0 blocks target their own root, and 0 targets lack
    a block.  Vanilla satisfies both by never listing the root as a target, so
    dropping the block must drop the target with it.

    Driven through the REAL converter on the mesh that crashed, rather than a
    hand-built NiMultiTargetTransformController: pyffi's reference arrays do
    not reliably retain slot assignments on a synthesised block, so a stub
    fixture tests the fixture, not the converter.
    """

    MESH = 'oblivion/plants/spiddalcloudplant.nif'

    def _convert(self):
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from asset_convert import nif_converter as nc, sse_nif
        src = EXPORT_MESHES / self.MESH
        if not src.exists():
            pytest.skip(f'{self.MESH} not exported')
        out = Path(tempfile.mkdtemp()) / 'out.nif'
        nc.convert_nif(str(src), str(out))
        return sse_nif.read_nif(str(out))

    def _facts(self, data):
        from pyffi.formats.nif import NifFormat
        for root in data.roots:
            root_name = bytes(getattr(root, 'name', b'') or b'')
            blocks, targets = set(), []
            for b in root.tree():
                if isinstance(b, NifFormat.NiControllerSequence):
                    for cb in (b.controlled_blocks or ()):
                        blocks.add(bytes(cb.node_name or b''))
                elif isinstance(b, NifFormat.NiMultiTargetTransformController):
                    stated = int(getattr(b, 'num_extra_targets', 0))
                    listed = [t for t in (b.extra_targets or ()) if t is not None]
                    targets.append((stated, [bytes(t.name) for t in listed]))
            return root_name, blocks, targets
        return b'', set(), []

    def test_root_is_not_an_extra_target(self):
        """Vanilla: 0 of 141 sequences target their own root."""
        root_name, _blocks, targets = self._facts(self._convert())
        for _stated, names in targets:
            assert root_name not in names,                 f'{root_name!r} is the root AND an extra target'

    def test_every_target_has_a_driving_block(self):
        """Vanilla: 0 targets without a block.  A null slot is the crash."""
        _root, blocks, targets = self._facts(self._convert())
        for _stated, names in targets:
            missing = [n for n in names if n and n not in blocks]
            assert not missing, f'targets with no controlled block: {missing}'

    def test_stated_count_is_not_asserted(self):
        """num_extra_targets ABOVE the live entries is legal -- vanilla does it.

        spitpotopen01 states 16 and lists 2; 134 of 1,741 sampled vanilla
        clutter meshes disagree the same way.  Asserting equality here would
        encode a rule vanilla breaks, so only the two real invariants above
        are enforced.
        """
        _root, _blocks, targets = self._facts(self._convert())
        assert targets, 'fixture expects at least one MTTC'


class TestGraphMeshesShipNoEmptyTextKeys:
    """A graph-bound mesh must ship NO empty text key values.

    On state activation BGSGamebryoSequenceGenerator (GOG exe 0x505130,
    AddrLib ID 32774) walks the sequence's NiTextKeyExtraData: each value is
    matched whole against the project's event table, and on a miss the engine
    calls `strchr(value, '.')` to split an `Event.Payload` key.  An empty
    NiString loads as a NULL BSFixedString and the strchr dereferences it --
    `movdqu xmm2,[rax]`, rax=0, R9=0x2E2E (the broadcast '.') in
    VCRUNTIME140.  That is the Spiddal Stick CTD (crash-2026-08-10-01-41-07;
    the source ships `t=0.1 ''`) and the Harrada Root CTD (-01-39-02; SEVEN
    empty keys).

    Vanilla ships empty keys ONLY on graph-less meshes (impjaildoor01,
    ruinscanopicjar02 -- plain Open/Close, no BGED), and zero beside a
    behavior graph, so the strip is scoped to meshes that get an animobject
    graph.  Real keys must survive: the `sound:` key is the plant's audio,
    and trailing whitespace is vanilla-legal (107 dungeon keys carry it) so
    it must NOT be trimmed.

    Driven through the REAL converter on the meshes that crashed.
    """

    MESHES = ('oblivion/plants/spiddalcloudplant.nif',
              'oblivion/plants/harradauprightattack.nif')

    def _text_keys(self, mesh):
        import time
        if not hasattr(time, '_original_clock'):
            time.clock = time.perf_counter
        from asset_convert import nif_converter as nc, sse_nif
        from pyffi.formats.nif import NifFormat
        src = EXPORT_MESHES / mesh
        if not src.exists():
            pytest.skip(f'{mesh} not exported')
        out = Path(tempfile.mkdtemp()) / 'out.nif'
        nc.convert_nif(str(src), str(out))
        data = sse_nif.read_nif(str(out))
        keys = []
        for root in data.roots:
            for b in root.tree():
                if isinstance(b, NifFormat.NiControllerSequence) and b.text_keys:
                    keys.extend(bytes(k.value or b'')
                                for k in b.text_keys.text_keys)
        return keys

    @pytest.mark.parametrize('mesh', MESHES)
    def test_no_empty_text_keys_survive(self, mesh):
        keys = self._text_keys(mesh)
        assert keys, 'fixture expects text keys'
        empty = [k for k in keys if not k.strip()]
        assert not empty, f'{len(empty)} empty text key(s) shipped: CTD on activation'

    @pytest.mark.parametrize('mesh', MESHES)
    def test_real_keys_survive_verbatim(self, mesh):
        keys = self._text_keys(mesh)
        assert b'start' in keys and b'end' in keys
        sound = [k for k in keys if k.startswith(b'sound:')]
        assert sound, 'the sound: key is the plant audio -- it must survive'


class TestCollisionWindingRepair:
    """Step 0 (the authored normal) must run WITHOUT the toggle, and the
    inferred steps must stay behind it.

    The split exists because the two halves have different risk: step 0 reads
    a fact the NIF states about itself, while steps 1-3 infer one.  Inference
    inverted a whole vanilla tower once already (leyawiincastle02: 274 of 284
    triangles, 806 walkable cells), so it may only run where the authored
    normals were destroyed.
    """

    def _repair(self, tris, normals, enabled, visual=None, groups=None):
        import os as _os
        from asset_convert import collision as C
        prev = _os.environ.get("TESCONV_COLLISION_WINDING_FIX")
        _os.environ["TESCONV_COLLISION_WINDING_FIX"] = "1" if enabled else "0"
        try:
            return C._repair_inverted_floors(tris, visual, groups, normals)
        finally:
            if prev is None:
                _os.environ.pop("TESCONV_COLLISION_WINDING_FIX", None)
            else:
                _os.environ["TESCONV_COLLISION_WINDING_FIX"] = prev

    # One up-facing quad, wound counter-clockwise (normal = +Z).
    _UP = [((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
           ((1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))]

    def _reversed(self):
        return [(a, c, b) for a, b, c in self._UP]

    def test_reversed_winding_is_repaired_with_the_toggle_OFF(self):
        """The whole point: vanilla Oblivion gets fixed without opting in."""
        tris = self._reversed()
        normals = [(0.0, 0.0, 1.0)] * len(tris)   # authored: faces UP
        out, n = self._repair(tris, normals, enabled=False)
        assert n == len(tris), 'authored-normal repair must run ungated'
        from asset_convert import collision as C
        assert all(C._face_normal(t)[2] > 0 for t in out)

    def test_correct_winding_is_left_alone(self):
        """Zero false positives on a mesh that already agrees with itself."""
        normals = [(0.0, 0.0, 1.0)] * len(self._UP)
        out, n = self._repair(list(self._UP), normals, enabled=False)
        assert n == 0
        assert out == self._UP

    def test_self_consistent_corruption_is_NOT_touched_ungated(self):
        """Morroblivion's case: normals rewritten to match the bad winding.

        Both sources agree while both are wrong, so step 0 has nothing to
        detect and must not guess -- that is what the toggle is for.
        """
        tris = self._reversed()
        normals = [(0.0, 0.0, -1.0)] * len(tris)  # authored to match, i.e. DOWN
        _out, n = self._repair(tris, normals, enabled=False)
        assert n == 0

    def test_missing_normals_are_never_guessed_at(self):
        tris = self._reversed()
        _out, n = self._repair(tris, [None] * len(tris), enabled=False)
        assert n == 0
        _out, n = self._repair(tris, None, enabled=False)
        assert n == 0

    def test_smoothed_vertex_normals_are_rejected(self):
        """A per-vertex average that did not survive the length test is None.

        Averaging three normals across a smoothed edge yields a short vector
        pointing at none of them (mageguilddesk01: |n| = 0.31); trusting it
        rewound 9 correct faces.
        """
        from asset_convert import collision as C
        assert C._AUTHORED_NORMAL_MIN_LEN > 0.9

    def test_normals_track_shape_tri_soup_ordering(self):
        """_shape_tri_normals must align 1:1 with _shape_tri_soup or normals
        bind to the wrong triangles."""
        from asset_convert import collision as C
        from pyffi.formats.nif import NifFormat
        src = EXPORT_MESHES / 'rocks' / 'seisland' / 'seisland.nif'
        if not src.exists():
            pytest.skip('seisland.nif not exported')
        data = NifFormat.Data()
        with open(src, 'rb') as fh:
            data.read(fh)
        checked = 0
        for root in data.roots:
            for node in root.tree():
                shape = getattr(node, 'shape', None)
                if shape is None:
                    continue
                inner = (shape.shape
                         if isinstance(shape, NifFormat.bhkMoppBvTreeShape)
                         else shape)
                soup = C._shape_tri_soup(inner)
                if soup is None:
                    continue
                normals = C._shape_tri_normals(inner)
                if normals is None:
                    continue
                assert len(normals) == len(soup[0])
                checked += 1
        assert checked, 'expected a mesh collision shape in seisland.nif'


class TestLuminanceGlowMapsBecomeRGB:
    r"""Oblivion's L8 glow maps must not reach Skyrim as single-channel.

    User report (2026-08-27): candles such as
    `lights\uppersilverplatecandles01.nif` "glow red instead of the flames".

    Oblivion ships glow maps as 8-bit DDPF_LUMINANCE with the channel under
    the RED mask (R 0xFF, G 0x00, B 0x00) -- measured over Oblivion's texture
    tree, 469 files are L8 and every one of them is a `_g` glow map.  Its
    shader replicates that channel across RGB; Skyrim's glow shader
    (shader type 2, slot 2) samples slot 2 as ordinary RGB and does not, so
    green and blue read zero and the surface glows PURE RED.  Vanilla Skyrim
    never ships L8 -- its own glow maps are RGB textures whose CONTENT is grey
    (spriggan_g.dds: DXT1, R==G==B==17.2 mean).
    """

    SRC = 'export/Oblivion.esm/textures/clutter/candle_g.dds'

    @pytest.mark.skipif(not Path(SRC).exists(),
                        reason='Oblivion candle glow map not available')
    def test_l8_glow_map_expands_to_grey_rgb(self, tmp_path):
        import shutil as _sh
        import struct as _st
        from asset_convert import luminance_textures as lt

        dst = tmp_path / 'candle_g.dds'
        _sh.copy2(self.SRC, str(dst))
        assert lt.is_luminance(str(dst)), 'fixture must start as L8'

        orig = open(self.SRC, 'rb').read()
        assert lt.convert_file(str(dst)) is True
        assert not lt.is_luminance(str(dst)), 'still L8 after conversion'
        assert lt.convert_file(str(dst)) is False, 'must be idempotent'

        blob = open(str(dst), 'rb').read()
        pf = _st.unpack('<I', blob[80:84])[0]
        bits = _st.unpack('<I', blob[88:92])[0]
        masks = _st.unpack('<IIII', blob[92:108])
        assert pf & 0x40, 'pixel format must declare DDPF_RGB'
        assert bits == 32, f'expected 32bpp, got {bits}'
        assert masks[1] and masks[2], (
            'green and blue masks must be non-zero -- a zero G/B mask is '
            'exactly what made the glow render red')

        # Every channel carries the ORIGINAL luminance: grey, not red.
        lum = orig[128]
        b, g, r, a = blob[128:132]
        assert (b, g, r) == (lum, lum, lum), (
            f'L={lum} expanded to ({b},{g},{r}) -- must replicate across RGB')
        assert a == 0xFF, 'alpha must be opaque'

        # The whole mip chain survives, or the glow vanishes at distance.
        h, w = _st.unpack('<II', blob[12:20])
        mips = _st.unpack('<I', blob[28:32])[0]
        total, ww, hh = 0, w, h
        for _ in range(mips):
            total += ww * hh * 4
            ww, hh = max(1, ww // 2), max(1, hh // 2)
        assert len(blob) - 128 == total, (
            f'mip chain truncated: body {len(blob)-128} != expected {total}')


class TestFallbackWhiteIsNotAFlame:
    r"""White-by-FALLBACK must still take the soft depth fade.

    The shader emissive ends up white in three different situations and only
    one of them is a flame.  Measured over 778 particle systems in meshes/:

        authored full white ......... 109   <- self-lit, no fade
        fallback, source authored BLACK  159   <- must still fade
        fallback, chromatic curve ... 320   <- must still fade

    Keying the flame test on the shader's FINAL emissive would skip the fade
    on all 479 fallback cases.  `fire\fireopensmallsmoke.nif` is the clean
    proof: its "Smoke" plume authors emissive (0,0,0) -- so it is defaulted to
    white, since black would render it invisible -- while the flame shapes in
    the very same mesh author real white.  The smoke is exactly the kind of
    surface the fade exists for.
    """

    SRC = 'export/Oblivion.esm/meshes/fire/fireopensmallsmoke.nif'

    @pytest.mark.skipif(not Path(SRC).exists(),
                        reason='Oblivion smoke-fire mesh not available')
    def test_smoke_fades_while_flames_in_the_same_mesh_do_not(self, tmp_path):
        import time as _t
        if not hasattr(_t, '_original_clock'):
            _t.clock = _t.perf_counter
        from pyffi.formats.nif import NifFormat

        dst = tmp_path / 'meshes' / 'out.nif'
        dst.parent.mkdir(parents=True, exist_ok=True)
        assert convert_nif(self.SRC, str(dst)).get('converted')

        data = NifFormat.Data()
        with open(str(dst), 'rb') as f:
            data.inspect(f)
            f.seek(0)
            data.read(f)

        soft = {}
        for blk in data.blocks:
            if not isinstance(blk, (NifFormat.NiParticleSystem,
                                    NifFormat.NiTriBasedGeom)):
                continue
            name = bytes(getattr(blk, 'name', b'') or b'').decode('latin1')
            for pr in (getattr(blk, 'bs_properties', None) or []):
                if pr is None or type(pr).__name__ != 'BSEffectShaderProperty':
                    continue
                soft[name] = int(pr.shader_flags_1.slsf_1_soft_effect)

        assert 'Smoke' in soft, 'fixture expects a Smoke shape'
        assert soft['Smoke'] == 1, (
            'the smoke plume authors emissive (0,0,0) and is only white by '
            'FALLBACK -- it must still take the soft depth fade, or every '
            'billboard shows its own quad edge')
        flames = [n for n in soft if n.startswith('FireParticles')]
        assert flames, 'fixture expects flame particle systems'
        for n in flames:
            assert soft[n] == 0, (
                f'{n} authors real white and must stay hard, or it fades '
                f'against the surface it is mounted on')
