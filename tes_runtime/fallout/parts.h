// Gun parts: a gun clip raises its own stem as an event, and the weapon
// mesh holds a NiControllerSequence of that name (the magazine, slide,
// bolt tracks lifted out of the FNV clip). This starts that sequence the
// way ObjectReference.PlayGamebryoAnimation does: the NiControllerManager
// on the weapon root, its name map, NiControllerSequence::Activate.
// (docs/commentary/asset_convert_falloutnv.md#gun-parts)

#pragma once

#include <cstdint>

namespace tesruntime {

// Resolves the engine entry points. False when one is missing.
bool InstallParts();

// Starts the sequence named `tag` (a BSFixedString's pointer) on every
// weapon attached to the actor's third- and first-person 3D. Returns the
// number started.
int PlayPartSequence(void* actor, void* tag);

}  // namespace tesruntime
