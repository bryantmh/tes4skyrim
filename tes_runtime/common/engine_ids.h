// Address Library stable IDs for the shared engine services (engine.cpp,
// glide.cpp).
//
// Derived like every other ids.h: located in the GOG/AE 1.6.659 build and
// inverted through versionlib-1-6-659-0.bin, then checked to exist in
// versionlib-1-6-1170-0.bin, the Steam build the user plays.

#pragma once

#include <cstddef>
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

// ---------------------------------------------------------------------------
// Moving a reference (glide.cpp)
// ---------------------------------------------------------------------------

// ObjectReference.GetPositionX/Y/Z (0x9ce600/610/620) and GetAngleX/Y/Z
// (0x9ce0c0/0e0/100), one instruction each: `movss xmm0,[r8+off]`, the angle
// getters then multiplying by 180/pi. They never touch rcx, so the VM pointer
// is irrelevant to them.
//
// 🛑 The ANGLE getters return DEGREES while the field holds radians, and
// SetAngle takes degrees back -- so a get/set round trip needs no conversion,
// but reading the field directly does.
constexpr std::uint64_t kRefGetPositionX = 56178;
constexpr std::uint64_t kRefGetPositionY = 56179;
constexpr std::uint64_t kRefGetPositionZ = 56180;

// 🛑 THE ANGLE GETTERS HAVE NO STABLE ID ON A CURRENT BUILD. Ids 56162-56164
// exist on 1.6.659 and are GONE on 1.6.1170 -- measured against both
// versionlibs, and the live log showed all three UNRESOLVED, which silently
// broke every rotation (Rotate, RotateWorld, PositionCell's zRot, Face).
// Each is a 3-instruction leaf the Address Library stopped covering, so the
// field is read directly instead: rotation x/y/z are floats at these offsets
// on TESObjectREFR, immediately before the position triple at +0x54.
// See: docs/commentary/morrowind_runtime.md#the-angle-getters-have-no-id
constexpr std::size_t kOffRefRotX = 0x48;
constexpr std::size_t kOffRefRotY = 0x4c;
constexpr std::size_t kOffRefRotZ = 0x50;

// ObjectReference.SetPosition(float x, y, z) (0x9d1c60) and SetAngle (0x9d12d0)
// take ALL THREE axes, so a one-axis `SetPos` reads the other two back first.
// Unlike the getters these DO use rcx, to report "Cannot move the player
// because they are dead", so they need the real VM.
constexpr std::uint64_t kRefSetPosition = 56234;
constexpr std::uint64_t kRefSetAngle = 56224;

// ObjectReference.TranslateTo(x, y, z, ax, ay, az, speed, maxRotSpeed)
// (0x9d1f70, the latent native registered beside the "TranslateTo" string at
// 0x9d760d). Angles in degrees; it glides the loaded 3D without reloading it.
constexpr std::uint64_t kRefTranslateTo = 56237;

// ---------------------------------------------------------------------------
// Event sinks
// ---------------------------------------------------------------------------

// BSTEventSource<T>::AddEventSink(this, sink) (0x5dc8c0 on 1.6.1170), the one
// copy every event type shares and the RVA SKSE 2.2.6 hardcodes as
// EventDispatcher::AddEventSink_Internal. Verified by disassembly: it takes
// the lock at +0x48, reads the "dispatching" flag at +0x50, searches the sink
// array at +0 (or the add buffer at +0x18 while dispatching) and appends.
constexpr std::uint64_t kAddEventSink = 35182;

}  // namespace tesruntime::ids
