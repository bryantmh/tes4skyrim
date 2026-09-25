// The HUD ammo counter for guns: rounds in the magazine out of its size,
// plus the spare count Skyrim shows on its own. The engine updates the
// counter through HUDMenu's `ShowArrowCount(count, show, text)` Scaleform
// call; that call is hooked, left as the engine made it, and while the
// player holds a gun the counter's text field is then overwritten with
// "rounds/size + spare" through the movie's SetVariable; the engine
// re-sends the counter on every shot and equip, a reload shows at the
// next of those.
// (docs/commentary/tes_runtime_guns.md#hud)

#pragma once

namespace tesruntime {

// Installs the Invoke hook. False when an address is unresolved.
bool InstallHud();

// The player's magazine as the DLL tracks it (`gun` false: no gun in hand,
// the counter is left to the engine).
void HudSetMagazine(bool gun, int rounds, int clipSize);

}  // namespace tesruntime
