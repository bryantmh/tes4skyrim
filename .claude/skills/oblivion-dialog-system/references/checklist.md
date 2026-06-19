# Oblivion Dialogue Requirements Checklist & Failure Modes

Everything that must be true for an Oblivion (TES4) dialogue line to fire in the
engine, followed by common ways it breaks and how to fix each. This is a pure
vanilla-Oblivion reference — the practical companion to `records.md` (layouts)
and `conditions.md` (CTDA).

---

## Master checklist — a line fires only if ALL hold

### Topic availability
- [ ] **The topic is reachable by the NPC.** Either it's a reserved
      always-available topic (`GREETING`, `GOODBYE`, `HELLO`, service topics,
      combat/detection/persuasion topics for the relevant situation), OR it has
      been added via:
      - a script `AddTopic <topic>` command (quest script, result script, etc.), or
      - an INFO's Add-Topics list (the `NAME` subrecord), or
      - a `Choice` (TCLT) / `Link From` (TCLF) wiring from a topic already shown.
- [ ] **DIAL.DATA Type matches the situation** (Topic for normal conversation,
      Combat/Detection/Persuasion/Service for those contexts).

### Quest layer
- [ ] **The owning quest is enabled / running** if the dialogue is quest-gated.
      `DATA.Flags 0x01` (Start game enabled) makes the quest available from a new
      game; otherwise something must start it.
- [ ] **Topic↔quest association (QSTI) is set** when the topic belongs to a
      quest — this implicitly gates the topic by that quest.
- [ ] **Quest priority (DATA.Priority)** is high enough that this quest's
      dialogue wins over competing quests for the same topic, if needed.

### Info layer
- [ ] **The INFO's conditions all pass.** For an NPC-specific line, a
      `GetIsID(npc)` condition; for quest progress, `GetStage` /
      `GetStageDone` / `GetQuestRunning`; etc.
- [ ] **OR-chains are well-formed** — OR flag on all but the last member; the
      chain is contiguous so it doesn't leak into the next condition.
- [ ] **At least one Response** with a `TRDT` and a `NAM1` (response text).
- [ ] **INFO ordering (PNAM / INOM / INOA)** is consistent if the topic relies on
      a specific info order.

### Result / flow
- [ ] **Result scripts are valid** (SCHR/SCDA/SCTX/SCRO) — e.g. an `AddTopic` or
      `SetStage` in a result script actually advances the conversation/quest.
- [ ] **Flags are right** — `Goodbye (0x01)` to end the conversation,
      `Say Once (0x04)` for one-shot lines, `Random (0x02)` for variation.

### Voice / asset layer
- [ ] **Voice audio exists** at
      `Sound\Voice\<plugin>\<Race>\<Gender>\<dialogFID>_<infoFID>.mp3` (plus the
      `.lip` lip-sync file). Missing audio → subtitle-only / silent line.
- [ ] **The NPC's race + gender resolve to a recorded voice** — Oblivion picks
      the file by race+gender, so an NPC of a race with no recording for that
      line gets nothing.

### AI presence (PACK)
- [ ] **The NPC is where/when the player can talk to them** — an AI package
      (PACK) places the NPC; a `force-greet` package can push dialogue on the
      player. Service packages (`Offers services` flag) surface service topics.

---

## Common failure modes → fix

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| **Topic never appears for an NPC** | It was never added (no AddTopic, not in any NAME/Choice, not a reserved topic). | Add it via a script `AddTopic`, an INFO's Add-Topics (`NAME`), or wire it from a shown topic via TCLT. |
| **Line shows on the WRONG NPC** | INFO lacks a `GetIsID` (or other identity) condition. | Add `GetIsID(npc)==1.0`. |
| **Quest dialogue appears too early/late** | Wrong `GetStage` / `GetStageDone` comparison, or quest not running. | Verify the stage gate and that the quest is started/enabled. |
| **A whole condition gate becomes "always true"** | Trailing OR flag on the last member of an OR-chain leaks into the next condition. | OR on all-but-last; keep the chain contiguous. |
| **Global-based condition behaves oddly** | "Use Global" flag not set (comparison value read as a float instead of a GLOB FormID), or vice versa. | Set/clear bit `0x04` to match whether the comparison value is a global. |
| **Conversation doesn't advance / loops** | Missing result-script `SetStage`/`AddTopic`, or no `Goodbye` flag to end it. | Add the appropriate result script and/or set the `Goodbye` flag. |
| **Line is silent / subtitle only** | No voice file for that race+gender at the expected path, or missing `.lip`. | Provide the `.mp3` (+`.lip`) under `Sound\Voice\<plugin>\<Race>\<Gender>\`. |
| **Two quests fight over the same topic** | Equal priority; engine picks unexpectedly. | Raise the intended quest's `DATA.Priority`. |
| **Service options (training/barter/etc.) don't appear** | NPC's package doesn't offer services, or service topic conditions fail. | Set the package `Offers services` flag (PKDT) and check the service topic conditions. |
| **NPC never present to talk to** | AI package schedule/location keeps them elsewhere. | Adjust the PACK PLDT location / PSDT schedule. |

---

## Cross-reference (who must point at whom)

```
Topic shown ─requires─► AddTopic (script / INFO NAME / TCLT wiring) OR reserved topic
DIAL.QSTI ─► QUST          (topic ↔ quest association; implicit gating)
INFO.QSTI ─► QUST          INFO.TPIC ─► DIAL
INFO.NAME ─► DIAL          (add topics)        INFO.TCLT ─► DIAL (choices)
INFO.PNAM/TCLF ─► INFO/DIAL (ordering / link from)
INFO result script ─► SetStage / AddTopic / etc.
QUST.SCRI ─► SCPT          QUST.Target QSTA ─► REFR/ACRE/ACHR
PACK ─► places the NPC and can force-greet / offer services
Voice file ◄─ NPC race + gender (no record link)
```

---

## Inspecting real Oblivion.esm for reference

The dump in `export/Oblivion.esm/` is the actual master-file content, one
KEY=VALUE text file per record type. Relevant files:

```
export/Oblivion.esm/DIAL.txt    # topics
export/Oblivion.esm/INFO.txt    # responses (conditions, choices, result scripts)
export/Oblivion.esm/QUST.txt    # quests (stages, targets, scripts)
export/Oblivion.esm/IDLE.txt    # idle animations
export/Oblivion.esm/PACK.txt    # AI packages
```

Conditions appear as `Condition[i].Raw=<24-byte hex>`; decode with the offset
table in `conditions.md`.
