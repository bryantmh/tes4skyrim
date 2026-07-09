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
  converts every `export/<plugin>/meshes/creatures/<name>/` folder (32/32 real
  Oblivion.esm creatures; `boxtest`+`endgame` excluded — test asset / unparseable KFM
  cinematic) → behavior project + converted skeleton.nif/body NIFs + animation singlefile
  registration + `export/<plugin>/creature_projects.json`. MUST run before import (Phase
  0f consumes the json).
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
- Remaining refinements: specialidle/IDLE wiring, foot IK/look-at/speed-blended gaits,
  ragdoll-driven death in the graph (PoweredRagdoll — Death state currently holds the last
  anim pose), per-creature SNDR sound sets + ARMA footstep SNDD, per-creature BPTD (RACE
  GNAM = vanilla canine body-part data for now), equip/unequip weapon states, in-game
  validation.
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
