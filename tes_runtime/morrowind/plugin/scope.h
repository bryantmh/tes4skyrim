// Which sidecar a row came from, and which sidecars can see each other.
//
// Every sidecar folder is a LAYER. Two layers see each other when one is a
// master of the other, directly or through a chain -- read from each plugin's
// own ESM header, the authored record of what it was built on. Two plugins
// that only SHARE a master (Arktwend and Morrowind, both built on Morroblivion)
// are siblings and never see each other's ids, dialogue or state.
//
// A lookup answers from the layers visible to the CURRENT layer, which the
// entry points set: the speaker's plugin for a conversation, the placing
// plugin for an object script, the starting plugin for a global script. No
// current layer means every layer, which is how a headless test or a load
// whose plugin headers cannot be read behaves -- exactly as one merged table.
// See: docs/commentary/morrowind_runtime.md#load-order

#pragma once

#include <cstddef>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace tesruntime::mw {

// A row every layer sees, ahead of any sidecar's: the engine's own clock
// globals, and rows a test registers directly.
constexpr int kEveryLayer = -1;

// Forgets every layer; the next load registers them again.
void ClearLayers();

// The layer for a sidecar folder, registered on first sight. Folder order is
// registration order, which breaks precedence ties between siblings.
int AddLayer(const std::string& plugin);

// The layer of that sidecar folder, or -1. Case-insensitive.
int LayerIndex(const std::string& plugin);
const std::string& LayerName(int layer);
std::size_t LayerCount();

// Reads each layer's masters from `<dataDir><plugin>.esm|.esp` and fixes who
// sees whom. A plugin whose header cannot be read sees, and is seen by, every
// layer -- the merged behaviour, never a silent loss.
void FinishLayers(const std::string& dataDir);

// The same, with each layer's masters given directly, for the tests. A layer
// absent from `masters` is one whose header could not be read.
void FinishLayersWith(
    const std::unordered_map<std::string, std::vector<std::string>>& masters);

// The layer whose view lookups use now, or kEveryLayer.
int CurrentLayer();

// Whether rows from `layer` are visible to the current layer.
bool LayerVisible(int layer);

// Whether `a` and `b` see each other: one is the other, or a master of it.
bool LayersRelated(int a, int b);

// Precedence among visible rows: a plugin overrides its masters, so a deeper
// layer answers first; siblings go in folder order. Lower answers first.
int LayerRank(int layer);

// How many of the other layers are this one's masters.
int LayerDepth(int layer);

// Makes `layer` current for its lifetime. kEveryLayer is the merged view.
class LayerScope {
public:
    explicit LayerScope(int layer);
    ~LayerScope();
    LayerScope(const LayerScope&) = delete;
    LayerScope& operator=(const LayerScope&) = delete;

private:
    int mPrevious;
};

// Records that `layer` defines the TES3 id `lowerId`, for StateKey.
void NoteId(int layer, const std::string& lowerId);

// The key a piece of saved state for `id` lives under: the id qualified by
// the most-master visible layer that defines it -- the record's ORIGIN, which
// every plugin overriding it shares -- or the bare lowercased id when no
// layer defines it. Stable whatever else is installed later.
std::string StateKey(const std::string& id);

// A StateKey back into its layer (-1 when unqualified or not loaded) and id.
std::pair<int, std::string> SplitStateKey(const std::string& key);

// One id's rows, one per layer that staged it, answered through the current
// layer's view.
template <typename T>
class LayeredTable {
public:
    // `ids`: whether the keys are TES3 ids, which StateKey resolves. A table
    // keyed by an alias name or an index is not.
    explicit LayeredTable(bool ids = true) : mIds(ids) {}

    // The first row per (key, layer) is kept, as one sidecar's table always
    // kept its first.
    void Add(int layer, const std::string& key, T value) {
        std::vector<Row>& rows = mRows[key];
        for (const Row& row : rows) {
            if (row.layer == layer) return;
        }
        rows.push_back({layer, std::move(value)});
        if (mIds && layer != kEveryLayer) NoteId(layer, key);
    }

    // Replaces this layer's row for `key`.
    void Set(int layer, const std::string& key, T value) {
        std::vector<Row>& rows = mRows[key];
        for (Row& row : rows) {
            if (row.layer == layer) {
                row.value = std::move(value);
                return;
            }
        }
        Add(layer, key, std::move(value));
    }

    const T* Find(const std::string& key) const {
        const Row* row = Best(key);
        return row ? &row->value : nullptr;
    }

    // The layer whose row answers `key`, or -1 when none is visible.
    int FindLayer(const std::string& key) const {
        const Row* row = Best(key);
        return row ? row->layer : -1;
    }

    // `fn(key, value, layer)` for the row answering each visible key.
    template <typename Fn>
    void ForEachVisible(Fn fn) const {
        for (const auto& entry : mRows) {
            const Row* row = BestOf(entry.second);
            if (row) fn(entry.first, row->value, row->layer);
        }
    }

    // `fn(key, value, layer)` for every row of every layer.
    template <typename Fn>
    void ForEachRow(Fn fn) const {
        for (const auto& entry : mRows) {
            for (const Row& row : entry.second) {
                fn(entry.first, row.value, row.layer);
            }
        }
    }

    // `fn(value, layer)` for every layer's row of one key.
    template <typename Fn>
    void ForEachOf(const std::string& key, Fn fn) const {
        const auto it = mRows.find(key);
        if (it == mRows.end()) return;
        for (const Row& row : it->second) fn(row.value, row.layer);
    }

    // Distinct keys, whichever layers stage them.
    std::size_t size() const { return mRows.size(); }
    bool empty() const { return mRows.empty(); }
    void clear() { mRows.clear(); }

private:
    struct Row {
        int layer;
        T value;
    };

    const Row* Best(const std::string& key) const {
        const auto it = mRows.find(key);
        return it == mRows.end() ? nullptr : BestOf(it->second);
    }

    static const Row* BestOf(const std::vector<Row>& rows) {
        const Row* best = nullptr;
        for (const Row& row : rows) {
            if (!LayerVisible(row.layer)) continue;
            if (!best || LayerRank(row.layer) < LayerRank(best->layer)) {
                best = &row;
            }
        }
        return best;
    }

    std::unordered_map<std::string, std::vector<Row>> mRows;
    bool mIds;
};

}  // namespace tesruntime::mw
