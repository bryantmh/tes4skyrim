# tes5_import/base/object_scripts.py - quests and quest scripts

**Code:** `tes5_import/dialogue/quest.py`, `script_convert/converter.py`, `tes5_import/dialogue/converter.py`, `tes5_import/base/object_scripts.py`, `script_convert/constants.py`

## Contents

- [Quest conversion: bugs found and fixed](#quest-conversion-bugs)
- [Bugs found and FIXED on this branch](#bugs)
- [Quest script conversion: defects found and fixed](#quest-script-conversion-defects)
- [Defects found and fixed (round 9)](#defects)
- [Defects found and fixed (round 7)](#defects-2)
- [Defects found and fixed (round 6)](#defects-2-2)
- [Defects found and fixed (round 5)](#defects-2-2-2)
- [Defects found and fixed (round 4)](#defects-2-2-2-2)
- [Defects found and fixed (round 3)](#defects-2-2-2-2-2)
- [Defects found and fixed (round 2)](#defects-2-2-2-2-2-2)
- [Defects found and fixed (round 1)](#defects-2-2-2-2-2-2-2)
- [Build verification (round 7)](#build-verification)
- [Build verification (round 6)](#build-verification-2)
- [Build verification (round 5)](#build-verification-2-2)
- [Build verification (round 4)](#build-verification-2-2-2)
- [Build verification (round 3)](#build-verification-2-2-2-2)
- [Build verification (rounds 1-2)](#build-verification-2-2-2-2-2)
- [Quest script conversion: verified-correct behaviour](#quest-script-conversion)
- [Verified-correct behaviours from round 9 (do NOT "fix" these)](#section)
- [Verified-correct behaviours from round 8 (do NOT "fix" these)](#section-2)
- [Verified-correct behaviours from round 7 (do NOT "fix" these)](#section-2-2)
- [Verified-correct behaviours from round 6 (do NOT "fix" these)](#section-2-2-2)
- [Verified-correct behaviours from round 5 (do NOT "fix" these)](#section-2-2-2-2)
- [Verified-correct behaviours from round 4 (do NOT "fix" these)](#section-2-2-2-2-2)
- [Verified-correct behaviours from round 3 (do NOT "fix" these)](#section-2-2-2-2-2-2)
- [Verified-correct behaviours from round 2 (do NOT "fix" these)](#section-2-2-2-2-2-2-2)
- [Verified-correct behaviours from round 1 (do NOT "fix" these)](#section-2-2-2-2-2-2-2-2)
- [Quest script conversion: known gaps](#quest-script-conversion-known-gaps)
- [Known gaps (round 9, not fixed here)](#known-gaps)
- [Known gaps (round 6, not fixed here)](#known-gaps-2)
- [Known gaps (round 3, not fixed here)](#known-gaps-2-2)
- [Known gap (not fixed here)](#known-gap)
- [Why an actor script must move to its placed reference](#actor-script-relocation)

## Why an actor script must move to its placed reference
<a id="actor-script-relocation"></a>

**Code:** `tes5_import/base/object_scripts.py:_relocate_actor_scripts_to_refs`

Three independent reasons an actor script MUST live on the reference, not on
the base `NPC_`/`CREA`:

1. **`GetVMScriptVariable(ref, "::var_var")` reads the property off a script
   attached to the *reference named in param1*** (the ACHR), not off the base
   record — verified against Skyrim.esm, where 100% of vanilla func-630 package
   conditions name a REFR that carries its own VMAD holding the variable. A
   base-attached script propagates to instances for property *access* (fragment
   writes work), but the condition *read* fails, so the quest package never wins
   its arbitration and the actor stays put (Pinarus/FGC01Rats,
   Arielle/MG04Restore, ~142 actors).

2. **Reference events never fire on a base-attached script.** `OnPackageEnd` and
   friends are declared on `Actor`/`ObjectReference` (vanilla Scripts.zip);
   `NPC_` is an ActorBase, so an event-driven script bound there is inert. This
   silently killed every converted quest that sequences on package completion —
   CharacterGen sets stage 12 from Renote's `OnPackageEnd`, so the chain stopped
   at stage 10 and the Emperor/guards had no `GetStage ==` package to select at
   all.

3. **Self-reference calls have no target on a base record.** A bare `enable` /
   `moveto` / `startcombat` acts on the calling REFERENCE; an ActorBase is not
   one, so the call does nothing. Oblivion's standard scripted-entrance idiom is
   an initially-disabled placement whose own GameMode block enables it on a cue,
   which makes this the difference between the actor appearing and never
   existing — Celebro, the Nehrim intro companion, was absent from the start
   cell for exactly this reason (`MQ00CelebroScript`:
   `if GetStage MQ00 == 5 / enable`).

Vanilla does exactly this split: instance-identified logic lives on the ACHR
(masterAmbushScript, 464 placements), while generic per-actor behaviour stays on
the base (WIDeadBodyCleanupScript, defaultGhostScript).

The script is moved (base entry removed) rather than duplicated so there is
exactly ONE instance — both the fragment write (via the ACHR-typed self
property) and the condition read resolve to it.

### Which scripts qualify, and the single-placement rule

The MASTERS' scripts are indexed alongside this plugin's (keyed on the
`master_export` key, this plugin's space — see `_collect_scpts`): a dependent
plugin's actor can carry one of its master's scripts, and missing it means the
relocation never happens, so every reason above silently applies.

A script may be moved OFF the base only when that base has a single placement,
else siblings would lose it. Shared bases (rare: SI victims, Sheogorath's sheep)
keep the base attachment and gain a per-ref one, matching Oblivion's
per-instance variables.

## Quest conversion: bugs found and fixed
<a id="quest-conversion-bugs"></a>

From the 2026-07-17 completability audit.

## Bugs found and FIXED on this branch
<a id="bugs"></a>

### 1. `End ;comment` silently dropped whole event blocks — the biggest find
[script_convert/converter.py](../../script_convert/converter.py) `_parse_source` only recognized a bare `end`
line. Shivering Isles scripts end blocks with `End ;OnActivate`, `End GameMode`, etc. — the End line fell
into the block body and the block was **silently discarded** (and a following `Begin` also discarded the
accumulated previous block). 15 scripts were affected, 11 of them quest-critical: the entire OnActivate
of `SE09AddItemsScript` (four `SetStage SE09` calls — **SE09 Ritual of Ascension was uncompletable**),
`SE02OrcCaptainScript` OnDeath, `SEDoorToShiveringIslesScript`, `SE02GatekeeperScript`,
`SERelmynaVerenimScript`, `SEJayredIceVeinsScript`, `SE09AltarScript`, `SE09BodyPartActivatorScript`,
`BejeenScript`, `EyeOfNocturnalScript` (Daedric quest Nocturnal), `SE04FelldewScript`.
**Downstream casualties:** SE10 stage 3 and SEObelisks stage 90 are set by SE09's stage-200 result — three
quests healed by one fix. Fix: recognize `End` + trailing comment/label, close an open block when a new
`Begin` starts, keep an unterminated final block.

### 2. Reserved-EditorID properties never bound in VMAD (MS14 uncompletable)
`_safe_property_name` renames a quest EditorID that collides with a vanilla Skyrim script (`MS14` →
`myMS14`) in the generated Papyrus, but the VMAD binders looked the **sanitized** name up as an EditorID,
found nothing, and silently skipped the binding → `myMS14` was None at runtime and every
`myMS14.SetStage(...)` in 8 dialogue fragments plus the attached scripts did nothing. **MS14 (Nothing You
Can Possess) was uncompletable.** Fix: `resolve_property_formid()` in
[script_convert/constants.py](../../script_convert/constants.py) reverses the `my` rename on lookup miss; used by
both the INFO-fragment binder ([tes5_import/dialogue/converter.py](../../tes5_import/dialogue/converter.py)) and the
object-script binder ([tes5_import/base/object_scripts.py](../../tes5_import/base/object_scripts.py)). Verified: TIF props
now bind `myMS14 → 01017606`, and SE09AddItemsScript's props now include `SE09` + all activator refs.

### 3. `StartConversation target topic` discarded the topic (`Say(None)`)
Scripted NPC↔NPC conversations are how several quests advance: Bejeen/WeebamNa's talk sets DANocturnal
stage 48, the Jauffre/Martin council sets MQ12 stage 26, the Llevana scene sets MS10 stage 79, Kaneh/Mirel
sets SE06 stage 30, plus SE05/SE11 scenes. The converter emitted `ref.Say(None)` — topic gone, result
fragment never fires. Fix: route through the SayTo path — `ref.Say(TopicProp)` with the Topic property
registered for VMAD binding (verified: `BejeenREF.Say(DANocturnalConvo1)` with bound Topic props).

### 4. LIGH records never got their object-script VMAD
`convert_LIGH` was the only script-capable converter that hand-rolls its header and never spliced
`get_object_vmad()` — so `SE06FlameOfAgnonSCRIPT` (sets SE06 stages 9/190, the Flame of Agnon mechanic)
was converted+compiled but attached to nothing. Fixed in
[tes5_import/record_types/items.py](../../tes5_import/record_types/items.py); all other types go through
`_common_header_subs`, which splices it.

### 5. QUST VMAD declared fragments the .psc doesn't define
The importer's fragment filter counted a whitespace-only (`"\r\n"`) stage result script; the psc
generator's filter (`script.strip()`) didn't. `TES4_QF_E3` and `TES4_QF_SEObelisks` VMADs referenced a
`Fragment_Stage_0100_Item_0` that doesn't exist. Fixed by aligning the importer filter
([tes5_import/dialogue/converter.py](../../tes5_import/dialogue/converter.py) `_quest_stage_fragments`).

### 6. Inherited bark gate dead-ended conversation-revealed choice topics (SE36 froze)
The bark-choice promotion stamps the revealing greeting's timing gate onto the choice topic's INFOs. SE36's
"story" choice is offered **ungated from a conversation topic** and *also* from a `GetStage==15` reminder
greeting — the inherited `GetStage(SE36)==15` gate made the line unspeakable on the conversation path, and
stage 15 is set *by that line*: the quest froze at the start. SE02's stage-60/80 `GetStageDone` self-gates
are the same family. Fix: a choice target that also has a non-bark reveal path is no longer
promoted-and-gated; its TCLT conversation link (Oblivion's own shape) stays authoritative.

### 7. Compile fixes surfaced by restoring the dropped blocks
`GetForceSneak`/`GetKnockedState` had no mapping (now `IsSneaking`/`IsBleedingOut`), and the Say-duration
approximation assigned `0.0` to an Int (TES4 `short`) variable. **All 11030 scripts now compile
(11030/11030, 0 TODO regressions — 2 `;TODO` markers total, same as before).**


## Quest script conversion: defects found and fixed
<a id="quest-script-conversion-defects"></a>

Per-round fix log from the quest-script audit, and the build verification
that followed each one.

## Defects found and fixed (round 9)
<a id="defects"></a>

### R9-1. `GetCurrentAIPackage == <type>` was the literal 0

TES4's `GetCurrentAIPackage` returns the running package's **type code**, not
its form — 5 = Wander, 6 = Travel, 0 = Find (xEdit `wbPackageTypeEnum`,
`wbDefinitionsCommon.pas`). A handler already converted the form-comparison
spelling (`== SomePackageEditorID`) onto vanilla `Actor.GetCurrentPackage()`,
but numeric comparands fell through to `0`, with a code comment calling them
"TES4 package-TYPE codes with no Skyrim equivalent".

That is true as far as it goes — neither vanilla `Package.psc` (which declares
only `GetOwningQuest` and `GetTemplate`) nor SKSE exposes a package's type. But
it does not follow that the *test* is unreachable. **The set of packages an
actor can be running is fixed at conversion time by its own `AIPackage` list**,
so a type test is exactly an equality against that actor's packages of that
type. Scoping to the actor's own list is what makes it tractable: the plugin has
1,820 Wander packages overall, but the affected actors carry 1-8 apiece.

14 of the 17 sites were numeric and dead. What they gated:

* `MG17Script` — `MG17Necro1.GetCurrentAIPackage == 5` is the trigger for
  Falcar's **entire flee sequence**; with it false, `FalcarFlee` never left 0,
  so Falcar never fled to the ruins, the three battlemages were never
  de-essentialised, and stage 30 was unreachable.
* `CaminaldaScript` — `== 5` gates Arielle attacking Caminalda. (Both of
  Caminalda's packages are Wander, so the reconstruction is exact.)
* The 5 SE10 seducer/saint force-greets and `SEBruscusDannusSCRIPT`'s
  find-package check, all `!= 5` "not currently wandering" guards.

**Fix.** `CrossRefGraph.get_actor_packages_of_type()` (named receiver, following
the ACHR/ACRE `NAME` chain) and `get_script_owner_packages_of_type()` (bare
call, resolved through `SCRI`), backed by two new scan indexes — `pack_type`
from `PKDT.Type` and `actor_packages` from `AIPackage[n]`. `==` emits an
OR-chain, `!=` an AND-chain. Unresolvable receivers return `[]` and keep the old
no-op, which is what `MS40Script` (a quest script with no attached actor) does.

`;NE: GetCurrentAIPackage` sites: 14 → **1**.

**The trap this exposed — and it is the important part.** The `.psc` is written
by the *script* stage, which builds its `CrossRefGraph` with
`load_from_export()`. The VMAD is written by the *import* stage, which
**hand-builds its own graph** from `all_records` and never calls that loader. So
the import's graph had no `pack_type`/`actor_packages`, `_packages_of_type`
returned `[]` there, and the converter took a **different branch inside the
import than it had when the .psc was written** — emitting a script that reads
six `Package` properties beside a VMAD that declares none of them. Every one
would have been `None` at runtime, i.e. exactly as dead as the `0` it replaced,
while looking fixed in the source. Anything added to the CLI scan must be
mirrored into `import_main`'s hand-built graph or the two stages silently
disagree. Verified with `tools/script/vmad_probe.py`: `TES4_MG17Script` 25 → **31**
bound properties, all six packages resolving.

### R9-2. `GetPlayerControlsDisabled` was the literal 0

Same shape as R6-2/R6-3: a `_NO_OP_FUNCS` entry and a `_convert_expression`
fallback both returned `0`. Because the function takes no arguments it is
*always* read bare, so the fallback always won.

Flattening it is not neutral, and that is what makes it a defect rather than a
gap: a constant `0` makes `== 1` permanently **false** *and* `== 0` permanently
**true**, so a script that polls the state gets both halves of its own
sequencing wrong. All 3 sites are `MG18Script`, the King of Worms confrontation:

```papyrus
If (0 == 1)                    ; was: if GetPlayerControlsDisabled == 1
  MannimarcoRef.Say(GREETING)  ; never fired — Mannimarco never spoke
...
If (0 == 0)                    ; the retry guard, now permanently true
...
If (doonce == 6)
  If (0 == 0)                  ; was: if GetPlayerControlsDisabled == 0
    MannimarcoRef.StartCombat(Player)   ; fired immediately
```

**Fix.** Skyrim has no getter, but the converter owns **both writers** as
natives, so the state is shadowed into a synthesized `TES4ControlsDisabled`
global (the `TES4GoldFenced` pattern, `_create_tes4_special_records`). The read
returns `GetValue()`; `_shadow_controls_writes` splices a `SetValue(1)`/`(0)`
after every emitted `Game.{Disable,Enable}PlayerControls()`.

Two details the build forced:

* **The shadow write cannot be returned from the call handler.** A trailing
  source comment is appended to whatever the handler returns
  (`DisablePlayerControls ; time to watch a cut scene`), which would strand the
  second line behind the comment. It is spliced in `_postprocess_lines` instead
  — which runs *before* the property block is emitted, so the late
  `_property_refs` registration is still picked up.
* **The shadow must not be gated on a same-script read.** MG18's reader and its
  two writers are three different scripts (`MG18MannimarcoSpellScript1`/`2`
  carry the writes), so a same-script gate would have shadowed nothing at all.

162 shadow writes across 76 files; 3 reads. `TES4ControlsDisabled` = `0118E956`,
bound on `TES4_MG18Script` — verified in the built ESM.

### R9-3. `SetForceRun` carried an inverted FUNCTION_MAP entry

Not a live defect — a latent one. `FUNCTION_MAP['setforcerun']` was
`('SetDontMove', ...)`, i.e. "force this actor to run" mapped onto "this actor
cannot move", the exact inverse. It was unreachable only because a dedicated
handler (SpeedMult 150/100) runs first; all 37 source sites emit the handler's
form, and `SetDontMove` appears only from `SetRestrained`. Removed, with a note,
so a future reordering cannot resurrect it.

---

## Defects found and fixed (round 7)
<a id="defects-2"></a>

### R7-1. `extends Actor` on non-actor records — 67 scripts never ran at all

`MS46Script` reads `goblinheadCrackedWood.playerhasme`, so the audit followed
into `GoblinHeadScript` — which is attached to a **WEAP** (`GoblinShamanStaff`)
yet was emitted as `extends Actor`.

Papyrus binds a script to a form only when the declared base type matches. An
`extends Actor` script on a WEAP/ACTI/CONT/DOOR is rejected outright and never
attaches, so **nothing in it runs** — no events, no poll, no properties. It is
silent: the script compiles cleanly (`Actor extends ObjectReference`, so every
`OnActivate`/`OnContainerChanged` inherits fine), which is why all 15,959
compiles passed and no earlier round noticed.

Most of the damage came from `_infer_extends`, which **overrides**
`get_extends_class`'s correct answer after a bare-call pre-scan. Four
independent causes, all fixed:

1. **The function set is unsound.** `_ACTOR_ONLY_FUNCTIONS` shares **14**
   entries with `_OBJREF_SHARED_FUNCTIONS` — `GetDistance`, `AddItem`,
   `GetItemCount`, `Say`, `PlaceAtMe`, `SetScale`… all declared on
   `ObjectReference` too. The call-site emitter already subtracts that set
   (and R3-5 recorded the same trap for `PlaceAtMe`); `_infer_extends` did not.
   `GetDistance` alone upgraded **101** scripts.
2. **The scan read comments and string literals.** `MessageBox "…not kill
   them!"` (`DAMalacathStatueScript`), `;StartCombat to get the scene rolling`
   (`SE09AltarScript`), `; evp the post guards` (`ICUmbacanoExitDoorScript`) —
   each upgraded a DOOR/ACTI script on prose alone.
3. **A local named like a function.** `MS05DreamworldAmuletScript` declares
   `short isEquipped`; reading it is not a call.
4. **Actor-parameter events.** In `begin OnEquip`, TES4's implicit subject is
   the **wearer**, not the item. The five `MGBloodwormHelmScript*` helms ride on
   **ARMO** records; the upgrade made them unbindable, and the bare `addspell`
   was emitted as `(Self as Actor).AddSpell(...)` — `None` on an ARMO — so the
   Bloodworm Helm's entire effect was lost twice over. Now routed onto the
   event's own `akActor` via the existing `_current_event_actor_param`.

A **fifth** cause lived one layer up, in `get_extends_class` itself: it scanned
attachments and returned on the **first** actor it found. `NoActivationScript`
is attached to both a **DOOR** and an **NPC_**, so every DOOR copy was unbound —
and its body is an empty `begin OnActivate`, which in Oblivion exists purely to
*consume* the activation. The doors it was meant to seal became activatable.
The base type must be one **every** attaching record can bind, so a mixed set
now resolves to the shared `ObjectReference`; it is the only such script in the
corpus, and the 720 genuinely actor-only scripts are untouched.

Confirmed against the user's last in-game run rather than by inference. The
Papyrus log carries **"Unable to bind script … because their base types do not
match"** for **108** distinct scripts; of the **63** that belong to this
export, **every single one is `extends Actor`** and **zero** scripts with any
other base type failed:

```
TES4_GoblinHeadScript       -> 1A08564B, 1A08565D, 1A08567F, 1A02FEAC, ...
TES4_SE04BarrierScript      -> 1A01344F
TES4_MS09ArnorasChestScript -> 1A093554
```

Casualties include every `Dark*DeadDropScript` (the Dark Brotherhood's contract
hand-off chain), `DarkSanctuaryDoorScript`, the Daedric shrine statue scripts
(`DAMalacathStatueScript`, `DAPeryiteStatueScript`, `DASanguineDoorScript`),
`DABoethiaCageOpenScript01`, the seven `Publican*` inn triggers, the Arena trap
scripts, and `MS49KvatchLadderScript`.

After the fix all **63** stay `ObjectReference`, while the 33 scripts that
genuinely call an Actor-only function on themselves (`SEShambles2`'s bare
`getdead`, `DAPeryiteIlvelScript`'s `setghost`, `NunTadeenScript`'s
`setunconscious`) still upgrade correctly.

`GetDeadCount`/`SetEssential` were additionally excluded from the *inference*
(a new `_ACTORBASE_ARG_FUNCTIONS` set): both name their target as an argument
and are `ActorBase` methods in Skyrim, so they say nothing about the calling
script's type. `saa`/`gaa` join them — `SetAlpha` is Actor-only, but Oblivion
lets any reference call it and simply does nothing off an actor
(`SE32GhostObject` rides on INGR/KEYM), and the call site already degrades the
bare form to the same no-op. They all stay in `_ACTOR_ONLY_FUNCTIONS`, because
the call-site cast still needs them.

---

## Defects found and fixed (round 6)
<a id="defects-2-2"></a>

### R6-1. Every bare `begin MenuMode` body was deleted

`begin MenuMode <id>` fires only while one specific menu is open (1014 =
lockpicking, 1030 = class menu), and the converter correctly refuses to run
those: MQ01's id'd blocks `setstage MQ01 70`/`84` unconditionally, so merging
them into the poll blew the tutorial's whole stage machine on the first tick.

But the same rule was applied to the **bare** form, and the two are not the same
thing. Censused over the corpus, **not one bare block is a menu-specific
trigger** — all 20 are time-and-inventory bookkeeping that Oblivion runs on the
frames where GameMode does *not*, i.e. wait/sleep and the inventory screen.
Several say so in their own comments:

| Script | Bare MenuMode body | What was lost |
|---|---|---|
| `MelisandeScript` | `set MS40.cureready to 1` + `RefreshTopicList` | the **only** 0→1 write in the plugin — MS40's vampirism cure could never be handed over |
| `Dark09RetirementScript` | `set GotFinger to 1` | the only writer; Phillida's finger never advanced the quest |
| `Publican*` ×7 | rent-hour counter | rented rooms never expired |
| `SEMiriliUlvenSCRIPT` | 195 lines of SE37 item checks | the whole block |
| `ErthorScript` | its comment reads *"contingency if player is waiting/resting"* | Erthor's package contingency |
| `SE02OrcCaptainScript` | guarded on `isTimePassing` | the early-scene skip |
| `SE42Script` | caliper count → gold | the barter value |
| `GandredhelScript` | `RefreshTopicList` at Acrobatics 70 | the topic reveal |

**Fix.** A bare, non-sleep `begin MenuMode` is merged into `gamemode_body` at
the point it appears in source, so it is emitted as part of the same OnUpdate
pass. That reproduces the union Oblivion actually had (GameMode + bare MenuMode
together covered every frame) instead of half of it, and inherits the poll, the
R5-3 early-`Return` re-arm and the quest gate for free. `_has_gamemode` also
had to account for it, or a script whose only block is a bare MenuMode
(`SE42Script`, `DAOghmaInfiniumScript`) would get no loop at all.

The two exceptions keep their own routes: the `isPCSleeping` idiom still becomes
`OnSleepStart`/`OnSleepStop`, and menu-ID blocks stay commented. Dropped bodies
21 → **5**, all genuinely id'd.

Checked before merging: no bare body misbehaves when run on an ordinary frame.
They are all idempotent state machines gated by their own doonce/stage
variables, and the one that reads a menu (`DAOghmaInfiniumScript`'s
`getbuttonpressed`) already resolves to `-1` under the round-3 rule, so no
branch matches and nothing fires. The `Publican*` rent counter appears in *both*
blocks in the original — running both in one pass still advances the hour
exactly once, because the first copy rewrites `renthour` to `GameHour` and the
second's `(renthour + 1) < GameHour` is then false.

### R6-2. `IsPCAMurderer` was the literal `0` — the Dark Brotherhood could never start

A `_convert_expression` fallback returns `'0'` for a list of argument-less
commands that have no equivalent. `ispcamurderer`/`getpcismurderer` were on it —
but they have a **real handler** in `_emit_function`. Because they genuinely
take no arguments they are *always* read bare, so the fallback always won and
the handler was unreachable dead code.

`DarkBrotherhoodScript` is the entry point of the whole questline:

```papyrus
If 0 == 1                    ; was: if IsPCAMurderer == 1
  Dark01Knife.Start()
  LucienLachanceMurderRef.Enable()
```

So Lucien Lachance never appeared after the player's first murder and
`Dark01Knife` never started.

**Fix.** Route the bare read to the same crime-gold reconstruction the handler
uses, and delete the two entries from the fallback list. The handler itself was
*also* wrong and is now corrected to match: it returned
`GetCrimeGoldViolent() > 0`, which is R4-1's **Attack** test — any violent
bounty at all, so a bar brawl would have made the player a "murderer". Murder is
the 1000-gold band (`TES4_MURDER_BOUNTY`), the same constant the importer writes
into `TES4CyrodiilCrimeFaction`'s CRVA. That faction carries Track Crime and
murder=1000 (R4-3), so the reconstruction has something to read.

Verified in the built ESM: `TES4CyrodiilCrimeFaction` → `0118E956`,
`Dark01Knife` → `010224EB`, `LucienLachanceMurderRef` → `010177D2`, all bound.

### R6-3. `GetDetectionLevel` was the literal `0` — 56 dead detection tests

Same fallback, same shape. `GetDetectionLevel` is `<observer>.GetDetectionLevel
<target>` — per UESP's function table opcode `0x10B4`, **1 param (Actor)**,
receiver "Actor Reference", i.e. structurally identical to `GetDetected`
(`0x102D`), which round 3 already maps onto `IsDetectedBy` with a receiver/
argument swap.

Flattening it to `0` was defensible only if scripts read the level numerically.
Censused over the plugin: **not one does.** All **56** sites are threshold tests
— `>= 2`, `>= 3` or `== 3` — pure "is the target detected" questions. What they
gated is not cosmetic:

* all **7** of `Dark04ExecutionScript`'s guard-aggro triggers (`If 0 == 3`);
* the 8 Dark Sanctuary assassins' reaction to the player
  (`DarkVicenteScript`, `DarkSanctuaryAssassins`);
* `BaenlinScript`'s and `GrommScript`'s murder-witness checks;
* `Dark09RetirementScript`'s bodyguard, `Dark12JghastaScript`,
  `Dark05AssassinatedScript`, the bandit sentries' challenge, and 9 SE guards.

**Fix.** The same swap `GetDetected` uses — `<target>.IsDetectedBy(<observer>)`.

One trap the CK compiler caught, and the reason a naive mapping is worse than
useless here: **the threshold must be rescaled, not merely wrapped.** A bare
`Bool >= 2` is rejected outright (*"cannot relatively compare variables of type
bool"*), and the generic `_BOOL_CMP_RE` pass wraps it as `(... as Int) >= 2` —
where `true as Int` is **1**. That compiles and is permanently false, trading
one dead form for another, and it would have silently killed the 36 `>=` sites
while appearing to fix them. The emission is therefore
`((X.IsDetectedBy(Y) as Int) * 3)`, giving TES4's own 0-or-3, which satisfies
every threshold the plugin uses exactly when detected:

| emitted | detected (3) | undetected (0) |
|---|---|---|
| `== 3` (18 sites) | ✓ | ✗ |
| `>= 2` (17 sites) | ✓ | ✗ |
| `>= 3` (19 sites) | ✓ | ✗ |

The argument-less spelling keeps the `0` no-op — with no target named there is
nothing to ask `IsDetectedBy` about, the same reasoning as `IsActorDetected`.

### R6-4. A quoted `PlaySound` minted a dead, unbindable property

Vanilla writes the EditorID quoted (`PlaySound "AMBBaenlinDeath"`). The handler
registered the **raw** argument — quotes included — while `_convert_expression`
stripped them for the emitted call. `_safe_property_name` then turned each quote
into an underscore, so every such site declared a second property beside the
real one:

```papyrus
Sound Property _AMBBaenlinDeath_ Auto    ; declared, never referenced
Sound Property AMBBaenlinDeath  Auto     ; the one actually used
```

**75 dead declarations across 23 files, referenced in none** — and unbindable,
since no record is named `"AMBBaenlinDeath"` with quotes. Same class of artifact
as round 2's `Game_GetPlayer__` (R2-5). Now the name is stripped before both the
registration and the conversion, so the receiver can never keep its quotes
regardless of whether the EditorID resolves. 75 → **0**; verified in the built
ESM that `AMBBaenlinDeath` and `AMBBaenlinMiss` bind to `01064209`/`0106420A`.

---

## Defects found and fixed (round 5)
<a id="defects-2-2-2"></a>

### R5-1. `AddTopic` in a script body never opened the topic's gate

Skyrim has no `AddTopic`, so the pipeline re-expresses Oblivion's dialogue
visibility model as one `TES4Unlock_<topic>` global per explicitly-added topic
(`tes5_import/dialogue/unlocks.py`). INFO fragments and quest-stage fragments both
emit `TES4Unlock_X.SetValue(1)`. A **script** `AddTopic X` — the third reveal
route — emitted an inert `;NE: AddTopic` comment instead.

`dialog_unlocks.py` justified this in a code comment: script AddTopics matter
"for BRANCH VISIBILITY, not gating… `AddTopic` has no Skyrim equivalent, so
script_convert emits it as an inert comment and NOTHING would ever open a gate
placed on them". That reasoning is circular — nothing opened the gate *because*
the emission was inert.

Measured over the whole corpus: **no gated topic is orphaned** (all 473 have at
least one other revealer), so nothing was permanently invisible and the
conclusion held by luck. But **19 gated topics are AddTopic'd from a script and
lost that route**, several of them the player's intended first encounter with
the topic:

| Script | Topic | What is lost |
|---|---|---|
| `TGReadWantedPoster` | `TGGrayFox`, `TGHieronymusLex` | reading the wanted poster |
| `TG00MysteriousNoteScript` | `TGGrayFox` | the note that opens the TG questline (it sets stage **30**; the topic's other stage revealer is stage **100**) |
| `MS45DarMaDiary` | `DarMaTopic` | finding Dar Ma's diary (fires from `OnActivate` *and* `OnAdd`) |
| `DAMephalaUlfgarScript` | `HrolUlfgarTOPIC` | Ulfgar's death |
| `Startup` | `JauffreTopic`, `SkingradTopic`, `ElderCouncilTopic`, `HouseInquiry`, `BaurusTopic` | the globally-available topics |

**Fix.** A real handler for `addtopic`: when the argument names a **gated**
topic it emits the same `TES4Unlock_X.SetValue(1)` the other two routes use;
an ungated topic (never explicitly added, or bark-revealed — both deliberately
ungated by the plan) still emits the comment, because it is already visible and
has no global to set. The topic→global map is built from the same
`build_unlock_plan` in both `script_convert/pipeline.py` and
`tes5_import/import_main.py`, so the `.psc` property set and the VMAD bindings
cannot drift.

13 scripts now emit a SetValue; inert `;NE: AddTopic` comments 130 → **57** (all
ungated). One trap the rebuild caught: a quest stage whose result script
contains the `AddTopic` is reached by *both* the stage-reveal path and the
converter's property registration, so the same `GlobalVariable Property` was
declared twice — 60 scripts failed with *"property with `TES4Unlock_…` name
already exists"*. The QUST emitter now seeds its `declared` set from
`quest_globals` (the INFO emitter already did this).

### R5-2. Every synthesized global was unbound on **object** scripts

The converter mints properties for records that exist only in the output —
`TES4Fame`, `TES4Infamy`, `TES4GoldFenced`, `TES4CyrodiilCrimeFaction`, and now
`TES4Unlock_*`. `_resolve_props` in `tes5_import/base/object_scripts.py` binds
properties through `resolve_property_formid()`, which looks them up in
`xref.edid_to_formid` — a map built **from the TES4 export**, which by
definition never contains a synthesized record. So every one silently resolved
to nothing and the property was left unbound → `None` at runtime.

The dialogue and quest VMAD builders already inject the same registry as
`well_known_props`, which is why `QF_*`/`TIF_*` fragments bound correctly and
**only object scripts were affected** — and why round 2's verification, which
counted the 4,762 *dialogue* bindings, did not catch it.

Confirmed against the built ESM with `tools/script/vmad_probe.py` before fixing:

```
TES4_TGStolenGoodsScript   declares TES4GoldFenced   BOUND: []   MISSING: [TES4GoldFenced]
TES4_AltarofAkatosh        declares 3                BOUND: []   MISSING: all 3
TES4_QF_Charactergen       declares 8 TES4Unlock_*    BOUND: all 12
```

Not cosmetic. `TGStolenGoodsScript` is the **Thieves Guild rank-advancement
driver** and all ten of its gates read `TES4GoldFenced.GetValue()`; a `None`
property throws on the first tick, so the script died immediately and **no TG
rank ever advanced**. `TG09ArrowScript` and `TG10BootsScript` (round 5 #14/#15)
read the same global and looked clean precisely because the defect was one layer
below the script text.

**Fix.** `_resolve_props` consults the registry before falling through to the
EditorID lookup, importing `WELL_KNOWN_PROPERTIES` directly from
`synth_records` (its defining module). The registry is populated in the main
process during phase 0 and the object-script plan is also main-process, so no
worker sees an empty copy.

### R5-3. An early `return` permanently killed the polling loop

TES4 `return` ends only **this frame's** `GameMode` pass — the script runs again
next frame. The converted `OnUpdate` is one-shot and self-rescheduling, so a
`Return` that skips the trailing `RegisterForSingleUpdate` stops the script for
the rest of the game.

The `!IsRunning()` guard the emitter writes gets this right (it re-registers
*before* its Return), but a `Return` from the converted body did not — and
`if GetStage X < N / return` is a standard Oblivion early-out. **115 such
Returns across 96 scripts**, including the MG quest drivers this round sampled
(`MG01`, `MG02`, `MG05`, `MG06`, `MG08`, `MG12`, `MG17`, `MG18`), `MQ16Script`,
`MS04`/`MS09`/`MS14`, and the house-furniture scripts.

The starkest case is `MG05RockScript`, which fires one shock bolt per tick and
uses `return` to serialize six of them. Without the re-register it fired
**exactly one bolt, ever**.

**Fix.** `_reregister_before_returns()` rewrites every bare `Return` in the
emitted GameMode body to re-arm the poll first, using the same form the
fall-through path uses — `Is3DLoaded()`-gated for object/actor scripts (whose
poll is *meant* to stop on unload), unconditional otherwise. A value-returning
`Return <x>` (OBSE user function) is deliberately not matched. 115 → **0**.

### R5-4. `IsPlayerInJail` asked about faction expulsion

`IsPlayerInJail` (TES4 opcode `0x10AB`, 0 params) means "is the player serving a
jail sentence". All four spellings (`IsPlayerInJail`, `GetPlayerInJail`,
`IsPlayerInPrison`, `SentToJail`) emitted
`TES4CyrodiilCrimeFaction.IsPlayerExpelled()` — an unrelated question, and one
that is never true: nothing expels the player from the synthesized crime
faction, so every site read false forever.

Skyrim has the exact native. Vanilla `Actor.psc`:

```papyrus
; Is this actor currently arrested?
bool Function IsArrested() native
```

(the condition-function form is `GetArrestedState`, index 656). All **9** TES4
sites across 7 scripts are jail mechanics — the two prison cell-door scripts,
the Leyawiin jailor, Amusei (whom you meet in a cell), `MS09Script`, the
tutorial's prison start, and `TG00FindThievesGuildScript`, whose stage 10 is the
**entry point of the entire Thieves Guild questline**. Now
`Game.GetPlayer().IsArrested()`; the 2 genuine `IsPlayerExpelled()` sites
(`FGExpulsionScript`, `ArcaneUGateScript`) are untouched.

---

## Defects found and fixed (round 4)
<a id="defects-2-2-2-2"></a>

### R4-1. Oblivion's three crime flags collapsed into one, and were read as gold

TES4 keeps three independent per-faction booleans — `GetPCFactionMurder`,
`GetPCFactionAttack`, `GetPCFactionSteal`. Murder and Attack were **both** mapped
onto `GetCrimeGoldViolent()`, making them indistinguishable, and all three were
compared against the source's `== 1`, which against a *gold amount* means
"exactly 1 gold of bounty" — a value no crime ever produces.

The collision is not theoretical: two scripts test murder and attack in the same
`if`/`elseif` chain, deliberately distinguished.

* `FGExpulsionScript` — `GetPCFactionMurder BlackwoodCompanyFaction` expels
  immediately; `GetPCFactionAttack BlackwoodCompanyFaction` only expels once
  `FGD08Infiltration` reaches stage 70. With both branches identical the elseif
  was unreachable and the stage gate destroyed.
* `TGCastOut` — the entire Thieves Guild expulsion driver. Stage 20 (attack) and
  stage 30 (murder, which also increments `MurderCount`) were the same test, so
  stage 30 was dead.

Census of all 64 call sites (SCPT/INFO/QUST) settles how to fix it: **every read
is `== 1` and every write is `0`.** Not one site writes 1 — the engine sets these
flags, scripts only test and clear them. So a synthesized global (the round-2
`TES4GoldFenced` pattern) cannot work: nothing would ever set it.

Skyrim keeps `GetPCFactionMurder` (`0x10C3`) and `GetPCFactionAttack` (`0x10C7`)
as condition functions but exposes no Papyrus native for any of the three, and
SKSE adds none (checked `references/skse64-master`). It also **dropped Steal
entirely** — opcode `0x10C5` is `GetPCEnemyofFaction` in Skyrim, not
`GetPCFactionSteal`. What *is* reachable is the crime-gold split, and Skyrim's
CRVA prices murder and assault separately:

| | Murder | Assault | Trespass | Pickpocket |
|---|---|---|---|---|
| vanilla CRVA (all 14 real crime factions) | **1000** | **40** | 5 | 25 |

The 25x gap is the discriminator. Reconstruction:

```
Steal  → GetCrimeGoldNonViolent() > 0
Attack → 0 < GetCrimeGoldViolent() < 1000
Murder → GetCrimeGoldViolent() >= 1000
```

Constants live in `script_convert/constants.py` (`TES4_MURDER_BOUNTY` etc.) and
the importer writes the same numbers, so the two sides stay in step.

### R4-2. TES4 FACT flags were dropped for **every** faction in every plugin

Found while checking whether the crime-gold plan could work at all. TES4's FACT
`DATA` is a **1-byte** field (xEdit `wbDefinitionsTES4`: Hidden from Player /
Evil / Special Combat), but the exporter guarded on `len(data.data) >= 4` and
unpacked a U32:

```python
if data and len(data.data) >= 4:                       # never true
    lines.append(f"DATA.Flags={struct.unpack_from('<I', data.data, 0)[0]}")
```

So `DATA.Flags` was absent from **all 476 FACT records** — verified 0 of 476 in
the export, and measured at exactly 1 byte in all 204 Nehrim.esm factions
(Nehrim being the locally available TES4 file; the layout is identical). After
the fix: 476 of 476, with values 0/1/2/3/5/7, matching the 3-bit layout.

### R4-3. The FACT CRVA struct was packed with the wrong layout

`convert_FACT` packed `'<HHHHIfI'`. That is the same 20 bytes as the real
struct, so it never errored — and misaligned every field. The correct layout,
per xEdit and confirmed byte-for-byte against Skyrim.esm's
`WERoad12HorsemanFaction` (`0101 E803 2800 0500 1900 0000 0000003F 6400 E803`):

```
Arrest U8, Attack On Sight U8, Murder U16, Assault U16, Trespass U16,
Pickpocket U16, Unknown U16, Steal Multiplier Float, Escape U16, Werewolf U16
```

Consequences for every converted faction: the leading `H` swallowed both U8
booleans, so **no converted crime faction ever arrested**, and murder / assault /
trespass / pickpocket were all left 0 — meaning `GetCrimeGoldViolent()` and
`GetCrimeGoldNonViolent()` returned 0 forever. R4-1's reconstruction would have
been dead on arrival without this.

The same function's flag handling was wrong in two further ways:

* **Straight passthrough of the TES4 bits.** The games number them differently:
  TES4 bit 1 is *Evil* but TES5 bit 1 is *Special Combat*, and TES4 bit 2
  (Special Combat) landed on TES5 bit 2 (unused). Now mapped explicitly —
  TES4 bit 0 → TES5 bit 0 (Hidden From NPC), TES4 bit 2 → TES5 bit 1.
* **The "Evil → Crime flags" line set the *Ignore* Crimes bits** (7-11, 13, 16),
  the exact opposite of its intent: it told the engine to ignore murder,
  assault, stealing, trespass and pickpocket. And **Track Crime (bit 6) was
  never set on anything**, which Skyrim requires before it accumulates crime
  gold at all.

Oblivion has no per-faction "tracks crime" flag to carry across, so the set is
derived generically in Phase 0 (`_load_crime_factions`): any faction appearing as
the argument of a `Get`/`SetPCFaction{Murder,Attack,Steal}` call in the plugin's
own scripts. On Oblivion.esm that resolves to exactly the 6 factions the scripts
test — ThievesGuild, ICWaterfrontResident, DarkBrotherhood, MagesGuild,
FightersGuild, BlackwoodCompanyFaction — and leaves the other 598 untouched.

The **synthesized `TES4CyrodiilCrimeFaction`** in `import_main.py` had the same
two bugs independently, and was missed on the first pass of this fix because it
is built by hand rather than through `convert_FACT`. It stands in for Oblivion's
single global crime faction and is the receiver for 7 of the 9 converted
`IsPlayerExpelled()` sites plus the jail calls, so it matters: it carried
`0x0001AF80` (which xEdit decodes as **IgnoreKills**) with no Track Crime, and an
all-zero CRVA in the wrong layout. Now `0x00008040` (Can Be Owner + Track Crime)
with the same vanilla amounts, matching the other six.

### R4-4. `GetPCExpelled` tested faction rank while `SetPCExpelled` set the flag

Found in `FGExpulsionScript`. The setter emitted `Faction.SetPlayerExpelled(...)`
but the reader emitted `Game.GetPlayer().GetFactionRank(f) < 0`, with a code
comment claiming *"Skyrim has no Expel/IsExpelled"* — contradicted three lines
below by the setter, and by vanilla `Faction.psc`, which declares **both**
`IsPlayerExpelled()` and `SetPlayerExpelled()`.

The pair was therefore asymmetric: `SetPlayerExpelled` sets the engine's expelled
flag and never touches rank, so nothing ever drove rank negative and **every
`GetPCExpelled` read was permanently false**. In `FGExpulsionScript` that is the
guard against double-expulsion, so `booted`/`killer`/`thief` never counted.

A second copy of the same wrong mapping existed further down the file, alongside
`Expel` writing `SetFactionRank(f, -1)` — also invisible to the fixed reader.
Both now use the natives. 12 sites emit `IsPlayerExpelled()`; 0 rank-based
expelled tests and 0 `SetFactionRank(x, -1)` remain.

### R4-5. `StartConversation` with no topic was dropped — 64 silent call sites

The 3-argument form (`X.StartConversation Player SomeTopic`) correctly became
`X.Say(SomeTopic)`, but the 2-argument form fell through to
`;NE: StartConversation (no topic)` with the comment *"TES4 falls back to
greeting AI; nothing to say here"*.

Per UESP's function table (opcode `0x1056`) the signature is
`Actor, Topic (Optional)` — the topic genuinely is optional, and omitting it makes
the engine open on the **greeting**, which is a real resolvable topic
(`DIAL GREETING`, `000000C8`; 46 converted scripts already bind it). So there was
something to say.

All **64** dropped sites are the standard `<npc>.StartConversation Player`
walk-up beat — `FGC01Script`'s Pinarus commenting after the mountain lions die,
and 63 more. Now `Say(GREETING)`, the same routing the 3-argument form uses.
`Say(GREETING)` sites: 50 → **114**.

---

## Defects found and fixed (round 3)
<a id="defects-2-2-2-2-2"></a>

### R3-1. A ref null check was flattened to the literal `0`

The worst of the round. `ref > 0` is Oblivion's standard "is this ref set" test
(refs coerce to 0 when unset), and a dedicated handler already existed to turn it
into `ref != None`. But an earlier block — "ObjectReference in a numeric
comparison ⇒ TES4 undeclared variable defaulting to 0" — ran **first** and
rewrote the whole left side to `0`, so the null-check handler never saw it.

The damage was double: the test became permanently false, *and* the operator was
stranded behind the inline comment, emitting

```papyrus
If combattarget != Player && 0  ;undeclared TES4 var > 0
```

All **8** occurrences in Oblivion.esm were null checks; not one was the
name-collision case the block was written for:

* `MQ04Script` — `elseif speaker > 0` is the **entire Cloud Ruler Temple
  conversation driver**. Martin, Jauffre and Cyrus never spoke a line.
* `MQ09Script` — `elseif restrainedRef > 0` releases the first ghost blade so it
  can approach the player.
* `CGEmperorScript` — `combattarget > 0` gates the Blades' call for help when
  someone other than the player attacks the Emperor.

**Fix.** The collision fallback now skips the null-check shapes (`> 0`, `>= 1`,
`<= 0`). `<= 0` also had to be *added* to the null-check handler, which only knew
the `>`/`>=` forms — it now emits `ref == None`. 8 → 0 flattened sites.

### R3-2. `GetDetected` asked the mirror-image question

TES4 `<observer>.GetDetected <target>` is "does the observer detect the target".
Skyrim's `<target>.IsDetectedBy(<observer>)` is the reverse — vanilla
`Actor.psc` reads "returns if **this** actor is detected by the other one", and
the function's shared Morrowind documentation is explicit that the argument is
the "target NPC used to check if the **source** actor can detect them".

The mapping was positional, so receiver and argument stayed put and every one of
the **38** call sites asked the opposite question. `CharGenQuest`'s
`GlenroyRef.getdetected player` — "has Glenroy spotted the player", which
advances the Ambush-B stage — became "has the player spotted Glenroy", true the
moment the player looks down the corridor.

**Fix.** A special handler that swaps the two refs. `IsActorDetected` (0 params,
"detected by anyone") keeps its round-1 no-op; the table comment claiming
`GetDetected` means "detected by X" was wrong and is corrected.

### R3-3. `Message`/`MessageBox` printed the format specifier literally

Vanilla TES4 uses the same printf convention as the OBSE variants
(`Message "%.0f seconds to close Great Gate!", remainingSec`), and a
`_format_string_call` helper already existed to turn that into Papyrus
concatenation — but only the OBSE spellings were routed through it. Plain
`Message`/`MessageBox` went through `_quote_msg`, which keeps the first quoted
string and **discards the arguments**, so the player was shown a raw `%.0f`.

**86 call sites** (16 SCPT + 70 INFO), several highly visible: MQ14's Great Gate
countdown, the current bounty, the Dawnfang/Duskfang kill counts, and the year on
the Bruma victory statue.

Two further defects surfaced while fixing it, both caught by rebuilding:

* **The specifier regex required a digit after the dot**, so the equally common
  `%0.f` spelling never matched (`XPKnotboneFactionFixerSCRIPT`). Both sides of
  the dot are now optional.
* **A `%` that is ordinary text was consumed anyway.** With the widened regex,
  `"100% done"` matches `"% d"`; the helper consumed it and ate the following
  letter, splitting the sentence into `"100" + "one with the job"`. A specifier
  with no argument left to fill it is now left as literal text.

TES4's optional trailing **display time** (`message "Rank %.0f Fireball",
SpellRank, 10` shows the value for 10 seconds) has no Papyrus equivalent and
would otherwise be concatenated onto the text as "Fireball10", so surplus numeric
literals beyond the specifier count are dropped. 86 → 0.

### R3-4. `SetDoorDefaultOpen 0` flung the door open

The argument is a boolean — per UESP's function table (opcode `0x10D8`, 1
Integer) "a value of 1 will make the door open by default" — but the handler
ignored it and hardcoded `SetOpen(true)`. MQ16's endgame line, whose own comment
reads `; close Elder Council door`, therefore did the opposite. `OpenDoor`, which
genuinely takes no argument, is now handled separately.

### R3-5. A static marker was downcast to `Actor`, storing None

Assigning into a remote script's `ref` variable added `as Actor` whenever that
variable was declared `ref`, with the code commenting that the target script's
post-processing "may upgrade it to Actor. Add cast preemptively for safety."
That is unsafe in the other direction: MQ16 assigns two static markers into
`MQ16OblivionGate1Script.mySpawnMarker`, which stays `ObjectReference`, and
`marker as Actor` fails the downcast and stores **None** — so both endgame
Oblivion gates spawned nothing.

**Fix.** A new `CrossRefGraph.script_actor_vars` records, per script, which `ref`
variables that script *itself* calls an Actor-only method on; the cast is now
gated on membership. One trap: `_ACTOR_ONLY_FUNCTIONS` is not sound for this
question — it lists several methods `ObjectReference` also declares, and
`PlaceAtMe` (the marker's only use) is one of them. The repo already keeps
`_OBJREF_SHARED_FUNCTIONS` for exactly this, so the detection subtracts it.

The 4 casts this removed elsewhere were all redundant, not load-bearing: their
targets are `Actor`-typed properties being fed values that already extend Actor.

### R3-6. Oblivion's one Encumbrance AV is two AVs in Skyrim

Skyrim splits it: **InventoryWeight** (index 31, "collective weight of everything
in your inventory") and **CarryWeight** (index 32, "max points of weight you can
carry"). TES4 has a single Encumbrance AV and separates the two the
modified-vs-base way, giving the over-encumbered idiom

```
player.getav encumbrance > player.getbaseav encumbrance
```

— MQ01's stage 75/78 tutorial, whose own text reads "your **current**
encumbrance exceeds the **maximum** you can carry". Both sides mapped to
`CarryWeight`, comparing the cap against itself, so neither tutorial stage could
ever fire. `getav encumbrance` now resolves to `InventoryWeight`; base/set/mod
keep `CarryWeight`. All 4 call sites are in MQ01.

---

## Defects found and fixed (round 2)
<a id="defects-2-2-2-2-2-2"></a>

### R2-1. `GetAmountSoldStolen` read a live vanilla crime stat

`GetAmountSoldStolen`/`ModAmountSoldStolen` track the **gold value of stolen
goods fenced** — the Thieves Guild INFOs print it as
`"Amount fenced: %.0f gold"`. Both were mapped onto Skyrim's `"Items Stolen"`
stat, which is wrong twice over:

* it counts **items**, not gold; and
* it is a **live vanilla stat the engine bumps on every theft**, so the TG rank
  gates (`>= 50` … `>= 1000`) would trip from ordinary pickpocketing without the
  player ever visiting a fence.

Skyrim keeps `GetAmountSoldStolen` (opcode `0x10BE`, index 190) only as a
condition/console function: there is no Papyrus native, and the exe's stat-name
table has no fence-gold entry (dumped and checked — `Items Stolen`, `Pockets
Picked`, `Horses Stolen` … no `Gold Fenced`).

**Fix.** A synthesized `TES4GoldFenced` GlobalVariable, following the existing
`TES4Fame`/`TES4Infamy` pattern in `_create_tes4_special_records`. Reads become
`TES4GoldFenced.GetValue()`, writes `TES4GoldFenced.Mod(...)`. `GetPCMiscStat`
keeps `Game.QueryStat` — it is a genuine stat query named by its argument.

Affects 14 SCPT call sites + 24 INFOs. Verified: 87 emitted references, 0
remaining `"Items Stolen"`, and all **4,762 VMAD bindings resolve** to the real
FormID (`0118E955`), none null.

`getamountsoldstolen` also had to join `_BARE_NO_EQUIV_COMMANDS`: it is read
bare (`If GetAmountSoldStolen >= 600`), so with a `None` table entry it fell
through as an undefined identifier and failed 29 scripts.

### R2-2. `SCAOnActor` became `StopCombat` — the opposite direction

`StopCombatAlarmOnActor` (alias `SCAOnActor`) "stops all combat and alarms
**against** this actor". It was mapped to `StopCombat`, which per the vanilla
`Actor.psc` header "removes **this** actor from combat" — the other direction.

`player.SCAOnActor` is Oblivion's idiom for calming a mob attacking the player
(Dark19Whispers uses it to hold the player still through the Night Mother's
speech); stopping only the player's own aggression left everyone hostile.

Skyrim has the exact native, `Actor.StopCombatAlarm()`. **64 call sites** (43
SCPT, 12 INFO, 9 QUST), all now emitting it; the 50 genuine `StopCombat` calls
are untouched.

### R2-3. `ModDisposition ≤ -100` started combat with the aggressor reversed

TES4's signature is `<actor>.ModDisposition <target> <value>` and it changes the
**calling** actor's disposition toward the target. Mapping a full -100 drop to
`StartCombat` is right (disposition does not exist in Skyrim), but the emitted
call was `<target>.StartCombat(<ref>)` — inverted.

So `UngolimRef.ModDisposition player -100` made the **player** attack Ungolim
rather than Ungolim turning on the player, framing the player for the murder
Dark16Kiss wants Ungolim to commit. Now `<ref>.StartCombat(<target>)`.

### R2-4. The measure-then-deliver Say idiom spoke every line twice

Oblivion's standard two-line conversation idiom:

```
Set InfoLength to ArmandRef.Say TG01Armand1     ; returns the duration
ArmandRef.SayTo Player TG01Armand1              ; delivers it to the listener
```

Both TES4 functions speak, so converting each independently emitted two
identical `ArmandRef.Say(TG01Armand1)` calls back-to-back and **every such line
played twice** — Armand's whole TG01 briefing, the SE07A Sheogorath/Thadon
endgame, SE03's chamber chatter. **92 pairs.**

**Fix.** A dedup pass in `_postprocess_lines`: when a `ref.Say(topic)` is
immediately followed by an identical `ref.Say(topic)`, drop the first (the
measuring half) and keep the delivery. The timer charge on the preceding line —
the measuring call's only real output — is unaffected.

Two details the rebuild forced:

* **Flatten embedded newlines first.** The measuring Say arrives glued to its
  timer-charge line as a single list entry
  (`"t = 9.25  ;line length\n  ref.Say(topic)"`), so without the split the two
  Says are never adjacent entries and 0 of 92 were caught.
* **Scan a short window, not just the next line.** The author may slip a
  `Look`/`SetLookAt` between the halves (SE07A's Sheogorath/Thadon exchange), so
  the pass looks ahead 3 lines, stopping at any control-flow keyword or a new
  `;line length` charge — so two Says belonging to different beats are never
  collapsed.

Verified: 92 → **0** remaining same-topic Say pairs.

### R2-5. `Actor Property Game_GetPlayer__ Auto` in 511 scripts, bound to nothing

Three sites registered an actor-only call's **receiver** as a property ref. But
the receiver has already been converted, so it can be an expression
(`Game.GetPlayer()`), a cast (`(x as Actor)`) or a fixed event parameter.
`_safe_property_name` mangled those into declarations like
`Actor Property Game_GetPlayer__ Auto` — emitted in **511 files and referenced
in none**.

**Fix.** `_is_bindable_property()` gates the *creation* of a new entry on the
receiver being a bare identifier that is not a known non-property name
(`Self`, `akSpeakerRef`, `Game.GetPlayer()`, …). Now 0 files.

Two traps, both caught by rebuilding:

* **Locals must still be registered.** `_property_refs` doubles as the marker
  that a script-local is Actor-typed, driving the `as Actor` downcast and the
  variable's declared type. Excluding locals broke **73 scripts**
  (`TempRef.UnequipItem` in `AmuletofKingsSCRIPT`).
* **Only guard creation, not upgrade.** Promoting an existing
  `ObjectReference` entry to `Actor` is always correct and must stay unguarded.

---

## Defects found and fixed (round 1)
<a id="defects-2-2-2-2-2-2-2"></a>

### 1. `GetInCell` matched one cell instead of the whole prefix family

The big one — **167 of 396 `GetInCell` calls were permanently false.**

TES4 matches the `GetInCell` argument as an EditorID **prefix**, not an
identity, so `GetInCell Chorrol` is true in all **86** cells whose EditorID
starts with `Chorrol` (`ChorrolCastle`, `ChorrolMagesGuild`, `ChorrolExterior*`,
…). Oblivion leans on this so heavily that **62 CELL records exist purely as the
named anchor of a family** and hold no refs at all — several say so in their own
`FULL` field:

```
00024802  Chorrol           FULL=Dummy Cell
0001EFDB  CloudRulerTemple  FULL=Dummy cell for GetInCell
000319D9  Bravil            FULL=Dummy cell so GetInCell function will work
```

The converter emitted `ref.GetParentCell() == Chorrol`, comparing against an
empty anchor cell the player can never stand in — so the test never passed.
MQ02's Chorrol and Weynon Priory stage advances were both dead this way.

Census over all scripts:

| | args | calls |
|---|---|---|
| matched >1 cell (broken) | 91 | **167** |
| matched exactly 1 cell (fine) | 124 | 229 |

Largest families: `IC` 431 cells, `Anvil` 91, `Leyawiin` 91, `Skingrad` 87,
`Chorrol` 86, `Bravil` 85, `Bruma` 72.

**Fix.** `CrossRefGraph.get_cell_family()` resolves the prefix family from the
CELL index; the `GetInCell` handler emits a generated per-family helper
(`TES4_IsIn<Name>`) and calls it. A single-cell match still emits the plain
equality, so nothing regressed. Families are keyed case-insensitively, so
`Chorrol` / `chorrol` and `SummitMistManor` / `Summitmist` share one helper.

Files: `script_convert/cross_ref.py`, `script_convert/converter.py`,
`script_convert/pipeline.py`.

Result: **87 distinct helpers across 109 scripts, 302 call sites.**

Two traps hit while implementing this, both worth remembering:

* The CK compiler rejects a local named `parent`
  (*"function variable parent already defined in the same scope"*), so the
  helper uses `TES4_parentCell`.
* **Fragment scripts assemble their own file.** `QF_*` (quest stage) and `TIF_*`
  (INFO) bodies called the helper but never emitted it, giving
  *"undefined function TES4_IsIn…"*. `convert_fragment` now preserves
  `_cell_families` across calls and both pipeline paths append
  `get_cell_family_helpers()` — the INFO path only when `has_script`, since
  `conv` does not exist otherwise.

### 2. `IsActorDetected` became the player detecting themselves

`player.IsActorDetected` converted to
`Game.GetPlayer().IsDetectedBy(Game.GetPlayer())` — always true.

Cause: `IsActorDetected` and `GetDetected` were both mapped to `IsDetectedBy`,
and the argument-less form fell through to the `_DEFAULT_ARGS` player default.
But per UESP's function table the two have **different arity**:

| function | opcode | params |
|---|---|---|
| `IsActorDetected` | `0x10B5` | **0** |
| `GetDetected` | `0x102D` | 1 (Actor) |

`IsActorDetected` means "am I detected by **anyone**", which Skyrim has no
primitive for (`IsDetectedBy` needs a specific observer). It is now a no-op
emitting `;NE: IsActorDetected (no Skyrim equivalent)`, matching how
`GetDetectionLevel` is already handled.

Affected: `Dark07MedicineScript`, `SEHirrusClutumnusSCRIPT` (whose variable is
literally named `someoneDetected`).

### 3. `WakeUpPC` became `Game.ForceThirdPerson()`

`WakeUpPC` kicks the player **out of sleep**. It does not move them, change the
camera, or play an animation — the old mapping did none of the right things, and
the code comment above it (`-> RestoreActorValue("Health", 0)`) described a
third unrelated thing.

Skyrim genuinely has no equivalent: no native in `Game`/`Debug`/`Actor`/
`ObjectReference` ends an active sleep, and SKSE registers none either (grepped
every `NativeFunction` in `references/skse64-master`). Vanilla's closest case,
the Dark Brotherhood abduction (`pDBEntranceQuestScript`), does not wake the
player with a function — it runs its whole sequence *inside* `OnSleepStart`.

That is exactly where the converted body already runs: **all 5 TES4 call sites
sit in a MenuMode block reading `isPCSleeping`**, which this converter routes
into `OnSleepStart`/`OnSleepStop`. So the code the script wanted to run on
waking *does* run at the right moment; only "cut the sleep short" has no target.
Now a no-op with `;NE: WakeUpPC (no Skyrim equivalent; body runs in
OnSleepStart)` rather than an invented side effect.

Call sites: `VampireScript`, `MS02Script`, `MS05DreamworldAmuletScript`,
`RufioDieScript` (×2).

---

## Build verification (round 7)
<a id="build-verification"></a>

Round 7 touched `script_convert/` only (`converter.py`, `constants.py`,
`cross_ref.py`), so `--scripts-only` is the matching build. No new records, so
no `--import-only` was needed.

`python convert.py -f Oblivion.esm --scripts-only`:

```
SCPT: 2393/2393 converted
INFO: 5629/5629 fragments
QUST: 1870/1870 stage scripts
Total: 9892 converted, 0 errors, 2 TODOs
Compilation: 15959/15959 succeeded, 0 failed
```

Measured in `output/`, before → after:

| Fix | Before | After |
|---|---|---|
| Scripts the Papyrus log logged as unbindable, still `extends Actor` | 67 / 67 | **0 / 67** |
| SCPT-derived `extends Actor` scripts | 885 | **736** |
| `GoblinHeadScript` (WEAP), `SE04BarrierScript` (ACTI), `DAMalacathStatueScript` (ACTI) | `Actor` | **`ObjectReference`** |
| `MGBloodwormHelmScript*` (ARMO) ×5 | `Actor` | **`ObjectReference`** |
| `NoActivationScript` (DOOR + NPC_) | `Actor` | **`ObjectReference`** |
| Helm `addspell`/`removespell` routed onto the wearer | 0 | **5** |

The before-list is not inferred: it is the set of scripts the user's last
in-game run logged as *"Unable to bind script … because their base types do not
match"* (108 distinct names, 67 of them belonging to this export). Every one of
the 67 was `extends Actor` and **no** script with any other base type appeared
in that list, which is what identified the cause.

Tests: `tests/test_script_converter.py` **202 passed** (10 new — three classes:
`TestInferExtendsDoesNotBreakBinding`, `TestBareActorCallUsesTheEventActor`,
`TestSharedScriptUsesTheCommonBaseType`); `tests/test_import.py` 214 passed,
29 skipped.

**Still unverified in-game.** The binding failure is only observable in the
Papyrus log, so the fix should be confirmed by re-running and checking that
`grep -c "Unable to bind script"` drops from 1,660 to the Nehrim-only remainder.

---

## Build verification (round 6)
<a id="build-verification-2"></a>

Round 6 touched `script_convert/` only, but the ESM was rebuilt too so the
property bindings could be checked against the output.

`python convert.py -f Oblivion.esm --scripts-only`:

```
SCPT: 2393/2393 converted
INFO: 5629/5629 fragments
QUST: 1870/1870 stage scripts
Total: 9892 converted, 0 errors, 2 TODOs
Compilation: 15959/15959 succeeded, 0 failed
```

`--import-only`: **34,965 records, 0 errors**, 799,089,258 bytes.

Per-fix counts measured in `output/`, before → after:

| Fix | Before | After |
|---|---|---|
| Bare MenuMode bodies dropped as comments | 21 | **5** (all genuinely menu-ID) |
| `MelisandeScript` `MS40.cureready = 1` executing | no | **yes** |
| `Dark09RetirementScript` `GotFinger = 1` executing | no | **yes** |
| `IsPCAMurderer` flattened to `If 0 == 1` | 2 | **0** |
| `GetDetectionLevel` flattened to `0` | 56 | **0** |
| Detection sites emitting a live `IsDetectedBy` | 0 | **54** (18 `==3`, 17 `>=2`, 19 `>=3`) |
| Dead `Sound Property _X_ Auto` declarations | 75 (23 files) | **0** |

Spot-compiled with the stricter CK compiler (`tools/script/ck_compile_check.py`):
`TES4_Dark04ExecutionScript`, `TES4_DarkVicenteScript`,
`TES4_DarkSanctuaryAssassins`, `TES4_SEZealotScript`,
`TES4_DarkBrotherhoodScript`, `TES4_Dark12JghastaScript`, `TES4_BaenlinScript`,
`TES4_GrommScript`, `TES4_MelisandeScript`, `TES4_PublicanGreyMareEmfrid`,
`TES4_SE42Script`, `TES4_Dark09RetirementScript`, `TES4_MGExpulsion02Script` —
**13/13 clean.**

Bindings verified in the built ESM with `tools/script/vmad_probe.py`:

```
TES4_DarkBrotherhoodScript   TES4CyrodiilCrimeFaction 0118E956
                             Dark01Knife              010224EB
                             LucienLachanceMurderRef  010177D2
TES4_Dark03AccidentsScript   AMBBaenlinDeath          01064209
                             AMBBaenlinMiss           0106420A
```

Tests: `tests/test_script_converter.py` **192 passed** (10 new — four classes:
`TestBareMenuModeRuns`, `TestIsPCAMurdererIsNotZero`,
`TestGetDetectionLevelIsDetection`, `TestPlaySoundPropertyIsNotQuoted`);
`tests/test_import.py` 214 passed, 29 skipped.

Tooling: `tools/misc/uesp_lookup.py` crashed with `UnicodeEncodeError` whenever a
matched wiki line contained a non-cp1252 character (an arrow, an accented
name), killing the search mid-result. It now forces UTF-8 on stdout/stderr with
`errors='replace'`.

---

## Build verification (round 5)
<a id="build-verification-2-2"></a>

Round 5 touched `script_convert/` and `tes5_import/`, so both were rebuilt.

`python convert.py -f Oblivion.esm --scripts-only`:

```
SCPT: 2393/2393 converted
INFO: 5629/5629 fragments
QUST: 1870/1870 stage scripts
Total: 9892 converted, 0 errors, 2 TODOs
Compilation: 15959/15959 succeeded, 0 failed
```

(The first attempt failed 60 scripts on the duplicate `TES4Unlock_*` property
described under R5-1; the second is the clean run above.)

Per-fix counts measured in `output/`, before → after:

| Fix | Before | After |
|---|---|---|
| Early `Return`s that kill the OnUpdate poll | 115 | **0** |
| Script `AddTopic` on a gated topic emitting `SetValue(1)` | 0 | **13 scripts** |
| Inert `;NE: AddTopic` comments | 130 | **57** (all genuinely ungated) |
| Emitted `TES4Unlock_*` names not in the unlock plan | — | **0** |
| `IsPlayerInJail` family → `IsArrested()` | 0 | **9** (7 scripts) |
| Rank/expulsion reads left for jail | 9 | **0** |

`python convert.py -f Oblivion.esm --import-only` (the R5-2 binding fix):
**34,965 records, 0 errors**, 799,088,862 bytes.

R5-2 verified against the built ESM by decoding **every** VMAD in the output
(11,671 subrecords, 10,217 distinct scripts) and comparing each script's bound
property names to what its `.psc` declares:

```
scripts declaring a synthesized prop            : 5689
scripts in a VMAD with an UNBOUND synthesized prop: 0     (was: all object scripts)
```

Tests: `tests/test_script_converter.py` **182 passed** (10 new — three classes:
`TestEarlyReturnKeepsPolling`, `TestJailIsNotExpulsion`,
`TestScriptAddTopicOpensTheGate`); `tests/test_import.py` 214 passed,
29 skipped.

---

## Build verification (round 4)
<a id="build-verification-2-2-2"></a>

Round 4 touched all three stages, so all three were rebuilt.

`python convert.py -f Oblivion.esm --scripts-only`:

```
SCPT: 2393/2393 converted
INFO: 5629/5629 fragments
QUST: 1870/1870 stage scripts
Total: 9892 converted, 0 errors, 2 TODOs
Compilation: 15959/15959 succeeded, 0 failed
```

`--export-only` (the FACT `DATA.Flags` fix) then `--import-only` (CRVA layout,
faction flags, Track Crime derivation): 34,965 records, 0 errors.

Per-fix counts measured in `output/`, before → after:

| Fix | Before | After |
|---|---|---|
| `FACT` records exporting `DATA.Flags` | 0 / 476 | **476 / 476** |
| `FACT` records with Track Crime set | 0 | **7** (6 derived + `TES4CyrodiilCrimeFaction`) |
| Murder/attack distinguishable | no | **yes** (12 murder + 9 attack sites) |
| Rank-based expelled tests | 12 | **0** |
| `IsPlayerExpelled()` emitted | 0 | **12** |
| `Expel` → `SetFactionRank(x, -1)` | 1 | **0** |
| Dropped no-topic `StartConversation` | 64 | **0** |
| `Say(GREETING)` sites | 50 | **114** |

CRVA verified in the built ESM against the vanilla byte pattern — e.g.
`ThievesGuild`: `DATA.Flags=0x00008040 (TrackCrime)`,
`CRVA.hex=0101E80328000500190000000000803F64000000` (arrest=1, aos=1,
murder=1000, assault=40, trespass=5, pickpocket=25, steal×1.0, escape=100).
All 603 CRVA subrecords are 20 bytes.

Tests: `tests/test_script_converter.py` 172 passed;
`tests/test_import.py` 214 passed, 29 skipped;
`tests/test_export.py` + `tests/test_export_diff.py` 59 passed.

---

## Build verification (round 3)
<a id="build-verification-2-2-2-2"></a>

After the six round-3 fixes,
`python convert.py -f Oblivion.esm --scripts-only`:

```
SCPT: 2393/2393 converted
INFO: 5629/5629 fragments
QUST: 1870/1870 stage scripts
Total: 9892 converted, 0 errors, 2 TODOs
Compilation: 15959/15959 succeeded, 0 failed
```

Per-fix counts measured in `output/`, before → after:

| Fix | Before | After |
|---|---|---|
| Flattened null checks | 8 | **0** |
| Literal `%` specifiers in messages | 86 | **0** |
| `GetDetected` sites (all now swapped) | 38 | 38 |
| Marker downcast to Actor | 2 | **0** |
| `SetDoorDefaultOpen 0` → closed | 0 | **1** |
| `getav encumbrance` → InventoryWeight | 0 | **2** |

No `--import-only` needed: round 3 added no new records.

Tests: `tests/test_script_converter.py` 172 passed;
`tests/test_import.py` 214 passed, 29 skipped. (The two `TestSayTimerRelease`
failures noted under round 2 are no longer present.)

---

## Build verification (rounds 1-2)
<a id="build-verification-2-2-2-2-2"></a>

After all fixes (rounds 1 and 2),
`python convert.py -f Oblivion.esm --scripts-only`:

```
SCPT: 2393/2393 converted
INFO: 5629/5629 fragments
QUST: 1870/1870 stage scripts
Total: 9892 converted, 0 errors, 2 TODOs
Compilation: 15959/15959 succeeded, 0 failed
```

Round 2 also needed `--import-only` (the new `TES4GoldFenced` GLOB):
34,965 records, 0 errors; global created at `0118E955` and bound in all
**4,762** VMAD property slots, none null.

Largest generated helpers were spot-compiled with the stricter CK compiler
(`tools/script/ck_compile_check.py`), including the 431-cell `IC` family in
`TES4_MS22SrazirrScript` — clean.

Tests: `tests/test_script_converter.py` 172 passed;
`tests/test_import.py` 211 passed, 29 skipped.
Two `TestSayTimerRelease` tests fail, and did so **before** these changes:
they assert the say-timer release never appears inside the sequence gate, but
committed `pipeline.py` (lines 887 + 892) deliberately emits it both inside and
after — its own comment says "the release is idempotent, so running it twice on
the accepted path is harmless". The tests encode a superseded rule and should be
reconciled with the implementation separately.

---


## Quest script conversion: verified-correct behaviour
<a id="quest-script-conversion"></a>

Findings from the quest-script audit rounds that are CORRECT and must not
be "fixed". Split out of the audit so they are not read as stale.

## Verified-correct behaviours from round 9 (do NOT "fix" these)
<a id="section"></a>

* **`SetForceRun 1` → `SetActorValue("SpeedMult", 150.0)`** (`MG07Script`).
  Skyrim keeps `SetForceRun` as a console/condition function (index 209) but
  exposes no Papyrus native — checked vanilla `Actor.psc` and SKSE. SpeedMult is
  the reachable approximation.
* **A property named `List`** (`Dark11Script`). `_PAPYRUS_RESERVED` covers the
  keywords plus every vanilla script name; `List` is neither, and the script
  compiles.
* **`GetInCell ArkvedsTower07` as a plain equality** (`DAVaerminaScript`). There
  are 8 `ArkvedsTower*` cells, but the round-1 rule matches the *argument* as a
  prefix and only `ArkvedsTower07` starts with `ArkvedsTower07`.
* **`GetPlayerInSEWorld` staying the literal 0.** This was investigated in depth
  and deliberately left alone; the reasoning is worth keeping because the
  "obvious" fix is a regression:
  * The **exterior** half is trivially reconstructible
    (`GetWorldSpace()` against the 13 `SE*` worldspaces). The **interior** half
    is not: an SI interior cell has no worldspace at all, carries no
    distinguishing climate or music (measured — SI interiors use the same music
    types as Cyrodiil's), and the **door graph does not separate the two
    worlds**, because the SI↔Cyrodiil gate is a legitimate edge. A flood fill
    from the SE worldspaces reaches **1,407 Cyrodiil interiors**; even depth-1
    leaks. There is no sound generic invariant, and an EditorID-prefix
    heuristic would be a fit-to-one-plugin patch.
  * Reconstructing only the exterior half would be **worse than the no-op**.
    Censused over the plugin, **11 of the 16 sites test `== 0`** — they are
    suppression guards (Lucien Lachance's sleep visit in `RufioDieScript`, the
    Gray Cowl's bounty transfer, `TutorialScript`'s jail hint), for which a
    constant 0 is the *right* answer everywhere in Cyrodiil. An exterior-only
    test would flip all 11 to false the moment the player entered an SI
    interior.
  * The 5 `== 1` sites do lose their behaviour, but 4 are in SI spell scripts
    that do not run at all (see the MGEF gap below).
* **71 unbound `Package` properties are declaration-only SCRO artifacts.** A
  sweep of all 242 scripts declaring a `Package` property found 73 unbound; 71
  are never referenced in the body (the round-8 `_preload_scro_refs` category).
  The 2 live ones are both explained by the gaps below.

## Verified-correct behaviours from round 8 (do NOT "fix" these)
<a id="section-2"></a>

* **The 4-argument `Say`'s speaker override, dropped — re-confirmed, and the
  earlier reasoning corrected.** Rounds 3 and 6 justified the drop as "the
  emitting ref is already the intended speaker". That justification is wrong:
  censused over SCPT/INFO/QUST there are **282** such sites, the emitter is an
  actor in exactly **1**, and the speaker argument is an `NPC_` in **281**. The
  emitters are shrines, statues, doors and Arena markers.

  The drop is nevertheless correct, for a different reason. Two facts settle it:
  1. **9 of 12** sampled speaker NPCs (`ArenaMouth`, `DAAzuraVoice`,
     `SEJyggalagVoice`, `DarkDoorSpeakNPC`, …) have **zero ACHR placements** —
     they are disembodied voice-carrier records that exist only to own a set of
     INFOs, so "make that actor speak" is not what the parameter can mean.
  2. Every topic used this way (`Announcer`, `SE13JyggalagSpeech`,
     `MQ15MankarRant`, `DABoethiaSpeech`, `SE01GateSpeech`, `DarkDoorSpeak`)
     carries INFOs with **no conditions at all**, so `emitter.Say(topic)`
     selects a line regardless of who the emitter is.

  A talking activator is normal in both games (Skyrim's own Daedric shrines are
  activators, and `ObjectReference.Say()` works on them), so keeping the emitter
  as the audio source reproduces Oblivion. Voice-file *routing* for these lines
  is a separate subsystem — see the voice-audit notes, not this file.

* **Calling a method on a `None` ref does not kill the polling loop**
  (`ArenaICGrandChampFights`). The script assigns up to three `combatant`
  refs and then unconditionally calls `setav`/`startcombat` on all three, so in
  every one- or two-creature fight the trailing calls run on `None`. Papyrus
  logs *"Cannot call X() on a None object, **aborting function call**"* and
  continues the frame — confirmed in a Papyrus log where the same script logged
  the error at 09:49:31 and again at 09:49:32, i.e. the `OnUpdate` re-registered
  in between. Only the offending call is skipped, which is exactly Oblivion's
  silent no-op on an unset ref. No guard needed.

* **A `TES4_<Script>`-typed property bound to a placed REFR.** 933 such sites
  (plus 2,252 ACHR and 253 ACRE). It looks unbindable, because the script is
  attached to the *base object* and the property names the *reference* — but
  Skyrim instantiates a base record's VMAD scripts on every placed reference,
  which is why `SCRIPTABLE_TYPES` in `tes5_import/base/object_scripts.py` includes
  the base signatures in the first place. `cross_ref.get_record_script_type`
  deliberately follows the `NAME` chain for this reason. Correct as written.

* **Dead `MagicEffect Property <CODE> Auto` declarations** (61, e.g. `FISH`,
  `REHE`, `BABO`). TES4's SCPT record carries a **SCRO** list of every form the
  compiled script referenced, and `_preload_scro_refs` declares a property for
  each. `PlayMagicEffectVisuals FISH` legitimately converts to the effect's
  EFSH (`effectFireShield.Play(...)`), so the MGEF property is left unreferenced
  — declared and bound, but never read. Harmless; 907 external-ref properties
  are unreferenced for the same reason (a converted call that no longer names
  the form). Not the R6-4 shape, which minted an *unbindable* quoted name.

---

## Verified-correct behaviours from round 7 (do NOT "fix" these)
<a id="section-2-2"></a>

* **`SetNoRumors` as `;NE`** (`MS13AltScript`). Skyrim keeps it as console /
  condition function 321 and the engine has `ExtraHasNoRumors`, but neither
  vanilla `.psc` nor SKSE exposes a Papyrus native — there is genuinely nothing
  to call.
* **`Activate X 1` dropping the trailing `1`** (`SE03AScript`). TES4's flag
  means "activate even if blocked"; the handler maps run-flag 1 to
  `Activate(x)` (full processing) and everything else to `Activate(x, true)`,
  which is what stops the OnActivate storm recorded in
  `project_activate_conversion`.
* **`PickIdle` → `Debug.SendAnimationEvent(ref, "IdleForceDefaultState")`**
  (`SE03AScript`). TES4 `PickIdle` (opcode `0x1064`, 0 params) forces an idle
  re-selection; the default-state reset is the standard Skyrim idiom.
* **`RemoveAllItems(dest, 1)`** (`SE38SCRIPT`). Both games put
  keep-ownership second — vanilla is
  `RemoveAllItems(akTransferTo, abKeepOwnership, abRemoveQuestItems)`.
* **`begin MenuMode 1034` preserved as a comment** (`GenericScript`). The
  persuasion minigame is a genuine menu-ID trigger — one of the 5 the R6-1 fix
  deliberately left dropped.
* **`GetSecondsPassed` → the fixed poll interval** (`SE04FinScript`). It makes
  the Felldew timer run 0.4s rather than ~20s, but it is the documented
  corpus-wide substitution, not a per-script defect.
* **A script-only `AddTopic` emitting `;NE: AddTopic (topic not gated)`**
  (`SE00Script`'s `SEFellmoorTopic`). R5-1's rule: a topic added only from a
  SCPT is never placed in `explicit_targets`, so it is ungated and already
  visible — there is no global to set.

---

## Verified-correct behaviours from round 6 (do NOT "fix" these)
<a id="section-2-2-2"></a>

* **`SetAV Aggression 40` → `SetActorValue("Aggression", 2)`** (`MG08`, `MG14`,
  `MG09`). TES4 stores aggression 0-100; TES5 defines it as a 0-3 enum
  (xEdit `wbAggressionEnum`) and rejects an out-of-range write outright. The
  bucketing mirrors the record-side thresholds in
  `tes5_import/record_types/actors.py`.
* **`If (X.GetCrimeGoldNonViolent() > 0) as Int == 1`** (`MGExpulsion02Script`,
  `DarkExiledScript`). The parenthesisation looks wrong but Papyrus is
  left-associative, so it reads `((expr) as Int) == 1` — the round-2 chained
  Bool/Int rule. Compiles clean under the CK compiler.
* **`if MenuMode == 0` → `If 0 == 0`** (`Dark18MotherScript`). Permanently true,
  and that is correct: Skyrim's `OnUpdate` never fires while a menu is open, so
  the guard's condition always holds where the body now runs.
* **`GetSecondsPassed` → the fixed poll interval** (`Dark18MotherScript`). Every
  timer increments by `0.1` and the loop re-registers at `0.1`, so
  `TraitorTimer > 48` really is ~48 seconds.
* **Dropping the 4-argument `Say`'s speaker override** (`Dark02WateryScript`'s
  `CapDoorRef.Say Dark02PirateDoor 1 DarkPirate6`). Round-3 rule — but see
  round 8, which re-derived *why*: the "emitting ref is already the intended
  speaker" reasoning given here is wrong (`CapDoorRef` is a door, `DarkPirate6`
  an NPC_). The drop is still correct, for the reasons recorded in round 8.
* **`GetQuestRunning X == 1` folding to `X.IsRunning()`** (`MG16Script`), and
  `GetIsCurrentPackage X == 1` to a bare equality (`Dark03AccidentsScript`).
* **`MG16Script`'s comment promising to "add Amulet to her inventory" while the
  code never does.** The omission is in the TES4 original; reproducing it is
  fidelity.
* **`FGD02Script`-style faithful reproduction of source oddities** — `MG09Script`
  has a stray backtick and an `if` block nested inside a branch it does not
  belong to, and `MGMageConversationFollowScript` has an unbalanced extra
  `endif`. Both are in the originals and both convert without changing meaning.
* **`GetSitState` keeping the raw 0/2/3/4 encoding** (`Dark03AccidentsScript`'s
  `>= 1`). Round-3 verified.

## Verified-correct behaviours from round 5 (do NOT "fix" these)
<a id="section-2-2-2-2"></a>

* **`GetRandomPercent` → `Utility.RandomInt(0, 99)`** (`FGPostQuest`). TES4
  returns 0-99 inclusive, which is exactly this range.
* **`FGRewardMod = ((FGRewards * 0.8)) as Int`** (`FGConversationScript`). The
  TES4 variable is a `short`, so the truncation *is* the original behaviour.
* **`Message "…" 7` dropping the trailing `7`** (`TG11HeistScript`). That is
  TES4's optional display time, which has no Papyrus equivalent — the R3-3 rule.
* **`SetQuestObject` as a no-op** (`TG11HeistScript`). Skyrim has no equivalent;
  quest-object status is a alias flag, not a runtime call.
* **`unlock` → `Lock(false)`** (`MG02Script`).
* **`GetInFaction X == 1` folding to a bare `IsInFaction(X)`** (TG09/TG10). The
  redundant `== 1` is absorbed, same rule as `GetIsCurrentPackage` in round 4.
* **A base form typed `ObjectReference` and passed to `GetItemCount`**
  (`MG02Script`'s `MG02RingofBurden`/`MG02BlackSoulGem`, a CLOT and an SLGM).
  The typing is loose but `ObjectReference` extends `Form`, so it compiles, and
  both bind to the right FormID — verified with `tools/script/vmad_probe.py`.

---

## Verified-correct behaviours from round 4 (do NOT "fix" these)
<a id="section-2-2-2-2-2"></a>

* **`FGD02Script`'s speaker-4/target-5 branch emitting `VantusPreliusREF.Say`.**
  It looks like a copy-paste slip that should say `WitseidutseiREF` — but the
  TES4 original has exactly the same bug on its own line 158. Reproducing it is
  fidelity, not a defect.
* **A `TES4_<Script>`-typed property used as a `Say()` receiver**
  (`FGD02Script`'s `WitseidutseiREF`, typed `TES4_PublicanFiveClawsWitseidutsei`).
  That script `extends Actor`, so `.Say()` resolves. Same round-2 rule.
* **A script-local named `fQuestDelayTime`** (`FGD02Script`, `FGExpulsionScript`).
  It shadows the TES4 built-in; the converter keeps it as an inert property and
  ticks at its own fixed interval, with `GetSecondsPassed` substituted to match.
  Round-1 verified-correct.
* **`IsPlayerExpelled() == 0`** as the negation. Same chained Bool/Int comparison
  verified in round 2 — Papyrus casts the Int literal to Bool, so `== 0` reads
  "not expelled".
* **`GetIsCurrentPackage X == 1` folding to a bare `(GetCurrentPackage() == X)`.**
  The redundant `== 1` is correctly absorbed rather than chained.

---

## Verified-correct behaviours from round 3 (do NOT "fix" these)
<a id="section-2-2-2-2-2-2"></a>

* **`setfactionreaction X Y 0` emitting `SetEnemy(Y, true, true)`.** It reads
  like the opposite of the intent ("make Mythic Dawn neutral so they won't attack
  anymore"), but both bools set is precisely how the Group Combat Reaction enum
  encodes **Neutral**. See the round-2 census behind `_faction_reaction_call`.
* **`GetSitting` → `GetSitState()` keeping the raw number.** Both games use the
  same 0/2/3/4 encoding (3 = Sitting), so `== 3` and `== 0` carry over directly.
* **`GetOpenState` keeping the raw number.** Same 0-4 encoding in both games
  (3 = Closed), so `BaurusScript`'s `< 3` is correct. Distinct from
  `SetDoorDefaultOpen` above, whose bug was dropping its argument entirely.
* **`SetOpen(0)` / `SetOpen(1)` passing an Int to a Bool parameter.** Papyrus
  coerces, and the 73 `SetOpenState` sites carry the right value.
* **`CreateFullActorCopy` → `PlaceAtMe(x.GetActorBase())`** (`MartinScript`'s
  player statue). Not an exact clone — Papyrus has no equivalent — but it places
  a real actor of the right base at the right spot.
* **`setav speed 0` → `SetActorValue("SpeedMult", 0)`.** The scales differ, but
  the call's purpose is immobilising the statue and SpeedMult 0 does that.
* **Dropping the 4-argument `Say`'s speaker override** (`MQ15Script`'s
  `MQ15ResurrectPad1.say MQ15MankarRant 1 MankarCamoran 1`). Correct to drop,
  but **not** for the reason originally recorded here ("the emitting ref is
  already the intended speaker") — `MQ15ResurrectPad1` is a marker, not Mankar.
  Round 8 censused all 282 sites and established the real justification; read
  that entry before touching this mapping.
* **MQ01's MenuMode tutorial blocks preserved as comments.** Every message inside
  them is commented out in the TES4 original too, so nothing executable is lost.
* **`set button to getbuttonpressed` → `button = -1`.** *(Superseded
  2026-08-03: button MessageBoxes now become authored MESG records and the
  poll reads a consume-once shadow of `Show()`'s return — see "Button
  MessageBoxes become authored MESG records" in papyrus_conversion_notes.md.
  The original rationale here — "no message box is ever shown" — was wrong
  for the door scripts whose box gated progression: CGSewerExitScript's
  dead `button == 3` branch was the only path to MQ01 stage 88, so the
  sewer exit could never be taken.)*

## Verified-correct behaviours from round 2 (do NOT "fix" these)
<a id="section-2-2-2-2-2-2-2"></a>

* **Chained `a == b == 0` / `== 1`** (`GetInWorldSpace`, `GetInSameCell`,
  single-cell `GetInCell`, `GetIsReference`). These look like a bug — a form
  comparison chained into another comparison — but they are correct. Compiled
  with the CK compiler and read back from the emitted assembly: Papyrus is
  left-associative and casts the Int literal to Bool, giving
  `COMPAREEQ t1, a, b` → `CAST t2, 0` → `COMPAREEQ t2, t1, t2`. So `== 0` means
  "not equal" and `== 1` means "equal", exactly reproducing TES4's 0/1 idiom.
  34 such sites.
* **A `TES4_<Script>`-typed property passed to `GetItemCount`** (`SQ06Script`'s
  `SQ06BearFang`). The type is derived from the item's own attached script
  (`MISC 0018AE3F` carries `SCRI=0018AE41` → `SQ06FangsScript`), so it names the
  right form, and Papyrus script types extend the base form type.
* **`SetWeather` / `ForceWeather` as a no-op.** Weather conversion is broken and
  currently skipped (`CONVERT_CLIMATE = False`), so the CLMT chain that is the
  only route to a weather is never written. Forcing an unreachable weather into
  Skyrim's sky system divides-by-zero and hard-crashes. Keep it a no-op until
  weather conversion itself is fixed. (The old code comment claiming "WTHR is in
  skipTypes" was wrong and has been corrected.)
* **`ModDisposition` with a value above -100** correctly emits `;NE`
  (`MS38Script`'s `+20`); only the full -100 hostility idiom maps to combat.
* **`EssentialDeathReload` as a no-op** (`MQ13Script`) — the "quest failed,
  reload" prompt has no Skyrim equivalent; the accompanying `scriptKill` still
  fires.
* **`StartConversation` → `Say(topic)`** is a deliberate, documented decision
  with recorded history (discarding the topic silenced every scripted NPC-NPC
  conversation). Not a defect. The one exception is a Player target, which is a
  force greet
  ([tes5_import_dialogue.md](tes5_import_dialogue.md#startconversation-player-force-greet)).

## Verified-correct behaviours from round 1 (do NOT "fix" these)
<a id="section-2-2-2-2-2-2-2-2"></a>

* **`GameDaysPassed` → `.GetValue() as Int`.** Skyrim declares it float
  (`FNAM=102`, `Ord('f')` per xEdit's GLOB definition), which makes the cast
  look lossy — but **Oblivion declares it Short** (`GLOB 00000039`,
  `FNAM.Type=s`), so the source scripts only ever saw whole days and the
  truncation is what *reproduces* their behaviour. This matters beyond the
  day-of-week idiom: 72 lines across 28 scripts compare it against script
  floats, several by exact equality (`MS39Script`:
  `GameDaysPassed == (CurrentDay + 1)`), which only ever matched in Oblivion
  because both sides were whole numbers. The old code comment claimed the
  day-of-week idiom was its "only bare use" — that was wrong and is now fixed.
* **`fQuestDelayTime` as an inert property.** Oblivion's script-polling interval
  has no Skyrim equivalent; writes to it are correctly kept as harmless state.
* **`X.WearingArmor`** (`ArenaScript`) is a remote *script variable* read on the
  target's attached script, not an unconverted function.
* **`DisablePlayerControls` with no local re-enable** (`Dark17FollowingScript`).
  The matching `EnablePlayerControls` lives in the follow-up dialogue INFO, and
  converts fine there (58 scripts emit it). Not a soft-lock.
* **`Key` → `myKey`** and similar renames are the Papyrus reserved-word guard.

---


## Quest script conversion: known gaps
<a id="quest-script-conversion-known-gaps"></a>

Gaps the audit found and deliberately did not fix.

## Known gaps (round 9, not fixed here)
<a id="known-gaps"></a>

* **No MGEF carries a VMAD, so all 97 `extends ActiveMagicEffect` scripts are
  compiled but never attached.** TES4 MGEFs are restructured wholesale
  (`magic_effects.py` writes no VMAD and MGEF is in `SKIP_TYPES`), so every
  converted script-effect script is dead. This is a structural conversion gap,
  not a script-conversion defect, and it is why R9-2's *writers* cannot
  currently maintain the shadow global even though its reader binds correctly.
  Worth its own pass.
* **`GetCurrentAIProcedure` is a `;NE` no-op returning `0`** (9 sites across 6
  scripts, e.g. `MS91Script`'s `!= 4`, permanently true). Unlike package *type*,
  the AI procedure is pure runtime engine state with no per-actor record to
  reconstruct from, and Skyrim exposes no Papyrus native (checked vanilla
  `Actor.psc` and SKSE). Genuinely unreachable.
* **`BravilGuardJailorScript` is attached to no record in the export** — an
  orphan script in Oblivion.esm itself, so its unbound `Package` property is
  inert in both games. Not a pipeline bug.

---

## Known gaps (round 6, not fixed here)
<a id="known-gaps-2"></a>

* **`GetRestrained` is a `;NE` no-op returning `0`**, so `FGD07AjumScript`'s
  three tests are constant (`(0 == 0)` always true, `(0 == 1)` always false) —
  the Fighters Guild kidnap quest's "is Ajum tied up" checks. Skyrim keeps
  `GetRestrained` as condition function 4340 but exposes **no Papyrus native**
  (checked vanilla `Actor.psc` and SKSE), so there is genuinely nothing to call.
  The writer works (`SetRestrained` → `SetDontMove`), so a script-tracked shadow
  flag is the plausible fix — a design change, not a function mapping.
* **`ModDisposition <target> -100` beside an explicit `StartCombat`** emits
  `StartCombat` twice (`DarkExiledScript`). Idempotent and harmless, but it is
  the same shape round 2 deduped for `Say`.

---

## Known gaps (round 3, not fixed here)
<a id="known-gaps-2-2"></a>

* **`AddScriptPackage` → `EvaluatePackage()`** bites hardest in `MartinScript`:
  the player statue is given `MQStatuePose` to hold, and re-running AI selection
  instead means it does not hold the pose. Same structural limitation recorded in
  round 2 — forcing a package is a quest alias with a package stack.
* **`ShowBirthsignMenu`** is a no-op (`CharGenQuest` stage 43), so the birthsign
  is never chosen. Skyrim has no birthsign menu at all; this needs a replacement
  UI or an auto-assignment, not a function mapping.

---

## Known gap (not fixed here)
<a id="known-gap"></a>

* **`AddScriptPackage` → `EvaluatePackage()`** drops the requested package
  (`SE05QuestScript`'s `SE05TortureHoldPosition`). Skyrim has no Papyrus
  equivalent: forcing a specific package is a quest **alias with a package
  stack**, a structural conversion rather than a function mapping. The current
  emission at least re-runs AI selection. Out of scope for a script audit.

---

## The three gaps from the 2026-07-17 completability audit, all now closed
<a id="completability-audit-gaps-closed"></a>

The audit left three quest-conversion gaps needing design.  All three shipped;
this records what each became so the design is not re-derived.

**A. Scripted magic effects (SEFF) were never attached** -- 4 quests degraded
(SE04 stage 40 Felldew withdrawal, MS47 stages 40-60, MS40 stage 60, FGD08
stage 40).  SEFF scripts converted to `extends ActiveMagicEffect` and compiled,
but nothing referenced them: no carrier MGEF, so the script never ran.
Built as `build_seff_variants` in `tes5_import/record_types/magic.py` -- one
Script-archetype MGEF per distinct SEFF script, VMAD built by
`build_magic_effect_script_plan` in `tes5_import/base/object_scripts.py`, spliced
into the converted SPEL/ENCH/INGR effect lists.

**B. MenuMode blocks were preserved as comments** -- MS05 (Through a Nightmare,
Darkly) degraded, because entering the Dreamworld happens in a MenuMode block
gated on `IsPCSleeping`.  Built as `sleep_listener` in
`script_convert/assemble.py`: a bare MenuMode body reading `isPCSleeping`
becomes a real `OnSleepStart`/`OnSleepStop` handler.  Menu-ID-gated MenuMode
blocks stay comment-only -- Papyrus has no per-menu event, and the naive
conversion caused the "MQ01 starts-then-fails" bug.

**C. MS14 stage 200 was never a regression.**  `MS14TivelaScript` is attached to
nothing in Oblivion.esm itself, so its stage-200 edge never ran in the original
game.  The walkthrough baseline excludes orphaned SCPTs.


## <a id="quest-conversion"></a>QUST conversion: what the module owns

**Code:** `tes5_import/dialogue/quest.py`

Split out of `dialog_converter.py`, which had grown to 2,759 code lines. The
seam was chosen by counting call-graph edges across every candidate cut, not by
the file's own section banners -- those claim seven sections, but one connected
component of 34 functions spans four of them. The minimum cut leaves 4 edges,
all running one way: dialogue reads `compute_quest_priorities`,
`_quest_state_ctdas`, `_has_quest_state_condition` and `_bark_choice_gate_bytes`
from the quest module, and nothing in the quest module imports dialogue.

### Script properties

The player's ids never bind through a SCRO. `0x14` has no EditorID to name a
property with, and `0x07` is the TES4 player `NPC_` (EditorID `Player`), which
would bind an `Actor Property Player` to a BASE record the VM refuses. The
declared-name pass binds whichever spelling the script uses to `PlayerRef`
instead.

### <a id="engine-globals"></a>Engine globals: dropping the record is half the contract

**Code:** `tes5_import/record_types/common.py` `convert_GLOB`

Skyrim already ships `gamehour`, `gamedayspassed`, `gameday`, `gamemonth`,
`gameyear` and `timescale` at the SAME FormIDs Oblivion uses, so `convert_GLOB`
emits no record for them -- our own copies would only be duplicate EditorIDs.

That is only half of it. VMAD properties naming those globals must also be
bound UNSHIFTED, via `constants.ENGINE_GLOBAL_FORMIDS`; they must not be
shifted into our index. For a long time only the record-dropping half existed,
which left every such property bound to a nonexistent form.

An earlier comment credited `script_convert._GLOBAL_CANONICAL` with fixing the
references. It does not -- it only canonicalizes the NAME.

One converter runs for all stages: `convert_fragment` deliberately accumulates
`_property_refs` across calls, which is how the QF_ generator collects the union
of every fragment's references.

### Quest-state conditions

TES4 CTDA functions expressing quest TIMING (56 `GetQuestRunning`, 58
`GetStage`, 59 `GetStageDone`, 99 `GetQuestCompleted`) are the conditions a
choice-reached response topic must inherit from the greeting that reveals it, so
a promoted top-level topic does not appear before its quest reaches the right
point.

Oblivion also tracks progress as the player's rank in a quest faction rather
than a stage -- Agronak's challenge greetings are gated on
`GetFactionRank(Arena...)` -- so those player-progress gates are carried onto
choice targets alongside the quest-state ones.

The CTDA operator is the top 3 bits of the type byte.

### Quest targets become per-objective aliases

Oblivion `QSTA` is QUEST-level: one entry per (target ref, condition set), where
the conditions are `GetStage` bounds saying WHEN that target's compass marker is
live. Skyrim `QSTA` is per-OBJECTIVE and vanilla leaves it UNCONDITIONAL -- the
objective being Displayed is what selects the marker. Checked across
Skyrim.esm: objectives read `QOBJ FNAM NNAM QSTA [QSTA?]`, CTDAs are the rare
exception, and the right target simply sits on the right objective.

So the faithful mapping RESOLVES Oblivion's `GetStage` gates at build time
rather than replaying them at runtime: each objective (= stage) emits only the
targets whose TES4 conditions hold AT THAT STAGE, with no CTDAs. Carrying every
target on every objective -- the previous design -- makes the engine face a list
whose leading entries are false, and it renders no marker at all: the objective
shows in the journal while compass and map stay empty.

An objective with no live target keeps its journal text and marks nothing, the
same as vanilla's marker-less objectives ("Return when you're ready"). If
Oblivion gated every target away at that stage, honour it rather than inventing
a marker.

The HUD objective is the SHORT line, not the long log entry `CNAM` carries (see
`objective_text.py`). It must stay identical to `quest_objective_texts()`, which
the override builder uses to rebuild this same run for a translation plugin.

### Package aliases

A Skyrim quest package must hang off a reference alias (`ALPC`): that is what
lets it outrank the actor's standing schedule, which is exactly what Oblivion
achieved by putting a conditioned package at the top of the actor's AI list.
`pack_plan` (built in Phase 0) says which refs this quest's packages name;
aliases are allocated during conversion and PACK reads back the SAME indices, so
the two cannot drift. A ref that was already a quest target can also run
packages.

### Forced-reference alias layout

`ALST, ALID, FNAM, ALFR, [ALPC...], VTCK, ALED`, following vanilla. **`VTCK` is
present on 2,687/2,687 vanilla forced-ref aliases -- a 100% invariant** (empty
means "no voice-type override"), and every one of the 255 vanilla
objective+forced-ref quests carries it.

Flags `0x0292` = Optional (`0x0002`, so a fill failure cannot block quest start
and take the dialogue down with it) + Allow Dead (`0x0010`) + Allow Disabled
(`0x0080`) + Allow Reserved (`0x0200`) -- an attested vanilla combination. The
old `0x109A` added Allow Reuse/Allow Destroyed and appears nowhere in vanilla.

### VMAD property binding

Bind every property the QF_ script DECLARES, not just what the SCROs cover: the
player is in no registry and its SCRO is skipped, and the synthesized records
(`TES4ControlsDisabled`, `TES4Msg_*`, ...) resolve ONLY through the well-known
registry, looked up per declared name -- merging the whole ~1,880-entry registry
put every unlock global on every scripted quest.

The engine's `PlayerRef` always wins: a SCRO-derived case variant naming the
converted TES4 player `NPC_` would bind a BASE record the VM refuses and the
property reads None.

A reference-typed property naming an actor BASE means the placed instance
(`CarmaloTruiand.moveto ...` on QF_MS26); the VM refuses an `NPC_`/`CREA` into
it and the property reads None. The SCRO's base is rebound to its one placed
ref, which also binds names the SCROs missed.

### <a id="quest-priority-arbitration"></a>Quest priority is the dialogue arbitrator

Oblivion picks the first passing INFO in QUEST PRIORITY order (highest first),
NOT file order.

`QUST.DNAM.Priority` is a U8, but the engine/CK band is 0-100: Skyrim.esm's
1,811 quests span 0-100 with **nothing** above it. The byte also arbitrates a
quest ALIAS PACKAGE against the actor's standing schedule, so an out-of-band
value there breaks AI, not just dialogue ordering.

`ZERO_STAGE_TOP = 49` is the ceiling for stage-less "conversation container"
quests (MG00General, FGConversations, ...). Staged quests keep their AUTHORED
TES4 priority; only a container quest that would otherwise outrank one is pulled
down.

Containers at or below the ceiling keep their authored priority untouched. Only
those ABOVE it move, and they are ORDER-PRESERVING compressed into the headroom
just under the ceiling rather than all clamped onto it -- a flat clamp would tie
MQConversations (85) with Dark00General (50) and hand arbitration between them
to file order. Distinct authored values map to distinct slots, the highest
landing on the ceiling, so relative order is exact. Slots run out only if a
plugin has more than `ZERO_STAGE_TOP` distinct over-ceiling values; ties at the
floor are then unavoidable but stay in the band.

The ceiling is FIXED, not `min(staged priority)`: three staged quests
(MQDragonArmor, SE06Battle, E3) are authored at 0, so a relative ceiling would be
0 and would flatten all 125 containers onto one value.

`_QUEST_PRIORITY_OVERRIDE` carries the result to `_quest_dnam`, so the WRITTEN
`QUST.DNAM.Priority` includes the container clamp. **DIAL `PNAM` must NOT be
derived from it** -- see `convert_DIAL`.

### <a id="conditions-a-choice-target-inherits"></a>Conditions a choice target inherits

A choice-reached response topic must inherit the conditions of the greeting that
reveals it, so a promoted top-level topic does not appear before its time.

`_QUEST_STATE_FUNCS` -- TES4 CTDA functions expressing quest TIMING (when a line
is live): 56 `GetQuestRunning`, 58 `GetStage`, 59 `GetStageDone`, 99
`GetQuestCompleted`.

`_PLAYER_PROGRESS_FUNCS` -- 71 `GetInFaction`, 73 `GetFactionRank`. Oblivion
questlines often track progress as the player's rank in a quest faction rather
than a stage: Agronak's challenge greetings are gated
`GetFactionRank(ArenaCombatants) == 7` ON TARGET (the player), and without
inheriting that, the promoted "Yes, I wish to challenge you" topic sits in his
menu from the first conversation. Only the run-on-target form is a progress
gate -- the subject form describes the SPEAKER, and the target topic already
carries its own audience conditions.

`_VAR_STATE_FUNCS` -- 53 `GetScriptVariable` / 79 `GetQuestVariable` are ALSO
timing gates (`set Arena.ChallengeAgronak to 1` both advances state and retires
the greeting). They inherit as translated `GetVMScriptVariable` /
`GetVMQuestVariable` conditions with their CIS2 variable name riding along.

`GetStageDone(quest, N)` means stage N has been completed; it is approximated as
"we are at or past N", the only monotonic reading available from static data.

The CTDA operator is the top 3 bits of the type byte. A trailing OR flag with
nothing after it is invalid and is cleared. Revealer gates build an OR-chain of
one condition per revealer; mixed AND-groups across revealers use the first
revealer's group.

### <a id="stage-log-entry-conditions"></a>Stage log entry conditions

An Oblivion stage log entry carries its own CTDAs, between QSDT and CNAM, that
decide whether that text displays. The importer read `.Text`, `.Flags`,
`.ResultScript` and `.SCRO` and never `.Condition[].Raw`, so every entry was
written unconditionally.

Mostly this did not show: 1,841 of Oblivion's 1,935 text-bearing stages carry
exactly ONE entry, with nothing to choose between. The ~94 multi-variant stages
silently showed the FIRST variant -- SE46 reporting the Demented residents dead
when the player killed the Manic ones.

`Charactergen` is where it became loud. 24 of its 30 entries are gated
`GetQuestVariable(Charactergen, 19) == 1`, where SCPT `Variable[26].Index=19` is
the `short debug` its script declares ("set to 1 to see debug messages in
chargen") and never assigns. Dropping that gate turned "display never" into
"display always", putting 24 developer notes -- "DEBUG: Stage 17: Emperor
approaches player" -- in the player's journal during a sequence they are not
meant to know is a quest.

Skyrim supports this natively: vanilla `Skyrim.esm` carries 370 log-entry
conditions across 37 quests (TutorialAlchemy, MQ103, HousePurchase), in exactly
the QSDT -> CTDA -> CNAM position xEdit documents. So the fix is to convert the
conditions the export already carries, via the same
`convert_ctda_list_with_strings` every other condition site uses -- NOT to
detect and delete debug entries, which needs a heuristic and cannot fix the
SE46 class of bug.

`script_vars` is required, not optional: `GetQuestVariable`/`GetScriptVariable`
(236 + 11 of Oblivion's 537 gated entries) carry the variable as a script-local
INDEX, and only that map turns it into the CIS2 name Skyrim needs. Without it
those conditions convert to nothing and the gate silently disappears again.

Measured over Oblivion.esm: 537 gated entries in 67 quests; 439 GetStageDone,
236 GetQuestVariable, 148 GetStage, and 18 `IsXBox` (func 309, same index and
meaning in both games -- the tutorial's gamepad/PC split, which `pc_stage_texts`
already filters, so the surviving PC variant's `IsXBox == 0` still passes).

### <a id="stage-journal-text"></a>Stage journal text: gamepad variants and control tokens

Oblivion shipped TWO journal texts for a control-tutorial stage -- a gamepad
variant and a keyboard/mouse variant -- and the engine picked by platform at
runtime (the same `if isXbox == 0 ... else` split guarding the matching
MessageBox calls in MQ01Script). Skyrim has no such selector: it renders every
QSDT/CNAM pair the record carries, so emitting both left console text showing on
PC ("Use the left stick to move around", "press A to equip") and, because the
objective takes the FIRST non-empty text, made the gamepad line the visible
objective. Only the PC variant is kept.

Only MQ01 (the tutorial) has these pairs -- 7 stages -- so the match is
deliberately narrow: a stage must have MULTIPLE texts and the pair must split
cleanly into one gamepad-only and one PC-only reading, otherwise all texts are
kept untouched.

Oblivion's journal text also embeds control-name tokens (`&sUActnForward;`) that
its UI expanded to the player's live key binding. Skyrim has no such expansion
and prints the token verbatim, so the tutorial read "To move forward,
&sUActnForward;". Skyrim's DEFAULT PC bindings are substituted, phrased to fit
the surrounding sentence ("To move forward, press W"). Only MQ01 uses these --
9 occurrences.

### <a id="stage-fragments-and-property-refs"></a>Stage fragments and property refs

One converter runs for all stages: `convert_fragment` deliberately preserves
`_property_refs` across calls, which is how the QF_ generator accumulates the
single union of every fragment's references.

`.strip()` must match the PSC generator's filter exactly: E3 and SEObelisks have
a whitespace-only `\r\n` stage-100 result script, which emitted a VMAD fragment
for a stage the PSC did not generate a function for.

An ActorBase-typed `Player` is the `NPC_` (0x7), not the reference (0x14): the VM
refuses a reference into an ActorBase property and the whole script's binding
fails.

### <a id="declared-properties-not-the-whole-registry"></a>Declared properties, not the whole registry

Running the same converter the `.psc` was generated from is what tells us WHICH
properties the script actually declares: the synthesized records
(`TES4ControlsDisabled`, `TES4Fame`, `TES4Msg_*`) that resolve only through the
well-known registry, the engine-hardcoded names (`Player`) no SCRO covers, and
the declared TYPE of each -- which decides whether an actor-base binding must be
redirected to the placed reference. This replaced binding all ~1,880 registry
entries to every quest. It is best-effort: a converter failure yields an empty
dict and the property is left unbound, exactly as before the filtering existed.

The record's SCROs cover ordinary records but NOT these. The player is in no
registry and its SCRO is deliberately skipped, and the synthesized records exist
only in the output. An unbound property is None and the first use aborts the
WHOLE fragment -- `UrielSeptimRef.SetLookAt(Player)` killed Charactergen stage 12
before it unlocked CGEmperor01-24, leaving the Emperor with only 'Rumors'.

The player check comes FIRST so the engine's `PlayerRef` can never be displaced
by a same-named registry entry or record. Names resolving to nothing are
omitted, never bound to zero.

### <a id="resolving-target-markers-per-stage"></a>Resolving target markers per stage

Oblivion gates each `QSTA` with conditions -- overwhelmingly `GetStage <op> N` on
the quest's own FormID, which is exactly "show this marker during this part of
the quest". Skyrim has no equivalent, its objective targets being unconditional,
so the gate is resolved at build time: evaluate the chain with
`GetStage == stage_idx` and put the target only on the objectives where it holds.

OR semantics follow the CTDA chain rule: bit 0 of the type byte ORs a condition
with the NEXT one, so the chain is an AND of OR-groups. A condition that cannot
be evaluated (any function other than `GetStage`/`GetStageDone` --
`GetQuestVariable`, `GetDeadCount`, ...) is treated as PASSING: it is a runtime
fact we cannot know, and dropping the target on a maybe would lose a marker
Oblivion did show. A target with no conditions at all is always live.

### <a id="timing-ctdas-a-promoted-target-inherits"></a>Timing CTDAs a promoted target inherits

`_quest_state_ctdas` reads `Condition[i].Raw` and keeps only quest-state
functions (`GetStage` etc.), run-on-target `GetInFaction`/`GetFactionRank`
(player questline progress -- Oblivion's faction-rank-as-stage idiom), and legacy
variable reads translated to `GetVMScriptVariable`/`GetVMQuestVariable` with
their CIS2 variable name. It converts and remaps them, then clears any dangling
OR flag so the returned list is a standalone AND-group.

Identity and voice conditions are deliberately excluded: the response topic
already carries its own `GetIsID`, and only the missing TIMING gate is inherited.
An always-available greeting has no timing conditions and yields an empty list.

### <a id="authored-objectives"></a>Authored objectives (FO3/FNV)

**Code:** `tes5_import/dialogue/quest_falloutnv.py`, called from `convert_QUST`.

An export carrying `Objective[]` blocks
([what FNV authors](tes4_export_falloutnv.md#objectives)) is written
verbatim: `QOBJ` = the authored index, `FNAM` 0, `NNAM` = the authored text,
one `QSTA` per target with the target's own conditions after it (Skyrim.esm
carries CTDAs under QSTA the same way). Targets become forced-reference
aliases through the same `alias_by_fid` table the derived path fills, so
alias packages and the ANAM count are unchanged.

The derived objective-per-journal-stage path is skipped for such a quest, and
the quest is journal type 8 (Side) whether or not any stage has `CNAM` text:
most FNV stages have none, and a type-0 quest never lists in the journal no
matter how many objectives its scripts display.

Target conditions go through `convert_ctda_list_with_strings` so a
`GetQuestVariable` gate (160 of FNV's 1,229 target conditions) becomes the
`GetVMQuestVariable` read with its CIS2 name, rather than a dropped marker.
