# TESRuntime

**The one every converted game needs.** It makes crime, the quest journal and
alchemy equipment behave the way they did in the game you converted, and it
works with any converted world — Oblivion, Nehrim, Morrowind or Fallout.

## What it does for you

### You go to the nearest jail, not the other side of the map

In Oblivion and Morrowind, when a guard arrests you, you're taken to the
closest prison. Skyrim instead ties every guard faction to one fixed jail. On
its own, getting arrested in a converted world could send you somewhere far
from where you were caught, or nowhere sensible at all.

TESRuntime keeps an eye on where you are and quietly points every converted
guard faction at the nearest open jail. Get caught in Chorrol and you serve
your time in Chorrol. The evidence chest works the same way: stolen goods end
up in that jail's chest, and you get your gear back when you're released, as
in the original games.

### Clicking a quest objective shows the text that came with it

Skyrim's journal shows only a quest's *latest* entry. Long questlines from
Oblivion and Morrowind write a new entry at almost every step, so the reason
you were sent somewhere three steps ago is lost.

With TESRuntime, clicking an objective in the journal shows the journal text
the quest had **when that objective appeared**. Click it again to go back to
the current text. It works on every quest, including Skyrim's own.

This needs the small journal patch the converter builds
(**Build > Quest Journal Stage Text**), which works with the vanilla journal,
SkyUI and Quest Journal Overhaul. Objectives you already had before installing
simply show the current text.

### Your mortar and pestle is a portable alchemy lab again

In Oblivion and Morrowind you brewed potions anywhere, with the mortar and
pestle, alembic, retort and calcinator you carried, and better equipment made
stronger potions. Skyrim only brews at an alchemy table, and its apparatus
are junk.

With TESRuntime, using a mortar and pestle from your inventory opens Skyrim's
alchemy menu wherever you are (just not mid-fight). The tools you carry then
change what you brew the way Morrowind's did: a better mortar makes every
effect stronger, a retort strengthens helpful effects, an alembic weakens
harmful ones, and a calcinator boosts both. The best of each kind you have is
used. Brewing itself stays Skyrim's: the same ingredients, perks, skill gains
and potion values.

## Should I keep it enabled?

Yes, whenever you play a converted game. Without it, arrests use Skyrim's
fixed jails, the journal only ever shows the newest entry, and apparatus are
just clutter. Morrowind's enchanting service also opens its menu through
TESRuntime. It doesn't change any records, and removing it doesn't break a
save; you just lose these behaviors.

---

## For developers

- **Jails** (`crime.cpp`): every 2 s, on the main thread, points every listed
  crime faction's jail, follower-wait marker and evidence chests at the enabled
  jail nearest the player, from the `<plugin>.crime.json` sidecars the importer
  writes; hooks ServeTime so the stolen goods are kept when a sentence is served.
  [tes_runtime_crime.md](../../docs/commentary/tes_runtime_crime.md#nearest-jail)
- **Journal stage text** (`journal_objectives.cpp`, `journal_log.cpp`): polls
  the player's objective array every 33 ms and records each new objective
  against its quest's current (stage, log entry) pair; patched journal movies
  call `_root.TESRT_Runtime.GetObjectiveLog`.
  [tes_runtime_journal.md](../../docs/commentary/tes_runtime_journal.md#journal-stage-text)
- **Alchemy apparatus** (`alchemy.cpp`, `alchemy_hooks.cpp`): selecting a
  staged apparatus in the inventory opens the alchemy bench; the brew is
  refused without a mortar, and each effect is scaled by OpenMW's
  `applyTools`, taken as a ratio. Read from the `<plugin>.apparatus.json`
  sidecars `tes5_import/record_types/apparatus.py` writes.
  [tes_runtime_alchemy.md](../../docs/commentary/tes_runtime_alchemy.md#alchemy-apparatus)
- **Crafting bench** (`crafting.cpp`, API in `../common/crafting.h`): owns the
  CraftingMenu swap and exports `TESRuntime_OpenBench` / `TESRuntime_BenchReady`,
  which MorrowindRuntime's enchanting service calls.
  [tes_runtime_alchemy.md](../../docs/commentary/tes_runtime_alchemy.md#crafting-bench)

Reads `Data\SKSE\Plugins\TESRuntime\*.crime.json` and `*.apparatus.json`.
Co-save: owner `'TES4'`, record `'JRNL'`. Log: `TESRuntime.log`.

Licensing: `alchemy.cpp` ports OpenMW's `applyTools` line for line, so the
binary is **GPL-3.0**; the rest of this folder is MIT.

`build.bat` → `..\dist\TESRuntime.dll` and two headless tests, each printing
`OK`: `journal_log_test.exe` (the journal record store and the journal's row
rules) and `alchemy_test.exe` (the apparatus ratio and the sidecar reader).
