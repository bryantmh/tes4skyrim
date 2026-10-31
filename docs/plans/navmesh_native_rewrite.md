# Navmesh generator: full C++ rewrite, one process, many threads

**Goal.** The whole per-cell corridor build runs in C++. One process runs every
cell on a thread pool and shares one read-only copy of the collision data. The
output must stay bit-identical to the Python reference, which stays switchable
(`TES_NAVMESH_IMPL=python`) until it is removed.

**Why.** The converter runs on any mod, so every end-user navmesh build is cold.
Kernel-by-kernel ports top out at about 5x because the remaining Python
orchestration is flat ([native-port-ab](../commentary/tes5_import_navmesh.md#native-port-ab)).
Memory also only drops when cells share one process: each worker process
carries about 80 MB of collision data and about 100 MB of imports.

## <a id="architecture"></a>Architecture

```
Python (pool thread, GIL held)          C++ (_navgrow_native, GIL released)
------------------------------          -----------------------------------
per cell: pathgrid graph, doors,  --->  corridor_cell: gather (world.cpp),
  spawns, obstacles, LAND grid,           obstacle bridge (bridge.cpp),
  placement rows (world.placements)       strips, doors, union, cleanup
geometry-cache hit/miss, hashing  <---  verts, tris, ledges
hand edits                        --->  pack_nvnm (navm.cpp): water flags,
                                          adjacency, door triangles, ledge
                                          links, lookup grid, NVNM, center
EDID/ONAM, zlib NAVM record       <---  nvnm bytes, door triangles, center
```

- `corridor_cell` (`pycell.cpp`) reads its inputs with the GIL held, then
  runs `build_cell` with it released: pure C++, no Python calls. Every GEOS
  call goes through a per-thread `GEOSContextHandle_t` on the same GEOS build
  that shapely loads, so overlay results round identically.
- The collision soups are borrowed zero-copy from the process's one loaded
  collision cache (`world.placements` hands the float32 arrays over as
  buffers), so there is no separate native store and no copy per worker.
- Python keeps reading the records into arrays, the geometry cache, hand
  edits and the zlib record wrap (`zlib.compress` releases the GIL; Python's
  own zlib keeps the compressed bytes identical). On 160 real Nehrim jobs this
  Python held the GIL for 1.4 s of 44.3 s single-threaded (3%); at 31 threads
  the same jobs take 4.0 s wall.
- `seeding.seed_graph` is native too (`seed_graph`), over the same gather. A
  seeded job hands its flooded graph straight to `convert_PGRD`
  (`seeding.seeded_graph`, in the order a pathgrid record reads back) instead
  of writing a PGRD dict for the build to parse again.
- Per-plugin planning stays Python: job gathering (`pool.py`) and the
  seeded-cell waves (`seeding.grow_into_empty`, seam seeds) run once in the
  importing process, not per cell.

## <a id="one-process-many-threads"></a>One process, many threads

Under `NATIVE`, `pool._pool` is a `ThreadPoolExecutor` in the importing
process instead of a `ProcessPoolExecutor`. The parent's context is already
complete (`cache_audit.init_context` loads collision, door centers and
activators before `prepare`), so threads need no initializer. The collector
is paused for the run, as it always was in a pool worker: with the whole
plugin's records alive, every container burst would trip a full traversal.
`TES_NAVMESH_IMPL=python` keeps the old process pool.

## <a id="exactness-model"></a>Exactness model

- Python `float` and `numpy.float64` differ in `sum()` (Neumaier compensation
  happens only on exact floats), `round()` (numpy uses `rint(x*10)/10`) and `**`.
  Where a stage mixes them, each vertex coordinate carries an "is numpy" bit
  (`inexact` flags, `PyMixedSum`).
- Dict, set and sort order is reproduced explicitly: insertion order, stable
  sort with the same key, and the same first-wins tie-breaking.
- `pow`, `cos` and `sin` come from ucrtbase, `hypot`/`dist` follow CPython's
  `vector_norm`, and floor-division follows `py_floordiv`.
- numpy's `(N,3,3) @ (3,3)` placement runs through BLAS, which accumulates
  each output as `fma(a2, r2, fma(a1, r1, fma(a0, r0, 0)))`: measured equal on
  every element of 166,698 outputs over stack sizes 1 to 5,000, where plain
  sequential arithmetic differs on about 1 in 5. `np.cross` and
  `np.linalg.norm(axis=1)` are plain left-to-right arithmetic.

## <a id="stages"></a>Stages (pipeline order) and status

| # | Stage | Python | Status |
|---|---|---|---|
| 1 | cleanup + tail | `corridor_clean.finalize`, `_drop_attach_scraps`, `_drop_degenerate_guarded`, `ccw_in_plan`, `_resolve_ledges` | **done** (`clean.cpp`, `stitch.cpp`) |
| 2 | union mesh | `corridor_union.build_union_mesh` + `union_sheets/geom/cdt/mesh` | **done** (`union.cpp`, `usheets.cpp`, `ustrip.cpp`, `ucdt.cpp`, `umesh.cpp`) |
| 3 | doors | `_door_geometry`, `corridor_doors`, `door_routes` | **done** (`cdoors.cpp`) |
| 4 | strips | `_build_corridor_strips`, discs, profiles | **done** (`cstrips.cpp`) |
| 5 | cell geometry | `world.gather_cell_geometry`, samplers, node Z snap, `standing_spawns` | **done** (`world.cpp`, `ccell.cpp`) |
| 6 | pre-passes | `bridge_obstacles`/`cell_ground`, `seed_graph` | **done** (`bridge.cpp`) |
| 7 | driver | thread pool, `pool.py` integration | **done** (`pool._pool`) |
| 8 | NVNM packing | `_compute_water_flags`, `compute_adjacency`, `build_door_links`, `_resolve_ledge_links`, `pack_nvnm`, `lookup_grid`, NVMI center | **done** (`navm.cpp`, `from_pgrd.packed_nvnm`) |

Verify with `tools/navmesh/native_ab.py` on the 8 Oblivion and 2 Nehrim
reference cells (`--mode stress` adds obstacles, water and a split pathgrid;
`--mode flood` the seed flood), and with `tools/navmesh/job_trace.py --ab` on
real pipeline jobs, which compares the packed NAVM bytes
([native-port-ab](../commentary/tes5_import_navmesh.md#native-port-ab)).
