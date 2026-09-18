// The result-script commands that act on the WORLD rather than on dialogue
// state. Each one reaches the engine through a GameHook, so the headless
// tests run them with the hooks null and only the stack effect is checked.
// The semantics follow OpenMW's mwscript extensions of the same name.
// See: docs/commentary/morrowind_runtime.md#result-scripts

#include <cctype>
#include <cstring>
#include <string>

#include <components/compiler/opcodes.hpp>
#include <components/interpreter/context.hpp>
#include <components/interpreter/opcodes.hpp>

#include "dialogue_state.h"
#include "log.h"
#include "script_ops.h"
#include "script_tables.h"

namespace mwruntime {

namespace {

// The id MWScript uses for the player.
constexpr const char* kPlayerId = "player";

// `Lock` with no level: OpenMW's default for a lock that never had one.
constexpr int kDefaultLockLevel = 100;

// The dynamic stats, in OpenMW's order: the opcode constants are base + index.
constexpr int kHealth = 0;
constexpr int kMagicka = 1;
constexpr int kFatigue = 2;

bool IEquals(const std::string& a, const std::string& b) {
    return a.size() == b.size() && _strnicmp(a.c_str(), b.c_str(), a.size()) == 0;
}

bool IStartsWith(const std::string& text, const std::string& prefix) {
    return text.size() >= prefix.size() &&
           _strnicmp(text.c_str(), prefix.c_str(), prefix.size()) == 0;
}

// `Activate`: the player activates the target, as OpenMW's executeActivation
// with the player as the actor.
template <class R>
class OpActivate : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        Log("world: %s activated", ref.c_str());
        if (Hooks().activate) Hooks().activate(ref);
    }
};

// `Lock [level]`: the level comes off AFTER the target, as OpenMW pops it.
template <class R>
class OpLock : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string ref = R::Target(runtime);
        const int level = optional > 0 ? PopInt(runtime) : kDefaultLockLevel;
        Log("world: %s locked at %d", ref.c_str(), level);
        if (Hooks().setLocked) Hooks().setLocked(ref, level);
    }
};

template <class R>
class OpUnlock : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        Log("world: %s unlocked", ref.c_str());
        if (Hooks().setLocked) Hooks().setLocked(ref, -1);
    }
};

template <class R>
class OpGetLocked : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        runtime.push(Hooks().isLocked && Hooks().isLocked(ref) ? 1 : 0);
    }
};

// `SetDelete 1` deletes the reference; `SetDelete 0` would undelete it,
// which Skyrim has no call for.
template <class R>
class OpSetDelete : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const int flag = PopInt(runtime);
        if (flag != 1) {
            Log("world: SetDelete %d on %s -- only deletion is ported", flag,
                ref.c_str());
            return;
        }
        Log("world: %s deleted", ref.c_str());
        if (Hooks().deleteRef) Hooks().deleteRef(ref);
    }
};

template <class R>
class OpGetDistance : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string from = R::Target(runtime);
        const std::string to = PopString(runtime);
        runtime.push(Hooks().distance ? Hooks().distance(from, to) : 0.0f);
    }
};

// GetHealth / GetMagicka / GetFatigue: the CURRENT value.
template <class R, int Which>
class OpGetDynamic : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        runtime.push(Hooks().dynamicStat ? Hooks().dynamicStat(actor, Which)
                                         : 0.0f);
    }
};

template <class R, int Which>
class OpSetDynamic : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const float value = PopFloat(runtime);
        if (Hooks().setDynamicStat) Hooks().setDynamicStat(actor, Which, value);
    }
};

// ModHealth and ModCurrentHealth both move the current value here: Skyrim
// restores or damages a value, it has no separate base to mod.
template <class R, int Which>
class OpModDynamic : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const float delta = PopFloat(runtime);
        if (Hooks().modDynamicStat) Hooks().modDynamicStat(actor, Which, delta);
    }
};

// `GetPos x` / `SetPos z 128` -- the axis is a STRING argument, not part of
// the command name, so one opcode serves all three. An unknown axis answers
// x, which is what OpenMW's own conversion does rather than failing.
int AxisOf(const std::string& axis) {
    if (axis.empty()) return 0;
    const char letter = static_cast<char>(::tolower(axis[0]));
    if (letter == 'y') return 1;
    return letter == 'z' ? 2 : 0;
}

template <class R>
class OpGetPos : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const int axis = AxisOf(PopString(runtime));
        runtime.push(Hooks().position ? Hooks().position(ref, axis) : 0.0f);
    }
};

template <class R>
class OpSetPos : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const int axis = AxisOf(PopString(runtime));
        const float value = PopFloat(runtime);
        if (Hooks().setPosition) Hooks().setPosition(ref, axis, value);
    }
};

template <class R>
class OpGetAngle : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const int axis = AxisOf(PopString(runtime));
        runtime.push(Hooks().angle ? Hooks().angle(ref, axis) : 0.0f);
    }
};

template <class R>
class OpSetAngle : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const int axis = AxisOf(PopString(runtime));
        const float value = PopFloat(runtime);
        if (Hooks().setAngle) Hooks().setAngle(ref, axis, value);
    }
};

// `Equip item`: the engine adds one when the actor has none, as OpenMW does.
template <class R>
class OpEquip : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string item = PopString(runtime);
        Log("item: %s equips %s", actor.c_str(), item.c_str());
        if (Hooks().equipItem) Hooks().equipItem(actor, item);
    }
};

// `GetPCCell "name"`: a PREFIX match on the player's cell name, so
// `GetPCCell "Balmora"` is true in every "Balmora, ..." interior.
class OpGetPCCell : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string prefix = PopString(runtime);
        const std::string cell = Hooks().playerCell ? Hooks().playerCell()
                                                    : std::string();
        runtime.push(IStartsWith(cell, prefix) ? 1 : 0);
    }
};

class OpGetInterior : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(Hooks().playerInInterior && Hooks().playerInInterior()
                         ? 1 : 0);
    }
};

// A result script only ever runs with the dialogue window up, so this is
// true whenever the hook can be asked at all.
class OpMenuMode : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(Hooks().menuMode && Hooks().menuMode() ? 1 : 0);
    }
};

// `GetRace "name"`: the target's authored race, the player's from the
// context; a creature or unknown actor answers 0.
template <class R>
class OpGetRace : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string wanted = PopString(runtime);
        std::string race;
        if (IEquals(actor, kPlayerId)) {
            race = runtime.getContext().getPCRace();
        } else if (const ActorDef* def = FindActor(actor)) {
            race = def->race;
        }
        runtime.push(!race.empty() && IEquals(race, wanted) ? 1 : 0);
    }
};

// `ForceGreeting`: the target starts a conversation with the player.
template <class R>
class OpForceGreeting : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        Log("conversation: %s force-greets", actor.c_str());
        if (Hooks().forceGreeting) Hooks().forceGreeting(actor);
    }
};

template <int Which>
void InstallDynamic(OpcodeInstaller& into) {
    namespace S = Compiler::Stats;
    into.Real<OpGetDynamic<Implicit, Which>>(S::opcodeGetDynamic + Which);
    into.Real<OpGetDynamic<Explicit, Which>>(S::opcodeGetDynamicExplicit + Which);
    into.Real<OpSetDynamic<Implicit, Which>>(S::opcodeSetDynamic + Which);
    into.Real<OpSetDynamic<Explicit, Which>>(S::opcodeSetDynamicExplicit + Which);
    into.Real<OpModDynamic<Implicit, Which>>(S::opcodeModDynamic + Which);
    into.Real<OpModDynamic<Explicit, Which>>(S::opcodeModDynamicExplicit + Which);
    into.Real<OpModDynamic<Implicit, Which>>(S::opcodeModCurrentDynamic + Which);
    into.Real<OpModDynamic<Explicit, Which>>(
        S::opcodeModCurrentDynamicExplicit + Which);
}

}  // namespace

void InstallWorldOps(OpcodeInstaller& into) {
    namespace M = Compiler::Misc;
    namespace C = Compiler::Cell;
    namespace S = Compiler::Stats;
    into.Real<OpActivate<Implicit>>(M::opcodeActivate);
    into.Real<OpActivate<Explicit>>(M::opcodeActivateExplicit);
    into.Real3<OpLock<Implicit>>(M::opcodeLock);
    into.Real3<OpLock<Explicit>>(M::opcodeLockExplicit);
    into.Real<OpUnlock<Implicit>>(M::opcodeUnlock);
    into.Real<OpUnlock<Explicit>>(M::opcodeUnlockExplicit);
    into.Real<OpGetLocked<Implicit>>(M::opcodeGetLocked);
    into.Real<OpGetLocked<Explicit>>(M::opcodeGetLockedExplicit);
    into.Real<OpSetDelete<Implicit>>(M::opcodeSetDelete);
    into.Real<OpSetDelete<Explicit>>(M::opcodeSetDeleteExplicit);
    into.Real<OpMenuMode>(M::opcodeMenuMode);
    into.Real<OpGetPCCell>(C::opcodeGetPCCell);
    into.Real<OpGetInterior>(C::opcodeGetInterior);
    into.Real<OpGetRace<Implicit>>(S::opcodeGetRace);
    into.Real<OpGetRace<Explicit>>(S::opcodeGetRaceExplicit);
    into.Real<OpGetDistance<Implicit>>(Compiler::Transformation::opcodeGetDistance);
    into.Real<OpGetDistance<Explicit>>(
        Compiler::Transformation::opcodeGetDistanceExplicit);
    into.Real<OpEquip<Implicit>>(Compiler::Container::opcodeEquip);
    into.Real<OpEquip<Explicit>>(Compiler::Container::opcodeEquipExplicit);
    into.Real<OpForceGreeting<Implicit>>(Compiler::Dialogue::opcodeForceGreeting);
    into.Real<OpForceGreeting<Explicit>>(
        Compiler::Dialogue::opcodeForceGreetingExplicit);
    namespace T = Compiler::Transformation;
    into.Real<OpGetPos<Implicit>>(T::opcodeGetPos);
    into.Real<OpGetPos<Explicit>>(T::opcodeGetPosExplicit);
    into.Real<OpSetPos<Implicit>>(T::opcodeSetPos);
    into.Real<OpSetPos<Explicit>>(T::opcodeSetPosExplicit);
    into.Real<OpGetAngle<Implicit>>(T::opcodeGetAngle);
    into.Real<OpGetAngle<Explicit>>(T::opcodeGetAngleExplicit);
    into.Real<OpSetAngle<Implicit>>(T::opcodeSetAngle);
    into.Real<OpSetAngle<Explicit>>(T::opcodeSetAngleExplicit);
    InstallDynamic<kHealth>(into);
    InstallDynamic<kMagicka>(into);
    InstallDynamic<kFatigue>(into);
}

}  // namespace mwruntime
