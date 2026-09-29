"""Shipped-LOD detection reads the asset tree, case-blind.

`shipped_lod_worldspaces` / `edid_keyed_lod_tiles` used to scan the RECORD dir
with an exact lower-case path. An imported (nested) mod keeps its records one
level below its assets, and ships the LOD folder in its source spelling --
Frostcrag Reborn ships `meshes/landscape/LOD/60.00.32.32.NIF` (upper-case) --
so its shipped LOD was dropped and it looked like it shipped none. The scan now
resolves the record dir to its asset dir (`assets_for`) and matches each LOD
folder case-blind (`case_paths.variants`).

See: docs/commentary/tes5_import_mod_merge.md#export-root-resolution
"""

from asset_convert.lod import terrain_lod
from asset_convert.lod.terrain_lod_falloutnv import edid_keyed_lod_tiles


def _nested(root, mod, plugin, wrlds, *, lod='LOD', tiles=(), fnv_dirs=(),
            masters=()):
    """A nested imported mod: records under root/mod/plugin, assets under root/mod.

    `sources.json` in `root` marks the export root so `assets_for` recognises the
    nested layout. `tiles` are flat Oblivion-style files under
    `meshes/landscape/<lod>`; `fnv_dirs` are {edid: file_count} FO3/FNV-style
    per-worldspace directories under the same folder. Returns the RECORD dir.
    """
    (root / 'sources.json').write_text('{}')
    asset = root / mod
    rec = asset / plugin
    rec.mkdir(parents=True)
    (rec / 'WRLD.txt').write_text(''.join(
        f'FormID={fid:08X}\nEditorID={edid}\n' for fid, edid in wrlds))
    (rec / '_HEADER.txt').write_text(
        ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters)))
    folder = asset / 'meshes' / 'landscape' / lod
    folder.mkdir(parents=True)
    for name in tiles:
        (folder / name).write_bytes(b'')
    for edid, n in dict(fnv_dirs).items():
        wsd = folder / edid
        wsd.mkdir()
        for i in range(n):
            (wsd / f'x{i}y0.dds').write_bytes(b'')
    return rec


def test_nested_mod_upper_case_lod_is_detected(tmp_path):
    """Frostcrag Reborn's shape: nested records, upper-case LOD one level up."""
    rec = _nested(tmp_path, 'Frostcrag Reborn 4.1.5 noUOP',
                  'DLCFrostcragReborn.esp', [(0x3C, 'Tamriel')],
                  lod='LOD', tiles=['60.00.32.32.NIF'])

    assert terrain_lod.shipped_lod_worldspaces(rec) == [('TES4Tamriel', 0x3C)]


def test_nested_mod_lower_case_lod_still_detected(tmp_path):
    """The same nested layout with a lower-case folder is unaffected."""
    rec = _nested(tmp_path, 'SomeMod', 'Some.esp', [(0x3C, 'Tamriel')],
                  lod='lod', tiles=['60.00.32.32.nif'])

    assert terrain_lod.shipped_lod_worldspaces(rec) == [('TES4Tamriel', 0x3C)]


def test_flat_single_plugin_export_is_unchanged(tmp_path):
    """A non-nested export (records and assets in one folder) still resolves.

    Guards against the asset-dir redirect over-reaching: `assets_for` must return
    the folder unchanged when it is not nested.
    """
    d = tmp_path / 'Oblivion.esm'
    lod = d / 'meshes' / 'landscape' / 'lod'
    lod.mkdir(parents=True)
    (d / 'WRLD.txt').write_text('FormID=0000003C\nEditorID=Tamriel\n')
    (d / '_HEADER.txt').write_text('')
    (lod / '60.0.0.32.nif').write_bytes(b'')

    assert terrain_lod.shipped_lod_worldspaces(d) == [('TES4Tamriel', 0x3C)]


def test_nested_fo3fnv_edid_keyed_lod_is_detected(tmp_path):
    """FO3/FNV per-worldspace LOD dirs, nested + upper-case, are counted."""
    rec = _nested(tmp_path, 'SomeFNVMod', 'SomeFNV.esp', [(0x40, 'WastelandNV')],
                  lod='LOD', fnv_dirs={'WastelandNV': 3})

    assert dict(edid_keyed_lod_tiles(rec)) == {'wastelandnv': 3}
