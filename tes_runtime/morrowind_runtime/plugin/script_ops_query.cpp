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

}  // namespace

void InstallQueryOps(OpcodeInstaller& into) {
    namespace A = Compiler::Ai;
    namespace M = Compiler::Misc;
    namespace C = Compiler::Control;
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
