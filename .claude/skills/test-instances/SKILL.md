---
name: test-instances
description: >-
  How to run the headless Skyrim test instances: build an instance for a plugin,
  launch, screenshot, run MWScript (.mwtest) and TES4 (.tes4test) quest cases,
  checkpoints and resuming, the 3-pass repeat loop, stopping a batch cleanly, and
  showing the user in-game proof of a fix. Use whenever a bug must be reproduced
  or a fix verified in game, or when running or writing quest test cases.
---

# Headless test instances

An isolated Skyrim on a hidden desktop: no window, sound or input on the user's
screen, nothing written to their install or saves. It costs the user no time, so
it is the default way to see a bug in game. Full detail:
[headless_testing.md](../../../docs/commentary/headless_testing.md).

Rules that apply (CLAUDE.md): reproduce an in-game bug here before fixing it,
rerun after the build, show before/after screenshots as links; never claim an
untested fix. Quest-run rules live at the top of the worklogs
([mwscript_full_runs.md](../../../docs/audits/mwscript_full_runs.md),
[oblivion_quest_runs.md](../../../docs/audits/oblivion_quest_runs.md)).

## Commands

`--instance N` goes **before** the subcommand (instances 1 and 2 run side by side;
their `Saves` are shared through a junction).

```bash
python -m tools.live.test_instance --instance 2 build --mod <output folder> [--plugin X.esp]
python -m tools.live.test_instance --instance 2 launch
python -m tools.live.test_instance --instance 2 smoke [--cell QASmoke]   # coc + screenshot
python -m tools.live.test_instance --instance 2 shot --out temp/<task>/<name>.png
python -m tools.live.test_instance --instance 2 menus | status | close <Menu> | stop
```

- **Re-run `build` after every import, sidecar change or runtime DLL build.** The
  importer writes a new ESM and the instance hard-links the old one; runtime DLLs
  are copies taken at `build`.
- `--mod` names an `output/` folder: `Morrowind.esm`, `Oblivion.esm`,
  `TESGameSelect`... Tamriel Rebuilt runs in Morroblivion mode.

## Running cases

```bash
# MWScript cases (Morrowind-format plugins); folder = tes_runtime/morrowind/testdata/quests/<quest>/
python -m tools.live.test_instance --instance 2 mwtest --cases <folder> --new-game --fast --checkpoints --stop-on-fail --timeout 3600
python -m tools.live.test_instance --instance 2 mwtest --cases <folder> --fast --checkpoints --stop-on-fail --from <case>
# TES4 cases (Oblivion, Nehrim); folder = tes_runtime/tes/testdata/quests/<quest>/
python -m tools.live.test_instance --instance 2 questtest --cases <folder> --new-game <button> --fast --checkpoints --stop-on-fail --timeout 3600
# 3 passes in a row from the checkpoint (a fix counts only at 3/3; a flaky history needs ~10)
python -m tools.live.repeat_case --cases <folder> --case <case> --times 3 --instance 2 --out temp/<task>/rc [--tool questtest]
```

- `--fast` = iFPSClamp 6 + god mode. Never slow a run globally; a case may lower
  its own speed with `pace <speed>`.
- `--checkpoints` saves `mwtest_<case>` after each pass; `--from X` loads the save
  of the case before X; `--from X --to X` runs only X. `repeat_case` never writes
  checkpoints: refresh a stale one with `mwtest --checkpoints --from X --to X`.
- `--keep` leaves the game running afterwards (for `shot` and console probes).
- A run takes minutes (the whole Morrowind main quest < 10 min); `--timeout` is
  only a hang cap. A quiet runtime log for 180 s ends the run as "GAME HUNG".
- 🛑 **Never start a run without a fallback timer.** Every background run gets
  `--stop-on-fail` and a watch that wakes you: a Monitor (or ScheduleWakeup) set
  to about twice the run's expected time (cases x ~10 s; a quest folder ~5 min),
  which checks the runtime log's last `test:` line and stops the run if it has not
  moved. A run that outlives its timer is a hang or a stuck case: read the log and
  act, never keep waiting. (A Hlaalu run sat idle 25 min on a failed case
  because none was set.) The same goes for any long background job.
  Watch: `Monitor` on `python tools/validate/safe_run.py python
  temp/full_run_2026_10_31/watch_lane.py <run .out> <instance> 180` -- it prints
  each case result, `STALLED` after 180 s with no result (a busy log counts), and
  exits on the run's totals. Monitor timeout = 2x the expected run time.
- Keep the next 2-3 quests' cases drafted, so a pass runs straight on.

## Writing a case

A case is `case <name>` ... `end`; steps run in file order; `#` comments only at
line start. Steps (`TestStepNames()` in `tes_runtime/common/test_runner.cpp`):
`run` (+ script lines), `wait`, `expect`, `until <ticks> <cond>`, `talk`, `ask`,
`choose`, `bye`, `button`, `activate`, `pick`, `close`, `take`, `console`,
`persuade`, `sleep`, `rest`, `walk`, `follow`, `steer`, `approach`, `defend`,
`punch`, `pace`, `hear`, `said`, `unsaid`. Copy syntax from an existing case in
the same folder; the step table in headless_testing.md covers the first ten.

- Only stand in for what a player does. Never move, enable or set state on an NPC.
- No follower along: place the player (`approach <ref FormID>`, Position,
  PositionCell). Follower along: one `walk` to the destination, never extra legs.
- `approach` takes a REF FormID; the "activation: X is" log line prints the BASE id.
- A walk that stalls is a navmesh question: use the **navmesh** skill.

## Stopping a batch

Stopping a background task kills only the top shell; its children keep the game
and `mwtest.jsonl` open (the next run fails with WinError 32). After stopping:

```bash
python tools/validate/safe_run.py powershell -File tools/live/find_procs.ps1 "instance 2|Skyrim Test 2"
taskkill //F //T //PID <each pid>
python -m tools.live.test_instance --instance 2 stop
```

## Proof for the user

1. Reproduce: run the case (or `smoke`/`--keep` + console placement) on the
   current build; `shot` the failure.
2. Fix, build every touched stage, `build` the instance again.
3. Rerun to 3/3; `shot` the same spot.
4. Report both images as clickable links (`[before.png](temp/<task>/before.png)`)
   with the pass count ("3/3 from checkpoint"), never a bare "PASS".
