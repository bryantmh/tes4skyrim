// The engine services more than one plugin uses, resolved once.
//
// Every entry point is an Address Library ID (engine_ids.h); nothing here is a
// raw RVA. Papyrus natives are called the way the VM calls them: the VM pointer
// the SKSE Papyrus interface hands over, a stack id of 0, then the script
// arguments. Forms are resolved by (plugin-local FormID, file name) through
// the engine's own load order (the Game.GetFormFromFile native), so the
// sidecars never assume a load position.

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

#include "addresses.h"
#include "skse_abi.h"

namespace tesruntime {

// TESForm layout the plugins read.
constexpr std::size_t kFormID = 0x14;
constexpr std::size_t kFormType = 0x1a;
constexpr std::uint8_t kFormTypeWeapon = 0x29;
constexpr std::uint8_t kFormTypeActor = 0x3e;

// TESObjectREFR / Actor layout the plugins read.
constexpr std::size_t kRefCount = 0x28;        // BSHandleRefObject, low 10 bits
constexpr std::size_t kActorValueOwner = 0xb8;
constexpr std::size_t kVtGet3D = 0x70;             // Get3D(): the 3D of the current camera
constexpr std::size_t kVtGet3DFirstPerson = 0x6f;  // Get3D(bool firstPerson)
constexpr std::size_t kVtGetObjectByName = 0x2a;
constexpr int kActorValueHealth = 0x18;

// NiAVObject / NiNode layout.
constexpr std::size_t kVtAsNode = 3;
constexpr std::size_t kVtAsGeometry = 9;
constexpr std::size_t kNodeChildren = 0x118;
constexpr std::size_t kNodeChildCount = 0x122;
constexpr std::size_t kWorldTranslate = 0xa0;

using FixedStringCtorFn = void* (*)(void* self, const char* text);
using FixedStringDtorFn = void (*)(void* self);
using GetFormFromFileFn = void* (*)(void* vm, std::uint32_t stack, void* tag,
                                    std::int32_t formID, void* fileName);
using LookupFormFn = void* (*)(std::uint32_t formID);

// A BSFixedString built and torn down by the engine's own constructor and
// destructor, so the interned pool is never touched by hand.
struct FixedString {
    void* ptr = nullptr;
    explicit FixedString(const char* text);
    ~FixedString();
    FixedString(const FixedString&) = delete;
    FixedString& operator=(const FixedString&) = delete;
};

struct EngineApi {
    FixedStringCtorFn fixedStringCtor = nullptr;
    FixedStringDtorFn fixedStringDtor = nullptr;
    GetFormFromFileFn getFormFromFile = nullptr;
    LookupFormFn      lookupForm = nullptr;
    void*             vm = nullptr;
    SKSETaskInterface* task = nullptr;
};

extern EngineApi g_api;

// Resolves the shared entry points; false when the fixed-string pair or the
// form lookup is missing, in which case nothing that needs forms installs.
bool ResolveEngine();

// The form (`local`, `file`) names in the running load order, or null.
void* FormFromFile(std::uint32_t local, const std::string& file);

// A ref's world translate as three floats (false when it has no 3D).
bool RefPosition(void* ref, float* out);

// Every *.<suffix> sidecar under SidecarDir(), parsed (unreadable ones are
// logged and skipped). `suffix` is e.g. "guns.json".
void ForEachSidecar(const char* suffix,
                    void (*visit)(const std::string& name, const class Json& doc));

// The same, for any folder (trailing separator required). With `unlessIn`, a
// file that folder also holds is skipped and each one read is logged as
// deprecated.
void ForEachSidecarIn(const std::string& dir, const char* suffix,
                      void (*visit)(const std::string& name, const class Json& doc),
                      const std::string& unlessIn = "");

// DEPRECATED, to be removed: the same, under Data\SKSE\Plugins\<folder>\, where
// a build from before the runtime split put it. A file the current folder
// also holds is skipped; each one read is logged as deprecated.
// See: docs/reference/tes_runtime_fragments.md#legacy-sidecar-paths
void ForEachLegacySidecar(const char* folder, const char* suffix,
                          void (*visit)(const std::string& name, const class Json& doc));

// Releases a reference LookupReferenceByHandle handed out.
void ReleaseRef(void* ref);

// Drops a heap TaskDelegate onto the game's main-thread task queue.
void RunOnMainThread(TaskDelegate* task);

// Runs `fn` on the game's main thread every `ms` milliseconds, paused or not.
// The wait sleeps on its own thread and only the call is posted; a call still
// queued is not posted again, so a stalled task pump holds one call rather
// than a backlog. False, and nothing started, without a task interface.
bool StartMainThreadTick(int ms, void (*fn)());

}  // namespace tesruntime
