// Address Library stable IDs for the shared engine services (engine.cpp).
//
// Derived like every other ids.h: located in the GOG/AE 1.6.659 build and
// inverted through versionlib-1-6-659-0.bin, then checked to exist in
// versionlib-1-6-1170-0.bin, the Steam build the user plays.

#pragma once

#include <cstdint>

namespace tesruntime::ids {

// BSFixedString::BSFixedString(const char*) (0xc60ac0) / ~BSFixedString (0xc60c30).
constexpr std::uint64_t kFixedStringCtor = 69161;
constexpr std::uint64_t kFixedStringDtor = 69164;

// The Game.GetFormFromFile Papyrus native (0x9adb30):
//   TESForm* (VM*, uint32 stack, void* tag, int32 formID, const BSFixedString& file)
// resolves a plugin-local id through the running load order.
constexpr std::uint64_t kGetFormFromFile = 55465;

// TESForm* LookupFormByID(uint32) (0x1a0b70), the body of Game.GetForm.
constexpr std::uint64_t kLookupFormByID = 14617;

// UIManager::AddMessage(this, BSFixedString* menu, u32 msgId, void* data)
// (0x170730). Identified by its pool arithmetic: [rcx+0x378] is poolUsed,
// compared against 0x40 = kPoolSize, and (poolUsed + 0x1c) << 5 lands on
// messagePool at 0x380 with stride 32. Inverts to the RVA SKSE hardcodes.
constexpr std::uint64_t kUIAddMessage = 13631;

// The UIManager singleton POINTER (0x20f8950 on 1.6.1170), read, never called.
constexpr std::uint64_t kUIManagerSingleton = 400445;

}  // namespace tesruntime::ids
