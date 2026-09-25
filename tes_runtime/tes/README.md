# TESRuntime

The runtime every converted game needs, whatever it was converted from.

- **Jails** (`crime.cpp`). Oblivion and Morrowind jail an arrested player at the
  nearest enabled prison marker; Skyrim fixes one jail per crime faction. Every
  2 s this points every converted crime faction's jail, follower-wait marker and
  evidence chests at the enabled jail nearest the player, from the
  `<plugin>.crime.json` sidecars the importer writes, and keeps the stolen goods
  when a sentence is served.
  [tes_runtime_crime.md](../../docs/commentary/tes_runtime_crime.md#nearest-jail)
- **Journal stage text** (`journal_objectives.cpp`, `journal_log.cpp`). A clicked
  quest objective shows the journal text the quest had when that objective first
  appeared. Works on every quest, converted or not. Needs the journal movies
  patched by Build > Quest Journal Stage Text (`asset_convert/ui/journal_patch.py`).
  [tes_runtime_journal.md](../../docs/commentary/tes_runtime_journal.md#journal-stage-text)

Reads `Data\SKSE\Plugins\TESRuntime\*.crime.json`. Co-save: owner `'TES4'`,
record `'JRNL'` (the recorded objectives). Log: `TESRuntime.log`.

## Building

`build.bat` → `..\dist\TESRuntime.dll` and `journal_log_test.exe`, the headless
test of the journal record store and the journal's row rules (run it; it prints
`OK`).
