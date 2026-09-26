# GUI overhaul: setup, MO2 integration and per-world launching - design plan

**Status: PLAN, unimplemented.** No code has been written. Each piece below is
meant to land as its own small PR, useful on its own; nothing here needs the
whole plan to be accepted.

Goal: a new user downloads the converter, converts a game and plays it without
typing a command, copying files by hand, or fighting their mod manager.

## Contents

- [Where the GUI stands](#where-it-stands)
- [The pieces](#pieces)
- [Suggested order](#order)
- [Constraints every piece follows](#constraints)
- [Open questions for the maintainer](#open-questions)

## <a id="where-it-stands"></a>Where the GUI stands

- tkinter, in `core/gui/`: 11 modules, about 5,800 lines, started by `gui.py`
  or `gui.pyw`. `runner.py` (975 lines) and `app.py` (827) are near the
  ~1,200-line file cap, so new features go in new modules.
- `preflight.py` already knows every missing dependency and how to install it,
  but only reports; the GUI shows the report.
- Installable output goes to `output/Finished Mods/` as zips whose root is the
  `Data` folder. Nothing detects, installs into, or writes profiles for any mod
  manager.
- The load order is read from `%LOCALAPPDATA%\Skyrim Special Edition\plugins.txt`
  (`core/gui/config.py:417`, `asset_convert/lod/sibling_lod.py:293`). Mod
  Organizer 2 keeps each profile's `plugins.txt` in its own folder and exposes
  it only to programs it launches, so for MO2 users this list is usually empty
  or stale; LOD then falls back to its structural order.
- Converted assets live under a per-game folder (`meshes\tes4\...`), so several
  worlds share a `Data` folder without colliding
  ([asset_convert_texture.md](../commentary/asset_convert_texture.md#per-game-asset-namespace)).
- Source extraction reads BSAs only; loose files in a game's `Data` folder are
  ignored
  ([tes5_import_mod_merge.md](../commentary/tes5_import_mod_merge.md#part-3-what-still-missing)).

## <a id="pieces"></a>The pieces

| # | Piece | New module(s) | Depends on |
|---|---|---|---|
| A | Dependency "fix it" buttons | `core/gui/dependencies.py` | nothing |
| B | Read the load order from an MO2 profile | `core/mo2.py` | nothing |
| C | Install output straight into MO2 | `core/mo2.py` | B |
| D | Generate one MO2 profile per world | `core/mo2.py` | C |
| E | Source mods and their order from a source-game MO2 instance | `core/mo2.py` | B |
| F | First-run setup screen | `core/gui/setup_wizard.py` | A, B |
| G | Worlds tab with Play buttons | `core/gui/worlds.py` | D or H |
| H | Standalone launcher, no mod manager | `core/launcher.py` | nothing |

### A. Dependency "fix it" buttons

Turn each preflight finding into an action instead of a message.

- **`xWMAEncode.exe`:** with the user's consent, download the Microsoft DirectX
  SDK (June 2010) from Microsoft and extract only `xWMAEncode.exe` into
  `external/xwmaencode/`. It cannot be redistributed, but fetching it from
  Microsoft on the user's own machine is what the README already asks users to
  do by hand. The installer is roughly 570 MB for one small file: show the size
  before downloading. Worth checking first whether Skyrim can play voice
  without xWMA, which would remove the dependency entirely.
- **Creation Kit:** only Steam can install it; open its Steam page and re-run
  the check when the user returns.
- **Python packages:** offer `pip install` into the running interpreter.

### B. Read the load order from an MO2 profile

A setting that points at an MO2 instance and profile. Instances are found from
a portable `ModOrganizer.ini` beside `ModOrganizer.exe`, or under
`%LOCALAPPDATA%\ModOrganizer\<instance>\`. The profile's `plugins.txt` replaces
`%LOCALAPPDATA%`'s for the plugin list and for LOD tile priority. Small, and it
fixes a real bug for every MO2 user. (MO2 file formats in this plan are to be
verified against MO2's own source before building.)

### C. Install output straight into MO2

Write each finished mod into the instance's `mods\<Name>\` (hard links when on
the same drive, copies otherwise) plus a `meta.ini`, instead of a zip the user
imports by hand. Re-converting updates the same folder. MO2 must be closed
while these files are written, because it rewrites its lists on exit; refuse
and say so when it is running.

### D. One MO2 profile per world

For each converted world, write a profile (for example `Cyrodiil`) with its mod
list, plugin list, and per-profile saves and INIs enabled. A world profile
loads only `Skyrim.esm` plus that world's plugins and the runtime DLLs, so
other worlds' start-game quests never run, saves stay separate, and per-world
rules, UI and visual mods become possible.

### E. Source mods from a source-game MO2 instance

When Oblivion, Morrowind or FNV is itself managed in MO2, that instance's mod
list already defines the merged setup, including loose files and overwrite
order. Feed it to the existing ordered import (`--import-mod A B C --as NAME`)
instead of asking the user to rebuild the order. Read MO2's files directly;
running the converter inside MO2's virtual file system would route tens of
thousands of file accesses and every worker process through its hooks.

The pipeline-side counterpart for non-MO2 installs, a loose-file overlay on top
of BSA extraction, is a separate change outside the GUI.

### F. First-run setup screen

On first launch: confirm the detected game installs, choose a workflow (Mod
Organizer 2, Vortex, or no mod manager), choose an output location, and run the
dependency check from A.

### G. Worlds tab

One row per converted world: converted, installed, profile ready, and a **Play**
button that launches the world's MO2 profile (D) or the standalone launcher (H).

### H. Standalone launcher, no mod manager

For users without a mod manager:

1. **Install into `Data` with a manifest** of every file written, so uninstall
   and update are clean. Not optional: removing loose files by hand is what mod
   managers exist to avoid.
2. **Swap the active plugin list** in `%LOCALAPPDATA%\Skyrim Special
   Edition\plugins.txt` for the chosen world, backing up the user's and
   restoring it when the game exits.
3. **Separate saves per world** through Skyrim's `sLocalSavePath` INI setting.
4. **Launch through `skse64_loader.exe`**, which the runtime DLLs need.

Detect Vortex or MO2 managing the same install and hand off to them rather
than fight over `plugins.txt`. Pairs with a TESGameSelect setting that skips
the menu when only one converted world is loaded.

## <a id="order"></a>Suggested order

1. **A** (dependency buttons): the biggest remaining barrier for new users.
2. **B** then **C**: small, and B fixes an existing bug.
3. **E**, then the non-MO2 loose-file overlay.
4. **D**, **H**, then **F** and **G** on top.

## <a id="constraints"></a>Constraints every piece follows

- **One PR per piece**, each mergeable and useful alone.
- **New modules, not growth** of `runner.py` or `app.py`.
- **`gui` stays importable headless**; tkinter is imported inside functions
  (`tests/test_gui_startup.py` asserts it).
- **No new runtime DLL.** A world picker on Skyrim's main menu would need one
  and is deliberately left out; it would be its own proposal.
- **No writes into a mod manager's files while it is running.**

## <a id="open-questions"></a>Open questions for the maintainer

1. Is MO2 the mod manager to integrate first, with Vortex kept on the zip route?
2. Should the standalone launcher (H) exist at all, or should the converter
   always hand off to a mod manager?
3. Is downloading the DirectX SDK on the user's behalf (A) acceptable, given
   its size and license?
4. Where should per-world MO2 profiles be named and ordered: fixed names per
   game, or user-chosen?
