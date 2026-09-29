"""LODSettings/<WRLD>.lod covers the source game's own LOD tiles, not only its cells.

Oblivion's TES4Tamriel LOD tiles reach x = -96 while its LAND cells stop at
-64; a grid built from cells alone leaves the rebuilt terrain there nodeless.
"""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.lod import lod_gen

WRLD = 0x0100003C


@pytest.fixture
def bake(monkeypatch, tmp_path):
    """Run generate_lod on synthetic records with every heavy stage stubbed."""
    cells = {0x10 + i: {'parent_wrld': WRLD, 'grid_x': x, 'grid_y': y}
             for i, (x, y) in enumerate([(-64, -69), (69, 59)])}
    worldspaces = {WRLD: {'edid': 'TES4Tamriel', 'sw_x': 0, 'sw_y': 0,
                          'ne_x': 0, 'ne_y': 0}}
    monkeypatch.setattr(lod_gen, 'parse_esm_cached',
                        lambda p: (worldspaces, cells, {}, []))
    monkeypatch.setattr(lod_gen, '_derive_far_meshes', lambda *a, **k: None)
    monkeypatch.setattr(lod_gen, 'write_lodgen_input', lambda *a, **k: None)
    monkeypatch.setattr(lod_gen, '_fill_missing_lod_textures',
                        lambda *a, **k: None)

    def run(**kw):
        """The .lod written, as (sw_x, sw_y, size, min, max)."""
        out = tmp_path / 'AutoConvertLOD'
        lod_gen.generate_lod(tmp_path / 'Oblivion.esm', out, 'TES4Tamriel',
                             **kw)
        raw = (out / 'LODSettings' / 'TES4Tamriel.lod').read_bytes()
        return struct.unpack('<hhIII', raw)
    return run


def _export(tmp_path):
    """An export record dir shipping Tamriel (decimal 60) LOD tiles, mixed case."""
    rd = tmp_path / 'export' / 'Oblivion.esm'
    lod = rd / 'Meshes' / 'Landscape' / 'LOD'
    lod.mkdir(parents=True)
    for x, y in ((-96, 32), (64, 64), (0, -96)):
        (lod / f'60.{x}.{y}.32.nif').write_bytes(b'NIF')
    (rd / 'WRLD.txt').write_text('FormID=0000003C\nEditorID=Tamriel\n',
                                 encoding='utf-8')
    return rd


def test_source_tiles_widen_the_grid(bake, tmp_path):
    """Old: (-64, -96, 256) from the cells alone; tiles at x -96 had no node."""
    assert bake(source_record_dirs=[_export(tmp_path)]) == (-96, -96, 256,
                                                           4, 32)


def test_without_source_tiles_the_cells_decide(bake):
    """No export dirs: the cell-derived grid is unchanged."""
    assert bake() == (-64, -96, 256, 4, 32)
