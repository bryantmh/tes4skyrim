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

#include <string>
#include <vector>

namespace tesruntime::mw {

// Wires the window's input into the menu. Once, after InstallMenu.
void InstallConversation();

// Greets `speaker` (a TES3 actor id) shown as `displayName`, fills the
// window and opens it. `playerName` is what "%PCName" becomes.
void BeginConversation(const char* speaker, const char* displayName,
                       const char* playerName);

// True while a conversation is open.
bool ConversationOpen();

// A headless run's clicks, each the same call the window's own click makes.
// The topic rows the list shows now, in order.
std::vector<std::string> ConversationTopics();
// Asks a LISTED topic; false when it is not listed or the list is locked.
bool ConversationAsk(const std::string& topic);
// Answers pending choice `index`; false when none such is pending.
bool ConversationChoose(int index);
// Persuades by name (admire, intimidate, taunt, bribe10/100/1000); false when
// the name is unknown, the list is locked or the purse cannot pay.
bool ConversationPersuade(const std::string& name);
// Clicks Goodbye; false when a pending choice locks it.
bool ConversationGoodbye();
// The newest reply or notice in the history, and the pending choices' texts.
std::string ConversationLastText();
std::vector<std::string> ConversationChoices();

// True while a reply's result script runs: a new conversation begun now would
// tear down the one that script is still running in.
bool InResultScript();

}  // namespace tesruntime::mw
