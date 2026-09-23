# asset_convert/character/body_wrap.py — worn armor, skin and fitting

**Code:** `asset_convert/character/body_wrap.py`, `asset_convert/character/skin_retarget.py`, `asset_convert/character/skin_replacement.py`, `asset_convert/nif/inv_marker.py`, `asset_convert/character/bow_rig.py`

## One offset per NIF, and which slot picks it
<a id="armor-offset-slot"></a>

**Code:** `_offset_slot` in `asset_convert/nif/nif_converter.py`

ONE offset applies to the whole NIF, so a record claiming a SINGLE biped slot
answers the question outright. A multi-slot record has no single stated answer —
the Knight of Order armour is helmet, torso, legs and feet in one mesh, flags
`0x003D` — and taking its head-ward slot **lifted the entire suit by the
helmet's dz=+7**: its Foot shape floated from z -1.3 to +5.9.

Those are resolved instead by where the mesh's skinned vertex MASS actually
sits, which is the rig the artist authored. Per-SHAPE slotting is handled
separately inside the retarget.

The same rule governs the body-splice fill: a fill partition takes the piece's
primary biped slot, because an ARMA only renders partitions for slots it claims
— a slot-49 pants ARMA culls a partition-32 fill, leaving invisible skin holes.

## <a id="biped-slot-conversion"></a>Biped slots: equipment conflicts vs body coverage

**Code:** `tes5_import/record_types/common.py` `_convert_biped_flags`

The source record's biped flags become the TES5 BOD2 first-person flags, and
the conversion adds **equipment-conflict** extras only: a helmet also claims
the Circlet slot so the two cannot be worn together (`BIPED_SLOT_EXTRA`).

**Body-coverage** extras are deliberately NOT added here — a cuirass covering
the forearms goes on the ARMA via `ARMA_BODY_COVERAGE_EXTRA` instead. Putting
coverage on the ARMO would make the ARMO occupy slots it does not equip, so it
would conflict with every other item using them. The ARMA slots decide which
body partitions are hidden; vanilla derives them the same way: IronCuirassAA
Body(32) + ForeArms(34) + Calves(38), IronBootsAA Feet(37) + Calves(38),
IronGlovesAA Hands(33) + ForeArms(34), IronHelmetAA Hair(31) + Ears(43).
Converted lower-body pieces take LowerBody(49) + Calves(38).

When two worn addons claim one slot, the higher ARMA DNAM priority draws it
(CK wiki `ArmorAddon`: naked body 0, torso 5, gloves over sleeves 10).
Skyrim.esm: 111 body-armor ARMAs at 5 and 92 skins at 0, against 51 hand
ARMAs at 10. Every converted ARMA was written at 10, so a converted cuirass
tied a vanilla gauntlet for ForeArms(34) and won it, hiding the gauntlet's
forearm; body and lower-body armor (slots 32, 49) are now written at 5, so
gloves win 34 and boots win Calves(38).

Oblivion hands claim both hands (33 and the right hand, 59) as an equip
conflict, so a glove hides both split hand files
([body slots](#body-slot-layout)).

Which table supplies the bits depends on the source game: Oblivion's 16-bit
set uses `BIPED_SLOT_MAP`, FO3/FNV's 20-bit set `FNV_BIPED_SLOT_MAP` — they
share only bits 0-2, see
[tes4_export_falloutnv.md](tes4_export_falloutnv.md#fnv-biped-slots).

## Head gear is fitted by MEASUREMENT, not by a scale
<a id="head-gear-fit"></a>

**Code:** `_apply_head_and_offsets`, `_retarget_worn_armor` in
`asset_convert/nif/nif_converter.py`

The two skulls differ in SHAPE, not by a factor. In world space the Oblivion
head spans z **106.84..126.04** (19.20 tall) and the Skyrim head z
**109.33..131.85** (22.52) — the Skyrim skull reaches **5.4 further down AND 2.1
higher at the crown**. No single scale expresses that, which is why the old
`ARMOR_PIECE_OFFSETS_PRN['helmet']` affine could never stop the back of the head
poking through.

Every Prn block hanging on the HEAD bone — helmets, hoods, hair — is instead run
through `head_fit`: each vertex keeps its authored signed distance from the
Oblivion skin, measured against the real Skyrim head. A helmet authored 2 units
off the skull stays exactly 2 units off, and the skull can no longer poke
through anything that covered it in Oblivion. Converted hair is fitted upstream
in `hair_pipeline.bake_hair_variant` and must not be touched again.

Everything else keeps the previous rules: skinned geometry is exact under the
wrap (offsets suppressed), non-head Prn pieces such as shields keep their
near-zero PRN offsets, and the FK-tuned constants remain the fallback whenever
the fit or field data is unavailable.

**Beast races get their own mesh**, exactly as vanilla ships one. A hood is ONE
Oblivion record worn by every race, so unlike hair — whose EDID names its race —
there is nothing on the record to read: the only way to serve a khajiit and a
human from one source is to write a mesh per race and let the per-race ARMA
pick. It must be a re-RUN of the whole conversion, not a re-fit of the finished
mesh: a hood is multi-bone SKINNED geometry (Bip01 Head + Neck + Clavicles), so
its head fit happens inside the retarget wrap, and there is no later point where
the head verts can be displaced again without redoing the skin solve.

Ordering inside the retarget is fixed: bones are renamed to Skyrim names only
AFTER the skin transforms are correct, and the body skin is collected after that
again, once the vertex positions are in Skyrim skeleton space — the section
bounding boxes must localise the armour hole in post-retarget coordinates,
including arm openings that shift ~20 z units.

## One bake per distinct input, not one per output name
<a id="hair-bake-sharing"></a>

**Code:** `_plan_jobs`, `_record_variants`, `_emit_variant`, `morph_applies` in
`asset_convert/character/hair_pipeline.py`

The head fit is ~90% of a hair variant's cost: one bake of a 1.6k-vert style
solves 2,480,807 closest points, 1,214,100 of them in the triangle pass alone
(`head_fit._fit_core`, which runs its full `TRI_PASS_ITERS` budget and does not
converge early). One HAIR record expands to bucket x gender x race group, so
Fallout NV asked for 1,164 variants from 61 source meshes — 19 bakes per mesh.

A bake depends only on `(mesh, weight, gender, race, group)`, and on `weight`
only when the sibling .tri's HairMorph can actually reach the geometry. The
bake pairs morph to geometry by VERTEX COUNT, so a .tri matching no shape — or
absent — leaves every length bucket baking byte-identical output. All 61 NV
hair meshes ship no .tri, so its nine buckets collapse to one bake; Oblivion's
morphs apply on 54 of 57 meshes, so its buckets stay distinct and hair length
is never flattened. Measured: NV 1,164 variants -> 183 bakes, Nehrim 1,559 ->
1,346, Oblivion 721 -> 659.

Names after the first are COPIES of the produced pair. `convert_nif` and
`_retype_hair_shader` read only the baked BYTES — the output path never feeds
back into their result (verified: identical digests under two different output
names) — so a copy is byte-identical to re-running them. A name REPEATED in
`out_stems` is one file on disk (several records can ask for the same variant,
90 of NV's 1,164) but still counts once each, so the reported totals are the
variants the plugin asked for, not the bakes.

Jobs are ordered LONGEST FIRST and dispatched at `chunksize=1`: bake cost spans
0.6-6.6s, and in plan order a slow mesh could start with nothing left to
overlap it, which measured 2.6x off ideal across the pool.

Measured on FalloutNV, 29 workers: **251.1s -> 46.4s (5.41x)**, output
byte-identical (2,148 files, same SHA-256).

## Prn attachment: shields, torches and weapons
<a id="shield-attachment"></a>

**Code:** `_convert_prn` in `asset_convert/character/equipment_rig.py`

The authored `Prn` string names the skeleton node a mesh hangs off. It is
remapped to the Skyrim node and re-written onto the new root; gear that the
engine has to resolve an equipped model for also gains a `BSInvMarker`.

**A shield needs a real attach transform.** Oblivion straps it to
`Bip01 L ForearmTwist` with an identity root transform, while Skyrim glues the
NIF root to the `SHIELD` bone at the hand grip. `shield_attach_transform()` maps
between the two attach frames through anatomically corresponding hand frames of
both skeletons, so the shield sits on the forearm at the handle exactly as it
did in Oblivion — no per-mesh bbox heuristics. The wrapper pass then detects the
non-identity rotation and bakes it into an inner NiNode.

### <a id="shield-forearm-clearance"></a>The forearm-clearance correction

**Code:** `shield_attach_transform` in `asset_convert/character/equipment_rig.py`

The base mapping is built from the same three landmarks on each skeleton — hand
joint, middle-finger base, thumb base — giving an anatomical hand frame per
game:

```
T = W_obForearmTwist @ F_ob^-1 @ F_sk @ W_SHIELD^-1
```

(row-vector convention, matching `skeleton_bones_*.json`.)

That preserves the shield's pose relative to the **Oblivion** forearm, which is
not where the Skyrim forearm is: the two leave the hand at different angles,
measured at **~16°** out of the strap plane, with the elbow at SHIELD-local
**z = +7.2** against a shield back face at **z ≈ +2**. The arm pokes through the
shield.

The fix rotates about the grip (the origin) so the mapped Oblivion forearm axis
lands on the actual Skyrim forearm axis — the shield lies along the real arm and
the hand position is unchanged. The Rodrigues construction is transposed from
the standard column form, because everything here is row-vector.

Validated against vanilla `ironshield.nif`: the result lands on the Skyrim
convention (face in the XY plane, dome toward -Z, grip near the origin) within a
few units.

The whole thing degrades to `None` when the skeleton JSONs are unavailable, and
the shield then keeps its Oblivion orientation rather than getting a guess.

**A TORCH also hangs off the SHIELD node** — Skyrim carries it in the off-hand —
**but it is not a shield and must NOT get that transform.** A torch is authored
at the grip in BOTH games: vanilla `meshes\weapons\torch\torch.nif` is
identity rotation, zero translation, geometry at identity. Applying the shield
transform threw it ~65° off with a -20.5 forearm-strap offset, which in game
read as a torch at a completely wrong orientation. It still needs its own
`BSInvMarker`, because `SHIELD` is in `_EQUIPPED_PRN_VALUES` and the per-mesh
inventory pass skips it — vanilla's values are rot (4712, 0, 0), zoom 0.82: a
shield's orientation, pulled back slightly.

### <a id="prn-remap-table"></a>The Prn remap table is authored, not derived

**Code:** `_PRN_REMAP`, `_WEAPON_FILENAME_PRN`, `_remap_prn` in
`asset_convert/character/equipment_rig.py`

Oblivion node names do not map onto Skyrim's by any rule, so the table is data:

| Oblivion `Prn` | Skyrim node | Why |
|---|---|---|
| `BackWeapon` | `WeaponBack` | 2H weapons; bows refine to `WeaponBow` |
| `SideWeapon` | `WeaponSword` | all Oblivion 1H; refined by filename |
| `Quiver` | `QUIVER` | case differs |
| `Shield` | `SHIELD` | case differs |
| `Bip01 L ForearmTwist` | `SHIELD` | Oblivion uses the forearm bone |
| `Bip01 Head` | `NPC Head [Head]` | helmets |
| `Torch` | `SHIELD` | Skyrim carries the torch off-hand |

**The 1H refinement is by filename keyword** because Oblivion has one node for
every 1H weapon while Skyrim has one per type: `dagger`→`WeaponDagger`,
`mace`/`club`/`hammer`→`WeaponMace`, `waraxe`/`axe`→`WeaponAxe`,
`staff`→`WeaponStaff`, everything else staying on `WeaponSword`.

**Shortswords are deliberately absent from that list.** The record converter
maps TES4 `Blade1H` to Skyrim `OneHandSword`, and the draw animation only finds
the weapon at the node matching the record's `AnimationType` — so
`Prn=WeaponDagger` on a Sword-type record renders the weapon INVISIBLE while
held. The same keyword refinement therefore runs on both sides, keyed on the
same model basename so the two can never diverge.

### <a id="weapon-attachment"></a>Side-carried weapons flip 180° about Y

Skyrim's `WeaponAxe` attachment node has a different local orientation from
Oblivion's `SideWeapon`, so the blade appears on the wrong side. A 180° rotation
around Y — the handle-blade axis — corrects it without flipping the weapon
upside-down, which a 180° Z rotation would do. The wrapper pass bakes the
resulting non-identity rotation into an inner NiNode so Skyrim applies it to
static geometry.

**Bows are excluded.** Oblivion bows already match the Skyrim `WeaponBow` frame,
with the string side at -X: the Oblivion steel bow's string is at x = -15.7 and
vanilla `steelbow`'s string bones at x = -13.7. Flipping them held the bow
backwards, with the curve facing the archer.

## Weight-slider variants
<a id="weight-slider-variants"></a>

**Code:** `_write_weight_variants` in `asset_convert/nif/nif_converter.py`

Biped wearables get vanilla-style `_0` / `_1` variants: the ARMA records for
body, hands and feet gear reference `<name>_1.nif` with the weight slider
enabled, and the engine lerps the pair per-vertex.

**That lerp REQUIRES identical topology, so the `_1` file is never a second
independent conversion.** Converting twice makes the body splice clip
differently and the pair explodes at intermediate slider values. `_1` is the
finished weight-0 mesh post-morphed by the fitted `_0`→`_1` Skyrim body morph
(`body_wrap.morph_converted_to_weight1`), with rigid PRN blocks untouched. When
there is no morph to apply — a PRN-only piece — the `_1` file is an identical
copy, so the ARMA's path always resolves.

**Which variants exist is the plugin's call, not the path's.** Gear without the
slider (helmets, shields, rings) is referenced as the plain mesh and gains
nothing from a pair, while slider gear never uses the plain mesh unless it also
serves as a ground model. `wearable_plan` derives this from the same records the
importer writes, so only referenced files are emitted, and `variants_for`
returns BASE for anything no ARMO/CLOT record names — which keeps
non-wearables on their plain conversion while gear filed outside `meshes\armor`
still gets the pair its ARMA asks for.

A beast-race head variant is a copy of ONE mesh, never a weight pair: head gear
has the slider off, and `_0`/`_1` would collide with the race suffix.

### <a id="prn-bone-fallback"></a>A worn piece with no Prn and no skin

`BODY_PART_FALLBACK_PRN_BONE` maps a Skyrim body part onto the Oblivion bone
that piece rigidly attaches to. It is used ONLY when a worn NIF carries no `Prn`
extra data AND no skin at all: those meshes fell straight out of `add_prn_skin`
and shipped with no skin instance, so they never left Oblivion object space —
Morroblivion's `cryohelm.nif` rendered at z −5..27 instead of ~115..133, i.e. on
the floor.

The fallback is keyed off the wearing record's BMDT biped flags — the plugin's
own statement of what the item is — never off the filename.

## Splicing body geometry into a worn piece
<a id="body-splice-fill-partition"></a>

**Code:** `_convert_nif` in `asset_convert/nif/nif_converter.py`

The splice runs AFTER the retarget and the bone rename, so the bone `NiNode`s in
the armor NIF already carry Skyrim names to match against.

It is always the **_0 fill**: the _1 variant is generated afterwards by
post-morphing the finished mesh, which is what keeps the pair
topology-identical (see [weight-slider variants](#weight-slider-variants)).

**The fill partition takes the piece's primary biped slot.** An ARMA only renders
partitions for the slots it claims, so a slot-49 pants ARMA culls a partition-32
fill and leaves invisible skin holes. The slot is resolved by the same rule as
the offset: a single-slot record states it, a multi-slot one is resolved from
where the skinned vertex mass sits.

## Rigid Prn skinning
<a id="rigid-prn-skinning"></a>

**Code:** `add_prn_skin` in `asset_convert/character/prn_skin.py`

Oblivion attaches some armor pieces — helmets above all — rigidly to a bone
through a `Prn` NiStringExtraData on the root, instead of skeleton skinning.
Skyrim requires all worn-armor geometry to carry a `BSDismemberSkinInstance`,
so the piece is given a one-bone skin: a NiNode placeholder for the target bone
(matched by NAME against the skeleton at load) with every vertex at weight 1.0.

**The per-bone bind transform is IDENTITY, and that is correct here.** The
caller runs `_bake_node_transforms_into_verts` first, which leaves the verts in
bone-LOCAL space — verified on the converted dog, whose Head centroid is
(2.8, 14.5, 0), a bone-local coordinate rather than the (0, 42, 57) bind-world
of `Bip01 Head`. So `vert · I · boneWorld` places the part correctly and it
tracks the bone under both animation and ragdoll.

**The per-bone bounding sphere must be real.** The engine visibility-culls
skinned geometry by these spheres, moving each one by its live bone every
frame, so a zero-radius sphere is never visible in game — even though NifSkope
ignores the field and renders the mesh fine. With an identity bind the sphere
is just the vertex bounds in mesh space.

The bone name picks the biped slot; note that vanilla Skyrim puts **helmets on
the HAIR slot (131)**, which is why a head or neck bone maps there rather than
to a head slot.

## Morrowind armor assembly
<a id="morrowind-armor-assembly"></a>

**Code:** `assemble_armor` in `asset_convert/character/morrowind_armor.py`,
fed by `tes4_export/morrowind_armor.py`.

A Morrowind wearable is several BODY meshes hung on named attach nodes of
`base_anim.nif` (OpenMW `npcanimation.cpp` `sPartList`: slot 4 Groin, 19
Right Knee, ...), where Skyrim wants one skinned mesh per ARMA. The export
lists the parts on the record (`MorrowindPart[i]`), and the mesh stage builds
the worn NIF the record names BEFORE the batch conversion, into the source
tree at 4.0.0.2, shaped like the Oblivion armor the worn path already
converts. Parts and both skeletons (`base_anim.nif`, `base_anim_female.nif`)
resolve through the plugin's tree, its masters', then the Morrowind install
([vanilla assets](tes4_export_morrowind.md#vanilla-assets)); a loose file in
that install beats the archive, as in the engine, and the loose replacers in
a modded install differ from the archive copies (root layout, skin skeleton
root) -- measure on what the assembler actually read.

The attach rules are OpenMW's `SceneUtil::attach`, reproduced per part:

- **Rigid part** (every boot, greave, helm and knee piece): the whole file
  hangs under the attach node; a `BoneOffset` node's translation offsets it,
  and an attach node whose name holds `Left` mirrors it in X (Morrowind ships
  ONE mesh for both sides), so the winding is reversed too. The vertices are
  then stored as `add_prn_skin` leaves an Oblivion helmet: upright offsets
  from the bone pivot (`v_world - pivot`), turned by the rotation that takes
  the bone's rest direction (pivot to first bone child) onto the Skyrim
  bone's, because Morrowind rests with the arms down where Skyrim's A-pose
  does not. The pelvis (the bone directly under `Bip01`) is never turned:
  it stands upright in both rests, but Skyrim's `NPC Spine` sits 5.24 behind
  and 3.79 above `NPC Pelvis` where Morrowind's is 7.61 straight above, so
  the pivot-to-child rule tilted every groin piece 54 degrees backwards and
  opened the pants' waist toward the camera. The turned offsets are then
  stretched along the bone (`RestSkeleton.stretch`): between pivot and
  child joint by Skyrim's segment length over Morrowind's, past the child
  joint keeping their offset from it. Morrowind's calf is 37.36 to the ankle
  against Skyrim's 27.95, so an unstretched ankle piece hung about 9 units
  below the Skyrim ankle -- boots sank below the ground, the foot left
  floating above them (thigh x1.10, upper arm x1.27, forearm x0.95).
  The first attempt stored bone-LOCAL vertices under the same
  identity bind, and every piece floated: `_bake_shape_into_bone_frame`
  composes the stored verts with the bone's TRANSLATION only. The one-bone
  skin is a plain `NiSkinInstance` on a flat bone node under the root; a
  `BSDismemberSkinInstance` with the head's slot 131 is a FO3 gore cap to
  `hide_dismember_caps`, which runs before the worn path, and hid every helm.
- **Skinned part** (cuirasses, shirts, robes): a part FILE holding any
  skinned shape is a rig (OpenMW loads it as a `SceneUtil::Skeleton`,
  `nifloader.cpp` `getUseSkinning`), and `CopyRigVisitor` copies ONLY its
  skinned shapes whose name starts with the attach node's name,
  case-insensitively and past a `Tri ` prefix -- its UNSKINNED shapes are
  dropped (`attach.cpp`: `if (!isRig) return;`). Every vanilla `B_N_*` body
  part is the whole skinned body plus four 3-vertex unskinned stubs (`Tri
  Left Ankle/Foot/Knee/Upper Leg`), and the `Tri Right` leg and foot shapes
  hold BOTH legs; judging per shape instead wore those stubs as rigid Prn
  pieces. The part carries its own bind skeleton (T-posed, `Bip01` as the
  file root or a child of an unnamed one, and the skin's `skeleton_root` may
  be an arm bone). Bind-pose vertices come from the skinning contract itself
  (`v @ G @ S @ B_i @ W_i`, blended), are moved bone by bone onto the
  shared bind skeleton (`inv(part bone) @ bind bone`, identity for vanilla
  parts) and bound to the `Bip01` tree in that pose, pruned to the bones
  used; the retarget then runs from Morrowind's own rig
  ([pose cache](#morrowind-pose-cache)). Bone names match
  case-insensitively (`Bip01 R Upperarm` on a shirt).
- **Shield**: geometry in the `Bip01 L Forearm` local frame. Measured on the
  iron shields of both games, Morrowind's forearm frame carries the shield
  exactly as Oblivion's `Bip01 L ForearmTwist` frame does (X along the arm,
  boss at -Y, width on Z), so no roll is applied. A 4.0.0.2 extra data has no
  name, so the assembler cannot write the `Prn`; `name_morrowind_shield`
  (`equipment_rig`) names `Bip01 L ForearmTwist` on a Morrowind-version
  shield before the version upgrade, and `shield_attach_transform` seats it
  like an Oblivion shield (same inner-node transform, -20.5/0.34/8.72).

Verified offline on iron boots, greaves, helmet, cuirass, left gauntlet,
shield and tower shield plus common shirt, pants, shoes, skirt and robe: every
converted rigid shape carries flags 14, its bone node at the Skyrim position
and its record slot; the gauntlet's hand shapes span 7 units at the Skyrim
hand; the cuirass torso sits at z 72-106 like Oblivion's. Not yet confirmed
in game.

## <a id="morrowind-skin-fill"></a>Morrowind skin fill: the part list is the coverage

**Code:** `asset_convert/character/morrowind_coverage.py`,
`asset_convert/character/morrowind_fill.py`, `_arma_bod2` in
`tes5_import/record_types/equipment.py`, `partition_skin_info` in
`asset_convert/character/skin_replacement.py`.

Morrowind draws the actor's own skin part in every body-part slot no equipped
item fills (OpenMW `npcanimation.cpp`: the head model is added only when
`mPartPriorities[PRT_Head] < 1`). Skyrim instead hides a whole body partition
per ARMA slot, and vanilla armor carries the skin left showing inside it. The
Oblivion splice finds that skin by the body-skin shapes an Oblivion armor
embeds; Morrowind parts embed none, so nothing was spliced and a chest-only
cuirass (41 of 280 vanilla ARMO) left the arms invisible.

The record's part list is the authored coverage:

- **ARMA BOD2** = the ARMO's slots plus the partitions its parts overlap
  (`PART_PARTITIONS`), in place of the Oblivion body-coverage extras (which
  hid forearms under every cuirass). Head extras (LongHair, Ears) still apply.
- **The fill** is Skyrim body skin only where Morrowind would draw the
  actor's own skin: inside the partitions the ARMA hides, over the Morrowind
  parts the record does NOT list whose partitions are hidden
  (`SkinFill.shown`). The parts come from the Morrowind reference body fitted
  onto the Skyrim body, whose wrap field stores each reference vertex's INDX
  slot (`mw_slot`, from the shape's attach-node name, side by +X), and the
  fill draws from every section of each skin file the ARMA hides
  (`hidden_skin`): a chest-only cuirass claims 32 alone, but that hides the
  whole torso file, forearm ring 34 included, and a fill drawn from 32 only
  left the lower arm above the wrist invisible in game. The
  fill is cut exactly along their borders ([exact cut](#exact-skin-cut),
  `morrowind_fill.skin_field`). So a chest-only cuirass fills neck, clavicles
  and arms but never its own chest nor the thighs. Skinned armor sunk behind
  the fill at a seam is then seated 0.2 outside it (`morrowind_fill.seat`,
  twins welded).

  Replaced, measured: filling each hidden partition WHOLE and trusting the
  occlusion trim left the Aryon left glove "almost completely hidden by skin"
  (in game) and gave cuirasses leg skin; the chitin cuirass fill fell 1275 ->
  402 verts. Armor sunk behind the fill (>0.1 within 2 units): chitin 12 ->
  0, bonemold 14 -> 0, robe 49 (1.94 deep) -> 4 (0.64). The nearest-vertex
  label that followed kept or dropped whole Skyrim triangles, and still
  left holes and skin poking through in game on many clothes.

  Reverted, in game: splitting the fill into one shape per hidden partition,
  each keeping its own partition, hung every save load with Morrowind armor
  in view (black loading screen, main thread rendering, every worker idle)
  although every mesh passed the NaN, partition and bone-limit checks.
  Cutting against every part the record does not list (hands, feet, knees
  included) added 262 lower-thigh triangles under chest-only robes
  (`common_robe_05`) that clipped through the skirt, and changed none of
  the 44 forearm-ring vertices it was meant to reach.

`PART_PARTITIONS` was measured on `malebody_0.nif`: Skyrim's 34 is only the
ForearmTwist2 ring above the wrist (44 verts), the rest of the forearm, the
upper arm, torso and clavicle are 32; calf-weighted verts are 38 (knee and
ankle parts), foot 37, left hand 33, right hand 59. Groin and upper leg go
to 49, the lower-body section [the body slots](#body-slot-layout) cut from
32. A one-sided piece (gauntlet, bracer, glove, pauldron) takes its own slot
from its armor/clothing type (`MorrowindWearableType`), and a pauldron lists
no coverage, so it hides nothing and carries no fill.

Helmets: an open helm fills only the Hair part (18 of 58 vanilla helmets), so
the exporter drops the Head bit and the ARMA stops hiding the face
([equipment slots](tes4_export_morrowind.md#equipment-slots)).

## <a id="exact-skin-cut"></a>Exact cut along a reference body's part border

**Code:** `asset_convert/character/mesh_cut.py`.

A reference body fitted onto the Skyrim body labels every one of its
vertices with the part it belongs to (Morrowind: the INDX slot, `mw_slot`;
Oblivion: the Skyrim slot of the body file it came from, `ob_slot`). A Skyrim
point's field is its distance to the reference triangles in a label set less
its distance to the rest, so the zero line is the reference body's own part
border. Skyrim triangles straddling it are split at the edge crossings, every
vertex attribute (position, normal, UV, skin weights) interpolated there, so
the kept side ends exactly on the border.

The nearest-vertex test it replaces kept or dropped whole Skyrim triangles:
the kept skin's edge wandered a triangle either side of the part border,
leaving holes on one side and skin poking through armor on the other.

## <a id="body-slot-layout"></a>Skyrim body slots shared by all three games

**Code:** `asset_convert/character/body_slots.py`,
`tools/creature/patch_body_slots.py`.

| Slot | Body section | Skyrim | Oblivion | Morrowind |
|---|---|---|---|---|
| 32 | torso, forearm ring (34) | cuirass, clothes | upper body | cuirass, shirt, robe |
| 49 | pelvis, thighs, calves (38) | slot-32 items, by the patch | lower body | greaves, pants, skirt, robe |
| 33 | left hand | gauntlets | hands | left gauntlet, bracer, glove |
| 59 | right hand | gauntlets, by the patch | hands | right gauntlet, bracer, glove |
| 57 / 58 | none | -- | -- | left / right pauldron |

49, 57, 58 and 59 are the CK wiki's recommended mod nodes (pelvis primary,
shoulder, left arm, right arm); 44 is face/mouth there, so the lower body left
it. Pauldrons rest on the shoulders and hide nothing.

An equipped item that claims a skin addon's first (lowest) slot drops that
addon whole; any other slot it claims hides only that partition of the file
(`morrowind_coverage.hidden_skin`). Vanilla gloves (33 + 34) and boots
(37 + 38) leave `NakedTorso` (32/34/35/36, its file holding 38 too) drawing
bar the forearm ring or calves, while a Morrowind chest-only cuirass (32
alone) took the forearm ring with the torso in game. So each independently
hidden section is its own file and addon: torso (32 + 34) and legs (49 + 38)
from the body, left (33) and right (59) from the hands, first person
included, and the calves (38) are a third body file and addon. A partition
draws only where its own addon claims the slot (vanilla `NakedTorso` claims
38 for the calves its file holds): a Legs addon on 49 alone left naked calves
invisible, and one on 38 + 49 made 38 its first slot, so boots dropped the
whole leg skin and greaves carried calf skin over boots. The
earlier patch cloned NakedTorso into Thighs/Calves addons that still pointed
at the unsplit vanilla body, whose thighs are partition 32, so pants hid the
Thighs addon while the torso addon went on drawing the legs.

An addon draws only when its slots share a bit with its ARMO's own slots, so
the patch also gives each skin ARMO 49 / 59 (not 38: `SkinNaked` itself lists
only 30/32/33/37 while its addons carry 34/38). Measured on Skyrim.esm: of
4298 ARMO -> ARMA links, 23 share no bit, and every one is an addon for a
race the ARMO is never worn by (`NakedDogAA` on horse skins,
`NakedTailArgonian` on `SkinNaked`). Without the skin bits the split Legs and
RightHand addons -- and the old Thighs/Calves clones -- never drew: naked
actors showed no legs and no right hand.

In first person a slot also has to be in the RACE's own BOD2: the addon
loop at 1.6.1170 RVA 0x213430 (reached from `TESObjectARMA::
InitWornArmorAddon`, 0x2775d0) tests `race + 0x60` (its
`BGSBipedObjectForm`) per slot for the first-person biped only. Every
playable race lists 32/33/34/36/39/61, so slot 59 -- skin, gauntlet or
glove -- never drew in first person. The patch gives every race listing 33
slot 59 too, and nothing else: an addon with no first-person model falls
back to its third-person one, so adding 49 would put greaves in the
first-person view. A race override is only as late as the patch: a mod
overriding the races later in the load order takes 59 away again, so the
patch is built over the whole load order and loads last. Every FormID a
race holds is translated into the patch's master list (xEdit's RACE
definition: FormID lists, the ATKD attack spell at offset 8, alternate-
texture lists). Records wait in their own plugin's master space until the
final master list is known: a real load order merges more than 255 masters
before the unreferenced ones are dropped, and renumbering early overflowed
the 8-bit index (`struct.error` in `pack_record`).

The waist is cut along the border between Oblivion's `upperbody` and
`lowerbody` fitted onto the Skyrim body; the hands split by the side of the
bones each triangle is weighted to.

## <a id="morrowind-hand-weights"></a>Morrowind hands and forearms take the Skyrim skin's weights

**Code:** `asset_convert/character/morrowind_weights.py`.

Morrowind's hand has three finger chains (`Finger0/01`, `Finger1/11`,
`Finger2/21`) rooted inside the palm, and its forearm has no twist bones.
Renamed onto Skyrim's bones, a converted glove disagreed with the Skyrim hand
skin under it (nearest vertex, `0commonugloveuleftu01_1` vs `malehands_1`):
46 palm vertices on `Finger20` where Skyrim has `Hand`, finger vertices on
`Finger21` over Skyrim's index and ring fingers, and the iron gauntlet's cuff
(74 vertices) on `Forearm` where Skyrim twists `ForearmTwist2`. In game the
fingers and lower arm were mangled, worst in first person, where the hand
animates most. Each vertex's share on a hand, finger or forearm bone now
takes the weights at its closest point on the Skyrim body and hand skin
(barycentric over that triangle's corners); bones the piece lacks are added
at their Skyrim rest frame. A first cut blended the 4 nearest skin vertices,
which mixed neighbouring fingers and left the gauntlet fingers still
mangled in game. It runs after the bones are placed and before the bind
data is rebuilt.

## <a id="morrowind-pose-cache"></a>Morrowind rest skeleton and pose cache

**Code:** `tools/generators/kf_morrowind.py`,
`asset_convert/havok/extract_skeleton_bones.py`.

Morrowind's skeleton is not Oblivion's rescaled: `base_anim.nif` has 33 bones
(no twist bones) with the same `Bip01` names, but its segments differ per
bone -- Calf->Foot 37.36 vs 27.33, Spine1->Spine2 11.47 vs 8.93,
Spine2->Neck 10.43 vs 13.92, UpperArm->Forearm 18.01 vs 21.85. Carrying a
skinned part through Oblivion's rest pose rescales every weight boundary by
those ratios, so Morrowind gets its own `skeleton_bones_morrowind[_female].json`
and `best_animation_pose_morrowind[_female].json`.

`xbase_anim.kf` is one 348.4 s clip of every animation group, a 4.0.0.2
`NiSequenceStreamHelper`: a NiStringExtraData chain names, in order, the bone
of each NiKeyframeController, and each bone is keyed at its own times (linear
quaternion keys, median spacing 1/15 s). The pose search compares
whole-body frames, so every bone is resampled onto one 1/15 s grid. The female
rig is `base_anim_female.nif` playing `xbase_anim_female.kf` over
`xbase_anim.kf`, as OpenMW loads it.

**The skinned rest is the BIND skeleton, not base_anim's.** Every vanilla
skinned part -- the `B_N_*_Skins` body of each race and armor such as
`A_M_Chitin_skinned` -- is bound to one T-posed skeleton (root transform
included, the imperial body, nord body and chitin cuirass agree to 0.01:
left hand at x -46.55 z 110.28), while base_anim's animation rest hangs the
arms down (left hand x -15.12 z 75.70). `skeleton_bones_morrowind[_female].json`
is that bind skeleton, read from the reference race's chest part
(`morrowind_body.bind_skeleton`); the assembler binds skinned parts to it (a
no-op for vanilla), rigid parts still hang from base_anim's attach nodes, and
the pose generator animates base_anim's hierarchy but measures its deltas
from the bind pose (`kf_morrowind.pose_to_bind`).

Measured against the SAME bind-pose source (`armor_fit_metrics --morrowind`;
the old pipeline's own "source" was already re-posed into Oblivion's rest,
which hid that step's distortion), old Oblivion-rig pipeline -> this one, main
torso shape: new clipping chitin 43.9% -> 16.3%, bonemold 21.6% -> 7.1%,
common shirt 39.6% -> 11.0%; worst edge stretch 292% -> 165%, 290% -> 104%,
347% -> 135%; max displacement ~12.6 -> ~8. Edges stretched >15% rise
(chitin 45% -> 53%) because the armor now FOLLOWS the body onto Skyrim's
shape: the Morrowind reference body itself stretches 65.8% of its edges >15%
to become the Skyrim body (Oblivion's: 30.2%). Re-posing onto base_anim's
arms-down rest instead of the bind skeleton measured no better on edges and
is a needless ~60 degree round trip through linear blend.

The converter hands `was_morrowind` -- the 4.0.0.2 version read before the
upgrade -- to the retarget, which then takes the Morrowind skeleton, pose
cache and wrap field. The bone names are Oblivion's, so the file version is
the authored marker, not the names (unlike FO3/FNV).

## <a id="morrowind-wrap-field"></a>Morrowind wrap field

**Code:** `asset_convert/character/body_wrap_build.py`,
`asset_convert/character/morrowind_body.py`.

The wrap keeps each armor vertex's AUTHORED clearance from the body it was
modelled around; for a Morrowind piece that is the Morrowind body, not
Oblivion's. Morrowind has no body mesh: an actor wears its race's skin BODY
parts on the skeleton, chosen as OpenMW `NpcAnimation::getBodyParts` does
(skin type, playable, third person, the actor's gender first with male parts
as the female fallback, each part hung on both sides). The reference body is
those parts assembled like armor, rigid parts bound to their real bones at
the rest pose, and `body_wrap_build` fits it exactly as it fits Oblivion's
(`body_wrap_morrowind_{gender}.npz`, no head group). Two choices keep the
runtime unchanged: segment rescaling measures Morrowind's own bone lengths,
and the per-vertex bone centroids are taken in Oblivion skeleton space by bone
name, the space `deform_geoms_wrap` computes the armor's in.

## Morrowind weapons
<a id="morrowind-weapons"></a>

**Code:** `build_weapon_prns` (`wearable_plan.py`), `convert_prn`
(`equipment_rig.py`).

Measured on the iron longsword, iron dagger and long bow: Morrowind authors
weapons in Oblivion's frame (grip at the origin, blade along +Y, a bow's
string at -X), so a Morrowind weapon converts exactly like an Oblivion one
once it carries the `Prn` a 4.0.0.2 NIF cannot. The WEAP record's WPDT type is
the authored answer (`MorrowindWeaponType` in the export): short blades and
thrown weapons hang on `WeaponDagger`, long blades on `WeaponSword`, one-hand
blunt on `WeaponMace`, one-hand axes on `WeaponAxe`, and every two-hander and
bow on Oblivion's `BackWeapon`, so the existing remap, axe flip, inventory
marker and bow bend rig apply unchanged; a staff keeps `WeaponStaff` by
name, as Oblivion's own refinement does. The wearable plan carries the Prn
per mesh and `convert_prn` uses it only when the source root names none.

## Contents

- [NIF worn armor conversion](#nif-worn-armor-conversion)
- [Closing the last ~10% cuirass-edge gap](#cuirass-edge-gap-ideas)
- [Body-wrap armor fitting (2026-07-10/11, asset_convert/character/body_wrap.py)](#body-wrap-armor-fitting)
- [NIF weapon Prn (attach node) contract](#nif-weapon-prn-contract)
- [NIF torch Prn — Skyrim carries the torch on the SHIELD node (SOLVED 2026-08-01)](#nif-torch-prn-skyrim-carries)
- [NIF shield conversion](#nif-shield-conversion)
- [NIF armor ground model (_gnd) conversion](#nif-armor-ground-model-conversion)
- [BSInvMarker inventory orientation (learned 2026-07-18)](#bsinvmarker-inventory-orientation)
- [NIF skin retargeting (Oblivion → Skyrim skeleton)](#nif-skin-retargeting)
- [Distorted worn clothing — nested bones placed with the wrong operand order (SOLVED 2026-08-25)](#distorted-worn-clothing-nested-bones)
- [Creature skin render crash — >80 skin bones per shape (SOLVED 2026-07-10)](#creature-skin-render-crash-80)

## <a id="morrowind-armor-assembly"></a>Morrowind armor assembly

**Code:** `asset_convert/character/morrowind_armor.py`.

Morrowind has no single worn-armor mesh. A cuirass is a SET of body-part NIFs
(chest, per-side pauldron, upper arm...) named by the ARMO record's index
list, so the converter assembles one wearable from several files before any of
the Oblivion armor path applies.

Each assembled part is classified by how it is bound, not by its name: a part
carrying `Prn` is a rigid attachment and takes the Prn path
([#prn-attached-rigid-pieces](#prn-attached-rigid-pieces)); a skinned part is
re-bound to the shared skeleton ([#nif-skin-retargeting](#nif-skin-retargeting)).
Shields are never skinned -- the root carries `Prn=Shield` and the Oblivion
shield path takes it ([#shield-attachment](#shield-attachment)).

The result is written as a Morrowind-version NIF beside the source meshes, so
every downstream stage sees the same shape of input it always has.

## NIF worn armor conversion
<a id="nif-worn-armor-conversion"></a>
- Worn armor (has_skin AND not _gnd AND in armor/clothes dir) must use **NiNode** root, NOT BSFadeNode
- BSFadeNode is for world objects only — worn armor is attached to the character skeleton
- BSDismemberSkinInstance is required for Skyrim biped slot assignment (upgrade from NiSkinInstance)
- Ground models (_gnd) with cloth-physics bones must have skin stripped (bones don't exist in Skyrim skeleton)
- **Material CRC (unknown_int_2)**: ALL vanilla Skyrim NiTriShapeData has `unknown_int_2=0`. This field is the Material CRC in Skyrim BSStream 83. Setting it to 8 (confused with the tangent flags) causes rendering issues. Always set to 0.
- **PRN rigid armor (helmets etc.)**: Oblivion attaches via `Prn` NiStringExtraData on root. Converted to BSDismemberSkinInstance with single bone at weight 1.0. Vanilla Skyrim structure has bone NiNode as FIRST child of root (before geometry blocks). bodyPart=131 (SBP_131_HAIR) is correct for helmets (they replace hair).
- **PRN piece verts are in an upright bone-pivot frame** (same convention as vanilla Skyrim head-local helmet verts), NOT in the rotated Bip01 bone frame — no rotation correction needed; the retarget places them exactly at the SK bone. Therefore the FK-tuned `ARMOR_PIECE_OFFSETS` (helmet dz=+7, tuned on genuinely-skinned helms like TownguardCho) must NOT apply to them — that floated iron/legion helms on top of the head. PRN blocks are collected via `retarget_skin_to_skyrim(prn_out=...)` and get `ARMOR_PIECE_OFFSETS_PRN` instead (helmet dz=-2.1: OB head pivot sits deeper in the skull — OB headhuman.nif top = pivot+13.6 vs SK malehead.nif +11.5).
- **Body part assignment (BSDismemberSkinInstance)**: Oblivion cuirass NIFs have geometry named 'Arms' and 'UpperBody'. The 'arm' keyword in ARMOR_GEOMETRY_BODY_PARTS maps to SBP_32_BODY (not SBP_34_FOREARMS) because gauntlet NIFs use 'Hand' geometry names — 'Arms' only appears in cuirass/shirt meshes. This prevents cuirass arm geometry from being hidden when gauntlets are equipped.
- **Clothing vs armor ARMA body coverage**: Clothing ARMA should NOT add ForeArms(34) extra coverage — shirt sleeves (SBP_32_BODY) should remain visible when gloves are equipped. Armor cuirasses DO add ForeArms(34) because the separate ARMA system allows gauntlets to properly overlay.
- **Shoes vs boots calves slot**: Shoes (clogs, sandals) should NOT claim Calves(38) in ARMA. Only boots get calves. Detection: `'boot' in model_path`. Clothing foot items without 'boot' are shoes.
- **Oblivion alpha-BLENDS surfaces it also alpha-TESTS; Skyrim must not (fixed 2026-07-27, `_skyrim_alpha_property`)**: Oblivion ships cutout geometry as `NiAlphaProperty` flags **0x12ED** (blend bit 0 SET + test bit 9 set). Skyrim reads the blend bit as "draw in the transparent pass", so an opaque diffuse authored that way renders wrong — this is why the **Shivering Isles Dark Seducer body armor was invisible when worn while its ground model was fine** (SI armor is one all-in-one ARMO covering slots 32/33/37/44; its `armor.dds` is DXT3 but **99.5% fully opaque**, so it should be a plain cutout). Vanilla uses **0x12EC** — the identical value with blending CLEAR — on **188/193** surveyed shapes that enable alpha testing (`references/Skyrim Meshes` armor + landscape + architecture + clutter). So whenever the test bit is on, the blend bit is dropped. This is a GENERAL TES4→TES5 rule, not an SI quirk; `grass_profile.py` had already learned the same thing for grass (`0x12ED`→blend clear) and this generalises it to every converted mesh.
- **Blend-on/test-OFF is real transparency EXCEPT under `APPLY_HILIGHT2` (fixed 2026-07-27)**: see-through SI mania/dementia rocks ship `0x00ED` (blend on, test off) — but so do gems, bottles, curtains, posters and potion liquids, which must keep blending, and a flawed emerald has *byte-identical* alpha flags to a rock overlay. The discriminator is **`NiTexturingProperty.apply_mode`**: the rocks use **APPLY_HILIGHT2 (4)**, which is Oblivion's **parallax** switch — that alpha is a HEIGHT FIELD, not transparency and not a blend weight (see the parallax section; the mid-tone-dominant profile the census measured is exactly a height map's) (SI `DMRockSideRoot01.dds`: 0% opaque / 99% partial). Skyrim has no equivalent mode, so it blends the mask across the whole surface → you see through the rock. Census over ~1,000 source meshes (rocks, clutter, architecture, dungeons, plants): **all 5** blend-on HILIGHT2 shapes are the SI rock overlays; **none** of the other 142 blend-on shapes use HILIGHT2 (they are MODULATE=2 or HILIGHT=3). Everything else is left exactly as authored. Do NOT try to classify these by texture alpha percentages — potion liquids measure 100% partial alpha and would be wrongly turned opaque.
  - **The remedy is to DROP the NiAlphaProperty (`hilight2_alpha_dropped`), not to reinterpret it.** Two earlier attempts are recorded because neither is in the code and both are worth not repeating: the first version of this fix turned HILIGHT2+blend+no-test into a threshold-128 cutout, which replaced see-through rock with **completely invisible sections** — these overlay masks are soft gradients with NO fully-opaque texels at all, so a cutout deletes a quarter to a half of the surface outright (`DMRockSideRoot01` peaks at alpha **221** with 29% of texels below 128; `DMRockSideMudBase01` peaks at 238, 26% below; `mrock01worn` 47% below). The second attempt was `slsf_1_decal` + `slsf_1_dynamic_decal` with blending KEPT. 🛑 **Neither shipped** — grep for `slsf_1_decal` or `0x10ED` in `nif_converter.py` and you will find nothing; `process_geometry` drops a blend-enabled NiAlphaProperty under HILIGHT2 outright and the rock renders solid. Vanilla census (400 random meshes, all block types): blend-on/test-off is a perfectly legal Skyrim mode (370 shapes), and **142/206 blend-on shapes carrying the decal pair all ship alpha flags exactly `0x10ED`** — Oblivion's own `0x00ED` plus the no-sort bit `0x1000`. Vanilla agrees with the drop: across 600 landscape/clutter meshes, 1088/1313 shapes ship no NiAlphaProperty at all and the commonest value on the rest is `0x12EC` (test, blend OFF). Vanilla rock does not alpha-blend.
  - **The same overlay breaks OBJECT LOD even with NO alpha property at all (fixed 2026-08-20)**: the two fixes above both hang off `alpha_prop is not None`, so a HILIGHT2 shape that ships no `NiAlphaProperty` was untouched — correct up close (nothing samples the channel) but see-through at LOD range. `RockGreatForest645` is the reference case: `apply_mode=4`, no alpha property, and its diffuses `GreatForestRock03/01.dds` measure alpha mean 101.6/157.2 with only 0.5%/0.0% of texels fully opaque — a blend WEIGHT, not a mask. Cause: LODGen stamps `slsf_2_lod_objects` on every shape it bakes (`num2 = 5U` in `LODApp.cs`), and the LOD object shader reads diffuse alpha as opacity. Confirmed in the artifacts — `TES4Tamriel.4.4.-12.bto` has 13 shapes and **zero** `NiAlphaProperty` blocks, and 154 shapes across 75 sampled VANILLA `.bto` tiles carry zero between them: vanilla object LOD is opaque, always.
    - LODGen cannot be told otherwise — it writes the shader itself, and it only harvests `NiAlphaProperty` in its `fo4`/`merge5` modes (`ShapeDesc.cs:369`), never the `tes5`/`sse` mode we run, so `isAlpha` stays false and the emit path writes `SetBSProperty(1, -1)`.
    - The remedy is a SEPARATE LOD texture: `lod_far_gen.redirect_overlay_diffuses` writes `<name>_lod.dds` and repoints the generated `_far` mesh at it, so the full-size mesh keeps the alpha it still needs as a detail blend weight. Flattening in place destroys that channel; removing the fix brought the transparency bug back in game; shadowing a copy at the SAME path from the LOD mod put 192 paths in two mods and shipped 401.6 MB of mipless uncompressed DDS. See: docs/commentary/asset_convert_shader.md#detail-overlay-diffuses
    - **The discriminator is the AUTHORED `apply_mode`, never the pixels.** A genuine cutout mask ships MODULATE (2), is absent from the manifest, and is left alone. Measuring alpha instead flattens tree billboards and cobwebs into solid rectangles — DXT5 leaves a billboard with ~0% of texels at exactly 255 even though it is unambiguously a mask. (Same trap the bullet above warns about for potion liquids.) Measured: 94 overlay diffuses across the 4 Tamriel contributors; `cobweb01.nif` (MODULATE) correctly reports none.
- **Body skin splice section_bboxes coordinate space**: OB body skin sections are in OB skeleton space; SK body NIF verts are in SK skeleton space. These are DIFFERENT frames. The OB arm area (z≈98–105) is at SK z≈72–92 after retarget. **Always use POST-RETARGET section_bboxes** from `collect_skin_info()` — these are in SK world space and correctly localise both arm openings and neck. Pre-retarget bboxes (source OB verts) only work for neck/collar (small-x geometry that happens to be at the same world z in both skeletons) but MISS the arms (which are displaced ~20 Z units by skeleton frame differences). SK male body max arm reach (|x|>20) sits at z=75–97 world, exactly within the post-retarget 'Arms' bbox z=72–92. Use `bbox_pad=1.0` to stay under 25% of total body verts spliced.

## Body-wrap armor fitting (2026-07-10/11, `asset_convert/character/body_wrap.py`)
<a id="body-wrap-armor-fitting"></a>
- **Architecture: FK base + measured-error correction field.** FK (animation DQS) is locally smooth but lands armor 0.5-2.5 units off the SK body (the in-game clipping). The wrap field measures FK's error EXACTLY by running the actual OB body meshes (upperbody/lowerbody/hand/foot) through the very same FK retarget, then fitting them onto the real Skyrim body NIFs via iterative closest-point projection with topology-aware delta smoothing (never bleeds between the legs) + limb-segment length prescaling. Fits BOTH weight-slider targets (`malebody_0` AND `malebody_1` etc.); cached per gender in `generated/body_wrap_{male,female}.npz` (src/fkp/dst0/dst1/tris/vert_bc/part). Runtime: FK first, then each armor vertex gets `delta = dst[w] - fkp` interpolated from the K=40 nearest body triangles (Gaussian distance + skin-weight bone-centroid gating + wrong-side penalty), then a clearance-enforcement push. Rebuild with `python -m asset_convert.character.body_wrap_build` (uses `allow_wrap=False` internally -- the field must never bootstrap from a previous field).
- **_0/_1 weight variants (2026-07-11)**: `convert_nif` writes `<name>_0.nif`/`<name>_1.nif` for every biped wearable (any non-`_gnd` mesh the wearable plan names — see the folder-vs-plugin note below). **The _1 file is NEVER a second independent conversion** — the engine lerps the pair per-vertex, so the pair must be topology-identical; a reconversion clips the body splice differently and mid-slider values vertex-explode (observed in game). Instead `body_wrap.morph_converted_to_weight1` post-morphs the finished _0 mesh with the fitted `dst1 - dst0` body morph (built from the REFERENCE Skyrim bodies — the modified output bodies have bugs and are never used for weights); spliced fill lies on the _0 surface so it gets the exact body morph, rigid PRN blocks are untouched. tes5_import ARMA enables the weight slider + `<name>_1.nif` path ONLY for gear covering TES4 biped bits 2-5 (upper/lower body, hand, foot) — vanilla helmets (IronHelmetAA) and shields (IronShieldAA) have the slider DISABLED and a plain path, and slider-on shields misbehaved in game.
- **What counts as worn gear is the PLUGIN's call, not the folder's (2026-08-08)**: `_convert_nif` used to decide with `'armor' in src_path or 'clothes' in src_path`. That holds for vanilla Oblivion, which files every wearable under `meshes\armor` or `meshes\clothes`, but it is a guess about a naming convention. Nehrim files 88 worn meshes under its own folders (`eyren/`, `spinat/`, `nehrim/`, `skeletonk/`, `dwemertechnology/`, `ttbeards/`, `mr_siika/`, `suedland_set/`) and every one of them was converted as a **world object**: BSFadeNode root instead of NiNode, plain NiSkinInstance instead of BSDismemberSkinInstance, no retarget onto the Skyrim skeleton — and, because the same substring gated the variant writer, no `_0`/`_1` pair, so 52 of the 61 unresolvable ARMA paths were simply never written and the engine drew nothing (guards with a head and hands but no torso). The authored answer is the plugin's own biped model references: `wearable_plan` now sets a `WORN` bit on every path an ARMO/CLOT names as a biped model, and `wearable_plan.is_worn` answers the question. The folder test survives only as the fallback for meshes no record references. Verified byte-identical output for `armor/` and `clothes/` controls (mesh conversion is **not reproducible across processes** unless `PYTHONHASHSEED` is fixed — set/dict iteration order leaks into the written bytes, so any A/B of NIF output must pin it). The remaining 9 misses are dead references: those meshes exist in no Nehrim BSA and no loose file, i.e. they were broken in the original game too.
- **🔴 Body-skin identity comes from the BONES, not the texture name (2026-08-09)**: Oblivion bakes the wearer's skin into a wearable; the converter strips it and splices Skyrim body geometry back, choosing which body NIF by a keyword in the texture path (`_SKIN_TEX_TO_BODY_NIF`). That is the author's *label*, not what the geometry *is*. Nehrim ships 18 wearables whose torso skin carries a foot or hand texture — the Silverlight cuirass (`Foot:Body`, 3321 verts, weighted to Spine/Spine1/Spine2/Clavicle/Neck/Pelvis, textured `characters\imperial\female\footfemale.dds`) and the entire female Eyren set (four battledresses at 3321 verts plus four greaves). The keyword picked `femalefeet_0.nif`, which contains no torso, so the stripped chest was never spliced back: the armour renders as plates with see-through gaps and the actor looks half-invisible rather than naked. `collect_skin_info` now overrides a hands/feet classification when the skin instance is weighted to **spine, clavicle or neck**. Those three are deliberately the only test — a gauntlet legitimately reaches the forearm and a boot the calf, so including those bones produced 9 false positives on correctly-named vanilla gauntlets; spine/clavicle/neck produced zero. Survey any plugin with `python tools/body_skin_audit.py [plugin] [--all]`. **Diagnostic trap:** the symptom reads as a texture or alpha problem — the source NIF genuinely does have `NiAlphaProperty flags=0x00ed blend=True` on several shapes — so it invites an alpha investigation. Compare the source's shape list against the converted one first; a missing body shape is instantly visible and the alpha is a red herring.
- **Cross-block solve is mandatory**: `deform_geoms_wrap` concatenates ALL non-PRN blocks into ONE weld/correction/diffusion system. Per-block solving gave coincident seam verts across blocks (cuirass/pauldron boundary) different corrections — visible seam splits. `weld_groups` is true distance welding (KDTree pairs + union-find), not grid rounding (rounding-boundary twins split).
- **Head gear (hair, Prn helmets, AND skinned helmets/hoods) is fitted by `asset_convert/character/head_fit.py`'s scalp displacement field (v3, 2026-08-24, after two in-game round trips)**: rigid head gear's verts are **bone-local (face-space)** while the wrap field lives in world space, so a field query for them lands on nothing. The v1 fit oversized every mesh in game — its affine carrier was measured from a WORLD-frame ICP fit and claimed sx 1.18 / sz 1.24; the x was ICP stretching the earless OB head over the SK EARS, the z conflated bone placement with head size. **Measured in head-LOCAL frames the two human skulls are the SAME width (OB x ±5.59, SK ±5.51 male / ±5.58 female earless) with local scalp deltas of only mean 0.96 / max 2.8** (crown ~2 DOWN, occiput/nape ~2-3.4 further back). Vanilla SK head parts (hair01.nif etc.) are stored head-bone-local, same convention as our output. NEVER fit a carrier in world frames.
  - **The v3 mechanism — one smooth scalp-to-scalp displacement field, sampled per vertex.** At BUILD (`build_arrays`, run by `python -m asset_convert.character.body_wrap_build`): for every OB head vertex, where its matching SK skin point is. Init = NEAREST POINT from the identity carrier; then FIELD_CYCLES of graph smoothing + reprojection ALONG THE OB VERTEX NORMALS (`_project_ray` — nearest-point reprojection exits sideways from inside the SK nape bulge; normal rays reach it and cannot drift laterally). The final step is a projection, so **every field target lies exactly ON the SK skin**. At RUNTIME (`fit_head_gear`/`field_deltas`): each vertex samples the field at its closest scalp point (Gaussian blend that widens with standoff — exact on the skin, smooth far off), so by construction: a vertex ON the skin lands ON the new skin (hairline edges exactly at the skin line), a vertex N units off stays exactly N off (helmets keep authored standoff), and everything over one scalp region moves identically (headbands/eye-coverings never stretch; the only deformation is the real anatomy gradient). Verts >4 units off (ponytails, domes) take their deltas by graph DIFFUSION from the near verts — per-vertex re-sampling at range measured 19% edge stretch on the lengthened style01 tail; diffusion restores 0%. Sweep over all 57 hairs x genders + every PRN helmet (164 meshes): flush err mean 0.05-0.08 / p95 <=0.25, |dlen| p99 <=1.0 human.
  - **Landmark ground truth (measured on the raw local-frame meshes)**: scalps SAME width, but the SK jaw/cheek is 1-1.6 WIDER per side (OB max|x| 3.0-4.5 vs SK 4.6-5.4 band-by-band), the SK nose tip and crown sit 2.1 LOWER, the occiput/nape 2-3.5 further BACK, and the under-occiput hollow and lip profiles differ by ~3 at fixed heights. So converted gear legitimately widens at cheek guards and deepens at the nape — that is flush-to-skin, not oversizing.
  - **The FaceGen UV correspondence is an ICP SEED, never a field init.** Tried and REJECTED for v3: the two layouts' v-coordinates differ by up to 0.042 at the same landmark (back of head OB v 0.506 vs SK 0.464), which read as a systematic ~2-unit downward drag on top of the real anatomy (field dz mean -3.9 vs a measured landmark shift of -2.1) — and vanilla SK helmets sit at the same local heights as OB ones, so the drag is parameterization bias, not anatomy.
  - **The OB head's INTERIOR geometry (mouth bag, inner structures) is excluded from the field domain** (`_visible_exterior`, radial occlusion test): its correspondences form +-3-unit dipoles (bag maps forward onto the lips, inner column backward onto the skull) that tore 4.9-unit edge strain into face-covering masks (darkbrotherhood cowl). Interior verts get their dv in-filled from the exterior field.
  - **EARS ARE IGNORED BY FLATTENING, NOT BY CUTTING.** The OB heads are earless by authoring (ears ship separately) but carry a recessed ear SOCKET; SK heads bake ears in. v2 cut the SK ear triangles — the HOLE's rim attracted every nearby projection ~2 units OUTWARD (iron helm x +2.8, nose guards stretched) and sampling jumped across it. v3 instead projects the SK ear verts onto the carrier-aligned OB socket (`_flatten_ears`): the surface stays continuous, the ear region behaves exactly as the OB socket, gear follows the skull, and SK ears may poke through hair sides exactly as vanilla hair allows.
  - **No orc pack**: Skyrim orcs use the shared human head, the OB orc SCALP is within 0.36 mean of the human one, and a headorc pack measured the worst distortion of any race. Khajiit/argonian keep their packs (SK ships their heads; the SK khajiit skull genuinely is wider, scalp band ±6.74 vs OB ±5.53). All other races (wood elf, high elf...) share malehead/femalehead — censused over all 766 vanilla HDPTs; race size is the skeleton height scale, which scales hair with the head.
  - **Frames**: face space ↔ world via `hf_o_ob` = Bip01 Head world translation and `hf_o_sk` = NPC Head [Head] world translation; Prn gear renders at `verts + o_sk`. Hair is fitted in `hair_pipeline.bake_hair_variant`; helmet Prn blocks in `nif_converter._fit_prn_head_blocks`. Data = `hf_*`/`hfr_*` arrays in the wrap npz, marker `hf_v4` (the loader refuses a stale npz).
  - 🛑 **EVERY in-game head is malehead/femalehead PLUS its RACE's races-tri morph — the base mesh is worn by NO ONE** (measured scalp deltas of `maleheadraces.tri`, names = RACE EditorIDs: elves 2.6, Orc 1.5, all five human races + Dremora <= 0.15 = the base scalp; the elf occiput sits +1.67 FORWARD of base). **A races.tri on the hair HDPT (NAM0=0, the beard mechanism) was shipped and the ENGINE DOES NOT APPLY IT to type-3 (Hair) parts** — in game the hair rendered unmorphed, floating 2.2+ units behind High Elf occiputs while nearly right on Imperials. Vanilla only puts races tris on heads (type 1) and beards (type 4); vanilla hair is per-race-group MESHES gated by RNAM lists, and the conversion now does the same: generic hair bakes THREE meshes — base scalp (bare stem), elves (`__ev`, fitted to base+HighElfRace; HighElf==DarkElf exactly, WoodElf within 1.4 — vanilla shares one hair set across all three too), orc (`__or`) — with FOUR HDPTs per variant (the humans+vampires and Dremora lists share the base mesh; see `HDPT_GROUPS`); race-NAMED hair bakes only its own group under its bare stem. `npc_face_mapper` routes each NPC to its race group's variant; group FormIDs extend the variant key with 'D'/'E'/'O' (existing human ids unchanged — no drift). Gate: `test_generic_hair_is_baked_per_race_group`.
  - **The fit surfaces are NECK-EXTENDED** (round 5): the OB head mesh ends at local z -3.5 on the back of the neck, so hair below it had nothing to conform to and kept the occiput's backward delta all the way down — a visible gap off the nape/neck. `_neck_surfaces` appends the body meshes' neck columns (OB body T-pose + SK malebody/femalebody _0, z>103, |x|<7) to BOTH fit surfaces, so the field, refinement and ear-cover floor all see real skin there. Side effect: helmet widening dropped (iron helm x +2.26 -> +0.77) because lower helmet verts now correspond to the neck instead of the jaw.
  - **Round-4 fit refinements (2026-08-24):** the ear cap projects SK ear verts onto the SK's OWN surrounding skull (an OB-socket cap recessed the region: short hair sat 0.4-0.8 under the skin around the ears); an exact-clearance refinement (capped ±0.5, graph-smoothed) removes the sampler's residual bias at the front hairline/nape (signed bias now −0.01); hair (not helmets) gets an EAR-COVER floor — near-skin verts under the REAL eared skin push out to +0.15 (cap 1.3), so short styles drape over the ear like vanilla (style07 below-skin verts 32→4). Hair specular: converted normal-map alpha masks average 94 vs vanilla ~17, so `specular_strength` is scaled per texture (`_spec_strength_for_normal`) — the flat 0.9 read as plastic shine. `HAIR_ALPHA_THRESHOLD` = 16 (35 still cut visible texels of the blindfold band).
  - 🛑 **Never look converted geometry up in `data.blocks`** — it is STALE after the strips→shape conversion replaces block objects. `_fit_prn_head_blocks` did, matched nothing silently, and every helmet fell back to the legacy `ARMOR_PIECE_OFFSETS_PRN` scale table (sy 1.165 — the in-game "extremely oversized, stretched wide" helmets). Walk `root.tree()` like `apply_armor_offset` does. Guarded by `test_converted_helmet_is_fitted_not_scaled`.
  - 🛑 **BEAST RACES GET THEIR OWN HEAD-GEAR MESH (2026-08-27).** Hair carries its race in the EditorID (`head_fit.fit_race_for_hair`), but a HOOD or HELMET is ONE Oblivion record worn by every race -- there is nothing on the record to route on, so every converted hood/helmet was fitted to the SHARED HUMAN skull and then sat inside a khajiit or argonian head. **Measured head-local:** the khajiit SK head reaches z 14.85 / |x| 8.47 against the human head's 11.51 / 6.85, and over the scalp region beast verts stand mean 1.91 (khajiit) / 1.40 (argonian) proud of the human surface (max 6.87 / 4.29). Signed penetration into the real beast skull, scalp region, blades/m/helmet: **khajiit 342.9 -> 43.0, argonian 466.1 -> 16.4** against a human-on-human baseline of 43.7; robemagearch/hood (skinned): **khajiit 37.1 -> 16.6, argonian 27.2 -> 7.2** against a baseline of 20.6.
    - **The fix mirrors vanilla exactly: a MESH PER RACE FAMILY named by a PER-RACE ARMA.** ARMA has no alternate-model slot -- race targeting is `RNAM` + the `MODL[]` additional races -- so a per-race mesh *requires* a per-race ARMA. Vanilla `ArmorIronHelmet` lists three armatures: `IronHelmetAA` (RNAM=DefaultRace, `Helmet.nif`), `IronHelmetKhajiitAA` (RNAM=KhajiitRace, `HelmetKhajiit.nif`), `IronHelmetArgonianAA` (RNAM=ArgonianRace, `HelmetArgonian.nif`); the same split runs through BoneCrown, Blades, Orcish, Dragonscale, Draugr, Dragonplate, Falmer, ThalmorHood and every Circlet. We write `<name>_khajiit.nif` / `<name>_argonian.nif` (`nif_converter._write_beast_head_variants`) and emit the matching ARMAs (`equipment._build_arma(beast_race=...)`, races in `skyrim_overrides.ARMA_BEAST_RACES`).
    - **The default ARMA must DROP the beast races** (`ARMA_ADDITIONAL_RACES_NONBEAST`) or the engine satisfies a khajiit with the human-fitted armature and never reaches the beast one. Each beast ARMA lists that race's VAMPIRE variant as its additional race (KhajiitRaceVampire 0x88845 / ArgonianRaceVampire 0x8883A), exactly as vanilla does.
    - **Khajiit and Argonian stay SEPARATE, never one shared "beast" mesh** -- the two skulls differ from each other as much as either differs from the human one (khajiit ears sit on TOP of the crown, argonian snout runs to y 15.39 vs khajiit's 13.63).
    - **A beast variant is a full RE-CONVERSION, not a re-fit of the finished mesh.** A hood is multi-bone SKINNED geometry (Bip01 Head + Neck + Clavicles), so its head fit happens inside the retarget wrap (`deform_geoms_wrap`), not in the rigid Prn pass -- there is no later point at which the head verts can be displaced again without redoing the skin solve. `race` therefore threads `convert_nif` -> `_convert_nif` -> `retarget_skin_to_skyrim` -> `deform_geoms_wrap` -> `field_deltas`, AND into `_fit_prn_head_blocks` for the rigid case. Re-reading also keeps each variant a FIRST fit through its race's field rather than a second displacement stacked on the human result.
    - **The gate is the AUTHORED BMDT flags, never the filename**, and the record must claim ONLY head slots (bits 0/1) and NO body slots (bits 2-5). A multi-slot suit (Knight of Order, flags 0x3D) is fitted by where its vertex MASS sits -- the body -- so asset_convert writes no per-race mesh for it; emitting a beast ARMA anyway pointed at a missing file (measured: 14 of 484 beast ARMAs before the gate, all that suit) and a missing mesh renders INVISIBLE, which is worse than a slightly-wrong fit.
    - **No FormID drift**: beast ARMAs are keyed `derive_formid('ARMA', (source_fid, race))` while the human one keeps the bare `source_fid`, so no existing id moves. Measured over Oblivion.esm: 0 main records moved, 0 companions lost, 470 gained across 235 head-gear records. Guarded by `tests/test_beast_head_gear.py`.
  - **Skinned head gear (guard helmets, cloth hoods) takes the SAME field, blended by head weight.** The wrap's correction field is graph-smoothed (DELTA_SMOOTH_PASSES), which smeared the real jaw widening across the whole head: the townguardcho skinned helmet shipped +2.5 units of head-band width and a +31% nose guard ("comically large" in game) even after the wrap's head dst rows were replaced with the exact field. `deform_geoms_wrap` now maps head-weighted verts directly through `head_fit.field_deltas` on their PRE-FK authored positions (per-vertex head-weight-fraction blend; measured after: ear-band +0.07, nose guard +0.02) while clavicle/spine-weighted drape keeps the wrap; field-fitted verts skip the clearance push. The wrap npz's head dst rows are ALSO the field mapping (build_field replaces the ICP head fit), so enforcement measures against the true SK skin. `ARMOR_PIECE_OFFSETS['helmet']` constants apply only when no field with a head exists.
- **`malehead.nif`/`femalehead.nif` are `BSDynamicTriShape`: verts inline, UVs+normals+tangents in the SKIN PARTITION (2026-08-23)**. `sse_nif._geometry_arrays` returned early on `sse_verts is not None` and handed back the inline buffer's `None` for every other attribute, so both heads read with **zero UVs and zero normals** — silently, since nothing checked. It now falls back **per attribute**, not just for triangles (length-checked against the vertex count). Body/hands/feet targets are unaffected (verified: all 7 wrap targets report uvs == verts).
- **When the field exists, `ARMOR_PIECE_OFFSETS` are SKIPPED** (nif_converter checks `body_wrap.wrap_available`) -- the field's far-range constant extrapolation replaces that hand-tuned drift table. PRN offsets (`ARMOR_PIECE_OFFSETS_PRN`) still apply to NON-head Prn pieces (shields); head-attached Prn pieces take the head fit instead (see above), and both constant tables survive only as the fallback when the fit/field data is unavailable.
- **Approaches that FAILED before landing here**: (1) pure surface-relative wrap (offset from body surface, rigid transport through triangle frames) -- preserves clearance perfectly but imprints the fitted map's tangential bunching onto every body-hugging vertex (gauntlets 31% edge failures vs FK's 2%); (2) tangential isometry relaxation of the fitted body -- the scale field is a fixed point of the current state (no-op) and heavy diffusion made fingers hop between surfaces; (3) restoring the normal component after correction-field smoothing -- the normal component of the noise IS the noise (gauntlets 24%). Correction-field smoothing (12 Jacobi passes at load, `DELTA_SMOOTH_PASSES`) is the tuned tradeoff: fewer passes = crisper fit, more = smoother mesh but clipping slowly returns past ~16.
- **Clearance enforcement** (`CLEAR_MARGIN=1.0`, 2 iterations): authored clearance (T-pose vert vs OB body) + outward margin is enforced against the fitted surface. The deficit is DIFFUSED over the (global) armor mesh graph before pushing (raw per-vertex pushes crumple meshes: gauntlets went to 62%), but `PUSH_RAW_KEEP=0.6` of the raw deficit survives as a floor — diffusion alone diluted genuine isolated deficits (shirt-collar rings 0.9-1.9 deep) into surrounding slack. Gates: authored proximity (`CLEAR_PROX=4.0`; 2.5 faded enforcement exactly where collars authored 2-3 off the neck clipped), fit-reliability (local stretch, floored at `REL_FLOOR=0.4` on body triangles — otherwise enforcement dies at wrist/neck seam rings), and **body-part-only** (`part` array; hand/foot fits are noisy at fingers, and gauntlets/boots replace body hands/feet in Skyrim — EXCEPT the wrist/ankle seam region: hand/foot verts within 3 units of the body surface count as body, which is where clothing shoe tops and shirt cuffs clip). In the reliability blend, zero-rel hand/foot triangles ABSTAIN (weighted mean over voters within d_best+2) instead of vetoing — a cuff half-surrounded by hand triangles keeps the forearm's reliability, but boot-shaft verts must not inherit reliability from calf triangles 8+ units away. Verts authored INSIDE the OB body (collar necklines, c0 ~ -0.6..-1.5) get depth-preservation (target = c0, no margin — `CLEAR_INNER_FADE`); excluding them entirely let the field drag collars 2+ units deeper.
- **Metrics tool**: `python -m tools.nif.armor_fit_metrics <src.nif> <converted.nif> [--weight 0|1]` -- edge-failure %, high-frequency distortion (per-tri stretch spread = crumple signal vs smooth reshaping), clearance preservation vs the wrap surfaces, penetration. Distortion is deliberately traded for anti-clipping (user: clipping is visible, distortion is not): iron cuirass ~25% edges>15%, boots ~19%, gauntlets ~7.6%, but flagged penetrating verts are near zero everywhere visible (male shirt collar 73→5, female shirt collar 67→2, gauntlets/boots vs their replaced body parts don't count). Known remaining warts: crotch-cavity hems (cuirass front fauld, robe center panel) where signed distance itself is ill-defined -- hidden in game.

## NIF weapon Prn (attach node) contract
<a id="nif-weapon-prn-contract"></a>
- The draw animation looks for the weapon at the skeleton node matching its WEAP AnimationType; the mesh's `Prn` decides where the engine actually parents it. A mismatch = weapon stays sheathed / hands look empty when drawn (seen THREE times: axes with Prn=WeaponAxe but WEAP type Mace; bows with Prn=WeaponBack; shortswords with Prn=WeaponDagger but WEAP type Sword → "invisible while held", fixed 2026-07-15).
- **Shortswords stay on WeaponSword** (they're Sword-type records); **daggers get Prn=WeaponDagger AND the WEAP record refined to AnimationType Dagger (2)** — the filename-keyword refinement runs on BOTH sides (`_remap_prn` in nif_converter.py and `convert_WEAP` in tes5_import/record_types/equipment.py) keyed on the model basename so they can never diverge.
- **Bows must get Prn='WeaponBow'** (vanilla ironbow.nif), NOT 'WeaponBack' — Oblivion uses 'BackWeapon' for both 2H weapons and bows, so `_remap_prn` refines by filename ('bow' in basename).
- **Bows are exempt from the blanket weapon 180° Y-flip** (the war-axe orientation fix applied to every `_WEAPON_PRN_VALUES` mesh): Oblivion bows already match the Skyrim WeaponBow frame — string plane at x≈-15.7 vs vanilla string bones at x≈-13.7, limbs along ±Y. The flip held them backwards (curve toward the archer).
- **Bow bend rig (`asset_convert/character/bow_rig.py`)**: converted bows get the exact vanilla 7-bone chain (Bow_MidBone → Lo/Up chains → StringBones; locals lifted from vanilla steelbow.nif — the rig is the animation contract, BowProject.hkx clips store absolute local bone transforms) + BGED `Weapons\Bow\BowProject.hkx` + BSXFlags Animated bit (0x08). Geometry is skinned with plain NiSkinInstance (vanilla bows never use BSDismember) using the measured vanilla weight profile (Mid→B1 crossfade |y| 4-16, B1→B2 20-36, tips ~58/42 B2/StringBone; string = SB1↔SB2 lerp). String verts are identified from the Oblivion NiGeomMorpherController draw morph (string moves ~28 units vs limb ~7-10; capture BEFORE controllers are stripped) — verified on all 8 vanilla Oblivion bows.
- **SLSF1_Skinned (shader_flags_1 bit 0x02) is mandatory on the bow shape's BSLightingShaderProperty** — without it the renderer never applies bone deforms: the bow renders frozen in bind pose while the graph animates the bones (string never draws, limbs never bend). Shader conversion runs before the rig exists, so `add_bow_rig` sets the flag itself after skinning (vanilla steelbow SF1=0x82400383 has it set).

## NIF torch Prn — Skyrim carries the torch on the SHIELD node (SOLVED 2026-08-01)
<a id="nif-torch-prn-skyrim-carries"></a>
- Oblivion `Prn='Torch'` → **`'SHIELD'`**, not `'NPC L MagicNode [LMag]'`.
- Skyrim holds the torch in the off-hand: vanilla `meshes\weapons\torch\torch.nif`
  ships `Prn='SHIELD'` (and lives under `weapons\`, not `lights\`). The static
  sconce torches under `clutter\common\` carry **no Prn at all** — they are
  placed world objects, so they are not evidence for the carried case.
- `NPC L MagicNode [LMag]` is the spell-**cast** node; its axes point outward
  from the open palm, so a torch parented there renders rotated ~90° with the
  flame sticking out to the left. Weapons were unaffected because they route
  through the `Weapon*` nodes, which is why this looked torch-specific.
- **Sharing the SHIELD node does NOT make it a shield.** The `remapped ==
  'SHIELD'` branch must be split on the ORIGINAL `prn_val`: a torch takes
  **no shield attach transform**. That transform exists only because Oblivion
  straps a shield to `Bip01 L ForearmTwist` while Skyrim glues the root to the
  SHIELD bone at the grip — a torch is authored at the grip in both games, so
  remapping frames throws it ~65° off with a -20.5 forearm-strap offset. This
  was the second, separate cause of "torch orientation completely wrong": the
  Prn was right, the geometry transform was not.
- Vanilla `torch.nif` is identity rotation, zero translation, geometry at
  identity; flame at +Y (`TorchFire` y≈28.96), matching the converted
  `FlameNode1` y=27 / `AttachLight` y=35.
- Torch **does** still need its own BSInvMarker: `SHIELD` is in
  `_EQUIPPED_PRN_VALUES`, so the per-mesh inventory pass skips it and it would
  otherwise ship none. Vanilla values rot (4712, 0, 0) **zoom 0.82** (shield is
  the same rotation but zoom 1.0) → `TORCH_INV_MARKER_*` in `skyrim_overrides.py`.
- Verified against `references/Skyrim Meshes` — **not** the SSE BSAs, which are
  off-limits (see CLAUDE.md); `asset_convert/sources/skyrim_assets.py` is for the
  runtime pipeline only, never for "what does vanilla do here?" debugging.

## NIF shield conversion
<a id="nif-shield-conversion"></a>
- Shields use BSFadeNode root + Prn='SHIELD' (same as weapons, NOT NiNode like worn armor)
- **Orientation fix**: Oblivion shields are modeled with thin (face-normal) axis along Y. Skyrim's SHIELD bone expects it along Z. A +90° rotation around X is applied to the BSFadeNode root. Root rotation baking wraps this in an inner NiNode.
- **BSInvMarker**: Shields need BSInvMarker for inventory display: rot=(4712,0,0), zoom=1.0 (from vanilla ironshield.nif). Without BSInvMarker, shield is invisible in inventory. Shields keep this constant — they are exempt from the per-mesh inventory-orientation pass (see BSInvMarker section below).
- Oblivion Prn values for shields: 'Shield' or 'Bip01 L ForearmTwist' → remapped to 'SHIELD'
- Oblivion shield geometry names: 'Shield:0', 'Shield:2' (single geometry block, no skin)

## NIF armor ground model (_gnd) conversion
<a id="nif-armor-ground-model-conversion"></a>
- Armor/clothing _gnd files need **BSInvMarker** for inventory display. Without it, items are invisible in the inventory 3D viewer.
- BSInvMarker is added during NiNode→BSFadeNode conversion when `_is_gnd and _in_armor_dir` (constant rot=(1570,0,0) as an initial value), then **recomputed per-mesh by the inventory-orientation finalize pass** (see below).
- BSXFlags: vanilla gnd files use 194 (0xC2); our converted use 130 (0x82). Both load fine.
- **Ground-model detection must match the bare `gnd` suffix, NOT `_gnd` (fixed 2026-07-28, `_is_ground_model`)**: Bethesda's convention is `<item>_gnd.nif`, but assets that came through a Morrowind→Oblivion conversion lost the separator. Morroblivion rewrites `_` as `u`, so the same files arrive as `<item>ugnd.nif` (`cumurobeucommonu02ugnd.nif`, cf. `TXUCUclothwrap01`); others drop the separator after a body-part word (`...shoegnd`, `...shirtgnd`, `...pgnd`, `amuletcommon1gnd`). Census of `export/`: **1,120 files use `_gnd`, 195 do not** — every one of the 195 was misread as *worn armor*. Three failures follow from that single flag, and they are exactly the reported symptoms:
  1. `_is_worn_armor` becomes true → root stays **NiNode** instead of BSFadeNode, so the item **has no collision and floats where it was dropped / can't be picked up**;
  2. the `_is_gnd and _in_armor_dir` guard skips **BSInvMarker** → invisible/unoriented in the inventory viewer;
  3. worst, the `not _is_gnd ... and has_skin` guard lets a *static* ground model be **FK-retargeted onto the Skyrim biped** — this **mangles the mesh**. 48 of the 195 are skinned and were being deformed this way.
  The mangling looks like a mesh-orientation bug (the robe's source bbox is X ±47, Z −86→32, i.e. apparently "on its side") but the source geometry and its bind data are correct and upright — the wide/negative extents are just sleeves and the down-the-bone bind offsets. Nothing needs to be "stood up" first; the mesh only had to skip the retarget. Worn variants of the same items (`cumupantsugucommon010.nif`) always looked fine because they take the worn path legitimately.
  Matching bare `gnd.nif` covers all three spellings. A worn mesh would have to genuinely end in the letters "gnd" to false-positive, which no body slot or equipment word does (verified: the char preceding `gnd.nif` across all assets is only `_`, `u`, or a body-part/index character, plus files named exactly `gnd.nif`).

## BSInvMarker inventory orientation (learned 2026-07-18)
<a id="bsinvmarker-inventory-orientation"></a>
- **Engine convention** (derived empirically with `tools/inv_marker_survey.py` (removed 2026-08-25) across ~500 vanilla meshes, mean alignment 0.97+): stored ushort angles are milliradians; the inventory view rotates the model by `M = Rx(-rx/1000) @ Ry(-ry/1000) @ Rz(-rz/1000)` (column-vector, XYZ order, negated angles) and the camera looks along **+Y** with **+Z as screen-up** (screen-right = X). Reproduces vanilla exactly: ironshield (4712,0,0) = −Z face toward camera; cuirassgnd (1570,0,0) = +Z face toward camera with model −Y at screen-up; iron weapons (4712,6283,0) ≈ pure Rx.
- **Per-mesh computation** (`asset_convert/nif/inv_marker.py`): the finalize pass at the end of `_convert_nif` orients each mesh so the side with the greatest front-facing projected area (of the six area-weighted PCA axis directions of the triangle soup) faces the camera. Screen roll keeps model +Z at screen-up (upright items stay upright); when the view normal is ±Z (items modeled lying flat: books, pelts, plates, gnd armor) it follows the vanilla cuirassgnd rule (−Y up for face-up items, +Y for face-down). Hidden geometry (flags & 1), `Blood*` decal shapes and EditorMarkers are excluded from the analysis.
- **Scope**: applied to every non-creature, non-skinned BSFadeNode root — a marker is inert on meshes never shown in inventory, and clutter/books/ingredients/keys/soul gems have no reliable path signature. Existing markers (gnd) are recomputed; missing ones are added (zoom 1.0).
- **Weapons/shields/quivers are exempt** (`_EQUIPPED_PRN_VALUES`, matched on the post-remap Prn): conversion normalizes them into vanilla attachment frames (Prn node convention / SHIELD attach transform), so the vanilla-derived constants are already exact. The computed value would also flip shields: a shield's concave strap side genuinely has more visible area than its display face.
- Geometry math uses PyFFI's row-vector transform convention throughout — the survey validated stored-marker ↔ pyffi-space relationships end-to-end, so the generator must use the same gather code (`gather_area_normals`).

## NIF skin retargeting (Oblivion → Skyrim skeleton)
<a id="nif-skin-retargeting"></a>
- **Critical**: Oblivion skeleton uses X-up coordinates (spine along X axis). Skyrim uses Z-up (spine along Z)
- **Current approach: Corpus search + L-BFGS-B continuous optimization**:
  1. `tools/generators/kf_animation_explorer.py --build-cache` searches 453 .kf animation files with parallel parsing (ThreadPoolExecutor, 31 workers, ~36s)
  2. Per-bone transform library: 65 bones, 336K candidates from entire animation corpus
  3. Chain-level softmax blend (T=1.0, effectively argmin) over ~25K coherent frames per chain (left/right arm, left/right leg — body chain excluded)
  4. **Multi-start L-BFGS-B refinement**: 50 starting frames per chain, axis-angle rotation perturbation bounded to ±0.35 rad (~20°), parallelized with ThreadPoolExecutor. Discovers poses NOT in the .kf corpus.
  5. L/R mirroring for symmetry
  6. Pre-computes delta matrices `inv(rest_world) @ anim_world` per bone, saved to `asset_convert/generated/best_animation_pose.json`
  7. `skin_retarget.py` Phase B.1: loads pre-computed deltas, applies standard LBS using OB skin weights: `v' = Σ w_i * (v @ delta_i)`
  8. Phase A: repositions bones to Skyrim skeleton positions
  9. Phase C+D: recomputes bind matrices (`manual_update_bind_position`) and skin partitions
  10. **FK+Gaussian double-deformation MUST be avoided** — Gaussian spatial blend only runs when FK was NOT applied.
- **FK results**: Post-mirror RMSD 9.64 (was 9.73 corpus-only). Legs: 2.8/1.8→1.08/1.08 (62% improvement). Arms: 4.4→4.0 (10%). 37/37 tests pass, 396 armor NIFs 0 errors.
  - **`_mat3_to_quat` NIF convention**: This function expects a column-vector convention matrix. PyFFI Matrix33 / NIF matrices use row-vector convention so `_mat3_to_quat(NIF_Matrix)` returns the CONJUGATE. In `skin_retarget.py` the delta matrices are numpy column-convention, so pass `_mat3_to_quat(delta[:3,:3].T)` (transpose, no sign flip). For collision baking this is moot — **do not apply _mat3_to_quat to bhkRigidBodyT at all**.
- **Spatial blend residual was wrong direction**: `v_spatial` (spatial blend from OB rest ≈ 50% to SK) minus `v_fk` (FK ≈ 90% to SK) = vector pointing BACKWARD toward the LESS-transformed position. DQS inherently handles joint boundaries — no separate residual needed.
- **ProcessPoolExecutor causes issues on Windows**: Exit code 1 + slightly worse results. Reverted to sequential `for` loop for L-BFGS-B multi-start. Module-level `_lbfgsb_trial_worker` kept (clean, no harm). ThreadPoolExecutor for first kf-parsing step is fine (I/O-bound).
- **Geometric limit**: Arm RMSD ~4.0 is the minimum achievable with rotation-only optimization. UpperArmTwist (err=13.4) and ForearmTwist (err=9.4) contribute 56% of arm cost from bone LENGTH differences between OB/SK skeletons. Excluding twist bones from cost made mesh quality WORSE (larger main-bone rotations).
- **Body chain**: Including spine in optimization gives spine RMSD 5.59 but BREAKS cuirass edges (18.8% fail) — spine deltas distort LBS. Spine gets identity delta; Phase A handles repositioning.
- **Gaussian spatial blend (fallback)**: Only runs when best_animation_pose.json is absent. Uses distance-based Gaussian-weighted bone blending with σ=20.
- Vertices in OB armor NIFs are in standard world-space coordinates (Z-up), NOT in the OB convention-rotated space.
- NiSkinData B_bone = inv(W_sk_bone) when M_mesh = identity (standard for skinned armor)
- Skeleton data: `asset_convert/generated/skeleton_bones_skyrim_{male,female}.json` and `skeleton_bones_oblivion.json`
- Female armor detected via `/f/` in path → uses female skeleton data
- PRN meshes (single bone, identity B) are NOT reposed — they're rigidly attached to one bone
- **Critical**: ALWAYS use `manual_update_bind_position()` instead of PyFFI's `update_bind_position()`. PyFFI's version computes wrong B values when geometry has a non-identity local transform. The manual numpy version handles this correctly.
- **Test suite**: `tests/test_skin_retarget.py` — 37 tests covering skeleton loading, bone mapping, MBW=I, vertex deformation, bone position accuracy, edge length preservation (<10% failure for cuirass, <5% for boots), full converter integration, skin partitions, PRN handling, BSDismemberSkin. All 37 pass.
- **Previous approaches that FAILED** (16+ attempts):
  - v2 bind-matrix-only (no vertex deformation): Arms stuck in A-pose at rest.
  - Skin-weight-based DQS/LBS: Sharp weight boundaries → 24-82% edge failure
  - Gaussian spatial blend alone: 40-50% displacement dilution on arms (normalized weight averaging)
  - FK LBS + Gaussian together: Double deformation → 13.9% edge failure
  - Laplacian-smoothed mesh weights: Created discontinuities. REVERTED.
  - Global/per-mesh inverse filter, RBF interpolation, 2x global overshoot: All failed (see repo memory for full list)

## Distorted worn clothing — nested bones placed with the wrong operand order (SOLVED 2026-08-25)
<a id="distorted-worn-clothing-nested-bones"></a>

- **Symptom**: worn shirts and armour render with long tapering spikes shooting
  out of the body, while torso and trousers sit correctly. Reported in game on
  Nehrim's `LowerShirt10` ("Flickweste", `Clothes\LowerClass\10\M\shirt.NIF`).
- **Cause**: Phase A of `retarget_skin_to_skyrim` computed a nested bone's local
  transform as `inv(parent_W) @ W_sk` — the COLUMN-vector form. This module is
  row-vector throughout (`m44_to_np` puts translation in row 3, and
  `get_transform` composes `world = local @ parent`), so the correct expression
  is `W_sk @ inv(parent_W)`. Now `skin_retarget.local_for_world`, guarded by
  `tests/test_skin_retarget.py::TestLocalForWorld`.
- 🔴 **Why it hid for months although the line never changed.** `git log -L`
  shows it untouched since the initial commit. Bones parented DIRECTLY to the
  skeleton root take the `parent is skel_root` shortcut and never multiply at
  all — and the output's bone tree used to be **flat**, every bone a direct
  child of the root. `7c6e3df` ("bake geometry to skeleton space before
  retargeting", 2026-07-28) made the tree **nested**, and from then on every
  bone below the first level ran through the faulty line. Verified by building
  the same mesh in a throwaway worktree at `f43ec32`, the commit before: 0
  misplaced bones there, 14 of 22 one commit later.
- **The signature to recognise it by**: clavicle, pelvis and neck are EXACTLY
  right (depth 1) while everything below them is displaced, and the error
  compounds down each chain. Measured on the shirt: UpperArm 85.87, Forearm
  162.78, Hand 275.97, Spine2 20.76 units — and the wrong operand order
  reproduces those four numbers to four decimals.
- 🔴 **Why no structural check could see it, and what to check instead.**
  `manual_update_bind_position` derives the bind matrices FROM these node
  positions, so the file stays internally consistent: weight pairs (587/587),
  `_0`/`_1` rig equality, skin partitions, dismember slots, bone names, no
  missing bones, and `S @ B_i @ W_i = I` all pass. The game animates the
  ACTOR's skeleton instead, which is where the disagreement lives. The only
  check that sees it compares bone node positions against the skeleton —
  `tools/skin_skeleton_check.py`. This is the concrete case behind CLAUDE.md's
  "a CLEAN audit is not an alibi".
- **After the fix** (full `--meshes-only` rebuild, spot-checked in game by the
  user 2026-08-26): clothes and armour female 100% correct nodes, armour male
  99.1%, clothes male 94.1%; the reported shirt 14/22 → 0. `weight_pair_check`
  unchanged at 587/587, so nothing that worked before regressed.
- **Ancestor-only nodes are placed too.** Phase A used to move only the
  bones some skin references, so a tree node kept only as an ancestor (a
  robe's `Neck`, and the clavicles under it) stayed at its source frame while
  its children moved; the body splice then bound fill skin to it by name and
  tore it away. Every node with a Skyrim target is now placed, root first:
  skin-bone world frames, vertices and binds are unchanged (Oblivion iron
  armor and shirt byte-identical), and Morrowind worn meshes with a
  misplaced node fell 232 -> 2 of 842.
- **Not yet diagnosed**: about a dozen male meshes keep 1–2 misplaced nodes
  each (`upperclass\01|03|05\m\shirt`, `middleclass\02\m\pants`,
  `middleclass\mcshirtsneaky\m\shirt`, `nehrimsoldier\m\cuirass03`), offsets
  6.7–67.4 units. Smaller, and mechanically different from the Phase A fault.


## Creature skin render crash — >80 skin bones per shape (SOLVED 2026-07-10)
<a id="creature-skin-render-crash-80"></a>
- Symptom: render-thread `EXCEPTION_ACCESS_VIOLATION` in a VCRUNTIME140 memcpy
  (`vmovdqa [rcx+…]`) inside BSBatchRenderer pass setup (BSUtilityShader =
  shadow-depth pass); crash objects show NiSkinInstance/NiSkinPartition +
  BSTriShape named `"(<armo fid>)[0]/(<arma fid>) [50%]"`. FormIDs resolve
  (via `tools/esm/tes5_esm_reader.py <esm> --formid <fid>`) to the generated
  creature skin ARMO/ARMA.
- **Root cause (proven by crash-log registers)**: SSE memcpys one 3x4 matrix
  (48 B) per NiSkinInstance bone into a fixed **80-matrix (3840 B) buffer**.
  Imp = 85 bones → copy size RBP=4080=85×48, fault at dest offset 3840=80×48,
  R8=464 remaining. Per-partition bone counts are irrelevant; the per-shape
  TOTAL is the limit. Vanilla max: dragon, 77. The crash needs the shadow
  path, so >80-bone actors can *appear* fine where no shadow-casting light
  hits them (mehrunesdagon/spiderdaedra initially seemed unaffected).
- **Fix (in-game verified)**: `skin_bone_cap.merge_oversized_skin_bones()` —
  merge the lowest-total-weight LEAF bones into their parents until ≤78
  (SSE_MAX_SKIN_BONES; vanilla max is 77 and splitting at exactly 80 froze the
  game, so stay clearly under). Bind pose is exact (B·W=I at rest); only tip
  articulation (fingertips/ear tips/eyebrows) is lost. Weights renormalized;
  partitions regenerated afterwards.
- **Where it runs matters**: called from `merge_creature_body()` AFTER rig
  grafting — Oblivion body-part NIFs store their skin bones FLAT (no
  parent/child links), so leaf detection only works on the merged rig. The
  hierarchy lookup is BY NAME (bone pointers aren't tree members at part
  stage). Affected creatures: imp 85→78, spiderdaedra 88→78, mehrunesdagon
  98→78; all other 151 merged bodies were already ≤80.
- **Shape-SPLITTING does NOT work** (tested in-game: game froze) — don't
  split skinned shapes to duck the cap; merge bones instead.
- Diagnostics: `tools/nif/skin_partition_dump.py <nif>` (per-shape/partition
  bone/vert/tri counts, flags >80-bone shapes + index-range problems).

## <a id="prn-attached-rigid-pieces"></a>PRN-attached rigid pieces take no FK compensation
<a id="prn-rigid-piece-offsets"></a>

PRN-attached rigid pieces (no real skin in the Oblivion NIF; rigid-skinned
to the Skyrim bone by nif_converter._add_prn_skin).  Their verts are
authored in an upright bone-pivot frame and land EXACTLY in the Skyrim bone
frame after retarget, so the FK-deformation compensation offsets in
ARMOR_PIECE_OFFSETS (e.g. helmet dz=+7, tuned on genuinely skinned helms
like TownguardCho) must NOT be applied — they pushed rigid helms on top of
the head.  The only correction needed is the anatomical difference between
the heads in their respective head-pivot frames, measured from
OB headhuman.nif vs SK malehead.nif (see docs/commentary/asset_convert_nif.md):
  skull top   OB +13.6  SK +11.5  -> dz = -2.1
  Y span      OB [-3.75, +11.31]  SK [-5.97, +11.58] -> the SK skull


### <a id="prn-multi-shape-bone-frame"></a>Every shape of a PRN piece gets the SAME bone frame

A PRN piece's own node translation is an offset WITHIN the piece, not a second
attachment point, so the bone position belongs on every shape equally and the
shape's own offset has to survive it. Two wrong ways, each measured:

- **Overwriting the node outright** drops the piece's own offset. Armun-An
  Bonemold (node y=+2.8567) landed 3.33 units behind the skull once the PRN
  sy=1.165 scale amplified it.
- **ADDING the bone position to the node** is right only for a single-shape
  piece. With two shapes it offsets them against each other, which split the
  Imperial Legion helm (`Helmet:0` at origin, `default` at x=-1.6) into a
  centered half and a shifted half.

Correct: bake each shape's own transform into its verts (`bake_block_transform`),
then give every shape the identical bone frame. Rigid skinning ignores node
transforms at render time anyway, and the identity bind `_add_prn_skin` writes
needs the verts to carry the offset.


## Closing the last ~10% cuirass-edge gap — 17 ideas, 11 measured failures
<a id="cuirass-edge-gap-ideas"></a>

Moved out of `skin_retarget.py`, where it was 260 lines of comment above the
first statement. Baseline at the time: **cuirass 10.80%, gauntlets 2.43%,
boots 0.45%** edge failure (thresholds 15% / 15% / 10%).

Root cause, measured: 418/418 UpperBody failures are Clavicle-adjacent
(238 Clavicle-Clavicle + 104 Clavicle-UpperArmTwist + 76
UpperArmTwist-UpperArmTwist). Adjacent vertices carry different bone-weight
ratios, so DQS gives them slightly different effective rotations and the edge
between them stretches. The residual arm RMSD ~4.0 is a rotation-only floor:
UpperArmTwist (err 13.4) and ForearmTwist (err 9.4) are ~56% of arm cost and
come from bone LENGTH differences, which rotation cannot fix.

**Tried and reverted — do not retry without new evidence:**

| Idea | Approach | Result |
|---|---|---|
| 1 | Pre-FK per-chain bone-length scaling | reverted |
| 5 | Post-FK per-chain Procrustes/Kabsch snap | reverted |
| 7 | Targeted twist-bone delta propagation | reverted |
| 8 | Factored global+local FK | cuirass 10.80% -> 13.8% |
| 9 | Edge spring relaxation, bone-dominance anchored | 10.80% -> 8.83%, but UV-seam twin vertices tore visible holes; DISABLED |
| 10 | Laplacian deformation, bone-position constrained | all variants reverted; uniform Laplacian unsuitable |
| 11 | Virtual intermediate shoulder-blend bone | 10.80% -> 10.04%, worse than spring |
| 13 | ARAP | cuirass -> 36.21%, boots -> 18.79% |
| 14 | Weight sharpening, gamma sweep 1.5-4.0 | reverted to gamma=1.0 |
| 15 | Cotangent Laplacian correction | cuirass -> 32.5%, catastrophic |
| 16 | Anchor co-rotation prediction (deformation transfer) | 9.05% -> 9.91% |

**Never tried** (predictions only, no measurement): Gaussian pre-warp (2),
thin-plate-spline warp (3), ARAP as originally framed (4), chain-level affine
FK with shear (6), seam-welded spring relaxation (12), anchor-constrained
spring with co-rotation targets (17).

The pattern across 11 failures: any method that lets non-rigid deformation
touch the mesh globally trades a small edge-length win for large distortion
elsewhere. Idea 9 is the only one that ever improved the number, and it
shipped disabled because the artifact it introduced was worse than the metric
it fixed.

## `retarget_skin_to_skyrim` — the four phases, and why the order is fixed
<a id="retarget-phase-order"></a>

`skin_retarget.retarget_skin_to_skyrim` runs four phases in a fixed order.
Each ordering constraint below was paid for by a real defect.

### Phase 0 — bake geometry into skeleton space

Everything downstream (the FK deform, the body wrap, and Phase C's bind-matrix
rewrite) assumes a geometry's stored vertices ALREADY sit in skeleton space.
**That is not part of the skinning contract.** NifSkope renders a skinned mesh
as `bone_world * skin_transform * vertex`, so the authored frame is arbitrary
and it is the bind matrices that stand the mesh up.

Most Oblivion armor happens to be authored with that product ≈ identity, so the
distinction never surfaced — but **81 of 171 Morroblivion clothing meshes** store
geometry in a genuinely different frame.

Phase C (`manual_update_bind_position`) unconditionally rewrites the bind
matrices to `B_i = G @ inv(W_i)`, which FORCES `S @ B_i @ W_i = I`. The
transforms that were standing those meshes upright get destroyed, and the
authored frame cannot be recovered afterwards. So the mesh must be baked into
skeleton space up front — which also makes the raw coordinates mean what every
later stage already assumes they mean.

### Phase B — vertex deformation, BEFORE bone repositioning

The preferred path is the surface-relative wrap: an exact fit onto the Skyrim
body that preserves each vertex's authored clearance from the body surface.
`weight` picks the `_0`/`_1` Skyrim body target for the weight-morph variants.

**The FK animation deform is not dead code superseded by the wrap — it is the
wrap's foundation.** `deform_geoms_wrap` runs `deform_vertices_animation_fk`
internally as its smooth base and only adds the measured correction on top. The
field BUILD itself FK-poses the Oblivion body meshes through this exact path
(with `allow_wrap=False`, so the wrap can never bootstrap from a previous
field), and FK remains the fallback when no wrap field exists for a gender.

### Phase A — move bone NiNodes to Skyrim positions

Bones are processed root-to-leaf (sorted by depth) so a parent's transform is
already set when its children are computed. See `local_for_world` for the
row-vector operand order and why reversing it stayed invisible until the bone
tree became nested.

`manual_update_bind_position` then derives the bind matrices FROM these node
positions, so **a wrong position leaves the file internally consistent** and no
structural check can catch it. In game the engine uses the actor's skeleton
instead, and the vertices shoot off as spikes.

### Phase C+D — recompute skin data, regenerate partitions

A PRN piece hangs off ONE bone: in Oblivion the whole piece is parented to it,
and each shape's node transform positions it within that bone's frame.
