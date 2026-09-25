# FalloutRuntime

**Makes Fallout 3 and New Vegas guns work like guns.** Skyrim only knows
swords, bows and crossbows; FalloutRuntime teaches it pistols, rifles and
automatic weapons.

## What it does for you

- **Guns are held and animated as guns.** Each converted weapon uses its own
  Fallout stance and animations instead of being treated like a crossbow.
- **Real gunfire.** Each shot fires when the gun's animation fires it, so an
  automatic weapon sprays at its real rate instead of Skyrim's one-bolt pace.
- **Magazines and reloading.** Guns fire until their magazine is empty, then
  reload. Press the reload key (default **mouse button 4**) to reload early.
- **The right ammo.** A gun only fires ammunition it accepts. If you're
  holding the wrong kind, it swaps to one you carry; with none, you hear the
  empty click.
- **Iron sights.** Hold the zoom key (default **right mouse**) to raise the gun
  and aim down its sights.
- **Moving parts.** Slides, bolts and magazines move when the gun fires and
  reloads.

### Changing the keys

Create `Data\SKSE\Plugins\FalloutRuntime\FalloutRuntime.ini` with the
[virtual-key code](https://learn.microsoft.com/windows/win32/inputdev/virtual-key-codes)
of the key you want, in decimal:

```ini
[Guns]
ReloadKey=82
ZoomKey=2
```

## Should I keep it enabled?

Yes, if you play Fallout 3 or New Vegas. Without it, Fallout guns can't be
fired properly. It does nothing in other games.

**Still in progress:** the on-screen ammo counter and severed limbs are not
switched on yet.

---

## For developers

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
`FalloutRuntime.ini` there. Log: `FalloutRuntime.log`.

`build.bat` → `..\dist\FalloutRuntime.dll`.
