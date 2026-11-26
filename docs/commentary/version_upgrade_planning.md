# Upgrade planning: which steps a new release owes

**Code:** `version.py` — `GLOBAL_STEPS`, `GROUP_STEPS`, `upgrade_plan()`

The upgrade planner answers one question per step: *did this step run, at this
version, for this plugin?* The state file records a version per (key, step), and
the whole design is about choosing the right key.

## <a id="one-table-not-four"></a>One table, not four

The step list exists once, in `core/gui/config.py`: `STEPS` (per-plugin, each with
its `convert.py` flag, tooltip and default state) and `GLOBAL_ACTIONS` (one-off
jobs, each with its tooltip and sidebar button row). Everything else derives:

| Derived | From | Was |
|---|---|---|
| `version.STEP_KEYS` | the (key, label) columns of both | a hand-copied 16-row literal |
| `version.GLOBAL_STEPS` | the keys of `GLOBAL_ACTIONS` | a hand-copied frozenset |
| `release_notes.STEP_ORDER` | `[label for _, label in STEP_KEYS]` | a hand-copied 16-row literal |

Order matters — it must read left-to-right, top-to-bottom off the sidebar
buttons — and deriving it is what makes that automatic rather than asserted.

These were copies because `STEPS` and `GLOBAL_ACTIONS` used to live in `gui.py`,
which imports tkinter; `version.py` and `convert.py` are used headless and could
not pay for that. The tables now live in `core/gui/config.py`, which holds no
tkinter import at module scope, so the copies had no remaining justification.

`tests/test_version_upgrade.py` still asserts the three agree. The assertions are
now tautological, and kept deliberately: they are what fails if someone
re-introduces a literal.

## <a id="steps-that-belong-to-no-plugin"></a>Steps that belong to no single plugin

`GLOBAL_STEPS` is recorded under one plugin-independent key (`"*"`), because each
of its members produces ONE artifact for the whole load order rather than
per-plugin output.

Recording such a step per-plugin makes it re-tick forever. Patching while
converting Oblivion left Nehrim with no record of it, so the planner saw a step
that had never run for Nehrim and selected it again — for every plugin the user
had not happened to run it alongside, despite the one shared artifact already
existing on disk.

Why each member qualifies:

| Step | Why it belongs to no plugin |
|---|---|
| `modify_body_meshes` ("Body Slot Patch") | Takes no `-f`. Patches the vanilla Skyrim body records for the user's whole load order and writes ONE shared `Body Slots Patch` mod folder (the plugin plus the split skin meshes) in `output/`, not into any per-plugin folder. `main()` must not bail with "No files to process" when only this step is asked for (an end user's config has no `files` list, so it never ran and the GUI re-ticked it), and it is recorded once under the global key, since stamping it per plugin left every other plugin looking like it had never run. |
| `create_lod` | LOD tiles are files on a fixed grid shared by every plugin that edits a worldspace, so baking them per plugin generates the contested tiles once per sibling and then discards all but one. It reconciles SEVERAL plugins against each other. This is why LOD is no longer a numbered per-plugin step at all. |
| `pack_lod` | Inherits it: zips that one shared folder into one shared archive, so it is no more per-plugin than the bake it packages. |
| `make_master` ("Convert to Master") | The ESM flag has to be applied to a whole dependency CHAIN at once, since an ESM may not master a plain ESP. |
| `convert_ui` ("Convert Oblivion UI") | The purest case: `tools/misc/convert_ui.py` takes no plugin argument at all. It reads the Oblivion and Skyrim installs and writes one `Oblivion UI` mod folder. |

`make_master` and `convert_ui` were each absent from this set while listed in
`GLOBAL_ACTIONS`, and two tests in `tests/test_version_upgrade.py` assert the two
tables agree precisely because that divergence is invisible at runtime — the step
still works, it just never stops being offered.

## <a id="saved-table"></a>The release table is saved between starts

`steps_table()` reads each release's checklist from GitHub, which took about
0.9 s and held up the Choose screen at every start. Every read now saves the
table to `.release_steps.json` beside the state file. While that saved copy
already lists this build's own release, it is used without the network: a
release's checklist never changes once it is cut, and an upgrade plan only asks
about releases up to this build's. A build newer than the saved copy (or a
development build with no release of its own) still fetches, and saves again.

## <a id="what-a-release-owes"></a>What a release owes: `tools/release/release_notes.py`

The tag-on-push workflow writes each release's "Steps to re-run in the GUI"
checklist, and that checklist is the ONLY input the app's upgrade planner reads.
An over-wide checklist is not cosmetic noise: it tells every user to redo a
multi-hour reconversion. Measured over the 30 releases 0.642–0.671 with the
hunk-header attribution this replaced: 7 asked for all 17 steps, every one
because `convert.py` changed, and in every case the changed code was imports,
the module docstring, or run/dispatch helpers (`_run_pipeline`,
`_phase_runners`, `_run_steps`, `_mod_commands`, …) that no phase calls. 0.672
changed only `tes_runtime/` and a config helper and still asked for all 17; it
owed only Package SKSE Mod.

Three layers decide a path's cost, cheapest first.

### <a id="python-files"></a>1. A Python file that runs no differently costs nothing

`modified_units()` parses the file at both revisions and compares each top-level
unit (def, class, single-name assignment) by its AST with docstrings stripped.
So comments, docstrings, formatting and imports never count. A **brand-new**
unit does not count either: nothing calls it until a caller changes, and that
caller is a change of its own. Exceptions, which do count:

- a new **decorated** def (a decorator can register it with no caller);
- any change to loose top-level code that binds no name (`MODULE_UNIT`);
- a file that is new, deleted or unparseable at either end — nothing proves it
  unused, so its rule applies.

Name-based dispatch was checked before relying on "new = unused": the only
`getattr(module, name)` lookup in the stage packages
(`tes5_import/overrides/builder.py`) takes its name from a table, which is
itself a modified unit when an entry is added.

With the change filtered to real units, 0.652, 0.663 and 0.664's
`output_layout.py` edits (new helpers only) and this release's cost nothing.

### <a id="convert-py"></a>2. `convert.py` costs the phases that reach the change

`convert_py_steps()` builds `convert.py`'s call graph (which top-level names each
unit loads) and charges each `phase_*` in `PHASE_STEPS` whose reachable set
contains a modified unit. A modified unit no phase reaches — `main`,
`_run_pipeline`, the dispatch helpers — is orchestration: it decides which
phases run and where output goes, never what a phase writes, so it costs
nothing. This replaced a hand-kept list of orchestration functions, which had
fallen six helpers behind. Only a change to loose top-level code falls back to
every step.

It used to read git's `-U0` hunk headers instead. Those name the nearest
preceding function *line*, so an import hunk read as "not in a function" →
every step, and any helper not on the list did the same.

### <a id="the-rules"></a>3. `RULES`: path → steps

First match wins, so every narrow rule sits above the blanket rule that would
swallow it. The non-obvious entries:

- **Every LOD module feeds ONE step.** The whole load order's LOD, plus the
  sibling merge, comes from the single "Create LOD" action.
- `worldmap_clouds.py` is generated from both sides — per worldspace by Import
  (`record_types/world.py`) and as a merged union by the sibling pass.
- `skin_replacement.py` is imported by `nif_converter`, so it is a mesh change
  as well as a Body Slot Patch one. `skyrim_assets.py` feeds meshes, creature
  skeletons (`extract_skeleton_bones`) and the body patch.
- `asset_convert` patterns allow any folder depth; a one-level pattern dropped
  nested modules through to the mesh catch-all.
- `native/*.md|txt` sits above `^native/`: 0.57 charged a mesh, creature and
  LOD rebuild for `native/dist/README.md`.
- Five `tools/` scripts ARE global actions (`create_lod`, `pack_lod`,
  `package_start_mod`, `package_runtime_dll`, `tools/misc/convert_ui.py`)
  and sit above the blanket `^tools/` rule.
- `core/worker_budget|subprocess_flags|process_job` and `output_layout.py` imply
  every step: pool plumbing and the output layout feed every stage. A new
  helper in either costs nothing (layer 1); a modified one does.
- `core/run_log|plugin_masters` only report. The bare `^core/` tail reads a new
  module as a GUI change (no re-run) rather than leaving it unmapped.
- `tes_runtime/` and `TESGameSelect/` are committed prebuilt plugins no phase
  reads; they re-run only their own packaging action.
- `preflight.py`, `convert_cli.py`, `source_paths.py`, `requirements.txt`: they
  gate, parse or locate inputs; a fix there makes a failing run work but never
  changes what a working run wrote. `game_bridge/` is a live-debug tool,
  `navmesh_cache/` a README.
- Packaging: 9./10. are added whenever a per-plugin producing step fires, Pack
  LOD only with Create LOD; the standalone global actions pull in neither.

`tests/test_release_notes.py` fails when any tracked path matches no rule, so
a new top-level file is caught at PR time instead of surfacing as "Unmapped
paths" in a public announcement. When every step fires, the notes name the
paths that caused it.

## <a id="what-selects-convert-ui"></a>What selects "Convert Oblivion UI" in the release notes

`release_notes.RULES` maps a changed path to the steps it makes stale. Three
patterns feed this action, each listed BEFORE a blanket rule that would otherwise
swallow it (first match wins):

| Pattern | Beats |
|---|---|
| `tools/misc/convert_ui.py` | `^tools/` → `[]`, which would report that changing the tool re-runs nothing |
| `asset_convert/ui/(ui_menus\|ui_cursor\|swf).py` | `^asset_convert/` → `3. Meshes` |

`asset_convert/ui/book_inam.py` is deliberately excluded from the second pattern:
it is the per-plugin book-icon step, takes an `output_dir`/plugin pair, and never
imports `swf`. It falls through to Meshes, which is correct for it.
