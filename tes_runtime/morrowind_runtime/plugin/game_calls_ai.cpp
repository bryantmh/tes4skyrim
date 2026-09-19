// The AI commands as real packages, and the AI settings as actor values.
// See: docs/commentary/morrowind_runtime.md#game-calls

#include "game_calls_internal.h"

#include <string>

#include "ids.h"
#include "log.h"
#include "main_thread.h"
#include "filter.h"

namespace mwruntime {
namespace gamecalls {

namespace {

using RefCallFn = void (*)(void* vm, std::uint32_t stack, void* ref);
// The alias plumbing the AI packages run on. `Quest.GetAlias(int)` hands back
// the alias at an ALST index, `ReferenceAlias.ForceRefTo(ref)` fills it, and
// `ReferenceAlias.Clear()` empties it. The quest must be RUNNING, or it owns
// no alias instances at all.
// See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages
using GetAliasFn = void* (*)(void* vm, std::uint32_t stack, void* quest,
                             std::int32_t aliasId);
using ForceRefToFn = void (*)(void* vm, std::uint32_t stack, void* alias,
                              void* ref);
// ReferenceAlias.GetReference() -> what the alias holds, or null.
using AliasRefFn = void* (*)(void* vm, std::uint32_t stack, void* alias);
// Actor.GetCurrentPackage() -> the PACK the actor runs, for
// GetCurrentAiPackage.
using CurrentPackageFn = void* (*)(void* vm, std::uint32_t stack, void* actor);

// XMarker, Skyrim.esm 0x3B: the invisible anchor a travel or sandbox package
// aims at, since a package destination cannot be raw coordinates.
constexpr std::uint32_t kXMarker = 0x3B;

// The TES3 package kinds, in OpenMW's AiPackageTypeId order, which is what
// `GetCurrentAiPackage` answers with. The alias prefix is the kind's name.
constexpr const char* kAiKinds[] = {"wander", "travel", "escort", "follow",
                                    "activate"};
constexpr int kAiKindCount = 5;

GetAliasFn       g_questGetAlias = nullptr;
ForceRefToFn     g_forceRefTo = nullptr;
AliasRefFn       g_aliasReference = nullptr;
CurrentPackageFn g_currentPackage = nullptr;
RefCallFn        g_aliasClear = nullptr;

// The AI commands. Each fills the QUEST ALIAS that the import hung a real
// PACK record off, and the engine runs an actual package from there -- so the
// actor walks, follows and sandboxes for real.
// See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages

// The AI quest, started so its aliases can hold references at all. Null when
// it cannot be started.
//
// 🛑 A quest that is not running owns no alias instances, so ForceRefTo on one
// silently does nothing.
// 🛑 Papyrus has NO working `Quest.ForceActive`; the native of that name is
// `Weather.ForceActive`, and handing it a quest makes the quest the sky's
// weather.
// See: docs/commentary/morrowind_runtime.md#forceactive-is-a-weather-call
void* AiQuestForm() {
    const FormRef* staged = AiQuest();
    void* form = staged ? Form(staged) : nullptr;
    bool justStarted = false;
    return form && StartQuest(form, &justStarted) ? form : nullptr;
}

// One named alias of the AI quest, or null.
void* AiAlias(const std::string& name) {
    void* quest = AiQuestForm();
    const int index = AiAliasIndex(name);
    if (!quest || index < 0 || !g_questGetAlias) return nullptr;
    return g_questGetAlias(PapyrusVm(), 0, quest, index);
}

// What an alias holds right now, read off the ENGINE so a loaded save's
// fills count without any bookkeeping of ours.
void* AiAliasHolds(void* alias) {
    return alias && g_aliasReference
               ? g_aliasReference(PapyrusVm(), 0, alias)
               : nullptr;
}

// A slot's name: the package kind and its number in the pool.
std::string AiSlot(const char* kind, int n) {
    return std::string(kind) + std::to_string(n);
}

// Calls `visit(slot, actorAlias)` for every slot of every kind until one
// returns true, and reports whether one did.
template <class Visit>
bool AnyAiSlot(Visit visit) {
    const int slots = AiAliasIndex("slots");
    for (int kind = 0; kind < kAiKindCount; ++kind) {
        for (int n = 0; n < slots; ++n) {
            const std::string slot = AiSlot(kAiKinds[kind], n);
            if (visit(slot, AiAlias(slot + "Actor"))) return true;
        }
    }
    return false;
}

// Takes `ref` out of every slot it sits in, which ends those packages. A new
// AI command replaces whatever the actor was given before.
void ReleaseAiActor(void* ref) {
    if (!g_aliasClear) return;
    AnyAiSlot([ref](const std::string& slot, void* alias) {
        if (AiAliasHolds(alias) != ref) return false;
        g_aliasClear(PapyrusVm(), 0, alias);
        if (void* target = AiAlias(slot + "Target")) {
            g_aliasClear(PapyrusVm(), 0, target);
        }
        return false;
    });
}

// Starts one package kind on `actor`, aimed at `at`: the first EMPTY slot of
// that kind takes both, and the engine picks the PACK up from the actor's
// alias. One alias holds one reference, so the pool is what lets several
// actors run one kind at once.
//
// 🛑 ForceRefTo itself makes the actor re-evaluate its packages, so no
// EvaluatePackage is needed after it. POSTED, like every other engine call.
// See: docs/commentary/morrowind_runtime.md#forcerefto-must-be-posted
bool RunAiPackage(const char* kind, const std::string& actor, void* at) {
    void* ref = OwnerRef(actor);
    if (!ref || !at || !g_forceRefTo) return false;
    PostToMainThread([kind, ref, at]() {
        ReleaseAiActor(ref);
        const int slots = AiAliasIndex("slots");
        for (int n = 0; n < slots; ++n) {
            const std::string slot = AiSlot(kind, n);
            void* alias = AiAlias(slot + "Actor");
            void* target = AiAlias(slot + "Target");
            if (!alias || !target || AiAliasHolds(alias)) continue;
            g_forceRefTo(PapyrusVm(), 0, target, at);
            g_forceRefTo(PapyrusVm(), 0, alias, ref);
            return;
        }
        Log("ai: no free %s slot of %d", kind, slots);
    });
    return true;
}

// An Ai command with an empty target ends what the actor was given.
void StopAiPackage(const std::string& actor) {
    void* ref = OwnerRef(actor);
    if (ref) PostToMainThread([ref]() { ReleaseAiActor(ref); });
}

// `AiTravel x y z`: a package destination cannot be raw coordinates, so a
// marker is spawned at the point and the destination alias holds IT.
//
// 🛑 The marker is XMarker (Skyrim.esm 0x3B), the same base the cell anchors
// use. Without a reference to aim at, the Travel package falls back to its
// "near editor location" default and walks the actor home.
void AiTravelTo(const std::string& actor, float x, float y, float z) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_placeAtMe) return;
    void* marker = FormFromFile(ids::kSkyrimMaster, kXMarker);
    if (!marker) return;
    void* made = g_placeAtMe(PapyrusVm(), 0, ref, marker, 1, true, false);
    if (!made) return;
    PlaceAt(made, x, y, z, 0.0f);
    RunAiPackage("travel", actor, made);
}

// `AiWander range duration`: the Sandbox package idles around a marker, so
// one is dropped where the actor stands and its radius is the package's.
void AiWanderAt(const std::string& actor, float range, float duration) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_placeAtMe) return;
    void* marker = FormFromFile(ids::kSkyrimMaster, kXMarker);
    if (!marker) return;
    void* made = g_placeAtMe(PapyrusVm(), 0, ref, marker, 1, true, false);
    if (!made) return;
    Log("ai: %s sandboxes range %g for %g h", actor.c_str(), range, duration);
    RunAiPackage("wander", actor, made);
}

// `AiFollow id ...`: the Follow package handles distance, doors and pathing.
// An empty `target` clears the alias, which ends the package.
void AiFollowActor(const std::string& actor, const std::string& target,
                   float duration, float x, float y, float z) {
    (void)x; (void)y; (void)z;
    if (target.empty()) {
        StopAiPackage(actor);
        return;
    }
    if (RunAiPackage("follow", actor, OwnerRef(target))) {
        Log("ai: %s follows %s for %g h", actor.c_str(), target.c_str(),
            duration);
    }
}

// `AiEscort id duration x y z`: the Escort package walks the TARGET to a
// destination, so the escorted actor goes in the target alias and the
// destination marker is what the package's location input aims at.
void AiEscortActor(const std::string& actor, const std::string& target,
                   float duration, float x, float y, float z) {
    if (target.empty()) {
        StopAiPackage(actor);
        return;
    }
    (void)x; (void)y; (void)z;
    if (RunAiPackage("escort", actor, OwnerRef(target))) {
        Log("ai: %s escorts %s for %g h", actor.c_str(), target.c_str(),
            duration);
    }
}

// `AiActivate id`: the Activate package walks there AND activates it, which
// is exactly the TES3 command rather than an approximation of it.
void AiActivateObject(const std::string& actor, const std::string& object) {
    RunAiPackage("activate", actor, OwnerRef(object));
}

// `GetCurrentAiPackage`: which of OUR packages the actor is running, as
// OpenMW's AiPackageTypeId, or -1.
//
// 🛑 Read off the ENGINE, by comparing the running PACK against the five this
// plugin staged. Our own bookkeeping would only say what a script last asked
// for, which is a different question once the engine drops a package.
int CurrentAiPackageOf(const std::string& actor) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_currentPackage) return -1;
    void* running = g_currentPackage(PapyrusVm(), 0, ref);
    if (!running) return -1;
    const std::uint32_t id = FormIdOf(running);
    const int slots = AiAliasIndex("slots");
    for (int i = 0; i < kAiKindCount; ++i) {
        for (int n = 0; n < slots; ++n) {
            const FormRef* staged = FindAiPack(AiSlot(kAiKinds[i], n));
            void* pack = staged ? Form(staged) : nullptr;
            if (pack && FormIdOf(pack) == id) return i;
        }
    }
    return -1;
}

// `GetAiPackageDone`: the actor no longer runs a package a script gave it.
bool AiPackageDoneFor(const std::string& actor) {
    return CurrentAiPackageOf(actor) < 0;
}

// Writes one Skyrim actor value on `ref`.
void SetActorValue(void* ref, const char* value, float amount) {
    RunOnGameThread([ref, value, amount]() {
        void* name = nullptr;
        if (g_setValue && FixedString(&name, value)) {
            g_setValue(PapyrusVm(), 0, ref, &name, amount);
        }
    });
}

// Whether an actor with this Fight attacks a player standing beside it:
// OpenMW's `fight + dispositionBias + distanceBias >= 100` at distance 0.
bool FightsOnSight(const std::string& actor, int fight) {
    const float disposition = static_cast<float>(State().Disposition(actor));
    const float bias = (50.0f - disposition) * GmstNumber("fFightDispMult", 0) +
                       GmstNumber("iFightDistanceBase", 0);
    return static_cast<float>(fight) + bias >= 100.0f;
}

// Skyrim's Confidence tier for a TES3 Flee, as the import maps an authored
// one: confidence is 100 - flee, and only tier 4 never flees.
float ConfidenceTier(int flee) {
    const int confidence = 100 - flee;
    return confidence >= 100  ? 4.0f
           : confidence >= 70 ? 3.0f
           : confidence >= 40 ? 2.0f
           : confidence >= 15 ? 1.0f
                              : 0.0f;
}

// `SetFight`/`SetFlee`/`SetAlarm`: the number also moves the actor value the
// engine acts on. Aggression 2 attacks neutrals, which the player is; 1 only
// enemies; 0 nobody. Alarm is the import's Responsibility, so it maps the
// same way. Hello has no Skyrim counterpart.
// See: docs/commentary/morrowind_runtime.md#ai-settings
void ApplyAiSetting(const std::string& actor, int which, int value) {
    void* ref = OwnerRef(actor);
    if (!ref) return;
    if (which == kAiFight) {
        const float tier = FightsOnSight(actor, value) ? 2.0f
                           : value > 5                 ? 1.0f
                                                       : 0.0f;
        SetActorValue(ref, "Aggression", tier);
    } else if (which == kAiFlee) {
        SetActorValue(ref, "Confidence", ConfidenceTier(value));
    } else if (which == kAiAlarm) {
        SetActorValue(ref, "Assistance", value >= 30 ? 1.0f : 0.0f);
        SetActorValue(ref, "Morality", value >= 80   ? 3.0f
                                       : value >= 50 ? 2.0f
                                       : value >= 30 ? 1.0f
                                                     : 0.0f);
    }
}

}  // namespace

std::vector<void*> PlayerFollowers() {
    std::vector<void*> out;
    void* player = PlayerRef();
    const int slots = AiAliasIndex("slots");
    for (int n = 0; player && n < slots; ++n) {
        const std::string slot = AiSlot("follow", n);
        void* actor = AiAliasHolds(AiAlias(slot + "Actor"));
        if (actor && AiAliasHolds(AiAlias(slot + "Target")) == player) {
            out.push_back(actor);
        }
    }
    return out;
}

namespace {

int FollowerCount() { return static_cast<int>(PlayerFollowers().size()); }

}  // namespace

void InstallAiCalls(GameHooks& hooks) {
    hooks.followerCount = FollowerCount;
    g_questGetAlias = Native<GetAliasFn>("Quest.GetAlias",
                                         ids::kQuestGetAlias);
    g_forceRefTo = Native<ForceRefToFn>("ReferenceAlias.ForceRefTo",
                                        ids::kAliasForceRefTo);
    g_aliasReference = Native<AliasRefFn>(
        "ReferenceAlias.GetReference", ids::kAliasGetReference);
    g_aliasClear = Native<RefCallFn>("ReferenceAlias.Clear",
                                    ids::kAliasClear);
    g_currentPackage = Native<CurrentPackageFn>("Actor.GetCurrentPackage",
                                                ids::kActorCurrentPackage);
    hooks.aiTravel = AiTravelTo;
    hooks.aiWander = AiWanderAt;
    hooks.aiFollow = AiFollowActor;
    hooks.aiEscort = AiEscortActor;
    hooks.aiActivate = AiActivateObject;
    hooks.currentPackage = CurrentAiPackageOf;
    hooks.packageDone = AiPackageDoneFor;
    hooks.applyAiSetting = ApplyAiSetting;
}

}  // namespace gamecalls
}  // namespace mwruntime
