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

namespace mwruntime {

// Why an INFO was rejected. Every filter decision is attributable, because a
// wrong answer shows the WRONG LINE rather than failing, and 69,270 responses
// cannot be diffed by reading them.
enum class Reject {
    None, Actor, Race, Class, Faction, Rank, Gender, PcFaction, PcRank,
    Cell, Disposition, Condition,
};

struct FilterResult {
    const Info* info = nullptr;
    Reject      why = Reject::None;
    // Index into the INFO's conditions when `why` is Condition, else -1.
    int         condition = -1;
};

// The first INFO in the topic whose filters all pass, or a null result.
// Order IS precedence: Morrowind takes the first match, never the best.
FilterResult SelectInfo(const Topic& topic, const ActorView& actor,
                        int choice);

// Every INFO that passes, in order. For the test harness and for barks, which
// pick at random rather than taking the first.
std::vector<const Info*> ListInfos(const Topic& topic, const ActorView& actor,
                                   int choice);

// One INFO against one actor; `why` says what rejected it.
FilterResult TestInfo(const Info& info, const ActorView& actor, int choice);

// One condition. Exposed so the harness can exercise all 85 functions
// without building a topic around each.
bool TestCondition(const Condition& cond, const ActorView& actor, int choice);

// TES3 function indices the filter answers. Names match
// esm3/dialoguecondition.hpp so the two can be read side by side.
enum Function {
    Fn_FacReactionLowest = 0, Fn_FacReactionHighest = 1,
    Fn_RankRequirement = 2, Fn_Reputation = 3, Fn_HealthPercent = 4,
    Fn_PcReputation = 5, Fn_PcLevel = 6, Fn_PcHealthPercent = 7,
    Fn_PcGender = 38, Fn_PcExpelled = 39, Fn_PcCrimeLevel = 43,
    Fn_SameSex = 44, Fn_SameRace = 45, Fn_SameFaction = 46,
    Fn_FactionRankDifference = 47, Fn_Detected = 48, Fn_Alarmed = 49,
    Fn_Choice = 50, Fn_Level = 61, Fn_Attacked = 62, Fn_TalkedToPc = 63,
    Fn_PcHealth = 64,
};

}  // namespace mwruntime
