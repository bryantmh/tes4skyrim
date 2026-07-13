# Navmesh Rebuild — Implementation Plan

Status: PROPOSED (not yet implemented)
Supersedes the pathgrid-buffer algorithm in `tes5_import/pgrd_to_navm.py`.
Related: [world_land_navmesh_notes.md](world_land_navmesh_notes.md).

---

## 1. Why the current navmesh is bad

The current converter never looks at the world. It *guesses* the floor by
buffering the pathgrid graph:

1. buffer every PGRD edge into a 75u capsule + every node into a 95u disc,
2. union them → "walkable region",
3. subtract obstacle footprints (2D **convex hulls** of placed statics),
4. Delaunay-fill, and take Z from the nearest pathgrid node.

Every symptom in the TODO traces back to that one design choice:

| Symptom | Root cause |
|---|---|
| Holes that shouldn't be there | The region is a union of discs/capsules. Anywhere the pathgrid is sparse (a room crossed by one edge), the buffer simply doesn't reach → hole. It has no idea there is floor there. |
| Blobby, wandering borders | Borders are buffer offsets of a graph, so they follow the *pathgrid*, not the walls. Compare `temp/anvilfg_navmesh.png` (blobs) with `temp/nav_AFG_withpaintedwalls.png` (painted walls are straight and rectilinear). |
| Disconnected sections | Two rooms joined by a doorway the pathgrid crosses in one long edge get pinched by the door-choke jambs, or the obstacle hull of the door frame severs the link. |
| **Walls "don't show on the image"** | The walls *are never loaded*. The Anvil FG walls live inside `anvilfgfirstfloor.nif`. That shell contains many pathgrid nodes, so `_classify_footprints` labels it a **FLOOR** and it is **never carved** (`FLOOR_NODE_COUNT=3` / `FLOOR_AREA_FRAC=0.35`). The whole building's geometry is discarded. |
| Convex-hull obstacles are wrong | An architecture shell is a *hollow box*. Its convex hull is a **solid rectangle** covering the entire room. It can only ever be "carve the whole room" or "carve nothing" — so the code is forced to pick "nothing". A hull can never represent a wall. |
| Second floor merges with first | Z comes from a 2D nearest-node blend. Two floors stacked in XY are one 2D region; the mesh is 2.5D with no vertical separation. |
| Stairs are bad | Stairs are a Z ramp with no pathgrid detail. Nearest-node Z interpolation across a stair produces a flat or wildly-sloped sheet. |
| Bridges/height wrong outdoors | Z eases toward raw LAND terrain past 300u from a node, so a bridge deck sinks into the river. |

**The classifier the TODO asks for is not the fix — the data it would need is
already in the files, and we are throwing it away.**

---

## 2. The key insight: collision meshes already ARE the answer

Oblivion NIFs ship the **exact geometry the game engine itself uses** to decide
what you stand on and what blocks you: the Havok collision shape. We do not
need to infer walls from render meshes or heuristics — the game already has a
ground-truth answer, and we can just read it.

Verified against the actual asset (`export/.../anvilfgfirstfloor.nif`):

```
NiTriStripsData 16, NiTriStrips 14        ← render geometry (what we use today)
bhkNiTriStripsShape 1  + bhkMoppBvTreeShape 1  + bhkRigidBody 1   ← COLLISION
```

Extracting that collision shape and classifying its 725 triangles by surface
normal alone:

```
collision tris 725   floor-ish (|nz|>0.7) 265   wall-ish (|nz|<0.3) 421
```

A clean, unambiguous split of floors from walls — from data the engine already
trusts, with **no heuristic at all**. This is the whole ballgame.

Coverage over a random 120-NIF sample of the 8032-mesh library:

| | count |
|---|---|
| has collision | **100 / 120 (83%)** |
| `bhkNiTriStripsShape` (+MOPP) | 68 |
| `bhkConvexVerticesShape` | 20 |
| `bhkBoxShape` / `bhkCapsuleShape` / `bhkSphereShape` / `bhkListShape` | 21 |
| no collision (foliage, FX, markers) | 18 |

Meshes with no collision are exactly the ones NPCs walk *through* — so "no
collision → not an obstacle" is correct by construction, not a fallback.

**We already have the extractor.** `asset_convert/collision.py:285`
`_shape_tri_soup(shape)` returns `(triangles, material)` for both mesh shape
types, and `_triangulate_strips()` handles the strip decode. The new code
reuses these rather than reimplementing.

### What this buys us, restated

The problem is no longer "guess the floor from a sparse graph and hope". It
becomes the classical, well-understood problem: **we have the real 3D world as
a triangle soup; voxelize it and extract walkable surfaces** — which is exactly
what Recast (the industry-standard navmesh library, and what the Creation Kit
itself effectively does) does.

---

## 3. Proposed architecture

Three new modules; `pgrd_to_navm.py` is gutted down to the NVNM serializer it
already does correctly (which is validated byte-exact vs Skyrim.esm — **keep it
untouched**).

```
asset_convert/collision_extract.py   NEW  NIF → walkable/blocking triangle soup, CACHED
tes5_import/navmesh/voxel.py         NEW  heightfield voxelization + region growing
tes5_import/navmesh/contour.py       NEW  region → polygons → triangles
tes5_import/navmesh/build.py         NEW  orchestrator (replaces convert_PGRD's middle)
tes5_import/pgrd_to_navm.py          KEEP _pack_nvnm / _pack_navm_record / door links
```

### 3.1 Phase A — collision soup cache (offline, once)

New `asset_convert/collision_extract.py`, mirroring `mesh_footprints.py`'s
proven two-phase cache pattern (`scan_*` after mesh conversion → JSON →
`load_*` in workers).

For each of the 8032 NIFs, walk the collision tree and emit a compact
**local-space triangle soup**, tagging each triangle by its normal:

```python
WALKABLE  = 0   # |nz| >= cos(MAX_SLOPE=46°)        floors, stair treads, bridge decks
BLOCKING  = 1   # steeper than that                 walls, pillars, railings
```

Also record, per mesh, a `pathable` flag from the Havok layer / block type:
* `bhkRigidBody` layer `OL_STATIC`, `OL_TERRAIN`, `OL_STAIRS` → real geometry.
* `OL_CLUTTER`, `OL_TRANSPARENT`, `OL_TRIGGER`, `OL_NONCOLLIDABLE`, and *any*
  havok-animated / non-`MO_SYS_FIXED` body (loose clutter, ropes, banners)
  → **ignored entirely**: these do not obstruct NPCs. This alone fixes the
  TODO's "currently too many objects are avoided".
* Primitive shapes (box/capsule/sphere/convex) are expanded to their triangle
  hull and tagged by normal exactly like mesh shapes — **not** blanket-BLOCKING.
  Whether a solid prop actually obstructs is decided in world space (§3.2b), not
  here.

**Phase A stores geometry, never a verdict.** Every triangle is tagged
`WALKABLE`/`BLOCKING` purely by its own normal. No "is this an obstacle" judgement
is made per-mesh, because it *cannot* be made per-mesh — see §3.2b.

Cache format (compact; float16-quantized to keep it small):
```json
{ "tes4/architecture/anvil/anvilfgfirstfloor.nif":
    {"w": [[x,y,z, x,y,z, x,y,z], ...],    // walkable tris
     "b": [[...], ...],                     // blocking tris
     "layer": "static"} }
```

Cost: measured **~9 NIF/s/core** for pyffi parse. 8032 NIFs ÷ 15 cores ≈
**~10 minutes, ONCE**, then cached to disk like the existing bounds/footprint
caches. Zero cost on subsequent runs. *This is the only new heavy cost and it
is amortized to nothing.*

> Deferred alternative if cache size or scan time disappoints: reuse the
> already-converted **Skyrim** output NIFs (`output/.../meshes/tes4/...`), whose
> CMS collision is already a packed triangle soup and parses faster. Same data,
> and `asset_convert/cms.py` already decodes it.

### 3.2 Phase B — per-cell voxel heightfield (Recast-style)

This is the core. Per cell (interior) or per exterior cell + 1-cell margin:

**B1. Gather the world.** For every REFR in the cell, look up its base model's
cached soup, transform each triangle by the ref's `RotX/RotY/RotZ` + `PosX/Y/Z`
+ `XSCL.Scale` into cell space. (Note: the current code only applies `RotZ` —
ramps and tilted statics need the **full** rotation matrix.) For exteriors,
also emit the LAND heightfield's 32×32×2 triangles as walkable.

**B2. Rasterize into a solid heightfield.**
- Cell size `CS = 16u` in XY (Skyrim NPC radius ≈ 20-35u; 16u gives sub-radius
  precision), `CH = 8u` in Z.
- For each triangle, rasterize into columns; each column stores a sorted list of
  **spans** `(zmin, zmax, walkable_flag)`. Walkable triangles open a walkable
  span; blocking triangles mark theirs blocking.
- Standard Recast filters:
  - **`filterLowHangingWalkableObstacles`** — a low ledge under walkable
    surface becomes walkable.
  - **`filterLedgeSpans`** — a span whose neighbour drops more than
    `MAX_CLIMB` (≈ 34u, Skyrim step height) is a ledge → not walkable.
    *This is what keeps the navmesh off the tops of walls.*
  - **`filterWalkableLowHeightSpans`** — need `AGENT_HEIGHT` (≈ 128u) of
    clearance above a span or an NPC can't fit (kills crawlspaces under stairs).

### 3.2b Step-over: rugs, pillows, and low clutter

Small flat objects (rugs, pillows, cushions, sacks, floor debris) **have
collision but must remain fully pathable** — an NPC walks straight over a rug.
Getting this wrong reintroduces the exact over-carving the TODO complains about
("too many objects are avoided"), so it is worth stating precisely why the
obvious fixes don't work.

**A per-mesh height threshold cannot work.** Measured from the real assets,
collision meshes are **origin-centered**, so a ref's `PosZ` sits at the object's
*middle*, not its base:

```
lowerclasstable01.nif    collision z [ -28.7 ..  +28.7]   (a ~57u tall table)
opensack01.nif           collision z [ -17.4 ..  +17.3]
castlediningtable01.nif  collision z [ -28.4 ..  +28.4]
lowerclassbench01.nif    total collision height 30u   ← BELOW MAX_CLIMB (34u)
```

A local-space "height < 34u ⇒ steppable" rule would classify a **dining table**
and a **bench** as walk-over-able. Local extents say nothing about how far an
object rises *off the floor it is standing on*.

**The decision belongs in the voxelizer, in world space, and Recast already
makes it.** Once triangles are placed in world space (§3.2 B1), the floor's Z
under each column is known, and the existing span filters resolve this with no
new heuristic and no per-mesh classification:

- A **rug / pillow / sack** rasterizes to a walkable span whose top is only a
  few units above the floor span. `filterLowHangingWalkableObstacles` merges it
  into the floor: the rug's top simply *becomes* the walkable surface. NPCs walk
  over it. Its near-vertical edge triangles are shorter than `MAX_CLIMB` and are
  stepped over, not treated as walls.
- A **table / barrel / crate** rises far more than `MAX_CLIMB` above the floor.
  Its sides are ledges (`filterLedgeSpans`), so the floor navmesh stops at them
  and paths route around — correct.
- A **table top** *is* a walkable span, but it is a separate region containing
  no pathgrid node, so Phase C1 seeding discards it. NPCs never path across
  tabletops.
- A **bench** (30u) is genuinely step-onto-able in Oblivion, and the voxelizer
  will treat it that way — which is right, and is what the pathgrid's sit-node
  already implies.

So the single governing rule is: **an object obstructs iff it rises more than
`MAX_CLIMB` (34u) above the walkable floor beneath it** — evaluated per voxel
column in world space, never per mesh. Rugs and pillows are handled by
construction; there is no rug list, no size gate, and no `MIN_EXCLUSION_HEIGHT`
tuning constant. (All three of today's gates — `MIN_EXCLUSION_HEIGHT`,
`MIN_EXCLUSION_HALF_EXTENT`, `MIN_EXCLUSION_AREA` — are deleted.)

Validation: assert that a rug REFR and a pillow REFR are covered by walkable
triangles, while a table/barrel REFR is not (invariant test, §6.4).

**B3. Erode by agent radius.** Erode the walkable set by `AGENT_RADIUS` (≈ 24u)
so the mesh keeps a clean standoff from every wall — this is what replaces the
current `EXCLUSION_MARGIN` hack, and it is *geometrically correct* rather than
tuned.

**This step solves, by construction:**
- *Walls*: they are blocking spans; the eroded walkable set stops at them, with
  straight borders that follow the actual wall — matching the painted reference.
- *Multi-floor*: each column holds **multiple spans at different Z**. Floor 1 and
  floor 2 are separate spans in the same column and are never merged. This is
  the fix for "incorrectly attach second floor triangles with the first floor".
- *Stairs*: a stair is a staircase of spans; `MAX_CLIMB` connects them
  step-to-step, producing a proper sloped, connected navmesh.
- *Bridges*: the bridge deck is a walkable span *above* the river's LAND span.
  Both exist; the deck is walkable, correctly at deck height.
- *Holes*: gone. Floor is floor because the floor collision mesh says so, not
  because a pathgrid edge happened to pass nearby.
- *Low clutter*: rugs, pillows and sacks are walked over rather than carved
  around — see §3.2b.

### 3.3 Phase C — pathgrid as SEED + ORACLE (not as geometry)

The pathgrid stops being the *source* of the mesh and becomes what it actually
is: **Bethesda's own ground-truth annotation of where NPCs are known to walk.**
This is exactly the user's "trust the pathgrid height" instinct, applied where
it is reliable.

1. **Seeding / region selection.** Voxelization yields many disconnected walkable
   regions (the floor, but also tabletops, roofs, window ledges, the tops of
   crates). Flood-fill regions and **keep only regions containing (or within
   `SEED_SNAP`≈48u of) a pathgrid node**. Everything else — roofs, tables,
   ledges — is silently dropped. The pathgrid tells us which of the many
   physically-standable surfaces are the ones the designers intended.

2. **Z disambiguation on multi-floor.** When a pathgrid node's XY has several
   spans (floor 1 / floor 2), pick the span whose Z is nearest the node's Z.
   The pathgrid node Z is the designer's statement of which floor it means.

3. **Bridge/height oracle.** A node hovering ~100u above LAND with a walkable
   span at exactly that height confirms the span (the bridge deck / cobbled
   street). No more `LAND_BLEND_DIST` easing hack — the geometry already has the
   deck, and the node just selects it.

4. **Connectivity repair.** If two regions each contain pathgrid nodes joined by
   a PGRD *edge*, they must be connected. If voxelization left them separate
   (a too-thin doorway, a missing collision plank), carve a corridor of walkable
   voxels along that edge and re-run. **This is the safety net that guarantees
   we never regress connectivity below today's output**, and it directly fixes
   "disconnected sections" and "fix discontinuities without going through
   walls" — the corridor follows a real pathgrid edge, which by definition does
   not pass through a wall.

### 3.4 Phase D — contour → polygons → triangles

Standard Recast back half, all straightforward:
1. **Build contours** by walking region boundaries in the voxel grid.
2. **Simplify** each contour (Douglas-Peucker, `MAX_SIMPLIFY_ERR ≈ 12u`) →
   straight wall-hugging edges instead of a 16u staircase. *This is what makes
   borders look like the painted reference.*
3. **Merge into convex polygons** (Recast's `rcBuildPolyMesh` greedy merge) →
   **large triangles**, satisfying "fills those holes with good large triangles"
   and keeping the triangle count near vanilla.
4. **Fan-triangulate** each polygon (Skyrim NVNM needs triangles).
5. Assign vertex Z from the span heights (with the pathgrid node Z as the
   authority near nodes, per C2).

Then hand off to the **existing, already-validated** code: `_compute_adjacency`,
`_compute_water_flags`, `_build_door_links`, `_pack_nvnm`, `_pack_navm_record`,
NAVI via `navi_builder.py`. **No change to the binary layer** — it is byte-exact
vs Skyrim.esm and must stay that way.

### 3.5 Doors

The current door-choke hack (jamb rectangles + a re-unioned passage slot) is
deleted. With real geometry, the door frame's own collision necks the corridor
naturally. We keep only:
- the door-triangle **link** (`_build_door_links` — unchanged), and
- a guaranteed voxel corridor through each door centre (Phase C4's repair),
  so a closed door's collision can never seal the navmesh.

---

## 4. Performance budget

Requirement: current ≈ 1 min; "a few times more" is acceptable → **target ≤ 5 min**.

| Phase | Cost | Notes |
|---|---|---|
| A: collision cache | ~10 min **once**, then **0** | Cached to JSON like `mesh_bounds`. Amortized; not in the per-run budget. |
| B: voxelize + filter | ~10-25 ms/cell | An interior is ~4096u² → 256×256 columns @16u. Numpy-vectorized rasterization. |
| C: regions + seeding | ~2-5 ms/cell | Flood fill on the voxel grid. |
| D: contour + polys | ~5-10 ms/cell | Pure python on a small boundary set. |
| **Total per cell** | **~25-40 ms** | |
| **8228 cells / 15 procs** | **≈ 15-25 s** | Reuses the existing `ProcessPoolExecutor` in `_precompute_navmeshes`. |

Comfortably inside budget — and the existing parallel harness
(`navm_worker.py` + `_precompute_navmeshes`) is reused as-is; only the worker's
init gains one more cache to load.

**Guard rails:**
- Voxel grid is `numpy`, not python lists (rasterization is the hot loop).
- Cap grid at 512×512 columns/cell; fall back to `CS=32u` for huge worldspace cells.
- Per-cell hard timeout → fall back to the *old* algorithm for that cell, so a
  pathological cell can never stall or regress the whole run.

---

## 5. Tunable constants (all in one place, `navmesh/params.py`)

Chosen from Skyrim's actual actor dimensions, not guessed:

| Const | Value | Meaning |
|---|---|---|
| `CS` | 16u | voxel XY size |
| `CH` | 8u | voxel Z size |
| `AGENT_RADIUS` | 24u | erode distance (standoff from walls) |
| `AGENT_HEIGHT` | 128u | required headroom |
| `MAX_CLIMB` | 34u | step height (stairs connect; ledges don't) |
| `MAX_SLOPE` | 46° | walkable normal cutoff |
| `MAX_SIMPLIFY_ERR` | 12u | contour simplification |
| `SEED_SNAP` | 48u | pathgrid node → region association radius |

---

## 6. Validation

1. **`tools/navmesh_render.py` gains a `--collision` overlay** rendering the
   walkable (green) / blocking (red) collision triangles. This makes walls
   *visible* — the single biggest debugging gap today, and exactly the user's
   complaint that "the walls don't show on the image".
2. **A/B against the painted reference.** `temp/nav_AFG_withpaintedwalls.png` is
   ground truth: the generated navmesh borders must land on the painted black
   wall lines. This is the acceptance test for the Anvil FG case.
3. **Regression cells** (already rendered, so directly comparable):
   AnvilFightersGuild (walls + 2 floors), AnvilFightersGuildDinningHall,
   AnvilMainGate (door choke), AnvilExt02 (exterior + LAND), plus a stairs cell
   and a bridge cell.
4. **Invariants** (extend `tests/test_pgrd_navm.py`):
   - every pathgrid node lies within `SEED_SNAP` of a navmesh triangle
     (**no lost coverage vs today**);
   - every PGRD edge's endpoints are in the *same* connected navmesh component
     (**no disconnected sections**);
   - no triangle centroid lies inside a BLOCKING collision triangle's XY
     footprint within ±`MAX_CLIMB` in Z (**no navmesh through walls**);
   - **step-over**: a cell containing a rug/pillow/sack REFR has walkable
     triangles covering that ref's footprint, while a table/barrel/crate REFR's
     footprint is *not* covered (**low clutter stays pathable, §3.2b**);
   - triangle count per cell within ~2× of vanilla Skyrim for a comparable cell;
   - NVNM round-trips byte-exact (existing tests, must stay green).
5. **In-game**: NPC pathing in Anvil FG across both floors and up the stairs.

---

## 7. Implementation order

Each step is independently verifiable; the pipeline stays runnable throughout.

1. `collision_extract.py` + cache + wire the scan into `run/convert.py` beside
   `scan_mesh_bounds` / `scan_mesh_footprints`. Verify: dump Anvil FG's soup,
   confirm 265 walkable / 421 blocking.
2. `navmesh_render.py --collision` overlay. **Do this second** — it is how every
   later step gets debugged, and it immediately answers "why don't walls show".
3. `voxel.py`: rasterize + span filters. Verify visually on Anvil FG: the
   walkable span set must look like the painted rooms.
4. `contour.py`: regions → contours → simplify → polys → tris.
5. `build.py`: pathgrid seeding, Z disambiguation, connectivity repair; swap
   `convert_PGRD`'s middle to call it; keep NVNM packing untouched.
6. Delete the dead pathgrid-buffer path (`_build_walkable_polygon`,
   `_classify_footprints`, `_triangulate_region`, `_assign_z`,
   `_build_exclusion_zones`, `_door_choke_obstacles`) and the now-unused
   `mesh_footprints.py` convex-hull cache.
7. Tune constants against the regression cells; update
   `docs/world_land_navmesh_notes.md`.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Collision cache scan is slow / large | Cached once; quantize to float16; or read the already-converted Skyrim CMS output instead (§3.1 note). |
| Some cells lose coverage vs today | Phase C4 connectivity repair + invariant test #4 (every node reachable). Per-cell fallback to the old algorithm on timeout/failure. |
| Meshes with no collision that *should* block | Only 15% of meshes, and they are foliage/FX/markers — correct to ignore. If a real blocker is found, fall back to its render-mesh hull for that model (opt-in list). |
| Low clutter (rugs/pillows) wrongly carved | Resolved by construction in world space, not by a size gate — see §3.2b. Guarded by invariant test §6.4. |
| Origin-centered collision misread as floor-based | Never infer height from local space; only the voxelizer (world space, floor Z known) may judge obstruction. §3.2b. |
| Exteriors: refs from neighbouring cells overlap the border | Gather REFRs from the 3×3 cell neighbourhood, clip the mesh to the cell bounds — same as vanilla. |
| Voxel memory on huge exterior cells | Cap at 512² columns; degrade `CS` to 32u. |
