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

Eleven measured attempts to close the remaining cuirass-edge gap all
regressed; do not retry one without new evidence.
See: docs/commentary/asset_convert_armor.md#cuirass-edge-gap-ideas
"""

import json
import math
import numpy as np
from pathlib import Path


# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from asset_convert import paths

from pyffi.formats.nif import NifFormat

from asset_convert.character.skyrim_overrides import (
    ARMOR_DEFAULT_BODY_PART,
    ARMOR_GEOMETRY_BODY_PARTS,
    OBLIVION_TO_SKYRIM_BONE_MAP,
    SBP_33_HANDS,
    SBP_37_FEET,
    SBP_38_CALVES,
    SBP_44_LOWERBODY,
    SBP_32_BODY,
    SBP_131_HAIR,
)

# ---------------------------------------------------------------------------
# Skeleton data paths and cache
# ---------------------------------------------------------------------------
from asset_convert.character.skyrim_overrides_falloutnv import (
    FALLOUT_SKELETON_MARKERS, bone_map_for, is_fallout_skeleton)
from asset_convert.character.wearable_plan import mesh_is_female

_GENERATED_DIR = paths.GENERATED

SKEL_OBLIVION = _GENERATED_DIR / 'skeleton_bones_oblivion.json'
SKEL_FALLOUT = _GENERATED_DIR / 'skeleton_bones_falloutnv.json'
SKEL_SKYRIM_MALE = _GENERATED_DIR / 'skeleton_bones_skyrim_male.json'
SKEL_SKYRIM_FEMALE = _GENERATED_DIR / 'skeleton_bones_skyrim_female.json'
_skel_cache: dict[str, dict[str, np.ndarray]] = {}


def load_skeleton(json_path: Path) -> dict[str, np.ndarray]:
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
    return load_skeleton(json_path)


def _source_skeleton(data) -> dict:
    """The skeleton matching the bones this NIF actually names.

    A mesh weighted to an FO3-only bone needs the FO3 bind pose; anything
    else is Oblivion.  Decided by AUTHORED bone names, never a plugin name.
    See: docs/commentary/asset_convert_falloutnv.md#selected-by-authored-bone-names
    """
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            name = getattr(block, 'name', None)
            if not name:
                continue
            if bytes(name).rstrip(b'\x00').decode(
                    'latin-1', 'replace') in FALLOUT_SKELETON_MARKERS:
                return load_skeleton(SKEL_FALLOUT) or load_skeleton(SKEL_OBLIVION)
    return load_skeleton(SKEL_OBLIVION)


def build_bone_mapping(ob_skel: dict, sk_skel: dict) -> dict[str, str]:
    """Build {src_name: sk_name} mapping for bones present in both skeletons.

    The source skeleton's own bone names select the table.
    See: docs/commentary/asset_convert_falloutnv.md#fnv-skeleton-bones
    """
    mapping = {}
    for ob_name, sk_name in bone_map_for(ob_skel).items():
        if ob_name in ob_skel and sk_name in sk_skel:
            mapping[ob_name] = sk_name
    return mapping


# ---------------------------------------------------------------------------
# NIF helpers (row-vector convention, PyFFI data structures)
# ---------------------------------------------------------------------------

def m44_to_np(m) -> np.ndarray:
    """Convert PyFFI Matrix44 to numpy 4×4 (row-vector convention)."""
    return np.array([
        [m.m_11, m.m_12, m.m_13, m.m_14],
        [m.m_21, m.m_22, m.m_23, m.m_24],
        [m.m_31, m.m_32, m.m_33, m.m_34],
        [m.m_41, m.m_42, m.m_43, m.m_44],
    ], dtype=np.float64)


def write_node_transform(node, M: np.ndarray):
    """Write numpy 4×4 (row-vector) to a NiNode's local transform."""
    node.rotation.m_11 = float(M[0, 0]); node.rotation.m_12 = float(M[0, 1]); node.rotation.m_13 = float(M[0, 2])
    node.rotation.m_21 = float(M[1, 0]); node.rotation.m_22 = float(M[1, 1]); node.rotation.m_23 = float(M[1, 2])
    node.rotation.m_31 = float(M[2, 0]); node.rotation.m_32 = float(M[2, 1]); node.rotation.m_33 = float(M[2, 2])
    node.translation.x = float(M[3, 0]); node.translation.y = float(M[3, 1]); node.translation.z = float(M[3, 2])
    node.scale = 1.0


def skin_transform_to_np(st) -> np.ndarray:
    """Convert PyFFI SkinTransform to numpy 4×4 (row-vector convention)."""
    M = np.eye(4, dtype=np.float64)
    M[0, 0] = st.rotation.m_11; M[0, 1] = st.rotation.m_12; M[0, 2] = st.rotation.m_13
    M[1, 0] = st.rotation.m_21; M[1, 1] = st.rotation.m_22; M[1, 2] = st.rotation.m_23
    M[2, 0] = st.rotation.m_31; M[2, 1] = st.rotation.m_32; M[2, 2] = st.rotation.m_33
    M[3, 0] = st.translation.x; M[3, 1] = st.translation.y; M[3, 2] = st.translation.z
    return M


def write_skin_transform(st, M: np.ndarray):
    """Write numpy 4×4 (row-vector) to a PyFFI SkinTransform."""
    st.rotation.m_11 = float(M[0, 0]); st.rotation.m_12 = float(M[0, 1]); st.rotation.m_13 = float(M[0, 2])
    st.rotation.m_21 = float(M[1, 0]); st.rotation.m_22 = float(M[1, 1]); st.rotation.m_23 = float(M[1, 2])
    st.rotation.m_31 = float(M[2, 0]); st.rotation.m_32 = float(M[2, 1]); st.rotation.m_33 = float(M[2, 2])
    st.translation.x = float(M[3, 0]); st.translation.y = float(M[3, 1]); st.translation.z = float(M[3, 2])
    st.scale = 1.0


def local_for_world(W_target: np.ndarray, parent_W: np.ndarray) -> np.ndarray:
    """Local transform that lands a node on `W_target` under `parent_W`.

    ROW-VECTOR convention throughout this module (see `m44_to_np`, which puts
    translation in row 3, and `NiAVObject.get_transform`, which composes the
    same way): world = local @ parent.  Therefore

        local = W_target @ inv(parent_W)

    and NOT ``inv(parent_W) @ W_target``.  The reversed form was shipped for
    months without symptoms because the output's bone tree used to be FLAT --
    every bone a direct child of the skeleton root, which takes the
    `parent is skel_root` shortcut and never multiplies at all.  Once the tree
    became nested, every bone below the first level was displaced and the error
    compounded down each chain (measured on a Nehrim shirt: UpperArm 85.87,
    Forearm 162.78, Hand 275.97 units).  Guarded by
    tests/test_skin_retarget.py::TestLocalForWorld.
    """
    return W_target @ np.linalg.inv(parent_W)


def get_block_name(block) -> str:
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


def get_body_parts_for_geometry(geom_name: str, num_partitions: int) -> list[int]:
    """Return body_part IDs for BSDismemberSkinInstance, one per partition.

    Last resort only: the slot normally comes from the wearing record's BMDT
    biped flags (wearable_plan.body_part_for_flags), which is authored data.
    This name match is a guess and is reached only for geometry inside a mesh
    that no ARMO/CLOT record references.
    """
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


# Which body region a skeleton bone belongs to.  Matched against the bones a
# shape is actually WEIGHTED to -- rigging the artist authored, not a name we
# parse.  Ordered most-distal first so 'Bip01 L Toe0' resolves as foot rather
# than leg.  Head deliberately excludes Neck: a cuirass collar routinely
# weights the neck without being headgear.
_BONE_REGION_RULES = [
    (('finger', 'hand'), SBP_33_HANDS),
    (('toe', 'foot'), SBP_37_FEET),
    (('calf',), SBP_38_CALVES),
    (('thigh', 'pelvis'), SBP_44_LOWERBODY),
    (('head',), SBP_131_HAIR),
    (('spine', 'clavicle', 'neck', 'upperarm', 'forearm'), SBP_32_BODY),
]


def _body_part_from_skin_bones(skin, allowed=None):
    """Body part implied by the bones a shape is skinned to, or None.

    Used for a mesh whose record claims SEVERAL biped slots at once (Oblivion's
    Knight of Order armour is one NIF holding helmet, torso, legs and feet, with
    flags 0x003D).  The record cannot say which shape is which, but the skin
    weights can: the Helmet shape binds to Bip01 Head alone, LowerBody to
    thigh/calf/pelvis, Foot to foot/toe.

    *allowed* restricts the answer to the slots the record actually claims, so
    a torso shape that happens to weight the neck cannot become headgear.
    Returns None when the weights are ambiguous or say nothing useful.
    """
    if skin is None:
        return None
    names = []
    for bone in (skin.bones or []):
        if bone is None:
            continue
        nm = bone.name
        names.append((nm.decode('latin-1', errors='replace')
                      if isinstance(nm, bytes) else str(nm)).lower())
    if not names:
        return None
    # Weight each region by how many VERTICES it actually holds, not by whether
    # the bone appears.  A torso shape lists the head bone (collar verts weight
    # to the neck/head chain) but almost none of its mass is there, so a plain
    # presence vote makes every multi-region shape ambiguous -- which sent the
    # Knight of Order torso, legs and arms to the hair slot.
    sd = getattr(skin, 'data', None)
    mass = {}
    for i, nm in enumerate(names):
        region = None
        for keys, bp in _BONE_REGION_RULES:
            if any(k in nm for k in keys):
                region = bp
                break
        if region is None:
            continue
        n_v = 0
        if sd is not None and i < getattr(sd, 'num_bones', 0):
            n_v = getattr(sd.bone_list[i], 'num_vertices', 0) or 0
        mass[region] = mass.get(region, 0) + n_v
    if allowed is not None:
        allowed = set(allowed)
        mass = {k: v for k, v in mass.items() if k in allowed}
    mass = {k: v for k, v in mass.items() if v > 0}
    if not mass:
        return None
    top = max(mass.values())
    winners = [k for k, v in mass.items() if v == top]
    return winners[0] if len(winners) == 1 else None


def dominant_body_part(data, allowed=None):
    """The body part holding most of a NIF's skinned vertex mass, or None.

    For a mesh whose record claims SEVERAL slots at once, ONE offset still has
    to be chosen for the whole file.  Oblivion's Knight of Order armour is
    helmet + torso + legs + feet in a single NIF: its head-ward slot (131) is
    the wrong choice, because the mesh is overwhelmingly a body piece and the
    helmet's dz=+7 lifted the entire suit off the ground.

    Weighing each shape's skinned vertices by region answers it from the rig
    the artist authored rather than from the file's name.
    """
    mass = {}
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, (NifFormat.NiTriShape,
                                      NifFormat.NiTriStrips)):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None:
                continue
            bp = _body_part_from_skin_bones(skin, allowed=allowed)
            if bp is None:
                continue
            gd = block.data
            n = getattr(gd, 'num_vertices', 0) if gd is not None else 0
            mass[bp] = mass.get(bp, 0) + (n or 0)
    if not mass:
        return None
    top = max(mass.values())
    winners = [k for k, v in mass.items() if v == top]
    return winners[0] if len(winners) == 1 else None


def bake_block_transform(block):
    """Fold a geometry block's own rotation/translation/scale into its verts.

    Used for rigid PRN pieces, whose shapes all share one attachment frame:
    the per-shape offset has to live in the vertices so the node can carry the
    bone position instead.  No-op when the transform is already identity.
    """
    M = block.get_transform()          # local, relative to the block's parent
    if M.is_identity():
        return
    gd = block.data
    if gd is None:
        return
    rot = M.get_matrix_33()
    for v in gd.vertices:
        nv = v * M
        v.x, v.y, v.z = nv.x, nv.y, nv.z
    if getattr(gd, 'has_normals', 0):
        for n in gd.normals:
            nn = n * rot
            n.x, n.y, n.z = nn.x, nn.y, nn.z
    block.rotation.set_identity()
    block.scale = 1.0
    try:
        gd.update_center_radius()
    except Exception:
        pass


def get_body_parts_for_bone(bone_name: str, num_partitions: int):
    """Body part IDs implied by the bone a rigid PRN piece is skinned to.

    Mirrors the classification in nif_converter._add_prn_skin, which picks the
    slot from the bone and then had it overwritten here.  Returns None when the
    bone says nothing useful, so the caller falls back to the geometry name.
    """
    lower = (bone_name or '').lower()
    if 'head' in lower or 'neck' in lower:
        bp = SBP_131_HAIR       # helmets/hoods ride the hair slot in Skyrim
    elif 'hand' in lower or 'finger' in lower:
        bp = SBP_33_HANDS
    elif 'foot' in lower or 'toe' in lower:
        bp = SBP_37_FEET
    elif 'calf' in lower or 'thigh' in lower:
        bp = SBP_38_CALVES
    else:
        return None
    return [bp] * num_partitions


def _resolve_sk_target(name: str, sk_skel: dict, src_map: dict = None) -> tuple:
    """Resolve a bone name to its Skyrim skeleton target.

    Handles source-named bones (mapped through src_map, which defaults to
    Oblivion's) and bones that already have Skyrim names (e.g. PRN bones).

    Returns (sk_name, W_sk_4x4) or (None, None) if not found.
    """
    # Direct lookup (already Skyrim name, e.g. from _add_prn_skin)
    if name in sk_skel:
        return name, sk_skel[name]
    sk_name = (src_map or OBLIVION_TO_SKYRIM_BONE_MAP).get(name)
    if sk_name and sk_name in sk_skel:
        return sk_name, sk_skel[sk_name]
    return None, None


# ---------------------------------------------------------------------------
# Bind matrix recomputation
# ---------------------------------------------------------------------------

def manual_update_bind_position(block, skin, skel_root):
    """Recompute NiSkinData transforms from current bone positions.

    S = inv(G)  where G = geometry world transform relative to skel_root
    B_i = G @ inv(W_bone_i)  where W_bone_i = bone world transform relative to skel_root

    Guarantees: S @ B_i @ W_i = inv(G) @ G @ inv(W_i) @ W_i = I
    """
    skin_data = skin.data
    if skin_data is None:
        return

    try:
        G = m44_to_np(block.get_transform(skel_root))
    except (ValueError, RuntimeError):
        G = np.eye(4)

    G_inv = np.linalg.inv(G)
    write_skin_transform(skin_data.skin_transform, G_inv)

    for i in range(skin_data.num_bones):
        if i >= skin.num_bones:
            break
        bone = skin.bones[i]
        if bone is None:
            continue
        try:
            W_bone = m44_to_np(bone.get_transform(skel_root))
        except (ValueError, RuntimeError):
            continue

        B = G @ np.linalg.inv(W_bone)
        write_skin_transform(skin_data.bone_list[i].skin_transform, B)


# ---------------------------------------------------------------------------
# Skin partition regeneration
# ---------------------------------------------------------------------------

def regen_skin_partition(block, skin, geom_name: str, bone_name: str | None = None,
                          authored_body_part: int | None = None,
                          authored_allowed=None):
    """Regenerate NiSkinPartition in Skyrim triangle format.

    bone_name : the skin's single bone, for rigid PRN-attached pieces.  Their
    geometry is named for the ARTWORK ('ArmunAn', 'Plane02'), which matches no
    keyword in ARMOR_GEOMETRY_BODY_PARTS, so the name lookup silently fell back
    to SBP_32_BODY -- tagging helmets as torso armour.  The bone the piece is
    rigid-skinned to states the slot unambiguously, so it wins when present.
    """
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
        n_part = max(new_n, 1)
        # Slot precedence, all authored data first:
        #  1. the record claims exactly ONE body slot -> that is the answer for
        #     every shape, with nothing left to disambiguate.
        #  2. the record claims SEVERAL (Oblivion's Knight of Order armour is
        #     one NIF holding helmet + torso + legs + feet, flags 0x003D) ->
        #     the record cannot say which shape is which, but the SKIN WEIGHTS
        #     can: its Helmet shape binds to Bip01 Head alone, LowerBody to
        #     thigh/calf/pelvis.  Restricted to the slots the record claims.
        #  3. a rigid PRN piece hangs off one bone, which states the slot.
        #  4. nothing authored is available (no record names this mesh) ->
        #     fall back to the geometry-name guess.
        body_parts = None
        if authored_body_part is not None:
            if authored_allowed and len(authored_allowed) > 1:
                bp = _body_part_from_skin_bones(skin, allowed=authored_allowed)
                if bp is not None:
                    body_parts = [bp] * n_part
            else:
                body_parts = [authored_body_part] * n_part
        # The record still outranks the PRN bone: a ring hangs off a FINGER
        # bone, which the bone rules read as hands (33), but vanilla Skyrim
        # partitions rings as 36 and amulets as 40.  Only consult the bone when
        # nothing was authored.
        if body_parts is None and authored_body_part is not None:
            body_parts = [authored_body_part] * n_part
        if body_parts is None and bone_name:
            body_parts = get_body_parts_for_bone(bone_name, n_part)
        if body_parts is None:
            body_parts = get_body_parts_for_geometry(geom_name, n_part)
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

_ANIM_POSE_PATH = _GENERATED_DIR / 'best_animation_pose.json'
_ANIM_POSE_FALLOUT = _GENERATED_DIR / 'best_animation_pose_falloutnv.json'
_anim_delta_cache: dict = {}

#: female -> Morrowind rest skeleton. See: docs/commentary/asset_convert_armor.md#morrowind-pose-cache
SKEL_MORROWIND = {False: _GENERATED_DIR / 'skeleton_bones_morrowind.json',
                  True: _GENERATED_DIR / 'skeleton_bones_morrowind_female.json'}

#: female -> the pose cache built over that Morrowind skeleton.
_ANIM_POSE_MORROWIND = {False: _GENERATED_DIR / 'best_animation_pose_morrowind.json',
                        True: _GENERATED_DIR / 'best_animation_pose_morrowind_female.json'}


def _pose_path(src_skel) -> Path:
    """The pose cache built for `src_skel`.

    A Morrowind skeleton shares Oblivion's bone names, so it is recognized as
    the very (cached) object `load_skeleton` returned for its JSON; FO3/FNV by
    its own bone names; anything else is Oblivion.
    See: docs/commentary/asset_convert_armor.md#morrowind-pose-cache
    """
    if src_skel is None:
        return _ANIM_POSE_PATH
    for female, skel_path in SKEL_MORROWIND.items():
        if src_skel is load_skeleton(skel_path) and _ANIM_POSE_MORROWIND[female].exists():
            return _ANIM_POSE_MORROWIND[female]
    if is_fallout_skeleton(src_skel) and _ANIM_POSE_FALLOUT.exists():
        return _ANIM_POSE_FALLOUT
    return _ANIM_POSE_PATH


def load_animation_deltas(src_skel: dict = None):
    """Pre-computed delta matrices (inv(rest_world) @ anim_world) per bone.

    Built by tools/generators/kf_animation_explorer.py --build-cache over the
    source game's own .kf corpus and skeleton; each delta moves a vertex from
    its rest pose to the best-matching animation pose. The source skeleton
    selects the cache (`_pose_path`).
    See: docs/commentary/asset_convert_falloutnv.md#fnv-animation-pose
    """
    path = _pose_path(src_skel)
    key = str(path)
    if key in _anim_delta_cache:
        return _anim_delta_cache[key]
    result = {}
    if path.exists():
        try:
            with open(path, 'r') as fh:
                raw = json.load(fh)
            for bone_name, flat in raw.get('delta_matrices', {}).items():
                result[bone_name] = np.array(
                    flat, dtype=np.float64).reshape(4, 4)
        except Exception:
            result = {}
    _anim_delta_cache[key] = result
    return result


def _bake_geoms_to_bind_pose(skinned_geoms, skel_root):
    """Rewrite skinned geometry so stored vertices sit in skeleton space.

    For each vertex the bind pose is the weighted blend of
    ``skin_transform_i @ bone_world_i`` (the standard skinning contract, cf.
    NifSkope ``glmesh.cpp``).  When that blend already equals the stored
    coordinates the mesh is left byte-identical; otherwise vertices and
    normals are moved onto it and the per-bone ``skin_transform`` matrices are
    reset to ``inv(bone_world)`` so the mesh still binds to the same pose.

    Returns the number of geometries actually rewritten.
    """
    baked = 0
    for block, is_prn, _prn_bone in skinned_geoms:
        if is_prn:
            continue
        skin = getattr(block, 'skin_instance', None)
        geom_data = getattr(block, 'data', None)
        if skin is None or skin.data is None or geom_data is None:
            continue
        num_verts = geom_data.num_vertices
        if not num_verts:
            continue

        try:
            G = m44_to_np(block.get_transform(skel_root))
        except (ValueError, RuntimeError):
            G = np.eye(4)
        G_is_identity = np.allclose(G, np.eye(4), atol=1e-6)

        verts = np.array([[v.x, v.y, v.z] for v in geom_data.vertices],
                         dtype=np.float64)
        verts_world = verts if G_is_identity else verts @ G[:3, :3] + G[3, :3]

        # Per-vertex blended bind matrix (full 4x4 so normals get the same
        # rotation the positions received).  The engine chain is
        # S @ B_i @ W_i applied to the G-transformed vertex, where the overall
        # S is normally inv(G) — so G cancels and the bind pose is driven by
        # the RAW stored coordinates.  S must therefore be part of the blend.
        skin_data = skin.data
        S = np.eye(4, dtype=np.float64)
        _s = skin_data.skin_transform
        _sr = _s.rotation
        S[:3, :3] = [[_sr.m_11, _sr.m_12, _sr.m_13],
                     [_sr.m_21, _sr.m_22, _sr.m_23],
                     [_sr.m_31, _sr.m_32, _sr.m_33]]
        S[3, :3] = [_s.translation.x, _s.translation.y, _s.translation.z]
        acc = np.zeros((num_verts, 4, 4), dtype=np.float64)
        wsum = np.zeros(num_verts, dtype=np.float64)
        bone_worlds = {}
        for bi in range(min(skin_data.num_bones, skin.num_bones)):
            bone_node = skin.bones[bi]
            if bone_node is None:
                continue
            bone_data = skin_data.bone_list[bi]
            st = bone_data.skin_transform
            r = st.rotation
            B = np.eye(4, dtype=np.float64)
            B[:3, :3] = [[r.m_11, r.m_12, r.m_13],
                         [r.m_21, r.m_22, r.m_23],
                         [r.m_31, r.m_32, r.m_33]]
            B[3, :3] = [st.translation.x, st.translation.y, st.translation.z]
            try:
                W = m44_to_np(bone_node.get_transform(skel_root))
            except (ValueError, RuntimeError):
                continue
            bone_worlds[bi] = W
            M = S @ B @ W
            n = bone_data.num_vertices
            if not n:
                continue
            idx = np.fromiter((vw.index for vw in bone_data.vertex_weights),
                              dtype=np.int64, count=n)
            wts = np.fromiter((vw.weight for vw in bone_data.vertex_weights),
                              dtype=np.float64, count=n)
            keep = (idx >= 0) & (idx < num_verts) & (wts > 0.0)
            if not keep.any():
                continue
            idx = idx[keep]; wts = wts[keep]
            np.add.at(acc, idx, wts[:, None, None] * M[None, :, :])
            np.add.at(wsum, idx, wts)

        ok = wsum > 1e-6
        if not ok.any():
            continue
        Mv = acc[ok] / wsum[ok, None, None]
        bind_world = verts_world.copy()
        bind_world[ok] = np.einsum('vi,vij->vj', verts_world[ok], Mv[:, :3, :3]) \
            + Mv[:, 3, :3]

        if np.allclose(bind_world[ok], verts_world[ok], atol=1e-3):
            continue    # already skeleton space — leave byte-identical

        # bind_world is SKELETON space.  Store it as-is and neutralise the
        # geometry node, rather than pushing it back through inv(G) — the
        # renderer would otherwise re-apply G on top of coordinates that
        # already contain it.
        new_verts = bind_world
        for vi in range(num_verts):
            geom_data.vertices[vi].x = float(new_verts[vi, 0])
            geom_data.vertices[vi].y = float(new_verts[vi, 1])
            geom_data.vertices[vi].z = float(new_verts[vi, 2])

        if getattr(geom_data, 'has_normals', False) and geom_data.normals:
            norms = np.array([[n.x, n.y, n.z] for n in geom_data.normals],
                             dtype=np.float64)
            nw = norms if G_is_identity else norms @ G[:3, :3]
            new_nw = nw.copy()
            new_nw[ok] = np.einsum('vi,vij->vj', nw[ok], Mv[:, :3, :3])
            ln = np.linalg.norm(new_nw, axis=1, keepdims=True)
            ln[ln < 1e-6] = 1.0
            new_nw /= ln
            for vi in range(num_verts):
                geom_data.normals[vi].x = float(new_nw[vi, 0])
                geom_data.normals[vi].y = float(new_nw[vi, 1])
                geom_data.normals[vi].z = float(new_nw[vi, 2])

        # Vertices are now skeleton-space, so the geometry node must no longer
        # contribute anything — otherwise the renderer applies it a second time
        # (observed as a rest pose shifted by exactly the node translation,
        # 102.45 / 71.58 units, on meshes whose nodes are off the origin).
        # With G neutralised the chain S @ B_i @ W_i reduces to S = identity
        # and B_i = inv(W_i).
        if not G_is_identity:
            write_node_transform(block, np.eye(4))
        write_skin_transform(skin_data.skin_transform, np.eye(4))
        for bi, W in bone_worlds.items():
            write_skin_transform(skin_data.bone_list[bi].skin_transform,
                                  np.linalg.inv(W))
        baked += 1

    return baked


def deform_vertices_animation_fk(skinned_geoms, skel_root, bone_deltas):
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
            name = get_block_name(bone_node)
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
            G = m44_to_np(block.get_transform(skel_root))
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

        deform_src = verts_world

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
        new_verts_world = _batch_quat_rotate(qr_blend, deform_src) + t_vec

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

# ---------------------------------------------------------------------------
# Main retarget entry point
# ---------------------------------------------------------------------------

def _prn_rigid_bone(skin, skin_data):
    """The bone name when this skin is a PRN-attached rigid piece, else None.

    One bone with an identity bind transform is the shape nif_converter's
    _add_prn_skin builds; such a piece skips FK deformation entirely.
    See: docs/commentary/asset_convert_armor.md#prn-attached-rigid-pieces
    """
    if skin.num_bones != 1 or skin_data.num_bones < 1:
        return None
    st = skin_data.bone_list[0].skin_transform
    identity = (
        abs(st.rotation.m_11 - 1.0) < 0.001
        and abs(st.rotation.m_22 - 1.0) < 0.001
        and abs(st.rotation.m_33 - 1.0) < 0.001
        and abs(st.translation.x) < 0.001
        and abs(st.translation.y) < 0.001
        and abs(st.translation.z) < 0.001
    )
    if not identity or skin.bones[0] is None:
        return None
    return get_block_name(skin.bones[0])


def _collect_skin_targets(data):
    """Scan for (skeleton root, deformable bone nodes, skinned geometries).

    Each geometry is (block, is_prn, prn_bone_name); PRN pieces contribute
    no bone nodes because they are never FK-deformed.
    """
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
            skin_data = getattr(skin, 'data', None) if skin else None
            if skin is None or skin_data is None:
                continue
            if skin.skeleton_root is not None:
                skel_root = skin.skeleton_root
            prn_bone_name = _prn_rigid_bone(skin, skin_data)
            skinned_geoms.append((block, prn_bone_name is not None, prn_bone_name))
            if prn_bone_name is not None:
                continue
            for i in range(skin.num_bones):
                if skin.bones[i] is not None:
                    bone_nodes.add(skin.bones[i])
    return skel_root, bone_nodes, skinned_geoms


def _bake_shape_into_bone_frame(block, W_sk):
    """Move one shape of a PRN piece onto the Skyrim bone frame.

    Bakes the shape's own transform into its verts, then sets its translation
    to the bone position, so every shape of a multi-shape piece shares one
    frame and keeps its own authored offset.

    See: docs/commentary/asset_convert_armor.md#prn-multi-shape-bone-frame
    """
    bake_block_transform(block)
    bone_pos = W_sk[3, :3]
    block.translation.x = float(bone_pos[0])
    block.translation.y = float(bone_pos[1])
    block.translation.z = float(bone_pos[2])


def _rig_skeleton(data, morrowind: bool, female: bool) -> dict:
    """The source rest skeleton: Morrowind's when `morrowind`, else by bone names."""
    if morrowind:
        return load_skeleton(SKEL_MORROWIND[female]) or load_skeleton(SKEL_OBLIVION)
    return _source_skeleton(data)


def _deform_vertices(skinned_geoms, skel_root, ob_skel, female, morrowind, wrap) -> None:
    """Phase B: the body wrap when its field loads, else the plain FK pose.

    `wrap` is (allowed, weight, race). body_wrap is imported here because it
    imports this module (through wrap_mesh).
    """
    allowed, weight, race = wrap
    wrapped = 0
    if allowed:
        try:
            from asset_convert.character.body_wrap import deform_geoms_wrap, get_field
            field = get_field(female, morrowind)
            if field is not None:
                wrapped = deform_geoms_wrap(skinned_geoms, skel_root, field, female,
                                            weight=weight, race=race, src_skel=ob_skel)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f'      [WRAP] wrap deform failed ({e}) — falling back to FK')
            wrapped = 0
    if not wrapped:
        bone_deltas = load_animation_deltas(ob_skel)
        if bone_deltas:
            deform_vertices_animation_fk(skinned_geoms, skel_root, bone_deltas)


def _node_depth(node, parent_map) -> int:
    """How many parents `node` has, capped at 101."""
    d, cur = 0, node
    while id(cur) in parent_map:
        cur = parent_map[id(cur)]
        d += 1
        if d > 100:
            break
    return d


def _place_bone(bone, parent_map, skel_root, sk_skel, src_map) -> None:
    """Move one bone node onto its Skyrim bone's world frame, keeping its parent."""
    sk_name, W_sk = _resolve_sk_target(get_block_name(bone), sk_skel, src_map)
    if sk_name is None:
        return
    parent_node = parent_map.get(id(bone))
    new_local = W_sk
    if parent_node is not None and parent_node is not skel_root:
        try:
            new_local = local_for_world(W_sk, m44_to_np(parent_node.get_transform(skel_root)))
        except (ValueError, RuntimeError):
            new_local = W_sk
    write_node_transform(bone, new_local)


def _place_bones(skinned_geoms, bone_nodes, skel_root, sk_skel, src_map) -> None:
    """Phase A: every PRN bone, skin bone and mapped tree node onto the Skyrim skeleton, root first.

    Ancestor-only nodes are placed too: the body splice later binds fill skin
    to them by name, and an unplaced one tears that skin away.
    See: docs/commentary/asset_convert_armor.md#distorted-worn-clothing-nested-bones
    """
    for block, is_prn, prn_bone_name in skinned_geoms:
        bone_node = block.skin_instance.bones[0] if is_prn and prn_bone_name else None
        if bone_node is not None:
            sk_name, W_sk = _resolve_sk_target(prn_bone_name, sk_skel, src_map)
            if sk_name is not None:
                write_node_transform(bone_node, W_sk)
    parent_map = _build_parent_map(skel_root)
    tree = {n for n in skel_root.tree() if isinstance(n, NifFormat.NiNode) and n is not skel_root}
    for bone in sorted(set(bone_nodes) | tree, key=lambda node: _node_depth(node, parent_map)):
        _place_bone(bone, parent_map, skel_root, sk_skel, src_map)


def _rebind(skinned_geoms, skel_root, targets, prn_out, authored) -> int:
    """Phase C+D: bind matrices from the moved bones, then fresh partitions; how many shapes.

    `targets` is (Skyrim skeleton, source bone map); `authored` is
    (authored body part, allowed body parts).
    """
    sk_skel, src_map = targets
    for block, is_prn, prn_bone_name in skinned_geoms:
        if is_prn and prn_out is not None:
            prn_out.add(id(block))
        if is_prn and prn_bone_name:
            sk_name, W_sk = _resolve_sk_target(prn_bone_name, sk_skel, src_map)
            if sk_name is not None:
                _bake_shape_into_bone_frame(block, W_sk)
        skin = block.skin_instance
        manual_update_bind_position(block, skin, skel_root)
        regen_skin_partition(block, skin, get_block_name(block), bone_name=prn_bone_name,
                             authored_body_part=authored[0], authored_allowed=authored[1])
    return len(skinned_geoms)


def retarget_skin_to_skyrim(data, src_path: str = '', prn_out: set | None = None,
                            allow_wrap: bool = True, weight: int = 0,
                            authored_body_part: int | None = None,
                            authored_allowed=None, race=None,
                            morrowind: bool = False) -> int:
    """Retarget skinned armor from its source skeleton to the Skyrim skeleton.

    Called BEFORE remap_bone_names() — bones still have source names — and
    AFTER the strips-to-shapes pass and the version upgrade. `morrowind`
    (the source file was Morrowind's version) selects Morrowind's skeleton,
    pose cache and wrap field.

    Runs Phase 0 (bake into skeleton space), B (deform vertices), A
    (reposition bone nodes) and C+D (rebuild bind data and partitions).
    The order is fixed and each constraint cost a real defect.
    See: docs/commentary/asset_convert_armor.md#retarget-phase-order

    PRN-attached rigid pieces (single bone, identity bind) skip the FK
    deformation; with ``prn_out`` each one's ``id(block)`` is added to it so
    the caller can exempt them from the FK-tuned armor offsets.

    Returns the number of geometries retargeted.
    """
    female = mesh_is_female(src_path)
    sk_skel = load_skeleton(SKEL_SKYRIM_FEMALE if female else SKEL_SKYRIM_MALE)
    ob_skel = _rig_skeleton(data, morrowind, female)
    if not sk_skel or not ob_skel:
        return 0
    src_map = bone_map_for(ob_skel)
    skel_root, bone_nodes, skinned_geoms = _collect_skin_targets(data)
    if not skel_root or not skinned_geoms:
        return 0
    _bake_geoms_to_bind_pose(skinned_geoms, skel_root)
    _deform_vertices(skinned_geoms, skel_root, ob_skel, female, morrowind,
                     (allow_wrap, weight, race))
    _place_bones(skinned_geoms, bone_nodes, skel_root, sk_skel, src_map)
    return _rebind(skinned_geoms, skel_root, (sk_skel, src_map), prn_out,
                   (authored_body_part, authored_allowed))
