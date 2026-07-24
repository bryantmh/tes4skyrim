# World / LAND / PGRD→NAVM Conversion Notes

Linked from [CLAUDE.md](../CLAUDE.md). Covers pathgrid→navmesh conversion and
LAND/landscape-texture record structure. For terrain LOD generation see
[nif_conversion_notes.md](nif_conversion_notes.md#terrainlodland-adjacent-asset-notes).

## PGRD → NAVM/NAVI Conversion (PathGrid → NavMesh)

TES4 PGRD (per-cell pathgrid of nodes+edges) is converted to a TES5 NAVM per
cell PLUS a single top-level NAVI (Navmesh Info Map). Implemented in
`tes5_import/pgrd_to_navm.py` (`convert_PGRD`) and `tes5_import/navi_builder.py`
(`build_navi_record`), wired in `import_main.py` Phase 4 for both interior
(`_build_cell_groups`) and exterior (`_build_world_groups`) cells.

- **NAVI IS MANDATORY**: Skyrim only uses a NAVM for pathfinding when it is also
  indexed in a top-level NAVI record. NAVM records alone are ignored. NAVI goes
  in the top-level group order immediately BEFORE CELL (verified vs xEdit
  `wbAddGroupOrder`, and added to `writer._group_order`).
- **Algorithm (collision-voxel, rewritten 2026-07-12 — replaces the pathgrid-
  buffering approach that could not represent walls; see
  [navmesh_rebuild_plan.md](navmesh_rebuild_plan.md))**: VOXELIZE the real Havok
  collision geometry of everything placed in the cell. The collision mesh is
  exactly what the engine uses to decide what an NPC stands on / is blocked by,
  so we use it directly instead of guessing from the pathgrid. Modules live in
  `tes5_import/navmesh/`:
  1. `world.gather_cell_geometry`: for every REFR, transform its base mesh's
     cached collision soup by the ref's FULL rotation + scale + position into
     cell space; split by surface normal into WALKABLE (|nz|≥cos46°) and
     BLOCKING. Exteriors also emit the LAND height field as walkable terrain.
  2. `voxel.build_heightfield` + `apply_filters`: rasterize into a column grid of
     Z-spans (CS=16u interior / 32u exterior, CH=8u), then Recast filters —
     low-hanging-obstacle merge, ledge (MAX_CLIMB=34u), min-headroom
     (AGENT_HEIGHT=128u) — plus agent-radius erosion (AGENT_RADIUS=24u) for a
     correct standoff from walls.
  2b. `voxel.stamp_pathgrid` — **the pathgrid goes in HERE, before any filter.**
     A band of PGRD_BAND (24u) either side of every pathgrid line is stamped as
     PROTECTED walkable spans, snapping onto real collision at that height where
     it exists and synthesizing a span where it does not. Protected spans are
     immune to every later stage: ledge filter, headroom filter, region cull and
     agent erosion all skip them. The stamp yields to NOTHING (an early version
     skipped columns with blocking collision, which silently refused to stamp
     staircases — a stair's own faces are steep, hence "blocking" — and left the
     storeys of a house as disconnected islands).
     **The sweep FOLLOWS THE WALKED SURFACE, not the edge's chord (2026-07-17).**
     Each step predicts `z + chord_slope` then locks onto the walkable surface
     nearest that prediction (window: PGRD_SNAP_Z=48 down, MAX_CLIMB up), so the
     ribbon walks down through gullies and up staircases like the NPC would; the
     chord is only the pacing fallback where geometry is absent. Snapping each
     sample independently against the raw chord had band columns alternating
     between terrain and chord height — a jagged lattice of near-vertical
     triangles down every hillside. Three self-contamination guards matter:
     (1) the follow ignores spans the sweep itself synthesized (`synth_tops`) —
     locking onto its own tail made climbing ribbons lag their chord and arrive
     a storey low (100+ broken edges in geometry-less cave cells); (2) a
     re-stamp keeps whichever pgz is CLOSER to the current sample — re-snapping
     onto a synth-merged span's walkable top ratcheted the mesh up furniture
     one MAX_CLIMB per pass; (3) post-sweep, a synthesized span within
     AGENT_HEIGHT of a SNAPPED protected span in the same column is dropped
     (two standable layers can't be that close; the chord fabricated air over
     real treads). Synth-vs-synth conflicts are kept — switchback flights both
     crossing a floor-less column are each load-bearing.
  3. `region.build_regions` + `seed_regions` + `keep_regions`: flood-fill spans
     into connected regions and KEEP only those a pathgrid node vouches for.
     Tabletops/roofs/ledges hold no node and are dropped. `keep_pathgrid_heights`
     then drops any span no pathgrid sample vouches for at its height — this is
     what stops navmesh appearing on the CEILING of a room a staircase passes over.
  4. `spanmesh.build_mesh`: mesh the SPAN GRAPH directly (see below). Then
     `_decimate` collapses edges, bounded by BOTH a plane error (MAX_SIMPLIFY_ERR)
     and a triangle-QUALITY test (aspect ratio ≤6, edge ≤TRI_TARGET_EDGE).
  5. `build.build_navmesh`: orchestrates the above, then `_drop_steep_triangles`
     (MAX_SLOPE_DEG is a HARD ceiling with no exceptions), `_cull_boundary_flaps`
     and `_prune_islands` (see below). Then this module computes adjacency,
     water flags, door triangles.

### Island pruning / boundary cleanup (2026-07-15 quality pass)

- **`_prune_islands` keep rules**: a disconnected component survives iff it has
  ≥ MIN_ISLAND_TRIS(5) triangles AND (it is ANCHORED — reaches a teleport door
  within ISLAND_DOOR_RADIUS, or in an exterior comes within ISLAND_EDGE_MARGIN
  of the cell border ("runs over into the next cell") — OR it is vouched by a
  pathgrid node and not merely SHADOWING a kept component in Z). The size gate
  applies to anchored components too: a 2-triangle doorstep scrap disconnected
  from the room is worse than no mesh at the door — it steals the Door Triangle
  from the main mesh and teleports NPCs onto an island they can't leave.
- **`_cull_boundary_flaps`** ("delete edge triangles that aren't up to snuff"):
  outline triangles with ≤1 neighbour (protruding flaps — provably never a
  bridge, so removal cannot disconnect anything) below EAR_MIN_AREA are deleted,
  EAR_ROUNDS(2) rounds. Exemption must be DISTANCE to the densified pathgrid
  line (EAR_PGRD_RADIUS), not node containment: containment-only let the cull
  eat ribbon ends and narrow cave ledges (2 wrong-floor nodes + broken edges in
  XPGloomstonePassage02 until fixed). Runs BEFORE `_prune_islands` so the size
  gate judges final component sizes.

### Door threshold quads (Door Triangles done right)

`spanmesh._stamp_door_quads`: every door REFR (teleport AND interior) gets an
exact oriented quad (DOOR_QUAD_HALF_WIDTH 48 × HALF_DEPTH 32, rotated by the
door's RotZ) stamped into the RAW voxel mesh — vertices inside the rect snap to
its 4 corners, which are then PINNED through decimation. Must happen
pre-decimation: afterwards triangles are bigger than the rect and there is
nothing to snap. `pgrd_to_navm._build_door_links` then links the triangle
CONTAINING the door point at the door's height (fallback: old nearest-centroid
cost). Result: two clean triangles precisely straddling every threshold.

#### 🔴 Snapping FOLDS triangles — restore winding (found 2026-07-22)

Snapping pulls several distinct vertices onto the 4 rect corners. A triangle
STRADDLING the rect boundary can have two of its corners pulled to *different*
corners, which **reverses its winding** — the remap preserved the original index
order and never rechecked. This was the ONLY source of downfacing triangles in
the entire generator, and (because a folded triangle is inverted relative to its
neighbours) the dominant source of CK `OPPOSITE_NORMALS` too.

Measured with `temp/wind_probe3.py`: the raw mesh is always clean
(`pre_stamp=0`) and the stamp injected 6 / 14 / 12 downfacing triangles into
XPAichan01 / SancreTor03 / ArkvedsTower04. Classification proved **zero** came
from the stamped quads themselves (that CCW emission is correct) — all were
pre-existing, previously up-facing triangles.

Fix: record each triangle's XY orientation BEFORE remapping and swap two indices
if the remap reversed it. `|nz|/2` is the XY-projected area, so the sign of the
2D cross product is the facing test. Triangle counts are unchanged (nothing is
dropped) — 943 DOWNFACING and most of 1,516 OPPOSITE_NORMALS went to zero.

Two smaller sources found alongside it:
- **Zero-XY-footprint slivers.** A triangle in an exactly vertical plane covers
  no ground (XY area 0.0000, `nz == 0` so invisible to a `nz < 0` test), yet a
  coplanar pair reads as OPPOSITE_NORMALS because their normals are antiparallel
  in XY. `_drop_steep_triangles` kept them: they are steep but their z-span is
  riser-sized, well under the `2.5 * MAX_CLIMB` gate. Now dropped by
  `MIN_XY_FOOTPRINT` (1.0u², far below one voxel quad, so only the genuinely
  degenerate-in-plan case goes). Example: Ondo tris 1445/1447, all six vertices
  at y=48.0 exactly.
- **Decimation drift.** The C++ collapse/flip/smooth guards were only
  RELATIVE (`new · old > 0`), so a triangle could rotate up to 90° per move and
  walk from up-facing to down-facing across passes without any single move
  tripping the guard. Added an absolute `nz >= 0` invariant to all three passes
  in `native/src/decimate.cpp` (rebuild with `python native/build.py`).

Verified on all 16 worst-offending cells from the shipped ESM (10 interior +
6 exterior): every one now reports CLEAN under `tools/navmesh_check.py`'s rules,
with coverage/steep/island metrics unchanged.

### Exterior coverage (the "discontinuities with no obstacles" fixes)

- **Reach**: `PGRD_XY_REACH_EXTERIOR` (8192) replaces the interior 384u gate
  outdoors — vanilla exterior navmeshes cover essentially the whole cell, and
  the tight gate carved open terrain into blobs around the road pathgrid.
  Geodesic flooding still can't climb >MAX_CLIMB per step or reach roofs.
- **Ledge spread test scales with cs**: `filter_ledge_spans`' steep-slope test
  `(max_drop - min_drop) > lim` must use `lim = max(MAX_CLIMB,
  2*cs*tan(MAX_SLOPE_DEG))`. With raw MAX_CLIMB at CS_EXTERIOR=32 it un-walked
  every hillside steeper than ~28° (2·32·tan28°≈34) — the mystery holes in open
  terrain. At CS=16 the scaled value equals MAX_CLIMB, so interiors unchanged.
- **Cell borders**: a neighbour column outside the exterior cell's LAND is
  unknown terrain (it continues in the next cell), NOT a cliff — treating it as
  a drop un-walked the border row and left a 2-column gap on every cell seam
  (`ext_rect` threading through `apply_filters`).

### Geometry cache (the import-time fix)

`pgrd_to_navm` caches built `(verts, tris)` per cell in
`export/<plugin>/navmesh_geom_cache/*.pkl` (float32/int32 arrays), keyed by a
sha1 of exactly what geometry consumes: pathgrid points/edges, per-REFR
(name, resolved model key, pos/rot/scale, XTEL), doors, LAND VHGT, origin, and
a TAG hashing the navmesh sources + collision-cache identity
(`import_main._navmesh_geom_cache`). Any code/param edit self-invalidates —
no version constant to forget (deliberate: stale caches must never explain a
bug). Warm hit ≈ 0.03s vs seconds; fresh builds round verts to float32 first so
cache hits are byte-identical to cold builds. FormID-dependent parts (NVNM
parent, door links, ONAM, water flags) are recomputed every run so load-order
changes can't bake in.

### Mesh the SPAN GRAPH, never contours (the decisive fix)

A contour is a **height map** — one Z per (cx,cy) column — and a building is not.
A staircase carries an NPC *over* the room below it, and a house stacks two
storeys in the same columns. The old contour mesher tried to slice the world into
height-map "layers" and contour each; every defect came from the seams:

- a staircase peeled into 5 layers, each contoured alone, each an island joined to
  the next only at a triangle **corner** (an NPC cannot cross that);
- a layer boundary falling between two floors let the triangulator bridge them —
  a wall of near-vertical triangles "connecting" storey 1 to storey 2;
- a short pathgrid stub became its own layer and was culled for being small,
  leaving a pathgrid line with **no navmesh under it**.

Tuning the slicer traded these defects for one another indefinitely. `spanmesh.py`
instead meshes the span graph: the unit is a **span**, not a column
(`node=(cx,cy,span_index)`, `adjacent = neighbouring column && |Δtop| ≤ MAX_CLIMB`),
one quad per span, and **adjacent spans share corner vertices**. Connectivity is
therefore structural — nothing to stitch, weld or repair — and two spans a storey
apart are simply never adjacent, so a cross-floor triangle is *unrepresentable*.
Result over 150 interior cells: **0 wrong-floor, 0 steep, 0.9% of pathgrid length
uncovered** (was 2.5% uncovered / 2452 broken pathgrid edges with contours).

- **Quality invariants** (`tools/navmesh_audit.py --interiors N` sweeps many cells
  in parallel; `tools/navmesh_diag.py <cell>` for one). The metric that matters is
  **BROKEN PATHGRID EDGES** — an edge whose two ends land on navmesh an NPC cannot
  cross between. A raw component count is NOT a bug metric: a cave with six
  chambers this cell's pathgrid never links is legitimately six components.
  Erosion uses a EUCLIDEAN distance transform (scipy `distance_transform_edt`),
  NOT a chamfer — a chamfer overestimates diagonal distance ~1.7x and left wide
  dead zones around obstacles.
- **Decimation must bound triangle QUALITY, not just planarity.** A vertex in the
  middle of a flat floor is coplanar with all its neighbours, so a purely planar
  collapse test drags it clear across the room and the floor degenerates into a
  fan of long thin slivers. Bound the aspect ratio and the edge length too.
  **And the EDGE RATIO (2026-07-17)**: aspect (`longest²/4·area`) alone passes a
  16u voxel edge with two ~100u edges (aspect ≈3, healthy area) — the "one side
  way shorter than the others" needles radiating from wall corners.
  `MAX_EDGE_RATIO` (4) bounds `longest/shortest` on every move, non-worsening
  (a move that improves an existing needle is still allowed, else voxel-scale
  needles freeze in place). The needles' SEED was outline notches: a boundary
  vertex whose boundary edge is shorter than ~1 cell is quantization noise, so
  it may absorb up to `0.9*cs` of outline error instead of MAX_SIMPLIFY_ERR
  (the true wall is within half a cell of either position). Together: RATIO
  defects 113-281/cell → 0 across the test set, and 20-40% fewer triangles.
- **Obstruction is decided in WORLD SPACE, never per-mesh.** An object obstructs
  iff it rises more than MAX_CLIMB above the floor beneath it — so rugs/pillows
  are walked over, tables/barrels are routed around, with NO size gate or rug
  list. Collision meshes are ORIGIN-CENTERED, so any per-mesh height rule is
  meaningless (a table's local extent says nothing about how high it stands).
- **Collision cache**: `asset_convert/collision_extract.py` reads the CONVERTED
  `output/.../meshes/tes4/**.nif` (collision is root-mounted there; the CMS is a
  flat triangle soup — no NiNode-transform walk needed). `scan_collision` →
  `export/<plugin>/collision_cache.bin` (binary, ~15MB, ~2 min one-time).
  Scales: CMS ×70, primitives (box/convex/capsule/sphere) ×10 — both measured
  exactly. Layer gate keeps only OL_STATIC/ANIM_STATIC/TERRAIN/GROUND/STAIRS.
- **REFR rotation is the TRANSPOSE** of the naive Rz@Ry@Rx product (the engine
  inverse-applies the stored rotation). The old code applied only RotZ and
  mis-oriented every ramp; the non-transposed full matrix put Anvil FG's floor
  shell ~180° backwards from its furniture. `world._rot_matrix`.
- **Door handling** (`_collect_doors`, `_build_door_links`): a door REFR is
  teleport (`XTEL.Door`) or interior-only (base in the DOOR set). BOTH get a Door
  Triangle linking the tri straddling the threshold line. The doorway is choked
  naturally now by the door frame's own collision — no jamb hack. Door CRC
  "PathingDoor" = `0xE48B73F3`. **Limitation**: cross-cell Portal Edge Links are
  not computed.
- **Base-model index**: `_build_base_model_index(by_type)` in import_main maps
  raw low-24 base FormID → `tes4/...nif` key, only for blocking base types. REFR
  exports position as `PosX/PosY/PosZ` + `RotX/RotY/RotZ` + `XSCL.Scale`, base as
  `NAME`.
- **Triangle flags** (wbDefinitionsTES5.pas): every generated tri sets
  `0x0800 Found`; water tris add `0x0200`, door-linked tris add `0x0400`. No Edge
  Links, empty Cover Triangles.
- **LAND VHGT decode** (`world.decode_vhgt`): offset float + 33×33 SIGNED int8
  gradients; BOTH the offset and the accumulated deltas scale by 8:
  `(cumsum(deltas) + offset) * 8`. The old code did `offset/8` in and `*8` out,
  which annihilated the offset and put exterior terrain ~16,700u below its own
  REFRs (Tamriel 47,6: terrain 829..3213 vs objects 18288..19776). This was the
  dominant coverage bug (pathgrid-on-floor 32%→92%).
- **Iteration tools**: `python tools/navmesh_preview.py --cell <FormID_or_EditorID>`
  renders the generated navmesh (green) OVER the collision layer — walkable dim,
  BLOCKING/walls RED — plus pathgrid and door markers (cyan threshold lines;
  white core = teleport door). `--focus X,Y --span N` zooms a world-coord
  window; `--ids` labels triangle indices + vertex heights; `--quality`
  colours steep triangles red and needles magenta. Exterior cells can be
  addressed as `--cell grid:X:Y` (colon form survives comma-list splitting;
  Windows filenames can't hold `:` so outputs sanitize it).
  `tools/navmesh_tri_check.py --cell A,B,...` checks EVERY triangle of a
  cell's mesh (slope/zspan/edge-ratio/aspect/area + JUT/SINK = signed distance
  off the real collision surface at its own XY) and lists offenders — the way
  the furniture-hoist and needle defects were found and verified fixed.
  `tools/navmesh_probe.py --cell X` reports pathgrid-on-floor coverage and Z
  error; `--probe X,Y` dumps nearby REFRs/pathgrid plus the span column
  raw/stamped/filtered — the ground-truth view of any one spot.
  `tools/navmesh_audit.py --interiors N --exteriors M` sweeps both cell kinds
  and reports UNCOV%/BROKEN/STEEP/FLOOR/ISL/TINY/SLIV%/MICRO per cell (UNCOV
  measures the EDGE's z-range, not the chord — the generator follows the
  surface, and a long cave edge's chord cuts open air two storeys up).
  `tools/navmesh_profile.py --cell X` cProfiles one cell's build (how the
  shadowed()/plane_err hotspots were found).
- **NVNM binary layout** (validated byte-exact against Skyrim.esm via
  `tools/navmesh_dump.py`): all arrays use U32 count prefixes; CRC of
  "PathingCell" = `0xA5E9A03C`; parent union decided by (Parent Worldspace==0)
  → interior = FormID Parent Cell, exterior = `S16 Grid Y` then `S16 Grid X`;
  `Max X/Y Distance` = bbox span / divisor; NavMeshGrid = divisor² arrays each
  `U32 count + count×S16`. Door Triangle struct is **10 bytes** (S16+U32+FormID),
  NOT 12. NAVM record is written with the Compressed flag (0x00040000).
- **NVMI (in NAVI)**: validated byte-exact (57 bytes) vs Skyrim.esm NAVI
  0x00012FB4: `FormID, U32 Category(0=Edited), 3×float centroid, 4B PrefMerge,
  U32 EdgeLink count, U32 PrefEdgeLink count, U32 DoorLink count, U8 IsIsland,
  [island union empty when 0], PathingCell(U32 CRC, FormID WS, parent union)`.
  We emit 0 edge/door links (can't compute cross-navmesh portals from PGRD).
  NAVI has NO EDID; order is `NVER(=12), NVMI…, NVPP(empty: two 0 counts)`.
- **Exterior PGRD/REFR point coords are WORLD coords** (not cell-local) → LAND
  origin = `grid_x*4096, grid_y*4096`.
- **Dependencies**: `numpy` + `scipy` (Delaunay); `mapbox_earcut` used when
  present (fallback ear-clipper otherwise). `shapely` is no longer needed.
- **Performance**: geometry is cached across runs (see Geometry cache above),
  so repeat imports pay ~ms per cell. Cold builds: the 2026-07-15 pass cut
  per-cell CPU ~33% on a 65-cell mix (Wendir02 13.6s→6.1s) by vectorizing
  `_prune_islands.shadowed` (was 45% of the build) and caching per-vertex
  planes in `_collapse_pass` (`vertex_planes`/`plane_dev` with early-out —
  the old code recomputed a full `_tri_shape` per incident triangle per
  collapse candidate).
- **Tests**: `tests/test_pgrd_navm.py` (19 tests: region flood-fill (flat floor,
  two-storey separation, staircase), wall-doesn't-swallow-floor, rug walked over
  vs table routed around, walls contain the mesh, contour orientation,
  triangulation area/holes, VHGT offset, NVNM/NAVI layout).
- **Reusable tool**: `python tools/navmesh_dump.py <esm> [--navi|--navm]
  [--nvnm-decode] [--max N]` — decompresses + decodes real NAVI/NAVM/NVNM for
  format verification (this is how the layout was validated against Skyrim.esm).

### 🔴 Edge Links are MISSING — cross-cell pathing is dead (found 2026-07-20)

`_pack_nvnm` hard-codes the Edge Links count to 0 ("cross-cell links can't be
resolved from PGRD alone"). Measured against Skyrim.esm:

| | exterior NAVM | with edge links | total edge links |
|---|---|---|---|
| VANILLA | 14,440 | **12,145 (84%)** | **194,744** (Portal 190,779 / LedgeUp 1,978 / LedgeDown 1,987) |
| OURS | 5,825 | **0 (0%)** | **0** |

Edge Links stitch adjacent cell navmeshes together. With none, **every cell
navmesh is an isolated island**: an actor paths fine inside its current cell and
can never cross a cell boundary, so any AI package with an out-of-cell
destination starts (the actor stands up, plays its en-route dialogue) and then
never moves. This is game-wide AI breakage — it was found while chasing
"Pinarus/Arielle don't travel" after their PACK records were proven clean by
`tools/pack_validate.py`. Geometry is fine: the destination cell's mesh
(`AnvilWest02`, grid -48,-7) has 1,304 verts / 1,959 tris and **does** cover the
target marker point — it just connects to nothing.

**Binary contract (verified; Skyrim.esm now parses 15,949/15,949 clean):**
- **Edge Link = `Type(U32) + Navmesh(FormID U32) + Triangle(S16)` = 10 bytes.**
  NOT 12 — `navmesh_dump.py` had 12 and silently misparsed every navmesh that has
  links (12,229 vanilla misparses → 0 after the fix). Verified on NAVM 0x00101F28
  (63 links): `00000000 a61a1000 4500 | ... b200 | ... 2901` = three links to
  neighbour 0x00101AA6 at triangles 69/178/297.
- A triangle's **flag bits 0/1/2** = `Edge 0-1 / 1-2 / 2-0 Link`. When bit N is
  set, that triangle's edge-N field is an **INDEX into the Edge Links array**
  instead of a local neighbour-triangle index (xEdit `wbEdgeToStr`,
  wbDefinitionsCommon.pas:3457). Other triangle flags: 3 Deleted, 4 No Large
  Creatures, 5 Overlapping, 6 Preferred, 9 Water, 10 Door, 11 Found.
- **Edge Link Type enum: 0 Portal** (cell seam), 1 Ledge Up, 2 Ledge Down,
  3 Enable/Disable Portal.
- Links are **reciprocal** and go to the four orthogonal neighbours — vanilla
  NAVM 0x00101F29 grid (7,7) has 63 links: (6,7)x15, (8,7)x11, (7,6)x22,
  (7,8)x15, and each neighbour links back the identical count.

**Algorithm to implement** (post-pass, after all cell meshes exist, since it
needs neighbour NAVM FormIDs and final triangle indices — and must stay
deterministic, see the parallelism rules in CLAUDE.md):
1. for each pair of orthogonally adjacent exterior cells, take triangles with a
   border edge (edge field `-1`) lying on the shared seam;
2. match them across the seam by coinciding edge endpoints (with a tolerance);
3. emit reciprocal Portal links on both meshes; on each triangle set flag bit
   `1<<edgeIndex` and replace that edge field with the index into its own Edge
   Links array.

**Audit tool**: `python tools/navmesh_connectivity.py <esm> [--ref Skyrim.esm]
[--cell gx,gy]` — reports exterior link coverage vs the vanilla 84% baseline,
link-type mix, door-triangle counts, and internal consistency between
link-flagged triangle edges and Edge Link entries. Exits non-zero while coverage
is far below vanilla.

### 🔴 Corridor redesign regressed edge links — the ribbons never reach the seam (found 2026-07-23)

The pathgrid-corridor redesign (build.py/corridor*.py, "THE PATHGRID IS THE
MESH") builds one flat ribbon per pathgrid EDGE. But `build_edge_links` matches
triangle border edges lying within `SEAM_BAND` (24u) of the exact cell-boundary
plane, and a corridor ribbon stops at the last pathgrid NODE **inside** the cell
— it never reaches the seam. Result: only **182 edge links across 6,504
exterior meshes**, every exterior cell an island again. Pinarus could leave his
house (interior door works) but couldn't cross a single Anvil grid seam.

**The missing input is PGRI (InterCell).** TES4 PGRD carries, besides the
intra-cell `Point[i].Edge[j]` topology, a **PGRI array of cross-cell links**:
each entry names a LOCAL node and the world-space EXIT point it connects to in a
neighbouring cell. `convert_PGRD` built edges only from `Point.Edge` and ignored
PGRI, so no ribbon ever crossed a boundary.

**Fix (two parts):**
1. **Export bug — PGRI is 16 bytes, not 14, and LocalNode is U32, not U16**
   (UESP TES4 PGRD ref: `Local node number (long)`, then float X/Y/Z of the
   FOREIGN node). The old 14-byte/U16 reading misaligned every entry after the
   first into uninitialised CS memory (denormal floats ~1e-41, node indices like
   17306). Fixed in `tes4_export/record_types/world.py::export_PGRD`. This is a
   pure-dump correctness fix — it belongs in the export, per CLAUDE.md.
2. **Import — build a cross-seam ribbon per valid PGRI link.**
   `pgrd_to_navm._collect_intercell` parses PGRI, drops residual garbage
   (LocalNode out of range, `(0,0,~0)` padding, non-finite / far-away exits),
   and for each survivor appends a synthetic node at the exit point plus an edge
   LocalNode→exit. The ribbon then physically crosses the boundary plane. To keep
   each mesh inside its own cell, `corridor_union.build_union_mesh` takes a
   `cell_bounds` rectangle (exterior only) and **clips the unioned coverage to it
   with shapely** before triangulating — leaving a clean border edge exactly on
   the seam for `build_edge_links` to stitch. Chosen over extending geometry into
   the neighbour cell (the "clip at seam, links only" model).

**Verified** on the 8 Anvil cells around Pinarus (worldspace 0x0001C31A, grid
x −48..−46, y −9..−7) via `tools/navmesh_seam_probe.py`: before = 8 isolated
islands; after = **104 reciprocal Portal links, all 8 cells in ONE connected
component**. InterCell yield jumped with the export fix (e.g. grid (−47,−8):
30→42 of 58 kept; total portals in the patch 30→104). `tools/navmesh_seam_probe.py
--wrld <hex> --gx lo hi --gy lo hi` reports per-cell seam-edge counts, InterCell
kept/raw, reciprocity, and the connected-component structure for a cell range —
use it to spot-check a region without a full rebuild.

### 🔴 NAVI is a SINGLETON override + must mirror connectivity (found 2026-07-21)

The edge-link stitching above was necessary but NOT sufficient — Arielle
(MG04, destination in her OWN cell, mesh verified connected across the stairs
by `tools/navmesh_reach.py`) still never walked. Two more defects in the NAVI
record itself, both now fixed:

1. **NAVI must be written as an OVERRIDE of Skyrim.esm's `0x00012FB4`.** The
   Navmesh Info Map is a singleton the engine resolves by that fixed FormID;
   every DLC registers its navmeshes by overriding it with its own NVMI set
   (Update 251, Dawnguard 1873, HearthFires 132, Dragonborn 1732 entries) and
   the engine merges the per-file overrides. We allocated a FRESH FormID
   (0x011930C9), producing a NAVI the engine never consults — **none of our
   8,156 navmeshes were registered, so no converted NPC could pathfind
   anywhere, even inside a single connected mesh.** Loaded actors with a valid
   package just stood; the only movement left was the engine's off-screen
   teleport failsafe (exactly the reported symptom: Arielle occasionally
   "teleported" to her destination, Pinarus never moved even when console-
   teleported outdoors). `navi_builder.NAVI_SINGLETON_FID`.

2. **Every NVMI entry declared zero connectivity.** Contract verified against
   ALL 15,462 Skyrim.esm NVMI entries:
   - `Edge Links` ∪ `Preferred Edge Links` == the distinct neighbour meshes in
     that navmesh's own NVNM Edge Link array, **self-links excluded** (the 347
     non-matching entries differ only by a self-link). We emit all of them as
     plain Edge Links.
   - `Door Links` == the door REFRs of that navmesh's own NVNM Door Triangles
     (15,462/15,462 exact), CRC `"PathingDoor"` = 0xE48B73F3. Each side of a
     load door lists only its own door ref; the engine joins the two meshes via
     the doors' XTEL pairing — this is what carries an actor through ANY load
     door (interior→exterior, city gates between worldspaces).
   - The U32 after the FormID is **Flags** (0x20 = Is Island + island-data
     union, 0x40 = Not Edited), not a "category"; island data is OPTIONAL
     (305 vanilla entries have no links and no island data). We write 0.
   Plumbing: `pgrd_to_navm` puts `door_refs` on the meta;
   `navm_edge_links.build_edge_links` puts `edge_link_fids` on the meta (for
   every exterior view, dirty or not); `navi_builder._pack_nvmi` mirrors both.

**Also matched vanilla**: top-group order places NAVI *before* CELL/WRLD
(Skyrim.esm order `... REGN NAVI CELL WRLD DIAL QUST ...`) — the engine fixes
up NVMI's forward NAVM references lazily, unlike QUST ALFR. Vanilla NVMI is
NOT sorted on disk (7,790 out-of-order adjacent pairs in Skyrim.esm), so entry
order is free.

**NVPP must be carried forward.** Every vanilla master's 0x12FB4 override
ships a FULL 25,696-byte NVPP (Skyrim/Update/Dawnguard/HearthFires/Dragonborn
each carry their own edited copy of the same 100-path table). Our override is
the winning one, so an empty NVPP would replace the vanilla precomputed-path/
road network. `navi_builder.read_master_nvpp` re-ships the newest vanilla blob
from the registry-detected SSE install.

**FormID-allocation stability.** The old code allocated the NAVI's FormID from
`writer.alloc_formid()`; switching to the fixed singleton id removed that
allocation and SHIFTED every later-allocated FormID (all generated
DIAL/INFO/DLBR/DLVW/LCTN/SNDR records — thousands) relative to previously
shipped builds, which scrambles any existing save's script/dialogue state. The
import now burns one id at the same point to keep the layout stable. Never
add or remove an `alloc_formid()` call without accounting for this.

**Reachability tool**: `python tools/navmesh_reach.py <esm> --from-ref <fid>
--to-ref <fid> [--cell <fid> --components]` — decodes every NAVM, builds the
(mesh, component) graph over NVNM edge links + door-XTEL joins, locates both
endpoints, and answers REACHABLE yes/no with component/z-range detail. This is
what proved Arielle's cell mesh was fine and pushed the investigation to the
NAVI layer.

**Exterior door triangles need the worldspace's PERSISTENT doors (2026-07-21).**
Exterior teleport doors (house entrances, city gates) are persistent REFRs
parented to the worldspace's persistent *dummy* cell, not to the grid cell they
physically stand in — so the per-cell refr list never contained them and only
89/6,516 exterior meshes had door triangles (interiors: 1,612/1,640). Pinarus's
exit chain died on the Anvil street side of his own front door.
`_gather_navm_jobs` now buckets each worldspace's persistent door refs by the
grid square their POSITION falls in and passes them to that cell's job as
`extra_door_refrs` (convert_PGRD feeds them to the door threshold stamp +
door-triangle linking only). The doors are part of `_geom_hash`, so affected
exterior cells regenerate automatically.

## LAND Record Structure

Both TES4 and TES5 use `wbLandscapeLayers` from wbDefinitionsCommon.pas. The "Layers" array is a FLAT array of Layer entries where each is EITHER a Base Layer (BTXT) OR an Alpha Layer (ATXT+VTXT) — they are NOT nested.

### Export Format
```
LayerCount=N
Layer[i].Type=BASE|ALPHA
Layer[i].BTXT.Texture=FormID    # BASE only
Layer[i].BTXT.Quadrant=0-3      # BASE only
Layer[i].ATXT.Texture=FormID    # ALPHA only
Layer[i].ATXT.Quadrant=0-3      # ALPHA only
Layer[i].ATXT.Layer=N            # ALPHA only
Layer[i].VTXTCount=K             # ALPHA only
Layer[i].VT[k].Pos=posval        # ALPHA only
Layer[i].VT[k].Op=opval          # ALPHA only
VTEXCount=N
VTEX[i]=FormID
```

### Import Notes
- `ElementAssign(layers, HighInteger, nil, False)` creates a default Base Layer (BTXT)
- For Alpha Layers: remove BTXT via `RemoveElement`, then add ATXT + VTXT
- VTXT structured data only available when `wbSimpleRecords = False`; raw byte array otherwise
- **Alpha layer numbers must be per-quadrant sequential (0,1,2…), NOT the TES4 original values**
- **Skip alpha layers with Texture FormID = 0** — they cause visual artifacts in TES5
- **Max 8 alpha layers per quadrant** in TES5. Skyblivion uses 5 but engine supports 8.
- VTXT export field is `VT[k].Op` but import uses `VT[k].Opacity` — use Opacity in import
- Exterior cell block grouping: block = `floor(grid / 32)`, sub-block = `floor(grid / 8)`. Use Python `//` (floor division), NOT bitwise `>>` — the `>>` formula is wrong for exact negative multiples (e.g. -32 gives -2 instead of -1).
- Persistent worldspace cell classification: use `RecordFlags & 0x400`, NOT `XCLC.X == ''`. Persistent cells often have XCLC=(0,0) so the empty-string check mis-classifies them as exterior cells, putting them in the wrong block/sub-block structure and breaking all exterior cell loading.

### TXST for Landscape Textures
- No DNAM: vanilla Skyrim LTEX TXSTs omit DNAM. The 0x0001Fa "No Specular Map" flag only applies to the object (BSLightingShader) path, NOT the landscape shader. Writing it has no positive effect.
- TX00 = diffuse (`tes4\landscape\<icon>.dds`)
- TX01 = normal map (`tes4\landscape\<icon>_n.dds`)
- LTEX SNAM specular exponent: **pass through the TES4 value**. SNAM is a Phong exponent used directly by the landscape shader. Setting SNAM=0 gives `pow(NdotH, 0) = 1.0` everywhere → whole landscape appears blindingly bright white. TES4 landscape textures use ~30 (moderate gloss). Do NOT write SNAM=0.

## OBND (Object Bounds) defaults
- ESM records without OBND crash the engine. Import script generates per-type defaults:
  - MISC=(-5,-5,0,5,5,8), KEYM=(-3,-3,0,3,3,3), WEAP=(-5,-5,0,5,5,30), STAT=(-50,-50,0,50,50,80)
  - ARMO=(-15,-10,0,15,10,30), NPC_/CREA=(-12,-12,0,12,12,60), LIGH=(-6,-6,0,6,6,20)
  - Other types get (-5,-5,0,5,5,5) as fallback
