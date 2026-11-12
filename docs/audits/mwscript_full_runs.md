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
   path across several cells around a large obstacle may need legs; anything else is a navmesh problem. Bad
   navmesh on a quest route = a CLEAN pin (no overlaps, holes, slivers, T-junctions), checked visually; never a
   generator change; no steer workaround.
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
18. 🛑🛑 (user, 11-09) EVERY walk stall is diagnosed FIRST with the engine-matched offline predictor
    (tools/navmesh/pathfind.py + pathfind_body.py via pathfind_calibrate.predict / pathfind_calibrate.py on
    walk_outcomes.py output), start -> target, its stop/reason set beside the game's; a disagreement is a tool
    calibration gap to fix. Every case walk is predicted offline BEFORE a full run. The engine often stops an
    actor where it can SEE the problem (e.g. entering the problem cell), so the mesh under a stall point proves
    nothing. On 11-09 I ignored this all day and chased slope/overhang theories with one-off scripts.
19. (user, 11-12) Flaky = fix it, never "left unchanged". Pictures for the user = clickable links + cell names +
    key + game track + target (route_full_pic.py takes a track file).
20. (user, 11-09) Overnight/autonomous work is mine to finish: don't stop to wait on questions that are mine.

## 🛑 BUG-FIX PROCESS (user, 2026-11-08 -- after weeks at ~4 Arktwend fails/run)
1. Work failures by their RATE over all logged runs (temp/.../run_totals.py + the run ledger below).
2. No edit before the mechanism is proven (reproducer or log line explaining passes AND fails).
3. Fix in the layer the bug lives in, generically. Case edits only REMOVE steps -- never add steer/pace/legs/waits.
4. One change at a time, judged by the case's rate over ~10 runs; 3/3 proves nothing (10% flake passes it 73%).
5. A change that doesn't move the rate is reverted, not stacked on. Every lane failure gets a cause.
6. Each fix logged: cause, change, rate before/after.

## 🛑 HOW THIS FILE IS KEPT (compacted 11-11 from 2,242 lines)
- Organized by quest: update the quest's entry IN PLACE -- status/rate, proven cause, fix, failed attempts.
  ~10 lines per entry max. No diary, no per-run narrative: a run gets ONE ledger line.
- Superseded detail moves to the archive, never deleted. Archives (grep them when an entry lacks detail):
  temp/full_run_2026_10_31/WORKLOG_ARCHIVE.md (10-31 .. 11-04) and WORKLOG_ARCHIVE_2.md (11-04 .. 11-11, the
  full pre-compaction file: pin coordinates, probe names, disasm chains, batch results).
- Before touching a quest: read its entry; never re-try an attempt listed under "Failed".
- A quest that keeps coming back = a generic bug patched around per case: fix the mechanism once.
- Context discipline: pull numbers (grep -c, tail -1, one field), never raw logs/scripts/JSON/disassembly.
  Heavy digging -> haiku/sonnet subagent, one-paragraph answer. Scratch scripts/logs: temp/full_run_2026_10_31/.

## Goal and procedure
- GOAL: two back-to-back, fully autonomous, fully clean full runs of every .mwtest under
  tes_runtime/morrowind/testdata (driver temp/full_run_2026_10_31/full_run.py).
  **Morrowind lane 2/2 (runs 20, 21). Morroblivion lane 2/2 (runs 16, 19). Arktwend lane 0/2 -- ACCEPTED by the
  user 11-12 at 190/191 x3 (runs 41-43; the one fail = sohn_home, the test's own long player walk).**
- Lanes (one instance each, env INSTANCES): (morrowind -> bloodmoon -> tribunal) | (arktwend) | (morroblivion).
  Morroblivion = how Tamriel Rebuilt starts (morroblivion_main --new-game 3, quiz, tr_telvanni, tr_necrom).
- Lane resume: RESUME=folder:case. A fix counts at 3/3 from the checkpoint (repeat_case.py); fail history ~10.
- Every lane watched by a Monitor on watch_lane.py; test_instance ends a run whose runtime log is quiet 180 s
  ("GAME HUNG", takes a hang dump via crash_capture.snapshot). crash_capture --watch stays attached on both
  instances (a second watcher fails on the open pid file -- don't start more).

## Launch pitfalls
- `approach` takes a REF FormID; the "activation: X is" log line prints the BASE id (TRACE / ACHR give refs).
- Launch: mwtest --timeout default 60 (repeat_case sets 3600); --mod Morrowind.esm (not Morrowind);
  a folder's first case can't take --from (repeat_case `-- --load <save>`); Bloodmoon stage = MW+BM build
  (refs carry slot 06), MW_ALL puts Bloodmoon at 07.
- 🛑 An import writes a NEW ESM; instances hard-link the OLD one -> `test_instance build` after every reimport.
- repeat_case never writes checkpoints: a stale shared save (mwtest_<case>) reproduces old bugs. Rewrite it with
  `test_instance --instance N mwtest --checkpoints --from X --to X`. A resumed case whose predecessor spoke
  the awaited line fails (quiz must run from its folder start).
- TaskStop orphans a batch + its game (locks mwtest.jsonl, WinError 32) -> taskkill /T.
- Unlink the ESM from a running game before an in-place patch (mesh_to_esm); the game keeps the old inode.

## Run ledger (one line per lane run; detail in WORKLOG_ARCHIVE_2.md)
| Run | Lane | Result | Fails |
|---|---|---|---|
| 6 (11-04) | all | -- | b2_escort, a1_2_key, bm_morale, tr_assassins, tate, sohn_worldend/home, fortress, brother, tr_shrine_dead, chargen_hall |
| 7 | MW | 3 fails | bm_beasts, co_supply_ship, co_estate, ms_performers (all governor timing/case; fixed) |
| 7 | Ark | 6 fails | tate, sohn_airship, sohn_home, verrat_brutus, burg_camp, burg_battle |
| 8 | MW | 3 fails | chargen_captain, b2_escort, co_supply_ship (carry) |
| 8 | Ark | 11 fails | tate, calisto_house, child, airship, worldend, home, scout, brother, picks, shaft, demon_king |
| 9 | MW | 1 fail | tr_shrine_dead (Gardens pin) |
| 10 | MW | 4 fails | chargen_captain, a1_v_levels (exit), ms_warlords, tr_blade (carry) |
| 11 | MW | 4 fails | chargen_captain, bm_carius, tt_join (exit), b2 parked |
| 11 | Mwob | 3 fails | quiz hang (runner), mb_chargen_sellus (dialogue timer), ht_va1_priest (exit) |
| 12 | MW | 3 fails | chargen_papers, a1_v_levels (exit), da_malacath_rumors |
| 13 | MW | CLEAN 443 | -- |
| 13 | Ark | 9 fails | tate, calisto_house, worldend, home, scout, camp, brother, mert, picks |
| 14 | Mwob | 2 fails | ht_join (exit), ht_vaerin_q6_done (alarm flag) |
| 15 | Mwob | 1 fail | ht_ra2_contact |
| 16 | Mwob | CLEAN 122 | -- |
| 17 | Ark | 194/197 | herbert_house, sohn_child, dwarves_shaft |
| 17 | MW | 442/443 | chargen_papers |
| 18 | Ark | 191/197 | herbert_house, baron_house, calisto_house, sohn_lift, sohn_home, demon_king |
| 18 | MW | 441/443 | b2_escort, b3_bride |
| 19 | Mwob | CLEAN 122 | -- (Morroblivion lane done) |
| 19 | Ark | 186/191 | herbert_house, sohn_lift, sohn_home, scout, shaft |
| 19 | MW | 440/443 | chargen_papers, b2_escort, co_razing_forest |
| 20, 21 | MW | CLEAN 443 x2 | -- (Morrowind lane done) |
| 22 | Ark | 5 fails | tate, baron_hideout (exit), calisto_house, sohn_airship, sohn_home |
| 23 | Ark | 4 fails | herbert_meet, calisto_house, sohn_home (+1) |
| 24 | Ark | 189/191 | baron_house, sohn_child |
| 25 | Ark | 186/191 | baron_hideout (exit), theater_guards, gargart_cell, goths_camp |
| 26 | Ark | 182/191 | dead_rise, gron (hang), baron_hideout, theater_guards, child, home, gate/camp/battle |
| 27 | Ark | 185/191 | gron, calisto_house, home, camp, battle, picks |
| 28 | Ark | 186/191 | dead_rise, baron_hideout (crash), home (hang), camp, battle |
| 29 | Ark | 188/191 | calisto_house, home, goths_camp |
| 30 | Ark | 189/191 | dead_rise, home |
| 31 | Ark | (not totalled) | herbert_escape, gron, demon_king |
| 32 | Ark | 189/191 | gron (case), home |
| 33 | Ark | 189/191 | sohn_irfl, demon_king |
| 34 | Ark | 186/191 | theater_guards, calisto (cascade), child, sohn_lift, home |
| 35 | Ark | 190/191 | herbert_meet |
| 36 | Ark | 186/191 | pass_judas_paid (exit), enderal_pass (exit), gargart_cell, camp, dwarves_mao |
| 37 | Ark | 190/191 | tate_door |
| 38 | Ark | 189/191 | home, dwarves_mert |
| 39 | Ark | 188/191 | storm_markus_reward, calisto_house, dwarves_mao (old case) |
| 40 | Ark | 189/191 | theater_guards, home |
| 41 | Ark | 190/191 | home (Ice Flow) |
| 42 | Ark | 190/191 | home (plank walkway drop) |
| 43 | Ark | 190/191 | home (X24Y1 ridge climb, on mesh; run43_sohn_home_stop.png) |
arktwend_side 6/6 in every Arktwend run since 13.

---------------------------------------------------------------------------------------------------------
# GENERIC MECHANISMS (cross-quest)

## Carry -- FIXED (hello approach)
- Cause (measured + disasm): Skyrim's hello evaluator (39632) gives an NPC whose package has "Hellos to player"
  and that sees the player within 150 a created Follow package (37367 -> 39300); only a spoken hello ends it, so
  the talked-to NPC followed the player through doors and after every teleport.
- Fix: activation.cpp HelloApproachHook skips the approach for Morrowind-index actors. hello_check: warlords
  6/6 (was 1-5/6), urvel 3/3, bm_morale 2/2; 0 created packages. UNTESTED: whether hello barks still play.
- Failed (don't retry): teleport-carry hook 41346; skipping the activation-interaction package (37864);
  ExtraInteraction 0xA9; EvaluatePackage at goodbye; bye settle ticks; talk-then-teleport probes.
- Related runtime rules: followers along = PlayerFollowersAlong (<= 800, OpenMW door/travel rule) for travel,
  fares, doors; PlayerParty (same place, <= 4096) for approach, door-reach guard, KillPlayerAttackers. Script
  moves of the player leave other followers behind (LeaveFollowersBehind, "stays behind", OpenMW rule; user OK).
- `approach` lines added to cases during the carry hunt stay (rule 7).

## Follower freeze -- mostly resolved; residue open
- Fixed causes: (1) Rolf (Bloodmoon, AiFollow forever) first in the player's ExtraFollower list pushed later
  followers out by the spacing term R -> FollowSpacingHook (R = 0 for our slot followers). (2) Slot reuse: a 2nd
  order into the slot whose PACK the actor still runs kept the finished procedure -> slot rule v3 (EvaluatePackage
  first) + LRU of the last 4 PACKs per actor ("ran it lately: re-evaluated first"). (3) Script SetPos of an actor
  bypassed the engine move -> actor SetPos goes through MoveInto (sohn 8/8). (4) b2: Eigma's paralysis never wore
  off -- MW AppliedOnce (0x1000) was not mapped to Recover -> magic_morrowind maps it (b2 ~50% -> 8/10).
- Residue: b2 Inner Shrine standoff (Eigma frozen at her placement in fails; she does move in passes). b2 passed
  lanes 20/21 with the stripped case. theater Wache01 follow freeze (run 34, ~1030 away after W02's greeting).
- Failed (don't retry): radius 64/128 (crowds); Accompany?=1; console evp; re-evaluate after door; teammate
  flag; RestartFollow on cell change; observe-combat interrupt bit 0x04 (reverted, MW+BM reimported back);
  swim state; lingering combat group; follow-order waits for SetPos (MoveLanding, removed); combat assist.
- Engine notes: Follow update 0x468f10 freezes a follower while a combat group targets the FOLLOW TARGET;
  ExtraFollower entry removed only by package END (11653 via 29620). Nearby combat suspends Follow; its end
  resumes it (b2: fail = fight never ends).

## Door decline (load door with a script that claims OnActivate) -- FIXED
- Cause: Skyrim's GetLock (20223) falls back to the LINKED door's lock; Enderal's outer gate locks itself 100 ->
  the inner gate's scripted Activate was refused. TES3 locks each door alone. Fix: activation.cpp OwnLockOnly
  (clears bit 0 around the original Activate when the lock is not the door's own; log "is unlocked -- its
  linked door's lock is not its own"). Door retry (kDoorActivateTries) REMOVED as a wrong theory.
- Door-reach guard: kDoorReach 512 + 128 (measured from the door origin; a walk stops up to 128 short).

## Faction-wide alarm -- FIXED
- Cause (disasm): an actor running an ALARM package reacting to a non-player target calls SetPlayerEnemy on its
  crime faction (40076+0x4FE) with no crime test -> House Telvanni enemy for 24 game h (Vaerin attacks).
- Fix: game_calls_crime InstallAlarmPlayerEnemy skips factions in plugin slots (> 4, < 0xFE). vaerin 3/3.
- Watch: ht_ra2_contact (2/11 fails since; the lv-1 contact can kill the guard on authored spells; A/B on the
  old DLL 4/4 with no guard death). Not rigged. If it recurs: compare OpenMW's AI choice for a 1-hp caster.

## Engine crash on Disable of a non-persistent scripted actor -- FIXED
- Cause (3 dumps + disasm): Disable's actor task (19801) rebuilds the MovementControllerNPC; the ctor looks up
  the actor's FormID, which is 0 for a non-persistent ACHR -> null owner -> AV at SkyrimSE+0x783642.
- Fix (import, world_morrowind.persist_script_named_refs): every TES3 actor placement whose base has a local
  script ships persistent. Counts: Arktwend 297 -> 1539, MW 800 -> 1507, Tri 210 -> 299, BM 180 -> 446
  (vanilla 1806/10504). baron_hideout 12/12 (was 4/4 crashes with that disable). Other MW-format plugins need a reimport.
- Other silent exits: ~7 since uncapped --fast; one was the doorjamb NaN crash (fixed); runs 36's two exits came
  right after a player script move with 5-6 attackers; 0 exits in 15 reruns. Watchers stay attached.

## Timing under the pace governor (~25x)
- Governor (runtime, frame timer kFrameTimer 0x31cc270 +0x10 = step in ms): PaceFrames rescales toward 25x,
  bounds 1..100 ms. v3 works (22-35x; Arktrand 0.8-5.6x). v4 (NextPaceStep) built, UNMEASURED.
- Any NEW timing failure since run 6: check the governor first (game-time windows shrink ~25x).
- Fixes: `pace <N>` runner step (per case, reset at case end); fAIInDialogueModewithPlayerTimer scales with the
  pace (KeepDialogueIdleRealTime); bm_beasts kills in one `run` block; dead actor's distance measured from its
  death spot (DistanceFromDeathSpot -- ragdolls fell through the floor, demon_king); landing check (MoveInto
  records the player's landing; Distance() answers from it until within kLandedWithin 16 -- chargen_papers).
- Failed (don't retry): controller sub-step patch (FinerControllerSubsteps; no change -- slope theory wrong for
  tate/scout/battle); iFPSClamp 6 (can't climb 38 deg); global step caps (user: never).

## Walk runner rules (test_runner)
- `approach <npc>`: player MoveTo 80 in front of the NPC, any cell, actor refs only, never with followers along.
- Followers through doors (fix 4/4b/4c): within 800 move with the player, onto the landing after 128 off / 120 ticks.
- `until` already true ignores held menus (both runners); HeldMenuError names open menus; rest refusal cap 10.
- Walk done when the player already stands at the end (no travel package; crevice fall fix). AiWalkSettledNear:
  stalled within 2 x kArrivedDistance of the end = arrived. StopAiPackage re-evaluates.
- RestartedWalk (user 11-11: "a walk that doesn't start is a framework failure"): stalled within kWalkStarted 128
  of its issue point -> re-issued, up to 3. Not yet seen firing in a lane.
- RescuedSlide (user 11-12): a stalled walk OFF the mesh asks navmesh_rescue.py for the nearest mesh point +256
  along the route, places the player, re-issues (kWalkRescues 3); ON the mesh = real stall, fails.
- A steer/walk failure appends the player's whereIs; "never loaded" appends where the NPC is.
- KillPlayerAttackers skips actors whose staged script is not yet bound (co_razing_forest) and also kills
  attackers of followers along.
- PlaySoundAt skips a disabled source (lava sound storm: 397k retries/run).

## Navmesh: pins, tools and generic defects
- Pin method: restore pins -> import -> plan on the shipped NAVM (pin_region.py) -> pin -> import (or in-place
  patch: mesh_to_esm / navm_patch) -> mesh_clean 0/0/0/0 + CheckNavMesh + picture + predictor. Always plan from the
  restored backup + unpinned reimport. Never pin a SEEDED cell near its border (neighbours re-grow).
  Every pin edit keeps a backup navmesh_pins/<plugin>.pins.pre_<name>.json (or temp/.../<plugin>.esm.pins.pre_*)
  and an ESM baseline <plugin>.pre_<name>.esm; one-off route/pin scripts (route_down, route_pins, steep_notch,
  snap_pin_seam, fix_x33yn36_pin, footprint, floor_points) live in temp/full_run_2026_10_31/.
- Recurring defects pinned: mesh under slabs/terraces/planks/platforms, channels bridged above the floor,
  floating strips, pinches, tris under a thin wall's end, mesh crossing ledges (65 deg tris), corridor routed
  under a rock shelf (route_down now has OVERHEAD + MAXSLOPE), pin verts a few units off a seam (snap_pin_seam.py).
- Fixed generically (import/tools): split NAVMs whose door tris lie in 2+ components (Wagon); door joins
  (improve-only) + landing seeds + door-at-landing pick (blast radius measured: Oblivion 12 cells, Nehrim 21,
  MW 20, Arktwend 38; protected chargen/Schattenruf cells unchanged); pins applied AFTER door choices (pin order;
  only DragonRidgeX40Y18 changes); seam lip fix in clean.cpp (bridge deck); storey gap over all overlap corners
  (FROZEN_VERSION 6); T-junction closing (FROZEN_VERSION 5); navm_patch restitches neighbours from their own
  NVNM + rewrites NAVI edge links (check.py NAVI_EDGE_LINKS); WinError 1450 parse retry; drop_buried removed
  (enclosure rule kept for the flood only).
- Open generic: 96 zero-area tris in Arktwend (5 on seams; drop_degenerate_guarded keeps them); apply_frozen
  leaves T 1 / slivers in X30Y12, holes 3 / slivers 5 in X29Y12; cellview differs from import (no spawns,
  obstacles, water; non-edge pin applied on the finished mesh); exterior To ESM loses seam links.
- Open for the USER (generator, parked): flood meshes slopes to 46 deg but the AI-walked actor slides on 41+
  (slope A/B 46 -> 41: TrollMountainsX24Y1 >41 tris 72 -> 13); exterior pathgrid cells never get the seeded
  flood (rock-static mountains unmeshed; prototype +52% area, 8 cells shrink); 11-08 flood meshed mountainsides
  the engine now sometimes routes over (gron, dead_rise, calisto).
- Offline predictor: pathfind.py (CK A* costs from disasm), pathfind_body.py, pathfind_calibrate.py;
  calibration set walk_outcomes.py -> walks.jsonl. Known false-stall classes listed in the archive.

## Crash: NaN cast hits (fixed)
- tr_assassins stack overflow: in_mh_doorjamb_02 sliver with det 0 in Havok's float32 decode -> cms.py fix,
  collision_sanity check; 102 meshes rebuilt.

---------------------------------------------------------------------------------------------------------
# QUESTS -- MORROWIND LANE (DONE: clean runs 20, 21)
- ingame 187/187; daedric 24/24 (da_boethiah_build indoors; rest cap fixed a 40-min hang); temple 70/70;
  raven_rock_brawl 3/3; mournhold 11/11 (warlords/scroll_sales SetDisposition; ms_performers player on the stage
  before the 2nd Meryn greeting); story 17/17.
- chargen_captain/papers/hall "MessageBoxMenu holds": `button 0` before `run` (case order) + button settle 2
  ticks + landing distance (kLandedWithin 16). TEMP box log in object_script.cpp still in.
- a1_2_key: carry (fixed); stale shared save once. b3_bride: Falura escort legs; passes.
- **b2_escort -- passes since the stripped case (runs 20, 21); was ~50%.** Case: walks (shrine door, Inner Shrine
  door, statue), creature kills before the first walk. Residual risk: Nals/Eigma standoff (see follower freeze).
  Failed: steer to the statue; walk onto 4471 3620; re-evaluate; Accompany; governor speed; observe flag; swim.
- bloodmoon 18/18: bm_beasts riekling kills in one `run` block; bm_carius `approach 0635C797`; bm_siege waits all
  7 werewolves; bm_trees waits the riekling disabled; bm_karstaag pins + Krish walks; bm_morale carry fixed.
- raven_rock 16/16: co_supply_ship/co_estate `approach` (Aldam approached in Raven Rock, not the mine);
  co_report shown exterior names; co_razing_forest = defend killed an unbound spriggan (runtime fix).
- tr_shrine_dead (Urvel): 10/10 after slot rule v3 + Gardens drop/north pins + Sewers crack pin; (325 -2257)
  Gardens stall seen 2 in ~25 runs before. tr_blade/ms_warlords: carry -> `approach`. da_malacath_rumors
  `approach 053E05E5`. tr_assassins: doorjamb crash fixed.

# QUESTS -- MORROBLIVION LANE (DONE: clean runs 16, 19)
- morroblivion_main 10/10; quiz 4/4 (runner until/menu fix in common test_runner.cpp); tr_necrom 19/19
  (nm_drunk causeway pins; nm_wake_start ENCH self-only); tr_telvanni 89/89 (vaerin = alarm fix; ht_el1
  SetDisposition; ht_oathrung winding oracle v2). mb_chargen_sellus = dialogue idle timer fix.
- Watch: ht_ra2_contact (see faction alarm); silent exits ht_join / ht_va1_priest were one-offs (3/3 reruns).

---------------------------------------------------------------------------------------------------------
# QUESTS -- ARKTWEND LANE (OPEN; arktwend_side 6/6). Best lanes: 190/191 (runs 35, 37, 41)

## Fixed (keep unless they fail again)
- galeone_rowboat `until 6000`. storm_calisto `until Tuniervar >= 13`. storm_markus_reward `approach 05366940`
  (3/3). gargart_cell `approach 0552AFC2` after journal 1 (6/6; Skyrim melee holds ~230). goths_camp `approach
  05A96921` (6/6). verrat_brutus `pace 5`. enderal_brother `pace 2` (moving marker boxes within 1000). tate_door
  one walk (3/3). dwarves_picks one walk (6/6). dwarves_mao one walk to the canyon door (3/3). dwarves_mert last
  steer removed (3/3). dwarves_shaft walk to the lift operator (3/3, rail pin). dwarves_demon_king death-spot
  fix (6/6) + Grul bridge/door pins. sohn_airship 3 walks. baron_house walks (3/3). baron_hideout crash fixed.
  herbert_house walk to the GorScript02 marker (3/3). herbert_meet `defend` (4/4; attackers knocked the player
  away). sohn_guard/sohn_child/sohn_irfl `defend` (4/4; snow wolves attack on arrival). sohn_worldend: pre-greet
  check accepts MenuMode (3/3). theater_calisto_house X0Y5/X0Y6 seam snap (5/5).
- Lava sound storm fixed (all MW-format games). Door decline fixed (fortress gate).

## herbert_gron -- watch
- Case: last walk to the marker itself (-47046 9635 128; Herbertvar 7 within 150) -> 3/3. Separately: dead still
  at the exact path corner (-47437.7 8800.8) in runs 26/27/31 + 2 of 6 reruns; mesh flat, clean. Mechanism OPEN.
  Not seen since run 32. HamletofGronXN12Y2 grew 212 -> 537 tris in the 11-08 flood.

## herbert_dead_rise / herbert_escape -- watch
- dead_rise: 13 of last 14 + run 30 fail at the GorXN17Y0 plateau ledge link (020421FF tri 86 -> 420). Fails head
  NW; offline route OK. Mechanism unproven, no change.
- herbert_escape run 31: dead still at the plateau edge (-66283 800 592); mesh there 70-120 under the real floor.
  User called Gor's mesh flawless on 11-09 -> SHOW THE USER before any pin.

## theater_guards -- 11/12 since `defend`
- Case: start Position ON the mesh (-776 48005 239) + `defend` (guild thieves renewed the fight; a player in
  combat runs no Travel); walk to (-690 48955) (inside W02's 500 greeting, outside W01's 400 -> W02 greets first).
- Remaining fail = first walk never starts (Travel slot 1 PACK 051D1C16, player still) -> RestartedWalk built.
  Old off-mesh start picture: run34_theater_guards_stop.png.
- Failed: start off-mesh (-343 47700) kept; on-mesh start WITHOUT defend 1/3; walk end (-343 49200) = race.

## theater_calisto_house -- watch (5/5 after the seam snap)
- Case: walks + `bye if open`; X0Y6 hill = user's long-path leg exception (steers v2 + valley legs 9960 28811 /
  11602 31869 / 14089 34673).
- Fixed: Wache02 must greet first (theater_guards walk); PastureLandX0Y6 user pin verts snapped onto the X0Y5 seam.
- X0Y6 corridor history (all FAILED, don't retry): ribbon pin; pins v1-v8 of the rock/strip (v8 kept as
  engine-valid); single walk replacing the steers; choke theory (150-172 wide); followers-as-obstacles;
  preferred-flag; slope sub-steps; stand-on-top / step / face-direction rules.

## tate_nembrest -- passing
- Case: steers v2 over X0Y6 (user allowed a FEW high-level steers) + station walk; plaza pin (pocket under the
  stands), cemetery wall-end pin, X2Y5 rock pin, X0Y6 v8. 3/3 since; not in a lane fail since run 22.

## sohn_home -- OPEN (the one recurring Arktwend fail; runs 38, 40, 41)
- Authored route (Grignir): crevice door a_Spalte_01 -> 'Ice Flow' -> 'Slate Cavern' -> exit -> descent -> Northron.
  Case: walk to each cave door + activate (0578DEC7, 05DDBAB8, 05B23937), walks down, walkway legs, walk home.
- Fixed: doors had no door tri (door joins/landing seeds); crevice mouth pin + pins-after-door-choices; crevice
  fall (walk already at its end starts no travel); descent = tri-94 south corridor + seam repair (navm_patch);
  X24Y1 zero-area seam tri (box pin restored); FrozenWildernessX40Y19 start pin; plank walkway twisted tri pin.
- Open stops (pictures for the USER, in temp/full_run_2026_10_31/): X24Y1 climb (101000 6700;
  run32_sohn_home_stop.png, noop3_sohn_home_stop.png at 101418 3128); X26Y0 pocket off the mesh's east edge
  (107816 1326; run38_sohn_home_stop.png); walkway leg goes NORTH up the mountain (96262 7812;
  noop4_sohn_home_stop.png); X29Y12 45-49 deg face slide (118999 49873; run 40). Ice Flow junction fall
  (run 41, run41_iceflow_stop.png) -> USER pinned Ice Flow 11-11,
  patched in place (021DA0B3 1900 -> 1852 tris); predictor then stops "no floor" at (3207 10122 -1270).
- rescue_home x3: 2/3 (no slide happened; fail = walkway leg north, on mesh). iceflow_pin x3 (user's Ice Flow
  pins in): 1/3. Rescue FIRED in game twice: walkway (96358 7880) -> (96239 7782 936), X29Y12 face (118801
  49675) -> (118567 49422 2271), walks went on. Fails: run 3 Ice Flow -- player came round the N ring (1062 3287
  -677) and dropped into the SAME pocket (1825 2198 -1030) as run 41 (on mesh, rescue 'none'); run 1 walk home
  stalled ON mesh at (101737 2398 4371) NordtorX24Y0/TrollMountainsX24Y1 (= noop3 picture).
- RUN 42 (restart + rescue DLL, pre-Ice-Flow-pin ESM): main 190/191, side 6/6. Fail = sohn_home walkway leg
  (97560 4122 43 -> 98304 4352 344): 30 units in, the player DROPPED 150 to the ground layer (97582 4134 -108)
  under the three-wide ex_de_shack_plank_04 run, then the engine routed it NW up the mountain (picture
  run42_sohn_walkway_stop.png). Mesh there is ON the planks (43.4 vs tops 34..44) and inside their width
  (y 4096..4175 vs 4063..4217); a ground mesh layer lies under (-109, tri 450). Plank collision fits in z 34..43
  (<= 9 thick). Lead (unproven): falls through a thin collision slab at test speed; next = compare
  ex_de_shack_plank_04's original MW collision vs ours (asked the user).
- 11-12 plank block (user: "connect the whole thing as a block"): hand frozen tris (plank_block_pins.py) in
  FelslandX23Y0 (2) + X23Y1 (3) sharing seam verts 97564.9/97679.1/97755 at z ~43; pre-existing X23Y0 hole only.
  pin_region attempt first (holes, resampled seam) reverted. rc plank_block 3/3.
- RUN 43: walk home stalled ON mesh at the X24Y1 ridge neck (99335 5178 1245), climb/slide 5 min; predictor
  also routes north over the mountain, stops climb (101025 6648). Picture for the user; no pin yet.
- Failed: Ice Flow route pin (removed 11-08); NordrenX24Y2 pin (seeded cell); tri-94 corridor (under a shelf);
  40-deg corridor pins in 3 cells (2 attempts); last-pack rule alone; follower-ahead theory.

## sohn_child -- passing (defend)
- Old steer chain + `pace 10` (user); NordrandX32YN10 wedge notch (>= 41 deg) + fold pin (9/9); bridge deck seam
  fix (5/6). Run 34 fail = wolf on arrival -> `defend` (4/4).
- Open question (user, parked): why the AI-walked actor slides on a 42 deg face the steered player climbs.

## sohn_lift -- watch
- Walks (rule 17). Run 34 fail in 'Arpk, Ancient Temple' (target on a tri 278-287 off the floor; one straight
  3200 leg; run34_sohn_lift_stop.png) -> USER pinned Arpk 11-11; userpins check 3/3.

## enderal_burg_* (fortress) -- passing on the user's pins
- gate: case waits `until player->GetPos Y < -143500` after the gate (landing y -143673). scout: `pace 10` +
  `defend` (5-6 attackers; 3/3). camp: TrunTelurX33YN35 ledge pin; X33YN36 user pin + seam fix (11-11) -> fort 3/3.
  battle: TrunTelurX34YN35 pin + the legs that work (rule 6 exception). brother = cascade.
- Failed (don't retry): legs/slope for scout (archive r9); stale-slot theory; gate-ckpt state theories (rock
  margin, cannons, followers, quest stage, start mode, steam vent, stale marker); inner-gate route for battle;
  door retry; closer scout target (engine declined from off the landing).

## Arktwend open questions for the user
- Run-26 pictures/notes: DorrenDornForestX22YN40 reshuffle from the seam fix (run26_pics/seamfix_22_40.png).
- Follower-rule debt: chapters VI+ (0840-0870) still `player->Position` with Jarko/Gargart/Calisto following.
- sohn_irfl run 33: bound proximity greeting never fired with attackers (now `defend`; watch).

---------------------------------------------------------------------------------------------------------
# UNCOMMITTED CHANGES (git status 11-11; the user commits after in-game testing)
- Staged: native navgrow (bridge/world .cpp/.h + dist .pyd), navmesh_pins.py, from_pgrd.py, Arktwend pins json,
  tests/test_navmesh_pins.py, cellview (bake/server/world/index.html), navmesh/check.py, navm_patch.py.
- Unstaged: MorrowindRuntime.dll; runtime dialogue_state.h, game_calls.cpp, game_calls_ai.cpp,
  game_calls_internal.h, game_calls_query.cpp, test_runner.cpp; Arktwend cases 0400/0600/0730/0760/0790/0800/0860;
  Arktwend pins json; test_instance.py; python_tools.md; this file. New: tools/navmesh/navmesh_rescue.py.
- FormID moves: approved for Arktwend TESTING (BPTD, NAVMs). NOT asked yet: vs pre-session 402 NAVMs new / 3 gone
  (02070E09, 02080068, 0219A866; navm_diff_session.txt) + 4 new from the tri-94 corridor; TR Kulidon/Manrizache
  NAVM ids; MW 02182139 removed; 2 MW + 21 TR NAVMs added.

# RECORDING (user 11-12): Morrowind main quest, one complete run, time-lapse (test speed), 1440p60, hidden.
- OBS on the SAME hidden desktop as the game records it (obs_hidden_probe.py: 640x360 hevc 60 fps, main menu
  frame OK). hidden_launch.launch takes `args`. OBS profile/scene "TESRecording" in the user's OBS config.
- BUILT: `test_instance mwtest ... --record DIR` (tools/live/obs_record.py): High.ini + 2560x1440, OBS on the
  hidden desktop, .mkv -> .mp4, Low.ini restored. Checked: ingame recorded at 2560x1440 60 fps, full frame
  (temp/recording_check). Its 4 fails (currentmagicka/fatigue/angle/position) = `--fast` god mode on the ingame
  cases (the lane runs ingame WITHOUT --fast); same fails at 640 with --fast. Recording run started 11-11
  (inst 1, temp/recording_mw_main[.out]) -> 72 passed, FAIL b3_bride (steer stuck 82 short of 109058 70235,
  the zigzag legs). Rule 17: steer chain (lines 119-189) -> one `walk 76766 83714 776` (backup
  temp/.../1200_b3_zainab_bride.pre_walk.mwtest); predictor: route found, false "collision ahead" at the yurt
  (77600 84186) where run 44's steers passed. Test bride_walk x3.
  User: the recording's camera rotates the whole video = Skyrim's idle vanity camera orbit (no input ever) ->
  SKYRIM_INI [Camera] fAutoVanityModeDelay=1000000 (string present in the 1.6.1170 exe). bride_walk 3/3.
  User: recording looked low-res -> launch() re-applied PREFS_INI 640x360 over the record size (fixed: launch
  takes `prefs`; 1:1 crop now sharp). Face Discoloration Fix (user's Vortex staging, FaceGenFixes.dll) linked
  via new `build --skse-plugin DLL` (removed again by the next build without it); skse64.log: loaded.
  RECORDED: temp/recording_mw_main2/2026-11-11 16-12-10.mp4, 97/97 passed, 2560x1440 60 fps, 8 m 29 s, 2.0 GB. Run 44 (MW lane) done: all clean but co_protect_falco (fixed). Recording run = morrowind stage build +
  `mwtest --cases quests/morrowind_main --new-game 2 --fast --timeout 21600 --record <dir>`; sound off.
- Run 44 (MW lane, inst 2) and run 45 (Morroblivion, inst 1) = post-change verification of the done lanes.
  Run 44: co_protect_falco FAIL (1 in 15 logged runs): last step SetPos x/y/z onto Falco, then Falco 1802 away
  for 600 ticks -> replaced by `approach Falco Galenus` (rule 7). Rerun 2/3: the fail was `until CO_12 == 20`
  with the journal already at 100 (assassins dead before the check) -> `>= 20`. falco_ge20 5/5.
  Run 45 (Morroblivion): CLEAN 122.

# PENDING (in order)
1. Read run 42 (run42_ark.out): fails, and whether "never started" / slide rescue fired.
2. Read rescue_home + iceflow_pin results (sohn_home); fix or picture each stop for the user.
3. Show the user herbert_escape's Gor plateau edge; theater Wache01 freeze if it recurs.
4. Next Arktwend full runs until 2 back-to-back clean.
Later: remove TEMP logs (activation.cpp ActivateHook; game_calls_crime SetPlayerEnemy + TempCrimeNote;
game_calls_combat health; game_calls_ai follower list/swim/fighters; ids.h GetCombatState/ExtraFollower;
object_script box owner); kWalkLogTicks 60 -> 300; measure pace v4; republish the navmesh cache; reimport the
other MW-format plugins (Recover, persistent actors, door joins); landing-link blast radius on Oblivion; docs
after in-game confirmation (hello approach, door lock, alarm, persistent actors, split/door joins, pins, root
rule, ENCH, lift); ask about the NAVM id changes above.
