// The TES4 test runner's game side; see test_game.h.

#include "test_game.h"

#include <windows.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#include "addresses.h"
#include "engine.h"
#include "exit_hold.h"
#include "glide.h"
#include "hook.h"
#include "ids.h"
#include "log.h"
#include "movie.h"
#include "test_runner.h"
#include "ui_message.h"

namespace tesruntime {

namespace {

using GetPlayerFn = void* (*)(void* vm, std::uint32_t stack, void* tag);
using RefFormFn = void* (*)(void* vm, std::uint32_t stack, void* ref);
using RefBoolFn = bool (*)(void* vm, std::uint32_t stack, void* ref);
using ActivateFn = void (*)(void* vm, std::uint32_t stack, void* ref, void* activator,
                            bool defaultOnly);
using AddItemFn = void (*)(void* vm, std::uint32_t stack, void* ref, void* item,
                           std::int32_t count, bool silent);
using FxCallbackFn = void (*)(void* args);
using AddSubtitleFn = void (*)(void* manager, void* speaker, const char* text, bool force);
using PathToFn = std::int32_t (*)(void* actor, void* target, float speed);
using AiDrivenFn = void (*)(void* vm, std::uint32_t stack, void* tag, bool on);
using GlobalCallFn = void (*)(void* vm, std::uint32_t stack, void* tag);
using PlaceAtMeFn = void* (*)(void* vm, std::uint32_t stack, void* self, void* base,
                             std::int32_t count, bool forcePersist, bool initiallyDisabled);
using RefCallFn = void (*)(void* vm, std::uint32_t stack, void* ref);
using MoveToFn = void (*)(void* vm, std::uint32_t stack, void* ref, void* target, float dx, float dy, float dz,
                          bool matchRotation);

// Milliseconds between the runner's steps, about 30 a real second.
constexpr int kTickMs = 33;
// Ticks between the third-person switch and the path: the Morrowind runtime
// measured a package started during the camera switch leaving the player
// standing for good (docs/commentary/morrowind_runtime.md#the-walk-step).
constexpr int kWalkSettleTicks = 30;
// How near the player counts as arrived, on the ground plane, as the
// Morrowind runtime's walk.
constexpr float kArrivedDistance = 128.0f;
// Ticks a path may run before it is asked for again, should the AI have
// replaced it.
constexpr int kWalkRetryTicks = 300;
// Full speed: the player runs, as a player does.
constexpr float kWalkSpeed = 1.0f;
// `approach`, as MorrowindRuntime's: no move within talking reach; otherwise
// the player is put this far in front of the NPC. A move is given this many
// ticks to land before the next step judges the player's place.
constexpr float kTalkReach = 200.0f;
constexpr float kTalkStandOff = 80.0f;
constexpr int kMoveLandTicks = 30;

struct Calls {
    GetPlayerFn player = nullptr;
    RefFormFn parentCell = nullptr;
    RefBoolFn cellIsInterior = nullptr;
    RefBoolFn is3DLoaded = nullptr;
    ActivateFn activate = nullptr;
    AddItemFn addItem = nullptr;
    void** menuManager = nullptr;
    void** topicManager = nullptr;
    FxCallbackFn topicClicked = nullptr;
    FxCallbackFn closeDialogue = nullptr;
    FxCallbackFn buttonPress = nullptr;
    AddSubtitleFn addSubtitle = nullptr;
    PathToFn pathTo = nullptr;
    AiDrivenFn aiDriven = nullptr;
    GlobalCallFn thirdPerson = nullptr;
    PlaceAtMeFn placeAtMe = nullptr;
    RefCallFn deleteRef = nullptr;
    MoveToFn moveTo = nullptr;
    char* boxQueue = nullptr;
};

Calls g_c;

// Ticks left for the player's last `approach` move to land.
int g_moveLanding = 0;

// Lines begun since the runner last read them; AddSubtitle may run off the
// main thread.
std::mutex g_spokenMutex;
std::vector<test::SpokenLine> g_spoken;

// A walk under way: where it aims, and the marker spawned for `x y z`.
struct Walk {
    void* target = nullptr;
    void* marker = nullptr;
    bool toPoint = false;
    float x = 0.0f, y = 0.0f, z = 0.0f;
    int settleTicks = 0;
    int ticks = 0;
};

Walk g_walk;

template <typename T>
T At(void* base, std::size_t offset) {
    return *reinterpret_cast<T*>(static_cast<char*>(base) + offset);
}

void* Player() { return g_c.player && g_api.vm ? g_c.player(g_api.vm, 0, nullptr) : nullptr; }

// The reference a step names: the harness hands over its runtime FormID in hex.
void* RefNamed(const std::string& id) {
    char* end = nullptr;
    const unsigned long formId = std::strtoul(id.c_str(), &end, 16);
    return !id.empty() && *end == '\0' && g_api.lookupForm ? g_api.lookupForm(formId) : nullptr;
}

std::string Lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return text;
}

bool MenuOpen(const std::string& name) { return MenuIsOpen(name.c_str()); }

// The open menu registered as `name`, or null.
void* MenuObject(const char* name) {
    void* manager = g_c.menuManager ? *g_c.menuManager : nullptr;
    FixedString fixed(name);
    if (!manager || !fixed.ptr) return nullptr;
    char* entries = At<char*>(manager, ids::kOffMenuTableEntries);
    const auto capacity = At<std::uint32_t>(manager, ids::kOffMenuTableCapacity);
    for (std::uint32_t i = 0; entries && i < capacity; ++i) {
        char* entry = entries + std::size_t{i} * ids::kMenuTableEntrySize;
        if (!At<void*>(entry, ids::kOffMenuEntryNext) || At<void*>(entry, 0) != fixed.ptr) continue;
        return At<void*>(entry, ids::kOffMenuEntryMenu);
    }
    return nullptr;
}

// Calls a movie callback on the open menu `name` as its movie does, with one number or none, at UI time --
// the runner ticks on a job thread that can run while that movie is drawn. False when the menu is not open;
// `then` follows the call in the same task.
// See: docs/commentary/morrowind_runtime.md#movies-are-touched-at-ui-time
bool CallMovie(FxCallbackFn callback, const char* name, const double* number, void (*then)() = nullptr) {
    if (!callback || !MenuObject(name)) return false;
    const bool numbered = number != nullptr;
    const double n = numbered ? *number : 0.0;
    return PostToUiTask([callback, name, numbered, n, then]() {
        void* menu = MenuObject(name);
        if (!menu) return;
        alignas(16) char value[ids::kGfxValueSize] = {};
        alignas(16) char args[0x40] = {};
        *reinterpret_cast<void**>(args + ids::kOffFxHandler) = menu;
        if (numbered) {
            *reinterpret_cast<std::uint32_t*>(value + ids::kOffGfxType) = ids::kGfxNumber;
            *reinterpret_cast<double*>(value + ids::kOffGfxNumber) = n;
            *reinterpret_cast<void**>(args + ids::kOffFxArgs) = value;
            *reinterpret_cast<std::uint32_t*>(args + ids::kOffFxArgCount) = 1;
        }
        callback(args);
        if (then) then();
    });
}

bool InWorld() {
    void* player = Player();
    return player && g_c.parentCell && g_c.parentCell(g_api.vm, 0, player);
}

bool Talking() { return MenuOpen(ids::kDialogueMenuName); }

// A pausing menu other than the dialogue holds the game.
bool Held() {
    void* manager = g_c.menuManager ? *g_c.menuManager : nullptr;
    if (!manager) return false;
    std::uint32_t pausing = At<std::uint32_t>(manager, ids::kOffMenuNumPauseGame);
    void* dialogue = Talking() ? MenuObject(ids::kDialogueMenuName) : nullptr;
    if (dialogue && (At<std::uint32_t>(dialogue, ids::kOffMenuFlags) & ids::kMenuPausesGame)) {
        --pausing;
    }
    return pausing > 0;
}

// Whether the open conversation is with `id`; true when that cannot be told, so nothing is closed on a guess.
bool TalkingWith(const std::string& id) {
    void* manager = g_c.topicManager ? *g_c.topicManager : nullptr;
    void* npc = RefNamed(id);
    const std::uint32_t speaker = manager ? At<std::uint32_t>(manager, ids::kOffTopicSpeaker) : 0;
    if (!npc || !speaker) return true;
    return (speaker & ids::kHandleIndexMask) ==
           (At<std::uint32_t>(npc, ids::kOffRefHandleCount) >> ids::kHandleIndexShift);
}

// Whether the engine still holds a speaker for the player: a scene spoken at the player without a menu (Savlian's
// chapel speech) keeps one until its last line, and the engine opens no new conversation until then.
bool SomeoneSpeaking() {
    void* manager = g_c.topicManager ? *g_c.topicManager : nullptr;
    return manager && At<std::uint32_t>(manager, ids::kOffTopicSpeaker) != 0;
}

// Whether `actor` is sitting down, getting up or lying down: the engine swallows a player's activation then.
std::uint32_t SitState(void* actor) {
    return At<std::uint32_t>(actor, ids::kOffActorState1) >> ids::kSitStateShift & ids::kSitStateMask;
}

bool BetweenSeatAndFeet(void* actor) {
    const std::uint32_t state = SitState(actor);
    return state != 0 && state != 3 && state != 7;
}

int Approach(const std::string& id);
bool CanTalkFrom(void* npc, void* player, const std::string& id);
bool MoveLanding();

// Activates `id` as the player does. Talking to an actor waits, as a player does, while someone is still speaking
// to the player (run 48a: a talk at the camp opened nothing during Savlian's chapel speech). An actor that has
// walked out of talking reach since `approach` (at test pace a wandering NPC crosses a room between two steps)
// is walked up to again first: false while it is out of reach, as the runner retries (a "walked off" answer posts
// no move, and the next try makes a fresh one: Martin hopping seats at the camp, 60a). A move is let land first:
// asked again the next tick, Approach took the unlanded front move for a missed one and put the player inside
// the ghost of Rielus, where no click opens anything (60a, mq09_first_blade). One in reach is not turned
// to: turning the player as the conversation opens closes it. The re-approach comes before the 3D test: an actor
// that has gone through a door is unloaded, and the player must follow it (run 53a, Martin at Cloud Ruler). One
// sitting down or getting up is waited for, as the engine ignores the click then (run 57f: Martin seat-hopping
// at the camp). An actor whose conversation with the player is already open has nothing left to click: its own
// force greet came first (mq05_baurus_book, 11-24: Baurus greets in the checkpoint's load gap).
bool Activate(const std::string& id) {
    void* ref = RefNamed(id);
    void* player = Player();
    if (!ref || !player || !g_c.activate || !g_c.is3DLoaded) return false;
    const bool actor = At<std::uint8_t>(ref, kFormType) == kFormTypeActor;
    if (actor && Talking() && TalkingWith(id)) return true;
    if (actor && SomeoneSpeaking()) return false;
    if (actor && MoveLanding()) return false;
    if (actor && !CanTalkFrom(ref, player, id)) {
        Approach(id);
        return false;
    }
    if (actor && BetweenSeatAndFeet(ref)) return false;
    if (!g_c.is3DLoaded(g_api.vm, 0, ref)) return false;
    g_c.activate(g_api.vm, 0, ref, player, false);
    return true;
}

void Take(const std::string& id) {
    void* ref = RefNamed(id);
    void* player = Player();
    if (ref && player && g_c.addItem) g_c.addItem(g_api.vm, 0, player, ref, 1, false);
}

void PressButton(int button) {
    const double index = button;
    CallMovie(g_c.buttonPress, ids::kMessageBoxMenuName, &index);
}

// A notice holding the game: the open message box, when it has a single button, gets that button clicked, as
// a player clicks a notice's OK (Blade1Script's practice reward comes whenever the player has stood 60 s near
// the practicing Blades). A box offering a choice is left for the case to answer.
bool DismissNotice() {
    if (MenuOpen(ids::kTutorialMenuName)) {
        Log("test: Skyrim's help popup holds the game -- it is closed with Exit");
        CloseMenuNamed(ids::kTutorialMenuName);
        return true;
    }
    const std::uint32_t queued = g_c.boxQueue ? At<std::uint32_t>(g_c.boxQueue, ids::kOffQueueCount) : 0;
    if (!queued || !MenuOpen(ids::kMessageBoxMenuName)) return false;
    char* box = At<char**>(g_c.boxQueue, 0)[queued - 1];
    if (!box || At<std::uint32_t>(box, ids::kOffBoxButtonCount) != 1) return false;
    const char* body = At<const char*>(box, ids::kOffBoxBody);
    Log("test: a one-button box holds the game -- its button is clicked: %.80s", body ? body : "");
    PressButton(0);
    return true;
}

void CloseMenu(const std::string& name) { CloseMenuNamed(name.c_str()); }

// The listed topics' text, in the order a click's index counts them.
std::vector<std::string> Topics() {
    std::vector<std::string> out;
    void* manager = g_c.topicManager ? *g_c.topicManager : nullptr;
    if (!manager) return out;
    for (void** node = FirstListedTopic(manager); node; node = static_cast<void**>(node[1])) {
        const char* text = node[0] ? At<const char*>(node[0], 0) : nullptr;
        const auto length = node[0] ? At<std::uint16_t>(node[0], 8) : 0;
        out.push_back(!text ? std::string() : length == 0xffff ? std::string(text)
                                                               : std::string(text, length));
    }
    return out;
}

bool ClickTopic(int index) {
    const std::vector<std::string> listed = Topics();
    if (index < 0 || index >= static_cast<int>(listed.size())) return false;
    const double number = index;
    return CallMovie(g_c.topicClicked, ids::kDialogueMenuName, &number);
}

// The listed topic whose text is `topic` (any case), else the one topic containing it.
bool Ask(const std::string& topic) {
    const std::vector<std::string> listed = Topics();
    const std::string want = Lower(topic);
    int contains = -1, containing = 0;
    for (std::size_t i = 0; i < listed.size(); ++i) {
        const std::string text = Lower(listed[i]);
        if (text == want) return ClickTopic(static_cast<int>(i));
        if (text.find(want) != std::string::npos) {
            contains = static_cast<int>(i);
            ++containing;
        }
    }
    return containing == 1 && ClickTopic(contains);
}

bool Choose(int index) { return ClickTopic(index - 1); }

// Leaves the conversation as the Tab key does: the menu's own CloseMenu callback marks the exit as the player's,
// and the hide message takes the menu down even while a choice list is showing (the callback alone waits for a
// line to end). While the choice hold keeps the player in, Tab goes to the movie's cancel method as a player's
// does, and the answer is false: Goodbye is locked by a pending choice.
bool Bye() {
    if (ExitHeld()) {
        PostToUiTask([]() {
            ForEachOpenMovie([](void* view) {
                if (MovieDefined(view, ids::kDialogueCancelPath)) InvokeMovie(view, ids::kDialogueCancelPath);
            });
        });
        return false;
    }
    return CallMovie(g_c.closeDialogue, ids::kDialogueMenuName, nullptr,
                     []() { CloseMenuNamed(ids::kDialogueMenuName); });
}

std::string HexId(void* form) {
    char hex[16];
    std::snprintf(hex, sizeof(hex), "%08X", At<std::uint32_t>(form, kFormID));
    return hex;
}

// The game clock in milliseconds, which MorrowindRuntime keeps for every converted game; 0 without it.
unsigned GameMs() {
    using ClockFn = std::uint32_t (*)();
    static const auto clock = [] {
        HMODULE runtime = GetModuleHandleA(ids::kMorrowindRuntimeDll);
        return runtime ? reinterpret_cast<ClockFn>(GetProcAddress(runtime, "TESTestGameMs")) : nullptr;
    }();
    return clock ? clock() : 0;
}

// Every AddSubtitle call: the line is noted for the runner, then shown as the
// engine meant.
void AddSubtitleHook(void* manager, void* speaker, const char* text, bool force) {
    if (speaker && text && *text) {
        const std::lock_guard<std::mutex> lock(g_spokenMutex);
        g_spoken.push_back({HexId(speaker), text, GetTickCount64(), GameMs()});
    }
    g_c.addSubtitle(manager, speaker, text, force);
}

std::vector<test::SpokenLine> Spoken() {
    const std::lock_guard<std::mutex> lock(g_spokenMutex);
    std::vector<test::SpokenLine> lines;
    lines.swap(g_spoken);
    return lines;
}

float Apart(void* a, void* b) {
    const float dx = RefPositionAxis(g_api.vm, a, 0) - RefPositionAxis(g_api.vm, b, 0);
    const float dy = RefPositionAxis(g_api.vm, a, 1) - RefPositionAxis(g_api.vm, b, 1);
    return std::sqrt(dx * dx + dy * dy);
}

// Turns the player to look at `target`. A player left facing away has the
// third-person camera sitting on whoever stands behind them; the engine fades
// an actor that close to the camera out, and opens no dialogue with one.
void FacePlayerTo(void* target) {
    void* player = Player();
    const float dx = RefPositionAxis(g_api.vm, target, 0) - RefPositionAxis(g_api.vm, player, 0);
    const float dy = RefPositionAxis(g_api.vm, target, 1) - RefPositionAxis(g_api.vm, player, 1);
    SetRefAngle(g_api.vm, player, 0.0f, 0.0f, std::atan2(dx, dy) * kDegreesPerRadian);
}

// `approach`: puts the player in front of `id`, wherever it stands, unless
// already within talking reach in the same cell -- as a player walks up to
// whoever they talk to -- and has them look at it. 1 when a move was posted,
// 0 when none is needed, -1 while `id` does not resolve. A second move to the same
// NPC goes onto it: its front spot can be outside its cage or over a drop (Menien, runs 59b, 59 replays).
// Whether the player stands within talking reach of `npc` in the same place. Two exterior cells count as
// one place: an NPC beside a cell border is never in the player's cell from 80 away, and the move repeated
// forever.
bool InTalkReach(void* npc, void* player) {
    if (!g_c.parentCell) return true;
    void* npcCell = g_c.parentCell(g_api.vm, 0, npc);
    void* playerCell = g_c.parentCell(g_api.vm, 0, player);
    const bool outdoors = npcCell && playerCell && g_c.cellIsInterior &&
                          !g_c.cellIsInterior(g_api.vm, 0, npcCell) && !g_c.cellIsInterior(g_api.vm, 0, playerCell);
    return (npcCell == playerCell || outdoors) && Apart(player, npc) <= kTalkReach;
}

// The NPC the last front move went to, and where it stood then: a second move to a standing NPC that stayed put
// means the front spot does not hold the player (outside a cage, over a drop: Menien, 59b), so that move goes
// onto the NPC. A walking NPC gets a front move again (Savlian leaving for the chapel, 60a). One that sat down or
// changed seats since, while the player stands where it was, got its walk-up: the step ends there and `talk`'s
// activation catches it up (Jauffre changing seats every second at test pace missed 10 moves, 60a).
std::string g_frontTried;
float g_frontAt[2] = {0.0f, 0.0f};
constexpr float kStayedPut = 64.0f;
// Closer than this the player stands inside the NPC (a case's moveto onto it) and no click opens a
// conversation (60b: Jauffre 0.00 away, three clicks); the player steps out to the front spot, unless the
// last move put it there on purpose (`g_onto`).
constexpr float kInsideIt = 40.0f;
std::string g_onto;

// Whether the player can talk to `npc` (`id`) from where it stands: in reach and not inside it.
bool CanTalkFrom(void* npc, void* player, const std::string& id) {
    return InTalkReach(npc, player) && (Apart(player, npc) >= kInsideIt || g_onto == id);
}

// How far `npc`, last found at g_frontAt, has moved to `at`.
float MovedSinceFront(const float at[2]) {
    return std::hypot(at[0] - g_frontAt[0], at[1] - g_frontAt[1]);
}

int Approach(const std::string& id) {
    void* npc = RefNamed(id);
    void* player = Player();
    if (!npc || !player || !g_c.moveTo || !g_c.parentCell) return -1;
    if (CanTalkFrom(npc, player, id)) {
        FacePlayerTo(npc);
        return 0;
    }
    const float at[2] = {RefPositionAxis(g_api.vm, npc, 0), RefPositionAxis(g_api.vm, npc, 1)};
    const bool stayed = MovedSinceFront(at) < kStayedPut;
    const bool seated = SitState(npc) != 0;
    const float mine[2] = {RefPositionAxis(g_api.vm, player, 0), RefPositionAxis(g_api.vm, player, 1)};
    if (g_frontTried == id && !stayed && seated &&
        std::hypot(mine[0] - g_frontAt[0], mine[1] - g_frontAt[1]) <= kTalkReach) {
        g_frontTried.clear();
        Log("test: %s walked off from where the player came up to it", id.c_str());
        return 0;
    }
    const bool onto = g_frontTried == id && !seated && stayed;
    g_frontTried = onto ? std::string() : id;
    g_onto = onto ? id : std::string();
    g_frontAt[0] = at[0];
    g_frontAt[1] = at[1];
    const float heading = RefAngleDegrees(npc, 2) / kDegreesPerRadian;
    const float standOff = onto ? 0.0f : kTalkStandOff;
    g_c.moveTo(g_api.vm, 0, player, npc, std::sin(heading) * standOff, std::cos(heading) * standOff, 0.0f, false);
    g_moveLanding = kMoveLandTicks;
    Log("test: the player walks up to %s", id.c_str());
    return 1;
}

// Whether the player's last `approach` move may still be landing: a move into another worldspace lands only once
// its loading screen is gone, and the landing ticks count from then (run 58f: 10 moves posted during one load).
bool MoveLanding() {
    if (g_moveLanding > 0 && MenuOpen(ids::kLoadingMenuName)) return true;
    return g_moveLanding > 0 && g_moveLanding-- > 0;
}

void SetPlayerDriven(bool on) {
    if (g_c.aiDriven) g_c.aiDriven(g_api.vm, 0, nullptr, on);
}

// `where` is a reference's runtime FormID in hex, or `x y z` in the player's cell. A reference already within
// arrival distance is reached without driving the player: a force-greet that comes due while the player is
// AI-driven is dropped for good (the Emperor's amulet greeting, 60a replays: walk from 72 away).
void WalkTo(const std::string& where) {
    g_walk = Walk{};
    g_walk.toPoint = sscanf_s(where.c_str(), "%f %f %f", &g_walk.x, &g_walk.y, &g_walk.z) == 3;
    g_walk.target = g_walk.toPoint ? nullptr : RefNamed(where);
    void* player = Player();
    if (g_walk.target && player && Apart(player, g_walk.target) <= kArrivedDistance) return;
    g_walk.settleTicks = kWalkSettleTicks;
    if (g_c.thirdPerson) g_c.thirdPerson(g_api.vm, 0, nullptr);
    SetPlayerDriven(true);
}

// The point a walk to `x y z` aims at: an XMarker set down there.
void* PointMarker(void* player) {
    void* base = FormFromFile(ids::kXMarker, ids::kSkyrimMaster);
    void* marker = base && g_c.placeAtMe ? g_c.placeAtMe(g_api.vm, 0, player, base, 1, true, false)
                                         : nullptr;
    if (marker) SetRefPosition(g_api.vm, marker, g_walk.x, g_walk.y, g_walk.z);
    return marker;
}

// Asks the engine for the path, and again after kWalkRetryTicks.
void RequestPath(void* player) {
    if (!g_walk.target && g_walk.toPoint) g_walk.target = g_walk.marker = PointMarker(player);
    const std::int32_t request = g_walk.target && g_c.pathTo
                                     ? g_c.pathTo(player, g_walk.target, kWalkSpeed)
                                     : -1;
    Log("test: the player walks from (%g, %g)%s", RefPositionAxis(g_api.vm, player, 0),
        RefPositionAxis(g_api.vm, player, 1), request < 0 ? " -- NO path" : "");
}

void WalkStop() {
    SetPlayerDriven(false);
    if (g_walk.marker && g_c.deleteRef) g_c.deleteRef(g_api.vm, 0, g_walk.marker);
    g_walk = Walk{};
}

// Whether the walk has arrived, which ends it.
bool WalkDone() {
    void* player = Player();
    if (!player) return false;
    if (g_walk.settleTicks > 0) {
        if (--g_walk.settleTicks == 0) RequestPath(player);
        return false;
    }
    if (g_walk.target && Apart(player, g_walk.target) <= kArrivedDistance) {
        WalkStop();
        return true;
    }
    if (++g_walk.ticks % kWalkRetryTicks == 0) RequestPath(player);
    return false;
}

// `pick <name>`: the class and birthsign menus a converted chargen opens are
// MorrowindRuntime's (TES4_Chargen.Ask), so the pick is its own, exported.
bool Pick(const std::string& name) {
    using PickFn = bool (*)(const char* name);
    HMODULE runtime = GetModuleHandleA(ids::kMorrowindRuntimeDll);
    const auto pick = runtime ? reinterpret_cast<PickFn>(GetProcAddress(runtime, "TESTestPick")) : nullptr;
    return pick && pick(name.c_str());
}

// `pace <speed>`: MorrowindRuntime's frame governor steers every converted game, so the speed is set there.
void Pace(float speed) {
    using PaceFn = bool (*)(float speed);
    HMODULE runtime = GetModuleHandleA(ids::kMorrowindRuntimeDll);
    const auto pace = runtime ? reinterpret_cast<PaceFn>(GetProcAddress(runtime, "TESTestPace")) : nullptr;
    if (!pace || !pace(speed)) Log("test: no pace -- MorrowindRuntime's governor is not loaded");
}

// `defend`: MorrowindRuntime walks the actors for every converted game and has the player kill its attackers.
// `ally` is the runtime FormID in hex of an actor the player fights beside, or empty.
void KillAttackersOf(const std::string& ally) {
    using KillFn = bool (*)(std::uint32_t ally);
    HMODULE runtime = GetModuleHandleA(ids::kMorrowindRuntimeDll);
    const auto kill =
        runtime ? reinterpret_cast<KillFn>(GetProcAddress(runtime, "TESTestKillPlayerAttackers")) : nullptr;
    const auto id = static_cast<std::uint32_t>(std::strtoul(ally.c_str(), nullptr, 16));
    if (!kill || !kill(id)) Log("test: no defend -- MorrowindRuntime is not loaded");
}

void KillPlayerAttackers() { KillAttackersOf(""); }

// A MorrowindRuntime export, or null: it does the world's work for every converted game.
template <typename Fn>
Fn RuntimeCall(const char* name) {
    HMODULE runtime = GetModuleHandleA(ids::kMorrowindRuntimeDll);
    return runtime ? reinterpret_cast<Fn>(GetProcAddress(runtime, name)) : nullptr;
}

// A step's reference for MorrowindRuntime: its runtime FormID, 0 for the player.
std::uint32_t RuntimeId(const std::string& id) {
    return Lower(id) == "player" ? 0 : static_cast<std::uint32_t>(std::strtoul(id.c_str(), nullptr, 16));
}

// `follow`: MorrowindRuntime's own position, distance and placement calls, as its cases use.
float Position(const std::string& ref, int axis) {
    const auto position = RuntimeCall<float (*)(std::uint32_t, int)>("TESTestPosition");
    return position ? position(RuntimeId(ref), axis) : 0.0f;
}

float Distance(const std::string& from, const std::string& to) {
    const auto distance = RuntimeCall<float (*)(std::uint32_t, std::uint32_t)>("TESTestDistance");
    return distance ? distance(RuntimeId(from), RuntimeId(to)) : 0.0f;
}

void PlaceHere(const std::string& ref, float x, float y, float z, float zRot) {
    const auto place = RuntimeCall<void (*)(std::uint32_t, float, float, float, float)>("TESTestPlaceHere");
    if (place) place(RuntimeId(ref), x, y, z, zRot);
}

void HookSubtitles() {
    const std::uintptr_t add = Resolve("SubtitleManager::AddSubtitle", ids::kAddSubtitle, nullptr);
    g_c.addSubtitle = reinterpret_cast<AddSubtitleFn>(add);
    const int patched = add ? PatchAllCalls(add, reinterpret_cast<void*>(&AddSubtitleHook),
                                            "SubtitleManager::AddSubtitle")
                            : 0;
    Log("test: spoken lines %s (%d call site(s))", patched ? "heard" : "NOT heard", patched);
}

template <typename T>
void Bind(T& slot, const char* name, std::uint64_t id) {
    slot = reinterpret_cast<T>(Resolve(name, id, nullptr));
}

bool ResolveCalls() {
    Bind(g_c.player, "Game.GetPlayer", ids::kGetPlayer);
    Bind(g_c.parentCell, "ObjectReference.GetParentCell", ids::kRefGetParentCell);
    Bind(g_c.cellIsInterior, "Cell.IsInterior", ids::kCellIsInterior);
    Bind(g_c.is3DLoaded, "ObjectReference.Is3DLoaded", ids::kRefIs3DLoaded);
    Bind(g_c.activate, "ObjectReference.Activate", ids::kRefActivate);
    Bind(g_c.addItem, "ObjectReference.AddItem", ids::kRefAddItem);
    Bind(g_c.menuManager, "MenuManager singleton", ids::kMenuManagerSingleton);
    Bind(g_c.topicManager, "MenuTopicManager singleton", ids::kMenuTopicManager);
    Bind(g_c.topicClicked, "DialogueMenu TopicClicked", ids::kDialogueTopicClicked);
    Bind(g_c.closeDialogue, "DialogueMenu CloseMenu", ids::kDialogueCloseMenu);
    Bind(g_c.buttonPress, "MessageBoxMenu buttonPress", ids::kMessageBoxButtonPress);
    Bind(g_c.pathTo, "Actor path request", ids::kActorPathTo);
    Bind(g_c.aiDriven, "Game.SetPlayerAIDriven", ids::kGameSetPlayerAIDriven);
    Bind(g_c.thirdPerson, "Game.ForceThirdPerson", ids::kGameForceThirdPerson);
    Bind(g_c.placeAtMe, "ObjectReference.PlaceAtMe", ids::kRefPlaceAtMe);
    Bind(g_c.deleteRef, "ObjectReference.Delete", ids::kRefDelete);
    Bind(g_c.moveTo, "ObjectReference.MoveTo", ids::kRefMoveTo);
    Bind(g_c.boxQueue, "message box queue", ids::kMessageBoxQueue);
    return g_c.player && g_c.parentCell && ResolveMovieCalls() && g_c.menuManager && ResolveGlide();
}

}  // namespace

void InstallTestRunner() {
    if (!test::TestCasesRequested()) return;
    if (!ResolveCalls()) {
        Log("test: the runner's engine calls did not resolve; no cases run");
        return;
    }
    test::TestBackend game;
    game.inWorld = InWorld;
    game.held = Held;
    game.menuOpen = MenuOpen;
    game.activate = Activate;
    game.take = Take;
    game.pressButton = PressButton;
    game.closeMenu = CloseMenu;
    game.talking = Talking;
    game.topics = Topics;
    game.ask = Ask;
    game.choose = Choose;
    game.choices = Topics;
    game.bye = Bye;
    game.menuPick = Pick;
    game.setPace = Pace;
    game.killPlayerAttackers = KillPlayerAttackers;
    game.killAttackersOf = KillAttackersOf;
    game.walkTo = WalkTo;
    game.approach = Approach;
    game.position = Position;
    game.distance = Distance;
    game.placeHere = PlaceHere;
    game.talkingWith = TalkingWith;
    game.dismissNotice = DismissNotice;
    game.talkClicks = 2;
    game.fillTrail = true;
    // A converted escort that has stopped walks on only with the player inside about 85 (measured); the
    // runner rests the player up to one trail step (40) short of its spot, so the spot is the NPC's own.
    game.followSlack = 60.0f;
    game.followBehind = 1.0f;
    game.moveLanding = MoveLanding;
    game.walkDone = WalkDone;
    game.walkStop = WalkStop;
    game.spoken = Spoken;
    game.gameMs = GameMs;
    HookSubtitles();
    test::SetTestBackend(game);
    const bool ticking = StartMainThreadTick(kTickMs, test::TickTestRunner);
    Log("test: runner %s", ticking ? "ticking" : "NOT started (no task interface)");
}

}  // namespace tesruntime
