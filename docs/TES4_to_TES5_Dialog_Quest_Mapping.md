# TES4 → TES5 Dialog & Quest Mapping Reference

## Overview

Oblivion's dialog system is **topic-based**: NPCs have topics listed in their dialog
menu, gated by conditions (faction, disposition, quest stage, NPC identity, etc.).
Skyrim restructured this into a **voice-type-routed, branch-based** system.

This document describes how every TES4 dialog/quest record maps to TES5, what new
records must be generated, and how the Skyrim engine evaluates conditions at runtime.

---

## Record Type Mapping

| TES4 Record | TES5 Record(s) | Purpose |
|-------------|-----------------|---------|
| QUST | QUST | Quest container (stages, objectives, aliases) |
| DIAL | DIAL | Dialog topic (points to owning quest + branch) |
| INFO | INFO | Individual dialog line (conditions + responses) |
| *(none)* | DLBR | Dialog Branch — links a conversation DIAL to its quest |
| *(none)* | DLVW | Dialog View — CK UI metadata (optional at runtime) |
| SCPT (result scripts) | VMAD fragments | Quest stage / INFO result scripts → Papyrus |

---

## TES4 Dialog Architecture

### Record Hierarchy
```
QUST (quest)
 └─ DIAL (topic)  — linked via QSTI subrecord (quest → topic association)
     └─ INFO (response) — child records in topic group
         ├─ Conditions (CTDA) — gates when this line appears
         ├─ Responses (TRDT + NAM1) — what NPC says
         ├─ Choices (TCLT) — links to other DIALs shown after this line
         └─ Result Script (SCTX) — script that runs when line is spoken
```

### TES4 DATA.Type (DIAL types)
| Value | Name | Description |
|-------|------|-------------|
| 0 | Topic | Standard conversation topic (player-initiated) |
| 1 | Conversation | System/ambient/rumor topics |
| 2 | Combat | Combat barks (Attack, Hit, Flee) |
| 3 | Persuasion | Persuasion minigame (Admire, Boast, Joke, Coerce) |
| 4 | Detection | Detection barks (Noticed, Unseen, Lost) |
| 5 | Service | Service UI (Barter, Repair, Training) |
| 6 | Misc | Miscellaneous ambient barks |

### TES4 Condition Gating
In Oblivion, dialog lines appear when ALL conditions evaluate to true:
- **GetIsID(npc)** — Only this specific NPC speaks this line
- **GetStage(quest) >= N** — Only after quest reaches stage N
- **GetFactionRank(faction) >= N** — Only for faction members
- **GetDisposition >= N** — Only when NPC likes player enough (no TES5 equivalent)
- **GetQuestRunning(quest)** — Only while quest is active
- **GetIsRace(race)** — Only for NPCs of this race

### Data Scale (Oblivion.esm)
- 3,817 DIAL topics (3,184 Topic, 555 Conversation, 16 Combat, 39 Persuasion, 4 Detection, 14 Service, 5 Misc)
- 19,278 INFO records (18,920 with conditions, 15,736 with GetIsID)
- 390 QUST records (265 with stages)
- 3,542 generic INFOs (no GetIsID — faction/race/quest gated only)

---

## TES5 Dialog Architecture

### Record Hierarchy
```
QUST (quest) — owns dialogue topics
 ├─ DLBR (branch) — links quest ↔ topic, determines UI behavior
 │   └─ SNAM → DIAL (starting topic)
 ├─ DLVW (view) — CK metadata, references branches + topics
 └─ DIAL (topic) — QNAM → quest, BNAM → branch
     └─ INFO (response) — conditions include GetIsVoiceType for routing
         ├─ GetIsVoiceType CTDAs — which voice types speak this line
         ├─ TES4 conditions (converted) — quest stage, faction, etc.
         ├─ Responses (TRDT/NAM1/NAM2/NAM3)
         ├─ Choices (TCLT) — links to other topics
         └─ VMAD — Papyrus fragment (replaces result script)
```

### Bark vs Conversation Topics

**Barks** fire automatically based on game events. They do NOT appear in the
player's dialog menu. They need:
- Category 3 (Combat), 5 (Detection), or 7 (Misc)
- NO DLBR — bark topics have no branch linkage
- NO BNAM on the DIAL record
- Conditions filter by voice type only (quest conditions stripped)

**Conversation topics** appear in the player's dialog menu when talking to an NPC.
They need:
- Category 0 (Topic)
- A DLBR record linking the topic to its quest
- BNAM on the DIAL pointing to the DLBR
- Full conditions preserved (quest stage, faction, voice type, etc.)

### Voice Type Routing (Critical)

TES5 uses **voice types** instead of NPC identity for dialog routing:
- Each NPC_ has a VTCK subrecord pointing to a VTYP record
- Each INFO has GetIsVoiceType conditions that specify which voice types speak it
- The engine first filters by voice type, then evaluates remaining conditions

**NPC-specific lines** (TES4 GetIsID):
1. TES4 INFO has `GetIsID(npc_formid)` condition
2. We look up NPC's race → voice type mapping
3. Add `GetIsVoiceType(vtyp_formid)` as first condition
4. Keep the original `GetIsID` condition (converted to TES5 format)

**Generic lines** (no GetIsID):
1. TES4 INFO has no NPC identity check
2. Lines gated by faction/race/quest conditions only
3. Currently: NO voice type filter injected (engine handles via quest routing)
4. Risk: Without voice type filter, any NPC with matching conditions could speak

### CTDA Format Change
| Field | TES4 (24 bytes) | TES5 (32 bytes) |
|-------|-----------------|-----------------|
| Type byte | `[0]` | `[0]` (same) |
| Padding | `[1-3]` | `[1-3]` (same) |
| CompValue | `[4-7]` float or FormID | `[4-7]` (same) |
| FuncIndex | `[8-9]` | `[8-9]` (same) |
| Padding | `[10-11]` | `[10-11]` (same) |
| Param1 | `[12-15]` | `[12-15]` (same) |
| Param2 | `[16-19]` | `[16-19]` (same) |
| RunOn | `[20-23]` | `[20-23]` (same) |
| Reference | *(none)* | `[24-27]` = 0 |
| Unknown | *(none)* | `[28-31]` = 0xFFFFFFFF |

**UseGlobal flag**: bit 2 (0x04) in the type byte. CompValue is a Global FormID.
**OR flag**: bit 0 (0x01) in the type byte. Chains with next condition as OR.

---

## Conversion Rules

### QUST Conversion

```
TES4 QUST → TES5 QUST
  EDID         → EDID (same)
  FULL         → FULL (same)
  DATA.Flags   → DNAM.Flags (remapped, 12 bytes)
  DATA.Priority→ DNAM.Priority (capped at 50 for dialogue quests)
  Stages       → INDX(4B) + QSDT + CNAM per log entry
  ResultScript → VMAD quest fragment (TES4_QF_<edid>)
```

**DNAM (12 bytes)**: `Flags(U16) + Priority(U8) + FormVersion(U8=0) + Unknown(4B) + Type(U32)`

**Critical rules:**
- FormVersion in DNAM MUST be 0 (not 44) — all vanilla Skyrim QUSTs use 0
- NEVER set HasDialogueData (0x8000) — causes engine to mishandle dialogue
- Dialogue quests get `StartGameEnabled(0x0001) | StartsEnabled(0x0010) = 0x0011`
- NEXT empty marker subrecord is required
- ANAM (next alias ID, U32) is required

**TES4 INDX**: 2 bytes (U16 stage index)
**TES5 INDX**: 4 bytes (U16 stage index + U8 flags + U8 unknown)

### DIAL Conversion

```
TES4 DIAL → TES5 DIAL + DLBR (for conversation topics)
  EDID        → EDID
  FULL        → FULL
  DATA.Type   → DATA(4B) = TopicFlags + Category + Subtype
  QSTI[0]     → QNAM (quest ownership — required!)
  (generated) → PNAM (priority, float 50.0)
  (generated) → BNAM (branch link, conversation only)
  (generated) → SNAM (4-char subtype code)
  (generated) → TIFC (child INFO count)
```

**EditorID → Subtype/Category Mapping:**
| EditorID | Subtype | SNAM | Category |
|----------|---------|------|----------|
| GREETING/HELLO | 79 | HELO | 7 (Misc) |
| GOODBYE | 78 | GBYE | 7 |
| Attack | 26 | ATCK | 3 (Combat) |
| Hit | 29 | HIT_ | 3 |
| Flee | 30 | FLEE | 3 |
| IdleChatter/Idle | 94 | IDLE | 7 |
| Noticed/Seen | 57 | NOTA | 5 (Detection) |
| Unseen/Lost | 63 | LOTN | 5 |
| *(conversation)* | 0 | CUST | 0 (Topic) |

**Orphan DIALs** (no quest association): Assigned to a catch-all quest
`TES4DialogueGeneric` with StartGameEnabled + StartsEnabled.

### INFO Conversion

```
TES4 INFO → TES5 INFO
  EDID            → EDID
  DATA.Flags      → ENAM.Flags (masked to 0x37) + ENAM.ResetHours
  (generated)     → CNAM (Favor Level = 0)
  TCLT choices    → TCLT (FormID links to other DIALs)
  TRDT + NAM1     → TRDT(24B) + NAM1 + NAM2 + NAM3
  Conditions      → GetIsVoiceType CTDAs + TES4 CTDAs (converted)
  ResultScript    → VMAD (TopicInfo fragment)
```

**TES5 TRDT (24 bytes)**:
`EmotionType(U32) + EmotionValue(U32) + Unused(4B) + ResponseNumber(U8) + Unused(3B) + Sound(FormID=0) + Flags(U8=1) + Unused(3B)`

**NAM2 and NAM3 MUST always be present** (even empty) — engine parses
responses as TRDT/NAM1/NAM2/NAM3 groups.

**Condition injection order:**
1. GetIsVoiceType CTDAs (OR chain) — voice type routing
2. TES4-converted CTDAs — quest/faction/race conditions

### DLBR Generation (new record)

One DLBR per non-bark DIAL topic:
```
EDID = "TES4_<dial_edid>_Branch"
QNAM = quest FormID
TNAM = 0 (Player dialogue)
DNAM = 1 (Top-Level branch)
SNAM = DIAL FormID (starting topic)
```

### DLVW Generation (new record)

One DLVW per quest that has dialogue branches:
```
EDID = "TES4_DLVW_<quest_formid>"
QNAM = quest FormID  
BNAM[] = all DLBR FormIDs for this quest
TNAM[] = all DIAL FormIDs for this quest
ENAM = 0 (Dialogue Branches view type)
DNAM = 0 (Don't show all text)
```

### VTYP Generation (new records)

27 custom voice type records created (one per race×gender combination):
```
TES4MaleImperial, TES4FemaleImperial, TES4MaleNord, ...
```
Each NPC_ gets VTCK pointing to the appropriate VTYP based on race + gender.

---

## Skipped/Dropped Records

### DIAL Topics Skipped Entirely
| Condition | Reason |
|-----------|--------|
| DATA.Type = 3 (Persuasion) | Oblivion persuasion minigame — no TES5 equivalent |
| DATA.Type = 5 (Service) | Barter/repair/training UI — handled by engine |
| EditorID = CreatureResponses | TES4 creature ambient sounds — no creatures in TES5 |
| EditorID = SECreatureResponses | Shivering Isles creature sounds |
| EditorID = TamrielGateResponses | Oblivion Gate commentary — no gates in TES5 |
| EditorID = ANY | Empty catch-all container |
| EditorID starts with Test/MarkNTest | Debug/test dialogue |

**WARNING**: EditorID-based skipping was previously disabled because it caused
infinite loading screens. See "Known Issues" section.

### TES4-Only Condition Functions Dropped
| Func | Name | Reason |
|------|------|--------|
| 76 | GetDisposition | No disposition system in TES5 |
| 40 | GetVampire | Replaced by keyword checks |
| 104 | IsYielding | Not in TES5 |
| 171 | IsPlayerInJail | Not in TES5 |
| 251 | GetPCInfamy | Replaced by crime system |

### Conditions Stripped from Bark INFOs Only
| Func | Name | Reason |
|------|------|--------|
| 56 | GetQuestRunning | Quest scripts not running (no Papyrus) |
| 58 | GetStage | Quest stage never set at runtime |
| 59 | GetStageDone | Quest stage never marked done |
| 99 | GetIsPlayableRace | Birthsign check — not in TES5 |
| 79 | GetIsPlayerBirthsign | Not in TES5 |
| 71 | GetInCell | Cell FormIDs may not map correctly |
| 67 | GetCurrentAIProcedure | AI system completely different |

**IMPORTANT**: These are ONLY stripped for bark topics. Conversation topics
MUST keep quest conditions (GetStage, GetStageDone, etc.) for proper gating.

---

## How Skyrim Evaluates Dialog at Runtime

### Bark Evaluation (automatic)
1. Game event triggers bark check (NPC sees enemy, takes hit, etc.)
2. Engine iterates all bark-category DIALs
3. For each DIAL, checks TIFC > 0 and QNAM quest is running
4. For each child INFO, evaluates conditions:
   - GetIsVoiceType must match the speaking NPC's voice type
   - All other conditions must pass
5. First matching INFO fires (plays voice line)

### Conversation Evaluation (player-initiated)
1. Player activates NPC → engine opens dialogue menu
2. Engine finds all DIAL topics where:
   - QNAM quest is running (StartGameEnabled counts)
   - The DIAL's DLBR branch is Top-Level
   - Category = 0 (Topic)
3. For each topic, checks child INFOs for matching conditions:
   - GetIsVoiceType matches the NPC's voice type
   - All quest/faction/race conditions pass
4. Topics with at least one valid INFO appear in the dialogue menu
5. Player selects a topic → best matching INFO plays
6. TCLT links determine which topics appear next

### Why Dialog Routing Can Go Wrong
1. **Missing GetIsVoiceType** → Line appears for wrong NPCs
2. **Quest conditions stripped** → Line appears before quest reaches required stage
3. **All quests StartGameEnabled** → Quest-gated topics appear too early
4. **Bark misclassification** → Conversation topic has conditions stripped
5. **Missing DLBR** → Conversation topic never appears in menu
6. **Missing QNAM** → Topic invisible to engine entirely

---

## Known Issues & Mitigations

### Issue 1: should_skip_dial EditorID Check Causes Loading Screens
**Status**: EditorID-based skipping commented out in code
**Cause**: Likely dangling TCLT references — when a DIAL is skipped, INFOs in
other DIALs may have TCLT (choice links) pointing to the skipped DIAL's FormID.
The engine tries to load a non-existent topic → hangs.
**Fix**: When skipping a DIAL, also strip all TCLT references to that DIAL's
FormID from all other INFOs. Or: keep the DIAL as an empty stub (0 INFOs).

### Issue 2: Generic INFOs Get No Voice Type Filter
**Status**: By design — `build_voice_type_ctdas_for_info` returns empty bytes
for INFOs without GetIsID conditions.
**Impact**: 2,952 generic conversation INFOs could appear for any NPC that
passes their faction/race/quest conditions.
**Mitigation**: Skyrim's quest routing (QNAM) limits which topics actually
appear. Faction/race conditions still gate access.

### Issue 3: Quest Stage Gating Without Running Quests
**Status**: All dialogue quests get StartGameEnabled + StartsEnabled
**Impact**: Quest topics become available immediately, but stage conditions
(GetStage >= 10) will always fail unless Papyrus scripts set stages.
**Mitigation**: Papyrus quest fragments (VMAD) should set stages when
conditions are met. Lines without quest conditions fire freely.

### Issue 4: "External References (fill in CK)"
**Status**: Script converter generates properties without FormID bindings
**Impact**: Papyrus scripts reference objects (Gold001, player, NPCs) that
need to be resolved to actual FormIDs in the ESM's VMAD data.
**Fix needed**: Write property values directly into VMAD binary data during
import, creating missing records (globals, factions) as needed.

---

## Conversion Statistics (Oblivion.esm)

| Metric | Count |
|--------|-------|
| DIAL topics converted | ~3,817 |
| Bark topics | ~851 |
| Conversation topics | ~2,966 |
| INFO records | ~19,278 |
| INFOs with GetIsID | 15,736 (81.6%) |
| Generic INFOs | 3,542 (18.4%) |
| DLBR branches created | ~2,966 |
| DLVW views created | ~275 |
| Dialogue quests | ~345 |
| VTYP records created | 27 |
| Condition functions dropped (TES4-only) | 1,751 (GetDisposition) |
