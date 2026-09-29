"""Which references reach the LODGen input, and how their rows are written.

`write_lodgen_input` is driven with a synthetic `_parsed` tuple and a tmp
mesh tree; LODGEN_EXE points into tmp_path because the input file is written
next to the exe.  The NIF header reader is stubbed ("a file that exists is a
safe NiNode root") so the tests exercise selection, not the parser.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.lod import lod_gen

WRLD = 0x0000003C
CELL = 0x00000100
LOD = 0x00008000


@pytest.fixture
def gen(monkeypatch, tmp_path):
    """lod_gen with the exe in tmp_path, a fresh screen cache and a stub reader."""
    exe_dir = tmp_path / 'lodgen'
    exe_dir.mkdir()
    monkeypatch.setattr(lod_gen, 'LODGEN_EXE', exe_dir / 'LODGenx64.exe')
    monkeypatch.setattr(lod_gen, '_NIF_ROOT_SAFE_CACHE', {})
    monkeypatch.setattr(lod_gen, '_root_is_ninode',
                        lambda full: Path(full).exists())
    return lod_gen


def _mesh(out, rel):
    """Create an (empty) mesh file at backslash path `rel` under `out/meshes`."""
    p = out / 'meshes' / Path(*rel.split('\\'))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b'NIF')
    return p


def _stat(model, flags=LOD, edid='Rock01', sig='STAT'):
    return {'edid': edid, 'sig': sig, 'flags': flags, 'model': model,
            'obnd': (0, 0, 0, 100, 100, 100),
            'lod4': '', 'lod8': '', 'lod16': ''}


def _ref(fid, base, flags=0, x=100.0, y=100.0, **extra):
    ref = {'form_id': fid, 'flags': flags, 'base_fid': base,
           'parent_wrld': WRLD, 'parent_cell': CELL,
           'x': x, 'y': y, 'z': 0.0, 'rx': 0.0, 'ry': 0.0, 'rz': 0.0,
           'scale': 1.0}
    ref.update(extra)
    return ref


def _parsed(stats, refs):
    worldspaces = {WRLD: {'edid': 'TES4Tamriel', 'sw_x': -2, 'sw_y': -2,
                          'ne_x': 2, 'ne_y': 2}}
    cells = {CELL: {'parent_wrld': WRLD, 'grid_x': 0, 'grid_y': 0}}
    return worldspaces, cells, stats, refs


def _rows(path):
    """{ref FormID: row fields} from a written LODGen input."""
    rows = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if '\t' in line:
            f = line.split('\t')
            rows[int(f[0], 16)] = f
    return rows


def _write(gen, out, stats, refs, **kw):
    return gen.write_lodgen_input(Path('x.esm'), out, 'TES4Tamriel',
                                  _parsed=_parsed(stats, refs),
                                  cell_sw=(-32, -32), **kw)


class TestInputShape:

    def test_rows_header_and_basic_filters(self, gen, tmp_path):
        """LOD base listed; non-LOD base, foreign worldspace, no-base dropped."""
        out = tmp_path / 'AutoConvertLOD'
        _mesh(out, 'tes4\\rocks\\rock01.nif')
        _mesh(out, 'tes4\\rocks\\rock01_far.nif')
        stats = {0x10: _stat('tes4\\rocks\\rock01.nif'),
                 0x11: _stat('tes4\\rocks\\rock01.nif', flags=0)}
        refs = [_ref(0xA1, 0x10, rz=1.0),
                _ref(0xA2, 0x11),
                _ref(0xA3, 0x10, parent_wrld=0x99, parent_cell=0x98),
                _ref(0xA4, 0x77)]
        txt = _write(gen, out, stats, refs)
        head = Path(txt).read_text(encoding='utf-8').splitlines()[:5]
        assert head[0] == 'GameMode=TES5'
        assert head[2] == 'CellSW=-32 -32'
        assert head[3].endswith('\\')
        rows = _rows(txt)
        assert set(rows) == {0xA1}
        r = rows[0xA1]
        assert r[7] == '57.2958'
        assert r[9] == 'Rock01'
        assert r[12] == 'meshes\\tes4\\rocks\\rock01.nif'
        assert r[13] == 'meshes\\tes4\\rocks\\rock01_far.nif'
        assert Path(txt).parent == tmp_path / 'lodgen'

    def test_nothing_listed_returns_none(self, gen, tmp_path):
        out = tmp_path / 'AutoConvertLOD'
        stats = {0x11: _stat('tes4\\rocks\\rock01.nif', flags=0)}
        assert _write(gen, out, stats, [_ref(0xA2, 0x11)]) is None
