#include "scope.h"

#include <algorithm>
#include <cctype>
#include <climits>
#include <cstdint>
#include <cstring>
#include <fstream>

#include "log.h"

namespace mwruntime {

namespace {

// Separates a StateKey's layer from its id. Neither a plugin stem nor a TES3
// id can hold a control character, and the co-save's own separators are tabs
// and newlines.
constexpr char kKeySeparator = '\x1F';

// A TES4 record header, and a subrecord's.
constexpr std::size_t kRecordHeader = 24;
constexpr std::size_t kFieldHeader = 6;

struct Layer {
    std::string name;
    std::string lower;
    // Every OTHER layer this one is built on, through any chain of masters.
    std::vector<bool> ancestors;
    // False when the plugin's header could not be read.
    bool known = false;
    int depth = 0;
    int rank = 0;
};

std::vector<Layer> g_layers;
std::unordered_map<std::string, int> g_byName;

// Which layers define each TES3 id, in folder order.
std::unordered_map<std::string, std::vector<int>> g_idLayers;

thread_local int g_current = kEveryLayer;

std::string Lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(),
                   [](unsigned char c) { return static_cast<char>(::tolower(c)); });
    return text;
}

// "Morrowind_ob.esm" -> "morrowind_ob": a master names a FILE, a layer is
// named after the file's stem.
std::string Stem(const std::string& file) {
    const std::size_t dot = file.find_last_of('.');
    return Lower(dot == std::string::npos ? file : file.substr(0, dot));
}

std::uint32_t U32(const char* at) {
    std::uint32_t value = 0;
    std::memcpy(&value, at, sizeof(value));
    return value;
}

std::uint16_t U16(const char* at) {
    std::uint16_t value = 0;
    std::memcpy(&value, at, sizeof(value));
    return value;
}

// The MAST entries of one plugin's TES4 header, or false when the file is
// missing or is not a TES4-format plugin. An XXXX field carries the next
// field's real size, which a large ONAM needs.
bool ReadMasters(const std::string& path, std::vector<std::string>* out) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return false;
    char header[kRecordHeader] = {};
    if (!in.read(header, kRecordHeader) || std::memcmp(header, "TES4", 4) != 0) {
        return false;
    }
    std::string body(U32(header + 4), '\0');
    if (!in.read(body.data(), static_cast<std::streamsize>(body.size()))) {
        return false;
    }
    std::uint32_t nextSize = 0;
    for (std::size_t at = 0; at + kFieldHeader <= body.size();) {
        const char* field = body.data() + at;
        const std::uint32_t size = nextSize ? nextSize : U16(field + 4);
        nextSize = 0;
        at += kFieldHeader;
        if (at + size > body.size()) break;
        if (std::memcmp(field, "XXXX", 4) == 0 && size == 4) {
            nextSize = U32(body.data() + at);
        } else if (std::memcmp(field, "MAST", 4) == 0) {
            out->push_back(std::string(body.data() + at,
                                       strnlen(body.data() + at, size)));
        }
        at += size;
    }
    return true;
}

// Marks everything `layer` is built on, following each master's own masters.
void MarkAncestors(std::size_t layer,
                   const std::vector<std::vector<int>>& direct) {
    std::vector<int> pending = direct[layer];
    std::vector<bool>& seen = g_layers[layer].ancestors;
    while (!pending.empty()) {
        const int next = pending.back();
        pending.pop_back();
        if (next == static_cast<int>(layer) || seen[next]) continue;
        seen[next] = true;
        pending.insert(pending.end(), direct[next].begin(), direct[next].end());
    }
}

// Deeper layers first, then folder order.
void AssignRanks() {
    std::vector<int> order(g_layers.size());
    for (std::size_t i = 0; i < order.size(); ++i) order[i] = static_cast<int>(i);
    std::stable_sort(order.begin(), order.end(), [](int a, int b) {
        return g_layers[a].depth > g_layers[b].depth;
    });
    for (std::size_t r = 0; r < order.size(); ++r) {
        g_layers[order[r]].rank = static_cast<int>(r);
    }
}

void LogLayers() {
    for (const Layer& layer : g_layers) {
        std::string masters;
        for (std::size_t i = 0; i < g_layers.size(); ++i) {
            if (!layer.ancestors[i]) continue;
            masters += (masters.empty() ? "" : ", ") + g_layers[i].name;
        }
        Log("scope:   %s -- %s", layer.name.c_str(),
            !layer.known ? "header unread, sees every sidecar"
            : masters.empty() ? "no sidecar masters"
                              : ("built on " + masters).c_str());
    }
}

}  // namespace

void ClearLayers() {
    g_layers.clear();
    g_byName.clear();
    g_idLayers.clear();
}

int AddLayer(const std::string& plugin) {
    const std::string lower = Lower(plugin);
    const auto it = g_byName.find(lower);
    if (it != g_byName.end()) return it->second;
    Layer layer;
    layer.name = plugin;
    layer.lower = lower;
    g_layers.push_back(layer);
    const int index = static_cast<int>(g_layers.size()) - 1;
    g_byName.emplace(lower, index);
    return index;
}

int LayerIndex(const std::string& plugin) {
    const auto it = g_byName.find(Lower(plugin));
    return it == g_byName.end() ? -1 : it->second;
}

const std::string& LayerName(int layer) {
    static const std::string kNone;
    return layer >= 0 && layer < static_cast<int>(g_layers.size())
               ? g_layers[layer].name
               : kNone;
}

std::size_t LayerCount() { return g_layers.size(); }

void FinishLayersWith(
    const std::unordered_map<std::string, std::vector<std::string>>& masters) {
    std::vector<std::vector<int>> direct(g_layers.size());
    for (std::size_t i = 0; i < g_layers.size(); ++i) {
        Layer& layer = g_layers[i];
        layer.ancestors.assign(g_layers.size(), false);
        const auto it = masters.find(layer.name);
        layer.known = it != masters.end();
        if (!layer.known) continue;
        for (const std::string& master : it->second) {
            const auto found = g_byName.find(Stem(master));
            if (found != g_byName.end()) direct[i].push_back(found->second);
        }
    }
    for (std::size_t i = 0; i < g_layers.size(); ++i) {
        MarkAncestors(i, direct);
        g_layers[i].depth = static_cast<int>(std::count(
            g_layers[i].ancestors.begin(), g_layers[i].ancestors.end(), true));
    }
    AssignRanks();
    LogLayers();
}

void FinishLayers(const std::string& dataDir) {
    std::unordered_map<std::string, std::vector<std::string>> masters;
    for (const Layer& layer : g_layers) {
        for (const char* ext : {".esm", ".esp"}) {
            std::vector<std::string> list;
            if (!ReadMasters(dataDir + layer.name + ext, &list)) continue;
            masters.emplace(layer.name, std::move(list));
            break;
        }
    }
    FinishLayersWith(masters);
}

int CurrentLayer() { return g_current; }

bool LayersRelated(int a, int b) {
    if (a == b || a == kEveryLayer || b == kEveryLayer) return true;
    const int count = static_cast<int>(g_layers.size());
    if (a < 0 || b < 0 || a >= count || b >= count) return false;
    const Layer& la = g_layers[a];
    const Layer& lb = g_layers[b];
    return !la.known || !lb.known || la.ancestors[b] || lb.ancestors[a];
}

bool LayerVisible(int layer) { return LayersRelated(g_current, layer); }

int LayerRank(int layer) {
    if (layer == kEveryLayer) return INT_MIN;
    return layer >= 0 && layer < static_cast<int>(g_layers.size())
               ? g_layers[layer].rank
               : INT_MAX;
}

int LayerDepth(int layer) {
    return layer >= 0 && layer < static_cast<int>(g_layers.size())
               ? g_layers[layer].depth
               : 0;
}

LayerScope::LayerScope(int layer) : mPrevious(g_current) { g_current = layer; }

LayerScope::~LayerScope() { g_current = mPrevious; }

void NoteId(int layer, const std::string& lowerId) {
    std::vector<int>& layers = g_idLayers[lowerId];
    if (std::find(layers.begin(), layers.end(), layer) == layers.end()) {
        layers.push_back(layer);
    }
}

// The most-master visible definer; folder order breaks a tie, which only
// siblings can produce and only in the merged view.
std::string StateKey(const std::string& id) {
    const std::string lower = Lower(id);
    const auto it = g_idLayers.find(lower);
    if (it == g_idLayers.end()) return lower;
    int origin = -1;
    for (const int layer : it->second) {
        if (!LayerVisible(layer)) continue;
        if (origin < 0 || LayerDepth(layer) < LayerDepth(origin) ||
            (LayerDepth(layer) == LayerDepth(origin) && layer < origin)) {
            origin = layer;
        }
    }
    return origin < 0 ? lower
                      : g_layers[origin].lower + kKeySeparator + lower;
}

std::pair<int, std::string> SplitStateKey(const std::string& key) {
    const std::size_t at = key.find(kKeySeparator);
    if (at == std::string::npos) return {-1, key};
    return {LayerIndex(key.substr(0, at)), key.substr(at + 1)};
}

}  // namespace mwruntime
