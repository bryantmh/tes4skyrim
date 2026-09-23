// Runtime address resolution: Address Library stable ID first, signature
// scan second, and 0 (capability unavailable) when both fail. Nothing here
// hardcodes an RVA.
//
// Copied from tes_runtime/plugin rather than shared, so this MIT code stays
// out of the GPL-3.0 binary's dependency graph.
// See: docs/commentary/morrowind_runtime.md#licensing

#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>

namespace mwruntime {

class VersionDb {
public:
    // Loads Data/SKSE/Plugins/versionlib-<maj>-<min>-<build>-<sub>.bin next
    // to the running executable (falling back to the -0 database).
    bool Load(std::uint32_t runtimeVersion);
    bool LoadFile(const std::string& path);
    std::uintptr_t Get(std::uint64_t id) const;
    bool loaded() const { return loaded_; }
    const std::string& path() const { return path_; }
    size_t count() const { return map_.size(); }

private:
    std::unordered_map<std::uint64_t, std::uint64_t> map_;
    bool        loaded_ = false;
    std::string path_;
};

std::uintptr_t ModuleBase();
bool TextRange(std::uintptr_t& begin, std::uintptr_t& end);

// Pattern syntax: "48 8B 05 ?? ?? ?? ?? 48 85 C0". First match in .text.
std::uintptr_t ScanSignature(const char* pattern);

// Stable ID, then signature. 0 when neither resolves; the caller must treat
// that as "do not hook".
std::uintptr_t Resolve(const char* debugName, std::uint64_t stableId,
                       const char* signature);

// Swaps the virtual at byte `slotOffset` of `vtable` for `replacement`, when
// that slot holds `expected`. Returns the function it held, or null when
// either address is 0 or the slot holds anything else -- nothing is written.
//
// 🛑 A slot holding the wrong function is REFUSED: writing it would hand the
// engine our function for some unrelated virtual, which fails in a way no log
// would explain. A vtable steals no bytes, so no prologue hazard arises.
void* SwapVtableSlot(const char* debugName, std::uintptr_t vtable,
                     std::size_t slotOffset, std::uintptr_t expected,
                     void* replacement);

// Virtual call by INDEX into an engine object's vtable.
template <typename Fn>
Fn VCall(void* object, std::size_t slot) {
    void** vtable = *reinterpret_cast<void***>(object);
    return reinterpret_cast<Fn>(vtable[slot]);
}

// The field of type T at byte `offset` into an engine object.
template <typename T>
T& At(void* base, std::size_t offset) {
    return *reinterpret_cast<T*>(static_cast<char*>(base) + offset);
}

extern VersionDb g_versionDb;

}  // namespace mwruntime
