#include "guns.h"

#include <intrin.h>

#include <cstdlib>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include "addresses.h"
#include "engine.h"
#include "fire.h"
#include "hook.h"
#include "ids.h"
#include "json.h"
#include "log.h"

namespace tesruntime {

namespace {

constexpr int kGunHandType = 13;
// The engine's interned animation string table (id 11437): iRightHandType
// sits at +0x390, iLeftHandType at +0x388 (0x1dfc20 on 1.6.659).
constexpr std::size_t kTableRightHandType = 0x390;

struct GunProfile {
    int cls = 0, reload = 0, attack = 0, clipSize = 0, automatic = 0;
};

struct PendingForm {
    std::uint32_t local = 0;
    std::string file;
};

struct PendingGun {
    std::uint32_t local = 0;
    std::string file;
    GunProfile profile;
    std::vector<PendingForm> ammo;
    std::string drySound;
    float sightFov = 0.0f;
};

// The actor's active BShkbAnimationGraph, read the way tools/live/graph_vars.py
// reads it: AIProcess +8 MiddleHighProcess, +0x1a8 BSAnimationGraphManager,
// whose BSTSmallArray of graphs sits at +0x40 (capacity|local flag), +0x48
// (inline element or heap pointer), +0x50 (size).
constexpr std::size_t kActorProcessOffset = 0xf8;
constexpr std::size_t kMiddleHighOffset = 0x8;
constexpr std::size_t kGraphManagerOffset = 0x1a8;
constexpr std::size_t kGraphsCapacityOffset = 0x40;
constexpr std::size_t kGraphsDataOffset = 0x48;
constexpr std::size_t kGraphsSizeOffset = 0x50;

using HandTypeFn = int (*)(void* form);
using SetVarIntFn = bool (*)(void* graph, void* name, int value);
using SetVarFloatFn = bool (*)(void* graph, void* name, float value);
using TableFn = void* (*)();

HandTypeFn  g_origHandType = nullptr;
SetVarIntFn g_origSetVar = nullptr;
SetVarFloatFn g_setVarFloat = nullptr;
TableFn     g_table = nullptr;

std::vector<PendingGun> g_pending;
std::unordered_map<void*, GunProfile> g_guns;
void* g_lastGun = nullptr;
// Callers that keep the vanilla answer: the attack IDLE tree conditions on
// GetEquippedItemType == 12 to send crossbowAttackStart, and scripts expect
// the vanilla range.
std::uintptr_t g_vanillaCallers[2] = {0, 0};

bool WantsVanillaType(std::uintptr_t ret) {
    for (std::uintptr_t fn : g_vanillaCallers) {
        if (fn && ret > fn && ret < fn + ids::kEquippedItemTypeSpan) return true;
    }
    return false;
}

// Interned once; the graph setter takes the BSFixedString by reference.
std::unique_ptr<FixedString> g_varNames[5];
const char* const kVarNames[5] = {"iGunClass", "iGunReload", "iGunAttack",
                                  "iGunClipSize", "iGunAuto"};

void LoadSidecar(const std::string& name, const Json& doc) {
    if (doc["version"].asInt() != 1) {
        Log("guns: %s has version %d, want 1", name.c_str(), doc["version"].asInt());
        return;
    }
    int n = 0;
    for (const auto& kv : doc["guns"].fields()) {
        PendingGun g;
        g.local = static_cast<std::uint32_t>(std::strtoul(kv.first.c_str(), nullptr, 16));
        g.file = kv.second["file"].asString();
        g.profile.cls = kv.second["class"].asInt();
        g.profile.reload = kv.second["reload"].asInt();
        g.profile.attack = kv.second["attack"].asInt(-1);
        g.profile.clipSize = kv.second["clip_size"].asInt();
        g.profile.automatic = kv.second["auto"].asInt();
        if (g.profile.attack < 0) g.profile.attack = 0;
        g.drySound = kv.second["dry_sound"].asString();
        g.sightFov = static_cast<float>(kv.second["sight_fov"].asNumber());
        for (const Json& a : kv.second["ammo"].items()) {
            PendingForm f;
            f.local = static_cast<std::uint32_t>(std::strtoul(a["id"].asString().c_str(), nullptr, 16));
            f.file = a["file"].asString();
            g.ammo.push_back(std::move(f));
        }
        g_pending.push_back(std::move(g));
        ++n;
    }
    Log("guns: %s: %d guns", name.c_str(), n);
}

int HandTypeHook(void* form) {
    const int v = g_origHandType(form);
    if (!form || g_guns.empty()) return v;
    if (At<std::uint8_t>(form, kFormType) != kFormTypeWeapon) return v;
    auto it = g_guns.find(form);
    if (it == g_guns.end()) return v;
    if (WantsVanillaType(reinterpret_cast<std::uintptr_t>(_ReturnAddress()))) return v;
    g_lastGun = form;
    return kGunHandType;
}

bool SetVarHook(void* graph, void* name, int value) {
    const bool r = g_origSetVar(graph, name, value);
    if (value != kGunHandType || !g_lastGun || !name) return r;
    void* table = g_table();
    if (!table || At<void*>(name, 0) != At<void*>(table, kTableRightHandType)) return r;
    auto it = g_guns.find(g_lastGun);
    if (it == g_guns.end()) return r;
    const GunProfile& p = it->second;
    const int values[5] = {p.cls, p.reload, p.attack, p.clipSize, p.automatic};
    for (int i = 0; i < 5; ++i) {
        if (!g_varNames[i]) g_varNames[i].reset(new FixedString(kVarNames[i]));
        g_origSetVar(graph, &g_varNames[i]->ptr, values[i]);
    }
    return r;
}

}  // namespace

bool InstallGuns() {
    ForEachSidecar("guns.json", LoadSidecar);
    if (g_pending.empty()) {
        Log("guns: no gun sidecars; routing not installed");
        return false;
    }
    const std::uintptr_t handType = Resolve("GetHandAnimType", ids::kHandAnimType, nullptr);
    const std::uintptr_t setVar = Resolve("BShkbAnimationGraph::SetVariableInt", ids::kGraphSetVariableInt, nullptr);
    const std::uintptr_t setFloat = Resolve("BShkbAnimationGraph::SetVariableFloat", ids::kGraphSetVariableFloat, nullptr);
    const std::uintptr_t table = Resolve("AnimStringTable", ids::kAnimStringTable, nullptr);
    if (!handType || !setVar || !setFloat || !table) {
        Log("guns: an address is unresolved; routing not installed");
        return false;
    }
    g_origHandType = reinterpret_cast<HandTypeFn>(handType);
    g_origSetVar = reinterpret_cast<SetVarIntFn>(setVar);
    g_setVarFloat = reinterpret_cast<SetVarFloatFn>(setFloat);
    g_table = reinterpret_cast<TableFn>(table);
    g_vanillaCallers[0] = Resolve("GetEquippedItemType(condition)", ids::kEquippedItemTypeCondition, nullptr);
    g_vanillaCallers[1] = Resolve("GetEquippedItemType(papyrus)", ids::kEquippedItemTypeNative, nullptr);
    if (!g_vanillaCallers[0]) {
        Log("guns: the GetEquippedItemType condition is unresolved; routing not installed");
        return false;
    }
    const int a = PatchAllCalls(handType, reinterpret_cast<void*>(&HandTypeHook), "GetHandAnimType");
    const int b = PatchAllCalls(setVar, reinterpret_cast<void*>(&SetVarHook), "SetVariableInt");
    return a > 0 && b > 0 && InstallFire();
}

void ResolveGunForms() {
    int n = 0;
    for (const PendingGun& g : g_pending) {
        void* form = FormFromFile(g.local, g.file);
        if (!form) continue;
        g_guns[form] = g.profile;
        std::vector<void*> ammo;
        for (const PendingForm& a : g.ammo) {
            if (void* f = FormFromFile(a.local, a.file)) ammo.push_back(f);
        }
        RegisterGunAmmo(form, std::move(ammo), g.drySound, g.sightFov, g.profile.clipSize);
        ++n;
    }
    Log("guns: %d of %zu guns resolved", n, g_pending.size());
}

// Applies `set(graph, name)` to every graph of the actor; the successes.
template <class Set>
int ForEachGraph(void* actor, const char* name, Set set) {
    void* process = At<void*>(actor, kActorProcessOffset);
    void* middleHigh = process ? At<void*>(process, kMiddleHighOffset) : nullptr;
    void* manager = middleHigh ? At<void*>(middleHigh, kGraphManagerOffset) : nullptr;
    if (!manager) return 0;
    const std::int32_t size = At<std::int32_t>(manager, kGraphsSizeOffset);
    const bool local = (At<std::uint32_t>(manager, kGraphsCapacityOffset) & 0x80000000u) != 0;
    void* slot = static_cast<char*>(manager) + kGraphsDataOffset;
    void* graphs = local ? slot : At<void*>(slot, 0);
    FixedString var(name);
    int n = 0;
    for (std::int32_t i = 0; graphs && i < size; ++i) {
        void* graph = At<void*>(graphs, i * sizeof(void*));
        if (graph && set(graph, &var.ptr)) ++n;
    }
    return n;
}

int SetActorGraphInt(void* actor, const char* name, int value) {
    return ForEachGraph(actor, name, [value](void* g, void** n) { return g_origSetVar(g, n, value); });
}

int SetActorGraphFloat(void* actor, const char* name, float value) {
    return ForEachGraph(actor, name, [value](void* g, void** n) { return g_setVarFloat(g, n, value); });
}

}  // namespace tesruntime
