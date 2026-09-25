// Gun fire: the shot is the DLL's, not the crossbow attack state machine's.
//
// A gun clip carries one `arrowRelease` trigger per round. Every trigger an
// actor's graph raises passes through Actor::ProcessEvent, whose vtable slot
// is replaced here; on arrowRelease from a gun holder the gun is fired through the
// engine's own TESObjectWEAP::Fire, the routine the vanilla arrow release
// ends in, minus the attack-state gate that capped the rate at one engine
// attack cycle per shot. The gun's ammo list gates it: the wrong round is
// swapped for one the actor carries; with none the click is answered by
// the gun's own no-ammo sound and no attack. The attack button itself is
// taken from the engine's action dispatcher and becomes the graph's own
// fire events. The reload and zoom keys (FalloutRuntime.ini, [Guns]
// ReloadKey / ZoomKey, virtual-key codes, default mouse button 4 and
// right mouse) send a graph event / drive the iron sights (zoom.h).
// (docs/commentary/tes_runtime_guns.md)

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace tesruntime {

// Installs the event-sink hook and the key poller. False when an address
// is unresolved.
bool InstallFire();

// After DataLoaded: the ammo forms a resolved gun may fire (empty = any),
// the EditorID of its no-ammo sound descriptor ('' = silent), its
// iron-sight FOV in degrees and its magazine size.
void RegisterGunAmmo(void* weapon, std::vector<void*> ammo, const std::string& drySound,
                     float sightFov, int clipSize);

// The Sight FOV of the gun the player holds; 0 without a gun.
float PlayerGunSightFov();

}  // namespace tesruntime
