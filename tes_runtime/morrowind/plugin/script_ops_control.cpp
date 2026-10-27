// The player-control switches and the one chargen menu Skyrim can open.
//
// TES3 keeps seven booleans -- playercontrols, playerfighting, playerjumping,
// playerlooking, playermagic, playerviewswitch, vanitymode -- and a script
// turns each on or off by name. The compiler registers all twenty-one
// commands in a LOOP over its own `controls[]` table, `opcodeEnable + i` and
// its two siblings, so the index IS the opcode offset. Only the four switches
// Skyrim has a matching flag for are installed; `playermagic`, `playerjumping`
// and `vanitymode` reach no engine flag, so they are left unported.
//
// 🛑 `playercontrols` also gates OUR activation hook, not just Skyrim's.
// The hook answers before the engine ever sees the activation, so the engine
// flag alone would leave a Morrowind speaker opening the dialogue menu while
// the tutorial has the player's controls taken away. OpenMW gates the same
// way, in ActionManager::activate().
// See: docs/commentary/morrowind_runtime.md#the-control-switches
//
// Of the chargen menus EnableRaceMenu opens Skyrim's own (Game.ShowRaceMenu)
// and EnableClassMenu and EnableBirthMenu the runtime's (chargen_menu.h).
// Name and the stat review have none, so they stay stubs that say so.
//
// `EnableTeleporting` / `DisableTeleporting` flip a switch of the runtime's
// own, which the teleport effects read.

#include <components/compiler/opcodes.hpp>
#include <components/interpreter/context.hpp>
#include <components/interpreter/opcodes.hpp>

#include "dialogue_state.h"
#include "script_ops.h"

namespace tesruntime::mw {

namespace {

// `EnablePlayerControls` / `DisablePlayerControls` and their siblings. The
// switch is a constructor argument, not a template parameter, so one class
// serves them all and the loop below can carry the index.
class OpSetControl : public Interpreter::Opcode0 {
public:
    OpSetControl(int which, bool on) : mWhich(which), mOn(on) {}

    void execute(Interpreter::Runtime&) override {
        State().SetControlEnabled(mWhich, mOn);
    }

private:
    int  mWhich;
    bool mOn;
};

// `GetPlayerControlsDisabled` and its siblings: TES3 asks the NEGATIVE.
class OpGetControlDisabled : public Interpreter::Opcode0 {
public:
    explicit OpGetControlDisabled(int which) : mWhich(which) {}

    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(State().ControlEnabled(mWhich) ? 0 : 1);
    }

private:
    int mWhich;
};

// `EnableTeleporting` / `DisableTeleporting`: whether Mark, Recall and the
// Interventions work. No Skyrim flag gates them, so the runtime's own does.
// See: docs/commentary/morrowind_runtime.md#teleport-effects
class OpSetTeleporting : public Interpreter::Opcode0 {
public:
    explicit OpSetTeleporting(bool on) : mOn(on) {}

    void execute(Interpreter::Runtime&) override {
        State().teleporting = mOn;
    }

private:
    bool mOn;
};

// `EnableLevitation` / `DisableLevitation`: whether Levitate lifts anyone.
// No Skyrim flag gates it, so the runtime's own does.
// See: docs/commentary/morrowind_runtime.md#levitate-and-slowfall
class OpSetLevitation : public Interpreter::Opcode0 {
public:
    explicit OpSetLevitation(bool on) : mOn(on) {}

    void execute(Interpreter::Runtime&) override {
        State().levitation = mOn;
    }

private:
    bool mOn;
};

// `EnableRaceMenu`: Skyrim's own race/sex menu.
class OpShowRaceMenu : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime&) override {
        if (Hooks().showRaceMenu) Hooks().showRaceMenu();
    }
};

// `EnableClassMenu` (1) / `EnableBirthMenu` (2): the runtime's own menus.
// See: docs/commentary/morrowind_runtime.md#chargen-menus
class OpShowChargenMenu : public Interpreter::Opcode0 {
public:
    explicit OpShowChargenMenu(int kind) : mKind(kind) {}

    void execute(Interpreter::Runtime&) override {
        if (Hooks().showChargenMenu) Hooks().showChargenMenu(mKind);
    }

private:
    int mKind;
};

// `EnableLevelUpMenu`: OpenMW's level-up dialog levels the player whatever
// their progress. Skyrim decides when the player levels here, so the hook
// fills its level bar and the level-up -- then the attribute step -- follows.
// See: docs/commentary/morrowind_runtime.md#leveling
class OpEnableLevelUpMenu : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime&) override {
        if (Hooks().fillLevelProgress) Hooks().fillLevelProgress();
    }
};

// `Xbox`: whether the game runs on the console; 0, as OpenMW's OpXBox answers.
class OpXBox : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override { runtime.push(0); }
};

}  // namespace

void InstallControlOps(OpcodeInstaller& into) {
    namespace C = Compiler::Control;
    static_assert(kControlSwitchCount == C::numberOfControls,
                  "ControlSwitch must mirror the compiler's controls[] table");
    for (int i : {kPlayerControls, kPlayerFighting, kPlayerLooking,
                  kPlayerViewSwitch}) {
        into.Real<OpSetControl>(C::opcodeEnable + i, i, true);
        into.Real<OpSetControl>(C::opcodeDisable + i, i, false);
        into.Real<OpGetControlDisabled>(C::opcodeGetDisabled + i, i);
    }
    into.Real<OpShowRaceMenu>(Compiler::Gui::opcodeEnableRaceMenu);
    into.Real<OpShowChargenMenu>(Compiler::Gui::opcodeEnableClassMenu, 1);
    into.Real<OpShowChargenMenu>(Compiler::Gui::opcodeEnableBirthMenu, 2);
    into.Real<OpEnableLevelUpMenu>(Compiler::Gui::opcodeEnableLevelupMenu);
    into.Real<OpSetTeleporting>(Compiler::Misc::opcodeEnableTeleporting, true);
    into.Real<OpSetTeleporting>(Compiler::Misc::opcodeDisableTeleporting,
                                false);
    into.Real<OpSetLevitation>(Compiler::Misc::opcodeEnableLevitation, true);
    into.Real<OpSetLevitation>(Compiler::Misc::opcodeDisableLevitation, false);
    into.Real<OpXBox>(Compiler::Misc::opcodeXBox);
}

}  // namespace tesruntime::mw
