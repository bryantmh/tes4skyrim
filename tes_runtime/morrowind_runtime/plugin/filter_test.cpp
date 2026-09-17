// Headless filter gate: the ported TES3 selection rules, with no game.
//
// Built by `build.bat test` into filter_test.exe. Beyond the unit cases it
// sweeps a real export, so the filter is exercised against 69,270 authored
// responses rather than only the shapes a fixture happens to name.

#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#include "filter.h"
#include "store.h"

namespace mwruntime {
namespace {

int g_failures = 0;

void Check(bool ok, const char* what) {
    if (!ok) {
        std::printf("  FAIL %s\n", what);
        ++g_failures;
    }
}

// An actor whose every answer is set by the test.
class FakeActor : public ActorView {
public:
    RefId id = "test_npc";
    bool  isNpc = true;
    RefId race = "Dark Elf";
    RefId clazz = "Commoner";
    bool  female = false;
    RefId faction;
    int   factionRank = -1;
    std::map<RefId, int> playerFactions;
    std::map<RefId, bool> expelled;
    int   disposition = 50;
    RefId playerRace = "Dark Elf";
    RefId playerClass = "Warrior";
    bool  playerFemale = false;
    int   playerLevel = 1;
    int   playerHealth = 100;
    int   crimeLevel = 0;
    std::string cellName = "Balmora";
    int   health = 100;
    int   level = 1;
    bool  detected = false;
    bool  alarmed = false;
    bool  attacked = false;
    bool  talkedTo = false;
    std::map<RefId, int> dead;
    std::map<RefId, int> items;
    std::map<RefId, int> journal;
    std::map<std::string, float> locals;
    std::map<std::string, float> globals;
    int   choice = -1;

    RefId Id() const override { return id; }
    bool  IsNpc() const override { return isNpc; }
    RefId Race() const override { return race; }
    RefId Class() const override { return clazz; }
    bool  IsFemale() const override { return female; }
    RefId PrimaryFaction() const override { return faction; }
    int   PrimaryFactionRank() const override { return factionRank; }
    int   PlayerFactionRank(const RefId& f) const override {
        const auto it = playerFactions.find(f);
        return it == playerFactions.end() ? -1 : it->second;
    }
    bool PlayerExpelled(const RefId& f) const override {
        const auto it = expelled.find(f);
        return it != expelled.end() && it->second;
    }
    int FactionReaction(const RefId&, const RefId&) const override { return 0; }
    int Disposition() const override { return disposition; }
    RefId PlayerRace() const override { return playerRace; }
    RefId PlayerClass() const override { return playerClass; }
    bool  PlayerIsFemale() const override { return playerFemale; }
    int   PlayerLevel() const override { return playerLevel; }
    int   PlayerHealthPercent() const override { return playerHealth; }
    int   PlayerCrimeLevel() const override { return crimeLevel; }
    int   PlayerSkill(int) const override { return 100; }
    int   PlayerAttribute(int) const override { return 100; }
    std::string PlayerCellName() const override { return cellName; }
    int  Health() const override { return health; }
    int  Level() const override { return level; }
    bool Detected() const override { return detected; }
    bool Alarmed() const override { return alarmed; }
    bool Attacked() const override { return attacked; }
    bool TalkedToPlayer() const override { return talkedTo; }
    int  DeadCount(const RefId& k) const override {
        const auto it = dead.find(k);
        return it == dead.end() ? 0 : it->second;
    }
    int ItemCount(const RefId& k) const override {
        const auto it = items.find(k);
        return it == items.end() ? 0 : it->second;
    }
    int JournalIndex(const RefId& k) const override {
        const auto it = journal.find(k);
        return it == journal.end() ? 0 : it->second;
    }
    float LocalVariable(const std::string& n, bool* found) const override {
        const auto it = locals.find(n);
        *found = it != locals.end();
        return *found ? it->second : 0.0f;
    }
    float GlobalVariable(const std::string& n, bool* found) const override {
        const auto it = globals.find(n);
        *found = it != globals.end();
        return *found ? it->second : 0.0f;
    }
    int Choice() const override { return choice; }
};

Info MakeInfo(const char* id, int ordinal) {
    Info info;
    info.id = id;
    info.ordinal = ordinal;
    info.gender = -1;
    info.rank = -1;
    info.pcRank = -1;
    info.type = DialType::Topic;
    return info;
}

Condition Numbered(int index, char cmp, int value) {
    Condition c;
    c.function = '1';
    c.index = index;
    c.comparison = cmp;
    c.valueInt = value;
    return c;
}

void TestOrderIsPrecedence() {
    std::printf("order is precedence\n");
    Topic topic;
    topic.infos.push_back(MakeInfo("first", 0));
    topic.infos.push_back(MakeInfo("second", 1));
    FakeActor actor;
    const FilterResult r = SelectInfo(topic, actor, -1);
    Check(r.info && r.info->id == "first", "takes the FIRST match, not the best");

    // A failing first response falls through to the second.
    topic.infos[0].actor = "someone_else";
    const FilterResult r2 = SelectInfo(topic, actor, -1);
    Check(r2.info && r2.info->id == "second", "falls through on reject");
}

void TestActorAndCreature() {
    std::printf("actor filters\n");
    FakeActor actor;
    Info info = MakeInfo("i", 0);
    info.actor = "Test_NPC";
    Check(TestInfo(info, actor, -1).why == Reject::None,
          "actor id matches case-insensitively");

    FakeActor creature;
    creature.isNpc = false;
    creature.id = "rat";
    Info generic = MakeInfo("g", 0);
    Check(TestInfo(generic, creature, -1).why == Reject::Actor,
          "a creature refuses a topic that does not name it");
    Info named = MakeInfo("n", 0);
    named.actor = "rat";
    Check(TestInfo(named, creature, -1).why == Reject::None,
          "a creature answers a topic naming it");
}

void TestGender() {
    std::printf("gender\n");
    FakeActor male;
    Info forFemale = MakeInfo("f", 0);
    forFemale.gender = 1;
    Check(TestInfo(forFemale, male, -1).why == Reject::Gender,
          "a female-only line rejects a male speaker");
    FakeActor female;
    female.female = true;
    Check(TestInfo(forFemale, female, -1).why == Reject::None,
          "and accepts a female one");
}

void TestFactionAndRank() {
    std::printf("faction and rank\n");
    FakeActor actor;
    actor.faction = "Fighters Guild";
    actor.factionRank = 2;

    Info needsRank = MakeInfo("r", 0);
    needsRank.faction = "Fighters Guild";
    needsRank.rank = 5;
    Check(TestInfo(needsRank, actor, -1).why == Reject::Rank,
          "rank below the requirement rejects");
    needsRank.rank = 1;
    Check(TestInfo(needsRank, actor, -1).why == Reject::None,
          "rank at or above passes");

    Info factionLess = MakeInfo("fl", 0);
    factionLess.factionLess = true;
    Check(TestInfo(factionLess, actor, -1).why == Reject::Faction,
          "FactionLess rejects a speaker who HAS a faction");
    FakeActor loner;
    Check(TestInfo(factionLess, loner, -1).why == Reject::None,
          "and accepts one who does not");
}

void TestCellIsAPrefix() {
    std::printf("cell prefix\n");
    FakeActor actor;
    actor.cellName = "Balmora, Guild of Fighters";
    Info info = MakeInfo("c", 0);
    info.cell = "Balmora";
    Check(TestInfo(info, actor, -1).why == Reject::None,
          "a cell filter matches as a PREFIX");
    info.cell = "Vivec";
    Check(TestInfo(info, actor, -1).why == Reject::Cell,
          "and rejects a different cell");
}

void TestDisposition() {
    std::printf("disposition\n");
    FakeActor actor;
    actor.disposition = 30;
    Info info = MakeInfo("d", 0);
    info.disposition = 50;
    Check(TestInfo(info, actor, -1).why == Reject::Disposition,
          "disposition below the threshold rejects");
    actor.disposition = 60;
    Check(TestInfo(info, actor, -1).why == Reject::None, "and above passes");

    // A journal entry is never disposition-gated.
    Info entry = MakeInfo("j", 0);
    entry.type = DialType::Journal;
    entry.disposition = 90;
    FakeActor hostile;
    hostile.disposition = 0;
    Check(TestInfo(entry, hostile, -1).why == Reject::None,
          "a journal entry ignores disposition");
}

void TestChoiceGating() {
    std::printf("choice\n");
    FakeActor actor;
    Info info = MakeInfo("ch", 0);
    info.conditions.push_back(Numbered(Fn_Choice, '0', 1));
    Check(TestInfo(info, actor, -1).why == Reject::Condition,
          "outside a choice EVERY choice condition fails");
    Check(TestInfo(info, actor, 1).why == Reject::None,
          "inside the matching choice it passes");
    Check(TestInfo(info, actor, 2).why == Reject::Condition,
          "a different choice does not");
}

void TestVariableSemantics() {
    std::printf("variable semantics\n");
    FakeActor actor;
    actor.globals["someglobal"] = 5.0f;
    actor.locals["somelocal"] = 5.0f;

    Condition missingGlobal;
    missingGlobal.function = '2';
    missingGlobal.variable = "nosuchglobal";
    missingGlobal.comparison = '0';
    missingGlobal.valueInt = 1;
    Check(TestCondition(missingGlobal, actor, -1),
          "a global that does not exist is IGNORED");

    Condition missingLocal;
    missingLocal.function = '3';
    missingLocal.variable = "nosuchlocal";
    missingLocal.comparison = '0';
    missingLocal.valueInt = 1;
    Check(!TestCondition(missingLocal, actor, -1),
          "a local that does not exist REJECTS");

    Condition global;
    global.function = '2';
    global.variable = "someglobal";
    global.comparison = '3';
    global.valueInt = 5;
    Check(TestCondition(global, actor, -1), "global >= compares");
}

void TestComparisons() {
    std::printf("comparison operators\n");
    FakeActor actor;
    actor.playerLevel = 10;
    const struct { char op; int value; bool want; } cases[] = {
        {'0', 10, true},  {'0', 9, false},
        {'1', 9,  true},  {'1', 10, false},
        {'2', 9,  true},  {'2', 10, false},
        {'3', 10, true},  {'3', 11, false},
        {'4', 11, true},  {'4', 10, false},
        {'5', 10, true},  {'5', 9, false},
    };
    for (const auto& c : cases) {
        const bool got = TestCondition(Numbered(Fn_PcLevel, c.op, c.value),
                                       actor, -1);
        if (got != c.want) {
            std::printf("  FAIL op '%c' vs %d\n", c.op, c.value);
            ++g_failures;
        }
    }
}

void TestInvertedFunctions() {
    std::printf("inverted functions\n");
    FakeActor actor;
    actor.id = "fargoth";
    Condition notId;
    notId.function = '7';
    notId.variable = "fargoth";
    notId.comparison = '0';
    Check(!TestCondition(notId, actor, -1),
          "NotId rejects the actor it names");
    notId.variable = "someone";
    Check(TestCondition(notId, actor, -1), "and passes anyone else");
}

void TestUnknownFunctionPasses() {
    std::printf("unknown function\n");
    FakeActor actor;
    // Index 99 is not a TES3 function; an unanswerable filter must not hide
    // the line, or a single unported function silently mutes dialogue.
    Check(TestCondition(Numbered(99, '0', 1), actor, -1),
          "an unimplemented function passes rather than rejecting");
}

// Sweeps a real export: every response is filtered against a permissive actor
// so the whole authored corpus runs through the real code path.
void Sweep(const char* dir) {
    std::printf("sweep %s\n", dir);
    const StoreStats stats = LoadStoreFrom(dir);
    if (!stats.topics) {
        std::printf("  (no sidecar there; skipped)\n");
        return;
    }
    FakeActor actor;
    std::size_t tested = 0;
    std::size_t passed = 0;
    std::map<int, std::size_t> rejects;
    for (const auto& entry : Topics()) {
        for (const Info& info : entry.second.infos) {
            const FilterResult r = TestInfo(info, actor, -1);
            ++tested;
            if (r.why == Reject::None) ++passed;
            else ++rejects[static_cast<int>(r.why)];
        }
    }
    static const char* kNames[] = {
        "none", "actor", "race", "class", "faction", "rank", "gender",
        "pcFaction", "pcRank", "cell", "disposition", "condition"};
    std::printf("  responses %zu, passing %zu\n", tested, passed);
    for (const auto& kv : rejects) {
        std::printf("    rejected by %-12s %zu\n", kNames[kv.first], kv.second);
    }
    Check(tested > 0, "responses swept");
    Check(passed > 0, "some response passes a permissive actor");
}

}  // namespace
}  // namespace mwruntime

namespace {

using namespace mwruntime;

// One numbered-function rule, as SCVR encodes it.
Condition Numbered(int index, char comparison, int value) {
    Condition cond;
    cond.function = '1';
    cond.index = index;
    cond.comparison = comparison;
    cond.valueInt = value;
    return cond;
}

// 🛑 The vampire regression: an index with no case must REJECT, or the
// greeting it guards wins for every ordinary NPC.
// See: docs/commentary/morrowind_runtime.md#unknown-functions
int UnknownFunctionCases() {
    FakeActor actor;
    int failed = 0;
    struct Case { int index; const char* what; bool expected; };
    const Case cases[] = {
        {Fn_PcVampire, "PCVampire == 1 rejects", false},
        {Fn_Werewolf, "Werewolf == 1 rejects", false},
        {Fn_PcCorprus, "PCCorprus == 1 rejects", false},
        {Fn_PcCommonDisease, "PCCommonDisease == 1 rejects", false},
        {74, "an index past the last function rejects", false},
    };
    for (const Case& row : cases) {
        const bool got = TestCondition(Numbered(row.index, '0', 1), actor, -1);
        std::printf("  %s  %s\n", got == row.expected ? "ok  " : "FAIL",
                    row.what);
        if (got != row.expected) ++failed;
    }
    const bool notVampire = TestCondition(Numbered(Fn_PcVampire, '0', 0),
                                          actor, -1);
    std::printf("  %s  PCVampire == 0 passes\n", notVampire ? "ok  " : "FAIL");
    if (!notVampire) ++failed;
    return failed;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 1) {
        std::printf("unknown numbered functions\n");
        const int failed = UnknownFunctionCases();
        std::printf("%s\n", failed ? "FAILED" : "all passed");
        return failed ? 1 : 0;
    }
    using namespace mwruntime;
    TestOrderIsPrecedence();
    TestActorAndCreature();
    TestGender();
    TestFactionAndRank();
    TestCellIsAPrefix();
    TestDisposition();
    TestChoiceGating();
    TestVariableSemantics();
    TestComparisons();
    TestInvertedFunctions();
    TestUnknownFunctionPasses();
    if (argc > 1) Sweep(argv[1]);
    std::printf(g_failures ? "\nFAILED (%d)\n" : "\nOK\n", g_failures);
    return g_failures ? 1 : 0;
}
