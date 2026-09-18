// The persuasion modal over the dialogue window: OpenMW's PersuasionDialog
// -- Admire, Intimidate, Taunt, three bribes, the gold label, Cancel --
// resolved in C++ against menu_layout.h the way the window itself is. A
// bribe the player cannot pay is listed disabled, as onOpen leaves it.
// See: docs/commentary/morrowind_runtime.md#persuasion

#pragma once

#include "persuasion.h"

namespace mwruntime {

// What the window does with the persuasion the player chose.
using PersuasionChosen = void (*)(Persuasion type);

// Shows the modal, reading the player's purse for the bribe rows.
void OpenPersuasionModal(PersuasionChosen onChosen);
void ClosePersuasionModal();
bool PersuasionModalOpen();

// Writes every field and the visibility. Part of the window's PushAll.
void PushPersuasionModal();

// Input while the modal is up, in stage pixels. A click on a row closes
// the modal and hands the choice on; anything off the modal is ignored.
void PersuasionModalHover(double x, double y);
void PersuasionModalClick(double x, double y);

}  // namespace mwruntime
