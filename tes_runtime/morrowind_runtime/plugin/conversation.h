// One open conversation: what the window shows and what a click means.
//
// The movie carries no ActionScript, so this is the whole of the dialogue
// window's behaviour -- the topic list with its rule and scrollbar, the
// history with its keyword links and scrollbar, the caption sized to the
// name, the Goodbye button -- resolved in C++ against the rects, paths and
// font metrics the generator wrote to menu_layout.h. It is what OpenMW's
// DialogueWindow does over MyGUI.
// See: docs/commentary/morrowind_runtime.md#the-conversation

#pragma once

namespace mwruntime {

// Wires the window's input into the menu. Once, after InstallMenu.
void InstallConversation();

// Greets `speaker` (a TES3 actor id) shown as `displayName`, fills the
// window and opens it. `playerName` is what "%PCName" becomes.
void BeginConversation(const char* speaker, const char* displayName,
                       const char* playerName);

// True while a conversation is open.
bool ConversationOpen();

}  // namespace mwruntime
