#include "object_tick.h"

#include <atomic>
#include <chrono>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include "dialogue_state.h"
#include "log.h"
#include "main_thread.h"
#include "object_script.h"
#include "script_tables.h"

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
// Seconds of unpaused play, which is the clock every runtime timer reads.
double g_gameSeconds = 0.0;
std::size_t g_lastCount = 0;
std::size_t g_ticks = 0;
// Read by the tick thread and written by the game thread, so it is atomic.
std::atomic<bool> g_running{false};

// Set while a posted tick has not run yet, so a stalled task pump holds ONE
// tick rather than a backlog that bursts when it resumes.
std::atomic<bool> g_queued{false};

// The player's cell as of the last tick, for CellChanged, and whether one has
// been sampled at all -- an unnamed exterior is "" and is still a cell.
std::string g_lastCell;
bool g_cellSampled = false;

// How many staged placements the discovery sweep tests per tick, and the
// heartbeat between laps when nothing has happened.
//
// 🛑 The table is 15,639 rows and almost all of them sit in cells nowhere near
// the player, so a CONTINUOUS sweep is 7,680 engine calls a second to learn
// nothing. Only a cell LOADING changes an answer, so the sweep is driven by
// the player changing cell and otherwise laps on a slow heartbeat -- exteriors
// stream neighbours in without a cell change, which the heartbeat covers.
constexpr std::size_t kDiscoverPerTick = 256;
constexpr std::size_t kHeartbeatTicks = 150;

// Where the next slice resumes -- 0 means no lap is in flight -- and how many
// ticks of rest are left before the next one starts.
std::size_t g_discoverAt = 0;
std::size_t g_untilHeartbeat = 0;

// Binds staged placements whose reference is now LOADED.
//
// 🛑 Without this the ONLY binding path is the Activate hook, so a script ran
// only for an object the player had clicked: `OnDeath`, `OnPCHitMe` and every
// proximity test (GetDistance/SetFight, ForceGreeting) were silently dead for
// everything else -- measured, Ga'Nahiru neither turned aggressive nor advanced
// its quest on death because nothing ever bound its instance.
// See: docs/commentary/morrowind_runtime.md#instances-bind-from-the-world
void DiscoverLoaded(bool cellChanged) {
    if (!Hooks().loadedRef) return;
    // A cell change restarts the lap at once, since that is when a batch of
    // references appears; otherwise the heartbeat picks up whatever streamed
    // in without one.
    if (cellChanged) g_untilHeartbeat = 0;
    if (g_discoverAt == 0 && g_untilHeartbeat) {
        --g_untilHeartbeat;
        return;
    }
    for (std::size_t n = 0; n < kDiscoverPerTick; ++n) {
        const InstanceRow* row = InstanceAt(g_discoverAt++);
        if (!row) {
            g_discoverAt = 0;
            g_untilHeartbeat = kHeartbeatTicks;
            return;
        }
        if (IsPlacementBound(row->plugin, row->localFormId)) continue;
        const std::uint32_t ref =
            Hooks().loadedRef(row->plugin, row->localFormId);
        if (!ref) continue;
        BindInstance(ref, row->plugin, row->localFormId);
        Log("object: %s bound from the world (%08X)", row->script.c_str(), ref);
    }
}

// 🛑 `CellChanged` is raised on EVERY instance the tick that the player's cell
// name differs from the last, which is what TES3 means by it: the script asks
// "has the player moved cell since I last ran", not "did I move".
bool PlayerCellChanged() {
    if (!Hooks().playerCell) return false;
    const std::string now = Hooks().playerCell();
    if (g_cellSampled && now == g_lastCell) return false;
    // 🛑 "Have we sampled yet", not "is the last name empty": an unnamed
    // exterior IS a cell, so an emptiness test swallowed every transition out
    // of one and re-armed itself on the way back in.
    const bool moved = g_cellSampled;
    g_lastCell = now;
    g_cellSampled = true;
    return moved;
}

// 🛑 Nothing ticks before a game is LOADED. The main menu still runs the task
// pump, so an ungated tick ran object scripts over the menu and popped their
// MessageBoxes there -- measured 2026-09-18, three "You pry open the lock"
// boxes on the title screen.
//
// 🛑 The test is that the player is in SOME cell, never that the cell has a
// NAME. Gating on the name held for interiors and was false across every
// unnamed exterior -- which is most of the world -- so no object script ticked
// outdoors at all: no OnDeath, no proximity poll, no discovery sweep.
// See: docs/commentary/morrowind_runtime.md#the-tick-is-gated-on-a-loaded-game
bool SessionLive() {
    return Hooks().playerInWorld && Hooks().playerInWorld();
}

// 🛑 A PAUSED game must not tick. The task pump keeps draining while a menu
// holds the game -- that is why the objective wait sleeps off-thread -- so
// nothing else stops us: timers integrated, `rotate`/`move` stepped and Say
// lines aged out behind an engine that had stopped playing them, which left a
// subtitle on screen forever and the line after it never spoken.
//
// 🛑 Events are NOT lost to this. They latch (`activated`, `died`,
// `cellChanged` are sticky until a body reads them), so whatever happens
// while paused is delivered on the first tick after.
// See: docs/commentary/morrowind_runtime.md#the-tick-stops-while-the-game-is-paused
bool GameHeldByMenu() {
    return Hooks().gamePaused && Hooks().gamePaused();
}

// Runs the script of every scripted object the PLAYER carries whose body reads
// `OnPCEquip`. An inventory item has no placement, so the world-discovery
// sweep above can never reach it.
//
// 🛑 The watch list is the objects whose script DECLARES the local, which is
// the only set that can answer; asking the engine about all 1,547 scripted
// objects every tick would not be affordable.
// See: docs/commentary/morrowind_runtime.md#engine-written-locals
std::size_t RunCarriedScripts(bool cellChanged) {
    std::size_t ran = 0;
    static std::size_t announced = 0;
    if (announced != EquipWatchList().size()) {
        announced = EquipWatchList().size();
        Log("object: %zu carried object(s) watch OnPCEquip", announced);
    }
    // 🛑 Every record is polled, and the instance runs at most once per tick.
    // Several item records share one script, so stopping at the first carried
    // one would miss the sibling actually worn; running per record would run
    // the body once per record.
    std::set<ObjectScript*> carried;
    for (const auto& entry : EquipWatchList()) {
        ObjectScript* instance = CarriedInstance(entry.first, entry.second);
        if (instance && instance->PollEquipped(entry.first)) {
            carried.insert(instance);
        }
    }
    for (ObjectScript* instance : carried) {
        if (cellChanged) instance->Events().cellChanged = true;
        if (instance->RunOnce()) ++ran;
    }
    return ran;
}

void RunOneTick() {
    ++g_ticks;
    if (!SessionLive() || GameHeldByMenu()) {
        g_lastCount = 0;
        return;
    }
    AdvanceGameSeconds(kTickDelta);
    if (Hooks().syncClock) Hooks().syncClock();
    const bool cellChanged = PlayerCellChanged();
    DiscoverLoaded(cellChanged);
    const std::vector<ObjectScript*> live = BoundInstances();
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
        //
        // 🛑 Polled BEFORE the gate, so the ORDER cannot lose the event. TES3
        // gives `OnDeath` one tick and the poll latches it once, so a gate that
        // skips the instance on the tick the death is seen discards it for good.
        // Corpses do NOT vanish -- they ragdoll and stay -- but the gate reads
        // false whenever `Game.GetForm` stops answering for the reference, and
        // the engine does free a dead one (see game_calls.cpp `RefByRuntimeId`).
        // Polling first costs nothing and does not depend on which it is.
        instance->PollDeath();
        if (Hooks().is3DLoaded) {
            if (Hooks().is3DLoaded(instance->RuntimeFormId())) {
                instance->MarkLoaded();
            } else if (instance->Events().died) {
                UnbindInstance(instance->RuntimeFormId());
            } else if (instance->WasLoaded()) {
                UnbindInstance(instance->RuntimeFormId());
                continue;
            } else {
                continue;
            }
        }
        if (cellChanged) instance->Events().cellChanged = true;
        instance->RunOnce();
        ++ran;
    }
    g_lastCount = ran + RunGlobalScripts() + RunCarriedScripts(cellChanged);
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

double GameSeconds() { return g_gameSeconds; }

void AdvanceGameSeconds(double seconds) { g_gameSeconds += seconds; }

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

void ResetTickState() {
    g_discoverAt = 0;
    g_untilHeartbeat = 0;
    g_lastCell.clear();
    g_cellSampled = false;
    g_accumulated = 0.0f;
    g_gameSeconds = 0.0;
}

}  // namespace mwruntime
