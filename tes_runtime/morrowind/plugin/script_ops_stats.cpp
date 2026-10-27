// The stat commands: Get/Set/Mod for the eight attributes, the 27 skills and
// the 24 magic-effect magnitudes, plus GetLevel -- 177 commands, one class.
//
// A stat that Skyrim HAS is Skyrim's: the command reads and writes the actor
// value, so what a script sets is what the engine acts on. A stat Skyrim has
// no value for is the DLL's own number, starting at what the NPC_ record
// authors, so a script that sets one and a script that reads it still agree.
// See: docs/commentary/morrowind_runtime.md#stat-commands

#include <algorithm>
#include <cstring>
#include <string>

#include <components/compiler/opcodes.hpp>
#include <components/interpreter/opcodes.hpp>

#include "actor_stats.h"
#include "dialogue_state.h"
#include "object_script.h"
#include "script_ops.h"
#include "script_tables.h"

namespace tesruntime::mw {

namespace {

// One stat: the Skyrim actor value that carries it, or null for none, and
// the second value a skill Skyrim split in two also reads (the higher wins;
// writes go to the first).
// See: docs/plans/character_sheet.md#bug-blade-blunt
struct Stat {
    const char* name;
    const char* skyrim;
    const char* also = nullptr;
};

// OpenMW's own order, which is the opcode offset and NPC_.txt's column order.
constexpr Stat kAttributes[Compiler::Stats::numberOfAttributes] = {
    {"strength", nullptr},  {"intelligence", nullptr}, {"willpower", nullptr},
    {"agility", nullptr},   {"speed", nullptr},        {"endurance", nullptr},
    {"personality", nullptr}, {"luck", nullptr}};

// 🛑 The weapon and armor folds are the IMPORT's, so a command reads the same
// value the converted NPC was given. Mercantile is Speechcraft because that
// is what persuasion and the fare formula already read for it.
constexpr Stat kSkills[Compiler::Stats::numberOfSkills] = {
    {"block", "Block"},             {"armorer", "Smithing"},
    {"mediumarmor", "HeavyArmor", "LightArmor"},  {"heavyarmor", "HeavyArmor"},
    {"bluntweapon", "OneHanded", "TwoHanded"},    {"longblade", "OneHanded", "TwoHanded"},
    {"axe", "OneHanded", "TwoHanded"},            {"spear", "TwoHanded"},
    {"athletics", nullptr},         {"enchant", "Enchanting"},
    {"destruction", "Destruction"}, {"alteration", "Alteration"},
    {"illusion", "Illusion"},       {"conjuration", "Conjuration"},
    {"mysticism", "Alteration"},    {"restoration", "Restoration"},
    {"alchemy", "Alchemy"},         {"unarmored", "LightArmor"},
    {"security", "Lockpicking"},    {"sneak", "Sneak"},
    {"acrobatics", nullptr},        {"lightarmor", "LightArmor"},
    {"shortblade", "OneHanded"},    {"marksman", "Marksman"},
    {"mercantile", "Speechcraft"},  {"speechcraft", "Speechcraft"},
    {"handtohand", "OneHanded"}};

constexpr Stat kEffects[Compiler::Stats::numberOfMagicEffects] = {
    {"resistmagicka", "MagicResist"},   {"resistfire", "FireResist"},
    {"resistfrost", "FrostResist"},     {"resistshock", "ElectricResist"},
    {"resistdisease", "DiseaseResist"}, {"resistblight", nullptr},
    {"resistcorprus", nullptr},         {"resistpoison", "PoisonResist"},
    {"resistparalysis", nullptr},       {"resistnormalweapons", nullptr},
    {"waterbreathing", "WaterBreathing"}, {"chameleon", nullptr},
    {"waterwalking", "WaterWalking"},   {"swimspeed", nullptr},
    {"superjump", nullptr},             {"flying", nullptr},
    {"armorbonus", "DamageResist"},     {"castpenalty", nullptr},
    {"silence", nullptr},               {"blindness", nullptr},
    {"paralysis", "Paralysis"},         {"invisible", "Invisibility"},
    {"attackbonus", nullptr},           {"defendbonus", nullptr}};

// Which authored column a stat starts at, for the ones the DLL owns.
enum class Family { Attribute, Skill, Effect };

// The owner the DLL's own stats are kept under: beside the script locals, so
// in the co-save with them, and per PLACEMENT where the actor has one.
std::string StatOwner(const std::string& actor) {
    return "stat|" + LocalsOwner(actor);
}

float Authored(const std::string& actor, Family family, int index) {
    const ActorDef* def = FindActor(actor);
    if (!def) return 0.0f;
    if (family == Family::Attribute) {
        return static_cast<float>(def->attributes[index]);
    }
    return family == Family::Skill ? static_cast<float>(def->skills[index])
                                   : 0.0f;
}

// The character sheet's [CharacterSheet] Enabled and SkillCap, set once at
// install.
bool g_sheetOn = true;
bool g_skillCapOn = true;

constexpr const char* kPlayerId = "player";
constexpr int kPersonality = 6;

// With the sheet off the player has no attributes of its own: each reads as
// a full 100, so no TES3 gate shuts on one, and Personality reads Skyrim's
// Speech, which persuasion and barter weigh it beside.
// See: docs/commentary/morrowind_runtime.md#sheet-off
constexpr float kSheetOffAttribute = 100.0f;

bool IsPlayer(const std::string& actor) {
    return actor.size() == 6 && _strnicmp(actor.c_str(), kPlayerId, 6) == 0;
}

float SheetOffAttribute(int index) {
    if (index != kPersonality || !Hooks().actorValue) return kSheetOffAttribute;
    return Hooks().actorValue(kPlayerId, "Speechcraft");
}

// What a stat holds before active magic moves it: what a Set wrote, else the
// authored column, or the Skyrim actor value that carries it -- its current
// value, or with `trueBase` the base a write moves.
float ReadBase(const std::string& actor, const Stat& stat, Family family,
               int index, bool trueBase = false) {
    if (stat.skyrim) {
        const auto read = trueBase ? Hooks().baseActorValue : Hooks().actorValue;
        if (!read) return 0.0f;
        const float first = read(actor, stat.skyrim);
        return stat.also ? std::max(first, read(actor, stat.also)) : first;
    }
    const std::string owner = StatOwner(actor);
    return State().HasVar(owner, stat.name) ? State().Var(owner, stat.name)
                                            : Authored(actor, family, index);
}

// What a stat reads as: an attribute follows the sheet's rule -- for the
// player 100 when it is off; with it on, any actor's base moved by active
// magic, never below 0.
float ReadStat(const std::string& actor, const Stat& stat, Family family,
               int index) {
    if (family != Family::Attribute) return ReadBase(actor, stat, family, index);
    if (!g_sheetOn && IsPlayer(actor)) return SheetOffAttribute(index);
    const float base = ReadBase(actor, stat, family, index);
    if (!g_sheetOn || !Hooks().attributeEffect) return base;
    return std::max(0.0f, base + Hooks().attributeEffect(actor, index));
}

void WriteStat(const std::string& actor, const Stat& stat, float value) {
    if (!stat.skyrim) {
        State().SetVar(StatOwner(actor), stat.name, value);
    } else if (Hooks().setActorValue) {
        Hooks().setActorValue(actor, stat.skyrim, value);
    }
}

enum class Verb { Get, Set, Mod };

// One command of one family. `integer` is the magic-effect family, whose
// values the compiler types as long rather than float.
template <class R>
class OpStat : public Interpreter::Opcode0 {
public:
    OpStat(const Stat* stat, Family family, int index, Verb verb)
        : mStat(*stat), mFamily(family), mIndex(index), mVerb(verb) {}

private:
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const bool integer = mFamily == Family::Effect;
        if (mVerb == Verb::Get) {
            const float now = ReadStat(actor, mStat, mFamily, mIndex);
            if (integer) {
                runtime.push(static_cast<int>(now));
            } else {
                runtime.push(now);
            }
            return;
        }
        const float operand = integer ? static_cast<float>(PopInt(runtime))
                                      : PopFloat(runtime);
        // An effect is a magnitude on top of what the engine derives, so it
        // moves the modifier, and Set makes the TOTAL the operand, as OpenMW's
        // OpSetMagicEffect does.
        if (integer && mStat.skyrim && Hooks().modActorValue) {
            const float now = ReadStat(actor, mStat, mFamily, mIndex);
            Hooks().modActorValue(actor, mStat.skyrim,
                                  mVerb == Verb::Mod ? operand : operand - now);
            return;
        }
        // Otherwise a write moves the base: a Fortify active now must not be baked in.
        const float base = ReadBase(actor, mStat, mFamily, mIndex, true);
        WriteStat(actor, mStat, mVerb == Verb::Mod ? base + operand : operand);
    }

    Stat mStat;
    Family mFamily;
    int mIndex;
    Verb mVerb;
};

// `GetLevel`: any actor's level, which Skyrim has for every one of them.
template <class R>
class OpGetLevel : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        runtime.push(Hooks().level ? Hooks().level(actor) : 1);
    }
};

// `RaiseRank` / `LowerRank`: the TARGET's rank in its OWN primary faction,
// which is the only faction an NPC_ record carries -- not the player's, which
// is `PCRaiseRank`. LowerRank stops at 0 rather than resigning.
//
// The player needs no special case: it has no NPC_ record, so `FindActor`
// answers null and the faction guard already returns.
// See: docs/commentary/morrowind_runtime.md#npc-rank
template <class R, int Delta>
class OpChangeRank : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const ActorDef* def = FindActor(actor);
        if (!def || def->faction.empty()) return;
        const int rank = State().ActorRank(actor);
        if (Delta < 0 && rank <= 0) return;
        State().SetActorRank(actor, rank + Delta);
    }
};

// The six opcode bases of one family, in Get, Set, Mod order, each bare then
// explicit.
struct FamilyCodes {
    int bare[3];
    int withRef[3];
};

void InstallFamily(OpcodeInstaller& into, const Stat* stats, int count,
                   Family family, const FamilyCodes& codes) {
    constexpr Verb kVerbs[3] = {Verb::Get, Verb::Set, Verb::Mod};
    for (int i = 0; i < count; ++i) {
        for (int v = 0; v < 3; ++v) {
            into.Real<OpStat<Implicit>>(codes.bare[v] + i, &stats[i], family, i,
                                        kVerbs[v]);
            into.Real<OpStat<Explicit>>(codes.withRef[v] + i, &stats[i], family,
                                        i, kVerbs[v]);
        }
    }
}

// One family's stat by TES3 index, bounds-checked: the filter passes an index
// straight off a condition, so an out-of-range one must not read past the
// table.
float StatByIndex(const std::string& actor, const Stat* stats, int count,
                  Family family, int index) {
    if (index < 0 || index >= count) return 0.0f;
    return ReadStat(actor, stats[index], family, index);
}

}  // namespace

float ActorSkill(const std::string& actor, int tes3Index) {
    return StatByIndex(actor, kSkills, Compiler::Stats::numberOfSkills,
                       Family::Skill, tes3Index);
}

void SkyrimSkillsOf(int tes3Index, const char** first, const char** second) {
    const bool known = tes3Index >= 0 && tes3Index < Compiler::Stats::numberOfSkills;
    *first = known ? kSkills[tes3Index].skyrim : nullptr;
    *second = known ? kSkills[tes3Index].also : nullptr;
}

float ActorAttribute(const std::string& actor, int tes3Index) {
    return StatByIndex(actor, kAttributes,
                       Compiler::Stats::numberOfAttributes, Family::Attribute,
                       tes3Index);
}

float ActorBaseAttribute(const std::string& actor, int tes3Index) {
    if (tes3Index < 0 || tes3Index >= Compiler::Stats::numberOfAttributes) return 0.0f;
    return ReadBase(actor, kAttributes[tes3Index], Family::Attribute, tes3Index);
}

void SetActorAttribute(const std::string& actor, int tes3Index, float value) {
    if (tes3Index < 0 || tes3Index >= Compiler::Stats::numberOfAttributes) return;
    WriteStat(actor, kAttributes[tes3Index], value);
}

float ActorAuthoredAttribute(const std::string& actor, int tes3Index) {
    if (tes3Index < 0 || tes3Index >= Compiler::Stats::numberOfAttributes) return 0.0f;
    return Authored(actor, Family::Attribute, tes3Index);
}

void SetSheetEnabled(bool on) { g_sheetOn = on; }

bool SheetEnabled() { return g_sheetOn; }

void SetSkillCapEnabled(bool on) { g_skillCapOn = on; }

bool SkillCapEnabled() { return g_sheetOn && g_skillCapOn; }

void InstallStatOps(OpcodeInstaller& into) {
    namespace S = Compiler::Stats;
    InstallFamily(into, kAttributes, S::numberOfAttributes, Family::Attribute,
                  {{S::opcodeGetAttribute, S::opcodeSetAttribute,
                    S::opcodeModAttribute},
                   {S::opcodeGetAttributeExplicit, S::opcodeSetAttributeExplicit,
                    S::opcodeModAttributeExplicit}});
    InstallFamily(into, kSkills, S::numberOfSkills, Family::Skill,
                  {{S::opcodeGetSkill, S::opcodeSetSkill, S::opcodeModSkill},
                   {S::opcodeGetSkillExplicit, S::opcodeSetSkillExplicit,
                    S::opcodeModSkillExplicit}});
    InstallFamily(into, kEffects, S::numberOfMagicEffects, Family::Effect,
                  {{S::opcodeGetMagicEffect, S::opcodeSetMagicEffect,
                    S::opcodeModMagicEffect},
                   {S::opcodeGetMagicEffectExplicit,
                    S::opcodeSetMagicEffectExplicit,
                    S::opcodeModMagicEffectExplicit}});
    into.Real<OpGetLevel<Implicit>>(S::opcodeGetLevel);
    into.Real<OpGetLevel<Explicit>>(S::opcodeGetLevelExplicit);
    into.Real<OpChangeRank<Implicit, 1>>(S::opcodeRaiseRank);
    into.Real<OpChangeRank<Explicit, 1>>(S::opcodeRaiseRankExplicit);
    into.Real<OpChangeRank<Implicit, -1>>(S::opcodeLowerRank);
    into.Real<OpChangeRank<Explicit, -1>>(S::opcodeLowerRankExplicit);
}

}  // namespace tesruntime::mw
