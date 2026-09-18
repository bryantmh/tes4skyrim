# Object scripts on the Morrowind interpreter

Status: PLAN — step 3's command port is largely DONE; the tick is not started.

**Code it would change:** `tes_runtime/morrowind_runtime/plugin/`,
`script_convert/blocks_morrowind.py`, `tes5_import/dialogue/morrowind_sidecar.py`

Dialogue result scripts already run on the vendored OpenMW compiler and
interpreter. **Object scripts do not** — they still go down the Papyrus path,
which is lossy by construction. This is the plan for moving them across, and
the measurements that say why it is worth doing.

## What is broken today, measured

Over `TR_Mainland.esm`:

| | |
|---|---|
| SCPT records with a body | **3,569** |
| Total script lines | **156,607** |
| `.pex` files produced | **1,054** |
| Scripts reaching the game | **30%** |

The 70% that never compile are not the whole problem. The ones that DO compile
are frequently inert. `TR_Aimrah_6_CageDoor` is representative — a cage door in
the Aimrah lighthouse grotto, converted to `TES4_TR_Aimrah_6_CageDoor.psc`:

```papyrus
If (OnActivate == 1)        ; reads an undeclared property: ALWAYS 0
  ...
  If (timer < 1)
    ;NE: Rotate             ; dropped
  Else
    ;NE: TODO: SetatStart   ; dropped
```

Three separate failures in one 40-line script: `OnActivate` became a property
that is never set, so the branch is unreachable; `Rotate` and `SetAtStart` have
no emitter. The door can never open. It also calls `RegisterForSingleUpdate(0.1)`
forever, so it costs a Papyrus wakeup ten times a second to do nothing.

**The Papyrus path cannot be fixed into correctness here.** The mismatch is
structural: a TES3 body is one block that runs every frame and branches on
state, and Papyrus has no equivalent, so the conversion reshapes control flow
it does not understand.

### What it costs in quests

`tools/dialog/morrowind_quest_trace.py` measures the consequence directly over
TR_Mainland's 2,086 journal quests:

| Verdict | Quests | Stages |
|---|---:|---:|
| `OK` | 792 | 8,270 |
| `DEGRADED` (a command in the script does nothing) | 186 | 1,143 |
| `BLOCKED` (only an object script sets the stage) | 699 | 1,463 |
| `UNREACHABLE` | 409 | 850 |

**699 quests cannot be finished because an object script owns a stage.** That
is a third of the plugin's quest content, and it is nearly four times the 186
held back by unported commands — so the tick is worth more than any further
opcode work. Both Old Ebonheart Fighters Guild quests fail this way.

## Why the interpreter is the right target

The runtime already has every piece this needs:

| Piece | Where | Status |
|---|---|---|
| MWScript compiler | `external/openmw/components/compiler/` | vendored, building |
| Interpreter VM | `external/openmw/components/interpreter/` | vendored, building |
| `Interpreter::Context` | `plugin/script_context.cpp` | implemented |
| Opcode installs | `plugin/script_runner.cpp` | 507 commands, 826 opcodes |
| Locals by owner | `plugin/dialogue_state.cpp` | `mVars`, persisted |
| Declared locals per script | `MWSV.txt` | staged |
| Which script an object runs | `MWOS.txt` | staged |
| Co-save | `plugin/cosave.cpp` | versioned, working |

Result scripts prove the stack: **99.4%** of 44,950 authored result scripts
compile and run. The same compiler on the same tables would take object scripts
at the same rate, because they are the same language and the tables are already
keyed by object.

`OnActivate` stops being a broken property and becomes what it is in TES3: a
flag the engine raises for one frame, which the interpreter reads and clears.

## The work

### 1. A script instance per object

`ScriptOf(actor)` already answers "which script does this object run". The
runtime needs the other half: a live instance holding that object's locals, keyed
by the object's FormID rather than its base id, since ten crates run one script
with ten sets of variables.

`dialogue_state` keys locals by owner STRING today. That widens to the RefNum —
`(plugin index, local id)` — which is the key the co-save already contracts for.

### 2. A tick

**This is the one genuinely new mechanism.** Local scripts run every frame while
their object is loaded; global scripts run every frame, period. Neither exists
in the runtime today: `StartScript` records that a script is running and nothing
ever ticks it.

The hook belongs in the DLL's main-thread update, not Papyrus. Only loaded
objects tick, which is the same rule Morrowind itself applies.

#### <a id="tick-rate"></a>Fixed rate, not per frame

OpenMW runs local then global scripts once per RENDERED frame, uncapped
(`engine.cpp`, `executeLocalScripts()` then `getGlobalScripts().run()`), and so
does Morrowind. **We should not copy that**, for correctness rather than cost:
TES3 scripts move things a fixed amount PER TICK, not scaled by the delta --
`rotate z, -110` in `TR_Aimrah_6_CageDoor` is literal. At 144 fps that door
spins 2.4x faster than the author saw at 60.

A fixed 30 Hz accumulator with a small catch-up cap makes authored behaviour
deterministic. 🛑 **`GetSecondsPassed` must then return the TICK delta**, not
the frame delta: hand scripts a 144 fps frame time while ticking at 30 Hz and
every integrating timer runs ~5x slow, silently.

#### The budget, measured

The working set the risk section asked for, over `TR_Mainland.esm`:

| | |
|---|---|
| SCPT records with a body | 3,569 |
| Code lines, median / p90 / max | 24 / 66 / 356 |
| Placed references running a script | 15,932 |
| Cells holding at least one | 3,520 |
| Scripts per cell, median / p90 / max | **2 / 9 / 541** |

So the population is 3,569 but the loaded set is single digits typically. Even
the 541-script outlier is a fraction of a frame -- but it lands on one frame in
four, and a periodic spike reads as judder. **Stagger the ticks across slots
and cap the tick with a time budget**, resuming next frame: a 30 Hz script does
not care which frame it lands on, and that bounds the cost whatever a cell
holds.

#### 🛑 Execution cannot be async

Script bodies read and write game state (`GetPos`, `Rotate`, `Enable`,
`GetDistance`), which Skyrim is not thread-safe for, and TES3 scripts assume a
coherent world each tick. `SetCombat` and `SetEnabled` already `PostToMainThread`
for exactly this reason. **Compilation** is the part that can go on a worker
pool -- 3,569 scripts and 122,550 lines, touching no game state, and the only
multi-second cost in the design.

#### 🛑 Do not use the tick-need classification to SKIP a tick

Classifying bodies as event-only vs polling vs timed suggests 14% could be
driven purely by `OnActivate`/`OnDeath` with no tick. **Measured, that
classification is wrong 43% of the time**: of 1,819 scripts containing an event
keyword, 778 have statements OUTSIDE the event guard. `TR_m4_Malmas_script`
sets a map flag unguarded; `TR_m4_NPC_FatherToMany` has `OnDeath` AND a
`GetDistance` proximity greeting. A keyword scan picks one and is wrong about
the other, and the failure is silent -- an NPC that never greets you, three
hours in.

Structural analysis does not rescue it either: a guard can be a variable set on
a previous tick, which is dataflow, not syntax. **Tick everything; use the
classification only to choose a RATE.** Being wrong then costs latency, not
correctness.

### 3. The commands objects use that dialogue does not

**Largely DONE.** These shipped once `MWRF.txt` gave the runtime an id ->
placed-reference index, since almost all of them name a reference:

| Command | Calls | Status |
|---|---:|---|
| `Enable` / `Disable` / `GetDisabled` | 6,031 | done |
| `StartCombat` / `StopCombat` | 1,764 | done |
| `MenuMode` | 1,111 | done |
| `GetDistance` | 782 | done |
| `ForceGreeting` | 579 | done |
| `Activate` | 504 | done |
| `GetPCCell` / `GetInterior` | 403 | done |
| `Unlock` / `Lock` / `GetLocked` | 608 | done |
| `GetRace` | 238 | done |
| `SetDelete` | 133 | done |
| `Equip` | 127 | done |
| Health / Magicka / Fatigue: `Get`/`Set`/`Mod`/`ModCurrent` | — | done |
| `StopScript` | 179 | done |

Still open, and all of them want the tick rather than a reference:
`GetSecondsPassed` (1,005), `CellChanged` (983), `OnDeath` (958),
`OnActivate` (852), `GetPos`/`SetPos`/`MoveWorld`/`Rotate` (~2,150),
`SetAtStart` (needs the authored placement).

`GetSecondsPassed` and `OnActivate` remain the two that make the difference
between a script that runs and a script that works.

### 4. Retire the Papyrus path for TES3

Once objects run on the interpreter, `blocks_morrowind.py` and the generated
`TES4_*.psc` for TES3 sources are dead weight and should be deleted rather than
left to double-drive the same objects. **Two engines writing the same state is
the specific risk that makes this a cutover rather than an addition** — it is
not safe to run both.

## Order

1. Script instances keyed by RefNum, locals in the co-save
2. The tick, loaded objects only, staggered and budget-capped, at a fixed rate
3. `OnActivate`, `OnDeath`, `CellChanged`, `GetSecondsPassed`
4. The movement commands: `GetPos`/`SetPos`/`Rotate`/`MoveWorld`/`SetAtStart`
5. Delete the TES3 Papyrus path

Step 3 of the original plan — the commands objects use that dialogue does not —
is largely done already, so the remaining path is shorter than it was.

### <a id="sharnoga"></a>The shortest path to a playable quest line

Sharnoga gra-Mal's Old Ebonheart Fighters Guild quests are the test case, and
`morrowind_quest_trace.py --actor` says each blocks on exactly ONE stage set by
one small object script:

| Quest | Stage | Script |
|---|---|---|
| Cursing Like a Witch | 40 | `TR_m3_OE_FG_q_VermaiScr` |
| A Champion Lost | 20 | `TR_m3_OE_FG_q_ConstJo1Scr` |
| A Final Fate | 20 | `TR_m3_OE_FG_q_LenwynScr` |

All three are under 25 lines, and every command they name is now ported except
`OnActivate`, `OnDeath`, `CellChanged`, `SetFatigue`/`SetHealth` (done) and
`MessageBox`. **They need almost none of the tick**: `LenwynScr` is pure
`OnActivate`, `ConstJo1Scr` is `OnActivate` plus a visibility guard that only
matters on cell load, and only `VermaiScr` wants a periodic check, for
`CellChanged` — which fires on transition, not per frame.

So steps 1 + 3 alone, with events hooked and no scheduler, make this quest line
playable, and prove the design end to end before the expensive part is built.

## Risks

- **Frame budget.** MEASURED: median 2 scripts per cell, p90 9, worst 541. The
  population is 3,569 but the loaded set is single digits typically, so the
  cost is scheduling the 541-cell spike, not the total. See
  [the budget](#tick-rate).
- **The cutover is one-way.** Deleting the Papyrus path with the interpreter
  half-finished leaves objects with no script at all, which is worse than a
  lossy one. The delete is last for that reason.
- **Save compatibility.** Object locals entering the co-save changes its
  contents; the format is versioned and skips unknown records, so an older save
  loads, but a save made after the cutover will not work on a build before it.
