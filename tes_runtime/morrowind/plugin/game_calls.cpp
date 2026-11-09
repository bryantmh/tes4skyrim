#include "game_calls.h"

#include "game_calls_internal.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <thread>

#include "activation.h"
#include "addresses.h"
#include "conversation.h"
#include "dialogue_state.h"
#include "glide.h"
#include "ids.h"
#include "log.h"
#include "main_thread.h"
#include "menu.h"
#include "object_script.h"
#include "object_tick.h"
#include "script_tables.h"

namespace tesruntime::mw {

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
// The workers those two natives queue for a base form, called at once: TES3
// AddItem and RemoveItem change the inventory before the next statement.
//   void AddForm(ref, form, count, silent, stack id, VM)
//   void RemoveForm(ref, form, count, silent, moveTo, stack id, VM)
using AddFormFn = void (*)(void* ref, void* item, std::int32_t count, bool silent,
                           std::uint32_t stack, void* vm);
using RemoveFormFn = void (*)(void* ref, void* item, std::int32_t count, bool silent,
                              void* moveTo, std::uint32_t stack, void* vm);
using ItemCountFn = std::int32_t (*)(void* vm, std::uint32_t stack, void* ref,
                                     void* item);
using GetPlayerFn = void* (*)(void* vm, std::uint32_t stack, void* tag);
using SetEnabledFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                              bool fade);
using IsDisabledFn = bool (*)(void* vm, std::uint32_t stack, void* ref);
using ActivateFn = bool (*)(void* vm, std::uint32_t stack, void* ref,
                            void* actionRef, bool defaultOnly);
using LockFn = void (*)(void* vm, std::uint32_t stack, void* ref, bool lock,
                        bool asOffLimits);
using CanSleepHereFn = bool (*)(void* player, void* bed);
using ToggleSleepMenuFn = void (*)(bool sleeping);
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
using DrinkPotionFn = bool (*)(void* actor, void* potion, void* extraData);
// Barter and persuasion: a call on an actor, an int read off one, and the
// GLOBAL Game.AdvanceSkill, whose self is a tag and whose float rides fifth.
using ActorCallFn = void (*)(void* vm, std::uint32_t stack, void* actor);
using ActorIntFn = std::int32_t (*)(void* vm, std::uint32_t stack, void* actor);
using GetRaceFn = void* (*)(void* vm, std::uint32_t stack, void* actor);
using AdvanceSkillFn = void (*)(void* vm, std::uint32_t stack, void* tag,
                                void* name, float amount);
// Game.ShowTrainingMenu(Actor), global: the trainer rides after the tag.
using ShowTrainingMenuFn = void (*)(void* vm, std::uint32_t stack, void* tag,
                                    void* trainer);

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

// ObjectReference.Say(Topic, Actor speakAs, bool inPlayersHead), a MEMBER
// function, so the speaking reference is `self`.
using RefSayFn = void (*)(void* vm, std::uint32_t stack, void* self,
                          void* topic, void* speakAs, bool inPlayersHead);

// Actor.Kill(Actor akKiller = None), a MEMBER function: the victim is `self`.
using KillFn = void (*)(void* vm, std::uint32_t stack, void* actor, void* killer);

// Skyrim's actor values for OpenMW's dynamic stats, in OpenMW's order.
constexpr const char* kDynamicNames[] = {"Health", "Magicka", "Stamina"};
constexpr int kDynamicCount = 3;
constexpr int kHealth = 0;
// More than any actor's damage: RestoreActorValue stops at the maximum.
constexpr float kAllDamage = 1.0e9f;

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
AddFormFn    g_addForm = nullptr;
RemoveFormFn g_removeForm = nullptr;
ItemCountFn  g_itemCount = nullptr;
GetPlayerFn  g_getPlayer = nullptr;
SetEnabledFn  g_enable = nullptr;
SetEnabledFn  g_disable = nullptr;
IsDisabledFn  g_isDisabled = nullptr;
// The enabled state each script asked of a reference (by FormID) that the
// engine's own flag has not caught up with yet.
std::mutex g_askedEnabledMutex;
std::map<std::uint32_t, bool> g_askedEnabled;
ActivateFn     g_activate = nullptr;
CanSleepHereFn g_canSleepHere = nullptr;
ToggleSleepMenuFn g_toggleSleepMenu = nullptr;
LockFn         g_lock = nullptr;
SetLockLevelFn g_setLockLevel = nullptr;
RefQueryFn     g_isLocked = nullptr;
RefCallFn      g_delete = nullptr;
DistanceFn     g_distance = nullptr;

ParentCellFn   g_parentCell = nullptr;
RefQueryFn     g_isInterior = nullptr;
RefQueryFn     g_cellAttached = nullptr;
GetValueFn     g_getValue = nullptr;
SetValueFn  g_setValue = nullptr;
SetValueFn     g_modValue = nullptr;
SetValueFn     g_restoreValue = nullptr;
SetValueFn     g_damageValue = nullptr;
EquipItemFn    g_equipItem = nullptr;
DrinkPotionFn  g_drinkPotion = nullptr;
ActorCallFn    g_showBarterMenu = nullptr;
ShowTrainingMenuFn g_showTrainingMenu = nullptr;
ActorIntFn     g_getLevel = nullptr;
GetValueFn     g_getValuePercent = nullptr;
GetValueFn     g_getBaseValue = nullptr;
GetRaceFn      g_getRace = nullptr;
AdvanceSkillFn g_advanceSkill = nullptr;
PlaceAtMeFn    g_placeAtMe = nullptr;
RefQueryFn     g_isDead = nullptr;
KillFn         g_kill = nullptr;
GetFormFn      g_getForm = nullptr;
RefQueryFn     g_is3DLoaded = nullptr;
SoundPlayFn      g_soundPlay = nullptr;
StopInstanceFn   g_stopInstance = nullptr;
InstanceVolumeFn g_instanceVolume = nullptr;
RefSayFn         g_refSay = nullptr;

std::string   g_speakerId;
std::uint32_t g_speakerFormId = 0;

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
    if (Lower(owner) == Lower(g_speakerId)) {
        if (void* speaker = SpeakerRef()) return speaker;
    }
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
    if (!ref || !form || !g_addForm) return;
    RunOnGameThread([ref, form, count]() {
        g_addForm(ref, form, count, false, 0, PapyrusVm());
    });
}

void RemoveItem(const std::string& owner, const std::string& item, int count) {
    void* ref = OwnerRef(owner);
    void* form = ItemForm(item);
    if (!ref || !form || !g_removeForm) return;
    RunOnGameThread([ref, form, count]() {
        g_removeForm(ref, form, count, false, nullptr, 0, PapyrusVm());
    });
}

// 🛑 Posted, like combat: enabling a reference moves it in and out of the
// world, which is not safe from the menu's own thread. TES3 fades nothing,
// so both pass false.
//
// 🛑 TES3 flips the flag at once -- OpenMW's RefData::enable -- so a script's
// `Enable` then `ForceGreeting` greets, and `GetDisabled` after `Disable`
// answers 1, in the same frame. The engine's flag lands later, so the state
// asked for answers until it does.
void SetEnabled(const std::string& id, bool enabled) {
    void* ref = OwnerRef(id);
    if (!ref) return;
    {
        const std::lock_guard<std::mutex> lock(g_askedEnabledMutex);
        g_askedEnabled[FormIdOf(ref)] = enabled;
    }
    RunOnGameThread([ref, enabled]() {
        SetEnabledFn call = enabled ? g_enable : g_disable;
        if (call) call(PapyrusVm(), 0, ref, false);
    });
}

// Read directly rather than posted: a condition needs the answer NOW, and
// reading the flag does not touch the world.
bool IsDisabled(const std::string& id) {
    void* ref = OwnerRef(id);
    if (!ref || !g_isDisabled) return false;
    const bool disabled = g_isDisabled(PapyrusVm(), 0, ref);
    const std::lock_guard<std::mutex> lock(g_askedEnabledMutex);
    const auto asked = g_askedEnabled.find(FormIdOf(ref));
    if (asked == g_askedEnabled.end()) return disabled;
    if (disabled != asked->second) {
        g_askedEnabled.erase(asked);
        return disabled;
    }
    return !asked->second;
}

int ItemCount(const std::string& owner, const std::string& item) {
    void* ref = OwnerRef(owner);
    void* form = ItemForm(item);
    if (!ref || !form || !g_itemCount) return 0;
    return g_itemCount(PapyrusVm(), 0, ref, form);
}

std::uint32_t ItemFormId(const std::string& item) {
    void* form = ItemForm(item);
    return form ? FormIdOf(form) : 0;
}

void* PlayerRef() {
    return g_getPlayer ? g_getPlayer(PapyrusVm(), 0, nullptr) : nullptr;
}

// World mutations are POSTED, as Enable and combat are: the menu's callbacks
// do not run on the game thread. Reads answer at once.
// The player activating `ref`: a script's `Activate` when `byScript`, which
// runs past an OnActivate claim; a click otherwise, which a claim holds.
void ActivateBy(void* ref, bool byScript) {
    void* player = PlayerRef();
    if (!ref || !player || !g_activate) return;
    PostToMainThread([ref, player, byScript]() {
        const ScriptActivationScope scope(byScript ? ref : nullptr);
        g_activate(PapyrusVm(), 0, ref, player, false);
    });
}

void Activate(const std::string& id) { ActivateBy(OwnerRef(id), true); }

// The reference a player action names: its id, or its live FormID in hex for
// one whose id other references share.
void* PlayerTarget(const std::string& id) {
    char* end = nullptr;
    const unsigned long formId = std::strtoul(id.c_str(), &end, 16);
    return id.size() == 8 && *end == '\0' ? RefByRuntimeId(formId) : OwnerRef(id);
}

// Waits, too, for the object's script to have run once since it loaded: only
// a run that reads `OnActivate` claims the activation, and a click in the
// load's own frame would otherwise reach Skyrim's default instead.
bool PlayerActivate(const std::string& id) {
    void* ref = PlayerTarget(id);
    if (!Has3D(ref)) return false;
    const ObjectScript* script = InstanceForRef(FormIdOf(ref));
    if (script && !script->WasLoaded()) return false;
    // A load door moves the player a frame or more later; until that lands the
    // next step would read the old cell's position.
    if (IsDoor(ref)) HoldMenusForPlayerMove();
    ActivateBy(ref, false);
    return true;
}

// Where the reference a player action names stands, for a failure message.
std::string WhereIs(const std::string& id) {
    void* ref = PlayerTarget(id);
    if (!ref) return "no reference";
    void* place = PlaceOf(ref);
    char text[256];
    std::snprintf(text, sizeof text, "%08X in '%s' (place %08X%s) at (%.0f, %.0f, %.0f), 3D %d, disabled %d, dead %d",
                  FormIdOf(ref), InteriorName(ref).c_str(), place ? FormIdOf(place) : 0,
                  place == PlaceOf(PlayerRef()) ? ", the player's" : "", RefPositionAxis(PapyrusVm(), ref, 0),
                  RefPositionAxis(PapyrusVm(), ref, 1), RefPositionAxis(PapyrusVm(), ref, 2), Has3D(ref) ? 1 : 0,
                  g_isDisabled && g_isDisabled(PapyrusVm(), 0, ref) ? 1 : 0, IsDead(ref) ? 1 : 0);
    return text;
}

bool IsDoor(void* ref) {
    void* base = ref ? At<void*>(ref, ids::kOffRefBase) : nullptr;
    return base && At<std::uint8_t>(base, ids::kOffFormType) == ids::kFormTypeDoor;
}

// How far the player stands from `id` when its base is a door; -1 otherwise.
float DoorDistance(const std::string& id) {
    void* ref = PlayerTarget(id);
    void* player = PlayerRef();
    if (!player || !IsDoor(ref)) return -1.0f;
    return std::hypot(RefPositionAxis(PapyrusVm(), ref, 0) - RefPositionAxis(PapyrusVm(), player, 0),
                      RefPositionAxis(PapyrusVm(), ref, 1) - RefPositionAxis(PapyrusVm(), player, 1));
}

// The player picking `id` up: the reference itself moves into the inventory,
// as ObjectReference.AddItem does when handed a reference rather than a base.
void PlayerTake(const std::string& id) {
    void* ref = PlayerTarget(id);
    void* player = PlayerRef();
    if (!ref || !player || !g_addItem) return;
    PostToMainThread([ref, player]() { g_addItem(PapyrusVm(), 0, player, ref, 1, false); });
}

// `ShowRestMenu` does what activating a Skyrim bed does: the player's
// can-sleep-here check, which refuses a bed someone else owns, then the
// Sleep/Wait menu opened for sleeping. No `bed` is a rest on the spot.
// See: docs/commentary/morrowind_runtime.md#show-rest-menu
void ShowRestMenu(const std::string& bedId) {
    void* bed = bedId.empty() ? nullptr : OwnerRef(bedId);
    if (!g_canSleepHere || !g_toggleSleepMenu) return;
    PostToMainThread([bed]() {
        void* player = PlayerRef();
        if (player && g_canSleepHere(player, bed)) g_toggleSleepMenu(true);
    });
}

// The Wait key's own call: the same toggle with `sleeping` false.
// See: docs/commentary/morrowind_runtime.md#show-rest-menu
void PlayerWait() {
    if (!g_toggleSleepMenu) return;
    PostToMainThread([]() { g_toggleSleepMenu(false); });
}

void SleepFor(int hours) {
    PostToMainThread([hours]() {
        void* view = nullptr;
        if (!EngineMenuObject(ids::kSleepWaitMenu, &view) || !view) return;
        MovieSetNumber(view, ids::kSleepHoursPath, hours);
        MovieInvoke(view, ids::kSleepOkPath, nullptr, 0, nullptr);
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
    // 🛑 Its script ends with it: `Game.GetForm` on a deleted spawn's id
    // returns the freed reference, and the next tick's poll crashed on it.
    // See: docs/commentary/morrowind_runtime.md#a-deleted-reference-unbinds
    UnbindInstance(FormIdOf(ref));
    PostToMainThread([ref]() { g_delete(PapyrusVm(), 0, ref); });
}

float DistanceBetween(void* from, void* to) {
    if (!from || !to || !g_distance) return -1.0f;
    return g_distance(PapyrusVm(), 0, from, to);
}

// The distance from `other` to where the player's script move lands, or -1 when no move is landing.
float DistanceToLanding(void* other) {
    void* place = nullptr;
    float at[3] = {};
    if (!other || !PlayerLandingSpot(&place, at)) return -1.0f;
    if (PlaceOf(other) != place) return std::numeric_limits<float>::max();
    const float dx = RefPositionAxis(PapyrusVm(), other, 0) - at[0];
    const float dy = RefPositionAxis(PapyrusVm(), other, 1) - at[1];
    const float dz = RefPositionAxis(PapyrusVm(), other, 2) - at[2];
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

// A reference Skyrim has not loaded stands in another cell, where OpenMW's
// GetDistance answers FLT_MAX: 0 would pass every `GetDistance < x` test.
// A player whose script move is landing stands where it lands, as in OpenMW.
float Distance(const std::string& from, const std::string& to) {
    void* a = OwnerRef(from);
    void* b = OwnerRef(to);
    void* player = PlayerRef();
    const float landing = a == player ? DistanceToLanding(b) : b == player ? DistanceToLanding(a) : -1.0f;
    if (landing >= 0.0f) return landing;
    const float apart = DistanceBetween(a, b);
    return apart < 0.0f ? std::numeric_limits<float>::max() : apart;
}

int Tes3GridIndex(float v) { return static_cast<int>(std::floor(v / kTes3CellSize)); }

void* PlayerCell() {
    void* player = PlayerRef();
    return player && g_parentCell ? g_parentCell(PapyrusVm(), 0, player)
                                  : nullptr;
}

// 🛑 Skyrim keeps no name on an exterior CELL -- no vanilla one has FULL, and
// the converted ones' read back empty -- so outdoors the name is the sidecar's
// for the player's TES3 grid, else OpenMW's `sDefaultCellname`.
std::string ExteriorName(void* player) {
    const int x = Tes3GridIndex(RefPositionAxis(PapyrusVm(), player, 0));
    const int y = Tes3GridIndex(RefPositionAxis(PapyrusVm(), player, 1));
    const std::string* name = FindExteriorName(x, y);
    return name ? *name : GmstText("sDefaultCellname", "Wilderness");
}

std::string InteriorName(void* ref) {
    void* cell = ref && g_parentCell ? g_parentCell(PapyrusVm(), 0, ref) : nullptr;
    if (!cell || !g_isInterior || !g_isInterior(PapyrusVm(), 0, cell)) return std::string();
    const char* name = *reinterpret_cast<const char**>(
        static_cast<char*>(cell) + ids::kOffCellFullName);
    return name ? name : "";
}

std::string PlayerCellName() {
    if (!PlayerCell()) return std::string();
    return PlayerInInterior() ? InteriorName(PlayerRef()) : ExteriorName(PlayerRef());
}

std::string PlayerRegion() {
    void* player = PlayerRef();
    if (!player || !PlayerCell()) return std::string();
    const std::string* region =
        PlayerInInterior()
            ? FindInteriorRegion(InteriorName(player))
            : FindExteriorRegion(Tes3GridIndex(RefPositionAxis(PapyrusVm(), player, 0)),
                                 Tes3GridIndex(RefPositionAxis(PapyrusVm(), player, 1)));
    return region ? *region : std::string();
}

// 🛑 Whether a GAME is loaded, which is the player standing in SOME cell --
// never whether that cell has a NAME. An unnamed exterior is most of the
// world: gating the tick on the name stopped every object script outdoors.
// See: docs/commentary/morrowind_runtime.md#the-tick-is-gated-on-a-loaded-game
bool PlayerInWorld() { return PlayerCell() != nullptr; }

bool PlayerInInterior() {
    void* cell = PlayerCell();
    return cell && g_isInterior && g_isInterior(PapyrusVm(), 0, cell);
}

void* PlaceOf(void* ref) {
    void* cell = ref && g_parentCell ? g_parentCell(PapyrusVm(), 0, ref) : nullptr;
    if (cell && g_isInterior && g_isInterior(PapyrusVm(), 0, cell)) return cell;
    return WorldSpaceOf(ref);
}

// A dead actor's health reads at most 0, as TES3 has no death without it;
// Skyrim's Kill leaves the value where it was.
float DynamicStat(const std::string& actor, int which) {
    void* ref = OwnerRef(actor);
    void* name = nullptr;
    if (!ref || !g_getValue || which < 0 || which >= kDynamicCount ||
        !FixedString(&name, kDynamicNames[which])) {
        return 0.0f;
    }
    const float value = g_getValue(PapyrusVm(), 0, ref, &name);
    return which == 0 && IsDead(ref) ? std::min(value, 0.0f) : value;
}

// OpenMW's Actors::update: an actor whose current health is at or below 0
// dies. Skyrim kills only through damage, so a script's write that drains it
// (`SetHealth 0`, girasScript's end of Dagoth Ur) does the killing itself.
// See: docs/commentary/morrowind_runtime.md#stat-commands
void KillIfDrained(void* ref, void* name) {
    if (!g_kill || !g_isDead || !g_getValue || g_isDead(PapyrusVm(), 0, ref)) return;
    if (g_getValue(PapyrusVm(), 0, ref, name) <= 0.0f) g_kill(PapyrusVm(), 0, ref, nullptr);
}

// The test runner's player fighting back: each nearby actor attacking the
// player, or an actor following the player, is killed by the player. Game
// thread. The kills come after the walk, since a death changes the process
// lists being walked. An actor whose script is staged but not yet bound has not
// run its first frame, which may disable it (a TES3 script runs before the AI).
void KillPlayerAttackers() {
    void* player = PlayerRef();
    if (!player || !g_kill) return;
    std::vector<void*> party = PlayerParty();
    party.push_back(player);
    std::vector<void*> attackers;
    ForEachHighActor([&party, &attackers](void* actor) {
        const std::uint32_t id = FormIdOf(actor);
        if (InstanceByLocal(id) && !InstanceForRef(id)) return;
        void* target = IsDead(actor) ? nullptr : CombatTargetOf(actor);
        if (target && std::find(party.begin(), party.end(), target) != party.end()) {
            attackers.push_back(actor);
        }
    });
    for (void* actor : attackers) {
        Log("test: the player kills %08X, which attacked the player or a follower", FormIdOf(actor));
        g_kill(PapyrusVm(), 0, actor, player);
    }
}

// OpenMW's OpSetDynamic: the base becomes `value` and the current value is
// filled to the new maximum. Skyrim's SetActorValue moves only the base and
// keeps the damage taken, so the damage is restored away after it -- a script
// that writes `SetHealth 500` every frame keeps its actor alive, as authored.
// See: docs/commentary/morrowind_runtime.md#stat-commands
void SetDynamicStat(const std::string& actor, int which, float value) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_setValue || !g_restoreValue || which < 0 || which >= kDynamicCount) return;
    RunOnGameThread([ref, which, value]() {
        void* name = nullptr;
        if (!FixedString(&name, kDynamicNames[which])) return;
        g_setValue(PapyrusVm(), 0, ref, &name, value);
        g_restoreValue(PapyrusVm(), 0, ref, &name, kAllDamage);
        if (which == kHealth) KillIfDrained(ref, &name);
    });
}

// Skyrim keeps restore and damage apart where TES3 has one signed `Mod`. A
// base move is OpenMW's OpModDynamic: the base shifts, and the current value
// with it because Skyrim keeps the damage taken; only fatigue may go below 0.
void ModDynamicStat(const std::string& actor, int which, float delta, bool base) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_restoreValue || !g_damageValue || !g_getBaseValue || !g_setValue ||
        which < 0 || which >= kDynamicCount) {
        return;
    }
    RunOnGameThread([ref, which, delta, base]() {
        void* name = nullptr;
        if (!FixedString(&name, kDynamicNames[which])) return;
        if (base) {
            const float moved = g_getBaseValue(PapyrusVm(), 0, ref, &name) + delta;
            g_setValue(PapyrusVm(), 0, ref, &name, which == 2 ? moved : std::max(moved, 0.0f));
        } else if (delta >= 0.0f) {
            g_restoreValue(PapyrusVm(), 0, ref, &name, delta);
        } else {
            g_damageValue(PapyrusVm(), 0, ref, &name, -delta);
        }
        if (which == kHealth) KillIfDrained(ref, &name);
    });
}

void EquipItem(const std::string& actor, const std::string& item) {
    void* ref = OwnerRef(actor);
    void* form = ItemForm(item);
    if (!ref || !form || !g_equipItem) return;
    const std::uint8_t type = At<std::uint8_t>(form, ids::kOffFormType);
    const bool potion = type == ids::kFormTypePotion;
    const bool read = type == ids::kFormTypeBook && Lower(actor) == kPlayerId;
    RunOnGameThread([ref, form, potion, read]() {
        if (read) {
            ReadBookFromInventory(form);
        } else if (potion && g_drinkPotion) {
            const bool drank = g_drinkPotion(ref, form, nullptr);
            Log("item: %08X drinks %08X -> %s", FormIdOf(ref), FormIdOf(form), drank ? "ok" : "REFUSED");
        } else {
            Log("item: %08X equips %08X (form type %02X)", FormIdOf(ref), FormIdOf(form),
                       At<std::uint8_t>(form, ids::kOffFormType));
            g_equipItem(PapyrusVm(), 0, ref, form, false, false);
        }
    });
}

// OpenMW's `MenuMode` is any GUI mode pushed, and a menu command pushes its
// mode at once; ours open a frame or more after the asking call.
bool MenuMode() { return ConversationOpen() || MenuOpening(); }

// 🛑 Our OWN dialogue menu carries the pause flag and so counts itself here,
// but TES3 runs scripts through a conversation -- `MenuMode` exists for them
// to branch on. Its own contribution is discounted, so a menu opened OVER the
// conversation still stops the tick.
// See: docs/commentary/morrowind_runtime.md#the-tick-stops-while-the-game-is-paused
bool GamePaused() {
    ForgetOpenedMenus();
    ReleaseHeldMenus();
    return PausingMenuCount() > (ConversationOpen() ? 1u : 0u);
}

// OpForceGreeting -> DialogueManager::startDialogue, synchronously: the
// greeting is chosen and its result script run before the caller's next line,
// which may change what the greeting tests. Only a result script's call is
// posted, since beginning tears down the conversation it runs in.
void ForceGreeting(const std::string& actor) {
    void* ref = OwnerRef(actor);
    const ActorDef* def = FindActor(actor);
    const char* refused = !ref ? "no reference" : !def ? "no actor row"
                        : IsDisabled(actor) ? "disabled"
                        : g_isDead && g_isDead(PapyrusVm(), 0, ref) ? "dead" : nullptr;
    if (refused) {
        Log("conversation: %s cannot greet: %s", actor.c_str(), refused);
        return;
    }
    const std::string shown = def->name.empty() ? actor : def->name;
    auto begin = [actor, ref, shown]() {
        Log("conversation: %s greeting begins", actor.c_str());
        SetSpeakerRef(actor.c_str(), ref);
        BeginConversation(actor.c_str(), shown.c_str(), PlayerName());
    };
    if (InResultScript()) {
        PostToMainThread(begin);
    } else {
        RunOnGameThread(begin);
    }
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

float BaseActorValue(const std::string& actor, const char* valueName) {
    void* ref = OwnerRef(actor);
    void* name = nullptr;
    if (!ref || !g_getBaseValue || !FixedString(&name, valueName)) return 0.0f;
    return g_getBaseValue(PapyrusVm(), 0, ref, &name);
}

std::uint32_t RaceOf(const std::string& actor) {
    void* ref = OwnerRef(actor);
    void* race = ref && g_getRace ? g_getRace(PapyrusVm(), 0, ref) : nullptr;
    return race ? FormIdOf(race) : 0;
}

std::string RaceNameOf(const std::string& actor) {
    void* ref = OwnerRef(actor);
    void* race = ref && g_getRace ? g_getRace(PapyrusVm(), 0, ref) : nullptr;
    const char* name = race ? At<const char*>(race, ids::kOffRaceFullName) : nullptr;
    return name ? name : "";
}

int QuestStageOf(const std::string& plugin, std::uint32_t formId) {
    void* quest = FormFromFile(plugin.c_str(), formId & kLocalMask);
    return quest ? At<std::uint16_t>(quest, ids::kOffQuestCurrentStage) : -1;
}

// TESNPC::GetSex, read in place: an NPC_ base whose +0x38 bit 0 is set.
bool FemaleOf(const std::string& actor) {
    void* ref = OwnerRef(actor);
    void* base = ref ? At<void*>(ref, ids::kOffRefBase) : nullptr;
    return base && At<std::uint8_t>(base, ids::kOffFormType) == ids::kFormTypeNpc &&
           (At<std::uint8_t>(base, ids::kOffNpcSexFlags) & 1) != 0;
}

// Writes a Skyrim actor value's base by name, for the stat commands.
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

// Moves a Skyrim actor value's permanent modifier by `delta`.
void ModActorValueOf(const std::string& actor, const char* valueName, float delta) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_modValue) return;
    RunOnGameThread([ref, valueName, delta]() {
        void* name = nullptr;
        if (FixedString(&name, valueName)) {
            g_modValue(PapyrusVm(), 0, ref, &name, delta);
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
    if (g_isDisabled && g_isDisabled(PapyrusVm(), 0, source)) return 0;
    const std::int32_t instance = g_soundPlay(PapyrusVm(), 0, form, source);
    if (instance && volume < 1.0f && g_instanceVolume) {
        g_instanceVolume(PapyrusVm(), 0, nullptr, instance, volume);
    }
    if (!instance) {
        Log("game: '%s' did not start (loop=%d)", sound.c_str(), loop ? 1 : 0);
    }
    return instance;
}

// When each speaker's current line is expected to end. Keyed by the reference
// because that is what `SayDone` names, and cleared as each one is read.
std::map<void*, double> g_saying;

// Seconds of UNPAUSED play; only differences are ever used.
//
// 🛑 Not wall-clock. A menu that pauses the game stops the engine playing the
// line but not `steady_clock`, so a line would be declared finished while the
// engine still held it -- and the next Say at that actor is DROPPED.
// See: docs/commentary/morrowind_runtime.md#the-tick-stops-while-the-game-is-paused
double Now() { return GameSeconds(); }

// How long a line of `text` takes to speak, for a recording the converter
// could not measure: estimated from the subtitle, with a floor for a line too
// short to time.
// See: docs/commentary/morrowind_runtime.md#scripted-say
double SpokenSeconds(const std::string& text) {
    constexpr double kCharsPerSecond = 14.0;
    constexpr double kShortestLine = 1.0;
    const double spoken = static_cast<double>(text.size()) / kCharsPerSecond;
    return spoken < kShortestLine ? kShortestLine : spoken;
}

// `Say file text`: the scripted VOICE channel. The engine moves a mouth only
// for a line it plays as DIALOGUE, so the file is turned back into the TOPIC
// the export minted for it -- one topic per line, holding that single INFO --
// and spoken through ObjectReference.Say, which takes a topic and nothing else.
//
// Nothing is queued: Skyrim DROPS a Say aimed at an actor already speaking,
// and the CK wiki records a crash when a line overlaps a greeting, so a
// refused line is simply reported and the script's own SayDone poll retries.
// See: docs/commentary/morrowind_runtime.md#scripted-say
bool SayLine(const std::string& ref, const std::string& file,
             const std::string& text) {
    const SayLineDef* line = FindSayLine(file);
    if (!line) {
        ReportOnce("say line", file);
        return false;
    }
    void* speaker = ref.empty() ? PlayerRef() : OwnerRef(ref);
    if (!speaker) return false;
    // Only an actor has a mouth; a door, an activator or the player's own
    // head plays the same recording as a sound from where it stands.
    void* topic = FindActor(ref) && line->topic.formId && g_refSay
                      ? Form(&line->topic) : nullptr;
    if (topic) {
        g_refSay(PapyrusVm(), 0, speaker, topic, nullptr, false);
    } else if (line->sound.empty() ||
               !PlaySoundAt(ref, line->sound, false, 1.0f)) {
        ReportOnce("say line without a voice", file);
        return false;
    }
    g_saying[speaker] = Now() + (line->seconds > 0 ? line->seconds
                                                   : SpokenSeconds(text));
    Log("game: '%s' says '%s'", ref.c_str(), file.c_str());
    return true;
}

// `SayDone`: whether the line this session started for that actor has ended.
//
// Skyrim exposes no "is this actor speaking" NATIVE -- `IsTalking` is a
// condition function, and the CK wiki does not settle what it reports -- so
// the end is taken from the line's own expected length, the way the say
// timer does. Nothing started means done, which is what OpenMW answers too
// (`SoundManager::sayDone` is true whenever no stream is playing).
// See: docs/commentary/morrowind_runtime.md#scripted-say
bool SayFinished(const std::string& ref) {
    void* speaker = ref.empty() ? PlayerRef() : OwnerRef(ref);
    if (!speaker) return true;
    const auto it = g_saying.find(speaker);
    if (it == g_saying.end()) return true;
    if (Now() < it->second) return false;
    g_saying.erase(it);
    return true;
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

void* SpeakerRef() { return RefByRuntimeId(g_speakerFormId); }

// 🛑 Actors only: `Actor.IsDead` on any other reference reads past its end.
bool IsDead(void* ref) {
    return ref && g_isDead && IsActorRef(ref) && g_isDead(PapyrusVm(), 0, ref);
}

bool IsDeadRef(std::uint32_t runtimeFormId) {
    return IsDead(RefByRuntimeId(runtimeFormId));
}

// OpenMW's `CreatureStats::hasDied`, read and cleared: a scripted actor's own
// instance holds it, any other actor's death waits in the engine-death set.
bool TakeDeathOf(const std::string& id) {
    void* ref = OwnerRef(id);
    if (!ref) return false;
    const std::uint32_t runtimeId = FormIdOf(ref);
    ObjectScript* instance = InstanceForRef(runtimeId);
    if (!instance) return TakeEngineDeath(runtimeId);
    instance->PollDeath();
    const bool died = instance->Events().died;
    instance->Events().died = false;
    return died;
}

// TES3's whole rule for when a local script runs: while its object is loaded.
//
// 🛑 The two halves mean DIFFERENT things and only one is an unload. `GetForm`
// answers for a persistent reference whether or not its 3D is loaded, so a
// null there is "could not ask", not "gone" -- and `Is3DLoaded` answering
// false is the only real unload. Collapsing them into one bool is what let a
// chargen guard who never leaves his cell be unbound forever.
// See: docs/commentary/morrowind_runtime.md#instances-bind-from-the-world
//
// 🛑 A DISABLED reference has no 3D and has NOT left the world. TES3 runs a
// local script for every reference in an active cell, disabled or not (OpenMW
// engine.cpp executeLocalScripts has no enabled test), and "disable myself
// until the story reaches me" depends on it: read as an unload, the script
// that disabled itself was dropped and could never enable itself again. So a
// reference without 3D is in the world for as long as its CELL is attached.
//
// 🛑 The cell, not `IsDisabled`: enabling queues the 3D, so for the frames
// after `Enable` the reference is neither disabled nor loaded, and a test on
// the flag would drop the script on exactly the tick it woke up.
//
// 🛑 An INTERIOR must also be the cell the player is in, 3D or not. The engine
// keeps the interior just left attached, 3D and all, for a while, so on
// `IsAttached` or `Is3DLoaded` alone the scripts of the previous cell ran on
// behind the player's back: measured, a chargen script went on enabling its
// light for 48 seconds, and Arktwend's Void marker moved the player back into
// the Hall of Fate 652 times over 40 seconds. Likewise an EXTERIOR is no
// active cell while the player is indoors: Jarko, the lookout outside the
// Abandoned House, force-greeted the player inside it.
bool Has3D(void* ref) {
    return ref && g_is3DLoaded && g_is3DLoaded(PapyrusVm(), 0, ref);
}

bool InWorld(void* ref) {
    void* cell = ref && g_parentCell ? g_parentCell(PapyrusVm(), 0, ref) : nullptr;
    const bool interior = cell && g_isInterior && g_isInterior(PapyrusVm(), 0, cell);
    if (interior ? cell != PlayerCell() : cell && PlayerInInterior()) return false;
    if (Has3D(ref)) return true;
    return cell && g_cellAttached && g_cellAttached(PapyrusVm(), 0, cell);
}

LoadState Load3DStateRef(std::uint32_t runtimeFormId) {
    if (!g_is3DLoaded) return LoadState::kUnknown;
    void* ref = RefByRuntimeId(runtimeFormId);
    if (!ref) return LoadState::kUnresolved;
    return InWorld(ref) ? LoadState::kLoaded : LoadState::kUnloaded;
}

bool Is3DLoadedRef(std::uint32_t runtimeFormId) {
    return Load3DStateRef(runtimeFormId) == LoadState::kLoaded;
}

// Why a placement the sweep WANTED did not rebind. The sweep tests 256 rows a
// tick over a 15,639-row table, almost all of them far from the player, so
// only a placement that has already been bound once this session is worth a
// line -- that is the set that can have been dropped and lost.
//
// Rate-limited to one line per placement per 150 ticks (5s), which is the
// sweep's own heartbeat: a permanently stuck instance prints every 5 seconds
// instead of 256 times a tick.
void ReportRebindMiss(const std::string& plugin, std::uint32_t localFormId,
                      bool resolved) {
    // An instance carrying a runtime id is one the engine handed us before,
    // so this placement HAS been in the world this session.
    const ObjectScript* had = FindInstance(plugin, localFormId);
    if (!had || !had->RuntimeFormId()) return;
    static std::map<std::uint32_t, std::size_t> lastSaid;
    const std::size_t now = TicksRun();
    std::size_t& said = lastSaid[localFormId];
    if (said && now - said < 150) return;
    said = now;
    LogVerbose("object: %s|%06X will not rebind -- %s", plugin.c_str(),
               localFormId,
               resolved ? "GetFormFromFile answered but Is3DLoaded said NO"
                        : "GetFormFromFile did not answer");
}

// A staged placement's RUNTIME FormID once the engine has it in the world, or
// 0 while it is not. `GetFormFromFile` answers for a non-persistent reference
// only while its cell is loaded, so the null is "not here yet", not a failure.
std::uint32_t LoadedRef(const std::string& plugin, std::uint32_t localFormId) {
    void* ref = FormFromFile(plugin.c_str(), localFormId & kLocalMask);
    if (!ref || !g_is3DLoaded || !InWorld(ref)) {
        ReportRebindMiss(plugin, localFormId, ref != nullptr);
        return 0;
    }
    return FormIdOf(ref);
}

// A `MessageBox` raised by an object script, which has no dialogue menu to
// render it. Without buttons TES3 shows a box that fades on its own, so it
// becomes Skyrim's notification; with buttons, the engine's modal.
// See: docs/plans/morrowind_object_scripts.md#messagebox
void ShowMessage(const std::string& text,
                 const std::vector<std::string>& buttons) {
    if (!buttons.empty()) {
        ShowButtonMessage(text, buttons);
        return;
    }
    PostToMainThread([text]() { Notify(text); });
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

// Skyrim's own training menu on the speaker, which teaches the skill its
// class names up to the class's cap -- the trainer class the import gave it.
// See: docs/commentary/morrowind_runtime.md#barter
void ShowTrainingMenu(const std::string& actor) {
    void* ref = OwnerRef(actor);
    if (!ref || !g_showTrainingMenu) {
        Log("game: training with '%s' -- %s", actor.c_str(),
            ref ? "ShowTrainingMenu unresolved" : "no reference");
        return;
    }
    PostToMainThread([ref]() {
        g_showTrainingMenu(PapyrusVm(), 0, nullptr, ref);
    });
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
    if (!source || !target || !gold || !g_removeForm || !g_addForm) return;
    g_removeForm(source, gold, count, false, nullptr, 0, PapyrusVm());
    g_addForm(target, gold, count, false, 0, PapyrusVm());
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
    g_addForm = Native<AddFormFn>("ObjectReference AddItem worker", ids::kRefAddForm);
    g_removeForm = Native<RemoveFormFn>("ObjectReference RemoveItem worker",
                                        ids::kRefRemoveForm);
    g_itemCount = Native<ItemCountFn>("ObjectReference.GetItemCount",
                                      ids::kRefGetItemCount);
    g_getPlayer = Native<GetPlayerFn>("Game.GetPlayer", ids::kGameGetPlayer);
    g_enable = Native<SetEnabledFn>("ObjectReference.Enable", ids::kRefEnable);
    g_disable = Native<SetEnabledFn>("ObjectReference.Disable",
                                     ids::kRefDisable);
    g_isDisabled = Native<IsDisabledFn>("ObjectReference.IsDisabled",
                                        ids::kRefIsDisabled);
    g_activate = Native<ActivateFn>("ObjectReference.Activate",
                                    ids::kRefActivate);
    g_canSleepHere = Native<CanSleepHereFn>("PlayerCharacter::CanSleepHere",
                                            ids::kPlayerCanSleepHere);
    g_toggleSleepMenu = Native<ToggleSleepMenuFn>("SleepWaitMenu toggle",
                                                  ids::kToggleSleepWaitMenu);
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
    g_cellAttached = Native<RefQueryFn>("Cell.IsAttached",
                                        ids::kCellIsAttached);
    g_getValue = Native<GetValueFn>("Actor.GetActorValue", ids::kActorGetValue);
    g_setValue = Native<SetValueFn>("Actor.SetActorValue", ids::kActorSetValue);
    g_modValue = Native<SetValueFn>("Actor.ModActorValue", ids::kActorModValue);
    g_restoreValue = Native<SetValueFn>("Actor.RestoreActorValue",
                                        ids::kActorRestoreValue);
    g_damageValue = Native<SetValueFn>("Actor.DamageActorValue",
                                       ids::kActorDamageValue);
    g_equipItem = Native<EquipItemFn>("Actor.EquipItem", ids::kActorEquipItem);
    g_drinkPotion = Native<DrinkPotionFn>("Actor::DrinkPotion", ids::kActorDrinkPotion);
    g_showBarterMenu = Native<ActorCallFn>("Actor.ShowBarterMenu",
                                           ids::kActorShowBarterMenu);
    g_showTrainingMenu = Native<ShowTrainingMenuFn>("Game.ShowTrainingMenu",
                                                    ids::kGameShowTrainingMenu);
    g_getLevel = Native<ActorIntFn>("Actor.GetLevel", ids::kActorGetLevel);
    g_getValuePercent = Native<GetValueFn>("Actor.GetActorValuePercentage",
                                           ids::kActorGetValuePercent);
    g_getBaseValue = Native<GetValueFn>("Actor.GetBaseActorValue",
                                        ids::kActorGetBaseValue);
    g_getRace = Native<GetRaceFn>("Actor.GetRace", ids::kActorGetRace);
    g_refSay = Native<RefSayFn>("ObjectReference.Say", ids::kRefSay);
    g_soundPlay = Native<SoundPlayFn>("Sound.Play", ids::kSoundPlay);
    g_stopInstance = Native<StopInstanceFn>("Sound.StopInstance",
                                            ids::kSoundStopInstance);
    g_instanceVolume = Native<InstanceVolumeFn>("Sound.SetInstanceVolume",
                                                ids::kSoundSetInstanceVolume);
    g_isDead = Native<RefQueryFn>("Actor.IsDead", ids::kActorIsDead);
    g_kill = Native<KillFn>("Actor.Kill", ids::kActorKill);
    g_getForm = Native<GetFormFn>("Game.GetForm", ids::kGameGetForm);
    g_is3DLoaded = Native<RefQueryFn>("ObjectReference.Is3DLoaded",
                                      ids::kRefIs3DLoaded);
    g_advanceSkill = Native<AdvanceSkillFn>("Game.AdvanceSkill",
                                            ids::kGameAdvanceSkill);
    GameHooks& hooks = Hooks();
    hooks.playerLevel = PlayerLevel;
    hooks.actorValue = ActorValue;
    hooks.setActorValue = SetActorValueOf;
    hooks.modActorValue = ModActorValueOf;
    hooks.level = LevelOf;
    hooks.statPercent = StatPercent;
    hooks.baseActorValue = BaseActorValue;
    hooks.race = RaceOf;
    hooks.raceName = RaceNameOf;
    hooks.questStageOf = QuestStageOf;
    hooks.female = FemaleOf;
    hooks.advanceSkill = AdvanceSkill;
    hooks.showBarterMenu = ShowBarterMenu;
    hooks.showTrainingMenu = ShowTrainingMenu;
    hooks.showMessage = ShowMessage;
    hooks.isDead = IsDeadRef;
    hooks.takeDeathOf = TakeDeathOf;
    hooks.is3DLoaded = Is3DLoadedRef;
    hooks.load3DState = Load3DStateRef;
    hooks.loadedRef = LoadedRef;
    hooks.say = SayLine;
    hooks.sayDone = SayFinished;
    hooks.playSound = PlaySoundAt;
    hooks.stopSound = StopSoundInstance;
    hooks.goldCount = GoldCount;
    hooks.moveGold = MoveGold;
    hooks.activate = Activate;
    hooks.playerActivate = PlayerActivate;
    hooks.whereIs = WhereIs;
    hooks.doorDistance = DoorDistance;
    hooks.playerTake = PlayerTake;
    hooks.showRestMenu = ShowRestMenu;
    hooks.sleepFor = SleepFor;
    hooks.playerWait = PlayerWait;
    hooks.setLocked = SetLocked;
    hooks.isLocked = IsLocked;
    hooks.deleteRef = DeleteRef;
    hooks.distance = Distance;
    hooks.dynamicStat = DynamicStat;
    hooks.setDynamicStat = SetDynamicStat;
    hooks.modDynamicStat = ModDynamicStat;
    hooks.equipItem = EquipItem;
    hooks.playerCell = PlayerCellName;
    hooks.playerInWorld = PlayerInWorld;
    hooks.playerInInterior = PlayerInInterior;
    hooks.gamePaused = GamePaused;
    hooks.menuMode = MenuMode;
    hooks.forceGreeting = ForceGreeting;
    hooks.setQuestStage = SetQuestStage;
    hooks.addItem = AddItem;
    hooks.removeItem = RemoveItem;
    hooks.itemCount = ItemCount;
    hooks.itemFormId = ItemFormId;
    hooks.killPlayerAttackers = KillPlayerAttackers;
    hooks.setEnabled = SetEnabled;
    hooks.isDisabled = IsDisabled;
    InstallMoveCalls(hooks);
    InstallAiCalls(hooks);
    InstallQueryCalls(hooks);
    InstallSpellCalls(hooks);
    InstallWerewolfCalls(hooks);
    InstallControlCalls(hooks);
    InstallStateCalls(hooks);
    InstallCrimeCalls(hooks);
    InstallCombatCalls(hooks);
    InstallWeatherCalls(hooks);
    InstallMessageCalls(hooks);
    InstallTeleportCalls();
    InstallFlightCalls(hooks);
    InstallStillCalls();
    InstallAttributeCalls(hooks);
    InstallBrawlCalls(hooks);
    InstallAnimCalls(hooks);
    Log("game: %zu spell(s) and %zu magic effect(s) resolvable by id",
        SpellCount(), EffectCount());
    Log("game: %zu named cell(s) for PositionCell", CellCount());
    Log("game: %zu placed reference(s) resolvable by id", RefCount());
    Log("game: %zu journal quest(s) mapped to Skyrim quests", QuestCount());
}

void SetSpeakerRef(const char* actorId, void* ref) {
    g_speakerId = actorId ? actorId : "";
    g_speakerFormId = ref ? FormIdOf(ref) : 0;
    SetSpeakerInstance(g_speakerId, g_speakerFormId);
}

}  // namespace tesruntime::mw
