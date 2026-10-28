#include "object_tick.h"

#include <algorithm>
#include <iterator>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <vector>

#include "dialogue_state.h"
#include "log.h"
#include "main_thread.h"
#include "main_tick.h"
#include "npc_anim.h"
#include "object_script.h"
#include "scope.h"
#include "script_tables.h"
#include "test_runner.h"

namespace tesruntime::mw {

namespace {

//: Ticks per second. See the header for why this is fixed, and why it is 30.
constexpr float kTickRate = 30.0f;

//: The seconds one tick covers, which GetSecondsPassed answers with.
constexpr float kTickDelta = 1.0f / kTickRate;

// The tick's wait, in WHOLE milliseconds (main_tick.h says why).
constexpr int kTickMs = static_cast<int>(1000.0f / kTickRate);

// An elapsed span this long is a load screen or a stall; catching up on it
// would run hundreds of ticks at once, so it is clamped instead. One game
// second per tick leaves room for a game run up to 30x by iFPSClamp.
constexpr float kMaxCatchUp = 1.0f;

float g_accumulated = 0.0f;
// The game seconds the running tick covers (TickDelta).
float g_tickDelta = kTickDelta;
// Seconds of unpaused play, which is the clock every runtime timer reads.
double g_gameSeconds = 0.0;
std::size_t g_lastCount = 0;
std::size_t g_ticks = 0;
bool g_started = false;

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

// Where the player's sleep stands for `GetPCSleep`: `kWoke` holds the answer
// until one tick has run after the engine said the sleep ended.
enum class Sleep { kAwake, kAsleep, kWoke };
Sleep g_sleep = Sleep::kAwake;

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
        // 🛑 A reference loads because the player changed cell, and TES3 runs
        // its first frame with `CellChanged` set. The sweep binds it a few
        // ticks AFTER the change, so without this every `if ( CellChanged ==
        // 0 ) return` guard missed its only chance -- measured, the Vivec
        // arena script never brought Bolvyn Venim to his duel.
        if (ObjectScript* bound = InstanceForRef(ref)) {
            bound->Events().cellChanged = true;
        }
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
    Log("cell: the player is in '%s'", now.c_str());
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

// Items the container-changed event reported since the last tick, by runtime
// FormID; the event arrives on any thread.
std::mutex g_changedLock;
std::vector<std::uint32_t> g_changedItems;

// The watch entries the player may hold, by EquipWatchList index, and whether
// the whole list is swept once to find them, as after a load.
std::set<std::size_t> g_maybeCarried;
bool g_sweepCarried = true;

// The watch entries naming one item's runtime FormID.
const std::vector<std::size_t>* WatchesFor(std::uint32_t formId) {
    static std::map<std::uint32_t, std::vector<std::size_t>> byForm;
    static bool built = false;
    if (!built && Hooks().itemFormId) {
        built = true;
        const std::vector<EquipWatch>& list = EquipWatchList();
        for (std::size_t i = 0; i < list.size(); ++i) {
            const LayerScope scope(list[i].layer);
            byForm[Hooks().itemFormId(list[i].item)].push_back(i);
        }
    }
    const auto it = byForm.find(formId);
    return it == byForm.end() ? nullptr : &it->second;
}

// Adds the entries the events named, or every entry when a sweep is due.
void GatherMaybeCarried() {
    std::vector<std::uint32_t> changed;
    {
        std::lock_guard<std::mutex> hold(g_changedLock);
        changed.swap(g_changedItems);
    }
    if (g_sweepCarried) {
        g_sweepCarried = false;
        for (std::size_t i = 0; i < EquipWatchList().size(); ++i) g_maybeCarried.insert(i);
    }
    for (const std::uint32_t id : changed) {
        const std::vector<std::size_t>* entries = WatchesFor(id);
        if (entries) g_maybeCarried.insert(entries->begin(), entries->end());
    }
}

// Runs the script of every scripted item the PLAYER carries, as TES3 does. An
// inventory item has no placement, so the world-discovery sweep above can
// never reach it.
//
// 🛑 Only items the player may hold are polled: one the event or the sweep
// named, kept until a poll finds it gone. Asking the engine about every
// scripted item each tick would not be affordable.
// See: docs/commentary/morrowind_runtime.md#engine-written-locals
std::size_t RunCarriedScripts(bool cellChanged) {
    std::size_t ran = 0;
    GatherMaybeCarried();
    // 🛑 Every record is polled, and the instance runs at most once per tick.
    // Several item records share one script, so stopping at the first carried
    // one would miss the sibling actually worn; running per record would run
    // the body once per record.
    std::set<ObjectScript*> carried;
    const std::vector<EquipWatch>& list = EquipWatchList();
    for (auto it = g_maybeCarried.begin(); it != g_maybeCarried.end();) {
        const EquipWatch& entry = list[*it];
        const LayerScope scope(entry.layer);
        ObjectScript* instance = CarriedInstance(entry.item, entry.script);
        const bool held = instance && instance->PollEquipped(entry.item);
        if (held) carried.insert(instance);
        it = held ? std::next(it) : g_maybeCarried.erase(it);
    }
    for (ObjectScript* instance : carried) {
        if (cellChanged) instance->Events().cellChanged = true;
        if (instance->RunOnce()) ++ran;
    }
    return ran;
}

// Why an instance stopped ticking, which is otherwise invisible: a dropped
// instance simply never appears in the log again, and the rebind sweep gates
// on the SAME reading, so a misread drops a script for the whole session.
//
// 🛑 Names which half of the old combined test was false. `kUnresolved` means
// `Game.GetForm` did not answer, which is NOT an unload for a persistent
// reference -- that distinction is the open question this logging settles.
// Every CHANGE in an instance's load reading, so the ticks leading into a drop
// are visible rather than only the drop itself. Silent while the answer holds
// steady, which is almost always -- a steady reading prints nothing.
void TraceLoadState(const ObjectScript& instance) {
    if (!Hooks().load3DState) return;
    static std::map<std::uint32_t, LoadState> last;
    const std::uint32_t id = instance.RuntimeFormId();
    const LoadState now = Hooks().load3DState(id);
    const auto seen = last.find(id);
    if (seen != last.end() && seen->second == now) return;
    last[id] = now;
    static const char* kNames[] = {"loaded", "UNLOADED", "UNRESOLVED",
                                   "unknown"};
    LogVerbose("object: %s (%08X) load reading -> %s (tick %zu)",
               instance.Script().c_str(), id,
               kNames[static_cast<int>(now)], g_ticks);
}

void ReportUnbind(const ObjectScript& instance, const char* why) {
    const std::uint32_t id = instance.RuntimeFormId();
    const LoadState state = Hooks().load3DState
                                ? Hooks().load3DState(id)
                                : LoadState::kUnknown;
    const char* reading = "unknown -- the native was never resolved";
    if (state == LoadState::kUnloaded) reading = "Is3DLoaded said NO";
    if (state == LoadState::kUnresolved) reading = "GetForm did not answer";
    if (state == LoadState::kLoaded) reading = "loaded again since the test";
    LogVerbose("object: %s (%08X) unbound -- %s, %s; ran %zu tick(s), "
               "cell '%s'", instance.Script().c_str(), id, why, reading,
               g_ticks, g_lastCell.c_str());
}

void RunOneTick() {
    ++g_ticks;
    const bool inWorld = SessionLive();
    if (inWorld && Hooks().crimeTick) Hooks().crimeTick();
    if (inWorld && Hooks().combatTick) Hooks().combatTick();
    if (inWorld) TickTestRunner(GameHeldByMenu());
    if (!inWorld || GameHeldByMenu()) {
        g_lastCount = 0;
        return;
    }
    AdvanceGameSeconds(g_tickDelta);
    if (Hooks().syncClock) Hooks().syncClock();
    if (Hooks().effectTick) Hooks().effectTick();
    if (Hooks().werewolfTick) Hooks().werewolfTick();
    if (Hooks().animTick) Hooks().animTick();
    const bool cellChanged = PlayerCellChanged();
    DiscoverLoaded(cellChanged);
    TickNpcAnims(cellChanged);
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
        TraceLoadState(*instance);
        if (Hooks().is3DLoaded) {
            if (Hooks().is3DLoaded(instance->RuntimeFormId())) {
                instance->MarkLoaded();
            } else if (instance->Events().died) {
                ReportUnbind(*instance, "died");
                UnbindInstance(instance->RuntimeFormId());
            } else if (instance->WasLoaded()) {
                ReportUnbind(*instance, "unloaded");
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
    g_lastCount = ran + RunGlobalScripts(cellChanged) + RunCarriedScripts(cellChanged);
    if (g_sleep == Sleep::kWoke) g_sleep = Sleep::kAwake;
}

}  // namespace

float TickDelta() { return g_tickDelta; }

double GameSeconds() { return g_gameSeconds; }

void AdvanceGameSeconds(double seconds) { g_gameSeconds += seconds; }

std::size_t LastTickCount() { return g_lastCount; }

std::size_t TicksRun() { return g_ticks; }

void NoteSleepStarted() { g_sleep = Sleep::kAsleep; }

void NoteSleepStopped() {
    if (g_sleep == Sleep::kAsleep) g_sleep = Sleep::kWoke;
}

void WakePlayer() { g_sleep = Sleep::kAwake; }

bool PlayerSleeping() { return g_sleep != Sleep::kAwake; }

void TickObjectScripts(float frameSeconds) {
    if (frameSeconds <= 0.0f) return;
    g_accumulated += frameSeconds > kMaxCatchUp ? kMaxCatchUp : frameSeconds;
    while (g_accumulated >= kTickDelta) {
        g_accumulated -= kTickDelta;
        RunOneTick();
    }
}

namespace {

std::uint32_t g_lastClockMs = 0;

// Each fixed-rate wake runs ONE tick covering the game time since the last
// wake, so a game sped up by iFPSClamp ticks its scripts at its own pace and
// every tick still gets its own frame: an AddItem is not visible until the
// next frame, so two ticks inside one would break a script reading it back.
// A held game, or one with no clock, gets a plain 1/30 s tick as before.
// See: docs/commentary/headless_testing.md#speed
void OnWake() {
    const std::uint32_t now = Hooks().frameClockMs ? Hooks().frameClockMs() : 0;
    const std::uint32_t passedMs = g_lastClockMs ? now - g_lastClockMs : 0;
    g_lastClockMs = now;
    const bool timed = now && passedMs && SessionLive() && !GameHeldByMenu();
    g_tickDelta = timed ? std::min(static_cast<float>(passedMs) / 1000.0f, kMaxCatchUp) : kTickDelta;
    RunOneTick();
}

}  // namespace

// 🛑 Posted through PostToMainThread, not the task interface directly: its
// task records the game thread's id, which RunOnGameThread needs to run a
// script's calls inline during the tick instead of a frame later.
// See: docs/plans/morrowind_object_scripts.md#tick-rate
void StartObjectTick() {
    if (g_started) return;
    if (!CanPostToMainThread() || !StartTick(PostToMainThread, kTickMs, OnWake)) {
        Log("object: no task interface -- object scripts will NOT tick");
        return;
    }
    g_started = true;
    Log("object: tick started at %g Hz", kTickRate);
}

void NoteCarriedChange(std::uint32_t itemFormId) {
    std::lock_guard<std::mutex> hold(g_changedLock);
    g_changedItems.push_back(itemFormId);
}

void ResetTickState() {
    g_maybeCarried.clear();
    g_sweepCarried = true;
    g_discoverAt = 0;
    g_untilHeartbeat = 0;
    g_lastCell.clear();
    g_cellSampled = false;
    g_accumulated = 0.0f;
    g_gameSeconds = 0.0;
    g_sleep = Sleep::kAwake;
    ResetNpcAnims();
}

}  // namespace tesruntime::mw
