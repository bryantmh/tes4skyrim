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
#include "object_script.h"
#include "object_tick.h"
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

// Compiles one whole SCPT body -- `script_test --object <file>` -- and reports
// the locals it declared, so a real object script can be checked against the
// interpreter without a game.
int CompileObjectFile(const char* path) {
    std::ifstream in(path);
    std::stringstream text;
    text << in.rdbuf();
    const bool ok = EnsureObjectScript(path, text.str());
    std::printf("%s: %s\n", path, ok ? "compiles" : "FAILED to compile");
    const ScriptLocals* locals = ObjectScriptLocals(path);
    if (locals) {
        for (const std::string& name : locals->shorts) {
            std::printf("  short %s\n", name.c_str());
        }
        for (const std::string& name : locals->longs) {
            std::printf("  long %s\n", name.c_str());
        }
        for (const std::string& name : locals->floats) {
            std::printf("  float %s\n", name.c_str());
        }
    }
    for (const std::string& cmd : UnportedCommandsSeen()) {
        std::printf("  unported: %s\n", cmd.c_str());
    }
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

// The staged bodies and instances: what the tick compiles, and one per
// PLACEMENT rather than per base.
// See: docs/plans/morrowind_object_scripts.md#instances
void ObjectScriptTableCases() {
    std::printf("object-script tables\n");
    Check(ScriptSourceCount() == 2, "two bodies staged");
    const std::string& body = ScriptSource("TestDoorScript");
    Check(body.find("begin TestDoorScript") == 0,
          "the body is unescaped back to real newlines");
    Check(body.find('\n') != std::string::npos, "and it is multi-line");
    Check(ScriptSource("NoSuchScript").empty(), "an unknown script is empty");

    Check(InstanceCount() == 3, "three instances staged");
    Check(InstanceScript("scripts.esm", 0x0300A001) == "TestDoorScript",
          "a placement names its script");
    Check(InstanceScript("scripts.esm", 0x0300A002) == "TestDoorScript",
          "a SECOND placement of the same script is its own instance");
    Check(InstanceScript("scripts.esm", 0x0000A001) == "TestDoorScript",
          "the load-order index byte is not part of the key");
    Check(InstanceScript("scripts.esm", 0x0300C000).empty(),
          "an unplaced FormID has no instance");
    Check(InstanceScript("other.esm", 0x0300A001).empty(),
          "and the key is per PLUGIN");
}

// A whole SCPT body, compiled and run as an object script: it declares its own
// locals, opens with `begin`, and reads the one-tick events.
// See: docs/plans/morrowind_object_scripts.md#instances
void ObjectScriptRunCases() {
    std::printf("an object script runs a whole SCPT body\n");
    ClearInstances();
    ObjectScript* door = InstanceFor("scripts.esm", 0x0300A001, "test_door");
    Check(door != nullptr, "a placement with a script gets an instance");
    if (!door) return;
    Check(InstanceFor("scripts.esm", 0x0300C000, "nothing") == nullptr,
          "a placement with no script gets none");

    Check(door->RunOnce(), "the body compiles and runs");
    Check(State().Var(door->Key(), "open") == 0.0f,
          "no activation, so the door stays shut");

    door->Events().activated = true;
    Check(door->RunOnce(), "runs again with OnActivate raised");
    Check(State().Var(door->Key(), "open") == 1.0f, "and the door opened");

    // The SECOND placement of the SAME script must not see the first's local.
    ObjectScript* other = InstanceFor("scripts.esm", 0x0300A002, "test_door");
    Check(other != nullptr && other->Key() != door->Key(),
          "a second placement is a separate instance");
    if (other) {
        Check(other->RunOnce() &&
              State().Var(other->Key(), "open") == 0.0f,
              "and it has its OWN locals -- the first door's stayed shut");
    }

    // The event lives one tick: the next run must see it cleared.
    Check(door->RunOnce() && !door->Events().activated,
          "the event is cleared after the tick that read it");

    std::printf("engine-written locals are set by NAME before the body\n");
    ObjectScript* actorObj = InstanceFor("scripts.esm", 0x0300B001, "test_actor");
    Check(actorObj != nullptr, "the actor placement has an instance");
    if (actorObj) {
        actorObj->Events().pcEquipped = true;
        actorObj->RunOnce();
        Check(State().Var(actorObj->Key(), "onpcequip") == 0.0f,
              "a script that does NOT declare OnPCEquip never gets one");
        Check(State().Var(actorObj->Key(), "met") == 1.0f,
              "and its own body still ran");
    }
    ClearInstances();
}

// The tick: a FIXED rate, so the same elapsed time runs the same number of
// bodies whatever the frame rate, and GetSecondsPassed answers its delta.
// See: docs/plans/morrowind_object_scripts.md#tick-rate
std::string TestCell() { return "Test Cell"; }

void TickCases() {
    std::printf("the tick runs bound instances at a fixed rate\n");
    ClearInstances();
    // A tick does nothing until a game is loaded, which a player cell means.
    Hooks().playerCell = nullptr;
    TickObjectScripts(TickDelta());
    Check(LastTickCount() == 0, "nothing ticks with no game loaded");
    Hooks().playerCell = TestCell;

    const std::size_t before = TicksRun();
    // Bound instances only: an unbound one has no reference to act on.
    BindInstance(0x0A000001, "scripts.esm", 0x0300A001);
    Check(BoundInstanceCount() == 1, "one instance bound to a live FormID");

    TickObjectScripts(TickDelta() * 3.0f);
    Check(TicksRun() == before + 3, "three deltas run three ticks");
    Check(LastTickCount() == 1, "and each ran the one bound instance");

    // Half a tick must not run one, and the remainder must not be lost.
    const std::size_t after = TicksRun();
    TickObjectScripts(TickDelta() * 0.5f);
    Check(TicksRun() == after, "half a delta runs nothing");
    TickObjectScripts(TickDelta() * 0.5f);
    Check(TicksRun() == after + 1, "the two halves together run one");

    // A stall must not run hundreds of ticks at once.
    const std::size_t stalled = TicksRun();
    TickObjectScripts(60.0f);
    Check(TicksRun() - stalled < 20, "a long stall is clamped, not caught up");
    Hooks().playerCell = nullptr;
    ClearInstances();
}

// --- sound -----------------------------------------------------------------

// What the fake sound hooks recorded: every start, and every instance stopped.
std::vector<std::string> g_played;
std::vector<int> g_stopped;
int g_nextInstance = 0;

int FakePlaySound(const std::string& ref, const std::string& sound, bool loop,
                  float volume) {
    char line[256];
    std::snprintf(line, sizeof(line), "%s|%s|%d|%.2f", ref.c_str(),
                  sound.c_str(), loop ? 1 : 0, volume);
    g_played.push_back(line);
    return ++g_nextInstance;
}

void FakeStopSound(int instance) { g_stopped.push_back(instance); }

void SoundCases(DialogueContext& context) {
    std::printf("the sound commands\n");
    Hooks().playSound = FakePlaySound;
    Hooks().stopSound = FakeStopSound;
    g_played.clear();
    g_stopped.clear();
    g_nextInstance = 0;

    Check(RunResultScript("PlaySound \"Cave Drip\"", context),
          "PlaySound compiles and runs");
    Check(g_played.size() == 1 && g_played[0] == "|cave drip|0|1.00",
          "it plays on the listener, no reference, full volume");

    Check(RunResultScript("PlaySound3DVP \"Flies\" 0.5 1.0", context),
          "PlaySound3DVP compiles and runs");
    Check(g_played.size() == 2 && g_played[1] == "test_actor|flies|0|0.50",
          "the VP form plays on the speaker at the authored volume");

    Check(RunResultScript("PlayLoopSound3D \"Machinery\"", context),
          "PlayLoopSound3D compiles and runs");
    Check(g_played.size() == 3 &&
              g_played[2] == "test_actor|machinery|1|1.00",
          "the looping form is flagged as a loop");

    RunResultScript("if ( GetSoundPlaying \"Machinery\" == 1 )\n"
                    "    set TestGlobal to 1\nendif", context);
    Check(State().Global("TestGlobal") == 1.0f,
          "GetSoundPlaying answers 1 for a loop this reference started");

    RunResultScript("StopSound \"Machinery\"", context);
    Check(g_stopped.size() == 1 && g_stopped[0] == 3,
          "StopSound stops the instance that loop returned");

    State().SetGlobal("TestGlobal", 0.0f);
    RunResultScript("if ( GetSoundPlaying \"Machinery\" == 1 )\n"
                    "    set TestGlobal to 1\nendif", context);
    Check(State().Global("TestGlobal") == 0.0f,
          "a stopped sound is no longer playing");

    RunResultScript("StopSound \"Never Started\"", context);
    Check(g_stopped.size() == 1,
          "stopping what was never started calls nothing");

    Hooks().playSound = nullptr;
    Hooks().stopSound = nullptr;
    Check(RunResultScript("PlaySound3D \"Cave Drip\"", context),
          "with no hook the command still runs, it just makes no sound");
}

void Cases() {
    ClearScriptTables();
    LoadScriptTables("testdata\\scripts\\");
    GameActor actor("test_actor");
    DialogueContext context(actor, "Test Actor", "Player");
    ObjectScriptTableCases();
    ObjectScriptRunCases();
    TickCases();
    FactionCases(context, actor);
    RankNameCases(context);
    CoSaveCases(context);
    AiAndDeathCases(context, actor);
    PersuasionCases(context);
    SoundCases(context);
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

// Compiles EVERY staged object-script body, which is the measurement the
// whole design rests on: the Papyrus path reached 30% of them.
// See: docs/plans/morrowind_object_scripts.md
int SweepObjects(const char* root) {
    ClearScriptTables();
    // Every sidecar under `root`, as the game loads them -- a plugin's body
    // names its masters' globals, which live in the MASTER's sidecar.
    // See: docs/plans/morrowind_object_scripts.md#masters-stage-themselves
    std::string dir = root;
    if (dir.back() != '\\' && dir.back() != '/') dir.push_back('\\');
    const std::vector<std::string> plugins = SidecarPlugins(dir);
    if (plugins.empty()) {
        LoadScriptTables(dir);
    } else {
        for (const std::string& plugin : plugins) {
            LoadScriptTables(dir + plugin + "\\");
        }
        std::printf("%zu sidecar(s)\n", plugins.size());
    }
    std::printf("%zu body(ies), %zu instance(s)\n", ScriptSourceCount(),
                InstanceCount());
    std::size_t total = 0, failed = 0;
    for (const auto& entry : ScriptSources()) {
        ++total;
        if (!EnsureObjectScript(entry.first, entry.second) && ++failed <= 40) {
            std::printf("--- FAILED %s\n", entry.first.c_str());
            LogToStdout(true);
            ClearObjectPrograms();
            EnsureObjectScript(entry.first, entry.second);
            LogToStdout(false);
        }
        if (total % 500 == 0) {
            std::printf("... %zu compiled, %zu failed\n", total, failed);
            std::fflush(stdout);
        }
    }
    std::printf("%zu body(ies), %zu failed to compile\n", total, failed);
    return failed ? 1 : 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc > 2 && std::string(argv[1]) == "--sweep") return Sweep(argv[2]);
    if (argc > 2 && std::string(argv[1]) == "--sweep-objects") {
        LogToStdout(false);
        return SweepObjects(argv[2]);
    }
    LogToStdout(true);
    // `--object <file> [tableDir]`: the tables default to the test fixtures,
    // but a real sidecar folder can be named so a shipped script is checked
    // against the data it was authored against.
    if (argc > 2 && std::string(argv[1]) == "--object") {
        LoadScriptTables(argc > 3 ? argv[3] : "testdata\\scripts\\");
        return CompileObjectFile(argv[2]);
    }
    if (argc > 1) return RunFile(argv[1]);
    Cases();
    std::printf("%s\n", g_failed ? "FAILED" : "all passed");
    return g_failed ? 1 : 0;
}
