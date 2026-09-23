// Journal stage text: a clicked quest objective shows the journal text the
// quest had when that objective first appeared.
//
// The runtime watches the player's objective array and records each new
// objective against its quest's current (stage, log entry) pair. Each tick it
// also gives every open patched journal movie `_root.MWRT_Runtime`, whose
// `GetObjectiveLog(formID, instance, row)` returns the text -- built by the
// engine's own description function, alias names filled in. The movies are
// patched by `asset_convert/ui/journal_patch.py`.
// See: docs/commentary/morrowind_runtime.md#journal-stage-text

#pragma once

#include "journal_log.h"
#include "skse_abi.h"

namespace mwruntime {

// The records, which the cosave writes and reads.
JournalLog& Journal();

// Resolves the engine functions and sets `Hooks().pollJournal`. Needs the
// Address Library loaded.
void InstallJournal();

// Forgets which objectives have been seen, so the first poll after a load
// takes the loaded save's objectives as already known instead of new.
void ResetJournalPoll();

}  // namespace mwruntime
