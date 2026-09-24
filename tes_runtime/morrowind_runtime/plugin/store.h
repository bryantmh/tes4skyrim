// The TES3 dialogue the runtime filters over, read from the export text.
//
// Loaded once on kMessage_DataLoaded from
// Data\SKSE\Plugins\MorrowindRuntime\<plugin>\{DIAL,INFO}.txt -- the same
// KEY=VALUE blocks tes4_export writes, so there is no second format to keep in
// step with the exporter.
// See: docs/reference/morrowind_dialogue_format.md

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace mwruntime {

// Which of the five kinds a topic is; DIAL.DATA's first byte.
enum class DialType { Topic, Voice, Greeting, Persuasion, Journal, Unknown };

// One SCVR filter. `function` is the rule's kind letter ('1' numbered, '2'
// global, '3' local, '4' journal, ...); `index` is the numbered function when
// that kind is '1', else -1.
struct Condition {
    char        function = 0;
    char        varType = 0;
    char        comparison = 0;
    int         index = -1;
    std::string variable;
    bool        isFloat = false;
    float       valueFloat = 0.0f;
    std::int32_t valueInt = 0;
};

// One response. Identity is (topic, id): 99 ids repeat ACROSS topics in
// Morrowind.esm, so `id` alone is not unique.
// See: docs/reference/morrowind_dialogue_format.md#info-identity
struct Info {
    // The sidecar that staged it: a sibling plugin's response is never
    // offered. See scope.h.
    int         layer = -1;
    std::string id;
    std::string topic;
    int         ordinal = 0;
    DialType    type = DialType::Unknown;
    int         disposition = 0;
    int         journalIndex = 0;
    int         rank = -1;
    int         gender = -1;
    int         pcRank = -1;
    bool        factionLess = false;
    std::string actor;
    std::string race;
    std::string clazz;
    std::string faction;
    std::string cell;
    std::string pcFaction;
    std::string voice;
    std::string response;
    std::string resultScript;
    std::string questStatus;
    std::vector<Condition> conditions;
};

// A topic and its responses, held in export order -- Morrowind takes the FIRST
// match, so the order IS the filter precedence.
struct Topic {
    std::string       id;
    DialType          type = DialType::Unknown;
    std::vector<Info> infos;
    // Every sidecar that stages this DIAL.
    std::vector<int>  layers;
};

// Whether any sidecar staging `topic` is visible to the current layer.
bool TopicVisible(const Topic& topic);

struct StoreStats {
    std::size_t files = 0;
    std::size_t topics = 0;
    std::size_t infos = 0;
    std::size_t scripts = 0;
};

// Reads every sidecar under Data\SKSE\Plugins\MorrowindRuntime. Safe to call
// again; the previous contents are dropped first.
StoreStats LoadStore();

// The same, from a caller-named root, so the loader is testable with no game
// install. `root` holds one subfolder per plugin, and sits in
// `<Data>\SKSE\Plugins\`, whose plugins' headers say who sees whom.
StoreStats LoadStoreFrom(const std::string& root);

// The loaded topics, keyed by their lowercased id -- every sidecar's, so a
// caller listing them skips what TopicVisible rejects.
const std::unordered_map<std::string, Topic>& Topics();

// The topic with this id, or null when no visible sidecar stages it.
// Case-insensitive, as TES3 ids are.
const Topic* FindTopic(const std::string& id);

// Reverses tes4_export's escaping: \\ \n \r \t.
std::string Unescape(const std::string& text);

// Parses one KEY=VALUE export file into records. Exposed for the tests.
std::vector<std::unordered_map<std::string, std::string>> ParseExport(
    const std::string& text);

// Data\SKSE\Plugins\MorrowindRuntime\, with a trailing separator.
std::string SidecarDir();

// The per-plugin subfolder names under `root`, which is where each plugin's
// own dialogue and actor index live. A folder the loaded-check rejects is left
// out.
std::vector<std::string> SidecarPlugins(const std::string& root);

// Whether the sidecar folder `plugin` under `root` belongs to a plugin in this
// load order. With none set (the headless tests) every folder counts.
//
// 🛑 Every table keeps the FIRST row per id, so a stale folder for a plugin
// that is not loaded shadows the live one and every id in it resolves to
// nothing -- the journal never starts, `->AIFollow` finds no reference.
// See: docs/commentary/morrowind_runtime.md#load-order
using SidecarLoadedFn = bool (*)(const std::string& root,
                                 const std::string& plugin);
void SetSidecarLoadedCheck(SidecarLoadedFn check);

// A whole file as text, or "" when it cannot be read.
std::string ReadFile(const std::string& path);

}  // namespace mwruntime
