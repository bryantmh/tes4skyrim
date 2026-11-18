#include "gamebryo_sequence.h"

#include <cstddef>

#include "addresses.h"
#include "engine_ids.h"

namespace tesruntime::sequence {

namespace {

constexpr std::size_t kObjectController = 0x18;   // NiObjectNET::controllers
constexpr std::size_t kMapCapacity = 0x7c;        // NiControllerManager name map
constexpr std::size_t kMapSentinel = 0x88;
constexpr std::size_t kMapBuckets = 0x98;
constexpr std::size_t kEntrySize = 0x18;          // {key, sequence, next}
constexpr std::size_t kEntryValue = 8;
constexpr std::size_t kEntryNext = 0x10;
constexpr std::size_t kSeqCycle = 0x40;
constexpr std::size_t kSeqFrequency = 0x44;
constexpr std::size_t kSeqBeginKey = 0x48;
constexpr std::size_t kSeqEndKey = 0x4c;
constexpr std::size_t kSeqOwner = 0x60;
constexpr std::size_t kSeqState = 0x68;
constexpr std::size_t kSeqOffset = 0x6c;
constexpr std::size_t kSeqWeightedTime = 0x54;
constexpr std::size_t kManagerSequences = 0x50;    // NiControllerManager sequence array (IsAnimPlaying 0x357c22)
constexpr std::size_t kManagerSequenceCount = 0x5a;
constexpr std::size_t kControllerFlags = 0x10;     // NiTimeController::flags
constexpr std::uint16_t kControllerActive = 0x8;
constexpr int kPriority = 0;

using HashFn = void (*)(std::uint32_t* out, std::uint64_t key);
using ActivateFn = bool (*)(void* seq, int priority, bool startOver, float weight,
                            float easeIn, void* timeSync, bool);
using DeactivateFn = bool (*)(void* seq, float easeOut, bool transition);

HashFn       g_hash = nullptr;
ActivateFn   g_activate = nullptr;
DeactivateFn g_deactivate = nullptr;
void*        g_managerVtable = nullptr;

// What PlayGamebryoAnimation (1.6.1170 0xa2ead0) does once Activate returns:
// the manager is flagged active, or the frame update skips it.
void Activated(void* seq) {
    void* manager = At<void*>(seq, kSeqOwner);
    if (manager) At<std::uint16_t>(manager, kControllerFlags) |= kControllerActive;
}

}  // namespace

bool Install() {
    if (g_activate) return true;
    const std::uintptr_t hash = Resolve("BSFixedString hash", ids::kFixedStringHash, nullptr);
    const std::uintptr_t activate = Resolve("NiControllerSequence::Activate", ids::kSequenceActivate, nullptr);
    const std::uintptr_t deactivate = Resolve("NiControllerSequence::Deactivate", ids::kSequenceDeactivate, nullptr);
    const std::uintptr_t vtable = Resolve("NiControllerManager vtable", ids::kControllerManagerVtable, nullptr);
    if (!hash || !activate || !deactivate || !vtable) return false;
    g_hash = reinterpret_cast<HashFn>(hash);
    g_deactivate = reinterpret_cast<DeactivateFn>(deactivate);
    g_managerVtable = reinterpret_cast<void*>(vtable);
    g_activate = reinterpret_cast<ActivateFn>(activate);
    return true;
}

void* ManagerOf(void* node) {
    void* ctrl = (node && g_managerVtable) ? At<void*>(node, kObjectController) : nullptr;
    return (ctrl && At<void*>(ctrl, 0) == g_managerVtable) ? ctrl : nullptr;
}

void* Named(void* manager, void* name) {
    if (!manager || !name || !g_hash) return nullptr;
    char* buckets = At<char*>(manager, kMapBuckets);
    const std::uint32_t cap = At<std::uint32_t>(manager, kMapCapacity);
    if (!buckets || !cap) return nullptr;
    std::uint32_t hash = 0;
    g_hash(&hash, reinterpret_cast<std::uint64_t>(name));
    char* entry = buckets + static_cast<std::size_t>(hash & (cap - 1)) * kEntrySize;
    if (!At<void*>(entry, kEntryNext)) return nullptr;
    void* sentinel = At<void*>(manager, kMapSentinel);
    for (;;) {
        if (At<void*>(entry, 0) == name) return At<void*>(entry, kEntryValue);
        entry = At<char*>(entry, kEntryNext);
        if (!entry || entry == sentinel) return nullptr;
    }
}

void Play(void* seq) {
    if (!seq || !g_activate) return;
    g_deactivate(seq, 0.0f, false);
    if (g_activate(seq, kPriority, true, 1.0f, 0.0f, nullptr, false)) Activated(seq);
}

void Stop(void* seq) {
    if (seq && g_deactivate) g_deactivate(seq, 0.0f, false);
}

bool Active(void* seq) {
    return seq && At<std::uint32_t>(seq, kSeqState) != 0;
}

bool AnyPlaying(void* manager) {
    if (!manager) return false;
    void** seqs = At<void**>(manager, kManagerSequences);
    const std::uint16_t count = At<std::uint16_t>(manager, kManagerSequenceCount);
    for (std::uint16_t i = 0; seqs && i < count; ++i) {
        void* seq = seqs[i];
        if (!Active(seq)) continue;
        const bool done = Cycle(seq) == kCycleClamp && At<float>(seq, kSeqWeightedTime) >= At<float>(seq, kSeqEndKey);
        if (!done) return true;
    }
    return false;
}

void Resume(void* seq, float paused) {
    if (!seq || !g_activate || Active(seq)) return;
    if (!g_activate(seq, kPriority, false, 1.0f, 0.0f, nullptr, false)) return;
    Activated(seq);
    At<float>(seq, kSeqOffset) -= paused;
}

float Length(void* seq) {
    if (!seq) return 0.0f;
    const float span = At<float>(seq, kSeqEndKey) - At<float>(seq, kSeqBeginKey);
    const float frequency = At<float>(seq, kSeqFrequency);
    if (!(span > 0.0f) || !(frequency > 0.0f)) return 0.0f;
    return span / frequency;
}

std::uint32_t Cycle(void* seq) {
    return seq ? At<std::uint32_t>(seq, kSeqCycle) : kCycleClamp;
}

}  // namespace tesruntime::sequence
