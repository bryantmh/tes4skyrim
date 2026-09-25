// Which journal text each quest objective was shown under.
//
// The engine keeps ONE (stage, log entry) pair per quest instance -- the text
// the journal's description shows -- and overwrites it at every stage with a
// log entry. This store remembers the pair that was current when each
// objective first appeared, so a clicked objective can show the text it came
// with. Pure data: no game memory, so the headless tests cover it.
// See: docs/commentary/tes_runtime_journal.md#journal-stage-text

#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <string>
#include <tuple>
#include <vector>

namespace tesruntime {

// One objective of one quest instance. `questFormId` is the RUNTIME FormID;
// the cosave stores it and re-resolves it on load.
struct ObjectiveKey {
    std::uint32_t questFormId = 0;
    std::uint16_t objective = 0;
    std::uint32_t instance = 0;

    bool operator<(const ObjectiveKey& o) const {
        return std::tie(questFormId, objective, instance) <
               std::tie(o.questFormId, o.objective, o.instance);
    }
};

// The engine's own pointer to a journal text: a stage index and which of that
// stage's log entries.
struct StagePointer {
    std::uint16_t stage = 0;
    std::uint8_t logEntry = 0;
};

// One entry of the player's objective array, reduced to what the journal's
// row rules read. `quest` is an identity only, never dereferenced here.
struct ShownObjective {
    const void* quest = nullptr;
    std::uint32_t instance = 0;
    std::uint32_t state = 0;
    std::uint16_t objective = 0;
    bool questIsMisc = false;
    bool orWithNext = false;
};

class JournalLog {
public:
    void Record(const ObjectiveKey& key, StagePointer pointer);
    const StagePointer* Find(const ObjectiveKey& key) const;
    std::size_t size() const { return records_.size(); }
    void Reset() { records_.clear(); }

    // Binary cosave body: u32 count, then per record u32 form, u16 objective,
    // u32 instance, u16 stage, u8 log entry.
    std::string Serialize() const;
    // Replaces the store. `resolve` maps a saved FormID to this session's, and
    // returns false for a form whose plugin is gone, which drops the record.
    // Returns how many records were kept.
    std::size_t Deserialize(
        const std::string& body,
        const std::function<bool(std::uint32_t, std::uint32_t*)>& resolve);

private:
    std::map<ObjectiveKey, StagePointer> records_;
};

// The objective behind row `row` of the journal's list for (`quest`,
// `instance`), from the player's array in its stored order (oldest first).
// Follows the journal's own rules: newest first, only shown states, a
// miscellaneous quest shows only its active objectives, and an objective
// flagged OR joins the next row instead of taking one. Null past the end.
const ShownObjective* ObjectiveAtRow(const std::vector<ShownObjective>& shown,
                                     const void* quest, std::uint32_t instance,
                                     std::size_t row);

}  // namespace tesruntime
