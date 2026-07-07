"""Compute and cache 2D footprint POLYGONS (silhouettes) from converted NIFs.

The navmesh obstacle-carving needs the real horizontal silhouette of each mesh,
not just its axis-aligned bounding box — an AABB turns an angled wall, an
L-shaped building, or a round well into a fat rectangle that carves far too much
(or too little) of the floor.

For each NIF we project every render-geometry vertex to the XY plane and take the
2D CONVEX HULL. The hull is a tight, ordered polygon that follows the mesh
silhouette (for the common convex-ish props/architecture it is exact; for an
L-shape it slightly over-covers, which is acceptable and still far better than an
AABB). To keep large architectural shells from exploding the vertex count we
simplify the hull to <= MAX_HULL_POINTS points.

Cache format (JSON): { path_key: [[x, y], [x, y], ...] }  — CCW ring, no repeat
of the first point. Path keys match mesh_bounds (lowercase, '/', 'tes4/' prefix,
'.nif').  Two-phase usage mirrors mesh_bounds:
    scan_mesh_footprints(mesh_dir, cache_path)  — after mesh conversion
    load_mesh_footprints(cache_path)            — called by the import pipeline
"""

import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

Point = Tuple[float, float]
Ring = List[Point]

MAX_HULL_POINTS = 24

# Module-level cache populated by load_mesh_footprints().
_FOOTPRINTS: Dict[str, Ring] = {}


# ---------------------------------------------------------------------------
# Convex hull (monotone chain) — no scipy dependency in the worker
# ---------------------------------------------------------------------------

def _convex_hull(pts: List[Point]) -> Ring:
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]   # CCW ring


def _simplify_ring(ring: Ring, max_pts: int) -> Ring:
    """Drop the least-significant vertices until <= max_pts remain."""
    if len(ring) <= max_pts:
        return ring
    r = list(ring)
    while len(r) > max_pts:
        n = len(r)
        best_i, best_area = None, None
        for i in range(n):
            a = r[i - 1]
            b = r[i]
            c = r[(i + 1) % n]
            area = abs((b[0] - a[0]) * (c[1] - a[1]) -
                       (c[0] - a[0]) * (b[1] - a[1]))
            if best_area is None or area < best_area:
                best_area, best_i = area, i
        r.pop(best_i)
    return r


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def _compute_footprint_worker(args: tuple):
    """Return (rel_key, ring | None) for a single NIF."""
    nif_path, rel_key = args
    import time
    if not hasattr(time, 'clock'):
        time.clock = time.perf_counter  # type: ignore[attr-defined]
    try:
        from pyffi.formats.nif import NifFormat  # type: ignore
        data = NifFormat.Data()
        with open(nif_path, 'rb') as fh:
            data.read(fh)

        pts: List[Point] = []
        for block in data.blocks:
            if type(block).__name__ == 'NiTriShapeData' and block.has_vertices:
                for i in range(block.num_vertices):
                    v = block.vertices[i]
                    pts.append((float(v.x), float(v.y)))

        if len(pts) < 3:
            return rel_key, None

        hull = _convex_hull(pts)
        if len(hull) < 3:
            return rel_key, None
        hull = _simplify_ring(hull, MAX_HULL_POINTS)
        return rel_key, [[round(x, 2), round(y, 2)] for (x, y) in hull]
    except Exception:
        return rel_key, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_mesh_footprints(mesh_dir: str, cache_path: str, workers: int = None) -> int:
    """Scan mesh_dir for .nif files, compute 2D hull footprints, write cache."""
    mesh_dir_norm = os.path.normpath(mesh_dir)
    if not os.path.isdir(mesh_dir_norm):
        print(f"  Mesh footprints: mesh dir not found ({mesh_dir}), skipping")
        return 0

    nif_files = []
    for root, _dirs, files in os.walk(mesh_dir_norm):
        for fname in files:
            if fname.lower().endswith('.nif'):
                abs_path = os.path.join(root, fname)
                rel = os.path.relpath(abs_path, mesh_dir_norm)
                rel_key = rel.lower().replace('\\', '/')
                nif_files.append((abs_path, rel_key))

    if not nif_files:
        print(f"  Mesh footprints: no .nif files found in {mesh_dir}")
        return 0

    n = len(nif_files)
    if workers is None:
        workers = max(1, (os.cpu_count() or 4) - 1)
    print(f"  Scanning {n} NIFs for footprints ({workers} workers)...")

    results: Dict[str, Ring] = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_compute_footprint_worker, item): item
                   for item in nif_files}
        done = 0
        for future in as_completed(futures):
            try:
                rel_key, ring = future.result()
                if ring is not None:
                    results[rel_key] = ring
            except Exception:
                pass
            done += 1
            if done % 1000 == 0:
                print(f"    {done}/{n} processed...")

    print(f"  Mesh footprints: {len(results)} / {n} NIFs computed")
    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    with open(cache_path, 'w', encoding='utf-8') as fh:
        json.dump(results, fh)
    return len(results)


def load_mesh_footprints(cache_path: str) -> int:
    """Load footprint polygons from cache into the module cache."""
    global _FOOTPRINTS
    if not os.path.exists(cache_path):
        print(f"  Mesh footprints: cache not found ({cache_path})")
        return 0
    try:
        with open(cache_path, encoding='utf-8') as fh:
            raw = json.load(fh)
        _FOOTPRINTS = {k: [(float(x), float(y)) for (x, y) in v]
                       for k, v in raw.items()}
        print(f"  Mesh footprints: loaded {len(_FOOTPRINTS)} entries")
        return len(_FOOTPRINTS)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"  Mesh footprints: could not load cache ({exc})")
        return 0


def get_mesh_footprint(path_key: str) -> Optional[Ring]:
    """Return the cached 2D hull ring for path_key, or None."""
    return _FOOTPRINTS.get(path_key)
