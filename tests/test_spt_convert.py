"""Tests for the SpeedTree (.spt) -> Skyrim NIF conversion pipeline.

Covers the parser (asset_convert.speedtree.spt_parser), the procedural geometry
generator (asset_convert.speedtree.spt_generator), and the NIF builder / TREE record
importer.  Real .spt inputs are used when the Oblivion export is available;
structural assertions run unconditionally against synthetic parses.
"""

import json
import struct
from pathlib import Path

import numpy as np
import pytest

import source_paths
from asset_convert.speedtree.spt_parser import parse_spt, SptParseError, BezierSpline
from asset_convert.speedtree import spt_engine_geom, spt_generator

_EXPORT = Path('export/Oblivion.esm/trees')
_HAVE_EXPORT = _EXPORT.is_dir() and any(_EXPORT.glob('*.spt'))

try:
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    _HAVE_PYFFI = True
except (ImportError, AttributeError):
    _HAVE_PYFFI = False


# ---------------------------------------------------------------------------
# BezierSpline
# ---------------------------------------------------------------------------

class TestBezierSpline:
    def test_constant_curve(self):
        s = BezierSpline.parse('BezierSpline 0.5 0.5 0\n{\n2\n'
                               '0 1 1 0 0.1\n1 0 1 0 0.1\n}\n')
        assert s.eval(0.0) == 0.5
        assert s.eval(1.0) == 0.5
        assert s.lo == s.hi == 0.5

    def test_range_maps_curve(self):
        # y goes 1 -> 0 over x; value = lo + y*(hi-lo)
        s = BezierSpline.parse('BezierSpline 10 20 0\n{\n2\n'
                               '0 1 1 0 0.1\n1 0 1 0 0.1\n}\n')
        assert abs(s.eval(0.0) - 20.0) < 1e-3   # y=1 -> hi
        assert abs(s.eval(1.0) - 10.0) < 1e-3   # y=0 -> lo

    def test_variance_bounds(self):
        s = BezierSpline.parse('BezierSpline 0 10 2\n{\n2\n'
                               '0 1 1 0 0.1\n1 0 1 0 0.1\n}\n')
        rng = np.random.default_rng(0)
        vals = [float(s.eval_var(0.0, rng)) for _ in range(200)]
        base = s.eval(0.0)
        assert all(abs(v - base) <= 2.0 + 1e-6 for v in vals)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAVE_EXPORT, reason='Oblivion tree export unavailable')
class TestParser:
    def test_all_spts_parse_completely(self):
        files = sorted(_EXPORT.glob('*.spt'))
        assert len(files) > 50
        for p in files:
            t = parse_spt(p)                       # raises on trailing bytes
            assert t.version == '__IdvSpt_02_'
            assert t.num_levels >= 2
            assert len(t.levels) == t.num_levels
            assert t.size > 0
            assert t.bark_texture

    def test_known_tree_values(self):
        oak = _EXPORT / 'treeenglishoakforestsu.spt'
        if not oak.exists():
            pytest.skip('oak sample missing')
        t = parse_spt(oak)
        assert t.size == 200.0
        assert t.num_levels == 4
        # trunk stores gravity 1 (stay vertical)
        assert t.levels[0].gravity.lo == 1.0
        assert t.leaf_maps                          # composite leaf textures
        assert t.leaf_quads                         # section 10002 UV crops

        willow = _EXPORT / 'treeweepingwillowsu.spt'
        if willow.exists():
            w = parse_spt(willow)
            # willow leaves store gravity 90 (hang straight down)
            assert w.levels[-1].gravity.lo == 90.0
            # branch levels store the strong upward gravity 2..4
            assert w.levels[1].gravity.hi == 4.0

    def test_bad_data_raises(self):
        with pytest.raises((SptParseError, Exception)):
            parse_spt(__file__)                     # this .py file is not an spt


# ---------------------------------------------------------------------------
# Geometry generator
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAVE_EXPORT, reason='Oblivion tree export unavailable')
class TestGenerator:
    def _build(self, stem):
        return spt_generator.build_tree(parse_spt(_EXPORT / f'{stem}.spt'))

    def test_bark_and_leaves_present(self):
        geo = self._build('treeenglishoakforestsu')
        assert len(geo.bark_verts) > 100
        assert len(geo.bark_tris) > 100
        assert geo.leaf_groups
        assert sum(len(g['verts']) for g in geo.leaf_groups) > 100

    def test_bark_winding_faces_outward(self):
        # geometric triangle normals must align with the radial vertex normals
        # (inverted winding renders the trunk visible only from inside)
        geo = self._build('treeenglishoakforestsu')
        vs, ns = geo.bark_verts, geo.bark_normals
        tris = geo.bark_tris
        gn = np.cross(vs[tris[:, 1]] - vs[tris[:, 0]],
                      vs[tris[:, 2]] - vs[tris[:, 0]])
        gl = np.linalg.norm(gn, axis=1)
        ok = gl > 1e-6
        gn = gn[ok] / gl[ok, None]
        vn = (ns[tris[:, 0]] + ns[tris[:, 1]] + ns[tris[:, 2]])[ok] / 3.0
        dots = (gn * vn).sum(axis=1)
        assert (dots > 0).mean() > 0.8

    def test_height_matches_billboard(self):
        # generated height should track the TREE record billboard height
        manifest = _read_manifest()
        checked = 0
        for stem, entries in manifest.items():
            p = _EXPORT / f'{stem}.spt'
            if not p.exists():
                continue
            bh = entries[0][2]
            if bh <= 0:
                continue
            geo = spt_generator.build_tree(parse_spt(p), seed=entries[0][1])
            ratio = geo.height / bh
            assert 0.4 < ratio < 2.5, f'{stem}: h={geo.height:.0f} bb={bh}'
            checked += 1
        assert checked > 30

    def test_deterministic(self):
        a = self._build('shrubdeadbush')
        b = self._build('shrubdeadbush')
        assert np.array_equal(a.bark_verts, b.bark_verts)

    def test_willow_drapes_below_branches(self):
        # weeping willow leaf gravity = 90 -> hanging strands reach well below
        # the lowest branch attachment
        geo = self._build('treeweepingwillowsu')
        assert geo.leaf_groups
        leaf_z = min(g['verts'][:, 2].min() for g in geo.leaf_groups)
        # foliage descends into the lower third of the tree
        assert leaf_z < geo.height * 0.55

    def test_collision_soup_present(self):
        geo = self._build('treeenglishoakforestsu')
        assert geo.collision_verts and geo.collision_tris
        # trunk tube must be in the soup
        total = sum(len(t) for t in geo.collision_tris)
        assert total > 20


# ---------------------------------------------------------------------------
# NIF builder
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not (_HAVE_EXPORT and _HAVE_PYFFI),
                    reason='export or pyffi unavailable')
class TestNifBuilder:
    def _nif(self, stem, tmp_path):
        from asset_convert.speedtree.spt_converter import convert_one, tex_index
        tex = tex_index(Path('export/Oblivion.esm/textures/trees'))
        out = tmp_path / f'{stem}.nif'
        assert convert_one(_EXPORT / f'{stem}.spt', out, tex_idx=tex, name=stem)
        data = NifFormat.Data()
        with open(out, 'rb') as f:
            data.read(f)
        return data

    def test_flora_root_structure(self, tmp_path):
        data = self._nif('treeenglishoakforestsu', tmp_path)
        root = data.roots[0]
        assert isinstance(root, NifFormat.BSLeafAnimNode)
        bsx = [e for e in root.extra_data_list
               if isinstance(e, NifFormat.BSXFlags)]
        assert bsx and bsx[0].integer_data == 130
        assert root.collision_object is not None

    def test_collision_is_cms_on_root(self, tmp_path):
        data = self._nif('treeenglishoakforestsu', tmp_path)
        root = data.roots[0]
        body = root.collision_object.body
        shape = body.shape
        assert isinstance(shape, NifFormat.bhkMoppBvTreeShape)
        cms = shape.shape
        assert isinstance(cms, NifFormat.bhkCompressedMeshShape)
        assert cms.target is root                 # target the BSLeafAnimNode
        assert body.__class__ is NifFormat.bhkRigidBody   # identity, not T
        assert body.motion_system == 5            # static

    def test_leaf_shader_flags(self, tmp_path):
        data = self._nif('treeenglishoakforestsu', tmp_path)
        for b in data.roots[0].tree():
            if isinstance(b, NifFormat.NiTriShape) and b'Leaves' in b.name:
                sh = b.bs_properties[0]
                f1, f2 = sh.shader_flags_1, sh.shader_flags_2
                assert f2.slsf_2_tree_anim
                assert f2.slsf_2_double_sided
                assert f2.slsf_2_vertex_colors
                assert f1.slsf_1_vertex_alpha
                # uv_scale must be non-zero (PyFFI defaults it to 0 = invisible)
                assert sh.uv_scale.u == 1.0 and sh.uv_scale.v == 1.0
                assert b.bs_properties[1] is not None   # NiAlphaProperty
                return
        pytest.fail('no leaf shape found')

    def test_bark_has_tangents(self, tmp_path):
        data = self._nif('shrubdeadbush', tmp_path)
        for b in data.roots[0].tree():
            if isinstance(b, NifFormat.NiTriShapeData):
                assert b.extra_vectors_flags == 16
                t = np.array([[v.x, v.y, v.z] for v in b.tangents[:20]])
                assert np.linalg.norm(t, axis=1).mean() > 0.5
                return


# ---------------------------------------------------------------------------
# TREE record importer
# ---------------------------------------------------------------------------

class TestTreeRecordImport:
    def _rec(self, **kw):
        base = {'Signature': 'TREE', 'FormID': '0001F392', 'RecordFlags': '0',
                'EditorID': 'Mbush16', 'Model.MODL': '\\Dbush16.spt',
                'BNAM.BillboardWidth': '270.0', 'BNAM.BillboardHeight': '270.0'}
        base.update(kw)
        return base

    def _subs(self, data):
        p, out = 24, {}                            # skip 24-byte record header
        while p < len(data) - 6:
            sig = data[p:p + 4]
            sz = struct.unpack_from('<H', data, p + 4)[0]
            p += 6
            out[sig] = data[p:p + sz]
            p += sz
        return out

    def test_modl_uses_editorid(self):
        from tes5_import.record_types.items import convert_TREE
        subs = self._subs(convert_TREE(self._rec()))
        assert subs[b'MODL'].rstrip(b'\x00') == b'tes4\\speedtrees\\mbush16.nif'

    def test_obnd_from_billboard(self):
        from tes5_import.record_types.items import convert_TREE
        subs = self._subs(convert_TREE(self._rec()))
        assert b'OBND' in subs and len(subs[b'OBND']) == 12
        x1, y1, z1, x2, y2, z2 = struct.unpack('<6h', subs[b'OBND'])
        assert x2 == 135 and z2 == 270 and z1 == 0     # from 270x270 billboard

    def test_cnam_and_pfpc_present(self):
        from tes5_import.record_types.items import convert_TREE
        subs = self._subs(convert_TREE(self._rec()))
        assert len(subs[b'CNAM']) == 48                # 12 wind floats
        assert subs[b'PFPC'] == b'\x00\x00\x00\x00'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read_manifest():
    from asset_convert.speedtree.spt_converter import load_tree_manifest
    man = load_tree_manifest(Path('export/Oblivion.esm'))
    # {stem: [(editorid, seed, billboard_h)]}
    out = {}
    tf = Path('export/Oblivion.esm/TREE.txt')
    cur = {}
    for line in open(tf, encoding='utf-8', errors='replace'):
        line = line.strip()
        if line == '---RECORD_BEGIN---':
            cur = {}
        elif line == '---RECORD_END---':
            modl = cur.get('Model.MODL', '').replace('\\\\', '/').replace('\\', '/').strip('/')
            stem = modl.rsplit('/', 1)[-1].lower().replace('.spt', '')
            if stem:
                out.setdefault(stem, []).append(
                    (cur.get('EditorID', ''), int(cur.get('Seed[0]', '0') or 0),
                     float(cur.get('BNAM.BillboardHeight', '0') or 0)))
        elif '=' in line:
            k, v = line.split('=', 1)
            cur[k] = v
    return out


# ---------------------------------------------------------------------------
# Engine-branch alternate path (asset_convert/speedtree/spt_engine_geom.py)
# ---------------------------------------------------------------------------

class TestEngineBranchPath:

    def _fixture(self):
        from asset_convert.speedtree.spt_converter import (tex_index, load_tree_manifest)
        from output_layout import assets_for
        export_dir = Path('export/Oblivion.esm')
        tex = tex_index(assets_for(export_dir) / 'textures' / 'trees')
        man = load_tree_manifest(export_dir)
        recs = man.get('treeginkgo')
        if not recs:
            pytest.skip('treeginkgo TREE record not in the export')
        eid, icon, seed = recs[0]
        return Path(_EXPORT / 'treeginkgo.spt'), tex, eid, icon, seed

    @pytest.mark.skipif(not (_HAVE_EXPORT and _HAVE_PYFFI),
                        reason='needs the Oblivion export and pyffi')
    def test_fallback_is_byte_identical_to_default(self, tmp_path):
        """An unavailable engine must fall back to the generator EXACTLY.

        Regression: build_tree_engine originally checked availability only on a
        dump-cache MISS, so a stale cached .bin made an unavailable engine look
        available and the fallback never fired.
        """
        from asset_convert.speedtree import spt_engine_geom as eg
        from asset_convert.speedtree.spt_converter import convert_one
        src, tex, eid, icon, seed = self._fixture()

        default = tmp_path / 'default.nif'
        convert_one(src, default, icon=icon, seed=seed, tex_idx=tex,
                    name=eid.lower(), use_engine=False)

        real = eg.HARNESS
        try:
            eg.HARNESS = tmp_path / 'missing_harness.exe'
            fb = tmp_path / 'fallback.nif'
            convert_one(src, fb, icon=icon, seed=seed, tex_idx=tex,
                        name=eid.lower(), use_engine=True)
        finally:
            eg.HARNESS = real
        assert fb.read_bytes() == default.read_bytes()

    @pytest.mark.skipif(not (_HAVE_EXPORT and _HAVE_PYFFI),
                        reason='needs the Oblivion export and pyffi')
    def test_missing_exe_falls_back(self, tmp_path):
        """No configured Oblivion.exe must fall back, not raise."""
        from asset_convert.speedtree import spt_engine_geom as eg
        from asset_convert.speedtree.spt_converter import convert_one
        src, tex, eid, icon, seed = self._fixture()

        orig = eg.find_oblivion_exe
        try:
            eg.find_oblivion_exe = lambda *a, **k: ''
            out = tmp_path / 'noexe.nif'
            convert_one(src, out, icon=icon, seed=seed, tex_idx=tex,
                        name=eid.lower(), use_engine=True)
        finally:
            eg.find_oblivion_exe = orig
        assert out.is_file() and out.stat().st_size > 0

    def test_engine_path_is_on_by_default(self):
        """Engine branches are the DEFAULT; Python is the FALLBACK.

        The engine path is what ships -- it is only skipped per tree when no
        Oblivion.exe is configured, the native harness is missing, or a dump
        fails.  Defaulting it off made the good path opt-in and left everyone
        on the reimplementation.
        """
        import inspect
        from asset_convert.speedtree.spt_converter import convert_one, convert_spt_directory
        from asset_convert.asset_pipeline import convert_speedtrees
        assert (inspect.signature(convert_one)
                .parameters['use_engine'].default is True)
        assert (inspect.signature(convert_spt_directory)
                .parameters['use_engine'].default is True)
        assert (inspect.signature(convert_speedtrees)
                .parameters['use_engine'].default is True)

    @pytest.mark.skipif(not _HAVE_EXPORT, reason='needs the Oblivion export')
    def test_strip_expansion_drops_degenerates(self):
        """Strip stitching repeats an index; those joins are not triangles."""
        from asset_convert.speedtree.spt_engine_geom import strips_to_triangles
        strip = np.array([0, 1, 2, 2, 2, 3, 4, 5], np.int64)
        tris = strips_to_triangles([strip])
        assert len(tris) == 3                     # 6 windows, 3 degenerate
        for a, b, c in tris:
            assert a != b and b != c and a != c

    @pytest.mark.skipif(not _HAVE_EXPORT, reason='needs the Oblivion export')
    def test_strip_expansion_alternates_winding(self):
        """A triangle strip flips winding on every other triangle."""
        from asset_convert.speedtree.spt_engine_geom import strips_to_triangles
        tris = strips_to_triangles([np.array([0, 1, 2, 3], np.int64)])
        assert tris.tolist() == [[0, 1, 2], [1, 3, 2]]

    @pytest.mark.skipif(not _HAVE_EXPORT, reason='needs the Oblivion export')
    def test_orphan_repair_winding_faces_outward(self):
        """Repair triangles must wind the same way the engine's own do.

        The engine's LOD-0 strip list leaves whole vertex blocks unreferenced
        (on treecottonwoodsu, 560 of 2,044 -- including the flared trunk base),
        so those blocks are stitched back into tubes here.  The engine's strips
        carry their winding; ours is derived, and the first version derived it
        BACKWARDS, giving inward-facing normals and an inside-out trunk.

        Ground truth is the engine's own per-vertex normals: a correctly wound
        face normal points the same way they do.
        """
        from asset_convert.speedtree.spt_engine_geom import (read_dump, run_dump,
                                                   orphan_ring_triangles,
                                                   engine_available)
        if not engine_available():
            pytest.skip('engine path unavailable')
        src = _EXPORT / 'treecottonwoodsu.spt'
        if not src.is_file():
            pytest.skip('treecottonwoodsu.spt not in the export')
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dump = run_dump(src, Path(td) / 'cw.bin', seed=301409)
            co, no, uv, strips = read_dump(dump)
        orphan = orphan_ring_triangles(co, strips)
        if not len(orphan):
            pytest.skip('no orphan blocks for this tree')

        a, b, c = co[orphan[:, 0]], co[orphan[:, 1]], co[orphan[:, 2]]
        face = np.cross(b - a, c - a)
        vn = (no[orphan[:, 0]] + no[orphan[:, 1]] + no[orphan[:, 2]]) / 3.0
        dot = np.einsum('ij,ij->i', face, vn)
        ok = np.isfinite(dot)
        agree = (dot[ok] > 0).mean()
        assert agree > 0.95, (
            f'repair triangles wound inward: only {agree:.1%} agree with the '
            f'engine vertex normals (reversed would be {1 - agree:.1%})')

    @pytest.mark.skipif(not _HAVE_EXPORT, reason='needs the Oblivion export')
    def test_orphan_repair_emits_no_oversized_triangles(self):
        """Ring-size inference is heuristic; the size guard must bound it.

        Mis-inferring a block's ring size stitches vertices from opposite
        sides of a tube, producing triangles that span a large fraction of the
        tree (measured up to 46% on treedogwoodsu across three successive
        inference attempts).  Whatever the inference does, nothing long may
        survive.
        """
        from asset_convert.speedtree.spt_engine_geom import (read_dump, run_dump,
                                                   orphan_ring_triangles,
                                                   engine_available)
        if not engine_available():
            pytest.skip('engine path unavailable')
        # Pick a tree the repair actually touches.  Only 14 of 202 dumps get
        # any repair triangles now that it is gated on coverage, and dogwood
        # (the original subject) is no longer one of them -- a test aimed at a
        # tree with zero repair triangles asserts nothing.
        src = _EXPORT / 'treecottonwoodsu.spt'
        if not src.is_file():
            pytest.skip('treecottonwoodsu.spt not in the export')
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dump = run_dump(src, Path(td) / 'cw.bin', seed=301409)
            co, no, uv, strips = read_dump(dump)
        orphan = orphan_ring_triangles(co, strips)
        if not len(orphan):
            pytest.skip('no orphan blocks for this tree')
        a, b, c = co[orphan[:, 0]], co[orphan[:, 1]], co[orphan[:, 2]]
        edge = np.maximum(np.maximum(np.linalg.norm(b - a, axis=1),
                                     np.linalg.norm(c - b, axis=1)),
                          np.linalg.norm(a - c, axis=1))
        fin = np.isfinite(co).all(1)
        span = float(np.linalg.norm(co[fin].max(0) - co[fin].min(0)))
        assert edge.max() <= 0.10 * span + 1e-6, (
            f'repair emitted a {edge.max():.1f}-unit edge on a {span:.1f}-unit '
            f'tree ({edge.max() / span:.1%} of the diagonal)')

    @pytest.mark.skipif(not _HAVE_EXPORT, reason='needs the Oblivion export')
    def test_bare_tree_gets_no_leaves(self):
        """A tree the engine gives no leaves must ship none.

        dtree01 stores child_freq = 0 on its leaf level, so the engine grows
        zero leaves and the dump records an EXPLICIT zero -- an empty ARRAY,
        never None, distinct from "no leaf chunk at all".  Reading that as
        None fell back to the Python foliage: 264 leaf cards placed against
        PYTHON branches, up to 36% of the tree diagonal off the engine bark.
        """
        from asset_convert.speedtree.spt_engine_geom import (read_leaf_centers, run_dump,
                                                   build_tree_engine,
                                                   engine_available)
        if not engine_available():
            pytest.skip('engine path unavailable')
        src = _EXPORT / 'dtree01.spt'
        if not src.is_file():
            pytest.skip('dtree01.spt not in the export')
        tree = parse_spt(src)
        assert float(tree.levels[-2].child_freq or 0.0) <= 0.0, \
            'dtree01 is supposed to gate its leaves off'

        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dump = run_dump(src, Path(td) / 'dtree01.bin', seed=581987)
            centers = read_leaf_centers(dump)
            assert centers is not None, \
                'a zero leaf count must be recorded, not omitted'
            assert len(centers) == 0

            geo = build_tree_engine(tree, src, seed=581987,
                                    cache_dir=Path(td))
        assert geo.leaf_groups == [], \
            f'bare tree grew {geo.n_leaves} leaves from the Python fallback'
        assert geo.n_leaves == 0
        assert geo.bark_tris is not None and len(geo.bark_tris) > 0, \
            'the bark must still be there'


def _install(root, name, exe=True):
    """A game folder under `root` with a Data dir and, unless told not, an Oblivion.exe."""
    (root / name / 'Data').mkdir(parents=True)
    if exe:
        (root / name / 'Oblivion.exe').write_bytes(b'')
    return str(root / name / 'Data')


class TestFindOblivionExe:
    """Which install's Oblivion.exe the engine harness is pointed at."""

    def _find(self, tmp_path, monkeypatch, registry, configured, **given):
        """`find_oblivion_exe()` with the registry stubbed and a real config file."""
        monkeypatch.setattr(source_paths, '_registry_path',
                            lambda game: registry if game == 'oblivion' else '')
        monkeypatch.setattr(source_paths, 'SCRIPT_DIR', tmp_path)
        monkeypatch.setattr(source_paths, 'DATA_ROOT', tmp_path)
        (tmp_path / 'conversion_config.json').write_text(
            json.dumps({'tes4DataPath': configured}), encoding='utf-8')
        return spt_engine_geom.find_oblivion_exe(**given)

    def test_the_configured_install_is_found_without_a_registry_entry(
            self, tmp_path, monkeypatch):
        """A machine with no registry key finds the install `tes4DataPath` names."""
        configured = _install(tmp_path, 'Oblivion')
        assert self._find(tmp_path, monkeypatch, '', configured) == str(
            tmp_path / 'Oblivion' / 'Oblivion.exe')
        assert self._find(tmp_path, monkeypatch, '', '') == ''
        monkeypatch.chdir(tmp_path / 'Oblivion')
        assert self._find(tmp_path, monkeypatch, '', '') == ''

    def test_the_config_comes_before_the_registry(self, tmp_path, monkeypatch):
        """With an Oblivion.exe in both, the harness follows `tes4DataPath`."""
        registry = _install(tmp_path, 'Registered')
        configured = _install(tmp_path, 'Configured')
        assert self._find(tmp_path, monkeypatch, registry, configured) == str(
            tmp_path / 'Configured' / 'Oblivion.exe')

    def test_the_registry_answers_when_the_config_has_no_oblivion_exe(
            self, tmp_path, monkeypatch):
        """An empty, stale or exe-less `tes4DataPath` leaves the registry's install."""
        registry = _install(tmp_path, 'Registered')
        expected = str(tmp_path / 'Registered' / 'Oblivion.exe')
        for configured in ('', str(tmp_path / 'gone' / 'Data'),
                           _install(tmp_path, 'DataOnly', exe=False)):
            assert self._find(tmp_path, monkeypatch, registry, configured) == expected
        (tmp_path / 'conversion_config.json').unlink()
        assert spt_engine_geom.find_oblivion_exe() == expected

    def test_a_data_folder_passed_in_is_the_only_one_looked_at(
            self, tmp_path, monkeypatch):
        """An explicit `tes4_data` outranks both the config and the registry."""
        registry = _install(tmp_path, 'Registered')
        configured = _install(tmp_path, 'Configured')
        given = _install(tmp_path, 'Given')
        assert self._find(tmp_path, monkeypatch, registry, configured,
                          tes4_data=given) == str(tmp_path / 'Given' / 'Oblivion.exe')
        bare = _install(tmp_path, 'Bare', exe=False)
        assert self._find(tmp_path, monkeypatch, registry, configured,
                          tes4_data=bare) == ''

    def test_a_config_that_cannot_be_parsed_is_an_error_only_when_it_is_read(
            self, tmp_path, monkeypatch):
        """Without an explicit folder the config is read, and a broken one raises."""
        given = _install(tmp_path, 'Given')
        self._find(tmp_path, monkeypatch, '', '')
        (tmp_path / 'conversion_config.json').write_text('{not json', encoding='utf-8')
        assert spt_engine_geom.find_oblivion_exe(given) == str(
            tmp_path / 'Given' / 'Oblivion.exe')
        with pytest.raises(ValueError):
            spt_engine_geom.find_oblivion_exe()
