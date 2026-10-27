// Console command execution.
//
// The riskiest file in the plugin: the only place that reaches into engine
// internals to make something happen. Isolated so a mistake here cannot spread.
//
// STRATEGY
// --------
// We call the console's own executor (ids::kConsoleExecute) rather than
// building a ScriptBuffer and calling CompileAndRun ourselves. That function
// takes the command text, then constructs the compiler, the buffer and the
// script internally -- both call sites of CompileAndRun live inside it.
//
// This matters because a mismatched ScriptBuffer layout does not fail loudly.
// It corrupts memory or reads garbage, and the resulting symptom looks like a
// conversion bug -- the single most expensive failure mode for this project.
// Handing the engine a string and letting it build its own structures removes
// that entire class of risk, and keeps working across game updates.
//
// The script object we pass is created by the engine's own script factory, so
// we never guess at its size or field layout either.

#include <windows.h>

#include <cstdio>
#include <string>

#include "addresses.h"
#include "game.h"
#include "ids.h"
#include "log.h"

namespace bridge {

namespace {

// (TESObjectREFR* target, Script* script, void* unk, int compilerType)
//
// ARGUMENT ORDER VERIFIED BY DISASSEMBLY (GOG 1.6.659, 0x1403013a0) -- the
// Script is arg2, NOT arg1. The prologue is:
//
//   48 8B F1        mov rsi, rcx    ; arg1 -> rsi   (target; never dereferenced
//                                   ;                here, so nullptr is fine)
//   48 8B FA        mov rdi, rdx    ; arg2 -> rdi   (the Script)
//   41 8B D9        mov ebx, r9d    ; arg4 -> ebx   (compiler type; cmp ebx,1)
//   ...
//   48 8B 47 38     mov rax, [rdi+0x38]             ; reads the SCRIPT TEXT
//
// and it calls Script::CompileAndRun (0x303cf0 == kCompileAndRun) internally,
// which confirms the identification.
//
// The earlier declaration had the Script in arg1 and passed nullptr in arg2, so
// the engine dereferenced [nullptr + 0x38] on every command -- caught by the
// SEH handler and reported as "engine raised an exception executing the
// command". A crash rather than garbage, fortunately: the loud failure mode.
using ConsoleExecute_t = bool (*)(void*, void*, void*, std::uint32_t);

// The console's FULL dispatcher: compiles, then RUNS, with the console-mode
// flag set around the run exactly as a typed command has it.
//
//   dispatch(rcx = Script, rdx = execContext, r8d = compilerType, r9 = target)
//
// See ids.h for the disassembly this came from, and why ConsoleExecute alone
// is not enough.
using ConsoleDispatch_t = bool (*)(void*, void*, std::uint32_t, void*);

// Compiler-name table index 1 == "SysWindowCompileAndRun".
constexpr std::uint32_t kCompilerTypeConsole = 1;

std::uintptr_t g_dispatch = 0;

// Finds the dispatcher STRUCTURALLY rather than by ID or a raw signature.
//
// Scan .text for `call kConsoleExecute`, then require the caller to contain the
// console-mode store (`mov byte [reg+reg], 1` reached via an `0x600`
// immediate) shortly after -- that is what distinguishes the dispatcher from
// the other two call sites, which only compile. Then walk back to the
// function's prologue.
//
// This is self-checking: if the shape is not found, we report nothing rather
// than returning a plausible wrong address, and the bridge keeps the old
// compile-only behaviour instead of calling into the middle of some function.
std::uintptr_t FindConsoleDispatch(std::uintptr_t consoleExecute) {
    if (!consoleExecute) return 0;

    std::uintptr_t begin = 0, end = 0;
    if (!TextRange(begin, end)) return 0;

    for (std::uintptr_t a = begin; a + 5 < end; ++a) {
        const auto* p = reinterpret_cast<const std::uint8_t*>(a);
        if (p[0] != 0xE8) continue;
        std::int32_t rel;
        std::memcpy(&rel, p + 1, sizeof(rel));
        if (a + 5 + static_cast<std::intptr_t>(rel) !=
            static_cast<std::intptr_t>(consoleExecute)) {
            continue;
        }

        // Look ahead for `mov e??, 0x600` (B8+r imm32), the console-mode
        // offset the dispatcher loads before enabling printing.
        bool hasModeStore = false;
        for (std::size_t i = 5; i < 0x60 && a + i + 5 < end; ++i) {
            if ((p[i] & 0xF8) == 0xB8) {
                std::uint32_t imm;
                std::memcpy(&imm, p + i + 1, sizeof(imm));
                if (imm == 0x600) { hasModeStore = true; break; }
            }
        }
        if (!hasModeStore) continue;

        // Walk back to the prologue:
        //     40 57        push rdi     <-- REX.W prefix is PART of it
        //     41 56        push r14
        //     41 57        push r15
        //     48 83 EC ..  sub  rsp, N
        //
        // 🛑 The `40` matters. Matching from the `57` finds the function one
        // byte late, and entering there SKIPS `push rdi` -- the epilogue then
        // pops a value that was never pushed and returns to garbage. The
        // pattern must be anchored on the prefix, and the int3 padding before
        // it (0xCC) is what proves this is a function start rather than a
        // coincidental byte run inside another function.
        for (std::size_t back = 0x20; back < 0x120; ++back) {
            const auto* q = reinterpret_cast<const std::uint8_t*>(a - back);
            if (q[0] == 0x40 && q[1] == 0x57 && q[2] == 0x41 && q[3] == 0x56 &&
                q[4] == 0x41 && q[5] == 0x57 && q[6] == 0x48 && q[7] == 0x83 &&
                *(q - 1) == 0xCC) {
                return a - back;
            }
        }
    }
    return 0;
}

}  // namespace

// Implemented in script_object.cpp: allocates a Script via the engine's factory
// and sets its command text, without the plugin knowing the layout.
void* AllocScriptObject(const std::string& text);
void  FreeScriptObject(void* script);

// Locates the console's compile-AND-run dispatcher. Called once at startup;
// without it, commands compile but produce no output and have no effect.
bool InitConsoleDispatch() {
    if (g_dispatch) return true;
    g_dispatch = FindConsoleDispatch(g_addr.consoleExecute);
    if (g_dispatch) {
        Log("console: dispatcher (compile+run) at %p",
            reinterpret_cast<void*>(g_dispatch));
    } else {
        Log("console: dispatcher NOT found -- falling back to compile-only, "
            "which produces no output and does not execute");
    }
    return g_dispatch != 0;
}

std::uintptr_t ConsoleDispatchAddr() { return g_dispatch; }

namespace {

// ARG1: the console's own execution context.
//
// ConsoleExecute's arg1 (rsi) is forwarded straight into CompileAndRun's rcx,
// where it IS dereferenced. Passing null lets the script COMPILE -- the Script
// gets real bytecode and the call returns 1 -- but the run step never happens,
// so the command silently does nothing. That was the 2026-08-14 symptom, and
// it is invisible from the return value.
//
// The game passes a live object here (`mov rcx,[rbp+0x128]` in the console's
// own caller at 0x2fdbb2). We cannot synthesize one, so we capture the real
// pointer the first time the game itself runs a console command, then reuse
// it. Until then commands are rejected up-front rather than silently no-oping,
// because a false success is far worse than a clear error.


// Resolves a FormID to the TESForm* the dispatcher wants as its target.
//
// Returns nullptr for 0 (meaning "no target", which is correct for a plain
// command) and for an id the engine does not know -- LookupByID itself returns
// null there, which is why this is self-validating rather than a guess.
void* LookupForm(std::uint32_t formId) {
    if (!formId || !g_addr.lookupFormByID) return nullptr;
    using LookupByID_t = void* (*)(std::uint32_t);
    auto fn = reinterpret_cast<LookupByID_t>(g_addr.lookupFormByID);
    void* form = nullptr;
    __try {
        form = fn(formId);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        form = nullptr;
    }
    return form;
}

// Runs one command line against an optional target reference.
//
// 🛑 `target` is NOT optional decoration -- it is the ONLY way a command acts
// on a reference. Passing nullptr while selecting via a separate `prid` call
// does not work: each ExecOne is its own compile+execute and the console's
// selection does not survive between them. Verified with a controlled test --
// after `prid 00000014`, a bare `getpos x` printed nothing while
// `player.getpos x` printed a real value. Commands still returned success, so
// the failure was invisible: `moveto player` on a quest actor reported ok and
// moved nothing at all.
bool ExecOne(const std::string& cmd, std::string* err, void* target = nullptr) {
    void* script = AllocScriptObject(cmd);
    if (!script) {
        if (err) *err = "could not allocate script object";
        return false;
    }

    // The "execution context" is the ScriptCompiler, and on 1.6.1170 nothing
    // on the compile path reads it: ConsoleExecute only forwards it to
    // CompileAndRun, which only passes it as `this` to helpers that never
    // dereference it. So a zeroed stand-in serves until the game's own pointer
    // is captured, and a headless run needs no typed console command.
    // See: docs/commentary/headless_testing.md#console-without-a-keystroke
    alignas(16) static std::uint8_t statelessCompiler[64] = {};
    const std::uintptr_t captured = g_execContext.load();
    const std::uintptr_t ctx = captured ? captured : reinterpret_cast<std::uintptr_t>(statelessCompiler);

    // Handlers only PRINT while the thread-local console-mode byte is set (see
    // console_capture.cpp) -- without this, every command runs fine and reports
    // nothing, which is half a tool. Set it exactly the way the real console
    // does: this thread (we are inside a main-thread task), this call only,
    // restored immediately -- including on the SEH path. Plain byte ops rather
    // than an RAII scope because __try forbids unwindable locals (C2712).
    std::uint8_t* modeFlag = ConsoleModeFlagPtr();
    const std::uint8_t savedMode = modeFlag ? *modeFlag : 0;
    if (modeFlag) *modeFlag = 1;
    // Report where this actually ran and what the flag pointer resolved to.
    // "Which thread executes the command" is not answerable from outside the
    // process -- SKSE tasks need not run on the window thread -- and getting it
    // wrong makes an externally-poked TLS byte land on a thread the command
    // never touches, which looks exactly like the flag having no effect.
    RecordExecDiag(GetCurrentThreadId(), reinterpret_cast<std::uintptr_t>(modeFlag));

    bool ok = false;
    // A malformed command can raise a SEH exception inside the engine. Catching
    // it keeps one bad command from taking down a long-lived test session.
    // (A command naming something that does not exist -- a misspelled GMST, say
    // -- genuinely faults inside the engine, so this is a normal path, not just
    // a safety net.)
    __try {
        if (g_dispatch) {
            // PREFERRED: the console's own dispatcher, which compiles AND runs
            // and manages the console-mode flag itself. Verified live to return
            // real output ("GameSetting fJumpHeightMin >> 76.00").
            auto fn = reinterpret_cast<ConsoleDispatch_t>(g_dispatch);
            // r9 = target: threaded into BOTH the compile and the run.
            fn(script, reinterpret_cast<void*>(ctx), kCompilerTypeConsole, target);
            // Its bool return reports "did the console consume this", not "did
            // the command succeed" -- a successful `getgs` returns 0 and a
            // misspelled command returns 1. So success cannot be read from it.
            //
            // The engine announces an unknown command by PRINTING
            //     Script command "<name>" not found.
            // which the capture buffer already holds. Checking for that is the
            // only signal the engine actually gives, and without it every
            // typo is reported as a success -- which makes all results
            // untrustworthy, not just the failing one.
            ok = true;
        } else {
            // FALLBACK: compile-only. Kept so a build where the dispatcher
            // cannot be located still runs commands, but note this path
            // produces NO output -- see ids.h.
            auto fn = reinterpret_cast<ConsoleExecute_t>(g_addr.consoleExecute);
            ok = fn(reinterpret_cast<void*>(ctx), script, target, kCompilerTypeConsole);
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        if (err) *err = "engine raised an exception executing the command";
        ok = false;
    }

    if (modeFlag) *modeFlag = savedMode;
    FreeScriptObject(script);
    return ok;
}

}  // namespace

// Compiles and runs a multi-statement script body against an optional target.
//
// This is genuine script INJECTION, not just command dispatch: the text goes
// through the engine's own script compiler (the same one the console uses), so
// anything the console language accepts is compiled to bytecode and executed
// inside the game -- including sequences that read state, branch on it, and
// act, all within one compile.
//
// Statements are separated by newlines (or ';' from the caller, normalized
// here). Each is compiled and run in order against the same selected
// reference, which is what makes a scripted probe coherent: the selection
// cannot drift between statements the way it can across separate round trips.
//
// Failure is per statement and reported with its index, because "the script
// failed" is useless when the interesting question is WHICH line the engine
// rejected.
bool ScriptInject(const std::string& body,
                  std::uint32_t refFormId,
                  bool stopOnError,
                  int* failedIndex,
                  std::string* err,
                  std::vector<InjectedStatement>* statements) {
    if (failedIndex) *failedIndex = -1;
    if (!g_addr.consoleExecute) {
        if (err) *err = "console executor unavailable on this runtime";
        return false;
    }

    // Resolved ONCE and passed as every statement's target. Previously this ran
    // a `prid` as a separate statement, which does not carry into the ones that
    // follow -- so an injected body targeting a reference silently acted on
    // nothing (see ExecOne).
    void* target = nullptr;
    if (refFormId) {
        target = LookupForm(refFormId);
        if (!target) {
            if (err) *err = "could not resolve reference (unknown form id)";
            if (failedIndex) *failedIndex = 0;
            return false;
        }
    }

    bool allOk = true;
    int index = 0;
    std::string stmt;
    stmt.reserve(body.size());

    // Walk the body, splitting on newline and ';'. Done inline rather than with
    // a split helper so an empty trailing statement never becomes a bogus
    // "failed" entry.
    for (std::size_t i = 0; i <= body.size(); ++i) {
        const bool atEnd = (i == body.size());
        const char c = atEnd ? '\n' : body[i];
        if (c != '\n' && c != '\r' && c != ';') {
            stmt.push_back(c);
            continue;
        }

        // Trim surrounding whitespace; skip blank lines and comments.
        std::size_t b = stmt.find_first_not_of(" \t");
        std::size_t e = stmt.find_last_not_of(" \t");
        if (b != std::string::npos && stmt[b] != ';') {
            const std::string line = stmt.substr(b, e - b + 1);
            if (!line.empty() && line.rfind("//", 0) != 0) {
                std::string lineErr;

                // Capture per statement so a probe can read each answer, not
                // just learn whether the whole body succeeded.
                ConsoleCaptureBegin();
                bool lineOk = ExecOne(line, &lineErr, target);
                const std::string captured = ConsoleCaptureEnd();

                // A misspelled statement runs and prints "not found"; without
                // this it would be reported as succeeding, and a probe that
                // half-ran would look clean while every conclusion drawn from
                // it was wrong.
                if (lineOk && ConsoleOutputIndicatesUnknownCommand(captured)) {
                    lineOk = false;
                    if (lineErr.empty()) lineErr = "unknown console command";
                }

                if (statements) {
                    InjectedStatement s;
                    s.text = line;
                    s.ok = lineOk;
                    s.output = captured;
                    s.error = lineErr;
                    statements->push_back(std::move(s));
                }

                if (!lineOk) {
                    allOk = false;
                    if (failedIndex && *failedIndex < 0) *failedIndex = index;
                    if (err && err->empty()) {
                        *err = "statement " + std::to_string(index) + " (" + line +
                               ") failed" + (lineErr.empty() ? "" : ": " + lineErr);
                    }
                    if (stopOnError) return false;
                }
                ++index;
            }
        }
        stmt.clear();
    }

    return allOk;
}

bool ConsoleExecute(const std::string& cmd, std::uint32_t refFormId, std::string* err) {
    if (!g_addr.consoleExecute) {
        if (err) *err = "console executor unavailable on this runtime";
        return false;
    }

    // The reference is passed as the dispatcher's TARGET, not selected with a
    // separate `prid` command.
    //
    // The old `prid`-first approach was silently broken: each ExecOne is its
    // own compile+execute, so the console selection never reached the command
    // that followed it. Every ref-targeted command ran against no target and
    // still reported success -- `moveto player` on a quest actor moved nothing
    // while claiming to have worked, which is the exact false-success class
    // this file exists to avoid. No struct layout is assumed here either: the
    // engine's own LookupByID hands back the pointer.
    void* target = nullptr;
    if (refFormId) {
        target = LookupForm(refFormId);
        if (!target) {
            if (err) *err = "could not resolve reference (unknown form id)";
            return false;
        }
    }

    return ExecOne(cmd, err, target);
}

}  // namespace bridge
