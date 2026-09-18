// Headless gate for result scripts: compile and run real MWScript on the
// vendored compiler and interpreter, with no Skyrim, and check what it did
// to the dialogue state.
//
//   script_test            the built-in cases
//   script_test <file>     run one script from a file and print the state

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include <components/interpreter/defines.hpp>

#include "dialogue_state.h"
#include "filter.h"
#include "game_actor.h"
#include "log.h"
#include "persuasion.h"
#include "script_context.h"
#include "script_runner.h"
#include "script_tables.h"
#include "store.h"

using namespace mwruntime;

namespace {

int g_failed = 0;

void Check(bool ok, const char* what) {
    std::printf("  %s  %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) ++g_failed;
}

void PrintState() {
    for (const ChoiceLine& choice : State().choices) {
        std::printf("  choice %d: %s\n", choice.index, choice.text.c_str());
    }
    for (const std::string& topic : State().addedTopics) {
        std::printf("  topic: %s\n", topic.c_str());
    }
    for (const std::string& message : State().messages) {
        std::printf("  message: %s\n", message.c_str());
    }
    std::printf("  goodbye: %d\n", State().goodbye ? 1 : 0);
}

int RunFile(const char* path) {
    std::ifstream in(path);
    std::stringstream text;
    text << in.rdbuf();
    GameActor actor("test_actor");
    DialogueContext context(actor, "Test Actor", "Player");
    const bool ok = RunResultScript(text.str(), context);
    std::printf("%s\n", ok ? "ran" : "FAILED");
    PrintState();
    return ok ? 0 : 1;
}

void TableCases(DialogueContext& context, const GameActor& actor) {
    std::printf("names are resolved from the sidecar's tables\n");
    Check(RunResultScript("if ( player->GetItemCount gold_001 >= 650 )\n"
                          "    Choice \"[Pay 650 gold.]\" 1\n"
                          "endif\n"
                          "Choice \"Later.\" 2\n", context),
          "`player->` inside an expression compiles");
    Check(RunResultScript("set QuestVars.escaping to 1", context),
          "`Script.member` compiles");
    Check(State().Var("QuestVars", "escaping") == 1.0f, "member is 1");
    Check(RunResultScript("set mood to 2\nset timer to 1.5", context),
          "the speaker's own locals compile bare");
    bool found = false;
    Check(actor.LocalVariable("mood", &found) == 2.0f && found,
          "the filter reads the local back");
    Check(State().Global("StartsAtNine") == 9.0f,
          "a global starts at its GLOB value");
    const int before = State().Disposition("test_actor");
    Check(RunResultScript("set NoSuchName to 1\nModDisposition 5", context),
          "an undeclared `set` target is a warning, as in OpenMW");
    Check(!State().HasGlobal("NoSuchName"), "and it is not invented");
    Check(State().Disposition("test_actor") == before + 5,
          "and only that line is skipped");
    State().SetDisposition("test_actor", before);
}

std::string g_stagedQuest;
int g_stagedIndex = -1;

void RecordStage(const std::string& quest, int stage) {
    g_stagedQuest = quest;
    g_stagedIndex = stage;
}

void FactionCases(DialogueContext& context, const GameActor& actor) {
    std::printf("the actor table feeds the filter\n");
    Check(actor.PrimaryFaction() == "Fighters Guild" &&
              actor.PrimaryFactionRank() == 8 && actor.IsFemale() &&
              actor.Race() == "Orc",
          "race, faction, rank and gender come from MWNP");
    Check(GameActor("gruff_actor").Disposition() == 35,
          "disposition starts at the NPC's authored value");

    std::printf("the player's factions\n");
    Check(actor.PlayerFactionRank("Fighters Guild") == -1, "not a member yet");
    RunResultScript("PCJoinFaction", context);
    Check(actor.PlayerFactionRank("Fighters Guild") == 0,
          "PCJoinFaction with no argument joins the SPEAKER's faction");
    RunResultScript("PCRaiseRank \"Fighters Guild\"\nModPCFacRep 5", context);
    Check(actor.PlayerFactionRank("Fighters Guild") == 1, "PCRaiseRank -> 1");
    Check(State().Faction("Fighters Guild").reputation == 5, "reputation 5");
    RunResultScript("if ( GetPCRank \"Fighters Guild\" == 1 )\n"
                    "    set TestGlobal to 42\nendif", context);
    Check(State().Global("TestGlobal") == 42.0f,
          "GetPCRank reads it back inside an expression");
    RunResultScript("PCExpell", context);
    Check(actor.PlayerExpelled("Fighters Guild"), "PCExpell expels");
}

void CoSaveCases(DialogueContext& context) {
    std::printf("the quest-stage hook and the co-save\n");
    Hooks().setQuestStage = RecordStage;
    RunResultScript("Journal \"Hooked_Quest\" 30", context);
    Check(g_stagedQuest == "hooked_quest" && g_stagedIndex == 30,
          "Journal tells the game which stage to set (ids arrive lowercased)");
    Hooks().setQuestStage = nullptr;

    State().LearnTopic("join the Fighters Guild");
    const std::string saved = State().Serialize();
    State().Reset();
    Check(State().JournalIndex("Hooked_Quest") == 0 &&
              !State().KnowsTopic("join the fighters guild"),
          "Reset forgets");
    const std::size_t taken = State().Deserialize(saved);
    Check(taken > 0 && State().JournalIndex("Hooked_Quest") == 30,
          "the journal survives a save and load");
    Check(State().Faction("Fighters Guild").rank == 1 &&
              State().Faction("Fighters Guild").expelled &&
              State().Global("TestGlobal") == 42.0f,
          "so do factions and globals");
    Check(State().KnowsTopic("JOIN THE FIGHTERS GUILD"),
          "and the topics the player has heard of, whatever their case");
    Check(State().Deserialize("SOMETHING ELSE\n") == 0,
          "a foreign record is refused, not misread");
    State().Reset();
}

// The AI settings and GetDeadCount: the DLL's own numbers, so a script that
// writes one and a filter that reads it back have to agree, and both have to
// survive a save.
void AiAndDeathCases(DialogueContext& context, const GameActor& actor) {
    std::printf("AI settings and dead count\n");
    RunResultScript("SetFight 90\nModFight -10\nSetHello 30", context);
    Check(actor.AiSetting(kAiFight) == 80, "SetFight then ModFight is 80");
    Check(actor.AiSetting(kAiHello) == 30, "SetHello is 30");
    RunResultScript("set TestGlobal to GetFight", context);
    Check(State().Global("TestGlobal") == 80.0f,
          "GetFight reads back what the script set");

    State().AddDeath("some_bandit");
    State().AddDeath("some_bandit");
    RunResultScript("set TestGlobal to GetDeadCount \"some_bandit\"", context);
    Check(State().Global("TestGlobal") == 2.0f, "GetDeadCount counts kills");

    const std::string saved = State().Serialize();
    State().Reset();
    State().Deserialize(saved);
    Check(State().AiSetting("test_actor", kAiFight) == 80 &&
              State().DeadCount("some_bandit") == 2,
          "both survive a save and load");
}

// 🛑 `%PCRank` and `%NextPCRank` print the FACTION's authored rank name, and
// returning "" left "You are now %PCName the  in the Fighters Guild." on
// screen. A non-member reads rank 0, which is Morrowind's own quirk.
// See: docs/commentary/morrowind_runtime.md#rank-names
void RankNameCases(DialogueContext& context) {
    std::printf("rank names in dialogue text\n");
    FactionDef def;
    def.rankNames = {"Associate", "Apprentice", "Journeyman"};
    AddFactionForTest("Fighters Guild", def);
    // FactionCases leaves the player at rank 1, so PCRank is the second name.
    const std::string got = Interpreter::fixDefinesDialog(
        "the %NextPCRank, was %PCRank", context);
    Check(got == "the Journeyman, was Apprentice", got.c_str());
}

// The fakes the persuasion formula reads the game through.
int g_roll = 0;
int FixedRoll() { return g_roll; }
int FakeLevel() { return 5; }
float FakeValue(const std::string&, const char*) { return 50.0f; }
float FakePercent(const std::string&, const char*) { return 1.0f; }
int FakeGold(const std::string&) { return 1000; }

struct GoldMove {
    std::string from, to;
    int count;
};
std::vector<GoldMove> g_goldMoves;
void RecordGold(const std::string& from, const std::string& to, int count) {
    g_goldMoves.push_back({from, to, count});
}
std::string g_advanced;
float g_advancedBy = 0;
void RecordAdvance(const char* skill, float amount) {
    g_advanced = skill;
    g_advancedBy = amount;
}

// OpenMW's persuasion formula on the fixture: with the fakes above the
// player's Admire target is 58.75, so a roll of 0 succeeds and 99 fails.
// fPerTempMult is 1, so every temporary change is kept whole on goodbye.
void PersuasionCases(DialogueContext& context) {
    std::printf("persuasion\n");
    Hooks().goldCount = FakeGold;
    Hooks().moveGold = RecordGold;
    Hooks().actorValue = FakeValue;
    Hooks().statPercent = FakePercent;
    Hooks().playerLevel = FakeLevel;
    Hooks().advanceSkill = RecordAdvance;
    Check(!Persuade(Persuasion::Admire, FixedRoll).ok,
          "no conversation, no persuasion");
    State().SetDisposition("test_actor", 50);
    State().BeginConversation("test_actor");
    g_roll = 0;
    PersuasionOutcome out = Persuade(Persuasion::Admire, FixedRoll);
    Check(out.ok && out.success && out.topic == "Admire Success" &&
              out.titleGmst == "sAdmireSuccess",
          "a roll of 0 admires successfully");
    Check(State().Disposition("test_actor") == 67,
          "disposition 50 -> 67 for the conversation");
    Check(g_advanced == "Speechcraft" && g_advancedBy == 1.0f,
          "Speechcraft is credited the SKIL success use value");
    g_roll = 99;
    out = Persuade(Persuasion::Taunt, FixedRoll);
    Check(out.ok && !out.success && out.topic == "Taunt Fail",
          "a roll of 99 taunts and fails");
    Check(State().Disposition("test_actor") == 48,
          "and it fell to 48 (the 0.66 distance factor at 67 widens the miss)");
    g_roll = 0;
    out = Persuade(Persuasion::Bribe100, FixedRoll);
    Check(out.success && g_goldMoves.size() == 1 &&
              g_goldMoves[0].from == "player" &&
              g_goldMoves[0].to == "test_actor" && g_goldMoves[0].count == 100,
          "a bribe moves 100 of Skyrim's gold from the player to the speaker");
    Check(State().Disposition("test_actor") == 91, "and it rose to 91");
    Check(BribeCost(Persuasion::Bribe1000) == 1000 && PlayerGold() == 1000,
          "bribe costs and the player's purse");
    State().EndConversation();
    Check(State().Disposition("test_actor") == 91,
          "goodbye keeps the permanent part, which is all of it here");
    const std::string saved = State().Serialize();
    State().Reset();
    State().Deserialize(saved);
    Check(State().Disposition("test_actor") == 91,
          "and the base survives the co-save");

    State().BeginConversation("test_actor");
    RunResultScript("SetDisposition 40", context);
    Persuade(Persuasion::Admire, FixedRoll);
    State().EndConversation();
    Check(State().Disposition("test_actor") == 54,
          "a script's SetDisposition mid-conversation resets the baseline");
    Hooks() = GameHooks();
    State().SetDisposition("test_actor", 50);
}

void Cases() {
    ClearScriptTables();
    LoadScriptTables("testdata\\scripts\\");
    GameActor actor("test_actor");
    DialogueContext context(actor, "Test Actor", "Player");
    FactionCases(context, actor);
    RankNameCases(context);
    CoSaveCases(context);
    AiAndDeathCases(context, actor);
    PersuasionCases(context);
    TableCases(context, actor);
    State().BeginConversation();

    std::printf("journal, topic, choice, goodbye\n");
    Check(RunResultScript("Journal \"MS_Test\" 10\n"
                          "AddTopic \"little secret\"\n"
                          "Choice \"Yes.\" 1 \"No.\" 2\n"
                          "Goodbye\n", context), "compiles and runs");
    Check(State().JournalIndex("ms_test") == 10, "journal index is 10");
    Check(State().choices.size() == 2 && State().choices[1].index == 2,
          "two choices, second answers 2");
    Check(State().addedTopics.size() == 1, "one topic added");
    Check(State().goodbye, "goodbye said");

    std::printf("journal only rises; SetJournalIndex always sets\n");
    RunResultScript("Journal MS_Test 5", context);
    Check(State().JournalIndex("MS_Test") == 10, "Journal 5 leaves 10");
    RunResultScript("SetJournalIndex MS_Test 5", context);
    Check(State().JournalIndex("MS_Test") == 5, "SetJournalIndex 5 sets 5");

    std::printf("disposition, implicit and explicit\n");
    RunResultScript("ModDisposition 15\n\"other_npc\"->ModDisposition -20",
                    context);
    Check(State().Disposition("test_actor") == 65, "speaker 50 -> 65");
    Check(State().Disposition("other_npc") == 30, "other 50 -> 30");

    std::printf("globals and control flow\n");
    RunResultScript("set TestGlobal to 3\n"
                    "if ( TestGlobal == 3 )\n"
                    "    set TestGlobal to TestGlobal + 4\n"
                    "endif\n", context);
    Check(State().Global("testglobal") == 7.0f, "global is 7");

    std::printf("an unported command pops its arguments and does nothing\n");
    Check(RunResultScript("Player->AddItem \"gold_001\" 50\n"
                          "AiTravel 1 2 3\n"
                          "ModDisposition 5\n", context),
          "script with unported commands still runs");
    Check(State().Disposition("test_actor") == 70,
          "the command AFTER them still worked");

    std::printf("a message box becomes a notice\n");
    State().messages.clear();
    RunResultScript("MessageBox \"You feel watched.\"", context);
    Check(State().messages.size() == 1, "one notice");
}

// Every authored result script under a sidecar root: how many compile and
// run, and which unported commands the content actually reaches.
int Sweep(const char* root) {
    const StoreStats stats = LoadStoreFrom(root);
    std::printf("%zu topics, %zu responses\n", stats.topics, stats.infos);
    std::size_t scripts = 0, failed = 0;
    for (const auto& entry : Topics()) {
        for (const Info& info : entry.second.infos) {
            if (info.resultScript.empty()) continue;
            ++scripts;
            // The INFO's own speaker when it names one: a result script may
            // use that actor's script locals bare.
            GameActor actor(info.actor.empty() ? "sweep_actor" : info.actor);
            DialogueContext context(actor, "Sweep", "Player");
            State().BeginConversation();
            if (!RunResultScript(info.resultScript, context) &&
                ++failed <= 25) {
                std::printf("--- FAILED %s / %s (speaker '%s')\n",
                            info.topic.c_str(), info.id.c_str(),
                            info.actor.c_str());
                LogToStdout(true);
                RunResultScript(info.resultScript, context);
                LogToStdout(false);
            }
            if (scripts % 2000 == 0) {
                std::printf("... %zu scripts, %zu failed\n", scripts, failed);
                std::fflush(stdout);
            }
        }
    }
    std::printf("%zu scripts, %zu failed\n", scripts, failed);
    for (const std::string& row : UnportedCommandsSeen()) {
        std::printf("  unported: %s\n", row.c_str());
    }
    return failed ? 1 : 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc > 2 && std::string(argv[1]) == "--sweep") return Sweep(argv[2]);
    LogToStdout(true);
    if (argc > 1) return RunFile(argv[1]);
    Cases();
    std::printf("%s\n", g_failed ? "FAILED" : "all passed");
    return g_failed ? 1 : 0;
}
