#include "script_tables.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>

#include "store.h"

namespace mwruntime {

namespace {

constexpr const char* kFileGlobals = "MWGL.txt";
constexpr const char* kFileLocals = "MWSV.txt";
constexpr const char* kFileActorScripts = "MWOS.txt";

std::unordered_map<std::string, GlobalDef> g_globals;
std::unordered_map<std::string, ScriptLocals> g_locals;
std::unordered_map<std::string, std::string> g_actorScripts;
std::unordered_map<std::string, ActorDef> g_actors;
std::unordered_map<std::string, FormRef> g_items;
std::unordered_map<std::string, FormRef> g_quests;

constexpr const char* kFileActors = "MWNP.txt";
constexpr const char* kFileItems = "MWID.txt";
constexpr const char* kFileQuests = "MWQS.txt";

std::vector<std::string> Split(const std::string& text, char sep) {
    std::vector<std::string> out;
    std::size_t start = 0;
    while (true) {
        const std::size_t end = text.find(sep, start);
        out.push_back(text.substr(start, end == std::string::npos
                                             ? std::string::npos
                                             : end - start));
        if (end == std::string::npos) return out;
        start = end + 1;
    }
}

// `race|class|faction|rank|disposition|female|name`.
ActorDef ParseActor(const std::string& value) {
    const std::vector<std::string> f = Split(value, '|');
    ActorDef out;
    if (f.size() < 7) return out;
    out.race = f[0];
    out.clazz = f[1];
    out.faction = f[2];
    out.rank = std::atoi(f[3].c_str());
    out.disposition = std::atoi(f[4].c_str());
    out.female = f[5] == "1";
    out.name = f[6];
    return out;
}

// `Plugin.esm|0012ABCD`.
FormRef ParseFormRef(const std::string& value) {
    FormRef out;
    const std::size_t bar = value.find('|');
    if (bar == std::string::npos) return out;
    out.plugin = value.substr(0, bar);
    out.formId = static_cast<std::uint32_t>(
        std::strtoul(value.c_str() + bar + 1, nullptr, 16));
    return out;
}

std::string Lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(),
                   [](unsigned char c) { return static_cast<char>(::tolower(c)); });
    return text;
}

// Calls `row(key, value)` for each `key=value` line of a table file.
template <typename Fn>
void ForEachRow(const std::string& path, Fn row) {
    const std::string text = ReadFile(path);
    std::size_t start = 0;
    while (start < text.size()) {
        std::size_t end = text.find('\n', start);
        if (end == std::string::npos) end = text.size();
        std::string line = text.substr(start, end - start);
        start = end + 1;
        while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) {
            line.pop_back();
        }
        const std::size_t eq = line.find('=');
        if (eq == 0 || eq == std::string::npos) continue;
        row(line.substr(0, eq), line.substr(eq + 1));
    }
}

// `s:on,s:open,f:timer` into the three ordered lists.
ScriptLocals ParseLocals(const std::string& value) {
    ScriptLocals out;
    std::size_t start = 0;
    while (start < value.size()) {
        std::size_t end = value.find(',', start);
        if (end == std::string::npos) end = value.size();
        const std::string item = value.substr(start, end - start);
        start = end + 1;
        if (item.size() < 3 || item[1] != ':') continue;
        const std::string name = Lower(item.substr(2));
        if (item[0] == 's') out.shorts.push_back(name);
        if (item[0] == 'l') out.longs.push_back(name);
        if (item[0] == 'f') out.floats.push_back(name);
    }
    return out;
}

bool Holds(const std::vector<std::string>& names, const std::string& name) {
    return std::find(names.begin(), names.end(), name) != names.end();
}

}  // namespace

char ScriptLocals::TypeOf(const std::string& name) const {
    const std::string key = Lower(name);
    if (Holds(shorts, key)) return 's';
    if (Holds(longs, key)) return 'l';
    if (Holds(floats, key)) return 'f';
    return ' ';
}

void ClearScriptTables() {
    g_globals.clear();
    g_locals.clear();
    g_actorScripts.clear();
    g_actors.clear();
    g_items.clear();
    g_quests.clear();
}

void LoadScriptTables(const std::string& pluginDir) {
    ForEachRow(pluginDir + kFileGlobals,
               [](const std::string& name, const std::string& value) {
                   GlobalDef def;
                   def.type = value.empty() ? 'f' : value[0];
                   const std::size_t comma = value.find(',');
                   if (comma != std::string::npos) {
                       def.value = static_cast<float>(
                           std::atof(value.c_str() + comma + 1));
                   }
                   g_globals.emplace(Lower(name), def);
               });
    ForEachRow(pluginDir + kFileLocals,
               [](const std::string& name, const std::string& value) {
                   g_locals.emplace(Lower(name), ParseLocals(value));
               });
    ForEachRow(pluginDir + kFileActorScripts,
               [](const std::string& actor, const std::string& script) {
                   g_actorScripts.emplace(Lower(actor), script);
               });
    ForEachRow(pluginDir + kFileActors,
               [](const std::string& actor, const std::string& value) {
                   g_actors.emplace(Lower(actor), ParseActor(value));
               });
    ForEachRow(pluginDir + kFileItems,
               [](const std::string& item, const std::string& value) {
                   g_items.emplace(Lower(item), ParseFormRef(value));
               });
    ForEachRow(pluginDir + kFileQuests,
               [](const std::string& quest, const std::string& value) {
                   g_quests.emplace(Lower(quest), ParseFormRef(value));
               });
}

const ActorDef* FindActor(const std::string& actor) {
    const auto it = g_actors.find(Lower(actor));
    return it == g_actors.end() ? nullptr : &it->second;
}

const FormRef* FindItem(const std::string& item) {
    const auto it = g_items.find(Lower(item));
    return it == g_items.end() ? nullptr : &it->second;
}

const FormRef* FindQuest(const std::string& quest) {
    const auto it = g_quests.find(Lower(quest));
    return it == g_quests.end() ? nullptr : &it->second;
}

std::size_t ActorCount() { return g_actors.size(); }

std::size_t QuestCount() { return g_quests.size(); }

const std::unordered_map<std::string, GlobalDef>& GlobalDefs() {
    return g_globals;
}

const GlobalDef* FindGlobal(const std::string& name) {
    const auto it = g_globals.find(Lower(name));
    return it == g_globals.end() ? nullptr : &it->second;
}

const ScriptLocals* FindScriptLocals(const std::string& script) {
    const auto it = g_locals.find(Lower(script));
    return it == g_locals.end() ? nullptr : &it->second;
}

const std::string& ScriptOf(const std::string& actor) {
    static const std::string kNone;
    const auto it = g_actorScripts.find(Lower(actor));
    return it == g_actorScripts.end() ? kNone : it->second;
}

std::size_t ScriptCount() { return g_locals.size(); }

std::size_t ActorScriptCount() { return g_actorScripts.size(); }

}  // namespace mwruntime
