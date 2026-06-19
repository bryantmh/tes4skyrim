---
name: oblivion-dialog-system
description: >-
  Pure reference for how Oblivion's (TES4) dialogue, voice, and quest systems
  work — every record type (DIAL, INFO, QUST, IDLE, PACK), its binary layout,
  field meanings, enums, the condition (CTDA) system, the topic-type and
  add-topic model, result scripts, and a checklist of everything required for
  dialogue to fire in-engine. Use when authoring, reading, or debugging Oblivion
  dialogue/quest/voice records, when reasoning about how a topic reaches an NPC,
  or when you need the exact structure of any of these records. This is a
  description of vanilla Oblivion only — it contains no conversion/import logic.
---

# Oblivion Dialogue, Voice & Quest System

This skill is a pure reference for how vanilla Oblivion (TES4) routes dialogue.
It describes only how the game itself works — it deliberately contains **no**
conversion or import logic.

It is built from two authoritative, grounded sources in this repo:

1. **xEdit Core record definitions** — `references/xEdit/Core/wbDefinitionsTES4.pas`
   (and shared pieces in `wbDefinitionsCommon.pas`): the binary layout of every
   record; verbatim field names/sizes/enums.
2. **Real Oblivion.esm data** — `export/Oblivion.esm/` (a pure KEY=VALUE dump of
   the actual master file, one file per record type), giving real field values.

When the answer needs an exact byte layout or full enum, open the matching
reference file rather than guessing:

- **`references/records.md`** — every record's complete subrecord-by-subrecord
  layout, field types, sizes, and full enums (DIAL, INFO, QUST, IDLE, PACK).
- **`references/conditions.md`** — the CTDA condition system: 24-byte layout,
  operator/flag byte, OR-chaining, the parameter-type table, and the function
  indices that matter for dialogue (GetIsID, GetStage, GetQuestRunning…).
- **`references/checklist.md`** — the full "everything that must be true for a
  line to fire" checklist, plus common failure modes and their fixes.

---

## The mental model (read this first)

Oblivion dialogue grew out of Morrowind's text-based system. It is a **filtered,
priority-ordered query** over INFO records, with two ideas that shape everything:

1. **Topics are global objects, not branches.** A DIAL (topic) is a named bucket
   of INFOs. The same topic can be shared by many NPCs and many quests.
2. **The `AddTopic` mechanic gates visibility.** A topic only appears in an NPC's
   list once it has been "added" — usually by a script command (`AddTopic`) or by
   an INFO's Add-Topics list (the `NAME` subrecord). Conditions then filter which
   INFO under that topic actually plays. AddTopic visibility is the central
   mechanic of Oblivion dialogue.

When an actor needs something to say, the engine looks at the topics available to
that actor, and for each it picks the first INFO whose conditions all pass, in
quest-priority order.

### The record hierarchy

```
QUST (Quest)        owns dialogue via topic association (DIAL.QSTI). Holds a
 │                  priority, flags, stages (journal + result scripts), targets,
 │                  conditions, and an attached SCPT script (SCRI).
 │
 ├── DIAL (Topic)   a named bucket of INFOs with a Type (Topic / Conversation /
 │    │             Combat / Persuasion / Detection / Service / Misc). Lists the
 │    │             quest(s) it's associated with (QSTI) and its display text
 │    │             (FULL). The INFOs live in the topic's GRUP.
 │    │
 │    └── INFO      ONE spoken exchange: a Type + NextSpeaker + Flags (DATA),
 │                  the owning quest (QSTI), one or more Responses (voiced text
 │                  with an emotion), the conditions (CTDAs) that gate it,
 │                  Choices (TCLT) that branch to follow-up topics, Add-Topics
 │                  (NAME) that reveal new topics, Link-From (TCLF/PNAM) wiring,
 │                  and an optional result script (SCHR/SCDA/SCTX/SCRO).

IDLE (Idle Animation)  a .kf animation gated by conditions; chained via DATA
                       (idle parent / previous). Played during AI and dialogue.

PACK (AI Package)      what an NPC does when not in dialogue (Find/Follow/Eat/
                       Sleep/Wander/Travel/…). Gated by conditions; can offer
                       services. Drives where/when an NPC is available to talk.
```

### Voice in Oblivion

Oblivion has **no Voice Type record**. Voice assignment is implicit: each NPC has
a race and gender, and the voice files for a spoken line are stored by
race+gender under
`Sound\Voice\<plugin>\<Race>\<Gender>\<dialogFID>_<infoFID>.mp3`. There is no
voice-type record and no voice-type condition — the engine resolves the audio
file from the speaking NPC's race+gender and the INFO's FormID.

---

## What must be true for a line to actually fire

(Full version with fixes in `references/checklist.md`. The essentials:)

1. **The topic is available to the NPC** — it has been added (via `AddTopic`
   script, or an INFO's Add-Topics `NAME` list, or it is an always-available
   greeting/service topic).
2. **The owning quest is enabled/running** if the dialogue is quest-gated.
   Quest-associated topics are implicitly gated by their quest (QSTI).
3. **The INFO's conditions all pass** — e.g. `GetIsID(npc)` to bind a line to a
   specific NPC, `GetStage`/`GetQuestRunning` to gate by quest progress.
4. **The INFO has at least one Response (TRDT + NAM1)** with response text.
5. **Voice audio exists** at
   `Sound\Voice\<plugin>\<Race>\<Gender>\<dialogFID>_<infoFID>.mp3`, or the line
   plays with subtitle only / silently.

---

## Quick subrecord cheat-sheet

Exact bytes, enums, and edge cases are in `references/records.md`. This table is
for fast recall only.

| Record | Key subrecords (in order) | Notes |
|--------|---------------------------|-------|
| **DIAL** | EDID, QSTI[](→QUST), QSTR[](→QUST), FULL, DATA(U8 Type), INOM, INOA | DATA.Type: 0 Topic, 1 Conversation, 2 Combat, 3 Persuasion, 4 Detection, 5 Service, 6 Misc. INOM/INOA = INFO ordering (internal). |
| **INFO** | DATA(3B: Type/NextSpeaker/Flags), QSTI(→QUST), TPIC(→DIAL), PNAM(prev INFO), NAME[](add topics), Responses[(TRDT 12B + NAM1 + NAM2)], CTDAs, TCLT[](choices), TCLF[](link from), result script (SCHR/SCDA/SCTX/SCRO) | No keyword/voice-type system. NextSpeaker enum: 0 Target, 1 Self, 2 Either. |
| **QUST** | EDID, SCRI(→SCPT), FULL, ICON, DATA(2B: Flags/Priority), CTDAs, Stages[INDX + log entries(QSDT/CTDAs/CNAM/result script)], Targets[QSTA + CTDAs] | DATA.Flags: 0x01 Start game enabled, 0x04 Allow repeated conversation topics, 0x08 Allow repeated stages. |
| **IDLE** | EDID, Model(MODL/MODB/MODT), CTDAs, ANAM(anim group section U8), DATA(related idle: parent + previous sibling FormIDs) | A .kf animation gated by conditions. |
| **PACK** | EDID, PKDT(general: flags+type), PLDT(location), PSDT(schedule), PTDT(target), CTDAs | Type enum: 0 Find, 1 Follow, 2 Escort, 3 Eat, 4 Sleep, 5 Wander, 6 Travel, 7 Accompany, 8 Use item at, 9 Ambush, 10 Flee, 11 Cast magic. |

---

## CTDA conditions (the gate)

Every TES4 condition is exactly **24 bytes**. Critical
offsets (full detail and the parameter-type table in `references/conditions.md`):

- byte[0]   = operator + flags. **Bit 0 (0x01) = OR** with the next condition;
  bit 1 (0x02) = Run on target; **bit 2 (0x04) = Use Global**.
- bytes[4:8]  = comparison value (float, or a GLOB FormID if Use Global).
  `1.0` = `00 00 80 3F`.
- bytes[8:10] = **function index** (U16 LE); bytes[10:12] unused.
- bytes[12:16] = param1, bytes[16:20] = param2.
- bytes[20:24] = unused.

Function indices you will see constantly in dialogue (decimal / hex):

| Func | Name | Use |
|------|------|-----|
| 72 / 0x48 | `GetIsID` | Restrict a line to a specific NPC/base object. |
| 58 / 0x3A | `GetStage` | Gate by quest stage (param2 is the stage). |
| 59 / 0x3B | `GetStageDone` | Whether a stage has run. |
| 56 / 0x38 | `GetQuestRunning` | Gate by whether a quest is active. |
| 53 / 0x35 | `GetScriptVariable` | Read a script variable. |
| 79 / 0x4F | `GetQuestVariable` | Read a quest-script variable. |
| 69 / 0x45 | `GetItemCount` | Inventory check. |
| 76 / 0x4C | `GetDisposition` | NPC disposition toward the player. |

**OR-chain rule:** to mean "A OR B OR C", set the OR flag on every CTDA in the
chain **except the last**; the last is AND.

---

## How to use this skill

- **Understanding a record** → `references/records.md` for the exact layout, then
  check a real example in `export/Oblivion.esm/<TYPE>.txt`.
- **Reasoning about why a topic reaches (or doesn't reach) an NPC** → walk
  `references/checklist.md`; it's usually AddTopic visibility, a quest not
  running, or a `GetIsID` condition.
- **Reading/decoding a CTDA** → `references/conditions.md` for offsets, the OR
  rule, the parameter-type table, and function indices.
- **Inspecting real data** → the dump in `export/Oblivion.esm/` (DIAL.txt,
  INFO.txt, QUST.txt, IDLE.txt, PACK.txt) is the actual Oblivion.esm content.
