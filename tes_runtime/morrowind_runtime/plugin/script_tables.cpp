#include "script_tables.h"

#include "filter.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>

#include "store.h"

namespace mwruntime {

namespace {

constexpr const char* kFileGlobals = "GLOB.txt";
constexpr const char* kFileLocals = "SCPT_locals.txt";
constexpr const char* kFileActorScripts = "SCPT_objects.txt";

// The bodies the object-script tick compiles, and one instance per placement.
// See: docs/plans/morrowind_object_scripts.md#instances
constexpr const char* kFileScriptBodies = "SCPT_source.txt";
constexpr const char* kFileInstances = "SCPT_instances.txt";

std::unordered_map<std::string, GlobalDef> g_globals;
std::unordered_map<std::string, ScriptLocals> g_locals;
std::unordered_map<std::string, std::string> g_actorScripts;
std::unordered_map<std::string, std::string> g_sources;
// Keyed by InstanceKey; the row keeps the plugin and local id apart so the
// load-order pass does not have to take them back out of the key.
std::unordered_map<std::string, InstanceRow> g_instances;

// The same rows by plugin-LOCAL FormID, which is all an engine hook can offer.
std::unordered_map<std::uint32_t, const InstanceRow*> g_instanceByLocal;

// The same rows in a stable ORDER, so the tick's discovery sweep can resume at
// an index rather than copying the whole table every frame.
std::vector<const InstanceRow*> g_instanceList;
std::unordered_map<std::string, ActorDef> g_actors;
std::unordered_map<std::string, FormRef> g_items;
std::unordered_map<std::string, FormRef> g_quests;
std::unordered_map<std::string, SayLineDef> g_sayLines;
std::unordered_map<std::string, FormRef> g_refs;
std::unordered_map<std::string, FormRef> g_bases;
std::unordered_map<std::string, FormRef> g_cells;

// The AI package quest, and each of its aliases by name -> ALST index.
FormRef g_aiQuest;
bool g_haveAiQuest = false;
std::unordered_map<std::string, int> g_aiAliases;
std::vector<std::string> g_startScripts;
std::unordered_map<std::string, std::vector<TravelDest>> g_travel;
std::unordered_map<std::string, FormRef> g_aiPacks;
std::unordered_map<std::string, FormRef> g_sounds;
std::unordered_map<std::string, SpellDef> g_spells;

// The effects by both keys the commands use: the name and the TES3 index.
std::unordered_map<std::string, FormRef> g_effects;
std::unordered_map<int, FormRef> g_effectsByIndex;

// The soul each creature carries, and the FILLED gems by `<gem>_Filled<n>`.
std::unordered_map<std::string, int> g_souls;
std::unordered_map<std::string, FormRef> g_filledGems;

constexpr const char* kFileActors = "NPC_.txt";
constexpr const char* kFileItems = "items_formid.txt";

// The PLACED reference behind a TES3 id, which `id->Command` acts on.
// See: docs/commentary/morrowind_runtime.md#placed-references
constexpr const char* kFileRefs = "refs_formid.txt";

// The BASE record behind a TES3 id, which PlaceAtPC creates a reference from.
// See: docs/plans/morrowind_object_scripts.md#placeatpc
constexpr const char* kFileBases = "bases_formid.txt";

// A reference INSIDE the cell of that name, which PositionCell aims at --
// Skyrim moves an object to another object, never to a cell.
// See: docs/commentary/morrowind_runtime.md#positioncell-needs-an-anchor
constexpr const char* kFileCells = "cells_formid.txt";

// The AI package quest and its alias indices, which the AI commands fill.
// See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages
constexpr const char* kFileAiAliases = "ai_aliases.txt";
constexpr const char* kFileStartScripts = "SSCR.txt";
constexpr const char* kFileTravel = "NPC_travel.txt";

// The row naming the quest itself, and the prefix on a PACK row; every other
// row is an alias name and its ALST index.
constexpr const char* kAiQuestRow = "quest";
constexpr const char* kAiPackPrefix = "pack.";
constexpr const char* kFileQuests = "quests_formid.txt";
// `Say` names a FILE and ObjectReference.Say takes a TOPIC; this maps
// one to the other. See: docs/commentary/morrowind_runtime.md#scripted-say
constexpr const char* kFileSayLines = "say_formid.txt";
constexpr const char* kFileFactions = "FACT.txt";
constexpr const char* kFileGmsts = "GMST.txt";
constexpr const char* kFileSkills = "SKIL.txt";

// The SNDR a TES3 sound id names, for PlaySound3D and its kin.
// See: docs/commentary/tes5_import_sound.md#the-runtime-sound-table
constexpr const char* kFileSounds = "SOUN.txt";

// The SPEL a TES3 spell id names, and the MGEF behind an effect name or index.
// See: docs/commentary/morrowind_runtime.md#spell-commands
constexpr const char* kFileSpells = "SPEL.txt";
constexpr const char* kFileEffects = "MGEF.txt";

// What the soul gem commands need: each creature's soul, and the filled gems.
// See: docs/commentary/morrowind_runtime.md#soul-gems
constexpr const char* kFileSouls = "CREA_soul.txt";
constexpr const char* kFileSoulGems = "SLGM.txt";

// How a filled gem's staged id ends: `<gem id>_Filled<n>`.
constexpr const char* kFilledSuffix = "_filled";

std::unordered_map<std::string, FactionDef> g_factions;
std::unordered_map<std::string, GmstDef> g_gmsts;
std::unordered_map<int, SkillDef> g_skills;

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

// `x,y,z,rx,ry,rz` into `row`. A row short of all six keeps none of it, so a
// half-written placement can never teleport an object into the void.
void ReadPlacement(const std::string& text, InstanceRow& row) {
    const std::vector<std::string> parts = Split(text, ',');
    if (parts.size() != std::size(row.placement)) return;
    for (std::size_t i = 0; i < parts.size(); ++i) {
        row.placement[i] = static_cast<float>(std::atof(parts[i].c_str()));
    }
    row.hasPlacement = true;
}

// A comma-joined run of ints into `out[0..count)`; short runs leave zeros.
void ParseInts(const std::string& text, int* out, std::size_t count) {
    const std::vector<std::string> f = Split(text, ',');
    for (std::size_t i = 0; i < count && i < f.size(); ++i) {
        out[i] = std::atoi(f[i].c_str());
    }
}

// `race|class|faction|rank|disposition|female|name|level|reputation|
// personality|luck|speechcraft|mercantile|services|gold|hello|fight|flee|
// alarm`.
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
    if (f.size() < 15) return out;
    out.level = std::atoi(f[7].c_str());
    out.reputation = std::atoi(f[8].c_str());
    out.personality = std::atoi(f[9].c_str());
    out.luck = std::atoi(f[10].c_str());
    out.speechcraft = std::atoi(f[11].c_str());
    out.mercantile = std::atoi(f[12].c_str());
    out.services = static_cast<std::uint32_t>(std::strtoul(f[13].c_str(),
                                                           nullptr, 10));
    out.gold = std::atoi(f[14].c_str());
    if (f.size() < 19) return out;
    out.aiSettings[kAiHello] = std::atoi(f[15].c_str());
    out.aiSettings[kAiFight] = std::atoi(f[16].c_str());
    out.aiSettings[kAiFlee] = std::atoi(f[17].c_str());
    out.aiSettings[kAiAlarm] = std::atoi(f[18].c_str());
    if (f.size() < 21) return out;
    ParseInts(f[19], out.attributes, 8);
    ParseInts(f[20], out.skills, 27);
    return out;
}


// `type,value`: 's' text (export-escaped), 'i' or 'f' a number.
GmstDef ParseGmst(const std::string& value) {
    GmstDef out;
    if (value.size() < 2 || value[1] != ',') return out;
    out.type = value[0];
    const std::string body = value.substr(2);
    if (out.type == 's') {
        out.text = Unescape(body);
    } else {
        out.number = static_cast<float>(std::atof(body.c_str()));
    }
    return out;
}

// `attribute|specialization|use0,use1,use2,use3`.
SkillDef ParseSkill(const std::string& value) {
    const std::vector<std::string> f = Split(value, '|');
    SkillDef out;
    if (f.size() < 3) return out;
    out.attribute = std::atoi(f[0].c_str());
    out.specialization = std::atoi(f[1].c_str());
    const std::vector<std::string> uses = Split(f[2], ',');
    for (std::size_t i = 0; i < 4 && i < uses.size(); ++i) {
        out.use[i] = static_cast<float>(std::atof(uses[i].c_str()));
    }
    return out;
}

// `attr1,attr2|skill,...|a1,a2,primary,favoured,rep;...` -- the two judged
// attributes, the faction's skills, then one threshold row per rank.
FactionDef ParseFaction(const std::string& value) {
    const std::vector<std::string> parts = Split(value, '|');
    FactionDef out;
    if (parts.size() < 3) return out;
    const std::vector<std::string> attrs = Split(parts[0], ',');
    for (std::size_t i = 0; i < 2 && i < attrs.size(); ++i) {
        out.attribute[i] = std::atoi(attrs[i].c_str());
    }
    for (const std::string& skill : Split(parts[1], ',')) {
        if (!skill.empty()) out.skills.push_back(std::atoi(skill.c_str()));
    }
    const std::vector<std::string> rows = Split(parts[2], ';');
    for (std::size_t r = 0; r < rows.size() && r < 10; ++r) {
        const std::vector<std::string> f = Split(rows[r], ',');
        if (f.size() < 5) continue;
        out.ranks[r].attribute1 = std::atoi(f[0].c_str());
        out.ranks[r].attribute2 = std::atoi(f[1].c_str());
        out.ranks[r].primarySkill = std::atoi(f[2].c_str());
        out.ranks[r].favouredSkill = std::atoi(f[3].c_str());
        out.ranks[r].reputation = std::atoi(f[4].c_str());
    }
    if (parts.size() > 3) out.rankNames = Split(parts[3], ',');
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

// `name|interior|x|y|z|zRot|Plugin/FormID;...`, one destination per `;`
// group. The marker spells its own separator `/`, since `|` parts the fields.
std::vector<TravelDest> ParseTravel(const std::string& value) {
    std::vector<TravelDest> out;
    for (const std::string& group : Split(value, ';')) {
        const std::vector<std::string> f = Split(group, '|');
        if (f.size() < 6 || f[0].empty()) continue;
        TravelDest dest;
        dest.name = f[0];
        dest.interior = f[1] == "1";
        dest.x = static_cast<float>(std::atof(f[2].c_str()));
        dest.y = static_cast<float>(std::atof(f[3].c_str()));
        dest.z = static_cast<float>(std::atof(f[4].c_str()));
        dest.zRot = static_cast<float>(std::atof(f[5].c_str()));
        if (f.size() > 6 && !f[6].empty()) {
            std::string marker = f[6];
            std::replace(marker.begin(), marker.end(), '/', '|');
            dest.marker = ParseFormRef(marker);
            dest.hasMarker = true;
        }
        out.push_back(dest);
    }
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

// The part of a FormID that is NOT the load-order index.
constexpr std::uint32_t kLocalMask = 0x00FFFFFF;

// The globals the ENGINE owns, which no GLOB record declares.
//
// 🛑 Without these the compiler answers "not a global" and a body that reads
// the clock fails outright: 363 of TR_Mainland's 4,316 bodies name one, which
// is most of what did not compile. Morrowind defines them itself; the value
// here is only the starting one, and the game's clock overwrites it.
// See: docs/plans/morrowind_object_scripts.md#built-in-globals
void AddBuiltinGlobals() {
    static const struct { const char* name; char type; } kBuiltins[] = {
        {"gamehour", 'f'}, {"day", 's'},        {"month", 's'},
        {"year", 's'},     {"dayspassed", 'l'}, {"timescale", 'f'},
    };
    for (const auto& builtin : kBuiltins) {
        GlobalDef def;
        def.type = builtin.type;
        g_globals.emplace(builtin.name, def);
    }
}

// An instance's key: the plugin that placed it and the placement's LOCAL id.
//
// 🛑 The index byte is dropped on both sides. The one a sidecar wrote is the
// converting load order's, not the player's (project_sidecar_formids_need_runtime_index).
std::string InstanceKey(const std::string& plugin, std::uint32_t formId) {
    char id[9] = {0};
    std::snprintf(id, sizeof(id), "%06X", formId & kLocalMask);
    return Lower(plugin) + "|" + id;
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
    g_sources.clear();
    g_instances.clear();
    g_instanceByLocal.clear();
    g_instanceList.clear();
    g_actors.clear();
    g_items.clear();
    g_sounds.clear();
    g_spells.clear();
    g_effects.clear();
    g_effectsByIndex.clear();
    g_souls.clear();
    g_filledGems.clear();
    g_quests.clear();
    g_sayLines.clear();
    g_refs.clear();
    g_bases.clear();
    g_cells.clear();
    g_aiAliases.clear();
    g_startScripts.clear();
    g_travel.clear();
    g_aiPacks.clear();
    g_haveAiQuest = false;
    g_factions.clear();
    g_gmsts.clear();
    g_skills.clear();
}

// The sidecar folder's own name, which is the plugin stem the placements in
// the instances belong to: "...\MorrowindRuntime\TR_Mainland\" -> "TR_Mainland".
std::string PluginOf(const std::string& pluginDir) {
    std::string dir = pluginDir;
    while (!dir.empty() && (dir.back() == '\\' || dir.back() == '/')) {
        dir.pop_back();
    }
    const std::size_t slash = dir.find_last_of("\\/");
    return slash == std::string::npos ? dir : dir.substr(slash + 1);
}

void LoadScriptTables(const std::string& pluginDir) {
    const std::string plugin = PluginOf(pluginDir);
    AddBuiltinGlobals();
    ForEachRow(pluginDir + kFileScriptBodies,
               [](const std::string& name, const std::string& value) {
                   g_sources.emplace(Lower(name), Unescape(value));
               });
    ForEachRow(pluginDir + kFileInstances,
               [](const std::string& formId, const std::string& value) {
                   const std::vector<std::string> f = Split(value, '|');
                   if (f.size() < 3) return;
                   InstanceRow row;
                   row.plugin = f[0];
                   row.baseId = f[1];
                   row.script = f[2];
                   if (f.size() > 3) ReadPlacement(f[3], row);
                   row.localFormId = static_cast<std::uint32_t>(
                       std::strtoul(formId.c_str(), nullptr, 16));
                   const auto added = g_instances.emplace(
                       InstanceKey(row.plugin, row.localFormId), row);
                   if (added.second) {
                       g_instanceByLocal.emplace(
                           row.localFormId & kLocalMask, &added.first->second);
                       g_instanceList.push_back(&added.first->second);
                   }
               });
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
    ForEachRow(pluginDir + kFileSayLines,
               [](const std::string& path, const std::string& value) {
                   const std::vector<std::string> f = Split(value, '|');
                   SayLineDef def;
                   def.topic = ParseFormRef(value);
                   if (f.size() > 2) {
                       def.seconds = static_cast<float>(std::atof(f[2].c_str()));
                   }
                   if (f.size() > 3) def.sound = f[3];
                   g_sayLines.emplace(Lower(path), def);
               });
    ForEachRow(pluginDir + kFileSounds,
               [](const std::string& id, const std::string& value) {
                   g_sounds.emplace(Lower(id), ParseFormRef(value));
               });
    ForEachRow(pluginDir + kFileSpells,
               [](const std::string& id, const std::string& value) {
                   const std::vector<std::string> f = Split(value, '|');
                   SpellDef def;
                   def.form = ParseFormRef(value);
                   if (f.size() > 2) {
                       for (const std::string& n : Split(f[2], ',')) {
                           if (!n.empty()) def.effects.push_back(
                               std::atoi(n.c_str()));
                       }
                   }
                   g_spells.emplace(Lower(id), def);
               });
    ForEachRow(pluginDir + kFileEffects,
               [](const std::string& index, const std::string& value) {
                   const std::vector<std::string> f = Split(value, '|');
                   if (f.size() < 3) return;
                   const FormRef form = ParseFormRef(value);
                   g_effects.emplace(Lower(f[2]), form);
                   g_effectsByIndex.emplace(std::atoi(index.c_str()), form);
               });
    ForEachRow(pluginDir + kFileSouls,
               [](const std::string& creature, const std::string& soul) {
                   g_souls.emplace(Lower(creature), std::atoi(soul.c_str()));
               });
    ForEachRow(pluginDir + kFileSoulGems,
               [](const std::string& id, const std::string& value) {
                   g_filledGems.emplace(Lower(id), ParseFormRef(value));
               });
    ForEachRow(pluginDir + kFileRefs,
               [](const std::string& id, const std::string& value) {
                   g_refs.emplace(Lower(id), ParseFormRef(value));
               });
    ForEachRow(pluginDir + kFileBases,
               [](const std::string& id, const std::string& value) {
                   g_bases.emplace(Lower(id), ParseFormRef(value));
               });
    ForEachRow(pluginDir + kFileCells,
               [](const std::string& name, const std::string& value) {
                   g_cells.emplace(Lower(name), ParseFormRef(value));
               });
    ForEachRow(pluginDir + kFileAiAliases,
               [](const std::string& name, const std::string& value) {
                   const std::string key = Lower(name);
                   const std::string prefix(kAiPackPrefix);
                   if (key == kAiQuestRow) {
                       g_aiQuest = ParseFormRef(value);
                       g_haveAiQuest = true;
                   } else if (key.compare(0, prefix.size(), prefix) == 0) {
                       g_aiPacks.emplace(key.substr(prefix.size()),
                                         ParseFormRef(value));
                   } else {
                       g_aiAliases.emplace(key, std::atoi(value.c_str()));
                   }
               });
    ForEachRow(pluginDir + kFileTravel,
               [](const std::string& actor, const std::string& value) {
                   g_travel[Lower(actor)] = ParseTravel(value);
               });
    ForEachRow(pluginDir + kFileStartScripts,
               [](const std::string& script, const std::string&) {
                   g_startScripts.push_back(script);
               });
    ForEachRow(pluginDir + kFileFactions,
               [](const std::string& faction, const std::string& value) {
                   g_factions.emplace(Lower(faction), ParseFaction(value));
               });
    ForEachRow(pluginDir + kFileGmsts,
               [](const std::string& name, const std::string& value) {
                   g_gmsts.emplace(Lower(name), ParseGmst(value));
               });
    ForEachRow(pluginDir + kFileSkills,
               [](const std::string& index, const std::string& value) {
                   g_skills.emplace(std::atoi(index.c_str()), ParseSkill(value));
               });
}

const FactionDef* FindFaction(const std::string& faction) {
    const auto it = g_factions.find(Lower(faction));
    return it == g_factions.end() ? nullptr : &it->second;
}

const GmstDef* FindGmst(const std::string& name) {
    const auto it = g_gmsts.find(Lower(name));
    return it == g_gmsts.end() ? nullptr : &it->second;
}

float GmstNumber(const std::string& name, float fallback) {
    const GmstDef* def = FindGmst(name);
    return def && def->type != 's' ? def->number : fallback;
}

std::string GmstText(const std::string& name, const std::string& fallback) {
    const GmstDef* def = FindGmst(name);
    return def && def->type == 's' ? def->text : fallback;
}

std::size_t GmstCount() { return g_gmsts.size(); }

const SkillDef* FindSkill(int index) {
    const auto it = g_skills.find(index);
    return it == g_skills.end() ? nullptr : &it->second;
}

std::size_t FactionCount() { return g_factions.size(); }

void AddFactionForTest(const std::string& faction, const FactionDef& def) {
    g_factions[Lower(faction)] = def;
}

const float* FindPlacement(const std::string& id) {
    const FormRef* ref = FindRef(id);
    if (!ref) return nullptr;
    const InstanceRow* row = InstanceByLocal(ref->formId & kLocalMask);
    return row && row->hasPlacement ? row->placement : nullptr;
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

// The topic carrying one scripted `Say` line, by the path the script wrote.
// A script may spell the same file either way round, so the separator is
// folded before the lookup, exactly as the table's key was built.
// See: docs/commentary/morrowind_runtime.md#scripted-say
const SayLineDef* FindSayLine(const std::string& file) {
    std::string key = Lower(file);
    for (char& c : key) {
        if (c == '/') c = '\\';
    }
    const auto it = g_sayLines.find(key);
    return it == g_sayLines.end() ? nullptr : &it->second;
}

const FormRef* FindSound(const std::string& sound) {
    const auto it = g_sounds.find(Lower(sound));
    return it == g_sounds.end() ? nullptr : &it->second;
}

std::size_t SoundCount() { return g_sounds.size(); }

bool SpellDef::Has(int index) const {
    return std::find(effects.begin(), effects.end(), index) != effects.end();
}

const SpellDef* FindSpell(const std::string& spell) {
    const auto it = g_spells.find(Lower(spell));
    return it == g_spells.end() ? nullptr : &it->second;
}

std::size_t SpellCount() { return g_spells.size(); }

std::vector<std::string> SpellsWithEffect(int index) {
    std::vector<std::string> out;
    for (const auto& entry : g_spells) {
        if (entry.second.Has(index)) out.push_back(entry.first);
    }
    return out;
}

const FormRef* FindEffect(const std::string& name) {
    const auto it = g_effects.find(Lower(name));
    return it == g_effects.end() ? nullptr : &it->second;
}

const FormRef* FindEffectByIndex(int index) {
    const auto it = g_effectsByIndex.find(index);
    return it == g_effectsByIndex.end() ? nullptr : &it->second;
}

std::size_t EffectCount() { return g_effects.size(); }

int CreatureSoul(const std::string& creature) {
    const auto it = g_souls.find(Lower(creature));
    return it == g_souls.end() ? 0 : it->second;
}

// `<gem id>_Filled<n>`, the id the export staged the filled variant under.
std::string FilledGemKey(const std::string& gem, int soul) {
    return Lower(gem) + kFilledSuffix + std::to_string(soul);
}

std::string FilledSoulGemId(const std::string& gem, int soul) {
    const std::string key = FilledGemKey(gem, soul);
    return g_filledGems.count(key) ? key : std::string();
}

std::vector<std::string> FilledSoulGemIds(int soul) {
    const std::string suffix = std::string(kFilledSuffix) +
                               std::to_string(soul);
    std::vector<std::string> out;
    for (const auto& entry : g_filledGems) {
        if (entry.first.size() > suffix.size() &&
            entry.first.compare(entry.first.size() - suffix.size(),
                                suffix.size(), suffix) == 0) {
            out.push_back(entry.first);
        }
    }
    return out;
}

std::size_t SoulGemCount() { return g_filledGems.size(); }

const FormRef* FindRef(const std::string& id) {
    const auto it = g_refs.find(Lower(id));
    return it == g_refs.end() ? nullptr : &it->second;
}

std::size_t RefCount() { return g_refs.size(); }

const FormRef* FindBase(const std::string& id) {
    const auto it = g_bases.find(Lower(id));
    return it == g_bases.end() ? nullptr : &it->second;
}

std::size_t BaseCount() { return g_bases.size(); }

const FormRef* FindCellAnchor(const std::string& cell) {
    const auto it = g_cells.find(Lower(cell));
    return it == g_cells.end() ? nullptr : &it->second;
}

std::size_t CellCount() { return g_cells.size(); }

const FormRef* AiQuest() { return g_haveAiQuest ? &g_aiQuest : nullptr; }

int AiAliasIndex(const std::string& name) {
    const auto it = g_aiAliases.find(Lower(name));
    return it == g_aiAliases.end() ? -1 : it->second;
}

const FormRef* FindAiPack(const std::string& kind) {
    const auto it = g_aiPacks.find(Lower(kind));
    return it == g_aiPacks.end() ? nullptr : &it->second;
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

const std::string& ScriptSource(const std::string& script) {
    static const std::string kNone;
    const auto it = g_sources.find(Lower(script));
    return it == g_sources.end() ? kNone : it->second;
}

const std::vector<std::string>& StartScripts() { return g_startScripts; }

const std::vector<TravelDest>* FindTravel(const std::string& actor) {
    const auto it = g_travel.find(Lower(actor));
    return it == g_travel.end() || it->second.empty() ? nullptr : &it->second;
}

std::size_t ScriptSourceCount() { return g_sources.size(); }

const std::unordered_map<std::string, std::string>& ScriptSources() {
    return g_sources;
}

const std::string& InstanceScript(const std::string& plugin,
                                  std::uint32_t localFormId) {
    static const std::string kNone;
    const auto it = g_instances.find(InstanceKey(plugin, localFormId));
    return it == g_instances.end() ? kNone : it->second.script;
}

std::size_t InstanceCount() { return g_instances.size(); }

const InstanceRow* InstanceByLocal(std::uint32_t localFormId) {
    const auto it = g_instanceByLocal.find(localFormId & kLocalMask);
    return it == g_instanceByLocal.end() ? nullptr : it->second;
}

const InstanceRow* InstanceAt(std::size_t index) {
    return index < g_instanceList.size() ? g_instanceList[index] : nullptr;
}

std::size_t ScriptCount() { return g_locals.size(); }

std::size_t ActorScriptCount() { return g_actorScripts.size(); }

}  // namespace mwruntime
