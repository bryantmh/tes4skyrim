#include "activation.h"

#include <windows.h>

#include <bitset>
#include <cstdlib>
#include <cstring>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

#include "addresses.h"
#include "conversation.h"
#include "game_calls.h"
#include "ids.h"
#include "log.h"
#include "object_script.h"
#include "script_tables.h"
#include "store.h"

namespace mwruntime {

namespace {

// The index each sidecar writes: FormID=EditorID, one per line.
constexpr const char* kFileActors = "NPC__index.txt";

// Load-order slots belonging to converted Morrowind plugins. One bit test
// rejects every vanilla and Oblivion-converted actor before any map lookup.
std::bitset<256> g_pluginMask;

// FormID -> TES3 id, which is what the dialogue filter matches on.
std::unordered_map<std::uint32_t, std::string> g_speakers;

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

void*             g_vm = nullptr;
GetFormFromFileFn g_getFormFromFile = nullptr;
FixedStringFn     g_fixedString = nullptr;

// A FormID's load-order index is its high byte.
inline std::uint8_t PluginIndex(std::uint32_t formId) {
    return static_cast<std::uint8_t>(formId >> 24);
}


// One `FormID=EditorID` line, keyed by the LOCAL id. False when it is blank
// or malformed, which is skipped rather than fatal.
//
// 🛑 The index byte in the file is the one the plugin had at CONVERSION time
// and is discarded here; the runtime one is resolved per plugin by
// ResolveIndex. See docs/commentary/morrowind_runtime.md#load-order
bool AddLine(const std::string& line) {
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
    g_speakers[formId & kLocalMask] = id;
    return true;
}

// The Activate this form's type had before the swap, by the vtable the object
// itself carries at offset 0.
ActivateFn OriginalActivate(void* form) {
    if (!form) return nullptr;
    const auto it = g_originalActivate.find(
        *reinterpret_cast<void**>(form));
    return it == g_originalActivate.end() ? nullptr : it->second;
}

// Raises OnActivate on the instance this PLACEMENT runs, when it runs one.
// Silent for every other reference, which is the overwhelming majority.
void RaiseActivated(void* ref) {
    const std::uint32_t refId = FormIdOf(ref);
    if (!refId || !g_pluginMask.test(PluginIndex(refId))) return;
    ObjectScript* instance = InstanceForRef(refId);
    if (!instance) return;
    instance->Events().activated = true;
    Log("object: %s activated (%08X)", instance->Script().c_str(), refId);
}

// Our Activate. The FIRST test is one bit on the ref's load-order
// index, so a vanilla or Oblivion-converted NPC reaches the engine's own
// Activate having cost nothing measurable.
//
// Returning TRUE without calling the original is what suppresses Skyrim's
// activation entirely -- no vanilla dialogue menu, no "this person has nothing
// to say". Anything we do not claim falls through untouched.
bool ActivateHook(void* base, void* ref, void* activator, std::uint8_t unk,
                  void* object, std::int32_t count) {
    // `base` is the BASE form, which the actor index keys on; `ref` is the
    // placed instance, whose own FormID belongs to whichever plugin placed it.
    // Checking the base is what makes one indexed NPC match all its refs.
    const std::uint32_t baseId = FormIdOf(base);
    if (baseId && IsMorrowindSpeaker(baseId)) {
        Log("activation: %08X is '%s' (\"%s\") -- opening the Morrowind menu",
            baseId, SpeakerId(baseId), DisplayName(base));
        SetSpeakerRef(SpeakerId(baseId), ref);
        BeginConversation(SpeakerId(baseId), DisplayName(base), PlayerName());
        return true;
    }
    // An object running a TES3 script raises OnActivate on THIS placement and
    // still activates normally: TES3's own `Activate` inside the body is what
    // opens the container or the book.
    RaiseActivated(ref);
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

// Reads one plugin's index. `sample` receives any of its FormIDs, which is
// what ResolveIndex needs to ask the engine where the plugin now sits.
std::size_t LoadOneIndex(const std::string& dir, std::uint32_t* sample) {
    const std::string text = ReadFile(dir + kFileActors);
    if (text.empty()) return 0;
    std::size_t added = 0;
    std::size_t start = 0;
    while (start < text.size()) {
        std::size_t end = text.find('\n', start);
        if (end == std::string::npos) end = text.size();
        const std::string line = text.substr(start, end - start);
        if (AddLine(line)) {
            if (!added) {
                *sample = std::strtoul(line.c_str(), nullptr, 16) & kLocalMask;
            }
            ++added;
        }
        start = end + 1;
    }
    return added;
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
    return g_speakers.find(formId & kLocalMask) != g_speakers.end();
}

const char* SpeakerId(std::uint32_t formId) {
    const auto it = g_speakers.find(formId & kLocalMask);
    return it == g_speakers.end() ? "" : it->second.c_str();
}

std::size_t SpeakerCount() { return g_speakers.size(); }

bool SpeakerExists(const std::string& id) {
    for (const auto& entry : g_speakers) {
        if (entry.second.size() == id.size() &&
            _stricmp(entry.second.c_str(), id.c_str()) == 0) {
            return true;
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
    Log("object: %zu script instance(s) staged; each binds when the engine "
        "first hands us its reference", InstanceCount());
    return InstanceCount();
}

std::size_t LoadActorIndex() { return LoadActorIndexFrom(SidecarDir()); }

std::size_t LoadActorIndexFrom(const std::string& rootIn) {
    g_pluginMask.reset();
    g_speakers.clear();
    if (rootIn.empty()) return 0;
    g_getFormFromFile = reinterpret_cast<GetFormFromFileFn>(
        Resolve("Game.GetFormFromFile", ids::kGetFormFromFile, nullptr));
    g_fixedString = reinterpret_cast<FixedStringFn>(
        Resolve("BSFixedString ctor", ids::kBSFixedStringCtor, nullptr));
    std::string root = rootIn;
    if (root.back() != '\\' && root.back() != '/') root.push_back('\\');
    std::size_t total = 0;
    for (const std::string& plugin : SidecarPlugins(root)) {
        std::uint32_t sample = 0;
        const std::size_t added = LoadOneIndex(root + plugin + "\\", &sample);
        if (!added) {
            Log("activation:   %s/%s -> 0 actor(s)", plugin.c_str(),
                kFileActors);
            continue;
        }
        const std::uint8_t index = ResolveIndex(plugin, sample);
        if (index == 0xFF) {
            Log("activation:   %s -> %zu actor(s), but the plugin is NOT "
                "loaded (or the VM was unavailable) -- none will route",
                plugin.c_str(), added);
            continue;
        }
        g_pluginMask.set(index);
        Log("activation:   %s -> %zu actor(s) at load-order index %02X",
            plugin.c_str(), added, index);
        total += added;
    }
    return total;
}

// Swaps one type's Activate slot. False when an address did not resolve or the
// slot does not already hold what it should, which is never fatal: the other
// types still hook and that type simply keeps vanilla behaviour.
//
// 🛑 Refuse a swap whose slot does not already hold what we expect. A vtable
// steals no bytes, so the prologue hazard does not arise -- but writing the
// wrong slot would hand the engine our function for some unrelated virtual,
// which fails in a way no log would explain.
bool SwapActivate(const ids::ActivateTarget& target) {
    const std::uintptr_t vtable = Resolve(target.name, target.vtable, nullptr);
    const std::uintptr_t expected = Resolve(target.name, target.activate,
                                            nullptr);
    if (!vtable || !expected) {
        Log("activation:   %s -- address unresolved, NOT hooked", target.name);
        return false;
    }
    auto* slot = reinterpret_cast<void**>(vtable + ids::kActivateSlot);
    if (*slot != reinterpret_cast<void*>(expected)) {
        Log("activation:   %s slot holds %p, expected %p -- REFUSING",
            target.name, *slot, reinterpret_cast<void*>(expected));
        return false;
    }
    DWORD old = 0;
    if (!VirtualProtect(slot, sizeof(void*), PAGE_READWRITE, &old)) {
        Log("activation:   %s -- could not unprotect, NOT hooked", target.name);
        return false;
    }
    g_originalActivate[reinterpret_cast<void*>(vtable)] =
        reinterpret_cast<ActivateFn>(*slot);
    *slot = reinterpret_cast<void*>(&ActivateHook);
    VirtualProtect(slot, sizeof(void*), old, &old);
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

}  // namespace mwruntime
