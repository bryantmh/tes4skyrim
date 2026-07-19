# Python Tools Reference

Linked from [CLAUDE.md](../CLAUDE.md). Command reference for the pipeline's
Python modules and `tools/` debug utilities.

## tes4_export (export pipeline)
- **Export**: `python -m tes4_export.export "path/to/Oblivion.esm"` — Pure TES4 binary dump to KEY=VALUE text
- **Pipeline**: `python convert.py` — Full export→import pipeline
- **List types**: `python -m tes4_export.export "path/to/Oblivion.esm" --list-types`
- Export performance: ~8s to parse 1.17M records from Oblivion.esm, ~36s total with write

## tes5_import (import pipeline)
- **Import**: `python -m tes5_import export/Oblivion.esm -o output/Oblivion.esm -m Skyrim.esm` — TES4 text → TES5 binary ESM/ESP
- **Tests**: `python -m pytest tests/ -v`
- Import performance: ~28K records converted from Oblivion.esm, 413MB output, 0 errors

## asset_convert (asset pipeline)
- **NIF conversion**: `python -m asset_convert.nif_converter <src_dir> <dst_dir> [--workers N]` — Full Oblivion→Skyrim NIF conversion (strips, textures, bones, collision, skin retarget). See [nif_conversion_notes.md](nif_conversion_notes.md) for the deep implementation notes.
- **SKIP_PATHS**: `asset_convert/nif_converter.py::SKIP_PATHS` — frozenset of path segments to skip during batch conversion (default: `menus`, `creatures`, `trees`). Trees are skipped because TREE records map model paths to `speedtrees/` via spt_converter — the original `trees/` geometry NIFs are not referenced at all.
- NIF conversion stats: 8032 source NIFs from Oblivion BSAs. 7380 v20 files converted (91.9%). 650 v10/v4 files copied as-is.
- **Book inventory art (INAM)**: `python -m asset_convert.book_inam <plugin> [--extract-dir export] [--output-dir output] [--templates-dir "references/Skyrim Meshes"] [--skyrim-data <SSE Data dir>] [--workers N]` — bakes each distinct BOOK model's Oblivion textures onto the vanilla Skyrim reading rigs and emits `meshes/tes4/clutter/books/inv/<base>.nif` + baked DDS. Runs automatically in `convert.py` phase_assets. See [nif_conversion_notes.md](nif_conversion_notes.md#book-inventory-art-inam-reading-rigs--books-were-invisible-with-no-text-when-opened-solved-2026-07-18).
- **SpeedTree (.spt) conversion**: `python -m asset_convert.spt_converter <trees_src> <nif_dst> [--export-dir <dir>]` — see [nif_conversion_notes.md](nif_conversion_notes.md#speedtree-spt-conversion) for the full algorithm.
- **Preview/iteration tool**: `python tools/spt_preview.py <spt_or_dir> [--views 0,90] [--out dir]` renders generated tree geometry to PNG with real leaf textures beside Oblivion's own billboard render for A/B comparison.
- **BSA packing**: `python -m asset_convert.bsa_pack <plugin.esm> [--output-dir output] [--compress-textures] [--size-limit BYTES]` — packs `output/<plugin>/` into Skyrim SE BSAs via `external/bsarch/BSArch.exe`. Normally produces `Oblivion.bsa` (meshes + every other non-texture dir, incl. `sound/` and `scripts/`) and `<stem> - Textures.bsa`.
  - **2 GiB limit + overflow ESLs**: BSA file-data offsets are 32-bit, so an archive cannot exceed 2,147,483,648 bytes (`BSA_HARD_LIMIT`). Files are binned in path order under `BSA_SIZE_LIMIT` (the hard limit minus a 64 MiB budget for the BSA header/folder/file/name tables, which are *not* counted in the raw payload sum). Overflow spills into extra archives.
  - Skyrim only auto-mounts `<PluginStem>.bsa` and `<PluginStem> - Textures.bsa` for plugins in the load order, so each overflow archive is paired with a generated record-free dummy **ESL** loader whose stem matches it: `oblivion_loader.esl` mounts `oblivion_loader.bsa` *and* `oblivion_loader - Textures.bsa`; then `oblivion_loader_1.esl`, etc. The ESL flag (`0x0200`) keeps loaders out of the 255-plugin limit. **The loaders must be enabled in the load order** or their assets are missing in-game.
  - Stale overflow `.bsa`/`.esl` files from a previous, larger run are swept on each run — otherwise a later run could re-create `oblivion_loader.esl` on top of a stale `oblivion_loader.bsa` and silently serve assets from the old conversion.

## tools/ (debug/analysis)

Rule: NOT meant for one-off tools. Only multi-use tools that take args. Should also have multiple functions per file, not one-off scripts.

- **NIF analyzer**: `python tools/tes4_nif_analyzer.py <nif_or_dir> [--outdir dir] [--max N]` — Dump NIF structure to text. `--bbox` prints world-space geometry bounding boxes.
- **NIF analyzer (Skyrim)**: `python tools/tes5_nif_analyzer.py <nif_or_dir> [--outdir dir] [--max N]` — Same format for Skyrim NIFs (re-exports from tes4 version; PyFFI handles both versions)
- **ESM reader**: `python tools/tes5_esm_reader.py <esm> [--outdir dir] [--types TYPE ...]` — TES5 binary reader with per-type KEY=VALUE output
- **Cell mesh lister**: `python tools/cell_meshes.py <export_dir> --cell <FormID_or_EditorID> [--cell ...] [--meshes-only]` — Lists all placed base objects + model paths in a cell; multiple --cell prints the mesh-set intersection. Use to find the suspect mesh set when a specific cell crashes.
- **NIF block scanner**: `python tools/nif_block_scan.py <dir> [--has TYPE]... [--any TYPE...] [--histogram] [--workers N]` — Header-only binary block-type search (block names are plaintext in NIF headers; ripgrep skips binaries so use this instead). `--has X --has Y` = the "0 vanilla files pair X with Y" diagnostic; `--histogram` = block-type census over a tree.
- **BSInvMarker convention survey**: `python tools/inv_marker_survey.py <vanilla_meshes_dir> [--max N] [--multiaxis] [--filter substr] [--detail CONV]` — Scores every candidate inventory-rotation convention (Euler order x angle sign x camera axis) against vanilla meshes' markers + geometry; `--detail` prints per-mesh alignment for one convention. This is how the `asset_convert/inv_marker.py` engine convention was derived/verified.
- **Particle chain dumper**: `python tools/psys_dump.py <nif> [...] [--convert]` — Dumps everything that determines particle visibility: BSXFlags, controller chains, every modifier, emitter params, NiPSysData, shader/alpha properties. `--convert` runs the converter in-memory first and dumps the RESULT (works around PyFFI being unable to re-read our hand-rolled Skyrim output).
- **Collision sanity checker**: `python tools/collision_sanity.py <nif_or_dir_or_listfile.txt> [--constraints] [--geometry] [--quiet]` — Walks all bhk blocks: NaN/Inf sweep, degenerate hulls/lists, non-unit constraint axes, hinge limit ordering; `--constraints` dumps full descriptor values; `--geometry` additionally NaN-sweeps RENDER geometry.
- **MOPP validator**: `python tools/mopp_validator.py <nif_or_dir> [--verbose|--summary|--histogram|--workers N]` — validates MOPP walk cleanliness AND exact terminal-key-set == shape-key decode.
- **Navmesh renderer**: `python tools/navmesh_render.py --cell <FormID_or_EditorID> [--out png] [--size N]` — top-down render of LAND heightmap, mesh footprints, pathgrid, generated navmesh triangles, doors. Primary PGRD→NAVM iteration tool.
- **Navmesh dumper**: `python tools/navmesh_dump.py <esm> [--navi|--navm] [--nvnm-decode] [--max N]` — decompresses + decodes real NAVI/NAVM/NVNM for format verification.
- **Terrain LOD renderer**: `python tools/terrain_lod_render.py --esm <esm> --worldspace <name> --cell X Y --radius R` — side-by-side hillshade + composited diffuse (incl. water murk) for terrain LOD iteration.
- **LOD NIF inspector**: `python tools/lod_nif_inspect.py` — dumps .btr/.bto geometry+shader.
- **Terrain LOD tile debugger**: `python -m tools.terrain_lod_tile_debug --tiles LEVEL,TX,TY ... [--png-dir temp]` — regenerates specific .btr/.dds tiles in-process (single-threaded), reports water quad counts, dumps diffuse PNGs.
- **Terrain LOD texture probe**: `python -m tools.terrain_lod_tex_probe [--cell X Y]` — audits LTEX→TXST→dds resolution (ok/missing/loadfail) and per-cell BTXT/ATXT layer data.
- **KF animation explorer**: `python tools/kf_animation_explorer.py --build-cache` — searches .kf animation corpus for skin retargeting.
- **NPC skin census**: `python -m tools.census_npc_skin [--dump references/Skyrim.esm] [--race NordRace]` — joins RACE tint-mask definitions (skin-tone TINI index per race+gender) with NPC_ tint layers to report the colors/TINV/QNAM vanilla NPCs actually use. Source of the `_RACE_SKIN_TONES` table in tes5_import/npc_face_mapper.py.
- **Voice audit (folder-level)**: `python tools/voice_audit.py [--esm ..] [--voice-dir ..] [--source-dir ..] [--csv out]` — recomputes every INFO's expected voice files (GetIsVoiceType folders × prefix × TRDT resp) from the BUILT ESM, diffs against disk, classifies misses (NO_SOURCE_AUDIO / PREFIX_MISMATCH / MISSING_IN_VTYP / NOT_ORGANIZED), orphans, voicemap drift.
- **Voice line table (per-NPC)**: `python tools/voice_line_table.py [--npc <edid>] [--status MISSING INVALID] [--csv out]` — one row per (INFO, response, resolved speaker); GetIsID speakers use the NPC's WRITTEN VTCK folder (the engine's actual lookup) and every file is structurally validated (FUZE/lip/RIFF, short-payload). Catches VTCK/gate/relocation drift, dead-line gate contradictions, invalid audio. See dialogue_conversion_notes.md.
- **Quest walkthrough emulator**: `python tools/quest_walkthrough.py --export export/Oblivion.esm --esm output/Oblivion.esm/Oblivion.esm --scripts output/Oblivion.esm/scripts --seq output/Oblivion.esm/seq/Oblivion.seq [--quest EDID] [--md report.md]` — symbolically plays every quest to a fixpoint over the CONVERTED data (TIF/QF fragments, attached scripts, VMAD property bindings, TCLT/Say/bark topic reachability, unlock-globals, CTDA gates) and diffs stage reachability against the same engine run on the TES4 export, naming the exact record/script that breaks each lost stage. Supersedes dialog_emulator.py for completability questions. See QUEST_AUDIT.md for the audit that built it.

## verify_plugin.py
- **Summary**: `python verify_plugin.py <plugin.esp>` — record counts, version info
- **Integrity checks**: `--check` — missing OBND, wrong form version, CELL DATA size, NPC_ race/ACBS
- **Record dump**: `--dump --verbose` — hex dump of all subrecords
- **Filter**: `--type NPC_`, `--formid 00012345`, `--edid SomeEditor`
