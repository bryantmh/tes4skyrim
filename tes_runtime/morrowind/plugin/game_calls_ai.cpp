// The AI commands as real packages, and the AI settings as actor values.
// See: docs/commentary/morrowind_runtime.md#game-calls

#include "game_calls_internal.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <functional>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "engine_ids.h"
#include "glide.h"
#include "ids.h"
#include "log.h"
#include "main_thread.h"
#include "filter.h"
#include "addresses.h"
#include "hook.h"
#include "ref_handle.h"

namespace tesruntime::mw {
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
// Game.SetPlayerAIDriven(bool): a global, so the tag is null.
using AiDrivenFn = void (*)(void* vm, std::uint32_t stack, void* tag, bool on);
// Game.ForceThirdPerson() and ForceFirstPerson(), more globals.
using GlobalCallFn = void (*)(void* vm, std::uint32_t stack, void* tag);

// XMarker, Skyrim.esm 0x3B: the invisible anchor a travel or sandbox package
// aims at, since a package destination cannot be raw coordinates.
constexpr std::uint32_t kXMarker = 0x3B;

// The TES3 package kinds, in OpenMW's AiPackageTypeId order, which is what
// `GetCurrentAiPackage` answers with. The alias prefix is the kind's name.
constexpr const char* kAiKinds[] = {"wander", "travel", "escort", "follow",
                                    "activate"};
constexpr int kAiKindCount = 5;
constexpr int kAiFollowKind = 3;

//: Aliases per slot: who runs the package, what it aims at, where it is going.
constexpr int kAiAliasesPerSlot = 3;

GetAliasFn       g_questGetAlias = nullptr;
ForceRefToFn     g_forceRefTo = nullptr;
AliasRefFn       g_aliasReference = nullptr;
CurrentPackageFn g_currentPackage = nullptr;
RefCallFn        g_aliasClear = nullptr;
RefCallFn        g_evaluatePackage = nullptr;
AiDrivenFn       g_playerAiDriven = nullptr;
GlobalCallFn     g_forceThirdPerson = nullptr;
GlobalCallFn     g_forceFirstPerson = nullptr;

// The test runner's walking player, as the AI commands name it.
constexpr const char* kPlayerId = "player";

// The AI commands. Each fills the QUEST ALIAS that the import hung a real
// PACK record off, and the engine runs an actual package from there -- so the
// actor walks, follows and sandboxes for real.
// See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages

// The AI quest's record, NOT started: enough to read its alias definitions.
// 🛑 Never start it before a game is loaded. Started at the main menu, it
// comes up empty from the next save loaded after a relaunch, and every
// follower, traveller and sandboxer that save held stands still.
// See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages
void* AiQuestRecord() {
    const FormRef* staged = AiQuest();
    return staged ? Form(staged) : nullptr;
}

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
    void* form = AiQuestRecord();
    bool justStarted = false;
    if (form && StartQuest(form, &justStarted)) {
        // Once: the start reads as new for every lookup until the engine
        // marks the quest running, about 40 lookups in the first tick.
        static bool startLogged = false;
        if (justStarted && !startLogged) {
            startLogged = true;
            Log("ai: the AI quest started");
        }
        return form;
    }
    // Once: every alias lookup comes through here, so a missing quest would
    // otherwise repeat this line for every slot of every package kind.
    static bool reported = false;
    if (!reported) {
        reported = true;
        Log("ai: the AI quest %s -- no package can run",
            form ? "will not start" : "is missing from this load order");
    }
    return nullptr;
}

// One named alias of the AI quest, or null.
//
// 🛑 Says ONCE when an alias the sidecar names is not in the quest. The index
// comes from the sidecar and is resolved against the ESM, so a sidecar
// deployed without its ESM silently reads the alias that now sits at that
// index -- a DIFFERENT slot of a DIFFERENT kind, which then runs the wrong
// package with no other symptom.
// See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages
void* AiAlias(const std::string& name) {
    void* quest = AiQuestForm();
    const int index = AiAliasIndex(name);
    if (!quest || index < 0 || !g_questGetAlias) return nullptr;
    void* alias = g_questGetAlias(PapyrusVm(), 0, quest, index);
    static bool reported = false;
    if (!alias && !reported) {
        reported = true;
        Log("ai: alias '%s' is index %d in the sidecar but the ESM has no "
            "such alias -- the ESM and sidecar are from different builds",
            name.c_str(), index);
    }
    return alias;
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
        if (void* spot = AiAlias(slot + "Where")) {
            g_aliasClear(PapyrusVm(), 0, spot);
        }
        return false;
    });
}

// How many packages each actor has been ordered onto, so an order posted
// earlier can tell a later one came after it.
std::mutex g_aiOrdersLock;
std::unordered_map<void*, unsigned> g_aiOrders;

unsigned AiOrdersOf(void* ref) {
    const std::lock_guard<std::mutex> hold(g_aiOrdersLock);
    return g_aiOrders[ref];
}

// The PACK each actor was last put on. Game thread only.
std::unordered_map<void*, void*> g_lastAiPack;

// Puts `ref` and `at` into the first EMPTY slot of `kind`; the engine picks the
// PACK up from the actor's alias. Game thread.
// 🛑 The slot whose PACK the actor is STILL running -- or, running none, was
// last put on -- comes last, and if it must be reused the emptied actor
// re-evaluates first: the engine keeps a package it re-picks along with its
// procedure, so a finished Travel given a new marker stays finished instead of
// starting afresh.
void FillAiSlot(const char* kind, void* ref, void* at, void* where) {
    void* running = g_currentPackage ? g_currentPackage(PapyrusVm(), 0, ref) : nullptr;
    if (!running) running = g_lastAiPack[ref];
    ReleaseAiActor(ref);
    const int slots = AiAliasIndex("slots");
    std::vector<int> order;
    for (int pass = 0; pass < 2; ++pass) {
        for (int n = 0; n < slots; ++n) {
            const FormRef* staged = FindAiPack(AiSlot(kind, n));
            const bool same = running && staged && Form(staged) == running;
            if (same == (pass == 1)) order.push_back(n);
        }
    }
    for (const int n : order) {
        const std::string slot = AiSlot(kind, n);
        void* alias = AiAlias(slot + "Actor");
        void* target = AiAlias(slot + "Target");
        void* holder = alias ? AiAliasHolds(alias) : nullptr;
        // A dead actor's package is over, so its slot serves the next order.
        if (!alias || !target || (holder && !IsDead(holder))) continue;
        const FormRef* again = FindAiPack(slot);
        if (running && again && Form(again) == running && g_evaluatePackage) {
            g_evaluatePackage(PapyrusVm(), 0, ref);
            for (int m = 0; m < slots; ++m) {
                void* other = AiAlias(AiSlot(kind, m) + "Actor");
                Log("ai: TEMP %08X reuses its running %s slot %d; slot %d holds %08X", FormIdOf(ref), kind, n, m,
                    other && AiAliasHolds(other) ? FormIdOf(AiAliasHolds(other)) : 0);
            }
        }
        g_forceRefTo(PapyrusVm(), 0, target, at);
        // 🛑 The destination is filled BEFORE the actor's alias, which is
        // what makes the engine evaluate packages: a package that starts
        // with an empty destination walks the actor to its fallback.
        if (where) {
            if (void* spot = AiAlias(slot + "Where")) {
                g_forceRefTo(PapyrusVm(), 0, spot, where);
            }
        }
        g_forceRefTo(PapyrusVm(), 0, alias, ref);
        if (ref == PlayerRef() && g_evaluatePackage) g_evaluatePackage(PapyrusVm(), 0, ref);
        const FormRef* staged = FindAiPack(slot);
        void* pack = staged ? Form(staged) : nullptr;
        g_lastAiPack[ref] = pack;
        Log("ai: %08X took %s slot %d, whose PACK is %08X",
            FormIdOf(ref), kind, n, pack ? FormIdOf(pack) : 0);
        return;
    }
    Log("ai: no free %s slot of %d", kind, slots);
}

// A reference's FormID as the co-saved state keys it.
std::string MarkerKey(void* marker) {
    char key[16];
    std::snprintf(key, sizeof(key), "%08X", FormIdOf(marker));
    return key;
}

// Follow-the-player orders a script move of the player paused, in the co-saved
// state by actor FormID: 1 while paused.
constexpr const char* kPausedFollowOwner = "ai|pausedfollow";

bool PausedFollow(void* actor) {
    return actor && State().Var(kPausedFollowOwner, MarkerKey(actor)) != 0.0f;
}

// Starts one package kind on `actor`, aimed at `at`. One alias holds one
// reference, so the slot pool is what lets several actors run one kind at once.
// A paused follower told again to follow the player stays paused; any other
// order ends the pause.
//
// 🛑 ForceRefTo itself makes the actor re-evaluate its packages, so no
// EvaluatePackage is needed after it. POSTED, like every other engine call: the
// fill runs a TICK OR MORE after the call, so the actor's reaction happens there.
// See: docs/commentary/morrowind_runtime.md#forcerefto-must-be-posted
bool RunAiPackageOn(const char* kind, const std::string& actor, void* ref, void* at,
                    void* where = nullptr) {
    {
        const std::lock_guard<std::mutex> hold(g_aiOrdersLock);
        ++g_aiOrders[ref];
    }
    if (!ref || !at || !g_forceRefTo) {
        Log("ai: %s cannot %s -- %s", actor.c_str(), kind,
            !ref ? "no reference for it"
                 : (!at ? "nothing to aim at"
                        : "ForceRefTo was never resolved"));
        return false;
    }
    PostToMainThread([kind, ref, at, where]() {
        const bool followsPlayer = std::strcmp(kind, "follow") == 0 && at == PlayerRef();
        if (PausedFollow(ref) && followsPlayer) return;
        if (PausedFollow(ref)) State().SetVar(kPausedFollowOwner, MarkerKey(ref), 0.0f);
        FillAiSlot(kind, ref, at, where);
    });
    return true;
}

// The same for the reference `actor` names in the calling script.
bool RunAiPackage(const char* kind, const std::string& actor, void* at) {
    return RunAiPackageOn(kind, actor, OwnerRef(actor), at);
}

// How close counts as arrived. The Travel package steers to the marker's own
// radius and then stops walking, so the actor parks a little short of the
// point rather than on it; this is that slack, not a search radius.
constexpr float kArrivedDistance = 128.0f;

// The RUNTIME FormID of the marker each actor was last sent to, so arrival can
// be measured. Only the kinds that HAVE a destination marker appear here --
// a follow keeps its point in FollowGoals, and activate ends on its own terms.
//
// 🛑 The id, not the pointer: the engine frees a reference, and a cached
// pointer outlives it while this is polled every tick.
std::map<std::string, std::uint32_t>& AiDestinations() {
    static std::map<std::string, std::uint32_t> destinations;
    return destinations;
}

void RememberAiDestination(const std::string& actor, void* marker) {
    if (marker) AiDestinations()[actor] = FormIdOf(marker);
}

// A follow's point, and the interior it counts in (empty: outdoors only).
struct FollowGoal {
    std::string cell;
    float x, y, z;
};

std::map<std::string, FollowGoal>& FollowGoals() {
    static std::map<std::string, FollowGoal> goals;
    return goals;
}

void ForgetAiDestination(const std::string& actor) {
    AiDestinations().erase(actor);
    FollowGoals().erase(actor);
}

// OpenMW's AiFollow::execute: within followDistance of the point, outdoors for
// a plain follow, or inside the named cell for the `...Cell` form.
// See: docs/commentary/morrowind_runtime.md#a-package-finishes-when-it-arrives
bool ReachedFollowPoint(const std::string& actor) {
    const auto it = FollowGoals().find(actor);
    void* ref = it == FollowGoals().end() ? nullptr : OwnerRef(actor);
    if (!ref || _stricmp(InteriorName(ref).c_str(), it->second.cell.c_str()) != 0) return false;
    const float dx = RefPositionAxis(PapyrusVm(), ref, 0) - it->second.x;
    const float dy = RefPositionAxis(PapyrusVm(), ref, 1) - it->second.y;
    const float dz = RefPositionAxis(PapyrusVm(), ref, 2) - it->second.z;
    return dx * dx + dy * dy + dz * dz <= kArrivedDistance * kArrivedDistance;
}

// How far apart two references stand on the ground plane.
float HorizontalApart(void* a, void* b) {
    const float dx = RefPositionAxis(PapyrusVm(), a, 0) - RefPositionAxis(PapyrusVm(), b, 0);
    const float dy = RefPositionAxis(PapyrusVm(), a, 1) - RefPositionAxis(PapyrusVm(), b, 1);
    return std::sqrt(dx * dx + dy * dy);
}

// Whether the actor has reached the destination its package was given. False
// for an actor with no tracked destination, which is every kind that ends on
// its own and every actor we never sent anywhere. Height does not count, as
// OpenMW's AiTravel ends on distanceIgnoreZ: an authored point below the floor
// the actor walks on (chargen's boat guard aims 240 units under the deck) is
// still reached.
// See: docs/commentary/morrowind_runtime.md#a-package-finishes-when-it-arrives
bool ReachedAiDestination(const std::string& actor) {
    if (ReachedFollowPoint(actor)) return true;
    const auto it = AiDestinations().find(actor);
    if (it == AiDestinations().end()) return false;
    void* ref = OwnerRef(actor);
    void* marker = RefByRuntimeId(it->second);
    return ref && marker && HorizontalApart(ref, marker) <= kArrivedDistance;
}

// An Ai command with an empty target ends what the actor was given. The
// destination goes with it: a package that is over has no goal left to reach,
// and a stale one would report the NEXT package done the moment it started.
void StopAiPackage(const std::string& actor) {
    ForgetAiDestination(actor);
    void* ref = OwnerRef(actor);
    if (!ref) return;
    PostToMainThread([ref]() {
        if (PausedFollow(ref)) State().SetVar(kPausedFollowOwner, MarkerKey(ref), 0.0f);
        ReleaseAiActor(ref);
    });
}

// An XMarker spawned on `actor`, for a package to aim at. Null when any step
// fails, and each failure says which -- a package that never starts otherwise
// goes completely silent, which is what hid the stalled chargen guard.
//
// 🛑 The marker is XMarker (Skyrim.esm 0x3B), the same base the cell anchors
// use. Without a reference to aim at, the Travel package falls back to its
// "near editor location" default and walks the actor home.
void* SpawnAiMarker(const char* kind, const std::string& actor, void* ref) {
    if (!ref) {
        Log("ai: %s cannot %s -- no reference for it", actor.c_str(), kind);
        return nullptr;
    }
    if (!g_placeAtMe) {
        Log("ai: %s cannot %s -- PlaceAtMe was never resolved", actor.c_str(),
            kind);
        return nullptr;
    }
    void* marker = FormFromFile(ids::kSkyrimMaster, kXMarker);
    if (!marker) {
        Log("ai: %s cannot %s -- XMarker %02X missing from %s", actor.c_str(),
            kind, kXMarker, ids::kSkyrimMaster);
        return nullptr;
    }
    void* made = g_placeAtMe(PapyrusVm(), 0, ref, marker, 1, true, false);
    if (!made) Log("ai: %s cannot %s -- PlaceAtMe made no marker",
                   actor.c_str(), kind);
    return made;
}

// 🛑 Runs `fn` after the moves already asked for. TES3 moves at once, so a
// script's `PositionCell` then `AiWander` in one frame wanders at the
// destination; our cell move is posted and lands later. A marker spawned at
// once stayed where the actor stood, and the package walked it back there
// from wherever it was sent (Markus, from Stormwend's tournament quarters).
void AfterPostedMoves(std::function<void()> fn) { PostToMainThread(std::move(fn)); }

// Actors whose script-given package is posted and not yet running. TES3 swaps
// the package at once, so until ours starts, `GetAiPackageDone` must not read
// the one it replaces as finished -- Rolf's hunt skipped every walk that way.
std::mutex g_aiStartingLock;
std::set<std::string> g_aiStarting;

bool AiStarting(const std::string& actor) {
    std::lock_guard<std::mutex> hold(g_aiStartingLock);
    return g_aiStarting.count(actor) != 0;
}

void StartAiAfterMoves(const std::string& actor, std::function<void()> start) {
    {
        std::lock_guard<std::mutex> hold(g_aiStartingLock);
        g_aiStarting.insert(actor);
    }
    AfterPostedMoves([actor, start]() {
        {
            std::lock_guard<std::mutex> hold(g_aiStartingLock);
            g_aiStarting.erase(actor);
        }
        start();
    });
}

// The markers of `AiWander 0` packages, in the co-saved state by FormID.
constexpr const char* kStillWanderOwner = "ai|stillwander";

// `AiTravel x y z`: a package destination cannot be raw coordinates, so a
// marker is spawned at the point and the destination alias holds IT.
void AiTravelTo(const std::string& actor, float x, float y, float z) {
    ForgetAiDestination(actor);
    void* ref = OwnerRef(actor);
    StartAiAfterMoves(actor, [actor, ref, x, y, z]() {
        void* made = SpawnAiMarker("travel", actor, ref);
        if (!made) return;
        PlaceAt(made, x, y, z, 0.0f);
        Log("ai: %s travels to (%g, %g, %g)", actor.c_str(), x, y, z);
        RememberAiDestination(actor, made);
        RunAiPackageOn("travel", actor, ref, made);
        PostToMainThread([actor, made]() {
            Log("ai: %s's travel marker %08X is at (%g, %g, %g)", actor.c_str(),
                FormIdOf(made), RefPositionAxis(PapyrusVm(), made, 0),
                RefPositionAxis(PapyrusVm(), made, 1), RefPositionAxis(PapyrusVm(), made, 2));
        });
    });
}

// `AiWander range duration`: the Sandbox package idles around a marker, so
// one is dropped where the actor stands and its radius is the package's.
void AiWanderAt(const std::string& actor, float range, float duration) {
    // Sandboxing has no destination to arrive at, and a stale one from an
    // earlier Travel would report this package done the moment it started.
    ForgetAiDestination(actor);
    void* ref = OwnerRef(actor);
    StartAiAfterMoves(actor, [actor, ref, range, duration]() {
        void* made = SpawnAiMarker("wander", actor, ref);
        if (!made) return;
        Log("ai: %s sandboxes range %g for %g h", actor.c_str(), range, duration);
        if (range <= 0.0f) State().SetVar(kStillWanderOwner, MarkerKey(made), 1.0f);
        RunAiPackageOn("wander", actor, ref, made);
    });
}

// `AiFollow id ...`: the Follow package handles distance, doors and pathing.
// An empty `target` clears the alias, which ends the package.
void AiFollowActor(const std::string& actor, const std::string& target,
                   const std::string& cell, float duration, float x, float y,
                   float z) {
    if (target.empty()) {
        StopAiPackage(actor);
        return;
    }
    ForgetAiDestination(actor);
    FollowGoals()[actor] = {cell, x, y, z};
    if (RunAiPackage("follow", actor, OwnerRef(target))) {
        Log("ai: %s follows %s for %g h", actor.c_str(), target.c_str(),
            duration);
    }
}

// `AiEscort id duration x y z`: the Escort package walks the TARGET to a
// destination, so the escorted actor goes in the target alias and the
// destination marker is what arrival is measured against.
//
// 🛑 The coordinates are the POINT the pair is walking to. Discarding them
// left the package with no goal, so it could never report itself done and the
// script waiting on it stalled exactly as a Travel with no marker would.
void AiEscortActor(const std::string& actor, const std::string& target,
                   float duration, float x, float y, float z) {
    if (target.empty()) {
        StopAiPackage(actor);
        return;
    }
    ForgetAiDestination(actor);
    void* ref = OwnerRef(actor);
    void* escorted = OwnerRef(target);
    StartAiAfterMoves(actor, [actor, ref, escorted, target, duration, x, y, z]() {
        void* made = SpawnAiMarker("escort", actor, ref);
        if (made) {
            PlaceAt(made, x, y, z, 0.0f);
            RememberAiDestination(actor, made);
        }
        if (RunAiPackageOn("escort", actor, ref, escorted, made)) {
            Log("ai: %s escorts %s to (%g, %g, %g) for %g h", actor.c_str(),
                target.c_str(), x, y, z, duration);
        }
    });
}

// `AiActivate id`: the Activate package walks there AND activates it, which
// is exactly the TES3 command rather than an approximation of it.
void AiActivateObject(const std::string& actor, const std::string& object) {
    // Activating ends when the engine has done it, not by distance.
    ForgetAiDestination(actor);
    RunAiPackage("activate", actor, OwnerRef(object));
}

// `GetCurrentAiPackage`: which of OUR packages the actor is running, as
// OpenMW's AiPackageTypeId, or -1. A follow a script move paused is still the
// actor's package, as in OpenMW, where it only waits unprocessed.
//
// 🛑 Read off the ENGINE, by comparing the running PACK against the five this
// plugin staged. Our own bookkeeping would only say what a script last asked
// for, which is a different question once the engine drops a package.
//
// 🛑 A DEAD actor answers -1, as OpenMW's does. Skyrim keeps a corpse's
// package assigned, and the companion template returns early every tick while
// `GetCurrentAIPackage == 3` -- above its `if ( OnDeath )`.
int CurrentAiPackageOf(const std::string& actor) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_currentPackage || IsDead(ref)) return -1;
    if (PausedFollow(ref)) return kAiFollowKind;
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

// `GetAiPackageDone`: whether the package a script gave this actor has met its
// goal. TES3 answers "has it FINISHED"; Skyrim's `GetCurrentPackage` answers
// "what is assigned", and an actor in the world always has something assigned
// -- our own pack keeps winning while its alias holds, so asking the engine
// alone can never go true. The goal test is ours, and reaching it RELEASES the
// actor, which is what makes the engine's answer agree from then on.
// See: docs/commentary/morrowind_runtime.md#a-package-finishes-when-it-arrives
bool AiPackageDoneFor(const std::string& actor) {
    if (AiStarting(actor)) return false;
    const int running = CurrentAiPackageOf(actor);
    bool done = running < 0;
    if (!done && ReachedAiDestination(actor)) {
        StopAiPackage(actor);
        done = true;
    }
    static std::map<std::string, bool> last;
    auto seen = last.find(actor);
    if (seen == last.end() || seen->second != done) {
        last[actor] = done;
        void* ref = OwnerRef(actor);
        void* now = ref && g_currentPackage
                        ? g_currentPackage(PapyrusVm(), 0, ref)
                        : nullptr;
        Log("ai: %s package -> %s (engine runs %08X)", actor.c_str(),
            done ? "DONE" : (running < 0 ? "none" : kAiKinds[running]),
            now ? FormIdOf(now) : 0);
    }
    return done;
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

// 🛑 TES3's Fight only ever targets the player (OpenMW's engageCombat), so
// Aggression never goes past 1, which attacks enemies alone; the hostile
// faction makes the player the one enemy. Aggression 2 would attack every
// neutral NPC, and a goblin killed Irfl before his quest greeting. A creature
// goes in `hostile_creature`, which the guard tick reads.
// SetDisposition comes here too, so the ranks conditions read never go stale.
// See: docs/commentary/tes5_import_actors.md#tes3-fight-targets-the-player
void SetFight(void* ref, const std::string& actor, int fight) {
    const ActorDef* def = FindActor(actor);
    SetActorValue(ref, "Aggression", fight > 5 ? 1.0f : 0.0f);
    const int disposition = State().Disposition(actor);
    const bool hostile = FightsOnSight(actor, fight);
    void* faction = def && def->creature ? DispositionForm("hostile_creature") : nullptr;
    if (!faction) faction = DispositionForm("hostile");
    RunOnGameThread([ref, fight, disposition, hostile, faction]() {
        SetFactionRank(ref, DispositionForm("disposition"), disposition);
        SetFactionRank(ref, DispositionForm("aggression"), std::clamp(fight, 0, 127));
        if (hostile) {
            SetFactionRank(ref, faction, 0);
        } else if (FactionRank(ref, faction) >= 0) {
            RemoveFromFaction(ref, faction);
        }
    });
}

// `SetFight`/`SetFlee`/`SetAlarm`: the number also moves what the engine acts
// on. Alarm is the import's Responsibility, so it maps the same way. Hello has
// no Skyrim counterpart.
// See: docs/commentary/morrowind_runtime.md#ai-settings
void ApplyAiSetting(const std::string& actor, int which, int value) {
    void* ref = OwnerRef(actor);
    if (!ref) return;
    if (which == kAiFight) {
        SetFight(ref, actor, value);
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

void* DispositionForm(const char* row) {
    const FormRef* ref = EffectForm(row);
    return ref ? Form(ref) : nullptr;
}

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

// The actors our follow slots have following the player, refreshed each game
// tick for the spacing hook, which the engine may call off the game thread.
std::mutex g_playerFollowersLock;
std::set<void*> g_playerFollowers;

using FollowSpacingFn = float (*)(void* follower, void* leader);
FollowSpacingFn g_followSpacing = nullptr;

// TES3 followers keep no queue: OpenMW's AiFollow rests each one within its
// own follow distance of the player however many follow, where Skyrim's Follow
// procedure stands each behind the followers listed before it.
float FollowSpacingHook(void* follower, void* leader) {
    if (leader && leader == PlayerRef()) {
        const std::lock_guard<std::mutex> hold(g_playerFollowersLock);
        if (g_playerFollowers.count(follower)) return 0.0f;
    }
    return g_followSpacing(follower, leader);
}

void InstallFollowSpacing() {
    const std::uintptr_t spacing = Resolve("Follow spacing", ids::kFollowSpacing, nullptr);
    const std::pair<std::uint64_t, std::size_t> callers[] = {
        {ids::kFollowProcedureUpdate, ids::kFollowProcedureScan},
        {ids::kActorFollowUpdate, ids::kActorFollowScan}};
    g_followSpacing = reinterpret_cast<FollowSpacingFn>(spacing);
    int patched = 0;
    for (const auto& [id, scan] : callers) {
        const std::uintptr_t caller = Resolve("Follow spacing caller", id, nullptr);
        const std::uintptr_t site = caller && spacing ? FindCallTo(caller, scan, spacing) : 0;
        if (site && PatchCall(site, reinterpret_cast<void*>(&FollowSpacingHook), "Follow spacing")) ++patched;
    }
    Log("ai: follower spacing hooked at %d of 2 call sites", patched);
}

}  // namespace

void LogPlayerFollowList();

namespace {

// OpenMW's actor processing range: AI past it, or in another place, does not run.
constexpr float kProcessingRange = 7168.0f;

// Whether `actor` stands in `place` within kProcessingRange of (x, y, z).
bool ActsNear(void* actor, void* place, float x, float y, float z) {
    if (PlaceOf(actor) != place) return false;
    const float dx = RefPositionAxis(PapyrusVm(), actor, 0) - x;
    const float dy = RefPositionAxis(PapyrusVm(), actor, 1) - y;
    const float dz = RefPositionAxis(PapyrusVm(), actor, 2) - z;
    return dx * dx + dy * dy + dz * dz <= kProcessingRange * kProcessingRange;
}

// A paused follower the player is back near takes its follow slot again.
void ResumeFollowsNearPlayer() {
    void* player = PlayerRef();
    if (!player) return;
    const float x = RefPositionAxis(PapyrusVm(), player, 0), y = RefPositionAxis(PapyrusVm(), player, 1),
                z = RefPositionAxis(PapyrusVm(), player, 2);
    for (const std::string& key : State().VarNames(kPausedFollowOwner)) {
        if (State().Var(kPausedFollowOwner, key) == 0.0f) continue;
        void* actor = RefByRuntimeId(static_cast<std::uint32_t>(std::stoul(key, nullptr, 16)));
        const bool gone = !actor || IsDead(actor);
        if (!gone && !ActsNear(actor, PlaceOf(player), x, y, z)) continue;
        State().SetVar(kPausedFollowOwner, key, 0.0f);
        if (gone) continue;
        Log("ai: %s follows the player again -- the player is back", key.c_str());
        FillAiSlot("follow", actor, player, nullptr);
    }
}

}  // namespace

// 🛑 A script move of the player carries no follower: in OpenMW only doors and
// travel do, and AiFollow runs only where the follower is processed. Skyrim's
// Follow catches up across any teleport, so Bloodmoon's Rolf (AiFollow Player
// forever) trailed the player through all of Mournhold. Ending the package
// before the move is what keeps the engine from bringing him.
void LeaveFollowersBehind(void* place, float x, float y, float z) {
    for (void* actor : PlayerFollowers()) {
        if (ActsNear(actor, place, x, y, z)) continue;
        State().SetVar(kPausedFollowOwner, MarkerKey(actor), 1.0f);
        ReleaseAiActor(actor);
        if (g_evaluatePackage) g_evaluatePackage(PapyrusVm(), 0, actor);
        Log("ai: %08X stays behind -- a script moved the player away", FormIdOf(actor));
    }
}

void TickPlayerFollowers() {
    static int listLogTicks = 0;
    if (++listLogTicks % 30 == 0) LogPlayerFollowList();
    ResumeFollowsNearPlayer();
    const std::vector<void*> now = PlayerFollowers();
    const std::lock_guard<std::mutex> hold(g_playerFollowersLock);
    g_playerFollowers = std::set<void*>(now.begin(), now.end());
}

namespace {

// OpenMW's AiTravel range (aitravel.cpp isWithinMaxRange): past it the
// package idles, unfinished, rather than walking.
constexpr float kTravelMaxRange = 7168.0f;

// The marker a slot's package walks to: Travel's target, Escort's spot.
void* SlotDestination(const std::string& kind, const std::string& slot) {
    return AiAliasHolds(AiAlias(slot + (kind == "escort" ? "Where" : "Target")));
}

// 🛑 TES3 AI never leaves the actor's cell, and Skyrim's walks through doors.
// OpenMW's AiWander with distance 0 never walks (execute and fastForward both
// skip it), so its marker goes with the moved actor. An AiTravel or AiEscort
// whose goal is now in another cell or out of range idles there in OpenMW, so
// the actor leaves its slot; the destination is kept, and the package stays
// unfinished.
bool AfterMoveIn(const std::string& kind, const std::string& slot, void* actor,
                 void* place, float x, float y, float z) {
    void* marker = SlotDestination(kind, slot);
    if (!marker) return false;
    if (kind == "wander") {
        if (!State().HasVar(kStillWanderOwner, MarkerKey(marker))) return false;
        MoveInto(marker, place, x, y, z, 0.0f);
        Log("ai: %08X's AiWander 0 marker moves with it", FormIdOf(actor));
        return false;
    }
    const float dx = RefPositionAxis(PapyrusVm(), marker, 0) - x;
    const float dy = RefPositionAxis(PapyrusVm(), marker, 1) - y;
    const float dz = RefPositionAxis(PapyrusVm(), marker, 2) - z;
    const bool far = dx * dx + dy * dy + dz * dz > kTravelMaxRange * kTravelMaxRange;
    if (PlaceOf(marker) == place && !far) return false;
    Log("ai: %08X was moved away from its %s goal -- it idles there", FormIdOf(actor),
        kind.c_str());
    return true;
}

// Whether `actor` runs any package a script gave it.
bool HoldsAiSlot(void* actor) {
    const int slots = AiAliasIndex("slots");
    for (int i = 0; i < kAiKindCount; ++i) {
        for (int n = 0; n < slots; ++n) {
            if (AiAliasHolds(AiAlias(AiSlot(kAiKinds[i], n) + "Actor")) == actor) return true;
        }
    }
    return false;
}

// 🛑 An actor on its AUTHORED package that a script sends to another TES3 cell
// stays there: OpenMW's AiWander keeps its centre in the old cell and finds no
// point to walk to, while Skyrim's "near editor location" walks it home --
// Korst Wind-Eye left Lake Fjalding for his hut. It takes an AiWander 0 here,
// unless the script that moved it gave it a package of its own since.
void StillAtNewSpot(void* actor) {
    char id[16];
    std::snprintf(id, sizeof id, "%08X", FormIdOf(actor));
    const std::string name = id;
    const unsigned orders = AiOrdersOf(actor);
    AfterPostedMoves([actor, name, orders]() {
        if (AiOrdersOf(actor) != orders) return;
        void* made = SpawnAiMarker("wander", name, actor);
        if (!made) return;
        Log("ai: %s was moved out of its cell -- it idles there", name.c_str());
        State().SetVar(kStillWanderOwner, MarkerKey(made), 1.0f);
        RunAiPackageOn("wander", name, actor, made);
    });
}

}  // namespace

// 🛑 A moved actor re-evaluates its package, posted after the move lands.
// Skyrim keeps a running package's "current location" from where it STARTED,
// so a stand-still actor a script moves walks back to its old spot -- the
// Masterthief left the Ruins of Storm for the Thieves Guild. TES3 has no such
// anchor: an actor without AI stays where the script put it.
void AiAfterScriptMove(void* actor, void* place, float x, float y, float z, bool leftCell) {
    if (!actor || actor == PlayerRef() || !IsActorRef(actor)) return;
    if (g_evaluatePackage) {
        PostToMainThread([actor]() { g_evaluatePackage(PapyrusVm(), 0, actor); });
    }
    const int slots = AiAliasIndex("slots");
    for (const char* kind : {"wander", "travel", "escort"}) {
        for (int n = 0; n < slots; ++n) {
            const std::string slot = AiSlot(kind, n);
            if (AiAliasHolds(AiAlias(slot + "Actor")) != actor) continue;
            if (!AfterMoveIn(kind, slot, actor, place, x, y, z)) return;
            ReleaseAiActor(actor);
            if (leftCell) StillAtNewSpot(actor);
            return;
        }
    }
    if (leftCell && !IsDead(actor) && !HoldsAiSlot(actor)) StillAtNewSpot(actor);
}

namespace {

int FollowerCount() { return static_cast<int>(PlayerFollowersAlong().size()); }

std::string FollowerNames() {
    std::string out;
    for (void* ref : PlayerParty()) {
        const std::string key = ActorKey(ref);
        out += (out.empty() ? "" : ", ") + (key.empty() ? std::to_string(FormIdOf(ref)) : key);
    }
    return out;
}

}  // namespace

// Whether the ESM's alias list is the one the sidecar was written against.
// The sidecar names three aliases per slot; an ESM built before that used two,
// and every index past the first slot then names a DIFFERENT slot of a
// DIFFERENT kind -- the actor runs a package nobody asked for and nothing else
// looks wrong. Checked once, by asking for the alias one PAST the last the
// sidecar names: it must not exist.
// See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages
void ReportAliasLayoutMismatch() {
    void* quest = AiQuestRecord();
    const int slots = AiAliasIndex("slots");
    if (!quest || slots <= 0 || !g_questGetAlias) return;
    const int expected = slots * kAiKindCount * kAiAliasesPerSlot;
    if (g_questGetAlias(PapyrusVm(), 0, quest, expected)) {
        Log("ai: the ESM has MORE than the %d aliases this sidecar names -- "
            "the two are from different builds, so every package will run "
            "from the wrong slot", expected);
    } else if (!g_questGetAlias(PapyrusVm(), 0, quest, expected - 1)) {
        Log("ai: the ESM has FEWER than the %d aliases this sidecar names -- "
            "the two are from different builds, so every package will run "
            "from the wrong slot", expected);
    }
}

// Hands the player to packages, or back to the controls.
void SetPlayerAiDriven(bool on) {
    if (!g_playerAiDriven) return;
    PostToMainThread([on]() { g_playerAiDriven(PapyrusVm(), 0, nullptr, on); });
}

// The point a walk heads for once the camera has settled. A steered walk
// keeps the player at the controls instead of handing it to a package.
struct PendingWalk {
    int settleTicks = 0;
    float x = 0.0f, y = 0.0f, z = 0.0f;
    int ticks = 0;
    bool steered = false;
    // Where a steered walk last made headway, and the ticks since.
    float fromX = 0.0f, fromY = 0.0f;
    int stillTicks = 0;
    // Where a steered walk began: the player keeps to the line from here to its point.
    float startX = 0.0f, startY = 0.0f;
    // A stalled steered walk backs off to its start once, then tries its point again.
    bool backing = false, backedOff = false;
    // A steered walk that reached its point: the next leg's line starts there.
    bool arrived = false;
};

// A steered player stops this close to its point: nothing parks it short, so
// it keeps to the line its legs were planned along.
constexpr float kSteerArrived = 48.0f;

// A steered walk that moves less than kSteerHeadway in kSteerStallTicks is
// stuck against something, and ends there rather than at the walk's timeout.
constexpr float kSteerHeadway = 32.0f;
constexpr int kSteerStallTicks = 150;

// Ticks between the walk's progress lines, which show where a stalled walk stopped.
constexpr int kWalkLogTicks = 60;

PendingWalk& Walk() {
    static PendingWalk walk;
    return walk;
}

// 🛑 Ticks between the third-person switch and the travel package: a package
// that starts during the camera switch leaves the player standing for good.
// See: docs/commentary/morrowind_runtime.md#the-walk-step
constexpr int kWalkSettleTicks = 30;

// The test runner's `walk x y z`: the player walks there on the engine's own
// pathing, AI driven in third person as the vanilla wedding drives it, so a
// follower sees a real walk. The package starts from PlayerWalkDone.
void PlayerWalkTo(float x, float y, float z) {
    if (g_forceThirdPerson) PostToMainThread([]() { g_forceThirdPerson(PapyrusVm(), 0, nullptr); });
    SetPlayerAiDriven(true);
    Walk() = {kWalkSettleTicks, x, y, z, 0, false};
}

// How far ahead along its leg's line a steered player aims, so a knock aside
// (a blow, a follower's bump) is walked back onto the line rather than cut across.
constexpr float kSteerLookahead = 96.0f;

// Holds the Auto-Move key's toggle on or off, and while on turns the player
// level toward a point kSteerLookahead along the line from (sx, sy) to (x, y),
// past the player's own place on it, as a player at the keys keeps to a path.
void SteerTick(bool on, float sx, float sy, float x, float y) {
    PostToMainThread([on, sx, sy, x, y]() {
        void* controls = PlayerControls();
        void* player = PlayerRef();
        if (!controls || !player) return;
        At<std::uint8_t>(controls, ids::kOffPlayerControlsAutoMove) = on ? 1 : 0;
        if (!on) return;
        const float px = RefPositionAxis(PapyrusVm(), player, 0);
        const float py = RefPositionAxis(PapyrusVm(), player, 1);
        const float dx = x - sx, dy = y - sy, len = std::hypot(dx, dy);
        float ax = x, ay = y;
        if (len > kSteerLookahead) {
            const float along = ((px - sx) * dx + (py - sy) * dy) / len + kSteerLookahead;
            const float t = std::clamp(along / len, 0.0f, 1.0f);
            ax = sx + dx * t;
            ay = sy + dy * t;
        }
        At<float>(player, tesruntime::ids::kOffRefRotX) = 0.0f;
        At<float>(player, tesruntime::ids::kOffRefRotZ) = std::atan2(ax - px, ay - py);
    });
}

// Holds the Always Run toggle off while `walking`, and gives the player back
// the setting it had once a walked leg ends.
void SetSteerPace(bool walking) {
    PostToMainThread([walking]() {
        static int saved = -1;
        void* controls = PlayerControls();
        if (!controls) return;
        auto& running = At<std::uint8_t>(controls, ids::kOffPlayerControlsRunning);
        if (walking) {
            if (saved < 0) saved = running;
            running = 0;
        } else if (saved >= 0) {
            running = static_cast<std::uint8_t>(saved);
            saved = -1;
        }
    });
}

// The test runner's `steer x y z`: the player goes there at the controls, in
// first person with Auto-Move held, straight over land or water, as a player
// crosses where no navmesh leads. A follower sees a real walk or swim. The
// line runs on from the last leg's point, not from wherever within
// kSteerArrived of it the player stopped, so legs chain along the planned route.
// See: docs/commentary/morrowind_runtime.md#the-walk-step
void PlayerSteerTo(float x, float y, float z, bool walking) {
    if (g_forceFirstPerson) PostToMainThread([]() { g_forceFirstPerson(PapyrusVm(), 0, nullptr); });
    SetPlayerAiDriven(false);
    SetSteerPace(walking);
    void* player = PlayerRef();
    const float px = player ? RefPositionAxis(PapyrusVm(), player, 0) : x;
    const float py = player ? RefPositionAxis(PapyrusVm(), player, 1) : y;
    const PendingWalk& last = Walk();
    const bool chained = last.arrived && std::hypot(px - last.x, py - last.y) <= kSteerArrived;
    const float sx = chained ? last.x : px, sy = chained ? last.y : py;
    Walk() = {0, x, y, z, 0, true, px, py, 0, sx, sy};
    SteerTick(true, sx, sy, x, y);
}

// Whether the player stands within kSteerArrived of the steered point, and
// counts the ticks it has gone without headway. The first stall turns the
// player back to the walk's start, as a player knocked into a corner backs
// out; once there it heads for the point again.
bool ReachedSteerPoint(PendingWalk& walk) {
    void* player = PlayerRef();
    if (!player) return false;
    const float px = RefPositionAxis(PapyrusVm(), player, 0);
    const float py = RefPositionAxis(PapyrusVm(), player, 1);
    if (std::hypot(px - walk.fromX, py - walk.fromY) > kSteerHeadway) {
        walk.fromX = px;
        walk.fromY = py;
        walk.stillTicks = 0;
    } else {
        ++walk.stillTicks;
    }
    if (walk.stillTicks >= kSteerStallTicks && !walk.backedOff) {
        Log("ai: the player is stuck at (%g, %g); backing off to (%g, %g)", px, py, walk.startX, walk.startY);
        walk.backing = walk.backedOff = true;
        walk.stillTicks = 0;
    }
    if (walk.backing && std::hypot(px - walk.startX, py - walk.startY) <= kSteerArrived) {
        walk.backing = false;
        walk.stillTicks = 0;
    }
    walk.arrived = !walk.backing && std::hypot(px - walk.x, py - walk.y) <= kSteerArrived;
    return walk.arrived;
}

// Whether a steered walk has stopped making headway.
bool PlayerWalkStuck() {
    return Walk().steered && Walk().stillTicks >= kSteerStallTicks;
}

// TEMP (follower-freeze diag): the player's ExtraFollower list, which the
// Follow procedure (0x4161f0) spaces each follower behind the nearer ones by.
using FollowerListFn = void* (*)(void* extraList);
FollowerListFn g_followerList = nullptr;
using CombatStateFn = std::int32_t (*)(void* vm, std::uint32_t stack, void* actor);
CombatStateFn g_combatState = nullptr;

// TEMP: SKSE's IsSwimming -- actorState1 bit 0x400.
int SwimmingFlag(void* actor) {
    const auto flags = *reinterpret_cast<const std::uint32_t*>(
        static_cast<const char*>(actor) + ActorField(ids::kOffActorState1));
    return (flags & 0x400u) ? 1 : 0;
}

void LogPlayerFollowList() {
    void* player = PlayerRef();
    void* list = player && g_followerList ? g_followerList(static_cast<char*>(player) + ids::kOffRefExtraList) : nullptr;
    const std::uint32_t n = list ? At<std::uint32_t>(list, 0x20) : 0;
    char* entries = list ? At<char*>(list, 0x10) : nullptr;
    Log("ai: TEMP the player's follower list holds %u", n);
    for (std::uint32_t i = 0; entries && i < n && i < 16; ++i) {
        void* ref = RefFromHandle(*reinterpret_cast<std::uint32_t*>(entries + 8 * i));
        Log("ai: TEMP   follower %u: %08X, F %g, %g from the player, in '%s'", i,
            ref ? FormIdOf(ref) : 0, *reinterpret_cast<float*>(entries + 8 * i + 4),
            ref ? HorizontalApart(ref, player) : -1.0f, ref ? InteriorName(ref).c_str() : "?");
        if (ref) ReleaseRef(ref);
    }
    for (void* ref : PlayerFollowers()) {
        void* pack = g_currentPackage ? g_currentPackage(PapyrusVm(), 0, ref) : nullptr;
        void* foe = CombatTargetOf(ref);
        Log("ai: TEMP   slot follower %08X runs PACK %08X, %g from the player, in '%s', combat target %08X, "
            "combat state %d (player in combat %d, state %d), swim %d, at (%g, %g, %g)", FormIdOf(ref),
            pack ? FormIdOf(pack) : 0, HorizontalApart(ref, player), InteriorName(ref).c_str(),
            foe ? FormIdOf(foe) : 0, g_combatState ? g_combatState(PapyrusVm(), 0, ref) : -1,
            ActorInCombat(player) ? 1 : 0, g_combatState ? g_combatState(PapyrusVm(), 0, player) : -1,
            SwimmingFlag(ref), RefPositionAxis(PapyrusVm(), ref, 0),
            RefPositionAxis(PapyrusVm(), ref, 1), RefPositionAxis(PapyrusVm(), ref, 2));
    }
    ForEachHighActor([](void* actor) {
        void* foe = IsDead(actor) ? nullptr : CombatTargetOf(actor);
        if (!foe) return;
        const auto* state = reinterpret_cast<const std::uint32_t*>(
            static_cast<const char*>(actor) + ActorField(ids::kOffActorState1));
        Log("ai: TEMP   %08X fights %08X at (%g, %g, %g), swim %d, state %08X %08X", FormIdOf(actor),
            FormIdOf(foe), RefPositionAxis(PapyrusVm(), actor, 0), RefPositionAxis(PapyrusVm(), actor, 1),
            RefPositionAxis(PapyrusVm(), actor, 2), SwimmingFlag(actor), state[0], state[1]);
        if (foe == PlayerRef()) Log("ai: TEMP     %08X on the player: %s", FormIdOf(actor), TempCrimeNote(actor).c_str());
    });
}

// Ends the walk, arrived or not, and gives the player back the controls.
void PlayerWalkStop() {
    LogPlayerFollowList();
    PendingWalk& walk = Walk();
    walk.settleTicks = 0;
    void* player = PlayerRef();
    void* pack = player && g_currentPackage ? g_currentPackage(PapyrusVm(), 0, player) : nullptr;
    Log("ai: the player's walk ends at (%g, %g), running PACK %08X",
        RefPositionAxis(PapyrusVm(), player, 0), RefPositionAxis(PapyrusVm(), player, 1),
        pack ? FormIdOf(pack) : 0);
    if (walk.steered) {
        walk.steered = false;
        SteerTick(false, 0.0f, 0.0f, 0.0f, 0.0f);
        SetSteerPace(false);
        return;
    }
    StopAiPackage(kPlayerId);
    SetPlayerAiDriven(false);
}

// Whether the walk has arrived, which ends it.
bool PlayerWalkDone() {
    PendingWalk& walk = Walk();
    if (walk.settleTicks > 0) {
        if (--walk.settleTicks == 0) AiTravelTo(kPlayerId, walk.x, walk.y, walk.z);
        return false;
    }
    if (++walk.ticks % kWalkLogTicks == 0) {
        void* player = PlayerRef();
        void* pack = player && g_currentPackage ? g_currentPackage(PapyrusVm(), 0, player) : nullptr;
        void* foe = player ? CombatTargetOf(player) : nullptr;
        const auto it = AiDestinations().find(kPlayerId);
        void* marker = it == AiDestinations().end() ? nullptr : RefByRuntimeId(it->second);
        Log("ai: the player walks at (%g, %g, %g), PACK %08X, fighting %08X, marker at (%g, %g)",
            RefPositionAxis(PapyrusVm(), player, 0), RefPositionAxis(PapyrusVm(), player, 1),
            RefPositionAxis(PapyrusVm(), player, 2), pack ? FormIdOf(pack) : 0,
            foe ? FormIdOf(foe) : 0, marker ? RefPositionAxis(PapyrusVm(), marker, 0) : 0.0f,
            marker ? RefPositionAxis(PapyrusVm(), marker, 1) : 0.0f);
    }
    if (walk.steered ? !ReachedSteerPoint(walk) : !ReachedAiDestination(kPlayerId)) {
        if (walk.backing) SteerTick(true, walk.startX, walk.startY, walk.startX, walk.startY);
        else if (walk.steered) SteerTick(true, walk.startX, walk.startY, walk.x, walk.y);
        return false;
    }
    PlayerWalkStop();
    return true;
}

void InstallAiCalls(GameHooks& hooks) {
    hooks.followerCount = FollowerCount;
    hooks.followerNames = FollowerNames;    g_playerAiDriven = Native<AiDrivenFn>("Game.SetPlayerAIDriven", ids::kGameSetPlayerAIDriven);
    g_forceThirdPerson = Native<GlobalCallFn>("Game.ForceThirdPerson", ids::kGameForceThirdPerson);
    g_forceFirstPerson = Native<GlobalCallFn>("Game.ForceFirstPerson", ids::kGameForceFirstPerson);
    hooks.playerWalk = PlayerWalkTo;
    hooks.playerSteer = PlayerSteerTo;
    hooks.playerWalkStuck = PlayerWalkStuck;
    hooks.playerWalkDone = PlayerWalkDone;
    hooks.playerWalkStop = PlayerWalkStop;
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
    g_combatState = Native<CombatStateFn>("Actor.GetCombatState", ids::kActorCombatState);
    g_evaluatePackage = Native<RefCallFn>("Actor.EvaluatePackage",
                                          ids::kActorEvaluatePackage);
    g_followerList = reinterpret_cast<FollowerListFn>(Resolve(
        "ExtraDataList::GetFollower", ids::kExtraListFollower, nullptr));
    InstallFollowSpacing();
    hooks.aiTravel = AiTravelTo;
    hooks.aiWander = AiWanderAt;
    hooks.aiFollow = AiFollowActor;
    hooks.aiEscort = AiEscortActor;
    hooks.aiActivate = AiActivateObject;
    hooks.currentPackage = CurrentAiPackageOf;
    hooks.packageDone = AiPackageDoneFor;
    hooks.applyAiSetting = ApplyAiSetting;
    ReportAliasLayoutMismatch();
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
