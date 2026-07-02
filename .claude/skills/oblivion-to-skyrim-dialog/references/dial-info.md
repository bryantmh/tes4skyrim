# DIAL & INFO Conversion (TES4 → TES5)

The hardest part of the conversion, because Skyrim's dialogue is conditions-only
(no AddTopic) and requires branch/voice scaffolding Oblivion doesn't have. For
field layouts see the `oblivion-dialog-system` and `skyrim-dialog-system` skills.

---

## DIAL — Dialog Topic

### Field map

| Oblivion DIAL | Skyrim DIAL | Transform |
|---------------|-------------|-----------|
| `EDID` | `EDID` | Copy. Generate one if absent (Skyrim tooling expects EditorIDs). |
| `QSTI[]` (quest assoc.) | `QNAM` (single owning quest) | Pick the **primary** owning quest (first QSTI). See "Quest ownership" below — a topic with multiple QSTI needs a decision. |
| `FULL` (topic text) | `FULL` | Copy. This is the menu prompt. |
| `DATA.Type` (U8 enum) | `DATA` (TopicFlags U8 + Category U8 + Subtype U16) + `SNAM` (4-char code) | Map per the table below. |
| — | `BNAM` (→DLBR) | **Synthesize** a branch for conversation topics; omit for barks (see DLBR synthesis). |
| — | `PNAM` (priority float) | Default 50.0; raise from the owning quest's priority if you want quest priority to influence topic order. |
| — | `TIFC` (info count U32) | Count of child INFOs placed in the topic's GRUP. |
| `INOM`/`INOA` | (internal ordering) | Not needed; Skyrim derives order from PNAM/GRUP. Drop. |

### Topic Type → Category / Subtype / SNAM

Oblivion `DATA.Type` is a single 7-value enum. Skyrim's DIAL `DATA` is a 4-byte
struct that must be written in the correct **on-disk** order and with the
correct **real subtype numbers** — both differ from what xEdit's struct labels
suggest.

> ⚠ **DATA byte layout (verified against real Skyrim.esm — getting this wrong
> CRASHES the game on load):**
> ```
> byte0 = TopicFlags (U8)       (usually 0)
> byte1 = Category   (U8)        ← 0 Topic,2 Scene,3 Combat,5 Detection,6 Service,7 Misc
> byte2,3 = Subtype  (U16)       ← the fine code (Hello=73, Hit=23, …); mirrors SNAM
> ```
> This IS xEdit's order (`wbDefinitionsTES5`: Topic Flags U8, Category U8,
> Subtype U16) and was confirmed byte-for-byte against real Skyrim.esm (Hello =
> `00 07 49 00`: category 7, subtype 0x49=73). If you swap them (subtype in
> byte1, category in the U16), the engine reads an out-of-range category,
> indexes its per-category topic tables out of bounds, and throws an
> `EXCEPTION_ACCESS_VIOLATION` while initializing topics at startup.
> The **subtype NUMBERS differ from xEdit's display enum** (which is shifted —
> the field is `cpIgnore` there and synced from SNAM) — take them from real
> data (table below), not from the xEdit enum. SNAM is what the engine keys
> subtype behavior on; the DATA subtype int just mirrors it.

Faithful TES4 Type → TES5 mapping by *purpose*:

| TES4 Type | TES5 Category | TES5 Subtype (real) | SNAM | Notes |
|-----------|---------------|--------------|------|-------|
| 0 Topic | 0 Topic | 0 Custom | `CUST` | Standard player topic. |
| 1 Conversation | 0 Topic | 0 Custom | `CUST` | No separate "Conversation" category in Skyrim; NPC-to-NPC / chained topics. |
| 2 Combat | 3 Combat | nearest combat subtype | (per) | Bark — Attack=20/`ATCK`, Hit=23/`HIT_`, Flee=24/`FLEE`, etc. |
| 3 Persuasion | 0 Topic | 0 Custom | `CUST` | Speechcraft minigame gone; line survives, mechanic does not. **Judgment.** |
| 4 Detection | 5 Detection | nearest detection subtype | (per) | Bark — Notice/Alert=51/`NOTA`, LostToNormal=57/`LOTN`, etc. |
| 5 Service | 6 Service | nearest service subtype | (per) | Skyrim drives services from the NPC/faction; line maps, service hookup is on the actor. |
| 6 Miscellaneous | 7 Misc | 88 Idle (`IDLE`) or nearest | (per) | Misc barks. |

**Real Skyrim subtype → SNAM → category** (use these exact numbers; from
Skyrim.esm). Reserved Oblivion EditorIDs map onto them:

| Oblivion EDID | Subtype | SNAM | Category |
|---------------|--------:|------|---------:|
| GREETING / HELLO | 73 | `HELO` | 7 |
| GOODBYE | 72 | `GBYE` | 7 |
| IDLE / IdleChatter | 88 | `IDLE` | 7 |
| Attack | 20 | `ATCK` | 3 |
| PowerAttack | 21 | `POAT` | 3 |
| Bash | 22 | `BASH` | 3 |
| Hit | 23 | `HIT_` | 3 |
| Flee | 24 | `FLEE` | 3 |
| Block | 29 | `BLOC` | 3 |
| Taunt | 30 | `TAUT` | 3 |
| Steal | 32 | `STEA` | 3 |
| Assault | 36 | `ASSA` | 3 |
| Murder | 37 | `MURD` | 3 |
| Trespass | 43 | `TRES` | 3 |
| Seen / Noticed | 51 | `NOTA` | 5 |
| Lost / Unseen | 57 | `LOTN` | 5 |
| NoticeCorpse | 70 | `NOTI` | 7 |
| ObserveCombat | 69 | `OBCO` | 7 |

> **SNAM is required** (mirrors the subtype; defaults to `CUST`). Identify
> Oblivion barks by reserved EditorID; Oblivion's coarse Type enum doesn't name
> the exact Skyrim subtype, so the EDID→subtype mapping above is partly
> **judgment** where the source is ambiguous. Full subtype table is in the
> `skyrim-dialog-system` records reference.

### Quest ownership (QSTI → QNAM)

Skyrim allows only one owning quest per topic; Oblivion topics may list several.
The owning quest matters because it gates the whole topic (the quest must be
running). Options, in order of fidelity:

1. **One QSTI:** use it as QNAM. Faithful.
2. **Multiple QSTI:** the topic was shared. Skyrim can't share a topic across
   quests, so either (a) assign the primary quest as QNAM and add
   `GetQuestRunning` conditions on the INFOs for the *other* quests, or
   (b) duplicate the topic per quest. (a) preserves visibility timing with one
   record; prefer it. **Judgment call** — the data doesn't say which quest is
   "primary"; using the first QSTI is the conventional choice.
3. **No QSTI (always-available / bark):** assign to a catch-all
   always-running quest so the topic has an owner (Skyrim ignores topics with no
   QNAM). This is required, not optional.

---

## INFO — Dialog Response (the highest-fidelity-risk record)

### Field map

| Oblivion INFO | Skyrim INFO | Transform |
|---------------|-------------|-----------|
| `DATA.Type` (U8) | (encoded via the parent DIAL's category) | Type is redundant once the topic is categorized; Skyrim INFO has no Type byte. |
| `DATA.NextSpeaker` (U8) | — | No direct Skyrim equivalent; scene/dialogue flow handles speaker turns. Drop (note loss for NPC-to-NPC). |
| `DATA.Flags` (U8) | `ENAM` Flags (U16) | Map the compatible bits (below). |
| `QSTI` (→QUST) | (parent DIAL's QNAM) | Skyrim INFO has no own quest link; it inherits the topic's quest. Use it to validate ownership. |
| `TPIC` (→DIAL) | (GRUP parent) | The INFO is placed inside the topic's GRUP; no explicit field. |
| `PNAM` (prev INFO) | `PNAM` (prev INFO) | Copy (preserves intra-topic order). |
| `NAME[]` (add topics) | **conditions** (not a field) | See "AddTopic → conditions" — the single most important transform. |
| Responses[TRDT 12B + NAM1 + NAM2] | Responses[TRDT 24B + NAM1 + NAM2/NAM3] | Re-pack TRDT (below); copy text. |
| `CTDA[]` | `CTDA[]` + injected voice/identity gates | See `conditions.md` and "Required injected conditions". |
| `TCLT[]` (choices) | `TCLT[]` (Link To) | Copy as follow-up topic links. |
| `TCLF[]` (link from) | (PNAM / branch structure) | Skyrim expresses "reachable from" via branch + PNAM; the explicit TCLF list is not a Skyrim field. Use it when building DLBR/PNAM, then drop. |
| result script (SCHR/SCDA/SCTX/SCRO) | `VMAD` script fragment | **Re-author as Papyrus** — see "Result scripts". No automatic conversion. |

### ENAM flag mapping (DATA.Flags U8 → ENAM Flags U16)

Same bit positions are compatible:

| TES4 bit | Name | TES5 bit | Name | Keep? |
|----------|------|----------|------|-------|
| 0x01 | Goodbye | 0x0001 | Goodbye | Yes |
| 0x02 | Random | 0x0002 | Random | Yes |
| 0x04 | Say Once | 0x0004 | Say once | Yes |
| 0x08 | Run Immediately | 0x0008 | Requires Player Activation | **No** — different meaning; drop. |
| 0x10 | Info Refusal | 0x0010 | Info Refusal | Yes |
| 0x20 | Random End | 0x0020 | Random end | Yes |
| 0x40 | Run for Rumors | — | — | No Skyrim equivalent; drop (rumors system differs). |

ENAM also has a Reset Hours field (U16); default 0.

### TRDT re-pack (12 bytes → 24 bytes)

| TES4 TRDT (12B) | TES5 TRDT (24B) |
|-----------------|-----------------|
| Emotion Type (U32) | Emotion Type (U32) — same 0–6 enum (Neutral…Surprise) |
| Emotion Value (S32) | Emotion Value (U32) — clamp 0–100 |
| Unused (4) | Unused (4) |
| Response number (U8) | Response number (U8) |
| Unused (3) | Unused (3) |
| — | Sound (FormID, →SNDR) = 0 (use voice file) |
| — | Flags (U8) = 0 (or 0x01 Use Emotion Animation) |
| — | Unused (3) |

Emotion type/value carry over directly, so facial emotion is preserved.

### AddTopic → conditions (the central fidelity transform)

In Oblivion, an INFO's `NAME` list (and script `AddTopic` calls) make topics
*visible*. Skyrim has no such mechanic, so visibility must be re-expressed as
conditions on the **target** topic's INFOs:

- If topic B was only ever added after the player reached stage N of quest Q
  (because that's when the `AddTopic B` script ran), B's INFOs must gain
  `GetStage(Q) >= N` (and/or `GetQuestRunning(Q)`).
- If topic B was added by a *specific NPC's* dialogue, B's INFOs need a
  `GetIsID`/voice-type gate so B doesn't appear on everyone once Q is running.

You cannot read "when was AddTopic called" from the INFO/DIAL data alone — it
lives in **result scripts and quest scripts**. So faithful AddTopic conversion
requires analyzing those scripts (SCTX) to discover the controlling stage/quest,
then encoding it as conditions. **This is a judgment-heavy, script-dependent
step**, and it is the difference between "the topic exists" and "the topic
appears at the right time." Where the script can't be analyzed, the safe fallback
is to gate B with `GetQuestRunning(Q)` for its owning quest plus a `GetIsID`
restriction to the NPCs that have lines under it (collected from the topic's own
INFO conditions), which prevents global leakage even if exact timing is lost.

### Required injected conditions (Skyrim needs these even though Oblivion didn't)

Every converted INFO should end up with:

1. **A voice-type gate** — `GetIsVoiceType(VTYP)` (OR-chain for multiple voices),
   so the right recording plays and audio isn't generated for every voice. Derive
   the VTYP(s) from the NPC(s) the line belongs to (via the line's `GetIsID`
   conditions → that NPC's race+gender → VTYP). See `voice.md`.
2. **An identity gate for conversation topics** — if the Oblivion line was
   effectively NPC-specific (it had a `GetIsID`, or its topic was only AddTopic'd
   for one NPC), keep/add `GetIsID(npc)` so Skyrim doesn't offer it to everyone.

Barks (combat/detection/greeting) generally keep their original situational
conditions and don't need an identity gate, but greetings still benefit from a
voice-type gate.

### Result scripts → VMAD (the deepest gap)

Oblivion INFOs run a **result script** when the line is delivered (e.g.
`SetStage QQ 20`, `AddTopic SomeTopic`, `Set QQ.var to 1`, `StopQuest QQ`). These
drive quest progression and conversation flow. Skyrim has no result-script
subrecord; the equivalent is a **Papyrus VMAD script fragment** on the INFO.

- There is **no data-only conversion** — Papyrus is a different language and must
  be compiled. The Oblivion source (SCTX) tells you *what behavior to reproduce*;
  you must re-author it as a fragment:
  - `SetStage Q N` → `GetOwningQuest().SetStage(N)` (or `Q_alias.SetStage`).
  - `Set Q.var to X` → a property/variable set in Papyrus.
  - `AddTopic` → usually unnecessary in Skyrim (use conditions instead), but the
    *intent* (reveal follow-up dialogue) maps to the condition work above.
  - `StopQuest`/`StartQuest` → `Stop()`/`Start()` on the quest.
- **If result scripts are dropped, the quest will not advance** and dialogue that
  depended on stage changes won't appear. This is the most common way a
  "structurally complete" conversion is behaviorally broken. Flag every INFO that
  had a result script as requiring a Papyrus fragment.

---

## DLBR synthesis (Skyrim branches from Oblivion's link graph)

Oblivion has no branch object; Skyrim needs one per conversation flow. Build them
from topic type + the TCLT/TCLF graph:

| Situation in Oblivion | Skyrim DLBR |
|-----------------------|-------------|
| A normal top-level topic the player can pick directly (Type 0/1, not reached only via TCLT) | A **Top-Level** branch (`DNAM 0x01`), `SNAM` = this topic, `QNAM` = owning quest. |
| A topic only reached as a `Choice`/`Link From` of another (it's a TCLT target, not independently shown) | A **Normal** branch (`DNAM 0x00`) so it's reachable but not menu-level. |
| Bark topics (combat/detection/greeting/misc) | **No branch** — barks aren't part of a branch. The DIAL has no BNAM. |

DLVW (view) records are CK UI metadata only; generate one per quest if you want
the converted plugin to be editable in the CK, but they don't affect runtime
behavior.

---

## Fidelity summary for DIAL/INFO

| Aspect | Faithful from data alone? | Notes |
|--------|---------------------------|-------|
| Topic text, response text, emotion | Yes | Direct copy / re-pack. |
| Goodbye/Random/SayOnce flags | Yes | Bit-compatible. |
| Choice graph (TCLT) | Yes | Copy. |
| Voice routing | Mostly | Needs race→VTYP judgment (`voice.md`). |
| Topic visibility timing (AddTopic) | **No** | Needs script analysis → conditions. Biggest risk. |
| Quest progression (result scripts) | **No** | Needs Papyrus re-authoring. Deepest gap. |
| Persuasion/rumors/services mechanics | No | Mechanics absent in Skyrim; lines survive, systems don't. |
