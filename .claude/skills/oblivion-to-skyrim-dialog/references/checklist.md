# Conversion Behavioral-Fidelity Checklist (TES4 → TES5)

A converted dialogue/quest is "done" only when it would **behave the same in
Skyrim as it did in Oblivion** for the same player actions — not when the records
merely exist. This checklist is organized around behavior, then lists the records
Skyrim requires that Oblivion has no source for, then the known fidelity gaps.

---

## Behavioral acceptance tests (the bar)

For a converted quest/topic, each of these should hold in-game:

- [ ] **Same speaker.** The line is said by the same NPC (not by everyone, not by
      the wrong NPC). → requires `GetIsID`/identity gating + correct race→VTYP
      mapping + `GetIsVoiceType`.
- [ ] **Same timing.** The line/topic appears at the same point in the game. →
      requires faithful `GetStage`/`GetQuestRunning`/`GetStageDone` translation
      **and** re-expressing Oblivion's `AddTopic` visibility as conditions
      (Skyrim won't hide a topic on its own).
- [ ] **Same conversation flow.** Choices lead to the same follow-ups; goodbye/
      say-once behave the same. → requires TCLT choice graph + ENAM flags +
      DLBR branch structure (top-level vs linked).
- [ ] **Same quest progression.** Stages advance, items/effects are granted,
      follow-up dialogue unlocks. → requires **result scripts re-authored as
      Papyrus** (the deepest gap; see below).
- [ ] **Right voice plays, audibly.** → requires VTYP+GetIsVoiceType +
      re-pathed/transcoded audio (MP3→FUZ) + LIP.
- [ ] **NPC is present to talk to.** → requires a working (nearest-template) AI
      package.

---

## Per-record conversion checklist

### DIAL
- [ ] EDID copied/generated.
- [ ] `DATA.Type` mapped to Category + Subtype + `SNAM` (4-char code; required).
- [ ] Bark topics identified by reserved EDID (GREETING/HELLO/GOODBYE/combat/
      detection) and given the right subtype + SNAM; **no BNAM** on barks.
- [ ] `QSTI` → `QNAM` (primary quest); extra QSTI handled (conditions or
      duplication); orphan topics given a catch-all running quest.
- [ ] Conversation topics given a synthesized `BNAM` → DLBR.
- [ ] `TIFC` = child INFO count.

### INFO
- [ ] `DATA.Flags` → `ENAM` (compatible bits only; drop Run-Immediately/Rumors).
- [ ] Responses re-packed (TRDT 12B→24B); text + emotion preserved.
- [ ] `TCLT` choices copied.
- [ ] **`GetIsVoiceType` injected** for the line's voice type(s).
- [ ] **Identity gate** present for NPC-specific conversation lines.
- [ ] **AddTopic visibility re-expressed as conditions** (stage/quest/identity).
- [ ] **Result script re-authored as VMAD Papyrus fragment** (or explicitly noted
      as dropped — and the quest-progression impact understood).
- [ ] Conditions translated (`conditions.md`): re-packed, index-reconciled,
      FormIDs remapped.

### QUST
- [ ] `DATA` → `DNAM` (flags incl. Start/Starts Enabled; priority; type=0).
- [ ] `ANAM` (Next Alias ID) set.
- [ ] Stages: INDX copied; QSDT Complete bit carried; CNAM journal text copied.
- [ ] Stage **result scripts re-authored** into the quest's Papyrus fragments.
- [ ] Targets remapped (ACRE→ACHR); compass flag carried.
- [ ] `SCRI` quest script **re-authored as Papyrus** (+ aliases as needed).
- [ ] (Optional UX) objectives/aliases synthesized for markers.

### IDLE
- [ ] `MODL` .kf → `DNAM` filename, validated against Skyrim animations (or
      mapped to an existing Skyrim gesture idle).
- [ ] `ENAM` animation event supplied.
- [ ] `DATA` parent/prev → `ANAM` related idles; ANAM (group section) → DATA.
- [ ] Conditions translated.

### PACK
- [ ] Mapped to the nearest Skyrim package **template** by intent (Eat/Sleep/
      Wander→Sandbox/Travel/…), with PLDT/PSDT/PTDT fed into its input data.
- [ ] Conditions translated; FormIDs remapped.
- [ ] Non-trivial packages (escort/force-greet/conversation) flagged for
      hand-authoring.

### Voice / NPC
- [ ] Every speaking NPC assigned `VTCK` → a VTYP via race+gender (`voice.md`).
- [ ] Per-NPC voice overrides for uniques where the racial default is wrong.
- [ ] Audio re-pathed to the voice-type layout + transcoded MP3→FUZ + LIP.

---

## Records Skyrim requires that Oblivion has NO source for (must generate)

These are not copies — they must be **synthesized**, or dialogue won't work:

| Record / artifact | Why required | Derived from |
|-------------------|--------------|--------------|
| **VTYP assignment (VTCK)** per speaking NPC | Skyrim routes voice by VTYP; without it an NPC can't speak its lines correctly. | NPC race + gender (judgment mapping, `voice.md`). |
| **`GetIsVoiceType` conditions** on INFOs | Without them lines leak to all voices / export audio for all voices. | The line's NPC(s) → VTYP. |
| **DLBR branches** | Skyrim needs to know which topics are menu-level vs linked-only. | Topic type + TCLT/TCLF link graph (`dial-info.md`). |
| **AddTopic-replacement conditions** | Skyrim has no AddTopic; visibility must be conditions. | Quest/result-script analysis → stage/quest/identity gates. |
| **Papyrus VMAD fragments** (INFO + QUST) | Skyrim runs result/quest logic in Papyrus, not result scripts. | Oblivion SCTX source (re-authored, compiled). |
| **SEQ file** per Start-Game-Enabled quest | Since game v1.7, SGE quests' dialogue/scenes don't initialize without it. | The set of SGE quests. |
| **DLVW views** (optional) | CK editability only; no runtime effect. | One per quest. |

---

## Known fidelity gaps (call these out in any conversion report)

| Gap | Impact | Status |
|-----|--------|--------|
| **Result/quest scripts (Papyrus)** | Quests don't advance; script-driven dialogue never unlocks. | No data-only conversion; must re-author. **Deepest gap.** |
| **AddTopic timing** | Topics appear at the wrong time (usually too early) unless re-gated. | Needs script analysis; safe fallback is quest-running + identity gating. |
| **Voice-type choice** | Racial mapping loses per-NPC personality nuance. | Judgment; allow overrides. |
| **AI packages (fine behavior)** | Escort/force-greet/conversation packages don't behave identically. | Nearest-template gets presence/schedule; fine behavior hand-authored. |
| **Removed mechanics** (persuasion minigame, disposition, rumors, infamy/fame, birthsign) | Lines survive but the *systems* gating/driving them are gone. | Lines map; mechanics can't. Note per-topic. |
| **Audio** | Lines silent until transcoded/re-pathed. | Asset pipeline step (MP3→FUZ + LIP). |
| **IDLE playback** | Oblivion .kf anims won't play in Skyrim's behavior graph. | Map to existing Skyrim gesture idles. |

---

## Source-data-insufficiency flags (where you must look beyond DIAL/INFO/QUST)

The conversion **cannot be made faithful from the dialogue/quest records alone**
in these cases — they require reading the Oblivion **scripts** (SCPT source / SCTX
result scripts) and making judgment calls:

- **When a topic became visible** (AddTopic was a script action, not a record
  field) → must analyze quest/result scripts.
- **What a line/stage actually did** (SetStage, item grants, variable sets) →
  must read result scripts to re-author Papyrus.
- **Which Skyrim VTYP best fits an NPC** → race+gender is known, but the choice of
  voice type (especially for uniques and non-racial-voice races) is judgment.
- **Which Skyrim package template matches an Oblivion package** → intent-based
  judgment.

Always state in a conversion report which of these were resolved by script
analysis, which by judgment/default, and which were left as gaps.
