// A conversation the source forced on the player cannot be left while a line's choices are offered.
//
// Oblivion hides the dialogue menu's Goodbye button (its only way out) when a line that has choices ends, and shows
// it again once the list is empty (Oblivion.exe 0x59eb90 -> 0x59e2b0). Skyrim lets Tab, Escape and the Exit
// button leave at any time, and a quest that waits on the answer (CharacterGen's Emperor, with the player's controls
// disabled) is then stuck. Opt-in: a converted force greet's OnBegin fragment calls
// TES4Polyfill.HoldExitUntilAnswered, which sends the mod event "TES4HoldExit" from the speaker. While that
// speaker's conversation is open, the line it lists choices of has choices, and the topic list is not empty, the
// Dialogue Menu's own cancel method (`onCancelPress`, what all three call) does nothing and its Exit button is
// hidden, and the speaker's in-dialogue idle countdown stands still so the conversation cannot time out either.
// The speaker is disarmed when that conversation closes.
// See: docs/commentary/tes_runtime_dialogue.md#no-exit-while-choices-are-offered

#pragma once

#include "skse_abi.h"

namespace tesruntime {

// Resolves the engine calls and listens for the hold's mod event; false (logged) when one is missing.
bool InstallExitHold(SKSEMessagingInterface* messaging);

// After DataLoaded: starts the tick that equips the Dialogue Menu's movie.
void StartExitHoldTick();

// Whether the player may not leave the open conversation now.
bool ExitHeld();

// The MenuTopicManager's first listed topic node ({item, next}), or null when nothing is listed.
void** FirstListedTopic(void* manager);

}  // namespace tesruntime
