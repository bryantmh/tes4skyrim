#include "object_tick.h"

#include <atomic>
#include <chrono>
#include <string>
#include <thread>
#include <vector>

#include "dialogue_state.h"
#include "log.h"
#include "main_thread.h"
#include "object_script.h"

namespace mwruntime {

namespace {

//: Ticks per second. See the header for why this is fixed, and why it is 30.
constexpr float kTickRate = 30.0f;

//: The seconds one tick covers, which GetSecondsPassed answers with.
constexpr float kTickDelta = 1.0f / kTickRate;

// An elapsed span this long is a load screen or a stall; catching up on it
// would run hundreds of ticks at once, so it is clamped instead.
constexpr float kMaxCatchUp = 0.25f;

float g_accumulated = 0.0f;
std::size_t g_lastCount = 0;
std::size_t g_ticks = 0;
// Read by the tick thread and written by the game thread, so it is atomic.
std::atomic<bool> g_running{false};

// Set while a posted tick has not run yet, so a stalled task pump holds ONE
// tick rather than a backlog that bursts when it resumes.
std::atomic<bool> g_queued{false};

// The player's cell as of the last tick, for CellChanged.
std::string g_lastCell;

// 🛑 `CellChanged` is raised on EVERY instance the tick that the player's cell
// name differs from the last, which is what TES3 means by it: the script asks
// "has the player moved cell since I last ran", not "did I move".
bool PlayerCellChanged() {
    if (!Hooks().playerCell) return false;
    const std::string now = Hooks().playerCell();
    if (now == g_lastCell) return false;
    const bool moved = !g_lastCell.empty();
    g_lastCell = now;
    return moved;
}

// 🛑 Nothing ticks before a game is LOADED. The main menu still runs the task
// pump, so an ungated tick ran object scripts over the menu and popped their
// MessageBoxes there -- measured 2026-09-18, three "You pry open the lock"
// boxes on the title screen. An unloaded game has no player cell.
bool SessionLive() {
    return Hooks().playerCell && !Hooks().playerCell().empty();
}

void RunOneTick() {
    ++g_ticks;
    if (!SessionLive()) {
        g_lastCount = 0;
        return;
    }
    if (Hooks().syncClock) Hooks().syncClock();
    const std::vector<ObjectScript*> live = BoundInstances();
    const bool cellChanged = PlayerCellChanged();
    std::size_t ran = 0;
    for (ObjectScript* instance : live) {
        // 🛑 TES3 runs a local script only while its object is LOADED. Without
        // this an instance ticks forever once bound, for a thing that left the
        // world -- and the bound set only ever grows.
        // See: docs/plans/morrowind_object_scripts.md#unload-with-the-cell
        //
        // 🛑 UNLOADING is a transition, not a state: a reference binds the
        // frame `PlaceAtMe` returns it, several frames BEFORE its 3D exists,
        // and a spawn has no rebind path -- so treating "not loaded yet" as
        // "unloaded" drops the creature's script forever.
        // See: docs/commentary/morrowind_runtime.md#a-spawn-is-not-loaded-on-its-first-frame
        if (Hooks().is3DLoaded) {
            if (Hooks().is3DLoaded(instance->RuntimeFormId())) {
                instance->MarkLoaded();
            } else if (instance->WasLoaded()) {
                UnbindInstance(instance->RuntimeFormId());
                continue;
            } else {
                continue;
            }
        }
        // Before the body: both must already be raised when it reads them.
        instance->PollDeath();
        if (cellChanged) instance->Events().cellChanged = true;
        instance->RunOnce();
        ++ran;
    }
    g_lastCount = ran + RunGlobalScripts();
}

// 🛑 The wait SLEEPS OFF the game thread and only the tick itself is posted.
// A task that re-posts itself drains in the SAME pump sweep, so no frame ever
// passes and the game hangs at the main menu -- measured 2026-09-18, and the
// identical hazard `game_calls.cpp`'s objective wait already documents.
// See: docs/plans/morrowind_object_scripts.md#tick-rate
void TickThread() {
    while (g_running) {
        std::this_thread::sleep_for(
            std::chrono::duration<float>(kTickDelta));
        if (!g_running) return;
        if (g_queued.exchange(true)) continue;
        PostToMainThread([]() {
            g_queued = false;
            RunOneTick();
        });
    }
}

}  // namespace

float TickDelta() { return kTickDelta; }

std::size_t LastTickCount() { return g_lastCount; }

std::size_t TicksRun() { return g_ticks; }

void TickObjectScripts(float frameSeconds) {
    if (frameSeconds <= 0.0f) return;
    g_accumulated += frameSeconds > kMaxCatchUp ? kMaxCatchUp : frameSeconds;
    while (g_accumulated >= kTickDelta) {
        g_accumulated -= kTickDelta;
        RunOneTick();
    }
}

void StartObjectTick() {
    if (g_running) return;
    if (!CanPostToMainThread()) {
        Log("object: no task interface -- object scripts will NOT tick");
        return;
    }
    g_running = true;
    std::thread(TickThread).detach();
    Log("object: tick started at %g Hz", kTickRate);
}

void StopObjectTick() { g_running = false; }

}  // namespace mwruntime
