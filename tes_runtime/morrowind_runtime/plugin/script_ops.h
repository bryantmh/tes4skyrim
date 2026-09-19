// What every real opcode needs: the stack helpers, the two ways a command
// names its target, and the installer that records which opcodes are real so
// the stub pass leaves them alone.
// See: docs/commentary/morrowind_runtime.md#result-scripts

#pragma once

#include <set>
#include <string>
#include <utility>

#include <components/interpreter/interpreter.hpp>
#include <components/interpreter/runtime.hpp>

namespace mwruntime {

inline std::string PopString(Interpreter::Runtime& runtime) {
    std::string text(runtime.getStringLiteral(runtime[0].mInteger));
    runtime.pop();
    return text;
}

inline int PopInt(Interpreter::Runtime& runtime) {
    const int value = runtime[0].mInteger;
    runtime.pop();
    return value;
}

inline float PopFloat(Interpreter::Runtime& runtime) {
    const float value = runtime[0].mFloat;
    runtime.pop();
    return value;
}

// Who a command acts on: the speaker, or the id written before `->`, which
// the compiler pushes LAST and so is popped first.
struct Implicit {
    static std::string Target(Interpreter::Runtime& runtime) {
        return runtime.getContext().getTarget().getRefIdString();
    }
};

struct Explicit {
    static std::string Target(Interpreter::Runtime& runtime) {
        return PopString(runtime);
    }
};

// Installs real opcodes and remembers their codes, so a stub is never laid
// over one.
struct OpcodeInstaller {
    Interpreter::Interpreter interpreter;
    std::set<int> segment5;
    std::set<int> segment3;

    // `args` go to T's constructor, for one class serving a whole family.
    template <class T, class... Args>
    void Real(int code, Args&&... args) {
        interpreter.installSegment5<T>(code, std::forward<Args>(args)...);
        segment5.insert(code);
    }

    // The segment-3 form: a command with optional arguments.
    //
    // 🛑 Which segment a command uses is a property of its REGISTRATION, not
    // of its argument string. `placeatpc` has an optional `X` and is still
    // segment 5. Guessing wrong installs the handler where nothing dispatches,
    // and its stub answers instead -- silently.
    // See: docs/plans/morrowind_object_scripts.md#check-the-segment
    template <class T>
    void Real3(int code) {
        interpreter.installSegment3<T>(code);
        segment3.insert(code);
    }
};

// The commands that reach into the WORLD through GameHooks: activation,
// locks, deletion, distance, the dynamic stats, equipping, the player's
// cell, and the menu. script_ops_world.cpp.
void InstallWorldOps(OpcodeInstaller& into);

// The one-tick event flags an object script reads -- `OnActivate`, `OnDeath`,
// `CellChanged`. script_ops_events.cpp.
void InstallEventOps(OpcodeInstaller& into);

// PlaySound and its 3D/looping/VP variants, StopSound and GetSoundPlaying.
// Each names a TES3 SOUN id the sidecar maps to an SNDR. script_ops_sound.cpp.
void InstallSoundOps(OpcodeInstaller& into);

// The Transformation commands that MOVE something: PositionCell, Position,
// Move/MoveWorld, Rotate/RotateWorld, the scale pair and PlaceItem.
// script_ops_move.cpp.
void InstallMoveOps(OpcodeInstaller& into);

// The AI package commands -- AiTravel, AiWander, AiFollow, AiEscort,
// AiActivate, Face -- and the two queries that read the stack back.
// script_ops_ai.cpp.
void InstallAiOps(OpcodeInstaller& into);

// The one-call world queries: GetLOS, GetDetected, GetTarget, GetWeaponDrawn,
// the sneak/run pair, Resurrect, Drop, GetCurrentWeather, GetSquareRoot and
// Fall. script_ops_query.cpp.
void InstallQueryOps(OpcodeInstaller& into);

// Get/Set/Mod for the attributes, the skills and the magic-effect
// magnitudes, and GetLevel. script_ops_stats.cpp.
void InstallStatOps(OpcodeInstaller& into);

}  // namespace mwruntime
