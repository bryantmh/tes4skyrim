# Pipeline Reference — orchestration, caching, layout, export format

Linked from [CLAUDE.md](../../CLAUDE.md). Everything about *running* the
conversion and the shape of the data that moves between stages. For per-tool
command lines see [python_tools.md](python_tools.md).

## Orchestrator

`convert.py` at the repo root drives every stage, reading the file list from
`conversion_config.json` in dependency order. Masters are auto-detected from
the TES4 binary headers; game data paths come from the Windows registry, or
from `conversion_config.json` on any OS (see
[Running off Windows](#running-off-windows) below).

```bash
python convert.py                          # full pipeline
python convert.py -f Oblivion.esm          # single file, all stages
python convert.py -f Oblivion.esm --export-only
python convert.py -f Oblivion.esm --no-cache      # force re-export
python convert.py -f Oblivion.esm --scripts-only  # Papyrus only
python gui.py                              # GUI front-end
```

Each stage has a `--<step>-only` flag. The steps are: `export`, `import`,
`extract`, `meshes`, `speedtrees`, `sounds`, `scripts`, `lod`,
`modify-body-meshes`, `pack`, `pack-zip`. Read `convert.py`'s module docstring
for the authoritative list — it changes more often than this doc.

### 🛑 LOD is NOT built with `convert.py --lod-only`

**`tools/release/create_lod.py` is the entry point for all LOD generation.** A
worldspace is baked ONCE from the plugin that owns it plus every other plugin
as an overlay, so `-f <plugin> --lod-only` bakes the wrong content: it misses
the overlays that supply most of the worldspace's objects.

```bash
python tools/release/create_lod.py --worldspaces WrldMorrowind --dry-run
python tools/release/create_lod.py --worldspaces WrldMorrowind
```

**Always `--dry-run` first and read the plan** — it names the owner and every
overlay, which is the only confirmation that the target is the one you meant.
`WrldMorrowind` is owned by `Morrowind_ob.esm` with `Tamriel_Data.esm` and
`TR_Mainland.esm` overlaid; baking `Morrowind_ob.esm` alone drops all Tamriel
Rebuilt content. `--worldspaces` only ever REMOVES work, so it is a scope, not
a filter on what is buildable. Full behaviour:
[python_tools.md](python_tools.md#terrain--lod--world).

### Asset-only mods are pseudo-plugins — they still take `-f`

A mod with no ESP/ESM (texture/mesh replacer, resource pack) is a legitimate
`-f` target. `--import-mod` registers it under its **mod name**, and its asset
stages run exactly like any other plugin's — the same `export/<name>/` and
`output/<name>/` layout:

```bash
python convert.py --import-mod <archive|folder>   # register it
python convert.py --list-mods                     # names + what each ships
python convert.py -f "Tamriel Landscape Pack" --speedtrees-only
```

Only the record stages (`export`/`import`/`scripts`/`creatures`) are skipped.
There is no binary for them to read, so those files are dropped from the
plugin-only phases up front (`source_paths.is_asset_only`) rather than left to
fail one by one on a missing source; the asset phases still run normally.
After `--import-mod`, the tool prints the exact command with the applicable
flags already filled in. **Never conclude that an asset has "no plugin to
build" because no ESP references it** — check `--list-mods` first.

### <a id="-f-takes-the-plugin"></a>🛑 A mod WITH a plugin takes the plugin, never the mod name

The mod-name form above is only for asset-only mods. A mod that ships a plugin
is converted by that plugin's filename: `-f TR_Mainland.esm`, never
`-f "Tamriel Rebuilt 25.08.12"`. The mod name used to be accepted and silently
build the wrong thing. `splitext` read `.12` as an extension, so the import
staged a second MorrowindRuntime sidecar, `Tamriel Rebuilt 25.08/`, with every
row naming a plugin that does not exist. The runtime keeps the first row per id,
so that folder shadowed the real one and the journal and follower ids resolved
to nothing in game.

`convert.py` now refuses a `-f` name that matches an imported mod's folder or
label when the mod ships plugins, and names the plugins to use instead
(`source_registry.mod_plugins`). `python convert.py --list-mods` lists every
mod with its plugins.

### <a id="plugin-source-resolution"></a>Where a plugin's binary comes from

**Code:** `convert.py` `resolve_plugin_path`

`-f <plugin>` names a plugin, not a path. Three sources are tried in order:

1. **An imported mod's retained binary** — `export/<plugin>/_source/<plugin>`,
   written by `mod_ingest.py` when the plugin came from a mod archive.
2. **The selected Data directory** (`tes4_data` = `tes4DataPath`, which the
   GUI rewrites whenever a directory source is picked), if it holds the plugin.
3. **The first registered game directory that holds it** — `source_registry`
   records each source Data folder as `kind: "directory"` (Oblivion, Nehrim,
   Fallout New Vegas), and `directory_for()` maps a plugin name back to a
   folder. This is how a master living in another install is found.

Step 2 must come before step 3 because plugin NAMES are not unique across
installs: Tale of Two Wastelands puts its own `Fallout3.esm` (masters
FalloutNV.esm + the FNV DLCs) in the New Vegas Data folder. With the registry
consulted first, picking the "Fallout 3 goty" source still exported the TTW
copy whenever New Vegas was registered earlier.

Step 3 is what lets a second or third game convert at all. Without it every
plugin resolves against the *default* Data directory, so `-f FalloutNV.esm`
looked for `FalloutNV.esm` inside the Oblivion folder and failed with
"Source file not found" — even though the registry held the correct path all
along. Nehrim worked only because its export tree already existed.

A registry that is missing or broken must never break a default-directory
conversion, so every registry lookup here is inside one `try` that falls
through to step 3.

### <a id="game-data-path-detection"></a>Detecting a game's own Data path

`find_game_path` checks the **config key first** (`tes4DataPath` /
`tes5DataPath`), then the Windows registry. Config wins deliberately: a
non-Windows host has no `winreg` at all, and a Windows user whose registry
entry points at the wrong install needs a way to override it. Both keys are
empty by default, so on a normal Windows box this changes nothing.

### Unreferenced textures are dropped at PACK time, never deleted

Oblivion's BSAs carry textures for content the conversion never emits, so the
textures archive is filtered against `texture_prune.build_refs` as `bsa_pack`
stages it. On Oblivion that is 26,099 files on disk → 13,492 packed (3.5 GB →
2.7 GB). **`output/<plugin>/textures/` always keeps the full tree**, so
loose-file testing is unaffected and re-packing is idempotent.

This used to be its own phase (11a) that unlinked from `output/`, which was
wrong twice over and is why a broken keep-set went unnoticed for months:

- The **meshes** phase re-copies the *entire* extracted texture tree into
  `output/` on every run (`asset_pipeline._copy_tree`, no incremental check),
  so the deletions were silently undone before anyone looked. A wrong keep-set
  only ever showed up *inside the BSA*, never as a missing file on disk.
- The user tests with **loose files**, so deleting from `output/` removed the
  very assets under test.

Corollary: **texture mtimes under `output/` prove nothing** about the keep-set
— `copy2` preserves the extract cache's timestamps, so files can look
untouched-since-extraction while having been deleted and restored repeatedly.

If the mesh manifest is missing, `build_refs` raises and packing ships every
texture rather than guessing — a missing mesh pass must never silently strip
textures that are in use.

**Both texture scanners were quadratic.** A `[...]{3,200}?\.dds` pattern opens
with a lazy star, so on a blob with no match the engine retries at *every*
offset. Two copies existed:

| Scanner | Fix | Result |
|---|---|---|
| `refs_from_records` (export text) | `'.dds' not in body` substring reject — `LAND.txt` is 1.47 GB with zero `.dds` | minutes → 4.2 s |
| `refs_from_assets` + `nif_converter._harvest_texture_bytes` (binary) | `texture_refs_in`: `bytes.find` each `.dds`, walk back over legal bytes | 13.8x, `build_refs` 87.6 s → 11.5 s |

The binary case could not use a substring reject — every `.bto` really does
contain `.dds`. Watch the bound when touching `texture_refs_in`: the old
`{3,200}` counted the run *before* `.dds`, so a whole match reaches **204**
bytes; capping the match at 200 silently truncates the longest paths.
Equivalence with the original regex is pinned by `TestBinaryTextureScan`.

Direct module entry points:

```bash
python -m tes4_export.export "C:/path/to/Oblivion.esm" --outdir export/Oblivion.esm
python -m tes4_export.export "C:/path/to/Oblivion.esm" --list-types
python -m tes5_import export/Oblivion.esm -o output/Oblivion.esm -m Skyrim.esm
python -m pytest tests/test_import.py -v          # targeted tests only
```

### Configuration

```json
{ "files": ["Oblivion.esm", "Knights.esp"] }
```

`tes4DataPath` / `tes5DataPath` (Oblivion's and Skyrim SE's `Data` folders)
and `bsarchPath` (an explicit BSArch.exe location) are also read from here —
see [Running off Windows](#running-off-windows) for why these matter more on
Linux/Mac than on Windows.

<a id="the-config-file-is-per-install"></a>
#### The config file is per-install, and untracked

`conversion_config.json` is the only file the running application mutates: the
GUI writes it whenever a setting changes (data paths, output directory, worker
count, navmesh-cache toggle, collision winding, LOD detail, Morrowind source).
It is therefore **gitignored** — committing it would ship one machine's paths to
every user, and any updater that overwrote it would wipe the real ones.

**Every key has an in-code default, so the file is optional.** `DEFAULT_CONFIG`
in `core/gui/config.py` holds the shipped values and the `//`-prefixed comment
keys that make a hand-edited file self-describing; the GUI writes it on first
run if nothing exists. Nothing depends on that write — `load_config()` reads an
absent file as `{}`, and each consumer falls back on its own constant
(`run_log.DEFAULT_RUNS_KEPT`, `mesh_decimate.LOD_DETAIL_DEFAULT`,
`morrowind.source_default()`, registry auto-detection for the data paths).

An explicit `--config PATH` is the one exception: a named file that does not
exist raises, because that is a typo rather than a fresh install.

<a id="run-logs"></a>
### Run logs

Every run writes its console output to its own file in `logs/`, so the record
of a run survives closing the GUI (whose scrollback was previously the only
copy) and starting the next one. Each name carries the start time and the
plugin the run converted:

```
logs/run-20260911-143002-Oblivion.esm.log
logs/run-20260911-101755-Nehrim.esm.log
logs/run-20260908-224410-global.log        # a run with no plugin selected
```

The timestamp leads so a plain lexical sort is chronological, and the plugin is
in the name so the log for the build you just played is identifiable without
opening every file. Two runs starting inside the same second get a `-2` suffix
rather than one overwriting the other. `logRunsKept` in
`conversion_config.json` sets how many are kept (default 20); `0` disables run
logging entirely. A new run deletes only the surplus oldest, so an existing
log's name never changes under a reader. Tools ▸ Open Logs Folder opens the
directory, and each run prints its own log path into the log.
`TESCONV_LOGS_DIR` overrides the directory (the test suite uses it so it never
writes beside a live run).

**The run's OWNER opens the log, never each process.** A GUI run is usually
several `convert.py` invocations (one per step), so opening one per process
would leave the retained set holding the last N *steps* of one run. The GUI
opens one log, writes every line through its own sink, and sets
`TESCONV_RUN_LOG` in the child environment; a child seeing that variable
neither prunes nor writes, so the file has exactly one writer. A bare
`python convert.py` sees no such variable, so there the process is the run and
it opens one for itself.

Lines are flushed as they are written — a log that only reaches disk on clean
exit is empty exactly when it matters most. A run that is killed or hangs
therefore keeps its output and simply has no `# Finished:` footer, which is
itself the signal that it did not terminate cleanly. Logging never fails a
conversion: every filesystem step degrades to "no logging" rather than raising.

<a id="running-off-windows"></a>
### Running off Windows (Linux / Mac, via Wine)

The pipeline runs off Windows. Two things differ from a Windows setup; nothing
else does — every bundled tool, code path and config key behaves identically
either way.

1. **Install Wine.** `subprocess_flags.windows_cmd()` transparently prepends
   `wine` to every invocation of a bundled `.exe` (BSArch, hkxcmd, the papyrus
   compiler, LODGen, the Havok mopp bridge) when not on Windows — callers never
   branch on platform themselves. Verified by hand under Wine 11.0: all five
   run correctly with ordinary Linux paths as arguments, no prefix or drive
   mapping needed for any of them, with ONE exception -- `hkxcmd.exe` parses
   its own argv and treats a leading `/` as a switch prefix, silently
   swallowing an absolute POSIX path as an unrecognised flag. Every call site
   already routes through `asset_convert/havok/hkx_xml.py`'s `to_hkxcmd_path()`,
   which prefixes Wine's `Z:` drive and swaps in backslashes for that one tool
   only — nothing else needs it, and this is transparent to callers.

   `xWMAEncode.exe` and `LipGenerator.exe` are not redistributable and are not
   verified under Wine in this repo (no copy was available to test with), but
   they're wrapped through the same `windows_cmd()` path and should work the
   same way once placed in `external/xwmaencode/` / `external/lipgen/`.
   `ffmpeg` is unaffected — it ships native Linux/Mac builds, so
   `find_ffmpeg()` resolves the system binary directly and `windows_cmd()`
   no-ops for it (it only ever wraps a literal `.exe`).

   `preflight.py` reports a missing `wine` the same way it reports a missing
   `.exe` -- as a blocking dependency for whichever phase needs it.

2. **`winreg`-based game-path auto-detection is Windows-only**, so
   `conversion_config.json`'s `tes4DataPath` / `tes5DataPath` are the
   equivalent everywhere else: set them to Oblivion's and Skyrim SE's `Data`
   folders and every phase that would otherwise consult the registry
   (script compilation's header lookup, `asset_convert/sources/skyrim_assets.py`'s
   vanilla-asset lookup, `preflight.py`'s checks, …) picks it up. This is
   checked FIRST everywhere, so it also works as a registry override on
   Windows if the registry ever points at the wrong install; left blank (the
   default) it changes nothing there.

3. **The compiled navmesh extension** (`native/dist/_navgrow_native*`) is a
   `.pyd` on Windows and a `.so` elsewhere, selected automatically by Python's
   own `EXT_SUFFIX` (`tes5_import/navmesh/_native_loader.py` already handles
   this — no code differs). Only the Windows `.pyd` ships in the repo; build
   the local one with:

   ```bash
   python native/build.py
   ```

   `native/build.py` looks for `g++`/`clang++`/`c++` on PATH off Windows (MSVC
   via `vswhere` on Windows, unchanged). `native/src/grow.cpp` is portable
   C++17 against only `Python.h` and numpy's C API, so it compiles unmodified
   either way — verified by building and running it through
   `tests/test_pgrd_navm.py`'s native-extension tests (grow_strips/levels_at,
   including the guard-rail cases) under g++ 14 on Linux.

### Caching

- Export text is cached per record type in `export/<filename>/` (`ACTI.txt`,
  `NPC_.txt`, …).
- Processing `Knights.esp` reuses the cached `Oblivion.esm` export.
- `--no-cache` forces a re-export.

### Versioned stage artifacts

Some stages hand data to the import stage through JSON on disk. Those files
outlive the run that wrote them, and — when a plugin has a TES4 master — they
outlive the PLUGIN that wrote them: a dependent plugin reads its master's
`creature_projects.json` and never rewrites it. So a file on disk can predate
the code reading it by any amount.

Registered artifacts carry an envelope (`tes5_import/base/artifact_schema.py`):

```json
{"version": 1, "plugin": "Oblivion.esm", "stage": "--creatures-only",
 "data": {...}}
```

`plugin` and `stage` are what make a stale file's error ACTIONABLE — the
consumer can infer neither (the file may belong to a master, and only the
producer knows which flag rebuilds it). Reading a stale file raises
`StaleArtifactError`, which `convert.py` prints as an `ERROR:` naming the
plugin to re-run and the exact command.

| Artifact | Rebuilt by |
|---|---|
| `creature_projects.json` | `--creatures-only` |
| `music_tracks.json` | `--sounds-only` |

**Adding a required field to one of these means bumping its `version` AND the
frozen digest in `tests/test_artifact_schema.py`, in the same commit.** That
test fails when a `required` set changes without a version bump — the slip that
caused `KeyError: 'behavior_hkx'` on every plugin with a master after a4fdb47
added the field with no version to detect it. A second, runtime check rejects a
file missing a required key even at a matching version, as the backstop for
when the test is bypassed.

A file with no `version` key reads as v0, so every artifact written before this
existed is rejected cleanly rather than half-read. Users upgrading past this
point hit the error once per stale artifact and re-run the named stage.

This does NOT apply to pure caches (mesh bounds, extracted-BSA bookkeeping,
voice durations) — a stale cache is discarded and recomputed, never fatal.
`collision_extract.BOUNDS_SCHEMA_VERSION` does that with an in-band
`__schema__` sentinel. `master_manifest.py` predates all of this and carries
its own equivalent envelope.

### Generated FormIDs are stable across builds

Every record the importer *invents* (ARMA, OTFT, NAVM, VTYP, SNDR, …) gets its
id from `writer.derive_formid(site, key)`, which hashes the SOURCE record's
authored identity. Adding, removing or reordering generated records leaves
every other id untouched, so an existing save stays valid across a rebuild.

Two things still renumber a plugin and require a new game:

- Changing the hash input, the derived region, or `FORMID_SCHEME_VERSION`.
- Changing which authored records exist (a different source plugin version).

Full contract: [performance_notes.md](../commentary/performance.md#formid-determinism--the-save-game-contract-rewritten-2026-08-17).

Historical note: this used to be a bare `+1` counter, and a shifted id made
`ErothinKampfmagier01` appear **naked** in an old save with the outfit,
armature and meshes all correct — a false alarm that cost a debugging cycle.
If you see that class of symptom now, it is NOT id drift; look elsewhere.

## Stages

1. **Export** (`tes4_export`) — reads the TES4 binary, writes KEY=VALUE text,
   one file per record type. Pure dump.
2. **Import** (`tes5_import`) — reads the text, applies every TES4→TES5
   transformation, writes the binary ESM/ESP. Type mapping (CREA→NPC_,
   CLOT→ARMO, LVLC→LVLN), FormID remapping, GRUP hierarchy (CELL/WRLD/DIAL),
   companion records (TXST for LTEX, SNDR for SOUN), LAND binary data.
3. **Assets** (`asset_convert`) — BSA extraction, NIF/texture/SpeedTree/sound
   conversion, LOD generation, BSA packing.

### The extract stage has two sources

`phase_extract` branches on `export/sources.json`:

- a plugin **imported from a mod archive** re-runs its ingest
  (`asset_convert/sources/mod_ingest.py::reingest`), unpacking from the archive copy
  retained under `export/<plugin>/_source/`;
- **everything else** extracts the BSAs beside it in the Oblivion Data
  directory, exactly as before.

Both produce the same `export/<plugin>/{meshes,textures,sound,trees,misc}`
tree, so no later stage knows which ran. A plugin's binary is located by
`convert.py::resolve_plugin_path` — the Data directory, or the imported copy —
and **every** path that used to be `os.path.join(tes4_data, name)` must go
through it. With no imported mods the registry is absent and behaviour is
byte-for-byte what it was (`tests/test_mod_ingest.py` asserts this).

See [mod_archive_ingest_plan.md](../commentary/asset_convert_mod_ingest.md) for the layout
rule, nesting, precedence and path-safety contract.

`import_main.py` runs a long phase sequence (Phase 0 pre-scans through Phase 5
dialogue). Ordering matters — e.g. PACK is written in its own Phase 3b2 after
QUST because quest packages need the aliases to exist first, and the ForceGreet
topic binding is patched in after Phase 5. Read the phase comments in
`import_main.py` rather than trusting a copy of the list here.

### Vanilla Papyrus headers: `Data/Scripts.zip` is unpacked automatically

Every converted script compiles against Bethesda's own `.psc` sources, passed
to the compiler as `-h`. The CK ships them one of three ways, and
`papyrus_compile.find_skyrim_source_scripts()` is the single lookup every caller
uses (the Scripts phase, `preflight._papyrus_headers`, `tools/script/ck_compile_check.py`,
`tools/script/compile_papyrus.py`) so the dependency check can never pass while the
phase then fails to find them:

1. `Data/Source/Scripts/` — the modern layout.
2. `Data/Scripts/Source/` — an older one. Note that on some installs this
   exists but is a *partial mirror* with no `Debug.psc`; every check tests for
   `Debug.psc`, not just `isdir`, so a partial mirror is correctly rejected.
3. **`Data/Scripts.zip` only.** Newer CK builds ship the sources solely as this
   archive and never unpack it, so an install with a perfectly good CK has no
   header directory at all. The pipeline extracts it in place (entries are
   already rooted at `Source/Scripts/`, so it lands exactly where the CK itself
   would have put it — where the CK and every other Papyrus tool on the machine
   already look). ~14,300 `.psc` plus `TESV_Papyrus_Flags.flg`, ~6 s, once.

Only `.psc`/`.flg` are taken (the archive also holds DialogueViews XML),
entries are flattened to basenames, and **existing files are never
overwritten** so a user's edited header survives. Preflight drives the lookup,
so the one-time extraction happens during the dependency check rather than in
the middle of the Scripts phase. A read-only install warns and falls through to
the normal "headers not found" Missing rather than crashing.

## Skipped record types

`SKIP_TYPES` in [tes5_import/base/constants.py](../../tes5_import/base/constants.py) is the
single source of truth. Currently skipped: ROAD, SCPT, SKIL, BSGN, RACE, MGEF,
CSTY, IDLE, GMST, EYES, HAIR.

Notably **converted** (do not assume otherwise): GLOB, CLAS, CLMT, WATR, PACK,
WTHR, REGN. PACK is converted in its own phase (3b2, after QUST) rather than
via the generic dispatch, and so is WTHR (Phase 2b — it mints four IMGS
companions per weather for HDR tone mapping, see
[weather_climate_conversion.md](../commentary/tes5_import_weather.md)). REGN is
converted for its **weather** entries only (RDWT lists + RPLI/RPLD areas);
its object/grass/sound/map generators stay dropped — that is where all of
Cyrodiil's weather variety lives, since TamrielClimate's own WLST is a single
Clear weather at 100%.
GMST is skipped wholesale *except* the four ambient-dialogue pacing settings in
`AMBIENT_GMST_OVERRIDES`, which exist in both engines with identical meaning.

Conditions whose params reference a skipped type must be translated (RACE →
Skyrim race via `RACE_MAP` in `dialog_conditions`) or dropped. A dangling param
can never pass, and the CK warns "Unable to find … TESForm in
TESConditionItem Parameter Init".

## Text export format

Records are delimited by `---RECORD_BEGIN---` / `---RECORD_END---`. Each line is
`KEY=VALUE`. Escapes: `\\`, `\"`, `\n`, `\r`, `\t`. `#` starts a comment.
FormIDs are 8-digit hex in load-order form. Arrays use indexed keys
(`Item[0].FormID`, `Item[0].Count`). `Signature=` carries the original TES4
record type; there are no derived or transformation fields.

```
---RECORD_BEGIN---
Signature=CREA
FormID=00000E35
EditorID=TestDeerDoe
RecordFlags=0
ParentCELL=00012345
ParentWRLD=0000003C
FULL=Deer
Model.MODL=Creatures\\Deer\\Skeleton.NIF
---RECORD_END---
```

## Directory layout

```
TESConversion/
  convert.py              # pipeline orchestrator (all stages)
  gui.py / gui.pyw        # GUI launcher only -- the window is core/gui/
  conversion_config.json  # file list and settings

  core/                   # shared plumbing every stage imports
    run_log.py            # per-run logs (see Run logs)
    worker_budget.py      # parallel worker count
    subprocess_flags.py   # POPEN_FLAGS, windows_cmd, run_streamed
    process_job.py        # Win32 Job Object containment
    collision_options.py  # collision winding-repair toggle
    plugin_masters.py     # master lists from binary headers

    gui/                  # the converter window
      app.py              # GuiApp carrier, palette, OS chrome, assembly order
      config.py           # step tables, settings, scanners, run_process
      widgets.py          # dialogs, scroller, tooltips, plugin combobox
      menus.py            # the dark menu bar
      selection.py        # sources, step checkboxes, Upgrade, drop zone
      panels.py           # the five modal panels
      runner.py           # log, run state, commands, stamps, execution
      mods.py             # Mods menu
      morrowind.py        # Settings > Morrowind source

  logs/                   # run-<timestamp>-<plugin>.log (gitignored)

  tes4_export/            # TES4 binary -> KEY=VALUE text (pure dump)
    tes4_reader.py        # mmap-based binary reader
    export.py             # export CLI
    text_reader.py        # parse text exports back to dicts
    record_types/         # per-type field emitters
      common.py items.py equipment.py actors.py world.py dialog_misc.py

  tes5_import/            # text -> TES5 binary (all transformations)
    constants.py          # lookup tables, dispatch maps, SKIP_TYPES
    writer.py             # TES5 binary packing (records, groups, headers)
    import_main.py        # phase orchestrator
    pack_converter.py     # PACK -> TES5 template instances
    pgrd_to_navm.py       # PGRD -> NAVM
    navi_builder.py       # top-level NAVI
    navmesh/              # corridor navmesh generator
    overrides.py          # override conversion (plugins with masters)
    export_diff.py master_manifest.py override_builder.py override_merge.py
    record_types/         # per-type converters
      common.py items.py equipment.py actors.py world.py dialog_misc.py

  script_convert/         # TES4 script -> Papyrus
    converter.py pipeline.py cross_ref.py say_durations.py static_scripts/

  asset_convert/          # asset pipeline
    nif_converter.py      # NIF conversion (strips, textures, bones, collision)
    collision.py          # Havok rigid bodies, shapes, materials
    cms.py cms_builder.py mopp.py     # compressed mesh shapes + MOPP
    skin_retarget.py      # Oblivion Bip01 -> Skyrim NPC bones
    skyrim_overrides.py   # bone mapping, BSX flags, biped slots
    skyrim_assets.py      # vanilla asset fetch (cache / BSA auto-extract)
    sse_nif.py            # SSE NIF read -> LE graph bridge
    bsa_extract.py bsa_pack.py asset_pipeline.py
    spt_parser.py spt_generator.py spt_converter.py   # SpeedTree

  native/                 # C++ extensions (grow.cpp -> _navgrow_native)
  tes_runtime/            # TESRuntime.dll, the shipped SKSE plugin (animation cache + FNV guns/limbs)
  external/               # third-party binaries (see README license table)
  tests/ tools/ docs/ references/ temp/
  export/                 # cached exports (gitignored)
    <plugin>/             #   a Data-directory plugin: records + its assets
    <Mod Label>/          #   an IMPORTED mod: one folder for the whole archive
  output/                 # WORKING area (gitignored)
    <plugin>/             #   one folder per converted plugin...
      SKSE/Plugins/TESRuntime/           # <plugin>.bodyparts.json / .guns.json, loose (never in a BSA)
      SKSE/Plugins/TESRuntime/animation/ #   the plugin's animation cache fragment
      meshes/actors/character/           # FO3/FNV only: patched humanoid behaviors + animations/<game>guns/
    <Mod Label>/          #   ...or one per imported mod (mirrors export/)
    AutoConvertLOD/       #   the baked LOD mod (tools/release/create_lod.py)
    Finished Mods/        #   everything the user INSTALLS -- see below
```

### Imported mods share ONE folder per MOD

A mod archive holding several plugins ships ONE set of meshes/textures/sound/
trees that all of them draw on. Giving each plugin its own `export/<plugin>/`
meant extracting that payload once per plugin (or hard-linking it, which is the
same bytes wearing a disguise), and left the pipeline unable to tell "these
three read the same assets" from "these three are unrelated".

So an imported mod gets one folder, named for the mod, in BOTH roots:

```
export/Tamriel Resource Pack Full 2.0/
  _source/              every plugin binary + the retained archive
  meshes/ textures/ sound/ trees/ misc/        <- SHARED payload
  collision_cache.bin, mesh_bounds_cache.json,
  door_centers_cache.json, door_panel_axis_cache.json   <- SHARED, asset-keyed
  TamRes.esm/           <- THIS plugin's .txt dump + per-plugin caches
  TamRes.esp/              (navmesh_geom_cache, creature_projects.json,
  TamRes_LandscapeResource.esm/   voice_durations.json, animdata_base)

output/Tamriel Resource Pack Full 2.0/
  TamRes.esm  TamRes.esp  TamRes_LandscapeResource.esm   + their manifests
  meshes/ Textures/ scripts/ sound/ seq/
```

**Three plugins in, three plugins out** — only the asset payload is shared.
Records, manifests and the converted ESM/ESP stay per plugin.

Which caches are shared is a measured fact, not a guess:
`collision_extract.scan_mesh_data` keys its entries on the mesh's relative path
with no plugin identity, and the door/navmesh caches are located via
`os.path.dirname(collision_cache)`, so they follow it. Everything else derives
from a plugin's records and stays in that plugin's subfolder.

Never join a plugin name onto a root by hand. Three resolvers own this, and
export/ and output/ only agree because they all go through them:

| Need | Call |
|---|---|
| the shared assets | `source_registry.asset_root(export_dir, plugin)` |
| this plugin's records | `source_registry.record_dir(export_dir, plugin)` |
| this plugin's output | `output_layout.plugin_out_root(out_root, plugin, export_dir)` |

A plugin that is not an imported mod resolves to `<root>/<plugin>` from all
three, so the game-Data path is byte-for-byte unchanged.

Two consequences worth knowing:

* **The output scanners cannot use `<folder>/<folder>`.** A group folder is
  named for the mod, so `sibling_lod.converted_plugins` and `gui.scan_converted`
  find plugins by their `<plugin>.manifest.json` instead. Getting this wrong
  drops a plugin out of load-order resolution silently.
* **Removing one plugin must not delete the shared tree.** `mod_ingest.remove`
  deletes the payload only when the plugin being removed is the last one
  registered in its group.

`tools/esm/migrate_group_layout.py` moves mods imported under the old layout.

### `output/Finished Mods/`

`output/` is a workspace, not a delivery folder: per-plugin working folders, the
baked LOD mod, caches and manifests all live there and none of it is what the
user installs. The installable artefacts used to sit at the same level, mixed in,
so finding the handful of files that actually ship meant knowing which entries
were products and which were scaffolding.

Everything installable is collected in `output/Finished Mods/` instead:

| Artefact | Written by |
|---|---|
| `<plugin>.zip` | `convert.py --pack-zip-only` (`phase_pack_zip`) |
| `AutoConvertLOD.zip` | `tools/release/pack_lod.py` |
| `TESGameSelect.zip` | `tools/release/package_start_mod.py` |
| `Body Slots Patch.zip` | `convert.py --modify-body-meshes` (the patch plugin and the split skin meshes it points at; see [body slots](../commentary/asset_convert_armor.md#body-slot-layout)) |

The folder name lives in `output_layout.py` (`FINISHED_DIR_NAME`), spelled once
because it contains a space and is user-facing. Its `finished_dir(out_root)`
helper creates the folder on demand — a run that packages nothing leaves no empty
folder promising deliverables. **Read-only checks must use the constant, not the
helper**: `gui._global_artifact` only asks whether an artefact exists, and calling
the helper there would create the folder just by opening the GUI.

Nothing here is ever mistaken for a converted plugin: `sibling_lod.converted_plugins`
requires `<folder>/<folder>` to exist and `gui.scan_converted` requires a
manifest, and this folder satisfies neither.

## Verifying output in SSEEdit

```powershell
if (Test-Path SSEEdit_log.txt) { Remove-Item SSEEdit_log.txt -Force }
$tes5 = "C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data"
$tp = "temp_plugins.txt"
"*Skyrim.esm`n*Oblivion.esm" | Set-Content $tp -Encoding UTF8
$args = "-P:`"$tp`" -D:`"$tes5`" -autoload -IKnowWhatImDoing `"Oblivion.esm`""
Start-Process -FilePath ".\sseEdit.exe" -ArgumentList $args -WorkingDirectory (Get-Location).Path
```
