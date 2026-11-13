# PACK / AI Package & CTDA Engine Contracts

Linked from [CLAUDE.md](../../CLAUDE.md). Verified engine behavior for converted AI
packages and condition items. Implemented in `tes5_import/packages/converter.py`,
`pack_templates.py`, `packages.py`, and `dialog_conditions`. For the original
design analysis see [package_conversion_plan.md](../commentary/tes5_import_package.md)
(note: its "PACK is in SKIP_TYPES" status header is stale — PACK is converted).
For measured OPEN gaps and the list of things verified correct (so they are not
re-litigated), see [package_conversion.md](../audits/package_conversion.md).

## CTDA parameter remapping — the crash rule

**Only a CTDA param that is actually a FormID may be load-order remapped.**
Most condition functions take a plain integer or enum, and Skyrim uses several of
them as a RAW ARRAY INDEX — so a remapped value is an out-of-bounds READ, not
merely a dangling reference.

`GetBaseActorValue(Speechcraft=32)` remapped to `0x01000020` indexed 16.7M
entries past the actor-value table and crashed the game
(EXCEPTION_ACCESS_VIOLATION, `mov rcx,[rax+rcx*8+8]`, MenuTopicManager on the
stack) the instant any converted NPC was spoken to.

The param-type table is GENERATED from xEdit's `wbConditionFunctions` array,
never hand-written:

```bash
python tools/generators/gen_ctda_param_types.py <path>/wbDefinitionsTES5.pas \
    -o tes5_import/generated/ctda_param_types.py
python tools/generators/gen_ctda_param_types.py <path>/wbDefinitionsTES5.pas --func N
```

Everything before `ptActor` in xEdit's `TConditionParameterType` enum is a value,
not a FormID. **Gate on the POST-`_FUNC_REMAP` (TES5) index** — that is the
function the output file actually invokes, and 7 indices were reused between
games with different param types. This corrupted 257 params in Nehrim (164
`GetBaseActorValue` crashers plus silent never-pass gates: 46 `GetStageDone`
stage numbers, 31 `GetIsSex`/`GetPCIsSex` enums, `GetIsUsedItemType`, `MenuMode`).

Related: condition params below `0x100` are engine-fixed (Player is `0x7`) and
are never remapped.

### RunOn targeting

Run-on-Target CTDAs in `Say()`-driven topics can never pass (Say has no dialogue
target) — the importer retargets them to RunOn=Reference (unique script target,
usually PlayerRef) or drops them (mixed targets); see
[dialogue_conversion_notes.md](../commentary/tes5_import_dialogue.md) 2026-07-19.

**IDENTITY functions are exempt** (GetIsID / GetIsRace / GetIsSex / GetIsClass /
GetInFaction / GetFactionRank): they ask WHO is being addressed, so retargeting
inverts their meaning — and against PlayerRef it makes them UNPASSABLE (GetIsID
compares the BASE form; PlayerRef's base is vanilla `0x00000007`, not the
converted `0x01000007`). The blanket version broke 667 INFOs / 101 topics and
stripped whole NPCs' topic lists.

## PTDA slot 3 is Distance, not Count

**TES4 `PTDT.Count` must NEVER be copied into TES5 `PTDA`'s third field.** That
slot is "Count / **Distance**" (xEdit wbDefinitionsTES5.pas ~8665) and for a
Specific-Reference target the engine reads it as the distance the actor must be
within to act on the target.

`CGRenoteOpenSecretDoor` carries `PTDT.Count=1`, which became "activate this
switch only from within 1 unit" — unreachable, so at CharacterGen stage 18
Renault took the package for a single frame at dist=120, failed it, and fell back
to `CGRenoteWalkToMarkerB` (`GetStage >= 15`, still true at 18): she stood at the
switch forever and the secret door never opened.

Skyrim never uses the field — **ALL 3,740 PTDA records** in Skyrim.esm +
Dawnguard + Dragonborn + HearthFires + Update are 0, across every target type.
**Write 0.**

## Ambush is an approach, not an attack

**TES4 "Ambush" (PKDT.Type 9) is usually NOT hostile — it is Oblivion's
scripted-APPROACH idiom.** The type means "wait for the TARGET to come near, then
act on it", and 64 of Oblivion's 80 Ambush packages target the PLAYER with names
like `SE02HaskillGreetPlayer` / `CGEmperorGreetPlayerInCell` /
`...ForceGreetPlayer`.

Converting them all to `HoldPosition` + `T5_WEAPON_DRAWN|NO_COMBAT_ALERT|
ALWAYS_SNEAK` dropped `PTDT` entirely, so the actor stood still with a weapon out
and never approached or spoke — Uriel at the prison cell, stalling CharacterGen
at stage 17.

A player-targeted Ambush now converts to the **Follow** template (target +
PLDT.Radius as the stopping distance) with the weapon/sneak flags SUPPRESSED;
targetless ambushes keep HoldPosition. The `AmbushPlayer`-named ones (Staada,
Thadon, Ruma) are also scripted confrontations that TALK first, so the
player-target test is the right discriminator, not the EditorID.

## Find at an ACTOR is a seek → Travel near-ref (2026-08-03)

TES4 `Find` (Type 0) splits three ways by what its `PTDT.Target` is:

| Target | Conversion |
|---|---|
| the player | ForceGreet (73 packages — `...FindPC`, `...ForceGreetPlayer...`) |
| an ACTI/DOOR/CONT ref | Activate — go operate it (24 packages) |
| a placed ACTOR (ACHR/ACRE) | **Travel, location = near-reference (alias-routed), radius = `PTDT.Count`** (232 packages) |

The actor case previously fell into the Sandbox fallback, and with no PLDT
most of these packages sandboxed **at the actor's own editor location** — i.e.
stand still.  That was `CGAssassinsAmbushAToGlenroy/Baurus/Renote`: at
CharacterGen stage ≥ 24 the ambush assassins each Find a Blade (distance 200),
which is what carries them out of the ambush room, through its teleport door
and off the mezzanine drop into the fight — they stood in the room forever.
The same idiom covers every `...VisitX` / `...TalkToY` schedule package and
all the guard-post reliefs.  `PTDT.Count` on a Find is the approach DISTANCE
(see the PTDA census note in `pack_converter.build_target`), so it becomes the
location radius.  `ref_base_sig` now maps ACHR→`NPC_` / ACRE→`CREA` so the
branch can recognise a placed actor.

## Defensive Combat is NOT Ignore Combat (fixed 2026-07-31)

**TES4 `Defensive Combat` and TES5 `Ignore Combat` occupy the same bit (20) and
mean opposite things.** xEdit `wbPackageFlags` (`wbDefinitionsCommon.pas:7635`)
spells it out: bit 20 is `Armor Unequipped` in TES4 / `Ignore Combat` in TES5,
while TES4's Defensive Combat is bit 22.

| Flag | Meaning |
|---|---|
| TES4 Defensive Combat | Do not **start** fights — but **do fight back** |
| TES5 Ignore Combat | Take **no part in combat at all** |

Mapping one onto the other told every Oblivion bodyguard to stand still and be
killed. This was CharacterGen's ambush: `CGGlenroyDefendEmperorAmbushA` — the
package whose entire job is defending the Emperor — carries Defensive Combat, so
the converted Blades drew their swords, then watched the assassin kill Renault
and attack the others without ever striking back. All four packages the Blades
run during the ambush had the flag (`DefendEmperorAmbushA`, `BladesWaitToMove`,
`ToMarkerF`, `AccompanyEmperorToC`).

Both sources describe the two behaviours precisely. UESP's Oblivion *The Killing
Field* talk page names the TES4 flag's own symptom — *"the brothers won't attack
the goblins unless provoked… one just stands there… remove [defensive combat]"* —
and the TES5 flag is what the "Horses Ignore Combat" mod uses to make a horse a
passive bystander (*"everything else still attacks the horse"*).

**Skyrim has no Defensive Combat equivalent and needs none** (Skyrim Mod:Mod File
Format/PACK lists no such flag): the aggression tier already decides whether an
actor initiates, and every actor retaliates when attacked. **TES5's default IS
TES4's Defensive Combat**, so the correct conversion is to DROP the bit. Setting
Ignore Combat is actively harmful — vanilla reserves it (576/5,961 packages) for
actors who must stay OUT of a scripted fight: horses, the MQ101 stand-still
archers, `CWFinaleEnemyLeaderWaitForExecution`, `pelagiusHoldPosSleepIgnoreCombat`
— never for a bodyguard. 388 of 7,209 TES4 packages set the TES4 bit, so this
suppressed combat well beyond CharacterGen; the converted plugin now writes
Ignore Combat on **zero** packages.

Related: the guards also needed the scripted-aggression fix (see
[papyrus_conversion_notes.md](../commentary/script_convert.md), "Aggression must not
collapse 6..105 onto tier 2"). Both were required — aggression governs whether
they pick a target, this flag governs whether they may fight at all.

## Force greet is a package, not a Papyrus call

**Skyrim has NO Papyrus "walk over and talk to the player" call — a FORCE GREET
is a PACKAGE whose Topic data input (`ANAM=Topic` + `PDTO` → a DIAL) names the
dialogue to open.** 228 vanilla packages use the `ForceGreet` template
`0003C1C4`. Without that input the actor approaches and then just stands there
(Uriel idling 211 units from the player, `IsInDialogueWithPlayer()==False`, quest
stalled at stage 17 with controls disabled). Converting a player-targeted TES4
Ambush to Follow/HoldPosition is NOT enough for this reason.

The greeting topics are built per quest in Phase 5, long after PACK is written in
Phase 3b2, so conversion leaves a 0 placeholder and `patch_forcegreet_topics()`
binds it afterwards (54 packages). Only 120/7,209 packages are quest-OWNED, so
the quest is taken from the package's own conditions
(`GetStage`/`GetQuestVariable` param1) when there is no owner.

### A force greet must be able to RETIRE

**Once Per Day (0x400) is a force greet's only retire mechanism when the
greeting advances no stage — so it is RESTORED on force greets even though
`convert_flags` strips it everywhere else.**

The general strip is correct and must stay: on a quest-gated package the
`GetStage` condition already scopes when it may run, and the daily latch
actively breaks it (a persistent actor loaded since game start counts as having
used the package today). That is the Renault `CGRenoteOpenSecretDoor`
regression — see the comment in `convert_flags`.

A force greet is the exception, because its condition is often unbounded. The
CharacterGen stage-56 stall:

- `CGBaurusGreetPlayer` (TES4 Find → player, flags 5124 = Must Complete + Once
  Per Day) hands the player a torch. Its ONLY condition is
  `GetStage(CharacterGen) >= 50` — nothing ever falsifies it.
- With the latch stripped it re-qualified forever, so **Baurus greeted, ended
  dialogue, immediately re-greeted**, and never advanced to
  `CGBaurusFollowEmperorToF/ToG`.
- Stage 56 requires *both* Baurus and the Emperor in `ImperialDungeon03`
  (`BaurusRef.getincell ImperialDungeon03 == 1 && UrielSeptimRef.getincell
  ImperialDungeon03`), so only Glenroy arrived and the quest stopped at 56.

Symptom to recognise: an NPC who **repeatedly force-greets the player and stops
moving** is a force greet that cannot retire.

Vanilla-legal at scale — census of Skyrim.esm's ForceGreet-template
(`0003C1C4`) packages:

```
302 ForceGreet-template packages (PKCU template 0003C1C4)
 55 carry Once Per Day (0x400)     <- incl. quest-gated ones:
                                      MQ203DelphineRiverwoodSceneForceGreet
                                      MG01FaraldaBridgeForcegreet
                                      DA07SilusForcegreetPiecesPackage
 38 carry Must Complete (0x4)
 16 carry both
```

Reproduce: scan `references/Skyrim.esm/PACK.txt` for records whose `PKCU.hex`
holds template `C4C10300` (little-endian `0003C1C4`) and unpack `PKDT.hex[0:4]`
as the flags u32.

Guarded by `tests/test_dialog.py::TestForceGreetOncePerDay` — one test that the
force greet keeps the latch, one that ordinary quest-gated packages still lose
it (the Renault fix must survive).

### <a id="force-greet-pkdt"></a>Force greet PKDT: vanilla's speed and interrupts, the source's flags

A converted force greet takes vanilla's speed 2 (run) and interrupt flags
`0xFEFF`, copied from `MS05InductionForcegreet`. The interrupts are the point:
they AUTHORISE the actor to break off the package to speak. The global default
is `0x0000` (every interrupt denied, right for ordinary packages), and with
that a force greet never opens dialogue however close the actor gets. The
converted TES4 flags are kept alongside: this used to write 0, and a force
greet with no flags never retires; Once Per Day (0x400) and Must Complete
(0x4) are what made Oblivion's one-shot greetings fire once and stop. Once Per
Day is RESTORED here after `convert_flags` strips it from quest-gated
packages (the Renault regression), because on a force greet whose only gate
is unbounded (`CGBaurusGreetPlayer`, `GetStage >= 50`) the latch is the only
retire mechanism — see the section above.

## Template data inputs

The engine SKIPS inline ANAM data inputs when `PKCU.Template != 0`. See
`GetPackageData` direct-vs-UNAM modes.

## evp is a request, not a switch

TES4 `evp` re-evaluated the package stack IMMEDIATELY; Skyrim's
`Actor.EvaluatePackage()` only requests it, and the previous package "will
continue to run for a random amount of time" (CK wiki). It also never restarts a
package that re-wins arbitration (a dead Escort stays dead).

Oblivion routinely stacks an OPEN-ENDED package just below a stage-gated one
(`CGEmperorGreetPlayerInCell` `GetStage == 17` above `CGEmperorFollowRenote`
`GetStage >= 16`), so when the stage fires the actor is mid-Follow until
arbitration lands: a force-greeted actor turns, walks away for about a second,
then turns back. Census: 49 open-ended packages (48 Oblivion, 1 Nehrim) sit below
a higher-priority `GetStage == N` gate, so this is a class, not a CharacterGen
quirk. `evp` emits a plain `{ref}.EvaluatePackage()` and the converter does not
bound the open-ended packages' conditions: they convert as authored and are
vanilla-legal.

Verified NOT at fault: package conditions incl. operators, alias ALPC order (exact
TES4 order), force-greet on vanilla's `ForceGreet` template `0x0003C1C4` with
UNAM/PDTO and an ANAM layout matching vanilla's 125-instance variant,
`MustComplete|OncePerDay`, deep alias stacks (vanilla goes to 10).

`SetLookAt` does not survive a package switch (the new package installs its own
headtracking), so the converter moves a look issued before an `evp` on the same
actor to AFTER the evaluation.

## Overlapping package conditions are not the bug

**When two packages' conditions OVERLAP (`== 18` vs `>= 15`), the overlap is not
the bug.** Oblivion resolves by AI-package-list ORDER (first passing package
wins) and the converter does preserve PKID order, so look for why the
higher-priority package was REJECTED. The log signature is a package appearing
for ONE frame followed by a `PKGEND` for the previous one.

Verified vanilla-legal and NOT causes — **don't "fix" them**:
- PKDT has no priority field.
- `Once Per Day` (0x400) is set on vanilla's own `TG08AKarliahOpenGatePackage`.
- Interrupt `0xFFFF` is attested (`CWEscapeCitySceneActivateDoor`, 1,372 vanilla
  packages).

## Horses

Converted Escort/Follow/Travel packages must keep "Ride Horse?"=0 unless the TES4
package set Use-Horse (0x00800000) — `ride_horse=1` on a horseless NPC freezes
them in place (Pinarus/FGC01Rats).

## Quest priority

`QUST.DNAM.Priority` carries the authored TES4 value clamped to the engine's
**0-100 band** (not a full U8). A staged boost overflowed to 161 on 68% of
quests and broke ALIAS-PACKAGE arbitration. Arbitration reads
`QUST.DNAM.Priority`, never a bark topic's `PNAM`.
