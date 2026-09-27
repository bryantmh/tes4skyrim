// Mark, Recall, and Divine and Almsivi Intervention: TES3 effects Skyrim has no
// archetype for. Each converts as a script-less Script effect, and the Papyrus
// VM's own OnMagicEffectApply sink says when one lands. OpenMW applies all four
// to the PLAYER only, and only while teleporting is enabled.
// See: docs/commentary/morrowind_runtime.md#teleport-effects

#include "game_calls_internal.h"

#include <algorithm>
#include <climits>
#include <cmath>
#include <cstdlib>
#include <string>
#include <unordered_map>
#include <vector>

#include "ids.h"
#include "log.h"
#include "main_thread.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

// The four TES3 effect indices, as teleports_formid.txt names them. Each
// is Morrowind's own effect: Morroblivion's stand-in scripts are replaced by
// the compat patch, never recognized here.
enum TeleportEffect { kMark = 60, kRecall = 61, kDivine = 62, kAlmsivi = 63 };

// The marker each Intervention looks for, as markers_formid.txt names it.
constexpr const char* kDivineKind = "divine";
constexpr const char* kTempleKind = "temple";

// A TES3 exterior cell's width, which OpenMW's nearest-marker ring counts in.
constexpr float kCellSize = 8192.0f;

constexpr const char* kPlayerId = "player";

using ProcessEventFn = int (*)(void* sink, const void* event, void* source);
using RefFormFn = void* (*)(void* vm, std::uint32_t stack, void* ref);

ProcessEventFn g_originalApply = nullptr;
RefFormFn g_worldSpace = nullptr;

// Each teleport MGEF's runtime FormID -> its TES3 index. Written once at
// install, then only read, from whichever thread applies an effect.
std::unordered_map<std::uint32_t, int> g_effectIds;

// Where the nearest-marker search starts: a worldspace and a point in it.
struct Spot {
    std::uint32_t world = 0;
    float x = 0, y = 0;
};

// A staged marker whose place resolved in this load order, with the root
// worldspace its search counts it in.
struct LiveMarker {
    TeleportMarker row;
    std::uint32_t place = 0;
    std::uint32_t root = 0;
};

// Resolved on the first Intervention; the load order cannot change after.
std::vector<LiveMarker> g_markers;
std::unordered_map<std::uint32_t, Spot> g_anchors;
std::unordered_map<std::uint32_t, std::uint32_t> g_parents;
bool g_placesResolved = false;

// The worldspace a child's chain of parents ends at; `place` itself when it
// has none. Bounded, so a malformed cycle cannot hang the game.
std::uint32_t RootOf(std::uint32_t place) {
    for (int depth = 0; depth < 8; ++depth) {
        const auto it = g_parents.find(place);
        if (it == g_parents.end()) break;
        place = it->second;
    }
    return place;
}

void ResolvePlaces() {
    if (g_placesResolved) return;
    g_placesResolved = true;
    ForEachWorldParent([](const FormRef& child, const FormRef& parent) {
        const std::uint32_t c = FormIdOf(Form(&child));
        const std::uint32_t p = FormIdOf(Form(&parent));
        if (c && p && c != p) g_parents[c] = p;
    });
    ForEachTeleportMarker([](const TeleportMarker& row) {
        const std::uint32_t place = FormIdOf(Form(&row.place));
        if (place) g_markers.push_back({row, place, RootOf(place)});
    });
    ForEachCellAnchor([](const CellAnchor& row) {
        const std::uint32_t cell = FormIdOf(Form(&row.cell));
        const std::uint32_t world = FormIdOf(Form(&row.world));
        if (cell && world) g_anchors[cell] = {world, row.x, row.y};
    });
    Log("teleport: %zu marker(s), %zu interior anchor(s), %zu child world(s) "
        "resolved", g_markers.size(), g_anchors.size(), g_parents.size());
}

void* PlayerWorld() {
    void* player = PlayerRef();
    return player && g_worldSpace ? g_worldSpace(PapyrusVm(), 0, player)
                                  : nullptr;
}

// Outdoors, where the player stands; indoors, where the interior opens onto
// the world. False from an interior no door walk leads out of.
bool PlayerSpot(Spot* out) {
    if (!PlayerInInterior()) {
        out->world = FormIdOf(PlayerWorld());
        out->x = Hooks().position(kPlayerId, 0);
        out->y = Hooks().position(kPlayerId, 1);
        return out->world != 0;
    }
    const auto it = g_anchors.find(FormIdOf(PlayerCell()));
    if (it == g_anchors.end()) return false;
    *out = it->second;
    return true;
}

int GridIndex(float v) { return static_cast<int>(std::floor(v / kCellSize)); }

// How far round a ring of cells `size` across a spot on its edge lies, walking
// SW -> SE -> NE -> NW from the south-west corner.
int EdgeDistance(int column, int row, int size) {
    if (row == 0) return column;
    if (column == size) return size + row;
    if (row == size) return size * 3 - column;
    return size * 4 - row;
}

// OpenMW's getClosestMarkerFromExteriorPosition: a marker in the spot's own
// cell at once, else the markers on the smallest square ring of cells around
// it, the tie going to the first met walking the ring's edge. A walled city
// and its parent world count as one, as they share coordinates.
const LiveMarker* NearestMarker(const std::string& kind, const Spot& at) {
    const int px = GridIndex(at.x);
    const int py = GridIndex(at.y);
    const std::uint32_t root = RootOf(at.world);
    const LiveMarker* best = nullptr;
    int bestRing = INT_MAX;
    int bestEdge = INT_MAX;
    for (const LiveMarker& marker : g_markers) {
        if (marker.row.kind != kind || marker.root != root) continue;
        const int dx = GridIndex(marker.row.x) - px;
        const int dy = GridIndex(marker.row.y) - py;
        const int ring = std::max(std::abs(dx), std::abs(dy)) * 2;
        if (ring == 0) return &marker;
        const int edge = EdgeDistance(ring / 2 + dx, ring / 2 + dy, ring);
        if (ring < bestRing || (ring == bestRing && edge < bestEdge)) {
            best = &marker;
            bestRing = ring;
            bestEdge = edge;
        }
    }
    return best;
}

// A marker standing in the interior the player is in, which OpenMW's search
// finds before it looks for a way out.
const LiveMarker* MarkerInCell(const std::string& kind, std::uint32_t cell) {
    for (const LiveMarker& marker : g_markers) {
        if (marker.row.kind == kind && marker.place == cell) return &marker;
    }
    return nullptr;
}

void SendPlayer(void* place, float x, float y, float z, float zRot,
                const char* why) {
    if (!MoveInto(PlayerRef(), place, x, y, z, zRot)) {
        Log("teleport: %s -- the destination is not a cell or worldspace", why);
    }
}

void Mark() {
    void* place = PlayerInInterior() ? PlayerCell() : PlayerWorld();
    if (!place) return;
    DialogueState::MarkedPlace& mark = State().mark;
    mark = {FormIdOf(place), Hooks().position(kPlayerId, 0),
            Hooks().position(kPlayerId, 1), Hooks().position(kPlayerId, 2),
            Hooks().angle(kPlayerId, 2)};
    Log("teleport: marked %08X at (%.0f, %.0f, %.0f)", mark.place, mark.x,
        mark.y, mark.z);
}

void Recall() {
    const DialogueState::MarkedPlace& mark = State().mark;
    void* place = RefByRuntimeId(mark.place);
    if (!place) {
        Log("teleport: Recall with nothing marked");
        return;
    }
    SendPlayer(place, mark.x, mark.y, mark.z, mark.zRot, "Recall");
}

void Intervene(const char* kind) {
    ResolvePlaces();
    const LiveMarker* found = MarkerInCell(kind, FormIdOf(PlayerCell()));
    Spot at;
    if (!found && PlayerSpot(&at)) found = NearestMarker(kind, at);
    if (!found) {
        Log("teleport: no %s marker reachable from here", kind);
        return;
    }
    const TeleportMarker& row = found->row;
    SendPlayer(Form(&row.place), row.x, row.y, row.z, row.zRot, kind);
}

// Game thread. The refusal is shown only to a player who cast it, as OpenMW's.
void OnTeleportEffect(int index, bool castByPlayer) {
    if (!State().teleporting) {
        if (castByPlayer) Notify(GmstText("sTeleportDisabled", std::string()));
        return;
    }
    switch (index) {
        case kMark: Mark(); break;
        case kRecall: Recall(); break;
        case kDivine: Intervene(kDivineKind); break;
        case kAlmsivi: Intervene(kTempleKind); break;
        default: break;
    }
}

// The VM's own sink, which sees every effect applied to anything. Ours only
// notes a teleport landing on the player and moves it on the next frame.
int ApplyHook(void* sink, const void* event, void* source) {
    const char* e = static_cast<const char*>(event);
    const auto found = e ? g_effectIds.find(*reinterpret_cast<const std::uint32_t*>(
                               e + ids::kOffApplyEffect))
                         : g_effectIds.end();
    void* target = e ? *reinterpret_cast<void* const*>(e + ids::kOffApplyTarget)
                     : nullptr;
    if (found != g_effectIds.end() && target && target == PlayerRef()) {
        const int index = found->second;
        const bool byPlayer =
            *reinterpret_cast<void* const*>(e + ids::kOffApplyCaster) == target;
        PostToMainThread([index, byPlayer]() { OnTeleportEffect(index, byPlayer); });
    }
    return g_originalApply(sink, event, source);
}

}  // namespace

void InstallTeleportCalls() {
    g_worldSpace = Native<RefFormFn>("ObjectReference.GetWorldSpace",
                                     ids::kRefGetWorldSpace);
    ForEachTeleportEffect([](const FormRef& effect, int index) {
        const std::uint32_t id = FormIdOf(Form(&effect));
        if (id) g_effectIds[id] = index;
    });
    if (g_effectIds.empty() || g_originalApply) {
        Log("teleport: %zu effect(s) staged -- sink %s", g_effectIds.size(),
            g_originalApply ? "already hooked" : "NOT hooked");
        return;
    }
    g_originalApply = reinterpret_cast<ProcessEventFn>(SwapVtableSlot(
        "SkyrimVM OnMagicEffectApply sink",
        Resolve("SkyrimVM magic effect apply sink vtable",
                ids::kVmMagicEffectApplySink, nullptr),
        ids::kProcessEventSlot,
        Resolve("SkyrimVM::ProcessEvent(TESMagicEffectApplyEvent)",
                ids::kVmMagicEffectApplyProcess, nullptr),
        reinterpret_cast<void*>(&ApplyHook)));
    Log("teleport: %zu effect(s), OnMagicEffectApply sink %s",
        g_effectIds.size(), g_originalApply ? "hooked" : "NOT hooked");
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
