# Quest Completability Audit — Oblivion.esm conversion

**Date:** 2026-07-17 · **Branch:** `quest-audit` (worktree off `papyrus-extension-and-speedup` @ 666faeb)
**Scope:** all 390 QUST records in Oblivion.esm (GOTY, incl. Shivering Isles)

## How the audit works

A new tool — the **quest walkthrough emulator** ([tools/quest_walkthrough.py](tools/quest_walkthrough.py) +
[tools/quest_walkthrough_tes5.py](tools/quest_walkthrough_tes5.py)) — symbolically "plays" every quest in the
converted plugin. It collects every stage-advancement edge that survived conversion (dialogue TIF
fragments, QF stage fragments, attached quest scripts, object-script VMAD attachments), gates each edge
by real Skyrim rules, and runs a fixpoint from new-game state until nothing more can fire. A quest is
completable when a TES4 complete-flag stage is reachable. The same optimistic engine runs over the TES4
export as a baseline, so stages that were already unreachable in Oblivion (orphaned scripts, commented-out
`setstage`) don't count as regressions.

Unlike the old `dialog_emulator.py`, the reachability rules follow the engine's actual behavior
(grounded in the skyrim-dialog-system reference):

- an INFO fires only if its DIAL has a QNAM to a **running** quest (start-game-enabled, `Start()`ed, or
  auto-started by SetStage);
- a topic is player-reachable via **top-level branch**, **transitive TCLT chains** (choice chains run 5+
  links deep — CGBaurusA→E), **bark subtypes** (HELO/GRET/ATCK/… fire without a menu), or **`Actor.Say(topic)`
  calls in any reachable script**;
- CTDA gates are evaluated statically: `GetStage`/`GetStageDone`/`GetQuestRunning` against the growing
  reached-set, `GetGlobalValue(TES4Unlock_*)` against fired revealer fragments, `GetVMQuestVariable`
  against declared `Conditional` variables in the attached script, and **any FormID param that exists in
  neither the output nor Skyrim.esm marks the condition permanently dead**;
- every Papyrus edge is checked end-to-end: psc generated → pex compiled → fragment function present →
  quest/topic **property actually bound in the VMAD** (an unbound property is None at runtime and the
  call is lost).

Run it with:

```
python tools/quest_walkthrough.py --export export/Oblivion.esm \
    --esm output/Oblivion.esm/Oblivion.esm --scripts output/Oblivion.esm/scripts \
    --seq output/Oblivion.esm/seq/Oblivion.seq --md temp/quest_audit_raw.md
```

`--quest <EditorID>` audits one quest; the `--md` report lists every issue with the exact blocking record.

## Headline result

| | count |
|---|---|
| Quests audited | 390 |
| Completable as converted | **375** |
| Broken (cannot finish) | 2 — SE09, MS14 |
| Degraded (main path OK, stages/side content lost) | 13 |

Every one of the 15 problem quests traces to one of **seven root-cause bugs — six of which are fixed on
this branch** — plus two known conversion gaps that need design work. The full machine-generated
per-quest list is `temp/quest_audit_raw.md`.

## Bugs found and FIXED on this branch

### 1. `End ;comment` silently dropped whole event blocks — the biggest find
[script_convert/converter.py](script_convert/converter.py) `_parse_source` only recognized a bare `end`
line. Shivering Isles scripts end blocks with `End ;OnActivate`, `End GameMode`, etc. — the End line fell
into the block body and the block was **silently discarded** (and a following `Begin` also discarded the
accumulated previous block). 15 scripts were affected, 11 of them quest-critical: the entire OnActivate
of `SE09AddItemsScript` (four `SetStage SE09` calls — **SE09 Ritual of Ascension was uncompletable**),
`SE02OrcCaptainScript` OnDeath, `SEDoorToShiveringIslesScript`, `SE02GatekeeperScript`,
`SERelmynaVerenimScript`, `SEJayredIceVeinsScript`, `SE09AltarScript`, `SE09BodyPartActivatorScript`,
`BejeenScript`, `EyeOfNocturnalScript` (Daedric quest Nocturnal), `SE04FelldewScript`.
**Downstream casualties:** SE10 stage 3 and SEObelisks stage 90 are set by SE09's stage-200 result — three
quests healed by one fix. Fix: recognize `End` + trailing comment/label, close an open block when a new
`Begin` starts, keep an unterminated final block.

### 2. Reserved-EditorID properties never bound in VMAD (MS14 uncompletable)
`_safe_property_name` renames a quest EditorID that collides with a vanilla Skyrim script (`MS14` →
`myMS14`) in the generated Papyrus, but the VMAD binders looked the **sanitized** name up as an EditorID,
found nothing, and silently skipped the binding → `myMS14` was None at runtime and every
`myMS14.SetStage(...)` in 8 dialogue fragments plus the attached scripts did nothing. **MS14 (Nothing You
Can Possess) was uncompletable.** Fix: `resolve_property_formid()` in
[script_convert/constants.py](script_convert/constants.py) reverses the `my` rename on lookup miss; used by
both the INFO-fragment binder ([tes5_import/dialog_converter.py](tes5_import/dialog_converter.py)) and the
object-script binder ([tes5_import/object_scripts.py](tes5_import/object_scripts.py)). Verified: TIF props
now bind `myMS14 → 01017606`, and SE09AddItemsScript's props now include `SE09` + all activator refs.

### 3. `StartConversation target topic` discarded the topic (`Say(None)`)
Scripted NPC↔NPC conversations are how several quests advance: Bejeen/WeebamNa's talk sets DANocturnal
stage 48, the Jauffre/Martin council sets MQ12 stage 26, the Llevana scene sets MS10 stage 79, Kaneh/Mirel
sets SE06 stage 30, plus SE05/SE11 scenes. The converter emitted `ref.Say(None)` — topic gone, result
fragment never fires. Fix: route through the SayTo path — `ref.Say(TopicProp)` with the Topic property
registered for VMAD binding (verified: `BejeenREF.Say(DANocturnalConvo1)` with bound Topic props).

### 4. LIGH records never got their object-script VMAD
`convert_LIGH` was the only script-capable converter that hand-rolls its header and never spliced
`get_object_vmad()` — so `SE06FlameOfAgnonSCRIPT` (sets SE06 stages 9/190, the Flame of Agnon mechanic)
was converted+compiled but attached to nothing. Fixed in
[tes5_import/record_types/items.py](tes5_import/record_types/items.py); all other types go through
`_common_header_subs`, which splices it.

### 5. QUST VMAD declared fragments the .psc doesn't define
The importer's fragment filter counted a whitespace-only (`"\r\n"`) stage result script; the psc
generator's filter (`script.strip()`) didn't. `TES4_QF_E3` and `TES4_QF_SEObelisks` VMADs referenced a
`Fragment_Stage_0100_Item_0` that doesn't exist. Fixed by aligning the importer filter
([tes5_import/dialog_converter.py](tes5_import/dialog_converter.py) `_quest_stage_fragments`).

### 6. Inherited bark gate dead-ended conversation-revealed choice topics (SE36 froze)
The bark-choice promotion stamps the revealing greeting's timing gate onto the choice topic's INFOs. SE36's
"story" choice is offered **ungated from a conversation topic** and *also* from a `GetStage==15` reminder
greeting — the inherited `GetStage(SE36)==15` gate made the line unspeakable on the conversation path, and
stage 15 is set *by that line*: the quest froze at the start. SE02's stage-60/80 `GetStageDone` self-gates
are the same family. Fix: a choice target that also has a non-bark reveal path is no longer
promoted-and-gated; its TCLT conversation link (Oblivion's own shape) stays authoritative.

### 7. Compile fixes surfaced by restoring the dropped blocks
`GetForceSneak`/`GetKnockedState` had no mapping (now `IsSneaking`/`IsBleedingOut`), and the Say-duration
approximation assigned `0.0` to an Int (TES4 `short`) variable. **All 11030 scripts now compile
(11030/11030, 0 TODO regressions — 2 `;TODO` markers total, same as before).**

## Remaining conversion gaps (need design, not quick fixes)

### A. Scripted magic effects (SEFF) are never attached — 4 quests degraded
TES4 script-effect spells/poisons/ingredients (`SCHR.Type=0x100`, referenced by `ScriptEffect[i].FormID`
on SPEL/ENCH/INGR) are converted to `extends ActiveMagicEffect` psc and compile — but **nothing in the
output references them**: there's no carrier MGEF, so the script never runs when the effect applies.
Casualties found by the walkthrough: **SE04** stage 40 (Felldew withdrawal), **MS47** stages 40–60
(reverse-invisibility counterspell + its AddTopic unlock chain), **MS40** stage 60 (dagger blessing),
**FGD08** stage 40 (the Hist-sap potion). Suggested design: for each SEFF script generate one MGEF
(archetype Script, matching casting/delivery), attach the AME script via MGEF VMAD (MGEF supports VMAD),
and splice that effect into the converted SPEL/ENCH/INGR effect lists — the condition-side polyfill
(`HasMagicEffectByID`) already exists, this is the effect-side counterpart.

### B. MenuMode blocks are commented out (by design) — MS05 degraded
The converter deliberately preserves `Begin MenuMode` bodies as comments (Papyrus has no per-menu event;
naive conversion caused the "MQ01 starts-then-fails" bug). Collateral: **MS05 (Through a Nightmare,
Darkly)** — entering the Dreamworld happens in a MenuMode block gated on `IsPCSleeping` (sleep menu +
teleport + `setstage MS05 50`). Suggested design: MenuMode blocks whose body is gated on `IsPCSleeping`
map cleanly onto an `OnSleepStart`/`RegisterForSleep` handler (player-alias quest script or TES4Polyfill).

### C. Not a regression: MS14 stage 200
`MS14TivelaScript` is attached to nothing **in Oblivion.esm itself** (orphaned SCPT) — its stage-200 edge
never ran in the original game either. The baseline now ignores orphaned scripts, so only real
regressions are reported.

## Cross-cutting checks that came back clean

- **SEQ file** contains every start-game-enabled quest (no "dialogue never initializes" cases).
- **No dangling CTDA FormID params** in quest dialogue (the CK "Unable to find TESForm" class) — the
  RACE_MAP/condition-translation work is holding.
- **Journal quests all have QOBJ objectives** and their stage fragments call
  SetObjectiveDisplayed/Completed.
- **Papyrus coverage:** every generated .psc has a compiled .pex (after fix 7); every VMAD fragment name
  resolves to a psc function (after fix 5); the AddTopic unlock-gate invariant holds — every
  `TES4Unlock_*` gate reachable in the walkthrough has a firing revealer.
- The infamous flaky parallel-compile failures (7 scripts, no error text) are just papyrus.exe races —
  they compile clean individually.

## Verification status / how to re-check

Scripts-side fixes are verified end-to-end (scripts phase re-run in this worktree: fragment bodies,
compile 11030/11030). ESM-side fixes (2, 4, 5, 6 — VMAD/record changes) are verified at unit level; the
full import wasn't re-run here (the navmesh phase crashed its worker pool in this worktree and per your
call I didn't chase it). **After the next full `python convert.py -f Oblivion.esm`, re-run the walkthrough
tool** — expected outcome: SE09, MS14, SE02, SE06, SE10, SE36, SEObelisks, DANocturnal, MQ12, MS10 all
clear; remaining flags should be exactly the SEFF quests (SE04/MS47/MS40/FGD08) and MS05 until gaps A/B
are designed.

## Caveats on emulator fidelity

The emulator is optimistic where the engine is dynamic: unknowable runtime conditions (distance checks,
faction ranks, random rolls, ref-walking script logic inside `if` bodies) are assumed satisfiable, so it
can miss a break hidden behind unconverted *conditional logic inside* a fragment body (only lost/unbound
*calls* are detected there). It does not model packages/scenes as advancement sources (TES4 quests advance
via dialogue/scripts, so coverage is high), and voice-file presence is out of scope (silent lines still
advance quests; `tools/voice_audit.py` covers that).
