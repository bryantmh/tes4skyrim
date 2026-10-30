# Headless quest runs: game bugs found

The game bugs that automated quest runs on the test instance found: converter, exporter,
navmesh and runtime faults, fixed (§2) or open (§3). Runs so far: the MWScript opcode sweep,
the Morrowind.esm, Arktwend, Bloodmoon and Tribunal main quests, Raven Rock, and Tribunal's
Mournhold side quests. The tool is described in
[headless_testing.md](../commentary/headless_testing.md); the cases are in
`tes_runtime/morrowind/testdata/quests/<run>/`.

**Status:** every fix below is uncommitted and untested in the user's own game. A fix counts as
verified when the quest case that found it passed on the test instance after it.

---

## 2. Fixed

### 2.1 MWScript opcode sweep (`testdata/ingame/`)

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

| Quest | Fix | Why | Where | Blast radius |
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
| Main quest rerun | `GetAttacked`, `GetCommonDisease`, `GetBlightDisease` ported. Diseases walk the actor's active effects for a source spell of that TES3 type, as Skyrim's GetDisease condition does; the sidecar's SPEL rows now carry the TES3 SPDT type (the export folds blight into disease) | Unported; a disease or attacked test always read 0 | `script_ops_query.cpp`, `game_calls_spell.cpp`, `game_calls_flight.cpp`, `game_calls_query.cpp`, `morrowind_sidecar.py`, `morrowind_sidecar_source.py`. Tests in `script_test.cpp`, `test_morrowind_spell_kinds.py` | Not yet run in a quest |

### 2.3 Arktwend main quest (`testdata/quests/arktwend_main/`)

| Quest | Fix | Why | Where | Blast radius |
|---|---|---|---|---|
| Chargen statue | `MenuMode` counts a menu asked to open | The script raced through race, class and sign; the sign menu never opened | `menu.cpp` `NoteMenuOpening`, `game_calls.cpp` | CharGenRaceNPC/ClassNPC (MW, patch), ANFKlosterChargenStatue |
| A_II Galeone rowboat | Player activation waits for the object's script to have run once since it loaded | The activation came before the script could claim it | `game_calls.cpp` `PlayerActivate` | — |
| A_IV Herbert | `Enable`/`Disable` answer at once | A posted `Enable` made `ForceGreeting` see a disabled actor | `game_calls.cpp` (asked-enabled state) | Enable+ForceGreeting: MW 3, Ark 10, TR 32 |
| A_IV Herbert (Gron) | **Converter + runtime:** exterior cell names table; `GetPCCell` outdoors uses it | Skyrim drops exterior FULL, so Herbert's farewell never matched | `morrowind_travel.py`, `morrowind_sidecar*.py`, `morrowind_placements.py`, `script_tables.cpp`, `game_calls.cpp` | All exterior cell-name conditions |
| Stormwend | Menus opened by a script wait for the player's move to land | A force-greet after a move closed at once | `menu.cpp` `HoldMenusForPlayerMove`, `game_calls_move.cpp` | — |
| Stormwend | AI package markers spawn after the posted move; a still-wander anchor follows its actor | Markus walked home | `game_calls_ai.cpp` | Move-then-AI: TR 614, patch 21, Ark 9, MW 2. AiWander 0: TR 712, Ark 21, MW 18 |
| Stormwend pass | A travel/escort package is released when the actor is moved out of range | Bernward walked out of his shop | `game_calls_ai.cpp` `AiAfterScriptMove` | Same sites. **The release has not been seen firing** |
| Stormwend | `EnableLevelUpMenu` fills the level progress so the dialog opens | It never opened | `script_ops_control.cpp`, `game_calls_attributes.cpp` | — |
| Thieves' Guild port | `OnPCAdd` raised when the player gains the item | Never set | `object_script.cpp` `NoteCarried`, `script_tables.cpp` | Add-only scripts: TR 34, Sky_Main 31, patch 3, Tamriel_Data 2, MW 2 (chargen papers/ring now show their "Ok" boxes), Bloodmoon 1, Ark 1 |
| Thieves' Guild port | **Converter:** sound ids decoded with the plugin's code page | 24 Arktwend sounds with non-ASCII letters were missing | `morrowind_sidecar.py` `_converted_sounds` | No other sidecar affected |
| Tate's bridge | `SetHealth` also restores damage | The shadows died although their script sets 500 every frame | `game_calls.cpp` `SetDynamicStat` | MW 11, Ark 3, TR 146 |
| Tate's bridge | `StartCombat` between two non-enemies sets archnemesis for the fight and puts it back after | Skyrim drops a fight between guild brothers within seconds | `game_calls_combat.cpp` (new) | ~430 NPC-vs-NPC sites in TR. See §3 caveat |
| Tate's bridge | `OnDeath`/`OnActivate` stay set until the script reads them | Tate's script returns early 20 of every 21 ticks, above its `if ( OnDeath )`. Matches OpenMW | `object_script.cpp/.h` `EndTick` | Reads below a `return`: MW 68+79, Sky_Main 20+58, Ark 21+69 |
| Tate's bridge | A dead actor's `GetCurrentAIPackage` is -1 | Skyrim keeps a corpse's follow package. Matches OpenMW | `game_calls_ai.cpp`, shared `IsDead` | Death checks under a package test |
| Tate's bridge | An actor a script moves re-evaluates its AI package | The old package kept steering it back | `game_calls_ai.cpp` `AiAfterScriptMove` (`Actor.EvaluatePackage`) | Every script move of an NPC |
| Chapter V–VIII | **Converter:** statics go in the reference table | A script that disables or moves a static (`id->Disable`) found nothing | `morrowind_placements.py` `PLACED_EXPORTS` | Every sidecar |
| Almasanoar | An NPC's hold release runs on the main thread after the script's move, not before | The release ran ahead of `Position`, and its glide pulled the ship's captain back to where he had stood | `npc_anim.cpp` `ReleaseNpcHold`; `script_ops_move.cpp`, `script_ops_world.cpp` call it after the move | `Position`, `PositionCell`, `SetPos` on NPCs |
| The Void | An object in an interior the player has left is not in the world | Skyrim keeps the cell's 3D a while, so the Void marker's script ran 652 times in 40 s after the player left | `game_calls.cpp` `InWorld` | Every interior object script |
| Dwarves' war | A second force-greet while the menu is open restarts the greeting in place | Two greetings in a row desynced the conversation, and the menu went dead. Matches OpenMW's `DialogueWindow::setPtr` | `conversation.cpp` `BeginConversation` | Back-to-back force-greets |
| Stormwend pass | The conversation's speaker is kept by FormID, not by pointer | After talking to Judas, a trip to Norat unloaded the inn. Back there, the stale pointer named a Dark Soldier in the freed memory, so talking to Judas activated the soldier ("fighting the player"). Intermittent; likely also the silent `pass_judas_paid` crash | `game_calls.cpp` `SpeakerRef`, `game_calls_crime.cpp` | Every `id->` naming the last speaker, and the crime faction |
| Verlorener Sohn | **Converter + runtime:** TES3 actors use the 0-100 disposition faction system. Disposition and Fight are faction ranks; Aggression stops at tier 1; an actor OpenMW calls aggressive (Fight + 20 + (50 − disposition) × 0.2 ≥ 100) joins `TES4DispositionHostileFaction`, the player's enemy alone. The runtime writes both ranks and re-runs the rule on every `SetFight` and disposition change. **Exporter:** NPDT disposition is dumped as `ACBS.Disposition` | Tier 2 attacks every neutral NPC: a leveled goblin killed Irfl before his greeting, and Judas joined the Kanzler soldiers' fight. In OpenMW Fight targets only the player and followers (`Actors::engageCombat`) | `disposition.py`, `actor_common.py`, `pipeline.py`, `morrowind_sidecar.py` (`effects_formid.txt` rows), `morrowind_actors.py` (export), `game_calls_ai.cpp` `SetFight`, `game_calls_state.cpp` (`Actor.RemoveFromFaction`, id 54958) | Arktwend: 733 actors hunt the player only (was 640 hunting everyone); 13 passive animals and summons stop hunting (guar, deer, scribs: Fight 50-70). Morrowind before re-export: 966 hunters, 44 stop. TES3 `GetDisposition` conditions now read the faction rank. TES4 unchanged |
| Verlorener Sohn | **Converter + runtime:** a TES3 Guard-class NPC joins the marker `TES3GuardFaction`; a hostile TES3 creature joins `TES3HostileCreatureFaction`, an Enemy of the player alone. The runtime sets each idle guard on a creature of that faction that is in combat within `fAlarmRadius`. `SetFight` picks the creature faction from the actor table's `creature` flag | OpenMW's guards attack an aggressive creature that is in combat (actors.cpp:733). Snow wolves fought the player beside the Nordrand guards, who never moved. Two faction-only versions started the fight on cell load and killed a guard | `disposition.py`, `owned_records.py` `adopted_or_derived`, `actor_common.py`, `game_calls_ai.cpp` `SetFight`, `game_calls_combat.cpp` `GuardsEngage`. Tests in `test_import.py` | Arktwend: 225 guards, 539 hostile creatures, 194 hostile NPCs (unchanged total of 733). 73 hostile creatures are placed near a guard, all in authored battle scenes |
| Verlorener Sohn | **Converter:** an autocalc TES3 NPC gets the health, magicka and fatigue OpenMW derives (`autoCalculateAttributes`, `setAttribute`) | The export dumps no pools for a 12-byte NPDT, so all 676 of Arktwend's autocalc NPCs had 50 health. The level-30 Nordrand guard was dead before his greeting | `morrowind_autocalc.py` `autocalc_pools`, `morrowind_sidecar_source.py` `npc_stats`, `npc_morrowind.py` `tes3_pools`, `npc.py` `_pools`. Tests in `test_morrowind_autocalc.py` | 676 of 1,262 Arktwend NPCs. The guard has 274 health, King Olim 694. Every other TES3 plugin needs a re-import |
| Verlorener Sohn | `ForceGreeting` logs why it refuses (no reference, no actor row, disabled, dead) | It returned silently, which hid the dead guard | `game_calls.cpp` `ForceGreeting` | Log only |
| Dwarves' war (Grul battle) | `StartCombat` on a dead attacker or target does nothing, as in OpenMW (`OpStartCombat`, `startCombat`) | The battle scripts call it every frame on the fallen. Each call began a feud that the next tick ended: about 300,000 log lines and game calls in 10 s | `game_calls_combat.cpp` `SetCombat`, `script_runner.cpp` | Every per-frame `StartCombat`. **Not yet run headless** |

### 2.4 Bloodmoon main quest (`testdata/quests/bloodmoon_main/`)

| Quest | Fix | Why | Where | Blast radius |
|---|---|---|---|---|
| Wild Hunt | **Exporter:** a one-way load door's arrival door (the nearest door to its destination) ships persistent | Skyrim cannot teleport to a non-persistent door in an unloaded cell, so the Mortrag Glacier Entry door did nothing | `tes4_export/export_morrowind.py` `_make_persistent`, `teleport_records` | No FormID drift in Morrowind or Bloodmoon; 1 new ref in Morrowind |
| Wild Hunt | **Exporter:** the arrival door is the nearest door at any distance (the 1024-unit radius is gone) | The Outer → Inner Ring door had no door within 1024 units of its destination and stayed unlinked | `export_morrowind.py` `_partner_door` | Same run as above |
| Carius | **Converter:** a wander of distance 0 converts to DoNothing | It became a Sandbox around "near self", which drifts forever; Carius wandered off | `tes5_import/packages/converter.py` `_pick_wander`, `location_morrowind.py` | Morrowind.esm 738 packages, Bloodmoon 77, Tamriel_Data 98, TR_Mainland 5,277 (of its 9,138 wanders) |
| Wild Hunt | **Converter:** the script-name scan decodes escapes before splitting lines | `\tbm_hircine2` was read as `tbm_hircine2`, so Hircine's ref was not persistent and never got disabled | `tes5_import/dialogue/morrowind_placements.py` `script_named_ids` | 16 Bloodmoon and 9 Morrowind refs now persistent; no FormID drift |
| All | Carried-item scripts poll only the items the player may hold, kept current by container changes | The per-tick sweep of every scripted item was the cost; behavior unchanged | `object_tick.cpp` `NoteCarriedChange`, `GatherMaybeCarried`; `script_tables.cpp` | Every scripted item |

### 2.5 Raven Rock (`testdata/quests/raven_rock/`)

| Quest | Fix | Why | Where | Blast radius |
|---|---|---|---|---|
| CO_7 | A statement that trips the compiler on a number is skipped and the rest of the script compiles, as Morrowind's compiler did ([skipped statements](../commentary/morrowind_runtime.md#a-bad-statement-is-skipped)) | `ColonySeler` wrote `PositionCell` with the cell first, so the whole script was rejected and Seler never greeted or fought. The only line skipped in either mode | `script_runner.cpp` | Runtime |
| CO_5 | `GetWeaponType` is ported: the right hand's weapon answers its authored TES3 type, carried as the third field of its `items_formid.txt` row (485 Morrowind and 68 Bloodmoon weapons) | Baro's `ColonyBaro` called it 28 times as a stub | `script_ops_query.cpp`, `game_calls_query.cpp`, `morrowind_sidecar.py`, `morrowind_sidecar_source.py` | Runtime and sidecar |
| CO_10 | `PollDeath` re-arms when the actor is alive again, so a resurrected actor raises `OnDeath` each time it dies | The Spriggans rise twice; the death latch stayed set after the first death, so `deathvar` stopped at 1 | `object_script.cpp` | Runtime; `script_test` case added |
| CO_8–CO_10 | `IsDead` answers only for actors; `DeleteRef` unbinds the reference's script instance first ([deleted references](../commentary/morrowind_runtime.md#a-deleted-reference-unbinds)) | Two crashes: `Actor.IsDead` on a non-actor read past its end, and a script bound to a deleted spawn looked up a form that was gone | `game_calls.cpp` | Runtime |
| CO_10 | Activators, containers and furniture whose script only ever disables its own object get Skyrim's Obstacle flag, and the pathgrid is bridged across them ([obstacles](../commentary/tes5_import_navmesh.md#scripted-blockers-are-obstacles), [bridge](../commentary/tes5_import_navmesh.md#a-removed-obstacle-is-bridged)) | The shaft's boulders cut the navmesh for good, and once Unel's script removed them the pathgrid still had no edge across the gap, so she could not follow | `base/obstacles.py`, `record_types/common.py`, `navmesh/obstacle_bridge.py`, `navmesh/pool.py`, `navmesh/from_pgrd.py` | Every plugin's import and navmesh; no FormID drift (`esm_diff` +0 -0) |
| CO_10 | The door-axis cache rebuilds when its door list changes, and NIF rotations are transposed into column form | The shaft door had no door triangle, so Unel stopped at it: the cache was stale, and the closed-pose axis was read from the transposed matrix | `collision_extract.py`, `door_geometry.py`, `pipeline.py` | Door triangles in every plugin; door cache schema 4 |

### 2.6 Tribunal (`testdata/quests/tribunal_mournhold/`, `tribunal_story/`)

| Quest | Fix | Why | Where | Blast radius |
|---|---|---|---|---|
| TR_DBAttack | Two plugins built on a common master are one game: they see each other's rows and key an id's state alike (`LayersRelated`, `SameGame`) | Bloodmoon defines the TR_DBAttack journal too. Tribunal's `db_assassinScript` wrote journal 10 under Tribunal's key, while a reader in another layer's view read Bloodmoon's, 0; Bloodmoon's own `dbFixScript` and a condition read it the same way. The layers exist to keep unrelated games (Arktwend beside Morrowind) apart, and siblings on Morrowind were split with them | `scope.cpp`, `scope.h`; `store_test` `TestLayers` | Runtime: every sidecar view whose plugins share a master |
| MS_Warlords | A dead actor's `GetHealth` reads at most 0 (`DynamicStat`) | Skyrim's `Kill` leaves health where it was: Soscean, dead (death state 1 in the log), read 214. TES3 has no death without health at 0, so a script testing `GetHealth < 1` (tarenScript) took a killed actor for alive. Damage deaths already read 0 | `game_calls.cpp` | Runtime: `GetHealth` on actors killed by `Kill` |
| TR_Assassins | A scripted fight whose attacker loses its target is started again until a death or `StopCombat` (`RenewFeuds`); a fight on the player is recorded as a feud but changes no relationship | One of the two assassins left behind the screens dropped the player within a second of `StartCombat`, a different one each run, and the quest waited on all three deaths. Setting the relationship to archnemesis did not stop it (tried, reverted), and Skyrim keeps relationships per base record: `ordinator wander`, placed 81 times, has 3 `StartCombat Player` rows. OpenMW's AiCombat lasts until a death or `StopCombat` | `game_calls_combat.cpp` | Runtime: every live `StartCombat` (about 400 `StartCombat Player` lines across Morrowind, Tribunal and Bloodmoon) keeps its attacker on the target. Renewed once for Karrod in TR_Champion |
| TR_ShowPower | Authored-mode regions get weather lists (census-picked Skyrim weathers, Dragonborn's volcanic ash storm for Ashstorm and Blight); `ChangeWeather` and `ModRegion` ported with OpenMW's semantics (set weathers expire every 20 hours, chances clamped 0-100) | `tr_weathmachine` set `MournWeather` and logged 95, but both commands did nothing, so Mournhold's sky never changed | `weather_morrowind.py`, `region.py`, `morrowind_region.py`, `morrowind_travel.py`, `game_calls_weather.cpp`, `script_ops_query.cpp` | Converter: Morrowind.esm gains Dragonborn.esm as a master and 9 REGN, Bloodmoon 6 REGN. Runtime: every `ChangeWeather`/`ModRegion`. Verified: "weather: type 6 now, held" in Godsreach, `GetCurrentWeather` 6 |
| TR_ShowPower | Generated FormIDs hash each FormID in their key as if no vanilla master past Skyrim.esm were listed | Adding Dragonborn.esm moved 10,475 of Morrowind.esm's generated ids, because the keys held shifted FormIDs | `formid_hash.py`, `writer.py` | Converter: every derived id in a plugin with a vanilla master past Skyrim.esm (today only authored Morrowind.esm); no drift in any plugin after it |
| TR_Blade | `AddTopic` teaches the topic at once, as OpenMW's `DialogueManager::addTopic` does | It only queued the topic, and the queue was read after a dialogue result and cleared when a conversation began. Torasa Aram's MuseumScript (an object script) set `daggerfang` and `dagsym` to 1 (probe) but its "Fang of Haynekhtnamet" and "Dagger of Symmachus" never appeared | `script_runner.cpp`, `conversation.cpp`, `dialogue_state.cpp/.h`; `script_test` | Runtime: `AddTopic` in object and global scripts: 19 Morrowind, 1 Tribunal, 20 Tamriel_Data and 91 TR_Mainland scripts |
| TR_Blade | A short global's junk FLTV reads as OpenMW's `floatCast<int16_t>` reads it: 0, not INT32_MIN | `MuseumDonations` holds 7.6e+23 and was staged as -2,147,483,648, so after both donations Torasa's "compensation" still answered "Bring me a couple quality pieces" | `morrowind_sidecar.py` `_global_value`; `tests/test_morrowind_globals.py` | Converter: 12 short globals now start at 0 instead of INT32_MIN: Morrowind `RedoranMurdered`, `MT_WritDiscount`, `wearingHelmHHDA`, `TGglove`, `ratskilled`; Tribunal `gobchiefdead`, `ShrineCleanse`, `MournWeather`, `MuseumDonations`, `KarrodFightStart`, `KingHit`; Bloodmoon `ColonyService` (all 12 also in the compat patch). Each is a flag or counter compared with small numbers |

---

## 3. Found, not fixed

**Arktwend**

- **Enderal tower marker (0840 `enderal_brother`).** Once `EnderalTurmvar` is 8, the marker in
  EnderalTurm01a moves north with `MoveWorld Y 300` until the player reaches it. Our rate matches
  OpenMW's (rate × frame time), yet the player never catches it. It is unclear how a real player
  does.
- **An actor killed in its first seconds never gets `OnDeath`.** Object scripts bind about 6 s
  after the actor loads, and `PollDeath` takes its first sample then. A corpse at that first
  sample is never seen dying.
- **Mauragin's force-greet while disabled is skipped** (0840 `enderal_pass`). OpenMW returns early
  for a disabled actor too, so this matches.
- **Asartan's journal 2 (0830)** is reachable only on a visit before Tyrat. Nothing reads it.
- **Accepting the Sultan's task sets `Mytrehen` to 10** (0837 `desert_outpost`), so the outpost
  leader attacks instead of greeting. This looks authored.
- **Herbert's road to Gron (0400 `herbert_gron`).** Wolves hunt the player on the road, and
  Herbert's companion script returns while his weapon is drawn, so he never reaches State 7. In
  OpenMW a Fight-87 wolf engages only within about 1,400 units; Skyrim has no such falloff (see
  the actors commentary).
- **Nordrand guard on a slope (0790 `sohn_guard`).** A player dropped near him can slide out of
  his 400-unit greeting range and into the child's, whose greeting skips the journal ahead.
- **Mao's greeting comes first (0860 `dwarves_mao`).** Mao's script makes Daras greet, ending in
  `Goodbye`; Daras's own script, which returns in menu mode, writes journal 4 only after that.
- **Two proximity scripts that did not fire, once each** (0790). In one run Irfl never called
  `ForceGreeting` (no "force-greets" line in 20 s, the player 120 units away); in the next,
  `ZwergenMissionMadchenSchreck` never wrote journal 6. Every other run of each passed. Cause
  unknown.

**Morrowind and Tribunal**

- **The game exits silently when the level-up menu opens** (`a1_v_levels`), twice in a row without
  a debugger. With cdb attached it did not crash. Timing-dependent; no dump yet.
- **`GetCollidingPC` is not ported** (Tribunal: `ExitBlockMessage` and `explosion_pushout`, 11
  calls logged during MS_Warlords; 275 in a Show of Power run). Same gap as the line below.
- **`HurtStandingActor`, `GetStandingPC`, `GetStandingActor` are not ported.** Skyrim has no
  standing-on query. CommonLibSSE names `bhkCharacterController::supportBody` at +0x2B0, but no
  function of the controller reads or writes a pointer there on 1.5.97 or 1.6.1170, and +0x2B8
  (CommonLib's float `bumpedForce`) is an integer counter in both. Not built on.

<a id="raven-rock-open"></a>**Bloodmoon and Raven Rock**

- **CO_2 (Hroldar) needs a punch to tire its target.** Built, not yet verified: a brawler (a
  script that declares `OnPCHitMe` and reads `GetFatigue`) takes no health from the player's
  bare fists, and each punch takes the player's `UnarmedDamage` from its Stamina
  ([brawls](../commentary/morrowind_runtime.md#brawls)). Three scripts match: Hroldar, TR's
  Anbarnag and the Savrethi couple. Hroldar has 40 health and 300 fatigue.
  **Measured 2026-10-28: the drain loses to regeneration.** 208 bare-fisted punches (about one
  every 1.5 s) each took 4 Stamina (`UnarmedDamage`, which includes the race's base 4).
  Hroldar's fatigue never reached 150, so he never greeted. Nord race data gives Stamina
  regeneration 5% of the maximum a second, about 15/s at 300; that rate is read from the record,
  not timed. OpenMW returns fatigue at `fFatigueReturnBase` + `fFatigueReturnMult` × Endurance a
  second, and a Morrowind punch drains Hand-to-Hand × 0.1 to 0.5. Open: which drain and which
  regeneration a brawler gets. The two choices on the table: Morrowind on both sides for
  brawlers (regeneration 2.5 + 0.02 × Endurance, 4.5/s for Hroldar; a punch drains
  Hand-to-Hand × 0.5), or Skyrim's 4-point punch with the brawler's regeneration paused while
  it is being punched (about 63 punches).
  **Switched off pending that choice** (`kBrawlsOn` in `game_calls_brawl.cpp`): no brawler
  is armed and no punch drains Stamina, so a brawler takes Skyrim's ordinary punch damage.
  The import still writes `TES3BrawlAbility`, unused.
- **Three bittergreen plants gave four petals, not six** (CO_10). The converted containers each
  hold two (`CNTO` count 2 on `flora_bittergreen_02/04/05`); after `activate` and console
  `removeallitems` the player had four, twice. Which plant came up empty is unknown.
- **Unel was missing from the bar in 2 of 4 runs** after the navmesh rebuild (CO_10). Not
  diagnosed.
- **A speaker left within two frames of the conversation closing goes with the player** (CO_9).
  Constans Atrius, whose converted package is DoNothing, was outside Fort Frostmoth (x -176812)
  the moment the player was moved to Raven Rock, then walked west and stuck at (-188627, 153614).
  With 150 ticks between the close and the move he stayed at (3408, 4090). Our menu never starts
  Skyrim's own dialogue, so the engine rule behind it is not known. Open for a result script
  that moves the player straight out of a conversation (transport NPCs such as Asciene Rane in
  Tribunal): the speaker may be dragged along there too.
- **The remaining object scripts that do not compile.** `script_test --sweep-objects`:
  - Authored mode: 4 of 1,644 fail. Bloodmoon's `WereChange2Script`, `MockChangeScript`,
    `wereDreamScript2` and `wereDreamScript3` stop at `if ( BM_BearHunt1 == 0 )`, which names
    a journal id where a variable belongs, so the werewolf dream scripts never run.
  - Morroblivion mode: 132 of 5,520 fail, nearly all on a member of another actor's script
    (`"Athyn Sarethi".duelOndresNerano`, `skinkintreesshade.soulAshGhoul`). The patch sidecar's
    `SCPT_objects.txt` maps 365 objects and leaves out vanilla actors such as Athyn Sarethi,
    whom the authored Morrowind sidecar maps to `athynSarethiScript`.
  - Neither is diagnosed yet.

**Engine behavior not matched yet**

- **Bystanders joining a fight** (Judas in the Patriarch Inn) came from the same tier-2 mapping
  as the goblin and is fixed with it (§2.3). Alarm still maps to "helps allies" (`build_aidt`);
  a TES3 actor has no ally relation, so it no longer finds a side to join. Not re-measured.
- `OnPCDrop` is still not raised.
- Dagoth Ur's second form falls into the pit. Not levitation (converted, and confirmed in game):
  `dagoth_ur_2` is a walking creature (flags 0x40, no Flies bit) with no Levitate spell, so he
  should stand on the ledge. Not investigated.
- A dead actor that a script then disables reports full health from `GetHealth` (Dagoth Ur read
  436). Not explained.
- The console's `kill` leaves health untouched.
- A world item and its carried copy keep separate script variables.
- `ChangeWeather` in Arktwend (VerlorenerSohn 41) is now ported but not re-run there.
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
- A save made before the short-global fix keeps a global already raised from INT32_MIN (for
  example `MuseumDonations` after a donation); only a global the save never wrote reads the new 0.

**Rebuilds still owed**

- The shared navmesh cache is stale for Oblivion, Nehrim and Morrowind_ob. Republishing is
  undecided.
- Oblivion has not been rebuilt for the door-measurement change.

**Crashes**

- Five silent game deaths, none reproduced on rerun and none captured: Sul-Matuul (A2_1), early B4,
  `storm_herbin`, `pass_judas_paid`, and one right after the `brennt_gate` save (no Windows
  crash or low-memory event; the user's Java work was running at the time).
- Wild Hunt (0990), once in four runs: the game died as five werewolves were killed in one tick in
  the Outer Ring. No crash log; three later runs passed.

**Noise**

- "the AI quest started" logs about 40 times in one tick.
- The "door heavy close" sound keeps playing.
- Authored per-frame calls flood the log.

**Data limits (correct as is)**

- Two A2_6 topics can't be reached under OpenMW's rule that a topic is learned only from a
  response's text. They are "mouth of the cave" (stage 29) and "Hortator and Nerevarine"
  (stage 54).
