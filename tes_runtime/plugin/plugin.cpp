// TESRuntime -- SKSE plugin entry point.
//
// Three jobs, one DLL:
//   * composes meshes\animationdatasinglefile.txt and
//     meshes\animationsetdatasinglefile.txt in memory, at the moment the
//     engine parses them, from the vanilla base plus every fragment under
//     Data\SKSE\Plugins\TESRuntime\animation\*.json
//     (docs/reference/tes_runtime_fragments.md);
//   * routes FO3/FNV guns to hand type 13 and the iGun* graph variables
//     (guns.cpp), which the patched humanoid graphs branch on;
//   * severs FO3/FNV limbs on killing blows and keeps them severed (sever.cpp);
//   * points every converted crime faction at the jail nearest the player
//     (crime.cpp).
//
// Both parsers open their file through one shared helper; the hook replaces
// that single call in each parser. Everything else is the engine's own code.
//
// Building with TESRUNTIME_CACHE_ONLY keeps only the first job, so the cache
// composition can be tested with nothing else patched into the game
// (build.bat cache-only -> TESRuntime_CacheOnly.dll). That variant resolves
// three addresses and touches no form, native or co-save.

#define _CRT_SECURE_NO_WARNINGS

#include <windows.h>

#include <cctype>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <type_traits>
#include <vector>

#include "addresses.h"
#include "compose.h"
#include "hook.h"
#include "ids.h"
#include "log.h"
#include "skse_abi.h"
#include "stream.h"

#ifndef TESRUNTIME_CACHE_ONLY
#include "crime.h"
#include "engine.h"
#include "fire.h"
#include "guns.h"
#include "sever.h"
#endif

using namespace tesruntime;

namespace {

constexpr UInt32 kPluginVersion = 2;
#ifndef TESRUNTIME_CACHE_ONLY
constexpr UInt32 kSerializationId = 'TES4';
#endif

using OpenFn = int (*)(const char* path, EngineStream** out, std::uint8_t flag, void* r9);

OpenFn      g_originalOpen = nullptr;
std::mutex  g_mutex;
bool        g_fragmentsLoaded = false;
std::vector<Json> g_fragments;
#ifndef TESRUNTIME_CACHE_ONLY
bool        g_severingInstalled = false;
bool        g_crimeInstalled = false;
bool        g_gunsInstalled = false;
#endif

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

std::string FragmentDir() {
    wchar_t buf[MAX_PATH]{};
    GetModuleFileNameW(nullptr, buf, MAX_PATH);
    std::wstring w(buf);
    auto slash = w.find_last_of(L"\\/");
    std::wstring dir = (slash == std::wstring::npos) ? L"." : w.substr(0, slash);
    dir += L"\\Data\\SKSE\\Plugins\\TESRuntime\\animation";
    std::string out;
    for (wchar_t c : dir) out.push_back(static_cast<char>(c));
    return out;
}

void EnsureFragments() {
    if (g_fragmentsLoaded) return;
    g_fragmentsLoaded = true;
    const std::string dir = FragmentDir();
    g_fragments = LoadFragments(dir);
    Log("compose: %zu fragment(s) under %s", g_fragments.size(), dir.c_str());
}

// The cache-only build writes the text it handed the parser beside the log,
// so a run can be diffed against the Python composer's output.
void DumpComposed(Which which, const std::string& text) {
#ifdef TESRUNTIME_CACHE_ONLY
    const std::wstring path = LogDir() + (which == Which::AnimData
        ? L"TESRuntime_composed_animationdatasinglefile.txt"
        : L"TESRuntime_composed_animationsetdatasinglefile.txt");
    FILE* f = _wfopen(path.c_str(), L"wb");
    if (!f) return;
    std::fwrite(text.data(), 1, text.size(), f);
    std::fclose(f);
#else
    (void)which; (void)text;
#endif
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
    DumpComposed(which, text);
    *out = NewMemoryStream(std::move(text));
    Log("compose: handing the parser memstream %p", static_cast<void*>(*out));
    return 0;
}

bool InstallHooks() {
    const std::uintptr_t open = Resolve("ResourceOpen", ids::kResourceOpen, ids::kSigResourceOpen);
    const std::uintptr_t p1 = Resolve("AnimDataParser", ids::kAnimDataParser, ids::kSigAnimDataParser);
    const std::uintptr_t p2 = Resolve("AnimSetDataParser", ids::kAnimSetDataParser, ids::kSigAnimSetDataParser);
    if (!open || !p1 || !p2) {
        Log("hooks: an address is unresolved; TES4 projects will NOT be registered");
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

#ifndef TESRUNTIME_CACHE_ONLY
bool CaptureVm(void* vm) {
    g_api.vm = vm;
    Log("papyrus: VM %p", vm);
    return true;
}

void OnMessage(SKSEMessagingInterface::Message* msg) {
    if (!msg) return;
    if (msg->type == SKSEMessagingInterface::kMessage_DataLoaded) {
        if (g_gunsInstalled) ResolveGunForms();
        if (g_severingInstalled) ResolveSeverForms();
        if (g_crimeInstalled) {
            ResolveCrimeForms();
            StartCrimeTick();
        }
    } else if (msg->type == SKSEMessagingInterface::kMessage_PostLoadGame) {
        if (g_severingInstalled) SeverReapplyAll();
    }
}

void QueryInterfaces(const SKSEInterface* skse) {
    const PluginHandle handle = skse->GetPluginHandle();
    auto* msg = static_cast<SKSEMessagingInterface*>(skse->QueryInterface(kInterface_Messaging));
    if (msg) msg->RegisterListener(handle, "SKSE", OnMessage);
    auto* papyrus = static_cast<SKSEPapyrusInterface*>(skse->QueryInterface(kInterface_Papyrus));
    if (papyrus) papyrus->Register(CaptureVm);
    g_api.task = static_cast<SKSETaskInterface*>(skse->QueryInterface(kInterface_Task));
    auto* ser = static_cast<SKSESerializationInterface*>(skse->QueryInterface(kInterface_Serialization));
    if (ser) {
        ser->SetUniqueID(handle, kSerializationId);
        ser->SetSaveCallback(handle, SeverSave);
        ser->SetLoadCallback(handle, SeverLoad);
        ser->SetRevertCallback(handle, SeverRevert);
    }
    Log("interfaces: messaging %s, papyrus %s, task %s, serialization %s",
        msg ? "ok" : "missing", papyrus ? "ok" : "missing",
        g_api.task ? "ok" : "missing", ser ? "ok" : "missing");
}
#endif  // TESRUNTIME_CACHE_ONLY

}  // namespace

extern "C" {

// MUST stay a static aggregate: SKSE reads it with
// LOAD_LIBRARY_AS_IMAGE_RESOURCE and runs no initializers
// (project_skse_version_data_static_init).
__declspec(dllexport) SKSEPluginVersionData SKSEPlugin_Version = {
    SKSEPluginVersionData::kVersion,  // dataVersion
    kPluginVersion,                   // pluginVersion
#ifdef TESRUNTIME_CACHE_ONLY
    "TESRuntimeCacheOnly",            // name[256]
#else
    "TESRuntime",                     // name[256]
#endif
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
    OpenLog();
    Log("TESRuntime %u loading (runtime %08X, SKSE %08X)", kPluginVersion,
        skse->runtimeVersion, skse->skseVersion);
    if (!g_versionDb.Load(skse->runtimeVersion)) {
        Log("addresses: no Address Library database for this runtime; "
            "falling back to signature scans only");
    } else {
        Log("addresses: loaded %s (%zu entries)", g_versionDb.path().c_str(),
            g_versionDb.count());
    }
#ifndef TESRUNTIME_CACHE_ONLY
    QueryInterfaces(skse);
#endif
    // The parsers run lazily, well after plugin load, so the call sites can
    // be patched here without waiting for any SKSE message.
    Log("hooks: animation cache %s", InstallHooks() ? "installed" : "NOT installed");
#ifndef TESRUNTIME_CACHE_ONLY
    if (ResolveEngine()) {
        g_gunsInstalled = InstallGuns();
        g_severingInstalled = InstallSevering();
        g_crimeInstalled = LoadCrimeSidecars();
    }
    Log("hooks: gun routing %s, limb severing %s",
        g_gunsInstalled ? "installed" : "NOT installed",
        g_severingInstalled ? "installed" : "NOT installed");
#else
    Log("cache-only build: gun routing and limb severing are not compiled in");
#endif
    return true;
}

}  // extern "C"
