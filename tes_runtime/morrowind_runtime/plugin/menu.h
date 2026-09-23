// The Morrowind dialogue menu: a Skyrim IMenu of our own, showing our own SWF.
//
// Registered under a NEW name with MenuManager::Register, loading
// Interface/morrowind_dialogue.swf. No vanilla menu, movie or record is
// touched, which is what keeps Skyrim's own dialogue working.
//
// This layer knows the MOVIE: fields, properties, mouse events. What the
// fields say and what a click means is conversation.cpp's, which registers
// itself through MenuInput.
// See: docs/commentary/morrowind_runtime.md#menu-registration

#pragma once

#include <cstddef>
#include <cstdint>

namespace mwruntime {

// Resolves the menu entry points and registers the menu. False when an
// address is missing, in which case NOTHING is hooked and the game behaves
// exactly as it would without this plugin.
bool InstallMenu();

// Opens or closes the menu by posting a UIMessage, the way the engine opens
// its own. Safe before InstallMenu, where both are no-ops.
void OpenMenu();
void CloseMenu();

// Posts kMessage_Close for ANY menu by name -- used to dismiss Skyrim's own
// dialogue menu when a Morrowind speaker takes over.
void CloseMenuNamed(const char* name);

// Posts kMessage_Open for ANY registered menu by name -- the crafting menu an
// alchemy apparatus opens.
void OpenMenuNamed(const char* name);

// Writes one of the movie's dynamic text fields by its VARIABLE path. Held
// and replayed when the menu next opens, so text set before the open lands.
// See: docs/commentary/morrowind_runtime.md#dynamic-text
void SetMenuText(const char* variable, const char* text);

// Sets or reads a NUMBER property on the live movie -- a field's textColor or
// scroll. Not replayed: a caller re-applies these from MenuInput::opened.
void SetMenuNumber(const char* path, double value);
bool GetMenuNumber(const char* path, double* out);

// Calls a method on the live movie with numeric arguments, e.g. a field's
// getCharIndexAtPoint. False when the movie is absent or the call failed.
bool InvokeMenuNumber(const char* path, const double* args, std::size_t count,
                      double* result);

// What the mouse did, in STAGE pixels, and when the movie came and went.
// Every pointer may be null.
struct MenuInput {
    void (*hover)(double x, double y) = nullptr;
    void (*click)(double x, double y) = nullptr;
    void (*wheel)(double x, double y, double delta) = nullptr;
    void (*cancel)() = nullptr;
    void (*opened)() = nullptr;
    void (*closed)() = nullptr;
    void (*tick)() = nullptr;
};

void SetMenuInput(const MenuInput& input);

// True once Register accepted the menu.
bool MenuInstalled();

// How many OPEN menus pause the game, ours included. 0 before InstallMenu.
// See: docs/commentary/morrowind_runtime.md#the-tick-stops-while-the-game-is-paused
std::uint32_t PausingMenuCount();

// The name it registered under, for UI.OpenMenu from Papyrus or the console.
const char* MenuName();

}  // namespace mwruntime
