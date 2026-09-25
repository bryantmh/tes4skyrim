#include "parts.h"

#include <cstdint>
#include <memory>

#include "addresses.h"
#include "engine.h"
#include "ids.h"
#include "log.h"

namespace tesruntime {

namespace {

// The layouts ObjectReference.PlayGamebryoAnimation (0x9cfdd0) reads.
constexpr std::size_t kObjectController = 0x18;         // NiObjectNET::controllers
constexpr std::size_t kMapCapacity = 0x7c;              // NiControllerManager name map
constexpr std::size_t kMapSentinel = 0x88;
constexpr std::size_t kMapBuckets = 0x98;
constexpr std::size_t kEntrySize = 0x18;                // {key, sequence, next}
constexpr std::size_t kEntryValue = 8;
constexpr std::size_t kEntryNext = 0x10;
constexpr int kSequencePriority = 0;

using HashFn = void (*)(std::uint32_t* out, std::uint64_t key);
using ActivateFn = bool (*)(void* seq, int priority, bool startOver, float weight,
                            float easeIn, void* timeSync, bool);
using DeactivateFn = bool (*)(void* seq, float easeOut, bool transition);
using ObjectByNameFn = void* (*)(void* root, void** name, bool recurse);

HashFn         g_hash = nullptr;
ActivateFn     g_activate = nullptr;
DeactivateFn   g_deactivate = nullptr;
ObjectByNameFn g_objectByName = nullptr;
void*          g_managerVtable = nullptr;
std::unique_ptr<FixedString> g_weaponNode;

void* ManagerOf(void* node) {
    void* ctrl = node ? At<void*>(node, kObjectController) : nullptr;
    return (ctrl && At<void*>(ctrl, 0) == g_managerVtable) ? ctrl : nullptr;
}

// The manager's sequence named by the interned string, or null.
void* SequenceOf(void* manager, void* tag) {
    char* buckets = At<char*>(manager, kMapBuckets);
    const std::uint32_t cap = At<std::uint32_t>(manager, kMapCapacity);
    if (!buckets || !cap) return nullptr;
    std::uint32_t hash = 0;
    g_hash(&hash, reinterpret_cast<std::uint64_t>(tag));
    char* entry = buckets + static_cast<std::size_t>(hash & (cap - 1)) * kEntrySize;
    if (!At<void*>(entry, kEntryNext)) return nullptr;
    void* sentinel = At<void*>(manager, kMapSentinel);
    for (;;) {
        if (At<void*>(entry, 0) == tag) return At<void*>(entry, kEntryValue);
        entry = At<char*>(entry, kEntryNext);
        if (!entry || entry == sentinel) return nullptr;
    }
}

// Starts `tag` on every managed NiNode under the WEAPON bone (the attached
// weapon's root and, one level down, its parts); geometry is skipped.
int PlayUnder(void* object, void* tag, int depth) {
    void* node = object ? VCall<void* (*)(void*)>(object, kVtAsNode)(object) : nullptr;
    if (!node || depth > 2) return 0;
    int n = 0;
    if (void* mgr = ManagerOf(node)) {
        if (void* seq = SequenceOf(mgr, tag)) {
            g_deactivate(seq, 0.0f, false);
            g_activate(seq, kSequencePriority, true, 1.0f, 0.0f, nullptr, false);
            ++n;
        }
    }
    void* kids = At<void*>(node, kNodeChildren);
    const std::uint16_t count = At<std::uint16_t>(node, kNodeChildCount);
    for (std::uint16_t i = 0; kids && i < count; ++i) {
        n += PlayUnder(At<void*>(kids, i * sizeof(void*)), tag, depth + 1);
    }
    return n;
}

}  // namespace

bool InstallParts() {
    const std::uintptr_t hash = Resolve("BSFixedString hash", ids::kFixedStringHash, nullptr);
    const std::uintptr_t activate = Resolve("NiControllerSequence::Activate", ids::kSequenceActivate, nullptr);
    const std::uintptr_t deactivate = Resolve("NiControllerSequence::Deactivate", ids::kSequenceDeactivate, nullptr);
    const std::uintptr_t vtable = Resolve("NiControllerManager vtable", ids::kControllerManagerVtable, nullptr);
    const std::uintptr_t byName = Resolve("NiAVObject::GetObjectByName", ids::kObjectByName, nullptr);
    if (!hash || !activate || !deactivate || !vtable || !byName) return false;
    g_hash = reinterpret_cast<HashFn>(hash);
    g_activate = reinterpret_cast<ActivateFn>(activate);
    g_deactivate = reinterpret_cast<DeactivateFn>(deactivate);
    g_managerVtable = reinterpret_cast<void*>(vtable);
    g_objectByName = reinterpret_cast<ObjectByNameFn>(byName);
    g_weaponNode.reset(new FixedString("WEAPON"));
    return true;
}

int PlayPartSequence(void* actor, void* tag) {
    if (!g_activate || !actor || !tag) return 0;
    void* third = VCall<void* (*)(void*)>(actor, kVtGet3D)(actor);
    void* first = VCall<void* (*)(void*, bool)>(actor, kVtGet3DFirstPerson)(actor, true);
    int n = 0;
    if (third) n += PlayUnder(g_objectByName(third, &g_weaponNode->ptr, true), tag, 0);
    if (first && first != third) n += PlayUnder(g_objectByName(first, &g_weaponNode->ptr, true), tag, 0);
    return n;
}

}  // namespace tesruntime
