// The one-call world queries: does this actor see, notice or fight that one,
// is its weapon out, is it sneaking, what is the weather -- plus the three
// commands that act in one call (Resurrect, Drop, Fall).
//
// Each is ONE Skyrim native, which is why they live together: there is no
// mechanism to explain, only a stack effect and a hook. The ones that need a
// mechanism are elsewhere (movement, AI packages, sound).
// See: docs/commentary/morrowind_runtime.md#the-query-commands

#include <cmath>
#include <string>

#include <components/compiler/opcodes.hpp>
#include <components/interpreter/context.hpp>
#include <components/interpreter/opcodes.hpp>

#include "dialogue_state.h"
#include "script_ops.h"

namespace mwruntime {

namespace {

// A query of one actor against ANOTHER, named as a string argument:
// `GetLOS "id"`, `GetDetected "id"`, `GetTarget "id"`.
template <class R, bool (*GameHooks::*Ask)(const std::string&,
                                           const std::string&)>
class OpPairQuery : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string other = PopString(runtime);
        const auto ask = Hooks().*Ask;
        runtime.push(ask && ask(actor, other) ? 1 : 0);
    }
};

// A query of one actor alone: `GetWeaponDrawn`, `GetPCSneaking`.
template <class R, bool (*GameHooks::*Ask)(const std::string&)>
class OpActorQuery : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const auto ask = Hooks().*Ask;
        runtime.push(ask && ask(actor) ? 1 : 0);
    }
};

// The same, but always about the PLAYER: `GetPCSneaking` names nobody.
template <bool (*GameHooks::*Ask)(const std::string&)>
class OpPlayerQuery : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const auto ask = Hooks().*Ask;
        runtime.push(ask && ask("player") ? 1 : 0);
    }
};

// A query about the player that names nobody and takes no actor at all:
// `GetPCSleep`.
template <bool (*GameHooks::*Ask)()>
class OpWorldQuery : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const auto ask = Hooks().*Ask;
        runtime.push(ask && ask() ? 1 : 0);
    }
};

// `HasItemEquipped "id"`: the item is named, the actor is the target.
template <class R>
class OpHasItemEquipped : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string item = PopString(runtime);
        const auto ask = Hooks().itemEquipped;
        runtime.push(ask && ask(actor, item) ? 1 : 0);
    }
};

// The forced-SNEAK latch. TES3 keeps it on the actor until cleared and reads
// the stance as `Flag_Sneak || Flag_ForceSneak`, so the DLL owns the flag and
// applies it through the hook.
//
// 🛑 Run, Jump and MoveJump are NOT here. Skyrim gives Papyrus no way to
// force a gait, so porting them would latch a value the world never reads --
// a command that looks ported and does nothing, which is worse than a stub
// because the audit stops counting it.
// See: docs/commentary/morrowind_runtime.md#forced-movement-is-a-latch
template <class R, bool On>
class OpSetMovementFlag : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        State().SetMovementFlag(R::Target(runtime), kForceSneak, On);
    }
};

template <class R>
class OpGetMovementFlag : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(
            State().MovementFlag(R::Target(runtime), kForceSneak) ? 1 : 0);
    }
};

template <class R>
class OpResurrect : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        if (Hooks().resurrect) Hooks().resurrect(actor);
    }
};

// `Drop item count`: OpenMW refuses a negative count and does nothing for 0.
template <class R>
class OpDrop : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string item = PopString(runtime);
        const int count = PopInt(runtime);
        if (count > 0 && Hooks().dropItem) {
            Hooks().dropItem(actor, item, count);
        }
    }
};

// TES3 weather ids, in OpenMW's own registration order (`weather.cpp`), to
// Skyrim's weather CLASSIFICATION, which is all its native reports:
// -1 none, 0 pleasant, 1 cloudy, 2 rainy, 3 snow.
//
// 🛑 The two schemes do NOT line up, so this is a mapping and not a cast.
// Skyrim has no ash or blight, and a script testing for one is asking about
// Morrowind's own hazard weather; those answer Cloudy, the nearest thing the
// classification can say.
constexpr int kTes3Clear = 0;
constexpr int kTes3Cloudy = 1;
constexpr int kTes3Foggy = 2;
constexpr int kTes3Rain = 4;
constexpr int kTes3Snow = 8;

int Tes3Weather(int classification) {
    switch (classification) {
        case 0: return kTes3Clear;
        case 1: return kTes3Cloudy;
        case 2: return kTes3Rain;
        case 3: return kTes3Snow;
        default: return kTes3Foggy;
    }
}

class OpGetCurrentWeather : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(Hooks().weather ? Tes3Weather(Hooks().weather())
                                     : kTes3Clear);
    }
};

// `GetSquareRoot f`: pure arithmetic, no game at all. OpenMW throws on a
// negative; answering 0 keeps the script running, which is what every other
// unresolvable value here does.
class OpGetSquareRoot : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const float value = PopFloat(runtime);
        runtime.push(value > 0.0f ? std::sqrt(value) : 0.0f);
    }
};

// `Fall`: a no-op in OpenMW TOO -- its opcode body is empty. Ported so the
// 44 call sites stop being counted as unported rather than to do anything.
template <class R>
class OpFall : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        R::Target(runtime);
    }
};

template <class R> using OpForceSneak = OpSetMovementFlag<R, true>;
template <class R> using OpClearSneak = OpSetMovementFlag<R, false>;

}  // namespace

void InstallQueryOps(OpcodeInstaller& into) {
    namespace A = Compiler::Ai;
    namespace M = Compiler::Misc;
    namespace C = Compiler::Control;
    namespace N = Compiler::Container;
    namespace S = Compiler::Stats;
    namespace K = Compiler::Sky;
    into.Real<OpPairQuery<Implicit, &GameHooks::hasLos>>(
        A::opcodeGetLineOfSight);
    into.Real<OpPairQuery<Explicit, &GameHooks::hasLos>>(
        A::opcodeGetLineOfSightExplicit);
    into.Real<OpPairQuery<Implicit, &GameHooks::detects>>(A::opcodeGetDetected);
    into.Real<OpPairQuery<Explicit, &GameHooks::detects>>(
        A::opcodeGetDetectedExplicit);
    into.Real<OpPairQuery<Implicit, &GameHooks::fighting>>(A::opcodeGetTarget);
    into.Real<OpPairQuery<Explicit, &GameHooks::fighting>>(
        A::opcodeGetTargetExplicit);
    into.Real<OpActorQuery<Implicit, &GameHooks::weaponDrawn>>(
        M::opcodeGetWeaponDrawn);
    into.Real<OpActorQuery<Explicit, &GameHooks::weaponDrawn>>(
        M::opcodeGetWeaponDrawnExplicit);
    into.Real<OpPlayerQuery<&GameHooks::sneaking>>(C::opcodeGetPcSneaking);
    into.Real<OpPlayerQuery<&GameHooks::running>>(C::opcodeGetPcRunning);
    into.Real<OpActorQuery<Implicit, &GameHooks::spellReadied>>(
        M::opcodeGetSpellReadied);
    into.Real<OpActorQuery<Explicit, &GameHooks::spellReadied>>(
        M::opcodeGetSpellReadiedExplicit);
    into.Real<OpWorldQuery<&GameHooks::playerSleeping>>(M::opcodeGetPcSleep);
    InstallPair<OpHasItemEquipped>(into, N::opcodeHasItemEquipped,
                                   N::opcodeHasItemEquippedExplicit);
    InstallPair<OpForceSneak>(into, C::opcodeForceSneak,
                              C::opcodeForceSneakExplicit);
    InstallPair<OpClearSneak>(into, C::opcodeClearForceSneak,
                              C::opcodeClearForceSneakExplicit);
    InstallPair<OpGetMovementFlag>(into, C::opcodeGetForceSneak,
                                   C::opcodeGetForceSneakExplicit);
    into.Real<OpResurrect<Implicit>>(S::opcodeResurrect);
    into.Real<OpResurrect<Explicit>>(S::opcodeResurrectExplicit);
    into.Real<OpDrop<Implicit>>(M::opcodeDrop);
    into.Real<OpDrop<Explicit>>(M::opcodeDropExplicit);
    into.Real<OpGetCurrentWeather>(K::opcodeGetCurrentWeather);
    into.Real<OpGetSquareRoot>(M::opcodeGetSquareRoot);
    into.Real<OpFall<Implicit>>(M::opcodeFall);
    into.Real<OpFall<Explicit>>(M::opcodeFallExplicit);
}

}  // namespace mwruntime
