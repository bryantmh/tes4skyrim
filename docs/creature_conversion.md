# Creature Conversion: Oblivion CREA → Skyrim Actor (Fully Automated, No Donors)

Step-by-step plan for converting Oblivion creatures (models, skeletons, animations,
behavior, records) to working Skyrim SE actors, **fully automated and plugin-agnostic**
(the project goal). Consolidates research from: **pynifly 27.4.0**
(`references/PyNifly-27.4.0` — Skyrim-side NIF/HKX read+write), the **niftools Blender
addon** (`references/DovahNifWorkbench 2.5 Source/external/sdk_imports/blender_niftools_addon-master`
— Oblivion-side KF/skeleton semantics), the **vanilla Skyrim actor meshes**
(`references/Skyrim Meshes/meshes/actors/`), the **extracted LE animation archive**
(`references/Skyrim Animations/` — behavior projects, animation HKX, animationdata),
the **Skyrim.esm record dump** (`references/Skyrim.esm/RACE.txt`), and the Oblivion
source assets (`export/Oblivion.esm/meshes/creatures/`).

**Strategy in one line**: faithful port of everything Oblivion provides (skeleton, skinned
meshes, animations, ragdoll) + **programmatic generation of the one thing Oblivion doesn't
have — the behavior graph** — from a fixed template, because Oblivion's "behavior" is a
uniform engine convention, not per-creature data.

---

## 1. The format gap

| Layer | Oblivion (TES4) | Skyrim SE (TES5) |
|---|---|---|
| Record | CREA (Model.MODL = skeleton path, NIFZ = body-part NIF list, KFFZ = special anims) | NPC_ → RNAM → **RACE** (ANAM=skeleton.nif, Behavior Graph MODL=`Actors\...\<X>Project.hkx`, WNAM=skin ARMO) → ARMO → ARMA (MOD2 = skinned body NIF) |
| Skeleton | `skeleton.nif`, NiNode "Scene Root" → `Bip01` → `Bip01 NonAccum`; per-bone `NiTransformController` + `bhkBlendController`; Oblivion-format bhk ragdoll (bhkBlendCollisionObject, Ragdoll/LimitedHinge/Malleable constraints); UPB strings carry BoneLOD/mass | `skeleton.nif`, **BSFadeNode** root; extra data: BSXFlags=**198**, BSBound, BSInvMarker, BSBoneLODExtraData, `NiIntegerExtraData 'SkeletonID'`; Skyrim-format bhk ragdoll baked into the NIF; **plus** a runtime `character assets\skeleton.hkx` (hkaSkeleton) |
| Body mesh | NiTriStrips/NiTriShape + NiTexturingProperty, `NiSkinInstance` | plain **NiNode** root (not BSFadeNode), NiTriShape + BSLightingShaderProperty, plain `NiSkinInstance` (creatures do NOT use BSDismember — that's humanoid body parts) |
| Animation | `.kf` = NiControllerSequence; bone tracks target the `Bip01` chain **by name via NiStringPalette**; interpolators are **mostly NiBSplineCompTransformInterpolator** (B-spline compressed) + some NiTransformInterpolator; text keys (`start/end`, `Sound: X`, `Enum: Left/Right` gait, `Hit`, `a: L/R`, `Blend: N`) | `.hkx` = Havok **hk_2010.2.0-r1** packfile, hkaSplineCompressedAnimation + hkaAnimationBinding; annotations for events; SE = 8-byte pointers, LE = 4-byte |
| Anim selection | Filename convention (`forward.kf`, `handtohandattackleft.kf`, `idleanims\specialidle_*.kf`) + IDLE records. **No behavior graphs — selection logic is hardcoded in the engine.** | **Behavior graph project** (`<X>Project.hkx` → character hkx → behavior hkx state machines) + `meshes\animationdata\` (per-clip triggers + root motion) + `meshes\animationsetdata\` |
| Movement/combat data | ACBS/AIDT/DATA on CREA | RACE: MTNM movement-type names (WALK/RUN1/SNEK/BLDO/SWIM → MOVT records), WKMV/RNMV, ATKD/ATKE attack events (must match behavior-graph event names), GNAM body part data (BPTD), footstep SNDD on ARMA |

**The key insight**: the only layer with no TES4 source is the behavior graph — and that's
because in Oblivion the behavior IS the engine's fixed, filename-driven animation
convention, identical for every creature in every plugin. A Skyrim behavior graph that
replicates that convention is therefore a **constant template parameterized only by each
creature's clip inventory** — exactly what automated generation is good at.

---

## 2. Why generation is feasible (evidence, verified 2026-07-07)

All from `references/Skyrim Animations/` (extracted LE `Skyrim - Animations.bsa`) and
hands-on tool tests:

1. **A creature project is small and boilerplate-heavy.** Deer's complete stack:
   - `deerproject.hkx` (800 bytes): hkbProjectData + hkbProjectStringData → just points at
     `Characters\DeerCharater.hkx`. Pure boilerplate.
   - `characters\deercharater.hkx` (4 KB): hkbCharacterData/StringData → rig path
     (`Character Assets\skeleton.HKX`), behavior path, **the animation filename list**,
     character properties. Template + file list.
   - `behaviors\deerbehavior.hkx` (17 KB, ~40 objects): thin creature wrapper — ragdoll
     drive (hkbPoweredRagdollControlsModifier, hkbRigidBodyRagdollControlsModifier),
     getup (hkbGetUpModifier), death pose matching (hkbPoseMatchingGenerator), and a
     hkbBehaviorReferenceGenerator into the shared locomotion graph.
   - `behaviors\quadrupedbehavior.hkx` (79 KB, ~350 objects, ~30 hkb class types): the
     real state machine — 21 nested hkbStateMachine, 57 states, 29 hkbClipGenerator,
     blend trees, expressions, BSLookAtModifier, BSSpeedSamplerModifier, foot IK.
2. **Bethesda themselves used one shared graph across species.** `quadrupedbehavior.hkx`
   serves deer, wolf, dog, cow, sabrecat, skeever, horse, and bear — its variables include
   `iCharacterSelector`, `iState_WolfDefault`, `iState_BearDefault`, `bIsWolf`, and its
   events include per-species idles (`idleDogBarkStart`, `idleCowFeedingStart`). Our
   generator replicating one parameterized graph per creature is *simpler* than what
   vanilla does (no cross-species switching needed).
3. **The engine⇄graph interface is enumerable.** From the graph string data:
   - Variables the engine drives: `Speed`, `TurnDelta`, `Direction`, `TargetLocation`,
     `iCombatStance`, `staggerMagnitude`, `bHeadTrackingOn`, `bAnimationDriven`,
     `bAllowRotation`, `FootIKEnable`, `IsAttacking/IsStaggering/IsRecoiling/IsBleedingOut`
     + foot-IK gains (`m_*`).
   - Events the engine sends: `moveStart/moveStop`, `moveForward/moveBackward`,
     `turnLeft/turnRight/turnStop`, `cannedTurnLeft90/…180/cannedTurnStop`,
     `combatStanceStart/Stop`, `weaponDraw/weaponSheathe`, **`attackStart_<Name>`**
     (must match RACE ATKE strings — we generate both sides, so consistency is by
     construction), `staggerStart`, `recoilStart/recoilLargeStart`,
     `bleedOutStart/Stop`, `deathStart`, `IdleStop/idleExit`, `ReanimateLeft/Right`,
     `GetUpLeft/Right`, `SyncLeft/SyncRight`.
   - Events the graph emits: `preHitFrame`, `HitFrame`, `SoundPlay`, `FootFront/FootBack`,
     `attackStop`, `GetUpEnd`, `Reanimated`, `AddCharacterControllerToWorld`.
4. **The full authoring toolchain is CLI-automatable and verified**:
   - `hkxcmd convert -v:XML` dumps any LE hkx to editable XML; `-v:WIN32` compiles XML →
     binary **byte-count-identical to the original** (deerproject round-trip: 800 → 800
     bytes). hkxcmd is at `references/PyNifly-27.4.0/io_scene_nifly/hkxcmd.exe`.
     **Generation/validation happen in Skyrim LE format (32-bit WIN32); the SHIPPED
     files must be converted to 64-bit AMD64 as the final step** (`hkx_xml.
     convert_hkx_to_amd64`, wired in creature_pipeline). SSE loads LE-format NIF/
     texture assets, but its 64-bit Havok runtime CANNOT deserialize 32-bit packfiles —
     every vanilla SSE hkx has pointer size 8 (verified against the user's install),
     and a 32-bit project makes the behavior-graph load fail silently → the actor
     spawns INVISIBLE with only its collision capsule working (the 2026-07-08 bug).
     `hkxcmd convert -v:AMD64` on the LE dogproject.hkx reproduces Bethesda's shipped
     SSE dogproject.hkx BYTE-IDENTICAL, proving it is the correct LE→SSE conversion.
     The 32-bit hkxcmd cannot READ AMD64 files back, so all round-trip validation must
     run on the WIN32 file before the in-place AMD64 conversion.
   - So generation = **emit XML from Python templates (diffable, testable) → hkxcmd →
     binary**. No Havok SDK. pynifly's native hk_2010 reader doubles as a validator.
5. **The sidecar data files are plain text.** The LE BSA ships *per-project sources*:
   - `meshes/animationdata/<x>project.txt`: behavior/character/skeleton file list + one
     entry per clip (clip name, playback rate, trigger events with timestamps, e.g.
     `GetUpEnd:2.66667`).
   - `meshes/animationdata/boundanims/anims_<x>project.txt`: **per-clip root motion** as
     keyed translation/rotation rows (`1.03333 0 1.95652 0` = time x y z) — exactly what
     we'll compute from the decoded Oblivion `Bip01 NonAccum` tracks.
   - `meshes/animationsetdata/<x>projectdata/`: attack-set/weapon-state data, same style.
   - The engine consumes merged `animationdatasinglefile.txt` /
     `animationsetdatasinglefile.txt` — a concatenation with an index; we need a merge
     step in the pipeline (grammar fully visible from the extracted sources).
6. **No separate ragdoll hkx for creatures.** Deer's character assets contain only
   `skeleton.hkx`; ragdoll physics come from the Skyrim-format bhk blocks in
   `skeleton.nif` (which our `collision.py` constraint conversion already handles) driven
   by the graph's ragdoll modifiers. (Verify against draugr/werewolf during
   implementation.)

### Why NOT donor behavior graphs (the alternative considered)

Using a vanilla donor graph with faithful everything-else is coherent (graphs reference
clips by name; we could name our converted clips to match), but it fails the automation
requirement: every donor graph hardcodes its species' clip inventory and event set (deer
expects `runforwardl/r`, canned 90°/180° turns, three stagger grades — Oblivion creatures
have a different, smaller, differently-shaped set), so each creature needs hand-curated
clip mapping and donor selection — per-creature human judgment that breaks "works for any
plugin". Oblivion's KF naming convention, by contrast, is engine-fixed and identical
across all plugins, so a generator keyed on it is inherently plugin-agnostic. Donor
graphs remain useful **only as development scaffolding** (§4 Step 7) to validate mesh/
skeleton/animation conversion in-game before the generator exists — never in shipped
output.

---

## 3. Resource inventory

| Resource | Location | What it gives us |
|---|---|---|
| Oblivion creature assets | `export/Oblivion.esm/meshes/creatures/` — 33 creatures with `skeleton.nif`, 258 body NIFs, **1068 .kf** (+ `idleanims/`, `specialanims/`) | The complete source |
| **Extracted LE animations** | `references/Skyrim Animations/meshes/` — `actors/<x>/` (projects, characters, behaviors, animations, skeleton.hkx), `animationdata/` (+`boundanims/`), `animationsetdata/`, `genericbehaviors/` | Behavior ground truth + template material + sidecar text grammars |
| Vanilla Skyrim actor meshes | `references/Skyrim Meshes/meshes/actors/` — 40+ creatures, 3391 NIFs | Target-side skeleton.nif/body-mesh ground truth |
| Skyrim RACE/ARMA/ARMO dumps | `references/Skyrim.esm/RACE.txt` (99 races, full values incl. behavior paths), `temp/esm_dump/` | Record-side ground truth |
| pynifly hkx codec (VENDORED) | `external/pynifly_hkx/` (from PyNifly 27.4.0; format docs remain at `references/PyNifly-27.4.0/docs/hkx_*.md`) | hk_2010 packfile READER (validator) + hkaSplineCompressedAnimation COMPRESSOR (used by hkx_anim.py). Its binary WRITER is bypassed — output crashes real Havok deserializers. Zero Oblivion support — Oblivion side stays on PyFFI. |
| hkxcmd.exe (VENDORED) | `external/hkxcmd/hkxcmd.exe` | XML↔binary hkx compiler (real Havok serializer — owns all binary layout), verified byte-identical round-trip; EXPORTKF for studying vanilla clips. GOTCHAS: crashes on forward-slash paths; its CONVERTKF compressor is unusably lossy (debug only). |
| niftools addon | `.../blender_niftools_addon-master/io_scene_niftools/` | Oblivion KF/skeleton semantics: Bip01 X-forward convention, string-palette targeting, B-spline API shape (`get_times()/get_translations()/…`), bhkBlendController layout |
| Our pipeline | `tools/kf_animation_explorer.py` (KF parse, palette resolve, FK math — **skips B-splines**), `asset_convert/collision.py` (OB→SK bhk + ragdoll constraint conversion), `nif_converter.py` (`_resolve_palette_strings`, version upgrade), `skin_retarget.py` (NOT needed for creatures — see §4 Step 3) | Most machinery exists |
| LE archives (more) | `D:\SteamLibrary\steamapps\common\Skyrim\Data\` (`Update.bsa` has animation fixes; Meshes/Misc as needed) | Additional reference data |

### Remaining gaps (action items)

1. CREA export **drops NIFZ and KFFZ** (body-part list + special-anim list) —
   `tes4_export` fix required (§4 Step 0.1).
2. `tools/tes4_nif_analyzer.py` crashes on `bhkSimpleShapePhantom` (no `.mass`) — minor.
3. **ck-cmd** (github.com/aerisarn/ck-cmd) — optional cross-check only; hkxcmd covers the
   XML round-trip we need.
4. `Update.bsa` not yet extracted (animation fixes overlay some LE base files).

---

## 4. Step-by-step pipeline

### IMPLEMENTATION STATUS (2026-07-09) — pipeline is LIVE end-to-end
The whole chain is implemented and wired as pipeline **Phase 4b: Creatures**
(`python convert.py -f X --creatures-only`, GUI step "5. Creatures"):

- `asset_convert/creature_pipeline.py` — orchestrator: per creature folder →
  behavior project (`hkx_behavior.generate_creature_project`) + skeleton.nif/
  body-NIF conversion (`nif_converter creature=True`) + animation singlefile
  registration (`animation_data.write_singlefiles`) + the
  `export/<plugin>/creature_projects.json` contract for the importer.
  32/32 real Oblivion.esm creatures convert (boxtest/endgame excluded: test
  asset / KFM cinematic).
- `asset_convert/animation_data.py` — animationdata + boundanims +
  animationsetdata emission and the **singlefile merge** (vanilla base
  auto-extracted from the user's `Skyrim - Animations.bsa`, LE v104 zlib or
  SSE v105 LZ4, cached in `export/animdata_base/`). Grammar + the
  Bethesda hash (crc32 init=0/xorout=0 of lowercase; ≤4-char strings stored
  as packed ASCII — `hkx` = 7891816; dirs hashed WITH `meshes\` prefix)
  byte-validated against the vanilla files.
- `asset_convert/hkx_ragdoll.py` — the ragdoll stage inside skeleton.hkx:
  Oblivion `bhkBlendCollisionObject` bodies + ragdoll/hinge constraints →
  ragdoll hkaSkeleton + 2 hkaSkeletonMappers + hkpPhysicsData +
  hkaRagdollInstance (vanilla deer anatomy; GAME units — ob-havok ×7;
  identity mappers by folding body translation offsets into shape verts).
  Three hard contracts learned from the mangled-ragdoll saga (2026-07-20,
  verify with `tools/ragdoll_validate.py`):
  - namedVariants lists the **anim→ragdoll mapper FIRST**, ragdoll→anim
    second (30/30 vanilla creature census);
  - `unmappedBones` are indices **in skeleton B**: they belong on the
    ragdoll→anim mapper (anim bones with no ragdoll part) — putting them
    on the anim→ragdoll mapper indexes past the ragdoll skeleton;
  - **bind-pose limit legalization**: Oblivion authors joints whose limit
    window excludes the bind pose (dog head hinge [23.8°,32.6°], bind 0;
    deer thigh cone axis 35° off bind, cone 15°) — Skyrim's solver yanks
    those limbs to the boundary at death (mangled corpse). Vanilla keeps
    every bind angle inside its window (deer: exactly 0.0 everywhere), so
    `_legalize_limits` widens (never shifts) each window to contain the
    measured bind angle.
  - <a id="resource-data-tree"></a>**the 'Resource Data'
    hkMemoryResourceContainer tree is load-bearing, NOT boilerplate**
    (2026-08-07, corpse-never-simulates root cause). Vanilla creature
    skeleton.hkx ships one `hkMemoryResourceContainer` **per ragdoll part**,
    named after the part, nested along the ragdoll parent tree, each holding
    two `hkMemoryResourceHandle`s — `hkRigidBody` → the part's `hkpRigidBody`
    and `hkpShapeInfo` → an `hkpShapeInfo` that names the part and carries
    its bind world transform (dog: 22 containers + 44 handles + 22
    hkpShapeInfo, under one empty-name root). We shipped a single **empty**
    root container. That was the last structural delta from vanilla and it
    is exactly the registry the engine's ragdoll-build path walks to
    resolve part-name → rigid body when it assembles the `bhkRagdoll`
    template from the graph's `hkaRagdollInstance`. With the tree empty the
    template comes back with no bodies, so `AddRagdollToWorld` never adds
    anything: the corpse plays its death/idle pose forever, is not affected
    by gravity, cannot be havok-grabbed, and its collision stays at the
    single base capsule (why a one-piece creature like the rat looked
    "fine-but-fixed" while a multi-part goblin/mountain-lion had only its
    torso selectable and sank through the floor). `_emit_ragdoll_objects`
    now builds the full per-part tree; `hkpShapeInfo`/`hkMemoryResourceHandle`
    signatures added. The engine confirms the shape: the graph's
    `Fully Ragdoll` state and its `FullyRagdollPose` clip must ALSO be
    registered in animationdata/animationsetdata (an unregistered creature
    clip never binds), and the character.hkx must carry the
    `m_worldFromModelFeedbackGain` driver properties — but none of those
    matter until the resource tree gives the ragdoll bodies to add.
- `tes5_import/creature_races.py` — Phase 0f: generated RACE/ARMA/ARMO per
  unique (creature folder, NIFZ body set), layouts mirrored from real
  Skyrim.esm DogRace/SkinDog/NakedDogAA dumps; ATKE = the generated
  `attackStart_TES4_*` events; `convert_CREA` RNAM → the generated race
  (`resolve_creature_race` aliasing kept only as fallback). NPC_ humanoids
  keep the Skyrim race override system.
- Death: Oblivion creatures ship NO death animations — death IS the
  ragdoll. The generated graph clones BOTH vanilla dogbehavior death states,
  and the split is load-bearing:
  - **AnimateToRagdoll** (vanilla state 3, entered on DeathAnimation): its
    enterNotifyEvents raises **`AddRagdollToWorld`** — the ONLY raiser in
    the entire pipeline. **The engine does NOT add the ragdoll by itself on
    the normal kill path** (2026-08-08 root cause: a "simplification" that
    dropped this state left every corpse with no ragdoll while Fully
    Ragdoll still removed the character controller — so corpses had NO
    collision at all: they fell through the floor, no part could be
    activated/selected, and the pose source played forever; multi-part
    goblins/lions made it obvious, the rat's standing blend-body ghost was
    close enough to its tiny body to look "relatively fine"). Modifiers:
    `KeyframeFullRagdoll` (all ragdoll bones — the freshly added bodies
    stay keyframed to the pose), `DriveRagdollRB`, and the
    `BSRagdollContactListenerModifier` (floor contact → `Ragdoll`); feet
    touch the ground immediately, so the corpse releases into the limp
    state right away.
  - **Fully Ragdoll** (vanilla state 4, entered on Ragdoll/RagdollInstant —
    fired by the contact listener or directly by the engine when IT already
    ragdolled the actor: killmoves, paralysis): event-driven `FullRagdoll`
    wrapper over `PoweredRagdoll No Matching` (maxForce 0,
    `WORLD_FROM_MODEL_MODE_RAGDOLL`), notifying
    `RemoveCharacterControllerFromWorld`.
  - Both states' pose source is the `FullyRagdollPose` clip,
    **MODE_SINGLE_PLAY at playbackSpeed 1.0** in both the graph and its
    animationdata entry — the exact vanilla death-state clip semantics
    (dogbehavior state 3 plays Death.hkx single-play; state 4's pose
    sources are single-play getup clips). A single play holds its last
    frame, so the corpse does not keep breathing/wagging (the 2026-08-08
    report against the LOOPING idle). A `playbackSpeed 0` MODE_LOOPING
    freeze was tried in between; **no vanilla file anywhere ships
    playbackSpeed 0** (grep of every decompiled canine behavior), so it was
    reverted to the vanilla contract the same day.
  - **The `Ragdoll` release is a CLIP TRIGGER** (2026-08-08, frozen-corpse
    root cause #2 — found immediately after the index fix landed: corpses
    stopped teleporting but stayed rigid in their death pose). Vanilla
    dogbehavior's Death clip (#0119 → triggers #0118) carries exactly one
    trigger: event 81 `Ragdoll`, `relativeToEndOfClip=true`; the wolf's
    animationdata Death block fires `Ragdoll:0.267` absolute. That event —
    not the `BSRagdollContactListenerModifier`, which never fired for our
    keyframed bodies — is what moves AnimateToRagdoll → Fully Ragdoll and
    releases the keyframed bodies into the limp powered ragdoll. Our pose
    source is a held idle rather than an authored dying animation, so the
    trigger uses the wolf's early absolute timing
    (`_RAGDOLL_RELEASE_T = 0.266667`, 8 frames — enough for the engine to
    process the `AddRagdollToWorld` raised on state entry), emitted in
    BOTH the graph's `hkbClipTriggerArray` and the animationdata block
    (clip_meta `events`).
  The `DeathWait` IDLE tree (`creature_idles.py`) routes the engine's kill
  flow into DeathAnimation (conditioned) or Ragdoll (fall-through when the
  actor is already ragdolled). The ragdoll add also requires the
  skeleton.hkx Resource Data tree to list the parts (see [the resource-tree
  contract](#resource-data-tree)).

Remaining refinements: specialidle/IDLE wiring (Step 7), foot IK / look-at /
speed-blended gait states, per-creature SNDR sound sets + ARMA footstep
SNDD, per-creature BPTD (GNAM currently points at the vanilla canine body
part data), equip/unequip weapon states, in-game validation pass.

### Step 0 — Groundwork
0.1 **DONE** — CREA export emits `NIFZ[i]`/`NIFZCount` + `KFFZ[i]`/`KFFZCount`
    (`tes4_export/record_types/actors.py`).
0.2 Fix `tes4_nif_analyzer.py` `bhkSimpleShapePhantom` crash. (open)
0.3 Extract `Update.bsa` over `references/Skyrim Animations/` (BSArch) for fixed vanilla
    animation data. (open — reference-only concern)

<a id="ragdoll-mass-and-friction"></a>
### ★★ Corpses "weigh a million pounds" + 4 creatures never fall over (2026-08-08)

Two independent defects, both fixed after the
[`lockTranslation` teleport fix](#ragdoll-root-locktranslation) landed.

#### 0. ⚠️ THE TRAP: mass/friction live in BOTH skeleton.nif AND skeleton.hkx

**A creature's ragdoll bodies are described TWICE** — as `bhkRigidBody` blend
bodies in `skeleton.nif` (converted by `collision.py`) and as `hkpRigidBody` in
`skeleton.hkx` (built by `hkx_ragdoll.py`). Vanilla ships **identical** values
in both (dog: mass total 74.00 either side; every constraint friction 0.0).

Both fixes below were first applied ONLY to `hkx_ragdoll.py` and **the in-game
symptoms did not change at all**, because the engine weighs the NIF blend
bodies: they still carried Oblivion's raw mass (dog total 262, per-body 25-50)
and raw `max_friction` (10.0/12.0) while the hkx said 37.4 / 0.0. The two
representations disagreed and the heavy, high-friction one won.

**Any change to ragdoll mass, friction, inertia or motion type MUST be made in
both files, or they desync.** Verify with: our output nif total == our output
hkx total, and both in vanilla's range for a comparable creature.

#### 1. Mass was never unit-converted (`_OB_MASS_DIV`)

**Symptom:** dead creatures are nearly immovable — havok-grabbing a limb moves
it only slightly, on EVERY ragdoll.

**Cause:** `extract_ragdoll` scaled every LENGTH by `_OB_TO_GAME` (7) but
carried Oblivion's authored **mass** through untouched. Oblivion tunes mass
against Oblivion-scale lengths, and Havok's rotational inertia goes as
`mass * length^2`, so unconverted mass inflates resistance-to-rotation by 49x
versus what the animators tuned.

**Fix:** divide mass by 7 (`_OB_MASS_DIV = _OB_TO_GAME`). Inertia is computed
from mass in `_capsule_inertia`, so it follows automatically. Matched-pair
landing (total mass, ours vs closest vanilla creature): dog 262 -> 37.4 vs wolf
29; rat 271 -> 38.7 vs skeever 60; lion 501 -> 71.6 vs sabrecat 245; minotaur
1270 -> 181 vs giant 510. Was 1.3-9x heavy, now 0.1-1.3x.

A single divisor is deliberate — vanilla per-body masses are hand-authored
round numbers with **no** volume/density law (vanilla dog's own density varies
**46x** across its bodies; totals span wolf 29 -> dragon 4852), so there is
nothing physical to derive, only the unit scale. It also preserves each rig's
RELATIVE proportions, so heavy creatures stay heavy (stormatronach 1617 vs
goblin 25.7) — a Skyrim dragon is immovable and should be.

#### 2. ★ THE REAL "never falls over" CAUSE: the NIF ragdoll tree had ORPHAN bodies

**Symptom:** skeleton, shambles, mehrunesdagon and stormatronach never fall
over on death (user-confirmed 4/4); every other creature does.

**Root cause:** vanilla creature skeleton.nifs ship exactly **`bodies - 1`**
constraints — every body except the ragdoll root is constrained, no orphans
(dog 22/21, wolf 22/21, sabrecat 28/27, skeever 21/20). Oblivion leaves some
bodies unconstrained (its animators wanted them as collision, not joints). A
`bhkRigidBody` with NO constraint is **not part of the ragdoll's constraint
island**, so the chain through it cannot collapse and the corpse stays
standing. `hkx_ragdoll` already synthesized joints for these on the
skeleton.**hkx** side — but nothing closed the tree in the NIF, and the engine
reads the NIF (see the two-file trap above).

Census of our output before the fix — matched the reports 4/4 with zero false
positives:

| creature | bodies | constraints | missing | result |
|---|---|---|---|---|
| stormatronach | 70 | 16 | **53** | never fell |
| mehrunesdagon | 23 | 0 | **22** | never fell |
| shambles | 17 | 13 | **3** | never fell |
| skeleton | 17 | 13 | **3** | never fell |
| all 39 others | — | — | **0** | all fell |

**Fix:** `collision.add_missing_creature_constraints(data, root)`, called from
`nif_converter` after `scale_constraint_pivots` (the synthesized pivots are
built from body `center` values already in Skyrim Havok units). Each orphan
body gets a `bhkRagdollConstraint` to its nearest body-carrying ancestor, on
the vanilla atronach rock template (cone 50 deg, plane +-90, twist +-5,
friction 0) with an orthonormal twist/plane/motor basis. After: 43/43 creatures
complete, one orphan each (the ragdoll root, which vanilla also leaves bare).

#### 3. `_SYNTH_FRICTION` (contributing, not the cause)

**Symptom:** skeleton, shambles, mehrunesdagon and stormatronach never fall
over on death (user-confirmed 4/4); everything else does.

**Cause:** those are **exactly** the 4 creatures whose Oblivion rig ships
bodies with NO authored constraint (skeleton/shambles 3 each on `Head` + both
`UpperArm`; mehrunesdagon 22 of 23 — its whole spine and both legs;
stormatronach 53, its rock shell). Every other creature has exactly one
unconstrained body — the ragdoll ROOT, which needs no joint. Our planner
attaches unconstrained bodies with the atronach rock-joint template, which
carried `_SYNTH_FRICTION = 10.0`; `maxFrictionTorque` is an angular friction
torque, so a joint with 10 resists rotation hard enough that the chain through
it cannot collapse under gravity. **Vanilla creature ragdolls are friction 0
across the board (dog census: 42/42 `maxFrictionTorque` = 0.000000).**

The tell was the storm atronach: its ROCKS (the synthetic-jointed bodies)
tumbled correctly while its BODY stayed rigid, and its mainline spine/leg
joints are authored at friction 0 — the frozen links were the ones our
template gave friction to.

**Fix, BOTH sides:** `hkx_ragdoll._SYNTH_FRICTION = 0.0`, and in
`collision.py` the creature blend joints are now clamped to **0.0** instead of
being exempted from the clamp. The old comment there claimed "vanilla
skeleton.nif joints mix 10.0/0.5/0.01 so keep the authored value" — measurably
wrong: a census of the vanilla dog/wolf/sabrecat/skeever creature skeleton.nifs
is **89/89 constraints at exactly 0.000000**. Rock-joint LIMITS (cone 50,
plane +-90, twist +-5) stay: a sane vanilla-derived default for a joint
Oblivion never authored.

**Dead ends on the fall-over hunt — do not re-chase.** Nothing on the
geometry/keyframe side separates fallers from non-fallers: skeleton and zombie
have IDENTICAL free-at-death sets (`Head`, `L Hand`, `R Hand`) and 17 parts
each, yet zombie falls. Also falsified: joint-limit tightness (zombie falls
with a 1-5 deg spine vs skeleton's 15), keyframe percentages (80-83% in both
groups), mass ratios, SPHERE_INERTIA counts (boar falls at 25%, horse did not
at 23%), "a free body must be low enough to touch the floor" (10/18
mismatches), and synthetic joints blocking the load path (stormatronach's
mainline legs are unblocked). The horse was ALSO a false lead — reported
not-falling once, then confirmed falling on a retest, so its uniform 44-55
mass spread is NOT a defect.

<a id="ragdoll-root-locktranslation"></a>
### ★★★★ THE TELEPORT-ON-DEATH ROOT CAUSE (2026-08-08): `lockTranslation` on the ragdoll root's bone

**Symptom:** on death the corpse teleports — sideways, upward, or into the
ground — by a fixed distance, then settles (sometimes hovering).

**Root cause:** `lockTranslation` on an anim bone tells the engine "this
bone's translation never changes, use the reference pose". The ragdoll's ROOT
body drives its mapped anim bone's **translation** (that is how a corpse moves
as one rigid unit), so a LOCKED root bone means the translation is discarded
and snapped back to bind — displacing the corpse by exactly that bone's offset
the frame physics takes over. Vanilla ships `Canine_COM`, the dog ragdoll's
root bone, with `lockTranslation=false` for exactly this reason (bone 0
`NPC Root [Root]` is unlocked too).

`UNLOCKED_BONES` only listed `Bip01` / `Bip01 NonAccum`, so any rig whose
ragdoll roots at `Bip01 Spine0` or `Bip01 Pelvis` shipped a **locked** root.

**In-game census — 15/15 match with the offset, user-tested:**

| ragdoll root bone | lock | creatures | result |
|---|---|---|---|
| `Bip01 NonAccum` | false | scamp, rat, zombie, minotaur, skeleton | **no teleport** |
| `Bip01 Pelvis` | true | spriggan (0.29), lich (0.72) | teleports, small |
| `Bip01 Spine0` | true | mudcrab 10.3, boar 45, dog 55.8, goblin 66, lion 67, troll 74.8, deer 78, horse 106 | teleports by that exact distance |

**Fix (`hkx_skeleton.build_skeleton_xml`):** extract the ragdoll BEFORE
emitting the anim skeleton, then add `bones[parts[0].anim_index].name` to the
unlocked set. 16 creatures change; the 26 others (including every
confirmed-working one) are byte-identical.

**Dead ends ruled out on the way — do not re-chase:** the ragdoll→anim mapper
(root pair is `A0 → B<own bone>` with identity `aFromBTransform` on ours AND
vanilla — correct everywhere, working and broken alike); the ragdoll
`referencePose` root translation (making it actor-root-relative instead of
world measured as a pure NO-OP, since the broken rigs' `NPC Root` is already at
the origin); root-hub capsule size; mass/inertia scale; and "the ragdoll must
be rooted at anim bone 1" (below).

<a id="ragdoll-root-bone1-dead-end"></a>
### ✗ DEAD END (2026-08-08): "the ragdoll must be rooted at ANIM BONE 1"

**Symptoms:** on death the corpse teleports to a different frame of reference,
falls through the floor or floats above it, its limbs stay rigid, and the
PRN-attached parts (mountain-lion head) lose pick/activation geometry even
though the *standing* creature was fully selectable.

**What isolated it:** the user reported that the **rat** and the **scamp**
ragdoll correctly while the mountain lion and goblin do not. Nothing about
size, mass, capsule volume or root-hub proportion separates those two groups —
but this does:

| creature | ragdoll root part | anim bone | result |
|---|---|---|---|
| rat, scamp (+22 more) | `Bip01 NonAccum` | **1** | **correct** |
| mountainlion, goblin, dog, bear, boar, deer, horse, imp, mudcrab, sheep, troll | `Bip01 Spine0` | **2** | broken |
| lich, spriggan, mehrunesdagon | `Bip01 Pelvis` | 2 | broken |
| landdreugh / willothewisp | `Spine01` / `ContainerGoo01` | 16 / 23 | broken |

**Vanilla contract** (dog `skeleton.hkx`, mapper `#0121`): the ragdoll
skeleton's root bone `Ragdoll_Canine_COM` maps to **anim bone 1**
(`Canine_COM`, the immediate child of `NPC Root [Root]`) with an
`aFromBTransform` of identity. The ragdoll root is ALWAYS the child of the
actor root — and that is precisely the premise our mappers rely on when they
emit identity `aFromBTransform` for every pair.

`extract_ragdoll` roots the tree at whichever authored body has no outgoing
constraint. On Oblivion rigs that is often `Spine0`, up at z=50–66. The
ragdoll skeleton's root then sits at the *spine* while the engine composes the
ragdoll pose relative to the ACTOR root, so the whole ragdoll is displaced by
the `NPC Root → Spine0` vector the instant physics takes over — lion
(0,−40.5,+53.5), goblin (0,−6.2,+65.8). That is the teleport; the displaced
capsules then sit below or above the floor (clip / float) and no longer cover
the rendered body, so the attached parts have no pick geometry. The rat and
scamp escape it only because their authored root already *is* bone 1.

**REVERTED — the whole theory was wrong.** Vanilla's bone 1 `Canine_COM` is at
world z=59.3 (up in the body); the working scamp/rat have `NonAccum` as a real
trunk bone, while lion/boar/dog have `NonAccum` at the FEET (0,0,0) with
`Spine0` as the trunk. So "bone index 1" was a coincidence of where `NonAccum`
sits, and re-targeting moved the trunk body's FRAME to the ground — corpses
stopped ragdolling and stood upright. The real invariant is
[`lockTranslation`](#ragdoll-root-locktranslation). Three variants were built
and all three broke it:

**(historical) Fix (`_ensure_root_at_bone1`): RE-TARGET the existing root body
onto anim bone 1 — do NOT add a body.** The part count, the constraint tree and every
authored mass stay exactly as Oblivion wrote them; only the root part's
`anim_index` moves from the trunk bone to bone 1, with its capsule and COM
re-expressed in bone 1's frame so the collision stays put in world space. Each
child constraint's PARENT-side data (`piv_b`, `rows_b`) is mapped through the
same delta; child-side data is untouched.

**Two wrong versions of this fix shipped first, both user-confirmed broken —
do not reintroduce either:**

1. **Insert a hub part whose capsule spans bone 1 → old root.** That is a
   67-unit-long, r=16.8 vertical bar running through the whole creature. It
   enclosed the body, so corpses could not fall over and **just stood
   upright**. Vanilla `Canine_COM` is r=15.35 with a segment of only 2.91 — a
   compact hub *at* the bone, never a bridge to the ground origin.
2. **Insert a compact hub at the trunk.** Still wrong: the hub came out an
   exact DUPLICATE of `Spine0` (same radius, length, mass, position), doubling
   the trunk mass and adding a synthetic joint the authored rig never had.
   Vanilla rigs have ONE trunk body — the rat/scamp `NonAccum` root is a
   genuine authored body with a separate `Spine` below it, not a twin.

After the fix all 40 ragdoll-bearing creatures root at anim bone 1, every part
count is byte-identical to before the change, and the emitted mapper starts
`A0 → B1` matching vanilla. (`ghost` and `wraith` have no extractable ragdoll
at all — `parts is None` before and after.)

Pre-existing pivot mismatches, NOT caused by this (verified byte-identical
with the fix disabled): lich `Bip01 Head` 2.93 units; landdreugh's 16 leg
constraints 0.2–0.9 units.

Pre-existing, NOT caused by this change: the lich's authored `Bip01 Head`
joint has a 2.93-unit pivot mismatch (byte-identical with the fix disabled).

<a id="keyframe-bone-sets"></a>
### ★★ Contributing cause (2026-08-08): every ragdoll bone was keyframed

**Symptoms (all four from one cause):** on death the corpse (1) teleports to a
different frame of reference, (2) has rigid limbs that never flop — exactly
like the rigid `prisoncellchains01.nif`, (3) falls through the floor or floats
above it, and (4) loses pick/activation geometry on the PRN-attached parts
(mountain-lion head), even though the *standing* creature was fully
selectable.

**Root cause:** the three `hkbBoneIndexArray`s in the generated behavior graph
were all `list(range(ragdoll['parts']))` — **every** ragdoll bone.
`hkbKeyframeBonesModifier` **PINS** each listed ragdoll body to the animation
pose, and a pinned body is immovable by the solver *and generates no
contacts*. With all of them pinned:

- nothing can flop (rigid limbs);
- `BSRagdollContactListenerModifier` never fires, because contact requires a
  dynamic body — so the `Ragdoll` release never comes from contact (the
  earlier "the contact listener never fired for our keyframed bodies" note was
  this bug, worked around with a clip trigger instead of fixed);
- `WORLD_FROM_MODEL_MODE_RAGDOLL` derives worldFromModel from a root body that
  is itself keyframed to the anim pose — a circular definition that resolves
  to the raw bind transform, snapping the corpse (teleport);
- the pinned bodies hold the bind pose while the character controller is
  removed, so the pick geometry sits where the bind pose is, not where the
  corpse is drawn — worst at the extremities, i.e. the attached parts.

**Vanilla dogbehavior census (22-body dog ragdoll) — none is `range(n)`:**

| array | modifier | count | contents |
|---|---|---|---|
| `#0122` | `KeyframeFullRagdoll` (death state 3) | **18/22** | all EXCEPT the deepest limb leaves (LBackLegToe, RFrontLeg2, L/RFrontLegPalm) |
| `#0134` | `KeyframeLowerBody` (LIVE root state) | **17/22** | all EXCEPT the tail chain, Neck2 and Head |
| `#0120` | `CollisionListener.bones` | **8/22** | limb ROOTS + Spine1/Spine3/Neck2/Head — no toe/palm tips, no tail |
| `#0126` | `DriveRagdollRB.bones` | **0** | empty == all bodies |

The splits are load-bearing: at death the limb *tips* are already free so
gravity gets a purchase; while alive the tail/neck/head hang free so a living
creature's tail wags (vanilla's live modifier is literally named
`KeyframeLowerBody`, not `KeyframeAllBones`).

**Fix:** `hkx_ragdoll._keyframe_bone_sets(parts)` derives all three
generically from ragdoll tree topology + bone names and returns them via
`ragdoll_info()` as `keyframe_full` / `keyframe_lower` / `contact_bones`;
`hkx_behavior` consumes those instead of `range(n)`. Measured against vanilla
(dog 81%/77%/36%), our output lands at 80–87% / 55–88% / 38–66%. Bone-name
matching is by WHOLE WORD (`_bone_words`), never substring — a plain
`'ear' in name` also matches "For**ear**m" and set every forearm loose.

Regression check: no `hkbBoneIndexArray` feeding a keyframe modifier or the
contact listener may ever equal `range(parts)`.

<a id="root-bone-not-identity"></a>
#### Measured, NOT the ragdoll bug, still NOT fixed: 33/42 non-identity `NPC Root [Root]`

**This is NOT the cause of the broken corpses** — the rat is in the
non-identity group and is one of the two creatures that ragdoll *correctly*,
while the mountain lion and goblin are identity-rooted and broken. The real
cause is [the ragdoll root bone](#ragdoll-root-at-bone1). Kept here as a
separate real deviation from the engine contract, unfixed.

Vanilla requires the anim skeleton's bone 0 `NPC Root [Root]` to be **exactly
identity** (dog referencePose[0] = t(0,0,0) q(0,0,0,1); it is the node the
engine equates with the reference's world position, at the actor's ground
origin). Oblivion's `Bip01` root carries an **arbitrary authored bind
transform**, and `BONE_RENAMES` renames it in place without normalising:

| creature | `Bip01` bind translation | rotation |
|---|---|---|
| bear, boar, deer, dog, goblin, mountainlion, slaughterfish, troll, willothewisp (9) | (0,0,0) | identity ✅ |
| daedroth | (0,0,**101.96**) | 90° |
| frostatronach | (0,−6.42,**89.14**) | 90° |
| mehrunesdagon | (0,0,**691.92**) | 90° |
| rat | (0,**−20.01**,**27.29**) | ~90° |
| …29 more | z 13.6–105.2 | mostly 90° |

Census: `for p in export/<plugin>/meshes/creatures/*/skeleton.nif:
load_skeleton_bones(p)[0]`. The rat is the extreme case — its ragdoll ROOT
part is `Bip01 NonAccum` (the motion-accumulation node) rather than a real
trunk bone like every other creature's `Bip01 Spine0`, and its whole skeleton
sits at (0,−20,+27.3) instead of the origin.

This is a real deviation from the engine contract and a plausible second
contributor to death-frame misplacement on those 33 creatures. It was NOT
changed together with the keyframe fix: normalising the root means
re-expressing every bone transform, every animation track and every skinned
mesh bind, which would touch the 9 creatures that currently work. Do it as its
own scoped pass with its own in-game verification — do not bundle it.

<a id="ragdoll-inertia-root-cause"></a>
### THE RIGID-RAGDOLL THEORY (2026-08-08): ill-conditioned inertia tensors

(Superseded as the root cause by the keyframe-bone-set bug above, but the
capsule-derived tensor is kept: it is closer to vanilla than Oblivion's
authored diagonals either way.)

Converted creatures died with a **rigid** ragdoll (limbs never flopped) that
**teleported, fell through the floor, and lost collision on its attached
parts** — all on the frame physics takes over, while the live creature was
perfect. Four sessions of static audits (bind pose, mappers, constraints,
friction, filters, motion system, body transforms) all found the ragdoll
byte-equivalent to vanilla, because they never compared the **inertia
tensor** — the one field that was wrong.

`extract_ragdoll` carried Oblivion's authored inertia diagonals through
verbatim (×49). Oblivion's values are wildly ill-conditioned: our shipped
tensors hit **32×** principal-axis anisotropy on the forearm, 20× on the
thigh, 15× on spine2 — against vanilla Skyrim's **worst creature body at
6.6×**. A badly ill-conditioned inertia on a constrained ragdoll body makes
Havok's joint solver **diverge**: the limp ragdoll stays rigid, and the
destabilised simulation island snaps to a fallback transform and drops out of
collision. Two vanilla-parity gaps compounded it — ours emitted every body
`MOTION_BOX_INERTIA` (vanilla uses `MOTION_SPHERE_INERTIA` with isotropic
inertia on the ~4 round bodies: the COM/torso hub and the tiny leg-tip caps),
and the anisotropy was unbounded.

**Fix (`asset_convert/hkx_ragdoll.py`):**
- `_capsule_inertia(shape, mass)` computes each body's tensor analytically
  from its capsule (solid cylinder + two hemisphere caps) instead of trusting
  Oblivion's diagonal.
- The principal-axis ratio is clamped to `MAX_ANISO = 6.5` (vanilla's
  ceiling) — even the *exact* capsule tensor of a long thin limb is ~31×, so
  the ellipsoid is fattened while keeping its orientation.
- `_add_rigid_body` emits `MOTION_SPHERE_INERTIA` + isotropic (min-axis)
  inertia for a "round" body (`seg_len < 0.8·radius`), else `BOX_INERTIA`.
  Dog result: 22 BOX + 4 SPHERE (vanilla's exact split), worst anisotropy
  6.5 (was 32).

Diagnostic: decompile a converted `skeleton.hkx` and read each
`hkpRigidBody`'s `inertiaAndMassInv` — any body whose max/min of the first
three components exceeds ~7 is the regression.

<a id="ragdoll-ab-bisect"></a>
### Death-ragdoll A/B bisect ESPs (2026-08-08 — superseded by the inertia fix above; kept for method)

The animationdata index fix and the `Ragdoll` release trigger below are real
contract fixes but did NOT fix the in-game ragdoll (user-confirmed): with the
release firing, corpses teleport and fall through the floor; with it absent
they freeze standing (stable, controller still present). **The failure is
therefore isolated to the ragdoll handover itself** — whatever
`AddRagdollToWorld` + `Fully Ragdoll` produce is wrong at runtime even though
skeleton.hkx / skeleton.nif / behavior / RACE all verify vanilla-contract
clean offline (do NOT re-audit them; three sessions have). Three test ESPs
exist in `output/` targeting `TES4SEUshnarDogRace` (dog rig — spawn via
console, kill, observe the corpse):

| ESP | Swaps in | Corpse ragdolls properly ⇒ | Still broken ⇒ |
|---|---|---|---|
| `TES4AB_AllVanilla.esp` | vanilla canine behavior+skeleton+body | records/cache exonerated; fault in generated assets | fault in records/cache side (or engine needs something per-race we lack) |
| `TES4AB_VanillaBehavior.esp` | vanilla behavior project only (our NIFs) | our project stack (hkx/cache) at fault | our skeleton.nif/body side at fault |
| `TES4AB_VanillaNifs.esp` | vanilla skeleton.nif+body only (our project) | our NIF side at fault | our project stack at fault |

(Judge ONLY death/ragdoll behavior; live animation will look wrong in the
mixed arms — bone names don't match across rigs.)

<a id="animdata-index-contract"></a>
### THE 2026-08-08 DEAD-RAGDOLL ROOT CAUSE: animationdata clip indices were clip ordinals, not file indices

**Symptom (persistent across ~a dozen graph/physics-side fixes that changed
nothing):** on death the corpse teleported up/sideways, limbs stayed rigid,
and the body fell through the floor.

**Root cause (`animation_data.py project_block_lines`):** the second line of
an animationdata clip block is the index into the character hkx's
`hkbCharacterStringData.animationNames` — the **deduplicated animation FILE
list** — but the emitter wrote `enumerate(manifest['clips'])` ordinals.
Clips and files stay aligned only until the first clip that SHARES a file
(CombatStance reuses idle.hkx), after which every index is off by one, and
every clip past the file count is **out of range**: all the parametric gait
children and `FullyRagdollPose`, the death-state pose source. An
out-of-range clip generator never binds, so the death states ran with a
dead pose source and the whole ragdoll handoff collapsed — regardless of
how correct the behavior graph, skeleton.hkx, skeleton.nif, RACE records,
character file and 64-bit serialization were (each was verified
vanilla-equal during the 2026-08-08 audit; the corruption lived only in the
cache text). Live creatures LOOKED fine because the first six clips (idle +
locomotion, one file each) were correctly numbered; the desync quietly made
each attack play the NEXT attack's file and the run gait play the
aware-vocal clip. `motion_block_lines` had the same bug — root-motion
blocks are keyed by animation index, one per FILE, not one per clip.

Fixed by `_anim_file_index()` (mirrors the character emitter's
`dict.fromkeys` dedupe exactly). **Audit with
`python tools/animdata_index_check.py`** after touching `animation_data.py`,
`clip_meta` composition, or the character animation list; the older
`animcache_validate.py` checks only the grammar and passed this corruption.

Note on the earlier "Resource Data tree is load-bearing" claim (2026-08-07,
above): ck-cmd's `build_skeleton_from_ragdoll` — the tool Skyblivion's own
working creatures come from — ships an **empty** `hkMemoryResourceContainer`
as Resource Data. The per-part tree matches vanilla and is kept, but it
cannot have been the ragdoll gate; the animationdata index corruption was
present under every experiment of that period.

<a id="animdata-plugin-collision"></a>
### The SAME out-of-range symptom from PLUGIN COLLISION (2026-08-10)

`animdata_index_check` can report out-of-range indices even when
`_anim_file_index()` is perfectly correct, because the block and the character
hkx are chosen by **different** mechanisms:

- Every plugin deploys its creatures LOOSE to the same
  `meshes\actors\tes4\<folder>\` path, so exactly one
  `tes4<folder>character.hkx` survives in Data — the one from whichever
  plugin the user actually installs.
- The clip block describing it comes from whichever plugin merged the shared
  `animationdatasinglefile.txt` LAST (children write through to their
  master's copy, `_shared_singlefile_dir`).

The same creature converts to a different clip/file count per plugin, because
each plugin's CREA records reference a different subset of animations.
Morrowind_ob's clannfear is 27 clips / 21 files; Oblivion's is 23 / 17.
Building Morrowind_ob last put its 21-file block on top of Oblivion's
17-file hkx, so `Equip_H2H`, `Unequip_H2H`, the `MoveForwardRun*` gaits and
`FullyRagdollPose` were all out of range and never bound. Measured on the
2026-08-10 output: 6 projects mismatched (clannfear, daedroth, flameatronach,
mehrunesdagon, scamp, slaughterfish); `animdata_index_check` flagged 13
out-of-range indices across clannfear + flameatronach.

The 2026-08-10 fix picked a "winner" block per folder (`_block_outranks`).
That was the wrong model and is **gone** — see the next section.

### ★★★ THE ROOT CAUSE OF "SCAMPS NEVER CAST / CAN'T MELEE": plugin path collision (2026-08-23)

Creature identity was the bare leaf folder name in ONE shared
`meshes\actors\tes4\<folder>\` tree. Oblivion's `scamp` and Morrowind_ob's
`scamp` (the Morrowind scamp — 75 bones, 2 attack clips
`handtohandattackrighta/b`, no cast clips, its own `0scampSmoan` sound) are
**different creatures** that wrote the same path, the same
`tes4scampproject` name and the same `tes4scampbehavior.hkx`. Data can hold
one; whichever plugin was deployed last won for *every* plugin.

Proven from the running game, not inferred: with the bridge, hooking
`hkbStateMachine::handleEvent` showed `sae Spell_FireForget_LH` on a Stunted
Scamp (Oblivion.esm actor) reached **zero** state machines while
`equipStart_H2H` reached five — the event died at
`BShkbAnimationGraph::SendEvent`'s name→id lookup. Dumping that map live
(`BShkbHkxDB::ProjectDBData` +0xc8, keyed by interned BSFixedString) gave
52 names: no `Spell_FireForget_LH`, no `Magic_Pre_Out`, none of Oblivion's
seven `attackStart_TES4_*` events, but `attackStart_TES4_handtohandattackrighta`
and `SoundPlay.TES4_0scampSmoan_SNDR` — Morrowind_ob's graph, loaded for an
Oblivion actor. So the engine was sending cast and attack events the loaded
graph simply did not have: `IsCasting=1` forever, no animation, no melee.

**Fix — one project per plugin, like Skyrim itself** (`hkx_behavior.
project_layout`, `creature_pipeline.plugin_namespace`):

```
meshes\actors\tes4\<namespace>\<folder>\tes4<namespace>_<folder>project.hkx
                                        characters\tes4<namespace>_<folder>character.hkx
                                        behaviors\tes4<namespace>_<folder>behavior.hkx
animationdata\tes4<namespace>_<folder>project.txt       (and its setdata dir)
```

`namespace` = plugin file stem, lowercase `[a-z0-9_]` (`oblivion`,
`morrowind_ob`, `nehrim`, `dlcshiveringisles`). The manifest now carries
every path (`project_hkx`, `behavior_hkx`, `body_dir`, `skeleton_nif`) and
the import side reads them instead of rebuilding `Actors\TES4\<folder>`
strings — RACE MODL/ANAM, ARMA MOD2, BPTD, and the IDLE DNAM that the engine
matches creature idle roots by (the CK's "resolve root behavior name" is
`<project dir> + behaviorPath + behaviorFilenames[0]`, so DNAM must equal the
shipped behavior path exactly). Record EditorIDs and every FormID key stay
on the leaf name: **no FormID drift** (`test_formid_determinism` green).

The singlefile union is now keyed on the (unique) project name, so every
plugin's block registers and no winner is chosen. `convert_creatures` also
deletes any pre-namespace `actors\tes4\<folder>` tree it finds in its own
output, so a full-folder deploy cannot reintroduce the collision — but the
user's Data folder still holds the OLD flat `actors\tes4\<folder>` copies
from every plugin; those must be removed by hand once, or the game keeps
loading them for nothing (they are no longer referenced by any record).

The `IdleStop` root wildcard was fixed in the same pass (vanilla routes
`idleStop` only out of idle states; ours yanked any state — including a cast
— back to Default). Keep both.

<a id="rigid-part-bind"></a>
### "Attached parts have no hitbox / corpse falls through ground" — it was AddRagdollToWorld (2026-08-08)

**Symptom (in-game):** attached rigid parts (mountain-lion head, goblin
head/helmets/pauldrons — every Prn part NIF) had NO hitbox and corpses
clipped/fell through the ground; corpses kept playing their idles.  The rat
only *looked* right because its standing blend-body ghost roughly overlaps
its tiny body.

**Root cause: nothing raised `AddRagdollToWorld`** (see the Death bullet
above).  With no ragdoll in the world and the character controller removed
by Fully Ragdoll, a corpse has NO collision anywhere — so every region
"had no hitbox", and the most-visible regions (the big attached parts)
were reported first.  Two capsule-geometry "fixes" were built on the wrong
theory and both REVERTED the same day; the authored Oblivion capsules are
correct and ship unchanged:

1. `fit_ragdoll_capsules_to_mesh` wrote each part's **bone-local** mesh
   centroid into the blend body's `rb.translation` — but that field is the
   body's **BIND WORLD** position in BOTH engines (vanilla dog census:
   Canine_Head rb_t = (0, 49.6, 70.4)); capsule vertexA/B are BODY-local,
   and Oblivion's body frame == the bone frame (verified |R_body −
   R_node_world| = 0 on lion source + converted).  The fit teleported
   capsules to the model origin (shipped goblin: `Bip01 Head` rb_t z=0,
   `R UpperArm` z=−17.7 — underground).
2. `_enlarge_for_attachments` grew capsules to enclose the Prn part meshes
   (lion head r 3.8→12.5, goblin head r 7.2→22, neck spanning the
   pauldrons).  Coverage math was correct but the result was visibly
   oversized in NifSkope and pointless once the real cause was found —
   reverted; revisit only with in-game evidence that authored capsules are
   too thin to activate (they match what Oblivion shipped, and the
   "radius 0.5, 40× too small" claim was a units error — 0.54 ob-havok
   units × `_OB_TO_GAME`(7) = 3.8 game units).

**Frame contracts (the traps that made both wrong fixes easy to write):**
- Blend body `rb.translation`/`rotation` are BIND WORLD; capsule
  vertexA/B are BODY-local (== bone-local on these rigs).  Never write
  bone-local values into `rb.translation`.
- `extract_ragdoll` reads the SOURCE (Oblivion) skeleton.nif; skeleton.hkx
  is generated BEFORE nif conversion, so post-conversion NIF edits never
  reach the hkx.
- skeleton.hkx works in GAME units (vanilla dog capsule radii 3.4–5.7,
  not havok metres); the converted skeleton.nif bhk fields are
  game/69.9904.
- The Prn attachment verts are bone-local after
  `_bake_node_transforms_into_verts`; only the mountain lion has a
  whitespace bone name (`'Bip01 Spine0 '`, trailing space) and it is
  consistent across skeleton.nif / hkx / BPTD, so it binds.

### Step 1 — Creature manifest (plugin-agnostic inventory)
New tool `tools/creature_inventory.py`: for each CREA record (post-0.1 export), emit a
JSON manifest: skeleton path, NIFZ body parts, clip inventory classified by the engine
naming convention (locomotion / attacks / idles / specialidles / recoil-stagger /
equip-unequip / swim), per-clip metadata (duration, cycle type, text keys, whether root
motion is present on `Bip01 NonAccum`), and skeleton bone census. This manifest is the
single input that drives records (Step 2), meshes (Step 3), animations (Step 4), and
behavior generation (Step 5) — for ANY plugin.

### Step 2 — Records (tes5_import) — DONE (see creature_races.py; notes below)
Implemented as described, with these deltas: one RACE per unique (folder,
NIFZ set) rather than per record (dog vs wolf get separate races sharing one
project); multi-part bodies get one ARMA per part NIF (slot 32-Body for the
first, creature slots 40+ for the rest) instead of a merged body NIF; GNAM
reuses the vanilla canine BPTD; ARMA SNDD omitted for now.

2.1 **RACE per creature**: ANAM = `Actors\TES4\<creature>\Character Assets\skeleton.nif`
    (our converted skeleton, both genders), Behavior Graph MODL =
    `Actors\TES4\<creature>\<creature>project.hkx` (our generated project), MTNM =
    WALK/RUN1/SNEK/BLDO/SWIM with vanilla MOVT FormIDs (WKMV/RNMV), **ATKD/ATKE generated
    from the same manifest as the behavior graph** (event strings match by construction),
    BOD2, VTCK, size/stats from CREA (BNAM.BaseScale → height, DATA → health/damage).
    GNAM (BPTD body-part data): generate a minimal BPTD per creature (or omit initially —
    verify engine tolerance). Reference layout: `WolfRace` 0001320A in
    `references/Skyrim.esm/RACE.txt`.
2.2 **Skin chain**: ARMO (`Skin<Creature>`, non-playable 0x4, BOD2 Body, RNAM) + ARMA
    (`Naked<Creature>AA`, BODT, RNAM, MOD2 = `tes4\creatures\<x>\<body>.nif`, SNDD =
    nearest vanilla footstep set by creature size class). RACE.WNAM → the ARMO.
    Multi-part creatures (deer body+antlers+eyes): merge parts into one body NIF at mesh
    convert time (simpler records; parts share one skeleton).
2.3 **NPC_**: existing convert_CREA output + RNAM → the new race.
2.4 Sounds: CREA sound-type lists → SNDR sets later; silence is acceptable initially.

### Step 3 — Skeleton + body meshes (asset_convert) — DONE
Implemented as `nif_converter creature=True`: skeleton.nif → BSFadeNode +
BSX=198 with bhkBlendCollisionObject ragdoll KEPT and converted
(`collision.py::_convert_blend_collision` — flags 137, keyframed/fixed,
layer 8 BIPED, translation scaled not zeroed); body parts keep NiNode root +
plain NiSkinInstance with regenerated partitions; Prn-attached heads/eyes
get node transforms baked into verts + rigid plain-NiSkinInstance to the
original Oblivion bone. skeleton.hkx (3.3) includes the full ragdoll stage
via hkx_ragdoll.py. BSBound/BSInvMarker/SkeletonID extra data not emitted
(engine-optional). Original notes:

Because we keep the Oblivion skeleton, **no reskinning/retargeting is needed at all** —
bone names, weights, and bind matrices in body meshes stay valid. This deletes the
hardest humanoid-pipeline problem (rest-pose retarget) from the creature path entirely.

3.1 **skeleton.nif conversion** (new `asset_convert/creature_skeleton.py` or a
    nif_converter branch): version upgrade 20.0.0.4→20.2.0.7; root NiNode "Scene Root" →
    BSFadeNode; add extra data set (BSXFlags=198, BSBound from bone extents, BSInvMarker,
    BSBoneLODExtraData from the UPB `BSBoneLOD#` strings, `SkeletonID`); keep ALL bone
    names/transforms verbatim; per-bone `bhkBlendCollisionObject`/`bhkRigidBody` ragdoll →
    Skyrim bhk format via the existing `collision.py` machinery (constraint pivots ×0.1,
    ragdoll motor basis, malleable demotion — all already implemented for world objects);
    keep per-bone NiTransformController+bhkBlendController (vanilla Skyrim skeletons have
    them too).
3.2 **Body mesh conversion**: remove `'creatures'` from `nif_converter.SKIP_PATHS`; route
    `meshes/creatures/**` through a creature-body branch: **plain NiNode root**,
    NiTriStrips→NiTriShape, BSLightingShaderProperty (`tes4\` texture prefix), keep plain
    `NiSkinInstance`, rebuild NiSkinPartition, NO skin retarget. Ground truth:
    `references/Skyrim Meshes/meshes/actors/canine/character assets wolf/wolf.nif`.
3.3 **skeleton.hkx generation**: hkaSkeleton (bone names, parent indices, reference pose
    from the converted skeleton.nif) — emit as hkx XML → hkxcmd. Small, fixed-structure
    file; vanilla examples in `references/Skyrim Animations/meshes/actors/*/character
    assets/skeleton.hkx` (dump with hkxcmd to copy the exact object layout, incl. the
    hkaSkeleton + hkbCharacterStringData conventions).

### Step 4 — Animations: KF → Skyrim HKX
4.1 **B-spline decode** — the blocker. The KF corpus is dominated by
    `NiBSplineCompTransformInterpolator` (dog forward.kf: 43/45 bone tracks;
    `kf_animation_explorer.py:146` currently skips them). Decode: quantized-short control
    points in `NiBSplineData`, dequantized by offset/half-range, cubic B-spline eval over
    `NiBSplineBasisData`. Check PyFFI 2.2.3 for existing helpers
    (`get_times()/get_translations()/get_rotations()/get_scales()` — the niftools addon
    calls exactly this API); else port from niftools `nifgen` or NifSkope. Edge cases:
    no-basis-data interpolators (bowidle.kf) = static pose;
    `NiBSplineCompFloatInterpolator` (bone stretch) dropped; `-3.4e38` sentinel = rest
    pose (already handled).
4.2 New `asset_convert/kf_decode.py`: per KF emit uniform 30 fps sampled local transforms
    per target bone (NiStringPalette resolution as in kf_animation_explorer), text keys,
    cycle type, duration. **Root motion split**: the sampled `Bip01 NonAccum` (and root
    `Bip01`) translation/rotation is extracted into a root-motion curve (→ boundanims,
    Step 6) and removed from the in-hkx track (Skyrim clips are in-place).
4.3 **Write HKX** — IMPLEMENTED (`asset_convert/hkx_anim.py`, 2026-07-08): no bone
    retargeting needed (our own skeleton). Winning path after testing all three:
    tracks → pynifly's `_compress_all_blocks` spline compressor (vendored
    `external/pynifly_hkx/`) → hkaSplineCompressedAnimation as packfile XML
    (`hkx_xml.HkxPackfile`) → `hkxcmd convert -v:WIN32`. Validated: 0.0000u /
    0.0000° track error vs the decoded source (pynifly reader) AND clean reads by
    hkxcmd's real Havok deserializer (XML round-trip + EXPORTKF).
    Rejected paths, measured: (a) `hkxcmd CONVERTKF` — its compressor is broken-lossy
    (vanilla round-trips at median 7.4°/max 37.6° bone rotation error); kept as a
    debug tool in `kf_writer.py` (its Skyrim-format KF output opens in NifSkope).
    (b) pynifly's hand-rolled BINARY packfile writer — output crashes real Havok
    deserializers (unaligned allocations, layout quirks; even a rewritten vanilla
    file crashes hkxcmd). Its reader + compressor are used; its writer is not.
4.4 **Text keys → ANNOTATIONS INSIDE THE ANIMATION HKX** (root cause of silent
    creatures, CONFIRMED IN GAME 2026-08-07): the engine dispatches these
    events from **hkaAnnotationTrack entries embedded in the animation .hkx
    itself** — 58/74 vanilla wolf animations carry `SoundPlay.NPCWolfAttackA`,
    `FootFront`/`FootBack`, `weaponSwing`/`preHitFrame`/`HitFrame` as
    annotations in the binary; `idle_sitdpeck.hkx` carries
    `SoundPlay.NPCChickenPeck`. Writing the triggers ONLY into
    `animationdatasinglefile.txt` (+ declaring them in the graph event table)
    produced **zero audio**; the same events embedded as animation annotations
    produce correct audio. Worse, the old converter embedded Oblivion's raw
    text keys VERBATIM (`Sound: NPCDogGrowl`, `Enum: Left`) — meaningless to
    Skyrim. The translation (hkx_anim.parse_kf_events/event_annotations):
    `Sound: X` → `SoundPlay.TES4_<X>_SNDR`, gait `Enum: Left/Right/BackLeft/
    BackRight` → the foot_tags event (AUTHORED footfall times — use them),
    `Hit` → hit times. animationdata triggers are still written to mirror
    vanilla, but they are not the dispatch channel.

    The sound annotation MUST be `SoundPlay.<SNDR EditorID>` — a bare
    `SoundPlay` is measured and discarded by the engine before any lookup
    (handler `0x140565c90`, GOG build), so vanilla's bare entries are inert
    leftovers and must not be copied.

    **Channel split (no event may fire from two places):** SoundPlay + foot
    events live in the animation annotations; the weaponSwing/preHitFrame/
    HitFrame damage triple and end-events live in the GRAPH's
    hkbClipTriggerArray (proven live: attack states return to default through
    them). Embedding the triple in the animation too would double the damage
    window. Attack clips without an authored `Hit` key get one synthesized at
    40% duration so the triple always exists.

#### Footsteps are a RECORD chain **plus** a matching animation event (2026-08-07)

**Skyrim reads creature locomotion audio from the body ARMA — but the chain
only fires when the playing animation raises a footstep EVENT whose name
matches an FSTP.ANAM tag verbatim:**

```
ARMA.SNDD -> FSTS (per-gait footstep lists)
               -> FSTP (one per footstep tag)
                    -> IPDS (material -> impact table)
                         -> IPCT (one impact, carrying the sound)
                              -> SNDR
```

Oblivion keeps the same sounds in CREA **CSDT slots 0-3** (LeftFoot, RightFoot,
LBackFoot, RBackFoot). Those were converted to SNDRs but never wired to
anything, and every generated creature ARMA had **no SNDD at all** — measured
before the fix: 0 IPCT / 0 IPDS / 0 FSTP / 0 FSTS in the entire output, 63 of 63
creature ARMAs with `SNDD=NONE`.

Implemented in `tes5_import/creature_footsteps.py`, following the vanilla
`NPCWolfFootFrontWalk*` chain exactly:
- **IPCT** = EDID + DATA(24) + DODT(36) + SNAM→SNDR. No model; footstep impacts
  are sound-only.
- **IPDS** = one PNAM(8) per MATERIAL, all pointing at the single IPCT. Wolf
  lists **60 materials** mapped to one impact, so a creature sounds the same on
  every surface — `_FOOTSTEP_MATERIALS` reproduces that list verbatim.
- **FSTP** = DATA→IPDS + ANAM tag. **ANAM is the EVENT NAME, matched verbatim
  against what the animation fires** — vanilla `NPCWolfFootFrontWalkFootstep`
  has `ANAM=FootFront`. The first implementation invented tags
  (`TES4DogFoot1`) that no event ever names: every footstep silent even with
  a perfect chain. `creature_pipeline.foot_tags()` is now the SINGLE SOURCE
  for the tag names on both sides (quadruped = back-foot CSDT slot authored →
  `FootFront`/`FootBack` like vanilla wolf; biped → `FootLeft`/`FootRight`);
  one FSTP per tag, IPCT/IPDS deduped per distinct sound.
- **FSTS** = XCNT(20) counts in **walk, run, sprint, sneak, swim** order, but
  the DATA arrays are the **REVERSE**: swim, sneak, sprint, run, walk
  (`wbDefinitionsTES5.pas:7108`). Getting this backwards silently misassigns
  every gait.

Allocated **last** (like the creature VTYPs) so no existing generated FormID
shifts, then `patch_creature_footsteps` INSERTS `SNDD` into the already-packed
ARMAs and fixes the 24-byte header's dataSize — unlike VTCK there is no
placeholder to overwrite, because SNDD is a genuinely new subrecord.

Group order matters: `IPCT`/`IPDS` must precede `ARMA`, and `FSTP` must precede
`FSTS` (xEdit canonical order `... VTYP MATT IPCT IPDS ARMA ... FSTP FSTS ...`).

Result: 41 footstep sets, 222 creature ARMAs bound, 0 broken links across the
whole chain, 125 footstep audio files all present on disk.

#### The audio file the SNDR names (silent-creature root cause, 2026-08-05)

Non-voice (creature/effect) audio is **PCM `.wav` in both games** — vanilla's
`sound/fx/npc/bear/npc_bear_idlerooting_01.wav` is `RIFF/WAVE` with
`wFormatTag=0x1`, and the Oblivion source is the same, so it needs no
transcode. **Never re-encode it to `.xwm`.**

There is **no extension substitution in the engine**: the SNDR `ANAM` is
opened exactly as written. The only exe code touching the `.wav`/`.xwm`/`.fuz`
strings (`0x140512485`) is the `sound\` / `data\sound\` path-prefix helper.
Encoding these to `.xwm` while ANAM still said `.wav` pointed all ~2000 actor
sound references at nonexistent files — every creature was silent even though
records, triggers and files each looked correct in isolation.

Only `.mp3` is transcoded (to PCM `.wav`), because the SSE exe contains no
`.mp3` string at all. `_shipped_name()` in `dialog_misc.py` mirrors that one
rename and must stay in lockstep with `audio_converter.convert_sounds`.
**Voice lines are the exception** and are legitimately `.xwm`/`.fuz` (lip data
loads only from `.fuz`) — that separate path working is what wrongly suggested
xWMA was needed everywhere.

### Step 5 — Behavior graph generation (the new core)
New `asset_convert/behavior_gen.py`: emit per-creature `Actors\TES4\<creature>\`:
`<creature>project.hkx`, `characters\<creature>character.hkx`, `behaviors\<creature>
behavior.hkx`, from Python-templated hkx XML → hkxcmd. Model the template on the deer
stack (simplest quadruped) with the draugr/troll stacks as bipedal references:

- **Project + character files**: pure boilerplate + the manifest's animation list +
  rig/behavior paths. Trivial.
- **Behavior graph template**, parameterized by the manifest's clip classes:
  - Locomotion state: blend tree over forward/backward/fastforward/runforward (+ swim
    states when swim clips exist), driven by `Speed`/`Direction`; turn states from
    turnleft/turnright (`turnLeft/turnRight/turnStop`); omit canned-turn states when no
    canned turn clips exist (Oblivion has none — vanilla transitions degrade gracefully
    to looping turns).
  - Idle state: `mt_idle` from idle.kf; `specialidle_*`/`dynamicidle_*` behind a
    hkbManualSelectorGenerator keyed by generated events (`idleTES4_<name>Start`) for
    IDLE-record wiring (Step 7).
  - Attack states: one per attack clip, entered by `attackStart_TES4_<clipname>` (the
    same strings written to RACE ATKE), emitting `preHitFrame/HitFrame/attackStop`.
  - Stagger/recoil states from recoil.kf/stagger.kf (`staggerStart/recoilStart`).
  - Equip/unequip → `weaponDraw/weaponSheathe` states (creatures with twohand/bow sets:
    minotaur etc.).
  - Death/ragdoll wrapper: clone the deerbehavior.hkx pattern verbatim (PoweredRagdoll +
    RigidBodyRagdoll modifiers, GetUp, PoseMatching, `deathStart`, `ReanimateLeft/Right`)
    — this part is creature-independent boilerplate over the skeleton's ragdoll bones
    (bone index arrays generated from the skeleton census).
  - Standard variable set (Speed/TurnDelta/Direction/…) copied from the vanilla interface
    (§2.3) — the engine drives these regardless of creature.
- Start with ONE creature (deer or rat: small clip set, no weapons) and iterate against
  in-game testing before generalizing.

### Step 6 — animationdata / animationsetdata emission + merge — DONE
`asset_convert/animation_data.py`. Grammar notes that cost real digging:
- animationdatasinglefile = N + names + per project `[linecount, block]`,
  where a `[linecount, motion block]` pair follows ONLY when the flag line
  AFTER the project-file list (NOT line 1) is "1". Validated by a full walk
  of both the vanilla file (429 SSE projects) and our merged output.
- Clip block = name, uid (index into the boundanims motion blocks),
  playbackspeed, crop×2, trigger count, `Event:time` lines, blank.
- Motion block = uid, duration, translation rows `t x y z`, rotation rows
  `t x y z w` (cumulative root displacement, GAME units, quats xyzw —
  from kf_decode's split_root_motion, RDP-simplified).
- animationsetdata V3 block = attacks (event, "0", clip count, clip names)
  + CRC triples (dir/file/ext) using crc32(init=0,xorout=0) over lowercase,
  ≤4-char strings packed as ASCII, dir = `meshes\actors\tes4\<name>\animations`.
- The merge base MUST be the user's own game version (SSE has 429 projects
  vs LE's 327 — merging over the wrong base kills DLC creatures); extracted
  from `Skyrim - Animations.bsa` via `bsa_extract.read_bsa_files`
  (v103/104/105, embedded names, zlib/LZ4-frame) and cached.

### Step 7 — IDLE records / special idles
Oblivion `idleanims/specialidle_*.kf` are chosen by IDLE records with conditions
(`export/Oblivion.esm/IDLE.txt`). Convert IDLE: conditions via existing CTDA machinery,
DNAM/ENAM → the `idleTES4_<name>Start` events registered in Step 5. Defer until one
creature is fully proven.

### Step 8 — Development scaffolding & validation
- **Scaffold milestone (before the generator exists)**: validate Steps 3–4 in isolation
  by pointing one converted creature's RACE at a *vanilla* behavior project whose clip
  names we temporarily mimic (e.g. deer). This is a donor graph used as a test jig only —
  it never ships and needs no per-creature curation beyond the one test creature.
- **Graph milestone (before full asset conversion)**: run our *generated* graph on a
  vanilla Skyrim creature (our graph + vanilla deer skeleton/clips) to isolate graph
  correctness from asset conversion.
- Unit tests: hkx XML→binary→pynifly-read round-trips; B-spline decode vs
  NiTransformInterpolator agreement on dual-format KFs (idle.kf has both); skeleton
  conversion block census vs vanilla; manifest classification coverage over all 33
  Oblivion.esm creatures.
- Tools (multi-use, arg-driven): `tools/hkx_inspect.py` (wrap hkxcmd XML dump + pynifly
  reader: skeleton/tracks/annotations/graph summary of any hkx), `tools/creature_inventory.py`
  (Step 1), kf dump mode post-B-spline.
- In-game: spawn each creature (`player.placeatme`); check locomotion, turning, attack
  (with hit registration — HitFrame), stagger, death ragdoll, swim where applicable;
  Collision Sentinel watches the converted skeleton ragdolls.

---

## 5. Key technical facts (verified from references)

- **Skyrim record chain** (Skyrim.esm dump): `CreatureWolf` NPC_ has NO model — only RNAM.
  `WolfRace`: ANAM=`Actors\Canine\Character Assets Wolf\skeleton.nif`, behavior
  MODL=`Actors\Canine\WolfProject.hkx`, MTNM=WALK/RUN1/SNEK/BLDO/SWIM, WNAM=0004E886 →
  ARMO `SkinWolf` → ARMA `NakedWolfAA` (MOD2=wolf.nif, SNDD footsteps).
- **Skyrim creature skeleton.nif**: BSFadeNode root; BSXFlags=198; `NPC Root [Root]`
  present even on quadrupeds; full Skyrim-format bhk ragdoll in the NIF (capsules +
  Ragdoll/LimitedHinge constraints per bone). Draugr reuse the humanoid `NPC * [Tag]` rig
  and carry `rigPerspective/species/rigVersion` NiStringExtraData.
- **Skyrim creature body nif**: plain NiNode root + BSInvMarker; NiTriShape +
  BSLightingShaderProperty (diffuse/_n/_sk) + plain NiSkinInstance.
- **Oblivion creature skeletons vary per species** (dog: `Bip01` chain + `Canine_`-style
  bones; deer/rat/minotaur: pure `Bip01/Bip02`) — irrelevant under faithful port (bone
  names are preserved), but KF controlled blocks target the `Bip01` chain via
  NiStringPalette.
- **Oblivion KF interpolators are mostly B-spline compressed** — any pipeline ignoring
  `NiBSplineCompTransformInterpolator` loses the majority of creature motion.
- **pynifly reads AND writes Skyrim LE/SE animation HKX in pure Python**
  (`io_scene_nifly/hkx/anim_skyrim.py`, hk_2010 packfile incl. spline-compressed encode).
  Zero Oblivion support. `has_skin_instance` marked broken; scale animation not exported
  — minor, we port the writer approach, not the addon.
- **hkxcmd round-trip verified**: `convert -v:XML` ↔ `-v:WIN32` reproduces deerproject.hkx
  at identical size. (SUPERSEDED on the output side: shipped hkx are converted to
  AMD64 as the final pipeline step — SSE cannot load 32-bit hkx; see §4 above.)
- **Behavior stack anatomy** (deer): project (800 B boilerplate) → character (4 KB: rig +
  anim list) → creature wrapper graph (~40 objects: ragdoll/getup/pose-match) → shared
  locomotion graph (~350 objects, 21 state machines, 29 clips). `quadrupedbehavior.hkx`
  is shared by 8 vanilla species via `iCharacterSelector` — Bethesda's own template
  precedent.
- **animationdata is per-project plain text** in the LE BSA (`deerproject.txt` +
  `boundanims/anims_deerproject.txt` root-motion curves); engine reads the merged
  singlefiles.
- **niftools addon** confirms Oblivion conventions: Bip01 X-forward axis, string-palette
  targeting, `bhkBlendController`+`bhkBlendCollisionObject` on biped-layer bones, per-bone
  NiTransformController required on skeleton exports; its B-spline import delegates to
  `nifgen`'s `get_times()/get_translations()` API (the shape to replicate); it does NOT
  model root motion (`Bip01 NonAccum` untouched) and drops priorities on import.

## 6a. Implementation status (2026-07-08 / 2026-07-09)

- Skyrim chain confirmed in practice: NPC_ → RACE{ANAM=skeleton.nif, Behavior Graph
  MODL=`<X>Project.hkx`, WNAM} → skin ARMO → ARMA(MOD2=body nif). Creature body NIFs use
  plain NiNode root + plain NiSkinInstance; the ragdoll lives in skeleton.nif (BSFadeNode,
  BSXFlags=198) — creatures have NO separate ragdoll hkx (deer verified).
- Because we keep the Oblivion skeleton, body meshes need NO reskin/retarget — bone
  names/weights/bind matrices stay valid. `skin_retarget.py` is NOT used for creatures.
- **`asset_convert/kf_decode.py`**: KF decode incl. B-spline, uniform 30fps sampling,
  `split_root_motion` (locomotion accumulates on `Bip01` ITSELF, NonAccum static; turn
  anims carry root ROTATION, both extracted).
- **`asset_convert/hkx_xml.py`**: hk_2010 packfile XML emitter + hkxcmd compile/decompile
  wrappers.
- **`asset_convert/hkx_skeleton.py`**: skeleton.nif → minimal skeleton.hkx (hkaSkeleton
  only; ragdoll stage handled separately, see below).
- **`asset_convert/hkx_anim.py`**: THE animation path — DecodedClip → AnimationData →
  pynifly spline COMPRESSOR → packfile XML → hkxcmd `-v:WIN32`; validated 0.0000u/0.0000°
  vs source + hkxcmd deserializer-clean.
- **`asset_convert/kf_writer.py`**: Skyrim-format KF writer + CONVERTKF wrapper — DEBUG
  ONLY (see toolchain gotchas below; hkxcmd's spline compression is too lossy to ship).
- **`asset_convert/hkx_behavior.py` (2026-07-08)**: full project generator —
  `generate_creature_project(ob_creature_dir, name, out_root)` emits `actors/tes4/<name>/`
  with project/character/behavior hkx (XML templates copied from the vanilla deer dumps),
  skeleton.hkx, all converted animations, and `project_manifest.json` (clips, durations,
  triggers, root-motion curves, attack events — the contract for animation_data.py +
  tes5_import). v1 graph = one root hkbStateMachine: Idle(start,loop) + locomotion states
  + single-play attack/recoil/stagger/**Death** states (death.kf on `deathStart`, no end
  event = holds last pose), wildcard event transitions in, clip-end triggers out
  (attackStop→Idle). Attack events = `attackStart_TES4_<clip>` via `build_attack_events()`
  (RACE ATKE strings use the same, in creature_races.py). Dog validated: 20/20 generated
  hkx deserialize cleanly through hkxcmd (real Havok).
- **CREATURE PIPELINE IS LIVE END-TO-END (2026-07-09)** — pipeline Phase 4b /
  `--creatures-only` / GUI step "5. Creatures": `asset_convert/creature_pipeline.py`
  converts every creature folder → behavior project + converted skeleton.nif/body NIFs +
  animation singlefile registration + `export/<plugin>/creature_projects.json`. MUST run
  before import (Phase 0f consumes the json). `boxtest`+`endgame` are excluded (test asset
  / unparseable KFM cinematic).
- **A creature folder is ANY folder with `skeleton.nif` + `.kf` files, at any depth**
  (fixed 2026-07-27). Oblivion.esm uses the flat `meshes\creatures\<name>\` layout, but
  plugins nest theirs freely: Morrowind_ob ships 67 such folders under
  `meshes\morro\creatures\<name>`, `meshes\morroblivion\creatures\<category>\<name>` and
  deeper (`…\symphony\fbr\fst`). The old depth-1 scan of `meshes\creatures` found only 16
  of them, so **167 of its 307 CREA records fell through to `resolve_creature_race`
  aliasing and shipped as BASE SKYRIM creatures** (a frostbite spider or a Nord standing
  in for the converted actor). `convert_creatures` now walks the whole mesh tree. Both
  sides key on the folder's **leaf name** (the record side derives it from `Model.MODL`),
  so discovery and lookup agree for any layout. Two folders sharing a leaf name are
  disambiguated by `_crea_model_dirs()` — whichever folder the CREA records actually point
  at wins (Morrowind_ob has both `meshes\characters\draugr`, a humanoid body-part folder,
  and the referenced `meshes\creatures\aa_blood\draugr`), then shallowest path, then
  alphabetical, so the choice is deterministic.
- **A plugin inherits its MASTERS' creature projects** (fixed 2026-07-27,
  `creature_races._load_projects`). A plugin with a TES4 master re-uses the master's
  creature folders wholesale — Morrowind_ob places 86 CREA records on Oblivion.esm's
  rat/skeleton/goblin/mudcrab/… meshes, which its own BSA never ships, so the creatures
  step extracts no folder for them and its `creature_projects.json` has no entry. Without
  the master's json those records also aliased to base Skyrim races. The master's project,
  skeleton and merged body NIFs live under ITS output dir and are referenced by
  **meshes-relative paths** (`Actors\TES4\rat\tes4ratproject.hkx`), so they resolve
  identically whichever plugin ships them. Own projects win on conflict.
  Combined with the nested-folder fix: Morrowind_ob went **54/307 → 307/307** CREA records
  mapped to a real converted creature (240 own + 67 inherited), 64 local projects (was 10),
  80 generated `TES4*Race` chains. Diagnose with `temp/crea_project_gap.py <plugin> <master>`.
- **animationdata/boundanims/animationsetdata + singlefile merge
  (`asset_convert/animation_data.py`)**: the engine loads projects ONLY via merged
  `meshes/animationdatasinglefile.txt` + `animationsetdatasinglefile.txt`. Singlefile
  grammar: N + names + per-project `[linecount, block]`; a `[linecount, motion block]`
  pair follows ONLY when the flag line AFTER the project-file list (NOT line 1) is '1'
  (walk-validated on the vanilla 429-project SSE file AND our merged output).
  animationsetdata hash = crc32 **init=0/xorout=0** of lowercase
  (`zlib.crc32(b,0xFFFFFFFF)^0xFFFFFFFF`); strings ≤4 chars stored as packed LE ASCII
  bytes ('hkx' = 7891816); dir strings include the `meshes\` prefix. Merge base = the
  USER'S OWN game's singlefiles (SSE has 429 projects vs LE 327 — merging over the wrong
  base breaks DLC creatures), auto-extracted via `bsa_extract.read_bsa_files` (BSA
  v103/104/105: v105 = 24-byte folder recs hash8+cnt4+unk4+off8 + LZ4-frame compression,
  embedded-name flag 0x100; layouts verified vs xEdit wbBSArchive.pas) and cached in
  `export/animdata_base/`. Always merge from the vanilla base → idempotent re-runs.
- **Ragdoll stage in skeleton.hkx (`asset_convert/hkx_ragdoll.py`, 2026-07-09)**: Oblivion
  skeleton.nif bhkBlendCollisionObjects + ragdoll/limited-hinge/malleable(demoted)
  constraints → vanilla anatomy (ragdoll hkaSkeleton "Ragdoll_<bone>" + 2
  hkaSkeletonMappers + hkpPhysicsData/System + hkaRagdollInstance; the constraint graph is
  DUPLICATED per owner exactly like vanilla; one shared hkpPositionConstraintMotor).
  skeleton.hkx works in GAME units (ob-havok ×7, inertia ×49) — NOT Havok metres; ragdoll
  bone frames are DEFINED = anim bone frames (body translation offsets folded into
  capsule verts/COM) → identity mappers; hkTransform XML prints ROW-convention matrix
  rows (same convention as NIF matrices); ragdoll constraint basis rows = (twist, plane,
  twist×plane), hinge = (axle, perp1, perp2), pivots ×7 + folded offset. PyFFI 2.2.3
  `bhkMalleableConstraint` attr is `sub_constraint` (`.type` 2=limited hinge, 7=ragdoll).
  Best-effort: failure falls back to anim-skeleton-only with a warning. Dog: 26
  bodies/capsules + 25 constraints compile + round-trip through real Havok.
- **Creature mesh conversion (`nif_converter creature=True`)**: skinned bodies keep NiNode
  root + plain NiSkinInstance + ORIGINAL Oblivion bone names (no retarget — same
  skeleton), NiSkinPartition regenerated in Skyrim tri format (`_regen_skin_partition`);
  Prn-attached parts (doghead 'Prn'="Bip01 Head") get node transforms BAKED into verts
  (`_bake_node_transforms_into_verts` — skinning ignores node transforms and the head
  root carries a real rotation) then rigid plain-NiSkinInstance to the Oblivion bone
  (`_add_prn_skin(keep_bone_names=True, plain=True)`); skeleton.nif → BSFadeNode +
  BSX=198; `collision.py::_convert_blend_collision` KEEPS + converts
  bhkBlendCollisionObject in creature mode (vanilla creature skeletons have them: flags
  =137, motion_system 4 KEYFRAMED, quality 1 FIXED, layer 8 BIPED, translation ×0.1 and
  NOT zeroed, inertia ×0.1 here + ×0.1 in the constraint pass) — world objects still strip
  blends as phantoms; hoist/remove_empty_collision_nodes disabled in creature mode (would
  eat leaf bones). **ENGINE CONTRACT — the anim rig root must be named `NPC Root [Root]`
  (2026-07-08, the second invisible-creature root cause)**: ALL 30 vanilla creature
  skeleton.hkx name their anim hkaSkeleton AND its bone 0 exactly `NPC Root [Root]`
  (census over every species; the ragdoll skeleton is `Ragdoll_<bone>` and always second
  in the hkaAnimationContainer), and every vanilla creature skeleton.nif has the matching
  NiNode. SSE binds the behavior graph to the actor 3D through that node BY NAME — an
  Oblivion `Bip01` root never binds and the actor spawns invisible (collision capsule
  still works, because the char controller comes up anyway). Isolated with the
  `tools/creature_vanilla_ab.py` A/B ESP (our records + vanilla canine assets rendered
  fine → records/cache exonerated, assets implicated). The rename `Bip01` →
  `NPC Root [Root]` is defined ONCE (`hkx_skeleton.BONE_RENAMES`) and applied at every
  emit site: skeleton.hkx bone list (`collect_bones`), animation track binding +
  `originalSkeletonName` (hkx_anim), ragdoll bone lookups (hkx_ragdoll), and the NIF
  node rename for skeleton + all body parts (nif_converter creature mode; exact-match
  only — `Bip01 Spine` etc. keep their names, and `Bip01 NonAccum` is free-form like
  vanilla's per-species COM bones). Oblivion-runtime bone controllers are STRIPPED in
  creature mode
  (`_strip_creature_bone_controllers`, 2026-07-08): Oblivion skeletons carry an ACTIVE
  (flags=12) dataless NiTransformController on every bone + a bhkBlendController on every
  ragdoll bone + a NiBSBoneLODController on Bip01 — vanilla Skyrim ships NONE of these
  (bhkBlendController: 0 across all vanilla actor meshes; the only vanilla skeleton
  NiTransformControllers carry a real interpolator+data, e.g. the dog jaw/tongue idle —
  which is also why NifSkope's play button animates vanilla skeletons but did nothing on
  ours). NiTransformControllers WITH an interpolator are kept.
- **RACE biped-slot naming is mandatory for multi-part creatures (2026-07-08, the
  missing-heads bug)**: an ARMA only attaches if its biped slot is NAMED in the race's
  biped-object NAME list (census: every vanilla multi-part creature race names its extra
  slots — spider HEAD 30/Spit 40, horse Saddle 45, giant Arms 33; unnamed slot = part
  silently never renders while slot-32 body works). `_build_race` names slot 32 'BODY' +
  every extra part slot (40+, index 10+) with the part's NIF stem, mirroring
  `_build_skin`'s slot assignment.
- **Merged body NIFs carry the FULL rig from the converted skeleton.nif (2026-07-08,
  the mangled-goblin bug)**: `merge_creature_body` builds a fresh NiNode root with the
  whole bone hierarchy copied from `character assets/skeleton.nif` (names incl.
  `NPC Root [Root]`, local transforms, NO collision/extra data) and grafts every part's
  shapes onto it, re-pointing skin bones by name. There is NO "base part": Oblivion
  body-part NIFs embed only the bone SUBSET they're skinned to (goblin hand = 14 finger
  bones, chest = 13 spine bones — the hand won the old most-bones heuristic), so
  grafting onto any single part left other parts' bones as identity placeholders at the
  origin → parts attached in wrong locations. A skin bone the rig lacks (part-local
  control nodes) is copied from the part's own tree with its true world transform.
  Merges also must NEVER read a file another merge wrote: parts are converted into
  `_parts/` staging, merged outputs get unique stems (collision-numbered), and the
  exact NIFZ-set→file mapping ships as `body_map` in the manifest /
  creature_projects.json (record side does zero name derivation — creature variants
  share parts across sets, and in-place merging compounded whole bodies into every
  later file: 82KB→6.3MB, quadratic time).
- **hkaRagdollInstance requires a CONNECTED constrained tree (2026-07-08, the storm
  atronach spawn crash)**: n ragdoll bones need exactly n-1 constraints, single root.
  Storm/frost/flame atronachs carry ~54 free-floating rock bodies
  (bhkBlendCollisionObject, NO constraints — animated orbiting rocks); making every
  body-carrying bone a ragdoll part put 70 bodies/16 constraints in the
  hkaRagdollInstance and the engine crashed at actor spawn while pairing blend bodies
  (crash stack: bhkBlendCollisionObject 'Rock Pelvis C' + hkpPositionConstraintMotor +
  hkaRagdollInstance + QueuedCharacter). `extract_ragdoll` now keeps only the largest
  constraint-connected component (atronachs: 17 parts/16 constraints); rocks stay in
  skeleton.nif as animated blend collision.
- **Creature pipeline uses ProcessPoolExecutor (2026-07-08)**: the per-creature work is
  CPU-bound pure Python (pyffi, KF decode, spline compression) — ThreadPoolExecutor
  serialized on the GIL and gave zero parallelism.
- **NiSkinData per-bone bounding spheres are mandatory (2026-07-08, the third
  invisible-part root cause)**: the engine visibility-culls skinned geometry through the
  per-bone bounding spheres in `NiSkinData.bone_list` (each sphere is moved by its live
  bone every frame); a zero-radius sphere is never visible in-game, while NifSkope
  ignores the field entirely and renders the mesh fine. Oblivion-skinned bodies carry
  real spheres from the source NIF (which is why the body rendered), but Prn-grafted
  rigid parts (heads/eyes/tails via `_add_prn_skin`) built their `NiSkinData` from
  scratch with the sphere left at 0 → dog/mountain-lion heads invisible in-game.
  `_add_prn_skin` now computes the sphere from the vertex bounds (bind is identity, so
  mesh space == bone space). Applies to the merged whole-animal NIFs too — 
  `merge_creature_body` grafts converted shapes verbatim, so the sphere must be right
  at part-conversion time.
  (`hkx_behavior.ENGINE_VARIABLES`: Speed/Direction/TurnDelta/TurnDeltaDamped/
  SpeedSampled, iState/iGetUpType/iCharacterSelector, IsAttacking/IsRecoiling/
  IsStaggering/... — the engine-bound subset of vanilla dogbehavior's 65 variables). A
  graph with NO variables leaves the movement hookup dead: the actor loops its start
  state forever (idle-only, never walks, ignores attack events). Attack clips also emit
  `preHitFrame`/`HitFrame` triggers converted from the Oblivion `Hit` text key (KF text
  keys → `clip['hits']` in the manifest) in BOTH the graph clip trigger arrays and the
  animationdata cache trigger lines — HitFrame is the engine's attack-damage contract.
- **BSSpeedSamplerModifier is the engine's movement hookup (2026-07-08, the
  stuck-in-idle root cause)**: the engine drives actor movement by SAMPLING the graph's
  animation-driven speed through a `BSSpeedSamplerModifier` (Bethesda hkb extension;
  every vanilla creature locomotion graph has exactly one, wrapped around the whole
  state machine at the root: root SM → single 'Root' state → `hkbModifierGenerator`
  { `hkbModifierList` [sampler] , inner SM }). Its members are variable-bound:
  state→iState, direction→Direction, goalSpeed→Speed, speedOut→SpeedSampled
  (`hkbVariableBindingSet`, BINDING_TYPE_VARIABLE). A graph WITHOUT it gives AI pathing
  no speed to drive → the actor never receives movement, stands in idle forever, and
  combat can't approach either — even though the event vocabulary (moveStart etc.),
  wildcard transitions, cache registration, and setdata CRCs are all correct (each was
  verified independently before finding this). Layout copied verbatim from
  quadrupedbehavior.hkx #0441/#0440/#0439/#0438/#0365/#0364 (userData values 0/1/1/2
  included). Signatures: BSSpeedSamplerModifier 0xd297fda9, hkbModifierGenerator
  0x1f81fae6, hkbModifierList 0xa4180ca1, hkbVariableBindingSet 0x338ad4ff.
- **`--names` subset runs preserve other registrations**: convert_creatures merges the
  singlefiles from ALL on-disk `project_manifest.json`s, not just the current batch
  (a subset run used to silently drop every other creature from the cache).
- **AI package substitution (2026-07-09 — necessary, but NOT the stuck-in-idle cause)**:
  PACK is in SKIP_TYPES but convert_CREA/convert_NPC_ passed the TES4 PKID FormIDs
  through, so every actor's package list pointed at records that don't exist (vanilla
  creatures each carry exactly one package, DefaultMasterPackageCreature). Fix
  (`tes5_import/packages.py` + import Phase 0g): creatures get PKID
  DefaultMasterPackageCreature (0010F2A5) + DPLT DefaultMasterPackageListCreature
  (0010F2A6); humanoids get DefaultSandboxCurrentLocation1024 (000BFB6B) standing in for
  wander/eat/sleep-type TES4 packages + DPLT DefaultMasterPackageList (00021E81).
  Companion fixes: ZNAM no longer dangles on skipped CSTY (creatures: csWolf 00057BE8
  for animal/horse types, DefaultCombatstyle 0000003D otherwise; NPCs:
  DefaultCombatstyle), and TES4 aggression >5 → TES5 tier 1 (dog aggr 30 was mapped to
  0 = Unaggressive and would never initiate combat; TES4 default is 5).
  **User-tested: creatures STILL idle with the fix in place.** The decisive datapoint was
  the creature_vanilla_ab ESP: our records + vanilla canine assets MOVED AROUND (even
  when its packages still dangled) — the movement gate was inside the generated asset
  stack, not the records/AI-input side. Statically re-verified clean vs vanilla during
  this hunt: character hkx (property/capsule/axis fields), project hkx, animationdata
  motion curves (nonzero, plausible speeds), setdata attack blocks (V3 grammar walk of
  the whole singlefile), sampler wiring, variable defaults (bAnimationDriven=0).
  `tools/creature_vanilla_ab.py` now supports `--layers behavior|skeleton,body` +
  `--edid` lookup for per-layer bisection. Bisection results: vanilla-behavior-only ESP
  moves, vanilla-NIFs-only ESP doesn't, and console `tc` (take control) can't move the
  actor either → the movement CONTROLLER itself had nothing to drive (see next bullet).
- **IDLE records are the engine-action → graph-event routing table (2026-07-09, the
  stuck-in-idle root cause #3 — animations)**: the engine does NOT send moveStart etc.
  directly; it fires Actor Actions (AACT — ActionMoveStart/ActionDraw/...) and walks the
  IDLE records parented under each action, filtered by DNAM == the actor's root behavior
  graph path; the match's ENAM string is the event actually sent. One IDLE per action per
  creature project exists in vanilla (DogMoveStart: DNAM=DogBehavior.hkx,
  ENAM=moveStart, ANAM parent=ActionMoveStart — 36 distinct MoveStart IDLEs in
  Skyrim.esm). A behavior file with no IDLE records receives NO events at all: after the
  MOVT fix the dog translated (movement controller live) but played idle forever and
  never attacked. `sae` works regardless because it bypasses the routing. Fix:
  `tes5_import/creature_idles.py` (called from build_creature_races, once per project)
  emits the vanilla-dog leaf set (move/turn/stagger/recoil/idle-stop/reset/death-wait +
  the conditioned swim root/start/stop tree, DATA bytes + IsSwimming CTDA copied
  verbatim) with DNAM = our generated behavior path. Attack events are NOT IDLE-routed —
  the combat controller sends RACE ATKE strings directly, but only after the DRAW
  HANDSHAKE: ActionDraw routes combatStanceStart into the graph and the combat
  controller waits for the graph to answer with a weaponDraw event before attacking.
  Discovery credit: the Skyrim Behavior Modding Guide ("the Idle Animations tab...
  parses events... and sends animation events to the behavior graph") + ck-cmd's
  `IDLERecord()` helper.
- **The weaponDraw reply is sent by a root-level expression-modifier pair, and combat
  additionally gates on IsAttackReady/bEquipOK == 1 (2026-07-09, the no-attacks root
  cause)**: state enter/exitNotifyEvents (first attempt) did NOT unlock attacks in-game.
  Vanilla ground truth (hkxcmd XML dump of canine quadrupedbehavior.hkx): NOTHING in
  the vanilla graph notifies weaponDraw from a state — the reply comes from an
  always-present pair in the ROOT modifier list: `StartCombat_EDM`
  (hkbEventDrivenModifier, activate=combatStanceStart, deactivate=combatStanceStop,
  activeByDefault=false) wrapping `StartCombat_EEM` (hkbEvaluateExpressionModifier)
  with expressions `iCombatStance = 1` (EVENT_MODE_SEND_ONCE) and `weaponDraw if
  (iCombatStance == 1)` (EVENT_MODE_SEND_ON_FALSE_TO_TRUE), plus the mirror-image
  StopCombat pair (activeByDefault=true) sending weaponSheathe. AND vanilla
  dogbehavior initializes `IsAttackReady = 1` and `bEquipOK = 1` — our graph had
  IsAttackReady=0, which the combat controller reads before sending any attackStart_*
  (symptom: follows/chases forever, never attacks). Both replicated verbatim in
  `build_behavior_xml` (ENGINE_VARIABLES now carries per-variable initial values);
  the CombatStance state remains as the combat-facing pose but no longer notifies.
- **Per-creature MOVT SPED from clip root motion + parametric walk/run blend
  (2026-07-09, the too-fast fix)**: shipping vanilla-dog SPED bytes (forwardRun ≈ 500
  u/s) for every creature made them slide far faster than their walk animation.
  ck-cmd `calculateMOVTs` (ConvertNif.cpp): forwardWalk = |root-motion end translation|
  / duration. `generate_creature_project` now computes `speeds` {walk/run/back/swim}
  from the motion endpoints (dog: walk 55.9, run 379.9 — vanilla dog is 74.54/500.14)
  → manifest + creature_projects.json → `creature_races._movt_sped()` packs the
  11-float SPED (left/right 0, rotate π & 3π/2 rad/s = vanilla dog; no run clip → run
  = walk). Animation side: vanilla plays the RIGHT-looking gait via a PARAMETRIC
  blender, not states — ForwardWalkBlend_Dog is an hkbBlenderGenerator with flags 17
  (SYNC|PARAMETRIC), blendParameter bound to SpeedSampled, children anchored at each
  clip's NATURAL speed (74.54 = the MOVT value, proving MOVT speeds == clip root-motion
  speeds). Our MoveForward state now wraps walk+run clips (runforward/fastforward.kf,
  previously unused) in exactly that blender, so the played animation tracks actual
  speed. Vanilla dog fact: `iState` never changes (stays 30) and both dog MOVTs are
  byte-identical — walk vs run is chosen by the AI from the forwardWalk/forwardRun
  COLUMNS of the active MOVT, not by switching MOVTs (iState/MOVT switching is for
  multi-mode creatures like horses; the values are arbitrary tags, NOT state ids).
- **Death = ragdoll for Oblivion creatures (2026-07-09, the kill-keeps-idling fix)**:
  Oblivion creatures ship NO death animations (physics death), so a deathStart-driven
  Death anim state can never fire for most creatures. Vanilla routing: ActionDeathWait
  → DogDeathWaitRoot → DogDeathWait (ENAM=DeathAnimation, 2 CTDAs) else
  DogDeathWaitRagdoll (ENAM=Ragdoll fall-through); dogbehavior's root SM handles
  DeathAnimation → AnimateToRagdoll (enterNotify **AddRagdollToWorld**, internal
  Ragdoll → Fully Ragdoll via BSRagdollContactListenerModifier floor contact) and
  Ragdoll/RagdollInstant → Fully Ragdoll (enterNotify
  **RemoveCharacterControllerFromWorld**) — those two notify events are consumed by
  the ENGINE. Replicated: creature_idles.py emits the DeathWait tree (CTDAs verbatim)
  + Knockdown (ActionKnockDown 000D1FDC → Ragdoll) + RagdollInstant (ActionRagdollInstant
  0009BB4E → RagdollInstant); build_behavior_xml adds the two wrapper states
  (hkbPoweredRagdollControlsModifier maxForce 200 COMPUTE / 0 RAGDOLL, pose-matching
  bones picked from ragdoll part depths, idle-clip pose holders) gated on
  `hkx_ragdoll.ragdoll_info()`. No getup states yet (Oblivion creatures lack getup
  clips) — a knocked-down-but-alive actor stays down; death is unaffected.
- **Blend-collision body rot/trans = the body's BIND-POSE WORLD transform, not a
  bone-local offset (2026-07-16, the mangled-ragdoll root cause)**: on every Oblivion
  creature skeleton, a ragdoll bhkRigidBody's translation×7 equals its bone's world
  position EXACTLY (dog 26/26) and its rotation quaternion is the body's world
  orientation — capsule vertices, COM, and constraint pivots/axes are authored in that
  body frame.  (Vanilla Skyrim skeleton.nif blend bodies use the same convention in
  metre units, so the NIF-side `_convert_blend_collision` ×0.1 pass-through was always
  right.)  hkx_ragdoll used to treat translation as a bone-local additive offset and
  ignored the rotation → every capsule/pivot displaced by the bone's world position
  and mis-rotated → the ragdoll tore itself apart on death.  Fix: per body compute the
  bone-from-body transform (row-convention `R_body_world @ R_bone_world.T` + offset)
  and map ALL body-frame data through it at extraction time (`extract_ragdoll`
  normalizes constraints into bone-space info dicts; the XML emitters do no frame
  math).  Verification: capsules land on their bones AND each constraint's
  child-frame/parent-frame pivots coincide in world space (~1e-6) — the definitive
  joint-correctness test.  Also: vanilla creature ragdoll constraints ALL have
  maxFrictionTorque 0.0 (dog census) — Oblivion descriptor frictions (≈10) freeze
  Skyrim's solver into distorted poses; converted joints now use 0.0 (synthetic
  atronach rock joints keep the vanilla 10.0).
- **Ragdoll bodies need the Havok group-filter subsystem chain + box inertia
  (2026-07-16, mangled-ragdoll round 2 — vanilla dog census)**: every vanilla ragdoll
  body carries collisionFilterInfo = systemGroup 1 (bits 16+) | subSystemId (bits 5-9,
  = part index+1) | subSystemDontCollideWith (bits 10-14, = PARENT's subSystemId),
  layer 0 (the engine ORs the live layer in at attach).  All-zero filter info lets
  every overlapping capsule collide with its constrained neighbour (Oblivion dog
  ribcage capsules overlap by design) and the ragdoll blasts itself apart on death.
  Also vanilla bodies are MOTION_BOX_INERTIA with the anisotropic tensor — our old
  isotropic MOTION_SPHERE_INERTIA (max diagonal) made long thin limbs tumble
  unnaturally; the Oblivion inertia diagonal ×49 is emitted per-axis now.  Note the
  Oblivion skeleton exporter writes body world transform == bone world transform
  (R_delta identity), so capsule verts are effectively bone-local already; the
  bone-from-body transform machinery still guards against exceptions.
- **Oblivion creature ground speed is ATTRIBUTE-driven, not animation-driven
  (2026-07-16, the slow-motion-run report)**: walk = fMoveCreatureWalkMin +
  (fMoveCreatureWalkMax−Min)×Speed/100, run = walk×fMoveRunMult (GMST 5.0/300.0/3.0
  verified from the export) — a Speed-50 mountain lion ran 457 u/s in Oblivion while
  its gallop clip's root motion is only 200 u/s (Oblivion never synced anim rate to
  speed; it just slid).  Clip-natural MOVT speeds therefore make fast predators crawl.
  The 2026-07-16 runtime fix (MOVT raise + rate-scaled blend children) FAILED in
  game — the lion still ran in slow motion.  The formula speed is now BAKED into
  the walk/run animation files at conversion instead; see §8.
- **PyFFI's NiGeomMorpherController has a phantom `unknown_2` byte at exactly
  10.1.0.106 (2026-07-16, the mountainlion-missing-head regression)**: the reference
  nif.xml has no such field; stacking it with the patch-6b Manager-Controlled byte
  made every dev-era morph-bearing NIF (mountainlion head/paws, minotaur
  head/eyelids) unreadable [RD].  pyffi_monkey_patch patch 6d removes it.
  eyelidslord.nif is a different dev sub-revision with NO controller byte at all —
  still [RD], pre-existing, cosmetic.  Dev-era tri-less shapes that the grass
  reconstructor can't rebuild (minotaur hair01/hornsa/minotaurold — no topology in
  the file at all) raise UnreconstructibleGeometry and are DROPPED per-shape by
  nif_converter instead of failing the whole creature.
- **Ragdoll constraint motors are mandatory in the hkaRagdollInstance set (2026-07-09,
  the Storm Atronach / Skeleton Load3D crash)**: crash log showed
  EXCEPTION_ACCESS_VIOLATION dereferencing an hkpPositionConstraintMotor with a
  packed-float garbage pointer during ragdoll attach, always on creatures whose
  Oblivion skeletons use bhkLimitedHingeConstraints (atronach rock hinges, skeleton
  foot/calf hinges) — our hinges had `motor=null` everywhere. Vanilla census (dog +
  atronachstorm skeleton.hkx dumps): the hkaRagdollInstance constraint set is FULLY
  MOTORED (hinges get the single shared hkpPositionConstraintMotor, ragdoll data gets
  it ×3), the duplicate hkpPhysicsSystem set is FULLY NULL. Also the motor must emit
  `type=TYPE_POSITION` explicitly (omitted param = TYPE_INVALID, solver dispatches on
  it) with vanilla values maxForce=100/prop=5.0/const=0.2. Fixed in hkx_ragdoll.py
  (`_constraints(motor_ref)` builds the motored + null sets).
- **iState_* graph variables ↔ MOVT records are the movement-type registration contract
  (2026-07-09, stuck-in-idle root cause #2 — locomotion/movement)**: the engine gives an actor its
  movement types by enumerating the root behavior graph's variables named
  `iState_<X>` and looking up the MOVT record whose MNAM == `<X>`. Vanilla
  dogbehavior.hkx declares `iState_DogDefault`/`iState_DogRun` (initial values 30/31,
  and `iState` itself initialized to the Default value 30) matching MOVT
  `Dog_Default_MT` (MNAM=DogDefault) / `Dog_Run_MT` (MNAM=DogRun);
  quadrupedbehavior.hkx declares one per species. ck-cmd's RetargetCreature.cpp
  (references/ck-cmd-master) is the Rosetta stone: retargeting a creature = renaming
  the `iState_*` variables AND cloning the matching MOVT records with the new MNAM. A
  graph with NO `iState_*` variables leaves the movement controller with ZERO movement
  types: the actor cannot move under AI or under console `tc`, no locomotion events are
  ever sent (`sae moveStart` still works — it's a direct graph poke), and it idles
  forever while every static layer (events, transitions, cache, CRCs, records,
  packages) validates clean. Fix: `hkx_behavior.movement_type_names()` declares
  `iState_TES4<name>Default`/`iState_TES4<name>Run` INT32 variables (value = the
  MoveForward state id, iState initialized to match), the names ship in
  project_manifest.json / creature_projects.json as `movement_types`, and
  `creature_races._build_movts` emits matching MOVT records (SPED now computed
  per creature from clip root motion — see the too-fast fix bullet above) —
  graph↔record consistency by construction, like ATKE.
- **Record side (`tes5_import/creature_races.py`, import Phase 0f)**: one generated RACE +
  skin ARMO + per-body-part ARMA per unique (creature folder, NIFZ body set) — layouts
  byte-mirrored from real Skyrim.esm dumps of DogRace(000131EE)/SkinDog(0004B2C9)
  /NakedDogAA(0004B2CA); RACE DATA = the 164-byte dog template patched at offsets 36/40/44
  (health/magicka/stamina) + 96/100 (unarmed damage/reach); ANAM = converted skeleton,
  NAM3 behavior MODL = generated project hkx, ATKD/ATKE from manifest attacks, KWDA by
  creature class (animal 00013798 / daedra 00013797 / undead 00013796 / creature
  00013795), NAME×32 biped names (slot 32='BODY'). ARMA slots: first part 32-Body (0x4),
  extras creature slots 40+ (bits 10+); skin ARMO flags=4 non-playable, BOD2 = slot union.
  `convert_CREA` RNAM → generated race via `get_creature_race()`;
  `resolve_creature_race` Skyrim-race aliasing = FALLBACK only. NPC_ humanoids keep the
  Skyrim race override system (user directive).
- **Nested state-machine topology is mandatory (2026-07-16, the head-whipping +
  never-attacks root cause)**: the old v1 graph made EVERY engine event a root-level
  wildcard.  In combat the engine streams turnLeft/turnRight facing corrections
  continuously, so the whole graph got yanked into full-body turn states (visible as
  the actor "constantly scanning left and right") and any in-progress attack state was
  aborted mid-swing (attacks never landed).  Vanilla topology (quadrupedbehavior.hkx
  dump), now replicated by `build_behavior_xml`:
  * Root SM: DefaultState(0) + one state per attack/recoil/stagger/death/swim.  Root
    WILDCARDS carry ONLY recoilStart/recoilLargeStart/staggerStart/deathStart/swimStart
    + returnToDefault (FLAG_IS_GLOBAL_WILDCARD, the universal way back) — never
    movement or turn events.
  * attackStart_* transitions are LOCAL transitions FROM DefaultState (attacks can only
    start from locomotion/standing, and once inside an attack state no move/turn event
    can leave it).
  * Attack states wrap their clip in hkbModifierGenerator + BSIsActiveModifier
    (0xb0fde45a) with bIsActive0→IsAttacking, bIsActive1→bAllowRotation,
    bIsActive2→bDisableHeadTrack — the engine reads IsAttacking to stop steering
    mid-swing.  Single-play clips fire `returnToDefault` at clip end (NOT attackStop);
    the completion events the engine waits for (attackStop/recoilStop/staggerStop) are
    state exitNotifyEvents.  Attack clips also emit `weaponSwing` (~0.3s before the
    hit) like every vanilla attack clip.
  * DefaultState → DefaultBehavior SM {StandingState(0) ↔ LocomotionState(1)} via
    local moveStart/moveStop transitions.
  * StandingState → StandingBehavior SM {StandingIdle(0), LoopingTurnRight(1),
    LoopingTurnLeft(2)} — turnLeft/turnRight/turnStop are LOCAL transitions here and
    exist nowhere else, so turn-in-place can only happen while standing (vanilla
    exact).
  * StandingIdle → StandingIdleBehavior SM {NonCombatIdle(0) ↔ CombatIdle(1)} on
    combatStanceStart/Stop (replaces the old root-level CombatStance state).
  * All SMs use START_STATE_MODE_DEFAULT (vanilla census: every SM on this path).
- **The speed blend needs a slow-creep child (2026-07-16, the gliding root
  cause; layout revised 2026-08-22 — see §8)**: the AI sandboxes well below full
  walk speed, and a blend whose bottom anchor is the walk clip at rate 1.0 plays
  wrong-rate animation there → feet slide.  Vanilla's fix is a slow child pair
  (chaurus WalkSlow@0.058 → anchor 5 u/s), which `speed_blend_plan()` keeps.
  The rest of the blend is now the vanilla monolithic 3-child layout — slow@5 +
  walk@1.0 + run@1.0 at NATURAL anchors; the 2026-07 rate-scaled ladder
  (walk@1.4, run@0.75/1.5/2.0) is gone, replaced by the clip-timescale bake (§8).
  Every blend-child hkbClipGenerator NAME must be registered in the animationdata
  cache with its playback rate (trigger times in the cache are playback-local,
  i.e. natural/rate).  A "run" clip with less root motion than the walk (wraith)
  is dropped (anchors must increase, MOVT run falls back to walk).
  iState/iState_*Default/iState_*Run use the vanilla 30/31 tag values.
- Remaining refinements: specialidle/random-idle IDLE wiring (DogIdleRoot/DogIdles
  pattern), foot IK/look-at, getup-after-knockdown (needs getup clips Oblivion lacks —
  knocked-down live actors stay down; death unaffected), canned 90/180° turns
  (impossible from Oblivion data: turnleft/turnright.kf are looping shuffles with NO
  root-motion rotation — vanilla canned turns are authored root-motion clips; looping
  TurnLeft/TurnRight states are the turn support Oblivion data allows), per-creature
  SNDR sound sets + ARMA footstep SNDD, per-creature BPTD (RACE GNAM = vanilla canine
  body-part data for now), equip/unequip weapon states, body_map misses for
  skinned-hound variants (race collapse onto one variant), in-game validation.
- **Nemesis de-registers every generated creature project, and the fix is to
  override its BASELINE (2026-08-25)**: the game reads ONE
  `meshesnimationdatasinglefile.txt`, and Nemesis regenerates it into its
  output mod. But Nemesis does not read that file to do so -- it reads its OWN
  `meshes
emesis_animationdatasinglefile.txt` / `nemesis_animationsetdata
  singlefile.txt` pair (429 / 49 vanilla projects), found by walking
  `nemesisInfo->GetDataPath() + "meshes\"` for the `nemesis_` prefix
  (`UpdateFilesStart::VanillaUpdate` -> `GetFileLoop` / `RegisterBehavior`,
  `curFileName.substr(8)`). Our projects are not in that baseline, so they fall
  out of every regeneration. Symptom: graph loads (actor visible, idle plays)
  but no clip has metadata -- the creature slides at its MOVT speed with no
  locomotion animation and never attacks.
  **Fix**: those baseline files are ordinary Data assets, so we ship our own
  copy (its originals + our projects) and let load order override them --
  `asset_convert/nemesis.py::write_baseline_override`, `convert.py
  --nemesis-only`, GUI sub-step *Nemesis baseline* under Creatures, opt-in.
  The Nemesis location is `nemesisDir` in the config -- the MOD folder, with
  `meshes` resolved by `nemesis.baseline_dir` -- set through the GUI's
  Tools > Set Nemesis Folder. It cannot be auto-detected under Mod Organizer,
  where the mods are not in the game Data folder at all.
  Both Skyrim's creatures and ours survive; the Nemesis install is only READ.
  Load order: our mod AFTER *Nemesis Unlimited Behavior Engine*, BEFORE
  *Nemesis Output*. The pair is kept out of the BSAs
  (`bsa_pack._LOOSE_ONLY_FILES`) because Nemesis walks the disk, not archives.
- **The `Nemesis_Engine/mod/` patch route was tried and ABANDONED — do not
  rebuild it.** The format works (reverse-engineered from the Nemesis sources
  and matched against the shipped `tkuc`/`zcbe`/`skice` mods: new entries ship
  raw, markers are only for EDITING existing ones, and `MasterAnimData::add`
  keys projects as `<name>~<N>` so a `<stem>~1` folder resolves correctly). It
  still crashed Nemesis with an access violation on Update Engine. Suspected
  but NOT proven: `SeparateMod` is enqueued on a thread pool per mod folder and
  the new-project registration inside `ModThread` mutates `projectlist` /
  `projectIndexMap` and `newAnimSetData` with no lock -- in the user's entire
  install only the base `nemesis` mod registers a new project, and it registers
  exactly one, where we registered 86 at once. The baseline route makes the
  question moot: our projects are already registered, so that branch is never
  entered.
- **Toolchain gotchas (all cost real debugging time)**: hkxcmd CRASHES (0xC0000417) on
  FORWARD-SLASH paths — always `os.path.abspath`. hkxcmd's XML parser needs referenced
  objects defined BEFORE referencers (root container LAST). PyFFI fresh
  `NifFormat.Data()` defaults header `endian_type=0` (BIG endian) — must set 1 or every
  reader misparses the file. `hkxcmd CONVERTKF` spline compression is UNUSABLY LOSSY
  (vanilla round-trips with median 7.4°/max 37.6° bone rotation error) — never ship its
  output. pynifly's hand-rolled BINARY packfile writer produces files that CRASH real
  Havok deserializers (unaligned allocs + layout quirks; even rewritten-vanilla crashes
  hkxcmd) — its reader and `_compress_all_blocks` spline compressor are gold, its writer
  is bypassed via the XML path.
- **Vendored to `external/`** (user directive: runtime deps must be committed,
  references/ is reference-only): `external/pynifly_hkx/` (anim_fo4.py + anim_skyrim.py
  from PyNifly 27.4.0, GPL-3.0, local edits marked `# TESConversion:`),
  `external/hkxcmd/hkxcmd.exe`. README credits updated. **Target Skyrim LE 32-bit ONLY**
  (user directive: SSE loads LE-format assets — no 64-bit step ever).
- Skyrim-format KF layout (for kf_writer/EXPORTKF analysis): NIF 20.2.0.7/uv **11**/uv2
  83, DIRECT node_name strings (no palette), controller_type='NiTransformController',
  QUADRATIC quat keys + LINEAR trans keys at 30/s, `start`/`end` text keys, cycle 2.
- Animation hkx XML layout (from vanilla walkforward dump): binding
  `transformTrackToBoneIndices` EMPTY = identity 1-track-per-bone mapping;
  `originalSkeletonName` = skeleton root bone name; annotations all on track 0 with EMPTY
  trackNames; maskAndQuantizationSize = 4×tracks; single-block blockDuration constant 8.5.
- Vanilla skeleton.hkx (deer dump) = hkaSkeleton(anim) + hkaSkeleton(ragdoll) + 2
  hkaSkeletonMappers + hkpPhysicsData(capsules/rigidbodies/Ragdoll+LimitedHinge
  constraints) + hkaRagdollInstance in ONE file, root container namedVariants ×6. Havok
  quats are x,y,z,w (NIF matrices row-convention w-first —
  `hkx_skeleton._mat33_to_quat_xyzw` validated 1.2e-7 reconstruction on all 45 dog bones).
  hkaSkeleton referencePose entries are LOCAL (t)(q xyzw)(s); lockTranslation true except
  root/COM-level bones.
- PyFFI 2.2.3 HAS B-spline helpers (get_times/get_translations/get_rotations/get_scales)
  but they return raw CONTROL POINTS (curve eval unimplemented per its docstring) — real
  de Boor eval is in asset_convert/kf_decode.py, algorithm mirrored from NifSkope
  glcontroller.cpp (degree 3, clamped integer knots; dequant = short/32767*half_range
  +offset; interval v=(t-start)/(stop-start)*(nctrl-3)).
- LE animation archive EXTRACTED to `references/Skyrim Animations/` (behavior projects,
  skeleton.hkx, animationdata incl. per-project sources + boundanims root-motion text,
  animationsetdata, genericbehaviors). Registration: engine reads merged
  animationdatasinglefile.txt/animationsetdatasinglefile.txt — merge step required for
  new projects.
- Oblivion creature KFs are MOSTLY NiBSplineCompTransformInterpolator (B-spline
  compressed) — kf_animation_explorer.py currently SKIPS these; B-spline decode is the
  animation blocker. Root motion = Bip01 NonAccum tracks → boundanims curves (Skyrim
  clips are in-place).
- CREA export drops NIFZ (body-part list) + KFFZ — must fix tes4_export before record
  work. convert_CREA currently aliases creatures onto existing Skyrim races
  (resolve_creature_race), creates no RACE/ARMO/ARMA. *(Superseded above — RACE/ARMO/ARMA
  generation is now implemented; this note kept for history.)*

## 6. Open questions (resolve during implementation)

1. ~~Does PyFFI 2.2.3 ship the B-spline decode helpers?~~ **RESOLVED (2026-07-07)**: yes —
   `get_times/get_translations/get_rotations/get_scales` exist and dequantize correctly,
   but they return raw CONTROL POINTS (PyFFI's own docstring says curve evaluation is
   unimplemented). Proper cubic B-spline (de Boor) evaluation implemented in
   `asset_convert/kf_decode.py` using NifSkope's exact algorithm (glcontroller.cpp:
   degree 3, clamped integer knots, Cox–de Boor blend).
3. Ragdoll sufficiency: deer has no separate ragdoll hkx (skeleton.nif bhk + graph
   modifiers only) — confirm the same holds for draugr/werewolf, and that our converted
   bhk ragdolls satisfy the PoweredRagdoll modifiers (bone index arrays must match).
4. `animationsetdatasinglefile.txt` requirements for creatures without weapon-draw states
   — do minimal projects need an entry at all?
5. GNAM/BPTD (body part data) — is it mandatory on RACE, and what's the minimal valid
   BPTD (dismemberment targeting)? Check what vanilla creatures without dismemberment use.
6. Does the singlefile merge require CRC-hashed project names in its index (some
   community docs mention hashed dir entries)? Derivable from diffing the vanilla
   singlefile against the per-project sources.
7. Per-variant NIFZ handling: one RACE per CREA record vs shared race + multiple skin
   ARMOs for variants sharing a skeleton (wolf/dog). Skyrim precedent supports either.
8. Character controller dimensions (hkbCharacterData capsule) — generate from creature
   bounds; verify units against vanilla values.

---

## 7. Spellcasting: the magic handshake (implemented 2026-08-21)

**Symptom:** converted creatures never cast spells, despite 600 of 914 Oblivion
CREA (65.6%, measured from `export/Oblivion.esm/CREA.txt`) carrying
`SpellCount>0`.

**Two independent defects had to be fixed — one on each side.**

### 7a. The records: convert_CREA never emitted spells (fixed 2026-08-21)

Initially mis-diagnosed as "the records are fine" — that check looked at the
NPC_ path and at aggregate SPCT counts in the output ESM (1,182 subrecords,
all of which turned out to be real NPCs). **`convert_CREA` is a separate
function and emitted no SPCT/SPLO at all.**

Measured before the fix, by matching source EditorIDs against the built
plugin: **600 of 600** spell-carrying Oblivion creatures shipped with zero
spells. Nehrim adds 475 more, Morrowind_ob 250.

Worked example — `CreatureScampStunted` (source `0003E9CB`):

| | Source CREA | Built NPC_ (before) | Built NPC_ (after) |
|---|---|---|---|
| SpellCount | 2 | *(no SPCT)* | 2 |
| Spell[0] | `0002B543` AbDaedricResistWeak (SPEL) | — | SPLO `0102B543` |
| Spell[1] | `0005D4A2` LL2CreatureScampStunted100 (**LVSP**) | — | SPLO `0105D4A2` |

The second entry is a **leveled spell**, not a SPEL — it resolves
LVSP → SPEL `000A97DF` "Flare" (FIDG fire damage, magnitude 6). xEdit types
SPLO as `[SPEL, SHOU, LVSP]` (`wbDefinitionsTES5.pas:2302`), so the leveled
list is referenced directly and never needs unrolling. `convert_LVSP` already
converted the record; only the actor's reference to it was missing.

Field order is `RNAM → SPCT → SPLO[] → COCT → CNTO → AIDT`, verified against
**both** the xEdit TES5 NPC_ definition and real Skyrim.esm records.

After the fix, across all three standalone plugins: **1,325 creatures,
3,201 SPLO references, 0 dropped.** Guarded by
`tests/test_import.py::test_crea_spells_are_emitted` (+ order and
null-FormID cases).

The override path (`override_builder.py`) already handled CREA spells via
`_RUN_SPELLS` anchored after RNAM — only the base converter was affected.

### 7b. HOW A CREATURE CASTS: the engine handshake, decompiled (corrected 2026-08-22)

Two earlier theories shipped and failed in game — a single fire-and-forget
state, then an "ATKD Attack Spell is how creatures cast" model whose graph
entered its cast states from expressions that tested **event names as if they
were variables** (`Spell_Target_RH_In_Start if (BeginCastRight)`) — an
hkbExpressionData condition can only read VARIABLES, so those events never
fired and nothing ever entered the cast states. The real mechanism was read
out of the decompiled vanilla casters (atronachflame/atronachstorm behavior
graphs, hagraven/spriggan/wisp IDLE trees, chaurus for the monolithic case):

1. The AI decides to cast → the engine writes `bWantCastLeft`/`Right`.
2. The graph's `BeginCast_EEM` raises
   `BeginCastLeft if (bWantCastLeft && bMLh_Ready && !IsCasting)` (verbatim
   vanilla expression). **BeginCast\* is the graph's message TO the engine**
   — no transition in any vanilla graph consumes it.
3. The engine performs the **Left-Attack ACTION** and walks the creature's
   IDLE tree under AACT `ActionLeftAttack` (0x13004):
   `<X>ActionLeftAttackRoot → <X>ActionLeftAttackMagic →
   <X>ActionMagicFireForgetRoot → <X>ActionMagicFireForget`, whose **ENAM
   `Spell_FireForget_LH` is the state-entry event** — the transition into the
   cast states is keyed on it, never on BeginCast*. Release/Ready/Interrupt
   route the same way: `ActionLeftRelease → Spell_Release`,
   `ActionLeftReady → Spell_Ready`, `ActionLeftInterrupt → Spell_Interrupt`,
   `ActionForceEquip → Magic_Equip`. **A graph with no such IDLE records
   never receives ANY of these events** — the same action-routing gate as
   movement (`creature_idles.py` docstring).
4. In the graph, the In clip charges; its `Magic_Pre_Out` end trigger chains
   into the Loop, which parks on the charged pose. The engine commits with
   `Spell_Release` → Out plays; the Out clip's **`MLh_SpellFire_Event`
   trigger (0.233s in, vanilla animationdata) is what actually fires the
   spell**; `Spell_Stop` at its end exits.

Hand convention: vanilla creature casters are LEFT-handed — the atronach's
`Spell_FireForget_RH` state is dead code (no transition uses it) and even its
RH Out clip fires `MLh_SpellFire_Event` (animationdata, verbatim); the
chaurus declares only `bWantCastLeft`/`bMLh_Ready`. The wisp is the
exception that defines the dual case: it **blocks on the left-hand actions
and casts on the right**.

Variable inits: `bWantCast*`, `bM*h_Ready`, `IsCasting` all init **0**
(atronach wordVariableValues, verbatim). **Readiness is the GRAPH's to grant
at runtime (found 2026-08-23, the "scamps get stuck" report):** vanilla's
`BSIsActiveModifier_CombatIdle` binds `bIsActive0→bMLh_Ready` over the
combat-idle subtree (idle + combat locomotion) and `BSIsActiveModifier_Stagger`
clears it (inverted binding). A graph that never writes `bMLh_Ready` can
never satisfy `bWantCastLeft && bMLh_Ready && !IsCasting`, so the AI keeps
wanting to cast and the actor stands there. Ours holds both hands' readiness
over the whole `DefaultState` (`CombatIdle_MG`) — ready whenever not
attacking/casting/staggering/blocking/swimming, each a sibling state.

**What ships (2026-08-22):**

- `hkx_behavior.py`: ONE `Mag_FF_In/Loop/Out` chain per caster graph
  (`Mag_FF_Behavior` sub-machine wrapped in a `BSIsActiveModifier` holding
  `IsCasting` for the whole cycle), entered by wildcards on
  `Spell_FireForget_LH`, `Spell_FireForget_RH` and `Spell_Concentration_LH`;
  internal wildcards `Spell_Release → Out`, `Spell_Ready → In`; the root
  `FireForgetState` exits on `Spell_Stop`/`Spell_Interrupt`/`InterruptCast`.
  The `BeginCast_EEM` carries the two vanilla expressions and NOTHING else.
  The chain plays one clip, preference `casttarget > casttouch > castself`
  (the engine's entry event carries no delivery information).
- `hkx_anim.split_cast_clip`: the release moment is the clip's authored
  'Hit' key (69/69 Oblivion cast clips carry one); the cut sits
  `CAST_PRE_RELEASE` (0.25s) before it so the Loop holds the charged pose
  and the Out opens with the throw — vanilla's Out fires SpellFire 0.233s
  in. The Out's animationdata block is vanilla-shaped:
  `MLh_SpellFire_Event:<t>` + `MRh_SpellFire_Event:<t>` + `Spell_Stop:<end>`
  (both hand events because vanilla itself answers a right-hand cast with
  the LEFT event; the extra event is inert).
- `creature_idles.py`: `_build_cast_idles` replicates the atronach IDLE tree
  node-for-node (left hand; right hand when the creature also blocks — the
  wisp split), **including its CTDA gates** (2026-08-23, the "scamp chases
  but can't melee and never casts" report): the engine walks the
  `ActionLeftAttack` tree for ordinary MELEE left attacks too, so vanilla
  gates the magic branch with `HasEquippedSpell(Left) == 1` (func 570), the
  FireForget root with `GetCurrentCastingType(Left) == 1` (571), the release
  leaf with `HasEquippedSpell`, and the force-equip leaf with
  `GetEquippedItemType(Left) == 9` (597). Shipped unconditioned for one
  build, every melee left attack was routed into the cast chain and parked
  there. The block tree carries vanilla's `GetWantBlocking == 0 / == 1`
  gates on its left-attack/left-release leaves for the same reason. The
  concentration branch is omitted: every converted TES4 spell is FireForget
  (`magic._delivery_and_cast` always returns cast type 1).
- Records (`creature_races.py` / `actors.py`):
  * SPLO spells (7a) — unchanged, LVSP targets are engine-legal (2,858
    vanilla SPLO refs point at LVSP records).
  * **Magicka**: generated races now carry starting magicka 0 and the
    actor's whole TES4 `ACBS.SpellPoints` pool ships as
    `ACBS.MagickaOffset` (the race is shared, so a per-race pool was wrong
    for everyone but the founding record; an actor with 0 magicka can never
    pay a cast cost). Vanilla's atronach uses the same split
    (`MagickaOffset=50`).
  * **ATKD 'Attack Spell' kept, but only where vanilla uses it**: a pure
    TOUCH-delivery offensive spell (SPIT.Type 0, ≥1 Touch effect, no Target
    effect) rides the existing melee attack entries — the flame atronach
    idiom (its four ordinary attacks each name a fire spell; 109 vanilla
    attack entries carry one). Aimed/self spells go through the cast chain,
    not the attack table. The invented `attackStart_TES4_cast*` attack
    entries are gone.
  * RACE VNAM Spell bit (`1<<9`) — unchanged (set when any creature sharing
    the race knows a spell).
  * **RACE QNAM LeftHand equip slot (0x13F43) — THE GATE (found live
    2026-08-23).** A spell is equipped into a HAND slot; VNAM only permits
    the class. Every vanilla caster race lists LeftHand (AtronachFlameRace
    LeftHand+Potion, HagravenRace Left+Right+Potion, SprigganRace/
    ChaurusRace/WispRace Left+Right); the non-caster WolfRace lists
    RightHand only — and so did our unarmed creature races. Bridge readback
    on a live scamp in combat: 100 magicka, weapon out, 77 units from the
    player, `HasEquippedSpell Left/Right = 0` → the engine never equipped a
    spell, so `bWantCast*` was never written and no graph/IDLE work could
    matter. Casters now get `[RightHand, LeftHand]`.
  * Note for testing: the Oblivion-gate template scamps
    (`MQ13TemplateGate1/2/3`, FULL "Scamp") carry NO spells in the source
    CREA records, so they cannot cast in either game; test casting against
    `CreatureScamp` / `CreatureScampStunted` (`player.placeatme 1203E9CB`
    with Oblivion.esm at load index 12).

### 7c. Swimming (implemented 2026-08-22)

The engine sends `swimStart`/`swimStop` (already routed by the
`ActionSwimStateChange` IDLE tree) and writes the graph variable
`isSwimming`. Vanilla's quadruped graph switches movement type on it —
`iState = cond((isSwimming ==1), iState_BearSwimDefault, iState_BearDefault)`
verbatim — which is what gives the actor its water speeds.

What ships:

- `classify_clips` claims the whole authored swim set (`SWIM_CLIPS`:
  swimforward / swimfastforward / swimidle / swimbackward / swimturnleft /
  swimturnright — previously only swimforward survived).
- The `SwimState` wraps a `SwimBehavior` sub-machine mirroring the land
  standing/locomotion split, driven by the SAME engine locomotion events:
  SwimIdle ⇄ SwimMove (a `SpeedSampled`-parametric blend of
  slow/forward/fastforward at natural anchors) ⇄ SwimBack, plus the turn
  states. Water natives (slaughterfish) finally idle, turn and back up.
- `movement_type_names` gains `TES4<x>Swim` when swim clips exist; the
  graph declares `isSwimming` + the vanilla-verbatim iState switch
  expression; `creature_races._build_movts` emits the matching MOVT with
  the swim clips' own speeds (`_movt_sped_swim`).

### 7d. Blocking (implemented 2026-08-22)

Vanilla contract (frost atronach = the unarmed-blocker layout, draugr = the
armed one): the engine sends `blockStart`/`blockStop`/`blockHitStart`/
`blockHitStop`, writes `iWantBlock`, and reads `IsBlocking` back from the
graph. An unarmed creature raises its guard by "holding the left attack" —
`AtronachFrostLeftAttack` ENAM=`blockStart`, `...LeftRelease`
ENAM=`blockStop`, plus `ActionBlockAnticipate → blockStart` and
`ActionBlockHit → blockHitStart`, all IDLE-routed.

What ships:

- `classify_clips` claims the guard/flinch pair (`BLOCK_CLIPS`: blockidle/
  block + blockhit, generic first then the stance-prefixed variants).
- Graph: looping `BlockState` + single-play `BlockHitState`, both wrapped in
  a `BSIsActiveModifier` holding `IsBlocking`; `blockHitStart` interrupts
  the guard, the flinch fires its own `blockHitStop` at clip end so the
  guard resumes even if the engine never sends the stop; `blockStop` exits.
  `IsBlocking`/`iWantBlock` declared only for blockers (vanilla splits the
  same way).
- `creature_idles._build_block_idles` replicates the frost-atronach IDLE
  routing. A creature with BOTH lanes (goblin) blocks on the left-hand
  actions and casts on the right — the wisp split.

### 7e. Still unwired (measured 2026-08-21, unchanged)

Remaining classes of authored-but-unused clips, by cost:

| Class | Files | Creatures | What is lost |
|---|---|---|---|
| Stance locomotion (`handtohand*`, `onehand*`, `twohand*`, `staff*`, `bow*`) | ~500 | 47 | An armed creature walks with the generic unarmed `forward.kf`; the authored `onehandforward.kf` etc. is never used. Vanilla drives these from `iRightHandType`, which we already declare and set. |
| `left` / `right` strafe | 76 | 38 | No strafe states; the locomotion machine has no sideways entry. |
| `jumpstart` / `jumploop` / `jumpland` | 9 | 3 | No jump states. |

---

## 8. Ground speed: BAKED, not commanded (rewritten 2026-08-22)

**Symptom (twice-reported): "the mountain lion runs in slow motion."**

Oblivion moved a creature at the Speed-attribute GMST formula (walk =
`fMoveCreatureWalkMin + (Max−Min)×Speed/100`, run = `walk×fMoveRunMult`;
5.0/300.0/3.0 from the export) regardless of the animation — clips just
slid. The lion's gallop clip is only 200 u/s natural, so clip-natural MOVT
speeds made it crawl (first report). The 2026-07-16 fix raised MOVT to
`max(natural, formula)` capped at rate-scaled blend children (run@0.75/1.5/
2.0 etc.) — and the lion STILL ran in slow motion in game (second report),
so the runtime-rate-ladder theory is dead. It also had no vanilla
precedent: **every vanilla creature's commanded MOVT speed equals its run
clip's natural speed at playback rate ~1.0** (chaurus `Forward_Run`: blend
anchor 350.267 = MOVT run = clip natural; sabrecat 563 = run clip 490 ×
1.15 = MOVT; wolf 555 likewise).

**The fix: bake the formula into the animation file itself.**
`hkx_anim.timescale_clip` compresses the walk/run clips' timelines at
conversion (factor = formula/natural, capped ×1.4 walk / ×2.0 run —
`generate_creature_project` `attr_speed` = the folder's MAX `DATA.Speed`,
from `creature_pipeline._speed_attr_by_folder`), so the shipped
`runforward.hkx` REALLY IS a 400 u/s gallop. Everything downstream — root
motion, `speeds`, blend anchors, MOVT SPED — is derived from the baked file
and agrees at rate 1.0 by construction; no runtime component can ignore it.
Mountain lion: walk 23.2→32.5 u/s, run 200→400 u/s (vs 457 formula at the
×2.0 cap; vanilla sabrecat runs 563).

**Two gait families in two states (2026-08-23).** The first bake layout put
walk and run in ONE 3-child blend (slow@5 / walk / run) and the lion
"briefly broke into a bad pose while running": the gallop's only blend
neighbour was the 2.9 s stalking walk, so every dip of `SpeedSampled` below
the run anchor SYNC-blended a phase-warped walk pose into it (the old
7-child ladder never showed this because the top anchor's neighbours were
the same run clip at other rates). Vanilla's answer (sabrecat
forwardlocomotion.hkx, verbatim shape): `ForwardWalkState` (walk family:
slow@5 + walk@1.0) and `ForwardRunState` (run family: RunSlow@0.75 +
Run@1.0 — the SAME clip) as separate states in `ForwardLocomotionBehavior`,
`startStateId` bound to `iMovementSpeed = cond((Speed < 100), 0, 1)`, and
`runStart if (SpeedSampled > hi)` / `walkStart if (SpeedSampled < lo)`
switching with hysteresis (`gait_thresholds`: midway between the walk anchor
and the run-family bottom, 15% band; lion 141/166). The gallop now only ever
blends with itself. Comparison operators in expressions must be XML-escaped
(`&lt;`/`&gt;`) or hkxcmd refuses the file.

Text keys, foot enums, SoundPlay times and the root-motion curve all scale
with the bake; cast/swim/block clips are untouched (no formula applies).

**The bake must land on the 30 fps grid (2026-08-23, "limbs going every
which way").** The first bake merely compressed the sample spacing, shipping
a 60 fps file (`frameDuration` 0.0167 under a block layout still sized for
30 fps) — a timing no vanilla animation has, and the lion's limbs exploded
while running. `timescale_clip` now RESAMPLES the tracks (linear / sign-
continuous quaternion nlerp) onto exactly 1/30 s frames and snaps the
duration to the grid (lion run: 46 frames @1.5 s → 24 frames @0.767 s,
`frameDuration` 0.03333 — identical timing fields to a native clip).
