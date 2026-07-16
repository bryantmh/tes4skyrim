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
  white core = teleport door). Exterior cells can be addressed as
  `--cell grid:X:Y` (colon form survives comma-list splitting; Windows filenames
  can't hold `:` so outputs sanitize it). `tools/navmesh_probe.py --cell X`
  reports pathgrid-on-floor coverage and Z error. `tools/navmesh_audit.py
  --interiors N --exteriors M` sweeps both cell kinds and reports UNCOV%/
  BROKEN/STEEP/FLOOR/ISL/TINY/SLIV%/MICRO per cell. `tools/navmesh_profile.py
  --cell X` cProfiles one cell's build (how the shadowed()/plane_err hotspots
  were found).
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
