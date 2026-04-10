"""Extract bone world-space transforms from Oblivion and Skyrim skeleton NIFs.

Uses PyFFI's get_transform(root) to compute each bone's world-space transform
relative to the skeleton root node.  This ensures the extracted data matches
the exact convention used by PyFFI's update_bind_position() and the NIF
skinning pipeline.

The output is stored as the raw 4x4 matrix values from PyFFI's Matrix44
(row-vector convention: translation in row 4, R in upper-left 3x3).

Usage:
    python tools/extract_skeleton_bones.py

Output:
    asset_convert/generated/skeleton_bones_oblivion.json
    asset_convert/generated/skeleton_bones_skyrim_male.json
    asset_convert/generated/skeleton_bones_skyrim_female.json
"""
import json
import os
import sys
import time

import asset_convert.pyffi_monkey_patch  # noqa: F401 — must precede NifFormat

from pyffi.formats.nif import NifFormat

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OBLIVION_SKELETON = os.path.join(BASE, 'export', 'Oblivion.esm', 'meshes',
                                 'characters', '_male', 'skeleton.nif')
SKYRIM_SKELETON_MALE = os.path.join(BASE, 'references', 'Skyrim Meshes', 'meshes',
                                    'actors', 'character', 'character assets',
                                    'skeleton.nif')
SKYRIM_SKELETON_FEMALE = os.path.join(BASE, 'references', 'Skyrim Meshes', 'meshes',
                                      'actors', 'character',
                                      'character assets female',
                                      'skeleton_female.nif')


def _m44_to_list(m):
    """Convert a PyFFI Matrix44 to a flat list of 16 floats (row-major)."""
    return [
        [float(m.m_11), float(m.m_12), float(m.m_13), float(m.m_14)],
        [float(m.m_21), float(m.m_22), float(m.m_23), float(m.m_24)],
        [float(m.m_31), float(m.m_32), float(m.m_33), float(m.m_34)],
        [float(m.m_41), float(m.m_42), float(m.m_43), float(m.m_44)],
    ]


def _walk_bones(root, node, result):
    """Recursively walk NiNode tree using get_transform(root) for world transforms."""
    name = bytes(node.name).rstrip(b'\x00').decode('latin-1', errors='replace')
    if name and node is not root:
        try:
            W = node.get_transform(root)
            result[name] = _m44_to_list(W)
        except (ValueError, RuntimeError):
            pass  # bone not reachable from root

    if hasattr(node, 'children'):
        for child in node.children:
            if child is None:
                continue
            if isinstance(child, NifFormat.NiNode):
                _walk_bones(root, child, result)


def extract_skeleton(nif_path):
    """Extract all bone world-space transforms from a skeleton NIF.

    Returns dict: bone_name -> 4x4 matrix (list of 4 lists of 4 floats)
    in PyFFI's Matrix44 convention (row-vector: R in upper-left, t in row 4).
    """
    data = NifFormat.Data()
    with open(nif_path, 'rb') as f:
        data.read(f)

    result = {}
    for root in data.roots:
        if root is None:
            continue
        if isinstance(root, NifFormat.NiNode):
            _walk_bones(root, root, result)
    return result


def save_json(bones, path):
    """Save bone transforms to JSON."""
    clean = {}
    for name in sorted(bones.keys()):
        m = bones[name]
        clean[name] = [[round(v, 8) for v in row] for row in m]
    with open(path, 'w') as f:
        json.dump(clean, f, indent=2)
    print(f"  Saved {len(clean)} bones to {path}")


def _translation_from_m44(m):
    """Get translation (row 4) from 4x4 matrix list."""
    return m[3][0], m[3][1], m[3][2]


def main():
    out_dir = os.path.join(BASE, 'asset_convert', 'generated')
    os.makedirs(out_dir, exist_ok=True)

    # Oblivion skeleton
    print(f"Reading Oblivion skeleton: {OBLIVION_SKELETON}")
    ob_bones = extract_skeleton(OBLIVION_SKELETON)
    save_json(ob_bones, os.path.join(out_dir, 'skeleton_bones_oblivion.json'))
    print(f"  Sample bones:")
    for name in ['Bip01', 'Bip01 Pelvis', 'Bip01 Spine2', 'Bip01 Head',
                  'Bip01 R Clavicle', 'Bip01 L Thigh']:
        if name in ob_bones:
            t = _translation_from_m44(ob_bones[name])
            print(f"    {name}: t=({t[0]:.3f}, {t[1]:.3f}, {t[2]:.3f})")

    # Skyrim male skeleton
    print(f"\nReading Skyrim male skeleton: {SKYRIM_SKELETON_MALE}")
    sk_male = extract_skeleton(SKYRIM_SKELETON_MALE)
    save_json(sk_male, os.path.join(out_dir, 'skeleton_bones_skyrim_male.json'))
    print(f"  Sample bones:")
    for name in ['NPC Root [Root]', 'NPC Pelvis [Pelv]', 'NPC Spine2 [Spn2]',
                  'NPC Head [Head]', 'NPC R Clavicle [RClv]', 'NPC L Thigh [LThg]']:
        if name in sk_male:
            t = _translation_from_m44(sk_male[name])
            print(f"    {name}: t=({t[0]:.3f}, {t[1]:.3f}, {t[2]:.3f})")

    # Skyrim female skeleton
    print(f"\nReading Skyrim female skeleton: {SKYRIM_SKELETON_FEMALE}")
    sk_female = extract_skeleton(SKYRIM_SKELETON_FEMALE)
    save_json(sk_female, os.path.join(out_dir, 'skeleton_bones_skyrim_female.json'))
    print(f"  Sample bones:")
    for name in ['NPC Root [Root]', 'NPC Pelvis [Pelv]', 'NPC Spine2 [Spn2]',
                  'NPC Head [Head]']:
        if name in sk_female:
            t = _translation_from_m44(sk_female[name])
            print(f"    {name}: t=({t[0]:.3f}, {t[1]:.3f}, {t[2]:.3f})")

    # Print mapping comparison
    from asset_convert.skyrim_overrides import OBLIVION_TO_SKYRIM_BONE_MAP
    print("\n--- Bone position comparison (Oblivion → Skyrim male) ---")
    for ob_name, sk_name in sorted(OBLIVION_TO_SKYRIM_BONE_MAP.items()):
        ob = ob_bones.get(ob_name)
        sk = sk_male.get(sk_name)
        if ob and sk:
            ob_t = _translation_from_m44(ob)
            sk_t = _translation_from_m44(sk)
            delta = [sk_t[i] - ob_t[i] for i in range(3)]
            print(f"  {ob_name:30s} -> {sk_name:30s}  "
                  f"ob=({ob_t[0]:8.2f},{ob_t[1]:8.2f},{ob_t[2]:8.2f})  "
                  f"sk=({sk_t[0]:8.2f},{sk_t[1]:8.2f},{sk_t[2]:8.2f})  "
                  f"d=({delta[0]:7.2f},{delta[1]:7.2f},{delta[2]:7.2f})")
        elif ob and not sk:
            print(f"  {ob_name:30s} -> {sk_name:30s}  ** MISSING in Skyrim **")



if __name__ == '__main__':
    main()
