// The engine side of the alchemy apparatus: four vtable swaps, no bytes
// patched.
//   InventoryMenu::Accept     -- `ItemSelect` on an apparatus closes the
//                                inventory and opens the crafting menu.
//   CraftingMenu slot 4       -- that open, with no furniture to read, builds
//                                the alchemy sub-menu the way bench type 5 does.
//   AlchemyMenu slot 5        -- the craft event is refused with no mortar.
//   ModEffectivenessFunctor 1 -- after Skyrim sets an effect's magnitude and
//                                duration, the carried tools scale them.
// See: docs/commentary/morrowind_runtime.md#alchemy-apparatus

#include "alchemy.h"

#include <cmath>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

#include "activation.h"
#include "addresses.h"
#include "dialogue_state.h"
#include "game_calls_internal.h"
#include "ids.h"
#include "log.h"
#include "menu.h"
#include "script_tables.h"
#include "store.h"

namespace mwruntime {

namespace {

using AcceptFn = void (*)(void* menu, void* processor);
using ProcessFn = void (*)(void* processor, void* name, void* callback);
using CallbackFn = void (*)(void* args);
using SelectedItemFn = void* (*)(void* itemList);
using CombatFn = bool (*)(void* vm, std::uint32_t stack, void* actor);
using NotificationFn = void (*)(void* vm, std::uint32_t stack, void* tag,
                                void* text);
using ItemCountFn = std::int32_t (*)(void* vm, std::uint32_t stack, void* ref,
                                     void* item);
using ProcessMessageFn = std::uint32_t (*)(void* menu, char* message);
using AllocFn = void* (*)(void* allocator, std::size_t size, void* tag);
using AlchemyCtorFn = void* (*)(void* self, void* movie, void* furniture);
using HelpFn = void (*)(std::uint32_t id);
using DelegateAddFn = void (*)(void* delegate, void* handler);
using ShowFn = void (*)(void* subMenu);
using EffectFn = bool (*)(void* functor, void* effect);
using GetMagnitudeFn = float (*)(void* effect);
using SetMagnitudeFn = bool (*)(void* effect, float value);
using GetDurationFn = std::uint32_t (*)(void* effect);
using SetDurationFn = bool (*)(void* effect, std::int32_t value);
using UserEventFn = bool (*)(void* subMenu, void* event);

// The id MWScript and the actor table use for the player.
constexpr const char* kPlayerId = "player";

// TES3's attribute order, which ActorDef::attributes keeps.
constexpr int kIntelligence = 1;
constexpr int kLuck = 7;

// The GMST OpenMW's ActionAlchemy shows when the player is fighting, and the
// one its AlchemyWindow shows for Result_NoMortarAndPestle.
constexpr const char* kInCombatGmst = "sInventoryMessage3";
constexpr const char* kNoMortarGmst = "sNotifyMessage45";

constexpr std::uint32_t kLocalMask = 0x00FFFFFF;

// One resolved apparatus.
struct Apparatus {
    void* form = nullptr;
    int type = kMortarPestle;
    float quality = 0.0f;
};

std::vector<Apparatus> g_apparatus;
std::unordered_map<std::uint32_t, std::size_t> g_byFormId;

AcceptFn        g_acceptOriginal = nullptr;
CallbackFn      g_itemSelect = nullptr;
CallbackFn      g_closeTween = nullptr;
UserEventFn     g_userEventOriginal = nullptr;
void**          g_userEvents = nullptr;
SelectedItemFn  g_selectedItem = nullptr;
CombatFn        g_isInCombat = nullptr;
NotificationFn  g_notification = nullptr;
ItemCountFn     g_itemCount = nullptr;
ProcessMessageFn g_craftingOriginal = nullptr;
void**          g_allocator = nullptr;
AlchemyCtorFn   g_alchemyCtor = nullptr;
HelpFn          g_help = nullptr;
DelegateAddFn   g_delegateAdd = nullptr;
void*           g_workbench = nullptr;
EffectFn        g_effectOriginal = nullptr;
GetMagnitudeFn  g_getMagnitude = nullptr;
SetMagnitudeFn  g_setMagnitude = nullptr;
GetDurationFn   g_getDuration = nullptr;
SetDurationFn   g_setDuration = nullptr;

// An apparatus asked for the crafting menu, and the next open is that one.
bool g_pending = false;
// The open crafting menu is an apparatus's, so its effects are scaled.
bool g_active = false;
Toolset g_tools;
AlchemyInputs g_inputs;


// ------------------------------------------------------------ the session ----

AlchemyInputs Inputs() {
    AlchemyInputs in;
    if (Hooks().actorValue) in.skill = Hooks().actorValue(kPlayerId, "Alchemy");
    if (const ActorDef* player = FindActor(kPlayerId)) {
        in.intelligence = static_cast<float>(player->attributes[kIntelligence]);
        in.luck = static_cast<float>(player->attributes[kLuck]);
    }
    in.strengthMult = GmstNumber("fPotionStrengthMult", in.strengthMult);
    in.magnitudeMult = GmstNumber("fPotionT1MagMult", in.magnitudeMult);
    in.durationMult = GmstNumber("fPotionT1DurMult", in.durationMult);
    return in;
}

// The best of each type in the player's inventory right now.
Toolset Census() {
    Toolset tools;
    void* player = gamecalls::PlayerRef();
    if (!player || !g_itemCount) return tools;
    for (const Apparatus& apparatus : g_apparatus) {
        if (g_itemCount(PapyrusVm(), 0, player, apparatus.form) > 0) {
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

// ------------------------------------------------------- crafting menu ----

// What CraftingMenu's bench-type-5 branch does, on vanilla's alchemy bench.
bool BuildAlchemyMenu(void* menu) {
    void* allocator = g_allocator ? *g_allocator : nullptr;
    if (!allocator || !g_workbench) {
        Log("alchemy: cannot build the sub-menu -- allocator %p bench %p",
            allocator, g_workbench);
        return false;
    }
    void* memory = VCall<AllocFn>(allocator, ids::kScaleformAllocSlot /
                                                 sizeof(void*))(
        allocator, ids::kAlchemyMenuSize, nullptr);
    if (!memory) return false;
    void* movie = static_cast<char*>(menu) + ids::kOffCraftingMovie;
    void* sub = g_alchemyCtor(memory, movie, g_workbench);
    At<void*>(menu, ids::kOffCraftingSubMenu) = sub;
    g_help(ids::kAlchemyHelp);
    g_delegateAdd(At<void*>(menu, ids::kOffCraftingDelegate), sub);
    VCall<ShowFn>(sub, ids::kSubMenuShowSlot)(sub);
    return true;
}

std::uint32_t CraftingProcessMessage(void* menu, char* message) {
    const std::uint32_t type =
        *reinterpret_cast<std::uint32_t*>(message + ids::kMessageTypeOffset);
    const std::uint32_t result = g_craftingOriginal(menu, message);
    if (type == ids::kMessageClose) g_active = false;
    if (type != ids::kMessageOpen) return result;
    const bool wanted = g_pending;
    g_pending = false;
    if (!wanted || At<void*>(menu, ids::kOffCraftingSubMenu)) return result;
    if (!BuildAlchemyMenu(menu)) return result;
    BeginSession();
    return ids::kResultHandled;
}

void Notify(const std::string& text) {
    void* message = nullptr;
    if (text.empty() || !g_notification ||
        !FixedString(&message, text.c_str())) {
        return;
    }
    g_notification(PapyrusVm(), 0, nullptr, &message);
}

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
        Notify(GmstText(kNoMortarGmst, ""));
        return true;
    }
    return g_userEventOriginal(subMenu, event);
}

// ------------------------------------------------------------ inventory ----

// OpenMW's ActionAlchemy: refused in combat, otherwise the alchemy window.
// The inventory leaves the way its own full exit does, through
// CloseTweenMenu: the TweenMenu under it would otherwise stay open, hidden.
void UseApparatus(void* args) {
    void* player = gamecalls::PlayerRef();
    if (player && g_isInCombat && g_isInCombat(PapyrusVm(), 0, player)) {
        Log("alchemy: apparatus used in combat -- refused");
        Notify(GmstText(kInCombatGmst, ""));
        return;
    }
    g_pending = true;
    g_closeTween(args);
    CloseMenuNamed(ids::kInventoryMenuName);
    OpenMenuNamed(ids::kCraftingMenuName);
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

std::size_t ResolveApparatus(const std::vector<ApparatusDef>& rows) {
    g_apparatus.clear();
    g_byFormId.clear();
    for (const ApparatusDef& row : rows) {
        void* form = FormFromFile(row.form.plugin.c_str(),
                                  row.form.formId & kLocalMask);
        if (!form) continue;
        g_byFormId[FormIdOf(form)] = g_apparatus.size();
        g_apparatus.push_back({form, row.type, row.quality});
    }
    Log("alchemy: %zu of %zu staged apparatus resolved", g_apparatus.size(),
        rows.size());
    return g_apparatus.size();
}

template <typename Fn>
Fn Address(const char* name, std::uint64_t id) {
    return reinterpret_cast<Fn>(Resolve(name, id, nullptr));
}

void ResolveNatives() {
    g_itemSelect = Address<CallbackFn>("InventoryMenu ItemSelect",
                                       ids::kInventoryItemSelect);
    g_closeTween = Address<CallbackFn>("InventoryMenu CloseTweenMenu",
                                       ids::kInventoryCloseTween);
    g_userEvents = Address<void**>("UserEvents singleton",
                                   ids::kUserEventsSingleton);
    g_selectedItem = Address<SelectedItemFn>("ItemList::GetSelectedItem",
                                             ids::kItemListSelected);
    g_isInCombat = Address<CombatFn>("Actor.IsInCombat", ids::kActorIsInCombat);
    g_notification = Address<NotificationFn>("Debug.Notification",
                                             ids::kDebugNotification);
    g_itemCount = Address<ItemCountFn>("ObjectReference.GetItemCount",
                                       ids::kRefGetItemCount);
    g_allocator = Address<void**>("Scaleform allocator",
                                  ids::kScaleformAllocator);
    g_alchemyCtor = Address<AlchemyCtorFn>("AlchemyMenu ctor",
                                           ids::kAlchemyMenuCtor);
    g_help = Address<HelpFn>("crafting help", ids::kCraftingHelpId);
    g_delegateAdd = Address<DelegateAddFn>("FxDelegate add handler",
                                           ids::kDelegateAddHandler);
    g_getMagnitude = Address<GetMagnitudeFn>("Effect::GetMagnitude",
                                             ids::kEffectGetMagnitude);
    g_setMagnitude = Address<SetMagnitudeFn>("Effect::SetMagnitude",
                                             ids::kEffectSetMagnitude);
    g_getDuration = Address<GetDurationFn>("Effect::GetDuration",
                                           ids::kEffectGetDuration);
    g_setDuration = Address<SetDurationFn>("Effect::SetDuration",
                                           ids::kEffectSetDuration);
    g_workbench = FormFromFile(ids::kSkyrimMaster, ids::kAlchemyWorkbench);
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
    if (!g_itemSelect || !g_closeTween || !g_selectedItem || !g_alchemyCtor ||
        !g_help || !g_delegateAdd || !g_workbench || !g_userEvents) {
        return false;
    }
    g_userEventOriginal = reinterpret_cast<UserEventFn>(Swap(
        "AlchemyMenu::ProcessUserEvent", ids::kAlchemyMenuVtable,
        ids::kUserEventSlot, ids::kAlchemyMenuUserEvent,
        reinterpret_cast<void*>(&AlchemyUserEvent)));
    if (!g_userEventOriginal) return false;
    g_craftingOriginal = reinterpret_cast<ProcessMessageFn>(Swap(
        "CraftingMenu::ProcessMessage", ids::kCraftingMenuVtable,
        ids::kProcessMessageSlot, ids::kCraftingMenuProcessMessage,
        reinterpret_cast<void*>(&CraftingProcessMessage)));
    if (!g_craftingOriginal) return false;
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
    if (!ResolveApparatus(LoadApparatusFrom(SidecarDir()))) {
        Log("alchemy: no apparatus staged -- NOT hooked");
        return;
    }
    ResolveNatives();
    const bool menus = InstallMenus();
    const bool effects = InstallEffectiveness();
    Log("alchemy: apparatus menu %s, potion strength %s",
        menus ? "hooked" : "NOT hooked", effects ? "hooked" : "NOT hooked");
}

}  // namespace mwruntime
