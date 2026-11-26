// ArchiveLoader -- SKSE plugin that mounts each active plugin's "<stem> - Part N.bsa" archives right after its
// own two (archives.cpp, docs/commentary/tes_runtime_archives.md#part-archives). Runs on AE (Address Library),
// SE 1.5.97 and VR 1.4.15 (ids_pre_ae.h).

#include <windows.h>

#include <iterator>
#include <type_traits>

#include "addresses.h"
#include "archives.h"
#include "ids_pre_ae.h"
#include "log.h"
#include "paths.h"
#include "skse_abi.h"

using namespace tesruntime;

namespace {

constexpr UInt32 kPluginVersion = 1;

}  // namespace

extern "C" {

// MUST stay a static aggregate: SKSE reads it with
// LOAD_LIBRARY_AS_IMAGE_RESOURCE and runs no initializers.
__declspec(dllexport) SKSEPluginVersionData SKSEPlugin_Version = {
    SKSEPluginVersionData::kVersion,  // dataVersion
    kPluginVersion,                   // pluginVersion
    "ArchiveLoader",                  // name[256]
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
// this instead of reading SKSEPlugin_Version.
__declspec(dllexport) bool SKSEPlugin_Query(const SKSEInterface* skse, PluginInfo* info) {
    info->infoVersion = PluginInfo::kInfoVersion;
    info->name = SKSEPlugin_Version.name;
    info->version = kPluginVersion;
    return skse && !skse->isEditor;
}

// Hooks the mount here, not on a later message: the engine mounts every plugin's archives at startup.
__declspec(dllexport) bool SKSEPlugin_Load(const SKSEInterface* skse) {
    SetPluginName(SKSEPlugin_Version.name);
    OpenLog();
    Log("ArchiveLoader %u loading (runtime %08X, SKSE %08X)", kPluginVersion,
        skse->runtimeVersion, skse->skseVersion);
    if (!g_versionDb.Load(skse->runtimeVersion) &&
        !g_versionDb.LoadPreAe(skse->runtimeVersion, pre_ae::kTables, std::size(pre_ae::kTables))) {
        Log("addresses: no Address Library database or pre-AE table for this runtime");
    }
    Log("hooks: Part archives %s", InstallPartArchives() ? "installed" : "NOT installed");
    return true;
}

}  // extern "C"
