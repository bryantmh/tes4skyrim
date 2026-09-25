// The Transformation commands that MOVE something: PositionCell and Position,
// Move and MoveWorld, Rotate and RotateWorld, the scale pair, and the two
// PlaceItem forms. Each follows OpenMW's own opcode of the same name.
//
// 🛑 `Move` and `Rotate` are RATES, not displacements. OpenMW multiplies both
// by the frame duration, so `rotate z -110` means 110 degrees per SECOND and a
// script calls it every frame. Here that factor is the TICK delta, which is
// what makes authored motion play at its authored speed whatever the frame
// rate -- and it is why the tick had to leave 15 Hz to port them.
// See: docs/commentary/morrowind_runtime.md#move-and-rotate-are-rates

#include <cctype>
#include <string>

#include <components/compiler/opcodes.hpp>
#include <components/interpreter/context.hpp>
#include <components/interpreter/opcodes.hpp>

#include "dialogue_state.h"
#include "log.h"
#include "object_tick.h"
#include "script_ops.h"

namespace tesruntime::mw {

namespace {

// `Rotate`, `Move` and their world forms name the axis as a STRING argument,
// exactly as GetPos does. An unknown axis is x, as OpenMW's own conversion
// answers rather than failing.
int AxisOf(const std::string& axis) {
    if (axis.empty()) return 0;
    const char letter = static_cast<char>(::tolower(axis[0]));
    if (letter == 'y') return 1;
    return letter == 'z' ? 2 : 0;
}

// `PositionCell x y z zRot "cell"`: moves the target into another cell.
//
// 🛑 The arguments come off in WRITTEN order -- x, y, z, then the rotation,
// then the cell -- because the compiler pushes them that way and OpenMW pops
// them that way. Only the `->` target is pushed last.
template <class R>
class OpPositionCell : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const float x = PopFloat(runtime);
        const float y = PopFloat(runtime);
        const float z = PopFloat(runtime);
        const float zRot = PopFloat(runtime);
        const std::string cell = PopString(runtime);
        Log("move: %s -> '%s' (%g, %g, %g) rot %g", ref.c_str(), cell.c_str(),
            x, y, z, zRot);
        if (Hooks().moveToCell) {
            Hooks().moveToCell(ref, cell, x, y, z, zRot);
        }
    }
};

// `Position x y z zRot`: the same, without leaving the cell.
template <class R>
class OpPosition : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const float x = PopFloat(runtime);
        const float y = PopFloat(runtime);
        const float z = PopFloat(runtime);
        const float zRot = PopFloat(runtime);
        if (Hooks().moveInCell) Hooks().moveInCell(ref, x, y, z, zRot);
    }
};

// `Move axis rate` and `MoveWorld axis rate`: a per-second rate, applied for
// one tick. `Move` is the object's OWN frame, `MoveWorld` the world's.
template <class R, bool Local>
class OpMove : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const int axis = AxisOf(PopString(runtime));
        const float rate = PopFloat(runtime);
        if (Hooks().moveBy) {
            Hooks().moveBy(ref, axis, rate * TickDelta(), Local);
        }
    }
};

// `Rotate axis rate` / `RotateWorld axis rate`, in DEGREES per second.
//
// 🛑 Both are the same call here. OpenMW's `RotateWorld` composes a quaternion
// on the object's world attitude while `Rotate` adds to its Euler angles, and
// the two differ only for an object already tilted on two axes. Skyrim's
// TranslateTo takes Euler degrees and has no world-composed form, so the
// distinction cannot be kept. Measured over both corpora: 86 rotate call sites
// across 17 scripts, of which 2 turn more than one axis
// (`TR_m1_lud_cogspinner`, `TR_m7_HH_Alvynu_7_ShipSink_sc`) and so are the
// only places the approximation can show.
// See: docs/commentary/morrowind_runtime.md#move-and-rotate-are-rates
template <class R>
class OpRotate : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const int axis = AxisOf(PopString(runtime));
        const float rate = PopFloat(runtime);
        if (Hooks().rotateBy) {
            Hooks().rotateBy(ref, axis, rate * TickDelta());
        }
    }
};

template <class R>
class OpGetScale : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        runtime.push(Hooks().scale ? Hooks().scale(ref) : 1.0f);
    }
};

template <class R>
class OpSetScale : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const float value = PopFloat(runtime);
        if (Hooks().setScale) Hooks().setScale(ref, value);
    }
};

// `ModScale delta` adds to the CURRENT scale, as OpenMW reads it back first.
template <class R>
class OpModScale : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        const float delta = PopFloat(runtime);
        if (!Hooks().setScale) return;
        const float now = Hooks().scale ? Hooks().scale(ref) : 1.0f;
        Hooks().setScale(ref, now + delta);
    }
};

// `PlaceItemCell id "cell" x y z zRot` and `PlaceItem id x y z zRot`: a base
// record placed at an absolute spot. Neither has an explicit form.
class OpPlaceItemCell : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string base = PopString(runtime);
        const std::string cell = PopString(runtime);
        const float x = PopFloat(runtime);
        const float y = PopFloat(runtime);
        const float z = PopFloat(runtime);
        const float zRot = PopFloat(runtime);
        if (Hooks().placeAtCell) {
            Hooks().placeAtCell(base, cell, x, y, z, zRot);
        }
    }
};

// The cell-less form places into the cell the PLAYER is in, which is what
// OpenMW does with the interpreter context's own cell.
class OpPlaceItem : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string base = PopString(runtime);
        const float x = PopFloat(runtime);
        const float y = PopFloat(runtime);
        const float z = PopFloat(runtime);
        const float zRot = PopFloat(runtime);
        if (Hooks().placeAtCell) {
            Hooks().placeAtCell(base, std::string(), x, y, z, zRot);
        }
    }
};

}  // namespace

void InstallMoveOps(OpcodeInstaller& into) {
    namespace T = Compiler::Transformation;
    into.Real<OpPositionCell<Implicit>>(T::opcodePositionCell);
    into.Real<OpPositionCell<Explicit>>(T::opcodePositionCellExplicit);
    into.Real<OpPosition<Implicit>>(T::opcodePosition);
    into.Real<OpPosition<Explicit>>(T::opcodePositionExplicit);
    into.Real<OpMove<Implicit, true>>(T::opcodeMove);
    into.Real<OpMove<Explicit, true>>(T::opcodeMoveExplicit);
    into.Real<OpMove<Implicit, false>>(T::opcodeMoveWorld);
    into.Real<OpMove<Explicit, false>>(T::opcodeMoveWorldExplicit);
    into.Real<OpRotate<Implicit>>(T::opcodeRotate);
    into.Real<OpRotate<Explicit>>(T::opcodeRotateExplicit);
    into.Real<OpRotate<Implicit>>(T::opcodeRotateWorld);
    into.Real<OpRotate<Explicit>>(T::opcodeRotateWorldExplicit);
    into.Real<OpGetScale<Implicit>>(T::opcodeGetScale);
    into.Real<OpGetScale<Explicit>>(T::opcodeGetScaleExplicit);
    into.Real<OpSetScale<Implicit>>(T::opcodeSetScale);
    into.Real<OpSetScale<Explicit>>(T::opcodeSetScaleExplicit);
    into.Real<OpModScale<Implicit>>(T::opcodeModScale);
    into.Real<OpModScale<Explicit>>(T::opcodeModScaleExplicit);
    into.Real<OpPlaceItemCell>(T::opcodePlaceItemCell);
    into.Real<OpPlaceItem>(T::opcodePlaceItem);
}

}  // namespace tesruntime::mw
