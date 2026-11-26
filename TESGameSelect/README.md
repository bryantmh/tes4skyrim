# Threads of Prophecy — Game Select

A small, redistributable Skyrim SE plugin. When you start a **new game**, it
detects which converted games are installed and asks which one you want to
play. Picking one hands control to that game's own opening; picking Skyrim runs
the vanilla Helgen opening.

Every character also carries an **Elder Scroll**. Reading it lets you travel
between the installed games: *Begin* a game you have not started yet, or
*Return to* one you have, arriving exactly where you left it.

Supported games, in menu order:

| Choice | Starts | Requires |
|---|---|---|
| Skyrim | the vanilla opening | — |
| Cyrodiil | Oblivion's `Charactergen` stage 5 — the Imperial Prison cell | `Oblivion.esm` |
| Vvardenfell | vanilla Morrowind's own opening — the Imperial Prison Ship | `Morrowind.esm` |
| Vvardenfell | Morroblivion's `fbmwChargen` stage 1 — the prison ship | `Morrowind_ob.esm` |
| Nehrim | Nehrim's `Charactergen` stage 5 + `MQ00` stage 1 | `Nehrim.esm` |
| Arktwend | Arktwend's own opening — Melee monastery | `Arktwend_English.esm` |
| Mojave | FalloutNV's `VCG00` stage 0 — the shallow grave | `FalloutNV.esm` |

Fallout 3 (vanilla or Tale of Two Wastelands) has its place reserved after
Mojave, for when its conversion exists.

**The TES3 games are the odd ones out.** Every other game is started by
setting a stage on its character-generation quest. Vanilla Morrowind and
Arktwend have no such quest: their openings are object scripts on placed
references, set running by a TES3 global, `CharGenState`. The `Main` start
script polls `CharGenState == 1` and launches `CharGen`, which moves the player
itself. The TES3 engine set that global when NEW GAME was picked; this plugin
sets it instead, and `MorrowindRuntime.dll` (shipped in `TESRuntime.zip`)
carries the value to the scripts. With the ESP alone the button appears but the
opening does not begin.

A game whose plugin is not in your load order simply never appears in a menu.

## Installing

Nothing is prebuilt in this repo — the plugin is built on demand, because its
`MQ101` override and its scroll are copied out of *your* installed
`Skyrim.esm`. Press **Pack Start Mod** in the GUI, or run:

```bash
python tools/release/package_start_mod.py
```

Either produces the mod folder `output/TESGameSelect/`, whose root IS the
`Data` folder, so it installs like any other converted mod:

```
TESGameSelect.esp
seq\TESGameSelect.seq                     (the travel quest)
scripts\TESGameSelectQuest.pex            (the menu and each game's start)
scripts\TESGameSelectMQ101.pex            (the takeover of Skyrim's opening)
scripts\TESGameSelectTravel.pex           (the travel between games)
scripts\TESGameSelectTravelPlayer.pex
scripts\TESGameSelectScroll.pex
scripts\source\*.psc                      (compiler input, harmless to keep)
```

Enable `TESGameSelect.esp`. It declares only `Skyrim.esm` as a master and finds
everything else at runtime, so any subset of the games works in any order. It
can be added to an existing save: the scroll appears on the next load, and the
game that save began with counts as started.

**Load order:** this plugin overrides Skyrim's opening quest `MQ101`, so it is
incompatible with other alternate-start mods (Live Another Life, Skyrim Unbound,
Alternate Perspective) — they all edit the same record, and only the last one
loaded wins. Use one at a time.

No SKSE required.

## How it works

The design notes, with the reasons behind each choice, are in
[docs/commentary/tesgameselect.md](../docs/commentary/tesgameselect.md).

**The new-game menu.** Vanilla `MQ101` stage 0 has five log entries; entry 0
(`GetGlobalValue(MQQuickstart) == 0`) is the real new-game path, and its
fragment launches the whole opening. This plugin retargets that one fragment,
so nothing of Helgen runs until you have chosen:

1. Any converted game whose opening starts on its own is held first (Nehrim's
   `Charactergen` is Start-Game-Enabled, so without this it pulls the player
   into Nehrim's start cave whichever game is picked). Nehrim's main quest
   `MQ00` is held only once another game is chosen.
2. Controls off, saving off, and the player waits in Skyrim's own empty holding
   cell until the load has finished.
3. Installed games are detected with `Game.GetFormFromFile()`, which returns
   `None` when a plugin is not loaded. Each game's button carries a
   `GetGlobalValue(...) == 1` condition set from that pass; Skyrim's button is
   unconditional, so the menu is never empty. A hidden button does not
   renumber the others, so the button pressed is directly the game id.
4. **Skyrim:** the vanilla fragment's two lines are replayed and stage 10 runs
   the opening. **Another game:** its starting equipment is added and worn, the
   player moves to its start marker, its opening is started, the race menu is
   shown for the TES4 games (the TES4 engine popped it on a new game). `MQ101`
   stays waiting at stage 0, so nothing of Helgen runs until the scroll begins
   Skyrim. It is never stopped: `MQ101` is a Run Once quest, and a stopped one
   can never be started again.

**Your inventory is never cleared by travel.** On a new game the takeover
removes Skyrim's player base-record items (the iron armor, potions and gold a
main-menu `coc` hands out), exactly what vanilla stage 10 strips, once the
load and the menu are done, so what the player already wears goes too. After that,
the plugin retargets stage 10 too: it sets your items aside in a chest while
the vanilla fragment runs, then gives them back (worn items come back
unequipped), so beginning Skyrim from another game keeps everything you carry.

**The Elder Scroll** uses vanilla's Elder Scroll model. It is a quest item: it
cannot be dropped or sold. Reading it plays vanilla's own Elder Scroll reading
(first person, the scroll in hand, the blinding light), and the travel menu
appears where vanilla's reading ends. It lists every installed game except the
one you are in:

- **Begin** a game you have not started: that game's own opening runs, as it
  does on a new game, and its starting equipment is added. The race menu is
  not shown by the scroll; a game whose opening shows its own (Morrowind's
  census office, Doc Mitchell's reflectron) still will.
- **Return to** a game you have started: you arrive where you last left it,
  facing the same way — the same idea as Morrowind's Mark and Recall.

Travel is refused in combat and while controls are disabled, which includes
every game's opening sequence.

### Starting equipment

Each game's own player record, worn:

| Game | Equipment |
|---|---|
| Cyrodiil | Sack Cloth Shirt / Pants / Sandals + Wrist Irons (`Oblivion.esm` NPC 00000007) |
| Vvardenfell (Morrowind) | Common Shirt, Pants and Shoes (`Morrowind.esm` `Player`) |
| Vvardenfell (Morroblivion) | Oblivion's set — Morroblivion does not override the player record |
| Nehrim | Flickweste, Geschnürte Lederhose, Jägermokassins, plus torch, Tagebuch and the anonymous MQ00 note (`Nehrim.esm` NPC 00000007) |
| Arktwend | Common Robe, Shirt, Pants and Shoes (Arktwend's own `Player`) |
| Mojave | nothing — `VCG00` stage 0 strips the Pip-Boy the player record carries |

## Configuring

Every plugin name, FormID and stage is a script property on the
`TESGSGameSelect` quest, editable in xEdit or the Creation Kit without
recompiling — useful if you ship renamed or translated plugins. FormIDs are
the low 24 bits — the id *within that plugin's own file*, which is what
`GetFormFromFile` takes, so the load-order byte is irrelevant.

| Property | Default |
|---|---|
| `OblivionPlugin` / `MorrowindPlugin` / `MorroblivionPlugin` | `Oblivion.esm` / `Morrowind.esm` / `Morrowind_ob.esm` |
| `NehrimPlugin` / `ArktwendPlugin` / `FalloutNVPlugin` | `Nehrim.esm` / `Arktwend_English.esm` / `FalloutNV.esm` |
| `OblivionChargenID` / `OblivionStartMarkerID` / `OblivionChargenStage` | `0002466E` / `00032AB5` / `5` |
| `MorrowindProbeID` / `MorrowindChargenStateID` | `00192940` / `006472DD` |
| `MorroChargenID` / `MorroStartMarkerID` / `MorroChargenStage` | `00F0A28C` / `00F0A278` / `1` |
| `NehrimChargenID` / `NehrimStartMarkerID` / `NehrimChargenStage` | `0002466E` / `00000D33` / `5` |
| `NehrimMainQuestID` / `NehrimMainQuestStage` | `00000811` / `1` |
| `ArktwendChargenStateID` | `006472DD` |
| `FalloutNVChargenID` / `FalloutNVStartMarkerID` / `FalloutNVChargenStage` | `00102037` / `00103E6B` / `0` |
| `OblivionWristIronsID` / `OblivionShirtID` / `OblivionPantsID` / `OblivionShoesID` | `000BE335` / `00027319` / `00027318` / `0002731A` |
| `MorrowindShirtID` / `MorrowindPantsID` / `MorrowindShoesID` | `007E3DE2` / `00A5FBF7` / `00C73369` |
| `NehrimShirtID` / `NehrimPantsID` / `NehrimShoesID` | `0002ECAD` / `000229AB` / `0001C82B` |
| `NehrimTorchID` / `NehrimDiaryID` / `NehrimNoteID` | `00000D49` / `00000B96` / `00000AED` |
| `ArktwendShirtID` / `ArktwendPantsID` / `ArktwendShoesID` / `ArktwendRobeID` | `007E3DE2` / `00A5FBF7` / `00C73369` / `00B75B56` |

Arktwend must be converted in authored mode (`convert.py -f
Arktwend_English.esm --morrowind-source vanilla`, or Settings > Morrowind
source > Vanilla); the exporter refuses to build it in Morroblivion mode.

## Rebuilding

```bash
python tools/release/make_game_select_esp.py        # -> output/TESGameSelect/
python -m pytest tests/test_game_select_esp.py -v
```

The build reads `MQ101` and the vanilla Elder Scroll out of your installed
`Skyrim.esm` (pass `--skyrim-esm` to point at another copy), writes the `.esp`
and the `.seq`, then compiles the scripts. `package_start_mod.py` calls exactly
this before zipping, so the archive can never lag the sources.

The script sources of record are in `TESGameSelect/scripts/source/`. The build
stages a copy of them into `<outdir>/scripts/source/` as compiler input.
