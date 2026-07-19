#!/usr/bin/env python3
"""Survey BSInvMarker conventions against vanilla Skyrim meshes.

The NIF format docs don't specify the Euler order, angle sign, or camera axis
Skyrim uses when applying BSInvMarker rotations in the inventory 3D view.
This tool derives the convention empirically: for every vanilla mesh with a
BSInvMarker it computes the mesh's per-direction visible surface area (the
"broad side"), applies the marker rotation under each candidate convention,
and scores how much of the mesh's best side ends up facing the camera.
The convention artists actually used should score highest on average.

Usage:
    python tools/inv_marker_survey.py "<vanilla meshes dir>" [--max N] [--workers N]
    python tools/inv_marker_survey.py "<dir>" --detail <convention-id>

Output: a ranked table of conventions with mean/median alignment scores, then
per-mesh details for the winning convention.
"""

import argparse
import itertools
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import cpu_count

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
from pyffi.formats.nif import NifFormat

from asset_convert.inv_marker import _gather_area_normals

AXES = {'+X': np.array([1.0, 0, 0]), '-X': np.array([-1.0, 0, 0]),
        '+Y': np.array([0, 1.0, 0]), '-Y': np.array([0, -1.0, 0]),
        '+Z': np.array([0, 0, 1.0]), '-Z': np.array([0, 0, -1.0])}

EULER_ORDERS = [''.join(p) for p in itertools.permutations('XYZ')]


def _rot_axis(axis, a):
    c, s = np.cos(a), np.sin(a)
    if axis == 'X':
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == 'Y':
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def euler_matrix(order, ax, ay, az):
    """Compose R = R_o0(a_o0) @ R_o1(a_o1) @ R_o2(a_o2) for order string like 'XYZ'."""
    angles = {'X': ax, 'Y': ay, 'Z': az}
    m = np.eye(3)
    for axis in order:
        m = m @ _rot_axis(axis, angles[axis])
    return m


def gather_geometry(data):
    """Return (tri_normals Nx3, tri_areas N) in root space for all visible tris."""
    root = data.roots[0] if data.roots else None
    if root is None:
        return None
    geo = _gather_area_normals(root)
    if geo is None:
        return None
    tri_n, tri_a, _tri_c = geo
    return tri_n, tri_a


def visible_area(tri_n, tri_a, view_dir):
    """Projected front-facing area seen by a camera in direction view_dir
    (unit vector pointing FROM the object TOWARD the camera)."""
    d = np.asarray(view_dir, dtype=float)
    dots = tri_n @ d
    return float(np.sum(tri_a * np.clip(dots, 0.0, None)))


def analyze_file(path):
    """Load one nif; return (relpath, rot_mrad, tri_n, tri_a) or None."""
    try:
        data = NifFormat.Data()
        with open(path, 'rb') as f:
            data.read(f)
    except Exception:
        return None
    root = data.roots[0] if data.roots else None
    if root is None or not hasattr(root, 'extra_data_list'):
        return None
    marker = None
    for ed in root.extra_data_list:
        if type(ed).__name__ == 'BSInvMarker':
            marker = ed
            break
    if marker is None:
        return None
    geo = gather_geometry(data)
    if geo is None:
        return None
    tri_n, tri_a = geo
    if tri_a.sum() < 1e-6:
        return None
    return (path, (marker.rotation_x, marker.rotation_y, marker.rotation_z),
            tri_n, tri_a)


def convention_id(order, sign, cam):
    return f"{order}{'+' if sign > 0 else '-'}{cam}"


def score_convention(meshes, order, sign, cam_axis):
    """For each mesh: rotate its normals by the marker rotation under this
    convention, measure visible area toward the camera vs the best possible
    single-axis view.  Returns per-mesh scores in [0..1]."""
    cam = AXES[cam_axis]
    scores = []
    for _path, (rx, ry, rz), tri_n, tri_a in meshes:
        ax, ay, az = (sign * rx / 1000.0, sign * ry / 1000.0, sign * rz / 1000.0)
        r = euler_matrix(order, ax, ay, az)
        rn = tri_n @ r.T
        a_cam = visible_area(rn, tri_a, cam)
        best = max(visible_area(rn, tri_a, d) for d in AXES.values())
        scores.append(a_cam / best if best > 0 else 0.0)
    return np.array(scores)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('meshdir')
    ap.add_argument('--max', type=int, default=400, help='max meshes to survey')
    ap.add_argument('--workers', type=int, default=max(1, cpu_count() - 1))
    ap.add_argument('--detail', default=None,
                    help='convention id (e.g. XYZ-+Y) to print per-mesh detail for')
    ap.add_argument('--filter', default=None,
                    help='only survey nifs whose path contains this substring')
    ap.add_argument('--multiaxis', action='store_true',
                    help='only score meshes whose marker has >=2 angles not '
                         'near a multiple of pi (discriminates Euler orders)')
    args = ap.parse_args()

    candidates = []
    for dirpath, _dirs, files in os.walk(args.meshdir):
        for fn in files:
            if not fn.lower().endswith('.nif'):
                continue
            if '1stperson' in fn.lower():
                continue
            p = os.path.join(dirpath, fn)
            if args.filter and args.filter.lower() not in p.lower():
                continue
            candidates.append(p)
    candidates.sort()

    meshes = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for res in ex.map(analyze_file, candidates, chunksize=8):
            if res is not None:
                meshes.append(res)
                if len(meshes) >= args.max:
                    break
    if args.multiaxis:
        def _nontrivial(mrad):
            a = (mrad / 1000.0) % np.pi
            return min(a, np.pi - a) > 0.15
        meshes = [m for m in meshes
                  if sum(_nontrivial(v) for v in m[1]) >= 2]
    print(f"surveyed {len(meshes)} meshes with BSInvMarker "
          f"(of {len(candidates)} nifs)")
    if not meshes:
        return

    rows = []
    for order in EULER_ORDERS:
        for sign in (1, -1):
            for cam_axis in AXES:
                s = score_convention(meshes, order, sign, cam_axis)
                rows.append((convention_id(order, sign, cam_axis),
                             float(s.mean()), float(np.median(s)),
                             float((s > 0.9).mean())))
    rows.sort(key=lambda r: -r[1])
    print(f"\n{'convention':<12} {'mean':>6} {'median':>7} {'frac>0.9':>9}")
    for cid, mean, med, frac in rows[:15]:
        print(f"{cid:<12} {mean:6.3f} {med:7.3f} {frac:9.2f}")

    detail_id = args.detail or rows[0][0]
    for order in EULER_ORDERS:
        for sign in (1, -1):
            for cam_axis in AXES:
                if convention_id(order, sign, cam_axis) == detail_id:
                    s = score_convention(meshes, order, sign, cam_axis)
                    print(f"\nper-mesh scores for {detail_id}:")
                    idx = np.argsort(s)
                    for i in idx:
                        path, rot, _n, _a = meshes[i]
                        rel = os.path.relpath(path, args.meshdir)
                        print(f"  {s[i]:5.3f} rot={rot} {rel}")
                    return


if __name__ == '__main__':
    main()
