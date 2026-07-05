# TES4-to-TES5 Conversion Project — AI Context

## Project Goal

Convert TES4 (Oblivion) master/plugin files to TES5 (Skyrim SE) format. The pipeline is:

1. **tes4_export** (Python) — Reads TES4 binary files directly and exports all records to KEY=VALUE text format. Pure dump — no transformations.
2. **tes5_import** (Python) — Reads the text export and writes a binary TES5 ESM/ESP. All TES4→TES5 transformations happen here.
3. **TES5_Import_Records.pas** — (Legacy, replaced by Python importer) Run in SSEEdit to create TES5 records.
4. **TES5_Relink_References.pas** — Run after import. Resolves cross-references (FormIDs) using FormID mappings.
5. **oblivion_to_papyrus.py** — Converts Oblivion script source to Papyrus (best-effort).

**IMPORTANT DESIGN PRINCIPLE**: The export is a PURE dump of TES4 data. No type mapping (CREA→NPC_), no field derivation, no path prefixing. ALL transformations belong in the import script.

**IMPORTANT Instructions**:
	You can access OG skyrim meshes for comparison in temp\skyrim meshes
  You can acces a skyrim esm dump for comparison in temp\skyrim.esm
	You should only run nif comparisons on a maximum of 10 nifs at a time when single threaded. Search for a folder structure and run comparison on a deeply nested subfolder that likely has the attributes you want
  NEVER run tests on a large batch of NIFs without many workers. It takes an excessively long time
	Another step could be to compare the output\ mesh with a few similar skyrim meshes and the export\ mesh
  Real Skyrim .nif files for you to analyze and test are found in the temp folder
	The idea is complete conversion. Avoid Stripping things out even if the conversion process would be complicated.
	Do one bug at a time and make the necessary edits before moving onto the next bug.
	If you find another bug in the course of your investigation don't hesitate to fix it as well
	You can reference docs/nif [version number].xml to determine if something is valid behavior for a skyrim nif. It is much newer and likely more correct than the very old version bundled with pyffi 2.2.3
	When testing converting nifs with nif_converter. Make sure to use the full number of workers (cpu_count() - 1) otherwise it will take much to long
	Use Pyffi with a monkey patch for clock when analyzing
  Files in these `references/`subfolders (`NIFConverter/`, `xEdit/`, `UESP/`, `nifskope`) are from other projects and included for reference only. The `xEdit/Core/` folder contains xEdit's record definition files which document the binary structure of every record type.
  You also have access to the NifSkope source code, to inform your understanding complex problems
  Ensure that you add any learnings to this file so that you can retain the knowledge for future reference.
  temp files that you create that you do not intend to use again should be created in the temp folder.
  Avoid making temp files with targeted outputs, and instead make and use reusable and  tools/ scripts with general outputs and arguments. This way you can reuse the same tools for future investigations and avoid cluttering the temp folder with files that are only useful for a single test.
  Utilize Multi-threading where possible!
  DON'T worry about backwards compatibility. Remove old code if it is no longer going to be used.
  Attempt to ensure that files do NOT go over approximately 1000 lines if at all possible. If a file is getting too long, break it up into multiple files with clear responsibilities.
  If I ask you to perform a task, and you don't see the problem, it is NOT because my test data is stale. There is always a REAL problem you need to solve.
  ALWAYS investigate and complete issues in the order that I present them to you in my prompt. They are given in order of highest importance for you to work on.
  output/Oblivion.esm is a FOLDER, not a file!! If you fail to write it is NOT because the file is locked. It is because you are trying to overwrite a folder with a file. The output .esm should go in output/Oblivion.esm/Oblivion.esm

**Assistant Constraint — No Git Stash**:
- The assistant MUST NOT run `git stash` or `git stash pop` in this repository.
- A persistent preference file has been created at `/memories/do_not_use_git_stash.md` to record this rule.

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

# Old PowerShell (still works)
.\convert.ps1
```

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
      actors.py           # NPC_, CREA, CONT, FACT, RACE, CLAS, EYES, HAIR, etc.
      world.py            # CELL, WRLD, REFR, ACHR, ACRE, LAND, LTEX, REGN, etc.
      dialog_misc.py      # DIAL, INFO, QUST, PACK, SCPT, GLOB, GMST, SOUN, etc.
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
      actors.py           # NPC_, CREA, FACT, EYES, HAIR, CLAS, GLOB, GMST, leveled lists
      world.py            # LTEX, CELL, WRLD, REFR, ACHR, ACRE, LAND, REGN, LSCR, EFSH
      dialog_misc.py      # QUST, DIAL, INFO, SOUN
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
    spt_converter.py      # SpeedTree .spt → Skyrim NIF (asset-matching from assets/speedtrees/)
    dovah_hkp_mesh_mopp_bridge.exe  # Havok MOPP/welding compiler (real hkpMoppUtility)
  tests/                  # Root-level test directory
    test_export.py        # Export pipeline tests (pytest)
    test_import.py        # Import pipeline tests (pytest)
    test_asset_convert.py # Mesh conversion tests (pytest)
    test_skin_retarget.py # Skin retargeting tests (pytest)
  tools/                  # Debug/analysis utilities
    tes4_nif_analyzer.py  # Dump NIF structure to text: python tools/tes4_nif_analyzer.py <nif_or_dir> [--outdir dir] [--max N]
    tes5_nif_analyzer.py  # Same for Skyrim NIFs (identical format for diff comparison)
    tes5_esm_reader.py    # TES5 ESM/ESP reader: python tools/tes5_esm_reader.py <esm> [--outdir dir] [--types TYPE ...]
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

### Critical Structural Requirements for TES5

1. **OBND (Object Bounds)** — 12-byte struct required on nearly all item/object records (ACTI, ALCH, AMMO, ARMO, BOOK, CONT, DOOR, ENCH, FLOR, FURN, INGR, KEYM, LIGH, MISC, NPC_, SLGM, SPEL, SCRL, STAT, TREE, WEAP, and more). TES4 has no OBND. **Missing OBND will cause the engine to reject records.**

2. **File Header (TES4 record)** — HEDR version must be 1.7 (Skyrim LE) or 1.71 (SSE), not 1.0 (Oblivion). Form version = 44 for SSE.

3. **Form Version** — Each TES5 record header has a form version field. SSE = 44, LE = 43. Some record structures differ by form version.

4. **No SCRI** — TES4 uses SCRI (FormID → SCPT record). TES5 uses VMAD (Virtual Machine Adapter) for Papyrus. VMAD cannot be auto-generated from TES4 scripts.

5. **Keyword System (KSIZ/KWDA)** — TES5 records extensively use keywords. ARMO, WEAP, NPC_, SPEL, ALCH, INGR, RACE, and many others have keyword arrays. Many game systems depend on specific keywords.

6. **Localized Strings** — When the TES4 header has the Localized flag (0x80), FULL/DESC/etc. become LString indices. Non-localized plugins use inline strings.

### Record Type Mapping

| TES4 Type | TES5 Type | Notes |
|-----------|-----------|-------|
| ACTI | ACTI | Add OBND. Needs VMAD instead of SCRI. |
| ALCH | ALCH | Add OBND. ENIT restructured. Effects need MGEF FormID resolution. |
| AMMO | AMMO | Add OBND. DATA restructured. Needs DNAM for projectile ref. |
| ANIO | ANIO | Minor changes. |
| APPA | MISC | No apparatus in TES5. Convert to MISC. |
| ARMO | ARMO | **Major changes**: BMDT(4B)→BOD2(8B), 16→32 biped slots. Armor models move to ARMA records. ARMO references ARMA via MODL array. No direct mesh on ARMO. Add OBND, RNAM (race), keywords. |
| BOOK | BOOK | Add OBND. DATA restructured. Skill teaching uses TES5 skill enum. |
| BSGN | *(none)* | Birthsigns don't exist. Spells should go to Race records or Standing Stones. Exported as BSGN_SPELLS for reference. |
| CELL | CELL | DATA: U8→U16 flags. Lighting (XCLL) expanded. New: LTMP (lighting template), XLCN (location), XCAS (acoustic space), XCMO (music type). |
| CLAS | CLAS | Simplified in TES5. No attributes/skills. Only Flags, Teaches, MaxTraining. |
| CLMT | CLMT | Minor changes. |
| CLOT | ARMO | Clothing → ARMO with ArmorType=Clothing in BOD2. Same ARMA requirement. |
| CONT | CONT | Add OBND. Minor changes. |
| CREA | NPC_ | **No CREA in TES5**. Must convert to NPC_. TES4 creature stats (attributes, skills) must map to TES5 DNAM. Needs race assignment. |
| CSTY | CSTY | **Completely restructured**: TES4 CSTD/CSAD → TES5 CSGD/CSMD/CSME. |
| DIAL | DIAL | Categories restructured. TES5 adds DLBR (Dialog Branch) and DLVW (Dialog View). |
| DOOR | DOOR | Add OBND. Minor changes. |
| EFSH | EFSH | DATA structure differs. |
| ENCH | ENCH | **ENIT completely restructured**: 16B→36B. Type enum changes (0-3 → 6/0xC). Add OBND. New fields: Cast Type, Target Type, Charge Time, Base Enchantment. |
| EYES | EYES | Minor changes. |
| FACT | FACT | DATA flags differ slightly. Crime data: CNAM→CRVA. |
| FLOR | FLOR | Add OBND. Minor changes. |
| FURN | FURN | Add OBND. Furniture markers restructured (FNMK was a U32 bitmask; TES5 uses entry-based system with FNPR). |
| GLOB | GLOB | Identical. |
| GMST | GMST | Many settings differ but format is same. Some TES4 GMSTs don't exist in TES5. |
| GRAS | GRAS | Add OBND. Minor changes. |
| HAIR | HDPT | Hair→Head Part. TES5 HDPT has Type=3 (Hair), flags, extra parts list, TNAM (texture set). |
| IDLE | IDLE | Add conditions changes. |
| INFO | INFO | **Major restructuring**: TES5 INFO uses VMAD fragments, ENAM, different response structure (TRDA vs TRDT), conditions restructured. |
| INGR | INGR | Add OBND. ENIT restructured. Effects need MGEF resolution. TES5 ingredients have exactly 4 effects. |
| KEYM | KEYM | Add OBND. Minor changes. |
| LAND | LAND | Compatible heightmap structure. Texture layers may need LTEX FormID remapping. |
| LIGH | LIGH | Add OBND. Minor changes. |
| LSCR | LSCR | Add OBND. TES5 uses NNAM (loading screen text) instead of ICON+DESC. |
| LTEX | LTEX | **Restructured**: TES4 uses ICON (texture path) + HNAM + SNAM + Grasses. TES5 uses TNAM (→TXST record) + HNAM(→MATT) + SNAM + Grasses. Needs TXST creation. |
| LVLC | LVLN | Leveled Creature → Leveled NPC. Same entry format (LVLO). |
| LVLI | LVLI | Same entry format. Minor flag differences. |
| LVSP | LVSP | Same entry format. |
| MGEF | MGEF | **Major restructuring**: TES4 uses 4-char codes (OBME), FormID-based effects (EFID/EFIT). TES5 MGEF has completely different DATA struct. Magic school → skill enum. |
| MISC | MISC | Add OBND. Minor changes. |
| NPC_ | NPC_ | **Massive restructuring**: ACBS different fields. DATA(33B)→empty marker. Skills/stats→DNAM(52+B). Hair→PNAM(HDPT array). Voice→VTCK(VTYP). Outfits→DOFT/SOFT(OTFT). Perks new. Template system new. Add OBND, keywords. |
| PACK | PACK | **Completely incompatible**: TES4 type-based (Find/Follow/Escort/Eat/Sleep). TES5 procedure-tree based. Must create skeleton records. |
| PGRD | *(skip)* | Path grids replaced by NavMesh (NAVM). Cannot auto-convert. |
| QUST | QUST | **Major restructuring**: DATA(2B)→DNAM(12B). Stages similar but restructured. Objectives are new. Alias system entirely new. VMAD fragments replace SCRI. |
| RACE | RACE | **Massive restructuring**: DATA completely different (30B→128+B). Many new subsystems: behavior graphs, movement types, tints, morph data. HAIR→HDPT, hair color→CLFM. Voice→VTYP records. |
| REFR | REFR | More subrecords in TES5: XLKR (linked refs), activate parents, locations, emittance. |
| ACHR | ACHR | Similar expansion. TES4 ACRE (placed creature) → ACHR. |
| ACRE | ACHR | Placed creature → Placed NPC (ACHR). |
| REGN | REGN | Minor changes. |
| ROAD | *(skip)* | Roads replaced by NavMesh. |
| SBSP | STAT | Subspace has no equivalent. Export as STAT. |
| SCPT | *(skip)* | Scripts must be rewritten in Papyrus. Source exported for reference. |
| SGST | SCRL | Sigil Stone → Scroll (closest equivalent). |
| SKIL | *(skip)* | Skills hardcoded in TES5. Exported for reference. |
| SLGM | SLGM | Add OBND. Minor changes. |
| SOUN | SOUN + SNDR | TES5 splits sound into SOUN (marker) + SNDR (Sound Descriptor with actual data). |
| SPEL | SPEL | **SPIT restructured**: 16B→36B. New fields: Cast Type, Target Type, Cast Duration, Range, Half-cost Perk. Add OBND, keywords. |
| STAT | STAT | Add OBND. Minor changes. |
| TREE | TREE | CNAM restructured. |
| WATR | WATR | DATA→DNAM. Completely different water properties structure. |
| WEAP | WEAP | **DATA restructured**: 32B→10B. Type moves to DNAM. Massive DNAM struct (~100B). CRDT (critical data) new. Add OBND, keywords. |
| WRLD | WRLD | New fields: XLCN, fixed dimensions, various flags. |
| WTHR | WTHR | Cloud system redesigned (layer-based). HDR/lighting data restructured. |

### New TES5 Record Types (May Need Creation)

| Type | Purpose | When Needed |
|------|---------|-------------|
| ARMA | Armor Addon | **Every ARMO record** needs at least one ARMA. Holds the actual mesh models. |
| KYWD | Keyword | Many records need keywords for game systems to work. |
| TXST | Texture Set | LTEX records need TXST instead of ICON paths. NPC_ head textures. |
| FLST | FormID List | Package override lists on NPC_. Quest objective lists. |
| OTFT | Outfit | NPC_ default outfits. |
| VTYP | Voice Type | NPC_ voice assignment. |
| CLFM | Color | Hair color (replaces inline HCLR). |
| LGTM | Lighting Template | Interior CELL lighting. |
| MUSC | Music Type | CELL music (was U8 enum). |
| SNDR | Sound Descriptor | Sound data (SOUN is just a marker in TES5). |
| MATT | Material Type | Landscape material (was HNAM enum). |

## Known Problems and Skipped Records

### Records That Cannot Be Auto-Converted
- **PGRD** (Path Grid) → Must be rebuilt as NAVM (NavMesh) in Creation Kit
- **ROAD** → Replaced by NavMesh system
- **SCPT** (Script) → Papyrus rewrite required (source exported for reference)
- **SKIL** (Skill) → Hardcoded in TES5, no record equivalent
- **BSGN** (Birthsign) → No record type. Spells should go to Race or Standing Stone

### Records With Major Conversion Issues
- **PACK** — TES5 package system is completely different (procedural tree). Only skeleton records can be created.
- **QUST** — Alias system, objectives, and VMAD fragments are all new. Only basic stage data can be transferred.
- **INFO** — Dialog response structure changed significantly. VMAD fragments replace result scripts.
- **NPC_/CREA** — Attribute system removed, skill system changed, many new subsystems (templates, outfits, perks, keywords).
- **RACE** — Almost entirely restructured. Only basic data (height/weight/skill boosts/spells) can transfer.
- **MGEF** — 4-char code system vs FormID system. Flag mapping is complex.
- **ENCH/SPEL** — ENIT/SPIT completely restructured. Effects need MGEF FormID resolution.
- **ARMO/CLOT** — Missing ARMA records means armor won't render in-game.

### Common Causes of ESP Failing to Load in Skyrim Engine
1. **Missing OBND** on records that require it (most item/object types)
2. **Wrong HEDR version** (must be 1.7/1.71, not 1.0)
3. **Wrong form version** (must be 43/44, not 0)
4. **Malformed record structures** — Wrong subrecord sizes (e.g., ENIT 16B instead of 36B)
5. **Invalid FormID references** — Pointing to non-existent records
6. **Missing required subrecords** — Some records crash without certain subrecords
7. **Wrong DATA sizes** — NPC_ DATA must be empty (0B) in TES5, not 33B
8. **CELL DATA flag size** — Must be U16, not U8
9. **Biped slots** — BOD2 (8B) required instead of BMDT (4B)
10. **ARMO without ARMA references** — Engine expects armor models via ARMA indirection

### Cross-File Reference System (Dependent Plugins)
When importing a dependent plugin (e.g., Knights.esp that depends on Oblivion.esm), the pipeline must:
1. **Load converted masters** — convert.ps1 adds converted Oblivion.esm to activePlugins during Knights.esp import
2. **Search all loaded files** — `FindMappedRecord` iterates `FileByIndex(0..FileCount-1)` to find parent records (CELLs, WRLDs, DIALs) in master files
3. **Create overrides for cross-file parents** — `CreateChildRecord` calls `wbCopyElementToFile(parentRec, TargetPlugin, False, False)` when parent is in a master file, which creates an override in the target plugin and automatically adds the master dependency
4. **Register masters in relink phase** — All loaded files are registered via `AddMasterIfMissing` so cross-file FormID references are valid when saved

**Key point**: `RecordByFormID(singleFile, formID, True)` only searches one file. Must loop `FileByIndex` to find records in any loaded file.

### NPC_ DNAM Skill Path Names
TES5 NPC_ DNAM stores skills as arrays. The correct xEdit paths are:
- `DNAM\Skill Values\OneHanded` (not `DNAM\One-Handed`)
- `DNAM\Skill Values\TwoHanded`, `Marksman`, `Block`, `Smithing`, `HeavyArmor`, `LightArmor`, `Pickpocket`, `Lockpicking`, `Sneak`, `Alchemy`, `Speechcraft`, `Alteration`, `Conjuration`, `Destruction`, `Illusion`, `Restoration`, `Enchanting`
- Plus `DNAM\Health`, `DNAM\Magicka`, `DNAM\Stamina` (U16 each)

## Asset Conversion Notes

- **NIF meshes**: Oblivion uses NIF version 20.0.0.4/20.0.0.5 (NetImmerse). Skyrim uses 20.2.0.7 (Gamebryo/BSTriShape). The external NIFConverter subfolder has reference tools.
- **NIF full conversion** (`mesh_convert` package): Performs complete Oblivion→Skyrim NIF conversion:
  1. NiTriStrips → NiTriShape (SE can't render strips)
  2. NiTexturingProperty + NiMaterialProperty → BSLightingShaderProperty + BSShaderTextureSet (Skyrim shader system)
  3. Texture path rewriting (prepend `tes4\` to keep separate from Skyrim assets)
  4. Bone name remapping (Oblivion Bip01 → Skyrim NPC skeleton)
  5. NiNode root → BSFadeNode root (Skyrim's standard root type)
  6. Geometry data finalization (`unknown_int_2 = 8`)
  7. NIF version upgrade (20.0.0.4 → 20.2.0.7, BSStream 83)
  8. bhk block format conversion (Oblivion UV2=11 → Skyrim UV2=83):
     - bhkRigidBody/T: +14 bytes (UnknownInt2 field swap at [44:52], TimeFactor, GravityFactor, RollingFrictionMult, UnknownBytes2, BodyFlags u32→u16)
     - bhkMoppBvTreeShape: +1 byte (BuildType insertion at offset 40)
  9. Orphan block removal (NiMaterialProperty, NiTexturingProperty, etc.)
  10. Oblivion-only block types force-removed (NiVertexColorProperty, NiSpecularProperty, etc.)
  Run: `python -m asset_convert.nif_converter <src_dir> <dst_dir>` (worker pool is automatic: cpu_count-3; there is NO --workers flag).
- **NIF conversion stats**: 8032 source NIFs from Oblivion BSAs. 7380 v20 files converted (91.9%). 650 v10/v4 files copied as-is. 2 remaining parse errors (magic effect particle NIFs).
- **NIF bhk conversion details** (Session 19+):
  - bhkRigidBody/T: Oblivion=236+n*4, Skyrim=250+n*4. Key: two `Unknown Int 2` fields with different vercond (UV2>34 vs UV2≤34). Bytes [44:52] need rearrangement, not just passthrough. Translation/Mass/Friction are at fixed offsets (52, 180, 192 in Oblivion, 52, 180, 200 in Skyrim)
  - Crash signature: SkyrimSE.exe+0A882E6 reading from 0xFFFF* addresses = corrupted bhkRigidBody pointers from misaligned fields
  - bhkNiTriStripsShape: Collision NiTriStripsData must NOT be renamed to NiTriShapeData (template type mismatch). Writer must write strips format, not triangulated.
  - Constraint descriptors (RagdollDescriptor, LimitedHingeDescriptor, HingeDescriptor, MalleableDescriptor): UV2≤16 vs UV2>16 field REORDERING is handled by PyFFI's ver1/ver2-guarded duplicate attrs (same attr names in both layouts, so values carry over automatically on read-Oblivion/write-Skyrim).
  - **Constraint conversion (rewritten 2026-07-04, `collision.py::scale_constraint_pivots`)**: the old code only fixed bhkLimitedHingeConstraint; every other descriptor shipped UNSCALED pivots (10× too far, e.g. UpperScales01 ragdoll pivot 3.57 vs vanilla-range 0.36) and zeroed Skyrim-only basis fields. Now for ALL descriptor types: pivot_a/pivot_b ×0.1 (stiff-spring `length` and prismatic min/max_distance too — they're lengths); RagdollDescriptor `motor_a/motor_b` = twist × plane (they are the 3rd column of the constraint's orthonormal basis, NOT motor params — zero = singular basis; handedness verified on vanilla desecratedimperial.nif); HingeDescriptor Skyrim-only `axle_a` = perp_a1 × perp_a2 and `perp_2_axle_in_b_1/2` = Gram-Schmidt complement of axle_b (plain hinge has no limits so any orthonormal complement is valid); inertia ×0.1 rescale deduped per body (the scales crossbar sits in 3 constraints — was being triple-scaled). Vanilla Skyrim constraint census (17,216 meshes): LimitedHinge 158, Ragdoll 59, Hinge 3, StiffSpring 2, **Malleable 0, Prismatic 0** → bhkMalleableConstraint is demoted to a plain constraint of its inner SubConstraint type (`_demote_malleable_constraints`; strength/tau/damping dropped); bhkPrismaticConstraint (Oblivion arrows only) is kept best-effort with a note that vanilla never ships it. Oblivion source census: LimitedHinge 278, Ragdoll 60, Malleable 21, Prismatic 10, Hinge 4, StiffSpring 3.
  - **KNOWN REMAINING bhkRigidBodyT+CMS violations (2026-07-04)**: 5 converted ANIMATED meshes still ship the forbidden pair (dungeons\ayleidruins\interior\traps\artrapspikepit01, dungeons\caves\cdoor03, dungeons\sewers\sewertunneldoor01, oblivion\clutter\traps\citadelhall3wayspiketrapbroken, oblivion\gate\obliviongate_simple) — keyframed child-node collision can't be demoted by the static bake pass; needs its own fix. ~100 speedtrees/ shrub+tree NIFs also contain the pair but are pre-made Skyblivion assets copied verbatim (not produced by our converter). Find them with `python tools/nif_block_scan.py <dir> --has bhkRigidBodyT --has bhkCompressedMeshShape`.
  - `asset_convert/mopp.py::walk_mopp()` is a full MOPP VM symbolic walker (PyFFI's parse_mopp opcode table + Skyrim-era opcodes: 0x52 TERM24, 0x29-0x2B DOUBLE_CUT24, 0x70 CHUNK_JUMP32), validated clean against 400 vanilla meshes. CLI: `python tools/mopp_validator.py <nif_or_dir> [--verbose|--summary|--histogram|--workers N]` (validates walk cleanliness AND exact terminal-key-set == shape-key decode). Vanilla opcode set observed: 0x01-0x06, 0x09-0x0B, 0x10-0x1C, 0x20-0x28 (0x29-0x2B rare), 0x30-0x53 — never 0x07/0x08/0x70; emit only these.
  - **MOPP_RL.exe is GONE (2026-07-03): all mesh collision is built by `asset_convert/cms_builder.py`**. History: MOPP_RL's chunked bytecode (0x70 chunk jumps, PC engine mis-executes) was first dechunked (`mopp.py::dechunk_mopp`), then its bytecode was replaced wholesale with Havok-bridge output — and the intermittent CTD STILL persisted (crash `SkyrimSE.exe+07D4C4B` fn 43870, runaway `hkpAllCdPointTempCollector` scan → EXCEPTION_STACK_OVERFLOW; Collision Sentinel: `CULPRIT ... key=0xFFFFFFFF` on the same meshes). Root cause was never the bytecode (see bhkRigidBodyT bullet below). MOPP_RL, its template.nif, and the dechunk fallback are all removed from the pipeline; `dechunk_mopp` remains in mopp.py for forensics only.
  - **CMS collision is built in pure Python + real Havok (2026-07-03)**: `cms_builder.py::build_cms_collision(tris_hu, sk_material_crc, NifFormat)` builds the whole bhkMoppBvTreeShape→bhkCompressedMeshShape→bhkCompressedMeshShapeData chain from a triangle soup: bpi=17/bpw=18, error=0.001, one identity bhkCMSDTransform, chunk = spatial bucket (split until extent <60 hu, ≤2000 tris), chunk translation = bucket min corner, u16 offsets = (v−min)×1000, triples-only indices (num_strips=0 — engine key decode identical to strips, `key=(ci+1)<<18|offset`), tris larger than the u16 span → big tris. MOPP bytecode + TWO_SIDED welding come from `asset_convert/dovah_hkp_mesh_mopp_bridge.exe` (Havok's real `hkpMoppUtility::buildCode`, chunk subdivision off, terminal keys self-validated by Havok's find-all-keys VM) — bridge input is `decode_cms()` of the freshly built block so MOPP/welding are computed over the exact quantized geometry the engine will decode. Welding u16 goes at the tri's first-index slot in chunk `indices_2` (= key offset); big-tri welding in `unknown_short_1`. Output re-verified in Python (walk clean + keys == `predict_keys`). Constants mirrored from vanilla: CMS radius=0.005, unknown_float_1=0.005, scale vec (1,1,1,0), data unknown_int_3=1, chunk unknown_short_1=0xFFFF, material layer=1. Wired in `collision.py::_rebuild_mesh_collision` (handles strips/packed/stale-Oblivion-MOPP sources; strips verts are GAME units → ÷70 to Skyrim hu; packed verts ×0.1). Fallback when the bridge fails: bare `_packed_from_tris` (no MOPP; packed data verts are stored ×10 hu = 1/7 game scale). NaN-vert tris are filtered before building.
  - **The MOPP bridge exe** came from inside `tools/DovahNifWorkbench_v6_47.exe` (PyInstaller onefile; payload `backend_exact_mopp\dovah_hkp_mesh_mopp_bridge.exe` + full C++ source `native_hkp_mesh_mopp_bridge/`, re-extractable by parsing the CArchive TOC at the `MEI\014\013\012\013\016` cookie). CLI: `--input in.json [--output report.json] [--no-stdout]`; input JSON `{"vertices":[x,y,z,...], "triangles":[a,b,c,...], "shape_keys":[k,...]}` (keys optional, must be unique); report has `mopp_origin`, `mopp_scale`, `mopp_data_hex`, `welding_info` (TWO_SIDED, per source tri), `mopp_keys_match_shape_keys`. GUI batch mode is NOT needed — the exe is called per-shape by cms_builder.py (`run_mopp_bridge`).
  - **CMS shape-key encoding (validated 200/200 vanilla meshes: walked MOPP key set == predicted set — `asset_convert/cms.py::decode_cms/predict_keys`)**: chunk tri key = `(chunk_idx+1) << bitsPerWIndex | winding << bitsPerIndex | first_index_offset` where the offset is the tri's first index position in the chunk's indices array; strips yield sliding-window tris (winding = window ordinal parity within the strip), then remaining indices are independent triples (winding 0, stride 3); big tris = part 0, key = big-tri index. Chunk vertex = chunk.translation + transform.translation + u16/1000 (rotate by transform quat if non-identity). PyFFI 2.2.3 field quirks: chunk welding array = `indices_2`, big-tri welding = `unknown_short_1`, big-tri fields `triangle_1/2/3` index into `big_verts`.
  - **PyFFI parse_mopp 0x0B (TERM_REOFFSET32) is WRONG** ("unsure about first two arguments" — reads only operand bytes 3-4): the operand is a full 32-bit big-endian value that SETS the terminal offset, and Skyrim CMS keys carry the chunk part in the HIGH bytes (0x00040000 = chunk 0). With the 2-byte read, every terminal after a 0x0B loses its chunk part — this made valid keys look like out-of-range "big tri" keys (a red herring chased for hours; vanilla showed the identical false pattern, which is what exposed the walker bug). Fixed in `walk_mopp`. Welding values legitimately span the full u16 range incl. ≥0x8000 and 0xffff — NOT a corruption signal (vanilla does the same).
  - `bhkCompressedMeshShape.target` must point to the BSFadeNode root (identity transform). Static collision MUST be on the root BSFadeNode — having bhkCollisionObject on a child NiNode causes STACK_OVERFLOW in Skyrim's `hkpCollisionDispatcher`.
  - **bhkRigidBodyT + CMS/MOPP = intermittent CTD — THE AnvilCastleGreatHall root cause (2026-07-03)**: vanilla Skyrim NEVER pairs a transformed rigid body with CompressedMesh collision — **0 of 6,341 vanilla CMS meshes contain bhkRigidBodyT** (checked by binary grep — block type names are plaintext in NIF headers). Shipping one exercises an engine path Bethesda never tested: queries intermittently resolve to HK_INVALID_SHAPE_KEY (Collision Sentinel `key=0xFFFFFFFF`) → runaway `hkpAllCdPointTempCollector` scan (Sentinel EVENT `b=129` vs the 128-slot stack collector) → EXCEPTION_STACK_OVERFLOW at `SkyrimSE.exe+07D4C4B`. Every Sentinel CULPRIT was a rotated-root mesh whose wrap pass produced bhkRigidBodyT+CMS ("diagonal/curved architecture" pattern). This explains all earlier observations: identity-body configs never crashed (only had rotated collision); transformed-body configs (bodyT OR collision on rotated child node) crashed ~50%. Replacing the MOPP bytecode alone did NOT fix it — the bytecode was never the problem.
  - **Root rotation wrap + collision (final design 2026-07-03)**: when the wrap pass zeroes the root transform L=(R,T), `bake_node_transform_into_body()` still composes bodyT' = L ∘ bodyT (in Oblivion hu; PyFFI `m_ij` names are the TRANSPOSE of the engine's column-vector matrix; rotation is QuaternionXYZW; ×0.1 rescale happens in `_convert_collision`). But for MESH collision the transform never reaches the file: `_bake_body_transform_into_tris()` applies the final bodyT to the triangle soup and DEMOTES the body back to a plain identity bhkRigidBody (class swap) before `build_cms_collision` runs — the output matches vanilla exactly (identity plain body, geometry in the world frame). Collision stays on the root BSFadeNode; CMS target = root. Regression test: `TestCollisionTargetPointsToRoot::test_static_collision_stays_on_root_when_wrapped` (asserts plain identity body + decoded CMS centroids match the source collision in the L∘bodyT frame within quantization — catches conjugate/transpose convention errors). Primitive shapes (convex/box/capsule, incl. constrained sign bodies) legitimately keep bhkRigidBodyT — vanilla does too.
  - **NaN geometry = silent cell-load CTD, NO crash log (2026-07-04, the AnvilMagesGuild/AnvilCastlePrivateQuarters root cause)**: some Oblivion source meshes ship non-finite floats in RENDER geometry (anvildooruc02.nif: 9 NaN UVs; middlecandlestickfloor03fake.nif: 2 NaN UVs — exactly one such mesh in each crashing cell, found by intersecting `tools/cell_meshes.py` output with a `tools/collision_sanity.py --geometry` sweep). Oblivion's renderer tolerated them; SSE dies at cell load WITHOUT writing a crash log (fail-fast, not a loggable exception) — collision was never involved. Fixed by `_sanitize_geometry_data()` in nif_converter.py (runs right after `_resolve_palette_strings`, BEFORE tangent computation/skin retarget so NaNs can't propagate): NaN UVs→0, NaN verts→finite centroid (+ bound-sphere recompute), NaN normals/tangents→+Z, NaN vertex colors→1. NOTE: the PyFFI warning summary from a full conversion run showed `nan_in_vertices: 155` — other meshes in the tree carry NaN too and previously shipped unsanitized; a full mesh reconversion (pipeline now sanitizes) or a `collision_sanity.py --geometry` sweep of output finds/fixes the rest.
  - NiParticleSystem: NiGeometry body needs format conversion (MaterialData→NumMaterials, Properties removed for UV2>34, FarBegin/End added for UV2≥83). IMPLEMENTED — `_convert_particle_system()` creates fresh NiPSysData with `bs_max_vertices = max(old_num_vertices, 75)`, keeps all modifiers, sets `base_scale=1.0` on NiPSysGrowFadeModifier.
- **PyFFI 2.2.3 version-condition bugs**: PyFFI's nif.xml has WRONG version conditions for some fields. Must monkey-patch at import time:
  - `NiPSysGrowFadeModifier.base_scale`: PyFFI has `userver="11"` (exact match on user_version=11). Correct condition per newer nif.xml: `User Version 2 >= 34`. Since we write `user_version=12` (Skyrim), PyFFI silently skips the field. Fix: set `_attrs[base_scale].userver = None` in monkey-patch.
  - Without the fix, `base_scale` defaults to 0.0 → particles invisible (scale = 0 × grow = 0).
  - The `bhkMoppBvTreeShape.build_type` field's vercond (`user_version >= 12`) is correct and does NOT need patching.
- **NIF reference docs**: NifSkope nif.xml at `external/NifSkope Built/nif.xml`, NifSkope HTML docs at `external/NifSkope Built/doc/`, NifSkope source at `external/nifskope-2.0.dev7/src/`
- **NIF BSStream versions**: 83 = Skyrim LE, 100 = Skyrim SE optimized. SE can load BSStream 83 files with NiTriShape geometry.
- **DDS textures**: Oblivion uses DXT1/DXT3/DXT5. Skyrim SE uses BC7/BC5/BC1 compression. May need re-export.
- **BSA archives**: Oblivion BSA format differs from Skyrim BSA. Need re-packing.
- **File paths**: The export prepends `tes4\` to all asset paths to avoid conflicts with Skyrim's own assets.

## Actor Value / Skill Mapping

| TES4 Skill (Index) | TES5 Skill (Index) | Notes |
|---------------------|---------------------|-------|
| Armorer (12) | Smithing (10) | |
| Athletics (13) | *(none)* | Removed in TES5 |
| Blade (14) | One-Handed (6) | |
| Block (15) | Block (9) | |
| Blunt (16) | One-Handed (6) | Merged with Blade |
| Hand to Hand (17) | One-Handed (6) | Merged with Blade |
| Heavy Armor (18) | Heavy Armor (11) | |
| Alchemy (19) | Alchemy (16) | |
| Alteration (20) | Alteration (18) | |
| Conjuration (21) | Conjuration (19) | |
| Destruction (22) | Destruction (20) | |
| Illusion (23) | Illusion (21) | |
| Mysticism (24) | Illusion (21) | Merged with Illusion |
| Restoration (25) | Restoration (22) | |
| Acrobatics (26) | *(none)* | Removed in TES5 |
| Light Armor (27) | Light Armor (12) | |
| Marksman (28) | Archery (8) | |
| Mercantile (29) | Pickpocket (13) | Approximate |
| Security (30) | Lockpicking (14) | |
| Sneak (31) | Sneak (15) | |
| Speechcraft (32) | Speech (17) | |

TES4 Attributes (Strength, Intelligence, etc.) have no TES5 equivalent. Health/Magicka/Stamina are derived from TES4 attributes for NPC conversion.

## Weapon Type Mapping

| TES4 Type | TES5 Animation Type | Notes |
|-----------|---------------------|-------|
| 0 (Blade 1H) | 1 (Sword) | |
| 1 (Blade 2H) | 5 (Greatsword) | |
| 2 (Blunt 1H) | 4 (Mace) | |
| 3 (Blunt 2H) | 6 (Battleaxe) | |
| 4 (Staff) | 8 (Staff) | |
| 5 (Bow) | 7 (Bow) | |

## Biped Slot Mapping

| TES4 Slot (Bit) | TES5 Slot (Bit) | Name |
|------------------|------------------|------|
| 0 (Head) | 0 (30-Head) | |
| 1 (Hair) | 1 (31-Hair) | |
| 2 (Upper Body) | 2 (32-Body) | |
| 3 (Lower Body) | 2 (32-Body) | Merged with upper |
| 4 (Hand) | 3 (33-Hands) | |
| 5 (Foot) | 7 (37-Feet) | |
| 6 (Right Ring) | 6 (36-Ring) | |
| 7 (Left Ring) | 6 (36-Ring) | Merged |
| 8 (Amulet) | 5 (35-Amulet) | |
| 13 (Shield) | 9 (39-Shield) | |
| 15 (Tail) | 13 (43-Tail) | |

## Enchantment Type Mapping

| TES4 Type | TES5 Type | Notes |
|-----------|-----------|-------|
| 0 (Scroll) | 6 (Enchantment) | |
| 1 (Staff) | 12 (Staff Enchantment) | |
| 2 (Weapon) | 6 (Enchantment) | |
| 3 (Apparel) | 6 (Enchantment) | |

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

### Legacy (PowerShell + xEdit)
```powershell
.\convert.ps1                       # Full pipeline for all configured files
.\convert.ps1 -Files "Knights.esp"  # Single file (masters must be cached)
.\convert.ps1 -NoCache              # Force re-export everything
.\convert.ps1 -ExtractAssets        # Extract BSAs + convert meshes/textures
```

### Manual
1. Export: `python -m tes4_export.export "path/to/Oblivion.esm" --outdir export/Oblivion.esm`
2. Import: `python -m tes5_import export/Oblivion.esm -o output/Oblivion.esm -m Skyrim.esm`
3. Validate: Open output in SSEEdit to check for errors
4. Manual fixup: ARMA creation, keyword assignment, MGEF resolution, package rebuilding, Papyrus scripting

## Asset Pipeline

The `-ExtractAssets` flag triggers BSA extraction and mesh conversion:

1. **BSA Extraction** — Uses `bsab.exe` (from external/fnv-to-fo4/bin/bsab/) to extract meshes and textures from Oblivion BSA archives
2. **Mesh Conversion** — Uses PyFFI-based NIFConverter (from external/NIFConverter/) to convert Oblivion NIF 20.0.0.4/5 → Skyrim NIF 20.2.0.7
3. **Texture Copy** — DXT textures from Oblivion are compatible with Skyrim; copied as-is under `tes4\` namespace
4. **BSA Repacking** — Not yet automated. Use BSArch.exe or Skyrim CK Archive tool.

### Prerequisites for mesh conversion
- Python 3.x
- PyFFI (`pip install PyFFI`)
- `asset_convert/dovah_hkp_mesh_mopp_bridge.exe` (checked in — Havok MOPP/welding compiler)

### BSA naming conventions (Oblivion)
- `Oblivion - Meshes.bsa`, `Oblivion - Textures - Compressed.bsa`
- `DLCShiveringIsles - Meshes.bsa`, `DLCShiveringIsles - Textures.bsa`
- `Knights.bsa` (single BSA for smaller DLCs)

## LAND Record Structure

Both TES4 and TES5 use `wbLandscapeLayers` from wbDefinitionsCommon.pas. The "Layers" array is a FLAT array of Layer entries where each is EITHER a Base Layer (BTXT) OR an Alpha Layer (ATXT+VTXT) — they are NOT nested.

### Export Format
```
LayerCount=N
Layer[i].Type=BASE|ALPHA
Layer[i].BTXT.Texture=FormID    # BASE only
Layer[i].BTXT.Quadrant=0-3      # BASE only
Layer[i].ATXT.Texture=FormID    # ALPHA only
Layer[i].ATXT.Quadrant=0-3      # ALPHA only
Layer[i].ATXT.Layer=N            # ALPHA only
Layer[i].VTXTCount=K             # ALPHA only
Layer[i].VT[k].Pos=posval        # ALPHA only
Layer[i].VT[k].Op=opval          # ALPHA only
VTEXCount=N
VTEX[i]=FormID
```

### Import Notes
- `ElementAssign(layers, HighInteger, nil, False)` creates a default Base Layer (BTXT)
- For Alpha Layers: remove BTXT via `RemoveElement`, then add ATXT + VTXT
- VTXT structured data only available when `wbSimpleRecords = False`; raw byte array otherwise
- **Alpha layer numbers must be per-quadrant sequential (0,1,2…), NOT the TES4 original values**
- **Skip alpha layers with Texture FormID = 0** — they cause visual artifacts in TES5
- **Max 8 alpha layers per quadrant** in TES5. Skyblivion uses 5 but engine supports 8.
- VTXT export field is `VT[k].Op` but import uses `VT[k].Opacity` — use Opacity in import
- Exterior cell block grouping: block = `floor(grid / 32)`, sub-block = `floor(grid / 8)`. Use Python `//` (floor division), NOT bitwise `>>` — the `>>` formula is wrong for exact negative multiples (e.g. -32 gives -2 instead of -1).
- Persistent worldspace cell classification: use `RecordFlags & 0x400`, NOT `XCLC.X == ''`. Persistent cells often have XCLC=(0,0) so the empty-string check mis-classifies them as exterior cells, putting them in the wrong block/sub-block structure and breaking all exterior cell loading.

### TXST for Landscape Textures
- No DNAM: vanilla Skyrim LTEX TXSTs omit DNAM. The 0x0001Fa "No Specular Map" flag only applies to the object (BSLightingShader) path, NOT the landscape shader. Writing it has no positive effect.
- TX00 = diffuse (`tes4\landscape\<icon>.dds`)
- TX01 = normal map (`tes4\landscape\<icon>_n.dds`)
- LTEX SNAM specular exponent: **pass through the TES4 value**. SNAM is a Phong exponent used directly by the landscape shader. Setting SNAM=0 gives `pow(NdotH, 0) = 1.0` everywhere → whole landscape appears blindingly bright white. TES4 landscape textures use ~30 (moderate gloss). Do NOT write SNAM=0.

### Dialogue conversion notes (DIAL / INFO / QUST / DLBR / DLVW)
- **Skyrim dialogue architecture**: QUST owns DIAL topics → DLBR branches link DIAL to QUST → INFO records have GetIsVoiceType conditions → engine routes dialogue by voice type
- **Per-quest topic ownership (2026-07 design)**: Single-quest topics → owned by their original quest (remapped QNAM); Skyrim then only evaluates their INFOs while that quest runs = Oblivion's QSTI gating, natively. Shared (multi-QSTI) or quest-less topics → always-running `TES4DialogueGeneric` quest, and each INFO whose own QSTI.Quest is non-SGE gets an injected GetQuestRunning(own quest) gate (Oblivion evaluates INFOs per-INFO-quest; vanilla Skyrim models shared subjects as one DIAL per quest — 288 separate HELO topics).
- **TES5 DIAL subrecord order**: EDID FULL PNAM(float priority=50.0) [BNAM(branch FID)] QNAM(quest) DATA(4B) SNAM(4-char code U32) TIFC(info count U32)
- **DIAL DATA** = TopicFlags(U8) + Category(U8) + Subtype(U16 LE) = 4 bytes total. **CRASH WARNING (verified 2026-07)**: writing subtype in byte1/category in the U16 puts an out-of-range value where the engine reads category → it indexes per-category topic tables out of bounds → EXCEPTION_ACCESS_VIOLATION at startup while initializing topics (crash log shows the TESTopic* + owning TESQuest*). Verified vs vanilla: Hello = `00 07 49 00` (cat 7, subtype 73).
- **DIAL subtype NUMBERS**: take from real Skyrim.esm data, NOT xEdit's display enum (which is shifted ~+6; the field is cpIgnore in xEdit and synced from SNAM). Real: Hello=73 GBYE=72 IDLE=88 ATCK=20 HIT_=23 FLEE=24 BLED=25 BLOC=29 TAUT=30 STEA=32 ASSA=36 MURD=37 TRES=43 NOTA=51 LOTN=57 OBCO=69 NOTI=70 TITG=71 IDAT=84. SNAM is what the engine keys subtype behavior on.
- **DIAL SNAM** = 4-char subtype code stored as raw ASCII bytes (e.g. b'HELO', b'CUST')
- **TES4→TES5 CTDA function reconciliation (data-verified 2026-07)**: joined both xEdit function tables — same-index-same-name functions pass through (incl. 50 GetTalkedToPC, which IS in TES5; an older drop of it was wrong). Renames at same index (128 GetFatigue→GetStaminaPercentage, 215, 327, 339 Horse→Mount) pass through. Remap: 101→263 IsWeaponOut, 116→459 GetCrimeGold, 127→497 CanPayCrimeGold. Vanilla CTDA tail = runOn 0, reference 0, param3 -1. TES4 type-bit 0x02 (Run on Target) → TES5 RunOn field =1 + CLEAR the bit (in TES5 that bit means "use aliases").
- **DIAL Category**: Bark topics → 7(Misc) or 3(Combat) or 5(Detection); all conversation topics → 0(Topic). Old TES4 type-based mapping removed.
- **DIAL BNAM**: Present on ALL non-bark conversation topics (links to DLBR). Bark topics must NOT have BNAM. TCLT target topics ALSO get BNAM — vanilla Skyrim requires BNAM on ALL CUST topics for the engine to route dialog to them.
- **Branch level (DNAM) rule (2026-07)**: a TCLT-target topic that is NEVER explicitly AddTopic'd is choice-only in Oblivion (nothing ever adds it to the menu) → Normal branch (DNAM=0), e.g. Azzan's "Yes. Sign me up."/"I'm not interested." and FGC01Choice1 "It was a mountain lion." — top-level branches on these leak player choice lines into the topic menu. TCLT targets that ARE explicitly added stay top-level + unlock-gated, with their TCLT-parent INFOs as revealers.
- **INFO order within a topic = quest priority (2026-07)**: Oblivion picks the first passing INFO in QUEST PRIORITY order (desc), not file order; Skyrim walks the topic's physical INFO list. Converted topics must sort children by their own QSTI quest's DATA.Priority (stable). Without this, Azzan's priority-11 first-meeting intro greeting outranks the priority-60 FG-ad greeting that reveals jointheFightersGuild/FightersGuildTopic → the join topics never unlock.
- **Quest journal visibility / markers (2026-07)**: QUST DNAM Type=0 (None) is Skyrim's journal-INVISIBLE control-quest type — the quest never lists in the journal, can't be tracked, and objective targets never produce compass/map markers even when objectives+QSTA+aliases are perfect (vanilla: only 16/~396 objective-bearing quests are Type 0). Converted quests with journal stage text get Type=8 (Side Quest).
- **TES4 DIAL DATA.Type classification**: Type 0=Topic (top-level, gets DLBR DNAM=1), Type 1=Conversation (chain topic, reachable via TCLT links, gets DLBR DNAM=0/Normal), Type 2=Combat (bark), Type 3=Persuasion (skipped), Type 4=Detection (bark), Type 5=Service (skipped), Type 6=Misc (bark). Type 1 is NOT excluded from DLBR — they need Normal branches for TCLT routing.
- **DLBR (Dialog Branch)**: EDID + QNAM(quest FID) + TNAM(0=Player) + DNAM(0=Normal or 1=TopLevel) + SNAM(starting DIAL FID). Created for ALL non-bark DIAL topics. Top-level topics get DNAM=1 (appear in dialog menu). TCLT chain topics get DNAM=0 (only reachable via TCLT choice links, not shown in menu).
- **DLVW (Dialog View)**: EDID + QNAM(quest) + BNAM[](branch FIDs) + TNAM[](topic FIDs) + ENAM(view type) + DNAM(show all text). CK UI metadata, one per quest.
- **GetIsVoiceType (func 426)**: Every Skyrim INFO must have this condition. Routes dialogue to the correct voice type. OR'd for multiple voice types. NPC-specific INFOs use GetIsID(npc_fid) from TES4 + GetIsVoiceType for the NPC's voice type. Generic fallback INFOs (no GetIsID) inherit voice types from NPC-specific siblings in the same topic — this prevents conditionless INFOs from making topics appear for ALL NPCs.
- **CTDA OR flag**: bit 0 of type byte. Voice type chain: VT1(OR)|VT2(OR)|...|VTn(AND) → evaluates as (any voice type matches) AND (remaining TES4 conditions). LAST voice type CTDA must NOT have OR flag.
- **Voice type injection order**: Voice type CTDAs are injected BEFORE TES4-converted conditions in INFO. This isolates the OR chain from any trailing OR flags in TES4 data.
- **QUST dialogue flags**: QUSTs that own DIAL topics get: 0x0001(StartGameEnabled) + 0x0010(StartsEnabled) = 0x0011. **NEVER set HasDialogueData (0x8000)** — Skyrim does not use this flag and it blocks dialogue processing.
- **QUST DNAM format**: 12 bytes: Flags(U16) + Priority(U8) + FormVer(U8=**0 always**) + Unknown(4B) + Type(U32). FormVer must be 0 (not 44). Dialogue quest priority capped at 50.
- **Orphan DIALs**: DIALs without quest association get assigned to a catch-all quest `TES4DialogueGeneric` (Flags=0x0011, Priority=0, FormVer=0). ALL DIALs MUST have QNAM or the engine ignores them.
- **NPC→VTYP mapping**: Built from NPC_+CREA records using TES4_RACE_FID_TO_EDID → VOICE_TYPE_MAP[(race_edid, gender)]. 3396 NPCs mapped to 27 voice types. ALL speakable NPCs (including CREA→NPC_) MUST have VTCK.
- **TES4-only condition functions dropped**: GetDisposition(76), GetVampire(40), IsYielding(104), IsPlayerInJail(171), GetPCInfamy(251) — these would always fail in TES5 and block valid dialogue.
- **Quest-dependent conditions on barks**: Functions {53(GetScriptVariable), 56(GetQuestRunning), 58(GetStage), 59(GetStageDone), 79(GetQuestVariable), 99(GetQuestCompleted)} are identified as quest/script-dependent (always return 0/False since TES4 scripts don't run in TES5). Currently PRESERVED on bark INFOs (not stripped) — per-quest ownership prevents wrong topics; fallback greetings provide baseline greeting functionality. Function 79 is GetQuestVariable (NOT GetIsPlayerBirthsign — that's function 224).
- **Location/AI conditions on barks**: GetInCell(71) and GetCurrentAIProcedure(67) are PRESERVED on bark INFOs — they provide critical location and AI-state filtering. FormIDs are properly remapped, so these conditions work in TES5. Stripping them causes city-specific greetings to fire everywhere.
- **CTDA Use Global flag**: bit 2 (0x04), NOT bit 5 (0x20). Wrong bit causes all global-based conditions to fail.
- **TES5 INFO subrecord order**: EDID ENAM CNAM [TCLT[]] [TRDT NAM1 NAM2 NAM3]* CTDAs
- **INFO ENAM** = Flags(U16) + ResetHours(U16) = 4 bytes. Flags map from TES4 DATA.Flags with compatible mask 0x37 (bits 0=Goodbye, 1=Random, 2=SayOnce, 4=InfoRefusal, 5=RandomEnd — same bit positions)
- **INFO TRDT** = 24 bytes: EmotionType(U32) + EmotionValue(U32) + Unused(4) + ResponseNumber(U8) + Unused(3) + Sound(FormID=0 U32) + Flags(U8) + Unused(3)
- **INFO CNAM** = Favor Level U8 (0=None, required)
- **INFO TCLT** = repeated FormID subrecords for each choice/next topic (xEdit: "Link To" array)
- **QUST INDX** = StageIndex(U16) + StageFlags(U8) + Unknown(U8) = 4 bytes (NOT 2 bytes like TES4)
- **Quest markers (targets, 2026-07)**: TES4 QSTA is quest-level (REFR FormID + flags + GetStage-bound conditions). TES5 QSTA is per-OBJECTIVE: struct Alias(i32)+Flags(U8)+3 unused, followed by CTDAs; markers show while a DISPLAYED objective has a passing target. Conversion: one forced-ref alias per unique target (ALST id, ALID, FNAM=0x109A — **Optional 0x0002 is mandatory or a fill failure silently blocks quest start**, +AllowReuse/AllowDead/AllowDisabled/AllowDestroyed —, ALFR remapped ref, ALED), ANAM = alias count, and every objective carries every target WITH its converted conditions (the GetStage bounds gate the marker per stage at runtime, exactly like Oblivion). Objectives: ONE per stage index (engine keys by index; index = stage so `SetObjectiveDisplayed(stage)` in the generated stage fragments matches). Record layout order: stages, objectives(QOBJ FNAM NNAM QSTA+CTDAs), ANAM, aliases.
- **QUST stage log entries**: exported as `Stage[i].LogCount + Stage[i].Log[j].{Flags,Text}`; imported with one QSDT (U8) + optional CNAM (string) per log entry
- **Voice files (naming verified vs vanilla Voices BSA, 2026-07)**: Skyrim resolves `Sound\Voice\<plugin>\<VoiceTypeEDID>\<prefix>_<fid8>_<n>.fuz|.xwm` at runtime from the CONVERTED records. `prefix` = owning-quest EDID + topic EDID with these truncation rules (empirically fitted over ~54K vanilla filenames): topic EDID present → `quest[:10]_topic[:25-len(questpart)]` (combined cap 26 — topic gets the slack when quest < 10 chars); topic EDID empty → quest UNCUT + `_` (double underscore before FormID). `fid8` = 8 lowercase hex with load-order byte ZEROED (never shifted); `n` = TRDT response number. Implemented as `dialog_converter.voice_file_prefix()`. The importer writes `<esm>.voicemap.txt` ({info fid24 → prefix}) and `organize_voice_files(voice_map=...)` renames by FormID — the Oblivion filename prefix CANNOT be trusted (different quest ownership + different truncation). Format conversion: ffmpeg→WAV→xWMAEncode→XWM (no ASF fallback).
- **Race→VoiceType mapping** is in `_TES4_VOICE_TYPE_MAP` in bsa_extract.py; includes all Oblivion playable races + Shivering Isles races
- **Conversion stats**: 3817 DIAL topics (851 barks, 2966 conversation), 19278 INFOs, 954 DLBR branches, 1 DLVW view, 2908 quest-owned conversation topics, 27 fallback greetings
- **Dialog filtering stats**: 18,761 INFOs with conditions, 20 conditionless (down from 958 before voice type fallback fix). 17,784 INFOs with GetIsVoiceType. 3,704 GetInCell CTDAs (preserved for location gating). 3,169 DLBR branches (555 Type 1 chain topics excluded). 9,365 INFOs quest-gated with GetQuestRunning (non-SGE QSTI quests).
- **Quest running gating (QSTI restoration, 2026-07 design)**: In Oblivion, each INFO only shows while its OWN `QSTI.Quest` is running. Single-quest topics get this natively via quest ownership. For shared topics (owned by TES4DialogueGeneric), `_build_one_topic()` injects `GetQuestRunning(info's own QSTI.Quest)==1.0` as the FIRST CTDA on each INFO whose quest is non-SGE and ≠ the topic owner. **Gate by the INFO's OWN quest, never the DIAL's Quest[0]** — gating all of GREETING's children by one arbitrary Quest[0] blocks ALL greetings (a hard-won earlier lesson). SGE quests are exempt (running from new game via the .seq file).
- **AddTopic unlock system (2026-07)**: Oblivion's CENTRAL visibility mechanic — a topic only appears once ADDED via an INFO's Add-Topics data list (export: `AddTopic[i]=` FormIDs, 1044 INFOs), an `AddTopic X` result-script command, a quest-stage script, or automatically when a spoken line's text mentions the topic's FULL name (Oblivion highlights + auto-adds mentioned names). Skyrim has no AddTopic → re-expressed via `tes5_import/dialog_unlocks.py`: one GLOB `TES4Unlock_<topic>` per gated topic (206); every INFO of a gated topic gets `GetGlobalValue(GLOB)==1` (func 74, same both games); every reveal event sets the global from a Papyrus fragment (INFO fragments fire OnEnd; reveal-only INFOs get a generated TIF fragment with just the SetValue call). The plan is built identically by the importer (GLOBs, conditions, VMAD property bindings) and script_convert/pipeline (fragment .psc bodies) — keys are low-24 FormIDs so it's load-order-offset independent. Gating rules (each violation caused a real in-game bug):
  - Gate ONLY topics explicitly added somewhere; mention-only topics stay ungated (name-match miss = dead content).
  - **Topics revealed by BARK lines (GREETING/HELLO) are NOT gated** — the bark fires on first contact, so in Oblivion they're effectively visible on first talk (Azzan's "Join the Fighters Guild" via his FG-ad greeting). Gating them makes topics go missing (fragment races the menu / a different greeting plays). 409 of 615 explicit targets are bark-revealed → 206 gated.
  - Gated TCLT targets keep the gate; their TCLT-parent INFOs are added as revealers.
  - Example that must stay gated: contract INFO (0003571C) lists AddTopic[0]=ratsTOPIC → TES4_TIF__0003571C sets TES4Unlock_ratsTOPIC OnEnd → "Rats" appears only after the contract line. Quest-running does NOT hide it — FGC01Rats starts at guild join (FGD00JoinFG stage 100 `StartQuest` → `.Start()` fragment).
- **'AnswerStatus' and 'TRANSITION'** are Oblivion NPC-to-NPC conversation system topics — classify as barks (IDLE/88/cat 7) or they leak into player topic menus.
- **Dialog emulator caveats**: tools/dialog_emulator.py does not model DLBR branch levels (Normal-branch choice-only topics still print) or GetGlobalValue (its func-74 mapping is wrong: 74=GetGlobalValue, GetFactionRank is 493 in TES5).
- **GetIsID injection (conversation topic NPC restriction)**: Oblivion uses `AddTopic` script command to control which NPCs show conversation topics. Skyrim has NO AddTopic — topics appear if quest is running and conditions match. Since all dialogue quests are StartGameEnabled, conversation topics without NPC-specific conditions appear on ALL NPCs. Fix: inject `GetIsID(npc_fid)==1.0` OR chains into INFOs lacking positive GetIsID, using sibling INFOs in the same topic as donor. Two-tier approach:
  1. **Topic-level**: `collect_topic_npc_fids()` gathers NPC FormIDs from positive `GetIsID(X)==1.0` conditions in sibling INFOs within the same DIAL topic. Injected into INFOs that lack positive GetIsID via `build_topic_npc_ctdas()`.
  2. **Quest-level fallback**: For ALL_CONDITIONLESS topics (every INFO in the topic lacks GetIsID), collect NPCs from ALL topics in the same quest. Handles cases like "Mother" topic in MS45 where the topic itself has no GetIsID but other MS45 topics identify the relevant NPC (Seed-Neeus).
  - OR chain pattern: `GetIsID(A) OR | GetIsID(B) OR | ... | GetIsID(N) AND` — injected BEFORE voice type and TES4-converted conditions.
  - Only conversation topics (non-bark) get injection. Bark topics use voice type conditions only.
  - 1,415 INFOs injected. Reduced wrong-NPC dialog from 3,775 to 208 across 100 NPCs (94.5%). Remaining 208 are 2 city-gossip topics using GetInCell (correct at runtime).
  - Key functions: `build_getisid_ctda()` (func 72), `build_topic_npc_ctdas()`, `info_has_positive_getisid()`, `collect_topic_npc_fids()` in `dialog_misc.py`
- **Dialog emulator**: `tools/dialog_emulator.py` — Simulates Skyrim dialog engine for validation. Modes: `--npc <edid>` (single NPC), `--batch --max-npcs N` (batch test), `--quest <edid>`, `--collisions`. Parses converted ESM, evaluates conditions, reports wrong-NPC matches.

### DOOR conversion notes
- TES4 FNAM bit 0 = "Oblivion gate" — **clear this bit** when writing TES5 FNAM (no TES5 equivalent, may corrupt flags)
- TES4 bits 1-3 (Automatic, Hidden, Minimal Use) map directly to TES5 bits 1-3
- XTEL Door FormID is remapped via get_formid() — both sides of a teleport pair must be in the output
- TES4 XTEL = 28 bytes (no flags field); TES5 XTEL = 32 bytes — must append 4 bytes of flags (0x00000000 = default) when writing TES5 XTEL
- Doors without XTEL are correctly treated as open/close doors

### NIF mesh rotation
- Some Oblivion architecture/static NIFs have a non-identity rotation on their root NiNode (from 3ds Max exporter)
- Skyrim's BSFadeNode ignores the root node's local rotation matrix for static placement (Oblivion's NiNode applied it); this means statics appear rotated in Skyrim
- **Fix (in nif_converter.py Pass 6c)**: For non-skinned NIFs, bake the root rotation into each direct child's local transform (R_child = R_root × R_child, T_child = R_root × T_child), then zero the root rotation. Skinned meshes excluded (need skeleton bone alignment).
- Simple zero-only reset (prior approach) does NOT fix the issue — the geometry is still in the rotated coordinate space; baking into children is required.

### NIF animated mesh conversion
- Oblivion animated doors/activators use keyframed collision (motion_system=6 in Oblivion format)
- Key differences from static collision in Skyrim:
  - bhkCollisionObject.flags = 137 (0x89 = ACTIVE | D_ANIMATED | bit 7)
  - bhkRigidBody.motion_system = 4 (MO_SYS_KEYFRAMED)
  - bhkRigidBody.quality_type = 1 (MO_QUAL_FIXED)
  - bhkRigidBody.unknown_byte = 10 (broadphase type for animated)
  - NiNode flags |= 0x80 (selective update sync for physics)
- BSXFlags must have bit 0 set (ANIMATED) → value 139 (0x8B) for animated meshes. Detect via NiControllerManager on root.
- Animation data: NiControllerSequence StringPalette offsets MUST be resolved BEFORE version upgrade (UV2=11→83). After upgrade, PyFFI switches to direct-string mode and offsets are ignored → empty node_name → crash.

### NIF particle system conversion  
- **BSXFlags bit 0 (Animated) is REQUIRED or particles NEVER TICK — THE final fire-invisibility root cause (fixed 2026-07-05)**: without BSX bit 0x01 on the root, the engine never updates the mesh's time controllers, so emitters never fire — the file is perfectly valid but the fire is invisible. Census: **399/400 vanilla particle meshes set bit 0** (sole exception: a trailer camera rig); collisionless particle meshes use plain BSX=1 (also 0x201/0x221 with external-emit/editor bits). Two converter gaps caused this: `_add_bsx_flags` (a) early-returned when the root had NO collision (fireopensmall loses its collision → no BSXFlags at all), and (b) detected "animated" only via NiControllerManager on the ROOT — particle controllers live on the NiParticleSystem, so even collision-bearing fire got 0x82 static. Fix: `_tree_is_animated()` (any NiParticleSystem, or any block with a controller, anywhere in the tree) → collisionless+animated gets BSX=1; collision values get bit 0 OR'd in (0x82→0x83, 0xC2→0xC3 — both appear in vanilla census); `_convert_flame_nodes` now CREATES a BSXFlags(=0x10) when the root has none (fake candles without collision previously lost the AddonNode bit). All the fixes below were necessary too, but this was the last blocker: the earlier gravity_object fix repaired the SIM, this makes the engine RUN it. (fixed 2026-07-05, `_skyrimize_modifiers`)**: the SSE particle engine does NOT drive Oblivion-era `NiPSysGrowFadeModifier` (scale) or `NiPSysColorModifier` (color) even though they're valid block types — particles spawn at scale 0 / alpha 0 = invisible. Convert them to the BS* equivalents the engine actually processes, matching a working vanilla fire (`references\Skyrim Meshes\...\slighthousefire.nif` Fireball): **NiPSysGrowFadeModifier → BSPSysScaleModifier** (60-entry scale ramp, grow-in/hold/fade-out, peak ~1.0 taper to 0.1); **NiPSysColorModifier → BSPSysSimpleColorModifier** (fade_in/out % + 3 Color4s). **Inject BSPSysLODModifier** — it's in 498/498 vanilla particle meshes (LOD begin/end/emit-scale/size = 0.033/0.233/0.2/1.0); without it the system culls at all distances. Keep emitter/spawn/rotation/gravity/position/bound-update/age-death as-is. Set NiPSysModifier `order` to vanilla bands: AgeDeath=0, LOD=1, Emitter/Spawn=1000, SimpleColor/Rotation/SubTex/Scale=3000, Gravity=4000, Position=6000, BoundUpdate=7000 (engine processes in ascending order). Set Name/Target(=the NiParticleSystem)/Active on every modifier.
- **Particle shader** (BSEffectShaderProperty): flags1 = `z_buffer_test` ONLY (NOT soft_effect — vanilla fire doesn't set it), flags2 = `vertex_colors` ONLY, `emissive_multiple`=1.5 + emissive_color=(1,1,1,1) (fire glows; vanilla 1.25–1.5), texture_clamp_mode=**0xFF03** (u32 packs clamp 3 in the low byte + Lighting Influence 0xFF in byte 1 — every vanilla fire uses 65283, not 3). Always attach a NiAlphaProperty flags=0x100d (additive SRC_ALPHA/ONE) — vanilla particles always have one (campfire01burning uses 0x10ed/threshold 128 standard blending; source alpha is passed through when present).
- **NiBillboardNode root scrambles particle emission → invisible (fixed 2026-07-05; quad re-billboarding added same day)**: Oblivion fire/effect NIFs have a `NiBillboardNode` ROOT (to face the 2D fire quads at the camera) with the particle-system emitters nested UNDER it. A NiBillboardNode re-orients its entire subtree to face the camera every frame; a world-space emitter under it emits into a spinning frame → particles fly off-screen / the system renders nowhere. Vanilla Skyrim keeps particle emitters under a PLAIN NiNode (`slighthousefire.nif`: BSFadeNode→NiNode "Fireball-Emitter"→NiParticleSystem). Fix in nif_converter Pass (root handling): if a NiBillboardNode root's subtree contains any NiParticleSystem, DEMOTE the root to a plain NiNode (copy name/transform/children/extradata/controller) — **but wrap each direct GEOMETRY child (the flat fire quads) in a fresh child NiBillboardNode carrying the source root's billboard_mode** (vanilla campfire01burning pattern: BSFadeNode → NiBillboardNode "Plane05" → NiTriShape). A plain demote leaves the quads fixed-facing = edge-on/backfacing from most in-game angles = fires look invisible (while NifSkope's default camera happens to face them). Emitter/marker child nodes stay unwrapped. Non-particle billboard roots keep the whole-root wrap. **BILLBOARD AXIS CONVENTION (the final fire-quad invisibility fix, 2026-07-05)**: Oblivion mode-1 (ROTATE_ABOUT_UP) keeps local **+Y up / +Z at camera**; Skyrim mode-1 keeps local **+Z up / ±Y at camera**. Fire quads are authored flat in local XY (height along +Y) with IDENTITY transforms — correct under Oblivion's convention, but under Skyrim's an identity-rotation billboard leaves the quad LYING FLAT spinning about Z (edge-on from every standing viewpoint = invisible). The wrapper NiBillboardNode must carry vanilla's **−90°-about-X** static rotation `[[1,0,0],[0,0,1],[0,−1,0]]` (maps local Y→world Z; byte-identical to vanilla campfire01burning "Plane05"). Diagnosed by comparing vanilla billboard-node rotations (non-identity!) vs quad vert planes (both games author quads flat in XY).
- **EditorMarker geometry must be STRIPPED** (`_walk_node`): Oblivion hides its editor-marker meshes (the pyramid in fire NIFs) via the node hidden flag, which our conversion clobbers with NIF_FLAGS (visible) — the marker then renders in game as an untextured BLACK PYRAMID (this was the mysterious "black pyramid" at placeatme'd fires; at world-placed fires it sat underground). Vanilla Skyrim ships no editor-marker geometry in these objects.
- **NiAlphaProperty must NOT be shared between particle systems**: Oblivion sources share one alpha block across several PS; vanilla Skyrim always pairs each PS with its own shader+alpha. `_convert_particle_system` clones the source alpha per PS.
- **NifSkope's "animate" option is NOT a valid diagnostic for Skyrim particle chains**: NifSkope 2.0.dev7 only registers the OLD `NiParticleSystemController`/`NiBSPArrayController` for particles (glparticles.cpp) and `BSEffectShaderPropertyFloat/ColorController` for effect shaders — it completely ignores `NiPSysEmitterCtlr`/`NiPSysUpdateCtlr`. A perfectly-authored Skyrim PSys NIF shows "No Animations in this NIF"; vanilla campfire only gets an animate option from its shader controllers on the glow quads.
- **UV SCALE (0,0) = INVISIBLE — THE fire-invisibility ENDGAME bug (fixed 2026-07-05)**: PyFFI's fresh `BSEffectShaderProperty` defaults `uv_scale` to **(0,0)** (vanilla: offset (0,0), scale **(1,1)**). Scale 0 collapses EVERY UV to the texture's top-left texel — transparent on flame textures — so all effect-shader geometry (particles AND quads) rendered fully transparent while being structurally perfect: sim ran (crash proved it), every block census-clean, texture valid. Diagnosed via A/B matrix: vanilla-structure+our-texture visible, our-structure+vanilla-texture invisible → field-by-field shader diff caught the one field never printed. ALWAYS set uv_offset(0,0)+uv_scale(1,1) on any PyFFI-created shader property; regression test asserts non-zero scale on every effect shader.
- **NiFlipController is dead in Skyrim (0/17,216 vanilla) — converted to atlas + float controller (2026-07-05, `asset_convert/flipbook.py`)**: Oblivion animates fire quads by flipping the diffuse per frame. Conversion: decode the N frame DDSes (DXT1/3/5 → BGRA), compose a horizontal strip atlas padded to POT frame count (uncompressed BGRA32 DDS, written into the output textures tree beside `\meshes\`), set `uv_scale.u = 1/N_pad`, and drive `BSEffectShaderPropertyFloatController` (flags 0x48, var **6 = U Offset**, `NiFloatData` keys mode **5 = CONST** at k·delta → k/N_pad; delta from `NiFlipController.delta`, fallback cycle/N or 1/15s). Planned in `_process_geometry` (validates source frames via `_resolve_source_texture` — maps the rewritten tes4 path back to the export textures tree), built in `convert_nif` (knows dst tree). NifSkope animates it too (its EffectFloatController is supported — NifSkope "no animate option" on PSys-only NIFs is normal, but flip-book quads DO animate there now). Fallback on unresolvable frames: static first frame.
- **Emitter controller flags** (`NiPSysEmitterCtlr`/`NiPSysUpdateCtlr`/`NiPSysModifierActiveCtlr`): Oblivion ships flags=0x08 (Active only); **OR in 0x48** (Active | Compute-Scaled-Time, bit 0x40 default-true in Skyrim) — do NOT overwrite, because Oblivion's NiPSysUpdateCtlr carries CLAMP cycle bits (0x0c) that vanilla keeps (campfire01burning UpdateCtlr = 0x4c, EmitterCtlr = 0x48). Without Compute-Scaled-Time the birth-rate interpolator can evaluate to 0 (no particles).
- **Dangling gravity_object → broken particle sim → invisible (fixed 2026-07-05; necessary but NOT sufficient — the BSX Animated bit above was the final blocker)**: `collision.py::remove_empty_collision_nodes` deletes EVERY bare empty NiNode child of the root (0 children, no collision). Oblivion fire NIFs have empty marker nodes named `Gravity`/`SparkGravity` that the `NiPSysGravityModifier.gravity_object` points at — deleting them dangles the reference (PyFFI writes "NiNode block is missing from the nif tree: omitting reference"), and the engine's particle physics then fails → particles never render. Vanilla campfire01burning.nif KEEPS its `Gravity` node (block [2], referenced by the gravity modifier). Fix: `remove_empty_collision_nodes` now protects nodes whose id() is in `_collect_psys_referenced_nodes(root)` (gravity_object + every *Emitter.emitter_object). Detect the symptom: convert with pyffi logging at WARNING and grep for "missing from the nif tree", or check `id(gravity_object) in tree` after conversion.
- **NiParticleSystem block size sanity**: at BSStream 83 an empty-modifier-list particle system is ~142 bytes, +8 per extra modifier band; vanilla fire particle systems are 150 (10 modifiers). Compare header block_size across many vanilla meshes — a size that's LOWER than the vanilla floor for the same modifier count means a dropped field/ref. The 4 Far/Near Begin/End ushorts (PyFFI `unknown_short_2`/`unknown_short_3`/`unknown_int_1`, only when user_version≥12) are all 0 in vanilla fire — not a culprit.
- Diagnosing invisibility: read a WORKING vanilla particle mesh and diff the modifier chain (needs `NiPSysData.read` from pyffi_monkey_patch Patch 4 — stock PyFFI can't read Skyrim NiPSysData). The reference NIFConverter (`references/NIFConverter/copyover_legacy_nif_animations.py:915`) just DELETES NiParticleSystem (`replace_global_node(node, None)`) — do NOT copy that; convert to the visible BS* vocabulary instead.
- NiPSysGrowFadeModifier base_scale patch (Patch 2) still needed for any GrowFade that survives; makes the block 29 bytes = correct Skyrim size (NiPSysModifier parent 13 + own 16).
- NiPSysData: preserve original max particle count (`max(num_vertices, 75)` → bs_max_vertices). num_vertices and bs_max_vertices ALIAS the same PyFFI field slot.
- **CRITICAL — PyFFI 2.2.3 NiPSysData layout is STRUCTURALLY WRONG for Skyrim; hand-rolled in `pyffi_monkey_patch.py` Patch 4 (fixed 2026-07-05, the AnvilCastleGreatHall CTD)**: PyFFI's NiPSysData attribute list is the wrong (older Bethesda) field arrangement — it is MISSING Material CRC (4), Consistency Flags (2), Additional Data ref (4), Has Texture Indices (1), Aspect Flags (2), and invents spurious unknown_byte_1/unknown_link/unknown_short_3/unknown_byte_4. Net: an empty block writes 66 bytes where real Skyrim is **70**, and the FIELD ORDER is wrong regardless of size, so the SSE engine (which trusts the header block_size to seek to the next block) misaligns EVERY following block → it builds a BSEffectShaderMaterial from garbage → `vmovntdq [rcx+0xA0/0xC0], ymm` non-temporal store past a page end → CTD (crash logs named `BSEffectShaderProperty "DamageSphere"/"CandleFat02Fake"`). The correct 70-byte #BS202# layout (from `references/nif 0.10.0.0.xml`, verified == 70 on a census of 27 vanilla empty NiPSysData blocks) is emitted by overriding `NiPSysData.get_size`/`write` to pack the bytes directly: GroupID(i) BSMaxVertices(H) KeepFlags(B) CompressFlags(B) HasVertices(B) BSDataFlags(H) MaterialCRC(I) HasNormals(B) BoundCenter(3f) BoundRadius(f) HasVColors(B) ConsistencyFlags(H) AdditionalData(i) HasRadii(B) NumActive(H) HasSizes(B) HasRotations(B) HasRotAngles(B) HasRotAxes(B) HasTexIndices(B) NumSubtexOffsets(I) AspectRatio(f) AspectFlags(H) SpeedToAspect×3(f) HasRotSpeeds(B). **Field values (raw-byte census of ALL 837 NiPSysData blocks in 400 vanilla particle meshes, 2026-07-05 — supersedes the earlier 27-block census which was read through PyFFI's MISALIGNED layout and got the flags wrong)**: HasVertices=1, BSDataFlags=0, MaterialCRC=0, HasNormals=0, **HasVColors=1** (810/837), Consistency=0, **AdditionalData=-1** (837/837 — NULL ref; writing 0 references BLOCK 0 = the root!), **HasRadii=1** (837/837), NumActive=0, HasSizes=1, HasRots=0, HasRotAngles=1|0, HasRotAxes=0, **HasTexIndices=0 whenever NumSubtexOffsets=0** — the engine does `rand % NumSubtexOffsets` for atlas frame selection when the flag is set, so flag=1+count=0 = **EXCEPTION_INT_DIVIDE_BY_ZERO in the emitter update** (`div [rsp+...]`, crash names NiPSysCylinderEmitter+NiPSysData+NiPSysEmitterCtlr; 0/837 vanilla blocks pair flag=1 with count=0; atlas blocks have count 1..128 and block size 70+16×count — all 837 satisfy that size equation, fully validating the layout). AspectRatio=1.0 for non-atlas (0.0 on atlas blocks), AspectFlags=0, s2a floats=0, HasRotSpeeds=0. This crash only SURFACED once the BSX Animated bit made emitters actually run. `read` is NOT overridden for Oblivion sources — the converter only reads Oblivion-version sources (PyFFI's Oblivion layout is separately correct); our Skyrim output is never re-read by the pipeline. **PyFFI can no longer parse our Skyrim particle output — verify via the HEADER block_size table (inspect-only), NOT a PyFFI struct re-read.** Sweep: `NiPSysData` block_size must be 70 for empty pools.
- **Diagnostic method for "which field is wrong" (data-driven, per user directive — never compare against a single mesh)**: census MANY vanilla meshes (`references\Skyrim Meshes`, ~400 particle NIFs) reading only the header block_size table + field values; the value that is uniform across all vanilla but differs in ours is the bug (e.g. `has_subtexture_offset_u_vs`=True in 27/27 vanilla). When PyFFI can't even READ vanilla (`Skipping -4092 bytes`), that itself proves PyFFI's layout ≠ the real engine layout → hand-roll from nif.xml.
- The self-consistency trap: `block.get_size()` (fills header block_size) and `block.write()` can DISAGREE for a mis-conditioned PyFFI struct (get_size=66, write=70) → header says 66 but 70 bytes are written → engine seeks 4 short. A read→write round-trip inside a test masks this (re-read reconstructs arrays). Check `get_size()==len(write())` on the freshly-converted in-memory block, or the deployed file's header block_size vs vanilla census.
- **CRITICAL — `pyffi_monkey_patch.py` NiPSysData vercond precedence bug (fixed 2026-07-05)**: the added-particles shorts vercond was written as `'! version >= X && user_version >= 11'`. PyFFI's Expression parser binds `!` to `version` FIRST → `((!version) >= X) && ...` = ALWAYS FALSE → the two shorts were dropped from OBLIVION reads too, misaligning every source NIF containing NiPSysData by 4 bytes → read abort. This is why the ENTIRE `fire\`, `effects\`, `magiceffects\`, `dungeons\misc\fx\`, `landscape\waterfall*` etc. list in TODO.txt §7 failed with [RD] (123 of 151 recovered by the one-line fix). MUST parenthesize: `'!((version >= 335675399) && (user_version >= 11))'`. Verify with `Expression(expr).eval(ctx)` against Oblivion (v=0x14000004,uv=11 → present=True) and Skyrim (v=0x14020007,uv=12 → present=False). The "Skipping N bytes in NiPSysData/NiPSysGrowFadeModifier" messages when a converted file is re-read by STOCK (unpatched) PyFFI are expected — stock PyFFI has the buggy layout; the game engine follows the real nif.xml (matches our output). Confirm real correctness via a patched-reader round-trip, not stock-PyFFI block-size checks.
- **Fire/effect QUAD emissive (`_process_geometry`, flip_ctrl path)**: BSEffectShaderProperty.emissive_multiple defaults to 0.0 → the flame quad renders BLACK. Fire is self-illuminated: set emissive_multiple=1.0 and emissive_color=(1,1,1,1). The particle-system path already set it; the effect-quad path did not.

### NIF FlameNode → flame (fixed 2026-07-05)
- Oblivion marks where a flame burns with an empty `FlameNode*` NiNode (a bare marker: name + transform, no children) and attaches a flame NIF there at RUNTIME. 108 Oblivion meshes have them (candles, candelabra, sconces, lamps, lecterns, chandeliers, torches).
- **Skyrim uses the Master Particle System**: a `BSValueNode` named `AddOnNodeNN` with `value=NN` (a ushort) tells the engine to spawn the matching `ADDN` record's particle NIF at that node. Vanilla `clutter\candles\candlehornfloor01.nif` ships 4× `BSValueNode "AddOnNode49"`. AddonNode indices from Skyrim.esm ADDN `DATA` field: **candle flame `MPSCandleFlame01`=49, torch fire `MPSTorchFire01`=46** (torch/fire meshes → 46, everything else → 49).
- `_convert_flame_nodes()` in nif_converter.py retypes each empty FlameNode NiNode → BSValueNode: keep translation, RESET rotation to identity + scale to 1.0 (Oblivion FlameNodes carry a 90° tip + ~2× scale meant for the old attached NIF), name `AddOnNode<val>`, value, unknown_byte=1 (vanilla), flags carried from the source. PyFFI struct/array fields (translation/rotation/children) are READ-ONLY properties — mutate in place, never `setattr`.
- Requires **BSXFlags bit 4 (0x10 = AddonNode present)** OR'd onto the root's existing BSXFlags or the engine ignores the spawn (vanilla candlehornfloor01 BSX=147=0x93). Done in `_convert_flame_nodes` after the walk.
- **DO NOT embed a converted flame particle subtree under the FlameNode** — the old (disabled) `_embed_flame_nodes` did this and crashed the engine: it builds a BSEffectShaderMaterial from the embedded particle's BSEffectShaderProperty and OVERRUNS its buffer (crash `vmovntdq [rcx+0xA0], ymm2` writing past a page end; crash log named `BSEffectShaderProperty "CandleFat02Fake"`). The BSValueNode path ships NO custom particle geometry — the engine spawns the vanilla, blessed MPS NIF. The "Code does work so it's been disabled" comment in the original was a typo for "does NOT work".

### NIF NiGeomMorpherController (dead in Skyrim, fixed 2026-07-05)
- **0 of 17,216 vanilla Skyrim meshes use NiGeomMorpherController** — it's Oblivion's bow flex/morph system; Skyrim bows are `*skinned.nif` and flex via skeletal animation. Strip it (and NiMaterialColorController) from geometry controller chains: `_strip_dead_geometry_controllers()` walks `geom.controller.next_controller` and unlinks them. This also lets NiTriStrips that were only kept as strips (because of the morpher) convert to NiTriShape.
- Why it mattered: PyFFI mis-serializes NiGeomMorpherController across the 20.0→20.2 bump — `interpolator_weights` is populated under the Oblivion layout but EMPTY under the Skyrim layout, so `data.write` aborts with `array size (0) different from field describing number of elements (N)`. This was the entire `weapons\*\bow.nif` [WR] failure list in TODO.txt §7.

### NIF bhkMultiSphereShape (dead in Skyrim, fixed 2026-07-05)
- **0 of 17,216 vanilla Skyrim meshes ship bhkMultiSphereShape** (deprecated Havok path). The only Oblivion source that has one is `clutter\magesguild\apparatusalembicnovice.nif`, and shipping it converted CRASHES SSE at cell load (Anvil Mages Guild) with no crash log. Vanilla expresses the same thing as ConvexTransform+Sphere children in a list shape (`clutter\kitchen\woodenladle01.nif`).
- `_expand_multisphere()` in collision.py expands it: each sphere → a `bhkSphereShape` (radius ×0.1) wrapped in a `bhkConvexTransformShape` (identity rotation, sphere center ×0.1 in the 4th column, 4th matrix row all zeros incl. m_44 — matches vanilla). 1 sphere → bare wrapper, N → bhkListShape. `_convert_shape`'s bhkListShape branch now FLATTENS a nested list produced by the expansion (a list shape has no transform of its own so flattening is safe; vanilla never nests list shapes).

### NIF worn armor conversion
- Worn armor (has_skin AND not _gnd AND in armor/clothes dir) must use **NiNode** root, NOT BSFadeNode
- BSFadeNode is for world objects only — worn armor is attached to the character skeleton
- BSDismemberSkinInstance is required for Skyrim biped slot assignment (upgrade from NiSkinInstance)
- Ground models (_gnd) with cloth-physics bones must have skin stripped (bones don't exist in Skyrim skeleton)
- **Material CRC (unknown_int_2)**: ALL vanilla Skyrim NiTriShapeData has `unknown_int_2=0`. This field is the Material CRC in Skyrim BSStream 83. Setting it to 8 (confused with the tangent flags) causes rendering issues. Always set to 0.
- **PRN rigid armor (helmets etc.)**: Oblivion attaches via `Prn` NiStringExtraData on root. Converted to BSDismemberSkinInstance with single bone at weight 1.0. Vanilla Skyrim structure has bone NiNode as FIRST child of root (before geometry blocks). bodyPart=131 (SBP_131_HAIR) is correct for helmets (they replace hair).
- **Body part assignment (BSDismemberSkinInstance)**: Oblivion cuirass NIFs have geometry named 'Arms' and 'UpperBody'. The 'arm' keyword in ARMOR_GEOMETRY_BODY_PARTS maps to SBP_32_BODY (not SBP_34_FOREARMS) because gauntlet NIFs use 'Hand' geometry names — 'Arms' only appears in cuirass/shirt meshes. This prevents cuirass arm geometry from being hidden when gauntlets are equipped.
- **Clothing vs armor ARMA body coverage**: Clothing ARMA should NOT add ForeArms(34) extra coverage — shirt sleeves (SBP_32_BODY) should remain visible when gloves are equipped. Armor cuirasses DO add ForeArms(34) because the separate ARMA system allows gauntlets to properly overlay.
- **Shoes vs boots calves slot**: Shoes (clogs, sandals) should NOT claim Calves(38) in ARMA. Only boots get calves. Detection: `'boot' in model_path`. Clothing foot items without 'boot' are shoes.
- **Body skin splice section_bboxes coordinate space**: OB body skin sections are in OB skeleton space; SK body NIF verts are in SK skeleton space. These are DIFFERENT frames. The OB arm area (z≈98–105) is at SK z≈72–92 after retarget. **Always use POST-RETARGET section_bboxes** from `collect_skin_info()` — these are in SK world space and correctly localise both arm openings and neck. Pre-retarget bboxes (source OB verts) only work for neck/collar (small-x geometry that happens to be at the same world z in both skeletons) but MISS the arms (which are displaced ~20 Z units by skeleton frame differences). SK male body max arm reach (|x|>20) sits at z=75–97 world, exactly within the post-retarget 'Arms' bbox z=72–92. Use `bbox_pad=1.0` to stay under 25% of total body verts spliced.

### NIF shield conversion
- Shields use BSFadeNode root + Prn='SHIELD' (same as weapons, NOT NiNode like worn armor)
- **Orientation fix**: Oblivion shields are modeled with thin (face-normal) axis along Y. Skyrim's SHIELD bone expects it along Z. A +90° rotation around X is applied to the BSFadeNode root. Root rotation baking wraps this in an inner NiNode.
- **BSInvMarker**: Shields need BSInvMarker for inventory display: rot=(4712,0,0), zoom=1.0 (from vanilla ironshield.nif). Without BSInvMarker, shield is invisible in inventory.
- Oblivion Prn values for shields: 'Shield' or 'Bip01 L ForearmTwist' → remapped to 'SHIELD'
- Oblivion shield geometry names: 'Shield:0', 'Shield:2' (single geometry block, no skin)

### NIF armor ground model (_gnd) conversion
- Armor/clothing _gnd files need **BSInvMarker** for inventory display. Without it, items are invisible in the inventory 3D viewer.
- BSInvMarker values from vanilla Skyrim: rot=(1570,0,0), zoom=1.0 (cuirass/gauntlet gnd files). 1570 milliradians ≈ 90° (upright). Helmets and boots may not have BSInvMarker in vanilla (display defaults work).
- BSInvMarker is added during NiNode→BSFadeNode conversion when `_is_gnd and _in_armor_dir`.
- BSXFlags: vanilla gnd files use 194 (0xC2); our converted use 130 (0x82). Both load fine.

### NIF skin retargeting (Oblivion → Skyrim skeleton)
- **Critical**: Oblivion skeleton uses X-up coordinates (spine along X axis). Skyrim uses Z-up (spine along Z)
- **Current approach: Corpus search + L-BFGS-B continuous optimization**:
  1. `tools/kf_animation_explorer.py --build-cache` searches 453 .kf animation files with parallel parsing (ThreadPoolExecutor, 31 workers, ~36s)
  2. Per-bone transform library: 65 bones, 336K candidates from entire animation corpus
  3. Chain-level softmax blend (T=1.0, effectively argmin) over ~25K coherent frames per chain (left/right arm, left/right leg — body chain excluded)
  4. **Multi-start L-BFGS-B refinement**: 50 starting frames per chain, axis-angle rotation perturbation bounded to ±0.35 rad (~20°), parallelized with ThreadPoolExecutor. Discovers poses NOT in the .kf corpus.
  5. L/R mirroring for symmetry
  6. Pre-computes delta matrices `inv(rest_world) @ anim_world` per bone, saved to `asset_convert/generated/best_animation_pose.json`
  7. `skin_retarget.py` Phase B.1: loads pre-computed deltas, applies standard LBS using OB skin weights: `v' = Σ w_i * (v @ delta_i)`
  8. Phase A: repositions bones to Skyrim skeleton positions
  9. Phase C+D: recomputes bind matrices (`_manual_update_bind_position`) and skin partitions
  10. **FK+Gaussian double-deformation MUST be avoided** — Gaussian spatial blend only runs when FK was NOT applied.
- **FK results**: Post-mirror RMSD 9.64 (was 9.73 corpus-only). Legs: 2.8/1.8→1.08/1.08 (62% improvement). Arms: 4.4→4.0 (10%). 37/37 tests pass, 396 armor NIFs 0 errors.
  - **`_mat3_to_quat` NIF convention**: This function expects a column-vector convention matrix. PyFFI Matrix33 / NIF matrices use row-vector convention so `_mat3_to_quat(NIF_Matrix)` returns the CONJUGATE. In `skin_retarget.py` the delta matrices are numpy column-convention, so pass `_mat3_to_quat(delta[:3,:3].T)` (transpose, no sign flip). For collision baking this is moot — **do not apply _mat3_to_quat to bhkRigidBodyT at all**.
- **Spatial blend residual was wrong direction**: `v_spatial` (spatial blend from OB rest ≈ 50% to SK) minus `v_fk` (FK ≈ 90% to SK) = vector pointing BACKWARD toward the LESS-transformed position. DQS inherently handles joint boundaries — no separate residual needed.
- **ProcessPoolExecutor causes issues on Windows**: Exit code 1 + slightly worse results. Reverted to sequential `for` loop for L-BFGS-B multi-start. Module-level `_lbfgsb_trial_worker` kept (clean, no harm). ThreadPoolExecutor for first kf-parsing step is fine (I/O-bound).
- **Geometric limit**: Arm RMSD ~4.0 is the minimum achievable with rotation-only optimization. UpperArmTwist (err=13.4) and ForearmTwist (err=9.4) contribute 56% of arm cost from bone LENGTH differences between OB/SK skeletons. Excluding twist bones from cost made mesh quality WORSE (larger main-bone rotations).
- **Body chain**: Including spine in optimization gives spine RMSD 5.59 but BREAKS cuirass edges (18.8% fail) — spine deltas distort LBS. Spine gets identity delta; Phase A handles repositioning.
- **Gaussian spatial blend (fallback)**: Only runs when best_animation_pose.json is absent. Uses distance-based Gaussian-weighted bone blending with σ=20.
- Vertices in OB armor NIFs are in standard world-space coordinates (Z-up), NOT in the OB convention-rotated space.
- NiSkinData B_bone = inv(W_sk_bone) when M_mesh = identity (standard for skinned armor)
- Skeleton data: `asset_convert/generated/skeleton_bones_skyrim_{male,female}.json` and `skeleton_bones_oblivion.json`
- Female armor detected via `/f/` in path → uses female skeleton data
- PRN meshes (single bone, identity B) are NOT reposed — they're rigidly attached to one bone
- **Critical**: ALWAYS use `_manual_update_bind_position()` instead of PyFFI's `update_bind_position()`. PyFFI's version computes wrong B values when geometry has a non-identity local transform. The manual numpy version handles this correctly.
- **Test suite**: `tests/test_skin_retarget.py` — 37 tests covering skeleton loading, bone mapping, MBW=I, vertex deformation, bone position accuracy, edge length preservation (<10% failure for cuirass, <5% for boots), full converter integration, skin partitions, PRN handling, BSDismemberSkin. All 37 pass.
- **Previous approaches that FAILED** (16+ attempts):
  - v2 bind-matrix-only (no vertex deformation): Arms stuck in A-pose at rest.
  - Skin-weight-based DQS/LBS: Sharp weight boundaries → 24-82% edge failure
  - Gaussian spatial blend alone: 40-50% displacement dilution on arms (normalized weight averaging)
  - FK LBS + Gaussian together: Double deformation → 13.9% edge failure
  - Laplacian-smoothed mesh weights: Created discontinuities. REVERTED.
  - Global/per-mesh inverse filter, RBF interpolation, 2x global overshoot: All failed (see repo memory for full list)

### NIF bhkRigidBody field mapping (PyFFI ↔ newer nif.xml)
- `unknown_int_1` → bhkWorldObjCInfo.Unused01 (4 bytes binary padding) — **zero for safety**
- `unknown_int_2` → BroadPhaseType(1B) + Unused02(3B) — set to 1 (BROAD_PHASE_ENTITY)
- `unknown_3_ints` → bhkWorldObjCInfoProperty (Data=0, Size=0, CapFlags=0x80000000)
- `unknown_byte` → bhkEntityCInfo.Unused01 — set to 116 (matching external NIFConverter)
- `unknown_2_shorts` → bhkRBCInfo padding — set to [29541, 23659]
- `unknown_6_shorts[2:4]` → bhkRBCInfo2010.UnknownInt1 — **MUST be 0** (Skyrim interprets as pointer)
- Static objects: quality_type=1 (MO_QUAL_FIXED), motion_system=5 (SYS_BOX_STABILIZED)
- Dynamic/clutter: quality_type=4 (MO_QUAL_MOVING), motion_system=3 (MO_SYS_SPHERE_INERTIA)
- Animated: quality_type=1 (MO_QUAL_FIXED), motion_system=4 (MO_SYS_KEYFRAMED)

### NIF dynamic clutter physics (Havok)
- **Mass**: Keep Oblivion mass as-is. Oblivion clutter (0.1–8.0) is already in Skyrim's range (0.5–100). The legacy converter's `mass *= 6` is WRONG — makes items too heavy and causes them to "hang in the air."
- **Inertia tensor**: Must scale by `HAVOK_SCALE² = 0.01`. Oblivion inertia (2.3–8.8) is ~100× Skyrim (0.02–0.32) because inertia ∝ mass × distance² and collision shapes are scaled 0.1× for Skyrim Havok units.
- **Skyrim clutter standard values**: friction=0.50, restitution=0.40, linear_damping=0.0996, angular_damping=0.0498, max_linear_velocity=104.4, max_angular_velocity=31.57, deactivator_type=1, solver_deactivation=2

### NIF NiDefaultAVObjectPalette fixup
- After converting NiTriStrips→NiTriShape, NiDefaultAVObjectPalette entries still reference old blocks. Must update `av_object` references using a block_map (old id → new block). Without this fix, PyFFI writes "NiTriStrips block is missing from the nif tree" warnings and the animation palette has stale references.

### NIF furniture marker conversion (rewritten 2026-07 — fixed backwards/floating NPCs)
- Oblivion: `BSFurnitureMarker` (NiExtraData) with FurniturePosition using `orientation` (ushort, milliradians), `position_ref_1`/`position_ref_2` (byte, always equal in practice)
- Skyrim: `BSFurnitureMarkerNode` (inherits BSFurnitureMarker) with FurniturePosition using `heading` (float, radians), `animation_type` (ushort: 1=Sit, 2=Sleep, 4=Lean), `entry_properties` (bitflags: front, behind, right, left, up)
- **CRITICAL SEMANTIC DIFFERENCE**: Oblivion positions are ENTRY POINTS — where the NPC stands on the floor ~51-106 units AWAY from the furniture, one marker per approach direction (a single chair has 3-4). Skyrim positions are the actual SIT/SLEEP spots (hip position), one per physical seat. A 1:1 position copy produces N duplicate seats with inconsistent headings (NPCs sit sideways/backwards) at the wrong place.
- **Conversion** (`_convert_furniture_markers` in nif_converter.py): compute a seat candidate per entry, cluster candidates within 20 units, emit ONE Skyrim position per cluster. Verified to reproduce vanilla marker topology exactly (chair→1 pos front|right|left; bench→3 pos; bed→1 sleep pos right|left).
- **Seat candidate**: sit entries stand a FIXED distance from their seat — 51.5 (side refs 11/12) / 55.0 (front/behind refs 13/14) — walk that far along the approach direction (handles curved benches like anviltreebenchseat01; a bench's side entry is 51.5 from the END seat so it clusters correctly). Sleep entry distances vary per bed (67-106), so instead project the geometry-bbox centre onto the approach ray (entries always point across the hip line).
- **Heading** (= direction occupant faces; for sleep = head→feet direction): `heading = orientation/1000 + offset[ref]` where offset = {1: −π/2, 2: +π/2, 3: −π/2, 4: 0, 11: −π/2, 12: +π/2, 13: 0, 14: +π}. 100% consistent across all 48 marker-bearing Oblivion.esm furniture NIFs. The old blanket `+π` rule was only right for ref 14. Ref semantics: 1/11 = occupant's left side, 2/12 = right side, 13 = behind occupant (step over / sit without turning), 14 = in front (approach facing seat, turn, sit), 3 = mat side entry, 4 = mat head-end crawl entry (3/4 verified against sleepingmat01's pillow bump; pillow end = taller z bump, calibrated on Skyrim bedroll01 where the marker proves head=+Y).
- **Z**: entry markers stand ON THE FLOOR in mesh coords (Oblivion furniture origins are at mid-height, so entry z is negative). Skyrim marker z = entry_z + 34.0 (sit) or + 37.0931 (sleep) — the vanilla floor-relative hip heights. All 24 Oblivion bed mattress surfaces lie 36.5-42 above their entry z, so floor+37.09 lands on the mattress. The old `z = -src.z` rule floated NPCs ~34 units in the air (it looked right on chairs only because origin-at-mid-height makes |−z| ≈ seat height by coincidence).
- **Entry flags** are relative to the final heading: flag = side of the seat the entry point lies on (front if (entry−seat)·facing > 0.5, etc.) — NOT a fixed per-ref mapping.
- Oblivion double beds get ONE centered sleep pos (entries converge mid-bed; single and double beds have identical entry spacing ~±91-94 so they cannot be distinguished, and Oblivion's fixed-travel sleep anim landed center-ish too).
- Marker-bearing NIFs live outside meshes/furniture too: clutter/castleinterior (castle beds/thrones), architecture (cathedral pews, tents/sleepingmat, ships/sibed, anvil tree bench), dungeons (benches, thrones, sacrifice altar), oblivion/architecture/citadel. Find them with a binary grep for the ASCII string `BSFurnitureMarker` (block type names are plaintext in NIF headers).
- BSFurnitureMarker lives in root NiNode's extra_data_list. During NiNode→BSFadeNode conversion, it must be explicitly converted and transferred (bulk extra_data_list copy breaks animated objects). Marker offsets are model-space and stay valid under the root-rotation wrap pass.
- **FURN record linkage (CRITICAL)**: TES5 FURN `MNAM` bits 0-23 enable NIF marker POSITION index 0-23. TES4 MNAM bits indexed the Oblivion NIF's ENTRY list — passing the bitmask through after seat clustering leaves dangling bits and the engine seats NPCs at garbage positions FAR from the mesh. The shared algorithm lives in `asset_convert/furniture_markers.py`; `tes5_import` (items.py `load_furniture_seats`, called in import Phase 0e) recomputes the same seat list from the source NIF and writes MNAM=(1<<n_seats)−1 + preserved high bits (0x40000000 sit-type / 0x80000000 bed-type, same in both games; beds add 0x08000000 MustExitToTalk like all vanilla beds) + WBDT(0,-1) + one FNPR per seat.
- **Oblivion entry-restriction variants**: many TES4 FURN records share one NIF and enable different entry-marker subsets (SEChair01F/R/L, 19 LCBench01* variants like `Fall`=front row only, `RL`=ends only). Conversion carries this into per-seat FNPR entry flags: only the entry directions whose TES4 entry bit was enabled are allowed (seats with no enabled entries fall back to all their entries). Verified vs vanilla: converted bench = 0x40000007 + 3×FNPR like CommonBench01; converted bed = 0x88000001 + FNPR 0x000C0002 byte-identical to CommonBed01; LCBed02L keeps right-entry-only (FNPR 0x00040002).
- FURN models whose NIF is missing from the export (SI furniture, palace thrones) get a conservative fallback: MNAM bit 0 + high flags, FNPR all entries. NIFs with NO markers get MNAM high flags only (no active positions — never enable bits beyond the NIF's position count).

### Activation pick region (HUD rollover "too big" on clutter) — SOLVED 2026-07
- Skyrim's crosshair activation is a PRECISE raycast against the Havok collision shape (user-verified: vanilla prompts appear only when the cursor is exactly on the mesh; `fActivatePickRadius` INI had no effect). An earlier theory blaming engine INI slop was WRONG.
- Root cause: Oblivion clutter ships ONE bhkConvexVerticesShape hull per object. A convex hull FILLS EVERY CONCAVITY — a goblet's hull fills the waist around the thin stem (collision radius 2.7-3.0 vs visual 1.6), a pitcher's hull fills the entire handle gap (y ±4.7 where the visual handle is ±0.53). AABB comparisons hide this (hull AABB == visual AABB exactly); compare CROSS-SECTIONS at concave features instead. Vanilla authors compound shapes instead (glazedgoblet01 = bhkListShape of cup box + stem box).
- Fix (`_decompose_clutter_hull` in collision.py): dynamic (mass>0) plain-bhkRigidBody single-convex-hull clutter is rebuilt as a bhkListShape of per-piece hulls: recursive binary split of the VISUAL vertices along the axis-aligned cut minimising total hull volume (scipy ConvexHull; accept cut if ≥10% volume gain, depth ≤3 → ≤8 pieces). Each half extends past the first vertex ring on the far side of the cut, or sparse vertex rows leave unfilled collision bands between pieces. Piece planes = scipy hull equations deduped, w = d − radius (vanilla stores planes pushed out by the convex radius). bhkRigidBodyT excluded (shape frame ≠ node frame). Frame sanity check vs the original hull AABB bails out when collision was authored differently from visuals. Result: goblet stem 2.7-3.0 → 1.8-2.4 (tighter than vanilla's box corners), pitcher handle strip y ±0.6.
- **Havok material conversion (was missing entirely)**: Oblivion materials are a 0-31 enum; Skyrim materials are CRC32 hashes (SkyrimHavokMaterial, values in references/nif 0.10.0.0.xml). `_convert_materials()` in collision.py maps them (`_OB_TO_SK_MATERIAL`); unmapped values leave the engine with an unknown material (no impact sounds/decals/stair-walk flag). **PyFFI trap: EnumBase.set_value() only LOGS "invalid enum value" and returns** for values outside its old enum list — must write `item._value` directly. PyFFI instantiates ONE material item per read context (typed OblivionHavokMaterial even when reading Skyrim CRC files — repr shows `<INVALID (...)>`, harmless; read/write via `_get_havok_material`/`_set_havok_material`).
- **Inertia scale regression**: collision.py had drifted to `_INERTIA_SCALE = 0.1` with a bogus justification comment ("Havok normalises by body scale internally"). Correct value is `_HAVOK_SCALE**2 = 0.01` (inertia ∝ mass·length², lengths scale 0.1) — verified: vanilla silverjug01 stores I_x=0.031 = m(3r²+h²)/12 exactly in SI/Havok metres. The 0.1 scale left inertia ~10× too large → sluggish rotation / "too much inertia" feel when grabbing or knocking clutter. (tests/test_asset_convert.py `_INERTIA_SCALE = 0.1` still asserts the old value and needs updating.)
- Note on masses: Oblivion authored masses differ per-item from Skyrim equivalents with no consistent ratio (OB silver pitcher 8.0 vs vanilla silver jug 0.8, but OB ceramic goblet 0.4 ≈ vanilla goblets 0.5-0.8) — masses stay unconverted.
- tes4/tes5_nif_analyzer print `BoundSphere` (NiTriShapeData center/radius) and bhkConvexVerticesShape vertex `extents` for this kind of investigation.

### NIF analyzer tools
- `python tools/tes4_nif_analyzer.py <nif_or_dir> [--outdir temp/analysis] [--max N]` — Dumps NIF structure to human-readable text (includes furniture marker positions/refs/orientations)
- `python tools/tes4_nif_analyzer.py <nif_or_dir> --bbox` — Prints world-space geometry bounding boxes (per-block + total, all transforms applied) to stdout; use to find mesh origins, floor levels, pillow bumps, etc.
- `tools/tes5_nif_analyzer.py` re-exports from tes4 version (PyFFI handles both versions)
- Useful for diff-based comparison between Oblivion, converted, and Skyrim reference NIFs

### OBND (Object Bounds) defaults
- ESM records without OBND crash the engine. Import script generates per-type defaults:
  - MISC=(-5,-5,0,5,5,8), KEYM=(-3,-3,0,3,3,3), WEAP=(-5,-5,0,5,5,30), STAT=(-50,-50,0,50,50,80)
  - ARMO=(-15,-10,0,15,10,30), NPC_/CREA=(-12,-12,0,12,12,60), LIGH=(-6,-6,0,6,6,20)
  - Other types get (-5,-5,0,5,5,5) as fallback

### WRLD World Bounds (NAM0/NAM9)
- NAM0 (bounds min) and NAM9 (bounds max) store X, Y as raw float world-unit values (same scale as TES4)
- xEdit **displays** them scaled by `1/4096` (cell units) but the raw file value is NOT divided
- TES4 exports `NAM0.MinX=-262144.0` → write exactly -262144.0 to TES5 file (do NOT divide by 4096)
- If divided: NAM0=-64.0 looks like valid cell coords but is actually 64 times smaller than needed → SSELodGen won't generate world map correctly

### Terrain LOD (SSELodGen) — data chain
- LAND BTXT/ATXT subrecords contain direct LTEX FormIDs (NOT indices into VTEX array)
- SSELodGen uses: BTXT.Texture(LTEX) → LTEX.TNAM(TXST) → TXST.TX00(path) → Data\Textures\{path}
- VTEX subrecord is a supplementary lookup array; most Oblivion LAND records don't have it (29/31823)
  - TES5 LAND VTEX format: packed array of uint32 LTEX FormIDs, one subrecord total (not per-quadrant)
  - Null slots (zero FormID) are valid and common — NOT a bug
- Landscape textures extracted from BSA are DXT1 BC1 512x512 — fully supported by SSELodGen
- If terrain LOD appears purple after correct data install: ensure OLD LOD tiles are deleted before regenerating

## Skyblivion Analysis — Conversion Best Practices

Analysis of ~140 Skyblivion/Skywind conversion scripts in `external/Skyblivion Conversion Edit Scripts/`. These findings are incorporated into our import script.

### Race Override System
Playable Oblivion races map directly to Skyrim equivalents by EditorID:
- Argonian→$00013740, Breton→$00013741, DarkElf→$00013742, HighElf→$00013743
- Imperial→$00013744, Khajiit→$00013745, Nord→$00013746, Orc→$00013747
- Redguard→$00013748, WoodElf→$00013749
- DarkSeducer, GoldenSaint, Sheogorath have no Skyrim equivalent (create new)
- ImportRACE() checks EditorID and stores a mapping to Skyrim's FormID instead of creating a new record

### NPC_ Conversion (from Skyblivion)
- **ACBS Flags**: Only keep compatible bits ($01+$02+$08+$10+$80+$4000). Force autocalc stats for creatures.
- **Level**: PC Level Mult formula = `1 + obLevel / 20`. CalcMin doubled, CalcMax doubled (default max=100).
- **ACBS Offsets**: Health=Endurance, Magicka=Intelligence, Stamina=Strength (from TES4 Attributes).
- **AI Thresholds**:
  - Aggression: 0-39→Unaggressive(0), 40-69→Aggressive(1), 70+→Very Aggressive(2)
  - Confidence: 0-29→Cowardly(0), 30-69→Average(2), 70+→Brave(3)
  - Responsibility: <30→No Crime(0) + Helps Allies, 30+→Any Crime(3) + Helps Nobody
  - Mood: always Neutral(4)
- **NPC_ Skills from Creatures**: TES4 CREA has aggregate skills (Combat/Magic/Stealth). Mapping:
  - OneHanded/Block/Smithing = Combat, TwoHanded = max(blunt,blade,h2h)
  - Destruction/Conjuration/Alteration/Illusion/Restoration = Magic
  - Marksman/Sneak/Lockpicking/Pickpocket = Stealth
  - HeavyArmor = max(HeavyArmor, Athletics), LightArmor = max(LightArmor, Acrobatics)
  - Alchemy = Alchemy, Speechcraft = Speechcraft, Enchanting = Intelligence/3

### ENCH/SPEL Conversion
- **ENCH Cast Type** varies by enchantment type: Scroll→4(Scroll), Staff/Weapon→2(Fire and Forget), Apparel→0(Constant Effect)
- **SPEL Cast Type**: Always Fire and Forget(2)
- **SPEL Flag Remapping**: $10→$80000 (No Absorb/Reflect), $20→$100000 (No Dual Cast Modifications), $40→$200000
- **Target Type**: Derived from first magic effect's EFIT\Type (exported as FirstEffect.Type)
- **ENCH Flags**: Only keep No Auto Calc ($08→$01)

### FACT Conversion
- **Evil flag** ($02) → all crime flags ($0080+$0100+$0200+$0400+$0800+$2000+$10000 = Assault/Murder/Trespass/Pickpocket/Steal/Werewolf/Attack on Sight)
- **Can Be Owner** ($8000) set on all factions
- **Relation Disposition → Combat Reaction**: ≤-50→Enemy(1), =100→Ally(3), ≥50→Friend(2), else→Neutral(0)

### ALCH Conversion
- **Food Detection**: Flag $02 (food) → set food flag + ITMPotionUse sound ($000CAF94) + VendorItemFood keyword
- **Poison Detection**: Name contains 'poison' → set poison flag ($20000) + ITMPoisonUse sound ($00106614) + VendorItemPoison keyword
- Needs OBND, standard potion sound otherwise

### CELL Conversion
- **Remove Oblivion Interior Flag**: Clear bit $08 from DATA flags on interior cells
- **Clear Hand Changed Flag**: Clear bit $40 from DATA flags
- **Fog Duplication**: TES4 has one fog color, TES5 has near+far → copy to both
- **Lighting Template**: Skyblivion assigns templates by music type (dungeon/public/default) — we don't do this yet

### WRLD Conversion
- **Clear Oblivion Flag**: Clear bit 2 from DATA
- **Move No LOD Water**: Bit $10 → bit $08
- **Add DNAM**: Default land height = -2048.0, water height = 0.0
- **Add NAMA**: Distant LOD multiplier = 1.0

### REFR Conversion
- **Lock Level Tiers**: 0-20→1(Novice), 21-40→25(Apprentice), 41-60→50(Adept), 61-80→75(Expert), 81+→100(Master)
- **Map Marker Types**: Camp→5, Cave→4, City→1, AyleidRuin→7, Fort→6, Landmark→11, Tavern→14, Settlement→3, DaedricShrine→34, OblivionGate→34

### LTEX Conversion
- **Create TXST**: Each LTEX needs a companion TXST record with diffuse texture path + derived normal map path (_n.dds suffix)
- **MATT Mapping** (Material Type → Skyrim MATT FormID):
  - Stone→$00012F34, Cloth→$00012F37, Dirt→$00012F38, Glass→$00012F39
  - Grass→$00012F3A, Metal→$00012F3B, Organic→$00012F3C, Skin→$00012F3D
  - Water→$00012F3E, Wood→$00012F3F, HeavyStone→$00012F40, HeavyMetal→$00012F41
  - HeavyWood→$00012F42, Chain→$00012F43, Snow→$00012F44

### SOUN Conversion
- **Create SNDR**: Each SOUN needs a companion SNDR (Sound Descriptor) with the actual sound file path linked via SDSC
- **Loop flag**: TES4 `SNDD.Flags` bit 4 (`0x10`) = "Is Looping". When set, write `LNAM = 0x00000800` (loop) in the SNDR record. `LNAM` is a 4-byte struct: byte[0]=Unknown, byte[1]=Looping enum (0x00=None, 0x08=Loop, 0x10=Envelope Fast, 0x20=Envelope Slow), byte[2]=Unknown, byte[3]=Rumble. `0x00000800` in little-endian = bytes [0x00, 0x08, 0x00, 0x00] = Loop. Default (`LNAM = 0`) = no loop / plays once. `0xFFFFFFFF` is INVALID and causes no sound to play.

### CLAS Conversion
- **Skill Weight Algorithm** (from Skyblivion):
  1. Start with all TES5 skills at weight 0
  2. Specialization (Combat/Magic/Stealth) adds +2 to corresponding TES5 skills
  3. Two primary attributes: each attribute's associated skills get +1
  4. Seven major skills: mapped to TES5 equivalents, each gets +3
  - Attribute→Skill mapping: Str→OneHanded/TwoHanded/Smithing, Int→Conjuration/Alchemy, Wil→Restoration/Alteration, Agi→Sneak/LightArmor/Lockpicking, Spd→Pickpocket/Speechcraft, End→Block/HeavyArmor, Per→Destruction/Illusion/Marksman/Enchanting, Luck→all skills +1

## Python Tools

### tes4_export (export pipeline)
- **Export**: `python -m tes4_export.export "path/to/Oblivion.esm"` — Pure TES4 binary dump to KEY=VALUE text
- **Pipeline**: `python convert.py` — Full export→import pipeline
- **List types**: `python -m tes4_export.export "path/to/Oblivion.esm" --list-types`
- Export performance: ~8s to parse 1.17M records from Oblivion.esm, ~36s total with write

### tes5_import (import pipeline)
- **Import**: `python -m tes5_import export/Oblivion.esm -o output/Oblivion.esm -m Skyrim.esm` — TES4 text → TES5 binary ESM/ESP
- **Tests**: `python -m pytest tests/ -v`
- Import performance: ~28K records converted from Oblivion.esm, 413MB output, 0 errors

### asset_convert (asset pipeline)
- **NIF conversion**: `python -m asset_convert.nif_converter <src_dir> <dst_dir> [--workers N]` — Full Oblivion→Skyrim NIF conversion (strips, textures, bones, collision, skin retarget)
- **SKIP_PATHS**: `asset_convert/nif_converter.py::SKIP_PATHS` — frozenset of path segments to skip during batch conversion (default: `menus`, `creatures`, `trees`). Trees are skipped because TREE records map model paths to `speedtrees/` via spt_converter — the original `trees/` geometry NIFs are not referenced at all.
- NIF conversion stats: 8032 source NIFs from Oblivion BSAs. 7380 v20 files converted (91.9%). 650 v10/v4 files copied as-is.
- **SpeedTree (.spt) conversion**: `asset_convert/spt_converter.py` — Converts Oblivion `.spt` files to Skyrim NIFs by matching pre-converted assets in `assets/speedtrees/`. Uses `_spt_to_skyblivion()` name mapping first (e.g. `TreeAsh01SU` → `treeaspen01summer`), then difflib fuzzy match on species stem as fallback. 328 NIFs indexed from `assets/speedtrees/Meshes/Oblivion/Landscape/Trees/`. Textures are bulk-copied from `assets/speedtrees/Textures/`. Wired into `asset_pipeline.py` Phase 4. Do NOT use procedural SPT generation — asset matching produces far better results.

### tools/ (debug/analysis. NOT meant for one-off tools. Only multi-use tools that take args. Should also have multiple functions per file, not one-off scripts.)
- **NIF analyzer**: `python tools/tes4_nif_analyzer.py <nif_or_dir> [--outdir dir] [--max N]` — Dump NIF structure to text
- **NIF analyzer (Skyrim)**: `python tools/tes5_nif_analyzer.py <nif_or_dir> [--outdir dir] [--max N]` — Same format for Skyrim NIFs
- **ESM reader**: `python tools/tes5_esm_reader.py <esm> [--outdir dir] [--types TYPE ...]` — TES5 binary reader with per-type KEY=VALUE output
- **Cell mesh lister**: `python tools/cell_meshes.py <export_dir> --cell <FormID_or_EditorID> [--cell ...] [--meshes-only]` — Lists all placed base objects + model paths in a cell; multiple --cell prints the mesh-set intersection. Use to find the suspect mesh set when a specific cell crashes.
- **NIF block scanner**: `python tools/nif_block_scan.py <dir> [--has TYPE]... [--any TYPE...] [--histogram] [--workers N]` — Header-only binary block-type search (block names are plaintext in NIF headers; ripgrep skips binaries so use this instead). `--has X --has Y` = the "0 vanilla files pair X with Y" diagnostic; `--histogram` = block-type census over a tree.
- **Particle chain dumper**: `python tools/psys_dump.py <nif> [...] [--convert]` — Dumps everything that determines particle visibility: BSXFlags, controller chains (flags/freq/start/stop, interpolators incl. visibility interpolator + birth rate), every modifier with all fields, emitter params, NiPSysData, shader/alpha properties. `--convert` runs the converter in-memory first and dumps the RESULT (works around PyFFI being unable to re-read our hand-rolled Skyrim output). This is the tool that found the missing BSX Animated bit.
- **Collision sanity checker**: `python tools/collision_sanity.py <nif_or_dir_or_listfile.txt> [--constraints] [--geometry] [--quiet]` — Walks all bhk blocks: NaN/Inf sweep, degenerate hulls/lists, non-unit constraint axes, hinge limit ordering; `--constraints` dumps full descriptor values for source-vs-converted comparison; `--geometry` additionally NaN-sweeps RENDER geometry (verts/normals/UVs/bound spheres) — this is what found the silent cell-load CTDs.

### verify_plugin.py
- **Summary**: `python verify_plugin.py <plugin.esp>` — record counts, version info
- **Integrity checks**: `--check` — missing OBND, wrong form version, CELL DATA size, NPC_ race/ACBS
- **Record dump**: `--dump --verbose` — hex dump of all subrecords
- **Filter**: `--type NPC_`, `--formid 00012345`, `--edid SomeEditor`
