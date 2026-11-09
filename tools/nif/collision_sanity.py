"""Collision sanity checker/dumper for NIF files (Oblivion or Skyrim format).

Walks every bhk* block, recursively checks all float fields for NaN/Inf,
flags degenerate shapes, and can dump constraint descriptors in detail
(pivots, axes, limits) for source-vs-converted comparison.

Usage:
    python -m tools.nif.collision_sanity <nif_or_dir_or_listfile.txt> [--constraints] [--geometry] [--quiet]
    python -m tools.nif.collision_sanity output/.../upperscales01.nif --constraints

Checks:
    - NaN/Inf in any float field of any bhk block (incl. nested structs/arrays)
    - bhkConvexVerticesShape with < 4 vertices or 0 planes
    - bhkListShape with 0 children
    - compressed-mesh triangles Havok decodes as a line (float32 determinant <= 0)
    - hinge min_angle > max_angle
    - constraint axis vectors with |v| far from 1
"""
import argparse
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from functools import partial

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

from pyffi.formats.nif import NifFormat

from asset_convert.collision.cms import decode_cms
from asset_convert.collision.cms_builder import float32_collinear

#: Constraint descriptor attributes, in the order a block is searched for one.
_DESCRIPTORS = ('ragdoll', 'limited_hinge', 'hinge', 'malleable')

#: Name fragments of descriptor fields that must be unit vectors.
_AXIS_WORDS = ('axle', 'axis', 'twist', 'plane', 'motor')


def _iter_floats(obj, path, out, depth=0, seen=None):
    """Append (path, value) for every float reachable from a PyFFI struct or array."""
    if seen is None:
        seen = set()
    if depth > 6 or id(obj) in seen:
        return
    seen.add(id(obj))
    if isinstance(obj, float):
        out.append((path, obj))
        return
    if isinstance(obj, (int, str, bytes, bool)) or obj is None:
        return
    attrs = getattr(obj, '_attrs', None)
    if attrs is not None:
        for a in attrs:
            v = _attr(obj, a.name)
            if v is not None:
                _iter_floats(v, f"{path}.{a.name}", out, depth + 1, seen)
        return
    try:
        items = list(obj)
    except TypeError:
        return
    for i, v in enumerate(items):
        _iter_floats(v, f"{path}[{i}]", out, depth + 1, seen)


def _attr(obj, name):
    """`obj.name`, or None when PyFFI cannot produce it."""
    try:
        return getattr(obj, name)
    except Exception:
        return None


def _vec_len(v):
    """Length of a PyFFI vector's xyz."""
    return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)


def _fmt4(v):
    """A PyFFI 4-vector as text."""
    return f"({v.x:.4f}, {v.y:.4f}, {v.z:.4f}, {v.w:.4f})"


def _finite(*values):
    """Whether every value is finite."""
    return all(math.isfinite(x) for x in values)


def _check_geometry(block, issues):
    """NaN/Inf sweep over render geometry (NiTriShapeData etc.)."""
    tname = type(block).__name__
    verts = block.vertices if getattr(block, 'has_vertices', False) else []
    norms = block.normals if getattr(block, 'has_normals', False) else []
    bad_v = sum(not _finite(v.x, v.y, v.z) for v in verts)
    bad_n = sum(not _finite(v.x, v.y, v.z) for v in norms)
    bad_uv = sum(not _finite(uv.u, uv.v) for s in getattr(block, 'uv_sets', []) for uv in s)
    c, r = getattr(block, 'center', None), getattr(block, 'radius', None)
    if c is not None and not _finite(c.x, c.y, c.z):
        issues.append(f"{tname}: NON-FINITE bound-sphere center")
    if r is not None and not math.isfinite(r):
        issues.append(f"{tname}: NON-FINITE bound-sphere radius {r}")
    for count, what in ((bad_v, f"/{block.num_vertices} NON-FINITE vertices"), (bad_n, " NON-FINITE normals"),
                        (bad_uv, " NON-FINITE UVs")):
        if count:
            issues.append(f"{tname}: {count}{what}")


def _check_floats(block, tname, issues):
    """Every non-finite float anywhere in the block."""
    floats = []
    _iter_floats(block, tname, floats)
    issues += [f"NON-FINITE {p} = {v}" for p, v in floats if not math.isfinite(v)]


def _check_shapes(block, tname, issues):
    """Degenerate hulls, empty lists, and compressed-mesh triangles Havok reads as a line.
    See: docs/commentary/asset_convert_collision.md#float32-collinear-triangles
    """
    if isinstance(block, NifFormat.bhkConvexVerticesShape):
        if block.num_vertices < 4:
            issues.append(f"{tname}: degenerate hull verts={block.num_vertices}")
        if block.num_normals == 0:
            issues.append(f"{tname}: hull with 0 planes")
    if isinstance(block, NifFormat.bhkListShape) and block.num_sub_shapes == 0:
        issues.append(f"{tname}: 0 children")
    if tname == 'bhkCompressedMeshShapeData':
        keys = [key for key, tri in decode_cms(block, engine=True) if float32_collinear(tri)]
        if keys:
            issues.append(f"{tname}: {len(keys)} float32-COLLINEAR triangles, keys "
                          + ' '.join('%08X' % k for k in keys[:8]))


def _descriptor(block):
    """(descriptor, attribute name) of a constraint block, or (None, None)."""
    for name in _DESCRIPTORS:
        d = getattr(block, name, None)
        if d is not None:
            return d, name
    return None, None


def _dump_descriptor(desc, tname, dname, lines):
    """Append every vector, float and int field of a constraint descriptor."""
    lines.append(f"  {tname} ({dname}):")
    for a in desc._attrs:
        v = _attr(desc, a.name)
        if hasattr(v, 'w') and hasattr(v, 'x'):
            lines.append(f"    {a.name:24} {_fmt4(v)}  |xyz|={_vec_len(v):.4f}")
        elif isinstance(v, float):
            lines.append(f"    {a.name:24} {v:.6f}")
        elif isinstance(v, int):
            lines.append(f"    {a.name:24} {v}")


def _check_descriptor(desc, label, issues):
    """Inverted angle limits and axis fields that are not unit vectors."""
    mn, mx = getattr(desc, 'min_angle', None), getattr(desc, 'max_angle', None)
    if mn is not None and mx is not None and mn > mx:
        issues.append(f"{label}: min_angle {mn} > max_angle {mx}")
    for a in desc._attrs:
        n = a.name
        v = _attr(desc, n) if any(w in n for w in _AXIS_WORDS) and not n.startswith('pivot') else None
        if not (hasattr(v, 'x') and hasattr(v, 'z')):
            continue
        length = _vec_len(v)
        if length <= 1e-6:
            issues.append(f"{label}.{n}: zero vector")
        elif abs(length - 1.0) > 0.05:
            issues.append(f"{label}.{n}: |v|={length:.4f} not unit")


def _check_block(block, issues, lines, dump_constraints):
    """All bhk checks on one block; constraint fields dumped into `lines` when asked."""
    tname = type(block).__name__
    _check_floats(block, tname, issues)
    _check_shapes(block, tname, issues)
    desc, dname = _descriptor(block)
    if desc is None:
        return
    if dump_constraints:
        _dump_descriptor(desc, tname, dname, lines)
    _check_descriptor(desc, f"{tname}.{dname}", issues)


def _read(path):
    """The parsed NIF, or the parse error's text."""
    try:
        data = NifFormat.Data()
        with open(path, 'rb') as f:
            data.read(f)
        return data
    except Exception as e:
        return str(e)


def check_file(path, dump_constraints=False, geometry=False):
    """(report lines, issue count) for one NIF."""
    data = _read(path)
    if isinstance(data, str):
        return [f"{path}: PARSE FAILED: {data}"], 1
    issues, lines = [], []
    for block in data.blocks:
        if geometry and isinstance(block, NifFormat.NiGeometryData):
            _check_geometry(block, issues)
        if type(block).__name__.startswith('bhk'):
            _check_block(block, issues, lines, dump_constraints)
    return [path] + [f"  ISSUE: {i}" for i in issues] + lines, len(issues)


def _files(root):
    """The NIFs named by a list file, a single NIF, or a directory tree."""
    if os.path.isfile(root) and root.lower().endswith('.txt'):
        with open(root, encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]
    if os.path.isfile(root):
        return [root]
    return [os.path.join(dp, f) for dp, _, fs in os.walk(root) for f in fs if f.lower().endswith('.nif')]


def main():
    """Check every NIF and print the reports; the issue total goes to stderr."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("--constraints", action="store_true", help="dump constraint descriptors in detail")
    ap.add_argument("--geometry", action="store_true",
                    help="also NaN-sweep render geometry (verts/normals/UVs/bound spheres)")
    ap.add_argument("--quiet", action="store_true", help="only print files with issues")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = ap.parse_args()
    files = _files(args.root)
    total_issues = 0
    check = partial(check_file, dump_constraints=args.constraints, geometry=args.geometry)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for out, nissues in ex.map(check, files, chunksize=8):
            total_issues += nissues
            if nissues or not args.quiet or args.constraints:
                print("\n".join(out), flush=True)
    print(f"\n{len(files)} files, {total_issues} issues", file=sys.stderr)


if __name__ == "__main__":
    main()
