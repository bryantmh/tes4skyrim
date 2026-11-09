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

#include <windows.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "hook.h"

#include "activation.h"
#include "addresses.h"
#include "conversation.h"
#include "game_calls.h"
#include "game_calls_internal.h"
#include "ids.h"
#include "log.h"
#include "main_thread.h"
#include "menu.h"
#include "ui_message.h"

namespace tesruntime::mw {
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
    void* speaker = SpeakerRef();
    if (speaker && g_getCrimeFaction) {
        if (void* own = g_getCrimeFaction(PapyrusVm(), 0, speaker)) return own;
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

// One realm's bounty, for the Statistics tab.
bool FactionCrimeGold(const std::string& plugin, std::uint32_t formId, float* out) {
    void* faction = FormFromFile(plugin.c_str(), formId & kLocalMask);
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
    RunOnGameThread([faction, gold]() { ClearAndSet(faction, gold); });
}

// OpenMW's PayFine: the bounty goes and the stolen goods are confiscated;
// PayFineThief only clears the bounty. The gold itself is the dialogue's own
// RemoveItem, so the engine is asked to pay what is left -- nothing.
void PayFine(bool confiscate) {
    void* faction = CrimeFaction();
    RunOnGameThread([faction, confiscate]() {
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
    if (!player || At<void*>(player, PlayerField(ids::kOffPlayerJailFaction)) != g_sentenced) {
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

// TESFaction::SetPlayerEnemy (24505, 0x3ada80 on 1.6.1170): the faction-wide flag that makes every member treat
// the player as an Enemy.
using SetPlayerEnemyFn = void (*)(void* faction, bool enemy);
SetPlayerEnemyFn g_setPlayerEnemy = nullptr;

// The alarm reaction (40076, 0x71ab20) calls SetPlayerEnemy on the reacting actor's crime faction at +0x4FE
// when its target is NOT the player -- no crime, no gold.
constexpr std::uint64_t kAlarmReaction = 40076;
constexpr std::uintptr_t kAlarmPlayerEnemyCall = 0x4FE;
constexpr std::uint64_t kSetPlayerEnemy = 24505;

// Skyrim.esm, Update.esm and the three DLCs always hold load-order slots 0..4; 0xFE is light plugins.
constexpr std::uint32_t kLastSkyrimMasterSlot = 4;
constexpr std::uint32_t kLightSlot = 0xFE;

// A faction from a converted plugin: a full slot after Skyrim's own masters.
bool ConvertedForm(std::uint32_t formId) {
    const std::uint32_t slot = formId >> 24;
    return slot > kLastSkyrimMasterSlot && slot < kLightSlot;
}

// Neither TES3 nor TES4 has a faction-wide alarm: a household fighting a creature never turns its house on the
// player. Skyrim's own factions keep the engine's rule.
void AlarmPlayerEnemyHook(void* faction, bool enemy) {
    if (faction && ConvertedForm(FormIdOf(faction))) return;
    g_setPlayerEnemy(faction, enemy);
}

// Repoints the alarm reaction's non-player call, after checking it is `call SetPlayerEnemy`.
void InstallAlarmPlayerEnemy() {
    const std::uintptr_t setter = Resolve("TESFaction::SetPlayerEnemy", kSetPlayerEnemy, nullptr);
    const std::uintptr_t alarm = Resolve("AlarmReaction", kAlarmReaction, nullptr);
    if (!setter || !alarm) return;
    g_setPlayerEnemy = reinterpret_cast<SetPlayerEnemyFn>(setter);
    const std::uintptr_t call = alarm + kAlarmPlayerEnemyCall;
    const auto* bytes = reinterpret_cast<const std::uint8_t*>(call);
    const std::int32_t rel = *reinterpret_cast<const std::int32_t*>(call + 1);
    if (bytes[0] != 0xE8 || call + 5 + rel != setter) {
        Log("crime: alarm reaction's SetPlayerEnemy call not found -- converted factions keep Skyrim's alarm");
        return;
    }
    if (PatchCall(call, reinterpret_cast<void*>(&AlarmPlayerEnemyHook), "alarm player enemy")) {
        Log("crime: an alarm against someone else no longer makes a converted faction the player's enemy");
    }
}

void TempSetPlayerEnemyHook(void* faction, bool enemy) {
    void* frames[6] = {};
    const USHORT n = CaptureStackBackTrace(1, 6, frames, nullptr);
    const auto base = reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr));
    char where[128] = {};
    for (USHORT i = 0; i < n; ++i) {
        const std::size_t used = std::strlen(where);
        std::snprintf(where + used, sizeof where - used, " %llX",
                      static_cast<unsigned long long>(reinterpret_cast<std::uintptr_t>(frames[i]) - base));
    }
    Log("crime: TEMP faction %08X player enemy -> %d; stack%s", faction ? FormIdOf(faction) : 0, enemy, where);
    g_setPlayerEnemy(faction, enemy);
}

void TempInstallSetPlayerEnemyLog() {
    const std::uintptr_t fn = Resolve("TESFaction::SetPlayerEnemy", kSetPlayerEnemy, nullptr);
    if (!fn) return;
    g_setPlayerEnemy = reinterpret_cast<SetPlayerEnemyFn>(fn);
    Log("crime: TEMP %d calls of SetPlayerEnemy logged",
        PatchAllCalls(fn, reinterpret_cast<void*>(&TempSetPlayerEnemyHook), "set player enemy"));
}

}  // namespace

std::string TempCrimeNote(void* actor) {
    void* faction = actor && g_getCrimeFaction ? g_getCrimeFaction(PapyrusVm(), 0, actor) : nullptr;
    const int gold = faction && g_getCrimeGold ? g_getCrimeGold(PapyrusVm(), 0, faction) : -1;
    void* hostile = DispositionForm("hostile");
    void* disposition = DispositionForm("disposition");
    void* aggression = DispositionForm("aggression");
    char out[256];
    std::snprintf(out, sizeof out,
                  "crime faction %08X gold %d (flags %08X, enemy since %g, alarm %g), hostile rank %d, relationship "
                  "to player %d, disposition rank %d, aggression rank %d",
                  faction ? FormIdOf(faction) : 0, gold, faction ? At<std::uint32_t>(faction, 0x58) : 0,
                  faction ? At<float>(faction, 0xFC) : 0.0f, faction ? At<float>(faction, 0xF8) : 0.0f,
                  hostile ? FactionRank(actor, hostile) : -9,
                  TempRelationRank(actor, PlayerRef()), disposition ? FactionRank(actor, disposition) : -9,
                  aggression ? FactionRank(actor, aggression) : -9);
    return out;
}

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
    InstallAlarmPlayerEnemy();
    TempInstallSetPlayerEnemyLog();
    hooks.crimeGold = CrimeGold;
    hooks.factionCrimeGold = FactionCrimeGold;
    hooks.setCrimeGold = SetCrimeGold;
    hooks.payFine = PayFine;
    hooks.goToJail = GoToJail;
    hooks.crimeTick = CrimeTick;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
