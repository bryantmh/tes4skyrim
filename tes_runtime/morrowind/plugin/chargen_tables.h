// The class and birthsign menus' rows and the stats window's Statistics rows.
//
// There is ONE class menu and ONE birthsign menu for every game: the shared
// chargen.txt at the sidecar root, which the packaged runtime carries, built
// from the game the converter's settings chose. Each plugin's own chargen.txt
// names only the request and choice globals its converted scripts use and its
// own classes and signs in menu order, so a choice is answered as that
// plugin's index of the same name. stats.txt is a plugin's Statistics rows.
// See: docs/commentary/morrowind_runtime.md#chargen-menus

#pragma once

#include <string>
#include <vector>

#include "script_tables.h"

namespace tesruntime::mw {

// A playable class: specialization 0..2, two favored attributes (TES3
// order, -1 when unset) and its description.
struct ClassRow {
    std::string name;
    int specialization = 0;
    int favored[2] = {-1, -1};
    std::string description;
};

// One line of the birthsign menu's spell list as OpenMW's BirthDialog writes
// it: a category header ('h'), a spell's name ('s') or an effect ('e') shown
// beside the movie's `Icon<row>_<icon>`.
struct SignLine {
    char kind = 's';
    std::string icon;
    std::string text;
};

// A birthsign: its picture's key (the movie's `Sign_<key>`), description, the
// names of its spells and the ids that grant them (a TES3 spell id, or
// `Plugin.esm@FormID` for a converted TES4 spell), and its spell list's lines.
struct SignRow {
    std::string name;
    std::string image;
    std::string description;
    std::vector<std::string> spells;
    std::vector<std::string> spellIds;
    std::vector<SignLine> lines;
};

// One question of the class quiz Morrowind.ini holds: the question, its three
// answers (combat, magic, stealth, OpenMW's sGenerateClassSteps), and the
// recording, a `Say` path.
// See: docs/commentary/morrowind_runtime.md#class-choice
struct QuizRow {
    std::string question;
    std::string answers[3];
    std::string sound;
};

// One plugin's side of the menus: the request global its converted scripts
// ask through, the two choice globals they read back, its own class and
// sign names in its menu order, and its install's class quiz.
struct ChargenTable {
    std::string plugin;
    int layer = -1;
    FormRef request;
    FormRef classChoice;
    FormRef signChoice;
    std::vector<std::string> classes;
    std::vector<std::string> signs;
    std::vector<QuizRow> quiz;
};

// The class quiz of the first table that has one, or empty.
const std::vector<QuizRow>& MenuQuiz();

void ClearChargenTables();

// Reads `pluginDir`'s chargen.txt, if any, as layer `layer`'s table.
void LoadChargenTable(const std::string& pluginDir, int layer);

// Reads the shared menu rows, `rootDir`'s chargen.txt.
void LoadSharedChargen(const std::string& rootDir);

const std::vector<ChargenTable>& ChargenTables();

// The one class menu's and birthsign menu's rows.
const std::vector<ClassRow>& MenuClasses();
const std::vector<SignRow>& MenuSigns();

// A menu row by name, case-insensitively, or null.
const ClassRow* FindClassRow(const std::string& name);
const SignRow* FindSignRow(const std::string& name);

// `names`' index of `name`, case-insensitively, or -1.
int IndexOfName(const std::vector<std::string>& names, const std::string& name);

// Parses one row's value; exposed for the tests.
ClassRow ParseClassRow(const std::string& value);
SignRow ParseSignRow(const std::string& value);

// One TES4 general statistic a plugin keeps in a global: its index, the
// setting naming it, the label without one.
// See: docs/commentary/morrowind_runtime.md#statistics-tab
struct MiscStatRow {
    int index = -1;
    std::string setting;
    std::string fallback;
    FormRef global;
};

// A realm's bounty: its crime faction and the name its bounty goes by.
struct BountyRow {
    std::string name;
    FormRef faction;
};

// One value read off quest stages: `value` while `quest` is below `stage`,
// each row in turn, else `otherwise`.
struct StageRule {
    FormRef quest;
    int stage = 0;
    float value = 0;
};

// A statistic the game's own statistics page showed: a global mirroring a
// script variable, or a value its stage rules give.
struct PageStatRow {
    std::string label;
    FormRef global;
    std::vector<StageRule> rules;
    float otherwise = 0;
};

// One plugin's Statistics rows: stats.txt.
struct StatsTable {
    std::string plugin;
    int layer = -1;
    FormRef fame;
    FormRef infamy;
    std::vector<BountyRow> bounties;
    std::vector<MiscStatRow> misc;
    std::vector<PageStatRow> page;
};

void LoadStatsTable(const std::string& pluginDir, int layer);
const std::vector<StatsTable>& StatsTables();

// The text for `setting` of the deepest plugin related to `layer` (the game's
// own plugins: its master, a translation built on it), else `fallback`.
std::string StatLabel(const std::string& setting, const std::string& fallback, int layer);

// Parses a `label|Plugin|FormID` page row or a `label|rule;rule|otherwise`
// one; exposed for the tests.
PageStatRow ParsePageRow(const std::string& value);

}  // namespace tesruntime::mw
