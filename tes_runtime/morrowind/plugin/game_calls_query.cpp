// What the world IS: the one-native queries, the kill count and the clock.
// See: docs/commentary/morrowind_runtime.md#game-calls

#include "game_calls_internal.h"

#include <algorithm>
#include <cstring>
#include <map>
#include <set>
#include <string>

#include "activation.h"
#include "addresses.h"
#include "ids.h"
#include "log.h"
#include "main_thread.h"
#include "object_script.h"
#include "object_tick.h"

namespace tesruntime::mw {
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
// Actor.IsEquipped(Form), Actor.GetEquippedSpell(int).
using FormQueryFn = bool (*)(void* vm, std::uint32_t stack, void* ref,
                             void* form);
using HandQueryFn = void* (*)(void* vm, std::uint32_t stack, void* ref,
                              std::int32_t hand);
FormQueryFn      g_isEquipped = nullptr;
HandQueryFn      g_equippedSpell = nullptr;
// Actor.GetEquippedWeapon(bool abLeftHand).
using EquippedWeaponFn = void* (*)(void* vm, std::uint32_t stack, void* actor,
                                   bool left);
EquippedWeaponFn g_equippedWeapon = nullptr;
ProcessEventFn   g_sleepStart = nullptr;
ProcessEventFn   g_sleepStop = nullptr;
ProcessEventFn   g_hit = nullptr;
ProcessEventFn   g_death = nullptr;
ProcessEventFn   g_container = nullptr;

// The weapon or spell each actor was last hit with, both by runtime FormID:
// OpenMW's `CreatureStats::mLastHitObject`, which `HitOnMe` tests and clears.
std::map<std::uint32_t, std::uint32_t> g_lastHit;

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

// The value each mirrored GLOB held when last read, by the GLOB's own form.
std::map<std::string, float> g_mirrorSeen;

// One sidecar's GLOB for a mirrored global, written back through that
// sidecar's view, so each game's own `CharGenState` starts only that game.
void SyncMirror(const char* name, const GlobalDef& def, int layer) {
    if (def.form.plugin.empty()) return;
    const std::uint32_t local = def.form.formId & 0x00FFFFFF;
    const auto* form = static_cast<const std::uint8_t*>(
        FormFromFile(def.form.plugin.c_str(), local));
    if (!form) return;
    // 🛑 Only a CHANGE of the GLOB crosses over, and the first sight is
    // never one. The scripts own the value from then on (`CharGen` sets
    // it to 10) and the co-save owns it after a load, so copying a
    // standing 1 would relaunch `CharGen` every tick and on every load.
    const float value =
        *reinterpret_cast<const float*>(form + ids::kOffGlobalValue);
    const std::string seenKey = def.form.plugin + '|' + std::to_string(local);
    const auto [seen, first] = g_mirrorSeen.try_emplace(seenKey, value);
    if (first || seen->second == value) return;
    seen->second = value;
    Log("global: %s mirrored from its GLOB in %s = %g", name,
        def.form.plugin.c_str(), value);
    const LayerScope scope(layer);
    State().SetGlobal(name, value);
}

void SyncMirroredGlobals() {
    for (const char* name : kMirrored) {
        ForEachGlobalDef(name, [name](const GlobalDef& def, int layer) {
            SyncMirror(name, def, layer);
        });
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

// A plain store, not posted: the next SyncClock must read the new value back.
void SetClockGlobal(const std::string& name, float value) {
    for (const auto& clock : kClock) {
        if (_stricmp(name.c_str(), clock.tes3) != 0) continue;
        auto* form = static_cast<std::uint8_t*>(
            FormFromFile(ids::kSkyrimMaster, clock.skyrim));
        if (form) *reinterpret_cast<float*>(form + ids::kOffGlobalValue) = value;
        Log("clock: %s set to %g", clock.tes3, value);
        return;
    }
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

}  // namespace

void* CombatTargetOf(void* actor) {
    return actor && g_combatTarget ? g_combatTarget(PapyrusVm(), 0, actor) : nullptr;
}

namespace {

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

// `GetPCSleep`: the VM's own sleep sinks, which hear the engine start and end
// the player's sleep, drive the tick's latch (object_tick.h).
int SleepStartHook(void* sink, const void* event, void* source) {
    PostToMainThread(NoteSleepStarted);
    return g_sleepStart(sink, event, source);
}

int SleepStopHook(void* sink, const void* event, void* source) {
    PostToMainThread(NoteSleepStopped);
    return g_sleepStop(sink, event, source);
}

// The actors struck by another actor they were not already fighting, by
// runtime FormID: OpenMW's `CreatureStats::mAttacked`, set in `Npc::onHit` and
// `Creature::onHit`, and cleared only when a witnessed crime is paid off. Kept
// for the session, not saved (OpenMW saves it).
// See: docs/commentary/morrowind_runtime.md#disease-queries
std::set<std::uint32_t> g_attacked;

// Main thread.
void NoteAttacked(std::uint32_t target, std::uint32_t attackerId) {
    void* victim = RefByRuntimeId(target);
    void* attacker = attackerId ? RefByRuntimeId(attackerId) : nullptr;
    if (!victim || !attacker || !IsActorRef(victim) || !IsActorRef(attacker)) return;
    void* fighting = g_combatTarget ? g_combatTarget(PapyrusVm(), 0, victim) : nullptr;
    if (fighting == attacker) return;
    g_attacked.insert(target);
}

bool WasAttacked(const std::string& actor) {
    void* ref = OwnerRef(actor);
    return ref && g_attacked.count(FormIdOf(ref)) != 0;
}

// A hit lands: the target remembers what struck it, and a hit by the PLAYER
// raises the target's `OnPCHitMe`, as OpenMW's `Npc::onHit` and
// `Creature::onHit` do; a brawler the player punched is tired. Main thread.
void NoteHit(std::uint32_t target, std::uint32_t source, bool byPlayer,
             std::uint32_t attacker) {
    LogVerbose("hit: %08X struck by %08X%s", target, source, byPlayer ? " (player)" : "");
    if (source) g_lastHit[target] = source;
    NoteAttacked(target, attacker);
    if (!byPlayer) return;
    if (ObjectScript* instance = InstanceForRef(target)) {
        instance->Events().pcHitMe = true;
        if (!IsBrawler(*instance)) return;
        Log("brawl: the player hits %08X with %08X", target, source);
        if (source == kUnarmedWeapon) {
            TireBrawler(target);
        } else {
            BreakBrawl(target);
        }
    }
}

int HitHook(void* sink, const void* event, void* source) {
    const char* e = static_cast<const char*>(event);
    void* target = e ? *reinterpret_cast<void* const*>(e + ids::kOffHitTarget) : nullptr;
    if (target) {
        void* attacker = *reinterpret_cast<void* const*>(e + ids::kOffHitAttacker);
        const std::uint32_t id = FormIdOf(target);
        const std::uint32_t by = *reinterpret_cast<const std::uint32_t*>(e + ids::kOffHitSource);
        const bool byPlayer = attacker && attacker == PlayerRef();
        const std::uint32_t attackerId = attacker ? FormIdOf(attacker) : 0;
        PostToMainThread([id, by, byPlayer, attackerId]() {
            NoteHit(id, by, byPlayer, attackerId);
        });
    }
    return g_hit(sink, event, source);
}

int DeathHook(void* sink, const void* event, void* source) {
    const char* e = static_cast<const char*>(event);
    void* actor = e ? *reinterpret_cast<void* const*>(e + ids::kOffDeathActor) : nullptr;
    const std::uint8_t state = e ? *reinterpret_cast<const std::uint8_t*>(e + ids::kOffDeathState) : 0;
    if (actor) {
        Log("death: %08X state %u", FormIdOf(actor), state);
        if (state == ids::kDeathStateDying) {
            NoteEngineDeath(FormIdOf(actor));
            NoteDeathSpot(actor);
        }
    }
    return g_death(sink, event, source);
}

// An item entering or leaving the player's inventory: the tick polls it, so
// its script runs while carried, as TES3 runs every carried item's script.
int ContainerHook(void* sink, const void* event, void* source) {
    const char* e = static_cast<const char*>(event);
    if (e) {
        const auto from = *reinterpret_cast<const std::uint32_t*>(e + ids::kOffContainerFrom);
        const auto to = *reinterpret_cast<const std::uint32_t*>(e + ids::kOffContainerTo);
        if (from == ids::kPlayerRefFormId || to == ids::kPlayerRefFormId) {
            NoteCarriedChange(*reinterpret_cast<const std::uint32_t*>(e + ids::kOffContainerItem));
        }
    }
    return g_container(sink, event, source);
}

// `HitOnMe "id"`: was the actor's last hit dealt with that object. A yes is
// consumed, so the next test needs a new hit, as OpenMW's opcode clears it.
bool TakeHitBy(const std::string& actor, const std::string& item) {
    void* ref = OwnerRef(actor);
    void* form = ItemForm(item);
    if (!ref || !form) return false;
    const auto hit = g_lastHit.find(FormIdOf(ref));
    if (hit == g_lastHit.end() || hit->second != FormIdOf(form)) return false;
    g_lastHit.erase(hit);
    return true;
}

ProcessEventFn HookVmSink(const char* what, std::uint64_t sink,
                          std::uint64_t process, ProcessEventFn hook) {
    return reinterpret_cast<ProcessEventFn>(SwapVtableSlot(
        what, Resolve(what, sink, nullptr), ids::kProcessEventSlot,
        Resolve(what, process, nullptr), reinterpret_cast<void*>(hook)));
}

void InstallSleepSinks() {
    if (g_sleepStart) return;
    g_sleepStart = HookVmSink("SkyrimVM OnSleepStart sink",
                              ids::kVmSleepStartSink,
                              ids::kVmSleepStartProcess, &SleepStartHook);
    g_sleepStop = HookVmSink("SkyrimVM OnSleepStop sink",
                             ids::kVmSleepStopSink, ids::kVmSleepStopProcess,
                             &SleepStopHook);
    Log("sleep: OnSleepStart sink %s, OnSleepStop sink %s",
        g_sleepStart ? "hooked" : "NOT hooked",
        g_sleepStop ? "hooked" : "NOT hooked");
    g_hit = HookVmSink("SkyrimVM OnHit sink", ids::kVmHitSink,
                       ids::kVmHitProcess, &HitHook);
    Log("hit: OnHit sink %s", g_hit ? "hooked" : "NOT hooked");
    g_death = HookVmSink("SkyrimVM OnDeath sink", ids::kVmDeathSink,
                         ids::kVmDeathProcess, &DeathHook);
    Log("death: OnDeath sink %s", g_death ? "hooked" : "NOT hooked");
    g_container = HookVmSink("SkyrimVM OnItemAdded sink", ids::kVmContainerSink,
                             ids::kVmContainerProcess, &ContainerHook);
    Log("object: OnItemAdded sink %s", g_container ? "hooked" : "NOT hooked");
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
            static_cast<std::uint8_t*>(ref) + ActorField(ids::kOffActorMoveFlags));
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

std::uintptr_t g_frameTimer = 0;

std::uint32_t FrameClockMs() {
    return *reinterpret_cast<const std::uint32_t*>(g_frameTimer + ids::kOffFrameClockMs);
}

// The game speed a clamped (--fast) run is steered to, in game seconds per
// real second: under the tick's 30x catch-up limit. A case's `pace` step can
// lower it for an authored game-time window the default outruns.
constexpr float kPaceDefaultTarget = 25.0f;
float g_paceTarget = kPaceDefaultTarget;
// The step's bounds, in ms: 1/10 s is the largest a 38-degree ramp is still climbed at.
constexpr float kPaceMinStep = 1.0f;
constexpr float kPaceMaxStep = 100.0f;
// Real seconds measured before each adjustment, and between log lines. One
// adjustment at most halves or doubles the step, so a load's burst of game
// time cannot stall the game.
constexpr float kPaceWindow = 0.5f;
constexpr float kPaceMaxChange = 2.0f;
constexpr float kPaceLogEvery = 10.0f;

float g_paceGame = 0.0f;
float g_paceReal = 0.0f;
float g_paceTicks = 0.0f;
float g_paceLogReal = 0.0f;
float g_paceSinceLog = kPaceLogEvery;
// The step and speed of the window before, and the step a raise was last seen
// to slow the game at; the ceiling creeps back up so a new cell is retried.
float g_pacePrevStep = 0.0f;
float g_pacePrevSpeed = 0.0f;
float g_paceCeiling = kPaceMaxStep;
constexpr float kPaceCeilingRelax = 1.02f;

// The next step: the last one when raising it to this one slowed the game,
// which also caps later raises; otherwise rescaled toward g_paceTarget.
float NextPaceStep(float step, float speed) {
    const bool raised = step > g_pacePrevStep * 1.05f;
    float next;
    if (g_pacePrevSpeed > 0.0f && raised && speed < g_pacePrevSpeed * 0.9f) {
        g_paceCeiling = next = g_pacePrevStep;
    } else {
        const float change = speed > 0.0f ? g_paceTarget / speed : kPaceMaxChange;
        next = std::clamp(step * std::clamp(change, 1.0f / kPaceMaxChange, kPaceMaxChange), kPaceMinStep,
                          g_paceCeiling);
    }
    g_pacePrevStep = step;
    g_pacePrevSpeed = speed;
    g_paceCeiling = std::min(kPaceMaxStep, g_paceCeiling * kPaceCeilingRelax);
    return next;
}

std::uintptr_t g_gameSettings = 0;

// The float setting `name` in the game's settings, or null. The map's hash is
// the engine's, so every bucket is scanned; done once per setting.
float* GameSettingFloat(const char* name) {
    void* collection = g_gameSettings ? *reinterpret_cast<void**>(g_gameSettings) : nullptr;
    if (!collection) return nullptr;
    const auto* map = static_cast<const char*>(collection) + ids::kOffSettingsMap;
    const auto count = *reinterpret_cast<const std::uint32_t*>(map + ids::kOffMapBucketCount);
    void* const* buckets = *reinterpret_cast<void* const* const*>(map + ids::kOffMapBuckets);
    for (std::uint32_t i = 0; buckets && i < count; ++i) {
        for (void* entry = buckets[i]; entry; entry = *static_cast<void**>(entry)) {
            char* setting = static_cast<char**>(entry)[2];
            const char* own = setting ? *reinterpret_cast<const char**>(setting + ids::kOffSettingName) : nullptr;
            if (own && _stricmp(own, name) == 0) return reinterpret_cast<float*>(setting + ids::kOffSettingValue);
        }
    }
    return nullptr;
}

// How long an NPC stays in dialogue with an idle player, in game seconds. The
// speed-up would end a conversation in a few real seconds that a person has a
// minute to answer, so a paced run keeps it at its real-time length.
constexpr const char* kDialogueIdleTimer = "fAIInDialogueModewithPlayerTimer";
float* g_dialogueIdle = nullptr;
float g_dialogueIdleVanilla = 0.0f;
bool g_dialogueIdleSought = false;

void KeepDialogueIdleRealTime() {
    if (!g_dialogueIdleSought) {
        g_dialogueIdleSought = true;
        g_dialogueIdle = GameSettingFloat(kDialogueIdleTimer);
        if (g_dialogueIdle) g_dialogueIdleVanilla = *g_dialogueIdle;
        Log("pace: %s %s", kDialogueIdleTimer, g_dialogueIdle ? "scales with the speed-up" : "NOT FOUND");
    }
    if (g_dialogueIdle) *g_dialogueIdle = g_dialogueIdleVanilla * g_paceTarget;
}

// Speed is frames per second times the fixed step, but a longer step also
// costs each frame more (Arktrand: 5.6x at 33 ms, 0.8x at 100 ms), so the step
// is steered toward g_paceTarget under a learned ceiling. An unclamped game
// (normal play) has no step and is left alone.
// See: docs/commentary/headless_testing.md#speed
void PaceFrames(float gameSeconds, float realSeconds, float tickSeconds) {
    float& step = *reinterpret_cast<float*>(g_frameTimer + ids::kOffFrameStep);
    if (step <= 0.0f) return;
    KeepDialogueIdleRealTime();
    g_paceGame += gameSeconds;
    g_paceReal += realSeconds;
    g_paceTicks += tickSeconds;
    g_paceLogReal += realSeconds;
    if (g_paceReal < kPaceWindow) return;
    const float speed = g_paceGame / g_paceReal;
    step = NextPaceStep(step, speed);
    g_paceSinceLog += g_paceReal;
    if (g_paceSinceLog >= kPaceLogEvery) {
        Log("pace: %.1fx real time, step now %.1f ms, runtime ticks %.0f%% of real time", speed, step,
            100.0f * g_paceTicks / g_paceLogReal);
        g_paceSinceLog = g_paceTicks = g_paceLogReal = 0.0f;
    }
    g_paceGame = g_paceReal = 0.0f;
}

// Takes effect at once: the step is rescaled by the change, as speed is frame
// rate times step, so a short authored window never runs at the old speed.
void SetPaceTarget(float speed) {
    const float target = speed > 0.0f ? speed : kPaceDefaultTarget;
    if (target == g_paceTarget) return;
    float& step = *reinterpret_cast<float*>(g_frameTimer + ids::kOffFrameStep);
    if (step > 0.0f) step = std::clamp(step * target / g_paceTarget, kPaceMinStep, kPaceMaxStep);
    Log("pace: target now %.1fx real time, step %.1f ms", target, step);
    g_paceTarget = target;
}

// `GetCurrentWeather`: the global native hands back a Weather form, whose
// classification is then read off it. The TES3 mapping is the opcode's.
int WeatherClassification() {
    if (!g_currentWeather || !g_classification) return -1;
    void* weather = g_currentWeather(PapyrusVm(), 0, nullptr);
    return weather ? g_classification(PapyrusVm(), 0, weather) : -1;
}

// `GetWeaponType`: the TES3 type of the right hand's weapon, -1 for none, as
// OpenMW reads `Slot_CarriedRight`. The types are the sidecar's, by runtime
// FormID, resolved once: the load order cannot change after. A weapon no TES3
// plugin defines has no TES3 type, and answers -1.
// See: docs/commentary/morrowind_runtime.md#the-query-commands
int WeaponTypeOf(const std::string& actor) {
    static std::map<std::uint32_t, int> types;
    static bool built = false;
    if (!built) {
        ForEachWeaponType([](const FormRef& ref, int type) {
            if (void* form = Form(&ref)) types[FormIdOf(form)] = type;
        });
        built = true;
    }
    void* ref = OwnerRef(actor);
    void* weapon = IsActorRef(ref) ? EquippedWeapon(ref, false) : nullptr;
    const auto found = weapon ? types.find(FormIdOf(weapon)) : types.end();
    return found == types.end() ? -1 : found->second;
}

}  // namespace

void* EquippedWeapon(void* actor, bool left) {
    return g_equippedWeapon ? g_equippedWeapon(PapyrusVm(), 0, actor, left) : nullptr;
}

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
    InstallSleepSinks();
    g_equippedSpell = Native<HandQueryFn>("Actor.GetEquippedSpell",
                                          ids::kActorEquippedSpell);
    g_equippedWeapon = Native<EquippedWeaponFn>("Actor.GetEquippedWeapon",
                                                ids::kActorGetEquippedWeapon);
    hooks.weaponType = WeaponTypeOf;
    hooks.weaponDrawn = WeaponIsDrawn;
    hooks.sneaking = ActorSneaking;
    hooks.running = ActorRunning;
    hooks.itemEquipped = ItemIsEquipped;
    hooks.hitOnMe = TakeHitBy;
    hooks.attacked = WasAttacked;
    hooks.spellReadied = SpellIsReadied;
    hooks.applyMovementFlag = ApplyMovementFlag;
    hooks.resurrect = ResurrectActor;
    hooks.dropItem = DropFromActor;
    hooks.weather = WeatherClassification;
    hooks.deadCount = DeadCountOf;
    hooks.syncClock = SyncClock;
    hooks.setClockGlobal = SetClockGlobal;
    hooks.advanceHours = AdvanceHours;
    g_frameTimer = Resolve("frame timer", ids::kFrameTimer, nullptr);
    g_gameSettings = Resolve("game settings", ids::kGameSettings, nullptr);
    if (g_frameTimer) {
        hooks.frameClockMs = FrameClockMs;
        hooks.paceFrames = PaceFrames;
        hooks.setPaceTarget = SetPaceTarget;
    }
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
