// OpenMW's PersuasionDialog -- Admire, Intimidate, Taunt and three bribes --
// as rows of the list modal. A bribe the player cannot pay is listed
// disabled, as onOpen leaves it.
// See: docs/commentary/morrowind_runtime.md#persuasion

#pragma once

#include "persuasion.h"

namespace mwruntime {

// What the window does with the persuasion the player chose.
using PersuasionChosen = void (*)(Persuasion type);

// Shows the list modal with the six persuasions, reading the player's purse
// for the bribe rows.
void OpenPersuasionModal(PersuasionChosen onChosen);

}  // namespace mwruntime
