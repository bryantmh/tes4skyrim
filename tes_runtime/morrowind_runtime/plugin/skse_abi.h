// Minimal SKSE plugin ABI.
//
// Deliberately standalone: we do NOT compile against the SKSE source tree.
// SKSE's own headers hardcode 1.6.x RVAs via RelocAddr, which ties the build to
// one runtime and silently reads garbage on any other. Everything this plugin
// needs from the game is resolved at runtime instead (versionlib stable IDs,
// falling back to signature scans), so the DLL keeps working across updates.
//
// Only the pieces of the ABI we actually use are declared here.

#pragma once

#include <cstdint>

using UInt32 = std::uint32_t;
using UInt64 = std::uint64_t;
using PluginHandle = UInt32;

enum { kPluginHandle_Invalid = 0xFFFFFFFF };

enum {
    kInterface_Invalid = 0,
    kInterface_Scaleform,
    kInterface_Papyrus,
    kInterface_Serialization,
    kInterface_Task,
    kInterface_Messaging,
    kInterface_Object,
    kInterface_Trampoline,
    kInterface_Max,
};

struct PluginInfo {
    enum { kInfoVersion = 1 };
    UInt32       infoVersion;
    const char*  name;
    UInt32       version;
};

struct SKSEInterface {
    UInt32 skseVersion;
    UInt32 runtimeVersion;
    UInt32 editorVersion;
    UInt32 isEditor;
    void*        (*QueryInterface)(UInt32 id);
    PluginHandle (*GetPluginHandle)(void);
    UInt32       (*GetReleaseIndex)(void);
    const PluginInfo* (*GetPluginInfo)(const char* name);
};

// Derive, allocate, AddTask; the game deletes via Dispose() on the main thread.
class TaskDelegate {
public:
    virtual void Run() = 0;
    virtual void Dispose() = 0;
};

class UIDelegate_v1;

struct SKSETaskInterface {
    enum { kInterfaceVersion = 2 };
    UInt32 interfaceVersion;
    void (*AddTask)(TaskDelegate* task);
    void (*AddUITask)(UIDelegate_v1* task);
};

struct SKSEMessagingInterface {
    struct Message {
        const char* sender;
        UInt32      type;
        UInt32      dataLen;
        void*       data;
    };
    typedef void (*EventCallback)(Message* msg);

    enum { kInterfaceVersion = 2 };
    enum {
        kMessage_PostLoad,
        kMessage_PostPostLoad,
        kMessage_PreLoadGame,
        kMessage_PostLoadGame,
        kMessage_SaveGame,
        kMessage_DeleteGame,
        kMessage_InputLoaded,
        kMessage_NewGame,
        kMessage_DataLoaded,
    };

    UInt32 interfaceVersion;
    bool (*RegisterListener)(PluginHandle listener, const char* sender, EventCallback handler);
    bool (*Dispatch)(PluginHandle sender, UInt32 messageType, void* data, UInt32 dataLen, const char* receiver);
    void* (*GetEventDispatcher)(UInt32 dispatcherId);
};

// The Papyrus VM handed to the registration callback; the natives the plugin
// calls take it as their first argument.
struct SKSEPapyrusInterface {
    enum { kInterfaceVersion = 1 };
    typedef bool (*RegisterFunctions)(void* vm);
    UInt32 interfaceVersion;
    bool (*Register)(RegisterFunctions callback);
};

struct SKSESerializationInterface {
    enum { kVersion = 4 };
    typedef void (*EventCallback)(SKSESerializationInterface* intfc);
    typedef void (*FormDeleteCallback)(UInt64 handle);

    UInt32 version;
    void (*SetUniqueID)(PluginHandle plugin, UInt32 uid);
    void (*SetRevertCallback)(PluginHandle plugin, EventCallback callback);
    void (*SetSaveCallback)(PluginHandle plugin, EventCallback callback);
    void (*SetLoadCallback)(PluginHandle plugin, EventCallback callback);
    void (*SetFormDeleteCallback)(PluginHandle plugin, FormDeleteCallback callback);
    bool (*WriteRecord)(UInt32 type, UInt32 version, const void* buf, UInt32 length);
    bool (*OpenRecord)(UInt32 type, UInt32 version);
    bool (*WriteRecordData)(const void* buf, UInt32 length);
    bool (*GetNextRecordInfo)(UInt32* type, UInt32* version, UInt32* length);
    UInt32 (*ReadRecordData)(void* buf, UInt32 length);
    bool (*ResolveHandle)(UInt64 handle, UInt64* handleOut);
    bool (*ResolveFormId)(UInt32 formId, UInt32* formIdOut);
};

struct SKSEPluginVersionData {
    enum { kVersion = 1 };
    enum {
        kVersionIndependent_AddressLibraryPostAE = 1 << 0,
        kVersionIndependent_Signatures           = 1 << 1,
        kVersionIndependent_StructsPost629       = 1 << 2,
    };
    enum { kVersionIndependentEx_NoStructUse = 1 << 0 };

    UInt32 dataVersion;
    UInt32 pluginVersion;
    char   name[256];
    char   author[256];
    char   supportEmail[252];
    UInt32 versionIndependenceEx;
    UInt32 versionIndependence;
    UInt32 compatibleVersions[16];
    UInt32 seVersionRequired;
};
