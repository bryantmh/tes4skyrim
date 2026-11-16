// Converted TES4 escorts of the player restart once the player is back beside them.
//
// A converted escort runs the TES4EscortWhenNear root: Stacked { Escort while the player is within
// 512 and the escorter is not in TES4EscortHold ; Wait }. Measured on Baurus's sewer escorts (runs
// 42a-43a): after he takes a loading door ahead of the player he can stand on the package with no
// procedure running, the player beside him, for 3600 ticks. Two states: one an `evp` ends; one only
// the Escort branch's gate going false and then true ends (the player stepping out of range and
// back), not `evp` or `resetai`. Oblivion's escort simply walks on once the player is near. So an
// escorter on one of those packages that stands still for two checks with the player within the
// radius, in the same place, out of combat and out of dialogue, is put in the hold faction (its
// package re-checked: Wait), and taken out kHold later (re-checked: a fresh Escort).
// See: docs/commentary/tes5_import_package.md#escort-restarts-when-the-target-returns

#include "game_calls_internal.h"

#include <chrono>
#include <cmath>
#include <cstdint>
#include <map>
#include <set>
#include <vector>

#include "glide.h"
#include "ids.h"
#include "log.h"
#include "menu.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

// escort_when_near.NEAR_RADIUS: the Escort branch runs while the player is this close.
constexpr float kEscortNear = 512.0f;
// Movement under this between two checks is standing still.
constexpr float kStillStep = 16.0f;
// Still checks (one per combat tick group) before the escorter's Escort is restarted.
constexpr int kStillChecks = 2;
// How long an escorter stays held, in real time: one check changed nothing in 179 tries (44c) and a ~2 s
// hold nothing in 20+ (54b), while the player out of range for 4-5 s restarted him at once (44c, 54b).
constexpr auto kHold = std::chrono::seconds(5);

struct Stall {
    float x = 0.0f, y = 0.0f, z = 0.0f;
    int still = 0;
};
// Escorters near the player by runtime FormID, rebuilt every check.
std::map<std::uint32_t, Stall> g_stalls;
// Escorters in the hold faction, by runtime FormID, with the moment each is released.
std::map<std::uint32_t, std::chrono::steady_clock::time_point> g_held;

// The runtime FormIDs of every plugin's converted player escorts.
std::set<std::uint32_t> EscortPackageIds() {
    std::set<std::uint32_t> ids;
    for (const FormRef& ref : PlayerEscortPackages()) {
        void* form = Form(&ref);
        if (form) ids.insert(FormIdOf(form));
    }
    return ids;
}

// Every plugin's hold faction that resolves.
std::vector<void*> HoldFactions() {
    std::vector<void*> holds;
    for (const FormRef& ref : EscortHoldFactions()) {
        void* form = Form(&ref);
        if (form) holds.push_back(form);
    }
    return holds;
}

bool InAnyHold(void* actor, const std::vector<void*>& holds) {
    for (void* faction : holds) {
        if (FactionRank(actor, faction) >= 0) return true;
    }
    return false;
}

// Puts `actor` in (or takes it out of) every hold faction, then has it re-check its package.
void SetHeld(void* actor, const std::vector<void*>& holds, bool held) {
    for (void* faction : holds) {
        if (held) {
            SetFactionRank(actor, faction, 0);
        } else {
            RemoveFromFaction(actor, faction);
        }
    }
    ReevaluatePackage(actor);
}

// Releases the held escorters whose hold is over.
void ReleaseHeld(const std::vector<void*>& holds) {
    const auto now = std::chrono::steady_clock::now();
    for (auto it = g_held.begin(); it != g_held.end();) {
        if (now < it->second) {
            ++it;
            continue;
        }
        void* actor = RefByRuntimeId(it->first);
        if (actor && IsActorRef(actor)) SetHeld(actor, holds, false);
        it = g_held.erase(it);
    }
}

// `actor`'s stall record after this check, from its last one in `before`.
Stall NextStall(const std::map<std::uint32_t, Stall>& before, std::uint32_t id, void* actor) {
    Stall now;
    now.x = RefPositionAxis(PapyrusVm(), actor, 0);
    now.y = RefPositionAxis(PapyrusVm(), actor, 1);
    now.z = RefPositionAxis(PapyrusVm(), actor, 2);
    const auto it = before.find(id);
    if (it != before.end() && std::hypot(now.x - it->second.x, now.y - it->second.y, now.z - it->second.z) <
                                  kStillStep) {
        now.still = it->second.still + 1;
    }
    return now;
}

// Whether the player is in reach of `actor`'s Escort branch, with nothing else holding it.
bool EscortsBesidePlayer(void* actor, void* player) {
    if (ActorInCombat(actor) || PlaceOf(actor) != PlaceOf(player)) return false;
    const float distance = DistanceBetween(actor, player);
    return distance >= 0.0f && distance <= kEscortNear;
}

// Restarts `actor`'s Escort through the hold faction; a plugin built without one only re-checks.
void Restart(void* actor, std::uint32_t id, const std::vector<void*>& holds) {
    Log("ai: %08X stands on its escort with the player beside it -- its escort restarts", id);
    if (holds.empty()) {
        ReevaluatePackage(actor);
        return;
    }
    SetHeld(actor, holds, true);
    g_held[id] = std::chrono::steady_clock::now() + kHold;
}

}  // namespace

void RestartStalledEscorts() {
    const std::vector<void*> holds = HoldFactions();
    ReleaseHeld(holds);
    void* player = PlayerRef();
    if (!player || PlayerEscortPackages().empty() || EngineMenuOpen(ids::kVanillaDialogueMenu)) {
        g_stalls.clear();
        return;
    }
    const std::set<std::uint32_t> escorts = EscortPackageIds();
    std::map<std::uint32_t, Stall> now;
    ForEachHighActor([&](void* actor) {
        void* package = IsDead(actor) ? nullptr : RunningPackageOf(actor);
        const std::uint32_t id = FormIdOf(actor);
        if (!package || !escorts.count(FormIdOf(package)) || g_held.count(id)) return;
        if (InAnyHold(actor, holds)) {
            SetHeld(actor, holds, false);
            return;
        }
        if (!EscortsBesidePlayer(actor, player)) return;
        Stall stall = NextStall(g_stalls, id, actor);
        if (stall.still >= kStillChecks) {
            Restart(actor, id, holds);
            stall.still = 0;
        }
        now[id] = stall;
    });
    g_stalls = now;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
