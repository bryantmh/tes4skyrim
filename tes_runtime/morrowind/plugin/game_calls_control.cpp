// The player-control switches and the race menu, as Skyrim natives.
//
// TES3 flips one switch at a time; Skyrim takes all eight of its flags in one
// call, so the state pushes the WHOLE set whenever any of it changes and this
// file only translates.
//
// A switch is mapped ONLY where the engine flag does the same thing. The
// mapping, from the CK wiki's parameter list:
//   playercontrols   -> abMovement
//   playerfighting   -> abFighting
//   playerlooking    -> abLooking
//   playerviewswitch -> abCamSwitch
//
// 🛑 Three switches reach no flag and are kept in state for their getter
// alone. `playermagic` is TES3's SPELL-drawing switch (OpenMW writes it to
// `mSpellDrawingDisabled`, beside `playerfighting` as `mWeaponDrawingDisabled`)
// while Skyrim's `abFighting` is the one flag for all combat -- folding magic
// into it would make `DisablePlayerMagic` block melee too. `playerjumping`
// and `vanitymode` have no flag at all.
// See: docs/commentary/morrowind_runtime.md#the-control-switches

#include "game_calls_internal.h"

#include "ids.h"
#include "log.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

// Game.ShowRaceMenu(), a GLOBAL native: its self slot is a tag.
using ShowRaceMenuFn = void (*)(void* vm, std::uint32_t stack, void* tag);
// Game.EnablePlayerControls / DisablePlayerControls, both global and both
// taking the same eight bools and the POV-type int.
using SetControlsFn = void (*)(void* vm, std::uint32_t stack, void* tag,
                               bool movement, bool fighting, bool camSwitch,
                               bool looking, bool sneaking, bool menu,
                               bool activate, bool journalTabs,
                               std::int32_t povType);

ShowRaceMenuFn g_showRaceMenu = nullptr;
SetControlsFn  g_enableControls = nullptr;
SetControlsFn  g_disableControls = nullptr;

void ShowRaceMenu() {
    if (!g_showRaceMenu) return;
    Log("control: opening Skyrim's race menu");
    g_showRaceMenu(PapyrusVm(), 0, nullptr);
}

// Pushes the set. Each engine flag is named by the one TES3 switch it
// follows, and a flag no switch covers -- sneaking, activation, the menus,
// the journal tabs -- is passed false to BOTH calls, which leaves it exactly
// as the player had it.
void ApplyControlSwitches(const bool* on, int count) {
    if (!g_enableControls || !g_disableControls) return;
    if (count < kControlSwitchCount) return;
    const bool movement = on[kPlayerControls];
    const bool fighting = on[kPlayerFighting];
    const bool looking = on[kPlayerLooking];
    const bool camSwitch = on[kPlayerViewSwitch];
    // A flag is only ever passed to the call that ACTS on it, so the pair
    // never fights over one the other just wrote: enable takes the flags
    // that are on, disable takes the flags that are off.
    g_enableControls(PapyrusVm(), 0, nullptr, movement, fighting, camSwitch,
                     looking, false, false, false, false, 0);
    g_disableControls(PapyrusVm(), 0, nullptr, !movement, !fighting,
                      !camSwitch, !looking, false, false, false, false, 0);
    Log("control: pushed movement=%d fighting=%d look=%d view=%d",
        movement ? 1 : 0, fighting ? 1 : 0, looking ? 1 : 0,
        camSwitch ? 1 : 0);
}

}  // namespace

void InstallControlCalls(GameHooks& hooks) {
    g_showRaceMenu = Native<ShowRaceMenuFn>("Game.ShowRaceMenu",
                                            ids::kGameShowRaceMenu);
    g_enableControls = Native<SetControlsFn>("Game.EnablePlayerControls",
                                             ids::kGameEnableControls);
    g_disableControls = Native<SetControlsFn>("Game.DisablePlayerControls",
                                              ids::kGameDisableControls);
    hooks.showRaceMenu = ShowRaceMenu;
    hooks.applyControlSwitches = ApplyControlSwitches;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
