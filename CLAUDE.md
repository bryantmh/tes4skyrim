# TES4-to-TES5 Conversion Project — AI Context

## Project Goal

Convert TES4 (Oblivion) master/plugin files to TES5 (Skyrim) format.

1. **tes4_export** (Python) — Reads TES4 binary files directly and exports all records to KEY=VALUE text format. Pure dump — no transformations.
2. **tes5_import** (Python) — Reads the text export and writes a binary TES5 ESM/ESP. All TES4→TES5 transformations happen here.
3. **asset_convert** (Python) - Performs all mesh / speedtree conversion

## Critical Working Rules

-The export is a PURE dump of TES4 data. No type mapping (CREA→NPC_), no field derivation, no path prefixing. ALL transformations belong in the import script.
- You can access OG skyrim meshes for comparison in `references\Skyrim Meshes`. A skyrim esm dump for comparison is in `Rreferences\Skyrim.esm`. Real Skyrim .nif files for analysis/testing are in the `references` folder.
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
  "skipTypes": ["GMST", "GLOB", "MGEF", "CSTY", "WTHR", "WATR", "RACE", "CLAS", "IDLE", "PACK", "CLMT", "REGN"],
  "files": ["Oblivion.esm", "Knights.esp"]
}
```

`skipTypes` — Record types to exclude from import. These are checked in the Python import script (SKIP_TYPES in tes5_import/constants.py). Types that cause Skyrim to fail to load when converted should be listed here.

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
