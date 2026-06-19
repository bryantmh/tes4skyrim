# QUST Conversion (TES4 → TES5)

Quests are medium-difficulty: stages and journal text map cleanly, but the two
things that make a quest *function* — its attached script and its stage result
scripts — are Papyrus in Skyrim and have no data-only conversion. See the
`oblivion-dialog-system` / `skyrim-dialog-system` skills for full field layouts.

---

## Field map

| Oblivion QUST | Skyrim QUST | Transform |
|---------------|-------------|-----------|
| `EDID` | `EDID` | Copy. |
| `FULL` | `FULL` | Copy (journal name). |
| `ICON` | — | Skyrim quests use type/loc, not an icon path. Drop. |
| `SCRI` (→SCPT) | `VMAD` (quest Papyrus script) | **Re-author as Papyrus.** No data-only conversion; see "Scripts". |
| `DATA` (2B: Flags U8 + Priority U8) | `DNAM` (12B) | Expand — see DNAM below. |
| `CTDA[]` (quest conditions) | quest `CTDA[]` | Translate via `conditions.md`. |
| Stages[INDX S16 + log entries] | Stages[INDX 4B + log entries] | See "Stages". |
| Targets[QSTA + CTDAs] | Targets[QSTA + CTDAs] | See "Targets". |
| — | `ANAM` (Next Alias ID) | Required; set even with no aliases (e.g. 0, or N if you create aliases). |
| — | Aliases (ALST/ALLS …) | Oblivion has no alias system. Optional to synthesize; needed only if Papyrus/objectives reference aliases. See "Aliases". |
| — | Objectives (QOBJ/QSTA) | Oblivion journal text ≈ Skyrim objectives, but they're separate systems. See "Objectives". |

---

## DATA (2 bytes) → DNAM (12 bytes)

| TES4 DATA | TES5 DNAM field | Transform |
|-----------|-----------------|-----------|
| Flags U8: 0x01 Start game enabled | DNAM Flags U16: 0x0001 Start Game Enabled | Carry the bit. Add 0x0010 (Starts Enabled) for dialogue/SGE quests so they run from a new game, matching Oblivion's "enabled" behavior. |
| Flags U8: 0x04 Allow repeated conversation topics | DNAM Flags U16: 0x1000 Repeats Conditions (closest) | **Judgment** — not an exact match; Skyrim's repeat model differs. Map to the nearest repeat flag or omit. |
| Flags U8: 0x08 Allow repeated stages | DNAM Flags U16: 0x0008 Allow repeated stages | Carry the bit (same name). |
| Priority U8 | DNAM Priority U8 | Copy. Controls dialogue precedence in both games. |
| — | DNAM Form Version U8 | Per-struct version byte; observed 0 in Skyrim.esm dialogue quests. |
| — | DNAM Unknown (4B) | Zero. |
| — | DNAM Type (U32) | Oblivion has no quest type; set 0 (None) unless you classify it (Misc/MainQuest/etc.) for journal grouping. |

> Do not infer engine-lore flags that aren't supported by the source data; set
> only what the Oblivion flags/priority and the Skyrim "must be enabled to run"
> requirement justify.

---

## Stages

| Oblivion stage | Skyrim stage | Transform |
|----------------|--------------|-----------|
| `INDX` (S16 stage index) | `INDX` (4B: Stage Index U16 + Flags U8 + Unknown U8) | Copy the index into the U16; set Flags from QSDT semantics (Start Up/Shut Down) if known, else 0. |
| Log entry `QSDT` (U8: 0x01 Complete) | Log entry `QSDT` (U8: 0x01 Complete, 0x02 Fail) | Carry the Complete bit. |
| Log entry `CNAM` (journal text) | Log entry `CNAM` (journal text) | Copy. |
| Log entry conditions `CTDA[]` | Log entry `CTDA[]` | Translate. |
| Log entry result script (SCHR/SCDA/SCTX/SCRO) | (no per-log script field) | **Re-author into the quest's Papyrus** — Skyrim runs stage logic in the quest script's `Fragment_N` for that stage, not in the log entry. See "Scripts". |
| — | `NAM0` (Next Quest) | Oblivion has no next-quest link on log entries; leave unset unless modeling a chain. |

The journal text and stage numbers carry over exactly, so the journal *reads* the
same. Whether the quest *advances* depends entirely on the result scripts being
re-authored (below).

---

## Targets

| Oblivion target | Skyrim target | Transform |
|-----------------|---------------|-----------|
| `QSTA` Target (→REFR/ACRE/ACHR) | `QSTA` Target (→ACHR/REFR/PGRE/PHZD/…) | Remap the FormID. ACRE (placed creature) targets become ACHR in Skyrim (creatures are NPCs there). |
| `QSTA` Flags (0x01 compass ignores locks) | `QSTA` Flags (0x01 compass ignores locks) | Carry the bit. |
| target `CTDA[]` | target `CTDA[]` | Translate. |

> In Skyrim, quest targets are usually expressed through **aliases + objectives**
> rather than direct refs. A direct QSTA→QSTA copy works for a simple compass
> marker, but a faithful objective/marker experience may require synthesizing an
> alias for the target and a QOBJ objective that points at it. **Judgment call** —
> the simple copy preserves the marker; the alias/objective version preserves the
> full Skyrim UX. Note which you chose.

---

## Objectives (Skyrim has them; Oblivion doesn't)

Oblivion shows quest progress purely through stage journal text. Skyrim adds a
parallel **Objectives** system (QOBJ index + display text + targets) that drives
the on-screen objective and the compass marker. To reproduce Oblivion's "current
goal" feel you may synthesize one objective per meaningful stage, with display
text derived from the stage's journal entry and a target alias for the marker.
This is optional for journal fidelity but improves marker/UX fidelity.
**Judgment call** — not derivable 1:1 from Oblivion data.

---

## Aliases (Skyrim-only; synthesize only as needed)

Oblivion scripts reference objects by EditorID/FormID directly. Skyrim Papyrus
strongly prefers **quest aliases** (ReferenceAlias/LocationAlias). When you
re-author an Oblivion quest script into Papyrus:

- If the script referenced specific actors/refs, create reference aliases for
  them and have the fragment use the alias, so the quest is robust the way Skyrim
  quests are.
- If it only used globals/quest variables, you may not need aliases.

`ANAM` (Next Alias ID) must equal the count of aliases created (0 if none).

---

## Scripts (SCRI + stage result scripts) → Papyrus VMAD

This is the functional heart of a quest and has **no data-only conversion**.

- **Oblivion quest script (SCRI → SCPT):** an imperative script with blocks like
  `GameMode`, `OnActivate`, etc., reading/writing quest variables and calling
  `SetStage`, `StartQuest`, etc. In Skyrim this becomes the quest's **Papyrus
  script** (and `VMAD` fragments for stage/dialogue logic). The Oblivion source
  tells you the behavior to reproduce; it must be re-authored and compiled.
- **Stage result scripts (SCTX):** the per-stage logic. In Skyrim these become
  the quest script's stage fragments (`Fragment_<stage>`), invoked when
  `SetStage` runs.

Common translations (intent-level):

| Oblivion | Papyrus (Skyrim) |
|----------|------------------|
| `set QQ.var to X` | `QQ.var = X` (property/variable) |
| `SetStage QQ N` | `GetOwningQuest().SetStage(N)` / `QQ.SetStage(N)` |
| `StartQuest QQ` / `StopQuest QQ` | `QQ.Start()` / `QQ.Stop()` |
| `player.AddItem X N` | `Game.GetPlayer().AddItem(X, N)` |
| `AddTopic T` | (usually replaced by conditions, not a call — see `dial-info.md`) |
| `GetStage`, `GetQuestVariable` in conditions | translate as CTDA functions, not script (see `conditions.md`) |

**Fidelity:** without re-authoring these, the quest can be entered and its
journal read, but it will **not progress** — stages won't advance, items won't be
granted, follow-up dialogue won't unlock. Always treat a quest with a SCRI or any
stage result script as requiring Papyrus work, and flag it.

---

## Fidelity summary for QUST

| Aspect | Faithful from data alone? | Notes |
|--------|---------------------------|-------|
| Journal text, stage numbers | Yes | Direct copy. |
| Flags (enabled / repeated stages) / priority | Yes | Bit-compatible; one repeat flag is a judgment. |
| Targets (compass marker) | Yes (simple) | Alias/objective UX is a judgment add-on. |
| Quest conditions | Yes | Via `conditions.md`. |
| Quest progression (scripts) | **No** | Papyrus re-authoring required. The functional core. |
| Objectives / aliases | No | Skyrim-only systems; synthesize as needed. |
