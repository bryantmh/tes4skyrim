// What the game_calls_*.cpp files share: the resolvers every engine call goes
// through, and the few natives more than one of them needs.
// See: docs/commentary/morrowind_runtime.md#game-calls

#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "activation.h"
#include "addresses.h"
#include "dialogue_state.h"
#include "scope.h"
#include "script_tables.h"

namespace tesruntime::mw {
namespace gamecalls {

using SetValueFn = void (*)(void* vm, std::uint32_t stack, void* actor,
                            void* name, float value);
// ObjectReference.PlaceAtMe(base, count, forcePersist, initiallyDisabled).
using PlaceAtMeFn = void* (*)(void* vm, std::uint32_t stack, void* self,
                              void* base, std::int32_t count,
                              bool forcePersist, bool initiallyDisabled);

extern SetValueFn  g_setValue;
extern PlaceAtMeFn g_placeAtMe;
// The live reference the player is talking to (SetSpeakerRef), or null.
extern void*       g_speakerRef;

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
// World units between two references, or -1 when either cannot be measured.
// The string form `Distance` resolves ids; this one takes what the caller
// already holds.
float DistanceBetween(void* from, void* to);
bool StartQuest(void* form, bool* justStarted);
// Puts `ref` at a position with a Z rotation in degrees. game_calls_move.cpp.
void PlaceAt(void* ref, float x, float y, float z, float zRot);

template <typename Fn>
Fn Native(const char* name, std::uint64_t id) {
    return reinterpret_cast<Fn>(Resolve(name, id, nullptr));
}

// Sends every reference to a spot in the named cell, `zRot` in degrees, in
// ONE engine move each, so the player's cell load cannot land between a move
// and a reposition. game_calls_move.cpp.
void SendToCell(const std::vector<void*>& refs, const std::string& cell,
                float x, float y, float z, float zRot);
// Sends every reference onto a persistent marker, taking its rotation.
void SendToMarker(const std::vector<void*>& refs, const FormRef& marker);
// The actors in a follow slot aimed at the player. game_calls_ai.cpp.
std::vector<void*> PlayerFollowers();

// Each file resolves its own natives and supplies its own hooks.
void InstallMoveCalls(GameHooks& hooks);
void InstallAiCalls(GameHooks& hooks);
void InstallQueryCalls(GameHooks& hooks);
// The spell natives: the actor's spell list, casting, and active effects.
// See: docs/commentary/morrowind_runtime.md#spell-commands
void InstallSpellCalls(GameHooks& hooks);
// The player's factions onto the converted FACTs, and the tick's copy of the
// state barks test into their GLOBs. game_calls_state.cpp.
// See: docs/commentary/morrowind_runtime.md#published-state
void InstallStateCalls(GameHooks& hooks);
// PC Crime Level on the engine's crime gold, and the fine and jail opcodes.
// See: docs/commentary/morrowind_runtime.md#crime-is-the-engines
void InstallCrimeCalls(GameHooks& hooks);
void PublishState();
// The seven player-control switches and Game.ShowRaceMenu.
// See: docs/commentary/morrowind_runtime.md#the-control-switches
void InstallControlCalls(GameHooks& hooks);
// The engine's buttoned message box, which Debug.MessageBox cannot raise.
// Hooked through ShowMessage rather than a hook of its own.
// See: docs/commentary/morrowind_runtime.md#messagebox-buttons
void InstallMessageCalls();
void ShowButtonMessage(const std::string& text,
                       const std::vector<std::string>& buttons);
// Skyrim's corner notification, which is what OpenMW's buttonless message box
// is: the line a refused action shows. Nothing for empty text.
void Notify(const std::string& text);

}  // namespace gamecalls
}  // namespace tesruntime::mw
