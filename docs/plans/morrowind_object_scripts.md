# Object scripts on the Morrowind interpreter

Status: PLAN

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

The hook belongs in the DLL's main-thread update, not Papyrus. Budget matters —
3,569 scripts at 60Hz is a real cost, so only loaded objects tick, which is the
same rule Morrowind itself applies.

### 3. The commands objects use that dialogue does not

From the census, ranked by how often they lead a line:

| Command | Uses | Note |
|---|---|---|
| `StopScript` | 179 | ours |
| `Disable` / `Enable` | 211 | native |
| `Activate` | 69 | the engine's own activation |
| `Rotate` / `SetPos` / `SetAngle` | 49 | native, currently dropped |
| `SetAtStart` | — | needs the object's authored placement |
| `GetSecondsPassed` | — | frame delta, trivial once there is a tick |
| `PlaceAtMe`, `SetDelete` | 19 | native |

`GetSecondsPassed` and `OnActivate` are the two that make the difference
between a script that runs and a script that works, and both are free once the
tick exists.

### 4. Retire the Papyrus path for TES3

Once objects run on the interpreter, `blocks_morrowind.py` and the generated
`TES4_*.psc` for TES3 sources are dead weight and should be deleted rather than
left to double-drive the same objects. **Two engines writing the same state is
the specific risk that makes this a cutover rather than an addition** — it is
not safe to run both.

## Order

1. Script instances keyed by RefNum, locals in the co-save
2. The tick, loaded objects only, with a measured frame budget
3. `OnActivate`, `GetSecondsPassed`, `StopScript`, `StartScript`
4. The movement and state commands the census names
5. Delete the TES3 Papyrus path

Steps 1–3 are what make the cage door open.

## Risks

- **Frame budget.** 3,569 scripts is the population, not the working set, but
  the working set has never been measured. Step 2 must report it before step 5
  removes the fallback.
- **The cutover is one-way.** Deleting the Papyrus path with the interpreter
  half-finished leaves objects with no script at all, which is worse than a
  lossy one. The delete is last for that reason.
- **Save compatibility.** Object locals entering the co-save changes its
  contents; the format is versioned and skips unknown records, so an older save
  loads, but a save made after the cutover will not work on a build before it.
