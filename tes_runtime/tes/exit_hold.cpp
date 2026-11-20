#include "exit_hold.h"

#include <cstdint>
#include <cstring>
#include <mutex>
#include <unordered_set>

#include "addresses.h"
#include "engine.h"
#include "ids.h"
#include "log.h"
#include "mod_event.h"
#include "movie.h"
#include "ref_handle.h"

namespace tesruntime {

namespace {

using ChoicesFn = void* (*)(void* info);
using IdleLeftFn = float (*)(void* process);
using IdleAddFn = void (*)(void* process, float seconds);

//: Milliseconds between looks at the Dialogue Menu's movie.
constexpr int kTickMs = 33;

//: The mod event TES4Polyfill.HoldExitUntilAnswered sends from the speaker of a forced conversation.
constexpr const char* kHoldEvent = "TES4HoldExit";

//: The vanilla Dialogue Menu movie's clip and the members the hold replaces or reads.
constexpr const char* kMenuPath = "_root.DialogueMenu_mc";
constexpr const char* kOriginalName = "TESRT_onCancelPress";
constexpr const char* kOriginalPath = "_root.DialogueMenu_mc.TESRT_onCancelPress";
constexpr const char* kExitVisiblePath = "_root.DialogueMenu_mc.ExitButton._visible";

//: Scaleform's boolean value type.
constexpr UInt32 kGfxBool = 2;

void** g_topicManager = nullptr;
ChoicesFn g_choices = nullptr;
IdleLeftFn g_idleLeft = nullptr;
IdleAddFn g_idleAdd = nullptr;

// The speaker's idle countdown when the hold began; below 0 while nothing is held.
float g_idleHeld = -1.0f;

// The Exit button's visibility before the hold hid it; held only while it is hidden.
bool g_exitHidden = false;
bool g_exitWasVisible = true;

void* Manager() { return g_topicManager ? *g_topicManager : nullptr; }

// The line item the manager lists the choices of next: the selected node's item, else the fallback item.
void* CurrentItem(void* manager) {
    void* node = At<void*>(manager, ids::kOffTopicSelectedNode);
    void* item = node ? At<void*>(node, 0) : nullptr;
    return item ? item : At<void*>(manager, ids::kOffTopicFallbackItem);
}

// Speakers whose forced conversation holds the exit, by reference handle index; the Papyrus thread adds, the main
// thread reads and removes.
std::mutex g_armedLock;
std::unordered_set<std::uint32_t> g_armed;

// The open conversation's speaker, by handle index; 0 with no speaker.
std::uint32_t SpeakerIndex(void* manager) {
    return At<std::uint32_t>(manager, ids::kOffTopicSpeaker) & ids::kHandleIndexMask;
}

bool Armed(std::uint32_t speaker) {
    std::lock_guard<std::mutex> hold(g_armedLock);
    return speaker && g_armed.count(speaker) != 0;
}

// Arms the sender of kHoldEvent: its next conversation keeps the player while a line's choices are offered.
class HoldSink : public ModEventSink {
public:
    int ReceiveEvent(const ModEvent* event, void*) override {
        if (!event || !event->eventName || !event->sender || _stricmp(event->eventName, kHoldEvent) != 0) return 0;
        const std::uint32_t speaker = At<std::uint32_t>(event->sender, ids::kOffRefHandleCount) >> ids::kHandleIndexShift;
        std::lock_guard<std::mutex> hold(g_armedLock);
        g_armed.insert(speaker);
        return 0;
    }
};

HoldSink g_sink;

GFxValue Bool(bool value) {
    GFxValue out;
    out.type = kGfxBool;
    out.data.number = 0.0;
    *reinterpret_cast<bool*>(&out.data) = value;
    return out;
}

// The movie's cancel method, as Tab, Escape and the Exit button call it: does nothing while the exit is held, as
// Oblivion's hidden Goodbye does.
// See: docs/commentary/tes_runtime_dialogue.md#no-exit-while-choices-are-offered
class CancelPress : public GFxFunctionHandler {
public:
    CancelPress() { refCount = 1 << 30; }

    void Invoke(GFxFunctionArgs* args) override {
        if (!ExitHeld()) {
            InvokeMovie(args->movie, kOriginalPath);
            return;
        }
        Log("dialogue: the player cannot leave -- the line's choices wait for an answer");
    }
};

CancelPress g_cancel;

// Gives the Dialogue Menu's movie the hold's cancel method once, keeping its own under kOriginalName.
void Equip(void* view) {
    if (MovieDefined(view, kOriginalPath)) return;
    GFxValue original, menu;
    const bool ok = GetMovieVariable(view, ids::kDialogueCancelPath, &original) &&
                    GetMovieVariable(view, kMenuPath, &menu) && SetMovieMember(&menu, kOriginalName, &original) &&
                    AddMovieFunction(view, &menu, "onCancelPress", &g_cancel);
    ReleaseValue(&original);
    ReleaseValue(&menu);
    Log("dialogue: the Dialogue Menu's cancel goes through the choice hold -- %s", ok ? "ok" : "FAILED");
}

// Hides the Exit button while the exit is held, and gives it back its own visibility after.
void ShowExit(void* view, bool held) {
    if (held == g_exitHidden) return;
    if (held) {
        GFxValue visible;
        g_exitWasVisible = !GetMovieVariable(view, kExitVisiblePath, &visible) ||
                           *reinterpret_cast<bool*>(&visible.data);
        ReleaseValue(&visible);
    }
    GFxValue value = Bool(held ? false : g_exitWasVisible);
    SetMovieVariable(view, kExitVisiblePath, &value);
    g_exitHidden = held;
}

void EquipDialogue(void* view) {
    if (!MovieDefined(view, ids::kDialogueCancelPath)) return;
    Equip(view);
    ShowExit(view, ExitHeld());
}

void EquipOpenDialogue() { ForEachOpenMovie(EquipDialogue); }

// The menu's state at the last look: 0 closed, 1 open, 2 open with the exit held. The movie is touched only when
// it changes, so a conversation costs a few UI tasks rather than one a tick.
int g_lastState = 0;

// The armed speaker of the open conversation; disarmed when that conversation closes.
std::uint32_t g_heldSpeaker = 0;

void Disarm() {
    std::lock_guard<std::mutex> hold(g_armedLock);
    g_armed.erase(g_heldSpeaker);
    g_heldSpeaker = 0;
}

// While the exit is held the speaker's idle countdown stands still, so the conversation does not time out on the
// player either: it is topped back up to what it held when the hold began.
void PauseIdle(void* manager, bool held) {
    void* speaker = held ? RefFromHandle(At<std::uint32_t>(manager, ids::kOffTopicSpeaker)) : nullptr;
    void* process = speaker ? At<void*>(speaker, ids::kOffActorProcess) : nullptr;
    if (!process) {
        g_idleHeld = -1.0f;
    } else {
        const float left = g_idleLeft(process);
        if (g_idleHeld < 0.0f) g_idleHeld = left;
        if (left < g_idleHeld) g_idleAdd(process, g_idleHeld - left);
    }
    if (speaker) ReleaseRef(speaker);
}

void Tick() {
    const bool open = MenuIsOpen(ids::kDialogueMenuName);
    void* manager = Manager();
    const std::uint32_t speaker = open && manager ? SpeakerIndex(manager) : 0;
    if (Armed(speaker)) g_heldSpeaker = speaker;
    const int state = open ? (ExitHeld() ? 2 : 1) : 0;
    if (manager) PauseIdle(manager, state == 2);
    if (state == g_lastState) return;
    g_lastState = state;
    if (open) {
        PostToUiTask(EquipOpenDialogue);
        return;
    }
    g_exitHidden = false;
    if (g_heldSpeaker) Disarm();
}

}  // namespace

void** FirstListedTopic(void* manager) {
    auto* node = At<void**>(manager, ids::kOffTopicList);
    if (node && At<std::uint8_t>(manager, ids::kOffTopicSkipFirst)) node = static_cast<void**>(node[1]);
    return node && node[0] ? node : nullptr;
}

bool ExitHeld() {
    void* manager = Manager();
    void* item = manager && g_choices && Armed(SpeakerIndex(manager)) ? CurrentItem(manager) : nullptr;
    if (!item || !At<std::uint8_t>(item, ids::kOffItemHasInfo) || At<std::uint8_t>(item, ids::kOffItemNoChoices)) {
        return false;
    }
    void* info = At<void*>(item, ids::kOffItemInfo);
    void* choices = info ? g_choices(info) : nullptr;
    return choices && At<void*>(choices, 0) && FirstListedTopic(manager);
}

bool InstallExitHold(SKSEMessagingInterface* messaging) {
    g_topicManager =
        reinterpret_cast<void**>(Resolve("MenuTopicManager singleton", ids::kMenuTopicManager, nullptr));
    g_choices = reinterpret_cast<ChoicesFn>(Resolve("TESTopicInfo choices", ids::kInfoChoices, nullptr));
    g_idleLeft = reinterpret_cast<IdleLeftFn>(Resolve("dialogue idle countdown", ids::kDialogueIdleLeft, nullptr));
    g_idleAdd = reinterpret_cast<IdleAddFn>(Resolve("dialogue idle countdown add", ids::kDialogueIdleAdd, nullptr));
    const bool ok = g_topicManager && g_choices && g_idleLeft && g_idleAdd && ResolveMovieCalls() &&
                    AddModEventSink(messaging, &g_sink, "choice hold");
    Log("dialogue: choice hold %s", ok ? "resolved" : "NOT installed -- an engine address or the mod events failed");
    return ok;
}

void StartExitHoldTick() {
    const bool started = StartMainThreadTick(kTickMs, Tick);
    Log("dialogue: choice hold tick %s", started ? "started" : "NOT started -- no task interface");
}

}  // namespace tesruntime
