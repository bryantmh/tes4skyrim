# tes_runtime — the converter's SKSE plugins

Five SKSE plugins, each its own DLL built from its own folder, so a fault in one
cannot take another down and each can be released on its own:

| Folder | DLL | Needed by | Does |
|---|---|---|---|
| [tes/](tes/README.md) | `TESRuntime.dll` | every converted game | nearest-jail crime factions; journal stage text |
| [creature/](creature/README.md) | `CreatureRuntime.dll` | any plugin with converted creatures | composes the animation cache from per-plugin fragments |
| [fallout/](fallout/README.md) | `FalloutRuntime.dll` | FO3/FNV conversions | guns: hand type, the shot, reload, ammo, iron sights, gun parts |
| [havok_world_size/](havok_world_size/README.md) | `HavokWorldSize.dll` | worldspaces wider than ±64 cells | widens Havok's broad-phase world |
| [morrowind/](morrowind/README.md) | `MorrowindRuntime.dll` | Morrowind conversions | Morrowind dialogue, MWScript, persuasion, travel, alchemy |

[common/](common/README.md) holds the MIT source every plugin compiles in (SKSE
ABI, Address Library resolution, call patching, logging, paths, JSON, shared
engine services). It is not a DLL.

## Data folders

Each plugin reads its sidecars from `Data\SKSE\Plugins\<its name>\`, written
there by the converter for each converted plugin:

| Folder | Holds |
|---|---|
| `SKSE\Plugins\TESRuntime\` | `<plugin>.crime.json` |
| `SKSE\Plugins\CreatureRuntime\animation\` | `<plugin>.json` animation cache fragments |
| `SKSE\Plugins\FalloutRuntime\` | `<plugin>.guns.json`, `<plugin>.bodyparts.json`, optional `FalloutRuntime.ini` |
| `SKSE\Plugins\MorrowindRuntime\` | each Morrowind plugin's dialogue and script tables |

Logs go to `Documents\My Games\Skyrim Special Edition\SKSE\<name>.log`.

## Building

`build.bat` builds every project into [dist/](dist/) and lists any that failed;
each project's own `build.bat` builds it alone. MSVC x64 only (Build Tools 18),
no SKSE source tree, no CMake: everything from the game is resolved at runtime
through the Address Library. `dist/` holds only the finished DLLs and is
committed, like the DLLs were before it.

Verify a DLL's SKSE version struct with
`python tools/misc/skse_version_data.py tes_runtime/dist/<name>.dll`, and every
Address Library id against every installed build with
`python tools/validate/stable_id_check.py`.

## Packaging

`python tools/release/package_runtime_dll.py` (GUI: Build > Package SKSE Mod)
zips every DLL in `dist/`, `havok_world_size/HavokWorldSize.ini` and
`morrowind/interface/morrowind_dialogue.swf` into one
`output/Finished Mods/TESRuntime.zip`, rooted at the Data folder.
