# Headless in-game testing

**Code:** `tools/live/test_instance.py`, `tools/live/hidden_launch.py`,
`game_bridge/sandbox/sandbox.cpp`, `game_bridge/plugin/` (console path)

An isolated Skyrim that an agent launches, drives and stops on its own: no
window on the player's screen, no sound, no input, no keystroke bootstrap, and
nothing written to the player's install, INIs, saves or logs.

```bash
python -m tools.live.test_instance build      # once, and after a bridge rebuild
python -m tools.live.test_instance launch
python -m tools.live.test_instance smoke      # coc, no pausing menu, time runs, screenshot
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
Creation Club content, ENB, `winhttp.dll` loaders, the CK and every other mod
stay out. Hard links cost no disk and leave the install untouched.

The exe is the unpacked `SkyrimSE.1.6.1170.unpacked.exe` from the depot
folder; `build --steam-exe` switches to Steam's. Both reach the main menu
identically. `steam_appid.txt` plus `SteamAppId`/`SteamGameId` keep the
Steam API from relaunching through Steam.

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

With `TES_SANDBOX_MUTE=1` a thread re-mutes the process's Windows audio
session every 2 s (`hr=0` logged), on top of `fAudioMasterVolume=0`. Master
volume rather than `bEnableAudio=0` keeps sound-timed behaviour, such as line
durations, real.

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
| `talk <npc>` | activates the NPC as the player does, then waits up to 90 ticks for the conversation to open |
| `ask <topic>` | clicks a topic the list shows; fails naming the listed topics when it is not there |
| `choose <n>` | answers pending choice `n`; fails naming the pending choices |
| `bye` | clicks Goodbye; fails while a choice locks it |
| `button <n>` | clicks button `n` of the open MessageBox |
| `activate <ref>` | activates the reference as the player does, without waiting for a conversation |
| `until <ticks> <condition>` | waits for the condition to hold; fails the case after `ticks` with the condition's `left` |

An action with no `wait` after it settles for 2 ticks. An action that fails
ends its case; a failed `expect` does not. The clicks are the window's own calls (`ConversationAsk` and the
rest, handed to the runner through `SetTestConversation`). While a menu other
than the conversation holds the game, a `run`, `activate`, `until` or a new
`talk` waits for it to close, and every other step goes on: a `button` has to
click the MessageBox that holds the game. After 300 held ticks the case fails
with "a menu holds the game" rather than the run hanging.

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

## Known limits

- One instance at a time (one pipe, `\\.\pipe\tes_test_bridge`, and one
  desktop name).
