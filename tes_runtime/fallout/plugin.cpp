// FalloutRuntime -- SKSE plugin entry point for FO3/FNV conversions.
//
// Gun routing (guns.cpp): FO3/FNV guns get hand type 13 and the iGun* graph
// variables the patched humanoid graphs branch on, and the shot, reload key,
// ammo restriction, iron sights and gun parts ride on it (fire.cpp, zoom.cpp,
// parts.cpp). Every sidecar is read from Data\SKSE\Plugins\FalloutRuntime\.
//
// Limb severing (sever.cpp) is compiled but DORMANT: it has never worked in
// game, so nothing here installs its hooks or its co-save.
// See: docs/commentary/asset_convert_falloutnv.md#dismemberment

#include <windows.h>

#include <type_traits>

#include "addresses.h"
#include "engine.h"
#include "guns.h"
#include "log.h"
#include "paths.h"
#include "skse_abi.h"

using namespace tesruntime;

namespace {

constexpr UInt32 kPluginVersion = 1;

bool g_gunsInstalled = false;

bool CaptureVm(void* vm) {
    g_api.vm = vm;
    Log("papyrus: VM %p", vm);
    return true;
}

void OnMessage(SKSEMessagingInterface::Message* msg) {
    if (msg && msg->type == SKSEMessagingInterface::kMessage_DataLoaded && g_gunsInstalled) {
        ResolveGunForms();
    }
}

void QueryInterfaces(const SKSEInterface* skse) {
    auto* msg = static_cast<SKSEMessagingInterface*>(skse->QueryInterface(kInterface_Messaging));
    if (msg) msg->RegisterListener(skse->GetPluginHandle(), "SKSE", OnMessage);
    auto* papyrus = static_cast<SKSEPapyrusInterface*>(skse->QueryInterface(kInterface_Papyrus));
    if (papyrus) papyrus->Register(CaptureVm);
    g_api.task = static_cast<SKSETaskInterface*>(skse->QueryInterface(kInterface_Task));
    Log("interfaces: messaging %s, papyrus %s, task %s", msg ? "ok" : "missing",
        papyrus ? "ok" : "missing", g_api.task ? "ok" : "missing");
}

}  // namespace

extern "C" {

// MUST stay a static aggregate: SKSE reads it with
// LOAD_LIBRARY_AS_IMAGE_RESOURCE and runs no initializers
// (project_skse_version_data_static_init).
__declspec(dllexport) SKSEPluginVersionData SKSEPlugin_Version = {
    SKSEPluginVersionData::kVersion,  // dataVersion
    kPluginVersion,                   // pluginVersion
    "FalloutRuntime",                 // name[256]
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
    Log("FalloutRuntime %u loading (runtime %08X, SKSE %08X)", kPluginVersion,
        skse->runtimeVersion, skse->skseVersion);
    if (!g_versionDb.Load(skse->runtimeVersion)) {
        Log("addresses: no Address Library database for this runtime; "
            "falling back to signature scans only");
    } else {
        Log("addresses: loaded %s (%zu entries)", g_versionDb.path().c_str(),
            g_versionDb.count());
    }
    QueryInterfaces(skse);
    g_gunsInstalled = ResolveEngine() && InstallGuns();
    Log("hooks: gun routing %s", g_gunsInstalled ? "installed" : "NOT installed");
    return true;
}

}  // extern "C"
