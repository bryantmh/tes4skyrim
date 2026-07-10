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
- **Algorithm (region-based, rewritten 2026-07-06 — replaces the old sparse
  "Delaunay-then-coverage-mask" that produced thin ribbons floating above the
  floor with jagged holes)**: reconstruct the WALKABLE FLOOR as a shapely
  polygon, then tile it.
  1. `_build_walkable_polygon`: buffer every pathgrid EDGE into a
     `CORRIDOR_HALF_WIDTH`(=75u) capsule + every NODE into a `NODE_RADIUS`(=95u)
     disc, union → clean-edged room/corridor polygons. Bethesda places pathgrid
     nodes just inside the walls, so this tight radius leaves the WALLS as clean
     gaps between corridors (validated against a user-painted wall reference for
     AnvilFightersGuild). A small dilate+erode close bridges node-disc pinholes
     BEFORE obstacle subtraction (so it can't refill carved holes).
  2. Subtract obstacle footprints (real furniture/architecture) and door jambs.
  3. `_triangulate_region`: sample boundary vertices along every ring at
     `BOUNDARY_STEP`(=64u) + an interior `FILL_STEP`(=128u) grid clipped to the
     polygon, Delaunay (scipy), keep triangles whose centroid is inside the
     polygon. Dense boundary sampling → clean edges that follow walls + holes.
  4. `_assign_z`: nearest-pathgrid-node Z, weighted STEEPLY (1/(d⁴+1)) toward the
     closest node so multi-level interiors don't average across floors. Exterior
     uses the SAME node-Z (nodes sit on the walkable street surface, which is
     ~100u above raw LAND terrain — see LAND note below), easing toward the LAND
     height only past `LAND_BLEND_DIST`(=300u) from any node.
  5. Compute edge adjacency, flag water tris (centroid Z < XCLW water height),
     add door triangles, choose a per-navmesh grid divisor (~600u buckets).
- **Obstacle carving uses REAL MESH SILHOUETTES, not AABBs** (2026-07-06):
  `tes5_import/mesh_footprints.py` caches the 2D CONVEX HULL of each converted
  NIF's XY vertices (`scan_mesh_footprints` → `export/<plugin>/mesh_footprints_cache.json`,
  simplified to ≤24 pts). `_build_exclusion_zones` rotates/scales/places each
  hull so a round well carves a circle, an angled wall its true diagonal, etc.
  — AABB (mesh_bounds) is used only for fast height/size gates + fallback. Each
  footprint is shrunk by `EXCLUSION_MARGIN`(=24u) so the navmesh hugs the object
  surface. `_classify_footprints` splits obstacles from FLOORS/shells: a
  footprint containing ≥`FLOOR_NODE_COUNT`(=3) pathgrid nodes or spanning
  >35% of the pathgrid extent is a FLOOR (NPC stands on it — a floor tile, rug,
  or the building shell whose AABB covers the whole room) and is NEVER carved;
  furniture with a single sit/sleep interaction node stays an obstacle. Size
  gate: skip flat objects (height <40u), and require min half-extent ≥13u AND
  area ≥1600u² so long-thin benches carve but sacks/cups don't.
- **Door handling** (`_collect_doors`, `_door_choke_obstacles`,
  `_build_door_links`): a door REFR is teleport (has `XTEL.Door` → connects two
  cells) or interior-only (base in the DOOR set → same-cell passage). BOTH get a
  Door Triangle. The navmesh is CHOKED at every door: jamb rectangles flank each
  opening to neck the region to `DOOR_WIDTH`(=90u), and a guaranteed passage
  SLOT is unioned back through the door centre so the choke can never sever
  connectivity. The Door Triangle links the triangle straddling the threshold
  line (weighted toward small along-facing offset). Door CRC "PathingDoor" =
  `0xE48B73F3`. `door_fids` (raw low-24 DOOR base FormIDs) is threaded from
  import_main via `_build_door_fid_set`. **Limitation**: cross-cell Portal Edge
  Links between the two navmeshes of a teleport door are NOT computed (can't
  resolve the other navmesh's triangle from PGRD alone).
- **Base-model index**: `_build_base_model_index(by_type)` in import_main maps
  raw low-24 base FormID → normalised `tes4/...nif` key, only for blocking base
  types (so doors/lights/markers never punch holes). REFR exports position as
  `PosX/PosY/PosZ` + `RotZ` + `XSCL.Scale` and base object as `NAME` (there is NO
  `DATA.PosX`/`BaseType`/`Model.MODL` on REFR).
- **Triangle flags** (wbDefinitionsTES5.pas): every generated tri sets
  `0x0800 Found` (matches vanilla); water tris add `0x0200`, door-linked tris add
  `0x0400`. `_TRI_EDGE_LINK` bits (0x0001/2/4) are NOT set (we emit no Edge
  Links). Cover Triangles array = empty.
- **LAND VHGT decode** (`_decode_vhgt`): offset float + 33×33 SIGNED int8
  gradients; accumulate in RAW units (÷8 the offset going in, ×8 coming out) —
  mixing raw and ×8-scaled units in the accumulator multiplies the running
  height by 8 every row → 1e30 overflow (the bug that made exterior navmesh Z
  garbage). Pathgrid node Z is ~100u ABOVE the raw LAND height in cities
  (streets are cobblestone statics), so LAND is the terrain, NOT the walk
  surface — Z comes from nodes, not LAND.
- **Rendering/iteration tool**: `python tools/navmesh_render.py --cell <FormID_or_EditorID>
  [--out png] [--size N]` renders a top-down image: LAND heightmap, real mesh
  SILHOUETTE footprints (obstacles red, floors/shells faint gray), pathgrid
  nodes+edges (yellow), generated navmesh triangles (green/water blue/door
  orange), teleport doors (magenta) vs interior doors (cyan) with facing lines.
  This is the primary tool for tuning the converter — always compare against it.
  Needs the mesh_bounds + mesh_footprints caches (auto-discovers pipeline paths).
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
- **Exterior PGRD point coords are WORLD coords** (not cell-local) → LAND
  sampler origin = `grid_x*4096, grid_y*4096`. Points can extend past the cell
  into neighbours; the sampler clamps to the 33×33 grid.
- **Dependencies**: needs `shapely` (walkable-region boolean ops) + `scipy`
  (Delaunay/KD-tree). Both pip-installed.
- **Tests**: `tests/test_pgrd_navm.py` (19 tests: NVNM round-trip, adjacency
  symmetry, grid coverage, water flags, footprint carving, door triangles/flags,
  VHGT decode, NAVI/NVMI layout).
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
