# TESRuntime — the engine add-ons that make converted games play right

Skyrim can load a converted Oblivion, Morrowind or Fallout world, but some of
what made those games work has no Skyrim equivalent: Morrowind's dialogue
window, jails that pick the nearest prison, creatures with their own
animation sets, Fallout's guns. These small SKSE plugins fill those gaps. They
run quietly in the background, touch nothing in your save that removing them
would break, and only do anything when the plugin they serve is installed.

All five come in one download, `TESRuntime.zip`. Install it like any other
mod (it needs [SKSE](https://skse.silverlock.org/) and the
[Address Library](https://www.nexusmods.com/skyrimspecialedition/mods/32444)),
and keep it enabled for as long as you play any converted game.

| Plugin | In one line | Keep it if you play… |
|---|---|---|
| [TESRuntime](tes/README.md) | Jails, quest journals and alchemy apparatus that behave like the original games | any converted game |
| [CreatureRuntime](creature/README.md) | Lets converted creatures move and fight with their own animations | anything with converted creatures (Oblivion, Nehrim, Morrowind, Fallout) |
| [FalloutRuntime](fallout/README.md) | Makes Fallout 3 / New Vegas guns shoot, reload and aim like guns | Fallout 3 or New Vegas |
| [HavokWorldSize](havok_world_size/README.md) | Keeps physics working in huge worldspaces | big worlds such as Tamriel Rebuilt — or any large mod world |
| [MorrowindRuntime](morrowind/README.md) | Brings back Morrowind's own dialogue, scripts and services | Morrowind or Tamriel Rebuilt |

Each plugin is independent: if one ever misbehaves it cannot take the others
down with it, and you can disable any one of them without touching the rest.
Each writes a log to `Documents\My Games\Skyrim Special Edition\SKSE\<name>.log`,
which is the first thing to attach to a bug report.

---

## For developers

Each plugin is its own DLL built from its own folder; [common/](common/README.md)
holds the MIT source they all compile in (SKSE ABI, Address Library
resolution, call patching, logging, paths, JSON, shared engine services, menu
messages, the crafting-bench API). It is not a DLL. MorrowindRuntime and
TESRuntime are GPL-3.0 binaries (OpenMW code and an OpenMW-ported formula);
the other three are MIT.

### Data folders

Each plugin reads its sidecars from `Data\SKSE\Plugins\<its name>\`, written
there by the converter for each converted plugin:

| Folder | Holds |
|---|---|
| `SKSE\Plugins\TESRuntime\` | `<plugin>.crime.json`, `<plugin>.apparatus.json` |
| `SKSE\Plugins\CreatureRuntime\animation\` | `<plugin>.json` animation cache fragments |
| `SKSE\Plugins\FalloutRuntime\` | `<plugin>.guns.json`, `<plugin>.bodyparts.json`, optional `FalloutRuntime.ini` |
| `SKSE\Plugins\MorrowindRuntime\` | each Morrowind plugin's dialogue and script tables |

### Building

`build.bat` builds every project into [dist/](dist/) and lists any that failed;
each project's own `build.bat` builds it alone. MSVC x64 only (Build Tools 18),
no SKSE source tree, no CMake: everything from the game is resolved at runtime
through the Address Library. `dist/` holds only the finished DLLs and is
committed.

Verify a DLL's SKSE version struct with
`python tools/misc/skse_version_data.py tes_runtime/dist/<name>.dll`, and every
Address Library id against every installed build with
`python tools/validate/stable_id_check.py`.

### Packaging

`python tools/release/package_runtime_dll.py` (GUI: Build > Package SKSE Mod)
zips every DLL in `dist/`, `havok_world_size/HavokWorldSize.ini` and
`morrowind/interface/morrowind_dialogue.swf` into one
`output/Finished Mods/TESRuntime.zip`, rooted at the Data folder.
