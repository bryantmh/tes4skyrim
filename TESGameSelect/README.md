# Threads of Prophecy — Game Select

A small, redistributable Skyrim SE plugin. When you start a **new game**, it
detects which converted TES games are installed and asks which one you want to
play. Picking one hands control to that game's own character generation; picking
Skyrim runs the vanilla Helgen opening exactly as shipped.

Supported starts:

| Choice | Starts | Requires |
|---|---|---|
| Skyrim | the vanilla opening, unmodified | — |
| Cyrodiil | Oblivion's `Charactergen` stage 5 — the Imperial Prison cell | `Oblivion.esm` |
| Nehrim | Nehrim's `Charactergen` stage 5 + `MQ00` | `Nehrim.esm` |
| Vvardenfell | Morroblivion's `fbmwChargen` stage 1 — the prison ship | `Morrowind_ob.esm` |
| Mojave | FalloutNV's `VCG00` stage 0 — the shallow grave | `FalloutNV.esm` |
| Morrowind | vanilla Morrowind's own opening — the Imperial Prison Ship | `Morrowind.esm` |

**Morrowind is the odd one out.** Every other game is started by setting a
stage on its character-generation quest. Vanilla Morrowind has no such quest:
its opening is object scripts on placed references, and the thing that sets
them running is a TES3 global, `CharGenState`. The `Main` start script polls
`CharGenState == 1` and launches `CharGen`, which moves the player into the
Imperial Prison Ship itself — so there is no marker to move to and no stage to
set. The TES3 engine set that global when NEW GAME was picked; Skyrim's engine
never does, which is why `MorrowindRuntime.dll` does it instead. It reads this
plugin's `TESGS_Chosen` global on a new game and writes `CharGenState` only
when the player chose Morrowind. That needs `MorrowindRuntime.dll` (shipped in
`TESRuntime.zip`, built by `tools/release/package_runtime_dll.py`); with the
ESP alone, the button appears but the opening does not begin.

A game whose plugin is not in your load order simply never appears in the menu.

## Installing

Nothing is prebuilt in this repo — the plugin is built on demand, because its
`MQ101` override is spliced out of *your* installed `Skyrim.esm`. Press **Pack
Start Mod** in the GUI, or run:

```bash
python tools/release/package_start_mod.py
```

Either produces `output/Finished Mods/TESGameSelect.zip`, whose root IS the
`Data` folder, so it installs like any other converted mod:

```
TESGameSelect.esp
scripts\TESGameSelectQuest.pex
scripts\TESGameSelectMQ101.pex
scripts\source\*.psc                    (compiler input, harmless to keep)
seq\TESGameSelect.seq                   (empty, see below)
```

Enable `TESGameSelect.esp`. It declares only `Skyrim.esm` as a master and finds
everything else at runtime, so any subset of the games works in any order.

**Load order:** this plugin overrides Skyrim's opening quest `MQ101`, so it is
incompatible with other alternate-start mods (Live Another Life, Skyrim Unbound,
Alternate Perspective) — they all edit the same record, and only the last one
loaded wins. Use one at a time.

The shipped `.seq` is intentionally **empty**: this plugin has no
Start-Game-Enabled quests. It is included so that upgrading over an older build
overwrites that build's non-empty `.seq`.

No SKSE required.

## How it works

Vanilla MQ101 stage 0 has five log entries, each conditioned on
`GetGlobalValue(MQQuickstart) == 0..4`. Entry 0 (`== 0`) is the real new-game
path, and its fragment — `QF_MQ101_0003372B.Fragment_2` — is the entire launch
of the opening: `GameHour.SetValue(7); SetStage(10)`. Stage 10 then does
everything else (equips the prisoner outfit, moves the player into the cart,
plays the title sequence and the cart audio, starts the scene).

This plugin **retargets that one fragment** at `TESGameSelectMQ101.RunTakeover`.
Nothing else about the record changes — every other fragment, all 54 aliases
and every log entry survive byte-identical, and the `MQQuickstart` condition
still routes debug quickstarts around the takeover.

> Earlier builds *appended* an unconditional sixth entry instead. That entry
> ran **alongside** Fragment_2, so stage 10 still fired: the title credits
> played, the cart audio rolled on, and the opening scene fought the handoff
> over the player — with the menu shown twice around the load screen for good
> measure. Retargeting means nothing of the opening runs until you have chosen.

The takeover, in order:

1. Controls off, saving off, and the player is parked in Skyrim's own empty
   holding cell (`WIDeadBodyCleanupCell`). The menu is deferred until the
   player's 3D is loaded — a `Message.Show()` issued during the initial load
   is drawn over the main menu, bashed by the load screen, and drawn again
   after it (the "popup appears twice" bug).
2. Installed games are detected with `Game.GetFormFromFile()`, which returns
   `None` when a plugin is not loaded. That is why the plugin needs no masters.
3. The menu is a single MESG with every button. Each converted game's
   button carries a `GetGlobalValue(...) == 1` condition, set from the
   detection pass, so absent games are not drawn. Skyrim's button is
   unconditional, so the menu can never appear with nothing to click. A hidden
   button does **not** renumber the others — `Message.Show()` returns the
   button's own index (vanilla's `dunMiddenHandSculptureSCRIPT` relies on the
   same behaviour), so the returned index is directly the game id.
4. Controls and the chargen state are restored to engine defaults, then:
   **Skyrim:** Fragment_2's two lines are replayed verbatim and stage 10 runs
   the opening as if this plugin had never existed (stage 10 moves the player
   into position itself).
   **Converted game:** the player's inventory is cleared (vanilla's own
   `RemoveAllItems` idiom from stage 10) and replaced with that game's real
   starting equipment, the player is moved to the game's start marker, its
   chargen quest is set to the stage its original author wrote as "the game
   begins here", the race menu is shown (the TES4 engine popped it
   automatically on a new game; Skyrim only shows it when a script asks — the
   name prompt follows it on a fresh character), and **MQ101 is stopped**.
   Stage 10 never ran, so there is nothing to undo — no cart, no Helgen, and
   the Skyrim main quest can never advance (MQ102 is only started by MQ101's
   own later stages).

### Starting equipment

The TES4 player base record's own inventory, worn:

| Game | Equipment |
|---|---|
| Oblivion | Sack Cloth Shirt / Pants / Sandals + Wrist Irons (`Oblivion.esm` NPC 00000007) |
| Nehrim | Flickweste, Geschnürte Lederhose, Jägermokassins, plus torch, Tagebuch and the anonymous MQ00 note (`Nehrim.esm` NPC 00000007) |
| Vvardenfell | Oblivion's set — Morroblivion does not override the player record, so a TES4 Morroblivion prisoner inherited its master file's |
| Mojave | nothing — `VCG00` stage 0 strips the Pip-Boy the player record carries |
| Morrowind | nothing — Morrowind's player record carries no inventory; the gear comes from the census office stuff room |

### Why not just stop MQ101 afterwards

The obvious design — a Start-Game-Enabled quest that waits, then calls
`MQ101.Stop()` — does not work, and was the first version of this plugin. By the
time such a quest runs, Skyrim's opening has already executed: the player is
bound in `PrisonerCuffsPlayer` (stopping the quest does not remove it), and
MQ101's packages and scene keep repositioning the player, so `MoveTo` strands
them. `OnInit` on such a quest also fires more than once, which showed the menu
twice. Retargeting the stage-0 fragment avoids all of it by never letting the
opening start.

## Configuring

Every plugin name, FormID and stage is a script property, editable in xEdit or
the Creation Kit without recompiling — useful if you ship renamed or translated
plugins.

| Property | Default |
|---|---|
| `OblivionPlugin` / `NehrimPlugin` / `MorroblivionPlugin` | `Oblivion.esm` / `Nehrim.esm` / `Morrowind_ob.esm` |
| `FalloutNVPlugin` / `MorrowindPlugin` | `FalloutNV.esm` / `Morrowind.esm` |
| `FalloutNVChargenID` / `FalloutNVStartMarkerID` / `FalloutNVChargenStage` | `00102037` / `00103E6B` / `0` |
| `MorrowindProbeID` (detection only — Morrowind starts itself) | `009D7E5D` |
| `OblivionChargenID` / `OblivionStartMarkerID` / `OblivionChargenStage` | `0002466E` / `00032AB5` / `5` |
| `NehrimChargenID` / `NehrimStartMarkerID` / `NehrimMainQuestID` / `NehrimChargenStage` | `0002466E` / `00000D33` / `00000811` / `5` |
| `MorroChargenID` / `MorroStartMarkerID` / `MorroChargenStage` | `00F0A28C` / `00F0A278` / `1` |
| `OblivionWristIronsID` / `OblivionShirtID` / `OblivionPantsID` / `OblivionShoesID` | `000BE335` / `00027319` / `00027318` / `0002731A` |
| `NehrimShirtID` / `NehrimPantsID` / `NehrimShoesID` | `0002ECAD` / `000229AB` / `0001C82B` |
| `NehrimTorchID` / `NehrimDiaryID` / `NehrimNoteID` | `00000D49` / `00000B96` / `00000AED` |

FormIDs are the low 24 bits — the id *within that plugin's own file*, which is
what `GetFormFromFile` takes, so the load-order byte is irrelevant.

## Rebuilding

```bash
python tools/release/make_game_select_esp.py        # -> output/TESGameSelect/
python -m pytest tests/test_game_select_esp.py -v
```

The build reads `MQ101` out of your installed `Skyrim.esm` (pass `--skyrim-esm`
to point at another copy), writes the `.esp` and the empty `.seq`, then compiles
both scripts. `package_start_mod.py` calls exactly this before zipping, so the
archive can never lag the sources.

The script sources of record are
`TESGameSelect/scripts/source/TESGameSelectQuest.psc` and
`TESGameSelectMQ101.psc`. The build stages a copy of them into
`<outdir>/scripts/source/` as compiler input.
