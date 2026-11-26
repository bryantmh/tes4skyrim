// Ownership globals: a TES3 reference may name a global that, while nonzero,
// lifts its ownership -- how an innkeeper's rented bed becomes the player's.
// Skyrim's REFR has no such field, so each rented reference is handed to the
// player with ObjectReference.SetActorOwner, as vanilla's RentRoomScript does,
// and given back to its authored owner when the global clears.
// See: docs/commentary/morrowind_runtime.md#ownership-globals

#include "game_calls_internal.h"

#include <windows.h>

#include <cstdint>
#include <vector>

#include "ids.h"
#include "log.h"
#include "scope.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

using SetOwnerFn = void (*)(void* vm, std::uint32_t stack, void* ref, void* owner);
SetOwnerFn g_setActorOwner = nullptr;
SetOwnerFn g_setFactionOwner = nullptr;

// A reference whose cell is not loaded has no form yet, so it is retried
// this often rather than every frame.
constexpr std::uint64_t kRetryMs = 500;
std::uint64_t g_nextPass = 0;

// What each OwnerGateRows() row was last set to: -1 not yet this session,
// 0 its authored owner, 1 the player. A load starts every row at -1, since
// the save holds whatever owner the reference had then.
std::vector<int> g_applied;

// Gives `ref` back to the owner the plugin authored: a faction or an NPC.
void RestoreOwner(void* ref, const OwnerGateRow& row) {
    void* owner = Form(&row.owner);
    if (!owner) return;
    const auto type = *(static_cast<const std::uint8_t*>(owner) + ids::kOffFormType);
    (type == ids::kFormTypeFaction ? g_setFactionOwner : g_setActorOwner)(
        PapyrusVm(), 0, ref, owner);
}

// Brings one row's reference in line with its global; false while its cell is not loaded.
bool Apply(const OwnerGateRow& row, int rented) {
    void* ref = Form(&row.ref);
    if (!ref) return false;
    if (rented) {
        g_setActorOwner(PapyrusVm(), 0, ref,
                        FormFromFile(ids::kSkyrimMaster, ids::kPlayerBase));
    } else {
        RestoreOwner(ref, row);
    }
    Log("owner: %s|%06X %s (%s)", row.ref.plugin.c_str(), row.ref.formId & 0x00FFFFFF,
        rented ? "rented to the player" : "back to its owner", row.global.c_str());
    return true;
}

}  // namespace

void TickOwnerGates() {
    const std::vector<OwnerGateRow>& rows = OwnerGateRows();
    if (rows.empty() || !g_setActorOwner || !g_setFactionOwner) return;
    const std::uint64_t now = GetTickCount64();
    if (now < g_nextPass) return;
    g_nextPass = now + kRetryMs;
    g_applied.resize(rows.size(), -1);
    for (std::size_t i = 0; i < rows.size(); ++i) {
        const LayerScope scope(rows[i].layer);
        const int rented = State().Global(rows[i].global) != 0.0f ? 1 : 0;
        if (g_applied[i] != rented && Apply(rows[i], rented)) g_applied[i] = rented;
    }
}

void ResetOwnerGates() {
    g_applied.assign(g_applied.size(), -1);
    g_nextPass = 0;
}

void InstallOwnerCalls() {
    g_setActorOwner = Native<SetOwnerFn>("ObjectReference.SetActorOwner",
                                         ids::kRefSetActorOwner);
    g_setFactionOwner = Native<SetOwnerFn>("ObjectReference.SetFactionOwner",
                                           ids::kRefSetFactionOwner);
    Log("game: %zu reference(s) whose ownership a global lifts", OwnerGateRows().size());
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
