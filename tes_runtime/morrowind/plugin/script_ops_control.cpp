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
// Of the chargen menus only EnableRaceMenu has a Skyrim equivalent
// (Game.ShowRaceMenu). Name, class, birthsign and the stat review have none,
// so they stay stubs that say so.

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

// `EnableRaceMenu`: Skyrim's own race/sex menu.
class OpShowRaceMenu : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime&) override {
        if (Hooks().showRaceMenu) Hooks().showRaceMenu();
    }
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
}

}  // namespace tesruntime::mw
