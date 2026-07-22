# What Oblivion dialogue does not transfer to Skyrim, and what to do about it

Every number below comes from running the two emulators over real data —
`tools/oblivion_dialog_emulator.py` against `export/Oblivion.esm` and
`tools/dialog_emulator.py` against the converted ESM — with the condition tables
read out of both game executables (see
[dialogue_engine_contracts.md](dialogue_engine_contracts.md)). Counts are for
vanilla Oblivion.esm: 3,817 DIALs, 19,278 INFOs, 2,482 NPCs.

The two engines agree on more than expected. Condition functions use the same
opcode numbering in both games (`GetIsID` is 0x1048 either side), and of the
370 condition functions Oblivion defines, only **78 share an index with a
different Skyrim function** — of which only **2 actually occur in vanilla
Oblivion data**, and both are harmless renames. The real problems are not the
conditions. They are the four mechanics Skyrim simply does not have.

## 1. Disposition — 1,451 INFOs — *translate, do not drop*

**Skyrim has a disposition system.** It is not called disposition and it is not
a 0–100 scale, which is why searching the engine for the word finds nothing.
An NPC's friendliness is a **Relationship Rank from −4 to +4** — Archnemesis,
Enemy, Foe, Rival, Acquaintance, Friend, Confidant, Ally, Lover — default 0,
read by condition function **419 `GetRelationshipRank`** and settable from
Papyrus through the native `Actor.SetRelationshipRank`
([UESP](https://en.uesp.net/wiki/Skyrim:Disposition)).

Oblivion uses `GetDisposition` on 1,751 conditions across 1,451 INFOs, tiering
dialogue overwhelmingly at 30 and 70:

| Threshold | Conditions |
|---|---|
| 30 | 646 |
| 70 | 564 |
| 40 | 146 |
| 50 | 128 |
| 20 | 107 |
| 60 | 80 |

**Implemented: the 0–100 disposition maps onto the −4..+4 rank**, in
`dialog_conditions.disposition_to_rank`. A full Oblivion.esm conversion emits
1,724 `GetRelationshipRank` conditions and leaks zero `FastTravel`:

| Oblivion disposition | Skyrim rank |
|---|---|
| 0–19 | −2 Foe |
| 20–39 | −1 Rival |
| 40–60 | 0 Acquaintance *(both games' default)* |
| 61–79 | 1 Friend |
| 80–100 | 2 Confidant |

Ally (3) and Lover (4) are left unused: Oblivion has no equivalent relationship,
and reserving them keeps quest-granted ranks meaningful.

This preserves the **ordering** of the tiers, which is the part that matters. A
line Oblivion gated behind high disposition stays gated more tightly than a
neutral one, so a stranger does not get intimate-friend greetings. The previous
behaviour — dropping the condition — made all three tiers unconditional and
fired them at the same NPC at once.

Two details the translation must get right, both covered by
`test_disposition_becomes_relationship_rank`:

* The condition must be **rewritten, not passed through**. CTDA index 76 is
  `FastTravel` in Skyrim, so an untouched condition invokes an unrelated
  function.
* `GetRelationshipRank` compares against an actor, which in dialogue is always
  the player — base form `0x00000007`, the engine-fixed id, never our converted
  copy of the TES4 Player record.

A `Use Global` disposition comparison is still dropped: it names a GLOB holding
a 0–100 value that cannot be rescaled at conversion time, and comparing a rank
against it would be meaningless.

Note that vanilla Skyrim itself never uses function 419 in a dialogue condition
— it drives relationship rank from Papyrus and gates dialogue on
`IsInFriendStatewithPlayer` / `HasParentRelationship` instead. Using 419
directly is therefore unusual but well-formed; the function, its parameter type
(Actor) and its Papyrus counterpart are all present in the engine.

The tiers now behave, where before conversion they collapsed. Pinarus
Inventius's greeting pool by relationship rank, from
`tools/dialog_emulator.py --relationship-rank`:

| Rank | Greetings |
|---|---|
| −2 Foe | 23 |
| 0 Acquaintance | 49 |
| 2 Confidant | 50 |

Previously all 98 fired at every rank. Oblivion's own numbers for the same NPC
(`--disposition 10 / 50 / 90`) show the same shape — a markedly smaller hostile
pool widening as regard improves.

## 2. AddTopic — 586 gated topics

**The gap.** In Oblivion a conversation topic is invisible until something adds
it: an INFO's `AddTopic` list, an `AddTopic X` result script, or a quest stage.
Skyrim has no equivalent — a topic shows whenever its conditions pass. Of 3,183
conversation topics, **586 are AddTopic-gated** and 2,597 are available from the
start.

**Recommendation: keep the existing global-per-topic translation. It works.**
The converter creates one `TES4Unlock_<topic>` global per gated topic and adds
`GetGlobalValue(...) == 1` to every INFO of that topic, set from a Papyrus
fragment when a revealing line plays. The two emulators independently confirm
this is faithful: Oblivion reports "Mountain Lions" as *hidden, awaiting
AddTopic* for Pinarus at FGC01Rats stage 40, and Skyrim reports the same topic
blocked solely by `GetGlobalValue(TES4Unlock_MountainLionsTOPIC)`. Same gate,
same state, expressed two ways.

The one thing to watch is over-gating. A topic mentioned by name in a bark is
auto-added by Oblivion, and treating that as a reveal previously stripped gates
from 162 topics — see the note in
[dialogue_conversion_notes.md](dialogue_conversion_notes.md).

## 3. Persuasion — 39 DIALs, 130 INFOs

**The gap.** The whole persuasion minigame is gone. Oblivion's 39 persuasion
topics are the wheel's outcomes — `ADMIRE_HATE`, `ADMIRE_LOVE`, `COERCE_*`,
`BOAST_*`, `JOKE_*`, `BRIBE`, `DEMAND*` — each a response to a wheel wedge
played at a disposition tier. Skyrim replaced all of it with Speech-checked
individual lines and persuade/bribe/intimidate *branches* on specific quests.

**Recommendation: skip entirely.** These 130 INFOs have no target to convert
into: there is no wheel to trigger them, no disposition to tier them, and the
Skyrim subtypes named `Intimidate`/`Bribe`/`Flatter` are per-quest favour
dialogue, not a general minigame. Converting them produces topics that can never
fire. This is the one family where dropping is clearly right — and it is cheap,
being 0.7% of INFOs.

## 4. Reply-only topics — 1,955 topics

**The gap.** Oblivion reaches follow-up lines through an INFO's `Choice` list;
the target is a topic that must never sit in the menu on its own. Skyrim
expresses the same idea as a DLBR branch flag: Top-Level (DNAM bit 0) means "in
the menu", clear means "reachable only by following a link".

**Recommendation: keep mapping Choice targets to Normal branches, and only
promote when the revealer is itself gated.** 1,955 topics are Choice targets.
This was a real bug: the Skyrim emulator originally listed every branch as a
menu topic and showed Pinarus offering 12 permanent topics — `SadGeneral`,
`AngerReceive`, `AnswerPositive` and the rest of the emotional-response family —
when he offers exactly one, "I would like some training." The emulator now
checks the Top-Level flag and nests Choice targets under the line that offers
them.

## 5. Smaller gaps, with verdicts

| Oblivion mechanic | Size | Recommendation |
|---|---|---|
| `GetQuestVariable` / `GetScriptVariable` | 4,429 conditions | **Translate, do not drop.** The legacy VM is gone but the live equivalents `GetVMQuestVariable` (629) / `GetVMScriptVariable` (630) exist; the property name travels in a CIS2 subrecord. Dropping these kills most quest package gating. |
| `GetCrimeGold` | 48 conditions | **Already remapped to 459 — keep it.** Index 116 is `IsIntimidatedbyPlayer` in Skyrim, so leaving it alone would call the wrong function. `_FUNC_REMAP` handles this correctly. |
| `IsRidingHorse` (327), `IsPlayersLastRiddenHorse` (339) | 25 conditions | **Pass through.** Renamed to `IsRidingMount` / `IsPlayersLastRiddenMount` at the same index; semantics unchanged. |
| Combat / Detection / Service barks | 945 INFOs | **Convert; the subtypes exist.** Both engines carry the same families (Attack, Hit, Flee, Yield, Noticed/Seen/Unseen/Lost, Barter/Repair/Training). |
| `Say Once` (1,162), `Goodbye` (1,908) | flags | **Convert.** Both flags exist in Skyrim's INFO response flags at the same bit positions (0 Goodbye, 2 Say Once). |
| Fixed-FormID channels | 4 | **Handle by FormID, not type.** `GREETING` (0x0000C8) is `DATA.Type=0`, the same value ordinary topics use, so it can only be told apart by its hardcoded id. Classifying by type alone turns the greeting channel into a menu topic. |
| Result scripts | 5,694 INFOs | **Convert to Papyrus fragments.** Already done; this is where AddTopic reveals and stage advances live, so failures here silently break gating. |

## How to check any of this yourself

    # what Oblivion gives an NPC
    python tools/oblivion_dialog_emulator.py export/Oblivion.esm \
        --npc PinarusInventius --stage FGC01Rats:40

    # what the conversion gives the same NPC
    python tools/dialog_emulator.py output/Oblivion.esm/Oblivion.esm \
        --npc PinarusInventius --stage FGC01Rats:40

    # the engines' own condition tables, side by side
    python tools/oblivion_engine_extract.py --functions GetDisposition
    python tools/dialog_engine_extract.py --functions FastTravel

A topic present on one side and absent on the other is a conversion bug unless
it falls into one of the five categories above.
