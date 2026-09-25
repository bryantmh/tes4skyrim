# FO3/FNV mesh conversion

## <a id="fnv-skeleton-bones"></a>FNV skeleton bones

**Code:** `asset_convert/character/skyrim_overrides_falloutnv.py`

The FO3/FNV human skeleton (`meshes/characters/_male/skeleton.nif`) carries
**65 bones, 16 of which have no entry in `OBLIVION_TO_SKYRIM_BONE_MAP`**.
Armor weighted to an unmapped bone collapses to the origin, so those 16 were
the cause of FNV clothing deforming to a point.

FO3 kept Oblivion's `Bip01` spine, limb and finger names, so the two tables
are **merged** rather than swapped — only the renamed and added bones need
new entries:

| FO3/FNV bone | Skyrim bone | Why |
|---|---|---|
| `Bip01 L/RUpArmTwistBone` | `NPC L/R UpperarmTwist1` | FO3 renamed the twist |
| `Bip01 L/R ForeTwist` | `NPC L/R ForearmTwist1` | FO3 renamed the twist |
| `Bip01 L/R Thumb1/11/12` | `NPC L/R Finger00/01/02` | Oblivion has no thumbs |
| `Bip01 L/RPauldron` | `NPC L/R Clavicle` | shoulder plate rides the clavicle |
| `Weapon` | `WEAPON` | Skyrim's node is uppercase |

Three FO3 bones are dropped outright — they drive the FO3 rig itself and have
no Skyrim counterpart, so skinning must never target them:
`Bip01 R ForeTwistDriver` (procedural twist driver), `Camera3rd` and
`HeadAnims`.

Every target above was verified present in both
`skeleton_bones_falloutnv.json` and `skeleton_bones_skyrim_male.json` before
the table was written.

### <a id="rename-follows-the-source-table"></a>The rename pass must use the same table

`retarget_skin_to_skyrim` moved the FNV-only bones to their Skyrim positions
through the merged table, but `head_gear.remap_bone_names` looked names up in
`OBLIVION_TO_SKYRIM_BONE_MAP` alone, so the twist, thumb and pauldron nodes
kept their FNV names in the output. Skyrim's skeleton has no such bones and the
vertices weighted to them stop following the arm. On the Caravaneer Outfit
190 of the 362 `arms:0` vertices (the outer forearm and the upper arm) sat on
unrenamed bones -- the "lower arms splay outward" report -- and on the Great
Khan suit 139 of the fur vest's 331 vertices, all at the shoulders. 362 of the
686 FNV armor NIFs reference at least one of these names. The rename now picks
its table from the NiNode names the tree carries, exactly as the retarget does.

## <a id="fnv-biped-slots-mesh-side"></a>FNV biped slots, mesh side

**Code:** `asset_convert/character/wearable_plan_falloutnv.py`

`wearable_plan._BIPED_BIT_BODY_PART` is Oblivion's bit table. Read through it,
FNV Left Hand (bit 3) became LowerBody 44, Weapon (5) became Feet, PipBoy and
Backpack (6, 7) became Ring 36, bit 13 (Earrings) read as Shield, and hats,
glasses, masks and chokers had no entry at all and fell back to filename
guessing. A partition whose slot the ARMA does not claim is culled, so a glove
partitioned 44 under an ARMA claiming 33 is invisible.
`FNV_BIPED_BIT_BODY_PART` mirrors the importer's `FNV_BIPED_SLOT_MAP` (body
part = slot number; head gear = the converter's 131) and is selected by the
per-NIF source latch. Upper Body stays a single 32 partition:
[upper-body-covers-feet](tes4_export_falloutnv.md#upper-body-covers-feet).

### <a id="gender-from-the-record"></a>Gender from the record, not the folder

Every gender decision in the retarget was `'/f/' in path`. Oblivion files
female gear under `armor\<set>\f\`; FNV names it by suffix
(`republicanF_01.nif`, `greatkhan_v2_f.nif`). Of the 153 distinct female biped
model paths in FalloutNV.esm, 99 have no `f` folder, so those outfits were
fitted to the male Skyrim body and skeleton and then written into the ARMA's
female model slot. `build_plan` now records MALE / FEMALE bits per mesh from
the record's two biped model fields, `convert_nif` latches "female-only" per
NIF, and `mesh_is_female` answers from the latch first and the folder second,
so Oblivion's layout is unchanged.

## <a id="fnv-animation-pose"></a>FNV animation pose

**Code:** `asset_convert/character/skin_retarget.py` `load_animation_deltas`

Phase B.1 of the retarget pre-deforms armor into a pose that best matches the
Skyrim rest skeleton, using per-bone delta matrices mined from the source
game's own `.kf` corpus by `kf_animation_explorer.py --build-cache`.

The deltas are only valid for the skeleton they were mined against — they are
`inv(rest_world) @ anim_world` in that skeleton's bind pose. FO3/FNV bind
poses differ from Oblivion's and the corpora are different sizes (**2,008 FNV
human clips vs Oblivion's 538**), so each source game gets its own cache:
`best_animation_pose.json` for Oblivion, `best_animation_pose_falloutnv.json`
for FO3/FNV. Which one loads is decided by the source skeleton, the same
authored-bone-name test the bone map uses.

The cache is keyed by path rather than held in a single module global, so both
can be resident when a run converts meshes from more than one source.

### <a id="selected-by-authored-bone-names"></a>Selected by authored bone names

Which table applies is decided by the **skeleton's own bone names**, never a
plugin or file name: `Bip01 L Thumb1` and `Bip01 LUpArmTwistBone` exist only
in FO3/FNV skeletons. A plugin name would break for any mod built on a
Fallout skeleton, and per the project rule the authored data is the indicator.

## <a id="fnv-body-fitting"></a>FNV body fitting: Oblivion's field, FNV's pose deltas

The body-wrap field stays Oblivion's (the torso and leg rest poses agree
within half a unit) but the wrap's FK base did not. `deform_geoms_wrap`
called `load_animation_deltas()` with no source skeleton, so it posed every
mesh with **Oblivion's** `inv(rest) @ pose` matrices. FNV's rest pose holds the
arms lower (rest hand z 85.3 vs Oblivion 100.8, forearm 94.4 vs 101.9), so
Oblivion's arm deltas rotate FNV forearms from the wrong start and both come
out horizontal at elbow height, hands drooping (render of
`armor/antagonist/outfitm.nif`; the mangled-forearm report). The FNV pose
cache itself is right: its posed hand lands at (-28.8, 2.0, 72.6) against
Skyrim's (-28.9, 1.8, 72.8).

`deform_geoms_wrap` now takes the source skeleton, loads the matching delta
cache, and aliases FNV-only bone names (`Bip01 L ForeTwist`, the thumbs, the
twist bones) to the Oblivion bone sharing their Skyrim target for the field's
bone-centroid gate (`oblivion_alias_map`), so those weights count toward the
region instead of being skipped.

The size argument that justified sharing the field still holds:

| Bone | Oblivion z | FNV z | Skyrim z |
|---|---:|---:|---:|
| `Bip01 Pelvis` | 67.41 | 67.77 | 68.91 |
| `Bip01 Head` | 112.44 | 112.82 | 120.34 |
| `Bip01 L Foot` | 6.75 | 7.07 | 6.08 |

Under half a unit apart on the torso and legs, so FNV proportions are
Oblivion's there and a separate field would fit the same shape twice. The
arms are where the two rests differ, and that is a POSE difference the deltas
own, not a shape difference for the field.

### The raw-vertex trap

FNV body meshes look wildly out of range at first: `upperbody.nif` stores
vertices spanning **z = -102 to 111**, against the field's 0.1-126 domain, and
a naive nearest-neighbour check puts the worst FNV vertex 103 units from any
field point.

That comparison is wrong. FNV stores body vertices in **bone-local** space
where Oblivion stores them already in world space (Oblivion raw == world). The
pipeline feeds `geom_world` output to the field, and in world space FNV's body
sits at **z = 0.8 to 119.9** — inside the field's domain:

| Source | World verts | dist p50 | p90 | p99 | max |
|---|---:|---:|---:|---:|---:|
| Oblivion | 885 | 0.00 | 0.00 | 0.00 | 0.00 |
| FalloutNV | 2,532 | 1.47 | 5.47 | 12.85 | 15.12 |

Oblivion is 0.00 by construction (the field is built from it). FNV's median
1.47 units is well within the field's interpolation range.

Always measure against `geom_world` output, never raw vertices.

### Why an FNV-specific field could not be built anyway

FNV does not decompose the body the way `_OB_BODY_SETS` expects. There is
**no `lowerbody.nif` and no `foot.nif`** — one `upperbody.nif` covers the whole
body head-to-toe — hands are split left/right where Oblivion has a single
`hand.nif`, and the head lives outside `characters/_male` entirely. The FNV
body mesh also carries FO3 dismemberment gore caps (`bodycaps`, `limbcaps`,
`meatneck01`, `meathead01`) as sibling shapes, which are stumps rather than
body surface and would have to be excluded.

If FNV fitting ever does prove insufficient in game, the corrective is data
derived from the measured bind poses in this sidecar — never per-piece offsets
in `ARMOR_PIECE_OFFSETS`.

## <a id="dismemberment-gore-caps"></a>Dismemberment gore caps

FO3/FNV skinned meshes carry the stumps for limb dismemberment as their own
shapes (`limbcaps`, `bodycaps`, `meatneck`, `meathead`, `GorecapsBody`) whose
`BSDismemberSkinInstance` partitions are typed 101-113 (`BP_SECTIONCAP_*`) and
201-213 (`BP_TORSOCAP_*`). The FO3 engine hides those partition types at load
and reveals one when it severs that limb. The 1000+ values
(`BP_TORSOSECTION_*`) that share shapes with ordinary partitions are visible
torso sections and need nothing.

Census over 400 FNV armor NIFs (`temp` script, `read_nif` + pyffi):

| Shapes | Count |
|---|---:|
| dismember skin, every partition a cap | 475 |
| dismember skin, caps mixed with a stray partition 0 | 4 (135/170 and 87/126 triangles are caps) |
| plain `NiSkinInstance` | 54 |

Creatures (brahmin, centaur, deathclaw ...) ship the same cap shapes.

Skyrim has no limb dismemberment and `regen_skin_partition` rebuilds every
partition from the wearing record's slot, so the caps rendered as ordinary
body geometry: the "bloody portion always visible" report, on armor and
creatures alike. `dismember_falloutnv.hide_dismember_caps` sets the NiAVObject
hidden bit on any dismember-skinned shape whose cap partitions hold at least
half its triangles, before any skin conversion runs; `converted_node_flags`
and the geometry prep carry the bit through. The geometry is kept: hidden is
exactly the FO3 engine's own load state. The caps are deliberately not mapped
to `SBP_230_HEAD`: Skyrim's beheading stump comes from the race's decapitate
armor, and typing every limb cap 230 would reveal all of them on a beheading.

## <a id="multi-line-text-keys"></a>Multi-line .kf text keys

FO3/FNV pack SEVERAL text keys into ONE `NiTextKeyExtraData` value separated by
CRLF; Oblivion always writes one key per value. `parse_kf_events` matched
`sound:` against the whole value and took everything after the first colon as
the SOUN EditorID, so a two-key value became one "EditorID" holding a newline.

`project_block_lines` writes each trigger as one line of
`animationdatasinglefile.txt`, which is positional: a count, then exactly that
many trigger lines. An embedded newline wrote 2 lines where the count promised
1, so every later project in the file read the WRONG block -- the same desync
class as the singlefile poisoning, but fatal at load rather than merely silent.

Measured over `export/FalloutNV.esm/meshes/creatures/libertyprime`: 106 text
keys, 15 of them multi-line, producing 14 malformed `Sound: ` lines in the
shipped singlefile. `_classify_key` now takes one already-split key and
`parse_kf_events` splits each value on newlines first.

## <a id="fo3-havok-enums"></a>FO3/FNV Havok enums

**Code:** `asset_convert/collision/collision_material_falloutnv.py`

FO3/FNV share Oblivion's material enum only for indices 0-13 and diverge
completely above it, so an FO3 value routed through the Oblivion table is
silently mistranslated (FNV 16 HOLLOW_METAL reads as Oblivion "Cloth Stairs",
26 TRANSPARENT_SMALL as "Line Of Sight"). The two tables are selected by
source game and never merged.

### <a id="source-game-latching"></a>Source-game latching

A material index alone does not say which enum authored it, so
`register_fallout_nif(user_version_2)` latches the source game once per NIF
from the header (`user_version_2 == 34` is FO3/FNV) and `is_fallout_source()`
routes every later material and layer lookup in that NIF to the FO3 tables.

### <a id="materials--128-values-32-bases"></a>Materials: 128 values, 32 bases

FO3 material values run 0-127: bits 0-4 pick the base material, bit 5 marks
the platform variant and bit 6 the stairs variant of the same base, so a
32-row base table plus a stairs table covers every authored value.
`fo3_material()` returns the Skyrim material CRC; stairs variants map to the
stairs CRC of their base, defaulting to stone stairs.

### <a id="layers--diverge-from-19"></a>Layers: diverge from 19

FO3 collision layers match Skyrim's numbering below 29 and are renumbered from
29 (DEADBIP) onward; `FO3_TO_SKY_LAYER` maps each FO3 layer to its Skyrim
`SKYL_*` value and passes unknown values through unchanged.

## <a id="collision-rules"></a>Collision rules that differ from Oblivion

**Code:** `asset_convert/collision/collision_falloutnv.py`

Three FO3/FNV behaviours differ from Oblivion's and were each producing an
in-game symptom. The source game is latched once per NIF from the header
(`user_version_2 == 34`) before the Skyrim version is stamped over it.

### <a id="root-rotation-ignored"></a>Root rotation is ignored, as in Skyrim

Oblivion applies a NIF root's rotation; FO3/FNV and Skyrim overwrite the
root transform with the REFR's. Rotated roots are equally common in all three
games (Oblivion architecture 22/339, FNV 24/521, vanilla Skyrim 25/489), so the
rule is per engine. Settled by a seam test (`temp/root_rot_seam_test.py`):
place each rotated-root kit piece under both hypotheses and count vertices
coinciding with unambiguous neighbours in the same cell.

| Plugin / cell | root rotation applied | root rotation ignored |
|---|---:|---:|
| Oblivion 0001C646 (castle kit) | 285 | 34 |
| FNV 000FA230 (office hall) | 5 | 31 |
| FNV 000EC3A2 (vault) | 64 | 438 |
| FNV 0013BC26 (vault) | 52 | 310 |
| FNV 00103DF9 (craftsman homes) | 25 | 268 |

So the AUTHORED root rotation of a FO3/FNV mesh is zeroed, and nothing is
baked into the collision because FNV, like Skyrim, already placed the root
body at REFR ∘ bodyT. Root translations occur only on VATS camera rigs.

<a id="fnv-weapon-flip"></a>The zeroing happens in `_convert_one_root`
BEFORE the Prn seating, not inside `wrap_root_transform`, so a seating
transform applied by `convert_prn` survives to be wrapped into the inner
NiNode. The Oblivion weapon flip itself is skipped for FO3/FNV sources
([why](#weapon-track-rename)): it briefly shipped on FNV guns and only
looked right because the weapon track was not playing.

### <a id="two-sided-welding"></a>Random winding, repaired from the render mesh

FO3's `hkPackedNiTriStripsData` (20.2.0.7) has no per-triangle normal and its
winding is random: the render-skin oracle (`collision_winding_truth.py`)
scores 133 of 267 horizontal road faces inverted, 295 of 652 in nv_rocks;
the stock detector flags 265 of 290 office-kit meshes. FNV plays on these
files, so its Havok collides both sides. Skyrim's CMS carries a welding type
(`hkpWeldingUtility::WeldingType`: ANTICLOCKWISE 0, CLOCKWISE 4, TWO_SIDED 5,
NONE 6) and vanilla writes 0 in all 346 sampled blocks. Writing TWO_SIDED (5)
was tried first and the player still fell through roads and floors, so the
engine does not honour the byte; it is back to 0. FO3/FNV sources instead
always run the winding repair (`repair_inverted_floors`), which is otherwise
gated per plugin; the render mesh is the authored surface. Scored offline:
roads 133 inverted to 0, nv_rocks 295 to 0, office/hallsmall 2 to 0.

**Round 4 rebased this onto the twin/nearest rule** that replaced steps 1-3
([why](asset_convert_collision.md#morroblivion-collision-is-copied-render)).
The principle is the one this section already relied on — the render mesh is
the authored surface — so the rebase is a change of matching method, not of
evidence, and it matters more here than anywhere: FO3 packed data carries no
per-triangle normal, so step 0 is structurally inert and the render mesh is
the ONLY signal. Re-scored on 44 architecture meshes: **815 inverted -> 0**,
no residual on any mesh.

### <a id="static-collection-parts"></a>Static-collection parts

An SCOL hangs one fixed body per part off `HavokN` child nodes carrying
translation, rotation and scale (0.77 to 1.99 in scolparkinglotchunk03b) plus
a body transform. The engine formula is
`T_node + R_node · (s · R_body · v + t_body)`: the scale applies to the shape
only, and the GECK pre-scaled the body translation (a roadchunk03 instance at
0.91 stores 0.91 × the standalone body translation). With that formula the
union of all 14 parts matches the visual bbox within 5 units on every axis;
the Oblivion composition `R·(t·s) + T` double-scales it. Every fixed packed
part is baked into one root triangle soup, which is what the GECK did to the
render geometry, so no scaled child collision node reaches Skyrim (0 of 179
vanilla child-collision meshes carry one).

### <a id="fo3-layers"></a>Collision layers

FO3 layers 0-28 are Skyrim's own and 29+ are renumbered, but the Oblivion
table sent them through Oblivion's enum: 19 DEBRIS_SMALL (truck hulks) became
31 STAIRHELPER and 26 TRANSPARENT_SMALL (CLFenceDestroyed01, scaffold grates)
became 41 LINEOFSIGHT, a pick layer with no physical collision, so the player
walked through them. Census of 1,500 FNV world meshes: layer 1 x373, 4 x32,
3 x23, 10 x13, 13 x13, 2 x12, 19 x12, 26 x9, 5 x4, 15 x2, 6/9/14 x1.
`fo3_layer()` passes 0-28 through and renumbers 29+.

## <a id="gun-graph"></a>Guns are hand type 13, not crossbows

**Code:** `asset_convert/havok/gun_anim_falloutnv.py`,
`gun_graph_falloutnv.py`, `gun_patch_falloutnv.py`, `humanoid_graph.py`,
`tes5_import/record_types/equipment_falloutnv.py` (`gun_profile`).

Skyrim's `GetHandAnimType` (Address Library id 14220) answers an anim type per
equipped form, topping out at 12 for a crossbow. A gun is given a **new type,
13**: FalloutRuntime patches every call site of that function and of the one
routine that writes the value into a graph
(`BShkbAnimationGraph::SetVariableInt`, id 63609), so a WEAP listed in
`<plugin>.guns.json` answers 13 and the write of `iRightHandType = 13` is
followed by `iGunClass`, `iGunReload`, `iGunAttack`, `iGunClipSize` and
`iGunAuto` from the same sidecar. Nothing plays a crossbow animation.

The SSE humanoid graphs are patched in place through `external/hkxconv`
(decompile to XML, edit, recompile). `1hm_behavior` gains a
`TES4Gun_AttackState` entered on `crossbowAttackStart if iRightHandType == 13`
ahead of the vanilla transition — that event is the only shot trigger the
engine sends for a ranged weapon, so it is reused as the entry signal rather
than as an animation choice.

### <a id="attack-event-idle-tree"></a>Where `crossbowAttackStart` comes from

The executable never references the `bowAttackStart` /
`crossbowAttackStart` literals except to *recognize* them (one compare each
in the attack-state receiver `ActorMediator` calls, id 39004); no RACE `ATKE`
and no animationsetdata attack block names them either (0 of 87k RACE lines,
0 of 74k set lines). The sender is the **IDLE tree**: an attack press runs
`PlayerControls` → BGSAction `ActionRightAttack` → `TESActionData` →
`ActorMediator::Process`, which finds the action's root IDLE, walks its
children evaluating their CTDAs, and sends the winner's `ENAM` string.
Skyrim.esm `BowAttack` (0005177C) is `GetEquippedItemType == 7`; Dawnguard's
crossbow idle is `== 12`; the fallthrough child is `attackStart`, the melee
attack. `GetEquippedItemType` (condition function 597, id 21677) calls
`GetHandAnimType`, so a hook that answers 13 there fails the crossbow idle
and the gun plays the melee attack with no `arrowRelease` and no ammo spent.
The hook therefore answers the vanilla type (12) to that function and to the
Papyrus `GetEquippedItemType` native (id 54685), and 13 to the graph writer
and the animation-set selectors only.

### <a id="turn-clips-are-overlays"></a>Turn clips are lower-body overlays

FNV composes a stance by per-block priority, not by whole clips. Measured
on the male rig: `mtidle` is priority 10 on every bone; `1hpaimdown` is 25
on the root/pelvis/legs, 30 on the spine and head, 35 on the arms;
`1hpturnright` has only 15 blocks (Bip01, NonAccum, pelvis, both legs, the
four twist bones) at 30; `1hpforward` is 30/31 on the body and 35 on the
arms. So standing still the AIM clip wins the whole body over the base idle
and turning overlays the legs on it. Played alone, a turn clip leaves 44
bones at the skeleton rest, a
T-pose; `ready_machine` now blends the turn clip's lower body under the aim
pose with the vanilla crossbow bone-weight arrays. `1hpaim.kf` (the level
pitch) is not in the FalloutNV BSAs; the level pose is the fire clip's
first frame ([why not the down/up midpoint](#level-aim-from-the-fire-clip)).

The jump, sprint, shout and horse selectors key on `iRightHandEquipped` /
`iLeftHandEquipped`, which the engine fills from the same hand-type routine
(id 38821) while the weapon is drawn, so they are in `HAND_TYPE_VARS` and
get a type-13 entry like the `iRightHandType` slots.

### <a id="accum-root-identity"></a>The accum root plays as identity; NonAccum carries the 90°

**Code:** `clip_retarget.py` `_source_locals`/`retarget_clip`,
`kf_decode.py` `split_root_motion` / `ACCUM_ROOT_BONES`,
`hkx_anim.py` `_drop_unanimated_root`.

Every FNV human clip follows the exporter convention in
[asset_convert_animation.md](asset_convert_animation.md#animated-object-behaviour-graphs):
the sequence's accum root `Bip01` is absent or an identity track, and
`Bip01 NonAccum` carries the body's real transform. Census of the 94 `_male`
gun and locomotion clips: 84 have no `Bip01` track, 10 an identity one, and
all 94 author NonAccum at 83–90° yaw (the skeleton's `Bip01` rest is the
same 90°). The engine applies the identity to `Bip01`, so NonAccum's 90° is
the whole facing.

The retarget did neither. `_source_locals` posed a track-less root at the
NIF rest (90°), and `split_root_motion` flattened NonAccum's rotation to
identity whenever it held translation motion (`mtidle` breathes). The two
errors cancelled on `mtidle`, and `1hpforward` authors a `Bip01` track (so
only the NIF rest was skipped, 87.5° kept on NonAccum), but a static clip
such as `1hpaimdown` got both: 90° + 90°, the drawn pistol stood facing
90° left and turned straight the moment locomotion played. Measured
frame-0 pelvis forward axis: aimdown (-1, 0, 0), mtidle and forward
(0, 1, 0).

Now the source root is always identity, `split_root_motion` flattens the
winner to its authored first sample (NonAccum keeps its heading), and the
root is never mapped, so `NPC Root [Root]` stays at rest and
`NPC COM [COM ]` carries the yaw, as vanilla clips do. `1hpforward` had
been writing `NPC Root` at -90° with COM at +87.5° to compensate.

**Creatures obey the same contract, and it is the whole floating/facing
story.** Census over every creature skeleton with an idle KF (FalloutNV 39
of 42, Oblivion 32 of 44, Morrowind_ob 40 of 64, Nehrim ~45 of 70): the
KF's frame-0 `Bip01 NonAccum` rotation IS the NIF's `Bip01` bind rotation
(super mutant yaw 90°, centaur the (0.5,-0.5,-0.5,-0.5) axis permutation),
and its translation is the same pose (smspinebreaker NIF Z 88.13 vs KF
79.47, deathclaw 105.23 vs 96.23). The NIF stores the biped's world pose on
`Bip01` with NonAccum at identity; the KF stores it on NonAccum. The engine
plays it ONCE — the accum root is engine-owned — or every actor would stand
at double height. Two symmetrical failures came from breaking that:

| track 0 | track 1 | in-game |
|---|---|---|
| skeleton rest pose (old fallback) | authored | floats at 2× height; both-90° attacks face 180° |
| identity | flattened to identity | every super mutant 90° right; centaur on its back |
| identity | authored first sample | correct (user-verified, FNV) |

So `_drop_unanimated_root` writes bone 0 as identity when the clip has no
`Bip01` track, `ACCUM_ROOT_BONES` tracks are zeroed after motion extraction
(a static authored `Bip01` such as ashvampire `idle.kf`'s 54.2°/Z 85.17 is
a copy of the bind pose, not a pose), and NonAccum's authored frame 0 is
kept. The user's 386 hand-corrected FNV clips compose to the same pose as
ours in 365; the 21 others are their unfixed alien/mirelurk/Liberty Prime
double-heading attacks and the centaur/sentrybot, where their track 0 still
rotated NonAccum's height out of Z.

### <a id="jump-and-sprint"></a>Jump and sprint hold the gun

**Code:** `gun_moves_falloutnv.py`, `gun_patch_falloutnv.py`
(`patch_master`, `patch_sprint`).

The six `Jump*_MSG` selectors in `0_master` and the two `Sprint*Side`
selectors in `sprintbehavior` key on `iRightHandEquipped`, so a drawn gun
used to jump and sprint as the cloned crossbow entry. Vanilla's crossbow
jump is the model: a `BSBoneSwitchGenerator` whose default is the plain
`MT_Jump_Behavior` and whose one overlay plays `CrossBow_IdleHeld` on the
`RightHandAndQuiver` character property (a bone-weight array in the
character file). The gun entry copies that shape with the class' aim pose
on the `Arms` property, which the character file defines as both arms,
both hands, their fingers and the `Weapon` node, so a rifle keeps both
hands on the gun. FNV has no jump-start clip (only `jumploop`/`jumpland`)
and no sprint at all, so the overlay is the whole answer for the jump
selectors, and sprint plays the class' `fastforward` run clip in both side
selectors (the blend of two copies is the clip itself).

### <a id="stale-slot-13"></a>Vanilla already has a state 13

`1HM_Readied_BehaviorGraph` (in `1hm_behavior` and its first-person copy)
ships a leftover `State00` at stateId 13 holding
`CrossBow_Standing_Locomotion_Behavior`, which no vanilla type reaches.
`extend_type_slots` used to skip a machine that already had the new id, so
the gun ready machine was never installed and a drawn gun stood in the
vanilla crossbow locomotion. A pre-existing entry at the new type is now
overwritten; the census over all 24 humanoid graphs found only these two. Per class the
state runs fire (A/B alternation for automatics) → reload once the WEAP's clip
size is spent → done, over a standing/moving lower body.

The shot is FalloutRuntime's: each clip carries one `TES4GunShot` trigger at
the FNV `Hit` text key and the DLL fires the gun on it
([the shot](tes_runtime_guns.md#the-shot)); the bow event trio it used to
raise went through the engine's attack state machine, one cycle per shot,
which is why nothing fired at a gun's rate
([why not the crossbow](tes_runtime_guns.md#why-not-the-crossbow)).
`reloadStart`/`reloadStop`/`bowEnd` do not exist in the executable and
are graph-internal here. Shots are counted by idempotent per-frame
`hkbEvaluateExpressionModifier` assignments rather than by an event, so a
dropped frame cannot desync the count.

<a id="fire-rate"></a>**Fire rate is FNV's own rule: the attack clip plays
at the gun's `AnimAttackMult` and the next shot is accepted at its `a:`
key.** Measured on the 9mm: `DNAM.AnimAttackMult` 1.25, `1hpattack3` next
key `a:3` at 0.400 s, and the record's `ShotsPerSec` is 3.125 =
1.25 / 0.4. Before this a shot cost the whole clip plus two 0.3 s root
blends, about a second per trigger pull. Now the fire clips bind
`playbackSpeed` to `weaponSpeedMult`, the engine variable the vanilla
`CrossBow_Release` clip binds, filled from the WEAP's DNAM speed, which a
FNV gun imports as its `AnimAttackMult`; the fire clip triggers
`AttackWinStart` and `attackStop` at the `a:` key and `AttackWinEnd` at its
end, exactly the vanilla release clip's trio; the fire states take
`crossbowAttackStart` to the other fire state under that initiate window
(`FLAG_USE_INITIATE_INTERVAL` on events 30/31, as vanilla); and the root
leaves the gun attack state on the graph-internal `TES4GunAttackEnd` from
Done instead of on `attackStop`, so an early `attackStop` no longer cuts
the clip. A clip with no `a:` key opens the window at its last frame.

<a id="automatic-fire-rate"></a>**An automatic fires once per loop, so the
loop plays at rate × loop duration.** The 9mm SMG (`AttackAnim` 74,
`attackloop`) has `FireRate` = `ShotsPerSec` = 11 and `AnimAttackMult`
1.0, and `2haattackloop` is 0.167 s with no fire key and no `a:` key: at
`weaponSpeedMult` = 1.0 that is six loops, so six shots, per second at
best, and in game it fired far slower than that. A gun flagged automatic
(`DNAM.Flags1` bit 1) now imports its `ShotsPerSec` (else `FireRate`) as
the WEAP speed instead of `AnimAttackMult`, and each class' fire states
carry a per-frame `fGunLoopSpeed = weaponSpeedMult * <loop duration>`
assignment (a REAL graph variable, initial 1.0) that the `attackloop`
clips bind `playbackSpeed` to, so the SMG's loop runs at 1.83× and its
trigger trio fires 11 times a second. Non-loop attacks keep the
`weaponSpeedMult` binding. Whether the engine's crossbow release path
keeps up at that rate is measured in game, not assumed.

<a id="reload-on-the-window"></a>**The empty-magazine check sits on the
re-attack window too.** The reload transition was only on
`TES4GunFireEnd`, but a held or spammed trigger takes the
`crossbowAttackStart` window transition to the other fire state at the
`a:` key, before the clip ends, so the magazine count ran past the clip
size until the player stopped firing. The window now carries a second
`crossbowAttackStart` transition to Reload at higher priority, without
`FLAG_DISABLE_CONDITION`, conditioned on `RELOAD_COND`.

<a id="fire-transitions-are-instant"></a>**The fire machine's transitions
carry no blend effect, and the graph no longer counts shots.** Two builds
counted the magazine inside the graph with per-frame
hkbEvaluateExpressionModifier assignments (`iGunShots = iGunBaseB + 1`,
re-based by Done and Reload). Under the shared 0.3 s crossfade FireA and
Done were both active for ~18 frames and every frame counted a shot ("two
shots then reload"); with instant transitions (`instant=True`, a null
transition) the count still ran past a 13-round clip after one shot, a
reload clip playing straight after the fire clip's `TES4GunFireEnd`.
FalloutRuntime already counts shots for the HUD, so it now writes
`iGunShots` into every graph of the actor on each shot and resets it on
`TES4GunReloadEnd` (`SetActorGraphInt`); the fire states carry only the
automatic's loop-speed assignment. The transitions stay instant since the
fire clips end at the aim pose.

Every other humanoid graph (bash, block, sprint, stagger, magic, mounted,
first person) has its type-12 slots cloned to 13, so no graph is short an
entry; vanilla ships 12-entry left-hand selectors against 13 types and the
engine clamps. Iron-sight variants are skipped — no zoom variable exists in
the graphs to blend them — and first person keeps the cloned crossbow entries,
since no FNV first-person clips are converted.

## <a id="gun-animations"></a>Gun clip selection

**Code:** `asset_convert/havok/gun_anim_falloutnv.py` (`select_stems`,
`weapon_bindings`), `gun_vocabulary_falloutnv.py`.

The gun/clip binding is authored on the WEAP `DNAM`, so the exporter must
emit all three fields the graph keys on. Their offsets (`wbDefinitionsFNV`,
`wbStruct(DNAM…)`) are **12 Flags1**, **15 Reload Animation**, **41 Attack
Animation** — note 13 is Grip Animation, not attack. The exporter originally
read only the first 12 bytes, so all 265 FalloutNV WEAPs exported without
them; every gun then fell back to `reload=0` (`ReloadA`) and `attack=-1`.

`DNAM.ReloadAnim` indexes `RELOAD_LETTERS` (`abcdefghijklmnopqrswxyz` —
`wbReloadAnimEnum` order: A..S, then W, X, Y, Z), and `DNAM.AttackAnim` is a
sparse enum (26 AttackLeft, 32 AttackRight, …, 255 DEFAULT) whose values are
listed in `ATTACK_ANIMS`.

**The weapon bone is `Weapon` in the Havok skeleton, `WEAPON` in the NIF.**
`FALLOUT_TO_SKYRIM_BONE_MAP` names the NIF node, and `Skeleton.index` is
case-sensitive, so the FNV `Weapon` track (`1hpequip.kf` carries one: the
draw from the holster turns the bone 100°+ over 12 frames) was dropped on
retarget and the gun sat rigid at Skyrim's rest offset in the hand. `_rig`
resolves map targets case-insensitively against the hkx bone names; a
mapped bone whose source is rigid to the hand retargets to exactly the rest
local, so the change only adds the authored relative motion.

<a id="weapon-bone-verbatim"></a>**The weapon bone keeps the source's world
transform.** The gun mesh is authored in FNV's `Weapon` frame: `9mm.nif`
runs along the bone's +X (extent −4.4..15.4 on X, 12.7 on Y, 2.5 on Z),
where a Skyrim blade runs along the WEAPON node's +Y (`1handsword.nif`
−11.6..58.1 on Y). The rotation retarget writes every bone as Skyrim's rest
times the source's deviation from its own rest, and the two rests disagree
by that quarter turn, so the drawn pistol pointed back at the holder. Bones
in `VERBATIM_BONES` (`Weapon`) now take the source's world rotation and
the source's offset from the parent unchanged, which puts the FNV mesh
exactly where FNV's hand held it. Vanilla clips still pose the node the
Skyrim way, so a gun held through a vanilla state shows the quarter turn.

<a id="level-aim-from-the-fire-clip"></a>**The level aim is the fire
clip's first frame, never the down/up midpoint.** With the weapon bone
verbatim the drawn pistol still pointed straight back, unchanged, because
the held pose was not the verbatim bone at all: base FNV ships no
third-person `1hpaim.kf` (only `1hpaimdown`/`1hpaimup`; the DLC and TTW
archives add one for the classes they touch), and the pose blender's level
was the 50/50 blend of down and up. Measured on the retargeted clips, the
`Weapon` X axis (the barrel) is (−0.11, −0.19, −0.98) in `1hpaimdown` and
(−0.29, 0.12, 0.95) in `1hpaimup`: 180° apart, so their quaternion
midpoint is equally forward or backward, and the hand and weapon took the
backward path while the body bones, which move far less, blended sanely.
`1hpattack3` frame 0 has the barrel at (−0.007, 1.0, 0.0), exactly
forward: every FNV attack clip starts from the level aim pose, which is
also the pose the partial clips are filled from. So a class (per stance
prefix) with `aimdown` and no `aim` now converts its first attack clip's
first frame as the `aim` stem (`first_frame_pose`, a looping two-frame
clip with no keys), and `_fill_clip` and the locomotion stand-ins fall
back to the same clip. The pitch blender then has a real level child, and
its neighbours are only 90° away.

<a id="weapon-track-rename"></a>**The weapon track never reached the file.**
With the level aim fixed the pistol still pointed backwards, and flipping
the mesh 180° (`_apply_axe_flip`, [below](#fnv-weapon-flip)) "fixed" the
pistol while the shotgun sat pitched into the ground, off the hands
(screenshot 114, first person). Offline every class agrees, in both views:
the retargeted `Weapon` X axis (the barrel) is (0, 1, 0), and the node sits
where FNV's hand held it (2hraim: 8 units from the right hand, as
authored). So the game was not playing the track. `clip_to_animation_data`
renames every track bone through `BONE_RENAMES`, the Oblivion-creature
table, which maps `Weapon` to `WEAPON`; the Skyrim `skeleton.hkx` bone is
spelled `Weapon`, so the renamed track matched nothing and the writer
emitted the skeleton's reference pose for it: Skyrim's rest offset under
the hand, whatever the hand does. For the pistol that rest offset is 180°
about the bone's Y from FNV's frame (hence the flip looked right); for a
rifle it is a different rotation, hence the pitch. A track already named
for a skeleton bone now keeps its name (`_merged_tracks`); the rename
applies only to names the skeleton lacks. The flip is skipped for FO3/FNV
sources: FNV attaches the weapon NIF to `Weapon` with identity, and the
barrel is the mesh's +X, which is the bone's X.

**A declared reload letter need not exist for the class.** All 12 `2hh`
handle weapons (minigun, flamer, gatling laser, plasma caster) declare
`ReloadA`, but the authored `2hh` corpus runs `b`..`g` — there is no
`2hhreloada.kf`. Selection therefore falls back to the class' first available
letter (`_reload_fallbacks`), and a class with no reload clip at all builds a
fire machine with no reload state, which is also the correct behaviour for a
belt-fed weapon. A class with no *attack* clip is dropped from
`present_classes` entirely and reuses the first class' machine.

## <a id="first-person-rig"></a>First-person gun clips

**Code:** `gun_anim_falloutnv.py` (`_rig`, `fill_missing_tracks`,
`convert_gun_clips`), `gun_patch_falloutnv.py` (`VIEWS`),
`tools/generators/skeleton_hkx_json.py`.

The first-person project is its own graph set (`_1stperson\behaviors`),
character file (`_1stperson\characters\firstperson.hkx`, rig
`CharacterAssets\skeletonFirst.hkx`) and animation-cache project
(`FirstPerson.txt`). It used to get only the cloned crossbow slots, so a
drawn gun showed the vanilla crossbow arms. Now the four patched graphs and
the registration run once per view over a per-view clip set. The
first-person clips come from `characters\_1stperson` and land under
`_1stperson\animations\<game>guns`: **a project's animation names resolve
relative to the project's own folder** (vanilla keeps `1HM_1stP_Run.hkx`
under `_1stperson\animations`), so the same `Animations\<GAME>Guns\<stem>`
name serves both projects. Written under the third-person folder instead,
the first-person graph reached our states (live: both graphs at
`iRightHandType` 13 with the gun variables) but every bone sat at the bind
pose, arms out of the camera's view.

**The two rigs share the 99-bone order** (`skeleton_hkx_json.py` dumps
both; the first-person one differs in proportions: calves 31.3 vs 35.6,
forearms 20.3 vs 22.8, hands rotated a few degrees) but not the hierarchy
above the pelvis: `NPC Root → LookNode (z 120.5) → Translate (−56.37) →
Rotate → COM`, with `Camera1st` under the root at 120.5. FNV's first-person
skeleton is the same idea, `Bip01 → NonAccum → Looking (118) → Translate
(−118) → Rotate → Bip (67.77) → Pelvis`, `Camera1st` under Looking at
y −3.74, and every shared body bone has the SAME world transform as the
male rig, so the mined pose deltas apply unchanged. The map adds
`Looking→LookNode`, `Translate`, `Rotate`, `Bip→COM`, `Camera1st`, and drops
`NonAccum` (at the origin here; mapping it would put COM on the floor).

**Translations are anchored on the look node.** Only the camera-relative
geometry is visible, and the rigs differ in camera-to-COM height (56.4 vs
50.2), so the translated bones (`LookNode`, `COM`, `Camera1st`) take the
source's offset from `Bip01 Looking` added to `NPC LookNode`'s rest, not the
target's own rest plus the source deviation as the third-person retarget
does. `Translate`/`Rotate` keep their vanilla rest locals so the engine's
pitch pivot stays where vanilla puts it.

**Partial clips are overlays on the aim pose.** FNV's first-person
locomotion clips carry five tracks (`Bip01` root motion, `Camera1st`,
`Translate`, `Rotate`), attacks 38 of 71, reloads 60; every absent bone kept
whatever the higher-priority aim clip held. A Havok clip plays an absent
track at the skeleton rest, a T-pose, so missing tracks are filled with the
class' level aim clip's first frame before the retarget. `1hpaim.kf` exists
only in `Update.bsa`
([asset_convert_mod_ingest.md](asset_convert_mod_ingest.md#update-bsa)).

**Only the pistol has first-person locomotion of its own.** The
`_1stperson\locomotion` folder holds `1hp*`, `1hm*` and `mt*` (plus a
`sneak2hh` set); a rifle walks on the base `mt<direction>` camera bob over
its aim. `_plan_view` converts those eight `mt` clips once per class that
lacks a `<class>forward`, filled with that class' aim and named
`<class><direction>`, so `loco_machine` needs no special case. FalloutNV:
67 selected + 40 stand-in first-person clips, 138 clip generators in the
FirstPerson project against 310 in DefaultMale/DefaultFemale.

<a id="first-person-hands-spread"></a>**The rotation retarget spreads the
hands and moves the sight.** Measured in each rig's `Camera1st` frame, frame
0 (scratch script, FNV source vs `retarget_gun_clip` output): FNV's own
iron-sight poses already center the `##SightingNode` side to side (0.03
units off in `1hpattackleftis` with `9mm.nif` and in `2hraimis` with
`varmintrifle.nif`) and stack the hands (5.8 and 24.9 units apart). After
the retarget each hand sits 2-3.5 units further to its own side (hand-to-hand
error 4.7-5.5 in all four clips measured), and the sight moves with the right
hand, 2.9 units (pistol) and 8.8 (rifle), because `Weapon` keeps FNV's
offset from the Skyrim hand. Every arm segment pushes outward: Skyrim's
clavicle-to-shoulder offset is 14.04 against FNV's 11.24 and points
outward, the upper arm is 20.26 against 17.36, and the forearm, the segment
that brings each hand in towards the middle, is 16.05 against 18.13. The
rifle adds a whole-torso shift: Skyrim's spine starts 6.7-7.6 units behind
FNV's from the same COM, which the aim's torso twist turns into a sideways
move of both shoulders. So both the sight offset and the loose left hand are
the retarget keeping Skyrim's proportions, not a missing runtime step.

The first-person retarget now ends with a two-bone IK per arm
(`FIRST_PERSON_ARMS`, `clip_retarget._chain_fix`): after the rotation pass,
each Skyrim upper arm and forearm bend and swing so the hand reaches the
FNV hand's position (look-node anchored, like the camera), then roll about
the shoulder-to-hand line so the elbow points the way FNV's does, projected
onto that line's plane (the unprojected elbow direction rolled the hand off
target by 5-6 units). The hands keep their own rotation, so `Weapon` and
the sight follow. Measured after: hands, `Weapon` and `##SightingNode` at
0.00 units from FNV's camera-relative positions in `1hpattackleftis`,
`1hpattack3is`, `2hraimis` (frames 0 and 20) and `2hraim`; the pass adds
0.13 s to a 91-frame clip. The third-person rig has no camera anchor and
keeps the rotation-only retarget.

An iron-sight clip is composed over its own stance first (`_fill_clips`:
iron aim, then hip aim), as is a synthesized iron aim; the hip-only fill
dropped `1hpattack3is`'s left forearm and hand (no tracks) 41 units below
the camera. That clip's authored left upper arm still hangs, as in FNV.

<a id="camera-kick"></a>**The fire clips move the camera.** Skyrim's
first-person camera is the `Camera1st [Cam1]` node's world transform
(`FirstPersonState` +0x58 is `GetObjectByName("Camera1st [Cam1]")`; its
GetTranslation reads the node's world translation, GetRotation its world
rotation plus the look pitch), as FO3's is (the camera code reads the
`Camera1st` node's position and rotation, `fFirstPersonCameraMult` 1.0
only blends toward `Camera3rd` while switching views). FNV's fire clips
animate that bone (`2haattackloopis`: 3.4 units forward, 1.4 degrees per
shot) and the retarget keeps it exactly, but the attack machine's
upper/lower blend reuses the vanilla crossbow weights, which give
`Camera1st` (with the root, look node and COM) to the standing pose: the
camera never moved. The first-person fire and reload states now take
copies of those weights with the camera bone on the fire clip
(`_camera_on_upper`; the manifest records the rig's camera bone index,
97). Confirmed in game on the 10mm pistol.

## <a id="gun-parts"></a>Gun parts: the magazine, slide and bolt

**Code:** `asset_convert/nif/gun_parts_falloutnv.py`,
`asset_convert/havok/gun_anim_falloutnv.py` (`parts` in the manifest),
`asset_convert/havok/gun_graph_falloutnv.py` (`GunGraphBuilder.clip`),
`asset_convert/havok/gun_patch_falloutnv.py` (`_prepare`).

FNV animates a gun's moving parts from the ACTOR clip: `1hpreloada.kf`
carries tracks for `##Clip` and `##Slide`, `2hrreloada.kf` for `##HRBolt`,
`##HRClip`, `##HRTrigger`, and `1hpequip.kf` for 24 such nodes; the `##`
prefix marks a node shared by every weapon mesh that has it, and no FNV
weapon mesh holds a NiControllerSequence of its own (0 of the exported
weapon meshes). The clip conversion keeps only skeleton bones, so those
tracks were dropped and the magazine never moved. Skyrim animates a
weapon's parts the other way round: a sequence inside the mesh, started
by an animation event of the same name, through the sub-graph an
attached mesh gets from its BGED (the crossbow string; Papyrus
`PlaySubGraphAnimation` "sends the event to the actor's sub graphs, used
for objects attached to actors"). So each FNV weapon mesh gains, for
every gun clip whose `##` tracks name one of its nodes, a
NiControllerSequence named after the clip stem (transform interpolators
sampled from the clip, `start`/`end` keys, a managed
NiTransformController per node), and the actor clip raises the stem as a
trigger at its first frame (the manifest records `parts` per clip; the
patch registers those stems as graph events). FalloutRuntime starts the
sequence on that event (`parts.cpp`): the weapon root under the actor's
`WEAPON` bone, its NiControllerManager, the name map and
`NiControllerSequence::Activate`, the exact path of
`ObjectReference.PlayGamebryoAnimation`; only a gun holder's events are
looked up, and only NiNodes (vtable slot 3, `AsNiNode`) are walked, since a
build that read the child array off every object crashed on a nocked
vanilla bow's trishapes. A first build gave each weapon
mesh a BGED sub-graph instead (the animated-object pass); attached to the
player it broke the actor's own graph: T-pose in third person, invisible
arms in first, no gun animation at all. The sequence plays at 1.0 while a
fire clip plays at `weaponSpeedMult`; reload and equip clips play at 1.0,
so only the slide of an automatic can drift.

## <a id="dismemberment"></a>Limb dismemberment at runtime

**Code:** `tes5_import/record_types/bodypart_falloutnv.py`,
`tes_runtime/fallout/sever.cpp`.

TES5's BPTD cannot hold FNV's limb data: the engine keys dismemberment on six
part types, and FO3/FNV author many more. The import therefore writes a
sidecar (`SKSE/Plugins/FalloutRuntime/<plugin>.bodyparts.json`) beside the plugin
and mints one **MSTT per gore model** so a severed limb has a real form to
place — `derive_formid('BPTD_LIMB', <model path>)`, keyed on the authored
path.

At runtime every melee and projectile hit lands in one routine
(`Actor::ApplyHit`, id 38586, 8 call sites, all patched). A hit that takes
health to zero severs the nearest severable or explodable part; a melee
`HitData` carries no impact point, so the distance is measured from the
attacker instead. The limb is hidden by partition, and the MSTT is dropped
with the PlaceAtMe / SetPosition / ApplyHavokImpulse natives at the bone.

Severed parts persist in the SKSE co-save (record `SEVR`) and are re-applied
on the engine's own 3D-load re-apply call (`ReapplyDismemberment`, id 37644)
and after a game load, so a limb does not grow back when the cell unloads.

Both sidecars carry plugin-local FormIDs plus the owning file, resolved
through the running load order at DataLoaded, so no load position is assumed.

## <a id="ammo-prn"></a>An AMMO model hangs on QUIVER

Skyrim attaches an equipped AMMO's model to the actor at the node its NIF
names in the `Prn` NiStringExtraData (Oblivion arrows carry `Quiver`,
remapped to `QUIVER`); a NIF without one attaches at the skeleton root, so
FNV's ammo boxes, which never hang on an actor in FNV and carry no `Prn`,
appeared at the player's feet and outside the QUIVER node FalloutRuntime culls
for a gun holder. The wearable plan now flags every `AMMO` record's
`Model.MODL` (`QUIVER`), and `convert_prn` gives such a root without an
authored `Prn` the value `QUIVER` with no seating transform or inventory
marker, so the box sits under the node the DLL hides.
