"""Explore Oblivion .kf animation data and skeleton for FK-based retargeting.

Usage:
    python tools/generators/kf_animation_explorer.py --skeleton   # Dump OB skeleton bone hierarchy
    python tools/generators/kf_animation_explorer.py --scan-kf    # Scan all .kf files, extract per-bone transforms per frame
    python tools/generators/kf_animation_explorer.py --find-pose   # Find best animation pose matching Skyrim targets
    python tools/generators/kf_animation_explorer.py --build-cache # Build animation pose cache for use in retarget pipeline
"""

import argparse
import concurrent.futures
import json
import math
import os
import sys
import time
if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

import numpy as np
from pathlib import Path
from pyffi.formats.nif import NifFormat

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

#: Source plugin whose skeleton and .kf corpus this run reads; set from --plugin.
PLUGIN = ['Oblivion.esm']

#: The Morrowind rig instead of a plugin's, and which gender; set from --morrowind / --female.
SOURCE = {'morrowind': False, 'female': False}

sys.path.insert(0, str(PROJECT_ROOT))
from asset_convert.character.skyrim_overrides_falloutnv import bone_map_for
from scipy.optimize import minimize as sp_minimize
from tools.generators.kf_morrowind import (is_morrowind_kf, morrowind_sources,
                                           parse_morrowind_kf, pose_to_bind)

#: Largest per-axis rotation, in radians, the refinement may add to a corpus frame.
_THETA_MAX = 0.35


# ---------------------------------------------------------------------------
# Source rig and pose-search primitives
# ---------------------------------------------------------------------------

def _plugin_slug():
    """The source's lowercase stem, used to name generated files.

    See: docs/commentary/asset_convert_armor.md#morrowind-pose-cache
    """
    if SOURCE['morrowind']:
        return 'morrowind_female' if SOURCE['female'] else 'morrowind'
    return PLUGIN[0].rsplit('.', 1)[0].lower()


def _source_files():
    """(skeleton NIF, .kf clips) this run reads."""
    if SOURCE['morrowind']:
        return morrowind_sources(PROJECT_ROOT / 'export', SOURCE['female'])
    return (_character_dir() / 'skeleton.nif',
            sorted(Path(_character_dir()).rglob('*.kf')))


def _source_hierarchy(skel_path) -> dict:
    """The skeleton the corpus animates, at the rest its pose deltas start from.

    Morrowind's is base_anim re-posed onto the bind skeleton skinned parts
    are authored in.
    See: docs/commentary/asset_convert_armor.md#morrowind-pose-cache
    """
    bones = load_skeleton_hierarchy(skel_path)
    if SOURCE['morrowind']:
        pose_to_bind(bones, _source_skeleton_json())
    return bones


def _read_clip(kf_path):
    """One .kf's {bone: {time: (translation, rotation)}}; {} when it cannot be read.

    See: docs/commentary/asset_convert_armor.md#morrowind-pose-cache
    """
    try:
        if is_morrowind_kf(kf_path):
            return parse_morrowind_kf(kf_path)
        return parse_kf_file(kf_path)
    except Exception:
        return {}


def _axis_angle_to_mat3(ax):
    """Axis-angle vector (3,) to 3x3 rotation matrix."""
    theta = np.linalg.norm(ax)
    if theta < 1e-12:
        return np.eye(3)
    k = ax / theta
    K = np.array([[0, -k[2], k[1]],
                  [k[2], 0, -k[0]],
                  [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(theta) * K + (1 - np.cos(theta)) * (K @ K)


def _trial_cost(params, active_bones, base, bones, parent_worlds, targets):
    """Squared target error of a chain whose `base` locals are turned by `params`.

    `targets` is [(source bone, Skyrim target position)].
    """
    worlds = {}
    for i, bname in enumerate(active_bones):
        loc = base[bname].copy()
        loc[:3, :3] = _axis_angle_to_mat3(params[i*3:(i+1)*3]) @ loc[:3, :3]
        parent = bones[bname]['parent']
        worlds[bname] = loc @ worlds.get(parent, parent_worlds.get(parent, np.eye(4)))
    cost = 0.0
    for bone, target in targets:
        if bone in worlds:
            cost += np.sum((worlds[bone][3, :3] - target) ** 2)
    return cost


def _run_trial(frame_locals, active_bones, bones, parent_worlds, targets):
    """(L-BFGS-B result, base locals) refining one corpus frame of a chain."""
    base = {b: frame_locals.get(b, bones[b]['local']).copy() for b in active_bones}
    n_params = len(active_bones) * 3
    result = sp_minimize(_trial_cost, np.zeros(n_params),
                         args=(active_bones, base, bones, parent_worlds, targets),
                         method='L-BFGS-B', bounds=[(-_THETA_MAX, _THETA_MAX)] * n_params,
                         options={'maxiter': 500, 'ftol': 1e-12, 'gtol': 1e-8})
    return result, base


def _source_skeleton_json():
    """Generated skeleton JSON for the source plugin."""
    gen = PROJECT_ROOT / 'asset_convert' / 'generated'
    return gen / ('skeleton_bones_%s.json' % _plugin_slug())


def _character_dir():
    """Exported human character folder holding the skeleton and .kf clips."""
    return (PROJECT_ROOT / 'export' / PLUGIN[0] / 'meshes'
            / 'characters' / '_male')


def _pose_out_path():
    """Where this run's best-pose cache is written."""
    gen = PROJECT_ROOT / 'asset_convert' / 'generated'
    if _plugin_slug() == 'oblivion':
        return gen / 'best_animation_pose.json'
    return gen / ('best_animation_pose_%s.json' % _plugin_slug())


def _m33_to_np(r):
    return np.array([
        [r.m_11, r.m_12, r.m_13],
        [r.m_21, r.m_22, r.m_23],
        [r.m_31, r.m_32, r.m_33],
    ], dtype=np.float64)


def _quat_to_mat3(w, x, y, z):
    """Quaternion [w,x,y,z] to 3x3 rotation matrix (row-vector convention)."""
    # row-vector: v' = v @ R
    n = math.sqrt(w*w + x*x + y*y + z*z)
    if n < 1e-12:
        return np.eye(3)
    w, x, y, z = w/n, x/n, y/n, z/n
    return np.array([
        [1 - 2*(y*y + z*z), 2*(x*y + w*z),     2*(x*z - w*y)],
        [2*(x*y - w*z),     1 - 2*(x*x + z*z), 2*(y*z + w*x)],
        [2*(x*z + w*y),     2*(y*z - w*x),     1 - 2*(x*x + y*y)],
    ], dtype=np.float64)


def load_skeleton_hierarchy(skel_path):
    """Load Oblivion skeleton.nif, return dict of bone_name -> {local_transform, parent, children, world_transform}."""
    with open(skel_path, 'rb') as f:
        data = NifFormat.Data()
        data.read(f)
    
    root = data.roots[0]
    bones = {}
    
    def walk(node, parent_name=None, parent_world=None):
        name = bytes(node.name).decode('latin-1').rstrip('\x00')
        if not name:
            return
        
        R = _m33_to_np(node.rotation)
        t = np.array([node.translation.x, node.translation.y, node.translation.z])
        s = float(node.scale)
        
        local = np.eye(4, dtype=np.float64)
        local[:3, :3] = R * s
        local[3, :3] = t
        
        if parent_world is None:
            world = local.copy()
        else:
            world = local @ parent_world
        
        bones[name] = {
            'local': local,
            'world': world,
            'parent': parent_name,
            'children': [],
        }
        if parent_name and parent_name in bones:
            bones[parent_name]['children'].append(name)
        
        if hasattr(node, 'children'):
            for child in node.children:
                if child is not None and isinstance(child, NifFormat.NiNode):
                    walk(child, name, world)
    
    walk(root)
    return bones


def parse_kf_file(kf_path):
    """Parse a .kf file and extract per-bone transforms at each keyframe time.
    
    Returns dict: {bone_name: {time: (translation, rotation_quat)}}
    where translation is (x,y,z) and rotation_quat is (w,x,y,z)
    """
    with open(kf_path, 'rb') as f:
        data = NifFormat.Data()
        data.read(f)
    
    bone_keyframes = {}
    
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiControllerSequence):
                continue
            
            # Get controlled blocks
            for i in range(block.num_controlled_blocks):
                cb = block.controlled_blocks[i]
                
                # Get bone name from string palette or direct
                bone_name = None
                if hasattr(cb, 'string_palette') and cb.string_palette is not None:
                    palette = cb.string_palette.palette
                    raw = bytes(palette.palette).decode('latin-1', errors='replace')
                    strings = raw.split('\x00')
                    # node_name_offset gives index into the palette
                    offset = cb.node_name_offset
                    if offset >= 0:
                        # Walk through to find the string at that byte offset
                        pos = 0
                        for s in strings:
                            if pos == offset:
                                bone_name = s
                                break
                            pos += len(s) + 1  # +1 for null terminator
                elif hasattr(cb, 'node_name') and cb.node_name:
                    bone_name = bytes(cb.node_name).decode('latin-1').rstrip('\x00')
                
                if not bone_name:
                    continue
                
                # Get interpolator
                interp = cb.interpolator if hasattr(cb, 'interpolator') else None
                if interp is None:
                    continue
                
                if not isinstance(interp, NifFormat.NiTransformInterpolator):
                    continue
                
                td = interp.data
                if td is None:
                    continue
                
                keyframes = {}
                
                # Extract rotation keys
                rot_keys = {}
                if td.num_rotation_keys > 0:
                    rot_type = td.rotation_type
                    if rot_type == 0:  # LINEAR_KEY / XYZ rotation
                        # Check for XYZ euler keys
                        if hasattr(td, 'xyz_rotations') and td.xyz_rotations:
                            pass  # Skip euler for now
                    else:
                        for rk in td.quaternion_keys:
                            t = float(rk.time)
                            q = rk.value
                            rot_keys[t] = (float(q.w), float(q.x), float(q.y), float(q.z))
                
                # Extract translation keys
                trans_keys = {}
                if hasattr(td, 'translations') and td.translations is not None:
                    if td.translations.num_keys > 0:
                        for tk in td.translations.keys:
                            t = float(tk.time)
                            v = tk.value
                            trans_keys[t] = (float(v.x), float(v.y), float(v.z))
                
                # Also check the interpolator's own transform as default
                # Sentinel value -3.4e38 means "use rest pose" — treat as None
                SENTINEL = -3.0e38
                def _is_sentinel(v):
                    return v < SENTINEL
                
                ix, iy, iz = float(interp.translation.x), float(interp.translation.y), float(interp.translation.z)
                if _is_sentinel(ix) or _is_sentinel(iy) or _is_sentinel(iz):
                    default_trans = None  # will use rest pose
                else:
                    default_trans = (ix, iy, iz)
                
                iw, irx, iry, irz = float(interp.rotation.w), float(interp.rotation.x), float(interp.rotation.y), float(interp.rotation.z)
                if _is_sentinel(iw) or _is_sentinel(irx):
                    default_rot = None
                else:
                    default_rot = (iw, irx, iry, irz)
                
                # Also filter out sentinel values from actual keyframe data
                # Filter translation keys
                clean_trans = {}
                for t_key, (tx, ty, tz) in trans_keys.items():
                    if not (_is_sentinel(tx) or _is_sentinel(ty) or _is_sentinel(tz)):
                        clean_trans[t_key] = (tx, ty, tz)
                trans_keys = clean_trans
                
                # Merge all times
                all_times = sorted(set(list(rot_keys.keys()) + list(trans_keys.keys())))
                if not all_times:
                    # Use default values at time 0 if we have any
                    if default_trans is not None or default_rot is not None:
                        all_times = [0.0]
                    else:
                        continue
                
                for t in all_times:
                    rot = rot_keys.get(t, default_rot)
                    trans = trans_keys.get(t, default_trans)
                    # Skip if both are None (rest pose, no animation data)
                    if rot is None and trans is None:
                        continue
                    keyframes[t] = (trans, rot)  # None means "use rest pose for that component"
                
                if keyframes:
                    bone_keyframes[bone_name] = keyframes
    
    return bone_keyframes


def compute_fk_world_positions(skeleton_bones, anim_transforms):
    """Compute world positions for all bones after applying animation transforms.
    
    skeleton_bones: dict from load_skeleton_hierarchy
    anim_transforms: dict {bone_name: (translation, rotation_quat)} for a single frame
    
    Returns dict: {bone_name: world_position (3,)}
    """
    # We need to traverse from root to leaves, applying local transforms
    world_positions = {}
    world_transforms = {}
    
    def get_world(name):
        if name in world_transforms:
            return world_transforms[name]
        
        bone = skeleton_bones.get(name)
        if bone is None:
            return np.eye(4)
        
        # Start with rest-pose local transform
        local = bone['local'].copy()
        
        # Override with animation transform if available
        if name in anim_transforms:
            trans, rot = anim_transforms[name]
            if rot is not None:
                R = _quat_to_mat3(*rot)
                local[:3, :3] = R  # replace rotation (scale=1 in animations)
            if trans is not None:
                local[3, :3] = np.array(trans)  # replace translation
        
        parent_name = bone['parent']
        if parent_name is not None:
            parent_world = get_world(parent_name)
            world = local @ parent_world
        else:
            world = local.copy()
        
        world_transforms[name] = world
        world_positions[name] = world[3, :3].copy()
        return world
    
    for name in skeleton_bones:
        get_world(name)
    
    return world_positions


def get_skyrim_targets_in_ob_space():
    """Load Skyrim bone positions and rotate 90° into Oblivion coordinate space.
    
    Skyrim skeleton is Z-up, Oblivion is effectively X-up (the 90° convention rotation).
    We rotate Skyrim positions by -90° around Z to get them into OB space.
    
    Actually from Procrustes analysis, bone POSITIONS are already in the same coordinate
    system (only 1.8° difference). So let's just load them directly and see.
    """
    sk_json = PROJECT_ROOT / 'asset_convert' / 'generated' / (
        'skeleton_bones_skyrim_%s.json' % ('female' if SOURCE['female'] else 'male'))
    ob_json = _source_skeleton_json()
    for path in (sk_json, ob_json):
        if not path.exists():
            raise SystemExit(
                'missing %s -- run extract_skeleton_bones first' % path)
    
    with open(sk_json) as f:
        sk_raw = json.load(f)
    with open(ob_json) as f:
        ob_raw = json.load(f)
    
    sk_positions = {}
    for name, m in sk_raw.items():
        M = np.array(m, dtype=np.float64)
        sk_positions[name] = M[3, :3]
    
    ob_positions = {}
    for name, m in ob_raw.items():
        M = np.array(m, dtype=np.float64)
        ob_positions[name] = M[3, :3]
    
    return ob_positions, sk_positions


def find_best_pose(skeleton_bones, kf_dir, ob_positions, sk_positions):
    """Scan ALL .kf files and find the animation frame where OB bone positions
    best match the Skyrim target positions (using mapped bone names).
    
    Returns the best per-bone transform dict and score.
    """
    kf_files = sorted(Path(kf_dir).rglob('*.kf'))
    print(f"Scanning {len(kf_files)} .kf files...")
    
    # Build OB->SK name mapping for bones we care about
    mapped_bones = {}
    for ob_name, sk_name in bone_map_for(skeleton_bones).items():
        if ob_name in skeleton_bones and sk_name in sk_positions:
            mapped_bones[ob_name] = sk_name
    
    print(f"  {len(mapped_bones)} mapped bones to match")
    
    best_score = float('inf')
    best_transforms = {}
    best_info = ""
    
    results = []
    
    for kf_file in kf_files:
        try:
            bone_keyframes = parse_kf_file(kf_file)
        except Exception as e:
            continue
        
        if not bone_keyframes:
            continue
        
        # Get all unique timestamps across all bones
        all_times = set()
        for bname, kfs in bone_keyframes.items():
            all_times.update(kfs.keys())
        
        for t in sorted(all_times):
            # Build per-bone transform for this frame
            frame_transforms = {}
            for bname, kfs in bone_keyframes.items():
                # Find closest time
                times = sorted(kfs.keys())
                closest_t = min(times, key=lambda x: abs(x - t))
                if abs(closest_t - t) < 0.01:
                    frame_transforms[bname] = kfs[closest_t]
            
            # Compute FK world positions with this pose
            world_pos = compute_fk_world_positions(skeleton_bones, frame_transforms)
            
            # Score: sum of squared distances for mapped bones
            total_dist = 0
            n_matched = 0
            per_bone_dist = {}
            for ob_name, sk_name in mapped_bones.items():
                if ob_name in world_pos:
                    dist = np.linalg.norm(world_pos[ob_name] - sk_positions[sk_name])
                    total_dist += dist ** 2
                    per_bone_dist[ob_name] = dist
                    n_matched += 1
            
            if n_matched > 0:
                rmsd = math.sqrt(total_dist / n_matched)
                results.append((rmsd, kf_file.name, t, n_matched, frame_transforms, per_bone_dist))
                
                if rmsd < best_score:
                    best_score = rmsd
                    best_transforms = frame_transforms.copy()
                    best_info = f"{kf_file.name} t={t:.3f} (RMSD={rmsd:.3f}, {n_matched} bones)"
    
    # Sort and print top results
    results.sort(key=lambda x: x[0])
    print(f"\nTop 20 best-matching animation frames:")
    print(f"{'RMSD':>8}  {'File':<45}  {'Time':>6}  {'Bones':>5}")
    print("-" * 75)
    for rmsd, fname, t, n, _, _ in results[:20]:
        print(f"{rmsd:8.3f}  {fname:<45}  {t:6.3f}  {n:5d}")
    
    # Print per-bone distances for best match
    if results:
        best = results[0]
        print(f"\nBest match: {best[1]} t={best[2]:.3f}")
        print(f"\nPer-bone distances (best frame):")
        per_bone = best[5]
        for name, dist in sorted(per_bone.items(), key=lambda x: -x[1]):
            sk_name = mapped_bones.get(name, '?')
            print(f"  {name:<30} -> {sk_name:<30} dist={dist:.2f}")
    
    return best_transforms, best_score, best_info


def dump_skeleton(args):
    skel_path = _character_dir() / 'skeleton.nif'
    bones = load_skeleton_hierarchy(skel_path)
    
    print(f"Oblivion skeleton: {len(bones)} bones")
    print()
    
    # Print hierarchy
    def print_tree(name, depth=0):
        bone = bones[name]
        pos = bone['world'][3, :3]
        print(f"{'  ' * depth}{name}: ({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})")
        for child in bone['children']:
            print_tree(child, depth + 1)
    
    # Find roots
    for name, bone in bones.items():
        if bone['parent'] is None:
            print_tree(name)


def scan_kf(args):
    kf_dir = _character_dir()
    kf_files = sorted(kf_dir.glob('*.kf'))
    print(f"Found {len(kf_files)} .kf files in _male/")
    
    # Also check idleanims subdirectory
    idle_dir = kf_dir / 'idleanims'
    if idle_dir.exists():
        idle_kfs = sorted(idle_dir.glob('*.kf'))
        print(f"Found {len(idle_kfs)} .kf files in _male/idleanims/")
        kf_files.extend(idle_kfs)
    
    for kf_file in kf_files[:5]:  # just first 5 for exploration
        print(f"\n{'=' * 60}")
        print(f"File: {kf_file.name}")
        try:
            bone_kfs = parse_kf_file(kf_file)
            print(f"  Bones with keyframes: {len(bone_kfs)}")
            for bname, kfs in sorted(bone_kfs.items()):
                times = sorted(kfs.keys())
                print(f"    {bname}: {len(kfs)} keyframes, t=[{times[0]:.3f}..{times[-1]:.3f}]")
                # Print first keyframe
                t0 = times[0]
                trans, rot = kfs[t0]
                print(f"      t={t0:.3f}: trans=({trans[0]:.2f}, {trans[1]:.2f}, {trans[2]:.2f}) rot=({rot[0]:.3f}, {rot[1]:.3f}, {rot[2]:.3f}, {rot[3]:.3f})")
        except Exception as e:
            print(f"  ERROR: {e}")


def find_pose(args):
    skel_path = _character_dir() / 'skeleton.nif'
    kf_dir = _character_dir()
    
    print("Loading OB skeleton...")
    skeleton_bones = load_skeleton_hierarchy(skel_path)
    
    print("Loading target positions...")
    ob_positions, sk_positions = get_skyrim_targets_in_ob_space()
    
    # Print rest-pose distances for reference
    print("\nRest-pose (T-pose) distances OB->SK:")
    total = 0
    n = 0
    for ob_name, sk_name in sorted(bone_map_for(skeleton_bones).items()):
        if ob_name in skeleton_bones and sk_name in sk_positions:
            ob_pos = skeleton_bones[ob_name]['world'][3, :3]
            sk_pos = sk_positions[sk_name]
            dist = np.linalg.norm(ob_pos - sk_pos)
            total += dist ** 2
            n += 1
            if dist > 1.0:
                print(f"  {ob_name:<30} -> {sk_name:<30} dist={dist:.2f}")
    if n > 0:
        print(f"  Rest-pose RMSD: {math.sqrt(total/n):.3f} ({n} bones)")
    
    print("\nSearching animations...")
    best_transforms, best_score, best_info = find_best_pose(
        skeleton_bones, kf_dir, ob_positions, sk_positions
    )
    
    print(f"\n{'=' * 60}")
    print(f"Best animation pose: {best_info}")
    print(f"Best RMSD: {best_score:.3f}")
    
    # Compare rest-pose RMSD vs best animation RMSD
    rest_rmsd = math.sqrt(total / n) if n > 0 else 0
    print(f"Improvement: {rest_rmsd:.3f} (rest) -> {best_score:.3f} (best anim) = {(1 - best_score/rest_rmsd)*100:.1f}% reduction")


def _mat3_to_quat_rv(R):
    """Convert 3x3 row-vector rotation matrix to quaternion [w,x,y,z]."""
    trace = R[0, 0] + R[1, 1] + R[2, 2]
    if trace > 0:
        s = 2.0 * math.sqrt(1.0 + trace)
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    q = np.array([w, x, y, z])
    return q / np.linalg.norm(q)


# ---------------------------------------------------------------------------
# Pose cache builder
# ---------------------------------------------------------------------------

#: Limb chains blended as whole coherent frames; the spine keeps its rest pose.
_CHAINS = {
    'left_arm':  ['Bip01 L Clavicle', 'Bip01 L UpperArm', 'Bip01 L UpperArmTwist',
                  'Bip01 L Forearm', 'Bip01 L ForearmTwist', 'Bip01 L Hand',
                  'Bip01 L Finger0', 'Bip01 L Finger01', 'Bip01 L Finger02',
                  'Bip01 L Finger1', 'Bip01 L Finger11', 'Bip01 L Finger12',
                  'Bip01 L Finger2', 'Bip01 L Finger21', 'Bip01 L Finger22',
                  'Bip01 L Finger3', 'Bip01 L Finger31', 'Bip01 L Finger32',
                  'Bip01 L Finger4', 'Bip01 L Finger41', 'Bip01 L Finger42'],
    'right_arm': ['Bip01 R Clavicle', 'Bip01 R UpperArm', 'Bip01 R UpperArmTwist',
                  'Bip01 R Forearm', 'Bip01 R ForearmTwist', 'Bip01 R Hand',
                  'Bip01 R Finger0', 'Bip01 R Finger01', 'Bip01 R Finger02',
                  'Bip01 R Finger1', 'Bip01 R Finger11', 'Bip01 R Finger12',
                  'Bip01 R Finger2', 'Bip01 R Finger21', 'Bip01 R Finger22',
                  'Bip01 R Finger3', 'Bip01 R Finger31', 'Bip01 R Finger32',
                  'Bip01 R Finger4', 'Bip01 R Finger41', 'Bip01 R Finger42'],
    'left_leg':  ['Bip01 L Thigh', 'Bip01 L Calf', 'Bip01 L Foot', 'Bip01 L Toe0'],
    'right_leg': ['Bip01 R Thigh', 'Bip01 R Calf', 'Bip01 R Foot', 'Bip01 R Toe0'],
}

#: Order the chains are blended and refined in.
_CHAIN_ORDER = ('left_arm', 'right_arm', 'left_leg', 'right_leg')

#: Softmax sharpness of the chain-level frame blend.
_TEMPERATURE = 1.0

#: Corpus frames each chain's refinement starts from, best first.
_MULTI_START_K = 50

#: X mirror between the left and right side's deltas.
_M4 = np.diag([-1.0, 1.0, 1.0, 1.0])

#: Distance one side must win by before it is mirrored onto the other.
_MIRROR_THRESHOLD = 0.01


class _Pose:
    """The pose being built: every bone's chosen local and world transform."""

    def __init__(self, skeleton_bones):
        """Start from the rest pose of `skeleton_bones`."""
        self.bones = skeleton_bones
        self.local = {n: b['local'].copy() for n, b in skeleton_bones.items()}
        self.world = {n: b['world'].copy() for n, b in skeleton_bones.items()}

    def recompute_subtree(self, name):
        """Rebuild the world transforms below `name` from their locals."""
        for child in self.bones[name]['children']:
            if child in self.local:
                self.world[child] = self.local[child] @ self.world[name]
                self.recompute_subtree(child)

    def refresh(self, names):
        """Rebuild `names` root-to-leaf from their locals, then their subtrees."""
        for bname in names:
            if bname not in self.bones:
                continue
            parent = self.bones[bname]['parent']
            self.world[bname] = self.local[bname] @ self.world.get(parent, np.eye(4))
            self.recompute_subtree(bname)


def _parse_corpus(kf_files) -> list:
    """Every clip's keyframes, read on a thread pool."""
    workers = max(1, (os.cpu_count() or 4) - 1)
    print(f"Parsing {len(kf_files)} .kf files ({workers} threads)...")
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        all_parsed = list(pool.map(_read_clip, kf_files))
    print(f"  Parsed in {time.perf_counter() - t0:.1f}s")
    return all_parsed


def _print_candidates(all_parsed, skeleton_bones) -> None:
    """Print how many per-bone local transforms the corpus offers."""
    print("Building per-bone transform library...")
    counts = {}
    for bone_keyframes in all_parsed:
        for bname, kfs in bone_keyframes.items():
            if bname in skeleton_bones:
                counts[bname] = counts.get(bname, 0) + len(kfs)
    print(f"  {len(counts)} bones, {sum(counts.values())} total candidates")


def _keyed_local(rest, kfs, t):
    """`rest` with the key nearest `t` (within 0.01) applied; an exact key needs no search."""
    local = rest.copy()
    key = kfs.get(t)
    if key is None:
        closest_t = min(sorted(kfs.keys()), key=lambda x: abs(x - t))
        key = kfs[closest_t] if abs(closest_t - t) < 0.01 else None
    if key is not None:
        trans, rot = key
        if rot is not None:
            local[:3, :3] = _quat_to_mat3(*rot)
        if trans is not None:
            local[3, :3] = np.array(trans)
    return local


def _frame_locals(bone_keyframes, t, chain_bones, bones) -> dict:
    """Each chain bone's local at time `t` of one clip; rest where it is unkeyed."""
    out = {}
    for bname in chain_bones:
        if bname not in bones:
            continue
        kfs = bone_keyframes.get(bname)
        rest = bones[bname]['local']
        out[bname] = rest.copy() if kfs is None else _keyed_local(rest, kfs, t)
    return out


def _frame_cost(frame_locals, chain_bones, chain_mapped, pose, sk_positions) -> float:
    """Squared target error of one frame's chain, hung from the pose built so far."""
    worlds = {}
    for bname in chain_bones:
        if bname not in pose.bones:
            continue
        parent = pose.bones[bname]['parent']
        pw = worlds.get(parent, pose.world.get(parent, np.eye(4)))
        worlds[bname] = frame_locals.get(bname, pose.bones[bname]['local']) @ pw
    cost = 0.0
    for ob_bone, sk_bone in chain_mapped:
        if ob_bone in worlds:
            cost += np.sum((worlds[ob_bone][3, :3] - sk_positions[sk_bone]) ** 2)
    return cost


def _chain_frames(all_parsed, chain_bones, chain_mapped, pose, sk_positions):
    """([cost], [frame locals]) over every coherent frame of every clip."""
    costs, frames = [], []
    for bone_keyframes in all_parsed:
        if not bone_keyframes:
            continue
        all_times = set()
        for bkf in bone_keyframes.values():
            all_times.update(bkf.keys())
        for t in all_times:
            locs = _frame_locals(bone_keyframes, t, chain_bones, pose.bones)
            costs.append(_frame_cost(locs, chain_bones, chain_mapped, pose, sk_positions))
            frames.append(locs)
    return costs, frames


def _blend_chain(chain_bones, pose, costs_arr, frames) -> float:
    """Softmax-blend every frame's chain locals into the pose; the effective frame count."""
    log_w = -costs_arr / _TEMPERATURE
    log_w -= log_w.max()
    alpha = np.exp(log_w)
    alpha /= alpha.sum()
    eff_n = 1.0 / np.sum(alpha ** 2) if np.sum(alpha ** 2) > 0 else 1.0
    for bname in chain_bones:
        if bname not in pose.bones:
            continue
        blended = np.zeros((4, 4), dtype=np.float64)
        for i, frame_locs in enumerate(frames):
            blended += alpha[i] * frame_locs.get(bname, pose.bones[bname]['local'])
        U, _S, Vt = np.linalg.svd(blended[:3, :3])
        R = U @ Vt
        if np.linalg.det(R) < 0:
            U[:, -1] *= -1
            R = U @ Vt
        blended[:3, :3] = R
        pose.local[bname] = blended
    return eff_n


def _softmax_chain(chain_name, all_parsed, pose, mapped_bones, sk_positions):
    """Blend one chain from the whole corpus; its (costs, frames), or None without any."""
    chain_bones = _CHAINS[chain_name]
    chain_mapped = [(b, mapped_bones[b]) for b in chain_bones if b in mapped_bones]
    if not chain_mapped:
        return None
    print(f"\n  Chain: {chain_name}")
    costs, frames = _chain_frames(all_parsed, chain_bones, chain_mapped, pose, sk_positions)
    if not costs:
        return None
    costs_arr = np.array(costs)
    best_cost = costs_arr.min()
    eff_n = _blend_chain(chain_bones, pose, costs_arr, frames)
    pose.refresh(chain_bones)
    chain_cost = sum(np.sum((pose.world[ob][3, :3] - sk_positions[sk]) ** 2)
                     for ob, sk in chain_mapped if ob in pose.world)
    print(f"  {chain_name:12s}: corpus RMSD={math.sqrt(chain_cost / len(chain_mapped)):.3f}  "
          f"(best_frame={math.sqrt(best_cost / len(chain_mapped)):.3f}, eff_N={eff_n:.1f})")
    return costs_arr, frames


def _best_trial(corpus, active_bones, pose, targets):
    """(start indices, (cost, result, base locals, trial)) of the best refinement."""
    costs, frames = corpus
    sorted_indices = np.argsort(costs)[:_MULTI_START_K]
    best = (float('inf'), None, None, 0)
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(_MULTI_START_K, os.cpu_count() or 4)) as pool:
        futures = [pool.submit(_run_trial, frames[int(idx)], active_bones,
                               pose.bones, pose.world, targets)
                   for idx in sorted_indices]
        for trial, future in enumerate(concurrent.futures.as_completed(futures)):
            result, base = future.result()
            if result.fun < best[0]:
                best = (result.fun, result, base, trial)
    return sorted_indices, best


def _refine_chain(chain_name, corpus, pose, mapped_bones, sk_positions) -> None:
    """Multi-start L-BFGS-B from the chain's best corpus frames; keep it if it wins."""
    active_bones = [b for b in _CHAINS[chain_name] if b in pose.bones]
    targets = [(b, sk_positions[mapped_bones[b]]) for b in active_bones if b in mapped_bones]
    if not targets or not corpus:
        return
    sorted_indices, (cost, result, base, start) = _best_trial(corpus, active_bones, pose, targets)
    pre_cost = corpus[0][sorted_indices[0]]
    pre_rmsd = math.sqrt(pre_cost / len(targets))
    if not cost < pre_cost:
        print(f"  {chain_name:12s}: {pre_rmsd:.3f} -> no improvement")
        return
    for i, bname in enumerate(active_bones):
        pose.local[bname] = base[bname].copy()
        pose.local[bname][:3, :3] = (_axis_angle_to_mat3(result.x[i*3:(i+1)*3])
                                     @ pose.local[bname][:3, :3])
    pose.refresh(active_bones)
    max_angle = max(np.linalg.norm(result.x[i*3:(i+1)*3]) for i in range(len(active_bones)))
    start_label = f"start#{start}" if start > 0 else "best_frame"
    print(f"  {chain_name:12s}: {pre_rmsd:.3f} -> {math.sqrt(cost / len(targets)):.3f}  "
          f"(max_rot={math.degrees(max_angle):.1f}°, {start_label})")


def _pose_rmsd(pose, mapped_bones, sk_positions) -> float:
    """RMS distance of the mapped bones from their Skyrim targets."""
    total, n = 0, 0
    for ob_name, sk_name in mapped_bones.items():
        if ob_name in pose.world:
            total += np.sum((pose.world[ob_name][3, :3] - sk_positions[sk_name]) ** 2)
            n += 1
    return math.sqrt(total / n) if n > 0 else 0


def _cache_dict(pose, rmsd) -> dict:
    """The cache: changed locals, world positions and rest->pose delta per bone."""
    cache = {'info': f'Approach A softmax T={_TEMPERATURE}, RMSD={rmsd:.4f}',
             'rmsd': float(rmsd), 'bone_transforms': {}, 'world_positions': {},
             'delta_matrices': {}}
    for bname, bone in pose.bones.items():
        local = pose.local[bname]
        if not np.allclose(local, bone['local'], atol=1e-4):
            cache['bone_transforms'][bname] = {
                'translation': [float(x) for x in local[3, :3]],
                'rotation': [float(x) for x in _mat3_to_quat_rv(local[:3, :3])],
            }
        anim_world = pose.world.get(bname)
        if anim_world is None:
            continue
        cache['world_positions'][bname] = [float(x) for x in anim_world[3, :3]]
        delta = np.linalg.inv(bone['world']) @ anim_world
        if not np.allclose(delta, np.eye(4), atol=1e-4):
            cache['delta_matrices'][bname] = [float(x) for x in delta.flatten()]
    return cache


def _lr_pairs(skeleton_bones) -> list:
    """Every (left bone, right bone) pair, by name."""
    pairs, seen = [], set()
    for name in sorted(skeleton_bones.keys()):
        rname = name.replace(' L ', ' R ')
        if ' L ' in name and rname in skeleton_bones and name not in seen:
            pairs.append((name, rname))
            seen.update((name, rname))
    return pairs


def _cached_delta(cache, bone):
    """The bone's cached delta as 4x4, identity when it has none."""
    return np.array(cache['delta_matrices'].get(bone, list(np.eye(4).flatten())),
                    dtype=np.float64).reshape(4, 4)


def _copy_side(cache, dst, src, rest_dst, delta_src, target, dist) -> bool:
    """Mirror `src`'s delta onto `dst` when that brings `dst` nearer its target."""
    delta_new = _M4 @ delta_src @ _M4
    new_world = rest_dst @ delta_new
    if not np.linalg.norm(new_world[3, :3] - target) < dist:
        return False
    cache['delta_matrices'][dst] = [float(x) for x in delta_new.flatten()]
    cache['world_positions'][dst] = [float(x) for x in new_world[3, :3]]
    cache['delta_matrices'][src] = [float(x) for x in delta_src.flatten()]
    return True


def _average_pair(cache, lbone, rbone, rests, deltas) -> None:
    """Make a pair symmetric by averaging its left delta with the mirrored right."""
    avg_l = 0.5 * (deltas[0] + _M4 @ deltas[1] @ _M4)
    U, _, Vt = np.linalg.svd(avg_l[:3, :3])
    avg_l[:3, :3] = U @ Vt
    avg_r = _M4 @ avg_l @ _M4
    cache['delta_matrices'][lbone] = [float(x) for x in avg_l.flatten()]
    cache['delta_matrices'][rbone] = [float(x) for x in avg_r.flatten()]
    cache['world_positions'][lbone] = [float(x) for x in (rests[0] @ avg_l)[3, :3]]
    cache['world_positions'][rbone] = [float(x) for x in (rests[1] @ avg_r)[3, :3]]


def _mirror_pair(cache, lbone, rbone, bones, targets) -> bool:
    """Carry the nearer side onto the other, or average them; whether the pair changed."""
    rest_l, rest_r = bones[lbone]['world'], bones[rbone]['world']
    delta_l, delta_r = _cached_delta(cache, lbone), _cached_delta(cache, rbone)
    l_dist = np.linalg.norm((rest_l @ delta_l)[3, :3] - targets[0])
    r_dist = np.linalg.norm((rest_r @ delta_r)[3, :3] - targets[1])
    if r_dist < l_dist - _MIRROR_THRESHOLD:
        return _copy_side(cache, lbone, rbone, rest_l, delta_r, targets[0], l_dist)
    if l_dist < r_dist - _MIRROR_THRESHOLD:
        return _copy_side(cache, rbone, lbone, rest_r, delta_l, targets[1], r_dist)
    _average_pair(cache, lbone, rbone, (rest_l, rest_r), (delta_l, delta_r))
    return True


def _mirror(cache, skeleton_bones, mapped_bones, sk_positions) -> int:
    """L/R-symmetrise the cached deltas; how many pairs changed."""
    count = 0
    for lbone, rbone in _lr_pairs(skeleton_bones):
        if lbone not in mapped_bones or rbone not in mapped_bones:
            continue
        lsk, rsk = mapped_bones[lbone], mapped_bones[rbone]
        if lsk in sk_positions and rsk in sk_positions:
            count += _mirror_pair(cache, lbone, rbone, skeleton_bones,
                                  (sk_positions[lsk], sk_positions[rsk]))
    return count


def _cache_rmsd(cache, mapped_bones, sk_positions) -> float:
    """RMS distance of the cached world positions from their Skyrim targets."""
    total, n = 0, 0
    for ob_name, sk_name in mapped_bones.items():
        pos_list = cache['world_positions'].get(ob_name)
        if pos_list is not None:
            total += np.sum((np.array(pos_list) - sk_positions[sk_name]) ** 2)
            n += 1
    return math.sqrt(total / n) if n > 0 else 0


def build_cache(args):
    """Build the animation pose cache by softmax-weighted corpus blending.

    Each limb chain blends every coherent corpus frame by its target error,
    SVD-projected to a proper rotation, then multi-start L-BFGS-B refines it
    and the left/right deltas are symmetrised.
    See: docs/commentary/asset_convert_armor.md#nif-skin-retargeting
    """
    skel_path, kf_files = _source_files()
    print("Loading OB skeleton...")
    skeleton_bones = _source_hierarchy(skel_path)
    print("Loading target positions...")
    _ob_positions, sk_positions = get_skyrim_targets_in_ob_space()
    mapped_bones = {ob: sk for ob, sk in bone_map_for(skeleton_bones).items()
                    if ob in skeleton_bones and sk in sk_positions}
    all_parsed = _parse_corpus(kf_files)
    _print_candidates(all_parsed, skeleton_bones)
    pose = _Pose(skeleton_bones)
    print(f"\nChain-level softmax blend over entire corpus (T={_TEMPERATURE})")
    corpus = {name: _softmax_chain(name, all_parsed, pose, mapped_bones, sk_positions)
              for name in _CHAIN_ORDER}
    print(f"\n  Multi-start L-BFGS-B refinement (K={_MULTI_START_K}, theta_max={_THETA_MAX:.2f} rad)")
    for name in _CHAIN_ORDER:
        _refine_chain(name, corpus[name], pose, mapped_bones, sk_positions)
    pre_mirror = _pose_rmsd(pose, mapped_bones, sk_positions)
    print(f"\n  Pre-mirror RMSD: {pre_mirror:.4f}")
    cache = _cache_dict(pose, pre_mirror)
    print(f"  Mirrored {_mirror(cache, skeleton_bones, mapped_bones, sk_positions)} L/R pairs")
    rmsd = _cache_rmsd(cache, mapped_bones, sk_positions)
    cache['rmsd'] = float(rmsd)
    cache['info'] = f'Approach A softmax T={_TEMPERATURE}, RMSD={rmsd:.4f}'
    print(f"  Post-mirror RMSD:  {rmsd:.4f}")
    out_path = _pose_out_path()
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(cache, f, indent=2)
    print(f"\nSaved {len(cache['bone_transforms'])} optimized bone transforms to {out_path}")




if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Explore Oblivion .kf animation data')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--skeleton', action='store_true', help='Dump OB skeleton hierarchy')
    group.add_argument('--scan-kf', action='store_true', help='Scan .kf files for animation data')
    group.add_argument('--find-pose', action='store_true', help='Find best animation pose matching Skyrim')
    group.add_argument('--build-cache', action='store_true', help='Build animation pose cache')
    
    parser.add_argument('--plugin', default='Oblivion.esm',
                        help='Source plugin whose skeleton and .kf clips to read')
    parser.add_argument('--morrowind', action='store_true',
                        help='Read the vanilla Morrowind rig (base_anim + xbase_anim.kf)')
    parser.add_argument('--female', action='store_true',
                        help='With --morrowind: the female rig, fitted to the female Skyrim skeleton')

    args = parser.parse_args()
    PLUGIN[0] = args.plugin
    SOURCE.update(morrowind=args.morrowind, female=args.female)
    
    if args.skeleton:
        dump_skeleton(args)
    elif args.scan_kf:
        scan_kf(args)
    elif args.find_pose:
        find_pose(args)
    elif args.build_cache:
        build_cache(args)
