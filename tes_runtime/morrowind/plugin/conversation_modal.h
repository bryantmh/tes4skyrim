// The list modal over the dialogue window: a title, the player's gold, up to
// layout::kModalRows clickable rows and Cancel. Persuasion and Travel are both
// this window with different rows, as they are two MyGUI layouts of one shape
// in OpenMW. Resolved in C++ against menu_layout.h, like the window itself.
// See: docs/commentary/morrowind_runtime.md#the-list-modal

#pragma once

#include <string>
#include <vector>

namespace tesruntime::mw {

// One row: what it says, and whether it can be clicked. A disabled row is
// listed greyed, which is how OpenMW leaves a bribe or a fare the player
// cannot pay.
struct ModalRow {
    std::string text;
    bool enabled = true;
};

// What the opener does with the row the player clicked.
using ModalChosen = void (*)(int row);

// Shows the modal. Rows past layout::kModalRows are dropped.
void OpenListModal(std::string title, std::vector<ModalRow> rows,
                   ModalChosen onChosen);
void CloseListModal();
bool ListModalOpen();

// Writes every field and the visibility. Part of the window's PushAll.
void PushListModal();

// Input while the modal is up, in stage pixels. A click on an enabled row
// closes the modal and hands the row on; anything off the modal is ignored.
void ListModalHover(double x, double y);
void ListModalClick(double x, double y);

}  // namespace tesruntime::mw
