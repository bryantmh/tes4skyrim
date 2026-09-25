// TESRuntime -- SKSE plugin entry point for what every converted game needs.
//
//   * points every converted crime faction at the jail nearest the player
//     (crime.cpp, docs/commentary/tes_runtime_crime.md#nearest-jail);
//   * gives a clicked quest objective the journal text it was shown with
//     (journal_objectives.cpp,
//     docs/commentary/tes_runtime_journal.md#journal-stage-text).

#include <windows.h>

#include <type_traits>

#include "addresses.h"
#include "crime.h"
#include "engine.h"
#include "journal_objectives.h"
#include "log.h"
#include "paths.h"
#include "skse_abi.h"

using namespace tesruntime;

namespace {

constexpr UInt32 kPluginVersion = 3;
constexpr UInt32 kSerializationId = 'TES4';

bool g_crimeInstalled = false;
bool g_journalInstalled = false;

bool CaptureVm(void* vm) {
    g_api.vm = vm;
    Log("papyrus: VM %p", vm);
    return true;
}

void OnMessage(SKSEMessagingInterface::Message* msg) {
    if (!msg) return;
    if (msg->type == SKSEMessagingInterface::kMessage_NewGame) {
        JournalRevert(nullptr);
    } else if (msg->type == SKSEMessagingInterface::kMessage_DataLoaded) {
        if (g_crimeInstalled) {
            ResolveCrimeForms();
            StartCrimeTick();
        }
        if (g_journalInstalled) StartJournalTick();
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
        ser->SetSaveCallback(handle, JournalSave);
        ser->SetLoadCallback(handle, JournalLoad);
        ser->SetRevertCallback(handle, JournalRevert);
    }
    Log("interfaces: messaging %s, papyrus %s, task %s, serialization %s",
        msg ? "ok" : "missing", papyrus ? "ok" : "missing",
        g_api.task ? "ok" : "missing", ser ? "ok" : "missing");
}

}  // namespace

extern "C" {

// MUST stay a static aggregate: SKSE reads it with
// LOAD_LIBRARY_AS_IMAGE_RESOURCE and runs no initializers
// (project_skse_version_data_static_init).
__declspec(dllexport) SKSEPluginVersionData SKSEPlugin_Version = {
    SKSEPluginVersionData::kVersion,  // dataVersion
    kPluginVersion,                   // pluginVersion
    "TESRuntime",                     // name[256]
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
    Log("TESRuntime %u loading (runtime %08X, SKSE %08X)", kPluginVersion,
        skse->runtimeVersion, skse->skseVersion);
    if (!g_versionDb.Load(skse->runtimeVersion)) {
        Log("addresses: no Address Library database for this runtime; "
            "falling back to signature scans only");
    } else {
        Log("addresses: loaded %s (%zu entries)", g_versionDb.path().c_str(),
            g_versionDb.count());
    }
    QueryInterfaces(skse);
    g_crimeInstalled = ResolveEngine() && LoadCrimeSidecars();
    g_journalInstalled = InstallJournal();
    Log("hooks: jails %s, journal stage text %s",
        g_crimeInstalled ? "installed" : "NOT installed",
        g_journalInstalled ? "installed" : "NOT installed");
    return true;
}

}  // extern "C"
