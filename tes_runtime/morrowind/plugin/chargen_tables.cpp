#include "chargen_tables.h"

#include <algorithm>
#include <cstdlib>
#include <cstring>

#include "scope.h"
#include "store.h"

namespace tesruntime::mw {

namespace {

constexpr const char* kFileChargen = "chargen.txt";

std::vector<ChargenTable> g_tables;
std::vector<ClassRow> g_classes;
std::vector<SignRow> g_signs;

std::vector<std::string> List(const std::string& text) {
    std::vector<std::string> out;
    if (text.empty()) return out;
    for (const std::string& item : SplitFields(text, ';')) out.push_back(Unescape(item));
    return out;
}

// Grows `rows` to hold `index` and returns that slot.
template <typename T>
T& Slot(std::vector<T>& rows, std::size_t index) {
    if (rows.size() <= index) rows.resize(index + 1);
    return rows[index];
}

// A plugin's rows: `form.<name>` names a global, `class.<i>` / `sign.<i>` its
// own names in menu order (the name is the row's first field).
void Take(ChargenTable& table, const std::string& key, const std::string& value) {
    const std::size_t dot = key.find('.');
    if (dot == std::string::npos) return;
    const std::string kind = key.substr(0, dot), name = key.substr(dot + 1);
    const std::size_t index = static_cast<std::size_t>(std::atoi(name.c_str()));
    if (kind == "form") {
        FormRef* slot = name == "request" ? &table.request
                        : name == "class"  ? &table.classChoice
                        : name == "birthsign" ? &table.signChoice : nullptr;
        if (slot) *slot = ParseFormRefField(value);
    } else if (kind == "class" || kind == "sign") {
        const std::vector<std::string> f = SplitFields(value, '|');
        Slot(kind == "class" ? table.classes : table.signs, index) =
            f.empty() ? std::string() : Unescape(f.front());
    } else if (kind == "quiz") {
        const std::vector<std::string> f = SplitFields(value, '|');
        if (f.size() < 5) return;
        QuizRow& row = Slot(table.quiz, index);
        row.question = Unescape(f[0]);
        for (int i = 0; i < 3; ++i) row.answers[i] = Unescape(f[1 + i]);
        row.sound = Unescape(f[4]);
    }
}

// The shared table's rows: whole classes and signs.
void TakeShared(const std::string& key, const std::string& value) {
    const std::size_t dot = key.find('.');
    if (dot == std::string::npos) return;
    const std::string kind = key.substr(0, dot);
    const std::size_t index = static_cast<std::size_t>(std::atoi(key.c_str() + dot + 1));
    if (kind == "class") Slot(g_classes, index) = ParseClassRow(value);
    if (kind == "sign") Slot(g_signs, index) = ParseSignRow(value);
}

constexpr const char* kFileStats = "stats.txt";

// A plugin's text for one sMisc setting.
struct Label {
    std::string setting;
    std::string text;
    int layer = -1;
};

std::vector<StatsTable> g_stats;
std::vector<Label> g_labels;

// `Plugin.esm@FormID` as a FormRef.
FormRef AtRef(const std::string& text) {
    std::string field = text;
    std::replace(field.begin(), field.end(), '@', '|');
    return ParseFormRefField(field);
}

// `fame=` / `infamy=` name the standing globals, `bounty.<i>=name|Plugin|FormID`
// a realm, `misc.<index>=setting|label|Plugin|FormID` a statistic,
// `page.<i>=...` a row of the game's own page, `label.<setting>=text` a label.
void TakeStat(StatsTable& table, int layer, const std::string& key, const std::string& value) {
    const std::size_t dot = key.find('.');
    const std::string kind = key.substr(0, dot);
    const std::vector<std::string> f = SplitFields(value, '|');
    if (kind == "fame" || kind == "infamy") {
        (kind == "fame" ? table.fame : table.infamy) = ParseFormRefField(value);
    } else if (dot == std::string::npos) {
        return;
    } else if (kind == "label") {
        g_labels.push_back({key.substr(dot + 1), Unescape(value), layer});
    } else if (kind == "bounty" && f.size() >= 3) {
        table.bounties.push_back({Unescape(f[0]), ParseFormRefField(f[1] + '|' + f[2])});
    } else if (kind == "page" && f.size() >= 3) {
        table.page.push_back(ParsePageRow(value));
    } else if (kind == "misc" && f.size() >= 4) {
        table.misc.push_back({std::atoi(key.c_str() + dot + 1), f[0], f[1],
                              ParseFormRefField(f[2] + '|' + f[3])});
    }
}

}  // namespace

// `name|specialization|a1,a2|description`.
ClassRow ParseClassRow(const std::string& value) {
    const std::vector<std::string> f = SplitFields(value, '|');
    ClassRow out;
    if (f.size() < 4) return out;
    out.name = Unescape(f[0]);
    out.specialization = std::atoi(f[1].c_str());
    const std::vector<std::string> favored = SplitFields(f[2], ',');
    for (std::size_t i = 0; i < 2 && i < favored.size(); ++i) {
        out.favored[i] = std::atoi(favored[i].c_str());
    }
    out.description = Unescape(f[3]);
    return out;
}

// `name|picture|description|spell;spell|id;id|kind~icon~text;...`.
SignRow ParseSignRow(const std::string& value) {
    const std::vector<std::string> f = SplitFields(value, '|');
    SignRow out;
    if (f.size() < 5) return out;
    out.name = Unescape(f[0]);
    out.image = f[1];
    out.description = Unescape(f[2]);
    out.spells = List(f[3]);
    out.spellIds = List(f[4]);
    for (const std::string& line : f.size() > 5 ? List(f[5]) : std::vector<std::string>()) {
        const std::size_t first = line.find('~');
        const std::size_t second = first == std::string::npos ? first : line.find('~', first + 1);
        if (second == std::string::npos || first == 0) continue;
        out.lines.push_back({line[0], line.substr(first + 1, second - first - 1),
                             line.substr(second + 1)});
    }
    return out;
}

// `label|Plugin|FormID`, or `label|Quest@FormID,stage,value;...|otherwise`.
PageStatRow ParsePageRow(const std::string& value) {
    const std::vector<std::string> f = SplitFields(value, '|');
    PageStatRow out;
    if (f.size() < 3) return out;
    out.label = Unescape(f[0]);
    if (f[1].find('@') == std::string::npos) {
        out.global = ParseFormRefField(f[1] + '|' + f[2]);
        return out;
    }
    for (const std::string& rule : SplitFields(f[1], ';')) {
        const std::vector<std::string> r = SplitFields(rule, ',');
        if (r.size() < 3) continue;
        out.rules.push_back({AtRef(r[0]), std::atoi(r[1].c_str()),
                             static_cast<float>(std::atof(r[2].c_str()))});
    }
    out.otherwise = static_cast<float>(std::atof(f[2].c_str()));
    return out;
}

void ClearChargenTables() {
    g_tables.clear();
    g_classes.clear();
    g_signs.clear();
    g_stats.clear();
    g_labels.clear();
}

void LoadStatsTable(const std::string& pluginDir, int layer) {
    StatsTable table;
    table.plugin = PluginOf(pluginDir);
    table.layer = layer;
    ForEachTableRow(pluginDir + kFileStats,
                    [&table, layer](const std::string& key, const std::string& value) {
                        TakeStat(table, layer, key, value);
                    });
    if (!table.fame.plugin.empty() || !table.misc.empty() || !table.bounties.empty() ||
        !table.page.empty()) {
        g_stats.push_back(std::move(table));
    }
}

const std::vector<StatsTable>& StatsTables() { return g_stats; }

std::string StatLabel(const std::string& setting, const std::string& fallback, int layer) {
    const Label* best = nullptr;
    for (const Label& label : g_labels) {
        if (label.setting != setting || !LayersRelated(label.layer, layer)) continue;
        if (!best || LayerDepth(label.layer) > LayerDepth(best->layer)) best = &label;
    }
    return best ? best->text : fallback;
}

void LoadChargenTable(const std::string& pluginDir, int layer) {
    ChargenTable table;
    table.plugin = PluginOf(pluginDir);
    table.layer = layer;
    ForEachTableRow(pluginDir + kFileChargen,
                    [&table](const std::string& key, const std::string& value) {
                        Take(table, key, value);
                    });
    if (!table.request.plugin.empty()) g_tables.push_back(std::move(table));
}

void LoadSharedChargen(const std::string& rootDir) {
    g_classes.clear();
    g_signs.clear();
    ForEachTableRow(rootDir + kFileChargen, TakeShared);
}

const std::vector<ChargenTable>& ChargenTables() { return g_tables; }

const std::vector<QuizRow>& MenuQuiz() {
    static const std::vector<QuizRow> kNone;
    for (const ChargenTable& table : g_tables) {
        if (!table.quiz.empty()) return table.quiz;
    }
    return kNone;
}

const std::vector<ClassRow>& MenuClasses() { return g_classes; }

const std::vector<SignRow>& MenuSigns() { return g_signs; }

int IndexOfName(const std::vector<std::string>& names, const std::string& name) {
    for (std::size_t i = 0; i < names.size(); ++i) {
        if (_stricmp(names[i].c_str(), name.c_str()) == 0) return static_cast<int>(i);
    }
    return -1;
}

const ClassRow* FindClassRow(const std::string& name) {
    for (const ClassRow& row : g_classes) {
        if (_stricmp(row.name.c_str(), name.c_str()) == 0) return &row;
    }
    return nullptr;
}

const SignRow* FindSignRow(const std::string& name) {
    for (const SignRow& row : g_signs) {
        if (_stricmp(row.name.c_str(), name.c_str()) == 0) return &row;
    }
    return nullptr;
}

}  // namespace tesruntime::mw
