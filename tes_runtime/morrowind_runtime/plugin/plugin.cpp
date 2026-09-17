// MorrowindRuntime.dll -- Morrowind dialogue, topics and journal in Skyrim.
//
// A SEPARATE DLL from TESRuntime.dll on purpose: it links vendored GPL-3.0
// OpenMW code, and a fault in a script interpreter must not take gun routing,
// limb severing or the animation cache down with it.
// See: docs/commentary/morrowind_runtime.md#licensing

#include <windows.h>

#include <cstdio>
#include <type_traits>

#include "activation.h"
#include "addresses.h"
#include "conversation.h"
#include "log.h"
#include "menu.h"
#include "skse_abi.h"
#include "store.h"

namespace mwruntime {

namespace {

constexpr UInt32 kPluginVersion = 1;

//: Serialization owner id, 'MWRT' -- the co-save records belong to this plugin.
constexpr UInt32 kSerializationId = 'MWRT';

SKSESerializationInterface* g_serialization = nullptr;

// The sidecars are only readable once the engine knows its own data path, so
// the store loads on kMessage_DataLoaded rather than at plugin load.
void OnSKSEMessage(SKSEMessagingInterface::Message* msg) {
    if (!msg || msg->type != SKSEMessagingInterface::kMessage_DataLoaded) return;
    const StoreStats stats = LoadStore();
    Log("store: %zu topics, %zu responses, %zu scripts from %zu sidecar(s)",
        stats.topics, stats.infos, stats.scripts, stats.files);
    if (!stats.files) {
        Log("store: no sidecar found -- no Morrowind dialogue will be offered, "
            "and every activation falls through to vanilla");
    }
    const std::size_t actors = LoadActorIndex();
    Log("activation: %zu actor(s) indexed", actors);
    // The MenuManager singleton only exists once the game is up, so the menu
    // registers here rather than at plugin load.
    Log("menu: install %s", InstallMenu() ? "ok" : "FAILED");
    InstallConversation();
    InstallActivation();
}

// SKSE hands the Papyrus VM over here, before kMessage_DataLoaded, which is
// what lets the actor index resolve each plugin's live load-order position.
bool CaptureVm(void* vm) {
    SetPapyrusVm(vm);
    Log("papyrus: VM %p", vm);
    return true;
}

void QueryInterfaces(const SKSEInterface* skse) {
    auto* msg = static_cast<SKSEMessagingInterface*>(
        skse->QueryInterface(kInterface_Messaging));
    if (msg) msg->RegisterListener(skse->GetPluginHandle(), "SKSE",
                                   OnSKSEMessage);
    auto* papyrus = static_cast<SKSEPapyrusInterface*>(
        skse->QueryInterface(kInterface_Papyrus));
    if (papyrus) papyrus->Register(CaptureVm);
    g_serialization = static_cast<SKSESerializationInterface*>(
        skse->QueryInterface(kInterface_Serialization));
    if (g_serialization) {
        g_serialization->SetUniqueID(skse->GetPluginHandle(),
                                     kSerializationId);
    }
    Log("interfaces: messaging %s, papyrus %s, serialization %s",
        msg ? "ok" : "MISSING", papyrus ? "ok" : "MISSING",
        g_serialization ? "ok" : "MISSING");
}

}  // namespace

}  // namespace mwruntime

extern "C" {

// MUST stay a static aggregate: SKSE reads it with
// LOAD_LIBRARY_AS_IMAGE_RESOURCE and runs no initializers, so an initializer
// here lands the export in .pdata and SKSE reads unwind entries as version
// fields (project_skse_version_data_static_init).
__declspec(dllexport) SKSEPluginVersionData SKSEPlugin_Version = {
    SKSEPluginVersionData::kVersion,   // dataVersion
    mwruntime::kPluginVersion,         // pluginVersion
    "MorrowindRuntime",                // name[256]
    "TESConversion",                   // author[256]
    "",                                // supportEmail[252]
    0,                                 // versionIndependenceEx
    SKSEPluginVersionData::kVersionIndependent_AddressLibraryPostAE |
        SKSEPluginVersionData::kVersionIndependent_Signatures |
        SKSEPluginVersionData::kVersionIndependent_StructsPost629,
    {0},                               // compatibleVersions
    0,                                 // seVersionRequired
};

static_assert(std::is_trivially_copyable<SKSEPluginVersionData>::value,
              "SKSEPlugin_Version must stay a POD written straight into .data");

// The pre-AE discovery path: SKSE 2.0.x (runtime 1.5.97) and SKSEVR call this
// instead of reading the version struct. Both exports coexist.
__declspec(dllexport) bool SKSEPlugin_Query(const SKSEInterface* skse,
                                            PluginInfo* info) {
    info->infoVersion = PluginInfo::kInfoVersion;
    info->name = SKSEPlugin_Version.name;
    info->version = mwruntime::kPluginVersion;
    return skse && !skse->isEditor;
}

__declspec(dllexport) bool SKSEPlugin_Load(const SKSEInterface* skse) {
    mwruntime::OpenLog();
    mwruntime::Log("MorrowindRuntime %u loading (runtime %08X, SKSE %08X)",
                   mwruntime::kPluginVersion, skse->runtimeVersion,
                   skse->skseVersion);
    if (!mwruntime::g_versionDb.Load(skse->runtimeVersion)) {
        mwruntime::Log("addresses: no Address Library database for this "
                       "runtime; falling back to signature scans only");
    } else {
        mwruntime::Log("addresses: loaded %s (%zu entries)",
                       mwruntime::g_versionDb.path().c_str(),
                       mwruntime::g_versionDb.count());
    }
    mwruntime::QueryInterfaces(skse);
    return true;
}

}  // extern "C"
