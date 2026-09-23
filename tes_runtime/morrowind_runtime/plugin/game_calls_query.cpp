// What the world IS: the one-native queries, the kill count and the clock.
// See: docs/commentary/morrowind_runtime.md#game-calls

#include "game_calls_internal.h"

#include <map>
#include <string>

#include "ids.h"
#include "log.h"
#include "main_thread.h"

namespace mwruntime {
namespace gamecalls {

namespace {

using RefQueryFn = bool (*)(void* vm, std::uint32_t stack, void* ref);
// The one-call queries. `HasLOS`, `IsDetectedBy` and `GetCombatTarget` all
// relate two actors; the rest ask about one.
// See: docs/commentary/morrowind_runtime.md#the-query-commands
using PairQueryFn = bool (*)(void* vm, std::uint32_t stack, void* actor,
                             void* other);
using CombatTargetFn = void* (*)(void* vm, std::uint32_t stack, void* actor);
using ResurrectFn = void (*)(void* vm, std::uint32_t stack, void* actor);
// ObjectReference.DropObject(Form item, int count) -> the dropped reference.
using DropObjectFn = void* (*)(void* vm, std::uint32_t stack, void* ref,
                               void* item, std::int32_t count);
// ActorBase.GetDeadCount() -> how many of that base have died.
using DeadCountFn = std::int32_t (*)(void* vm, std::uint32_t stack,
                                     void* base);
// Weather.GetCurrentWeather() is GLOBAL, so its self is a tag; the
// classification is then read off the returned Weather form.
using CurrentWeatherFn = void* (*)(void* vm, std::uint32_t stack, void* tag);
using ClassificationFn = std::int32_t (*)(void* vm, std::uint32_t stack,
                                          void* weather);

PairQueryFn      g_hasLos = nullptr;
PairQueryFn      g_isDetectedBy = nullptr;
CombatTargetFn   g_combatTarget = nullptr;
ResurrectFn      g_resurrect = nullptr;
DropObjectFn     g_dropObject = nullptr;
CurrentWeatherFn g_currentWeather = nullptr;
DeadCountFn      g_deadCount = nullptr;
ClassificationFn g_classification = nullptr;
RefQueryFn       g_weaponDrawn = nullptr;
RefQueryFn       g_isSneaking = nullptr;
RefQueryFn       g_actorRunning = nullptr;
// Actor.IsEquipped(Form), Actor.GetSleepState(), Actor.GetEquippedSpell(int).
using FormQueryFn = bool (*)(void* vm, std::uint32_t stack, void* ref,
                             void* form);
using StateFn = std::int32_t (*)(void* vm, std::uint32_t stack, void* ref);
using HandQueryFn = void* (*)(void* vm, std::uint32_t stack, void* ref,
                              std::int32_t hand);
FormQueryFn      g_isEquipped = nullptr;
StateFn          g_sleepState = nullptr;
HandQueryFn      g_equippedSpell = nullptr;

// `GetDeadCount id`: the ENGINE's own count for that base actor, which covers
// every death, scripted or not, and is saved with the game.
int DeadCountOf(const std::string& actor) {
    void* base = Form(FindBase(actor));
    if (!base) ReportOnce("base", actor);
    return base && g_deadCount ? g_deadCount(PapyrusVm(), 0, base) : 0;
}

// TES3's clock globals and the Skyrim.esm GLOB each one reads.
//
// 🛑 Month is 0-based and Day 1-based in BOTH games, so they copy across. The
// YEAR is Skyrim's own (4E 201), not Morrowind's 3E 427.
constexpr struct {
    const char* tes3;
    std::uint32_t skyrim;
} kClock[] = {{"gamehour", 0x38}, {"day", 0x37},        {"month", 0x36},
              {"year", 0x35},     {"dayspassed", 0x39}, {"timescale", 0x3A}};

// TES3 globals a converted GLOB mirrors, read back each tick so a value
// PAPYRUS writes reaches the scripts polling it. `CharGenState` is the case
// that needs it: vanilla Morrowind's opening is started by setting it to 1,
// which the TES3 engine did on a new game and TESGameSelect does instead.
//
// 🛑 Only globals a script READS as an input belong here. A write-back would
// fight the object scripts, which own every other global's value.
// See: docs/commentary/morrowind_runtime.md#vanilla-morrowind-chargen
constexpr const char* kMirrored[] = {"chargenstate"};

// The value each mirrored GLOB held when last read.
std::map<std::string, float> g_mirrorSeen;

void SyncMirroredGlobals() {
    for (const char* name : kMirrored) {
        const GlobalDef* def = FindGlobal(name);
        if (!def || def->form.plugin.empty()) continue;
        const auto* form = static_cast<const std::uint8_t*>(
            FormFromFile(def->form.plugin.c_str(),
                         def->form.formId & 0x00FFFFFF));
        if (!form) continue;
        // 🛑 Only a CHANGE of the GLOB crosses over, and the first sight is
        // never one. The scripts own the value from then on (`CharGen` sets
        // it to 10) and the co-save owns it after a load, so copying a
        // standing 1 would relaunch `CharGen` every tick and on every load.
        const float value =
            *reinterpret_cast<const float*>(form + ids::kOffGlobalValue);
        const auto [seen, first] = g_mirrorSeen.try_emplace(name, value);
        if (first || seen->second == value) continue;
        seen->second = value;
        Log("global: %s mirrored from its GLOB = %g", name, value);
        State().SetGlobal(name, value);
    }
}

// A Skyrim.esm GLOB never unloads, so each form is looked up once.
void SyncClock() {
    static const std::uint8_t* forms[6] = {};
    for (int i = 0; i < 6; ++i) {
        if (!forms[i]) {
            forms[i] = static_cast<const std::uint8_t*>(
                FormFromFile(ids::kSkyrimMaster, kClock[i].skyrim));
        }
        if (!forms[i]) continue;
        State().SyncGlobal(kClock[i].tes3,
                           *reinterpret_cast<const float*>(
                               forms[i] + ids::kOffGlobalValue));
    }
    SyncMirroredGlobals();
    // After the mirror, so a value Papyrus just wrote reaches the state first.
    PublishState();
}

// `advanceHours`: moves GameHour on, which is how Skyrim's own wait does
// it -- the engine rolls a value past 24 into the day, month and year.
void AdvanceHours(int hours) {
    PostToMainThread([hours]() {
        auto* hour = static_cast<std::uint8_t*>(
            FormFromFile(ids::kSkyrimMaster, kClock[0].skyrim));
        if (!hour) return;
        *reinterpret_cast<float*>(hour + ids::kOffGlobalValue) +=
            static_cast<float>(hours);
    });
}

// The one-call queries. Each resolves both references and asks one native.
// See: docs/commentary/morrowind_runtime.md#the-query-commands
bool AskPair(PairQueryFn fn, const std::string& actor,
             const std::string& other) {
    void* a = OwnerRef(actor);
    void* b = OwnerRef(other);
    return a && b && fn && fn(PapyrusVm(), 0, a, b);
}

bool HasLosOn(const std::string& actor, const std::string& other) {
    return AskPair(g_hasLos, actor, other);
}

// 🛑 The arguments SWAP. `x->GetDetected y` asks whether y is detected by x
// (OpenMW's `isActorDetected(actor, observer)`, where the observer is the
// command's target and the actor is its string argument), while Skyrim's
// `self.IsDetectedBy(other)` asks whether SELF is detected by other. So the
// named actor is self and the speaker is the observer.
bool DetectsActor(const std::string& actor, const std::string& other) {
    return AskPair(g_isDetectedBy, other, actor);
}

// `GetTarget "id"` is a COMPARISON, not a lookup: is the actor's combat
// target that particular reference.
bool FightingActor(const std::string& actor, const std::string& other) {
    void* a = OwnerRef(actor);
    void* b = OwnerRef(other);
    if (!a || !b || !g_combatTarget) return false;
    return g_combatTarget(PapyrusVm(), 0, a) == b;
}

bool AskActor(RefQueryFn fn, const std::string& actor) {
    void* ref = OwnerRef(actor);
    return ref && fn && fn(PapyrusVm(), 0, ref);
}

bool WeaponIsDrawn(const std::string& actor) {
    return AskActor(g_weaponDrawn, actor);
}

bool ActorSneaking(const std::string& actor) {
    return AskActor(g_isSneaking, actor);
}

bool ActorRunning(const std::string& actor) {
    return AskActor(g_actorRunning, actor);
}

// `HasItemEquipped "id"`: IsEquipped takes the FORM, so the id resolves
// through the same item table AddItem and RemoveItem use.
bool ItemIsEquipped(const std::string& actor, const std::string& item) {
    void* ref = OwnerRef(actor);
    void* form = ItemForm(item);
    return ref && form && g_isEquipped &&
           g_isEquipped(PapyrusVm(), 0, ref, form);
}

// `GetPCSleep`: GetSleepState is an ENUM -- 0 awake, 2 about to sleep,
// 3 asleep, 4 waking. TES3 asks one yes/no question, which is state 3.
constexpr std::int32_t kSleepStateAsleep = 3;

bool PlayerIsSleeping() {
    void* ref = PlayerRef();
    return ref && g_sleepState &&
           g_sleepState(PapyrusVm(), 0, ref) == kSleepStateAsleep;
}

// `GetSpellReadied`: TES3's third draw state. Skyrim has no drawstate
// getter, but a readied spell IS an equipped spell, so the question is
// whether either hand holds one.
constexpr std::int32_t kHandLeft = 0;
constexpr std::int32_t kHandRight = 1;

bool SpellIsReadied(const std::string& actor) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_equippedSpell) return false;
    return g_equippedSpell(PapyrusVm(), 0, ref, kHandRight) ||
           g_equippedSpell(PapyrusVm(), 0, ref, kHandLeft);
}

// `ForceSneak`/`ClearForceSneak`. TES3 reads the stance as
// `Flag_Sneak || Flag_ForceSneak` (OpenMW `CreatureStats::getStance`), so the
// latch forces the stance ON TOP of whatever the AI is doing.
//
// 🛑 This is a FLAG WRITE, not a native call. `Actor.StartSneaking` compares
// its target against the player singleton and only acts for the player, so it
// did nothing for an NPC however it was called. The console's `SetForceSneak`
// is the real mechanism and it just sets one bit on the actor.
// See: docs/commentary/morrowind_runtime.md#forced-movement-is-a-latch
//
// 🛑 `which` is compared against the SHARED `MovementFlag` enum, never a
// number copied here: a local `kFlagForceSneak = 1` outlived the enum being
// cut to sneak-only at 0, so every call returned early and nobody sneaked.
void ApplyMovementFlag(const std::string& actor, int which, bool on) {
    if (which != kForceSneak) return;
    void* ref = OwnerRef(actor);
    if (!ref) {
        Log("sneak: %s has no loaded reference", actor.c_str());
        return;
    }
    PostToMainThread([ref, on]() {
        auto* flags = reinterpret_cast<std::uint32_t*>(
            static_cast<std::uint8_t*>(ref) + ids::kOffActorMoveFlags);
        *flags = on ? (*flags | ids::kActorFlagForceSneak)
                    : (*flags & ~ids::kActorFlagForceSneak);
        Log("sneak: %s -> %08X", on ? "on" : "off", *flags);
    });
}

void ResurrectActor(const std::string& actor) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_resurrect) return;
    PostToMainThread([ref]() { g_resurrect(PapyrusVm(), 0, ref); });
}

void DropFromActor(const std::string& actor, const std::string& item,
                   int count) {
    void* ref = OwnerRef(actor);
    void* form = ItemForm(item);
    if (!ref || !form || !g_dropObject) return;
    PostToMainThread([ref, form, count]() {
        g_dropObject(PapyrusVm(), 0, ref, form, count);
    });
}

// `GetCurrentWeather`: the global native hands back a Weather form, whose
// classification is then read off it. The TES3 mapping is the opcode's.
int WeatherClassification() {
    if (!g_currentWeather || !g_classification) return -1;
    void* weather = g_currentWeather(PapyrusVm(), 0, nullptr);
    return weather ? g_classification(PapyrusVm(), 0, weather) : -1;
}

}  // namespace

void InstallQueryCalls(GameHooks& hooks) {
    g_hasLos = Native<PairQueryFn>("Actor.HasLOS", ids::kActorHasLos);
    g_isDetectedBy = Native<PairQueryFn>("Actor.IsDetectedBy",
                                         ids::kActorIsDetectedBy);
    g_combatTarget = Native<CombatTargetFn>("Actor.GetCombatTarget",
                                            ids::kActorCombatTarget);
    g_weaponDrawn = Native<RefQueryFn>("Actor.IsWeaponDrawn",
                                       ids::kActorWeaponDrawn);
    g_isSneaking = Native<RefQueryFn>("Actor.IsSneaking",
                                      ids::kActorIsSneaking);
    g_actorRunning = Native<RefQueryFn>("Actor.IsRunning",
                                        ids::kActorIsRunning);
    g_resurrect = Native<ResurrectFn>("Actor.Resurrect", ids::kActorResurrect);
    g_dropObject = Native<DropObjectFn>("ObjectReference.DropObject",
                                        ids::kRefDropObject);
    g_deadCount = Native<DeadCountFn>("ActorBase.GetDeadCount",
                                      ids::kActorBaseDeadCount);
    g_currentWeather = Native<CurrentWeatherFn>("Weather.GetCurrentWeather",
                                                ids::kWeatherCurrent);
    g_classification = Native<ClassificationFn>("Weather.GetClassification",
                                                ids::kWeatherClassification);
    hooks.hasLos = HasLosOn;
    hooks.detects = DetectsActor;
    hooks.fighting = FightingActor;
    g_isEquipped = Native<FormQueryFn>("Actor.IsEquipped",
                                       ids::kActorIsEquipped);
    g_sleepState = Native<StateFn>("Actor.GetSleepState",
                                   ids::kActorGetSleepState);
    g_equippedSpell = Native<HandQueryFn>("Actor.GetEquippedSpell",
                                          ids::kActorEquippedSpell);
    hooks.weaponDrawn = WeaponIsDrawn;
    hooks.sneaking = ActorSneaking;
    hooks.running = ActorRunning;
    hooks.itemEquipped = ItemIsEquipped;
    hooks.playerSleeping = PlayerIsSleeping;
    hooks.spellReadied = SpellIsReadied;
    hooks.applyMovementFlag = ApplyMovementFlag;
    hooks.resurrect = ResurrectActor;
    hooks.dropItem = DropFromActor;
    hooks.weather = WeatherClassification;
    hooks.deadCount = DeadCountOf;
    hooks.syncClock = SyncClock;
    hooks.advanceHours = AdvanceHours;
}

}  // namespace gamecalls
}  // namespace mwruntime
