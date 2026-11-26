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
- [Step 4, Shared mods](#load-order)
- [Step 5, Install & play: mod managers](#mod-managers)
- [Reading Vortex's own folders](#vortex-staging)
- [Uninstalling a mod Vortex has deployed](#vortex-deployed-uninstall)
- [Starting: the splash, then a finished window](#splash)
- [TESRACT: a compiled folder](#folder-build)
- [Updating TESRACT](#updates)

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

Every callback in `state.slint` returns nothing, and Slint raises "Object is not a dict or NamedTuple"
when a Python handler returns anything else. `lambda f: (setattr(ui, "filter", f), self.paint_list())`
returns a tuple: the work ran, then every filter chip and the search printed a traceback (found by the
walk, below). Handlers are methods or single calls that return None;
`tests/test_gui_window.py::test_no_interface_callback_returns_a_value` checks every `ui.<name> = ...`.

`Tick` and `Seg` show the value they are given and never change it themselves: a click only asks
(`toggled`, `selected`) and the owner sets the property. When they assigned their own `checked` or
`current`, the assignment cut the binding to `Ui`, and the screen and the program drifted apart: the
plugin list's header box always started empty, so its first click ticked what was already ticked, and
turning parallax on never enabled "Textures only" until the dialog reopened (`Ui.parallax` stayed 0).
Each `Tick` also carries a `label` and the checkbox role, so screen readers, and the walk, find it by
name and read its state from the ✓ it draws.

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
- Standard handles: `gui.main` first calls `ensure_std_handles`, which points any standard handle that
  is not a handle in this process at `NUL`. The compiled app is built `--windows-console-mode=attach`;
  started without a console (by the launcher, Explorer or a shortcut) its handles were copied values
  that mean nothing in it (measured 2026-11-23: input -1, output 4, error 5, `GetFileType` 0 with
  error 6), and `subprocess.Popen` duplicates the inherited standard input, so every conversion failed
  at once with "[WinError 6] The handle is invalid". Started from a terminal the handles are real, which
  is why runs started from a shell never showed it. Reproduced with a probe compiled the same way and
  started by the real launcher, detached and through `start`; after the fix the same probe starts its
  child.

  That first fix ran only in the GUI's own process, and a run still failed: every creature in a
  Morrowind Creatures run (2026-11-23) reported "OSError: [WinError 6]". A run's pool workers are new
  processes the frozen app starts without passing handles, they never go through `gui.main`, and the
  creature tools capture output but leave input alone (`hkx_xml`, `humanoid_graph`), so Python
  duplicated the worker's invalid standard input. The repair now lives in `core/subprocess_flags.py`
  and runs when that module loads: every module that starts a program imports it for `POPEN_FLAGS`,
  so every process repairs its handles before it starts anything. Reproduced with the built exe through
  the launcher with no console (`--cli -f Morrowind.esm --creatures-only --only guar` failed the same
  way); `tests/test_subprocess_flags.py` sets a worker's invalid input and checks a capture fails
  without the module and runs with it.

## <a id="splash"></a>Starting: the splash, then a finished window

Something is on screen within a fraction of a second of starting, and the window never appears half
filled. In the release folder the launcher (`native/src/launcher`, the top-level TESRACT.exe) shows the
splash before Windows has read any of the app: it draws from `app/splash.bin`, which the build renders
with `splash.write_splash_file` at 100-300% display scale, so both splashes are the same pixels. It
starts `app/TESRACT.exe` with the splash window's handle in `TESRACT_SPLASH`; `splash.Splash` adopts
that window and closes it when the app's window is ready, and the launcher exits. Started any other way
(from source, or `app/TESRACT.exe` directly), `gui.gui_main` shows the splash itself before it imports
the window: a layered Win32 popup on its own thread and message loop, so the main thread can import,
compile the interface and read every screen's data without freezing it. Each frame is the still
picture plus the load bar's sliding segment (`splash.segment_span`, the launcher does the same
arithmetic), handed to Windows with `UpdateLayeredWindow`; the window procedure is `DefWindowProcW`, so
Windows never calls back into Python.

Both are per-monitor DPI aware before their first window (the launcher by its manifest, the app by
`splash.make_dpi_aware`) and draw at the display's scale. The first splash was not: Windows placed it
in scaled coordinates, then Slint made the process DPI aware and the splash jumped aside.

`window.run_window` builds the window hidden, starts the first checks, and runs posted work itself until
every job `Ctx.work` started has finished and nothing is left to run (`Ctx.busy`), so Setup, Choose,
Shared mods and Install are painted before the window is shown. Setup and Shared mods read side by side;
Choose and Install follow Setup. The update check is not counted (`Ctx.work(..., counted=False)`): its
footer notice may arrive after the window, but a slow network never holds it back. The release table is
read from its saved copy ([saved table](version_upgrade_planning.md#saved-table)). `STARTUP_WAIT` caps
the wait for anything else that stalls. The splash closes once Windows has created the window (`_hook`).

Measured from source, warm (2026-11-23): 5.5 s before these changes, 1.7 s after. The cuts: the
release table fetch (0.9 s, now saved), Choose re-parsing `.conversion_state.json` 482 times (now one
parse per file stamp, `version._read_state`), Shared mods read twice at startup (its `plugins_changed`
now rereads only when statuses really changed), `worldspace_owners` running the whole LOD dialog scan
(it now asks only for worldspaces by plugin), the CELL dump scan (one byte regex, not a line loop) and
FO3/FNV LOD tile counting (`os.scandir` instead of a stat per file: 0.29 s to 0.02 s).
`script_convert/__init__.py` re-exports nothing, so importing one light submodule no longer loads the
converter.

## <a id="session"></a>Settings and the current choices

`session.Session` holds what every screen reads: the saved settings (`conversion_config.json`, saved
key by key through `Session.save`) and the choices that last until the window closes (`Selection`: the
LOD worldspaces, the masters to flag, the body-patch plugins, the Oblivion UI menus, the mesh
folders, and the steps when the user overrides the automatic plan). A load-order mod is included when
its `include.<key>` setting says so; body patch, start mod and journal are included unless turned off.

## <a id="setup"></a>Step 1, Setup

Checked on every start on a worker (`checks.py`): Skyrim's folder and version, SKSE64, the Address
Library for that exact version (also inside MO2's mods), the Creation Kit, xWMAEncode and the bundled
tools, the output folder, MO2 and Vortex. The window opens on Setup while Skyrim, SKSE or the Address
Library is missing, and on Choose otherwise. Skyrim 1.6 and 1.7 have full support; older builds convert
every game but Morrowind, whose runtime needs 1.6 or later (Choose refuses a Morrowind game there).

When the output folder is on a different drive than Mod Organizer 2's mods folder or Vortex's staging
folder (`install.drive_notice`, from the paths found above), a notice at the top of "Output and mod
manager" says installing will copy instead of hardlink and which drive the output folder should be on.

The shared in-game menus (look, icons, classes and birthsigns) are chosen here, because one choice
serves every converted game. A choice with more than three options (classes and birthsigns lists every
converted game) is a dropdown: a segmented row does not wrap and ran off the window.

## <a id="choose"></a>Step 2, Choose: games and plugins

A game card per data folder and per standalone added mod. Picking a Morrowind game asks for its mode
(vanilla, Morroblivion, hybrid) before anything else; every Morrowind mod follows the root's mode, and
changing it later offers to rebuild the Morroblivion patch. The plugin list shows each plugin's status
from `version.upgrade_plan` (new, out of date, up to date). Every out-of-date plugin starts ticked; of the
plugins never converted, only the folder's root plugin does (`games.roots`, below), so base-game DLCs and
mods are the user's choice. A plugin no longer wanted is cleared (below) and stops being out of date.
A game with anything out of date opens on the "Out of date" filter, so what will run is what shows. Once the user picks a filter themselves, switching games keeps it.
The steps are automatic: each ticked plugin owes the steps its plan names, and plugins owing the same
steps share one `convert.py` (`runner.group_plugins`). Masters run first: `games.masters_first` orders
the ticked plugins by their masters (read from each plugin's header), and a plugin joins a group only at
or after its masters' groups, so Morrowind.esm converts before Tribunal and Bloodmoon. Advanced
overrides the steps.

Mod archives (`.7z`, `.zip`, `.rar`) and folders are added by dropping them on the window or with the
buttons beside the drop line; an installer with options asks which parts to use (`mods.ImportPick`).
"From archives" filters the list to plugins that came from one.

A plugin reads "Out of date" only when it owes a step it can and would run (`games._row`): owing only
Scripts or SpeedTrees on a Morrowind-format plugin (that stage does not exist for it), or only the pack
steps while packing is off, is up to date. Before this, Morrowind.esm and Tamriel_Data.esm were ticked as
out of date and then skipped by the run, so "Ticked" said 8 and the button "Update 6". A plugin that an
added mod provides is that mod's row only, even when a copy also sits in the Data folder: the registry
holds one entry per plugin, and the second row gave TR_Mainland, Tamriel_Data and the grass plugin two
ticks and two statuses. Clicking anywhere on a row toggles its tick, in the plugin list and in every
pick dialog; ticking repaints the filter counts too.

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

"Base game" is the official plugin list of each game (`games.OFFICIAL`, root master first), not "has
no masters": a masterless mod (BetterMenuControls.esp, Cobl Main.esm) is not base game. Fallout New
Vegas and Fallout 3 are separate games in separate folders; New Vegas's list also holds Fallout 3's
plugins because a Tale of Two Wastelands New Vegas install includes them. A folder's root
(`games.roots`) is each present game's root master that no other present game lists, so the TTW New
Vegas folder's root is FalloutNV.esm alone. For a standalone added mod, its masterless plugins are both.

"Clear" on a converted Data-folder plugin deletes what conversion made for it (its `export/<plugin>/`,
`output/<plugin>/` or its mod's group folder, an `output/<plugin stem>/` folder no registered plugin owns, and
`Finished Mods/<plugin>.zip`, `games.converted_files`) and its records in
`.conversion_state.json` (`version.forget_plugin`), so it reads as not converted and stops being
reported out of date. The plugin itself, and any copy a mod manager installed, stay. It is refused
while a converted plugin of the same game lists it as a master, since that plugin's conversion reads
the master's export. Added mods are removed with Manage added mods instead, which now deletes their converted output too (`games.remove_mod`, which keeps a group folder a remaining plugin of the mod still uses); before, the output stayed, which left `output/thelostspiresv14` behind.

## <a id="convert"></a>Step 3, Convert

One lane per step per `convert.py`; a lane starts when `convert.py` prints that step's banner
(`core/phase_info.PHASE_INFO`, shared with `convert.py`, so a renamed banner cannot desynchronize them)
and ends at the next. Inside a lane, each sub-step starts at its own log line (`eta.SUBSTEPS`, [below](#run-estimates)),
and a plugin's `[<plugin>] ` line moves a multi-plugin lane to that plugin's first sub-step, closing any it skipped.
The running lane reads "Running · <sub-step>" under its name and in its bar (with the plugin when the lane has
several, and the sub-step's `done/total` count when it prints one); the selected lane's card lists every
sub-step with its time, the running one as "time so far of ~estimate"; the rows scroll when they outgrow
the card, and skipped (up-to-date) lanes are 32 px tall instead of 46 so the card and the log keep room.

**Bars.** The running bar has diagonal stripes that move two pixels each redraw (`STRIPE_STEP`, four times a
second with `PAINT_EVERY`), drawn as one `Path` per stripe: no animation timer, so the window repaints no more
often than before. A queued bar is an outline with faint, still stripes; a finished one is solid. Bar text is
shown whole, else its short form ("Running", "Queued"), else not at all: each is measured by a hidden `Text`,
never elided to "Qu…". The red now label sits right of the now line (left near the axis's end), and a tick label
it would cover is hidden. The now line is drawn per lane, through the header and the lanes with bars only, so it
never crosses a skipped lane's "Up to date, skipped".

**The log** keeps the last 500 lines on screen (`LOG_SHOWN`), each colored by its `runner.classify` tag (error,
warning, success, heading, command) and each a read-only `TextInput`, so a line can be selected and copied;
**Copy** puts every line on screen on the clipboard (a hidden text field selects all and copies, the only
clipboard route Slint's Python API has). Slint 1.19's `StyledText` can color text (`<font color>`) but cannot be
selected, and a `TextEdit` selects but has one color, so neither does both. The rows sit in a `ListView`, which
lays out only the visible rows; new lines are appended to one `ListModel` and the oldest deleted, so an update
costs only the rows that changed. It follows the end while the reader is at the end; scrolled up, new lines wait
(`Ui.log-follow`) and arrive when the view is back at its end (`Ui.log-caught-up`). Measured with a 5 ms
heartbeat timer on the hidden desktop (`temp/profile/log_redraw.py`; 500 lines, 25 more every 250 ms, the bars
redrawn): the rows keep the UI thread busy 3.1% (12 ms a redraw), the earlier single `TextEdit`, which lays the
whole text out again on each update, 9.3% (20 ms; 75 ms at 2,000 lines and 150 ms at 4,000, which is why the
log is capped). "Full log" opens every line. The full run log is written as before (`core_run_log.md`). Cancel
stops the running command (`process_job.run_process`'s cancel event); the commands after it are skipped.

### <a id="run-estimates"></a>How long a run will take

**Code:** `core/gui/eta.py` (content, drivers, sub-steps, this computer), `core/gui/eta_fit.py` (the fitted
model, written by `temp/profile/substep_fit.py`), `core/gui/run_view.py` (lanes, live adjustment).

Lanes used to show the time the same step last took for the same plugin set, or 60 seconds when it had
never run. The next model fitted each whole step (`a + b*x` single-core plus `c + d*x` multi-core per format)
and missed wherever one sub-step dominated: Morrowind.esm's Export (267 s) was mostly the Morroblivion
hybrid's ground repaint (146 s) and asset copy (91 s), so TR_Mainland's and Grass_Aes's exports were predicted
at 13 minutes each (they took 160 s and 236 s) and Sky_Main + Sky_Main_Grass at 3:08 (80 s). Each step is now
a list of sub-steps, each timed from its own driver.

**What a plugin holds** is read before anything runs: its records' bytes by type (TES3 record headers;
TES4/FNV top-level group sizes), counts of LAND, HAIR and WEAP, Morrowind's voiced lines (INFO with SNAM),
worn models (ARMO/CLOT with body parts, INDX), its exterior references by base object (a regex over the
exterior CELL bodies), its grass statics (STAT whose model is under `grass\`, as
`morrowind_grass.is_grass_model` decides), and its assets by kind from its archives' file tables (plus an added
mod's loose tree). A reference to its own or any master's grass static is a groundcover placement. Grass_Aes's
2.7 million references take 0.47 s; the masters are read once and cached by file time.

**The sub-steps** (`eta.SUBSTEPS`) and their drivers. A sub-step starts at the first line matching its marker
(the first at the step's own start); the fit cuts every profiled log at the same markers, so the model and the
lanes cannot disagree about where one ends.

| Step | Sub-step | Starts at | Driver |
|---|---|---|---|
| Export | Exporting records | step start | records MB, masters' MB, groundcover placements and exterior cells under grass |
| | Matching Morroblivion models | `Creatures: N of` / `Hybrid: matching Morroblivion` | hybrid; a vanilla master's LANDs (it alone is repainted); non-grass exterior references |
| | Copying Morroblivion assets | `Morroblivion ground:` | hybrid; a vanilla master's MB; a non-vanilla plugin shipping textures |
| | Writing records | `Morroblivion (hybrid):` / `Text code page:` | records MB |
| Extract | Unpacking archives | step start | archive MB and files (warm: cached) |
| Meshes | Assembling Morrowind armor | step start | worn models |
| | Planning | `Wearable variant plan:` | NIFs, records MB |
| | Converting NIFs | `Found N NIF files` | NIFs, NIF MB (its `n/N` lines give progress) |
| | Hair and grass | `Results: N converted` | HAIR records |
| | Copying textures | `Copy Textures to Output` | DDS files, a vanilla master's MB |
| | Checking textures | `Textures: N files` | DDS files, a vanilla master's MB |
| | Book icons | `Generating book inventory-art meshes` | constant |
| SpeedTrees | Converting SpeedTrees | step start | SPT files |
| Creatures | Splitting creatures | step start | animations (warm: cached) |
| | Converting creatures | `Converting N creatures` | animations, animation MB; at least 6.5 s per MB of the largest folder |
| | Gun animations | `Retargeting N … gun clips` | WEAP records (New Vegas) |
| Import | Loading masters | step start | masters' MB |
| | Reading the export | `Reading exports from:` | records MB, masters' MB |
| | Converting records | `Converting records...` | records MB |
| | Generating navmeshes | `Generating N navmeshes` | indoor pathgrids, exterior cells (warm: navmesh cache) |
| | Writing the plugin | `Phase: navmesh generation` / `Converting N LAND` | records MB |
| | Morroblivion companion | `… Morroblivion Hybrid.esp] Importing` | hybrid; the plugin's and masters' MB |
| Sounds | Voices | step start | voice files (Morrowind: voiced lines) (warm: cached) |
| | Sounds | `[Non-voice sounds]` | sound files |
| | Music | `Converting music to xWMA` | music files |
| Scripts | Translating scripts | step start | SCPT MB |
| | Compiling | `Compiling N Papyrus scripts` | SCPT MB |

The Morroblivion sub-steps apply only when `morrowindSource` is `hybrid`, to every Morrowind-format plugin.
Only a vanilla master (Morrowind, Tribunal, Bloodmoon) is repainted (`match_ground` takes
Morroblivion's lands only for one), which is why Morrowind's matching took 146 s and TR_Mainland's, with no
lands repainted, 57 s (its 425,000 references). The one marker added for this is the export's `Hybrid:
matching Morroblivion models and ground...` line, printed before `apply_hybrid`, so a plugin without creatures
(no `Creatures:` line) still shows the sub-step. What the logs showed against the brief's guesses: the texture
copy follows files, not bytes (Tamriel_Data's 4 GB of HD textures copied in 15 s, 1.0-1.6 ms per file across
all four plugins), and the "silent pre-pass" before Tamriel_Data's NIF batch is the Morrowind armor assembly,
200 s single-core for 1,732 worn meshes.

**Fitting.** Each sub-step is fitted by non-negative least squares over its drivers (`"one"` is a constant),
weighted by `1/sqrt(seconds)`, per format from that format's plugins when two or more ran it, else pooled with
Oblivion and New Vegas. Data: the profiled runs (Morrowind, Tribunal, Knights, Nehrim, FalloutNV, cold and
warm; Tribunal at 29 workers; the Bloodmoon, ElsweyrAnequina and Anchorage validation runs, whose imports count
as cold) and the user's run log of Tamriel_Data's meshes, Sky_Main, Sky_Main_Grass, TR_Mainland and Grass_Aes
(its cancelled import and Anchorage's failed one left out). Sky_Main_Grass and Grass_Aes were logged before
the matching marker existed and place no creatures, so the matching fit's share of their export is moved out of
'Exporting records' (17.5 s each). Each sub-step's busy-core histogram comes from the
longest profiled run of it.

**Checked**, at the reference (29 workers): mean |error| over the 58 steps of 10 s or more is 12% in sample and
59% leave-one-plugin-out (each plugin predicted by a fit that never saw it); per step held out: export 13%,
meshes 43%, sounds 45%, scripts 52%, creatures 59%, import 119% (Nehrim's warm navmeshes predicted from
Knights alone). With two to four plugins per format, a held-out plugin is usually an extrapolation, so the
held-out numbers are the honest floor of what an unseen mod gets before the live adjustment. The user's run, in
sample (held out): Tamriel_Data meshes 896 s actual, 831 (963); Sky_Main export 45, 45 (46); Sky_Main_Grass
export 35, 37 (38); TR_Mainland export 160, 144 (102); Grass_Aes export 236, 240 (184: the only other grass
plugin is 19 times smaller); Sky_Main import 185, 104 (87). Known misses: Sky_Main's import re-read Tamriel_Data's
meshes just rebuilt in the same run (88 s against 31) and its companion loads 141 MB of masters (30 s against
14); Tamriel_Data's armor assembly runs 0.12 s per worn mesh against Morrowind's 0.05 (fitted 122 s, actual 201).

**This computer.** Single-core time scales by `bench_seconds` (a fixed text/dict/float loop, best of
three) over the profiling machine's. Multi-core time is rescaled from each sub-step's busy-core histogram:
a quarter second that kept `c` cores busy takes `max(1, τ(c) / T)` as long on a machine whose `workers`
deliver throughput `T = min(w, physical) + 0.5 * extra threads`. Fitted on Tribunal's meshes and creatures at
4, 8, 12, 16, 22 and 29 workers, predicted from the 29-worker trace alone: mean error 4.4% (hyperthread gain
0.3: 5.1%, 0.75: 4.5%, 1.0: 7.3%).

**Live.** A running lane ends at what it has run, plus what its sub-step still needs, plus its later
sub-steps' estimates. What the sub-step needs comes from its `done/total` lines once 2% is done, else its
estimate; past either it is still running, so the bar ends at least 5 s or a tenth of the time it has run
after now (`OVERRUN_MIN`, `OVERRUN_SHARE`), never at now. Queued lanes start at the running one's projected
end. Every estimate is scaled by this run's actual / estimated over its finished sub-steps of 3 s or more,
starting from 30 s of on-time evidence (`SPEED_PRIOR`) and kept within 4×: without both, the walk's Tribunal
run had a 0.3 s split that took 1 s stretch a 12 s Creatures bar to 40 s. Each finished sub-step of 3 s or more moves this computer's learned factor for that sub-step
(`logs/step_speed.json`, keyed `step/sub-step`) halfway toward its own ratio. The Choose screen's total is
`eta.plugin_estimate`, the same sub-steps summed.

**Profiles.** `tools/validate/profile_convert.py` ran every step cold (export/ and output/ moved aside, no cache
download) and warm (immediately again) with TESRACT.exe on a Ryzen 9 7950X3D (16 cores, 32 threads, 31 GB) at
29 workers, while another session kept 5-10 cores busy. Totals, cold / warm: Morrowind 818 / 565 s, Tribunal
161 / 122 s, Knights 131 / 82 s, Nehrim 1317 / 1054 s, FalloutNV 1709 / 988 s. Warm only helps extraction, the
creature split, the navmesh cache (Morrowind 175 -> 38 s, Nehrim 209 -> 55 s) and voices (FalloutNV 840 ->
156 s). Import loads the masters' export first: Knights' import holds 11 GB in one process for Oblivion.esm.

#### <a id="lod-estimate"></a>Create LOD's estimate

A load-order action had no model and was drawn as a flat minute, which Create LOD overran by tens of minutes.
Its time follows the land it bakes, so `eta.lod_plan` counts the exterior cells (the same `exterior_cells`
measure the steps use) of every plugin it reads land from (`runner.lod_sources`: each chosen worldspace's
owner and every selected plugin adding to one), as converted cells (a Morrowind cell is 2x2 of them), at
`LOD_SECONDS_PER_CELL`, from the one end-to-end measurement on record: all 18 of Oblivion.esm's worldspaces,
33,639 cells, in 10m58s ([performance](performance.md#4-terrain-lod-the-python-side-is-not-the-bottleneck)).
Counting only the owners was tried: for WrldMorrowind (Morrowind.esm owning, TR_Mainland adding most of the
land) it predicted 39 s against a measured 10m22s; the converted cells of all five plugins predict 6m22s. It
is scaled by this machine's benchmark, and the run's own time trains `create_lod/Creating LOD` like any
sub-step.

## <a id="quiet-tasks"></a>Jobs that are not a conversion

Clearing a plugin's output, installing, uninstalling and adding a mod run beside the pages (`core/gui/tasks.py`):
they never take over the Convert page. Progress shows in the status bar and a toast, and a failure shows in a
dialog. A job asked for while a conversion or another job runs waits its turn, says so, and starts when that one
ends.

A job is `RunView.start_task(title, fn, done)`; only a download the user watches (updating TESRACT, getting
xWMAEncode.exe) and the quest journal patch, which belongs to a load-order run, pass `foreground=True` and take
the Convert page as a run. A conversion asked for while a job runs says "Wait for ..." and does not start. A run
that ends, finished or cancelled, also sets the Steps choice back to automatic: the next plan comes from the
plugins' on-disk state, so steps that finished are no longer ticked. Pack Mod Zip is not a Convert step any more:
zips are made by Install & play's Manual install (`convert.py --pack-zip-only` stays for the command line).

## <a id="idle-steps"></a>Steps with nothing to do

`games.idle_steps` removes a step from a plugin's steps when the plugin holds nothing it works on. It reads
headers only: each top group's label (Oblivion, New Vegas; an empty group counts as absent) or each record's
type (Morrowind), and the folder names of the plugin's archives (`bsa_extract.read_bsa_folders`, which skips
every file record) or an added mod's top asset folders. SpeedTrees needs a TREE group or a `trees\` folder;
Creatures needs CREA, a `meshes\creatures\` folder, or FNV WEAP (whose gun clips it retargets); Sounds needs
SOUN, DIAL, INFO or MUSC, or a `sound\` or `music\` folder. A plugin whose file cannot be read keeps every step.
Measured: 1 ms (Tamriel.esp, none of the three) to 64 ms (Morrowind.esm, the record walk).

It runs only for ticked plugins, on the worker that also computes the time estimate (`games.read_idle`, called
from `ChooseView._estimate`), and the go bar repaints when it returns. Nothing reads it at startup, because
nothing on screen shows a plugin's steps until it is ticked: reading `eta.content` for all 233 plugins of six
games in the startup rows took 19.4 of the 19.5 s the rows took, past the splash's wait, so the window opened
empty.

## <a id="load-order"></a>Step 4, Shared mods

The mods built once for every converted game: SKSE runtime, body slot patch, start mod, quest journal
stage text, masters, distant LOD and the Oblivion UI. Each is up to date while its stamp
(`runner.global_is_current`) matches what it was built from. "Build ready" builds every included mod
that is out of date, one after another, the journal patch last (in-process, `journal.build_journal_mod`).

Stamps live only in memory, and a mod whose folder exists adopts the stamp it finds on launch. So a mod
whose inputs can change between launches has to be read back off disk instead (`runner._READ_BACK`).
Masters reads each plugin's ESM flag. The SKSE runtime compares every built DLL and ini in
`tes_runtime/dist` against its packaged copy by size and mtime (packaging uses `copy2`;
`runtime_files.packaged_state`). It also checks that the Morrowind menus are present when a Morrowind
install is registered. The runtime used to stamp only `TESRuntime.dll`, and its folder adopted that stamp,
so a rebuilt `MorrowindRuntime.dll` read as up to date and left no way to repackage it.

The plugins chosen in Masters are saved (`masterPlugins` in the config, `make_master.CONFIG_KEY`). Every
import that writes one of them flags it ESM again (`make_master.reflag_if_chosen`, called from
`convert.phase_import`, and for its hybrid companion too), so a reconverted plugin stays a master without a
Masters rebuild.

#### <a id="distant-lod-worldspaces"></a>Distant LOD: which worldspaces build

The Distant LOD item lists every worldspace the chosen plugins offer, each with a tick box, and builds only
the ticked ones (`create_lod.py --worldspaces`). The ticks start on the worldspaces whose LOD is out of date;
a tick the user changes holds until the next run ends. Out of date comes from the LOD mod's own bake record
(`asset_convert/lod/bake_record.py`, `.lod_bake_record.json`, never installed): `create_lod.py` forgets a
worldspace before baking it and, after a clean bake, records the start time, the run's plugin order and the
plugins the bake read (the owner and its overlays). A worldspace reads out of date when it was never baked,
its bake failed, a plugin the bake read is no longer chosen, or the plugins that ship LOD for it
(`loadorder.world_rows`) changed set or order or have a converted ESM newer than the bake. A LOD mod an
older build baked has no record; `bake_record.bake_of` dates each of its worldspaces by its
`meshes/terrain/<worldspace>` folder (every bake deletes and rewrites the tiles there), so updating the
converter doesn't tick every worldspace at once. The
plugin order the dialog confirms is put masters-first, as `create_lod.py` applies it, so a dependent the
user dragged above its master can't leave its worldspace reading out of date forever.

LOD built from a plugin that is itself out of date bakes the old version. That used to be a warning strip
under the list with "Update them" and "Build anyway" buttons, which read as two notices. Now Build asks only
when a ticked worldspace has such a plugin, in a dialog: Cancel, "Update them first" (ticks those plugins on
Choose) or "Build anyway". The row's pill names the count of out-of-date plugins.

"Order & detail" keeps what is rarely touched: the detail preset, the plugins (untick, reorder) and Force
(offer worldspaces with no shipped LOD). Done saves the whole plugin order, the plugins the user unticked and
Force (`lodChoice` in the config, `Session.save_lod_choice`), so they hold across launches. Saving only the
ticked plugins lost an untick at once: a plugin missing from that list read as newly converted and came
back ticked, at the bottom. A plugin converted later is still added ticked; one greyed out because its
master is off comes back with the master. Each read of the Shared mods screen rebuilds the plugin list
Create LOD gets (`Selection.lod_plugins`) from the saved choice.

The LOD dialog's Reset rescans on a worker (seconds in the compiled app) and then only repaints the
dialog if it is still open (`_reset_read`). It used to reopen it: Reset followed by Done brought the
dialog back over the screen a few seconds later, where it swallowed the next clicks (the walk caught
it in the compiled build only, where the rescan is slow enough to lose the race).

Reopening the dialog seeds it from the last confirmed choice, as the old Tk panel did. Arktwend's
`WrldArktwend` needs no Force ([always LOD](asset_convert_terrain.md#morrowind-format-worldspaces)).

## <a id="mod-managers"></a>Step 5, Install & play: mod managers

The page lists every converted mod, each with a tick box (`install_mods.list_mods`): every folder in `output/`
that holds a plugin at its root (a game, an added mod, a mod group such as Tamriel Rebuilt, the start mod),
plus the folders the Load order actions write, which have no plugin at their root and are known by name
(`install_mods.ROLES`: `TESRuntime`, `CreatureRuntime`, `HavokWorldSize`, `AutoConvertLOD`, `Oblivion UI`,
`Body Slots Patch`, `Quest Journal Stage Text`). The folders `Finished Mods` and the merged-sibling-LOD
folder are not mods. Order: the runtime first, the rest by name, LOD last. Rows that need work (new,
changed, no zip, old zip) start ticked.

Every mod is a folder. The runtime DLLs (`package_runtime_dll.py`), the Oblivion UI (`convert_ui.py`), the
body patch (`convert.py --modify-body-meshes`) and the journal patch (`journal.build_journal_mod`) used to be
built only as zips in `Finished Mods`, which MO2 and Vortex installs then had to extract. They now write the
same file tree as a folder through `output_layout.write_mod_folder` (built under `<name>.part` and swapped in;
the zip an older build left in `Finished Mods` is deleted, so it never sits there out of date). The start mod
was already a folder and no longer zips itself, and the Distant LOD item runs only Create LOD (Pack LOD stays
a Build-menu action). An end user upgrading from a zip-only build sees those mods as not built on Load order
until they are built again; the old installs still uninstall, since that reads only `tesract.json`.

Three ways, saved as `modManager` (a saved `vortex_downloads` falls back to Mod Organizer 2):

| Way | What it does |
|---|---|
| Mod Organizer 2 | Hardlinks each ticked mod's files from `output/` into the instance's mods folder, lists every mod of ours (not only the ticked ones, so a later partial install keeps LOD on top) enabled at the top of the profile's `modlist.txt` in install order (the last wins), and enables their plugins, masters first, in `plugins.txt` and `loadorder.txt`. MO2 must be closed: it rewrites those files when it exits. "Play through MO2" starts `moshortcut://<instance>:SKSE`. |
| Vortex | Hardlinks into Vortex's staging folder for Skyrim SE, then registers each mod in Vortex's state with its own command line (see [vortex-staging](#vortex-staging)). Vortex must be closed. |
| Manual install | Makes zips in `Finished Mods` for the ticked mods whose zip is missing, older than their newest file, or lists different files than the folder (the converter's pack-zip leaves loose meshes out, so its zips read as old). Every mod, the load-order ones included, is zipped from its folder the same way. |

No zip is made for MO2 or Vortex. Each file is hardlinked (`os.link`); when the target is on another
drive (`install.same_volume`, the volume id of the nearest existing folder) or a link fails, the files are
copied one by one and the log says so. What leaves the output folder are the mod's files without the
converter's own sidecars (`.manifest.json`, `.liptext.txt`, `.voicemap.txt`, `.voicecopies.txt`,
`music_tracks.json`, the packer's records, and the developer's `TESGameSelect_NoNehrimStart.esp`).

The page does not move when the way changes: the switch row holds only the label, the switch and the
manager's own button (Play through MO2 / Open Vortex), and the switch's segments keep one width whichever
is chosen. The second row of the same box is always there, one line high: the folder this way installs
into (MO2's mods folder, Vortex's staging folder, `Finished Mods`), elided, a muted detail (MO2's profile,
"hardlinked, same drive" or "copied: the output folder is on another drive"; for Manual install which
mods get zipped), and one button opening that folder (Open mods folder, Open staging folder, Open Finished
Mods). Select all and Select none sit left of the main button, which is right-aligned, so a longer label
moves nothing else. The walk checks this (`install switch stays put`).

`tesract.json` in each installed mod lists exactly the files placed, the source's stamp (file count, total
size, newest time) and the mode. Reinstalling after a reconversion links new files, relinks rewritten
ones, deletes the files the output no longer has and the folders they emptied; a mod reads "Update" while
the stamp differs. A marker from an older TESRACT has no file list: that mod was an extracted copy and its
folder is removed whole.

Uninstalling is per mod, like Choose's Clear: each installed row (Installed or Update) has a trash button
labelled Uninstall, which asks first. It deletes only the files `tesract.json` lists (a hardlink removed
leaves the output file alone; a file the user added to the mod folder stays, and so does its folder). A
listed path that would leave the mod folder is refused.

- MO2 (`install.mo2_uninstall(mo2, names)`): the mod folder's listed files, then that mod's line in
  `modlist.txt` and its plugins' lines in `plugins.txt` and `loadorder.txt`, nothing else. MO2's virtual
  file system deploys nothing into the game's Data folder (it overlays the mods folder only while a program
  runs through it), so with MO2 closed this is the whole uninstall, deployed or not.
- Vortex: when the staging folder is Vortex's own, `install.vortex_uninstall` first takes the mod's deployed
  files out of the game's Data folder ([vortex-deployed-uninstall](#vortex-deployed-uninstall)), then removes
  the staging files and unregisters the mod. A folder picked in Setup that Vortex does not use only loses the
  files.

MO2 is found from the folder saved in Setup, else beside the `nxm:` handler MO2 registers, with the
instance in its own folder (portable) or `%LOCALAPPDATA%\ModOrganizer\<instance>` whose `gameName` is
Skyrim Special Edition.

## <a id="install-summaries"></a>The Install list's saved file summaries

Each Install & play row needs its mod's file count, size and fingerprint (`install_mods.summarize`) to say
"installed" or "update". Walking every file of every mod took 5 s at startup on the developer's machine
(Oblivion, Tamriel Data and Morrowind_ob hold over 100,000 loose files each; LOD stays loose by design), so
`install_mods.summary` saves the result in the mod folder (`output_layout.SUMMARY_NAME`, never installed)
and reads that back: 0.08 s for 29 mods. Whatever changes a folder drops its summary
(`output_layout.forget_summary`): `convert.py` for every plugin it runs, at the start and again at the end
(so a list read mid-run is not kept), and the shared Morroblivion Hybrid Assets folder too when a hybrid
plugin is in the run (a hybrid plugin converts what it borrows into that folder; without this, converting
Arktwend left the shared mod's summary a day old and it read "Update" against a newer install);
`create_lod.py` for the LOD folder; Masters (`make_master.set_flag`)
for the folder of each plugin whose ESM flag it changes. That last one was missing: Masters writes the flag
into the plugin in place, so its mod kept its old summary and read "Installed" after the flag changed. As a
backstop for any tool that edits a plugin in place (and for summaries already stale on users' machines), a
saved summary older than a plugin at its mod's root is ignored and the folder walked again
(`install_mods._plugin_newer`: one directory listing per mod). A shared mod's folder is rebuilt whole by `write_mod_folder`, so it never carries an old one. A folder without
a summary is walked once.

An install reads each ticked mod's files fresh, and the marker it writes records their summary. The same read
is saved as the mod's summary (`install_mods.save_summary`) and the list is reread afterwards, so a summary
that went stale for any reason can't make a just-installed mod read "Update".

#### <a id="install-ticks"></a>Which rows start ticked

A row is ticked when it newly has work to do: the first time it is listed, or whenever its status or its
files' summary differs from the last time the list was read, while the status is New, Update, No zip or
Old zip. Ticking only on first sight left an updated mod unticked: it had been listed before as Installed,
so turning into Update never ticked it. A row the user unticks stays unticked until it changes again.
A finished install unticks the mods it placed. The rail's "N to install" counts the same rows as the
Install N button (`InstallView._todo`): it used to count rows whose status needed work while the button
counted ticked rows, and with nothing unticked after an install the two drifted apart (the button still
said "Install 4" after installing them).

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
`{USERDATA}` expanded. Vortex holds the `.log` open while it runs, so a running Vortex's newest
change may not be readable yet; the `.ldb` tables always are. A folder chosen in Setup overrides it.

Vortex lists a mod only when `persistent.mods.skyrimse.<id>` exists in that database; a folder dropped into
staging is ignored. Writing LevelDB ourselves is not an option, so `install.vortex_register` uses Vortex's
own command line (`Vortex.exe -s <path>=<json>`, read from its `main.cjs`: it opens the state, sets the
keys and quits), one leaf per field (`id`, `installationPath`, `state` = installed, `type`,
`attributes.name`, `attributes.logicalFileName`, `attributes.installTime`), mod-name dots escaped as `\.`
because its path parser splits on dots, `ELECTRON_RUN_AS_NODE` removed from the environment, a hundred
arguments per start. Uninstall uses `--del`. Vortex must be closed (it holds the database). Registration
happens only when the staging folder is the one Vortex itself uses; a folder picked in Setup just receives
the files. Unverified against a live Vortex: the argument shapes are unit-tested, but no run was made
against a real state (a test run against a temporary user-data folder opened Vortex on the real profile, so
it was stopped); the first real install is the check. The user enables and deploys the mods in Vortex.

## <a id="vortex-deployed-uninstall"></a>Uninstalling a mod Vortex has deployed

Vortex deploys by placing each enabled mod's files in the game's Data folder (hardlinks by default) and
recording them. Removing only our staging folder and the state entry would leave those files in Data, and
on its next start Vortex would report each one's source as deleted. So `vortex_deploy.undeploy` does what
Vortex's own purge does for that one mod, with Vortex closed. Everything below was read from Vortex's
source (github.com/Nexus-Mods/Vortex, master, read 2026-11-24), not guessed:

- **The record.** `src/renderer/src/extensions/mod_management/util/activationStore.ts`, `manifestPaths`:
  the default mod type's manifest is `vortex.deployment.json` in the deployment target, with copies in the
  staging folder: `vortex.deployment.msgpack` (written by `saveActivation` whenever files are listed, removed
  when none are) and a transient JSON copy that `saveActivation` deletes after writing the main file.
  `IDeploymentManifest.ts`: `{version, instance, deploymentMethod, deploymentTime, stagingPath, gameId,
  targetPath, files}`; `IDeploymentMethod.ts`, `IDeployedFile`: `relPath`, `source` (the mod's staging folder
  name), `merged` (the mods a merged file came from), `target` (empty when mods share one folder, as Skyrim's
  do), `time` (the deployed file's mtime in ms). `saveActivation` writes `JSON.stringify(data, undefined, 2)`
  and, when `files` is empty, deletes both the manifest and the msgpack copy. The JSON is written the same
  way here, and the msgpack copy with a small encoder (`vortex_deploy.msgpack`), so a later "Restore from
  backup" (`getManifestImpl`) never brings our entries back.
- **Which Data folder.** Vortex's state key `settings###gameMode###discovered###skyrimse###path` (the
  `discovered` selector in `gamemode_management/selectors.ts`) plus `Data`, falling back to Setup's Skyrim
  Data folder. The manifest is used only when its `gameId` is `skyrimse` and its `stagingPath` is the
  staging folder we installed into.
- **Is the file still ours.** A file is removed only when it is still the one Vortex placed from our
  staging copy, per `deploymentMethod`: `hardlink_activator`, the same file as the staging file (its
  `isLink` compares file ids); `symlink_activator` / `symlink_activator_elevated`, a link resolving to the
  staging file; `move_activator`, the staging `<file>.vortex_lnk` names this Data path and the mtime is the
  entry's within 1 s (`isDeployedFile`). Any other method raises before anything changes, asking the user
  to purge in Vortex. A file the user replaced in Data is left there and its entry dropped, which is what
  Vortex's purge does (`purgeDeployedFiles` skips a changed file, `filesStillDeployed` drops its entry); the
  done notice names it.
- **Game files Vortex moved aside.** `LinkingDeployment.ts`: deploying over a file it does not own renames
  it to `<file>.vortex_backup` (`backUpGameFile`), and removing a deployed file renames it back
  (`removeDeployedFile(..., restoreBackup=true)`). Done here too.
- **Conflicts.** The manifest holds one entry per path: the winner's. When our mod won a file another mod
  also has, Vortex puts the other copy in place only at its next deploy (the file is not in the manifest,
  so `LinkingDeployment` treats it as added). We do not copy it ourselves (we cannot know that mod is
  enabled or that it would win over a third). Instead `vortex_deploy.shadowed` looks for the removed paths
  in the other mod folders in staging (entering only the folders our files are in); when any is found, or
  an entry's `merged` list names our mod, the uninstall sets `persistent.deployment.needToDeploy.skyrimse`
  (the `setDeploymentNecessary` reducer in `reducers/deployment.ts`, registered at
  `persistent.deployment`) through Vortex's command line, and the done notice says to open Vortex and
  deploy, naming the count and one file. Without such a path the notice says nothing.

Not done: the folders our files emptied in Data stay (Vortex removes empty folders it tagged on its own
next deploy), and the plugin's line in Vortex's `plugins.txt` stays until Vortex rewrites it (the game
skips a missing plugin). Unverified against a live Vortex: the tests use a fake Data folder, staging and
manifest; Vortex is never started by them.

## <a id="ui-walk"></a>Testing the whole window: `tools/validate/gui_walk.py`

The walk starts the GUI (from source, or the built exe with `--exe`) and uses it the way a person does:
it finds each label through Windows UI Automation (Slint publishes every text it draws, with its
place) and clicks it with posted window messages, so the user's mouse and focus are never taken. Every
step names the labels it expects afterwards; a step fails when they do not appear within
`STEP_WAIT`, when an error label shows (`ERROR_LABELS`), or when the app has gone. A picture of the
window is saved after every step. `--runs` adds steps that start real conversions from the window,
which is the only way to exercise what a frozen build does differently: pool workers, tools and
handles (the WinError 6 in creature workers was invisible to every source test). Destructive buttons
(Remove from list, Clear) are opened and then cancelled.

The Install & play steps never reach a real mod manager: `make_fakes` points the settings at
`temp/gui_walk_fakes/` (a staging folder for Vortex, a portable MO2 instance whose `ModOrganizer.exe` is an
empty file nobody starts) and the app's `%APPDATA%` at an empty folder there, so Vortex's real state is never
read, our staging is never Vortex's own, and no Vortex command line or Data folder is touched. The settings
file is restored and the fakes deleted when the walk ends. `install switch stays put` clicks each way in
turn and requires the switch's segments, "Install with", Select all and Select none at the same pixels each
time; the per-row Uninstall is walked for both Vortex and MO2 (one mod removed, the other still installed,
the MO2 profile listing only the rest).

## <a id="folder-build"></a>TESRACT: a compiled folder

`tools/release/build_exe.py` compiles `gui.py` and everything it reaches with Nuitka (`--standalone`)
into `build/exe/TESRACT/`:

```
TESRACT/
  TESRACT.exe          the launcher (native/src/launcher; static runtime, no DLLs beside it)
  tesract_files.txt    every program file, for the updater
  app/                 the compiled app: TESRACT.exe, Python's and the packages' DLLs, data files,
                       splash.bin and data_root.txt
  export/ output/ logs/ conversion_config.json    the player's data, made on first use
```

Python's DLL must sit beside the exe that loads it, so the compiled app cannot move its own 59 top-level
entries into a subfolder; the launcher does instead. A release is that folder zipped (`--zip` writes
`build/exe/TESRACT.zip`, entries under `TESRACT/`). Nuitka turns the Python into C, so pure-Python work
(record export, NIF parsing) runs faster than under CPython; numpy and scipy work is unchanged.

- **Why a folder, not one file**: Windows loads a DLL or `.pyd` only from a file on disk and starts a
  bundled tool (ffmpeg, hkxcmd, LODGen, BSArch, the Papyrus compiler) only from a file, so a one-file
  build (`--onefile`, used first) must unpack everything (350 MB) before Python even starts. Measured
  2026-11-23: 14.2 s of nothing on screen at a version's first start, 6.6 s in a fresh folder; later
  starts reused the unpacked copy. A folder has nothing to unpack: the user's unzip is the only copy.
- **What is inside**: the compiled code, Python itself, and the files the app reads, listed in
  `build_exe.SHIPPED` (bundled tools, generated tables, runtime DLLs and inis, navmesh pins, the start
  mod's script sources, the window's .slint files, banner and icon), staged into `build/exe/data`. It
  is a list of what ships, not of what to skip: shipping every tracked non-source file put the test
  harness's `game_bridge/` DLLs and 271 `tes_runtime/*/testdata` files in the release.
- **Where it writes**: `output_layout.DATA_ROOT` is the folder `app/data_root.txt` names, relative to
  the app's TESRACT.exe (`..` in a release: the TESRACT folder itself), so `export/`, `output/`,
  `logs/`, `conversion_config.json` and a located `xWMAEncode.exe` (`external/xwmaencode/`) sit beside
  the launcher and survive a new version. The exe's folder is `sys.executable`'s, not Nuitka's
  `__compiled__.containing_dir`, which in a standalone build is the folder ABOVE the program folder (a
  build once wrote its data there). The first process puts the folder in `TESRACT_DATA_ROOT`, which the
  processes it starts inherit. From source, `DATA_ROOT` is the checkout.
- **Testing the build on the checkout's folders**: a development build's `data_root.txt` names the
  checkout (`--data-root`, default the checkout), so `build/exe/TESRACT/TESRACT.exe` shares `export/`,
  `output/`, `logs/`, `conversion_config.json` and `.conversion_state.json` with `python gui.py`. The
  release zip always writes `..` instead. (Links were tried first: the state file is saved by
  `os.replace`, which swaps a linked file for a plain copy, and a junction inside `build/` would let a
  cleanup of that folder delete the real `export/` or `output/`.)
- **Its version**: the build stamps `app/VERSION` with `version.current_version()`: a bare tag for a
  release, `<tag>+g<sha>` (`version.BUILD_STAMP`) for a development build. The compiled app has no
  `.git` to ask, so `_read_version_file` accepts both. It once accepted only a bare tag, so a
  development build read as `0.0-dev`: every upgrade plan compared against 0.0, About showed `0.0-dev`,
  and Choose said "Nothing to do" with FalloutNV.esm ticked and out of date (only the packing steps,
  off by default, survived the comparison).
- **Starting another program**: there is no `python.exe` and no `.py` file to run, so every place that
  starts a script calls `core.python_cmd.python_cmd(script, *args)`: from source `python -u <script>`,
  compiled `app/TESRACT.exe --tesract-run <module>` (`sys.executable`, never the launcher). `gui.main` hands that flag to `run_from_argv`, which
  imports the module and exits with its `main()` (Nuitka's compiled modules cannot be re-run as
  `__main__`), with line-buffered output (the Convert screen reads it line by line). A module started
  this way must be in `build_exe.RUN_MODULES`.
- **Measured** (2026-11-23, one-file build, Nehrim.esm, 32-thread machine, each run writing to its own
  scratch folder): record export 9.6 s from source, 3.8 s compiled (2.5x; the formatting/write part
  8.4 s to 2.3 s); extract + meshes 779 s from source, 749 s compiled (4%: that stage's time is numpy,
  the native extension and disk). Both outputs are byte-identical (65 export files, 12,649 NIFs). A
  build takes about 33 minutes cold and 8-12 minutes with Nuitka's compile cache warm.
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

## <a id="updates"></a>Updating TESRACT

Setup's Update (when a newer release tag exists, `version.check_for_update`) downloads the release's
`TESRACT.zip` asset into `.update/` in the TESRACT folder and unpacks it to `.update/new`
(`core/gui/exe_update.py`). Windows will not replace a running program's files, so the new copy
finishes the job: "Restart now" starts `.update/new/app/TESRACT.exe --tesract-run core.gui.exe_update
finish <folder> <pid>` and closes the window; that process waits for the old one to exit, deletes
exactly the files the old `tesract_files.txt` lists (and the folders that leaves empty), copies the new
folder in and starts the updated TESRACT.exe, whose start deletes `.update/`. "Later" does the same
when the window closes, without starting TESRACT again.

Nothing the player made is touched: export, output, logs, settings, `.conversion_state.json` and a
located xWMAEncode.exe are never in the manifest, so after the update the plugin list marks exactly the
steps the new version changed (`version.upgrade_plan`), as it does for a source update. Run from
source, Update opens the release page.

Every release must carry a `TESRACT.zip` asset built by `tools/release/build_exe.py --zip`;
a release without one makes Update fail with "Release <tag> has no TESRACT.zip".
