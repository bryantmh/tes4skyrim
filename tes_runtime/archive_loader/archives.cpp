#include "archives.h"

#include <windows.h>

#include <cctype>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <set>
#include <string>

#include "addresses.h"
#include "hook.h"
#include "ids.h"
#include "log.h"

namespace tesruntime {

namespace {

using LoadArchiveFn = void (*)(const char* name, void* arg1, void* arg2);

//: What the engine appends to a plugin's stem for its second archive (its suffix table, 1.6.1170 0x1fd4c90).
constexpr const char* kTexturesSuffix = " - Textures.bsa";
constexpr int kFirstPart = 2;

LoadArchiveFn g_loadArchive = nullptr;
std::mutex g_mountedLock;
std::set<std::string> g_mounted;

// Whether Data\<name> is a file, looked up the way the engine's own archive list looks (relative to the game).
bool ArchiveExists(const std::string& name) {
    const DWORD attrs = GetFileAttributesA(("Data\\" + name).c_str());
    return attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY);
}

// True the first time `name` is seen (case-insensitively): an archive is mounted once however often the engine
// re-mounts its plugin.
bool FirstMount(const std::string& name) {
    std::string key = name;
    for (char& c : key) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    std::lock_guard<std::mutex> guard(g_mountedLock);
    return g_mounted.insert(key).second;
}

// Replaces MountPluginArchives' call that mounts "<stem> - Textures.bsa": mounts it, then each
// "<stem> - Part N.bsa" from 2 up to the first one missing, with the same arguments and inside the same sink.
void MountTexturesThenParts(const char* name, void* arg1, void* arg2) {
    g_loadArchive(name, arg1, arg2);
    const std::size_t len = name ? std::strlen(name) : 0;
    const std::size_t suffixLen = std::strlen(kTexturesSuffix);
    if (len <= suffixLen || _stricmp(name + len - suffixLen, kTexturesSuffix) != 0) return;
    const std::string stem(name, len - suffixLen);
    for (int part = kFirstPart;; ++part) {
        const std::string archive = stem + " - Part " + std::to_string(part) + ".bsa";
        if (!ArchiveExists(archive)) break;
        if (!FirstMount(archive)) continue;
        g_loadArchive(archive.c_str(), arg1, arg2);
        Log("archives: mounted %s", archive.c_str());
    }
}

// The length of the function starting at `fn`, from the exe's unwind table; 0 when `fn` starts none.
std::size_t FunctionLength(std::uintptr_t fn) {
    DWORD64 imageBase = 0;
    const RUNTIME_FUNCTION* entry = fn ? RtlLookupFunctionEntry(fn, &imageBase, nullptr) : nullptr;
    if (!entry || imageBase + entry->BeginAddress != fn) return 0;
    return entry->EndAddress - entry->BeginAddress;
}

}  // namespace

bool InstallPartArchives() {
    const std::uintptr_t mount = Resolve("MountPluginArchives", ids::kMountPluginArchives, nullptr);
    g_loadArchive = reinterpret_cast<LoadArchiveFn>(Resolve("LoadArchive", ids::kLoadArchive, nullptr));
    std::uintptr_t sites[2] = {};
    const int calls = FindCallsTo(mount, FunctionLength(mount),
                                  reinterpret_cast<std::uintptr_t>(g_loadArchive), sites, 2);
    if (calls != 2) {
        Log("archives: Part archives NOT installed -- MountPluginArchives makes %d LoadArchive calls, expected 2",
            calls);
        return false;
    }
    return PatchCall(sites[1], reinterpret_cast<void*>(&MountTexturesThenParts), "plugin Part archives");
}

}  // namespace tesruntime
