"""Explore Oblivion .kf animation data and skeleton for FK-based retargeting.

Usage:
    python tools/kf_animation_explorer.py --skeleton   # Dump OB skeleton bone hierarchy
    python tools/kf_animation_explorer.py --scan-kf    # Scan all .kf files, extract per-bone transforms per frame
    python tools/kf_animation_explorer.py --find-pose   # Find best animation pose matching Skyrim targets
    python tools/kf_animation_explorer.py --build-cache # Build animation pose cache for use in retarget pipeline
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from asset_convert.skyrim_overrides import OBLIVION_TO_SKYRIM_BONE_MAP


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
    sk_json = PROJECT_ROOT / 'asset_convert' / 'generated' / 'skeleton_bones_skyrim_male.json'
    ob_json = PROJECT_ROOT / 'asset_convert' / 'generated' / 'skeleton_bones_oblivion.json'
    
    if not sk_json.exists() or not ob_json.exists():
        # Try non-generated paths
        sk_json = PROJECT_ROOT / 'asset_convert' / 'skeleton_bones_skyrim_male.json'
        ob_json = PROJECT_ROOT / 'asset_convert' / 'skeleton_bones_oblivion.json'
    
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
    for ob_name, sk_name in OBLIVION_TO_SKYRIM_BONE_MAP.items():
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
    skel_path = PROJECT_ROOT / 'export' / 'Oblivion.esm' / 'meshes' / 'characters' / '_male' / 'skeleton.nif'
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
    kf_dir = PROJECT_ROOT / 'export' / 'Oblivion.esm' / 'meshes' / 'characters' / '_male'
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
    skel_path = PROJECT_ROOT / 'export' / 'Oblivion.esm' / 'meshes' / 'characters' / '_male' / 'skeleton.nif'
    kf_dir = PROJECT_ROOT / 'export' / 'Oblivion.esm' / 'meshes' / 'characters' / '_male'
    
    print("Loading OB skeleton...")
    skeleton_bones = load_skeleton_hierarchy(skel_path)
    
    print("Loading target positions...")
    ob_positions, sk_positions = get_skyrim_targets_in_ob_space()
    
    # Print rest-pose distances for reference
    print("\nRest-pose (T-pose) distances OB->SK:")
    total = 0
    n = 0
    for ob_name, sk_name in sorted(OBLIVION_TO_SKYRIM_BONE_MAP.items()):
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
# BRAINSTORM: Better use of the animation corpus in the cache builder
# ---------------------------------------------------------------------------
#
# Current approach: greedy per-chain argmin — for each chain (arm/leg), scan
# every keyframe in every .kf, evaluate the chain's total bone-position error,
# keep the frame with the lowest cost. This gives one delta matrix per bone.
#
# Why this leaves 10% on the table:
#   1. Greedy chain-by-chain: spine is never optimised, so all downstream
#      bones (arm, shoulder) start from a sub-optimal root world transform.
#   2. Best-frame selection discards all pose variation — hundreds of frames
#      collapse to one. The optimal delta for a bone may not exist in any
#      single frame; it lives in the convex hull of all frames.
#   3. The delta is applied as LBS; see skin_retarget.py brainstorm for why
#      LBS causes joint collapse compared to DQS.
#
# Better approaches to implement here:
#
# APPROACH A — Least-squares optimal delta (replaces argmin frame search)
#   For each bone b, collect all N frames: pairs (R_ob_i, t_ob_i) and target
#   (R_sk, t_sk) in world space. The delta D = inv(world_ob_rest) @ world_ob_anim
#   only changes per frame; the target is constant (SK rest pose).
#   Frame selection is equivalent to picking the D_i that minimises
#       || W_sk - W_ob_rest @ D_i ||_F
#   Rather than argmin over discrete frames, compute the least-squares D*
#   across ALL frames (or a weighted average favoring frames that have low
#   cost for ALL bones in the chain simultaneously). This gives a delta that
#   is not constrained to a single keyframe and may be significantly better.
#   Implementation: compute D_weighted = Σ alpha_i * D_i where alpha_i is
#   softmax(-cost_i / temperature). Recover the nearest rotation via SVD.
#
# APPROACH B — Full-body joint-angle optimisation (IK over the corpus)
#   Treat the Skyrim rest pose as a target configuration. Use the animation
#   corpus to define the feasible joint-angle space (convex hull or Gaussian
#   model per joint). Run L-BFGS-B minimising total bone-position error with
#   joint angles as the free variables, constrained to the feasible space.
#   The result is a physically plausible pose that gets as close as possible
#   to the Skyrim rest pose within Oblivion's skeletal degrees of freedom.
#   compute_fk_world_positions() is the forward pass; gradients can be
#   approximated by finite differences since the skeleton is small (~50 bones).
#
# APPROACH C — Per-bone transform distribution → confidence-weighted blend
#   Instead of a single best delta, store the mean and variance of all deltas
#   observed for each bone across the corpus. Bones with low variance (e.g.
#   foot — always near the same pose) get their mean delta used with high
#   confidence. Bones with high variance (e.g. arm — many different poses)
#   get their delta blended toward the target-minimising direction. This
#   naturally handles the fact that some bones are over-determined by
#   animation data while others are under-determined.
#
# APPROACH D — Body chain optimisation (currently excluded, see comment below)
#   The spine chain is excluded because it's asymmetric across L/R. This
#   causes the entire upper body to inherit a sub-optimal root transform.
#   Fix: include the spine in optimisation but use a bilateral symmetry
#   constraint — force L and R deltas to be mirror images of each other.
#   This is implementable as a joint optimisation over the spine + both arms
#   simultaneously with a symmetry regularisation term.

def _parse_kf_safe(kf_path):
    """Thread/process-safe wrapper around parse_kf_file."""
    try:
        return parse_kf_file(kf_path)
    except Exception:
        return {}


def build_cache(args):
    """Build animation pose cache using Approach A: softmax-weighted delta blending.

    For each bone (root-to-leaf), blends ALL animation corpus frames using
    softmax weights based on descendant position errors.  Uses analytical
    translation to place every mapped bone at its exact Skyrim target position.
    Rotation is blended from the corpus (weighted toward candidates that also
    position descendants well) then SVD-projected to nearest proper rotation.
    """
    skel_path = PROJECT_ROOT / 'export' / 'Oblivion.esm' / 'meshes' / 'characters' / '_male' / 'skeleton.nif'
    kf_dir = PROJECT_ROOT / 'export' / 'Oblivion.esm' / 'meshes' / 'characters' / '_male'

    print("Loading OB skeleton...")
    skeleton_bones = load_skeleton_hierarchy(skel_path)

    print("Loading target positions...")
    ob_positions, sk_positions = get_skyrim_targets_in_ob_space()

    # Build OB→SK name mapping
    mapped_bones = {}
    for ob_name, sk_name in OBLIVION_TO_SKYRIM_BONE_MAP.items():
        if ob_name in skeleton_bones and sk_name in sk_positions:
            mapped_bones[ob_name] = sk_name

    # ── Step 1: Parse ALL .kf files in parallel ─────────────────────────
    kf_files = sorted(Path(kf_dir).rglob('*.kf'))
    workers = max(1, (os.cpu_count() or 4) - 1)
    print(f"Parsing {len(kf_files)} .kf files ({workers} threads)...")
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        all_parsed = list(pool.map(_parse_kf_safe, kf_files))
    print(f"  Parsed in {time.perf_counter() - t0:.1f}s")

    # ── Step 2: Build per-bone local transform library ──────────────────
    print("Building per-bone transform library...")
    bone_locals_list: dict[str, list[np.ndarray]] = {}

    for bone_keyframes in all_parsed:
        for bname, kfs in bone_keyframes.items():
            if bname not in skeleton_bones:
                continue
            rest = skeleton_bones[bname]['local']
            if bname not in bone_locals_list:
                bone_locals_list[bname] = []
            for _t, (trans, rot) in kfs.items():
                local = rest.copy()
                if rot is not None:
                    local[:3, :3] = _quat_to_mat3(*rot)
                if trans is not None:
                    local[3, :3] = np.array(trans)
                bone_locals_list[bname].append(local)

    # Convert to numpy arrays for vectorised operations
    bone_locals_np: dict[str, np.ndarray] = {}
    for bname, lst in bone_locals_list.items():
        bone_locals_np[bname] = np.array(lst)
    del bone_locals_list

    total_transforms = sum(a.shape[0] for a in bone_locals_np.values())
    print(f"  {len(bone_locals_np)} bones, {total_transforms} total candidates")

    # ── Helper: descendant relative transforms ──────────────────────────
    _desc_cache: dict[str, dict[str, np.ndarray]] = {}

    def _get_descendants_relative(bone_name):
        """Precompute rest-pose relative transforms for all descendants.
        desc_world = desc_relative @ bone_world for any bone_world."""
        if bone_name in _desc_cache:
            return _desc_cache[bone_name]
        result = {}

        def _walk(name, cumulative):
            for child in skeleton_bones[name]['children']:
                if child not in skeleton_bones:
                    continue
                child_rel = skeleton_bones[child]['local'] @ cumulative
                result[child] = child_rel
                _walk(child, child_rel)
        _walk(bone_name, np.eye(4))
        _desc_cache[bone_name] = result
        return result

    # ── Initialise with rest pose ───────────────────────────────────────
    chosen_local  = {n: b['local'].copy() for n, b in skeleton_bones.items()}
    chosen_world  = {n: b['world'].copy() for n, b in skeleton_bones.items()}

    def _recompute_subtree(n):
        for c in skeleton_bones[n]['children']:
            if c in chosen_local:
                chosen_world[c] = chosen_local[c] @ chosen_world[n]
                _recompute_subtree(c)

    # ── Step 3: Per-bone Approach A optimisation ────────────────────────
    CHAINS = {
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

    TEMPERATURE = 1.0  # Softmax sharpness for chain-level blending
    chain_order_list = ['left_arm', 'right_arm', 'left_leg', 'right_leg']

    # Storage for corpus frame data (needed by multi-start refinement)
    chain_corpus_data = {}  # chain_name -> (costs_arr, all_per_bone_locals)

    print(f"\nChain-level softmax blend over entire corpus (T={TEMPERATURE})")

    for chain_name in chain_order_list:
        chain_bones = CHAINS[chain_name]
        chain_mapped = [(b, mapped_bones[b]) for b in chain_bones if b in mapped_bones]

        if not chain_mapped:
            continue

        print(f"\n  Chain: {chain_name}")

        # Collect ALL coherent chain poses and their costs ────────────
        # Each "pose" is per-bone locals from the SAME animation frame,
        # preserving FK consistency. Different frames from different .kf
        # files are separate poses.
        all_costs = []          # list of float
        all_per_bone_locals = []  # list of dict[bone_name -> 4x4]

        for bone_keyframes in all_parsed:
            if not bone_keyframes:
                continue

            # Get all unique timestamps across all bones
            all_times = set()
            for bkf in bone_keyframes.values():
                all_times.update(bkf.keys())

            for t in all_times:
                # Build per-bone locals for this frame (from same animation)
                frame_locals = {}
                for bname in chain_bones:
                    if bname not in skeleton_bones:
                        continue
                    local = skeleton_bones[bname]['local'].copy()
                    if bname in bone_keyframes:
                        kfs = bone_keyframes[bname]
                        times_list = sorted(kfs.keys())
                        closest_t = min(times_list, key=lambda x: abs(x - t))
                        if abs(closest_t - t) < 0.01:
                            trans, rot = kfs[closest_t]
                            if rot is not None:
                                local[:3, :3] = _quat_to_mat3(*rot)
                            if trans is not None:
                                local[3, :3] = np.array(trans)
                    frame_locals[bname] = local

                # Compute FK world transforms for chain, using the current
                # chosen_world for the chain root's parent
                frame_worlds = {}
                for bname in chain_bones:
                    if bname not in skeleton_bones:
                        continue
                    parent = skeleton_bones[bname]['parent']
                    pw = frame_worlds.get(parent, chosen_world.get(parent, np.eye(4)))
                    frame_worlds[bname] = frame_locals.get(bname, skeleton_bones[bname]['local']) @ pw

                # Cost = sum of squared position errors for mapped chain bones
                cost = 0.0
                for ob_bone, sk_bone in chain_mapped:
                    if ob_bone in frame_worlds:
                        cost += np.sum((frame_worlds[ob_bone][3, :3] - sk_positions[sk_bone]) ** 2)

                all_costs.append(cost)
                all_per_bone_locals.append(frame_locals)

        N_frames = len(all_costs)
        if N_frames == 0:
            continue

        costs_arr = np.array(all_costs)
        best_cost = costs_arr.min()
        best_idx = int(np.argmin(costs_arr))

        # Save for multi-start refinement
        chain_corpus_data[chain_name] = (costs_arr, all_per_bone_locals)

        # Softmax blend — weight each coherent frame pose by its chain cost
        log_w = -costs_arr / TEMPERATURE
        log_w -= log_w.max()
        alpha = np.exp(log_w)
        alpha /= alpha.sum()

        # Effective number of frames (how many really contribute)
        eff_n = 1.0 / np.sum(alpha ** 2) if np.sum(alpha ** 2) > 0 else 1.0

        # Weighted blend of per-bone locals
        for bname in chain_bones:
            if bname not in skeleton_bones:
                continue
            blended = np.zeros((4, 4), dtype=np.float64)
            for i, frame_locs in enumerate(all_per_bone_locals):
                local = frame_locs.get(bname, skeleton_bones[bname]['local'])
                blended += alpha[i] * local

            # SVD → nearest proper rotation
            U, _S, Vt = np.linalg.svd(blended[:3, :3])
            R = U @ Vt
            if np.linalg.det(R) < 0:
                U[:, -1] *= -1
                R = U @ Vt
            blended[:3, :3] = R

            chosen_local[bname] = blended

        # Recompute FK for chain (root-to-leaf)
        for bname in chain_bones:
            if bname not in skeleton_bones:
                continue
            parent = skeleton_bones[bname]['parent']
            parent_world = chosen_world.get(parent, np.eye(4))
            chosen_world[bname] = chosen_local[bname] @ parent_world
            _recompute_subtree(bname)

        # Chain RMSD (before refinement)
        chain_cost_val = sum(np.sum((chosen_world[ob][3, :3] - sk_positions[sk]) ** 2)
                         for ob, sk in chain_mapped if ob in chosen_world)
        chain_rmsd = math.sqrt(chain_cost_val / len(chain_mapped))
        best_rmsd = math.sqrt(best_cost / len(chain_mapped))
        print(f"  {chain_name:12s}: corpus RMSD={chain_rmsd:.3f}  (best_frame={best_rmsd:.3f}, eff_N={eff_n:.1f})")

    # ── Step 3b: Multi-start L-BFGS-B refinement ─────────────────────────
    # For each chain, try L-BFGS-B from the top-K corpus frames and pick
    # the result with the lowest post-refinement cost.  This avoids local
    # minima inherent to a single starting point.
    from scipy.optimize import minimize as sp_minimize

    THETA_MAX = 0.35  # Max rotation per axis in radians (~20°)
    MULTI_START_K = 50  # Number of starting corpus frames to try

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

    print(f"\n  Multi-start L-BFGS-B refinement (K={MULTI_START_K}, θ_max={THETA_MAX:.2f} rad)")

    for chain_name in chain_order_list:
        chain_bones_list = CHAINS[chain_name]
        active_bones = [b for b in chain_bones_list if b in skeleton_bones]
        chain_mapped_refine = [(b, mapped_bones[b]) for b in active_bones if b in mapped_bones]
        if not chain_mapped_refine:
            continue

        n_params = len(active_bones) * 3
        bounds_list = [(-THETA_MAX, THETA_MAX)] * n_params

        corpus_data = chain_corpus_data.get(chain_name)
        if not corpus_data:
            continue
        c_costs, c_frame_locals = corpus_data

        sorted_indices = np.argsort(c_costs)[:MULTI_START_K]

        # Run all trials in parallel using threads (numpy releases GIL)
        def _run_trial(frame_idx):
            bl = {}
            for bname in active_bones:
                bl[bname] = c_frame_locals[frame_idx].get(
                    bname, skeleton_bones[bname]['local']).copy()

            def _cost(params):
                worlds = {}
                for i, bname in enumerate(active_bones):
                    loc = bl[bname].copy()
                    ax = params[i*3:(i+1)*3]
                    R_delta = _axis_angle_to_mat3(ax)
                    loc[:3, :3] = R_delta @ loc[:3, :3]
                    parent = skeleton_bones[bname]['parent']
                    pw = worlds.get(parent, chosen_world.get(parent, np.eye(4)))
                    worlds[bname] = loc @ pw
                c = 0.0
                for ob_bone, sk_bone in chain_mapped_refine:
                    if ob_bone in worlds:
                        c += np.sum((worlds[ob_bone][3, :3] - sk_positions[sk_bone]) ** 2)
                return c

            x0 = np.zeros(n_params)
            result = sp_minimize(_cost, x0, method='L-BFGS-B', bounds=bounds_list,
                                 options={'maxiter': 500, 'ftol': 1e-12, 'gtol': 1e-8})
            return result, bl

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(MULTI_START_K, os.cpu_count() or 4)) as pool:
            futures = [pool.submit(_run_trial, int(idx)) for idx in sorted_indices]
            best_overall_cost = float('inf')
            best_overall_result = None
            best_overall_base_locals = None
            best_start_idx = 0
            for trial, future in enumerate(concurrent.futures.as_completed(futures)):
                result, bl = future.result()
                if result.fun < best_overall_cost:
                    best_overall_cost = result.fun
                    best_overall_result = result
                    best_overall_base_locals = bl
                    best_start_idx = trial

        # Apply the best result
        pre_cost_single = c_costs[sorted_indices[0]]
        pre_rmsd = math.sqrt(pre_cost_single / len(chain_mapped_refine))
        post_rmsd = math.sqrt(best_overall_cost / len(chain_mapped_refine))

        if best_overall_cost < pre_cost_single:
            for i, bname in enumerate(active_bones):
                ax = best_overall_result.x[i*3:(i+1)*3]
                R_delta = _axis_angle_to_mat3(ax)
                chosen_local[bname] = best_overall_base_locals[bname].copy()
                chosen_local[bname][:3, :3] = R_delta @ chosen_local[bname][:3, :3]

            for bname in active_bones:
                parent = skeleton_bones[bname]['parent']
                parent_world = chosen_world.get(parent, np.eye(4))
                chosen_world[bname] = chosen_local[bname] @ parent_world
                _recompute_subtree(bname)

            max_angle = max(np.linalg.norm(best_overall_result.x[i*3:(i+1)*3])
                           for i in range(len(active_bones)))
            start_label = f"start#{best_start_idx}" if best_start_idx > 0 else "best_frame"
            print(f"  {chain_name:12s}: {pre_rmsd:.3f} → {post_rmsd:.3f}  "
                  f"(max_rot={math.degrees(max_angle):.1f}°, {start_label})")
        else:
            print(f"  {chain_name:12s}: {pre_rmsd:.3f} → no improvement")

    # Overall RMSD before mirroring
    total_sq = 0
    n = 0
    for ob_name, sk_name in mapped_bones.items():
        if ob_name in chosen_world:
            total_sq += np.sum((chosen_world[ob_name][3, :3] - sk_positions[sk_name]) ** 2)
            n += 1
    pre_mirror_rmsd = math.sqrt(total_sq / n) if n > 0 else 0
    print(f"\n  Pre-mirror RMSD: {pre_mirror_rmsd:.4f}")

    # ── Step 4: Build cache dict ────────────────────────────────────────
    cache = {
        'info': f'Approach A softmax T={TEMPERATURE}, RMSD={pre_mirror_rmsd:.4f}',
        'rmsd': float(pre_mirror_rmsd),
        'bone_transforms': {},
        'world_positions': {},
        'delta_matrices': {},
    }

    for bname in skeleton_bones:
        local = chosen_local[bname]
        rest_local = skeleton_bones[bname]['local']
        if not np.allclose(local, rest_local, atol=1e-4):
            R = local[:3, :3]
            q = _mat3_to_quat_rv(R)
            t = local[3, :3]
            cache['bone_transforms'][bname] = {
                'translation': [float(x) for x in t],
                'rotation': [float(x) for x in q],
            }
        if bname in chosen_world:
            cache['world_positions'][bname] = [float(x) for x in chosen_world[bname][3, :3]]

        rest_world = skeleton_bones[bname]['world']
        anim_world = chosen_world.get(bname)
        if anim_world is not None:
            delta = np.linalg.inv(rest_world) @ anim_world
            if not np.allclose(delta, np.eye(4), atol=1e-4):
                cache['delta_matrices'][bname] = [float(x) for x in delta.flatten()]

    # ── Step 5: L/R rotation-only mirroring ────────────────────────────
    # Mirror only the ROTATION part of deltas for L/R symmetry.
    # Average the translation too (mirrored), preserving the blended positions.
    M4 = np.diag([-1.0, 1.0, 1.0, 1.0])
    LR_PAIRS = []
    seen_lr = set()
    for name in sorted(skeleton_bones.keys()):
        if ' L ' in name:
            rname = name.replace(' L ', ' R ')
            if rname in skeleton_bones and name not in seen_lr:
                LR_PAIRS.append((name, rname))
                seen_lr.add(name)
                seen_lr.add(rname)

    mirror_count = 0
    for lbone, rbone in LR_PAIRS:
        if lbone not in mapped_bones or rbone not in mapped_bones:
            continue
        lsk, rsk = mapped_bones[lbone], mapped_bones[rbone]
        if lsk not in sk_positions or rsk not in sk_positions:
            continue

        rest_world_l = skeleton_bones[lbone]['world']
        rest_world_r = skeleton_bones[rbone]['world']

        delta_l = np.array(cache['delta_matrices'].get(lbone, list(np.eye(4).flatten())),
                           dtype=np.float64).reshape(4, 4)
        delta_r = np.array(cache['delta_matrices'].get(rbone, list(np.eye(4).flatten())),
                           dtype=np.float64).reshape(4, 4)

        l_world = rest_world_l @ delta_l
        r_world = rest_world_r @ delta_r
        l_dist = np.linalg.norm(l_world[3, :3] - sk_positions[lsk])
        r_dist = np.linalg.norm(r_world[3, :3] - sk_positions[rsk])

        mirror_threshold = 0.01
        if r_dist < l_dist - mirror_threshold:
            delta_l_new = M4 @ delta_r @ M4
            new_world = rest_world_l @ delta_l_new
            new_dist = np.linalg.norm(new_world[3, :3] - sk_positions[lsk])
            if new_dist < l_dist:
                cache['delta_matrices'][lbone] = [float(x) for x in delta_l_new.flatten()]
                cache['world_positions'][lbone] = [float(x) for x in new_world[3, :3]]
                cache['delta_matrices'][rbone] = [float(x) for x in delta_r.flatten()]
                mirror_count += 1
        elif l_dist < r_dist - mirror_threshold:
            delta_r_new = M4 @ delta_l @ M4
            new_world = rest_world_r @ delta_r_new
            new_dist = np.linalg.norm(new_world[3, :3] - sk_positions[rsk])
            if new_dist < r_dist:
                cache['delta_matrices'][rbone] = [float(x) for x in delta_r_new.flatten()]
                cache['world_positions'][rbone] = [float(x) for x in new_world[3, :3]]
                cache['delta_matrices'][lbone] = [float(x) for x in delta_l.flatten()]
                mirror_count += 1
        else:
            # Enforce symmetry by averaging the full delta
            delta_l_from_r = M4 @ delta_r @ M4
            avg_delta_l = 0.5 * (delta_l + delta_l_from_r)
            U, _, Vt = np.linalg.svd(avg_delta_l[:3, :3])
            avg_delta_l[:3, :3] = U @ Vt
            avg_delta_r = M4 @ avg_delta_l @ M4
            cache['delta_matrices'][lbone] = [float(x) for x in avg_delta_l.flatten()]
            cache['delta_matrices'][rbone] = [float(x) for x in avg_delta_r.flatten()]
            new_l = (rest_world_l @ avg_delta_l)[3, :3]
            new_r = (rest_world_r @ avg_delta_r)[3, :3]
            cache['world_positions'][lbone] = [float(x) for x in new_l]
            cache['world_positions'][rbone] = [float(x) for x in new_r]
            mirror_count += 1

    print(f"  Mirrored {mirror_count} L/R pairs")

    # ── Step 6: Recompute RMSD ─────────────────────────────────────────

    # Recompute RMSD with mirrored deltas
    total_sq_m = 0
    n_m = 0
    for ob_name, sk_name in mapped_bones.items():
        pos_list = cache['world_positions'].get(ob_name)
        if pos_list is not None:
            pos = np.array(pos_list)
            total_sq_m += np.sum((pos - sk_positions[sk_name]) ** 2)
            n_m += 1
    mirror_rmsd = math.sqrt(total_sq_m / n_m) if n_m > 0 else 0
    cache['rmsd'] = float(mirror_rmsd)
    cache['info'] = f'Approach A softmax T={TEMPERATURE}, RMSD={mirror_rmsd:.4f}'
    print(f"  Post-mirror RMSD:  {mirror_rmsd:.4f}")

    out_path = PROJECT_ROOT / 'asset_convert' / 'generated' / 'best_animation_pose.json'
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
    
    args = parser.parse_args()
    
    if args.skeleton:
        dump_skeleton(args)
    elif args.scan_kf:
        scan_kf(args)
    elif args.find_pose:
        find_pose(args)
    elif args.build_cache:
        build_cache(args)
