# Oblivion Conditions (CTDA) — the dialogue gate

Conditions are how Oblivion decides who can say what and when. Each `CTDA`
subrecord is a single check that evaluates true/false. An INFO (or quest, quest
stage, target, idle, package) is eligible only when **all** its conditions pass —
except where conditions are joined by OR. This file documents the exact byte
layout, the OR/flag mechanics, the parameter-type table, and the function indices
that matter for dialogue.

This is a pure description of vanilla Oblivion (TES4). Layout/enums are from
`references/xEdit/Core/wbDefinitionsTES4.pas`; example values are from
`export/Oblivion.esm/`.

> **Oblivion CTDAs are 24 bytes.**
> An older 20-byte `CTDT` variant also exists in early TES4 files; xEdit reads it
> as "Condition (old format)" with the same fields minus the trailing unused
> dword. Modern Oblivion.esm uses `CTDA`.

---

## CTDA binary layout (24 bytes)

| Offset | Size | Field | Notes |
|-------:|-----:|-------|-------|
| 0  | 1 | **Type (operator + flags)** | See bit tables below. |
| 1  | 3 | Unused (padding) | Zero. |
| 4  | 4 | **Comparison value** | float by default. If "Use Global" flag set, this is a GLOB FormID instead. `1.0` = `00 00 80 3F`. |
| 8  | 2 | **Function index** | U16 LE. Selects which game function to call. |
| 10 | 2 | Unused | Zero. |
| 12 | 4 | **Param 1** | Type depends on the function (see parameter-type table). |
| 16 | 4 | **Param 2** | Type depends on the function. |
| 20 | 4 | Unused | Zero. |

### Type byte (offset 0)

The high nibble is the comparison operator; the low bits are flags.

**Comparison operator (`value & 0xF0`):**
| Bits | Operator |
|------|----------|
| `0x00` | Equal to (==) |
| `0x20` | Not equal to (!=) |
| `0x40` | Greater than (>) |
| `0x60` | Greater than or equal to (>=) |
| `0x80` | Less than (<) |
| `0xA0` | Less than or equal to (<=) |

**Flags (`value & 0x0F`):**
| Bit | Flag | Meaning |
|-----|------|---------|
| `0x01` | **Or** | OR this condition with the *next* one (instead of AND). |
| `0x02` | Run on target | Evaluate the function against the target, not the subject. |
| `0x04` | **Use global** | Comparison value is a GLOB FormID, not a literal float. |

Worked example (real Oblivion.esm condition blob):
`00 00 00 00 | 00 00 80 3F | 48 00 | 00 00 | 9B 55 01 00 | 00 00 00 00 | 00 00 00 00`
- byte0 `00` → operator `==`, no flags.
- comp `00 00 80 3F` → `1.0`.
- func `48 00` → `0x48` = **72 = GetIsID**.
- param1 `9B 55 01 00` → FormID `0x0001559B` (an NPC).
- → "the speaker IS NPC 0001559B".

---

## OR-chains

To express **"A OR B OR C"**, set the OR flag (`0x01`) on every CTDA in the chain
**except the last**; the last is plain AND. The chain evaluates as
`(A or B or C) AND <next conditions>`.

```
CTDA  A   type=0x01 (OR)
CTDA  B   type=0x01 (OR)
CTDA  C   type=0x00 (AND)   <- last in chain, no OR
CTDA  D   type=0x00 (AND)   <- separate condition
→ (A or B or C) and D
```

A trailing OR flag on the last member of a chain leaks into the following
(unrelated) condition. Keep an OR-chain contiguous and drop OR on its last
member.

---

## Parameter-type table (param1 / param2)

The meaning of Param 1 and Param 2 depends on the function. xEdit resolves each
to one of these parameter types (the index here is xEdit's internal decider
index, not a stored value — the function determines which applies):

| # | Parameter type | Holds |
|---|----------------|-------|
| 00 | Unknown | raw 4 bytes |
| 01 | None | (unused) |
| 02 | Integer | S32 literal |
| 03 | Variable Name | script/quest variable index |
| 04 | Sex | sex enum |
| 05 | Actor Value | →ACVA (actor-value) |
| 06 | Crime Type | crime-type enum |
| 07 | Axis | axis enum |
| 08 | Form Type | form-type enum |
| 09 | Quest Stage | stage number (param2 of GetStage etc.) |
| 10 | Object Reference | →PLYR/REFR/ACHR/ACRE/TRGT |
| 12 | Inventory Object | →ARMO/AMMO/MISC/WEAP/INGR/SLGM/SGST/BOOK/KEYM/CLOT/ALCH/APPA/LIGH |
| 13 | Actor | →PLYR/ACHR/ACRE/TRGT |
| 14 | Quest | →QUST |
| 15 | Faction | →FACT |
| 16 | Cell | →CELL |
| 17 | Class | →CLAS |
| 18 | Race | →RACE |
| 19 | Actor Base | →NPC_/CREA/ACTI |
| 20 | Global | →GLOB |
| 21 | Weather | →WTHR |
| 22 | Package | →PACK |
| 23 | Owner | →FACT/NPC_ |
| 24 | Birthsign | →BSGN |
| 25 | Furniture | →FURN |
| 26 | Magic Item | →SPEL |
| 27 | Magic Effect | →MGEF |
| 28 | Worldspace | →WRLD |
| 29 | Referenceable Object | →CREA/NPC_/TREE/SBSP/LVLC/SOUN/ACTI/DOOR/FLOR/STAT/FURN/CONT/ARMO/AMMO/MISC/WEAP/INGR/SLGM/SGST/BOOK/KEYM/CLOT/ALCH/APPA/LIGH/GRAS |

So e.g. `GetIsID` (72) takes param1 = Referenceable Object/Actor Base; `GetStage`
(58) takes param1 = Quest and the stage in the comparison value; `GetItemCount`
(69) takes param1 = Inventory Object.

---

## Function indices that matter for dialogue

(Decimal / hex. These are the ones that recur in Oblivion.esm dialogue/package
conditions.)

| Func | Name | Typical use |
|------|------|-------------|
| 72 / 0x48 | `GetIsID` | Restrict a line to a specific NPC / base object. |
| 58 / 0x3A | `GetStage` | Gate by quest stage (comparison value = stage; param1 = quest). |
| 59 / 0x3B | `GetStageDone` | Whether a specific stage has run (param2 = stage). |
| 56 / 0x38 | `GetQuestRunning` | Gate by whether a quest is active. |
| 53 / 0x35 | `GetScriptVariable` | Read a script variable. |
| 79 / 0x4F | `GetQuestVariable` | Read a quest-script variable. |
| 69 / 0x45 | `GetItemCount` | Inventory check. |
| 76 / 0x4C | `GetDisposition` | NPC disposition toward the player (drives many greetings). |
| 67 / 0x43 | `GetInCell` | Location gating. |

> The condition system is shared across record types — INFO, QUST (record-level,
> stage log entries, and per-target), IDLE, and PACK all use the same 24-byte
> CTDA.

---

## How a line is bound to one NPC (worked example)

Oblivion has no voice-type record, so to make a line belong to one specific NPC
you add a `GetIsID(npc)==1.0` condition:

```
CTDA  type=0x00 (== , AND)  comp=1.0  func=72 (GetIsID)  param1=<NPC FormID>
```

The voice file the engine plays is then resolved from that NPC's **race +
gender** (and the dialog/info FormIDs), not from any condition:
`Sound\Voice\<plugin>\<Race>\<Gender>\<dialogFID>_<infoFID>.mp3`.

To make a line belong to several NPCs, OR several `GetIsID` conditions (OR on all
but the last).

---

## Inspecting conditions in the dump

In the `export/Oblivion.esm/` dump, conditions are emitted as a single raw hex
blob per condition — `Condition[i].Raw=<48 hex chars = 24 bytes>` (and
`Target[i].Condition[j].Raw` on QUST targets). They are **not** broken into
fields, so decode with the offset table above: operator/flags at byte 0,
comparison value at [4:8], function index at [8:10], param1 at [12:16],
param2 at [16:20].
