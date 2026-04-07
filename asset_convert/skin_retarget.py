"""Skin retargeting: Oblivion skeleton → Skyrim skeleton.

Deforms mesh vertices from Oblivion T-pose to Skyrim rest pose using Dual
Quaternion Skinning (DQS), then replaces NiSkinData bind matrices to reference
Skyrim skeleton bone positions.

Algorithm:
  Phase A: Capture old bone world transforms, then reposition bone NiNodes
           to Skyrim skeleton rest positions (including correct rotations/rolls)
  Phase B: Deform vertices using DQS with swing-only transforms:
           Per bone: compute shortest-arc rotation from OB to SK bone direction.
           Convert to dual quaternion, blend in DQ space (normalized linear blend),
           apply to vertices.  DQS preserves volume at joint boundaries much
           better than LBS, avoiding the extreme edge distortion at the shoulder
           where spine (~5° swing) meets upper arm (~61° swing).
  Phase C: Recompute NiSkinData bind matrices from new bone positions
           B_i = G @ inv(W_sk_i), S = inv(G)
           Guarantees M@B@W = I (identity at rest)
  Phase D: Regenerate NiSkinPartition in Skyrim triangle format

Called from nif_converter._convert_nif() BEFORE bone renaming.
Bones still have Oblivion names (Bip01, Bip01 L UpperArm, etc.) when this runs.
We use OB→SK name mapping to find target Skyrim skeleton positions.
Bone renaming happens AFTER retarget completes, so that NiSkinData transforms
are computed while names and transforms are still consistent.
"""

import json
import math
import numpy as np
from pathlib import Path

import time

# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from . import pyffi_monkey_patch as _patch  # noqa: F401

from pyffi.formats.nif import NifFormat

from .skyrim_overrides import (
    ARMOR_DEFAULT_BODY_PART,
    ARMOR_GEOMETRY_BODY_PARTS,
    OBLIVION_TO_SKYRIM_BONE_MAP,
)

# ---------------------------------------------------------------------------
# IDEAS FOR CLOSING THE LAST ~10% GAP
# ---------------------------------------------------------------------------
# After DQS FK + Z-scale, arm RMSD sits at ~4.0 (down from ~9.7 at T-pose).
# The dominant residual comes from BONE LENGTH differences, not rotation:
#   - UpperArmTwist err 13.4, ForearmTwist err 9.4 → ~56% of arm cost
#   - FK can only rotate; it cannot stretch/compress along the bone axis.
#   - Spine FK deltas BREAK cuirass edges (18.8% fail), so spine is excluded.
# Edge-failure BASELINE (threshold 15% cuirass/gauntlets, 10% boots):
#   cuirass=10.80%, gauntlets=2.43%, boots=0.45%
# Root cause of cuirass failures: 418/418 UpperBody failures are ARM-ARM
#   (NOT torso-arm boundary). Clavicle↔Clavicle=238, Clavicle↔UpperArmTwist=104,
#   UpperArmTwist↔UpperArmTwist=76. DQS mixing different bone rotations at
#   vertices with mixed secondary weights is geometrically inherent to this
#   T-pose→arm-down deformation. The 10.80% baseline is close to the
#   physical minimum achievable with rotation-only skinning.
#
# IDEA 1 — Pre-FK per-chain bone-length scaling ❌ TRIED, REVERTED
#   Scale mesh vertices along each long-bone's local axis (weighted by skin
#   weights) by SK_len/OB_len before applying FK rotation deltas.
#   RESULT: cuirass 10.80%→29.19%, gauntlets 2.43%→3.63%, boots 0.45%→7.57%.
#   Much worse. Root cause: Non-rigid axis stretch inherently changes edge
#   lengths in the wrong direction; the FK delta was optimised for OB-scale
#   geometry, so pre-scaling breaks FK alignment.
#
# IDEA 2 — Gaussian pre-warp (translation only) → then FK rotation
#   Apply a spatial Gaussian blend of (SK_bone_pos - OB_bone_pos) vectors
#   as a pure TRANSLATION field across the mesh BEFORE FK. FK then handles
#   only rotation on already-translated vertices. Difference from old post-FK
#   Gaussian: direction is FORWARD (toward SK), translation only (no rotation
#   double-application). NOT YET TRIED. Prediction: likely negative — non-
#   rigid spatial warp will distort edges at chain boundaries.
#
# IDEA 3 — Thin-Plate Spline (TPS) warp from OB→SK bone control points
#   TPS scattered-data interpolant minimising global bending energy.
#   Control points: pairs (ob_bone_world_pos, sk_bone_world_pos).
#   NOT YET TRIED. Predicted worse than FK (non-rigid → edge distortion).
#   Skipping: complexity high, predicted benefit low.
#
# IDEA 4 — ARAP (As-Rigid-As-Possible) mesh deformation
#   Pose residual as energy minimisation: edges rigid as possible subject to
#   bone-attachment constraints. Would require per-mesh solve; complex.
#   NOT YET TRIED. Would need clear benefit to justify implementation cost.
#
# IDEA 5 — Post-FK per-chain Procrustes/Kabsch snap correction ❌ TRIED, REVERTED
#   Single rigid rotation per arm chain (L/R) fitted by SVD Kabsch to map
#   FK positions → SK positions. Applied to all arm-weighted vertices.
#   RESULT: cuirass 10.80%→14.19%, gauntlets 2.43%→21.32% (worst=45.834 —
#   vertex explosion on gauntlets). Much worse. Root cause: the centroid
#   translation component T[3,:3] = c_B - c_A @ R displaced gauntlet
#   vertices by 45× their edge lengths. A single global rotation+translation
#   for a whole arm is too coarse for per-segment meshes.
#
# IDEA 6 — Chain-level affine FK (scale + shear + rotation + translation)
#   Full affine transform per bone chain fitted to OB→SK bone positions.
#   Strict superset of rotation-only FK; captures arm-length stretch.
#   NOT YET TRIED. Risk: shear component → visible distortion at chain
#   boundaries. Prediction: similar failure mode to Idea 5 (non-rigid).
#   Skipping: shear inherently non-edge-length-preserving.
#
# IDEA 7 — Targeted twist-bone delta propagation ❌ TRIED, REVERTED
#   Copy parent bone delta to UpperArmTwist/ForearmTwist instead of using
#   their from-corpus deltas (53.19°→54.16°, 67.48°→61.55°).
#   RESULT: Arms 72→40 failures (improved), UpperBody 418→464 (worse).
#   Net: +10 total failures (marginal negative). Root cause: increasing
#   UpperArmTwist delta widened Clavicle↔UpperArmTwist gap — 46 more
#   UpperBody failures outweighed 32 Arms failures saved.
#
# CONCLUSION (Session 1): The 10.80% cuirass baseline resisted all rotation-
#   only approaches (Ideas 1,2,5,6 negative; Ideas 3,4,6 predicted negative
#   via shear). The dominant failures are geometrically inherent to DQS at
#   weight boundaries where Clavicle (9.46°) meets UpperArm (54.16°). The
#   question for Session 2 is whether POST-FK mesh correction or a
#   fundamental reformulation of the blend can break through.
#
# ===========================================================================
# BRAINSTORM SESSION 2 — strategies to reach <5% cuirass failures (95%+)
# ===========================================================================
# Diagnosis recap: 238 Clavicle↔Clavicle + 104 Clavicle↔UpperArmTwist edges
# fail because adjacent vertices have DIFFERENT bone weight ratios → DQS
# gives them slightly different effective rotations → edge stretches.
# The maximum angular gap across a boundary = ~44.7° (54.16° − 9.46°).
# ANY fix that reduces this effective gap at boundary vertices should help.
#
# IDEA 8 — Factored global+local FK (separate uniform motion from local deltas)
#   Core insight: if ALL shoulder/arm vertices receive the SAME rotation, no
#   intra-shoulder edges can distort regardless of weight ratios.
#   ❌ TRIED → REVERTED: cuirass 10.80%→13.8%. Even with pure-rotation blending
#   around the shoulder centroid, TORSO-side edges create NEW distortions at
#   the torso/shoulder transition. Root cause: the lerp boundary just moves the
#   problem to a different location.
#
# IDEA 9 — Edge spring relaxation with bone-dominance anchoring
#   Post-FK correction directly targeting the measured metric:
#     1. Build edge adjacency from mesh triangles (unique pairs only).
#     2. Compute anchor strength a_v = clamp(max_weight_v, 0, 1)² for each vert.
#     3. For N iterations: restore edges toward pre-FK lengths via spring forces;
#        distort_threshold=0.10 (only fix >10% stretch); spring_k=0.3; n=50.
#        Per-vertex cumulative displacement cap = 2.0 units (prevents runaway).
#     4. Post-pass: clamp edge to max 3× stretch (fixes rare false equilibria
#        near mixed-weight wrist vertices adjacent to short hand edges).
#   ✅ KEPT: cuirass 10.80%→8.83% (-18%), boots 0.45%→0.00% (perfect!),
#   gauntlets 2.43%→1.42% (-41%). Gauntlets worst edge 11.22→1.24 (post-pass).
#   37/37 tests pass.
#
# IDEA 10 — Laplacian deformation with bone-position constraints
#   Express the mesh in differential (Laplacian) coordinates so that shape is
#   preserved globally, not just locally.  Three formulations were tried:
#   (a) min ||L v' - L v_fk||² + λ||C v' - b||²  — preserves distorted FK
#       diff-coords exactly → cuirass 10.86% (baseline wash, boots/gauntlets
#       identical to baseline).
#   (b) Harmonic interpolation: L_ff * v_free = -L_fa * v_anchor  — singular
#       L_ff on meshes with large free-vertex clusters far from anchors; with
#       eps regularisation the system is non-singular but still 43-52% failure
#       because the harmonic ABSOLUTE positions bear no relation to FK positions.
#   (c) Correction formulation: (L_ff + eps*I) * delta = -lap_fk  — corrects
#       FK Laplacian residuals; uniform Laplacian on irregular armor meshes
#       gives large residuals everywhere (not just at distorted boundaries),
#       so corrections amplify existing irregularities: 15-41% failures.
#   ❌ TRIED ALL VARIANTS → REVERTED: uniform Laplacian not suitable for
#   irregular armor geometry.  Root problem: L residual at any vertex reflects
#   LOCAL TOPOLOGY (degree, edge lengths), not just DQS distortion, so the
#   correction signal is dominated by geometric irregularity noise.
#
# IDEA 11 — Virtual intermediate "shoulder blend" bone
#   Insert a synthetic bone delta midway on SO(3) between Clavicle and UpperArm:
#     q_virtual = slerp(q_Clavicle, q_UpperArm, 0.5)
#   For boundary vertices (both Clavicle and UpperArm weight > 0.15), convert
#   part of their Clavicle + UpperArm weight to Virtual weight.
#   Concretely: if vertex has w_clav, w_ua:
#     virtual_draw = min(w_clav, w_ua) * 0.6   (draw 60% of the min weight)
#     reduce w_clav by virtual_draw/2, reduce w_ua by virtual_draw/2
#     add w_virtual = virtual_draw
#   The result: max angular gap between any two bones at a vertex boundary
#   drops from ~44.7° to ~22.4° (cut in half) → DQS distortion ∝ gap → halved.
#   INITIALLY targeted wrong bone pair ('Bip01 L UpperArm') — cuirass actually
#   uses 'Bip01 L UpperArmTwist'. After fixing pairs to all likely boundaries
#   (Clav↔UArmTwist, Clav↔UArm, UArm↔UArmTwist, UArmTwist↔Forearm):
#   ❌ TRIED → REVERTED: cuirass 10.80%→10.04% (regression vs spring 8.83%).
#   Root problem: inserting virtual bone creates NEW weight discontinuities
#   at the threshold boundary (vertices just below VIRTUAL_THRESHOLD=0.15 stay
#   at DQS while vertices just above get virtual → new boundary artefact).  The
#   new boundary replaces the old one without reducing total distortion.  Combined
#   with spring relaxation, the virtual bone fights the spring.
#
# Implementation priority: 8 → 9 → 10 → 11 (ascending complexity)
#
# ===========================================================================
# BRAINSTORM SESSION 3 — strategies to reach <5% cuirass failures (95%+)
# ===========================================================================
# STATUS: cuirass currently at 10.80% (spring DISABLED — see below).
# Spring relaxation (Idea 9) improved cuirass to 8.83% but caused visible
# holes in the mesh: UV-seam "twin" vertices (same 3D position, different UV)
# received different spring forces due to disjoint adjacency, so they diverged.
# Goal: fix that or find a new path to <5%.
#
# MESH CONTINUITY TEST ADDED (tests/test_skin_retarget.py::TestMeshContinuity):
#   Detects hole creation by checking that coincident vertex pairs in the
#   source NIF remain coincident (within 5mm) after conversion.  Confirmed:
#   spring active → 3 tests FAIL.  Spring disabled → 3 tests PASS.
#
# IDEA 12 — Seam-welded spring relaxation (fix holes in Idea 9)
#   Root cause of holes: UV-seam twin vertices are topologically disconnected
#   in the mesh (each appears in different triangles), so they accumulate
#   different spring forces and diverge.  Fix by synchronising their motion:
#     1. Before spring: detect vertex groups where all members share the same
#        3D position (within tolerance 1e-3 units).  These are seam twins.
#     2. Anchor: use the MAX anchor per group (conservative; most-anchored
#        twin dominates, preventing any twin from being over-mobile).
#     3. Each iteration: after computing per-vertex displacement, average the
#        displacement across all members of each twin group.  They see the
#        same net force and move identically.
#     4. After all spring iterations AND after the outlier post-pass: snap
#        every twin group to its member mean position (closes any residual
#        drift from floating-point accumulation).
#   ❌ Failed: cuirass 10.80%→9.05% (-16%), boots 0.00%, gauntlets 1.98%.
#   Created holes / splits between edges
#
# IDEA 13 — ARAP (As-Rigid-As-Possible) deformation
#   Gold-standard geometry-processing technique for minimum-distortion mesh
#   correction.  Two-stage iteration:
#     Local step: per-vertex, SVD-fit the optimal rotation R_i that best maps
#       the REST-POSE vertex neighbourhood to the current deformed neighbourhood.
#       S_i = Σ_j w_ij (p_j−p_i)(s_j−s_i)^T  (covariance; s=rest, p=current)
#       SVD: S_i = U Σ V^T,  R_i = V U^T
#     Global step: solve sparse Laplacian system:
#       For each FREE vertex i:  Σ_j L[i,j] q_j = Σ_j w_ij/2 (R_i+R_j)(s_j−s_i)
#       Anchor vertices (max_bone_w > 0.85) are hard-constrained to DQS positions.
#   Uses COTANGENT weights (w_ij = cot α + cot β) for geometric correctness.
#   Direct seam-welding also needed (same twin-sync as Idea 12).
#   ❌ TRIED → REVERTED: cuirass 10.80%→36.21%, boots 0.00%→18.79%.
#   Root cause: T-pose→arms-lowered is a large deformation (~54°). ARAP
#   converges to a LOCAL MINIMUM from the DQS initialization rather than
#   the correct global minimum. The shoulder/arm transition region doesn't
#   converge well from DQS starting positions because the DQS distortions
#   are so large relative to the neighborhood scale that the local rotation
#   estimates in iteration 1 are unreliable, poisoning subsequent iterations.
#
# IDEA 14 — Weight sharpening (narrow the blend zone)
#   For each vertex: w_i' = w_i^γ / Σ_k w_k^γ  (gamma sharpening, γ=2.0-4.0).
#   Applied PRE-FK: modifies the weights used for DQS deformation only.
#   ❌ TRIED: gamma sweep 1.5→4.0 (all with spring relaxation):
#      gamma=1.0: cuirass 9.05% (baseline spring)
#      gamma=1.5: cuirass 9.48% ↑ worse
#      gamma=2.0: cuirass 9.74% ↑ worse
#      gamma=3.0: cuirass 9.97% ↑ worse
#      gamma=4.0: cuirass 10.06% ↑ worse
#   ❌ REVERTED: gamma=1.0 (no sharpening).
#   Root cause: sharpening changes the DQS result, but spring uses T-pose
#   lengths as targets (computed before sharpening) → they fight each other.
#   Even without spring, sharpening just moves the 44.7° angular gap to a
#   tighter spatial boundary without reducing it — equal or worse distortion.
#
# IDEA 15 — Cotangent Laplacian correction (improved Idea 10)
#   Idea 10 failed because the UNIFORM Laplacian residual at a vertex reflects
#   local TOPOLOGY (vertex degree, edge count) rather than geometric distortion.
#   Cotangent Laplacian (L_cot) is the discrete Laplace-Beltrami operator:
#     L_cot[i,j] = −(cot α_ij + cot β_ij)/2  for edge (i,j)
#   Its residual L_cot * v correctly measures mean-curvature distortion,
#   NOT topology noise.  Correction formulation:
#     (L_ff + eps*I) * Δ = −L_cot * v_fk  → apply Δ to free vertices
#   ❌ TRIED → REVERTED: cuirass 10.80%→32.5%, catastrophic failure.
#   Root cause: (1) eps=0.5 is far too small for cotangent Laplacian scale
#   (cotangent diagonal >> uniform diagonal) — effectively no regularisation.
#   (2) More fundamentally, smoothing the LAPLACIAN RESIDUAL drives free
#   vertices toward a minimal surface (Laplacian=0), which DESTROYS intentional
#   mesh folds at shoulder/armpit joints. Same root failure as ARAP:
#   geometry-smoothing approaches cannot distinguish "bad" DQS artefacts from
#   "good" intentional folds — they erase both equally.
#
# IDEA 16 — Anchor co-rotation prediction (Deformation Transfer)
#   Instead of blending bone TRANSFORMS at free vertices (DQS) or smoothing
#   positions (Laplacian), derive each free vertex's position from the ACTUAL
#   deformation observed at K nearest anchor vertices:
#   For each anchor j: predicted_ij = v_fk[j]  +  R_j @ (v_tpose[i] − v_tpose[j])
#   where R_j = rotation of j's dominant bone (column convention, from bone_deltas).
#   Final position = inverse-T-pose-distance weighted mean across K anchors.
#   This is SPATIALLY-based (KD-tree on T-pose coords) not weight-space-based.
#   ❌ TRIED → REVERTED: cuirass 9.05%→9.91% (worse).
#   Root cause: at the shoulder boundary, free vertices are spatially equidistant
#   from shoulder anchors (9° delta) and arm anchors (54° delta). The spatial
#   inverse-distance blend gives ~30° effective rotation, which is actually WORSE
#   than DQS's weight-based ~33° blend AND ignores the actual weight distribution
#   that was painted by an artist for a reason. Spatial proximity ≠ better weighting.
#
# IDEA 17 — Anchor-constrained spring (spring with hard position constraints)
#   The problem with the current spring: it relaxes vertices toward T-pose edge
#   lengths, but anchor vertices do not move, so spring CANNOT fully fix an edge
#   (i, j) where i is anchor and j is free — the spring would need to move j to
#   a specific location, but j is shared with other edges pointing in different
#   directions. The result is a compromise that satisfies none of them fully.
#   New approach: run spring BUT instead of using T-pose edge lengths as targets,
#   use PREDICTED lengths from the FK anchor positions + co-rotation prediction:
#     target_ij = |v_fk[anchor_i] + R_i @ (v_tpose[j] - v_tpose[anchor_i])|
#   when anchor_i is the closest anchor to j. This gives spring realistic targets
#   that account for the actual rotation of the dominant bone, not just T-pose.
#   NOT YET TRIED.
#   ❌ TRIED → REVERTED


# ---------------------------------------------------------------------------
# Skeleton data paths and cache
# ---------------------------------------------------------------------------
_DATA_DIR = Path(__file__).parent
# add "generated" subdir for outputs to avoid cluttering source directory
_GENERATED_DIR = _DATA_DIR / 'generated'
_GENERATED_DIR.mkdir(exist_ok=True)

_SKEL_OBLIVION = _GENERATED_DIR / 'skeleton_bones_oblivion.json'
_SKEL_SKYRIM_MALE = _GENERATED_DIR / 'skeleton_bones_skyrim_male.json'
_SKEL_SKYRIM_FEMALE = _GENERATED_DIR / 'skeleton_bones_skyrim_female.json'
_skel_cache: dict[str, dict[str, np.ndarray]] = {}


def _load_skeleton(json_path: Path) -> dict[str, np.ndarray]:
    """Load skeleton bone world transforms from JSON → {name: numpy 4×4}."""
    key = str(json_path)
    if key in _skel_cache:
        return _skel_cache[key]
    if not json_path.exists():
        _skel_cache[key] = {}
        return {}
    try:
        with open(json_path, 'r') as fh:
            raw = json.load(fh)
        result = {name: np.array(m, dtype=np.float64) for name, m in raw.items()}
    except Exception:
        result = {}
    _skel_cache[key] = result
    return result


# Public API for tests
def load_skeleton_from_nif(json_path: Path) -> dict[str, np.ndarray]:
    """Public wrapper for loading skeleton data."""
    return _load_skeleton(json_path)


def build_bone_mapping(ob_skel: dict, sk_skel: dict) -> dict[str, str]:
    """Build {ob_name: sk_name} mapping for bones present in both skeletons."""
    from .skyrim_overrides import OBLIVION_TO_SKYRIM_BONE_MAP
    mapping = {}
    for ob_name, sk_name in OBLIVION_TO_SKYRIM_BONE_MAP.items():
        if ob_name in ob_skel and sk_name in sk_skel:
            mapping[ob_name] = sk_name
    return mapping


# ---------------------------------------------------------------------------
# NIF helpers (row-vector convention, PyFFI data structures)
# ---------------------------------------------------------------------------

def _m44_to_np(m) -> np.ndarray:
    """Convert PyFFI Matrix44 to numpy 4×4 (row-vector convention)."""
    return np.array([
        [m.m_11, m.m_12, m.m_13, m.m_14],
        [m.m_21, m.m_22, m.m_23, m.m_24],
        [m.m_31, m.m_32, m.m_33, m.m_34],
        [m.m_41, m.m_42, m.m_43, m.m_44],
    ], dtype=np.float64)


def _np_to_nif_node(node, M: np.ndarray):
    """Write numpy 4×4 (row-vector) to a NiNode's local transform."""
    node.rotation.m_11 = float(M[0, 0]); node.rotation.m_12 = float(M[0, 1]); node.rotation.m_13 = float(M[0, 2])
    node.rotation.m_21 = float(M[1, 0]); node.rotation.m_22 = float(M[1, 1]); node.rotation.m_23 = float(M[1, 2])
    node.rotation.m_31 = float(M[2, 0]); node.rotation.m_32 = float(M[2, 1]); node.rotation.m_33 = float(M[2, 2])
    node.translation.x = float(M[3, 0]); node.translation.y = float(M[3, 1]); node.translation.z = float(M[3, 2])
    node.scale = 1.0


def _skin_transform_to_np(st) -> np.ndarray:
    """Convert PyFFI SkinTransform to numpy 4×4 (row-vector convention)."""
    M = np.eye(4, dtype=np.float64)
    M[0, 0] = st.rotation.m_11; M[0, 1] = st.rotation.m_12; M[0, 2] = st.rotation.m_13
    M[1, 0] = st.rotation.m_21; M[1, 1] = st.rotation.m_22; M[1, 2] = st.rotation.m_23
    M[2, 0] = st.rotation.m_31; M[2, 1] = st.rotation.m_32; M[2, 2] = st.rotation.m_33
    M[3, 0] = st.translation.x; M[3, 1] = st.translation.y; M[3, 2] = st.translation.z
    return M


def _write_skin_transform(st, M: np.ndarray):
    """Write numpy 4×4 (row-vector) to a PyFFI SkinTransform."""
    st.rotation.m_11 = float(M[0, 0]); st.rotation.m_12 = float(M[0, 1]); st.rotation.m_13 = float(M[0, 2])
    st.rotation.m_21 = float(M[1, 0]); st.rotation.m_22 = float(M[1, 1]); st.rotation.m_23 = float(M[1, 2])
    st.rotation.m_31 = float(M[2, 0]); st.rotation.m_32 = float(M[2, 1]); st.rotation.m_33 = float(M[2, 2])
    st.translation.x = float(M[3, 0]); st.translation.y = float(M[3, 1]); st.translation.z = float(M[3, 2])
    st.scale = 1.0


def _get_block_name(block) -> str:
    """Get a NIF block's name as a Python string."""
    return bytes(block.name).rstrip(b'\x00').decode('latin-1', errors='replace')


def _build_parent_map(root):
    """Build id(child) → parent_node map for NiNode hierarchy."""
    parent_map = {}
    for node in root.tree():
        if not hasattr(node, 'children'):
            continue
        for child in node.children:
            if child is not None and isinstance(child, NifFormat.NiNode):
                parent_map[id(child)] = node
    return parent_map


def _get_body_parts_for_geometry(geom_name: str, num_partitions: int) -> list[int]:
    """Return body_part IDs for BSDismemberSkinInstance, one per partition."""
    lower = geom_name.lower()
    for keyword, single_bp, multi_bps in ARMOR_GEOMETRY_BODY_PARTS:
        if keyword in lower:
            if multi_bps is not None and num_partitions > 1:
                result = list(multi_bps)
                while len(result) < num_partitions:
                    result.append(result[-1])
                return result[:num_partitions]
            return [single_bp] * num_partitions
    return [ARMOR_DEFAULT_BODY_PART] * num_partitions


def _resolve_sk_target(name: str, sk_skel: dict) -> tuple:
    """Resolve a bone name to its Skyrim skeleton target.

    Handles both Oblivion-named bones (maps through OB→SK) and
    bones that already have Skyrim names (e.g. PRN bones).

    Returns (sk_name, W_sk_4x4) or (None, None) if not found.
    """
    # Direct lookup (already Skyrim name, e.g. from _add_prn_skin)
    if name in sk_skel:
        return name, sk_skel[name]
    # Map Oblivion name → Skyrim name
    sk_name = OBLIVION_TO_SKYRIM_BONE_MAP.get(name)
    if sk_name and sk_name in sk_skel:
        return sk_name, sk_skel[sk_name]
    return None, None


# ---------------------------------------------------------------------------
# Bind matrix recomputation
# ---------------------------------------------------------------------------

def _manual_update_bind_position(block, skin, skel_root):
    """Recompute NiSkinData transforms from current bone positions.

    S = inv(G)  where G = geometry world transform relative to skel_root
    B_i = G @ inv(W_bone_i)  where W_bone_i = bone world transform relative to skel_root

    Guarantees: S @ B_i @ W_i = inv(G) @ G @ inv(W_i) @ W_i = I
    """
    skin_data = skin.data
    if skin_data is None:
        return

    try:
        G = _m44_to_np(block.get_transform(skel_root))
    except (ValueError, RuntimeError):
        G = np.eye(4)

    G_inv = np.linalg.inv(G)
    _write_skin_transform(skin_data.skin_transform, G_inv)

    for i in range(skin_data.num_bones):
        if i >= skin.num_bones:
            break
        bone = skin.bones[i]
        if bone is None:
            continue
        try:
            W_bone = _m44_to_np(bone.get_transform(skel_root))
        except (ValueError, RuntimeError):
            continue

        B = G @ np.linalg.inv(W_bone)
        _write_skin_transform(skin_data.bone_list[i].skin_transform, B)


# ---------------------------------------------------------------------------
# Skin partition regeneration
# ---------------------------------------------------------------------------

def _regen_skin_partition(block, skin, geom_name: str):
    """Regenerate NiSkinPartition in Skyrim triangle format."""
    skin.skin_partition = None
    try:
        block.update_skin_partition(
            maxbonesperpartition=18,
            maxbonespervertex=4,
            stripify=False,
            stitchstrips=False,
            padbones=False,
        )
    except Exception:
        pass

    if isinstance(skin, NifFormat.BSDismemberSkinInstance):
        new_n = (skin.skin_partition.num_skin_partition_blocks
                 if skin.skin_partition is not None else 0)
        body_parts = _get_body_parts_for_geometry(geom_name, max(new_n, 1))
        skin.num_partitions = new_n
        skin.partitions.update_size()
        for pi in range(new_n):
            skin.partitions[pi].body_part = body_parts[pi]
            skin.partitions[pi].part_flag.pf_editor_visible = 1
            skin.partitions[pi].part_flag.pf_start_net_boneset = 1




# ---------------------------------------------------------------------------
# Quaternion and rotation utilities
# ---------------------------------------------------------------------------


def _batch_quat_rotate(q, v):
    """Rotate N points by N quaternions.  q: (N,4) [w,x,y,z], v: (N,3) → (N,3).

    Uses the formula: v' = v + 2w(u×v) + 2(u×(u×v))
    where u = q.xyz, w = q.w.  Convention-independent for 3-vectors.
    """
    w = q[:, 0:1]      # (N, 1)
    u = q[:, 1:4]      # (N, 3)
    uv = np.cross(u, v)        # (N, 3)
    uuv = np.cross(u, uv)      # (N, 3)
    return v + 2.0 * (w * uv + uuv)


def _mat3_to_quat(R: np.ndarray) -> np.ndarray:
    """3×3 rotation matrix (row-vector convention) → quaternion [w, x, y, z]."""
    trace = R[0, 0] + R[1, 1] + R[2, 2]
    if trace > 0:
        s = 0.5 / math.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (R[2, 1] - R[1, 2]) * s
        y = (R[0, 2] - R[2, 0]) * s
        z = (R[1, 0] - R[0, 1]) * s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        w = (R[2, 1] - R[1, 2]) / s;  x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s;  z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        w = (R[0, 2] - R[2, 0]) / s;  x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s;                   z = (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        w = (R[1, 0] - R[0, 1]) / s;  x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s;  z = 0.25 * s
    q = np.array([w, x, y, z], dtype=np.float64)
    n = np.linalg.norm(q)
    return q / n if n > 1e-10 else np.array([1.0, 0.0, 0.0, 0.0])



# ---------------------------------------------------------------------------
# Animation-based FK pre-deformation (Phase B.1)
# ---------------------------------------------------------------------------
# Uses cached per-chain animation pose to deform the mesh ~90% of the way
# from OB T-pose to SK rest pose, using Oblivion's own skin weights and
# standard LBS (Linear Blend Skinning). The remaining small residual is
# then handled by Gaussian spatial blending (Phase B.2).
#
# The animation pose cache is built by tools/kf_animation_explorer.py --build-cache
# which searches ALL Oblivion .kf animation files for the best per-chain pose.

_ANIM_POSE_PATH = _GENERATED_DIR / 'best_animation_pose.json'
_anim_delta_cache = None


def _load_animation_deltas():
    """Load pre-computed delta matrices (inv(rest_world) @ anim_world) per bone.
    
    These are computed by tools/kf_animation_explorer.py --build-cache using the
    FULL Oblivion skeleton hierarchy. Each delta transforms a vertex from its
    rest-pose position to the best-matching animation pose position.
    """
    global _anim_delta_cache
    if _anim_delta_cache is not None:
        return _anim_delta_cache
    if not _ANIM_POSE_PATH.exists():
        _anim_delta_cache = {}
        return _anim_delta_cache
    try:
        with open(_ANIM_POSE_PATH, 'r') as fh:
            raw = json.load(fh)
        deltas = raw.get('delta_matrices', {})
        _anim_delta_cache = {}
        for bone_name, flat in deltas.items():
            _anim_delta_cache[bone_name] = np.array(flat, dtype=np.float64).reshape(4, 4)
    except Exception:
        _anim_delta_cache = {}
    return _anim_delta_cache


def _deform_vertices_animation_fk(skinned_geoms, skel_root, bone_deltas):
    """Apply FK animation deformation via Dual Quaternion Skinning (DQS).

    DQS blends rigid-body transforms in dual quaternion space (normalized linear
    blend) rather than matrix space, avoiding the 'candy-wrapper' volume collapse
    of LBS at joint weight boundaries (shoulders, hips, wrists).

    For each vertex:
      qr_blend = normalize(Σ w_i * antipodal_align(qr_i))
      qd_blend = (Σ w_i * antipodal_align(qd_i)) / |qr_blend|
      v' = rotate(qr_blend, v) + 2 * Im(qd_blend * conj(qr_blend))

    where (qr_i, qd_i) = delta_to_dq(delta_i = inv(rest_world_i) @ anim_world_i).
    Uses Oblivion's own skin weights — same data, better blend math.
    """
    if not bone_deltas:
        return

    for block, is_prn, prn_bone_name in skinned_geoms:
        if is_prn:
            continue

        skin = block.skin_instance
        geom_data = block.data
        skin_data = skin.data

        if geom_data is None or geom_data.num_vertices == 0:
            continue
        if skin_data is None:
            continue

        num_verts = geom_data.num_vertices

        # Build per-bone dual quaternions indexed by bone slot in skin.bones[]
        bone_slot_qr = {}   # slot_index -> [w,x,y,z] rotation quaternion
        bone_slot_qd = {}   # slot_index -> [w,x,y,z] translation dual part
        for i in range(skin.num_bones):
            bone_node = skin.bones[i]
            if bone_node is None:
                continue
            name = _get_block_name(bone_node)
            if name in bone_deltas:
                delta = bone_deltas[name]
                # MUST transpose: delta[:3,:3] is row-vector convention,
                # _mat3_to_quat expects column-vector (standard) convention.
                qr = _mat3_to_quat(delta[:3, :3].T)
                # qd = 0.5 * pure_quat(t) * qr  (encodes translation in DQ)
                t = delta[3, :3]
                w1, x1, y1, z1 = 0.0, t[0], t[1], t[2]   # pure quaternion for t
                w2, x2, y2, z2 = qr
                qd = 0.5 * np.array([
                    w1*w2 - x1*x2 - y1*y2 - z1*z2,
                    w1*x2 + x1*w2 + y1*z2 - z1*y2,
                    w1*y2 - x1*z2 + y1*w2 + z1*x2,
                    w1*z2 + x1*y2 - y1*x2 + z1*w2,
                ], dtype=np.float64)
                bone_slot_qr[i] = qr
                bone_slot_qd[i] = qd

        if not bone_slot_qr:
            continue

        # Geometry transform
        try:
            G = _m44_to_np(block.get_transform(skel_root))
        except (ValueError, RuntimeError):
            G = np.eye(4)
        G_rot = G[:3, :3]
        G_trans = G[3, :3]
        G_is_identity = np.allclose(G, np.eye(4), atol=1e-6)

        # Read vertices
        verts = np.zeros((num_verts, 3), dtype=np.float64)
        for vi in range(num_verts):
            v = geom_data.vertices[vi]
            verts[vi] = [v.x, v.y, v.z]

        if G_is_identity:
            verts_world = verts
        else:
            verts_world = verts @ G_rot + G_trans

        # Build skin weight arrays: (V, 4) slots
        vert_weights = np.zeros((num_verts, 4), dtype=np.float64)
        vert_bone_ids = np.full((num_verts, 4), -1, dtype=np.int32)
        for bi in range(skin_data.num_bones):
            bone_data = skin_data.bone_list[bi]
            for vw in bone_data.vertex_weights:
                vi = vw.index
                w = float(vw.weight)
                if vi >= num_verts or w < 1e-6:
                    continue
                for s in range(4):
                    if vert_bone_ids[vi, s] < 0:
                        vert_bone_ids[vi, s] = bi
                        vert_weights[vi, s] = w
                        break

        # Normalize and sort slots by descending weight (slot 0 = antipodal reference)
        w_sum = vert_weights.sum(axis=1, keepdims=True)
        w_sum[w_sum < 1e-10] = 1.0
        vert_weights /= w_sum
        sort_order = np.argsort(-vert_weights, axis=1)
        vert_weights = np.take_along_axis(vert_weights, sort_order, axis=1)
        vert_bone_ids = np.take_along_axis(vert_bone_ids, sort_order, axis=1)

        # Build DQ lookup tables indexed by bone slot
        max_bi = int(vert_bone_ids.max()) + 1 if vert_bone_ids.max() >= 0 else 1
        qr_table = np.zeros((max_bi, 4), dtype=np.float64)
        qd_table = np.zeros((max_bi, 4), dtype=np.float64)
        qr_table[:, 0] = 1.0  # default: identity rotation
        for bi, qr in bone_slot_qr.items():
            if bi < max_bi:
                qr_table[bi] = qr
                qd_table[bi] = bone_slot_qd[bi]

        # Gather DQs for each vertex-bone slot: (V, 4slots, 4quat)
        safe_ids = np.where(vert_bone_ids >= 0, vert_bone_ids, 0)
        qr_gath = qr_table[safe_ids]   # (V, 4, 4)
        qd_gath = qd_table[safe_ids]   # (V, 4, 4)

        # Zero out invalid slots
        invalid = vert_bone_ids < 0    # (V, 4)
        qr_gath[invalid] = np.array([1., 0., 0., 0.])
        qd_gath[invalid] = np.zeros(4)

        # Antipodal alignment: flip DQs whose qr is on the wrong hemisphere.
        # Slot 0 (highest-weight bone) is the reference hemisphere.
        ref = qr_gath[:, 0, :]                          # (V, 4)
        dots = np.einsum('vi,vsi->vs', ref, qr_gath)    # (V, 4)
        flip = (dots < 0)                               # (V, 4)
        qr_gath[flip] *= -1
        qd_gath[flip] *= -1

        # Weighted sum (zero weight for invalid slots)
        w_masked = np.where(vert_bone_ids >= 0, vert_weights, 0.0)   # (V, 4)
        qr_blend = (w_masked[:, :, None] * qr_gath).sum(axis=1)      # (V, 4)
        qd_blend = (w_masked[:, :, None] * qd_gath).sum(axis=1)      # (V, 4)

        # Normalize
        mag = np.maximum(np.linalg.norm(qr_blend, axis=1, keepdims=True), 1e-10)
        qr_blend /= mag
        qd_blend /= mag

        # Extract translation: t = 2 * Im(qd * conj(qr))
        # = 2 * (xyz_d * w_r  -  xyz_r * w_d  +  xyz_r × xyz_d)
        w_r  = qr_blend[:, 0:1];  xyz_r = qr_blend[:, 1:4]
        w_d  = qd_blend[:, 0:1];  xyz_d = qd_blend[:, 1:4]
        t_vec = 2.0 * (xyz_d * w_r - xyz_r * w_d + np.cross(xyz_r, xyz_d))  # (V, 3)

        # Apply: v' = rotate(qr, v) + t
        new_verts_world = _batch_quat_rotate(qr_blend, verts_world) + t_vec

        # Convert back to geometry-local
        if G_is_identity:
            new_verts = new_verts_world
        else:
            G_rot_inv = np.linalg.inv(G_rot)
            new_verts = (new_verts_world - G_trans) @ G_rot_inv

        for vi in range(num_verts):
            geom_data.vertices[vi].x = float(new_verts[vi, 0])
            geom_data.vertices[vi].y = float(new_verts[vi, 1])
            geom_data.vertices[vi].z = float(new_verts[vi, 2])

        # Normals: rotation only (no translation)
        has_normals = hasattr(geom_data, 'has_normals') and geom_data.has_normals
        if has_normals:
            norms_arr = np.zeros((num_verts, 3), dtype=np.float64)
            for vi in range(num_verts):
                n = geom_data.normals[vi]
                norms_arr[vi] = [n.x, n.y, n.z]
            if not G_is_identity:
                norms_arr = norms_arr @ G_rot
            new_norms = _batch_quat_rotate(qr_blend, norms_arr)
            if not G_is_identity:
                G_rot_inv = np.linalg.inv(G_rot)
                new_norms = new_norms @ G_rot_inv
            lengths = np.linalg.norm(new_norms, axis=1, keepdims=True)
            lengths[lengths < 1e-6] = 1.0
            new_norms /= lengths
            for vi in range(num_verts):
                geom_data.normals[vi].x = float(new_norms[vi, 0])
                geom_data.normals[vi].y = float(new_norms[vi, 1])
                geom_data.normals[vi].z = float(new_norms[vi, 2])

# def _apply_residual_corrections(skinned_geoms, skel_root,
#                                 old_bone_worlds, ob_skel, sk_skel,
#                                 bone_deltas):
#     """Apply Z-scale residual correction after FK deformation.

#     Scales vertices proportionally along Z (height) relative to pelvis to
#     correct for Oblivion/Skyrim skeleton height ratio differences.
#     Only fires when both head AND pelvis OB world transforms are available.
#     Skipped for NIFs that only contain head/neck/arm bones (e.g. helmets) where
#     the pelvis is absent and the scale ratio would be meaningless or wrong.
#     """
#     # Require pelvis to be explicitly present — otherwise the height ratio is
#     # computed from pelvis_z=0 (identity matrix fallback) which produces a
#     # wildly incorrect z_scale and destroys the mesh positions.
#     if 'Bip01 Pelvis' not in old_bone_worlds:
#         return

#     # Compute Z-scale factor from pelvis-to-head height ratio
#     pelvis_ob_z = old_bone_worlds.get('Bip01 Pelvis', np.eye(4))[3, 2]
#     pelvis_sk_name = OBLIVION_TO_SKYRIM_BONE_MAP.get('Bip01 Pelvis')
#     pelvis_sk_z = (sk_skel[pelvis_sk_name][3, 2]
#                    if pelvis_sk_name and pelvis_sk_name in sk_skel
#                    else pelvis_ob_z)

#     head_sk_name = OBLIVION_TO_SKYRIM_BONE_MAP.get('Bip01 Head')
#     head_ob_mat = old_bone_worlds.get('Bip01 Head')
#     head_sk_present = head_sk_name and head_sk_name in sk_skel

#     if head_ob_mat is not None and head_sk_present:
#         head_ob_z = head_ob_mat[3, 2]
#         head_sk_z = sk_skel[head_sk_name][3, 2]
#         ob_height = head_ob_z - pelvis_ob_z
#         sk_height = head_sk_z - pelvis_sk_z
#         # Both heights must be positive; if not, skeleton data is degenerate.
#         if ob_height > 1.0 and sk_height > 1.0:
#             z_scale = sk_height / ob_height
#         else:
#             z_scale = 1.0
#     else:
#         z_scale = 1.0

#     if abs(z_scale - 1.0) <= 0.001:
#         return

#     for block, is_prn, prn_bone_name in skinned_geoms:
#         if is_prn:
#             continue

#         geom_data = block.data
#         if geom_data is None or geom_data.num_vertices == 0:
#             continue

#         num_verts = geom_data.num_vertices

#         try:
#             G = _m44_to_np(block.get_transform(skel_root))
#         except (ValueError, RuntimeError):
#             G = np.eye(4)
#         G_rot = G[:3, :3]
#         G_trans = G[3, :3]
#         G_is_identity = np.allclose(G, np.eye(4), atol=1e-6)

#         verts = np.zeros((num_verts, 3), dtype=np.float64)
#         for vi in range(num_verts):
#             v = geom_data.vertices[vi]
#             verts[vi] = [v.x, v.y, v.z]

#         if G_is_identity:
#             verts_world = verts.copy()
#         else:
#             verts_world = verts @ G_rot + G_trans

#         z_relative = verts_world[:, 2] - pelvis_ob_z
#         verts_world[:, 2] = pelvis_ob_z + z_relative * z_scale

#         if G_is_identity:
#             new_verts = verts_world
#         else:
#             G_rot_inv = np.linalg.inv(G_rot)
#             new_verts = (verts_world - G_trans) @ G_rot_inv

#         for vi in range(num_verts):
#             geom_data.vertices[vi].x = float(new_verts[vi, 0])
#             geom_data.vertices[vi].y = float(new_verts[vi, 1])
#             geom_data.vertices[vi].z = float(new_verts[vi, 2])

#         try:
#             geom_data.update_tangent_space()
#         except Exception:
#             pass

# ---------------------------------------------------------------------------
# Main retarget entry point
# ---------------------------------------------------------------------------

def retarget_skin_to_skyrim(data, src_path: str = '') -> int:
    """Retarget skinned armor from Oblivion skeleton to Skyrim skeleton.

    Called BEFORE _remap_bone_names() — bones still have Oblivion names.
    Uses OB→SK name mapping to find target Skyrim skeleton positions.
    AFTER NiTriStrips → NiTriShape conversion, and AFTER version upgrade.

    Algorithm:
      Phase A: Capture old bone world transforms, reposition bone NiNodes
               to Skyrim skeleton rest positions (correct rotation/roll)
      Phase B: Deform vertices via LBS from OB T-pose to SK rest pose
      Phase C: Recompute NiSkinData bind matrices (S, B_i) from new positions
      Phase D: Regenerate NiSkinPartition in Skyrim triangle format

    Returns the number of geometries retargeted.
    """
    src_lower = src_path.replace('\\', '/').lower()
    female = '/f/' in src_lower

    sk_skel = _load_skeleton(_SKEL_SKYRIM_FEMALE if female else _SKEL_SKYRIM_MALE)
    ob_skel = _load_skeleton(_SKEL_OBLIVION)
    if not sk_skel or not ob_skel:
        return 0

    # Collect skeleton root, bone nodes, and skinned geometries
    skel_root = None
    bone_nodes = set()
    skinned_geoms = []

    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None:
                continue
            skin_data = skin.data
            if skin_data is None:
                continue
            if skin.skeleton_root is not None:
                skel_root = skin.skeleton_root

            # Detect PRN-attached rigid armor (1 bone, identity bind)
            is_prn = False
            prn_bone_name = None
            if skin.num_bones == 1 and skin_data.num_bones >= 1:
                st = skin_data.bone_list[0].skin_transform
                is_prn = (
                    abs(st.rotation.m_11 - 1.0) < 0.001
                    and abs(st.rotation.m_22 - 1.0) < 0.001
                    and abs(st.rotation.m_33 - 1.0) < 0.001
                    and abs(st.translation.x) < 0.001
                    and abs(st.translation.y) < 0.001
                    and abs(st.translation.z) < 0.001
                )
                if is_prn and skin.bones[0] is not None:
                    prn_bone_name = _get_block_name(skin.bones[0])

            skinned_geoms.append((block, is_prn, prn_bone_name))

            if not is_prn:
                for i in range(skin.num_bones):
                    if skin.bones[i] is not None:
                        bone_nodes.add(skin.bones[i])

    if not skel_root or not skinned_geoms:
        return 0

    # --- Capture old bone world transforms BEFORE repositioning ---
    old_bone_worlds = {}
    for bone in bone_nodes:
        name = _get_block_name(bone)
        try:
            old_bone_worlds[name] = _m44_to_np(bone.get_transform(skel_root))
        except (ValueError, RuntimeError):
            pass

    # --- Phase B.1: FK animation pre-deformation (BEFORE bone repositioning) ---
    # Deform vertices ~90% of the way from OB T-pose toward SK rest pose using
    # DQS with delta matrices from the animation corpus cache.
    bone_deltas = _load_animation_deltas()
    fk_applied = False
    if bone_deltas:
        _deform_vertices_animation_fk(skinned_geoms, skel_root, bone_deltas)
        fk_applied = True
        # Phase B.1b: Z-scale correction
        # _apply_residual_corrections(skinned_geoms, skel_root,
        #                             old_bone_worlds, ob_skel, sk_skel,
        #                             bone_deltas)

    # --- Phase A: Move bone NiNodes to Skyrim positions ---
    # PRN meshes: reposition their single bone node
    for block, is_prn, prn_bone_name in skinned_geoms:
        if is_prn and prn_bone_name:
            skin = block.skin_instance
            bone_node = skin.bones[0]
            if bone_node is not None:
                sk_name, W_sk = _resolve_sk_target(prn_bone_name, sk_skel)
                if sk_name is not None:
                    _np_to_nif_node(bone_node, W_sk)

    parent_map = _build_parent_map(skel_root)

    def _depth(node):
        d, cur = 0, node
        while id(cur) in parent_map:
            cur = parent_map[id(cur)]
            d += 1
            if d > 100:
                break
        return d

    # Process bones from root to leaf so parent transforms are set first
    for bone in sorted(bone_nodes, key=_depth):
        name = _get_block_name(bone)
        sk_name, W_sk = _resolve_sk_target(name, sk_skel)
        if sk_name is None:
            continue

        parent_node = parent_map.get(id(bone))
        if parent_node is None or parent_node is skel_root:
            new_local = W_sk
        else:
            try:
                parent_W = _m44_to_np(parent_node.get_transform(skel_root))
                new_local = np.linalg.inv(parent_W) @ W_sk
            except (ValueError, RuntimeError):
                new_local = W_sk

        _np_to_nif_node(bone, new_local)


    # --- Phase C+D: Recompute skin data and regenerate partitions ---
    count = 0
    for block, is_prn, prn_bone_name in skinned_geoms:
        skin = block.skin_instance
        geom_name = _get_block_name(block)

        if is_prn:
            if prn_bone_name:
                sk_name, W_sk = _resolve_sk_target(prn_bone_name, sk_skel)
                if sk_name is not None:
                    bone_pos = W_sk[3, :3]
                    block.translation.x = float(bone_pos[0])
                    block.translation.y = float(bone_pos[1])
                    block.translation.z = float(bone_pos[2])
            _manual_update_bind_position(block, skin, skel_root)
            _regen_skin_partition(block, skin, geom_name)
            count += 1
            continue

        _manual_update_bind_position(block, skin, skel_root)
        _regen_skin_partition(block, skin, geom_name)
        count += 1

    return count
