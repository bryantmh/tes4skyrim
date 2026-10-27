// BecomeWerewolf, UndoWerewolf and IsWerewolf on Skyrim's own beast form.
//
// The PLAYER changes the way Beast Form changes them: PlayerWerewolfQuest's
// start-up stage takes the UI and the camera, and WerewolfChangeFX plays the
// transformation and swaps the race (WerewolfTransformVisual, then stage 1).
// Its stage 100 is ShiftBack, which puts every one of those back. What TES3
// leaves to the script that asked -- when to change back -- Skyrim times
// itself, so the shift-back day is held ahead of the clock until UndoWerewolf.
//
// An NPC takes the same spell, which is how Skyrim's own werewolf encounters
// change, and SetRace(None) gives it back the race it was made with.

#include "game_calls_internal.h"

#include <cstdint>
#include <cstdio>
#include <string>

#include "ids.h"
#include "log.h"
#include "main_thread.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

// Actor.SetRace(Race), a MEMBER function: the actor is `self`.
using SetRaceFn = void (*)(void* vm, std::uint32_t stack, void* actor, void* race);

SetRaceFn g_setRace = nullptr;

// Skyrim.esm's PlayerWerewolfQuest, WerewolfChangeFX, and the two GLOBs the
// quest's OnUpdate compares to decide it is time to change back.
constexpr std::uint32_t kPlayerWerewolfQuest = 0x0002BA16;
constexpr std::uint32_t kWerewolfChangeFx = 0x000F8208;
constexpr std::uint32_t kShiftBackTime = 0x0002BA19;
constexpr std::uint32_t kGameDaysPassed = 0x00000039;

// PlayerWerewolfQuest's ShiftBack stage.
constexpr int kShiftBackStage = 100;

// How far ahead of the clock, in game days, the shift-back day is held, and
// how close it may come before it is pushed out again.
constexpr float kHoldDays = 1.0f;
constexpr float kRenewDays = 0.5f;

// The co-saved owner of each changed actor's flag, keyed by runtime FormID.
constexpr const char* kWerewolfOwner = "werewolf";

std::string KeyOf(void* ref) {
    char key[16];
    std::snprintf(key, sizeof key, "%08X", FormIdOf(ref));
    return key;
}

bool Flagged(void* ref) {
    return ref && State().Var(kWerewolfOwner, KeyOf(ref)) > 0.0f;
}

void* PlayerQuest() { return FormFromFile(ids::kSkyrimMaster, kPlayerWerewolfQuest); }

void ChangePlayer(void* player, void* fx, bool on) {
    void* quest = PlayerQuest();
    if (!quest) {
        Log("werewolf: PlayerWerewolfQuest %08X missing from %s", kPlayerWerewolfQuest,
            ids::kSkyrimMaster);
        return;
    }
    PostToMainThread([player, fx, quest, on]() {
        if (!on) {
            SetStage(quest, kShiftBackStage, false);
            return;
        }
        bool justStarted = false;
        if (StartQuest(quest, &justStarted)) CastForm(fx, player, player);
    });
}

void ChangeNpc(void* npc, void* fx, bool on) {
    if (on) {
        CastForm(fx, npc, npc);
    } else if (g_setRace) {
        PostToMainThread([npc]() { g_setRace(PapyrusVm(), 0, npc, nullptr); });
    }
}

// `BecomeWerewolf` / `UndoWerewolf`. A second ask in the same state is
// ignored, as TES3's setWerewolf returns when the flag already matches.
void SetWerewolf(const std::string& actor, bool on) {
    void* ref = OwnerRef(actor);
    void* fx = FormFromFile(ids::kSkyrimMaster, kWerewolfChangeFx);
    if (!ref || !IsActorRef(ref) || !fx || Flagged(ref) == on) return;
    State().SetVar(kWerewolfOwner, KeyOf(ref), on ? 1.0f : 0.0f);
    Log("werewolf: %s %s", actor.c_str(), on ? "becomes a werewolf" : "turns back");
    if (ref == PlayerRef()) {
        ChangePlayer(ref, fx, on);
    } else {
        ChangeNpc(ref, fx, on);
    }
}

bool IsWerewolf(const std::string& actor) { return Flagged(OwnerRef(actor)); }

// Keeps PlayerWerewolfQuest's shift-back day ahead of the clock while TES3
// has the player changed.
void HoldBeastForm() {
    if (!Flagged(PlayerRef())) return;
    float* back = GlobalSlot(ids::kSkyrimMaster, kShiftBackTime);
    const float* days = GlobalSlot(ids::kSkyrimMaster, kGameDaysPassed);
    if (back && days && *back < *days + kRenewDays) *back = *days + kHoldDays;
}

}  // namespace

void InstallWerewolfCalls(GameHooks& hooks) {
    g_setRace = Native<SetRaceFn>("Actor.SetRace", ids::kActorSetRace);
    hooks.setWerewolf = SetWerewolf;
    hooks.isWerewolf = IsWerewolf;
    hooks.werewolfTick = HoldBeastForm;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
