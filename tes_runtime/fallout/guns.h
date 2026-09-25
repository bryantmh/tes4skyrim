// Gun routing: a WEAP listed in a *.guns.json sidecar gets hand type 13 and
// the iGun* graph variables the patched humanoid graphs branch on.
//
// The engine computes a hand type from the equipped form (one function,
// every caller patched) and writes it into the graph through one setter
// (also every caller patched); the setter hook writes the gun's class,
// reload letter, attack, clip size and automatic flag right after it.
// Two callers keep the vanilla answer: the GetEquippedItemType condition
// function, which the attack IDLE tree uses to pick crossbowAttackStart,
// and the Papyrus native of the same name
// (docs/commentary/asset_convert_falloutnv.md#attack-event-idle-tree).

#pragma once

#include <cstdint>

namespace tesruntime {

// Loads the sidecars and installs both hooks. False when there is nothing
// to route or an address is unresolved.
bool InstallGuns();

// After DataLoaded: resolves every sidecar entry to its runtime form.
void ResolveGunForms();

// Writes an int variable on every behavior graph of the actor (the
// player's third and first person) through the engine's own
// SetVariableInt; the number of graphs written.
int SetActorGraphInt(void* actor, const char* name, int value);
int SetActorGraphFloat(void* actor, const char* name, float value);

}  // namespace tesruntime
