# tools/dialog/ - in-game test methodology

**Code:** `tes5_import/base/conditions.py`, `tools/live/quest_labtest.py`, `tools/live/dialog_live.py`, `tools/live/game_input.py`

## Contents

- [1. Why a clean room](#1-why-clean-room)
- [2. The clean room: 🛑 AnvilMarkTest](#2-clean-room-anvilmarktest)
- [3. The generic discovery chain](#3-generic-discovery-chain)
- [4. Session bootstrap (no save file required)](#4-session-bootstrap)
- [5. The test protocol](#5-test-protocol)
- [6. The autonomous loop: run → reset → modify → run → diff](#6-autonomous-loop-run-reset)
- [7. 🛑 Moving the REAL reference (the bug that hid here)](#7-moving-real-reference)
- [8. 🛑 The quest fights the clean room — re-establish AFTER the reset](#8-quest-fights-clean-room)
- [9. What to observe, and from where](#9-what-observe-from-where)
- [10. Determinism rules](#10-determinism-rules)
- [11. Bridge commands this methodology requires](#11-bridge-commands-this-methodology)
- [12. Known limits (stated, not worked around)](#12-known-limits)

How to test **any** converted quest, dialogue tree, or script in the running
game, remotely and autonomously, without the user at the keyboard.

The entry point is `tools/live/quest_labtest.py`. Nothing in it is specific to any
one quest — the quest name is an argument, and every actor, stage, variable and
topic involved is **discovered from the export** for whatever quest you name.

---

## 1. Why a clean room
<a id="1-why-clean-room"></a>

Observing a converted quest in the middle of a live playthrough is not evidence.
Measured the hard way (2026-08-15): a 95-second "reproduction" was taken at
stage 50 of a bug that only exists at stage 42, with hand-written baton
variables — and proved nothing at all.

In a live save, all of the following are moving while you measure:

| Confounder | What it does to a reading |
|---|---|
| The quest keeps advancing | the stage you think you are testing is gone by the time you read it |
| Other quests' scripts run | they write the same globals/quest variables you are watching |
| AI packages | actors walk out of the cell, out of earshot, or into combat |
| Ambient/idle dialogue | fires into the same topic-selection machinery you are testing |
| Other NPCs in the cell | contribute GREETING/HELLO candidates and steal the conversation |
| The previous run's state | run N leaves variables set, so run N+1 is not a fresh trial |

A reading taken under those conditions will support almost any theory you bring
to it. That is the failure mode this methodology exists to remove.

**The rule: change one thing, in an empty room, from a known start state.**

---

## 2. The clean room: 🛑 `AnvilMarkTest`
<a id="2-clean-room-anvilmarktest"></a>

The room is **`AnvilMarkTest`** — the established test cell for this project.
**Do not "improve" on it.**

### The trap: talking yourself out of the right cell

`AnvilMarkTest`'s child group carries 14 `REFR` records — architecture at
z≈7367. A session read that, declared it a furnished dungeon, and swapped the
default to `QASmoke`:

```
CELL 0103E2B8 AnvilMarkTest -> child GRUP 1224 bytes, 14 REFR
```

That reasoning is wrong in the way that matters. Those refs are the room's own
**geometry** — it is a test room, so it has a floor and walls, which is exactly
the requirement (§ the floorless-dummy-cell problem below). What a clean room
must not contain is **other actors and other quests' scripts**, and
AnvilMarkTest contains neither.

`QASmoke` is the *worse* choice precisely because it is Skyrim's live QA cell:
it is stuffed with vanilla test actors and containers that contribute their own
GREETING/HELLO candidates to the very topic-selection machinery a dialogue test
is trying to measure.

The deeper lesson: a note written earlier in a session is not evidence. That
swap was recorded in this doc and in a memory, and then treated as settled
authority on a later turn — which is how the wrong room survived.

### Why not just any empty Oblivion interior

86 interior cells have zero refs, but they are worldspace dummy cells with **no
floor**: the player free-falls (measured z −12691 and still dropping), which
breaks pathing and physics for every actor moved in. Empty is not the same as
usable.

### 🛑 Verify the cell with `getincell`, never with coordinates

```bash
player.getincell AnvilMarkTest  # >> 1.00  -- actually there
player.getincell WITestHold     # "Item 'WITestHold' not found"  -- no such cell
```

Two different cells here both report z≈7239, so a position comparison
"confirmed" a room the player had never left. `getincell` also separates *"the
coc failed"* from *"that cell does not exist"* — a distinction coordinates
cannot make, and one that matters because a non-existent cell name fails
silently.

`establish_room` runs all three checks — cell exists, player is in it, and the
floor holds (a settling drop of a few units after a `coc` is normal; an unbounded
fall is not) — and says which one failed instead of proceeding on a false
premise.

Override with `--cell`. The plugin's **load-order index** (`xx` in `xx03E2B8`)
belongs to the user's setup, not the conversion — measured live it was `1A`, not
`01` — so the harness asks the running game rather than assuming (see §7).

---

## 3. The generic discovery chain
<a id="3-generic-discovery-chain"></a>

This is what makes the harness quest-agnostic. Given **only** a quest EDID, the
cast is derived from the export text — no hardcoded names anywhere:

```
quest EDID  ──►  QUST.txt        EditorID → FormID, stage list
                     │
                     ▼
            INFO.txt  QSTI.Quest = <quest FormID>          all dialogue lines
                     │
                     ▼
            Condition[i].Raw   (24-byte TES4 CTDA blobs)
                     │  decode func 72 GetIsID / 71 GetInFaction / 69 GetIsRace
                     ▼
            speaker base FormIDs (NPC_ / CREA)
                     │
                     ▼
            ACHR.txt / ACRE.txt  NAME = <base FormID>       the PLACED refs
                     │                ParentCELL = where it lives
                     ▼
            the actors to bring into the clean room
```

Field names verified against the real export files:

| Question | File | Field |
|---|---|---|
| quest FormID from EDID | `QUST.txt` | `EditorID=` / `FormID=` |
| which lines belong to a quest | `INFO.txt` | `QSTI.Quest=<quest FormID>` |
| who may speak a line | `INFO.txt` | `Condition[i].Raw=` (hex blob) |
| what a line says | `INFO.txt` | `Response[0].ResponseText=` |
| what a line does | `INFO.txt` | `ResultScript=` |
| the placed ref for a base NPC | `ACHR.txt` (NPC), `ACRE.txt` (creature) | `NAME=<base FormID>` |
| where that ref lives | `ACHR.txt` | `ParentCELL=` |

### Decoding a TES4 condition

Conditions are exported as raw 24-byte hex, not decoded fields. The layout
(identical to `tes5_import/base/conditions.py`, which the tool imports rather
than re-implementing):

```
offset 0   type byte      (flags: 0x01 OR, 0x04 use-global, compare op in high bits)
offset 4   comparison     (float, or a GLOB FormID when use-global)
offset 8   function index (u16)
offset 12  param1         (u32 — for GetIsID this is the base FormID)
offset 16  param2         (u32)
```

Speaker-selecting functions that matter here:

| Func | Name | param1 is | Meaning for the harness |
|---|---|---|---|
| 72 | `GetIsID` | NPC_/CREA base FormID | **exactly this actor** — the strongest signal |
| 71 | `GetInFaction` | FACT FormID | any member of the faction |
| 69 | `GetIsRace` | RACE FormID | any actor of the race |
| 42 | `GetIsSex` | 0/1 | gender gate |

`GetIsID` is the one that yields a concrete cast. The tool reports the others
as *broad* conditions so you can see when a topic is not actor-specific (a
generic guard/bandit line), which is itself a useful answer.

---

## 4. Session bootstrap (no save file required)
<a id="4-session-bootstrap"></a>

`coc <cell>` works **from the main menu** — it boots a playable session directly
into the target cell. So an autonomous test needs no prepared save, and cannot
corrupt one.

Two engine facts the console path depends on, both verified live and both of
which fail *silently* if ignored:

1. **`ConsoleExecute` only COMPILES.** Its tail is
   `call <finalizer>; mov al,1; ret` — it returns success having produced only
   bytecode. The console performs a separate execution step. A caller that stops
   at `ConsoleExecute` gets `returned: 1`, zero output, and no effect.
2. **Printing is gated on a thread-local byte** (TLS slot + `0x600`). The real
   console sets it while dispatching a typed command; a direct call does not, so
   handlers execute and print nothing — indistinguishable from a dead hook.

**No keystroke bootstrap is needed any more.** The "execution context" a typed
command used to supply is a ScriptCompiler that nothing on the compile path
reads, so the bridge passes a stand-in; the real blockers were a null
MemoryManager and an off-thread probe
([headless_testing.md](headless_testing.md#console-without-a-keystroke)).
`tools/live/game_input.py bootstrap` still works but is no longer required.
For runs that need no player at all, use the isolated instance in
[headless_testing.md](headless_testing.md).

`quest_labtest.py doctor` checks all of this and tells you which step is
missing, rather than failing later inside a test with a confusing symptom.

---

## 5. The test protocol
<a id="5-test-protocol"></a>

```bash
python tools/live/quest_labtest.py doctor

python tools/live/quest_labtest.py cast --quest <QuestEDID>

python tools/live/quest_labtest.py setup --quest <QuestEDID>

python tools/live/quest_labtest.py reset --quest <QuestEDID> --stage <N>

python tools/live/quest_labtest.py run --quest <QuestEDID> --seconds 60 --out temp/run1.log

python tools/live/quest_labtest.py restore
```

### Why the steps are separate

`reset` writes; `run` does not. A write during the observation window is exactly
what makes a reading untrustworthy, so all setup writes happen **before** the
window opens. `run` is strictly passive.

### Real refs vs spawned copies

| Mode | What it does | Use when |
|---|---|---|
| `--use-real-refs` (default) | `moveto` the ORIGINAL placed refs into the test cell | quest scripts hold properties pointing at specific refs (`UrielSeptimRef`) — **the normal case for dialogue** |
| `--spawn` | `placeatme` fresh copies | testing behaviour that does not depend on identity (combat, animation, ragdoll) |

This distinction is not cosmetic. A converted quest script's properties are
bound to *particular* placed references. A spawned copy is a different reference
with a different FormID, so the quest's own conditions and properties will not
see it, and the test will "fail" for a reason that has nothing to do with the
bug under investigation.

**Spawned copies are tracked engine-side** (`spawn` / `cleanup` commands) so
`restore` can actually delete them. Previously the harness could only print
"remove this manually", because `placeatme` does not report the ref it created
through console output — the created pointer only exists inside the engine.

---

## 6. The autonomous loop: run → reset → modify → run → diff
<a id="6-autonomous-loop-run-reset"></a>

This is the whole point: change something and see what it changed, without a
relaunch and without the user at the keyboard.

```bash
python tools/live/quest_labtest.py ab --quest <QuestEDID> --stage <N> \
    --seconds 45 --inject temp/change.txt \
    --out-a temp/A.log --out-b temp/B.log
```

What it does, in order:

```
trial A   reset -> observe -> transcript A
modify    apply the change (the ONLY difference between the trials)
trial B   reset -> observe -> transcript B
diff      what the change actually altered
```

Both trials reset first, so trial B cannot inherit trial A's state, and both use
the *same* observation code — if the measurement differed between trials, the
diff would be an artefact of the harness rather than of the change.

### Two ways to modify, and they are NOT equivalent

| Flag | Mechanism | Relaunch? |
|---|---|---|
| `--inject <file>` | the engine's own compiler compiles and runs the script body **in-process** | **no** — this is the live loop |
| `--pex <file>` | stages a recompiled `.pex` into the load path | **yes**, next session |

`--inject` is the live loop and covers most quest/dialogue logic questions,
because it can run any sequence of actions a fragment would.

`--pex` is honest about its limit: **the VM binds script types when the session
loads, so an already-loaded script is not re-read.** The command says
`STAGED ONLY` rather than letting stale bindings masquerade as a result — a
trial that silently measured the *old* code would be worse than no trial.

### 🛑 A pre-window change moves the BASELINE, not a transition

Measured live, 2026-08-15. Injecting `setstage <quest> 12` before trial B's
window produced this:

```
A:  203 transition(s)  stage 10 -> 12
B:  214 transition(s)  stage 12 -> 12
```

The `10 -> 12` transition appears **only in trial A** — not because the change
failed, but because in B it had already happened before the window opened. Read
naively, a change that plainly worked looks like a regression.

So the harness records the stage each window **opened at** and **finished at**,
and prints an explicit note when they differ:

```
NOTE: the two windows OPENED at different stages (10 vs 12). The change took
effect before the window, so compare the end states, not just the transitions.
```

Compare end states when you see that note. Transitions alone are only the whole
story when both windows start from the same place.

---

## 7. 🛑 Moving the REAL reference (the bug that hid here)
<a id="7-moving-real-reference"></a>

Quest scripts bind their properties to **specific placed references**. A
`placeatme` copy is a different FormID, so the quest cannot see it — which makes
spawned copies useless for quest/dialogue testing. The harness must move the
real ref, and for a long time it silently could not.

### The defect

The bridge selected a reference by running `prid <id>` as a **separate**
command, then ran the real command. That does not work:

```
ExecOne("prid 1A032A17")   <- compile + execute #1  (selection made here)
ExecOne("moveto player")   <- compile + execute #2  (selection GONE)
```

Each `ExecOne` is its own compile-and-execute, so the console's selection never
reached the following command. Every ref-targeted command ran against **no
target** — and still reported success. `moveto player` on a quest actor returned
`ok` and moved nothing.

Proven with a controlled test on the player: after `prid 00000014`, a bare
`getpos x` printed **nothing**, while `player.getpos x` printed a real value.

### The fix

The console dispatcher takes the target in `r9` and threads it into both halves
(from the live disassembly):

```
dispatch(rcx=Script, rdx=ctx, r8d=compilerType, r9=target)
  mov r14, r9      ; save target
  mov r8,  r14     ; -> ConsoleExecute arg3   (compile)
  mov rdx, r14     ; -> runner arg2           (run)
```

The plugin passed `nullptr` there. It now resolves the FormID with
`TESForm::LookupByID` (**stable id 14617**) and passes the real pointer.

`LookupByID` was found without guessing an id: the Papyrus native name string
`"GetForm"` is referenced by its registration site, whose bound implementation
is a two-instruction thunk `mov ecx, r9d ; jmp <LookupByID>`. Verified live
before use — `0x14`, `0x1A032A17` and `0x7` all return real pointers and a bogus
id returns 0, which is self-validating in a way a blind signature scan is not.

Measured result: ref `1A032A17` went from `ELSEWHERE` (different cell) to
**37.75 units** from the player.

### Two things the harness now refuses to assume

**Never trust the return value — measure the distance.** A ref-targeted command
reports success even when it targets nothing. `player.getdistance <ref>` is
player-side (no selection involved), and a ref in an unloaded cell reports
`FLT_MAX`, so "did it arrive?" is decidable. The check requires the actor to be
both **close** *and* to have **actually changed distance** — proximity alone is
a false positive, because an actor already 338 units away reads as "near"
without having moved. That false positive is precisely how the broken selection
escaped notice.

**Never assume the load-order index.** It belongs to the user's setup, not to
the conversion. Measured live: the plugin sat at `1A`, not `01`, and every id
built from `01` resolved to nothing while the console still reported success.
`setup` probes the running game for the index of a form it knows exists
(`--no-auto-index` to override).

---

## 8. 🛑 The quest fights the clean room — re-establish AFTER the reset
<a id="8-quest-fights-clean-room"></a>

A quest's own stage scripts move actors **and the player**. Charactergen is the
worked example:

| Stage | What its result script does |
|---|---|
| 0 | `GlenroyRef.moveto CGMarker*`, `UrielSeptimRef.moveto …`, `BaurusRef.moveto …` |
| 5 | **`player.moveto CGPlayerStartMarker`** — teleports the PLAYER into the prison |
| 14 | sets `speaker` / `target` / `convCount` — this is what starts the conversation |

`reset` runs `startquest`, so those scripts fire and drag everyone back into the
quest's own cell. Measured: after resetting to stage 10 the player was at
x=567 (the prison) while the harness believed it was testing in `AnvilMarkTest`
at x=2093, and all four actors read `ELSEWHERE`. **The "clean room" test was
really running in the CharacterGen cell** — the exact confounded environment the
methodology exists to avoid.

So the room is (re)established **after** the reset, never before:

```
reset  (stopquest / resetquest / startquest / setstage)   <- moves everyone
coc <cell>                                                <- player back
<ref>.moveto player  for each actor                       <- cast back
observe
```

`trial` and `ab` do this automatically (`--cell`, `--actor`, `--load-wait`), and
both the player position and every actor's arrival are **verified**, not assumed.

Expect the quest to keep pulling actors out during a long window — that is the
quest working, not the harness failing. It is visible in the transcript as
actors drifting back to `ELSEWHERE`.

### 🛑 NEVER restrain the cast to hold them in the room

Measured live 2026-08-15, and it cost a whole debugging round.

The cast walking out looks like a harness defect, and `setrestrained 1` looks
like the fix. It is not — **a restrained actor cannot turn to face or approach
its conversation target**, so the `Say` / force-greet handshake never completes.
The script re-fires the same line while `convTimer` waits.

The symptoms this fabricates are indistinguishable from a conversion bug:

```
the same lines repeated over and over again before continuing
the timers seem to be getting extremely drawn out
```

Both were pure harness artefacts — produced *inside* the clean room that exists
to prevent artefacts. `quest_labtest.py` therefore has only `unpin_cast()` (an
undo); there is deliberately **no** function that applies a restraint.

If the cast walks out, let it, and re-establish the room between trials.

### 🛑 A mid-run change does not apply to the run in progress

Clearing that restraint partway through did not rescue the trial: the quest
instance still carried state from the broken condition. **You cannot change
something mid-quest and expect it to work.** Do a full
`stopquest` / `resetquest` / `startquest` and start from a known stage —
which is what `trial` does, and why every measurement belongs inside one.

### Scope the cast to the stage, not to the quest

Moving every speaker in the quest into the room is not neutral: each extra
actor brings its own GREETING/HELLO candidates into the topic selection under
test. `stage_cast()` reads the stage's own `Stage[i].Log[j].SCRO[k]` list — the
forms the CK derived from the result script text — so Charactergen stage 40
yields exactly UrielSeptim / Baurus / Glenroy rather than all 7 speakers.

Filtering the *dialogue* by stage does **not** work here and was tried first:
only **78 of 296** CharacterGen lines carry a `GetStage` gate at all. The rest
are driven by func 79 `GetQuestVariable` on `speaker`/`target`/`convCount`, so a
stage window over INFO conditions kept 246/296 lines and all 7 speakers — a
heuristic dressed up as a filter. The SCRO list is the *authored* signal.

### Pick the stage that STARTS the thing you are testing

Resetting to a stage before the one that drives the behaviour produces a quest
that runs and does nothing. Charactergen stage 10 leaves `speaker`/`target` at 0
forever; **stage 14** is the stage that assigns them, and from there the whole
conversation replays:

```
Speaker 1 -> 2      Target 2 -> 0     convCount 12 -> 13 -> 14
Speaker 0 -> 2      Target 0 -> 3     convCount 14 -> 8
Speaker 2 -> 3      Target 3 -> 2     convCount  8 -> 9
Speaker 3 -> 2      Target 2 -> 5     convCount  9 -> 11 -> 12
```

Find it by grepping the quest's `Stage[N].Log[M].ResultScript` in `QUST.txt` for
the variables the behaviour depends on — the harness prints the stage list, and
`cast` gives you the speakers to look for.

---

## 9. What to observe, and from where
<a id="9-what-observe-from-where"></a>

No single channel sees everything. The harness merges four, on one clock:

| Channel | Sees | Blind to |
|---|---|---|
| `sqv <quest>` (console) | stage, aliases, script variables, objectives | anything between polls |
| `vmlog` (Papyrus sink) | `Debug.Trace`, VM errors, aborted fragments | lines that emit nothing |
| `console_log` ring | console output whoever caused it | dialogue events — `tdt` paints the screen without going through `Console::Print` |
| `MenuTopicManager` (memory) | **which topics are offered / which line is playing** | anything not currently open |

**The dialogue channel is memory-only.** Measured 2026-08-15: the console print
hook fires only for commands the bridge issues (delta 0 across 12s of the game
running on its own), and converted INFO fragments contain no trace statements,
so a fired line logs nothing. "Which INFO fired" exists *only* in
`MenuTopicManager`, which `tools/live/dialog_live.py` reads and `quest_labtest.py`
merges into the transcript when `--dialogue` is passed.

### Reading the transcript

`run` emits a transition log, not a state dump — a line appears only when
something actually changes:

```
[14:22:31] RUN     CharacterGen stage=42 (18 fields)
[14:22:33] TOPIC   offered: GREETING_0102466E / CharGenMain
[14:22:34] VAR     Speaker None -> [Actor < (0100E2B8)>]
[14:22:36] STAGE   stage 42 -> 45
[14:22:36] VM      [CharacterGen] fragment 45 entered
```

The **order** of those transitions is the evidence. A snapshot taken after the
fact cannot show it, which is why the window is a recording and not a poll.

---

## 10. Determinism rules
<a id="10-determinism-rules"></a>

1. **Always `reset` between runs.** Run N leaves variables set; without a reset
   run N+1 is measuring the residue of run N.
2. **Never write during the observation window.** Seed in `reset`, observe in
   `run`.
3. **One variable per run.** If two things changed between runs, the run pair
   proves nothing.
4. **Record the whole window to a file** (`--out`). An agent-driven session has
   a hard command timeout; a run that outlives it still leaves usable evidence
   because every line is flushed individually.
5. **Keep the game focused.** Windows throttles a background window, so the main
   thread stops draining SKSE's task queue and marshalled commands time out with
   `E_LOADING`. The client retries, but a long alt-tab still creates blind gaps
   (they are marked `GAP` in the transcript rather than silently dropped).

---

## 11. Bridge commands this methodology requires
<a id="11-bridge-commands-this-methodology"></a>

Implemented for this workflow (`game_bridge/plugin/commands.cpp`):

| cmd | Why the harness needs it |
|---|---|
| `spawn` | `placeatme` cannot report the ref it created through console output; the engine-side handler captures the created reference so it can be tracked and later deleted |
| `cleanup` | deletes every tracked spawn (disable + markfordelete), so a test leaves no residue in the save |
| `moveref` | move a ref to the test cell **and back**, capturing its original cell+position first, so `restore` is a recorded undo rather than a guess |
| `wait_ready` | `coc` triggers a load screen; without this every following command races it and fails with `E_LOADING` |

Pre-existing and reused: `console`, `batch`, `inject`, `vmlog`, `console_log`,
`status`, `capabilities`.

---

## 12. Known limits (stated, not worked around)
<a id="12-known-limits"></a>

- **A structural plugin change needs a relaunch.** The load order and record
  list are read once at startup. Asset files (`.nif`, `.hkx`, textures) can be
  re-read live; new/changed/removed *records* cannot. So an `--import-only`
  rebuild requires restarting the game; a `--meshes-only` rebuild does not.
- **`coc` from the main menu is not a full new-game start.** Quests that depend
  on new-game initialisation may not have run their startup fragments. For those,
  `reset --stage` is the substitute, and it is not identical to reaching the
  stage by play.
- **Moving a real ref out of its home cell can disturb its AI package.** The ref
  is returned by `restore`, but a package that was mid-evaluation may need the
  cell reloaded to resume normally. This is why the clean room is preferred over
  editing the actor in place.
- **One client at a time.** The pipe refuses a second connection rather than
  queueing it, so two agents cannot interleave mutations.
