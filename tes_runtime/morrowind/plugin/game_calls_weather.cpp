// ChangeWeather and ModRegion, with OpenMW's semantics (mwworld/weather.cpp).
//
// Skyrim rolls a region's weather itself, from the REGN the import wrote. What
// the scripts change is kept here, in the co-save. ChangeWeather sets a
// region's weather; it shows now if the player is in that region, or when the
// player next enters it. ModRegion replaces a region's chances for good, so
// while the player is in such a region its weather is rolled from them here
// and held with an override, since the engine would roll the REGN's own.
// Every set weather expires on OpenMW's clock, every 20 game hours, and a
// region with chances then rolls again.
// See: docs/commentary/morrowind_runtime.md#region-weather

#include <cstdint>
#include <random>
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

using SetActiveFn = void (*)(void* vm, std::uint32_t stack, void* weather,
                             bool override, bool accelerate);
using ForceActiveFn = void (*)(void* vm, std::uint32_t stack, void* weather, bool override);
using ReleaseOverrideFn = void (*)(void* vm, std::uint32_t stack, void* tag);
// Weather.GetCurrentWeather() is global, so its self is a tag.
using CurrentWeatherFn = void* (*)(void* vm, std::uint32_t stack, void* tag);
SetActiveFn g_setActive = nullptr;
ForceActiveFn g_forceActive = nullptr;
ReleaseOverrideFn g_releaseOverride = nullptr;
CurrentWeatherFn g_currentWeather = nullptr;

// Morrowind's ten hardcoded weather types.
constexpr int kTypes = 10;
// No weather set.
constexpr float kNone = -1.0f;
// The co-save owners: a region's chances by `region|type`, its set weather by
// region, and the game hour the set weathers next expire, by `next`.
constexpr const char* kChances = "weather|chances";
constexpr const char* kSet = "weather|set";
constexpr const char* kClock = "weather|clock";
// OpenMW's Weather_Hours_Between_Weather_Changes (openmw.cfg), Morrowind.ini's.
constexpr float kHoursBetweenChanges = 20.0f;
// Skyrim.esm's GameDaysPassed GLOB.
constexpr std::uint32_t kGameDaysPassed = 0x00000039;
// Ticks between two looks at the player's region.
constexpr int kWeatherTicks = 10;

// The region the sky was last brought to, and the type applied there; this
// session's alone, so a load applies again.
std::string g_region;
int g_applied = -1;
bool g_sampled = false;

void* WeatherForm(int type) {
    const FormRef* ref = EffectForm("weather" + std::to_string(type));
    return ref ? Form(ref) : nullptr;
}

int CurrentType() {
    void* now = g_currentWeather ? g_currentWeather(PapyrusVm(), 0, nullptr) : nullptr;
    for (int type = 0; now && type < kTypes; ++type) {
        if (WeatherForm(type) == now) return type;
    }
    return -1;
}

bool HasChances(const std::string& region) {
    return !region.empty() && State().HasVar(kChances, region + "|0");
}

int Chance(const std::string& region, int type) {
    return static_cast<int>(State().Var(kChances, region + "|" + std::to_string(type)));
}

int SetWeather(const std::string& region) {
    return State().HasVar(kSet, region) ? static_cast<int>(State().Var(kSet, region)) : -1;
}

// OpenMW's RegionWeather::chooseNewWeather: a roll of 1..100 against the
// running sum of the chances, Clear when they do not reach it.
int Roll(const std::string& region) {
    static std::mt19937 engine{std::random_device{}()};
    const int roll = std::uniform_int_distribution<int>(1, 100)(engine);
    int sum = 0;
    for (int type = 0; type < kTypes; ++type) {
        sum += Chance(region, type);
        if (roll <= sum) return type;
    }
    return 0;
}

// OpenMW's updateWeatherTime: every set weather expires together.
void ExpireOnClock() {
    const float* days = GlobalSlot(ids::kSkyrimMaster, kGameDaysPassed);
    if (!days) return;
    const float hours = *days * 24.0f;
    if (!State().HasVar(kClock, "next")) {
        State().SetVar(kClock, "next", hours + kHoursBetweenChanges);
        return;
    }
    if (hours < State().Var(kClock, "next")) return;
    for (const std::string& region : State().VarNames(kSet)) State().SetVar(kSet, region, kNone);
    State().SetVar(kClock, "next", hours + kHoursBetweenChanges);
    Log("weather: the set weathers expire");
}

// A forced weather shows at once, as OpenMW's forceWeather; otherwise the sky
// transitions to it once any transition under way ends.
void Apply(int type, bool hold, bool force) {
    void* weather = WeatherForm(type);
    if (!weather || !g_setActive || !g_forceActive) {
        Log("weather: no weather form for type %d", type);
        return;
    }
    if (force) {
        g_forceActive(PapyrusVm(), 0, weather, hold);
    } else {
        g_setActive(PapyrusVm(), 0, weather, hold, false);
    }
    Log("weather: type %d %s%s", type, force ? "forced" : "now", hold ? ", held" : "");
}

// Game thread: the player's region and what is set for it, onto the sky. A
// held weather is released on the way out of its region, and once after a
// load, which may have carried one out with it. Arriving in a region forces
// its weather (OpenMW's playerTeleported), as does a load that undid it.
void UpdateWeather() {
    ExpireOnClock();
    const std::string region = PlayerRegion();
    const bool arrived = !g_sampled || region != g_region;
    if (arrived) {
        const bool held = !g_sampled || HasChances(g_region);
        if (held && !HasChances(region) && g_releaseOverride) {
            g_releaseOverride(PapyrusVm(), 0, nullptr);
        }
        g_region = region;
        g_applied = -1;
        g_sampled = true;
    }
    if (region.empty()) return;
    const bool hold = HasChances(region);
    int want = SetWeather(region);
    if (hold && want < 0) {
        want = Roll(region);
        State().SetVar(kSet, region, static_cast<float>(want));
    }
    if (want < 0 || (want == g_applied && CurrentType() == want)) return;
    Apply(want, hold, arrived || want == g_applied);
    g_applied = want;
}

void ChangeWeather(const std::string& region, int type) {
    if (type < 0 || type >= kTypes) return;
    const std::string key = Lower(region);
    Log("weather: ChangeWeather %s %d", key.c_str(), type);
    PostToMainThread([key, type]() {
        State().SetVar(kSet, key, static_cast<float>(type));
        if (key == g_region) g_applied = -1;
        UpdateWeather();
    });
}

// A region whose current weather its new chances still allow keeps it.
void ModRegion(const std::string& region, const std::vector<int>& chances) {
    const std::string key = Lower(region);
    Log("weather: ModRegion %s, %zu chance(s)", key.c_str(), chances.size());
    PostToMainThread([key, chances]() {
        for (int type = 0; type < kTypes; ++type) {
            const std::size_t at = static_cast<std::size_t>(type);
            State().SetVar(kChances, key + "|" + std::to_string(type),
                           at < chances.size() ? static_cast<float>(chances[at]) : 0.0f);
        }
        if (SetWeather(key) < 0 && key == PlayerRegion()) {
            State().SetVar(kSet, key, static_cast<float>(CurrentType()));
        }
        const int set = SetWeather(key);
        if (set >= 0 && Chance(key, set) == 0) State().SetVar(kSet, key, kNone);
        if (key == g_region) g_applied = -1;
        UpdateWeather();
    });
}

void WeatherTick() {
    static int untilLook = 0;
    if (--untilLook > 0) return;
    untilLook = kWeatherTicks;
    PostToMainThread(UpdateWeather);
}

void ResetWeather() {
    g_region.clear();
    g_applied = -1;
    g_sampled = false;
}

}  // namespace

void InstallWeatherCalls(GameHooks& hooks) {
    g_setActive = Native<SetActiveFn>("Weather.SetActive", ids::kWeatherSetActive);
    g_forceActive = Native<ForceActiveFn>("Weather.ForceActive", ids::kWeatherForceActive);
    g_releaseOverride = Native<ReleaseOverrideFn>("Weather.ReleaseOverride",
                                                  ids::kWeatherReleaseOverride);
    g_currentWeather = Native<CurrentWeatherFn>("Weather.GetCurrentWeather",
                                                ids::kWeatherCurrent);
    hooks.weatherType = CurrentType;
    hooks.changeWeather = ChangeWeather;
    hooks.modRegion = ModRegion;
    hooks.weatherTick = WeatherTick;
    hooks.weatherReset = ResetWeather;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
