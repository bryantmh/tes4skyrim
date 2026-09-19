#include "game_calls.h"

#include "game_calls_internal.h"

#include <algorithm>
#include <cctype>
#include <chrono>
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

namespace gamecalls {

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

ParentCellFn   g_parentCell = nullptr;
RefQueryFn     g_isInterior = nullptr;
GetValueFn     g_getValue = nullptr;
SetValueFn  g_setValue = nullptr;
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
// 🛑 An object script's OWN id resolves to the reference RUNNING it. The
// placement table holds one reference per id, so without this every copy of
// a base placed many times acted on the same one.
void* OwnerRef(const std::string& owner) {
    if (Lower(owner) == kPlayerId) {
        return g_getPlayer ? g_getPlayer(PapyrusVm(), 0, nullptr) : nullptr;
    }
    const ObjectScript* running = RunningInstance();
    if (running && running->RuntimeFormId() &&
        Lower(owner) == Lower(running->BaseId())) {
        if (void* self = RefByRuntimeId(running->RuntimeFormId())) return self;
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
    if (!ref || !form || !g_addItem) return;
    RunOnGameThread([ref, form, count]() {
        g_addItem(PapyrusVm(), 0, ref, form, count, false);
    });
}

void RemoveItem(const std::string& owner, const std::string& item, int count) {
    void* ref = OwnerRef(owner);
    void* form = ItemForm(item);
    if (!ref || !form || !g_removeItem) return;
    RunOnGameThread([ref, form, count]() {
        g_removeItem(PapyrusVm(), 0, ref, form, count, false, nullptr);
    });
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
    RunOnGameThread([ref, enabled]() {
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
    RunOnGameThread([ref, level]() {
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
    RunOnGameThread([ref, which, value]() {
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
    RunOnGameThread([ref, which, delta]() {
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
    RunOnGameThread([ref, form]() {
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

// Writes a Skyrim actor value by name, for the stat commands.
void SetActorValueOf(const std::string& actor, const char* valueName,
                     float value) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_setValue) return;
    RunOnGameThread([ref, valueName, value]() {
        void* name = nullptr;
        if (FixedString(&name, valueName)) {
            g_setValue(PapyrusVm(), 0, ref, &name, value);
        }
    });
}

// Any actor's level; 1 for something that has none.
int LevelOf(const std::string& actor) {
    void* ref = OwnerRef(actor);
    return ref && g_getLevel ? g_getLevel(PapyrusVm(), 0, ref) : 1;
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

}  // namespace gamecalls

using namespace gamecalls;

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
    g_isDead = Native<RefQueryFn>("Actor.IsDead", ids::kActorIsDead);
    g_getForm = Native<GetFormFn>("Game.GetForm", ids::kGameGetForm);
    g_is3DLoaded = Native<RefQueryFn>("ObjectReference.Is3DLoaded",
                                      ids::kRefIs3DLoaded);
    g_advanceSkill = Native<AdvanceSkillFn>("Game.AdvanceSkill",
                                            ids::kGameAdvanceSkill);
    GameHooks& hooks = Hooks();
    hooks.playerLevel = PlayerLevel;
    hooks.actorValue = ActorValue;
    hooks.setActorValue = SetActorValueOf;
    hooks.level = LevelOf;
    hooks.statPercent = StatPercent;
    hooks.advanceSkill = AdvanceSkill;
    hooks.showBarterMenu = ShowBarterMenu;
    hooks.showMessage = ShowMessage;
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
    hooks.setQuestStage = SetQuestStage;
    hooks.addItem = AddItem;
    hooks.removeItem = RemoveItem;
    hooks.itemCount = ItemCount;
    hooks.setCombat = SetCombat;
    hooks.setEnabled = SetEnabled;
    hooks.isDisabled = IsDisabled;
    InstallMoveCalls(hooks);
    InstallAiCalls(hooks);
    InstallQueryCalls(hooks);
    Log("game: %zu cell anchor(s) for PositionCell", CellCount());
    Log("game: %zu placed reference(s) resolvable by id", RefCount());
    Log("game: %zu journal quest(s) mapped to Skyrim quests", QuestCount());
}

void SetSpeakerRef(const char* actorId, void* ref) {
    g_speakerId = actorId ? actorId : "";
    g_speakerRef = ref;
    SetSpeakerInstance(g_speakerId, ref ? FormIdOf(ref) : 0);
}

}  // namespace mwruntime
