#include "persuasion.h"

#include <algorithm>
#include <cmath>
#include <random>
#include <set>

#include "dialogue_state.h"
#include "filter.h"
#include "log.h"
#include "script_tables.h"

namespace mwruntime {

namespace {

constexpr const char* kPlayerId = "player";

// Skyrim's actor values standing in for TES3's: Speechcraft carries both
// Speechcraft and Mercantile (the importer folds them), Stamina is fatigue.
constexpr const char* kSpeechValue = "Speechcraft";
constexpr const char* kFatigueValue = "Stamina";

// TES3 skill 25, and its SKIL use slots: Speechcraft_Success, _Fail.
constexpr int kSpeechcraftSkill = 25;
constexpr int kUseSuccess = 0;
constexpr int kUseFail = 1;

constexpr int kBribeGold[kPersuasionCount] = {0, 0, 0, 10, 100, 1000};
constexpr const char* kTopicStem[kPersuasionCount] = {
    "Admire", "Intimidate", "Taunt", "Bribe", "Bribe", "Bribe"};

// Every GMST the formula reads. None may be guessed: the table stages them.
struct Gmsts {
    float personalityMod, luckMod, reputationMod, levelMod;
    float fatigueBase, fatigueMult;
    float bribe10, bribe100, bribe1000;
    float perMinChance, perMinChange, perDieRollMult, perTempMult;
};

constexpr const char* kGmstNames[] = {
    "fPersonalityMod", "fLuckMod", "fReputationMod", "fLevelMod",
    "fFatigueBase", "fFatigueMult", "fBribe10Mod", "fBribe100Mod",
    "fBribe1000Mod", "iPerMinChance", "iPerMinChange", "fPerDieRollMult",
    "fPerTempMult"};

// One side's NpcStats as getPersuasionRatings reads them.
struct Side {
    float personality = 0, luck = 0, reputation = 0, level = 1;
    float speechcraft = 0, mercantile = 0;
    // Fatigue as a fraction of its maximum; 1 when the game cannot say.
    float fatigue = 1.0f;
};

struct Change {
    bool success = false;
    int temp = 0;
    int perm = 0;
};

std::set<std::string> g_reported;

void ReportOnce(const std::string& what) {
    if (g_reported.insert(what).second) Log("persuasion: %s", what.c_str());
}

// Fills `out`; returns the first missing name, or "".
std::string LoadGmsts(Gmsts* out) {
    float* slots[] = {&out->personalityMod, &out->luckMod, &out->reputationMod,
                      &out->levelMod, &out->fatigueBase, &out->fatigueMult,
                      &out->bribe10, &out->bribe100, &out->bribe1000,
                      &out->perMinChance, &out->perMinChange,
                      &out->perDieRollMult, &out->perTempMult};
    for (std::size_t i = 0; i < sizeof(slots) / sizeof(slots[0]); ++i) {
        const GmstDef* def = FindGmst(kGmstNames[i]);
        if (!def || def->type == 's') return kGmstNames[i];
        *slots[i] = def->number;
    }
    return std::string();
}

float Fatigue(const std::string& actor) {
    if (!Hooks().statPercent) return 1.0f;
    return std::max(0.0f, Hooks().statPercent(actor, kFatigueValue));
}

Side NpcSide(const std::string& actor) {
    Side out;
    const ActorDef* def = FindActor(actor);
    if (!def) {
        ReportOnce("'" + actor + "' has no actor row -- its stats read 0");
        return out;
    }
    out.personality = static_cast<float>(def->personality);
    out.luck = static_cast<float>(def->luck);
    out.reputation = static_cast<float>(def->reputation);
    out.level = static_cast<float>(def->level);
    out.speechcraft = static_cast<float>(def->speechcraft);
    out.mercantile = static_cast<float>(def->mercantile);
    out.fatigue = Fatigue(actor);
    return out;
}

// The player: Personality and Luck from Morrowind's own `player` record --
// Skyrim has neither -- and the rest from the running game.
// See: docs/commentary/morrowind_runtime.md#npc-stats
Side PlayerSide() {
    Side out = NpcSide(kPlayerId);
    out.reputation = static_cast<float>(State().reputation);
    if (Hooks().playerLevel) out.level = static_cast<float>(Hooks().playerLevel());
    if (Hooks().actorValue) {
        out.speechcraft = Hooks().actorValue(kPlayerId, kSpeechValue);
        out.mercantile = out.speechcraft;
    }
    return out;
}

// getPersuasionRatings.
void Ratings(const Side& s, const Gmsts& g, bool player, float* rating1,
             float* rating2, float* rating3) {
    const float persTerm = s.personality / g.personalityMod;
    const float luckTerm = s.luck / g.luckMod;
    const float repTerm = s.reputation * g.reputationMod;
    const float fatigueTerm = g.fatigueBase - g.fatigueMult * (1 - s.fatigue);
    const float levelTerm = s.level * g.levelMod;
    *rating1 = (repTerm + luckTerm + persTerm + s.speechcraft) * fatigueTerm;
    if (player) {
        *rating2 = *rating1 + levelTerm;
        *rating3 = (s.mercantile + luckTerm + persTerm) * fatigueTerm;
    } else {
        *rating2 = (levelTerm + repTerm + luckTerm + persTerm + s.speechcraft) *
                   fatigueTerm;
        *rating3 = (s.mercantile + repTerm + luckTerm + persTerm) * fatigueTerm;
    }
}

void ShiftAi(const std::string& actor, int fleeDelta, int fightDelta) {
    const int flee = State().AiSetting(actor, kAiFlee);
    const int fight = State().AiSetting(actor, kAiFight);
    State().SetAiSetting(actor, kAiFlee, std::clamp(flee + fleeDelta, 0, 100));
    State().SetAiSetting(actor, kAiFight, std::clamp(fight + fightDelta, 0, 100));
}

// Admire and the bribes share one shape: the roll against a floored target.
float AdmireOrBribe(float target, int roll, const Gmsts& g, bool* success) {
    *success = roll <= target;
    const float c = std::floor(g.perDieRollMult * (target - roll));
    return *success ? std::max(g.perMinChange, c) : c;
}

float Intimidate(float target2, int roll, const Gmsts& g,
                 const std::string& actor, bool* success, float* y) {
    *success = roll <= target2;
    const float r = roll != target2 ? std::floor(target2 - roll) : 1.0f;
    if (roll <= target2) {
        const float s = std::floor(r * g.perDieRollMult * g.perTempMult);
        ShiftAi(actor, static_cast<int>(std::max(g.perMinChange, s)),
                static_cast<int>(std::min(-g.perMinChange, -s)));
    }
    const float c = -std::abs(std::floor(r * g.perDieRollMult));
    if (*success && std::abs(c) < g.perMinChange) {
        // OpenMW's deviation: a marginal win still moves disposition.
        *y = g.perMinChange;
        return g.perMinChange;
    }
    *y = c;
    return *success ? -std::floor(c * g.perTempMult)
                    : std::floor(c * g.perTempMult);
}

float Taunt(float target1, int roll, const Gmsts& g, const std::string& actor,
            bool* success) {
    *success = roll <= target1;
    const float c = std::abs(std::floor(target1 - roll));
    if (*success) {
        const float s = c * g.perDieRollMult * g.perTempMult;
        ShiftAi(actor, std::min(-static_cast<int>(g.perMinChange),
                                static_cast<int>(-s)),
                std::max(static_cast<int>(g.perMinChange),
                         static_cast<int>(s)));
    }
    float x = std::floor(-c * g.perDieRollMult);
    if (*success && std::abs(x) < g.perMinChange) x = -g.perMinChange;
    return x;
}

float BribeMod(Persuasion type, const Gmsts& g) {
    if (type == Persuasion::Bribe10) return g.bribe10;
    if (type == Persuasion::Bribe100) return g.bribe100;
    return g.bribe1000;
}

// getPersuasionDispositionChange.
Change DispositionChange(Persuasion type, int roll, const Gmsts& g,
                         const std::string& actor) {
    float n1, n2, n3, p1, p2, p3;
    Ratings(NpcSide(actor), g, false, &n1, &n2, &n3);
    Ratings(PlayerSide(), g, true, &p1, &p2, &p3);
    const int current = State().Disposition(actor);
    const float d = 1 - 0.02f * std::abs(current - 50);
    const float target1 = std::max(g.perMinChance, d * (p1 - n1 + 50));
    const float target2 = std::max(g.perMinChance, d * (p2 - n2 + 50));
    const float target3 =
        std::max(g.perMinChance, d * (p3 - n3 + 50) + BribeMod(type, g));
    Change out;
    float x = 0, y = 0;
    if (type == Persuasion::Admire) {
        x = AdmireOrBribe(target1, roll, g, &out.success);
    } else if (type == Persuasion::Intimidate) {
        x = Intimidate(target2, roll, g, actor, &out.success, &y);
    } else if (type == Persuasion::Taunt) {
        x = Taunt(target1, roll, g, actor, &out.success);
    } else {
        x = AdmireOrBribe(target3, roll, g, &out.success);
    }
    const bool intimidate = type == Persuasion::Intimidate;
    out.temp = static_cast<int>(intimidate ? x : x * g.perTempMult);
    if (current + out.temp > 100) {
        out.temp = 100 - current;
    } else if (current + out.temp < 0) {
        out.temp = -current;
    }
    const int scaled = static_cast<int>(out.temp / g.perTempMult);
    out.perm = intimidate ? (out.success ? -scaled : static_cast<int>(y))
                          : scaled;
    return out;
}

int UniformRoll() {
    static std::mt19937 engine{std::random_device{}()};
    return std::uniform_int_distribution<int>(0, 99)(engine);
}

}  // namespace

int BribeCost(Persuasion type) { return kBribeGold[static_cast<int>(type)]; }

int PlayerGold() {
    return Hooks().goldCount ? Hooks().goldCount(kPlayerId) : 0;
}

PersuasionOutcome Persuade(Persuasion type, RollFn roll) {
    PersuasionOutcome out;
    Gmsts gmsts;
    out.missing = LoadGmsts(&gmsts);
    if (State().speaker.empty()) out.missing = "a conversation";
    if (!out.missing.empty()) {
        ReportOnce("cannot persuade without " + out.missing);
        return out;
    }
    const std::string actor = State().speaker;
    const int rolled = roll ? roll() : UniformRoll();
    const Change change = DispositionChange(type, rolled, gmsts, actor);
    State().ApplyPersuasion(change.temp, change.perm);
    const SkillDef* skill = FindSkill(kSpeechcraftSkill);
    const float credit = skill ? skill->use[change.success ? kUseSuccess
                                                             : kUseFail]
                               : 0.0f;
    if (credit > 0 && Hooks().advanceSkill) {
        Hooks().advanceSkill(kSpeechValue, credit);
    }
    const int gold = change.success ? BribeCost(type) : 0;
    if (gold && Hooks().moveGold) Hooks().moveGold(kPlayerId, actor, gold);
    out.ok = true;
    out.success = change.success;
    out.topic = std::string(kTopicStem[static_cast<int>(type)]) +
                (change.success ? " Success" : " Fail");
    out.titleGmst = "s" + std::string(kTopicStem[static_cast<int>(type)]) +
                    (change.success ? "Success" : "Fail");
    Log("persuasion: %s on '%s' rolled %d -> %s, disposition %+d now, %+d "
        "kept", out.topic.c_str(), actor.c_str(), rolled,
        change.success ? "success" : "fail", change.temp, change.perm);
    return out;
}

}  // namespace mwruntime
