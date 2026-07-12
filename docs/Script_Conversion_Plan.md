# TES4 Script → Papyrus Conversion Plan

## Scope

| Source | Count | Description |
|--------|-------|-------------|
| SCPT records | 2,393 | Standalone scripts (object, quest, magic effect) |
| INFO ResultScript | 5,694 | Dialogue result scripts (run when INFO is selected) |
| QUST stage SCTX | 1,881 | Quest stage result scripts (NOT yet exported) |
| **Total** | **9,968** | All scripts requiring conversion |

### SCPT Type Distribution
| SCHR.Type | Meaning | Count | Papyrus `extends` |
|-----------|---------|-------|--------------------|
| 0 | Object script | 2,031 | `ObjectReference` (or `Actor` if attached to NPC_/CREA) |
| 1 | Quest script | 265 | `Quest` |
| 256 | Magic effect script | 97 | `ActiveMagicEffect` |

### Variable Type Distribution
| Type | Count | Papyrus |
|------|-------|---------|
| `short` | 4,994 | `Int Property ... Auto` |
| `float` | 1,147 | `Float Property ... Auto` |
| `ref` | 984 | `ObjectReference Property ... Auto` |
| `long` | 1 | `Int Property ... Auto` |

### Top 10 Block Types (from 2,393 scripts)
| Block | Count | Papyrus Event |
|-------|-------|---------------|
| `GameMode` | 1,335 | `OnUpdate()` + `RegisterForSingleUpdate()` |
| `OnActivate` | 899 | `OnActivate(ObjectReference akActionRef)` |
| `OnDeath` | 452 | `OnDeath(Actor akKiller)` |
| `OnReset` | 224 | `OnReset()` |
| `OnLoad` | 208 | `OnLoad()` |
| `OnPackageDone` | 174 | `OnPackageEnd(Package akOldPackage)` |
| `OnTrigger` | 151 | `OnTriggerEnter(ObjectReference akActionRef)` |
| `OnPackageEnd` | 135 | `OnPackageEnd(Package akOldPackage)` |
| `OnAdd` | 102 | `OnContainerChanged(ObjectReference akNew, ObjectReference akOld)` |
| `OnPackageChange` | 90 | `OnPackageChange(Package akOldPackage)` |

---

## Architecture

### Pipeline Overview

```
1. Export phase (tes4_export)
   └── SCPT.txt         (SCTX source + SCHR.Type + SCRO refs)
   └── INFO.txt          (ResultScript field)
   └── QUST.txt          (Stage[i].Log[j].ResultScript — NEEDS ADDING)
   └── NPC_.txt / CREA.txt / ACTI.txt / etc. (SCRI → script attachment)

2. Script conversion (tools/oblivion_to_papyrus.py)
   ├── Parse all script sources
   ├── Build cross-reference graph (FormID→EditorID→ScriptName)
   ├── Classify each script (extends type)
   ├── Convert line-by-line with function mapping
   ├── Inject RegisterForSingleUpdate for GameMode blocks
   ├── Generate property declarations for all external refs
   ├── Generate polyfill calls for unmapped functions
   └── Write .psc files

3. Quest fragment generation (tes5_import side)
   └── For QUST with stage scripts → populate VMAD script fragments

4. Compilation (optional)
   └── PapyrusCompiler.exe validates output
```

### Output Structure
```
output/oblivion.esm/
  scripts/source/
    TES4_<EditorID>.psc              # Standalone scripts
    TES4_QF_<QuestEDID>.psc          # Quest fragment scripts
    TES4_TIF_<FormID>.psc            # Topic info fragment scripts
    TES4Polyfill.psc                 # Polyfill library
    TES4Compat.psc                   # Compatibility utilities
  scripts/compiled/                   # .pex (if compiler available)
```

---

## Step-by-Step Implementation Plan

### Step 1: Export Quest Stage Scripts

The TES4 QUST export currently only captures stage index, flags, and log text. It misses 1,881 per-stage SCTX (result scripts) and per-stage CTDA (conditions). These are INDX → QSDT → CTDA → SCHR → SCDA → SCTX ordered subrecords.

**Changes to `tes4_export/record_types/dialog_misc.py::export_QUST()`:**
- After QSDT, check for SCHR/SCTX subrecords following the stage entry
- Export as `Stage[i].Log[j].ResultScript=<escaped source>`
- Export `Stage[i].Log[j].SCHR.Type=<int>` for script type context

### Step 2: Build Cross-Reference Graph

Before converting any script, build a lookup table from the export data:

1. **FormID → EditorID map**: From ALL record types (ACTI, NPC_, CREA, QUST, WEAP, etc.)
2. **EditorID → Script name map**: From SCRI fields on all records → SCPT EditorID
3. **SCPT FormID → SCHR.Type**: Script type classification
4. **QUST EditorID → variable list**: Quest scripts' variables are globally accessible

This graph enables:
- Resolving `set SomeRef.VarName to value` → `(SomeRef as ScriptType).VarName = value`
- Determining correct `extends` class when SCHR.Type=0 (check if attached to NPC_→Actor)
- Property declaration for all referenced FormIDs

### Step 3: Script Type Classification

| Signal | Extends | Priority |
|--------|---------|----------|
| SCHR.Type = 1 | `Quest` | Highest |
| SCHR.Type = 256 | `ActiveMagicEffect` | Highest |
| Attached to NPC_/CREA via SCRI | `Actor` | High |
| Contains `ScriptEffectStart` block | `ActiveMagicEffect` | Medium |
| Contains `SetStage`/`GetStage` as self | `Quest` | Medium |
| Calls `Kill`, `GetAV`, `StartCombat` on self | `Actor` | Low |
| Default (SCHR.Type = 0) | `ObjectReference` | Lowest |

### Step 4: Function Mapping (Complete)

The existing FUNCTION_MAP has ~90 entries. Full Oblivion has ~200+ vanilla functions. We need three tiers:

**Tier 1: Direct equivalents (~100 functions)**
Same or very similar Papyrus function exists. Mechanical substitution.

**Tier 2: Polyfill required (~50 functions)**
Function exists in Oblivion but not Papyrus. A polyfill script provides the equivalent:
- `GetRandomPercent` → `Utility.RandomInt(0, 99)`
- `GetButtonPressed` → Queue-based message system via polyfill
- `PlayGroup` → `Debug.SendAnimationEvent()` (animation group name mapping)
- `GetPos X/Y/Z` → `GetPositionX()` / `GetPositionY()` / `GetPositionZ()`
- `SetPos X/Y/Z` → `SetPosition(x, y, z)` (needs axis decomposition)
- `GetAngle X/Y/Z` → `GetAngleX()` / `GetAngleY()` / `GetAngleZ()`
- `ShowMap` → `Game.ShowFirstPersonGeometry(true)` (approximate)
- `SetCrimeGold` → `Faction.SetCrimeGold(amount, false)`
- `GetInCell` → Polyfill: compare `GetParentCell() == targetCell`
- `GetSelf` → `Self` (keyword, not function call)
- `IsActionRef player` → `akActionRef == Game.GetPlayer()` (event parameter)
- `PMS`/`SMS` (play/stop magic shader) → `Game.ShakeCamera()` (approximate)
- `PlaceAtMe` with persistent flag → `Game.CreateReferenceAtLocation()`

**Tier 3: No equivalent (~50 functions)**
Functions with no Papyrus equivalent. Emit `;TODO:` comments:
- `CloseOblivionGate` — Oblivion-specific
- `SetQuestObject` — Engine-level, no Papyrus API
- `PurgeCellBuffers` — Engine memory management
- `SetCellOwnership` — No direct Papyrus equivalent
- `Reset3DState` — Rendering internals
- `ShowMap` (discovery) — Partial via `WorldSpace.SetMapMarkerVisible()`

### Step 5: GameMode → OnUpdate Conversion

Every `begin GameMode` block becomes:

```papyrus
Event OnInit()
  RegisterForSingleUpdate(0.5)  ; Default interval
EndEvent

Event OnUpdate()
  ; ... converted GameMode body ...
  RegisterForSingleUpdate(0.5)  ; Re-register at end
EndEvent
```

**Interval heuristic:**
- Script uses `GetSecondsPassed` → 0.1s (fast poller)
- Script checks distance/position → 0.5s (spatial check)
- Script only checks flags/stages → 1.0s (slow check)
- Default → 0.5s

**`begin MenuMode <id>` blocks: comment out, do NOT merge into OnUpdate**

A `begin MenuMode <id>` block runs *only while that specific menu is open* —
1014 = lockpicking, 1030 = class menu, 1002 = inventory, 1023 = quest/map,
1022 = magic. Skyrim has no per-menu event, and `Utility.IsInMenuMode()` only
answers "is *some* menu open", so **there is nothing to convert the trigger to.**

Merging these bodies into the GameMode `OnUpdate` loop (which is what the
converter used to do, with no guard at all) makes them run on the first tick as
if every menu were open at once. `MQ01Script` is the worst case: its
`MenuMode 1014` and `MenuMode 1030` blocks call `setstage MQ01 70` / `84`
unconditionally, so on a new game the tutorial quest blew straight through its
stage machine and hit stage 100's `stopquest MQ01` — this was the
"MQ01 starts then immediately fails / jumps to the last stage" bug.

The converter now emits MenuMode bodies as a converted-but-commented block after
`OnUpdate`, so the trigger can't fire and the translation stays available for
anyone hand-porting it to a Papyrus menu hook. Only ~11 MenuMode blocks exist in
all of Oblivion.esm, 5 of them in MQ01Script.

**Locals whose name collides with a TES4 command**

`DiveRockScript` declares `short message`. A local is registered under BOTH its
original TES4 spelling and its Papyrus-safe rename (`message` → `myMessage`,
since `Message` is a Papyrus type): the body still spells it the TES4 way, so if
only the safe name is registered, `if message == 0` is compiled as the TES4
`Message` *command* and comes out as `If Debug.Notification("") == 0`.

### Step 6: Variable → Property Conversion

```
TES4: short doOnce            → Int Property doOnce = 0 Auto
TES4: float timer             → Float Property timer = 0.0 Auto
TES4: ref mySelf              → ObjectReference Property mySelf Auto
```

**Special cases:**
- Variables used as boolean flags (short with only 0/1 values) → `Bool Property ... Auto`
- `ref` variables that always hold actors → `Actor Property ... Auto`
- Quest variables accessed cross-script → public properties on Quest script

**`_property_refs` MUST be keyed on the Papyrus-safe name**

Everything that writes a property ref — `_add_scro_ref` (SCRO preload) and
`_convert_ref` (body conversion) — has to key `_property_refs` on
`_safe_property_name(edid)`, which is also what `_collect_scro_properties` writes
into the VMAD. Keying on the raw EditorID anywhere creates a *second* entry for
any EditorID that gets renamed, and many Oblivion EditorIDs collide with vanilla
Skyrim script names (`MS14` → `myMS14`).

When that happened, the generic `Quest` type seeded from the SCRO and the
specific `TES4_MS14Script` promoted by `_convert_ref` lived under different keys.
The "don't downgrade a promoted type" guard compared the wrong key and never
fired, so the *generic* type won the declaration and the script compiled to
`Quest Property myMS14` with a body calling `myMS14.QuestDone` →
`field or property QuestDone not found`. Same root cause for `GoHomeRythe`.

### Step 7: Expression Conversion

TES4 expressions have function calls inline:
```
if GetActorValue Health > 50
set myVar to GetDistance player
```

Papyrus requires:
```papyrus
If GetActorValue("Health") > 50
myVar = GetDistance(Game.GetPlayer())
```

Key transformations:
1. Actor value names become string parameters: `Health` → `"Health"`
2. Function calls get parenthesized arguments
3. `player` → `Game.GetPlayer()`
4. `set X to Y` → `X = Y`
5. `let X := Y` (OBSE) → `X = Y`
6. `X <> Y` → `X != Y`
7. `&&` / `||` already valid in Papyrus

### Step 8: INFO Result Script → VMAD Fragments

Each INFO ResultScript becomes a Papyrus fragment:

```papyrus
; TES4_TIF__<InfoFormID>.psc
ScriptName TES4_TIF__<InfoFormID> extends TopicInfo Hidden

Function Fragment_0()
  ; converted result script body
EndFunction
```

The import script must populate INFO VMAD with the fragment reference. VMAD structure:
```
VMAD {
  Version: 5
  ObjectFormat: 2
  Scripts: []  (empty — no persistent scripts)
  ScriptFragments: {
    UnknownByte: 0
    FileName: "TES4_TIF__<InfoFormID>"
    Fragments: [
      { Unknown: 0, ScriptName: "TES4_TIF__<InfoFormID>", FragmentName: "Fragment_0" }
    ]
  }
}
```

### Step 9: Quest Stage Script → VMAD Fragments

Each QUST stage script becomes a function in a quest fragment script:

```papyrus
; TES4_QF_<QuestEditorID>.psc
ScriptName TES4_QF_<QuestEditorID> extends Quest Hidden

Function Fragment_Stage_0010_Item_0()
  ; converted stage 10 script body
EndFunction

Function Fragment_Stage_0020_Item_0()
  ; converted stage 20 script body
EndFunction
```

QUST VMAD gets populated with:
```
VMAD {
  Scripts: [{ name: "TES4_QF_<QuestEditorID>", properties: [...] }]
  ScriptFragments: {
    FileName: "TES4_QF_<QuestEditorID>"
    Fragments: [
      { StageIndex: 10, Unknown: 0, StageIndex2: 10,
        ScriptName: "TES4_QF_<QuestEditorID>",
        FragmentName: "Fragment_Stage_0010_Item_0" },
      ...
    ]
  }
}
```

### Step 10: Polyfill Library

Create `TES4Polyfill.psc` — a utility script providing functions that don't exist in vanilla Papyrus:

```papyrus
ScriptName TES4Polyfill extends Quest
{Utility functions for converted TES4 scripts. Attach to a quest and access via property.}

; --- Random ---
Int Function GetRandomPercent() Global
  Return Utility.RandomInt(0, 99)
EndFunction

; --- Cell comparison ---
Bool Function IsInCell(ObjectReference akRef, Cell akCell) Global
  Return akRef.GetParentCell() == akCell
EndFunction

; --- Timer utility ---
Float Function GetSecondsPassed() Global
  ; Papyrus has no frame delta. Return update interval estimate.
  Return 0.5
EndFunction

; --- Actor value wrappers with TES4 AV name resolution ---
Float Function GetTES4ActorValue(Actor akActor, String avName) Global
  ; Maps TES4 attribute/skill names to TES5 equivalents
  If avName == "Strength"
    Return akActor.GetActorValue("UnarmedDamage")
  ElseIf avName == "Intelligence"
    Return akActor.GetActorValue("Magicka")
  ElseIf avName == "Willpower"
    Return akActor.GetActorValue("MagickaRate")
  ElseIf avName == "Agility"
    Return akActor.GetActorValue("SpeedMult")
  ElseIf avName == "Speed"
    Return akActor.GetActorValue("SpeedMult")
  ElseIf avName == "Endurance"
    Return akActor.GetActorValue("HealRate")
  ElseIf avName == "Personality"
    Return akActor.GetActorValue("Speechcraft")
  ElseIf avName == "Luck"
    Return 50.0  ; No equivalent
  ElseIf avName == "Fatigue"
    Return akActor.GetActorValue("Stamina")
  ElseIf avName == "Armorer"
    Return akActor.GetActorValue("Smithing")
  ElseIf avName == "Athletics"
    Return akActor.GetActorValue("Stamina")
  ElseIf avName == "Blade"
    Return akActor.GetActorValue("OneHanded")
  ElseIf avName == "Blunt"
    Return akActor.GetActorValue("TwoHanded")
  ElseIf avName == "HandToHand"
    Return akActor.GetActorValue("UnarmedDamage")
  ElseIf avName == "Mysticism"
    Return akActor.GetActorValue("Alteration")
  ElseIf avName == "Mercantile"
    Return akActor.GetActorValue("Speechcraft")
  ElseIf avName == "Security"
    Return akActor.GetActorValue("Lockpicking")
  ElseIf avName == "Acrobatics"
    Return akActor.GetActorValue("SpeedMult")
  Else
    Return akActor.GetActorValue(avName)
  EndIf
EndFunction

; --- PlayGroup approximation ---
Function PlayAnimationGroup(ObjectReference akRef, String groupName, Bool abForward) Global
  ; TES4 PlayGroup → TES5 animation event
  If groupName == "Forward"
    Debug.SendAnimationEvent(akRef, "IdleForceDefaultState")
  ElseIf groupName == "Backward"
    Debug.SendAnimationEvent(akRef, "IdleForceDefaultState")
  ElseIf groupName == "SpecialIdle"
    Debug.SendAnimationEvent(akRef, "IdleForceDefaultState")
  Else
    Debug.SendAnimationEvent(akRef, "IdleForceDefaultState")
  EndIf
EndFunction

; --- MessageBox with button tracking ---
; Note: Full MessageBox conversion requires creating Message form records.
; This provides a basic notification fallback.
Function ShowMessage(String text) Global
  Debug.Notification(text)
EndFunction
```

### Step 11: VMAD Binary Generation

The import script (`tes5_import`) needs a VMAD writer to attach scripts to records.

**VMAD binary format (version 5, object format 2):**
```
I16  version (5)
I16  objectFormat (2)
U16  scriptCount
  For each script:
    WSTRING  scriptName
    U8       flags (0=local, 1=inherited)
    U16      propertyCount
    For each property:
      WSTRING  propertyName
      U8       propertyType (1=Object, 2=String, 3=Int, 4=Float, 5=Bool)
      U8       propertyFlags (0x01=readonly)
      <value depending on type>
        Object:  U16(1) + U16(aliasId) + U32(formID)
        String:  WSTRING
        Int:     I32
        Float:   F32
        Bool:    U8
```

**For QUST ScriptFragments:**
```
After scripts array:
  U8   unknownByte (0)
  WSTRING fileName
  U16  fragmentCount
  For each fragment:
    U16  stageIndex
    U16  unknown (0)
    I32  stageIndex2 (same as above, signed)
    U8   unknown2 (1)
    WSTRING scriptName
    WSTRING fragmentName ("Fragment_Stage_NNNN_Item_0")
```

**For INFO ScriptFragments:**
```
After scripts array:
  U8   unknownByte (0)
  WSTRING fileName
  U8   fragmentCount (usually 1)
  For each fragment:
    U8   unknown (0)
    WSTRING scriptName
    WSTRING fragmentName ("Fragment_0")
  U8   unknown (1 if has condition scripts, 0 if not)
```

### Step 12: Pipeline Integration

Add to `run/convert.py` as Phase 4 (after Phase 3: Assets):

```
Phase 4: Script Conversion
  1. Load cross-reference graph from export data
  2. Convert SCPT → .psc files
  3. Convert INFO ResultScript → fragment .psc files
  4. Convert QUST stage scripts → fragment .psc files
  5. Generate polyfill library
  6. (Optional) Compile via PapyrusCompiler.exe
  7. Copy .psc to output/scripts/source/
  8. Copy .pex to output/scripts/compiled/ (if compiled)
```

---

## Conversion Quality Tiers

### Tier 1: Mechanically Correct (~60% of scripts)
Simple scripts with direct function mappings. No cross-script references. No complex expressions.

**Example:**
```oblivion
scriptname SE09RootGateScript
short open
begin onActivate
  if isActionRef player == 1
    message "The roots will not budge."
  endif
end
```
→
```papyrus
ScriptName TES4_SE09RootGateScript extends ObjectReference
Int Property open = 0 Auto
Event OnActivate(ObjectReference akActionRef)
  If akActionRef == Game.GetPlayer()
    Debug.Notification("The roots will not budge.")
  EndIf
EndEvent
```

### Tier 2: Needs Polyfill (~25% of scripts)
Uses functions without direct equivalents. Polyfill library provides replacements.

### Tier 3: Manual Review Required (~15% of scripts)
Complex patterns: state machines, multi-frame sequences, cross-script communication, MessageBox with choices, animation sequencing. Emit `;TODO:` markers.

---

## Testing Strategy

1. **Syntax validation**: Every .psc must parse without errors (basic Papyrus grammar check)
2. **Property completeness**: Every referenced FormID/EditorID has a property declaration
3. **Event coverage**: Every TES4 block maps to a Papyrus event
4. **Function coverage**: No unmapped functions appear without `;TODO:` markers
5. **Compilation test**: If PapyrusCompiler.exe is available, compile all .psc and report errors
6. **Round-trip test**: Convert sample scripts, verify output matches expected Papyrus

---

## Known Limitations

1. **No VMAD generation yet**: The import script doesn't write VMAD subrecords. Scripts will compile but not attach to records until VMAD writer is implemented.
2. **Cross-script variable access**: `set QuestRef.VarName to value` requires knowing which script type is on QuestRef. Partial solution via cross-reference graph.
3. **MessageBox choices**: Oblivion MessageBox with buttons + GetButtonPressed needs synthetic Message form records. Initially emit `;TODO:`.
4. **Frame-rate dependent timing**: `GetSecondsPassed` has no Papyrus equivalent. OnUpdate interval is an approximation.
5. **Cell/location mismatch**: TES4 `GetInCell` uses Cell records; TES5 `IsInLocation` uses Location records (not created by converter).
6. **Animation events**: TES4 PlayGroup animation names don't map 1:1 to Skyrim animation events.
7. **OBSE extensions**: Scripts using OBSE functions (ar_*, sv_*, etc.) cannot be mechanically converted.

---

## Creation Kit Papyrus Compiler Contracts (2026-07-12)

The bundled MIT compiler (`external/papyrus-compiler/papyrus.exe`) accepts code the
**real** compiler rejects, so a clean run there means nothing. Always validate with
`python tools/ck_compile_check.py` — it drives Skyrim's own
`Papyrus Compiler/PapyrusCompiler.exe`, the one the CK uses. A script that fails to
compile produces no `.pex`, so **the object it is attached to silently does nothing
in-game** — and it takes every script that references it down too (all member
accesses on it then fail), so one bad script can mask hundreds.

These contracts were each verified against `PapyrusCompiler.exe`:

| Contract | Symptom if violated |
|---|---|
| **ScriptName ≤ 38 chars.** Enforce via `constants.papyrus_script_name()` — the single source of truth for the `.psc` ScriptName, the `.psc` filename, AND the VMAD ScriptName (they must agree or binding breaks). Long names are truncated + given an MD5 tag, since many Oblivion EditorIDs differ only past the cut (`…RdCitadel0{1..5}SCRIPT`). | `"…" is too long, please shorten it to 38 characters or less`. 81 Oblivion scripts overflowed. |
| **No identifier may start with a lowercase `temp`.** The compiler mangles a variable `x` to the register `::x_var` and reserves the `::temp*` namespace for its own scratch registers. Case-sensitive, prefix-anchored: `temp`, `tempstage`, `template`, `temperature` all fail; `Temp`, `tmp`, `atemp` are fine. `_safe_property_name` capitalises the leading `t`. | `Attempting to add temporary variable named ::temp_var to free list multiple times` (558 errors from 15 scripts). |
| **No identifier may reuse ANY Skyrim script name** — not just native types. `Door`, `DarkBrotherhood`, `MS14` are all real Skyrim `.psc` files. The reserved list lives in `script_convert/papyrus_reserved.txt`, generated by `tools/gen_papyrus_reserved.py` from `Data/Scripts.zip` (Bethesda's pristine archive — do NOT read `Data/Source/Scripts`, which on a modded install also contains the user's mods and would make conversion non-reproducible). | `cannot name a variable or property the same as a known type or script`, then `Door is not a variable` / `cannot call the member function SetStage … on a type` at every use. |
| **A rename must reach EVERY emission path.** Renames are only recorded when `safe != vname` (**case-sensitive** — `temp`→`Temp` differs only in case, and a case-insensitive test skipped it). Handlers that emit an operand *raw* bypass renaming entirely: `setstage`'s stage arg, `startquest`/`stopquest`'s quest arg, and `_convert_ref`'s quest path all had to be routed through `_convert_expression` / `_safe_property_name`. | Declaration renamed but body still references the old name. |
| **No doubled cast.** `X as Int as Int` is a parse error. Emit casts via `ScriptConverter._cast()`, which is a no-op if the expression already ends in that cast. | `no viable alternative at input 'Int'` — 1965 errors, the single biggest class, from just 116 sites (the CK reports each one many times). |
| **Bool-returning functions can't meet a number.** TES4's `GetDetected`/`GetDead`/`GetDeadCount` return Int 0/1, so scripts write `getdetected X > 0` and `set n to getdeadcount X + 3`. Papyrus refuses to order or add a Bool. `_BOOL_CMP_RE` casts the call; `GetDeadCount` (which has no Papyrus equivalent at all, and whose operand is a *base* form, not a reference) now emits a typed `0`. | `cannot relatively compare variables of type bool`, `cannot add a bool to a int`. |
| **`OnEffectStart`/`OnEffectFinish` take `(Actor akTarget, Actor akCaster)`.** The signature is fixed by `ActiveMagicEffect.psc`; an invented one is rejected. | `the parameter types of function oneffectstart … do not match the parent script activemagiceffect`. |
| **A `Global` function may not touch a script property** (there is no instance). `TES4Polyfill` is all-Global, so `GetDayOfWeek` fetches GameDaysPassed via `Game.GetFormFromFile(0x39, "Skyrim.esm")` instead of holding a property. | `variable GameDaysPassed is undefined`. |
| **`GetIsID` → `GetBaseObject()`, never `(x as Actor).GetActorBase()`.** TES4's `GetIsID` compares against *any* base form (the SE38 oddities are MISC/INGR/WEAP/KEY, not actors). `GetBaseObject()` is declared on ObjectReference (no cast needed, works for actors too) and returns a Form, which compares against every base type. Operands are typed via `_record_type_to_base_papyrus` (NPC_/CREA → **ActorBase**, not Actor). | `cannot cast a tes4_se38oddityscript to a actor, types are incompatible`. |
| **`_property_refs` must be keyed on `_safe_property_name(edid)` on EVERY write path** (`_add_scro_ref` *and* `_convert_ref`) — that is also the name `_collect_scro_properties` puts in the VMAD. Keying on the raw EditorID makes a *second* entry for any renamed EditorID (`MS14` → `myMS14`), so the "don't downgrade a promoted type" guard compares the wrong key, never fires, and the generic `Quest` from the SCRO beats the specific `TES4_MS14Script` promoted from the body. | `field or property QuestDone not found` / `field or property GoHomeRythe not found` — a `Quest`-typed property with a body calling quest-script members on it. |
| **Always pass `-nocache` to `papyrus.exe compile`.** Its cache keys on the *source* only, not the output path: an unchanged `.psc` is treated as already compiled, so it **exits 0 and writes no `.pex` at all**. Static scripts whose text never varies between runs (`TES4_ShowBarterMenu`, `TES4_ShowTrainingMenu`, `TES4Polyfill`) hit this every time. | Reported as a bare `exit code 0` "failure" with no error text, and the object the script is attached to silently does nothing in-game. |

### Quest scripts: gate the GameMode body on `IsRunning()`

In TES4 a **quest script**'s `begin gamemode` block only executes *while the quest is
running*, so its body routinely assumes that. Skyrim raises `OnInit` on the quest
object whether or not the quest ever started, and **`SetStage` on a stopped quest
STARTS it** — so an ungated body silently auto-starts the quest at load. This is why
"Imperial Dragon Armor" appeared in the journal on a new game: `MQDragonArmorQuestSCRIPT`
runs `if gamedayspassed >= armorFinishDay: setstage MQDragonArmor 20`, and at day 1
vs. an unset `armorFinishDay` of 0 that is immediately true.

The converter now wraps the OnUpdate body of any `extends Quest` script in
`If (!IsRunning()) … Return`, re-arming the poll while stopped so it resumes on its own
once the quest legitimately starts (211 quest scripts affected).
