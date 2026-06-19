# Dialogue Requirements Checklist & Failure Modes

Everything that must be true for a Skyrim dialogue line to fire in the engine,
followed by common ways it breaks and how to fix each. This is a pure
vanilla-Skyrim reference — the practical companion to `records.md` (layouts) and
`conditions.md` (CTDA).

---

## Master checklist — a line fires only if ALL hold

### Quest layer
- [ ] **QUST exists and is eligible.** Dialogue quests commonly use
      `DNAM Flags = 0x0011` (Start Game Enabled `0x0001` + Starts Enabled
      `0x0010`), or the quest is started by other means and is running when the
      line is needed.
- [ ] **`DNAM` is the correct 12 bytes:** Flags(U16) + Priority(U8) +
      FormVer(U8) + Unknown(4B) + Type(U32).
- [ ] **`ANAM` (Next Alias ID) present** even if there are no aliases.
- [ ] **A SEQ file exists** for every Start-Game-Enabled quest (game ≥ v1.7) or
      its dialogue/scenes never initialize.

### Branch layer
- [ ] **DLBR present for conversation topics**, with `QNAM` → owning quest and
      `SNAM` → starting DIAL.
- [ ] **Branch type correct:** Top-Level (`DNAM 0x01`) for menu-visible topics;
      Normal (`DNAM 0x00`) for chain/linked topics reached only via TCLT.

### Topic layer
- [ ] **DIAL has `QNAM`** → a running quest. No quest owner = ignored.
- [ ] **Conversation topics have `BNAM`** → their DLBR; bark topics
      (combat/detection/misc subtypes) have no branch.
- [ ] **`DATA` Category + Subtype** match the topic's purpose; **`SNAM`** holds
      the right 4-char subtype code (e.g. `CUST`, `HELO`, `GBYE`).
- [ ] **`TIFC`** equals the actual number of child INFOs in the topic's GRUP.

### Info layer
- [ ] **At least one Response** with a `TRDT` (24B) and a valid `NAM1`
      (response text).
- [ ] **`GetIsVoiceType` condition present** (func 426) for the voice type(s)
      that should say it — so the right recording plays and audio isn't exported
      for every voice. Multiple voice types = OR-chain (OR on all but last).
- [ ] **Conversation topics have an identity gate** (e.g. `GetIsID(npc)==1.0`)
      so the line doesn't appear on every NPC.
- [ ] **All other CTDAs pass** and the OR-chains are well-formed (no trailing OR
      flag leaking into the next condition).

### Voice / asset layer
- [ ] **The NPC has `VTCK`** → a VTYP that matches the INFO's GetIsVoiceType.
- [ ] **Voice file exists** at
      `Sound\Voice\<plugin>\<VoiceType>\<infoFID>_<respNum>.fuz` (or `.xwm` +
      `.lip`). Missing audio = silent or skipped line.
- [ ] **`No LIP File (0x0800)`** set on responses that have no lip-sync data, or
      provide a silence/placeholder.

---

## Common failure modes → fix

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| **Topic appears on EVERY NPC** | INFO has no identity condition (no GetIsID / GetIsVoiceType). | Add a `GetIsID(npc)==1.0` (or voice-type) condition. |
| **Line plays for the WRONG voice / no audio exported** | Missing `GetIsVoiceType`, or it points at a VTYP not in the file. | Add GetIsVoiceType(VTYP) and ensure the VTYP exists. |
| **A whole condition gate collapses to "always true"** | Trailing OR flag (`0x01`) on the last condition of an OR-chain leaks into the next condition. | OR on all-but-last; keep the OR-chain contiguous. |
| **All global-based conditions fail** | "Use Global" set on the wrong bit. | Use bit 2 (`0x04`), not bit 5 (`0x20`). |
| **A topic is silently ignored** | DIAL missing `QNAM` (no quest owner), or owning quest isn't running. | Assign a `QNAM` and make sure the quest is started/enabled. |
| **Dialogue/scenes don't initialize at all on a new game** | No SEQ file for the Start-Game-Enabled quest. | Generate the SEQ file. |
| **NPC can't speak any line** | Missing `VTCK` (no voice type). | Give the NPC a voice type. |
| **City-specific greeting fires everywhere** | `GetInCell` (location) condition missing or wrong. | Add/repair the `GetInCell (71)` condition. |
| **Quest line never offered** | A required `GetStage`/`GetQuestRunning` condition never becomes true. | Verify the gating stage/quest state matches the line's intended window. |

---

## Cross-reference (who must point at whom)

```
DLVW.QNAM ─► QUST            DLBR.QNAM ─► QUST
DLVW.BNAM ─► DLBR            DLBR.SNAM ─► DIAL (starting topic)
DLVW.TNAM ─► DIAL
                             DIAL.QNAM ─► QUST   DIAL.BNAM ─► DLBR
                             INFO.TPIC ─► DIAL   INFO.TCLT ─► DIAL/INFO
                             INFO.ANAM ─► NPC_   INFO response SNAM/LNAM ─► IDLE
                             INFO.CTDA GetIsVoiceType ─► VTYP
NPC_.VTCK ─► VTYP            QUST alias VTCK ─► VTYP/FLST
```

---

## Inspecting real Skyrim.esm for reference

```
# Dump dialogue records from real Skyrim.esm to compare structure:
python tools/tes5_esm_reader.py references/Skyrim.esm \
    --types DIAL INFO QUST DLBR DLVW VTYP --outdir temp/skyrim_dialog_dump
```
