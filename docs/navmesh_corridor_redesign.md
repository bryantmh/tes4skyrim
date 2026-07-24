# Navmesh redesign: pathgrid corridor ribbons

**Status:** design, not yet implemented. Author-approved direction 2026-07-23.
**Supersedes (once built):** whatever navmesh surface generator lives in
`tes5_import/navmesh/build.py::build_navmesh`. Work happens directly on
`master`. The prior Recast-based generator (and its ~900 lines of stitch/clip
repair) has been moved to branch **`test-navmesh-2`** and can be pulled from
there if any piece is needed.

## Baseline on `master` (verified 2026-07-23)

`master`'s navmesh is NOT the Recast pipeline — it is a **voxel / span-graph**
generator: `voxel.py` (heightfield + `stamp_pathgrid` + filters + erosion),
`region.py` (region flood + pathgrid seeding), `spanmesh.py` (mesh the span
graph directly). `build_navmesh` signature:

```
build_navmesh(refr_recs, base_model_by_fid, get_collision, nodes, edges,
              land_rec=None, origin_x=0.0, origin_y=0.0, budget=None, doors=None)
    -> (verts, tris)   # world-space; [] , [] on failure
```

There is **no `door_carve.py` on master** — doors are stamped into the voxel
grid and passed to `spanmesh.build_mesh(doors=door_rects)`. The Recast-era
`door_carve.py` (shapely cut-and-earcut) lives on `test-navmesh-2`.

This voxel pipeline is cleaner than the Recast one (pathgrid stamped first,
span-graph meshing so adjacency is structural), but it is still heavy: voxel
grid, filters, region flood, erosion, span meshing, steep-tri drop, flap cull,
island prune. The corridor model replaces the whole surface generator with a
direct ribbon build.

---

## Why replace it

The pathgrid is already the "an actor walks here" graph. Every voxel/Recast
generator spends its complexity RE-DISCOVERING walkable surface from collision
and then fighting to keep the mesh connected across the seams that discovery
introduces (the Recast version needed ~900 lines of weld/stitch/clip to undo
its own per-sheet fragmentation; the voxel version needs region flood +
seeding + geodesic pathgrid-reach culling to keep the pathgrid's surface and
throw away the ceiling a staircase flood-merged into).

The corridor model builds the mesh **directly on the pathgrid**, so:
- connectivity is structural (edges meeting at a node share the node vertex);
- there is no surface to re-discover, so no filters/flood/erosion;
- the result is exactly what the pathgrid asserts and nothing more.

It removes the problem at the source rather than repairing it downstream.

### The core idea

The pathgrid **is** the "an actor walks here" graph. Build the navmesh directly
on it:

> Emit a fixed-width ribbon of triangles centred on every pathgrid edge. Edges
> that meet at a shared node **share that node's vertices by construction**, so
> triangle adjacency links automatically. No independent sheets, so nothing to
> weld or stitch.

Connectivity becomes a property of the construction, not a post-process. The
entire 900-line stitch/clip/dedup/manifold apparatus is deleted.

The trade the author accepted explicitly: **a completely functional navmesh
with zero bad triangles, even if it is a bit sparse, beats a dense but broken
one.** Sparse-but-correct is the Phase 1 target.

---

## Author-set principles (do not violate)

These came from direct decisions on 2026-07-23. They constrain every phase.

1. **The pathgrid centerline is sacred.** The pathgrid asserts an actor walks
   the line; we trust it. We never cut, clip, or move the centerline — not even
   where it clips a wall (Oblivion authors cut corners constantly). Only *grown
   width* may ever be clipped (Phase 2+), never the ribbon spine.

2. **Downward snap follows the pathgrid line's own slope — it is NOT a per-tread
   re-fit.** A pathgrid edge already has a slope: node A at `z_a`, node B at
   `z_b`. That straight line **is** the walk ramp. A staircase comes out as one
   clean ramp because the Oblivion nodes are placed at tread level and the A→B
   line is already the ramp. "Snap down" means: sit the ribbon on that line, and
   only push a cross-section *down* onto walkable collision when the line floats
   above it — never let jagged tread collision push samples up and reintroduce a
   sawtooth. A slope stays a slope. (This is the single biggest simplification
   over the current `EDGE_SEG_TOL`/`STAIR_TRACK_TOL` per-sample piecewise fit.)

3. **Be conservative; stop when unsure.** Doorways are *assumed* to already have
   pathgrid running through them, so lateral growth never has to "find" a
   doorway — it only has to avoid leaking through one. When growth is uncertain,
   stop. We can always widen later. A missing sliver of floor is recoverable; a
   through-wall triangle is a bug.

4. **Never put navmesh on the wrong side of a wall.** The current code "often
   puts navmesh on the other side of walls." The corridor model must not
   reproduce this. Because the centerline is sacred (principle 1), through-wall
   mesh can only arise from *grown width* leaking across a wall — so all wall
   handling lives in the width-grow phase, and defaults to stopping early.

5. **Phase it. Phase 1 is corridors + doors + links, and must be completely
   right before any width-grow or polish is added.** A navmesh with a perfect
   surface but no door links and no cell links is DEAD in the engine — an actor
   cannot cross a doorway or a cell boundary. So Phase 1 is not "surface only";
   it is "a *complete, functional* navmesh, just narrow." Door carve and the
   link passes are in scope for Phase 1 (author, 2026-07-23).

---

## What stays exactly as-is

The corridor generator replaces the surface generator inside `build_navmesh`.
The record packing and the link passes are downstream, mesh-agnostic, and
already verified byte-exact — they are REUSED, not rewritten. Phase 1's job is
to feed them a mesh that presents the anchors they need.

| Component | Role | Change |
|---|---|---|
| `world.gather_cell_geometry` | REFR + LAND collision → `walkable`/`blocking` (N,3,3) soups | **none** — Phase 1 uses `walkable` for the downward snap; Phase 2 uses `blocking` for lateral stop |
| `pgrd_to_navm.convert_PGRD` | reads PGRD, builds NVNM/NAVM bytes, water flags, ONAM, calls `_build_door_links` | **none** — still calls `build_navmesh(...)` → `(verts3d, tris)` and links doors on the result |
| `pgrd_to_navm._compute_adjacency` | writes the NVNM neighbour fields the engine walks | **none** — the corridor mesh MUST satisfy the same manifold rule (≤2 tris/edge) |
| `pgrd_to_navm._build_door_links` | finds the tri CONTAINING each door threshold; falls back to nearest-on-threshold-line | **none** — but Phase 1's door carve must guarantee a triangle actually sits under each door, else this silently falls back or drops the link |
| `navm_edge_links.build_edge_links` | reciprocal Portal links across exterior cell seams; needs border edges near the seam plane | **none** — decodes NVNM bytes and matches border edges; works on ANY mesh. Phase 1 must ensure ribbons reach the cell boundary so border edges exist there |
| `navi_builder` NAVI singleton + NVMI mirror | registers every mesh engine-wide (no NAVI ⇒ zero pathfinding anywhere) + mirrors door/edge links | **none** |
| geometry cache (`_geom_hash`, `_GEOM_BUILD_VERSION`) | disk cache keyed on inputs | bump `_GEOM_BUILD_VERSION`; the corridor build is a new pipeline |

The **contract** `build_navmesh` must keep: return `(verts3d, tris)`, a list of
`(x,y,z)` float tuples and a list of `(i,j,k)` int tuples, forming a
**manifold** mesh (every edge shared by ≤2 triangles — a 3+ edge silently
disconnects everything around it under `_compute_adjacency`).

### The two link systems, and what the corridor mesh owes each

**Door links** (interior passages AND cross-cell teleport doors). Built in
`pgrd_to_navm._build_door_links(verts, tris, doors)`: for each door it finds the
triangle whose 2D footprint CONTAINS the (pivot-corrected) threshold point at
the door's storey Z; failing that, the nearest triangle centred on the threshold
line within `DOOR_LINK_MAX_DIST`. That triangle is flagged `_TRI_FLAG_DOOR` and
emitted as a Door Triangle, and its ref FormID goes into the NVMI door mirror.
**What the corridor mesh owes it:** a well-shaped, connected triangle sitting
exactly on each door threshold. In the sparse ribbon model this only happens for
free if a pathgrid edge runs through the door — and even then the pivot→panel
offset can nudge the threshold just off the ribbon. So **Phase 1 includes a door
carve** (below) whose whole job is to place that triangle and connect it to the
corridor mass.

**Cell links** (exterior cross-cell Portals). Built in
`navm_edge_links.build_edge_links` as a post-pass over the whole navmesh cache:
it finds border edges (neighbour field −1) lying within `SEAM_BAND` of a shared
cell-boundary plane and pairs them reciprocally across the seam. **What the
corridor mesh owes it:** ribbon triangles with border edges at the cell boundary
plane. An exterior pathgrid edge that crosses (or ends at) the cell boundary
produces exactly such border edges — so this is satisfied by construction as
long as the ribbon is emitted out to the node, and no clamp pulls it inside the
seam band. Phase 1 verifies this; it writes no new code for cell links.

---

## Phase 1 — corridors + doors + links (a complete, narrow navmesh)

**Goal:** for every cell, a connected, manifold, zero-bad-triangle ribbon mesh
following the pathgrid graph, sitting on walkable collision, with a Door
Triangle under every door and border edges at cell seams so the existing door-
link and cell-link passes produce a fully functional (if narrow) navmesh.

### Inputs (already available inside `build_navmesh`)
- `nodes`: pathgrid nodes `[(x,y,z), ...]` (world coords: cell-local interior,
  world exterior — same frame as collision).
- `edges`: `[(i,j), ...]` node-index pairs.
- `walkable`: `(N,3,3)` float array of walkable collision (floors, treads,
  terrain), from `gather_cell_geometry`.
- `doors`: `[(x, y, z, rot_z, is_teleport), ...]` pivot-corrected door centres
  (already assembled by `pgrd_to_navm._collect_doors` and passed through).

### Algorithm

**Step 0 — walkable surface sampler.**
Reuse the existing `_walkable_surface_sampler(walkable)` from `build.py`
verbatim (it is already independent of the rest). It returns
`sample(x, y, near_z) -> z | None`: the walkable-collision height at `(x,y)`
nearest `near_z`, bucketed to a coarse XY grid. This is the only collision query
Phase 1 needs.

**Step 1 — a vertex per node.**
For each pathgrid node `i`, its ribbon spine point is the node XY at the node's
own Z, snapped down onto walkable collision:

```
z_i = snap_down(node_i.x, node_i.y, node_i.z)
```

where `snap_down(x, y, z)`:
- `s = sample(x, y, z)`
- if `s is None`: keep `z` (no collision known here — trust the pathgrid; a
  missing sample must never delete the spine, principle 1).
- else if `s <= z + SEED_SNAP_UP` and `s >= z - SEED_SNAP_DOWN`: use `s`
  (the surface is within the plausible window; sit on it).
- else if `s < z`: the surface is far below (node floats over a pit/upper
  storey) — clamp the drop to `z - SEED_SNAP_DOWN` rather than teleporting to a
  distant floor. **Conservative.**
- else (`s > z + SEED_SNAP_UP`): surface is above the node (an object sitting on
  the floor, or the node is under geometry) — keep `z`, do **not** rise onto it.

Reuse `SEED_SNAP_DOWN` (96) and `SEED_SNAP_UP` (=MAX_CLIMB, 34) from `params`.

**Step 2 — ribbon each edge, following the line's slope.**
For edge `(i, j)` with snapped endpoints `A=(ax,ay,az)`, `B=(bx,by,bz)`:

- Width direction `w = normalize(perp(B-A in XY))`; half-width `HALF`
  (Phase 1 constant, below).
- Densify the edge into `k = max(1, round(len_xy(A,B) / RIBBON_STEP))` segments
  so a long edge is several quads (needed so the ribbon can *follow* a curved
  or bumpy floor in Z; a single quad would bridge straight over dips).
- For each cross-section parameter `t` in `{0, 1/k, ..., 1}`:
  - centre `C(t) = lerp(A, B, t)` — **Z comes from the straight A→B line**, not
    re-sampled per cross-section (principle 2: the line's slope is the ramp).
  - left `L(t) = C(t) + HALF * w`, right `R(t) = C(t) - HALF * w`, **both at
    `C(t).z`** — the corridor is FLAT across its width (author decision
    2026-07-23: "just keep the corridors of navmesh flat"). No per-rail snap.
    The whole cross-section lies on the centerline plane, so a rail can never
    drape down a ledge and no side-collision query is needed in Phase 1.
- Emit two triangles per segment (quad `L(t),R(t),R(t+1),L(t+1)`), CCW.

**Step 3 — shared vertices at nodes = free connectivity.**
Key detail that makes the whole model work: **the two cross-section vertices at
a node are minted ONCE per node and reused by every edge incident to that node.**
Maintain `node_ribbon_verts[i]` — but a node has one spine point and *many*
incident edges leaving at different angles, so the left/right rails of different
edges do **not** coincide. Two options, decide in Open Question B:

- **B1 (Phase 1 default — simplest, guaranteed manifold):** every edge is an
  independent quad strip that shares **only the single spine vertex** at each
  node (mint one shared vertex per node at `(node.x, node.y, z_i)`, and have
  every incident edge's strip include a triangle fan back to it). Ribbons then
  overlap slightly at junctions but always share the node vertex, so adjacency
  links through the node. Overlap at a junction is coplanar and small; the
  manifold pass (Step 4) resolves any 3+-shared edge.
- **B2 (nicer, more work — deferred):** compute a proper junction polygon at
  each node (miter the incident ribbons) so rails meet cleanly. This is
  Phase 2+ polish, not Phase 1.

Phase 1 uses **B1**: correctness first, junction beauty later.

**Step 4 — door carve (connect every door to the corridor mass).**
A door with no triangle under its threshold gets no Door Triangle, so the engine
cannot path through it — the mesh is dead at that doorway. Because the pathgrid
is assumed to run through every doorway (principle 3), a ribbon usually already
passes near each door; the carve's job is to guarantee a well-shaped triangle
sits *exactly* on the (pivot-corrected) threshold and is *connected* to the
ribbon. The ribbon model makes this far simpler than the shapely cut-and-earcut
`door_carve.py` on `test-navmesh-2`:

For each door `(dx, dy, dz, rz, is_tp)`:
1. **Find the storey Z** = the ribbon Z nearest `dz` within `DOOR_QUAD_ZTOL`
   (the door REFR z only picks the storey). If no ribbon triangle is within
   `DOOR_BRIDGE_RADIUS` of `(dx,dy)` at that storey, the door is genuinely walled
   off from the pathgrid — skip it (conservative; do not invent a floating
   patch).
2. **Stamp a small threshold quad** on the door line: an oriented rect centred at
   `(dx,dy,storey_z)`, width `2·DOOR_QUAD_HALF_WIDTH` along the door axis, depth
   `2·DOOR_QUAD_HALF_DEPTH` across it, flat at `storey_z`. Two triangles. Its long
   edge lies ON the door line — exactly what `_build_door_links` wants to flag.
3. **Connect it to the ribbon** by welding the quad's corners to the nearest
   ribbon vertices within a small weld epsilon, and — where a quad corner lands
   in a ribbon triangle's interior rather than on a vertex — splitting that
   ribbon edge so both sides share indices (a minimal, LOCAL T-junction split, not
   the general stitch machinery). If the quad and the ribbon overlap, drop the
   quad triangles that fall inside the ribbon and keep only the part that extends
   coverage to the threshold. The manifold pass (Step 5) cleans any residue.
4. Interior doors: done. Teleport doors: same, and Phase 1 does NOT clip the far
   side (deferred — see Phase 3). The ribbon simply ends where the pathgrid ends.

This is a self-contained `corridor_doors.py` (or a function in the new build
module), NOT the `test-navmesh-2` `door_carve.py`. It reuses `DOOR_QUAD_*` and
`DOOR_BRIDGE_RADIUS`-style constants from `params`.

**Step 5 — make manifold + drop degenerate.**
Run the existing `_make_manifold` and `_drop_degenerate` (generic, no sheet
assumptions). This guarantees the ≤2-tris-per-edge invariant
`_compute_adjacency` requires. Nothing else — no welding of the ribbon body
(vertices are already shared by construction), no stitching, no clipping.

**Step 6 — return `(verts, tris)`.** `pgrd_to_navm.convert_PGRD` then runs
`_build_door_links` (finds the Door Triangle we stamped) and packs the NVNM;
`navm_edge_links` + `navi_builder` run as post-passes over the whole cache.

### Phase 1 parameters (new, in `params.py`)
```
RIBBON_HALF_WIDTH = 40.0     # half of ~door width (80u), fits Oblivion ~110u doors
RIBBON_STEP       = 32.0     # cross-section spacing along an edge (follow Z)
RIBBON_WELD_EPS   = 8.0      # weld door-quad corners to nearby ribbon vertices
```
Reuse `SEED_SNAP_DOWN`, `SEED_SNAP_UP`, `MAX_CLIMB`, `MIN_XY_FOOTPRINT`,
`DOOR_QUAD_HALF_WIDTH`, `DOOR_QUAD_HALF_DEPTH`, `DOOR_QUAD_ZTOL`.

### What Phase 1 deliberately does NOT do
- No lateral width-grow (fixed `RIBBON_HALF_WIDTH`) — Phase 2.
- No `blocking`/wall collision use at all. It cannot leak through a wall because
  it never grows into one; it CAN still ribbon *along* a wall-hugging pathgrid
  line — accepted (principle 1).
- No teleport-door far-side clipping (`_interior_sign`) — Phase 3.
- No junction mitering (Open Question B2) — Phase 2+.
- Likely no unreachable-cull / sliver-prune: the corridor mesh has no stray
  scraps to cull. Leave them out; add back only if real output needs it (Q C).
- No exterior special-casing beyond the terrain already in `walkable`.

### Phase 1 acceptance (get it *completely* right)
A cell is done only when it is a *complete, functional* navmesh — surface AND
links. Verify on the canonical problem cells:
- **Pinarus' house (interior, stairs + upper floor + door):** one connected
  component; staircase is a single clean ramp (not a sawtooth); upstairs
  reachable from downstairs; the exterior door has a Door Triangle and
  `_build_door_links` attaches it. `tools/navmesh_reach.py` shows the quest
  start→goal reachable *through* the door.
- **A cave interior:** floor followed in Z, no bad triangles.
- **An exterior grid cell with terrain + a road pathgrid:** ribbon follows the
  road, sits on LAND terrain, and `navm_edge_links` reports Portals created at
  the shared seams with its neighbours (border edges present at the boundary
  plane).
- **A house with a load door, both sides:** the interior mesh and the exterior
  mesh each carry the door's Door Triangle, and the NVMI door mirror lists the
  same ref both sides (the vanilla rule already in `convert_PGRD`).
- **Global invariants (all cells):** zero degenerate/zero-area triangles; every
  edge shared by ≤2 triangles (manifold); `_components` count equals the pathgrid
  connected-component count (no splits, no false merges); every door with a
  pathgrid edge through it gets a Door Triangle; byte-reproducible
  (`tools/esm_diff.py`).

Tools: `tools/navmesh_probe.py`, `tools/navmesh_reach.py`, `tools/navmesh_check.py`
(validate against Skyrim.esm first — it has known findings, don't chase those).

---

## Phase 2 — grow width to walls (deferred, sketch only)

Once Phase 1 is solid: replace the fixed `RIBBON_HALF_WIDTH` with a per
cross-section width that grows outward until it *conservatively* hits a wall.

- Use `blocking` collision. Grow each rail outward in steps; stop the rail when
  the vertical column from the ribbon floor up to `AGENT_HEIGHT` at the trial
  point intersects `blocking`, **or** the walkable surface under the trial point
  departs from the centerline Z by more than `MAX_CLIMB`, **or** a hard
  `RIBBON_MAX_HALF_WIDTH` cap (~128–192u) is reached.
- **The centerline never moves** (principle 1). Only rails grow.
- **Conservative stop** (principle 3): if a growth step is ambiguous (sample
  returns `None`, or the column is marginal), stop there. Under-growing is fine.
- The max-width cap means even a doorway leak becomes a small nub reaching into
  the next room, never a whole extra floor — the specific failure the author
  flagged. Combined with "doorways already have pathgrid through them," growth
  rarely needs to reach a doorway at all.

This is where wall-side correctness is won or lost; it gets its own design pass
and its own acceptance run before it ships.

## Phase 3 — polish (deferred, sketch only)

- Teleport-door far-side clipping (port `_interior_sign` from `test-navmesh-2`'s
  `door_carve.py`) so a teleport door does not trail ribbon into the decorative
  geometry beyond the cell shell.
- Junction mitering (Open Question B2) for cleaner intersections.
- Wider door thresholds / better-shaped Door Triangles if the stamped quad reads
  as too small in-game.

---

## Decisions made (author) and open questions

Resolved 2026-07-23:
- **Rails are FLAT** on the centerline plane (Step 2). No per-rail snap. Closed.
- **Junctions use B1** (shared spine vertex). Mitering deferred to Phase 2+.
- **Door carve + door links + cell links are IN Phase 1.** A navmesh without
  them is dead in-engine.
- **Work on `master`;** the Recast generator is preserved on `test-navmesh-2`.

Still open, to resolve during the Phase 1 build:
- **C. Do we need any island cull / sliver prune at all?** Hypothesis: no — the
  corridor mesh has no stray scraps. Leave them out; add back only if output
  demands. The pathgrid-component-count invariant (acceptance) will catch a
  regression.
- **D. Door-quad → ribbon connection robustness.** Step 4's weld+split must not
  create a non-manifold edge or an island threshold. Validate the Door Triangle
  is in the SAME component as the ribbon it serves (not just spatially near it) —
  reuse `_components` to assert it during the acceptance run.
- **E. `_GEOM_BUILD_VERSION` bump** and the geometry cache key: the corridor
  build consumes the same inputs (`points`, `edges`, refrs, land), so the
  existing `_geom_hash` covers it; just bump the version constant so old cached
  meshes self-invalidate.

---

## Risk register

| Risk | Mitigation |
|---|---|
| Sparse mesh: NPCs path single-file, don't use room area | Accepted for Phase 1 (author). Phase 2 width-grow restores room coverage. |
| Pathgrid edge clips a wall → ribbon straddles wall | Accepted (principle 1); the fixed narrow width limits how far it protrudes. Phase 2 must not *widen* it through the wall. |
| Junction overlap creates non-manifold edges | `_make_manifold` (Step 5) resolves; keep the largest tris. |
| Node floats far above the floor (pit/upper storey) | `snap_down` clamps the drop to `SEED_SNAP_DOWN`; never teleports to a distant surface. |
| Door with no pathgrid edge through it → no Door Triangle → dead doorway | Step 4 skips only genuinely walled-off doors; author asserts doorways have pathgrid. Acceptance counts doors that got a Door Triangle vs. total; a shortfall is a real bug to chase. |
| Exterior sparse pathgrid → spiderweb over open terrain | Accepted for Phase 1; Phase 2 width-grow + terrain already in `walkable`. |
| Cross-cell connectivity | Unchanged — NAVI/NVMI + edge-link passes already handle it and consume `(verts,tris)`; Phase 1 only owes them border edges at the seam. |
