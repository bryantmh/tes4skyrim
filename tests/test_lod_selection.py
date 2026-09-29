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
    """A placed reference in cell (0, 0) of the test worldspace."""
    ref = {'form_id': fid, 'flags': flags, 'base_fid': base,
           'parent_wrld': WRLD, 'parent_cell': CELL,
           'x': x, 'y': y, 'z': 0.0, 'rx': 0.0, 'ry': 0.0, 'rz': 0.0,
           'scale': 1.0}
    ref.update(extra)
    return ref


def _parsed(stats, refs):
    """A `parse_esm`-shaped tuple: one worldspace, one cell."""
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
        """No listable reference -> no file, None."""
        out = tmp_path / 'AutoConvertLOD'
        stats = {0x11: _stat('tes4\\rocks\\rock01.nif', flags=0)}
        assert _write(gen, out, stats, [_ref(0xA2, 0x11)]) is None


class TestCaseBlindLookups:
    """Mixed-case source files (FR ships `X.NIF`, `Architecture\\`) are found."""

    def test_mesh_exists_ignores_case(self, gen, tmp_path):
        _mesh(tmp_path, 'tes4\\FR\\FRSkBridgeSmall.NIF')
        assert gen._mesh_exists('meshes\\tes4\\fr\\frskbridgesmall.nif',
                                tmp_path / 'meshes')

    def test_mixed_case_master_model_is_listed_with_its_authored_far(
            self, gen, tmp_path):
        """Old: model not found in the master tree -> base dropped silently."""
        out = tmp_path / 'AutoConvertLOD'
        (out / 'meshes').mkdir(parents=True)
        fr = tmp_path / 'FR'
        _mesh(fr, 'tes4\\FR\\FRSkBridgeSmall.NIF')
        _mesh(fr, 'tes4\\FR\\FRSkBridgeSmall_far.NIF')
        stats = {0x20: _stat('tes4\\FR\\FRSkBridgeSmall.NIF', edid='FRBridge')}
        txt = _write(gen, out, stats, [_ref(0xB1, 0x20)],
                     master_mesh_dirs=[fr])
        assert txt is not None
        row = _rows(txt)[0xB1]
        assert row[13] == 'meshes\\tes4\\fr\\frskbridgesmall_far.nif'
        assert (out / 'meshes/tes4/fr/frskbridgesmall_far.nif').is_file()
        gen._drop_staged_master_meshes()

    def test_authored_far_in_any_case_is_not_regenerated(self, tmp_path):
        """FR's `X_far.NIF` counts as authored for a lowercase `_far` path."""
        from asset_convert.lod import lod_far_gen
        _mesh(tmp_path, 'tes4\\FR\\FRSkBridgeSmall_far.NIF')
        assert lod_far_gen.has_authored_lod(
            tmp_path / 'meshes', 'tes4\\fr\\frskbridgesmall_far.nif')

    def test_find_texture_ignores_case(self, tmp_path):
        """A billboard shipped as `Trees\\Billboards\\Oak.DDS` is found."""
        from asset_convert.lod import lod_far_gen
        p = tmp_path / 'tex' / 'tes4' / 'Trees' / 'Billboards' / 'Oak.DDS'
        p.parent.mkdir(parents=True)
        p.write_bytes(b'DDS ')
        assert lod_far_gen.find_texture(
            [tmp_path / 'lod', tmp_path / 'tex'],
            'tes4\\trees\\billboards\\oak.dds') == p

    def test_no_flat_normal_over_a_masters_mixed_case_normal(self, gen,
                                                             tmp_path, capsys):
        """D1 #10: a lowercase .bto ref to FR's `Architecture\\X_n.dds`.

        Old: the master copy was missed, so a flat normal was written into the
        LOD mod at the same archive path, shadowing FR's real one.
        """
        bto_dir = tmp_path / 'bto'
        bto_dir.mkdir()
        (bto_dir / 'W.4.0.0.bto').write_bytes(
            b'\x00textures\\tes4\\architecture\\frwall01_n.dds\x00')
        master = tmp_path / 'FR' / 'textures'
        real = master / 'tes4' / 'Architecture' / 'FRWall01_n.dds'
        real.parent.mkdir(parents=True)
        real.write_bytes(b'DDS real')
        lod_tex = tmp_path / 'AutoConvertLOD' / 'textures'
        lod_tex.mkdir(parents=True)
        gen._fill_missing_lod_textures(bto_dir, lod_tex,
                                       master_tex_roots=[master])
        assert [p for p in lod_tex.rglob('*') if p.is_file()] == []
        assert 'Synthesized' not in capsys.readouterr().out

    def test_an_unmakeable_texture_leaves_no_empty_folders(self, gen, tmp_path,
                                                          capsys):
        """A missing diffuse with no source creates nothing (old: its folders)."""
        bto_dir = tmp_path / 'bto'
        bto_dir.mkdir()
        (bto_dir / 'W.4.0.0.bto').write_bytes(
            b'\x00textures\\tes4\\lodonly\\gone.dds\x00')
        lod_tex = tmp_path / 'AutoConvertLOD' / 'textures'
        lod_tex.mkdir(parents=True)

        gen._fill_missing_lod_textures(bto_dir, lod_tex)

        assert list(lod_tex.rglob('*')) == []
        assert '1 LOD textures missing' in capsys.readouterr().out


class TestAuthoredFarFromAnyPlugin:
    """A later plugin's hand-made `_far` beats deriving one from the owner's model."""

    def _derive(self, gen, monkeypatch, tmp_path, dirs, model):
        """Run `_derive_far_meshes`, recording what it asked to generate."""
        from asset_convert.lod import lod_far_gen
        asked = []
        monkeypatch.setattr(lod_far_gen, 'generate_missing_far_nifs',
                            lambda stats, d, referenced_models, **k:
                            asked.extend(referenced_models) or 0)
        out = tmp_path / 'AutoConvertLOD'
        (out / 'meshes').mkdir(parents=True, exist_ok=True)
        gen._derive_far_meshes({}, out, {model}, dirs)
        return out, asked

    def test_later_plugins_authored_far_is_staged_not_derived(
            self, gen, monkeypatch, tmp_path):
        """Old: owner Oblivion derived it; FR's `_far.nif` was never looked at."""
        obl, fr = tmp_path / 'Oblivion.esm', tmp_path / 'FR'
        model = 'tes4\\architecture\\skingrad\\skbridgesmall.nif'
        _mesh(obl, model)
        _mesh(fr, 'tes4\\Architecture\\skingrad\\skbridgesmall_far.nif')
        out = tmp_path / 'AutoConvertLOD'
        stale = _mesh(out, 'tes4\\architecture\\skingrad\\skbridgesmall_far.nif')
        stale.with_suffix('.nif.generated').write_text('generated\n')
        out, asked = self._derive(gen, monkeypatch, tmp_path, [obl, fr], model)
        assert asked == []
        assert not stale.with_suffix('.nif.generated').exists()
        assert stale.is_file()
        gen._drop_staged_master_meshes()

    def test_an_earlier_plugins_authored_far_does_not_beat_a_later_model(
            self, gen, monkeypatch, tmp_path):
        """FR overrides the full model: Oblivion's authored `_far` is stale."""
        obl, fr = tmp_path / 'Oblivion.esm', tmp_path / 'FR'
        model = 'tes4\\rocks\\rock01.nif'
        _mesh(obl, model)
        _mesh(obl, 'tes4\\rocks\\rock01_far.nif')
        _mesh(fr, model)
        _out, asked = self._derive(gen, monkeypatch, tmp_path, [obl, fr], model)
        assert asked == [model]


PLAYER = 0x00000014
PARENT_ON, PARENT_OFF = 0xC1, 0xC2


class TestSelectionRules:
    """Effective initial state through XESP, persistent Full LOD, effect meshes."""

    def _listed(self, gen, tmp_path, refs, models=('tes4\\rocks\\rock01.nif',)):
        """FormIDs `write_lodgen_input` lists; parents are non-LOD markers."""
        out = tmp_path / 'AutoConvertLOD'
        stats = {0x10 + i: _stat(m, edid='B%d' % i) for i, m in enumerate(models)}
        stats[0x30] = _stat('marker.nif', flags=0)
        for m in models:
            _mesh(out, m)
            _mesh(out, m[:-4] + '_far.nif')
        parents = [_ref(PARENT_ON, 0x30), _ref(PARENT_OFF, 0x30, flags=0x800)]
        txt = _write(gen, out, stats, parents + refs)
        return set(_rows(txt)) if txt else set()

    def test_game_start_state_decides(self, gen, tmp_path):
        """Old: every LOD-flagged ref was listed, whatever its enable state."""
        refs = [_ref(0xD1, 0x10),
                _ref(0xD2, 0x10, flags=0x800),
                _ref(0xD3, 0x10, xesp=(PARENT_ON, 0)),
                _ref(0xD4, 0x10, xesp=(PARENT_ON, 1)),
                _ref(0xD5, 0x10, xesp=(PARENT_OFF, 1)),
                _ref(0xD6, 0x10, xesp=(PARENT_OFF, 0)),
                _ref(0xD7, 0x10, flags=0x8800, xesp=(PARENT_OFF, 0)),
                _ref(0xD8, 0x10, flags=0x800, xesp=(PARENT_ON, 0)),
                _ref(0xD9, 0x10, xesp=(PLAYER, 0)),
                _ref(0xDA, 0x10, xesp=(0xEE, 0)),
                _ref(0xDB, 0x10, xesp=(0xD5, 0))]
        assert self._listed(gen, tmp_path, refs) == {
            0xD1, 0xD3, 0xD5, 0xD8, 0xD9, 0xDA, 0xDB}

    def test_persistent_full_lod_is_left_to_the_engine(self, gen, tmp_path):
        """R4: persistent + Is Full LOD dropped; either flag alone kept."""
        refs = [_ref(0xE1, 0x10, flags=0x10400),
                _ref(0xE2, 0x10, flags=0x10000),
                _ref(0xE3, 0x10, flags=0x400)]
        assert self._listed(gen, tmp_path, refs) == {0xE2, 0xE3}

    def test_non_vwd_effect_mesh_is_dropped(self, gen, tmp_path):
        """R3': NDCloudLayer-style effect meshes, unless the ref is VWD."""
        models = ('tes4\\effects\\ndcloudlayer.nif',
                  'tes4\\dungeons\\misc\\fx\\fxmist01.nif',
                  'tes4\\rocks\\effectsrock.nif')
        refs = [_ref(0xF1, 0x10, flags=0x400), _ref(0xF2, 0x11),
                _ref(0xF3, 0x10, flags=0x8000), _ref(0xF4, 0x12)]
        assert self._listed(gen, tmp_path, refs, models) == {0xF3, 0xF4}

    def test_counts_and_top_models_are_logged(self, gen, tmp_path, capsys):
        """No silent drop: each rule prints its count and models."""
        self._listed(gen, tmp_path, [_ref(0xD2, 0x10, flags=0x800),
                                     _ref(0xDA, 0x10, xesp=(0xEE, 0))])
        log = capsys.readouterr().out
        assert '1 dropped by per-reference rules' in log
        assert 'disabled at game start' in log
        assert 'tes4\\rocks\\rock01.nif x1' in log
        assert 'enable parent not scanned (kept): 1' in log

    def test_prefetch_skips_a_base_whose_only_ref_is_dropped(self, gen):
        """`_screenable_mesh_paths` filters with the same rules."""
        stats = {0x10: _stat('tes4\\rocks\\rock01.nif'),
                 0x11: _stat('tes4\\rocks\\rock02.nif')}
        refs = [_ref(0xD2, 0x10, flags=0x800), _ref(0xD1, 0x11)]
        scope = gen._Scope({CELL: WRLD}, WRLD, None,
                           {r['form_id']: r for r in refs}, {})
        paths = gen._screenable_mesh_paths(refs, stats, scope)
        assert 'tes4\\rocks\\rock02.nif' in paths
        assert 'tes4\\rocks\\rock01.nif' not in paths


class TestEnableParentChain:
    """The game-start state of a chain is the same whichever ref is asked first.

    A depth cap measured from the entry point memoised None for a ref deep in
    one walk that a shorter walk would have answered: in a 21-ref chain R17
    alone was True, but None once R0 had been walked. The walk now has no
    cap; only a missing parent or a cycle is unknown.
    """

    @staticmethod
    def _chain(gen, n, opposite=0):
        """A scope over refs 0x100..0x100+n-1, each enabled by the next; the last plain."""
        refs = [_ref(0x100 + i, 0x10, xesp=(0x101 + i, opposite))
                for i in range(n - 1)] + [_ref(0x100 + n - 1, 0x10)]
        return gen._Scope({CELL: WRLD}, WRLD, None,
                          {r['form_id']: r for r in refs}, {})

    def test_walk_order_does_not_change_a_deep_refs_state(self, gen):
        """R17 is True asked alone and after R0 (old: None after R0)."""
        alone = self._chain(gen, 21)
        after = self._chain(gen, 21)

        gen._initially_enabled(0x100, after)

        assert gen._initially_enabled(0x111, alone) is True
        assert gen._initially_enabled(0x111, after) is True

    def test_a_long_chain_is_resolved(self, gen):
        """40 links with the opposite bit alternate the state all the way down."""
        scope = self._chain(gen, 40, opposite=1)

        assert gen._initially_enabled(0x100, scope) is (39 % 2 == 0)
        assert gen._initially_enabled(0x101, scope) is (38 % 2 == 0)

    def test_a_cycle_is_unknown_from_every_entry(self, gen):
        """A -> B -> A never terminates; both, and a ref hanging off them, are None."""
        refs = [_ref(0x201, 0x10, xesp=(0x202, 0)), _ref(0x202, 0x10, xesp=(0x201, 1)),
                _ref(0x203, 0x10, xesp=(0x201, 0))]
        for first in (0x201, 0x202, 0x203):
            scope = gen._Scope({CELL: WRLD}, WRLD, None,
                               {r['form_id']: r for r in refs}, {})
            gen._initially_enabled(first, scope)
            assert [gen._initially_enabled(f, scope)
                    for f in (0x201, 0x202, 0x203)] == [None, None, None]
