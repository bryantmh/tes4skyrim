// OpenMW's rule for an actor no AI or player is trying to move: "current
// position is assumed correct" (MovementSolver::unstuck), so nothing pushes it
// out of what it stands in. Morrowind plugins rely on it -- Arktwend sinks an
// NPC 36 units into the floor to seat it. Skyrim's character proxy pushes any
// penetrating actor out at its penetration recovery speed, moving or not; on
// each physics step a still actor gets a speed of 0 instead, which leaves the
// floor it is sunk into a surface at distance 0 right where it stands.
//
// The tick names the nearby actors that are NOT Morrowind's, which keep the
// engine's speed; every other actor is held from its first step, before any
// tick has seen it.
//
// An NPC npc_anim holds on its spot is moved by TranslateTo, which the
// character states do not step; a recovery speed of 0 did not keep it in the
// floor, so the tick takes its controller out of the simulation altogether
// with the engine's own paired-animation flag (ids::kControllerNoSim), which
// skips the controller's whole step and the move onto the navmesh. The flag
// is cleared again the tick after the hold is released.

#include "game_calls_internal.h"

#include <atomic>
#include <cstdint>
#include <iterator>
#include <mutex>
#include <unordered_set>

#include "ids.h"
#include "log.h"
#include "npc_anim.h"
#include "ref_handle.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

// Havok units a second under which an actor is not trying to move.
constexpr float kStillSpeed = 0.01f;

void** g_processLists = nullptr;
void* g_proxyVtable = nullptr;

// The engine's own recovery speed, read off the player's proxy; 0 until read.
std::atomic<float> g_engineSpeed{0.0f};

// The nearby actors' controllers that are not Morrowind's: written by the
// tick, read by the physics step.
std::mutex g_lock;
std::unordered_set<void*> g_skyrimControllers;

// The controllers the tick took out of the simulation, game thread only.
std::unordered_set<void*> g_noSim;

// The recovery speed of a proxy controller's proxy, or null for any other.
float* RecoverySpeed(void* controller) {
    if (!controller || !g_proxyVtable || At<void*>(controller, 0) != g_proxyVtable) {
        return nullptr;
    }
    void* proxy = At<void*>(controller, ids::kOffProxyControllerProxy);
    return proxy ? &At<float>(proxy, ids::kOffProxyRecoverySpeed) : nullptr;
}

bool FromMorrowind(void* actor) {
    void* base = At<void*>(actor, ids::kOffRefBase);
    return IsMorrowindForm(FormIdOf(actor)) || (base && IsMorrowindForm(FormIdOf(base)));
}

// The speed a non-player controller steps with: the engine's, or 0 while it is
// still.
float SpeedFor(void* controller, float engine) {
    std::lock_guard<std::mutex> lock(g_lock);
    if (g_skyrimControllers.count(controller)) return engine;
    const float* velocity = &At<float>(controller, ids::kOffControllerVelocity);
    const float planar = velocity[0] * velocity[0] + velocity[1] * velocity[1];
    return planar < kStillSpeed * kStillSpeed ? 0.0f : engine;
}

// Learns the engine's speed off the player, which is never held.
void LearnEngineSpeed(void* player) {
    const float* speed = RecoverySpeed(CharController(player));
    if (!speed || *speed <= 0.0f || g_engineSpeed.exchange(*speed) > 0.0f) return;
    Log("still: engine recovery speed %.3f; still actors keep their spot", *speed);
}

// Takes a held actor's controller out of the simulation, or gives one the
// hold has released back; a bit the engine set itself is left alone.
void SetNoSim(void* actor, void* controller, bool held) {
    if (!controller) return;
    std::uint32_t& flags = At<std::uint32_t>(controller, ids::kOffControllerFlags);
    const bool ours = g_noSim.count(controller) != 0;
    if (held && !ours && !(flags & ids::kControllerNoSim)) {
        flags |= ids::kControllerNoSim;
        g_noSim.insert(controller);
        LogVerbose("still: %08X held out of the simulation", FormIdOf(actor));
    } else if (!held && ours) {
        flags &= ~ids::kControllerNoSim;
        g_noSim.erase(controller);
        LogVerbose("still: %08X simulated again", FormIdOf(actor));
    }
}

}  // namespace

void HoldStill(void* controller, bool isPlayer) {
    float* speed = RecoverySpeed(controller);
    const float engine = g_engineSpeed.load();
    if (!speed || isPlayer || engine <= 0.0f || !g_processLists) return;
    *speed = SpeedFor(controller, engine);
}

void ForEachHighActor(const std::function<void(void*)>& fn) {
    void* lists = g_processLists ? *g_processLists : nullptr;
    const auto* handles = lists ? At<std::uint32_t*>(lists, ids::kOffHighActorHandles) : nullptr;
    const std::uint32_t count = handles ? At<std::uint32_t>(lists, ids::kOffHighActorCount) : 0;
    for (std::uint32_t i = 0; i < count; ++i) {
        void* actor = RefFromHandle(handles[i]);
        if (!actor) continue;
        fn(actor);
        ReleaseRef(actor);
    }
}

void TickStillActors(void* player) {
    LearnEngineSpeed(player);
    std::unordered_set<void*> skyrim;
    std::unordered_set<void*> seen;
    ForEachHighActor([&](void* actor) {
        void* controller = CharController(actor);
        if (!FromMorrowind(actor)) {
            skyrim.insert(controller);
        } else {
            SetNoSim(actor, controller, IsNpcHeld(FormIdOf(actor)));
        }
        if (controller) seen.insert(controller);
    });
    // A controller gone from the list may be freed: forget it rather than
    // touch it; a reloaded actor comes back with fresh flags.
    for (auto it = g_noSim.begin(); it != g_noSim.end();) {
        it = seen.count(*it) ? std::next(it) : g_noSim.erase(it);
    }
    std::lock_guard<std::mutex> lock(g_lock);
    g_skyrimControllers.swap(skyrim);
}

void InstallStillCalls() {
    g_processLists = reinterpret_cast<void**>(
        Resolve("ProcessLists singleton", ids::kProcessListsSingleton, nullptr));
    g_proxyVtable = reinterpret_cast<void*>(
        Resolve("bhkCharProxyController vtable", ids::kProxyControllerVtable, nullptr));
    Log("still: process lists %s, proxy controller %s", g_processLists ? "ok" : "MISSING",
        g_proxyVtable ? "ok" : "MISSING");
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
