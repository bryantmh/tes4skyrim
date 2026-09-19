#include "cosave.h"

#include <string>

#include "dialogue_state.h"
#include "log.h"

namespace mwruntime {

namespace {

// 'MWST', and the version of the record's framing -- the state's own text
// carries its own format line inside.
constexpr UInt32 kRecordState = 'MWST';
constexpr UInt32 kRecordVersion = 1;

void OnSave(SKSESerializationInterface* intfc) {
    const std::string text = State().Serialize();
    const bool ok = intfc->OpenRecord(kRecordState, kRecordVersion) &&
                    intfc->WriteRecordData(text.data(),
                                           static_cast<UInt32>(text.size()));
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
        std::string text(length, '\0');
        const UInt32 read = length ? intfc->ReadRecordData(text.data(), length)
                                   : 0;
        text.resize(read);
        const std::size_t taken = State().Deserialize(text);
        Log("cosave: loaded %u byte(s), %zu record(s) of state", read, taken);
        found = true;
    }
    if (!found) {
        State().Reset();
        Log("cosave: this save carries no Morrowind state -- starting clean");
    }
    State().StartStartupScripts();
}

// A new game or a load about to happen: nothing from the last game survives.
void OnRevert(SKSESerializationInterface*) {
    State().Reset();
    State().StartStartupScripts();
    Log("cosave: state reverted");
}

}  // namespace

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

}  // namespace mwruntime
