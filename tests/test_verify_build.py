"""The release gate: each check's verdict, its denominator, and its refusals."""

import argparse
import datetime as dt
import json
import os
import struct
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.release import gate_terrain as gt
from tools.release import verify_build as vb

LOG = """preamble line
=== 2026-09-29 10:00:00 convert Oblivion.esm
  Race skin tones: 12 races resolved from authored textures
  Magic effect phase meshes: 40 written from 21 models (0 not converted)
[Oblivion.esm] Compilation: 10/10 succeeded, 0 failed
NullReferenceException in an early per-plugin bake
=== 2026-09-29 10:20:00 convert Knights.esp
  Race skin tones: 3 races resolved from authored textures
[Knights.esp] Compilation: 5/5 succeeded, 0 failed
=== 2026-09-29 10:30:00 create_lod
{body}
=== 2026-09-29 10:40:00 ALL DONE
"""


def _ctx(tmp_path, log_body='', **kw):
    """A context over a log in tmp_path, empty trees, a run start of 2026-09-02."""
    log = tmp_path / 'rebuild.out'
    log.write_text(LOG.format(body=log_body), encoding='utf-8')
    sections = vb.read_log(log)
    out = tmp_path / 'output'
    out.mkdir(exist_ok=True)
    ctx = argparse.Namespace(
        mode='pre', sections=sections, start=dt.datetime(2026, 9, 2), log=log,
        output=out, export=tmp_path / 'export', lod=out / 'AutoConvertLOD',
        plugins=vb.plugins_in(sections), skyrim_data=None, source_esm=None,
        worldspaces=['TES4Tamriel'], allow_missing=frozenset(),
        deploy=[], zips=[], min_bto={'TES4Tamriel': 2}, cache={})
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def _lodgen_file(tmp_path, rows: int) -> Path:
    """A LODGen input with a 5-line header and `rows` 13-field rows."""
    p = tmp_path / 'LODGen TES4Tamriel.txt'
    head = ['GameMode=TES5', 'Worldspace=TES4Tamriel', 'CellSW=-96 -96',
            'PathData=x\\', 'PathOutput=y']
    p.write_text('\n'.join(head + ['\t'.join(['0'] * 16)] * rows) + '\n',
                 encoding='utf-8')
    inside = dt.datetime(2026, 9, 29, 10, 35).timestamp()
    os.utime(p, (inside, inside))
    return p


class TestLog:

    def test_sections_start_and_plugins(self, tmp_path):
        """Stamps split the log; the first stamp is the run start."""
        ctx = _ctx(tmp_path)
        assert [s.label for s in ctx.sections][:3] == [
            'preamble', 'convert Oblivion.esm', 'convert Knights.esp']
        assert vb.run_start(ctx.sections) == dt.datetime(2026, 9, 29, 10, 0)
        assert ctx.plugins == ['Oblivion.esm', 'Knights.esp']
        assert vb.final_section(ctx.sections, 'create_lod').label == \
            'create_lod'


class TestSourceAgnostic:

    def test_na_never_fails_the_gate(self):
        """N/A is a status of its own and, like INFO, never blocks."""
        na = vb.not_applicable('G12', 'no scripts')
        assert na['status'] == vb.NA
        assert vb.verdict([na, vb.result('G1', vb.PASS, '1', '')]) == vb.PASS
        assert vb.verdict([na, vb.result('G1', vb.FAIL, '1', '')]) == vb.FAIL

    def test_worldspaces_come_from_the_final_bake(self, tmp_path):
        """Only the last create_lod step's LODGen inputs name worldspaces."""
        body = ('  LODGen input: C:\\x\\LODGen SEWorld.txt (5 references)\n'
                '  LODGen input: /x/LODGen My World.txt (2 references)')
        ctx = _ctx(tmp_path, body)
        ctx.sections[1].lines.append(
            '  LODGen input: /x/LODGen Early.txt (1 references)')
        assert vb.worldspaces_in(ctx.sections) == ['SEWorld', 'My World']

    def test_since_starts_the_run_later(self, tmp_path):
        """--since drops sections stamped earlier; the run starts after it."""
        log = tmp_path / 'rebuild.out'
        log.write_text(LOG.format(body=''), encoding='utf-8')
        since = dt.datetime(2026, 9, 29, 10, 20)
        sections = vb.read_log(log, since)
        assert vb.run_start(sections) == since
        assert vb.plugins_in(sections) == ['Knights.esp']

    def test_default_output_is_the_configured_one(self, monkeypatch):
        """The output root follows the install config's outputDir."""
        import source_paths
        from output_layout import REPO_ROOT
        monkeypatch.setattr(source_paths, 'load_config',
                            lambda *a: {'outputDir': 'elsewhere'})
        assert vb.default_roots()[0] == REPO_ROOT / 'elsewhere'

    def test_tree_alpha_reads_every_worldspace(self, tmp_path):
        """G8 counts billboard tiles in each listed worldspace."""
        ctx = _ctx(tmp_path, worldspaces=['A', 'B'])
        for ws in ('A', 'B'):
            objs = ctx.lod / 'meshes' / 'terrain' / ws / 'Objects'
            objs.mkdir(parents=True)
            (objs / f'{ws}.4.0.0.bto').write_bytes(b'trees\\billboards\\x')
        assert vb.check_tree_alpha(ctx)['denominator'].startswith('2 tiles')


class TestLogChecks:

    def test_races_every_line_positive(self, tmp_path):
        """G4 passes 2/2, fails a zero, refuses with no line at all."""
        assert vb.check_races(_ctx(tmp_path))['status'] == vb.PASS
        ctx = _ctx(tmp_path)
        ctx.sections[2].lines[0] = '  Race skin tones: 0 races resolved'
        r = vb.check_races(ctx)
        assert (r['status'], r['denominator']) == (vb.FAIL, '1/2 plugin lines')
        for s in ctx.sections:
            s.lines[:] = [x for x in s.lines if 'Race skin' not in x]
        assert vb.check_races(ctx)['status'] == vb.REFUSE

    def test_compile_needs_every_plugin(self, tmp_path):
        """G12: a plugin with no Compilation line fails the check."""
        assert vb.check_compile(_ctx(tmp_path))['status'] == vb.PASS
        ctx = _ctx(tmp_path, plugins=['Oblivion.esm', 'Knights.esp', 'FR.esp'])
        r = vb.check_compile(ctx)
        assert (r['status'], r['denominator']) == (vb.FAIL, '2/3 plugins')

    def test_lodgen_reads_only_the_final_bake(self, tmp_path):
        """G11: the early NullReference does not count; tile minimum does."""
        objs = tmp_path / 'output/AutoConvertLOD/meshes/terrain/TES4Tamriel/Objects'
        objs.mkdir(parents=True)
        for i in range(2):
            (objs / f'TES4Tamriel.4.{i}.0.bto').write_bytes(b'x')
        assert vb.check_lodgen(_ctx(tmp_path))['status'] == vb.PASS
        died = _ctx(tmp_path, 'NullReferenceException at LODGen')
        assert vb.check_lodgen(died)['status'] == vb.FAIL
        (objs / 'TES4Tamriel.4.1.0.bto').unlink()
        assert vb.check_lodgen(_ctx(tmp_path))['status'] == vb.FAIL

    def test_lod_rows_two_instruments(self, tmp_path):
        """G9: recount == printed passes; drift fails; after a retry it warns."""
        f = _lodgen_file(tmp_path, 3)
        line = f'  LODGen input: {f} (3 references)'
        sel = '  Object-LOD selection: 3 reference(s) listed, 1 dropped'
        assert vb.check_lod_rows(_ctx(tmp_path, sel + '\n' + line))[
            'status'] == vb.PASS
        drift = line.replace('(3 ', '(4 ')
        assert vb.check_lod_rows(_ctx(tmp_path, drift))['status'] == vb.FAIL
        retry = drift + '\n  LodGen died ... Re-running without X (attempt 2).'
        assert vb.check_lod_rows(_ctx(tmp_path, retry))['status'] == vb.WARN
        bad_sel = sel.replace(': 3 ', ': 2 ') + '\n' + line
        assert vb.check_lod_rows(_ctx(tmp_path, bad_sel))['status'] == vb.FAIL
        assert vb.check_lod_rows(_ctx(tmp_path))['status'] == vb.REFUSE

    def test_lod_rows_stale_file_fails(self, tmp_path):
        """A LODGen input older than the run is someone else's."""
        f = _lodgen_file(tmp_path, 3)
        old = dt.datetime(2026, 9, 1).timestamp()
        os.utime(f, (old, old))
        ctx = _ctx(tmp_path, f'  LODGen input: {f} (3 references)')
        assert vb.check_lod_rows(ctx)['status'] == vb.FAIL

    def test_lod_rows_file_from_a_later_run_fails(self, tmp_path):
        """Written after the create_lod step ended (the next stamp) -> a later run's file."""
        f = _lodgen_file(tmp_path, 3)
        ctx = _ctx(tmp_path, f'  LODGen input: {f} (3 references)')
        assert vb.check_lod_rows(ctx)['status'] == vb.PASS
        later = dt.datetime(2026, 9, 29, 10, 40, 2).timestamp()
        os.utime(f, (later, later))
        assert vb.check_lod_rows(ctx)['status'] == vb.FAIL

    def test_last_step_window_ends_at_the_log_write(self, tmp_path):
        """The final step has no next stamp: its window ends at the log's mtime."""
        ctx = _ctx(tmp_path)
        end = dt.datetime(2026, 9, 29, 11, 0).timestamp()
        os.utime(ctx.log, (end, end))
        last = ctx.sections[-1]
        assert vb.section_window(ctx.sections, last, ctx.log) == (
            last.start.timestamp(), end + 1.0)


class TestFileChecks:

    def test_census_fails_on_file_collision_only(self, tmp_path):
        """G1: twin folders warn, two files on one archive path fail."""
        ctx = _ctx(tmp_path)
        tree = ctx.output / 'Plugin.esp'
        (tree / 'textures' / 'a').mkdir(parents=True)
        (tree / 'textures' / 'a' / 'x.dds').write_bytes(b'1')
        assert vb.check_census(ctx)['status'] == vb.PASS
        (tree / 'textures' / 'A').mkdir()
        assert vb.check_census(ctx)['status'] == vb.WARN
        (tree / 'textures' / 'A' / 'X.dds').write_bytes(b'2')
        r = vb.check_census(ctx)
        assert r['status'] == vb.FAIL and r['denominator'].startswith('2 files')

    def test_census_reads_zip_members_and_refuses_empty(self, tmp_path):
        """A zip carrying two spellings fails; an empty tree refuses."""
        ctx = _ctx(tmp_path)
        (ctx.output / 'Empty.esp').mkdir()
        assert vb.check_census(ctx)['status'] == vb.REFUSE
        (ctx.output / 'Empty.esp' / 'a.txt').write_text('a')
        z = tmp_path / 'm.zip'
        with zipfile.ZipFile(z, 'w') as zf:
            zf.writestr('Meshes/a.nif', b'1')
            zf.writestr('meshes/A.NIF', b'2')
        ctx.zips = [z]
        assert vb.check_census(ctx)['status'] == vb.FAIL

    def _doors(self, tmp_path, monkeypatch, want, caches, alone=None):
        """A context whose plugins classify `want` doors and hold `caches`."""
        monkeypatch.setattr(vb, 'classifiable_doors', lambda rec: {
            m: f'/m/{m}' for m in want[Path(rec).name]}, raising=False)
        monkeypatch.setattr(vb, 'confirm_alone', lambda paths: set(paths)
                            if alone is None else alone, raising=False)
        tmp_path.mkdir(exist_ok=True)
        ctx = _ctx(tmp_path, plugins=list(want))
        for name, (schema, models) in caches.items():
            d = ctx.export / name
            d.mkdir(parents=True, exist_ok=True)
            data = {'__schema__': [schema],
                    **{f'tes4/{m}': ['X'] for m in models}}
            (d / vb.DOOR_AXIS_CACHE).write_text(json.dumps(data))
        return vb.check_door_axes(ctx)

    def test_door_axes_cover_every_classifiable_model(self, tmp_path,
                                                      monkeypatch):
        """G5: a current cache holding each readable door passes; 0-door plugins are left out."""
        from asset_convert.collision.collision_extract import (
            DOOR_AXIS_SCHEMA_VERSION as V)
        want = {'Oblivion.esm': {'a.nif', 'b.nif'}, 'Zero.esp': set(),
                'FR.esp': {'c.nif'}}
        good = {'Oblivion.esm': (V, ['a.nif', 'b.nif']), 'FR.esp': (V, ['c.nif'])}
        r = self._doors(tmp_path, monkeypatch, want, good)
        assert (r['status'], r['denominator']) == (
            vb.PASS, '2/2 plugins with doors')

    def test_door_axes_stale_schema_or_lost_model_fails(self, tmp_path,
                                                        monkeypatch):
        """An older-schema cache, or one missing a model, fails however full it looks."""
        from asset_convert.collision.collision_extract import (
            DOOR_AXIS_SCHEMA_VERSION as V)
        want = {'Oblivion.esm': {'a.nif'}, 'FR.esp': {'c.nif', 'd.nif'}}
        r = self._doors(tmp_path / 'old', monkeypatch, want, {
            'Oblivion.esm': (V - 1, ['a.nif']), 'FR.esp': (V, ['c.nif', 'd.nif'])})
        assert (r['status'], r['denominator']) == (
            vb.FAIL, '1/2 plugins with doors')
        r = self._doors(tmp_path / 'lost', monkeypatch, want, {
            'Oblivion.esm': (V, ['a.nif']), 'FR.esp': (V, ['c.nif'])})
        assert r['data']['plugins']['FR.esp']['lost'] == ['d.nif']
        assert r['status'] == vb.FAIL

    def test_door_that_fails_alone_is_not_lost(self, tmp_path, monkeypatch):
        """A model that classifies only in a batch (reader state) is not held against the cache."""
        from asset_convert.collision.collision_extract import (
            DOOR_AXIS_SCHEMA_VERSION as V)
        r = self._doors(tmp_path, monkeypatch, {'A.esp': {'a.nif', 'b.nif'}},
                        {'A.esp': (V, ['a.nif'])}, alone=set())
        assert (r['status'], r['denominator']) == (
            vb.PASS, '1/1 plugins with doors')
        assert r['data']['plugins']['A.esp']['want'] == 1

    def test_door_axes_not_applicable_without_doors(self, tmp_path,
                                                    monkeypatch):
        """No plugin with a readable door model -> N/A, not a failure."""
        r = self._doors(tmp_path, monkeypatch, {'A.esp': set()}, {})
        assert r['status'] == vb.NA

    def test_classifiable_doors_without_door_records(self, tmp_path):
        """No DOOR.txt -> nothing to classify."""
        assert vb.classifiable_doors(tmp_path) == {}

    def test_deploy_compares_crc(self, tmp_path):
        """G10: a changed or missing deployed file fails."""
        z = tmp_path / 'm.zip'
        with zipfile.ZipFile(z, 'w') as zf:
            zf.writestr('a.esp', b'plugin')
            zf.writestr('sub/b.bsa', b'archive')
        mod = tmp_path / 'mod'
        (mod / 'sub').mkdir(parents=True)
        (mod / 'a.esp').write_bytes(b'plugin')
        (mod / 'sub' / 'b.bsa').write_bytes(b'archive')
        ctx = _ctx(tmp_path, deploy=[(str(z), str(mod))])
        assert vb.check_deploy(ctx)['status'] == vb.PASS
        (mod / 'sub' / 'b.bsa').write_bytes(b'stale')
        r = vb.check_deploy(ctx)
        assert (r['status'], r['denominator']) == (vb.FAIL, '1/2 members')


class TestTextures:

    def test_texture_key(self):
        """Archive and NIF spellings reduce to one textures-relative key."""
        assert vb.texture_key('Data\\Textures\\TES4\\A.DDS') == 'tes4\\a.dds'
        assert vb.texture_key('textures/tes4/b.dds') == 'tes4\\b.dds'
        assert vb.texture_key('meshes\\x.nif') == ''

    def _run(self, monkeypatch, tmp_path, refs, table, allow=()):
        """check_textures over stubbed references ({key: meshes}) and tables."""
        if not isinstance(refs, dict):
            refs = dict.fromkeys(refs, 1)
        monkeypatch.setattr(vb, 'mesh_texture_refs', lambda ctx: (refs, 7))
        monkeypatch.setattr(vb, 'texture_table', lambda ctx: table)
        return vb.check_textures(_ctx(tmp_path, allow_missing=frozenset(allow)))

    def test_missing_texture_fails_and_allowed_key_passes(
            self, monkeypatch, tmp_path):
        """G2: an unshipped texture fails; a listed FULL key is allowed."""
        refs = {'tes4\\a.dds': 5, 'tes4\\x\\black.dds': 1}
        r = self._run(monkeypatch, tmp_path, refs, {'tes4\\a.dds'},
                      allow={'tes4\\x\\black.dds'})
        assert r['status'] == vb.PASS and r['data']['allowed'] == [
            'tes4\\x\\black.dds']
        r = self._run(monkeypatch, tmp_path, {**refs, 'tes4\\b.dds': 1},
                      {'tes4\\a.dds'}, allow={'tes4\\x\\black.dds'})
        assert r['status'] == vb.FAIL and r['data']['missing'] == ['tes4\\b.dds']

    def test_allow_list_is_by_full_key_not_basename(self, monkeypatch,
                                                    tmp_path):
        """Allowing one black.dds must not hide another folder's black.dds."""
        refs = {'tes4\\a.dds': 5, 'tes4\\y\\black.dds': 1}
        r = self._run(monkeypatch, tmp_path, refs, {'tes4\\a.dds'},
                      allow={'tes4\\x\\black.dds'})
        assert r['status'] == vb.FAIL
        assert r['data']['stale_allow'] == ['tes4\\x\\black.dds']

    def test_allow_file_reads_full_keys(self, tmp_path):
        """One key per line, comments and blank lines ignored, / folded to \\."""
        f = tmp_path / 'allow.txt'
        f.write_text('# source defects\nTES4/X/Black.dds  # absent\n\n',
                     encoding='utf-8')
        assert vb.read_allow_missing(f) == {'tes4\\x\\black.dds'}

    def test_control_is_the_most_named_texture(self, monkeypatch, tmp_path):
        """The control comes from the run: absent from every table -> blind."""
        refs = {'tes4\\a.dds': 1, 'tes4\\common.dds': 9}
        r = self._run(monkeypatch, tmp_path, refs,
                      {'tes4\\a.dds', 'tes4\\common.dds'})
        assert (r['status'], r['data']['control']) == (
            vb.PASS, 'tes4\\common.dds')
        r = self._run(monkeypatch, tmp_path, refs, {'tes4\\a.dds'},
                      allow={'tes4\\common.dds'})
        assert r['status'] == vb.FAIL

    def test_node_name_is_not_a_texture_but_drive_paths_are_whole(
            self, tmp_path):
        """No separator -> a node name; a colon stays inside the key."""
        ctx = _ctx(tmp_path)
        objs = ctx.lod / 'meshes' / 'terrain'
        objs.mkdir(parents=True)
        (objs / 'W.4.0.0.bto').write_bytes(
            b'\x11\x00\x00\x00CPStone01.dds.b:0\x00\x00\x00'
            b'\x20\x00\x00\x00Textures\\tes4\\f:\\gg\\data\\x.dds'
            b'\x14\x00\x00\x00textures\\tes4\\next.dds')
        refs, _meshes = vb.mesh_texture_refs(ctx)
        assert set(refs) == {'tes4\\f:\\gg\\data\\x.dds',
                             'tes4\\next.dds'}

    def test_mesh_strings_are_read_from_loose_lod_tiles(self, tmp_path):
        """Texture paths inside a .bto are found by the string scan."""
        ctx = _ctx(tmp_path)
        objs = ctx.lod / 'meshes' / 'terrain'
        objs.mkdir(parents=True)
        (objs / 'W.4.0.0.bto').write_bytes(
            b'\x10\x00\x00\x00textures\\tes4\\a.dds\x00\x00'
            b'\x14\x00\x00\x00Textures\\TES4\\B_n.dds')
        refs, meshes = vb.mesh_texture_refs(ctx)
        assert meshes == 1 and set(refs) == {'tes4\\a.dds', 'tes4\\b_n.dds'}


def _sub(sig, data):
    """One TES4 subrecord."""
    return sig + struct.pack('<H', len(data)) + data


def _rec(sig, body, fid=1):
    """One TES4 record (20-byte header)."""
    return sig + struct.pack('<IIII', len(body), 0, fid, 0) + body


def _grup(label, gtype, payload):
    """One TES4 GRUP (20-byte header)."""
    return (b'GRUP' + struct.pack('<IIiI', 20 + len(payload), label, gtype, 0)
            + payload)


class TestTerrainChecks:

    def test_source_atxt_only_quadrants(self):
        """G6 source side: ATXT without BTXT in the right worldspace only."""
        vtxt = struct.pack('<HHf', 0, 0, 0.5)
        land = _rec(b'LAND', _sub(b'BTXT', struct.pack('<IBBh', 1, 0, 0, 0))
                    + _sub(b'ATXT', struct.pack('<IBBh', 2, 1, 0, 0))
                    + _sub(b'VTXT', vtxt)
                    + _sub(b'ATXT', struct.pack('<IBBh', 3, 0, 0, 0))
                    + _sub(b'VTXT', vtxt))
        cell = _rec(b'CELL', _sub(b'XCLC', struct.pack('<ii', 5, -3)))
        world = _grup(0x3C, 1, cell + _grup(0, 6, land))
        other = _grup(0x99, 1, cell + _grup(0, 6, land))
        assert gt.source_atxt_only(world + other) == {((5, -3), 1)}

    def test_land_layers_ratio(self, monkeypatch, tmp_path):
        """G6: kept quadrants over source ATXT-only quadrants."""
        src = tmp_path / 'Oblivion.esm'
        src.write_bytes(b'')
        monkeypatch.setattr(gt, 'source_atxt_only',
                            lambda raw: {((0, 0), 1), ((0, 0), 2)})
        lands = {(0, 0): {'layers': {'base': {}, 'alpha': {1: [1]}}}}
        ctx = _ctx(tmp_path, source_esm=str(src))
        ctx.cache['lands'] = (lands, {}, 0.0)
        r = gt.check_land_layers(ctx)
        assert (r['status'], r['denominator']) == (
            vb.FAIL, '1/2 ATXT-only quadrants')

    def test_terrain_colour_orientation(self, monkeypatch, tmp_path):
        """G7: ours == flipped source passes; the unflipped control is worse."""
        from PIL import Image
        rng = np.random.default_rng(1)
        src = rng.integers(0, 255, (32, 32, 3)).astype(np.uint8)
        src = np.kron(src, np.ones((4, 4, 1), np.uint8))
        ours = tmp_path / 'o.png'
        theirs = tmp_path / 't.png'
        Image.fromarray(np.flipud(src).copy()).save(ours)
        Image.fromarray(src).save(theirs)
        monkeypatch.setattr(gt, 'tile_pairs', lambda ctx: [(0, 0, ours, theirs)])
        monkeypatch.setattr(gt, 'tile_mask',
                            lambda *a: np.ones((32, 32), bool))
        ctx = _ctx(tmp_path)
        ctx.cache['lands'] = ({}, {}, 0.0)
        r = gt.check_terrain_colour(ctx)
        assert r['status'] == vb.PASS and r['data']['mae'] == 0
        assert r['data']['control'] > 0


class TestMain:

    def test_writes_json_and_exit_code(self, tmp_path):
        """A refusing check fails the gate; the JSON carries every check."""
        ctx_log = tmp_path / 'rebuild.out'
        ctx_log.write_text(LOG.format(body=''), encoding='utf-8')
        (tmp_path / 'output').mkdir()
        out = tmp_path / 'gate.json'
        rc = vb.main(['--pre-deploy', '--log', str(ctx_log), '--output',
                      str(tmp_path / 'output'), '--export',
                      str(tmp_path / 'export'), '--only', 'G4,G9,G12',
                      '--json', str(out)])
        data = json.loads(out.read_text())
        assert rc == 1 and data['verdict'] == vb.FAIL
        assert [c['id'] for c in data['checks']] == ['G4', 'G9', 'G12']
        assert [c['status'] for c in data['checks']] == [
            vb.PASS, vb.REFUSE, vb.PASS]


class TestMagicArt:

    def test_unconverted_models_fail(self, tmp_path, monkeypatch):
        """G3: '0 written from 0 models (21 not converted)' is 0/21, a FAIL."""
        monkeypatch.setattr(vb, 'record_counts',
                            lambda esm, sigs: {'ARTO': 3, 'EXPL': 2})
        assert vb.check_magic_art(_ctx(tmp_path))['status'] == vb.PASS
        ctx = _ctx(tmp_path)
        ctx.sections[1].lines[1] = ('  Magic effect phase meshes: 0 written '
                                    'from 0 models (21 not converted)')
        r = vb.check_magic_art(ctx)
        assert (r['status'], r['denominator']) == (vb.FAIL, '0/21 models')
