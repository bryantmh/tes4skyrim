# Full-run worklog (2026-11-01 ..), organized by quest

## 🛑 USER'S STANDING INSTRUCTIONS -- obey every one, every time
1. After EVERY compaction or resume: read THIS WHOLE FILE before anything else (a hook enforces it).
2. Goal: two back-to-back clean full runs. Known failures fixed one by one first; in a full run, fix a failure
   and resume from it, never restart everything. Never stop a running loop or lane.
3. One pass proves nothing: 3 consecutive passes per changed case (repeat_case.py); flaky history needs more.
4. Tests NEVER run slower than real time and are never slowed; making them faster needs no permission.
   Exception (user, 11-06): a lower speed is an OPTION for the specific cases that need it -- the case's
   `pace <speed>` step (authored game-time windows, slope climbs the ~25x governor steps break); default elsewhere.
   NEVER ask about a global slowdown (step cap, lower default): "the answer is ALWAYS no other than specific cases".
5. Followers really walk: never teleport the player with a follower; doors used from their landing.
6. No route-forcing legs of ANY kind -- `walk` legs at mesh_route points are route forcing too, not just
   `steer` (user, 11-06; the enderal_burg_battle legs stay because they work, nothing new). Only exception: a long
   path across several cells around a large obstacle may need legs; anything else is a navmesh problem. Bad navmesh on a quest route = a CLEAN pin (no overlaps, holes, slivers,
   T-junctions), checked visually; never a generator change; no steer workaround.
7. NPCs move: don't wait for an NPC at a spot -- `approach` it. Change tests only where failures are found.
   User 11-06: "This is the ENTIRE reason we built approach. Just teleport to an npc if the player doesn't have
   a follower." An NPC not where the case expects (no follower along) -> `approach <ref FormID>` (a FormID
   reaches an unloaded NPC); do NOT investigate where it went.
8. No bribery/persuasion RNG in cases: SetDisposition stands in.
9. Fixes are generic (never for one record, plugin or game); the goal is complete conversion.
10. Two failed attempts on one theory = the theory is wrong. Gain in one place + regression elsewhere = wrong idea.
11. Every shell command through `python tools/validate/safe_run.py` (-c for loops, pipes, $(), sed, printf).
12. Never git commit/push/stash/add/rm/reset. No docs or tests before in-game confirmation. No code in AppData.
13. Never limit workers. Every check under 120 s. Build only the plugin about to be tested.
14. TR/TD builds take --morrowind-source morroblivion. Mod names Morrowind.esm/Tribunal.esm/Bloodmoon.esm.
    The morroblivion stage (TR's game start) runs UNCHANGED; arktwend and morroblivion never share a lane.
15. Autonomous = no questions; keep going. Plain words in reports. Report honestly: untested is said.
16. Measure the invariant asked for; trust the user's in-game results; a stated fact is ground truth.
17. 🛑 (user, 11-07) A FLAKY case's FIRST fix = REMOVE its walking/steering. No follower along -> place the player
    (Position/PositionCell/approach -- not rigging). Follower must come -> ONE `walk` to the destination; a stall
    there = navmesh pin. Never add or tune steer/walk legs. Two weeks went into tuning legs against this rule.

## 🛑 BUG-FIX PROCESS (user, 2026-11-08 -- after weeks at ~4 Arktwend fails/run)
1. Work failures by their RATE over all logged runs (temp/.../run_totals.py), not the last one seen.
2. No edit before the mechanism is proven (reproducer or log line explaining passes AND fails).
3. Fix in the layer the bug lives in, generically. Case edits only REMOVE steps -- never add steer/pace/legs/waits.
4. One change at a time, judged by the case's rate over ~10 runs; 3/3 proves nothing (10% flake passes it 73%).
5. A change that doesn't move the rate is reverted, not stacked on. Every lane failure gets a cause.
6. Each fix logged: cause, change, rate before/after.

## 🛑 PROCESS CHANGES (2026-11-04, after repeated re-work and compactions)
- Before touching a quest: read its entry below; never re-try an attempt listed as failed.
- A quest that keeps coming back = a generic bug patched around per case: fix the mechanism once.
- Context discipline: pull numbers (grep -c, tail -1, one field), never raw logs/scripts/JSON/disassembly.
  Heavy digging (disassembly, log forensics, save probes, long scans) -> haiku subagent, one-paragraph answer.
- Default loop: run -> read the one failing line -> fix -> rerun. Deep dives only for a recurring failure.
- This file is organized by quest: update the quest's entry in place (status, last finding, failed attempts);
  no diary. Raw chronological original: temp/full_run_2026_10_31/WORKLOG_ARCHIVE.md (backup only, grep it if
  an entry lacks detail). Scratch scripts and run logs stay in temp/full_run_2026_10_31/.

## Goal and procedure
- GOAL: two back-to-back, fully autonomous, fully clean full runs of every .mwtest under
  tes_runtime/morrowind/testdata (driver temp/full_run_2026_10_31/full_run.py). **Clean runs: 0/2.**
- Lanes (one instance each, env INSTANCES): (morrowind -> bloodmoon -> tribunal) | (arktwend) | (morroblivion).
  Morroblivion is how Tamriel Rebuilt starts (morroblivion_main --new-game 3, quiz, tr_telvanni, tr_necrom):
  runs UNCHANGED, never edited; arktwend and morroblivion never share a lane.
- User procedure: "work on known failures and fix them all individually before attempting a full run, and when
  doing a full run, if you hit a failure fix it and retry just from the failure again." Lane resume:
  RESUME=folder:case. Never stop a running loop or lane.
- A fix counts at 3/3 consecutive from the checkpoint (repeat_case.py); a case with a fail history needs ~10.

## Launch pitfalls
- `approach` takes a REF FormID; the "activation: X is" log line prints the BASE id (TRACE / ACHR give refs).
- Launch: mwtest --timeout default 60 (repeat_case sets 3600); --mod Morrowind.esm (not Morrowind);
  a folder's first case can't take --from (repeat_case `-- --load <save>`); Bloodmoon stage = MW+BM build
  (refs carry slot 06), MW_ALL puts Bloodmoon at 07.

## RUN 7 (11-06), Morrowind lane (MW/Tri/BM reimported with the Recover fix; DLL = slot v3 + actor SetPos move)
- ingame 187/187, morrowind_main 97/97 (b2_escort PASSED), bloodmoon_main 17/18, raven_rock 14/16,
  raven_rock_brawl 3/3, tribunal_mournhold 10/11, tribunal_story 17/17, morrowind_temple 70/70, daedric 24/24.
- bm_beasts (no history): the bear (900 hp, regen = Restore Health 5/s, no AppliedOnce -> Recover change does not
  touch it) died 2 s real after the 5 rieklings attacked it, while the case's console kills ran one per tick;
  beastbearScript OnDeath -> journal 105. Rerun x3 (run7_fails.sh -> rc/bm_beasts_r7) to tell flake from new.
- co_supply_ship / co_estate: the 2nd Carnius wait (Fort Frostmoth shrine) and the Aldam wait (Raven Rock Mine) saw
  GetDistance = FLT_MAX (NPC not in the player's cell) = the carry class. CASE (rule 7): those two waits ->
  `approach`. Rerun x3 each (rc/co_*_r7).
- ms_performers (no history): journal 150 = playScript's stage-30 timer: the player must be within 100 of
  Script_PlayScript (activator at -1689 12 116; the case's spot -1689 -30 is 42 away) within 15 GAME seconds;
  30 at 15:41:13 -> 150 at 15:41:14 (~0.6 s real at 25x) before the runner's bye/run/PositionCell landed. Passed
  runs 1-6. Rerun x3 before changing (rc/ms_performers_r7).
- Reruns x3 (run7_fails.sh): bm_beasts 0/3, co_supply_ship 3/3 (approach), co_estate 0/3 ("Aldam never
  loaded"), ms_performers 0/3 -> not flakes.
  bm_beasts: probe diag_bear (no kills): bear 970 at arrival, 720 at journal 20, 256 ten ticks later, dead ten
  after -- the rieklings attack once the player is within 1500 of them, and MW's ~36 game s of riekling damage
  is < 2 s real at ~25x; the case's 5 `console X.kill` lines go through the harness one at a time (AskHarness),
  too slow. CASE: the kills are one `run` block of `"BM_riekling_be_UNIQUEn"->SetHealth 0` (runtime kills, one tick).
  co_estate: ColonyTimer, on ColonyState 30 (the very state the case waits for), PositionCells Aldam to Raven Rock
  (-197492 160314, beside Falco); the only move INTO the mine is co_under_siege's greeting (ColonyState 21). The
  case's mine visit + 28 steer legs + 2 doors out of the mine were wrong for the authored data. CASE: approach Aldam
  in Raven Rock, then walk to the 2nd torch (torches are outside, torch 1 at -201553 159678).
  ms_performers: stage 30 (greeting) -> 150 one second later: the player's PositionCell to the stage landed AFTER
  150; 15 game s = ~0.6-0.9 s real at 16-27x. Why it passed runs 1-6 not found (no passing log kept). CASE: the
  player is put on the stage spot before the second Meryn greeting (talk has no range check).
  -> run7_fixes.sh: x3 each.
  WHY NEW (user's question): run 6 PREDATES the pace governor (archive l.665-687 vs l.712). Run 6 ran uncapped 1/60
  steps (speed = fps/60: 25x only in light cells, Arktrand 0.5x); the governor drives ~25x everywhere (Mournhold
  16-27x). bm_beasts and ms_performers are authored game-time windows the speedup shrank. Any NEW timing failure
  since run 6: check the governor first.
  RESULT run7_fixes: bm_beasts 3/3, co_estate 3/3, ms_performers 3/3 (+ co_supply_ship 3/3). -> RUN 8 Morrowind
  lane on instance 2 (run8_mw.out).
- Arktwend run 7 failures: tate_nembrest (X0Y6 stall 2301 25957 again), sohn_airship + sohn_home (steer stuck),
  verrat_brutus (VerratMarker01's 8 game s timer showed the Gargart MessageBox before Brutus's force-greet; resumed
  from a stale sohn_home ckpt too), enderal_burg_camp (wall-top walk), enderal_burg_battle (my return leg 138888
  -141658). Scout PASSED in-lane. Asked the user about the governor: answer "lower the speed target, but as an
  optional part of certain tests that require it". BUILT IN SOURCE (unbuilt: run 8 holds the DLL): runner step
  `pace <speed>` (test_runner Kind::kPace -> Hooks().setPaceTarget; g_paceTarget replaces kPaceTarget in
  game_calls_query; reset to the default at every case end; log "pace: target now N x"). To use it in: tate
  (X0Y6 climb), verrat_brutus, and wherever a timing window/climb fails; then revisit the bear/play case fixes.
  Instance 1: sohn_airship, sohn_home, enderal_burg_camp x3 (ark_run7_fails.sh): airship 0/3, home 1/3, camp
  2/3 -- 5 of 6 fails = `steer` legs stuck at a different spot each run (player controller at big governor
  steps, the archive's 1/6-s climb class) -> retest with `pace` once built.

## RUN 8 (11-06), Morrowind lane: NOT clean (3 fails)
- ingame 187/187, bloodmoon 18/18 (bm_beasts fixed), raven_rock 15/16, mournhold 11/11 (ms_performers fixed),
  story 17/17, temple 70/70.
- chargen_captain: "menu holds the game (MessageBoxMenu)" after the move into the Census office: the "Press E to
  talk" box (CharGenDialogueMessage, once only) opened at 16:47:12, no button click logged, talk held. Flake
  history (chargen_papers/hall). Rerun x3.
- b2_escort: journal 40 -- the Nals/Eigma standoff (8/10 since the Recover fix; still open).
- co_supply_ship: last `approach Carnius Magius` -> "never loaded": he was talked to in the Frostmoth shrine at
  CO_3 10, then the player teleported away = the CARRY bug, which approach cannot mask once the NPC lands in an
  unloaded place. Carry is the recurring generic bug -> needs its mechanism fixed, not more case masking.
- `pace` BUILT (after run 8): a new target rescales the step at once (step x new/old); the target is set at each
  case START to its leading `pace` (else the default) -- so a window opened at the end of one case keeps the
  lower speed into the next case that also leads with `pace`. Cases: `pace 10` leads tate_nembrest, sohn_airship,
  sohn_home, enderal_burg_scout, enderal_burg_camp; `pace 5` after verrat_arrest's bye + leading verrat_brutus.
  pace_check.sh -> rc/{tate,verrat,airship,home,scoutcamp}_pace + captain_r8 (x3 each).
  RESULT: verrat 3/3, sohn_airship 3/3 (was 0/3), captain 3/3 (flake), tate 2/3 (was 0/3), sohn_home 1/3,
  scout->camp 0/3 -- scout PASSES, camp's walk to the wall top starts on the SCOUT side (140455 -144119).
  ROOT CAUSE OF THE WHOLE "SCOUT/CAMP STATE" MYSTERY (runtime bug, all games): the inner gate EnderalTrunTelurTor02
  = `if Enderalvar == 3 / OnActivate == 1 / Set Enderalvar 5 / Activate`. Probes (diag_gate, from the gate ckpt):
  claimed player activation + the script's own Activate -> Enderalvar 5 but NO teleport; console activate (no
  claim) -> teleported to the XTEL (140484); our scripted Activate with NO prior claim -> teleported. So a door
  whose script claims OnActivate then calls Activate never carries the player once the click was claimed. The
  old "gate-ckpt state" difference = camp passed only from ckpts where the player had been Position'd. Sonnet
  agent (code reading): NO runtime state found that blocks it -- claimed call sets only Events().activated +
  HoldMenusForPlayerMove (menu/runner only, ~1 s); the scripted call passes the hook (ref == g_scriptActivation)
  and reaches the original. Gap noted: the claim flag is never cleared (OpenMW's activateByScript clears
  Flag_SuppressActivate) -- not the cause. Probe C targeted 05D5AC07 (its base has ONE placement). Leading
  (INFERRED) theory: the engine latches something when ActivateRef's base Activate returns true without the
  original (claimed), and ignores the same ref's next player activation. NEXT (when no run holds the DLL): TEMP
  log in ActivateHook (claimed / original called / original's return) for MW doors, rerun probe A.
  (TEMP log edited into activation.cpp ActivateHook, unbuilt.)

## RUN 11 (11-07, carry fix DLL): MW lane 4 fails, Morroblivion lane 2 fails (lane still running)
- MW: ingame 187, main 96/97 + chargen_captain, bloodmoon 17/18, raven_rock 16, brawl 3, mournhold 11, story 17,
  temple 69/70 (tt_join = silent exit at 0 result lines), daedric 24. b2_escort = parked (Nals/Eigma standoff).
- chargen_captain ("MessageBoxMenu holds", 3rd time in 4 full runs): CASE ORDER BUG -- the "Press E" box
  (CharGenDialogueMessage: GetDistance Player < 300) opens on ARRIVAL in the office, before the `run`
  PositionCell step, which waits on the held menu while `button 0` came after it = deadlock whenever the box
  wins the race. CASE: `button 0` moved before `run`. (The run-10 "click in the opening frame" theory was wrong;
  button settle kept, harmless.)
- bm_carius (until MenuMode never): Gaea Artoria (0635C797) joins as a follower in bm_smugglers, the case then
  teleports the player around -> under the new OpenMW rule she "stays behind" (log 03:29:43), so artoriaScript's
  ForceGreeting (journal 100 + within 256) never fires. Before, Skyrim's catch-up carried her. CASE: `approach
  0635C797` before the MenuMode wait (rule 7).
- run11_fixes.sh: captain 3/3, tt_join 3/3 (exit = one-off), carius 3/3.
- Morroblivion lane then hung the same way in morroblivion_quiz mbq_questions_6_to_10 (7468 judges) -> lane
  stopped, TESRuntime built with the fix (both instances hard-linked), lane RESUMED from that case
  (run11_mwob_b.out, inst 1); mwob_fixes.sh: morroblivion_main new game .. mb_chargen_sellus x3 (inst 2).
  Resumed quiz case failed "never said 'while in the marketplace'" = MY resume artifact (the line is spoken
  before the predecessor's checkpoint); quiz must run from its folder start.
- mb_chargen_sellus 0/3 = REAL: Gravius says "See you later" ~70 ticks after his greeting, before the case's
  `wait 120` + ask (run 4, pre-governor, kept it open 130+ ticks). CAUSE (probe diag_sellus: console setgs
  fAIInDialogueModewithPlayerTimer 600 -> 2/2): Skyrim's in-dialogue idle timer (60 GAME s) runs out in ~2-3 real
  s at the paced ~25x. FIX (runtime, game_calls_query KeepDialogueIdleRealTime): a paced run scales that GMST by
  the pace target (60 real s, as in play); game settings read through ids::kGameSettings 400782 (= SKSE
  g_gameSettingCollection 0x30fd6f0 on 1.6.1170). dist DLL moved aside (temp/.../MorrowindRuntime.inst1_in_use.dll,
  instance 1's lane holds it) and rebuilt; mwob_fixes.sh: sellus x3 + quiz x3 on instance 2.
  RESULT: sellus 3/3 (log "fAIInDialogueModewithPlayerTimer scales with the speed-up"), quiz (whole) 3/3.
- RUN 12 MW lane started on instance 2 (run12_mw.out) with both new DLLs; Morroblivion lane run 11 continues on
  instance 1 (old MW DLL) through tr_telvanni / tr_necrom.

## RUN 12 (11-07), Morrowind lane: NOT clean (3 fails; b2_escort PASSED)
- ingame 187, main 96/97 + papers, bloodmoon 18, raven_rock 16, brawl 3, mournhold 11, story 17, temple 70,
  daedric 23/24.
- chargen_papers "MessageBoxMenu holds": the captain's "Press E" box (his own script CharGenDialogueMessage,
  GetDistance Player < 300, once) opened right after papers' PositionCell to (160 -170) -- 1224 from his
  placement (NPC 00CA8705 placed at 1366 -380 196; PACK wander radius 0, hello-approach skip
  installed). Same held-box class as the papers/hall/captain flakes. TEMP log (object_script messageBox: where
  the box's owner and the player stand) -> papers_r12 x3 (new game .. captain) to measure before a fix.
- a1_v_levels: silent game exit again (2nd: runs 10 + 12) -> levels_r12 x3.
- da_malacath_rumors: `until "nadene rotheran"->GetDistance player < 1000` failed -> rule 7: CASE + `approach
  053E05E5` (ACHR 003E05E5, MW at slot 05 in the Tri build) -> malacath_r12 x3.
- run12_fixes.sh (inst 2): papers 3/3, levels 3/3 (exit = one-off), malacath 3/3. In all 3 papers runs the
  captain's box opened at the captain case (him ~200 from the player at his spot) -- the papers-desk opening
  did not recur; TEMP box log stays for run 13 to catch it.
- RUN 13 MW lane: CLEAN, 443/443 (ingame 187, main 97, bloodmoon 18, raven_rock 16, brawl 3, mournhold 11,
  story 17, temple 70, daedric 24) -- first clean MW lane. No papers-desk box. Then Arktwend lane on inst 2
  (run13_ark.out); Morroblivion run 11 still in tr_telvanni on inst 1.
- RUN 13 Arktwend lane: 9 fails -- tate_nembrest (X0Y6 stall 2294 25967, same spot), theater_calisto_house,
  sohn_worldend, sohn_home (steer 101267 4703), scout, camp, brother (cascade), dwarves_mert (steer 7051 9109),
  dwarves_picks (steer -963 1800); dwarves_shaft..end + arktwend_side 6 pass.
  theater_calisto_house: journal 4 = TheaterTreffenMarkerCalistosHaus (ACTI MarkerTheaterTreffen, REFR 004748B5 at
  33365 37236): `GetDistance "TheaterTreffenWache02" <= 800`. Both guards < 800 of the player, but the marker's
  instance NEVER BOUND this run (passes: bound the second the player arrives, stage 4 3 s later). Sweep lap is
  13474 rows / 256 = 53 ticks + 150 rest -> not the sweep's speed. Open.
  sohn_worldend: Mauragin froze 582 from the player (needs < 500) -- at 44 after the walk, then Grignir's
  force-greet opened our dialogue menu and the player walked 575 on; he never moved again (PACK follow, combat
  0). Follower-freeze class again (sohn had 8/8 after the actor-SetPos fix).
  LAVA SOUND STORM (real bug, all MW-format games): EnderalLavaFluss = `if Enderalvar < 9 Disable` + `if
  GetSoundPlaying "lava layer" == 0 PlayLoopSound3DVP` every frame; Skyrim's Sound.Play from a DISABLED ref
  returns 0 -> retried every tick: 397k "did not start" lines + synchronous engine calls (~1000/s) from
  enderal_pass on (present in scout passes and fails alike, so not the scout cause). OpenMW plays it at the
  disabled ptr's position (script settles). FIX (runtime): PlaySoundAt skips the engine call for a disabled
  source; Start logs only started sounds.
  scout: the outer gate DID carry the player (original returned 1 x2; at 141542 -145100 on the scout side 2 s
  later), then the player's AI walk went BACK to the square side (139497 -142546, no runtime move logged =
  engine door traversal) and bumped the outer gate (its script claims it, Enderalvar != right) for 5 min.
  mesh_route landing -> scout spot exists on the scout side. Open.
  ark13_fails.sh: theater/worldend/home/fortress chain (scout..brother)/mert/picks x3 (inst 2, sound-fix DLL).
  RESULT: 0 "did not start" lines (storm fixed); home 3/3, theater 1/3, worldend 1/3, fortress 0/3, mert 2/3,
  picks 2/3. CAUSES (all case-side):
  theater: STADAntonia force-greets just outside the gate (09:48:16, player at -2994 37306); the dialogue stays
  open, every later steer "ends" at once, the guards still follow (< 800 passes) -> player never near the marker
  = the "never bound" of run 13. CASE: `bye if open` + repeat of that steer. (run 3 = steer stuck 133 short of
  23116 38601 on the hill road, one-off.)
  worldend: ZwergenMissionGrignir = ForceGreeting at GetDistance Player <= 800, no Mauragin condition; the greeting
  opens before Mauragin reaches < 500 and he can't close in during the menu. CASE: the pre-greet check accepts
  MenuMode (`- 100000 * MenuMode`), and Mauragin < 500 is checked after `bye`.
  fortress: the gate case's journal 2 is set on the CLICK; the checkpoint/next case started with the player
  still on the square (scout's walk "ended" at 140500 -143234 = gate's walk target, then the player's AI walk used
  the outer gate itself) = run 13's "walked back through the gate" too. CASE: gate + `until player->GetPos Y <
  -143500` (landing y -143673).
  mert: never left its start (5482 1833); picks: stuck at (-300 1883) going to -400 1766 (new spots each run).
  ark13_fails2.sh: theater, worldend, fortress (from gate), mert, picks x3.
  RESULT: worldend 3/3, picks 3/3, gate + scout pass 3/3 (gate landing wait works); theater 1/3 (both fails =
  steer stuck in the X0Y6 hill corridor -- 2209 25767 and 0 25518 = TATE's corridor, one shared defect); mert 1/3
  (start 5482 1833 never moves x1, steer 7065 9170 x1); fortress 0/3 at camp's first walk.
  DOOR DECLINE ROOT CAUSE (the whole "intermittent" inner-gate story): Tor02 (inner gate) = `if Enderalvar == 3 /
  OnActivate / Set Enderalvar 5 / Activate`; Tor01 (outer gate) = `if Enderalvar == 5 / lock 100`. Fails: "Steintur01
  locked at 100" lands BEFORE the posted Activate -> all 121 retries return 0 (3/3); old pass fortress_chain_2: the
  Activate returned 1 BEFORE the lock line. Disasm (sonnet): TESObjectREFR::GetLock (20223, 0x2fad60) returns the
  ref's own ExtraLock (0x2A), ELSE the linked door's (ExtraTeleport 0x2B) -> Skyrim shares a load door's lock with
  its partner; TESObjectDOOR::Activate (0x27da50) refuses the player on that lock's flags bit 0. TES3 locks each door
  alone. FIX (runtime, activation.cpp OwnLockOnly): around the original Activate of a TES3 placement with no
  ExtraLock of its own whose GetLock is locked, bit 0 is cleared and restored after; log "is unlocked -- its linked
  door's lock is not its own". Door retry (kDoorActivateTries/ScriptDoorActivate) + LogDoorDecline REMOVED (wrong
  theory). Built (inst-1 lane DLL moved to temp/.../MorrowindRuntime.inst1_run14.dll). fortress_lock.sh x3.
  X0Y6 (tate + theater, one defect): layers (x0y6_layers.py: lowest/highest surface per 20-grid point): the only
  two-layer area = the rocks, x 1880..2180 y 25580..26180 (high up to 674); ground under the strip 437 (y 26000)
  .. 540 (y 25760), single layer, while the shipped strip tris sit 514-556 (NO_FLOOR, up to 76 above ground) and
  the walk path hugs the rock edge 20-60 away. Ground EAST of the strip (x 2300..2480) is an even 20-25 deg slope
  with no mesh. PIN (backup pins.pre_x0y6_wide.json): points = lowest layer, 40 grid (thin_pts.py), box 2100
  25600 2480 26120 band 380..640, extend "2150,25690;2480,25690;2480,26090;2250,26090" (wide ramp east), notch =
  rocks' outline padded 45 (footprint.py) "1855,25555;...;2210,25845;2160,25760;2025,25555", margin 30: removes
  24, builds 125, min 12.5 deg. SAVED; import waits for inst 2's fortress batch.
  tate X0Y6 (run 13): the player stands EXACTLY still (0.01) the whole walk = AI path failure, not physics.
  engine_check over the corridor: X0Y6 NO_FLOOR 7 tris (2230..2326, 25883..25963, z 514-556) right over the
  stall; route (mesh_route road point -> station, tate_route3.txt, 67 points) climbs a hill to z ~1676 and also
  crosses NO_FLOOR tris in PastureLandX1Y6 (5, z 1673) and X1Y5 (10, z 1542-1672). Others clean. slab_map
  (x0y6_now.png) shows the strip 29-74 BELOW a top surface + a two-layer wedge west -- contradicts engine_check
  NO_FLOOR and the drop probe (ground 450-474 only). Unresolved; pinning one spot only moved the stall.
- Morroblivion mb_chargen_ship (runner never finished, 10000 judges "1 1"): RUNTIME BUG in the common runner's
  until/menu exemption (tes_runtime/common/test_runner.cpp): the held-menu check's Judge consumed the harness
  answer, then Until asked again (pending) -> loops forever while a menu (race menu) is up. FIX: the held check's
  true verdict finishes the until itself. test_runner_test passes; TESRuntime.dll link waits for instance 1.
- mb_chargen_sellus: Gravius greeted, then said "Bye." himself before `ask duties` -- no history; rerun x3 after.
- Run 11 Morroblivion lane (resumed, inst 1): tr_telvanni ht_va1_priest "runner did not finish" = SILENT EXIT
  (log ends 04:48:51 on `move: player -> the exterior 'Wilderness' (342815, 140900, 1600)`; no crash log). The
  lane continued from ht_va1_done. Lane end: tr_telvanni 68/68 after the resume, tr_necrom 19/19 -> the lane's
  only fail is that exit. mwob11_fails.sh: inst 1 rebuilt on the current DLLs, ht_va1_priest x3: 3/3 (one-off).
- RUN 14 Morroblivion lane started on inst 1 (current DLLs: carry fix, dialogue timer, sound fix):
  run14_mwob.out. RESULT: main 10, quiz 4, necrom 19; tr_telvanni 2 fails: ht_join = SILENT EXIT (GAME EXITED
  after 0 result lines, log ends on Vero Renim's dialogue menu opening; no crash log, NO Application Error event)
  and ht_vaerin_q6_done (talk Vaerin: "fighting the player" -- on arrival in Tel Vaerin FF00288A (a runtime ref,
  not in the log before) died and "combat: 09E14EF6 (Vaerin) now targets 00000014"; his script starts no combat).
  Both new. mwob14_fails.sh: x3 each on inst 1 (rebuilt with the door-lock DLL).
  RESULT: ht_join 3/3 (exit = one-off); ht_vaerin_q6_done 0/3 = REAL. Not the ritual guardian (that is FF002F05);
  FF00288A/B = hostile creatures fighting Dralothas's household in Tel Mothrivra. diag_vaerin (from the ritual
  ckpt, temp/.../diag_vaerin/): no crime gold (realm faction) at any step, Vaerin disposition > 30; Dralothas talk
  result script harmless. CORRELATION 6/6 logs: Vaerin attacks the player on arrival in Tel Vaerin (tick 1, before
  his script binds) exactly when someone in Tel Mothrivra targeted the player (household 09565AF9/09BA92DC or
  summon FF000E22: 2-3 "now targets 00000014"); 0 such lines -> Vaerin calm. Runtime feuds/guards don't touch the
  player. TEMP log (game_calls_crime TempCrimeNote): anyone on the player prints its crime faction, gold, and
  'hostile' rank -> diag_vaerin.sh x3 (inst 1).
  enderal_brother (fortress_lock 3/3 reach it; door-lock fix: 2 "is unlocked" lines per run, gate..battle pass
  3/3): `button 0` "no MessageBox opened" -- EnderalTurm01a's marker MoveWorlds Y 300/game s and boxes only within
  1000 (~3 game s = 0.13 s real at 25x). CASE: `pace 2` (rule 4 exception).
  X0Y6 pin import: ark_import_x0y6_wide.log -> mesh_clean 0/0/0/0 but x0y6_wide_pinned.png showed the pin's west
  edge under the rock's hook (x 2210-2240, y 25900-26000, tris 3-10 below its top; the 20-grid outline missed it).
  Re-planned from the restored backup + unpinned reimport: notch + "2250,26010;2250,25960;2215,25910": 122 tris,
  min 12.4; SHIPPED (ark_import_x0y6_wide2.log), mesh_clean 0/0/0/0 (135 tris), x0y6_wide2_pinned.png clear of the
  rock, tris 0..6 off the floor. ark_x0y6.sh (inst 2): brother x3, tate x3, theater x3.
  Vaerin diag_vaerin3 (from the ritual ckpt) 3/3 calm (no tower actor targeted the player); diag_vaerin_b from the
  DRALOTHAS ckpt reproduces: Vaerin 09E14EF6 + 09209B4E target the player on arrival, crime faction 06BBA8D8 gold
  0, hostile rank -2 -> not crime, not our hostile faction. TEMP note + relationship rank / disposition / aggression
  ranks -> diag_vaerin_c: FOUR Tel Vaerin NPCs on the player (09E14EF6 Vaerin, 09209B4E, 09A02B2C, 09E6262E), all
  crime faction 06BBA8D8 gold 0, hostile -2, relationship to player 0, disposition rank 50, aggression rank 30-60
  -> nothing per-actor; faction-level state (player-enemy flag / alarm) suspected. No converted Morroblivion
  script calls SetPlayerEnemy/SetEnemy/SetReaction for these factions. Sonnet disasm asked (faction player-enemy
  flag: offset, who sets it, how to read/clear).
  DISASM: TESFaction+0x58 bit 3 = player-is-enemy (+0xFC set time in game hours, +0xF8 alarm timer), saved with
  the form; relationship fn 0x689FB0 (37667) returns Enemy for the player when ANY of the NPC's factions has it.
  MEASURED (TEMP note): 06BBA8D8 flags 00008048 (bit 3 SET), enemy since 138.689 h. TEMP hook on all 19 direct
  calls of SetPlayerEnemy (24505): set the tick the player arrives in Tel Mothrivra (diag_vaerin_e_2), stack
  0x71B023 (inside 0x71AB20 = 40076, witness reaction) <- 0x6F3B3C <- 0x6D3C58 (AI process update) <- 0x667AD1.
  Sonnet asked what that witness branch tests (player gold check reads 0 on every faction we read).
  ark_x0y6.sh: BROTHER 3/3 (pace 2). tate 0/3: stall now INSIDE the new pin (2344..2380, 25867..25885), clean
  on-ground mesh; mesh_route from there: 957 points, leaving NORTH (was 306 via the strip SW) -> MY PIN CUT THE
  CLIMB: x0y6 10-grid layers show the rock's SE side IS the hill (rock top 549-566 = ground there, single layer);
  only NW of a diagonal (1935,25495 .. 2205,25885) is the rock a slab over lower ground (450). The old strip's
  "float" = the smoothed mesh over that 50-80 ledge. The padded-45 convex notch cut the real path. theater 0/3 at
  3 other steer points (-2487 35522, 9365 26090, -1600 25950). NEXT: pin v3 = concave two-layer outline (rise 25,
  pad 15, fp15.txt) as notch, single-layer floor points (one_layer_pts.py, 148), box 1990 25560 2480 26120, joined
  to the strip SW at (2018 25584). Unpinned reimport ark_import_x0y6_unpin2.log first.
  Pin v3 planned: removes 29, builds 196, min 11.8 -> pinned + ark_import_x0y6_v3.log.
  VAERIN ROOT CAUSE (disasm, sonnet): 0x71AB20 (40076) = the per-update reaction of an actor running an ALARM
  package (type 0x15, inferred from xEdit): target NOT the player -> SetPlayerEnemy(actor's crime faction, true)
  at 0x71B01E with no crime/gold test (target = player needs that faction's gold > 0). The Tel Mothrivra household
  alarming at the hostile creatures flags House Telvanni (06BBA8D8 = Morrowind_ob slot 06) -> every member
  (Vaerin) treats the player as Enemy for 24 game h. Neither TES3 nor TES4 has a faction-wide alarm. FIX (runtime,
  game_calls_crime InstallAlarmPlayerEnemy): the call at 40076+0x4FE (byte-checked `call SetPlayerEnemy`) is
  repointed; it skips factions in a full load-order slot after Skyrim's masters (slot > 4, < 0xFE); Skyrim's own
  factions keep it. TEMP setter log kept for the check. vaerin_alarm.sh: dralothas..done x3 (inst 1). Saves that
  already carry the flag (the dralothas ckpt) keep it until it expires (24 game h).
  RESULT vaerin_alarm 3/3 (the hook skipped the alarm's flag once per run).
  X0Y6 v3 shipped: route stall->station goes SW (212 points), road->station 317; but 4 tris under the rock's low
  hook (2170..2240, 25925..26000, 2-15 deep). v4 (hook notch) left a gap between the two notch parts; v5 = ONE
  merged notch "1885,25475;1885,26105;1945,26175;1985,26175;2065,26105;2125,26065;2185,26035;2215,26015;
  2250,26010;2250,25935;2215,25895;2165,25835;2115,25765;2065,25695;2025,25635;1975,25565;1935,25495;1915,25475",
  single-layer points (x0y6_pin3_pts.txt), box 1990 25560 2480 26120 band 380..640, extend
  "2000,25560;2480,25560;2480,26090;2250,26090", margin 30: 193 tris, min 11.8. SHIPPED (ark_import_x0y6_v5.log),
  mesh_clean 0/0/0/0 (206), road->station 318 points, x0y6_v5_pinned.png all 0..3, clear of the rock.
  ark_x0y6_v5.sh: tate x3 + theater x3 (inst 2).
  RESULT v5: tate 0/3 (stalls (2183 25738), (2120 25666) on the pin's SW corner; one at (-128 25613) on the road),
  theater 1/3. engine_check on v5: NO_FLOOR x5 on the SE corner (the extend climbed the cliff to z 768-1214) +
  HEADROOM at (2227 25747)/(2253 25773) = the stall. x0y6_standing.py (engine_check's standing rules at each 10-grid
  ground point): 559/3309 fail -- under the small rock, the cliff (HIGH), and a thin diagonal HEADROOM LINE
  (2130,25640 .. 2490,25920; single walk surface, something overhead 32-100 up; refs in the box: only the bush
  a_Busch_Static_01 and the big terrain_rock_wg_08 at 2497 25501 z 1137 -> its lower lip, inferred). The climb is
  a channel ~50-80 wide between the small rock and the line's west end, then ~40 wide SW. v6 (pad 12) left an
  island hole + gaps between line patches; v7 (pad 20 merged) clean but NO_FLOOR along the cliff-foot edge; v8 =
  floor points from the passing set (x0y6_stand_ok40.txt), notch = all failing points padded 20
  (x0y6_bad_notch20.txt, 5 parts), extend "2000,25560;2240,25560;2480,25760;2480,26090;2250,26090", margin 25: 183
  tris, min 13.5. SHIPPED (ark_import_x0y6_v8.log): mesh_clean 0/0/0/0 (196), engine_check STANDING 0/0/0 for the
  whole cell, road->station 326 points through the channel; x0y6_v8_pinned.png (4 tris 2-15 under the rock's low
  hook, passed by the standing rules). CASE theater: its 16 old-strip steers (2270 26119 .. 1987 25499, the
  floating heights) -> one `walk 1987 25499 596`. ark_x0y6_v5.sh (now v8 labels): tate x3 + theater x3.
  RESULT v8: tate 0/3 (stalls (2196..2206, 25768) x2, (2301 25853) = 60-80 N of the overhang line), theater 0/3
  (its new walk never reached 1987 25499 x2; steer -38 25602 x1). 4th pin attempt failed on the same corridor ->
  the "fix the mesh shape" theory is WRONG for this stall (rule 10). The overhang reaches beyond the 32-100
  headroom band (pad 60 merges rocks+line and closes the channel entirely); yet theater's STEER chain crossed
  this hill in many past passes -> passable by steering, the engine's AI walk picks a line that scrapes the
  overhang. Kept: pin v8 (engine-valid, better than the floating strip). CASES: theater's 16 steers restored (my
  walk replacement reverted); tate gets theater's proven steer chain from the road point over the hill to (6307
  22975 1162), then the station walk -- user's leg exception (long multi-cell path around a large obstacle).
  -> ark_x0y6_v5.sh (labels tate_legs / theater_steer) x3 each.
  RESULT: tate 2/3 (fail: steer stuck short of 2018 25584 in the channel), theater 0/3 (steer stuck: 23116 38601
  -- 3rd time at that spot --, 2161 25751 in the channel, -1535 26511 on the road S of town). Steer = player
  controller (no navmesh) -> physical stalls at governor steps. CASES: `pace 10` leads both (rule 4 exception for
  climbs); tate's earlier pace-10 3/6 was the AI walk, not steers. -> labels tate_pace10 / theater_pace10 x3.
  RESULT: tate 3/3, theater 3/3. dwarves_mert (1/3 + run-13 fail): 2x stuck steering round the boulder by the
  door landing (5506 1807 -> 5790 1630, back-off failed), 1x 7065 9170 on the rails -> `pace 10` -> mert_pace10 x3.
  RESULT mert 3/3. All known Arktwend failures fixed -> RUN 17 Arktwend lane on inst 2 (run17_ark.out).
- RUN 17 Arktwend lane: 194/197 (arktwend_side 6/6). Passed in-lane: tate, theater, fortress gate..brother,
  sohn_worldend, sohn_home, mert, picks. FAIL herbert_house (steer stuck short of -65687 1083 524), sohn_child
  (`until` NordrandKind < 600: the child never caught up; player steered fine to 130797 -38151), dwarves_shaft
  (steer stuck short of 1395 3855). ark17_fails.sh: x3 each (inst 2) before changing anything.
  RESULT: herbert 3/3, shaft 3/3 (one-offs); sohn_child 0/3 (until child x1, steer stuck 130084 -38305 / 132667
  -37519) -> `pace 10` -> child_pace10 x3.
- RUN 17 Morrowind lane (door-lock + alarm DLL): 442/443 -- ingame 187, main 96/97, bloodmoon 18, raven_rock 16,
  brawl 3, mournhold 11, story 17, temple 70, daedric 24. FAIL chargen_papers "MessageBoxMenu holds" (4th time):
  TEMP box log CAUGHT IT: the captain's box (owner 05636B45 at 1380 -390) opened at 14:36:12, the tick of papers'
  PositionCell to (160 -170 220), with the PLAYER at (1173 -391 255) -- 207 from him, ~1000 from the papers
  spot. No walk/travel on the player that run; MoveInto = the engine's MoveRefrToPosition, no marker. So the
  player really stood by the captain at that moment (engine relocation of the landing? unconfirmed). Rerun
  papers_r17 (new game .. captain) x3 on inst 1 to get a second capture.
- child_pace10 3/3 -> RUN 18 Arktwend lane on inst 2 (run18_ark.out).
- papers_r17 2/3: in ALL 3 runs the captain's box log shows the player at (1173 -391) = the courtyard door's
  landing; passes = the real courtyard arrival (box opens before the captain case's move); the fail = during
  papers' PositionCell to (160 -170) in the same interior -> the engine parks the player at the cell's door
  landing for a few frames before the position lands; the captain's GetDistance Player < 300 reads it.
  FIX (runtime): MoveInto records the player's landing (place, xyz, tick); until the player stands within 64 of it
  in that place (or 30 ticks), Distance() with the player answers from the landing (game_calls.cpp
  DistanceToLanding, game_calls_move.cpp PlayerLandingSpot). script_test passes. papers_land x6 (inst 1): 6/6.
  -> RUN 18 Morrowind lane on inst 1 (run18_mw.out) with the landing DLL. (Arktwend run 18 on inst 2 runs the
  DLL before it: temp/.../MorrowindRuntime.inst2_run18.dll.)
- RUN 18 Arktwend lane (same DLL as run 17): 191/197 -- FAIL herbert_house (steer stuck -67740 5814), baron_house
  (walk never reached -41 -57 724), theater_calisto_house (Wache01 800 > 600 behind), sohn_lift (Mauragin < 600
  wait), sohn_home (walk never reached 96882 4113), dwarves_demon_king (no MessageBox). Run 17 had 3 other fails
  -> lane-to-lane variance in walk/steer/follower cases. ark18_fails.sh x3 each (inst 2, landing DLL).
- RUN 18 Morrowind lane (landing DLL): 441/443 -- chargen PASSED in-lane; FAIL b2_escort (steer stuck short of 217
  -2746 -256) and b3_bride (steer stuck short of 77642 84144). mw18_fails.sh x3 each (inst 1).
  RESULT: b3_bride 3/3 (one-off); b2_escort 2/3 (fail = steer stuck short of -163 129 254 inside the Wise Woman's
  yurt, a different spot). b2 stays OPEN (history: governor speed already a failed attempt; not re-trying pace).
- ark18_fails (landing DLL): lift 3/3, home 3/3, demon 3/3 (one-offs); baron 2/3 (steer stuck 3722 54962);
  herbert_house 0/3 (steer stuck short of -67740 5814 -61, same spot all 3 + the lane); theater 0/3: Wache01 holds
  at ~800 from the start, the guard's companion script (TheaterWache01, 0544412F) runs but never issues AiFollow
  ("TheaterTreffenWache01 follows player" 0 lines; 2685 in the pace10 pass) -> the run-18 theater_guards
  CHECKPOINT lacks the follow state (theater_guards passed in-lane without checking it). Landing fix not involved
  (no player move in the case). theater_chain: theater_guards..calisto_house x3 (inst 2).
  RESULT theater_chain 0/3 -> checkpoint theory WRONG. CAUSE (authored data + a race): Wache01's Greeting 4
  ("Calisto must die") has NO condition and does `Journal A_V_TheaterTreffen 3 / Goodbye` (no follow); Wache02's
  Greeting 4 ("we will accompany you") needs journal == 2 and does journal 3 + AIFollow for BOTH guards. Both
  scripts force-greet as the player arrives; run 18's lane + chain: Wache01 first -> Wache02 then gives Greeting 9
  (no follow) -> the guards never follow. Run 17 passed = Wache02 first (inferred). Run 18's lane failed on the
  pre-landing DLL -> not the landing fix. NEXT: which guard's script should win the race in TES3 (their force-greet
  conditions + OpenMW's script order), then fix generically (or the case's arrival spot if it is test-side).
  AUTHORED INTENT: TheaterWache02 (05262A7A) ForceGreeting at GetDistance PLAYER <= 500, TheaterWache01 (0544412F)
  at <= 400 (both on Stormwendvar 20, State 0) -> an approaching player meets Wache02 first. The case TELEPORTED
  the player from 1500 out straight into both ranges = race. CASE: the last approach is `walk -343 49200 220`.
  RESULT theater_walk (guards..calisto) 3/3, 52 follow lines. herbert_ab on the pre-landing DLL 0/3 too -> not the
  landing fix; herbert_chain (gor..house) 1/3, both fails steer stuck short of -67872 3838 64 -> `pace 10` ->
  herbert_pace10 x3: 1/3 (stuck short of -67740 5814 and -67872 3838) -> pace does NOT help herbert_house; it
  passed 3/3 after run 17 (herbert_r17) and now fails from fresh herbert_gor runs too -> OPEN. Candidates to
  check next: what stands at the two stuck spots now (refs_near / slab_map), and whether today's Arktwend
  reimports changed that cell's mesh (the pins json was restored from pre_x0y6_wide several times).
  Stuck spots: (-68613..-68762, 6992..7045) = beside terrain_rock_wg_14/15 + ex_drystonewall_end_01 (-68650..
  -68727, 6932..7087) where herbert_gor leaves the player; (-67821 3879): no placed ref (terrain).
- RUN 19 Morroblivion lane: CLEAN 122/122 -> MORROBLIVION LANE: 2 back-to-back clean runs (16, 19).
  RUN 19 Morrowind lane (inst 1, run19_mw.out) + RUN 19 Arktwend lane (inst 2, run19_ark.out) started.
  herbert_ab.sh: herbert_house on the pre-landing DLL x3 (A/B vs 0/3 on the landing DLL). RUN 19 Morroblivion
  lane on inst 1 (run19_mwob.out).
- RUN 19 Arktwend lane: 186/191 + side 6 -- FAIL herbert_house (steer -67740 5814, as before), sohn_lift
  (Mauragin < 600), sohn_home (steer 163178 52943), enderal_burg_scout (walk 143300 -147900), dwarves_shaft
  (steer 1395 3855). ark19_fails.sh: lift/home/scout/shaft x3 (inst 2), then razing (MW+BM build) x3.
- RUN 19 Morrowind lane (in progress): chargen_papers (MessageBoxMenu, 5th), b2_escort (steer stuck short of
  -163 129 254 = Outer Shrine's first leg, 2nd lane in a row at that leg), co_razing_forest.
  co_razing_forest CAUSE (runner): `defend` in an EARLIER case (19:04:18) killed BM_spriggan_co4 the tick it
  loaded, before its ColonySpriggan4 script bound and disabled it -> deathvar 1 early -> its 3rd death came in
  round 3 (journal 30) and `co4 GetHealth > 0` never held. FIX (runtime, KillPlayerAttackers): an actor whose
  staged script is not yet bound is skipped (TES3 runs a script before the AI).
  chargen_papers CAUSE: papers' move is only 59 from where class left the player (160 -111 -> 160 -170), so the
  landing check (kLandedWithin 64) counted the player as landed BEFORE the engine parked him at the door
  landing (1175 -611, 299.7 from the captain) -> real distance used -> box. FIX: kLandedWithin 16.
  Runner: a steer/walk failure now appends the player's whereIs (for b2's Outer Shrine leg).
  DLL built (dist; inst-1 lane copy temp/.../MorrowindRuntime.inst1_run19.dll); script_test passes.
  RUN 19 MW lane final: 440/443 (ingame 187, main 95/97, bloodmoon 18, raven_rock 15/16, brawl 3, mournhold 11,
  story 17, temple 70, daedric 24). mw19_fails.sh (inst 1): new game..captain x6, b2_escort x3.
  herbert_house CASE: + `steer -68693 7482 163` (gor's last point) first -- stuck spots lay ~280 W of the line
  from there (inferred: start displaced) -> herbert_r19.sh (inst 2) after ark19_fails.
  mw19_fails: papers 6/6 (landing 16), b2_escort 3/3. mw20.sh (inst 1): razing x3, then RUN 20 MW lane
  (run20_mw.out) if 3/3.
  razing_i1_r19 0/3 = STALE SAVE (my error): the co_report checkpoint already holds co4's early death (0 defend
  kills, co4 died twice). ark19_fails' razing_r19 uses the same save -> ignore it. Rerun: raven_rock from
  mwtest_bm_wildhunt .. co_razing_forest x3 (razing_full_r19), then the run-20 lane.
  ark19_fails: lift 2/3 (Mauragin < 600 wait), home 3/3, shaft 1/3 (steer stuck 1520 4105 / 1395 3855), scout 0/3.
  SCOUT (lane + 3/3 reruns, reproducible from the run-19 ckpt): the gate DID carry the player (141549 -145109);
  the scout walk (marker placed right at 143300 -147900) then walks BACK through both gate doors to the square
  and "ends" at (140500 -143234) = the GATE case's walk target -> the travel procedure kept the OLD goal. Player
  took travel slot 2 = the PACK (050260FF) it was still running (the gate walk), i.e. slot rule v3's reuse path
  (EvaluatePackage before the fill) does not reset the player. mesh_route landing -> target exists (straight SE).
  Passing fortress_lock reused slot 3 too -> not every reuse goes stale. TEMP log (FillAiSlot: on reuse, who
  holds every slot of that kind) -> herbert_r19.sh: scout x1 diag, then herbert chain x3 (inst 2).
  RESULT scout_slot 0/1 with 0 reuse lines -> STALE-SLOT THEORY WRONG. Fail track: (141487 -145027) on the
  scout side -> 2 s later (139475 -142475) on the camp side, with a burst of new cells loading (engine door
  traversal, inferred) -> square (140500 -143234) -> outer gate. mesh_route landing -> target exists (straight
  SE). engine_check X34YN36 untrustworthy here (CALIBRATE: 14 of 26 pathgrid nodes NO_FLOOR). OPEN: why the
  engine's path from the scout side runs through the gate doors. (A door ban on walks would only turn this into
  a stuck walk -- not a fix.)
  razing_full_r19 (raven_rock from its start) 3/3 -> RUN 20 MW lane started (inst 1, run20_mw.out).
  herbert: back-steer -> first leg OK; herbert_r19 2/3 (fail: stuck 65 short of -67872 3838 = against the well
  ex_nord_well_01a at -68031 3843) -> that steer point removed -> herbert_well 2/3 (fail: climb leg -65990 1377
  -> -65687 1083, player stuck at (-66020 1351 454), Herbert 49 behind, not blocking). Each fix moves the stall
  to the next leg of the steer chain.
- 11-07 RULE 17 applied (user): legs REMOVED, one walk per destination (followers along), `bye if open` +
  re-walk where a greeting may end a walk: herbert_house (13 steers -> 1 walk), dwarves_shaft (40 -> 2 walks),
  sohn_lift (90 lines -> 4 walks), sohn_home (551 lines -> 3x the door walk), tate_nembrest (my theater chain +
  road leg removed -> station walk), theater_calisto_house (114 lines -> house walk), b2_escort (shrine door,
  Inner Shrine door, statue; creature kills moved before the first walk). pace removed from those cases.
  ark_nolegs.sh (inst 2): the six Arktwend ones x3. b2 when inst 1 frees. A stall now = navmesh pin.
- 11-07 OFFLINE PATHFINDER (user: "an offline tool that exactly matches ingame pathfinding"): plan = (1)
  calibration set from our logs: tools/navmesh/walk_outcomes.py -> temp/.../walks.jsonl: 5174 walks, 4745
  reached, 175 stalled, 254 other (only walks against the CURRENT shipped navmesh count); (2) engine route
  planning (start/goal lookup = engine_check's, triangle A*, path smoothing) -- rules being read from CK
  disasm (sonnet agent); (3) body check along the path: actor capsule vs collision, step/slope; (4) calibrate
  until every known stall/pass agrees; then run every case walk offline and fix all breaks before game runs.
  Engine fact (contracts doc): the route planner ignores actor width; the body meets collision while walking.
  tools/navmesh/pathfind.py v0 (World loader by grid/interior FULL name, engine lookup via engine_check.locate,
  triangle A* with PLACEHOLDER centroid cost, Mononen funnel): scout landing -> target = direct SE path (game
  went through the gate doors) -> the door hop must come from the engine's real A* rules (agent pending).
  CK DISASM (sonnet): A* node = triangle (centroid; start = start point); g step = 2D dist x flag multipliers
  (0x40 preferred x0.01, water has x4 / change x5) x edge-width 0.5*(1+clamp(1-L/512)); h = 0.01 x 3D; edge
  links type 1/3 refused; load doors = info-level hops (NAVI), door route ~3x walked cost; smoother keeps
  (R+5)/1.01 from the corridor; CK default request R 35 h 128; controller max slope 47 deg (SSE 0xe9dcd0).
  Built: pathfind.py (those A* costs, border-only inset funnel), pathfind_body.py (body walk: footprint floor,
  step 40, 3 steep steps, head-on hit within R+12), pathfind_calibrate.py. Arktwend score (logs after the ESM
  was written, first 60 distinct): game reached 44 agree / 11 tool false stalls; game stalled 5: 1 placed right.
  Fixed on the way: stale walk starts (first sample used), plank gaps (footprint floor), funnel collapsing on
  short interior edges (inset only border ends). Scout: NAVI connects the two meshes, start lookup is right ->
  door choice still unexplained. Open false-stall classes: corner by the pinned rock (9316 21225), canyon
  "climb 43" at (2778 2183), interior-coord starts (12 20 704).
  X0Y6 (tate/theater stall): the tool's route runs THROUGH the game's stall spots (2207 25770, 2301 25853), so
  the route matches; but static clearance there is 34-47 (walls S/E/SE at every height, nothing overhead)
  while the same walk passed a spot with clearance 33 (1947 26199). Static geometry alone does not separate
  this stall from passes -> candidate: the AI-driven player overshooting corners near walls at fast-mode
  step sizes (dynamic). Headroom test added (none fires there).
- RUN 20 MW lane (so far): ingame 187, main 97, bloodmoon 18, raven_rock 16, brawl 3, mournhold 11, story 17,
  temple 70, daedric 24 -> RUN 20 MW LANE CLEAN 443/443. RUN 21 MW lane started (inst 1, run21_mw.out;
  first lane with the stripped b2_escort).
- ark_nolegs RESULT: sohn_lift 3/3, herbert_house 2/3 (Herbert.State 5 never -- he went in? diag later),
  tate 0/3 + theater 0/3 = the ONE walk went INTO the steel horse 'Wagon' interior and stuck at (-527 -228)
  (NOT the X0Y6 hill), sohn_home 0/3 (stuck ~167300 81600 1230, near the start), dwarves_shaft 0/3 (stuck at
  (1383 3225) / (1389 3375) in the shaft gallery: the rails cut its navmesh -- pin).
  WAGON ROOT CAUSE (generic): 'Wagon' (02AA6954) = three separate little rooms, one per stop door (town
  50817, town 38843, station 20893); its one NAVM 020FF438 holds two disconnected components with a door each
  (the middle room has no navmesh). The engine plans load-door hops over WHOLE navmeshes (CK disasm), so one
  NAVM holding doors in separate parts reads as a walkable link -> the walk takes town door -> Wagon ->
  station door and sticks. Same class likely = scout (gate doors). split.py only split when a SAME-CELL
  teleport pair spanned components. FIX (import): split whenever door triangles lie in 2+ components
  (split.py _doors_in_several_components; unused door-pair lookup removed from pipeline_records). Measured
  before: 53 such interior NAVMs in Arktwend, 115 in Morrowind (temp/.../door_components.py). Adds NAVM
  records (siblings), existing ids kept -- verify by diff vs Arktwend_English.pre_split.esm. split.py is a
  navmesh/*.py file -> navmesh cache tag changes (full regen; republish later). Doc
  tes5_import_navmesh.md#split-only-the-meshes-that-need-it still states the old rule (update after confirm).
  Arktwend reimport: ark_import_split.log -> 62 interiors split; vs pre_split ESM: NAVM +186, nothing gone or
  moved. split_tate/split_theater x3 running (inst 2).
- sohn_home AUTHORED ROUTE (user: "is that the intended route?"): Grignir (167317 81886, the case start) says
  "follow the path through the frozen waterfall behind me. Or through the woods by the sea." The frozen
  waterfall (a_Gefrorener_Wasserfall x30, 164376..164481 75789..76111) has the crevice door a_Spalte_01 at its
  foot -> 'Ice Flow' -> 'Slate Cavern' -> exit (127597 54533 5359) toward Northron. The old steered mountain
  climb was NOT the authored route (cells 41,16 / 41,17 have no navmesh at all).
  The route is dead for any AI: 4 of its doors have NO Door Triangle (crevice outside, both Ice Flow<->Slate
  Cavern doors, the cave exit outside). Census: Arktwend 204 of 1192 load doors have no door triangle (166:
  mesh never comes within 220; 37: cell has no navmesh), Morrowind 155 of 3139 (temp/.../door_miss.py).
  USER: "Doors are supposed to always have triangles, it should be in the navmesh code."
  Why: door reach was NARROWED 11-03 (archive l.61: it changed 797 Oblivion / 243 Nehrim interiors incl.
  protected chargen cells) to pathgrid-less interiors only; a door beyond an authored pathgrid's mesh gets none.
  FIX (worker.convert_job): build the cell; if any teleport door standing in the cell got no Door Triangle,
  flood from ONLY those doors (native door_reach, anchors = the cell's graph nodes; exteriors pass the land
  field + origin), join the paths to the graph, rebuild (door_reach.join_missing_doors). Cells whose doors all
  have triangles are unchanged. tests: test_navmesh_cache 101, test_load_door_reach 14 pass. Not yet built.
  NEXT: Arktwend import (also ships the shaft pin) -> door_miss census + sohn_home route offline; then blast
  radius on Oblivion/Nehrim protected cells (chargen, Nehrim intro) before anything ships there.
- split_tate 0/3, split_theater 1/3 (first single-walk pass): NO wagon entries any more. Fails: (-1665 27179
  413) x3 and X0Y6 (2264..2288, 25812..25865) x2. (-1665 27179): navmesh runs through a ~65-wide gap between
  terrain_rocks_ai_03 (rock, 28 away) and fz_f_tree1a (tree trunk, 38 away) -- narrower than an actor. X0Y6
  is the same shape (50-80 channel between the small rock and the overhang). => CHOKES: navmesh laid through
  gaps narrower than the body. Generic generator-side defect (user: "path exists is too lenient, counting
  choked navmesh that an NPC couldn't pass").
- Arktwend import with the door joins + shaft pin: ark_import_doors.log (copy before: Arktwend_English.pre_doors.esm).
  Doors w/o triangle 166 -> 133 (join from door points only). The Ice Flow door is mounted upside down
  (RotY pi): origin -1125, floor -1408 (283 below, past DOOR_SEED_DROP 256); its threshold computes to -1630
  (wrong for a flipped door). FIX: seeds also = each landing within 512 of a missing door (pool gives every
  job 'landings') + the door at the landing's height. Reimport (ark_import_doors2.log): 133 -> 114; both cave
  doors on sohn_home's route now have triangles; the two exterior ones still not (crevice: cell 40,18 has 8
  pathgrid nodes, all far; the flood reaches the mesh but no anchor) -> anchors = graph nodes + points along
  graph edges (next).
  Edge anchors added (door_reach.edge_anchors, EDGE_ANCHOR_STEP 128): 114 -> 115 (no help). Real cause for
  the crevice: it is a PERSISTENT door (WrldMorrowindPersistent), so landings_by_cell files its landing under
  the persistent cell, not grid (40,18) -> its job got no landing seed; and its threshold computes to z -120
  for a door at 889 (tilted RotX 5.88 / RotY 3.58, scale 2) = collect_doors' threshold ignores X/Y rotation
  (second generic bug, same as the flipped Ice Flow door; not fixed yet -- landings stand in).
  FIX: pool._attach_landings: exterior jobs get every world-cell landing in their grid square. Reimport
  ark_import_doors4.log: doors w/o triangle 166 -> 100 (+37 in navmesh-less cells). Crevice + both cave doors
  linked; start -> crevice path OK. The cave EXIT door 023864BD (127859 54745 5880) still none: the mesh near
  it lies 500-600 lower (5241-5346), a 544-tri pocket with no doors and no way on -> the authored route
  breaks again on the high ground past the cave. sohn_home stays open (more unmeshed mountain).
- DOOR-JOIN BLAST RADIUS v1 (door_join_blast.sh, OFF/ON same code): Oblivion int 10/1676 + ext 7/6586 (+13
  door tris, 229 -> 215 s); Nehrim int 15/623 + ext 8/2366 (+23, 61 -> 61 s); MW int 10/1128 + ext 18/357 (+27,
  8.7 -> 11.4 s); Arktwend int 23/389 + ext 19/775 (+62, 23.5 -> 24.4 s). PROTECTED: 5 ImperialDungeon + 8
  Schattenruf cells UNCHANGED. But ~9 cells changed with NO door gained (BrokenPromisesCave, DreamofPerception,
  XPXedilian02, SENSBliss01, NimblesTurm1, OblivionRD006TowerRight, 0003F5B0, 000040FE...) -> rule: keep the
  joined rebuild only if it links MORE doors (worker._linked). Rerun ON (dj_*_on2 / dj_*_cmp2). Seeded
  exterior cells (grow_into_empty waves) are NOT in this A/B -- measure separately. Connectivity (breaks.py)
  still to run on the changed cells.
  ON2 (improve-only rule): Nehrim 21 cells (+23 doors, 8 protected unchanged), Arktwend 38 (+62); every
  changed cell gains a door. Oblivion + MW ON2 died (WinError 1450 / serialize: both game instances running)
  -> rerun when idle. Arktwend ESM still carries the v1 rule until its next import (after run 22).
  USER (11-07): "your door a/b is acceptable". Other plugins get it on their next build (build only the plugin
  about to be tested).
- dwarves_shaft: walk now passes the rail (pin works); 0/3 at `button 0`: walk ends <=128 short; the lift
  operator (VAufzugfuhrerScript, ACHR 002BD6D6 at 4448 4170) boxes within 200 -> CASE walks to the operator.
  op_dwarves_shaft x3 (inst 2). herbert_house 2/3: Herbert stopped 305 from the GorScript02 marker (-66699 421
  685; sets Herbertvar 2 when HERBERT is within 300) -> CASE walks to that marker.
- Launched: RUN 22 Arktwend lane (inst 1, run22_ark.out) = regression check of split + door joins + shaft pin;
  doors_dwarves_shaft / doors_herbert_house x3 (inst 2); door_join_blast.sh (Oblivion, Nehrim, MW, Arktwend
  OFF/ON A/B, protected cells flagged).
- doors_dwarves_shaft 3/3 (walk to the operator), doors_herbert_house 3/3 (walk to the GorScript02 marker).
- RUN 22 Arktwend lane (split + door joins v1 + shaft pin): 5 fails, rest passed (herbert, shaft, side 6/6):
  tate_nembrest (X0Y6 choke 2172 25785), baron_hideout (GAME EXITED after 19 lines), theater_calisto_house
  (rock/tree choke -1670 27211), sohn_airship (an old STEER chain: stuck short of -826 -1294 in 'Old Airship'),
  sohn_home (no route: cave exit door, see above). sohn_airship -> rule 17: steers + pace removed, 3 walks
  (gangway door, greeting spot, deck end by the cranks). Next: Arktwend reimport (improve-only door rule),
  airship x3 + baron_hideout x3; chokes = passage-width metric + blast radius BEFORE asking the user.
- Door A/B ON2 (improve-only): Oblivion 12 cells (+13 door tris, 5 protected unchanged, 229 -> 197 s), MW 20
  (+27). Arktwend reimported with it (ark_import_doors5.log, 0 errors). r22 reruns on it: sohn_airship 3/3
  (steers -> 3 walks), baron_hideout 3/3 (exit = one-off).
- CHOKE THEORY CHECKED (choke_width.py, narrowest chord over steep surfaces): X0Y6 stall spots are 150-172
  wide -> NOT a choke; rock/tree spot 71-98. Theater r22 track: on mesh at (-1660 27410 473, tri 35) then
  ~200 S / 56 down OFF the mesh (150 from any tri) into the rock/tree hole, still there 5 min.
  PREFERRED-FLAG idea (we set 0x40 on nothing; vanilla 2.5% of exterior tris = roads): offline A/B
  (preferred_ab.py, tris under exterior pathgrid edges flagged): neither stall route changes -> dropped.
  Authored pathgrid passes 5 from the X0Y6 stall (edge 2126,25734 -> 2281,25931) and 417 from the rock gap.
  LEAD: both stalls sit on SHORT edges (engine_check: mesh ends short of walkable floor; tri 35 SHORT64; 11
  SHORT32/64 edges within 60 of X0Y6's stall, several made by my padded pin notches) with followers 41-52
  behind = contracts doc: smoother adds actor obstacle circles, a corridor short of its walls leaves no room
  to detour -> smoothing fails. diag_nofollow.sh: same walks with the followers DISABLED x3 each (inst 2).
- sohn_home on the doors5 import: cave exit 023864BD still no door tri -- its origin is 520 ABOVE and 337 from
  its partner's XTEL landing (127597 54533 5359), which lies on a 536-tri pocket (lookup not exact) with no way
  on; authored pathgrid there = 25 nodes, all in the pocket; nothing down the ~27 deg mountainside (gap 4364,
  2235 down). Grignir's start = an exterior ISLAND of 2769 tris (x 163-180k, y 74-88k; the shore/plateau climb
  IS connected); its only neighbours are sea floor 80-4000 below; cells 41,16 / 41,17 south of it unmeshed. Both
  authored routes (frozen waterfall / woods by the sea) cross pathgrid-less exterior ground the generator never
  meshed. Fix = big pins over whole cells, or generator coverage (user's call). Not touched.
  CORRECTION (user): the pocket IS beside the walkable (27 deg) slope; slope limits are 46 deg (MAX_SLOPE_DEG)
  / 34 per 32 step (seeded flood). REAL CAUSE: every pocket pathgrid node stands 78-576 ABOVE the LAND (the
  mountain surface is rock/snow statics over buried terrain) -> not stands_on_land -> grow.cpp marches a FLAT
  rail (level = node z: floor must stay within MAX_CLIMB 34 -> ~70 on a 27 deg slope) and spends the 160
  built-floor cap (statics are not LAND). User believed the cap is interior-only; in code it binds any non-LAND
  floor. Arktwend: 3740 / 15989 exterior pathgrid edges (358 / 775 cells) have a node > 34 off the LAND.
  Pathgrid cells are never seeded, so nothing else grows there. Generator fix = prototype + A/B (needs approval).
- diag_nofollow: tate WITHOUT Tate 1/3 (stalls at the rock gap -1665 27179 and X0Y6 2192 25756) ->
  followers-as-obstacles theory WRONG.
- USER (11-08): nembrest + Calisto may get a FEW high-level steers around the obstacles. Old chain (107 steers)
  is in the staged copy (git show :path; HEAD is stale). Both cases now: walk to -1641 28801 742 (x2 + bye if
  open), pace 10, 6 steers (-1145 27342, -1532 26162, 1994 26389, 2270 26119, 1987 25499, 1729 24498), pace 25,
  then the destination walks. ark_obstacle_steers.sh x3 each (inst 1).
  RESULT 0/6: long straight steers between the old chain's turns hit the hill (stuck at -1474 26606 / -206
  26282). v2: steer -1145 27342, WALK 1994 26389 (x2), 6 steers up the X0Y6 channel on the old line (2270 26119
  .. 1729 24498) -> rerun x3 each.
- GROWTH PROTOTYPES (temp/.../ground_ab.py, flood_ab.py; production untouched):
  ground grid (buried LAND points raised to the lowest standable surface) -> pocket cells grow only a little
  (193->249, 250->361, 88->159); many pocket nodes stand 30-576 above UNburied LAND (rock tops over open
  terrain). collision_flood.py (real collision, step 64, <= 47 deg, from the landing at floor 5250): 11459
  points, down to z 4151 -> a walkable descent EXISTS; the generator never meshes it because pathgrid cells
  never get the seeded flood. FLOOD prototype (exterior pathgrid cells also run seeded_graph from their nodes,
  unioned with the pathgrid graph, door joins as shipped): SlateMountainsX30Y12 193 -> 929 tris, ONE component
  z 4150..5286 reaching all four cell edges. Ground grid + flood together is WORSE (fake LAND soup) -> dropped.
  Full Arktwend exterior A/B (f_ark_cmp.txt): 724 of 775 exterior cells change (653 grow), area 3498 -> 5328 M
  sq units (+52%), tris 306k -> 467k, build 5.9 -> 19.2 s; 8 cells LOSE > 2% (NordwindXN12YN5 -27%: flood
  lines act as extra centerlines that stop rails). Scratch-ESM import (flood_import.py) died on memory while a
  game ran -- not retried.
  OPEN QUESTION (user 11-08, parked): exterior pathgrid cells never get the seeded flood, so walkable ground
  away from the pathgrid (rock-static mountains especially) stays unmeshed. Too many cells affected for now;
  revisit later (fix the 8 shrinking cells, scratch ESM for routes + engine_check, all plugins' A/B).
  For now: PIN sohn_home's route in Arktwend.
- tate_nembrest 3/3 + theater_calisto_house 3/3 with steers v2 (obst_* in rc). Followers theory dropped
  (theater alone 2/3 too).
- sohn_home DESCENT PIN: route_down.py floods real collision (step 64, <= 47 deg) from the cave landing
  (127597 54533, floor 5250) to the nearest tri of the destination's component: 168 points, ends (120864 51537
  3974) via cells SlateMountainsX31Y13 -> X30Y13 -> NordrenX29Y13 -> NordrenX29Y12. TOOL FIXES on the way:
  index.CellCtx: a pathgrid-less exterior cell was read as an interior (no LAND collision) -> exterior/origin
  from XCLC; pin_region: flip_once skips a degenerate one-vertex edge; shipped_mesh falls back to the NAVM with
  the most tris in the cell's own square (cell with no mesh in the box); without_near_new drops a point on top of
  the previous one. route_pins.py (temp) splits a route per cell (corridor = route simplified 40, buffer 96,
  clipped to the cell; floor_points --route; pin_region --route). 29,12 pinched an old vertex (120705 51585)
  -> route extended onto it + --route-band 200 200. Pinned (backup pins.pre_sohn_descent.json): 31,13 257 tris
  min 5.0; 30,13 1432 min 7.6; 29,13 1151 min 26.1; 29,12 692 min 9.2. Import ark_import_sohn_descent.log.
  Landing <-> target then connected, but the cave EXIT door still had no tri: its threshold (127859 54745 z
  5122) is inside the rock face; walkable floor ends ~136 from the landing (cliff to 6500+), nearest pin tri
  ~230 > DOOR_LINK_MAX_DIST 220. GENERIC FIX (import): worker._at_landings -- a teleport door still without a
  Door Triangle after build + joins is rebuilt with its triangle picked at the nearest arrival landing within
  DOOR_LANDING_REACH (from_pgrd.convert_PGRD door_spots / at_spots, used only for door picking; kept only if
  it links MORE doors). Tests 115 pass. Arktwend door-less 137 -> 128. A/B harness without pins: 0 cells
  changed (joins already link those) -> blast radius on other plugins STILL TO MEASURE before they ship it.
  Seeded cells re-grow from the new pin seams: 29,13's and 29,12's pins overlapped their new own mesh ->
  both pins DROPPED (route holds without them); 30,13 re-planned (route band 200) + a box pin filling an 88x50
  hole kept over walkable floor (pin_region keeps old holes) -> mesh_clean 0/0/0/0 in all four boxes
  (ark_import_descent4.log). Plan: Grignir -> crevice -> Ice Flow -> Slate Cavern -> Northron OK.
  sohn_home x3 (before re-plan, one walk): 0/3, walk entered Ice Flow and stood still at (-1140 -771) x2 /
  (-373 930) on mesh. CASE (rule 6 long multi-cell path): walk to each cave door + activate (0578DEC7 crevice,
  05DDBAB8 Ice Flow -> Slate Cavern, 05B23937 exit), then the walk home. sohn_descent.sh x3 (inst 1).
  Landing-link blast radius (door_join_ab on3 vs on2, no pins): Morrowind 0/1485 cells, Nehrim 0/2989 (8
  Schattenruf protected unchanged), Arktwend 0/1164. Oblivion still to run (heavy; when no game runs).
  Door-by-door case x3: 0/3, all in Ice Flow, the player SHUTTLING between (-484 937) and (-437 866) for
  minutes (no object within 900; follower Mauragin standing at (-531 902) behind) on a stretch with SHORT32
  edges both sides. Also the hole box pin had broken 29,12's re-grown mesh (route gone) -> re-pinned the LOW
  stretch only (sohn_down_low.txt, width 80, min 6.9). ICE FLOW PIN (backup pins.pre_iceflow.json): route
  (-832 824 .. -87 740) width 72 (all floor), --route mode. TOOL FIX pin_region: an old ring vertex is set onto
  the floor only when a floor point lies within FLOOR_REACH 48 (far vertices were moved onto extrapolated
  heights, up to 46 wrong). 442 tris, min 9.1, mesh_clean 0/0/0/0; all descent boxes 0/0/0/0; route ok.
  sohn_descent.sh x3 again.
  RESULT: all 3 got THROUGH both caves; stalls: 2x a PIT at (125866 54361) -- the corridor's south rim in 30,13
  hangs over a lower level (5120); the walker drops in, no mesh -> notch y < 54395 over x 125700..125990
  (pins.pre_pit.json backup), 34.7 deg; 1x (98567 9167) NordrenX24Y2, shuttling with Mauragin 44-100 away on
  a mesh 80-176 wide over open ground. Next batch: 1x X24Y2 again (98456 9219), 1x walk never left the start
  (167236 81528; the same start walked fine in 8 other runs), 1x into the old pocket's lower level, dead end
  (125832 53751). CASE (rule 6, long multi-cell): legs after the cave at the descent's turns (125453 54502 5028,
  120864 51537 3974, 96068 8603 1326) then home. Pins NordrenX23Y2 (route x24y2_route.txt, width 96, min 11.9)
  shipped clean. NordrenX24Y2 CANNOT be pinned: it is a SEEDED cell -- any pin touching its border changes the
  seeds it offers, the neighbour re-grows and so does it (12, then 124 overlaps); its removable tris span to the
  border (98304) -> no X24Y2 pin. GENERIC LESSON: pin seeded cells only away from their borders, or not at all.
  With legs: 3/3 pass every leg to (96068 8603); then all stall ~200 E of it (engine heads E, route goes W)
  -> legs at the next turns (94825 8194 / 95936 5759 / 98304 4352). Then 3/3 stall at (96726 4099 39) = the
  FOOT OF A PLANK WALKWAY (ex_de_shack_plank_04 x2, two planks wide, top 39): player dead still, Mauragin 44
  AHEAD of it on the planks (blocking). Same family as Ice Flow (follower + narrow way). CASE: leg to the
  walkway's foot (96650 4180 52) + wait Mauragin < 400 (closes up behind) + leg along it (97560 4122 43).
  OPEN generic question for the user: followers ending up AHEAD of the player in narrow ways.
  -> still 3/3 there with Mauragin BEHIND: follower theory WRONG for it. Real cause: TWISTED tri 249 (corners z
  3 / 49 / 63) under the plank tops (39) = mesh under a plank; box pin FelslandX23Y1 sets its 3 corners onto
  the plank (6.7 deg). The follower wait removed from the case (legs to the walkway's foot/end kept).
  Next 3/3 stall at (98300 4338 332), the X23/X24 seam (x 98304): the seam link led into a ZERO-AREA tri
  (TrollMountainsX24Y1 NAVM 021596D6 tri 507, all corners on x=98304, a 32-unit fold kept because
  drop_degenerate_guarded keeps a degenerate tri that bridges its neighbours). Arktwend has 96 zero-area tris
  (68 navmeshes), 5 on seams -> generic defect noted, not fixed. Box pin X24Y1 (both cells have pathgrids =
  stable): seam now links a real tri. sohn x3 again. RESULT: SOHN_HOME 3/3 (first passes on the authored route).
- RUN 23 Arktwend lane started (inst 1, run23_ark.out): tate/theater steers v2, airship walks, sohn_home door
  legs + descent pins, all Arktwend pins/landing-link import (ark_import_seam24.log).
  RESULT run 23: 4 fails, rest + side 6/6 pass (tate, theater steers, airship, baron, shaft, herbert_house ok):
  * herbert_meet: Herbert (HerbertScript State 1 -> 2 within 400) never close -> rule 7: `approach 054CBD39`
    after the Position. r23_herbert_meet 3/3.
  * theater_calisto_house: Wache01 greeted BEFORE Wache02 (07:44:56 / :59) -> no follow (the run-18 race):
    the walk's end (-343 49200) lies inside BOTH greeting ranges (W02 at -343 49129 <= 500, W01 at -448 49336
    <= 400); at 25x the scripts race. CASE theater_guards: walk stops at (-343 48700) = 429 from W02, 645 from
    W01. r23_theater (guards..calisto) x3 running.
  * sohn_home: first walk never moved (2nd time). Player took travel slot 1 = PACK 051D1C16, the SAME PACK
    its previous walks (sohn_worldend) ran and finished; GetCurrentPackage was 0 at the order, so slot rule v3
    did not apply; the engine keeps the finished procedure. FIX (runtime, FillAiSlot): g_lastAiPack[actor] =
    the PACK it was last put on stands in for "running" when none runs. Built (dist). lastpack.sh:
    sohn_worldend..sohn_home chain x3 (inst 1). RESULT 2/3: the start still stalled once -> last-pack theory NOT
    sufficient. Real lead: the start (167236 81528) is at the mesh's SW EDGE (mesh reach 0 to SW/S, walkable floor
    1249-1270 there) and the walk heads SW. PIN FrozenWildernessX40Y19 (backup pins.pre_grignir.json): extend
    "167000,81300;167250,81300;167250,81520;167000,81520", box 166850 81150 167450 81700, band 1150..1330, min 6.4;
    import ark_import_grignir.log 0 errors, mesh_clean 0/0/0/0, start->crevice + full route plan ok. Leg 94825
    8194 1043 was off-mesh -> 94960 8289 1116. lastpack2 (worldend..home) 3/3.
- RUN 24 Arktwend lane started (inst 1, run24_ark.out): all run-23 fixes in.
  RESULT: main 189/191, side 6/6. FAIL baron_house (steer stuck short of 3257 54857, player at 3624 54853 467)
  and sohn_child (child never < 600). Both = old steer chains with a follower along -> RULE 17: baron_house's
  ~60 steers -> walk to the guild door, walk off the landing, walk to the harbor gate (pole wait kept), walk to
  6480 59100 (x3 with bye if open); sohn_child's 38 steers + pace 10 -> one walk to 121700 -37350 5523 (x3).
  Offline plan ok for every walk. Backups 0740/0790.pre_r24.mwtest. r24_fails.sh x3 each (baron inst 2,
  child inst 1). RESULT baron_house 3/3; sohn_child 0/3, all stuck on the cliff-path climb (132661..132691,
  -37596..-37758, z 291-366; mesh 35-43 deg there) -> `pace 10` restored (rule 4 climb exception, the old
  case's reason) -> r24b_sohn_child x3: 0/3 -- the AI walk climbs to z ~345 on the 41-42 deg face (collision,
  coll_here.py/slope_map.py), slides back to 250, repeats, then stands still; steering crosses the same face.
  OPEN QUESTION (user 11-08, parked): why does the AI-walked player slide on a 42 deg face with navmesh (generator
  46, controller ~47) that the steered player climbs? Next measurement: AI walk speed/movement state vs steer.
  sohn_child back to its OLD steer chain + pace 10 (user: "continue with the old chain").
  Run-24 child failure (old chain): the player climbed fine; the child froze for good ~(132330 -37540) when
  NordrandWache03 force-greeted (09:42:24) at 402 from the player; in child_pace10 passes the same greeting
  comes and she catches up after goodbye. Rerun x3 (r24c) before changing anything.
  r24c 2/3; fail = the child stops for good ~300-390 behind once the player passes x ~132400, i.e. ON the
  41-45 deg wedge SW of a rock (x 132610-132740, y -37530..-37635; rock 132720-132760 x -37450..-37580) = the
  same slide the AI-walked player had (the child is AI too); the guard greeting is incidental. The AI walk held
  38-41 deg below the wedge and slid on 41+. PIN NordrandX32YN10 (backup pins.pre_child_wedge.json): notch =
  every collision cell >= 41 deg in 132280 -37900 132830 -37400 (steep_notch.py, step 10, pad 8, simplify 10,
  5 parts), floor points step 30, box 132200 -37920 132880 -37350 band 200..760, margin 40: 210 tris, min 6.6
  (a wedge-only notch left the route on another 41-45 band at y -37800). + a small pin over a PRE-EXISTING
  fold/overlap at (132100 -37990) on the route (box 131990 -38090 132190 -37930, 8 tris, min 25.3).
  ark_import_child_corner.log 0 errors; mesh_clean 131900 -38150 132950 -37300: 0/0/0/0; route now crosses
  38-41 deg collision only. r24d_sohn_child x3 (inst 1, old steer chain): 3/3. + r24e x6 (inst 2): 6/6 -> 9/9.
- RUN 25 Arktwend lane started (inst 1, run25_ark.out): baron_house walks, scout defend, child pins.
  RESULT main 186/191 + side 6: baron_hideout (GAME EXITED), theater_guards (first walk after the resume
  load never moved: slot 1 PACK 051D1C16, player still at its Position spot 5 min = the sohn_home start
  signature; passing runs ALSO take slot 1 051D1C16 first -> not the slot choice alone; the save differs),
  gargart_cell (Rakhissh 336 > 150), goths_camp (no force-greet). r25_fails.sh x3 each from the lane ckpts:
  gargart 3/3 (one-off); goths_camp 2/3 = REAL: VGothsLagerGesprach01 = ForceGreeting at GetDistance Player
  <= 500 on VGothWache03 (ref 05A96921), who in the fail joined the fight with the camp's attackers ("guard
  05A96921 sets on ...") -> rule 7 CASE: `approach 05A96921` after the Position. r25b_goths_camp x3 (inst 1).
  * theater r23 0/3 with the new stop (-343 48700): player never moved -- that point is OFF the mesh. Walk now
    `walk -727 48903 208` (on mesh, 446 from W02, 515 from W01 -> only W02's range). r23_theater 3/3.
  * enderal_burg_scout: walk 3 (scout spot -> gate landing) went SE/downhill for 5 min to (144761 -147201);
    planner's route NW (3536) climbs triangles of 48-86 deg (15 tris > 47 in the box); a real-collision
    walkable route exists (route_down GOAL_POINT, 70 steps W along y -145735 then N along x 140783, 28 of its
    points unmeshed). Pin attempts (corridor + steep notch) gave 1.9-4.2 deg slivers / 5 parts; notching the
    whole box would drop 37 tris (mostly 1-39 deg, 250k area). NOT shipped; scout stays OPEN (passes ~50%).
    REREAD of the run-23 log: the walk DID reach the climb's foot on the planned route (142658 -145896), then
    turned SE; creature 057C86E0 targeted the player from 08:03:28 (307 "fights" lines) and the player drifted
    toward it, both ending ~600 apart, frozen. The r19 fails had 0 attackers (gate-door mode, not seen since the
    split fix). route_slopes.py: planned route tris <= 41 deg (offline floor here unreliable). CASE: scout +
    `defend`. scout_defend.sh (gate..camp x3, inst 2): 3/3 (5-6 attackers per run, all killed).
- RUN 21 MW lane: CLEAN 443/443 -> MORROWIND LANE: 2 back-to-back clean runs (20, 21). Morroblivion already
  2 (16, 19). Remaining: Arktwend lane.
- Door-join + split blast radius MUST be measured on every plugin before shipping beyond Arktwend
  (door_join_ab.py / door_join_blast.sh; protected cells ImperialDungeon*, Schattenruf*).
- RUN 15 Morroblivion lane: main 10, quiz 4, necrom 19, tr_telvanni 88/89 (Vaerin passed in-lane). FAIL
  ht_ra2_contact: TR_M1_Q66_Ra2_Guard_Scpt (state 6 -> StartCombat the contact, who has DATA.Health 1, no script)
  -- the GUARD (0978D9FC, base health 244) dies the same tick the fight starts, 250 from her; journal 20 never.
  Rerun ra2_r15 1/3 (the pass: she died). TEMP: SetCombat logs both fighters' health -> ra2_hp x3 (inst 1).
  ra2_hp 3/3 + ra2_hp2 4/4: guard 244/244 and contact 1 at the fight's start every time; "same tick" deaths are
  ~25 GAME s at the pace (not instant). Only the contact ever targets the guard -> she (Nightblade lv 1, 21 spells,
  1 hp) kills him in the fails; in 3 of the 4 passes he dies ~1 s after her (her lingering spell, inferred) but his
  greeting set journal 20 first. Fails 2/11 overall, none before run 15 -> A/B on the run-14 DLL (pre door-lock /
  alarm), instance 1's plugin swapped by copy: ra2_olddll x4.
  RESULT ra2_olddll 4/4 and the GUARD NEVER DIED (0/4) vs 6/11 guard deaths on today's DLL -> REGRESSION from
  today's DLL; suspect the alarm fix (inferred: with the old flag she treated the player as an enemy, so not only
  the guard; now she fights the guard alone and a lv-1 Nightblade beats a lv-25 guard). Her 21 spells being
  resolved (haiku) to compare their damage with MW. Instance 1's plugin is the run-14 COPY until rebuilt.
  Her spells (Morrowind_ob 0106xxxx, converted 1:1 -- cost/magnitude/duration/area identical in the output): damage
  = 3 shock spells (shockbloom 13x5, lightning bolt 30x2, lightning storm 6x10, area 10, ~60-65 per cast); also
  invisibility x2, reflect 15, silence, burden, absorbs. 4 casts kill the 244-hp guard, so her winning is possible
  on the authored data; the old DLL's player-enemy flag distracted her. NOT a record conversion error found. OPEN
  (not rigged): watch its rate in the next lanes; if it recurs, compare OpenMW's AI choice (does a 1-hp lv-1
  caster cast at range or flee) before changing anything.
- RUN 16 Morroblivion lane (door-lock + alarm DLL): CLEAN 122/122 (main 10, quiz 4, tr_telvanni 89, necrom 19;
  ht_ra2_contact passed). RUN 17 Morrowind lane (MW->BM->Tri) started on inst 1 with the same DLL (run17_mw.out).

## RUN 9 Morrowind lane (11-06): ONE fail -- tr_shrine_dead
- ingame 187, morrowind_main 97 (b2 passed), bloodmoon 18, raven_rock 16 (co_supply_ship passed), brawl 3,
  mournhold 11, story 16/17, temple 70, daedric 24.
- tr_shrine_dead: Gardens walk (to -1173 5997) froze at (394.5 -1611.8 -394.5), ~8 units OFF the north edge of
  the pinned raised walk (gardens_stall3.png: no navmesh north of the walk's edge line; grey floor there);
  steps 25-34 ms. 3rd Gardens stall (after 337 -2245 and the pinned 368 -1698). mesh_clean box clean.
  Rerun x3 (rc/urvel_r9): 1/3 -- fails at (377 -1609) again + the old (325 -2257) spot. CAUSE (377..394 -1610):
  the floor NORTH of the raised walk (x 260..630, y -1640..-1450, z -377..-425, gentle, no objects; water -600)
  had NO navmesh; the walk's path drifts a few units onto it and the AI walk freezes off-mesh. PIN (backup
  temp/.../Tribunal.esm.pins.pre_gardens_north.json): floor_points box 250 -1660 620 -1440 band -440..-360 step
  40; pin_region box 300 -1650 670 -1430 band -440..-370, --extend "301,-1600;366,-1640;485,-1591;605,-1544;
  617,-1521;631,-1511;625,-1450;300,-1450;262,-1560" (the walk's open-edge chain + the floor), margin 20: removes
  19, builds 84, min angle 16.1. SHIPPED (trib_import_gardens_north.log); mesh_clean 200 -1750 750 -1350:
  0/0/0/0 (107 tris); gardens_north_pinned.png: new tris -5..+7 of the floor. -> rc/urvel_north x3.
  The (325 -2257) stall: next if it recurs (2 of ~25 runs).
  RESULT urvel_north 3/3.
- Arktwend run-8 steer cases at default under load (ark_run8_fails): shaft 3/3, child 2/3 (until 3000), picks 2/3
  (steer -963 1800), demon_king 1/3 (walk 2690 900 11221 x2) -> demon_king `pace 10` x3 (demon_pace).
- Door bug, TEMP ActivateHook log built: probe A now TELEPORTS 3/3 (claimed click -> script's Activate -> original
  ran, returned 1, twice: script pass 1 then the follow-up). The first probe-A failure was intermittent. Also seen:
  two NPCs (0552AFC2, 0546339F) activating the OUTER gate 05BC8A49 repeatedly. Chain scout->camp x2 with the log
  running (scoutcamp_log) to catch the real failure.
  scoutcamp_log 0/2: run 1 -- the gate DID carry the player (original ran x2, as in the probes); camp's walk
  from the square to the wall top then stalled on the low ground (139569 -142836) = the battle's long detour
  in reverse (115 mesh_route points round the fortress wall, several cells) -> the user's leg exception:
  CASE camp: 7 legs at that route's turns before the wall top (as battle's return). Run 2 -- the scout walk to
  the landing ended 517 from the gate with followers (guard 512; the landing is ~400 from the gate and a walk
  ends within 128): scout's walk target -> tri 317 (140652 -143591 5650, ~290 from the gate). The door bug
  did NOT reproduce in 4 logged tries -> watch it; the TEMP log stays until the chain is green.
  -> rc/scoutcamp_legs x3.
- dwarves_demon_king `pace 10`: 2/3; fail = walk to 2816 4224 stuck at (2867 4794 11517) on the Grul canyon bridge
  corridor. floor_points: a raised block 50-80 above the deck (x ~2852..2897, y ~4705..4775, top 11552-11582)
  that the corridor mesh runs OVER (slab_map tri "39" at its edge). PIN (backup temp/.../Arktwend_English.esm.pins.
  pre_bridge_block.json): floor_points box 2800 4680 2980 4860 band 11400..11650 step 15; pin_region box 2780
  4690 2915 4800 band 11440..11560, notch "2850,4700;2900,4700;2900,4780;2850,4780", margin 12: removes 18, builds
  43, min 19.4, 70-wide passage west of the block. SAVED; Arktwend import waits for instance 1's chain.
- scoutcamp_legs 0/3, with the TEMP log: run 1 the gate carried the player (original returned 1 twice), camp then
  stalled at leg 138888 -141658; runs 2-3 the door's ORIGINAL Activate RETURNED 0 (no follow-up, no teleport).
  Probe diag_gate6 (Position at my new scout target 140652 -143591, ~290 from the gate and 350 below it):
  original returned 0, 2/2. From the landing it returned 1 every logged time. => the ENGINE declines the
  door's activation from that spot (cause unknown; OpenMW's script Activate always carries). My closer target
  caused runs 2-3 -> REVERTED to the landing 140730 -143673. The 517 guard failure: the guard measures from the
  door's ORIGIN (scale-2 gate, origin 256 above the landing: landing is 472 away in 3D) and a walk stops up to
  128 short -> kDoorReach 512 -> 512 + 128 (test_runner.cpp; unbuilt, MW run 10 holds the DLL).
  The intermittent no-teleport from the landing (first probe A) is still unexplained -- watch with the TEMP log.
  Arktwend reimported with the bridge pin (+ the Recover MGEF fix): ark_import_bridge.log.
- Bridge pin shipped: mesh_clean 0/0/0/0, demon_bridge_pinned.png (mesh stops at the block, deck 0..-11).
  demon_bridge 0/3: all at the LAST walk (to the door landing 2690 900 11221), frozen at (2741 1106) the whole
  walk = the canyon corridor (my old pin) ends in a cap at y ~990 and the door-landing mesh below it never
  joins (mesh_route: no path; 25-47 gap, flat 11221). Earlier passes = luck. PIN (backup temp/.../Arktwend_
  English.esm.pins.pre_grul_door.json): floor_points box 2590 890 2810 1090 band 11150..11300 step 45; pin_region
  box 2600 890 2800 1040, --extend "2716,1024;2722,1004;2738,991;2756,988;2772,994;2784,1007;2763,931;2728,983;
  2676,1018" (both open-edge chains), margin 35: removes 15, builds 22, min 9.5 (margins 10/30 + step 20 grids
  gave 1.9-3.4 slivers). SHIPPED (ark_import_grul_door.log); mesh_route joins (14 points); demon_door_pinned.png
  all 0; mesh_clean 021DB3B7 0/0/0/0. TOOL FIX: mesh_clean crashed (GEOS) on zero-area triangles -> they now
  count as slivers and stay out of the shape ops. -> rc/demon_door x3: 2/3; fail = frozen the WHOLE last walk
  at (2768 1153) in the corridor ~150 above its end (route exists now). Open.

## RUN 10 Morrowind lane (11-06): 4 fails so far
- chargen_captain again "MessageBoxMenu holds": only ONE "Press E" box opened after the door; courtyard's 4
  button presses match its 4 boxes. Theory (unproven): the `button 0` press came in the box's opening frame and
  was lost. Runner change (unbuilt): `button` presses only after the box has been up kBoxSettleTicks (2).
- a1_v_levels: GAME EXITED after 18 result lines (silent exit; see crash notes). ms_warlords: `talk Olvyne Dobar`
  (line 68, Vacant Manor, right after the Dovor talk) -> "never loaded" (passed 3/3 before). tr_blade: the wait
  for "Torasa Aram" < 2000 in the Museum of Artifacts -> FLT_MAX: never loaded there; no Tribunal script moves
  her. All three: rerun x3 on the next DLL (button settle + door reach 640) before diagnosing.
  RESULT: captain 3/3 (button settle works), a1_v_levels 3/3 (exit = one-off), warlords 2/3, blade 0/3, fortress
  chain 1/3. tr_blade probe (diag_torasa, from mwtest_tr_missing_hand): Torasa ENABLED, alive (207), at
  (-488 -2560 294), GetDistance FLT_MAX = another cell; her only package = wander radius 0 at her placement; talked
  to once (ms_museum 21:46:02, then the player PositionCell'd away). Carry class. CASES (user: approach it):
  tr_blade `approach 06D497DB`, ms_warlords `approach 062E6218` (x2), co_supply_ship `approach 06223A46` ->
  approach_check.sh. User also: "or find out the root cause why npcs are moving from their authored locations"
  -- the carry root cause is the real fix; the approach run logs which cell Torasa landed in (next clue).
- Fortress chain x3 (fortress_chain): run 2 PASSED scout+camp+battle (first whole-chain pass); run 3 = the door
  declined (original returned 0) at (140785 -143780) where run 2's returned 1 at (140780 -143771) -> not the
  spot; the engine declines intermittently. Run 1 = scout walk to the landing stalled at (142579 -147243) (old
  SW-drop spot). FIX (runtime, unbuilt): ActivateFn returns bool (Papyrus Activate's result); a SCRIPT's Activate
  of a door retries each frame up to kDoorActivateTries (120) while the engine declines (OpenMW's script Activate
  always carries). game_calls.cpp ScriptDoorActivate.
- approach_check: co_supply_ship 3/3; tr_blade 0/3 + warlords 2/3 -- `approach` did NOTHING: Rolf (Bloodmoon's
  rolfScript: AiFollow Player forever) is a slot follower in MW_ALL, so "never with followers" refused although
  he stands in Ebonheart, thousands away. FIX (runtime): PlayerFollowersAlong() = followers in the player's place
  within kFollowersAlong (800, OpenMW's teleport follower range); used by approach, the door-reach guard
  (followerCount/followerNames) and travel fares (OpenMW counts only those too). Built with the door retry.
  along_check.sh: blade/warlords x3 (inst 2), fortress chain x3 (inst 1).
  USER: "A previous quest should not be breaking later quests. having a follower from an old quest should have
  zero bearing on the current quest." Audit of every PlayerFollowers() use: also switched to PlayerFollowersAlong
  -- KillPlayerAttackers (defend/follow killed attackers of a far-away Rolf) and TravelTo (travel pulled Rolf from
  Ebonheart along; OpenMW takes only nearby followers). Kept: FollowersThroughDoors (already same place <= 800),
  the spacing hook (only shapes the follower's own walk), TEMP list log. UNBUILT until along_check ends.
  USER: "would that not break if a follower you needed happened to be a little further away?" -- yes (Mauragin
  906, Sinnammu 815 behind). Split: PlayerFollowersAlong (<= 800, OpenMW's travel/door rule) for travel, fares,
  doors; PlayerParty (same place within one cell, 4096) for approach, the door-reach guard (followerNames) and
  KillPlayerAttackers -- a lagging current-quest follower counts, an old one elsewhere does not.
  along_check: blade 0/3, warlords 1/3, fortress 1/3. blade log: approach still refused -- Rolf stood 80 from the
  player IN the museum. ROOT CAUSE (log): after each script PositionCell of the player Rolf lands beside them
  (115 away in Barenziah's chambers, 79 in the museum) = Skyrim's Follow catches up across any teleport; OpenMW's
  script moves carry nobody. USER (asked): "Yes, OpenMW rule". FIX (runtime, game_calls_ai LeaveFollowersBehind/
  ResumeFollowsNearPlayer): before a script move of the player (MoveInto, SendToMarker, approach), every follow-
  the-player slot actor not within 7168 (OpenMW processing range) of the destination in its place leaves its slot
  (+ EvaluatePackage) and is marked paused (co-saved "ai|pausedfollow"); a repeat AiFollow Player stays paused,
  any other order ends the pause; GetCurrentAiPackage still answers follow; the tick refills the slot once the
  player is back within 7168 in its place. Travel's followers pause then resume next tick (moved along). Logs
  "stays behind" / "follows the player again". stay_check.sh: blade/warlords/urvel x3 (inst 2), fortress x3 (1).
  Risk: cases that `Position` the player with followers (Arktwend chapters VI+) now leave them behind (= OpenMW).
  RESULT stay_check: blade 3/3 (was 0/3; Rolf "stays behind" once per run), Urvel 3/3, warlords 2/3 (fail =
  Dovor "never loaded" at line 87 = no 3D in the Vacant Manor after the talk + 2 script moves = CARRY; the
  player's ExtraFollower list held 0 the whole run -> carry is NOT that list; TRACE logged no place change),
  fortress 0/3 (camp leg 138888 -141658: player never moves from (138899 -141145) -- see enderal_burg_*).
  Case: warlords + `approach 06EB7D8B` (Dovor ref, MW_ALL) before line 87's talk.
  USER (fact): vanilla Skyrim -- talk to an NPC, leave the cell quickly, and it follows you out the door. ->
  carry = that engine behaviour. Sonnet disasm asked for the mechanism (what selects the talked-to NPC, which
  moves carry it, how to clear it per actor). User: get the carry right; it is the persistent real bug.
  DISASM (sonnet; temp/carry_disasm/): the player-teleport carry = ID 41346 (0x76e900), called by the door
  teleport finish (40656), teleport cleanup (40661), a door-less cell move (~0x747d50) and the pending-teleport
  handler (40438); it moves (37648 -> Actor::MoveTo) every actor in the process lists passing bool(player,
  actor) ID 40493 (0x737220, call at +0x129): not disabled, ... and AIProcess+0x110 (follow target, set by the
  Follow/FollowTo procedures) == the player. INFERRED: talk's ActivateRef builds a type-0x2b package (shares
  Follow's handler) on the NPC -> sets that target. FIX (runtime, game_calls_move CarryTestHook): the call at
  +0x129 answers false for EVERY actor and logs "the engine would carry X with the player -- it stays" (OpenMW:
  doors carry followers <= 800, which FollowersThroughDoors does; script moves carry nobody). Dovor approach
  REMOVED from warlords (tests the fix). carry_check.sh: warlords/morale/urvel x3 (inst 2), fortress x3 (1).
  RESULT: hook installed ("call at ...EA29", "hooked") but "would carry" logged 0 times in all 12 runs; warlords
  2/3 -- Dovor "never loaded" again => the 41346 carry is NOT what moves the speaker (theory 1 wrong; hook kept,
  inert so far). morale 0/3 = MY ERROR: run on the MW_ALL build (Bloodmoon slot 07) with the case's slot-06 ref.
  urvel 3/3. NEXT: measure, don't guess -- runner "never loaded" now appends Hooks().whereIs (place, position, 3D,
  disabled, dead): where_check.sh = warlords x6 (inst 2).
  RESULT 5/6; the fail: Dovor IN the Vacant Manor at his spot, 3D 1, not disabled/dead, but NOT the player's
  place -- the earlier `approach 062E6218` had taken the player to Olvyne, who was in the TEMPLE COURTYARD:
  TRACE: 2 ticks after the player's script move to the courtyard, Olvyne (just talked to) changed place to the
  courtyard at (-4360 -2608) = EXACTLY the XTEL landing of the Godsreach->courtyard gate 01557022
  (near_spot.py); later speaker 06530413 too (-4418 -2594). => CARRY MECHANISM: the talked-to NPC WALKS AFTER
  THE PLAYER across cells (unloaded = door-to-door jumps), as the user said vanilla does (Hasphat on the guild
  exit's landing = same). Source (disasm agent, activateref.txt): ActivateRef 19796 calls the activation
  interaction 37864 (0x694c40; (activator actor, target, object, count, before)) at +0x53a (before the base
  Activate) and +0x6ea (after it returned true) -> builds a type-0x2b package on the target aimed at the activator.
  FIX (runtime, activation.cpp InteractionHook): both calls skip it when the PLAYER (not sneaking) activates a
  living Morrowind speaker; log "talks -- no Skyrim activation package". Teleport-carry hook (41346) REMOVED.
  talk_check.sh: warlords x6 + urvel x3 (inst 2), bm_morale x3 (inst 1, MW+BM build); measure = TRACE count.
  RESULT talk_check: morale 3/3 (MW+BM build), urvel 2/3, WARLORDS 1/6 (worse than 5/6): Olvyne STILL walks after
  the player (TRACE in all 6 runs: to the courtyard gate landing, or through 3 places in 1 s) although the log
  shows "talks -- no Skyrim activation package" -> 2nd FAILED carry theory (activation-interaction package) ->
  REVERTED. Olvyne's authored AI: one PACK, wander radius 0 (PKDT.Type 5), script nolore -> nothing authored
  moves her. NEXT = measure: TRACE now logs the speaker's running package (id, type byte +0x24) and AIProcess
  (+0xF8) follow target (+0x110) at each place change. trace_check.sh: warlords x3 + fortress x3 (door diag).
  RESULT trace_check: warlords 2/3; at EVERY speaker place change (Olvyne x4, 06530413 x3): package = her own
  authored one (0674CB42 = PKCU template DoNothing 000654E2: a lone Wait, no Travel), type 18, process follow
  target 0 -> no follow, no travel package moves her. Olvyne is PLACED in the Vacant Manor (ACHR 012E6218 cell
  01530900), so no "go home" either. Door diag: gate declined 120/120 with NO open/close marker (0x68 = none)
  -> the 0x68 candidate is wrong; fortress 1/3.
  Still standing (agent): ActivateRef creates ExtraInteraction (0xA9) on the NPC BEFORE the base Activate, and
  Actor::Update re-applies a 0x2b package while it lasts -- not touched by the reverted skip. TRACE now logs 0xA9
  on speaker + player at talk and at each place change: trace_check2.sh warlords x3.
  RESULT trace_check2: ExtraInteraction (0xA9) = 0 on speaker and player at every talk and every move -> 3rd
  candidate dead. MEASURE THE MOVER: TEMP hook on all 22 call sites of the move core 0x2eb8c0 (callx.py), logging
  moves of traced speakers. trace_check3: every carry = 5 core moves in one tick: 0x664AB7 (inside Actor::MoveTo
  0x664950) x3 + 0x27E08A (inside the DOOR's Activate code, 0x27da50..) x2 = the NPC USES the two load doors
  (manor -> Godsreach -> courtyard) at once = Skyrim's unloaded-actor path traversal. Something gives her a
  destination toward the player. Now an 12-frame stack per move (trace_check4.sh, warlords x2) to name the driver.
  STACK: teleport = 0x664ABC (Actor::MoveTo) <- 37877 <- 37892 (dispatcher by action type) <- 41766 <- 41717 <-
  41297 <- 69378; door = door Activate 0x27E08F <- ActivateRef 0x2EB2E0 <- 37878 <- 37892 <- 41717 <- 41297. =
  she FOLLOWS A MOVEMENT PATH whose door/teleport steps 37892 dispatches. Sonnet disasm asked: what is the path's
  goal (DoNothing package, follow target 0, no 0xA9), and how to cancel it per actor (agent ad309...).
  AGENT: 41717 = MovementControllerNPC (Actor+0x150) per-frame message pump (queues +0x158/count +0x168, timed
  +0x170/count +0x180); 41766 = one timed entry; 41297 = pops controllers; messages: NewPath, SetStaticPath,
  ApproachingDoor, ActivateDoor (= the door step), WarpToLocation (= the teleport step), PathComplete/Failed/
  Cleared. Cross-cell routing = TeleportDoorSearch, built from the AIProcess update 0x6d3c10 (switch on the
  package), BGSProcedureEscort and 0x7304f0. The GOAL's source NOT found. Stop-movement = ID 37817 (0x6934e0,
  (Actor*, float xmm1 -- meaning unknown)). NewPath ctor 0x11b9090 has 0 direct callers.
  NEXT measure WHEN she gets the path: TRACE path logs her queue counts + position (>20 moved) per tick with the
  player's last move tick (trace_check5.sh, warlords x3).
  RESULT: each player move queues ONE movement message on every recent speaker the next tick (no motion). Olvyne
  moved only after her 2nd talk + the next player move (tick 991): then strides ~500/tick across cells toward the
  player, and on the NEXT player move (1033) re-targets to the player's new place -> a PERSISTENT chase of the
  player's current location, outside her package (DoNothing) and follow target (0). Our runtime calls no Skyrim
  dialogue/look-at native on speakers. Sonnet disasm asked for the persistent per-actor chase state (where set
  on activation, where cleared, how to clear for one actor).
  AGENT a6bf: 37864 with no 0xA9 on the player -> 0x6992c0(player, npc): a created type-0x29 package (target the
  NPC) put on the PLAYER (vfunc 0x6f8), Actor+0x204 |= 8 on both, timer MHPD+0x204 = global 0x2013d48; 0x718e80
  counts it down, at <= 0 calls 0x673960(actor, 0) (clears bit 3 + timer). The UNLOADED update (0x6d9470) rebuilds
  AIProcess+0x114/+0x118 target handles each tick from the package in MHPD+0x60 (else AIProcess+0x20); goal = that
  package's location. Theory (unconfirmed): an override package in MHPD+0x60 aims at the player. TRACE chase now
  logs +0x114/+0x118 vs the player's handle, both packages (form, type +0x24, tree +0xd8), bit 3, timer
  (trace_check6.sh, warlords x3).
  RESULT (3/3 passed, chase seen in run 3): while Olvyne chases, AIProcess+0x114 = 00100000 = THE PLAYER's handle;
  MHPD+0x60 = null; package = her own DoNothing-templated PACK (type 18, tree 46); talking bit 0, timer -FLT_MAX.
  Dovor (same kind of package) +0x114 = 0, stays. => no override/talk package: her DoNothing package's TARGET
  resolves to the player (our PACK sets only ActualSeconds/StopMovement; location left at the template default).
  Sonnet asked how a tree-46 Wait with no location computes +0x114 unloaded, why the player, and the fix.
  AGENT a094: tree 46 never reads a location; +0x114 can come from the package's target (+0x40) or an extra 0x19
  record. trace_check7 (warlords 1/4): BOTH null on all speakers. NEW: during chases MHPD+0x60 sometimes holds a
  RUNTIME-CREATED package (forms FF000D4C/8C/8D) type 1 (FOLLOW) tree 43 = the engine makes a follow-the-player
  package for the talked-to NPC (= vanilla "follows you out"). TEMP hook on Character vtable (207886) slot 0x6f8
  PutCreatedPackage logs package + 12-frame stack for traced speakers (trace_check8.sh, warlords x4).
  RESULT: 31 creations of Follow (type 1, tree 43) packages for speakers Olvyne + 06530413, ALL with one stack:
  39300 (+0x1e3, puts it) <- 37367 (+0x1a9) <- 39632 (+0x2c2) <- 39637 (+0x747) <- 39108 (AIProcess update) <- Actor
  update. Sonnet asked which state along that chain picks the player and how to clear it per actor.
  ROOT CAUSE (agent a9ff, static; matches every measurement): 39632 = Skyrim's HELLO evaluator. When the running
  package has interrupt bit 0 "Hellos to player" (our MW wander/travel packages: interrupt_morrowind sets it for
  Hello > 0; Olvyne's PKDT interrupt 0xD5), the NPC detects the player within 150 units, and the cooldown
  mh+0x314 (20 s) has run out, 37367 (the hello APPROACH) builds a created Follow package aimed at the player
  (39300, tree 0x2b) and puts it on the NPC. Only the hello line being spoken ends it (mh+0x340 5 s timer while
  the NPC is the current speaker); no line spoken -> it never ends -> the NPC follows the player through doors /
  after every teleport. Talking = being within 150 = why speakers get it. OpenMW: Hello is a line said in place;
  actors never walk up. FIX (runtime, activation.cpp HelloApproachHook): the hello evaluator's call to the
  approach is repointed and skipped for actors in the Morrowind actor index; the Hellos flag (barks) is kept.
  hello_check.sh: warlords x6 + urvel x3 (inst 2), bm_morale x3 (inst 1); measure = 0 created packages, 0 speaker
  place changes. UNTESTED: whether Skyrim still speaks the hello bark without the approach.
  DOOR DECLINE disasm (sonnet, temp/door_disasm/): NO distance/LOS test on the player-activator path (the one
  geometric check 0x2eb5f0 is NPC-only). 0x747200 is SendPlayerToJail, not a door teleport (premise wrong).
  ActivateRef's player-relevant false exits: (6) the door has XTEL and its ExtraOpenCloseActivateRef (0x68,
  +0x10) is set; plus non-positional flag/name/process checks. Door Activate 0x27da50 for the player: false only
  on no process / mounted / locked-key fail. Inferred alternative: XTEL linked-door handle not resolving.
  TEMP diag (unbuilt, after talk_check): ScriptDoorActivate logs on its first and last decline whether the
  door's 0x68 marker is set (ExtraDataList::GetByType 12329 on ref+0x70) + raw handle.
  Fortress gate (fortress_carry 1/3, same 121 declines): the scout case's `until Enderalvar == 5` passes without
  the teleport. Sonnet disasm asked: every path where a load door's Activate returns 0 for the player, which
  depend on the player's position, and how to make a script's door Activate always teleport (OpenMW).
- tate_nembrest `pace 10`: 3/6 in all (tate_pace 2/3, tate_pace2 1/3), every fail at the SAME X0Y6 spot (2251
  25952..26009) with ~25 ms steps that climb elsewhere => 2nd failed attempt on the slope theory for tate (after
  the substep patch): WRONG. `pace` removed from tate. It is the X0Y6 navmesh corridor (see tate entry) -- open.
- DLL built (button settle + kDoorReach 640 + TEMP door log). settle_check.sh: captain/levels/warlords/blade x3
  (inst 2), fortress scout..battle chain x3 (inst 1).

## RUN 8 Arktwend (11-06, concurrent with MW run 9): 12 segments
- FAIL: tate_nembrest (station walk, with pace 10), theater_calisto_house (Wache01 wait), sohn_child (steer stuck
  131684 -38120), sohn_airship (steer stuck 4340 -1859 12748 -- SAME spot as run 7; 3/3 standalone; step ~12 ms
  at pace 10, so not steps: lane state), sohn_worldend (until 3000), sohn_home (steer 101267 4703), scout
  (door-reach guard: 05D5AC07 used from 517 with followers Calisto + Rakhissh), enderal_brother (cascade),
  dwarves_picks / dwarves_shaft (steer stuck), dwarves_demon_king (walk). Galleon end + arktwend_side pass.
- dwarves_shaft ran 49-63 ms steps (both instances busy -> lower fps -> bigger steps). User: per-case `pace`
  only, never a global cap. ark_run8_fails.sh: child/picks/shaft/demon_king x3 at default under load.
- Arktwend lane run 7 (instance 1): tate_nembrest FAIL (station walk), sohn_airship FAIL (steer stuck short of
  4340 -1859 12748); rest of arktwend_main to be read when the lane ends.

## Status of the last full run (RUN 6, 11-04) and since
- Passed whole: ingame 187, morrowind_main except below, raven_rock 16, brawl 3, mournhold 11, temple,
  daedric 24, arktwend_side 6. Morroblivion lane last run (run 3): all pass but nm_wake_start (flake).
- Open: b2_escort, a1_2_key, bm_morale, tr_assassins (carry), tate_nembrest, sohn_worldend, sohn_home,
  enderal_burg_camp/scout/battle, enderal_brother, tr_shrine_dead (Urvel), chargen_hall (flake).
- Instances: both MW_ALL, running the Urvel teammate A/B. crash_capture watchers from the previous session are
  still alive on both (a second watcher fails on their open pid file -- don't start more).

---------------------------------------------------------------------------------------------------------
# GENERIC MECHANISMS (cross-quest)

## Follower freeze -- OPEN (b2_escort, sohn_worldend, tr_shrine_dead)
- Symptom: a following NPC (pkg 3) stops 300-1100 behind a motionless player and never closes in; a 33-unit
  player step moves him the same 33 (mirrors the leader). Urvel 326-358 vs dulniScript <= 256 greeting;
  Mauragin 501-509 vs his <= 500 greetings; Sinnammu ~1122 in the Inner Shrine (journal 40 never sets).
- OpenMW rests a follower <= 226 (aifollow.cpp followDistance = halfExtents(target)+64+2*halfExtents(actor),
  stops at -30). Vanilla Follow template 00019B2C: Min 128 / Max 256, input 4 "Accompany?".
- Tried: radius 64/128 -> crowds, blocks the AI-driven player in a cave exit (reverted). Max radius 128
  (min 128) -> fixed Mauragin once (3/3), not Urvel; KEPT. Accompany?=1 -> Urvel 0/8 -> 3/3 but b2 2/5 and
  sohn_worldend failed -> reverted (vanilla player-follower template uses 0). console evp -> no effect.
  Re-evaluate after a door -> not decisive (3/4), removed. Combat-assist theory, AI-driven-leader theory:
  wrong (GetTarget 0; steered player same result).
- Vanilla: ~85 Follow instances set Accompany?=1, ~8 use 0; the 0s include FollowerPackageTemplate, whose actors
  are PLAYER TEAMMATES (engine catch-up). Ours are neither. Console A/B on tr_shrine_dead: unchanged 1/4,
  Urvel made a teammate after his goodbye 3/4 (every fail = Urvel greeting/journal 60).
  CANDIDATE (runtime, TEMP until verified): game_calls_ai.cpp SetFollowingPlayer -- a follow slot aimed at the
  player sets Actor.SetPlayerTeammate(true, false) (ids kActorSetPlayerTeammate 54970 = 0x9f56b0 on 1.6.1170;
  lookup method checked on EvaluatePackage 54672), cleared when the slot is released; log "is now a player
  teammate". Result: tr_shrine_dead 4/4 (was 1/4) + 5/6 (rc/urvel_rt2_*) = 9/10; the fail = no shrine
  greeting (player's walk reached 1895 -2272 with no greeting; passing runs are greeted mid-walk ~1500) ->
  diag_urvel/ probes his distance/package/teammate/pos at the walk end x6 (rc/urvel_diag_*); b2_escort 0/4 (journal 40
  x3, Outer Shrine steer stuck x1; teammate fired for Sinnammu) -> diag copy diag_escort/ (her distance, package,
  position 300 ticks after the last shrine walk) x3 (rc/b2_rtdiag_*): run 1 she FOLLOWS (202 from the player)
  but the player's walk ended 270 from the statue -> she rests 455 from it (> 400); run 3 she stands ON the
  Inner Shrine landing (4440 2512), 804 away, pkg 3 -- the ENGINE carried her (teammate; no runtime carry
  line), while in run 1 our runtime carried her and she followed. Run 2 = Outer Shrine steer leg stuck.
  REJECTED (user): re-placing the engine-carried follower on the landing = teleporting a follower to unstick
  her; reverted unbuilt. Why she stands after an engine door carry is OPEN (find the engine cause, no teleport).
  Case b2: + `walk 4412 3440 50` after `walk 4392 3389` -> 2/3 (rc/b2_walk_*): the fail's walk ended at
  3326 (211 from the statue) vs 3367 passing -- every `walk` arrives within kArrivedDistance 128, so she
  (~200 behind) lands past 400. + `steer 4412 3440 50` -> b2_steer_1 FAIL with the player 56 from the statue:
  she was NOT following -- "player too far from the statue" is WRONG (user: seen ~6 times on this quest; every
  attempt moved the player). Steer removed from the case. Urvel diag (probe values are in the .out "left"
  field): fail run 5 = pkg 3 (follow), 348 behind a stopped player (needs <= 256); passes 208/217/246/67.
  => b2 and Urvel are the follower not closing in, never the player's spot. b2_steer 2/3 (fail = the 56 run).
  Engine follow rule (sonnet disasm, 1.6.1170, partly inferred): BGSProcedureFollow init 0x467970 reads Min
  (default 150) / Max (default 128) into state+0x34/+0x38; tick 0x468f10: A = R+min, B = R+max (R from
  0x4161f0(actor, leader), undecoded); holds while dist <= B; "arrived" when dist < A; re-path at 0x469cc4
  KEEPS the old path goal while it lies between A and B of the leader and is reachable -> a 350 park is likely
  a kept stale goal, NOT a radius band (unconfirmed). No fFollow* GMST string in the exe; Skyrim.esm has
  fFollowSpaceBetweenFollowers 192, fFollowRunMinSlowdownMultiplier 0.75. Radius changes already failed twice
  (64/128, Accompany) -> do NOT retry radius.
  R DECODED (sonnet, 0x4161f0 = ID 26378): R = 0 unless the LEADER's ExtraFollower list (extra 0x1D, ID 11651;
  entries {handle, F} at +0x10, count +0x20) holds >= 2. Sorted (inferred: by distance to the leader); walking it,
  S += radius[i-1] + radius[i] + 100 (radius = max bound extent x scale x 0.5, min 16), S = max(F[i], S); R = S -
  F at the follower's own entry. Recomputed every tick; no GMST, no speed, no teammate flag. => Urvel's 348 =
  128 + ~220 = he is SECOND in the player's follower list; someone else nearer the player is listed. Our slots
  hold only Urvel (urvel_diag logs). TEMP diag DLL logs the list at every walk end ("TEMP the player's
  follower list"): follow_list.sh -> rc/urvel_fl_*, rc/b2_fl_*.
  FOUND (urvel_fl, 4/6 real: run 5 no greeting at 260, run 2 Sewers East stair walk): the list holds ROLF
  (0759B621, Bloodmoon trackerScript state 70 `"rolf long-tooth"->AiFollow Player` -- authored, forever) first and
  Urvel second; order = join order, not distance (Rolf listed first from another cell). Run 5 at the shrine: Rolf
  212 from the player, Urvel 422 -> parks 260 > 256. Our runtime carries Rolf through doors with the player.
  OpenMW has no follower queue. FIX (runtime, game_calls_ai.cpp): FollowSpacingHook at both call sites of
  0x4161f0 (0x468f10+0x6f8 = ID 28644, 0x6f50a0+0x580 = ID 39639): R = 0 for an actor our follow slots have
  following the player (set refreshed by TickPlayerFollowers in EffectTick; hook may run off-thread).
  Built; spacing_check.sh -> rc/urvel_sp (x6), b2_sp, tate_nembrest_sp, sohn_worldend_sp.
  b2 is NOT this: the list holds only Sinnammu; after the engine carries her into the Inner Shrine the list is
  EMPTY in passing and failing runs alike (her Follow procedure no longer lists her). TEMP log now also names
  each slot follower's running PACK + distance at every walk end.
  spacing_check (hook ON, teammate ON): urvel_sp_1/_2 FAIL in a NEW way -- Urvel attacked on arrival (Sewers /
  Gardens), fights (PACK 0) while the player walks on, left behind (`defend` only killed the PLAYER's attackers)
  -> runner: KillPlayerAttackers now also kills attackers of PlayerFollowers() (unbuilt until the batch ends).
  b2_sp_1 FAIL: Sinnammu runs her follow PACK 07BE381C but stands on the Inner Shrine landing (243 -> 814 as the
  player walks); navmesh landing -> route connects (mesh_route). Every b2 run: ENGINE carries her (teammate) onto
  the landing together with the player. Theory: the teammate candidate's engine carry (vs our carry after the
  player steps 128 off) strands her; the teammate change is not needed for Urvel (real cause = Rolf spacing).
  -> teammate candidate REMOVED from the source (SetFollowingPlayer, kActorSetPlayerTeammate). Next A/B: hook +
  defend-followers, no teammate: Urvel x6, b2 x6.
  spacing_check totals (teammate ON): urvel 3/6 (2 left behind fighting, 1 = 2nd AiTravel), b2 0/3, sohn 2/3
  (fail: Mauragin ALONE in the list, R = 0, runs follow PACK, 506 -> 570 from a stopped player = the same
  not-closing-in as Sinnammu), tate 0/3 (see tate). noteam_check.sh running: urvel_nt x6, tate_nt x3, b2_nt x6.
  noteam_check (hook + defend-followers, NO teammate): URVEL 5/6 (first 5 in a row; fail 6 = ?), b2 2/6 (3x
  journal 40, 1x Outer Shrine steer stuck). Teammate theory WRONG: the engine still carries Sinnammu (a follower
  whose Follow targets the player goes through load doors with them) and she stands on the landing -- in
  PASSING runs too (815 away at the last walk end, she recovers during the 1800 wait; failures don't).
  Re-evaluating after the carry already tried (archive fix 4d, 3/4; the fail walked BACK to the entry door) ->
  not retried. Lead: the Follow procedure's leader-went-through-a-door state survives the engine carry; sonnet
  disasm asked what stores/clears it.
  Disasm (sonnet, 0x468f10): NO cell/load-door handling in the update; state = proc+0x28 cached path (goal =
  leader RAW coords, no cell), +0x34/+0x38 min/max, +0x3c/+0x3f flags; the path is dropped only when it is not
  the actor's current path, the leader is inside min, or a new path request succeeds; re-path is skipped while
  the old goal passes a validity test (inferred coordinate-only). EvaluatePackage keeps the procedure; package
  START runs init 0x467970 (ID 28624). 30 s give-up only with +0x3c + leader > 2500 + no sight.
  FIX (runtime, built-compiled, link waits for the A/B): TickPlayerFollowers tracks each follower's PlaceOf
  (interior cell / worldspace); on a change, RestartFollow (posted) clears and refills its follow slot's actor
  alias -> the package restarts with a fresh path. Log "changed cell -- its follow package restarts".
  Distinct from fix 4d (EvaluatePackage). Test: b2 x6, Urvel x6 (diag_urvel2 probes the 2nd AiTravel), sohn.
  RESULT restart_check: b2 2/6 (restart fired, she STILL stands) -> 2nd failed attempt on the stale-follow-state
  theory = WRONG; RestartFollow REMOVED. Urvel 4/6 (2x 2nd AiTravel; diag probes never ran: a failed `until`
  ends the case). sohn 2/3 (fail: walk to 174334 87086 never reached).
  NEW CORRELATION (13 runs, b2_fl/nt/rs): the Inner Shrine's authored fight (fightNals/fightEigma: StartCombat on
  CellChanged, to the death) ENDS within ~1 s in EVERY pass (5/5) and is STILL GOING in EVERY fail (8/8). Theory:
  Sinnammu (not a combatant) stands while combat goes on near her (Skyrim bystander reaction; MW reacts only when
  attacked). Experiment: diag_fight = b2 + `console 0531274B.kill` on arrival, x6 (fight_check.sh); Urvel x6
  real case with a TEMP travel-slot position log (every 300 ticks).
  RESULT fight_check: b2 with the fighter killed on arrival 6/6 (rc/b2_fight_*) -> the authored fight going on
  near her IS what stalls Sinnammu (the kill is only the proof, not the fix; find the generic mechanism).
  Urvel x6 4/6: run 2 = his 2nd AiTravel never moved him (stood at the dais 2190 -2456 the whole wait); his 1st
  travel had taken slot 4 (PACK 07BECE6E), finished at once, and the 2nd was put in the SAME slot/PACK the same
  tick (passes: 1st ran in another slot). Same PACK = engine keeps the finished procedure. FIX (runtime):
  RunAiPackageOn tries a slot the actor was NOT just released from first. slot_check.sh -> rc/urvel_slot x6:
  5/6, the fail = the SAME bug (slot 4 again: the 1st travel's DONE had already released him, so "just
  released" was empty). v2: the slot whose PACK the actor is still RUNNING (GetCurrentPackage) goes last.
  b2_combat.sh -> rc/urvel_slot2 x6 + rc/b2_cb x4: BUILD HAD FAILED (ambiguous LogPlayerFollowList decl) -> ran
  the v1 DLL; urvel 4/6 (both fails = slot 4 again: it was the ONLY free travel slot -- the others are held by
  NPCs on their own travels), b2 3/4. v3: reusing the slot of the still-running PACK -> EvaluatePackage the
  emptied actor first (drops the package), then fill. Script now stops on a build error. Rerun b2_combat.sh.
  b2_cb_2 (fail): Sinnammu's combat target 0 at every walk end -> she is NOT in the fight ("joined to help"
  ruled out). Fighters' positions come with the rerun.
  RERUN (v3 DLL): URVEL 6/6 (rc/urvel_slot2) -> +4 more for 10 (urvel_slot3). b2 0/4 (rc/b2_cb): in every fail
  Sinnammu stands EXACTLY on the landing (4440 2512 -33) the whole wait, combat target 0; Nals (placed 3904
  3424) at 3984 3453, Eigma (placed 4032 2976) at 4029 2986 -- a standoff 470 apart (Nals Marksman 52, Eigma a
  Witch), neither closing in; mesh_route joins them (13 points). Not a frozen cell. Next: her GetCombatState
  (TEMP, id 54679 = 0x9e84e0) -> rc/b2_cs x4.
  URVEL: 10/10 in a row with slot rule v3 (urvel_slot2 6/6 + urvel_slot3 4/4) -> FIXED (runtime, TEMP logs to
  strip later). Run-6-style Gardens give-up not seen in those 10.
  b2_cs 0/4: combat state 0 always (not in combat, not searching). FIGHT THEORY BROKEN: b2_cs_2's fight ended in
  1 s (Nals died) and she still failed -- within 2 s of landing she walked ~820 EAST, away from the fight
  (to 4863 3195), then never moved again; other runs froze on the landing or at 5376 3633. All three spots
  route to the statue (mesh_route). Lead: Skyrim bystander danger-avoidance, then no return to the follow.
  Next: her position every 30 ticks, b2 x4 on each instance (rc/b2_path*, b2_path2*).
  b2_path 4/8. Fight ended -> pass 4/5; fight going -> fail 11/11 (all 16 runs). In a PASS she also stands on the
  landing until ~4 s after the fight ends, then walks (journal 40 1 s later); b2_cs_2's exception had a
  creature (05D0440A) fighting the player = also nearby combat. CAUSE: our Morrowind PACKs carry PKDT interrupt
  flag 0x04 "Observe combat behavior" (DEFAULT_INTERRUPT 0x44 from the TES4 converter) -> Skyrim halts the
  package to watch any nearby fight. OpenMW actors react only when attacked/alarmed. FIX (import):
  interrupt_morrowind.interrupt_for_kind clears 0x04 for every MW package (test expectation updated, 29 pass).
  Reimport Morrowind.esm (mw_import_observe.log) -> b2 x4 per instance. Blast radius: every MW-authored and
  slot PACK (TR/TD/Tri/BM/Arktwend need a reimport too once confirmed). This likely also explains Mauragin.
  b2_obs (MW reimported only) 3/10, fight going -> fail 7/7 again: her slot PACK 07BE381C is BLOODMOON's (MW_ALL
  slot 07; the last plugin's AI quest serves the slots) -> not yet changed. Reimport Bloodmoon.esm
  (bm_import_observe.log) + rerun obs_check.sh (overwrites rc/b2_obs*).
  RESULT: 5/10, fight going -> fail 5/5, with MWAIFollow0 (02BE381C) verified at interrupt 0x0050 and 0 of
  Bloodmoon's 434 PACKs carrying 0x04 (temp pack_flags.py). OBSERVE-COMBAT THEORY WRONG -> REVERTED (source,
  test; MW + BM reimported back: *_import_revert.log).
  REREAD: in fails the FIGHTERS are stuck too (each moved 70-95 toward the other, then stood for good, 470
  apart, though mesh_route joins them); in passes they meet within 1 s. So every NPC in the Inner Shrine
  stops moving in a fail and the unresolved fight is a SYMPTOM, not the cause. Inner Shrine NAVM: every tri
  flagged Water (XCLW 0 over a -33 floor), same in passes. Next: what stops all NPC movement there.
  Pass vs fail logs (b2_obs_4 / b2_obs_1): in BOTH, the moment the fight starts she drops out of the player's
  ExtraFollower list (holds 1 -> 0) and stands; in the pass she walks again ~5 s after a fighter dies (journal 40
  at +5 s). So nearby combat suspends her follow and its end resumes it; fail = the fight never ends (fighters
  stuck too -- separate question). Vanilla census (pack_census.py): No Combat Alert (bit 27) on 39 of 182
  "follow" PACKs, Ignore Combat (bit 20) on 1 -- no clear pattern; not guessing a flag. Sonnet disasm asked what
  removes an ExtraFollower entry / suspends Follow on nearby combat.
  Disasm answer: the ONLY per-entry remover is ExtraDataList::RemoveFollower (ID 11653, 0x15d7f0), called by the
  package-END handler ID 29620 (0x4965e0) -> her drop = her Follow package ENDED/REPLACED (not a flag test in
  Follow). Two callers then install another package: ID 37406 (0x66da10) and 37417 (0x66ede0), fetched via
  TESNPC::GetPackage 0x3b7960 (type 0x12 check). Follow update 0x468f10 also freezes a follower while a
  combat group targeting the FOLLOW TARGET is detected (not NPC-vs-NPC). No read of PKDT bit 27/20 on these
  paths. With 0x04 removed the entry still drops at fight start (b2_obs_1). Lead: an NPC override package
  (Spectator SPOR / combat override) -- subagent tracing 37406/37417. Our StartCombat (game_calls_combat.cpp)
  touches only the two fighters.
  Meanwhile: tate_pins.sh -> rc/tate_pins x3 (instance 2, Arktwend, all pins).
  Disasm 2: 37406 = guard-warn (GWOR, +0x178 list) replacer, 37417 = observe-corpse (OCOR, +0x170, fires on a
  DEATH, gated by interrupt bit 3) -- neither fires at fight start. Fight-start candidate = Spectator handler
  ID 40256 (0x7270e0; SPOR +0x168 list, else a runtime-built package), gated by interrupt bit 2 (0x04) of the
  CURRENT package via 0x661f50 (ID 37246; also true if process flag [process+0x459]). 0x04 removal made no
  difference and GetCurrentPackage never changed from 07BE381C -> unconfirmed. Other leads: combat-override path
  0x71c020 (ID 40079) from detection fn 0x6e2970 (ID 39359); ID 37172; predicates 39746/39747/39751/39753/39754.
  Water: CELL DATA.Flags 3 (has water), XCLW 0 over a -33 floor = authored, faithful (not a conversion bug).
  PARKED after 4 theories (fight-only, combat assist, observe flag, water): b2 ~50%; fail = the Nals/Eigma fight
  never resolves (fighters stuck 240-470 apart, Nals melee/demon mace) AND she is suspended while it lasts.
  Next attempt should start from WHY the fighters stall (two melee/caster NPCs, 13-point winding path).
  11-06 b2_today x3 on today's runtime (slot v3 + actor SetPos engine move): 2/3, same pattern -- the 2 passes
  had a fighter die, the fail did not (Sinnammu on the landing 4440 2512, 846 away).
  FIGHTERS FROZEN (b2_today_1 + 5 older fails, 30-tick log): Eigma never leaves her placed spot (4032 2976);
  Nals (placed 3904 3424 -24) freezes at a FIXED point 11-37 ABOVE the -33 floor for 68-72 samples: mostly
  (3985..3997, 3446..3459, z -18..-22), also (4023 3216 +4.3), (3985 3001 -14), (4932 3360 -11). Nothing under
  him (in_dae_hall_l_entry_01 / rubble nearby; the rubble is cut from the mesh; mesh there clean). The cell
  has water at 0 over the -33 floor and EVERY navmesh tri flagged Water -> THEORY: Nals enters Skyrim's
  swimming state in 33-deep water (root floats ~15-20 under the surface) and never leaves it (no non-water tri
  to climb out to); a swimmer can't fight, so the fight never ends and Sinnammu stays suspended. TEMP log now
  prints `swim` (SKSE IsSwimming: actorState1 0x400) for fighters and slot followers -- next b2 batch.
  b2_swim 1/3: swim = 0 for both fighters every sample -> SWIM THEORY WRONG. Fails: Nals frozen at (4024 3422
  -8.5) and (3989 3061 -21.6), 12-25 above the floor, not swimming; Eigma moved this time (4233 2962). In the
  earliest log Nals is already 37 up, 230 from his spot, 1 s after arrival -> lunge/knockback stuck mid-air?
  TEMP log now prints both actor state words (+0xC8/+0xCC raw) for fighters -> b2_state x3.
  CAUSE FOUND (b2_state): Nals' state 0x06000041 = knockState 3 (out) for good -- Eigma's "paralysis" (007D48FA,
  Paralyze Touch 5 s) never wears off: MGEF TES4MW045ParalyzeFFTargetActor flags 0x00400405 lack Recover (0x2);
  vanilla sets Recover on all 19 Paralysis MGEFs. Morrowind's AppliedOnce MEDT bit (0x1000; OpenMW applies once
  and undoes on removal = Skyrim Recover) was never mapped. FIX (import, magic_morrowind MW_FLAG_TO_TES4: 0x1000
  -> TES4 Recover 0x2). Tests 46 pass. MW reimported (mw_import_recover.log): paralysis flags 0x00400407.
  Blast radius (recover_census.py): before, 0 Recover on any MW-source MGEF (Arktwend build). Now Paralysis 5/5,
  Calm/Demoralize/Frenzy, Peak Value Mod 229/229 (vanilla 220/225), Value Mod 17/85, Script 114/166; archetypes
  vanilla never flags (Bound 11, Summon 23, Light 3, Absorb 3, Command 4) already carry Recover in the shipped
  Oblivion build (21/39/6/43/7) -- not new; MW-only new: Dispel 4, Lock 2, Open 2 (instant, no-op), Cloak 7.
  Every MW-format plugin (Tri/BM/Arktwend/TD/TR) needs a reimport once confirmed. b2_recover.sh -> rc/b2_recover x4.
  RESULT: b2_recover 3/4 + b2_recover2 5/6 = 8/10 (was ~50%). Both fails = a new standoff, no knockdown lasting:
  Nals walks UP the statue platform's west ramp (z 54, meshed, mesh_route joins it to Eigma) within 1 s of the
  fight starting, stays there (4335..4700, 3420..3650); Eigma never moves a unit from her placed spot (4032 2976)
  -- in passes she does move (4018 3124 etc.). Ruled out: Chameleon (10 pts / 30 s), missing ZNAM (vanilla:
  1052 non-template NPCs have none), ranged weapons (both melee: warhammer / demon mace), her package (wander
  512), her script (StartCombat once). OPEN: why Eigma is frozen at her placement in fails.
  Tribunal (1 MGEF override) and Bloodmoon (4) reimported for the MW_ALL load order (*_import_recover.log).
  Run 6 = new: after journal 60 our door carry put Urvel on the Gardens landing (1244 -3108), the case then
  walks the player ~7000 in ONE leg (`walk -1173 5997`) and Urvel never closes in (7625 at the walk end, PACK
  follow). Suspect the Follow procedure's 30 s give-up (leader > 2500, no sight); MW AiFollow never gives up.
  Unconfirmed: the next DLL logs follow-slot actors every 300 ticks.
  b2: TEMP log now adds each slot follower's combat target + position and every fighting actor's position,
  every 300 ticks (b2_combat.sh -> rc/b2_cb x4, real case): is she in combat, or do the fighters block her way?
  Dropped unbuilt edits (theory superseded, not tested): ReachedFollowPoint 256; ReachedWalkPoint (walk within 48).
  (arch 340's steer there failed only because of the freeze). DLL link blocked by inst 2's urvel_rt2 runs.
  Then sohn_worldend (Arktwend).
  Blast radius: every MW-source AiFollow Player NPC.

## Carry -- FIXED in tests 11-07 (pending in-game confirmation): Skyrim's HELLO APPROACH
- Mechanism (measured + disasm, see RUN 10 notes): the hello evaluator (39632) gives an actor whose package has
  "Hellos to player" and that sees the player within 150 a created Follow package on the player (37367 -> 39300);
  only a spoken hello ends it, so with none spoken the NPC followed the player through doors and after every
  teleport (= vanilla's "talk then leave, it follows you out"). Fix: activation.cpp HelloApproachHook skips the
  approach for Morrowind-index actors (OpenMW never walks up to greet). hello_check: warlords 6/6 (was 1-5/6),
  urvel 3/3, bm_morale 2/2 ran (3rd = game never connected) -- 0 created packages, 0 speaker place changes.
- UNTESTED: whether a converted NPC's Skyrim hello bark still plays without the approach (Olvyne's never did:
  31 approach packages, none ended by a line). TEMP carry traces REMOVED (move-core call-site hooks, created-
  package vtable hook, chase/path logs, TraceSpeaker). Kept: runner "never loaded -- <where it is>".
- The `approach` lines cases gained for the carry stay (user's rule 7). Full runs 11: MW lane (inst 2,
  run11_mw.out), Morroblivion lane (inst 1, run11_mwob.out).
- Old notes:
- The NPC the player just talked to leaves with the player's script teleport within ~2 ticks. Proven by TRACE
  + save: Hasphat moved to EXACTLY the guild exit door's XTEL landing; Helseth hopped 4 places into the Winged
  Guar; Constans walked out of the shrine toward the player; Vilbia place -> null. Seen: Hasphat, Helseth,
  Vilbia, Athyn, Falco, Constans, Carnius, Falx. No authored AI; runtime holds nothing on the speaker.
- Our talk = TESNPC vtable Activate hook (ID 195816 slot 0x1b8) via ActivateRef (ID 19796); Skyrim dialogue
  never starts.
- Ruled out: Say voice, AI slots, ActivateRef command branch, stale speaker pointer, talk-then-teleport probe
  (0/10 Athyn, 0/10 Hasphat), EvaluatePackage at goodbye (bm_morale 9/10 vs 8/10; Falx still hopped;
  reverted), timing after goodbye (kByeSettleTicks 30 does not hold).
- Reproducer: bm_morale ~1 in 3. TEMP speaker TRACE in game_calls_move.cpp (remove when solved).
- Tests mitigate with `approach` (moves the player to the NPC, any cell) -- not a fix.

## Test speed (--fast)
- iFPSClamp 6 (1/6 s step, ~11x): the character can't climb a 38-deg slope (Sewers East stair); havok substep
  INIs don't help (the controller integrates the whole frame). N=10/12/15 climb, N=8 not. User: never slower.
- Now: uncapped frames (iVSyncPresentInterval 0, bLockFramerate 0, iFPSClamp 60): 25-27x in QASmoke but
  speed = fps x step, heavy exteriors ~0.5x (Arktrand galeone: boat timed out).
- Pace governor (runtime): frame timer kFrameTimer (0x31cc270 on 1.6.1170) +0x10 = step in MILLISECONDS
  (1000/N from the INI; the update at 0xcc9520 reads it every frame; delta = step x 0.001). PaceFrames
  rescales it every 0.5 s toward 25x, bounds 1..100 ms, x2 max per step, paces even when the clock stalls.
  v1 (paced only when the clock moved) froze; v2 (seconds instead of ms) froze -- both my errors.
  v3 WORKS in light/medium cells (22-35x). Arktrand: 5.6x at 33 ms, 0.8x at 100 ms (frames get far costlier
  with a longer step); runtime ticks 1% of real time; render cuts (shadows/SAO/grass) no help.
  v4 (learned step ceiling: NextPaceStep) BUILT 11-04 (script_test passed), unmeasured. User has a global
  172 fps cap (measured runs exceed it; may cap Arktrand's 33 ms window).
- SLOPES (11-06, sonnet disasm 1.6.1170): bhkCharacterController::Update (ID 78275, 0xe9f170; sole caller
  0x6771a8 in ID 37507) ALREADY sub-steps its frame: N = 1 + trunc(20 x delta) (`mulss` at +0x69C on the
  -20.0 at 0x1ad2ae0), capped at 2 (`mov eax,2` at +0x6B1); delta and the move vector are divided by N (only
  when moving: move vector length^2 >= 10, controller flag 0x14 clear). At 50-75 ms N = 2 -> 25-37 ms pieces =
  the climb limit. Per-frame timers (+0x220/+0x22c) use the original delta. Slope test itself
  (processConstraintsCallback 0xed0b40, max-slope cos at controller+0x310) is delta-free; delta enters via the
  proxy integrate's cast length (0xb81ac0). FIX (runtime, game_calls_query FinerControllerSubsteps, applied the
  first time PaceFrames sees a fixed step -- never in normal play): rate -> our own -50.0 (NearData slot within
  rel32; the shared -20.0 untouched), cap 2 -> 6, both byte-checked (PatchBytes). 50 ms -> 3 x 16.7, 75 -> 4 x
  18.8, 100 -> 6 x 16.7. common/hook: NearSlot (shared page), PatchBytes, NearData. Unbuilt until inst 1 frees.
  RESULT (substep_check, patch logged as applied): tate 0/3 (stall 2244 25938 again), scout 0/3, battle 0/2 --
  NO CHANGE. Scout's track: south from the scout spot (y -146132 -> -147207, z 5029 -> 4759), then a ~590 drop
  to (142145 -147329 4170) -- it walked OFF the navmesh (scout_fall.png: mesh only east of x ~142800) into the
  unmeshed area; a route choice, not a climb. SLOPE THEORY WRONG for these stalls -> patch + hook helpers
  REMOVED (source back to HEAD for hook.cpp/h; ids/query cleaned). Scout: engine heads south and leaves the mesh
  only in the gate-checkpoint state; mesh_route (one NAVM 0219ADEF, shared edges) says north. Still open.

## Navmesh pins: method and defect patterns
- Method (SHIPPED mode): restore pins -> import -> plan on the shipped NAVM (pin_region.py) -> pin -> import ->
  mesh_clean 0/0/0/0 + slab_map picture. Heights from in-game drops (drop_probe.py, DROP_Z) where collision is
  stacked. Exterior pins ship by --import-only only (cellview To ESM loses seam links: no neighbour seeds).
- Recurring defects pinned: mesh UNDER a slab/terrace/plank (Karstaag, Gardens terrace, Sewers East block,
  Gron exit, scout rock), channels BRIDGED above the floor (Sewers East), floating ribbons/strips (Karstaag
  passage, Gron pass), pinches, a tri under a thin wall's end (tate cemetery).
- Generator changes are not allowed (user): decimate flip/refine fan fixes reverted; pin guard v2 kept
  (measured: tiny, all positive, chargen untouched). T-junction fix in navmesh_frozen (close_t_junctions,
  FROZEN_VERSION 5) because the import's pin merge left T-junctions -> AI walk stalls.
- Open tool bugs: cellview CellCtx.build lacks the import's inputs (spawns, obstacles, water, extra refs) so its
  mesh differs from the shipped one; exterior To ESM seam loss; seam_links.py grid mapping wrong for Arktwend.

## Crash: NaN cast hits (fixed)
- tr_assassins stack overflow (c00000fd, quicksort over havok hits with a NaN pivot): in_mh_doorjamb_02 chunk
  tri = a sliver whose determinant is 0 in Havok's float32 decode. FIX cms.py/cms_builder.py (engine decode),
  collision_sanity check; 102 meshes rebuilt (Tri 2, Arktwend 7, Mwob 4, TD 56, Oblivion 18+142 trees, Nehrim
  14+245 trees, FNV 14+10). TR's 3 flagged creature NIFs are stale 09-13 leftovers (TD owns them; not deleted).

## Runner / runtime rules added
- Door-reach guard: `activate` of a door > 512 away with a follower fails the case (names the followers).
- Followers through doors (fix 4/4b/4c): followers within 800 move with the player through a door, onto the
  landing once the player walked 128 off it or waited 120 ticks.
- `until` already true ignores held menus (both runners: morrowind + common TESRuntime). HeldMenuError names
  open menus. Rest refusal cap 10. Route steps skip after a non-dialogue menu opens.
- `approach <npc>`: player MoveTo 80 in front of the NPC, any cell, actor refs only, never with followers.
- Silent exits ~7 since uncapped --fast; one captured = the doorjamb crash above.

---------------------------------------------------------------------------------------------------------
# QUESTS -- MORROWIND LANE

## ingame -- 187/187.

## morrowind_main
- chargen_papers / chargen_hall: "a menu holds the game" flakes (log shows no menu); HeldMenuError will name it.
- a1_2_key: CARRY (Hasphat 055DC840 to the guild exit landing). Open with the carry.
- **b2_escort -- OPEN (follower freeze).** Case now: defend; swim legs planned around drowned menhirs
  (swim_route.py) + dry ramp at x ~94300; console door uses -> `activate` after real walks (Outer Shrine 13
  navmesh legs); no spacing wait at the statue. Journal 40 = her AiFollowCell (4471 3620 -22, done within ~150)
  OR Sinnammu within 400 of the statue (00DBEA02 at 4414 3537) while the player is in the Inner Shrine.
  History: 3/3 on 11-03 (after removing the spacing check -- that run she ran AHEAD to the statue); 2/5 with
  Accompany; 11-04 0/3 / 1/3: she freezes ~1122 behind. Failed attempts: steer to 55 from the statue (arch 340);
  walk onto 4471 3620 (11-04, 1/3; reverted); re-evaluate package; Accompany; speed (governor).
- b3_bride: Falura escort -- exit east of the pods, navmesh lead to the sea, swim legs, 39 navmesh legs to the
  Ashkhan's yurt; passes. (Slope north of the pods has no navmesh; not pinned.)
- b5_hortator: CARRY (Athyn, run 5). Fixed-count bribes in b2/b3/b6/b7 unchanged.

## bloodmoon_main
- bm_siege: wait for all seven werewolves enabled before the kills; 5/5.
- **bm_morale -- OPEN (carry).** Vilbia ref 063FD697 (base 06EA1F8F -- passing the base crashed the game),
  Falx 062C7CC4. Case: Position outside + `approach 063FD697` before each Vilbia talk: 2/3 (fail = carried).
  11-06 bm_morale_now x3 on the current DLL (MW+BM build): 3/3 (approach still masks the carry).
- bm_trees: `until 600 "BM_riekling_st_UNIQUE"->GetDisabled == 1` before the stone (script arms only if it ran
  while journal < 10); 3/3.
- bm_karstaag: Krish led on 13 engine walks with waits; passage pin (clean rebuild 88 tris), throat pin (drops,
  26 tris), walk target at floor height -760, `approach BM_riekling_Dulk_UNIQUE` in the Throne Room; 3/3.
  Fast-mode falls through the cave floor recover (cause unknown).
- bm_water, bm_draugr: run-1 failures, not reassessed.

## raven_rock -- 16/16 in run 6
- co_report: Carnius PositionCells to region-named exteriors ("Solstheim, Isinfier Plains"); FIX shown exterior
  names in cells_formid (morrowind_travel/sidecar); 3/3. Census: MW/Tri/BM 64/64 resolve, Arktwend 43/44,
  TR+TD 446/452 (rest have no exterior in the chain).
- co_under_siege / co_thief / co_mine: carry (Falco, Constans) and a lane-only co_mine walk failure; 3/3 each
  from checkpoints. Raven Rock silent exits: 4 consecutive passes on the new cell tables.
- co_survivor (20 steer legs to Falco), co_estate (mine steers), co_bar_brawl (walk to the bar door): pass.

## raven_rock_brawl -- 3/3.

## tribunal_mournhold
- ms_warlords, ms_scroll_sales: SetDisposition stand-in (bribe spiral = OpenMW's rule); 3/3.

## tribunal_story
- **tr_shrine_dead -- OPEN (Urvel, follower freeze).** Case: all 50 steer legs removed -> walks. Pins (Tribunal):
  Temple Gardens (door landing, target, raised terrace), Temple Shrine (user's repin + hollow, ramp, lower slope,
  entrance ramp, dais), Sewers East (raised block, east + west channels, ramp, stair foot, whole west stair,
  cave-exit pocket). Remaining failures are all Urvel: shrine greeting (he parks > 256) or his 2nd AiTravel.
  3/3 only with Accompany (reverted).
  Sewers East walk to 6354 7683 froze (exactly still 5 min) at (5812,4247,12433) in urvel_fl_2 and fast10b_1: a crack
  (no floor 11800..12950, 8-40 wide, x 5520..6150) along the foot of a 47 ledge (south 12433 / north 12480; ramps
  at both ends) that the shipped mesh BRIDGES. PIN (backup Tribunal.esm.pins.pre_sewers_crack.json): floor_points
  step 40 offset grid, pin_region box 5450 4100 6340 4500 band 12400..12500, notch = crack +12, margin 30: 270
  tris, min angle 5.2; Sewers East 2081 frozen / 428 voids. SHIPPED (trib_import_crack.log); mesh_clean
  5400 4050 6400 4550: 0/0/0/0 (305 tris); picture sewers_crack_pinned.png: crack cut, both floors 0-8 off,
  joined at both ramp ends.
  urvel_sp_6 (journal 60 never): greeted with Urvel 108 from the player (spacing fix works), 1st AiTravel to the
  dais (2190 -2471 4) done, 2nd AiTravel (1900 -2146 -173) never done; mesh_route connects them. His position
  unknown -> probe if it recurs.
- tr_kill_goblins, tr_mazed_band: Fedris waits < 4000 (wander 512; talk has no range check); 3/3.
- tr_assassins: doorjamb crash fixed; Helseth carry ~1 in 5; SetDisposition; 3/3 last loop.
- tr_blade: defend + wait 30 before taking the blade.
- tr05_people, tr06_temple, tr09_journalist: SetDisposition (tr09 after `talk`: her greeting resets to 40); 3/3.

## morrowind_temple
- tt_supply_deliver (Sendus < 4000), tt_curingtouch_lette (Lette < 4000), tt_falseincarnate,
  tt_diseasecarrier (SetDisposition): 3/3.

## morrowind_daedric -- 24/24 (da_boethiah_build moved indoors; rest cap fixed a 40-min hang).

---------------------------------------------------------------------------------------------------------
# QUESTS -- ARKTWEND LANE (arktwend_main; arktwend_side 6/6)

- galeone_rowboat: `until 6000` for the boat (game ran 0.5x in Arktrand); 3/3.
- herbert_gor/house: defend + steer routes; pass. herbert_escape: walks to each door + defend + Gor passage
  ramp pin; 3/3. herbert_gron: cave-exit slab pin, plank pins (2), rock pin v3, gron pass pins, inn walk split
  at 3 road points; 3/3. (Engine routes through the cave door if the target is far.)
- storm_calisto: `until ... Tuniervar >= 13`.
- **tate_nembrest -- OPEN.** Guild-yard fire-box pin (StormwendXN2Y10); steering route -> walks: road point
  (-1641 28801) then the station (8450 20995) (direct walk went through the steel-horse wagon's doors); 3/3 on
  11-04 05:50, then 0/3 in run 6 (dead still at 3 spots, two on cell seams). Under the governor: road walk
  passes; station walk stalls 3/3 at (-1472,26112) = a shipped tri under the west end of a thin wall
  (CemeteryofStormwendXN1Y6; wall y 26085-26100, top 405-480, x -1510..-1400). Pin not clean yet (min angle
  2.3; backup navmesh_pins/Arktwend_English.esm.pins.pre_wall_end.json). Route scan route_obstacles.py ->
  tate_route_obstacles.txt (x >= 0 cells only: VorMountainsX0Y2 45, PastureLandX0Y7 8, VorMountainsX0Y9 5,
  X2Y5 2, X0Y6 1) -> rerun for x < 0, triage flags, pin all real ones in one pass.
  x < 0 scan (tate_route_obstacles_all.txt, corridor = tate_pace_1 walks + road/station): TournamentPlazaXN2Y9 10,
  XN1Y9 5, StormwendXN2Y10 1, VorMountainsXN1Y7 1 (all on the road walk, which passes) + the cemetery wall end.
  Wall = y 26080-26100, x -1510 to past -1280 (top 405-480), ground 248-294. PIN v2 (backup
  pins.pre_wall_end2.json): floor_points step 60 (offset grid), pin_region box -1760 25800 -1200 26360 band
  200..340, notch = wall +12..22 (x -1535..-1100, y 26068..26122), margin 40: 116 tris, min angle 5.2 (step 25/30
  grids gave 2.0-4.4 fan slivers beside long kept edges) + a 2nd notch for a post on the wall's north side
  (x -1440..-1428, y 26100..26164, top 466). Planned on the UNPINNED reimport: 120 tris, 144 frozen / 42 voids.
  Shipped; mesh_clean 0/0/0/0 (141 tris); rescan: cemetery 0 flagged; picture tate_wall_pinned2.png (all 0-2 off
  the floor, wall + post cut). tate x3 in spacing_check.sh: 0/3, NEW stall on the ROAD walk (passed in tate_pace
  15:00): the player bounces at the start then sticks at (-5720,40650) under the plaza stands (TournamentPlazaXN2Y9,
  stands 439-459 over ground 233); engine heads S, mesh_route still says NE like the pass. ESM diff vs the good
  import = only the cemetery pin (+1.5 KB, logs identical, no navmesh code newer). Rerun with the no-teammate DLL
  (tate_nt); if still 0/3 -> A/B without the cemetery pin.
  tate_nt (no-teammate DLL): 0/3 -- road passes 2/3 (plaza stall 1/3), station walk stalls at (2322,26170) x2 =
  PastureLandX0Y6 overlap + sliver (mesh_clean) -> PIN (box 2100 25950 2450 26250 band 300..480, terrain points
  step 45, 12 tris, min 8.1; backup pins.pre_x0y6.json); shipped, mesh_clean 0/0/0/0.
  A/B WITHOUT the cemetery pin (tate_nocem): 1/3; plaza stall happened anyway -> the cemetery pin is NOT the
  cause (my link was wrong); pin restored (pins.with_cemetery.json -> json, reimported in restart_check).
  Plaza stall = the player wanders off-mesh among sacks under Ex_common_plat_LRG (z 448) east of the mesh; cause
  of the engine heading there not found. 3rd stall (9134,21534): the mesh runs UNDER terrain_rock_ai_12 (9330
  21480) -> PIN: notch = convex hull of points rising > 50 over the ground (footprint.py) +12, box 9050 21150 9600
  21780 band 150..320, 111 tris, min 9.3 (backup pins.pre_x2y5_rock.json). SHIPPED (ark_import_rock.log): mesh_clean
  0/0/0/0 (134 tris), tate_x2y5_pinned.png (rock cut, rest 0-2 off the floor; crates SW of it never had mesh);
  mesh_route X0Y6 stall -> station and rock -> station connect.
  tate_pins (all pins) 0/3: 2x plaza stall (-5705 40665 z 233), 1x (2315 26012 z 444) in the X0Y6 pin area.
  PLAZA CAUSE (slab_map plaza_slab_wide.png): a dead-end pocket of 10 shipped tris on the ground UNDER the stands'
  platform (Ex_common_plat_LRG, top ~448; tris 221-227 below it; footprint x -6200..-5690 y 40570..40830),
  joined only to the open ground north; the stall = its east edge. PIN (backup pins.pre_plaza.json): floor_points
  box -6300 40480 -5600 40960 band 150..340 step 60; pin_region box -6260 40540 -5640 40960 band 150..340, notch =
  footprint +12, margin 40: removes 12, builds 16, min 11.0 deg. Import ark_import_plaza.log -> mesh_clean +
  picture, then the X0Y6 stall.
  Plaza pin needed 2 re-plans (always from the restored backup + unpinned reimport): a tiny 221-deep tri stayed
  under the platform's north edge (~40838) -- a STEP in the notch outline leaves a wedge there. FINAL notch
  "-6212,40558;-5678,40558;-5678,40912;-5782,40912;-5782,40862;-6212,40862": 9 tris, min 6.4 deg; SHIPPED
  (ark_import_plaza4.log), mesh_clean 0/0/0/0, plaza_pinned4.png: no tri under the platform.
  X0Y6 stall (2315 26011 z 444): ground a smooth ~24 deg rise (no ledge; pace step 50-65 ms there), no object
  within 200 (slab_map's purple diagonals = terrain quad edges, not walls). The mesh strip running SW from the
  stall FLOATS 15-74 above the ground y 25780..26000 (x0y6_south.png). PIN (backup pins.pre_x0y6_ribbon.json):
  floor_points box 2120 25720 2480 26040 band 380..620 step 45; pin_region box 2150 25740 2450 26005 band
  380..620 margin 40: 12 -> 12 tris, min 16.7, ring vertices set onto the floor (555 -> 466..527).
  Import ark_import_ribbon.log -> check, then tate x3.
  Ribbon pin shipped clean (0/0/0/0, all tris -3..+6), but tate_pins2 0/3: road walk PASSES 3/3 (plaza pin
  works), station walk stalls 3/3 at (2244..2274, 25938, z 465) -- INSIDE the re-pinned strip, at the edge of a
  two-layer area west of it (terrain_rock_ai_12 x2 at 1924 25795 / 1931 25738, ~360 away; bushes). The
  "floating" strip was riding the rock; on the ground it walks the player into the rock's face. RIBBON PIN
  WRONG -> REVERTED (pins.pre_x0y6_ribbon.json, ark_import_unribbon.log). The 1/3 stall at 2315 26011 stays
  open; next look = the rock's footprint (footprint.py), not the strip's height.
  CORRECTION: tate_pins3 (ribbon reverted, plaza pin kept) 0/3, ALL at (2292..2302, 25960..26000) -- the stall
  happens EVERY time the player gets here (tate_pins' 1/3 was because 2 runs died at the plaza first). So the
  ribbon pin was not what caused it and its revert reason was wrong; both states stall. Stalls sit at the SOUTH
  EDGE of the first X0Y6 pin (box y >= 25950), where its ground-level tris meet the shipped strip floating 50-74
  above (or, with the ribbon pin, just inside it). Before the first X0Y6 pin the stall was at (2322 26170).
  The region keeps moving the stall as it is pinned -> look at the whole X0Y6 route corridor at once.
  mesh_route road point -> station (306 points): ... (1994 26389) -> (2289 26127 391) -> ONTO the strip at
  (2251 25961 514) -> SW along it to (1987 25499 596) -> up the hill (1729 24498 1015 .. 3724 25101 1676) -> down
  to the station. The strip is the ONLY climb (the slope east of it has no mesh). Unpinned strip: stall under
  its floating tris (path points 29-57 above the ground the player stands on). Ground-level strip (ribbon pin):
  stall at (2244..2274 25938) by the two-layer area's edge. Nothing walkable above the ground there (floor_points
  band 505..900 = ground only); jdbush3.nif has no collision; the two terrain_rock_ai_12 (~330 W) are the only
  candidates (steep faces are invisible to floor_points). UNRESOLVED: needs an in-game look / drop probe at
  (2250 25935) before another pin.
  DROP PROBE (drop_x0y6, Position x y 700, 18 points): ground only -- y 25935: z 466..474, y 25965: z 450..461,
  i.e. +18..20 over 30 = 31-34 deg right at the stall. => the SAME fast-mode slope limit as the Trun Telur gate
  climb, not a mesh/collision object. Test with the controller sub-step patch (FinerControllerSubsteps).
- tate_door: PositionCell -> walk/steer up the stairs + Tate wait; pass.
- baron_house .. fall_theater: guild secret-door pin (thieves guild ONE component), steer up the guild stairs,
  walks across the city, harbor gate wait, Jarko lookout; runtime fixes (StartCombat dedupe, glide collision
  settle, exterior-InWorld) ; all pass.
- theater_calisto_house .. theater_guard_report: 3/3 (run-6 guard-inside not reproduced; no change).
- sohn_lift/airship/crash: followers-through-doors fix 4/4b/4c; pass.
- **sohn_worldend -- OPEN (follower freeze).** Mauragin parks 501-509 vs his <= 500 greetings; 3/3 with
  radius 128 before Accompany, 1/3 now. His companion warp only fires > 680 while the player moves.
  11-04 eve: sohn_sp 2/3 (Mauragin alone in the list at 506-570, runs follow), sohn_nt 2/3, sohn_rs 2/3 (fail: walk
  to 174334 87086 4 -- the player stands at (174490,87190,-146), 150 BELOW the target, PACK 0, marker (0,0)).
  11-06 sohn_now x3: 2/3. Fail = the LAST check (Mauragin < 500 at the end): he stays exactly at the previous
  leg's end (167439 82861 1201) while the player steers ~510 on to (167360 82351); listed in the player's
  ExtraFollower list (F 128), follow PACK 0517381C, combat state 0; mesh_route joins them (9 points). His
  script reissued AiFollow 54x in the run (last 02:15:14, before the freeze -- not churn). defend killed 4
  attackers at 02:15:12-13, he froze from 02:15:14. Lead (disasm 1): Follow update 0x468f10 skips movement
  while the follower detects a combat group whose targets include the FOLLOW TARGET (the player) -- a dead
  attackers' group may linger. sohn_check.sh -> rc/sohn_cb x4 logs the player's combat flag/state.
  sohn_cb 2/4, both fails the same (Mauragin 510 / 525 from the stopped player at the previous leg's end):
  player in combat 0, state 0 -> LINGERING-COMBAT-GROUP LEAD WRONG. Passes: ~448 at that point, then 112.
  His script (ZwergenMissionMauragin01): weapon drawn -> returns early every frame (combatCheck, sheathe by
  equip+remove "silver dagger"); every 20 frames SetSpeed 15 within 300 / 2.25x player's beyond -- but our
  Speed only maps to StaminaRateMult (attribute_buffs.cpp), so not a movement cause. No mechanism yet.
  TRAILS (30-tick follower log): pass sohn_cb_1 -- he keeps up mostly by his script's WARPS (735 -> 44 in 1 s =
  SetPos to the player's track when > 680), and walks normally after them (389 -> 43 at the end). Fail
  sohn_cb_3 -- at 02:37:55 he is 906 behind, warps ~770 to (167531 83026), and never moves a unit again (510
  from the stopped player, below the 680 warp threshold). So the freeze follows a script warp. SetPos goes via
  queued Papyrus SetPosition (game_calls_move.cpp SetAxis), the script then calls AiFollow the same frame, and
  RunAiPackageOn fills the slot at once -> the follow package may start (path from the OLD spot) before the
  move lands. Unconfirmed; next = log the package start vs the move's landing tick.
  CONFIRMED by log order (sohn_cb_3 l.5605-5646): "Mauragin01 follows player" -> "took follow slot 0" -> next
  log 906 from the player (old spot) -> then 510 (warp landed) -> frozen. FIX (runtime): game_calls_move
  MoveLanding(ref) = a SetPos written in the last 10 ticks not yet in the position; RunAiPackageOn parks the
  order (newest per actor, g_afterMove; log "waits for its SetPos to land") and StartOrdersAfterMoves (in
  TickPlayerFollowers) fills the slot once it lands; FillAiSlot = the old posted body; StopAiPackage drops a
  parked order. warp_check.sh -> rc/sohn_warp x4 + rc/urvel_warp x3 (regression).
  RESULT: SOHN 4/4 (orders parked 55-77x per run). Urvel 2/3, the change never fired there (0 parks); the fail =
  a player walk in Temple Gardens (to 1217 -3200) stuck at (337 -2245 -164), at the edge of the mesh by the
  two-layer terrace; mesh_clean on cell 56029D box 100 -2500 600 -2000: clean (51 tris). 1 in 13 Urvel runs
  since the slot fix -> rare walk stall, not pinned on one fail. Loop: sohn x6 + urvel x3 more (sohn_warp2,
  urvel_warp2).
  sohn_warp2 5/6 -- Mauragin froze in NONE of 10 runs (warp fix holds); the fail = steer leg stuck short of
  177385 87440 10 with the player at (177302 87552 z -121), 130 below the target -- slipped off the shore into
  the water (same signature as the old "174334 87086" fail at z -146). urvel_warp2 3/3 (Gardens stall: 1 in 16).
  CASE CHANGE (rule 6, failure found): the 6 on-land shore legs (180064 86899 .. 173030 86649 486) steer ->
  `walk` to the same points; mesh_route joins them along the shore (120 points through those spots).
  sohn_shore x5: 0/5 -- every run passed the shore walks but the LAST leg (the climb 174334 87086 4 -> 173030
  86649 486) never moved the player (the plateau climb has no real navmesh; mesh_route's one long edge there is
  not walkable). That leg back to `steer` (it passed before); the 5 shore walks stay. sohn_shore2 x5.
  sohn_shore2 3/5 -- shore OK in all 5; BOTH fails = Mauragin frozen 510 / 547 after a warp, although his follow
  order WAITED for the SetPos to land this time (l.6577 waits -> 6619 took slot 1). => order timing is NOT the
  cause; "warp fix confirmed" was WRONG (after it: 2 freezes / 14 runs vs ~5/13 before -- weak). Kept for now
  (harmless), to remove if the next fix holds.
  NEW THEORY: SetPos used Papyrus SetPosition, which teleports an actor without the engine move Position/
  PositionCell use (MoveInto = MoveRefrToPosition + AiAfterScriptMove: evaluate after the move, slot fix-ups) ->
  his pathing still thinks he is at the old spot. FIX (runtime, game_calls_move SetAxis): an actor's (not the
  player's) SetPos goes through MoveInto with the composed x/y/z. warp_check.sh -> sohn_setpos x5 +
  urvel_setpos x3. Blast radius: every MW script SetPos on an actor (companion warps, levitation z-sets).
  MoveLanding / g_afterMove / StartOrdersAfterMoves REMOVED (moot once actor SetPos is an engine move);
  FillAiSlot kept as the posted body. warp_check.sh -> sohn_final x3 + urvel_final x3 on that DLL: 3/3 + 3/3.
  => sohn 8/8 since the SetPos fix; Urvel 8/8 since the Gardens drop pin.
  RESULT: SOHN 5/5 (sohn_setpos). Urvel 2/3: fail = player walk (to -1173 5997) stuck in Temple Gardens at
  (368 -1698 z -420) -- 2nd Gardens walk stall (with 337 -2245): 2 of the last 19 Urvel runs. There the mesh
  bridges a no-floor strip (x ~395..410, y -1690..-1880) and one tri floats 55 over a no-floor area east of it
  (gardens_stall2.png; mesh_clean on 56029D clean). The floor south of y -1690 is multi-level (raised walk ~-385,
  lower ~-450..-465, pits -510..-560; gardens_floor.txt) -- the player stood between levels at a drop's edge.
  OPEN: needs a careful multi-level pin (not done).
  Route (mesh_route landing -> target): (274 -1719 -370) -> (320 -1687 -380) -> (363 -1685 -385) -> east along
  the raised walk's south edge; the stall (368 -1698 z -420) = slipped 13 S / 35 down off that edge into the
  drop. PIN (backup Tribunal.esm.pins.pre_gardens_drop.json): floor_points box 230 -1830 600 -1590 band -425..-330
  step 30 (walk level only); pin_region box 250 -1810 590 -1610 band -480..-340, notches = drop S of the walk
  (342..412 x -1810..-1692) | E drop under the floating tri (442,-1672;500,-1672;500,-1656;590,-1660;590,-1810;
  442,-1810), margin 30: 12 -> 36 tris, min 6.2 deg (box widened west to avoid a T-junction; notch corner moved
  off a ring line: 0.3 -> 6.2). Import trib_import_gardens_drop.log -> check + picture.
  Hand notches left tris over the drop twice (-67, then -66/-119): the drop edge steps by a few units. FINAL notch
  derived from the floor (footprint.py with a flat -390 "top" vs a step-10 highest-floor grid, rise 35, pad 12),
  then simplified (spike + spur bay + west wiggles removed): "358,-1822;328,-1802;318,-1732;318,-1708;328,-1698;
  368,-1698;368,-1688;438,-1678;438,-1668;508,-1668;518,-1648;558,-1648;558,-1638;578,-1638;578,-1618;612,-1618;
  612,-1822"; floor_points box 230 -1850 690 -1560 band -440..-330 step 30; box 250 -1830 660 -1580 (east to 660
  for a T-junction); 14 -> 37 tris, min 9.4. SHIPPED (trib_import_gardens_drop4.log), mesh_clean 0/0/0/0,
  gardens_drop_pinned4.png: walk edge tris -5..+9; 3 tris at -11..-21 by the east drop (x 610-650, off the route)
  left. urvel_gardens x5: 5/5.
- sohn_home: plank walk = `walk` along authored pathgrid nodes over the planks; passed; run-6 fail came from an
  old save (rerun with logs).
- sohn_child (wolf walk with child waits), sohn_tower (ridge walk), sohn_crypt..sohn_mauragin: pass.
- dwarves_mert/picks/shaft/mao: pathgrid steers, rail-top legs, Daras wait 3000; pass.
- dwarves_demon_king: canyon corridor pin (measured heights, clean), 5 canyon walks + `bye if open`, door from
  its foot, walk into the fight area; 3/3.
- **enderal_burg_* -- OPEN.** Fortress gap pin (camp <-> gate), scout rock pin v4 (32 clearance, clean);
  burg_gate passes; scout parked: the walk back from the scout spot turns downhill only in the gate-checkpoint
  state (ruled out: rock margin, cannons, followers, quest stage, walk-vs-teleport start, steam vent, stale
  marker). camp/battle fail; enderal_brother = cascade.
  11-06 status (fortress_check.sh, current DLL): scout 1/2 (fail: walk to 140730 -143673 5744, player at 142579
  -147242 4735), CAMP 2/2, battle 0/2. Battle fail = `walk 140292 -143227 5584` (wall top -> gate square): the
  wall walk ends fine (139532 -143937), then the player goes EAST along the wall top and circles at
  (140130..140310, -143440..-143545, z 6440-6490), ~900 above the target, 5 min. mesh_route from there goes WEST
  (132 points: back along the wall, down past the camp 138539 -142743 5883, round to the square). Cell
  GreatSeaX17YN18 (8192 grid). mesh_clean box 139900 -143750 140600 -143150: NAVM 0219ADEF clean, NAVM 02120304
  (the square level) 1 overlap at (140543 -143221), ~250 from the target. slab_map shows no collision there
  (black) -- tool limitation in this cell. Lead: the overlap near the target / the engine picking an east
  path mesh_route does not have. Not fixed.
  The gate = two back-to-back load doors: outer 05BC8A49 (140545 -143281 6005, XTEL -> 140730 -143673 5744 =
  the scout walk target) and inner 05D5AC07 (140554 -143317 6000). The square target sits beside them; the
  overlap is at the gate. Theory: the engine's path from the wall top heads for the gate doors (walking player
  can't use them) -> circling on the wall above. CASE: battle walks via the camp point (`walk 138526 -142621
  5886`, the camp case's own leg; on mesh_route's path) before the square. Test after scout_now2.
  scout_now2 0/3: all = walk scout spot (143029 -145985) -> gate landing (140730 -143673 5744): the player goes
  SW downhill and sticks at (142119 -147288 z 4170); marker placed right; no events during the walk; objects
  on the direct stretch = EnderalRauch01 smoke + terrain rocks (no doors/XESP). mesh_route: straight NW (142165
  -145358 -> 141395 -144988 -> landing), one Skyrim cell. CASE: split at (141395 -144988 5663) (a mesh_route
  point). a1_2_key 0/3: "'hasphat antabolis' never loaded" ("does not resolve in this load order", no trace of
  him moving this run) -> inst 2's a1_2_key checkpoint is from a run where the carry lost him; rerun from
  a1_2_report (a1_key_fresh): 3/3. But `--from a1_2_key` (mw_main_tail) failed the same way again: repeat_case
  never writes checkpoints, so mwtest_a1_2_report (shared Saves junction) is an OLD save in which the carry had
  lost Hasphat -- a stale save, not a game bug. Rewritten with `test_instance --instance 2 mwtest --checkpoints
  --from a1_2_report --to a1_2_report`, then mw_main_tail2 (a1_2_key .. c3_azura x1).
  Scout split 1/3: 1 fail = the same SW downhill stall; 1 = stuck at (142548 -145894 z 5143) at the foot of the
  climb to the gate: ground grid (floor_points cell TrunTelurX34YN36 -- Arktwend cells here are named on the 4096
  grid, GreatSea* holds nothing) rises 33-38 per 50 = 33-38 deg; pace step there 57-75 ms. Same class as the
  fast-mode slope limit (Sewers East 38 deg). Passing run step 33-48 ms.
  battle_camp 0/3: the camp leg works (ends 138573 -142706), then the square walk climbs BACK up toward the wall
  and sticks at (138891 -143239 z 6238). CASE: + low-ground legs from mesh_route (138700 -141906 5436, 139801
  -142921 5546) before the square -> battle_low x3. tr_assassins 3/3 (assassins_now).
  battle_low 0/3 (low-ground leg barely moves; one fail on the wall walk). REREAD: the gate square is OUTSIDE
  the gate (the gate case activates the outer gate from 140500 -143235, beside the square); wall and camp are
  inside. The engine heading for the gate doors was right -- a walk cannot use a load door. CASE: battle now
  walks to the inner gate's landing (140730 -143673 5744), `activate 05D5AC07` (as scout does), then the
  square; camp/low-ground legs removed. battle_gate x2.
  battle_gate 0/3: the walk to the landing circles on the wall top at (140418..140501, -143461 z 6508) every run.
  mesh_route: landing -> camp/wall NO PATH, so the landing is on the SCOUT side (the outer gate's XTEL lands
  there from the square) and the REREAD above was WRONG: square, wall and camp are one side. Inner-gate route
  reverted. Gate doors' NAVM door tris are both on the square (02120304 tri 79/82). No mesh edge joins the wall
  top (6500) to the square (5600) near the gate; the only route is 122 points ~2500 west past the camp, round
  by the low ground (138955 -141109 -> 138700 -141906), then east. Every stall (both targets) = the wall-top
  point nearest the goal -> theory: the engine's path search gives up on the long detour and walks to the
  closest point (unconfirmed). CASE: both wall -> square trips walked in 8 legs at mesh_route points.
  battle_gate.sh -> rc/battle_legs x3.
  battle_legs 1/3: run 1 PASSED the whole case (first ever); runs 2-3 stalled on the RETURN walk (low ground
  138779 -141927 -> wall top) barely moving -- same long-detour shape (86 points). CASE: + 6 return legs at
  mesh_route points. -> rc/battle_legs2 x3: 3/3 -> BATTLE FIXED (with run 1 above: 4 of 4 with the legs).
  Then ark_tail2 = arktwend_main enderal_burg_scout .. final_arkt x1: FAIL at scout, the same SW drop to (142119
  -147288 4170). CASE: scout spot -> landing walked in 5 legs at mesh_route turns (142706 -145970, 142652
  -145793, 142165 -145358, 141395 -144988, landing) -> rc/scout_legs x3.
  scout_legs 0/3: all on the climb leg (142693 -145910 5196 -> 142165 -145358 5597); within one log tick (2 s
  real ~ 50 s game) the player is already heading S, then walks off the mesh's open west edge (x ~142800) and
  drops ~580 to (142119 -147288 4168, no mesh). mesh_route + MAXSLOPE (new env): no route landing <-> scout area
  under 38-40 deg (both the scout-spot climb and the descent path need ~41 deg tris; climb tris 33-52 deg).
  🛑 MY ERROR (user: "we already went over this 'climb slopes' at fast speed before!"): the legs = archive r9
  (FAILED, same SW drop), and slope was already RULED OUT for scout: the same walk climbs straight up from the
  scout ckpt; it fails only in the state carried from the gate ckpt (archive r3-r9: rock margin, cannons,
  followers, quest stage, walk-vs-teleport start, steam vent, stale marker all ruled out). Step-size correlation
  (pass 33-43 ms, fail 55-68) is NOT evidence. Legs reverted; disasm agent stopped. Scout stays PARKED on the
  open question: what in the gate-ckpt state makes the engine route around the hill. Do NOT retry legs/slope.
  (Superseded below: scout passes with `pace 10`; camp = the claimed-door Activate bug.)
  Instance 1 meanwhile: full run 7 of the Arktwend lane (run7_ark.out). Instance 2 meanwhile: full run 7 of the Morrowind lane (MW/Tri/BM
  reimported with the Recover fix), run7_mw.out.
  Elsewhere: bm_water 1/1, bm_draugr 1/1 (run-1 failures gone); b3_task+b3_bride 1/1 (Falura stall = flake);
  a1_2_key 3/3 from a rewritten checkpoint; mw_main_tail2 passed a1_2_key .. up to b2_escort (fails: fight).
  11-07 camp leg (138955 -141109 -> 138888 -141658) stuck 3/3 at (138899 -141145): a ~30 ledge E-W at y ~-141190
  (x 138900..138960; floor 5694 -> 5729 over 20) that the shipped mesh crosses on tri 611 (65 deg); mesh_route
  without it (MAXSLOPE 45) goes round. PIN (backup pins.pre_camp_ledge.json): floor_points TrunTelurX33YN35 box
  138785 -141395 139115 -140965 band 5550..5850 step 30; pin_region box 138800 -141380 139100 -140980, notch
  "138892,-141202;138968,-141202;138968,-141168;138892,-141168" margin 25: removes 12, builds 166, min 7.5.
  Import ark_import_camp_ledge.log -> mesh_clean -> fortress x3.
  SHIPPED: mesh_clean 0/0/0/0 (181 tris), route under 45 deg (26 points), camp_ledge_pinned.png (notch cut, all
  tris within 15). fortress_ledge 1/3: run 3 PASSED the whole chain; runs 1-2 = the inner gate 05D5AC07 declined
  ALL 121 retries (original returned 0 every frame) -> the door RETRY FIX FAILS: the decline is persistent, not a
  frame's hiccup. Afterwards the player stands at (140746 -143365 5631) -- ~310 N of the landing and ~110 BELOW
  it, ~190 from the gate (the walk logged 141035 -144243 one second before the activation). Pass: activated from
  the landing, returned 1. With probe diag_gate6 (declined 2/2 at 140652 -143591): the engine declines when the
  player is NOT on the landing. Next: why the player ends off the landing (slid down?) before `activate`.
- Follower-rule debt: chapters VI+ (0840-0870) still `player->Position` with Jarko/Gargart/Calisto following.

---------------------------------------------------------------------------------------------------------
# QUESTS -- MORROBLIVION LANE
- morroblivion_main 10/10, morroblivion_quiz 4/4 (common-runner until/menu fix).
- tr_telvanni 89/89: ht_ra2_contact lands inside the room; ht_fa1/ht_oathrung Tel Oren drop 7390 4400;
  ht_oathrung_start Tirele fall FIXED by the winding oracle v2; ht_el1 SetDisposition 3/3.
- tr_necrom 18-19/19: nm_drunk causeway pins (NecromX84YN21 etc.) + walks along Olmu's route; nm_wake_start ENCH
  self-only fix; Vanryth missing-on-return = rare carry flake (3/3 from checkpoint).

---------------------------------------------------------------------------------------------------------
# UNCOMMITTED CHANGES (user commits after in-game testing)
- Meshes: OpenMW root-transform rule (nif_converter_morrowind.discard_root_transform; MW/Tri/BM/Arktwend/TD
  rebuilt; pins for the 2 MW splits); winding oracle v2 for Morroblivion (morrowind_assets.find_original_mesh,
  collision_original.py, collision_winding._original_flips; 775 meshes rebuilt); collinear CMS fix (above).
- Records: ENCH constant effects self-only (equipment.py); MW actor lift 20 (world_morrowind.py); shown exterior
  names (morrowind_travel.py, morrowind_sidecar.py); MW follow max radius 128 (ai_packages_morrowind.py).
- Navmesh: door reach (narrowed to pathgrid-less interiors; Oblivion/Nehrim unchanged); fold drop (clean.cpp);
  exact-slab lone nodes (regressions open: Puzzle Canal deck split, SadrithMoraX35Y8 +2, VivecTempleX7YN27 +1
  uncovered); pin guard v2 (decimate.cpp); from_pgrd pins on uncached builds; close_t_junctions.
  FormID moves approved: Arktwend BPTD, 3 NAVM. NOT asked: TR Kulidon/Manrizache NAVM ids, MW 02182139 removed,
  2 MW + 21 TR NAVMs added.
- Pins: Arktwend, Tribunal, Bloodmoon, Morrowind (Aharunartus, Beshara), TR (Necrom).
- Runtime DLL: steer step; route skipping; ask/choose service rows; rest cap; StartCombat dedupe; SettleGlides;
  exterior InWorld; followers through doors 4/4b/4c; RenewFeuds same place; door-reach guard + follower names;
  HeldMenuError; until/menu exemption (both runners); `approach`; pace governor v3 (+v4 unbuilt); TEMP TRACE.
- Tools: test_instance --instance/--to/unlink_plugins/uncapped FAST_INI; repeat_case.py; navm_patch path fix;
  pin_region, floor_points, mesh_clean, slab_map, authored.navm_arrays; collision_sanity rewrite;
  .claude/hooks/worklog_gate.py (locks tools after compaction until this file is read).
- Cases: see each quest above.

# PENDING (in order)
1. Urvel FIXED (slot rule v3; Gardens drop pin): 5/5 latest.  2. sohn FIXED (actor SetPos = engine move;
   shore legs walked): 5/5 latest -- remove the now-moot MoveLanding/g_afterMove machinery, rerun to confirm.
   b2 parked (see follower freeze).
3. tate: plaza FIXED (pin); X0Y6 strip unresolved (needs an in-game look).  4. Carry (masked by approach).
5. Arktwend fortress: camp 2/2; battle 0/2 (wall-top -> square walk, see enderal_burg_*); scout 1/2.
6. Measure pace v4.  7. Next full run.
Later: remove TRACE; kWalkLogTicks 60 -> 300; republish navmesh cache; docs after in-game confirmation
(folded-sheet-welds, steer, ask/choose, pins, root rule, ENCH, lift); cellview bugs; reimport Morrowind_ob/TD/TR
after the Accompany revert; ask about the unapproved NAVM id changes.
