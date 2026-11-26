// Jump and the Acrobatics effects: TES3 throws a jumper higher with them, and
// Skyrim's jump reads no actor value for it. Skyrim's jump height is the game
// setting fJumpHeightMin times the actor's scale, so while the player carries
// either effect the tick sets it to the vanilla value times how much higher
// OpenMW's Npc::getJump throws the player with the effects than without them,
// from the same Acrobatics, load and fatigue. Without them the vanilla value is
// back. Landing and fall damage stay Skyrim's.
// See: docs/commentary/morrowind_runtime.md#jump-and-acrobatics

#include "game_calls_internal.h"

#include <algorithm>
#include <cmath>

#include "actor_stats.h"
#include "log.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

constexpr const char* kPlayerId = "player";
constexpr const char* kJumpSetting = "fJumpHeightMin";

// TES3 effect indices: Jump, and Drain and Fortify Skill; Acrobatics' skill index.
constexpr int kJumpEffect = 9;
constexpr int kDrainSkill = 21;
constexpr int kFortifySkill = 83;
constexpr int kAcrobatics = 20;

// OpenMW's Constants::GravityConst * UnitsPerMeter, which getJump adds before dividing by 3.
constexpr float kGravity = 8.96f * 69.99125109f;

// getJump: Acrobatics up to 50 raises the speed along a power curve, every point past it linearly.
constexpr float kCurveCap = 50.0f;
constexpr float kCurveDivisor = 15.0f;
constexpr float kPastCapWeight = 3.0f;
constexpr float kJumpPointWeight = 64.0f;
constexpr float kSpeedDivisor = 3.0f;

float* g_setting = nullptr;
float g_vanilla = 0.0f;
bool g_sought = false;
float g_written = 1.0f;
float g_loggedJump = 0.0f;
float g_loggedChange = 0.0f;

// OpenMW's Npc::getJump for Acrobatics `a` and a Jump of `jump`: the take-off speed.
float JumpSpeed(float a, float jump, float load, float fatigue) {
    const float mult = GmstNumber("fJumpAcroMultiplier", 4.0f);
    const float curve = std::min(a, kCurveCap);
    const float past = std::max(0.0f, a - kCurveCap);
    float x = GmstNumber("fJumpAcrobaticsBase", 128.0f) + std::pow(curve / kCurveDivisor, mult);
    x += kPastCapWeight * past * mult + kJumpPointWeight * jump;
    x *= GmstNumber("fJumpEncumbranceBase", 0.5f) +
         GmstNumber("fJumpEncumbranceMultiplier", 1.0f) * (1.0f - load);
    x *= fatigue;
    return (x + kGravity) / kSpeedDivisor;
}

// What active Fortify and Drain Acrobatics add to the player's Acrobatics.
float AcrobaticsChange(void* player) {
    float change = 0.0f;
    ForEachActiveEffect(player, [&](const RuntimeEffect& row, float magnitude) {
        if (row.skill != kAcrobatics) return;
        if (row.index == kFortifySkill) change += magnitude;
        if (row.index == kDrainSkill) change -= magnitude;
    });
    return change;
}

// How much higher than vanilla the player jumps: getJump's speed with the
// effects over its speed without them, squared, as height goes with speed squared.
float HeightScale(float jump, float change) {
    if (!Hooks().actorValue || !Hooks().statPercent) return 1.0f;
    const float capacity = Hooks().actorValue(kPlayerId, "CarryWeight");
    const float load = capacity > 0.0f
                           ? Hooks().actorValue(kPlayerId, "InventoryWeight") / capacity
                           : 0.0f;
    if (load > 1.0f) return 1.0f;
    const float stamina = Hooks().statPercent(kPlayerId, "Stamina");
    const float fatigue = GmstNumber("fFatigueBase", 1.25f) -
                          GmstNumber("fFatigueMult", 0.5f) * (1.0f - stamina);
    const float base = ActorSkill(kPlayerId, kAcrobatics);
    const float without = JumpSpeed(base, 0.0f, load, fatigue);
    const float with = JumpSpeed(std::max(0.0f, base + change), jump, load, fatigue);
    return without > 0.0f ? std::max(0.0f, with * with / (without * without)) : 1.0f;
}

}  // namespace

void TickJump(void* player) {
    if (!g_sought) {
        g_sought = true;
        g_setting = GameSettingFloat(kJumpSetting);
        if (g_setting) g_vanilla = *g_setting;
        Log("jump: %s %s", kJumpSetting, g_setting ? "found" : "NOT FOUND -- Jump stays inert");
    }
    if (!g_setting || !player) return;
    const float jump = ActiveMagnitude(player, kJumpEffect);
    const float change = AcrobaticsChange(player);
    const float scale = jump != 0.0f || change != 0.0f ? HeightScale(jump, change) : 1.0f;
    if (jump != g_loggedJump || change != g_loggedChange) {
        g_loggedJump = jump;
        g_loggedChange = change;
        Log("jump: Jump %.0f, Acrobatics %+.0f -> height x%.2f", jump, change, scale);
    }
    if (scale == g_written) return;
    g_written = scale;
    *g_setting = g_vanilla * scale;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
