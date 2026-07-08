"""Tests for the creature animation pipeline:
kf_decode (B-spline decode) → hkx_skeleton (skeleton.hkx) → hkx_anim (clip hkx).

Uses the Oblivion dog as the fixture creature (small, mixed interpolator types,
real root motion). Tests that need source assets or hkxcmd skip cleanly when
they are absent.
"""

import os
import subprocess
import sys

import numpy as np
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from asset_convert.kf_decode import (decode_kf, eval_bspline,  # noqa: E402
                                     split_root_motion, _knots,
                                     _basis_weights)
from asset_convert.hkx_xml import HKXCMD  # noqa: E402

DOG_DIR = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                       'creatures', 'dog')
DOG_SKEL = os.path.join(DOG_DIR, 'skeleton.nif')
DOG_FORWARD = os.path.join(DOG_DIR, 'forward.kf')
DOG_IDLE = os.path.join(DOG_DIR, 'idle.kf')

needs_assets = pytest.mark.skipif(
    not os.path.exists(DOG_FORWARD), reason='Oblivion export assets missing')
needs_hkxcmd = pytest.mark.skipif(
    not os.path.exists(HKXCMD), reason='hkxcmd.exe missing')


# ---------------------------------------------------------------------------
# B-spline evaluation math
# ---------------------------------------------------------------------------

class TestBSplineEval:
    def test_endpoints_interpolated(self):
        ctrl = np.array([[0.0, 0], [1, 5], [2, -3], [3, 1], [4, 4]])
        v = np.array([0.0, float(len(ctrl) - 3)])
        out = eval_bspline(ctrl, v)
        assert np.allclose(out[0], ctrl[0])
        assert np.allclose(out[-1], ctrl[-1])

    def test_partition_of_unity(self):
        n = 9
        u = _knots(n)
        for v in np.linspace(0.0, n - 3 - 1e-6, 20):
            w = _basis_weights(n, v, u)
            assert abs(w.sum() - 1.0) < 1e-9

    def test_curve_stays_in_convex_hull(self):
        ctrl = np.array([[0.0], [1], [2], [3], [4], [5]])
        out = eval_bspline(ctrl, np.linspace(0, 3, 50))
        assert out.min() >= -1e-9 and out.max() <= 5 + 1e-9

    def test_single_control_point(self):
        ctrl = np.array([[7.0, 8, 9]])
        out = eval_bspline(ctrl, np.array([0.0, 0.5]))
        assert np.allclose(out, [[7, 8, 9], [7, 8, 9]])


# ---------------------------------------------------------------------------
# KF decoding
# ---------------------------------------------------------------------------

@needs_assets
class TestDecodeKf:
    def test_forward_clip(self):
        clip = decode_kf(DOG_FORWARD)[0]
        assert clip.name == 'Forward'
        assert abs(clip.duration - 4.0 / 3.0) < 1e-3
        assert len(clip.tracks) == 45          # all transform tracks decoded
        bones = {t.bone for t in clip.tracks}
        assert 'Bip01' in bones and 'Bip01 Spine0' in bones
        # only float channels skipped
        assert all('FloatInterpolator' in why or 'rest pose' in why
                   for _, why in clip.skipped_blocks)

    def test_quaternions_unit_and_finite(self):
        clip = decode_kf(DOG_FORWARD)[0]
        for tr in clip.tracks:
            if tr.rotations is not None:
                n = np.linalg.norm(tr.rotations, axis=1)
                assert abs(n - 1).max() < 1e-5
                assert np.isfinite(tr.rotations).all()
            if tr.translations is not None:
                assert np.isfinite(tr.translations).all()

    def test_text_keys(self):
        clip = decode_kf(DOG_FORWARD)[0]
        texts = [s.strip() for _, s in clip.text_keys]
        assert texts[0] == 'start' and texts[-1] == 'end'
        assert any(s.startswith('Enum:') for s in texts)

    def test_static_rotation_fallback(self):
        # idle.kf Spine0 B-spline has no rotation channel — must fall back to
        # the interpolator's static transform, not drop the channel
        clip = decode_kf(DOG_IDLE)[0]
        sp0 = next(t for t in clip.tracks if t.bone == 'Bip01 Spine0')
        assert sp0.rotations is not None

    def test_root_motion_split_forward(self):
        clip = decode_kf(DOG_FORWARD)[0]
        motion = split_root_motion(clip)
        assert motion is not None and motion['bone'] == 'Bip01'
        assert np.linalg.norm(motion['translations'][-1]) > 70
        track = next(t for t in clip.tracks if t.bone == 'Bip01')
        assert np.allclose(track.translations, track.translations[0])

    def test_root_motion_split_idle_none(self):
        clip = decode_kf(DOG_IDLE)[0]
        assert split_root_motion(clip) is None


# ---------------------------------------------------------------------------
# Skeleton hkx generation
# ---------------------------------------------------------------------------

@needs_assets
class TestSkeletonHkx:
    def test_bone_collection(self):
        from asset_convert.hkx_skeleton import load_skeleton_bones
        bones = load_skeleton_bones(DOG_SKEL)
        assert bones[0].name == 'Bip01' and bones[0].parent == -1
        assert len(bones) == 45
        # parent-before-child ordering (required by Havok)
        for i, b in enumerate(bones):
            assert b.parent < i

    def test_quat_matrix_roundtrip(self):
        from asset_convert import pyffi_monkey_patch  # noqa: F401
        from asset_convert.hkx_skeleton import (_mat33_to_quat_xyzw,
                                                find_skeleton_root,
                                                quat_xyzw_to_mat33)
        from pyffi.formats.nif import NifFormat
        data = NifFormat.Data()
        with open(DOG_SKEL, 'rb') as f:
            data.read(f)
        root = find_skeleton_root(data)
        stack = [root]
        while stack:
            nd = stack.pop()
            stack.extend(c for c in nd.children
                         if isinstance(c, NifFormat.NiNode))
            m = nd.rotation
            orig = [[m.m_11, m.m_12, m.m_13], [m.m_21, m.m_22, m.m_23],
                    [m.m_31, m.m_32, m.m_33]]
            rec = quat_xyzw_to_mat33(_mat33_to_quat_xyzw(m))
            assert np.abs(np.array(rec) - np.array(orig)).max() < 1e-5

    @needs_hkxcmd
    def test_generate_and_roundtrip(self, tmp_path):
        from asset_convert.hkx_skeleton import generate_skeleton_hkx
        from asset_convert.hkx_xml import decompile_hkx
        out = str(tmp_path / 'skeleton.hkx')
        bones = generate_skeleton_hkx(DOG_SKEL, out)
        assert os.path.getsize(out) > 1000
        back = str(tmp_path / 'skeleton.xml')
        decompile_hkx(out, back)
        txt = open(back, encoding='ascii', errors='replace').read()
        assert 'hkaSkeleton' in txt
        for b in bones:
            assert b.name in txt


# ---------------------------------------------------------------------------
# Animation hkx generation (full path)
# ---------------------------------------------------------------------------

@needs_assets
@needs_hkxcmd
class TestAnimHkx:
    def test_convert_and_verify(self, tmp_path):
        from asset_convert.hkx_anim import convert_clip_hkx, verify_hkx
        from asset_convert.hkx_skeleton import load_skeleton_bones
        bones = load_skeleton_bones(DOG_SKEL)
        out = str(tmp_path / 'forward.hkx')
        clip, motion = convert_clip_hkx(DOG_FORWARD, bones, out)
        assert motion is not None
        stats = verify_hkx(out, clip, [b.name for b in bones])
        assert stats['tracks'] == len(bones)
        # spline-compressed round trip must be lossless within quantization
        assert stats['max_trans_err'] < 0.01
        assert stats['max_rot_err_deg'] < 0.1

    def test_hkxcmd_can_deserialize(self, tmp_path):
        # the real Havok deserializer (what the engine uses) must accept it
        from asset_convert.hkx_anim import convert_clip_hkx
        from asset_convert.hkx_skeleton import load_skeleton_bones
        bones = load_skeleton_bones(DOG_SKEL)
        out = str(tmp_path / 'forward.hkx')
        convert_clip_hkx(DOG_FORWARD, bones, out)
        res = subprocess.run(
            [HKXCMD, 'convert', '-v:XML', os.path.abspath(out),
             os.path.abspath(str(tmp_path / 'back.xml'))],
            capture_output=True, text=True)
        assert res.returncode == 0

    def test_annotations_carried(self, tmp_path):
        from asset_convert.hkx_anim import convert_clip_hkx
        from asset_convert.hkx_skeleton import load_skeleton_bones
        from external.pynifly_hkx.anim_skyrim import load_skyrim_animation
        bones = load_skeleton_bones(DOG_SKEL)
        kf = os.path.join(DOG_DIR, 'handtohandattackleft.kf')
        out = str(tmp_path / 'attack.hkx')
        convert_clip_hkx(kf, bones, out)
        back = load_skyrim_animation(out)
        texts = {a.text for a in back.annotations}
        assert any('Hit' in t for t in texts)
        assert any('Sound' in t for t in texts)
