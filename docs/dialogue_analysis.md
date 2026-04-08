# TES4→TES5 Dialogue System: Forensic Analysis & Fix Report

## Summary of Root Causes

NPCs were completely silent because **five compounding issues** prevented the Skyrim dialogue engine from processing any converted dialogue. Each issue alone could block dialogue; together they created a complete failure.

---

## Issue 1: QUST DNAM HasDialogueData Flag (CRITICAL)

### Problem
All 345 dialogue quests had `HasDialogueData` (0x8000) set in DNAM flags. **Zero** Skyrim.esm quests have this flag.

### Evidence
```
Our output:  DNAM Flags=0x8011 [StartGameEnabled, StartsEnabled, HasDialogueData]
Skyrim.esm:  DNAM Flags=0x0011 [StartGameEnabled, StartsEnabled]
```

Every vanilla Skyrim dialogue quest (DialogueGeneric, DialogueWhiterun, CreatureDialogueFox, etc.) uses `0x0011`. The `HasDialogueData` flag likely causes the engine to expect VMAD dialogue fragment structures that don't exist, silently preventing the quest from activating its dialogue.

### Fix
Removed `safe_flags |= 0x8000` from `convert_QUST`. Dialogue quests now use `0x0011`.

---

## Issue 2: QUST DNAM FormVersion = 44 (CRITICAL)

### Problem
All QUSTs had `FormVersion=44` in byte 3 of the DNAM struct. **All** Skyrim.esm QUSTs have `FormVersion=0`.

### Evidence
```
Our output:  DNAM bytes: ...00 2C 00 00 00 00... (FormVer=44=0x2C)
Skyrim.esm:  DNAM bytes: ...00 00 00 00 00 00... (FormVer=0)
```

The DNAM FormVersion byte controls how xEdit (and possibly the engine) interprets subsequent DNAM fields. With FormVer=44, the engine may enter a different DNAM parsing path that misaligns the remaining fields.

### Fix
Changed `struct.pack('<HBBII', safe_flags, priority, 44, 0, 0)` to `...priority, 0, 0, 0)`.

---

## Issue 3: 830 DIALs Without QNAM (CRITICAL)

### Problem
830 DIAL topics (of 3817 total) had no QNAM subrecord. All Skyrim DIALs require QNAM — the engine ignores topics without a quest owner.

### Cause
These TES4 DIALs had no quest association in the export data (`QuestCount=0`).

### Fix
Created a catch-all quest `TES4DialogueGeneric` (matching Skyrim's DialogueGeneric pattern: `StartGameEnabled + StartsEnabled, Priority=0, FormVer=0`). All orphan DIALs are assigned to this quest.

---

## Issue 4: Quest Conditions on Bark INFOs (MAJOR)

### Problem
3650 of 7583 bark INFOs (48.1%) had quest-dependent conditions:
- `GetQuestRunning` (56): Always false — TES4 quests have no Papyrus scripts
-  `GetStage` (58): Always returns 0 — stages never set
- `GetStageDone` (59): Always false
- `GetQuestCompleted` (99): Always false
- `GetInCell` (71): TES4 cell FormIDs may not resolve correctly
- `GetCurrentAIProcedure` (67): TES5 AI is completely different

In Oblivion, these conditions gated dialogue lines to specific quest phases. In Skyrim, with no running scripts, ALL these conditions fail, blocking most bark dialogue.

### Impact on GREETING
```
Before: 3743 GREETING INFOs, only 15 with voice-type-only conditions
After:  3743 GREETING INFOs, 164 with voice-type-only conditions
```

### Fix
Added `_QUEST_DEPENDENT_FUNCS` frozenset and `is_bark` parameter to `convert_INFO`. Bark INFOs (GREETING, Attack, Hit, Flee, Idle, combat/detection barks) strip quest conditions. Conversation INFOs keep all conditions for quest gating.

---

## Issue 5: 914 NPCs Without VTCK (MODERATE)

### Problem
`convert_CREA` (CREA→NPC_ conversion) did not add a VTCK (voice type) subrecord. 914 creature-converted NPCs had no voice type, making them invisible to the `GetIsVoiceType` condition routing system.

### Fix
Added VTCK assignment to `convert_CREA` using the same race→voice type lookup as `convert_NPC_`.

**After fix: 3396/3396 NPCs have VTCK.**

---

## Issue 6: CTDA Use Global Flag Bug (MINOR)

### Problem
`_convert_ctda_tes4_to_tes5` checked `type_byte & 0x20` for the Use Global flag. Bit 5 (0x20) is actually the NotEqual operator bit. The Use Global flag is bit 2 (0x04).

### Impact
For conditions using the NotEqual operator, the comparison value was incorrectly treated as a Global FormID and remapped. Limited practical impact because the `_remap` heuristic (top byte must be 0x00) rarely triggers on float comparison values, but still wrong.

### Fix
Changed `0x20` to `0x04`.

---

## TES4↔TES5 Dialogue Record Relationship

### Record Chain: How Dialogue Works

```
NPC_ ─(VTCK)─→ VTYP ←─(GetIsVoiceType)─── INFO ─(parent)─→ DIAL ─(QNAM)─→ QUST
                                                                   ─(BNAM)─→ DLBR ─(QNAM)─→ QUST
```

1. **VTYP** (Voice Type): Defines a "voice slot." Has DNAM flags: AllowDefaultDialogue(1), Female(2).
2. **NPC_** references VTYP via **VTCK** subrecord.
3. **QUST** must be active (StartGameEnabled + StartsEnabled) for its dialogue to work.
4. **DIAL** must have **QNAM** pointing to an active QUST.
5. **INFO** conditions include `GetIsVoiceType(VTYP_FID)` to route responses.
6. **DLBR** only needed for conversation topics (not barks).

### TES4 vs TES5 Comparison

| Aspect | TES4 (Oblivion) | TES5 (Skyrim) |
|--------|------------------|----------------|
| Voice routing | Implicit (race+gender) | Explicit (VTCK→VTYP + GetIsVoiceType conditions) |
| Topic ownership | Optional (some DIALs have no quest) | Required (QNAM mandatory) |
| Bark topics | Same group as conversation | Separate category (Misc/Combat/Detection) |
| Dialog branch | N/A | Required DLBR for conversation topics |
| Conditions | 24 bytes, TES4-specific functions | 32 bytes, different function set |
| Quest flags | Simple (StartGameEnabled) | Complex (no HasDialogueData in practice) |
| DNAM format | 2 bytes (Flags + Priority) | 12 bytes (Flags + Priority + FormVer + Unknown + Type) |

### CTDA Format (32 bytes in TES5)

| Offset | Size | Field | Notes |
|--------|------|-------|-------|
| 0 | 1 | Type | Bits 5-7: operator (Equal/NotEqual/GT/GTE/LT/LTE). Bit 0: OR. Bit 2: UseGlobal. |
| 1-3 | 3 | Padding | |
| 4-7 | 4 | CompValue | Float literal, or Global FormID if UseGlobal |
| 8-9 | 2 | FuncIndex | Function number (426=GetIsVoiceType, 72=GetIsID) |
| 10-11 | 2 | Padding | |
| 12-15 | 4 | Param1 | Function parameter 1 (often FormID) |
| 16-19 | 4 | Param2 | Function parameter 2 |
| 20-23 | 4 | RunOn | 0=Subject, 1=Target, 2=Reference, etc. |
| 24-27 | 4 | Reference | FormID when RunOn=Reference |
| 28-31 | 4 | Parameter3 | Vanilla always 0xFFFFFFFF |

### QUST DNAM Format (12 bytes)

| Offset | Size | Field | Our Value | Skyrim Value |
|--------|------|-------|-----------|--------------|
| 0-1 | 2 | Flags | 0x0011 | 0x0011 |
| 2 | 1 | Priority | 0-50 | 0-80 |
| 3 | 1 | FormVersion | 0 | 0 |
| 4-7 | 4 | Unknown | 0 | 0 |
| 8-11 | 4 | Type | 0 (None) | 0 (None) |

---

## Verification Results

### Before Fixes
| Metric | Value |
|--------|-------|
| DIALs without QNAM | 830 |
| QUSTs with HasDialogueData | 345 |
| NPCs without VTCK | 914 |
| Bark INFOs with quest conditions | 3650 (48.1%) |
| Generic GREETING INFOs (VT only) | 15 |
| QUST DNAM FormVer | 44 |

### After Fixes
| Metric | Value |
|--------|-------|
| DIALs without QNAM | **0** |
| QUSTs with HasDialogueData | **0** |
| NPCs without VTCK | **0** |
| Bark INFOs with quest conditions | **0** |
| Generic GREETING INFOs (VT only) | **164** |
| QUST DNAM FormVer | **0** |

### Test Coverage
76 tests pass, including:
- 33 new dialogue-specific tests
- 3 Skyrim reverse-engineering tests (verify against actual Skyrim.esm records)
- CTDA format, voice type routing, QUST DNAM, DIAL categories, INFO conditions, bark stripping, DLBR/DLVW structure, CREA VTCK
