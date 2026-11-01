// Brawls: TES3's punch tires its target, Skyrim's wounds it. An actor whose
// script reads its own fatigue when the player hits it (IsBrawler) gets the
// import's TES3BrawlAbility when it loads, and so does the player: the
// ability's effect marks the brawler, and its perk, which the engine applies
// only to the player, zeroes the player's bare fists on a marked target. Each
// such punch then takes the player's unarmed
// damage from its Stamina instead, and its Stamina stops regenerating until
// the punches stop. Every other actor keeps Skyrim's punch.
// See: docs/commentary/morrowind_runtime.md#brawls

#include "game_calls_internal.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <map>
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

// How long after its last punch a brawler stays winded. Skyrim pauses Stamina
// regeneration only 0.5 s after damage (fDamagedStaminaRegenDelay), which a
// punch every 1.5 s never outlasts.
constexpr std::uint32_t kWindedMs = 3000;

// Skyrim's default StaminaRateMult, what a winded value left in a save reverts to.
constexpr float kDefaultRateMult = 100.0f;

// Skyrim.esm's DGIntimidateFaction, vanilla's brawl faction. Its SpecialCombat
// flag makes a fight between members no crime that bystanders join; vanilla's
// DGIntimidateAliasScript puts the player and the opponent in it and takes
// both out on a hit with anything but fists.
constexpr std::uint32_t kBrawlFaction = 0x0004CFA6;

using AddSpellFn = bool (*)(void* vm, std::uint32_t stack, void* actor,
                            void* spell, bool verbose);
using GetValueFn = float (*)(void* vm, std::uint32_t stack, void* actor,
                             void* name);

// Actor.DrawWeapon(): the actor readies what it holds, its fists when nothing.
using DrawWeaponFn = void (*)(void* vm, std::uint32_t stack, void* actor);
using UnequipFn = void (*)(void* vm, std::uint32_t stack, void* actor, void* item,
                           bool preventEquip, bool silent);
using WeaponDrawnFn = bool (*)(void* vm, std::uint32_t stack, void* actor);

AddSpellFn g_addSpell = nullptr;
GetValueFn g_getValue = nullptr;
SetValueFn g_damageValue = nullptr;
DrawWeaponFn g_drawWeapon = nullptr;
UnequipFn g_unequip = nullptr;
WeaponDrawnFn g_weaponDrawn = nullptr;

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
// Further than this from its target, the player is placed in front again.
constexpr float kPunchReach = 110.0f;
// Turned further than this from its target (degrees), the player is placed again.
constexpr float kPunchAim = 20.0f;
constexpr const char* kAttackEvent = "attackStart";
constexpr float kDegreesPerRadian = 57.29578f;

// The test runner's `punch`: the player stands in front of `npc` facing it,
// readies its fists and swings once, so the engine lands a real hit. A player
// already in reach with fists up only swings: a move or a draw cancels the
// attack animation it lands in, which at real speed left 1 hit in 150.
bool PlayerPunch(const std::string& npc) {
    void* target = OwnerRef(npc);
    void* player = PlayerRef();
    if (!IsActorRef(target) || !player || !g_drawWeapon || !g_unequip ||
        !g_weaponDrawn || !Hooks().sendAnimEvent) {
        return false;
    }
    const float tx = RefPositionAxis(PapyrusVm(), target, 0);
    const float ty = RefPositionAxis(PapyrusVm(), target, 1);
    const float dx = RefPositionAxis(PapyrusVm(), player, 0) - tx;
    const float dy = RefPositionAxis(PapyrusVm(), player, 1) - ty;
    const float far = std::max(std::hypot(dx, dy), 1.0f);
    const float facing = std::atan2(-dx, -dy) * kDegreesPerRadian;
    const float off = std::remainder(facing - RefAngleDegrees(player, 2), 360.0f);
    if (far > kPunchReach || std::fabs(off) > kPunchAim) {
        const float x = tx + dx / far * kPunchStand, y = ty + dy / far * kPunchStand;
        PlaceAt(player, x, y, RefPositionAxis(PapyrusVm(), target, 2),
                std::atan2(tx - x, ty - y) * kDegreesPerRadian);
    }
    PostToMainThread([player]() {
        const bool armed = EquippedWeapon(player, false) || EquippedWeapon(player, true);
        if (armed) PutWeaponsAway(player);
        if (armed || !g_weaponDrawn(PapyrusVm(), 0, player)) {
            g_drawWeapon(PapyrusVm(), 0, player);
            return;
        }
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

// A winded brawler: the StaminaRateMult it had, and when it was last punched.
struct Winded {
    float rateMult;
    std::uint32_t lastPunchMs;
};
std::map<std::uint32_t, Winded> g_winded;

// The actor-value name StaminaRateMult as the natives take it (the address of
// its BSFixedString), or null when it does not resolve.
void* RateName() {
    static void* name = nullptr;
    if (!name && !FixedString(&name, "StaminaRateMult")) return nullptr;
    return &name;
}

void ArmBrawler(std::uint32_t runtimeFormId) {
    void* ability = Ability();
    if (!ability || !g_addSpell) return;
    PostToMainThread([runtimeFormId, ability]() {
        void* actor = RefByRuntimeId(runtimeFormId);
        if (!IsActorRef(actor)) return;
        g_addSpell(PapyrusVm(), 0, actor, ability, false);
        g_addSpell(PapyrusVm(), 0, PlayerRef(), ability, false);
        void* faction = RefByRuntimeId(kBrawlFaction);
        SetFactionRank(actor, faction, 0);
        SetFactionRank(PlayerRef(), faction, 0);
        void* rate = RateName();
        if (rate && g_getValue && g_setValue &&
            g_getValue(PapyrusVm(), 0, actor, rate) == 0.0f) {
            g_setValue(PapyrusVm(), 0, actor, rate, kDefaultRateMult);
        }
        Log("brawl: %08X fights with fists", runtimeFormId);
    });
}

// Stops `actor`'s Stamina regenerating until kWindedMs pass without a punch.
void Wind(std::uint32_t target, void* actor) {
    void* rate = RateName();
    if (!rate || !g_setValue || !Hooks().frameClockMs) return;
    auto it = g_winded.find(target);
    if (it == g_winded.end()) {
        const float saved = g_getValue(PapyrusVm(), 0, actor, rate);
        g_setValue(PapyrusVm(), 0, actor, rate, 0.0f);
        it = g_winded.emplace(target, Winded{saved, 0}).first;
        Log("brawl: %08X is winded", target);
    }
    it->second.lastPunchMs = Hooks().frameClockMs();
}

// Gives each brawler the punches have stopped for its regeneration back.
void BrawlTick() {
    if (g_winded.empty() || !Hooks().frameClockMs) return;
    const std::uint32_t now = Hooks().frameClockMs();
    for (auto it = g_winded.begin(); it != g_winded.end();) {
        if (now - it->second.lastPunchMs < kWindedMs) {
            ++it;
            continue;
        }
        void* actor = RefByRuntimeId(it->first);
        if (IsActorRef(actor)) g_setValue(PapyrusVm(), 0, actor, RateName(), it->second.rateMult);
        Log("brawl: %08X has its breath back", it->first);
        it = g_winded.erase(it);
    }
}

}  // namespace

void TireBrawler(std::uint32_t target) {
    void* actor = RefByRuntimeId(target);
    void* player = PlayerRef();
    void* unarmed = nullptr;
    void* stamina = nullptr;
    if (!IsActorRef(actor) || !player || !g_getValue || !g_damageValue ||
        !FixedString(&unarmed, "UnarmedDamage") || !FixedString(&stamina, "Stamina")) {
        return;
    }
    const float punch = g_getValue(PapyrusVm(), 0, player, &unarmed);
    g_damageValue(PapyrusVm(), 0, actor, &stamina, punch);
    Wind(target, actor);
    Log("brawl: %08X loses %g stamina to a punch", target, punch);
}

void BreakBrawl(std::uint32_t target) {
    void* actor = RefByRuntimeId(target);
    void* faction = RefByRuntimeId(kBrawlFaction);
    if (!IsActorRef(actor) || !faction) return;
    RemoveFromFaction(actor, faction);
    RemoveFromFaction(PlayerRef(), faction);
    Log("brawl: %08X hit with a weapon -- a real fight now", target);
}

void InstallBrawlCalls(GameHooks& hooks) {
    g_addSpell = Native<AddSpellFn>("Actor.AddSpell", ids::kActorAddSpell);
    g_getValue = Native<GetValueFn>("Actor.GetActorValue", ids::kActorGetValue);
    g_damageValue = Native<SetValueFn>("Actor.DamageActorValue", ids::kActorDamageValue);
    g_drawWeapon = Native<DrawWeaponFn>("Actor.DrawWeapon", ids::kActorDrawWeapon);
    g_unequip = Native<UnequipFn>("Actor.UnequipItem", ids::kActorUnequipItem);
    g_weaponDrawn = Native<WeaponDrawnFn>("Actor.IsWeaponDrawn", ids::kActorWeaponDrawn);
    hooks.armBrawler = ArmBrawler;
    hooks.brawlTick = BrawlTick;
    hooks.playerPunch = PlayerPunch;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
