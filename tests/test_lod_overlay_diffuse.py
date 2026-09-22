"""A LOD mesh gets its OWN opaque copy of each detail-overlay diffuse.

Oblivion's `APPLY_HILIGHT2` marks a diffuse whose ALPHA is a per-texel BLEND
WEIGHT, not transparency. Two readers want incompatible things from it:

  * the full-size mesh still needs that channel as authored;
  * object LOD must not have it. LODGen stamps `slsf_2_lod_objects` on every
    baked shape and the LOD object shader samples diffuse alpha as OPACITY, so
    `RockGreatForest645` renders solid up close and see-through at distance.

Flattening in place destroys the blend weight, and shadowing a copy at the SAME
path from the LOD mod means one file serves both readers -- Data holds one file
per path, so the full mesh silently got the flattened copy too.

`redirect_overlay_diffuses` gives the LOD mesh a distinct `<name>_lod.dds` and
repoints slot 0 at it, so every file has exactly one reader. WHICH textures
those are is AUTHORED: mesh conversion records every APPLY_HILIGHT2 diffuse and
this reads that manifest back. Alpha alone cannot stand in for it -- a cutout
mask and a blend weight are both alpha.

See: docs/commentary/asset_convert_shader.md#detail-overlay-diffuses
"""


import struct

import pytest

from asset_convert.lod import lod_far_gen

OVERLAY = 'tes4/rocks/greatforestrock03.dds'
#: A DXT5 diffuse that is NOT an overlay -- alpha here is a real cutout mask.
MASK = 'tes4/trees/billboards/treedeodar.dds'
OVERLAYS = {OVERLAY}
BS = chr(92)

#: DDS header byte offsets used to build and inspect the fixtures.
_OFF_MIPS, _OFF_PF_FLAGS = 28, 80


@pytest.fixture
def nif():
    """The patched NifFormat, whose layouts the fixtures are built from."""
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    return NifFormat


def _dxt5(path, blocks=1):
    """Write a minimal DXT5 DDS: `blocks` 4x4 blocks, alpha half-transparent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    hdr = bytearray(128)
    hdr[0:4] = b'DDS '
    struct.pack_into('<I', hdr, 4, 124)
    struct.pack_into('<I', hdr, 8, 0x1 | 0x2 | 0x4 | 0x1000)
    struct.pack_into('<I', hdr, 12, 4)
    struct.pack_into('<I', hdr, 16, 4 * blocks)
    struct.pack_into('<I', hdr, _OFF_MIPS, 1)
    struct.pack_into('<I', hdr, 76, 32)
    struct.pack_into('<I', hdr, _OFF_PF_FLAGS, 0x4)
    hdr[84:88] = b'DXT5'
    struct.pack_into('<I', hdr, 108, 0x1000)
    alpha = bytes((128, 128, 0, 0, 0, 0, 0, 0))
    color = struct.pack('<HHI', 0x1234, 0x1234, 0)
    path.write_bytes(bytes(hdr) + (alpha + color) * blocks)


def _dxt1(path, blocks=1):
    """Write a minimal DXT1 DDS -- no alpha channel at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    hdr = bytearray(128)
    hdr[0:4] = b'DDS '
    struct.pack_into('<I', hdr, 4, 124)
    struct.pack_into('<I', hdr, 8, 0x1 | 0x2 | 0x4 | 0x1000)
    struct.pack_into('<I', hdr, 12, 4)
    struct.pack_into('<I', hdr, 16, 4 * blocks)
    struct.pack_into('<I', hdr, _OFF_MIPS, 1)
    struct.pack_into('<I', hdr, 76, 32)
    struct.pack_into('<I', hdr, _OFF_PF_FLAGS, 0x4)
    hdr[84:88] = b'DXT1'
    struct.pack_into('<I', hdr, 108, 0x1000)
    block = struct.pack('<HHI', 0x1234, 0x1234, 0)
    path.write_bytes(bytes(hdr) + block * blocks)


def _fourcc(path):
    """The DDS pixel format's FourCC, or None when it is uncompressed."""
    data = path.read_bytes()
    return (data[84:88]
            if struct.unpack_from('<I', data, _OFF_PF_FLAGS)[0] & 0x4
            else None)


def _nif_with(NifFormat, diffuse, raw=None):
    """(nif data, shader property) for a one-shape NIF using `diffuse`."""
    data = NifFormat.Data(version=0x14020007, user_version=12)
    shape = NifFormat.NiTriShape()
    prop = NifFormat.BSLightingShaderProperty()
    texset = NifFormat.BSShaderTextureSet()
    texset.num_textures = 9
    texset.textures.update_size()
    texset.textures[0] = (raw if raw is not None else
                          'textures' + BS
                          + diffuse.replace('/', BS)).encode('latin-1')
    prop.texture_set = texset
    shape.bs_properties.update_size()
    shape.bs_properties[0] = prop
    data.roots = [shape]
    return data, prop


def _slot0(prop):
    """The shader property's slot-0 diffuse path, as a str."""
    return bytes(prop.texture_set.textures[0]).decode('latin-1')


def _rock(tmp_path, name='greatforestrock03.dds'):
    """Path of the overlay fixture texture under a temp texture root."""
    return tmp_path / 'tes4' / 'rocks' / name


class TestLodDiffuseRel:
    def test_appends_the_lod_suffix(self):
        """The LOD copy sits beside the original, never replacing it."""
        assert lod_far_gen.lod_diffuse_rel(
            'tes4' + BS + 'a' + BS + 'rock.dds') == (
            'tes4' + BS + 'a' + BS + 'rock_lod.dds')


class TestRedirectOverlayDiffuses:
    def test_overlay_is_repointed_and_the_copy_is_written(self, tmp_path, nif):
        """Slot 0 moves to _lod.dds and that file appears, DXT1."""
        _dxt5(_rock(tmp_path))
        data, prop = _nif_with(nif, OVERLAY)
        assert lod_far_gen.redirect_overlay_diffuses(
            data, (tmp_path,), OVERLAYS) == 1
        assert _slot0(prop).lower().endswith('greatforestrock03_lod.dds')
        assert _fourcc(_rock(tmp_path, 'greatforestrock03_lod.dds')) == b'DXT1'

    def test_the_copy_is_written_to_the_lod_mod_only(self, tmp_path, nif):
        """The plugin's tree is read, never written: a BSA packs it earlier."""
        lod, plugin = tmp_path / 'lod', tmp_path / 'plugin'
        _dxt5(_rock(plugin))
        data, prop = _nif_with(nif, OVERLAY)
        assert lod_far_gen.redirect_overlay_diffuses(
            data, (lod, plugin), OVERLAYS) == 1
        assert _rock(lod, 'greatforestrock03_lod.dds').is_file()
        assert sorted(p.name for p in plugin.rglob('*.dds')) == [
            'greatforestrock03.dds']
        assert _slot0(prop).lower().endswith('greatforestrock03_lod.dds')

    def test_the_original_keeps_its_alpha(self, tmp_path, nif):
        """The full-size mesh still needs it as a detail blend weight."""
        src = _rock(tmp_path)
        _dxt5(src)
        before = src.read_bytes()
        data, _prop = _nif_with(nif, OVERLAY)
        lod_far_gen.redirect_overlay_diffuses(data, (tmp_path,), OVERLAYS)
        assert src.read_bytes() == before

    def test_a_diffuse_outside_the_manifest_is_left_alone(self, tmp_path, nif):
        """Alpha is not the gate: a cutout mask is DXT5 and must keep it."""
        mask = tmp_path / 'tes4' / 'trees' / 'billboards' / 'treedeodar.dds'
        _dxt5(mask)
        data, prop = _nif_with(nif, MASK)
        assert lod_far_gen.redirect_overlay_diffuses(
            data, (tmp_path,), OVERLAYS) == 0
        assert _slot0(prop).lower().endswith('treedeodar.dds')
        assert not mask.with_name('treedeodar_lod.dds').exists()

    def test_an_empty_manifest_redirects_nothing(self, tmp_path, nif):
        """A plugin that authored no overlays must not gain _lod copies."""
        _dxt5(_rock(tmp_path))
        data, prop = _nif_with(nif, OVERLAY)
        assert lod_far_gen.redirect_overlay_diffuses(data, (tmp_path,), set()) == 0
        assert _slot0(prop).lower().endswith('greatforestrock03.dds')

    def test_a_diffuse_with_no_alpha_is_left_alone(self, tmp_path, nif):
        """DXT1 has no alpha to drop, so there is nothing to redirect to."""
        _dxt1(_rock(tmp_path))
        data, prop = _nif_with(nif, OVERLAY)
        assert lod_far_gen.redirect_overlay_diffuses(
            data, (tmp_path,), OVERLAYS) == 0
        assert _slot0(prop).lower().endswith('greatforestrock03.dds')
        assert not _rock(tmp_path, 'greatforestrock03_lod.dds').exists()

    def test_absent_texture_leaves_the_path_alone(self, tmp_path, nif):
        """No file to copy means no redirect -- never a dangling reference."""
        data, prop = _nif_with(nif, OVERLAY)
        assert lod_far_gen.redirect_overlay_diffuses(
            data, (tmp_path,), OVERLAYS) == 0
        assert _slot0(prop).lower().endswith('greatforestrock03.dds')

    def test_every_mip_level_survives(self, tmp_path, nif):
        """The old LOD-local copy was written mipless; this one is not."""
        _dxt5(_rock(tmp_path), blocks=4)
        data, _prop = _nif_with(nif, OVERLAY)
        lod_far_gen.redirect_overlay_diffuses(data, (tmp_path,), OVERLAYS)
        blob = _rock(tmp_path, 'greatforestrock03_lod.dds').read_bytes()
        assert struct.unpack_from('<I', blob, _OFF_MIPS)[0] == 1
        assert len(blob) - 128 == 4 * 8

    def test_rerunning_reuses_the_existing_copy(self, tmp_path, nif):
        """A second pass must not rewrite a file another worker may be using."""
        _dxt5(_rock(tmp_path))
        data, _prop = _nif_with(nif, OVERLAY)
        lod_far_gen.redirect_overlay_diffuses(data, (tmp_path,), OVERLAYS)
        lod = _rock(tmp_path, 'greatforestrock03_lod.dds')
        stamp = lod.read_bytes()
        data2, prop2 = _nif_with(nif, OVERLAY)
        assert lod_far_gen.redirect_overlay_diffuses(
            data2, (tmp_path,), OVERLAYS) == 1
        assert lod.read_bytes() == stamp
        assert _slot0(prop2).lower().endswith('greatforestrock03_lod.dds')

    @pytest.mark.parametrize('raw', [
        'textures' + BS + 'tes4' + BS + 'rocks' + BS + 'GreatForestRock03.dds',
        'Textures/tes4/rocks/greatforestrock03.dds',
        'tes4' + BS + 'rocks' + BS + 'greatforestrock03.dds',
    ])
    def test_matches_however_the_nif_spells_the_path(self, tmp_path, nif, raw):
        """NIFs vary in case, separator and the `textures` prefix."""
        _dxt5(_rock(tmp_path))
        data, prop = _nif_with(nif, OVERLAY, raw=raw)
        assert lod_far_gen.redirect_overlay_diffuses(
            data, (tmp_path,), OVERLAYS) == 1
        assert _slot0(prop).lower().endswith('greatforestrock03_lod.dds')
