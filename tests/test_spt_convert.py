"""Tests for the SpeedTree (.spt) -> Skyrim NIF conversion pipeline.

Covers the parser (asset_convert.spt_parser), the procedural geometry
generator (asset_convert.spt_generator), and the NIF builder / TREE record
importer.  Real .spt inputs are used when the Oblivion export is available;
structural assertions run unconditionally against synthetic parses.
"""

import os
import struct
from pathlib import Path

import numpy as np
import pytest

from asset_convert.spt_parser import parse_spt, SptParseError, BezierSpline
from asset_convert import spt_generator

_EXPORT = Path('export/Oblivion.esm/trees')
_HAVE_EXPORT = _EXPORT.is_dir() and any(_EXPORT.glob('*.spt'))

try:
    from asset_convert import pyffi_monkey_patch as _patch  # noqa: F401
    from pyffi.formats.nif import NifFormat  # noqa: F401
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
        from asset_convert.spt_converter import convert_one, _tex_index
        tex = _tex_index(Path('export/Oblivion.esm/textures/trees'))
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
    from asset_convert.spt_converter import load_tree_manifest
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
