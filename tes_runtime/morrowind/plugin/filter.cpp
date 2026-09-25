#include "filter.h"

#include <algorithm>
#include <cctype>
#include <vector>

#include "scope.h"
#include "script_tables.h"

namespace tesruntime::mw {

namespace {

// What worn clothing is assumed to be worth until inventories are read. Any
// positive number reads as "dressed"; the authored tests are `<= 0` (naked)
// and `>= 1`, never a real amount.
constexpr float kClothedValue = 100.0f;

// TES3 factions have ten ranks, 0..9; at the top there is nothing to qualify
// for and the requirement reads as zero.
constexpr int kTopRank = 9;

bool IEquals(const std::string& a, const std::string& b) {
    if (a.size() != b.size()) return false;
    for (std::size_t i = 0; i < a.size(); ++i) {
        if (::tolower(static_cast<unsigned char>(a[i])) !=
            ::tolower(static_cast<unsigned char>(b[i]))) {
            return false;
        }
    }
    return true;
}

// TES3 matches a cell filter as a PREFIX, which is how "Balmora" catches
// "Balmora, Guild of Fighters".
bool IStartsWith(const std::string& text, const std::string& prefix) {
    if (prefix.size() > text.size()) return false;
    for (std::size_t i = 0; i < prefix.size(); ++i) {
        if (::tolower(static_cast<unsigned char>(text[i])) !=
            ::tolower(static_cast<unsigned char>(prefix[i]))) {
            return false;
        }
    }
    return true;
}

template <typename T>
bool Compare(char op, T lhs, T rhs) {
    switch (op) {
        case '0': return lhs == rhs;
        case '1': return lhs != rhs;
        case '2': return lhs > rhs;
        case '3': return lhs >= rhs;
        case '4': return lhs < rhs;
        case '5': return lhs <= rhs;
        default:  return true;
    }
}

bool CompareValue(const Condition& cond, float value) {
    if (cond.isFloat) return Compare(cond.comparison, value, cond.valueFloat);
    return Compare(cond.comparison, static_cast<int>(value), cond.valueInt);
}

// The `Not*` functions are inverted identity tests: the rule's own comparison
// is NOT applied to them, matching SelectWrapper::Type_Inverted.
bool TestInverted(const Condition& cond, const ActorView& actor) {
    switch (cond.function) {
        case '7': return !IEquals(actor.Id(), cond.variable);
        case '8': return !IEquals(actor.PrimaryFaction(), cond.variable);
        case '9': return !IEquals(actor.Class(), cond.variable);
        case 'A': return !IEquals(actor.Race(), cond.variable);
        case 'B': return !IStartsWith(actor.PlayerCellName(), cond.variable);
        default:  return true;
    }
}

// NpcStats::hasSkillsForRank. NOT a per-named-skill test: the faction's own
// skills are sorted and the best three are measured against the row, so any
// one at primary and two more at favoured will do.
bool HasSkillsForRank(const FactionDef& faction, const RankReq& row,
                      const ActorView& actor) {
    std::vector<int> skills;
    for (int index : faction.skills) {
        if (index >= 0) skills.push_back(actor.PlayerSkill(index));
    }
    if (skills.empty()) return true;
    std::sort(skills.rbegin(), skills.rend());
    if (skills[0] < row.primarySkill) return false;
    if (skills.size() < 2) return true;
    if (skills[1] < row.favouredSkill) return false;
    if (skills.size() < 3) return true;
    return skills[2] >= row.favouredSkill;
}

// Filter::Function_RankRequirement: a two-bit answer, 1 for the stats and 2
// for the reputation, so `== 3` means EVERY requirement for the next rank is
// met. A non-member is rank -1, so joining tests row 0.
float RankRequirement(const ActorView& actor) {
    const RefId faction = actor.PrimaryFaction();
    if (faction.empty()) return 0.0f;
    const FactionDef* def = FindFaction(faction);
    if (!def) return 0.0f;
    const int rank = actor.PlayerFactionRank(faction);
    if (rank >= kTopRank) return 0.0f;
    const RankReq& row = def->ranks[rank + 1];
    int result = 0;
    if (HasSkillsForRank(*def, row, actor) &&
        actor.PlayerAttribute(def->attribute[0]) >= row.attribute1 &&
        actor.PlayerAttribute(def->attribute[1]) >= row.attribute2) {
        result += 1;
    }
    if (actor.PlayerFactionReputation(faction) >= row.reputation) result += 2;
    return static_cast<float>(result);
}

// The stats, the AI settings, and every flag whose answer is simply NO for a
// character this runtime does not model that way. Split out of NumberedValue
// so the one switch does not outgrow its own shape limit.
float PlainValue(const Condition& cond, const ActorView& actor, bool* known) {
    const int index = cond.index;
    if (index >= kFirstPcSkill && index <= kLastPcSkill) {
        return static_cast<float>(actor.PlayerSkill(index - kFirstPcSkill));
    }
    if (index >= kFirstPcAttribute && index <= kLastPcAttribute) {
        return static_cast<float>(
            actor.PlayerAttribute(index - kFirstPcAttribute));
    }
    switch (index) {
        // 🛑 Not modelled, and the answer is NO -- which is what keeps the
        // "Get away from me, vampire!" greeting off every ordinary NPC.
        case Fn_PcVampire:
        case Fn_Werewolf:
        case Fn_PcCorprus:
        case Fn_PcCommonDisease:
        case Fn_PcBlightDisease:
        case Fn_PcWerewolfKills:
        case Fn_CreatureTarget:
        case Fn_ShouldAttack:
        case Fn_FriendHit:
        case Fn_Weather:
            return 0.0f;
        // The GOLD VALUE of everything worn (OpenMW sums slots 0..15), so a
        // clothed player is far above zero and 0 means NAKED -- which is what
        // the "Cover yourself!" greeting tests with `<= 0`.
        case Fn_PcClothingModifier:
            return kClothedValue;
        // Stats the runtime does not track; the player reads as ordinary.
        case Fn_PcMagicka:
        case Fn_PcFatigue:
            return static_cast<float>(actor.PlayerHealthPercent());
        case Fn_Reputation:
        case Fn_PcReputation:
            return 0.0f;
        // The actor's AI settings, which scripts set and dialogue tests back.
        case Fn_Fight:  return static_cast<float>(actor.AiSetting(kAiFight));
        case Fn_Hello:  return static_cast<float>(actor.AiSetting(kAiHello));
        case Fn_Alarm:  return static_cast<float>(actor.AiSetting(kAiAlarm));
        case Fn_Flee:   return static_cast<float>(actor.AiSetting(kAiFlee));
        default:
            *known = false;
            return 0.0f;
    }
}

// A numbered function's value, and whether we can answer it at all.
float NumberedValue(const Condition& cond, const ActorView& actor, int choice,
                    bool* known) {
    *known = true;
    switch (cond.index) {
        case Fn_FacReactionLowest:
        case Fn_FacReactionHighest:
            return static_cast<float>(
                actor.FactionReaction(actor.PrimaryFaction(),
                                      actor.PrimaryFaction()));
        case Fn_RankRequirement:  return RankRequirement(actor);
        case Fn_HealthPercent:    return static_cast<float>(actor.Health());
        case Fn_PcLevel:          return static_cast<float>(
            actor.PlayerLevel());
        case Fn_PcHealthPercent:
        case Fn_PcHealth:         return static_cast<float>(
            actor.PlayerHealthPercent());
        case Fn_PcGender:         return actor.PlayerIsFemale() ? 0.0f : 1.0f;
        case Fn_PcCrimeLevel:     return static_cast<float>(
            actor.PlayerCrimeLevel());
        case Fn_PcExpelled:
            return actor.PlayerExpelled(actor.PrimaryFaction()) ? 1.0f : 0.0f;
        case Fn_SameSex:
            return actor.IsFemale() == actor.PlayerIsFemale() ? 1.0f : 0.0f;
        case Fn_SameRace:
            return IEquals(actor.Race(), actor.PlayerRace()) ? 1.0f : 0.0f;
        case Fn_SameFaction:
            return actor.PlayerFactionRank(actor.PrimaryFaction()) >= 0
                       ? 1.0f : 0.0f;
        case Fn_FactionRankDifference: {
            const RefId faction = actor.PrimaryFaction();
            if (faction.empty()) return 0.0f;
            return static_cast<float>(actor.PrimaryFactionRank() -
                                      actor.PlayerFactionRank(faction));
        }
        case Fn_Detected:   return actor.Detected() ? 1.0f : 0.0f;
        case Fn_Alarmed:    return actor.Alarmed() ? 1.0f : 0.0f;
        case Fn_Attacked:   return actor.Attacked() ? 1.0f : 0.0f;
        case Fn_TalkedToPc: return actor.TalkedToPlayer() ? 1.0f : 0.0f;
        case Fn_Level:      return static_cast<float>(actor.Level());
        case Fn_Choice:     return static_cast<float>(choice);
        default:
            return PlainValue(cond, actor, known);
    }
}

}  // namespace

bool TestCondition(const Condition& cond, const ActorView& actor, int choice) {
    switch (cond.function) {
        case '1': {
            // Outside a choice, EVERY condition testing one fails -- this is
            // what keeps a branch's answers hidden until it is entered.
            if (cond.index == Fn_Choice && choice == -1) return false;
            bool known = false;
            const float value = NumberedValue(cond, actor, choice, &known);
            // 🛑 An unanswerable function REJECTS. Passing it was measured
            // wrong in game: `PCVampire == 1` is index 59, had no case, and
            // so passed -- every NPC greeted the player as a vampire and said
            // goodbye. A rule this runtime cannot judge must not decide FOR a
            // response; the next INFO in the list is the right answer.
            // See: docs/commentary/morrowind_runtime.md#unknown-functions
            if (!known) return false;
            return CompareValue(cond, value);
        }
        case '2': {
            bool found = false;
            const float value = actor.GlobalVariable(cond.variable, &found);
            // A global that does not exist is ignored, not failed.
            return found ? CompareValue(cond, value) : true;
        }
        case '3': {
            bool found = false;
            const float value = actor.LocalVariable(cond.variable, &found);
            // A local that does not exist REJECTS: the script cannot answer.
            return found ? CompareValue(cond, value) : false;
        }
        case 'C': {
            bool found = false;
            const float value = actor.LocalVariable(cond.variable, &found);
            return found ? !CompareValue(cond, value) : true;
        }
        case '4':
            return CompareValue(
                cond, static_cast<float>(actor.JournalIndex(cond.variable)));
        case '5':
            return CompareValue(
                cond, static_cast<float>(actor.ItemCount(cond.variable)));
        case '6':
            return CompareValue(
                cond, static_cast<float>(actor.DeadCount(cond.variable)));
        case '7': case '8': case '9': case 'A': case 'B':
            return TestInverted(cond, actor);
        default:
            return true;
    }
}

FilterResult TestInfo(const Info& info, const ActorView& actor, int choice,
                      bool invertDisposition) {
    FilterResult out;
    out.info = &info;
    if (!LayerVisible(info.layer)) {
        out.why = Reject::Plugin;
        return out;
    }
    const bool isCreature = !actor.IsNpc();

    if (!info.actor.empty()) {
        if (!IEquals(info.actor, actor.Id())) {
            out.why = Reject::Actor;
            return out;
        }
    } else if (isCreature) {
        // A creature only ever answers topics naming it directly.
        out.why = Reject::Actor;
        return out;
    }

    if (!isCreature) {
        if (!info.race.empty() && !IEquals(info.race, actor.Race())) {
            out.why = Reject::Race;
            return out;
        }
        if (!info.clazz.empty() && !IEquals(info.clazz, actor.Class())) {
            out.why = Reject::Class;
            return out;
        }
        if (info.factionLess) {
            if (!actor.PrimaryFaction().empty()) {
                out.why = Reject::Faction;
                return out;
            }
        } else if (!info.faction.empty()) {
            if (!IEquals(info.faction, actor.PrimaryFaction())) {
                out.why = Reject::Faction;
                return out;
            }
            if (actor.PrimaryFactionRank() < info.rank) {
                out.why = Reject::Rank;
                return out;
            }
        } else if (info.rank != -1 &&
                   actor.PrimaryFactionRank() < info.rank) {
            // A rank with no faction means the speaker's own faction.
            out.why = Reject::Rank;
            return out;
        }
        // mGender is 0 male / 1 female, and the test is for the OPPOSITE.
        if (info.gender == (actor.IsFemale() ? 0 : 1)) {
            out.why = Reject::Gender;
            return out;
        }
    }

    if (!info.pcFaction.empty()) {
        const int rank = actor.PlayerFactionRank(info.pcFaction);
        if (rank < 0) {
            out.why = Reject::PcFaction;
            return out;
        }
        if (rank < info.pcRank) {
            out.why = Reject::PcRank;
            return out;
        }
    } else if (info.pcRank != -1) {
        const int rank = actor.PlayerFactionRank(actor.PrimaryFaction());
        if (rank < 0 || rank < info.pcRank) {
            out.why = Reject::PcRank;
            return out;
        }
    }

    if (!info.cell.empty() &&
        !IStartsWith(actor.PlayerCellName(), info.cell)) {
        out.why = Reject::Cell;
        return out;
    }

    // Disposition gates a topic response but never a journal entry. Service
    // Refusal inverts it: the line answers BELOW the threshold, 0 always.
    if (!isCreature && info.type != DialType::Journal) {
        const bool passes =
            invertDisposition
                ? (info.disposition == 0 ||
                   actor.Disposition() < info.disposition)
                : actor.Disposition() >= info.disposition;
        if (!passes) {
            out.why = Reject::Disposition;
            return out;
        }
    }

    for (std::size_t i = 0; i < info.conditions.size(); ++i) {
        if (TestCondition(info.conditions[i], actor, choice)) continue;
        out.why = Reject::Condition;
        out.condition = static_cast<int>(i);
        return out;
    }
    return out;
}

FilterResult SelectInfo(const Topic& topic, const ActorView& actor,
                        int choice, bool invertDisposition) {
    for (const Info& info : topic.infos) {
        FilterResult result = TestInfo(info, actor, choice, invertDisposition);
        if (result.why == Reject::None) return result;
    }
    FilterResult none;
    return none;
}

std::vector<const Info*> ListInfos(const Topic& topic, const ActorView& actor,
                                   int choice) {
    std::vector<const Info*> out;
    for (const Info& info : topic.infos) {
        if (TestInfo(info, actor, choice).why == Reject::None) {
            out.push_back(&info);
        }
    }
    return out;
}

}  // namespace tesruntime::mw
