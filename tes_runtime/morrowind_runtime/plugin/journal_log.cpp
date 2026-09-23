#include "journal_log.h"

#include <cstring>

namespace mwruntime {

namespace {

constexpr std::size_t kRecordBytes = 4 + 2 + 4 + 2 + 1;

template <typename T>
void Put(std::string& out, T value) {
    char bytes[sizeof(T)];
    std::memcpy(bytes, &value, sizeof(T));
    out.append(bytes, sizeof(T));
}

template <typename T>
T Take(const char*& at) {
    T value;
    std::memcpy(&value, at, sizeof(T));
    at += sizeof(T);
    return value;
}

// The journal lists states 1, 3 and 5 -- the three "shown" variants.
bool ShownState(std::uint32_t state) {
    return ((state - 1) & ~6u) == 0 && state != 7;
}

bool OnRow(const ShownObjective& entry, const void* quest,
           std::uint32_t instance) {
    if (!ShownState(entry.state)) return false;
    if (entry.questIsMisc && entry.state - 2 <= 3) return false;
    return entry.quest == quest && entry.instance == instance &&
           !entry.orWithNext;
}

}  // namespace

void JournalLog::Record(const ObjectiveKey& key, StagePointer pointer) {
    records_[key] = pointer;
}

const StagePointer* JournalLog::Find(const ObjectiveKey& key) const {
    const auto found = records_.find(key);
    return found == records_.end() ? nullptr : &found->second;
}

std::string JournalLog::Serialize() const {
    std::string out;
    out.reserve(4 + records_.size() * kRecordBytes);
    Put<std::uint32_t>(out, static_cast<std::uint32_t>(records_.size()));
    for (const auto& [key, pointer] : records_) {
        Put(out, key.questFormId);
        Put(out, key.objective);
        Put(out, key.instance);
        Put(out, pointer.stage);
        Put(out, pointer.logEntry);
    }
    return out;
}

std::size_t JournalLog::Deserialize(
    const std::string& body,
    const std::function<bool(std::uint32_t, std::uint32_t*)>& resolve) {
    records_.clear();
    if (body.size() < 4) return 0;
    const char* at = body.data();
    const std::uint32_t count = Take<std::uint32_t>(at);
    if (body.size() < 4 + std::size_t{count} * kRecordBytes) return 0;
    for (std::uint32_t i = 0; i < count; ++i) {
        ObjectiveKey key;
        key.questFormId = Take<std::uint32_t>(at);
        key.objective = Take<std::uint16_t>(at);
        key.instance = Take<std::uint32_t>(at);
        StagePointer pointer;
        pointer.stage = Take<std::uint16_t>(at);
        pointer.logEntry = Take<std::uint8_t>(at);
        if (resolve(key.questFormId, &key.questFormId)) {
            records_[key] = pointer;
        }
    }
    return records_.size();
}

const ShownObjective* ObjectiveAtRow(const std::vector<ShownObjective>& shown,
                                     const void* quest, std::uint32_t instance,
                                     std::size_t row) {
    std::size_t at = 0;
    for (auto it = shown.rbegin(); it != shown.rend(); ++it) {
        if (!OnRow(*it, quest, instance)) continue;
        if (at++ == row) return &*it;
    }
    return nullptr;
}

}  // namespace mwruntime
