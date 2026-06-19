# Oblivion Dialogue/Voice/Quest — Record Definitions

Authoritative binary layout of every dialogue-related Oblivion (TES4) record.
Field names, types, sizes, and enums are taken verbatim from
`references/xEdit/Core/wbDefinitionsTES4.pas` (and shared definitions in
`wbDefinitionsCommon.pas`); example values are from the real Oblivion.esm dump in
`export/Oblivion.esm/`.

**Type legend:** `U8/U16/U32` = unsigned int of that bit width (little-endian);
`S8/S16/S32` = signed; `float` = 32-bit IEEE; `FormID` = U32 reference. All
multi-byte values are little-endian.

**Oblivion has no Voice Type record.** Voice is resolved implicitly from the
speaking NPC's race + gender; there is no voice-type record or voice-type
condition (see SKILL.md "Voice in Oblivion").

---

## DIAL — Dialog Topic

A named bucket of INFOs. The INFOs themselves live in the topic's GRUP (group),
not inline in the DIAL record.

| Subrecord | Type | Meaning |
|-----------|------|---------|
| `EDID` | string | Editor ID (e.g. `ADMIREHATE`, `GREETING`). |
| `QSTI` | FormID[]→QUST | Quest(s) this topic is associated with. A topic may list several quests; this association implicitly gates the topic by quest. |
| `QSTR` | FormID[]→QUST | Secondary quest list ("Quests?"); rarely populated. |
| `FULL` | string | Topic text — the prompt/name shown for the topic. |
| `DATA` | U8 | **Topic Type** enum (required). See below. |
| `INOM` | FormID[]→INFO | INFO order (masters only) — internal ordering metadata. |
| `INOA` | FormID[]→INFO | INFO order (all previous modules) — internal ordering metadata. |

**DATA Type enum (U8):**
`0` Topic, `1` Conversation, `2` Combat, `3` Persuasion, `4` Detection,
`5` Service, `6` Miscellaneous.

> Special always-available topics use reserved EditorIDs (e.g. `GREETING`,
> `GOODBYE`, `HELLO`, service topics). These don't need AddTopic — the engine
> queries them directly for the relevant situation.

Real record (from `export/Oblivion.esm/DIAL.txt`):
```
Signature=DIAL
FormID=000000AA
EditorID=ADMIREHATE
QuestCount=2
Quest[0]=0001E722        # QSTI
Quest[1]=00010602        # QSTI
FULL=ADMIRE_HATE
DATA.Type=3              # Persuasion
```
(In the dump, `QSTI` is emitted as `QuestCount=N` + `Quest[i]`. `ParentDIAL` in
the dump is an exporter grouping pointer, not a native subrecord.)

---

## INFO — Dialog Response

ONE spoken exchange. Lives in the GRUP of its parent DIAL.

| Subrecord | Type | Meaning |
|-----------|------|---------|
| `DATA` | struct (3B) | Type(U8) + NextSpeaker(U8) + Flags(U8). **Required.** |
| `QSTI` | FormID→QUST | The owning quest. |
| `TPIC` | FormID→DIAL | The owning topic. |
| `PNAM` | FormID→INFO | Previous INFO (ordering/chaining within a topic). |
| `NAME` | FormID[]→DIAL | **Add Topics** — topics revealed to the player when this info plays. |
| Responses[] | array | One or more `Response` structs (see below). |
| `CTDA` | array | The conditions gating this INFO (see `conditions.md`). |
| `TCLT` | FormID[]→DIAL | **Choices** — follow-up topics offered after this line. |
| `TCLF` | FormID[]→DIAL | **Link From** — topics this info can be reached from. |
| (result script) | SCHR/SCDA/SCTX/SCRO | The result script that runs with the line — see below. |

### DATA breakdown
- **Type (U8):** same enum as DIAL.Type (0 Topic … 6 Misc).
- **Next Speaker (U8):** `0` Target, `1` Self, `2` Either.
- **Flags (U8):** `0x01` Goodbye, `0x02` Random, `0x04` Say Once,
  `0x08` Run Immediately, `0x10` Info Refusal, `0x20` Random End,
  `0x40` Run for Rumors.

### Response struct (one per spoken line; an INFO may have several)
| Field | Type | Meaning |
|-------|------|---------|
| `TRDT` | struct (12B) | Response Data — see below. |
| `NAM1` | string | **Response Text** — the spoken/subtitled line. |
| `NAM2` | string | **Actor notes** — voice-direction notes for the actor (e.g. "Shock, then fear"). |

**TRDT (12 bytes):** Emotion Type(U32) + Emotion Value(S32) + Unused(4)... in the
xEdit struct it is EmotionType(U32) + EmotionValue(S32) + Unused(4) +
ResponseNumber(U8) + Unused(3). Emotion Type enum: `0` Neutral, `1` Anger,
`2` Disgust, `3` Fear, `4` Sad, `5` Happy, `6` Surprise. Emotion Value is 0–100.
Response Number is the 1-based order when an info has multiple lines.

### Result script
A small script that runs when the line is delivered. Stored as the standard TES4
script block: `SCHR` (basic script data), `SCDA` (compiled bytecode), `SCTX`
(source text, e.g. `AddTopic SEDyusTormentTopic`), and `SCRO` (FormID operands
the script references). This is the imperative scripting that, among other
things, calls `AddTopic`, `SetStage`, `StopQuest`, etc.

Real records (from `export/Oblivion.esm/INFO.txt`):
```
# INFO bound to a specific NPC (GetIsID) with a result script
Signature=INFO
FormID=00072DAB
ParentDIAL=000000C8
DATA.DialogType=0  DATA.NextSpeaker=0  DATA.Flags=0
QSTI.Quest=00071FE1
Response[0]: EmotionType=3(Fear) EmotionValue=100 ResponseNumber=1
   ResponseText="You... you're a cat! Get away from me!"
   ActorNotes="Shock, then fear"
Response[1]: EmotionType=1(Anger) EmotionValue=100 ResponseNumber=2
   ResponseText="Kill! Kill the cat! Good dog."
Condition[0]: GetIsID == 1.0   (func 72, param1 = NPC FormID)
Condition[1]: GetItemCount      (func 69)
ResultScript: "Set SE43.DogAttackPC to 1 / StopQuest SE43"

# INFO with choices (TCLT) and a GetStage condition
Signature=INFO
FormID=00014B4C
QSTI.Quest=000135B2
Condition[0]: GetIsID == 1.0    (func 72)
Condition[1]: GetStage >= 5.0   (func 58, param1 = quest 000135B2)
Choice[0]=00014B3F   Choice[1]=00014B3E    # TCLT

# INFO with Add-Topic (NAME) and GetStageDone
Signature=INFO
FormID=000807A6
QSTI.Quest=0005E2B7
AddTopic[0]=...                  # NAME
Condition[0]: GetIsID           (func 72)
Condition[1]: GetStageDone(stage 200)  (func 59)
ResultScript: "AddTopic SEDyusTormentTopic"
```

---

## QUST — Quest

Owns dialogue (via topic association), journal stages, targets, and an attached
script.

| Subrecord | Type | Meaning |
|-----------|------|---------|
| `EDID` | string | Editor ID. |
| `SCRI` | FormID→SCPT | Attached quest script. |
| `FULL` | string | Quest journal name. |
| `ICON` | string | Journal icon path. |
| `DATA` | struct (2B) | Flags(U8) + Priority(U8). **Required.** |
| `CTDA` | array | Quest-level conditions. |
| Stages[] | array | `INDX` (stage index) + Log Entries — see below. |
| Targets[] | array | `QSTA` (target ref + flags) + CTDAs — see below. |

### DATA (2 bytes)
- **Flags (U8):** `0x01` Start game enabled, `0x04` Allow repeated conversation
  topics, `0x08` Allow repeated stages. (Bit `0x02` is unused/unnamed.)
- **Priority (U8):** higher-priority quests' dialogue is offered first.

### Stages
- `INDX` (S16): the stage index (e.g. 10, 15, 200).
- Each stage has one or more **Log Entries**:
  - `QSDT` (U8): Stage Flags — `0x01` Complete quest.
  - `CTDA` array: conditions on the log entry.
  - `CNAM` (string): the journal log text shown to the player.
  - result script (SCHR/SCDA/SCTX/SCRO): runs when the stage is set (e.g.
    `SetEssential SEHorkvirBearArmMania 0`).

### Targets
- `QSTA` struct: Target FormID (→REFR/ACRE/ACHR) + Flags(U8: `0x01` Compass
  marker ignores locks) + Unused(3).
- `CTDA` array: conditions on the target (e.g. show this target only at a
  certain stage).

Real records (from `export/Oblivion.esm/QUST.txt`):
```
# Minimal quest
Signature=QUST
FormID=00091D1A  EditorID=SEHaskillSummonQuest
SCRI=00091D19
DATA.Flags=1(Start game enabled)  DATA.Priority=0

# Full quest with conditions, stages, targets
Signature=QUST
FormID=00081DD5  EditorID=SE46
SCRI=00081F5B  FULL="The Great Divide"  ICON="Quests\iconSEMisc.dds"
DATA.Flags=1  DATA.Priority=40
ConditionCount=45
Stage[0].Index=10
   Stage[0].Log[0].Flags=0
   Stage[0].Log[0].Text="A resident of Split has told me I should speak to Horkvir Bear-Arm..."
Stage[3].Index=15
   Stage[3].Log[0].Text="I've agreed to kill all of the Manics living in Split."
   Stage[3].Log[0].ResultScript="SetEssential SEHorkvirBearArmMania 0"
   Stage[3].Log[0].SCRO[0]=0001558F
Stage[11].Index=200
   Stage[11].Log[0].Flags=1(Complete quest)
   Stage[11].Log[0].Text="Since all the Manic duplicates in Split are dead, Horkvir Bear-Arm gave me my reward."
Target[0].FormID=0001656A  Target[0].Flags=...
   Target[0].Condition[0]: GetQuestVariable (func 79)
   Target[0].Condition[1]: GetStage (func 58, this quest)
```

---

## IDLE — Idle Animation

A keyframed `.kf` animation, gated by conditions and chained to other idles.
Played by the AI/animation system and during dialogue gestures.

| Subrecord | Type | Meaning |
|-----------|------|---------|
| `EDID` | string | Editor ID (e.g. `EscortWaitingWave`). |
| Model | MODL/MODB/MODT | Animation: `MODL` = `.kf` path, `MODB` = bound radius, `MODT` = texture/hash data. |
| `CTDA` | array | Conditions under which the idle may play. |
| `ANAM` | U8 | Animation Group Section (which animation group/sub-section). |
| `DATA` | FormID[2] | Related idles: `[0]` Parent, `[1]` Previous Sibling (0 = none). |

Real record (from `export/Oblivion.esm/IDLE.txt`):
```
Signature=IDLE
FormID=000274C9  EditorID=EscortWaitingWave
Model.MODL="Characters\_Male\IdleAnims\FollowMe.kf"  Model.MODB=0.0
ConditionCount=2     # func 111 and func 110
DATA.IdleParent=00000000   DATA.IdlePrev=000477FF
```

---

## PACK — AI Package

What an NPC does when not in dialogue. Determines where and when an NPC is
present and available to talk, and can offer services (which surface as dialogue
options). Gated by conditions.

| Subrecord | Type | Meaning |
|-----------|------|---------|
| `EDID` | string | Editor ID. |
| `PKDT` | struct | **General**: Flags + Type (+ unused). Two layout variants (old: U16 flags; new: U32 flags) — the dump labels which via `PKDT.Format`. |
| `PLDT` | struct | **Location**: Type(S32) + Location(union, by type) + Radius(S32). |
| `PSDT` | struct | **Schedule**: Month(S8) + DayOfWeek(S8) + Date(U8) + Time(S8, -1=any) + Duration(S32). |
| `PTDT` | struct | **Target**: Type(S32) + Target(union) + Count(S32). |
| `CTDA` | array | Conditions gating the package. |

### PKDT
- **Flags (U16 old / U32 new):** `0x01` Offers services, `0x02` Must reach
  location, `0x04` Must complete, `0x08`/`0x10`/`0x20` Lock doors (start/end/
  location), `0x40`/`0x80`/`0x100` Unlock doors (start/end/location),
  `0x200` Continue if PC near, `0x400` Once per day, `0x1000` Skip fallout
  behavior, `0x2000` Always run, `0x20000` Always sneak, `0x40000` Allow
  swimming, `0x80000` Allow falls, `0x100000` Armor unequipped,
  `0x200000` Weapons unequipped, `0x400000` Defensive combat, `0x800000` Use
  horse, `0x1000000` No idle anims.
- **Type (U8) enum:** `0` Find, `1` Follow, `2` Escort, `3` Eat, `4` Sleep,
  `5` Wander, `6` Travel, `7` Accompany, `8` Use item at, `9` Ambush,
  `10` Flee not combat, `11` Cast magic.

### PLDT location-type (S32)
`0` Near reference, `1` In cell, `2` Near current location, `3` Near editor
location, `4` Object ID, `5` Object type. The `Location` union holds a FormID
(REFR/CELL/object) or an object-type integer depending on the type.

### PSDT schedule
`Day of week` enum includes the Tamrielic day names (Sundas…Loredas) plus
combination/range entries; `-1` = Any. `Time` is the hour (`-1` = any).

### PTDT target-type (S32)
`0` Specific reference, `1` Object ID, `2` Object type. The `Target` union holds
a reference FormID (e.g. `00000014` = Player), object FormID, or object-type int.

Real records (from `export/Oblivion.esm/PACK.txt`):
```
# Wander package at a location, schedule 19:00 for 2h, gated by GetQuestRunning
Signature=PACK
FormID=00097641  EditorID=SEBhishaWorship19x2
PKDT.Format=new  PKDT.Flags=0  PKDT.Type=6(Travel/Wander)
PLDT.Type=0  PLDT.Location=00066D13  PLDT.Radius=0
PSDT.Month=-1  PSDT.DayOfWeek=-1  PSDT.Date=0  PSDT.Time=19  PSDT.Duration=2
Condition[0..2]: GetQuestRunning  (func 56)

# Force-greet package targeting the Player, gated by GetStage/GetScriptVariable
Signature=PACK
FormID=00097640  EditorID=SE10DyloraForceGreetPlayerDeath
PKDT.Format=new  PKDT.Type=0(Find)
PTDT.Type=0  PTDT.Target=00000014(Player)  PTDT.Count=0
Condition[0]: GetStage (func 58)
Condition[1]: GetScriptVariable (func 53)
Condition[2]: GetQuestVariable (func 79)
```

---

## Cross-reference summary (who points at whom)

```
DIAL.QSTI ─► QUST          (topic ↔ quest association; implicit gating)
INFO.QSTI ─► QUST          INFO.TPIC ─► DIAL (owning topic)
INFO.PNAM ─► INFO          INFO.NAME ─► DIAL (add topics)
INFO.TCLT ─► DIAL          (choices / follow-up topics)
INFO.TCLF ─► DIAL          (link from)
INFO result script SCRO ─► (any referenced record)
QUST.SCRI ─► SCPT          QUST.Target QSTA ─► REFR/ACRE/ACHR
IDLE.DATA ─► IDLE          (parent / previous idle)
PACK.PLDT/PTDT ─► REFR/CELL/object   PACK conditions ─► QUST etc.
```

Voice files are resolved by the speaking NPC's **race + gender** (no record
link):
`Sound\Voice\<plugin>\<Race>\<Gender>\<dialogFID>_<infoFID>.mp3`.
