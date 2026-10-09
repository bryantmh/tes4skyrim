# English Oblivion/Knights dialogue loss audit (2026-10-03)

The conversion loses valid source dialogue in **English as well as Russian**.
The English originals resolve all their TCLT references. The installed English
conversion has 86 unresolved TCLT references in Knights, targeting 11 topics
that the converter omitted. Their owners are Oblivion.esm and Knights.esp,
both present in the inspected set; an absent third-party mod is not needed to
explain these references.

Removing unresolved choices prevents the UTF-8 startup crash described in
[dialogue_engine_contracts.md](dialogue_engine_contracts.md), but does not
restore the missing responses or their result fragments. This is a separate
dialogue fidelity defect that predates the UTF-8 writer change.

## Inputs and method

Original English plugins were read from
`E:/SteamLibrary/steamapps/common/Oblivion/Data/`.
Converted English plugins were read from
`E:/Games/Nexus Mod Manager/SkyrimSE/VirtualInstall/`, using the unsuffixed
`Oblivion.esm/Oblivion.esm` and `Knights.esp/Knights.esp` folders. These are the
English baseline files, not the repaired Russian Knights.

| File | SHA-256 |
| --- | --- |
| Original Oblivion.esm | `a26e21ea8c3041f8737ffb3a266129dedb7f8a88590625ecfecd5eb7f66b4a70` |
| Original Knights.esp | `a0b48c54ef788fd17a3c042c24795e52aefa76050d226448b91005b36f5d681b` |
| Converted Oblivion.esm | `fb032c7539bb19f8896b2714d1ffba2a7dcb734429e05df40fd60ecb548700a6` |
| Converted Knights.esp | `38e56f24eb7f6b671025c153ddacd8ac2b371207bd1b4cf39275962aa199418f` |

The binary audit compares effective DIAL/INFO records, their parents, TCLT
targets and response text. Record identities use the owning filename and local
FormID, resolved through each file's MAST table. The extra Skyrim.esm master in
converted files therefore does not appear as a lost record. Deleted targets
are not valid choices; owners without a supplied plugin are unindexed, rather
than assumed missing. Neither input set had choices to unindexed owners.

A supplementary source-code investigation used the English cached exports.
Every exported DIAL EditorID and every INFO Choice array was checked against
the original binaries before using the exports for classification. Conversation
plans used DIAL, INFO, SCPT, QUST, ACHR, ACRE, NPC_ and CREA records; the large
REFR export was not loaded. Plans are supporting evidence, not a complete
simulation of every reference or script call.

No plugin conversion, Papyrus compilation or game launch was performed for
this audit. No game files or converter settings were changed. Private logs and
chat files were not used.

## The 86 dangling choices

The merged English originals contain 4,015 DIAL and 20,146 INFO records, with
**zero unresolved TCLT targets**. The English conversion contains **86**
unresolved choices, all in Knights, in **49 INFO records** whose source parent
is the shared `INFOGENERAL` topic. These are 86 links to 11 distinct topics,
not 86 distinct topics.

FormIDs below are in the original TES4 namespace (Oblivion slot 00, Knights
slot 01). The conversion adds one master slot.

| Source target | EditorID | Links | Source INFOs | INFOs with no matching response text anywhere in the English output |
| --- | --- | ---: | ---: | ---: |
| `00024110` | FearGeneral | 10 | 5 | 4 |
| `00024112` | HappyReceive | 12 | 10 | 10 |
| `00024113` | SurpriseReceive | 23 | 11 | 10 |
| `00024115` | FollowupPositive | 4 | 16 | 15 |
| `00024117` | AnswerPositive | 3 | 23 | 17 |
| `0002411A` | NeutralReceive | 22 | 22 | 18 |
| `0006137E` | MQ00NineDivinesResponse | 1 | 2 | 2 |
| `01002B9D` | NDRumorAttackResponse2 | 1 | 3 | 3 |
| `01002BA1` | NDRumorAttackResponse1 | 1 | 3 | 3 |
| `01002BC7` | NDRumorsUmaril | 6 | 6 | 6 |
| `01002BCE` | ND06KellenRumors | 3 | 3 | 3 |
| **Total** | | **86** | **104** | **91** |

All 11 source targets are live DIAL records with DATA.Type 1. None of their
104 original INFO IDs survives in the English output. No replacement DIAL
EditorID containing one of these 11 names was found. The remaining 13 INFOs
share short response text with other output records; such a match does not
establish a playable replacement for the original conversation.

The first six topics are explicitly skipped response channels. The other five
fall under the general NPC-conversation skip predicate and are not restored
by the conversation driver plan.

### Why those choices escaped the earlier cleanup

In [overrides/nested.py](../../tes5_import/overrides/nested.py),
`_attach_new_records` attaches Knights' new INFO records under the master's
existing `INFOGENERAL` DIAL. `_convert_nested` calls `convert_INFO` directly.
These records bypass the normal `build_dialog_groups` path and its
`_strip_dead_tclt` pass. The bark-parent exception does not apply to
`INFOGENERAL`.

There is also a master visibility problem: a cleanup supplied only Knights'
own skipped topics cannot recognize the omitted Oblivion topics. Applying
`_strip_dead_tclt` to copies of these 49 English source INFO dictionaries gave:

| Skipped-target set | Choices before | Removed | Remaining |
| --- | ---: | ---: | ---: |
| Knights topics only | 86 | 11 | 75 |
| Knights and Oblivion topics | 86 | 86 | 0 |

The final writer-level cleanup introduced in `b2bb8cd3` covers these records
and the indexed master set. It removes unsafe references; it does not supply
the target dialogue. Both facts must remain separate when judging the fix.

## Quest-bearing continuations are also omitted

Beyond those 11 targets, **306 Type-1 source topic IDs** are absent from the
English output: 245 from Oblivion and 61 from Knights. Of these, **189 have
source INFOs** (131 Oblivion, 58 Knights), holding **1,755 INFOs** in total
(1,588 and 167). The other 117 source topics are empty. These are counts of
absent original IDs, not a claim that every associated response is lost:
some conversion paths synthesize alternative topics or INFOs.

Six INFOs under absent topics contain SetStage result commands. In each of
these six cases the original INFO ID is absent **and its exact response text
does not occur anywhere in the English output**:

| Original INFO | Topic | Result command |
| --- | --- | --- |
| `00035FBB` | TG04Hlidara2 | `SetStage TG04Mistake 50` |
| `0003EABD` | MQ15OrtheEldamil | `SetStage MQ15 58` |
| `00081183` | SE05EavesDropDetected | `SetStage SE05 65` |
| `0007BA9E` | TG11TalkToMillona15 | `SetStage TG11Heist 135` |
| `01002CAF` | ND10SirThedretSpeech06 | `SetStage ND10Fin 100` |
| `01002CCF` | ND09ProphetSermon03 | `SetStage ND09 25` |

This proves that the corresponding source dialogue records are missing. It
does not by itself prove an in-game quest deadlock: alternate stage-setting
paths and PEX execution were not exhaustively checked, and the quests were
not played during this audit.

### Concrete multi-topic chains

The original English scripts directly start these conversation heads:

- `NDProphetSCRIPT`: `ND09ProphetSermon01`, followed through TCLT by
  `ND09ProphetSermon02` and `ND09ProphetSermon03`. The last INFO advances ND09
  to stage 25.
- `ND10FinSCRIPT`: `ND10SirThedretSpeech01`, followed by topics 02 through 06.
  The last INFO advances ND10Fin to stage 100.
- `GrayFoxScript`: `TG11TalkToMillona`, followed by topics 02 through 15.
  The last INFO advances TG11Heist to stage 135.

The heads remain in the converted English plugins; the listed terminal
topics and INFOs do not. Cached generated Knights sources also show a single
`Say` of the first topic for the prophet and Thedret. Those sources are
supporting evidence only; the packaged English PEX files were not verified.

Generated `TES4_TIF__01002CCF.psc` and `TES4_TIF__01002CAF.psc` fragment sources
still contain their stage-setting calls. Keeping a fragment source does not
restore its omitted INFO record or create an engine call to that fragment.

### Why the existing chain support misses them

In [dialogue/converter.py](../../tes5_import/dialogue/converter.py),
`is_npc_to_npc_conversation` spares topics named directly by
Say/SayTo/StartConversation scans. It does not retain the transitive TCLT
continuations of those topics. A directly called head survives while later
topics are classified as unused NPC chatter and dropped.

The existing restoration mechanisms cover narrower shapes:

- `build_script_chain_map` in
  [dialogue/conversations.py](../../tes5_import/dialogue/conversations.py)
  counts consecutive quest-variable gates among INFOs under **one DIAL**.
  It returns no chain for the three heads above. `_replay_chain` in
  [script_convert/commands.py](../../script_convert/commands.py) repeats the
  same topic, so it cannot walk a chain spread across multiple DIAL records.
- `build_conversation_plan` detects identity-pinned, quest-advancing chains
  rooted in HELLO. The English Oblivion export produces 15 such chains, but
  none restores the missing terminal topics listed above. Knights produces
  none. This planner does not generally recognize script-started topic heads.
- [dialogue/groups.py](../../tes5_import/dialogue/groups.py) and
  [script_convert/pipeline.py](../../script_convert/pipeline.py) disable the
  generated conversation driver for plugins with masters, including Knights.

The dialogue skip logic is already present at `847305a7`, the earliest
available commit touching these files. The UTF-8 writer commit `65b3b15a`
changes none of these dialogue, nested-override or StartConversation paths.
The available history establishes that this defect predates that change;
it does not establish who originally authored the earlier skip policy.

## Requirements for a complete fix

Retaining all dropped Type-1 topics as player-selectable topics would expose
NPC reaction lines as dialogue-menu choices. Deleting their incoming TCLTs
avoids invalid references but discards the conversation. Neither is a complete
restoration strategy.

A repair needs to identify script-started heads and reachable continuations
across plugin/master boundaries, retain their INFOs and result fragments, and
replay the topics with the original speaker/listener selection and conditions.
Multi-topic chains need explicit topic transitions, branching and cycle
handling; the existing one-topic counter replay is insufficient. Dependent
plugins need distinct, correctly bound driver records/scripts rather than a
blanket driver exclusion. Response channels need an appropriate NPC reaction
route rather than automatic exposure in the player's topic menu.

Validation should separately cover preserved dialogue records, valid emitted
references, and actual English quest progression through the affected scenes.
A successful launch alone does not establish dialogue fidelity.

## Reproduction

The reusable binary audit is
[tools/dialog/dialogue_fidelity_audit.py](../../tools/dialog/dialogue_fidelity_audit.py).
It reads only the explicitly supplied plugins and writes a JSON report. Supply
pairs in master/load order. From the repository root, in PowerShell:

```powershell
.venv/Scripts/python.exe tools/dialog/dialogue_fidelity_audit.py `
  --pair 'E:/SteamLibrary/steamapps/common/Oblivion/Data/Oblivion.esm' `
         'E:/Games/Nexus Mod Manager/SkyrimSE/VirtualInstall/Oblivion.esm/Oblivion.esm' `
  --pair 'E:/SteamLibrary/steamapps/common/Oblivion/Data/Knights.esp' `
         'E:/Games/Nexus Mod Manager/SkyrimSE/VirtualInstall/Knights.esp/Knights.esp' `
  --report output/english-dialogue-investigation-20261003/binary-audit.json
```

The checked English files decode with source CP1252 and output UTF-8. Use the
encoding switches when inspecting a different build. The JSON includes input
hashes, unresolved versus unindexed choices, affected targets, absent Type-1
topic IDs, and missing INFOs containing stage/quest result commands.

The reusable binary audit independently reproduced the export investigation's
86 links, 49 affected INFOs, 104 target INFOs, 91 INFOs without matching text,
and six stage-fragment candidates. As a master-availability check, running
with only the Knights pair reported 11 invalid own-plugin choices and kept
the other 75 choices as unindexed Oblivion references. It did not classify
those 75 as missing simply because the master was not supplied.

The supplementary export/classification audit and its JSON were saved locally
under `output/english-dialogue-investigation-20261003/`; they are not required
by the reusable binary audit.
