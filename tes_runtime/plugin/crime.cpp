#include "crime.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "addresses.h"
#include "engine.h"
#include "hook.h"
#include "ids.h"
#include "json.h"
#include "log.h"

namespace tesruntime {

namespace {

// Seconds between two re-points. A jail only matters at the moment of an
// arrest, and walking to the next town takes far longer than this.
constexpr int kTickMs = 2000;

// A form named by (file, local id), resolved at DataLoaded.
struct FileLocal {
    std::string file;
    std::uint32_t local = 0;
};

struct PendingJail {
    FileLocal marker, chest, world;
    float x = 0, y = 0;
};

struct PendingAnchor {
    FileLocal cell, world;
    float x = 0, y = 0;
};

struct Jail {
    void* marker = nullptr;
    void* chest = nullptr;
    std::uint32_t root = 0;
    float x = 0, y = 0;
};

struct Anchor {
    std::uint32_t root = 0;
    float x = 0, y = 0;
};

using GetPlayerFn = void* (*)(void* vm, std::uint32_t stack, void* tag);
using RefFormFn = void* (*)(void* vm, std::uint32_t stack, void* ref);
using RefFloatFn = float (*)(void* vm, std::uint32_t stack, void* ref);
using RefBoolFn = bool (*)(void* vm, std::uint32_t stack, void* ref);

struct Natives {
    GetPlayerFn player = nullptr;
    RefFormFn parentCell = nullptr;
    RefFormFn worldSpace = nullptr;
    RefFloatFn posX = nullptr;
    RefFloatFn posY = nullptr;
    RefBoolFn isDisabled = nullptr;
    RefBoolFn isInterior = nullptr;
};

Natives g_n;
std::vector<FileLocal> g_pendingPools;
std::vector<PendingJail> g_pendingJails;
std::vector<PendingAnchor> g_pendingAnchors;
std::vector<std::pair<FileLocal, FileLocal>> g_pendingRoots;

std::vector<void*> g_pools;
std::vector<Jail> g_jails;
std::unordered_map<std::uint32_t, std::uint32_t> g_rootOf;
std::unordered_map<std::uint32_t, Anchor> g_anchors;
std::atomic<bool> g_running{false};
std::atomic<bool> g_queued{false};

FileLocal ReadFileLocal(const Json& pair, std::size_t first = 0) {
    FileLocal out;
    if (pair.size() >= first + 2) {
        out.file = pair.at(first).asString();
        out.local = pair.at(first + 1).asU32();
    }
    return out;
}

void LoadSidecar(const std::string& name, const Json& doc) {
    for (const Json& pool : doc["pools"].items()) g_pendingPools.push_back(ReadFileLocal(pool));
    for (const Json& row : doc["world_roots"].items()) {
        g_pendingRoots.emplace_back(ReadFileLocal(row, 0), ReadFileLocal(row, 2));
    }
    for (const Json& row : doc["anchors"].items()) {
        PendingAnchor a{ReadFileLocal(row, 0), ReadFileLocal(row, 2)};
        a.x = static_cast<float>(row.at(4).asNumber());
        a.y = static_cast<float>(row.at(5).asNumber());
        g_pendingAnchors.push_back(a);
    }
    for (const Json& j : doc["jails"].items()) {
        PendingJail p{ReadFileLocal(j["marker"]), ReadFileLocal(j["chest"]),
                      ReadFileLocal(j["world"])};
        p.x = static_cast<float>(j["x"].asNumber());
        p.y = static_cast<float>(j["y"].asNumber());
        g_pendingJails.push_back(p);
    }
    Log("crime: %s -- %zu faction(s), %zu jail(s), %zu anchor(s)", name.c_str(),
        doc["pools"].size(), doc["jails"].size(), doc["anchors"].size());
}

void* ResolveForm(const FileLocal& ref) {
    return ref.file.empty() ? nullptr : FormFromFile(ref.local, ref.file);
}

std::uint32_t IdOf(void* form) { return form ? At<std::uint32_t>(form, kFormID) : 0; }

// The root worldspace a worldspace belongs to; itself when it has no parent.
std::uint32_t RootOf(std::uint32_t world) {
    const auto it = g_rootOf.find(world);
    return it == g_rootOf.end() ? world : it->second;
}

template <typename T>
bool Bind(T& slot, const char* name, std::uint64_t id) {
    slot = reinterpret_cast<T>(Resolve(name, id, nullptr));
    return slot != nullptr;
}

bool BindNatives() {
    bool ok = Bind(g_n.player, "Game.GetPlayer", ids::kGetPlayer);
    ok &= Bind(g_n.parentCell, "ObjectReference.GetParentCell", ids::kRefGetParentCell);
    ok &= Bind(g_n.worldSpace, "ObjectReference.GetWorldSpace", ids::kRefGetWorldSpace);
    ok &= Bind(g_n.posX, "ObjectReference.GetPositionX", ids::kRefGetPositionX);
    ok &= Bind(g_n.posY, "ObjectReference.GetPositionY", ids::kRefGetPositionY);
    ok &= Bind(g_n.isDisabled, "ObjectReference.IsDisabled", ids::kRefIsDisabled);
    ok &= Bind(g_n.isInterior, "Cell.IsInterior", ids::kCellIsInterior);
    return ok;
}

// Where the player stands, as (root worldspace, x, y); false when unknown.
// Inside, that is the spot the interior opens onto, from the sidecar.
bool PlayerSpot(void* player, std::uint32_t* root, float* x, float* y) {
    void* vm = g_api.vm;
    void* cell = g_n.parentCell(vm, 0, player);
    if (!cell) return false;
    if (g_n.isInterior(vm, 0, cell)) {
        const auto it = g_anchors.find(IdOf(cell));
        if (it == g_anchors.end()) return false;
        *root = it->second.root;
        *x = it->second.x;
        *y = it->second.y;
        return true;
    }
    void* world = g_n.worldSpace(vm, 0, player);
    if (!world) return false;
    *root = RootOf(IdOf(world));
    *x = g_n.posX(vm, 0, player);
    *y = g_n.posY(vm, 0, player);
    return true;
}

const Jail* NearestJail(std::uint32_t root, float x, float y) {
    const Jail* best = nullptr;
    float bestD = 0;
    for (const Jail& j : g_jails) {
        if (j.root != root || g_n.isDisabled(g_api.vm, 0, j.marker)) continue;
        const float dx = j.x - x, dy = j.y - y;
        const float d = dx * dx + dy * dy;
        if (!best || d < bestD) {
            best = &j;
            bestD = d;
        }
    }
    return best;
}

void PointFactions(const Jail& jail) {
    for (void* pool : g_pools) {
        if (At<void*>(pool, ids::kFactionJail) == jail.marker) continue;
        At<void*>(pool, ids::kFactionJail) = jail.marker;
        At<void*>(pool, ids::kFactionWait) = jail.marker;
        if (jail.chest) {
            At<void*>(pool, ids::kFactionStolen) = jail.chest;
            At<void*>(pool, ids::kFactionInventory) = jail.chest;
        }
        Log("crime: faction %08X -> jail %08X", IdOf(pool), IdOf(jail.marker));
    }
}

void Update() {
    if (!g_api.vm || g_pools.empty()) return;
    void* player = g_n.player(g_api.vm, 0, nullptr);
    std::uint32_t root = 0;
    float x = 0, y = 0;
    if (!player || !PlayerSpot(player, &root, &x, &y)) return;
    const Jail* jail = NearestJail(root, x, y);
    if (jail) PointFactions(*jail);
}

using ServeTimeFn = void (*)(void* player);
using PayCrimeGoldFn = void (*)(void* player, void* faction, bool goToJail, bool removeStolen);
ServeTimeFn g_serveTime = nullptr;

// The engine's own confiscation, run once the sentence is served.
class ConfiscateTask : public TaskDelegate {
public:
    explicit ConfiscateTask(void* faction) : faction_(faction) {}
    void Run() override {
        void* player = g_n.player(g_api.vm, 0, nullptr);
        if (!player) return;
        VCall<PayCrimeGoldFn>(player, ids::kVtPayCrimeGold)(player, faction_, false, true);
        Log("crime: sentence served -- stolen goods kept by faction %08X", IdOf(faction_));
    }
    void Dispose() override { delete this; }

private:
    void* faction_;
};

// ServeTime hands back the whole player-inventory chest, which is the same
// evidence chest the stolen goods went into; both source games keep those.
// See: docs/commentary/tes_runtime_crime.md#serve-time
void ServeTimeHook(void* player) {
    void* faction = At<void*>(player, ids::kPlayerJailFaction);
    g_serveTime(player);
    const bool fading = At<std::uint8_t>(player, ids::kPlayerServeFlags) & ids::kServeFadePending;
    if (faction && !fading &&
        std::find(g_pools.begin(), g_pools.end(), faction) != g_pools.end()) {
        RunOnMainThread(new ConfiscateTask(faction));
    }
}

class UpdateTask : public TaskDelegate {
public:
    void Run() override {
        g_queued = false;
        Update();
    }
    void Dispose() override { delete this; }
};

void TickThread() {
    while (g_running) {
        std::this_thread::sleep_for(std::chrono::milliseconds(kTickMs));
        if (g_queued.exchange(true)) continue;
        RunOnMainThread(new UpdateTask());
    }
}

}  // namespace

bool LoadCrimeSidecars() {
    ForEachSidecar("crime.json", LoadSidecar);
    return !g_pendingPools.empty() && !g_pendingJails.empty();
}

void ResolveCrimeForms() {
    if (!BindNatives()) {
        Log("crime: a native is unresolved; jails stay where the plugins put them");
        g_pools.clear();
        return;
    }
    for (const auto& row : g_pendingRoots) {
        const std::uint32_t world = IdOf(ResolveForm(row.first));
        const std::uint32_t root = IdOf(ResolveForm(row.second));
        if (world && root) g_rootOf[world] = root;
    }
    for (const FileLocal& p : g_pendingPools) {
        if (void* pool = ResolveForm(p)) g_pools.push_back(pool);
    }
    for (const PendingAnchor& a : g_pendingAnchors) {
        const std::uint32_t cell = IdOf(ResolveForm(a.cell));
        const std::uint32_t world = IdOf(ResolveForm(a.world));
        if (cell && world) g_anchors[cell] = Anchor{RootOf(world), a.x, a.y};
    }
    for (const PendingJail& p : g_pendingJails) {
        Jail j{ResolveForm(p.marker), ResolveForm(p.chest), RootOf(IdOf(ResolveForm(p.world))), p.x, p.y};
        if (j.marker && j.root) g_jails.push_back(j);
    }
    Log("crime: %zu faction(s), %zu of %zu jail(s), %zu anchor(s) resolved",
        g_pools.size(), g_jails.size(), g_pendingJails.size(), g_anchors.size());
    if (!g_pools.empty() && !g_serveTime) {
        auto* vt = reinterpret_cast<void**>(Resolve("PlayerCharacter vtable", ids::kPlayerVtable, nullptr));
        g_serveTime = reinterpret_cast<ServeTimeFn>(PatchVtableSlot(
            vt, ids::kVtServeTime, reinterpret_cast<void*>(&ServeTimeHook), "ServeTime"));
    }
}

void StartCrimeTick() {
    if (g_running || g_pools.empty() || g_jails.empty()) return;
    g_running = true;
    std::thread(TickThread).detach();
    Log("crime: jail tick started every %d ms", kTickMs);
}

}  // namespace tesruntime
