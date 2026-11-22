# Headless in-game testing

**Code:** `tools/live/test_instance.py`, `tools/live/hidden_launch.py`,
`game_bridge/sandbox/sandbox.cpp`, `game_bridge/plugin/` (console path)

An isolated Skyrim that an agent launches, drives and stops on its own: no
window on the player's screen, no sound, no input, no keystroke bootstrap, and
nothing written to the player's install, INIs, saves or logs.

```bash
python -m tools.live.test_instance build      # once, and after any rebuild (see below)
python -m tools.live.test_instance launch
python -m tools.live.test_instance smoke      # coc, no pausing menu, time runs, screenshot
python -m tools.live.test_instance launch --hd  # High preset at 2560x1440 for proof shots; `stop` restores Low
python -m tools.live.test_instance menus | shot | status | close <Menu> | stop
```

Measured on 1.6.1170 (unpacked exe), 2026-10-24: launch to bridge answering
**4-5 s**; `coc QASmoke` from the main menu **1.1 s**; game time at real speed
(0.571 h of `GameHour` per 2 s at timescale 1000); six consecutive `smoke`
passes with 0, 10 and 20 s spent at the main menu first.

## <a id="instance"></a>The instance

`C:\Other Games\Skyrim Test`, built from **hard links** to only the vanilla
files of the Steam install: the exe, the game-root DLLs, the five masters,
`_ResourcePack`, `MarketplaceTextures.bsa` and the `Skyrim - *.bsa` archives,
plus SKSE, the Address Library bin, the game bridge and any `--plugin`.
SKSE's Papyrus scripts come from its release archive, extracted once into
`C:\Other Games\Skyrim Test\_skse` (`SKSE_RELEASE`): without them every
converted script's SKSE call (`RegisterForModEvent`, `Math.LeftShift`) fails
in the instance, as it never does in a player's game.
Creation Club content, ENB, `winhttp.dll` loaders, the CK and every other mod
stay out. Hard links cost no disk and leave the install untouched.

**Re-run `build` with the same `--mod`s after every import or new sidecar
table.** The importer writes a new file rather than rewriting the old one, so
the instance's link keeps pointing at the previous build, and a file new to
the output is never linked at all. Show of Power ran on the old masters this
way, with no `cell_regions.txt`, and its weather never changed.

The exe is the unpacked `SkyrimSE.1.6.1170.unpacked.exe` from the depot
folder; `build --steam-exe` switches to Steam's. Both reach the main menu
identically. The instance writes no `steam_appid.txt` and sets no
`SteamAppId`/`SteamGameId`, so Steam does not see Skyrim running or count
playtime. The unpacked exe imports no `SteamAPI_RestartAppIfNecessary` (the
stripped DRM stub did the relaunch), and its `SteamAPI_Init` caller (1.6.1170
RVA 0x1532020) only stores the failed result at `+0x1e8` and returns.

### <a id="checking-out-an-instance"></a>Checking out an instance

**Code:** `tools/live/instance_lock.py`

Several sessions share instances 1-3, so each one must be checked out before
use. `checkout --who "<task>"` leases the first free instance and prints its
number and a token. Every other subcommand except `checkouts` and `status`
refuses to run unless it gets both `--instance N` and the matching `--token T`.
`checkin` stops that game and releases the lease. Leases are files in
`C:\Other Games\Skyrim Test checkouts\<N>.json`. Each use of the lease refreshes
it. A lease that has gone unused for 2 hours, on an instance whose game is not
running, is stale, and the next `checkout` takes it over and prints that it did.
A lease taken with `while_running` (OBRConversion's `play`, which the user starts
by hand) is stale as soon as its game is not running. The module is game-neutral:
OBRConversion's `obr_convert.instance` leases its own instances
(`C:\Other Games\OBR Test checkouts`) with its own slots and command line.

```bash
python -m tools.live.test_instance checkout --who "Hlaalu quest run"   # -> instance 2  token 1a2b3c4d
python -m tools.live.test_instance --instance 2 --token 1a2b3c4d build --mod Morrowind.esm
python -m tools.live.test_instance --instance 2 --token 1a2b3c4d checkin
python -m tools.live.test_instance checkouts
```

### <a id="parallel-instances"></a>Parallel instances

`--instance N` (before the subcommand) selects another instance:
`C:\Other Games\Skyrim Test N`, with its own profile, bridge pipe
(`tes_test_bridgeN`) and hidden desktop (`TESConversionTestN`), so two runs
go side by side. Each is built separately (`--instance 2 build --mod ...`) and
may hold a different plugin set. The runtime DLLs and their INIs are
**copies**, not hard links, so each instance keeps the runtime it was built
with: rebuilding `tes_runtime` (or another instance) never touches a running
game, and a new runtime reaches an instance only on its next `build`. The
bridge DLL and the converted **plugins** (`.esm`/`.esp`/`.esl`) are copies for
the same reason: a linked bridge DLL could not be relinked while any instance
ran, and the game holds its plugin open, so a linked plugin made the next
`--import-only` fail at its final rename ("file is locked"). Assets and
scripts stay hard links.
Instance N's `Saves` is a **junction** to
instance 1's, so a checkpoint saved by one loads in the other. Never run the
same folder with `--checkpoints` in both at once (same checkpoint names), and
never `rmtree` an instance profile: it follows the junction into instance 1's saves.

```bash
python -m tools.live.test_instance --instance 2 build --mod Morrowind.esm --mod TESGameSelect
python -m tools.live.test_instance --instance 2 mwtest --cases <folder> --fast --from <case>
```

### <a id="repeat-a-case"></a>Repeat a case

One pass proves nothing for a case that has failed intermittently: a fix
counts only after several passes **in a row** from its checkpoint.
`tools/live/repeat_case.py` runs one case (`--case X`, i.e. `--from X --to X`)
or a range `--times N` times on `--instance`, keeps each run's runner output
and runtime log as `<out>/<label>_<n>.out/.log`, stops at the first failure,
and exits 0 only when all N passed.

```bash
python -m tools.live.repeat_case --cases <folder> --case co_report --instance 2 --out temp/rc
```

## <a id="hidden-launch"></a>Hidden launch

`hidden_launch.launch` is skse64_loader's shape (create suspended, load DLLs
from a remote thread, resume) with the process placed on its own desktop via
`STARTUPINFO.lpDesktop`. The game renders and ticks normally there and can
never take focus, input or the screen.

- The game holds a duplicate of the desktop handle, so the desktop outlives
  the launcher process.
- **SKSE needs its entry call.** A plain `LoadLibrary` of `skse64_1_6_1170.dll`
  loads it and does nothing: the loader's stub (`InjectDLLThreadData`,
  `skse64_loader_common/Inject.cpp`) also calls export ordinal 1,
  `StartSKSE`. `inject(..., entry_ordinal=1)` writes that stub byte for byte.
- **Screenshots** use `PrintWindow(PW_RENDERFULLCONTENT)`, which captures the
  D3D window on a hidden desktop. The desktop handle must stay open across
  find and capture: once this process closes it, the window handle reads as
  invalid here (`IsWindow` 0, `PrintWindow` error 1400).

## <a id="sandbox"></a>Profile sandbox

The exe finds My Games (INIs, saves) and `Plugins.txt` through one import,
`SHGetFolderPathA` (CSIDL 5 and 0x1C); SKSE and our plugins use
`SHGetFolderPath`/`SHGetKnownFolderPath`. SKSE opens its log at
`_initterm_e`, before any plugin loads, so an SKSE plugin is too late.

`TESSandbox.dll` is injected **before** SKSE. It repoints shell32's export
table (everything bound later: SKSE, every plugin) and the exe's import slot
(bound already) at hooks that answer Documents and LocalAppData from
`TES_SANDBOX_DOCUMENTS` / `TES_SANDBOX_LOCALAPPDATA`. No code bytes change.
Verified: the player's `skse64.log`, `Skyrim.ini`, `SkyrimPrefs.ini` and
`Plugins.txt` kept their timestamps across every run.

<a id="sandbox-mute"></a>With `TES_SANDBOX_MUTE=1` a thread re-mutes the process's Windows audio
session every 2 s (`hr=0` logged). Windows remembers that mute per exe path, so a later launch of the
same exe meant to be heard sets `TES_SANDBOX_MUTE=0`: the session is unmuted for its first minute. That mute is the ONLY silencing: the game
itself runs at `fAudioMasterVolume=1`, and never at 0. At game volume 0 the
engine does not wait out a line's voice file; it moves to the next response by
the text alone. Measured 2026-11-20 at real speed on the Emperor's greeting
(`chargen_emperor_in_cell`, `pace 1`): at volume 0 each response ended 0.85 to
2.98 s before its voice file did; at volume 1, with the session still muted,
0.07 to 0.93 s (the measure takes the longest voice type's file). The instance
ran at volume 0 from 2026-10-26 until then, so every line-timing reading in
between (`quiet`, `hear` waits, gaps) came from text-timed lines.

## <a id="startup-popups"></a>Startup popups that pause the game

Each opens a `MessageBoxMenu` (menu flag bit 0, pauses the game) a few
seconds after the main menu, so world time stops for every later test. Both
are settled in the INIs `launch` re-applies every time:

| Popup | Cause | Setting |
|---|---|---|
| "Thanks for buying Skyrim Anniversary Edition! Download and activate?" | the Bethesda.net AE entitlement reply, with no CC files present (`0x946df0` -> `0x947630` -> `0x946250` on 1.6.1170) | `bEnablePlatform:Bethesda.net=0` |
| "Couldn't connect to the Bethesda.net servers." | the main-menu login, still attempted with the platform off | `bAutoSkipMainMenuLogin:Bethesda.net=1` |

`bUpsellOwned:General=1` (set in the player's own prefs) does not suppress
the first. `smoke --settle` waits at the main menu first because both arrive
late: a run that `coc`s within ~4 s escapes them by luck.

The settings are not enough on their own. The login box still opened on 3 of
6 launches with Morrowind.esm loaded (`MessageBoxMenu` over a pausing
`Login Menu`). The exe only hands `bAutoSkipMainMenuLogin` to the main menu
movie as an argument (`0x94505b`), so the race is inside the movie.
`enter_cell` therefore force-hides both (UI message 4, kForceHide) before and
after the `coc`. Message 3 (kHide) leaves a `MessageBoxMenu` open. The
registered name of `LoginMenu::loginMenu` is `Login Menu`.

## <a id="console-without-a-keystroke"></a>Console without a keystroke

The bridge used to need one real, typed console command per session to
capture the console's "execution context". On 1.6.1170 that context is the
ScriptCompiler, and nothing on the compile path reads it: ConsoleExecute
(id 21954, `0x3412d0`) only forwards it to CompileAndRun (`0x343c20`), which
only passes it as `this` to `0x344af0`, `0x343e80`, `0x345390` and
`0x345270`, none of which dereferences it. `ExecOne` therefore passes a zeroed
stand-in until the game's own pointer is captured.

Two faults hid behind the bootstrap:

- **MemAlloc's heap.** `AllocScriptObject` called MemoryManager::Allocate
  (id 68115) with a null `this`; it reads `[this+0x28]` first and faulted at
  `SkyrimSE.exe+0xcc40f6`. The engine passes the global at id 400188 in all
  7,034 of its calls; the bridge now does too.
- **Off-thread probe.** `IsGameLoaded()` ran a console command from the pipe
  thread; the Script constructor faults off the main thread, so every `ping`
  at the main menu returned `E_INTERNAL unknown`. The probe now runs on the
  main thread, and `MenuManager::IsMenuOpen("Main Menu")` decides first,
  because the player object already exists at the main menu.
- **Never zero a block from the game's allocator.** MemAlloc (id 68115) returns
  memory carrying allocator bookkeeping (the first qwords of a fresh 0x80 block
  are heap pointers). A `memset(mem, 0, 0x80)` before `Script::ctor` in
  `game_bridge/plugin/script_object.cpp` made the console executor compile the
  script and do nothing, with every piece verified correct in isolation (vtable,
  `[+0x1A]=0x13`, text at `+0x38`, compiler index 1, Script as arg2). A
  constructor initializes every field it owns; zero only fields you own, after
  construction. Found by running `resolve`/`readmem`/`call`/`hookstats`
  primitives against the live process: console hook 0 hits vs Papyrus hook
  23,916 proved the detour was fine and the commands never executed.

Task faults are logged with code and module offset
(`main thread: task faulted, code C0000005 at SkyrimSE.exe+0x...`) instead of
surfacing as a bare "handler did not produce a result".

A `coc` keeps the console's print mode on through the cell load, and every
condition evaluated then echoes: 10.5 MB for one `coc QASmoke`. Capture is
capped at 64 KB (`[output truncated]`).

## <a id="mwscript-cases"></a>MWScript opcode cases

```bash
python -m tools.live.test_instance build --mod Morrowind.esm   # links output/Morrowind.esm + runtime DLLs
python -m tools.live.test_instance mwtest [--cases X.mwtest] [--keep]
```

`build --mod <name>` hard-links the converted `output/<name>` tree (the
plugins at its root are enabled) and the runtime DLLs from `MODS[MOD_NAME]`
in `package_runtime_dll.py`. It also writes the menus the package composes
(`morrowind_menus`, `chargen_table`). Without them the dialogue window has no
movie: `LoadMovie('morrowind_dialogue') FAILED`, and the conversation closes the
moment it opens. A file is removed before it is written, so a write can never
go through a hard link into `output/`.

`mwtest` launches with `TES_MW_TEST_CASES`/`TES_MW_TEST_RESULTS` set, `coc`s,
and waits for the `done` line. MorrowindRuntime's test runner
(`test_runner.cpp`) is driven by the object tick once the player is in a cell.
A case is a list of steps, run in file order:

| Step | Does |
|---|---|
| `run` + MWScript lines | runs the body as `actor` through the OpenMW interpreter |
| `wait <ticks>` | lets that many ticks pass |
| `expect <condition>` | wraps it as `if ( cond ) set TestResult to 1 else ... 0`; -1 means the probe did not compile or run |
| `talk <npc>` | activates the NPC as the player does, then waits up to 90 ticks for the conversation to open; a conversation already open with `npc` is taken as is, one left open with someone else gets its Goodbye first (MW backend; `approach` likewise) |
| `ask <topic>` | clicks a topic the list shows; fails naming the listed topics when it is not there |
| `unlisted <topic>` | a check, at once: the open conversation does not list the topic (whole text, any case); fails naming the listed topics. Baurus's `unlisted "Rumors"` (`0040_chargen_baurus`) guards the No Rumors flag |
| `choose <n>` | answers pending choice `n`; fails naming the pending choices |
| `bye` | clicks Goodbye; fails while a choice locks it |
| `button <n>` | clicks button `n` of the open MessageBox once it has been up 2 ticks, then waits (up to 30 ticks) for that box to close, so the next `button` never clicks a box that is still closing. MorrowindRuntime's class quiz raises its next box at once; before the wait, one `button` answered two questions (2026-11-17) |
| `activate <ref>` | activates the reference as the player does, without waiting for a conversation |
| `until <ticks> <condition>` | waits for the condition to hold; fails the case after `ticks` with the condition's `left` |
| `rest <max>[x<hours>] <bed\|-> <condition>` | sleeps `hours` (default 1) in the bed, or waits on the spot for `-`, up to `max` times until the condition holds; each sleep counts once, when its Sleep/Wait menu closes. A script that counts `Day` changes (stronghold builders, Boethiah's shrine) takes `x24`: one day per sleep, seconds instead of ~30 min of 1-hour sleeps |

An action with no `wait` after it settles for 2 ticks. An action that fails
ends its case; a failed `expect` does not. The clicks are the window's own calls (`ConversationAsk` and the
rest, handed to the runner through `SetTestConversation`). While a menu other
than the conversation holds the game, a `run`, `activate`, `until` or a new
`talk` waits for it to close, and every other step goes on: a `button` has to
click the MessageBox that holds the game. After 300 held ticks the case fails
with "a menu holds the game" rather than the run hanging; the sleep/wait menu
gets 20 times that (`kSleepHeldFactor`), because a 24-hour sleep runs in real
time at 1x and outlasts 300 ticks (Raven Rock's `co_brawl`). A `follow` step
that already stands at its spot issues no new move: each move set `MoveLanding`,
which holds every `until` (`co_thief` hung on it).

One JSON line per case records `ok`, the step `error` if one failed, each
verdict, `said` (the newest history line after each conversation step), and
the stubbed (unported) commands the case reached for the first time. `mwtest
--stop-on-fail` (`TES_MW_TEST_STOP_ON_FAIL`) ends the run at the first failing
case, for cases that build on each other; the `done` line counts the `skipped`
ones.

```
case skill
actor player
run
player->SetBluntWeapon 42
wait 2
expect player->GetBluntWeapon == 42
end

case talk_ask_bye
talk arrille
ask little advice
bye
end
```

`actor` (default `player`) is optional; `#` starts a comment only at the
beginning of a line, and `actor` is a directive only outside a `run` body.

The verdict global exists only at run time, so the compile context falls back
to `State().HasGlobal` (as `DialogueContext` already did at run time).

Cases live in `tes_runtime/morrowind/testdata/ingame/`. `mwtest` joins every
`*.mwtest` there in name order, so a later file can rely on an earlier one:
`40_npc.mwtest` starts with `PositionCell` into Arrille's Tradehouse.
`20_stats.mwtest` is generated (Set, Mod, Get of every attribute, skill and
effect).

- **A failing check carries `left`**, the value its left side read
  (`set TestResult to <left side>`), so a failure says what the game held.
- **Coverage:** the closing line lists `uncovered`, the commands with a real
  opcode (`RealCommands()`: keywords whose code `InstallReal` answers) that
  no case's script or expect names. `script_test` prints the same list
  offline, with no game.
- `==` on a regenerating value (health, fatigue) is a bad case: health read
  150.14 two ticks after `ModCurrentHealth`. Use a range.

2026-10-24: 174 cases, all passing. They cover 345 of the 365 real commands.
The 20 left either open a menu or a conversation (`EnableRaceMenu`,
`ShowRestMenu`, `PlayBink`, `ForceGreeting`, `Choice`, `Goodbye`,
`GetButtonPressed`, `Activate`), end the session (`GoToJail`, `SetDelete`), or
need an ID not yet chosen (soul gems, a lockable reference, a corpse for
`Resurrect`). A menu case needs the runner to close the menu it opened.
Cases with no `expect` only prove a command compiles and runs. Building them
found seven runtime bugs, all fixed and documented in
[morrowind_runtime.md](morrowind_runtime.md): `set GameHour` reverted
([the clock](morrowind_runtime.md#the-clock)), `ModPCCrimeLevel` after a set
([crime](morrowind_runtime.md#crime-is-the-engines)), `ModHealth` never
raising the maximum, magic-effect `Set`/`Mod`
([stat commands](morrowind_runtime.md#stat-commands)), and `GetScale` read
with the race height and stale after `SetScale`
([scale](morrowind_runtime.md#scale)), and `Position` not moving anything
([one engine move](morrowind_runtime.md#place-at)).

## <a id="speed"></a>Speed and resources

```bash
python -m tools.live.test_instance speed [--ini General:iFPSClamp=30] [--console "sgtm 4"] [--seconds 10]
```

The profile's SkyrimPrefs.ini starts from the launcher's `Low.ini` preset at
640×360. Both INIs live in the sandbox profile (`_profile\Documents\My
Games`), never the player's. `speed` reports game seconds per real second and
the engine's two per-frame deltas (ids 410199 and 410200).

The speed-up is Skyrim's own `iFPSClamp=N` ([General] in Skyrim.ini): every
frame advances the game a fixed 1/N s however long it took to draw. The
instance draws about 60 frames a second, so the speed is about 60/N, measured
2026-10-24 in QASmoke: N=15 4.19×, N=6 11.18×, N=3 24.5×. The cost did not
change with it: about 1.3 cores of 32 and 2 GB at 1× and at 10×. `--fast`
uses N=6.

The runtime's own tick follows game time, not the wall clock. It still wakes
30 times a real second, but each wake runs ONE tick covering the game time
the frame timer advanced since the last wake (id 410196, `+0x20`, a ms clock
that adds each clamped frame delta), capped at 1 s. Several fixed ticks per
wake broke cases at 1×: an `AddItem` and the `GetItemCount` after it landed
in the same frame, and the count does not see an item added that frame.

At 10× and 24×, 4 of 186 cases fail because the case assumes time: `==` on
magicka and fatigue that regenerate, an angle read across ticks, and a fight
expected to end within 30 ticks. Not engine faults; they want ranges or
`until`.

## <a id="quest-runs"></a>Quest runs

```bash
python -m tools.live.test_instance mwtest --cases <quest folder> --new-game --fast --checkpoints --stop-on-fail --timeout 3600
python -m tools.live.test_instance mwtest --cases <quest folder> --fast --checkpoints --stop-on-fail --from <case>
```

A quest run plays from a new game with only what a player does: talking,
asking, choosing, activating, walking, and console commands that stand in
for play (`coc`, `kill`, god mode). It never sets a quest global or a stage a
player could not.

- **`--new-game`** `coc`s from the main menu and sets `CharGenState` to 1,
  the write the TES3 engine made on New Game and the one TESGameSelect's
  `BeginMorrowind` makes ([the opening](morrowind_runtime.md#vanilla-morrowind-chargen));
  the real `CharGen` script then moves the player to the prison ship.
- **`--fast`** adds `iFPSClamp=6` for the run and `tgm` once in the game.
- **`--checkpoints`** (`TES_MW_TEST_CHECKPOINTS`): after each passing case the
  runner waits; the harness saves `mwtest_<case>` in the sandbox profile (never
  the player's saves) and writes the count of finished cases to
  `<results>.ack`, and the runner goes on.
- **`--from <case>`** loads the save of the case before it and runs from there,
  so a fix is retested without replaying the quest.

Cases live in `tes_runtime/morrowind/testdata/quests/<quest>/`.

## <a id="tes4-quest-cases"></a>TES4 quest cases (Oblivion, Nehrim)

**Code:** `tes_runtime/common/test_runner.{h,cpp}`, `tes_runtime/tes/test_game.cpp`,
`tools/live/tes4_cases.py`, `tools/live/test_instance.py` (`questtest`)

```bash
python -m tools.live.test_instance build --mod Oblivion.esm
python -m tools.live.test_instance questtest --cases tes_runtime/tes/testdata/quests/oblivion_main --new-game <button> --fast --checkpoints --stop-on-fail --timeout 3600
```

A converted TES4 plugin has no interpreter of ours to hook: its quests,
dialogue and scripts are Skyrim's own records and Papyrus. So the runner is
split. `common/test_runner.cpp` holds the grammar and step loop above
(the same steps, `.tes4test` files, `TES_TEST_*` variables), and each runtime
hands it a `TestBackend`. TESRuntime's (`test_game.cpp`) drives Skyrim's own
menus as a player does:

| Step | Engine call (1.6.1170) |
|---|---|
| `talk`, `activate` | `ObjectReference.Activate(player)` once `Is3DLoaded` |
| `ask`, `choose` | the Dialogue Menu's `TopicClicked` movie callback (id 51509), with the index of the topic whose text matches |
| `bye` | its `CloseMenu` callback (id 51511), what leaving the conversation runs |
| `button` | MessageBoxMenu's `buttonPress` (id 52279), so `Message.Show` gets its answer |
| `take` | `AddItem(ref)` on the player |
| `pick` | MorrowindRuntime's `TESTestPick` export, the same row pick its own cases use: a converted chargen's birthsign and class asks (`TES4_Chargen.Ask`) open that runtime's menus, and `Message.Show` is only the fallback |
| `walk <ref \| x y z>` | the player is AI-driven (`Game.SetPlayerAIDriven`, third person) and given the path request `Actor.PathToReference` makes (id 37893), to the ref or a placed XMarker; asked again every 300 ticks, done within 128 units |
| `hear <ticks> <speaker \| any> [text]` | `SubtitleManager::AddSubtitle` (id 52626, 6 call sites patched) queues each begun line with its speaker |

Spoken lines go to the results the tick they begin
(`{"speaker","text","tick","during"}`) and onto the case line's `said`.
AddSubtitle records nothing while both subtitle settings are off, so
`questtest` turns on `bGeneralSubtitles` and `bDialogueSubtitles`; it skips
the player. A `hear` takes the first line not yet taken, run-wide, so a
force-greet that the last stage of the previous case started is still heard.
Engine text is cp1252: the harness writes the prepared cases and reads
results in it.

The topic list is MenuTopicManager's (id 401099): a node list at `+0x20`
whose first node is skipped when byte `+0xb7` is set, each item's text a
`char*` at `+0` with its length at `+8`. That is the walk `SelectTopic`
(`0x5dfa50`) makes from a clicked index, and the text `PopulateDialogueList`
(`0x914de0`) hands the movie. `TopicClicked` reads only the handler's
presence and one number, then calls `SelectTopic` under the manager's lock.
Read off 1.6.1170 and 1.6.659. **None of it has run in game yet.**

`run` and `expect`/`until` have no game-side runner, so they go to the
harness: the runner writes `{"run": ...}` or `{"judge": ...}`, the harness
runs it through the bridge console and writes `<verdict> <left>` to
`<results>.answer` before the ack. A judge's left side is a quest variable
(`Quest.var`, read from `sqv`) or any console condition function, read from
the `>> value` it prints.

Cases name the **TES4** records. Skyrim keeps no EditorID for a placed
reference or most base objects at runtime, so the harness reads each loaded
converted plugin once (2.6 s for Oblivion.esm) and turns names into runtime
FormIDs:

- a reference's EditorID or a base placed exactly once (`talk Baurus`);
- a TES4 FormID in hex, for a reference with no EditorID. Converted records
  keep the low 24 bits: all 8,003 named ACHR/REFR in Oblivion.esm do;
- quests and globals stay names, which the console resolves. A TES4 name
  that Skyrim.esm already uses carries a `TES4` prefix (`MQ00` becomes
  `TES4MQ00`), and the harness tries both.

Measured against the export: every ACHR, NPC_, QUST and REFR EditorID in
Oblivion.esm (10,875) and Nehrim.esm (6,645) resolves. Each case's line also
lists the Papyrus error lines logged during it (`questtest` turns on
`bEnableLogging`).

Both runtimes run this one step loop, so a `.tes4test` uses exactly the
`.mwtest` grammar: MorrowindRuntime's `plugin/test_backend.cpp` hands it the
in-game MWScript judge and its game hooks. TESRuntime has `approach` (MoveTo
in front of the NPC, as MorrowindRuntime's) and `pace` (MorrowindRuntime's
frame governor, which runs in every converted game, through its `TESTestPace`
export); a step whose hook TESRuntime lacks (`defend`, `follow`, `punch`)
fails naming it. `persuade` needs the in-game judge.

TESRuntime's `talk` / `activate` on an ACTOR walks the player up to it again
when it has left talking reach since `approach`. At test pace a wandering NPC
crosses a room, or goes out a door, between two steps: run 47a met Jauffre on
his once-a-day patrols at 17:30, and the talk opened nothing from 523 away. It
does not turn a player who is already in reach. A SetAngle that lands as the
conversation opens closes it (47d, 47e: every conversation closed after 10
ticks with no line). It also waits while the engine's topic manager still names
a speaker (+0x68). A scene spoken at the player without a menu holds one until
its last line, and the engine opens no new conversation until then. Run 48a's
talk with Martin opened nothing during Savlian's chapel speech.

<a id="talk-waits-for-a-seat"></a>It also waits while the actor is sitting down
or getting up. TESNPC::Activate (1.6.1170 0x3b9846) treats a player's
activation as handled and does nothing while the actor's sit state is anything
but standing (0), sitting (3) or sleeping (7). The sit state is bits 14..17 of
the u32 at actor +0xC8, measured equal to GetSitting. A player clicks again; the
runner's single click was lost. At test pace a sandboxing NPC changes seats
every few seconds. In run 57f Martin at the Kvatch refugee camp opened nothing,
and a scratch case of 12 talks failed at the second or third talk (twice). With
the wait, 12 of 12 opened.

Every movie call the runner makes (`ask`, `choose`, `bye`, `button`, `pick`) runs
at UI time
([morrowind_runtime.md](morrowind_runtime.md#movies-are-touched-at-ui-time)).

### <a id="quiet"></a>Silences between lines: `expect quiet <= N`

**Code:** `tools/live/silences.py`; the runner's `Listen` / `NoteMenusOpened` in
`tes_runtime/common/test_runner.cpp`.

`hear` proves a line was said, never how long the silence before the next one
lasted. The Morroblivion census office's 11 s of silence after Socucius's quiz
offer, and the Emperor's late "Let me see your face...", passed every case.
Wall-clock silences also mean nothing under `--fast`: script timers count game
time, which runs about 10x ahead, while voice audio plays in real time. At test
speed, Socucius's 21.16 s timer ran out after 2 real seconds, and the box
came up over his 9.82 s line.

- Each spoken line in the results carries `ms` (`GetTickCount64` when
  `AddSubtitle` ran) and `game` (the engine's game clock in ms,
  MorrowindRuntime's `TESTestGameMs`). The runner also writes
  `{"menu","ms","game","tick","during"}` when a `MessageBoxMenu`,
  `MorrowindClassMenu` or `MorrowindBirthMenu` opens, because a script's
  silence often ends at a box, not a line.
- The silence is the one a player at normal speed hears, worked out at any
  speed (`silences.silence`). An event that came while the line's audio still
  played was released by game time alone, so its silence is the game time
  since the line began, less the line's length (Socucius: 21.16 - 9.82 =
  11.3 s). An event that came after the audio ended may have waited on it, so
  only the time after the end counts, the smaller of its real and game
  readings: a 0.3 s real pause is 3 game seconds at 10x. That branch can only
  under-report a timer that happens to outlast the audio. The first branch can
  over-report: see the noise below.
- A line ends at its start plus its own voice file's length. The harness finds
  the response by its text in the loaded plugins' exports (`INFO.txt`), then
  its `<quest>_<topic>_<fid>_<n>.mp3`, taking the longest voice type. The index
  takes about 3 s, on the first `quiet` of a run.
- `expect quiet <= N` passes when the longest silence in the case so far, from
  a line's end to the next line or menu, is at most N seconds. A line with no
  voice file counts as length 0, and the report names it.
- At test speed the measure carries up to ~5 s of noise. Any real-time delay
  between a script's timer and its next line or box (a poll, the Papyrus VM)
  reads as game time multiplied by the speed: at 17-23x, 0.25 real seconds is
  4-6 game seconds. Socucius's fixed gap measured 0.6, 1.9, 2.7 and 5.5 s on
  four runs (2026-11-17), against 11.3 s before the fix. Cases use
  `quiet <= 6`, which still catches the 8-11 s gaps that were bugs.
- `pace 1` does NOT give real timing under `--fast`, and lasts only for the case
  that sets it. The governor keeps steering the frame step, and Papyrus's
  `GetCurrentRealTime` (what converted timers count) drifts against the wall
  clock the audio plays to: within one `pace 1` run the two clocks moved up to
  5 s apart (2026-11-20). The Emperor's greeting then started 2.67-3.59 s after
  a 3.07 s line, fixed or not. To judge a cut line or a gap of a few tenths, run
  WITHOUT `--fast`, setting `--ini Skyrim.ini:General:iFPSClamp=0 --ini
  Skyrim.ini:Display:iVSyncPresentInterval=1 --ini
  Skyrim.ini:Display:bLockFramerate=1` (the profile keeps `--fast`'s values):
  the same case read 3.59-3.94 s, five runs in a row
  ([script_convert.md](script_convert.md#a-stage-fragment-runs-before-its-poll)).
- A pair where the second line is the next response of the same INFO is not
  measured: the engine plays an INFO's responses back to back on its own. In
  run `ob_before` (2026-11-17) the Emperor's four greeting responses came
  3.6-6.9 game seconds apart but 0.17 real seconds apart, read as false gaps.
  That run was at game volume 0, which times responses by text, not by voice
  file ([sandbox mute](#sandbox-mute)); the instance no longer runs that way.
- `expect quiet <= N since <text>` measures from the first line of the case
  containing `<text>` (any case and spacing) and judges -1 when no line does.
  Scope it to NPC-driven stretches: NPCs walking to their marks are silence
  too, and a step the player takes (`ask`, `button`) puts the harness's own
  delay into it.

### <a id="failure-snapshot"></a>Failure snapshot

**Code:** `tools/live/fail_snapshot.py`, called from `test_instance.tes4_game`.

A failed TES4 case is snapshotted the moment its result line arrives, through
the bridge the harness already holds, before the runner goes on or the game
stops: open menus, the player's position and combat state, every script
variable (`sqv`) and the stage of each quest the case's expectations read, and
position / distance / combat / AI procedure of each reference the case names
or that spoke in it, plus the package the engine actually RUNS for it
(`RunningPackage=<FormID> type <n>`: AddrLib 39155 on the actor's process at
+0xF8; TESPackage +0x24 is the procedure type, SKSE's order, Spectator = 24).
`GetIsCurrentPackage` reads the package's own slot and missed the case that
needed this: an unloaded Countess and Martin frozen under an engine-made
(`FF`) package of type 32 while their real one still read current (run 60a);
a runtime-made package also shows the first 16 bytes of its target (+0x38).
The lines print under the `FAIL` and go to
`_profile/fail_<case>.txt`, with a screenshot beside it
(`fail_<case>.png`). An intermittent failure is therefore captured on its
first occurrence; no `--keep` rerun is needed to see the state. The harness
owns the bridge pipe for the whole run, so an outside probe cannot read the
game while cases run -- anything worth reading at a failure belongs in the
snapshot.

### <a id="new-game-click"></a>The New Game click is sent once

`start_new_game` calls the main menu's New Game handler through the bridge.
The handler runs on the main thread and starts the load, which reads the whole
converted plugin; on a busy machine that outlasts the bridge's wait and the
call comes back `E_LOADING` while it is still running (dump
`temp/crash/nostart_1112_213858`: main thread inside the call, in `fread`).
`Bridge.request` retries `E_LOADING`, which for this call would queue a second
New Game, so the click goes out with one attempt and the harness then waits
for the game to be ready (300 s). It also waits for the game to answer BEFORE
the click. When the load outlasts the bridge's wait, the New Game still
happens (`bridge: new game` in TESGameBridge.log), but the bridge's task
faults as the call returns to a request the harness side already gave up
(`main thread: task faulted, code C0000005 at TESGameBridge.dll+0x8d0a`, runs
17b, 17c) and the next request answers with an error; `ready_after_load`
keeps asking until the game answers. The fault itself is a bridge defect that
is still open. A bridge that never connects, or any other
bridge error before the cases start, leaves a `nostart_*` dump in
`temp/crash/`.

### <a id="drafting-a-case"></a>Drafting a case

**Code:** `tools/live/quest_brief.py`.

`python -m tools.live.quest_brief --export export/<plugin> --quest <EDID>
[--skeleton]` prints one block per stage of a TES4 quest, as comment lines
ready to paste above a case: the journal text, the stage's own result script,
and every place that sets the stage -- a script with the `begin` block it sits
in, another quest stage, or a dialogue line with its topic, first spoken line,
stage / speaker conditions and the choices it offers. `--skeleton` adds a
draft `case` per stage. What a case still needs from its author is what the
player does between stages (where they stand, whom they fight), which the
export cannot say.

### <a id="god-mode-after-a-load"></a>God mode after a checkpoint load

**Code:** `tools/live/test_instance.py` (`enter_game`, `god_mode_on`, `LOADING_MENU`).

`--fast` turns god mode on with the console's `tgm`, which TOGGLES. Two replays from checkpoints (run 59: Menien,
Mankar) ran without it: the player took damage in the load gap (Skyrim's "Health Low" help popup) and was later
killed by a Paradise daedra, and Skyrim's own death reload stopped on the "Creations are loaded" prompt. The load
wait looked for the loading screen by its menu name, "Loading Menu", but `open_menus` reports RTTI class names
(`LoadingMenu`), so the wait for it to close passed at once and `tgm` went out while the save was still loading.
Now every load waits on the class name, and `god_mode_on` reads tgm's answer ("God Mode enabled." / "God Mode
disabled.") and sends it again when it turned god mode off. That was not enough on its own: a later Menien replay
printed "God Mode enabled." and the Sigil Keeper still killed the player, because without the "Creations" prompt the
wait for the loading screen to CLOSE ran before it had even OPENED, and the save then loaded over god mode. Every
load now waits for the loading screen to appear, then to close, before `tgm`. That reading was WRONG too, measured
on the kept game: a checkpoint load takes under a second, and god mode SURVIVES a load (after a death reload the
first `tgm` answered "disabled"). The real gap: the save drops the player into a fight (Menien's tower: the Sigil
Keeper's first hit 3 s after the load) while the harness was still in its menu waits (up to ~10 s, over a game
minute at test pace) before `tgm`. So `god_mode_on` now runs BEFORE the `load` as well, and once more after it
(either answer leaves it on). Note: a console `DamageAV` lowers health even in god mode; combat damage does not.

## Known limits

- One instance at a time (one pipe, `\\.\pipe\tes_test_bridge`, and one
  desktop name).
