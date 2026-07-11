"""Bow bend rig grafting: Oblivion static bows → Skyrim skinned bows.

Skyrim animates bows (limb bend + string draw) through a skeletal rig inside
the weapon NIF itself: seven bones (Bow_MidBone → Lo/Up chains ending in the
two string bones) driven by the behavior graph ``Weapons\\Bow\\BowProject.hkx``
(referenced via a BSBehaviorGraphExtraData on the root).  Oblivion instead
used a NiGeomMorpherController on the bow geometry, which Skyrim cannot play
(and which PyFFI mis-serializes at Skyrim version, so the converter strips it).

This module rebuilds the vanilla rig procedurally:

1. ``capture_string_masks`` — run on the SOURCE nif before controllers are
   stripped.  The Oblivion draw morph moves the string vertices by the full
   draw distance (~28 units) while limb tips move only ~7-10, so thresholding
   the morph deltas identifies the string reliably (verified on all 8 vanilla
   Oblivion bows: string plane sits at x ≈ -15.7, same side as Skyrim's
   string bones at x ≈ -13.7).

2. ``add_bow_rig`` — run on the CONVERTED nif (BSFadeNode root, NiTriShape
   geometry).  Grafts the exact vanilla bone hierarchy (locals lifted from
   vanilla steelbow.nif), skins every shape to it with weights replicating
   the vanilla profile, and adds the BGED behavior-graph reference.

Vanilla weight profile (measured from steelbow.nif, tip |y| ≈ 57):
  wood |y|:  0-4 Mid=1 · 4-16 Mid→B1 · 16-20 B1=1 · 20-36 B1→B2 ·
             36-(tip-12) B2=1 · last 12 units ramp in ~0.42 StringBone
  string:    linear StringBone1↔StringBone2 lerp along y (0.5/0.5 at center),
             blending into the wood/tip rule near the ends.
"""

import numpy as np

from . import pyffi_monkey_patch as _patch  # noqa: F401
from pyffi.formats.nif import NifFormat

from .skin_retarget import (
    _get_block_name,
    _m44_to_np,
    _manual_update_bind_position,
)

BOW_BEHAVIOR_GRAPH = 'Weapons\\Bow\\BowProject.hkx'

# Morph-delta fraction of the max delta above which a vertex counts as string.
_STRING_DELTA_FRAC = 0.4
# String verts must also sit within this distance of the string plane (the
# median x of the strongest-moving verts) — trims flexing limb faces on
# heavily-decorated bows (elven) whose deltas pass the threshold.
_STRING_PLANE_TOL = 2.0

# Vanilla bow bone hierarchy: name -> (parent, rotation 3x3 row-major,
# translation, scale).  Local transforms lifted verbatim from vanilla
# steelbow.nif (identical across vanilla bows — the rig IS the animation
# contract: BowProject.hkx clips store absolute local bone transforms, so the
# rest pose must match vanilla exactly).
BOW_BONES = [
    ('Bow_MidBone', None,
     (-0.0, -1.0, -0.0,
      1.0, -0.0, 0.0,
      -0.0, -0.0, 1.0),
     (1.306356, 6.373506, -0.019776)),
    ('Bow_LoBone1', 'Bow_MidBone',
     (0.994996, -0.099919, 0.0,
      0.099919, 0.994996, -0.0,
      0.0, 0.0, 1.0),
     (12.519382, -0.000001, 0.0)),
    ('Bow_LoBone2', 'Bow_LoBone1',
     (0.944491, -0.328538, 0.0,
      0.328538, 0.944491, 0.0,
      -0.0, 0.0, 1.0),
     (20.906403, 0.000008, 0.0)),
    ('Bow_StringBone1', 'Bow_LoBone2',
     (-0.906363, -0.422501, 0.000333,
      0.422501, -0.906363, -0.000001,
      0.000302, 0.000140, 1.0),
     (30.601265, -0.000002, 0.000001)),
    ('Bow_UpBone1', 'Bow_MidBone',
     (-0.994996, -0.099920, 0.0,
      0.099920, -0.994996, 0.0,
      0.0, 0.0, 1.0),
     (0.000011, -0.0, 0.0)),
    ('Bow_UpBone2', 'Bow_UpBone1',
     (0.944693, 0.327956, -0.000022,
      -0.327956, 0.944693, 0.000002,
      0.000022, 0.000005, 1.0),
     (20.906404, -0.000002, 0.0)),
    ('Bow_StringBone2', 'Bow_UpBone2',
     (-0.906561, 0.422074, 0.000394,
      -0.422074, -0.906561, 0.000001,
      0.000358, -0.000165, 1.0),
     (30.645283, -0.000008, 0.000002)),
]

# Bone slot indices (order of BOW_BONES == order in every skin instance)
_MID, _LO1, _LO2, _SB1, _UP1, _UP2, _SB2 = range(7)


def capture_string_masks(data) -> dict:
    """Identify string vertices from the Oblivion draw morph.

    Must run on the source nif BEFORE controllers are stripped (the converter
    removes NiGeomMorpherController).  Returns {geometry name: bool ndarray}
    for every shape that carries a draw morph; empty dict if none do.
    """
    masks: dict = {}
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, (NifFormat.NiTriShape,
                                      NifFormat.NiTriStrips)):
                continue
            geom_data = block.data
            if geom_data is None or geom_data.num_vertices == 0:
                continue
            morpher = None
            ctrl = block.controller
            while ctrl is not None:
                if isinstance(ctrl, NifFormat.NiGeomMorpherController):
                    morpher = ctrl
                ctrl = getattr(ctrl, 'next_controller', None)
            if morpher is None or morpher.data is None:
                continue
            md = morpher.data
            if md.num_morphs < 2 or md.num_vertices != geom_data.num_vertices:
                continue
            # Morph 0 is the base; use the largest non-base morph (the draw)
            best = None
            for mi in range(1, md.num_morphs):
                dv = np.array([[v.x, v.y, v.z]
                               for v in md.morphs[mi].vectors])
                if best is None or np.abs(dv).max() > np.abs(best).max():
                    best = dv
            mag = np.linalg.norm(best, axis=1)
            peak = mag.max()
            if peak < 5.0:
                continue  # not a draw morph
            cand = mag >= _STRING_DELTA_FRAC * peak
            # String plane = median x of the strongest movers (mid-string)
            verts = np.array([[v.x, v.y, v.z] for v in geom_data.vertices])
            core = verts[mag >= 0.8 * peak]
            if len(core) == 0:
                continue
            plane_x = float(np.median(core[:, 0]))
            mask = cand & (np.abs(verts[:, 0] - plane_x) <= _STRING_PLANE_TOL)
            if mask.any():
                masks[_get_block_name(block)] = mask
    return masks


def _wood_weights(ay: float, y_tip: float) -> tuple:
    """Vanilla wood (limb) weight profile at |y| = ay.

    Returns (w_mid, w_b1, w_b2, w_sb) where b1/b2/sb are the chain bones on
    the vertex's side of the bow.
    """
    tip_ramp = max(y_tip - 12.0, 38.0)
    if ay < 4.0:
        return (1.0, 0.0, 0.0, 0.0)
    if ay < 16.0:
        t = (ay - 4.0) / 12.0
        return (1.0 - t, t, 0.0, 0.0)
    if ay < 20.0:
        return (0.0, 1.0, 0.0, 0.0)
    if ay < 36.0:
        t = (ay - 20.0) / 16.0
        return (0.0, 1.0 - t, t, 0.0)
    if ay < tip_ramp:
        return (0.0, 0.0, 1.0, 0.0)
    # Tip: ramp in the string bone (vanilla tips are ~58% B2 / 42% SB)
    t = min((ay - tip_ramp) / max(y_tip - tip_ramp, 1e-3), 1.0)
    sb = 0.42 * t
    return (0.0, 0.0, 1.0 - sb, sb)


def _vertex_weights(verts: np.ndarray, string_mask, y_tip: float) -> np.ndarray:
    """Per-vertex weights (V, 7) replicating the vanilla bow skinning."""
    nv = len(verts)
    W = np.zeros((nv, 7), dtype=np.float64)
    for i in range(nv):
        y = float(verts[i, 1])
        ay = abs(y)
        lower = y < 0.0
        mid, b1, b2, sb = _wood_weights(ay, y_tip)
        if string_mask is not None and string_mask[i]:
            # String: StringBone1 ↔ StringBone2 lerp along y, blending into
            # the wood/tip rule near the ends so the string stays attached.
            s = min(max((y / y_tip + 1.0) * 0.5, 0.0), 1.0)
            a_norm = min(ay / y_tip, 1.0)
            blend = min(max((a_norm - 0.6) / 0.25, 0.0), 1.0)
            W[i, _SB1] = (1.0 - s) * (1.0 - blend)
            W[i, _SB2] = s * (1.0 - blend)
            if blend > 0.0:
                W[i, _MID] += mid * blend
                W[i, _LO1 if lower else _UP1] += b1 * blend
                W[i, _LO2 if lower else _UP2] += b2 * blend
                W[i, _SB1 if lower else _SB2] += sb * blend
        else:
            W[i, _MID] = mid
            W[i, _LO1 if lower else _UP1] = b1
            W[i, _LO2 if lower else _UP2] = b2
            W[i, _SB1 if lower else _SB2] = sb
    # Normalize (guard degenerate rows)
    totals = W.sum(axis=1, keepdims=True)
    totals[totals < 1e-6] = 1.0
    return W / totals


def _build_bone_nodes(root) -> list:
    """Create the 7 bone NiNodes under root; returns them in BOW_BONES order."""
    nodes: dict = {}
    ordered = []
    for name, parent, rot, trans in BOW_BONES:
        node = NifFormat.NiNode()
        node.name = name.encode('latin-1')
        node.flags = 14
        (node.rotation.m_11, node.rotation.m_12, node.rotation.m_13,
         node.rotation.m_21, node.rotation.m_22, node.rotation.m_23,
         node.rotation.m_31, node.rotation.m_32, node.rotation.m_33) = rot
        node.translation.x, node.translation.y, node.translation.z = trans
        node.scale = 1.0
        nodes[name] = node
        ordered.append(node)
        if parent is None:
            # Insert as FIRST child of root (vanilla bone-before-geometry order)
            old_count = root.num_children
            root.num_children = old_count + 1
            root.children.update_size()
            for ci in range(old_count, 0, -1):
                root.children[ci] = root.children[ci - 1]
            root.children[0] = node
        else:
            p = nodes[parent]
            p.num_children += 1
            p.children.update_size()
            p.children[p.num_children - 1] = node
    return ordered


def _has_bow_rig(root) -> bool:
    for block in root.tree():
        if isinstance(block, NifFormat.NiNode) and \
                _get_block_name(block) == 'Bow_MidBone':
            return True
    return False


def add_bow_rig(data, string_masks: dict) -> int:
    """Skin converted bow geometry to the vanilla 7-bone bend rig.

    Expects the converted nif (BSFadeNode root, NiTriShape geometry, Prn
    already remapped to WeaponBow).  Adds the bone hierarchy, plain
    NiSkinInstance + partition per shape (vanilla bows use NiSkinInstance,
    not BSDismember), and the BGED behavior-graph reference.  Also sets the
    BSXFlags Animated bit so the graph ticks.

    Returns the number of shapes skinned.
    """
    skinned = 0
    for root in data.roots:
        if root is None or not hasattr(root, 'children'):
            continue
        if _has_bow_rig(root):
            continue
        shapes = [b for b in root.tree()
                  if isinstance(b, (NifFormat.NiTriShape, NifFormat.NiTriStrips))
                  and b.data is not None and b.data.num_vertices > 0
                  and getattr(b, 'skin_instance', None) is None]
        if not shapes:
            continue

        bones = _build_bone_nodes(root)

        # Global tip extent across all shapes (string + limbs share it)
        y_tip = 0.0
        for b in shapes:
            vs = np.array([[v.x, v.y, v.z] for v in b.data.vertices])
            G = _m44_to_np(b.get_transform(root))
            vr = vs @ G[:3, :3] + G[3, :3]
            y_tip = max(y_tip, float(np.abs(vr[:, 1]).max()))
        if y_tip < 20.0:
            y_tip = 57.0  # sane fallback (vanilla extent)

        for block in shapes:
            geom_data = block.data
            nv = geom_data.num_vertices
            vs = np.array([[v.x, v.y, v.z] for v in geom_data.vertices])
            G = _m44_to_np(block.get_transform(root))
            vr = vs @ G[:3, :3] + G[3, :3]   # verts in root/attach frame

            mask = string_masks.get(_get_block_name(block))
            if mask is not None and len(mask) != nv:
                mask = None
            W = _vertex_weights(vr, mask, y_tip)

            skin_data = NifFormat.NiSkinData()
            skin_data.has_vertex_weights = 1
            used = [bi for bi in range(7) if (W[:, bi] > 1e-4).any()]
            skin_data.num_bones = len(used)
            skin_data.bone_list.update_size()

            skin = NifFormat.NiSkinInstance()
            skin.skeleton_root = root
            skin.data = skin_data
            skin.num_bones = len(used)
            skin.bones.update_size()

            for slot, bi in enumerate(used):
                skin.bones[slot] = bones[bi]
                entry = skin_data.bone_list[slot]
                idx = np.where(W[:, bi] > 1e-4)[0]
                entry.num_vertices = len(idx)
                entry.vertex_weights.update_size()
                for k, vi in enumerate(idx):
                    entry.vertex_weights[k].index = int(vi)
                    entry.vertex_weights[k].weight = float(W[vi, bi])
            block.skin_instance = skin

            # Bind matrices: S = inv(G), B_i = G @ inv(W_bone) — and per-bone
            # bounding spheres (engine culls skinned shapes by these; zero
            # radius = invisible in game).
            _manual_update_bind_position(block, skin, root)
            for slot, bi in enumerate(used):
                entry = skin_data.bone_list[slot]
                # B = G @ inv(W_bone), so shape-local → bone space is v @ B
                B = _m44_to_np_skin(entry.skin_transform)
                idx = np.where(W[:, bi] > 1e-4)[0]
                vb = np.hstack([vs[idx], np.ones((len(idx), 1))]) @ B
                c = (vb[:, :3].min(0) + vb[:, :3].max(0)) * 0.5
                r = float(np.linalg.norm(vb[:, :3] - c, axis=1).max())
                entry.bounding_sphere_offset.x = float(c[0])
                entry.bounding_sphere_offset.y = float(c[1])
                entry.bounding_sphere_offset.z = float(c[2])
                entry.bounding_sphere_radius = r

            try:
                block.update_skin_partition(
                    maxbonesperpartition=18, maxbonespervertex=4,
                    stripify=False, stitchstrips=False, padbones=False)
            except Exception:
                pass

            # SLSF1_Skinned: the renderer only applies bone deforms when the
            # shader carries this flag — without it the bow renders frozen in
            # bind pose while the graph animates the bones (string never
            # draws).  Shader conversion ran before this rig existed, so the
            # flag must be set here.
            for prop in getattr(block, 'bs_properties', []):
                if isinstance(prop, NifFormat.BSLightingShaderProperty):
                    prop.shader_flags_1.slsf_1_skinned = 1
            skinned += 1

        if skinned:
            _add_bged(root)
            _set_bsx_animated(root)
    return skinned


def _m44_to_np_skin(st) -> np.ndarray:
    M = np.eye(4, dtype=np.float64)
    M[0, :3] = [st.rotation.m_11, st.rotation.m_12, st.rotation.m_13]
    M[1, :3] = [st.rotation.m_21, st.rotation.m_22, st.rotation.m_23]
    M[2, :3] = [st.rotation.m_31, st.rotation.m_32, st.rotation.m_33]
    M[3, :3] = [st.translation.x, st.translation.y, st.translation.z]
    return M


def _add_bged(root):
    """Attach BSBehaviorGraphExtraData → BowProject.hkx (vanilla bow contract)."""
    for ed in getattr(root, 'extra_data_list', []):
        if isinstance(ed, NifFormat.BSBehaviorGraphExtraData):
            return
    bged = NifFormat.BSBehaviorGraphExtraData()
    bged.name = b'BGED'
    bged.behaviour_graph_file = BOW_BEHAVIOR_GRAPH.encode('latin-1')
    bged.controls_base_skeleton = 0
    root.num_extra_data_list += 1
    root.extra_data_list.update_size()
    root.extra_data_list[root.num_extra_data_list - 1] = bged


def _set_bsx_animated(root):
    """OR the Animated bit (0x08) into BSXFlags — the graph never ticks
    without it (vanilla bows: 202/203, both include 0x08)."""
    for ed in getattr(root, 'extra_data_list', []):
        if isinstance(ed, NifFormat.BSXFlags):
            ed.integer_data = int(ed.integer_data) | 0x08
            return
