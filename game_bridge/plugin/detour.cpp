#include "detour.h"

#include <windows.h>

#include <cstdio>
#include <cstring>
#include <string>

#include "generic_hook.h"
#include "log.h"

namespace bridge {

namespace {

// 48 B8 <imm64>  mov rax, imm64
// FF E0          jmp rax
constexpr std::size_t kJumpBytes = 12;

void WriteAbsoluteJump(std::uint8_t* at, std::uintptr_t dest) {
    at[0] = 0x48;
    at[1] = 0xB8;
    std::memcpy(at + 2, &dest, sizeof(dest));
    at[10] = 0xFF;
    at[11] = 0xE0;
}

}  // namespace

std::uintptr_t FollowTailJump(std::uintptr_t addr, std::size_t maxScan) {
    if (!addr) return 0;
    const auto* p = reinterpret_cast<const std::uint8_t*>(addr);
    for (std::size_t i = 0; i < maxScan; ++i) {
        if (p[i] == 0xE9) {  // jmp rel32
            std::int32_t rel;
            std::memcpy(&rel, p + i + 1, sizeof(rel));
            return addr + i + 5 + rel;
        }
    }
    return 0;
}

bool InstallDetour(std::uintptr_t target,
                   void*          detour,
                   const std::uint8_t* expected,
                   std::size_t    stolenLen,
                   void**         outOriginal,
                   const char*    what) {
    if (!target || !detour || !expected || !outOriginal) return false;

    // The caller's own byte count, kept for the equality check below; the
    // decoder may pick a different (safe) steal length.
    const std::size_t expectedLen = stolenLen;

    if (stolenLen < kJumpBytes) {
        Log("detour(%s): stolen length %zu < %zu; refusing", what, stolenLen, kJumpBytes);
        return false;
    }

    auto* fn = reinterpret_cast<std::uint8_t*>(target);

    // 🛑 Byte-equality is NOT sufficient. A prologue can match exactly and
    // still be unsafe to relocate. This crashed the game on 2026-08-14: the
    // stolen bytes matched, but they contained `mov rax,rsp` (48 8B C4), so
    // the copy in the trampoline recorded the TRAMPOLINE's stack pointer and a
    // later `mov [rax+8],rbx` wrote into executable memory.
    //
    // The relocatability check is done by AnalyzePrologue (generic_hook.cpp),
    // which DECODES instructions rather than scanning bytes.
    //
    // A byte scan is not merely imprecise, it is wrong: it reads operand bytes
    // as opcodes. It rejected CompileAndRun because byte +12 is 0x74 -- the
    // modrm of `mov [rsp+0x18],rsi` -- which a scanner sees as `jcc rel8`.
    // Two "safety" checks then disagreed, and the naive one won.
    //
    // Callers must pass a length AnalyzePrologue returned, so the cut is
    // guaranteed to land on an instruction boundary and to contain nothing
    // position-dependent.
    {
        std::string why;
        const std::size_t safeLen = AnalyzePrologue(target, &why);
        if (!safeLen) {
            Log("detour(%s): REFUSING -- %s. The prologue may match exactly and "
                "still be unsafe to move; this function needs a different "
                "approach, not a longer steal.",
                what, why.c_str());
            return false;
        }
        if (stolenLen != safeLen) {
            // The caller's hand-written length disagrees with the decoder.
            // Trust the DECODER -- a hand-written constant is exactly how an
            // instruction gets cut in half, and it is derived from one build's
            // disassembly while the decoder reads the bytes actually loaded.
            Log("detour(%s): caller asked for %zu bytes, decoder says %zu -- "
                "using %zu",
                what, stolenLen, safeLen, safeLen);
            stolenLen = safeLen;
        }
    }

    // The prologue must match what the caller verified in a disassembler, over
    // the bytes they actually supplied. (The decoder may have chosen a longer
    // safe cut than the caller's constant; only compare what was provided.)
    if (std::memcmp(fn, expected, expectedLen < stolenLen ? expectedLen : stolenLen) != 0) {
        // Log the FULL actual prologue, not a prefix. The expected bytes were
        // derived from the GOG 1.6.659 build (the only copy that disassembles
        // statically -- Steam's .text is encrypted on disk, entropy 8.00, and
        // only decrypts in memory). So this mismatch is the one place a
        // cross-build difference can surface, and the actual bytes are exactly
        // what is needed to fix it: paste them into ids.h as the new expected
        // prologue after checking they are still position-independent.
        char got[3 * 24 + 1] = {};
        char want[3 * 24 + 1] = {};
        const std::size_t show = stolenLen < 24 ? stolenLen : 24;
        for (std::size_t i = 0; i < show; ++i) {
            std::snprintf(got + i * 3, 4, "%02X ", fn[i]);
            std::snprintf(want + i * 3, 4, "%02X ", expected[i]);
        }
        Log("detour(%s): prologue mismatch at %p; refusing to hook.\n"
            "    got      %s\n"
            "    expected %s\n"
            "    (expected bytes come from GOG 1.6.659; if this runtime differs, "
            "verify the new bytes are position-independent before updating.)",
            what, fn, got, want);
        return false;
    }

    auto* tramp = static_cast<std::uint8_t*>(VirtualAlloc(
        nullptr, stolenLen + kJumpBytes, MEM_COMMIT | MEM_RESERVE,
        PAGE_EXECUTE_READWRITE));
    if (!tramp) {
        Log("detour(%s): VirtualAlloc failed", what);
        return false;
    }

    std::memcpy(tramp, fn, stolenLen);
    WriteAbsoluteJump(tramp + stolenLen, target + stolenLen);

    DWORD old = 0;
    if (!VirtualProtect(fn, stolenLen, PAGE_EXECUTE_READWRITE, &old)) {
        Log("detour(%s): VirtualProtect failed", what);
        VirtualFree(tramp, 0, MEM_RELEASE);
        return false;
    }

    // Publish the trampoline BEFORE the jump goes live: another thread can
    // enter the detour the instant the jump is written.
    *outOriginal = tramp;
    WriteAbsoluteJump(fn, reinterpret_cast<std::uintptr_t>(detour));
    // Fill the rest of the stolen range with int3 so a jump landing mid-range
    // traps immediately instead of executing a partial instruction.
    for (std::size_t i = kJumpBytes; i < stolenLen; ++i) fn[i] = 0xCC;

    VirtualProtect(fn, stolenLen, old, &old);
    FlushInstructionCache(GetCurrentProcess(), fn, stolenLen);

    Log("detour(%s): hooked %p, trampoline %p", what,
        reinterpret_cast<void*>(target), tramp);
    return true;
}

}  // namespace bridge
