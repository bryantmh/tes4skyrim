# FalloutRuntime

What a Fallout 3 / New Vegas conversion needs at runtime.

- **Gun routing** (`guns.cpp`): a WEAP listed in a `<plugin>.guns.json` sidecar
  gets hand type 13 and the `iGun*` graph variables the patched humanoid graphs
  branch on. [asset_convert_falloutnv.md](../../docs/commentary/asset_convert_falloutnv.md#gun-graph)
- **The shot, reload key and ammo** (`fire.cpp`), **iron sights** (`zoom.cpp`)
  and **gun parts** (`parts.cpp`) ride on the routing.
  [tes_runtime_guns.md](../../docs/commentary/tes_runtime_guns.md)
- **HUD ammo counter** (`hud.cpp`): compiled; `InstallHud` is not called
  anywhere yet.
- **Limb severing** (`sever.cpp`): 🛑 **dormant**. It has never worked in game,
  so it is compiled but nothing installs its hooks or its co-save. The importer
  still writes `<plugin>.bodyparts.json`.
  [asset_convert_falloutnv.md](../../docs/commentary/asset_convert_falloutnv.md#dismemberment)

Reads `Data\SKSE\Plugins\FalloutRuntime\*.guns.json` and the optional
`FalloutRuntime.ini` there (`[Guns] ReloadKey=`, `ZoomKey=`, virtual-key codes).
Log: `FalloutRuntime.log`.

## Building

`build.bat` → `..\dist\FalloutRuntime.dll`.
