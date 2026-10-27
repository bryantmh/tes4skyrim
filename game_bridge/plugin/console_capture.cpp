// Console output capture.
//
// Console commands print through Console::Print(fmt, ...). We install a
// detour on it, so `sqv`, `getav`, `getstage`, `help` etc. return their text
// over the pipe instead of only painting the screen. Without this the bridge
// can make things happen but cannot read anything back, which is half a tool.
//
// FINDING THE TARGET (see ids.h for the full derivation): located through RTTI
// -- ".?AVConsole@@" -> CompleteObjectLocator -> the Console vtable -> the
// print thunk, which contains the literal "> %s" (the console's echo prefix).
// That string is the proof of identity. Static call-counting does NOT work
// here, because the callers reach it indirectly.
//
// THE DETOUR
// ----------
// A 12-byte absolute jmp is written over the function's first bytes:
//     48 B8 <imm64>   mov rax, imm64
//     FF E0           jmp rax
// The overwritten bytes are relocated into a trampoline so the original can
// still be called. This is safe for the target because its first instruction
// boundary is known (verified in the disassembly) and no instruction in the
// overwritten range is rip-relative -- a rip-relative instruction MOVED to a
// trampoline would silently read the wrong address, which is exactly the class
// of failure this plugin must never introduce.
//
// The target thunk begins:
//     48 8B 0D xx xx xx xx    mov rcx,[rip+...]   <- RIP-RELATIVE
// so we do NOT hook the thunk. We hook the function it tail-jumps to
// (0x89b480 on 1.6.659), whose prologue is plain register/stack stores:
//     48 89 54 24 10   mov [rsp+10],rdx
//     4C 89 44 24 18   mov [rsp+18],r8
//     4C 89 4C 24 20   mov [rsp+20],r9
// 15 bytes of position-independent stores -- safe to relocate.

#include <windows.h>

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>

#include "addresses.h"
#include "detour.h"
#include "game.h"
#include "log.h"

namespace bridge {

// Defined in game.cpp; appends one line to the active capture buffer.
void ConsoleCaptureAppend(const char* text);

namespace {

// The DEEP printer takes an already-built va_list, not varargs:
//     Print(ConsoleLog* log, const char* fmt, va_list args)
//
// Hooking this rather than the varargs forwarder above it is what makes the
// re-call correct. The forwarder (0x89b480) is only 0x22 bytes whose entire
// body spills rdx/r8/r9 into the caller's shadow space and does
// `lea r8,[rsp+40]` to point a va_list at them. Detouring THAT and then
// re-calling it as `(self, "%s", text)` re-runs those spills against a
// different stack frame -- the formatted text never reaches the console and
// nothing is captured, which is exactly the empty-output symptom seen in-game.
using PrintFn = void (*)(void* self, const char* fmt, va_list args);

PrintFn  g_original = nullptr;   // trampoline -> original code
void*    g_trampoline = nullptr;
bool     g_installed = false;
std::uintptr_t g_target = 0;
volatile long  g_hits = 0;       // proves liveness without a rebuild

// Our replacement. Formats exactly as the original would, hands the text to
// the capture buffer, then calls through so the console still displays it.
void PrintDetour(void* self, const char* fmt, va_list args) {
    // Format from a COPY, so the original's va_list is left untouched and can
    // be forwarded verbatim. Consuming `args` here and then passing it on
    // would make the real printer read past the end of the argument list.
    char stackBuf[1024];
    std::string heapBuf;
    const char* text = stackBuf;

    va_list copy;
    va_copy(copy, args);
    const int n = std::vsnprintf(stackBuf, sizeof(stackBuf), fmt ? fmt : "", copy);
    va_end(copy);

    if (n >= static_cast<int>(sizeof(stackBuf))) {
        va_list copy2;
        va_copy(copy2, args);
        heapBuf.resize(static_cast<std::size_t>(n) + 1);
        std::vsnprintf(heapBuf.data(), heapBuf.size(), fmt ? fmt : "", copy2);
        va_end(copy2);
        heapBuf.resize(static_cast<std::size_t>(n));
        text = heapBuf.c_str();
    } else if (n < 0) {
        text = "";
    } else {
        stackBuf[n] = '\0';
    }

    InterlockedIncrement(&g_hits);
    if (*text) ConsoleCaptureAppend(text);

    // Forward the ORIGINAL va_list unchanged, so the console renders exactly
    // what it would have without the hook.
    if (g_original) g_original(self, fmt, args);
}

// The DEEP printer's prologue at 0x89b4b0 (1.6.659):
//   4C 89 44 24 18   mov [rsp+18],r8
//   48 89 54 24 10   mov [rsp+10],rdx
//   53               push rbx
//   55               push rbp
// 12 bytes, all position-independent, ending exactly on `push rsi` -- a clean
// instruction boundary (verified: byte 12 is 0x56).
constexpr std::uint8_t kExpectedPrologue[] = {
    0x4C, 0x89, 0x44, 0x24, 0x18,
    0x48, 0x89, 0x54, 0x24, 0x10,
    0x53,
    0x55,
};

// The forwarder in the middle of the chain: thunk -> forwarder -> deep printer.
// Its body is a 5-byte `call rel32` we must step over to reach the printer.
std::uintptr_t FollowInnerCall(std::uintptr_t addr, std::size_t maxScan = 48) {
    const auto* p = reinterpret_cast<const std::uint8_t*>(addr);
    for (std::size_t i = 0; i < maxScan; ++i) {
        if (p[i] == 0xE8) {  // call rel32
            std::int32_t rel;
            std::memcpy(&rel, p + i + 1, sizeof(rel));
            return addr + i + 5 + rel;
        }
    }
    return 0;
}

}  // namespace

bool InstallConsoleCapture(std::uintptr_t printAddr) {
    if (g_installed) return true;
    if (!printAddr) {
        Log("capture: no Console::Print address; console output will not be captured");
        return false;
    }

    // Three functions deep:
    //   thunk (resolved id)  -- rip-relative first instruction, DO NOT hook
    //     -> tail jmp -> varargs forwarder (0x89b480)
    //          -> call -> the real printer (0x89b4b0), which takes a va_list
    //
    // Hook the deepest one. The forwarder's entire body spills the varargs
    // into shadow space and points a va_list at them; detouring it and then
    // re-calling it corrupts that setup (this produced silently empty capture
    // in-game on 2026-08-14). The deep printer takes an already-built va_list,
    // so the detour can format a copy and forward the original untouched.
    const std::uintptr_t forwarder = FollowTailJump(printAddr);
    if (!forwarder) {
        Log("capture: no tail jmp in the print thunk at %p; refusing to hook",
            reinterpret_cast<void*>(printAddr));
        return false;
    }
    const std::uintptr_t target = FollowInnerCall(forwarder);
    if (!target) {
        Log("capture: no inner call in the print forwarder at %p; refusing to hook",
            reinterpret_cast<void*>(forwarder));
        return false;
    }

    void* orig = nullptr;
    if (!InstallDetour(target, reinterpret_cast<void*>(&PrintDetour),
                       kExpectedPrologue, sizeof(kExpectedPrologue),
                       &orig, "Console::Print")) {
        return false;
    }

    g_trampoline = orig;
    g_original = reinterpret_cast<PrintFn>(orig);
    g_target = target;
    g_installed = true;
    return true;
}

bool ConsoleCaptureInstalled() { return g_installed; }
long ConsoleCaptureHits() { return g_hits; }
std::uintptr_t ConsoleCaptureTarget() { return g_target; }

// ---------------------------------------------- execution-context capture --
//
// ConsoleExecute's arg1 is the ScriptCompiler the console builds in its own
// frame. We remember the game's own pointer when it runs one; until then
// ExecOne passes a stateless stand-in (see console_exec.cpp).

std::atomic<std::uintptr_t> g_execContext{0};

namespace {

// CompileAndRun(context, Script*, char** text) -- arg1 is the SAME execution
// context ConsoleExecute forwards, and unlike ConsoleExecute this function is
// safely hookable (its prologue is three shadow-space stores).
using CompileAndRun_t = std::uint32_t (*)(void*, void*, void*);

CompileAndRun_t g_carOriginal = nullptr;
bool            g_execHooked = false;
volatile long   g_execHits = 0;

std::uint32_t CompileAndRunDetour(void* ctx, void* script, void* text) {
    if (ctx) {
        g_execContext.store(reinterpret_cast<std::uintptr_t>(ctx));
        InterlockedIncrement(&g_execHits);
    }
    return g_carOriginal ? g_carOriginal(ctx, script, text) : 0;
}

// CompileAndRun prologue (GOG 1.6.659, 0x303cf0):
//   48 89 5C 24 08   mov [rsp+8],rbx
//   48 89 6C 24 10   mov [rsp+10],rbp
//   48 89 74 24 18   mov [rsp+18],rsi   (first 2 bytes complete the steal)
// 12 bytes of pure shadow-space stores: no rip-relative operand, no rsp
// capture, and byte 12 lands inside the third store's own encoding -- so we
// take all 15 and stop before `push rdi`.
constexpr std::uint8_t kCompileAndRunPrologue[] = {
    0x48, 0x89, 0x5C, 0x24, 0x08,
    0x48, 0x89, 0x6C, 0x24, 0x10,
    0x48, 0x89, 0x74, 0x24, 0x18,
};

}  // namespace

// 🛑 ConsoleExecute CANNOT BE DETOURED with a prologue-stealing hook.
//
// It CRASHED THE GAME (2026-08-14, access violation writing into .text). Two
// independent reasons, either one fatal:
//
//   +00  48 8B C4               mov rax,rsp          <-- CAPTURES rsp
//   +03  57                     push rdi
//   +04  48 81 EC C0 00 00 00   sub rsp,0xC0
//   +0B  48 C7 44 24 20 FE FF FF FF   mov qword [rsp+20],-2
//   +14  48 89 58 08            mov [rax+8],rbx      <-- uses that rax
//
// 1. `mov rax,rsp` is position-dependent in the way that matters: relocated
//    into a trampoline it records the TRAMPOLINE's stack pointer, and the
//    later `mov [rax+8],rbx` then writes 8 bytes past a stale address -- which
//    landed in executable memory and killed the process.
// 2. A 12-byte steal cuts the 9-byte `mov qword [rsp+20],-2` in half, so the
//    int3 padding sat mid-instruction.
//
// The prologue guard did not save us: the bytes MATCHED, they simply were not
// safe to move. Byte-equality proves the build is the expected one; it says
// nothing about relocatability. Any future hook must check BOTH.
//
// So the execution context is not obtained by hooking. It is captured
// passively instead -- see the note in console_exec.cpp.
// Hooks CompileAndRun, NOT ConsoleExecute. Both receive the console's
// execution context as arg1, but only CompileAndRun can be safely detoured.
bool InstallExecContextCapture(std::uintptr_t compileAndRunAddr) {
    if (g_execHooked) return true;
    if (!compileAndRunAddr) {
        Log("capture: no CompileAndRun address; execution context cannot be "
            "captured and console commands will compile but not run");
        return false;
    }
    void* orig = nullptr;
    if (!InstallDetour(compileAndRunAddr,
                       reinterpret_cast<void*>(&CompileAndRunDetour),
                       kCompileAndRunPrologue, sizeof(kCompileAndRunPrologue),
                       &orig, "CompileAndRun (context capture)")) {
        return false;
    }
    g_carOriginal = reinterpret_cast<CompileAndRun_t>(orig);
    g_execHooked = true;
    return true;
}

bool ExecContextCaptured() { return g_execContext.load() != 0; }
long ExecContextHits() { return g_execHits; }

// ------------------------------------------------- console-mode TLS flag --
//
// WHY COMMANDS PRINTED NOTHING (2026-08-14)
// -----------------------------------------
// The print hook above was correct all along -- and captured nothing, because
// with the bridge the prints never HAPPEN. Every ObScript handler that reports
// a value gates its print on a thread-local byte:
//
//   GetBaseActorValue handler, byte-identical shape on 1.6.659 and 1.6.1170:
//     8B 0D xx xx xx xx        mov ecx, [g_tlsIndex]     ; TLS slot index
//     65 48 8B 04 25 58 00 00 00   mov rax, gs:[0x58]    ; TLS block array
//     BA 00 06 00 00           mov edx, 0x600            ; flag offset
//     48 8B 04 C8              mov rax, [rax+rcx*8]      ; this thread's block
//     80 3C ?? 00              cmp byte [rdx+rax], 0
//     74 ..                    je  <skip the print>
//
// The console UI sets that byte on the main thread while dispatching a typed
// command; ConsoleExecute called directly never does. Proof, live: with the
// byte forced on, the hook went from 10 hits to 15,293. So the fix is to set
// the byte around our own executions -- same thread, same scope, same thing
// the real console does.
//
// DISCOVERY
// ---------
// The idiom above is inlined into MANY handlers, so instead of trusting one
// address, all matches are collected and the operands (index-global address,
// in-block offset) must AGREE. A consensus over dozens of independent inline
// copies cannot be a misidentified function. Both operands are read from the
// live bytes -- nothing about the TLS layout is hardcoded, so a build that
// moves the flag moves us with it.

namespace {

std::uintptr_t g_tlsIndexGlobal = 0;   // &g_tlsIndex (the slot index dword)
std::uint32_t  g_tlsFlagOffset = 0;    // byte offset inside the TLS block
int            g_tlsSigMatches = 0;

// VERIFIED against the GOG 1.6.659 image: 253 matches in .text, and the
// operands agree 248-to-5 on (global 0x352b548, offset 0x600). The 5 dissenters
// use offset 0x5D8 -- a different flag in the same TLS block, which is exactly
// why this takes a majority vote rather than trusting the first hit.
constexpr const char* kSigConsoleModeCheck =
    "8B 0D ?? ?? ?? ?? 65 48 8B 04 25 58 00 00 00 BA ?? ?? ?? ?? "
    "48 8B 04 C8 80 3C ?? 00";

}  // namespace

bool DiscoverConsoleModeFlag() {
    if (g_tlsIndexGlobal) return true;

    const auto matches = ScanSignatureAll(kSigConsoleModeCheck, 64);
    if (matches.size() < 2) {
        Log("console-mode: %zu match(es) for the handler idiom -- need >=2 "
            "for consensus; command output will not be captured",
            matches.size());
        return false;
    }

    // Tally (global, offset) pairs across every match.
    std::uintptr_t bestGlobal = 0;
    std::uint32_t  bestOffset = 0;
    int            bestVotes = 0;
    for (std::size_t i = 0; i < matches.size(); ++i) {
        const auto* m = reinterpret_cast<const std::uint8_t*>(matches[i]);
        std::int32_t disp;
        std::uint32_t off;
        std::memcpy(&disp, m + 2, sizeof(disp));   // mov ecx,[rip+disp32]
        std::memcpy(&off, m + 16, sizeof(off));    // mov edx, imm32
        const std::uintptr_t global = matches[i] + 6 + disp;

        int votes = 0;
        for (std::size_t j = 0; j < matches.size(); ++j) {
            const auto* n = reinterpret_cast<const std::uint8_t*>(matches[j]);
            std::int32_t d2; std::uint32_t o2;
            std::memcpy(&d2, n + 2, sizeof(d2));
            std::memcpy(&o2, n + 16, sizeof(o2));
            if (matches[j] + 6 + d2 == global && o2 == off) ++votes;
        }
        if (votes > bestVotes) { bestVotes = votes; bestGlobal = global; bestOffset = off; }
    }

    if (bestVotes < 2) {
        Log("console-mode: no operand consensus across %zu matches; refusing",
            matches.size());
        return false;
    }

    g_tlsIndexGlobal = bestGlobal;
    g_tlsFlagOffset = bestOffset;
    g_tlsSigMatches = bestVotes;
    Log("console-mode: flag = tls[*%p] + 0x%X (%d/%zu matches agree)",
        reinterpret_cast<void*>(bestGlobal), bestOffset, bestVotes,
        matches.size());
    return true;
}

namespace {
std::atomic<std::uint32_t>  g_lastExecThread{0};
std::atomic<std::uintptr_t> g_lastExecFlagPtr{0};
}  // namespace

void RecordExecDiag(std::uint32_t threadId, std::uintptr_t flagPtr) {
    g_lastExecThread.store(threadId);
    g_lastExecFlagPtr.store(flagPtr);
}
std::uint32_t  LastExecThreadId() { return g_lastExecThread.load(); }
std::uintptr_t LastExecFlagPtr() { return g_lastExecFlagPtr.load(); }

bool ConsoleModeFlagAvailable() { return g_tlsIndexGlobal != 0; }
std::uintptr_t ConsoleModeTlsIndexGlobal() { return g_tlsIndexGlobal; }
std::uint32_t  ConsoleModeTlsOffset() { return g_tlsFlagOffset; }
int            ConsoleModeSigMatches() { return g_tlsSigMatches; }

// The CALLING thread's flag byte. The bridge only ever calls this from inside
// a main-thread task, which is the one thread the engine itself flips it on.
std::uint8_t* ConsoleModeFlagPtr() {
    if (!g_tlsIndexGlobal) return nullptr;
    const std::uint32_t idx =
        *reinterpret_cast<const std::uint32_t*>(g_tlsIndexGlobal);
    const auto* tlsArray =
        reinterpret_cast<std::uintptr_t*>(__readgsqword(0x58));
    if (!tlsArray) return nullptr;
    const std::uintptr_t block = tlsArray[idx];
    if (!block) return nullptr;
    return reinterpret_cast<std::uint8_t*>(block + g_tlsFlagOffset);
}

}  // namespace bridge
