#include "anim_playing.h"

#include <cstring>

#include "addresses.h"
#include "engine.h"
#include "gamebryo_sequence.h"
#include "hook.h"
#include "ids.h"
#include "log.h"

namespace tesruntime {

namespace {

// The graph variable converted IsAnimPlaying calls ask for.
constexpr char kVariable[] = "TES4IsAnimPlaying";
// IAnimationGraphManagerHolder's place inside TESObjectREFR: GetAnimationVariableBool (1.6.1170 0xa2cdf0)
// calls [ref + 0x38] slot 0x90.
constexpr std::size_t kGraphHolder = 0x38;
constexpr std::size_t kSlotGetGraphVariableBool = 18;

// (holder, BSFixedString* name, bool* out): true when the variable was read.
using GetBoolFn = bool (*)(void* holder, const char* const* name, bool* out);
GetBoolFn g_original = nullptr;

bool Playing(void* ref) {
    void* root = VCall<void* (*)(void*)>(ref, kVtGet3D)(ref);
    return sequence::AnyPlaying(sequence::ManagerOf(root));
}

bool GetBoolHook(void* holder, const char* const* name, bool* out) {
    if (name && *name && _stricmp(*name, kVariable) == 0) {
        *out = Playing(static_cast<char*>(holder) - kGraphHolder);
        return true;
    }
    return g_original(holder, name, out);
}

}  // namespace

bool InstallAnimPlaying() {
    if (!sequence::Install()) return false;
    auto* vtable = reinterpret_cast<void**>(
        Resolve("TESObjectREFR IAnimationGraphManagerHolder vtable", ids::kRefGraphHolderVtable, nullptr));
    if (!vtable) return false;
    g_original = reinterpret_cast<GetBoolFn>(PatchVtableSlot(
        vtable, kSlotGetGraphVariableBool, reinterpret_cast<void*>(&GetBoolHook), "GetGraphVariableBool"));
    return g_original != nullptr;
}

}  // namespace tesruntime
