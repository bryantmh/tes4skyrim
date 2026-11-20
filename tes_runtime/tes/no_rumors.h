// TES4 SetNoRumors on a converted actor.
//
// Oblivion lists no Rumors topic for an actor whose base is flagged No Rumors, or whose reference a script told
// so with SetNoRumors. The importer keeps the flag and gates every Rumors line on GetNoRumors == 0; Papyrus has no
// SetNoRumors, so a converted script sends the mod event "TES4SetNoRumors" (numArg the value) from the actor and
// this sets the reference's override exactly as the engine's own SetNoRumors command does.
// See: docs/commentary/tes5_import_dialogue.md#no-rumors

#pragma once

#include "skse_abi.h"

namespace tesruntime {

// Resolves the engine calls and listens for the mod event; false (logged) when one is missing.
bool InstallNoRumors(SKSEMessagingInterface* messaging);

}  // namespace tesruntime
