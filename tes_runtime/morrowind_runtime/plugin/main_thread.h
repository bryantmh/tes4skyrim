// Task posting for game calls made from the dialogue menu.
//
// Posting never waits. The menu is modal and pauses the game, so blocking the
// calling thread on the task pump would deadlock rather than marshal.
//
// A task runs a whole sequence in ONE trip: staging a quest and displaying its
// objective both land before the game advances, which is what the engine's own
// console does.
// See: docs/commentary/morrowind_runtime.md#objectives-must-be-displayed

#pragma once

#include <functional>

#include "skse_abi.h"

namespace mwruntime {

// Takes the task interface SKSE hands out at plugin load.
void SetTaskInterface(SKSETaskInterface* task);

// True once the task interface is available, so a caller can log the reason a
// call was dropped rather than failing silently.
bool CanPostToMainThread();

// Queues `fn` to run on the game's main thread and returns immediately.
// Returns false when there is no task interface and `fn` was NOT run.
bool PostToMainThread(std::function<void()> fn);

}  // namespace mwruntime
