// INFO selection: which response an actor gives for a topic.
//
// A port of MWDialogue::Filter's semantics onto ActorView. The rules are
// TES3's and are not reinterpreted; only the accessors differ, because the
// answer has to match what OpenMW would choose for the same state.
// See: docs/commentary/morrowind_runtime.md#the-filter

#pragma once

#include <string>
#include <vector>

#include "actor.h"
#include "store.h"

namespace tesruntime::mw {

// Why an INFO was rejected. Every filter decision is attributable, because a
// wrong answer shows the WRONG LINE rather than failing, and 69,270 responses
// cannot be diffed by reading them.
enum class Reject {
    None, Actor, Race, Class, Faction, Rank, Gender, PcFaction, PcRank,
    Cell, Disposition, Condition,
    // Staged by a plugin the current one cannot see (scope.h).
    Plugin,
};

struct FilterResult {
    const Info* info = nullptr;
    Reject      why = Reject::None;
    // Index into the INFO's conditions when `why` is Condition, else -1.
    int         condition = -1;
};

// The first INFO in the topic whose filters all pass, or a null result.
// Order IS precedence: Morrowind takes the first match, never the best.
// `invertDisposition` is Service Refusal's rule: the INFO answers when the
// actor's disposition is BELOW its threshold, and 0 still always answers.
FilterResult SelectInfo(const Topic& topic, const ActorView& actor,
                        int choice, bool invertDisposition = false);

// Every INFO that passes, in order. For the test harness and for barks, which
// pick at random rather than taking the first.
std::vector<const Info*> ListInfos(const Topic& topic, const ActorView& actor,
                                   int choice);

// One INFO against one actor; `why` says what rejected it.
FilterResult TestInfo(const Info& info, const ActorView& actor, int choice,
                      bool invertDisposition = false);

// One condition. Exposed so the harness can exercise all 85 functions
// without building a topic around each.
bool TestCondition(const Condition& cond, const ActorView& actor, int choice);

// TES3 function indices, in the order esm3/dialoguecondition.hpp declares
// them, so the two can be read side by side. EVERY index is named: a rule
// whose function has no name is a rule this runtime cannot judge.
enum Function {
    Fn_FacReactionLowest = 0, Fn_FacReactionHighest, Fn_RankRequirement,
    Fn_Reputation, Fn_HealthPercent, Fn_PcReputation, Fn_PcLevel,
    Fn_PcHealthPercent, Fn_PcMagicka, Fn_PcFatigue,
    // The eight attributes and 27 skills, 10..37, answered by the stat map.
    Fn_PcStrength, Fn_PcBlock, Fn_PcArmorer, Fn_PcMediumArmor,
    Fn_PcHeavyArmor, Fn_PcBluntWeapon, Fn_PcLongBlade, Fn_PcAxe, Fn_PcSpear,
    Fn_PcAthletics, Fn_PcEnchant, Fn_PcDestruction, Fn_PcAlteration,
    Fn_PcIllusion, Fn_PcConjuration, Fn_PcMysticism, Fn_PcRestoration,
    Fn_PcAlchemy, Fn_PcUnarmored, Fn_PcSecurity, Fn_PcSneak, Fn_PcAcrobatics,
    Fn_PcLightArmor, Fn_PcShortBlade, Fn_PcMarksman, Fn_PcMercantile,
    Fn_PcSpeechcraft, Fn_PcHandToHand,
    Fn_PcGender, Fn_PcExpelled, Fn_PcCommonDisease, Fn_PcBlightDisease,
    Fn_PcClothingModifier, Fn_PcCrimeLevel, Fn_SameSex, Fn_SameRace,
    Fn_SameFaction, Fn_FactionRankDifference, Fn_Detected, Fn_Alarmed,
    Fn_Choice,
    Fn_PcIntelligence, Fn_PcWillpower, Fn_PcAgility, Fn_PcSpeed,
    Fn_PcEndurance, Fn_PcPersonality, Fn_PcLuck,
    Fn_PcCorprus, Fn_Weather, Fn_PcVampire, Fn_Level, Fn_Attacked,
    Fn_TalkedToPc, Fn_PcHealth, Fn_CreatureTarget, Fn_FriendHit, Fn_Fight,
    Fn_Hello, Fn_Alarm, Fn_Flee, Fn_ShouldAttack, Fn_Werewolf,
    Fn_PcWerewolfKills = 73,
};

// Which of the four AI settings `ActorView::AiSetting` is being asked for.
// The DLL's own keys, since these are its own numbers.
constexpr int kAiFight = 0;
constexpr int kAiHello = 1;
constexpr int kAiAlarm = 2;
constexpr int kAiFlee = 3;

// The first and last of the contiguous attribute/skill runs, which map onto
// the TES3 stat indices the ActorView exposes.
constexpr int kFirstPcSkill = Fn_PcBlock;
constexpr int kLastPcSkill = Fn_PcHandToHand;
constexpr int kFirstPcAttribute = Fn_PcIntelligence;
constexpr int kLastPcAttribute = Fn_PcLuck;

}  // namespace tesruntime::mw
