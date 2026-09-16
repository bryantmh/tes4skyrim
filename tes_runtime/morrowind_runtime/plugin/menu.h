// The Morrowind dialogue menu: a Skyrim IMenu of our own, showing our own SWF.
//
// Registered under a NEW name with MenuManager::Register, loading
// Interface/morrowind_dialogue.swf. No vanilla menu, movie or record is
// touched, which is what keeps Skyrim's own dialogue working.
// See: docs/commentary/morrowind_runtime.md#menu-registration

#pragma once

namespace mwruntime {

// Resolves the menu entry points and registers the menu. False when an
// address is missing, in which case NOTHING is hooked and the game behaves
// exactly as it would without this plugin.
bool InstallMenu();

// Opens or closes the menu. Safe before InstallMenu, where both are no-ops.
void OpenMenu();
void CloseMenu();

// True once Register accepted the menu.
bool MenuInstalled();

// The name it registered under, for UI.OpenMenu from Papyrus or the console.
const char* MenuName();

}  // namespace mwruntime
