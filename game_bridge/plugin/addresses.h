// Runtime address resolution.
//
// Two independent mechanisms, tried in order:
//   1. Address Library (versionlib-<ver>.bin) stable ID -> RVA. Authoritative
//      and already deployed in this install for 1.6.1170.
//   2. Signature scan of .text. Survives a game update that ships before a new
//      versionlib does.
//
// Nothing here hardcodes an RVA. A wrong address in a plugin does not fail
// loudly -- it reads plausible garbage, which is the worst outcome for a tool
// whose entire job is to be trusted about what the engine decided.

#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace bridge {

// Address Library v2 database reader.
class VersionDb {
public:
    // Loads Data/SKSE/Plugins/versionlib-<maj>-<min>-<patch>-0.bin, resolved
    // relative to the running executable.
    bool Load(std::uint32_t runtimeVersion);

    // Loads a specific file. Used by the offline parser test, which runs
    // outside the game and so cannot derive the path from the module.
    bool LoadFile(const std::string& path);

    // stable ID -> absolute address, or 0.
    std::uintptr_t Get(std::uint64_t id) const;

    bool loaded() const { return loaded_; }
    const std::string& path() const { return path_; }
    size_t count() const { return map_.size(); }

private:
    std::unordered_map<std::uint64_t, std::uint64_t> map_;  // id -> rva
    bool        loaded_ = false;
    std::string path_;
};

// Byte-pattern scanner over the main module's .text.
// Pattern syntax: "48 8B 05 ?? ?? ?? ?? 48 85 C0"
std::uintptr_t ScanSignature(const char* pattern);

// Every match, up to maxMatches. For patterns that occur once per call site
// (e.g. an inlined check repeated across many handlers), where the point is a
// CONSENSUS over the operands rather than a single address.
std::vector<std::uintptr_t> ScanSignatureAll(const char* pattern,
                                             std::size_t maxMatches);

// Resolves an Address Library stable id against the loaded database. Exposed
// so a probe can name engine functions by id from Python, without the plugin
// needing a hardcoded entry for every address someone wants to look at.
std::uintptr_t ResolveStableId(std::uint64_t id);

// Module base / .text bounds of SkyrimSE.exe.
std::uintptr_t ModuleBase();

// Bounds of the main module's .text. Exposed so a caller can do its own
// structural scan (e.g. locating a function by the shape of its call sites)
// rather than trusting a raw signature.
bool TextRange(std::uintptr_t& begin, std::uintptr_t& end);

// Resolve by stable ID, falling back to a signature. Returns 0 if both fail;
// callers must treat 0 as "capability unavailable" and report E_UNSUPPORTED
// rather than calling through it.
std::uintptr_t Resolve(const char* debugName,
                       std::uint64_t stableId,
                       const char* signature = nullptr);

// Everything the bridge calls into. Populated once at DataLoaded.
struct GameAddresses {
    // Console command execution. We drive the console's own executor, which
    // builds the compiler/buffer/script internally, so the plugin never has to
    // match a ScriptBuffer layout.
    std::uintptr_t consoleExecute    = 0;   // id 21954
    std::uintptr_t compileAndRun     = 0;   // id 21964 (inner step; unused by default)

    // Script object construction.
    std::uintptr_t consolePrint      = 0;   // id 51105 (output capture)
    std::uintptr_t papyrusLog        = 0;   // id 53551 (VM output capture)
    std::uintptr_t scriptVtable      = 0;   // id 191694
    std::uintptr_t scriptCtor        = 0;   // id 21874 (1170: signature only)
    std::uintptr_t scriptSetText     = 0;   // id 21883
    std::uintptr_t scriptDtor        = 0;
    std::uintptr_t memAlloc          = 0;   // id 68115
    std::uintptr_t memoryManager     = 0;   // id 400188, MemAlloc's `this`

    // Form lookup.
    std::uintptr_t lookupFormByID    = 0;

    // Asset caches, for hot reload.
    std::uintptr_t modelDb           = 0;

    bool Init(std::uint32_t runtimeVersion);

    // Which optional capabilities resolved. Reported by the `capabilities`
    // command so the client can degrade gracefully instead of guessing.
    std::vector<std::string> missing;
};

extern GameAddresses g_addr;
extern VersionDb     g_versionDb;

}  // namespace bridge
