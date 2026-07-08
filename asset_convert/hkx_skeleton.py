"""Generate a Skyrim LE skeleton.hkx (hkaSkeleton) from an Oblivion skeleton.nif.

Stage 1 of the creature behavior pipeline: emits the minimal packfile a
character project needs — hkRootLevelContainer → hkaAnimationContainer →
hkaSkeleton(name, parentIndices, bones, referencePose). Bone names and local
transforms are taken verbatim from the NIF's NiNode tree (faithful port — the
converted skeleton.nif keeps Oblivion bone names, so animations need no
retargeting).

The ragdoll stage (second hkaSkeleton + hkaSkeletonMappers + hkpPhysicsData +
hkaRagdollInstance, as in vanilla skeleton.hkx) is generated from the
Oblivion skeleton.nif's bhkBlendCollisionObject bodies + constraints by
asset_convert/hkx_ragdoll.py and merged into the same packfile.

Conventions (validated against vanilla deer skeleton.hkx via hkxcmd XML dump):
  - referencePose entries are LOCAL transforms `(t)(q)(s)` with quaternions in
    Havok x,y,z,w order (NIF matrices are row-vector convention w-first —
    converted here).
  - Root bone has parentIndex -1; bones are emitted parent-before-child (DFS).
  - lockTranslation: false for the root + accumulation bones, true elsewhere
    (mirrors vanilla: only root/COM are unlocked).
"""

import math
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from asset_convert import pyffi_monkey_patch  # noqa: F401
from asset_convert.hkx_xml import (HkxPackfile, compile_hkx, decompile_hkx,
                                   fmt_vec)
from pyffi.formats.nif import NifFormat

UNLOCKED_BONES = {'Bip01', 'Bip01 NonAccum'}

# The SSE engine binds the behavior graph to the actor's 3D through a root
# node hard-named 'NPC Root [Root]': ALL 30 vanilla creature skeleton.hkx
# name their anim hkaSkeleton AND its bone 0 exactly that (census 2026-07-08),
# and every vanilla creature skeleton.nif contains a matching NiNode.  An
# Oblivion rig root named 'Bip01' never binds -> actor spawns INVISIBLE with
# only its collision capsule working.  The rename must be applied everywhere
# a bone name is emitted: skeleton.hkx (here), animation tracks/
# originalSkeletonName (hkx_anim), ragdoll lookups (hkx_ragdoll), and the
# converted skeleton/body NIF node names (nif_converter creature mode).
ROOT_BONE_NAME = 'NPC Root [Root]'
# 'Bip02' = 3ds Max second-biped naming (Oblivion horse). Source census over
# all 32 Oblivion.esm creatures: 31x Bip01, 1x Bip02, nothing else.
BONE_RENAMES = {'Bip01': ROOT_BONE_NAME, 'Bip02': ROOT_BONE_NAME}


@dataclass
class Bone:
    name: str
    parent: int
    translation: tuple
    quat_xyzw: tuple
    scale: float


def _mat33_to_quat_xyzw(m) -> tuple:
    """PyFFI Matrix33 (row-vector convention) → unit quaternion (x,y,z,w).

    Shepperd's method on the row-convention matrix directly yields the
    rotation NIF applies to row vectors; validated below by reconstructing
    the matrix (see nif_local_transforms round-trip in tests).
    """
    m00, m01, m02 = m.m_11, m.m_12, m.m_13
    m10, m11, m12 = m.m_21, m.m_22, m.m_23
    m20, m21, m22 = m.m_31, m.m_32, m.m_33
    trace = m00 + m11 + m22
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        w = 0.25 * s
        x = (m12 - m21) / s
        y = (m20 - m02) / s
        z = (m01 - m10) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        w = (m12 - m21) / s
        x = 0.25 * s
        y = (m10 + m01) / s
        z = (m20 + m02) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        w = (m20 - m02) / s
        x = (m10 + m01) / s
        y = 0.25 * s
        z = (m21 + m12) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        w = (m01 - m10) / s
        x = (m20 + m02) / s
        y = (m21 + m12) / s
        z = 0.25 * s
    n = math.sqrt(w * w + x * x + y * y + z * z)
    return (x / n, y / n, z / n, w / n)


def quat_xyzw_to_mat33(q) -> list:
    """Inverse of _mat33_to_quat_xyzw (row-convention 3x3), for validation."""
    x, y, z, w = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
        [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
        [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)],
    ]


def _node_name(node) -> str:
    return bytes(node.name).decode('latin-1').rstrip('\x00')


def find_skeleton_root(nif_data):
    """The first NiNode child of the file root that has NiNode children —
    'Bip01' on every Oblivion creature skeleton (Scene Root itself is the
    file node, not a bone)."""
    for root in nif_data.roots:
        if not isinstance(root, NifFormat.NiNode):
            continue
        candidates = [c for c in root.children
                      if isinstance(c, NifFormat.NiNode)]
        for c in candidates:
            if any(isinstance(gc, NifFormat.NiNode) for gc in c.children):
                return c
        if candidates:
            return candidates[0]
    raise ValueError('no NiNode bone root found')


def collect_bones(root_node) -> list:
    """DFS the NiNode tree into a parent-before-child Bone list."""
    bones = []

    def visit(node, parent_idx):
        idx = len(bones)
        raw = _node_name(node)
        bones.append(Bone(
            name=BONE_RENAMES.get(raw, raw),
            parent=parent_idx,
            translation=(float(node.translation.x), float(node.translation.y),
                         float(node.translation.z)),
            quat_xyzw=_mat33_to_quat_xyzw(node.rotation),
            scale=float(node.scale),
        ))
        for child in node.children:
            if isinstance(child, NifFormat.NiNode):
                visit(child, idx)

    visit(root_node, -1)
    return bones


def load_skeleton_bones(skeleton_nif_path: str) -> list:
    data = NifFormat.Data()
    with open(skeleton_nif_path, 'rb') as f:
        data.read(f)
    return collect_bones(find_skeleton_root(data))


def build_skeleton_xml(bones: list, skeleton_nif_path: str = None) -> str:
    """Render the skeleton packfile XML: anim hkaSkeleton, plus the full
    ragdoll stage (ragdoll skeleton, mappers, physics, ragdoll instance —
    see hkx_ragdoll.py) when the source nif carries one."""
    # vanilla convention: referenced objects defined BEFORE referencers,
    # root container last (hkxcmd's parser crashes on forward references)
    pf = HkxPackfile(first_id=8)
    skel = pf.add('hkaSkeleton')

    skel.param('name', bones[0].name)
    skel.param_array('parentIndices', [b.parent for b in bones])
    skel.param_structs('bones', [
        [('name', b.name),
         ('lockTranslation', b.name not in UNLOCKED_BONES and b.parent != -1)]
        for b in bones])
    pose_lines = [
        fmt_vec(*b.translation) + fmt_vec(*b.quat_xyzw)
        + fmt_vec(b.scale, b.scale, b.scale)
        for b in bones]
    skel.param_raw('referencePose', '\n'.join(pose_lines),
                   numelements=len(bones))
    skel.param_array('referenceFloats', [])
    skel.param_raw('floatSlots', '', numelements=0)
    skel.param_raw('localFrames', '', numelements=0)

    ragdoll_skel = None
    extra_variants = []
    if skeleton_nif_path:
        from asset_convert.hkx_ragdoll import emit_ragdoll, extract_ragdoll
        parts = extract_ragdoll(skeleton_nif_path, bones)
        if parts:
            ragdoll_skel, extra_variants = emit_ragdoll(pf, bones, parts,
                                                        skel.ref)

    anim_container = pf.add('hkaAnimationContainer')
    top = pf.add('hkRootLevelContainer')

    skeletons = [skel.ref] + ([ragdoll_skel.ref] if ragdoll_skel else [])
    anim_container.param_array('skeletons', skeletons)
    anim_container.param_array('animations', [])
    anim_container.param_array('bindings', [])
    anim_container.param_array('attachments', [])
    anim_container.param_array('skins', [])

    top.param_structs('namedVariants', [
        [('name', 'Merged Animation Container'),
         ('className', 'hkaAnimationContainer'),
         ('variant', anim_container.ref)],
    ] + extra_variants)
    return pf.render(top)


def generate_skeleton_hkx(skeleton_nif_path: str, out_hkx_path: str,
                          keep_xml: bool = False) -> list:
    """skeleton.nif → skeleton.hkx (LE 32-bit, incl. ragdoll stage when the
    nif has one). Returns the Bone list."""
    bones = load_skeleton_bones(skeleton_nif_path)
    try:
        xml = build_skeleton_xml(bones, skeleton_nif_path=skeleton_nif_path)
    except Exception as e:
        # ragdoll stage is best-effort — never block the whole project
        print(f'  [warn] ragdoll stage failed for {skeleton_nif_path}: '
              f'{type(e).__name__}: {e}; emitting anim skeleton only')
        xml = build_skeleton_xml(bones)
    xml_path = os.path.splitext(out_hkx_path)[0] + '.hkx.xml'
    os.makedirs(os.path.dirname(os.path.abspath(out_hkx_path)), exist_ok=True)
    with open(xml_path, 'w', encoding='ascii', errors='replace',
              newline='\n') as f:
        f.write(xml)
    compile_hkx(xml_path, out_hkx_path)
    if not keep_xml:
        os.remove(xml_path)
    return bones


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(
        description='Generate Skyrim LE skeleton.hkx from Oblivion skeleton.nif')
    ap.add_argument('skeleton_nif')
    ap.add_argument('out_hkx')
    ap.add_argument('--keep-xml', action='store_true')
    ap.add_argument('--verify', action='store_true',
                    help='round-trip the output through hkxcmd and report')
    args = ap.parse_args()

    bones = generate_skeleton_hkx(args.skeleton_nif, args.out_hkx,
                                  keep_xml=args.keep_xml)
    print(f'{args.out_hkx}: {len(bones)} bones, root {bones[0].name!r}')
    for b in bones[:8]:
        print(f'  [{bones.index(b):2d}] parent={b.parent:3d} {b.name}')
    if args.verify:
        back = args.out_hkx + '.roundtrip.xml'
        decompile_hkx(args.out_hkx, back)
        with open(back, encoding='ascii', errors='replace') as f:
            txt = f.read()
        ok = all(b.name in txt for b in bones)
        print(f'round-trip XML: {os.path.getsize(back)} bytes, '
              f'all bone names present: {ok}')
