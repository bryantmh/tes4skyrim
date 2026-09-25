// Skyrim's crafting menu, opened on one bench's sub-menu with no furniture in
// use. CraftingMenu's own open reads the player's furniture and, finding none,
// builds nothing; its ProcessMessage is swapped so an open asked for here
// builds the sub-menu the way that bench's case of its switch does.
//
// ONE runtime owns the swap: a vtable slot swapped once is refused a second
// time. TESRuntime compiles the hook (tes/crafting.cpp) and exports it; every
// other runtime compiles crafting_client.cpp, which calls those exports.
// See: docs/commentary/tes_runtime_alchemy.md#crafting-bench

#pragma once

namespace tesruntime {

// The benches CraftingMenu's switch builds, by the WBDT bench type it reads.
enum class Bench { kEnchanting, kAlchemy };

// What a caller learns about the session it opened: the sub-menu was built,
// and the crafting menu closed. Either may be null.
struct BenchSession {
    void (*opened)() = nullptr;
    void (*closed)() = nullptr;
};

// Posts the crafting menu's open; that open builds `bench`. False when the
// hook is not installed, in which case nothing is posted.
bool OpenBench(Bench bench, const BenchSession& session = {});

// True once the swap went in.
bool CraftingInstalled();

// TESRuntime only: resolves the sub-menu builders and swaps
// CraftingMenu::ProcessMessage. Nothing is swapped when any address is missing.
void InstallCrafting();

// TESRuntime's exports, found by name from the other runtimes.
constexpr const char* kCraftingModule = "TESRuntime.dll";
constexpr const char* kOpenBenchExport = "TESRuntime_OpenBench";
constexpr const char* kBenchReadyExport = "TESRuntime_BenchReady";
using OpenBenchExportFn = bool (*)(int bench, void (*opened)(),
                                   void (*closed)());
using BenchReadyExportFn = bool (*)();

}  // namespace tesruntime
