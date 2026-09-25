// OpenBench for a runtime that does not own the crafting hook: it calls
// TESRuntime's exports. Without TESRuntime loaded, no bench opens.
// See: docs/commentary/tes_runtime_alchemy.md#crafting-bench

#include <windows.h>

#include "crafting.h"

namespace tesruntime {

namespace {

template <typename Fn>
Fn Export(const char* name) {
    HMODULE module = GetModuleHandleA(kCraftingModule);
    return module ? reinterpret_cast<Fn>(GetProcAddress(module, name))
                  : nullptr;
}

}  // namespace

bool OpenBench(Bench bench, const BenchSession& session) {
    const auto open = Export<OpenBenchExportFn>(kOpenBenchExport);
    return open && open(static_cast<int>(bench), session.opened,
                        session.closed);
}

bool CraftingInstalled() {
    const auto ready = Export<BenchReadyExportFn>(kBenchReadyExport);
    return ready && ready();
}

}  // namespace tesruntime
