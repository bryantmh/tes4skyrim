// TESGameBridge -- SKSE plugin entry point.
//
// Opens a named pipe so the conversion pipeline can drive a RUNNING game:
// execute console commands, inspect what the engine actually built, and reload
// assets without a relaunch.
//
// Load order of operations:
//   SKSEPlugin_Load   -- grab interfaces, register for messages. No game state
//                        exists yet, so nothing is resolved here.
//   kMessage_DataLoaded -- forms exist and the task pump is live. Resolve
//                        addresses and start the pipe server.

#define _CRT_SECURE_NO_WARNINGS

#include <windows.h>

#include <type_traits>

#include "addresses.h"
#include "commands.h"
#include "game.h"
#include "log.h"
#include "main_thread.h"
#include "pipe_server.h"
#include "recorder.h"
#include "skse_abi.h"

namespace bridge {
extern std::uint32_t g_runtimeVersion;
extern bool          g_gameLoaded;
}  // namespace bridge

using namespace bridge;

namespace {

PluginHandle            g_pluginHandle = kPluginHandle_Invalid;
SKSEMessagingInterface* g_messaging = nullptr;
bool                    g_started = false;

void StartBridge() {
    if (g_started) return;

    if (!g_addr.Init(g_runtimeVersion)) {
        Log("bridge: core capability unavailable -- the plugin will load but "
            "console execution is disabled. Check the unresolved list above.");
        // Still start the server: `capabilities` must be answerable so the
        // client can report precisely what is missing instead of just timing out.
    }

    // Console output capture. Optional: a failure here leaves commands
    // runnable but unreadable, which is reported through `capabilities`
    // rather than blocking startup.
    InstallConsoleCapture(g_addr.consolePrint);
    // Without the console-mode flag, handlers never print, so the capture hook
    // above has nothing to capture. Discovery is a signature-consensus scan;
    // failure degrades to commands-run-but-unreadable, reported in hookstats.
    DiscoverConsoleModeFlag();
    // The console's compile+run dispatcher. ConsoleExecute alone only compiles,
    // so without this every command is a silent no-op that reports success.
    InitConsoleDispatch();
    InstallPapyrusCapture(g_addr.papyrusLog);
    // Watches the game's own console calls to learn the execution context that
    // ConsoleExecute's arg1 requires -- without it, commands compile but never
    // run. See game.h.
    InstallExecContextCapture(g_addr.compileAndRun);
    // The flight recorder: script events to a JSONL file while the game is
    // played. Independent of the pipe -- it records with no client attached.
    InstallRecorder();

    g_taskPumpLive = true;

    g_pipe.onDisconnect = [] { ReleaseSession(); };
    g_pipe.Start([](const std::string& line) { return HandleRequest(line); });

    g_started = true;
    Log("bridge: ready on %s", kPipeName);
}

void OnSKSEMessage(SKSEMessagingInterface::Message* msg) {
    if (!msg) return;
    switch (msg->type) {
        case SKSEMessagingInterface::kMessage_DataLoaded:
            Log("bridge: data loaded");
            StartBridge();
            break;
        case SKSEMessagingInterface::kMessage_PostLoadGame:
            g_gameLoaded = (msg->data != nullptr);
            Log("bridge: post-load game (loaded=%d)", g_gameLoaded ? 1 : 0);
            RecorderMessage(g_gameLoaded ? "loaded" : "load_failed", nullptr);
            break;
        case SKSEMessagingInterface::kMessage_NewGame:
            g_gameLoaded = true;
            Log("bridge: new game");
            RecorderMessage("new_game", nullptr);
            break;
        case SKSEMessagingInterface::kMessage_PreLoadGame:
            // A load screen is starting; main-thread tasks stop being pumped
            // promptly, so mark the game unloaded to fail fast rather than hang.
            g_gameLoaded = false;
            // SKSE passes the save's name here and with kMessage_SaveGame.
            RecorderMessage("loading", static_cast<const char*>(msg->data));
            break;
        case SKSEMessagingInterface::kMessage_SaveGame:
            RecorderMessage("saved", static_cast<const char*>(msg->data));
            break;
        default:
            break;
    }
}

}  // namespace

extern "C" {

// MUST be a static (constant) initializer, not a lambda or any other dynamic
// one. SKSE reads this struct with LOAD_LIBRARY_AS_IMAGE_RESOURCE, which maps
// the DLL as raw data and runs NO CRT initializers -- so it sees whatever bytes
// are in .data on disk. A dynamic initializer leaves those bytes zero, and
// SKSE's first check is `if (!dataVersion) return "disabled, bad version data"`.
// The failure is silent at build time and only appears in skse64.log.
//
// versionIndependence: we read game structures, so we are bound to the
// post-1.6.629 layout. Declaring it makes SKSE refuse to load us on older
// runtimes rather than reading the same offsets and getting different fields.
// compatibleVersions is left zeroed -- with AddressLibraryPostAE that means
// "any post-AE runtime with an Address Library", which is the intent.
__declspec(dllexport) SKSEPluginVersionData SKSEPlugin_Version = {
    SKSEPluginVersionData::kVersion,  // dataVersion
    kPluginVersion,                   // pluginVersion
    "TESGameBridge",                  // name[256]
    "TESConversion",                  // author[256]
    "",                               // supportEmail[252]
    0,                                // versionIndependenceEx
    SKSEPluginVersionData::kVersionIndependent_AddressLibraryPostAE |
        SKSEPluginVersionData::kVersionIndependent_StructsPost629,
    {0},                              // compatibleVersions
    0,                                // seVersionRequired
};

// Guards the rule above. If someone reintroduces a dynamic initializer, the
// struct stops being constant-initializable and this fails the BUILD rather
// than shipping a DLL that SKSE silently rejects at startup.
static_assert(std::is_trivially_copyable<SKSEPluginVersionData>::value,
              "SKSEPlugin_Version must stay a POD written straight into .data: "
              "SKSE reads it via LOAD_LIBRARY_AS_IMAGE_RESOURCE and runs no "
              "initializers.");

__declspec(dllexport) bool SKSEPlugin_Load(const SKSEInterface* skse) {
    OpenLog();
    Log("TESGameBridge %s loading (runtime %08X, SKSE %08X)",
        kPluginVersionString, skse->runtimeVersion, skse->skseVersion);

    g_runtimeVersion = skse->runtimeVersion;
    g_pluginHandle = skse->GetPluginHandle();

    g_task = static_cast<SKSETaskInterface*>(skse->QueryInterface(kInterface_Task));
    if (!g_task) {
        Log("bridge: FATAL -- no task interface; refusing to load. Without it "
            "every command would have to touch game state off-thread, which "
            "produces intermittent crashes that look like data bugs.");
        return false;
    }

    g_messaging = static_cast<SKSEMessagingInterface*>(skse->QueryInterface(kInterface_Messaging));
    if (!g_messaging) {
        Log("bridge: FATAL -- no messaging interface; refusing to load.");
        return false;
    }
    g_messaging->RegisterListener(g_pluginHandle, "SKSE", OnSKSEMessage);

    Log("bridge: waiting for data load");
    return true;
}

}  // extern "C"
