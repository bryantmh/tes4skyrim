# Object scripts on the Morrowind interpreter

Status: **BUILT and CONFIRMED IN GAME** (2026-09-18). Object scripts run on the
interpreter; the TES3 Papyrus path is deleted. Remaining gaps are individual
COMMANDS, listed under [what is left](#what-is-left).

## <a id="status"></a>Implementation status, measured

| | Before | Now |
|---|---:|---:|
| SCPT bodies that compile | 1,054 of 3,569 (30%) | **3,395 of 3,569 (95.1%)** |
| Ported commands / call sites | 82 / 74,322 | **95 / 77,115** |
| Quests OK | 792 | **1,201** |
| Quests BLOCKED by an object script | **699** | **0** |
| Quests DEGRADED by an unported command | 186 | 476 |
| Quests UNREACHABLE (nothing sets the stage) | 409 | 409 |

The BLOCKED tier is gone: a stage an object script sets is now as reachable as
one set from dialogue, which is what moved 699 quests. The DEGRADED count rose
because those quests are no longer hidden behind BLOCKED — the same stages are
now reported against the exact command they still need.

✅ **CONFIRMED IN GAME**: Sharnoga gra-Mal's Fighters Guild line, all 6 quests
`OK`. *Cursing Like a Witch* completes end to end — `PlaceAtPC` spawns the
vermai, its script binds and ticks, `OnDeath` fires on the kill, the journal
advances to 40 and Foedus's dialogue follows.

### <a id="what-is-left"></a>What is left, ranked by stages it degrades

`morrowind_quest_trace.py --all --blockers` over TR_Mainland:

| Command family | Stages | Note |
|---|---:|---|
| `aiwander` `aifollow` `aitravel` `aifollowcell` | **444** | AI packages; the largest single win left |
| `positioncell` | 149 | needs a cell -> FormID table, which is not staged |
| `addspell` `removespell` `cast` `explodespell` | ~290 | needs a spell id -> FormID table |
| `removesoulgem` `addsoulgem` | 39 | |
| `modmercantile` `modalteration` `modalchemy` | 44 | skill mods |
| `payfinethief` | 18 | |

✅ **Every family above is now ported except `payfinethief`** — the AI packages,
the cell and spell tables, the soul gems and the skill mods all landed, and the
audit's "Blocked on EXPORT or IMPORT" section no longer emits because no
data-blocked command is still stubbed. `payfinethief` (18 calls) is the largest
pure-runtime gap left, ahead of `payfine` at 4.

🛑 The stub counts this table was first built from were inflated by prose: the
audit scored quoted dialogue as calls until it learned to strip string
literals, which put `Help` on top at 398. See
[the audit's string rule](../commentary/morrowind_runtime.md#opcode-audit-strings).

🛑 **The 409 UNREACHABLE are NOT a runtime gap.** Nothing in either corpus sets
those stages -- they are authored dead ends, or set by a mechanism TES3 itself
does not expose as a `Journal` call.

**Code it would change:** `tes_runtime/morrowind/plugin/`,
`tes5_import/dialogue/morrowind_sidecar.py`

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
opcode work.

## Why the interpreter is the right target

The runtime already has every piece this needs:

| Piece | Where | Status |
|---|---|---|
| MWScript compiler | `external/openmw/components/compiler/` | vendored, building |
| Interpreter VM | `external/openmw/components/interpreter/` | vendored, building |
| `Interpreter::Context` | `plugin/script_context.cpp` | implemented |
| Opcode installs | `plugin/script_runner.cpp` | 487 commands, 82 ported |
| Locals by owner | `plugin/dialogue_state.cpp` | `mVars`, persisted |
| Declared locals per script | `SCPT_locals.txt` | staged |
| Which script an object runs | `SCPT_objects.txt` | staged |
| Placed reference per id | `refs_formid.txt` | staged |
| Co-save | `plugin/cosave.cpp` | versioned, working |

Result scripts prove the stack: **99.4%** of 44,950 authored result scripts
compile and run. The same compiler on the same tables would take object scripts
at the same rate, because they are the same language and the tables are already
keyed by object.

`OnActivate` stops being a broken property and becomes what it is in TES3: a
flag the engine raises for one frame, which the interpreter reads and clears.

## <a id="what-is-not-staged"></a>🛑 The script BODY is not staged today

**Measured, not assumed:** `morrowind_sidecar.py` writes `SCPT_locals.txt` (declared
locals) and `SCPT_objects.txt` (object → script name) from `SCPT.txt`, and it writes
**neither the source text nor anything keyed to a placement**. `declared_locals`
reads `SCTX` and keeps only the declarations; the body is discarded.

So step 1 is not only a runtime change. A new table has to carry the 3,569
bodies — call it `SCPT_source.txt` — or the interpreter has nothing to compile.

## <a id="built-in-globals"></a>🛑 The globals the compiler must know

**A name the compiler does not know is a global breaks the whole body**, not
just the line: the parser answers "not a global", then the comparison that
follows is an unexpected token and the script fails to compile. Measured over
TR_Mainland, this was most of what did not compile, in two groups.

**Engine globals**, which no GLOB record anywhere declares — `GameHour`,
`Day`, `Month`, `Year`, `DaysPassed`, `TimeScale`. Morrowind defines them
itself, so the runtime does too (`script_tables.cpp:AddBuiltinGlobals`).
**363 of 4,316 bodies name one**, and supplying them took failures from
**513 → 181**, a compile rate of 88.1% → 95.8%.

**Vanilla GLOB records** — `CharGenState`, `PCVampire`, `PCRace`, `VampClan`.
These are authored in `Morrowind.esm` and reach us through the Morroblivion
patch's export, not through the plugin being converted. See below.

## <a id="masters-stage-themselves"></a>🛑 A master stages its OWN sidecar

**The runtime already reads EVERY sidecar folder into one set of tables**
(`store.cpp:LoadStoreFrom`, first-wins `emplace`), so a plugin's sidecar must
carry only the plugin's own records. Cross-plugin names resolve because the
master's sidecar is loaded too — not because each plugin copied its masters in.

In Morroblivion mode, vanilla Morrowind/Tribunal/Bloodmoon data belongs to
**`Morrowind-Morroblivion-Compatibility.esp`**, whose export already holds it
as text: **139 GLOB** records (`CharGenState`, `PCVampire`, `Day`, `GameHour`)
and **1,204 SCPT** bodies including `skinkScript`. `Morrowind_ob.esm` holds the
placements plus its own 950 scripts.

🛑 **Do NOT read the TES3 binaries for this.** An earlier attempt had `gather`
parse Morrowind + Tribunal + Bloodmoon + Tamriel_Data to collect GLOB and SCPT.
It worked, but it was both slow (a multi-minute parse before anything is
written) and wrong: measured, it staged **1,207 vanilla bodies into TR's own
sidecar of which ZERO are run by any TR placement**, and of the 2,940 distinct
scripts TR's placements do run, the only master-owned ones are 56 `T_Sc*` from
Tamriel_Data, which is already an exported master.

Even outside Morroblivion mode the rule holds: reference the master's sidecar,
never copy it. Checking every master's data is still right — a plugin may name
anything — but the check reads the masters' EXPORTS, which is a text scan.

🛑 **`Morrowind_ob.esm` stages NO script bodies.** Its 950 SCPTs are TES4's
language (`ScriptName` / `scn`), so feeding them to the MWScript compiler made
1,226 bodies fail. **Morroblivion mode is the ONLY case where a TES3 plugin's
master is a TES4 plugin**, so the gate is the plugin name (`morrowind_ob`), not
a scan of the source — those scripts belong to the Papyrus path.

🛑 **Both modes are live.** Morroblivion mode and standalone Morrowind mode are
both supported targets; neither may be treated as the only one. In Morroblivion
mode the vanilla records reach us through
`Morrowind-Morroblivion-Compatibility.esp`; in Morrowind mode they come from
converted `Morrowind.esm` / `Tribunal.esm` / `Bloodmoon.esm` themselves. Either
way the owner stages them and dependents reference them.

### <a id="cumulative-gather-must-go"></a>TODO: the cumulative dialogue gather still duplicates

**Not yet done.** `morrowind_sidecar.py:write_morrowind_sidecar` still calls
`gather(plugin_chain(...))`, which reads every TES3 master BINARY and merges
their dialogue into the dependent's own sidecar. Measured on TR_Mainland:
`INFO.txt` is **76 MB against TR's own 51 MB**, so ~25 MB is its masters'
dialogue copied in, and every dependent plugin re-copies it. The actor,
faction, GMST and SKIL tables come from the same pass and duplicate the same
way. **This must move to per-owner staging like the scripts did.**

🛑 **It cannot simply be deleted, and here is the blocker.** Dialogue is an
ORDERED union: `export_INFO` assigns `Ordinal` by walking the merged PNAM/NNAM
chain over the whole chain at once, and `store.cpp:SortInfos` sorts on
`Ordinal` alone — **PNAM/NNAM are never exported**. Stage each plugin's
dialogue separately today and ordinals restart per sidecar, so a master's
greeting no longer correctly precedes a dependent's override and the filter
silently picks the wrong response.

So the fix is two-part, in this order:
1. Export `PNAM`/`NNAM` on each INFO (`morrowind_dialog.py:export_INFO`).
2. Have the runtime merge the chains across sidecars at load
   (`store.cpp`, OpenMW's `InfoOrder::insertInfo`, already mirrored in
   `morrowind_sidecar_source.py:_place`), and stop trusting `Ordinal` across
   plugin boundaries.

Only then may the `gather` call and `_stage_dialogue`'s merge branch go.

## <a id="messagebox"></a>`MessageBox` outside a conversation

`MessageBox` is a compiler BUILTIN, not an extension — it emits `opMessageBox`,
which the interpreter routes to `Context::messageBox`. Inside dialogue that
appends to `State().messages` and the menu renders it. An object script has no
menu open, so it needs the game's own.

🛑 **`Debug.MessageBox(string)` (ID 55376) is the right native, and
`Message.Show` is not.** `Show` displays a MESG *record*, so its text is
authored at build time; TES3 passes arbitrary runtime strings, and the corpus
has thousands of distinct ones. `Debug.MessageBox` takes the string directly.
It was located at its registration — `lea r8,['Debug'] / lea rdx,['MessageBox']`
at `0x9a9598`, callback stored into `[rdi+0x50]` after the call, `0x9a8640`.

## <a id="placeatpc"></a>`PlaceAtPC` and the instance a placement table cannot hold

`PlaceAtPC`/`PlaceAtMe` create a reference at runtime, so it has **no authored
placement** and no row in `SCPT_instances.txt`. Measured: `tr_m3_OE_FG_cr_verm`
appears in `CREA.txt` and in ONE INFO result script and nowhere else — Foedus
Locutius's `Greeting 1` runs `placeatpc "tr_m3_OE_FG_cr_verm" 1 1 1`, and
*Cursing Like a Witch* stage 40 comes from the script on that creature.

So the instance is created from the reference `PlaceAtMe` RETURNS, and bound by
its runtime FormID exactly as an authored placement is. This is the second
reason instances key on the placement rather than the base: a base key could
not tell two spawned vermai apart.

🛑 **Its locals must persist.** `deadDone` guards the whole body, so an instance
that forgets it re-runs the quest stage. The key is the runtime FormID of a
reference that did not exist when the save was made, so it is written into the
co-save with its plugin and local id like any other.

## The work

### <a id="instances"></a>1. A script instance per PLACED reference

`ScriptOf(id)` answers "which script does this base object run". The runtime
needs the other half: a live instance holding that object's locals, keyed by
the **placement**, not the base.

🛑 **This keying is forced by the data, not by taste.** Measured over
`TR_Mainland.esm`:

| | |
|---|---:|
| SCPT records with a body | 3,569 |
| Base objects carrying a script | 5,175 |
| Placed references running a script | 8,343 |
| Scripted bases placed exactly once | 4,070 |
| Scripted bases placed **more than once** | **798** |
| ...of those, whose script **declares locals** | **303** |
| Highest multiplicity of one scripted base | **116 placements** |

Keying locals by base id would collapse 116 separate doors onto one variable
set. `dialogue_state` keys locals by owner STRING today, which is exactly that
mistake; it has been harmless only because dialogue speakers are placed once.

The key becomes the placement's FormID, which is what the co-save already
contracts for. 🛑 **Store it masked to the low 24 bits with its plugin name**,
never the raw id — the index byte is the converting order, not the player's
(`project_sidecar_formids_need_runtime_index`).

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

#### <a id="repost-freeze"></a>🛑 The driver must SLEEP off the game thread, not re-post itself

**Measured 2026-09-18: the game froze at the main menu.** The first driver was
a task that re-posted itself from inside `PostToMainThread`, on the theory that
the task interface runs one such task per frame. It does not — the new task
lands in the SAME pump sweep the pump is already draining, so the sweep never
ends, no frame ever passes, and the game hangs before the menu appears.

This is the identical hazard `game_calls.cpp` already documents for the
objective wait: *"a task that reposts itself drains in the same pump sweep and
never lets a frame pass, which froze the game for the whole wait."*

The driver is therefore a detached thread that **sleeps one delta off the game
thread** and posts only the tick's work. The sleep is the rate.

🛑 **A second freeze cause sat beside it.** `BindInstances` resolved each
instance by trying `.esm` then `.esp`, which is **31,080** `Game.GetFormFromFile`
calls for TR_Mainland's 15,540 rows, each interning a `BSFixedString`, all at
`DataLoaded`. The extension is now resolved once per PLUGIN.

#### <a id="unload-with-the-cell"></a>🛑 An instance must UNBIND when its object unloads

TES3 runs a local script only while its object is loaded. Bindings are created
lazily and were never removed, so the bound set only ever grew and instances
kept ticking for things that had left the world.

The tick now drops an instance whose `ObjectReference.Is3DLoaded()` (ID 56188)
is false. **The binding goes; the INSTANCE stays** — its locals live in
`DialogueState` under the instance's own key, which is what TES3 keeps across an
unload, so a door left open is still open when the cell comes back.

🛑 **KNOWN GAP: an instance binds only when a hook touches its reference** —
an activation, or the `PlaceAtMe` return. A scripted object you merely walk
past never ticks until you click it, so a body that acts on proximity
(`GetDistance`) or on cell entry alone does not run yet. Closing this needs an
enumeration of the loaded cell's references at cell-load, which is not built.

#### <a id="spawned-refs-need-getform"></a>🛑 A SPAWNED reference is reachable only by its runtime FormID

**Measured in game:** the vermai spawned and its body ran — the log shows
`object: spawned FF0017D8 runs 'TR_m3_OE_FG_q_VermaiScr'` and then
`local: spawn:FF0017D8|0017D8.doonce = 1` a second later — but killing it never
raised `OnDeath`, so the quest stayed at stage 30.

`PollDeath` resolved its reference with `Game.GetFormFromFile(plugin, local)`.
For a spawned instance the "plugin" is the synthetic key `spawn:FF0017D8`,
which names no file, so the lookup could never succeed. **`PlaceAtPC` creates a
reference with no authored placement at all**, and that is exactly the creature
whose death a quest turns on.

The instance now remembers the FormID the engine minted for it
(`ObjectScript::RuntimeFormId`), and the death check goes through
**`Game.GetForm`** (ID 55566), which takes a runtime FormID. The form is cached:
the poll runs for every bound instance 15 times a second.

#### <a id="check-the-segment"></a>🛑 An opcode's SEGMENT comes from its registration, not its signature

**Measured in game: `script: 'placeatpc' is not ported yet -- it did nothing`,
while `mwscript_opcode_audit.py` called it `ported`.** Both were telling the
truth about different things — the handler was installed, and it was installed
where nothing dispatches.

`placeatpc` takes `clflX`. The trailing `X` is an optional argument, which is
what segment 3 exists for, so it was installed with `Real3`. It is a **segment
5** command: the compiler emits `0xCA00019C` for it, tag `0x32`.

🛑 **Read the emitted WORD; do not infer the segment from the argument
string.** `Extensions::registerInstruction` decides the segment, and the only
way to know is to generate the command and look at the top six bits.

##### The guard that makes this loud

A handler installed under a code no command emits is invisible: the command
runs its stub and logs "not ported", while every install-side check says it is
ported. `Machine::CheckRealOpcodesReachable` now closes that gap — `StubKeyword`
already asks the extension table to emit every command, so those words are the
ground truth for what is reachable. Any real opcode missing from that set is
reported at load:

```
script: REAL segment-3 opcode 412 is UNREACHABLE -- no command emits it,
        so its stub answers instead
```

This is what found the bug once the symptom was known, and it will fire on the
next one before a build-and-play cycle is spent on it.

#### <a id="only-persistent-refs-exist"></a>🛑 Only a PERSISTENT reference exists before its cell loads

**Measured in game: `139 of 15540 script instance(s) resolved`.** Not a data
fault — every one of the 15,540 staged placements is present in the built ESM
(10,445 ACHR + 5,095 REFR, verified by walking the record headers). The
resolver was simply asking too early.

`Game.GetFormFromFile` answers only for a form the engine has LOADED. Reading
the record flags explains the number exactly:

| | |
|---|---:|
| staged instances | 15,540 |
| with the persistent flag (`0x400`) | **139** |
| not persistent | 15,401 |

**139 is precisely what bound.** A non-persistent reference does not exist at
`DataLoaded` — it comes into being when its cell loads and dies when that cell
unloads, and its runtime FormID cannot be precomputed at all.

So instances are **never resolved up front**. They bind LAZILY, from the live
reference an engine hook already holds: `InstanceForRef` takes the FormID the
hook was handed, masks it, and finds the staged row by local id alone —
`script_tables.cpp:InstanceByLocal`. The row therefore has to carry everything
the instance needs, which is why `SCPT_instances.txt` is
`placement FormID=Plugin.esm|base id|script`.

#### <a id="no-tick-before-a-game"></a>🛑 Nothing may tick before a game is loaded

**Measured: three "You pry open the lock marked 'A'" boxes on the TITLE
SCREEN.** The main menu still runs SKSE's task pump, so the tick ran, the
persistent instances that had bound ran their bodies, and their `MessageBox`
calls popped over the menu.

`RunOneTick` now returns unless `Hooks().playerCell()` is non-empty. An
unloaded game has no player cell.

#### <a id="mask-before-getformfromfile"></a>🛑 MASK the id before `Game.GetFormFromFile`

**Measured in game: `0 of 15540 script instance(s) resolved`, so every object
script was dead while everything logged as installed.** `SCPT_instances.txt`
keys rows by the FormID as the CONVERTING load order spelled it
(`0x03BF7A31`); `Game.GetFormFromFile` takes a plugin-LOCAL id. Passing the
unmasked value resolves nothing, silently.

`ResolveIndex` and `game_calls.cpp:Form` have always masked with `& kLocalMask`.
`BindInstances` and `IsDeadRef` did not, and that one omission is the whole
difference between a working object-script engine and an inert one. It is the
same trap as [the sidecar's index byte](#instances): **the only id that means
anything across load orders is the low 24 bits.**

`IsDeadRef` additionally CACHES the resolved reference. It runs per bound
instance per tick, and two internings × 15,540 instances × 15 Hz is not
affordable.

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

### 3. The events, which are TWO mechanisms and not one

🛑 **The plan previously treated `OnActivate` and `OnPCEquip` as one thing.
They are not, and the difference decides where each is implemented.**

**Registered opcodes** (`extensions0.cpp` `registerFunction`): `onactivate`,
`ondeath`, `onmurder`, `onknockout`, `cellchanged`. These are functions the
interpreter calls, so each is an `Opcode0` installed in `script_runner.cpp`
that reads the instance's latched flag and clears it.

**Engine-written LOCALS**, which are NOT registered anywhere: `OnPCEquip`,
`PCSkipEquip`, `OnPCAdd`, `OnPCDrop`, `OnPCHitMe`. The script `declare`s them
itself (`short OnPCEquip`) and the engine writes the variable before the tick.
Measured over TR_Mainland: **322 of 3,569 scripts (9%) declare one.**

| Engine-written local | Scripts declaring it |
|---|---:|
| `OnPCHitMe` | 160 |
| `OnPCEquip` | 111 |
| `PCSkipEquip` | 101 |
| `OnPCAdd` | 43 |
| `OnPCDrop` | 6 |

So the tick must, before running a body, write these by NAME into the
instance's locals when the corresponding game event fired. An opcode
implementation would never be reached, because the compiler resolves the name
to a local slot and emits a load, not a call.

### 4. The commands objects use that dialogue does not

**Largely DONE.** These shipped once `refs_formid.txt` gave the runtime an id ->
placed-reference index, since almost all of them name a reference. The current
audit (`docs/audits/mwscript_opcodes.md`) measures **82 ported commands over
74,322 call sites; 400 stubbed over 14,644** — and 207 of the stubs have zero
call sites in either corpus, so the real remainder is 193 commands.

Still open, and all of them want the tick rather than a reference:
`GetSecondsPassed` (1,005), `CellChanged` (983), `OnDeath` (958),
`OnActivate` (852), `GetPos`/`SetPos`/`MoveWorld`/`Rotate` (~2,150),
`SetAtStart` (needs the authored placement).

### 5. Retire the Papyrus path for TES3

Once objects run on the interpreter, `blocks_morrowind.py` and the generated
`TES4_*.psc` for TES3 sources are dead weight and should be deleted rather than
left to double-drive the same objects. **Two engines writing the same state is
the specific risk that makes this a cutover rather than an addition** — it is
not safe to run both.

**DONE.** `script_convert/blocks_morrowind.py` and
`assemble.py:_retype_implicit_blocks` are deleted; nothing in `script_convert/`
mentions TES3 any more (the remaining `Morrowind_ob` hits are the TES4
Morroblivion plugin, which keeps the Papyrus path). The measurement that
justified the cutover is kept in
[script_convert_morrowind.md](../commentary/script_convert_morrowind.md).

## Order

1. Stage the bodies (`SCPT_source.txt`) and key instances by placement FormID
2. `OnActivate` and the engine-written locals, event-driven, no scheduler
3. The tick, loaded objects only, staggered and budget-capped, at a fixed rate
4. `OnDeath`, `CellChanged`, `GetSecondsPassed`
5. The movement commands: `GetPos`/`SetPos`/`Rotate`/`MoveWorld`/`SetAtStart`
6. Delete the TES3 Papyrus path

### <a id="sharnoga"></a>The first target: Sharnoga gra-Mal's Fighters Guild line

Sharnoga gra-Mal in Old Ebonheart is the test case.
`morrowind_quest_trace.py --actor "TR_m3_Sharnoga gra-Mal"` measures **6 quests,
of which 4 are BLOCKED**, on **7 object scripts** — not the 3 this plan named
before:

| Quest | Stage | Script |
|---|---|---|
| A Champion Lost | 20 | `TR_m3_OE_FG_q_ConstJo1Scr` |
| Cursing Like a Witch | 40 | `TR_m3_OE_FG_q_VermaiScr` |
| A Final Fate | 20 | `TR_m3_OE_FG_q_LenwynScr` |
| A Final Fate | 30 | `TR_m3_OE_FG_q_ConstJo3Scr` |
| A Final Fate | 40 | `TR_m3_OE_FG_q_ConstSkeScr` |
| A Final Fate | 55 | `TR_m3_OE_FG_q_ConstAxeScr` |
| A Legacy's Trail | 30 | `TR_m3_OE_FG_q_ConstJo2Scr` |

All seven are under 45 lines. Read against the audit, every command they name
is already ported except these six:

| Needed | Kind | Where it lands |
|---|---|---|
| `onactivate` | registered opcode | `script_runner.cpp` |
| `ondeath` | registered opcode | `script_runner.cpp` |
| `cellchanged` | registered opcode | `script_runner.cpp` |
| `OnPCEquip` / `PCSkipEquip` | engine-written local | the tick, by name |
| `MessageBox` | compiler builtin, already routed to `Context::messageBox` | needs a non-dialogue sink |
| `placeatpc` | registered opcode, stubbed | `script_ops_world.cpp` |

🛑 **`MessageBox` already works** — it is not an extension command at all but a
compiler builtin emitting `opMessageBox`, which `DialogueContext::messageBox`
already handles by appending to `State().messages`. Outside a conversation
there is no menu rendering that list, so an object-script context needs to send
it to Skyrim's own notification instead.

🛑 **The vermai has NO authored placement.** `refs_formid.txt` cannot name it: the
creature `tr_m3_OE_FG_cr_verm` is created at runtime by `PlaceAtPC` from
Foedus Locutius's `Greeting 1` result script, and `placeatpc` is stubbed (128
call sites). So the Sharnoga line needs script instances to attach to
**dynamically created references**, which the base-id tables cannot express —
this is the second reason instances key on the placement rather than the base.

The other two script hosts are placed exactly once each
(`TR_m3_OE_FG_bk_ConJou1`, a BOOK; `TR_m3_OE_FG_w_LenwynTanto`, a WEAP).

🛑 **The activation hook is NPC-only today.** `InstallActivation` swaps one slot
in the `TESNPC` vtable (`ids::kNpcVtable`, slot `0x1b8`). Five of the seven
scripts sit on a BOOK, a WEAP or a skeleton ACTI, whose Activate dispatches
through their own vtables — so widening the hook to non-actor types is part of
step 2, not an afterthought.

#### <a id="activate-is-per-type"></a>🛑 `Activate` is OVERRIDDEN per type, so one hook cannot cover it

Read straight out of `SkyrimSE.exe` at vtable byte `0x1b8` (the slot the
dispatch `mov rcx,[ref+0x40] / mov rax,[rcx] / call [rax+0x1b8]` uses):

| Activate | Types holding it |
|---|---|
| `0x233b60` | MISC, ARMO, Key, Ingredient, Alchemy, Ammo, STAT — the inherited `TESBoundObject::Activate` |
| `0x379930` | NPC |
| `0x2470a0` | WEAP |
| `0x23df20` | DOOR |
| `0x23c920` | CONT |
| `0x234e40` | ACTI |
| `0x23ace0` | BOOK |
| `0x22fe60` | LIGH |
| `0x228870` | FLOR |

So the base slot is NOT shared: **9 distinct functions**, and a single swap on
`TESBoundObject` would miss BOOK, WEAP, ACTI, CONT, DOOR, LIGH, FLOR and NPC —
which is most of what carries a script. The hook is one vtable swap per type,
each refusing to install unless the slot already holds the function it expects,
exactly as the NPC swap does today.

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
- **Instance identity across a save.** Keying on a placement FormID is stable
  for authored refs. A `PlaceAtPC` creature has no authored id at all, so its
  instance must be re-keyed from the created reference and persisted with it,
  or the vermai's `deadDone` resets on every load.
