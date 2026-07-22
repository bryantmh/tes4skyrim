# CreationKit.exe NavMesh Generation — Static Analysis

Source: `C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\CreationKit.exe`
(47,954,944 bytes, CK 1.6.1130, MSVC x64). Read-only interoperability analysis —
nothing here is patched or redistributed.

## Is it encrypted?

**No.** Unlike `SkyrimSE.exe` (Steam DRM, `.text` entropy 8.00 — see
[project_skyrimse_exe_drm_packed]), CreationKit.exe is a plain unpacked binary:

| Section | VA | VSize | Entropy |
|---|---|---|---|
| `.text` | 0x00f5d000 | 0x01fc6200 (33 MB) | **5.53** |
| `.rdata` | 0x02f24000 | 0x008953c9 | 4.22 |
| `.data` | 0x037ba000 | 0x01ffa7f1 | 2.84 |
| `.pdata` | 0x057b5000 | 0x001d24a0 | 6.98 |

`.pdata` gives ~29k function boundaries, RTTI type names are intact (3,902
classes), and — critically — Bethesda shipped it with **assert strings carrying
original source paths** (`e:\_skyrimhd\code\gamesln\shared\pathfinding\navmesh\navmesh.cpp`),
plus a fully-named progress log for every generation pass. Static analysis is
easy; most of what follows was read straight out of strings + the settings
initializers.

Tools added for this: `tools/ck_settings_dump.py` (recovers compiled-in INI
defaults) and `tools/ck_strref.py` (indexes rip-relative references from `.text`
to a set of strings in one pass). `tools/skyrim_disasm.py` takes `--exe` and
works on the CK unchanged.

## The headline finding: there are THREE generators

The CK does not have one navmesh algorithm, it has three, and they are visible
as three separate string/INI families:

1. **`BGSRecastModule` / `NavGenMeshRecastImport@NavGenUtil`** — a genuine
   Mikko Mononen **Recast** integration. The `rcBuildCompactHeightfield`,
   `rcBuildDistanceField`, `rcBuildContours`, `rcBuildPolyMesh`,
   `rcMergePolyMeshes`, `filterSmallRegions`, `rcMarkReachableSpans` error
   strings are verbatim Recast, lightly renamed (`arCHF`, `arMesh`, `pData`
   — Bethesda's own Hungarian applied to Recast's locals). Config is the
   `[Recast]` INI section, which is exactly `rcConfig`.
2. **Bethesda's own geometry pipeline** — the `NavMeshGeneration:` pass log
   and the `[NavMeshGeneration]` INI section. This is *not* voxel-based; it is
   a **triangle-soup CSG pipeline** (cutting triangles, intersection passes,
   containment removal, T-junction repair). This is the legacy path that
   produced vanilla Skyrim's shipped navmeshes.
3. **Havok AI** (`hkaiNavMeshGenerationSettings`, `hkaiNavMeshCutter`,
   `hkaiNavMeshSimplificationUtilsSettings`, `hkaiCarver`) — linked in and used
   for runtime obstacle cutting (`BSNavmeshObstacleData`,
   `NavMeshObstacleManager`), not for authoring.

**Our generator is architecturally #1.** `tes5_import/navmesh/` is a Recast
reimplementation: `voxel.py` = heightfield + `filter_ledge_spans` /
`filter_low_hanging_obstacles` / `filter_walkable_low_height_spans` /
`erode_walkable` (Recast's four filters verbatim), `region.py` = region
building, `spanmesh.py` = contour → polygon → simplify. That was the right
call and is validated by this analysis. The improvements below are therefore
mostly about **parameters and missing passes**, not about a rewrite.

---

## Recovered parameters (compiled-in defaults)

Each CK setting is registered by a dynamic-initializer thunk:

```
sub  rsp, 0x28
movss xmm2, [rip+X]        ; the default value
lea  rdx, [rip+name]       ; "fRecastCellSize:Recast"
lea  rcx, [rip+object]     ; the Setting object
call Setting::ctor
```

so defaults are recoverable exactly. All values are **game units**.

### `[Recast]` — the voxel generator

| Setting | Default | `rcConfig` field | Our value (`navmesh/params.py`) |
|---|---|---|---|
| `fRecastCellSize` | **8.0** | `cs` | `CS = 16.0`, `CS_EXTERIOR = 32.0` |
| `fRecastCellHeight` | **8.0** | `ch` | `CH = 8.0` ✅ |
| `fRecastAgentHeight` | **128.0** | `walkableHeight` | `AGENT_HEIGHT = 128.0` ✅ |
| `fRecastAgentRadius` | **32.0** | `walkableRadius` | `AGENT_RADIUS = 24.0` |
| `fRecastAgentMaxClimb` | **32.0** | `walkableClimb` | `MAX_CLIMB = 34.0` ≈ ✅ |
| `fRecastAgentMaxSlope` | **45.0** | `walkableSlopeAngle` | `MAX_SLOPE_DEG = 46.0` ≈ ✅ |
| `fRecastEdgeMaxLen` | **512.0** | `maxEdgeLen` | `TRI_TARGET_EDGE = 128.0` |
| `fRecastEdgeMaxError` | **1.3** | `maxSimplificationError` | `MAX_SIMPLIFY_ERR = 12.0` |
| `uRecastRegionMinSize` | **50** | `minRegionArea` | `MIN_REGION_VOXELS = 8` |
| `uRecastRegionMergeSize` | **20** | `mergeRegionArea` | *(no equivalent)* |

> **UNITS TRAP — read before porting any of these.** Recast expresses
> `maxSimplificationError` in **VOXELS**, and `walkableHeight`/`walkableClimb`/
> `walkableRadius` in **voxel counts** too (`rcConfig` stores them as ints after
> dividing by `cs`/`ch`). Only `cs`, `ch` and the world-space extents are in
> world units. So `fRecastEdgeMaxError = 1.3` is **1.3 × cs = 10.4 game units**
> at the CK's `cs = 8`, *not* 1.3 units. Our `MAX_SIMPLIFY_ERR` is compared
> against `plane_dev`/`_seg_dist`, which are world-space, so the correct
> translation is **10.4** — almost exactly the 12.0 it was already set to.
> Taking the 1.3 literally is a ~7x tighter budget that stops the decimator
> removing the voxel staircase at all: measured on SageGlenHollow it took the
> mesh from 4,368 triangles to **12,890 (+195%)** with zero coverage gain.

Note Recast semantics: `minRegionArea`/`mergeRegionArea` are stored as
**voxel counts squared** in stock Recast (`rcConfig` docs), but Bethesda's
values (50/20) read as direct span counts, matching the `filterSmallRegions`
string being present unmodified.

### `[NavMeshOverlay]` — the render-window live preview

The overlay uses a *deliberately looser* config than generation, for speed:

| Setting | Default |
|---|---|
| `fVoxelWidth` | 8.0 |
| `fVoxelHeight` | 20.0 |
| `fAgentHeight` | *(not an immediate — computed)* |
| `fWalkableSlope` | **90.0** (i.e. disabled) |
| `fWalkableStepHeight` | 128.0 |
| `fEdgeMaxLen` | 4096.0 |
| `fEdgeMaxError` | 1.3 |
| `uOverlayColorPref` | 0x80808080 |

### `[NavMeshGeneration]` — the legacy triangle-soup pipeline

Note the `1`/`2` suffixes: the CK runs this pipeline as **two configurable
passes** with independent parameters. Both ship with identical defaults, so
stock behaviour is "run the same pass twice".

| Setting | Pass 1 | Pass 2 |
|---|---|---|
| `fWieldVertexDistance` | 2.5 | 2.5 |
| `fOverlapDistance` | 128.0 | 128.0 |
| `fMinIslandArea` | **150.0** | 150.0 |
| `fSimplificationLevel` | 2.5 | 2.5 |
| `fMaxWalkableAngle` | **45.0** | 45.0 |
| `fSimplifyEdgeSwapAngle` | **25.0** | 25.0 |
| `fShortestEdgeLength` | **10.0** | 10.0 |
| `fLongestEdge` | **512.0** | 512.0 |
| `fCornerSimplifyThreshold` | **0.9** | 0.9 |
| `fStepHeight` | **25.0** | 25.0 |
| `fMaxXYThreshold` | 8.0 | 8.0 |
| `bFinalizeNavMesh` | 0 | 0 |

Cell-boundary portal stitching:

| Setting | Default |
|---|---|
| `fCellPortalDistance` | **4.0** |
| `fCellPortalXYVertDistance` | **16.0** |
| `fCellPortalZVertDistance` | **64.0** |

Warning thresholds (drive the NavMesh Audit CSV):

| Setting | Default |
|---|---|
| `uNavmeshTriangleCountWarnThreshold` | **3500** (exterior) |
| `uNavmeshTriangleCountInteriorWarnThreshold` | **5000** |
| `bGlobalNavMeshCheck` | 0 |
| `bGlobalNavMeshCheckDeleteWarningTriangles` | 0 |

---

## The legacy pipeline's pass order

Recovered from the driver at `.text:0x01ff59d0` (progress strings are emitted
in execution order, and the string references are address-monotonic within the
function). This is the **full-cell generation** entry point:

```
 1. Importing Data                                  (collect collision geometry)
 2. Imported %u raw triangles, copying to local format
 3. Creating %u cutting triangles from land triangles
 4. Performing Intersection Pass on %u triangles against %u land triangles
 5. Adding Land %u triangles to navmesh
 6. Fixing Degenerates and T-Junctions
 7. Creating cutting triangles from %u havok triangles
 8. Performing Intersection Pass on %u triangles against %u havok triangles
 9. Removing Contained Triangles on %u triangles against %u havok triangles
10. Performing Overlap Removal Pass on %u triangles
11. Performing Intersection Pass on %u triangles against %u projected
12. Fixing T-Junctions on %u triangles
13. Removing Overlapped Triangles on %u triangles
14. Connecting Stairs on %u triangles
15. Performing Thin Area Removal on %u triangles
16. Performing Flood Fill Removal on %u triangles
17. Performing Simplification on %u triangles
18. Cutting long edges
19. Removing Unwalkable triangles on %d triangles
20. Removing small outside triangles on %u triangles
21. Clipping Cell boundries on %u triangles
22. Finding Cover Edges
23. Finalizing NavMesh with %u triangles
24. Complete %u triangles and %u vertices
```

Stair connection is its own sub-pipeline (`.text:0x02008f78`):

```
Building List of Potential Stair Edges on %d Outside Edges
Removing Duplicate Potential Stair Edges on %d Edges
Connecting Stairs on %d Edges
```

Key structural observations:

- **Land and Havok are separate inputs, handled in that order.** Terrain is
  laid down first as a base sheet (steps 3–5), then Havok collision is used as
  a *cutter* against it (7–9). We do the same thing implicitly by rasterizing
  both into one heightfield, which is fine for a voxel approach.
- **"Removing Contained Triangles ... against havok triangles"** — an explicit
  pass that deletes navmesh area *inside* solid collision. Our equivalent is
  the ledge/headroom filters, which is weaker (see below).
- **Stair connection is a post-mesh edge operation**, not a voxel operation.
  This is the single biggest architectural difference from our pipeline.
- **"Clipping Cell boundries"** happens near the very end, after simplification
  — so vertices land exactly on the cell border and neighbouring cells' meshes
  can be portal-stitched by coordinate match.
- **"Finding Cover Edges"** is a real generation output we do not produce at all.

---

## Where our generator differs, and what to do about it

Ordered by expected impact on NPC pathing.

### 1. `fRecastEdgeMaxError` — NOT a defect (resolved, units trap)

**Superseded.** The original reading of this item was wrong and is kept here as
a warning. `fRecastEdgeMaxError = 1.3` is in **voxels**, so at the CK's `cs = 8`
it equals **10.4 game units** — our `MAX_SIMPLIFY_ERR = 12.0` was already
correct to within 15%. Applied as a literal 1.5, it inflated SageGlenHollow
from 4,368 to 12,890 triangles (+195%) and introduced 78 micro-triangles in
ICArcaneUniversitySpellmaker, with no coverage improvement anywhere.

Now set to **10.4**, the honest translation at our matching `cs = 8`.

### 2. `fRecastCellSize` is 8.0, ours is 16.0/32.0 — HIGH

The CK voxelizes at **8u in both XY and Z**, uniformly, for interiors *and*
exteriors. We use 16u interior / 32u exterior. Since `AGENT_RADIUS` is 32u
(CK) and erosion is measured in whole voxels, a 32u cell size means erosion
quantizes to a single voxel — a ±32u error in wall standoff. That is the
mechanism behind blobby exterior meshes.

The cost is real (4x columns at 16→8, 16x at 32→8), but `CELL_TIME_BUDGET` and
`MAX_GRID_DIM = 512` already exist to bound it. Recommend: interior 8.0,
exterior 16.0, and let `MAX_GRID_DIM` coarsen the pathological cells rather
than pre-coarsening every cell.

### 3. `fRecastAgentRadius` is 32.0, ours is 24.0 — MEDIUM

We under-erode by 8u, so our mesh hugs walls closer than the CK's. Combined
with the 12u simplify error this compounds: triangles that reach *into*
geometry. Set `AGENT_RADIUS = 32.0` to match. (Vanilla NPC pathing assumes a
32u standoff; a mesh generated at 24u will have NPCs clipping corners and
snagging on door frames.)

### 4. `fRecastEdgeMaxLen` is 512.0, ours is 128.0 — MEDIUM

We force edges 4x shorter than the CK does, i.e. we emit roughly an order of
magnitude more triangles per unit area than vanilla for no pathing benefit.
Given the warn thresholds are 3500 (exterior) / 5000 (interior) triangles per
cell, and `fLongestEdge` in the legacy pipeline is *also* 512.0, 512 is clearly
the intended target. Raising `TRI_TARGET_EDGE` to ~512 buys back most of the
budget that items 1 and 2 spend.

Corroborating: `uNavmeshTriangleCountWarnThreshold = 3500` is a useful new
audit check for us — any cell we emit above 3500/5000 tris is one the CK would
have flagged.

### 5. No explicit stair-connection pass — HIGH

The CK builds an explicit list of **outside (boundary) edges**, dedupes them,
and connects stairs across them as a mesh-level operation, twice (steps 14 and
the dedicated sub-pipeline). We rely entirely on `MAX_CLIMB` during voxel
filtering, which only connects steps that are *vertically adjacent within one
climb* in the *same column neighbourhood*. Oblivion staircases with open risers,
or treads separated by more than one voxel column, come out as disconnected
islands — which we currently paper over with `stamp_pathgrid`'s unconditional
band (`PGRD_BAND`, and the comment there explicitly notes staircases failing).

Recommended: after `build_mesh`, add a boundary-edge stair pass — for each pair
of boundary edges within `fCellPortalXYVertDistance` (16u) in XY and
`fStepHeight` (25u) in Z, and roughly antiparallel, weld/bridge them. This is
a principled replacement for the pathgrid stamp hack, and would let
`PGRD_BAND` shrink.

### 6. No cover-edge generation — MEDIUM (feature gap)

`NavMeshGeneration: Finding Cover Edges` is a generation pass we do not have.
The engine consumes it: `Open Edge No Cover` / `Ledge Cover` / `NavMesh Cover`
UI strings, plus `fCombatCoverEdgeOffsetDistance` and
`fCombatCoverLedgeOffsetDistance` gameplay settings. Cover flags live in the
NVNM edge data. Without them, converted cells give NPCs **no combat cover**, so
archers and mages behave incorrectly in converted interiors.

This is additive and low-risk: classify each boundary edge as cover based on
whether blocking collision rises above it, and set the flag.

### 7. No water / preferred-path triangle flags — MEDIUM (feature gap)

`HKFunc_FlagWaterTris` is a CK operation, and the engine's pathing costs are
driven by it: `fWaterTriangleCostMultiplier`,
`fWaterTriangleCrossingCostMultiplier`, `fPreferredTriangleMultiplier`,
`fAvoidPreferredTriangleMultiplier`,
`fAvoidPreferredTriangleCrossingMultiplier`, plus
`TrianglePathWaterAndLedgeSplitter` and `PathSmootherRayCastUsePreferredTris`.

We emit neither flag. Consequences: NPCs treat water as ordinary ground (they
will wade across rivers instead of using bridges), and there is no way to
express "prefer the road". Water flagging is mechanical — we already know each
cell's water height (`XCLW`/worldspace water level); flag every triangle whose
centroid is below it.

### 8. Cell-boundary portal tolerances — LOW, but verify

The CK stitches cross-cell portals with `fCellPortalDistance = 4.0`,
`fCellPortalXYVertDistance = 16.0`, `fCellPortalZVertDistance = 64.0`. Worth
checking `navm_edge_links.py` against these — particularly the very loose 64u
Z tolerance, which suggests neighbouring cells' terrain meshes are *not*
expected to agree in Z, and we may be rejecting valid portals with a tighter
gate. (See [project_navmesh_edge_links] — we found 0/5825 exterior navmeshes
had edge links; tolerance is a plausible contributor.)

### 9. `fMinIslandArea = 150.0` vs our `MIN_ISLAND_TRIS = 5` — DONE

The CK prunes islands by **area** (150 sq units), we pruned by **triangle
count**. With item 4 applied (512u edges) a 5-triangle island can be an entire
room, so a count means something different every time the tessellation changes.
Now `MIN_ISLAND_AREA = 150.0`, applied in `build._prune_islands` and mirrored in
`tools/navmesh_audit.py`'s TINY census.

Note `MIN_REGION_VOXELS` (the `uRecastRegionMinSize` analogue) is **unused** and
documented as such: our region pass keeps regions by PATHGRID SEEDING rather
than by size, which is a stronger signal than area.

### 10. Adopt the CK's own validation rules — LOW effort, HIGH diagnostic value

`NavMesh::CheckNavMesh` (strings at `0x02142410`–`0x02142f80`) enumerates
exactly what Bethesda considers a malformed navmesh. Every one of these is a
check we can run in `tools/navmesh_audit.py`:

- mismatched edge connection (A→B but B↛A)
- bad vertex index / triangle index / extra-info index
- **downfacing normal** (CK flips the triangle)
- degenerate triangle (two verts share an index) — checked for all three pairs
- two edges of one triangle pointing at the same neighbour
- **linked triangles with opposite normals**
- portal to a navmesh in a different worldspace
- portal to a nonexistent navmesh/navmesh-info, or to an out-of-range triangle
- `NavmeshInfo` referring to a missing form or a non-navmesh form
- "Navmesh has more vertices than should be possible"

Plus the finalize-time check we should definitely mirror:
`Finalize NavMesh: Teleport marker for door %s (%08x) in cell %s (%08x) is not
sitting on a navmesh` — a direct, cheap test for the door-triangle failures
we've chased before.

---

## Order of work

1. ~~Params~~ **DONE (2026-07-22)** — see results below.
2. ~~Port `CheckNavMesh`'s rule set~~ **DONE** — `tools/navmesh_check.py`.
3. Boundary-edge stair connection (item 5); then reduce `PGRD_BAND` and see if
   the mesh survives on its own merit.
4. Water triangle flags (item 7) — mechanical, immediate AI benefit.
5. Cover edges (item 6).
6. Portal tolerance review against 4/16/64 (item 8).

## Results — params pass (2026-07-22)

Final values in `tes5_import/navmesh/params.py`:

| Param | Was | Now | Source |
|---|---|---|---|
| `CS` | 16.0 | **8.0** | `fRecastCellSize` |
| `CS_EXTERIOR` | 32.0 | **16.0** | one octave coarser (whole-worldspace runs) |
| `AGENT_RADIUS` | 24.0 | **32.0** | `fRecastAgentRadius` |
| `TRI_TARGET_EDGE` | 128.0 | **512.0** | `fRecastEdgeMaxLen` (now ABSOLUTE, was cs-scaled) |
| `MAX_SIMPLIFY_ERR` | 12.0 | **10.4** | `fRecastEdgeMaxError` × cs (units trap above) |
| `MIN_ISLAND_TRIS = 5` | — | **`MIN_ISLAND_AREA = 150.0`** | `fMinIslandArea` |

Measured on 12 interiors (`python tools/navmesh_audit.py --interiors 12`),
old params vs new:

| Cell | Tris old → new | Uncovered |
|---|---|---|
| SageGlenHollow | 4368 → **2709** (-38%) | 0.0% → 0.0% |
| Elenglynn | 2584 → **1777** (-31%) | 0.1% → 0.1% |
| GoblinJimsCave | 2135 → **1647** (-23%) | 0.0% → 0.0% |
| KvatchChapelUndercroft | 674 → **589** (-13%) | 0.0% → 0.0% |
| ICArcaneUniversitySpellmaker | 160 → **136** (-15%) | 0.6% → 1.1% |

Population: mean uncovered **0.1%**, 0 steep, 0 wrong-floor, 0 tiny islands,
0.0% slivers, 7 micro-triangles (was 373 under the mis-parameterized run),
25.9 cpu-s for 12 cells. The one coverage regression
(ICArcaneUniversitySpellmaker, +0.5pp) traces to `TRI_TARGET_EDGE = 512` alone
and is a single large triangle cutting a corner in a small room — the accepted
cost of matching vanilla's triangle scale.

**Method note:** every parameter was A/B'd one at a time
(`temp/ab_sweep.py` (throwaway)) rather than changed as a block. That is what caught the
`MAX_SIMPLIFY_ERR` units trap — as a block the change looked like a modest
regression, but isolated it was a +195% triangle explosion masked by the
-40% from `TRI_TARGET_EDGE`.

## Results — CheckNavMesh port (2026-07-22)

`tools/navmesh_check.py` implements all 15 rules. Validated against ground
truth before use — vanilla must come out near-clean, or the checker is wrong:

| File | Navmeshes | Triangles | Findings |
|---|---|---|---|
| Skyrim.esm | 15,966 | 2,670,354 | **225** (0.008%) |
| Dawnguard.esm | 1,863 | 254,236 | **46** |
| our Oblivion.esm (pre-params) | 8,156 | 14,301,983 | 2,637 |

Three parser bugs were found and fixed **by that validation**, each of which
would have made the tool useless:

1. **Edge-link bits.** When a triangle's flags carry `0x0001/0x0002/0x0004`,
   that edge's S16 is an index into the EDGE-LINK array, not a neighbouring
   triangle. Misreading it scored vanilla at 188,785 ASYMMETRIC_EDGE + 3,030
   DUP_EDGE_TARGET + 76 BAD_TRI_INDEX — all false.
2. **Master-owned portals.** A plugin's navmeshes legitimately portal into its
   masters'; judging those reported 3,206 false BAD_PORTAL_MESH on Dawnguard.
   Now scoped by the file's own FormID index (`--all-portals` to override).
3. **The door rule.** "Teleport marker is not sitting on a navmesh" cannot be
   checked geometrically from record data — the engine drops the arriving actor
   onto the mesh rather than requiring the marker to be embedded in it, so a
   position test flagged 1,178 of 1,722 vanilla markers (median vertical gap
   338u, p90 5,089u). Reformulated as **"every teleport door must own a Door
   Triangle"**, which is self-validating: Skyrim.esm's NVNM Door Triangle
   arrays name exactly 1,703 door REFRs and all 1,703 are teleport doors.

The residual vanilla findings were hand-verified as genuine Bethesda defects,
not parser artifacts — e.g. navmesh `00094D76` tris 63/64 share verts {61,64}
but tri 64's edges are (231,248,-1) and never name 63 (a real one-way link);
navmesh `0010F9A4` portals to triangle 422 of `0010F989`, which has 416.

**Our output's headline number:** 14.3M triangles across 8,156 navmeshes =
~1,753 tris/mesh, against vanilla's 167 — a **10.5x** over-tessellation. That
measurement predates the params pass; re-run after a full conversion.
`OPPOSITE_NORMALS` (1,506) and `DOWNFACING` (937) are real generator defects
worth attacking next, and neither appears in vanilla at anything like that rate.

## Reference: source paths in the binary

Useful for orienting future analysis:

```
e:\_skyrimhd\code\gamesln\shared\pathfinding\navmesh\navmesh.cpp
e:\_skyrimhd\code\gamesln\shared\pathfinding\navmesh\navmesharray.h
e:\_skyrimhd\code\gamesln\bspathfinding\bsnavmesh.inl
e:\_skyrimhd\code\gamesln\bspathfinding\bsnavmeshtriangle.h
e:\_skyrimhd\code\gamesln\construction set\misc\navmesheditmodule.cpp
e:\_skyrimhd\code\gamesln\construction set\misc\bgsrenderwindownavmesheditmodule.cpp
```
