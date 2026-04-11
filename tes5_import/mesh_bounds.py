"""
Compute and cache axis-aligned bounding boxes (AABB) from converted NIF meshes.

Used by the import pipeline to set accurate OBND values on records instead of
type-based defaults.

Two-phase usage (called from convert.py):
    1. scan_mesh_bounds(mesh_dir, cache_path)  — after mesh+speedtree conversion
    2. load_mesh_bounds(cache_path)             — called by import_main.py

Path keys are normalised: lowercase, forward slashes, relative to the mesh
output directory root.  Example: "tes4/furniture/chairnoble01.nif".

Records store raw TES4 model paths like "Furniture\\ChairNoble01.NIF"; after
_prefix_path() and normalisation these map to the same key.
"""

import json
import math
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, Optional, Tuple

OBNDTuple = Tuple[int, int, int, int, int, int]

# Module-level cache populated by load_mesh_bounds().
_MESH_BOUNDS: Dict[str, OBNDTuple] = {}


# ---------------------------------------------------------------------------
# Worker — defined at module level so it is picklable on Windows (spawn)
# ---------------------------------------------------------------------------

def _compute_bounds_worker(args: tuple):
    """Return (rel_key, OBNDTuple | None) for a single NIF file."""
    nif_path, rel_key = args
    import time
    if not hasattr(time, 'clock'):
        time.clock = time.perf_counter  # type: ignore[attr-defined]
    try:
        from pyffi.formats.nif import NifFormat  # type: ignore
        data = NifFormat.Data()
        with open(nif_path, 'rb') as fh:
            data.read(fh)

        xs: list = []
        ys: list = []
        zs: list = []
        for block in data.blocks:
            if type(block).__name__ == 'NiTriShapeData':
                if block.has_vertices:
                    for i in range(block.num_vertices):
                        v = block.vertices[i]
                        xs.append(v.x)
                        ys.append(v.y)
                        zs.append(v.z)

        if not xs:
            return rel_key, None

        return rel_key, (
            int(math.floor(min(xs))), int(math.floor(min(ys))), int(math.floor(min(zs))),
            int(math.ceil(max(xs))),  int(math.ceil(max(ys))),  int(math.ceil(max(zs))),
        )
    except Exception:
        return rel_key, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_mesh_bounds(mesh_dir: str, cache_path: str, workers: int = None) -> int:
    """Scan *mesh_dir* for all .nif files, compute AABB for each, write to *cache_path*.

    Called from convert.py AFTER mesh and speedtree conversion so SpeedTree NIFs
    are already present.  Always performs a full scan and overwrites the cache.

    Returns the number of NIFs successfully processed.
    """
    mesh_dir_norm = os.path.normpath(mesh_dir)
    if not os.path.isdir(mesh_dir_norm):
        print(f"  Mesh bounds: mesh dir not found ({mesh_dir}), skipping scan")
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
        print(f"  Mesh bounds: no .nif files found in {mesh_dir}")
        return 0

    n = len(nif_files)
    if workers is None:
        workers = max(1, (os.cpu_count() or 4) - 1)
    print(f"  Scanning {n} NIFs for bounds ({workers} workers)...")

    results: Dict[str, OBNDTuple] = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_compute_bounds_worker, item): item for item in nif_files}
        done = 0
        for future in as_completed(futures):
            try:
                rel_key, bounds = future.result()
                if bounds is not None:
                    results[rel_key] = bounds
            except Exception:
                pass
            done += 1
            if done % 1000 == 0:
                print(f"    {done}/{n} processed...")

    print(f"  Mesh bounds: {len(results)} / {n} NIFs computed")

    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    with open(cache_path, 'w', encoding='utf-8') as fh:
        json.dump({k: list(v) for k, v in results.items()}, fh)

    return len(results)


def load_mesh_bounds(cache_path: str) -> int:
    """Load previously computed bounds from *cache_path* into the module cache.

    Per-key lookup: if a key exists in the JSON it is used; missing keys fall
    back to type defaults (no recompute).  Returns the number of entries loaded.
    """
    global _MESH_BOUNDS
    if not os.path.exists(cache_path):
        print(f"  Mesh bounds: cache not found ({cache_path}), using type defaults")
        return 0
    try:
        with open(cache_path, encoding='utf-8') as fh:
            raw = json.load(fh)
        _MESH_BOUNDS = {k: tuple(v) for k, v in raw.items()}
        print(f"  Mesh bounds: loaded {len(_MESH_BOUNDS)} entries from cache")
        return len(_MESH_BOUNDS)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  Mesh bounds: could not load cache ({exc}), using type defaults")
        return 0


def get_mesh_obnd(path_key: str) -> Optional[OBNDTuple]:
    """Return cached OBND tuple for *path_key*, or ``None`` if not found.

    *path_key* must be lowercase with forward slashes, relative to the mesh
    output directory root (e.g. ``"tes4/furniture/chairnoble01.nif"``).
    """
    return _MESH_BOUNDS.get(path_key)
