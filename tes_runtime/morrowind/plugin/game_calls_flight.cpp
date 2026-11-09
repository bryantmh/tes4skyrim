// SwiftSwim, Levitate and SlowFall: TES3 effects Skyrim's physics has no switch for.
// Each converts as a script-less Script effect, so the engine's own
// active-effect list holds it, magnitude and all, for exactly as long as it
// lasts -- no start or end signal is needed, and no vanilla actor value is
// borrowed. The tick sums each one's magnitude for the player; a hook run
// after the InAir and OnGround states' simulate rewrites the player's
// character-controller velocity the way OpenMW's movement solver moves a
// flying or slow-falling actor, and the Swimming state's simulate strokes
// faster. The same tick drives Sanctuary's ranks.
// See: docs/commentary/morrowind_runtime.md#levitate-and-slowfall

#include "game_calls_internal.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <functional>
#include <string>

#include "actor_stats.h"
#include "engine_ids.h"
#include "ids.h"
#include "log.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

constexpr const char* kPlayerId = "player";

// TES3 attribute index of Speed, which Levitate's speed adds to.
constexpr int kSpeedAttribute = 4;

// OpenMW damps a slow fall once per physics step, at this fixed rate.
constexpr float kOpenMwStepsPerSecond = 60.0f;

// OpenMW: each point of SlowFall keeps 0.5% less of the speed per step.
constexpr float kSlowFallPerPoint = 0.005f;

// OpenMW: each point of SwiftSwim adds 1% to the swim speed.
constexpr float kSwiftSwimPerPoint = 0.01f;

// Vec4 is an hkVector4: x, y, z and an unused w, in havok units.
constexpr std::size_t kVec4Bytes = 16;

// Whatever the original leaves in rax is handed back unchanged: InAir's ends
// on a call whose result it returns, and no caller was read to rule it out.
using SimulateFn = std::uintptr_t (*)(void* state, void* controller);
using ControllerFn = void* (*)(void* actor);
using PositionFn = void (*)(void* controller, float* out, bool applyCenterOffset);
using EffectListFn = void** (*)(void* magicTarget);

SimulateFn g_inAir = nullptr;
SimulateFn g_onGround = nullptr;
SimulateFn g_swimming = nullptr;
ControllerFn g_controllerOf = nullptr;
void** g_playerControls = nullptr;

// Written by the tick on the game thread, read by the hook on whichever
// thread steps the physics.
std::atomic<void*> g_player{nullptr};
std::atomic<void*> g_playerController{nullptr};
std::atomic<bool> g_levitating{false};
std::atomic<float> g_flySpeed{0.0f};
std::atomic<float> g_slowFall{0.0f};
std::atomic<float> g_swiftSwim{0.0f};
// Set when the SwiftSwim read changes, so the next moving boosted stroke is logged.
std::atomic<bool> g_swimReport{false};
// Likewise for the first swim step on a controller that is not the player's.
std::atomic<bool> g_swimOtherReport{false};

// Whether the refusal has been shown for the Levitate now active.
bool g_refusalShown = false;

// OpenMW's Npc::getMaxSpeed while levitating, in game units a second.
float FlySpeed(float magnitude) {
    const float carried = Hooks().actorValue(kPlayerId, "InventoryWeight");
    const float capacity = Hooks().actorValue(kPlayerId, "CarryWeight");
    const float load = capacity > 0.0f ? carried / capacity : 0.0f;
    if (load > 1.0f) return 0.0f;
    const float slowest = GmstNumber("fMinFlySpeed", 5.0f);
    const float fastest = GmstNumber("fMaxFlySpeed", 300.0f);
    const float speed = ActorAttribute(kPlayerId, kSpeedAttribute) + magnitude;
    const float flying = slowest + 0.01f * speed * (fastest - slowest);
    const float burdened = 1.0f - GmstNumber("fEncumberedMoveEffect", 0.3f) * load;
    return std::max(0.0f, flying * burdened);
}

void FlightTick(void* player) {
    g_player = player;
    g_playerController = CharController(player);
    if (!player) return;
    const float swiftSwim = ActiveMagnitude(player, kSwiftSwimEffect);
    if (swiftSwim != g_swiftSwim.exchange(swiftSwim)) {
        Log("flight: SwiftSwim now %.0f", swiftSwim);
        g_swimReport = swiftSwim > 0.0f;
        g_swimOtherReport = swiftSwim > 0.0f;
    }
    const float levitate = ActiveMagnitude(player, kLevitateEffect);
    // OpenMW drops a Levitate the moment levitation is disabled, and says so.
    const bool refused = levitate > 0.0f && !State().levitation;
    if (refused && !g_refusalShown) {
        Notify(GmstText("sLevitateDisabled", std::string()));
    }
    g_refusalShown = refused;
    g_levitating = levitate > 0.0f && !refused;
    g_flySpeed = g_levitating ? FlySpeed(levitate) : 0.0f;
    g_slowFall = ActiveMagnitude(player, kSlowFallEffect);
}

void EffectTick() {
    void* player = PlayerRef();
    FlightTick(player);
    TickStillActors(player);
    TickSanctuary(player);
    TickAttributeEffects(player);
    TickPlayerFollowers();
}

// Where the keys and the look point, at the fly speed, in havok units: OpenMW
// flies the way it swims, forward along the pitch and strafing level.
bool FlyVelocity(float out[4]) {
    void* controls = PlayerControls();
    void* player = g_player.load();
    if (!controls || !player) return false;
    const float strafe = At<float>(controls, ids::kOffPlayerControlsMoveX);
    const float forward = At<float>(controls, ids::kOffPlayerControlsMoveY);
    const float pitch = At<float>(player, tesruntime::ids::kOffRefRotX);
    const float yaw = At<float>(player, tesruntime::ids::kOffRefRotZ);
    float dir[3] = {
        std::sin(yaw) * std::cos(pitch) * forward + std::cos(yaw) * strafe,
        std::cos(yaw) * std::cos(pitch) * forward - std::sin(yaw) * strafe,
        -std::sin(pitch) * forward};
    const float length = std::sqrt(dir[0] * dir[0] + dir[1] * dir[1] + dir[2] * dir[2]);
    const float scale = g_flySpeed.load() / ids::kHavokToGame / std::max(1.0f, length);
    for (int i = 0; i < 3; ++i) out[i] = dir[i] * scale;
    out[3] = 0.0f;
    return true;
}

// OpenMW lands a flying or slow-falling actor every step, so the fall that
// hurts starts where the effect ends, never where the flight began.
void ResetFallStart(void* controller) {
    alignas(16) float at[4] = {};
    VCall<PositionFn>(controller, ids::kControllerGetPositionSlot)(controller, at, true);
    At<float>(controller, ids::kOffControllerFallStart) = at[2] * ids::kHavokToGame;
}

// OpenMW's movement solver: past gravity, a falling speed keeps only
// (1 - 0.005 * magnitude) of itself per step, and so does the drift.
void DampFall(void* controller, float magnitude) {
    float velocity[4];
    std::memcpy(velocity, &At<char>(controller, ids::kOffControllerVelocity), kVec4Bytes);
    const float steps =
        At<float>(controller, ids::kOffControllerStepSeconds) * kOpenMwStepsPerSecond;
    const float keep = std::pow(
        1.0f - std::clamp(magnitude * kSlowFallPerPoint, 0.0f, 1.0f), steps);
    if (velocity[2] < 0.0f) velocity[2] *= keep;
    velocity[0] *= keep;
    velocity[1] *= keep;
    std::memcpy(&At<char>(controller, ids::kOffControllerVelocity), velocity, kVec4Bytes);
    ResetFallStart(controller);
}

// A levitating player on the ground only leaves it by flying upward; level
// flight there is left to the ground state's own walk.
void Steer(void* controller, bool grounded) {
    if (!controller || controller != g_playerController.load()) return;
    if (g_levitating.load()) {
        float velocity[4];
        if (!FlyVelocity(velocity) || (grounded && velocity[2] <= 0.0f)) return;
        std::memcpy(&At<char>(controller, ids::kOffControllerVelocity), velocity, kVec4Bytes);
        ResetFallStart(controller);
        return;
    }
    const float slowFall = g_slowFall.load();
    if (!grounded && slowFall > 0.0f) DampFall(controller, slowFall);
}

std::uintptr_t InAirHook(void* state, void* controller) {
    const std::uintptr_t result = g_inAir(state, controller);
    Steer(controller, false);
    HoldStill(controller, controller == g_playerController.load());
    return result;
}

std::uintptr_t OnGroundHook(void* state, void* controller) {
    const std::uintptr_t result = g_onGround(state, controller);
    Steer(controller, true);
    HoldStill(controller, controller == g_playerController.load());
    return result;
}

// OpenMW's getSwimSpeed: the stroke, not the buoyancy, grows by 1% a point.
// The stroke is scaled for the original's read and put back, so it never
// compounds whether or not the engine rewrites it next step.
std::uintptr_t SwimmingHook(void* state, void* controller) {
    const float swiftSwim = g_swiftSwim.load();
    if (swiftSwim <= 0.0f) return g_swimming(state, controller);
    if (controller != g_playerController.load()) {
        if (g_swimOtherReport.exchange(false)) {
            Log("flight: swim step on controller %p, the player's is %p", controller,
                g_playerController.load());
        }
        return g_swimming(state, controller);
    }
    float* stroke = &At<float>(controller, ids::kOffControllerStroke);
    float saved[4];
    std::memcpy(saved, stroke, kVec4Bytes);
    const float scale = 1.0f + kSwiftSwimPerPoint * swiftSwim;
    for (int i = 0; i < 3; ++i) stroke[i] *= scale;
    const std::uintptr_t result = g_swimming(state, controller);
    std::memcpy(stroke, saved, kVec4Bytes);
    const bool stroking = saved[0] != 0.0f || saved[1] != 0.0f || saved[2] != 0.0f;
    if (stroking && g_swimReport.exchange(false)) {
        const float* velocity = &At<float>(controller, ids::kOffControllerVelocity);
        Log("flight: swim stroke (%.3f, %.3f, %.3f) x%.2f -> velocity (%.3f, %.3f, %.3f)",
            saved[0], saved[1], saved[2], scale, velocity[0], velocity[1], velocity[2]);
    }
    return result;
}

SimulateFn SwapSimulate(const char* what, std::uint64_t vtableId,
                        std::uint64_t simulateId, SimulateFn hook) {
    return reinterpret_cast<SimulateFn>(SwapVtableSlot(
        what, Resolve(what, vtableId, nullptr), ids::kStateSimulateSlot,
        Resolve(what, simulateId, nullptr), reinterpret_cast<void*>(hook)));
}

}  // namespace

void* CharController(void* actor) {
    return actor && g_controllerOf ? g_controllerOf(actor) : nullptr;
}

void* PlayerControls() { return g_playerControls ? *g_playerControls : nullptr; }

// Every active instance of a runtime-carried effect, in any of its delivery
// copies. An effect whose conditions switched it off is skipped.
void ForEachActiveEffect(void* actor,
                         const std::function<void(const RuntimeEffect&, float)>& fn) {
    if (!actor) return;
    void* target = &At<char>(actor, ActorField(ids::kOffActorMagicTarget));
    auto* node = VCall<EffectListFn>(target, ids::kActiveEffectListSlot)(target);
    for (; node; node = static_cast<void**>(node[1])) {
        void* effect = node[0];
        void* item = effect ? At<void*>(effect, ids::kOffActiveEffectItem) : nullptr;
        void* base = item ? At<void*>(item, ids::kOffEffectItemBase) : nullptr;
        const RuntimeEffect row = base ? RuntimeEffectOf(FormIdOf(base)) : RuntimeEffect{};
        if (row.index < 0 ||
            (At<std::uint32_t>(effect, ids::kOffActiveEffectFlags) & ids::kActiveEffectInactive)) {
            continue;
        }
        fn(row, At<float>(effect, ids::kOffActiveEffectMagnitude));
    }
}

// Whether a live active effect on `actor` came from a spell `match` accepts,
// by its FormID: the walk the engine's own GetDisease makes.
bool AnyEffectSource(void* actor, const std::function<bool(std::uint32_t)>& match) {
    if (!actor) return false;
    void* target = &At<char>(actor, ActorField(ids::kOffActorMagicTarget));
    auto* node = VCall<EffectListFn>(target, ids::kActiveEffectListSlot)(target);
    for (; node; node = static_cast<void**>(node[1])) {
        void* effect = node[0];
        void* source = effect ? At<void*>(effect, ids::kOffActiveEffectSource) : nullptr;
        if (!source || (At<std::uint32_t>(effect, ids::kOffActiveEffectFlags) &
                        ids::kActiveEffectDispelled)) {
            continue;
        }
        if (match(FormIdOf(source))) return true;
    }
    return false;
}

// OpenMW's MagicEffects::getOrDefault: every active instance of the effect
// adds its magnitude.
float ActiveMagnitude(void* actor, int tes3Index, int attribute) {
    float total = 0.0f;
    ForEachActiveEffect(actor, [&](const RuntimeEffect& row, float magnitude) {
        if (row.index == tes3Index && (attribute < 0 || row.attribute == attribute)) {
            total += magnitude;
        }
    });
    return total;
}

void InstallFlightCalls(GameHooks& hooks) {
    if (hooks.effectTick) return;
    hooks.effectTick = EffectTick;
    g_controllerOf = Native<ControllerFn>("Actor::GetCharController",
                                          ids::kActorGetCharController);
    g_playerControls = reinterpret_cast<void**>(Resolve(
        "PlayerControls singleton", ids::kPlayerControlsSingleton, nullptr));
    if (!g_controllerOf || !g_playerControls) {
        Log("flight: controller or controls did not resolve -- Levitate and "
            "SlowFall stay inert");
        return;
    }
    g_inAir = SwapSimulate("bhkCharacterStateInAir::Simulate",
                           ids::kInAirStateVtable, ids::kInAirSimulate, InAirHook);
    g_onGround = SwapSimulate("bhkCharacterStateOnGround::Simulate",
                              ids::kOnGroundStateVtable, ids::kOnGroundSimulate,
                              OnGroundHook);
    g_swimming = SwapSimulate("bhkCharacterStateSwimming::Simulate",
                              ids::kSwimmingStateVtable, ids::kSwimmingSimulate,
                              SwimmingHook);
    Log("flight: InAir %s, OnGround %s, Swimming %s", g_inAir ? "hooked" : "NOT hooked",
        g_onGround ? "hooked" : "NOT hooked", g_swimming ? "hooked" : "NOT hooked");
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
