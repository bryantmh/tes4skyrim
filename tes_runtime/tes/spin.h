// Turns and moves the objects a converted TES4 GameMode block steps every
// frame, at a steady 30 Hz, the way MorrowindRuntime runs Rotate and Move.
//
// TES4 `SetAngle Z (GetAngle Z - 2)` in GameMode turns two degrees per FRAME.
// The converted Papyrus poll runs every 0.1 s at best and late under load, so
// a glide paced by it stops and starts. Instead each pass sends a mod event
// (axis 0-2 position, 3-5 angle): "TES4Spin" with a rate per second for a step
// from the object's own pose, "TES4Track" with the target of an absolute
// SetPos/SetAngle, reached over 1.5 pass gaps so the next pass takes over
// before it stops. This tick re-aims the object one tick ahead every tick. An
// axis no pass renews stops.
// See: docs/commentary/script_convert.md#gamemode-steps-are-rates

#pragma once

#include "skse_abi.h"

namespace tesruntime {

// Registers the mod-event sink and starts the tick. False, logged, when the
// messaging interface, task interface or a native is missing.
bool InstallSpin(SKSEMessagingInterface* messaging);

}  // namespace tesruntime
