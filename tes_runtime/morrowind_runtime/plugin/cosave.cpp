#include "cosave.h"

#include <string>

#include "dialogue_state.h"
#include "journal_objectives.h"
#include "log.h"
#include "object_script.h"
#include "object_tick.h"

namespace mwruntime {

namespace {

// 'MWST', and the version of the record's framing -- the state's own text
// carries its own format line inside.
constexpr UInt32 kRecordState = 'MWST';
constexpr UInt32 kRecordVersion = 1;

// 'MWJL': which journal text each quest objective was shown with.
// See: docs/commentary/morrowind_runtime.md#journal-stage-text
constexpr UInt32 kRecordJournal = 'MWJL';
constexpr UInt32 kJournalVersion = 1;

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
    const bool journal = WriteRecord(intfc, kRecordJournal, kJournalVersion,
                                     Journal().Serialize());
    Log("cosave: saved %zu journal record(s) -- %s", Journal().size(),
        journal ? "ok" : "WRITE FAILED");
}

void LoadJournal(SKSESerializationInterface* intfc, UInt32 length) {
    const std::size_t kept = Journal().Deserialize(
        ReadRecord(intfc, length),
        [intfc](std::uint32_t saved, std::uint32_t* now) {
            return intfc->ResolveFormId(saved, now);
        });
    Log("cosave: loaded %zu journal record(s)", kept);
}

void OnLoad(SKSESerializationInterface* intfc) {
    UInt32 type = 0, version = 0, length = 0;
    bool found = false;
    while (intfc->GetNextRecordInfo(&type, &version, &length)) {
        if (type == kRecordJournal && version <= kJournalVersion) {
            LoadJournal(intfc, length);
            continue;
        }
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
    Journal().Reset();
    ResetJournalPoll();
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

}  // namespace mwruntime
