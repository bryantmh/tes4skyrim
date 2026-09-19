// The AI commands: AiTravel, AiWander, AiFollow, AiEscort, AiActivate, Face,
// and the two queries that read back what is running.
//
// Each one fills a QUEST ALIAS, and the PACK record the import hung off that
// alias is what the engine then runs. Skyrim has no call that hands an actor
// a package, so this is the CK's own mechanism rather than an approximation:
// the actor really walks, really follows, really sandboxes.
// See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages

#include <string>

#include <components/compiler/opcodes.hpp>
#include <components/interpreter/context.hpp>
#include <components/interpreter/opcodes.hpp>

#include "dialogue_state.h"
#include "log.h"
#include "script_ops.h"

namespace mwruntime {

namespace {

// Every Ai* command takes a trailing `reset` flag the compiler makes optional,
// and OpenMW discards its VALUE. Each extra word is popped so the stack stays
// balanced whatever the author wrote.
void DropOptional(Interpreter::Runtime& runtime, unsigned int count) {
    for (unsigned int i = 0; i < count; ++i) runtime.pop();
}

// `AiTravel x y z [reset]`: walk to a world point.
//
// 🛑 A package destination cannot be raw coordinates -- the PLDT enum has no
// such type -- so the hook spawns a marker at the point and fills the
// destination alias with it.
template <class R>
class OpAiTravel : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string actor = R::Target(runtime);
        const float x = PopFloat(runtime);
        const float y = PopFloat(runtime);
        const float z = PopFloat(runtime);
        DropOptional(runtime, optional);
        if (Hooks().aiTravel) Hooks().aiTravel(actor, x, y, z);
    }
};

// `AiWander range duration time [idle2..idle9] [reset]`: sandbox around here.
//
// 🛑 The first idle chance is UNUSED and the list starts at Idle2, which is
// OpenMW's own comment. The chances are dropped: Skyrim's sandbox package
// picks its own idles from the furniture around the marker, and a TES3 idle
// index names no Skyrim idle.
template <class R>
class OpAiWander : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string actor = R::Target(runtime);
        const float range = PopFloat(runtime);
        const float duration = PopFloat(runtime);
        PopFloat(runtime);
        DropOptional(runtime, optional);
        if (Hooks().aiWander) Hooks().aiWander(actor, range, duration);
    }
};

// `AiFollow id duration x y z [reset]` and `AiEscort id ...`: the id comes
// FIRST, then the duration, then the destination.
template <class R, bool Escort>
class OpAiFollow : public Interpreter::Opcode1 {
public:
    static void Apply(const std::string& actor, const std::string& target,
                      float duration, float x, float y, float z) {
        if (Escort) {
            if (Hooks().aiEscort) {
                Hooks().aiEscort(actor, target, duration, x, y, z);
            }
        } else if (Hooks().aiFollow) {
            Hooks().aiFollow(actor, target, duration, x, y, z);
        }
    }

private:
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string actor = R::Target(runtime);
        const std::string target = PopString(runtime);
        const float duration = PopFloat(runtime);
        const float x = PopFloat(runtime);
        const float y = PopFloat(runtime);
        const float z = PopFloat(runtime);
        DropOptional(runtime, optional);
        Apply(actor, target, duration, x, y, z);
    }
};

// The `...Cell` forms name the cell between the id and the duration. The cell
// only bounds where the package gives up, which a Skyrim follow package does
// by distance instead, so it is popped and dropped.
template <class R, bool Escort>
class OpAiFollowCell : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string actor = R::Target(runtime);
        const std::string target = PopString(runtime);
        PopString(runtime);
        const float duration = PopFloat(runtime);
        const float x = PopFloat(runtime);
        const float y = PopFloat(runtime);
        const float z = PopFloat(runtime);
        DropOptional(runtime, optional);
        OpAiFollow<R, Escort>::Apply(actor, target, duration, x, y, z);
    }
};

// `AiActivate id [reset]`: walk to something and activate it. The Activate
// package does both, so this is exact rather than an approximation.
template <class R>
class OpAiActivate : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string actor = R::Target(runtime);
        const std::string object = PopString(runtime);
        DropOptional(runtime, optional);
        if (Hooks().aiActivate) Hooks().aiActivate(actor, object);
    }
};

// `Face x y`: turn toward a world point. Not a package in TES3 either -- it
// runs to completion and pops -- so it is applied directly.
template <class R>
class OpFace : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const float x = PopFloat(runtime);
        const float y = PopFloat(runtime);
        if (Hooks().aiFace) Hooks().aiFace(actor, x, y);
    }
};

// `GetCurrentAiPackage`: OpenMW's AiPackageTypeId of what is running, or -1.
// The engine is asked which PACK the actor runs and it is mapped back.
template <class R>
class OpGetCurrentAiPackage : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        runtime.push(Hooks().currentPackage ? Hooks().currentPackage(actor)
                                            : -1);
    }
};

// `GetAiPackageDone`: whether the actor has stopped running the package a
// script gave it, which is what TES3 scripts poll to know an escort arrived.
template <class R>
class OpGetAiPackageDone : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        runtime.push(Hooks().packageDone && Hooks().packageDone(actor) ? 1 : 0);
    }
};

}  // namespace

void InstallAiOps(OpcodeInstaller& into) {
    namespace A = Compiler::Ai;
    into.Real3<OpAiTravel<Implicit>>(A::opcodeAiTravel);
    into.Real3<OpAiTravel<Explicit>>(A::opcodeAiTravelExplicit);
    into.Real3<OpAiWander<Implicit>>(A::opcodeAiWander);
    into.Real3<OpAiWander<Explicit>>(A::opcodeAiWanderExplicit);
    into.Real3<OpAiFollow<Implicit, false>>(A::opcodeAiFollow);
    into.Real3<OpAiFollow<Explicit, false>>(A::opcodeAiFollowExplicit);
    into.Real3<OpAiFollowCell<Implicit, false>>(A::opcodeAiFollowCell);
    into.Real3<OpAiFollowCell<Explicit, false>>(A::opcodeAiFollowCellExplicit);
    into.Real3<OpAiFollow<Implicit, true>>(A::opcodeAiEscort);
    into.Real3<OpAiFollow<Explicit, true>>(A::opcodeAiEscortExplicit);
    into.Real3<OpAiFollowCell<Implicit, true>>(A::opcodeAiEscortCell);
    into.Real3<OpAiFollowCell<Explicit, true>>(A::opcodeAiEscortCellExplicit);
    into.Real3<OpAiActivate<Implicit>>(A::opcodeAIActivate);
    into.Real3<OpAiActivate<Explicit>>(A::opcodeAIActivateExplicit);
    into.Real<OpFace<Implicit>>(A::opcodeFace);
    into.Real<OpFace<Explicit>>(A::opcodeFaceExplicit);
    into.Real<OpGetCurrentAiPackage<Implicit>>(A::opcodeGetCurrentAiPackage);
    into.Real<OpGetCurrentAiPackage<Explicit>>(
        A::opcodeGetCurrentAiPackageExplicit);
    into.Real<OpGetAiPackageDone<Implicit>>(A::opcodeGetAiPackageDone);
    into.Real<OpGetAiPackageDone<Explicit>>(A::opcodeGetAiPackageDoneExplicit);
}

}  // namespace mwruntime
