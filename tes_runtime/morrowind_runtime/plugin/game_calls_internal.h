// What the game_calls_*.cpp files share: the resolvers every engine call goes
// through, and the few natives more than one of them needs.
// See: docs/commentary/morrowind_runtime.md#game-calls

#pragma once

#include <cstdint>
#include <string>

#include "activation.h"
#include "addresses.h"
#include "dialogue_state.h"
#include "script_tables.h"

namespace mwruntime {
namespace gamecalls {

using SetValueFn = void (*)(void* vm, std::uint32_t stack, void* actor,
                            void* name, float value);
// ObjectReference.PlaceAtMe(base, count, forcePersist, initiallyDisabled).
using PlaceAtMeFn = void* (*)(void* vm, std::uint32_t stack, void* self,
                              void* base, std::int32_t count,
                              bool forcePersist, bool initiallyDisabled);

extern SetValueFn  g_setValue;
extern PlaceAtMeFn g_placeAtMe;

std::string Lower(std::string text);
// Logs an id that does not resolve, once per id.
void ReportOnce(const char* what, const std::string& id);
void* Form(const FormRef* ref);
void* ItemForm(const std::string& item);
// The reference a TES3 id names: the player, the speaker, the instance
// running the current script, or a staged placement.
void* OwnerRef(const std::string& owner);
void* PlayerRef();
void* RefByRuntimeId(std::uint32_t runtimeFormId);
bool StartQuest(void* form, bool* justStarted);
// Puts `ref` at a position with a Z rotation in degrees. game_calls_move.cpp.
void PlaceAt(void* ref, float x, float y, float z, float zRot);

template <typename Fn>
Fn Native(const char* name, std::uint64_t id) {
    return reinterpret_cast<Fn>(Resolve(name, id, nullptr));
}

// Each file resolves its own natives and supplies its own hooks.
void InstallMoveCalls(GameHooks& hooks);
void InstallAiCalls(GameHooks& hooks);
void InstallQueryCalls(GameHooks& hooks);

}  // namespace gamecalls
}  // namespace mwruntime
