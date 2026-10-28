// StartCombat and StopCombat.
//
// OpenMW's StartCombat gives the actor an AiCombat package that lasts until
// the target dies or StopCombat removes it, whatever their factions say.
// Skyrim's drops a fight between allies within seconds -- measured on
// Arktwend's bridge, where three Thieves Guild shadows walk away from Tate,
// their guild brother, and he never dies. So between two NPCs that are not
// already enemies, the attacker's relationship to its target is set to
// archnemesis for the fight, and put back once either side is dead or the
// attacker is told to StopCombat.
//
// 🛑 Skyrim keeps a relationship between the two BASE records, in the save.
// The rank it replaced is kept in the co-save, so a fight saved and loaded
// still ends with it restored.
// See: docs/commentary/morrowind_runtime.md#startcombat-between-allies

#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "dialogue_state.h"
#include "game_calls.h"
#include "game_calls_internal.h"
#include "ids.h"
#include "log.h"
#include "main_thread.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

using StartCombatFn = void (*)(void* vm, std::uint32_t stack, void* actor,
                               void* target);
using StopCombatFn = void (*)(void* vm, std::uint32_t stack, void* actor);
using GetRankFn = std::int32_t (*)(void* vm, std::uint32_t stack, void* actor,
                                   void* other);
using SetRankFn = void (*)(void* vm, std::uint32_t stack, void* actor,
                           void* other, std::int32_t rank);
StartCombatFn g_startCombat = nullptr;
StopCombatFn g_stopCombat = nullptr;
GetRankFn g_getRank = nullptr;
SetRankFn g_setRank = nullptr;

// Skyrim's archnemesis rank, which keeps two actors fighting on sight.
constexpr std::int32_t kArchnemesis = -4;
// The co-save owner the feuds live under, by `attacker|target` TES3 id.
constexpr const char* kFeuds = "combat|feud";
// A feud already ended; the co-save has no way to drop a variable.
constexpr float kEnded = 99.0f;

std::string FeudName(const std::string& attacker, const std::string& target) {
    return Lower(attacker) + "|" + Lower(target);
}

// Puts the rank `name`'s feud replaced back, and marks it ended.
void EndFeud(const std::string& name, void* actor, void* foe) {
    const float rank = State().Var(kFeuds, name);
    State().SetVar(kFeuds, name, kEnded);
    if (g_setRank) g_setRank(PapyrusVm(), 0, actor, foe, static_cast<std::int32_t>(rank));
    Log("combat: %s ends -- relationship back to %d", name.c_str(),
        static_cast<int>(rank));
}

// Calls `fn(name, actor, foe)` for each live feud whose two actors resolve.
template <typename Fn>
void ForEachFeud(Fn fn) {
    for (const std::string& name : State().VarNames(kFeuds)) {
        if (State().Var(kFeuds, name) == kEnded) continue;
        const std::size_t bar = name.find('|');
        void* actor = OwnerRef(name.substr(0, bar));
        void* foe = OwnerRef(name.substr(bar + 1));
        if (actor && foe) fn(name, actor, foe);
    }
}

// Game thread: the feud that keeps `actor` on `foe`, unless they are enemies
// already or the target is the player, whom Skyrim's combat never drops.
void BeginFeud(const std::string& name, void* actor, void* foe) {
    if (foe == PlayerRef() || !g_getRank || !g_setRank) return;
    if (State().HasVar(kFeuds, name) && State().Var(kFeuds, name) != kEnded) return;
    const std::int32_t rank = g_getRank(PapyrusVm(), 0, actor, foe);
    if (rank <= kArchnemesis) return;
    State().SetVar(kFeuds, name, static_cast<float>(rank));
    g_setRank(PapyrusVm(), 0, actor, foe, kArchnemesis);
    Log("combat: %s -- relationship %d set to archnemesis for the fight",
        name.c_str(), static_cast<int>(rank));
}

// 🛑 Posted to the game thread rather than called here: the dialogue menu's
// callbacks do not run on it, and combat reaches deep into the AI process.
// An empty `target` means StopCombat, which takes no target at all. As in
// OpenMW (OpStartCombat, MechanicsManager::startCombat), a dead attacker or
// target starts nothing: Arktwend's battle scripts call it every frame on the
// fallen, and each call began and ended a feud.
void SetCombat(const std::string& attacker, const std::string& target) {
    void* actor = OwnerRef(attacker);
    void* foe = target.empty() ? nullptr : OwnerRef(target);
    if (!actor || (!target.empty() && !foe)) return;
    if (foe && (IsDead(actor) || IsDead(foe))) return;
    if (foe) Log("combat: %s attacks %s", attacker.c_str(), target.c_str());
    const std::string name = FeudName(attacker, target);
    const std::string prefix = Lower(attacker) + "|";
    PostToMainThread([actor, foe, name, prefix]() {
        if (foe) {
            BeginFeud(name, actor, foe);
            if (g_startCombat) g_startCombat(PapyrusVm(), 0, actor, foe);
            return;
        }
        ForEachFeud([&](const std::string& feud, void* from, void* to) {
            if (feud.compare(0, prefix.size(), prefix) == 0) EndFeud(feud, from, to);
        });
        if (g_stopCombat) g_stopCombat(PapyrusVm(), 0, actor);
    });
}

// Ticks between two looks for creatures guards should set on.
constexpr int kGuardTicks = 10;
// OpenMW's (and Morrowind.esm's default) fAlarmRadius.
constexpr float kAlarmRadius = 2000.0f;

// OpenMW's guard rule (Actors::engageCombat, actors.cpp:733): a Guard-class
// NPC not already fighting attacks a hostile creature that is in combat
// within fAlarmRadius. The import marks both with a faction; a creature's own
// Fight targets the player alone, so nothing else sets a guard on it.
// See: docs/commentary/tes5_import_actors.md#tes3-guards-attack-hostile-creatures
void GuardsEngage() {
    void* guards = DispositionForm("guard");
    void* hostile = DispositionForm("hostile_creature");
    if (!guards || !hostile || !g_startCombat) return;
    const float radius = GmstNumber("fAlarmRadius", kAlarmRadius);
    std::vector<void*> fighting, idle;
    static std::map<std::uint32_t, std::uint32_t> targets;
    ForEachHighActor([&](void* actor) {
        if (IsDead(actor)) return;
        void* target = CombatTargetOf(actor);
        const std::uint32_t id = FormIdOf(actor), on = target ? FormIdOf(target) : 0;
        if (targets[id] != on) {
            targets[id] = on;
            Log("combat: %08X now targets %08X", id, on);
        }
        const bool inCombat = ActorInCombat(actor);
        if (inCombat && FactionRank(actor, hostile) >= 0) fighting.push_back(actor);
        if (!inCombat && FactionRank(actor, guards) >= 0) idle.push_back(actor);
    });
    for (void* guard : idle) {
        for (void* creature : fighting) {
            if (DistanceBetween(guard, creature) > radius) continue;
            g_startCombat(PapyrusVm(), 0, guard, creature);
            Log("combat: guard %08X sets on %08X", FormIdOf(guard), FormIdOf(creature));
            break;
        }
    }
}

void CombatTick() {
    ForEachFeud([](const std::string& name, void* actor, void* foe) {
        if (IsDead(actor) || IsDead(foe)) EndFeud(name, actor, foe);
    });
    static int untilGuards = 0;
    if (--untilGuards > 0) return;
    untilGuards = kGuardTicks;
    PostToMainThread(GuardsEngage);
}

}  // namespace

void InstallCombatCalls(GameHooks& hooks) {
    g_startCombat = Native<StartCombatFn>("Actor.StartCombat", ids::kActorStartCombat);
    g_stopCombat = Native<StopCombatFn>("Actor.StopCombat", ids::kActorStopCombat);
    g_getRank = Native<GetRankFn>("Actor.GetRelationshipRank",
                                  ids::kActorGetRelationshipRank);
    g_setRank = Native<SetRankFn>("Actor.SetRelationshipRank",
                                  ids::kActorSetRelationshipRank);
    hooks.setCombat = SetCombat;
    hooks.combatTick = CombatTick;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
