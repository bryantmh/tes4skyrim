// Morrowind's class and birthsign menus (OpenMW's PickClassDialog with the
// custom class beside it, and BirthDialog), without major and minor skills.
// ONE of each serves every game, its rows the shared table's
// (chargen_tables.h). A TES3 script opens one with EnableClassMenu /
// EnableBirthMenu; a converted TES4 script asks through its plugin's
// TES4ChargenRequest global (TES4_Chargen.psc); the console opens one with
// `showmenu MorrowindClassMenu` / `showmenu MorrowindBirthMenu`. A chosen class
// moves the player's starting attributes (leveling.h); a chosen sign's spells
// are granted here, the last sign's taken back.
// See: docs/commentary/morrowind_runtime.md#chargen-menus

#pragma once

#include <string>

namespace tesruntime::mw {

// Registers both menus and the TES3 request hook. False when the engine's
// menu entry points are missing.
bool InstallChargenMenus();

// Game thread, every tick: takes a TES4 script's request, and opens a
// waiting menu once no other menu holds the game. True while a menu is open
// or waiting, when nothing else should open.
bool TickChargen();

// Whether either menu is open, or the class choice before them is under way.
bool ChargenMenuOpen();

// `name` with each word's first letter capitalized: a custom class's name is
// kept lowercase, as every chosen name is.
std::string TitleCase(const std::string& name);

}  // namespace tesruntime::mw
