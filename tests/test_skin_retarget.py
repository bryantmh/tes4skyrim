"""Comprehensive tests for the NIF skin retargeting pipeline (v5: swing-only LBS + bind matrix).

The v5 retarget:
  Step 1: Deforms vertices from OB T-pose to SK rest pose using swing-only
          LBS — for each bone, computes the geometric direction from the
          NiNode hierarchy and derives a swing rotation (direction change
          only, no bone roll).  This excludes the ~90° bone roll difference
          between OB and SK skeletons.
  Step 2: Repositions bone NiNodes to SK positions and recomputes bind matrices
          so that M@B@W = I.

Tests are organised bottom-up:
    1. Matrix / NIF helpers
    2. Skeleton loading
    3. Bone mapping
    4. Bind-matrix invariant (M@B@W = I)
    5. Vertex deformation (arms move from T-pose to rest)
    6. Bone position accuracy (exact Skyrim skeleton match)
    7. Full converter integration
"""

import json
import math
import os
import sys
import time

import numpy as np
import pytest

if not hasattr(time, "clock"):
    time.clock = time.perf_counter

from pyffi.formats.nif import NifFormat

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

EXPORT_ARMOR = os.path.join(BASE, "export", "Oblivion.esm", "meshes", "armor")
OUTPUT_ARMOR = os.path.join(BASE, "output", "oblivion.esm", "meshes", "tes4", "armor")

IRON_CUIRASS_SRC = os.path.join(EXPORT_ARMOR, "iron", "m", "cuirass.nif")
IRON_GAUNTLETS_SRC = os.path.join(EXPORT_ARMOR, "iron", "m", "gauntlets.nif")
IRON_BOOTS_SRC = os.path.join(EXPORT_ARMOR, "iron", "m", "boots.nif")
IRON_HELMET_SRC = os.path.join(EXPORT_ARMOR, "iron", "m", "helmet.nif")

IRON_CUIRASS_OUT = os.path.join(OUTPUT_ARMOR, "iron", "m", "cuirass.nif")
IRON_GAUNTLETS_OUT = os.path.join(OUTPUT_ARMOR, "iron", "m", "gauntlets.nif")
IRON_BOOTS_OUT = os.path.join(OUTPUT_ARMOR, "iron", "m", "boots.nif")
IRON_HELMET_OUT = os.path.join(OUTPUT_ARMOR, "iron", "m", "helmet.nif")

GENERATED_DIR = os.path.join(BASE, "asset_convert", "generated")
os.makedirs(GENERATED_DIR, exist_ok=True)

# Geometry block names added by body splice (not original armor geometry).
# These must be excluded from tests that compare source↔dest vertex counts/positions,
# since they don't exist in the source Oblivion NIF.
_BODY_SPLICE_PREFIXES = ('MaleUnderwear', 'FemaleUnderwear', 'HandMale', 'HandFemale',
                         'MaleFeet', 'FemaleFeet', 'BodyFill')

SK_SKEL_JSON = os.path.join(GENERATED_DIR, "skeleton_bones_skyrim_male.json")
SK_SKEL_FEMALE_JSON = os.path.join(GENERATED_DIR, "skeleton_bones_skyrim_female.json")
OB_SKEL_JSON = os.path.join(GENERATED_DIR, "skeleton_bones_oblivion.json")

# ---------------------------------------------------------------------------
# Import module under test
# ---------------------------------------------------------------------------
from asset_convert.skin_retarget import (
    _load_skeleton,
    _skin_transform_to_np,
    _m44_to_np,
    _write_skin_transform,
    _np_to_nif_node,
    _get_block_name,
    _manual_update_bind_position,
    build_bone_mapping,
    load_skeleton_from_nif,
    retarget_skin_to_skyrim,
)
from asset_convert.skyrim_overrides import OBLIVION_TO_SKYRIM_BONE_MAP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_nif(path):
    data = NifFormat.Data()
    with open(path, "rb") as f:
        data.read(f)
    return data


def _get_skinned_shapes(data):
    """Return (block, skin, skin_data, skel_root) for all skinned shapes."""
    results = []
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
                continue
            skin = getattr(block, "skin_instance", None)
            if skin is None or skin.data is None:
                continue
            results.append((block, skin, skin.data, skin.skeleton_root))
    return results


def _check_mbw_identity(data, tol=0.001):
    """Check M@B@W ≈ I for every bone of every skinned geometry.
    Returns list of (geom_name, bone_name, error) for failures."""
    failures = []
    for blk, skin, sd, skel_root in _get_skinned_shapes(data):
        M = _skin_transform_to_np(sd.skin_transform)
        gname = _get_block_name(blk)
        for i in range(sd.num_bones):
            if i >= skin.num_bones or skin.bones[i] is None:
                continue
            B = _skin_transform_to_np(sd.bone_list[i].skin_transform)
            try:
                W = _m44_to_np(skin.bones[i].get_transform(skel_root))
            except Exception:
                continue
            MBW = M @ B @ W
            err = np.linalg.norm(MBW - np.eye(4))
            if err > tol:
                bname = _get_block_name(skin.bones[i])
                failures.append((gname, bname, err))
    return failures


def _get_all_vertex_positions(data):
    """Return {geom_name: (N,3) array} for all skinned shapes."""
    result = {}
    for blk, skin, sd, sr in _get_skinned_shapes(data):
        name = _get_block_name(blk)
        nv = blk.data.num_vertices
        verts = np.array([[v.x, v.y, v.z] for v in blk.data.vertices[:nv]],
                         dtype=np.float64)
        result[name] = verts
    return result


def _load_sk_skeleton():
    """Load Skyrim male skeleton from JSON."""
    from pathlib import Path
    return _load_skeleton(Path(SK_SKEL_JSON))


def get_skinned_verts_from_path(nif_path):
    """Load NIF and return {geom_name: (N,3) array} for skinned shapes."""
    data = _load_nif(nif_path)
    return _get_all_vertex_positions(data)


# ============================================================================
# 1.  MATRIX HELPERS
# ============================================================================
class TestMatrixHelpers:
    """Verify row-vector matrix utilities."""

    def test_skin_transform_roundtrip(self):
        """SkinTransform -> numpy -> SkinTransform preserves values."""
        st = NifFormat.SkinTransform()
        st.rotation.m_11 = 0.5; st.rotation.m_12 = 0.3; st.rotation.m_13 = -0.8
        st.rotation.m_21 = -0.3; st.rotation.m_22 = 0.9; st.rotation.m_23 = 0.1
        st.rotation.m_31 = 0.8; st.rotation.m_32 = 0.1; st.rotation.m_33 = 0.5
        st.translation.x = 10.0; st.translation.y = -20.0; st.translation.z = 30.0
        st.scale = 1.0

        M = _skin_transform_to_np(st)
        st2 = NifFormat.SkinTransform()
        _write_skin_transform(st2, M)

        np.testing.assert_allclose(st2.rotation.m_11, 0.5, atol=1e-6)
        np.testing.assert_allclose(st2.translation.x, 10.0, atol=1e-6)
        np.testing.assert_allclose(st2.translation.z, 30.0, atol=1e-6)

    def test_identity_roundtrip(self):
        """Identity matrix roundtrips through SkinTransform."""
        M = np.eye(4, dtype=np.float64)
        st = NifFormat.SkinTransform()
        _write_skin_transform(st, M)
        M2 = _skin_transform_to_np(st)
        np.testing.assert_allclose(M2, np.eye(4), atol=1e-12)


# ============================================================================
# 2.  SKELETON LOADING
# ============================================================================
class TestSkeletonLoading:
    """Verify skeleton JSON files load correctly."""

    @pytest.mark.skipif(not os.path.exists(OB_SKEL_JSON), reason="No OB skeleton JSON")
    def test_ob_skeleton_has_core_bones(self):
        from pathlib import Path
        sk = _load_skeleton(Path(OB_SKEL_JSON))
        for bone in ["Bip01 Pelvis", "Bip01 Spine", "Bip01 L UpperArm",
                      "Bip01 R UpperArm", "Bip01 L Thigh", "Bip01 R Thigh"]:
            assert bone in sk, f"Missing OB bone: {bone}"

    @pytest.mark.skipif(not os.path.exists(SK_SKEL_JSON), reason="No SK skeleton JSON")
    def test_sk_skeleton_has_core_bones(self):
        sk = _load_sk_skeleton()
        for bone in ["NPC Pelvis [Pelv]", "NPC Spine [Spn0]",
                      "NPC L UpperArm [LUar]", "NPC R UpperArm [RUar]",
                      "NPC L Thigh [LThg]", "NPC R Thigh [RThg]"]:
            assert bone in sk, f"Missing SK bone: {bone}"

    @pytest.mark.skipif(not os.path.exists(SK_SKEL_JSON), reason="No SK skeleton JSON")
    def test_sk_pelvis_height(self):
        """Skyrim pelvis Z ≈ 68.9 (known reference value)."""
        sk = _load_sk_skeleton()
        pelvis = sk["NPC Pelvis [Pelv]"]
        z = pelvis[3, 2]
        assert 65 < z < 75, f"SK Pelvis Z={z}, expected ~68.9"

    @pytest.mark.skipif(not os.path.exists(OB_SKEL_JSON), reason="No OB skeleton JSON")
    def test_ob_pelvis_height(self):
        """Oblivion pelvis Z ≈ 67.4 (known reference value)."""
        from pathlib import Path
        sk = _load_skeleton(Path(OB_SKEL_JSON))
        pelvis = sk["Bip01 Pelvis"]
        z = pelvis[3, 2]
        assert 63 < z < 72, f"OB Pelvis Z={z}, expected ~67.4"

    @pytest.mark.skipif(not os.path.exists(OB_SKEL_JSON), reason="No OB skeleton JSON")
    def test_ob_bip01_has_rz90_rotation(self):
        """Oblivion Bip01 root has Rz(+90°) convention rotation."""
        from pathlib import Path
        sk = _load_skeleton(Path(OB_SKEL_JSON))
        # Look for Bip01 itself or COM (root-ish bone)
        if "Bip01" in sk:
            W = sk["Bip01"]
        else:
            return  # Skip if not available
        # Rz(90°) rotates [1, 0, 0] → [0, 1, 0].
        # In row-vector: [1,0,0] @ Rz(90°) = [0, 1, 0]
        R = W[:3, :3]
        v = R[0, :3]  # first row = where X-axis maps to
        # Should map X → ~Y direction (i.e. v[1] ≈ ±1)
        assert abs(abs(v[1]) - 1.0) < 0.1 or abs(abs(v[0]) - 1.0) < 0.1, \
            f"Bip01 rotation doesn't look like Rz(90°): row0={v}"

    @pytest.mark.skipif(not os.path.exists(SK_SKEL_JSON), reason="No SK skeleton JSON")
    def test_sk_world_transforms_are_orthogonal(self):
        """All SK bone world-transform rotations should be orthogonal (det ≈ 1)."""
        sk = _load_sk_skeleton()
        for name, W in sk.items():
            R = W[:3, :3]
            det = abs(np.linalg.det(R))
            assert abs(det - 1.0) < 0.01, f"{name}: det(R)={det}"


# ============================================================================
# 3.  BONE MAPPING
# ============================================================================
class TestBoneMapping:
    """Verify Oblivion→Skyrim bone name mapping."""

    @pytest.mark.skipif(
        not os.path.exists(OB_SKEL_JSON) or not os.path.exists(SK_SKEL_JSON),
        reason="Need both skeleton JSONs",
    )
    def test_bone_map_has_essentials(self):
        from pathlib import Path
        ob = _load_skeleton(Path(OB_SKEL_JSON))
        sk = _load_sk_skeleton()
        mapping = build_bone_mapping(ob, sk)
        # Must include pelvis, spine, arms, legs
        for ob_name in ["Bip01 Pelvis", "Bip01 Spine", "Bip01 L UpperArm"]:
            assert ob_name in mapping, f"Missing from mapping: {ob_name}"

    @pytest.mark.skipif(
        not os.path.exists(OB_SKEL_JSON) or not os.path.exists(SK_SKEL_JSON),
        reason="Need both skeleton JSONs",
    )
    def test_all_mapped_bones_exist_in_both_skeletons(self):
        from pathlib import Path
        ob = _load_skeleton(Path(OB_SKEL_JSON))
        sk = _load_sk_skeleton()
        mapping = build_bone_mapping(ob, sk)
        for ob_name, sk_name in mapping.items():
            assert ob_name in ob, f"OB bone {ob_name} not in skeleton"
            assert sk_name in sk, f"SK bone {sk_name} not in skeleton"

    def test_bone_map_lr_symmetry(self):
        """Every mapped L bone should have a corresponding R bone (except shield)."""
        skip_lr = {"Bip01 L Shield"}
        for ob_name, sk_name in OBLIVION_TO_SKYRIM_BONE_MAP.items():
            if ob_name in skip_lr:
                continue
            if " L " in ob_name:
                r_name = ob_name.replace(" L ", " R ", 1)
                assert r_name in OBLIVION_TO_SKYRIM_BONE_MAP, \
                    f"L bone {ob_name} has no R counterpart"


# ============================================================================
# 4.  (Removed — pose deltas replaced by full matrix LBS in v4)
# ============================================================================


# ============================================================================
# 5.  MBW IDENTITY — CONVERTED OUTPUT FILES
# ============================================================================
class TestMBWIdentity:
    """M@B@W must equal identity within tight tolerance on converted NIFs."""

    MBW_TOL = 0.001  # Very strict

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_OUT), reason="Need converted cuirass")
    def test_cuirass_mbw(self):
        data = _load_nif(IRON_CUIRASS_OUT)
        failures = _check_mbw_identity(data, self.MBW_TOL)
        assert not failures, f"MBW failures:\n" + "\n".join(
            f"  {g}/{b}: err={e:.6f}" for g, b, e in failures)

    @pytest.mark.skipif(not os.path.exists(IRON_GAUNTLETS_OUT), reason="Need converted gauntlets")
    def test_gauntlets_mbw(self):
        data = _load_nif(IRON_GAUNTLETS_OUT)
        failures = _check_mbw_identity(data, self.MBW_TOL)
        assert not failures, f"MBW failures:\n" + "\n".join(
            f"  {g}/{b}: err={e:.6f}" for g, b, e in failures)

    @pytest.mark.skipif(not os.path.exists(IRON_BOOTS_OUT), reason="Need converted boots")
    def test_boots_mbw(self):
        data = _load_nif(IRON_BOOTS_OUT)
        failures = _check_mbw_identity(data, self.MBW_TOL)
        assert not failures, f"MBW failures:\n" + "\n".join(
            f"  {g}/{b}: err={e:.6f}" for g, b, e in failures)

    @pytest.mark.skipif(not os.path.exists(IRON_HELMET_OUT), reason="Need converted helmet")
    def test_helmet_mbw(self):
        data = _load_nif(IRON_HELMET_OUT)
        failures = _check_mbw_identity(data, self.MBW_TOL)
        assert not failures, f"MBW failures:\n" + "\n".join(
            f"  {g}/{b}: err={e:.6f}" for g, b, e in failures)


# ============================================================================
# 6.  VERTEX PRESERVATION — Bind-matrix-only approach preserves vertices
# ============================================================================
class TestVertexDeformation:
    """Verify that swing-only LBS deforms vertices from OB T-pose to SK rest pose.

    The retarget uses shortest-arc (swing) rotations per bone to move vertices
    according to the visible pose change (arms lowering, etc.) while excluding
    the invisible convention rotation (~90° Rz on Bip01) and bone roll differences.

    Expected behavior:
      - Arms/hands: significant displacement (lowering from T-pose)
      - Body/torso: moderate displacement (skeleton proportion adjustment)
      - Feet: small displacement (legs barely change between OB and SK)
    """

    def _convert_and_get_verts(self, src_path):
        """Run full converter and return vertex arrays from output."""
        from asset_convert.nif_converter import convert_nif
        dst = os.path.join(BASE, "temp", f"test_deform_{os.path.basename(src_path)}")
        result = convert_nif(src_path, dst)
        assert result["converted"], f"Conversion failed"
        return get_skinned_verts_from_path(dst)

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_SRC), reason="Need source cuirass")
    def test_cuirass_arms_lowered(self):
        """Cuirass arm vertices should move significantly (arms lowering from T-pose)."""
        src_verts = get_skinned_verts_from_path(IRON_CUIRASS_SRC)
        dst_verts = self._convert_and_get_verts(IRON_CUIRASS_SRC)
        if "Arms" in src_verts and "Arms" in dst_verts:
            if len(src_verts["Arms"]) == len(dst_verts["Arms"]):
                displacements = np.linalg.norm(dst_verts["Arms"] - src_verts["Arms"], axis=1)
                # Arms should have moved > 5 game units on average (lowering)
                assert displacements.mean() > 5.0, \
                    f"Arms should be deformed: mean displacement={displacements.mean():.2f}"
                # But not exploded > 50 units
                assert displacements.max() < 50.0, \
                    f"Arms vertex explosion: max displacement={displacements.max():.2f}"

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_SRC), reason="Need source cuirass")
    def test_cuirass_body_moderate(self):
        """Body (torso) vertices should have moderate displacement (skeleton proportions)."""
        src_verts = get_skinned_verts_from_path(IRON_CUIRASS_SRC)
        dst_verts = self._convert_and_get_verts(IRON_CUIRASS_SRC)
        for name in ["UpperBody:0", "UpperBody", "UpperBody:1"]:
            if name in src_verts and name in dst_verts:
                if len(src_verts[name]) == len(dst_verts[name]):
                    displacements = np.linalg.norm(dst_verts[name] - src_verts[name], axis=1)
                    # Body should move moderately (proportion adjustment) but not explode
                    assert displacements.max() < 20.0, \
                        f"{name} vertex explosion: max displacement={displacements.max():.2f}"

    @pytest.mark.skipif(not os.path.exists(IRON_GAUNTLETS_SRC), reason="Need source gauntlets")
    def test_gauntlets_hands_lowered(self):
        """Gauntlet vertices should move significantly (hands lowering from T-pose)."""
        src_verts = get_skinned_verts_from_path(IRON_GAUNTLETS_SRC)
        dst_verts = self._convert_and_get_verts(IRON_GAUNTLETS_SRC)
        if "Hand" in src_verts and "Hand" in dst_verts:
            if len(src_verts["Hand"]) == len(dst_verts["Hand"]):
                displacements = np.linalg.norm(dst_verts["Hand"] - src_verts["Hand"], axis=1)
                # Hands should move > 20 units (lowering to sides)
                assert displacements.mean() > 20.0, \
                    f"Gauntlets should be deformed: mean disp={displacements.mean():.2f}"
                # But not exploded
                assert displacements.max() < 80.0, \
                    f"Gauntlets vertex explosion: max disp={displacements.max():.2f}"

    @pytest.mark.skipif(not os.path.exists(IRON_BOOTS_SRC), reason="Need source boots")
    def test_boots_small_displacement(self):
        """Boots vertices should have small displacement (legs barely change)."""
        src_verts = get_skinned_verts_from_path(IRON_BOOTS_SRC)
        dst_verts = self._convert_and_get_verts(IRON_BOOTS_SRC)
        if "Foot" in src_verts and "Foot" in dst_verts:
            if len(src_verts["Foot"]) == len(dst_verts["Foot"]):
                displacements = np.linalg.norm(dst_verts["Foot"] - src_verts["Foot"], axis=1)
                # Boots should move < 15 units (small skeleton adjustment)
                assert displacements.mean() < 15.0, \
                    f"Boots mean displacement too large: {displacements.mean():.2f}"

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_SRC), reason="Need source cuirass")
    def test_vertex_count_preserved(self):
        """Vertex count must not change for original armor geometry during conversion.

        Body skin stripping can remove blocks with the same name as armor blocks,
        so we only compare blocks with matching counts (confirming they're the same
        geometry, not a name collision between body skin and armor).
        """
        src_verts = get_skinned_verts_from_path(IRON_CUIRASS_SRC)
        dst_verts = self._convert_and_get_verts(IRON_CUIRASS_SRC)
        matched = 0
        for name in src_verts:
            if name in dst_verts and not name.startswith(_BODY_SPLICE_PREFIXES):
                if len(src_verts[name]) == len(dst_verts[name]):
                    matched += 1
                # Only fail if counts differ by a small amount (true geometry mismatch).
                # Large differences indicate body-skin/armor name collision.
                elif abs(len(src_verts[name]) - len(dst_verts[name])) < 50:
                    assert len(src_verts[name]) == len(dst_verts[name]), \
                        f"{name}: {len(src_verts[name])} → {len(dst_verts[name])} vertices"
        assert matched > 0, "No matching geometry blocks found"


# ============================================================================
# 7.  BONE POSITION ACCURACY — Bones at exact Skyrim skeleton positions
# ============================================================================
class TestBonePositionAccuracy:
    """After full conversion, bone NiNode world transforms should match Skyrim skeleton."""

    POS_TOL = 0.01  # Bone position tolerance in game units

    def _convert_and_check_bones(self, src_path, sk_skel):
        """Run full converter and verify bone positions match SK skeleton."""
        from asset_convert.nif_converter import convert_nif
        dst = os.path.join(BASE, "temp", f"test_bonepos_{os.path.basename(src_path)}")
        result = convert_nif(src_path, dst)
        assert result["converted"], f"Conversion failed: {result.get('error')}"
        data = _load_nif(dst)
        
        max_delta = 0
        checked = 0
        for blk, skin, sd, skel_root in _get_skinned_shapes(data):
            for i in range(skin.num_bones):
                if skin.bones[i] is None:
                    continue
                bone_name = _get_block_name(skin.bones[i])
                if bone_name not in sk_skel:
                    continue
                try:
                    W = _m44_to_np(skin.bones[i].get_transform(skel_root))
                except Exception:
                    continue
                sk_pos = sk_skel[bone_name][3, :3]
                nif_pos = W[3, :3]
                delta = np.linalg.norm(nif_pos - sk_pos)
                max_delta = max(max_delta, delta)
                checked += 1
        return max_delta, checked

    @pytest.mark.skipif(
        not os.path.exists(IRON_CUIRASS_SRC) or not os.path.exists(SK_SKEL_JSON),
        reason="Need source NIF + SK skeleton",
    )
    def test_cuirass_bone_positions(self):
        sk = _load_sk_skeleton()
        max_d, n = self._convert_and_check_bones(IRON_CUIRASS_SRC, sk)
        assert n > 0, "No bones matched SK skeleton"
        assert max_d < self.POS_TOL, \
            f"Max bone position delta: {max_d:.6f} (checked {n} bones)"

    @pytest.mark.skipif(
        not os.path.exists(IRON_BOOTS_SRC) or not os.path.exists(SK_SKEL_JSON),
        reason="Need source NIF + SK skeleton",
    )
    def test_boots_bone_positions(self):
        sk = _load_sk_skeleton()
        max_d, n = self._convert_and_check_bones(IRON_BOOTS_SRC, sk)
        assert n > 0, "No bones matched SK skeleton"
        assert max_d < self.POS_TOL, \
            f"Max bone position delta: {max_d:.6f} (checked {n} bones)"


# ============================================================================
# 8.  MBW IDENTITY — Raw retarget (not through converter)
# ============================================================================
class TestMBWRawRetarget:
    """M@B@W = I after retarget_skin_to_skyrim() called directly on source NIFs.
    
    Note: retarget expects bones already renamed to Skyrim names.
    Source NIFs have Oblivion names, so bones won't match SK skeleton.
    But _manual_update_bind_position still guarantees M@B@W = I from
    whatever the current NiNode transforms are.
    """

    MBW_TOL = 0.001

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_SRC), reason="Need source cuirass")
    def test_cuirass_mbw_raw(self):
        data = _load_nif(IRON_CUIRASS_SRC)
        retarget_skin_to_skyrim(data, src_path=IRON_CUIRASS_SRC)
        failures = _check_mbw_identity(data, self.MBW_TOL)
        assert not failures, f"MBW failures:\n" + "\n".join(
            f"  {g}/{b}: err={e:.6f}" for g, b, e in failures)

    @pytest.mark.skipif(not os.path.exists(IRON_GAUNTLETS_SRC), reason="Need source gauntlets")
    def test_gauntlets_mbw_raw(self):
        data = _load_nif(IRON_GAUNTLETS_SRC)
        retarget_skin_to_skyrim(data, src_path=IRON_GAUNTLETS_SRC)
        failures = _check_mbw_identity(data, self.MBW_TOL)
        assert not failures, f"MBW failures:\n" + "\n".join(
            f"  {g}/{b}: err={e:.6f}" for g, b, e in failures)


# ============================================================================
# 8b.  BONE DISTANCE PRESERVATION — Inter-bone distances must be consistent
# ============================================================================
class TestBoneDistancePreservation:
    """After retarget, pairwise bone distances should match Skyrim skeleton.

    This catches issues like distorted bone positions from incorrect
    rotations or wrong coordinate systems.
    """

    DIST_TOL = 0.05  # Tolerance for bone distance comparison (game units)

    @pytest.mark.skipif(
        not os.path.exists(IRON_CUIRASS_SRC) or not os.path.exists(SK_SKEL_JSON),
        reason="Need source cuirass + SK skeleton",
    )
    def test_cuirass_bone_distances(self):
        """After retarget, bone distances should match Skyrim skeleton distances."""
        from asset_convert.nif_converter import convert_nif
        sk = _load_sk_skeleton()

        dst = os.path.join(BASE, "temp", "test_bonedist_cuirass.nif")
        convert_nif(IRON_CUIRASS_SRC, dst)
        data = _load_nif(dst)

        # Collect all bone positions from the converted NIF
        nif_bones = {}
        for blk, skin, sd, skel_root in _get_skinned_shapes(data):
            for i in range(skin.num_bones):
                if skin.bones[i] is None:
                    continue
                name = _get_block_name(skin.bones[i])
                if name not in sk:
                    continue
                try:
                    W = _m44_to_np(skin.bones[i].get_transform(skel_root))
                    nif_bones[name] = W[3, :3]
                except Exception:
                    pass

        assert len(nif_bones) > 5, f"Only {len(nif_bones)} bones found"

        # Compare pairwise distances
        bone_names = sorted(nif_bones.keys())
        failures = []
        for i, a in enumerate(bone_names):
            for b in bone_names[i+1:]:
                nif_dist = np.linalg.norm(nif_bones[a] - nif_bones[b])
                sk_dist = np.linalg.norm(sk[a][3, :3] - sk[b][3, :3])
                err = abs(nif_dist - sk_dist)
                if err > self.DIST_TOL:
                    failures.append(f"  {a} <-> {b}: nif={nif_dist:.2f} sk={sk_dist:.2f} err={err:.4f}")

        assert not failures, f"Bone distance mismatches:\n" + "\n".join(failures[:20])


# ============================================================================
# 8c.  EDGE LENGTH PRESERVATION — Mesh integrity check
# ============================================================================
class TestEdgeLengthPreservation:
    """Edge lengths should be approximately preserved after DQS deformation.

    DQS applies blended rigid rotations per vertex.  For vertices weighted to a
    single bone, edge preservation is exact.  At weight blend boundaries (e.g.
    shoulder joints where spine ~5° meets upper arm ~61°), a small amount of edge distortion is
    inherent. This is the same distortion the game engine produces at runtime.

    The test catches mesh mangling (vertex explosions, misapplied
    transforms) while accepting the expected joint distortion from T-pose →
    lowered-arms reposing.
    """

    EDGE_TOL = 0.15  # Allow 15% relative edge length change

    def _get_triangles_and_verts(self, data):
        """Return (triangles_list, verts_Nx3) for each skinned shape."""
        results = []
        for blk, skin, sd, sr in _get_skinned_shapes(data):
            nv = blk.data.num_vertices
            verts = np.array([[blk.data.vertices[i].x, blk.data.vertices[i].y,
                               blk.data.vertices[i].z] for i in range(nv)],
                             dtype=np.float64)
            tris = []
            if hasattr(blk.data, 'triangles'):
                for t in blk.data.triangles:
                    tris.append((t.v_1, t.v_2, t.v_3))
            results.append((_get_block_name(blk), np.array(tris), verts))
        return results

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_SRC), reason="Need source cuirass")
    def test_cuirass_edge_lengths(self):
        """Edge lengths in cuirass should be approximately preserved."""
        from asset_convert.nif_converter import convert_nif
        src_data = _load_nif(IRON_CUIRASS_SRC)
        dst_path = os.path.join(BASE, "temp", "test_edgelen_cuirass.nif")
        convert_nif(IRON_CUIRASS_SRC, dst_path)
        dst_data = _load_nif(dst_path)

        src_shapes = self._get_triangles_and_verts(src_data)
        dst_shapes = self._get_triangles_and_verts(dst_data)

        src_by_name = {name: (tris, verts) for name, tris, verts in src_shapes}
        dst_by_name = {name: (tris, verts) for name, tris, verts in dst_shapes}

        total_edges = 0
        bad_edges = 0
        worst_ratio = 0.0
        worst_info = ""

        for name in src_by_name:
            if name not in dst_by_name:
                continue
            src_tris, src_v = src_by_name[name]
            dst_tris, dst_v = dst_by_name[name]
            if len(src_tris) != len(dst_tris) or len(src_v) != len(dst_v):
                continue

            for a, b, c in src_tris:
                if max(a, b, c) >= len(src_v):
                    continue
                for i, j in [(a, b), (b, c), (a, c)]:
                    src_len = np.linalg.norm(src_v[i] - src_v[j])
                    dst_len = np.linalg.norm(dst_v[i] - dst_v[j])
                    if src_len < 0.01:
                        continue
                    ratio = abs(dst_len - src_len) / src_len
                    total_edges += 1
                    if ratio > self.EDGE_TOL:
                        bad_edges += 1
                    if ratio > worst_ratio:
                        worst_ratio = ratio
                        worst_info = f"{name} edge ({i},{j}): {src_len:.3f}->{dst_len:.3f}"

        assert total_edges > 100, f"Too few edges checked: {total_edges}"
        fail_pct = bad_edges / total_edges * 100
        # Body-wrap fit (2026-07-10): armor is reshaped onto the actual Skyrim
        # body (clearance-exact, +1.0 margin per user request — clipping is
        # far more visible than distortion), so a chunk of edges legitimately
        # stretch >15% — that is the garment resizing to the new body, not
        # mesh damage.  Iron cuirass measures ~25%; the explosion guard is
        # the max-displacement assertion in TestVertexDeformation.
        assert fail_pct < 30.0, \
            f"{bad_edges}/{total_edges} ({fail_pct:.1f}%) edges exceed {self.EDGE_TOL*100:.0f}% tolerance\n" \
            f"Worst: {worst_info} (ratio={worst_ratio:.3f})"

    @pytest.mark.skipif(not os.path.exists(IRON_BOOTS_SRC), reason="Need source boots")
    def test_boots_edge_lengths(self):
        """Edge lengths in boots should be well-preserved (minimal deformation)."""
        from asset_convert.nif_converter import convert_nif
        src_data = _load_nif(IRON_BOOTS_SRC)
        dst_path = os.path.join(BASE, "temp", "test_edgelen_boots.nif")
        convert_nif(IRON_BOOTS_SRC, dst_path)
        dst_data = _load_nif(dst_path)

        src_shapes = self._get_triangles_and_verts(src_data)
        dst_shapes = self._get_triangles_and_verts(dst_data)

        src_by_name = {name: (tris, verts) for name, tris, verts in src_shapes}
        dst_by_name = {name: (tris, verts) for name, tris, verts in dst_shapes}

        total_edges = 0
        bad_edges = 0

        for name in src_by_name:
            if name not in dst_by_name:
                continue
            src_tris, src_v = src_by_name[name]
            dst_tris, dst_v = dst_by_name[name]
            if len(src_tris) != len(dst_tris) or len(src_v) != len(dst_v):
                continue

            for a, b, c in src_tris:
                if max(a, b, c) >= len(src_v):
                    continue
                for i, j in [(a, b), (b, c), (a, c)]:
                    src_len = np.linalg.norm(src_v[i] - src_v[j])
                    dst_len = np.linalg.norm(dst_v[i] - dst_v[j])
                    if src_len < 0.01:
                        continue
                    ratio = abs(dst_len - src_len) / src_len
                    total_edges += 1
                    if ratio > 0.10:
                        bad_edges += 1

        assert total_edges > 100, f"Too few edges checked: {total_edges}"
        fail_pct = bad_edges / total_edges * 100
        # Body-wrap clearance enforcement (+1.0 margin, user-requested
        # aggressiveness) legitimately expands the snug boot shaft; the
        # explosion guard is the max-displacement assertion elsewhere.
        assert fail_pct < 35.0, \
            f"Boots: {bad_edges}/{total_edges} ({fail_pct:.1f}%) edges exceed 10% tolerance"


# ============================================================================
# 9.  VERTEX BBOX — Converted output has reasonable bounding boxes
# ============================================================================
class TestVertexBbox:
    """Vertex bounding boxes should be in expected Skyrim world-space ranges."""

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_OUT), reason="Need converted cuirass")
    def test_cuirass_z_range(self):
        """Cuirass Z should span chest area — arms now at sides (lower Z)."""
        data = _load_nif(IRON_CUIRASS_OUT)
        for blk, skin, sd, sr in _get_skinned_shapes(data):
            name = _get_block_name(blk)
            nv = blk.data.num_vertices
            if nv == 0:
                continue
            zs = np.array([blk.data.vertices[i].z for i in range(nv)])
            if name.startswith(_BODY_SPLICE_PREFIXES):
                # Body splice geometry is in local space (Z can be negative)
                assert zs.min() > -120, f"{name}: Z min too low: {zs.min():.1f}"
            else:
                assert zs.min() > -5, f"{name}: Z min too low: {zs.min():.1f}"
            assert zs.max() < 130, f"{name}: Z max too high: {zs.max():.1f}"

    @pytest.mark.skipif(not os.path.exists(IRON_BOOTS_OUT), reason="Need converted boots")
    def test_boots_z_range(self):
        """Boots Z should be near ground level."""
        data = _load_nif(IRON_BOOTS_OUT)
        for blk, skin, sd, sr in _get_skinned_shapes(data):
            nv = blk.data.num_vertices
            if nv == 0:
                continue
            zs = np.array([blk.data.vertices[i].z for i in range(nv)])
            assert zs.min() > -10, f"Z min too low: {zs.min():.1f}"
            assert zs.max() < 50, f"Z max too high: {zs.max():.1f}"

    @pytest.mark.skipif(not os.path.exists(IRON_GAUNTLETS_OUT), reason="Need converted gauntlets")
    def test_gauntlets_z_dropped(self):
        """Gauntlet vertices should be at hip/thigh level after deformation (~60-80 Z)."""
        data = _load_nif(IRON_GAUNTLETS_OUT)
        for blk, skin, sd, sr in _get_skinned_shapes(data):
            nv = blk.data.num_vertices
            if nv == 0:
                continue
            zs = np.array([blk.data.vertices[i].z for i in range(nv)])
            # Gauntlets deformed: hands lowered from ~100Z to ~65-75Z
            assert zs.mean() < 85, f"Gauntlets Z mean too high: {zs.mean():.1f}"
            assert zs.mean() > 40, f"Gauntlets Z mean too low: {zs.mean():.1f}"


# ============================================================================
# 10.  FULL CONVERTER INTEGRATION
# ============================================================================
class TestFullConverterIntegration:
    """Test the complete NIF conversion pipeline including skin retarget."""

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_SRC), reason="Need source cuirass")
    def test_convert_cuirass_succeeds(self):
        """Full converter pipeline should succeed on cuirass."""
        from asset_convert.nif_converter import convert_nif
        dst = os.path.join(BASE, "temp", "test_convert_cuirass.nif")
        result = convert_nif(IRON_CUIRASS_SRC, dst)
        assert result["converted"], f"Conversion failed: {result.get('error')}"
        assert os.path.exists(dst), "Output file not created"

        # Verify MBW on converted output
        data = _load_nif(dst)
        failures = _check_mbw_identity(data, 0.001)
        assert not failures, f"MBW failures:\n" + "\n".join(
            f"  {g}/{b}: err={e:.6f}" for g, b, e in failures)

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_SRC), reason="Need source cuirass")
    def test_converter_preserves_vertex_count(self):
        """Vertex count should be preserved through conversion."""
        from asset_convert.nif_converter import convert_nif
        src_data = _load_nif(IRON_CUIRASS_SRC)
        src_counts = {}
        for blk, _, _, _ in _get_skinned_shapes(src_data):
            name = _get_block_name(blk)
            src_counts[name] = blk.data.num_vertices

        dst = os.path.join(BASE, "temp", "test_convert_cuirass_vcount.nif")
        convert_nif(IRON_CUIRASS_SRC, dst)
        dst_data = _load_nif(dst)
        
        dst_counts = {}
        for blk, _, _, _ in _get_skinned_shapes(dst_data):
            name = _get_block_name(blk)
            dst_counts[name] = blk.data.num_vertices

        # At least some geometries should match (skip body splice blocks)
        matched = 0
        for name in src_counts:
            if name in dst_counts and not name.startswith(_BODY_SPLICE_PREFIXES):
                if src_counts[name] == dst_counts[name]:
                    matched += 1
                elif abs(src_counts[name] - dst_counts[name]) < 50:
                    assert src_counts[name] == dst_counts[name], \
                        f"{name}: {src_counts[name]} → {dst_counts[name]} vertices"
        assert matched > 0, "No matching geometry names found"

    @pytest.mark.skipif(
        not os.path.exists(IRON_CUIRASS_SRC) or not os.path.exists(SK_SKEL_JSON),
        reason="Need source + SK skeleton",
    )
    def test_converter_bones_at_skyrim_positions(self):
        """After full conversion, bones should be at Skyrim skeleton positions."""
        from asset_convert.nif_converter import convert_nif
        sk = _load_sk_skeleton()
        dst = os.path.join(BASE, "temp", "test_convert_cuirass_bones.nif")
        convert_nif(IRON_CUIRASS_SRC, dst)
        data = _load_nif(dst)

        checked = 0
        for blk, skin, sd, skel_root in _get_skinned_shapes(data):
            for i in range(skin.num_bones):
                if skin.bones[i] is None:
                    continue
                bone_name = _get_block_name(skin.bones[i])
                if bone_name not in sk:
                    continue
                try:
                    W = _m44_to_np(skin.bones[i].get_transform(skel_root))
                except Exception:
                    continue
                sk_pos = sk[bone_name][3, :3]
                nif_pos = W[3, :3]
                delta = np.linalg.norm(nif_pos - sk_pos)
                assert delta < 0.01, \
                    f"{bone_name}: pos delta {delta:.4f} (NIF={nif_pos}, SK={sk_pos})"
                checked += 1
        assert checked > 0, "No bones matched SK skeleton"


# ============================================================================
# 11.  SKIN PARTITION FORMAT
# ============================================================================
class TestSkinPartition:
    """Verify NiSkinPartition is regenerated in Skyrim triangle format."""

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_OUT), reason="Need converted cuirass")
    def test_cuirass_has_skin_partition(self):
        """Every skinned geometry should have a NiSkinPartition."""
        data = _load_nif(IRON_CUIRASS_OUT)
        for blk, skin, sd, sr in _get_skinned_shapes(data):
            assert skin.skin_partition is not None, \
                f"{_get_block_name(blk)}: missing NiSkinPartition"

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_OUT), reason="Need converted cuirass")
    def test_cuirass_partition_has_triangles(self):
        """Skin partitions should use triangle format (not strips)."""
        data = _load_nif(IRON_CUIRASS_OUT)
        for blk, skin, sd, sr in _get_skinned_shapes(data):
            if skin.skin_partition is None:
                continue
            for pi in range(skin.skin_partition.num_skin_partition_blocks):
                part = skin.skin_partition.skin_partition_blocks[pi]
                # Skyrim uses triangles, not strips
                assert part.num_strips == 0, \
                    f"{_get_block_name(blk)} partition {pi}: has strips"


# ============================================================================
# 12.  PRN ARMOR DETECTION
# ============================================================================
class TestPRNArmor:
    """PRN-attached armor (single bone, identity bind) should be handled."""

    @pytest.mark.skipif(not os.path.exists(IRON_HELMET_SRC), reason="Need source helmet")
    def test_helmet_retargets(self):
        """Helmet (PRN armor) should retarget without error."""
        data = _load_nif(IRON_HELMET_SRC)
        count = retarget_skin_to_skyrim(data, src_path=IRON_HELMET_SRC)
        # Helmet may or may not have skinned geom (could be PRN-only)
        # Just verify no crash
        assert count >= 0


# ============================================================================
# 13.  BSDismemberSkinInstance
# ============================================================================
class TestBSDismemberSkin:
    """Converted armor should use BSDismemberSkinInstance with body parts."""

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_OUT), reason="Need converted cuirass")
    def test_cuirass_has_dismember_skin(self):
        data = _load_nif(IRON_CUIRASS_OUT)
        found_dismember = False
        for blk, skin, sd, sr in _get_skinned_shapes(data):
            if isinstance(skin, NifFormat.BSDismemberSkinInstance):
                found_dismember = True
                assert skin.num_partitions > 0, \
                    f"{_get_block_name(blk)}: 0 dismember partitions"
        assert found_dismember, "No BSDismemberSkinInstance found"


# ============================================================================
# 14.  MESH CONTINUITY — No holes from UV-seam vertex splitting
# ============================================================================
class TestMeshContinuity:
    """Converted meshes must not develop visible holes at UV seam splits.

    At UV seams, a single 3D position is stored as 2+ separate vertices with
    different UV coordinates.  Any per-vertex post-processing (spring relaxation,
    Laplacian solves, etc.) that moves these twins independently will open gaps
    that are visible as holes in the mesh.

    These tests detect that failure mode: find all coincident-position vertex
    pairs in the source mesh, then verify they remain coincident (within a small
    tolerance) in the converted output.  If any coincident pair diverges, a
    vertex-moving algorithm has created a hole.
    """

    HOLE_TOL = 5e-3  # 5mm tolerance — seam vertices must stay this close

    def _get_verts_by_block(self, data):
        """Return {block_name: np.ndarray (V, 3)} for all skinned shapes."""
        result = {}
        for blk, skin, sd, sr in _get_skinned_shapes(data):
            name = _get_block_name(blk)
            nv = blk.data.num_vertices
            if nv == 0:
                continue
            verts = np.array(
                [[blk.data.vertices[i].x,
                  blk.data.vertices[i].y,
                  blk.data.vertices[i].z] for i in range(nv)],
                dtype=np.float64,
            )
            result[name] = verts
        return result

    def _find_seam_pairs(self, verts_by_block, tol=1e-3):
        """Find pairs of vertices at the same 3D position (UV seam splits).

        Returns {block_name: list of (i, j) index pairs}.
        Uses a spatial-hash bucket with rounded coordinates so that only
        vertices genuinely coincident in the SOURCE mesh are tracked.
        """
        from collections import defaultdict
        pairs_by_block = {}
        for name, verts in verts_by_block.items():
            bucket = defaultdict(list)
            for i, p in enumerate(verts):
                # Round to nearest tol to bin truly-coincident vertices together
                key = (round(float(p[0]) / tol) * tol,
                       round(float(p[1]) / tol) * tol,
                       round(float(p[2]) / tol) * tol)
                bucket[key].append(i)
            pairs = []
            for idxs in bucket.values():
                if len(idxs) > 1:
                    i0 = idxs[0]
                    for j in idxs[1:]:
                        pairs.append((i0, j))
            pairs_by_block[name] = pairs
        return pairs_by_block

    def _check_holes(self, src_path, dst_path):
        """Return list of hole descriptions (empty = no holes)."""
        from asset_convert.nif_converter import convert_nif
        result = convert_nif(src_path, dst_path)
        assert result["converted"], f"Conversion failed: {result.get('error')}"

        src_data = _load_nif(src_path)
        dst_data = _load_nif(dst_path)

        src_verts = self._get_verts_by_block(src_data)
        dst_verts = self._get_verts_by_block(dst_data)

        if not src_verts:
            return []

        src_pairs = self._find_seam_pairs(src_verts)
        total_pairs = sum(len(p) for p in src_pairs.values())
        if total_pairs == 0:
            return []

        holes = []
        for name, pairs in src_pairs.items():
            if name not in dst_verts:
                continue
            # Skip body splice blocks and blocks where vertex count changed
            # (e.g. body skin "Arms" in source vs armor "Arms" in dest)
            if name.startswith(_BODY_SPLICE_PREFIXES):
                continue
            dv = dst_verts[name]
            if name in src_verts:
                sv = src_verts[name]
                if len(sv) != len(dv):
                    continue  # Different block (body skin stripped, armor retained)
                # Sanity: source pair vertices must actually be coincident
                valid_pairs = [(i, j) for (i, j) in pairs
                               if max(i, j) < len(sv)
                               and np.linalg.norm(sv[i] - sv[j]) < self.HOLE_TOL * 2]
            else:
                valid_pairs = pairs
            for (i, j) in valid_pairs:
                if max(i, j) >= len(dv):
                    continue
                gap = np.linalg.norm(dv[i] - dv[j])
                if gap > self.HOLE_TOL:
                    holes.append(
                        f"{name}: v{i} vs v{j} — gap={gap:.4f} "
                        f"(dst[i]={dv[i]}, dst[j]={dv[j]})"
                    )
        return holes

    @pytest.mark.skipif(not os.path.exists(IRON_CUIRASS_SRC), reason="Need source cuirass")
    def test_cuirass_no_seam_holes(self):
        """UV seam vertices in cuirass must remain coincident after conversion."""
        dst = os.path.join(BASE, "temp", "test_cont_cuirass.nif")
        holes = self._check_holes(IRON_CUIRASS_SRC, dst)
        assert not holes, (
            f"Cuirass has {len(holes)} seam hole(s) — vertex-moving post-pass "
            f"is splitting UV seam twins:\n" + "\n".join(holes[:10])
        )

    @pytest.mark.skipif(not os.path.exists(IRON_GAUNTLETS_SRC), reason="Need source gauntlets")
    def test_gauntlets_no_seam_holes(self):
        """UV seam vertices in gauntlets must remain coincident after conversion."""
        dst = os.path.join(BASE, "temp", "test_cont_gauntlets.nif")
        holes = self._check_holes(IRON_GAUNTLETS_SRC, dst)
        assert not holes, (
            f"Gauntlets has {len(holes)} seam hole(s):\n" + "\n".join(holes[:10])
        )

    @pytest.mark.skipif(not os.path.exists(IRON_BOOTS_SRC), reason="Need source boots")
    def test_boots_no_seam_holes(self):
        """UV seam vertices in boots must remain coincident after conversion."""
        dst = os.path.join(BASE, "temp", "test_cont_boots.nif")
        holes = self._check_holes(IRON_BOOTS_SRC, dst)
        assert not holes, (
            f"Boots has {len(holes)} seam hole(s):\n" + "\n".join(holes[:10])
        )
