# TES4 → TES5 Conversion — Issue Tracker

## Table of Contents

### Major Issues
1. [Havok feels off — too much inertia, items hang in air](#1---havok-feels-off--too-much-inertia-on-small-items-items-hang-in-air)
2. [Books crash the game when read](#2---books-crash-the-game-when-read-bookmenu--scaleform-crash)
3. [Animated signs mispositioned](#3---animated-signs-mispositioned-signthecountsarmsnif-signfightersguildnif)
4. [Hit detection too large (3–4× mesh size)](#4---hit-detection-too-large-34x-mesh-size)
5. [NiParticleSystem not correctly converted — flames not animated](#5---niparticlesystem-not-correctly-converted--flames-not-animated)
6. [NiDynamicEffect not correctly converted](#6---nidynamiceffect-not-correctly-converted)
7. [Furniture markers mispositioned — floating, backwards](#7---furniture-markers-mispositioned-floating-in-air-backwards)
8. [Remaining armor bugs](#8---remaining-armor-bugs)
   - [8.5 — Iron Gauntlets: left gauntlet shows only fingers with cuirass equipped](#85--iron-gauntlets--left-gauntlet-shows-only-fingers-when-anvil-cuirass-equipped)
   - [8.6 — Guard Helmet: too far forward and too low](#86--guard-helmet--too-far-forward-and-too-low)
   - [8.8 — Shields attached 180° wrong and not at handle](#88--shields-attached-180-wrong-and-not-at-handle)
   - [8.9 — Armor ground model invisible in inventory](#89--armor-ground-model-_gnd-invisible-in-inventory-but-visible-on-ground)
9. [SpeedTree (.spt) to NIF converter](#9---speedtree-spt-to-nif-converter)
10. [Crash from updated collision objects](#10---crash-from-updated-collision-objects-castleint2waynif-interior-cells)
11. [NIF converter: fix error reporting](#11---nif-converter-fix-error-reporting-suppress-pyffi-read-warnings-count-errors)
12. [NIF converter: fix broken conversion (RD/WR failures)](#12---nif-converter-fix-broken-conversion-rdwr-failures)

### Minor Issues
- [M1 — Fix face and body bug](#m1---fix-face-and-body-bug)
- [M2 — Update OBND based on meshes](#m2---update-obnd-based-on-meshes-instead-of-guessing)
- [M3 — Quest Records crash on startup](#m3---quest-records-crash-on-startup)
- [M4 — Pathgrid Records](#m4---pathgrid-records)
- [M5 — Package Records](#m5---package-records)
- [M6 — Every class teaches 1-handed](#m6---every-class-teaches-1-handed-not-correct-per-original-data)
- [M7 — Replace Prison marker](#m7---replace-prison-marker-with-skyrim-equivalent)
- [M9 — Weapons have attack animation "unknown"](#m9---weapons-have-attack-animation-unknown-instead-of-default)
- [M10 — Improve landscape layer blending](#m10---improve-landscape-layer-blending)
- [M11 — Remove skin portions of armor meshes](#m11---remove-skin-portions-of-armor-meshes--overlay-skyrim-body)
- [M12 — Fix default Skyrim body for greaves](#m12---fix-default-skyrim-body-to-work-with-greaves)
- [M13 — Properly position inventory items](#m13---properly-position-inventory-items)
- [M14 — Convert creatures / use Skyrim replacements](#m14---convert-creatures-with-animations--use-skyrim-replacements)

### Refactoring
- [R2 — MOPP_RL and temp folder cleanup](#r2---use-mopp_rlexe-from-asset_convert-use-temp-for-build-temp-files)
- [R4 — Standardize function calls; update README](#r4---standardize-and-simplify-function-call-conventions-update-readme)
- [R5 — Update and simplify GUI](#r5---update-and-simplify-gui)
- [R6 — BSA extraction: handle zlib-compressed BSAs](#r6---bsa-extraction-handle-zlib-compressed-bsas)
- [R7 — Option to re-pack into BSA](#r7---option-to-re-pack-into-bsa-after-conversion)
- [R8 — Fix tests and make more comprehensive](#r8---fix-tests-and-make-more-comprehensive)
- [R9 — Add texture_overrides folder to export](#r9---add-texture_overrides-folder-to-export)
- [R10 — Upgrade PyFFI to 2.2.4?](#r10---upgrade-pyffi-to-224)
- [R11 — ESM output shouldn't auto-copy to game directory](#r11---esm-output-shouldnt-copy-to-game-directory-automatically)

### Unknown Issues
- [Grass does not appear in-game](#grass-does-not-appear-in-game)
- [Nirnroot sound too loud and doesn't loop](#nirnroot-sound-very-loud-and-doesnt-properly-loop)
- [Items don't sound material hits](#items-dont-sound-material-hits)
- [Land textures have weird specular](#land-textures-have-weird-specular)
- [Harvested plants don't make a sound](#harvested-plants-dont-make-a-sound)

---

## Major Issues

---

### 1 - Havok feels off — too much inertia on small items, items hang in air

**Examples:** `uppersilverpitcher01.nif`, `uppergobletceramic01.nif`

#### Root Cause Analysis

Oblivion rigid bodies store physics properties (mass, friction, restitution, inertia tensor) tuned for Oblivion's Havok 5 runtime. Skyrim uses Havok 2010 (`bhkRigidBodyT`). Our converter in `collision.py:_convert_rigid_body()` only patches binary padding fields and collision filter. It does **not** touch:

- `rb.mass` — too heavy = too much inertia, slow to respond
- `rb.friction` — wrong surface response
- `rb.restitution` — bounciness
- `rb.linear_damping` — drag
- `rb.angular_damping` — spin drag
- `rb.inertia_tensor` — anisotropic resistance to rotation
- `rb.center_of_mass` — balance point, affects tumbling
- `rb.linear_velocity` / `rb.angular_velocity` — should be zeroed

The "hanging in air" symptom: when a rigid body has a very low mass relative to its inertia tensor, or the center_of_mass is wrong, Havok's sleeping system gets confused and leaves bodies suspended.

#### Brainstorm — Solutions in Order of Effort

**A)** Compare with a vanilla Skyrim clutter NIF (`temp\skyrim meshes\clutter\silverpitcher01.nif`). Log mass/friction/restitution/inertia from both and identify the delta. Use `tes5_nif_analyzer.py` to dump the rb fields.

**B)** Add override tables in `skyrim_overrides.py`: per-collision-material-type physics presets. Map `bhk` material enum → `(mass_mult, friction, restitution, linear_damping, angular_damping)`. Apply in `_convert_rigid_body`. Material enum is already copied from source — use it as a key.
- Stone/metal: higher mass_mult, lower restitution (~0.1)
- Wood: medium
- Cloth/leather: low mass, high angular_damping

**C)** Compute mass from volume. For `bhkBoxShape`/`bhkSphereShape`/`bhkCapsuleShape` we have exact dimensions in `_convert_shape` — compute volume × density and override `rb.mass`. Density known by material.

**D)** Zero angular/linear velocity on load. Ensure `(0,0,0,0)` on any converted body or Skyrim will apply the Oblivion velocity at load time.

**E)** Override with vanilla Skyrim ESM values when a matching record exists. Build per-family lookup using `tes5_nif_analyzer` diffing of `temp\skyrim meshes`.

#### Gotchas

- `inertia_tensor` is a 3×4 float matrix in `bhkRigidBodyT`. Wrong inertia makes objects spin unrealistically even with correct mass.
- The 0.1 `HAVOK_SCALE` factor applies to vertices — mass is already in Havok units. **Do NOT multiply mass by `_HAVOK_SCALE`.**
- `bhkRigidBodyT` vs `bhkRigidBody`: T version has orientation quaternion. Oblivion often uses the non-T version; PyFFI may produce T at Skyrim version.
- `unknown_int_6 = 196608` controls the sleeping threshold. Wrong value can freeze items mid-air after spawning.

---

### 2 - Books crash the game when read (BookMenu / Scaleform crash)

**Partially fixed (2026-04-04).** Three changes applied. In-game validation still needed.

**Crash Log:** `RSP+70 = BookMenu*`, `RSP+130 = NiPointLight*`, `RSP+1F0 = HUDData*`

#### Changes Made

1. **Added INAM** (pickup sound FormID = `0x000E894C`, the generic Skyrim book pickup sound) to all BOOK records. All vanilla Skyrim books have INAM; missing INAM can cause a null-deref when BookMenu or the inventory tries to play the pickup sound. File: `tes5_import/record_types/equipment.py:convert_BOOK()`, constant in `skyrim_overrides.py:BOOK_INAM`.

2. **Added CNAM** (null FormID = `0x00000000`) to all BOOK records. CNAM is always present in vanilla Skyrim books; for spell tomes it points to the spell FormID, for regular books it is null. Missing CNAM may cause field-parsing issues.

3. **Improved `_fix_book_html()`**: Replaces `<font face=N>` (all Oblivion face values) with `<font face=3>` (the standard Skyrim BookMenu body font). Fixed the IMG src regex to not double-quote the path. Fixed `\r\n` → `<br>` conversion to not double `<br>` tags already preceding newlines.

#### Remaining Concerns

- The `NiPointLight*` crash may still occur if there are other causes (e.g. shader incompatibilities in book NIFs, or very large DESC strings). If crash persists after recompiling the ESM, try approach C: override with Skyrim ESM books where EditorIDs match.
- Books with no DESC (null/empty) — Skyrim should tolerate this but verify.
- Spell-teaching books (DATA.Flags & 0x04) with CNAM pointing to a spell FormID are not yet converted.

---

### 3 - Animated signs mispositioned (signthecountsarms.nif, signfightersguild.nif)

Doors are now correct. Signs still wrong.

#### Root Cause Analysis

Oblivion animated signs use a `NiControllerManager` with a `bhkLimitedHingeConstraint` (or `bhkRagdollConstraint`) to make the sign swing. The converter has two guards that prevent hoisting collision on constrained NIFs (`has_constraints` check ~line 1582) and prevent wrapping the root rotation for animated objects.

The sign NIF likely has:
- Non-identity root rotation (sign faces perpendicular to the attachment point)
- `NiControllerManager` (animated)
- `bhkLimitedHingeConstraint` (swinging physics)

Since `root_is_animated = True`, the rotation-baking pass is **skipped** (pass 6c). The root rotation is never zeroed, but `BSFadeNode` ignores root rotation for statics. For animated objects the engine applies root transform in some contexts but not others — resulting in misposition.

The hinge pivot point is in the local coordinate of the constraint relative to the pre-bake root. Zeroing the root without transforming the constraint pivot makes the sign hang at the wrong location.

#### Brainstorm — Solutions in Order of Effort

**A)** Investigate specific sign NIFs first. Use `tes4_nif_analyzer.py` on `signthecountsarms.nif` to dump the full tree, root rotation, constraint data, and child `NiNode` transforms. Compare against a vanilla Skyrim animated sign (e.g. `SignInn.nif` in `temp\skyrim meshes`).

**B)** For animated objects with non-identity root rotation AND constraints: transform the constraint pivot points by the root rotation matrix before zeroing the root. `bhkLimitedHingeConstraint` has `pivot_A` / `pivot_B` and `axis_A` / `axis_B` — all must be multiplied by root rotation matrix R.

**C)** Alternate: push root transform into all child node translations (like pass 6c does for statics) but leave the constraint bodies alone.

**D)** Test: temporarily disable baking for animated signs and leave root rotation in place. Some Skyrim engine versions DO respect root rotation on `NiNode` (not `BSFadeNode`) — may fix positioning at cost of a possible scale issue.

#### Gotchas

- `bhkConstraint` pivot coordinates are in Havok space (scaled ×0.1), stored in A-body-local and B-body-local frames. Transformation must apply root rotation in NIF space, then convert.
- Doors were fixed separately — review any door-specific special-casing that may have introduced assumptions that break signs.
- Signs use `bhkKeyframeObject` (not `bhkRigidBody`) for the static post — the swinging part uses `bhkRigidBody`. These have different transform semantics.

---

### 4 - Hit detection too large (3–4× mesh size)

**Examples:** `uppersilverpitcher01.nif`, `uppergobletceramic01.nif`

#### Root Cause Analysis

"Hit detection" here is the activation/crosshair detection range, which Skyrim derives from the `OBND` (Object Bounds) subrecord on the base form — **NOT** from the Havok collision shape.

Current code in `common.py` uses hardcoded static guesses from `_OBND_DEFAULTS` (e.g. `MISC: (-5, -5, 0, 5, 5, 8)`). These are intentionally large "safe" values. For small items like goblets and pitchers the default OBND is 3–4× larger than the actual mesh.

Secondary cause: the collision shape itself may be inflated. Oblivion sometimes uses a `bhkBoxShape` or `bhkSphereShape` sized for the bounding sphere, not the tight geometry. After ×0.1 scale, if the source value was inflated, the Skyrim collision box remains proportionally too large.

#### Brainstorm — Solutions in Order of Effort

**A)** Read actual NIF bounds from the converted mesh. `NiTriShapeData` has `has_bounding_box` and `bounding_box` (center + dimensions). Sum min/max extents across all geometry nodes to get a tight AABB, then use it for `OBND`. Options:
- Pre-compute during asset conversion and write a **sidecar JSON file** with per-NIF OBND data, read by the importer.
- Post-processing pass that reads converted NIFs.
- Store NIF-derived bounds in the export text for mesh-bearing records.

**B)** Read Oblivion's own bounding sphere from the NIF. `NiNode` has `has_bounding_sphere` + `bounding_sphere`. Use radius as a symmetric OBND.

**C)** For MISC items specifically: build a lookup table by EditorID from the `Skyrim.esm` dump (`temp\skyrim.esm`) and use those values when available.

**D)** Quick fix: reduce all `_OBND_DEFAULTS` by ~50%. `MISC: (-3, -3, 0, 3, 3, 5)` instead of `(-5, -5, 0, 5, 5, 8)`.

#### Gotchas

- `OBND` is in game units (centimeters). NIF vertex coordinates are also in game units for Oblivion static meshes — **no ×0.1 factor needed**.
- Negative Z values in `OBND` mean the object extends below its pivot (e.g. a goblet resting on its base needs Z min near 0).
- Too-small `OBND` makes items hard to pick up. Better slightly too large than unactivatable.
- The sidecar JSON approach requires the **asset pipeline to run before the ESM importer** — document this dependency.

---

### 5 - NiParticleSystem not correctly converted — flames not animated

**Examples:** `fireopensmall.nif`, `middlecandlestickfloor01fake.nif` (no candle flame)

#### Root Cause Analysis

`_convert_particle_system()` (`nif_converter.py:615`) currently:
- ✅ Replaces `NiPSysData` with a fresh Skyrim-compatible instance
- ✅ Fixes `NiPSysGrowFadeModifier.base_scale = 1.0`
- ✅ Converts `NiTexturingProperty` → `BSEffectShaderProperty`
- ✅ Drops `NiFlipController`
- ✅ Sets `emissive_multiple = 1.0`

Still broken:

1. **Particle system not visible and not animated.** Key modifiers must be present **in order** in the `modifiers` array:
   - `NiPSysEmitter` (generates particles)
   - `NiPSysGravityModifier`
   - `NiPSysGrowFadeModifier`
   - `NiPSysAgeDeathModifier`
   - `NiPSysBoundUpdateModifier` — **MUST be last**, updates bounding sphere
   - `NiPSysRotationModifier` (optional)

2. `NiPSysEmitter.initial_speed`, `burst_size`, `emission_rate` may have been in Oblivion-specific units or wrongly zeroed.

3. `NiPSysEmitter` target reference may become stale or null at version upgrade.

4. **`middlecandlestickfloor01fake.nif`**: the flame embed code is **commented out** at `nif_converter.py:1700-1704` with `"Code does work so it's been disabled"`. This is the candle flame issue — not the particle conversion itself.

#### Brainstorm — Solutions in Order of Effort

**A)** Re-enable the flame embedding code at lines 1697–1704. The comment says "Code does work" — investigate why it was disabled and test on a small batch first.

**B)** After converting, walk the modifiers list and verify `NiPSysBoundUpdateModifier` is present. If missing, add one. If not last, move it to the end.

**C)** Verify emitter fields survive the version upgrade. For each `NiPSysEmitter`: ensure target ptr points back to `NiParticleSystem`; log `emission_rate` and `burst_size` — if 0, copy from a vanilla Skyrim equivalent.

**D)** Compare a vanilla Skyrim particle NIF structure (`tes5_nif_analyzer` on `temp\skyrim meshes`) against the converted output. Block sequence in the output NIF matters.

**E)** For fire specifically: build a `skyrim_overrides` entry that substitutes an equivalent vanilla Skyrim fire NIF instead of converting the Oblivion version (template substitution).

#### Gotchas

- `NiPSysModifier.name` must match specific strings Skyrim expects. Some emitters are found by name not type — `"Emitter"` must be present.
- `NiPSysData.bs_max_vertices` must be `> 0` and match what the emitter expects to spawn.
- `NiPSysSpawnModifier` with high `spawn_count` can cause infinite loops — performance issue.

---

### 6 - NiDynamicEffect not correctly converted

#### Root Cause Analysis

Current handling in `_walk_node()` (`nif_converter.py:754`):
- ✅ Strips `NiTextureEffect` — no Skyrim equivalent
- ✅ Strips `NiDirectionalLight` — would override day/night cycle
- ⚠️ Keeps `NiPointLight` / `NiSpotLight` / `NiAmbientLight` with `NIF_FLAGS`
- ✅ Clears root `NiNode`'s `effects[]` array

Probable issues:
1. `NiPointLight.dimmer` controls light contribution — PyFFI may initialize it to 0 at version upgrade → invisible lights.
2. Lights in the root `effects[]` array are lost when effects are cleared. **Root-level lights need to be moved to `children[]` first.**
3. Some Oblivion lights are in `effects[]` (root-level), others are child `NiNodes`. Root-level ones are silently dropped.
4. `NiAmbientLight` in Skyrim is global — per-mesh ambient lights wash out the entire object.

#### Brainstorm — Solutions in Order of Effort

**A)** Audit: run `tes4_nif_analyzer` on a NIF known to have lights and check where they sit (`effects[]` vs `children[]`).

**B)** For lights in root `effects[]`: convert them to child `NiNodes` BEFORE clearing `effects[]`. The engine correctly finds child `NiPointLight` nodes.

**C)** For `NiPointLight`: set `dimmer` to a sane default. Map Oblivion `fade_const` / `fade_linear` → Skyrim `linear_attenuation`.

**D)** `NiSpotLight`: remap cone angle. Rare case — mainly special effects.

**E)** `NiAmbientLight`: strip safely — ambient in Skyrim is per-cell, not per-mesh.

#### Gotchas

- `effects[]` and `children[]` are separate lists in PyFFI. A `NiNode` in `effects[]` is **NOT** in `children[]`. Converting means adding to children, not moving.
- Skyrim NIFs with multiple `NiPointLight` children are valid (e.g. chandeliers). The performance cost is higher than Oblivion.
- Zero `dimmer` = invisible light. Must set explicitly after version upgrade.

---

### 7 - Furniture markers mispositioned (floating in air, backwards)

Cannot sit in chairs. Markers appear in the wrong place/orientation.

#### Root Cause Analysis

`BSFurnitureMarker` → `BSFurnitureMarkerNode` conversion happens in two places:
1. **Primary** (`nif_converter.py:1283`): converts positions, maps `position_ref_1` to `animation_type` (Sleep/Sit) and `entry_properties`.
2. **Root-rotation compensation** (`nif_converter.py:1507`): when root rotation is baked, marker offsets are transformed by root R matrix + translation vector before zeroing root.

Known issues to investigate:

**a) Heading/orientation:** Oblivion stores in **milliradians** (0–6283 for full circle). We divide by 1000 → radians. But if Skyrim expects degrees, the divisor should be `1000 × (180/π) ≈ 57.3`. Check vanilla chairs in `temp\skyrim meshes`.

**b) Y-axis "backwards":** A 180° heading error (π offset) would explain chairs being backwards. May be a convention mismatch.

**c) Z "floating in air":** The root-rotation compensation at `nif_converter.py:1526` does `off_new = off @ Rmat + Tvec`. If `root.translation.z` is non-zero (common for objects with non-origin pivots), **every marker floats by that amount**. Marker offsets are in model space — only rotation should be applied, not translation.

**d) entry_properties mapping:** `position_ref_1` is a U8 enum. The mapping `(1=Left, 2=Right, 13=Front, 14=Behind)` needs verification against Oblivion's `BSFurnitureMarker` documentation.

#### Brainstorm — Solutions in Order of Effort

**A)** Dump a vanilla Skyrim chair NIF (`UpperBench01.nif` in `temp\skyrim meshes`) with `tes5_nif_analyzer`: check offset XYZ vs mesh geometry, heading value in radians, `animation_type`, `entry_properties` bits.

**B)** Dump the equivalent Oblivion chair and compare `BSFurnitureMarker` values to the Skyrim output to verify the orientation divisor.

**C)** Fix the Z inflation: at `nif_converter.py:1526` change `off_new = off @ Rmat + Tvec` → `off_new = off @ Rmat` (rotation only, no translation).

**D)** Test heading divisors: `/ 1000.0` (radians, current) vs `/ (1000 / (180/π))` (degrees). Compare against a known-correct chair.

**E)** Verify `position_ref_1 = 0` doesn't accidentally trigger sleep mode.

#### Gotchas

- Skyrim's furniture entry system changed significantly from Oblivion. Test in-game after each parameter change.
- Some chairs have 2–4 markers (left-side and right-side entry). Verify bit numbering matches Oblivion's.
- `FNPR` in the ESM (`items.py:convert_FURN`) and `BSFurnitureMarkerNode` in the NIF must agree on `animation_type`. Mismatch causes NPCs to play wrong animation.

---

### 8 - Remaining armor bugs

#### 8.5 — Iron Gauntlets — left gauntlet shows only fingers when Anvil cuirass equipped

**Root Cause:** Body partition conflict. When the cuirass and gauntlets share overlapping `BSDismemberSkinInstance` partition IDs, Skyrim hides one. The Anvil cuirass likely has a partition overlapping the gauntlet's left-hand body part ID (BP 37 = Left Hand).

**Files:** `asset_convert/nif_converter.py:_get_body_parts_for_geometry()` and `skyrim_overrides.py:ARMOR_GEOMETRY_BODY_PARTS`

**Investigation:**
1. Dump Anvil cuirass NIF partitions via `tes5_nif_analyzer` — look at `BSDismemberSkinInstance.partitions[].body_part`.
2. Dump iron gauntlets NIF partitions.
3. Check if any partition ID appears in both. BP 37 = Left Hand is the likely conflict.
4. Verify `ARMOR_GEOMETRY_BODY_PARTS` has distinct IDs for cuirass vs gauntlet geometry.
5. Gauntlet NIF may have arm+finger geometry in one `NiTriShape` whose name matches no keyword → assigned `ARMOR_DEFAULT_BODY_PART` which conflicts with the cuirass.

**Fix:** Update `ARMOR_GEOMETRY_BODY_PARTS` to give gauntlet finger geometry explicit body part IDs (37=LeftHand, 36=RightHand). Ensure cuirass geometry only uses 32=Body.

---

#### 8.6 — Guard Helmet — too far forward and too low

**Root Cause:** Helmet is a single-bone non-skinned mesh attached via `Prn`. `_add_prn_skin()` creates a `NiNode` placeholder for the `Prn` bone and weights all vertices to it 100%. The `Prn` bone maps to `'NPC Head [Head]'` in Skyrim.

The mismatch:
- **a)** Oblivion's helmet coordinate origin is at the base of the head; Skyrim's `NPC Head` bone is at the center of the skull (~8–10 game units forward/upward).
- **b)** `_add_prn_skin()` does not set `NiSkinData.root_transform` or per-bone transform — leaves them as identity, placing the helmet at the bone's world origin, not its Oblivion-relative position.

**Fix:** After `_add_prn_skin()`, set `NiSkinData.root_transform` to the inverse of Skyrim skeleton's `NPC Head` bind pose transform. Alternatively, apply a per-helmet offset in `skyrim_overrides.py` (similar to weapon PRN remapping) — a per-type correction matrix for helmet, boot, gauntlet types.

---

#### 8.8 — Shields attached 180° wrong and not at handle

**Partially Fixed.** `nif_converter.py` now applies a proper orientation fix in the `remapped == 'SHIELD'` block:

- **Rotation R = [[-1,0,0],[0,0,1],[0,1,0]]**: maps Oblivion face-in-XZ (face normal = -Y) to Skyrim face-in-XY (face normal = -Z), matching vanilla `ironshield.nif`.
- **Translation**: computed from mesh vertex bbox center so the shield face center lands at origin (matching Skyrim SHIELD bone attachment convention).
- **Pass-6c** wraps the non-identity rotation + translation into an inner NiNode under the BSFadeNode root (identity), exactly as vanilla Skyrim shields use.

Result: converted shields have X(±21), Y(±21), Z(±7) — matches vanilla Skyrim iron shield X(±21), Y(±22), Z(±6). Test coverage in `test_asset_convert.py::TestShieldVsArmorClassification::test_shield_orientation_corrected`. ⚠️ Needs in-game validation — rotation matrix is derived analytically; the 180° direction may still need adjustment.

---

#### 8.9 — Armor ground model (_gnd) invisible in inventory but visible on ground

**Root Cause (regression):** The `_gnd` NIF is the model shown in the inventory 3D viewer and when dropped. Code strips skin from `_gnd` files (`_strip_gnd_skin`, line 1205). If `_is_gnd` detection is wrong (false negative), the skin stays and the inventory model fails to render because bones don't exist in the inventory scene.

**Investigation:**
1. Verify `_is_gnd` detection: `nif_basename.endswith('_gnd.nif')` — case and path sensitivity.
2. Dump the output `_gnd` NIF and check for `NiSkinInstance` — it should be absent.
3. Check `BSInvMarker` values in `skyrim_overrides.py:ARMOR_GND_INV_MARKER_ROT_*`. Compare with a vanilla armor ground model that works.
4. Check if `_is_worn_armor` is accidentally true for `_gnd` — this would give it `NiNode` root instead of `BSFadeNode`, breaking inventory display.

---

### 9 - SpeedTree (.spt) to NIF converter

- SPT files: `export\Oblivion.esm\trees\`
- Format reference: `external\spttools-master\FORMAT` (v2 format spec)
- Manual conversion examples: `external\Speed Tree Conversion\` (static-leaf NIFs)

#### Format Analysis

The `.spt` format is a binary proprietary format by Interactive Data Visualization. Key sections:

| Section | Content |
|---------|---------|
| `1000` | File magic `"__IdvSpt_02_"` — version ID |
| `2000` | Trunk/branch texture map path (intstring) |
| `2005` | Random seed (int) |
| `2006/2007` | Base size + variance (floats) |
| `4003` | Leaf texture map filename (intstring) |
| `4004/4005` | Leaf texture origin XYZ + size XY (floats → quad dimensions) |
| `6000–6017` | Bezier splines for trunk/branch geometry — **procedural, NOT static mesh data** |
| `60002/60003` | Trunk vertex positions and normals (actual geometry data) |
| `70000` | Texture path cross-reference |

The SPT format encodes trees **procedurally** — there are no pre-computed vertex arrays for trunk/branches. We **cannot** extract a mesh directly from the spline parameters.

#### Approach Options (in order of output quality)

**A — Filename Mapping (easiest):** Use the manual conversion examples in `external\Speed Tree Conversion` as templates. Build a mapping table from SPT filename → converted NIF filename. Fall back to a generic tree billboard NIF for unmatched trees.

**B — Python SPT Reader + NIF Builder (recommended):**
1. Parse sections from binary format (each section: U16 ID + U16 length + data)
2. Extract trunk geometry from section `60002` (vertex positions) and `60003` (normals)
3. Extract leaf quad dimensions from `4004` + `4005`
4. Extract texture paths from `2000` (bark) and `4003` (leaf)
5. Build Skyrim NIF: `BSFadeNode` root + `NiTriShape` for trunk + alpha-tested `NiTriShape` planes for leaf billboards

**C — Billboard-only (simplest):** Parse leaf texture and size from SPT, generate 4–8 crossed-plane billboard quads at approximate tree height. No trunk. Works for distant LOD, looks bad up close.

#### Implementation Plan (B approach)

1. Write `asset_convert/spt_reader.py` — parse SPT binary sections into a dict. Intstrings = U16 length + UTF8 bytes. Floats = IEEE 754 little-endian.
2. Write `asset_convert/spt_to_nif.py` — convert parsed dict to Skyrim NIF.
3. Wire into `asset_pipeline.py` — process `trees/` folder after standard NIF conversion. Output → `output\meshes\landscape\trees\`.

#### Gotchas

- Check SPT version: FORMAT spec covers v2. Oblivion may use v2 and v3 — check `speedtreecadnotesv3` / `speedtreecadnotesv4` files in spttools-master.
- Trunk geometry from `60002` is low-resolution (LOD0 only). UV generation is non-trivial for cylindrical bark mapping.
- Each tree has seasons (spring/summer/fall/winter leaf textures). May need 4 NIF variants or a single one using the summer texture.
- Generate simple box/capsule collision for the trunk (reference: `*_col.nif` files in `external\Speed Tree Conversion`).

---

### 10 - Crash from updated collision objects (castleint2way.nif, interior cells)

```
Crash 1:
  RSP+0:  hkpCollisionDispatcher*
  RSP+18: bhkCollisionFilter*
  RSP+60: bhkWorld*
  RSP+B8: NiNode "Box03"  (AnvilCurtain02.NIF)
  RSP+C0: BSTriShape "StackLroomLeftB:34"  (CastleStackLLeftBottom.NIF)

Crash 2:
  RSP+E0:  BSGeometryListCullingProcess*
  RSP+108: BSTriShape "UpperBench02:2"  (UpperBench02.NIF)
  RSP+188: bhkCharProxyController*
```

#### Root Cause Analysis

The crash is inside `hkpCollisionDispatcher` — **NOT** a null-deref on geometry data. It's a collision system assertion or memory corruption. Candidates:

1. **`unknown_6_shorts[2:3]` MUST be 0** — already confirmed and set. **DO NOT revisit this. It has been checked many times.**

2. **Sub-shape layer hardcoded to 1** (`LAYER_STATIC`). Oblivion uses layer values like 2 (`LAYER_ANIM_STATIC`) or 8 (`LAYER_BIPED`). Wrong layer causes the collision filter to route shapes to the wrong handler → crash.

3. **Stale constraint body references** — `NiNode "Box03"` from `AnvilCurtain02.NIF`. Curtains likely use `bhkLimitedHingeConstraint`. If constraint body references are stale after conversion, the dispatcher walks a null/invalid body chain.

4. **`UpperBench02` + `bhkCharProxyController`** — player character controller collides with the bench. Wrong collision layer could crash the proxy controller when resolving contact.

5. **`castleint2way.nif` structural mismatch** — "Skyrim version has 2 `NiNodes` with value `new2way` under the collision object, whereas Oblivion only had one." Missing second wrapper `NiNode` may cause stale target pointer.

#### Brainstorm — Solutions in Order of Effort

**A)** Preserve source layer instead of hardcoding 1. In `_ni_strips_to_packed()` (`collision.py:144`):
```python
packed.sub_shapes[0].layer = (
    bhk_strips.sub_shapes[0].layer
    if hasattr(bhk_strips, 'sub_shapes') and bhk_strips.sub_shapes
    else 1
)
```

**B)** Strip `bhkLimitedHingeConstraint` from curtain NIFs (filename contains `"curtain"`). Replace with static collision. Dynamic cloth physics cannot transfer without re-rigging.

**C)** Add target-pointer validation: after converting all collisions, walk the tree and verify every `bhkCollisionObject.target` points to an actual `NiNode`. Null or stale targets are the most common cause of dispatcher crashes.

**D)** For `castleint2way` specifically: compare the Oblivion NIF structure (1 inner `NiNode`) against the Skyrim vanilla equivalent. Add the second `NiNode` wrapper programmatically if needed.

**E)** Analyze crash logs in the project root: `crash-2026-03-31-22-12-43.log` and `crash-2026-03-31-23-53-12.log`. Run through a Crash Log Analyzer (SKSE-based tool) to identify the exact instruction and stack depth.

#### Gotchas

- **Do NOT modify `unknown_6_shorts[2:3]`** — already set to 0, verified many times. Not the cause.
- The `bhkCharProxyController` crash happens "around half the time" — suggests a race condition or heap state dependency, harder to reproduce consistently.
- `bhkCompressedMeshShape` (output of MOPP_RL) has a target pointer that must point to the root `NiNode`. Verify `_extract_mopp_result()` works correctly when the NIF has been wrapped with an inner `NiNode`.

---

### 11 - NIF converter: fix error reporting (suppress PyFFI read warnings, count errors)

Current error count shows 0 — obviously wrong. Read errors spew into output. Write errors/warnings need to be captured and counted by type.

**Known PyFFI read-time warnings (NOT our output issues — suppress these):**
- `"NaN files (2)"` — corrupt source data in Oblivion NIFs
- `"invalid enum value for ExtraVectorsFlags"` — Oblivion NIFs
- `"Reading bhkMoppBvTreeShape failed"` — corrupt MOPP data in source

**Actual output issues still occurring:**
- `"NiTexturingProperty block is missing from the nif tree: omitting reference"`
- `"NiNode block is missing from the nif tree: omitting reference"`
- `"NiMaterialProperty block is missing from the nif tree: omitting reference"`

#### Root Cause Analysis

PyFFI's `write()` prints warnings to `sys.stderr` when it encounters dangling references. `batch_convert` only counts exceptions at the Python level (`_batch_worker` try/except), not PyFFI's internal validation messages.

"Block missing from nif tree" errors occur when:
- A `NiNode.children[]` entry points to a block not reachable from `data.roots`
- We modify the tree (strip particles, strip collision) but leave stale pointers in parent nodes
- PyFFI's `write()` skips unreachable blocks and emits these warnings

#### Brainstorm — Solutions in Order of Effort

**A)** Redirect and capture PyFFI's `stderr` during `write()`. Use `io.StringIO` as a context-managed replacement for `sys.stderr`. Parse captured output to categorize and count each warning type. Accumulate across workers in the worker return value.

**B)** Use `warnings.catch_warnings()` context manager to intercept PyFFI's `UserWarning` emissions. Cleaner than `stderr` redirect.

**C)** Fix the root cause of "block missing" errors: after any node is set to `None` or removed from `children`, walk the entire tree and null out any references to that block in `properties[]`, `controllers`, `extra_data[]`. Add a `_clean_dangling_refs(data)` utility.

**D)** Run `pyffi.spells.nif.fix` spells before write. Test which are relevant:
- `SpellFixDegenerateTriangles`
- `SpellCleanTextureFile` (fixes path casing)
- `SpellMergeDuplicateVertices` (reduces size)

**E)** Suppress "NaN files" warning at source: scan `NiTriShapeData` vertices for NaN before writing, replace with 0.0.

#### Gotchas

- `stderr` redirection in `mp.Pool` workers is tricky — each worker process has its own `stderr`. Use a logging queue or return captured warnings in the worker return value.
- `pyffi.spells.nif.optimize` significantly increases conversion time. Run only cheap spells (fix/check, not optimize) in the batch path.
- Some "block missing" warnings may be benign (Oblivion NIFs already referencing missing blocks). Need to distinguish our-fault vs source-fault.

---

### 12 - NIF converter: fix broken conversion (RD/WR failures)

**RD (Read Failure) — corrupt/truncated/unknown blocks in source:**
```
[RD] architecture\basementsections\ungrdltraphingedoor.nif
[RD] architecture\castle\kvatch\kvatch castle int hallway01.nif
[RD] clutter\farm\handscythe01.nif
[RD] clutter\farm\oar01.nif
[RD] clutter\floorplane01.nif
[RD] clutter\stonepedastellarge01.nif
[RD] dungeons\ayleidruins\interior\arwelkydclusterfx01.nif
[RD] oblivion\architecture\citadel\interior\switch\scampswitch01.nif
```

**WR (Write Failure) — version-incompatible blocks:**
```
[WR] architecture\ships\mainmast02.nif
[WR] architecture\statue\nightmotherstatue.nif
[WR] architecture\statue\nightmotherstatuebase.nif
[WR] dungeons\caves\triggers\ctrigtripwire01.nif
[WR] oblivion\gate\obgatemini01.nif
[WR] oblivion\gate\obliviongate_forming.nif
[WR] oblivion\plants\harradagroundattack.nif
[WR] oblivion\plants\harradauprightattack.nif
[WR] oblivion\sigil\sigillighttowerbase.nif
[WR] weapons\daedric\bow.nif  (and all other bows)
```

#### Root Cause Analysis — RD Failures

PyFFI raises during `data.read()` when encountering unknown block types, binary structure mismatches, or corrupt data (NaN, impossible values, truncated file). The Ayleid cluster FX file likely has `NiParticleSystem` with uncommon modifiers. The trap hinge door likely has an unusual constraint type.

#### Root Cause Analysis — WR Failures

`data.write()` fails when a block at Skyrim version has fields that can't be serialized. Most common cause: **`NiGeomMorpherController`** — its morph data arrays have a version-conditional element count that fails at UV2=83.

**All bows fail → common root cause.** Bows use `NiGeomMorpherController` for the draw animation. Oblivion gate forming and statue NIFs likely use morph animation or advanced particle effects.

#### Brainstorm — Solutions

**RD Fixes:**

**A)** For each failing RD NIF, run PyFFI with verbose output to identify WHICH block type causes the failure. If it's an unknown block type, skip just that block (replace with `NiNode` placeholder) and continue reading.

**B)** Pre-read pass: inspect the block type list in the NIF header before full read. If an unknown type is listed, try reading with a per-block exception handler instead of failing the whole NIF.

**C)** For NaN files: pre-process step that scrubs NaN/Inf from vertex data using `struct.pack/unpack` on raw bytes before PyFFI reads.

**WR Fixes — Bows (all WR):**

**D)** Strip `NiGeomMorpherController` from the controller chain of all `NiTriStrips`/`NiTriShape` nodes **before the version upgrade**. This removes the morph animation but preserves the static mesh. Bows will display but won't animate the draw. Look in `_process_controller_manager()` — add a strip step for `NiGeomMorpherController`.

**WR Fixes — Oblivion gate / statue / harrrada:**

**E)** After stripping morph controllers, check if write succeeds. If still failing, add a verbose write path: catch the exception, iterate over the block list, binary-write each block individually to find which one fails, then strip just that block.

**WR Fixes — Tripwire / Switch:**

**F)** `ctrigtripwire` and `scampswitch` are triggers — may use `bhkSPCollisionObject` or `bhkNPCollisionObject` on the **root node**. These are already stripped for child nodes in `_convert_collision()`. Extend phantom-strip to root-level collision objects.

**General — RD files:**

**G)** Consider running failing NIFs through NifSkope as a pre-processing step (NifSkope can read corrupt NIFs that PyFFI chokes on and re-save in a clean form).

#### Gotchas

- Removing `NiGeomMorpherController` from bows means the bow string doesn't animate when drawn. Acceptable for a first pass.
- The morph controller strip **must happen before version upgrade** (UV2=11 → 83) because at UV2=83 the morph arrays have already been mis-read.
- `NiGeomMorpherController` must be removed from the controller **chain** of its parent node (linked list: `node.controller → .next_controller`), not just from `data.blocks`.

---

## Minor Issues

---

### M1 - Fix face and body bug

Insufficient detail to diagnose. Likely NPC head/body NIF has skin retargeting issues (same category as armor bugs). The head uses `HDPT` (head part) records in TES5 created from TES4 `HAIR`/`EYES` records — a missing `HDPT` could make NPCs appear headless or with wrong textures.

**File:** `tes5_import/record_types/actors.py` — `NPC_` and `HDPT` conversion.

---

### M2 - Update OBND based on meshes instead of guessing

See [Major Issue #4](#4---hit-detection-too-large-34x-mesh-size) — same root cause and solutions. Priority: implement the sidecar JSON approach in `asset_pipeline.py`.

---

### M3 - Quest Records crash on startup

**SKIP.** Known incompatibility — TES5 `QUST` requires `VMAD` (Papyrus) and `ALST` (alias array) that cannot be auto-generated. Keep in `SKIP_TYPES`.

---

### M4 - Pathgrid Records

`PGRD→NAVM` conversion is now wired in `pgrd_to_navm.py`. Still "unusable with many limitations." Advanced algorithm brainstorm already added to `pgrd_to_navm.py`. Keep skipped until brainstorm items are implemented.

---

### M5 - Package Records

`PACK` conversion brainstorm already added to `dialog_misc.py`. Template-based approach (pointing at vanilla Skyrim package templates via `PKCU.PackageTemplate`) is the recommended first step. Keep skipped until the ANAM procedure tree logic is implemented.

---

### M6 - Every class teaches 1-handed (not correct per original data)

TES4 `CLAS` records have a list of taught skills. TES5 `CLAS` only has a single "Teaches" field. The importer in `actors.py` likely hard-codes 1-handed as a fallback when the TES4 skill doesn't map.

**File:** `tes5_import/record_types/actors.py:convert_CLAS()`

**Fix:** Map TES4 skill index to TES5 skill enum and use the primary taught skill. Reference: `TES4_SKILL_TO_TES5_INDEX` in `equipment.py` for the mapping table.

---

### M7 - Replace Prison marker with Skyrim equivalent

Oblivion uses `XMRK`/`FMRK` subrecords on `REFR` for the Prison marker location. TES5 uses `XLKR` (linked ref) pointing to a `XEZN` location record.

**Fix:** In `world.py:convert_REFR()`, detect prison marker references and replace with the Skyrim prison marker FormID override from `skyrim_overrides`.

---

### M9 - Weapons have attack animation "unknown" instead of "default"

TES5 `WEAP` has `DNAM.Attack Animation` field. `convert_WEAP()` likely leaves this as 255 (unknown) instead of 0 (default).

**Fix:** Map TES4 weapon type → TES5 attack animation enum in `constants.py`.

**File:** `tes5_import/record_types/equipment.py:convert_WEAP()` DNAM packing.

---

### M10 - Improve landscape layer blending

Current `BTXT`/`ATXT`/`VTXT` layer system has alpha blending issues at layer boundaries. Skyrim supports up to 8 layers (capped at 5 for safety).

Options:
- Increase per-quadrant layer cap from 5 to 6–7 to preserve more detail
- Improve layer opacity normalization — if all layers for a pixel sum to >1.0, normalize them
- Add dithering at layer edges to reduce hard blend lines

---

### M11 - Remove skin portions of armor meshes / overlay Skyrim body

Some Oblivion armor meshes include body skin geometry that partially overlaps the underlying body mesh, causing z-fighting or double-skin in Skyrim.

**Investigation:** Check if armor `NiTriShape` nodes named `"Body"` or `"Skin"` should be stripped in `nif_converter.py` before writing. Controversial — stripping could leave coverage gaps.

---

### M12 - Fix default Skyrim body to work with greaves

Oblivion has greaves (leg armor) as a separate slot. Skyrim's default body mesh has no separate leg slot.

Options:
- Use a Skyrim body mod that preserves leg coverage under greaves
- Convert greaves to TES5 biped slot 35 (Legs) and ensure body shows legs
- Overlay greaves as a full-leg overlay on slot 35

---

### M13 - Properly position inventory items

`BSInvMarker` rotation values control how items display in the 3D inventory. Current values in `skyrim_overrides.py` are hand-tuned per category. For precise positioning, compare against vanilla Skyrim item `BSInvMarker` values using `tes5_nif_analyzer`, then build per-item overrides keyed by EditorID.

---

### M14 - Convert creatures with animations / use Skyrim replacements

**Major task.** TES4 `CREA` → TES5 `NPC_` conversion is in `actors.py`. Full conversion requires:
- Behavior graph (`.hkx`) for each creature type
- Skeleton NIF remapping (Oblivion creature rigs → Skyrim creature rigs)
- Animation `.kf` files converted to Skyrim `.hkx` format

**Short-term fix:** Build a `skyrim_overrides` lookup table from Oblivion `CREA` EditorID → Skyrim `NPC_` FormID using `Skyrim.esm` + Beyond Skyrim ESMs as source. (Horse → horse, wolf → wolf, etc.)

---

## Refactoring

---

### R4 - Standardize and simplify function call conventions; update README

Entry points are inconsistent: some use `python -m module`, some use direct script paths. Document all entry points in `README` with copy-paste examples.

---

### R5 - Update and simplify GUI

`run/gui.py` — review current state, ensure all pipeline steps are accessible from the GUI.

---

### R6 - BSA extraction: handle zlib-compressed BSAs

Current `bsa_extract.py` may fail on compressed BSAs. The Oblivion BSA Uncompressor can decompress them first. Wire it in as a pre-step in `asset_pipeline.py` when compression is detected.

**Detection:** BSA header flags bit 2 = `CompressedArchive`. Check in `bsa_extract.py`.

---

### R7 - Option to re-pack into BSA after conversion

Post-conversion: gather all `output\` meshes/textures into a BSA using `BSArch.exe` or similar. This reduces file count from thousands to one archive.

---

### R8 - Fix tests and make more comprehensive

**Files:** `tests/test_import.py`, `test_skin_retarget.py`, `test_orientation.py`

Areas lacking tests:
- Collision conversion (`bhkNiTriStrips` → `bhkPackedNiTriStrips`)
- Furniture marker conversion
- Book HTML sanitization (`_fix_book_html`)
- Batch convert error counting
- ESM record imports (`FURN`, `BOOK`, `WEAP` with specific field values)

---

### R9 - Add texture_overrides folder to export

Create an overrides mechanism: if a file exists in `texture_overrides/`, use it instead of the extracted BSA version. Useful for hand-corrected textures.

---

### R10 - Upgrade PyFFI to 2.2.4?

PyFFI 2.2.4 may have fixes for the `nif.xml` version-conditional issues patched manually at `nif_converter.py:80` (`NiPSysGrowFadeModifier.base_scale`). Check the 2.2.4 changelog before upgrading. Run the full test suite afterward.

**Risk:** `nif.xml` changes may break existing conversion passes.

---

### R11 - ESM output shouldn't copy to game directory automatically

The pipeline currently auto-copies the output ESM to the Skyrim Data folder. Add a flag (`--no-deploy` or `--output-only`) to disable this.

---

## Unknown Issues (ESM or Mesh — root cause not identified)

---

### Grass does not appear in-game

TES5 `GRAS` records need specific `DNAM` fields (density, min slope, max slope, water distance flags). `LAND` records need `VTEX` correctly wired to `LTEX→TXST`. Also: `CELL` needs `XCLL` lighting that doesn't suppress grass. Verify `convert_GRAS()` in `items.py` is complete and the `GRAS` record's `OBND` is set.

---

### Nirnroot sound very loud and doesn't properly loop

`SOUN`+`SNDR` conversion (`dialog_misc.py`). The `SNDR` record's loop flag and volume multiplier may not be correctly mapped from TES4 `SOUN.DATA`. Nirnroot uses a looping ambient sound — check `SNDR.FNAM` loop flags and `SNDR.VNAM` volume.

---

### Items don't sound material hits

Skyrim uses `MATT` (Material Type) records linked to `LAND`/collision to determine footstep/impact sounds. Converted NIFs may have wrong `bhk` material enum, preventing Havok from looking up the sound. Also check `IPDS` (Impact Data Set) on `WEAP` records — verify `convert_WEAP()` includes `INAM→IPDS` mapping.

---

### Land textures have weird specular

Known partial fix: `LTEX SNAM=0` and `TXST DNAM=0x0001`. If still wrong, check `TXST TX01` (normal map path) — a missing or wrong normal map causes the specular highlight to use the diffuse as a specular source (bright/flat appearance). Also check `LAND VNML` (vertex normals) — these feed into terrain lighting.

---

### Harvested plants don't make a sound

TES4 `FLOR` records have a harvest sound in `DATA.Sound`. TES5 `FLOR` has `RNAM` (harvest sound `SNDR` reference). Check `convert_FLOR()` in `items.py` — is `RNAM` being emitted? If the `SOUN→SNDR` conversion ran first (pass ordering), the FormID should be mappable.
