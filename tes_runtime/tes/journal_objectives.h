// Journal stage text: a clicked quest objective shows the journal text the
// quest had when that objective first appeared.
//
// The runtime watches the player's objective array and records each new
// objective against its quest's current (stage, log entry) pair. Each tick it
// also gives every open patched journal movie `_root.TESRT_Runtime`, whose
// `GetObjectiveLog(formID, instance, row)` returns the text -- built by the
// engine's own description function, alias names filled in. The movies are
// patched by `asset_convert/ui/journal_patch.py`.
// See: docs/commentary/tes_runtime_journal.md#journal-stage-text

#pragma once

#include "skse_abi.h"

namespace tesruntime {

// Resolves the engine functions. Needs the Address Library loaded. False
// (with the reason logged) when a required one is missing.
bool InstallJournal();

// After DataLoaded: starts the poll on the game's main thread.
void StartJournalTick();

// SKSE serialization callbacks for the recorded objectives. A revert also
// forgets which objectives have been seen, so the first poll after a load
// takes the loaded save's objectives as already known instead of new.
void JournalSave(SKSESerializationInterface* intfc);
void JournalLoad(SKSESerializationInterface* intfc);
void JournalRevert(SKSESerializationInterface* intfc);

}  // namespace tesruntime
