// The crafting-bench hook every runtime shares; TESRuntime owns it and
// exports it (common/crafting.h).
// See: docs/commentary/tes_runtime_alchemy.md#crafting-bench

#include "crafting.h"

#include <cstddef>
#include <cstdint>

#include "addresses.h"
#include "engine.h"
#include "ids.h"
#include "log.h"
#include "ui_message.h"

namespace tesruntime {

namespace {

using ProcessMessageFn = std::uint32_t (*)(void* menu, char* message);
using AllocFn = void* (*)(void* allocator, std::size_t size, void* tag);
using SubMenuCtorFn = void* (*)(void* self, void* movie, void* furniture);
using HelpFn = void (*)(std::uint32_t id);
using DelegateAddFn = void (*)(void* delegate, void* handler);
using ShowFn = void (*)(void* subMenu);

// What one case of CraftingMenu's bench switch builds.
struct BenchSpec {
    const char* name;
    std::uint64_t ctorId;
    std::size_t size;
    std::uint32_t help;
    std::uint32_t workbench;
    SubMenuCtorFn ctor = nullptr;
    void* furniture = nullptr;
};

BenchSpec g_benches[] = {
    {"enchanting", ids::kEnchantMenuCtor, ids::kEnchantMenuSize,
     ids::kEnchantHelp, ids::kEnchantingWorkbench},
    {"alchemy", ids::kAlchemyMenuCtor, ids::kAlchemyMenuSize,
     ids::kAlchemyHelp, ids::kAlchemyWorkbench},
};

ProcessMessageFn g_original = nullptr;
void**           g_allocator = nullptr;
HelpFn           g_help = nullptr;
DelegateAddFn    g_delegateAdd = nullptr;

// The bench the next open builds, and the session that asked for it.
BenchSpec*   g_pending = nullptr;
BenchSession g_pendingSession;
// The session whose sub-menu is open, told when the menu closes.
BenchSession g_open;

// What the bench's case of CraftingMenu's switch does, on the vanilla bench.
bool BuildSubMenu(void* menu, const BenchSpec& bench) {
    void* allocator = g_allocator ? *g_allocator : nullptr;
    if (!allocator || !bench.ctor || !bench.furniture) {
        Log("crafting: cannot build the %s sub-menu -- allocator %p ctor %p "
            "bench %p", bench.name, allocator,
            reinterpret_cast<void*>(bench.ctor), bench.furniture);
        return false;
    }
    void* memory = VCall<AllocFn>(allocator, ids::kScaleformAllocSlot /
                                                 sizeof(void*))(
        allocator, bench.size, nullptr);
    if (!memory) return false;
    void* movie = static_cast<char*>(menu) + ids::kOffCraftingMovie;
    void* sub = bench.ctor(memory, movie, bench.furniture);
    At<void*>(menu, ids::kOffCraftingSubMenu) = sub;
    g_help(bench.help);
    g_delegateAdd(At<void*>(menu, ids::kOffCraftingDelegate), sub);
    VCall<ShowFn>(sub, ids::kSubMenuShowSlot)(sub);
    return true;
}

void EndSession() {
    const BenchSession ended = g_open;
    g_open = {};
    if (ended.closed) ended.closed();
}

std::uint32_t CraftingProcessMessage(void* menu, char* message) {
    const std::uint32_t type =
        *reinterpret_cast<std::uint32_t*>(message + ids::kMessageTypeOffset);
    const std::uint32_t result = g_original(menu, message);
    if (type == kMessageClose) EndSession();
    if (type != kMessageOpen) return result;
    BenchSpec* wanted = g_pending;
    const BenchSession session = g_pendingSession;
    g_pending = nullptr;
    g_pendingSession = {};
    if (!wanted || At<void*>(menu, ids::kOffCraftingSubMenu)) return result;
    if (!BuildSubMenu(menu, *wanted)) return result;
    Log("crafting: %s sub-menu built", wanted->name);
    g_open = session;
    if (session.opened) session.opened();
    return ids::kResultHandled;
}

template <typename Fn>
Fn Address(const char* name, std::uint64_t id) {
    return reinterpret_cast<Fn>(Resolve(name, id, nullptr));
}

}  // namespace

bool OpenBench(Bench bench, const BenchSession& session) {
    if (!g_original) return false;
    g_pending = &g_benches[static_cast<int>(bench)];
    g_pendingSession = session;
    OpenMenuNamed(ids::kCraftingMenuName);
    return true;
}

void InstallCrafting() {
    g_allocator = Address<void**>("Scaleform allocator",
                                  ids::kScaleformAllocator);
    g_help = Address<HelpFn>("crafting help", ids::kCraftingHelpId);
    g_delegateAdd = Address<DelegateAddFn>("FxDelegate add handler",
                                           ids::kDelegateAddHandler);
    bool ready = g_allocator && g_help && g_delegateAdd;
    for (BenchSpec& bench : g_benches) {
        bench.ctor = Address<SubMenuCtorFn>(bench.name, bench.ctorId);
        bench.furniture = FormFromFile(bench.workbench, ids::kSkyrimMaster);
        ready = ready && bench.ctor && bench.furniture;
    }
    if (ready) {
        g_original = reinterpret_cast<ProcessMessageFn>(SwapVtableSlot(
            "CraftingMenu::ProcessMessage",
            Resolve("CraftingMenu vtable", ids::kCraftingMenuVtable, nullptr),
            ids::kProcessMessageSlot,
            Resolve("CraftingMenu::ProcessMessage",
                    ids::kCraftingMenuProcessMessage, nullptr),
            reinterpret_cast<void*>(&CraftingProcessMessage)));
    }
    Log("crafting: bench menus %s", g_original ? "hooked" : "NOT hooked");
}

bool CraftingInstalled() { return g_original != nullptr; }

}  // namespace tesruntime

extern "C" {

__declspec(dllexport) bool TESRuntime_OpenBench(int bench, void (*opened)(),
                                                void (*closed)()) {
    if (bench < 0 || bench > static_cast<int>(tesruntime::Bench::kAlchemy)) {
        return false;
    }
    return tesruntime::OpenBench(static_cast<tesruntime::Bench>(bench),
                                 {opened, closed});
}

__declspec(dllexport) bool TESRuntime_BenchReady() {
    return tesruntime::CraftingInstalled();
}

}  // extern "C"
