#include "filter.h"

#include <algorithm>
#include <cctype>

namespace mwruntime {

namespace {

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
        case Fn_RankRequirement:  return static_cast<float>(
            actor.PrimaryFactionRank());
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
            *known = false;
            return 0.0f;
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
            // An unimplemented function must not silently reject a line: TES3
            // has no such state, so passing keeps the response reachable.
            if (!known) return true;
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

FilterResult TestInfo(const Info& info, const ActorView& actor, int choice) {
    FilterResult out;
    out.info = &info;
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

    // Disposition gates a topic response but never a journal entry.
    if (!isCreature && info.type != DialType::Journal &&
        actor.Disposition() < info.disposition) {
        out.why = Reject::Disposition;
        return out;
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
                        int choice) {
    for (const Info& info : topic.infos) {
        FilterResult result = TestInfo(info, actor, choice);
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

}  // namespace mwruntime
