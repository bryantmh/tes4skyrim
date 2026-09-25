#include "cosave.h"

#include <string>

#include "dialogue_state.h"
#include "log.h"
#include "object_script.h"
#include "object_tick.h"

namespace tesruntime::mw {

namespace {

// 'MWST', and the version of the record's framing -- the state's own text
// carries its own format line inside.
constexpr UInt32 kRecordState = 'MWST';
constexpr UInt32 kRecordVersion = 1;

bool WriteRecord(SKSESerializationInterface* intfc, UInt32 type,
                 UInt32 version, const std::string& body) {
    return intfc->OpenRecord(type, version) &&
           intfc->WriteRecordData(body.data(), static_cast<UInt32>(body.size()));
}

std::string ReadRecord(SKSESerializationInterface* intfc, UInt32 length) {
    std::string body(length, '\0');
    const UInt32 read = length ? intfc->ReadRecordData(body.data(), length) : 0;
    body.resize(read);
    return body;
}

void OnSave(SKSESerializationInterface* intfc) {
    const std::string text = State().Serialize();
    const bool ok = WriteRecord(intfc, kRecordState, kRecordVersion, text);
    Log("cosave: saved %zu byte(s) of state -- %s", text.size(),
        ok ? "ok" : "WRITE FAILED");
}

void OnLoad(SKSESerializationInterface* intfc) {
    UInt32 type = 0, version = 0, length = 0;
    bool found = false;
    while (intfc->GetNextRecordInfo(&type, &version, &length)) {
        if (type != kRecordState || version > kRecordVersion) {
            Log("cosave: skipped record %08X v%u (%u bytes)", type, version,
                length);
            continue;
        }
        const std::string text = ReadRecord(intfc, length);
        const std::size_t taken = State().Deserialize(text);
        Log("cosave: loaded %zu byte(s), %zu record(s) of state", text.size(),
            taken);
        found = true;
    }
    if (!found) {
        State().Reset();
        Log("cosave: this save carries no Morrowind state -- starting clean");
    }
    State().StartStartupScripts();
}

// A new game or a load about to happen: nothing from the last game survives.
//
// 🛑 The INSTANCES go too, not just the state. They hold per-life flags -- the
// latched `OnDeath`, whether the reference has been seen loaded -- and a
// binding to a FormID from the session being torn down. Keeping them meant a
// creature killed in one save could never raise `OnDeath` again in another,
// which softlocked any quest that turns on killing it.
// See: docs/commentary/morrowind_runtime.md#a-load-resets-the-instances
void OnRevert(SKSESerializationInterface*) { RevertState(); }

}  // namespace

void RevertState() {
    State().Reset();
    ClearInstances();
    ResetTickState();
    State().StartStartupScripts();
    Log("cosave: state reverted");
}

void InstallCoSave(SKSESerializationInterface* serialization,
                   PluginHandle plugin) {
    if (!serialization) {
        Log("cosave: NOT installed -- no serialization interface, so journal "
            "and disposition will not survive a save");
        return;
    }
    serialization->SetRevertCallback(plugin, OnRevert);
    serialization->SetSaveCallback(plugin, OnSave);
    serialization->SetLoadCallback(plugin, OnLoad);
    Log("cosave: installed");
}

}  // namespace tesruntime::mw
