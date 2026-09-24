// Crime: TES3's bounty is the engine's crime gold, and its guards arrest
// through the engine.
//
// PC Crime Level reads and writes the crime gold Skyrim keeps on the
// speaker's crime faction -- the realm's, when no speaker answers -- so a
// bounty the engine records for a crime is the one the guards' dialogue sees.
// PayFine / PayFineThief / GoToJail are the Faction natives the engine's own
// arrest dialogue uses.
//
// A pursuing guard opens Skyrim's dialogue menu through the import's blank
// PFGT line; when that menu's speaker is a Morrowind actor, it is closed and
// the Morrowind conversation opened with the same guard.
// See: docs/commentary/morrowind_runtime.md#crime-is-the-engines

#include <cstdint>

#include "activation.h"
#include "conversation.h"
#include "game_calls.h"
#include "game_calls_internal.h"
#include "ids.h"
#include "log.h"
#include "main_thread.h"
#include "menu.h"

namespace mwruntime {
namespace gamecalls {

namespace {

constexpr std::uint32_t kLocalMask = 0x00FFFFFF;

using GetCrimeFactionFn = void* (*)(void* vm, std::uint32_t stack, void* actor);
using GetGoldFn = std::int32_t (*)(void* vm, std::uint32_t stack, void* faction);
using SetGoldFn = void (*)(void* vm, std::uint32_t stack, void* faction,
                           std::int32_t gold);
using TwoFlagFn = void (*)(void* vm, std::uint32_t stack, void* faction,
                           bool first, bool second);
using GetSpeakerFn = bool (*)(void* manager, void** out);
using ServeTimeFn = void (*)(void* vm, std::uint32_t stack, void* tag);

GetCrimeFactionFn g_getCrimeFaction = nullptr;
GetGoldFn g_getCrimeGold = nullptr;
SetGoldFn g_setCrimeGold = nullptr;
SetGoldFn g_setCrimeGoldViolent = nullptr;
TwoFlagFn g_payCrimeGold = nullptr;
TwoFlagFn g_sendToJail = nullptr;
ServeTimeFn g_serveTime = nullptr;
GetSpeakerFn g_getSpeaker = nullptr;
void** g_topicManager = nullptr;

// The engine speaker last handed to the Morrowind menu, so one force-greet
// diverts once; cleared when the engine has no speaker.
void* g_diverted = nullptr;

// The crime faction GoToJail named, until the dialogue closes; then the one
// the player was sent to jail for, until served.
void* g_toJail = nullptr;
void* g_sentenced = nullptr;

// The speaker's crime faction, else the realm's from crime_formid.txt.
void* CrimeFaction() {
    if (g_speakerRef && g_getCrimeFaction) {
        if (void* own = g_getCrimeFaction(PapyrusVm(), 0, g_speakerRef)) return own;
    }
    const FormRef* realm = RealmCrimeFaction();
    return realm ? FormFromFile(realm->plugin.c_str(), realm->formId & kLocalMask)
                 : nullptr;
}

bool CrimeGold(float* out) {
    void* faction = CrimeFaction();
    if (!faction || !g_getCrimeGold) return false;
    *out = static_cast<float>(g_getCrimeGold(PapyrusVm(), 0, faction));
    return true;
}

void ClearAndSet(void* faction, float gold) {
    if (!faction || !g_setCrimeGold || !g_setCrimeGoldViolent) return;
    g_setCrimeGoldViolent(PapyrusVm(), 0, faction, 0);
    g_setCrimeGold(PapyrusVm(), 0, faction, static_cast<std::int32_t>(gold));
}

void SetCrimeGold(float gold) {
    void* faction = CrimeFaction();
    PostToMainThread([faction, gold]() { ClearAndSet(faction, gold); });
}

// OpenMW's PayFine: the bounty goes and the stolen goods are confiscated;
// PayFineThief only clears the bounty. The gold itself is the dialogue's own
// RemoveItem, so the engine is asked to pay what is left -- nothing.
void PayFine(bool confiscate) {
    void* faction = CrimeFaction();
    PostToMainThread([faction, confiscate]() {
        if (confiscate && faction && g_payCrimeGold) {
            ClearAndSet(faction, 0.0f);
            g_payCrimeGold(PapyrusVm(), 0, faction, true, false);
        }
        ClearAndSet(faction, 0.0f);
    });
}

// OpenMW's World::goToJail: the player goes once the dialogue has closed, so
// its text can be read first. TES3's jail keeps the player's inventory and
// serves the whole sentence at once (OpenMW's JailScreen), so the engine's
// ServeTime follows as soon as the engine has jailed the player.
void GoToJail() {
    g_toJail = CrimeFaction();
    if (!g_toJail || !g_sendToJail || !g_serveTime) {
        Log("crime: GoToJail -- no crime faction or native");
        g_toJail = nullptr;
    }
}

void ServeSentence() {
    if (g_toJail && !ConversationOpen()) {
        g_sendToJail(PapyrusVm(), 0, g_toJail, false, true);
        g_sentenced = g_toJail;
        g_toJail = nullptr;
    }
    void* player = g_sentenced ? PlayerRef() : nullptr;
    if (!player || *reinterpret_cast<void**>(static_cast<char*>(player) +
                                             ids::kOffPlayerJailFaction) != g_sentenced) {
        return;
    }
    g_sentenced = nullptr;
    Log("crime: jailed -- serving the sentence");
    g_serveTime(PapyrusVm(), 0, nullptr);
}

void DivertEngineDialogue() {
    if (!g_getSpeaker || !g_topicManager || !*g_topicManager) return;
    void* speaker = nullptr;
    if (!g_getSpeaker(*g_topicManager, &speaker) || !speaker) {
        g_diverted = nullptr;
        return;
    }
    if (speaker == g_diverted || ConversationOpen()) return;
    void* base = *reinterpret_cast<void**>(static_cast<char*>(speaker) +
                                           ids::kOffRefBase);
    const std::uint32_t baseId = FormIdOf(base);
    if (!baseId || !IsMorrowindSpeaker(baseId)) return;
    g_diverted = speaker;
    Log("crime: %s opened Skyrim's dialogue -- diverting to the Morrowind menu",
        SpeakerId(baseId));
    CloseMenuNamed(ids::kVanillaDialogueMenu);
    const LayerScope scope(SpeakerLayer(baseId));
    SetSpeakerRef(SpeakerId(baseId), speaker);
    BeginConversation(SpeakerId(baseId), DisplayName(base), PlayerName());
}

void CrimeTick() {
    DivertEngineDialogue();
    ServeSentence();
}

}  // namespace

void InstallCrimeCalls(GameHooks& hooks) {
    g_getCrimeFaction = Native<GetCrimeFactionFn>("Actor.GetCrimeFaction",
                                                  ids::kActorGetCrimeFaction);
    g_getCrimeGold = Native<GetGoldFn>("Faction.GetCrimeGold",
                                       ids::kFactionGetCrimeGold);
    g_setCrimeGold = Native<SetGoldFn>("Faction.SetCrimeGold",
                                       ids::kFactionSetCrimeGold);
    g_setCrimeGoldViolent = Native<SetGoldFn>("Faction.SetCrimeGoldViolent",
                                              ids::kFactionSetCrimeGoldViolent);
    g_payCrimeGold = Native<TwoFlagFn>("Faction.PlayerPayCrimeGold",
                                       ids::kFactionPlayerPayCrimeGold);
    g_sendToJail = Native<TwoFlagFn>("Faction.SendPlayerToJail",
                                     ids::kFactionSendPlayerToJail);
    g_serveTime = Native<ServeTimeFn>("Game.ServeTime", ids::kGameServeTime);
    g_getSpeaker = Native<GetSpeakerFn>("MenuTopicManager::GetSpeaker",
                                        ids::kGetSpeaker);
    g_topicManager = Native<void**>("MenuTopicManager singleton",
                                    ids::kMenuTopicManagerSingleton);
    hooks.crimeGold = CrimeGold;
    hooks.setCrimeGold = SetCrimeGold;
    hooks.payFine = PayFine;
    hooks.goToJail = GoToJail;
    hooks.crimeTick = CrimeTick;
}

}  // namespace gamecalls
}  // namespace mwruntime
