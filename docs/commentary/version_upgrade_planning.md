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
| `modify_body_meshes` ("Body Slot Patch") | Takes no `-f`. Patches the vanilla Skyrim body records for the user's whole load order and writes ONE shared `Body Slots Patch.zip` (the plugin plus the split skin meshes) into Finished Mods, not into any per-plugin folder. `main()` must not bail with "No files to process" when only this step is asked for (an end user's config has no `files` list, so it never ran and the GUI re-ticked it), and it is recorded once under the global key, since stamping it per plugin left every other plugin looking like it had never run. |
| `create_lod` | LOD tiles are files on a fixed grid shared by every plugin that edits a worldspace, so baking them per plugin generates the contested tiles once per sibling and then discards all but one. It reconciles SEVERAL plugins against each other. This is why LOD is no longer a numbered per-plugin step at all. |
| `pack_lod` | Inherits it: zips that one shared folder into one shared archive, so it is no more per-plugin than the bake it packages. |
| `make_master` ("Convert to Master") | The ESM flag has to be applied to a whole dependency CHAIN at once, since an ESM may not master a plain ESP. |
| `convert_ui` ("Convert Oblivion UI") | The purest case: `tools/misc/convert_ui.py` takes no plugin argument at all. It reads the Oblivion and Skyrim installs and writes one `Oblivion UI` zip. |

`make_master` and `convert_ui` were each absent from this set while listed in
`GLOBAL_ACTIONS`, and two tests in `tests/test_version_upgrade.py` assert the two
tables agree precisely because that divergence is invisible at runtime — the step
still works, it just never stops being offered.

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
