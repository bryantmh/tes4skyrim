// Brawls: TES3's punch tires its target, Skyrim's wounds it. An actor whose
// script reads its own fatigue when the player hits it (IsBrawler) gets the
// import's TES3BrawlAbility when it loads, whose perk zeroes the health the
// player's bare fists take; each such punch then takes the player's unarmed
// damage from its Stamina instead. Every other actor keeps Skyrim's punch.
// See: docs/commentary/morrowind_runtime.md#brawls

#include "game_calls_internal.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>

#include "activation.h"
#include "glide.h"
#include "ids.h"
#include "log.h"
#include "main_thread.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

// The row effects_formid.txt names the ability by.
constexpr const char* kAbilityRow = "brawl";

// Off until a brawler's drain and regeneration are chosen: Skyrim's Stamina
// regeneration outruns a 4-point punch.
// See: docs/audits/headless_quest_runs.md#raven-rock-open
constexpr bool kBrawlsOn = false;

using AddSpellFn = bool (*)(void* vm, std::uint32_t stack, void* actor,
                            void* spell, bool verbose);
using GetValueFn = float (*)(void* vm, std::uint32_t stack, void* actor,
                             void* name);

// Actor.DrawWeapon(): the actor readies what it holds, its fists when nothing.
using DrawWeaponFn = void (*)(void* vm, std::uint32_t stack, void* actor);
using UnequipFn = void (*)(void* vm, std::uint32_t stack, void* actor, void* item,
                           bool preventEquip, bool silent);

AddSpellFn g_addSpell = nullptr;
GetValueFn g_getValue = nullptr;
SetValueFn g_damageValue = nullptr;
DrawWeaponFn g_drawWeapon = nullptr;
UnequipFn g_unequip = nullptr;

// Empties both of `actor`'s hands of weapons, so its attack is a bare fist.
void PutWeaponsAway(void* actor) {
    for (const bool left : {false, true}) {
        void* weapon = EquippedWeapon(actor, left);
        if (weapon) g_unequip(PapyrusVm(), 0, actor, weapon, false, true);
    }
}

// How far in front of its target the test runner's player stands to punch,
// inside Skyrim's unarmed reach; and its attack's behavior event.
constexpr float kPunchStand = 60.0f;
constexpr const char* kAttackEvent = "attackStart";
constexpr float kDegreesPerRadian = 57.29578f;

// The test runner's `punch`: the player stands in front of `npc` facing it,
// readies its fists and swings once, so the engine lands a real hit.
bool PlayerPunch(const std::string& npc) {
    void* target = OwnerRef(npc);
    void* player = PlayerRef();
    if (!IsActorRef(target) || !player || !g_drawWeapon || !g_unequip ||
        !Hooks().sendAnimEvent) {
        return false;
    }
    const float tx = RefPositionAxis(PapyrusVm(), target, 0);
    const float ty = RefPositionAxis(PapyrusVm(), target, 1);
    const float dx = RefPositionAxis(PapyrusVm(), player, 0) - tx;
    const float dy = RefPositionAxis(PapyrusVm(), player, 1) - ty;
    const float far = std::max(std::hypot(dx, dy), 1.0f);
    const float x = tx + dx / far * kPunchStand, y = ty + dy / far * kPunchStand;
    PlaceAt(player, x, y, RefPositionAxis(PapyrusVm(), target, 2),
            std::atan2(tx - x, ty - y) * kDegreesPerRadian);
    PostToMainThread([player]() {
        PutWeaponsAway(player);
        g_drawWeapon(PapyrusVm(), 0, player);
        Hooks().sendAnimEvent(FormIdOf(player), kAttackEvent);
    });
    return true;
}

// The staged ability, once it resolves; the load order cannot change after.
void* Ability() {
    static void* ability = nullptr;
    if (!ability) {
        const FormRef* row = EffectForm(kAbilityRow);
        ability = row ? Form(row) : nullptr;
    }
    return ability;
}

void ArmBrawler(std::uint32_t runtimeFormId) {
    void* ability = kBrawlsOn ? Ability() : nullptr;
    if (!ability || !g_addSpell) return;
    PostToMainThread([runtimeFormId, ability]() {
        void* actor = RefByRuntimeId(runtimeFormId);
        if (!IsActorRef(actor)) return;
        g_addSpell(PapyrusVm(), 0, actor, ability, false);
        Log("brawl: %08X fights with fists", runtimeFormId);
    });
}

}  // namespace

void TireBrawler(std::uint32_t target) {
    void* actor = RefByRuntimeId(target);
    void* player = PlayerRef();
    void* unarmed = nullptr;
    void* stamina = nullptr;
    if (!kBrawlsOn || !IsActorRef(actor) || !player || !g_getValue || !g_damageValue ||
        !FixedString(&unarmed, "UnarmedDamage") || !FixedString(&stamina, "Stamina")) {
        return;
    }
    const float punch = g_getValue(PapyrusVm(), 0, player, &unarmed);
    g_damageValue(PapyrusVm(), 0, actor, &stamina, punch);
    Log("brawl: %08X loses %g stamina to a punch", target, punch);
}

void InstallBrawlCalls(GameHooks& hooks) {
    g_addSpell = Native<AddSpellFn>("Actor.AddSpell", ids::kActorAddSpell);
    g_getValue = Native<GetValueFn>("Actor.GetActorValue", ids::kActorGetValue);
    g_damageValue = Native<SetValueFn>("Actor.DamageActorValue", ids::kActorDamageValue);
    g_drawWeapon = Native<DrawWeaponFn>("Actor.DrawWeapon", ids::kActorDrawWeapon);
    g_unequip = Native<UnequipFn>("Actor.UnequipItem", ids::kActorUnequipItem);
    hooks.armBrawler = ArmBrawler;
    hooks.playerPunch = PlayerPunch;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
