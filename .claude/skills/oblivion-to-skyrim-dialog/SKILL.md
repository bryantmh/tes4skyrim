---
name: oblivion-to-skyrim-dialog
description: >-
  Complete record-by-record mapping for converting Oblivion (TES4) dialogue,
  voice, and quest data into Skyrim (TES5) so it behaves the same in-game, not
  merely so the right records exist. Covers DIAL, INFO, QUST, IDLE, PACK, the
  voice system (race+gender → VTYP), the condition (CTDA) translation, and the
  structural pieces Skyrim requires that Oblivion has no source for (DLBR
  branches, DLVW views, VTYP routing, SEQ files). Use when planning or
  implementing TES4→TES5 dialogue/quest conversion, deciding how a specific
  Oblivion field becomes Skyrim structure, or auditing whether a conversion is
  behaviorally faithful.
---

# Oblivion → Skyrim Dialogue / Voice / Quest Conversion

This skill maps **every** Oblivion (TES4) dialogue/quest/voice record onto the
Skyrim (TES5) structure that reproduces its in-game behavior. The bar is
**behavioral fidelity**: a converted plugin should make NPCs say the same lines,
to the same people, at the same times, advancing the same quests — not just
contain records of the right signatures.

It is grounded in the two pure references in this repo (open them whenever you
need exact field layouts — this skill assumes them):

- **`oblivion-dialog-system`** skill — how TES4 dialogue actually works.
- **`skyrim-dialog-system`** skill — how TES5 dialogue actually works.
- Underlying data: `references/xEdit/Core/wbDefinitionsTES4.pas` +
  `wbDefinitionsTES5.pas`; real `export/Oblivion.esm/` and `references/Skyrim.esm`.

This mapping is reasoned fresh from those two systems. Where the source data
alone does **not** determine the right answer, the section says so explicitly and
flags it as a **judgment call**.

Detailed per-area mappings live in the reference files:

- **`references/dial-info.md`** — DIAL and INFO: topics, responses, conditions,
  choices, add-topics, result scripts. The hard part.
- **`references/quest.md`** — QUST: flags, priority, stages, journal, targets,
  scripts.
- **`references/voice.md`** — the voice system: Oblivion race+gender → Skyrim
  VTYP, and the GetIsVoiceType routing that Skyrim requires.
- **`references/idle-pack.md`** — IDLE and PACK (AI packages).
- **`references/conditions.md`** — CTDA translation: 24→32 bytes, function-index
  reconciliation, FormID remapping.
- **`references/checklist.md`** — the behavioral-fidelity checklist and the
  required new records Oblivion has no source for.

---

## The core architectural mismatch (understand before mapping anything)

The two dialogue systems share ancestry but diverge on three load-bearing
points. Every conversion decision flows from these.

### 1. Topic visibility: `AddTopic` (TES4) vs. conditions-only (TES5)

- **Oblivion:** a topic appears for an NPC only after it's been *added* — by a
  script `AddTopic` command, an INFO's Add-Topics (`NAME`) list, or topic
  chaining. Conditions then pick which INFO plays. Quest association (`DIAL.QSTI`)
  and the quest's running state implicitly gate the topic.
- **Skyrim:** there is **no AddTopic**. A topic is offered whenever its owning
  quest is running and an INFO's conditions pass. Visibility is *entirely*
  conditions + quest-running.

**Consequence:** Oblivion's `AddTopic` gating must be re-expressed as Skyrim
**conditions**. A topic that in Oblivion only appeared after `AddTopic` (e.g.
because a quest script revealed it) must, in Skyrim, gain conditions that
reproduce *when* it was visible — typically `GetStage`/`GetQuestRunning` for the
controlling quest, and `GetIsID`/voice-type to keep it on the right NPC. If you
copy Oblivion INFOs verbatim without adding these gates, every NPC will offer
every topic from the moment the quest is enabled. This is the single biggest
source of behavioral divergence.

### 2. Voice: implicit race+gender (TES4) vs. explicit VTYP (TES5)

- **Oblivion:** no voice-type record. The voice file is resolved from the
  speaking NPC's **race + gender** (optionally a race override via NPC_ `VNAM`).
  An INFO carries no voice routing at all.
- **Skyrim:** every speaking NPC has a `VTCK` → a **VTYP** record, and nearly
  every INFO carries a `GetIsVoiceType(VTYP)` condition so the engine plays the
  right recording and exports audio only for the right voices.

**Consequence:** conversion must (a) assign every speaking NPC a Skyrim VTYP
derived from its Oblivion race+gender, and (b) add `GetIsVoiceType` conditions to
INFOs. See `references/voice.md` — this is a **judgment call** in its mapping of
~10 Oblivion races × 2 genders onto Skyrim's voice-type set, because there is no
1:1 correspondence in the data.

### 3. Branch/view scaffolding: implicit (TES4) vs. explicit (TES5)

- **Oblivion:** conversation flow is implicit — topics link via `Choice` (TCLT),
  `Link From` (TCLF), and `AddTopic`. There is no branch or view object.
- **Skyrim:** topics are organized into **DLBR** (branches: top-level vs. linked)
  and **DLVW** (CK view metadata). The engine uses DLBR to know which topics are
  menu-level and which are reached only by being linked to.

**Consequence:** conversion must **synthesize** DLBR records (and optionally
DLVW) that Oblivion has no direct source for, derived from the topic type and the
TCLT/TCLF link graph. See `references/dial-info.md`.

> Two more Skyrim requirements have **no Oblivion source** and must be generated:
> **VTYP** assignment per NPC (above), and a **SEQ file** per Start-Game-Enabled
> quest (or its dialogue/scenes never initialize). Both are on the checklist.

---

## Record-to-record map (summary)

Full per-record detail in the reference files. This is the index.

| Oblivion | Skyrim | Fidelity difficulty | Key transformation |
|----------|--------|---------------------|--------------------|
| **DIAL** | DIAL (+ synthesized DLBR, DLVW) | High | Topic Type → Category+Subtype+SNAM; QSTI → QNAM; synthesize branch; AddTopic gating → conditions. `dial-info.md` |
| **INFO** | INFO | **Highest** | DATA(3B)→ENAM+structure; add GetIsVoiceType; re-express AddTopic visibility as conditions; TCLT choices→TCLT; result scripts→VMAD (or drop, see below); responses TRDT 12B→24B. `dial-info.md` |
| **QUST** | QUST | Medium | DATA(2B)→DNAM(12B); priority; stages INDX(S16)→INDX(4B); result scripts→VMAD; targets QSTA→QSTA; SCRI→VMAD. `quest.md` |
| **IDLE** | IDLE | Low–Medium | Conditions translate; DATA(parent/prev)→IDLE's anim-group fields; verify anim event names. `idle-pack.md` |
| **PACK** | PACK | **Very high (lossy)** | TES4 type-based (Find/Follow/Eat/…) → TES5 procedure-tree. No faithful 1:1; skeleton-only. `idle-pack.md` |
| NPC voice (race+gender / VNAM) | VTCK → VTYP | High (judgment) | Map race+gender → a Skyrim VTYP; assign VTCK to every speaker. `voice.md` |
| (none) | DLBR, DLVW | n/a (synthesize) | Built from topic type + link graph. `dial-info.md` |
| (none) | SEQ file | n/a (generate) | One per SGE quest. `checklist.md` |
| CTDA (24B) | CTDA (32B) | Medium | Re-pack; reconcile function indices; remap FormIDs; inject voice/identity gates. `conditions.md` |

---

## What "behaviorally faithful" means here (the standard to hold)

A conversion of a dialogue/quest is faithful when, for the same player actions:

1. **The same NPC says the same line.** Requires correct `GetIsID`/voice-type
   gating and correct race→VTYP mapping. A line that was Bendu-only in Oblivion
   must be Bendu-only in Skyrim.
2. **At the same point in the game.** Requires faithful `GetStage`/
   `GetQuestRunning`/`GetStageDone` translation *and* re-expressing AddTopic
   visibility as conditions, since Skyrim won't hide a topic on its own.
3. **The conversation flows the same way.** Requires the TCLT choice graph and
   the goodbye/say-once flags to carry over, and DLBR branches that make the same
   topics menu-level vs. linked-only.
4. **The quest advances the same way.** Requires stage/journal/target fidelity
   and — critically — the **result scripts** that fired on lines and stages.
   Oblivion result scripts (`SetStage`, `AddTopic`, `Set X to Y`, `StopQuest`)
   have no automatic Skyrim equivalent; they must be re-authored as Papyrus VMAD
   fragments or the quest will not progress. This is the deepest fidelity gap and
   is called out wherever it appears. (See `quest.md` / `dial-info.md`.)
5. **The right voice plays.** Requires VTYP + GetIsVoiceType and audio re-pathed
   from Oblivion's race/gender layout to Skyrim's voice-type layout.

> Where a faithful result cannot be produced from data alone (Papyrus scripting,
> package procedures, voice-type judgment), the reference files mark it and
> describe the closest achievable behavior plus what is lost.

---

## How to use this skill

- **Planning a converter** → read this file, then `references/checklist.md` for
  the full behavioral bar and the must-generate records.
- **Mapping a specific field** → the per-record reference file. Each gives the
  Oblivion field, the Skyrim target, the transform, and a fidelity note.
- **Anything involving scripts/packages/voice judgment** → those sections flag
  exactly where data is insufficient and what the best-effort behavior is.
