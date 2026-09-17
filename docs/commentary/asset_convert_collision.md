# asset_convert/collision/collision.py — Havok collision

**Code:** `asset_convert/collision/collision.py`, `asset_convert/collision/collision_constraints.py`, `asset_convert/collision/collision_winding.py`, `asset_convert/collision/collision_hulls.py`, `asset_convert/collision/collision_material.py`, `asset_convert/collision/collision_extract.py`, `asset_convert/collision/mopp.py`

## Contents

- [NIF bhkRigidBody field mapping (PyFFI ↔ newer nif.xml)](#nif-bhkrigidbody-field-mapping)
- [NIF dynamic clutter physics (Havok)](#nif-dynamic-clutter-physics)
- [Morrowind dynamic clutter: synthesizing Havok from nothing](#morrowind-dynamic-clutter)
- [MO_SYS_FIXED (7) statics simulated as clutter — "floating / spinning / on its side" (SOLVED 2026-07-28)](#mosysfixed-statics-simulated-as-clutter)
- [Skyrim APPLIES rotation/translation on non-T bhkRigidBody (THE fundamental havok bug, found 2026-07-15)](#skyrim-applies-rotationtranslation-non-t)
- [Hoisted collision dropped the child node's ROTATION (SOLVED 2026-08-27)](#hoisted-collision-dropped-child-nodes)
- [Inverted collision winding — "I fall through the floor" (SOLVED 2026-07-20; **rewritten 2026-08-20, see round 3 below**)](#inverted-collision-winding-i-fall)
- [bhkPackedNiTriStripsShape sub-shapes MOVED between formats — load CTD (SOLVED 2026-07-28)](#bhkpackednitristripsshape-sub-shapes-moved-between)
- [Constrained objects: chains, swinging traps, gates, trigger phantoms (2026-07-15)](#constrained-objects-chains-swinging-traps)
- [Activation pick region (HUD rollover "too big" on clutter) — SOLVED 2026-07](#activation-pick-region)
- [NIF bhkMultiSphereShape (dead in Skyrim, fixed 2026-07-05)](#nif-bhkmultisphereshape)

## NIF bhkRigidBody field mapping (PyFFI ↔ newer nif.xml)
<a id="nif-bhkrigidbody-field-mapping"></a>
- `unknown_int_1` → bhkWorldObjCInfo.Unused01 (4 bytes binary padding) — **zero for safety**
- `unknown_int_2` → BroadPhaseType(1B) + Unused02(3B) — set to 1 (BROAD_PHASE_ENTITY)
- `unknown_3_ints` → bhkWorldObjCInfoProperty (Data=0, Size=0, CapFlags=0x80000000)
- `unknown_byte` → bhkEntityCInfo.Unused01 — set to 116 (matching external NIFConverter)
- `unknown_2_shorts` → bhkRBCInfo padding — set to [29541, 23659]
- `unknown_6_shorts[2:4]` → bhkRBCInfo2010.UnknownInt1 — **MUST be 0** (Skyrim interprets as pointer)
- Static objects: quality_type=1 (MO_QUAL_FIXED), motion_system=5 (SYS_BOX_STABILIZED)
- Dynamic/clutter: quality_type=4 (MO_QUAL_MOVING), motion_system=3 (MO_SYS_SPHERE_INERTIA)
- Animated: quality_type=1 (MO_QUAL_FIXED), motion_system=4 (MO_SYS_KEYFRAMED)

## NIF dynamic clutter physics (Havok)
<a id="nif-dynamic-clutter-physics"></a>
- **Mass**: Keep Oblivion mass as-is. Oblivion clutter (0.1–8.0) is already in Skyrim's range (0.5–100). The legacy converter's `mass *= 6` is WRONG — makes items too heavy and causes them to "hang in the air."
- **Inertia tensor**: Must scale by `HAVOK_SCALE² = 0.01`. Oblivion inertia (2.3–8.8) is ~100× Skyrim (0.02–0.32) because inertia ∝ mass × distance² and collision shapes are scaled 0.1× for Skyrim Havok units.
  - The full ×0.01 is applied EXACTLY ONCE, in `_convert_collision` (dynamic + keyframed branches) and `_convert_blend_collision`. `scale_constraint_pivots` must NOT rescale again — a leftover ×0.1 there had every constrained body's inertia 10× too small (fixed 2026-07-15).
- **Skyrim clutter standard values**: friction=0.50, restitution=0.40, linear_damping=0.0996, angular_damping=0.0498, max_linear_velocity=104.4, max_angular_velocity=31.57, deactivator_type=1, solver_deactivation=2

## Morrowind dynamic clutter: synthesizing Havok from nothing
<a id="morrowind-dynamic-clutter"></a>
**Code:** `asset_convert/collision/clutter_plan.py`, `nif_converter_morrowind.build_collision`.

A Morrowind NIF (4.0.0.2) carries no Havok data whatsoever — no `bhkCollisionObject`,
no rigid body, no layer, no mass. There is nothing to translate, so unlike the
Oblivion path every field is synthesized. `build_collision` originally shipped one
static body (mass 0, `motion_system=5`, layer 1) over the RootCollisionNode geometry,
which left every cup, ingredient and weapon welded to the world.

- **Static vs dynamic is decided by RECORD TYPE, never the filename or the mesh.**
  The rule is *anything that can be added to the player's inventory has physics*:
  MISC / INGR / ALCH / APPA / WEAP / BOOK / KEYM / AMMO / SLGM, plus the wearables
  ARMO / CLOT. ACTI / CONT / DOOR / STAT are placed fixtures. The plugin states
  this, so `clutter_plan` maps model path -> record type straight from the export
  text, exactly as `wearable_plan` does for biped slots.
- **LIGH is NOT in the list, though a torch is carryable.** The record type covers
  both carried torches and fixed fixtures -- chandeliers, lanterns, hanging
  braziers -- and Tamriel Data's chandeliers (`pc_com_chandelier_01`, weight 0.0)
  came out as 1 kg dynamic bodies hanging from a ceiling. Being an inventory item
  is a property of the RECORD, not of the type, wherever a type spans both.
- **A wearable contributes only its WORLD model.** ARMO/CLOT carry two meshes: the
  biped model the armor path rigs to the body, and the world (`_GND`) model that is
  the dropped item. Only the latter is a loose object. `_loose_item_mass` enforces
  this structurally as well -- a tree with any skinned geometry never gets a body,
  whatever the record says -- because a rigid body on a worn mesh would detach the
  gear from the actor. Measured on Tamriel Data: 5,116 item models, of which only 2
  are named as both a worn and a dropped mesh (tower shields).
- **Mass is `DATA.Weight`**, the authored value, kept as-is per
  [dynamic clutter physics](#nif-dynamic-clutter-physics). Weight 0 (quest items)
  clamps to 0.1 kg — a mass-0 dynamic body is what Havok reads as immovable.
- **A dynamic body cannot use the MOPP/`bhkCompressedMeshShape` the static path
  builds**: Havok will not simulate concave triangle soup. Dynamic clutter gets a
  `bhkConvexVerticesShape` over the same triangles, reusing the hull builder that
  already serves Oblivion's clutter decomposition.
- **Inertia** is the solid-body tensor over the hull's AABB in Havok units. The
  Oblivion path scales an authored tensor by `_HAVOK_SCALE**2`; there is no authored
  tensor here, so it is computed directly in Havok units and needs no rescale.

**Vanilla census, `meshes/clutter`, 700 files / 248 layer-4 bodies with mass > 0:**

| Field | Measured |
|---|---|
| mass | min 0.5, p50 8.0, p95 30.0, **max 100.0** |
| `inertia.m_11` | min 0.00047, p50 0.217, max 30.96 |
| `motion_system` | 3 (225), 2 (23) |
| `quality_type` | 4 (248/248, zero exceptions) |
| shape | ConvexVertices 118, Box 81, Capsule 22, Mopp 15, List 10, Sphere 2 |

Max mass 100.0 is why `_MAX_MASS` clamps: Morrowind authors Stendarr's Hammer at
weight 1000, which is a real authored value but ten times heavier than anything
Havok simulates in vanilla. Convex shapes (ConvexVertices + List = 128 of 248)
dominate; the 15 Mopp entries are the reason a synthesized dynamic body is built
from `build_clutter_hull` rather than the static MOPP path.

## MO_SYS_FIXED (7) statics simulated as clutter — "floating / spinning / on its side" (SOLVED 2026-07-28)
<a id="mosysfixed-statics-simulated-as-clutter"></a>
Third-party plugin statics (streetlights, beds, tables, shrines, chests, torches) tipped onto their sides, drifted, or spun off through the air on cell load.
- **Cause**: `_convert_collision`'s static-vs-dynamic branch dispatched on **mass alone** (`elif rb.mass == 0:` → static, `else:` → dynamic). Oblivion `MO_SYS_FIXED` (7) — nif.xml: *"used for the static elements of a game scene, e.g. the landscape"* — was never consulted, so any fixed body with a non-zero mass field became a fully-simulated Skyrim prop with a mesh collision shape.
- **Why base Oblivion never showed it**: a 300-NIF census of `Oblivion.esm` found **198 ms=7 bodies, 0 with mass>0** — Bethesda always zeroes mass on fixed bodies, so mass alone happened to classify every one correctly. The inference was wrong but indistinguishable from correct on vanilla data.
- **Why Morroblivion did**: the same census over `Morrowind_ob.esm` found **186 ms=7 bodies, 157 with mass>0** (its idiom is `mass=1000` + `layer=1 OL_STATIC` for "static"). The majority of its statics were being converted into 1000 kg dynamic clutter.
- **Fix**: before the mass dispatch, `rb.motion_system == 7 and rb.num_constraints == 0` → `rb.mass = 0.0`, falling into the existing static branch (ms=5 BOX_STABILIZED, quality 0, mass 0). Constraint-owning fixed bodies are left alone — they are real trap/chain parts handled by the constraint branches.
- Measured: 125/227 sampled Morroblivion meshes corrected, 0 left dynamic; base-Oblivion clutter (ms=1/2/4, real masses) verified unchanged and still dynamic.
- **General lesson**: the source's declared motion type is the authoritative statement of static intent — never re-derive it from mass. A heuristic that is *accidentally* total on vanilla data will silently misclassify third-party content.

## Skyrim APPLIES rotation/translation on non-T bhkRigidBody (THE fundamental havok bug, found 2026-07-15)
<a id="skyrim-applies-rotationtranslation-non-t"></a>
The single most important havok-conversion fact, and the root cause of both "constrained objects act completely rigid" AND the longstanding "havok interactions feel weird on normal misc items":
- **Oblivion ignores the translation/rotation fields on plain (non-T) `bhkRigidBody`**, so Oblivion NIFs ship arbitrary leftover values there (chain links carried rotations up to ~115°).
- **Skyrim applies BOTH fields on BOTH body classes.** Proof: vanilla `trapmace01.nif` Base01 — the node is rotated +0.5° about X and the plain bhkRigidBody carries exactly the inverse quaternion (-0.0044,0,0,1) so its root-space MOPP stays aligned; every other vanilla non-T body is exactly identity/zero, unlike Bethesda's genuinely-garbage padding fields.
- Consequence of passing them through: every constraint frame and collision shape is rotated out from under the solver → constraint assemblies act welded solid; ordinary clutter collision sits askew from the visual mesh.
- Fix in `_convert_collision`: non-T bodies get translation=(0,0,0,0) AND rotation=(0,0,0,1). bhkRigidBodyT keeps its (scaled) transform. NOTE: field-level dumps looked "fine" for months because everyone (and the docs) believed the non-T fields were dead — when a converted mesh matches vanilla on every OTHER field, byte-diff the remaining "ignored" ones.

## Hoisted collision dropped the child node's ROTATION (SOLVED 2026-08-27)
<a id="hoisted-collision-dropped-child-nodes"></a>
"citadelballconystandardendleft02.nif has no collision" — but the output carried a
complete `bhkCollisionObject` + MOPP + CMS with 373 non-degenerate triangles. The
collision was **present and correctly formed, just parked in the wrong half of the
world**, so nothing the player walks on ever touched it.

Measured (game units): render Y `-956.8..-494.2`, collision Y `+508.7..+1035.6`,
Z off by ~286. X matched exactly — the tell that this is a transform bug, not a
geometry one.

Cause: Skyrim requires collision on the root, so `hoist_collision` moves it up from
a child NiNode — but it read **only `child.translation`**, and only for the two
strips shape types via `_offset_collision_shape_verts`. This mesh hangs its
collision on `collisionCitadelBallconyStandardEndRight04`, whose rotation is
`diag(1,-1,-1)` (180° about X). That flip was silently discarded, negating Y and Z.

**The A/B that isolates it**: sibling `citadelballconystandardendleft.nif` — same
author, same geometry, same 373-tri hull, same code path — converts correctly, and
its collision node's rotation is identity. The node rotation is the only
discriminator; the shape type, mesh and plugin are all constants across the pair.

Fix: `hoist_collision` now composes the child's FULL `(R, T, s)`:
- **`bhkRigidBody(T)`** → `bake_node_transform_into_body` (promotes to
  `bhkRigidBodyT`). Shape-agnostic, so it also fixes convex hulls, list shapes and
  primitives — none of which have a vertex array the old path could rewrite, so
  they had been dropping the translation too.
- **`bhkSimpleShapePhantom`** (trap-damage volumes, trigger zones) has no body
  transform field and cannot be promoted → `_wrap_shape_in_node_transform` composes
  `L` into a `bhkTransformShape` wrapper instead (composing into an existing
  transform shape rather than double-wrapping).

Safe against the bodyT+CMS CTD above: mesh collision folds any bodyT back into the
triangles in `_bake_body_transform_into_tris` and demotes the body to plain
identity, so no shipped CMS mesh gains a `bhkRigidBodyT`.

**Blast radius, measured over 28,470 exported NIFs**: 645 reach the hoist path; 55
have a non-identity rotation/scale there; 19 are creature assets (`creature=True`
skips the hoist) and 15 more take the `wrapped` path (which already baked rotation
correctly) — leaving **14 meshes that actually changed**, across Oblivion.esm and
Nehrim.esm. Verified: collision/render overlap ≥0.97 on the balcony pair, 0 issues
from `collision_sanity.py`, and the phantom's composed transform matches a hand
computation to 4 decimals.

The `wrapped` gate in `nif_converter.py` still skips hoisting — not because
rotation is unsupported any more, but because that case would have to compose the
WRAPPER's transform too.

## Inverted collision winding — "I fall through the floor" (SOLVED 2026-07-20; **rewritten 2026-08-20, see round 3 below**)
### <a id="packed-shape-vertex-scale"></a>A packed shape stores verts at 1/7 scale

`_offset_collision_shape_verts` must handle BOTH mesh shape types, and they
store vertices at DIFFERENT scales:

- `bhkNiTriStripsShape` — game units (x7 Havok units) -> add the offset as-is.
- `bhkPackedNiTriStripsShape` — 1/7 game units (Oblivion Havok units) -> the
  offset must be divided by 7 first.

Handling only the strips case silently dropped the offset for every
packed-shape mesh, leaving its collision centered on the origin while the
visual mesh sat elsewhere. Battlehorn's `stackstairsmid02b` is the case in
point: a `collisionStackBalconyMid02b` node at Z=+394.5 whose collision came
through at z[-332.8..332.8] instead of z[61.7..727.3] — the shape ends up half a
storey low, which on a stair/balcony wedge reads in-game as the collision being
flipped upside-down. Its sibling `stackbalconymid02.nif` has the identical node
offset but ships a `bhkNiTriStripsShape`, so it was always converted correctly
— the pair is the A/B that isolates the shape type as the discriminator.

<a id="inverted-collision-winding-i-fall"></a>
Falling through floors in Nehrim (worst in caves) that are solid in Oblivion. **Source-data corruption, not a conversion bug** — the converter faithfully reproduced broken input.

> **⚠ Read [round 3](#rewritten-2026-08-20-round-3--the-winding-is-authored-stop-inferring-it) before changing anything here.** Two claims in the sections below are now superseded: the repair is **no longer Nehrim-specific** (vanilla Oblivion has the same damage — `seisland.nif` ships 1480 inverted triangles, `meshes/rocks` measures 14.5%), and **"vanilla Oblivion is the control test for any detector" is FALSE**. The winding is recorded per-triangle in the NIF itself, so the primary repair no longer infers anything; the inferred steps described below still exist but are now gated to Morroblivion alone.
- **Symptom shape matters**: you fall through *half* a floor, not all of it. Collision is present, MOPP is clean, layer/material/orientation all correct.
- **Cause**: Nehrim re-exported collision as `bhkPackedNiTriStripsShape` (Oblivion ships `bhkNiTriStripsShape`). Flattening strips → triangle lists dropped the parity flip on odd-indexed triangles, so one half of every floor quad has reversed winding. **Havok mesh collision is single-sided** → a down-facing triangle is walked straight through from above.
  - `priorychapelinterior.nif` floor: Oblivion `(37,35,34)`+`(35,36,34)` both UP; Nehrim `(37,35,34)` UP + `(35,34,36)` **DOWN** (last two indices swapped). Floor area 719k → 359k, exactly half.
  - `rfrmfloor.nif` (fort/cave tileset, used in hundreds of cells): Oblivion `(0,1,2)`+`(1,3,2)` UP; Nehrim `(0,1,2)` UP + `(1,2,3)` DOWN.
- **Scale**: 1065/4485 Nehrim meshes vs 10/4199 vanilla Oblivion (~100×). Vanilla-Oblivion cleanliness is the control test for any detector here — if a scanner flags lots of Oblivion meshes, the scanner is wrong.
- **Fix**: `_repair_inverted_floors()` in `asset_convert/collision/collision.py`, called from `_rebuild_mesh_collision` after the body-transform bake (so triangles are in final orientation). Counter via `inverted_floor_flip_count()`.
- **Verify**: A/B a mesh with `_repair_inverted_floors` monkeypatched to a no-op and compare output hashes; count up/down near-horizontal faces in the decoded CMS (`asset_convert.collision.cms.decode_cms`) before/after.

### Rewritten 2026-07-28 (round 2) — it is a STRIP-PARITY bug, so solve it structurally
The first rewrite (coplanar contradiction + co-located visual face) scored 35.8% recall against ground truth and left `priorychapelinterior`, `skbridgesmall` and `rockgreatforest645lichen` unwalkable. Both it and the original z-band rule were **geometric guesses at a structural defect**.

- **What the data actually says.** Score a Nehrim mesh triangle-by-triangle against its Oblivion original (same relative path, identical vertices — the vanilla winding is ground truth) and print the correct/reversed flag in *packed triangle order*:

  ```
  priorychapelinterior  ++++++++++++++++++++-+-+-+-+-+-+-+-+-+-...-+-+-+-..+-+-+-
  skbridgesmall         ++++++-+-+-+-+-+-+-+-+-++-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
  ```

  It **strictly alternates**. 97.2% of reversed triangles are explained purely by position within a flattened strip, and of 301 decided strip runs, **301 have the same phase** (first triangle correct, alternating after). This is exactly the dropped `bhkPackedNiTriStripsShape` parity flip the section above describes — no geometry required to find it.
- **New design — two steps, the first exact.**
  1. **Relative orientation (`_orient_components`).** Two triangles sharing an edge are consistently wound **iff they traverse that shared edge in opposite directions** — the standard manifold-orientation test. Weld coincident vertices, BFS the shared-edge graph, flip whatever disagrees. No thresholds, no normals, no flatness. It undoes the dropped parity exactly and is **completely inert on correctly wound input**.
  2. **Absolute sign (only where step 1 cannot help).** Step 1 makes a component self-consistent but cannot tell outward from inside-out, because flipping *every* triangle of a component is also self-consistent. So per component: a **closed** component must enclose positive volume; otherwise the **render mesh** decides (artist winding is correct by construction). Undecided ⇒ leave alone.
- **Weld per geometry group** (`shape_tri_groups`). A shape can hold several independent pieces — one `NiTriStripsData` block each, or one packed sub-shape each — that merely touch in space. Welding across that seam fuses them into one component and forces a single orientation on both.
- **The visual vote needs a quorum, not more geometry.** Every false positive measured on already-correct collision had `cov == 1`: a single stray facet (the far skin of a slab, or a decorative mesh passing nearby) condemning a whole component. Requiring **half the component to have seen evidence**, with trust radius `0.30` hu, removes them and still fixes uniformly-reversed floors. Attempts to separate the cases by *geometry* instead (signed-volume floor, area-normalized "solidity") both failed — volume is meaningless on the open sheets that dominate here.
- **Result** (shipped code, scored against the Oblivion originals):

  | Tree | recall | broken |
  |---|---|---|
  | dungeons (114 meshes, 33k tris) | **99.9%** (13,123/13,135) | **0** |
  | architecture (155 meshes, 53k tris) | **99.7%** (17,310/17,354) | 112 (0.32%) |
  | rocks (147 meshes, 51k tris) | 99.1% (24,467/24,693) | 268 (1.00%) |
  | the 3 reported failures | **99.8%** (494/495) | **0** |

  Previous code scored **35.8%** on the same corpus. Floor-regression sweep (`--floor-regress`): **0** walkable floors turned into fall-throughs across 300 vanilla Oblivion and 300 Nehrim architecture meshes. `inuhlaaluuroomuside` still converts walkable (the sign step flips its 4-triangle floor component).
- **"Vanilla is clean" is FALSE for the SI bridges.** `dementiabridge01` has **242 of 324 shared edges inconsistently wound** in vanilla Oblivion. A safety harness that counts "triangles the repair changed" on vanilla will report ~1.4% false positives that are actually genuine repairs. Measure the invariant that matters instead — *does a walkable floor become a fall-through one* — which is what `--floor-regress` does.
- **Do not weld across strip-data blocks when auditing.** An early safety test flattened `bhkNiTriStripsShape` into one soup and reported 272 vanilla "false positives"; evaluating each `NiTriStripsData` separately (as the shipped code does) gave **0**. The artifact was the harness, not the algorithm.
- **Known limit — 3 Morrowind_ob meshes lose a floor** (`morro/f/actubmurootu01`, `morro/i/actusothautenderizer`, `morro/i/actusothauoilbridge`; the same sweep *fixes* 2 others). Cause is the **relative pass**, not the sign step: `--floor-regress` still reports them with the visual oracle disabled entirely, and the visual radius makes no difference (swept 0.10→0.30, identical outcome). The BFS anchors each component on whichever triangle it reaches first, so when the floor is the minority of a large component the whole component comes out inverted. For `actubmurootu01` this is arguably correct anyway — its render skin at distance 0.000 faces **down** (`agree = -1.00`), i.e. the source mesh is self-inconsistent.
- **Two tie-breaks were tried against this and BOTH were reverted** — record them so they are not re-attempted:
  1. *Minority-flip in the relative pass* (choose whichever sign flips fewer triangles, since orientation is only defined up to a global sign). Did **not** fix the Morrowind meshes and cost Nehrim recall: 99.7%→98.6%, broken 112→273.
  2. *Floor-preserving tie-break in the sign step* (when volume and visual both abstain, pick the sign that keeps the lowest surface up-facing). Also did not fix them — the visual vote **does** reach quorum on these components (`cov=80/80`) so it never abstains and the tie-break never runs — and it hurt Nehrim badly: 99.7%→97.5%, broken 112→818.

  The lesson both times: these components are decided by *confident* evidence, so a fallback cannot reach them, and any rule strong enough to override the evidence damages the 99.7% case. Fixing this properly needs a better component seed, not another tie-break.
- **Morrowind_ob scale check** (400 `morro/` meshes, `--floor-regress`): 1 fixed, 3 regressed — all three in the `morro/f/` flora family (`actubmurootu01`, `floraurootuwgu01`, `floraurootuwgu05`), i.e. root/plant props rather than walkable architecture. For context `--floor-orientation` shows **58 of those meshes already fall-through in the source before any repair**, so this tree is broadly broken upstream and is not the case the repair is tuned for. Nehrim and vanilla Oblivion — the trees with real ground truth — regress **0** floors across 600 architecture meshes.
- **Tooling**: `tools/nif/collision_winding.py --ab <ref_tree>` (exact recall/breakage vs ground truth) and `--floor-regress` (the in-game invariant). Run both before shipping a change here.

### Rewritten 2026-08-20 (round 3) — the winding is AUTHORED; stop inferring it

Round 2 solved Nehrim but was gated per-plugin and off for vanilla, on the
premise that "vanilla Oblivion is authored correctly". **That premise is
false**, and the gate hid a signal that was in the file the whole time.

- **Vanilla Oblivion falls through its own floors.** `rocks/seisland/seisland.nif`
  (the Shivering Isles island, `STAT 00078C2C`, placed at scale 1.0 in two
  worldspaces) ships **1480 of 3590** collision triangles wound against their
  own recorded normal. Downward-raycast over its top surface: **538 walkable /
  432 fall-through**. Across `meshes/rocks`, **14.5%** of decidable floor faces
  are inverted in vanilla. The converter reproduced this faithfully — it was
  never a conversion bug, and the per-plugin gate meant it was never repaired.

- **THE AUTHORED INDICATOR: every collision triangle records the direction it is
  meant to face, independently of the winding that produces that facing.**

  | format | field |
  |---|---|
  | `hkPackedNiTriStripsData` | `triangles[i].normal` — one per triangle |
  | `NiTriStripsData` | `normals[]` — per **vertex**, averaged per face |

  A strip flatten reverses the winding and carries the stored normal through
  **unchanged**, so `dot(face_normal(tri), stored_normal) < 0` is the file
  stating which of its own triangles are damaged. No adjacency walk, no oracle
  mesh, no thresholds, and inert wherever the two already agree. On seIsland it
  identifies 1480 triangles — matching the 1487 the round-2 heuristic flips —
  and rewinding to it alone gives **970 walkable / 0 fall-through**.

  Cross-checked against the render-skin oracle (`collision_winding_truth.py`,
  which is independent of both winding and normals):

  | tree | authored normal agrees with render truth |
  |---|---|
  | Oblivion architecture | **98.6%** |
  | Nehrim architecture | **97.9%** |
  | Morroblivion `morro/i` | **60.0%** |

- **This is now STEP 0 and it is UNGATED.** `_shape_tri_normals()` extracts the
  normals index-aligned with `_shape_tri_soup` (**same degenerate-triangle
  filtering — edit the two together or normals bind to the wrong faces**), and
  `_repair_inverted_floors` applies them before consulting the toggle at all.

- **Steps 1-3 are INFERENCE and stay gated, because inference costs false
  positives.** Step 1 seeds each welded component from an arbitrary triangle and
  propagates that choice, so a component seeded inward inverts wholesale:
  `architecture/castle/leyawiin/leyawiincastle02.nif` is ONE `NiTriStripsData`
  block of 1849 triangles that welds into **40 disconnected components**; step 1
  flipped **274 of 284** triangles in one of them (96% — the tell that the seed
  was in the 4%) and cost **806 walkable raycast cells on a vanilla mesh**.
  Step 2 nearly caught it (visual vote 11:1 against, well past `_VIS_MARGIN`)
  but abstained: the component is not closed, and coverage was **137/284**,
  five short of the quorum. Measured over Oblivion architecture, step 1 alone
  takes 43 inverted faces to **103**.

- **Nehrim no longer needs the gate.** Its exporter left the normals intact, so
  step 0 alone does the job and the inference risk is not worth taking:

  | tree | raw | step 0 only (SHIPPED) |
  |---|---|---|
  | Nehrim dungeons | 46.07% inverted | **0.09%** |
  | Nehrim architecture | 24.17% | **0.70%** |

- **Morroblivion still needs it, and is the ONLY default member.** Its exporter
  rewrote each normal to match the winding it emitted, so both agree while both
  are wrong and step 0 has nothing to detect. `morro/i/inuhlaaluuroomuside.nif`:
  all 10 triangles score `dot = +1.0` over a floor (`z = -123`, under a ceiling
  at `z = +97`) you fall straight through. Across `morro/i` the authored normals
  change **0 of 5496** inverted faces; steps 1-3 take it to **20**.

- **⚠ MEASURING AN ENCLOSED MESH: a downward raycast sees the CEILING.** This
  cost two wrong conclusions in one session — that `inuhlaaluuroomuside` was
  unrepaired (it is repaired; its floor is simply under a roof) and that the
  round-2 docstring was stale (it is accurate). For a room, score the **lowest**
  surface, or use the render-skin oracle. Never read a top-down "fall-through"
  count on interior geometry.

- **Result, scored on the SHIPPED output against the source render skin:**

  | tree | raw | shipped |
  |---|---|---|
  | Oblivion rocks | 17.8% | **0.10%** |
  | Oblivion architecture | 0.81% | **0.43%** |
  | Oblivion dungeons | 0.10% | **0.10%** (inert) |
  | Nehrim dungeons | 46.07% | **0.09%** |
  | Nehrim architecture | 24.17% | **0.70%** |

  `leyawiincastle02` ships **847 walkable / 0 fall-through**, identical to its
  source — zero false positives.

- **Caveat on the numbers above.** The render-skin oracle shares its signal with
  step 2's `_component_visual_vote` (same coincidence rule, same constants), so
  scores for variants *containing step 2* are partly self-graded and read high.
  The step-0 figures do not depend on it — they come from reading normals
  directly out of the files. The oracle also has a documented thin-slab trap
  (chairs, benches, stairs), which is why Oblivion clutter/furniture measure
  ~9-12% "inverted" raw while `lowerclasschair01` and `lowerclassbench01` in
  fact have **0** triangles disagreeing with their normals.

### <a id="concave-hull-decomposition"></a>Concave clutter hull decomposition

**Code:** `asset_convert/collision/collision_hulls.py`

One convex hull over a concave prop — a chair, a cart, a rack — blocks every
gap the shape should leave open. The hull is therefore cut recursively along
whichever axis wastes the most volume and shipped as a `bhkListShape` of
convex pieces.

- **The cut must pay for itself.** A split is accepted only when the two piece
  hulls together lose at least `1 - _DECOMP_SPLIT_GAIN` of the parent's volume,
  so a genuinely convex shape stays a single piece.
- **An axis under 3 game units is too thin to split**, and a piece under
  `_DECOMP_MIN_PIECE_VERTS` vertices is rejected.
- **Points near the cut are shared by BOTH halves**, so the piece hulls overlap
  slightly and leave no gap. Each half has to reach past the first vertex
  "ring" on the far side of the cut — sparse vertex rows otherwise leave an
  unfilled band of collision between the two hulls.
- **Vertices are quantised to a grid** (0.004 / 0.008 / 0.015 havok units =
  0.28 / 0.56 / 1.05 game units) to keep hull vertex counts in the vanilla
  range, taking the first grid that lands under `_DECOMP_MAX_HULL_VERTS`.
- **scipy facet equations are triangulated**, so coplanar planes are deduped.
  The convention is `n·x + d <= 0` inside, with `n` an outward unit normal.
- **Face planes are pushed out by the convex radius.** The plane sits at
  `n·x = -w`, and vanilla stores face distance = vertex distance + radius.

### <a id="havok-material-crc"></a>Material values: enum index vs CRC

**Code:** `asset_convert/collision/collision_material.py`

Oblivion stores a havok material as a small enum index (0-31); Skyrim stores a
CRC of the material's name. Values ≤ 31 are therefore Oblivion indices and
anything larger is already a Skyrim CRC, which makes `convert_materials`
idempotent — safe to call on a partially converted tree.

`set_havok_material` assigns `it._value` directly instead of calling PyFFI's
`set_value()`. **`EnumBase.set_value` only logs a warning and RETURNS when the
value is not in its enum list**, and every Skyrim CRC is outside the
Oblivion-era list PyFFI matched at read time, so `set_value` would silently do
nothing. PyFFI also instantiates one enum item per read context, typed as
whichever variant matched the source version, which is why the write loops over
`_items` rather than touching a single field.

### <a id="winding-repair-steps"></a>The four steps, and what each one can see

**Code:** `asset_convert/collision/collision_winding.py`

| Step | Question it answers | Gated |
|---|---|---|
| 0 — authored normal | Does this triangle contradict its own recorded normal? | no |
| 1 — relative orientation | Do edge-neighbours traverse the shared edge in opposite directions? | yes |
| 2 — absolute sign | Which way does this whole COMPONENT face? | yes |
| 3 — walkable repair | Which way does this ONE floor triangle face? | yes |

Step 1 is exact and threshold-free: two triangles sharing an edge are
consistently wound if and only if they traverse it in opposite directions, so a
breadth-first walk of the shared-edge graph settles a whole connected component
from whichever triangle it starts on. It undoes a dropped strip parity exactly
and is inert on correctly-wound input.

Step 2 supplies the absolute reference step 1 cannot: flipping every triangle
of a component is also self-consistent. A CLOSED component must enclose
positive volume; otherwise the render mesh decides, since the artist's visual
winding is correct by construction. The visual vote needs a quorum — at least
half the component's triangles must have seen a qualifying visual face. Every
false positive measured on already-correct vanilla collision came from a single
stray facet (typically the far skin of a thin slab: an altar top, a shelf, a
step tread) condemning a whole component. Where neither test is decisive the
component is left alone: a lone down-facing surface is a perfectly valid ceiling
or overhang, and flipping it would punch a new hole.

### <a id="step-3-walkable-repair"></a>Step 3 — per-triangle walkable repair

Step 2 decides one sign for a WHOLE component, which is right for a uniformly
reversed surface but blind to a small patch inside a large one.
`exUdeUship`'s raised foredeck is 12-22 triangles inside hull components of
384-610, so the hull's correct faces outvote it ~1000:1 and the deck stays
inverted — you fall through the front of the chargen ship.

It is blind for a second reason too: the deck is a ZERO-THICKNESS double-sided
sheet in the render mesh, so its up skin and down skin share a plane (measured
mean z 3.6533 vs 3.6787). Step 2 weights votes by 1/distance, so the coincident
down skin scores ~200000 against the real walkable skin's ~33 and "nearest face
wins" picks the wrong one.

So step 3 asks the question that actually matters for a floor, and asks it PER
TRIANGLE: of the render faces **coincident** with this down-facing collision
face, which way does the nearest one point? Coincident, not merely nearby — the
render face has to BE this surface for its normal to settle the question, which
is what makes the double-sided sheet decidable (the true skin sits ~0.004 away,
any other surface is 0.2+). A face with no coincident render skin is a genuine
underside or overhang and is left alone.

The test is COINCIDENCE, not proximity, so the tolerances are tight. Measured on
`exUdeUship`'s foredeck the walkable skin sits at dxy 0.003-0.006 / dz 0.002
from its collision face, while the nearest DOWN skin is 0.2-1.6 away — three
orders of magnitude of separation, so this is a wide margin, not a knife edge.

Only near-horizontal faces are considered: a wall's sidedness is not decidable
this way, and Havok single-sidedness only strands the player on floors.

**Measured on `exUdeUship` by downward raycast** (the engine's own test):
fall-through cells 10 → 0, walkable 92 → 102. Vanilla control sweeps (400
architecture + 400 dungeon + 61 ship meshes) report 0 regressions.

Candidate visual faces are bucketed into an XY grid at `_FLOOR_XY` cell size so
a collision face tests the handful of visual faces above and below it rather
than all of them — the whole-mesh scan is O(collision × visual) and this runs
inside the per-mesh worker.

### <a id="welding-is-per-group"></a>Welding is scoped PER GROUP

The packed triangle list stores each triangle's corners independently, so
without welding to shared vertex indices no two triangles ever share an edge and
step 1 is a no-op.

Welding is scoped per group (see `shape_tri_groups`). Independent pieces of a
shape frequently touch — a bridge deck resting on its posts, a stair block
against a landing — and welding across that seam would fuse them into one
component, forcing a single orientation on both.

## `bhkPackedNiTriStripsShape` sub-shapes MOVED between formats — load CTD (SOLVED 2026-07-28)
<a id="bhkpackednitristripsshape-sub-shapes-moved-between"></a>
Three crashes in converted Morrowind_ob traced to one mesh, named directly in the crash log's stack strings (`inputFilePath: "data\MESHES\tes4\morro\i\inucaveuplant00.nif"`). Exception was `vmovntdq [rcx+0x40], ymm3` in VCRUNTIME140 (a `memcpy`) writing off the end of a heap page, with `bhkPackedNiTriStripsShape` + `bhkRigidBody` + `BSResource::LooseFileStream` on the stack — i.e. **a crash while reading the NIF, before anything renders**.
- **Root cause — the sub-shape list changed owner between the two NIF versions** (`references/nif 0.10.0.0.xml`):
  - `bhkPackedNiTriStripsShape.Num Sub Shapes` — `until="20.0.0.5"` (Oblivion)
  - `hkPackedNiTriStripsData.Num Sub Shapes` — `since="20.2.0.7"` (Skyrim)

  Our builders wrote the count onto the **shape** (the Oblivion field, not even serialised at Skyrim's 20.2.0.7), so the **data** block shipped `num_sub_shapes = 0` while carrying real geometry. Skyrim sizes its sub-part allocation from that count, then memcpys the vertex/triangle payload into the undersized buffer → access violation on load.
- **Fix**: `_set_packed_sub_shape()` in `asset_convert/collision/collision.py` writes the covering sub-shape to **both** fields (correct at either version). Called from `_packed_from_tris`, `_ni_strips_to_packed`, and the `bhkListShape`-child path.
- **Three independent defects, same symptom** — all had to be fixed:
  1. `_packed_from_tris` / `_ni_strips_to_packed` set only the Oblivion-side count.
  2. `_convert_shape` returned a `bhkPackedNiTriStripsShape` **completely unconverted** (`return shape`) when it appeared as a `bhkListShape` child — no rescale, no sub-shape migration. This is how `crescentblade.nif` and 4 Oblivion.esm meshes were hit; the standalone case never reached it because `_rebuild_mesh_collision` handles that first.
  3. Degenerate hulls (below).
- <a id="degenerate-hulls-mopp-retry"></a>**Degenerate collision hulls crash the MOPP bridge**: the bridge access-violates (`rc 3221225477`) inside "computing two-sided welding" on very small hulls — Havok divides by near-zero edge lengths. Verified by scale sweep on `inucaveuplant00`: ×1 crashes, ×10/×100/×1000 all succeed. The duplicated reversed triangle in that mesh is *not* the trigger (A/B tested — crashes with and without it). **This is not Morroblivion-specific**: vanilla Oblivion `paintbrush01/02/03` (0.034 hu) hit it too.
  - **Fix is a scaled rebuild, not a drop** (`cms_builder.build_cms_collision`): MOPP encodes geometry as `(v - origin) * scale`, so building the bytecode over vertices scaled by `k` and storing `origin/k` with `scale*k` is an **exact** restatement for the original geometry — no approximation, and the CMS chunk data stays at native scale. Retries k=10/100/1000. Verified on paintbrush01: MOPP origin reproduces the source AABB minimum on all 3 axes, decoded CMS AABB matches the source, `walk_mopp` returns 0 errors and its terminal key set equals the CMS key set.
  - **Dropping is now only for sub-viable hulls**: `_MIN_HULL_EXTENT = 0.01` hu (≈0.07 game units, sub-millimetre) — far below the smallest vanilla Skyrim hull (**0.179 hu**, censused from vanilla clutter CMS). Morroblivion's `inucaveuplant00` is 0.0098 hu against a 73.5-game-unit visual mesh (~1000× too small), so its collision is meaningless and the collision object is removed (`collision_object = None`). Counter: `degenerate_hull_drop_count()`. Do **not** raise this to catch things like the paintbrush — that clutter must stay grabbable, and the scaled retry already gives it real MOPP.
- **Vanilla control**: **0 of 17,216** vanilla Skyrim meshes contain `bhkPackedNiTriStripsShape` at all — Skyrim always ships MOPP+CMS. That was read here as "our fallback must at least be structurally valid"; **2026-08-22 corrected it to "never emit it"** — a structurally-valid shape of a type the engine does not support still corrupts the heap on load.
- **Scale of the bug**: 3 meshes in Morrowind_ob, 4 in Oblivion.esm; 0 in Nehrim/SI. After the fix the paintbrushes and `crescentblade` get real MOPP+CMS (collision preserved), the two `inucaveuplant` hulls are dropped, and the only remaining packed shape is `gnarlspawner.nif`'s **`bhkSPCollisionObject` trigger phantom** — a deliberately-preserved type (see the constrained-objects section) that goes through `_convert_shape`, not the rebuilder. It now ships `data.num_sub_shapes = 1`, so it is structurally safe even though it keeps the fallback shape. **SUPERSEDED 2026-08-22:** keeping the fallback shape at all was wrong -- Skyrim never loads that type, and it caused the 2 GB-memcpy heap corruption documented below. `gnarlspawner`'s phantom now carries MOPP+CMS (the `bhkSPCollisionObject` / `bhkSimpleShapePhantom` types are still preserved; only the shape inside changed, geometry exact at 120->120 and 124->124 triangles), and the packed shape is no longer emitted anywhere.
- **Verify**: `python tools/nif/nif_block_scan.py output/<plugin>/meshes --has bhkPackedNiTriStripsShape`; any hit must have `data.num_sub_shapes >= 1` covering all vertices. Note the scanner reads the header block-type *table*, so it flags a file whose table still lists the type — confirm with a block walk before concluding a real shape is present.

## Constrained objects: chains, swinging traps, gates, trigger phantoms (2026-07-15)
<a id="constrained-objects-chains-swinging-traps"></a>
Besides the non-T rotation root cause above, the "chains/traps look right but never move when touched/grabbed" cluster had four more independent causes, all in `asset_convert/collision/collision.py`:
1. **Constraint max_friction**: Oblivion ships 3.0 (limited hinge) / 10.0 (ragdoll); in Skyrim that much joint friction locks the joint solid. Vanilla Skyrim prop constraints use **0.01** (desecratedimperial.nif ragdolls, spitpot hinges, tavern signs). Clamp >0.5 → 0.01 in BOTH `_fix_limited_hinge` AND `_fix_ragdoll` (the sign fix originally only covered limited hinge — chains use ragdoll constraints).
2. **Collision-filter layer remap** (`_remap_world_filter`): Oblivion layers 0-18 equal Skyrim's, but 19+ diverge and Oblivion authored world props on ragdoll bone layers (cellchain01 anchor = 42 OL_L_FOOT → Skyrim PATHPICK = raycast-only, NO collision). Body-part layers 33-57 → 10 SKYL_PROPS (vanilla trapmace links' layer); pick layers shift +15; stairs 19→31, char controller 20→30, avoid box 21→34.
3. **Filter flags/part byte + group must be zeroed** on world objects: Oblivion chains ship 0x80|partnum ("Linked Group" bit 7 + biped part); vanilla Skyrim constrained props ship 0 and rely on runtime per-reference group assignment. Biped part numbers only mean anything on layer 8/32/33.
4. **Oblivion MO_SYS_KEYFRAMED (6) is a THREE-WAY split** (this took three in-game test rounds to get right — both simpler rules made things worse):
   - node driven by animation (`_node_is_animated`: sequence controlled-blocks, NiMultiTargetTransformController extra targets, or a transform controller on the node) → Skyrim KEYFRAMED (ms 4, quality 1, collObj 137, node flag |0x80, **mass forced to 0**). Gate leaves (mass=100, no constraints, Open/Close sequences) — treating gates as dynamic made them "fall off their hinges" (they have NO hinge constraints; they swing by animation). **The mass-0 forcing is mandatory**: a keyframed body with non-zero mass is an active simulated island, so physics overwrites the animated transform every step and the object never visibly moves even though the sequence plays normally (vanilla: 11/11 keyframed bodies are mass 0). See "A keyframed body MUST have mass 0" above.
   - mass>0 AND owns a constraint → DYNAMIC. Oblivion marks entire swinging traps keyframed ("Unyielding=1" links) because ITS engine holds traps rigid until the trap script enables havok; Skyrim's trapmace01 ships the same links DYNAMIC (ms 3, quality 4). Keyframing them = trap welded solid.
   - everything else (constrained-island anchors cellchain01/cellChainMiddle mass=100, unyielding props) → STATIC with mass forced to 0. Vanilla chain/noose/trap anchors are ALWAYS static mass-0 bodies (NooseRopePiece01 root, trapmace Base01), NEVER keyframed — a keyframed body with anim flags on a non-animated object flips the engine into the baked path and the whole compound (all its dynamic children included) acts welded solid.
- **Trigger phantoms are SUPPORTED by Skyrim** — do NOT strip `bhkSPCollisionObject`/`bhkSimpleShapePhantom`: vanilla ships 31 under meshes/traps alone (tripwire, pressure plates, bear trap), always collObj flags=129 + layer 12 TRIGGER. Convert the inner shape (×0.1 + material) and keep. Stripping them killed every Oblivion trigger volume (tripwire never fired).
- Vanilla reference meshes: `traps/macetrap/trapmace01.nif` (swinging mace analogue), `traps/tripwire/traptripwire01.nif`, `clutter/woodfires/spitpot*.nif` (hinge), `clutter/deadsoldiers/desecratedimperial.nif` (prop ragdoll constraints).
- Debug tool: `python tools/nif/havok_constraint_dump.py <nif|dir>` prints per-body filter (layer/flags/group), inertia, motion/quality, damping, and full constraint descriptors (pivots/axes/limits/friction) — the scene-tree analyzers hide all of this.

## Activation pick region (HUD rollover "too big" on clutter) — SOLVED 2026-07
<a id="activation-pick-region"></a>
- Skyrim's crosshair activation is a PRECISE raycast against the Havok collision shape (user-verified: vanilla prompts appear only when the cursor is exactly on the mesh; `fActivatePickRadius` INI had no effect). An earlier theory blaming engine INI slop was WRONG.
- Root cause: Oblivion clutter ships ONE bhkConvexVerticesShape hull per object. A convex hull FILLS EVERY CONCAVITY — a goblet's hull fills the waist around the thin stem (collision radius 2.7-3.0 vs visual 1.6), a pitcher's hull fills the entire handle gap (y ±4.7 where the visual handle is ±0.53). AABB comparisons hide this (hull AABB == visual AABB exactly); compare CROSS-SECTIONS at concave features instead. Vanilla authors compound shapes instead (glazedgoblet01 = bhkListShape of cup box + stem box).
- Fix (`_decompose_clutter_hull` in collision.py): dynamic (mass>0) plain-bhkRigidBody single-convex-hull clutter is rebuilt as a bhkListShape of per-piece hulls: recursive binary split of the VISUAL vertices along the axis-aligned cut minimising total hull volume (scipy ConvexHull; accept cut if ≥10% volume gain, depth ≤3 → ≤8 pieces). Each half extends past the first vertex ring on the far side of the cut, or sparse vertex rows leave unfilled collision bands between pieces. Piece planes = scipy hull equations deduped, w = d − radius (vanilla stores planes pushed out by the convex radius). bhkRigidBodyT excluded (shape frame ≠ node frame). Frame sanity check vs the original hull AABB bails out when collision was authored differently from visuals. Result: goblet stem 2.7-3.0 → 1.8-2.4 (tighter than vanilla's box corners), pitcher handle strip y ±0.6.
- **Havok material conversion (was missing entirely)**: Oblivion materials are a 0-31 enum; Skyrim materials are CRC32 hashes (SkyrimHavokMaterial, values in references/nif 0.10.0.0.xml). `_convert_materials()` in collision.py maps them (`_OB_TO_SK_MATERIAL`); unmapped values leave the engine with an unknown material (no impact sounds/decals/stair-walk flag). **PyFFI trap: EnumBase.set_value() only LOGS "invalid enum value" and returns** for values outside its old enum list — must write `item._value` directly. PyFFI instantiates ONE material item per read context (typed OblivionHavokMaterial even when reading Skyrim CRC files — repr shows `<INVALID (...)>`, harmless; read/write via `_get_havok_material`/`set_havok_material`).
- **Inertia scale regression**: collision.py had drifted to `_INERTIA_SCALE = 0.1` with a bogus justification comment ("Havok normalizes by body scale internally"). Correct value is `_HAVOK_SCALE**2 = 0.01` (inertia ∝ mass·length², lengths scale 0.1) — verified: vanilla silverjug01 stores I_x=0.031 = m(3r²+h²)/12 exactly in SI/Havok metres. The 0.1 scale left inertia ~10× too large → sluggish rotation / "too much inertia" feel when grabbing or knocking clutter. (tests/test_asset_convert.py `_INERTIA_SCALE = 0.1` still asserts the old value and needs updating.)
- Note on masses: Oblivion authored masses differ per-item from Skyrim equivalents with no consistent ratio (OB silver pitcher 8.0 vs vanilla silver jug 0.8, but OB ceramic goblet 0.4 ≈ vanilla goblets 0.5-0.8) — masses stay unconverted.
- tes4/tes5_nif_analyzer print `BoundSphere` (NiTriShapeData center/radius) and bhkConvexVerticesShape vertex `extents` for this kind of investigation.

## NIF bhkMultiSphereShape (dead in Skyrim, fixed 2026-07-05)
<a id="nif-bhkmultisphereshape"></a>
- **0 of 17,216 vanilla Skyrim meshes ship bhkMultiSphereShape** (deprecated Havok path). The only Oblivion source that has one is `clutter\magesguild\apparatusalembicnovice.nif`, and shipping it converted CRASHES SSE at cell load (Anvil Mages Guild) with no crash log. Vanilla expresses the same thing as ConvexTransform+Sphere children in a list shape (`clutter\kitchen\woodenladle01.nif`).
- `_expand_multisphere()` in collision.py expands it: each sphere → a `bhkSphereShape` (radius ×0.1) wrapped in a `bhkConvexTransformShape` (identity rotation, sphere center ×0.1 in the 4th column, 4th matrix row all zeros incl. m_44 — matches vanilla). 1 sphere → bare wrapper, N → bhkListShape. `_convert_shape`'s bhkListShape branch now FLATTENS a nested list produced by the expansion (a list shape has no transform of its own so flattening is safe; vanilla never nests list shapes).

## Constraint descriptor conversion
<a id="constraint-descriptors"></a>

**Code:** `asset_convert/collision/collision_constraints.py`

Oblivion's constraint descriptors are missing fields Skyrim's Havok 2010
layout requires. PyFFI leaves them zero, which ships a degenerate or singular
basis, so each joint kind needs its own derivation.

### <a id="limited-hinge-fix"></a>Limited hinge

1. **`perp_2_axle_in_b_1` does not exist in Oblivion's descriptor.** Left zero
   the sign spawns at a wrong tilt. Derive as `perp_b2 × axle_b`, normalized.
   Vanilla Skyrim stores `w=-1` on both `perp_2_axle_in_a_1` and
   `perp_2_axle_in_b_1`.
2. **Clamp `max_friction`.** Oblivion stores 3.0; Skyrim signs use 0.01. At
   3.0 the hinge has enough rotational friction to lock the sign at any angle
   against gravity, so it stops at a wrong tilt instead of swinging back to
   vertical.

### <a id="ragdoll-descriptor-fix"></a>Ragdoll descriptor

In the Havok 2010 layout twist / plane / motor are the three columns of an
orthonormal basis — **motor = twist × plane** (verified on vanilla
`desecratedimperial.nif`: twist=(1,0,0), plane=(0,1,0), motor=(0,0,1)).
Oblivion's layout has no motor fields at all.

`max_friction`: Oblivion chain and trap ragdoll constraints store 10.0, at
which the joint locks solid — chains and swinging traps LOOK fine but never
move when touched. Vanilla Skyrim prop ragdoll constraints use 0.01, the same
value the limited-hinge clamp uses.

`friction_target` selects the contract: **0.01 for props, 0.0 for creature
blend joints** — vanilla creature skeleton.nifs are 89/89 constraints at
exactly 0.0 (dog / wolf / sabrecat / skeever census 2026-08-08), matching
their skeleton.hkx ragdolls. Earlier code exempted creature joints from the
clamp entirely on the false premise that "the vanilla creature census mixes
10.0/0.5/0.01"; carrying Oblivion's 10.0 through is what stopped corpses
falling over.

### <a id="hinge-descriptor-fix"></a>Plain hinge

Oblivion stores only `pivot_a`, `perp_a1`, `perp_a2`, `pivot_b`, `axle_b`.
Skyrim also needs `axle_a` and `perp_2_axle_in_b_1/2`; left zero the hinge
axis is degenerate. Frame convention (per nif.xml) is `perp2 = axle × perp1`,
so `axle_a = perp_a1 × perp_a2`. For the B side only `axle_b` is known, and
any orthonormal complement works because a plain hinge has no angle limits —
build `perp_b1` by Gram-Schmidt from `perp_a1`, then
`perp_b2 = axle_b × perp_b1`. When `perp_a1` is parallel to `axle_b`, fall
back to whichever world axis is not.

### <a id="plain-hinge-promotion"></a>Plain hinges are ILLEGAL in a ragdoll — promote to limited hinge

A `bhkHingeConstraint` on a ragdoll body crashes SSE at actor Load3D
(2026-09-07, the FalloutNV sentry turret). Read out of the GOG/AE exe:
the ragdoll attach (id 63792) calls `hkpConstraintUtils::convertToPowered`
(id 62885, `0xb04e80`), which dispatches on the constraint data's `getType()`
and accepts **only type 2 (limited hinge) and type 7 (ragdoll)** — the
`0xb04ebb`/`0xb04ec4` compares. Every other type takes the error path, which
emits

    Cannot convert constraint "<name>" to a powered constraint.
    Only limited hinges and ragdoll constraints can be powered.
    Constraint\Bilateral\hkpConstraintUtils.cpp

and returns **NULL** (`0xb04faa xor eax,eax`). The caller stores that in `rdi`
(`0xb34443`) and immediately calls `hkReferencedObject::removeReference`
(id 57011) on it **unguarded** at `0xb34459`, so `rcx = 0` and the crash lands
on `cmp word ptr [rcx+8], 0` — id `57011+0x6`, returning to `63792+0x4EE`.
Note the sibling call at `0xb344ea` guards the same call with `test rbx,rbx`;
this one does not.

The crash is therefore about the joint's TYPE, not its geometry (the turret's
hinge has fully populated axes and pivots) and not the tree order (its tree is
a clean chain: 4 bodies, bare root, each joint to the previous body).

**Census.** 50 vanilla actor skeletons carry 853 ragdoll constraints: 453
`bhkRagdollConstraint` + 400 `bhkLimitedHingeConstraint` and **zero** plain
hinges. Skyrim never ships one in a ragdoll. Ours had 3 of 5,006 across 296
rigs, all FalloutNV: `sentryturret`, `minisentryturret`, `zaxeye`.

**Fix** (`promote_plain_hinge`, applied from `_chosen_joints` so it runs on
exactly the joints the shared `plan_ragdoll_tree` keeps): rewrite the block as
a `bhkLimitedHingeConstraint` with limits ±pi. The seven frame vectors are
common to both descriptors, so it is a field copy; `_fix_limited_hinge` then
derives the B-side perpendicular as for any other limited hinge. ±pi is a full
circle, which is the free rotation a plain hinge means — the joint binds only
at the wrap point, which a corpse never reaches under the motor.

The hkx side already did this implicitly: `_joint_info` has no `plain_hinge`
branch, so a plain hinge fell through to the `else` and got ±pi limits inside
an `hkpLimitedHingeConstraintData`. That made the two files disagree on the
joint's type while the ENGINE USES THE NIF's copy (it overwrites hkx
`constraint[i-1]` with NIF `B[j-1]`), so the well-formed hkx constraint was
discarded and the illegal NIF one converted. `plain_hinge` is now deleted as a
kind: a plain hinge classifies as `hinge` everywhere, with the limits carried
in the descriptor.

### <a id="prismatic-descriptor-fix"></a>Prismatic

Oblivion stores `pivot_a`, `pivot_b`, `sliding_b`, `plane_b` and a rotation;
Skyrim also wants `sliding_a` / `plane_a`, the same axes in body A's frame.
Without the body world transforms at this point we copy the B-frame axes —
constrained prop pairs sit at near-identity relative rotation in practice.
Sliding distances are lengths, so they scale by 0.1.

**Vanilla Skyrim ships zero `bhkPrismaticConstraint` meshes**, so this path is
inherently untested by Bethesda.

### <a id="malleable-demotion"></a>Malleable demotion

A `bhkMalleableConstraint` wraps an inner descriptor of any type. Skyrim
expects the plain block, so each is replaced by a constraint of its inner
type, and every rigid body's `constraints` array is repointed at the
replacement. The `bhkConstraint` header's entities and priority come from the
OUTER block — the SubConstraint's own entity list is "usually NONE".

### <a id="enforce-ragdoll-tree"></a>Rebuilding the NIF constraint tree

`enforce_ragdoll_tree` rewrites every creature ragdoll body's constraint list
to exactly the joint `hkx_ragdoll.plan_ragdoll_tree` chose — the tree the
skeleton.hkx ships — so the NIF satisfies the engine's ragdoll-attach
contract. The contract, and the 2026-08-28 alit crash it explains, are
documented on `plan_ragdoll_tree`.

Per body:

- **The chosen authored joint is kept and every other one dropped.** The
  mudcrab ships bodies carrying 2, which shifts every later slot the engine
  indexes.
- **A joint authored on the parent's side moves to the child**, with its ends
  exchanged. The landdreugh's first body is constrained to a LATER body.
- **An unconstrained body gets the synthetic vanilla rock joint** to its
  nearest body-carrying ancestor. This is the 2026-08-08 "corpse never falls
  over" fix: an unconstrained `bhkRigidBody` is not in the ragdoll's
  constraint island, so the chain through it cannot collapse. Vanilla ships
  exactly bodies−1 constraints (dog 22/21, wolf 22/21, sabrecat 28/27,
  skeever 21/20); stormatronach, mehrunesdagon, shambles and skeleton were
  the incomplete rigs.

Markers are stripped before collision conversion, while values are still in
source units, so `enforce_ragdoll_tree` passes `exclude_markers=False`: every
body left is real, and the converted mass/radius would fool the source-unit
predicate (azura's static bodies convert to mass 0).

A synthesized joint registers on the CHILD body — the Havok convention is that
the constrained body is entity A — as well as in the block list.

### <a id="creature-blend-body-contract"></a>Creature blend bodies are not props

Creature-skeleton blend bodies follow the vanilla CREATURE contract, not the
prop one: the broadphase byte stays 0, not the dynamic-prop 10, and
`max_friction` is FORCED to 0.

An older comment claimed "vanilla skeleton.nif joints mix 10.0/0.5/0.01 so
keep the authored value". That is measurably wrong: a census of the vanilla
dog / wolf / sabrecat / skeever creature skeleton.nifs is **89/89 constraints
at exactly 0.000000**, matching their skeleton.hkx ragdolls.

### <a id="synthesized-ragdoll-joints"></a>Synthesized joints

An orphan body — one no constraint reaches — gets a synthesized
`bhkRagdollConstraint` to its parent, on the vanilla atronach rock-joint
template shared with `hkx_ragdoll`: cone 50°, plane ±90°, twist ±5°,
friction 0. Pivots sit at the child body's own center expressed in each body's
local space (both `center` fields are already in Skyrim Havok units by then).
Frames are axis-aligned — twist = X, plane = Y, motor = Z — the orthonormal
basis the 2010 layout requires, since a zero motor ships a singular basis.

## <a id="nested-and-multiple-collision"></a>Nested and multiple collision

**Code:** `asset_convert/collision/hoist.py`

Skyrim reads the `bhkCollisionObject` on the root BSFadeNode. Oblivion and
FO3/FNV meshes hang collision off child NiNodes, sometimes several (an SCOL has
one per part) and sometimes two levels down. `hoist_collision()` moves every
descendant collision object onto the root, carrying each part's transform
relative to the root: one part goes into its body (`bhkRigidBodyT`) or, for a
phantom, a `bhkTransformShape`; several static parts (`MO_SYS_FIXED`) merge
into one root body holding a `bhkListShape` of transform-wrapped shapes. When
the parts cannot be merged the first is hoisted and the rest are counted in
`PARTS_DROPPED` for the conversion report.

## <a id="collision-extraction-scale"></a>Collision extraction scale

**Code:** `asset_convert/collision/collision_extract.py`

`extract_nif_collision()` runs on CONVERTED meshes, where every shape already
sits in the same units: `collision.py` multiplies primitive vertices, box
half-extents, sphere/capsule radii and `bhkConvexTransformShape` translation
columns by `_HAVOK_SCALE` (0.1) on write, and `decode_cms()` returns havok
units divided by 7. Both therefore reach game units through the same factor,
`70.0` (7 game-per-havok / 0.1), so `CMS_TO_GAME == PRIM_TO_GAME`.

`PRIM_TO_GAME` was previously `10.0`, the factor correct for a TES4 SOURCE
mesh, whose primitives are plain havok units. Against converted output that
extracted every primitive shape exactly 7x too small, so navmesh generation
saw a collision body far inside the render geometry and walked straight
through it. Measured on `furniture/middleclass/middletable02.nif`
(`bhkListShape` of a box plus two convex hulls): render geometry spans
+/-57 units, extracted collision spanned +/-8.11, a collision/geometry size
ratio of 0.141 on x, 0.146 on y, 0.143 on z. The three sibling tables
(`middletable05/06/08`), which convert to `bhkCompressedMeshShape` and so take
the CMS path, measured 0.997-1.010 on the same test and were never affected.
2,327 meshes under `output/Oblivion.esm/meshes/tes4` carry a primitive
collision shape and were all extracted undersized.

<a id="body-placement"></a>**A rigid body is placed at its OWNING NODE's world transform** (`collision_from_data`, `_body_placements`). The extractor read every `bhkRigidBody` out of `data.blocks` and emitted its triangles in the body's own frame, on the stated assumption that conversion moves collision to the root node. That holds for most meshes but not all: `tes4/dungeons/chargen/prisonsecretwall01.nif` keeps its two boxes on the child nodes `bed` (translation 12.8, 170.0, -125.8) and `wall` (-61.6, 214.7, -64.0), so both were emitted ~200u from where they belong -- a phantom 171x94x227u box at the mesh origin that the navmesh then built against, while IN GAME the collision sits correctly because the engine honours the node chain. The walk accumulates rotation, uniform scale and translation per node and records a placer only for a chain that is not the identity, so the common root-mounted body pays nothing. The shape's own transform is already baked in by `_primitive_tris` / `decode_cms` and is not re-applied.
