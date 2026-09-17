# docs/ — where documentation goes

Sorted by KIND, not by subject: the big files all span several subjects, but an
agent always knows which KIND it just produced.

## Where does my new document go?

1. Not prose (image, icon, data table)? → `assets/`
2. Describes a format or contract that exists independent of our code? → `reference/`
3. Describes work not yet done? → `plans/`, with `Status: PLAN` on line 2
4. A dated count over a corpus? → `audits/`
5. Explains code that EXISTS? → `commentary/`, named `<package>_<subsystem>.md`

**Naming:** `lower_snake_case.md`, named for its SUBJECT only. No ALLCAPS, no
`Title_Case`, no dates, and **no kind suffix** — the folder already states the
kind. `audits/aggression_faction.md`, not `..._audit.md`;
`commentary/asset_convert_nif.md`, not `..._notes.md`;
`reference/pipeline.md`, not `..._reference.md`.

**One kind per file.** A dated fix narrative inside a reference doc, or a
"verified correct, do NOT fix" finding inside an audit, belongs in `notes/`.
Split it out rather than appending to whichever file looked closest.

`python tools/validate/doc_links.py --index` checks that every link resolves
and that every doc below appears in this table.

## `reference/`

What a format or contract IS. Stable; no dates, no status.

| Doc | Covers |
|---|---|
| [creature_bug_reports.md](reference/creature_bug_reports.md) | User-filed creature bug reports, verbatim |
| [creature_race_equivalence.md](reference/creature_race_equivalence.md) | Skyrim ↔ Oblivion Creature Equivalence Map |
| [dialogue_engine_contracts.md](reference/dialogue_engine_contracts.md) | Dialogue contracts read out of SkyrimSE.exe |
| [havok_contact_hook.md](reference/havok_contact_hook.md) | The engine's contact callback: classes, slot 8, stable IDs |
| [item_swap_table.md](reference/item_swap_table.md) | Oblivion → Skyrim Item Swap Table (MISC + Ingredients/Food) |
| [morrowind_dialogue_format.md](reference/morrowind_dialogue_format.md) | TES3 DIAL/INFO export vocabulary the Morrowind runtime reads |
| [package_ai_contracts.md](reference/package_ai_contracts.md) | PACK / AI Package & CTDA Engine Contracts |
| [pipeline.md](reference/pipeline.md) | Pipeline Reference — orchestration, caching, layout, export format |
| [prior_art_php_scriptconverter.md](reference/prior_art_php_scriptconverter.md) | PHP ScriptConverter (Skyblivion) — Comprehensive Analysis |
| [python_tools.md](reference/python_tools.md) | Python Tools Reference |
| [record_mapping.md](reference/record_mapping.md) | TES4 → TES5 Record Mapping Reference |
| [script_convert_architecture.md](reference/script_convert_architecture.md) | `script_convert/` architecture — read this BEFORE writing any code here |
| [skyrim_commands.md](reference/skyrim_commands.md) |  |
| [skyrim_mountable_actor.md](reference/skyrim_mountable_actor.md) | What makes a Skyrim actor mountable |
| [tes4_record_definitions.md](reference/tes4_record_definitions.md) | TES4 (Oblivion) Complete Binary Record Definitions |
| [tes5_import_architecture.md](reference/tes5_import_architecture.md) | `tes5_import/` architecture — read this BEFORE writing any code here |
| [tes5_binary_format.md](reference/tes5_binary_format.md) | Skyrim SE (TES5/SSE) Binary File Format — Exact Layout |
| [tes_runtime_fragments.md](reference/tes_runtime_fragments.md) | TESRuntime animation cache fragments — the schema any mod emits |
| [xedit_scripting.md](reference/xedit_scripting.md) | xEdit Scripting Reference (historical) |

## `commentary/`

Why the SHIPPED code is the way it is. Named after the code it explains; opens with `**Code:**`. The DEFAULT.

| Doc | Covers |
|---|---|
| [asset_convert_animation.md](commentary/asset_convert_animation.md) | asset_convert/havok/hkx_anim.py — animation and behaviour graphs |
| [asset_convert_armor.md](commentary/asset_convert_armor.md) | asset_convert/character/body_wrap.py — worn armor, skin and fitting |
| [asset_convert_audio.md](commentary/asset_convert_audio.md) | asset_convert/audio/audio_converter.py - sound and music |
| [asset_convert_bsa.md](commentary/asset_convert_bsa.md) | asset_convert/sources/bsa_pack.py — BSA packing and staging |
| [asset_convert_collision.md](commentary/asset_convert_collision.md) | asset_convert/collision/collision.py — Havok collision |
| [asset_convert_creature.md](commentary/asset_convert_creature.md) | asset_convert/havok/creature_pipeline.py - creature conversion |
| [asset_convert_facegen.md](commentary/asset_convert_facegen.md) | asset_convert/character/facegen_tri.py - faces, skin tone and tints |
| [asset_convert_falloutnv.md](commentary/asset_convert_falloutnv.md) | asset_convert/collision/collision_material_falloutnv.py — FO3/FNV meshes |
| [asset_convert_mod_ingest.md](commentary/asset_convert_mod_ingest.md) | asset_convert/sources/mod_ingest.py - mod archive ingest |
| [asset_convert_nif.md](commentary/asset_convert_nif.md) | asset_convert/nif/nif_converter.py — NIF conversion |
| [asset_convert_shader.md](commentary/asset_convert_shader.md) | asset_convert/nif/nif_converter.py - shader values |
| [asset_convert_speedtree.md](commentary/asset_convert_speedtree.md) | asset_convert/speedtree/spt_generator.py - SpeedTree conversion |
| [asset_convert_terrain.md](commentary/asset_convert_terrain.md) | asset_convert/lod/terrain_lod.py — terrain, LOD and grass |
| [asset_convert_texture.md](commentary/asset_convert_texture.md) | asset_convert/texture/parallax.py — textures, shaders and parallax |
| [asset_convert_ui.md](commentary/asset_convert_ui.md) | asset_convert/ui/ui_menus.py - Oblivion UI in Skyrim |
| [ck_exe_disassembly.md](commentary/ck_exe_disassembly.md) | tools/disasm/ - CreationKit.exe as a source |
| [ck_navmesh_generation.md](commentary/ck_navmesh_generation.md) | tools/navmesh/ - how the CK generates navmesh |
| [ck_reference_init_hang.md](commentary/ck_reference_init_hang.md) | tes5_import/base/writer.py - the CK reference-init hang |
| [ck_vs_game_missing_objects.md](commentary/ck_vs_game_missing_objects.md) | tes5_import/ - objects in the CK, missing in game |
| [ck_warnings.md](commentary/ck_warnings.md) | tes5_import/ - what the CK complains about |
| [core_run_log.md](commentary/core_run_log.md) | core/run_log.py - per-run logs and their profiling timestamps |
| [ingame_testing.md](commentary/ingame_testing.md) | tools/dialog/ - in-game test methodology |
| [morrowind_runtime.md](commentary/morrowind_runtime.md) | tes_runtime/morrowind_runtime/, external/openmw/ - Morrowind dialogue and MWScript in-engine |
| [performance.md](commentary/performance.md) | the whole pipeline - performance and parallelism |
| [script_convert.md](commentary/script_convert.md) | script_convert/ - TES4 script to Papyrus |
| [script_convert_morrowind.md](commentary/script_convert_morrowind.md) | script_convert/ - what TES3 scripts do differently |
| [tes4_export_falloutnv.md](commentary/tes4_export_falloutnv.md) | tes4_export/record_types/falloutnv.py - FO3/FNV export deltas |
| [tes4_export_morrowind.md](commentary/tes4_export_morrowind.md) | tes4_export/tes3_reader.py, export_morrowind.py - TES3 export and Morroblivion compatibility |
| [tes5_import_conditions.md](commentary/tes5_import_conditions.md) | tes5_import/base/conditions.py - CTDA translation |
| [tes5_import_dialogue.md](commentary/tes5_import_dialogue.md) | tes5_import/dialogue/converter.py - dialogue and voice |
| [tes5_import_actors.md](commentary/tes5_import_actors.md) | tes5_import/record_types/actor_common.py, npc.py, creature.py - actor conversion |
| [tes5_import_falloutnv_actors.md](commentary/tes5_import_falloutnv_actors.md) | tes5_import/record_types/actors_falloutnv.py - FO3/FNV actor templates |
| [tes5_import_landscape.md](commentary/tes5_import_landscape.md) | tes5_import/record_types/region.py - REGN, LSCR and WATR |
| [tes5_import_magic.md](commentary/tes5_import_magic.md) | tes5_import/record_types/magic.py - magic conversion |
| [tes5_import_mod_merge.md](commentary/tes5_import_mod_merge.md) | tes5_import/overrides/manifest.py - merging a mod stack |
| [tes5_import_navmesh.md](commentary/tes5_import_navmesh.md) | tes5_import/navmesh/ - PGRD to NAVM, LAND and worldspace |
| [tes5_import_override.md](commentary/tes5_import_override.md) | tes5_import/overrides/nested.py - plugins with masters |
| [tes5_import_pipeline.md](commentary/tes5_import_pipeline.md) | tes5_import/pipeline*.py - phase ordering and the finalize pass |
| [tes5_import_package.md](commentary/tes5_import_package.md) | tes5_import/packages/converter.py - AI packages |
| [tes5_import_quest.md](commentary/tes5_import_quest.md) | tes5_import/base/object_scripts.py - quests and quest scripts |
| [tes5_import_sound.md](commentary/tes5_import_sound.md) | tes5_import/record_types/sound.py - SOUN, SNDR and SOPM |
| [tes5_import_world.md](commentary/tes5_import_world.md) | tes5_import/record_types/world.py - CELL, WRLD and placed references |
| [tes5_import_weather.md](commentary/tes5_import_weather.md) | tes5_import/record_types/world.py - weather and climate |
| [tes_runtime_guns.md](commentary/tes_runtime_guns.md) | tes_runtime/plugin/fire.cpp - the gun shot, reload key and ammo restriction in the SKSE plugin |
| [version_upgrade_planning.md](commentary/version_upgrade_planning.md) | version.py - which steps a new release owes |

## `plans/`

Designed, NOT yet built. Opens with `Status: PLAN`. Becomes commentary when built.

| Doc | Covers |
|---|---|
| [horse_rideability.md](plans/horse_rideability.md) | Rideable Horse Conversion: Oblivion CREA → Skyrim Mountable Actor |
| [morrowind_object_scripts.md](plans/morrowind_object_scripts.md) | Move TES3 object scripts off the lossy Papyrus path onto the vendored interpreter |
| [in_app_update.md](plans/in_app_update.md) | In-app update: download only what changed — design plan |
| [vanilla_creature_swap.md](plans/vanilla_creature_swap.md) | Plan — "Vanilla Creature Swap" ESP generator + GUI |
| [vanilla_item_swap.md](plans/vanilla_item_swap.md) | Plan — "Vanilla Item Swap" (ingredients, food, clutter) + preview renderer |

## `audits/`

A dated sweep over a corpus, with counts. Frozen once written; a re-audit is a NEW file.

| Doc | Covers |
|---|---|
| [aggression_faction.md](audits/aggression_faction.md) | Aggression / Ally / Enemy Conversion Audit |
| [ck_warnings.md](audits/ck_warnings.md) | CK Warnings Audit — Oblivion.esm |
| [fallout_nv_mesh_conversion.md](audits/fallout_nv_mesh_conversion.md) | Fallout NV / FO3 mesh conversion — what already works, and the particle-NIF defect |
| [morroblivion_mesh_axis_rotation.md](audits/morroblivion_mesh_axis_rotation.md) | Morroblivion meshes authored along +Y; 45 meshes, 2,578 refs carry a compensating pitch |
| [package_conversion.md](audits/package_conversion.md) | PACK Conversion Audit — 2026-08-17 |
| [quest.md](audits/quest.md) | Quest Completability Audit — Oblivion.esm conversion |
| [quest_script_conversion.md](audits/quest_script_conversion.md) | Quest Script Conversion Audit |
| [skse_conversion.md](audits/skse_conversion.md) | SKSE / OBSE Convertibility Audit — Grounded in the Original Nehrim Scripts |
| [worldspace_havok_range.md](audits/worldspace_havok_range.md) | Worldspace extents vs the Havok ±64-cell band; why recentering does not help |

## `assets/`

Non-prose files. `banner.png` and `favicon.ico` are loaded at RUNTIME by `gui.py`.
| [preexisting_test_failures.md](audits/preexisting_test_failures.md) | 50 tests failing on clean `master`, and why |
