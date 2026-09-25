// The engine side of the alchemy apparatus: three vtable swaps, no bytes
// patched, on top of crafting.cpp's bench opener.
//   InventoryMenu::Accept     -- `ItemSelect` on an apparatus closes the
//                                inventory and opens the alchemy bench.
//   AlchemyMenu slot 5        -- the craft event is refused with no mortar.
//   ModEffectivenessFunctor 1 -- after Skyrim sets an effect's magnitude and
//                                duration, the carried tools scale them.
// See: docs/commentary/tes_runtime_alchemy.md#alchemy-apparatus

#include "alchemy.h"

#include <windows.h>

#include <cmath>
#include <cstdint>
#include <fstream>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#include "addresses.h"
#include "crafting.h"
#include "engine.h"
#include "ids.h"
#include "log.h"
#include "paths.h"
#include "ui_message.h"

namespace tesruntime {

namespace {

using AcceptFn = void (*)(void* menu, void* processor);
using ProcessFn = void (*)(void* processor, void* name, void* callback);
using CallbackFn = void (*)(void* args);
using SelectedItemFn = void* (*)(void* itemList);
using CombatFn = bool (*)(void* vm, std::uint32_t stack, void* actor);
using ItemCountFn = std::int32_t (*)(void* vm, std::uint32_t stack, void* ref,
                                     void* item);
using NotificationFn = void (*)(void* vm, std::uint32_t stack, void* tag,
                                void* text);
using ActorValueFn = float (*)(void* owner, int value);
using EffectFn = bool (*)(void* functor, void* effect);
using GetMagnitudeFn = float (*)(void* effect);
using SetMagnitudeFn = bool (*)(void* effect, float value);
using GetDurationFn = std::uint32_t (*)(void* effect);
using SetDurationFn = bool (*)(void* effect, std::int32_t value);
using UserEventFn = bool (*)(void* subMenu, void* event);

// One resolved apparatus.
struct Apparatus {
    void* form = nullptr;
    int type = kMortarPestle;
    float quality = 0.0f;
};

std::vector<Apparatus> g_apparatus;
std::unordered_map<std::uint32_t, std::size_t> g_byFormId;
AlchemySettings g_settings;

void**          g_playerSlot = nullptr;
AcceptFn        g_acceptOriginal = nullptr;
CallbackFn      g_itemSelect = nullptr;
CallbackFn      g_closeTween = nullptr;
UserEventFn     g_userEventOriginal = nullptr;
void**          g_userEvents = nullptr;
SelectedItemFn  g_selectedItem = nullptr;
CombatFn        g_isInCombat = nullptr;
ItemCountFn     g_itemCount = nullptr;
NotificationFn  g_notification = nullptr;
EffectFn        g_effectOriginal = nullptr;
GetMagnitudeFn  g_getMagnitude = nullptr;
SetMagnitudeFn  g_setMagnitude = nullptr;
GetDurationFn   g_getDuration = nullptr;
SetDurationFn   g_setDuration = nullptr;

// The open crafting menu is an apparatus's, so its effects are scaled.
bool g_active = false;
Toolset g_tools;
AlchemyInputs g_inputs;

void* Player() { return g_playerSlot ? *g_playerSlot : nullptr; }

std::uint32_t FormIdOf(void* form) {
    return form ? At<std::uint32_t>(form, kFormID) : 0;
}

// Debug.Notification; nothing for an empty text.
void Notify(const std::string& text) {
    if (text.empty() || !g_notification) return;
    FixedString message(text.c_str());
    g_notification(g_api.vm, 0, nullptr, &message.ptr);
}

// ------------------------------------------------------------ the session ----

AlchemyInputs Inputs() {
    AlchemyInputs in = g_settings.inputs;
    if (void* player = Player()) {
        void* owner = static_cast<char*>(player) + kActorValueOwner;
        in.skill = VCall<ActorValueFn>(owner, 1)(owner, ids::kActorValueAlchemy);
    }
    return in;
}

// The best of each type in the player's inventory right now.
Toolset Census() {
    Toolset tools;
    void* player = Player();
    if (!player || !g_itemCount) return tools;
    for (const Apparatus& apparatus : g_apparatus) {
        if (g_itemCount(g_api.vm, 0, player, apparatus.form) > 0) {
            tools.Offer(apparatus.type, apparatus.quality);
        }
    }
    return tools;
}

void BeginSession() {
    g_tools = Census();
    g_inputs = Inputs();
    g_active = true;
    Log("alchemy: session -- mortar %.2f alembic %.2f calcinator %.2f retort "
        "%.2f; skill %.0f int %.0f luck %.0f", g_tools.quality[kMortarPestle],
        g_tools.quality[kAlembic], g_tools.quality[kCalcinator],
        g_tools.quality[kRetort], g_inputs.skill, g_inputs.intelligence,
        g_inputs.luck);
}

void EndSession() { g_active = false; }

// ------------------------------------------------------ potion strength ----

std::int32_t RoundHalfUp(float value) {
    return static_cast<std::int32_t>(std::floor(value + 0.5f));
}

// Scales one effect by the session's tools, through the engine's own setters
// so its cost is recomputed.
void ScaleEffect(void* effect) {
    void* setting = At<void*>(effect, ids::kOffEffectSetting);
    if (!setting) return;
    const std::uint32_t flags = At<std::uint32_t>(setting, ids::kOffEffectFlags);
    const float cost = At<float>(setting, ids::kOffEffectBaseCost);
    const float magnitude = ApparatusScale(g_tools, g_inputs, flags, cost, true);
    if (magnitude != 1.0f && !(flags & kEffectNoMagnitude)) {
        g_setMagnitude(effect, static_cast<float>(RoundHalfUp(
                                   g_getMagnitude(effect) * magnitude)));
    }
    const float duration = ApparatusScale(g_tools, g_inputs, flags, cost, false);
    if (duration != 1.0f && !(flags & kEffectNoDuration)) {
        g_setDuration(effect, RoundHalfUp(static_cast<float>(
                                  g_getDuration(effect)) * duration));
    }
}

bool ApplyEffectiveness(void* functor, void* effect) {
    const bool result = g_effectOriginal(functor, effect);
    if (g_active && effect) ScaleEffect(effect);
    return result;
}

// ---------------------------------------------------------- the brew gate ----

// Whether `event` (a BSFixedString*) is the one that starts a brew.
bool IsCraftEvent(void* event) {
    void* events = g_userEvents ? *g_userEvents : nullptr;
    return event && events &&
           At<void*>(event, 0) == At<void*>(events, ids::kOffUserEventCraft);
}

// OpenMW's Result_NoMortarAndPestle: the brew is refused and nothing is
// consumed. Every other event, and every other session, passes through.
bool AlchemyUserEvent(void* subMenu, void* event) {
    if (g_active && !g_tools.has[kMortarPestle] && IsCraftEvent(event)) {
        Log("alchemy: brew refused -- no mortar and pestle carried");
        Notify(g_settings.noMortar);
        return true;
    }
    return g_userEventOriginal(subMenu, event);
}

// ------------------------------------------------------------ inventory ----

// OpenMW's ActionAlchemy: refused in combat, otherwise the alchemy window.
// The inventory leaves the way its own full exit does, through
// CloseTweenMenu: the TweenMenu under it would otherwise stay open, hidden.
void UseApparatus(void* args) {
    void* player = Player();
    if (player && g_isInCombat && g_isInCombat(g_api.vm, 0, player)) {
        Log("alchemy: apparatus used in combat -- refused");
        Notify(g_settings.inCombat);
        return;
    }
    g_closeTween(args);
    CloseMenuNamed(ids::kInventoryMenuName);
    OpenBench(Bench::kAlchemy, {BeginSession, EndSession});
}

// The form of the entry the inventory has highlighted, or null.
void* SelectedForm(void* args) {
    void* menu = At<void*>(args, ids::kOffDelegateArgsHandler);
    void* list = menu ? At<void*>(menu, ids::kOffInventoryItemList) : nullptr;
    void* entry = list ? g_selectedItem(list) : nullptr;
    return entry ? At<void*>(entry, 0) : nullptr;
}

void ItemSelect(void* args) {
    const auto found = g_byFormId.find(FormIdOf(SelectedForm(args)));
    if (found == g_byFormId.end()) {
        g_itemSelect(args);
        return;
    }
    UseApparatus(args);
}

// Stands in for the processor Accept registers through: `ItemSelect` is
// handed OUR callback, every other registration passes through untouched.
struct ProcessorProxy {
    void** vtable;
    void* real;
};

void ProxyDtor(ProcessorProxy*) {}

void ProxyProcess(ProcessorProxy* self, void* name, void* callback) {
    void* chosen = callback == reinterpret_cast<void*>(g_itemSelect)
                       ? reinterpret_cast<void*>(&ItemSelect)
                       : callback;
    VCall<ProcessFn>(self->real, 1)(self->real, name, chosen);
}

void* g_proxyVtable[] = {reinterpret_cast<void*>(&ProxyDtor),
                         reinterpret_cast<void*>(&ProxyProcess)};

void InventoryAccept(void* menu, void* processor) {
    ProcessorProxy proxy{g_proxyVtable, processor};
    g_acceptOriginal(menu, &proxy);
}

// -------------------------------------------------------------- install ----

std::vector<ApparatusDef> g_rows;

void LoadSidecar(const std::string& name, const Json& doc) {
    const std::size_t before = g_rows.size();
    ReadApparatus(doc, &g_rows, &g_settings);
    Log("alchemy: %s -- %zu apparatus", name.c_str(), g_rows.size() - before);
}

std::string ReadText(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    std::ostringstream text;
    text << in.rdbuf();
    return text.str();
}

// DEPRECATED, to be removed: each pre-split MorrowindRuntime\<plugin>\ folder
// with an APPA.txt, when that plugin has no apparatus.json.
// See: docs/reference/tes_runtime_fragments.md#legacy-sidecar-paths
void LoadLegacySidecars() {
    const std::string root = PluginsDir() + "MorrowindRuntime\\";
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA((root + "*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        const std::string stem = fd.cFileName;
        const std::string dir = root + stem + "\\";
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || stem[0] == '.' ||
            GetFileAttributesA((dir + "APPA.txt").c_str()) == INVALID_FILE_ATTRIBUTES ||
            GetFileAttributesA((SidecarDir() + stem + ".apparatus.json").c_str()) !=
                INVALID_FILE_ATTRIBUTES) {
            continue;
        }
        const std::size_t before = g_rows.size();
        ReadLegacyApparatus(ReadText(dir + "APPA.txt"), ReadText(dir + "GMST.txt"),
                            ReadText(dir + "NPC_.txt"), &g_rows, &g_settings);
        Log("alchemy: DEPRECATED -- %zu apparatus read from %sAPPA.txt; rebuild "
            "%s to move them to %s", g_rows.size() - before, dir.c_str(),
            stem.c_str(), SidecarDir().c_str());
    } while (FindNextFileA(h, &fd));
    FindClose(h);
}

std::size_t ResolveApparatus() {
    g_apparatus.clear();
    g_byFormId.clear();
    for (const ApparatusDef& row : g_rows) {
        void* form = FormFromFile(row.local, row.file);
        if (!form) continue;
        g_byFormId[FormIdOf(form)] = g_apparatus.size();
        g_apparatus.push_back({form, row.type, row.quality});
    }
    Log("alchemy: %zu of %zu staged apparatus resolved", g_apparatus.size(),
        g_rows.size());
    return g_apparatus.size();
}

template <typename Fn>
Fn Address(const char* name, std::uint64_t id) {
    return reinterpret_cast<Fn>(Resolve(name, id, nullptr));
}

void ResolveNatives() {
    g_playerSlot = Address<void**>("PlayerCharacter singleton",
                                   ids::kPlayerSingleton);
    g_itemSelect = Address<CallbackFn>("InventoryMenu ItemSelect",
                                       ids::kInventoryItemSelect);
    g_closeTween = Address<CallbackFn>("InventoryMenu CloseTweenMenu",
                                       ids::kInventoryCloseTween);
    g_userEvents = Address<void**>("UserEvents singleton",
                                   ids::kUserEventsSingleton);
    g_selectedItem = Address<SelectedItemFn>("ItemList::GetSelectedItem",
                                             ids::kItemListSelected);
    g_isInCombat = Address<CombatFn>("Actor.IsInCombat", ids::kActorIsInCombat);
    g_itemCount = Address<ItemCountFn>("ObjectReference.GetItemCount",
                                       ids::kRefGetItemCount);
    g_notification = Address<NotificationFn>("Debug.Notification",
                                             ids::kDebugNotification);
    g_getMagnitude = Address<GetMagnitudeFn>("Effect::GetMagnitude",
                                             ids::kEffectGetMagnitude);
    g_setMagnitude = Address<SetMagnitudeFn>("Effect::SetMagnitude",
                                             ids::kEffectSetMagnitude);
    g_getDuration = Address<GetDurationFn>("Effect::GetDuration",
                                           ids::kEffectGetDuration);
    g_setDuration = Address<SetDurationFn>("Effect::SetDuration",
                                           ids::kEffectSetDuration);
}

void* Swap(const char* name, std::uint64_t vtable, std::size_t slot,
           std::uint64_t expected, void* replacement) {
    return SwapVtableSlot(name, Resolve(name, vtable, nullptr), slot,
                          Resolve(name, expected, nullptr), replacement);
}

// The menu hooks go in together or not at all: an inventory that closes onto
// a crafting menu we cannot fill, or cannot gate, would strand the player.
// The inventory's swap goes LAST, since only it can start a session.
bool InstallMenus() {
    if (!g_itemSelect || !g_closeTween || !g_selectedItem || !g_userEvents ||
        !CraftingInstalled()) {
        return false;
    }
    g_userEventOriginal = reinterpret_cast<UserEventFn>(Swap(
        "AlchemyMenu::ProcessUserEvent", ids::kAlchemyMenuVtable,
        ids::kUserEventSlot, ids::kAlchemyMenuUserEvent,
        reinterpret_cast<void*>(&AlchemyUserEvent)));
    if (!g_userEventOriginal) return false;
    g_acceptOriginal = reinterpret_cast<AcceptFn>(Swap(
        "InventoryMenu::Accept", ids::kInventoryMenuVtable, ids::kAcceptSlot,
        ids::kInventoryMenuAccept, reinterpret_cast<void*>(&InventoryAccept)));
    return g_acceptOriginal != nullptr;
}

bool InstallEffectiveness() {
    if (!g_getMagnitude || !g_setMagnitude || !g_getDuration ||
        !g_setDuration) {
        return false;
    }
    g_effectOriginal = reinterpret_cast<EffectFn>(Swap(
        "ModEffectivenessFunctor", ids::kEffectivenessVtable,
        ids::kEffectivenessSlot, ids::kEffectivenessApply,
        reinterpret_cast<void*>(&ApplyEffectiveness)));
    return g_effectOriginal != nullptr;
}

}  // namespace

void InstallAlchemy() {
    LoadLegacySidecars();
    ForEachSidecar("apparatus.json", LoadSidecar);
    if (!ResolveApparatus()) {
        Log("alchemy: no apparatus staged -- NOT hooked");
        return;
    }
    ResolveNatives();
    const bool menus = InstallMenus();
    const bool effects = InstallEffectiveness();
    Log("alchemy: apparatus menu %s, potion strength %s",
        menus ? "hooked" : "NOT hooked", effects ? "hooked" : "NOT hooked");
}

}  // namespace tesruntime
