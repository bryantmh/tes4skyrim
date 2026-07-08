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
     **Target format is Skyrim LE (32-bit WIN32) throughout** — SSE loads LE-format
     assets, so no 64-bit conversion step is needed (user-confirmed).
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
- `tes5_import/creature_races.py` — Phase 0f: generated RACE/ARMA/ARMO per
  unique (creature folder, NIFZ body set), layouts mirrored from real
  Skyrim.esm DogRace/SkinDog/NakedDogAA dumps; ATKE = the generated
  `attackStart_TES4_*` events; `convert_CREA` RNAM → the generated race
  (`resolve_creature_race` aliasing kept only as fallback). NPC_ humanoids
  keep the Skyrim race override system.
- Death: `death.kf`/`dies.kf` = single-play `Death` state on `deathStart`
  (holds last pose); ragdoll-driven death via the behavior graph
  (PoweredRagdoll modifier) is still a refinement.

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
4.4 **Text keys → clip triggers/annotations**: `Sound: X` → `SoundPlay` (+ SNDR wiring
    later), `Hit` → `HitFrame` (and a `preHitFrame` slightly earlier), `a: L/R` → attack
    annotations, gait `Enum: Left/Right` → `FootFront/FootBack`. These land in the
    animationdata clip trigger lists (timestamps) and/or in-hkx annotations — copy
    whichever placement vanilla uses per event type (visible in the extracted deer data:
    triggers live in `animationdata/<x>project.txt`).

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
  at identical size. All output stays LE 32-bit (SSE loads LE-format assets — user
  directive, no 64-bit step).
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
