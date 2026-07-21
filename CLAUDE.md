# TES4-to-TES5 Conversion Project — AI Context

## Project Goal

Convert TES4 (Oblivion) master/plugin files to TES5 (Skyrim) format.

1. **tes4_export** (Python) — Reads TES4 binary files directly and exports all records to KEY=VALUE text format. Pure dump — no transformations.
2. **tes5_import** (Python) — Reads the text export and writes a binary TES5 ESM/ESP. All TES4→TES5 transformations happen here.
3. **asset_convert** (Python) - Performs all mesh / speedtree conversion

## Critical Working Rules

-The export is a PURE dump of TES4 data. No type mapping (CREA→NPC_), no field derivation, no path prefixing. ALL transformations belong in the import script.
- You can access OG skyrim meshes for comparison in `references\Skyrim Meshes`. A skyrim esm dump for comparison is in `Rreferences\Skyrim.esm`. Real Skyrim .nif files for analysis/testing are in the `references` folder.
- **`references/` is for comparison/analysis ONLY — the pipeline must NEVER resolve runtime assets through it.** Any vanilla Skyrim file the conversion needs is fetched via `asset_convert/skyrim_assets.py` (cache in `export/skyrim_assets/`, else auto-extracted from the SSE BSAs via registry-detected install). BSA meshes are SSE-format; read them with `asset_convert/sse_nif.py` (`read_nif` accepts bytes or path, converts BSTriShape graphs to LE NiTriShape graphs in-memory — pyffi Patch 8 provides the SSE read layouts). Output is always written LE (uv2=83), which SSE loads natively.
- Only run NIF comparisons on a maximum of 10 NIFs at a time when single-threaded. Search for a folder structure and run comparisons on a deeply nested subfolder likely to have the attributes you want.
- **NEVER run tests on a large batch of NIFs without many workers** — it takes an excessively long time. When testing conversion with nif_converter, use the full number of workers (`cpu_count() - 1`).
- Compare the `output\` mesh with a few similar Skyrim meshes and the `export\` mesh.
- The goal is COMPLETE conversion. Avoid stripping things out even if the conversion process would be complicated.
- Do one bug at a time and make the necessary edits before moving onto the next bug. If you find another bug while investigating, feel free to fix it too.
- Reference `references/nif [version number].xml` to determine valid Skyrim NIF behavior — it's much newer/more correct than the version bundled with pyffi 2.2.3.
- Use Pyffi with a monkey patch for clock when analyzing.
- Files in `references/` subfolders (`NIFConverter/`, `xEdit/`, `UESP/`, `nifskope`) are from other projects, reference only. `xEdit/Core/` documents the binary structure of every record type. NifSkope source is also available for complex problems.
- **Always add new learnings to this file (or the linked docs/ file) for future reference.**
- Put throwaway temp files in the `temp` folder. Avoid one-off targeted-output scripts — build reusable `tools/` scripts with general outputs and arguments instead, so they're reusable for future investigations.
- Utilize multi-threading where possible.
- Don't worry about backwards compatibility. Remove old code if it's no longer used.
- Keep files under ~1000 lines where possible; split into multiple files with clear responsibilities if a file is getting too long.
- If you don't see the problem I'm describing, it is NOT because test data is stale — there is always a REAL problem to solve.
- ALWAYS investigate and complete issues in the order presented in the prompt (highest priority first).
- `output/Oblivion.esm` is a FOLDER, not a file. The output .esm goes in `output/Oblivion.esm/Oblivion.esm`. A write failure there means you're trying to overwrite a folder with a file, not a locked file.
- The assistant MUST NOT run `git stash` or `git stash pop` in this repository.

## Override Conversion (plugins with masters)

Converting a plugin that has TES4 masters (Nehrim's `Translation.esp` is ~100%
overrides) follows xEdit's "copy as override" model. Modules:
`overrides.py` (OverrideContext + nested-GRUP emission — the only override
code import_main touches), `export_diff.py`, `master_manifest.py`,
`override_builder.py` (field application), `override_merge.py` (master index).
Audit coverage anytime with `python tools/override_audit.py
export/<Plugin>` — it reports, per record type, what the override path does
with every record and every authored field with no output mapping.

**The rule:** a NEW record takes the normal conversion path. An OVERRIDE is the
master's converted record bytes EXACTLY, with only the fields the author changed
substituted in — and authorship comes from diffing the two TES4 EXPORTS, never
from comparing two conversion runs.

- **Never diff two conversions.** It conflates "the author changed this" with
  "our pass re-derived it differently", and the converter cannot tell them
  apart. That produced 1821 NPC_ `RNAM` races rewritten to vanilla Skyrim ones
  (the authors changed ZERO) and hung the game on load. Diffing the exports
  answers the question directly: a field neither export touches is never
  rewritten, so it cannot drift. No heuristics, no guessing.
- **List diffs must be ORDER-INSENSITIVE.** Oblivion does not preserve list
  order between a master and an overriding plugin: 1166 of 1264 Nehrim NPC_
  inventories differ positionally while only 5 differ as a set.
- **Export every record; never filter by load-order index.** A record whose
  FormID carries a master's index is an OVERRIDE. The old `source_filter`
  dropped 13,890 of Translation.esp's 13,892 records.
- **The FormID shift is the count of NEWLY PREPENDED masters, not
  `len(masters)`.** Using the latter moves overrides onto the plugin's own
  index, turning all 12,177 into duplicate new records.
- **Companion pairings come from a MANIFEST, not inference.** Converting one
  record generates companions (ARMO->ARMA, NPC_->OTFT/VTYP, AMMO->PROJ) whose
  ids come from a bare sequential counter. `writer.converting(source_fid)`
  records the pairing AT CREATION into `<Plugin>.manifest.json`; the plugin's
  run reads it. Re-deriving is impossible — the plugin converts a few thousand
  records, not the master's ~700k, so the counter lands elsewhere.
- **A cell override ships ONLY the references it CHANGES, never the master's
  whole child list.** Verified against BS_DLC_patch.esp: 54 cell overrides, ~5
  REFRs each (278 total), not the master's contents. Copying the full list
  bloated Translation.esp to 34 MB and duplicated 265k records for no benefit.
- **ONAM is what keeps the master's other refs visible.** The header's ONAM
  array lists every record this file overrides in a master's TEMPORARY cell-
  children group (type 9); the engine loads those on demand and, per xEdit's
  docs, "will ignore the override that is missing from ONAM". Persistent refs
  (type 8) are always resident and excluded. BS_DLC_patch's 216 ONAM entries
  are exactly its 216 temporary-group overrides — `writer.py` builds the list
  the same way at save time. WITHOUT ONAM a cell override suppressed the
  master's temporary children and interiors rendered black.
- **A record's children group must directly FOLLOW that record.** The engine
  reads `CELL, GRUP(6,cell), CELL, GRUP(6,cell), ...`; emitting all the records
  first and their groups afterwards pairs each group with the wrong cell.
- **NEVER synthesize an empty children group.** xEdit deletes them:
  `if Assigned(ChildGroup) and (ChildGroup.ElementCount < 1) then
  ChildGroup.Remove` (wbImplementation.pas ~5607).
- **Interior cells bucket by the last two DECIMAL digits of the OBJECTID**
  (`fid & 0xFFFFFF`): block = ones digit, sub-block = tens digit (xEdit
  wbImplementation CheckPosition; verified against Skyrim.esm). Bucketing by
  the full FormID (master-index byte included) put every Nehrim interior in
  the wrong block/sub-block. The master ALONE still played fine — the engine
  reads a winning cell's children from the offset recorded at load — but when
  a plugin overrides the CELL record, the engine re-locates the MASTER's copy
  via the bucket walk to demand-load its temporary children; wrong bucket =
  refs silently missing, only the eagerly-loaded persistent refs survive
  (renamed Translation.esp cells showed only Nehrim.esm persistent refs, and
  an xEdit copy-as-override reproduced it because xEdit buckets correctly
  while the master didn't). Exterior labels were verified CORRECT as written:
  `('<hh', Y, X)` with FLOOR division (Skyrim.esm grid x=7,y=-41 sits in
  sub-block low=-6=y//8, so Pascal-style truncation would be wrong).
- **Persistent-flagged refs inside temporary groups are vanilla-legal** —
  Skyrim.esm itself has 0x400-flagged REFR/ACHRs in type-9 groups
  (DragonBridgeFarm). DLC ESMs keep it clean (persistent overrides in type 8),
  which our source-flag routing already matches. Don't "fix" this.
- **But the record must still sit in the master's GRUP NESTING.** Interior:
  `CELL -> type 2 -> type 3`. Exterior: `WRLD -> type 1 -> type 4 -> type 5`.
  INFO: `DIAL -> type 7`. A record written flat under its top-level group is
  never indexed by the engine — as invisible as a missing one, and the second
  cause of black cells. Copy the nesting from `MasterIndex.group_path()`
  instead of recomputing it; there is then no block-number formula to get wrong.
  (Note the GRUP header layout: label is at offset 8, type at 12.)
- **Skip support-record creation for a plugin with masters.** VTYP/LCTN/vendor
  factions/TES4 globals are created by the master's run; recreating them in a
  dependent plugin duplicates master content (27 VTYP, 35 GLOB, 27 FACT, 265
  LCTN) and the duplicates compete with the originals the overrides reference.
- **INJECTED records** (Oblivion let a plugin ADD records at a master's index;
  Translation.esp does it 3x) move into OUR index. Detect against the master's
  **export**, never its converted output: conversion re-keys DIAL/INFO and skips
  whole types, so judging by output called 1693 records injected instead of 3.
- An export key `override_builder` cannot map is REPORTED, never approximated —
  the master's value stays and the run prints a summary. Mapped changes are
  applied three ways: translated-string substitution, SUBRECORD REBUILD (the
  converter's own builder — `_npc_acbs`, `build_cell_xcll`, `build_armo_bod2`,
  … — re-run against the PLUGIN's export and swapped in whole; drift-free
  because unchanged fields are identical in both exports), and run rebuilds
  for repeated-subrecord families (CNTO/SPLO/PKID) that preserve
  converter-ADDED entries (vendor gold, quest-package filtering) by deriving
  them from master-export-vs-master-output. Effect-list changes (SPEL/ENCH)
  RECONVERT the whole record instead — clone companions can't be spliced.
- **TES4 QSTA 'Flags' is a u8 + 3 bytes of uninitialized CS garbage** — diff
  it masked (`export_diff._LIST_FIELD_NORMALIZERS`) or 58 quests report
  phantom Target[] changes. Expect more TES4 fields like this; the fix
  belongs in the DIFF, never the export (which stays a pure dump).
- **A NEW record nested in a master's GRUP tree** (Translation.esp injects a
  map-marker REFR into a Nehrim cell) is converted normally and placed under
  the master parent's children group; if the parent record isn't already
  overridden, its converted bytes are pulled in VERBATIM as the anchor —
  the engine pairs a children GRUP with the record preceding it, so a group
  can never stand alone (same as xEdit's copy-as-override of a reference).
- Verify with: zero non-text diffs vs the master (only FULL/NAM1/DESC/CNAM/NNAM
  should differ), zero dangling refs, zero records at undefined master ids, and
  every override nested exactly as the master nests it.

## Parallelism Rules (learned 2026-07-16)

- **ThreadPoolExecutor is ONLY for I/O or subprocess work** (file reads, papyrus.exe, xWMAEncode). Pure-Python record conversion/parsing/formatting holds the GIL — threads pin one core AND (when converters allocate companion FormIDs) make output nondeterministic. Use ProcessPoolExecutor.
- **Worker state replay pattern**: converter functions depend on module globals set in Phase 0 (formid offset, cell locations, WORLD_NAMES, furniture origin shifts, mesh bounds). Process pools must replay them via an initializer — see `tes5_import/navm_worker.py` and `tes5_import/convert_worker.py`.
- **Determinism contract**: the output ESM must be byte-reproducible. Process results in submission order (`ex.map`, not `as_completed`) and keep any `writer.alloc_formid()` callers serial. Verify with `tools/esm_diff.py A.esm B.esm` (distinguishes real diffs from reorders).
- **Export format workers re-read from mmap**: `tes4_export` scans record offsets only (`read_file(..., parse_subs=False)`) and workers re-read/format from their own mmap — never pickle `Record` objects across process boundaries.
- **`unescape_value` fast path matters**: a `'\\' not in value` check made text parsing ~7x faster; keep C-speed scans in per-line hot paths.
- **Don't parallelize µs-level converters** (REFR/ACHR/CELL): the pickle round-trip costs more than the conversion. Only LAND (~0.9 ms/record) is worth a pool.
- **`bytes += big` is quadratic** — accumulate group contents in lists and `b''.join` at wrap points (CELL/WRLD builders).

## Documentation Map

Deep reference material lives in `docs/` so this file stays short. Load the
relevant doc when working in that area:

| Doc | Covers |
|---|---|
| [docs/record_mapping_reference.md](docs/record_mapping_reference.md) | Full TES4→TES5 record type mapping table, OBND/structural requirements, skipped/problem records, skill/weapon/biped-slot/enchantment mapping tables, Skyblivion best-practices (NPC_/ENCH/SPEL/FACT/ALCH/CELL/WRLD/REFR/LTEX/SOUN/CLAS conversion rules) |
| [docs/nif_conversion_notes.md](docs/nif_conversion_notes.md) | NIF mesh conversion deep-dive: bhk collision/MOPP/CMS, particle systems, FlameNode grafting, worn armor/shields/furniture markers, skin retargeting, clutter physics, terrain LOD, SpeedTree procedural conversion |
| [docs/dialogue_conversion_notes.md](docs/dialogue_conversion_notes.md) | DIAL/INFO/QUST/DLBR/DLVW conversion implementation notes, voice type routing, AddTopic unlock system, GetIsID injection |
| [docs/world_land_navmesh_notes.md](docs/world_land_navmesh_notes.md) | PGRD→NAVM/NAVI conversion algorithm, LAND record structure, landscape TXST |
| [docs/creature_conversion.md](docs/creature_conversion.md) | CREA→Skyrim actor conversion plan + implementation status: generated behavior graphs, HKX skeleton/animation/ragdoll, creature records |
| [docs/python_tools_reference.md](docs/python_tools_reference.md) | Command reference for `tes4_export`/`tes5_import`/`asset_convert` modules and `tools/` debug utilities |
| `oblivion-dialog-system` skill | Vanilla TES4 dialogue/voice/quest record reference |
| `skyrim-dialog-system` skill | Vanilla TES5 dialogue/voice/quest record reference |
| `oblivion-to-skyrim-dialog` skill | TES4→TES5 dialogue/quest/voice mapping reference |

## Automation Pipeline

The conversion is orchestrated by `run/convert.py` (Python), which processes files from `conversion_config.json` in dependency order. The old `convert.ps1` (PowerShell) is preserved but superseded.

### Running the Pipeline

```bash
# Full pipeline (Python orchestrator)
python -m run.convert

# Export only (native Python — no xEdit needed)
python -m run.convert --export-only -f Oblivion.esm

# Force re-export
python -m run.convert --no-cache -f Oblivion.esm

# Direct export CLI
python -m tes4_export.export "C:/path/to/Oblivion.esm" --outdir export/Oblivion.esm

# List record types and counts
python -m tes4_export.export "C:/path/to/Oblivion.esm" --list-types

# Run tests
python -m pytest tests/ -v

# Run import (TES4 text → TES5 binary)
python -m tes5_import export/Oblivion.esm -o output/Oblivion.esm -m Skyrim.esm

# GUI
python -m run.gui

```

See [docs/python_tools_reference.md](docs/python_tools_reference.md) for the full command reference (asset conversion, debug tools, verify_plugin.py).

### Pipeline Phases

1. **Phase 1: Export** (Python native) — Reads TES4 binary, outputs KEY=VALUE text per record type into `export/<filename>/` directory (one file per record type)
2. **Phase 2: Import** (Python native) — Reads text export, converts all records to TES5 format, writes binary ESM/ESP. Handles type mapping (CREA→NPC_, CLOT→ARMO, etc.), FormID remapping, group hierarchy (CELL/WRLD/DIAL), companion record generation (TXST for LTEX, SNDR for SOUN), and LAND binary data.
3. **Phase 3: Extract Assets** (optional) — BSA extraction + mesh/texture conversion

### Configuration (`conversion_config.json`)

```json
{
  "files": ["Oblivion.esm", "Knights.esp"]
}
```

Skipped record types are managed in code (`SKIP_TYPES` in `tes5_import/constants.py`): ROAD, SCPT, SKIL, BSGN, RACE, MGEF, CSTY, IDLE, GMST, CLMT, REGN, EYES, HAIR. GLOB, CLAS, WTHR, WATR and PACK ARE converted. Conditions whose params reference skipped types must be translated (RACE → Skyrim race via RACE_MAP in dialog_conditions) or dropped — a dangling param means the condition can never pass and the CK warns "Unable to find ... TESForm in TESConditionItem Parameter Init".

**Only a CTDA param that is actually a FormID may be load-order remapped.**
Most condition functions take a plain integer or enum, and Skyrim uses several
of them as a RAW ARRAY INDEX — so a remapped value is an out-of-bounds READ,
not merely a dangling reference. `GetBaseActorValue(Speechcraft=32)` remapped
to `0x01000020` indexed 16.7M entries past the actor-value table and crashed
the game (EXCEPTION_ACCESS_VIOLATION, `mov rcx,[rax+rcx*8+8]`, MenuTopicManager
on the stack) the instant any converted NPC was spoken to. The param-type table
is GENERATED from xEdit's `wbConditionFunctions` array, never hand-written:
`python tools/gen_ctda_param_types.py <path>/wbDefinitionsTES5.pas -o
tes5_import/ctda_param_types.py` (`--func N` prints one function's signature).
Everything before `ptActor` in xEdit's `TConditionParameterType` enum is a
value, not a FormID. Gate on the POST-`_FUNC_REMAP` (TES5) index — that is the
function the output file actually invokes, and 7 indices were reused between
games with different param types. This corrupted 257 params in Nehrim (164
`GetBaseActorValue` crashers plus silent never-pass gates: 46 `GetStageDone`
stage numbers, 31 `GetIsSex`/`GetPCIsSex` enums, `GetIsUsedItemType`,
`MenuMode`).

Files are listed in dependency order. Masters are auto-detected from the TES4 binary headers. Game data paths are auto-detected from the Windows registry.

### Caching

- Export files are cached in `export/<filename>/` directory (one .txt file per record type)
- FormID mappings are cached in `export/mappings/<filename>.FormID_Mapping.txt`
- When processing `Knights.esp`, cached `Oblivion.esm` export and mappings are reused
- Use `--no-cache` to force re-export

### Settings File Communication

The orchestrator and xEdit scripts communicate via `Edit Scripts/conversion_settings.txt`:
- `MODE` = EXPORT, IMPORT, or RELINK
- `SOURCE_FILE` = which file to export (export only processes records from this file)
- `EXPORT_DIR` / `IMPORT_FILE` / `OUTPUT_NAME` = paths for I/O
- `MAPPING_DIR` = where FormID mapping files are stored
- `MASTER_MAPPINGS` = semicolon-separated list of master FormID mapping files

When no settings file is present, scripts fall back to their original interactive behavior.

### Directory Structure

```
TESConversion/
  convert.py              # Pipeline orchestrator (export + import)
  verify_plugin.py        # Plugin validation tool
  tes4_export/            # Python export pipeline
    __init__.py
    __main__.py           # python -m tes4_export (runs export)
    tes4_reader.py        # Binary TES4 file reader (mmap-based)
    export.py             # Export CLI: per-type KEY=VALUE text output
    text_reader.py        # Parse KEY=VALUE text exports back to dicts
    record_types/         # Per-record-type field parsers (export)
      common.py           # Shared: emit_string, emit_formid, emit_effects, etc.
      items.py            # STAT, ACTI, MISC, KEYM, DOOR, FLOR, FURN, GRAS, etc.
      equipment.py        # WEAP, ARMO, CLOT, AMMO, BOOK, ENCH, SPEL, ALCH, etc.
      actors.py            # NPC_, CREA, CONT, FACT, RACE, CLAS, EYES, HAIR, etc.
      world.py             # CELL, WRLD, REFR, ACHR, ACRE, LAND, LTEX, REGN, etc.
      dialog_misc.py       # DIAL, INFO, QUST, PACK, SCPT, GLOB, GMST, SOUN, etc.
  tes5_import/            # Python import pipeline (TES4 text → TES5 binary)
    __init__.py           # Package init, re-exports dispatch/type maps
    __main__.py           # python -m tes5_import (runs import)
    constants.py          # All lookup tables, dispatch maps, SKIP_TYPES
    writer.py             # TES5 binary packing (records, groups, headers)
    import_main.py        # Import orchestrator: 5-phase conversion
    record_types/         # Per-record-type converter functions (import)
      common.py           # Shared: _prefix_path, _add_model, _common_header_subs, etc.
      items.py            # STAT, ACTI, MISC, KEYM, DOOR, FLOR, FURN, GRAS, etc.
      equipment.py        # WEAP, ARMO, CLOT, AMMO, BOOK, ENCH, SPEL, ALCH, etc.
      actors.py            # NPC_, CREA, FACT, EYES, HAIR, CLAS, GLOB, GMST, leveled lists
      world.py             # LTEX, CELL, WRLD, REFR, ACHR, ACRE, LAND, REGN, LSCR, EFSH
      dialog_misc.py       # QUST, DIAL, INFO, SOUN
  asset_convert/          # Asset conversion pipeline
    nif_converter.py      # NIF mesh conversion (strips, textures, bones, collision, retarget)
    collision.py          # Havok collision conversion (rigid bodies, shapes, materials)
    cms.py                # bhkCompressedMeshShapeData decode + engine shape-key prediction
    cms_builder.py        # CMS building from triangle soup (+ Havok bridge MOPP/welding)
    mopp.py               # MOPP VM symbolic walker + dechunker (forensics)
    skin_retarget.py      # Skeleton retargeting (Oblivion Bip01 → Skyrim NPC bones)
    skyrim_overrides.py   # Bone mapping, BSX flags, biped slot tables
    bsa_extract.py        # BSA extraction with manifest caching
    asset_pipeline.py     # 3-phase orchestrator: extract→convert→output
    spt_parser.py         # SpeedTree .spt binary → structured params (bezier curves, levels, leaf maps, collision)
    spt_generator.py      # Procedural tree geometry from parsed SPT params (bark tubes + leaf cards + collision soup)
    spt_converter.py      # SpeedTree .spt → Skyrim NIF (real procedural conversion, one NIF per TREE record)
  external/               # Third-party binaries & vendored code (see README license table)
    bsarch/BSArch.exe               # BSA packing (xEdit)
    lodgen/LODGenx64.exe            # Object LOD generation (xEdit)
    hkxcmd/hkxcmd.exe               # Havok packfile XML<->binary compiler
    mopp_bridge/dovah_hkp_mesh_mopp_bridge.exe  # Havok MOPP/welding compiler (real hkpMoppUtility)
    papyrus-compiler/papyrus.exe    # Papyrus compiler (MIT)
    xwmaencode/xWMAEncode.exe       # xWMA voice compression (not redistributed)
    pynifly_hkx/                    # Vendored PyNifly Havok reader (GPL-3.0)
  tests/                  # Root-level test directory
    test_export.py        # Export pipeline tests (pytest)
    test_import.py        # Import pipeline tests (pytest)
    test_asset_convert.py # Mesh conversion tests (pytest)
    test_skin_retarget.py # Skin retargeting tests (pytest)
  tools/                  # Debug/analysis utilities — see docs/python_tools_reference.md
  conversion_config.json  # File list and settings
  export/                 # Cached export files (gitignored)
    Oblivion.esm/         # Per-type text files (ACTI.txt, NPC_.txt, etc.)
    Knights.esp/          # Per-type text files
    mappings/
      Oblivion.esm.FormID_Mapping.txt
      Knights.esp.FormID_Mapping.txt
  output/                 # Final converted plugins (gitignored)
  docs/
    TES5_Binary_Format.md # TES5 binary structure reference
```

## xEdit Scripting Environment

- **Language**: Pascal (JvInterpreter). Not full Delphi — many features unsupported.
- **Entry points**: `Initialize` (startup), `Process(e: IInterface)` (per-record), `Finalize` (cleanup).
- **Type system**: Everything is `IInterface`. Internally wraps `IwbFile`, `IwbMainRecord`, `IwbGroupRecord`, `IwbSubRecord`, etc.
- **Short-circuit evaluation does NOT work**: `if Assigned(x) and (x.Foo = 1)` will crash. Use nested ifs.
- **No object types, no constructors, no overloading, no `as`/`is` operators, no `with`, no `in`**.
- **`try/except` does not catch all runtime errors**.
- **No nested try blocks**: `try/except` inside `try/finally` causes a syntax error.
- **TStringList cannot be passed as function parameters** — causes "Type mismatch" at runtime. Use global variables instead.
- **TStringList.Values[]** uses `=` as separator (same as our KEY=VALUE format).
- **TStringList.IndexOfName(key)** returns -1 if key not found (useful for checking key existence).

### Key xEdit Functions

| Function | Purpose |
|----------|---------|
| `Add(container, sigOrName, True)` | Create/find a subrecord or group |
| `ElementBySignature(rec, 'XXXX')` | Get subrecord by 4-char signature |
| `ElementByPath(rec, 'path\to\field')` | Navigate nested elements |
| `ElementByName(rec, 'Name')` | Get element by display name |
| `ElementByIndex(container, i)` | Get the i-th child element |
| `ElementCount(container)` | Number of child elements |
| `ElementExists(rec, 'path')` | Check if element exists |
| `GetElementEditValues(rec, 'path')` | Get string representation of value |
| `SetElementEditValues(rec, 'path', val)` | Set value from string |
| `GetElementNativeValues(rec, 'path')` | Get native value (int/float) |
| `SetElementNativeValues(rec, 'path', val)` | Set native value |
| `LinksTo(element)` | Follow a FormID reference to the target record |
| `Signature(rec)` | Get 4-char record type (e.g. 'NPC_') |
| `EditorID(rec)` | Get EditorID |
| `GetLoadOrderFormID(rec)` | Get load-order FormID |
| `SetLoadOrderFormID(rec, id)` | Set load-order FormID |
| `ElementAssign(array, HighInteger, nil, False)` | Append new entry to array element |
| `wbCopyElementToFile(el, file, asNew, deep)` | Copy element to file |
| `wbCopyElementToRecord(el, rec, asNew, deep)` | Copy element into a record |
| `GetFormVersion(rec)` / `SetFormVersion(rec, v)` | Record form version (43=LE, 44=SSE) |
| `GroupBySignature(file, sig)` | Get top-level group from file |
| `RecordByFormID(file, id, allowInjected)` | Find record by FormID |
| `FileByIndex(i)` | Get loaded file by index |
| `AddMasterIfMissing(file, 'name')` | Add master dependency |
| `GetIsESM(file)` / `SetIsESM(file, b)` | ESM flag |

### Global Variables

| Variable | Type | Description |
|----------|------|-------------|
| `DataPath` | String | Path to game's Data folder |
| `ProgramPath` | String | Path to xEdit installation |
| `ScriptsPath` | String | Path to Edit Scripts folder |
| `FileCount` | Integer | Number of loaded files |
| `wbAppName` | String | 'TES5', 'TES4', etc. |

## Record Format Differences: TES4 vs TES5

See [docs/record_mapping_reference.md](docs/record_mapping_reference.md) for the full
record type mapping table, structural requirements (OBND, form version, etc.), skipped/
problem records, and all actor value/weapon/biped-slot/enchantment mapping tables.

## Text Export Format

Records are delimited by `---RECORD_BEGIN---` and `---RECORD_END---`. Each line is `KEY=VALUE`. Special characters are escaped: `\\`, `\"`, `\n`, `\r`, `\t`. Lines starting with `#` are comments. FormIDs are 8-digit hex strings in load-order format. Array items use indexed keys like `Item[0].FormID`, `Item[0].Count`.

### New Python Export Format (v2)
The Python exporter uses `Signature=` for the original TES4 record type (replaces `TargetType=`/`OriginalType=`). No transformation fields are included.
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
...
---RECORD_END---
```
Key differences from old xEdit export: no `TargetType=`, no `OriginalType=`, no `tes4\\` path prefix, no derived fields. The import script must handle all type mapping (CREA→NPC_, CLOT→ARMO, LVLC→LVLN, etc.) and path prefixing.

## Papyrus Notes

TES4 uses an imperative scripting language with event blocks (GameMode, OnActivate, etc.). TES5 uses Papyrus, an object-oriented language. Key differences:
- Variables become Properties: `short myVar` → `Int Property myVar Auto`
- Event blocks change: `begin OnActivate` → `Event OnActivate(ObjectReference akActionRef)`
- Functions change: `Message "text"` → `Debug.Notification("text")`
- TES4 `set x to y` → `x = y`
- Player reference: `player.` → `Game.GetPlayer().`
- No direct equivalent for: GetInCell (→IsInLocation), ShowMap, CloseOblivionGate, SetQuestObject
- TES4 attributes (Strength, etc.) have no Papyrus equivalent
- Vanilla Papyrus has more than the wikis suggest — check the game's `Data/Source/Scripts/*.psc` headers before declaring something unconvertible. `Faction.SetReaction/ModReaction`, `Actor.GetCurrentPackage()` (→ GetIsCurrentPackage/GetCurrentAIPackage-vs-form), `ObjectReference.PushActorAway` and `ObjectReference.GetAnimationVariableBool("bAnimPlaying")` (→ IsAnimPlaying) all exist and are used by the converter.
- `pme`/`sme` (PlayMagicEffectVisuals) take a MGEF code, not a shader: resolve code → TES4 MGEF → its `DATA.EffectShader` (else EnchantEffect, else school enchant glow) → converted EFSH, and emit `<shader>.Play(ref, dur)`. EFSH records are converted, so the property binds.
- `IsSpellTarget X` → `TES4Polyfill.HasMagicEffectByID(ref, <Skyrim MGEF fid>)` where the MGEF is the spell's first effect surviving import (same mapping as `_pack_effects`); pure script-effect spells are detected via the importer's first filler effect, which keeps the dropped effect's duration for exactly this reason.
- `begin OnAlarm` → `OnCombatStateChanged` guarded `aeCombatState != 0`; `OnStartCombat` bodies are guarded `== 1` (the event also fires on combat END).
- Bare `begin MenuMode` + `isPCSleeping` (Oblivion's sleep-detection idiom) → `RegisterForSleep()` + OnSleepStart/OnSleepStop running the body twice with a `TES4_PCSleeping` flag (11 quests incl. MG04 inn ambush, Rufio murder, vampirism relied on it). Menu-ID MenuMode blocks stay commented out.
- `GetSecondsPassed` substitutes `_get_update_interval()` (must equal the RegisterForSingleUpdate arg or timers run off-rate); TES4 `Say`/`SayTo` returned the line duration — assignments get `SAY_LINE_SECONDS` (3.0) so polling conversations don't machine-gun.
- Run-on-Target CTDAs in Say()-driven topics can never pass (Say has no dialogue target) — the importer retargets them to RunOn=Reference (unique script target, usually PlayerRef) or drops them (mixed targets); see docs/dialogue_conversion_notes.md 2026-07-19.
- Converted Escort/Follow/Travel packages must keep "Ride Horse?"=0 unless the TES4 package set Use-Horse (0x00800000) — ride_horse=1 on a horseless NPC freezes them in place (Pinarus/FGC01Rats).
- Vanilla forms with no TES4 counterpart are reached via `Game.GetFormFromFile(0x..., "Skyrim.esm")` in TES4Polyfill (ActorTypeNPC keyword for GetIsCreature, GuardDialogueFaction for IsGuard, PlayerVampireQuestScript.VampireStatus for HasVampireFed) — no property binding needed.

### Papyrus syntax traps found via Nehrim (2026-07-20, 50.5% → 98.4% compile rate)

- **`;/` opens a Papyrus BLOCK comment** (closed by `/;`). Oblivion scripts use `;//////...` banner rules constantly and TES4 had no block-comment syntax, so every banner swallowed the rest of the file. The compiler only reports this as `unexpected end of file` at the LAST line, and one unterminated banner in a widely-extended base script cascaded into ~300 downstream failures. `_postprocess_lines` pads a space after the `;`.
- **Oblivion accepted a comma between a command and its first argument** (`IsActionRef, Player`, `MessageBox, "text"`, `SetPCExpelled Fac, 1`). `_emit_function` strips a leading comma once for all handlers; the expression router also matches `^(\w+)(?:\s*,\s*|\s+)(.+)$`. Handlers that `split(None, 1)` must still `rstrip(',')` the token.
- **TES4 EditorIDs may start with a digit** (`1Feuerball`, `01SetBonus...`); Papyrus identifiers may not. Regexes anchored on `^[a-zA-Z_]` silently skipped these, leaving the raw name in the output. Use `^\w+` and exclude pure digits / `(?!\d+\.)` so float literals still parse. `_safe_property_name` strips the leading digit for the declaration, so call sites must go through the same lookup or the two disagree.
- **`"EditorID".Function` (quoted ref)** is valid TES4 and appears in 143 Nehrim scripts. Unquote before the ref patterns run, or the call is emitted as a property access on a string.
- **Anything unparseable must be emitted COMMENTED**, never as bare code — TES4 uses `-----` separator rules, which parse as a prefix expression.
- A `FUNCTION_MAP` entry with a `None` Papyrus name normally falls through to the EditorID lookup on purpose (bare `getSecondsPassed` etc. are rewritten by later passes; routing them early TODO's them mid-expression and leaves `timer = timer - `). Bare-read commands that have no such pass belong in `_BARE_NO_EQUIV_COMMANDS`.

### OBSE constructs (Nehrim depends on these heavily)

- **User-defined functions**: `begin Function{ a, b }` + `Call <ScriptName> arg1, arg2` (first arg space-separated, rest comma-separated; param list may use EITHER separator). Converted to a Papyrus method named `TES4Call` on the callee script, reached through a property typed as that script. NOT `Global` — the bodies read the script's own object properties.
  - Params must NOT also be emitted as auto-properties; the parameter would shadow the property while callers write neither, so the body reads a permanent 0.
  - A TES4 `ref` param is an untyped handle: type it from USAGE (convert the body first, then read `_property_refs`), else `Form`. Typing it `ObjectReference` — the literal translation — rejected all 170 call sites that pass a Spell.
  - `SetFunctionValue X` + `return` → `Return X`, and the function needs a return type plus a trailing `Return 0` for fall-through paths.
- `eval <expr>` is a pure pass-through wrapper (Nehrim uses it only around `Call`) — drop it.
- `Let X := Y` and the compound forms `+= -= *= /=` → `X = X op Y` (Papyrus has no compound assignment).
- No Papyrus equivalent, emitted inert with `;NE:` — OBSE arrays/strings (`ar_*`, `sv_*`, `forEach`), path-based music (`StreamMusic` and Nehrim's bundled `emc*` plugin; Skyrim music is MusicType-based), `GetPlayerHasLastRiddenHorse`, `HasFlames`/`AddFlames`/`RemoveFlames`, `PositionCell` (Papyrus `MoveTo` takes a reference, not cell coordinates), `GetIgnoreFriendlyHits` (Skyrim exposes only the setter).
- **OBSE `IsCasting` maps NATIVELY** — `GetAnimationVariableBool("bIsCastingRight"/"bIsCastingLeft")`, no SKSE needed. Check for a native equivalent before declaring a function unconvertible.

## Development Workflow

### Automated (Recommended)
```bash
# Export TES4 records to text
python -m tes4_export.export "C:/path/to/Oblivion.esm" --outdir export/Oblivion.esm

# Import text → TES5 binary
python -m tes5_import export/Oblivion.esm -o output/Oblivion.esm -m Skyrim.esm

# OR run both export and import
python convert.py -f Oblivion.esm

# Convert NIF meshes (Oblivion → Skyrim format)
python -m asset_convert.nif_converter export/Oblivion.esm/meshes/ output/oblivion.esm/meshes/tes4/

# Run tests
python -m pytest tests/ -v

# Verify output with xEdit
if (Test-Path SSEEdit_log.txt) { Remove-Item SSEEdit_log.txt -Force }; $tes5 = "C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data"; $tp = "temp_plugins.txt"; "*Skyrim.esm`n*Oblivion.esm" | Set-Content $tp -Encoding UTF8; $args = "-P:`"$tp`" -D:`"$tes5`" -autoload -IKnowWhatImDoing `"Oblivion.esm`""; Start-Process -FilePath ".\sseEdit.exe" -ArgumentList $args -WorkingDirectory (Get-Location).Path
```
### Manual
1. Export: `python -m tes4_export.export "path/to/Oblivion.esm" --outdir export/Oblivion.esm`
2. Import: `python -m tes5_import export/Oblivion.esm -o output/Oblivion.esm -m Skyrim.esm`
3. Validate: Open output in SSEEdit to check for errors
4. Manual fixup: ARMA creation, keyword assignment, MGEF resolution, package rebuilding, Papyrus scripting

## Asset Pipeline, NIF/Mesh Conversion, PGRD→NAVM, Dialogue, Creatures

These deep-dive topics are documented in `docs/` — see the Documentation Map above.
Quick links:
- Asset/BSA pipeline overview, NIF/collision/particle/animation internals → [docs/nif_conversion_notes.md](docs/nif_conversion_notes.md)
- PathGrid→NavMesh conversion, LAND structure → [docs/world_land_navmesh_notes.md](docs/world_land_navmesh_notes.md)
- Dialogue/quest/voice conversion → [docs/dialogue_conversion_notes.md](docs/dialogue_conversion_notes.md)
- Creature (CREA→actor) conversion → [docs/creature_conversion.md](docs/creature_conversion.md)
- Tool commands → [docs/python_tools_reference.md](docs/python_tools_reference.md)
