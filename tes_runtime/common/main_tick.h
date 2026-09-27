// A fixed-rate call on the game's main thread, shared by every runtime.
//
// 🛑 The wait SLEEPS on its own thread and only the call is posted. A task
// that re-posts itself drains in the SAME pump sweep, so no frame ever passes
// and the game hangs at the main menu.
//
// 🛑 A call still queued is not posted again, so a stalled task pump holds ONE
// call rather than a backlog that bursts when it resumes.
//
// 🛑 The wait is WHOLE milliseconds, never a float duration: `sleep_for` adds
// it to steady_clock's nanoseconds since boot, and past ~156 hours of uptime a
// float cannot hold 33 ms at that size -- the sleep returned at once and the
// thread re-posted every tick inside one task drain (1-3 fps, measured).

#pragma once

#include <functional>

namespace tesruntime {

// Hands `fn` to the game's main-thread task queue; false when there is none.
using PostToMainFn = bool (*)(std::function<void()> fn);

// Runs `fn` on the game thread every `ms` milliseconds, paused or not, posted
// through `post`. False, and nothing started, when `post` or `fn` is missing.
bool StartTick(PostToMainFn post, int ms, void (*fn)());

}  // namespace tesruntime
