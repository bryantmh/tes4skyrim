# tes5_import/navmesh/ - PGRD to NAVM, LAND and worldspace

**Code:** `tes5_import/import_main.py`, `asset_convert/collision/collision_extract.py`, `asset_convert/lod/worldmap_clouds.py`, `tes4_export/record_types/world.py`

## Contents

- [World / LAND / PGRD→NAVM Conversion Notes](#world-land-pgrdnavm-conversion-notes)
- [PGRD → NAVM/NAVI Conversion (PathGrid → NavMesh)](#pgrd-navmnavi-conversion)
- [Ribbon construction (`corridor.py`)](#ribbon-construction)
- [Mesh cleanup passes (`corridor_clean.py`)](#mesh-cleanup-passes)
- [Boundary sliver cull (`corridor_clean.cull_boundary_slivers`)](#boundary-sliver-cull)
- [Decimation (`corridor_clean.decimate`)](#decimation)
- [Boundary notch fill and level lookup (`corridor_union.py`)](#notch-fill-and-levels)
- [Surface emission (`corridor_union._emit_surfaces`)](#surface-emission)
- [The union mesh driver (`corridor_union.build_union_mesh`)](#union-mesh-driver)
- [Navmesh build entry point (`build.py`)](#navmesh-build-entry-point)
- [Corridor width growth (`corridor_grow.py`)](#corridor-width-growth)
- [Door footprints (`corridor_doors.py`)](#door-footprints)
- [Cell geometry gathering (`world.py`)](#cell-geometry-gathering)
- [LAND Record Structure](#land-record-structure)
- [OBND (Object Bounds) defaults](#obnd-defaults)
- [The world-map camera clamp — MNAM's cell rectangle (verified by disassembly)](#world-map-camera-clamp-mnams)
- [World-map cloud banks (WRLD MODL) — sized to the LAND](#world-map-cloud-banks-sized)
- [The shared navmesh cache — design rationale](#shared-navmesh-cache-design-rationale)
- [Navmesh redesign: pathgrid corridor ribbons](#navmesh-redesign-pathgrid-corridor-ribbons)
- [Baseline before the rewrite (historical — verified 2026-07-23)](#baseline-before-rewrite)
- [Why replace it](#why-replace)
- [Author-set principles (do not violate)](#author-set-principles)
- [What stays exactly as-is](#what-stays-exactly-as)
- [Phase 1 — corridors + doors + links (a complete, narrow navmesh)](#phase-1-corridors-doors-links)
- [Phase 2 — grow width to walls (deferred, sketch only)](#phase-2-grow-width-walls)
- [Phase 3 — polish (deferred, sketch only)](#phase-3-polish)
- [Decisions made (author) and open questions](#decisions-made-open-questions)
- [Risk register](#risk-register)
- [Connectivity invariant: status 2026-07-25](#connectivity-invariant-status)
- [The triangle-quality contract (2026-08-04)](#triangle-quality-contract)

## World / LAND / PGRD→NAVM Conversion Notes
<a id="world-land-pgrdnavm-conversion-notes"></a>

Linked from [CLAUDE.md](../../CLAUDE.md). Covers pathgrid→navmesh conversion and
LAND/landscape-texture record structure. For terrain LOD generation see
[nif_conversion_notes.md](asset_convert_terrain.md#terrainlodland-adjacent-asset-notes).

## PGRD → NAVM/NAVI Conversion (PathGrid → NavMesh)
<a id="pgrd-navmnavi-conversion"></a>

TES4 PGRD (per-cell pathgrid of nodes+edges) is converted to a TES5 NAVM per
cell PLUS a single top-level NAVI (Navmesh Info Map). Implemented in
`tes5_import/navmesh/from_pgrd.py` (`convert_PGRD`) and `tes5_import/navmesh/navi.py`
(`build_navi_record`), wired in `import_main.py` Phase 4 for both interior
(`_build_cell_groups`) and exterior (`_build_world_groups`) cells.

- **NAVI IS MANDATORY**: Skyrim only uses a NAVM for pathfinding when it is also
  indexed in a top-level NAVI record. NAVM records alone are ignored. NAVI goes
  in the top-level group order immediately BEFORE CELL (verified vs xEdit
  `wbAddGroupOrder`, and added to `writer._group_order`).
> ### ⚠ Surface-generation sections below are HISTORICAL (flagged 2026-07-26)
>
> The **collision-voxel** algorithm described in the next block — and every
> `voxel.*` / `region.*` / `spanmesh.*` / decimation rule that follows from it —
> **is no longer how the navmesh is built.** Those modules are deleted from
> `master`. The live generator is the **pathgrid corridor-ribbon** model:
> [navmesh_corridor_redesign.md](tes5_import_navmesh.md) (implemented),
> tuned per [performance_notes.md](performance.md).
>
> **Still current and safe to rely on** in this file: NAVI-is-mandatory, the NVNM
> and NVMI binary layouts, door handling/links, the base-model index, triangle
> flags, LAND VHGT decode, REFR rotation transpose, world-space obstruction, the
> collision cache, and the iteration tools. Treat the voxel/region/spanmesh
> pipeline details as background on a superseded attempt.

- **Algorithm (collision-voxel — HISTORICAL, see the notice above; rewritten
  2026-07-12 to replace the pathgrid-buffering approach that could not represent
  walls)**: VOXELIZE the real Havok
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

### 🔴 Drop-down storeys arrive as separate components (found 2026-07-26)

**Symptom:** CharacterGen's Ambush A never fired. The Mythic Dawn assassins sit
in a holding cell that teleports (a door pair, both refs in the SAME cell) onto
a mezzanine they are *meant to step off* into the ambush room below. The
mezzanine and the room floor came out as two disconnected navmesh components, so
`CGAssassinsAmbushA4` could never complete, its `OnPackageEnd` never set stage
23, and A1/A2/A3 — gated `GetStage >= 23` — stayed parked in
`DefaultMasterPackage` forever. In game the assassin visibly walks *into* the
door instead of through it.

**Cause — and it is NOT a navmesh defect.** Oblivion has no pathgrid edge for a
DROP. A balcony and the floor beneath it are two disconnected pathgrid islands
and the actor simply steps off. Verified in the source data: cell 0001FBB9's
PGRD has **zero** edges between the pen (points 268–272, z=-594), the mezzanine
(z=-640) and the room floor (z=-832), and its single RefMap entry covers neither
door. Our navmesh reproduces the pathgrid faithfully, islands included — so the
faithfulness is what produced the break. Skyrim has no "step off here" construct
either; connectivity IS the mesh.

**Fix:** `corridor_clean.find_ledge_links` (params `ISLAND_BRIDGE_*`) detects
component pairs whose boundary edges nearly meet in plan (`ISLAND_BRIDGE_XY`,
two ribbon widths) but are separated by a drop of `MAX_CLIMB`..220u, and
`pgrd_to_navm._pack_nvnm` writes them as **Ledge Down / Ledge Up edge links**
(see "Drop-downs are EDGE LINKS" below).  Measured geometry in 0001FBB9: the
mezzanine/floor drop is 192u.  Both sides must ALREADY be separate components,
so stairs, ramps and genuinely-connected storeys never enter the candidate set.
A geometry-welding `_bridge_islands` variant was tried first and rejected —
bridging triangles let actors walk on air and bred downfacing triangles; the
edge link is Skyrim's own construct for this.

### 🔴 A door needs XNDP on the REFR, not just door triangles (found 2026-08-03)

**Symptom:** the *same* CharacterGen Ambush A stall as the drop-down bug above,
still present after that fix. The four Mythic Dawn assassins stayed in their
holding cell at stage 22+. Every layer checked out: `CGAssassinsAmbushA1-A4`
convert to Travel instances of the vanilla `Travel` template (00016FAA) with the
right `GetStage` CTDAs; all three packages per assassin sit on the actor's QUST
reference alias in TES4 order (ALPC verified in the written ESM); the aliases
are filled with the right ACHRs; `pack_validate.py` reports clean.

**Cause.** Three separate structures bind a door to the navmesh, and we wrote
only two:

| Structure | Direction | Written? |
|---|---|---|
| NVNM "Door Triangles" (in NAVM) | navmesh → door | yes |
| NAVI NVMI "Door Links" | navmesh → door | yes |
| **REFR `XNDP`** | **door → navmesh triangle** | **no** |

The engine builds its `BSPathingDoor` from the DOOR REFERENCE an actor is
heading for, so it needs the door→navmesh direction — and that is `XNDP` alone.
Without it a teleport door is not a pathing node: an actor whose destination
lies beyond it has no route, and simply never leaves the room even though its
package, alias and conditions are all correct.

**Vanilla census (the invariant):** 1,705 of 1,722 Skyrim.esm teleport-door
REFRs (99.0%) carry XNDP, and 1,705 of the 1,706 XNDP-bearing REFRs in the file
are teleport doors — the subrecord is essentially *the* teleport-door navmesh
binding. `XNDP` also appears as a literal in SkyrimSE.exe's REFR load switch.

**Layout** (xEdit `wbStruct(XNDP, 'Navmesh Door Link')`): `Navmesh FormID u32 +
Triangle s16 + 2 unused`, 8 bytes. The trailing 2 bytes are uninitialised CK
memory in vanilla (`DA08` x1262, but `0000` x107) — write zero.

**Ordering:** XNDP goes LAST, immediately before DATA — after XLOC/XOWN/XLRT/
XSCL. All 1,706 vanilla records agree.

**Fix:** `_convert_pgrd` already computes `door_tris` = `[(triangle, door_ref)]`
for NVNM; it now also exports `meta['door_xndp']` = `{door_ref: (navm_fid,
triangle)}`. `import_main` merges those across every navmesh right after
`build_edge_links` (triangle indices are final only then, and it must precede
the group builders that convert REFRs) and hands them to
`world.set_door_navmesh_links`; `convert_REFR` emits the subrecord.
`convert_worker.init_worker` replays the map into pool children — module state
like the location maps, and a worker missing it writes door REFRs with no
navmesh link at all.

Note `build_edge_links` only appends edge links to *exterior* meshes and never
reorders triangles, so indices captured before it stay valid.

<a id="navmesh-worker-rebuilt-globals"></a>
**What `init_worker` must rebuild in every pool child** (`navmesh/worker.py`).
A spawned child does NOT inherit the parent's module globals, so each of these
is rebuilt per worker or the stage silently produces nothing:

- `text_reader._formid_index_offset` — the load-order master-index shift
  `get_formid()` applies (e.g. +1 for Oblivion.esm behind Skyrim.esm). Left at
  the default 0, every FormID `convert_PGRD` reads (PathingCell
  ParentCELL/ParentWRLD, door REFR links, ONAM base objects) keeps master index
  `0x00` instead of the plugin's real index; the engine then cannot resolve the
  navmesh's parent cell at load and null-derefs in `Hook_NavMeshLoad`. **MUST be
  set before any `get_formid()` call.**
- `collision_extract._COLLISION` — the per-mesh Havok collision soups the
  navmesh is voxelized from. Without it every cell has no geometry and produces
  no navmesh at all.
- `from_pgrd`'s door caches — see [the door-center caches](#door-center-caches).
- The containment job (`core.process_job.join_pool_job`), so a worker cannot
  outlive a parent that dies without cleanup. No-op off Windows.

### Door threshold axis comes from the COLLISION PANEL, never the bbox

Which local axis a door's threshold runs along decides the whole quad's
orientation. It is read from the door's **collision panel** — the body the
engine collides with — in `asset_convert.collision.collision_extract.door_panel_axis_from_data`,
cached to `door_panel_axis_cache.json` by `collision_extract.scan_door_axes`:

> A door panel is thin THROUGH the opening and wide ACROSS it. The panel's thin
> horizontal axis is the swing direction; the wide one is the threshold.

**The whole-NIF bounding box cannot answer this** — it includes the door
frame/arch, which routinely dwarfs the panel and inverts the result:

| model | bbox | panel | old (bbox) | correct |
|---|---|---|---|---|
| `AnvilDoorMC01` | 98 × 150 | 97.9 × 4.5 | Y | **X** |
| `chorrolfightersguildinteriordoorjam` | 188 × 32 | 34.5 × 186.5 | X | **Y** |
| `icbarreddoor01` | 152 × 14 | 15.2 × 136.8 | X | **Y** |

22 of 184 door models were wrong under the bbox rule, each laying its door quad
90° out (Anvil's exterior doors — Pinarus's house among them).

Read the **`output/`** meshes, not `export/`: the shipped collision is what the
navmesh and engine use, and its body transform is already baked into the shape,
so there is no `bhkRigidBodyT`-vs-`bhkRigidBody` rotation branch to get wrong.

The same measurement supplies the doorway **WIDTH**, and the quad must span it.
Door panels run **16u to 764u wide (median 121)**, so the old hardcoded
`DOOR_LINE_HALF = 45` (a 90u base line) was simply the wrong size for most
doors. On `impdundoor01` (115u) it left the **first 30u of the threshold with
no mesh under it**, and the Door Triangle came out a 571-unit scrap — smaller
than *every one* of 1,659 vanilla door triangles (min 992, median 9,614) and too
narrow for an actor to stand on. That is what stopped the CharacterGen assassins
dead at their cell door: they reached the door triangle and could not settle onto
it, so `OnPackageEnd` never fired, stage 23 never ran, and the other three
assassins never got a valid package at all.

Three traps, all of which silently dropped real doors:
* **CMS must be unwrapped.** Converted doors ship `bhkMoppBvTreeShape` →
  `bhkCompressedMeshShape`; 85 vanilla models (every Cheydinhal/Bravil/Leyawiin
  and castle-tower door) arrive that way. Decode with `asset_convert.collision.cms.decode_cms`.
* **A zero-thickness collision sheet is legal.** `cathedraldoor02`,
  `priorydoor01`, `weynondoor01`, `skdoormiddle01`, `icwalldoor01` ship a flat
  plane where the ZERO axis *is* the swing direction. Rejecting `min(ex,ey)==0`
  dropped 10 real doors.
* **Thin-in-Z means no threshold at all.** Trapdoors, hatches, grates, manhole
  covers and display cases swing about a HORIZONTAL axis. They get no quad
  (`_DOOR_NO_THRESHOLD`); assigning one lays a quad across the floor in an
  arbitrary direction. Teleport doors are kept regardless — they still link two
  navmeshes.

#### 🔴 An unreadable door shape is NOT a trapdoor

`_DOOR_NO_THRESHOLD` (thin-in-Z) must only suppress the door QUAD — never the
door itself. Dropping such doors from `_collect_doors` deleted the Imperial
Prison cell gates, **including the player's own starting cell door**, because
`bhkListShape` (the gates ship as a list of bars) read as "no shape" and is
indistinguishable from a real trapdoor once it reaches the cache. Every door
must still receive a Door Triangle or the doorway is dead in the engine.

Collision shapes that must be unwrapped before measuring: `bhkMoppBvTreeShape`
and `bhkConvexTransformShape` (single child), `bhkListShape` (**several**
children).

#### 🔴 The debug tools were measuring doors the pipeline never builds

`navmesh_audit.py` cached `door_fids` as a **set**, while the pipeline
(`import_main._build_door_fid_set`) builds a **fid → model-key map**. With a set,
`_collect_doors` takes its legacy membership-only path: no panel centering, no
threshold axis, **width 0**. `navmesh_cell_check.py` additionally never called
`load_door_centroids` at all. So every generated-cell tool silently graded doors
with the default orientation and no width — the exact opposite of what shipped.
If a door metric from a debug tool disagrees with the ESM, check this first.
`tools/navmesh/index.py` (and therefore `tools/navmesh/render.py`) always loaded it.

### Drop-downs are EDGE LINKS, not bridging triangles

Oblivion expresses a drop-down as two disconnected pathgrid islands — the actor
steps off a ledge and there is no pathgrid edge for it. Skyrim's own mechanism
is an NVNM **Edge Link**, typed by `wbNavmeshEdgeLinkEnum`
(`xEdit/Core/wbDefinitionsCommon.pas:7272`):

| Type | Meaning |
|---|---|
| 0 | Portal (ordinary cross-mesh connection) |
| 1 | **Ledge Up** |
| 2 | **Ledge Down** |
| 3 | Enable/Disable Portal |

A drop-down is a **pair**: `Ledge Down` on the upper triangle, `Ledge Up` on the
lower. Vanilla census (Skyrim.esm, 3,000 navmeshes): 30,546 Portal, **467 Ledge
Up, 476 Ledge Down** — near-symmetric, exactly as pairing implies. Both links
may name the SAME navmesh when both triangles are in it (`0008FFE1` links to
itself), which is the usual case for us.

The linked triangle must also set the matching **per-edge link bit** in its
flags — `0x0001`/`0x0002`/`0x0004` for edge slot 0/1/2 (vanilla shows `0x0801`,
`0x0802`, `0x0804`). Pick the slot that is an OPEN edge (no neighbour) facing
the other side: that is the lip.

Two more parts of the contract, verified against real Skyrim.esm ledge links
(NAVM 0002FB4A/0002FB4B reciprocal pair, 001090A8 self-links) — the first
implementation got BOTH wrong and shipped dead links:

* **The carrier edge's neighbour field becomes the link INDEX.** When flag bit
  N is set, triangle edge-N no longer holds a neighbour-triangle index (or −1);
  it holds the index into the Edge Links array — the same `wbEdgeToStr` rule
  the Portal stitcher (`navm_edge_links.add_link`) already follows. Setting the
  bit but leaving the field at −1 makes the engine deref link −1.
* **The link's Triangle field names the TARGET triangle**, i.e. the one on the
  other side of the drop, in the navmesh the link's FormID names. Writing the
  carrier's own index makes every link point back at itself.

`corridor_clean.find_ledge_links` detects the pairs and `pgrd_to_navm._pack_nvnm`
writes them. It previously **stitched two triangles across the lip** instead,
which is wrong twice over: actors walk on air across the gap, and the near-
vertical quad breeds downfacing/opposite-normal triangles (ImperialDungeon01:
DOWNFACING 4 → 2 once the bridging was removed).

Triangles are identified by CENTROID between detection and packing — the cull
and compaction passes reorder both triangles and vertices, so an index captured
early is meaningless later.

<a id="ledge-lip-is-pushed-to-the-edge"></a>**A ledge lip is pushed out to where the upper floor really ends, and a pair a railing blocks is dropped** (`clean_validate.find_ledge_links`, `corridor._ledge_reach`). Vanilla (1,705 same-mesh Ledge Down links in Skyrim.esm) puts the lip ON the floor's edge, with the landing triangle a median 40u out from under it (p10 14u, p90 72u). Ours sat **24-68u inside the real edge in 7 of 17 pairs across ImperialDungeon01/02/03**: railing segments and an edge curb stand on the ledge edge, the width march stops at anything in the actor slab, and the outline passes trim the corner further. An actor then reaches our lip with floor still under it and the engine drives it in a straight line toward a landing almost directly beneath, through the railing or the ledge face -- "the drop is not vertical enough and clips". After pairing, each lip edge is marched along the drop direction with the same wall slab and floor test the width-grow uses; the two lip vertices move out to where the floor ends (2u short of it), and a pair whose way to the edge is blocked by a wall taller than a step is not linked at all, since no actor can get over that railing.

<a id="tri-target-edge-192"></a>**`TRI_TARGET_EDGE` stays 128; 192 was measured and REJECTED** (`params.py`). Five vanilla interiors carry 3,870-10,022 u² per triangle with p90 areas of 8.5k-25k; ours carry 4,655-5,144 with p90 under 9.2k, because the hex lattice and the outline densify both run at 128u (an equilateral of that edge is ~7,100 u²), so a room never holds a large triangle. Measured with `tools/navmesh/coverage.py` and `slope_check.py` on ImperialDungeon01/02/03 and AnvilFightersGuild: **192 cuts 14-23% of the triangles (887->685, 985->852, 669->562, 220->207) for at most 0.17 points of coverage -- but opens 7 uncovered steep-edge samples on ImperialDungeon02 (0 at 128)**, at stair landings such as node n267 (771, 6634): the landing sits 35.3u above the floor, one unit over `MAX_CLIMB`, so a coarse triangle spanning landing and floor has corners sharing no storey and is dropped, leaving a notch at the node. The lattice, not the densify, is the cause (lattice 192 + densify 128 still misses 4); keeping the fine lattice only on sheets that contain a flight recovers a third of the saving (887->797, 985->855, 669->642) and still misses 1. Vanilla's larger triangles are therefore not reachable by spacing alone; the straddle drop at the storey boundary would have to be solved first.

### 🔴 The Door Triangle is RESERVED, not protected

Vanilla marks a door with **ONE** triangle whose long edge is the **full width
of the doorway**. The way to guarantee that is not to defend the triangle from
the passes that would damage it — it is to make sure they never see it:

1. `corridor_union._triangulate` computes the door triangle (base line +
   apex) and **cuts it out of the polygon** with `difference()` before
   Delaunay runs. The triangulator fills around a hole and cannot subdivide
   what is not there.
2. Every pass afterwards — the 3D weld, the T-junction split, the
   pathgrid-node merge, make-manifold, decimation, the island cull — sees the
   doorway as ordinary mesh boundary. Nothing there to split, weld or drop.
3. `corridor.build_corridors` calls `corridor_union.attach_door_triangles`
   **last**, after `finalize`, snapping the base endpoints tightly
   (`ATTACH_R_BASE = 2`) so the door line keeps its exact width and the apex
   loosely (`ATTACH_R_APEX = 8`) so it shares real edges with the mesh.

**Do not add per-pass protection instead.** That was tried across
`_weld_sheets`, `_split_t_junctions`, `_merge_at_pathgrid_nodes` and
`_make_manifold`; survival went 13/27 → 17/28 → 19/28 and never reached the
guarantee. Reservation reached **28/28 on the first try**, and every protective
branch was deleted afterwards.

Three rules the reservation itself must obey:

* **Never cut a hole that DISCONNECTS the sheet.** Where a door sits in a
  narrow passage the wedge can span the whole corridor: ImperialDungeon01's
  main surface stopped at x=2170 instead of 2293 and the door triangle became a
  lone island. Compare polygon part-counts before/after each `difference()` and
  skip any cut that raises it. A door triangle is worth nothing if it costs the
  corridor it serves.
* **Skip a triangle with nothing to attach to.** If all three corners mint new
  vertices, the pathgrid never reached that door; the triangle would land as an
  unreachable scrap.
* **Dedupe per STOREY, not per XY.** Two sheets bordering one threshold each
  reserve it (drop one), but the same door line at a different height is a
  different floor's doorway and keeps its own triangle (ChorrolCastleWallTowerSW
  has one at z=526 and one at z=-15).

Measured over 40 interior cells: 72 doorways, **every one with exactly one
full-width triangle**, none missing.

### Door reservation hardening (2026-08-02)

Rules added after the reservation model met ImperialDungeon01 end-to-end; each
was measured against a concrete failure in that cell and re-verified against
the four reference houses (all 1 component, CK-clean, every door ≥ vanilla
min area 992):

* **Frontal-strip candidate gate** (`corridor_doors`): a corridor edge only
  qualifies as a bridge target when it lies within the doorway's span across
  the facing (± a ribbon width).  The sweep extends along the facing, so a
  candidate displaced sideways is unreachable — accepting one laid a floating
  5-triangle patch beside the tower door whose only corridor runs 283u to the
  door's SIDE.  `DOOR_BRIDGE_RADIUS` is 384 (220 stranded that door's
  neighbours); the wall walk still vetoes blocked candidates.
* **Far-side quad for disconnected doorways** (`corridor_doors`): when a
  non-teleport door has walkable ground on both faces but the two sides'
  nearest pathgrid nodes are in DIFFERENT pathgrid components
  (`_sides_disconnected`), a second, constraint-free quad bridges the far
  side.  This is the prison-cell-gate case — Oblivion ships the cell interiors
  as pathgrid islands with no edge through the (openable) gate, and the
  player's own cell was an unreachable island an escorted Uriel could never
  enter.  The gate MUST be the pathgrid-component test: emitting far quads for
  ordinary doors (whose pathgrid crosses the doorway) severed the staircase
  sheets in Pinarus's and Arvena's houses.
* **De-stacking** (`attach_door_triangles`): the triangulator keeps any
  Delaunay triangle with ≥50% of its area inside the polygon, so ground
  overlapping the reserved wedge survives the cut.  If that gives a door-tri
  edge two users already, appending the door triangle 3-shares the edge — and
  `_compute_adjacency` links only 2-shared edges, so the doorway DISCONNECTS.
  The overlapping triangle is dropped and the door triangle takes its place.
* **Teleport apron rule** (`SPLIT_TINY_AREA`, corridor_union): every teleport
  door carries a thin apron of ribbon extension beyond its threshold, so the
  "never cut a hole that disconnects the sheet" guard read every wedge cut as
  a split and NO teleport door ever reserved — 158737's Door Triangle came
  out as the 534-unit apron sliver.  Pieces under 2,000 sq units are not
  counted as a disconnection and are dropped with the cut.
* **Stitching** (`_stitch_isolated_tri`): a door triangle whose corners all
  snapped to real mesh vertices can still share no EDGE (the Delaunay bridged
  the wedge corners through other vertices).  A short open-edge boundary chain
  from corner to corner is fan-filled; a COLLINEAR chain (mesh boundary
  running along a wedge side, a T-junction) instead splits the door
  triangle's SIDE edge at those vertices — the base line never splits.  The
  chain graph must exclude the door triangle's own edges or the BFS "reaches"
  the far corner through the door itself.
* **Island withdrawal**: if the stitch finds nothing, the reserved triangle is
  WITHDRAWN and `_build_door_links` falls back to the containing mesh
  triangle.  An unreachable 1-triangle island is strictly worse than a
  fallback door triangle.
* **Winding normalization + scrap sweep** (`corridor.build_corridors` tail):
  every triangle is forced CCW in plan (decimation edge collapses can flip
  one → CK DOWNFACING), and 1-2 triangle components that carry no door
  threshold are dropped.

### Analytic door wedge (2026-08-03)

The reservation no longer *searches* for a door triangle — the wedge is a pure
function of the door, computed in `corridor_doors` and passed through
`door_edges` as `(base0, base1, apex, storey_z)`:

* **Base** = the doorway's exact measured width (collision panel, capped at
  `DOOR_LINE_HALF_MAX`), centered on the exact panel center.  The old
  45u-minimum widening is gone — it pushed a narrow gate's base through both
  jambs.
* **Apex** = base midpoint + facing × `max(w/2, DOOR_TRI_MIN_DEPTH=64)` on the
  side the PATHGRID serves.  The old `_door_apex` ladder tried BOTH normals and
  five shrinking depths until something fit the polygon, so a cramped near side
  flipped the whole triangle to the far side of the door (three doors in
  ImperialDungeon01), and the area varied with the surrounding geometry.  Same
  door → same triangle, every build.
* **Exact centers**: `door_panel_axis_cache.json` now carries each model's
  collision-panel center (`[axis, width, cx, cy]`, world units); `_door_threshold`
  prefers it over the legacy mesh-bbox `door_centers_cache.json`.  The bbox
  centers were 25–35u off along the threshold on the CharacterGen prison gates
  (`cgprisoncellgate01`, `idgate01`) — over half those gates' own width.  Double
  doors merge their per-leaf rigid bodies (parallel panel-shaped bodies of
  comparable size), so the width spans the whole doorway, not one leaf.
* **Storey-gated claiming** (`build_union_mesh`): parts are 2D, so where two
  floors stack, BOTH used to pass the containment test and iteration order
  decided which sheet cut the wedge — Arvena's upstairs door was reserved out
  of the sheet that only covers that spot downstairs.  The claim now requires
  the sheet to have a surface level within `STOREY_GAP_Z` of the door's own
  storey.  The claim test also uses `part.boundary` (holes included), not
  `part.exterior` — a mid-floor doorway lies on an interior ring.
* **Apron consumption**: when the wedge cut consumes its whole part (the
  sheet fragment was barely bigger than the doorway — Arvena's front door),
  the door triangle is still emitted (`PENDING_DOOR_TRIS` keeps it, with the
  door's storey_z since no local mesh exists to tag it from) and the remaining
  crumbs are triangulated regardless of `SPLIT_TINY_AREA`, because those
  crumbs are what the door triangle attaches to.
* **Door-storey level seeding** (`_apply_door_apex_levels`): a doorway can be
  wider than the ribbon crossing it, leaving a base corner on ground no strip
  covers — no level, dropped by `_emit_surfaces`, door triangle gone.  Base
  endpoints/apex/ring corners are seeded with the door's own storey height.
* **Corner pull** (`attach_door_triangles`): decimation collapses the wedge's
  hole-ring corners into nearby boundary vertices (7–10u inboard).  When no
  vertex sits within `ATTACH_R_BASE` of a base corner, the nearest vertex
  within `ATTACH_R_BASE_PULL=16` is MOVED to the exact corner — full width
  restored, and the survivor's shared edges to the apex come with it.
* **Base-edge far-face fan**: where the pathgrid runs THROUGH a doorway,
  ground exists on both faces; only the apex side is wedge-cut, so the far
  face can end up point-touching a base corner (Pinarus's bedroom door split
  the upstairs floor in two).  When the base edge has no second user after
  attach, the far face's open boundary is fan-filled onto it
  (`_stitch_isolated_tri(only_edges=[base])`).

### Door placement convention + closed-pose cache (2026-08-03)

* **Placement rotation is the TRANSPOSE.**  Bethesda applies the inverse of
  the stored REFR rotation when placing a mesh (`navmesh/world.py
  _rot_matrix`, measured on the AnvilFG floor shell).  `_door_threshold` and
  every door direction formula used the naive CCW form — wrong for any
  rotation off 0/180, which is why it survived every cardinal-rotation test:
  Arvena's upstairs door (raw 90°) had its center one FULL door width from
  the real doorway.  Correct forms everywhere now: center offset
  `(lx·c + ly·s, −lx·s + ly·c)`; threshold `(sin rz, cos rz)`; facing
  `(cos rz, −sin rz)`.
* **The door cache measures the ORIGINAL NIF at the CLOSED pose**
  (`asset_convert.collision.collision_extract.door_closed_geometry`, built by
  `collision_extract.scan_door_axes` from `export/<plugin>/meshes`; the
  converted-mesh scan no longer writes it).  The 'Close' controller
  sequence's FINAL key values override the animated nodes, and the union
  bbox of the KEYED shapes — the door leaf/leaves, never frames or static
  fence sections — gives `[axis, width, center_x, center_y, z_min]` per
  model.  This is the only correct source: idgate01's leaves are STORED
  mid-open (nowhere near the doorway; they swing 90° shut), its static side
  grates span 269u where the keyed leaves close to 133u, and the converted
  collision (the previous source) additionally baked the leaf transforms
  wrong — which is what rotated the CharacterGen pen gate's Door Triangle
  90° and put a corner at the door center.  z_min (the closed slab's base)
  also replaces the whole-NIF bounds z-min as the pivot→floor drop.
* **Attach completion ladder** (in order, each a measured failure): stitch →
  T-junction split → apex bridges → **carve** (`_carve_door`: locally remove
  the same-storey triangles overlapping the wedge, retriangulate the region
  minus the wedge from their own vertices with the kept-mesh boundary edges
  and the wedge's edges forced back — the last resort when another sheet's
  uncut ground covers the doorway) → **door-to-door bridge** (a 2-triangle
  strip to the nearest other door triangle: a room with doors but no
  pathgrid, the CharacterGen pen, keeps nothing else to attach to) →
  withdraw.  The attach runs TWO passes so a door with nothing to attach to
  can succeed once its neighbours attached.
* **Known limitation**: pathgrid-less interiors (prison pens, closets) keep
  only door triangles plus their bridges — thin but traversable door to
  door; and a door no pathgrid approaches within 384u still gets no
  triangle.

<a id="storey-grouping-by-connectivity"></a>**Storey grouping is by CONNECTIVITY, not a Z threshold** (`corridor_union._storey_groups`). Flattening a cell into one 2D union bridges an upper and a lower floor that overlap in plan, producing triangles with corners on two floors at once. A Z threshold cannot separate them either, because a STAIRCASE legitimately spans two floors. So two ribbons join the same storey when they share a pathgrid NODE and their heights AT THAT NODE agree within `SAME_SURFACE_Z`: a stair joins the floor at its foot and the floor at its head, merging all three, while two floors that merely overlap in plan and share no node never merge.

### Door sheet matching: the area tie-break is GONE (2026-08-30)

**Code:** `corridor_union._storey_groups`.

The door-to-sheet match had an area tie-break -- "prefer the group the door
overlaps MOST" -- that NEVER ran once from commit 31c2594 (2026-07-24).
`unary_union` is imported locally inside functions throughout
`corridor_union.py`, never at module level, so both call sites raised
`NameError` inside a bare `except`: `group_polys` filled with `None`, every
`area` stayed `0.0`, and the key `(-area, hz)` collapsed to the HEIGHT
tie-break alone.  Ruff `--select F821` is what surfaced it.

**It is now DELETED**, along with `group_polys`: the code says what it does,
which is match each door to the overlapping group nearest in height.  Output
is byte-identical over 90 door-heavy cells, because that is what the engine
was already doing.

**Repairing it instead was tried and REJECTED.** Supplying the missing import
was measured over 190 door-heavy cells (1,529 doors): exactly ONE changed,
`Vilverin02` at 2026 -> 2021 tris, with identical coverage, one component and
CK-rule CLEAN -- but **crack edges rose 15 -> 16**.  A crack is a boundary edge
a walked pathgrid line crosses, an adjacency break the engine cannot path
across, so five fewer triangles does not pay for it.

The `else` branch (a door matching no group becomes its own) is NOT dead: it
fires in ChorrolFightersGuild, and deleting it cost 2 triangles there.

<a id="sheet-weld-is-distance-based"></a>**The sheet weld is DISTANCE-based, never grid-snapped** (`union_mesh._weld_sheets`). Two sheets sample a shared boundary independently, so their vertices land 1-3u apart -- **measured in Chorrol: 310 border pairs under 25u, the closest at 1.2u, all at identical Z**. Rounding to a grid puts such a pair in different buckets as often as the same one, so it welded almost nothing.

A same-emission fuse that moves a vertex sideways is PROVISIONAL: usually it is the glue joining a fold's stacked copies (**banning it outright split ImperialDungeon05**), but occasionally it drags a vertex across a neighbour's edge. Such welds are reverted if their triangles then overlap other mesh. Only triangles touching a provisional rep are suspects, but a cave cell has hundreds of them, so suspects are tested against an STRtree of the whole soup rather than a per-call scan -- **the naive scan was 6 of Moranda02's 14 seconds**. The STRtree query is a BOX filter, so most candidates do not actually touch: ask the cheap predicate before paying for a clip, because `intersection` builds a whole new polygon just to read its area and was **the single hottest call in the build (23,553 of 41,473 GEOS clips across two reference cells, ~14% of total time)**.

<a id="t-junction-split-projects-in-plan"></a>**T-junction splitting projects in PLAN with a separate Z window** (`union_mesh._split_t_junctions`). The old spherical 2u test never sealed a stair fold: the hanging vertex sits ON the edge in plan but **3-8u off in Z** (two emissions of a flight disagree by part of a tread), and the unsealed crack reads as a zero-area hole NPCs cannot walk across -- the **ImperialDungeon01 staircase "hole"**. Boundary vertices get a wider plan tolerance (up to 6u): a crack has boundary on BOTH sides, so such a vertex is the far lip and splitting seals it, whereas an INTERIOR vertex that close is dense healthy mesh. The fan's new edges must not give any edge a 3rd owner, or `_make_manifold` rips the extras out and deletes real coverage (**3-sample corridor losses in ImperialDungeon05 / LeyawiinCastleCountyHall**).

<a id="plan-overlap-split-uses-bonded-pairs"></a>**Sheets are separated by plan overlap PLUS bonded pairs** (`union_mesh._split_plan_overlaps`). Ribbons that overlap in plan and agree in height are the same sheet; overlapping and disagreeing by more than a storey makes them different sheets. A BONDED pair -- two ribbons meeting at a pathgrid NODE where their heights agree -- always wins, because the pathgrid asserts an actor walks from one onto the other, so they are ONE junction even if their ribbons also overlap elsewhere at a different storey. That is exactly what a staircase does: **Pinarus's flight (0,1) meets the landing at node 1 (heights 68.6 vs 68.6) and passes UNDER five upper-floor ribbons**. Scoring on mean height alone loses this case, since a staircase's mean sits midway between its two floors and is near neither sub-sheet.

Each ribbon joins the sub-sheet it AGREES with best, not merely the first that does not conflict: first-fit scatters one floor's ribbons across several sub-sheets which then overlap in plan at the SAME height -- duplicate ground, **7% of Chorrol's triangles stacked on another at the same height**. Any two sub-sheets overlapping in plan at the same height are then MERGED BACK (**Chorrol: 11 overlapping pairs**), or both mesh that ground independently and the triangles stack.

Candidate pairs come from an R-tree, not all-pairs: a ribbon is a short local quad so only a handful of the n(n-1)/2 pairs can touch, but testing them all cost **7.4M scalar shapely `intersects` calls on Moranda, ~33% of the cell's build time**. Note that batching the intersections through shapely's vectorised form measured **SLOWER (17.0s -> 17.9s over the 6-cell set)**: the cost is GEOS clipping itself, not Python call overhead, and the bulk form materialises an intersection for every candidate whereas the loop discards most on the cheap predicate.

<a id="destack-and-bridge-overlap-details"></a>**Two smaller invariants.** `union_mesh._destack` treats surfaces within 40u at the overlap as duplicates: the tightest gap two REAL storeys ever have is `STOREY_GAP_Z` (120), so anything closer is a duplicate, not a floor above. `union_mesh._tri_overlaps_mesh` counts a triangle fan-opened this round as still present, since its halves cover exactly the parent's footprint; skipping them let **400-1600u^2 bridge overlaps through on Moranda02**.

<a id="pathgrid-node-merge-is-memoised"></a>**The pathgrid-node merge caches its component state** (`union_mesh._merge_at_pathgrid_nodes`). `comp`/`vcomp` describe the CURRENT triangle soup, so they go stale only when a node actually welds something -- and most nodes weld nothing. Rebuilding them per node made this **the single hottest function in the whole navmesh build**: full-mesh union-find once per pathgrid node is O(nodes x tris), **831 x ~4000 on Moranda, ~60% of a large cell's total time**.

The junction disc reaches one ribbon width past the node's own half-width: two sheets meeting at a junction can each stop short of it (a claim seam leaves their boundaries **20-35u apart**), and a disc covering only the node's own corridor missed the neighbour sheet's nearest vertex -- **BarrenCave's tunnel at node 337: 72u away against a 64u disc**, so the junction was never seen. Candidates come from a 3x3 bucket neighbourhood then an exact radius test, sorted so banding stays deterministic (the byte-reproducibility contract).

Banding is on the STOREY gap, not one step: two corridors meeting at a node are the same junction even when the sheets left them a step or two apart in Z -- that disagreement is precisely the defect being repaired. Then weld ONE vertex per foreign component (the closest) onto the keeper. Welding the WHOLE band deleted every triangle fitting inside the node disc: with the interior lattice the mesh near a junction is exactly disc-sized triangles, and a 160u grown radius **swallowed two upstairs floor triangles and a door quad whole on ChorrolFightersGuild**. Weld the CLOSEST cross pair, never everything onto the node-nearest vertex -- the pair across a claim seam is 20-35u apart while the node-nearest vertex can be a full disc away, and welding onto it **dragged geometry ~100u**. The drag is bounded to a claim seam's width; an uncapped weld swept edges across unrelated mesh and **the overlaps came back (Moranda02: 24 pairs)**.

<a id="sheet-stitching-runs-to-convergence"></a>**Sheet stitching runs to CONVERGENCE, not a fixed round count** (`union_mesh._stitch_shared_nodes`). A junction whose border edges are all too long to bridge needs a split round per halving before its bridge round (**the Sanctum pit gate took split+split+bridge on each side**), and a busy cell spends early rounds on other junctions -- **3 and even 8 rounds left it disconnected**. Every round either bridges, opens a fan, or halves an over-long border edge; the last rounds run relaxed unconditionally, because a busy cell can otherwise exhaust the loop without the stall-retry ever reaching a stubborn junction.

Coincident vertices are FUSED first: passes before and inside the loop mint midpoints independently on both sides of a seam, so two components can touch at IDENTICAL positions under different indices, invisible to the index-keyed junction scan -- **Chorrol and BarrenCave each ended with a component pair 0.00u apart**. The scan is driven from the GEOMETRY, not only the pathgrid node list: a junction is a vertex used by two components, and **Pinarus has two such points (the stair top at (-316.9, 134.9) and a second stair node at (-318.5, -88.0))**; stitching only sheet-shared nodes fixed one and left the other, so the house stayed in two pieces.

A component may USE a junction while presenting no BORDER edge there -- the other surface arrives into the MIDDLE of its fan, so every edge already has two owners. A bridge cannot help: `_compute_adjacency` links an edge shared by 3+ triangles to NOTHING, so laying a bridge SEVERS the fan it lands on. Candidates are tried LARGEST FIRST until one passes; trying only the largest gave up whenever it failed a guard while a splittable fan triangle sat beside it (**the Sanctum pit-gate seam: the 15,676u^2 candidate's opposite edge spans dz 36, 2u over MAX_CLIMB, while the 2,936u^2 one is dead flat**).

Three guards apply. A split must not manufacture a near-VERTICAL or degenerate triangle -- the halves inherit the parent's corners plus a midpoint, so a parent spanning a big drop hands both halves that drop and the result reads as wall; splitting those **added OPPOSITE_NORMALS/DOWNFACING triangles to ImperialSewers03 and Bruma**. The test is SLOPE-based: a bridge on ramped ground may climb with its plan run (~35 degrees); only height without run is a wall. The MANIFOLD guard requires every introduced edge to end with at most TWO owners. The OVERLAP guard requires the bridge to land on empty ground -- a wide, guard-passing bridge can lie across mesh it shares no vertex with, and at **Pinarus's stair top a 126u flat bridge at the landing height overlapped the flight's emerging top triangles (same surface, dz 19)**.

When every candidate spans too far, the shortest border edge of each side is split at its midpoint. Decimation merges boundary vertices into edges well past the 160u bridge cap, so both sides offer only LONG border edges -- **the Sanctum pit gate: components touching at 0.00u, shortest edges 104/173u, all bridges rejected**.

<a id="ribbon-centerline-seeds"></a>**Every ribbon gets a row of centerline seeds** (`union_cdt._ribbon_seeds`), for two reasons. **Connectivity:** a corridor is only ~one ribbon wide (80u), so at a 128u target edge it gets no interior hex row, and a bend is triangulated by long triangles whose centroids fall outside the bend and are culled -- silently snapping the corridor into pieces (**ChorrolFightersGuild fell into 10 components**). A row of centerline points guarantees a triangle chain that stays inside. **Stairs:** a ribbon climbing more than half a storey gap over a `target_edge` run is a stair; one uniform triangle on it would span more than `STOREY_GAP_Z` across its corners and be dropped by the per-surface emission, so the whole flight vanishes (**Pinarus's two floors, 268u apart, on a single 2-node edge**). Steep ribbons are sampled much finer, along the centerline and both rails, at a spacing giving ~a third of the storey gap of climb per step. On flat open ground the Poisson guard rejects most seeds in favour of the coarse hex lattice, so rooms stay large-triangled.

<a id="flip-never-onto-an-existing-diagonal"></a>**A 2D flip may never land on a diagonal that already exists** (`union_cdt._flip2d`). The shared vertex space spans several cut pieces, so an edge can recur; a 3-owner edge is non-manifold and gets torn out later.

<a id="cdt-is-a-true-constrained-delaunay"></a>**The union triangulates with a TRUE constrained Delaunay** (`union_cdt._triangulate`, GEOS via shapely's `constrained_delaunay_triangles`). Every ring edge is a constraint the result must conform to, so no triangle can cross a hole or the outline, the door base line survives as exactly ONE edge, no coverage is lost to an in/out filter, and the whole part triangulates against one consistent vertex set. The point-set-Delaunay predecessor guaranteed none of these, and every miss was a disconnection: giant triangles spanning the door wedge, T-junction seams, missing slivers beside the door triangle.

A boundary-only CDT triangulates any region wider than one triangle as a FAN, so long thin triangles are a mathematical certainty rather than a tuning problem. Near-equilateral triangles need INTERIOR vertices, so a hex lattice at `target_edge` spacing is inserted and the diagonals flipped to shape (`_hex_refine`).

<a id="door-wedges-are-cut-not-stitched"></a>**Door wedges are CUT OUT and triangulated in the same vertex space, never stitched back** (`union_cdt._triangulate`). The wedge's ring coordinates appear verbatim on the pieces' boundaries because the cut created them, so after the shared re-index the door triangle SHARES its base and side edges with the surrounding mesh by construction -- nothing to stitch, no repair pass to go wrong. The old shape (leave a hole, attach the triangle after all cleanup) was never robust: the attach needed the ring to survive weld and decimation exactly, and every drifted corner produced an island door.

Where the wedges consume a whole part, that part WAS the doorway apron and the door triangles replace its ground. Where the cut yields a MultiPolygon every piece is real: the slivers either side of the wedge are the door triangle's edge-connection to the corridor, and an earlier `SPLIT_TINY_AREA` gate that dropped the small ones left **doorways point-joined**. A piece that genuinely leads nowhere is culled later by the island pass, which knows about reachability; area is not a proxy for it.

<a id="densify-on-a-world-grid"></a>**Outline densification lands on a WORLD grid along each segment, not on per-segment fractions** (`union_cdt._grid_stations`). Densifying each ring segment at fractions of its own length puts the two rails of a corridor at STAGGERED positions (each rail starts counting from its own corner), so the Delaunay triangulation zigzags between them -- the "assortment of triangles in a corridor" both hand corrections replaced with rows of quads (ImperialDungeon03's east door corridor: our 4-triangle zigzag against the human's 2 quads; the N-S corridor likewise). Placing a point wherever a segment's along-direction coordinate `p . u` crosses a multiple of `TRI_TARGET_EDGE` makes two parallel rails (same `u` up to sign) hit the same stations, and the CDT then pairs them straight across into quad rows. A point is not placed within a quarter of the spacing of a segment end, where it would only mint a sliver against the corner.

<a id="door-base-line-suppresses-densification"></a>**Densification is suppressed along a door base line** (`union_cdt._triangulate`). The threshold edge is part of the union boundary, so the densify loop would drop samples ALONG it and chop the one big door triangle into pieces -- measured on the CharacterGen assassins' cell door, whose **115u base came out as a 26.8u + 21.6u pair, leaving a 571-unit scrap as the Door Triangle** (every vanilla door triangle is >= 992). The line keeps its two endpoints and nothing between, which is exactly what makes the Delaunay span it with a single triangle.

<a id="outline-corners-snap-onto-door-lines"></a>**Ring corners lying on a door base line are SNAPPED to its nearer endpoint, never deleted** (`union_cdt._snap_outline_to_door_lines`). Ribbons meeting a threshold contribute their own corners ON the base line, baked into the polygon before triangulation; the wedge cut then hands each piece a boundary still carrying them, so the doorway is triangulated as several triangles instead of the guaranteed one and the leftovers ship as needles -- measured on **Pinarus's upstairs door 113054, whose 99u base carried intruders at -359.4 and -277.3 and came out as a 2930u^2 door triangle plus a 237u^2 badness-5.5 rogue**. Snapping (rather than deleting) keeps the ring's vertex count and winding, every edge still arrives at a point on the same line, and no ground is added or removed; a deletion instead stretched the mesh over ground no ribbon covered and **cost 5 door-passage samples on AnvilFightersGuild's teleport door**. `DOOR_SNAP_PERP = 4.0` is well under a foot width, so only corners the union genuinely put on the line move, and they move along it.

<a id="door-edge-must-accept-interior-rings"></a>**A door base line may sit on an INTERIOR ring** (`union_cdt._door_edge_on_part`). The threshold edge is part of the union boundary, so a strict interior test silently drops it and the door never gets its forced edge. The test accepts the edge when its midpoint is within the polygon or within `tol` of the FULL boundary, holes included: **Arvena's upstairs door base sat on a hole of its sheet, 137u from the exterior ring**, so an exterior-only test never claimed it and the door lost its reservation.

<a id="steep-refinement-keeps-stairs-alive"></a>**Steep ground is refined after the CDT** (`union_cdt._refine_steep`). The CDT builds from ring vertices only, so a stair or ramp comes out as a few large triangles whose corners span more than a storey step -- the per-surface emission drops them and the whole stair vanishes. Any triangle a steep centerline seed lands in is bisected at its longest edge, splitting the neighbour across that edge at the same midpoint so the mesh STAYS conforming. Door triangles and their ring edges are exempt: the doorway must stay ONE triangle.

<a id="ledge-links-spread-along-the-lip"></a>**Ledge links are emitted for EVERY qualifying edge pair along a lip, not just the closest** (`clean_validate.find_ledge_links`). A vanilla balcony carries a ledge link on several triangles along its edge -- **Skyrim.esm census: half of all mesh->target ledge pairs have 2-12+ links, owning-triangle median area 9,398, linked-edge median 135u** -- so an actor can drop off anywhere along it. Without that spread the **CharacterGen assassins stood at the balcony edge and stayed there**.

Endpoint pairing is tried both ways round, because the two boundaries wind in opposite directions and the matching endpoints are as often swapped as not. Nearest pairs go first, with ONE link per triangle so links spread along the lip rather than stacking on one edge (an NVNM triangle has three link slots, but one slot per lip triangle is the vanilla shape). The ordering is deterministic -- sorted by (dxy, vertex ids) -- for byte-reproducibility.

<a id="union-geometry-constants"></a>**The union's shared constants, and what fixed each value** (`union_geom.py`).

* `SAME_SURFACE_Z = 36.0` -- heights within this at one point are ONE walkable surface. Small enough that a genuine step between stacked sheets is never fused, large enough to absorb where two ribbons cross on a slope.
* `STOREY_GAP_Z = 120.0` -- two levels are different STOREYS only this far apart. Anything closer is one surface (a stair step, a ramp, two ribbons at a slight angle) and must produce ONE triangle; emitting both stacks them (measured: **levels 39u apart on a Chorrol stair**).
* `REACH_TOL = STOREY_GAP_Z` -- how far a corner's ground may be from a surface and still count as ON it. Deliberately storey-scale: a stair triangle legitimately spans ~65u across one edge (a 128u edge on a 27-degree flight), so a step-sized tolerance tears flights mid-air (measured: **`REACH_TOL=MAX_CLIMB` opened a 127u hole in the middle of Pinarus's staircase**). Wall-like triangles a wide reach admits are rejected by `WALL_SLOPE_COS` instead -- slope separates a stair from a wall cleanly, where no reach distance can.
* `WALL_SLOPE_COS = 0.574` (cos 55 deg) -- steepest triangle the mesh should carry. Walkable ground tops out at `MAX_SLOPE_DEG` (46) and real flights measure 27-40 degrees; steeper than 55 is a WALL, the near-vertical flaps that rendered as "a triangle sticking up at the top of the stairs" (**58-84 degrees measured**). `_drop_walls` removes one ONLY when its neighbours stay connected without it: dropping walls at emission time instead **tore ImperialDungeon04 and BarrenCave apart**, because on jagged cave ground a steep triangle is sometimes the only link between two ledges.
* `FLAP_EDGE_DROP = 40.0` -- a free edge dropping this far is a SILHOUETTE over open space, not a join to adjoining ground; two on one triangle make it a flap hanging into a stairwell. Sized above a stair's per-triangle rise (~65u, but shared with the next tread) and well under a storey, so a real ledge -- ONE free edge, at its bottom -- is never matched however deep the drop.

<a id="ribbon-polygon-memoisation"></a>**The ribbon-polygon memo is keyed on strip IDENTITY** (`union_geom._ribbon_polygon`). The nested ribbon-pair loops (`_same_surface_region`, `_split_plan_overlaps`) ask for the same strips over and over: **379,250 calls on a cell of a few thousand strips, ~12.7s of a 33s build**, with the invalid-outline repair re-running its buffer/union work every time. Strips are plain dicts that live for the whole build and are never mutated after their polygon could first be asked for, so `id()` is a sound key; the cache holds a reference to every strip it keys, so a freed dict's id cannot be recycled onto a stale entry. Cleared per build by `_ribbon_cache_clear`.

<a id="invalid-ribbon-outline-repair"></a>**A self-intersecting ribbon outline is repaired by union PLUS a centerline band** (`union_geom._ribbon_polygon_uncached`). A grown outline can self-intersect where two cross-sections' rails cross at a sharp concavity. `buffer(0)` alone is NOT a safe repair: on a bow-tie it returns a MultiPolygon and shapely's union keeps the lobes separate, losing the part of the ribbon that bridged to a neighbour. Measured on ChorrolFightersGuild: exactly the **7 ribbons with invalid outlines** -- (22,23), (22,24), (26,43), (26,42), (26,27), (41,42), (25,26) -- were the ones whose sheet unioned into **5 disjoint parts**, with ribbon (22,23) appearing in two parts without joining them; **pathgrid=1 but navmesh=4**. The repair keeps EVERY lobe and, critically, covers the CENTERLINE with a minimum-width band: the pathgrid asserts an actor walks it, so the ribbon must always contain it -- which is also what makes two ribbons sharing a node overlap into one sheet.

<a id="clip-strip-cuts-at-the-node-projection"></a>**A donated strip is cut at the NODE'S projection, not the segment end** (`union_geom._clip_strip_near`). A stair strip is extended up to 48u beyond its end node (`RIBBON_STAIR_END_EXTEND`), so measuring from the endpoint left only r-48u of covered disc and the piece's rim lost its levels -- **which is what disconnected ChorrolFightersGuild**.

<a id="height-follows-the-pathgrid-line"></a>**Ribbon height follows the straight A->B line, never re-fitted collision** (`union_geom._height_on`). The pathgrid edge IS the walk ramp, so the ribbon's angle is the LINE's angle. Re-fitting to sampled collision was tried and is wrong: it changes the staircase's angle away from the pathgrid line the designer drew, the one thing this model treats as ground truth. A STEEP strip may carry a `prof` polyline (`corridor._surface_profile`) whose endpoints ARE the node heights but whose interior follows the real treads -- the chord of a long stair edge runs tens of units off the surface wherever the flight does not span the whole edge. This projection must stay IDENTICAL to the native mirror in `grow.cpp` (`py_levels_at`), or the scalar and batch level lookups disagree.

<a id="uniform-hex-lattice-triangulation"></a>**Uniform triangulation is a hex lattice + centroid-inside Delaunay** (`corridor_union._triangulate`). The old approach earcut the polygon after cutting it on an 8u grid and produced needles and slivers along every boundary -- **20% of triangles had an edge ratio > 3, some > 400**. Vanilla Skyrim navmeshes are near-uniform ~`target_edge` triangles, so: (1) sample interior Steiner points on a HEX lattice at `target_edge` spacing -- hex, not a square grid, so the Delaunay is near-equilateral rather than right-isoceles; (2) densify boundary rings at the same spacing so boundary triangles match interior scale; (3) Delaunay the whole point set and keep only triangles whose CENTROID lies inside the polygon, which honours the outline and every hole exactly without a constrained triangulator.

`steep_seeds` are points along STEEP ribbon centerlines (stairs, ramps). A uniform `target_edge` triangle on a staircase climbs more than one storey gap across its corners and is dropped by the per-surface emission -- the whole stair vanishes. The seeds are forced in at fine spacing so the stair keeps short, gently-climbing triangles.

<a id="door-triangle-is-reserved-as-a-hole"></a>**The door triangle is RESERVED as a hole, not coaxed out of the Delaunay** (`corridor_union._triangulate`). Vanilla marks a door with ONE triangle whose long edge is the whole doorway. Every attempt to get that from the triangulator failed the same way: the door line lies on the union BOUNDARY, so the ribbon's own outline corners land on it and split it into 3-4 pieces, and no amount of seeding, keep-out or constraint recovery can remove a corner already baked into the polygon. So the region is cut OUT of the polygon before triangulation -- the triangulator fills around it as a hole and cannot subdivide what it never sees. The wedge's shape is fixed by `corridor_doors` (full doorway base, deterministic depth, apex on the pathgrid's side); the reservation never moves, shrinks or flips it. A wedge that severs the sheet is allowed -- the MultiPolygon branch triangulates every significant piece. The old guard that SKIPPED reserving such a door instead demoted it to whatever sliver the fallback containing-triangle link happened to find.

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
6 exterior): every one now reports CLEAN under `tools/navmesh/check.py`'s rules,
with coverage/steep/island metrics unchanged.

### Exterior coverage: the cap counts BUILT floor, ONE code path
<a id="exterior-coverage-one-code-path"></a>

**Code:** `native/src/navgrow/grow.cpp` (`grow_half_width`), `corridor._stands_on_land`, `world.LandField`. **NOT in-game verified.**

A 160u half-width cap clipped exteriors: `WrldMorrowind -17 -51` (Tamriel
Rebuilt) meshed **13.7%** of the cell; it now meshes **62.7%**. Interiors and
exteriors run the SAME march — no cell-type branch. What differs is the ground
under each step.

**Why interiors must not change at all.** Every stop rule that altered interior
widths scrambled the union downstream, moving cracks from cell to cell with no
pattern (`tools/navmesh/sweep.py`, crack counts, HEAD first):

| rule | ImperialDungeon01 | Leyawiin hall | AnvilFightersGuild |
|---|---|---|---|
| HEAD, cap 160 | 2 | 2 | 0 |
| halfway line to the nearest centerline | 7, +4 miss | 1 | 0 |
| wall + floor only | 7, +4 miss | 13, +29 miss, 2 comps | 3, 4 comps |
| stop AT the first crossed centerline | 12 | 6, 2 comps | 0 |
| cap 160 only after a crossing | 2 | 6, 2 comps | 0 |

The mechanism behind the uncapped failures: a hall edge 1000u away threads a
doorway and lays its ribbon over another room (AnvilFightersGuild, edges (1,2)
and (4,16) covering the NW room). The rule shipped leaves all nine reference
interiors IDENTICAL to HEAD (same triangle counts, components, miss, crack).

**The cap is a budget of BUILT floor.** A step whose walkable sample is a LAND
triangle (`land_from` marks where LAND starts in the walkable soup) is free; any
other step spends `RIBBON_GROW_MAX_HALF`. A doorway leak is the cap's whole
reason and terrain has no doorways. An interior has no LAND, so it is HEAD.

**Past another centerline the cap binds on LAND too**
(`NeighbourField::first_crossing`): the rail ends at max(crossing, cap). Ground
beyond another corridor's line is that corridor's to cover. Measured on two
exterior cells, removing this stop raised cracks 88 -> 195 and 437 -> 502.

<a id="terrain-standing-ribbons-follow-the-land"></a>**A ribbon whose nodes
stand on LAND follows the LAND** (`_stands_on_land`: each node snapped within
`NODE_SNAP_RADII[0]` of the terrain, or lies in a neighbouring cell). The cap
was not what clipped hilly exteriors — the flat-ribbon floor test was: a rail
stopped once terrain left its chord by `MAX_CLIMB` (median rail 96u; 303u with
that test off). For such a ribbon:

- the march measures wall and floor above the LAND under each step, and its
  stations start AT the land (`_plan_stations`);
- its height at any point IS the land (`union_geom._height_on`, native
  `levels_at` column 12). "Chord plus terrain rise" was tried first and shredded
  slopes: chords float above or sink below a hillside by different amounts, so
  overlapping ribbons disagreed;
- it is never a staircase: a hill path over `RIBBON_GROW_MAX_SLOPE` otherwise
  took a tread profile and sat 30u under the terrain;
- the sheet splitter skips its node-height shortcut (`union_sheets._z_ranges`):
  two terrain ribbons from different hill heights read as different storeys and
  one cell fell into **59 sheets instead of 2**;
- emission compares corners in heights ABOVE the land (`_emit_surfaces`), so a
  hillside is one surface. At HEAD the same cell had **212 corners with
  conflicting levels and 588 triangles emitted twice**; now 0 and 0.

A ribbon with a node on built floor (bridge, porch) stays flat, as before.

<a id="far-widths-are-eroded"></a>**Past the cap every rail and disc ray is held
to its SHORTEST neighbour** (`_erode_far_widths`, +-`FAR_WIDTH_WINDOW` stations,
+-1 disc ray); interiors never reach it. Adjacent rays stopped by a rock or a
steep LAND patch end at wildly different distances, and the sawtooth outline
triangulates into fans of needles — in-game report, `AnvilAnvilExteriorCastle03`.
Less coverage, clean outline.

<a id="land-slits-are-closed"></a>**Hairline gaps between terrain ribbons are
closed** (`corridor_union._close_land_slits`, `corridor._land_slit_ok`). Long
ribbons from different edges run nearly parallel and leave slits under
`2 * LAND_SLIT_HALF` wide that triangulate into needle fans. The sheet's union
is buffered out and back; each filled piece is kept only if a wall probe along
its outline, at actor height above the LAND, finds nothing — so a fence or a
trunk standing in a gap keeps its gap. Terrain sheets only.

<a id="land-sheets-bisect-long-edges"></a>**A terrain sheet bisects every edge
over `LAND_MAX_EDGE_RATIO` target edges** (`union_cdt._refine_steep` with
`steep_pts=None`, then `_flip2d`). The CDT fans a far vertex onto each 128u
segment of a long straight ribbon side; those needles are too thin to contain a
hex-lattice point, so `_hex_refine` never breaks them —
`AnvilAnvilExteriorCastle03` emitted **427 needles in 1,602 triangles**.
`_fan_split` fans from corner 0, which is only right when the marked edge is the
one OPPOSITE it: with edge (a, b) marked it yields the collinear (a, m, b) plus
the ORIGINAL triangle, i.e. no split at all (that cell's everywhere-refine made
**44,974 zero-area triangles out of 46,791**). The terrain pass therefore fans
from the first midpoint (`from_midpoint`). The stair pass keeps corner 0: its
output is in-game verified and was left untouched — a KNOWN DEFECT, unfixed.
For the same reason a terrain ribbon never seeds the stair refine
(`_ribbon_seeds`): fed hill paths, that pass left T-junction cracks.

<a id="seam-probe-replays-real-jobs"></a>**`tools/navmesh/seam_probe.py` replays
the import's REAL jobs** (`job_trace.load_export`). It used to parse the
plugin's own export only, so for a plugin with masters it resolved no master
bases and saw none of the neighbours' overhanging refs: TR_Mainland (-17,-51)
probed as **728 verts / 1,297 tris over open ground with 104 refs**, where the
import builds **258 / 360 from 122 refs and 10,620 blocking triangles** — every
conclusion drawn from the old probe was about a mesh that does not exist.

<a id="land-rails-overshoot-the-cell"></a>**A terrain rail overshoots its
cell's LAND by one cap, so the cell clip cuts the sheet ON the plane**
(`grow.cpp`, `Land::covers`). LAND covers exactly one cell, so a rail used to
end on its last 8u step INSIDE the cell, and the far-width erosion, the 12u rail
simplify and decimation then pulled the outline further in: the sheet came
within a few dozen units of the seam without lying on it. `edge_links` pairs
border edges whose ends are both within `SEAM_BAND` and whose midpoints agree
within `SEAM_TOLERANCE`, and both cells put seam vertices on the same world grid
ONLY where the clip made the boundary. Measured on the real jobs for TR_Mainland
(-17,-51)/(-17,-50), plane y=-204800: border edges **5 vs 0, 0 matched -> 7 vs
7, 4 matched**. At 128u target edges a near-miss outline still scattered enough
short edges into the band to pair by luck; at 256u it did not, which is why the
coarse terrain sheets exposed it. Rejected: raising the cap to the cell edge for
every terrain rail (it also lifted the crossing limit and the BUILT-floor
budget), densifying near the seam, dilating the polygon, loosening
`_match_seam`.

<a id="land-sheets-mesh-coarse"></a>**A terrain sheet meshes at
`LAND_TRI_TARGET_EDGE` (256u) and refines only where the LAND curves**
(`corridor_union._target_edge`, `union_cdt._refine_land`, `_cuts_relief`). Most
of a cell's build is spent AFTER triangulation and scales with triangle count,
and the NVNM payload is ~16 bytes a triangle. Measured on 12 Oblivion exterior
cells, target edge alone (time / triangles / payload / sample points over 16u
off the ground): **128: 42.9s / 27,305 / 610 KB / 0.4%; 192: 25.4s / 14,156 /
325 KB / 1.3%; 256: 21.4s / 8,983 / 213 KB / 3.2%; 384: 19.8s / 5,919 / 146 KB
/ 8.1%** — the time curve is flat past 256 and the hover tail is not. So 256,
plus a bisection of any triangle whose edge midpoint sits more than
`LAND_RELIEF_TOL` (16u) off the chord between its ends (never below
`LAND_MIN_EDGE`): **23.3s / 10,802 / 252 KB / 1.0%**, with p99 off-ground 25.9
-> 16.2. `AnvilAnvilExteriorCastle03`: 2,269 -> 905 triangles. Walked-line
cracks rise (60 -> 76 on the sample) — accepted by the author for open terrain.
Interiors keep `TRI_TARGET_EDGE`: at 256 twelve interiors saved 8% build time
and DOUBLED their uncovered pathgrid samples (26 -> 58).

<a id="cross-sheet-weld-spans-one-step"></a>**Two sheets' vertices on the SAME
plan spot weld across one step of height** (`union_mesh._weld_match`). The weld
is a 16u sphere, so two sheets that disagree by more than that at a shared
outline corner each keep a vertex and the seam between them is a crack. Hand-
corrected in `tests/navmesh_fixed/Oblivion.esm/imperialdungeon01.2.json`: the
gate quad west of node 137 put (-112.5, -13.2) at **-61.8** (ground -64.3) while
the room sheet, whose flat -84 ribbons lie under the stair flank, put it at
**-84.8** — a 100u crack across the walked line. Vertices from DIFFERENT
emissions within `SAME_PART_WELD_XY` in plan and `MAX_CLIMB` in height now fuse.
Measured on seven interiors: ImperialDungeon01 cracks **2 -> 0** with 11 of 867
triangles changed and the fused vertex at -61.8, the hand fix's own value; the
other six cells changed **0 triangles**. Two things were tried first and
rejected: dropping the far-side quad where the probe mesh already joins the two
faces left a wedge-shaped gap at the same seam, and the quad's own height was
the CORRECT one.

<a id="land-triangles-are-never-walls"></a>**A triangle lying on the LAND is
never a wall** (`union_mesh._steep_triangles`). A sliver's 3D tilt is
ill-conditioned: three nearly collinear corners with a little terrain relief
read as steeper than `WALL_SLOPE_COS`. Indoors slivers are flat and the test
holds; on one Oblivion exterior cell `_drop_walls` deleted **1,135 of 12,205
triangles, all with every corner exactly on the land, and cracks went 11 ->
1,514**. Terrain steepness is already decided upstream — a steep LAND face is
blocking (`world._split_by_slope`) and never gets mesh.

**Measured result** (`tools/navmesh/sweep.py` invariants, eight Oblivion
exterior cells): walked-line cracks **246 at HEAD -> 39**, coverage up 12-28
points per cell. `AnvilAnvilExteriorCastle03` (in-game report: hovering
triangles and long slivers): `tri_check.py` flags **315 at HEAD (JUT 216, SINK
97), 113 on the first terrain-following build (NEEDLE 87), 8 now**; cracks 50
-> 2. `WrldMorrowind -17 -51`: 13.7% -> 62.7%. Cost: the densest cell measured
(143 nodes, 95% covered) builds in 15s against 5.5s; the rest are unchanged.
Thin-obstacle "shadows" were tested as a crack source and ruled out (closing /
opening the rail widths moved 88 to 72 / 85).

<a id="poly-strips-admit-by-containment"></a>**A poly strip admits by
CONTAINMENT, never by radius** (`union_geom._levels_at`). A door quad or a
grown corridor owns exactly its outline. `half` is an admission radius only for
a fixed-width rectangle; for a grown ribbon it is the widest that rail ever got
anywhere along its length, so `distance <= half` claims points far outside the
real ribbon and injects phantom surface levels that split the triangulation —
**Pinarus fragmented into 11 components**.

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

### <a id="pool-orchestration"></a>Pool orchestration (`navmesh/pool.py`)

**Code:** `navmesh/pool.py`. The parent-side scheduling around `navm_worker` —
gathering jobs, the indexes their carving needs, the cache tag, and the pool.

#### <a id="refs-overhang-their-cell"></a>A quarter of exterior refs overhang their own cell

**Code:** `navmesh/pool.py::_overhang_index`. A cell's geometry was gathered
from `refr_by_cell[cell_fid]` alone — the refs whose `ParentCELL` is this cell.
But an exterior placement is parented to the cell containing its ORIGIN, and
large statics routinely extend well past that square.

Measured over Oblivion.esm (`export/Oblivion.esm`, non-persistent exterior
cells only, refs whose centre lies inside their own parent square):

| | count |
|---|---|
| exterior cells | 33,540 |
| placements with a bounding radius | 628,086 |
| **reaching into a neighbouring cell** | **168,197 (26.8%)** |
| centred outside their parent cell (excluded) | 31 |

Reach past the border, in game units: 72,787 under 512; 24,675 at 512+;
25,761 at 1024+; 23,274 at 1536+; 11,623 at 2048+; 4,727 at 2560+; 5,350 at
3072+. The largest single reach is 9,864 units — more than two cells.

Such a ref carved nothing in the cell it visually occupies. AnvilBayEast01
(grid −47,−9) is the case that exposed it: `AnvilBoardwalkComplete01`
(`MODB=1721.96`) is placed by refr `00014CFB` at X=−193042.2, whose origin
sits in grid −48,−9 and whose `ParentCELL` is therefore `0000AC27`, the
neighbour. The pier reaches to X=−191320 — 1,192 units into AnvilBayEast01 —
yet none of its 189 walkable triangles were ever gathered there. The result
was a pier-shaped hole in both the navmesh and the transplant render, with a
completely healthy collision cache.

Persistent (dummy) cells are excluded: their refs are scattered worldwide and
their `XCLC` says nothing about placement, so they are not overhangs.

#### <a id="speedtree-model-keys"></a>A TREE base names a `.spt`, not a NIF

**Code:** `navmesh/pool.py::_model_key`. TREE bases carry a SpeedTree path —
`\ShrubSeabuckthornSU.spt`, `\Dbush16.spt` — with a leading separator, no
directory, and a `.spt` extension. The speedtree stage rebuilds each one as
`<ns>/speedtrees/<name>.nif` (`asset_convert/asset_pipeline.py::convert_speedtrees`),
so BOTH the directory and the extension differ from the authored path.

The old key appended `.nif` rather than replacing `.spt` and never added the
`speedtrees/` directory, producing `tes4/shrubseabuckthornsu.spt.nif`. No such
entry exists, so `get_collision` returned None for **every TREE in the game**:
Oblivion.esm ships 142 speedtree collision entries, all with non-empty soups,
and all were unreachable. Trees carved nothing and drew nothing in the
transplant editor. TREE is in `_BLOCKING_BASE_TYPES`, so this was silent — the
index built happily, it just pointed at keys that could not resolve.

Found via AnvilBayEast01, where every seabuckthorn was missing from the render.

**Why the tag excludes collision.** It hashes the navmesh generator SOURCES
only, so editing any navmesh code (params included) invalidates every entry
automatically. Collision deliberately does NOT enter here. It used to, as
(size, mtime) of `collision_cache.bin`, which was wrong twice over: mtime is
machine-local and survives neither git nor an archive round-trip, so the tag
differed on every machine and a published cache would have missed 100% for
every downloader; and whole-file granularity meant replacing ONE mesh
invalidated all ~8,200 Oblivion entries. Collision now enters per-cell and
per-mesh via `pgrd_to_navm._geom_hash`, so a changed mesh only misses the cells
that place it.

**Why the tag is stamped only after a generation pass.** Merely asking for the
tag must not certify a cache as current, or a tool that reads the stamp would
mark a stale cache fresh. Nothing in the import reads `CACHE_TAG` — every entry
self-validates — but the pre-push gate and publish manifest rely on it, and
mtimes cannot answer the question (a checkout, branch switch or unzip rewrites
them).

**Masters are REQUIRED for the base-model and door indexes.** A dependent plugin
overwhelmingly places its MASTER's statics — 83% of
TWMP_ValenwoodImproved.esp's 129,371 placements name a base that exists only in
Oblivion.esm. Without the master export every such REFR resolves to no model
key and carves NOTHING, so actors path straight through the master's buildings
and rocks. Masters go in FIRST so the plugin's own records win the key. The same
holds for DOOR bases (2,165 placements there): membership of the door map IS the
"is this a DOOR" test, so missing them means the navmesh neither chokes nor
links at those doorways.

**Exterior teleport doors are PERSISTENT refs** parented to the worldspace's
dummy cell, so the grid cell they physically stand in never lists them.
Exterior navmeshes got no door triangles (89/6,516 vs 1,612/1,640 interiors)
and cross-door pathing broke at every house door and city gate. Each
worldspace's persistent door refs are bucketed by the grid square their POSITION
falls in, and the matching cell's job stamps and links them like its own.

**Job order mirrors the group builders exactly** — interiors by block/sub-block,
then exteriors per worldspace — so the FormIDs handed out match single-threaded
allocation.

**A process pool, not threads:** `convert_PGRD` is dominated by pure-Python work
holding the GIL (only scipy's Delaunay releases it). The worker lives in the
light `navm_worker` module so each spawned child avoids re-importing the whole
pipeline, and takes no writer, so nothing unpicklable crosses the boundary.
`max_tasks_per_child` recycles workers so scipy allocator fragmentation cannot
grow unbounded across 8k+ cells. The initializer also runs ONCE IN THE PARENT:
an exception inside a pool `initializer=` cannot be returned (the worker dies
first, and `subprocess_flags` points multiprocessing at pythonw.exe, so its
stderr goes nowhere), which is why a failure there once produced a log with no
cause in it.

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

- **Quality invariants** (`tools/navmesh/audit.py --interiors N` sweeps many cells
  in parallel; `tools/navmesh/tri_check.py --cell <id>` for one). The metric that matters is
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

  <a id="needle-split-must-improve-the-worst-shape"></a>**A needle split must
  IMPROVE the local worst shape** (`corridor_clean._split_needles`). Bisecting
  a triangle whose SHORT edge is tiny halves the long side but keeps the tiny
  side, so the split is strictly worse: measured, two r=11 slivers appeared
  where one r=4 had been. The pass therefore computes the worst badness over
  the edge's owners before and after and abandons the split unless it strictly
  drops — `after >= before` means leave the needle alone.
- **Obstruction is decided in WORLD SPACE, never per-mesh.** An object obstructs
  iff it rises more than MAX_CLIMB above the floor beneath it — so rugs/pillows
  are walked over, tables/barrels are routed around, with NO size gate or rug
  list. Collision meshes are ORIGIN-CENTERED, so any per-mesh height rule is
  meaningless (a table's local extent says nothing about how high it stands).
- **Collision cache**: `asset_convert/collision/collision_extract.py` reads the CONVERTED
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
- **Iteration tools**: `python tools/navmesh/render.py <cell> [--collision]`
  renders the generated navmesh (green) OVER the collision layer — walkable dim,
  BLOCKING/walls RED — plus pathgrid and door markers (cyan threshold lines;
  white core = teleport door). `--focus X,Y --span N` zooms a world-coord
  window; `--ids` labels triangle indices + vertex heights; `--quality`
  colors steep triangles red and needles magenta. Exterior cells can be
  addressed as `--cell grid:X:Y` (colon form survives comma-list splitting;
  Windows filenames can't hold `:` so outputs sanitize it).
  `tools/navmesh/tri_check.py --cell A,B,...` checks EVERY triangle of a
  cell's mesh (slope/zspan/edge-ratio/aspect/area + JUT/SINK = signed distance
  off the real collision surface at its own XY) and lists offenders — the way
  the furniture-hoist and needle defects were found and verified fixed.
  `tools/navmesh/probe.py --cell X` reports pathgrid-on-floor coverage and Z
  error; `--probe X,Y` dumps nearby REFRs/pathgrid plus the span column
  raw/stamped/filtered — the ground-truth view of any one spot.
  `tools/navmesh/audit.py --interiors N --exteriors M` sweeps both cell kinds
  and reports UNCOV%/BROKEN/STEEP/FLOOR/ISL/TINY/SLIV%/MICRO per cell (UNCOV
  measures the EDGE's z-range, not the chord — the generator follows the
  surface, and a long cave edge's chord cuts open air two storeys up).
  `tools/navmesh/perf.py --cell X` cProfiles one cell's build (how the
  shadowed()/plane_err hotspots were found).
- **NVNM binary layout** (validated byte-exact against Skyrim.esm via
  `tools/navmesh/dump.py`): all arrays use U32 count prefixes; CRC of
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
- **Reusable tool**: `python tools/navmesh/dump.py <esm> [--navi|--navm]
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
`tools/esm/pack_validate.py`. Geometry is fine: the destination cell's mesh
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

**Audit tool**: `python tools/navmesh/connectivity.py <esm> [--ref Skyrim.esm]
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
x −48..−46, y −9..−7) via `tools/navmesh/seam_probe.py`: before = 8 isolated
islands; after = **104 reciprocal Portal links, all 8 cells in ONE connected
component**. InterCell yield jumped with the export fix (e.g. grid (−47,−8):
30→42 of 58 kept; total portals in the patch 30→104). `tools/navmesh/seam_probe.py
--wrld <hex> --gx lo hi --gy lo hi` reports per-cell seam-edge counts, InterCell
kept/raw, reciprocity, and the connected-component structure for a cell range —
use it to spot-check a region without a full rebuild.

### 🔴 NAVI is a SINGLETON override + must mirror connectivity (found 2026-07-21)

The edge-link stitching above was necessary but NOT sufficient — Arielle
(MG04, destination in her OWN cell, mesh verified connected across the stairs
by `tools/navmesh/reach.py`) still never walked. Two more defects in the NAVI
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

**The NAVI takes the fixed singleton id** `0x00012FB4`, not a generated one.
Generated ids are hashed from their source record, so removing the NAVI's own
allocation moves nothing else — an earlier burn-one-id workaround, needed when
ids came from a positional counter, is gone.

**Reachability tool**: `python tools/navmesh/reach.py <esm> --from-ref <fid>
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

## Ribbon construction (`corridor.py`)
<a id="ribbon-construction"></a>

**Code:** `tes5_import/navmesh/corridor.py`

<a id="stations-are-planned-then-marched"></a>**Station planning is split from marching so the grow crosses into C++ ONCE** (`_plan_stations`). It returns an (N, 9) array plus a plan recording how to reassemble the results. The geometry each station measures against is fixed, so batching cannot change any result -- the march was already order-independent by design, measuring against collision and never against another corridor's grown width.

<a id="only-dead-ends-extend"></a>**Only DEAD ENDS get the end extension** (`_build_corridor_strips`). Extending past a node another corridor also uses puts this corridor's stub entirely inside that corridor -- guaranteed double coverage at every junction, and the dominant residual overlap (**collinear pairs sharing a node overlapped for 22 triangles each**). At a dead end there is no other corridor, so the stub is the only thing reaching the wall or door ahead and costs nothing. Overlap between ribbons is resolved by the union, so a ribbon never needs to stop short of a junction.

<a id="a-steep-ribbon-is-never-grown"></a>**The FLIGHT of a steep edge is never width-grown, and keeps a wider fixed width** (`_grown_outline`, `_on_flight`; the flat approaches DO grow, see [steep-edges-are-grown](#steep-edges-are-grown)). The ribbon is a tilted plane, so a perpendicular rail immediately leaves the treads -- **the Guild's stair edge grew to 82u and put mesh through the wall beside it**. But a plain corridor's width is too narrow at the mouth: a steep flight keeps `RIBBON_STAIR_HALF_WIDTH` so it presents a mouth comparable to the landing it joins, since otherwise the two meet only at the landing's CORNER vertices -- measured at **the top of Pinarus's stairs, where the entire route from the landing onto the flight ran through two 27-degree wedges (one with edge ratio 6.1) hanging off those corners, each dropping 39-45u**. The mesh was ONE component and still not walkable.

<a id="stair-end-extension-was-reverted"></a>**A stair-end EXTENSION was tried twice and is wrong both ways** (`_build_corridor_strips`). Sloped, it drives the ramp plane past the node, up into the air above the landing at the top -- **measured: ramp triangles at z=93 where the landing is z=69**. Footprint-only, the overhang keeps interpolating the ramp slope while the landing is flat, so the flight's last row tilts UP off the landing edge -- **measured a 38.9-degree joint whose ramp apex sat 14.8u above the shared edge**, a connection an actor cannot cross. A stair ribbon therefore runs node to node exactly, like any other edge.

<a id="steep-heights-follow-the-treads"></a>**A steep edge's heights follow the REAL treads, not the chord** (`_surface_profile`). The pathgrid draws a straight chord node to node, but a real staircase rarely descends along the whole chord: **Pinarus's flight starts ~90u east of its top node, so the chord ran 39u BELOW the actual landing there, the stair ribbon reported z=30 where the real floor is 68.6**, and the union emitted a near-vertical triangle joining the two fictions (`tools/navmesh_tri_check` measured the same chord error as **+46/-49u float over the whole flight**). The path is a shortest path over the WALKABLE LAYERS along the line: at each 16u station the candidates are every walkable surface within the edge's own z range, a transition may climb at most one step, and the path must START at the near node's height and END at the far node's. That end constraint is what selects the treads -- a greedy walk anchored on the previous height simply followed the GROUND FLOOR continuing UNDER the flight, **ending 260u below the far node with a cliff at the anchor**. The flight is the only layer path that actually arrives. This is NOT the reverted "re-fit the line to collision" experiment, which changed the flight's overall angle: the profile keeps both endpoints and the plan line, replacing only the straight-line INTERPOLATION between them.

<a id="profile-samples-the-cross-section"></a>**The tread profile samples the whole CROSS-SECTION, and falls back to a clamped chord** (`_profile_stations`, `_clamped_chord`). Centerline-only sampling returned no profile for all three stair edges at ImperialDungeon02's prison stairs (n194 to n195/n196/n197): the lines clip the flight's corner, so along the line the layers jump -1248 -> -1195 (53u, more than a step) and the one-step walk finds no path. The chord then ran **32u BELOW the flat -1184 floor for 90% of a 172-281u edge** -- the "clipping into collision" the hand correction (`tests/navmesh_fixed/Oblivion.esm/imperialdungeon02.json`) lifted back to -1187. Sampling +-half/2 and +-half across the line finds the treads at -1230/-1213 beside it and the path closes. When it still cannot, each node's level is held for as long as its own floor continues and one ramp joins the two; only when those plateaus overlap (a lower floor running under the upper) is the chord kept.

<a id="steep-edges-are-grown"></a>**A steep edge IS width-grown, with each station's height taken from the profile** (`_edge_march_rows`). The blanket "never grow a stair" rule left every flat approach a steep edge crosses at the fixed 64u half-width: ImperialDungeon02's three prison-stair edges span 160-250u of FLAT -1184 floor before the 90u flight, and the corridor beside them (y 7300-7373) was covered by nothing but two node discs -- the hand correction pushed the boundary 45-60u north to the pillar line. Marching from the profile height keeps the wall slab and the floor test honest on the flight itself, where the floor test stops the rail as soon as the treads leave the station's level; the flight keeps `RIBBON_STAIR_HALF_WIDTH` as its soft floor where the local profile slope exceeds `RIBBON_GROW_MAX_SLOPE`, and the approaches use the ordinary corridor floor.

<a id="blocked-stations-are-bridged"></a>**A station blocked on BOTH sides at the centerline takes its neighbours' widths** (`_bridge_blocked_stations`). The wall slab straddles the line, so an object hanging over the pathgrid line at head height zeroes both rails: ImperialDungeon03's east corridor (n181-n182) lost **six consecutive stations, 48u of a 74u-wide corridor, to a 4x11x25u block at z 64-90 above the floor** -- the corridor came out as a point in the union. The pathgrid line is the author's assertion that actors pass there, so a station whose line is inside collision cannot be a wall; its widths are interpolated from the nearest stations along the edge that grew on at least one side.

<a id="neighbour-cap-removed"></a>**No rail is capped by its DISTANCE to another centerline** (`grow_half_width`, `grow.cpp`). Two versions were built and both reverted. Precomputing `0.5 * distance-to-nearest-parallel-edge` PINCHED corridor mouths: ImperialDungeon03's N-S corridor (n98-n99) had its right rail cut from 64u to 40u over its last 100u by edge n100-n101 **in the room beyond**, 27u short of the wall, and the hand correction ran that rail straight to the wall. Restricting the query to roughly-PARALLEL edges did not fix it. Testing the halfway line DURING the march (so an edge behind a wall could not cap) was reported in-game as seams across walked paths and measured on ImperialDungeon01 as **cracks 2 -> 7 with 4 uncovered pathgrid samples**. The flaw is shared: a halfway line hands ground to the other corridor, and nothing makes that corridor's perpendicular rails cover it. What replaced both: [exterior-coverage-one-code-path](#exterior-coverage-one-code-path).

<a id="the-grow-cap-is-load-bearing"></a>**`RIBBON_GROW_MAX_HALF` (160u) is load-bearing indoors.** Removing it while the march had only wall + floor tests took **40-48% of stations past 160u (max 2621u)** and through-wall triangles from **9 -> 41, 9 -> 59, 12 -> 19** (floating 4 -> 26 on ImperialDungeon01) -- collision alone does NOT bound a rail, because the floor test binds only beyond `lo` and a single ray threading a doorway or a gap in the blocking soup finds neither a wall nor a departure.

**Coverage metrics cannot see this class of bug, in either direction.** A mesh sprawling through walls still covers the floor, so mean-uncovered IMPROVED while the mesh got worse; and a mesh with its node discs collapsed to nothing has FEWER through-wall triangles, because geometry that does not exist cannot cross a wall. Measure `tools/navmesh/grow_check.py` through-wall counts AND the width distribution per station class (edge rails vs disc rays) when changing a stop condition.

<a id="disc-radius-capped-by-its-ribbons"></a>**A node disc reaches no further than the ribbons meeting at that node** (`_disc_strip`, `_node_reach`). Removing the neighbour cap let every disc ray run to `RIBBON_GROW_MAX_HALF` (160u) whenever walls and the floor test allowed, and the floor test allows ground a full step below: in-game verified discontinuities, both hand-corrected. ImperialDungeon01 node n74 (-1.7, 3019, z -805) at the top of a 27u flight grew a flat 160u disc over the -832/-840 floor 150u away, emitting a second vertex column at (-1.7, 3171) 30.7u above the corridor's own -- a crack an actor cannot cross (`ImperialDungeon01.json` welds them). ImperialDungeon02 nodes n74-n76 (1530, 5480-5709) each grew a 160u disc and the union of three overlapping fans plus the 57u ribbon between them was a fan of seven slivers (`ImperialDungeon02.2.json` collapses it to two triangles). The pre-change generator had neither, because the neighbour cap bounded discs at half the distance to the next edge. A disc exists to fill the notch between the ribbons that meet at its node, so past their reach it has no business unless the ground really is its own floor: its rays are clamped to the largest half-width any incident ribbon reached at that node's station (never below `RIBBON_HALF_WIDTH`), and may run on beyond that only while the walkable layers there hold a height within `DISC_LEVEL_TOL` (2u, the sampler's own layer dedupe) of the node's level (`_level_reach`). A plain clamp was tried first and split ImperialDungeon02's -992 mezzanine into two components (28 -> 12 + 9 triangles): it carries two pathgrid islands with no edge between them, and the discs running over the level floor between them were the only join. The n74 disc in ImperialDungeon01 stops at its ribbons because the floor beyond is 27u below its level; the mezzanine discs run on because the floor is exactly -992.

<a id="stair-nodes-get-discs-too"></a>**A node touching a steep edge DOES get a node disc** (`_plan_stations`). Excluding them was a blanket rule with no stated justification, and it left the corner at the top of a staircase DEAD: measured on **Pinarus, nodes 0 and 1 (the stair's two endpoints) were the ONLY nodes in the cell without a disc -- every other node, 2 through 38, had one**. The upper floor's ribbon coverage stopped at y=146 with the corner beyond it unmeshed, and the union bridged the gap with a single tilted triangle flapping 38.6u under the landing -- the sole, unnavigable link between the two floors. The disc is a FLAT radial fan at the node's own height, which is exactly what a stair top needs since the landing there IS flat, and it cannot spill over the stairwell because each ray marches against real collision and stops at the drop. The disc excludes only its OWN node, via a synthetic self-pair appended to the edge table; the native `NeighbourField` skips zero-length segments so it adds no geometry. Each ray's width floor is 0, because a wall must always beat any minimum or the disc pushes mesh through a wall standing close to the node.

<a id="disc-rays-are-trimmed-at-stairs"></a>**A disc ray is TRIMMED where the surface ramps away** (`_build_corridor_strips`). The march stops at walls and sudden drops, but a surface that RAMPS descends a legal step per station, so a ray at a stair-top node marches the whole flight and the FLAT disc then covers ground 40u+ below its own height. The level lookup answers both heights there and emission bridges them with a near-vertical triangle -- measured at **the top of ImperialDungeon01's prison staircase: disc level 513.8 hanging over stair ground at 457-474**. The trim walks the real surface outward and stops the ray where it has left the node's level by more than a step in total. A collision GAP is bridged rather than stopping the ray, since the march itself saw ground there; only an off-level surface stops it. A node where TWO OR MORE steep runs meet is a mid-flight landing, not where a flight reaches a floor.

<a id="flat-polys-are-clipped-off-level"></a>**A flat polygon gives up ground where a steep ribbon has left its level** (`_clip_flat_poly_off_level`). It keeps the flight's mouth -- the ribbon within `MAX_CLIMB` of its own height, legitimately shared ground where the two must weld -- and surrenders everything further along the flight, so a flat surface can never hang mesh over a stairwell. ANCHORING is what makes it safe: |dz| alone cannot tell "my own flight ramping away" from "an unrelated flight on another storey passing under me in plan", and cutting the latter **opened holes on ChorrolFightersGuild's mid-floor corridors, 37 pathgrid samples lost**. A cut interval is therefore taken only when it is CONTIGUOUS along the strip with a mouth station lying INSIDE this polygon: the flight genuinely joins this surface here, while a storey-below flight has its mouth elsewhere in plan and never anchors. The anchor polygon is built lazily, since most discs and quads have no steep strip in range.

<a id="rails-are-simplified-before-triangulation"></a>**Each rail is Douglas-Peucker simplified before it becomes an outline** (`_build_corridor_strips`, `_simplify`). The march samples a width every `RIBBON_STEP` (8u), so a raw rail carries a vertex every 8u -- and `_triangulate` FORCES every outline corner as a Steiner point, which is precisely what turns a grown room into fans of 8u slivers. Simplification keeps the shape (a wall the rail followed stays straight, a corner stays a corner) at a fraction of the vertices, so the hex lattice governs the interior and triangles come out near equilateral.

<a id="the-union-is-coverage-preserving"></a>**The boolean union is coverage-preserving by construction** (`build_corridors`). Its area is exactly the ground the ribbons cover, and a triangulation of it cannot self-overlap. Cutting the ribbons pairwise instead -- trim, weld, patch the junction -- is an approximation that must handle every configuration (end-to-end, crossing, wedge, collinear), and every case it got wrong appeared as lost ground or stacked sheets.

<a id="wall-cut-splitting-was-reverted"></a>**Splitting the union along wall footprints was tried and REVERTED** (`build_corridors`, `wall_cut`). Walls are Z-dependent but the union is ONE 2D operation spanning every storey, so cutting on all wall footprints fragmented the polygon against walls belonging to other floors -- **Pinarus: 575 -> 908 triangles and MORE wall crossings, not fewer**. Per-storey handling is needed instead.

<a id="the-door-quad-ramps-and-is-clipped"></a>**A door quad RAMPS from its threshold to the mesh under its far edge, clipped and slope-capped** (`build_corridors`). A door at the top of a staircase sweeps its footprint toward the nearest corridor mesh, which is the FLIGHT below it, so the flat quad would cover ramping ground 40u+ under its own height -- measured at **the top of ImperialDungeon01's prison stairs: door quad at 513.8 hanging over stair ground at 457-474**. The clip keeps the quad down to where the flight is within a step of the door's level, exactly where the two must weld. The ramp may then only slope as steeply as walkable ground (the steepest real stair at a door measures ~0.4): `z_far` comes from a mesh probe with a storey-scale tolerance, so a doorway over a stacked lower floor can grab the WRONG storey, and the quad paints a 45-degree cliff across the corridor whose degenerate and wall culls tear real coverage out with it -- **measured on Moranda02 nodes 40/41/57**.

<a id="door-mesh-stays-in-the-union"></a>**The door rectangle stays part of the ONE union** (`build_corridors`). Cutting it out and emitting its triangles separately leaves them sharing no vertices with the surrounding mesh, because the union's own boundary around the hole is sampled independently -- an overlap-and-disconnect, not a fix. Its base line is handed over as a triangulation CONSTRAINT instead. A far-side quad carries no base constraint: one Door Triangle per door, on the primary side. The `door_edges` entry is (base0, base1, apex, storey_z) -- the triangle's exact shape plus the height of the corridor it bridges to, so the claim in `build_union_mesh` can pick the right SHEET when two stacked floors both pass a 2D containment test.

<a id="door-wedge-rings-are-pinned"></a>**The wedge's ring is PINNED through the cleanup passes** (`build_corridors`, `door_pins`). Base corners, base midpoint and apex. Where a door is wider than the ribbon crossing it, the ground beside the reserved wedge is thin crumb geometry that decimation eats -- taking the hole-ring vertices with it, so the attach found nothing within snap range and withdrew the door triangle (**measured on the CharacterGen pen gate**).

<a id="every-centerline-is-sampled"></a>**Every pathgrid centerline is sampled into `pin_xy`** (`build_corridors`). The samples both PIN the mesh over a steep ribbon through decimation and mark a component as pathgrid-carrying so the island pass can never drop it. A steep ribbon keeps only the narrow Phase-1 width, so an edge collapse can eat it outright -- **measured on exterior grid (-48,-8), where all four steep hillside edges lost their mesh entirely, 4/4 midpoints covered before decimation and 0/4 after**, while every flat corridor was unaffected. Each sample is (x, y, z, ux, uy): the direction lets the sliver cull measure the corridor's CROSS-WIDTH at that sample, and consumers reading only x/y/z are unaffected.

<a id="the-wall-sampler-is-lazy"></a>**The door wall-test sampler is built LAZILY** (`build_corridors`). Indexing the blocking soup costs **~0.4s on a dense cell**, and a cell with no doors never asks it a single question; once the grow went native that build was **the second-largest remaining cost**, spent entirely on an object most cells discard unused. Only the DOOR footprint still needs a Python-side wall test, running a few probes per door rather than the ~890k the width march does, so it is not worth crossing into C++ for.

<a id="node-z-snaps-down-onto-collision"></a>**A node's Z is snapped DOWN onto walkable collision, within a window** (`_snap_node_z`). The pathgrid hovers above the walked surface and the navmesh must sit ON it, but the snap must never teleport to a distant floor or rise onto an object standing on the floor -- so it moves only within `SEED_Z_TOLERANCE` above and `SEED_SNAP` below, clamps a far drop, and otherwise trusts the pathgrid. **The surface nearest the node's OWN height within `NODE_SNAP_RADII` (8u, 16u) wins over the one directly under it.** ImperialDungeon02's prison stairs are three tread boxes with a few-unit seam between them, and node n194 (authored z -1204) sits in that seam: directly under it the only walkable layer was the -1248 floor running beneath the flight, so the node was snapped 44u down, every edge leaving it became a chord from the wrong floor, and the mesh ran **32u below the flat -1184 floor for 90% of three 172-281u edges** -- the hand correction in `tests/navmesh_fixed/Oblivion.esm/imperialdungeon02.json` lifted it back. The tread 8u to either side is at the node's height and is the surface the author placed the node on.

<a id="winding-must-be-ccw-in-plan"></a>**Every triangle must be CCW in plan** (`build_corridors`). The mesh is a heightfield, and both the engine and the CK's DOWNFACING rule read a CW triangle as a downward-facing surface. Edge collapses in decimation, and the weld, can flip a triangle's plan winding -- **measured two CW triangles in ImperialDungeon01 once the far-side door quads reshaped the local triangulation**. Orientation is a per-triangle property, so flipping is always safe at this point. The degenerate cull runs once more here too, because the attach can mint plan-degenerate seam slivers of its own (**measured: a zero-width 65u wall along ImperialDungeon01's prison-gate quad seam**) and the finalize-era cull ran before the attach. Drop-down pairs are resolved to FINAL triangle indices only after all of it, since the attach both appends (door and stitch fills) and removes (de-stacked overlap) triangles, and any index resolved earlier would be stale.

<a id="attach-era-scraps-are-dropped"></a>**Attach-era scraps are dropped after `finalize`** (`build_corridors`). The island cull runs inside `finalize`, BEFORE the door attach, and de-stacking there can orphan a mesh triangle whose only edge-neighbours were removed -- leaving 1-2 triangle specks the engine can never route onto (**measured: one each in Pinarus, ChorrolFG, AnvilFG**). A speck carrying a door's threshold is kept: it IS that door's triangle and the door link needs it.

## Mesh cleanup passes (`corridor_clean.py`)
<a id="mesh-cleanup-passes"></a>

**Code:** `tes5_import/navmesh/corridor_clean.py`

<a id="a-flap-is-topological-not-steep"></a>**An open flap is found by TOPOLOGY, never by slope** (`cull_open_flaps`). A triangle with TWO UNSHARED edges that each fall a storey-ish drop is not ground: both long edges are the mesh's own silhouette, so nothing walks across them and nothing connects through them, and the only thing holding it on is the short edge along the floor rim it grew from. It reaches from that rim down to a single vertex on the stairs below, across a void -- the shape the author saw jutting "from the upper left to the right" through a wall in Pinarus's upstairs. `_drop_walls` cannot find it, because a flap is not vertical: **the Pinarus one measures 30 degrees (corners at z 68.6, 68.6 and 18.8 over ~95u of plan)**, comfortably inside the walkable band. Nor is it unsupported -- every sample under it lands on real collision, because the stairwell it spans genuinely has floor at the bottom. What distinguishes it is that real sloped ground is STITCHED: a stair tread shares its edges with the treads above and below, a ramp shares its edges with the floors it joins, and even a ledge an actor drops off has ONE free edge at its lip, never two that both plunge.

<a id="the-pathgrid-is-the-prime-directive"></a>**A triangle carrying a walked sample is never dropped, however shaped** (`cull_open_flaps`). Topology alone is not enough: a ramp landing in an open room legitimately presents two free sloping edges, and the census caught one -- **LeyawiinCastleCountyHall's 105u/91u ramp spanning 44u, a ~25 degree slope the authored pathgrid walks straight down; culling it cost three walked samples**. The same guard is why `_drop_walls` spares steep ground the pathgrid uses. What remains is only ground the author never routed an actor across. Census over the reference cells: **zero matches in ChorrolFightersGuild, ImperialDungeon01, ImperialDungeon05, AnvilFightersGuild and Moranda02, and in Pinarus exactly the triangle the author reported.**

<a id="a-sample-must-fall-strictly-inside"></a>**The walked sample must fall strictly INSIDE, off the corners** (`cull_open_flaps`). A sample sitting exactly ON a corner proves only that the walked line reaches the rim the flap grew from -- which it always does, since that is the floor edge. **The Pinarus flap's single "hit" was precisely its own corner (-269.9, 58.2), barycentric (0,1,0).** Requiring `PG_INSIDE_FRAC` (5% of the triangle, a few units on a 100u triangle) separates "the pathgrid crosses this ground" from "the pathgrid touches the vertex it hangs off", while excluding vertex and edge hits cleanly.

<a id="a-walked-line-outranks-size"></a>**On an over-shared edge, the triangle carrying a walked line outranks every other candidate** (`_make_manifold`). The connectivity guard cannot protect these: dropping the sole mesh in a doorway does not SPLIT the mesh -- the two rooms still meet elsewhere -- it just makes the doorway impassable, so the `components()` test waves it through. Measured at **ImperialDungeon01's prison door 0001FC1E, where three triangles fanned off one 17u edge of a near-collinear boundary chain and the victim chosen by area/neighbours was the only triangle spanning the threshold** -- a 24u hole across the doorway with a 12u height step, the "completely mangled" area in the report. After the walked line the ranking is CONNECTIVITY, then area: dropping purely by area cut the node-disc bridges (the smallest triangles at every junction) and **split LeyawiinCastleCountyHall's mesh into 8170 + 4275 after it had been built as a single connected sheet**. A triangle may only go if the mesh stays as connected without it -- dropping purely to satisfy the manifold rule repeatedly split meshes built as one sheet (**5 components in the Guild**), which is far worse than one over-shared edge: the engine ignores the extra edge, but an island is unreachable.

<a id="no-shape-cull-among-degenerates"></a>**There is NO shape-based cull among plan-degenerate triangles** (`_drop_degenerate_guarded`). Dropping tiny high-badness "spikes" was tried -- 5% of `MIN_TRI_AREA` at 4x the shape contract, with both the local BFS guard and a walked-line exemption -- and is a NET LOSS: it removed the badness-28 needle at ImperialDungeon01's prison door but **cost 25 more defects across the reference cells, LeyawiinCastleCountyHall alone going from 10 crack edges to 21**. A spike is usually the last adjacency between two sheets, and adjacency the engine can use beats a tidy triangle list. Shape is the decimator's and the sliver cull's job, where coverage and width are checked first. The pass drops only footprints under `MIN_XY_FOOTPRINT` -- walls or zero-width seam slivers no actor can stand on, which the CK flags as OPPOSITE_NORMALS -- and never one that is the only route between its neighbours, since a degenerate connector is still a working NVNM adjacency. The guard is a bounded BFS over shared-edge adjacency: the first version re-ran a whole-mesh `components()` per removal trial, **2.6s on one cave cell**. The one-at-a-time fallback re-derives candidate INDICES after every removal, because the first version remapped a pre-built index list while iterating it and the stale indices deleted arbitrary HEALTHY triangles once the list had shifted -- measured on **Moranda02 as 122 walked-line samples losing their (7,000u^2) covering triangles**.

<a id="an-island-is-dropped-only-if-unreachable"></a>**An island is dropped only when it reaches NO cell exit -- never by size** (`_drop_unreachable_islands`). A component is KEPT when it comes within `ISLAND_DOOR_RADIUS` of a door, or (exterior) touches the cell border where a worldspace edge-link continues it into the neighbour cell, or carries a PATHGRID line. The pathgrid is the one input asserting "an actor walks here", so a component covering it is reachable BY DEFINITION however isolated this cell's mesh makes it look -- without that rule a steep hillside ribbon, kept narrow because steep edges are not width-grown, was dropped wholesale on **exterior grid (-48,-8): all four of its pathgrid edges lost their mesh, 4/4 midpoints covered before the island pass and 0/4 after**. Everything still connected to the main body is kept as one component, so only a component BOTH disconnected from the main mesh AND reaching no exit is noise.

<a id="island-drop-is-a-known-workaround"></a>**Some dropped fringe islands are REAL coverage -- this pass is a stopgap** (`_drop_unreachable_islands`). Verified on Chorrol: their centroids are inside no main-component triangle. They arise where the retriangulation pinched a surface to a single-vertex bowtie, leaving a corner edge-detached. The proper fix is to seed the triangulation so the neck stays edge-connected, or to split the bowtie vertex, NOT to drop. Until then an island is dropped only when unreachable -- connected to no cell door and no worldspace border -- so a doorstep or a border-crossing scrap is always preserved even if tiny.

<a id="finalize-is-a-backstop"></a>**`finalize` is a backstop, not a remesh** (`finalize`). `corridor_union` already yields ONE connected non-overlapping surface, so this welds, guarantees manifold, drops stray islands and compacts -- no decimation of its own. Ledges come back as MARKS (centroids) because later passes shift indices; the caller resolves them with `_resolve_ledges` LAST. A component reaching a door center or the exterior `cell_bounds` leads out of the cell and is KEPT. `cs` and `pinned` are accepted for signature stability and unused. Only DOORS pin the decimator, since a collapse at a threshold kills the Door Triangle: `pin_xy` carries every pathgrid sample and is used by the island pass, so pinning all of it would disable decimation everywhere. `door_pins` carries the reserved wedges' RING points -- base corners, base midpoint, apex -- because decimation collapsing those left the attach nothing to snap the door triangle to where the doorway outreaches its ribbon.

<a id="badness-catches-needles-and-caps"></a>**Shape badness is the max of two normalized terms** (`_badness`). `max(edge_ratio / MAX_EDGE_RATIO, aspect / MAX_TRI_ASPECT)`: the ratio term catches needles (one short edge), the aspect term catches CAPS (all edges comparable, near-zero height) which the ratio cannot see. 1.0 is exactly the contract boundary.

## Boundary sliver cull (`corridor_clean.cull_boundary_slivers`)
<a id="boundary-sliver-cull"></a>

**Code:** `tes5_import/navmesh/corridor_clean.py`

<a id="a-residual-needle-is-fringe"></a>**A residual boundary needle is FRINGE, not usable ground** (`cull_boundary_slivers`). After collapses and flips have done what they can, a needle left on the outline means the outline's shape does not admit a good triangle there. Per the design brief those little bits are simply REMOVED: an actor loses a sliver of fringe it could not stand on anyway, and the mesh keeps only triangles honouring the shape contract. A triangle qualifies when `ratio > CULL_SLIVER_RATIO` and `area < CULL_SLIVER_MAX_AREA`, or `area < MIN_TRI_AREA` -- where ratio is normalized badness, 1.0 being the contract.

<a id="fringe-needs-two-boundary-edges"></a>**True fringe has TWO boundary edges, never one** (`cull_boundary_slivers`). A triangle with a single edge on the outline and its other two deep in the interior is a WEDGE filling a concave pocket: removing it does not trim the fringe, it bites a slim V into walkable ground, apex inward, with open boundary down both new sides. Coverage and crack metrics are both blind to that -- no ground is uncovered, no walked line crosses the new boundary -- so it shipped while being plainly visible on the staircase. Measured on **ImperialDungeon01, ONE cull round took the cell from 1 open notch to 7, two of them bitten out of the tower stairs** the author reported twice. With two boundary edges the triangle is a corner of the outline with one edge back into the mesh, and removing it leaves the remaining boundary running straight through.

<a id="the-walked-line-is-sacrosanct"></a>**Only a sliver that is the SOLE cover of a walked sample must stay** (`_samples_still_covered`). A sliver merely GRAZED by the pathgrid line at a corner shares those samples with its neighbours, and removing it costs the line nothing. The replacement cover must be within a STEP of the sample's own height (or of this triangle's, when the pin carries no z): an **80u window was tried and let a stacked cave ledge 50-70u below count as cover -- the cull then ate the real ledge and BarrenCave lost 39 walked-line samples**.

<a id="corridor-width-is-a-contract"></a>**A cull may not squeeze a walked line under the width contract** (`_narrows_corridor`). Only samples whose corridor is ALREADY tight protect their fringe; in a wide room the same fringe triangle culls freely. The width is re-measured against the LIVE mesh, so a chain of culls stops the moment the contract (~half a doorway) is at risk.

<a id="a-cull-can-never-disconnect"></a>**A sliver that bridges its neighbours is kept** (`cull_boundary_slivers`). Before removal, a bounded BFS (128 steps) checks that every neighbour stays mutually reachable without it, so a cull can never disconnect the mesh. Culls are also bounded in total by `CULL_SLIVER_AREA_FRAC` of the mesh area, and a triangle touching a door-pinned vertex or carrying a border edge on the exterior cell seam is never a candidate -- the Door Triangle region is a contract with the engine, and `build_edge_links` stitches the neighbour cell against the seam.

## Decimation (`corridor_clean.decimate`)
<a id="decimation"></a>

**Code:** `tes5_import/navmesh/corridor_clean.py`

<a id="decimate-collapses-short-edges"></a>**Decimation collapses the shortest edges under `DECIMATE_MIN_EDGE`** (`decimate`). The grown ribbon outlines contribute many boundary corners, and a corner landing a few units from a lattice point yields a needle however good the point sampling was. Rather than tune the sampler for every case the needles are removed directly, turning fans of slivers into big well-shaped triangles without touching coverage. A collapse must never move a BOUNDARY vertex, never touch a PINNED vertex, never flip or degenerate any triangle around it, and never make the worst edge ratio of the affected triangles worse than it already was -- so a collapse can only improve shape.

<a id="the-outline-may-not-move"></a>**The OUTLINE may not move** (`decimate`). The boundary is the wall standoff: any boundary motion -- even sliding one boundary vertex onto another, which cuts the corner between them -- pushes mesh through walls. So only an INTERIOR vertex may be collapsed, and it collapses INTO its neighbour. Two outline vertices may collapse only along an OUTLINE edge: if the edge between them is interior they sit on opposite sides of a thin neck, and fusing them pinches the sheet at a point so the far side comes off as a vertex-attached scrap (**BarrenCave: [1768, 6, 5, 3]**).

<a id="sawtooth-and-concave-allowances"></a>**Sawtooth teeth are cut inward; concave corners get a fraction of the standoff** (`decimate`). A boundary vertex that juts OUTWARD from its neighbours' chord (convex) may be removed with a larger deviation (`DECIMATE_SAWTOOTH_DEV`), because cutting it can only SHRINK the mesh, and the union outline's zigzag teeth are exactly such vertices. Cuts are inward-only, bounded in total by `DECIMATE_MAX_AREA_LOSS` of the mesh's area, so the periphery is straightened and never eaten. CONCAVE corners get a small allowance too: the outline is a wall STANDOFF, not the wall -- a ribbon is laid at `RIBBON_HALF_WIDTH` from its centerline, so the boundary already sits clear of real collision (**measured 11-17u at the Pinarus corner**). Refusing them is what left sliver fans nothing could repair: such a corner cannot be collapsed, cannot be flipped (the quad around a concave corner is non-convex) and cannot be split (its longest edge is under the split floor), and **a census of five reference cells found 16-32 of them EACH, carrying triangles up to badness 68**. The allowance (`CONCAVE_CUT_FRAC`) is a deliberate fraction of the standoff, so it can never cross the wall the standoff buys.

<a id="concave-notches-straightened-against-collision"></a>**A concave notch is cut straight when the chord across it is clear of walls and over floor** (`decimate`, `_outline_move_ok`, `corridor._outline_ground_ok`). Every rail stops at a wall with a standoff that depends on the angle between its march and the wall (the slab's 20u tangent half-width touches an oblique wall up to 14u before the rail does), so where strips meeting a wall at different angles union, the outline is a chain of 10-20u jags -- ImperialDungeon03's chamfered room corner came out as **four steps (2439,10061)-(2465,10032)-(2467,10014)-(2484,10012)-(2505,9992), the concave one 12u inside the chord**, and the hand correction drew one diagonal. The fixed concave allowance (`CONCAVE_CUT_FRAC` of the standoff) cannot take that vertex, because without collision in hand a deeper cut might cross a real pillar corner. With the cell's blocking and walkable soups the question is answered directly: the box between the chord and the notch vertex is probed for walls in the actor band and the chord is sampled for walkable floor within a step, and only then may the notch go, up to `DECIMATE_SAWTOOTH_DEV`. Tools that call `decimate` without collision keep the fixed allowance.

<a id="a-pin-protects-a-position"></a>**A pin protects a POSITION, so the far end may still collapse INTO it** (`decimate`). Door threshold corners are pinned because collapsing them destroys the Door Triangle and the doorway goes dead in the engine. But nothing about a pin requires its NEIGHBOUR to stay: refusing the whole edge froze the merge, since **v464 sits 7.5u from pathgrid node n124, so the 24u node pin vetoed collapsing the redundant v598 into it and two 300u^2 slivers survived on the stairs**. Only both-pinned is a genuine stalemate -- and even then the two may fuse when REDUNDANT, a vertex within `DECIMATE_OUTLINE_TOL` of the straight line between its two boundary neighbours contributing no position of its own. A single door's 24u center pin covers ~50u of boundary, so at **ImperialDungeon01's prison door FOUR consecutive vertices of one near-collinear chain were all pinned and could never collapse into each other**, forcing the fan of 8-360u^2 slivers through the doorway reported as "completely mangled".

<a id="link-condition-guards-the-bowtie"></a>**The link condition guards against a surface closing on itself at a point** (`decimate`). A collapse is edge-topology safe only when the two vertices' neighbourhoods meet EXACTLY at the opposite corners of the triangles being collapsed; any other shared vertex normally means the collapse pinches the surface into a bowtie joined at a single vertex, which edge adjacency then reads as TWO components (**BarrenCave: decimation took one connected cave to [1771, 7]**). The pinch needs the collapsed edge to be INTERIOR: with mesh on both sides, fusing its ends joins two separate fans at a single vertex. On a BOUNDARY edge (one owner) there is no second fan -- one side is open space -- so the collapse merely shortens the outline and cannot bowtie, whatever else the two vertices share. Measured on **ImperialDungeon01's stairs, v600/v601 sit 2.5u apart on the OUTLINE joined by a single 71u^2 sliver, each reaching the V apex v382 through its own 300u^2 sliver**; the strict test saw v382 as an extra shared vertex and refused, so the slivers survived every pass, neither dropped (they carry adjacency) nor merged. Allowing it yields the two fat triangles the author sketched: (394,382,465) and (382,514,464).

<a id="shape-bound-is-not-strict-improvement"></a>**The shape bound is "no worse than the worst already present", not "strictly better"** (`decimate`). A collapse may not push any affected triangle past the shape contract (badness 1.0) nor past the worst shape already there. Strict improvement was tried and STALLED: a sawtooth cut often worsens one neighbour a little before the next collapse fixes it, so the outline never cleaned.

<a id="decimate-keeps-a-vertex-incidence-map"></a>**The collapse loop maintains a vertex->triangle incidence map** (`decimate`). The previous form scanned and REBUILT the whole triangle list per committed collapse -- O(T) twice per candidate, quadratic per cell -- **which alone pushed a large cell's decimation into minutes**.

<a id="split-at-the-apex-projection"></a>**A needle splits at its APEX'S PROJECTION, not at the midpoint** (`_split_needles`). A cap's badness lives at its apex, so bisecting the long edge at the midpoint just leaves two smaller caps. The projection is clamped so both halves stay above `DECIMATE_MIN_EDGE`. A non-manifold edge (more than two owners) is left alone.  Its Z was the CHORD's, which only sits on the ground while the edge stays on one surface: ImperialDungeon01's ramp foot bisected the -590.3 to -632.1 edge and put the new vertex at -614.6, **17.6u above the -632.1 floor under that point**, so the floor rim and the ramp rim came out as two parallel boundaries over the same ground -- 17.6 sq.u. across a 152u span, a mean width of 0.12u, welded by hand in `ImperialDungeon01.json`.  The split now snaps onto walkable collision within `MAX_CLIMB`, sampling near the height of the endpoint it is closest to -- nearest-to-the-chord picks the ramp layer 14u up instead of the floor.  The vertex lands ON a border edge, which the sheet across that edge does not share, so `finalize` re-runs the T-junction zipper after decimation (`_repair_junctions_after_cleanup`); upstream it ran before this vertex existed.

<a id="flips-remesh-a-convex-quad"></a>**A flip re-meshes the same ground with the same four vertices** (`_flip_pass`). For an interior edge (a, b) shared by exactly two triangles (a,b,c) and (b,a,d), when the plan quad c-a-d-b is strictly convex, replacing the diagonal (a,b) with (c,d) changes no coverage. Four guards: the new diagonal must not ALREADY be an edge elsewhere in the mesh -- possible where storeys fold and reuse vertices -- since flipping onto it gives that edge 3+ owners and `_make_manifold` later rips the extras out with no connectivity guard, **measured as whole regions detaching in ChorrolFightersGuild and BarrenCave**; it may not climb more than the old diagonal plus a step, because flipping across a fold bridges two walkable levels; triangles whose three corners are all door-pinned are never touched, the Door Triangle's shape being a contract; and the flip must strictly reduce the pair's worst edge ratio. Strict convexity means a and b sit on OPPOSITE sides of c-d, each a non-degenerate distance off it -- on a non-convex quad they land on the same side and the flip folds the quad over itself. Winding follows from the side.

<a id="flips-and-splits-finish-what-collapse-cannot"></a>**Flips and long-edge bisection finish what collapse cannot reach** (`decimate`). Collapses cannot fix a fan of long slivers whose edges are all above the collapse threshold -- the classic boundary-driven CDT artefact. Flipping the shared diagonal of a convex pair moves NO vertex, so outline and coverage are untouched by construction, and a flip is taken only when it strictly improves the pair's worst edge ratio. A needle whose edges are all LONG can be fixed by neither collapse (nothing short) nor flip (the neighbour may be fine already), so its longest edge is bisected instead: the split moves no vertex and changes no coverage, only adding a vertex ON the edge, and the next round's flips and collapses reshape the halves.

<a id="seam-vertices-only-collapse-collinearly"></a>**A vertex on the exterior cell seam only ever collapses collinearly** (`decimate`, `seam_bounds`). Boundary vertices on the cell rectangle are the cross-cell seam, so the line `build_edge_links` stitches against cannot be cut inward.

<a id="boundary-cuts-are-charged-to-a-budget"></a>**A boundary cut's removed ground is charged against the area budget** (`decimate`). The area of the triangles that vanish (they contained both ends) minus what the survivors regain is the ground the outline gave up; interior collapses net to ~zero and charge nothing.

## Boundary notch fill and level lookup (`corridor_union.py`)
<a id="notch-fill-and-levels"></a>

**Code:** `tes5_import/navmesh/corridor_union.py`

<a id="notch-is-not-a-t-junction-or-a-hole"></a>**A boundary notch is neither a T-junction nor a coverage hole** (`_fill_boundary_notches`). Where two sheets meet at an angle -- a stair mouth meeting its floor -- the boundaries can stop short of each other and leave a sliver-shaped bite in the surface, apex inward. No vertex lies on either edge, so the zipper cannot see it; the sheet BELOW still covers the plan area, so the pathgrid-coverage test passes. What it does is break adjacency across the mouth, exactly where the corridor is narrowest -- the author's report was **"a missing sliver near the bottom of the staircase that chokes the width by half"**, measured on **ImperialDungeon01 at the tower stair bottom, vertices (-64.9,134.8,31.3)/(-66.5,149.9,31.3) against (-113.1,211.8,98.5), a 4-edge open V straddling the walked line n124->n125**.

<a id="notch-fill-has-four-gates"></a>**A notch is filled only when four things hold** (`_fill_boundary_notches`). It must be TWO boundary edges sharing an apex vertex with their far ends close enough to bridge (`NOTCH_MAX_MOUTH` 160u -- a wider mouth is a real room corner, not a bite); DEEP relative to that mouth (`NOTCH_MIN_DEPTH_RATIO` 1.2, as `side >= mouth * ratio` -- the first version had the direction backwards as `mouth > side * ratio` and so **never fired on the very notches it was written for, the stair V-cracks whose mouth is 15u against 65u sides**); near a walked pathgrid line (`NOTCH_NEAR_LINE` 192u, since **64 was under half a ribbon width so a notch bitten out of the SIDE of a corridor -- exactly where they appear -- fell outside it and was never filled**; 192 covers the full ribbon plus its grow margin), because the pathgrid is what makes the bite a defect rather than authored geometry; and fillable without giving any edge a third owner.

<a id="notch-fill-must-not-stack"></a>**A notch is never filled over ground something already covers** (`_fill_boundary_notches`, `_overlaps_existing`). The notch's plan area is usually still covered by the sheet BELOW -- which is why the coverage test never saw a hole -- so a blind fill stacks a second surface on it and the engine picks one arbitrarily: **ImperialDungeon01's same-surface overlaps went 2 -> 7**. The candidate is tested against an STRtree of the existing triangles, skipping any that share a vertex with it or lie more than a storey away in Z.

<a id="point-attached-triangles-are-dropped"></a>**A triangle attached to the mesh at a single VERTEX is dropped** (`_drop_point_attached`). One 2D triangle of the union is emitted once per SURFACE its corners' levels suggest, so where a corridor and a nearby quad at a different height both cover a point, a second copy appears at the other height -- and because none of its edges is shared with anything at that height it hangs off the mesh by a corner. That is the rogue triangle climbing a staircase toward a door.

<a id="levels-are-batched"></a>**Level lookup is batched over all of a part's vertices at once, natively** (`_levels_batch`). Per point `_levels_at` scans every strip (**~1,900 in a dense cell**), and a grown strip's admission test is a point-in-polygon plus a min-distance over its whole outline. Measured on **Wendir02 that was 29.3s of a 31.9s build -- 4.5ms per call over 6,491 calls** -- the single hottest thing left after the width-grow went native. The strips are flattened ONCE per union rather than per point, and the native side buckets them by XY bounds so each point tests only strips that could actually cover it.

<a id="point-attached-cannot-be-walked-onto"></a>**A triangle sharing no full EDGE cannot be walked onto** (`_drop_point_attached`). NVNM adjacency links only across shared edges (`pgrd_to_navm._compute_adjacency`), so such a triangle is never useful mesh and dropping it removes the artefact without touching anything reachable. Iterated, because removing one can leave its neighbour edge-isolated in turn.

## Surface emission (`corridor_union._emit_surfaces`)
<a id="surface-emission"></a>

**Code:** `tes5_import/navmesh/corridor_union.py`

<a id="height-is-a-property-of-point-and-surface"></a>**A point's height is a property of THE POINT AND ITS SURFACE, never of whichever triangle reached it first** (`_emit_surfaces`). The old code chose a triangle's height as the MEAN of its three corners' levels, then bound each corner to whatever vertex already sat within `SAME_SURFACE_Z` of that mean -- so a corner's height depended on WHICH TRIANGLE ASKED FIRST, and two triangles sharing a corner on ONE surface routinely bound it to two different vertices (**corner 22, a single level at 395.3, minted vertex 370 at z=395.3 for one neighbour and vertex 413 at z=356.2 for the next**). They then share no EDGE and the engine cannot walk between them, since `_compute_adjacency` links only across shared edges. On a STAIR every consecutive triangle has a different mean, so stairs tore worst: measured on **ICPrisonSewerExit01, 28 of 582 shared 2D edges were lost and the mesh fell into 12 components; ICPrisonEntrance01 fell into 28**. No value of `SAME_SURFACE_Z` fixes it -- widening fuses real storeys, narrowing tears more. It is a first-match-wins race, not a tolerance. The fix gives each (corner, surface) pair one height: the level at that corner nearest its own surface. Because the level came from the ribbon's own centerline (`union_geom._height_on` follows the pathgrid line A->B), the lifted surface is PARALLEL TO THE SEED LINE by construction and a stair comes out as one straight ramp rather than a sawtooth of per-triangle averages. Coverage is untouched: every 2D triangle is still emitted on every surface beneath it.

<a id="levels-recluster-on-the-storey-gap"></a>**Corner levels are RE-CLUSTERED on the storey gap before emission** (`_emit_surfaces`, `storeys_of`). `_levels_at` clusters a corner's covering ribbons on `SAME_SURFACE_Z` (36u), so a staircase arrives already split into a level per tread-ish step -- **corner 162 came back as [-302.3, -254.7], two entries 47u apart that are ONE flight** -- and emission then treated each as its own surface and stacked a second triangle on the stair. Re-clustering makes "surface" mean the same thing to the level lookup and to the emission: a stair is one surface, and only a genuine floor-above is a second.

<a id="every-corner-proposes-its-own-storeys"></a>**Every corner proposes its own storeys, and a surface is kept only where all three reach it** (`_emit_surfaces`). Pooling the three corners' levels and clustering the pool merges storeys TRANSITIVELY: a corner standing on a stair carries heights between the two floors, chaining the -302 floor to the +127 floor into one band whose mean, -89, is in MID-AIR -- which put **225 of 609 ChorrolFightersGuild triangles up to 213u from any walkable collision**, sheets hanging between the storeys. Proposals must also be REAL band endpoints, never band midpoints, since a band spanning a flight has its midpoint in mid-air. A corner with no ground of its own abstains and takes the surface height through the vertex fallback, so the triangle is still emitted; using only corner `a`'s storeys instead silently loses a surface the other two share and **splits the floor laterally (same-storey components 47-115u apart in Chorrol)**.

<a id="reach-tolerance-is-one-step"></a>**A corner's band is widened by ONE STEP, never by a storey** (`_emit_surfaces._reaches`). A band is an interval [lo, hi]; on a stair it spans the whole rise, so a plain point test is right -- the corner genuinely has ground everywhere between. Widening by `STOREY_GAP_Z` (120u) instead let a corner vote for a surface it has no ground on at all, producing the flap that made **Pinarus's only floor-to-floor link unnavigable**: 2D triangle (-242.7,132.5)/(-316.9,134.9)/(-317.5,173.3) with corner 1 on band [30.0, 30.0] (stair ribbon) and corners 2,3 on [68.6, 68.6] (landing) -- with a 120u tolerance BOTH heights passed for every corner, so the one triangle was emitted twice, at 30.0 tilted 27 degrees and at 68.6 flat, **with identical 1425u^2 plan footprints 38.6u apart, sharing edge (126,127)**. That shared edge was the ONLY connection between the two floors, and an actor crossing it would step onto a surface directly beneath the one it stands on. An actor can step up or down `MAX_CLIMB` onto an adjoining surface, so one step is the right tolerance.

<a id="straddling-triangles-are-not-emitted"></a>**A triangle whose corners share no storey is NOT emitted** (`_emit_surfaces`). Such a triangle straddles a stairwell: measured in Chorrol, corners with levels [-45], [-302] and [-302,-45] -- **two of them on floors 257u apart with no ground in between**. Forcing it onto one storey by majority vote drags the odd corner down through the stairwell and produces exactly the near-vertical sheets that render as "triangles between floors". That is a WALL, not walkable ground. It costs no real coverage: the ground is still covered by the upper floor's triangles at -45 and the lower floor's at -302, and only the impossible bridge between them is gone. The stair proper is a ribbon whose own levels are continuous, so its corners DO share a band and it is emitted normally.

<a id="vertex-key-is-corner-and-slot"></a>**The vertex key is (corner, slot) DIRECTLY -- no union-find** (`_emit_surfaces`). `slot_of` already gives a corner a stable identity per walkable surface: it indexes that corner's OWN clustered levels, which do not depend on which triangle is asking, so two triangles meeting at a corner on one surface compute the same slot and therefore the same vertex. An earlier attempt union-found the (corner, slot) pairs and keyed the vertex on the class root; that was wrong twice over, because the class merges DIFFERENT corners so the root is not a per-corner identity, and keying on it produced a vertex per triangle -- **355 of 609 triangles came out as isolated singletons**. The slot is keyed on the storey BAND rather than the individual level, because two triangles stepping along a stair ask with slightly different z and must land on the same band or they mint different vertices and the stair tears.

<a id="level-less-corners-cluster-their-own-surfaces"></a>**A corner with NO level of its own clusters the surfaces that reach it** (`_emit_surfaces`, `bare_clusters`). Such a corner still has to be distinguished per storey, or every one in the cell collapses into a single class and the mesh flattens. It CANNOT be keyed by quantising z into fixed bands: band edges are arbitrary, so two neighbours a unit apart in Z straddle one and land in different classes -- that **shattered ChorrolFightersGuild into 83 components and lost two thirds of its triangles**. Instead each level-less corner accumulates the surface heights that actually reach it and clusters them on `STOREY_GAP_Z` exactly as a corner's own levels are, giving a stable, band-free slot that separates storeys only where a real gap exists.

<a id="vertex-height-is-the-band-median"></a>**A vertex takes the MEDIAN of its band, never a per-triangle average** (`_emit_surfaces.vert`). A band holds the heights of every ribbon covering this exact point on this storey, each computed by `_height_on` along that ribbon's centerline, so on a stair they agree to within the ribbons' own crossing error and their median is the point's height ON the pathgrid line. The height depends ONLY on the key -- were it to depend on which triangle asked, the first caller would win and the original order-dependence would come straight back. A fallback is used only when the corner carries no level at all: the union covers it but no centerline claims it.

<a id="emission-drops-duplicates-and-slivers"></a>**Emission de-duplicates windings and drops zero-area triangles** (`_emit_surfaces`). A 2D triangle is emitted once per surface beneath it, and two of a corner's storey bands can resolve to the SAME vertices, so the same triangle is emitted twice (once per winding) or several times over. Measured on **Pinarus: (167,178,152) and its reverse formed a 2-triangle "component", and a collinear sliver (57,58,59) was emitted FOUR times as four 1-triangle "components"** -- duplicates and degenerates, not islands, and what made a house whose corridor mesh is ONE component report seven. Identity is therefore winding-independent (the sorted vertex triple), and triangles under `MIN_XY_FOOTPRINT` are dropped: they cover no ground, cannot be stood on, and only ever attach to the mesh at a point.

<a id="band-reps-are-precomputed"></a>**The per-corner band endpoints are precomputed once** (`_emit_surfaces`, `reps_all`). The per-triangle closure recomputed these min/max pairs millions of times and was **~40% of a large cell's whole build**.

## The union mesh driver (`corridor_union.build_union_mesh`)
<a id="union-mesh-driver"></a>

**Code:** `tes5_import/navmesh/corridor_union.py`

<a id="union-has-no-storey-buckets"></a>**There are no storey BUCKETS -- ribbons are grouped by CONNECTIVITY** (`build_union_mesh`). A staircase has no single height, so any attempt to assign corridors to floors forces one Z threshold to be both loose enough for a stair's slope and tight enough for a 200u floor gap, which no value satisfies. A single flattened union instead merges floors that sit on top of each other in plan and the triangulation then bridges them: measured in **ChorrolFightersGuild, 15 triangles had corners on the -302 floor AND the -45 floor at once, 3-46u from a walked pathgrid line**. Emitting them stacks a near-vertical sheet between the storeys ("triangles between floors"); dropping them severs 24 shared edges and splits the floor into 7 pieces. Neither is right, because the flattened polygon was never the correct region to triangulate. So ribbons are grouped into storeys FIRST (`_storey_groups`, walking ribbon to ribbon) and each storey is unioned and triangulated on its own, leaving exactly one surface per sheet and an unambiguous height at every corner.

<a id="junction-union-is-exclusive"></a>**A cross-sheet junction is unioned into exactly ONE sheet, never both** (`build_union_mesh`). Corridors meeting at a pathgrid node must come out as one merged surface. Where the sheet split separated them -- a staircase genuinely conflicts in plan with the floor it passes UNDER, so no scoring keeps it with the landing it arrives at -- the junction is unioned explicitly. Keeping it in both sheets is not a union at all: each triangulates that ground independently and the result is stacked, overlapping triangles (**Pinarus: 16 pairs of same-surface triangles overlapping by 5,582u^2**; before the exclusive subtraction, **Chorrol 135 pairs / 90,947u^2 and Pinarus 20 pairs / 3,448u^2**). Each node is OWNED by the first sheet that reaches it; that sheet unions in the far ribbon of every arriving edge, and the far sheet gives that ground up.

<a id="junction-strips-are-clipped"></a>**A donated junction strip is CLIPPED to the junction disc, never handed over whole** (`build_union_mesh`). The strip joins the owning sheet's LEVEL LOOKUP, and levels are answered wherever a strip covers a point, so handing over the full stair strip leaks its heights across everything it passes under. Measured on **Pinarus: the upper-floor sheet (whose polygon spans the whole house) received the stair strip for a 64u junction at its top node, its corners above the stair BOTTOM then answered levels [-199, 69], and the sheet emitted a phantom duplicate of the ground floor** -- stacked, overlapping triangles at the foot of the stairs.

<a id="sheets-claim-ground-exclusively"></a>**Later sheets are clipped against earlier ones where they agree on HEIGHT** (`build_union_mesh`, `claimed`). Two sheets meeting at a shared floor level (**Chorrol's sheet0 spans z -45..143 and sheet1 z -302..-40, meeting around z=-45**) otherwise both mesh that ground: **each sheet alone measured ZERO overlap, while 12 overlapping pairs existed across sheets**. Ground is only surrendered where the two agree on the height (`_same_surface_region`) -- where they disagree they are different storeys stacked in plan and both must keep their own mesh.

<a id="shared-node-points-are-seeded"></a>**A pathgrid node shared by two sheets is forced into both** (`build_union_mesh`, `node_pts`). It is the one place they MUST connect: the top or bottom of a staircase. Measured on **Pinarus: node 1 is the stair top, its stair ribbon (0,1) landed in one sheet and the upper floor's ribbon (1,8) in another, and because each sheet is triangulated independently the two nearest vertices came out 31u apart** -- far beyond the weld radius, so the house stayed in two components with the break exactly at the top of the stairs. Forcing the node's XY into every sheet with a ribbon there makes both place a vertex at the same point and height, so `union_mesh._weld_sheets` fuses them. Seeding alone can only give a shared POINT -- two independently triangulated polygons meeting at one vertex form a fan and share no EDGE, so NVNM adjacency cannot link them (**Pinarus v152 at the stair top was used by both components, still 2 components**); `union_mesh._stitch_shared_nodes` turns the point into edges.

<a id="pathgrid-nodes-are-not-forced-seeds"></a>**Pathgrid nodes are NOT appended as forced triangulation seeds** (`build_union_mesh`). Under the old point-set sampler a True flag forced a vertex at each node so cross-sheet welds could fuse stair tops. The CDT takes vertices only from the polygon rings, so a node seed cannot become a vertex -- the only thing the flag still did was mark every node junction "steep" and trigger 64u refinement around all of them, **exploding a large cell to ~50k triangles that emission then paid for (~85s of a 118s cell)** and decimation collapsed right back down.

<a id="door-claim-is-single-and-gated"></a>**Each door base line is claimed by exactly ONE part, gated three ways** (`build_union_mesh`). A base line belongs to a part when it lies inside it OR ON ITS OUTLINE -- the threshold edge IS part of the union boundary, so a strict interior test rejected it and the constraint never reached the triangulation, leaving the **CharacterGen assassins' 115u cell door as a 571-unit scrap** (every vanilla door triangle is >= 992) after the boundary densify chopped its base into 26.8u + 21.6u pieces. But the tolerant test matches in several sheets that meet at the threshold, so: the claim is consumed (`_door_claimed`) to stop duplicate overlapping door triangles colliding in the weld; the part must actually HOLD the wedge (`DOOR_CLAIM_MIN_FRAC`), because the FIRST sheet iterated used to win even when the wedge sat elsewhere -- measured on **ImperialDungeon01's 99.5u prison gate (0001FC1E), claimed by the 3.49M sheet covering 1.4% of the wedge while the sheet covering 98.6% was never offered it**, shipping a fan of 8-360u^2 needles through the doorway; and a STOREY gate requires the sheet to have a surface at the door's own height, since parts are 2D and **Arvena's upstairs bedroom door was claimed by the sheet that only covers that spot DOWNSTAIRS**, its wedge cut from ground that does not span the doorway at that height, so the triangle came back unattachable and was withdrawn.

<a id="door-apex-inherits-base-levels"></a>**The door apex inherits the levels of its base endpoints** (`_apply_door_apex_levels`). Its triangle is reserved out of the union as a hole, so no corridor covers that point and `_levels_at` returns nothing for it; `_emit_surfaces` then drops any triangle whose corners do not all share a surface, which **silently deleted 4 of every 5 reserved door triangles** -- the protection passes downstream never saw them because they never existed. The apex stands on the same ground as its own door line.

<a id="t-junctions-are-split-three-times"></a>**T-junctions are re-split after every vertex-moving pass** (`build_union_mesh`). The merge and the stitch move and fuse vertices, which can land a vertex in the middle of another triangle's border edge -- a hanging node the first split ran too early to see. An unshared edge reads as point-attached and `_drop_point_attached` then deletes REAL coverage: measured on **ImperialDungeon01, the junction triangle spanning pathgrid nodes 137/138/139 was dropped and the walked line through the prison lost its mesh**, a hole an NPC cannot cross. A third split follows the wall cull, which can itself OPEN a crack: a plan-degenerate triangle reads as a 90-degree wall and is dropped correctly, but where it was the only thing bridging a hanging vertex to its edge, dropping it UN-SPLITS that T-junction. Measured on **ImperialDungeon01's tower staircase: the zero-area triangle (-288.5,183.2)/(-270.8,286.9)/(-279.6,235.0) was the bridge, and losing it left a 105u boundary edge straight across the flight with the pathgrid running through it** -- the mesh looks continuous in plan but an NPC cannot cross ("a missing sliver that chokes the staircase by half").

<a id="merge-runs-before-the-stitch"></a>**The pathgrid-node merge runs BEFORE the stitch** (`build_union_mesh`). The merge makes each junction a shared POINT (one weld per component) and the stitch is the machinery that turns shared points into shared EDGES (fan-open + bridge, with the overlap guards). This is the guarantee that corridors meeting at a pathgrid node are joined EVERY time, driven by the pathgrid rather than by any property of the geometry, so there is no case it can decline to handle.

<a id="probe-only-stops-before-notch-fill"></a>**`probe_only` stops after the last T-split** (`build_union_mesh`). Everything above it can change which corridor edge is nearest a door -- the wall cull in particular removes edges a door must never bridge across, and dropping it **moved a door in ImperialDungeon01**. What remains below only ADDS ground (notch fill) or removes triangles hanging off a point, neither of which is a bridge candidate the door search would pick, since candidates are filtered to edges within `DOOR_BRIDGE_RADIUS` on the door's own storey reached without crossing a wall. The probe's mesh is discarded either way; the second pass rebuilds the union with the door quads unioned in.

<a id="weld-only-across-emissions"></a>**The weld may only fuse vertices from DIFFERENT emissions** (`build_union_mesh`, `vert_src`). Within one part the CDT already connects everything, so a same-part weld can only move a vertex sideways -- measured at **Pinarus's stair bottom, where the steep refinement put a stair-copy vertex and a floor vertex 15.8u apart in 3D and the weld dragged one onto the other**, sweeping a triangle edge across a neighbour it shared no vertex with (overlapping triangles).

<a id="union-inputs-and-clipping"></a>**The driver's three optional inputs.** `extra_strips` are door FOOTPRINT strips (`corridor_doors.door_footprints` via `_poly_strip`) that join the union as ordinary ground -- the flat connection quad from each door base to the nearest corridor edge -- contributing both their polygon and their flat height, so a vertex standing on door-only ground still knows how high it is; their coverage is preserved exactly and the union resolves any overlap. `door_edges` are the door BASE lines, each forced to appear as a triangle edge so every door gets one large triangle with its long side on the door line. `cell_bounds` CLIPS the unioned coverage to the cell rectangle on exterior cells, so a cross-seam ribbon (built from a PGRI InterCell link reaching into the neighbour) stops exactly on the boundary plane, leaving a border edge for `build_edge_links` to stitch while each mesh stays strictly within its own cell. The ribbon-polygon memo is bound to one build (`_ribbon_cache_clear`), since a worker converts thousands of cells in a row and the cache pins a Polygon per entry.

## Navmesh build entry point (`build.py`)
<a id="navmesh-build-entry-point"></a>

**Code:** `tes5_import/navmesh/build.py`

<a id="teleport-doors-are-barriers-and-anchors"></a>**Teleport-door positions are both barriers and island-pruning ANCHORS** (`teleport_door_positions`). A teleport door leads to ANOTHER cell, so the navmesh must end at its threshold exactly as vanilla navmeshes do. These positions become barriers for the pathgrid-reach flood (`region.keep_pathgrid_heights`): without them an interior cell's mesh escapes through the open doorway and spreads over the decorative street/porch geometry outside the shell. They also anchor island pruning -- the doorstep component in front of each door is how an NPC enters the cell, so it is always kept. This is the FALLBACK list, used only when the caller cannot supply one (tools with no DOOR base-record set); interior-only doors are missed then, and a bare-XTEL fallback door carries width 0 so `corridor_doors` uses its constant half-width. `float()` happily parses NaN and 8.9e17, so the coordinates are range-checked against `world._MAX_PLACEMENT` as well as tested for finiteness.

<a id="ledges-are-returned-out-of-band"></a>**Drop-down ledge pairs are returned OUT-OF-BAND** (`build_navmesh`, `ledges_out`). The long-standing `(verts, tris)` return stays intact for the many callers that only want geometry; `pgrd_to_navm` reads the ledge list to write the edge links. `budget` is accepted for signature compatibility -- the corridor build has no per-cell time risk -- and ignored. `door_bases` holds low-24 DOOR base FormIDs whose panel collision is excluded; when None, door refs are found via XTEL plus the `doors` list positions.

## Corridor width growth (`corridor_grow.py`)
<a id="corridor-width-growth"></a>

**Code:** `tes5_import/navmesh/corridor_grow.py`

<a id="grow-is-batched-into-one-native-call"></a>**The march is batched into ONE native call per cell** (`grow_batch`). It is **~890k wall-slab probes for a single dense interior cell (Wendir02, 938 edges)**, each testing ~140 candidate triangles; at ~170us per probe in Python that is **~150s for one cell** -- the dominant cost of the whole navmesh pipeline. Crossing the Python/C boundary once per CELL instead of once per probe is what makes it tractable; a native `wall_hit` alone would still pay 890k crossings. The tunables are passed per call rather than compiled in (`_native_params`), so the C++ and the Python cannot drift apart when a constant is retuned.

<a id="trigrid-queries-nine-buckets"></a>**The triangle index queries a 3x3 bucket neighbourhood** (`_TriGrid.candidates`). Single-bucket lookups miss a triangle whenever the query point sits near a bucket boundary -- for a wall test that means growth walks straight THROUGH the wall. Querying the point's bucket and its eight neighbours means a triangle within one bucket (>= the probe extent) is never missed.

<a id="wall-probe-sweeps-the-interval"></a>**The wall probe tests the SWEPT interval, never the end point** (`grow_half_width`). A point probe with a thin slab steps straight over a wall whenever the wall falls between two samples -- measured, **a 2u-deep slab on an 8u step missed a wall by 2u on both sides and produced 124 through-wall triangles in the Fighters Guild**. Centering the slab on the interval midpoint with half the interval as depth (plus the slab's own sliver) makes the sweep continuous, so a wall cannot be skipped. Callers marching in steps therefore pass HALF THE STEP as `depth` and center on the step's midpoint (`wall_slab_sampler`). On a hit the position is BISECTED (`RIBBON_GROW_BISECT`) so the ribbon ends AT the wall rather than up to a whole step short of it -- a step-short stop is what narrowed doorways.

<a id="soft-floor-never-beats-a-wall"></a>**A WALL always overrides the caller's soft floor** (`grow_half_width`, `lo`). `lo` keeps junctions overlapping, but forcing the ribbon out to a connectivity floor drove mesh straight through walls near every junction -- the same defect the Phase-1 unconditional width has. So the soft floor is marched too, from zero, and the wall test may cut it short. The walkable-floor test binds only BEYOND `lo`: inside it the pathgrid's own assertion wins (a node at a threshold or a ledge lip would otherwise collapse its corridor to nothing), and no wall was found there, so nothing can be on the far side of anything.

<a id="only-parallel-edges-cap-a-width"></a>**There is no parallel-edge cap** -- see [neighbour-cap-removed](#neighbour-cap-removed).

<a id="node-discs-fill-junction-notches"></a>**Pathgrid NODES grow radial discs to fill the corner notches** (`corridor._disc_march_rows`, `_disc_strip`). Ribbons grow only PERPENDICULAR to their own edge, so where two edges meet at an angle the outer corner is a notch no ribbon reaches -- a right-angle junction leaves a square bite out of the mesh. Marching outward on `RIBBON_GROW_DISC_RAYS` evenly-spaced bearings under the same stop rules as a rail, then closing the ray ends into a polygon, fills exactly that corner; it joins the union like any other strip.

<a id="slab-test-is-a-2d-sat"></a>**The slab test is a separating-axis check in the slab's own frame** (`_tri_hits_slab`). The slab is an oriented box centered at (cx, cy): extent `half_w` along the edge-tangent (~actor width), `depth` along the march direction (thin), full Z span. The triangle's vertices are projected into that frame (tangent = X', march = Y') and tested against the axis-aligned rectangle, gated first by a cheap Z overlap.

## Door footprints (`corridor_doors.py`)
<a id="door-center-caches"></a>**The door-center caches and why the panel center wins** (`from_pgrd`, `load_door_centroids`). A door REFR's position is the model PIVOT, which sits on the HINGE, not on the opening; the point an actor walks through — and where the Door Triangle belongs — is the panel midpoint. Two caches hold it, both built in the SAME pass as collision and mesh bounds (`asset_convert.collision.collision_extract.scan_mesh_data`), keyed by normalized model path:

- `_DOOR_PANEL_CTR` — local-space `(cx, cy)` of the COLLISION PANEL in world units. **Preferred.** Verified against placed doors in-world the collision center is exact to **~1.5u**, where the mesh-bbox center was **25-35u off along the threshold on the CharacterGen prison gates (`cgprisoncellgate01`, `idgate01`)** — more than half those gates' own 40/63u width, putting the Door Triangle mostly on the jamb.
- `_DOOR_CENTROIDS` — local-space `(cx, cy)` XY midpoint of the largest mesh shape. The fallback when no collision panel was read.
- `_DOOR_FLOOR_DZ` — local z of the door mesh's BASE (bbox z-min). Added to the REFR `PosZ` it gives the threshold's floor height, dropping the point from the hinge (up the door leaf) to the storey the door actually opens onto.
- `_DOOR_THRESH_LOCAL_Y` — True when the threshold runs along the mesh's LOCAL +Y (the wider horizontal extent), False when along local +X. Read per model; door meshes do not share one convention (**`impdundoor01` is wide in Y, `icdoorint01` wide in X**), so `collect_doors` adds a quarter turn for the X-wide meshes and every consumer — the door quad and `navmesh_preview` alike — then uses one rule.
- `_DOOR_NO_THRESHOLD` — models whose collision panel is thin in Z: trapdoors, hatches, display cases. They swing about a HORIZONTAL axis, so no vertical-axis threshold line exists and they get no door quad (width 0). They are still EMITTED: `_build_door_links` must give every door a Door Triangle or the doorway goes dead in the engine. Dropping them outright deleted the **Imperial Prison cell gates, the player's own starting cell door among them**, because a shape the extractor could not read (`bhkListShape`) is indistinguishable in the cache from a real trapdoor.
- `_DOOR_WIDTH` — the real doorway width off the collision panel; see [the base-line section](#door-base-spans-the-real-doorway).

<a id="door-center-uses-transpose-rotation"></a>**The pivot->panel offset rotates by the TRANSPOSE matrix** (`_door_threshold`). Bethesda placement applies the transpose of the naive rotation (`navmesh/world.py::_rot_matrix`, verified against the AnvilFG floor shell), so the world offset is `(lx·cos + ly·sin, -lx·sin + ly·cos)`. The naive CCW form put **Arvena's upstairs door center one FULL door width from the real doorway**. Only doors rotated 90/270 expose it — 0/180 are sign-invariant — which is why it survived every 0/180 test.

<a id="door-triangle-linking-distance"></a>**A triangle links to a door within `DOOR_LINK_MAX_DIST`, weighted toward the threshold LINE** (`from_pgrd`). Triangles centered on the threshold line (a small offset along the facing) are preferred by weighting the along-facing offset up by `DOOR_LINK_ALONG_WEIGHT`.

<a id="door-footprints"></a>

**Code:** `tes5_import/navmesh/corridor_doors.py`

<a id="door-base-line-is-local-y"></a>**A door's base line is its LOCAL +Y, and the facing is local +X** (`door_footprints`). A door mesh's local +X points THROUGH the opening and local +Y runs ALONG the threshold -- measured on **impdundoor01.nif, whose panel is 5.6u thick in X and 115.3u wide in Y**: a panel is thin through the doorway and wide across it. `_door_threshold` agrees, rotating the hinge->doorway-center offset (which lies along local X) by the same standard matrix. Using the facing as the base line laid the threshold across the axis the door actually opens along -- **every door quad rotated 90 degrees from its real opening**, visible as a sideways door line in `navmesh_preview`. Under the TRANSPOSE placement convention (`world._rot_matrix`) the threshold is `(sin rz, cos rz)` and the facing `(cos rz, -sin rz)`; the old CCW forms drew and swept doors mirrored for any rotation off 0/180.

<a id="door-base-spans-the-real-doorway"></a>**The base line spans the REAL doorway width, never a constant** (`door_footprints`). Door panels run from **16u to 764u wide (median 121)**, measured off each model's collision panel, so the old constant 90u base line was the wrong size for most doors: on impdundoor01 (115u) it left the first 30u of the threshold with no mesh under it and the **Door Triangle came out a 571-unit scrap -- below the smallest of 1,659 vanilla door triangles (min 992, median 9,614)**, too narrow for an actor to stand on. That is what stopped the **CharacterGen assassins dead at their cell door**.

<a id="door-candidate-edge-gating"></a>**Candidate corridor edges are ranked by distance and gated three ways** (`door_footprints`). A blocked candidate is SKIPPED and the search continues outward, never abandoned -- checking only candidates nearer than the current best let a near-but-blocked edge shadow a slightly farther clear one, and the door then produced no footprint at all. The height gate restricts candidates to this door's storey: without it a door bridges to whatever ribbon is nearest in plan, which **in Pinarus's house meant reaching up the staircase and laying a triangle across the floor below it**. The frontal-strip gate keeps only edges within the doorway's span across the facing plus a ribbon width, because the quad sweeps the base line along the facing and can reach nothing else -- accepting a candidate displaced mostly ALONG the threshold axis **laid a floating 5-triangle patch beside ImperialDungeon01's tower door, whose only corridor runs 283u to the door's SIDE**.

<a id="door-side-comes-from-the-pathgrid"></a>**Which side the door serves is decided by the nearest pathgrid NODE** (`door_footprints`). The pathgrid is the only input that asserts "an actor walks here". Derived ribbon edges run past BOTH faces of most doorways, so nearest-edge, majority and distance-weighted votes all disagreed with the pathgrid on **~47% of doors (14 of 30); keying on the nearest node cut that to 2**. That side is PRIMARY: it carries the base-line constraint and so the Door Triangle. When it has no clear corridor, whichever side does.

<a id="door-far-side-bridge"></a>**A far-side quad is added only when the two faces are pathgrid-DISCONNECTED** (`_sides_disconnected`). The case is walkable ground on both faces of a doorway with no pathgrid route between them -- **the prison-cell gates, whose interiors were unreachable islands** an actor could never leave. Where the pathgrid IS connected across the door the ribbon already runs through or around the doorway, and an extra quad only adds overlapping ground: measured, it **severed the staircase sheets in Pinarus's and Arvena's houses**. A teleport door's far side is another cell, so it gets the primary side only, and the far-side quad never carries a base constraint -- one Door Triangle per door.

<a id="door-footprint-is-a-rectangle"></a>**The footprint is a RECTANGLE swept along the facing, not the corridor edge's endpoints** (`door_footprints._sweep`). Using the edge's own two endpoints as the far side made the quad's width arbitrary: when they projected close together the quad pinched to a wedge and the door joined the mesh AT A POINT, with a long thin triangle reaching off to whatever the other end was. A rectangle guarantees the base line BL-BR is one FULL edge (the vanilla door triangle's long side) and that the two triangles it splits into share the full diagonal, so the second attaches along an EDGE rather than a corner. It spans EXACTLY the doorway -- widening past the door line pushes the footprint through the wall beside the frame.

<a id="door-quad-depth-floor"></a>**Quad depth is floored and pushed PAST the corridor edge** (`door_footprints._sweep`). The nearest corridor edge usually sits right at the threshold, so the raw projection alone gave depths of **1-20u: a 90x1.3u sliver** that connects to nothing and ships as a rogue scrap. Depth is therefore floored at `DOOR_MIN_DEPTH` and pushed past the edge by `DOOR_OVERLAP`, and must also clear the door triangle's apex so the wedge reserved out of the union always sits on ground the quad itself contributed.

<a id="door-quad-is-a-ramp"></a>**The quad is a RAMP, not a shelf** (`door_footprints._sweep`, `z_far`). Its depth is driven by the apex, so a wide door sweeps deep and over a staircase the far edge stands on ground well below the threshold. A flat quad at `s_z` then hangs 30-40u above the real treads, the level lookup answers BOTH heights, and emission bridges them with a near-vertical triangle -- measured on **ImperialDungeon01's 139.5u-wide prison gate at the stair head: quad at 513.8 over stair mesh at 474**. The corridor mesh knows the real height under the far edge, so it is carried and the strip slopes to meet it.

<a id="door-apex-is-analytic"></a>**The door triangle's apex is fixed analytically, never searched** (`door_footprints`). Base = the full doorway, apex on the perpendicular bisector at a depth that is a pure function of the width, on the side the pathgrid serves. The old apex search (`corridor_union._door_apex`) tried BOTH normals and a ladder of shrinking depths until a candidate fit inside the walkable polygon, so a cramped near side flipped the whole triangle to the FAR side of the door -- **three doors in ImperialDungeon01 had their reserved triangle on the opposite side from the pathgrid** -- and the area varied build to build with the surrounding geometry.

<a id="door-own-collision-is-skipped"></a>**The blocking walk skips the door's OWN collision** (`_blocked_between`). A door REFR is a placed mesh standing exactly on the threshold, so a walk beginning at the door position hits the door panel on its first step and every candidate looks blocked -- which left doors with wide open floor in front of them unconnected. The walk starts `DOOR_SELF_CLEARANCE` away and stops the same distance short of the corridor edge, so only genuine geometry BETWEEN them can reject the bridge. It steps in `RIBBON_GROW_STEP` with the same thin actor slab the width-grow uses, starting just above the door's own floor so the threshold lip and the floor are not read as a wall.

<a id="door-mesh-height-probe"></a>**The far-edge height probe buckets the raw corridor mesh** (`door_footprints`). Point-in-triangle over a 128u grid of the unmodified ribbon union, answering the height nearest a given z within `DOOR_QUAD_ZTOL`.

## Cell geometry gathering (`world.py`)
<a id="cell-geometry-gathering"></a>

**Code:** `tes5_import/navmesh/world.py`

<a id="refr-placement-is-the-transpose"></a>**A REFR's placement matrix is the TRANSPOSE of the naive product** (`_rot_matrix`). Oblivion and Skyrim store a REFR's rotation and the engine applies its INVERSE when placing the mesh, so the placement matrix is `(Rz @ Ry @ Rx).T`. Verified on AnvilFightersGuild: the floor shell has RotZ = -90 deg, and only the transpose lands its footprint (x -852..584, y -822..431) under the cell's pathgrid (x -769..511, y -742..357) -- **52/52 nodes inside, vs 34/52 with the non-transposed matrix**, a ~180 deg error that put the room mesh backwards relative to the furniture.

<a id="vhgt-offset-scales-too"></a>**BOTH the VHGT offset and its accumulated deltas scale by `_VHGT_UNIT`** (`decode_vhgt`). The layout is a float offset then a 33x33 grid of SIGNED int8 gradients -- the first column of each row is a delta from the previous row's first column, and within a row each column is a delta from the previous. The old converter did `offset / 8` going in and `* 8` coming out, which cancels for the deltas but silently ANNIHILATES the offset's contribution, so every exterior cell's terrain came out at the wrong absolute height. For **Tamriel (47,6) that put terrain at z=829..3213 while the cell's own pathgrid and REFRs sat at z=18288..19776, a ~16,700u error**; with the offset scaled correctly the terrain lands at 17608..19992, under the objects standing on it.

<a id="placements-are-slope-resplit"></a>**The walkable/blocking split is re-derived from PLACED normals** (`gather_cell_geometry`). Rotating a static can turn a floor triangle into a wall and vice versa, so the cache's local-space classification cannot be trusted once a rotation is applied.

<a id="door-panels-are-never-blocking"></a>**A door panel contributes no BLOCKING collision, but keeps its FLAT faces** (`gather_cell_geometry`, `skip_bases`). A door is a thing an actor OPENS, never a wall: vanilla navmesh runs under every door, and treating the panel as blocking walls off the corridor wherever the panel happens to be parked -- measured on **Pinarus's upstairs ANIMATED door, whose at-rest panel sits 47u from its threshold ACROSS the passage**, pinching the ribbon to nothing and making the doorway unwalkable. The walkable faces stay because a trapdoor or platform door IS the floor the pathgrid walks on -- measured on **ImperialDungeon01 nodes 243-248, whose whole junction stands on a flat door piece**; excluding it wholesale deleted the floor. Gates are authored upright and laid flat by rotation, so the classification comes from the placed slope, and steep door faces are DISCARDED rather than demoted to blocking (a vertical panel's edge sliver would wall the doorway right back up).

<a id="land-split-for-the-grid-rasterizer"></a>**`split_land=True` keeps LAND terrain separate** (`gather_cell_geometry`). Terrain is a regular grid of large triangles, and the generic scalar rasterizer spends most of an exterior cell's build time on it, so the caller sends it down the vectorized grid-rasterizer path instead.

<a id="wild-placements-are-dropped"></a>**A REFR placed beyond `MAX_PLACEMENT` (1e7) contributes no collision** (`_finite_placement`). Such a ref is nowhere near the pathgrid so it could contribute nothing usable, and a non-finite or absurd placement crashed a worker and failed the whole Nehrim import with a bare `BrokenProcessPool`. The ref itself is still converted and written normally by the record path.

Oblivion's worldspaces span roughly ±2e5 units (a 4096-unit cell grid at ±32 blocks), so the 1e7 threshold is ~50x the whole map and cannot be a real placement. **This is not defensive padding.** Nehrim genuinely ships refs whose position the CS never initialised: **17 REFRs across 10 base objects carry PosY = 8.936455989415117e+17** (with PosX = 1.68e-36), e.g. REFR `001E57C4` in cell `001E4FEC`. Placing one stretches the cell's triangle soup to 8.9e17 units wide, which blew the native TriGrid's dense bucket grid to **5.4e14 buckets — a 4-billion-GB allocation** whose `std::bad_alloc` aborted the pool worker.

## LAND Record Structure
<a id="land-record-structure"></a>

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
- …but a NON-persistent cell with no XCLC is **not** a persistent cell: it is a real exterior cell at grid (0,0) whose coords Oblivion omitted. Stamp `XCLC=(0,0)` and leave it in the block tree — moving it out punches a null grid hole. See below.

### <a id="exterior-block-ordering"></a>Exterior block order is unsigned (X, Y) — X MAJOR

**Code:** `navmesh/pool.py::grid_sort_key`, `navmesh/pool.py::ensure_cell_grid`.

A block / sub-block GRUP label is `struct.pack('<hh', Y, X)` — Y in the LOW
word — but vanilla orders the groups by the UNSIGNED 16-bit halves with **X
major, Y minor**. Census of the real Skyrim.esm: all 168 blocks of worldspace
0000003C sort by unsigned (X, Y) and by no other key; the same holds for every
sub-block and for all 37 worldspaces in the file.

Sorting on the label's own word order gives (Y, X) — the TRANSPOSE — and that is
what shipped: TWMP_ValenwoodImproved emitted its Tamriel blocks as (-1,0),
(-2,-3), (-2,-2), (-1,-2), (-2,-1), (-1,-1), where X descends and re-ascends.
The engine walks this list to build the worldspace's cell grid while PARSING the
file, so a non-monotonic run never terminates: the game hung on the main menu
with no crash and no log, xEdit called the file clean, and deleting exterior
blocks in xEdit made it load again — each deletion shortens the list until what
remains happens to be monotonic.

**Never census our own converted output for this:** it carries the same bug,
which is precisely how the transposed key was mistaken for vanilla's.

`ensure_cell_grid` is the companion: Oblivion omits XCLC when a cell sits at
grid (0,0), and the coordinate must be stamped before bucketing so the job
gatherer and the group builders agree on which block a cell belongs to. Writing
the default is faithful, not a patch — every ref in all 30 affected cells floors
to (0,0). Details in the section below.

### 🔴 A worldspace CELL with no XCLC is a real (0,0) cell — STAMP IT (2026-08-10)

Crash `crash-2026-08-09-23-15-19` / `-23-34-53` / `crash-2026-08-10-00-00-48`,
all byte-identical: `EXCEPTION_ACCESS_VIOLATION` at `SkyrimSE.exe+050E6AD`,
`mov rbx, [rax+rcx*8]` with `rax=0`, on a `BSJobs::JobThread`, streaming
`OblivionMQKvatchEntrance` in `Plane of Oblivion`.

**Oblivion omits XCLC when a cell sits at grid (0,0)** — an absent subrecord
already reads as 0, so the CS never wrote one. Skyrim does not tolerate the
omission: it builds its grid-cell array by walking the type-4/5 block tree and
reading each cell's XCLC, so a cell without one never occupies its slot. The
slot stays null while all four neighbours are live, and the streaming tick
indexes it **without a bounds check** — an allocated grid array is an assumed
invariant.

30 cells are affected: `OblivionMQKvatchBridge` (60 refs), `MQ14OblivionGate`
(34), `CheydinhalOblivion` (19), `DABoethiaStatue` (21), every IC district,
MQ16, DreamWorld. **100% of every one of their refs floors to grid (0,0)**
(`floor(pos / 4096)`), so stamping `XCLC=(0,0)` is faithful, not a patch.

Fix: `_ensure_cell_grid()` in `import_main.py` stamps the default on any
non-persistent exterior cell lacking XCLC, before both `convert_CELL` and the
block/sub-block bucketing — and `_gather_navm_jobs` calls it too, or the two
passes disagree about which block a cell belongs to.

**A dead end worth recording: REMOVING these cells from the block tree makes it
worse.** That was the first attempt here — it looked right (vanilla is 0 of
16,942 blocked cells without XCLC) but it *punched* the hole instead of filling
it. The diagnostic that settles it is counting **enclosed grid holes** (a
missing (x,y) whose four neighbours all exist):

| | enclosed holes |
|---|---|
| vanilla Skyrim.esm | 2, at arbitrary coords (WindhelmWorld, KatariahWorld) |
| ours, after removing the cells | 22, **every one at exactly (0,0)** |
| ours, after stamping XCLC | 0 |

Holes are legal in general; a hole at (0,0) is the signature of this bug.
Guarded by `tests/test_import.py::TestGridlessWorldspaceCellPlacement` and
checkable with `tools/validate/cell_grid_check.py --holes`.

### 🔴 The texture PRUNE must speak the importer's paths (2026-08-09)

Cause of "almost all landscape textures are missing" on Nehrim. An Oblivion
LTEX `ICON` is relative to `Textures\Landscape\`, and the importer prepends
`landscape\` (`record_types/world.py:111`). `texture_prune.refs_from_records`
did not: it kept `oblivion/terrainhd…dds` and `tes4/oblivion/terrainhd…dds`
while the plugin asks for `tes4/landscape/oblivion/terrainhd…dds`. Nothing
matched, so **every LTEX texture was pruned as unused and never packed.**

Measured on the shipped build: **252 of 484 referenced LTEX texture slots were
in no BSA, all of them still on disk.** The survivors were exactly the 116 the
MESH manifest happened to name — a texture a mesh also used survived, which is
why *some* terrain was textured and most was not. The LTEX records themselves
were fine (229/242 resolve, 0 dangling LAND layers), which is what makes this
so easy to misdiagnose: every record-level check passes.

`refs_from_records` now carries a per-signature prefix table
(`_RECORD_TEX_PREFIX`), keyed on the export filename since that is the only
place the record type is known. **Any record type whose texture field is
relative to a subfolder has to be listed there**, and it must mirror whatever
the importer prepends. Guarded by `tests/test_texture_prune.py`.

Diagnose by walking LAND → LTEX → TNAM → TXST → TX00 → `.dds` (the old
`ltex_check.py` did this; removed 2026-08-25 as a one-plugin script) —
dangling LAND layers).

**Why this hid for so long:** the keep-set is applied by `bsa_pack` when the
textures archive is staged, and the mesh phase re-copies the whole texture tree
into `output/` on every run. A full pipeline run therefore always has the
textures back on disk by the time anyone looks, so a wrong keep-set only ever
showed up *inside the BSA* — never as a missing file. It bites hardest on
`--mesh-subdirs` runs, where the manifest names a fraction of the tree.
Corollary: **file mtimes in `output/textures/` prove nothing** about what the
keep-set did — `copy2` preserves the extract cache's timestamps.

`refs_from_records` used to regex-scan every `.txt` in the export (~2 GB for
Nehrim, minutes) because `_TEX_TEXT_RE` opens with a lazy star and expands at
every position on text with no match. `LAND.txt` alone is 1.47 GB on Oblivion
with zero `.dds` in it. A `'.dds' not in body` substring test skips those
outright: **4.2 s** for the whole export.

### TXST for Landscape Textures
- No DNAM: vanilla Skyrim LTEX TXSTs omit DNAM. The 0x0001Fa "No Specular Map" flag only applies to the object (BSLightingShader) path, NOT the landscape shader. Writing it has no positive effect.
- TX00 = diffuse (`tes4\landscape\<icon>.dds`)
- TX01 = normal map (`tes4\landscape\<icon>_n.dds`)
- LTEX SNAM specular exponent: **pass through the TES4 value**. SNAM is a Phong exponent used directly by the landscape shader. Setting SNAM=0 gives `pow(NdotH, 0) = 1.0` everywhere → whole landscape appears blindingly bright white. TES4 landscape textures use ~30 (moderate gloss). Do NOT write SNAM=0. An ABSENT SNAM is safe: the `TESLandTexture` constructor (1.6.1170, RVA 0x2b0950) writes 30 at +0x38, the value all 68 vanilla LTEX author, so a TES3 source with no specular exponent renders at vanilla gloss.

## OBND (Object Bounds) defaults
<a id="obnd-defaults"></a>
- ESM records without OBND crash the engine. Import script generates per-type defaults:
  - MISC=(-5,-5,0,5,5,8), KEYM=(-3,-3,0,3,3,3), WEAP=(-5,-5,0,5,5,30), STAT=(-50,-50,0,50,50,80)
  - ARMO=(-15,-10,0,15,10,30), NPC_/CREA=(-12,-12,0,12,12,60), LIGH=(-6,-6,0,6,6,20)
  - Other types get (-5,-5,0,5,5,5) as fallback

## The world-map camera clamp — MNAM's cell rectangle (verified by disassembly)
<a id="world-map-camera-clamp-mnams"></a>

How far the world map can SCROLL is set by WRLD `MNAM`'s NW/SE **cell**
rectangle — not by `NAM0`/`NAM9`, and not by any LOD, terrain, `.btr`/`.bto`,
or map-image input. Recovered from `SkyrimSE.exe` (GOG/AE):

`MapCameraStates::World::Update` at RVA **`0x9213e0`** (vtable
`.?AVWorld@MapCameraStates@@` @ `0x17b0fc0`, slot 3) branches at `0x9216ac` on
`MapCamera+0x68`, a border-polygon list, into two **mutually exclusive** clamps:

* **MNAM border polygon (wins whenever present).** Built in the state-enter
  handler at RVA `0x9219f0`. It calls `0x2c7d80`, which walks the parent chain
  (`WRLD+0x158` = WNAM) while `WRLD+0xa2` bit 2 (**PNAM "Use Map Data"**) is
  set and returns `owner+0x188`, the MNAM blob. **If all four MNAM cell int16s
  are zero it jumps to `0x921cb2` and leaves the polygon NULL**; otherwise
  `0x921b20`/`0x921b94`/`0x921c08`/`0x921c7c` build four vertices from
  `NW.X(+8) NW.Y(+0xa) SE.X(+0xc) SE.Y(+0xe)`, each `shl 12` (cells → world
  units). Clamping is a point-in-polygon push-back at `0x921f10`.
* **NAM0/NAM9 box — FALLBACK ONLY,** reached at `0x921717` solely when the
  polygon is NULL (`je 0x921717`). Clamps X into `[WRLD+0x1c0, +0x1c8]` and Y
  into `[+0x1c4, +0x1cc]`, filled by `TESWorldSpace::Load` (`0x2c5620`) via
  `minss` on NAM0 (`0x2c57a2`) and `maxss` on NAM9 (`0x2c591b`). MNAM lands in
  separate storage at `+0x188` and is never read by the box clamp.

`MNAM+0x10/+0x14/+0x18` are Min Height / Max Height / Initial Pitch, matching
xEdit's `wbWorldMapData` "Camera Data" — which validates the offset mapping.
`UsableDimX/Y` participates in nothing here; all 3 Skyrim.esm WRLDs that carry
MNAM write `(0, 0)`, Tamriel included, so we write 0 too.

### A worldspace's rectangle is SHARED, so it is unioned across plugins

🛑 **The last plugin to override a WRLD wins, so one plugin that never touched
the terrain can clamp the map back down.** Ten converted plugins override
Tamriel `0100003C`; only `Tamriel.esp` adds the outer land (99,946 cells, grid
X -192..191, Y -129..159). The other nine measure nothing there, fell back to
Oblivion's authored 119x106-cell rectangle, and three of them are plain ESPs
that therefore load after every ESM — so the widened map reverted to Cyrodiil.
`ElsweyrAnequina.esp` additionally reverted NAM0/NAM9 to cells -64..70.

So the extent is **unioned across every plugin built into an output tree** and
persisted in `output/world_extents.json`
(`tes5_import.import_main._merge_world_extents`), keyed by output-space FormID.
Every plugin emits the same widest rectangle and load order stops mattering.
A narrow measurement can only ever widen the stored box, never shrink it.

`set_world_land_extents` UNIONS for the same reason: it is called twice per
import -- once over every exterior cell before the override pass, and again
from `_build_world_groups` over just the own-hierarchy cells -- so replacing
would let the narrower second call shrink a rectangle the first measured
correctly.

## World-map cloud banks (WRLD MODL) — sized to the LAND
<a id="world-map-cloud-banks-sized"></a>

Skyrim's world map draws a bank of cloud sheets over the terrain. The mesh is
picked by a three-step fallback in the engine (`SkyrimSE.exe` RVA `0x2c7e00`,
the only cross-reference to the string): the PARENT worldspace's cloud model
when WRLD `DATA` bit 2 ("Use Map Data") is set, else this worldspace's own
`MODL` (xEdit's `wbRStruct('Cloud Model', [wbGenericModel])`, written between
DNAM and MNAM), else a HARDCODED `Meshes\Sky\SkyrimWorldMapCloudBank.nif`.

Oblivion has no world-map cloud layer and vanilla Skyrim authors no MODL
either (0 of 35 uncompressed Skyrim.esm WRLDs carry one), so without this every
converted worldspace inherits a bank sized for Skyrim's Tamriel.
`asset_convert/lod/worldmap_clouds.py` emits one per worldspace and points MODL at
`meshes\tes4\worldmapclouds\<edid>.nif` (under `tes4\`, never `sky\`, so a
generated bank can never shadow the vanilla file the weather system loads by
name).

### <a id="cloud-bank-rect-precedence"></a>Size and center come from the exterior CELL GRID, not from MNAM or NAM0/NAM9

Per axis the deck is given **Bethesda's own deck-to-land ratio** — the stock
910,445 sheet against Skyrim's 487,424 x 385,024 land, i.e. **1.868x on X and
2.365x on Y** — and centered on the land rectangle's midpoint. Feeding Skyrim's
own land back in reproduces the stock scale of exactly **8.0 / 8.0**, which is
the control that validates the rule.

Both inputs are measured from the worldspace's non-persistent exterior cells
(`tes5_import.import_main._land_extents_by_wrld`; cell `(gx,gy)` spans
`gx*4096 .. (gx+1)*4096`). Persistent cells are excluded — they hold the
worldspace's persistent refs, are commonly parked at a dummy `(0,0)`, and drag
the extent toward the origin.

🛑 **MNAM is authored map-camera framing and a converted plugin's can simply be
WRONG about its own terrain.** NehrimWorldspace's MNAM rectangle is centered
26,624 units SOUTH of its land and its north edge clips 16,384 units of real
land off. Sizing or centering off it produces a deck that is both offset and
undersized. MNAM, then NAM0/NAM9, remain fallbacks only for a worldspace that
contributes no cells.

### The sheets must be stretched PER AXIS, and the UVs stretched with them

A NIF node `scale` is a single float, so it can only size the (square) stock
sheet off its longer side. Nehrim's land is portrait (92 x 101 cells) where
Skyrim's is landscape, so a uniform scale hangs far more cloud across the short
axis than the tall one needs. The stretch is therefore baked into the VERTICES
with each node's `scale` set to 1.0 — node scale and vertex scale multiply, so
leaving it at the stock 8.0 applies the factor twice.

🛑 **The clouds are a TEXTURE (`textures\sky\SkyrimCloudsMap01.dds`), not vertex
alpha.** Stretching vertices while leaving UVs alone keeps the cloud pattern
pinned to the same FRACTION of the sheet at any size, so the dense border band
lands wherever it likes relative to the terrain and no amount of resizing moves
it. UVs are scaled by the same world-span factor (`sx / 8.0`, since the
vertices already absorbed the stock node scale — scaling by `sx` directly
over-tiles by 8x). Verified by texel density: units-per-UV must come out
identical to stock (75858 / 94209 / 45791 / 192743 for High/Mid/Top/Low).

Only the horizontal axes are touched. Vertex Z (the sheet's own relief) and the
nodes' Z translations (cloud ALTITUDES: 0 / 1000 / 1500 / 12500) are preserved;
scaling those would sink the deck into terrain or launch it out of frame. The
bounding sphere is updated unconditionally, since X/Y always change.

### Sibling worldspaces: union the LAND, not the WRLD records

`sibling_lod.merge_cloud_bank` writes ONE bank covering every contributor, into
the merged LOD folder that installs last and wins the overwrite deliberately.

🛑 **The union must be measured from each plugin's cells** (`_wrld_land_bounds`
+ `_wrld_formid`, reading XCLC out of the built ESM). A dependent overrides the
master's WRLD record WITHOUT touching MNAM/NAM0/NAM9, so all five TES4Tamriel
contributors report the identical rectangle `X[-241664,245760]` and a
record-based union collapses to the master's 487,424 x 434,176 — against a real
combined land span of **1,572,864 x 1,183,744**, a deck 3.2x too small. That is
exactly the overwrite bug the function exists to prevent.

### Benign: two copies of each bank exist

`create_lod.py` calls `merge_cloud_bank` for every worldspace, including ones
with no contributors, so a solo plugin gets a second copy under
`output/AutoConvertLOD/`. Both resolve to the same Data-relative path and the
LOD copy wins. This is harmless — but note that **`--import-only` refreshes only
the per-plugin copy**, so after an import-side change the game still loads the
older AutoConvertLOD mesh until a LOD run regenerates it. Verify the copy that
actually wins before concluding a cloud-bank change had no effect.

## <a id="split-only-the-meshes-that-need-it"></a>Split only the interiors that actually need it

**Code:** `navmesh/split.py` — `split_disconnected_interiors`,
`_has_cross_component_door_pair`, `_component_fids`, `_split_one_mesh`.

A cell's disconnection is only the CharacterGen-class bug (see that module's
docstring) when a same-cell teleport pair has its two ends in DIFFERENT
components — the one case the engine cannot route without a split. Most
disconnected interiors have no such pair: two unrelated rooms that never
teleport to each other. Splitting those serves no pathing purpose and only
multiplies the NAVM/NVMI records the CK revalidates on load.

**Measured on Oblivion.esm: 352 cells have multiple components, but only 18
have a genuine same-cell door pair spanning them.** With all 352 split the CK
hung in *"Initializing References"* (confirmed live, and unaffected by fixing
an unrelated portal-link bug) — variance the plugin cannot be blamed for once
every check passes. Splitting only the 18 that need it is the generic fix, not
a size cap.

🛑 **Sibling FormIDs key on AUTHORED data, never on component order.**
Component order is DERIVED: it falls out of triangle connectivity, so any
change upstream of triangulation renumbers every component and would move the
ids. `_component_fids` therefore keys each sibling on the sorted door REFRs its
triangles touch — TES4 ids that survive re-triangulation — falling back to the
ordinal only for a component with no door, which carries no door state to lose.
Moving one of these ids breaks saves.

## <a id="collision-geometry-and-the-cache-hit"></a>Collision geometry, and what `convert_PGRD` keeps inline

**Code:** `from_pgrd._cell_graph` / `_cell_frame` / `_cell_geometry`,
`convert_PGRD`.

`convert_PGRD` reads as four phases: the pathgrid graph, the cell frame, the
collision geometry, then NVNM packing and the `meta` dict. The first three are
helpers; packing stays inline because it is the record contract this module
exists to own.

**The graph helper is shared with the cache key, and must stay shared.**
`_cell_graph` returns the points and edges INCLUDING the synthetic PGRI exit
nodes, and that exact tuple feeds `geom_hash`. `convert_PGRD` used to carry its
own inline COPY of the same logic; any divergence between the two silently
invalidates every cached entry, which is why `cell_geom_key` and the converter
now call one function. Equivalence of the two forms was verified across the
explicit-PGRR, nearest-neighbour-fallback, exterior-with-PGRI, single-node and
decline branches before the copy was removed.

**A single pathgrid node is only viable WITH a PGRI link.** One node with no
in-cell edge can never form a ribbon, but one node with cross-cell links can:
the synthetic exit edges lay cross-seam stubs. **53 exterior cells shipped with
no navmesh at all** under the old `< 2`-points gate, leaving holes in the
cross-cell network exactly where a road crosses a cell corner.

**Verts are rounded to float32 BEFORE the cache store** (`_cell_geometry`). The
cache holds float32, NVNM packs float32, so rounding at build time is what makes
a fresh build and a later cache hit produce byte-identical NVNMs. Moving the
rounding after the store would make the two paths disagree in the low bits.

🛑 **`geom_key` is the hash VALUE; `geom_hash` is the module FUNCTION.** Naming
a local after the function shadows it — the call then resolves to `None`,
raising `'NoneType' object is not callable` per cell and **silently producing 61
empty navmeshes with every XNDP door link dropped**, which no test caught.

## The shared navmesh cache — design rationale
<a id="shared-navmesh-cache-design-rationale"></a>

Navmesh generation is the slowest import stage. Results are cached per cell in
`export/<plugin>/navmesh_geom_cache/*.pkl` and published as a **GitHub Release
asset** so downloaders don't regenerate them. Not committed (git keeps every
version of a churning binary forever) and not Git LFS (free tier is 1 GB
bandwidth per *month* — about three clones).

Commands are in [CLAUDE.md](../../CLAUDE.md#shared-navmesh-cache).

### <a id="geom-payload-version"></a>The `geom-vN` literal versions the PAYLOAD, not the inputs

**Code:** `geom_hash` in `tes5_import/navmesh/from_pgrd.py`

`geom_hash` mixes a literal `geom-vN` into the key, bumped whenever the cached
payload's SHAPE changes even though its inputs did not — otherwise an older
entry silently restores geometry the current build would never produce:

- **v2** — entries began carrying ledge links; an older entry restored
  geometry with no drop-downs.
- **v3** — analytic door wedges (exact width, center and apex side) changed
  the geometry of every cell with a door, with identical inputs.
- **v4** — per-mesh collision digests replaced the whole-file collision hash
  that used to ride in via `tag`. One replaced mesh previously invalidated
  **every** entry (~8,200 for Oblivion) and forced a full regeneration; now
  only the cells that actually place that mesh miss. This is also what lets a
  published cache survive a user's own mesh edits — see
  `collision_extract.collision_digest` and `tools/navmesh/navmesh_cache.py`.

### <a id="the-tag-covers-the-native-march"></a>The tag hashes the native march too, by SOURCE

**Code:** `_native_tag_sources` / `navmesh_geom_cache` in `tes5_import/navmesh/pool.py`

The tag globbed `tes5_import/navmesh/*.py` only, but the width-grow march that
decides a cell's geometry lives in `native/src/navgrow/grow.cpp`. Editing it
changed every exterior mesh while leaving the tag — and therefore every cached
entry — untouched: the cache stayed *valid* and became *wrong*, which is worse
than a miss because nothing reports it. `_TAG_NATIVE` names the native sources
that decide geometry, and `NAVMESH_PATHS` in `navmesh_cache_hook.py` carries the
same path so a push republishes.

It hashes the **`.cpp`, never the built `.pyd`**: a different compiler or flag
gives identical source a different binary, so hashing the artifact would miss
every downloader's cache for no semantic reason. The lookup is anchored on the
repo rather than on `pool.__file__` — `test_tag_ignores_line_endings` copies the
navmesh package to a temp dir, and a path derived from that copy finds no native
tree. A native source that is missing entirely drops out of the hash instead of
voiding the tag, so a source-only checkout still tags.

### <a id="cache-tag-steps-in-its-own-scheme"></a>`previous_tag` steps in the tag's OWN width

**Code:** `_raw_minor_field` / `previous_tag` in `tools/navmesh/navmesh_cache.py`

The step reads the tag's RAW digits rather than `_version_key`, whose value is
normalized to thousandths for comparison. The WIDTH is the scheme here, not the
value: `0.73` is a hundredths name even though 730 sits above the
`_SCHEME_SWITCH_MILS` (580) boundary, so it must step to `0.72`, never `0.729`
-- a tag that names a release which never existed and 404s the download.

### <a id="door-triangle-tie-break"></a>The door triangle tie-break is AREA, not index

A door point lies **on** the threshold, which after constraint recovery is a
shared triangle EDGE — so two or more triangles legitimately contain it and the
choice is a genuine tie.

Breaking that tie by triangle INDEX picked an arbitrary winner. On the
**CharacterGen assassins' cell door** the same geometry yielded either a
**1,586-unit triangle or a 572-unit sliver** depending only on iteration order,
and the sliver is too narrow for an actor to stand on (vanilla door triangles:
**min 992, median 9,614**).

`_containing_triangle` therefore prefers the **largest** containing triangle,
which is both standable and deterministic. Heights within a step (32 units) are
treated as the same floor and ranked by area, so a sliver never wins over the
real door triangle beside it.

### <a id="nvnm-xxxx-size-protocol"></a>An NVNM over 64 KB needs the XXXX size protocol

A subrecord's own length field is 16-bit. When a payload exceeds 65,535 bytes
the format writes a **4-byte `XXXX` subrecord carrying the REAL size**, and the
following subrecord's own length field reads 0.

`edge_links.extract_nvnm` must honour that or an oversized NVNM reads as empty
and the whole mesh silently skips edge linking. This was invisible while meshes
were small, but the interior-lattice triangulation pushed **119 exterior meshes
past the limit** and every one of them shipped as a cross-cell island.

`pack_subrecord` re-emits the `XXXX` prefix on write, so the one the reader
consumed is deliberately dropped rather than carried through.

### <a id="what-gates-a-push"></a>What gates a push, and why the lists are generous

`NAVMESH_PATHS` names the sources whose **bytes feed the cache tag**, and must
stay in step with `pool.navmesh_geom_cache` — a test asserts they agree.

`NAVMESH_FUNCS` is different: those files do **not** feed the tag, but hold
code that can change *what* gets cached or *how it is keyed*. Listing a
function there makes the CHECK run; it does not by itself block anything. A
block happens only if the check then finds the cache was built by different
code (`cache_matches_tag`), so over-triggering costs a fast stamp comparison
and nothing else — a cache that is already correct always passes.

**That asymmetry is why the lists can stay generous: a missed trigger ships a
dead cache, an extra trigger costs microseconds.**

Attribution uses git's `-U0` hunk headers, which name the enclosing function
(the same technique `release_notes.py` uses to attribute `convert.py` per
phase). It exists to keep the *reported reason* honest — so the hook says "you
changed `_gather_navm_jobs`" rather than "you changed `pipeline.py`" — not to
suppress checks.

**`navmesh/edge_links.py` is excluded from the tag** (`pool._TAG_EXCLUDE`).
`build_edge_links` stitches cross-cell portals into the NVNM *after* geometry
comes out of the cache, so editing it changes the written mesh but never the
cached geometry. Note that the restructure moved this file **under the
`tes5_import/navmesh/` prefix**, so every edit to it now gates a push via
`NAVMESH_PATHS` unless `NAVMESH_EXCLUDE` keeps listing it. That is a permanent,
low-grade cost — an unnecessary tag bump per edit — and never a wrong mesh.

### <a id="publishable-plugins"></a>`PUBLISHABLE_PLUGINS` — why a whitelist

The only plugins we publish a shared cache for. Everything else under `export/`
is a local experiment: a DLC, a landmass mod, a half-converted ESP somebody ran
once. Those caches are worthless to a downloader (nobody else has that plugin)
but they are **not harmless** — `discover_plugins()` is what the pre-push gate
iterates, so a 0-entry or partially-generated cache from a throwaway run fails
`verify()` and **blocks the push**, and a large one gets zipped and uploaded as
a release asset nobody wants.

Deliberately a whitelist, **not a size/entry-count heuristic**: "big enough to
publish" would silently start shipping the next landmass mod that happens to
cross the threshold. Adding a plugin here is a decision to host its cache.

Matched case-insensitively — `export/` folder names come from whatever the user
typed after `-f`, and `Nehrim.esm` vs `nehrim.esm` must not change what gets
published.

### GAP (unfixed): the download path ignores `PUBLISHABLE_PLUGINS`

**Measured 2026-08-26.** `auto_install` makes its anonymous releases API call
for **any** plugin, including ones whose cache is never published. The whitelist
that should gate it already exists and is already correct:

```python
PUBLISHABLE_PLUGINS = ('Oblivion.esm', 'Nehrim.esm', 'Morrowind_ob.esm')
def is_publishable(plugin): ...   # case-insensitive
```
([navmesh_cache.py:149](../../tools/navmesh/navmesh_cache.py#L149))

It gates **publishing** — `discover_plugins`
([:171](../../tools/navmesh/navmesh_cache.py#L171)) and the `publish` command
([:1390](../../tools/navmesh/navmesh_cache.py#L1390)) both filter on it — but
**nothing on the download side consults it**. `auto_install`
([:1138](../../tools/navmesh/navmesh_cache.py#L1138)) walks
already-current → drop-ins → `allow_download` → `_api_releases()`
([:1222](../../tools/navmesh/navmesh_cache.py#L1222)) with no plugin-name check
anywhere.

Cost per non-cacheable plugin, per import run: **one wasted API call (~0.5 s
measured)** that can only ever end in "no matching asset". `auto_install` is
invoked once per plugin ([convert.py:662](../../convert.py#L662)), so a user
converting their own mods pays it every run for a lookup guaranteed to miss.

**The fix is a single early return** in `auto_install`, after the
already-up-to-date and drop-in checks but **before** `allow_download` /
`_api_releases()`. Ordering matters:

- Drop-ins must still work for *any* plugin — a user who builds and drops in
  their own zip is a supported path and must not be gated by a whitelist about
  what *we* host.
- Only the **network** step is restricted, so the gate belongs immediately
  before it.

**Verified safe — nothing is lost.** Two off-whitelist assets exist
(`navmesh-cache-DLCBattlehornCastle.zip`, `navmesh-cache-ElsweyrAnequina.zip`),
but they appear on exactly one historical release, `navmesh-cache-0.586-0.586`
(2026-08-11). That range covers 0.586 only; current builds report 0.616, so the
existing version-range gate already rejects them. Every current release
(`0.616+`, `0.609-0.615`, `0.600-0.608`, …) carries only the three whitelisted
plugins.

Keep the user-facing message honest when gating: for a non-hosted plugin the
truth is "no cache is published for this plugin", **not** the existing "could
not reach the releases API" wording, which would be a false diagnosis.

### GitHub anonymous rate limit — measured, not a practical risk

The download path is anonymous (`_api_releases`,
[navmesh_cache.py:307](../../tools/navmesh/navmesh_cache.py#L307)) and GitHub's
anonymous REST limit is **60 requests/hour, counted per source IP** — shared by
everyone behind that IP. Measured 2026-08-26, this is nonetheless fine:

- **A cache install costs exactly ONE API call.** `auto_install` is invoked once
  per plugin per import run ([convert.py:662](../../convert.py#L662)), and its only
  API call is the single `releases?per_page=100` request. Verified: remaining
  went 60 → 59.
- **The asset download itself costs ZERO.** `browser_download_url` redirects to
  `release-assets.githubusercontent.com`, which is outside the API. Verified by
  range-fetching 1 MB of the real 114 MB `navmesh-cache-Oblivion.zip` — API
  remaining was unchanged (56 → 56).
- So a user converting all three plugins spends **3 of 60**. Even a shared
  university/office NAT would need ~20 simultaneous first-time users in one hour
  to exhaust it.
- Today that is **1 call per plugin converted**, not per *cacheable* plugin —
  see the gap above. Gating on `is_publishable` caps it at 3 per run no matter
  how many plugins a user converts.

**On exhaustion it degrades safely, and this is already handled.** A 429 makes
`_api_releases` return `[]`, which the caller treats exactly like being offline:
it prints "could not reach the releases API (offline or blocked); generating
normally", names the manual drop-in route, and regenerates
([navmesh_cache.py:1223](../../tools/navmesh/navmesh_cache.py#L1223)). The cost is
slow generation, never wrong geometry or a failed run.

The one aggravating factor to keep in mind: **any future anonymous API caller
shares this same 60/hour budget** — notably the update check
([version.py:965](../../version.py#L965)) and the planned in-app updater
([in_app_update_plan.md](../plans/in_app_update.md)). That is why the updater's
launch check caches its result rather than polling every start.

**Never ship `collision_cache.bin`.** It maps Oblivion mesh *paths* to verbatim
Havok collision triangles lifted from Bethesda's NIFs — derived asset data keyed
by asset name. Only the generated `navmesh_geom_cache` pickles (hash + verts +
tris + ledges, our own output) go in the archive; the manifest carries a one-way
hash of the collision cache to prove a local build matches. For the same reason
archiving names the cache dir explicitly: globbing `export/**/*.pkl` would sweep
in the ~2.1 GB index pickles.

**Invalidation is per mesh, not per file.** Each cell's hash folds in the
collision digest of only the meshes *that cell places*
(`collision_extract.collision_digest`), so replacing a few meshes costs only the
cells that use them. It used to hash the whole collision file, where one changed
mesh invalidated all ~8,200 Oblivion entries.

**The cache tag must stay machine-independent.** It hashes the navmesh sources
only. It previously folded in `collision_cache.bin`'s *mtime*, which is
machine-local and survives neither git nor an unzip — every downloader computed
a different tag and the shared cache would have missed 100% of the time. Never
reintroduce mtime, absolute paths, or worker counts into any cache key.

**`CACHE_TAG` is written only by a real, failure-free generation pass.**
Computing the tag must never stamp it, or reading the tag would certify a stale
cache as fresh. A stale entry always regenerates, so a wrong cache is *slow*,
never *incorrect*.

**The pre-push gate only runs on direct pushes to master** (a PR merged in
GitHub's UI runs no local hook) — CI cannot validate a cache built from
gitignored `export/` data. Use `--run` for the PR case.


## The download path never runs `git`
<a id="the-download-path-never-runs-git"></a>

**Code:** `navmesh_cache.api_repo`, `CACHE_REPO`.

git is **not a prerequisite** for running the converter and must not be reachable
from any end-user path. The README tells people to paste a source drop over their
folder, so the usual install has no `.git` directory, and many such machines have
no `git` executable on PATH at all.

`api_repo()` used to derive `owner/name` by spawning
`git remote get-url origin` (via `gh_repo()`), falling back to a constant when
that returned non-zero. The fallback never fired on a machine without git:
`subprocess.run` raises `FileNotFoundError` — `[WinError 2] The system cannot
find the file specified` — at *launch*, before a return code exists. That
exception propagated out of `_api_releases()` (whose own `try` starts after the
`api_repo()` call) and was swallowed by `auto_install()`'s catch-all, which
printed `Navmesh cache: skipped ([WinError 2] ...)` and regenerated navmesh from
scratch — hours of work, silently, for precisely the population the shared cache
exists to serve.

The repo name is now the constant `CACHE_REPO`. Nothing about the download needs
git or `gh`: the release listing and the ~115 MB asset transfer are both plain
anonymous HTTPS through `urllib.request`. Deriving the name only ever helped a
*fork's developers*, and cost every gitless end user the entire feature.

`gh_repo()` still shells out to git and is still correct — it is used solely by
the publish path and by `install`'s explicit-`--tag` branch, both of which gate
on `have_gh()` first, so git is guaranteed there. It stays because every `gh`
call must name the repo explicitly instead of relying on the process CWD:
`install` is the one command a user may run from outside a checkout (or against
a redirected repo root), and a bare `gh release list` there reports "no releases
found" rather than failing loudly — which is exactly how a working publish once
looked broken.

**Do not reintroduce a git call on the download path**, guarded or otherwise.
`version.py:_version_from_git_dir` makes the same choice for the same reason and
reads `.git` ref files directly rather than spawning.


## Verifying a cache against fresh geometry
<a id="verifying-a-cache-against-fresh-geometry"></a>

**Code:** `pgrd_to_navm.geom_equal` / `geom_quantize`, `navm_worker.run_job`,
`import_main._precompute_navmeshes`, `tools/navmesh/navmesh_cache.py adopt`.

**A prover must build exactly as the import does, and must never store.** Two
defects made a child plugin's cache look non-reproducible (Morrowind_ob, push
gate refusing with a geometry MISMATCH of 1-3 verts on scattered cells):

- `navmesh_adopt.load_plugin` and `job_trace` loaded only the plugin's OWN
  `collision_cache.bin`, while the import loads `collision_cache_chain`
  (masters first). Master-owned meshes had no collision in the tool, so cell
  024802DC built 107 verts there against the import's 109, and 024802C1 354
  against 355. Both now go through `job_trace.init_worker_for`.
- `job_trace.load_export` never called `set_namespace(namespace_for(...))`,
  so model keys took the default `tes4/` prefix. Nehrim's are `nehrim/`: all
  10,337 base-model keys differed from the import's and EVERY Nehrim cell
  mismatched (011A575C 36 verts cached, 37 in the tool), while Oblivion, whose
  namespace IS `tes4`, passed 40/40. It looked random because the old proof
  was vacuous while the tag matched (a cache hit compared with itself) and
  only really compared after a navmesh source edit.
- A proving rebuild ran `run_job` with the live cache. Once the tag has moved
  every lookup misses, so the rebuild STORED over the entry being proven — a
  refused adoption left its wrong geometry behind under the new tag. Proving
  jobs now carry `job['prove']`, which builds with `geom_cache=None`.

The source tag is a *proxy* for "the generator's behaviour changed": it hashes
the bytes of `tes5_import/navmesh/*.py` and `pgrd_to_navm.py`. It cannot tell a
behaviour change from a rename, a docstring edit or a file split, so a pure
refactor invalidates every entry and forces a full regeneration.

**Measured, on the `corridor_union.py` split** (−474 net lines into
`union_cdt`/`union_geom`/`union_mesh`/`union_sheets`): comparing the 2,136
pre-refactor entries that survived against the regenerated cache gave
**2,136/2,136 identical** verts, tris and ledges — and a *different* per-entry
`hash` on every one. The geometry was right and the key was wrong. That is the
whole problem this machinery exists to solve.

**Adoption** proves the output is unchanged, then re-keys the entries instead of
rebuilding them: `_geom_hash` takes the tag as its first argument, so re-keying
is a hash recompute per cell with no geometry rebuild. A 95-minute regeneration
becomes a sampled verification plus a rewrite.

**Comparison is exact, and that is sound.** Nothing under `tes5_import/navmesh/`
uses randomness, clocks, threads or pools; every iterated `set` holds ints or
int-tuples (`PYTHONHASHSEED` randomizes only `str`/`bytes`/`datetime`); the
native extension is single-threaded and built `/fp:precise` so the compiler may
not reassociate float ops. `convert_PGRD` demotes verts to float32 on the
fresh-build path *before* packing or storing, so a fresh build and a cache hit
converge on the same bits by construction — `geom_quantize` exists so a caller
comparing `build_navmesh`'s raw f64 return applies the same demotion.

**The tag does not cover shapely/GEOS or the compiled `.pyd`.** The union path
leans on GEOS (`unary_union`, `constrained_delaunay_triangles`, `buffer(0)`,
`STRtree`), so a library upgrade can change output *without* moving the tag.
That is why adoption is not enough on its own: **the import re-verifies a sample
of cache hits on every run**, rebuilding those cells and comparing. A mismatch
warns, drops the cache for the remainder of the run, and regenerates — so a
wrong cache costs the sample, not silently wrong navmesh. This restores the
"slow, never incorrect" guarantee that adoption otherwise weakens.

**Re-keying must never build geometry.** `_geom_hash` is a pure function of a
cell's INPUTS -- pathgrid graph, REFRs, doors, LAND, per-mesh collision digests
-- and `convert_PGRD` computes it *before* deciding whether to build. So
adoption re-keys through `cell_geom_key`, which shares `_cell_graph` with
`convert_PGRD` and stops at the hash. Routing it through `convert_PGRD` instead
rebuilds every cell to learn a value no geometry feeds: **measured 1,869 ms/cell
against 0.9 ms/cell — 8.7 hours against ~8 seconds for Oblivion's 8,221
entries**, which would make adoption slower than the 95-minute regeneration it
exists to replace. `_cell_graph` is shared rather than duplicated precisely
because a divergence between the two derivations would silently invalidate
every entry.

**The import adopts a stale cache before it regenerates one.**
`navm_verify.prepare` runs before the navmesh pool dispatches: when `CACHE_TAG`
does not match the current tag, it proves a sample against the STORED geometry
and, if every cell reproduces, re-keys the entries and stamps the new tag. Only
if a cell genuinely differs does the run regenerate.

This is the case a user hits after editing any navmesh source, and it is the
one that matters most: the tag moves, every entry misses, and without adoption
the import rebuilds *all* ~2,900-8,200 cells for geometry that is already
correct. Measured on a real moved tag: **Morrowind_ob 40/40 identical → 5,239
entries adopted → 40/40 cache hits, 0 rebuilt**; Nehrim 39/39 → 2,885 adopted.
With a deliberately corrupted entry the same path refused (1/7 differ) and left
the stamp uncertified.

**Proving runs on the pool, not in the parent.**
<a id="proving-runs-on-the-pool"></a>
Proving rebuilds real cells with the same `navm_worker.run_job` the main stage
uses, so it takes the same parallelism: `pool.pooled_prover` hands
`cache_audit.prove_cache` a `rebuild` callable backed by the same
`ProcessPoolExecutor`, initializer and `initargs` as `_run_pooled`. Serially,
adoption cost the sample size in full cell builds (**1,869 ms/cell measured**,
so ~75 s for the default 40) immediately before a stage that fans the identical
call across every worker.

Results are yielded in **submission order** (`ex.map`, not `as_completed`), which
is what keeps the early exit deterministic: the first mismatch a caller sees is a
property of the job list, not of which worker happened to finish first. Two runs
over the same cache therefore refuse on the same cell and report the same
`checked` count.

`prove_cache` keeps a serial default (`rebuild=None` calls `run_job` directly),
because `navmesh_adopt` and the tests drive it outside a pool context, and a
pool of one worker is slower than no pool at all.

**Proving stops at the first mismatch.**
<a id="proving-stops-at-the-first-mismatch"></a>
Both callers of `cache_audit.prove_cache` — `adopt_if_unchanged` and
`navmesh_adopt.adopt` — refuse on any non-empty `bad`, so a single differing
cell has already decided the verdict. Continuing spends a full rebuild per
remaining cell (**measured 1,869 ms/cell**) to enrich a message nothing reads:
the refusal prints `N/checked differ` and regenerates either way. A cache that
has genuinely changed is the common failing case — a real behaviour change
moves most cells, not one — so the break usually lands on cell 1 and turns a
~75-second sample into ~2 seconds. `checked` therefore counts cells COMPARED,
not the sample size, and the refusal line reads `1/1 differ`.

Verifying only cells that ALREADY HIT is not enough on its own — that guards a
cache whose tag matches but whose geometry does not (GEOS drift, a bad adopt),
and does nothing when the tag has moved, because then nothing hits at all. Both
halves are needed.

**Adoption needs one process per plugin.**
<a id="adoption-needs-one-process-per-plugin"></a>
Per-plugin state lives in module globals: the collision soups
(`collision_extract._COLLISION`), the door panel tables in `pgrd_to_navm`, and
the injected-FormID map. Loading a second plugin into a process that already
holds the first mixes them, cells build against the wrong data, and adoption
REFUSES a cache that is perfectly good. Measured: **Morrowind_ob.esm verified
12/12 alone, and 5/12 immediately after Nehrim.esm in the same process** —
a false refusal that looks exactly like a real behaviour change. The real
pipeline runs one plugin per invocation, so adoption does too.

Note `load_door_centroids` is skipped entirely when no `door_centers_cache.json`
exists, which leaves the previous plugin's door widths in place; Nehrim and
Morrowind_ob both lack that file while Oblivion has it. Passing the path
unconditionally makes the loader clear its tables even when the file is absent,
but that alone does not fix the collision/FormID leak — only a fresh process
does.

**The verify budget is chosen in the parent, never held per worker.**
`initargs` are copied into every process, so a worker-side budget would verify
N cells *per worker* — on a 29-worker run that is ~1,160 rebuilds, turning a
bounded check into most of a regeneration.


## `convert_PGRD` arguments worth explaining
<a id="convert-pgrd-arguments"></a>

**Code:** `pgrd_to_navm.convert_PGRD`.

- **`navm_fid`** — a pre-allocated NAVM FormID. When given, the writer is not
  touched for allocation, which lets callers assign FormIDs deterministically
  before farming the heavy, scipy-bound geometry work out to a pool.
- **`geom_cache`** — `(cache_dir, tag)` enabling the on-disk geometry cache. The
  tag must cover the generator code; collision enters per mesh via `_geom_hash`.
- **`extra_door_refrs`** — door REFRs that stand in this cell but are PARENTED
  elsewhere. Exterior teleport doors are persistent refs living in the
  worldspace's persistent (dummy) cell, so the per-cell REFR list never contains
  them. Without this, exterior meshes got door triangles on only **89 of 6,516**
  cells and cross-door pathing died at every house door and city gate. They feed
  the door threshold stamp and door-triangle linking only.
- **`meta['geometry']`** — the `(verts, tris, ledges)` this call produced, so a
  verify pass can compare a cache hit against a fresh build without re-deriving
  the inputs.


## Navmesh redesign: pathgrid corridor ribbons
<a id="navmesh-redesign-pathgrid-corridor-ribbons"></a>

> **Status: IMPLEMENTED and live on `master`** (design approved 2026-07-23;
> status corrected 2026-07-26 — this header previously read "design, not yet
> implemented"). `build.py::build_navmesh` keeps its historical signature and
> **delegates to `corridor.build_corridors`**. The corridor modules are
> `corridor.py`, `corridor_clean.py`, `corridor_doors.py`, `corridor_grow.py`,
> `corridor_union.py`, plus `params.py` and `world.py`.
>
> The superseded voxel/span-graph generator is **DELETED** from `master`:
> `voxel.py`, `region.py`, `spanmesh.py` and `native/src/decimate.cpp` no longer
> exist. The Recast-era generator remains on branch **`test-navmesh-2`**.
> Performance work on the corridor path is recorded in
> [performance_notes.md](performance.md); geometry is verified by
> `tools/navmesh/check.py`, `navmesh_reach.py`, `navmesh_slope_check.py`.
>
> Read the rest of this document as the design rationale for what was built.

## Baseline before the rewrite (historical — verified 2026-07-23)
<a id="baseline-before-rewrite"></a>

The pre-corridor `master` was **not** the Recast pipeline — it was a **voxel /
span-graph** generator: `voxel.py` (heightfield + `stamp_pathgrid` + filters +
erosion), `region.py` (region flood + pathgrid seeding), `spanmesh.py` (mesh the
span graph directly). **All three are now deleted.** `build_navmesh`'s signature
was, and still is:

```
build_navmesh(refr_recs, base_model_by_fid, get_collision, nodes, edges,
              land_rec=None, origin_x=0.0, origin_y=0.0, budget=None, doors=None)
    -> (verts, tris)   # world-space; [] , [] on failure
```

(`budget` is now accepted only for signature compatibility — the corridor build
has no budget knob.)

There was **no `door_carve.py`** — doors were stamped into the voxel grid and
passed to `spanmesh.build_mesh(doors=door_rects)`. The Recast-era `door_carve.py`
(shapely cut-and-earcut) lives on `test-navmesh-2`; the corridor model handles
doors in `corridor_doors.py`.

That voxel pipeline was cleaner than the Recast one (pathgrid stamped first,
span-graph meshing so adjacency is structural), but still heavy: voxel grid,
filters, region flood, erosion, span meshing, steep-tri drop, flap cull, island
prune. The corridor model replaced the whole surface generator with a direct
ribbon build.

---

## Why replace it
<a id="why-replace"></a>

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

> Emit a fixed-width ribbon of triangles centered on every pathgrid edge. Edges
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
<a id="author-set-principles"></a>

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
<a id="what-stays-exactly-as"></a>

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
the door's storey Z; failing that, the nearest triangle centered on the threshold
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
<a id="phase-1-corridors-doors-links"></a>

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
- `doors`: `[(x, y, z, rot_z, is_teleport), ...]` pivot-corrected door centers
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
  - center `C(t) = lerp(A, B, t)` — **Z comes from the straight A→B line**, not
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
2. **Stamp a small threshold quad** on the door line: an oriented rect centered at
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
  `_build_door_links` attaches it. `tools/navmesh/reach.py` shows the quest
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
  (`tools/esm/esm_diff.py`).

Tools: `tools/navmesh/probe.py`, `tools/navmesh/reach.py`, `tools/navmesh/check.py`
(validate against Skyrim.esm first — it has known findings, don't chase those).

---

## Phase 2 — grow width to walls (deferred, sketch only)
<a id="phase-2-grow-width-walls"></a>

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
<a id="phase-3-polish"></a>

- Teleport-door far-side clipping (port `_interior_sign` from `test-navmesh-2`'s
  `door_carve.py`) so a teleport door does not trail ribbon into the decorative
  geometry beyond the cell shell.
- Junction mitering (Open Question B2) for cleaner intersections.
- Wider door thresholds / better-shaped Door Triangles if the stamped quad reads
  as too small in-game.

---

## Decisions made (author) and open questions
<a id="decisions-made-open-questions"></a>

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
<a id="risk-register"></a>

| Risk | Mitigation |
|---|---|
| Sparse mesh: NPCs path single-file, don't use room area | Accepted for Phase 1 (author). Phase 2 width-grow restores room coverage. |
| Pathgrid edge clips a wall → ribbon straddles wall | Accepted (principle 1); the fixed narrow width limits how far it protrudes. Phase 2 must not *widen* it through the wall. |
| Junction overlap creates non-manifold edges | `_make_manifold` (Step 5) resolves; keep the largest tris. |
| Node floats far above the floor (pit/upper storey) | `snap_down` clamps the drop to `SEED_SNAP_DOWN`; never teleports to a distant surface. |
| Door with no pathgrid edge through it → no Door Triangle → dead doorway | Step 4 skips only genuinely walled-off doors; author asserts doorways have pathgrid. Acceptance counts doors that got a Door Triangle vs. total; a shortfall is a real bug to chase. |
| Exterior sparse pathgrid → spiderweb over open terrain | Accepted for Phase 1; Phase 2 width-grow + terrain already in `walkable`. |
| Cross-cell connectivity | Unchanged — NAVI/NVMI + edge-link passes already handle it and consume `(verts,tris)`; Phase 1 only owes them border edges at the seam. |

---

## Connectivity invariant: status 2026-07-25
<a id="connectivity-invariant-status"></a>

Acceptance test is `tools/navmesh/component_audit.py` (SINGLE-PROCESS — see its
docstring): **one connected pathgrid component must produce one connected
navmesh component.** Anything more means the engine cannot make a walk the
pathgrid asserts, however good the mesh looks in the preview.

Measured over `--all --limit 60`: **18 bad (32.7%) -> 15 bad (27.3%)** after
raising `_weld_sheets`' `WELD_R` 12.0 -> 16.0. The four reference houses
(Pinarus / Arvena / ChorrolFG / AnvilFG) are all `pathgrid=1 navmesh=1`.

### Fixed: sheet weld radius (the stair-top class)

Where a stair FLIGHT meets its LANDING the two sheets both seed a vertex at the
shared pathgrid node, but at different Z — the flight's last row sits on the
ribbon CHORD, the landing's on the floor. Pinarus's stair top came out
**12.66u** apart (identical XY, pure Z gap), just outside a 12u weld, so the
house shipped as 150/148 triangles with no shared edge across the joint.

`WELD_R = 16.0` closes it. The value is bounded on both sides and must stay
there: it equals `RIBBON_GROW_MIN_HALF`, so the radius cannot span two distinct
rails, and it is far under `MAX_CLIMB` (34) so it cannot fuse a step an actor is
supposed to climb. A trial at 20.0 scored marginally better (14 bad) but eroded
triangle counts everywhere and pushed `XPGloomstonePassage02` from 16 to 17
components — a tolerance past its justification, not a fix. **Do not raise it
further to chase a component count.**

The weld must stay **3D**. Measured in plan alone, Pinarus `B v56` is 0.00u from
a main-mesh edge and **267u** below it — a different storey. A plan-only or
grid-snapped weld fuses floors.

### Remaining failures — diagnosed, NOT fixed

Diagnose with `temp/_edgecheck.py <cell>`, which reports every pathgrid edge
whose endpoints land in different navmesh components together with that edge's
slope; that slope separates the classes below. `temp/_gap2.py <cell> <ci> <cj>`
measures the closest approach between two named components.

1. **Vertical drops (NOT a geometry bug — needs an edge link).**
   `VeyondCave02` `n35->n36`: run **24.7u**, dz **308u**, slope **12.49**. The
   two components sit at the same XY (`341.5, 606.9`) 308u apart in Z — a shaft
   an NPC FALLS down. Oblivion pathgrids legitimately connect across such
   ledges. No continuous surface can represent it, and forcing triangles here
   reproduces exactly the unnavigable "fold" rejected at Pinarus's stair top.
   Skyrim's representation is a NAVM edge/portal link, not geometry. The audit
   should classify a crossing edge steeper than ~1.5 as link-only and stop
   counting it as a component violation.

2. **Real holes: the ribbon never got built (the big splits).**
   `VeyondCave02` `n47->n48`: run **329.5u**, dz 44u, slope **0.13** — nearly
   flat, so it SHOULD be one surface, yet comp1<->comp2 are **97.3u** apart at
   the closest point. A gentle ramp was severed outright; no weld radius can or
   should bridge 97u. This is the cause of the large multi-way cave splits
   (`XPAichan01` 23-24 comps, `XPGloomstonePassage02/03`, `XPMilchar02a`,
   `Elenglynn`, `SENSGreenmoteSilo`, `XPXeddefen03spire`) and the 121.86u gap in
   `XPGloomstonePassage03`. Find why the width-grow/clip drops these ribbons
   before touching tolerances again.

3. **1-2 triangle specks** (`KvatchChapelUndercroft` [419,2,2],
   `GoblinJimsCave` [1633,2], `BrumaJGhastasHouse` [415,2],
   `BramblePointCave03` [2540,2], `Piukanda02` [5294,1]).
   Vertex-only contact: the speck shares VERTEX ids with the main mesh but zero
   common EDGES, so `_drop_point_attached` / `_split_t_junctions` do not fire.
   Note `_split_t_junctions` tests candidate vertices in **3D** with `tol=2.0`;
   a speck lying on a main-mesh edge in plan but offset in Z is never split in.
   Cheapest correct fix is to DROP a component under a few triangles that has no
   shared edge, rather than to stitch it.

### The real Pinarus defect: triangles that exceed MAX_CLIMB (2026-07-25)

"One component" is NOT sufficient. Pinarus passed the component audit and was
still unnavigable in game — the upper floor hung off a single vertex through a
fan of triangles each climbing 44-54u, and the pathfinder will not traverse a
triangle whose rise exceeds `MAX_CLIMB` (34). Measure it with
`tools/navmesh/bottleneck.py`, which reports single-edge BRIDGES (a shared edge
whose removal splits the mesh) and the total shared-edge width across each Z
level. Pinarus's stair throat showed **1 shared edge / 106.7u** where every
other level had 6 edges / ~520u.

**The pathgrid was NOT the problem.** `tools/navmesh/surface_residual.py`
measures mesh_z minus real collision_z per vertex: Pinarus's upper floor is
**100% of vertices at exactly 0.00u** (flush on collision), and 86.5% of the
whole cell is within +/-2u. The hover theory is disproved for this cell —
lowering the upper floor would sink it INTO the floor. No pathgrid edge in any
of the four houses is steeper than **0.91** (Arvena's worst is 0.53), so the
input lines are all ordinary staircases.

**Cause: two thresholds in `_ribbon_seeds` were set from `STOREY_GAP_Z` when the
walkability question is `MAX_CLIMB`.**

- the steep DETECTION test was `rise/run*target_edge > STOREY_GAP_Z * 0.5` (60u),
  so any ribbon climbing 34-60u per triangle was treated as flat ground and kept
  128u triangles.
- the steep SPACING aimed at `STOREY_GAP_Z * 0.33` = **39.6u of climb per step,
  above MAX_CLIMB**. Its stated goal was only to keep a triangle under
  `STOREY_GAP_Z` so the per-surface emission would not DROP it; whether an actor
  could walk it was never considered.

Both now key off `MAX_CLIMB` (detection at `> MAX_CLIMB`, spacing at
`MAX_CLIMB * 0.6`). On Pinarus's 515u/264u stair that is **13 segments at 20.56u
climb** instead of 6 at 44.55u. Over-climb triangles per cell:

| cell | before | after |
|---|---|---|
| Pinarus | 33 | **17** |
| Arvena | 34 | **15** |
| ChorrolFG | 65 | **45** |
| AnvilFG | 20 | 28 |

Ramp fidelity is untouched (slopes identical to HEAD, `ramp_miss=0/38`), the
component invariant is unchanged over `--all --limit 60` (15 bad, same as the
weld fix alone), Chorrol's Z-seam went 1 -> 0, and the 28 targeted tests pass.

**STILL OPEN — the remaining over-climb triangles, and Pinarus's sliver joint.**
The joint is still a fan around ONE vertex (v14, z=68.6): `edge(14,116)` spans
z 68.6 -> 15.1. The seeding fix demonstrably reaches the area (a new intermediate
vertex appears at z=48.0, dz=-20.6) but the triangulator still draws the long
diagonal PAST those seeds, so the top step remains 41-53u. Suspect the Poisson
keepout in `_triangulate` (`min_dist=target_edge*0.6`, `keepout2`) thinning the
fine stair seeds, and/or the plan-space Delaunay preferring the long diagonal
because the stair polygon is a narrow band in plan. Fixing this needs work in
`_triangulate`, not another threshold.

**DO NOT "fix" this by dropping over-climb triangles.** Measured: dropping every
triangle spanning > MAX_CLIMB shatters ChorrolFG into `[153,124,123,123,12]` and
Pinarus into `[128,107,57]`. Those triangles ARE the only floor-to-floor
connection — they must be SUBDIVIDED into walkable steps, never removed.

---

## The triangle-quality contract (2026-08-04)
<a id="triangle-quality-contract"></a>

The author's explicit brief: **triangles MUST be close to equilateral** — the
long side no more than 2x the short side, plus a hard MINIMUM triangle area,
with the sawtooth/decimation machinery existing precisely so the mesh can be
broken into LARGE well-formed triangles and the little bits around the outside
simply removed.

**Shape metric — `corridor_clean._badness`.** Edge ratio alone cannot see a
CAP (obtuse, near-zero height, all edges comparable — visually the worst
sliver there is).  Badness = max(edge_ratio / MAX_EDGE_RATIO (2.0),
aspect / MAX_TRI_ASPECT (2.5)) with aspect = longest^2/(4*area); 1.0 is the
contract boundary.  Every cleanup pass (collapse bound, flip objective, cull
candidacy, split candidacy) uses this one metric.

**The passes, in order** (decimate -> cull -> decimate -> cull inside
`finalize`, budget split 100%/40%):

* collapses (edges < DECIMATE_MIN_EDGE 64) with link-condition +
  outline/sawtooth rules; a collapse may not push shape past the contract;
* Lawson flips (`_flip_pass`), duplicate-edge-guarded;
* long-edge bisection (`_split_needles`): a needle whose edges are all LONG
  can be fixed by neither collapse nor flip — bisect its longest edge at the
  apex projection, only when both halves beat the parent's badness (a naive
  midpoint bisect minted two r=11 slivers where one r=4 stood);
* boundary sliver cull: badness > 1 and area < 3000, or area < MIN_TRI_AREA
  (1000 — a Skyrim actor's footprint; vanilla door triangles bottom out at
  992).

**The walkability contract (added same day, author's rule).** Connectivity is
not the metric — CHOKEPOINTS are: two areas joined by a strip narrower than
half a doorway (~48u) are UNWALKABLE for NPCs.  Enforcement in the cull:

* a candidate whose pathgrid samples remain covered by neighbours may go
  (sole-cover slivers never go; replacement cover must be within a STEP of
  the sample's own z — an 80u window let a stacked cave ledge below count);
* `_narrows_corridor`: pin_xy samples carry the line DIRECTION; the cull
  measures the corridor's live cross-width at any nearby sample and refuses
  the cull when it is already under 56u.  Wide-room fringe still culls.
* pathgrid NODES are pinned (DECIMATE_PIN_NODE_RADIUS 24): outline collapses
  and culls had no node awareness and shaved the boundary across junctions
  (single-sample holes exactly at nodes in ImperialDungeon01/BarrenCave).

**Doors are never walls.** A door is a thing an actor OPENS: vanilla navmesh
runs under every door.  `gather_cell_geometry(skip_bases=door bases)` keeps a
door ref's placed FLAT faces (a trapdoor/platform door IS the floor — the
ImperialDungeon01 nodes 243-248 junction stands on one, and gates are
authored upright then laid flat by rotation, so the local-space class cannot
be trusted) and drops everything steep (the panel).  Measured defect: the
Pinarus upstairs animated door's at-rest panel sits 47u from its threshold
ACROSS the passage and pinched the doorway to nothing.

**Flat surfaces over flights.**  Three mechanisms keep a FLAT surface (node
disc, door quad) from hanging mesh over a staircase:

* disc RAY TRIM at stair nodes (`DISC_RAY_TRIM`): the march stops at walls
  and sudden drops but happily follows a RAMP down a legal step per station;
  the trim walks the real surface and stops the ray where it has left the
  node's level by more than a step in total;
* `_clip_flat_poly_off_level`: discs and door quads give up the parts of a
  steep ribbon's footprint that are off their level — but ONLY intervals
  contiguous with a mouth station INSIDE the polygon (anchoring).  |dz| alone
  cannot tell "my own flight ramping away" from "another storey's flight
  passing under me in plan": the unanchored version opened 37 walked-line
  holes on ChorrolFightersGuild's mid floors;
* door quads are RAMPS, not shelves: `door_footprints` probes the corridor
  mesh under the quad's far edge (`z_far`) and the strip slopes to meet it,
  clamped to slope 0.5 (the probe's storey-scale tolerance could grab the
  WRONG floor and paint a 45-degree cliff across a corridor — Moranda02).

**The crack zipper.** Two emissions of a flight can meet along a zero-area
lens: coincident in plan, 3-8u apart in z — no shared edge, so the engine
cannot path across, and it renders as a hairline hole ON the staircase (the
ImperialDungeon01 "holes in the highest stairs").  `_split_t_junctions` seals
them: hits project in PLAN with a separate z window (TSPLIT_Z_TOL 12 — a full
MAX_CLIMB window grabbed genuine fold vertices and minted 18 overlaps), a hit
that is itself a BOUNDARY vertex may be up to TSPLIT_CRACK_TOL 6u off the
edge (both sides of a crack are boundary; an interior vertex that close is
dense healthy mesh and keeps the 2u radius), and a hit is refused when the
fan's new edges would give any edge a 3rd owner (_make_manifold would rip the
extras and delete real corridor — measured 3-sample losses in two cells).

**Repair-pass ordering.** `_split_t_junctions` re-runs after the last
vertex-moving pass (merge/stitch): a hanging node minted late reads as
point-attached and `_drop_point_attached` deletes REAL coverage (the
ImperialDungeon01 prison junction triangle).  Plan-degenerate triangles are
culled by `_drop_degenerate_guarded` (never disconnecting; load-bearing
degenerate connectors survive) — in `finalize` AND once more after
`attach_door_triangles`, which mints seam slivers of its own.

**Measured state (in-process harness vs the prior user-approved build)**:
badness p90 1.15-1.6 vs 1.4-2.1; contract violations down 6-11 points per
cell; sub-1000u^2 triangles roughly halved; walked-line coverage and
chokepoints at parity (residual: 1-3 single 16u samples per cave cell and
+1 choke edge on two cells, all borderline z-drift on jagged cave floors).
Verify with `temp/sweep.py` / `temp/esm_shape_cmp.py` (miss / choke / ovl /
badness per cell, current build vs the ESM on disk).
long side no more than ~2x the short side, plus a minimum triangle area — with
sawtooth outlines simplified inward and the leftover "little bits around the
outside simply removed."  Implemented as a pipeline of guarded passes; every
one preserves the two hard invariants (no overlapping same-surface triangles,
no disconnection the pathgrid contradicts).

### Where the shape comes from

1. **Interior hex lattice** (`corridor_union._hex_refine`).  GEOS's
   constrained Delaunay uses only the polygon's own vertices, so any region
   wider than one triangle triangulates as a fan of slivers — no post-collapse
   can fix that, because the vertices to break the fans do not exist.  A hex
   lattice at `TRI_TARGET_EDGE` spacing is inserted point-by-point into the
   CDT (containing-triangle 3-fan split; each point kept 0.45×spacing clear of
   existing vertices and of the boundary), then `_flip2d` restores local
   shape.  Lattice anchored on the part's own bounds — deterministic.
2. **Ratio-improving diagonal flips** (`corridor_union._flip2d` in 2D at
   triangulation time, `corridor_clean._flip_pass` in 3D during decimation).
   A flip moves no vertex, so outline and coverage cannot change.  Guards:
   strict ratio improvement, quad convexity via signed areas, z-span of the
   new diagonal, door triangles (all corners pinned) untouched, and — learned
   the hard way — **never flip onto a diagonal that already exists as an edge
   elsewhere**: folded storeys reuse vertices, the duplicate edge is
   non-manifold, and `_make_manifold` later rips whole regions out.
3. **Decimation shape ceiling** `MAX_EDGE_RATIO = 2.0`: no collapse may push
   any triangle past the 2× contract (or past the worst ratio already
   present).
4. **Sawtooth cuts** (decimate boundary rule): a *convex* outline vertex —
   one whose removal can only SHRINK the mesh — may be cut with deviation up
   to `DECIMATE_SAWTOOTH_DEV` (32u), budgeted by `DECIMATE_MAX_AREA_LOSS`
   (10%).  Concave vertices never move (their removal would extend the mesh
   outward, i.e. through a wall).  Exterior-seam vertices only ever collapse
   collinearly, so cross-cell stitching is untouched.
5. **Peripheral sliver cull** (`corridor_clean.cull_boundary_slivers`): a
   boundary triangle with ratio > `CULL_SLIVER_RATIO` and area <
   `CULL_SLIVER_MAX_AREA`, or below `MIN_TRI_AREA`, is removed outright —
   unless it touches a door pin, contains a pathgrid sample, lies on the
   cell seam, or its neighbours would lose each other (bounded BFS).
6. **Door pins are TIGHT** (`DECIMATE_PIN_RADIUS` 8u around the wedge ring
   points, 24u around door centers).  The old 80u blanket froze every sliver
   near a doorway beyond repair (area-3 MICRO triangles parked forever).

Measured on the reference cells: edge-ratio p50 1.56–1.85, p90 2.3–3.1;
needles (>3.0) 3–12% (they are the protected minority: pathgrid-carrying
strips, connectivity bridges, genuinely thin corridors).

### The overlap/connectivity repairs that made it safe

The quality passes exposed a series of latent defects; the fixes are load
bearing and each encodes a measured failure:

* **Same-emission weld = provisional, checked, reverted** (`_weld_sheets`):
  a sideways weld that creates any overlap is undone (an outright ban broke
  ImperialDungeon05's connectivity; the unchecked weld created Pinarus's
  stair-bottom overlaps).
* **Junction strips are clipped to the junction disc** (`_clip_strip_near`),
  and the clip measures from the NODE's projection, not the segment end
  (stair ribbons extend 48u past their nodes).  Handing over the whole strip
  leaked its heights across everything it passes under → phantom duplicate
  floors.
* **Steep edges carry a tread-following height profile**
  (`corridor._surface_profile`, mirrored natively in `grow.cpp
  py_levels_at`): a DP over walkable collision layers along the line,
  constrained to start/end at the node heights.  The end constraint is what
  selects the treads over the floor that continues under the flight.
* **`_merge_at_pathgrid_nodes` welds ONE closest cross-component pair per
  junction, capped at `RIBBON_HALF_WIDTH`**, in a disc widened by one ribbon
  width, and runs BEFORE the stitch.  The old whole-band weld deleted every
  triangle that fit inside the node disc (lattice-sized triangles all do).
* **`_stitch_shared_nodes`** fuses coincident vertices each round (post-weld
  passes mint identical positions under different indices), bridges with an
  overlap guard (relaxed to a 250u² sliver tolerance only for junctions
  nothing else could join), and slope-based dz guards (a bridge may climb
  with its plan run; only height without run is a wall).  It is re-run at
  the END of `finalize` — decimation can land two components' vertices on
  the same position, invisible to everything upstream.
* **`_destack`**: same-surface stacked duplicates (two triangles covering
  the same plan area within 40u of height) that survive the claim are
  removed, smaller first, connectivity-guarded.
* **`_drop_walls`**: triangles steeper than `WALL_SLOPE_COS` (55°) removed
  when their neighbours stay connected without them — never at emission
  time, which tore caves apart.
* **Decimation topology guards**: the standard link condition, plus
  boundary-pair collapses only along an OUTLINE edge (collapsing across a
  thin neck pinches the sheet and sheds vertex-attached scraps).

State on the 10 reference cells (2026-08-04): component invariant 9/10 OK
(Moranda02 at 2 components — its historical defect, previously 3–4 — with an
85u genuine hole in one tunnel), overlaps 0 everywhere except two mutually
load-bearing bridge pairs in a BarrenCave throat.

### XXXX-oversized NVNMs and the edge linker (2026-08-04)

The lattice pushed ~119 exterior meshes past the 65,535-byte subrecord limit,
so their NVNMs are written under the XXXX size-override protocol.
`navm_edge_links._extract_nvnm` did not speak XXXX and read those NVNMs as
EMPTY — every oversized mesh silently skipped cross-cell edge linking (the
"6504 → 6385 exterior navmeshes" drop in the build log; the meshes themselves
were present and fine).  The walker now honours XXXX; `pack_subrecord`
re-emits it on write.  Any new consumer that walks raw NAVM subrecords MUST
handle XXXX — `navm_split._decode_record` and `navi_builder` already do.

Known pre-existing gap (not from this work): 53 exterior cells whose pathgrid
is a single node with only PGRI (cross-seam) links get no navmesh job at all
(`_gather_navm_jobs` requires in-cell edges); `build_navmesh` produces a valid
ribbon for them when invoked directly, so the fix is to gate jobs on
"edges OR PGRI links".

## <a id="ledge-links"></a>Ledge links: both sides, or neither

**Code:** `tes5_import/navmesh/from_pgrd.py` (`_resolve_ledge_links`)

A DROP-DOWN between disconnected storeys is Skyrim's own mechanism for
stepping off a ledge — vanilla Skyrim.esm carries 476 Ledge Down / 467 Ledge Up
across 3,000 navmeshes. Bridging the gap with triangles instead makes actors
walk on air across the lip.

On each linked triangle the link goes on the edge that has no neighbour and
faces the other side; vanilla marks it with the per-edge link bit
(0x0801/2/4).

**A pair is committed only if BOTH directions resolve an open edge.**
`_open_edge_towards` is evaluated independently per side, and slightly
asymmetric geometry can make one side find a facing open edge while the other
does not. A one-sided link is what the CK's own loader flags as *"Bad portal
navmesh ID/triangle index … the cell needs to be refinalized"*.

Measured: exactly **1 of 234,612** vanilla-verified portal links in a full
Oblivion.esm conversion came out one-sided this way, in FortRayles. Skipping
the half-resolved pair leaves that lip un-linked — no fall-through — instead of
writing a reference the engine's own validator rejects.

## <a id="navm-formid-preallocation"></a>NAVM FormID pre-allocation

**Code:** `tes5_import/import_main.py` (`_assign_navm_formids`)

Navmesh ids derive from the navmesh's SOURCE (cell + pathgrid), never from the
order jobs happen to be gathered in, so the parallel and serial paths agree.

They are **save-persisted**: measured, 564 NAVM ids appear in a real save's
FormID array, carrying obstacle and door pathing state. Moving one breaks
saves.

The master-index offset returned alongside is a `text_reader` module global set
once in the parent process. Spawned workers start at 0, so it is captured here
and replayed in each child's init; without it their `get_formid()` calls
mis-map every PathingCell parent FormID, which the engine meets as a
navmesh-load null deref.

## <a id="cellview-progress"></a>Cellview: a progress bar with measured weights

**Code:** `tools/cellview/progress.py`, polled by `GET /progress`.

Opening a cell is not a short wait, so an indeterminate spinner cannot tell
"working" from "hung". Measured on `Oblivion.esm` / `ImperialDungeon01`, a cold
open costs **43.9s**, split:

| stage | seconds | share |
|---|---|---|
| load the export index | 21.6 | 49% |
| arm collision + resolve cell | 15.5 | 35% |
| generate the navmesh | 6.7 | 15% |
| gather collision, name sources | 0.11 | <1% |

The weights in `STAGES` are those shares, so the bar tracks elapsed time
instead of jumping 0 -> 90 -> done. Gathering collision is effectively free
once the cache is armed (0.01s for 13,862 triangles) and is folded into its
neighbour rather than given a visible stage of its own.

Progress is per JOB, keyed by an id the page generates, because two tabs on one
server would otherwise overwrite each other's state. `read` reports completed
stage weights plus the fraction of the current stage, so it never rewinds.

Two details the measurement forced:

- **The server is `ThreadingHTTPServer`.** A bake holds its handler for a
  minute or more, and the single-threaded default could not answer the
  `/progress` poll that is meant to be tracking it. Verified: 34 polls
  answered during one 75s bake, advancing 0 -> 49 -> 85%.
- **`creep` advances within a stage on a time estimate.** Unpickling a 2 GB
  index is one opaque call with no checkpoints, so the bar sat at a dead 0%
  for 42s -- which reads as a hang. It now approaches the stage boundary
  asymptotically and snaps to the true value when the stage ends.

## <a id="cellview-cell-index"></a>The per-cell index (`cell_index.sqlite`)

**Code:** `tools/navmesh/cell_index.py`, built by `audit.cell_index`.

`audit_index3.pkl` was one pickle of the whole plugin. Measured on
`TR_Mainland.esm` (1098 MB): `land_by_cell` 628 MB, `refr_by_cell` 378 MB
(**1,598,079 REFRs**), `pgrd_by_cell` 80 MB, everything else 7 MB. Reading the
bytes cost 0.6s; the remaining ~5s warm (**~60s COLD**, page-faulting a 1.1 GB
allocation) was Python rebuilding 1.6M dicts. Opening one cell paid for all
21,078.

Every heavy table is keyed by cell FormID and only ever read as `.get(fid)`, so
it stores as one row per cell in SQLite.

**A child plugin does not copy its masters -- it reads them.** The first
attempt merged the masters' records into the child's own index, and
Morrowind_ob went from 379 MB to **2400 MB, 1767 MB of it duplicated LAND**.
Each plugin now indexes only what it OWNS, and a lookup walks the chain
(`TR_Mainland -> Morrowind_ob -> Oblivion -> ...`, resolved recursively) until
one answers. The child's own record wins. Measured after: Morrowind_ob **383 MB**,
TR_Mainland **725 MB**.

Everything is deferred, because each of these was separately fatal to open time
on a four-master chain:

| what | eager | lazy |
|---|---|---|
| open the chain | 40.1s | **0.03s** |
| `resolve_cell` (coordinates) | 28.9s | **0.85s** |
| merge 61,181 CELL records | (at open) | 0.09s, on first `.cells` |

- **Masters open on first need**, not at construction.
- **`base_model` / `door_fids` / `cells` are properties**, merged across the
  chain on first access. A caller that only wants one cell's geometry never
  triggers it; `NavIndex` defers its EditorID/FormID lookup tables the same way.
- **`ensure_index` opens the index instead of calling `build_index`**, which
  would reassemble every REFR in the chain to answer one cell. This was the
  entire remaining 28s.

`build_index` still returns all six tables for the batch tools (`audit.py
--interiors`, `sweep.py`, `render.py`, `cell_check.py`); reassembly measured
4.70s against the 5.81s pickle, so they lose nothing.

**One connection PER THREAD.** sqlite3 refuses a connection used from a thread
that did not create it, and the server is threaded (for the progress poll), so
a cached index outlives the request that opened it -- the second request on a
different thread raised `ProgrammingError`. `_Store` keeps its connection in a
`threading.local`, which is safe because the index is read-only once built.

**`pathgrid_fids` reads an indexed column**, not every cell's pickle. The cell
search box asks which cells have a pathgrid, and unpickling the chain to answer
measured 10.8s; the stored `has_pgrd` flag answers in 0.1s.

A single file, not ~21k loose shards: 21k files per plugin is hostile to
Windows directory listing and to any future zip of the export. The build writes
to a temp name and renames, so an interrupted build cannot leave a partial index
that reads as complete. `SCHEMA` bumps force a rebuild rather than serving a
stale shape.

## <a id="cellview-open-is-cached"></a>Cellview: why opening a cell was slow

**Code:** `tools/navmesh/audit.py` (`_TABLES`), `tools/cellview/bake.py`.

The generator was never the problem. Profiled on `TR_Mainland.esm`
`wrldmorrowind -17 -51`, before -> after:

| step | before | after |
|---|---|---|
| `build_index` (warm) | 6.17s | **0.00s** |
| `resolve_cell` (warm) | 7.73s | **0.00s** |
| `grid_of` | 8.62s | **0.01s** |
| `worlds()` | 5.51s | **~0s** |
| `src.build()` (real work) | 1.08s | 0.56s |

Three separate re-reads, all ours:

- **`build_index` had no in-process memo.** It re-unpickled the whole index
  from disk on EVERY call -- and `index_of` called it per request, after
  `index_for` had already cached a `NavIndex` holding those same tables.
  `_TABLES` keyed on the normalized export path fixes it.
- **`index_of` re-ran the existence check every call.** `_READY` records the
  plugins this process already ensured.
- **`worlds()` loaded the full master export** (476k records, 5.5s) to read
  six WRLD records. The masters' own `WRLD.txt` is 2.3 KB, so it reads those
  directly.

What remains on a cold open is the one unavoidable unpickle (~5s) plus the
navmesh generation itself. Everything after the first cell of a plugin is
effectively instant.

## <a id="cellview-edge-links"></a>Cellview: showing cross-cell edge links

**Code:** `tools/cellview/seams.py`.

An exterior navmesh with no links to its neighbours is an island: actors path
inside the cell and can never leave it (the failure documented under
[edge links](#edge-links)). In a single-cell view a missing link looks exactly
like a correct one, so the editor draws the seam itself -- matched border edges
in green, dangling ones in red, per side.

It reuses production's own `border_edges` and `match_seam` (promoted from
`_`-private for this; `edge_links.py` is excluded from the geometry cache tag,
so touching it invalidates nobody's cache). A second seam predicate here would
be free to disagree with the writer, which is the one thing this view must not
do.

The generator emits corner indices only, while `border_edges` needs the
neighbour columns to tell a border edge from an interior one, so `seams.py`
derives them from shared edges and presents the same row shape `NavMeshView`
exposes.

**Seams load on their own request, after the cell is on screen.** Each of the
four neighbours costs a full navmesh generation: measured on `Tamriel 4 12`,
44s for the cell alone against 178s with its seams folded in. `GET /seams` is
therefore separate from `GET /mesh`, and neighbour geometry is cached per
session -- walking a worldspace re-uses what the last cell already built.

Since those neighbour meshes are generated anyway, they ship with the report
and draw dimmed behind the cell: a dangling edge is only legible against the
mesh it was meant to meet. Measured on that cell, all four neighbours resolve
(355/893/809/1022 triangles) and the seam pairing is 19 matched against 31
dangling.

## <a id="navmeshview-decode-is-vectorised"></a>NavMeshView decode: why numpy, why float64

**Code:** `tes5_import/navmesh/edge_links.py` (`NavMeshView.__init__`, `pack`).

The per-element `struct.unpack_from` loops this replaces were **39% of the whole
edge-link pass** (15.3M calls over 6.5k meshes).

`verts`/`tris` stay NUMPY ARRAYS rather than being converted back to Python
lists: `add_link` mutates a triangle row in place, so a list form would have to
be re-converted with `np.asarray` on every seam scan -- measured, 103k asarray
calls and 11.9s, more than the decode it was meant to save. Arrays are mutated
directly and `border_edges` reads them with no conversion at all.

`verts` are float64 for the same reason the seam midpoints are: the original
scalar code did that arithmetic on Python floats (doubles), and computing it in
float32 shifts midpoints enough to reorder near-ties in the greedy pairing
(measured: 4 extra links).

The triangle record is 6 signed + 2 unsigned shorts. Reading it as int16 would
make the two trailing unsigned fields negative, so it widens to int32 and
restores the sign of the last two columns only. `pack` is byte-for-byte
identical to the per-element `struct.pack` loop it replaces.

## <a id="renderer-colour-contract"></a>The renderer as an instrument (`tools/navmesh/render.py`, `draw.py`)

**Code:** `tools/navmesh/draw.py` (layers), `tools/navmesh/render.py` (CLI).

Every navmesh judgement that is not a number is made by looking at a render, so
the picture has to be unambiguous. Four measurements drove the current design;
each one names the defect it fixes.

**Colour is a contract: red / orange / yellow mean a MESH DEFECT and nothing
else.** Before the split, blocking collision drew at `(190, 50, 45)` and a
`badness > 2` triangle at `(220, 40, 40)` — two reds a few units apart, so a wall
and a sliver were indistinguishable at a glance. Collision now draws in cool
blue-grey (blocking) and neutral grey (walkable), and the authored-navmesh
underlay reuses the same cool palette. An authored mesh must never be scored in
the defect palette: authored navmeshes fail our own `badness > 1` contract
40.8% (Bruma) to 51.8% (vanilla Skyrim) of the time, against our 18.5%, so
colouring them by that contract would paint the answer key as broken.

**Collision is clipped to the mesh's own Z slab.** Measured on
BrumaCastleGreatHall: the generated mesh spans z −575..−408 while blocking
collision spans z −590..214 — 800 units, the ceiling and roof included, drawn
straight over the floor plan. Clipping to
`floor − MAX_CLIMB .. ceiling + AGENT_HEIGHT` drops **29%** of blocking
triangles on the Great Hall (4,923 → 3,488) and **15%** on BrumaChapelHall
(3,685 → 3,125), all of it roof. The window is deliberately generous at both
ends: a step the generator must see sits below the floor, and a lintel an actor
must duck under sits above head height, so a tighter clip would hide geometry
the mesh is judged against.

**Opacity, not geometry, was what buried the plan.** Blocking is already **84%
near-vertical wall and only 1% horizontal**, so filtering by normal would buy
almost nothing — but 4,129 wall triangles at alpha 90 stack into a solid slab.
Blocking now draws at a very low fill alpha with a brighter rim, so the rim
carries the shape and overlapping walls stay individually readable.

**Both matched test cells are single-storey** (one band each), but they carry
real relief — the Great Hall spans 167u — and a flat fill hides every ramp,
fold and step. `--z-shade` maps triangle centroid Z to lightness within the
triangle's own defect hue, so height reads without costing a defect colour.
The lightness ramp is deliberately wide (30%–120% of the base colour): interior
storeys are shallow, and a gentler ramp across 167u renders as flat colour.
`--bands` prints the storey clusters (gaps > `STOREY_GAP_Z`) so `--z` is chosen
from the cell's data rather than guessed.

The remaining chrome exists because the renderer feeds a hand-transplant of
pathgrids onto Bruma's geometry: a labelled game-unit grid and a scale bar (so a
node's target coordinate can be read off the image), `--node-ids` (`--ids`
labels triangles; moving a node requires naming it), `--path-alpha` (at Great
Hall density the pathgrid buries the mesh), and a legend so a render is
self-describing.

## <a id="authored-navmesh-corpus"></a>The authored-navmesh corpus (`tools/navmesh/transplant.py`)

**Code:** `tools/navmesh/transplant.py`, `tools/navmesh/authored.py`.

Every previous attempt to improve the navmesh failed the same way: we had no
ground truth. The inputs were the pathgrid and a set of "is it broken"
invariants (miss / crack / choke / orphan), so *better* was unmeasurable and
each change traded one invariant for another. Swapping in Shewchuk's Triangle,
for instance, improved the shape contract (`bad>1` 17%→4%) while destroying
coverage (`miss` 0→570 on AnvilFightersGuild, 0→5599 on ImperialDungeon01).

Beyond Skyrim: Bruma rebuilt Cyrodiil's architecture in Skyrim and navmeshed it
**by hand**, so a Bruma interior replicating an Oblivion one is an answer key.
Seven cells make up the corpus, each with a real Oblivion pathgrid and an
authored Bruma navmesh (Bruma prefixes cell EditorIDs with `CYR`):

| corpus cell | Oblivion source | OB nodes | authored tris | ours |
|---|---|---|---|---|
| BrumaCastleGreatHall | same | 201 | 427 | 999 |
| BrumaCastleLordsManor | same | 163 | 300 | |
| BrumaCastleDungeon | same | 88 | 208 | 408 |
| BrumaChapelUndercroft | same | 43 | 265 | |
| BrumaChapelHall | same | 43 | 239 | 261 |
| BrumaCathedralofStMartin | BrumaChapel | 73 | 373 | |
| BrumaCastleBarracks | same | 55 | 105 | 184 |

**A corpus cell need not share its name with its Oblivion source.** The
Cathedral of St. Martin is the same building as Oblivion's `BrumaChapel`, so
its entry sets `source_cell` explicitly; the rigid fit lands 70 of 73 nodes on
the authored mesh (96%), the best of the corpus.

`BrumaCastleServiceHall` was REMOVED: despite the matching name it is a
different building, and the fit left 32 of 52 nodes off-mesh with 65 of 84
edges crossing void. A name match is not a building match — check the fit score
before trusting one.

### Why the grid must be transplanted, not reused

The two frames are unrelated: BrumaChapelHall's authored navmesh spans
x 16..1732, y −816..823, while our generated mesh for the same-named Oblivion
cell spans x −411..1366, y −1337..799. `fit` recovers the rigid placement — one
of the four right-angle rotations plus a translation, hill-climbed on a coarse
occupancy score — which is a **starting position, not the answer**. Bruma moved
statics, so nodes still land in walls and are nudged by hand afterwards.

### The leak rule

**Node positions are never derived from the authored navmesh.** A pathgrid
shaped by the answer would flatter the generator — the test would leak its
answer into its input, which is what makes auto-derived grids worthless here.
The fit consults the authored mesh only to place the grid as a rigid body;
every subsequent edit is a human judgement about where people walk, made
against Bruma's *collision*, and the result must look like something an
Oblivion author would have drawn for that room: sparse, hand-placed, running
along the traffic lines — not a lattice fitted to the authored triangles.

The corpus stores that judgement: `fit` (rotation, offset, score), `moved`
(per-node final coordinates) and `dropped` (nodes Bruma's layout removed). A
refit deliberately does **not** re-derive the overrides — moving the room
invalidates a node placed against the old walls, so each has to be re-examined.

## <a id="bruma-collision"></a>Bruma collision for the transplant editor

**Code:** `tools/navmesh/bruma_collision.py`.

Placing transplanted pathgrid nodes against the authored navmesh outline would
leak the answer into the input (see
[the corpus](#authored-navmesh-corpus)). Nodes must be placed against Bruma's
**actual walls**, which means the same Havok collision soup the Oblivion-side
renders use: every REFR in the cell, its base model's collision triangles,
transformed by the ref's full rotation, scale and position — reusing
`world.rot_matrix` / `world.place` so the editor cannot drift from production.

### Where Bruma's meshes actually live

Not where the filenames suggest. Measured folder-table walks of all four
archives:

| archive | folders | mesh entries |
|---|---|---|
| `BSHeartland - Textures.bsa` | 347 | **8,197** |
| `BSAssets - Textures.bsa` | 260 | **1,549** |
| `BSHeartland.bsa` | 111 | 0 (sound/voice/scripts) |
| `BSAssets.bsa` | 14 | 0 |

The two "Textures" archives carry the meshes; the two plain ones do not. They
live in the Vortex staging tree (`stagingPath` in
`Data/vortex.deployment.json`), not under `Data/` — Bruma's meshes are never
deployed loose, so a `Data\meshes\bscyrodiil` walk finds only LOD.

Bruma cells also place **vanilla** Skyrim statics (`Clutter\Barrel01.NIF` and
friends), so a model missing from both Bruma archives falls back to
`skyrim_assets.get_asset_bytes`, which resolves from the SSE BSAs.

### <a id="vanilla-bases-need-the-master"></a>Vanilla BASES need the master, not just vanilla assets

Resolving vanilla *assets* is not enough: the REFRs that place them point at
base records living in **Skyrim.esm**, and a scan of `BSHeartland.esm` alone
resolves none of them. This is [master-export
blindness](../../CLAUDE.md#master-blindness) in the editor's own loader.

Measured on `CYRBrumaChapelHall`: 300 REFRs over **174 distinct bases, 105 of
them vanilla — and 0 of those 105 resolved.** Only 72 of 300 REFRs got a model.
The missing objects are exactly the furniture a room is made of — beds
(`NobleBedSingle01`), tables, benches, chairs — so the renderer showed open
floor where the navmesh correctly stopped at something solid, which is what
"unexplained gaps" in the chapel actually were.

`cell_refrs` therefore loads base models from Skyrim.esm FIRST, then overlays
the plugin's own so Bruma's overrides win. Skyrim.esm is read from the live SSE
install, which CLAUDE.md permits for Papyrus logs and Skyrim.esm specifically.
A vanilla base carries a `00` master index here only because Bruma lists
Skyrim.esm first; never assume that index across other plugins.

## <a id="transplant-editor"></a>Cellview: the whole-cell editor

**Code:** `tools/cellview/` (`server.py`, `bake.py`, `corpus.py`, `plugins.py`,
`static/`).

```bash
python tools/cellview/server.py
```

Named `transplant_editor` while its only job was hand-fitting Bruma pathgrids.
It is now a whole-cell preview and editor; the transplant corpus is one feature
inside it, reached from a secondary panel rather than the main toolbar, and the
mesh editor is the default view.

### <a id="cellview-index-on-demand"></a>Any cell of any export

A plugin used to appear only if `export/<p>/audit_index3.pkl` already existed,
which was true of three exports out of the fourteen that have a `CELL.txt`.
The limit was never a whitelist — it was a missing artifact.

`tools/cellview/plugins.py` lists every export with a `CELL.txt` and builds the
index on first open (`audit.build_index`, the same builder `audit.py` uses; a
second one would drift from the pickle's six-tuple shape).

**Collision is a precondition, not a nicety.** `ce.load_collision` returns 0 for
a missing cache instead of raising, so a cell of a plugin with no
`collision_cache.bin` used to open with a pathgrid, no walls and no floor, and
looked merely broken. `preconditions()` refuses those cells by name and says
which stage to run. Measured at the time of writing: 5 of 14 exports have both
`collision_cache.bin` and `meshes/`.

The test is `collision_extract.collision_cache_is_current`, not
`os.path.isfile`: existence is not usability. Measured while building this --
Morrowind.esm's cache is a 0-byte placeholder, and Nehrim.esm (`TESCOL06`) and
FalloutNV.esm (`TESCOL05`) predate the current `TESCOL07` magic. All three load
as ZERO entries, so those cells drew no walls at all under the old editor and
looked like a generation bug. They now say so and name the stage to re-run.

Cache paths resolve through `output_layout.assets_for`, so an imported mod
nested as `export/<Mod>/<plugin>/` finds its assets one level up.

### <a id="cellview-exterior-coordinates"></a>Opening an exterior cell

Measured on Oblivion.esm: 33,639 of 35,494 cells are exterior, and **not one
of them has an EditorID**. The cell search box matches EditorIDs, so until now
an exterior cell simply could not be opened -- the overwhelming majority of the
plugin was unreachable.

An exterior cell is named the way the engine names it, by worldspace and grid:

```
wrldmorrowind -17 -51
Tamriel 4 12
```

The worldspace is matched by EditorID, case-insensitively, and the numbers are
the cell's own `XCLC.X`/`XCLC.Y` exactly as authored -- no scaling, which
matters because a Morrowind-derived worldspace does not share Oblivion's cell
size. A comma between them is accepted, since the CK prints them that way.

The name-to-FormID map is read from `WRLD.txt` (84 records, 0.01s) rather than
from the cached index: an exterior CELL record carries only a `ParentWRLD`
FormID, and adding a seventh table to `audit_index3.pkl` would invalidate every
index already on disk, including Oblivion's 2.1 GB one.

A miss reports the worldspace's actual grid extent, because the common cause of
one is a coordinate outside it. The grid is indexed once per worldspace: the
original linear scan measured 8.5s per lookup on a 35k-cell export, and a miss
is exactly what a mistyped coordinate produces.

### <a id="cellview-master-owned-cells"></a>Master-owned worldspaces and cells

**A child plugin need not own the worldspace it builds in.** Measured:
`TR_Mainland.esm` has ZERO WRLD records of its own, and every one of its
exteriors sits in `wrldmorrowind`, owned by `Morrowind_ob.esm`. Reading only
the plugin's own `WRLD.txt` resolves nothing, and its cells and their collision
read as absent -- the master-export blindness CLAUDE.md warns about.

Cellview therefore reads worldspaces and cells from `load_master_export` first
and lets the plugin's own records override by FormID, the same order
`navmesh/pool.py:_merge_master_cell_records` uses. `load_master_export` re-keys
each master id into THIS plugin's index space, which is what makes a
master-owned FormID comparable to one of the plugin's own.

Three places had to learn this, and each was separately fatal:

- `audit.build_index` now merges the masters' records into `by_type`
  (`_merge_masters`) before indexing base models, doors and LAND.
- `NavIndex.arm` passes `collision_caches()` -- masters first -- to
  `ce.load_collision`, which already took a LIST for exactly this reason.
- `plugins.export_dir` resolves through `output_layout.record_dir`, because an
  imported mod's plugin lives at `export/<Mod>/<plugin>/`, not `export/<plugin>/`.

Measured on `TR_Mainland.esm` cell `wrldmorrowind -17 -52`, before -> after:
base models 3,723 -> 34,007; collision cache entries 95 -> 29,163; REFRs whose
base model resolves 0/27 -> 27/27; collision triangles 0 -> 316 walkable +
2,999 blocking. Before the fix the cell drew nothing at all.

A cell with no pathgrid generates no navmesh, which is not an error -- the
camera frames the COLLISION in that case, and the mode badge says
`NO PATHGRID`, rather than leaving an empty viewport unexplained.

Hand-placing nodes by reading coordinates off a rendered grid and issuing
`transplant.py move` commands is slow and error-prone; dragging them is the
natural interface, and the corpus is already a small JSON a page can read and
write back.

The page draws five layers, each independently toggleable:

1. **Bruma's real collision** — the same Havok soup the Oblivion-side renders
   use (see [Bruma collision](#bruma-collision)), clipped to the storey the
   authored mesh occupies. Blocking draws as low-alpha fill plus a bright rim,
   the same reason as in the renderer: it is overwhelmingly vertical wall, so
   the rim carries the shape.
2. **The authored navmesh**, dim, as context.
3. **Door triangles**, magenta — a subset of layer 2, drawn apart so "did the
   door land on the mesh" is answerable by eye.
4. **Our generated mesh**, coloured by `draw.tri_class` so the editor and
   `render.py` cannot disagree about what counts as a defect. Off by default:
   it is the thing under test, not context.
5. **The pathgrid**, draggable.

### <a id="nvnm-flags-vs-cover-flags"></a>An NVNM triangle has TWO flag fields

The triangle struct is `'<6h2H'`: three vertex indices, three neighbours, then
**Flags at index 6 and Cover Flags at index 7**. `wbNavmeshTriangleFlags` in
`references/xEdit/Core/wbDefinitionsCommon.pas` puts Door at bit 10 of *Flags*;
bit 10 of *Cover Flags* means something else entirely, and the two are easy to
confuse because both are `itU16` and adjacent.

Reading index 7 reports **13 door triangles** in `BrumaChapelHall`; reading
index 6 reports **one, triangle 224**. The NVNM **Doors array** (parsed into
`check.py`'s `nm.door_tris`) names its triangle outright and independently
gives `[224]` — so cross-checking the flag against that array is the way to
confirm the field index, and any disagreement means the wrong field was read.

**Nodes are placed against the WALLS, never against the authored mesh.** The
authored layer is there to show which room you are in, not to snap to — a grid
fitted to the answer key leaks the answer into the input, which is the whole
failure mode the corpus exists to avoid. The live score (nodes off the mesh,
edges crossing a void) is a *diagnostic* for the same reason: it reports where
the inherited Oblivion grid disagrees with Bruma's layout, which is a prompt to
think about where people actually walk, not a number to drive to zero.

### Known gap

Six vanilla Skyrim clutter meshes placed in Bruma interiors fail to parse even
after [Patch 16](asset_convert_nif.md#patch-16-sse-havok-layouts) —
`MiscSackLargeFlat01`/`03`, `UpperChest01`, `NobleWardrobe01`, `Barrel01`,
`StrongBox01`. On BrumaChapelHall that is **8 of 300 placements**, all small
clutter; every wall, floor, pillar and door extracts. The editor draws what it
has rather than hiding the gap.

### <a id="editor-navindex-cache"></a>Why the editor caches the NavIndex

`placed_nodes` needs the source cell's pathgrid, which lives in the audit index
(`export/<plugin>/audit_index3.pkl`, ~2 GB). Constructing a `NavIndex` to read
it measured **10.1 s** — and it was constructed on every call. The editor
re-scores on each drag release, so an uncached index put a ten-second stall
between letting go of a node and seeing the readout update: `POST /score`
measured **10,735 ms**. `transplant.index_for` keeps one index per export path
for the life of the process, which takes that to milliseconds.

The cache key is the NORMALIZED path. The same export reaches `index_for`
spelled two ways -- `export/Oblivion.esm` from the corpus JSON, and
`export\\Oblivion.esm` from `os.path.join('export', plugin)` in
`cellview.bake.mesh_bake` -- which are different dict keys. That built two
NavIndex objects for one export and paid the 10 s load twice on startup. It
also broke `NavIndex.arm`, whose `_armed` check compared the two spellings and
reloaded the collision tables on every alternation.

**The cache is LOCKED, because the server is threaded.** `ThreadingHTTPServer`
handles each request on its own thread, and `index_for` was an unguarded
check-then-build: two requests that missed the same key each constructed a
`NavIndex`, so both ran `arm()` and interleaved their writes to the shared
collision, door and namespace globals. Observed as a `/plugin_cells` and a
`/mesh` request racing on one export, surfacing as `ValueError: I/O operation
on closed file`.

That exception came from `load_origin_shifts`, which silenced its callee by
wrapping it in `contextlib.redirect_stdout(open(os.devnull))` -- a swap of the
PROCESS-GLOBAL `sys.stdout`. Thread B exiting the `with` closed the devnull
handle and restored stdout while thread A was still inside, so A's `print`
wrote to a closed file. **A threaded caller cannot mute a callee by redirecting
`sys.stdout`**; `load_furniture_models` takes a `quiet` flag instead and the
redirect is gone.

The page-side half of the same problem: the canvas prototype redrew all ~22,000
collision triangles per mouse-move. The WebGL version uploads each static layer
to the GPU once, and a drag patches only the moved node's instance matrix and
the few line-buffer slots its edges occupy (`nudgeNode` / `moveEdgesOf`), so
mouse-move cost is independent of cell size.

### <a id="le-references-beat-sse-bsas"></a>Vanilla meshes come from the LE references, not the SSE BSAs

Bruma interiors place plenty of **vanilla** Skyrim furniture and clutter. Pulled
from the SSE BSAs those meshes fetch fine and then fail to parse, so the object
contributes **no collision at all** — silently. That is what an unexplained gap
in a render is: the navmesh stops at something the picture does not draw.

`references/Skyrim Meshes/meshes` holds the LE copies (23,424 files), and LE
assets are SSE-compatible, so they are simply a better source. Measured on the
six meshes that failed from the SSE side:

| mesh | SSE BSAs | LE references |
|---|---|---|
| `furniture\noble\noblebedsingle01.nif` | parse error | **b=1080, w=486** |
| `clutter\barrel01.nif` | parse error | b=900, w=504 |
| `clutter\upperclass\upperchest01.nif` | parse error | b=144, w=72 |
| `clutter\common\strongbox01.nif` | parse error | b=72, w=36 |
| `clutter\containers\miscsacklargeflat01.nif` | parse error | b=639, w=117 |
| `furniture\noble\noblewardrobe01.nif` | parse error | b=504, w=180 |

The SSE failures are pyffi layout bugs in the Havok blocks
(`bhkConvexVerticesShape`, `bhkCompressedMeshShapeData`) that survive
[Patch 16](asset_convert_nif.md#patch-16-sse-havok-layouts) — Patch 16 fixes the
`bhkRigidBody` Body Flags width, which is necessary but not sufficient. Rather
than chase each remaining layout, the fetch order is now **Bruma archives → LE
references → SSE BSAs**, and the LE copies sidestep the whole class.

## <a id="hand-corrected-navmesh-corpus"></a>The hand-corrected navmesh corpus

**Code:** `tools/navmesh/meshedit.py`, served by `tools/cellview/bake.py`; read back by `tools/navmesh/fix_analyze.py`.

<a id="a-correction-is-read-off-its-result-only"></a>**A correction is read off `result` against `base`; `ops` are keystrokes and mean nothing** (`make_entry`, `fix_analyze.py`). The first two corrections (ImperialDungeon02/03) carried 152 ops for 58 net vertex moves -- one vertex was dragged seven times -- and reading the ops as decisions measured the human's fight with the editor, not the mesh they wanted. `base` (the generator mesh the human edited) is stored in the file so the diff survives the generator moving; without it an index-based diff against a fresh build reads every vertex as changed. The analysis is scoped to the touched region: the two corrections changed 25-45 triangles of 600-940, so cell-wide percentages read as noise ("+0.2% area") while inside the region the human moved **13 of 85 and 45 of 101 vertices** and covered **3.4-3.6% more floor**. What they encoded, measured with the tool: the boundary sat 10-60u inside the real floor edge (steep-edge approaches never grew, a hanging object zeroed a corridor, the neighbour cap pinched a mouth), corridors were zigzag-triangulated where rows of quads belong, and a mid-stair pathgrid node snapped 44u down through a tread seam sank the ramp 32u into the floor. After the fixes the generator covers **326 of the 361 floor samples the human added in ImperialDungeon02 and 249 of 374 in ImperialDungeon03**; what it still does worse than the human is the flight itself, which the profile places where the pathgrid line climbs (compressed to 45u where the line cuts the foot of the stairs) rather than along the flight's own 90u axis, so the stair-mouth triangles run 50-56 degrees against the human's 33.

### Mesh mode lives in the SAME page

Triangle editing is a MODE of the transplant editor (`t`), not a second page:
the collision, camera, picking and orbit arbitration are already there, and a
parallel page would duplicate ~800 lines that must not drift apart. The two
modes share `beginDragAt`/`dragTo`, generalized from node indices to plain
positions for exactly this. Mesh mode hides the read-only `generated` layer,
whose editable copy draws the same triangles and would z-fight it.

| key | does |
|---|---|
| drag vertex | move in XY |
| `alt`+drag | move in Z |
| `shift`+click two vertices | weld the first onto the second (close a crack) |
| `g` | snap the selected vertex down to the collision under it |
| `b` | build mode: click 3 corners, each an existing vertex OR bare floor |
| `del` | delete the selected triangle |
| `d` | toggle the selected triangle's door flag |
| `l` | link two triangles as a drop-down, UPPER picked first |

The cell picker is a prefix search against `/plugin_cells`, not a full list: an
export holds thousands of cells with a pathgrid, and shipping them all to a
`datalist` stalls the page.

### <a id="welding-rewrites-the-index"></a>Welding rewrites the INDEX, not just the position

The first `snap_vert` moved vertex `v` onto `to_v`'s coordinates and stopped
there. That looks like nothing happened, because the two corners were already
almost coincident — and, worse, it fixes nothing: a navmesh crack closes only
when the two triangles **share a vertex index**. Two distinct indices at
identical coordinates are still a crack, and the CK's own validation counts
them as duplicate/asymmetric edges.

So the weld rewrites every triangle that referenced `v` to reference `to_v`,
and drops any triangle the weld leaves degenerate (fewer than three distinct
corners). The moved coordinates are kept as well, so an orphaned `v` no longer
used by any triangle sits harmlessly on top of `to_v`.

### Building a triangle must reach OPEN FLOOR

The first cut collected "the last three vertices clicked", which can only ever
fill a hole between triangles that already exist — it cannot EXTEND the mesh,
which is the more common repair. Build mode (`b`) takes each corner as either
an existing vertex or a click on bare collision, the same gesture that adds a
pathgrid node in corpus mode; a corner on open floor becomes an explicit
`add_vert` op so `replay` reproduces it.

Ring corners are drawn enlarged and magenta, and join the handle set even when
no triangle uses them yet — a corner placed in open floor would otherwise be
invisible, leaving no indication of what is selected.

### One cell at a time, and the scope follows it

Opening a cell drops **every** layer of the previous one — collision, authored
underlay and pathgrid alike — via `clearLayers` / `clearPathgrid` /
`meshDiscard`. The first cut only replaced collision when the name differed,
which left two cells on screen at once.

The collision-source buttons (Bruma / Oblivion / Both) and the authored/ours
toggles are **corpus concepts**, hidden for an arbitrary cell which has one
collision set and no Bruma counterpart. `applyScope` keys that off `G` being
non-null, and the pathgrid edit modes are disabled rather than silently inert.

**The corpus cell picker always stays visible.** Hiding it made leaving corpus
mode a one-way door with no way back; it keeps a blank first option so it can
show "no corpus cell" and still re-select one.

The cell's **pathgrid is drawn read-only** in mesh mode: it is the generator's
input, so it is the first thing to look at when the output is wrong. Node
hover is suppressed there, because the thing under the pointer is a vertex.

### <a id="instanced-colour-is-not-vertex-colour"></a>Black nodes: `vertexColors` on an INSTANCED mesh

Per-instance colour and per-vertex colour are different three.js features, and
asking for both when only one exists paints everything black.

`InstancedMesh.setColorAt` allocates `instanceColor`, and three.js sets
`USE_INSTANCING_COLOR` by itself when that buffer is non-null. The shader chunk
is:

```glsl
#elif defined( USE_COLOR ) || defined( USE_INSTANCING_COLOR )
    vColor = vec3( 1.0 );
#endif
#ifdef USE_COLOR
    vColor *= color;          // per-VERTEX attribute
#endif
#ifdef USE_INSTANCING_COLOR
    vColor.xyz *= instanceColor.xyz;
#endif
```

Setting `vertexColors: true` additionally defines `USE_COLOR`, whose
`vColor *= color` reads a per-vertex `color` attribute that `SphereGeometry`
does not have. **An absent attribute reads `(0,0,0)`**, so the multiply zeroes
the instance colour and every sphere renders solid black.

**`instanceColor` needs no material flag.** The fix is to drop `vertexColors`
from both instanced handle meshes. `classedLayer` keeps it, correctly — it sets
a real `color` attribute on its geometry first.

Verified by reading the shader chunks out of the pinned
`three.js/0.160.0/three.min.js` the page loads, not by inference.

### <a id="never-dispose-a-shared-geometry"></a>Also: never dispose a SHARED geometry

`nodeGeo` is a module-level `SphereGeometry`, built once and reused by every
`buildPathgrid`. `buildPathgrid` also began by calling
`nodeMesh.geometry.dispose()` — and `nodeMesh.geometry` **is** `nodeGeo`. So
the first rebuild freed the shared geometry's GPU buffers, and every rebuild
afterwards handed that dead geometry to a new `InstancedMesh`. The nodes drew
**solid black**.

It stayed latent while the page only ever loaded one cell at startup; adding
`clearPathgrid` (for one-cell-at-a-time switching) made a rebuild happen on
every cell open, so it surfaced constantly.

**A cached geometry must outlive every mesh that borrows it.** `vertHandles`
was never affected because it builds a fresh `SphereGeometry` per call — the
per-build line and triangle geometries are still disposed, correctly.

Two earlier theories for this were wrong and are recorded so they are not
retried: the material being `MeshLambertMaterial` (an unlit `MeshBasicMaterial`
is right for markers regardless, but lighting was not the cause), and a
temporal-dead-zone read of the palette constants (no top-level code calls
`buildPathgrid`, so the constants are always initialized by then).

Separately, **blue collided with blue**: mesh mode already spends blue on
vertex handles and white on triangle outlines, so the grid's usual `0x3ba0ff`
read as more mesh. In mesh mode it switches to violet, which means
`buildPathgrid` must re-run on a mode change, not only on load.

`cPath` and `cTri` hide the pathgrid and the triangles independently — with
both layers occupying the same floor, being able to drop one is what makes the
other readable.

### Drop links are editable

`build_navmesh` returns Ledge Up/Down pairs **out-of-band** through
`ledges_out` so the long-standing `(verts, tris)` return stays intact;
`CellCtx.build` now forwards that parameter, which is why the editor can see
them at all. A link joins two triangles that do NOT share an edge, so the only
way to see one is to draw it centroid-to-centroid (orange).

Links are **directional** — Ledge Up is not Ledge Down — so `l` takes the upper
triangle first. `replay` drops a link whose triangle was deleted and remaps the
survivors, the same tombstone-then-compact rule the door flags follow.

Measured: `ImperialDungeon01` has one (triangle 567 → 276, a 192-unit drop);
the Bruma corpus interiors have none, being single-storey.

The [pathgrid corpus](#authored-navmesh-corpus) says what the generator's INPUT
should look like. This says what its OUTPUT should have been, which is a
sharper signal: a cell where the mesh is visibly wrong gets fixed by hand --
snap a crack shut, tilt a ramp to match the stairs under it, fill a hole -- and
the corrected mesh becomes a target to score against.

Corrections live in `tests/navmesh_fixed/<plugin>/<cell>.json`, separate from
`tests/navmesh_authored/` because they answer a different question and apply to
**any** cell of **any** plugin, not only the seven Bruma name-matches.

### Ops AND result, not one or the other

Each file stores both:

- **`ops`** -- the reviewable changelist (`move_vert`, `snap_vert`, `add_tri`,
  `del_tri`, `set_door`). This is what says *what was being fixed*, which is
  the part that tells the generator what to learn.
- **`result`** -- the finished `verts`/`tris`/`doors`. This is the scoring
  target, and it never goes stale.

`base_hash` pins the generated mesh the ops were authored against (SHA-1 over
vertices rounded to 0.01u and triangle indices; the rounding absorbs float
jitter that moves nothing a human would call different). When the generator
improves, the hash stops matching and `is_stale` reports it: the **ops** are
then suspect, but `result` is still a valid target. Storing only ops would lose
the target on every generator change; storing only the result would lose the
intent.

### Deletions tombstone

`replay` sets a deleted triangle to `None` and compacts only at the end, so
every op keeps addressing triangles by their ORIGINAL index. The page, the
changelist and the ops all speak the same index space, and an op list stays
order-independent with respect to deletions.

### Door triangles are editable

A door triangle is marked by bit 10 of an NVNM triangle's Flags (see
[flags vs cover flags](#nvnm-flags-vs-cover-flags)). Our own door triangles are
identified with production's `corridor._tri_carries_door`, not a second
predicate, so what the editor marks is what the writer would flag.

This immediately showed a real defect: `BrumaChapelHall` has **one** authored
door triangle (224) where we flag **seven** (110, 140, 157, 173-176) -- our
door quad fragments where Bethesda's is a single triangle.


## <a id="navindex-arms-shared-tables"></a>NavIndex arms shared module tables

**Code:** `tools/navmesh/index.py`, `asset_convert/collision/collision_extract.py`

`ce.load_collision` replaces a module-global dict (`_COLLISION`), and
`load_door_centroids` and `load_origin_shifts` behave the same way. Building a
`NavIndex` therefore does not give that index its own tables -- it repoints one
shared set of globals at that export.

In a one-shot tool this is invisible: a process builds one index and exits. The
transplant editor is long-lived and lists every export that has an audit index
(measured: `Morrowind_ob.esm`, `Nehrim.esm`, `Oblivion.esm`), so opening a cell
from a second plugin re-armed the globals and every later Oblivion cell then
found no collision for its meshes -- `get_collision` returned `None` for every
path key, and the page drew a navmesh floating in empty space with "walls" and
"floor" both ticked. Cells baked BEFORE the switch kept working, because
`cellview.bake.CACHE` had already stored their geometry, which made the
failure look specific to one cell.

Measured on the running editor: of five cells requested, only
`imperialdungeon03` (cached earlier) returned collision; `imperialdungeon02`,
`AnvilPinarusInventiusHouse`, `AnvilCastleGreatHall` and
`ICMarketDistrictTheBestDefense` all returned `walk 0 block 0` while the same
cells baked correctly in a fresh process.

`NavIndex.arm()` re-arms the globals for its own export and is called from
`cell()`, so whichever index is used is the one whose tables are live. A
class-level `_armed` records which export currently owns them, so the common
single-export case still pays the load exactly once.

### The asset namespace is one of those globals

**A model key carries the game namespace, so arming must set it too.** Every
key is `<namespace>/<model path>` (`pool.model_key`, off `current_namespace()`),
and only the conversion pipeline used to call `set_namespace`. A diagnostic left
the process default `tes4` in force, so `Morrowind.esm` -- namespace `morrowind`
-- looked its meshes up as `tes4/o/contain_crate_01.nif` against a cache keyed
`morrowind/o/contain_crate_01.nif`, and `get_collision` returned `None` for all
4,509 of its base models. Cellview drew the pathgrid in an empty room.

`audit.py` made it permanent: it carried its OWN copy of `model_key` with
`'tes4/'` HARDCODED, so the miss was baked into the persisted `cell_index`
rather than recomputed per process. That duplicate is deleted -- the one
namespace-aware implementation is shared -- and `_parse_tables` and
`NavIndex.arm()` both call `set_namespace(namespace_for(export))`. `SCHEMA` is
bumped to 4 so indexes written with `tes4/` keys rebuild instead of being
served.

Measured on `Imperial Prison Ship` (Morrowind.esm): `walk 0 block 0` before,
14,949 walkable and 49,023 blocking triangles after. Oblivion is unaffected --
its namespace IS `tes4`.

### An EMPTY collision cache read as current

**`_entry_table_is_intact` accepted `count == 0`** (`collision_extract`), so a
20-byte `export/Morrowind.esm/collision_cache.bin` -- valid `TESCOL07` magic,
zero entries, table consuming the blob exactly -- pinned itself as fresh. The
rescan in `_rescan_mesh_caches` is gated on that check, so it never fired again
and the empty cache survived every run. An empty table is now never current;
the rescan found 4,732 of 6,978 NIFs with collision.


## <a id="land-slit-buffer-must-be-repaired"></a>Land slits: repair the buffer, never kill the cell

**The slit-closing buffer produces INVALID geometry, and a failed fill must never kill the cell** (`corridor_union._close_land_slits`). The mitre round-trip `buffer(+16, join_style=2).buffer(-16, join_style=2)` self-intersects on terrain sheets whose outline has sharp spurs, and the `difference` against it then throws out of the whole cell -- **13 Oblivion exteriors and 1 TR_Mainland cell produced NO navmesh at all**, as `TopologyException: found non-noded intersection` (9), `unable to assign free hole to a shell` (3), `side location conflict` (1) and one `AssertionFailedException: Should never reach here`.

The input is valid every time; only the buffered ring is (measured on 0100604B: `in valid=True buffered valid=False`, self-intersection at 42882.0 13177.3). So the ring is repaired with `make_valid` before the difference, which rescues the topology cases and yields a sensible fill. One cell (01006879) has both polygons valid and fails inside GEOS's overlay anyway, where `make_valid` does NOT help -- so `GEOSException` is caught around both the difference and the union and `gmerged` is returned unchanged. **The slits are cosmetic; a cell with hairline gaps beats a cell with no navmesh.** All 14 cells build after the fix, and a 40-cell sample of already-working Oblivion cells is byte-identical, so the repair touches only what was crashing.

## <a id="leveled-placements-never-carve"></a>Leveled placements never carve

**A REFR placing a leveled creature is gone before the navmesh gathers** (`pool.drop_leveled_placements`). `leveled_actors.build_leveled_actor_shells` rewrites every such REFR into an ACHR aimed at a shell NPC_, and it runs in the pre-scan phase -- BEFORE phase 4a. A navmesh built from the raw REFR list therefore carves placements the import never carves.

This is what made the pre-push gate refuse a cache the import had just written: `job_trace.load_export` parsed the export and gathered jobs directly, so Morroblivion cell 024C16D6 came to 48 refs in the checker against the import's 42 (**501 verts stored, 514 rebuilt**). The checker must run the same preparation as the import, so the filter lives here and BOTH paths call it; only the import also mints the shells. Every other step the import performs between parse and gather -- `exclude=RUNTIME_ONLY_TYPES`, `drop_author_deleted_records`, `set_namespace`, `set_formid_index_offset` -- is on the checker's path for the same reason.

## <a id="base-ids-carry-their-plugin-index"></a>Base ids carry their plugin index

**A carving base is keyed by its FULL FormID, never the low 24 bits** (`pool._records_of`). Morroblivion's LVLC `02000BC6` (`fbmw0bmUexUfelcoastU40`, a region spawn list) and Oblivion's TREE `00000BC6` (`TreeWillowOakFreeSU`) share the low bytes, so a low-24 index handed the leveled list an unrelated tree's collision and carved a phantom trunk. **16 low-24 ids in Morroblivion are claimed from two plugins at once, 7 of them by an LVLC.**

The two id spaces must be aligned before they can be compared. `load_master_export` keys by the RAW TES4 slot (Oblivion = 0) while `get_formid` shifts every REFERENCE by the load-order offset, so a NAME of `01000BC6` names master key `00000BC6`; the master's key is shifted by the offset here to match. Masking to low bytes hid the mismatch by accident and resurrected the collision at the same time -- **offset-correcting raises exact REFR->base matches from 247,678 to 253,462 and drops low-only matches from 6,006 to 222**, the 222 being exactly the collisions that SHOULD miss.

### The debug tools kept their own low-24 copies

**Three tools rebuilt the carving index with `int(f, 16) & 0xFFFFFF` while the
pipeline keys the FULL FormID**, so every lookup done through them missed on a
plugin with masters. `audit.py:_parse_tables` and `_door_model_map` persist
theirs into `cell_index.sqlite`, so the miss was BAKED IN rather than recomputed
per process; `probe.py` built a third copy; and `index.py:collision_sources`
masked the key a fourth time, one line away from the `base_fid` that
`_placed_soup` actually resolves with.

Morroblivion is the case that shows it, because its REFRs name bases in two id
spaces at once: of 275 refs in `ImperialSPrisonSShip` (`Morrowind_ob.esm`), 259
are `01xxxxxx` and 16 are Oblivion's `00xxxxxx`. Measured on that cell,
`base_model.get(base_fid(refr))` hit **7** of 275 while the masked key hit 178 --
the index was keyed one way and queried the other, so cellview drew the pathgrid
in an empty room exactly as the `tes4/` namespace bug did.

All four copies now key by `get_formid`, and `cell_index.SCHEMA` is bumped to 5
so indexes written with masked keys rebuild instead of being served. Measured on
`ImperialSPrisonSShip`: `walk 0 block 0`, 0 named collision sources before;
**16,080 walkable, 51,475 blocking, 167/167 sources named** after. `Morrowind.esm`
(no masters) and `Oblivion.esm` are unchanged -- with one plugin in the chain the
two keyings agree.

## <a id="master-owned-cells"></a>Navmesh in a cell the plugin does not own

**Code:** `navmesh/pool.py:gather_navm_jobs`, `overrides/master_index.py:navms`.

A plugin that edits a master's cell — Unique Landscapes moving rocks around an
Oblivion.esm exterior — must **override** that cell's navmesh, shipping it under
the master's NAVM FormID. It must not ship a navmesh under a new id: the master's
navmesh is still loaded, so both cover the same ground.

This is what Skyrim modding practice already requires, for engine reasons rather
than tidiness:

* The engine resolves navmesh conflicts **by FormID, one winning record per id**.
  Overrides replace; they never merge. Two records with different ids are not a
  conflict the engine can resolve — both stay live.
* xEdit's guidance is explicit that navmesh conflicts are settled by load order
  and that one mod's navmesh is never forwarded into another's. Its
  "remove identical to previous override" cleaning exists precisely to strip
  redundant NAVM overrides, which presupposes overrides share the master's id.
* NAVI (the Navigation Mesh Info Map) is a singleton, `0x00012FB4`, that every
  file overrides with its own NVMI entries. An NVMI naming a duplicate navmesh
  registers a second mesh over the same ground.

### Why the id cannot be derived

`derive_formid` composes every generated id as `(own_index << 24) | offset`, so a
derived id **always** carries the plugin's own index byte and is structurally
incapable of naming a master's record. Nor can the master's id be recomputed:
`_choose_derived_region` picks the hash window from *that plugin's* emptiest 64K
run of authored ids, so the offset depends on Oblivion.esm's own occupancy, not
on anything Unique Landscapes can see.

The id is therefore **read back** from the converted master, exactly as
`MasterIndex.land` reads back reallocated LAND ids — `_scan` already walks every
record of the built ESM, so keying NAVM by its enclosing cell-children GRUP costs
one more branch. No formula, nothing to get wrong.

### Why a list, and why the id is chosen BEFORE generation

`navms()` returns a list where `land()` returns a single id: a cell owns at most
one LAND, but a navmesh may be **split**. Measured on the converted
`output/Oblivion.esm`: 8,238 NAVM across 8,202 cells, of which 8,182 own one and
20 own between 2 and 7.

The master id is substituted in `gather_navm_jobs`/`precompute_navmeshes`, before
`convert_PGRD` runs — never restamped onto finished bytes the way LAND is. A NAVM
carries its own FormID in **three** places:

1. the record header,
2. the NVNM body, where every ledge edge-link names the owning navmesh
   (`_resolve_ledge_links` appends `(type, navm_fid, other)`),
3. `meta['door_xndp']`, which seeds the door refs' XNDP pathing links,

plus `meta['fid']`, which is what NVMI registers in NAVI. Restamping the header
alone would leave the NVNM payload and the door links pointing at an id no record
has. Passing the id in as `navm_fid` makes all four agree by construction.

### The invariant

Substitution happens only when a converted master index is present **and** the
cell is master-owned. A masterless plugin (Oblivion.esm, Nehrim.esm) has no
master index, so it takes the unchanged path and its navmesh output is
byte-identical.

`derive_formid` is still called for **every** job, master-owned or not, and the
master id replaces the result afterwards. Skipping the call instead would change
the allocator's `_derived_taken` set, and since collisions are resolved by
rehashing against that set, unrelated derived ids in the same plugin could move —
drift for records that have nothing to do with navmesh.

### <a id="cross-plugin-edge-links"></a>Edge links across a plugin boundary

**Code:** `navmesh/edge_links.py:_master_neighbour_views`, `_repack`.

`build_edge_links` indexed `views` from `navm_cache` alone, so a cell the
plugin does not navmesh simply was not there. Two things went wrong at
every region boundary:

* `views.get(other)` missed, so the seam was skipped and NO link was made.
* `live_fids` held only this plugin's ids, so `_prune_links` judged every
  master-bound link dangling, deleted it, and reverted the triangle edge to
  a plain border.

Because an override keeps the master's NAVM FormID, the stripped record
WINS: the master's own links cannot compensate for what its override drops.

Measured on Unique Landscapes Compilation v2.2.0 against Oblivion.esm:

| | |
|---|---|
| UL cells bordering a master-only cell | 815 (1,212 edges) |
| UL cells linked to any master navmesh | **0** |
| Boundary cells the master linked across | 710 |
| ...whose link UL destroyed | **710 (100%)** |

Every UL region was a sealed navmesh island: actors path inside it and
inside vanilla terrain, never between them.

The fix loads each bordering master mesh read-only into `views` (so seams
match and pruning keeps the links) and re-emits any that gained one as an
override of the master's NAVM, keyed `('master_navm', fid)` so it cannot
collide with a `(cell, pgrd)` job key.

### A re-emitted master must keep the links it already had

**Code:** `navmesh/edge_links.py:_prune_dead_links`.

`live_fids` was built from the meshes THIS plugin writes plus the loaded
neighbours, so a master mesh we re-emit had its links to cells further out
in the master's own terrain judged dangling and deleted -- shipping an
override strictly worse than the record it replaced. Measured on Unique
Landscapes: of 2,675 links the master copies arrived with, 833 were lost.

Every master navmesh id is therefore live, whether or not it was loaded
here: the master's own records are always present at runtime.

The frontier stops one ring out by design. A master mesh gains the seam
facing us, but its own outward neighbours are never requested (they enter
`views` after `wanted` is computed). Those master-to-master seams already
exist in the master file and need nothing from us -- measured 211 such
seams, all with a relinked master copy as their source cell.

**Links must stay symmetric.** Both halves of a seam ship: ours through its
normal cache entry, the master's through that synthetic one. Emitting only
our side would leave a one-way portal.

### The COLLISION caches must chain too

**Code:** `navmesh/pool.py:collision_cache_chain`,
`collision/collision_extract.py:load_collision`.

Merging the reference records is only two thirds of the chain. A REFR is a
placement; carving needs the base record's model path, and then the collision
mesh cached for that path. `build_base_model_index` already merged
`master_export`, so the model path resolved — but `load_collision` REPLACED
the module cache with a single file, and the pipeline handed it the plugin's
OWN `collision_cache.bin`, which holds only the meshes that plugin ships.

Measured on UL's `CloudRulerTempleExterior02`, over the 154 merged references:

| Cache loaded | entries | collision hits | misses |
|---|---|---|---|
| UL's own | 1,083 | **0** | 149 |
| Oblivion.esm's | 6,093 | 143 | 6 |
| both, chained | 7,176 | **143** | 6 |

So the record merge alone left the carver with 154 references and collision for
**none** of them. The triangle count went UP rather than down, because nothing
carved holes where the buildings are — a denser sheet over the same ground.

`load_collision` therefore takes a path OR an iterable of them, MASTERS FIRST
so the plugin's own mesh wins a shared path key, and `collision_cache_chain`
builds that list from the export header's master names. A masterless plugin
yields a one-element chain and is byte-identical. The 6 residual misses are UL
meshes absent from both caches.

The parent process loads the same chain (`pipeline.py`), since furniture seats
and door panels read the module cache too.

### The geometry must merge the masters too

**Code:** `navmesh/pool.py:_merge_master_cell_records`.

Owning the master's NAVM id is only half the contract. A child plugin restates
**only the references it edits**, so building the navmesh from `by_type` alone
carves the cell as if the master had furnished nothing.

Measured on Unique Landscapes Compilation v2.2.0, `CloudRulerTempleExterior02`
(`000044AD`, Tamriel 3,39):

| Source | REFR in cell |
|---|---|
| Oblivion.esm | 121 |
| UL's own export | 45 |
| of those, NEW in UL | 33 |
| master refs UL never restates | 109 |

So 109 of the master's 121 references were invisible to the carver. The LAND is
fully restated (1,888 keys in both files), which is why the symptom was a
*degenerate, hole-filled* navmesh rather than a missing one: the ground was
there, almost every static that should carve or support it was not. The part of
the cell UL does not modify is exactly the part whose geometry lives only in the
master, and it degraded worst.

This was latent before navmesh overriding landed. The thin navmesh used to ship
under a **derived** id, so the master's correct navmesh stayed loaded beside it;
once the child adopted the master's id, the thin mesh *replaced* the good one.

`_merge_master_cell_records` therefore makes the masters the baseline for REFR
and LAND: master records first, the plugin's own overriding by FormID, and an
override flagged `DELETED_FLAG` dropping out so a deleted ref cannot resurrect.
After merging, the cell above carries 154 refs (121 master + 33 new; the 12
edited ones replace rather than add).

**PGRD is deliberately NOT merged.** Which jobs exist, and their
`(cell_fid, pgrd_fid)` keys, stay driven by the plugin's own pathgrids — merging
there would invent jobs and move every derived NAVM id. Verified on UL: job
count is 1,865 before and after, and the probe cell keeps pathgrid `000392E8`.


## <a id="patching-a-navmesh-into-a-built-esm"></a>Patching one corrected navmesh into a built ESM

**Code:** `tools/navmesh/navm_patch.py`, reached from cellview's "To ESM" button
(`/esm_patch` in `tools/cellview/server.py`).

A correction in `tests/navmesh_fixed/` was a scoring target and nothing else:
testing one in-game meant a whole `--import-only`. This writes the corrected
geometry into `output/<plugin>/<plugin>.esm` in place instead, so the edit loads
on the next launch.

**Only the NVNM geometry moves.** The record keeps its FormID, its EDID and its
ONAM, so NAVI's NVMI entry, every REFR's XNDP and every neighbour's edge link
still name something real. `pack_nvnm` reproduces a shipped NVNM **byte for
byte** from its own decode — verified on 3 interior and 2 exterior records of
`output/Oblivion.esm` — which is what makes repacking with new triangles safe:
the only difference in the blob is the geometry the human changed.

**Doors and water flags are carried over, not recomputed.** A retriangulation
renumbers every triangle, so each Door Triangle is re-aimed at whichever new
triangle is nearest the old one's centroid, keeping the door REFR's FormID (and
therefore its XNDP) intact. The water flag is a height test against the cell's
water plane; reading that plane back as the highest Z any water triangle reached
reproduces the test without re-reading the CELL record.

**An exterior cell's seams are re-stitched, both sides.** Cross-cell Portal
links name triangle indices, so the neighbours' links into a rewritten mesh are
all stale. Each of the four orthogonal neighbours has its links to this mesh
dropped and renumbered, the seam is matched again with the import's own
`border_edges`/`match_seam`, and the neighbour record is rewritten too. Measured
on `output/Oblivion.esm`, 1,000 of 1,002 exterior navmeshes sampled carry edge
links, so skipping this would strand the edited cell.

**A split cell is refused.** `split.py` cuts an interior with a same-cell
teleport pair into one NAVM per component; re-splitting mints new FormIDs and
moves door XNDPs, which only a real import can do. Measured on
`output/Oblivion.esm`: 8,183 of 8,203 navmeshed cells hold exactly one NAVM, 20
hold more, so the refusal costs almost nothing.

**Resizing a record means fixing every GRUP above it.** A GRUP's size covers its
children, so a record that grows or shrinks changes the size of each GRUP
enclosing it; `splice` collects those headers by walking down to the record's
offset, applies the delta to each, and then does the byte replacements back to
front so the earlier offsets stay valid.

The load-order shift is read back off the two files rather than reconstructed:
the output's master list is the TES4 one with new masters prepended, so the
difference in their lengths IS the shift every FormID's index byte took.


## <a id="pinned-navmesh-floor"></a>Pinned navmesh floor: a correction that survives the generator

**Code:** `tes5_import/base/navmesh_pins.py`, written by cellview's **Pin edits**
button, consumed in `from_pgrd._cell_geometry` and `corridor.build_corridors`.

A correction in `tests/navmesh_fixed/` records triangle and vertex INDICES, so
it is meaningless the moment the generator renumbers anything — `is_stale`
exists only to refuse replaying one. The corpus census shows the decay
directly: of five corrections on disk, three (`ImperialDungeon01/02/03`) have
**zero ops and identical base/result**, having already collapsed into snapshots.
The files are also gitignored, so nothing a human decided ever reaches another
machine.

A pin is the durable half of the same intent. It stores the **world positions**
a human declared walkable — not indices — so it survives any retriangulation,
and it is small enough to commit and read in a diff.

**Pins ride a mechanism that already existed.** `corridor_clean.finalize` takes
`pin_xy`, a list of `(x, y, z)` points, and `_covers_any_sample` keeps any
triangle containing one at its own height. Every destructive stage already
consults it: `_make_manifold` (three times), `cull_boundary_slivers` (twice),
`cull_open_flaps` and `_drop_unreachable_islands`. Pathgrid samples and doors
already ride it, and the docs record that a walked line "outranks every other
candidate on an edge." A hand pin is one more point in that list.

**Pins go to `pin_xy`, never to `_pins`.** The decimator's own pin list is
deliberately limited to doors and nodes — `finalize`'s comment states that
pinning all of `pin_xy` there would disable decimation everywhere. A pinned
triangle is therefore protected from being CUT, while the mesh over it may
still be re-triangulated. That is the intended reading: the human declared the
space walkable, not the tessellation sacred.

**What a pin cannot do.** It protects floor that the generator produces; it does
not make the generator REACH ground it never grew. Forcing coverage is the
`build_union_mesh(extra_strips=...)` path that door footprints already use, and
it is deliberately not part of this.

**The key is plugin plus cell name.** One committable file per source plugin,
`navmesh_pins/<plugin>.json`, keyed by cell EditorID — or, for an exterior cell,
by the `"<worldspace> X Y"` grid reference cellview opens it with, since an
exterior CELL has no EditorID. Lookup is case-insensitive: the name is one a
human typed.

**The loader lives outside `tes5_import/navmesh/`.** That folder's bytes ARE the
geometry cache tag (`pool.navmesh_geom_cache` hashes every `.py` in it), so a
loader placed there would invalidate all ~8,200 cached cells on every edit. In
`tes5_import/base/` it is free to change. The pin DATA still enters
`geom_hash` per cell, so a pinned cell's stored hash stops matching while every
unpinned cell's hash is byte-identical to what it was before pins existed.

> Note: a changed hash makes a cell's cached geometry stale, which is not the
> same as making the converter skip the others. Regenerating only the cells
> whose pins moved is separate, unbuilt behavior.


### <a id="weld-pins"></a>Weld pins: the crack a position pin cannot express

**Code:** `corridor_clean.apply_welds`, stored in the `welds` section of
`navmesh_pins/<plugin>.json`.

A floor pin protects a PLACE, and that covers most hand corrections. It cannot
express the commonest one of all. Measured on the two real corrections that
carry any ops at all, both are welds: `Imperial Prison Ship` is one
`move_vert` plus one `snap_vert`, and `imperialdungeon01.2` is one move plus
two snaps.

A crack is not missing floor. Two triangles can meet at identical coordinates
and still leave a crack, because the engine joins them only when they **share a
vertex index** — the reason `meshedit._snap_vert` rewrites indices rather than
just moving a vertex. Nothing about that is a position to protect, so a floor
pin is silently a no-op: rebuilding the Imperial Prison Ship with and without
its floor pins gave byte-identical results, 145 verts and 171 tris either way,
with the crack still open (v95 and v124 sitting 33u apart, one triangle each).

A weld pin therefore stores the two PLACES whose vertices must become one.
`apply_welds` re-finds each endpoint as the nearest generated vertex within
`WELD_TOLERANCE`, points the first at the second, and drops any triangle the
merge leaves degenerate — the same three steps `_snap_vert` performs, but keyed
on geometry that survives regeneration instead of indices that do not.

**8 units is unambiguous.** Measured on the Imperial Prison Ship, the closest
two generated vertices sit 17u apart, the 10th percentile at 32u and the median
at 64u; no vertex has a neighbour within 8u. An endpoint therefore cannot match
the wrong vertex, and a weld whose endpoint has drifted further than that
matches nothing and is skipped rather than guessed at.

It runs inside `finalize`, immediately after `_weld_coincident` and before
anything reads adjacency — `_make_manifold`, the decimator and the cull passes
all reason about shared edges, so a weld applied later would be invisible to
every one of them.

A weld is applied ONCE, at the position it was recorded. If a later generator
moves that floor wholesale the weld simply stops matching; it never drags
unrelated geometry together, because both endpoints must independently land
within tolerance.


### <a id="pin-ab-toggle"></a>The pinned-edits toggle is a RE-BAKE

Cellview's **pinned edits** checkbox re-fetches `/mesh?pinned=0|1` rather than
hiding a layer. Pins change what the generator PRODUCES, so there is no
pinned-vs-unpinned geometry sitting in the page to show or hide — the only
honest A/B is to run the generator both ways. The bake cache is keyed on the
flag so flipping back is instant, and both variants are dropped whenever the
pin file is written, or the first bake after pinning would serve the mesh from
before the pin.

Until this landed `CellCtx.build` passed no pins at all, so the editor rendered
the RAW generator while the pipeline rendered the corrected one — the viewer
silently disagreed with the build it claims to mirror.

**An unpinned cell says so.** Both halves of the A/B are identical when nothing
is committed, which reads exactly like a broken switch, so the status line
distinguishes "no pins committed" from "2 pinned tris, 1 weld APPLIED" and
"… DISABLED". Measured on the Imperial Prison Ship: 171 tris / 145 verts with
pins disabled, 170 / 143 with them applied.

Edits in progress are dropped on a flip, because their triangle and vertex
indices address the mesh being replaced.

The camera is NOT reframed. A re-bake of the cell already on screen keeps
the view (`loadMesh`'s `keepCamera`), for the pin toggle and for Reload:
the A/B is a comparison of one spot, and reframing throws away the spot.
Opening a DIFFERENT cell still frames it.


### <a id="current-mesh-is-the-generator"></a>"Current mesh" is the GENERATOR'S output, never a replay

Cellview's mesh dropdown names two different things, and they must stay
different:

* **Current mesh** — what the generator produces for this cell, right now,
  plus any edits made in THIS session. Nothing else.
* **Saved result** — the frozen `result` from `tests/navmesh_fixed/`, read-only,
  because ops cannot extend a stored mesh.

The page used to seed `mOps` from the saved correction on every load and replay
it onto the live mesh. Both dropdown entries then drew the same picture, and
every comparison against the generator was worthless: the pinned/unpinned A/B
changed the geometry underneath and the replay painted the correction straight
back over both halves, so the toggle read as doing nothing at all. The bug
survived three wrong diagnoses (the dropdown, a `hasPins` guard, the weld
endpoints) because each was reasoned from the code rather than from what the
page draws.

So `mOps` now holds only this session's edits, and the view opens on **Current
mesh** regardless of whether a correction exists. A correction is reached by
choosing it, never by having it applied invisibly.

Measured on `Morrowind.esm` / `Imperial Prison Ship`, vertex 95 is the tell:
Current mesh has it at `(-61.63, 1175.06)` with the crack open, Saved result at
`(-80.96, 1147.73)` welded. With the cell's pins committed, Current mesh drops
to 170 triangles as the weld collapses one.


### <a id="the-tag-hashes-geometry-only"></a>The cache tag hashes the GEOMETRY code, not the whole folder

**Code:** `pool._TAG_EXCLUDE`, consumed by `navmesh_geom_cache`.

The geometry cache key is per-cell inputs plus a **tag**: a SHA-1 over the bytes
of every `.py` in `tes5_import/navmesh/`. The tag is identical for every cell,
so one edit anywhere in that folder invalidates all ~8,200 entries at once.
That is correct for code that decides geometry and pure waste for code that
does not.

The cached payload is `(verts, tris, ledges)` and nothing else. Seven modules
in that folder cannot reach it: `edge_links`, `navi`, `split`, `cache_audit`,
`pool`, `worker` and `__init__`. They orchestrate the run, or rewrite records
AFTER geometry has left the cache. Editing `navi.py` used to cost a full
rebuild of every cell to produce byte-identical output.

Verified three independent ways, all agreeing:

* **Import-graph reachability** from `from_pgrd`/`build` — none of the seven is
  reachable.
* **A runtime trace** (`sys.setprofile`) over real builds of ImperialDungeon01,
  ImperialDungeon02 and AnvilCastleGreatHall — 14 files execute, none of the
  seven among them.
* **Direct reference search** — the only edges are `pool → cache_audit, worker`
  and `cache_audit → worker`, all orchestration. No geometry module names any
  of them.

**Two files that look inert and are NOT.** `params.py` never *calls* anything —
it is constants — but its values drive every stage, so it stays in the tag. And
`from_pgrd.py` did not appear in the trace only because `CellCtx.build` calls
`build_navmesh` directly; the real pipeline runs it, and it computes the hash
itself. Neither may be excluded, and "it did not execute in the trace" is not
sufficient grounds on its own.

Measured cost of the tag being all-or-nothing: a cache **hit** costs 12-18ms
against a **miss** at 1.4-5.5s, so a full warm pass is ~129s of CPU over 8,218
cells plus ~1.2 GB of job records pickled out to workers. An edit to one of the
seven used to pay a full rebuild for zero change in output.


### <a id="mixed-separators-lost-the-pins"></a>Mixed separators made every pin a no-op

**Code:** `navmesh_pins.plugin_of`.

Pins are looked up by plugin, and the plugin is derived from the geometry cache
directory rather than threaded through every navmesh worker's signature. That
directory is built by `os.path.join(os.path.dirname(collision_cache),
'navmesh_geom_cache')`, and the collision cache path arrives with FORWARD
slashes, so the result is mixed:

    export/Morrowind.esm\navmesh_geom_cache

`os.path.dirname` on Windows splits at the last separator it recognises, which
here is the backslash, leaving `export/Morrowind.esm`; `os.path.basename` of
that is the whole string, and the original code's `basename(dirname(...))`
answered **`export`**. `pins_for('export', ...)` finds nothing, so every pin and
every weld silently did not apply — including in a full `--import-only` run,
where the cell rebuilt for an unrelated reason (a tag change) and the operator
saw a fresh mesh with no correction in it.

The failure is silent by construction: a missing pin file is not an error,
because a conversion must never abort over one. That makes the wrong plugin
name indistinguishable from "nobody pinned anything".

`plugin_of` now normalizes to forward slashes before splitting, so both path
forms answer the same. Verified against all three spellings — pure forward,
pure backslash, and the mixed form the pipeline actually produces.

**How it was found:** the shipped NAVM had 145 verts / 171 tris with the
crack-open vertex still present, while calling `build_navmesh` directly with
the same pins gave 143 / 170. Same code, same inputs, different answer — which
located the difference in how the pins were fetched, not in how they were
applied.
