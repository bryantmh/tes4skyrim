# Pipeline Reference — orchestration, caching, layout, export format

Linked from [CLAUDE.md](../CLAUDE.md). Everything about *running* the
conversion and the shape of the data that moves between stages. For per-tool
command lines see [python_tools_reference.md](python_tools_reference.md).

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
| `refs_from_assets` + `nif_converter._harvest_texture_bytes` (binary) | `_texture_refs_in`: `bytes.find` each `.dds`, walk back over legal bytes | 13.8x, `build_refs` 87.6 s → 11.5 s |

The binary case could not use a substring reject — every `.bto` really does
contain `.dds`. Watch the bound when touching `_texture_refs_in`: the old
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
   already routes through `asset_convert/hkx_xml.py`'s `_to_hkxcmd_path()`,
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
   (script compilation's header lookup, `asset_convert/skyrim_assets.py`'s
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
- FormID mappings live in `export/mappings/<filename>.FormID_Mapping.txt`.
- Processing `Knights.esp` reuses the cached `Oblivion.esm` export + mappings.
- `--no-cache` forces a re-export.

### 🔴 Generated FormIDs are NOT stable across builds — an old save is not a valid test bed

`writer.alloc_formid()` is a plain sequential counter, so every record the
importer *invents* (as opposed to converting from TES4) takes the next number in
one shared stream. Adding or removing records anywhere early shifts everything
after it. On Nehrim that stream holds **25,891 records**:

| | |
|---|---|
| REFR | 11,111 |
| NAVM | 3,350 |
| ARMA | 2,215 |
| OTFT | 1,754 |
| IDLE / STAT / SNDR / DLBR | 1,386 / 1,248 / 1,045 / 916 |
| VTYP | 116 |

Skyrim persists an actor's inventory and a reference's state in the save once it
has been loaded. Re-run `--import-only` after a change that emits a different
number of early records — 2026-08-08 the voice fix added 28 VTYP and the
female-only-armature fix added 5 ARMA — and every OTFT, ARMA and REFR after them
moves. A save made against the previous ESM then resolves an actor's stored
equipment to FormIDs that now mean something else, and the actor **stands there
naked** even though the outfit, the armature and the meshes are all correct.
That exact false alarm cost a debugging cycle: `ErothinKampfmagier01` verified
clean at every level (5-piece outfit, armature present, meshes on disk,
BSDismember partition 32, `_0`/`_1` pair complete) and was still naked in game.

**Always test an ESM change on a NEW GAME.** `resetinventory` on the actor is the
quick confirmation — if it dresses, the save was stale, not the build. Making
the allocation deterministic (keyed on the source record rather than on
call order) would fix this properly and is not yet done.

## Stages

1. **Export** (`tes4_export`) — reads the TES4 binary, writes KEY=VALUE text,
   one file per record type. Pure dump.
2. **Import** (`tes5_import`) — reads the text, applies every TES4→TES5
   transformation, writes the binary ESM/ESP. Type mapping (CREA→NPC_,
   CLOT→ARMO, LVLC→LVLN), FormID remapping, GRUP hierarchy (CELL/WRLD/DIAL),
   companion records (TXST for LTEX, SNDR for SOUN), LAND binary data.
3. **Assets** (`asset_convert`) — BSA extraction, NIF/texture/SpeedTree/sound
   conversion, LOD generation, BSA packing.

`import_main.py` runs a long phase sequence (Phase 0 pre-scans through Phase 5
dialogue). Ordering matters — e.g. PACK is written in its own Phase 3b2 after
QUST because quest packages need the aliases to exist first, and the ForceGreet
topic binding is patched in after Phase 5. Read the phase comments in
`import_main.py` rather than trusting a copy of the list here.

## Skipped record types

`SKIP_TYPES` in [tes5_import/constants.py](../tes5_import/constants.py) is the
single source of truth. Currently skipped: ROAD, SCPT, SKIL, BSGN, RACE, MGEF,
CSTY, IDLE, GMST, EYES, HAIR.

Notably **converted** (do not assume otherwise): GLOB, CLAS, CLMT, WATR, PACK,
WTHR, REGN. PACK is converted in its own phase (3b2, after QUST) rather than
via the generic dispatch, and so is WTHR (Phase 2b — it mints four IMGS
companions per weather for HDR tone mapping, see
[weather_climate_conversion.md](weather_climate_conversion.md)). REGN is
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
  gui.py / gui.pyw        # GUI front-end
  conversion_config.json  # file list and settings

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
  external/               # third-party binaries (see README license table)
  tests/ tools/ docs/ references/ temp/
  export/                 # cached exports (gitignored)
  output/                 # converted plugins (gitignored)
```

## Verifying output in SSEEdit

```powershell
if (Test-Path SSEEdit_log.txt) { Remove-Item SSEEdit_log.txt -Force }
$tes5 = "C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data"
$tp = "temp_plugins.txt"
"*Skyrim.esm`n*Oblivion.esm" | Set-Content $tp -Encoding UTF8
$args = "-P:`"$tp`" -D:`"$tes5`" -autoload -IKnowWhatImDoing `"Oblivion.esm`""
Start-Process -FilePath ".\sseEdit.exe" -ArgumentList $args -WorkingDirectory (Get-Location).Path
```
