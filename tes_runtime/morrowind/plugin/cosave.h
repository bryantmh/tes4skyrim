// The co-save: DialogueState, written into the SKSE co-save beside every
// Skyrim save and read back with it.
//
// It is the AUTHORITY for everything Morrowind holds that Skyrim cannot:
// journal indices, disposition, script variables, the player's factions. One
// versioned record holds the state's own text form; a record of a type or
// version this build does not know is skipped, never fatal.
// See: docs/commentary/morrowind_runtime.md#co-save

#pragma once

#include "skse_abi.h"

namespace tesruntime::mw {

// Registers the save, load and revert callbacks. Once, at plugin load.
void InstallCoSave(SKSESerializationInterface* serialization,
                   PluginHandle plugin);

// Clears every per-game state and starts the startup scripts again. SKSE's
// revert callback does NOT fire for a new game chosen from the main menu, so
// kMessage_NewGame calls this too.
void RevertState();

}  // namespace tesruntime::mw
