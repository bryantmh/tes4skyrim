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

extern VersionDb g_versionDb;

}  // namespace mwruntime
