#!/usr/bin/env python3
"""Validate a creature ragdoll conversion at bind pose.

For each Oblivion skeleton.nif, runs the SAME extraction the skeleton.hkx
generator uses (asset_convert.hkx_ragdoll.extract_ragdoll) and then checks,
with every ragdoll body placed at its bind-pose bone world transform:

  pivot     child-frame and parent-frame constraint pivots must coincide in
            world space (mismatch = joint tears on death)
  frames    constraint basis orthonormality / handedness
  cone      angle(twistA, twistB) must be inside [0, coneMaxAngle]
  plane     asin(twistA . planeB) must be inside [planeMin, planeMax]
  twist     rotation of planeA vs planeB about the twist axis must be inside
            [twistMin, twistMax]
  hinge     axleA/axleB world alignment + bind angle (perp1A vs perp1B about
            the axle) inside [minAngle, maxAngle]

A bind pose that VIOLATES a limit is the classic mangled-ragdoll signature:
the solver yanks the limb to the nearest limit the instant the ragdoll
activates.  Also reports per-body frame info: rigid body class (bhkRigidBodyT
vs plain bhkRigidBody — plain bodies' rotation/translation are IGNORED by
Oblivion's engine and may hold garbage) and the bone-from-body delta the
extractor computed from those fields.

Usage:
    python tools/ragdoll_validate.py <skeleton.nif> [...]
    python tools/ragdoll_validate.py --creatures-dir export/Oblivion.esm/meshes/creatures dog deer bear
    python tools/ragdoll_validate.py <skeleton.nif> -v      # per-joint detail even when OK
"""

import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from asset_convert import pyffi_monkey_patch  # noqa: F401
from asset_convert.hkx_ragdoll import (_bone_worlds, _quat_to_mat_row, _v4,
                                       _OB_TO_GAME, extract_ragdoll,
                                       plan_ragdoll_tree)
from asset_convert.hkx_skeleton import load_skeleton_bones
from pyffi.formats.nif import NifFormat

DEG = 180.0 / math.pi


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n else v


def _angle(a, b):
    return math.acos(max(-1.0, min(1.0, float(np.dot(_unit(a), _unit(b))))))


def _twist_angle(axis, ref_a, ref_b):
    """Signed rotation of ref_a relative to ref_b about axis (world vecs)."""
    axis = _unit(axis)
    pa = _unit(ref_a - axis * float(np.dot(ref_a, axis)))
    pb = _unit(ref_b - axis * float(np.dot(ref_b, axis)))
    s = float(np.dot(np.cross(pb, pa), axis))
    c = float(np.dot(pa, pb))
    return math.atan2(s, c)


def _body_frame_report(nif_path):
    """Per body-carrying node: (name, class, |R_delta-I| fro, |t_delta|)."""
    data = NifFormat.Data()
    with open(nif_path, 'rb') as f:
        data.read(f)
    plan = plan_ragdoll_tree(data)
    if plan is None:
        return []
    out = []
    for n in plan['body_nodes']:
        body = n.collision_object.body
        q = body.rotation
        R_bw = _quat_to_mat_row((q.x, q.y, q.z, q.w))
        t_bw = _v4(body.translation, _OB_TO_GAME)
        R_bone, t_bone = plan['worlds'][id(n)]
        R_delta = R_bw @ R_bone.T
        t_delta = (t_bw - t_bone) @ R_bone.T
        name = bytes(n.name).decode('latin-1').rstrip('\x00')
        out.append((name, body.__class__.__name__,
                    float(np.linalg.norm(R_delta - np.eye(3))),
                    float(np.linalg.norm(t_delta))))
    return out


def validate(nif_path, verbose=False):
    print('=' * 78)
    print('SKELETON:', nif_path)
    bones = load_skeleton_bones(nif_path)
    parts = extract_ragdoll(nif_path, bones)
    if not parts:
        print('  (no usable ragdoll)')
        return 0

    frames = _body_frame_report(nif_path)
    n_bad_frame = 0
    for name, cls, rdev, tdev in frames:
        flag = ''
        if rdev > 1e-3 or tdev > 0.5:
            n_bad_frame += 1
            flag = '   <-- body frame != bone frame'
        if verbose or flag:
            print(f'  body {name:28s} {cls:16s} |R_delta-I|={rdev:.5f} '
                  f'|t_delta|={tdev:8.3f}{flag}')
    print(f'  {len(parts)} parts; {n_bad_frame} bodies with non-identity '
          f'bone-from-body delta')

    worlds = _bone_worlds(bones)
    issues = 0
    for p in parts:
        if p.constraint is None or p.parent < 0:
            continue
        kind, info = p.constraint
        R_cw, t_cw = worlds[p.anim_index]
        R_pw, t_pw = worlds[parts[p.parent].anim_index]

        piv_a_w = np.asarray(info['piv_a']) @ R_cw + t_cw
        piv_b_w = np.asarray(info['piv_b']) @ R_pw + t_pw
        piv_err = float(np.linalg.norm(piv_a_w - piv_b_w))

        rows_a = [np.asarray(r, dtype=float) for r in info['rows_a']]
        rows_b = [np.asarray(r, dtype=float) for r in info['rows_b']]
        wa = [r @ R_cw for r in rows_a]
        wb = [r @ R_pw for r in rows_b]

        probs = []
        if piv_err > 0.1:
            probs.append(f'PIVOT MISMATCH {piv_err:.3f} game units')
        for tag, rows in (('A', rows_a), ('B', rows_b)):
            if abs(float(np.dot(rows[0], rows[1]))) > 1e-4 or \
               float(np.linalg.norm(np.cross(rows[0], rows[1]) - rows[2])) > 1e-3:
                probs.append(f'frame {tag} not orthonormal/right-handed')

        if kind == 'ragdoll':
            cone = _angle(wa[0], wb[0])
            plane = math.asin(max(-1.0, min(1.0, float(np.dot(_unit(wa[0]),
                                                              _unit(wb[1]))))))
            twist = _twist_angle(wa[0] + wb[0], wa[1], wb[1])
            desc = (f'cone {cone * DEG:7.2f} (max {info["cone"] * DEG:7.2f})  '
                    f'plane {plane * DEG:7.2f} '
                    f'[{info["plane_min"] * DEG:7.2f},{info["plane_max"] * DEG:7.2f}]  '
                    f'twist {twist * DEG:7.2f} '
                    f'[{info["twist_min"] * DEG:7.2f},{info["twist_max"] * DEG:7.2f}]')
            margin = 0.5 / DEG
            if cone > info['cone'] + margin:
                probs.append(f'BIND CONE {cone * DEG:.2f} > limit '
                             f'{info["cone"] * DEG:.2f}')
            if not (info['plane_min'] - margin <= plane
                    <= info['plane_max'] + margin):
                probs.append(f'BIND PLANE {plane * DEG:.2f} outside '
                             f'[{info["plane_min"] * DEG:.2f},'
                             f'{info["plane_max"] * DEG:.2f}]')
            if not (info['twist_min'] - margin <= twist
                    <= info['twist_max'] + margin):
                probs.append(f'BIND TWIST {twist * DEG:.2f} outside '
                             f'[{info["twist_min"] * DEG:.2f},'
                             f'{info["twist_max"] * DEG:.2f}]')
        else:
            axle_mis = _angle(wa[0], wb[0])
            ang = _twist_angle(wa[0] + wb[0], wa[1], wb[1])
            desc = (f'axle-misalign {axle_mis * DEG:6.2f}  '
                    f'angle {ang * DEG:7.2f} '
                    f'[{info["min"] * DEG:7.2f},{info["max"] * DEG:7.2f}]')
            margin = 0.5 / DEG
            if axle_mis > 2.0 / DEG:
                probs.append(f'HINGE AXLES misaligned {axle_mis * DEG:.2f} deg')
            if not (info['min'] - margin <= ang <= info['max'] + margin):
                probs.append(f'BIND ANGLE {ang * DEG:.2f} outside '
                             f'[{info["min"] * DEG:.2f},{info["max"] * DEG:.2f}]')

        if probs or verbose:
            print(f'  {kind:7s} {p.name:32s} piv_err={piv_err:8.4f}  {desc}')
        for pr in probs:
            issues += 1
            print(f'      *** {pr}')

    print(f'  => {issues} bind-pose violations')
    return issues


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('paths', nargs='+',
                    help='skeleton.nif files, or creature names with '
                         '--creatures-dir')
    ap.add_argument('--creatures-dir',
                    help='base dir; paths become <dir>/<name>/skeleton.nif')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()

    files = ([os.path.join(args.creatures_dir, p, 'skeleton.nif')
              for p in args.paths] if args.creatures_dir else args.paths)
    total = 0
    for f in files:
        try:
            total += validate(f, verbose=args.verbose)
        except Exception as e:
            print(f'ERROR {f}: {type(e).__name__}: {e}')
    print('=' * 78)
    print(f'TOTAL violations: {total}')
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())
