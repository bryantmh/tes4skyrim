// Address Library stable IDs.
//
// Every ID here was derived by locating the target in the GOG/AE 1.6.659 build
// (the only non-DRM-packed copy, so the only one that disassembles statically)
// and inverting its RVA through versionlib-1-6-659-0.bin. The same ID then
// resolves on ANY build shipping a versionlib -- including the DRM-packed Steam
// 1.6.1170 the user actually plays. That is why nothing here is a raw RVA.
//
// VERIFIED translations (659 -> 1170), checked against both databases:
//   21964  CompileAndRun        0x303cf0  -> 0x343c20
//   368092 compilerNameTable    0x1e5cc70 -> 0x1ff2d60
//   386860 hkClass hkpRigidBody 0x1e8f798 -> 0x2025e78
//   387372 hkClass hkaRagdoll   0x1e90798 -> 0x2026e78
//
// Re-deriving one: find the address in the GOG exe, then
//   python tools/address_lib.py --rva <rva> --from 1.6.659
//
// !! Do NOT add "IDs" for globals like g_thePlayer / g_console by taking the
// nearest-below versionlib entry. Those live inside one enormous .data symbol
// (id 414365 on 1.6.640) at offsets of hundreds of KB, and that offset does not
// survive a rebuild -- it resolves to a plausible-looking wrong pointer, which
// is the worst failure mode this plugin can have. Reach such state through the
// console/Papyrus instead, or discover it via RTTI at runtime.

#pragma once

#include <cstdint>

namespace bridge::ids {

// Script::CompileAndRun(ScriptCompiler*, ScriptBuffer*, Script*)
//
// The inner compile+run step. Callable, but it expects a ScriptBuffer the
// caller has already built -- see kConsoleExecute for why we do not do that.
constexpr std::uint64_t kCompileAndRun = 21964;

// The console's full command executor:
//   (rcx = Script*, rdx = TESObjectREFR* target, r8 = ?, r9d = compilerType)
//
// r9d indexes the compiler-name table; 1 == "SysWindowCompileAndRun", the
// console's own compiler. This function builds the ScriptBuffer, selects the
// compiler and calls CompileAndRun itself -- both call sites of
// kCompileAndRun live inside it.
//
// Preferring this over CompileAndRun is the whole point: we hand over the
// command text and the engine constructs every intermediate structure, so the
// bridge never has to match a ScriptBuffer layout. A wrong layout would not
// fail loudly; it would corrupt memory or read garbage.
constexpr std::uint64_t kConsoleExecute = 21954;

// Table of compiler-type name pointers. Index 1 is "SysWindowCompileAndRun",
// the compiler the console itself uses.
constexpr std::uint64_t kCompilerNameTable = 368092;

// TESForm* TESForm::LookupByID(std::uint32_t formId)
//
// 🛑 THIS IS WHAT MAKES A REF-TARGETED COMMAND ACTUALLY TARGET THE REF.
//
// The console dispatcher takes the target reference in r9 and threads it into
// BOTH the compile and the run (verified by disassembling the live process):
//
//   dispatch(rcx=Script, rdx=ctx, r8d=compilerType, r9=target)
//     mov r14, r9        ; save target
//     mov r8,  r14       ; -> ConsoleExecute's arg3   (compile)
//     mov rdx, r14       ; -> runner's arg2           (run)
//
// This plugin used to pass nullptr there and select the reference by running
// `prid <id>` as a SEPARATE ExecOne first. That does not work: each ExecOne is
// its own compile+execute, so the selection does not survive into the next
// call. Proven with a controlled test on the player -- after `prid 00000014`,
// a bare `getpos x` printed NOTHING while `player.getpos x` printed a real
// value. Every ref-targeted command was silently acting on no target, and
// because the dispatcher still returned success it looked like it had worked.
// `moveto player` on a quest actor reported ok and moved nothing.
//
// Found WITHOUT guessing an id (see the warning at the top of this file): the
// Papyrus native name string "GetForm" is referenced by its registration site,
// whose bound implementation is a two-instruction thunk --
//     mov ecx, r9d ; jmp <LookupByID>
// -- and that jump target is this function. Verified live before use: 0x14,
// 0x1A032A17 and 0x7 all return real pointers, and a bogus id returns 0, which
// is the self-validating behaviour a blind signature scan cannot give you.
//
// 1.6.1170 rva 0x1e01a0 -> id 14617 + 0x0, which resolves to 0x1a0b70 on the
// GOG 1.6.659 build.
constexpr std::uint64_t kLookupFormByID = 14617;

// 🛑 THE CONSOLE'S FULL DISPATCHER -- compile AND run.
//
// `kConsoleExecute` ONLY COMPILES. Its tail is `call <compile finalizer>;
// mov al,1; ret`, so a caller that stops there gets a truthful-looking
// `returned: 1` with no output and no effect. That false success cost this
// project two separate debugging sessions.
//
// This function is the one the console itself calls, and it does the whole job
// (verified by disassembling the LIVE Steam 1.6.1170 process, not GOG):
//
//   dispatch(rcx = Script, rdx = ?, r8d = compilerType, r9 = target)
//     rbp = TLS block ; save [rbp+0x768] ; [rbp+0x768] = 0x14   (source marker)
//     call ConsoleExecute(ctx, Script, target, type)            (COMPILES)
//     if (ok && Script->bytecodeLen != 0):
//         byte [rbp+0x600] = 1        <-- console-mode: makes handlers PRINT
//         call runner(Script, target) <-- THIS is what actually executes
//         byte [rbp+0x600] = 0
//     [rbp+0x768] = saved
//
// Calling this instead of ConsoleExecute gets execution, the console-mode flag
// and its restore all handled by the engine, exactly as a typed command does.
//
// It is NOT resolved by a stable ID or a raw signature. Both would be guesses
// about a function whose only job is to wrap kConsoleExecute. Instead it is
// found STRUCTURALLY: scan .text for a `call kConsoleExecute` whose following
// bytes match the "compile ok -> set 0x600 -> call runner" shape, then walk
// back to that function's prologue. That derivation is self-checking (the
// console-mode store must be there) and survives any build where
// kConsoleExecute itself resolves. See FindConsoleDispatch in console_exec.cpp.

// Havok class registry entries, used by the ragdoll probe to identify objects
// by their hkClass pointer rather than by guessing at struct layout.
constexpr std::uint64_t kHkClassRigidBody      = 386860;
constexpr std::uint64_t kHkClassRagdollInstance = 387372;

// Script object construction.
//
// The engine allocates 0x80 bytes and calls Script::ctor. We MUST call the real
// constructor -- reproducing it by hand does not work:
//
//   Script::ctor (0x2fc5d0 on 1.6.659) first calls a BASE-CLASS constructor
//   (0x19e330), which touches thread-local state and stores its own vtable,
//   then writes several fields including `mov byte [rbx+0x1A], 0x13` -- a
//   non-zero type/flags byte. The earlier "memset to zero, store the vtable"
//   substitute skipped both, and the console executor faulted on the
//   half-built object (SEH-caught: "engine raised an exception").
//
// Its stable ID 21874 exists on 1.6.659 but is ABSENT from 1.6.1170, so it is
// resolved by SIGNATURE (kSigScriptCtor below) -- verified unique in .text.
constexpr std::uint64_t kScriptCtor   = 21874;   // 0x2fc5d0 -> (absent on 1170)
constexpr std::uint64_t kScriptVtable = 191694;  // 0x166e680 -> 0x17c1618
constexpr std::uint64_t kMemAlloc     = 68115;   // 0xc390a0  -> 0xcc40c0
// MemoryManager, MemAlloc's `this`: it reads [this+0x28] first, so a null
// heap faults. The engine passes this global in all 7,034 calls (1.6.1170
// rva 0x35f11e0 -> id 400188 + 0x0).
constexpr std::uint64_t kMemoryManager = 400188;  // 0x1f50f80 -> 0x35f11e0
constexpr std::size_t   kScriptSize   = 0x80;    // from the console's own alloc

// Script::SetText(const char*) -- allocates a copy and stores it at script+0x38.
// (Verified by disassembly: `mov [rbp+0x38], rdi` after the alloc.)
constexpr std::uint64_t kScriptSetText = 21883;  // 0x2fd170 -> 0x33cf80

// Offset of the script text pointer. The only struct offset the plugin assumes,
// and it is read straight out of Script::SetText's own store instruction.
constexpr std::size_t kScriptTextOffset = 0x38;

// ---------------------------------------------------------------------------
// Signature fallbacks. Used only when versionlib has no entry (e.g. a game
// update landed before the Address Library was refreshed). A target that
// resolves through neither path is reported as E_UNSUPPORTED and never called.
// ---------------------------------------------------------------------------

// Script::CompileAndRun prologue on 1.6.x:
//   mov [rsp+8],rbx / mov [rsp+10],rbp / mov [rsp+18],rsi / push rdi
//   sub rsp,20 / mov rbx,r8 / mov rsi,rdx / mov rbp,rcx / test rdx,rdx / je
//
// Verified against GOG 1.6.659: exactly ONE match in .text, at 0x140303cf0.
// (Uniqueness matters more than length here -- a signature that matches twice
// resolves to whichever comes first, silently.)
constexpr const char* kSigCompileAndRun =
    "48 89 5C 24 08 48 89 6C 24 10 48 89 74 24 18 57 48 83 EC 20 "
    "49 8B D8 48 8B F2 48 8B E9 48 85 D2 0F 84";

// Console executor prologue. Verified unique in .text on GOG 1.6.659
// (exactly one match, at 0x1403013a0).
constexpr const char* kSigConsoleExecute =
    "48 8B C4 57 48 81 EC C0 00 00 00 48 C7 44 24 20 FE FF FF FF "
    "48 89 58 08 48 89 70 10 41 8B D9 48 8B FA";

// Console::Print(const char* fmt, ...) -- the console's own print entry point.
//
// FOUND VIA RTTI, not guesswork. Static call-counting fails here (the callers
// reach it indirectly), which is why an earlier pass wrongly concluded it could
// not be identified safely. The reliable route:
//
//   1. TypeDescriptor ".?AVConsole@@"            (rva 0x1f0e4b8 on 1.6.659)
//   2. -> CompleteObjectLocator                  (rva 0x1a1c960)
//   3. -> the Console vtable                     (rva 0x179c060)
//   4. vtable[11] is a 5-instruction thunk at 0x89b340:
//        mov  rcx, [rip+...]   ; the Console singleton (id 401203)
//        mov  r8,  rdx         ; caller's text becomes arg3
//        lea  rdx, [rip+...]   ; -> the literal "> %s"   <-- PROOF
//        jmp  0x89b480         ; tail-call the real printer
//
// The "> %s" format string is the console's echo prefix, which identifies this
// beyond doubt. Hooking it captures everything a command prints.
constexpr std::uint64_t kConsolePrint = 51105;   // 0x89b340 -> 0x8f75d0

// The thunk shape above, with the two rip-relative operands wildcarded.
// Verified: exactly ONE match in .text on GOG 1.6.659.
constexpr const char* kSigConsolePrint =
    "48 8B 0D ?? ?? ?? ?? 4C 8B C2 48 8D 15 ?? ?? ?? ?? E9";

// SkyrimScript::Logger::Log(const char* msg, ...) -- the sink EVERY Papyrus
// message passes through (Debug.Trace, Debug.Notification, VM errors).
//
// Found by RTTI, like Console::Print:
//   TypeDescriptor ".?AVLogger@SkyrimScript@@"  (rva 0x1f12078 on 1.6.659)
//     -> CompleteObjectLocator -> vtable        (rva 0x17b5b70, id 216736)
//     -> vtable[1] = Log                        (rva 0x945290)
//
// The prologue saves rdx into rdi before anything else, which is what pins the
// message to arg2.
constexpr std::uint64_t kPapyrusLog = 53551;   // 0x945290 -> 0x9a43d0

// Script::ctor prologue:
//   push rbx / sub rsp,20 / mov rbx,rcx / call <base ctor> / mov ecx,[rip+?]
//   lea rax,[rip+?] (the Script vtable) / mov [rbx],rax / xor r8d,r8d
//   mov [rbx+0x60],r8
//
// The three rel32/disp32 operands are wildcarded because they move every build;
// the surrounding opcodes do not. Verified against GOG 1.6.659: exactly ONE
// match in .text, at 0x1402fc5d0 -- which is also the address stable ID 21874
// gives, and the `lea` it contains points at kScriptVtable (0x166e680), so the
// signature, the ID and the vtable all corroborate each other.
constexpr const char* kSigScriptCtor =
    "40 53 48 83 EC 20 48 8B D9 E8 ?? ?? ?? ?? 8B 0D ?? ?? ?? ?? "
    "48 8D 05 ?? ?? ?? ?? 48 89 03 45 33 C0 4C 89 43 60";

}  // namespace bridge::ids
