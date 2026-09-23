# `tes5_import/generated/`

Artifacts produced by a tool rather than written by hand. **Read this before
editing or deleting anything here.**

## Regenerate with

| File | Command |
|---|---|
| `ctda_param_types.py` | `python tools/generators/gen_ctda_param_types.py <path>/wbDefinitionsTES5.pas -o tes5_import/generated/ctda_param_types.py` |
| `ctda_fnv_remap.py` | `python tools/generators/gen_ctda_fnv_remap.py references/xEdit/Core/wbDefinitionsFNV.pas references/xEdit/Core/wbDefinitionsTES5.pas -o tes5_import/generated/ctda_fnv_remap.py` |
| `vanilla_mgef_data.py` | `python tools/generators/gen_vanilla_mgef_table.py` |
| `dialog_engine_tables.json` | `python tools/disasm/dialog_engine_extract.py --json tes5_import/generated/dialog_engine_tables.json` |
| `morrowind_quest_names.json` | `python tools/generators/gen_morrowind_quest_names.py` |

Each is derived from an external source (xEdit's `wbDefinitionsTES5.pas`, the
Skyrim.esm MGEF dump, `SkyrimSE.exe`, the UESP dump). Hand edits are lost on
the next run.

## 🛑 DO NOT REGENERATE

**`objective_short_text.json` — 6,996 hand-authored lines. There is no tool that
can rebuild it.**

Skyrim shows two quest texts: the long retrospective log entry (CNAM) and a
short imperative line (NNAM) on the objective HUD. Oblivion authored only one
string per stage, so **there is no TES4 source for the short form** — every
entry here was written by reading the long journal text and composing the short
one, keyed on `(EditorID, stage_index)`.

`tools/generators/objective_text_extract.py` does **not** produce this file. It
reports which slots the table must still cover, and writes `temp/slots.json`.
Running it can never regenerate the content, only tell you what is missing.

**`morrowind_quest_names_authored.json` — hand-written TES3 quest names.** It
covers only the journals that author no QSTN name and that the UESP table
misses, and it wins over the UESP table. Add to it by reading the journal's
pages; nothing generates it.
See [morrowind_runtime.md](../../docs/commentary/morrowind_runtime.md#quest-names).
