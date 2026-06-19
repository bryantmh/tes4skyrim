# Skyrim Conditions (CTDA) — the dialogue routing engine

Conditions are how Skyrim decides who can say what. Each `CTDA` subrecord is a
single check that evaluates to true/false. An INFO (or quest, objective, alias,
scene phase…) is eligible only when **all** its conditions pass — except where
conditions are joined by OR. This file documents the exact byte layout, the
OR-chain mechanics, and the function indices that matter for dialogue.

This is a pure description of vanilla Skyrim. Layout/enums are from
`references/xEdit/Core/wbDefinitionsTES5.pas`; example values are from
`references/Skyrim.esm`.

---

## CTDA binary layout (32 bytes)

| Offset | Size | Field | Notes |
|-------:|-----:|-------|-------|
| 0  | 1 | **Operator + Flags** | See bit table below. |
| 1  | 3 | Unused (padding) | Zero. |
| 4  | 4 | **Comparison value** | float by default. If "Use Global" flag set, this is a GLOB FormID instead. `1.0` = `00 00 80 3F`. |
| 8  | 2 | **Function index** | U16 LE. Selects which game function to call. |
| 10 | 2 | Padding | Zero. |
| 12 | 4 | **Param 1** | Usually a FormID (NPC, quest, cell, voice type…). |
| 16 | 4 | **Param 2** | Second parameter; often 0. |
| 20 | 4 | Run On | 0 Subject, 1 Target, 2 Reference, 3 Combat Target, 4 Linked Ref, 5 Quest Alias, 6 Package Data, 7 Event Data. |
| 24 | 4 | Reference | FormID used when Run On = Reference (else 0). |
| 28 | 4 | Unknown / Param3 | `FF FF FF FF` in vanilla = none. |

### Operator + Flags byte (offset 0)

The high bits are the comparison operator; the low bits are flags.

**Operator (top 3 bits, `value & 0xE0`):**
| Bits | Operator |
|------|----------|
| `0x00` | Equal to (==) |
| `0x20` | Not equal (!=) |
| `0x40` | Greater than (>) |
| `0x60` | Greater than or equal (>=) |
| `0x80` | Less than (<) |
| `0xA0` | Less than or equal (<=) |

**Flags (low 5 bits):**
| Bit | Flag | Meaning |
|-----|------|---------|
| `0x01` | **OR** | OR this condition with the *next* one (instead of AND). |
| `0x02` | Parameters use aliases | |
| `0x04` | **Use Global** | Comparison value is a GLOB FormID, not a literal. (bit 2 — not bit 5.) |
| `0x08` | Use Pack Data | |
| `0x10` | Swap Subject and Target | |

Examples seen in real Skyrim.esm data:
- `GetIsVoiceType(VTYP) == 1.0` → operator `0x00`, func `426` (`AA 01`),
  comp `00 00 80 3F`, param1 = VTYP FormID.
- `GetStage(quest) >= 70.0` → operator `0x60`, func `58` (`3A 00`).
- `GetIsID(npc) == 1.0` → operator `0x00`, func `72` (`48 00`).

---

## OR-chains (the rule that breaks dialogue when wrong)

To express **"voice type A OR B OR C"**, you set the OR flag (`0x01`) on every
CTDA in the chain **except the last**. The last one is plain AND. The chain then
evaluates as `(A or B or C) AND <next conditions>`.

```
CTDA GetIsVoiceType(A) op=0x01 (==1.0, OR)
CTDA GetIsVoiceType(B) op=0x01 (==1.0, OR)
CTDA GetIsVoiceType(C) op=0x00 (==1.0, AND)   <- last, no OR
CTDA <other condition>                         <- AND'd with the whole chain
```

**Failure mode:** if the last condition in an OR-chain keeps its OR flag, it
leaks into the following unrelated condition and the whole gate collapses
(everything becomes "or true"). Keep OR on all-but-last, and keep an OR-chain
contiguous so it isn't split by an unrelated condition.

---

## Function indices that matter for dialogue

| Func | Name | Param1 | Used for |
|------|------|--------|----------|
| **426** | `GetIsVoiceType` | VTYP FormID | Route a line to a voice type. On nearly every INFO. |
| **72** | `GetIsID` | NPC_ FormID | Restrict a conversation line to a specific NPC. |
| **71** | `GetInCell` | CELL FormID | Location gating (city greetings, gossip). |
| **58** | `GetStage` | QUST FormID | Gate by quest stage (comparison value = stage). |
| **56** | `GetQuestRunning` | QUST FormID | Gate by whether a quest is active. |
| **67** | `GetCurrentAIProcedure` | — | AI-state gating on barks. |
| **59** | `GetStageDone` | QUST FormID | Whether a specific stage has run. |
| **99** | `GetQuestCompleted` | QUST FormID | Whether a quest is complete. |
| **263** | `IsWeaponOut` | — | Combat/bark gating. |

> A definitive `1.0` comparison with `==` (`op=0x00`, comp=`00 00 80 3F`) is the
> idiomatic "is X true" test used for GetIsVoiceType / GetIsID / GetInCell.

---

## How a line is routed to the right voice (worked example)

A speaking NPC has `VTCK` → a VTYP (say `MaleNord`, FormID `0x0001F2E6`). For the
engine to play `MaleNord`'s recording of an INFO, that INFO carries:

```
CTDA  op=0x00  func=426 (GetIsVoiceType)  comp=1.0  param1=0x0001F2E6
```

If the line should be said by several voice types, those become an OR-chain of
GetIsVoiceType CTDAs. If the line is for one specific NPC, a
`GetIsID(npc)==1.0` condition is added so the topic doesn't appear on every NPC
who happens to share that voice type.

---

## Verifying / inspecting conditions

Inspect real Skyrim.esm INFOs and their conditions for comparison:

```
python tools/tes5_esm_reader.py references/Skyrim.esm \
    --types INFO QUST --outdir temp/skyrim_dialog_dump
```

(The reader may emit CTDA/TRDT blocks as raw hex; decode using the offsets in
the table above — function index at bytes [8:10], comparison at [4:8],
param1 at [12:16].)
