// The object-script tick: every loaded instance's body, at a FIXED rate.
//
// 🛑 NOT once per rendered frame, which is what Morrowind and OpenMW do. TES3
// bodies move things a fixed amount PER TICK rather than scaled by the delta
// (`rotate z, -110` is literal), so a 144 fps machine would spin a door 2.4x
// faster than its author saw. A fixed accumulator makes authored behaviour the
// same everywhere.
//
// 🛑 The RATE is not a free performance dial: it sets how fast authored motion
// plays. `rotate`/`move` are now ported and BOTH are scaled by TickDelta, so
// the rate no longer changes their speed -- but it does bound how finely they
// step, which is why it is 30 rather than 15.
// See: docs/commentary/morrowind_runtime.md#move-and-rotate-are-rates
//
// 🛑 `GetSecondsPassed` must then return the TICK delta, not the frame's --
// hand a script a 144 fps frame time while ticking at 30 Hz and every
// integrating timer runs ~5x slow, silently.
//
// 🛑 The tick runs ON THE GAME THREAD, and cannot not. Bodies read and write
// game state directly (`Enable`, `Activate`, `GetDistance`, `SetHealth`) and
// assume a coherent world within one tick: off-thread, a script's `Enable`
// would land a frame after the `GetDisabled` two lines below it.
// See: docs/plans/morrowind_object_scripts.md#tick-rate

#pragma once

#include <cstddef>

namespace mwruntime {

// Advances the accumulator by `frameSeconds` and runs whatever ticks are due.
// Call once per frame from the game thread.
void TickObjectScripts(float frameSeconds);

// Starts the tick, once at load: a detached thread that SLEEPS one delta and
// posts one tick's work to the game thread.
//
// 🛑 The sleep is off the game thread and only the work is posted. A task that
// re-posts itself drains in the SAME pump sweep, so no frame ever passes and
// the game hangs at the main menu -- measured 2026-09-18, and the same hazard
// `game_calls.cpp`'s objective wait documents.
void StartObjectTick();

// Stops it. The thread is detached, so this is how it is asked to end.
void StopObjectTick();

// The seconds ONE tick covers, which is what GetSecondsPassed answers.
float TickDelta();

// How many instances the last tick ran, and how many ticks have run.
std::size_t LastTickCount();
std::size_t TicksRun();

// Forgets the discovery sweep's position, the last cell and the accumulator,
// so one case cannot inherit the sweep's rest from the case before it.
void ResetTickState();

}  // namespace mwruntime
