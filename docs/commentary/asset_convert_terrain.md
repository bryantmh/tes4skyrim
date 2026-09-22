# asset_convert/lod/terrain_lod.py — terrain and LOD

**Code:** `asset_convert/lod/terrain_lod.py`, `asset_convert/texture/dds_codec.py`, `asset_convert/lod/lod_gen.py`, `asset_convert/lod/lod_far_gen.py`, `asset_convert/lava_surface.py`

## Contents

- [Grass (GRAS) conversion — record invariants + shader profile (2026-07-09)](#grass-conversion-record-invariants-shader)
- [Terrain/LOD/LAND-adjacent asset notes](#terrainlodland-adjacent-asset-notes)
- [Lava surfaces — Oblivion realm water rendered as actual lava (2026-08-23)](#lava-surfaces-oblivion-realm-water)
- [QEM decimation: the budget and its tuning constants](#qem-decimation-tuning)
- [Object LOD: `_lod` in FO3/FNV, `_far` in Oblivion](#object-lod-suffix-differs-by-game)
- [Prescreening the LODGen input](#prescreening-the-lodgen-input)
- [`write_lodgen_input`: master modes and `only_cells`](#write-lodgen-input-master-modes)
- [Terrain LOD invents ground over cells that own no LAND](#lod-invents-terrain-over-cells-with-no-land)
- [GENERATED `_far.nif` belong to the LOD mod](#generated-far-nif-belong-to-the-lod-mod)
  - [Why one LOD folder, not one per plugin](#one-lod-folder-not-one-per-plugin)

## Grass (GRAS) conversion — record invariants + shader profile (2026-07-09)
<a id="grass-conversion-record-invariants-shader"></a>
- Skyrim grass spawns purely from LAND texture layers whose LTEX has GNAM — no REFR/region records involved. Chain verified in output ESM: GRAS DATA layout byte-matches the xEdit TES5 def; LTEX GNAM links and subrecord order (EDID/TNAM/MNAM/HNAM/SNAM/GNAM) match vanilla; all 419,902 LAND BTXT/ATXT layers resolve to real LTEX (~120k grass-linked); WRLD DATA bit 0x80 = "No Grass" must stay clear.
- **GRAS record invariants (the working-example pattern)**: every working GRAS record — vanilla Skyrim.esm, USSEP, Beyond Skyrim's BSHeartland.esm, Skyrim Extended Cut, Legacy Orsinium (all dumpable from the install with tools/esm/tes5_esm_reader.py) — has (1) **OBND all ZEROS**, never computed mesh bounds, (2) **MODT present** (BSHeartland proves a 12-byte version-2 stub `02000000 00000000 00000000` suffices), and (3) **the model under `meshes\landscape\grass\`** — 45/45 surveyed MODL paths contain `landscape\grass\`; nothing outside it is known to work (same kind of hardcoded naming contract as the `NPC Root [Root]` bone). convert_GRAS builds the record manually to honor all three (MODL via `grass_profile.grass_model_dest()` → `landscape\grass\tes4_<basename>.nif`, flattened — TES4 grass basenames are collision-free), and `grass_profile.run()` copies each converted grass NIF there (sources under tes4\ stay for FLOR/STAT sharing). BSHeartland also shows GRAS DATA density up to 80 and TES4-style values are fine; LTEX INAM (SSE "Is Snow" flag) is optional.
- **Grass NIFs with Vertex_Alpha + low VC alpha look invisible/ghosted in NifSkope — that is normal and matches vanilla** (vanilla grass VC alpha avg ~0.15; NifSkope multiplies it into the alpha test, the in-game grass shader uses it as wind weight instead). Don't diagnose grass visibility in NifSkope. Alpha-test thresholds are clamped to the vanilla grass envelope (≤100; Oblivion used up to 128).
- **GRAS DATA Density/PositionRange — placement parity with the source engine.** <a id="grass-placement-parity"></a> The first grass CTD hunt clamped Density ≤80 / PositionRange ≤32 on the theory that raw TES4 values over-instance the planter; the real crash causes turned out to be #2 and #3 below, and the clamp actually made things WORSE, because the clamp direction was backwards. Disassembly of all three planters (Oblivion.exe `0x4ea8a0`, FalloutNV shares the engine, CreationKit.exe `bgsgrassmanager.cpp` at `0x124ad00`) shows the SAME rule: per land quad the grid is `n = min(2048 / PositionRange, 2048 / iMinGrassSize)` cells per side, one candidate per cell jittered inside it, placed with probability `Density% × layer-blend` (Skyrim: `rand(0..32767) < weight × 32768`). The only differences are the ini default — **iMinGrassSize is 80 in Oblivion_default.ini and Fallout_default.ini, 20 in Skyrim** — and that Oblivion/FNV divide the 3×3 weight grid by `n_capped / n_wanted` (i.e. boost density by `80 / PositionRange`, saturating at 100%) when the cap bites, which Skyrim never does. So an Oblivion record with PositionRange 40 / Density 40 plants 25×25 cells at 80% (500/quad) in Oblivion, while the old clamp (PositionRange 32) planted 64×64 at 40% (1,638/quad); FNV `NVGreenGrass02` (PositionRange 17, Density 35) is 625/quad in FNV vs 10,404 × 35% = 3,641/quad. That 3-6× pile-up of 120-180-unit-wide clumps is what reads in-game as "grass twice as big" — the per-instance size is provably identical: both vertex shaders compute `pos × (1 + heightDelta × ScaleMask)` (Oblivion `GRASS2020.vso` CTAB names `c2 ScaleMask`, writer at `0x7e912f` = (1,1,1) uniform / (0,0,1); Skyrim `RunGrass` `InstanceData4.y × ScaleMask + 1`), both planters draw `heightDelta = HeightRange × rand(-1..1)` (Oblivion `0x7c4f50` ×100 into `InstanceData.w`; CK `0x124bb0e`), and neither pixel shader uses vertex alpha for the alpha test (wind weight only). Fix in `convert_GRAS`: `PositionRange_out = max(PositionRange, 80)` and `Density_out = min(100, Density × 80 / PositionRange)` when PositionRange < 80, which reproduces the source grid and its density compensation exactly under Skyrim's default iMinGrassSize. Mesh geometry, textures and alpha thresholds are unchanged by conversion (bbox of every converted grass NIF equals its source; textures are byte copies). Per-cell grass lists: `python tools/esm/cell_grass.py export/Oblivion.esm --wrld Tamriel --cell X,Y`.
- **FNV grass textures**: all 17 FNV grass NIFs carry their diffuse as the single `File Name` of a `TallGrassShaderProperty` (no NiTexturingProperty, no BSShaderTextureSet), so `collect_shader_inputs` must read that property or the shape falls back to `Textures\white.dds`.
- **Grass CTD root cause #2 — Oblivion meshes with NO triangle data (SOLVED 2026-07-10)**: several vanilla Oblivion grass meshes (GroundCoverMediumGrass01/LongGrass01, GroundCoverPineappleWeed*, GroundCoverWildPlant*, ms14longgrass01) ship NiTriShapeData with `has_triangles=False` — Num Triangles is set but the index array is ABSENT (only `Oblivion - Meshes.bsa` has them; no intact alternates exist). Oblivion's grass renderer tolerated it; Skyrim's planter dereferences the missing data → **region-specific CTD with no log** (Heartland/Cheydinhal cells). Several others (JMMediumGrass*, brmediumgrassyellow01, groundcoverfern01, oblivionmoldroots01) carry legacy vertex **match groups** (no vanilla Skyrim mesh has any) → same crash in Bruma cells. Fix: `asset_convert/nif/tri_reconstruct.py` — blade triangles are reconstructed from the 3-UV-role pattern (base-left/base-right/tip; tip role = highest avg z; each blade pairs a tip with the base pair whose midpoint it tops; winding from stored normals), and match groups are cleared on every converted shape. Both run inside `_convert_strips_or_shape`, so FLOR/STAT meshes sharing these sources heal on full re-runs. Diagnosis method: `tools/esm/cell_grass.py` per-cell grass lists × working-vs-crashing region diff → the defective meshes were exactly the types unique to crashing regions. A mesh with `has_triangles=False` also renders as NOTHING in NifSkope (that was the real cause of the "invisible in NifSkope" report, not vertex alpha).
- **Grass CTD root cause #3 — intermediate NiNode wrapper on rotated sources (SOLVED 2026-07-10)**: Skyrim's grass instancer (`AddCellGrassTask` → `BSMultiStreamInstanceTriShape`) requires grass geometry as a **direct child of the BSFadeNode root** — every working grass NIF (vanilla + converted `gcgorsegrass`/`gclonggrass`) is flat `BSFadeNode → NiTriShape`. But the generic converter's Pass-6c wraps geometry in an inner NiNode whenever the **source root carries a non-identity rotation** (it bakes the rotation into a child NiNode because Skyrim honors child-NiNode rotation but ignores BSFadeNode root rotation for statics). The grass path never traverses that inner node → dereferences garbage (`rdi=0x0001000100010001`, `movzx ecx,[rdi+0x32]`) → **CTD** on any cell spawning the type. Hit TES4 **BWCattail01/02/03** (their source roots are rotated; the crash object was `BWCattail02` with a nested `NiNode "BWCattail02"`). Fix: `grass_profile._flatten_grass_root()` (runs inside `apply_grass_profile`) bakes each plain NiNode wrapper's transform into its geometry's verts+normals and re-parents the geometry onto the root, dropping the empty NiNode — world-space geometry preserved (verified: Z height extent unchanged). Only collapses bare NiNode wrappers holding pure geometry (no collision/controller/extra-data). Diagnosis: crash log named `tes4_bwcattail02.nif` + `BSFadeNode`/`NiNode` both named "BWCattail02"; block dump vs a working converted grass NIF showed the extra nesting; source root rotation identity=False (vs gcgorsegrass identity=True, which stayed flat).
- **Shape transforms and collision on a grass NIF (Morrowind groundcover).** <a id="grass-shape-transforms"></a> Census of the 26 vanilla grass NIFs in `references/Skyrim Meshes/meshes/landscape/grass/`: 26/26 have identity rotation and scale 1.0 on the shape (2 carry a small translation only), and 0/26 carry a collision object or BSXFlags. The Morrowind static converter leaves the Y-up rotation, a 0.31 scale and a -24.7 translation ON the NiTriShape and attaches a bhkMoppBvTreeShape rigid body plus `BSX 130`; the instancer never applies a shape transform, so the blade lies flat at its unscaled size under the terrain and no grass is seen. `grass_profile._bake_shape_transforms` folds any root-level shape transform into the vertices and normals (before `add_wind_weights`, whose height ramp reads vertex z) and `_strip_collision` drops the root collision and BSXFlags. NOT yet in-game verified.
- Known remaining grass-NIF oddities (not crash-related, grass renders): most grass tex[1] `_n.dds` normal maps don't exist (vanilla grass points tex[1] at `textures\effects\HighFrequencyNormals.dds` or the literal string `NOR`); bwcattail03 references BWCatTail02.dds which is absent from the extracted BSAs.
- **BSHeartland.esm is the best reference for "custom worldspace + custom grass records that provably work"** — compare against it before vanilla when a worldspace-scoped feature is dead.
- Grass NIFs additionally get the vanilla grass shader profile (`asset_convert/nif/grass_profile.py`, run by `asset_pipeline.convert_meshes`; models identified from the export's `GRAS.txt`): NiAlphaProperty alpha-test only (blend bit clear), SLSF1 OwnEmit+VertexAlpha set / Specular clear (Specular + glossiness 0 = pow(NdotH,0)=1 white-out), emissive ×1.0, gloss 80, spec white/1.0, lighting effects 0.3/2.0, clamp 0 — matching every vanilla LE grass mesh in `references/Skyrim Meshes/meshes/landscape/grass/`. Geometry/UVs/vertex-color alpha (wind weight) preserved.
- 8 Shivering Isles grass models (Plants\Dementia\*, Plants\Mania\*) are absent from the extracted BSAs — their GRAS records exist but have no mesh until SI assets are extracted.

## Terrain/LOD/LAND-adjacent asset notes
<a id="terrainlodland-adjacent-asset-notes"></a>

### WRLD World Bounds (NAM0/NAM9)
- NAM0 (bounds min) and NAM9 (bounds max) store X, Y as raw float world-unit values (same scale as TES4)
- xEdit **displays** them scaled by `1/4096` (cell units) but the raw file value is NOT divided
- TES4 exports `NAM0.MinX=-262144.0` → write exactly -262144.0 to TES5 file (do NOT divide by 4096)
- If divided: NAM0=-64.0 looks like valid cell coords but is actually 64 times smaller than needed → SSELodGen won't generate world map correctly

### 🔴 MTTC targets and sequence blocks must be dropped TOGETHER (2026-08-10)

Crashes `crash-2026-08-10-00-42-35` / `-00-51-26` / `-00-53-49`:
`EXCEPTION_ACCESS_VIOLATION` at `VCRUNTIME140.dll+0019BCF`,
`movdqu xmm2, [rax]` with `rax=0`. This one names its own cause in the object
list: `NiControllerSequence`, `BGSGamebryoSequenceGenerator`, behavior graph
`spiddalcloudplant`, `BSFadeNode "spiddalplant"
("tes4\Oblivion\Plants\SpiddalCloudPlant.NIF")`. It fires **when the plant
animates** (walking up to a Spiddal Stick as it spews its cloud), not on cell
load.

`NiMultiTargetTransformController.extra_targets` is a **POSITIONAL** list: the
engine pairs slot N with the `NiControllerSequence` controlled-block that
drives it. Break the pairing either way and the slot resolves to a null
interpolator, which the sequence generator dereferences.

**Both directions were shipped and both crashed identically** — worth
recording, because each looked correct in isolation:

1. The original code dropped any controlled block whose node name equalled the
   **root node's** name, leaving `num_extra_targets` unchanged. Oblivion
   routinely names the root and its animated node the same thing
   (`spiddalcloudplant.nif`'s root IS `spiddalplant`, also extra-target #1),
   so the target lost its driver. Source 10 blocks / 3 targets → ours 9 / 3.
2. "Keep the root-named block so the target matches" — this restored 10/3 and
   **still crashed**, because a root-targeting entry is itself illegal.

Census of 141 sequences across 43 animated vanilla meshes, and **both numbers
are zero**:

| invariant | vanilla |
|---|---|
| controlled blocks targeting their own root | **0** |
| MTTC extra targets with no controlled block | **0** |

Vanilla satisfies both by never listing the root as a target at all. So the
correct fix is to drop the block **and** remove that node from every MTTC
target list, decrementing `num_extra_targets` with it (`_drop_mttc_target`).
Result on the crashing mesh: 9 blocks / **2** targets, root absent, every
target driven.

Blast radius: **413 meshes**, including `obcloud01.nif` and
`oblivionsmokeemitter01.nif` — the two REFRs that appeared in the earlier
`SkyrimSE+050E6AD` logs, where a separate LOD fault happened to crash first.
Audit with `tools/validate/mttc_target_check.py`; guarded by
`tests/test_asset_convert.py::TestMTTCTargetsStayInSyncWithControlledBlocks`,
which drives the REAL converter (a hand-built MTTC tests pyffi's reference
arrays, not the converter).

### 🔴 A graph-bound mesh must ship NO empty text keys (2026-08-10)

Crashes `crash-2026-08-10-01-08-13` / `-01-39-02` / `-01-41-07` — the Spiddal
Stick and Harrada Root CTDs that survived both the MTTC fix (above) and every
Rest-state theory.  Same signature as the MTTC family (`movdqu xmm2,[rax]`,
rax=0, VCRUNTIME140, under `BGSGamebryoSequenceGenerator`) but a different
null.

**Mechanism, from disassembly** (`tools/disasm/address_lib.py --log` → GOG RVA
`0x505130`, Address Library ID 32774; crash site is the call returning to
`+0x1B0`): on state activation the generator walks the activated
`NiControllerSequence`'s `NiTextKeyExtraData` translating keys into behavior
events.  Each value is first matched WHOLE against the project's registered
event table (hash map on `BShkbHkxDB::ProjectDBData+0xC8`); on a miss the
engine calls `strchr(value, '.')` to split an `Event.Payload` key
(`mov edx, 0x2e` right before the crashing call — and the crash log's
`R9=0x2E2E` is strchr's broadcast needle).  The strchr runs on the RAW string
pointer: an **empty NiString loads as a NULL BSFixedString**, and the read of
address 0 is the CTD.  So the crash fires the moment the object first
animates — the Spiddal Stick spewing its cloud as the player walks up.

The sources really do ship empty keys — Oblivion authored them freely and its
engine ignored them: `spiddalcloudplant.nif`'s Forward has `t=0.1 ''`,
`harradauprightattack.nif` has SEVEN of them.

**Vanilla census draws the exact legality line:**

| where | empty text keys |
|---|---|
| graph-carrying meshes (animobjects, traps, furniture) | **0** |
| graph-less meshes | 2 (`impjaildoor01`, `ruinscanopicjar02` — plain Open/Close) |
| keys with trailing whitespace (`'Sound: X\r\n'`) | 107 in dungeons alone — **legal, do not trim** |

Empty keys are tolerated on the graph-less path (vanilla ships them and those
doors work), and lethal on the graph path (vanilla ships none).  So the fix
(`_strip_empty_text_keys`) drops whitespace-only keys **only for meshes that
get an animobject graph** — converted graph-less doors stay byte-identical to
what already works.  Verified: `idsecretwall01` and `doceilingcollapse01`
(the ImperialDungeon01 hidden door and falling rubble) re-convert
byte-identical; the plants lose exactly their empty keys and keep
`start`/`end`/`sound:` verbatim, including the vanilla-legal trailing `\r\n`.

Audit: `tools/validate/gamebryo_seq_check.py` (check 3).  Guarded by
`tests/test_asset_convert.py::TestGraphMeshesShipNoEmptyTextKeys`, driven
through the real converter on both crashing meshes.

### 🔴 LODSettings must COVER the terrain, or the worldspace CTDs on entry (2026-08-10)
<a id="lodsettings-must-cover-the-terrain"></a>

Crashes `crash-2026-08-09-23-15-19` through `crash-2026-08-10-00-16-34`, all
byte-identical: `EXCEPTION_ACCESS_VIOLATION` at `SkyrimSE.exe+050E6AD`,
`mov rbx, [rax+rcx*8]` with `rax=0`, on a `BSJobs::JobThread`, in
`Plane of Oblivion`. **Reproducible with `coc OblivionMQKvatchEntrance`** —
which is what proved it is the worldspace's own load path, not the Oblivion
gate, the transition, or any script.

The engine builds its terrain-LOD quadtree from `LODSettings/<WRLD>.lod`: root
at SW, `size` cells across, recursively subdivided into 4 children
(`0x4d1020` recurses over `[node+0x30]` with exactly 4 children, stride 0x50).
A `.btr` tile outside that square has **no node**, and the per-frame walk
indexes the node array with **no bounds check**.

Cause: `write_lod_settings` took its extents from `WRLD.MNAM` alone, and **57
of 84 TES4 worldspaces author no usable MNAM** — so `sw == ne == 0` arrived,
and the old `size = 1 << ceil(log2(ne - sw))` plus `eff_sw = -(size // 2)`
produced a **1×1 grid** (`SWx=0 SWy=1`) for every converted worldspace, while
LODGen still emitted tiles out to (-32,-32).

Fix, in two parts:
- Extents are measured from the **CELLS** (always carry XCLC), unioned with
  MNAM only when MNAM is populated.
- The square is anchored and grown until it covers `[sw, ne)`, and `maxLOD`
  tracks `size` (capped at 32) rather than being hardcoded to 32.

The root must contain every TILE, not merely every cell: LODGen snaps each
tile's origin DOWN to a multiple of its own level (a level-16 tile covering cell
-9 is named `...16.-16.y`), so tiles start below the literal terrain corner. SW
is therefore anchored at a multiple of `max_lod` and the square sized from there.
`max_lod` is the coarsest level emitted, capped at 32, and is chosen FIRST
because it sets the anchor granularity; both grow together, the pair recomputed
each round so the anchor tracks the level. Growing is always safe. An earlier
attempt that snapped SW down to a multiple of `size` never converges for a span
crossing the origin -- the gap grows as fast as the size -- which is why the
anchor granularity is `max_lod`, not `size`.

Ground truth — vanilla `.lod` files extracted from `Skyrim - Meshes0.bsa`
(layout `<hhIII` = SWx i16, SWy i16, size u32, minLOD u32, maxLOD u32):

| worldspace | SW | size | minLOD | maxLOD |
|---|---|---|---|---|
| japhetsfollyworld | (-9, -6) | 16 | 4 | **16** |
| dlc01falmervalley | (-16, -13) | 32 | 4 | 32 |
| skuldafnworld | (0, -21) | 64 | 4 | 32 |

The formula reproduces the first two **exactly** from their cell extents, which
is what confirms it rather than merely being self-consistent. Vanilla also
confirms SW is REAL, not centered, and that `maxLOD` is not always 32. Guarded by
`tests/test_asset_convert.py::TestLODSettingsCoversTheTerrain`.

### Terrain LOD (SSELodGen) — data chain
- LAND BTXT/ATXT subrecords contain direct LTEX FormIDs (NOT indices into VTEX array)
- SSELodGen uses: BTXT.Texture(LTEX) → LTEX.TNAM(TXST) → TXST.TX00(path) → Data\Textures\{path}
- VTEX subrecord is a supplementary lookup array; most Oblivion LAND records don't have it (29/31823)
  - TES5 LAND VTEX format: packed array of uint32 LTEX FormIDs, one subrecord total (not per-quadrant)
  - Null slots (zero FormID) are valid and common — NOT a bug
- Landscape textures extracted from BSA are DXT1 BC1 512x512 — fully supported by SSELodGen
- If terrain LOD appears purple after correct data install: ensure OLD LOD tiles are deleted before regenerating

### Distant LOD generation (one-click, rebuilt 2026-07-06) — `convert.py` Phase 8 `phase_lod`
Two pieces, both native, both re-enabled in the pipeline (`generate_lod` + `generate_terrain_lod`).

**Terrain LOD** (`asset_convert/lod/terrain_lod.py` + `terrain_lod_textures.py`): per-tile `.btr` heightmap NIF + composited diffuse `.dds` + heightmap-derived BC5 normal `.dds`, LOD levels 4/8/16/32. TES4Tamriel = 1301 tiles.
- **The old diffuse was the bug**: it upscaled raw LAND VCLR vertex colors → a blurry color grid (why distant terrain looked wrong). FIX: `terrain_lod_textures.composite_cell()` composites the REAL landscape textures — resolve LTEX FormID→diffuse via `build_ltex_texture_map` (LAND BTXT/ATXT → LTEX.TNAM → TXST.TX00 = `tes4\landscape\*.dds`), then per quadrant blend base + alpha layers using the ATXT/VTXT opacity grid (17×17, pos=row*17+col, sorted by ATXT layer index), ×VCLR shading at 0.4 strength (full x2 caused hard cell seams). Landscape UV repeats every 2 cells.
- **Compositor orientation contract (fixed 2026-07-09 — the "large single color areas" bug was three separate defects):**
  1. **Quadrants with no BTXT base layer** (22.6% of Tamriel quadrants, whole sea floor) rendered flat grey-128. The engine's default for unpainted land is `Landscape\Default.dds` → `DEFAULT_LAND_TEXTURE = tes4\landscape\default.dds`. Cells with NO LAND record now also composite (default texture) instead of a flat fill.
  2. **V ran the wrong way**: the diffuse tile is written image-row-0 = NORTH, so world V must DECREASE as the image row grows. Sampling with ascending V mirrored every quadrant and broke ground-texture continuity at every quadrant boundary (horizontal banding every half cell). Same flip applies to VTXT opacity grids and VCLR (LAND row 0 = SOUTH → `np.flipud` to image space), and to the heightmap-derived normal map (`_heightmap_normal_rgb` flips + negates the row gradient so the `_n.dds` matches the diffuse orientation — it was N/S-mirrored vs the diffuse before).
  3. **No underwater murk**: vanilla/xLODGen LOD diffuse bakes submerged terrain toward a flat murky color; without it the sea floor reads as bright land. `composite_cell(heights=, water_height=)` blends toward `MURK_COLOR` by depth (`MURK_FULL_DEPTH=512`, cap `MURK_MAX=0.9`).
  - Ground truth for row-0=north: xLODGen's own `tamriel.32.0.32.dds` (northern Sea of Ghosts at the TOP).
- `.btr` structure = `BSMultiBoundNode` "chunk" → child[0] `NiTriShape` "land" (scale=level, local 0..4096 verts, shader type 18 LODLandscapeNoise, no normals/vcol), child[1] optional WATER node → `BSMultiBound`/`BSMultiBoundAABB`. Loads in-game; AABB magnitude matches vanilla LOD4. (xLODGen source only READS .btr for object-face culling — terrain .btr generation is entirely ours.)
- **Land UVs are REQUIRED and meaningful** (fixed 2026-07-09): vanilla maps the tile texture across the tile with `u = x/4096`, `v = 1 − y/4096` (v=0 = NORTH edge = DDS row 0). "UVs are irrelevant for terrain .btr" was only true of how xLODGen *reads* them — the ENGINE samples them. All-zero UVs make every triangle sample one texel → each tile renders as a single flat color → the in-game/world-map "hard-edged checkerboard, one color per tile" symptom. Water shape has NO UVs (num_uv_sets=0), matching vanilla.
- **LOD water (added 2026-07-09, vanilla-exact)**: child[1] = `BSMultiBoundNode` named `WATER` (scale 1) → one shape with an independent flat quad per water cell (4 verts/2 tris each, cell-local size 4096/level, Z = water height / level, NO shader/UV/normals — the engine textures it from WRLD NAM3). LOD4 uses `BSSegmentedTriShape` with EXACTLY 16 segments (fixed 4×4 grid, column-major sx*4+sy, so the engine can hide quads over loaded cells); LOD8/16/32 use plain `NiTriShape`. Segment binary layout (nif.xml `BSGeometrySegmentData`): `flags:byte=0, start_index:uint (tri-POINTS, 0 when segment empty), num_primitives:uint`; PyFFI's `BSSegment` fields are misaligned over the same 9 bytes — write `internal_index = start<<8` and `flags.bsseg_water = 1` (== num_prims 2 << 8). WATER AABB: XY = quad bbox in world units rel. tile origin; Z spans [min water height, max(max height, 0)]. Water cells = CELL HasWater (DATA bit 0x02) AND terrain dips below the cell water height (XCLW override valid only in ±1e9, else WRLD DNAM default).
  - **The old CTD** (BSMultiBoundNode "Water" → null deref): the engine's LOD-water path derefs the worldspace's WATR via **WRLD NAM3** — the fix is NOT to avoid the node, it's to write NAM2/NAM3 = Skyrim.esm DefaultWater (0x18) + NAM4 (LOD water height, 0 for Oblivion) in `convert_WRLD`. Also: TES4 CELL XCLW `-2147483648.0` = "use default" sentinel — must be OMITTED on conversion, not written as a literal height.
- Normal map derived from the heightmap gradient (`_heightmap_normal_rgb` + real BC5 via `_encode_bc4_block`), replacing the old flat normal so distant terrain is lit.
- Debug single tiles without a full run: `python tools/lod/terrain_lod_render.py` (rebuilds specific tiles in-process, reports water quads, dumps diffuse PNG). `python -m tools.lod.terrain_lod_tex_probe [--cell X Y]` audits LTEX→TXST→dds resolution and per-cell layers.
- Validate with `python tools/lod/terrain_lod_render.py --esm output/oblivion.esm/oblivion.esm --worldspace TES4Tamriel --cell X Y --radius R` → side-by-side hillshade + composited diffuse (the primary iteration tool; do NOT byte-match vanilla .btr). `tools/lod/lod_nif_inspect.py` dumps .btr/.bto geometry+shader.
- **🔴 A worldspace a MASTER defines is ALWAYS sourced from that master, with the plugin as an OVERLAY — never from the plugin alone, however much terrain it adds** (2026-08-11, Tamriel.esp). `convert.py::_records_esm` used to hand record ownership to whichever file held the *bulk* of the LAND records. That silently inverts for a plugin which **extends** a master's worldspace rather than patching it: Tamriel.esp adds a landmass around Cyrodiil (99,910 LAND vs Oblivion.esm's 31,823), won ownership, and every tile was then built from the plugin ALONE — all of the master's own terrain was missing from the heightmap and `fill_missing` edge-extended it into flat plateaus. Symptom: tile-sized discontinuities along the vanilla border, **worst at level 32** where one tile spans 32×32 cells (tile `32.0.-32` had 86 of 1024 cells and encoded world Z `4096..16416` instead of `-4576..20152`; tile `32.0.0` had ZERO cells and rendered dead flat). The tell is that the only level-32 tiles that looked *correct* were the two the plugin never regenerated, so the master's copy survived. Record COUNT never distinguished "patches a worldspace" from "extends a worldspace" and must not decide ownership — the overlay path already expresses "master's terrain + this plugin's edits" correctly and is what the DLC/override case always used. Verify with `Parsing LAND records from <master>, <plugin>` in the run log and a LAND count ABOVE the plugin's own (110,095 vs 99,910 here); a single-file parse line means the bug is back.

**Object LOD + tree billboards** (`asset_convert/lod/lod_gen.py` via `external/lodgen/LODGenx64.exe`): STAT/etc. flagged `0x8000` (Distant LOD)/`0x10000000` (World Map) by size in the importer get baked into `.bto`. TREE refs render as **crossed-quad billboard cards** via LODGen's FlatTextures mechanism (`_tree_billboard` points the LOD "model" at `tes4\trees\billboards\<sptstem>.dds` — Oblivion ships 118 billboard renders; `_write_flat_textures` emits the descriptor + a normals file so cards are lit + `_ensure_white_dds`). 91 flat textures, 716/734 LOD4 .bto contain tree billboards. `.btt`/`.lst` vanilla tree-LOD format was deliberately NOT reverse-engineered (risky, unvalidatable) — flat cards in .bto is the reliable Skyblivion path.
- **GOTCHA**: `LODGenx64.exe` runs with cwd=external/lodgen/ → `PathData=` MUST be absolute (`Path(output_dir).resolve()`) or it fails its Data-dir check (exit -1, log "No Data directory"). LODGen's "Oh crap N = N = ..." stderr spam is a harmless degenerate-triangle notice, not an error.
- **GOTCHA — a geometry-rooted NIF kills the ENTIRE worldspace's object LOD** (found 2026-07-27, Morrowind_ob.esm): `LODGenx64` casts every LOD mesh's root block to `NiNode` unchecked, so a root that is a bare `NiTriShape`/`NiTriStrips` throws `System.InvalidCastException: Unable to cast … NiTriShape to … NiNode` **on a worker thread, unhandled** → the process dies, writes NO log (the stale log is the previous worldspace's, so it looks like LOD "just didn't run"), and the worldspace ends up with a single junk `.bto`. Two 4-triangle `bcscum02/03.nif` scum patches cost Morrowind_ob all 75,316 of its LOD references. Two independent guards now exist:
  1. `nif_converter` wraps a geometry root in a `NiNode` before the usual `NiNode→BSFadeNode` step (vanilla census: 400/400 sampled Skyrim meshes have a NiNode-derived root — `BSFadeNode` 340, `NiNode` 55, `BSMasterParticleSystem` 2, `BSLeafAnimNode` 3; **zero** geometry roots, so this is invalid for Skyrim regardless of LODGen).
  2. `lod_gen._lod_mesh_is_safe()` screens every listed mesh's root block and drops (with a warning) anything unreadable or non-NiNode, so one bad mesh can never again cost a whole worldspace.
  Note `lod_far_gen` legitimately refuses to build a `_far.nif` for shapes under `_MIN_SRC_TRIS` (20 tris) — such models simply get no LOD entry; a **stale** `_far.nif` left from an older run is what got listed here.
- **`external/lodgen/LODGenx64.exe` is 3.0.36.0 — it REPLACED the 2.2.0.0 build of the same name** (2026-08-09; the 2.2 exe was deleted, so an old checkout's `LODGenx64.exe` is a different program). The guards above screen the *root block*, but a mesh can still fault LODGen deeper in the walk — and 2.2 handles **no** exceptions anywhere, so any such throw on a ThreadPool worker kills the process and loses every tile not yet written. Nehrim: 28 of 418 tiles, twice in a row, from ONE model (`LeyawiinHouseLower01`, 5 refs game-wide) throwing `ArgumentOutOfRangeException` in `LODApp.TransformShape` → `IterateNodes` → `ParseNif`. 3.0.36.0 catches the same fault **per object**, prints `Error processing <EditorID>`, and finishes the worldspace, so one bad model costs only its own distant LOD (it pops in at load distance).
  - Verified equivalent before switching: on identical input both versions emit the same tile set with the same block structure (39 `BSSegmentedTriShape`/`BSMultiBoundNode` per tile, NIF 20.2.0.7); 3.x only reduces slightly tighter (48,993 vs 49,527 tris on the sampled tile).
  - **3.x's exit code is NOT a success signal**: 0 on a clean run, but nonzero when any object failed *even though the bake completed and every tile was written*. `run_lodgen` therefore judges success by `.bto` files present in `PathOutput`, and logs the skipped EditorIDs as a warning.
  - The mesh is not at fault — repairing `LeyawiinHouseLower01`'s tangent flag and recomputing its missing normals each made 2.2 crash *earlier*. Don't chase the mesh; the bug is inside LODGen.
- **A plugin's LOD bakes its MASTERS' models too**, and those textures were only converted into the master's output (Morrowind_ob places Oblivion architecture in its own worldspace → 117 `.bto`-referenced textures missing). `_fill_missing_lod_textures(master_tex_roots=…)` copies them in, and the normal-map synthesis prefers a master's real `_n.dds` over a flat fallback. Note `master_dirs` is set only when a master owns the WORLDSPACE, so the texture fallback uses its own `master_texture_dirs` argument (always this plugin's masters) — do not conflate the two.
- **Terrain LOD texture lookup is independent of the dependency chain.** <a id="terrain-lod-texture-lookup"></a> Tamriel Rebuilt's terrain LOD baked flat grey (RGB 132/130/129, std 1.2) on 137 of 1,037 WrldMorrowind level-4 tiles, and partly grey on far more: TR's terrain names vanilla Morrowind land textures (`tes4\Tx_*.dds`, 31k of 61k TR base layers) that only `Morrowind-Morroblivion-Compatibility.esp\textures` ships, and that patch has NO masters, so it was never a "supplier" of the worldspace and its tree was never on the compositor's root list; `load_texture_rgb` then returned its 128 fallback. `create_lod._all_plugin_dirs` now hands every converted plugin tree to both the terrain compositor (`extra_texture_roots`) and the object-LOD fill (`master_texture_dirs`), which also stops `_fill_missing_lod_textures` re-synthesizing `tes4\default_n.dds` (Oblivion.esm ships it, and Oblivion.esm is the owner's MASTER, not a dependent). Suppliers still scope MESHES. Two latent defects fixed in the same pass: `build_ltex_texture_map` keyed LTEX/TXST by raw per-file ids and resolved TNAM per file, so an override LTEX (the grass plugin's 180) landed with an empty diffuse and 32 TR layers naming Morroblivion textures at TR's index byte missed; ids are now load-order-normalized through `formid_remap_table` (LAND layer ids included, via `decode_land_layers(remap=...)`) and TNAM resolves against the union of every listed file's TXST. NOT yet in-game verified.
- `_promote_lod_textures` copies .bto-referenced textures to the textures root AND synthesizes missing NORMAL maps (`_a_n.dds` atlas normals + any missing `_n.dds`) from the source normal or a flat normal — LODGen writes atlas diffuse but not atlas normals, so object LOD would render unlit without this.

**World map** uses terrain LOD (all levels incl. LOD32) + object LOD. WRLD NAM0/NAM9 bounds are RAW float world units (NOT /4096) and MNAM map dims must be correct — both verified in the output ESM.

## Lava surfaces — Oblivion realm water rendered as actual lava (2026-08-23)
<a id="lava-surfaces-oblivion-realm-water"></a>

**Skyrim's water shader physically cannot render lava.** The complete
`BSWaterShaderPixelConstants` table, read out of SkyrimSE.exe at `0x1455789`,
is: `ShallowColor DeepColor ReflectionColor FresnelRI CameraData ProjData
VarAmounts SunDir SunColor NumLights LightPos LightColor WaterParams
DepthControl SSRParams`. **No diffuse texture slot and no emissive term** — the
three colors only tint a reflection/refraction result. All 93 `NNAM` entries
across vanilla Skyrim's 34 WATR records name the same `DefaultWater.dds`, which
is the normal/noise map, not a color map. Oblivion's lava colors are DARK
(`shallow=(79,12,2)`) precisely because they tinted a bright emissive texture
Skyrim has no way to sample, so a faithful WATR port renders as dark water.
Chasing better WATR values is a dead end.

**Bethesda's own answer is geometry.** Dawnguard's Aetherium Forge
(`DLC1Bthalft01`) layers two things: `LavaSettings` (WATR, reached by the
cell's `XCWT`) for the PHYSICS — swim, damage, fog, plus an `INAM` image space
for being submerged — and `DweSpecialForgeLava01/02/03.nif` for the LOOK, an
ordinary mesh with a `BSEffectShaderProperty`:
`source_texture=WavyTurbulence01.dds`, `greyscale_texture=GradHotCoals.dds`,
`emissive 1,1,1 × 2.0`, and a `BSEffectShaderPropertyFloatController` on
U Offset. `Skyrim.esm`'s own `LavaWater` record is **dead data** — zero cell,
worldspace, or REFR references; the only "lava" strings in Skyrim.esm are
Olava the Feeble.

**Our implementation**: `asset_convert/lava_surface.py` generates the plane,
`tes5_import/actors/lava_placement.py` places it. Oblivion's `oblivionlava06.dds` is
already full color (DXT1 blocks decode to `(230,97,49)`, `(222,64,32)`; mean
channel spread 151/255), so it goes straight into `source_texture` and the
greyscale-to-palette path is NOT used — setting `slsf_1_greyscale_to_palette_color`
without binding a gradient samples a missing texture.

**Lava is identified from AUTHORED data only**: a WATR is lava when
`MNAM.MaterialID == "lava"` (Oblivion.esm: exactly `OblivionLavaTest01` and
`OblivionCitadelLavaPlane`; Nehrim: `LavaLow`, `LavaDurchsichtig`). A cell gets
a plane when its `XCWT.Water` is such a record, or — failing that — when it
inherits one via its worldspace's `NAM2.Water`, which is how the engine itself
resolves a cell's water. **Never infer from the worldspace being an "Oblivion
realm"**: that flag says nothing about whether the water is lava. Oblivion.esm
yields 7,765 planes (7,719 exterior on the 4096-unit cell grid, 46 interior at
their authored `XCLW` heights).

### Three bugs that each cost an in-game test cycle — check these FIRST

All three produce a mesh that is perfectly valid on disk, loads without crash
or warning, and is silently wrong in game:

1. **No `BSXFlags` on the root → the scroll never runs.** Skyrim only ticks a
   mesh's time controllers when the root sets BSXFlags **bit 0 (Animated)**.
   The controller sits in the file and the texture is frozen. Vanilla lava
   ships `BSX=1` (collisionless + animated). Identical to the fire-invisibility
   root cause above — the same trap, twice.
2. **Reversed winding → invisible from above.** With `a=(x,y)`, `b=(x+1,y)`,
   `c=(x,y+1)`, winding `(a,c,b)` yields a **−Z** normal and the plane
   backface-culls exactly where the player stands. Correct is `(a,b,c)` /
   `(b,d,c)`. Compute the cross product; do not trust the comment.
3. **Controller `target` unset → animation never binds.** Vanilla sets
   `.target` on every shader float controller, as does `nif_converter.py` at
   all six of its own sites.

Also match `texture_clamp_mode = 0xFF03` (both vanilla and our own working
converted meshes; a bare `3` differs) and controller `flags = 0x48`
(Active | Compute Scaled Time — without the scaled-time bit the curve does not
advance). `NiFloatInterpolator.float_value` may be `0.0`: our shipped,
in-game-confirmed scrolling meshes (streetlamps, flame atronach) use `0.0` and
animate fine, so it is **not** the blocker it first appears to be.

### <a id="lava-shader-flags"></a>The shader flags are copied from Dawnguard, minus one bit

`flags1 = 0x80000010`, `flags2 = 0x21` on Dawnguard's `DweSpecialForgeLava01`,
copied rather than guessed. We set `slsf_1_z_buffer_test`,
`slsf_2_z_buffer_write`, `slsf_2_vertex_colors` and `slsf_2_double_sided`, and
deliberately DROP `slsf_1_greyscale_to_palette_color` (0x10): that bit tells the
shader to look the source texture's greyscale value up in `greyscale_texture`,
and with no palette bound it samples a missing texture. Our source is already
full color, so it stays off and `greyscale_texture` stays empty.

`slsf_2_vertex_colors` obliges the geometry to CARRY vertex colors — the engine
reads them as a per-vertex multiplier, so opaque white (1,1,1,1) leaves the
texture untouched. Declaring the flag without the data is the mismatch case.

`slsf_2_double_sided` is not cosmetic: the winding faces up, but the player
stands IN the lava (the WATR still governs the swim) and sees the plane from
BELOW at that moment, where a single-sided surface disappears entirely.

Guarded by `tests/test_lava_surface.py` (5 tests, each asserting one of the
silent-failure properties).


## FO3/FNV keys LOD by EditorID

**Code:** `asset_convert/lod/terrain_lod_falloutnv.py`

`shipped_lod_worldspaces` treats the source game's own shipped LOD assets as
the authority on which worldspaces deserve distant LOD. Oblivion and Nehrim key
those tiles by the worldspace's **decimal FormID**, flat in one directory:

    meshes\landscape\lod\113463.-32.-32.32.nif
    textures\landscapelod\generated\<formid>.*.dds

FO3/FNV key the same assets by **EditorID**, one directory per worldspace:

    meshes\landscape\lod\wastelandnv\wastelandnv.level4.x-2.y3.nif
    textures\landscape\lod\freesidefortworld\normals\...

A FormID-only scan therefore finds nothing in a FO3/FNV tree, so
`lod_capable_worldspaces` returns empty and the LOD dialog offers no
worldspaces at all. The reported reason -- "ships no distant LOD of its own" --
is measurably wrong: FalloutNV.esm extracts **4,394** LOD meshes across **18**
worldspace directories, against Oblivion's 104 files, and `wastelandnv` is
one of them.

Both layouts are now scanned. The tile count is only a relative weight used to
rank worldspaces, so every file under a worldspace's directory counts,
`blocks` and `normals` subfolders included.


## <a id="write-lodgen-input-master-modes"></a>`write_lodgen_input`: master modes and `only_cells`

**Code:** `asset_convert/lod/lod_gen.py` (`write_lodgen_input`)

`master_dirs` lists the converted output dirs of this plugin's MASTERS. An
override plugin re-uses its masters' records wholesale, so every ref whose LOD
mesh the master already ships is DROPPED: the master's own LOD run baked it,
and re-baking would ship a duplicate copy of the master's entire object LOD to
gain the handful of objects this plugin actually introduces.

`replace_tiles` turns that off. When a plugin REPLACES whole tiles (it changed
cells the master also covers), the tile it writes is the only one the engine
loads for those cells, so it must contain the master's objects as well --
otherwise every tree, rock and building in the rebuilt tiles disappears. The two
modes are mutually exclusive: skip the master's objects only when shipping tiles
ALONGSIDE the master's.

`master_mesh_dirs` is where MESHES are sourced from, and unlike `master_dirs` it
is set even when THIS plugin owns the worldspace. LODGen resolves every listed
mesh under the one PathData root it is given, so a master-owned model must be
copied into this tree to be listable at all.

`only_cells` restricts the listed references to those that can land in a tile
the run actually KEEPS. Without it an override plugin lists the master's entire
worldspace, LODGen bakes every tile, and `_prune_unaffected_tiles` deletes
almost all of them -- **ElsweyrAnequina fed 189,702 references to bake 997 tiles
and kept 127; DLCBattlehornCastle kept 8.** The refs are still needed
(`replace_tiles` means rebuilt tiles must carry the master's objects), just only
within the surviving tiles' footprint.


## <a id="prescreening-the-lodgen-input"></a>Prescreening the LODGen input

**Code:** `asset_convert/lod/lod_gen.py` (`_prescreen_meshes`,
`_screenable_mesh_paths`)

The LODGen-input loop screens each unique base's meshes with a NIF header read,
and those reads are latency-bound rather than CPU-bound: **11.5 s of Tamriel's
13.5 s** in this function was `_io.open` alone, serialised one mesh at a time.
Warming the safety cache concurrently first removes that.

Only paths certain to be screened are prefetched -- the model plus its LOD
tiers, derived with the same helpers the loop uses -- so the prefetch can only
remove work, never change which meshes are judged safe.

`master_meshes` must be among the searched roots. The loop STAGES meshes from
there into the bake tree and screens immediately afterwards, so a warm-up that
looked only at the destination cached "missing" for every mesh not yet staged.
That is exactly how the shared LOD mod lost object LOD in all 18 worldspaces.

The same reasoning applies to LOD-suffix resolution: see
[Object LOD](#object-lod-suffix-differs-by-game) -- the bake tree starts EMPTY,
so resolving a suffix against it alone always answers `_far`.


## <a id="object-lod-suffix-differs-by-game"></a>Object LOD: `_lod` in FO3/FNV, `_far` in Oblivion

**Code:** `asset_convert/lod/lod_gen.py` (`far_nif_path`, `LOD_SUFFIXES`)

The two games name the per-object LOD mesh differently:

| Game | full model | object LOD mesh |
|---|---|---|
| Oblivion / Nehrim | `foo.nif` | `foo_far.nif` |
| Fallout 3 / New Vegas | `foo.nif` | `foo_lod.nif` |

`far_nif_path` knew only the Oblivion form, so for FO3/FNV the existence test
in `generate_missing_far_nifs` was never true and EVERY Visible-When-Distant
object was treated as missing LOD and QEM-decimated from its full-res mesh.

Measured on FalloutNV.esm's export against `STAT.txt`:

| | count |
|---|---|
| STAT records with a model | 6,790 |
| flagged Visible When Distant (`0x8000`) | 469 |
| of those shipping a hand-authored `_lod.nif` | **469 (100%)** |
| of those shipping a `_far.nif` | **0** |
| non-VWD statics that also ship a `_lod.nif` | 85 |

Scanning the game's own BSAs confirms the convention is not an export artifact:
`Fallout - Meshes.bsa` holds 0 `_far.nif` against 343 `_lod.nif`;
`Fallout3 - Meshes.bsa` holds 2 against 246 (the two are `washmonumentlod_far`
and `ravecity_far`, one-offs rather than a convention).

The loss was real, not merely wasted CPU: these are hand-authored silhouettes
with merged geometry and dedicated low-res LOD textures. A decimation of the
full-res mesh keeps the interior detail the artist deleted and keeps full-size
diffuse/normal maps, so distant Vegas came out both uglier and heavier than the
meshes already sitting on disk.

`far_nif_path` now takes an optional `meshes_root` and returns the first of
`_LOD_SUFFIXES` present there. The split matters:

- **Resolution** sites pass the root, so an authored `_lod.nif` wins.
- **Generation** sites pass nothing and still get `_far.nif`. We must never
  write over Bethesda's authored mesh, and the `.nif.generated` marker that
  protects hand-crafted files from `force_regen_generated` keys off that path.

Oblivion cannot regress: it ships no `_lod.nif` at all, so the first candidate
never matches and every lookup falls through to the same `_far.nif` as before.

`_tier_path` takes the suffix it is stripping for the same reason -- assuming
`_far` against a `_lod` path yields `foo_lodfar8.nif`.


## <a id="authored-lod-texture-names"></a>An authored `_lod.nif` names a `_lod` texture we never shipped

**Code:** `_destem_lod_texture`, `_copy_lod_destem` in `asset_convert/lod/lod_gen.py`

FO3/FNV's hand-authored `_lod.nif` meshes reference their own low-res textures
(`roadwasteland01_lod.dds`, 128x128 DXT1). Bethesda shipped pre-baked tiles and
dropped those sources from the shipping BSAs, so LODGen has nothing to sample
and the roads bake untextured -- correct up close, blank at distance.

Measured over FalloutNV's 698 `_lod.nif`/full-mesh pairs: **83** name at least
one missing texture, **168** missing references in all.

The relation is AUTHORED, not guessed: strip `_lod`/`lod` from the texture
STEM, ahead of any map suffix.

```
roadwasteland01_lod.dds   -> roadwasteland01.dds
roadwasteland01_lod_n.dds -> roadwasteland01_n.dds
```

| bucket | missing refs | destem resolves | unresolved |
|---|---|---|---|
| base game | 96 | **78** | 18 |
| FO3 DLC leftovers (`dlcanch*`, `dlcpitt*`) | 72 | 0 | 72 |

The 72 DLC-leftover references are named by ZERO STAT records and are already
filtered out by `referenced_models`, so they never reach a tile. Of the 18
unresolved base-game references, the reachable sets are the satellite dishes,
`atomicwranglerext`, `mrsingledebris02` and `nv_mrcurvedmg01`; `crawlerplatform`,
`monoraildclod01/02`, `dome_lod`, `nv_noso_rowhouse_plaster2` and
`dlc03craextbase` are named by no STAT record at all.

**Do NOT take "a texture from the sibling mesh" instead.** The full-res
`wastelandroad3wayrb.nif` carries BOTH `roadwasteland01.dds` and
`architecture\urban\edgetrim01.dds`, so a per-MESH pick paints road LOD with
wall trim. The substitution has to be per TEXTURE, by name.

Substituting the full-size image is safe because the `_lod` mesh's UVs sit in
the same [0,1] space and map the same surfaces -- it is the same picture at a
higher resolution, which a distant mesh does not need but is not harmed by.

Do not add TTW's BSA as a source: it belongs to `TaleOfTwoWastelands.esm`, a
separate plugin, and stem-matching correctly excludes it.


## <a id="generated-far-nif-belong-to-the-lod-mod"></a>GENERATED `_far.nif` belong to the LOD mod

**Code:** `generate_missing_far_nifs` (`gen_meshes_dir`), `lod_gen.generate_lod`,
`LOD_DIR_NAME` in `asset_convert/lod/sibling_lod.py`

A derived `_far.nif` is a bake-time intermediate, not a shipped asset, so it is
written into `output/AutoConvertLOD/meshes/` rather than the plugin's tree.

### Why one LOD folder, not one per plugin
<a id="one-lod-folder-not-one-per-plugin"></a>

A LOD tile is a file on a fixed grid keyed only by worldspace and coordinate,
so every plugin editing a worldspace produces the SAME tile paths. Per-plugin
output therefore meant rival copies of one file, with the mod manager's install
order silently picking a winner. Generating once for the whole load order leaves
exactly one copy of each tile: no overwrite to win, and no merge pass to
reconcile it afterwards.

What lives in `AutoConvertLOD` is what belongs to the whole load order —
LODSettings, the baked `.btr`/`.bto`/`.dds` tiles, and every GENERATED
`_far.nif`. An AUTHORED `_far.nif` is the plugin's own art and stays with it.
`ZZZ Merged Sibling LOD` is the previous merged-tile folder, recognised only so
an existing install can be cleaned up; nothing writes there any more.

A plugin's own TEXTURES never belong here either. They were shadowed into this
tree to flatten detail-overlay alpha, which cost 401.6 MB of mipless
uncompressed DDS and put 192 paths in two mods at once; that fix has been
removed entirely. See
[asset_convert_shader.md](asset_convert_shader.md#detail-overlay-diffuses).

### <a id="the-bake-never-writes-a-plugin-folder"></a>The bake never writes into a plugin's folder

Everything the bake GENERATES goes here, textures included: the opaque
`<name>_lod.dds` overlay copies, rendered tree billboards, and the flat
`<tree>_n.dds` billboard normals. Each has a path of its own, so none shadows a
plugin file. Plugin folders are read-only inputs (`find_texture` searches
`tex_roots` in order; only `tex_roots[0]`, this mod, is written).

They used to land in `output/<plugin>/textures/`. That tree is what `--pack-only`
archives, and the bake runs after it, so a BSA install lacked them while a loose
install had them: the "missing rock / distant tree texture" reports. Measured in
the plugin folders before the move: Oblivion 25 `_lod` copies (18 rocks) and 117
flat normals, FalloutNV 130, TWMP 72, Unique Landscapes 63, ElsweyrAnequina 41,
Morrowind_ob 16. `tools/lod/render_tree_billboard.py` writes here for the same
reason.

### <a id="render-a-missing-billboard"></a>A tree without a billboard gets one rendered, never decimated

Decimating a canopy is catastrophic at LOD scale: the card is 8 verts, the
decimated tree is 25-330 KB, and it is baked once per placement. Censused across
the load order, 113 such trees accounted for 3.35 GB of baked geometry that
becomes 0.05 GB as cards (63x lighter); `dementiatree10l` alone, 8,006
placements in one level-16 tile, drove that tile to 663 MB. So
`_far_nif_worker` renders the missing billboard and decimates only if rendering
fails. The render resolves leaf textures against every sibling output folder, so
a plugin's trees find their master's textures.

Measured before the change. Scanning 28,828 non-`_far` files (meshes, `.bto`
tiles, records) across FalloutNV, Oblivion and AutoConvertLOD for any reference
to a `_far`/`_far8`/`_far16`/`_lod` mesh found **one** hit: `Oblivion.esm`
naming its own authored `TowerSmall02_far.NIF` and siblings. Cross-checked
against 6,827 record MODL paths from STAT/TREE/FURN/ACTI/DOOR, **zero**
`_far.nif` -- authored or generated -- is ever a record's model. Tiles embed
geometry and name only textures, so nothing loads a generated `_far.nif` at
runtime.

They are also rebuilt every run: `generate_lod` passes
`force_regen_generated=True`, and every derived file carries a
`.nif.generated` marker (FalloutNV 1311/1311 generated; Oblivion 605/762).

| plugin | `_far` | generated | authored |
|---|---|---|---|
| FalloutNV.esm | 1,311 | 1,311 | 0 |
| Oblivion.esm | 762 | 605 | **157** |

**The 157 authored files must stay in the plugin.** Oblivion ships hand-made
`_far.nif` (`seisland_far.nif`, the Citadel tower set) with no marker, and
`Oblivion.esm` itself references them -- they are that plugin's own art.
`_is_generated` is the discriminator, so only the generated branch is
redirected; the source tree is still read for full models and authored LOD.

Two further gains: the staging round-trip
(`_import_master_mesh` / `_drop_staged_master_meshes`) becomes a no-op for
generated files, since they are written where the bake already looks; and they
stop colliding in the shared namespace, which is where
`tes4/furniture/lowerclass/lowerbar02_far.nif` was measured differing between
FalloutNV and Oblivion.


## <a id="lod-suppliers-vs-contributors"></a>Overlay scoping must not scope ASSETS

**Code:** `_plan_jobs` in `tools/release/create_lod.py`

Two different questions get asked about the plugins around a worldspace's
owner, and answering both with one list loses textures.

*Which plugins are overlaid as RECORDS* is deliberately narrow. Depending on a
worldspace's owner is not the same as editing it: `Morrowind_ob.esm` rests on
`Oblivion.esm` and so passes the dependency gate for all 18 of its
worldspaces, while placing nothing in any of them — 18 parses of a 206 MB file
to merge zero records. A plugin whose ESM cannot be read is kept rather than
dropped, because an unnecessary overlay costs time and a missing one costs LOD.

*Which plugins SUPPLY assets* is wider, and scoping it the same way is wrong. A
plugin that places no references can still define the base objects that another
plugin's references point at. The Morroblivion compatibility patch is exactly
that shape: **3,215 base records and no CELL or REFR dump at all**. Filtered
out of the asset roots, it took its texture tree with it, and LODGen reported
**73 LOD textures missing** — every one of them a file sitting in
`output/Morrowind-Morroblivion-Compatibility.esp/textures/tes4/`.

So `_plan_jobs` returns both: `contributors` (scoped, overlaid as records) and
`suppliers` (every dependency-legal plugin, feeding `master_mesh_dirs`,
`master_texture_dirs` and `far_nif_dirs`).

The dependency gate itself is not optional in either list. A plugin that does
not rest on the owner cannot legally touch its worldspace, and overlaying one
anyway merges two unrelated games: `Nehrim.esm` is standalone, and stacking it
onto Oblivion's `TES4Tamriel` would pull its FormIDs into Cyrodiil's tiles.

## <a id="lodgen-rejects-animated-roots"></a>LODGen rejects animated NiNode roots

**Code:** `_LODGEN_BAD_ROOTS` / `_ninode_root_names` in `asset_convert/lod/lod_gen.py`

`_root_is_ninode` excludes meshes whose root block would crash LODGen's
`NiNode` cast. It derived the accepted set from the pyffi class tree — every
`NiNode` subclass — and that is too generous.

`NiBSAnimationNode` **is** a `NiNode` subclass (`NiBSAnimationNode -> NiNode ->
NiAVObject`), so it passed the guard, and LODGen still threw
`NullReferenceException` in `ParseNif` (`LODApp.cs:1386`). These are
Morrowind-era animated roots that survive conversion intact: the four models
that killed a Tamriel Rebuilt bake one after another —
`T_Mw_FloraOW_Bulbshroom_01`/`_03`, `T_Glb_TerrWater_Waterfall_01`/`_03` — all
have one.

Censused over 40,962 converted meshes (Tamriel Data + Morroblivion):

| Root type | Count |
|---|---|
| BSFadeNode | 39,754 |
| NiNode | 1,151 |
| NiBSAnimationNode | 56 |
| NiSwitchNode | 1 |

57 meshes, but any ONE of them entering a bake costs the whole worldspace's
object LOD — the failure is not proportional to the count.

So the derived set is filtered by `_LODGEN_BAD_ROOTS`. The excluded types are
the ones whose semantics are a controller or a selector rather than a plain
transform: animation (`NiBSAnimationNode`, `NiBSParticleNode`), runtime
selection (`NiSwitchNode`, `NiLODNode`, `NiBillboardNode`), and the non-render
roots (`RootCollisionNode`, `AvoidNode`). An excluded mesh loses only its own
distant LOD and pops in at load distance, which is what the pre-existing
`skipped_unsafe` path already does for unreadable meshes.

This is the pre-flight half of the defence; the retry
([#lodgen-nullreference-retry](#lodgen-nullreference-retry)) covers roots that
pass the guard and still throw.

## <a id="lodgen-nullreference-retry"></a>LODGen dies on NullReferenceException, and the retry

**Code:** `run_lodgen` in `asset_convert/lod/lod_gen.py`

`LODGenx64.exe` 3.0.36.0 replaced 2.2.0.0 because 2.2 handles no exceptions: a
model it cannot parse throws on a ThreadPool worker and kills the process, so
every tile not yet written is silently lost. Measured on Nehrim: 28 of 418
tiles baked, twice in a row, because of ONE model (`LeyawiinHouseLower01`, 5
references in the entire game). The fault is inside LODGen, not the mesh —
repairing that model's tangent flag and recomputing its normals each made 2.2
crash EARLIER.

3.x catches `ArgumentOutOfRangeException` per object, prints
`Error processing <EditorID>` and carries on. It does NOT catch
`NullReferenceException`, which unwinds the parallel loop and ends the run with
the same total loss.

Measured on the Morroblivion + Tamriel Data + Tamriel Rebuilt load order: an
input of **286,985 references** produced **26 level-4 tiles in 7 seconds** and
no level 8/16/32 at all, because `T_Glb_TerrWater_Waterfall_01` threw. The run
looked clean — exit code carries no signal (3.x returns nonzero whenever any
object failed, even on a complete bake), and "tiles > 0" was satisfied by the
26. In game that is a worldspace with essentially no distant objects.

So a run whose output contains `NullReferenceException` is retried with every
model named in an `Error processing` line stripped from the input
(`_drop_lodgen_refs`, matching the base EditorID at field index 9 of a
reference row). Each attempt bans one more faulting model, bounded at 4; a
clean run never retries. If it still dies, `run_lodgen` now returns False
instead of reporting success on a truncated bake.

This complements the pre-flight `skipped_unsafe` check, which excludes meshes
that are unreadable or have a non-NiNode root. That predicts the crashes it
can; the retry covers the ones it cannot.

### <a id="lodgen-output-must-stream"></a>Its output must be STREAMED, not captured

Parsing the output for `Error processing` and `NullReferenceException` is why
it is piped rather than inherited — a piped child also cannot pop up its own
console window under the console-less GUI launcher. But `capture_output=True`
withholds every line until the child exits, and a worldspace bake runs for
many MINUTES: the Morrowind run above printed its whole per-tile progress log
(`Finished LOD level 4 coord ...`) in one dump at the end, so the pipeline
looked hung throughout. `run_streamed` (`subprocess_flags.py`) echoes each
line as it arrives and still returns the full text the retry logic parses.

`--skyblivionTexPath` is deliberately NOT passed: it prepends an extra `tes4\`
to texture paths already under `textures	es4\`, doubling the prefix and
causing null-pointer crashes.

## <a id="lod-for-plugins-that-only-edit"></a>LOD for plugins that only EDIT a worldspace

**Code:** `_add_edited_worldspaces` in `asset_convert/lod/sibling_lod.py`

`lod_capable_worldspaces` treats the SOURCE GAME's shipped LOD assets as the
authority on which worldspaces deserve LOD. That reasoning holds for
Oblivion-format content, where every plugin extending a landmass ships LOD for
the master's worldspace, and it is why Oblivion LOD has always been correct.

It fails for a plugin that adds land to someone else's worldspace while
shipping no LOD of its own. Morroblivion ships Oblivion-format LOD for
`WrldMorrowind` and qualifies. Tamriel Rebuilt is Morrowind-native, overrides
that same worldspace (`00380000`), and ships no LOD assets at all, so
`shipped_lod_worldspaces` returned `[]` and it contributed nothing to the bake.

Measured: TR holds **10,368 LAND records** over grid X -42..99, Y -116..67,
but the baked tiles covered only X -64..44, Y -32..56 -- Morroblivion's island.
Every TR cell east, west and south of it rendered with no distant terrain.

So after the per-plugin scan, a plugin joins any ALREADY-QUALIFIED worldspace
it puts exterior cells in. The qualifying judgement still comes from a plugin
that ships LOD -- this only widens who contributes to a worldspace already
being built, and never invents one. That keeps the debug-worldspace false
positives out (`TestGatekeeper` et al. are qualified by nobody) while letting
Morrowind-native content ride on the grid its sibling established.

Membership is read from the CELL dump's `ParentWRLD`, not the converted ESM:
the scan runs before the bake and for plugins that may not be converted yet,
and the dump is the same authored data the extent pass measures from.
## <a id="child-worldspaces-with-their-own-lod"></a>Child worldspaces with their own LOD

`detect_terrain_worldspaces` skipped every WNAM child on the belief that it
renders inside its parent's LOD grid. That holds only when PNAM sets *Use LOD
Data* (0x02), and it only looked true because the importer wrote no PNAM at
all, which the engine takes as "use everything from the parent"
([tes4_export_falloutnv.md](tes4_export_falloutnv.md#child-worldspaces)).

FNV ships LOD for 9 of its 10 child worldspaces, all authored PNAM `0x0004`
(map only): FreesideWorld 40 tiles, FreesideNorthWorld 27, FreesideFortWorld
27, TheStripWorldNew 337, WastelandNVmini 337, Lucky38World 135,
BoulderCityWorld 17, GamorrahWorld 1. The converted output had terrain LOD for
WastelandNV alone. The scan now reads PNAM from the converted ESM and skips a
child only when it borrows LOD, or carries no PNAM (an ESM written before the
importer emitted one). Oblivion's children stay LOD-less: `shipped_lod_worldspaces`
is still the authority and Oblivion ships no tiles for them.


## Why the WRLD scan includes masters

**Code:** `worldspace_edids` in `asset_convert/lod/terrain_lod.py`

An OVERRIDE plugin ships LOD assets for a worldspace it does not itself define.
The GOTY `DLCShiveringIsles.esp` is an 85-byte header-only stub -- every
Shivering Isles record was merged into Oblivion.esm -- yet its BSA supplies
every SEWorld LOD tile. Scanning only the plugin's own WRLD.txt leaves the
FormID lookup falling through to a raw hex id that no downstream EditorID match
can resolve, so the worldspace silently drops out of the LOD set.

A master's records are also NOT reliably a sibling directory: an imported mod's
plugins live inside their mod's shared folder, so `.parent` is that folder
rather than `export/`. Masters resolve through the source registry instead.

## QEM decimation: the budget and its tuning constants
<a id="qem-decimation-tuning"></a>

**Code:** `asset_convert/lod/mesh_decimate.py`, called from
`asset_convert/lod/lod_far_gen.py`.

The budget targets roughly vanilla Skyrim object-LOD density +25%. Vanilla
Tamriel spends **~11,000 bytes of object LOD per CELL at level 4** (measured
from `Skyrim - Meshes1.bsa`); before this pass we spent **~410,000, i.e. 37x
vanilla**, because decimation was structurally broken and every constant had
been clamped down to hide the damage:

- Shapes were decimated **independently**, so shared rims drifted apart and
  tore holes. `_BOUNDARY_WEIGHT` froze both rims to limit the drift, and
  `_MIN_SRC_TRIS`/`_MIN_TRIS` deleted small shapes rather than risk them.
- Collapses kept the **original UV**, so charts were squeezed into a third of
  their proper footprint. `MAX_DEV_FRAC` was clamped to 0.03 to limit the
  shearing, which stalled reduction at ~40% of source verts against a nominal
  8% target.

Both defects are fixed — one welded topology per model, UVs interpolated onto
the survivor — so the defensive constants are gone and the real budget stands
on its own.

### Why welding matters

Decimating a model as ONE welded topology is what keeps it watertight. A rim
shared by two shapes is a free boundary to both: each side chooses different
survivors, the rims drift apart, and the gap is the hole. Welding makes the
shared rim one graph node, so a collapse moves both sides at once.

### The constants

| Constant | Value | Role |
|---|---:|---|
| `WELD_EPS` | 1e-3 | position weld tolerance, game units |
| `MAX_DEV_FRAC` | 0.25 | error floor, as a fraction of the model diagonal |
| `TOPO_BOUNDARY_WEIGHT` | 6.0 | budget multiplier per unit of boundary fraction |
| `_BOUNDARY_WEIGHT` | 1.0 | boundary-edge constraint quadric weight (× len²) |
| `_STITCH_FRAC` | 0.008 | proximity-stitch tolerance, fraction of the diagonal |
| `_STITCH_MAX_EDGE_MULT` | 1.0 | stitch radius cap, in median edge lengths |
| `_EDGE_LEN_REG` | 0.5 | edge-length regularization (× mean face area) |

`_BOUNDARY_WEIGHT` used to be 8.0, to stop the two sides of a shared rim
drifting apart — which it could never do, because each side was decimated
separately and nothing made them agree. Now that a model is one welded
topology the seam cannot open, so it only has to hold genuinely open rims
(window frames, wall tops, leaf-card edges) a little longer than interior
geometry.

`MAX_DEV_FRAC` at 0.25 is deliberately loose: at LOD range (2+ km) a quarter
of the model diagonal is well under a pixel of silhouette.

`TOPO_BOUNDARY_WEIGHT` scales the budget by how much of the model is open rim,
since rim vertices are pinned by the open-rim guard — a 30%-rim building gets
2.8x the vertices of a closed rock at the same ratio.

`_STITCH_MAX_EDGE_MULT` caps the stitch radius so a merge never crosses more
than the scale of the authored detail.

### Stitching: why an exact weld is not enough
<a id="qem-stitch-pass"></a>

An exact position weld only joins vertices that **coincide**. Game models are
not built that way: `piratecabin01` is 14 open sheets that overlap and
interpenetrate — visually one solid cabin, topologically **33 separate
components**, with only 20 of 91 shape pairs having any vertex within a unit of
each other. Decimating that divides one budget among 33 pieces, which grinds
each to nothing and takes whole planks with it.

So nodes that are merely CLOSE are merged, not just identical ones. The
tolerance is relative to the model, because "touching" means something
different on a 100-unit crate and an 8,000-unit fort. The stitch runs before
quadrics are built, so the collapse sees one connected surface and simplifies a
plank into its neighbour exactly as it simplifies one rock face into the next.

**The radius is capped by the model's DETAIL scale, not just its overall
size.** A fraction of the diagonal is right for a building, whose planks are
large and genuinely overlap, but on thin repeated geometry it exceeds the size
of the parts themselves and fuses things that merely pass near each other:
`mainmast01` is rigging with a 2,968-unit diagonal and a 9-unit median edge, so
a 24-unit radius welded separate ropes into one and the collapse dragged them
together. Measured across models, radius / median-edge cleanly separates the
two cases:

| model | ratio | wants stitching |
|---|---:|---|
| `piratecabin01` | 0.38 | yes |
| castle | 0.79 | yes |
| IC wall | 1.78 | no |
| `mainmast01` | 2.62 | no |

Hence the cap at the median edge length. When a group is merged the
representative keeps its **original** position — averaging the group would pull
the surface off the silhouette.

### Why welding is what stops seams tearing
<a id="qem-weld-seams"></a>

`tri_mat` tags each input triangle with the material (source shape) it came
from. It is carried through the collapse unchanged and returned alongside the
surviving triangles, which is what lets a whole model be welded into ONE
topology and decimated together: shared rims between shapes become genuinely
shared graph nodes, so a collapse moves both sides at once and the seam cannot
tear. The triangles are split back out per material afterwards.

Decimating each shape separately instead let the two sides of a seam pick
different survivors and drift apart. Measured on `centrancerockmosslg01`, the
shared boundary went from **32% welded to 9%**, and the gap opened from **3.8
to 93.4 units** — 6% of the object diagonal.

### The four collapse guards
<a id="qem-collapse-guards"></a>

Each guard exists because removing it produced a specific measured failure.

**Open-rim guard.** A boundary vertex may only collapse INTO another boundary
vertex, so an open rim simplifies along itself and stays where the author put
it. The constraint quadrics cannot prevent this on their own, because a
half-edge collapse is charged the SURVIVOR's quadric — they only penalise
moving a rim vertex ALONG its edge line, and say nothing about it being
absorbed upward into the body. Measured on `rockgreatforest1125rdm`, whose 106
rim verts all sit at z=-241.2: without the guard only **12 of 178 rim nodes
survived and the rim rose 162 units** — 34% of the model height — leaving the
rock floating above the terrain.

**Per-component floor.** A model is often many DISCONNECTED pieces —
`piratecabin01` is 33 planks, beams and panels — and a global vertex budget
says nothing about how it should be split between them. Asking for 54 vertices
across 33 pieces is ~1.6 each, far below the 4 a closed piece needs, so the
loop ground whole planks out of existence and the "holes" were missing parts,
not torn surface. Every component gets its own floor of `_COMP_MIN = 4`: four
vertices is the minimum for a closed piece (a tetrahedron), and while a flat
open sheet still reads at 3, one wasted vertex on a plank is nothing against
the plank disappearing.

**Isolation guard.** Decimation must never leave a triangle floating on its
own. A collapse removes the faces containing edge (u,v) and rewrites the rest;
if that would strand any surviving neighbour as a triangle sharing no edge with
another live face, the collapse is refused. Without it a low budget shreds a
surface into loose confetti rather than simplifying it, which reads in-game as
holes with stray triangles floating in them.

**Normal-flip guard.** A collapse is rejected if it would flip an adjacent
face's normal.

### Stranded vertices must be counted
<a id="qem-stranded-verts"></a>

A degenerating face can strand a THIRD vertex — not just `u` or `v` — by taking
its last face away. Those have to be counted, or `alive` drifts above the real
vertex count and the loop keeps collapsing long after the budget is met:
`piratecabin01` asked for 54 vertices and was ground down to **14, losing 10 of
its 14 shapes**.

### UV charts: why the corner UV is mutable
<a id="qem-uv-charts"></a>

A collapse u→v moves the corner's POSITION to v while the corner keeps u's
ORIGINAL UV. The triangle then covers the geometry both vertices used to span,
but its UV footprint is unchanged — so the chart is squeezed into less and less
of the texture as collapses accumulate. Measured on
`rockgreatforest1500fgdrlichen`: the far mesh retains **96.3% of the source's
geometric area but only 32.3% of its UV area**, leaving 80% of triangles below
half the source texel density. On `icexteriorwall02` the density spread reached
**53,303x** — a single-texel streak, which reads in-game as a garbled or
invisible texture.

The fix is a MUTABLE UV per corner that moves with the vertex: when u collapses
into v, the corner's UV becomes the point in u's chart corresponding to v's
position. The face still holds its other two corners, whose UVs are known and
whose positions are unchanged, so the face defines a local affine map from
position to UV; solving it for v's position gives exactly where v lands in this
face's chart. UVs are piecewise-linear over the surface, so this is exact and
keeps the chart's area in step with the geometry it covers. A degenerate face
leaves the UV unchanged.

A corner's UV is per-FACE once it starts moving, since two faces sharing a
vertex can sit in different charts, so each corner gets its own slot.

### Deliberately NO component-pruning fallback
<a id="qem-no-component-pruning"></a>

An earlier version dropped whole connected components smallest-area-first when
collapses stalled above target. That was written when each SHAPE was decimated
alone, so a "component" meant a disconnected island within one shape. Now that
a model is decimated as ONE welded soup, every shape is its own component, and
the same code deleted entire shapes to meet the budget: `piratecabin01` went
from **14 shapes / 2,686 verts to 4 shapes / 15 verts**, and
`ruinshallnxdeadenda01` lost most of its geometry the same way.

Overshooting the budget is far better than deleting parts of the model, so a
shape is simply left heavier than target when the error floor genuinely blocks
further collapses.

### Scalar arithmetic in the inner loop
<a id="qem-scalar-inner-loop"></a>

`cost_of`, `flips` and `uv_at` are written out longhand in plain Python
scalars rather than NumPy. They run ~100k times per shape on 3-vectors, where
NumPy's per-call dispatch overhead dwarfs the arithmetic — `np.cross` alone
spent more time in `normalize_axis_tuple`/`moveaxis` than on the cross product.
The corner UVs are plain `(u, v)` tuples for the same reason.

### The budget must be counted in WELDED nodes
<a id="qem-budget-welded-nodes"></a>

The target handed to `qem_decimate` must be expressed in **welded nodes**,
because that is what the collapse loop counts down. A NIF's vertex array splits
a position once per UV/normal seam: measured across greatforest `_far.nif`,
**310 stored vertices for 62 distinct positions — 5.0x**. So a ratio applied to
the stored count asks for far more geometry than actually exists.

That is what made the far-ring tiers inert. `TIER16`'s ratio of 0.25 against
the stored count worked out to **1.26x the welded count**, so `alive > target`
was false on entry, the loop never ran, and `_far16.nif` was written as a
byte-for-byte copy of `_far.nif`.

## Scoping a LAND scan to one worldspace
<a id="land-scan-scoping"></a>

**Code:** `parse_land_records`, `scan_land_file` in
`asset_convert/lod/terrain_lod.py`.

Overlays are keyed by grid coordinate, so a later file's LAND for a cell simply
replaces the earlier one -- exactly override semantics. That is what lets an
override plugin's regraded terrain reach LOD: DLCBattlehornCastle rewrites VHGT
on **10 Tamriel cells**, and reading only the master left distant terrain showing
the ORIGINAL ground while the loaded cells showed the new ground.

`parse_land_records` resolves the worldspace FormID ONCE, from the file that
DEFINES the worldspace, and hands it to every overlay scan rather than letting
each overlay look it up itself: an override plugin routinely edits a master's
worldspace while shipping no WRLD record, and the unscoped fallback would then
sweep in every OTHER worldspace it carries. Only the base file may fall back to
"take everything" (`allow_unscoped`); for an overlay that fallback is the
corruption measured below.

That FormID is NORMALIZED into the load-order-wide space before it is handed
across, because it is compared against ids from a DIFFERENT file. A raw id from
one file means nothing in another, and comparing an unnormalized one is exactly
the cross-file mistake normalization exists to prevent.

`scan_land_file` tracks CELL grid coords alongside each LAND with a lightweight
group scanner: GRUP type 6 is the cell children group (label = cell FormID) and
type 1 the world children group (label = parent WRLD FormID). The target
worldspace FormID is found first by a fast linear scan; when this file overrides
the worldspace without shipping its WRLD record, its edits live under a type-1
GRUP labelled with the DEFINING file's FormID, so scoping on `known_wrld_fid` is
exact and still excludes every other worldspace the plugin carries.

`known_wrld_fid` is the target worldspace's FormID as resolved from the file
that DEFINES it. An override plugin edits a master's worldspace through the
master's GRUPs — its records sit under a type-1 GRUP labelled with the master's
WRLD FormID — while shipping no WRLD record of its own. Passing the master's
FormID in is what lets those edits be scoped correctly instead of falling back
to a wildcard.

`allow_unscoped` decides what "this file has no such WRLD record, and no FormID
was supplied" means:

- **True** (the default, correct for the file the worldspace is sourced FROM)
  keeps the historical fallback: take every LAND record, because a file scanned
  for its own worldspace may name it differently, and returning nothing would
  silently produce no terrain at all.
- **False** is mandatory for OVERLAYS, where the same fallback is a
  data-corruption bug: it imports the plugin's OTHER worldspaces as if they
  were this one. `Morrowind_ob.esm` ships no `TES4Tamriel` WRLD, so all **5,796
  of its Vvardenfell cells** were collected into Cyrodiil's heightmap,
  overwriting **5,787 of Oblivion's own Tamriel cells** and stamping
  Vvardenfell across central Cyrodiil's distant terrain.

A CELL record an override ships carries only the fields its author changed, so
its XCLC grid coords may be absent. Coordinates are resolved against the coords
already learned from earlier files in load order before falling back to this
file's own.

## LODGen rejects poisoned floats, and drops the whole worldspace
<a id="lodgen-poisoned-floats"></a>

**Code:** `finite` in `asset_convert/lod/esm_scan.py`.

LODGen's C# parser rejects a poisoned line and then emits **NO .bto tiles for
the entire worldspace**, so one bad REFR costs all of its object LOD. Two
distinct poisons appear in real plugins, and they fail differently:

| value | formats as | LODGen error |
|---|---|---|
| NaN (`0x7FC00000`) | `nan` | "Input string was not in a correct format" |
| `-FLT_MAX` (`0xFF7FFFFF`) | a 40-digit literal | "Value was either too large or too small for a Single" |

The second is **finite**, so an `isfinite()` check alone lets it straight
through — hence the magnitude bound as well. `_PLACEMENT_LIMIT` is 1e9;
Oblivion's largest worldspace spans ~2e6 units, so a sane coordinate never
comes close.

TWMP Valenwood/Elsweyr ships **505 REFRs** carrying one or the other in DATA's
RotZ. A MASTER's record reaches this parser without passing the import-side
`get_float` clamp, so both screens are needed here too.

## Level 16 is gated by size, not a header flag
<a id="level-16-is-gated-by-size"></a>

**Code:** `lod_gen._lod_meshes_for`, `lod_far_gen.generate_far_nifs`.

Level 16 is the world-map ring. It used to be gated on record flag
`0x10000000`, which the import set on every STAT/TREE over 1024 units as
"Show in World Map". That flag does not exist (Skyrim.esm sets it on 143 FURN
and 1 STAT of 9,720 — see
[ck_vs_game_missing_objects.md](ck_vs_game_missing_objects.md#no-show-in-world-map-flag)),
so the import stopped writing it. The LOD stage kept gating on it, and from
that commit on every worldspace baked all-empty level-16 tiles: WrldMorrowind
produced 87 tiles of 236 bytes each, and the LODGen input carried zero level-16
model paths across 286,612 rows.

The gate is now a size gate, but its OWN: `LOD16_MIN_SIZE`, separate from
`LOD8_MIN_SIZE` because level 16 sits ~4x further out. Trees already worked this
way, and an authored `_far.nif` bypasses both --
[coarse-ring size gates](#coarse-ring-size-gates).

## The parsed-ESM cache
<a id="parsed-esm-cache"></a>

**Code:** `parse_esm_cached` in `asset_convert/lod/esm_scan.py`.

`generate_lod()` is called ONCE PER WORLDSPACE and used to re-parse the whole
plugin every time. Oblivion.esm ships 18 worldspaces and the parse is **5.7 s
over 613 MB (1,017,612 refs)**, so ~103 s of the object-LOD stage was spent
re-deriving byte-for-byte identical data.

Keyed on `(path, mtime_ns, size)` so a rebuilt ESM is re-parsed rather than
served stale.

The cache holds the BASE plugin and its OVERLAYS together. It used to keep a
single entry, which made the two uses evict each other: the overlay merge
parses every overlay once per worldspace, so a 1-entry cache serving only the
base still re-parsed ~930 MB of overlays 18 times — **114 s measured on the
12-plugin selection, of which 4 s was useful.**

The bound is the number of plugins in one run (a dozen), not a byte budget:
these are compact index structures, and the raw `bytes` object is released
inside `parse_esm` as soon as the scan finishes.

🔴 The returned structures are treated as **READ-ONLY** by callers.
`write_lodgen_input` builds its own per-worldspace views and `generate_lod`
merges overlays into a COPY. If that ever stops being true this must hand out
deep copies instead — the overlay merge in particular MUST NOT mutate what it
is handed, now that the same object is served to the next worldspace.

### The topology-aware budget
<a id="qem-topology-budget"></a>

A flat share of the vertex count assumes every model simplifies equally well,
and they do not. A rock is one closed blob: **12%** of its vertices sit on an
open rim, so almost every vertex is interior and free to collapse. A building
is a pile of open sheets — `piratecabin01` is **30%** boundary,
`ruinshallnxdeadenda01` **47%** — and those rim vertices are pinned by the
open-rim guard.

Give both the same 5% and the rock lands on a clean silhouette while the
building runs out of collapsible interior and tears itself apart. Measured on
the cabin, open edges went **11.5% (source) → 21% → 43%** as the target dropped
500 → 300 → 54.

So the budget scales by how much of the model is rim:

```
topo_scale   = 1.0 + TOPO_BOUNDARY_WEIGHT * boundary_fraction
total_target = clamp(weld_nodes * ratio * topo_scale, _MIN_TOTAL_TARGET, cap)
```

A mostly-closed model keeps the base ratio; a rim-heavy one gets
proportionally more vertices, which is what it needs to still read as itself.

### Object LOD detail presets
<a id="object-lod-detail-presets"></a>

**Code:** `mesh_decimate.LOD_DETAIL_PRESETS`, selected by `lodDetail` in
`conversion_config.json` and by Settings > Distant LOD detail in the GUI.

A preset is `(stitch fraction, ratio, level-4 floor, far-ring floor)`. These are
ONE lever, not four, and moving any alone makes LOD worse, so they all derive
from the same index and can never drift apart.

The proximity stitch runs BEFORE QEM and sets a geometry CEILING; the ratio sets
the TARGET; the floor sets the MINIMUM. Measured on `houselower01` (8,011 source
verts), target vs delivered:

| stitch | ratio | target | delivered |
|---|---|---|---|
| 0.008 | 0.05 | 479 | 479 |
| 0.008 | 0.20 | 1,917 | **610** -- ceiling, ratio wasted |
| 0.002 | 0.20 | 1,917 | 1,620 |

At a stitch of 0.008 the weld crushes 8,011 verts to 636 nodes, so raising the
ratio alone changes nothing. Lowering the stitch alone LOWERS triangle count,
because the budget is computed FROM weld nodes: 0.008 to 0.001 took
`houselower01` from 723 to 315 triangles. Large architecture is where the
ceiling binds hardest -- `outerwallgate01` welds 4,473 to 707 and will not
exceed 651 output verts at ANY target or `MAX_DEV_FRAC`.

| preset | stitch | ratio | L4 | far | triangles | WrldMorrowind LOD |
|---|---|---|---|---|---|---|
| 0 | 0.008 | 0.050 | 24 | 24 | 1.0x | **3.45 GB (MEASURED)** |
| 1 | 0.009 | 0.050 | 48 | 32 | 1.4x | ~3.9 GB |
| 2 | 0.009 | 0.050 | 72 | 40 | 2.6x | ~5.3 GB |
| 3 | 0.008 | 0.050 | 96 | 48 | 3.0x | ~5.8 GB |
| 4 (default) | 0.005 | 0.080 | 96 | 48 | 3.5x | **5.23 GB (MEASURED)** |
| 5 | 0.004 | 0.100 | 96 | 48 | 4.0x | ~6.9 GB |
| 6 | 0.003 | 0.130 | 96 | 48 | 4.7x | ~7.8 GB |

**Preset 0 reproduces the pre-preset settings exactly**, so it is the "same size
as before" option rather than a reduced one. Multipliers are against preset 0.

Size is near-linear in TRIANGLES: fitting three measured WrldMorrowind bakes
(3,482.5 MB / 19.4M tris, 5,187.1 MB / 40.7M, 6,381.0 MB / 67.4M) gives
`MB = 2482 + 59.7 * Mtri`, within 5% on all three. A per-VERTEX model does NOT
work -- marginal cost falls from 180 to 95 MB/Mtri across that range because
seam duplication shrinks as the budget grows, and vertex-ratio estimates ran
25-35% low twice.

Object LOD duplicates each mesh into every tile referencing it -- level-4
`.bto` totalled **5,577 MB against a 163 MB `_far.nif` corpus, 34x** -- so a
detail change costs far more on disk than the mesh corpus suggests. A `.bto` is
near-pure vertex data at a measured **64 bytes/vertex** (six level-4 tiles:
14.6 MB / 240,190 verts).

### The vertex floor is the lever for SMALL meshes, and it is the cheap one
<a id="lod-vertex-floor"></a>

Stitch and ratio only move a mesh whose budget clears `_MIN_TOTAL_TARGET`, so at
the old floor of 24 a full **38% of meshes were pinned** and no preset change
touched them. `tr_terr_rock_rr_18` (97 source verts) emitted a byte-identical
LOD at presets 0 through 3.

Measured over 897 LOD source meshes at one fixed stitch/ratio, raising the floor
from 24 to 64 costs **+3.8% vertices overall** while changing **40.9% of
meshes**, and on those it delivers **1.76x verts / 1.95x triangles**. Floor 96
ALONE, with stitch and ratio untouched, gives **1.86x triangles for 4.43 GB**
where the stitch/ratio pair alone gave 2.10x for 5.07 GB. Per vertex the cost is
identical; the floor simply aims it at the meshes that are visually broken.

The floor binds rocks and non-rocks about equally (22.1% vs 23.2% at one
preset) -- it is a SMALL-MESH problem, not a rock-specific one. Rocks are where
it is noticed because they are numerous and read as silhouettes.

A mesh already under the floor is returned untouched: `_collapse_loop` runs
`while m.alive > target`, so an 8-vertex cube at target 1000 stays 8 verts / 12
tris. The floor never subdivides or inflates.

The far ring carries its OWN floor. `TIER16` caps at 120 verts, so a floor of 96
pins it against that cap and it cannot decimate at all -- which is why level 16
once cost nearly as much as level 8. Holding level 4 at 96 while dropping the
far ring to 48 left level-4 tiles **byte-identical** (114 triangles of merge
noise out of 38M) and cut 520 MB: level 8 to 0.92x, level 16 to 0.76x.

### Decimation DUPLICATES vertices at UV seams
<a id="qem-seam-duplication"></a>

Output size tracks EMITTED vertices, and that count does not fall monotonically
with the budget. `_rebuild` keys output vertices by (weld node, UV, material),
so one position on a UV seam becomes several output vertices. Interior vertices
collapse freely while seam vertices are pinned by the boundary guard, so the
survivors of a hard decimation are disproportionately seam vertices. Measured on
`tr_terr_rock_rr_18` (source 97 verts / 97 unique positions -- no duplication):

| target | emitted verts | unique positions | dup factor | tris |
|---|---|---|---|---|
| 16 | 64 | 16 | 4.00x | 23 |
| 64 | 128 | 64 | 2.00x | 111 |
| 96 | 99 | 96 | 1.03x | 172 |
| 128 | 97 | 97 | 1.00x | 174 |

So a HIGHER preset can emit a SMALLER file with MORE triangles. **This is not a
quantization bug**: the duplicate UVs differ by a median of **0.121** against a
`_UV_QUANT` step of 0.000244, and **0 of 19** clusters sit within one step, so
no loosening merges them. `uv_at` reprojects a moved corner barycentrically per
surviving face, and a vertex absorbing neighbours from different parts of the
chart genuinely lands on different texture coordinates -- collapsing them would
tear the texture. Fixing it needs UV-aware collapse costs, a change to the QEM
contract rather than a bug fix.

The floor is a WELD-NODE budget, so a floor of 96 emits roughly 100-200 actual
vertices once seams are expanded.

### Coarse-ring size gates
<a id="coarse-ring-size-gates"></a>

**Code:** `lod_gen.LOD8_MIN_SIZE`, `lod_gen.LOD16_MIN_SIZE`,
`lod_gen._lod_meshes_for`.

Level 4 has no gate; everything LOD-flagged is drawn there. Level 8 and level 16
each have their own minimum OBND dimension. They were ONE gate at 600 feeding
both rings, which is why level 16 cost almost as much as level 8 despite sitting
~4x further out.

Censused on TES4Tamriel's 606 level-16 qualifying bases (113,129 placed
instances, 1,343 MB of level-16 tiles):

| class | bases | instances | verts/instance | % of tier bytes |
|---|---|---|---|---|
| rock/cliff | 152 | 17,723 | 268 | **67.1%** |
| architecture | 358 | 3,842 | 269 | 14.6% |
| tree (billboard) | 60 | 89,840 | **8** | 10.2% |
| other | 36 | 1,724 | 331 | 8.1% |

**Trees are not the cost.** 89,840 tree instances are billboards at 8 verts each
and carry 10% of the tier; 23,289 non-tree instances carry the other 90% as real
decimated geometry. Rocks alone are two thirds of it, and half of them have an
OBND under 1,390.

Measured savings on TES4Tamriel: level 8 at gate 800 is 1,500 MB (from 2,225);
level 16 at gate 1200 is 474 MB (from 1,343). Together that took the worldspace
from 9.61 GB to 8.05 GB with level-4 tiles byte-identical.

An empty tier column is legal in the LODGen input: `LODApp.cs:939` draws a level
only `if (curStat.staticModels[level].Contains(".nif"))`, so a gated-out object
is simply absent from that ring rather than falling back to a heavier mesh.

### An AUTHORED `_far.nif` bypasses every gate
<a id="authored-lod-bypasses-gates"></a>

Shipping a hand-made LOD mesh IS the decision that the object belongs at
distance, so a size gate must not override it. `has_authored_lod` (no
`.generated` marker beside the file) is the authored signal, and it applies to
every class rather than to any one folder.

Level 16 is what the WORLD MAP renders, so hiding a landmark there removes it
from the map, not just the horizon. Of the 93 architecture bases / 689 instances
the 600-1200 band drops on TES4Tamriel, the authored rule recovers 10 bases / 75
instances -- `ChorrolLODHouse01`, `BravilHouseLOD` and `AnvilHouseGeneric01`
among them, all purpose-built LOD meshes. It cost 76 MB and returned 35% more
level-16 triangles.

It does NOT recover the other 614: `FarmHouse01`, `SkCastleBase01`,
`AnvilDock01` and `SkBridgeSmallEnd` have only generated LOD. A class-aware
exemption was measured for those -- holding architecture at the level-8 gate
keeps all 3,842 instances for ~90 MB -- using `is_architecture`, a PATH SEGMENT
test for Bethesda's own top-level `Architecture` mesh folder (the record type
cannot help: 4,253 of the LOD-flagged bases are STAT, 119 TREE, 10 MSTT).
Not shipped pending an in-game look at the authored rule alone.

## The DDS block codec
<a id="dds-block-codec"></a>

**Code:** `asset_convert/texture/dds_codec.py`

Split out of `terrain_lod.py`: nothing in it knows about terrain, every entry
point takes a pixel array and returns bytes.  It lives under `texture/` because
the parallax height-map writer shares its BC4 encoder.

### <a id="dxt1-is-vectorised-over-blocks"></a>DXT1 is vectorised over blocks

For each 4×4 block the encoder takes the per-channel min and max as the DXT1
endpoints (`c0 > c1`, opaque 4-color mode) and assigns each pixel the nearest
of the four interpolated colors. The palette is re-expanded **from** 565 — the
scalar version built its palette from `c565_to_rgb` of the quantised endpoints,
and matching that is what keeps the output byte-identical.

A 1024² tile is ~65k blocks, and the old per-block Python loop was the single
hottest function in terrain LOD: **1.4s per LOD16 tile, ~33% of all tile time.**

### <a id="dxt1-chunking-is-a-memory-fix"></a>The nearest-palette search is CHUNKED, and that is not an optimisation

The whole-array form allocates `(N,16,4,3)` for the differences plus an
`(N,16,4)` reduction, and `sum` promotes int32 to int64, so a 1024² tile
(65,536 blocks) transiently needs **~80 MB**. That is survivable alone and fatal
in parallel: with one worker per core, **29 of them peaked together and every
level-16 tile died** on `Unable to allocate 32.0 MiB for an array with shape
(65536, 16, 4)`.

Chunking bounds the peak per worker regardless of tile size, and the explicit
int32 accumulator halves what remains. The squared distance maxes at
`3 × 255² = 195,075`, so int32 cannot overflow.

### <a id="bc4-index-selection"></a>BC4 picks each index by NEAREST VALUE, not by arithmetic

`encode_bc4_channel` argmins over the eight palette entries. The obvious
cheaper form — quantise `hi - v` onto sevenths of the range and use the step as
the index — is **wrong**, and `parallax.py` shipped it until it was measured
against the format's own decode rule.

The palette entries are integer **floors** of `((7-i)*hi + i*lo)/7`, so the true
midpoint between adjacent entries sits slightly *below* the exact seventh
boundary. Rounding onto exact sevenths therefore picks the lower entry for any
value just under a boundary.

Measured over 4,000 random blocks: argmin hits the theoretical optimum on
**4000/4000**; the computed index misses on **293** and is never better on any
block. The error scales inversely with the endpoint range — for endpoints
108/100 it is wrong on **3 of the 9** representable values.

That regime is exactly a parallax height field, which is downscaled 2× and
blurred (radius 20/1000) before encoding. On a simulated blurred field
**64.8% of blocks encoded differently and the computed index carried 73.4% more
error**; pure gradients were unaffected, which is why it went unnoticed. There
were no BC4 tests in `tests/test_parallax.py`.

The arithmetic index existed to avoid the cost of searching eight entries in a
per-texel Python loop, which was a real constraint. Vectorising over blocks
removes the tradeoff rather than trading against it: on one machine, one
512² level, the same input, the scalar computed index takes **0.062 s** and the
vectorised search **0.009 s** — 6.7× faster *and* optimal. (The 0.34 s in the
original note was a different machine and is not comparable to either.)

### <a id="one-dds-header-builder"></a>One header builder, three formats

`dds_header()` writes the 128-byte header for any compressed square texture,
parameterised by FourCC and mip count. DXT1 and BC5 (`ATI2`) previously each
hand-rolled the same layout — three copies, with the field names repeated as
comments in each. The layout, in order: magic, `dwSize`, `dwFlags`,
`dwHeight`, `dwWidth`, `dwPitchOrLinearSize` (the TOP mip), `dwDepth`,
`dwMipMapCount`, `dwReserved1[11]`, the 32-byte pixel format (size, flags,
FourCC, five unused masks), `dwCaps`, and four trailing reserved words.

Mipmaps run down to 1×1, as vanilla Skyrim terrain LOD DDS files do. Tile size
matches vanilla per LOD level: 1024 for LOD4/8, 2048 for LOD16/32.

## The purple test, and two ways to fake a purple
<a id="the-purple-test"></a>

**Code:** `tools/audit/lod_texture_resolve.py`

A baked tile embeds geometry and names its textures by path. A named texture
present in neither our output nor vanilla Skyrim renders purple, so scanning
every tile is the only check that proves "no purple LOD" -- a per-defect check
passes while a tile still names something nothing provides.

Measured on the FNV rebuild: **1,760 tiles (1.30 GB), 1,943 distinct refs, 0
purple.** The one ref absent from `output/` is `white.dds`, which resolves in
vanilla (1,540 bytes) -- which is why the check MUST consult vanilla. An
output-only check reports every stock reference as a defect.

Two scanner bugs each invented purples that did not exist:

- **Comparing un-normalized paths.** Terrain tiles name textures
  `data\textures\terrain\...` while object refs are already relative to the
  textures root. Without stripping both prefixes, every terrain tile reads as
  missing: **3,952 false positives**, including files sitting on disk.
- **Matching a substring instead of a whole string.** A regex allowing `.`
  and `-` inside the run can begin inside binary float data and run forward
  into a following `.dds`. It invented `JEYE.Dds` (float bytes at offset
  359,278 glued to a real path's tail) and `default.dds` (from
  `EyeDefault.dds`). Zero NUL-delimited runs in all 1,760 tiles equal either
  name. A texture path is a COMPLETE NUL-delimited run, never a slice of one.

Both `jeye.dds` and `default.dds` appear in earlier purple counts and are
artifacts, not defects. `default_land_texture()` is correctly namespaced
(`<game>\landscape\default.dds`) and is a read path for baking, not a name
written into a tile.

### Two `_lod.dds` are absent upstream, not dropped
<a id="two-lod-dds-absent-upstream"></a>

`falloutnv\architecture\noso\nv_noso_rowhouse_plaster2_lod.dds` and
`falloutnv\clutter\nvsatellitetripod\nvsatellitedishtripodlod.dds` are listed
in `textures_used.txt` but exist in neither `export/` nor `output/` -- only
their `_n` normals shipped. They are named by `_lod.nif` meshes whose base
`.nif` uses entirely different textures (`ConcreteMetalBase01`,
`MetalWorkQuad02a/03a`), so a destem fallback has no valid target. The
rowhouse model is placed by **0 STAT records**; the satellite tripod by 2.
Neither reaches a shipped tile, so the purple count stays 0.

## Planning a create-LOD run before anything bakes
<a id="create-lod-run-planning"></a>

**Code:** `main`, `_worldspace_fid_resolver` in `tools/release/create_lod.py`.

Every worldspace is resolved to its owner and overlay stack BEFORE any bake
starts, so a bad selection is reported as a plan rather than discovered halfway
through an hour of baking. `owner_map` resolves all of them in ONE pass over the
load order instead of re-listing every plugin per worldspace.

Staged meshes are swept up front rather than trusting the post-bake cleanup: a
killed run would otherwise pin them forever, and because this mod installs LAST
to win the tile overwrite, a stale mesh here silently overrides every plugin's
current copy.

WRLD-FormID lookups are memoised per (owner, edid). One owner's read resolves
every wanted worldspace while its bytes are in hand: several worldspaces share an
owner -- **Oblivion.esm owns 18** -- and resolving each independently re-read its
**613 MB**. Jobs are built in worldspace order, not owner order, so the bytes are
held only for the duration of one owner's lookups and only the resolved FormIDs
persist.

Ids are NORMALIZED into the load-order-wide space because they are compared
against ids from OTHER plugins (`touched_worldspace_fids`), and a raw id is only
meaningful inside the file it came from.

## The world-map cloud bank is scaled AND recentered
<a id="world-map-cloud-bank-sizing"></a>

**Code:** `compute_center`, `_rescale_and_flatten`, `generate_cloud_bank` in
`asset_convert/lod/worldmap_clouds.py`; `merge_cloud_bank` in
`asset_convert/lod/sibling_lod.py`.

### Why the deck must move, not just grow

A worldspace's NAM0/NAM9 rectangle is NOT centered on the worldspace origin --
it is wherever its author laid the terrain out. The stock bank IS
origin-centered (every sheet node has translation x=y=0 and vertices symmetric
about zero, verified against the shipped mesh), so scaling alone leaves the deck
over (0,0) while the landmass sits elsewhere, and terrain on the far side of the
origin runs out from under the clouds.

NehrimWorldspace is the reported case: NAM0 (-266240,-188416) to NAM9
(110592,225280), midpoint (-77824, 18432). The origin-centered deck hangs east
and north, leaving the WEST and SOUTH terrain bare -- exactly the two edges seen
in game. **16 of 34** Nehrim worldspaces and **31 of 84** Oblivion worldspaces
are not covered by an origin-centered sheet at their own scale.

`merge_cloud_bank` centers on the UNION's midpoint for the same reason it sizes
off the union: that is the rectangle the map actually draws once every sibling is
installed.

### Parsed graph, not byte patching

The BSAs ship this mesh in SSE BSTriShape form (**96,851 bytes**, half-float
packed vertices) while `references/Skyrim Meshes` holds the LE NiTriShape form
(**182,953 bytes**). A patch written against either layout silently no-ops on the
other, which is what happened to the first version of `_rescale_and_flatten`.
`sse_nif.read_nif` normalizes both to LE NiTriShape and marks the data LE, so the
edit is done on the graph and written out LE (uv2=83), which SSE loads natively.

Stock vertices are symmetric about local (0,0), so a plain multiply stretches
about the sheet's own center.

### Sizing, centering, flattening

X and Y are stretched INDEPENDENTLY (see `compute_axis_scales`) so the deck can
match a map whose aspect differs from Skyrim's. A NIF node `scale` is a single
float and cannot express that, so the stretch is baked into the VERTICES and each
node's scale set to 1.0 -- node scale and vertex scale multiply, so leaving the
stock 8.0 would apply the factor twice. Only the horizontal axes are touched:
vertex Z (the sheet's own relief) and the nodes' Z translations (cloud ALTITUDES)
are preserved.

Sheet node X/Y translations are SET to the center. Stock is (0,0) on all four and
the root above them is an identity-transform BSFadeNode at scale 1.0, so a node
translation is already in world units -- no division by the parent scale, and the
node's own scale applies to its vertices, not its translation. Setting rather
than adding is idempotent if the mesh is regenerated from a previous output.

The stock sheets are not flat: each carries billowing Z relief up to **3523**
local units (~28,000 world units at scale 8) over the interior, with a skirt
dropping to **-899** at the rim. That relief is modelled for Skyrim's terrain,
most visibly the bank piled around High Hrothgar; over a converted worldspace it
is a mountain of cloud on unrelated flat land. `keep` is the fraction retained
(0.0 = flat, 1.0 = untouched), applied about each shape's MEDIAN z so the sheet
settles onto its own base plane instead of being dragged to local zero -- the rim
skirt is part of the silhouette, and collapsing everything to 0 would flare it up
into the deck.

The bounding sphere is updated unconditionally: the X/Y stretch always changes
the extent, even when no flattening is requested, and a stale volume lets the
engine cull the sheet.

### The clouds are a TEXTURE, not vertex alpha

Every sheet samples `textures\sky\SkyrimCloudsMap01.dds`, so the visible pattern
-- the open middle and the dense band around it -- lives in the UVs. Stretching
vertices alone leaves the UV range untouched, pinning that dense band to the same
FRACTION of the sheet however big it gets: on a worldspace shaped unlike
Skyrim's it lands on playable land, and no rescaling moves it.

Scaling UVs by the SAME factor as the vertices keeps texel density constant in
world units, so one cloud stays one cloud and the dense band stays at the rim
where Bethesda put it. The stock ranges run outside 0..1 (u -8.2..11.7) and the
sampler is set to wrap (`texture_clamp_mode` 65283), so the pattern tiles and a
wider range simply shows more of it -- no clamping artefact at the edges.

The factor is the change in WORLD span, not the node scale: the vertices already
carried the stock node scale of 8.0 once it was baked in, so scaling UVs by sx/sy
directly would over-tile by 8x. `world_new / world_stock = sx / _STOCK_NODE_SCALE`
per axis.

### One file, written once

`land_rect` is the worldspace's REAL LAND rectangle in world units and supersedes
width/height entirely: the sheet is grown until its clear middle reaches the
farthest land edge, so the opaque band lands beyond the terrain. width/height is
legacy span-based sizing kept for callers with no land rectangle.

The mesh is ONE file at a fixed path shared by every plugin in a worldspace, so
per-plugin copies were rival versions of it -- each sized to its own bounds, the
install order picking a winner. `write=False` computes and validates the bank
without writing, returning the MODL path it WOULD have written;
`sibling_lod.merge_cloud_bank` writes the single authoritative copy, sized to the
UNION of every sibling's land, into the LOD mod that installs last. MODL is the
same string either way, and every validity check still runs.

When the sheet layout is not the one verified against (`scaled == 0`) or the
vanilla source mesh is unavailable, None is returned: the caller omits MODL and
the engine falls back to its own default, never a broken model reference.


## <a id="lod-invents-terrain-over-cells-with-no-land"></a>Terrain LOD invents ground over cells that own no LAND

**Code:** the tile queue in `generate_terrain_lod`, `_assemble_tile` and
`fill_missing` in `asset_convert/lod/terrain_lod.py`.

A tile is queued when `any(c in lands for c in cells)` — ONE real cell anywhere
in a `level x level` footprint builds the whole tile. `_assemble_tile` leaves
every cell with no LAND as NaN, and `fill_missing` then edge-extends the nearest
real row/column across them rather than leaving a hole. The result is LOD ground
where the plugin authored none.

Measured on WrldMorrowind (Morrowind_ob.esm + TR_Mainland.esm, 15,615 LAND
cells), counting each baked tile's footprint against cells that own a LAND:

| level | tiles | real cells | footprint | real |
|---|---|---|---|---|
| 4 | 1,037 | 15,602 | 16,592 | 94.0% |
| 8 | 291 | 15,607 | 18,624 | 83.8% |
| 16 | 89 | 15,615 | 22,784 | 68.5% |
| 32 | 30 | 15,615 | 30,720 | 50.8% |

At level 32 HALF the baked footprint is invented. The coarser the level the
further a tile reaches past the landmass, because the footprint grows as
`level^2` while the real cells do not.

Worked example — TES4 cell (30,-60), which no plugin owns. TR's TES3 source
stops at TES3 x=14, and a TES3 cell splits into four TES4 cells, so TES4 x=30
would need TES3 x=15. No LOD4 tile exists at (28,-60), but the level 8/16/32
tiles at (24,-64), (16,-64) and (0,-64) all cover it, and `fill_missing`
column-extends the heights of (27,-60) — the nearest real cell, three cells
west — across it. Because each empty column takes its OWN nearest source
column, neighbouring invented cells inherit DIFFERENT heights: that is the
vertically disjointed terrain seen in-game. `WrldMorrowind.4.28.-64.btr` is
12/16 invented.

`fill_missing` is right for a hole INSIDE the landmass, where a Z=0 crater
between real cells would be worse. It is wrong past the coastline, where absent
LAND means "no ground here". The two cases are not distinguished today.
