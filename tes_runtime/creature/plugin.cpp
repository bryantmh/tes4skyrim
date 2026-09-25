// CreatureRuntime -- SKSE plugin entry point.
//
// One job: composes meshes\animationdatasinglefile.txt and
// meshes\animationsetdatasinglefile.txt in memory, at the moment the engine
// parses them, from the vanilla base plus every fragment under
// Data\SKSE\Plugins\CreatureRuntime\animation\*.json, and, deprecated, the
// older Data\SKSE\Plugins\TESRuntime\animation\*.json
// (docs/reference/tes_runtime_fragments.md).
//
// Both parsers open their file through one shared helper; the hook replaces
// that single call in each parser. Everything else is the engine's own code.
// It resolves three addresses and touches no form, native or co-save.

#define _CRT_SECURE_NO_WARNINGS

#include <windows.h>

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <mutex>
#include <set>
#include <string>
#include <type_traits>
#include <vector>

#include "addresses.h"
#include "compose.h"
#include "hook.h"
#include "ids.h"
#include "log.h"
#include "paths.h"
#include "skse_abi.h"
#include "stream.h"

using namespace tesruntime;

namespace {

constexpr UInt32 kPluginVersion = 1;

using OpenFn = int (*)(const char* path, EngineStream** out, std::uint8_t flag, void* r9);

OpenFn      g_originalOpen = nullptr;
std::mutex  g_mutex;
bool        g_fragmentsLoaded = false;
std::vector<Json> g_fragments;

enum class Which { None, AnimData, AnimSetData };

std::string NormPath(const char* p) {
    std::string s(p ? p : "");
    for (auto& c : s) {
        if (c == '\\') c = '/';
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return s;
}

Which Classify(const char* path) {
    const std::string s = NormPath(path);
    if (s.find("animationdatasinglefile.txt") != std::string::npos) return Which::AnimData;
    if (s.find("animationsetdatasinglefile.txt") != std::string::npos) return Which::AnimSetData;
    return Which::None;
}

std::string SourceKey(const Json& fragment) {
    std::string key = fragment["source"].asString();
    for (auto& c : key) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return key;
}

// DEPRECATED, to be removed: Data\SKSE\Plugins\TESRuntime\animation, where
// fragments went before CreatureRuntime was its own DLL. A plugin whose
// fragment is also in the current folder takes the current one.
// See: docs/reference/tes_runtime_fragments.md#legacy-sidecar-paths
void AddLegacyFragments() {
    if (PluginsDir().empty()) return;
    const std::string dir = PluginsDir() + "TESRuntime\\animation";
    std::set<std::string> have;
    for (const Json& fragment : g_fragments) have.insert(SourceKey(fragment));
    std::size_t added = 0;
    for (Json& fragment : LoadFragments(dir)) {
        if (!have.insert(SourceKey(fragment)).second) continue;
        g_fragments.push_back(std::move(fragment));
        ++added;
    }
    if (!added) return;
    Log("compose: DEPRECATED -- %zu fragment(s) read from %s; rebuild those "
        "plugins' creatures so they move to CreatureRuntime\\animation",
        added, dir.c_str());
    std::sort(g_fragments.begin(), g_fragments.end(),
              [](const Json& a, const Json& b) { return SourceKey(a) < SourceKey(b); });
}

void EnsureFragments() {
    if (g_fragmentsLoaded) return;
    g_fragmentsLoaded = true;
    const std::string dir = SidecarDir() + "animation";
    g_fragments = LoadFragments(dir);
    AddLegacyFragments();
    Log("compose: %zu fragment(s) under %s", g_fragments.size(), dir.c_str());
}

// The replacement for the resource-open helper, reached only from the two
// parsers' call sites. Same signature as the helper.
int OpenHook(const char* path, EngineStream** out, std::uint8_t flag, void* r9) {
    const Which which = Classify(path);
    if (which == Which::None) return g_originalOpen(path, out, flag, r9);

    std::lock_guard<std::mutex> lk(g_mutex);
    EnsureFragments();
    const int rc = g_originalOpen(path, out, flag, r9);
    if (rc != 0 || !out || !*out) {
        Log("compose: original open of %s failed (%d)", path, rc);
        return rc;
    }
    if (g_fragments.empty()) return rc;

    std::string base;
    if (!ReadEngineStream(*out, base)) {
        Log("compose: could not read the base for %s; leaving it untouched", path);
        return rc;
    }
    ReleaseEngineStream(*out);

    const Lines baseLines = SplitLines(base);
    const Lines composed = which == Which::AnimData
        ? ComposeAnimationData(baseLines, g_fragments)
        : ComposeAnimationSetData(baseLines, g_fragments);
    std::string text = JoinLines(composed);
    Log("compose: %s base %zu lines (%zu projects) -> %zu lines (%s projects)",
        path, baseLines.size(), baseLines.empty() ? 0 : static_cast<size_t>(std::atoi(baseLines[0].c_str())),
        composed.size(), composed.empty() ? "0" : composed[0].c_str());
    *out = NewMemoryStream(std::move(text));
    Log("compose: handing the parser memstream %p", static_cast<void*>(*out));
    return 0;
}

bool InstallHooks() {
    const std::uintptr_t open = Resolve("ResourceOpen", ids::kResourceOpen, ids::kSigResourceOpen);
    const std::uintptr_t p1 = Resolve("AnimDataParser", ids::kAnimDataParser, ids::kSigAnimDataParser);
    const std::uintptr_t p2 = Resolve("AnimSetDataParser", ids::kAnimSetDataParser, ids::kSigAnimSetDataParser);
    if (!open || !p1 || !p2) {
        Log("hooks: an address is unresolved; converted creature projects will NOT be registered");
        return false;
    }
    DetectStreamLayout(p1, ids::kParserBodyBytes);
    const std::uintptr_t c1 = FindCallTo(p1, ids::kParserScanBytes, open);
    const std::uintptr_t c2 = FindCallTo(p2, ids::kParserScanBytes, open);
    if (!c1 || !c2) {
        Log("hooks: no unique call to the open helper in a parser (%p / %p); refusing",
            reinterpret_cast<void*>(c1), reinterpret_cast<void*>(c2));
        return false;
    }
    g_originalOpen = reinterpret_cast<OpenFn>(open);
    const bool ok1 = PatchCall(c1, reinterpret_cast<void*>(&OpenHook), "AnimData");
    const bool ok2 = PatchCall(c2, reinterpret_cast<void*>(&OpenHook), "AnimSetData");
    return ok1 && ok2;
}

}  // namespace

extern "C" {

// MUST stay a static aggregate: SKSE reads it with
// LOAD_LIBRARY_AS_IMAGE_RESOURCE and runs no initializers
// (project_skse_version_data_static_init).
__declspec(dllexport) SKSEPluginVersionData SKSEPlugin_Version = {
    SKSEPluginVersionData::kVersion,  // dataVersion
    kPluginVersion,                   // pluginVersion
    "CreatureRuntime",                // name[256]
    "TESConversion",                  // author[256]
    "",                               // supportEmail[252]
    0,                                // versionIndependenceEx
    SKSEPluginVersionData::kVersionIndependent_AddressLibraryPostAE |
        SKSEPluginVersionData::kVersionIndependent_Signatures |
        SKSEPluginVersionData::kVersionIndependent_StructsPost629,
    {0},                              // compatibleVersions
    0,                                // seVersionRequired
};

static_assert(std::is_trivially_copyable<SKSEPluginVersionData>::value,
              "SKSEPlugin_Version must stay a POD written straight into .data");

// The pre-AE discovery path: SKSE 2.0.x (runtime 1.5.97) and SKSEVR call
// this instead of reading SKSEPlugin_Version. Both exports coexist; AE-era
// SKSE prefers the version struct.
__declspec(dllexport) bool SKSEPlugin_Query(const SKSEInterface* skse, PluginInfo* info) {
    info->infoVersion = PluginInfo::kInfoVersion;
    info->name = SKSEPlugin_Version.name;
    info->version = kPluginVersion;
    return skse && !skse->isEditor;
}

__declspec(dllexport) bool SKSEPlugin_Load(const SKSEInterface* skse) {
    SetPluginName(SKSEPlugin_Version.name);
    OpenLog();
    Log("CreatureRuntime %u loading (runtime %08X, SKSE %08X)", kPluginVersion,
        skse->runtimeVersion, skse->skseVersion);
    if (!g_versionDb.Load(skse->runtimeVersion)) {
        Log("addresses: no Address Library database for this runtime; "
            "falling back to signature scans only");
    } else {
        Log("addresses: loaded %s (%zu entries)", g_versionDb.path().c_str(),
            g_versionDb.count());
    }
    // The parsers run lazily, well after plugin load, so the call sites can
    // be patched here without waiting for any SKSE message.
    Log("hooks: animation cache %s", InstallHooks() ? "installed" : "NOT installed");
    return true;
}

}  // extern "C"
