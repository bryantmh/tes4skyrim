#include "activation.h"

#include <bitset>
#include <cstdlib>
#include <cstring>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

#include "addresses.h"
#include "conversation.h"
#include "dialogue_state.h"
#include "game_calls.h"
#include "game_calls_internal.h"
#include "ids.h"
#include "log.h"
#include "paths.h"
#include "object_script.h"
#include "scope.h"
#include "script_tables.h"
#include "store.h"

namespace tesruntime::mw {

namespace {

// The index each sidecar writes: FormID=EditorID, one per line.
constexpr const char* kFileActors = "NPC__index.txt";

// Load-order slots belonging to converted Morrowind plugins. One bit test
// rejects every vanilla and Oblivion-converted actor before any map lookup.
std::bitset<256> g_pluginMask;

// One indexed actor: its TES3 id, which is what the dialogue filter matches
// on, the sidecar that indexed it, and that plugin's load-order slot now.
struct Speaker {
    std::string id;
    int layer = -1;
    std::uint8_t index = 0xFF;
};

// Local FormID -> every sidecar's actor with that local id. Two sibling
// plugins mint the same local id for the same authored id, so the runtime
// slot is what tells them apart.
std::unordered_map<std::uint32_t, std::vector<Speaker>> g_speakers;

bool g_installed = false;

// TESNPC::Activate(this, ref, activator, unk, object, count). Returns whether
// the engine performed the activation.
using ActivateFn = bool (*)(void* npc, void* ref, void* activator,
                            std::uint8_t unk, void* object, std::int32_t count);

// The Activate each swapped type had, keyed by that type's VTABLE -- which is
// the one thing the hook can read back off the form it is handed, since a
// single hook now serves nine types.
std::unordered_map<void*, ActivateFn> g_originalActivate;

// TESForm::formID, at +0x14 on every form.
constexpr std::size_t kOffFormId = 0x14;

// The part of a FormID that is NOT the load-order index.
constexpr std::uint32_t kLocalMask = 0x00FFFFFF;

// Game.GetFormFromFile, and the interning ctor its file argument needs.
using GetFormFromFileFn = void* (*)(void* vm, std::uint32_t stack, void* tag,
                                    std::int32_t local, void* fileName);
using FixedStringFn = void* (*)(void* out, const char* text);

// The `Actor.IsDead` Papyrus native, which IGNORES the VM and the stack: it is
// a four-instruction thunk that tail-calls the reference's own virtual --
// `mov rax,[r8] / mov dl,1 / mov rcx,r8 / jmp [rax+0x4c8]`, identical on
// 1.6.659 and 1.6.1170. So it answers from inside the Activate vtable call,
// where re-entering the VM is not an option. `Actor.IsSneaking` (a test of
// actorState1) and `Actor.GetCombatTarget` (a handle lookup) read only r8 too.
// See: docs/commentary/morrowind_runtime.md#a-corpse-is-looted-not-talked-to
using RefTestFn = bool (*)(void* vm, std::uint32_t stack, void* ref);
using CombatTargetFn = void* (*)(void* vm, std::uint32_t stack, void* actor);
// ContainerMenu's open(ref, mode), as TESNPC::Activate calls it.
using OpenContainerFn = void (*)(void* ref, std::int32_t mode);

void*             g_vm = nullptr;
GetFormFromFileFn g_getFormFromFile = nullptr;
FixedStringFn     g_fixedString = nullptr;
RefTestFn         g_isDead = nullptr;
RefTestFn         g_isSneaking = nullptr;
CombatTargetFn    g_combatTarget = nullptr;
OpenContainerFn   g_openContainer = nullptr;

// The reference a script's `Activate` is activating right now, or null.
void* g_scriptActivation = nullptr;

// The GMST OpenMW's Npc::activate shows for an actor fighting the player.
constexpr const char* kInCombatGmst = "sActorInCombat";

// A FormID's load-order index is its high byte.
inline std::uint8_t PluginIndex(std::uint32_t formId) {
    return static_cast<std::uint8_t>(formId >> 24);
}


// One `FormID=EditorID` line as (LOCAL id, TES3 id). False when it is blank
// or malformed, which is skipped rather than fatal.
//
// 🛑 The index byte in the file is the one the plugin had at CONVERSION time
// and is discarded here; the runtime one is resolved per plugin by
// ResolveIndex. See docs/commentary/morrowind_runtime.md#load-order
bool ParseLine(const std::string& line,
               std::pair<std::uint32_t, std::string>* out) {
    const std::size_t eq = line.find('=');
    if (eq == 0 || eq == std::string::npos) return false;
    const std::uint32_t formId = std::strtoul(line.substr(0, eq).c_str(),
                                              nullptr, 16);
    if (!formId) return false;
    std::string id = line.substr(eq + 1);
    while (!id.empty() && (id.back() == '\r' || id.back() == '\n')) {
        id.pop_back();
    }
    if (id.empty()) return false;
    *out = {formId & kLocalMask, id};
    return true;
}

// The speaker a live FormID names: the sidecar whose plugin holds that slot,
// else the first that indexed the local id.
const Speaker* FindSpeaker(std::uint32_t formId) {
    const auto it = g_speakers.find(formId & kLocalMask);
    if (it == g_speakers.end() || it->second.empty()) return nullptr;
    for (const Speaker& speaker : it->second) {
        if (speaker.index == PluginIndex(formId)) return &speaker;
    }
    return &it->second.front();
}

// The Activate this form's type had before the swap, by the vtable the object
// itself carries at offset 0.
ActivateFn OriginalActivate(void* form) {
    if (!form) return nullptr;
    const auto it = g_originalActivate.find(
        *reinterpret_cast<void**>(form));
    return it == g_originalActivate.end() ? nullptr : it->second;
}

// The instance this PLACEMENT runs, or null for every other reference, which
// is the overwhelming majority.
ObjectScript* ScriptInstance(void* ref) {
    const std::uint32_t refId = FormIdOf(ref);
    if (!refId || !g_pluginMask.test(PluginIndex(refId))) return nullptr;
    return InstanceForRef(refId);
}

// Whether the object's script has taken this activation, which then only
// raises `OnActivate` -- OpenMW's RefData::activate. A script's own `Activate`
// is never taken, or it could not do the default thing it exists to do.
//
// 🛑 Only the PLAYER's. Skyrim's NPCs activate every door they path through,
// and TES3 has no such thing: taken, the door's `if ( OnActivate == 1 )` would
// fire for the NPC and its `Activate` would then act as the player.
bool ScriptTakesActivation(void* ref, void* activator) {
    if (ref == g_scriptActivation) return false;
    ObjectScript* instance = ScriptInstance(ref);
    if (!instance || activator != gamecalls::PlayerRef()) return false;
    if (!instance->ActivationClaimed()) return false;
    instance->Events().activated = true;
    Log("object: %s activated (%08X) -- its script handles it",
        instance->Script().c_str(), FormIdOf(ref));
    return true;
}

// Says once per actor that its dialogue was held back. Once, because the
// player mashing activate at a tutorial NPC would otherwise fill the log.
void ReportSuppressed(std::uint32_t baseId) {
    static std::set<std::uint32_t> reported;
    if (!reported.insert(baseId).second) return;
    Log("activation: %08X is '%s' -- player controls are off, no dialogue",
        baseId, SpeakerId(baseId));
}

// Whether this PLACEMENT is a dead actor. The base form cannot answer it: life
// is state on the reference, and one base serves every corpse and every living
// copy of that NPC alike.
//
// Unknown counts as alive, so a hook that cannot reach the engine keeps the
// dialogue it has always opened rather than silently losing every speaker.
// See: docs/commentary/morrowind_runtime.md#a-corpse-is-looted-not-talked-to
bool IsCorpse(void* ref) {
    return ref && g_isDead && g_isDead(nullptr, 0, ref);
}

// Whether the actor is fighting the player -- OpenMW's
// `aiSequence.isInCombat(player)`, which is Skyrim's combat target. An actor
// fighting someone else still talks, as it does in OpenMW.
bool FightingPlayer(void* ref, void* player) {
    return player && g_combatTarget && g_combatTarget(nullptr, 0, ref) == player;
}

// Whether the actor is down -- knocked over, or knocked out -- which TES3
// reads as `getKnockedDown`. Standing is knock state 0 and life state alive.
bool KnockedDown(void* ref) {
    const std::uint32_t state = *reinterpret_cast<const std::uint32_t*>(
        static_cast<const char*>(ref) + ids::kOffActorState1);
    return (state & ids::kKnockStateMask) != 0 ||
           (state & ids::kLifeStateMask) == ids::kLifeUnconscious;
}

bool PlayerSneaking(void* player) {
    return player && g_isSneaking && g_isSneaking(nullptr, 0, player);
}

// Opens the conversation with this placement of the speaker.
void Converse(void* base, void* ref, std::uint32_t baseId) {
    Log("activation: %08X is '%s' (\"%s\") -- opening the Morrowind menu",
        baseId, SpeakerId(baseId), DisplayName(base));
    const LayerScope scope(SpeakerLayer(baseId));
    SetSpeakerRef(SpeakerId(baseId), ref);
    BeginConversation(SpeakerId(baseId), DisplayName(base), PlayerName());
}

// What activating a LIVING speaker does, in the order OpenMW's Npc::activate
// decides it. True when it is handled here; false hands it to Skyrim's own
// Activate, which is what pickpockets.
//
// Everything else is claimed either way: a speaker must not fall through to
// Skyrim's own dialogue menu just because our side declined to open.
//
// 🛑 `playercontrols` gates the player's activation, because we answer BEFORE
// the engine does: Skyrim's own abActivate flag never gets the chance to stop
// an activation we have already claimed. A script's `Activate` is not the
// player's and passes, as OpenMW's executeActivation does.
// See: docs/commentary/morrowind_runtime.md#the-control-switches
bool ActivateSpeaker(void* base, void* ref, std::uint32_t baseId) {
    if (ref != g_scriptActivation && !State().ControlEnabled(kPlayerControls)) {
        ReportSuppressed(baseId);
        return true;
    }
    void* player = gamecalls::PlayerRef();
    const bool fighting = FightingPlayer(ref, player);
    if (!fighting && KnockedDown(ref) && g_openContainer) {
        Log("activation: %08X is down -- opening its inventory", baseId);
        g_openContainer(ref, ids::kContainerLoot);
        return true;
    }
    if (!fighting && PlayerSneaking(player)) return false;
    if (fighting) {
        Log("activation: %08X is fighting the player -- no dialogue", baseId);
        gamecalls::Notify(GmstText(kInCombatGmst, ""));
        return true;
    }
    Converse(base, ref, baseId);
    return true;
}

// Our Activate. The FIRST test is one bit on the ref's load-order
// index, so a vanilla or Oblivion-converted NPC reaches the engine's own
// Activate having cost nothing measurable.
//
// Returning TRUE without calling the original is what suppresses Skyrim's
// activation entirely -- no vanilla dialogue menu, no "this person has nothing
// to say". Anything we do not claim falls through untouched, which is how a
// CORPSE reaches the engine's own Activate and opens as a container.
bool ActivateHook(void* base, void* ref, void* activator, std::uint8_t unk,
                  void* object, std::int32_t count) {
    if (ScriptTakesActivation(ref, activator)) return true;
    // `base` is the BASE form, which the actor index keys on; `ref` is the
    // placed instance, whose own FormID belongs to whichever plugin placed it.
    // Checking the base is what makes one indexed NPC match all its refs.
    const std::uint32_t baseId = FormIdOf(base);
    if (baseId && IsMorrowindSpeaker(baseId) && !IsCorpse(ref) &&
        ActivateSpeaker(base, ref, baseId)) {
        return true;
    }
    const ActivateFn original = OriginalActivate(base);
    return original ? original(base, ref, activator, unk, object, count)
                    : false;
}

// The load-order index `plugin` actually has in THIS game, by resolving one of
// its own forms through the engine, or 0xFF when it is not loaded.
//
// One call per plugin at load, not per activation: the answer cannot change
// while the game runs, and the hot path stays a single bit test.
std::uint8_t ResolveIndex(const std::string& plugin, std::uint32_t sample) {
    for (const char* ext : {".esm", ".esp"}) {
        void* form = FormFromFile((plugin + ext).c_str(), sample & kLocalMask);
        if (form) return PluginIndex(FormIdOf(form));
    }
    return 0xFF;
}

void ResolveNatives() {
    g_getFormFromFile = reinterpret_cast<GetFormFromFileFn>(
        Resolve("Game.GetFormFromFile", ids::kGetFormFromFile, nullptr));
    g_fixedString = reinterpret_cast<FixedStringFn>(
        Resolve("BSFixedString ctor", ids::kBSFixedStringCtor, nullptr));
    g_isDead = reinterpret_cast<RefTestFn>(
        Resolve("Actor.IsDead", ids::kActorIsDead, nullptr));
    g_isSneaking = reinterpret_cast<RefTestFn>(
        Resolve("Actor.IsSneaking", ids::kActorIsSneaking, nullptr));
    g_combatTarget = reinterpret_cast<CombatTargetFn>(
        Resolve("Actor.GetCombatTarget", ids::kActorCombatTarget, nullptr));
    g_openContainer = reinterpret_cast<OpenContainerFn>(
        Resolve("ContainerMenu open", ids::kOpenContainerMenu, nullptr));
}

// Reads one plugin's index as (local id, TES3 id) rows.
std::vector<std::pair<std::uint32_t, std::string>> ReadOneIndex(
    const std::string& dir) {
    std::vector<std::pair<std::uint32_t, std::string>> rows;
    const std::string text = ReadFile(dir + kFileActors);
    std::size_t start = 0;
    while (start < text.size()) {
        std::size_t end = text.find('\n', start);
        if (end == std::string::npos) end = text.size();
        std::pair<std::uint32_t, std::string> row;
        if (ParseLine(text.substr(start, end - start), &row)) {
            rows.push_back(std::move(row));
        }
        start = end + 1;
    }
    return rows;
}

}  // namespace

std::uint32_t FormIdOf(void* form) {
    if (!form) return 0;
    return *reinterpret_cast<std::uint32_t*>(
        reinterpret_cast<char*>(form) + kOffFormId);
}

void SetPapyrusVm(void* vm) { g_vm = vm; }

void* PapyrusVm() { return g_vm; }

bool FixedString(void** out, const char* text) {
    *out = nullptr;
    if (!g_fixedString) return false;
    g_fixedString(out, text);
    return *out != nullptr;
}

void* FormFromFile(const char* file, std::uint32_t local) {
    void* name = nullptr;
    if (!g_vm || !g_getFormFromFile || !FixedString(&name, file)) return nullptr;
    return g_getFormFromFile(g_vm, 0, nullptr, static_cast<std::int32_t>(local),
                             &name);
}

const char* DisplayName(void* npc) {
    if (!npc) return "";
    const char* name = *reinterpret_cast<const char**>(
        static_cast<char*>(npc) + ids::kOffNpcFullName);
    return name ? name : "";
}

const char* PlayerName() {
    return DisplayName(FormFromFile(ids::kSkyrimMaster, ids::kPlayerBaseFormId));
}

bool IsMorrowindSpeaker(std::uint32_t formId) {
    if (!g_pluginMask.test(PluginIndex(formId))) return false;
    return FindSpeaker(formId) != nullptr;
}

const char* SpeakerId(std::uint32_t formId) {
    const Speaker* speaker = FindSpeaker(formId);
    return speaker ? speaker->id.c_str() : "";
}

int SpeakerLayer(std::uint32_t formId) {
    const Speaker* speaker = FindSpeaker(formId);
    return speaker ? speaker->layer : kEveryLayer;
}

std::size_t SpeakerCount() {
    std::size_t count = 0;
    for (const auto& entry : g_speakers) count += entry.second.size();
    return count;
}

bool SpeakerExists(const std::string& id) {
    for (const auto& entry : g_speakers) {
        for (const Speaker& speaker : entry.second) {
            if (!LayerVisible(speaker.layer)) continue;
            if (speaker.id.size() == id.size() &&
                _stricmp(speaker.id.c_str(), id.c_str()) == 0) {
                return true;
            }
        }
    }
    return false;
}

// Drops any bindings a previous session left and reports what is staged.
//
// 🛑 It resolves NOTHING here. `Game.GetFormFromFile` only answers for a form
// the engine has LOADED, and only 139 of TR_Mainland's 15,540 placements are
// persistent -- the rest do not exist until their cell does. Instances bind
// lazily, from the live reference an engine hook already holds.
// See: docs/plans/morrowind_object_scripts.md#only-persistent-refs-exist
std::size_t BindInstances() {
    ClearInstanceBindings();
    Log("object: %zu script instance(s) staged; each binds when its reference "
        "is in the world", InstanceCount());
    return InstanceCount();
}

std::size_t LoadActorIndex() { return LoadActorIndexFrom(SidecarDir()); }

// Whether the folder's own plugin answers for one of its base records: an
// actor from the index, else any quest, object, item, faction, GLOB or
// apparatus row naming the plugin's own file. True when the folder names no
// such record, which leaves nothing to prove it absent.
//
// 🛑 Never the actor index ALONE. A folder without one -- an incomplete
// sidecar, a TES4 plugin's apparatus table, a leftover from an old release --
// used to count as loaded unasked, and its staged placements then cost a
// failing `GetFormFromFile` each, which the engine reports to the Papyrus log,
// 256 a tick for as long as the sweep runs.
// See: docs/commentary/morrowind_runtime.md#load-order
bool ProvesLoaded(const std::string& dir, const std::string& plugin) {
    const std::uint32_t sample =
        std::strtoul(ReadFile(dir + kFileActors).c_str(), nullptr, 16);
    if (sample) return ResolveIndex(plugin, sample) != 0xFF;
    OwnForm own;
    if (!FindOwnForm(dir, plugin, &own)) return true;
    return FormFromFile(own.file.c_str(), own.formId & kLocalMask) != nullptr;
}

// Asked once per folder and remembered: the load order cannot change while
// the game runs. An engine that cannot be asked yet counts as loaded -- only a
// proven miss is dropped.
bool SidecarPluginLoaded(const std::string& root, const std::string& plugin) {
    static std::unordered_map<std::string, bool> known;
    const auto it = known.find(plugin);
    if (it != known.end()) return it->second;
    if (!g_getFormFromFile) ResolveNatives();
    if (!g_vm || !g_getFormFromFile || !g_fixedString) return true;
    const bool loaded = ProvesLoaded(root + plugin + "\\", plugin);
    if (!loaded) {
        Log("store:   %s is staged for a plugin NOT in this load order -- "
            "skipped", plugin.c_str());
    }
    known.emplace(plugin, loaded);
    return loaded;
}

std::size_t LoadActorIndexFrom(const std::string& rootIn) {
    g_pluginMask.reset();
    g_speakers.clear();
    if (rootIn.empty()) return 0;
    if (!g_getFormFromFile) ResolveNatives();
    std::string root = rootIn;
    if (root.back() != '\\' && root.back() != '/') root.push_back('\\');
    std::size_t total = 0;
    for (const std::string& plugin : SidecarPlugins(root)) {
        const auto rows = ReadOneIndex(root + plugin + "\\");
        if (rows.empty()) {
            Log("activation:   %s/%s -> 0 actor(s)", plugin.c_str(),
                kFileActors);
            continue;
        }
        const std::uint8_t index = ResolveIndex(plugin, rows.front().first);
        const int layer = AddLayer(plugin);
        for (const auto& row : rows) {
            g_speakers[row.first].push_back({row.second, layer, index});
        }
        if (index == 0xFF) {
            Log("activation:   %s -> %zu actor(s), but the plugin is NOT "
                "loaded (or the VM was unavailable) -- none will route",
                plugin.c_str(), rows.size());
            continue;
        }
        g_pluginMask.set(index);
        Log("activation:   %s -> %zu actor(s) at load-order index %02X",
            plugin.c_str(), rows.size(), index);
        total += rows.size();
    }
    return total;
}

// Swaps one type's Activate slot. False when it did not swap, which is never
// fatal: the other types still hook and that type keeps vanilla behaviour.
bool SwapActivate(const ids::ActivateTarget& target) {
    const std::uintptr_t vtable = Resolve(target.name, target.vtable, nullptr);
    void* original = SwapVtableSlot(
        target.name, vtable, ids::kActivateSlot,
        Resolve(target.name, target.activate, nullptr),
        reinterpret_cast<void*>(&ActivateHook));
    if (!original) return false;
    g_originalActivate[reinterpret_cast<void*>(vtable)] =
        reinterpret_cast<ActivateFn>(original);
    return true;
}

// 🛑 One swap PER TYPE: Activate is overridden, so the NPC vtable reaches only
// actors and an object script sits on a BOOK or a WEAP as often as on an NPC.
// See: docs/plans/morrowind_object_scripts.md#activate-is-per-type
bool InstallActivation() {
    if (g_speakers.empty() && !InstanceCount()) {
        Log("activation: no actor index and no script instances -- NOT hooked");
        return false;
    }
    std::size_t hooked = 0;
    for (const ids::ActivateTarget& target : ids::kActivateTargets) {
        if (SwapActivate(target)) ++hooked;
    }
    g_installed = hooked > 0;
    Log("activation: hooked %zu of %zu Activate slot(s); %zu speaker(s) across "
        "%zu plugin slot(s), %zu script instance(s)", hooked,
        sizeof(ids::kActivateTargets) / sizeof(ids::kActivateTargets[0]),
        g_speakers.size(), g_pluginMask.count(), InstanceCount());
    return g_installed;
}

bool ActivationInstalled() { return g_installed; }

ScriptActivationScope::ScriptActivationScope(void* ref)
    : mPrevious(g_scriptActivation) {
    g_scriptActivation = ref;
}

ScriptActivationScope::~ScriptActivationScope() {
    g_scriptActivation = mPrevious;
}

}  // namespace tesruntime::mw
