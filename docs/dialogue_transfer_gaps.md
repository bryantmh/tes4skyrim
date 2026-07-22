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

## 1. Disposition — 1,451 INFOs

**The gap.** `GetDisposition` is Oblivion's most-used mechanic with no Skyrim
counterpart at all: 1,751 conditions across 1,451 INFOs. Worse, CTDA index 76 is
reused in Skyrim for `FastTravel`, so an unmapped condition does not merely fail,
it invokes an unrelated function.

Oblivion tiers dialogue by disposition, overwhelmingly at 30 and 70:

| Threshold | Conditions |
|---|---|
| 30 | 646 |
| 70 | 564 |
| 40 | 146 |
| 50 | 128 |
| 20 | 107 |
| 60 | 80 |

**Recommendation: evaluate at disposition 50 and keep the surviving lines.**
Dropping every disposition-gated line loses 1,451 INFOs including most generic
greetings; keeping them all makes hostile-NPC and intimate-friend lines fire at
strangers. Fixing the threshold at 50 keeps the neutral register — the "Good
day." / "What can I do for you?" tier — and discards both extremes, which is the
closest single behaviour to what a mid-relationship Oblivion NPC actually says.
276 INFOs are gated on *nothing but* disposition; those become
unconditional after conversion, which is acceptable for neutral-tier lines and
wrong for the 20-and-below and 80-and-above ones, so drop those two tails rather
than admitting them.

The emulator models this with `--disposition N` (default 50). Setting it to 50
took Pinarus Inventius from 2 certain greetings to 7, all of them the neutral
register.

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
