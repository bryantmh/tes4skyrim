# Game Bridge Protocol

A live, bidirectional control channel into a **running** SkyrimSE.exe, so the
conversion pipeline can be verified in-engine without a relaunch cycle.

    Python (tools/live/game_bridge.py)  <--named pipe-->  TESGameBridge.dll (SKSE plugin)

## Why this exists

Most creature/ragdoll defects in this project are **silent binding failures**:
the file is grammatically valid, the offline validator passes it, the actor
spawns, and the engine binds *nothing*. Offline tools cannot see this because
the verdict only exists inside the engine. The bridge asks the engine directly.

Design consequence: the bridge is a **readback** tool first and a command
injector second. Anything it can make the game *do*, it must also be able to
report what the game *decided*.

## Transport

- **Named pipe**: `\\.\pipe\tes_game_bridge` (local-only by construction; no
  listening TCP port is ever opened).
- **Framing**: one JSON object per line, UTF-8, `\n` terminated. Request and
  response are 1:1 and strictly ordered on a single connection.
- **Concurrency**: the pipe accepts one client at a time. A second connect
  attempt is rejected rather than queued, so two agents cannot interleave state
  mutations.

### Request

```json
{"id": 17, "cmd": "console", "args": {"command": "player.placeatme 0x00023ABC 1"}}
```

### Response

```json
{"id": 17, "ok": true, "result": {...}, "log": ["..."], "elapsed_ms": 3}
```

On failure:

```json
{"id": 17, "ok": false, "error": "no game loaded", "code": "E_NO_GAME"}
```

`id` is echoed verbatim. Every response carries `ok`. Errors are **always**
structured — the client never has to parse prose.

## Threading contract (non-negotiable)

The pipe server runs on its own thread. **No command handler touches game state
on that thread.** Every handler that reads or mutates a form, reference, cell,
or Havok object is marshalled onto the game's main thread via
`SKSETaskInterface::AddTask`, and the pipe thread blocks on a completion event
until the task has run.

Violating this produces intermittent crashes and torn reads that look exactly
like conversion bugs — the single most expensive possible failure mode for this
project, because it sends both sides chasing ghosts in the data.

Commands that touch **only** the filesystem or plugin-local state (`ping`,
`status`) may answer on the pipe thread and are marked as such below.

## Command surface

### Session / introspection

| cmd | args | returns | main thread |
|---|---|---|---|
| `ping` | – | `{pong, plugin_version, runtime_version}` | no |
| `status` | – | `{in_main_menu, game_loaded, cell, player_pos, save_name}` | yes |
| `capabilities` | – | list of supported cmds + whether each is available in the current game state | no |

### Navigation / lifecycle

| cmd | args | returns |
|---|---|---|
| `coc` | `{cell}` | `{cell, player_pos}` — works **from the main menu**; boots a new game into the target cell |
| `load_save` | `{name}` | `{cell}` |
| `wait_ready` | `{timeout_ms}` | `{ready}` — blocks until the game is out of a load screen |

### Console

| cmd | args | returns |
|---|---|---|
| `console` | `{command, ref?}` | `{output}` — runs a console command, optionally with a selected reference (the `ref` is the console's implicit `this`) |

**Console output capture** is a detour on `Console::Print`. The hook is
verified live: a command typed into the in-game console reliably increments
`console_print_hits` and its text lands in the ring (`console_log`).

### 🛑 Printing is gated on a THREAD-LOCAL byte

Every printing ObScript handler compiles to:

```
mov ecx,[g_tlsIndex] ; mov rax,gs:[0x58] ; mov edx,0x600
mov rax,[rax+rcx*8]  ; cmp byte [rdx+rax],0 ; je skip-the-print
```

The real console sets that byte while dispatching a typed command. A command
run straight through `ConsoleExecute` never does, so it executes and prints
**nothing** — which looks exactly like a dead hook. Forcing the byte on took the
print hook from 10 hits to 15,293 in one session.

The plugin discovers the flag by scanning for that idiom and taking the
**operand consensus** over every inlined copy (253 matches; 248 agree on offset
`0x600`, 5 use `0x5D8`), then sets it on the executing thread only, around each
command. Setting it on every thread FROZE the game — background threads are not
supposed to reach that path.

### 🛑 `ConsoleExecute` COMPILES; it does not run

Verified by disassembling the **live Steam process** (`tools/live/live_disasm.py`),
not the GOG build: `ConsoleExecute`'s tail is `call <compile finalizer>;
mov al,1; ret`. It returns success having only produced bytecode. The console
then performs a separate execution step. A caller that stops at `ConsoleExecute`
gets `returned: 1`, zero output, and no effect — the false-success this
subsystem has been bitten by twice.

`console_log` exists to make these failures decidable: it is an always-on ring
that records console output regardless of who caused it, so "the hook is dead"
and "the command printed nothing" stop being the same observation.

Finding the target needed RTTI, not call-counting — an earlier pass wrongly
concluded it could not be identified safely, because the plausible candidates
have 0–1 *direct* callers (the real one is reached indirectly):

    TypeDescriptor ".?AVConsole@@"  ->  CompleteObjectLocator
      ->  Console vtable (rva 0x179c060 on 1.6.659)
      ->  vtable[11]: a 5-instruction thunk that loads the Console singleton
          and tail-jumps to the printer, carrying a `lea rdx, ["> %s"]`

That `"> %s"` literal is the console's echo prefix and is the proof of
identity. Stable id **51105**, with a signature fallback verified unique.

### Papyrus VM capture

A second, independent hook on `SkyrimScript::Logger::Log` (stable id **53551**,
found by the same RTTI route via `.?AVLogger@SkyrimScript@@`) captures every
message the VM emits — `Debug.Trace`, notifications, and VM errors.

| cmd | args | returns |
|---|---|---|
| `vmlog` | `{arm?, limit?}` | `{lines, count, source}` — `arm` starts a slice; a later call returns everything emitted in between |

`inject` arms this automatically and returns a `papyrus` array with exactly the
lines its script produced. That is the whole autonomous loop in one call, and
it is strictly better than tailing the log file for attribution: no cursor
arithmetic, no racing the game's buffered writes, no guessing which lines are
yours.

Tailing the file (`tools/script/papyrus_tail.py`) is still supported and complementary
— it sees history and anything emitted while nothing was armed.

### Papyrus

| cmd | args | returns |
|---|---|---|
| `papyrus` | `{script, function, args, self?}` | `{value}` — invokes a Papyrus function and returns its typed result |

### Forms / records

| cmd | args | returns |
|---|---|---|
| `lookup` | `{editor_id?, form_id?}` | `{form_id, editor_id, type, ...}` |
| `get_record` | `{form_id, fields?}` | decoded field values as the **engine** currently holds them (post-load-order, not what the ESM on disk says) |
| `set_record` | `{form_id, field, value}` | `{old, new}` — live edit of a loaded form (see *Hot reload* for what sticks) |

### Actors / spawning / clean-room testing

Implemented. These back `tools/live/quest_labtest.py` — see
`docs/notes/ingame_test_methodology.md`.

| cmd | args | returns |
|---|---|---|
| `spawn` | `{form_id, count?}` | `{base, count, output, refs, tracked, note}` |
| `cleanup` | `{refs?}` | `{removed, failed}` — disable + markfordelete |
| `moveref` | `{ref, x?, y?, z?}` | `{before, moved_to_player, ok}` |
| `wait_ready` | `{timeout_ms?}` | `{ready, waited_ms}` |
| `actor_state` | `{ref}` | *(not implemented)* position, 3D-loaded flag, graph state |

These are composed from the engine's **own console commands** rather than from a
reconstructed `TESObjectREFR` layout — the same reasoning as `prid`-based
selection. A wrong struct offset does not fail loudly; it corrupts memory or
reads garbage, and the symptoms are indistinguishable from a conversion bug.

🛑 **`spawn` cannot report the reference it created.** `placeatme` returns it
neither through its return value nor through console output, and there is no
verified reference enumerator here. So `refs` comes back **empty** with
`tracked: false` and a `note` saying so, rather than implying the spawn is
recoverable — `cleanup` takes an explicit `refs` list from the client instead.
A spawn you cannot delete silently bloats the save, so this is stated rather
than papered over.

`moveref` reads the current position and moves in **one main-thread trip**, so
the recorded `before` is the position the ref actually had at the moment it
moved. A position captured a frame earlier is not that, and that gap is what
makes a later restore a guess rather than an undo.

`wait_ready` polls a cheap read-only command rather than reading a loading flag:
there is no verified UI singleton here, and a guessed pointer reports plausible
garbage.

### The readback layer (the reason this exists)

| cmd | args | returns |
|---|---|---|
| `ragdoll_probe` | `{ref}` | see below |
| `anim_probe` | `{ref}` | behavior graph: project name, bound clips, active state machine node, last event, whether the graph bound at all |
| `nif_probe` | `{ref}` | loaded 3D node tree, skin bone count/names, per-bone world transforms |

`ragdoll_probe` returns, for a live reference:

```json
{
  "has_3d": true,
  "graph_bound": true,
  "ragdoll_instance": true,
  "rigid_bodies": 21,
  "constraints": 20,
  "in_world": false,
  "bones": [{"name": "NPC Spine", "world": [x,y,z], "rot": [...]}, ...]
}
```

### `trace` — frame-rate sampler + timestamped hooks (`trace.cpp`)

| action | args | effect |
|---|---|---|
| `hook` | `{rva\|address, cap_arg, cap_len, label}` | detour (≤6) that records entry/exit µs, thread, 4 args, return, `cap_len` bytes at arg `cap_arg` — only while a trace runs |
| `start` | `{path, interval_ms, meta, regions:[{kind 0 direct\|1 deref\|2 ptr-array, addr, off, len, stride, elem_ptr_off, max}]}` | background thread copies every region each interval (SEH-guarded) into `path` |
| `stop` / `status` | – | `{running, samples, hook_records, bytes, path}` |

File: framed records `u8 type, u32 len, payload` — 0 meta JSON, 1 sample, 2 hook
(layout in `trace.cpp`). Client and decoder: `tools/live/graph_trace.py`.

`ragdoll_instance:false` or `rigid_bodies:0` on a creature that looks fine on
screen is the exact silent-binding signature that offline validation misses.
`in_world` distinguishes "ragdoll constructed" from "ragdoll actually raised
into the physics world" — per project notes only `AnimateToRagdoll`'s
enterNotify raises it, so a constructed-but-not-raised ragdoll is a real and
distinct failure mode.

## Hot reload

The goal is to avoid relaunching. What the engine actually permits differs by
asset class, and the bridge is honest about which is which:

| cmd | what it does | relaunch still needed? |
|---|---|---|
| `reload_nif` | drops the cached model and re-reads the `.nif` from disk, then refreshes 3D on matching refs | **no** |
| `reload_hkx_anim` | reloads animation binaries for a given project | **no** for clip data |
| `reload_behavior` | rebuilds the behavior graph for a project | **partial** — new graph binds to newly spawned actors; existing actors must be respawned |
| `reload_texture` | re-reads a texture | **no** |
| `set_record` | live-edits a loaded form in memory | **no**, but not persisted to the ESM |
| plugin/ESM structural change (new records, changed masters) | – | **yes** — the load order is read once at startup |

The honest boundary: **asset files can be re-read live; the plugin's record
list cannot.** A `--import-only` rebuild that adds or removes records requires a
relaunch. A mesh/hkx rebuild does not — which covers the large majority of the
creature iteration loop, and is where the round-trip savings come from.

`reload_*` commands report `{reloaded: n, refreshed_refs: n}` so a no-op reload
is visibly a no-op rather than a false success.

## Safety

The bridge can silently corrupt a live session, and a corrupted session
produces symptoms indistinguishable from conversion bugs. Guards:

- **Spawn tracking** — every `spawn` is recorded; `cleanup` removes them. The
  plugin also cleans up on client disconnect.
- **Save guard** — `spawn`, `set_record`, and `reload_*` refuse to run unless
  the loaded save is a designated test save (name prefix `BRIDGE_`) or the
  session was started by the bridge itself via `coc`. Override per-command with
  `"force": true`, which is logged.
- **No autosave** — the bridge disables autosave on connect so a test session
  cannot overwrite a real save.
- **Spawn cap** — a per-session ceiling (default 256) prevents a runaway loop
  from filling the cell and hanging the game.

## Error codes

| code | meaning |
|---|---|
| `E_NO_GAME` | command needs a loaded game; currently at the main menu |
| `E_LOADING` | a load screen is in progress; retry after `wait_ready` |
| `E_NOT_FOUND` | form / editor ID / reference does not resolve |
| `E_NO_3D` | reference has no loaded 3D (not in an attached cell) |
| `E_GUARDED` | blocked by the save guard; pass `force` to override |
| `E_UNSUPPORTED` | command not available on this runtime version |
| `E_INTERNAL` | handler threw; details in `error` |
