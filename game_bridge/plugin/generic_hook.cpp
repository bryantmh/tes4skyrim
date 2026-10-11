// Generic, Python-driven hooking.
//
// WHY
// ---
// Every hook used to be hardcoded, so trying a new one cost a rebuild AND a
// game restart. That is the exact round trip this bridge exists to remove, and
// it is what made the console-execution bug take a whole session to find.
//
// With this, a hook is an experiment: name an address from Python, get a
// counter and an argument log back, remove it when done. No rebuild, no
// relaunch.
//
// WHAT A GENERIC HOOK DOES
// ------------------------
// It cannot run arbitrary user code (there is no interpreter in here), but it
// can do the two things a probe actually needs:
//
//   * COUNT calls -- answers "does this function even run?", which is the
//     question that repeatedly cost rebuild cycles to guess at
//   * RECORD the first four integer/pointer arguments of the last N calls --
//     which is how you learn what an engine function is really being handed
//     (and, e.g., recover a context pointer built in someone else's stack
//     frame, without hooking the function that builds it)
//
// SAFETY
// ------
// Relocating the wrong instruction corrupts the game in ways that look like
// data bugs -- and one such mistake already crashed it (see
// project_detour_relocatability). So the analysis is done HERE, in C++,
// against the bytes actually in memory, and an unsafe target is refused with
// a specific reason rather than installed hopefully.

#include <windows.h>

#include <cstdint>
#include <cstring>
#include <deque>
#include <mutex>
#include <string>
#include <vector>

#include "detour.h"
#include "generic_hook.h"
#include "log.h"

namespace bridge {

namespace {

std::mutex g_mutex;

struct Slot {
    bool           used = false;
    std::uintptr_t target = 0;
    std::string    label;
    volatile long  hits = 0;
    void*          original = nullptr;
    std::size_t    stolen = 0;
    std::deque<HookCall> calls;   // bounded ring of recent argument sets
    std::size_t    keep = 16;
};

// Fixed slot count: each needs its own thunk (below), and a debugging session
// never needs many at once.
constexpr int kMaxHooks = 8;
Slot g_slots[kMaxHooks];

void Record(int idx, std::uintptr_t a1, std::uintptr_t a2,
            std::uintptr_t a3, std::uintptr_t a4) {
    Slot& s = g_slots[idx];
    InterlockedIncrement(&s.hits);
    std::lock_guard<std::mutex> lk(g_mutex);
    if (s.calls.size() >= s.keep) s.calls.pop_front();
    s.calls.push_back(HookCall{a1, a2, a3, a4});
}

// One concrete thunk per slot. A capturing lambda cannot be used as a raw
// function pointer, and the detour must be a plain __fastcall-compatible
// function, so the slot index is baked in at compile time.
//
// Each returns whatever the original returned, so a hooked function keeps
// behaving exactly as before -- these observe, they never alter.
template <int N>
std::uintptr_t Thunk(std::uintptr_t a1, std::uintptr_t a2,
                     std::uintptr_t a3, std::uintptr_t a4) {
    Record(N, a1, a2, a3, a4);
    auto orig = reinterpret_cast<std::uintptr_t (*)(
        std::uintptr_t, std::uintptr_t, std::uintptr_t, std::uintptr_t)>(
        g_slots[N].original);
    return orig ? orig(a1, a2, a3, a4) : 0;
}

void* ThunkFor(int i) {
    switch (i) {
        case 0: return reinterpret_cast<void*>(&Thunk<0>);
        case 1: return reinterpret_cast<void*>(&Thunk<1>);
        case 2: return reinterpret_cast<void*>(&Thunk<2>);
        case 3: return reinterpret_cast<void*>(&Thunk<3>);
        case 4: return reinterpret_cast<void*>(&Thunk<4>);
        case 5: return reinterpret_cast<void*>(&Thunk<5>);
        case 6: return reinterpret_cast<void*>(&Thunk<6>);
        case 7: return reinterpret_cast<void*>(&Thunk<7>);
        default: return nullptr;
    }
}

}  // namespace

// Decides how many bytes can be stolen from `addr`, or 0 with a reason.
//
// Walks real instruction boundaries (a minimal length decoder covering the
// prologue forms Skyrim actually uses) and refuses anything position-dependent.
// A caller must never have to guess a length -- guessing is what cut an
// instruction in half and crashed the game.
std::size_t AnalyzePrologue(std::uintptr_t addr, std::string* why) {
    const auto* p = reinterpret_cast<const std::uint8_t*>(addr);
    std::size_t len = 0;

    while (len < 24) {
        const std::uint8_t* i = p + len;
        std::size_t n = 0;

        // Optional REX prefix.
        std::size_t r = (i[0] >= 0x40 && i[0] <= 0x4F) ? 1 : 0;
        const std::uint8_t op = i[r];
        const std::uint8_t modrm = i[r + 1];

        if (op == 0xE8 || op == 0xE9) { if (why) *why = "relative call/jmp"; return 0; }
        if (op == 0xEB)               { if (why) *why = "relative jmp (rel8)"; return 0; }
        if (op >= 0x70 && op <= 0x7F) { if (why) *why = "conditional jump (rel8)"; return 0; }
        if (op == 0x0F && modrm >= 0x80 && modrm <= 0x8F) {
            if (why) *why = "conditional jump (rel32)"; return 0;
        }
        // mov r64, rsp -- captures the stack pointer; meaningless once moved.
        if (r && op == 0x8B && (modrm & 0xC7) == 0xC4) {
            if (why) *why = "mov r64,rsp (captures rsp)"; return 0;
        }
        // lea r64,[rip+disp32] -- rip-relative.
        if (r && op == 0x8D && (modrm & 0xC7) == 0x05) {
            if (why) *why = "lea r64,[rip+...]"; return 0;
        }
        // Any modrm with mod=00, rm=101 is rip-relative addressing.
        if ((op == 0x89 || op == 0x8B) && (modrm & 0xC7) == 0x05) {
            if (why) *why = "rip-relative memory operand"; return 0;
        }

        // Lengths for the forms that appear in these prologues.
        if (op == 0x55 || (op >= 0x50 && op <= 0x57)) {
            n = r + 1;                                   // push r64
        } else if (op == 0x89 || op == 0x8B) {           // mov r/m64, r64
            const std::uint8_t mod = modrm >> 6;
            const std::uint8_t rm = modrm & 7;
            n = r + 2;                                   // opcode + modrm
            if (rm == 4) n += 1;                         // SIB
            if (mod == 1) n += 1;                        // disp8
            else if (mod == 2) n += 4;                   // disp32
        } else if (op == 0x83) {                         // sub/add r/m64, imm8
            n = r + 3;
        } else if (op == 0x81) {                         // sub/add r/m64, imm32
            n = r + 6;
        } else if (op == 0xC7) {                         // mov r/m64, imm32
            const std::uint8_t mod = modrm >> 6;
            const std::uint8_t rm = modrm & 7;
            n = r + 2;
            if (rm == 4) n += 1;
            if (mod == 1) n += 1;
            else if (mod == 2) n += 4;
            n += 4;                                      // imm32
        } else {
            if (why) *why = "unrecognised instruction in the prologue";
            return 0;
        }

        len += n;
        if (len >= 12) return len;   // enough for the 12-byte absolute jump
    }
    if (why) *why = "no safe cut point within 24 bytes";
    return 0;
}

int InstallGenericHook(std::uintptr_t target, const std::string& label,
                       std::size_t keep, std::string* err) {
    if (!target) { if (err) *err = "null target"; return -1; }

    std::lock_guard<std::mutex> lk(g_mutex);
    int idx = -1;
    for (int i = 0; i < kMaxHooks; ++i) {
        if (g_slots[i].used && g_slots[i].target == target) {
            if (err) *err = "already hooked";
            return i;
        }
        if (!g_slots[i].used && idx < 0) idx = i;
    }
    if (idx < 0) { if (err) *err = "no free hook slots (max 8)"; return -1; }

    std::string why;
    const std::size_t stolen = AnalyzePrologue(target, &why);
    if (!stolen) {
        if (err) *err = "cannot hook safely: " + why;
        return -1;
    }

    std::vector<std::uint8_t> expected(stolen);
    std::memcpy(expected.data(), reinterpret_cast<const void*>(target), stolen);

    Slot& s = g_slots[idx];
    if (!InstallDetour(target, ThunkFor(idx), expected.data(), stolen,
                       &s.original, label.c_str())) {
        if (err) *err = "detour install failed (see the log)";
        return -1;
    }

    s.used = true;
    s.target = target;
    s.label = label;
    s.hits = 0;
    s.stolen = stolen;
    s.keep = keep ? keep : 16;
    s.calls.clear();
    Log("hook[%d]: %s at %p (%zu bytes stolen)", idx, label.c_str(),
        reinterpret_cast<void*>(target), stolen);
    return idx;
}

// Removes a hook by restoring the original bytes.
//
// Without this, a wrong hook is permanent for the session and the only way to
// try a different one is a rebuild plus a relaunch -- exactly the round trip
// this whole subsystem exists to eliminate. A hook you cannot take back is a
// hook you cannot safely experiment with.
bool RemoveGenericHook(int idx, std::string* err) {
    if (idx < 0 || idx >= kMaxHooks) {
        if (err) *err = "bad slot index";
        return false;
    }
    std::lock_guard<std::mutex> lk(g_mutex);
    Slot& s = g_slots[idx];
    if (!s.used) {
        if (err) *err = "slot is empty";
        return false;
    }

    // The trampoline holds the original instructions; copy them back over our
    // jump. Do NOT free the trampoline: another thread may be executing inside
    // it right now, and there is no safe way to know. Leaking a few hundred
    // bytes per experiment is the correct trade.
    auto* fn = reinterpret_cast<std::uint8_t*>(s.target);
    DWORD old = 0;
    if (!VirtualProtect(fn, s.stolen, PAGE_EXECUTE_READWRITE, &old)) {
        if (err) *err = "VirtualProtect failed";
        return false;
    }
    std::memcpy(fn, s.original, s.stolen);
    VirtualProtect(fn, s.stolen, old, &old);
    FlushInstructionCache(GetCurrentProcess(), fn, s.stolen);

    Log("hook[%d]: removed %s at %p", idx, s.label.c_str(),
        reinterpret_cast<void*>(s.target));
    s.used = false;
    s.target = 0;
    s.label.clear();
    s.calls.clear();
    s.hits = 0;
    return true;
}

bool GetGenericHook(int idx, HookInfo* out) {
    if (idx < 0 || idx >= kMaxHooks || !out) return false;
    std::lock_guard<std::mutex> lk(g_mutex);
    Slot& s = g_slots[idx];
    if (!s.used) return false;
    out->index = idx;
    out->target = s.target;
    out->label = s.label;
    out->hits = s.hits;
    out->stolen = s.stolen;
    out->calls.assign(s.calls.begin(), s.calls.end());
    return true;
}

int GenericHookCount() { return kMaxHooks; }

}  // namespace bridge
