#include "game_calls.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <thread>

#include "activation.h"
#include "addresses.h"
#include "conversation.h"
#include "dialogue_state.h"
#include "ids.h"
#include "log.h"
#include "main_thread.h"
#include "object_script.h"
#include "script_tables.h"

namespace mwruntime {

namespace {

// 🛑 NOT the Papyrus native. The console's `setstage` starts a stopped quest
// SYNCHRONOUSLY and then sets the stage directly; Quest.SetCurrentStageID only
// queues the start, and the paused game never promotes it while the dialogue
// menu is open.
// See: docs/commentary/morrowind_runtime.md#objectives-must-be-displayed
using EnsureStartedFn = bool (*)(void* quest, bool* justStarted, bool startNow);
using GetStageFn = void* (*)(void* quest, std::uint32_t index);
using SetStageFn = bool (*)(void* quest, std::uint32_t index);
// The objective pair the console's `setobjectivedisplayed` calls directly;
// a hook on the Papyrus Quest.SetObjectiveDisplayed recorded ZERO hits while
// that command changed the state.
//   BGSQuestObjective* TESQuest::GetObjective(quest, u16 index)
//   void BGSQuestObjective::SetState(objective, u32 state)
using GetObjectiveFn = void* (*)(void* quest, std::uint32_t index);
using SetObjectiveStateFn = void (*)(void* objective, std::uint32_t state);
// Papyrus natives, as the VM calls them: VM, stack id, self, then arguments.
using IsRunningFn = bool (*)(void* vm, std::uint32_t stack, void* quest);
using AddItemFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                           void* item, std::int32_t count, bool silent);
using RemoveItemFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                              void* item, std::int32_t count, bool silent,
                              void* moveTo);
using ItemCountFn = std::int32_t (*)(void* vm, std::uint32_t stack, void* ref,
                                     void* item);
using GetPlayerFn = void* (*)(void* vm, std::uint32_t stack, void* tag);
using StartCombatFn = void (*)(void* vm, std::uint32_t stack, void* actor,
                               void* target);
using StopCombatFn = void (*)(void* vm, std::uint32_t stack, void* actor);
using SetEnabledFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                              bool fade);
using IsDisabledFn = bool (*)(void* vm, std::uint32_t stack, void* ref);
// One axis of a reference's position or rotation; the setters take all three.
using AxisGetFn = float (*)(void* vm, std::uint32_t stack, void* ref);
using AxisSetFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                           float x, float y, float z);
using ActivateFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                            void* actionRef, bool defaultOnly);
using LockFn = void (*)(void* vm, std::uint32_t stack, void* ref, bool lock,
                        bool asOffLimits);
using SetLockLevelFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                                std::int32_t level);
using RefQueryFn = bool (*)(void* vm, std::uint32_t stack, void* ref);
using RefCallFn = void (*)(void* vm, std::uint32_t stack, void* ref);
using DistanceFn = float (*)(void* vm, std::uint32_t stack, void* ref,
                             void* other);
using ParentCellFn = void* (*)(void* vm, std::uint32_t stack, void* ref);
// The `name` is a BSFixedString handed by address, as Game.GetFormFromFile
// takes its file name.
using GetValueFn = float (*)(void* vm, std::uint32_t stack, void* actor,
                             void* name);
using SetValueFn = void (*)(void* vm, std::uint32_t stack, void* actor,
                            void* name, float value);
using EquipItemFn = void (*)(void* vm, std::uint32_t stack, void* actor,
                             void* item, bool preventRemoval, bool silent);
// Barter and persuasion: a call on an actor, an int read off one, and the
// GLOBAL Game.AdvanceSkill, whose self is a tag and whose float rides fifth.
using ActorCallFn = void (*)(void* vm, std::uint32_t stack, void* actor);
using ActorIntFn = std::int32_t (*)(void* vm, std::uint32_t stack, void* actor);
using AdvanceSkillFn = void (*)(void* vm, std::uint32_t stack, void* tag,
                                void* name, float amount);

// Debug.MessageBox(string), global: no `self`, the text in the last argument.
using MessageBoxFn = void (*)(void* vm, std::uint32_t stack, void* tag,
                              void* text);

// Game.GetForm(int formId) -> the form, global so its self is a tag.
using GetFormFn = void* (*)(void* vm, std::uint32_t stack, void* tag,
                            std::int32_t formId);

// ObjectReference.PlaceAtMe(Form, int, bool, bool) -> the new reference.
using PlaceAtMeFn = void* (*)(void* vm, std::uint32_t stack, void* self,
                              void* base, std::int32_t count, bool persist,
                              bool disabled);

// ObjectReference.MoveTo(target, xOff, yOff, zOff, matchRotation): the only
// call that crosses cells, and it aims at another REFERENCE.
using MoveToFn = void (*)(void* vm, std::uint32_t stack, void* self,
                          void* target, float x, float y, float z,
                          bool matchRotation);

// ObjectReference.GetScale() / .SetScale(float).
using ScaleGetFn = float (*)(void* vm, std::uint32_t stack, void* ref);
using ScaleSetFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                            float scale);

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
// Weather.GetCurrentWeather() is GLOBAL, so its self is a tag; the
// classification is then read off the returned Weather form.
using CurrentWeatherFn = void* (*)(void* vm, std::uint32_t stack, void* tag);
using ClassificationFn = std::int32_t (*)(void* vm, std::uint32_t stack,
                                          void* weather);

// Sound.Play(ObjectReference) -> the playback instance id, 0 on failure. A
// MEMBER function, so the SNDR form is `self`. Sound.StopInstance(int) and
// Sound.SetInstanceVolume(int, float) are global, so their self is a tag.
using SoundPlayFn = std::int32_t (*)(void* vm, std::uint32_t stack,
                                     void* sound, void* source);
using StopInstanceFn = void (*)(void* vm, std::uint32_t stack, void* tag,
                                std::int32_t instance);
using InstanceVolumeFn = void (*)(void* vm, std::uint32_t stack, void* tag,
                                  std::int32_t instance, float volume);

// Skyrim's actor values for OpenMW's dynamic stats, in OpenMW's order.
constexpr const char* kDynamicNames[] = {"Health", "Magicka", "Stamina"};
constexpr int kDynamicCount = 3;

// XMarker, Skyrim.esm 0x3B: the invisible anchor a travel or sandbox package
// aims at, since a package destination cannot be raw coordinates.
constexpr std::uint32_t kXMarker = 0x3B;

// The TES3 package kinds, in OpenMW's AiPackageTypeId order, which is what
// `GetCurrentAiPackage` answers with. The alias prefix is the kind's name.
constexpr const char* kAiKinds[] = {"wander", "travel", "escort", "follow",
                                    "activate"};
constexpr int kAiKindCount = 5;

// TES3's gold, and the record Skyrim keeps for the same thing.
constexpr const char* kTes3Gold = "gold_001";
constexpr std::uint32_t kSkyrimGold = 0xF;

// The id MWScript uses for the player.
constexpr const char* kPlayerId = "player";

constexpr std::uint32_t kLocalMask = 0x00FFFFFF;

// BGSQuestObjective states, read off the console handler and the natives:
// `SetObjectiveDisplayed` passes 1, and `SetObjectiveCompleted` passes 3 when
// the objective is currently displayed and 2 when it is not.
constexpr std::uint32_t kObjectiveDisplayed = 1;
constexpr std::uint32_t kObjectiveCompleted = 3;

// The objective's current state, at BGSQuestObjective+0x1f.
constexpr std::size_t kOffObjectiveState = 0x1f;

// TESQuest+0xdc flag byte, bit 0 = running; TESQuest+0xe0 event scope, -1 =
// none. A stopped quest with a scope belongs to the Story Manager, and the
// console refuses to start it. A stage's flag byte at +2, bit 1 = start-up
// stage, which TESQuest::Start has already run when the quest just started.
constexpr std::size_t  kOffQuestFlags = 0xdc;
constexpr std::uint8_t kQuestRunning = 1;
constexpr std::size_t  kOffQuestEventScope = 0xe0;
constexpr std::size_t  kOffStageFlags = 2;
constexpr std::uint8_t kStageStartUp = 2;

// How often a displayed objective re-checks that its quest has finished
// starting, and how long it keeps checking. The wait sleeps OFF the game
// thread: a task that reposts itself drains in the same pump sweep and never
// lets a frame pass, which froze the game for the whole wait.
constexpr int kStartPollMs = 50;
constexpr int kStartWaitMs = 60000;

IsRunningFn         g_isRunning = nullptr;
EnsureStartedFn     g_ensureStarted = nullptr;
GetStageFn          g_getStage = nullptr;
SetStageFn          g_setStage = nullptr;
GetObjectiveFn      g_getObjective = nullptr;
SetObjectiveStateFn g_setObjectiveState = nullptr;
AddItemFn    g_addItem = nullptr;
RemoveItemFn g_removeItem = nullptr;
ItemCountFn  g_itemCount = nullptr;
GetPlayerFn  g_getPlayer = nullptr;
StartCombatFn g_startCombat = nullptr;
StopCombatFn  g_stopCombat = nullptr;
SetEnabledFn  g_enable = nullptr;
SetEnabledFn  g_disable = nullptr;
IsDisabledFn  g_isDisabled = nullptr;
ActivateFn     g_activate = nullptr;
LockFn         g_lock = nullptr;
SetLockLevelFn g_setLockLevel = nullptr;
RefQueryFn     g_isLocked = nullptr;
RefCallFn      g_delete = nullptr;
DistanceFn     g_distance = nullptr;

// x, y, z.
constexpr int kAxisCount = 3;
AxisGetFn g_getPosition[kAxisCount] = {nullptr, nullptr, nullptr};
// The rotation field's offset per axis; there is no native to call.
// See: docs/commentary/morrowind_runtime.md#the-angle-getters-have-no-id
constexpr std::size_t kRotOffset[kAxisCount] = {
    ids::kOffRefRotX, ids::kOffRefRotY, ids::kOffRefRotZ};

// Radians per degree, for the conversion the absent getters used to do.
constexpr float kDegreesPerRadian = 57.2957795f;

// One rotation axis in DEGREES, read straight off the reference. The axis is
// clamped here rather than through SafeAxis, which is declared further down.
float RefAngle(void* ref, int axis) {
    if (!ref) return 0.0f;
    const int which = axis < 0 || axis >= kAxisCount ? 0 : axis;
    const float radians = *reinterpret_cast<const float*>(
        static_cast<const char*>(ref) + kRotOffset[which]);
    return radians * kDegreesPerRadian;
}
AxisSetFn g_setPosition = nullptr;
AxisSetFn g_setAngle = nullptr;
ParentCellFn   g_parentCell = nullptr;
RefQueryFn     g_isInterior = nullptr;
GetValueFn     g_getValue = nullptr;
SetValueFn     g_setValue = nullptr;
SetValueFn     g_restoreValue = nullptr;
SetValueFn     g_damageValue = nullptr;
EquipItemFn    g_equipItem = nullptr;
ActorCallFn    g_showBarterMenu = nullptr;
ActorIntFn     g_getLevel = nullptr;
GetValueFn     g_getValuePercent = nullptr;
AdvanceSkillFn g_advanceSkill = nullptr;
MessageBoxFn   g_messageBox = nullptr;
PlaceAtMeFn    g_placeAtMe = nullptr;
RefQueryFn     g_isDead = nullptr;
GetFormFn      g_getForm = nullptr;
RefQueryFn     g_is3DLoaded = nullptr;
SoundPlayFn      g_soundPlay = nullptr;
StopInstanceFn   g_stopInstance = nullptr;
InstanceVolumeFn g_instanceVolume = nullptr;
MoveToFn     g_moveTo = nullptr;
ScaleGetFn   g_getScale = nullptr;
ScaleSetFn   g_setScale = nullptr;
GetAliasFn       g_questGetAlias = nullptr;
ForceRefToFn     g_forceRefTo = nullptr;
AliasRefFn       g_aliasReference = nullptr;
RefCallFn        g_aliasClear = nullptr;
CurrentPackageFn g_currentPackage = nullptr;
PairQueryFn      g_hasLos = nullptr;
PairQueryFn      g_isDetectedBy = nullptr;
CombatTargetFn   g_combatTarget = nullptr;
RefQueryFn       g_weaponDrawn = nullptr;
RefQueryFn       g_isSneaking = nullptr;
RefQueryFn       g_actorRunning = nullptr;
ResurrectFn      g_resurrect = nullptr;
DropObjectFn     g_dropObject = nullptr;
CurrentWeatherFn g_currentWeather = nullptr;
ClassificationFn g_classification = nullptr;

std::string g_speakerId;
void*       g_speakerRef = nullptr;

// The objective each quest is currently showing, so the previous one can be
// hidden when the quest moves on.
std::map<std::string, int> g_shownObjective;

// Ids already reported as unresolvable, so the log names each once.
std::set<std::string> g_reported;

std::string Lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(),
                   [](unsigned char c) { return static_cast<char>(::tolower(c)); });
    return text;
}

void ReportOnce(const char* what, const std::string& id) {
    if (g_reported.insert(what + id).second) {
        Log("game: %s '%s' does not resolve in this load order", what,
            id.c_str());
    }
}

void* Form(const FormRef* ref) {
    return ref ? FormFromFile(ref->plugin.c_str(), ref->formId & kLocalMask)
               : nullptr;
}

void* ItemForm(const std::string& item) {
    if (Lower(item) == kTes3Gold) {
        return FormFromFile(ids::kSkyrimMaster, kSkyrimGold);
    }
    void* form = Form(FindItem(item));
    if (!form) ReportOnce("item", item);
    return form;
}

// The reference a command acts on: the player, the NPC being spoken to, or
// any id the plugin places, so `"TR_m3_Yak gro-Yam"->Enable` reaches a real
// reference rather than reporting.
//
// 🛑 The speaker is tried BEFORE the table. An actor placed more than once
// resolves to its first placement there, which is the wrong one while you are
// standing in front of a different instance of it.
// See: docs/commentary/morrowind_runtime.md#placed-references
void* OwnerRef(const std::string& owner) {
    if (Lower(owner) == kPlayerId) {
        return g_getPlayer ? g_getPlayer(PapyrusVm(), 0, nullptr) : nullptr;
    }
    if (g_speakerRef && Lower(owner) == Lower(g_speakerId)) return g_speakerRef;
    if (void* placed = Form(FindRef(owner))) return placed;
    ReportOnce("reference", owner);
    return nullptr;
}

// Sets one objective's state, and reports the state it actually holds after.
// Returns false when the quest carries no objective at that index.
//
// 🛑 The state is READ BACK from the objective rather than assumed. An earlier
// version logged success unconditionally and hid a total failure for several
// rounds.
bool SetObjectiveState(void* form, int index, std::uint32_t state) {
    if (!g_getObjective || !g_setObjectiveState) return false;
    void* objective = g_getObjective(form, static_cast<std::uint32_t>(index));
    if (!objective) return false;
    g_setObjectiveState(objective, state);
    return *(static_cast<std::uint8_t*>(objective) + kOffObjectiveState) ==
           static_cast<std::uint8_t>(state);
}

// Starts a stopped quest on the spot, as the console's `setstage` handler
// (0x30df30) does. Returns false for an event-scoped quest, which only the
// Story Manager may start. `justStarted` reports whether this call started it.
bool StartQuest(void* form, bool* justStarted) {
    if (!g_ensureStarted || !g_getStage || !g_setStage) return false;
    const auto* quest = static_cast<const std::uint8_t*>(form);
    const bool running = quest[kOffQuestFlags] & kQuestRunning;
    const auto scope =
        *reinterpret_cast<const std::int32_t*>(quest + kOffQuestEventScope);
    if (!running && scope != -1) return false;
    return g_ensureStarted(form, justStarted, true);
}

// Sets the stage, unless the start already ran it as a start-up stage.
// Returns false for a stage the quest does not have.
bool SetStage(void* form, int stage, bool justStarted) {
    const auto index = static_cast<std::uint32_t>(stage);
    const auto* item = static_cast<const std::uint8_t*>(g_getStage(form, index));
    if (!item) return false;
    if (justStarted && (item[kOffStageFlags] & kStageStartUp)) return true;
    return g_setStage(form, index);
}

// Displays the objective for a stage, completing the previous one.
void ShowObjective(void* form, const std::string& quest, int stage) {
    const auto previous = g_shownObjective.find(Lower(quest));
    if (previous != g_shownObjective.end() && previous->second != stage) {
        SetObjectiveState(form, previous->second, kObjectiveCompleted);
    }
    const bool shown = SetObjectiveState(form, stage, kObjectiveDisplayed);
    g_shownObjective[Lower(quest)] = stage;
    Log("game: objective %d for %s -> %s", stage, quest.c_str(),
        shown ? "DISPLAYED" : "NOT displayed (state did not take)");
}

// 🛑 Sets the stage and displays its objective only once the quest reports
// IsRunning, which is false from the synchronous start until the StoryTeller
// finishes it on a later unpaused frame -- after the menu closes, since the
// menu pauses the game. Both the stage's log entry and the objective are
// filed under the quest's CURRENT instance: set in the start's own trip they
// land on instance 0, the finished quest holds instance 1, and the journal
// shows no text and lists the quest as done. Papyrus SetCurrentStageID defers
// the stage the same way when it just started the quest.
// See: docs/commentary/morrowind_runtime.md#objectives-must-be-displayed
void StageOnceRunning(void* form, const std::string& quest, int stage,
                      bool justStarted, int waited) {
    if (!g_isRunning || g_isRunning(PapyrusVm(), 0, form)) {
        const bool ok = SetStage(form, stage, justStarted);
        Log("game: SetStage %s %d -> %s", quest.c_str(), stage,
            ok ? "ok" : "no such stage");
        if (ok) ShowObjective(form, quest, stage);
        return;
    }
    if (waited >= kStartWaitMs) {
        Log("game: quest '%s' never finished starting -- stage %d not set",
            quest.c_str(), stage);
        return;
    }
    std::thread([form, quest, stage, justStarted, waited]() {
        std::this_thread::sleep_for(std::chrono::milliseconds(kStartPollMs));
        PostToMainThread([form, quest, stage, justStarted, waited]() {
            StageOnceRunning(form, quest, stage, justStarted,
                             waited + kStartPollMs);
        });
    }).detach();
}

// 🛑 Setting the stage SHOWS nothing on its own: an objective is invisible
// until it is displayed, and vanilla does that from a stage fragment a
// generated QUST does not have. The objective index is the journal index.
//
// 🛑 The earlier step is COMPLETED, never hidden. A Morrowind journal is a
// running log the player reads back, so every entry stays -- completing it
// strikes it through and keeps it, which is what Skyrim does for its own
// multi-step quests.
// See: docs/commentary/morrowind_runtime.md#objectives-must-be-displayed
void SetQuestStage(const std::string& quest, int stage) {
    const FormRef* ref = FindQuest(quest);
    if (!ref) {
        ReportOnce("journal quest", quest);
        return;
    }
    // Resolved INSIDE the task: Game.GetFormFromFile is an engine call, and
    // the dialogue menu's callbacks do not run on the game's thread.
    const std::string named = quest;
    const std::string plugin = ref->plugin;
    const std::uint32_t local = ref->formId & kLocalMask;
    const bool posted = PostToMainThread([plugin, local, named, stage]() {
        void* form = FormFromFile(plugin.c_str(), local);
        if (!form) {
            Log("game: journal '%s' did not resolve on the game thread",
                named.c_str());
            return;
        }
        bool justStarted = false;
        const bool ok = StartQuest(form, &justStarted);
        Log("game: start %s -> %s (form %p, just started %d)", named.c_str(),
            ok ? "ok" : "refused", form, justStarted);
        if (ok) StageOnceRunning(form, named, stage, justStarted, 0);
    });
    if (!posted) {
        Log("game: no task interface -- journal '%s' stage %d DROPPED",
            quest.c_str(), stage);
    }
}

void AddItem(const std::string& owner, const std::string& item, int count) {
    void* ref = OwnerRef(owner);
    void* form = ItemForm(item);
    if (ref && form && g_addItem) {
        g_addItem(PapyrusVm(), 0, ref, form, count, false);
    }
}

void RemoveItem(const std::string& owner, const std::string& item, int count) {
    void* ref = OwnerRef(owner);
    void* form = ItemForm(item);
    if (ref && form && g_removeItem) {
        g_removeItem(PapyrusVm(), 0, ref, form, count, false, nullptr);
    }
}

// 🛑 Posted to the game thread rather than called here: the dialogue menu's
// callbacks do not run on it, and combat reaches deep into the AI process.
// An empty `target` means StopCombat, which takes no target at all.
void SetCombat(const std::string& attacker, const std::string& target) {
    void* actor = OwnerRef(attacker);
    void* foe = target.empty() ? nullptr : OwnerRef(target);
    if (!actor || (!target.empty() && !foe)) return;
    PostToMainThread([actor, foe]() {
        if (foe) {
            if (g_startCombat) g_startCombat(PapyrusVm(), 0, actor, foe);
        } else if (g_stopCombat) {
            g_stopCombat(PapyrusVm(), 0, actor);
        }
    });
}

// 🛑 Posted, like combat: enabling a reference moves it in and out of the
// world, which is not safe from the menu's own thread. TES3 fades nothing,
// so both pass false.
void SetEnabled(const std::string& id, bool enabled) {
    void* ref = OwnerRef(id);
    if (!ref) return;
    PostToMainThread([ref, enabled]() {
        SetEnabledFn call = enabled ? g_enable : g_disable;
        if (call) call(PapyrusVm(), 0, ref, false);
    });
}

// Read directly rather than posted: a condition needs the answer NOW, and
// reading the flag does not touch the world.
bool IsDisabled(const std::string& id) {
    void* ref = OwnerRef(id);
    return ref && g_isDisabled && g_isDisabled(PapyrusVm(), 0, ref);
}

int ItemCount(const std::string& owner, const std::string& item) {
    void* ref = OwnerRef(owner);
    void* form = ItemForm(item);
    if (!ref || !form || !g_itemCount) return 0;
    return g_itemCount(PapyrusVm(), 0, ref, form);
}

void* PlayerRef() {
    return g_getPlayer ? g_getPlayer(PapyrusVm(), 0, nullptr) : nullptr;
}

// World mutations are POSTED, as Enable and combat are: the menu's callbacks
// do not run on the game thread. Reads answer at once.
void Activate(const std::string& id) {
    void* ref = OwnerRef(id);
    void* player = PlayerRef();
    if (!ref || !player || !g_activate) return;
    PostToMainThread([ref, player]() {
        g_activate(PapyrusVm(), 0, ref, player, false);
    });
}

// A positive level locks at that level, zero re-locks at the current one,
// and a negative level unlocks -- OpenMW's `Lock`/`Unlock` in one call.
void SetLocked(const std::string& id, int level) {
    void* ref = OwnerRef(id);
    if (!ref || !g_lock) return;
    PostToMainThread([ref, level]() {
        if (level > 0 && g_setLockLevel) {
            g_setLockLevel(PapyrusVm(), 0, ref, level);
        }
        g_lock(PapyrusVm(), 0, ref, level >= 0, false);
    });
}

bool IsLocked(const std::string& id) {
    void* ref = OwnerRef(id);
    return ref && g_isLocked && g_isLocked(PapyrusVm(), 0, ref);
}

void DeleteRef(const std::string& id) {
    void* ref = OwnerRef(id);
    if (!ref || !g_delete) return;
    PostToMainThread([ref]() { g_delete(PapyrusVm(), 0, ref); });
}

float Distance(const std::string& from, const std::string& to) {
    void* a = OwnerRef(from);
    void* b = OwnerRef(to);
    return a && b && g_distance ? g_distance(PapyrusVm(), 0, a, b) : 0.0f;
}

// An axis index from a script, kept inside the array whatever it says.
int SafeAxis(int axis) {
    return axis < 0 || axis >= kAxisCount ? 0 : axis;
}

float Position(const std::string& id, int axis) {
    void* ref = OwnerRef(id);
    AxisGetFn get = g_getPosition[SafeAxis(axis)];
    return ref && get ? get(PapyrusVm(), 0, ref) : 0.0f;
}

float Angle(const std::string& id, int axis) {
    return RefAngle(OwnerRef(id), axis);
}

// 🛑 The natives take ALL THREE axes, so the other two are read back first
// and written unchanged. Posted, like every other call that moves something:
// the menu's callbacks do not run on the game thread.
void SetAxis(const std::string& id, int axis, float value, bool isAngle) {
    void* ref = OwnerRef(id);
    AxisSetFn set = isAngle ? g_setAngle : g_setPosition;
    if (!ref || !set) return;
    float xyz[kAxisCount];
    for (int i = 0; i < kAxisCount; ++i) {
        xyz[i] = isAngle ? RefAngle(ref, i)
                         : (g_getPosition[i]
                                ? g_getPosition[i](PapyrusVm(), 0, ref)
                                : 0.0f);
    }
    xyz[SafeAxis(axis)] = value;
    const float x = xyz[0], y = xyz[1], z = xyz[2];
    PostToMainThread([ref, set, x, y, z]() {
        set(PapyrusVm(), 0, ref, x, y, z);
    });
}

void SetPosition(const std::string& id, int axis, float value) {
    SetAxis(id, axis, value, false);
}

void SetAngle(const std::string& id, int axis, float value) {
    SetAxis(id, axis, value, true);
}

void* PlayerCell() {
    void* player = PlayerRef();
    return player && g_parentCell ? g_parentCell(PapyrusVm(), 0, player)
                                  : nullptr;
}

std::string PlayerCellName() {
    void* cell = PlayerCell();
    if (!cell) return std::string();
    const char* name = *reinterpret_cast<const char**>(
        static_cast<char*>(cell) + ids::kOffCellFullName);
    return name ? name : "";
}

bool PlayerInInterior() {
    void* cell = PlayerCell();
    return cell && g_isInterior && g_isInterior(PapyrusVm(), 0, cell);
}

float DynamicStat(const std::string& actor, int which) {
    void* ref = OwnerRef(actor);
    void* name = nullptr;
    if (!ref || !g_getValue || which < 0 || which >= kDynamicCount ||
        !FixedString(&name, kDynamicNames[which])) {
        return 0.0f;
    }
    return g_getValue(PapyrusVm(), 0, ref, &name);
}

void SetDynamicStat(const std::string& actor, int which, float value) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_setValue || which < 0 || which >= kDynamicCount) return;
    PostToMainThread([ref, which, value]() {
        void* name = nullptr;
        if (FixedString(&name, kDynamicNames[which])) {
            g_setValue(PapyrusVm(), 0, ref, &name, value);
        }
    });
}

// Skyrim keeps restore and damage apart where TES3 has one signed `Mod`.
void ModDynamicStat(const std::string& actor, int which, float delta) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_restoreValue || !g_damageValue || which < 0 ||
        which >= kDynamicCount) {
        return;
    }
    PostToMainThread([ref, which, delta]() {
        void* name = nullptr;
        if (!FixedString(&name, kDynamicNames[which])) return;
        if (delta >= 0.0f) {
            g_restoreValue(PapyrusVm(), 0, ref, &name, delta);
        } else {
            g_damageValue(PapyrusVm(), 0, ref, &name, -delta);
        }
    });
}

void EquipItem(const std::string& actor, const std::string& item) {
    void* ref = OwnerRef(actor);
    void* form = ItemForm(item);
    if (!ref || !form || !g_equipItem) return;
    PostToMainThread([ref, form]() {
        g_equipItem(PapyrusVm(), 0, ref, form, false, false);
    });
}

bool MenuMode() { return ConversationOpen(); }

// Posted rather than begun here: BeginConversation tears the current one
// down, and the script asking for it is still running against that actor.
void ForceGreeting(const std::string& actor) {
    void* ref = OwnerRef(actor);
    if (!ref) return;
    const ActorDef* def = FindActor(actor);
    const std::string shown = def && !def->name.empty() ? def->name : actor;
    PostToMainThread([actor, ref, shown]() {
        SetSpeakerRef(actor.c_str(), ref);
        BeginConversation(actor.c_str(), shown.c_str(), PlayerName());
    });
}

// ------------------------------------------------ barter and persuasion

int PlayerLevel() {
    void* player = PlayerRef();
    return player && g_getLevel ? g_getLevel(PapyrusVm(), 0, player) : 1;
}

// A Skyrim actor value by name, read now: the formula wants the number.
float ActorValue(const std::string& actor, const char* valueName) {
    void* ref = OwnerRef(actor);
    void* name = nullptr;
    if (!ref || !g_getValue || !FixedString(&name, valueName)) return 0.0f;
    return g_getValue(PapyrusVm(), 0, ref, &name);
}

// The same value as a fraction of its maximum, 0..1.
float StatPercent(const std::string& actor, const char* valueName) {
    void* ref = OwnerRef(actor);
    void* name = nullptr;
    if (!ref || !g_getValuePercent || !FixedString(&name, valueName)) {
        return 1.0f;
    }
    return g_getValuePercent(PapyrusVm(), 0, ref, &name);
}

// Posted: advancing a skill can level the player up, with all that opens.
void AdvanceSkill(const char* skill, float amount) {
    if (!g_advanceSkill) return;
    const std::string named = skill;
    PostToMainThread([named, amount]() {
        void* name = nullptr;
        if (FixedString(&name, named.c_str())) {
            g_advanceSkill(PapyrusVm(), 0, nullptr, &name, amount);
        }
    });
}

// `PlaceAtPC id count` and `PlaceAtMe`: creates references of a BASE record
// beside `near` -- the player for the PC form, any reference for the other.
//
// 🛑 The new reference has no authored placement, so nothing in
// SCPT_instances.txt names it. Its instance is bound from the FormID PlaceAtMe
// RETURNS, which is the only way a spawned creature's script ever runs.
// See: docs/plans/morrowind_object_scripts.md#placeatpc
void PlaceNear(const std::string& near, const std::string& base, int count) {
    const FormRef* ref = FindBase(base);
    if (!ref || !g_placeAtMe) {
        Log("game: place '%s' -- %s", base.c_str(),
            ref ? "PlaceAtMe unresolved" : "no such base record");
        return;
    }
    void* at = OwnerRef(near);
    if (!at) return;
    const FormRef target = *ref;
    const std::string id = base;
    PostToMainThread([target, id, count, at]() {
        void* form = Form(&target);
        if (!form) return;
        void* made = g_placeAtMe(PapyrusVm(), 0, at, form,
                                 count > 0 ? count : 1, false, false);
        if (!made) {
            Log("game: placing '%s' created nothing", id.c_str());
            return;
        }
        BindSpawnedInstance(FormIdOf(made), id);
    });
}

// Sets all three position axes at once, which every absolute move needs --
// SetPosition takes the whole vector and SetAxis only ever changes one.
void PlaceAt(void* ref, float x, float y, float z, float zRot) {
    if (g_setPosition) g_setPosition(PapyrusVm(), 0, ref, x, y, z);
    if (!g_setAngle) return;
    const float ax = RefAngle(ref, 0);
    const float ay = RefAngle(ref, 1);
    g_setAngle(PapyrusVm(), 0, ref, ax, ay, zRot);
}

// `PositionCell x y z zRot "cell"`: the anchor carries the move across the
// cell boundary and the position puts the reference where the script asked.
//
// 🛑 MoveTo is the ONLY call that changes an object's cell, and it aims at
// another REFERENCE -- hence the staged anchor. Both steps are posted
// together so the reference is never seen at the anchor's own spot.
// See: docs/commentary/morrowind_runtime.md#positioncell-needs-an-anchor
void MoveRefToCell(const std::string& id, const std::string& cell, float x,
                   float y, float z, float zRot) {
    const FormRef* anchor = FindCellAnchor(cell);
    if (!anchor || !g_moveTo) {
        if (!anchor) ReportOnce("cell", cell);
        return;
    }
    void* ref = OwnerRef(id);
    if (!ref) return;
    const FormRef target = *anchor;
    PostToMainThread([ref, target, x, y, z, zRot]() {
        void* to = Form(&target);
        if (!to) return;
        g_moveTo(PapyrusVm(), 0, ref, to, 0.0f, 0.0f, 0.0f, false);
        PlaceAt(ref, x, y, z, zRot);
    });
}

// `Position x y z zRot`: the same without the cell change.
void MoveRefInCell(const std::string& id, float x, float y, float z,
                   float zRot) {
    void* ref = OwnerRef(id);
    if (!ref) return;
    PostToMainThread([ref, x, y, z, zRot]() {
        PlaceAt(ref, x, y, z, zRot);
    });
}

// `Move`/`MoveWorld`: adds `delta` along one axis. `local` rotates the offset
// into the object's own frame, which is the whole difference between them --
// a Z rotation is all an upright object has, so that is what is applied.
void MoveRefBy(const std::string& id, int axis, float delta, bool local) {
    void* ref = OwnerRef(id);
    if (!ref || !g_setPosition) return;
    float offset[kAxisCount] = {0.0f, 0.0f, 0.0f};
    offset[SafeAxis(axis)] = delta;
    PostToMainThread([ref, offset, local]() {
        float x = offset[0], y = offset[1];
        if (local) {
            const float radians = RefAngle(ref, 2) / kDegreesPerRadian;
            x = offset[0] * std::cos(radians) - offset[1] * std::sin(radians);
            y = offset[0] * std::sin(radians) + offset[1] * std::cos(radians);
        }
        float at[kAxisCount];
        for (int i = 0; i < kAxisCount; ++i) {
            at[i] = g_getPosition[i] ? g_getPosition[i](PapyrusVm(), 0, ref)
                                     : 0.0f;
        }
        g_setPosition(PapyrusVm(), 0, ref, at[0] + x, at[1] + y,
                      at[2] + offset[2]);
    });
}

// `Rotate`/`RotateWorld`: adds `degrees` to one Euler angle.
void RotateRefBy(const std::string& id, int axis, float degrees) {
    void* ref = OwnerRef(id);
    if (!ref || !g_setAngle) return;
    const int which = SafeAxis(axis);
    PostToMainThread([ref, which, degrees]() {
        float at[kAxisCount];
        for (int i = 0; i < kAxisCount; ++i) {
            at[i] = RefAngle(ref, i);
        }
        at[which] += degrees;
        g_setAngle(PapyrusVm(), 0, ref, at[0], at[1], at[2]);
    });
}

float RefScale(const std::string& id) {
    void* ref = OwnerRef(id);
    return ref && g_getScale ? g_getScale(PapyrusVm(), 0, ref) : 1.0f;
}

void SetRefScale(const std::string& id, float value) {
    void* ref = OwnerRef(id);
    if (!ref || !g_setScale) return;
    PostToMainThread([ref, value]() {
        g_setScale(PapyrusVm(), 0, ref, value);
    });
}

// `PlaceItem`/`PlaceItemCell`: create the base at an absolute spot. An empty
// cell means the player's own, which is what the cell-less form does.
void PlaceBaseAt(const std::string& base, const std::string& cell, float x,
                 float y, float z, float zRot) {
    const FormRef* found = FindBase(base);
    if (!found || !g_placeAtMe) {
        if (!found) ReportOnce("base", base);
        return;
    }
    const FormRef target = *found;
    const FormRef* anchor = cell.empty() ? nullptr : FindCellAnchor(cell);
    const FormRef into = anchor ? *anchor : FormRef();
    const bool cross = anchor != nullptr;
    const std::string id = base;
    PostToMainThread([target, into, cross, id, x, y, z, zRot]() {
        void* player = PlayerRef();
        void* form = Form(&target);
        if (!player || !form) return;
        void* made = g_placeAtMe(PapyrusVm(), 0, player, form, 1, false,
                                 false);
        if (!made) return;
        if (cross && g_moveTo) {
            if (void* to = Form(&into)) {
                g_moveTo(PapyrusVm(), 0, made, to, 0.0f, 0.0f, 0.0f, false);
            }
        }
        PlaceAt(made, x, y, z, zRot);
        BindSpawnedInstance(FormIdOf(made), id);
    });
}

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

// `Face x y`: SetLookAt needs a REFERENCE to look at, and a bare point has
// none, so the actor is turned by its Z angle instead -- the same thing TES3
// means, since Face only ever turns an upright actor about Z.
void AiFacePoint(const std::string& actor, float x, float y) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_setAngle || !g_getPosition[0]) return;
    const float dx = x - g_getPosition[0](PapyrusVm(), 0, ref);
    const float dy = y - g_getPosition[1](PapyrusVm(), 0, ref);
    if (dx == 0.0f && dy == 0.0f) return;
    const float degrees = std::atan2(dx, dy) * 180.0f / 3.14159265f;
    const float ax = RefAngle(ref, 0);
    const float ay = RefAngle(ref, 1);
    g_setAngle(PapyrusVm(), 0, ref, ax, ay, degrees);
}

// `PlaySound3D sound` and its kin: plays a TES3 SOUN through the SNDR the
// import minted for it, from `ref` -- or from the player, which is where
// TES3's non-3D PlaySound puts a sound with no reference.
//
// 🛑 Returns Skyrim's playback INSTANCE id, which is the only handle
// StopSound and GetSoundPlaying have. Playing is posted to the main thread
// like every other engine call, so the id cannot be returned from the post;
// it is taken synchronously here because Sound.Play is a plain native and the
// caller needs the id in the same script step.
int PlaySoundAt(const std::string& ref, const std::string& sound, bool loop,
                float volume) {
    const FormRef* found = FindSound(sound);
    if (!found || !g_soundPlay) {
        if (!found) ReportOnce("sound", sound);
        return 0;
    }
    void* form = Form(found);
    void* source = ref.empty() ? PlayerRef() : OwnerRef(ref);
    if (!form || !source) return 0;
    const std::int32_t instance = g_soundPlay(PapyrusVm(), 0, form, source);
    if (instance && volume < 1.0f && g_instanceVolume) {
        g_instanceVolume(PapyrusVm(), 0, nullptr, instance, volume);
    }
    if (!instance) {
        Log("game: '%s' did not start (loop=%d)", sound.c_str(), loop ? 1 : 0);
    }
    return instance;
}

void StopSoundInstance(int instance) {
    if (g_stopInstance && instance) {
        g_stopInstance(PapyrusVm(), 0, nullptr, instance);
    }
}

// Whether a placement is a dead actor, for the tick's `OnDeath`. Reads at
// once: the tick already runs on the game thread, so there is nothing to post.
// 🛑 By RUNTIME FormID, so a reference `PlaceAtPC` created answers too -- it
// has no authored placement for GetFormFromFile to name.
//
// 🛑 RESOLVED EVERY TIME for a staged placement, never cached: the engine FREES
// a reference, and a cached pointer outlives it while the tick polls 15 times a
// second -- measured 2026-09-18, a PlaceAtPC creature died and the poll crashed
// 41 seconds later reading a flag off the freed actor.
//
void* RefByRuntimeId(std::uint32_t runtimeFormId) {
    if (!g_getForm || !runtimeFormId) return nullptr;
    return g_getForm(PapyrusVm(), 0, nullptr,
                     static_cast<std::int32_t>(runtimeFormId));
}

bool IsDeadRef(std::uint32_t runtimeFormId) {
    void* ref = RefByRuntimeId(runtimeFormId);
    return ref && g_isDead && g_isDead(PapyrusVm(), 0, ref);
}

// TES3's whole rule for when a local script runs: while its object is loaded.
bool Is3DLoadedRef(std::uint32_t runtimeFormId) {
    void* ref = RefByRuntimeId(runtimeFormId);
    return ref && g_is3DLoaded && g_is3DLoaded(PapyrusVm(), 0, ref);
}

// A `MessageBox` raised by an object script, which has no dialogue menu to
// render it. Debug.MessageBox is global, so it takes no `self`.
// See: docs/plans/morrowind_object_scripts.md#messagebox
void ShowMessage(const std::string& text) {
    if (!g_messageBox) {
        Log("game: MessageBox \"%s\" -- Debug.MessageBox unresolved",
            text.c_str());
        return;
    }
    PostToMainThread([text]() {
        void* message = nullptr;
        if (FixedString(&message, text.c_str())) {
            g_messageBox(PapyrusVm(), 0, nullptr, &message);
        }
    });
}

// Skyrim's own barter menu on the speaker, opened from the game thread over
// the dialogue -- the stacking a vanilla fragment's ShowBarterMenu uses.
// See: docs/commentary/morrowind_runtime.md#barter
void ShowBarterMenu(const std::string& actor) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_showBarterMenu) {
        Log("game: barter with '%s' -- %s", actor.c_str(),
            ref ? "ShowBarterMenu unresolved" : "no reference");
        return;
    }
    PostToMainThread([ref]() { g_showBarterMenu(PapyrusVm(), 0, ref); });
}

// Skyrim's gold, form 0xF of Skyrim.esm -- a bribe never touches Morrowind's.
void* GoldForm() { return FormFromFile(ids::kSkyrimMaster, kSkyrimGold); }

int GoldCount(const std::string& owner) {
    void* ref = OwnerRef(owner);
    void* gold = GoldForm();
    if (!ref || !gold || !g_itemCount) return 0;
    return g_itemCount(PapyrusVm(), 0, ref, gold);
}

void MoveGold(const std::string& from, const std::string& to, int count) {
    void* source = OwnerRef(from);
    void* target = OwnerRef(to);
    void* gold = GoldForm();
    if (!source || !target || !gold || !g_removeItem || !g_addItem) return;
    g_removeItem(PapyrusVm(), 0, source, gold, count, false, nullptr);
    g_addItem(PapyrusVm(), 0, target, gold, count, false);
}

template <typename Fn>
Fn Native(const char* name, std::uint64_t id) {
    return reinterpret_cast<Fn>(Resolve(name, id, nullptr));
}

}  // namespace

void InstallGameCalls() {
    g_isRunning = Native<IsRunningFn>("Quest.IsRunning", ids::kQuestIsRunning);
    g_ensureStarted = Native<EnsureStartedFn>("TESQuest::EnsureQuestStarted",
                                              ids::kQuestEnsureStarted);
    g_getStage = Native<GetStageFn>("TESQuest::GetStage", ids::kQuestGetStage);
    g_setStage = Native<SetStageFn>("TESQuest::SetStage", ids::kQuestSetStage);
    g_getObjective = Native<GetObjectiveFn>("TESQuest::GetObjective",
                                            ids::kQuestGetObjective);
    g_setObjectiveState = Native<SetObjectiveStateFn>(
        "BGSQuestObjective::SetState", ids::kQuestObjectiveSetState);
    g_addItem = Native<AddItemFn>("ObjectReference.AddItem", ids::kRefAddItem);
    g_removeItem = Native<RemoveItemFn>("ObjectReference.RemoveItem",
                                        ids::kRefRemoveItem);
    g_itemCount = Native<ItemCountFn>("ObjectReference.GetItemCount",
                                      ids::kRefGetItemCount);
    g_getPlayer = Native<GetPlayerFn>("Game.GetPlayer", ids::kGameGetPlayer);
    g_startCombat = Native<StartCombatFn>("Actor.StartCombat",
                                          ids::kActorStartCombat);
    g_stopCombat = Native<StopCombatFn>("Actor.StopCombat",
                                        ids::kActorStopCombat);
    g_enable = Native<SetEnabledFn>("ObjectReference.Enable", ids::kRefEnable);
    g_disable = Native<SetEnabledFn>("ObjectReference.Disable",
                                     ids::kRefDisable);
    g_isDisabled = Native<IsDisabledFn>("ObjectReference.IsDisabled",
                                        ids::kRefIsDisabled);
    g_activate = Native<ActivateFn>("ObjectReference.Activate",
                                    ids::kRefActivate);
    g_lock = Native<LockFn>("ObjectReference.Lock", ids::kRefLock);
    g_setLockLevel = Native<SetLockLevelFn>("ObjectReference.SetLockLevel",
                                            ids::kRefSetLockLevel);
    g_isLocked = Native<RefQueryFn>("ObjectReference.IsLocked",
                                    ids::kRefIsLocked);
    g_delete = Native<RefCallFn>("ObjectReference.Delete", ids::kRefDelete);
    g_distance = Native<DistanceFn>("ObjectReference.GetDistance",
                                    ids::kRefGetDistance);
    g_parentCell = Native<ParentCellFn>("ObjectReference.GetParentCell",
                                        ids::kRefGetParentCell);
    g_isInterior = Native<RefQueryFn>("Cell.IsInterior", ids::kCellIsInterior);
    g_getValue = Native<GetValueFn>("Actor.GetActorValue", ids::kActorGetValue);
    g_setValue = Native<SetValueFn>("Actor.SetActorValue", ids::kActorSetValue);
    g_restoreValue = Native<SetValueFn>("Actor.RestoreActorValue",
                                        ids::kActorRestoreValue);
    g_damageValue = Native<SetValueFn>("Actor.DamageActorValue",
                                       ids::kActorDamageValue);
    g_equipItem = Native<EquipItemFn>("Actor.EquipItem", ids::kActorEquipItem);
    g_getPosition[0] = Native<AxisGetFn>("ObjectReference.GetPositionX",
                                         ids::kRefGetPositionX);
    g_getPosition[1] = Native<AxisGetFn>("ObjectReference.GetPositionY",
                                         ids::kRefGetPositionY);
    g_getPosition[2] = Native<AxisGetFn>("ObjectReference.GetPositionZ",
                                         ids::kRefGetPositionZ);
    g_setPosition = Native<AxisSetFn>("ObjectReference.SetPosition",
                                      ids::kRefSetPosition);
    g_setAngle = Native<AxisSetFn>("ObjectReference.SetAngle",
                                   ids::kRefSetAngle);
    g_showBarterMenu = Native<ActorCallFn>("Actor.ShowBarterMenu",
                                           ids::kActorShowBarterMenu);
    g_getLevel = Native<ActorIntFn>("Actor.GetLevel", ids::kActorGetLevel);
    g_getValuePercent = Native<GetValueFn>("Actor.GetActorValuePercentage",
                                           ids::kActorGetValuePercent);
    g_messageBox = Native<MessageBoxFn>("Debug.MessageBox",
                                        ids::kDebugMessageBox);
    g_soundPlay = Native<SoundPlayFn>("Sound.Play", ids::kSoundPlay);
    g_stopInstance = Native<StopInstanceFn>("Sound.StopInstance",
                                            ids::kSoundStopInstance);
    g_instanceVolume = Native<InstanceVolumeFn>("Sound.SetInstanceVolume",
                                                ids::kSoundSetInstanceVolume);
    g_placeAtMe = Native<PlaceAtMeFn>("ObjectReference.PlaceAtMe",
                                      ids::kRefPlaceAtMe);
    g_isDead = Native<RefQueryFn>("Actor.IsDead", ids::kActorIsDead);
    g_getForm = Native<GetFormFn>("Game.GetForm", ids::kGameGetForm);
    g_is3DLoaded = Native<RefQueryFn>("ObjectReference.Is3DLoaded",
                                      ids::kRefIs3DLoaded);
    g_advanceSkill = Native<AdvanceSkillFn>("Game.AdvanceSkill",
                                            ids::kGameAdvanceSkill);
    g_moveTo = Native<MoveToFn>("ObjectReference.MoveTo", ids::kRefMoveTo);
    g_getScale = Native<ScaleGetFn>("ObjectReference.GetScale",
                                    ids::kRefGetScale);
    g_setScale = Native<ScaleSetFn>("ObjectReference.SetScale",
                                    ids::kRefSetScale);
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
    g_currentWeather = Native<CurrentWeatherFn>("Weather.GetCurrentWeather",
                                                ids::kWeatherCurrent);
    g_classification = Native<ClassificationFn>("Weather.GetClassification",
                                                ids::kWeatherClassification);
    GameHooks& hooks = Hooks();
    hooks.playerLevel = PlayerLevel;
    hooks.actorValue = ActorValue;
    hooks.statPercent = StatPercent;
    hooks.advanceSkill = AdvanceSkill;
    hooks.showBarterMenu = ShowBarterMenu;
    hooks.showMessage = ShowMessage;
    hooks.placeNear = PlaceNear;
    hooks.isDead = IsDeadRef;
    hooks.is3DLoaded = Is3DLoadedRef;
    hooks.playSound = PlaySoundAt;
    hooks.stopSound = StopSoundInstance;
    hooks.goldCount = GoldCount;
    hooks.moveGold = MoveGold;
    hooks.activate = Activate;
    hooks.setLocked = SetLocked;
    hooks.isLocked = IsLocked;
    hooks.deleteRef = DeleteRef;
    hooks.distance = Distance;
    hooks.dynamicStat = DynamicStat;
    hooks.setDynamicStat = SetDynamicStat;
    hooks.modDynamicStat = ModDynamicStat;
    hooks.equipItem = EquipItem;
    hooks.playerCell = PlayerCellName;
    hooks.playerInInterior = PlayerInInterior;
    hooks.menuMode = MenuMode;
    hooks.forceGreeting = ForceGreeting;
    hooks.position = Position;
    hooks.setPosition = SetPosition;
    hooks.angle = Angle;
    hooks.setAngle = SetAngle;
    hooks.setQuestStage = SetQuestStage;
    hooks.addItem = AddItem;
    hooks.removeItem = RemoveItem;
    hooks.itemCount = ItemCount;
    hooks.setCombat = SetCombat;
    hooks.setEnabled = SetEnabled;
    hooks.isDisabled = IsDisabled;
    hooks.moveToCell = MoveRefToCell;
    hooks.moveInCell = MoveRefInCell;
    hooks.moveBy = MoveRefBy;
    hooks.rotateBy = RotateRefBy;
    hooks.scale = RefScale;
    hooks.setScale = SetRefScale;
    hooks.placeAtCell = PlaceBaseAt;
    hooks.aiTravel = AiTravelTo;
    hooks.aiWander = AiWanderAt;
    hooks.aiFollow = AiFollowActor;
    hooks.aiEscort = AiEscortActor;
    hooks.aiActivate = AiActivateObject;
    hooks.aiFace = AiFacePoint;
    hooks.currentPackage = CurrentAiPackageOf;
    hooks.packageDone = AiPackageDoneFor;
    hooks.hasLos = HasLosOn;
    hooks.detects = DetectsActor;
    hooks.fighting = FightingActor;
    hooks.weaponDrawn = WeaponIsDrawn;
    hooks.sneaking = ActorSneaking;
    hooks.running = ActorRunning;
    hooks.resurrect = ResurrectActor;
    hooks.dropItem = DropFromActor;
    hooks.weather = WeatherClassification;
    Log("game: %zu cell anchor(s) for PositionCell", CellCount());
    Log("game: %zu placed reference(s) resolvable by id", RefCount());
    Log("game: %zu journal quest(s) mapped to Skyrim quests", QuestCount());
}

void SetSpeakerRef(const char* actorId, void* ref) {
    g_speakerId = actorId ? actorId : "";
    g_speakerRef = ref;
}

}  // namespace mwruntime
