// Call-site and vtable-slot patching.
//
// The two singlefile parsers each contain exactly one `call <resource-open
// helper>`; redirecting that rel32 is the whole hook. No prologue is stolen
// and nothing is relocated, so none of the detour hazards recorded in
// project_detour_relocatability apply here.

#pragma once

#include <cstddef>
#include <cstdint>

namespace tesruntime {

// The unique `E8 rel32` in [fn, fn + scanLen) whose target is `target`.
// 0 when there is none or more than one.
std::uintptr_t FindCallTo(std::uintptr_t fn, std::size_t scanLen, std::uintptr_t target);

// Repoints the call at `callAddr` to `replacement` through a trampoline
// allocated within rel32 reach of the exe. Returns false and changes nothing
// on any failure.
bool PatchCall(std::uintptr_t callAddr, void* replacement, const char* what);

// Repoints EVERY `E8 rel32` in .text whose target is `target` (a function
// with many callers, none of them virtual). Returns the number patched.
int PatchAllCalls(std::uintptr_t target, void* replacement, const char* what);

// Points `vtable[slot]` at `replacement`. Returns the function it held, or
// null and changes nothing when the vtable is missing or not writable.
void* PatchVtableSlot(void** vtable, std::size_t slot, void* replacement, const char* what);

}  // namespace tesruntime
