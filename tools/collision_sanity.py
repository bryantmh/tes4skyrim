"""Collision sanity checker/dumper for NIF files (Oblivion or Skyrim format).

Walks every bhk* block, recursively checks all float fields for NaN/Inf,
flags degenerate shapes, and can dump constraint descriptors in detail
(pivots, axes, limits) for source-vs-converted comparison.

Usage:
    python tools/collision_sanity.py <nif_or_dir> [--constraints] [--workers N]
    python tools/collision_sanity.py output/.../upperscales01.nif --constraints

Checks:
    - NaN/Inf in any float field of any bhk block (incl. nested structs/arrays)
    - bhkConvexVerticesShape with < 4 vertices or 0 planes
    - bhkListShape with 0 children
    - hinge min_angle > max_angle
    - constraint axis vectors with |v| far from 1
"""
import argparse
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

from pyffi.formats.nif import NifFormat


def _iter_floats(obj, path, out, depth=0, seen=None):
    """Recursively yield (path, value) for float-ish attributes of PyFFI structs."""
    if depth > 6:
        return
    if seen is None:
        seen = set()
    if id(obj) in seen:
        return
    seen.add(id(obj))
    if isinstance(obj, float):
        out.append((path, obj))
        return
    if isinstance(obj, (int, str, bytes, bool)) or obj is None:
        return
    # PyFFI struct: iterate declared attributes
    attrs = getattr(obj, '_attrs', None)
    if attrs is not None:
        for a in attrs:
            name = a.name
            try:
                v = getattr(obj, name)
            except Exception:
                continue
            _iter_floats(v, f"{path}.{name}", out, depth + 1, seen)
        return
    # Arrays
    try:
        it = list(obj)
    except TypeError:
        return
    for i, v in enumerate(it):
        _iter_floats(v, f"{path}[{i}]", out, depth + 1, seen)


def _vec_len(v):
    return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)


def _fmt4(v):
    return f"({v.x:.4f}, {v.y:.4f}, {v.z:.4f}, {v.w:.4f})"


def check_file(path, dump_constraints=False):
    lines = []
    issues = []
    try:
        data = NifFormat.Data()
        with open(path, 'rb') as f:
            data.read(f)
    except Exception as e:
        return [f"{path}: PARSE FAILED: {e}"], 1

    for block in data.blocks:
        tname = type(block).__name__
        if not tname.startswith('bhk'):
            continue

        # NaN/Inf sweep over the whole block
        floats = []
        _iter_floats(block, tname, floats)
        for fpath, val in floats:
            if math.isnan(val) or math.isinf(val):
                issues.append(f"NON-FINITE {fpath} = {val}")

        if isinstance(block, NifFormat.bhkConvexVerticesShape):
            nv = block.num_vertices
            npl = block.num_normals
            if nv < 4:
                issues.append(f"{tname}: degenerate hull verts={nv}")
            if npl == 0:
                issues.append(f"{tname}: hull with 0 planes")

        if isinstance(block, NifFormat.bhkListShape):
            if block.num_sub_shapes == 0:
                issues.append(f"{tname}: 0 children")

        # Constraint detail
        desc = None
        dname = None
        for attr in ('ragdoll', 'limited_hinge', 'hinge', 'malleable'):
            d = getattr(block, attr, None)
            if d is not None:
                desc, dname = d, attr
                break
        if desc is None:
            continue

        if dump_constraints:
            lines.append(f"  {tname} ({dname}):")
            for a in desc._attrs:
                try:
                    v = getattr(desc, a.name)
                except Exception:
                    continue
                if hasattr(v, 'w') and hasattr(v, 'x'):
                    lines.append(f"    {a.name:24} {_fmt4(v)}  |xyz|={_vec_len(v):.4f}")
                elif isinstance(v, float):
                    lines.append(f"    {a.name:24} {v:.6f}")
                elif isinstance(v, int):
                    lines.append(f"    {a.name:24} {v}")

        # Range checks
        mn = getattr(desc, 'min_angle', None)
        mx = getattr(desc, 'max_angle', None)
        if mn is not None and mx is not None and mn > mx:
            issues.append(f"{tname}.{dname}: min_angle {mn} > max_angle {mx}")
        for a in desc._attrs:
            n = a.name
            if ('axle' in n or 'axis' in n or 'twist' in n or 'plane' in n or 'motor' in n) and not n.startswith('pivot'):
                try:
                    v = getattr(desc, n)
                except Exception:
                    continue
                if hasattr(v, 'x') and hasattr(v, 'z'):
                    L = _vec_len(v)
                    if L > 1e-6 and abs(L - 1.0) > 0.05:
                        issues.append(f"{tname}.{dname}.{n}: |v|={L:.4f} not unit")
                    if L <= 1e-6:
                        issues.append(f"{tname}.{dname}.{n}: zero vector")

    header = f"{path}"
    out = [header]
    if issues:
        out += [f"  ISSUE: {i}" for i in issues]
    out += lines
    return out, len(issues)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("--constraints", action="store_true", help="dump constraint descriptors in detail")
    ap.add_argument("--quiet", action="store_true", help="only print files with issues")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = ap.parse_args()

    if os.path.isfile(args.root) and args.root.lower().endswith('.txt'):
        files = [l.strip() for l in open(args.root) if l.strip()]
    elif os.path.isfile(args.root):
        files = [args.root]
    else:
        files = [os.path.join(dp, f) for dp, _, fs in os.walk(args.root)
                 for f in fs if f.lower().endswith('.nif')]

    total_issues = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for out, nissues in ex.map(lambda p: check_file(p, args.constraints), files):
            total_issues += nissues
            if nissues or not args.quiet or args.constraints:
                print("\n".join(out))
    print(f"\n{len(files)} files, {total_issues} issues", file=sys.stderr)


if __name__ == "__main__":
    main()
