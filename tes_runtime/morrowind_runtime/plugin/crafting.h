// Skyrim's crafting menu, opened on one bench's sub-menu with no furniture in
// use. CraftingMenu's own open reads the player's furniture and, finding none,
// builds nothing; its ProcessMessage is swapped so an open asked for here
// builds the sub-menu the way that bench's case of its switch does.
// See: docs/commentary/morrowind_runtime.md#alchemy-apparatus

#pragma once

namespace mwruntime {

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

// Resolves the sub-menu builders and swaps CraftingMenu::ProcessMessage.
// Nothing is swapped when any address is missing.
void InstallCrafting();

// True once the swap went in.
bool CraftingInstalled();

}  // namespace mwruntime
