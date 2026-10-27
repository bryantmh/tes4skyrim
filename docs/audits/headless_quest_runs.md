# Headless quest runs: fixes and open findings

**Date:** 2026-10-26. **Runs covered:** the MWScript opcode sweep, the Morrowind.esm main quest
(new game to Azura's ending), and the Arktwend main quest from New Game to Arkt's defeat
(`final_arkt`): 27 quests in 191 cases, passed in one continuous run of 22 minutes.

**Status:** every change below is uncommitted. None has been tested in the user's own game.
"Passed headless" means the quest case that found the bug passed on the test instance after the
fix. Cases live in `tes_runtime/morrowind/testdata/quests/<run>/`. The tool is described in
[headless_testing.md](../commentary/headless_testing.md).

Rerun one stretch with:

```bash
python -m tools.live.test_instance build --mod Arktwend --mod TESGameSelect
python -m tools.live.test_instance mwtest --cases tes_runtime/morrowind/testdata/quests/arktwend_main \
    --fast --checkpoints --stop-on-fail --keep --from <case> --timeout 3000
```

---

## 1. The test tool itself

None of these is a converter bug. They are listed because they are part of the same uncommitted
change.

| Piece | Files |
|---|---|
| Hard-linked test instance in `C:\Other Games\Skyrim Test`, hidden desktop, muted, own Documents and saves | `tools/live/test_instance.py`, `tools/live/hidden_launch.py`, `game_bridge/sandbox/` (TESSandbox.dll) |
| Bridge fixes needed headless: console with no captured context, MemAlloc needs the MemoryManager, IsGameLoaded probed on the main thread, `TES_BRIDGE_PIPE` | `game_bridge/plugin/*` |
| Case runner inside MorrowindRuntime. Steps: run, wait, expect, until, talk, ask, choose, bye, button, activate, pick, close, take, console, persuade, sleep. Checkpoints and `--from` resume | `test_runner.h/.cpp`, `conversation.cpp` (test API) |
| Real New Game through TESGameSelect (button index = game id) | `test_instance.py` |
| Speed: `iFPSClamp=6` gives about 11×. The runtime ticks once per 30 Hz wake with the real elapsed game time | `object_tick.cpp`, `game_calls_query.cpp` (frame clock) |
| Checkpoint saves wait until the `.ess` stops changing. Acknowledging a half-written save deadlocked and crashed the game | `test_instance.py` |
| `morrowind_quest_trace.py --detail` prints each stage's speaker, conditions and result script | `tools/dialog/morrowind_quest_trace.py` |

---

## 2. Fixes, by the run that found them

### 2.1 MWScript opcode sweep (`testdata/ingame/`, 186 cases)

| Fix | Why | Where | Blast radius |
|---|---|---|---|
| `ModHealth` (and kin) moves base and current; `ModCurrentHealth` moves current only | Matches OpenMW | `script_ops_world.cpp`, `game_calls.cpp` | `mastriusScript` vampire curse |
| Integer stat writes go through `ModActorValue` | `setav` did not take on some values (damage resist) | `script_ops_stats.cpp` | All stat writes |
| `SetMagicEffect` sets the total; `ModMagicEffect` adds | Matches OpenMW | runtime | 0 vanilla uses |
| `GetScale` reads the reference's own scale (refScale at +0x98 on 1.6.1170) | Papyrus `GetScale` includes the race height and lags `SetScale` | `game_calls_move.cpp`, `ids.h` | 0 vanilla uses |
| `Position`/`SetPos` move with one `MoveTo` | Papyrus `SetAngle` after `SetPosition` reverted the move | `game_calls_move.cpp` | 5 uses in 4 scripts |
| Clock globals (`GameHour` etc.) write through to Skyrim's | Script writes did not move the clock | `game_calls_query.cpp` | — |
| `SetCrimeGold`/`PayFine` run on the game thread at once | Posted calls landed after the script read them back | `game_calls_crime.cpp` | — |
| `SetHealth 0` / draining to 0 kills | Skyrim's `SetActorValue` never kills; OpenMW does | `game_calls.cpp` `KillIfDrained` | 12 uses (Dagoth Ur, girasScript, …) |
| Filled soul gem ids resolve as items | `AddSoulGem`/`DropSoulGem` found nothing | `script_tables.cpp` | 0 vanilla uses |
| **Converter:** a reference named in a script ships persistent, and exterior persistent refs move into the world's persistent cell | TES3 finds any ref anywhere; Skyrim reaches only persistent refs in unloaded cells | `world_morrowind.py`, `pipeline_records.py`, `indexes.py` | 787 placements in Morrowind.esm; Oblivion, Nehrim, FNV unaffected |

### 2.2 Morrowind.esm main quest (`testdata/quests/morrowind_main/`)

| Quest / case | Fix | Why | Where | Blast radius |
|---|---|---|---|---|
| Chargen | `^ActionForward`-style key names print the player's real Skyrim binding | Printed blank | `menu.cpp` `BoundKeyName`, `script_context.cpp` | Chargen tips |
| Chargen | 🛑 AI travel "arrives" ignoring height | The guard's target was 240 units under the deck. **Pinned: the user doubts this is a bug** ("he takes a long time to arrive") | `game_calls_ai.cpp` `HorizontalApart` | All AiTravel/AiEscort |
| Chargen | `%PCName`/`%PCRace`/`%PCClass` filled in books | Release papers showed the raw codes | `game_calls_message.cpp` (book hook, 3 call sites) | Morrowind books with `%` |
| Chargen | Player race, class and sex wired to conditions and text | Read nothing | `game_actor.cpp`, `script_context.cpp` | 109 SameRace, 110 SameSex, 77 PcGender, 117 responses |
| Chargen | `Xbox` ported (returns 0) | Unported | `script_ops_control.cpp` | — |
| A1_7 Huleeya | The AI quest is no longer started at the main menu | Followers lost their packages after a relaunch | `game_calls_ai.cpp` `AiQuestRecord` | Every follower/AI-package actor |
| A1_11 Zainsubani | A declaration-only script counts as compiled | Logged "will not compile" (codusScript, nolore, strongholdWives) | `script_runner.cpp` | Empty-body scripts |
| A2_2 Dagoth Gares | Creatures are in the actor table; `IsNpc` false for them; a creature with no greeting stays mute | Dagoth Gares never talked | `morrowind_sidecar_source.py`, `morrowind_sidecar.py`, `game_actor.cpp`, `activation.cpp` | 35 talking creatures (vivec_god, dagoth_ur_1/2, yagrum bagarn, …) |
| A2_4 Milo | Global scripts see `CellChanged` | MoveMehra never fired | `object_script.cpp`, `object_tick.cpp` | 4 global scripts |
| A2_6 Incarnate | `Position` on the player from an interior goes to the exterior (`Wilderness` cell row) | Did nothing | `morrowind_sidecar.py`, `game_calls_move.cpp` | — |
| A2_6 Incarnate | A load door's deferred second activation is not claimed | Cavern door looped | `activation.cpp` `ScriptPass` | Scripted load doors |
| A2_6 Incarnate | Activation hooked on every item type (16 vtables) | Moon-and-Star never activated | `ids.h` `kActivateTargets` | 15 objects (Moon-and-Star, ring of Azura, guild rings, cursed gems) |
| B2 Ahemmusa | **Exporter:** a door's partner is searched in the destination grid and its 8 neighbors | Doors across a grid border had no XTEL | `tes4_export/export_morrowind.py` | Unlinked doors 27 → 2; no FormID drift |
| B2 Ahemmusa | **Navmesh:** a door the corridor never reaches is routed to the pathgrid over collision; door-axis cache fixed (pyffi monkey patch at module scope, `RootCollisionNode` skipped, attached-shape cluster) | Sinnammu couldn't path out of the yurt (no XNDP) | `navmesh/door_routes.py` (new), `corridor.py`, `corridor_doors.py`, `spawn_nodes.py`, `collision/door_geometry.py` (new), `collision_extract.py` | Morrowind unlinked teleport doors 240 → 105; door axes 0 → 81 of 91. Oblivion: 4 models change (2 correct now, 2 gates wrong either way, accepted). `DOOR_AXIS_SCHEMA_VERSION` 3 |
| B5 Redoran | A reference bound after a cell change sees `CellChanged` on its first run | Bolvyn Venim never came to the arena duel | `object_tick.cpp` `DiscoverLoaded` | 161 Morrowind.esm scripts (all sampled are arrival checks) |
| C3 Dagoth Ur | `HitOnMe` ported and `OnPCHitMe` raised from Skyrim's hit event | The Heart of Lorkhan could not be destroyed. The hook is off on 1.5.97 and VR | `game_calls_query.cpp`, `script_ops_query.cpp`, `ids.h` | — |
| C3 Dagoth Ur | **Converter:** a non-biped creature with no move flags gets Skyrim's Immobile race flag | The heart slid 450 units out of reach | `tes5_import/actors/creature_races.py` | heart_akulakhan, kwama queens (MW, Tamriel_Data, TR), 2 TR dummies, 1 unplaced Nehrim goblin |

### 2.3 Arktwend main quest (`testdata/quests/arktwend_main/`)

| Quest / case | Fix | Why | Where | Blast radius |
|---|---|---|---|---|
| Chargen statue | `MenuMode` counts a menu asked to open | The script raced through race, class and sign; the sign menu never opened | `menu.cpp` `NoteMenuOpening`, `game_calls.cpp` | CharGenRaceNPC/ClassNPC (MW, patch), ANFKlosterChargenStatue |
| A_II Galeone rowboat | Player activation waits for the object's script to have run once since it loaded | The activation came before the script could claim it | `game_calls.cpp` `PlayerActivate` | — |
| A_IV Herbert | `Enable`/`Disable` answer at once | A posted `Enable` made `ForceGreeting` see a disabled actor | `game_calls.cpp` (asked-enabled state) | Enable+ForceGreeting: MW 3, Ark 10, TR 32 |
| A_IV Herbert (Gron) | **Converter + runtime:** exterior cell names table; `GetPCCell` outdoors uses it | Skyrim drops exterior FULL, so Herbert's farewell never matched | `morrowind_travel.py`, `morrowind_sidecar*.py`, `morrowind_placements.py`, `script_tables.cpp`, `game_calls.cpp` | All exterior cell-name conditions |
| Stormwend (0600) | Menus opened by a script wait for the player's move to land | A force-greet after a move closed at once | `menu.cpp` `HoldMenusForPlayerMove`, `game_calls_move.cpp` | — |
| Stormwend (0600) | AI package markers spawn after the posted move; a still-wander anchor follows its actor | Markus walked home | `game_calls_ai.cpp` | Move-then-AI: TR 614, patch 21, Ark 9, MW 2. AiWander 0: TR 712, Ark 21, MW 18 |
| Stormwend pass (0710) | A travel/escort package is released when the actor is moved out of range | Bernward walked out of his shop | `game_calls_ai.cpp` `AiAfterScriptMove` | Same sites. **The release has not been seen firing** |
| Stormwend (0600–0710) | `EnableLevelUpMenu` fills the level progress so the dialog opens | It never opened | `script_ops_control.cpp`, `game_calls_attributes.cpp` | — |
| Thieves' Guild port (0720) | `OnPCAdd` raised when the player gains the item | Never set | `object_script.cpp` `NoteCarried`, `script_tables.cpp` | Add-only scripts: TR 34, Sky_Main 31, patch 3, Tamriel_Data 2, MW 2 (chargen papers/ring now show their "Ok" boxes), Bloodmoon 1, Ark 1 |
| Thieves' Guild port (0720) | **Converter:** sound ids decoded with the plugin's code page | 24 Arktwend sounds with non-ASCII letters were missing | `morrowind_sidecar.py` `_converted_sounds` | No other sidecar affected |
| Tate's bridge (0730) | `SetHealth` also restores damage | The shadows died although their script sets 500 every frame | `game_calls.cpp` `SetDynamicStat` | MW 11, Ark 3, TR 146 |
| Tate's bridge (0730) | `StartCombat` between two non-enemies sets archnemesis for the fight and puts it back after | Skyrim drops a fight between guild brothers within seconds | `game_calls_combat.cpp` (new) | ~430 NPC-vs-NPC sites in TR. See §3 caveat |
| Tate's bridge (0730) | `OnDeath`/`OnActivate` stay set until the script reads them | Tate's script returns early 20 of every 21 ticks, above its `if ( OnDeath )`. Matches OpenMW | `object_script.cpp/.h` `EndTick` | Reads below a `return`: MW 68+79, Sky_Main 20+58, Ark 21+69 |
| Tate's bridge (0730) | A dead actor's `GetCurrentAIPackage` is -1 | Skyrim keeps a corpse's follow package. Matches OpenMW | `game_calls_ai.cpp`, shared `IsDead` | Death checks under a package test |
| Tate's bridge (0730) | An actor a script moves re-evaluates its AI package | The old package kept steering it back | `game_calls_ai.cpp` `AiAfterScriptMove` (`Actor.EvaluatePackage`) | Every script move of an NPC |
| Chapter V–VIII | **Converter:** statics go in the reference table | A script that disables or moves a static (`id->Disable`) found nothing | `morrowind_placements.py` `PLACED_EXPORTS` | Every sidecar. **TR, Tamriel_Data, Bloodmoon, Morrowind and the patch not re-imported yet** |
| Almasanoar (0837) | An NPC's hold release runs on the main thread after the script's move, not before | The release ran ahead of `Position`, and its glide pulled the ship's captain back to where he had stood | `npc_anim.cpp` `ReleaseNpcHold`; `script_ops_move.cpp`, `script_ops_world.cpp` call it after the move | `Position`, `PositionCell`, `SetPos` on NPCs |
| The Void (0850–0860) | An object in an interior the player has left is not in the world | Skyrim keeps the cell's 3D a while, so the Void marker's script ran 652 times in 40 s after the player left | `game_calls.cpp` `InWorld` | Every interior object script |
| Dwarves' war (0860) | A second force-greet while the menu is open restarts the greeting in place | Two greetings in a row desynced the conversation, and the menu went dead. Matches OpenMW's `DialogueWindow::setPtr` | `conversation.cpp` `BeginConversation` | Back-to-back force-greets |
| Stormwend pass (0710, full replay) | The conversation's speaker is kept by FormID, not by pointer | After talking to Judas, a trip to Norat unloaded the inn. Back there, the stale pointer named a Dark Soldier in the freed memory, so `talk JudasBlutacker01` activated the soldier ("fighting the player"). Intermittent; likely also the silent `pass_judas_paid` crash | `game_calls.cpp` `SpeakerRef`, `game_calls_crime.cpp` | Every `id->` naming the last speaker, and the crime faction |
| Verlorener Sohn (0790 `sohn_irfl`, full replay) | **Converter + runtime:** TES3 actors use the 0-100 disposition faction system. Disposition and Fight are faction ranks; Aggression stops at tier 1; an actor OpenMW calls aggressive (Fight + 20 + (50 − disposition) × 0.2 ≥ 100) joins `TES4DispositionHostileFaction`, the player's enemy alone. The runtime writes both ranks and re-runs the rule on every `SetFight` and disposition change. **Exporter:** NPDT disposition is dumped as `ACBS.Disposition` | Tier 2 attacks every neutral NPC: a leveled goblin killed Irfl before his greeting, and Judas joined the Kanzler soldiers' fight. In OpenMW Fight targets only the player and followers (`Actors::engageCombat`) | `disposition.py`, `actor_common.py`, `pipeline.py`, `morrowind_sidecar.py` (`effects_formid.txt` rows), `morrowind_actors.py` (export), `game_calls_ai.cpp` `SetFight`, `game_calls_state.cpp` (`Actor.RemoveFromFaction`, id 54958) | Arktwend: 733 actors hunt the player only (was 640 hunting everyone); 13 passive animals and summons stop hunting (guar, deer, scribs: Fight 50-70). Morrowind before re-export: 966 hunters, 44 stop. TES3 `GetDisposition` conditions now read the faction rank. TES4 unchanged |
| Verlorener Sohn (0790 `sohn_guard`) | **Converter + runtime:** a TES3 Guard-class NPC joins the marker `TES3GuardFaction`; a hostile TES3 creature joins `TES3HostileCreatureFaction`, an Enemy of the player alone. The runtime sets each idle guard on a creature of that faction that is in combat within `fAlarmRadius`. `SetFight` picks the creature faction from the actor table's `creature` flag | OpenMW's guards attack an aggressive creature that is in combat (actors.cpp:733). Snow wolves fought the player beside the Nordrand guards, who never moved. Two faction-only versions started the fight on cell load and killed a guard | `disposition.py`, `owned_records.py` `adopted_or_derived`, `actor_common.py`, `game_calls_ai.cpp` `SetFight`, `game_calls_combat.cpp` `GuardsEngage`. Tests in `test_import.py` | Arktwend: 225 guards, 539 hostile creatures, 194 hostile NPCs (unchanged total of 733). 73 hostile creatures are placed near a guard, all in authored battle scenes |
| Verlorener Sohn (0790 `sohn_guard`) | **Converter:** an autocalc TES3 NPC gets the health, magicka and fatigue OpenMW derives (`autoCalculateAttributes`, `setAttribute`) | The export dumps no pools for a 12-byte NPDT, so all 676 of Arktwend's autocalc NPCs had 50 health. The level-30 Nordrand guard was dead before his greeting | `morrowind_autocalc.py` `autocalc_pools`, `morrowind_sidecar_source.py` `npc_stats`, `npc_morrowind.py` `tes3_pools`, `npc.py` `_pools`. Tests in `test_morrowind_autocalc.py` | 676 of 1,262 Arktwend NPCs. The guard has 274 health, King Olim 694. Every other TES3 plugin needs a re-import |
| Verlorener Sohn (0790 `sohn_guard`) | `ForceGreeting` logs why it refuses (no reference, no actor row, disabled, dead) | It returned silently, which hid the dead guard | `game_calls.cpp` `ForceGreeting` | Log only |
| Dwarves' war (0860, Grul battle) | `StartCombat` on a dead attacker or target does nothing, as in OpenMW (`OpStartCombat`, `startCombat`) | The battle scripts call it every frame on the fallen. Each call began a feud that the next tick ended: about 300,000 log lines and game calls in 10 s | `game_calls_combat.cpp` `SetCombat`, `script_runner.cpp` | Every per-frame `StartCombat`. **Not yet run headless** |
| Case files | A script line after a step opens its own `run` body | The parser silently dropped lines with no `run` in front, so cases skipped their moves (`mord_city`) | `test_runner.cpp` `ParseTestCases`, test in `script_test.cpp` | Test harness only |
| Test tool | `mwtest` stops with `GAME EXITED` when the game dies | A dead game left the runner waiting out its whole timeout | `test_instance.py` `wait_results` | Test harness only |
| Test tool | A failed run copies the runtime log to `_profile/mwtest_failed.log` | The next launch overwrote the only evidence of a failure | `test_instance.py` `keep_failed_log` | Test harness only |
| Test tool | The instance and the package take the chargen classes and birthsigns from the game being installed (`--chargen-source` on the packager) | With no `chargenSource` set, both took Arktwend's lists for a Morrowind-only instance | `test_instance.py` `link_runtime`, `package_runtime_dll.py` | Test harness and packaging |
| Morrowind main quest (rerun 2026-10-27) | **Runtime:** `GetAttacked`, `GetCommonDisease`, `GetBlightDisease` ported. Diseases walk the actor's active effects for a source spell of that TES3 type, as Skyrim's GetDisease condition does; the sidecar's SPEL rows now carry the TES3 SPDT type (the export folds blight into disease) | Unported; a disease or attacked test always read 0 | `script_ops_query.cpp`, `game_calls_spell.cpp`, `game_calls_flight.cpp`, `game_calls_query.cpp`, `morrowind_sidecar.py`, `morrowind_sidecar_source.py`. Tests in `script_test.cpp`, `test_morrowind_spell_kinds.py` | Not yet run in a quest. Needs a re-import of every TES3 plugin |
| Morrowind main quest (rerun 2026-10-27) | Cases: interior moves use `PositionCell`; chargen presses the OnPCAdd tip boxes; `b2_escort` walks the player to the statue | `Position` now moves an indoor player outdoors, as Morrowind.exe and OpenMW do; OnPCAdd now raises the papers' and ring's tips; Sinnammu's `AiFollowCell` ends only near its point (OpenMW `AiFollow::execute`) | `morrowind_main/*.mwtest` | Test cases only. Every case then passed, chargen to Azura, in segments |

With the guard rule in, `sohn_guard` through `final_arkt` (72 cases) passed in one continuous run,
then all 191 cases from New Game.
The guard probe kept the Nordrand guard at 274 health with the player 4,944 units away, and a
guard in the Upper Mines set on a creature already fighting.

Tate's case kept failing for a reason that was not a game bug: only `build.bat test` ran, so the
instance loaded a stale DLL without the `OnDeath` and dead-actor fixes. After a real DLL build it
passed. Always build the DLL itself (`build.bat`, then `build.bat test`).

---

## 3. Found, not fixed

**Arktwend: worked around in the cases, not fixed**

- **Enderal tower marker (0840 `enderal_brother`).** Once `EnderalTurmvar` is 8, the marker in
  EnderalTurm01a moves north with `MoveWorld Y 300` until the player reaches it. Our rate matches
  OpenMW's (rate × frame time), yet the player never catches it. It is unclear how a real player
  does. The case moves the player to the tower top with `Position`.
- **An actor killed in its first seconds never gets `OnDeath`.** Object scripts bind about 6 s
  after the actor loads, and `PollDeath` takes its first sample then. A corpse at that first
  sample is never seen dying. Cases wait for the script to bind before the `damageav`.
- **A message box posted around a checkpoint save may be lost** (suspected, not confirmed). Cases
  press `button 0` before the checkpoint.
- **Mauragin's force-greet while disabled is skipped** (0840 `enderal_pass`). OpenMW returns early
  for a disabled actor too, so this matches. The case does not wait for that greeting.
- **Asartan's journal 2 (0830)** is reachable only on a visit before Tyrat. Nothing reads it.
- **Accepting the Sultan's task sets `Mytrehen` to 10** (0837 `desert_outpost`), so the outpost
  leader attacks instead of greeting. This looks authored; the case kills him.
- **Herbert's road to Gron (0400 `herbert_gron`).** Wolves hunt the player on the road, and
  Herbert's companion script returns while his weapon is drawn, so he never reaches State 7. The
  case kills the five wolves. In OpenMW a Fight-87 wolf engages only within about 1,400 units;
  Skyrim has no such falloff (see the actors commentary).
- **Nordrand guard on a slope (0790 `sohn_guard`).** A player dropped near him can slide out of
  his 400-unit greeting range and into the child's, whose greeting skips the journal ahead. The
  case stands the player on a measured footing just above him.
- **Mao's greeting comes first (0860 `dwarves_mao`).** Mao's script makes Daras greet, ending in
  `Goodbye`; Daras's own script, which returns in menu mode, writes journal 4 only after that.
  The case closes the first greeting. Earlier passes won a race between the two.
- **Checkpoints that do not save** right after a fight's last kill (`storm_dargun`,
  `storm_herbin`, `storm_robert`, `thief_jarko`, `final_shade` and others). The cases pass; only
  a resume from that case is lost. Not investigated.
- **Two proximity scripts that did not fire, once each** (0790). In one run Irfl never called
  `ForceGreeting` (no "force-greets" line in 20 s, the player 120 units away); in the next,
  `ZwergenMissionMadchenSchreck` never wrote journal 6. Every other run of each passed (three
  apiece), including the full stretch from `sohn_guard`. The logs were lost to the next launch, which is why
  failed runs now keep theirs. Cause unknown.

**Morrowind: open**

- **The game exits silently when the level-up menu opens** (`a1_v_levels`), twice in a row without
  a debugger. With cdb attached it did not crash, and the run went on to `b2_escort`. The next
  segment passed its level-ups too. Timing-dependent; no dump yet.
- **`HurtStandingActor`, `GetStandingPC`, `GetStandingActor` are not ported.** Skyrim has no
  standing-on query. CommonLibSSE names `bhkCharacterController::supportBody` at +0x2B0, but no
  function of the controller reads or writes a pointer there on 1.5.97 or 1.6.1170, and +0x2B8
  (CommonLib's float `bumpedForce`) is an integer counter in both. Not built on.

**Engine behavior not matched yet**

- **Bystanders joining a fight** (Judas in the Patriarch Inn) came from the same tier-2 mapping
  as the goblin and is fixed with it (§2.3). Alarm still maps to "helps allies" (`build_aidt`);
  a TES3 actor has no ally relation, so it no longer finds a side to join. Not re-measured.

- `OnPCDrop` is still not raised. `OnPCAdd` and `OnPCHitMe` are fixed above.
- Dagoth Ur's second form falls into the pit. Not levitation (converted, and confirmed in game):
  `dagoth_ur_2` is a walking creature (flags 0x40, no Flies bit) with no Levitate spell, so he
  should stand on the ledge. Not investigated.
- A dead actor that a script then disables reports full health from `GetHealth` (Dagoth Ur read
  436). Not explained.
- The console's `kill` leaves health untouched. Cases use `damageav health 10000`.
- A world item and its carried copy keep separate script variables.
- `ChangeWeather` does nothing (Arktwend VerlorenerSohn 41).
- Unported commands seen in runs: `GetStandingPC`, `HurtStandingActor` (~20,000 calls per heart
  run), `GetCollidingActor`, `EnableRest`, `PCForce1stPerson`, `tcl`, and the chargen menu
  enables (`EnableInventoryMenu` and the like). Arktwend adds `StreamMusic` and `BecomeWerewolf`.

**Side effects of shipped fixes**

- The combat feud sets the relationship between the two *base* records, and the save keeps it.
  Two generic actors of the same base would stay enemies until the feud ends.
- The navmesh door changes leave one newly unlinked door: Vivec grate `0191B687`
  (`Ex_V_palace_grate_02`). An obstacle sits over both route starts.
- Two Oblivion gate models (`oblivionarchgate01`, `obliviongate_forming`) measure wrong before and
  after. Accepted.

**Rebuilds and checks still owed**

- The shared navmesh cache is stale for Oblivion, Nehrim and Morrowind_ob. Republishing is
  undecided.
- Oblivion has not been rebuilt for the door-measurement change.
- TR, Tamriel_Data and the compat patch were last imported before the exterior-names table, the
  sound-id fix and statics in the reference table. Bloodmoon also needs a re-import for the
  statics.
- Every TES3 plugin except Arktwend and Morrowind.esm (rebuilt 2026-10-27, no FormID moved, five
  new factions) needs a **re-export and re-import** for the disposition
  factions (`ACBS.Disposition` is new in the export; without it every actor reads 50), and a
  re-import for the guard and hostile-creature factions and the autocalc pools.
- Morrowind's chargen case has not been rerun since the `MenuMode` latch and `OnPCAdd` changes.
- Four opcode cases fail at 10× and above. Their timing assumptions are wrong, not the engine.

**Crashes**

- Five silent game deaths, none reproduced on rerun and none captured: Sul-Matuul (A2_1), early B4,
  `storm_herbin`, `pass_judas_paid`, and one right after the `brennt_gate` save (no Windows
  crash or low-memory event; the user's Java work was running at the time).

**Noise**

- "the AI quest started" logs about 40 times in one tick.
- The "door heavy close" sound keeps playing.
- Authored per-frame calls flood the log.

**Data limits (correct as is)**

- Two A2_6 topics can't be reached under OpenMW's rule that a topic is learned only from a
  response's text. They are "mouth of the cave" (stage 29) and "Hortator and Nerevarine"
  (stage 54).
- The journal index only rises, so cases expect `>=` or the highest stage.
