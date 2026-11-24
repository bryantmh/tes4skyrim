# The window: TESRACT's five steps

**Code:** `gui.py`, `core/gui/` (`ui/*.slint` and the Python controllers beside them)

## Contents

- [Why Slint, and how it is split](#slint)
- [Threads: nothing blocks the window](#threads)
- [What Windows supplies itself](#native-pieces)
- [Settings and the current choices](#session)
- [Step 1, Setup](#setup)
- [Step 2, Choose: games and plugins](#choose)
- [Games: data folders and added mods](#games)
- [Step 3, Convert](#convert)
- [Step 4, Load order](#load-order)
- [Step 5, Install & play: mod managers](#mod-managers)
- [Reading Vortex's own folders](#vortex-staging)
- [TESRACT.exe: one file](#single-exe)
- [Updating TESRACT.exe](#updates)

## <a id="slint"></a>Why Slint, and how it is split

The window is drawn by Slint (`slint` 1.19.0b1 on PyPI, a pre-release; `requirements.txt` pins it, and
pip installs an exactly pinned pre-release without `--pre`). The look lives in `core/gui/ui/*.slint`:
`theme.slint` (colors for light and dark, fonts), `widgets.slint` (buttons, pills, ticks, segmented
switches), `state.slint` (one global, `Ui`, holding every value a screen shows and every callback it
raises), one file per step and `dialogs.slint`. The `.slint` files hold no logic. The Python controllers
(`setup_view`, `choose_view`, `run_view`, `loadorder_view`, `install_view`) set `Ui`'s properties and
answer its callbacks; the modules without `_view` (`checks`, `games`, `mods`, `loadorder`, `install`,
`runner`, ...) read and write the disk and know nothing of Slint, so tests call them directly.

Slint's Python API maps a property `skyrim-checks` to `app.Ui.skyrim_checks`; a struct is a dict whose
keys use underscores; a list is a `slint.ListModel`.

Layout rules learned the hard way (each one blanked or clipped a screen):

- An element placed in a plain `Rectangle` (not a layout) gets its own preferred size, which for a
  component is often zero. Every view and every dialog is given `width`/`height` of its parent.
- A `ScrollView` makes its content as wide as the content wants; every page sets
  `content-width: self.visible-width` so long text wraps or elides instead of running off the right.
- A `VerticalLayout` inside a `ScrollView` stretches its rows to fill the height unless it has
  `alignment: start`.
- Columns that must line up across rows get a fixed share (`width: 45%`): stretch factors are applied
  to what is left after each cell's minimum, so a row with a tag lays out differently from one without.

## <a id="threads"></a>Threads: nothing blocks the window

Slint is not thread-safe and has no way to post to its event loop from another thread. `Ctx.post(fn)`
puts `fn` on a queue; a 50 ms `slint.Timer` on the UI thread (`window._tick`) runs whatever is queued,
expires the notice, takes dropped files and advances the run. `Ctx.work(fn, done)` runs `fn` on a daemon
thread and posts `done(result)` back; an exception is posted to `Ctx.fail`, which shows it. Everything
that reads plugins, scans exports, checks a version over the network or installs runs this way.

Garbage collection runs only on the UI thread. Slint's Python objects may be freed only by the thread that
made them (pyo3 panics "PyStruct is unsendable, but is being dropped on another thread" and the process
dies), and Python's automatic collector runs on whichever thread happens to allocate, so a worker
reading Vortex's state once collected a cycle holding a Slint struct and killed the window at start.
`run_window` disables automatic collection and freezes what startup built (`gc.freeze`); `_tick`
collects new objects every tick and does a full pass every `FULL_GC_TICKS` (a full pass measured 9 ms
unfrozen, 0.2 ms frozen).

The first checks (Setup's `refresh` and the old-version clean-up) start from a one-shot timer once the
event loop runs, not inside `build()`: a worker started earlier competes for the GIL with the first
frame, and Nuitka-compiled code hands the GIL over far less often than the interpreter does. Measured
2026-11-23: the window took 10.7 s to appear from TESRACT.exe while the Vortex read below decompressed
every block on a worker; from source it took 1.5-2.2 s.

A run (`runner.Run` for `convert.py` and tools, `run_view.Task` for in-process work such as the journal
patch and installs) reports `("cmd", i)`, `("line", text)` and `("end", code)` events on its own queue;
the tick drains it.

## <a id="native-pieces"></a>What Windows supplies itself

Slint draws the window but has no file dialogs, no file-drop event and no window icon API that reads an
`.ico`. `native.py` calls Win32 through ctypes:

- Pickers: `GetOpenFileNameW` and `SHBrowseForFolderW`, owned by the window so they stay on top.
- Dropped files: the window's drop target is revoked (`RevokeDragDrop`, Slint registers an OLE one that
  accepts nothing), `DragAcceptFiles` turns on `WM_DROPFILES`, and `SetWindowSubclass` catches it.
- Icon: `docs/assets/favicon.ico` is loaded at the small and big sizes Windows asks for and sent with
  `WM_SETICON`; the handles are kept for the life of the process (freeing one blanks the taskbar
  button). `SetCurrentProcessExplicitAppUserModelID` gives the taskbar button our icon instead of
  Python's.
- Slint creates the native window when its event loop starts, not in `show()`: `find_window` returns
  nothing until then, so the icon and drop target are attached from a timer once the window exists.
- Dark mode: `HKCU\...\Themes\Personalize\AppsUseLightTheme`, for the "system" theme.

## <a id="splash"></a>Starting: the splash, then a finished window

Something is on screen within a fraction of a second of starting, and the window never appears half
filled. `gui.gui_main` shows the splash (`splash.py`) before it imports the window: a layered Win32
popup on its own thread and message loop, so the main thread can import, compile the interface and read
every screen's data without freezing it. Its picture is drawn once with PIL and given to Windows with
`UpdateLayeredWindow`, and its window procedure is `DefWindowProcW` itself, so no Python runs on the
splash thread after that.

`window.run_window` builds the window hidden, starts the first checks, and runs posted work itself until
every job `Ctx.work` started has finished and nothing is left to run (`Ctx.busy`), so Setup, Choose,
Load order and Install are painted before the window is shown. `STARTUP_WAIT` caps the wait: a network
read (the update check, the release table) that stalls shows the window anyway. The splash closes once
Windows has created the window (`_hook`).

## <a id="session"></a>Settings and the current choices

`session.Session` holds what every screen reads: the saved settings (`conversion_config.json`, saved
key by key through `Session.save`) and the choices that last until the window closes (`Selection`: the
LOD order and worldspaces, the masters to flag, the body-patch plugins, the Oblivion UI menus, the mesh
folders, and the steps when the user overrides the automatic plan). A load-order mod is included when
its `include.<key>` setting says so; body patch, start mod and journal are included unless turned off.

## <a id="setup"></a>Step 1, Setup

Checked on every start on a worker (`checks.py`): Skyrim's folder and version, SKSE64, the Address
Library for that exact version (also inside MO2's mods), the Creation Kit, xWMAEncode and the bundled
tools, the output folder, MO2 and Vortex. The window opens on Setup while Skyrim, SKSE or the Address
Library is missing, and on Choose otherwise. Skyrim 1.6 and 1.7 have full support; older builds convert
every game but Morrowind, whose runtime needs 1.6 or later (Choose refuses a Morrowind game there).

The shared in-game menus (look, icons, classes and birthsigns) are chosen here, because one choice
serves every converted game. A choice with more than three options (classes and birthsigns lists every
converted game) is a dropdown: a segmented row does not wrap and ran off the window.

## <a id="choose"></a>Step 2, Choose: games and plugins

A game card per data folder and per standalone added mod. Picking a Morrowind game asks for its mode
(vanilla, Morroblivion, hybrid) before anything else; every Morrowind mod follows the root's mode, and
changing it later offers to rebuild the Morroblivion patch. The plugin list shows each plugin's status
from `version.upgrade_plan` (new, out of date, up to date). Out-of-date plugins start ticked, and new ones
only when they belong to the base game (a data folder holds many optional plugins).
The steps are automatic: each ticked plugin owes the steps its plan names, and plugins owing the same
steps share one `convert.py` (`runner.group_plugins`). Advanced overrides them.

Mod archives (`.7z`, `.zip`, `.rar`) and folders are added by dropping them on the window or with the
buttons beside the drop line; an installer with options asks which parts to use (`mods.ImportPick`).
"From archives" filters the list to plugins that came from one.

## <a id="games"></a>Games: data folders and added mods

`games.list_games` lists the directory games the exports and config know, plus added mods that need no
base game (their masters are all their own) as games of their own, keyed `mod:<id>`. Morrowind and its
kin are found by their TES3 plugins; their `scripts` and `speedtrees` steps are skipped.

The cards are in a fixed order: registered Data folders in the order they were added, then the
configured `tes4DataPath` when it is not registered, then standalone added mods, newest first. The
configured folder used to come first, and picking a game saves its folder there, so the cards
reordered on every start.

"Remove from list" beside the selected game unlists a Data folder (`source_registry.remove_directory`
records it under `removed_directories`, so neither `migrate_known_directories` nor the `tes4DataPath`
fallback lists it again; Add a game brings it back) and removes a standalone added mod the way Manage
added mods does. Nothing on disk is deleted for a folder; converted output stays either way.

The filters are All, Converted (up to date or out of date), Out of date, Not converted, From archives
and Ticked; the chips read their keys from `choose_view.FILTERS`.

## <a id="convert"></a>Step 3, Convert

One lane per step per `convert.py`; a lane starts when `convert.py` prints that step's banner
(`core/phase_info.PHASE_INFO`, shared with `convert.py`, so a renamed banner cannot desynchronize them)
and ends at the next. Each finished lane's duration is kept in `logs/step_times.json` and estimates the
next run's bar. The log keeps the last 4,000 lines on screen; the full run log is written as before
(`core_run_log.md`). Cancel stops the running command (`process_job.run_process`'s cancel event); the
commands after it are skipped.

## <a id="load-order"></a>Step 4, Load order

The mods built once for every converted game: SKSE runtime, body slot patch, start mod, quest journal
stage text, masters, distant LOD and the Oblivion UI. Each is up to date while its stamp
(`runner.global_is_current`) matches what it was built from. "Build ready" builds every included mod
that is out of date, one after another, the journal patch last (in-process, `journal.build_journal_mod`).

Distant LOD bakes one pass over every worldspace, so it is held back while any plugin that ships LOD
for a worldspace (`loadorder.worldspace_owners`) is out of date: building then bakes the old version.
The list shows each worldspace and what still needs updating; "Update them" ticks those plugins on
Choose, "Build anyway" builds now.

## <a id="mod-managers"></a>Step 5, Install & play: mod managers

Four ways, saved as `modManager`:

| Way | What it does |
|---|---|
| Mod Organizer 2 | Extracts each zip into the instance's mods folder, puts them enabled at the top of the profile's `modlist.txt` in install order (the last installed wins, so LOD overrides), and enables their plugins in `plugins.txt` and `loadorder.txt`. MO2 must be closed: it rewrites those files when it exits. "Play" starts `moshortcut://<instance>:SKSE`. |
| Vortex staging | Extracts each zip into Vortex's staging folder for Skyrim SE; Vortex lists them as mods on its next start, to enable and deploy. |
| Vortex downloads | Copies each zip into Vortex's downloads folder, where Vortex offers to install it. |
| Zips only | Nothing installed; the zips stay in Finished Mods, listed in install order. |

A `tesract.json` beside each extracted mod records the zip's name, size and time, so an unchanged zip
reads as installed and uninstall removes only the mods TESRACT put there. The order is the runtime
first, the converted games by name, LOD last.

MO2 is found from the folder saved in Setup, else beside the `nxm:` handler MO2 registers, with the
instance in its own folder (portable) or `%LOCALAPPDATA%\ModOrganizer\<instance>` whose `gameName` is
Skyrim Special Edition.

## <a id="vortex-staging"></a>Reading Vortex's own folders

Vortex keeps its settings in a LevelDB database, `%APPDATA%\Vortex\state.v2`, not in a readable file.
`vortex_state.py` reads it without a LevelDB library: each `.ldb` table's blocks (snappy-compressed;
the decompressor is in the module) and the `.log` journal's batches, newest value winning. A lookup
decompresses only the blocks whose index range can hold the key (an index key is at or past every key
in its block and before every key in the next; versions of one key can span blocks, so a block whose
predecessor's index key equals the key is read too). Decompressing every block in pure Python took
about 2 s per setting on a real 2,612-block state, twice per start; the indexed lookup takes 0.02 s for
both. The staging
folder is the value of `settings###mods###installPath###skyrimse`, with `{game}`, `{GAME}` and
`{USERDATA}` expanded; the downloads root is `settings###downloads###path`, and Skyrim SE's downloads go
in its `skyrimse` subfolder. Vortex holds the `.log` open while it runs, so a running Vortex's newest
change may not be readable yet; the `.ldb` tables always are. A folder chosen in Setup overrides both.

## <a id="single-exe"></a>TESRACT.exe: one file

`tools/release/build_exe.py` compiles `gui.py` and everything it reaches with Nuitka (`--onefile`) into
`build/exe/TESRACT.exe`. Nuitka turns the Python into C, so pure-Python work (record export, NIF
parsing) runs faster than under CPython; numpy and scipy work is unchanged.

- **What is inside**: the compiled code, Python itself, and every tracked file the pipeline reads that
  is not source (`build_exe.data_files`: external tools, generated tables, runtime DLLs, the window's
  banner and icon), staged into `build/exe/data` and included at the root.
- **Unpacking**: the first start of a version unpacks into `.tesract/<version>` beside the exe
  (`--onefile-cache-mode=cached`); every later start, and every process it starts, reuses it.
- **Where it writes**: `output_layout.DATA_ROOT` is the exe's folder (Nuitka's
  `__compiled__.containing_dir`), so `export/`, `output/`, `logs/`, `conversion_config.json` and a
  located `xWMAEncode.exe` (`external/xwmaencode/`) sit beside TESRACT.exe and survive a new version. The
  first process puts the folder in `TESRACT_DATA_ROOT`, which the processes it starts inherit, because
  they run from the unpacked copy and would otherwise see that copy's folder. From source,
  `DATA_ROOT` is the checkout, as before.
- <a id="dev-link"></a>**Testing the exe on the checkout's folders**: every build also hardlinks
  `build/exe/TESRACT.exe` as `TESRACT.exe` in the repo root (`build_exe.link_into_checkout`; ignored by
  git, as is its `.tesract/`). Started from there, its data folder is the checkout, so it shares
  `export/`, `output/`, `logs/`, `conversion_config.json` and `.conversion_state.json` with
  `python gui.py`. One link to the exe, not links to each folder: the state file is saved by
  `os.replace`, which swaps a linked file for a plain copy, and a junction inside `build/exe` would let
  a cleanup of that folder delete the real `export/` or `output/`.
- **Starting another program**: there is no `python.exe` and no `.py` file inside the exe, so every
  place that starts a script calls `core.python_cmd.python_cmd(script, *args)`: from source
  `python -u <script>`, inside the exe `TESRACT.exe --tesract-run <module>`. `gui.main` hands that flag
  to `run_from_argv`, which imports the module and exits with its `main()` (Nuitka's compiled modules
  cannot be re-run as `__main__`), with line-buffered output (the Convert screen
  reads it line by line). A module started this way must be in `build_exe.RUN_MODULES`.
- **Measured** (2026-11-23, Nehrim.esm, 32-thread machine, each run writing to its own scratch
  folder): record export 9.6 s from source, 3.8 s from TESRACT.exe (2.5x; the formatting/write part
  8.4 s to 2.3 s); extract + meshes 779 s from source, 749 s from the exe (4%: that stage's time is
  numpy, the native extension and disk). Both outputs are byte-identical (65 export files, 12,649
  NIFs). The exe is 120 MB built with `--low-memory` (zstd with fewer threads), about 90 MB without;
  it unpacks to 350 MB on first start (6.6 s), and a later start of `convert` takes 3.3 s. A build
  takes about 33 minutes cold and 8 minutes with Nuitka's compile cache warm.
- **Packages whose source ships too** (`build_exe.SOURCE_PACKAGES`): PyFFI is compiled, and its whole
  folder is also copied beside the compiled code. PyFFI reads its XML format definitions and `VERSION`
  from that folder, and `asset_convert/nif/pyffi_monkey_patch.py` rewrites one PyFFI method from its
  source (`inspect.getsource`), which finds a compiled function's source only when the `.py` file sits
  at the compiled module's path. Without it the patch does not install and converted NIFs vary between
  runs. (Shipping PyFFI uncompiled instead needs `distutils`, which Python 3.12+ gets only from
  setuptools' startup hook.)
- **Data files**: `--include-data-dir` silently drops `.exe`, `.dll`, `.pyd` and `.bin` files, which
  is most of `external/` and `native/dist/`; the staged folder goes in with `--include-raw-dir`.
- **Console**: `--windows-console-mode=attach`: no console window when double-clicked, but output
  appears when the exe runs from a terminal (`TESRACT.exe --tesract-run convert -f Nehrim.esm`).

## <a id="updates"></a>Updating TESRACT.exe

Setup's Update (when a newer release tag exists, `version.check_for_update`) downloads the release's
`TESRACT.exe` asset to `TESRACT.exe.new` beside the running one (`core/gui/exe_update.py`). Windows
refuses to overwrite a running exe but allows renaming it, so the running exe becomes `TESRACT.exe.old`
and the download takes its name; "Restart now" starts it and closes the window. Each start deletes the
`.old` file and the unpacked copies of every other version under `.tesract/`.

Nothing the player made is touched: export, output, logs, settings and `.conversion_state.json` sit
beside the exe, so after the restart the plugin list marks exactly the steps the new version changed
(`version.upgrade_plan`), as it does for a source update. Run from source, Update opens the release page.

Every release must carry a `TESRACT.exe` asset built by `tools/release/build_exe.py`; a release without
one makes Update fail with "Release <tag> has no TESRACT.exe".
