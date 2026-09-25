// What a converted bark's conditions read, written where the engine can see
// it: every TES3 global and each minted bark-state GLOB on every tick, and the
// player's factions onto the converted FACTs as they change.
// See: docs/commentary/morrowind_runtime.md#published-state

#include "game_calls_internal.h"

#include <cctype>
#include <cstdint>
#include <string>
#include <unordered_map>

#include <components/interpreter/context.hpp>

#include "ids.h"
#include "log.h"
#include "main_thread.h"
#include "script_ops.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

constexpr std::uint32_t kLocalMask = 0x00FFFFFF;

// Actor.SetFactionRank(Faction, int) and Faction.SetPlayerExpelled(bool).
using SetFactionRankFn = void (*)(void* vm, std::uint32_t stack, void* actor,
                                  void* faction, std::int32_t rank);
using SetExpelledFn = void (*)(void* vm, std::uint32_t stack, void* faction,
                               bool expelled);

SetFactionRankFn g_setFactionRank = nullptr;
SetExpelledFn g_setExpelled = nullptr;

// The value slot of the GLOB a row names, resolved once. A GLOB never
// unloads, and one this load order lacks stays null instead of being asked
// for again every tick.
float* ValueSlot(const FormRef& ref) {
    static std::unordered_map<std::string, float*> slots;
    const std::uint32_t local = ref.formId & kLocalMask;
    const std::string key = Lower(ref.plugin) + '|' + std::to_string(local);
    const auto found = slots.find(key);
    if (found != slots.end()) return found->second;
    auto* form = static_cast<std::uint8_t*>(
        FormFromFile(ref.plugin.c_str(), local));
    float* slot = form ? reinterpret_cast<float*>(form + ids::kOffGlobalValue)
                       : nullptr;
    slots.emplace(key, slot);
    return slot;
}

// Only a CHANGE is written, so a GLOB whose value holds is never touched.
void Publish(const FormRef& ref, float value) {
    if (ref.plugin.empty()) return;
    float* slot = ValueSlot(ref);
    if (slot && *slot != value) *slot = value;
}

// TES3 matches a cell filter as a case-blind PREFIX of the player's cell.
bool CellMatches(const std::string& cell, const std::string& prefix) {
    if (prefix.size() > cell.size()) return false;
    for (std::size_t i = 0; i < prefix.size(); ++i) {
        if (::tolower(static_cast<unsigned char>(cell[i])) !=
            ::tolower(static_cast<unsigned char>(prefix[i]))) {
            return false;
        }
    }
    return true;
}

// One bark-state key's value now.
float StateValue(const std::string& key, const std::string& cell) {
    const std::size_t colon = key.find(':');
    const std::string kind = key.substr(0, colon);
    const std::string id =
        colon == std::string::npos ? std::string() : key.substr(colon + 1);
    if (kind == "journal") return static_cast<float>(State().JournalIndex(id));
    if (kind == "cell") return CellMatches(cell, id) ? 1.0f : 0.0f;
    if (kind == "reputation") return static_cast<float>(State().reputation);
    if (kind == "weather" && Hooks().weather) {
        return static_cast<float>(Tes3Weather(Hooks().weather()));
    }
    return 0.0f;
}

// The player's standing in one TES3 faction, onto its converted FACT.
// See: docs/commentary/morrowind_runtime.md#player-factions
void ApplyPlayerFaction(const std::string& faction, int rank, bool expelled) {
    const FormRef* ref = FindFactionForm(faction);
    if (!ref) {
        ReportOnce("faction", faction);
        return;
    }
    const std::string plugin = ref->plugin;
    const std::uint32_t local = ref->formId & kLocalMask;
    PostToMainThread([plugin, local, faction, rank, expelled]() {
        void* form = FormFromFile(plugin.c_str(), local);
        void* player = PlayerRef();
        if (!form || !player || !g_setFactionRank || !g_setExpelled) {
            Log("faction: %s not pushed -- %s", faction.c_str(),
                form ? "no player or no native" : "its FACT did not resolve");
            return;
        }
        g_setFactionRank(PapyrusVm(), 0, player, form, rank);
        g_setExpelled(PapyrusVm(), 0, form, expelled);
        Log("faction: player at rank %d%s in %s", rank,
            expelled ? ", expelled," : "", faction.c_str());
    });
}

}  // namespace

// 🛑 Every sidecar's own mirror GLOB, each read through its own plugin's
// view: a sibling plugin's global of the same name is a different value.
void PublishState() {
    ForEachGlobalRow(
        [](const std::string& name, const GlobalDef& def, int layer) {
            if (def.form.plugin.empty()) return;
            const LayerScope scope(layer);
            Publish(def.form, State().Global(name));
        });
    const std::string cell =
        Hooks().playerCell ? Hooks().playerCell() : std::string();
    for (const StateRow& row : StateRows()) {
        const LayerScope scope(row.layer);
        Publish(row.form, StateValue(row.key, cell));
    }
}

void InstallStateCalls(GameHooks& hooks) {
    g_setFactionRank = Native<SetFactionRankFn>("Actor.SetFactionRank",
                                                ids::kActorSetFactionRank);
    g_setExpelled = Native<SetExpelledFn>("Faction.SetPlayerExpelled",
                                          ids::kFactionSetPlayerExpelled);
    hooks.applyPlayerFaction = ApplyPlayerFaction;
    Log("game: %zu bark state GLOB(s) to publish", StateRows().size());
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
