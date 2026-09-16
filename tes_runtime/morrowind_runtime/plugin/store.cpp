#include "store.h"

#include <windows.h>

#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <sstream>

#include "log.h"

namespace mwruntime {

namespace {

constexpr const char* kSigTopic = "MWDI";
constexpr const char* kSigInfo = "MWIN";
constexpr const char* kFileTopics = "MWDI.txt";
constexpr const char* kFileInfos = "MWIN.txt";

constexpr const char* kBegin = "---RECORD_BEGIN---";
constexpr const char* kEnd = "---RECORD_END---";

std::unordered_map<std::string, Topic> g_topics;

std::string Lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(),
                   [](unsigned char c) { return static_cast<char>(::tolower(c)); });
    return text;
}

DialType ParseDialType(const std::string& name) {
    if (name == "Topic") return DialType::Topic;
    if (name == "Voice") return DialType::Voice;
    if (name == "Greeting") return DialType::Greeting;
    if (name == "Persuasion") return DialType::Persuasion;
    if (name == "Journal") return DialType::Journal;
    return DialType::Unknown;
}

using Record = std::unordered_map<std::string, std::string>;

const std::string& Get(const Record& rec, const char* key) {
    static const std::string kEmpty;
    const auto it = rec.find(key);
    return it == rec.end() ? kEmpty : it->second;
}

int GetInt(const Record& rec, const char* key, int fallback) {
    const auto it = rec.find(key);
    if (it == rec.end() || it->second.empty()) return fallback;
    return std::atoi(it->second.c_str());
}

// Directory this plugin's sidecars live in, or "" when the game path is
// unknown. Its own folder, never TESRuntime's.
std::string SidecarDir() {
    char exe[MAX_PATH] = {0};
    if (!GetModuleFileNameA(nullptr, exe, MAX_PATH)) return "";
    std::string path(exe);
    const std::size_t slash = path.find_last_of("\\/");
    if (slash == std::string::npos) return "";
    return path.substr(0, slash) + "\\Data\\SKSE\\Plugins\\MorrowindRuntime\\";
}

std::string ReadFile(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return "";
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

void AddConditions(const Record& rec, Info& info) {
    const int count = GetInt(rec, "ConditionCount", 0);
    info.conditions.reserve(static_cast<std::size_t>(count));
    for (int i = 0; i < count; ++i) {
        const std::string prefix = "Condition[" + std::to_string(i) + "].";
        const std::string& fn = Get(rec, (prefix + "Function").c_str());
        if (fn.empty()) continue;
        Condition cond;
        cond.function = fn[0];
        const std::string& varType = Get(rec, (prefix + "VarType").c_str());
        const std::string& cmp = Get(rec, (prefix + "Comparison").c_str());
        cond.varType = varType.empty() ? 0 : varType[0];
        cond.comparison = cmp.empty() ? 0 : cmp[0];
        cond.variable = Unescape(Get(rec, (prefix + "Variable").c_str()));
        const std::string& fnIndex = Get(rec, (prefix + "FunctionIndex").c_str());
        if (!fnIndex.empty()) cond.index = std::atoi(fnIndex.c_str());
        const std::string& valueType = Get(rec, (prefix + "ValueType").c_str());
        const std::string& value = Get(rec, (prefix + "Value").c_str());
        cond.isFloat = valueType == "Float";
        if (cond.isFloat) {
            cond.valueFloat = static_cast<float>(std::atof(value.c_str()));
        } else {
            cond.valueInt = std::atoi(value.c_str());
        }
        info.conditions.push_back(std::move(cond));
    }
}

Info MakeInfo(const Record& rec) {
    Info info;
    info.id = Unescape(Get(rec, "EditorID"));
    info.topic = Unescape(Get(rec, "Topic"));
    info.ordinal = GetInt(rec, "Ordinal", 0);
    info.type = ParseDialType(Get(rec, "InfoType"));
    info.disposition = GetInt(rec, "Disposition", 0);
    info.journalIndex = GetInt(rec, "JournalIndex", 0);
    info.rank = GetInt(rec, "Rank", -1);
    info.gender = GetInt(rec, "Gender", -1);
    info.pcRank = GetInt(rec, "PCRank", -1);
    info.factionLess = GetInt(rec, "FactionLess", 0) != 0;
    info.actor = Unescape(Get(rec, "Actor"));
    info.race = Unescape(Get(rec, "Race"));
    info.clazz = Unescape(Get(rec, "Class"));
    info.faction = Unescape(Get(rec, "Faction"));
    info.cell = Unescape(Get(rec, "Cell"));
    info.pcFaction = Unescape(Get(rec, "PCFaction"));
    info.voice = Unescape(Get(rec, "Voice"));
    info.response = Unescape(Get(rec, "Response"));
    info.resultScript = Unescape(Get(rec, "ResultScript"));
    info.questStatus = Get(rec, "QuestStatus");
    AddConditions(rec, info);
    return info;
}

// Responses arrive per topic in file order; Ordinal is the authority, so a
// sidecar concatenated out of order still filters correctly.
void SortInfos() {
    for (auto& entry : g_topics) {
        std::stable_sort(entry.second.infos.begin(), entry.second.infos.end(),
                         [](const Info& a, const Info& b) {
                             return a.ordinal < b.ordinal;
                         });
    }
}

std::size_t LoadOne(const std::string& dir, const char* name,
                    StoreStats& stats) {
    const std::string text = ReadFile(dir + name);
    if (text.empty()) return 0;
    const auto records = ParseExport(text);
    for (const Record& rec : records) {
        const std::string sig = Get(rec, "Signature");
        if (sig == kSigTopic) {
            Topic topic;
            topic.id = Unescape(Get(rec, "EditorID"));
            topic.type = ParseDialType(Get(rec, "DialType"));
            if (topic.id.empty()) continue;
            g_topics[Lower(topic.id)] = std::move(topic);
            ++stats.topics;
        } else if (sig == kSigInfo) {
            Info info = MakeInfo(rec);
            const auto it = g_topics.find(Lower(info.topic));
            if (it == g_topics.end()) continue;
            if (!info.resultScript.empty()) ++stats.scripts;
            it->second.infos.push_back(std::move(info));
            ++stats.infos;
        }
    }
    return records.size();
}

}  // namespace

std::string Unescape(const std::string& text) {
    std::string out;
    out.reserve(text.size());
    for (std::size_t i = 0; i < text.size(); ++i) {
        if (text[i] != '\\' || i + 1 >= text.size()) {
            out.push_back(text[i]);
            continue;
        }
        switch (text[++i]) {
            case 'n': out.push_back('\n'); break;
            case 'r': out.push_back('\r'); break;
            case 't': out.push_back('\t'); break;
            case '\\': out.push_back('\\'); break;
            default: out.push_back('\\'); out.push_back(text[i]); break;
        }
    }
    return out;
}

std::vector<std::unordered_map<std::string, std::string>> ParseExport(
    const std::string& text) {
    std::vector<Record> out;
    std::istringstream in(text);
    std::string line;
    Record cur;
    bool open = false;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line == kBegin) {
            cur.clear();
            open = true;
            continue;
        }
        if (line == kEnd) {
            if (open) out.push_back(cur);
            open = false;
            continue;
        }
        if (!open) continue;
        const std::size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        // First wins: a repeated key is the header echo of a body line.
        cur.emplace(line.substr(0, eq), line.substr(eq + 1));
    }
    return out;
}

StoreStats LoadStore() { return LoadStoreFrom(SidecarDir()); }

StoreStats LoadStoreFrom(const std::string& rootIn) {
    g_topics.clear();
    StoreStats stats;
    if (rootIn.empty()) return stats;
    std::string root = rootIn;
    if (root.back() != '\\' && root.back() != '/') root.push_back('\\');

    WIN32_FIND_DATAA find;
    HANDLE handle = FindFirstFileA((root + "*").c_str(), &find);
    if (handle == INVALID_HANDLE_VALUE) return stats;
    std::vector<std::string> plugins;
    do {
        if (!(find.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
        const std::string name = find.cFileName;
        if (name == "." || name == "..") continue;
        plugins.push_back(name);
    } while (FindNextFileA(handle, &find));
    FindClose(handle);

    // DIAL first for every plugin: an INFO is dropped unless its topic exists.
    for (const std::string& plugin : plugins) {
        const std::string dir = root + plugin + "\\";
        if (LoadOne(dir, kFileTopics, stats)) ++stats.files;
    }
    for (const std::string& plugin : plugins) {
        LoadOne(root + plugin + "\\", kFileInfos, stats);
    }
    SortInfos();
    return stats;
}

const std::unordered_map<std::string, Topic>& Topics() { return g_topics; }

const Topic* FindTopic(const std::string& id) {
    const auto it = g_topics.find(Lower(id));
    return it == g_topics.end() ? nullptr : &it->second;
}

}  // namespace mwruntime
