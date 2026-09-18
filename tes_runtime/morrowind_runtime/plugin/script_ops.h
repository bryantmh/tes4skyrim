// What every real opcode needs: the stack helpers, the two ways a command
// names its target, and the installer that records which opcodes are real so
// the stub pass leaves them alone.
// See: docs/commentary/morrowind_runtime.md#result-scripts

#pragma once

#include <set>
#include <string>

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

    template <class T>
    void Real(int code) {
        interpreter.installSegment5<T>(code);
        segment5.insert(code);
    }

    // The segment-3 form: a command with optional arguments.
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

}  // namespace mwruntime
