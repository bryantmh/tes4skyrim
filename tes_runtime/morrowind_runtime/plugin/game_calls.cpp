#include "game_calls.h"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <set>
#include <string>

#include "activation.h"
#include "addresses.h"
#include "dialogue_state.h"
#include "ids.h"
#include "log.h"
#include "script_tables.h"

namespace mwruntime {

namespace {

// Papyrus natives, as the VM calls them: VM, stack id, self, then arguments.
using SetStageFn = bool (*)(void* vm, std::uint32_t stack, void* quest,
                            std::int32_t stage);
using AddItemFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                           void* item, std::int32_t count, bool silent);
using RemoveItemFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                              void* item, std::int32_t count, bool silent,
                              void* moveTo);
using ItemCountFn = std::int32_t (*)(void* vm, std::uint32_t stack, void* ref,
                                     void* item);
using GetPlayerFn = void* (*)(void* vm, std::uint32_t stack, void* tag);

// TES3's gold, and the record Skyrim keeps for the same thing.
constexpr const char* kTes3Gold = "gold_001";
constexpr std::uint32_t kSkyrimGold = 0xF;

// The id MWScript uses for the player.
constexpr const char* kPlayerId = "player";

constexpr std::uint32_t kLocalMask = 0x00FFFFFF;

SetStageFn   g_setStage = nullptr;
AddItemFn    g_addItem = nullptr;
RemoveItemFn g_removeItem = nullptr;
ItemCountFn  g_itemCount = nullptr;
GetPlayerFn  g_getPlayer = nullptr;

std::string g_speakerId;
void*       g_speakerRef = nullptr;

// Ids already reported as unresolvable, so the log names each once.
std::set<std::string> g_reported;

std::string Lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(),
                   [](unsigned char c) { return static_cast<char>(::tolower(c)); });
    return text;
}

void ReportOnce(const char* what, const std::string& id) {
    if (g_reported.insert(what + id).second) {
        Log("game: %s '%s' does not resolve in this load order", what,
            id.c_str());
    }
}

void* Form(const FormRef* ref) {
    return ref ? FormFromFile(ref->plugin.c_str(), ref->formId & kLocalMask)
               : nullptr;
}

void* ItemForm(const std::string& item) {
    if (Lower(item) == kTes3Gold) {
        return FormFromFile(ids::kSkyrimMaster, kSkyrimGold);
    }
    void* form = Form(FindItem(item));
    if (!form) ReportOnce("item", item);
    return form;
}

// The reference a command acts on: the player, or the NPC being spoken to.
void* OwnerRef(const std::string& owner) {
    if (Lower(owner) == kPlayerId) {
        return g_getPlayer ? g_getPlayer(PapyrusVm(), 0, nullptr) : nullptr;
    }
    if (g_speakerRef && Lower(owner) == Lower(g_speakerId)) return g_speakerRef;
    ReportOnce("reference", owner);
    return nullptr;
}

void SetQuestStage(const std::string& quest, int stage) {
    void* form = Form(FindQuest(quest));
    if (!form || !g_setStage) {
        ReportOnce("journal quest", quest);
        return;
    }
    const bool ok = g_setStage(PapyrusVm(), 0, form, stage);
    Log("game: SetStage %s %d -> %s", quest.c_str(), stage,
        ok ? "ok" : "refused");
}

void AddItem(const std::string& owner, const std::string& item, int count) {
    void* ref = OwnerRef(owner);
    void* form = ItemForm(item);
    if (ref && form && g_addItem) {
        g_addItem(PapyrusVm(), 0, ref, form, count, false);
    }
}

void RemoveItem(const std::string& owner, const std::string& item, int count) {
    void* ref = OwnerRef(owner);
    void* form = ItemForm(item);
    if (ref && form && g_removeItem) {
        g_removeItem(PapyrusVm(), 0, ref, form, count, false, nullptr);
    }
}

int ItemCount(const std::string& owner, const std::string& item) {
    void* ref = OwnerRef(owner);
    void* form = ItemForm(item);
    if (!ref || !form || !g_itemCount) return 0;
    return g_itemCount(PapyrusVm(), 0, ref, form);
}

template <typename Fn>
Fn Native(const char* name, std::uint64_t id) {
    return reinterpret_cast<Fn>(Resolve(name, id, nullptr));
}

}  // namespace

void InstallGameCalls() {
    g_setStage = Native<SetStageFn>("Quest.SetCurrentStageID",
                                    ids::kQuestSetCurrentStageId);
    g_addItem = Native<AddItemFn>("ObjectReference.AddItem", ids::kRefAddItem);
    g_removeItem = Native<RemoveItemFn>("ObjectReference.RemoveItem",
                                        ids::kRefRemoveItem);
    g_itemCount = Native<ItemCountFn>("ObjectReference.GetItemCount",
                                      ids::kRefGetItemCount);
    g_getPlayer = Native<GetPlayerFn>("Game.GetPlayer", ids::kGameGetPlayer);
    GameHooks& hooks = Hooks();
    hooks.setQuestStage = SetQuestStage;
    hooks.addItem = AddItem;
    hooks.removeItem = RemoveItem;
    hooks.itemCount = ItemCount;
    Log("game: %zu journal quest(s) mapped to Skyrim quests", QuestCount());
}

void SetSpeakerRef(const char* actorId, void* ref) {
    g_speakerId = actorId ? actorId : "";
    g_speakerRef = ref;
}

}  // namespace mwruntime
