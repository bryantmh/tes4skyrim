// The one-tick event flags an object script reads: `OnActivate`, `OnDeath`,
// `CellChanged` and their kin.
//
// 🛑 These are NOT world queries. TES3 raises each on ONE object for ONE tick
// and the read clears it, so each reads the flags of the instance whose body
// is running -- not the game's state, and not a shared value. Outside a tick
// there is no instance and every one of them is 0, which is what makes a
// dialogue result script asking `OnActivate` harmless.
//
// The engine-written locals (`OnPCEquip` and friends) are NOT here: they are
// not opcodes at all, and the tick writes them by name before the body runs.
// See: docs/plans/morrowind_object_scripts.md#events

#include <cstring>
#include <string>

#include <components/compiler/opcodes.hpp>
#include <components/interpreter/opcodes.hpp>

#include "object_script.h"
#include "object_tick.h"
#include "script_ops.h"

namespace mwruntime {

namespace {

// Reads one flag off the running instance and CLEARS it, which is the whole
// of TES3's event contract: a flag survives exactly one read.
template <bool ObjectEvents::*Flag>
class OpEvent : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        ObjectScript* instance = RunningInstance();
        const bool raised = instance && instance->Events().*Flag;
        if (instance) instance->Events().*Flag = false;
        runtime.push(raised ? 1 : 0);
    }
};

// The explicit form, `"some_id"->OnActivate`, pops the reference it names and
// answers for THAT placement's instance when one exists.
template <bool ObjectEvents::*Flag>
class OpEventExplicit : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string target = PopString(runtime);
        ObjectScript* instance = RunningInstance();
        const bool mine = instance && _stricmp(instance->BaseId().c_str(),
                                               target.c_str()) == 0;
        const bool raised = mine && instance->Events().*Flag;
        if (mine) instance->Events().*Flag = false;
        runtime.push(raised ? 1 : 0);
    }
};

// 🛑 The TICK delta, never the frame's. Scripts integrate this into timers, so
// answering a 144 fps frame time while ticking at 30 Hz runs every timer ~5x
// slow -- silently, because nothing ever reports it.
// See: docs/plans/morrowind_object_scripts.md#tick-rate
class OpGetSecondsPassed : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(RunningInstance() ? TickDelta() : 0.0f);
    }
};

}  // namespace

void InstallEventOps(OpcodeInstaller& into) {
    into.Real<OpGetSecondsPassed>(Compiler::Misc::opcodeGetSecondsPassed);
    namespace M = Compiler::Misc;
    namespace S = Compiler::Stats;
    namespace C = Compiler::Cell;
    into.Real<OpEvent<&ObjectEvents::activated>>(M::opcodeOnActivate);
    into.Real<OpEventExplicit<&ObjectEvents::activated>>(
        M::opcodeOnActivateExplicit);
    into.Real<OpEvent<&ObjectEvents::died>>(S::opcodeOnDeath);
    into.Real<OpEventExplicit<&ObjectEvents::died>>(S::opcodeOnDeathExplicit);
    into.Real<OpEvent<&ObjectEvents::cellChanged>>(C::opcodeCellChanged);
}

}  // namespace mwruntime
