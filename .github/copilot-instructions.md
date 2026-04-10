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
    collision.py          # Havok collision (bhkNiTriStrips→bhkPackedNiTriStrips via MOPP_RL)
    collision_new.py      # Pure-Python collision (no MOPP_RL dependency)
    skin_retarget.py      # Skeleton retargeting (Oblivion Bip01 → Skyrim NPC bones)
    skyrim_overrides.py   # Bone mapping, BSX flags, biped slot tables
    bsa_extract.py        # BSA extraction with manifest caching
    asset_pipeline.py     # 3-phase orchestrator: extract→convert→output
    spt_converter.py      # SpeedTree .spt → Skyrim NIF (asset-matching from assets/speedtrees/)
    MOPP_RL.exe           # Havok MOPP generation tool (self-contained)
    template.nif          # Template NIF required by MOPP_RL.exe
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
  Run: `python -m asset_convert.nif_converter <src_dir> <dst_dir> [--workers N]`.
- **NIF conversion stats**: 8032 source NIFs from Oblivion BSAs. 7380 v20 files converted (91.9%). 650 v10/v4 files copied as-is. 2 remaining parse errors (magic effect particle NIFs).
- **NIF bhk conversion details** (Session 19+):
  - bhkRigidBody/T: Oblivion=236+n*4, Skyrim=250+n*4. Key: two `Unknown Int 2` fields with different vercond (UV2>34 vs UV2≤34). Bytes [44:52] need rearrangement, not just passthrough. Translation/Mass/Friction are at fixed offsets (52, 180, 192 in Oblivion, 52, 180, 200 in Skyrim)
  - Crash signature: SkyrimSE.exe+0A882E6 reading from 0xFFFF* addresses = corrupted bhkRigidBody pointers from misaligned fields
  - bhkNiTriStripsShape: Collision NiTriStripsData must NOT be renamed to NiTriShapeData (template type mismatch). Writer must write strips format, not triangulated.
  - Constraint descriptors (RagdollDescriptor, LimitedHingeDescriptor, HingeDescriptor, MalleableDescriptor): UV2≤16 vs UV2>16 field REORDERING + Motor addition. FULLY IMPLEMENTED in Session 20.
  - bhkMoppBvTreeShape.build_type: MOPP_RL.exe writes 0xCD (uninit memory) for build_type. Must set build_type=1 (BUILT_WITHOUT_CHUNK_SUBDIVISION) in `_extract_mopp_result()` immediately after reading MOPP_RL output.
  - MOPP_RL.exe hardcodes `template.nif` in its binary.
  - `bhkCompressedMeshShape.target` must point to the BSFadeNode root (identity transform). The body orientation is encoded in `bhkRigidBodyT.rotation` instead. Static collision MUST be on the root BSFadeNode — having bhkCollisionObject on a child NiNode causes STACK_OVERFLOW in Skyrim's `hkpCollisionDispatcher`.
  - When root rotation baking wraps geometry in an inner NiNode, the collision STAYS on the root BSFadeNode. The bhkRigidBodyT data is already in Havok world-space coordinates — the target node's transform does NOT additionally rotate/translate the rigid body. **Do NOT modify bhkRigidBodyT.rotation or .translation when zeroing the root transform.** The original Oblivion bhk values are already correct for Skyrim.
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
- template.nif copied to `C:\template.nif` (done automatically by the pipeline)

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
- No DNAM: vanilla Skyrim LTEX TXSTs omit DNAM. The 0x0001 "No Specular Map" flag only applies to the object (BSLightingShader) path, NOT the landscape shader. Writing it has no positive effect.
- TX00 = diffuse (`tes4\landscape\<icon>.dds`)
- TX01 = normal map (`tes4\landscape\<icon>_n.dds`)
- LTEX SNAM specular exponent: **pass through the TES4 value**. SNAM is a Phong exponent used directly by the landscape shader. Setting SNAM=0 gives `pow(NdotH, 0) = 1.0` everywhere → whole landscape appears blindingly bright white. TES4 landscape textures use ~30 (moderate gloss). Do NOT write SNAM=0.

### Dialogue conversion notes (DIAL / INFO / QUST / DLBR / DLVW)
- **Skyrim dialogue architecture**: QUST owns DIAL topics → DLBR branches link DIAL to QUST → INFO records have GetIsVoiceType conditions → engine routes dialogue by voice type
- **TES5 DIAL subrecord order**: EDID FULL PNAM(float priority=50.0) [BNAM(branch FID)] QNAM(quest) DATA(4B) SNAM(4-char code U32) TIFC(info count U32)
- **DIAL DATA** = TopicFlags(U8) + Category(U8) + Subtype(U16 LE) = 4 bytes total
- **DIAL SNAM** = 4-char subtype code stored as raw ASCII bytes (e.g. b'HELO', b'CUST')
- **DIAL Category**: Bark topics → 7(Misc) or 3(Combat) or 5(Detection); all conversation topics → 0(Topic). Old TES4 type-based mapping removed.
- **DIAL BNAM**: ONLY present on conversation topics (links to DLBR). Bark topics (GREETING, Attack, Hit, Flee, Idle, etc.) must NOT have BNAM — they fire automatically.
- **DLBR (Dialog Branch)**: EDID + QNAM(quest FID) + TNAM(0=Player) + DNAM(1=TopLevel) + SNAM(starting DIAL FID). Created for each non-bark DIAL topic.
- **DLVW (Dialog View)**: EDID + QNAM(quest) + BNAM[](branch FIDs) + TNAM[](topic FIDs) + ENAM(view type) + DNAM(show all text). CK UI metadata, one per quest.
- **GetIsVoiceType (func 426)**: Every Skyrim INFO must have this condition. Routes dialogue to the correct voice type. OR'd for multiple voice types. NPC-specific INFOs use GetIsID(npc_fid) from TES4 + GetIsVoiceType for the NPC's voice type. Generic INFOs get ALL 27 custom voice types.
- **CTDA OR flag**: bit 0 of type byte. Voice type chain: VT1(OR)|VT2(OR)|...|VTn(AND) → evaluates as (any voice type matches) AND (remaining TES4 conditions). LAST voice type CTDA must NOT have OR flag.
- **Voice type injection order**: Voice type CTDAs are injected BEFORE TES4-converted conditions in INFO. This isolates the OR chain from any trailing OR flags in TES4 data.
- **QUST dialogue flags**: QUSTs that own DIAL topics get: 0x0001(StartGameEnabled) + 0x0010(StartsEnabled) = 0x0011. **NEVER set HasDialogueData (0x8000)** — Skyrim does not use this flag and it blocks dialogue processing.
- **QUST DNAM format**: 12 bytes: Flags(U16) + Priority(U8) + FormVer(U8=**0 always**) + Unknown(4B) + Type(U32). FormVer must be 0 (not 44). Dialogue quest priority capped at 50.
- **Orphan DIALs**: DIALs without quest association get assigned to a catch-all quest `TES4DialogueGeneric` (Flags=0x0011, Priority=0, FormVer=0). ALL DIALs MUST have QNAM or the engine ignores them.
- **NPC→VTYP mapping**: Built from NPC_+CREA records using TES4_RACE_FID_TO_EDID → VOICE_TYPE_MAP[(race_edid, gender)]. 3396 NPCs mapped to 27 voice types. ALL speakable NPCs (including CREA→NPC_) MUST have VTCK.
- **TES4-only condition functions dropped**: GetDisposition(76), GetVampire(40), IsYielding(104), IsPlayerInJail(171), GetPCInfamy(251) — these would always fail in TES5 and block valid dialogue.
- **Quest-dependent conditions on barks**: Functions {56(GetQuestRunning), 58(GetStage), 59(GetStageDone), 99(GetIsPlayableRace), 79(GetFactionRank), 71(GetFactionReaction), 67(GetCurrentAIPackage)} stripped from bark INFOs — barks fire automatically without quest context.
- **CTDA Use Global flag**: bit 2 (0x04), NOT bit 5 (0x20). Wrong bit causes all global-based conditions to fail.
- **TES5 INFO subrecord order**: EDID ENAM CNAM [TCLT[]] [TRDT NAM1 NAM2 NAM3]* CTDAs
- **INFO ENAM** = Flags(U16) + ResetHours(U16) = 4 bytes. Flags map from TES4 DATA.Flags with compatible mask 0x37 (bits 0=Goodbye, 1=Random, 2=SayOnce, 4=InfoRefusal, 5=RandomEnd — same bit positions)
- **INFO TRDT** = 24 bytes: EmotionType(U32) + EmotionValue(U32) + Unused(4) + ResponseNumber(U8) + Unused(3) + Sound(FormID=0 U32) + Flags(U8) + Unused(3)
- **INFO CNAM** = Favor Level U8 (0=None, required)
- **INFO TCLT** = repeated FormID subrecords for each choice/next topic (xEdit: "Link To" array)
- **QUST INDX** = StageIndex(U16) + StageFlags(U8) + Unknown(U8) = 4 bytes (NOT 2 bytes like TES4)
- **QUST stage log entries**: exported as `Stage[i].LogCount + Stage[i].Log[j].{Flags,Text}`; imported with one QSDT (U8) + optional CNAM (string) per log entry
- **Voice files**: TES4 path `Sound\Voice\<plugin>\<Race>\<Gender>\<dialFID>_<infoFID>.mp3`; TES5 path `Sound\Voice\<plugin>\<VoiceType>\<infoFID>_0.mp3`. Use `asset_convert.bsa_extract.organize_voice_files()` to reorganize after extraction. Audio format conversion (MP3→XWM/FUZ) required for actual playback — not automated.
- **Race→VoiceType mapping** is in `_TES4_VOICE_TYPE_MAP` in bsa_extract.py; includes all Oblivion playable races + Shivering Isles races
- **Conversion stats**: 3817 DIAL topics (851 barks, 2966 conversation), 19278 INFOs, 2966 DLBR branches, 275 DLVW views, 345 dialogue quests

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
- Keep NiPSysGrowFadeModifier (controls particle size — without it, base_scale=0 makes particles invisible). Set base_scale=1.0.
- Keep NiPSysColorModifier (particle color transitions).
- NiPSysData: preserve original max particle count (`max(num_vertices, 75)`). Set has_sizes=True and sizes initialized to 1.0.
- PyFFI "Block size check" warnings on particle read-back are a PyFFI limitation, not a game issue.

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

### NIF furniture marker conversion
- Oblivion: `BSFurnitureMarker` (NiExtraData) with FurniturePosition using `orientation` (ushort, milliradians), `position_ref_1`/`position_ref_2` (byte)
- Skyrim: `BSFurnitureMarkerNode` (inherits BSFurnitureMarker) with FurniturePosition using `heading` (float, radians), `animation_type` (ushort: 1=Sit, 2=Sleep, 4=Lean), `entry_properties` (bitflags: front, behind, right, left, up)
- Conversion: `heading = orientation/1000.0 + math.pi` (**+π offset required** — Oblivion orientation is the direction the furniture faces; Skyrim heading is the direction the occupant faces, which is opposite); ref 1-10→Sleep(2), ref 11-19→Sit(1); ref 1/11→left, ref 2/12→right, ref 13→front, ref 14→behind
- **Z offset must be negated**: `dst_pos.offset.z = -src_pos.offset.z`. Oblivion stores Z as negative (seat below mesh origin). Skyrim expects positive Z (seat above floor origin). Skyrim reference bench Z = +33.84; Oblivion chair Z = -33.91. Without negation, NPCs sit in the air.
- **Vanilla reference** (sovbench01.nif, wrtemplebench01.nif): Z ≈ +33.84, heading = π, entry = front
- BSFurnitureMarker lives in root NiNode's extra_data_list. During NiNode→BSFadeNode conversion, it must be explicitly converted and transferred (bulk extra_data_list copy breaks animated objects)

### NIF analyzer tools
- `python tools/tes4_nif_analyzer.py <nif_or_dir> [--outdir temp/analysis] [--max N]` — Dumps NIF structure to human-readable text
- `tools/tes5_nif_analyzer.py` re-exports from tes4 version (PyFFI handles both versions)
- Useful for diff-based comparison between Oblivion, converted, and Skyrim reference NIFs

### OBND (Object Bounds) defaults
- ESM records without OBND crash the engine. Import script generates per-type defaults:
  - MISC=(-5,-5,0,5,5,8), KEYM=(-3,-3,0,3,3,3), WEAP=(-5,-5,0,5,5,30), STAT=(-50,-50,0,50,50,80)
  - ARMO=(-15,-10,0,15,10,30), NPC_/CREA=(-12,-12,0,12,12,60), LIGH=(-6,-6,0,6,6,20)
  - Other types get (-5,-5,0,5,5,5) as fallback

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

### verify_plugin.py
- **Summary**: `python verify_plugin.py <plugin.esp>` — record counts, version info
- **Integrity checks**: `--check` — missing OBND, wrong form version, CELL DATA size, NPC_ race/ACBS
- **Record dump**: `--dump --verbose` — hex dump of all subrecords
- **Filter**: `--type NPC_`, `--formid 00012345`, `--edid SomeEditor`
