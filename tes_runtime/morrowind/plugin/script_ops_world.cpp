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

namespace tesruntime::mw {

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

// `PlaceAtPC/PlaceAtMe id count distance direction`: creates references of a
// BASE record near an actor -- the PLAYER for `PlaceAtPC`, the named one for
// `PlaceAtMe`, which is OpenMW's same `OpPlaceAt` under both names. The
// distance and direction are popped and dropped -- Skyrim places beside the
// actor, which is where every authored call wants it anyway.
//
// 🛑 The created reference runs the base's script, and the hook binds its
// instance from the FormID PlaceAtMe returns.
// See: docs/plans/morrowind_object_scripts.md#placeatpc
// 🛑 SEGMENT 5, not 3, though its `X` looks optional: the compiler emits
// 0xCA00019C for it, whose tag is 0x32. The segment a command lands in is a
// property of its REGISTRATION, not of its argument string -- read the word.
template <class R, bool AtPlayer>
class OpPlaceAt : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        // 🛑 The `->` target is popped FIRST whatever the form, so the PC
        // form has to consume it before the arguments even though it places
        // at the player regardless.
        const std::string named = R::Target(runtime);
        const std::string base = PopString(runtime);
        const int count = PopInt(runtime);
        PopFloat(runtime);
        PopInt(runtime);
        const std::string near = AtPlayer ? std::string(kPlayerId) : named;
        Log("world: place %s x%d beside %s", base.c_str(), count,
            near.c_str());
        if (Hooks().placeNear) Hooks().placeNear(near, base, count);
    }
};

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

// `ShowRestMenu`: OpenMW's OpShowRestMenu, whose bed is the target.
template <class R>
class OpShowRestMenu : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string bed = R::Target(runtime);
        Log("world: rest menu for bed '%s'", bed.c_str());
        if (Hooks().showRestMenu) Hooks().showRestMenu(bed);
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
//: x, y, z -- and the three angles that follow them in a placement row.
constexpr int kAxisCount = 3;

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

// `GetStartingPos x` / `GetStartingAngle z`: the AUTHORED placement rather
// than where the object stands now, which is what a script compares against to
// see how far its own mechanism has travelled. `Offset` picks the half of the
// row: 0 for the position, kAxisCount for the angles, already in degrees.
// See: docs/commentary/morrowind_runtime.md#setatstart
template <class R, int Offset>
class OpGetStarting : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const int axis = AxisOf(PopString(runtime));
        const float* at = FindPlacement(ref);
        runtime.push(at ? at[Offset + axis] : 0.0f);
    }
};

// `SetAtStart`: back to the placement the cell record authors, position and
// rotation both. The scenery scripts pair it with MoveWorld/rotate to re-arm a
// trap or re-close a door, usually on CellChanged.
// See: docs/commentary/morrowind_runtime.md#setatstart
template <class R>
class OpSetAtStart : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const float* at = FindPlacement(ref);
        if (!at || !Hooks().setPosition || !Hooks().setAngle) return;
        for (int axis = 0; axis < kAxisCount; ++axis) {
            Hooks().setPosition(ref, axis, at[axis]);
            Hooks().setAngle(ref, axis, at[kAxisCount + axis]);
        }
    }
};

// `ResetActors`: every LOADED placed actor back to its authored spot, which is
// how a scene puts its cast back on their marks. Takes no target and no
// argument -- OpenMW sweeps the active cells, and the loaded check is what
// stands in for that here.
//
// Scripted placements only, which the instance table is: measured, all 13
// placed actors of the two cells that call this are scripted.
// See: docs/commentary/morrowind_runtime.md#setatstart
class OpResetActors : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime&) override {
        if (!Hooks().loadedRef || !Hooks().setPosition || !Hooks().setAngle) {
            return;
        }
        for (std::size_t at = 0; const InstanceRow* row = InstanceAt(at); ++at) {
            if (!row->hasPlacement) continue;
            if (!Hooks().loadedRef(row->plugin, row->localFormId)) continue;
            for (int axis = 0; axis < kAxisCount; ++axis) {
                Hooks().setPosition(row->baseId, axis, row->placement[axis]);
                Hooks().setAngle(row->baseId, axis,
                                 row->placement[kAxisCount + axis]);
            }
        }
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

// `GetCurrentTime`: the hour of day, 0..24. OpenMW reads its own timestamp;
// ours is the `gamehour` global, which SyncClock refreshes from Skyrim's own
// GLOB every tick, so this is the same number the clock conditions see.
// See: docs/commentary/morrowind_runtime.md#the-clock
class OpGetCurrentTime : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(State().Global("gamehour"));
    }
};

// A result script only ever runs with the dialogue window up, so this is
// true whenever the hook can be asked at all.
class OpMenuMode : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(Hooks().menuMode && Hooks().menuMode() ? 1 : 0);
    }
};

// `GetButtonPressed`: which button of the last `MessageBox` the player
// clicked, POLLED from a script's body rather than waited on -- the box does
// not block, so this answers -1 every tick until the click lands. TES3 hands
// the answer out ONCE, so reading it clears it.
// See: docs/commentary/morrowind_runtime.md#messagebox-buttons
class OpGetButtonPressed : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(State().buttonPressed);
        State().buttonPressed = -1;
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
    namespace G = Compiler::Gui;
    namespace S = Compiler::Stats;
    into.Real<OpActivate<Implicit>>(M::opcodeActivate);
    into.Real<OpActivate<Explicit>>(M::opcodeActivateExplicit);
    into.Real<OpShowRestMenu<Implicit>>(G::opcodeShowRestMenu);
    into.Real<OpShowRestMenu<Explicit>>(G::opcodeShowRestMenuExplicit);
    into.Real3<OpLock<Implicit>>(M::opcodeLock);
    into.Real3<OpLock<Explicit>>(M::opcodeLockExplicit);
    into.Real<OpUnlock<Implicit>>(M::opcodeUnlock);
    into.Real<OpUnlock<Explicit>>(M::opcodeUnlockExplicit);
    into.Real<OpGetLocked<Implicit>>(M::opcodeGetLocked);
    into.Real<OpGetLocked<Explicit>>(M::opcodeGetLockedExplicit);
    into.Real<OpSetDelete<Implicit>>(M::opcodeSetDelete);
    into.Real<OpSetDelete<Explicit>>(M::opcodeSetDeleteExplicit);
    into.Real<OpMenuMode>(M::opcodeMenuMode);
    into.Real<OpGetCurrentTime>(M::opcodeGetCurrentTime);
    into.Real<OpGetButtonPressed>(G::opcodeGetButtonPressed);
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
    into.Real<OpPlaceAt<Implicit, true>>(T::opcodePlaceAtPc);
    into.Real<OpPlaceAt<Implicit, false>>(T::opcodePlaceAtMe);
    into.Real<OpPlaceAt<Explicit, false>>(T::opcodePlaceAtMeExplicit);
    into.Real<OpGetPos<Implicit>>(T::opcodeGetPos);
    into.Real<OpGetPos<Explicit>>(T::opcodeGetPosExplicit);
    into.Real<OpSetPos<Implicit>>(T::opcodeSetPos);
    into.Real<OpSetPos<Explicit>>(T::opcodeSetPosExplicit);
    into.Real<OpGetAngle<Implicit>>(T::opcodeGetAngle);
    into.Real<OpGetAngle<Explicit>>(T::opcodeGetAngleExplicit);
    into.Real<OpSetAngle<Implicit>>(T::opcodeSetAngle);
    into.Real<OpSetAngle<Explicit>>(T::opcodeSetAngleExplicit);
    into.Real<OpSetAtStart<Implicit>>(T::opcodeSetAtStart);
    into.Real<OpSetAtStart<Explicit>>(T::opcodeSetAtStartExplicit);
    into.Real<OpResetActors>(T::opcodeResetActors);
    into.Real<OpGetStarting<Implicit, 0>>(T::opcodeGetStartingPos);
    into.Real<OpGetStarting<Explicit, 0>>(T::opcodeGetStartingPosExplicit);
    into.Real<OpGetStarting<Implicit, kAxisCount>>(T::opcodeGetStartingAngle);
    into.Real<OpGetStarting<Explicit, kAxisCount>>(
        T::opcodeGetStartingAngleExplicit);
    InstallDynamic<kHealth>(into);
    InstallDynamic<kMagicka>(into);
    InstallDynamic<kFatigue>(into);
}

}  // namespace tesruntime::mw
