# Game Bridge

A live control channel into a **running** Skyrim SE, so conversion output can be
verified in-engine without a relaunch cycle.

    python (tools/live/game_bridge.py)  <--named pipe-->  TESGameBridge.dll (SKSE plugin)

## Why

Most creature/ragdoll defects in this project are **silent binding failures**:
the `.hkx` is grammatically valid, the offline validator passes it, the actor
spawns and looks fine — and the engine bound nothing. Offline tools cannot see
this, because the verdict only exists inside the engine.

So the bridge is a **readback** tool first, a command injector second. Anything
it can make the game do, it should also be able to report what the game decided.

## Build

Needs only MSVC + the Windows SDK. No SKSE source tree, no CMake, no vcpkg.

```bat
game_bridge\build.bat            :: build to game_bridge\TESGameBridge.dll
game_bridge\build.bat deploy     :: build, then copy into Data\SKSE\Plugins
```

Then verify the DLL before launching, and launch through `skse64_loader.exe`:

```bash
python tools/misc/skse_version_data.py game_bridge/TESGameBridge.dll
```

### 🛑 `SKSEPlugin_Version` must be STATICALLY initialized

SKSE reads the version struct with `LOAD_LIBRARY_AS_IMAGE_RESOURCE`, which maps
the DLL as **raw data and runs no CRT initializers**. Only the bytes already in
`.data` on disk exist as far as SKSE is concerned.

The struct was originally filled by an immediately-invoked lambda. That gave the
linker no static data to place, so the exported `SKSEPlugin_Version` symbol
resolved into **`.pdata`** — and SKSE read exception-unwind entries as version
fields, reporting `disabled, bad version data`. It compiled clean; the only
evidence was one line in `skse64.log`.

So: plain aggregate initializer, no function calls, no `strncpy`. A
`static_assert` in `plugin.cpp` guards it, and `tools/misc/skse_version_data.py`
confirms the shipped bytes (it prints which section the export landed in, which
is what separates this failure from a merely-wrong value).

## Use

```bash
python tools/live/game_bridge.py ping           # is the bridge alive?
python tools/live/game_bridge.py capabilities   # what resolved on this runtime?
python tools/live/game_bridge.py status         # game / session state
python tools/live/game_bridge.py console "coc WhiterunDragonsreach"
python tools/live/game_bridge.py console "getpos z" --ref 0x0001A2B3
python tools/live/game_bridge.py --json status  # machine-readable
```

As a library:

```python
from tools.live.game_bridge import Bridge

with Bridge() as b:
    b.console("coc BridgeTestCell")
    print(b.status())
```

## How it stays correct across game updates

**No RVA is hardcoded.** Every entry point resolves at runtime, in this order:

1. **Address Library stable ID** (`versionlib-<ver>.bin`, already deployed in
   this install).
2. **Signature scan** of `.text`, so a game update that lands before a new
   versionlib does not take the bridge offline.
3. Neither → the capability reports `E_UNSUPPORTED` and is **never called
   through**.

The IDs were derived by locating each target in the GOG/AE 1.6.659 build (the
only non-DRM-packed copy, so the only one that disassembles statically) and
inverting through `versionlib-1-6-659-0.bin`. The same ID then resolves on the
DRM-packed Steam 1.6.1170 the user actually plays. See `plugin/ids.h`.

DRM only blocks *static disassembly of the file on disk*. An SKSE plugin loads
after the exe has unpacked itself in memory, so Steam is fully supported — and
is the right target, since it is the build being played.

## Design decisions worth keeping

**Console commands go through the engine's own executor** (`ids::kConsoleExecute`),
which builds the compiler, script buffer and script internally. The alternative —
constructing a `ScriptBuffer` and calling `CompileAndRun` — means matching a
struct layout exactly, and a mismatch does not fail loudly: it corrupts memory or
reads garbage, producing symptoms indistinguishable from a conversion bug. The
only struct offset assumed anywhere is the script text pointer at `+0x38`, read
straight out of `Script::SetText`'s own store instruction.

**Reference selection uses `prid`**, not a resolved `TESObjectREFR*`. Same end
state, zero layout assumptions.

**Every handler that touches game state is marshalled to the main thread** via
`SKSETaskInterface`, and the pipe thread blocks on completion. Off-thread access
to an `Actor*` races the game loop and yields intermittent crashes that look
exactly like data bugs — the most expensive possible failure for this tool,
because it sends both sides hunting ghosts in the data. A marshalling timeout is
reported as `E_LOADING` rather than hanging the pipe.

**A named pipe, not TCP** — local-only by construction, no listening port.
One client at a time; a second connection is refused rather than queued, so two
clients cannot interleave mutations.

## Testing

```bash
python game_bridge/test_protocol.py    # framing, pairing, error codes; no game needed
```

The versionlib parser is verified against `tools/disasm/address_lib.py` (the Python
reference): 428,461 entries and all 7 probe addresses match exactly on
1.6.1170. That check matters because the parser's failure mode is silent — a
desynced stream still yields plausible numbers, which would then be called as
function pointers.

## Status

Working now:

| | |
|---|---|
| `ping` / `capabilities` / `status` | session + capability introspection |
| `console` | any console command, optional selected reference — **returns its output** |
| `console_log` | always-on ring of console output, whoever caused it |
| `batch` | several commands in ONE main-thread trip (the game does not advance between them) |
| `inject` | compile + run a MULTI-STATEMENT script; returns the Papyrus output it produced |
| `vmlog` | Papyrus VM output captured from the logger sink |
| `resolve` / `readmem` / `call` | raw probes: stable id → address, read live memory, call a function |
| `hookstats` | how many times each hook has fired |
| `recorder` | the [flight recorder](#flight-recorder): which event sinks are hooked, how many events were written |

### 🛑 `ConsoleExecute` COMPILES — it does not run

Verified 2026-08-14 by disassembling the **live Steam process** (not the GOG
copy) with `tools/live/live_disasm.py`. `ConsoleExecute`'s tail is
`call <compile finalizer>; mov al,1; ret`: it returns success having only
produced bytecode. Stopping there gives `returned: 1`, no output, and no effect
— a false success that cost two debugging sessions.

The console's own dispatcher does the whole job:

    dispatch(rcx = Script, rdx = execContext, r8d = compilerType, r9 = target)
      save the TLS script-source marker
      call ConsoleExecute                 -- compiles
      if ok && bytecodeLen: TLS[0x600] = 1 ; call runner ; TLS[0x600] = 0

The plugin locates it structurally (a `call ConsoleExecute` whose caller also
loads the `0x600` console-mode immediate, then walk back to the prologue), so no
RVA or stable ID is baked in. **Anchor that walk-back on the REX prefix** — the
entry is `40 57`, and matching from the `57` lands one byte late, skipping
`push rdi` so the epilogue returns to garbage.

### 🛑 Printing is gated on a thread-local byte

Every printing handler checks `TLS[slot] + 0x600` and silently skips its print
when it is zero. The real console sets it while dispatching; a direct
`ConsoleExecute` call does not. That is why output was empty even though the
print hook was correct all along — forcing the byte on took the hook from 10
hits to 15,293. Setting it on **all** threads FROZE the game; it must be set
only on the thread running the command, which is what the dispatcher does for us.

### 🛑 Command success is not in the return value

A good `getgs` returns 0; a misspelled command returns 1. The only signal is the
printed `Script command "x" not found.`, so both the plugin and the client check
for it. Without that, every typo reads as a success and no result is
trustworthy.

### 🛑 Never memset a block from the engine's allocator

`MemAlloc` returns memory that still carries the allocator's own bookkeeping —
verified live: the first qwords are heap pointers, not zeros. The plugin used
to `memset(mem, 0, 0x80)` before `Script::ctor`, which destroyed it. The
console executor then **compiled the script but the command did nothing**:
returned success, printed nothing, acted on nothing.

That symptom survived four other fixes because every individual piece —
`Script::ctor`, `SetText`, compiler index 1 (`SysWindowCompileAndRun`), and the
argument order — verified correct in isolation. The bug was one line that
looked like defensive hygiene. The constructor initialises every field it owns,
so the pre-zeroing was never buying anything.

### Probe from Python, don't rebuild

`resolve`/`readmem`/`call`/`hookstats` exist so a theory about engine internals
can be tested **against the live process, with no rebuild and no restart**.
That round trip is the entire cost this bridge exists to remove, so any
question that would otherwise be answered by "add a log line, rebuild,
relaunch" should be answered with these instead.

`hookstats` in particular makes "capture is empty" decidable: **0 hits** means
the detour is on the wrong function, **non-zero** means the plumbing after it
is at fault. Previously that distinction cost a full rebuild cycle.

`call` can corrupt the game if given a wrong signature. It is SEH-guarded so a
mistake kills the command rather than the session, but the risk is real and
deliberate — the alternative has proven worse in practice.

Both capture channels are hooked in-process and were located via **RTTI**, not
by counting callers (which fails here — the real functions are reached
indirectly). `Console::Print` is identified by the `"> %s"` echo-prefix literal
inside its thunk; `SkyrimScript::Logger::Log` by
`.?AVLogger@SkyrimScript@@` → vtable[1]. Details in `docs/protocol.md`.

Higher-level tools built on this:

```bash
python tools/live/quest_debug.py state charactergen      # real quest state
python tools/live/quest_debug.py setstage charactergen 27  # drive it + VM output
python tools/live/quest_debug.py watch charactergen        # every stage change
python tools/script/papyrus_tail.py since --cursor N         # log history
python tools/live/game_bridge_verify.py                    # READY / NOT READY
```

**Alt-tabbing pauses the bridge.** Windows throttles a background window, so
the game's main thread stops draining SKSE's task queue and marshalled commands
time out with `E_LOADING`. The client retries automatically; if you are driving
it by hand, leave the game focused.

<a id="flight-recorder"></a>
## Flight recorder

An always-on log of what the engine decided, written while the game is played
normally and read afterwards with the game closed. No client needs to be
connected, and nobody has to reproduce anything for it.

```bash
python tools/live/flight_log.py show --plugin <path>/FalloutNV.esm
python tools/live/flight_log.py show --kinds quest_stage,topic_info,package --grep VCG01
python tools/live/flight_log.py show --run 1        # the run before
python tools/live/flight_log.py status              # live: hooks and counts
```

The file is `Documents\My Games\Skyrim Special Edition\SKSE\TESGameBridge_events.jsonl`.
Older runs rotate to `.1` and `.2`. Each line is one event:

| `ev` | Fields |
|---|---|
| `quest_stage` | `quest`, `stage` |
| `quest_init` | `quest` (a quest starting) |
| `topic_info` | `speaker`, `info`, `phase` begin/end: every dialogue line spoken, follow-ups included |
| `package` | `actor`, `package`, `phase` start/change/end (force greets show here) |
| `trigger_enter` / `trigger_leave` | `trigger`, `actor` |
| `activate` | `target`, `by` |
| `open_close` | `target`, `by`, `opened` |
| `furniture` | `actor`, `furniture`, `phase` enter/exit (beds, chairs) |
| `cell_loaded` | `cell` |
| `death`, `combat`, `equip`, `lock_changed` | the actors, items and refs involved |
| `container`, `location` | the player's only |
| `load_game`, `loading`, `loaded`, `saved`, `new_game` | session boundaries, with the save name |
| `papyrus` | Papyrus errors and warnings with their stack frames (at most 100 lines a second) |

**How it hooks.** SkyrimVM is registered as a sink for every script event,
since that is how OnActivate and OnStageSet reach scripts. Each
`BSTEventSink<T>` base has its own vtable, whose slot 1 is `ProcessEvent`. The
recorder finds those vtables at startup by walking MSVC RTTI from the name
`.?AVSkyrimVM@@` (`plugin/rtti.cpp`), then swaps slot 1 for a thunk that writes
one line and calls through. It uses no stable ID, signature or inline detour.
A missing sink goes in the log's `missing` list instead of failing the plugin.

**Layouts.** Each event struct's field offsets were checked against the reads
SkyrimVM's own `ProcessEvent` makes in the unpacked 1.6.1170 exe
(`C:\Coding\tes4skyrim\SkyrimSE.exe.unpacked.exe`). The check caught one
mistake: `TESTopicInfoEvent`'s speaker is at `+08`. Its `+00` is a ref-counted
callback; the handler bumps a count at `[p+8]`, where a reference keeps its
count at `+0x28`.

**Self-checks.** Each decoded form is still verified at runtime:

- a quest id must look up to a QUST;
- a speaker must be a reference.

When a check fails, the line gets `<field>_check`, the raw event bytes, and
every offset holding something that resolves to a form. So a wrong layout
shows up on the first run with the correction in hand, instead of quietly
logging garbage.

**Tests.** `plugin/test_recorder.cpp` runs the RTTI walk against the real exe,
mapped the way SKSE maps plugins. It must reproduce the SkyrimVM vtables
`tools/disasm/skyrim_disasm.py` lists; both the packed and unpacked 1.6.1170
exes pass. The same file also runs the decoders over fake forms. The build
line is in the file's header.

The engine-side handlers below are specified in `docs/protocol.md` and not yet
implemented — they need struct work that should be validated against a live game
first, one at a time:

| | |
|---|---|
| `ragdoll_probe` | rigid bodies, constraints, `AddRagdollToWorld` state, bone transforms |
| `anim_probe` | behavior graph bind status, resolved clip index |
| `spawn` / `cleanup` | tracked test actors |
| `reload_nif` / `reload_hkx_anim` | hot reload without relaunch |

Note that `coc` already works today through `console`, including from the main
menu — which covers autonomous navigation to a test cell.

### 🛑 One bootstrap step per session

`ConsoleExecute`'s first argument is an execution context the console builds
**only when the game itself dispatches a typed command**. It cannot be
synthesized, so until one such command runs, injected commands compile and
silently do nothing.

`tools/live/game_input.py bootstrap` removes that last manual step: it sends real
keystrokes to the game window, runs one read-only command, and closes the
console. Two traps, both of which fail *silently*:

- **`PostMessage` and `KEYEVENTF_UNICODE` do not work.** Skyrim reads the
  keyboard through DirectInput, which never looks at the window message queue
  and only sees **scan codes**.
- **`INPUT` must be 40 bytes on x64** (size the union to `MOUSEINPUT`), or
  every `SendInput` returns 0 with error 87 having sent nothing.

`status` no longer trusts SKSE's PostLoadGame message alone either: `coc` from
the main menu boots a playable session without that message ever arriving, so a
successful `player.getav health` probe is the fallback.

## Safety

A corrupted live session produces symptoms indistinguishable from conversion
bugs, so the guards are part of the design, not decoration:

- **Spawn tracking** — every spawn recorded, removable via `cleanup`, and
  released on client disconnect.
- **Save guard** — mutating commands refuse to run unless the session is a
  designated test save (`BRIDGE_` prefix) or was started by the bridge itself.
  `"force": true` overrides, and is logged.
- **Spawn cap** — default 256, so a runaway loop cannot fill the cell.

## Files

| | |
|---|---|
| `plugin/plugin.cpp` | SKSE entry point, message handling, startup |
| `plugin/commands.cpp` | JSON command dispatch |
| `plugin/console_exec.cpp` | console execution (the riskiest file, isolated) |
| `plugin/script_object.cpp` | Script allocation via the engine's own heap |
| `plugin/addresses.cpp` | versionlib parser + signature scanner |
| `plugin/ids.h` | **verified stable IDs — read the warnings before adding one** |
| `plugin/main_thread.cpp` | main-thread marshalling |
| `plugin/pipe_server.cpp` | named-pipe server |
| `plugin/json.cpp` | dependency-free JSON |
| `plugin/recorder.cpp` | flight recorder: script event sinks to a JSONL file |
| `plugin/rtti.cpp` | runtime RTTI walk (a base class to its vtable) |
| `plugin/test_recorder.cpp` | offline test: the RTTI walk on the real exe, the event decoders |
| `docs/protocol.md` | full command surface and error codes |
| `tools/live/game_bridge.py` | Python client + CLI |
