#include "hook.h"

#include <windows.h>

#include <cstring>
#include <vector>

#include "addresses.h"
#include "log.h"

namespace tesruntime {

namespace {

std::uintptr_t CallTarget(const std::uint8_t* p) {
    std::int32_t rel;
    std::memcpy(&rel, p + 1, sizeof(rel));
    return reinterpret_cast<std::uintptr_t>(p) + 5 + rel;
}

// A page holding `mov rax, imm64; jmp rax` stubs, placed so a rel32 at
// `site` can reach it. A DLL can load anywhere in the 64-bit space, so the
// patched call cannot reach the replacement directly.
//
// `site` must be the CALL SITE, not the module base: .text spans tens of MB,
// so a page within reach of the base can still be out of reach of a call
// deep inside it (measured: every hook failed "out of rel32 range" on
// 1.6.1170 when this was ModuleBase()). The search is bounded well inside
// rel32 so one page stays reachable from every call site.
std::uint8_t* NearStub(std::uintptr_t site, std::uintptr_t dest) {
    static std::uint8_t* page = nullptr;
    static std::size_t   used = 0;
    if (page) {
        const std::intptr_t d = reinterpret_cast<std::intptr_t>(page)
                                - static_cast<std::intptr_t>(site);
        if (d > INT32_MAX || d < INT32_MIN) return nullptr;
    }
    if (!page) {
        SYSTEM_INFO si;
        GetSystemInfo(&si);
        const std::uintptr_t gran = si.dwAllocationGranularity;
        // Stay clear of the rel32 edges: the stub must remain reachable from
        // call sites either side of `site`, not just from `site` itself.
        constexpr std::uintptr_t kSpan = 0x70000000;
        for (std::uintptr_t off = gran; off < kSpan && !page; off += gran) {
            for (int dir = -1; dir <= 1; dir += 2) {
                const std::uintptr_t at = site + static_cast<std::uintptr_t>(
                    static_cast<std::intptr_t>(off) * dir);
                if (at < gran) continue;
                auto* p = static_cast<std::uint8_t*>(VirtualAlloc(
                    reinterpret_cast<void*>(at), si.dwPageSize,
                    MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE));
                if (p) { page = p; break; }
            }
        }
        if (!page) return nullptr;
    }
    if (used + 16 > 4096) return nullptr;
    for (std::size_t i = 0; i < used; i += 16) {
        std::uintptr_t have;
        std::memcpy(&have, page + i + 2, sizeof(have));
        if (have == dest) return page + i;
    }
    std::uint8_t* s = page + used;
    s[0] = 0x48; s[1] = 0xB8;
    std::memcpy(s + 2, &dest, sizeof(dest));
    s[10] = 0xFF; s[11] = 0xE0;
    used += 16;
    FlushInstructionCache(GetCurrentProcess(), s, 12);
    return s;
}

}  // namespace

std::uintptr_t FindCallTo(std::uintptr_t fn, std::size_t scanLen, std::uintptr_t target) {
    if (!fn || !target) return 0;
    std::uintptr_t begin = 0, end = 0;
    if (!TextRange(begin, end)) return 0;
    if (fn < begin || fn + scanLen > end) return 0;
    std::uintptr_t found = 0;
    const auto* p = reinterpret_cast<const std::uint8_t*>(fn);
    for (std::size_t i = 0; i + 5 <= scanLen; ++i) {
        if (p[i] != 0xE8) continue;
        if (CallTarget(p + i) != target) continue;
        if (found) return 0;              // ambiguous: refuse
        found = fn + i;
    }
    return found;
}

int PatchAllCalls(std::uintptr_t target, void* replacement, const char* what) {
    std::uintptr_t begin = 0, end = 0;
    if (!target || !TextRange(begin, end)) return 0;
    std::vector<std::uintptr_t> sites;
    const auto* p = reinterpret_cast<const std::uint8_t*>(begin);
    for (std::uintptr_t i = 0; i + 5 <= end - begin; ++i) {
        if (p[i] == 0xE8 && CallTarget(p + i) == target) sites.push_back(begin + i);
    }
    int n = 0;
    for (std::uintptr_t s : sites) {
        if (PatchCall(s, replacement, what)) ++n;
    }
    Log("hook(%s): %d of %zu call sites patched", what, n, sites.size());
    return n;
}

bool PatchCall(std::uintptr_t callAddr, void* replacement, const char* what) {
    auto* p = reinterpret_cast<std::uint8_t*>(callAddr);
    if (!p || p[0] != 0xE8) return false;
    std::uint8_t* stub = NearStub(callAddr, reinterpret_cast<std::uintptr_t>(replacement));
    if (!stub) {
        Log("hook(%s): no memory within rel32 reach of the call at %p",
            what, reinterpret_cast<void*>(callAddr));
        return false;
    }
    const std::intptr_t delta = reinterpret_cast<std::intptr_t>(stub)
                                - static_cast<std::intptr_t>(callAddr + 5);
    if (delta > INT32_MAX || delta < INT32_MIN) {
        Log("hook(%s): stub out of rel32 range", what);
        return false;
    }
    const std::int32_t rel = static_cast<std::int32_t>(delta);
    DWORD old = 0;
    if (!VirtualProtect(p, 5, PAGE_EXECUTE_READWRITE, &old)) {
        Log("hook(%s): VirtualProtect failed", what);
        return false;
    }
    std::memcpy(p + 1, &rel, sizeof(rel));
    VirtualProtect(p, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), p, 5);
    Log("hook(%s): call at %p -> %p", what, reinterpret_cast<void*>(callAddr), replacement);
    return true;
}

void* PatchVtableSlot(void** vtable, std::size_t slot, void* replacement, const char* what) {
    if (!vtable) return nullptr;
    void** at = vtable + slot;
    void* original = *at;
    DWORD old = 0;
    if (!VirtualProtect(at, sizeof(void*), PAGE_READWRITE, &old)) {
        Log("hook(%s): slot %zu is not writable", what, slot);
        return nullptr;
    }
    *at = replacement;
    VirtualProtect(at, sizeof(void*), old, &old);
    FlushInstructionCache(GetCurrentProcess(), at, sizeof(void*));
    Log("hook(%s): slot %zu %p -> %p", what, slot, original, replacement);
    return original;
}

}  // namespace tesruntime
