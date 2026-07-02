---
name: skyrim-dialog-system
description: >-
  Pure reference for how Skyrim's (TES5) dialogue, voice, and quest systems work
  — every record type (DIAL, INFO, QUST, DLBR, DLVW, SCEN, VTYP, IDLE), its
  binary layout, field meanings, enums, the condition (CTDA) system, the
  voice-routing pipeline, and a checklist of everything required for dialogue to
  fire in-engine. Use when authoring, reading, or debugging Skyrim
  dialogue/quest/voice records, when reasoning about how a topic reaches an NPC,
  or when you need the exact structure of any of these records. This is a
  description of vanilla Skyrim only — it contains no conversion/import logic.
---

# Skyrim Dialogue, Voice & Quest System

This skill is a pure reference for how vanilla Skyrim (TES5) routes dialogue. It
describes only how the game itself works — it deliberately contains **no**
conversion or import logic.

It is built from two authoritative, grounded sources in this repo:

1. **xEdit Core record definitions** — `references/xEdit/Core/wbDefinitionsTES5.pas`
   (the binary layout of every record; verbatim field names/sizes/enums).
2. **Real Skyrim.esm data** — `references/Skyrim.esm` (dumped via
   `tools/tes5_esm_reader.py`), giving real field values.

When the answer needs an exact byte layout or full enum, open the matching
reference file rather than guessing:

- **`references/records.md`** — every record's complete subrecord-by-subrecord
  binary layout, field types, sizes, and full enums (DIAL, INFO, QUST, DLBR,
  DLVW, SCEN, VTYP, IDLE).
- **`references/conditions.md`** — the CTDA condition system: 32-byte layout,
  operator/flag byte, OR-chaining, and the function indices that matter for
  dialogue (GetIsVoiceType, GetIsID, GetStage, GetQuestRunning, GetInCell…).
- **`references/checklist.md`** — the full "everything that must be true for a
  line to fire" checklist, plus the most common failure modes and their fixes.

---

## The mental model (read this first)

Skyrim dialogue is a **filtered query system**, not a script. When an actor needs
something to say (player greets them, combat starts, player picks a topic), the
engine collects every **INFO** whose conditions all evaluate true, in priority
order, and speaks the first valid one. **Conditions are the only gate.** If you
want a line restricted to one NPC, the restriction must live in a CTDA condition
on the INFO.

The record hierarchy, top to bottom:

```
QUST (Quest)            owns dialogue; must be running/enabled for its topics
 │                      to be eligible. Holds stages, objectives, aliases, and
 │                      quest-level dialogue conditions.
 │
 ├── DLBR (Branch)      groups topics into a conversation flow. Points at its
 │    │                 owning QUST (QNAM) and its first DIAL (SNAM). Flags say
 │    │                 whether it's Top-Level (appears in the menu) or Normal
 │    │                 (only reachable by being linked to).
 │    │
 │    └── DIAL (Topic)  a prompt + a bucket of INFOs. Has a Category + Subtype
 │         │            that classify it (Topic / Combat / Detection / Misc…),
 │         │            a 4-char Subtype code (SNAM), and points back at its
 │         │            QUST (QNAM) and branch (BNAM).
 │         │
 │         └── INFO     ONE actual spoken exchange: one or more Responses
 │                      (lines of voiced text), the conditions (CTDAs) that
 │                      gate it, optional VMAD script fragments, and "Link To"
 │                      (TCLT) entries that chain to follow-up topics.
 │
 └── DLVW (View)        Creation-Kit-only UI metadata listing a quest's branches
                        and topics. Not gameplay-critical but vanilla has them.

VTYP (Voice Type)       the routing key. Every speaking NPC has a voice type
                        (VTCK). Almost every INFO carries a GetIsVoiceType
                        condition so the engine plays the right recording.

SCEN (Scene)            scripted multi-actor sequences (cutscene-like). Drives
                        DIAL topics through phases/actions. Not needed for
                        ordinary NPC chatter.

IDLE (Idle Animation)   referenced by INFO responses (SNAM/LNAM) for speaker /
                        listener gestures while a line plays.
```

### Voice routing in one paragraph

A speaking NPC_ has `VTCK` → a **VTYP** record (e.g. `MaleNord`). When the engine
picks an INFO to play, it needs the audio file recorded for *that* voice type.
The standard way an INFO is bound to a voice type is a **`GetIsVoiceType(VTYP)
== 1.0`** CTDA condition (function index **426**). Multiple voice types are
expressed as an **OR-chain** of GetIsVoiceType CTDAs. Voice files live at
`Sound\Voice\<plugin>\<VoiceType>\<infoFID>_<respNum>.fuz`. An INFO with **no**
GetIsVoiceType condition will (a) be eligible for every NPC and (b) try to
export an audio file for every voice type — both almost always undesirable.

---

## What must be true for a line to actually fire

(Full version with fixes in `references/checklist.md`. The essentials:)

1. **The owning QUST is eligible** — `Start Game Enabled (0x0001)` (and usually
   `Starts Enabled 0x0010`) so it's running, OR the quest is started by other
   means. Dialogue-only quests in vanilla use `DNAM Flags = 0x0011`.
2. **Every DIAL has a `QNAM`** pointing at a running quest. A topic with no quest
   owner is ignored.
3. **The branch (DLBR) is correct** — Top-Level (`DNAM 0x01`) for things that
   appear directly in the menu; Normal (`DNAM 0x00`) for chain/linked topics.
4. **The INFO's conditions all pass** — including a `GetIsVoiceType` so the
   right voice says it, and (for conversation topics) an identity gate such as
   `GetIsID` so it doesn't appear on every NPC.
5. **The INFO has at least one Response (TRDT + NAM1)** with valid response text.
6. **A SEQ file exists** for Start-Game-Enabled quests (since game v1.7) or their
   dialogue/scenes won't initialize.
7. **Voice audio exists** in `.fuz` (or `.xwm`+`.lip`) at the right path for the
   NPC's voice type, or the line plays silently / not at all.

---

## Quick subrecord cheat-sheet

Exact bytes, enums, and edge cases are in `references/records.md`. This table is
for fast recall only.

| Record | Key subrecords (in order) | Notes |
|--------|---------------------------|-------|
| **DIAL** | EDID, FULL, PNAM(float priority, dflt 50.0), BNAM(→DLBR), QNAM(→QUST), DATA(4B), SNAM(U32 4-char code), TIFC(U32 info count) | DATA on-disk = TopicFlags(U8)+**Category(U8)**+**Subtype(U16)** (the xEdit order — swapping it crashes at startup; see records.md ⚠). Subtype NUMBERS come from real data, not xEdit's shifted display enum (real Hello=73). SNAM raw LE ASCII (e.g. `CUST`,`HELO`) mirrors the subtype and is what the engine keys on. |
| **INFO** | EDID, VMAD, ENAM(4B flags+reset), TPIC(→DIAL), PNAM(prev INFO), CNAM(favor U8), TCLT[](→DIAL/INFO links), Responses[(TRDT 24B + NAM1 + NAM2 + NAM3 + SNAM/LNAM idle)], CTDAs, RNAM(prompt), ANAM(speaker) | A child INFO references its parent topic via the GRUP. ENAM flags incl. Goodbye(0x01), Random(0x02), Say once(0x04). |
| **QUST** | EDID, VMAD, FULL, DNAM(12B general), ENAM(event 4B), QTGL[], FLTR, quest CTDAs, INDX/QSDT stages, QOBJ/QSTA objectives, ANAM(next alias id), ALST/ALLS aliases, NNAM, QSTA targets | DNAM = Flags(U16)+Priority(U8)+FormVer(U8)+Unknown(4B)+Type(U32). |
| **DLBR** | EDID, QNAM(→QUST, req), TNAM(category U32: 0=Player,1=Command), DNAM(flags U32: 0x01 Top-Level, 0x02 Blocking, 0x04 Exclusive), SNAM(→DIAL starting topic, req) | One branch per conversation flow. |
| **DLVW** | EDID, QNAM(→QUST), BNAM[](→DLBR), TNAM[](→DIAL), ENAM(view category U32), DNAM(show all text U8) | CK UI metadata only. |
| **VTYP** | EDID, DNAM(flags U8: 0x01 Allow Default Dialog, 0x02 Female) | The routing key. ~143 in Skyrim.esm. |
| **SCEN** | EDID, VMAD, FNAM(flags U32), Phases[(HNAM/NAM0/conditions/NEXT)], Actors[(ALID/LNAM/DNAM)], Actions[(ANAM type/…)] | Multi-actor scripted sequences. |
| **IDLE** | EDID, conditions, DATA, anim event | Referenced by INFO response SNAM/LNAM. |

---

## CTDA conditions (the heart of routing)

Every condition is exactly **32 bytes**. Critical offsets (full detail in
`references/conditions.md`):

- byte[0]   = operator + flags. **Bit 0 (0x01) = OR** with the next condition.
- byte[0]   (bit 2, 0x04) = **Use Global** (comparison value is a GLOB FormID).
- bytes[4:8]  = comparison value (float). `1.0` = `00 00 80 3F`.
- bytes[8:10] = **function index** (U16 LE).
- bytes[12:16] = param1, bytes[16:20] = param2.

Function indices you will see constantly in dialogue:

| Func | Name | Use |
|------|------|-----|
| 426 | `GetIsVoiceType` | Route line to a voice type. On nearly every INFO. |
| 72  | `GetIsID` | Restrict line to a specific NPC (conversation topics). |
| 71  | `GetInCell` | Location gating (city-specific greetings/gossip). |
| 58  | `GetStage` | Gate by quest stage. |
| 56  | `GetQuestRunning` | Gate by whether a quest is active. |
| 67  | `GetCurrentAIProcedure` | AI-state gating on barks. |

**OR-chain rule:** to mean "voice type A OR B OR C", set OR on every CTDA in the
chain **except the last**; the last is AND. A trailing OR flag leaks into the
following (unrelated) condition.

---

## How to use this skill

- **Understanding a record** → `references/records.md` for the exact layout, then
  check a real example in `references/Skyrim.esm/<TYPE>.txt`.
- **Reasoning about why a topic reaches (or doesn't reach) an NPC** → walk
  `references/checklist.md`; it's almost always a condition (GetIsVoiceType or
  GetIsID) or a quest not running.
- **Reading/understanding a CTDA** → `references/conditions.md` for offsets,
  OR-chain rules, and function indices.
- **Inspecting real data** → `python tools/tes5_esm_reader.py references/Skyrim.esm
  --types DIAL INFO QUST DLBR DLVW VTYP --outdir temp/skyrim_dialog_dump`.
