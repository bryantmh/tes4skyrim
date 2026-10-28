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
#include "glide.h"
#include "hook.h"
#include "ids.h"
#include "log.h"
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
using IsMenuOpenFn = bool (*)(void* manager, void** name);
using FxCallbackFn = void (*)(void* args);
using AddSubtitleFn = void (*)(void* manager, void* speaker, const char* text, bool force);
using PathToFn = std::int32_t (*)(void* actor, void* target, float speed);
using AiDrivenFn = void (*)(void* vm, std::uint32_t stack, void* tag, bool on);
using GlobalCallFn = void (*)(void* vm, std::uint32_t stack, void* tag);
using PlaceAtMeFn = void* (*)(void* vm, std::uint32_t stack, void* self, void* base,
                             std::int32_t count, bool forcePersist, bool initiallyDisabled);
using RefCallFn = void (*)(void* vm, std::uint32_t stack, void* ref);

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

struct Calls {
    GetPlayerFn player = nullptr;
    RefFormFn parentCell = nullptr;
    RefBoolFn is3DLoaded = nullptr;
    ActivateFn activate = nullptr;
    AddItemFn addItem = nullptr;
    IsMenuOpenFn isMenuOpen = nullptr;
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
};

Calls g_c;

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

bool MenuOpen(const std::string& name) {
    if (!g_c.isMenuOpen || !g_c.menuManager || !*g_c.menuManager) return false;
    FixedString fixed(name.c_str());
    return fixed.ptr && g_c.isMenuOpen(*g_c.menuManager, &fixed.ptr);
}

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

// Calls a movie callback on `menu` as its movie does, with one number or none.
bool CallMovie(FxCallbackFn callback, void* menu, const double* number) {
    if (!callback || !menu) return false;
    alignas(16) char value[ids::kGfxValueSize] = {};
    alignas(16) char args[0x40] = {};
    *reinterpret_cast<void**>(args + ids::kOffFxHandler) = menu;
    if (number) {
        *reinterpret_cast<std::uint32_t*>(value + ids::kOffGfxType) = ids::kGfxNumber;
        *reinterpret_cast<double*>(value + ids::kOffGfxNumber) = *number;
        *reinterpret_cast<void**>(args + ids::kOffFxArgs) = value;
        *reinterpret_cast<std::uint32_t*>(args + ids::kOffFxArgCount) = 1;
    }
    callback(args);
    return true;
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

bool Activate(const std::string& id) {
    void* ref = RefNamed(id);
    void* player = Player();
    if (!ref || !player || !g_c.activate || !g_c.is3DLoaded ||
        !g_c.is3DLoaded(g_api.vm, 0, ref)) {
        return false;
    }
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
    CallMovie(g_c.buttonPress, MenuObject(ids::kMessageBoxMenuName), &index);
}

void CloseMenu(const std::string& name) { CloseMenuNamed(name.c_str()); }

// The listed topics' text, in the order a click's index counts them.
std::vector<std::string> Topics() {
    std::vector<std::string> out;
    void* manager = g_c.topicManager ? *g_c.topicManager : nullptr;
    if (!manager) return out;
    auto* node = At<void**>(manager, ids::kOffTopicList);
    if (node && At<std::uint8_t>(manager, ids::kOffTopicSkipFirst)) node = static_cast<void**>(node[1]);
    for (; node; node = static_cast<void**>(node[1])) {
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
    return CallMovie(g_c.topicClicked, MenuObject(ids::kDialogueMenuName), &number);
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

bool Bye() { return CallMovie(g_c.closeDialogue, MenuObject(ids::kDialogueMenuName), nullptr); }

std::string HexId(void* form) {
    char hex[16];
    std::snprintf(hex, sizeof(hex), "%08X", At<std::uint32_t>(form, kFormID));
    return hex;
}

// Every AddSubtitle call: the line is noted for the runner, then shown as the
// engine meant.
void AddSubtitleHook(void* manager, void* speaker, const char* text, bool force) {
    if (speaker && text && *text) {
        const std::lock_guard<std::mutex> lock(g_spokenMutex);
        g_spoken.push_back({HexId(speaker), text});
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

void SetPlayerDriven(bool on) {
    if (g_c.aiDriven) g_c.aiDriven(g_api.vm, 0, nullptr, on);
}

// `where` is a reference's runtime FormID in hex, or `x y z` in the player's cell.
void WalkTo(const std::string& where) {
    g_walk = Walk{};
    g_walk.toPoint = sscanf_s(where.c_str(), "%f %f %f", &g_walk.x, &g_walk.y, &g_walk.z) == 3;
    g_walk.target = g_walk.toPoint ? nullptr : RefNamed(where);
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
    Bind(g_c.is3DLoaded, "ObjectReference.Is3DLoaded", ids::kRefIs3DLoaded);
    Bind(g_c.activate, "ObjectReference.Activate", ids::kRefActivate);
    Bind(g_c.addItem, "ObjectReference.AddItem", ids::kRefAddItem);
    Bind(g_c.isMenuOpen, "MenuManager::IsMenuOpen", ids::kMenuManagerIsMenuOpen);
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
    return g_c.player && g_c.parentCell && g_c.isMenuOpen && g_c.menuManager && ResolveGlide();
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
    game.walk = WalkTo;
    game.walkDone = WalkDone;
    game.walkStop = WalkStop;
    game.spoken = Spoken;
    HookSubtitles();
    test::SetTestBackend(game);
    const bool ticking = StartMainThreadTick(kTickMs, test::TickTestRunner);
    Log("test: runner %s", ticking ? "ticking" : "NOT started (no task interface)");
}

}  // namespace tesruntime
