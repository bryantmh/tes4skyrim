// Where Morrowind state reaches into Skyrim: a journal index becomes a quest
// stage, and AddItem / RemoveItem / GetItemCount act on real inventories.
//
// Every call is a Papyrus native invoked the way the VM invokes it, resolved
// by Address Library id. Installed into DialogueState's GameHooks, so the
// script opcodes stay free of engine types and the headless tests run without
// any of this.
// See: docs/commentary/morrowind_runtime.md#game-calls

#pragma once

namespace tesruntime::mw {

// Resolves the natives and fills GameHooks. A native that does not resolve
// leaves its hook null, which the opcodes treat as "log only".
void InstallGameCalls();

// The placed reference the player is talking to, so a command with no
// explicit target can act on the speaker. Null clears it.
void SetSpeakerRef(const char* actorId, void* ref);

}  // namespace tesruntime::mw
