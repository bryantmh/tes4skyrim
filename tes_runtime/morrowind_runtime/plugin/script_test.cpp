// Headless gate for result scripts: compile and run real MWScript on the
// vendored compiler and interpreter, with no Skyrim, and check what it did
// to the dialogue state.
//
//   script_test            the built-in cases
//   script_test <file>     run one script from a file and print the state

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
// 🛑 16-bit relics windows.h still defines as EMPTY macros, which silently eat
// any parameter named `near` or `far` -- `FakePlaceNear` has one.
#undef near
#undef far

#include <cstdarg>
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
#include "travel.h"

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

// What the fake engine says has died, and what it was last told to apply.
int TwoDead(const std::string& actor) {
    return actor == "some_bandit" ? 2 : 0;
}

int g_appliedWhich = -1;
int g_appliedValue = -1;
void RecordApplied(const std::string&, int which, int value) {
    g_appliedWhich = which;
    g_appliedValue = value;
}

// The AI settings are the DLL's own numbers that START at the authored AIDT,
// and every write is pushed to the engine. GetDeadCount is the engine's.
void AiAndDeathCases(DialogueContext& context, const GameActor& actor) {
    std::printf("AI settings and dead count\n");
    Check(State().AiSetting("gruff_actor", kAiFight) == 90 &&
              State().AiSetting("gruff_actor", kAiHello) == 25 &&
              State().AiSetting("gruff_actor", kAiFlee) == 10 &&
              State().AiSetting("gruff_actor", kAiAlarm) == 5,
          "an unset AI setting reads the authored AIDT");
    Hooks().applyAiSetting = RecordApplied;
    RunResultScript("SetFight 90\nModFight -10", context);
    Check(g_appliedWhich == kAiFight && g_appliedValue == 80,
          "a written AI setting is pushed to the engine");
    Hooks().applyAiSetting = nullptr;
    RunResultScript("SetHello 30", context);
    Check(actor.AiSetting(kAiFight) == 80, "SetFight then ModFight is 80");
    Check(actor.AiSetting(kAiHello) == 30, "SetHello is 30");
    RunResultScript("set TestGlobal to GetFight", context);
    Check(State().Global("TestGlobal") == 80.0f,
          "GetFight reads back what the script set");

    Hooks().deadCount = TwoDead;
    RunResultScript("set TestGlobal to GetDeadCount \"some_bandit\"", context);
    Check(State().Global("TestGlobal") == 2.0f,
          "GetDeadCount answers with the engine's count");
    Hooks().deadCount = nullptr;

    const std::string saved = State().Serialize();
    State().Reset();
    State().Deserialize(saved);
    Check(State().AiSetting("test_actor", kAiFight) == 80,
          "an AI setting survives a save and load");
}

std::string TestCell();
// 🛑 An UNNAMED cell -- what every exterior wilderness cell returns. The tick
// must run there exactly as it does indoors.
std::string UnnamedCell();
bool InWorld();
bool NotInWorld();

// 🛑 Dialogue and the speaker's OWN object script must share one variable: a
// result script that sets `met` and the NPC's script that reads it are the
// same local in TES3.
void SharedLocalsCases(DialogueContext& context) {
    std::printf("dialogue and the object script share locals\n");
    ClearInstances();
    BindInstance(0x0A00B001, "scripts.esm", 0x0300B001);
    SetSpeakerInstance("test_actor", 0x0A00B001);
    RunResultScript("set met to 7", context);
    Check(State().Var("scripts.esm|00B001", "met") == 7.0f,
          "a result script writes the speaker's PLACEMENT locals");
    Check(LocalsOwner("test_actor") == "scripts.esm|00B001",
          "and the speaker's id names that same owner");
    Check(LocalsOwner("TestCounterScript") == "TestCounterScript",
          "a global script still owns its locals by name");
    SetSpeakerInstance("", 0);
    ClearInstances();
    State().Reset();
}

bool Indoors() { return true; }
int OneFollower() { return 1; }
int g_paid = 0;
int g_hoursAdvanced = 0;
std::string g_wentTo;
void RecordFare(const std::string&, const std::string&, int count) {
    g_paid = count;
}
int RichPlayer(const std::string&) { return 1000; }
void RecordHours(int hours) { g_hoursAdvanced = hours; }
void RecordTrip(const TravelDest& dest) { g_wentTo = dest.name; }

// TravelWindow's fares. With the fixture's stats the haggle term is 1.1125:
// player (5 + 3 + 6) * 1.25 = 17.5 against the actor's (20 + 4 + 8) * 1.25 =
// 40, so buying costs 0.01 * (100 - 0.5 * (17.5 - 40)) of the base.
void TravelCases() {
    std::printf("travel\n");
    Check(OffersTravel("test_actor") && !OffersTravel("gruff_actor"),
          "an actor offers travel exactly when it lists destinations");
    std::vector<Fare> fares = TravelFares("test_actor");
    Check(fares.size() == 2 && fares[0].dest.name == "Far Place" &&
              !fares[0].dest.interior && fares[1].dest.interior,
          "both destinations parse, in order");
    Check(fares[0].dest.hasMarker &&
              fares[0].dest.marker.plugin == "scripts.esm" &&
              (fares[0].dest.marker.formId & 0x00FFFFFF) == 0x00C001 &&
              !fares[1].dest.hasMarker,
          "a destination carries the marker the export minted, when it did");
    Check(fares[0].price == 11,
          "outdoors the fare is distance / fTravelMult, then haggled");
    Check(TravelHours(fares[0].dest) == 2,
          "and the trip takes distance / fTravelTimeMult whole hours");
    Hooks().followerCount = OneFollower;
    Check(TravelFares("test_actor")[0].price == 22,
          "a follower doubles the base fare");
    Hooks().followerCount = nullptr;
    Hooks().playerInInterior = Indoors;
    Check(TravelFares("test_actor")[0].price == 11 &&
              TravelHours(fares[0].dest) == 0,
          "indoors it is the flat fMagesGuildTravel and takes no time");
    Hooks().playerInInterior = nullptr;

    Check(!TakeTrip("test_actor", fares[0], nullptr),
          "a player who cannot pay goes nowhere");
    Hooks().goldCount = RichPlayer;
    Hooks().moveGold = RecordFare;
    Hooks().advanceHours = RecordHours;
    Hooks().travelTo = RecordTrip;
    Check(TakeTrip("test_actor", fares[0], nullptr) && g_paid == 11 &&
              g_hoursAdvanced == 2 && g_wentTo == "Far Place",
          "a trip pays, moves the clock on and sends the player");
    Hooks().goldCount = nullptr;
    Hooks().moveGold = nullptr;
    Hooks().advanceHours = nullptr;
    Hooks().travelTo = nullptr;
}

float g_liveSneak = 40.0f;
float LiveStat(const std::string&, const char* name) {
    return std::string(name) == "Sneak" ? g_liveSneak : 0.0f;
}
void SetLiveStat(const std::string&, const char* name, float value) {
    if (std::string(name) == "Sneak") g_liveSneak = value;
}
int LiveLevel(const std::string&) { return 12; }

// A stat Skyrim has is Skyrim's; one it lacks is ours and starts at the
// authored NPC_ value.
void StatCases(DialogueContext& context) {
    std::printf("stat commands\n");
    Hooks().actorValue = LiveStat;
    Hooks().setActorValue = SetLiveStat;
    Hooks().level = LiveLevel;
    RunResultScript("ModSneak 5\nset TestGlobal to GetSneak", context);
    Check(g_liveSneak == 45.0f && State().Global("TestGlobal") == 45.0f,
          "a mapped skill reads and writes the Skyrim actor value");
    RunResultScript("set TestGlobal to GetStrength", context);
    Check(State().Global("TestGlobal") == 61.0f,
          "an attribute starts at the authored NPC_ value");
    RunResultScript("ModStrength -11\nset TestGlobal to GetStrength", context);
    Check(State().Global("TestGlobal") == 50.0f,
          "and is the DLL's own number once a script moves it");
    RunResultScript("SetChameleon 30\nset TestGlobal to GetChameleon", context);
    Check(State().Global("TestGlobal") == 30.0f,
          "a magic-effect magnitude is an integer stat of the same kind");
    RunResultScript("set TestGlobal to GetLevel", context);
    Check(State().Global("TestGlobal") == 12.0f, "GetLevel asks the engine");
    Hooks().actorValue = nullptr;
    Hooks().setActorValue = nullptr;
    Hooks().level = nullptr;
    State().Reset();
}

// SSCR scripts start by themselves, and start AGAIN after a load even when
// they had stopped, which is TES3's own behaviour.
void StartScriptCases() {
    std::printf("start scripts\n");
    State().Reset();
    Check(StartScripts().size() == 1, "one SSCR staged");
    State().StartStartupScripts();
    Check(State().ScriptRunning("TestCounterScript"),
          "a start script runs without anyone calling StartScript");
    Check(State().ScriptRunning("Main"),
          "and so does Main, which no SSCR names");
    State().StopScript("TestCounterScript");
    State().StartStartupScripts();
    Check(State().ScriptRunning("TestCounterScript"),
          "and a stopped one starts again on the next load");
    State().Reset();
}

// Whether an actor attacks on sight depends on Fight AND disposition, so a
// disposition write has to re-apply Fight.
void DispositionReappliesFight(DialogueContext& context) {
    std::printf("disposition re-applies Fight\n");
    g_appliedWhich = -1;
    Hooks().applyAiSetting = RecordApplied;
    RunResultScript("ModDisposition -10", context);
    Check(g_appliedWhich == kAiFight,
          "a disposition write re-applies the Fight setting");
    Hooks().applyAiSetting = nullptr;
    State().Reset();
}

// `StartScript` makes a global script TICK, under locals keyed by its name,
// until it stops itself; the running set and its target survive a save.
void GlobalScriptCases(DialogueContext& context) {
    std::printf("global scripts tick\n");
    ClearInstances();
    Hooks().playerCell = TestCell;
    Hooks().playerInWorld = InWorld;
    RunResultScript("StartScript TestCounterScript", context);
    Check(State().ScriptRunning("TestCounterScript"), "StartScript starts it");
    const std::string saved = State().Serialize();
    State().Reset();
    State().Deserialize(saved);
    const auto running = State().RunningScripts();
    Check(running.size() == 1 && running[0].second == "test_actor",
          "a running script and its target survive a save");
    TickObjectScripts(TickDelta());
    Check(State().Var("TestCounterScript", "count") == 1.0f,
          "one tick runs the body once, under the script's own name");
    TickObjectScripts(TickDelta());
    Check(!State().ScriptRunning("TestCounterScript"),
          "a script that stops itself stops");
    TickObjectScripts(TickDelta());
    Check(State().Var("TestCounterScript", "count") == 2.0f,
          "and a stopped script no longer ticks");
    Hooks().playerCell = nullptr;
    Hooks().playerInWorld = nullptr;
    State().Reset();
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
    Check(ScriptSourceCount() == 5, "five bodies staged");
    const std::string& body = ScriptSource("TestDoorScript");
    Check(body.find("begin TestDoorScript") == 0,
          "the body is unescaped back to real newlines");
    Check(body.find('\n') != std::string::npos, "and it is multi-line");
    Check(ScriptSource("NoSuchScript").empty(), "an unknown script is empty");

    Check(InstanceCount() == 4, "four instances staged");
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
        actorObj->Events().pcHitMe = true;
        actorObj->PollEquipped("test_actor");
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
std::string UnnamedCell() { return std::string(); }
bool InWorld() { return true; }
bool NotInWorld() { return false; }

bool Always3DLoaded(std::uint32_t) { return true; }
bool Never3DLoaded(std::uint32_t) { return false; }

bool g_gamePaused = false;
bool FakeGamePaused() { return g_gamePaused; }

void SpawnedTickCases();
void DiscoveryTickCases();
void DeathOnUnloadCases();
void UnnamedCellTickCases();
void PausedTickCases();

// The placement the fake `loadedRef` reports as being in the world, and the
// runtime FormID it answers with. 0 means "nothing is loaded".
std::uint32_t g_loadedLocal = 0;

// How many times the sweep asked, which is what proves it rests between laps.
std::size_t g_loadedRefCalls = 0;

std::uint32_t FakeLoadedRef(const std::string& plugin,
                            std::uint32_t localFormId) {
    ++g_loadedRefCalls;
    if (plugin != "scripts.esm" || localFormId != g_loadedLocal) return 0;
    return 0x0A000000 | (localFormId & 0x00FFFFFF);
}

// 🛑 An instance must bind because its object is IN THE WORLD, never because
// the player clicked it. Activation used to be the only binding path, so
// `OnDeath` and every proximity poll were dead for anything unclicked.
// See: docs/commentary/morrowind_runtime.md#instances-bind-from-the-world
void DiscoveryTickCases() {
    std::printf("a loaded placement binds without ever being activated\n");
    ClearInstances();
    ResetTickState();
    Hooks().playerCell = TestCell;
    Hooks().playerInWorld = InWorld;
    Hooks().is3DLoaded = Always3DLoaded;
    Hooks().loadedRef = FakeLoadedRef;

    g_loadedLocal = 0;
    TickObjectScripts(TickDelta());
    Check(BoundInstanceCount() == 0, "nothing binds while nothing is loaded");

    // The cell loads. No activation, no spawn -- the sweep alone must find it.
    // A lap rests afterwards, so the reset is what the game's cell change does.
    g_loadedLocal = 0x0300B001;
    ResetTickState();
    TickObjectScripts(TickDelta());
    Check(BoundInstanceCount() == 1, "the loaded placement binds by itself");
    Check(LastTickCount() == 1, "and its body runs");

    // 🛑 The sweep must not rebind what it already bound, or one placement
    // would be re-bound every lap of the table.
    ResetTickState();
    TickObjectScripts(TickDelta());
    Check(BoundInstanceCount() == 1, "a bound placement is not bound twice");

    // It leaves the world, then comes back: an instance outlives its binding,
    // so an existence test here would never rebind.
    Hooks().is3DLoaded = Never3DLoaded;
    TickObjectScripts(TickDelta());
    Check(BoundInstanceCount() == 0, "unloading unbinds it");
    Hooks().is3DLoaded = Always3DLoaded;
    ResetTickState();
    TickObjectScripts(TickDelta());
    Check(BoundInstanceCount() == 1, "re-entering the cell binds it AGAIN");

    // 🛑 A swept instance must carry its BASE id. It is what a bare command
    // inside the body acts on (`StartCombat` with no `->` is the object
    // itself), and binding with "" made every implicit command target nothing
    // -- measured in-game as `combat:  attacks ...` with an empty attacker.
    ResetTickState();
    g_loadedLocal = 0x0300B001;
    TickObjectScripts(TickDelta());
    const ObjectScript* swept = FindInstance("scripts.esm", 0x0300B001);
    Check(swept && swept->BaseId() == "test_actor",
          "a swept instance knows the base it places");

    // 🛑 The sweep must REST between laps. 15,639 rows x 2 engine calls every
    // tick is 7,680 calls a second to learn nothing, since only a cell load
    // changes an answer. Counting calls is the only way to catch a regression
    // here -- behaviour looks identical either way.
    ResetTickState();
    g_loadedLocal = 0;
    g_loadedRefCalls = 0;
    TickObjectScripts(TickDelta() * 20.0f);
    Check(g_loadedRefCalls <= InstanceCount() * 2,
          "20 ticks cost about one lap, not twenty");

    Hooks().loadedRef = nullptr;
    Hooks().is3DLoaded = nullptr;
    Hooks().playerCell = nullptr;
    Hooks().playerInWorld = nullptr;
    g_loadedLocal = 0;
    ClearInstances();
}

// What the fake engine reports for IsDead, so a test can kill something
// mid-run rather than only describing a corpse.
bool g_actorDead = false;
bool FakeIsDead(std::uint32_t) { return g_actorDead; }

// 🛑 THE bug behind Ga'Nahiru: the tick gated on the player's cell having a
// NAME, and every exterior wilderness cell has none. Scripts ran indoors and
// nowhere else, so no OnDeath, no proximity poll and no discovery sweep ever
// ran in the open world -- where most of the game is.
// See: docs/commentary/morrowind_runtime.md#the-tick-is-gated-on-a-loaded-game
void UnnamedCellTickCases() {
    std::printf("an UNNAMED exterior cell still ticks\n");
    ClearInstances();
    Hooks().is3DLoaded = Always3DLoaded;

    // No game at all: the main menu still pumps tasks, and nothing may run.
    Hooks().playerCell = UnnamedCell;
    Hooks().playerInWorld = NotInWorld;
    BindInstance(0x0A000001, "scripts.esm", 0x0300A001);
    TickObjectScripts(TickDelta());
    Check(LastTickCount() == 0, "nothing ticks on the main menu");

    // A game IS loaded, outdoors, where the cell has no name.
    Hooks().playerInWorld = InWorld;
    TickObjectScripts(TickDelta());
    Check(LastTickCount() == 1, "a bound instance ticks in an unnamed cell");

    Hooks().is3DLoaded = nullptr;
    Hooks().playerCell = nullptr;
    Hooks().playerInWorld = nullptr;
    ClearInstances();
}

// 🛑 `OnDeath` lives ONE tick, so gating before the poll can discard it for
// good. This pins the order: a death seen on the same tick the gate reads
// "gone" still runs its body once.
// See: docs/commentary/morrowind_runtime.md#instances-bind-from-the-world
void DeathOnUnloadCases() {
    std::printf("a death seen as the 3D unloads still runs its body\n");
    ClearInstances();
    Hooks().playerCell = TestCell;
    Hooks().playerInWorld = InWorld;

    // Bound and loaded once ALIVE, so a later false is a real unload and the
    // death below is a transition rather than a corpse's starting state.
    BindInstance(0x0A00C001, "scripts.esm", 0x0300C001);
    Hooks().is3DLoaded = Always3DLoaded;
    g_actorDead = false;
    Hooks().isDead = FakeIsDead;
    TickObjectScripts(TickDelta());

    // The kill: dead AND unloaded on the same tick.
    g_actorDead = true;
    Hooks().is3DLoaded = Never3DLoaded;
    TickObjectScripts(TickDelta());
    Check(State().Var("scripts.esm|00C001", "deaddone") == 1.0f,
          "OnDeath ran on the tick the corpse unloaded");
    Check(BoundInstanceCount() == 0, "and the instance is still unbound after");

    // 🛑 `OnDeath` is a TRANSITION. A body placed dead in the cell never died
    // during play, so binding it raises nothing -- and neither does rebinding
    // any corpse when its cell reloads.
    std::printf("a corpse bound already dead raises nothing\n");
    ClearInstances();
    State().Reset();
    g_actorDead = true;
    Hooks().is3DLoaded = Always3DLoaded;
    BindInstance(0x0A00C001, "scripts.esm", 0x0300C001);
    TickObjectScripts(TickDelta() * 3.0f);
    Check(State().Var("scripts.esm|00C001", "deaddone") == 0.0f,
          "a pre-placed body never raises OnDeath");

    // 🛑 A LOAD must forget the per-life flags. The instance outlives the
    // session, so a creature killed in one save kept `mDeathSeen` and could
    // never raise `OnDeath` again -- in a new game, or after reloading a save
    // from before the kill. Both softlock any quest that turns on the kill.
    // See: docs/commentary/morrowind_runtime.md#a-load-resets-the-instances
    std::printf("a load forgets that something already died\n");
    ClearInstances();
    State().Reset();
    Hooks().is3DLoaded = Always3DLoaded;

    // Session one: it is alive, then killed.
    g_actorDead = false;
    BindInstance(0x0A00C001, "scripts.esm", 0x0300C001);
    TickObjectScripts(TickDelta());
    g_actorDead = true;
    TickObjectScripts(TickDelta());
    Check(State().Var("scripts.esm|00C001", "deaddone") == 1.0f,
          "it died in the first session");

    // The load: everything from that session goes. State alone, as the old
    // OnRevert did, is NOT enough -- the instance holds the flag.
    ClearInstances();
    State().Reset();
    ResetTickState();

    // Session two, the save from BEFORE the kill: alive again, killed again.
    g_actorDead = false;
    BindInstance(0x0A00C001, "scripts.esm", 0x0300C001);
    TickObjectScripts(TickDelta());
    g_actorDead = true;
    TickObjectScripts(TickDelta());
    Check(State().Var("scripts.esm|00C001", "deaddone") == 1.0f,
          "and it can die AGAIN after a load");

    g_actorDead = false;
    Hooks().isDead = nullptr;
    Hooks().is3DLoaded = nullptr;
    Hooks().playerCell = nullptr;
    Hooks().playerInWorld = nullptr;
    ClearInstances();
}

void TickCases() {
    std::printf("the tick runs bound instances at a fixed rate\n");
    ClearInstances();
    // A tick does nothing until a game is loaded, which a player cell means.
    Hooks().playerCell = nullptr;
    Hooks().playerInWorld = nullptr;
    TickObjectScripts(TickDelta());
    Check(LastTickCount() == 0, "nothing ticks with no game loaded");
    Hooks().playerCell = TestCell;
    Hooks().playerInWorld = InWorld;

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

    // 🛑 An unloaded object stops ticking and is FORGOTTEN, or the bound set
    // only ever grows. See #unload-with-the-cell.
    std::printf("an unloaded object stops ticking\n");
    // It has to have been LOADED first: unloading is a transition, and an
    // instance that was never loaded has not unloaded.
    Hooks().is3DLoaded = Always3DLoaded;
    TickObjectScripts(TickDelta());
    Hooks().is3DLoaded = Never3DLoaded;
    TickObjectScripts(TickDelta());
    Check(LastTickCount() == 0, "an unloaded instance does not run");
    Check(BoundInstanceCount() == 0, "and it is unbound, not kept forever");

    // Its locals survive: TES3 keeps them across an unload.
    Check(State().Var("scripts.esm|00A001", "open") == 1.0f,
          "but its locals are kept for when the cell loads again");
    Hooks().is3DLoaded = nullptr;
    Hooks().playerCell = nullptr;
    Hooks().playerInWorld = nullptr;
    ClearInstances();
    SpawnedTickCases();
    DiscoveryTickCases();
    DeathOnUnloadCases();
    UnnamedCellTickCases();
    PausedTickCases();
}

// 🛑 A paused game must not tick, and the runtime clock must not advance
// through the pause. A Say line aged on wall-clock while a menu held the game
// was freed while the engine still had it, which stranded the subtitle and
// made the engine DROP the next line at that actor.
// See: docs/commentary/morrowind_runtime.md#the-tick-stops-while-the-game-is-paused
void PausedTickCases() {
    std::printf("a menu that pauses the game stops the tick and its clock\n");
    ClearInstances();
    ResetTickState();
    Hooks().is3DLoaded = Always3DLoaded;
    Hooks().playerCell = TestCell;
    Hooks().playerInWorld = InWorld;
    Hooks().gamePaused = FakeGamePaused;

    BindInstance(0x0A000001, "scripts.esm", 0x0300A001);
    g_gamePaused = false;
    TickObjectScripts(TickDelta());
    Check(LastTickCount() == 1, "an unpaused tick runs its instance");
    const double ranTo = GameSeconds();
    Check(ranTo > 0.0, "and the clock advanced with it");

    g_gamePaused = true;
    TickObjectScripts(TickDelta() * 10.0f);
    Check(LastTickCount() == 0, "nothing ticks while a menu holds the game");
    Check(GameSeconds() == ranTo, "and the clock did not move through it");

    g_gamePaused = false;
    TickObjectScripts(TickDelta());
    Check(LastTickCount() == 1, "the tick resumes when the menu closes");
    Check(GameSeconds() > ranTo, "and the clock resumes with it");

    Hooks().gamePaused = nullptr;
    Hooks().is3DLoaded = nullptr;
    Hooks().playerCell = nullptr;
    Hooks().playerInWorld = nullptr;
    ClearInstances();
}

// 🛑 A spawn binds the frame `PlaceAtMe` returns, BEFORE its 3D exists. Reading
// that as "unloaded" unbinds it on its first tick, and nothing ever rebinds a
// spawn -- so the creature's script never runs and the quest that turns on
// killing it cannot advance.
// See: docs/commentary/morrowind_runtime.md#a-spawn-is-not-loaded-on-its-first-frame
void SpawnedTickCases() {
    std::printf("a spawn survives the frames before its 3D loads\n");
    ClearInstances();
    Hooks().playerCell = TestCell;
    Hooks().playerInWorld = InWorld;
    BindSpawnedInstance(0xFF001590, "test_actor");
    Check(BoundInstanceCount() == 1, "the spawn is bound");

    // The frames between PlaceAtMe and the 3D appearing.
    Hooks().is3DLoaded = Never3DLoaded;
    TickObjectScripts(TickDelta() * 3.0f);
    Check(BoundInstanceCount() == 1, "it is NOT dropped before it ever loads");
    Check(LastTickCount() == 0, "and it does not run while unloaded");

    // The 3D arrives: now it ticks.
    Hooks().is3DLoaded = Always3DLoaded;
    TickObjectScripts(TickDelta());
    Check(LastTickCount() == 1, "once loaded its body runs");
    Check(State().Var("spawn:FF001590|001590", "met") == 1.0f,
          "which wrote the spawn's OWN local, under its own key");

    // Having been loaded, a later unload is real and drops the binding.
    Hooks().is3DLoaded = Never3DLoaded;
    TickObjectScripts(TickDelta());
    Check(BoundInstanceCount() == 0, "unloading AFTER a load still unbinds");

    Hooks().is3DLoaded = nullptr;
    Hooks().playerCell = nullptr;
    Hooks().playerInWorld = nullptr;
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

// --- movement and AI -------------------------------------------------------

// What the fake movement hooks recorded, one line per call, and the position
// the fake world reports back so ModScale and the arrival test have a value.
std::vector<std::string> g_moved;
float g_fakeScale = 1.0f;
float g_fakePos[3] = {0.0f, 0.0f, 0.0f};

void Record(const char* format, ...) {
    char line[256];
    va_list args;
    va_start(args, format);
    std::vsnprintf(line, sizeof(line), format, args);
    va_end(args);
    g_moved.push_back(line);
}

void FakeMoveToCell(const std::string& ref, const std::string& cell, float x,
                    float y, float z, float zRot) {
    Record("cell|%s|%s|%g,%g,%g|%g", ref.c_str(), cell.c_str(), x, y, z, zRot);
}

void FakeMoveInCell(const std::string& ref, float x, float y, float z,
                    float zRot) {
    Record("pos|%s|%g,%g,%g|%g", ref.c_str(), x, y, z, zRot);
}

void FakeMoveBy(const std::string& ref, int axis, float delta, bool local) {
    Record("by|%s|%d|%g|%d", ref.c_str(), axis, delta, local ? 1 : 0);
}

void FakeRotateBy(const std::string& ref, int axis, float degrees) {
    Record("rot|%s|%d|%g", ref.c_str(), axis, degrees);
}

float FakeScale(const std::string&) { return g_fakeScale; }

void FakeSetScale(const std::string& ref, float value) {
    g_fakeScale = value;
    Record("scale|%s|%g", ref.c_str(), value);
}

void FakePlaceAtCell(const std::string& base, const std::string& cell,
                     float x, float y, float z, float zRot) {
    Record("place|%s|%s|%g,%g,%g|%g", base.c_str(), cell.c_str(), x, y, z,
           zRot);
}

void FakePlaceNear(const std::string& near, const std::string& base,
                   int count) {
    Record("near|%s|%s|%d", near.c_str(), base.c_str(), count);
}

float FakePosition(const std::string&, int axis) {
    return g_fakePos[axis < 0 || axis > 2 ? 0 : axis];
}

void FakeAiTravel(const std::string& actor, float x, float y, float z) {
    Record("travel|%s|%g,%g,%g", actor.c_str(), x, y, z);
}

void FakeAiWander(const std::string& actor, float range, float duration) {
    Record("wander|%s|%g|%g", actor.c_str(), range, duration);
}

void FakeAiFollow(const std::string& actor, const std::string& target,
                  float duration, float x, float y, float z) {
    Record("follow|%s|%s|%g|%g,%g,%g", actor.c_str(), target.c_str(), duration,
           x, y, z);
}

void FakeAiEscort(const std::string& actor, const std::string& target,
                  float duration, float x, float y, float z) {
    Record("escort|%s|%s|%g|%g,%g,%g", actor.c_str(), target.c_str(), duration,
           x, y, z);
}

void FakeAiActivate(const std::string& actor, const std::string& object) {
    Record("aiactivate|%s|%s", actor.c_str(), object.c_str());
}

void FakeAiFace(const std::string& actor, float x, float y) {
    Record("face|%s|%g,%g", actor.c_str(), x, y);
}

void InstallMoveHooks() {
    Hooks().moveToCell = FakeMoveToCell;
    Hooks().moveInCell = FakeMoveInCell;
    Hooks().moveBy = FakeMoveBy;
    Hooks().rotateBy = FakeRotateBy;
    Hooks().scale = FakeScale;
    Hooks().setScale = FakeSetScale;
    Hooks().placeAtCell = FakePlaceAtCell;
    Hooks().position = FakePosition;
    Hooks().aiTravel = FakeAiTravel;
    Hooks().aiWander = FakeAiWander;
    Hooks().aiFollow = FakeAiFollow;
    Hooks().aiEscort = FakeAiEscort;
    Hooks().aiActivate = FakeAiActivate;
    Hooks().aiFace = FakeAiFace;
}

void ClearMoveHooks() {
    Hooks().moveToCell = nullptr;
    Hooks().moveInCell = nullptr;
    Hooks().moveBy = nullptr;
    Hooks().rotateBy = nullptr;
    Hooks().scale = nullptr;
    Hooks().setScale = nullptr;
    Hooks().placeAtCell = nullptr;
    Hooks().position = nullptr;
    Hooks().aiTravel = nullptr;
    Hooks().aiWander = nullptr;
    Hooks().aiFollow = nullptr;
    Hooks().aiEscort = nullptr;
    Hooks().aiActivate = nullptr;
    Hooks().aiFace = nullptr;
}

// The argument ORDER is what these pin down. Every one of these commands
// takes its arguments in written order and the `->` target last, and getting
// that backwards is silent -- the script runs and moves the wrong thing.
void MoveCases(DialogueContext& context) {
    std::printf("the movement commands\n");
    InstallMoveHooks();
    g_moved.clear();
    g_fakeScale = 1.0f;

    Check(RunResultScript("PositionCell 128 256 512 90 \"Balmora, Guild\"",
                          context),
          "PositionCell compiles and runs");
    // The scanner lowercases every string literal, which is why the tables
    // are keyed lowercase too -- same as the sound ids.
    Check(g_moved.size() == 1 &&
              g_moved[0] == "cell|test_actor|balmora, guild|128,256,512|90",
          "x y z then rotation then the cell, acting on the speaker");

    g_moved.clear();
    RunResultScript("\"other_npc\"->PositionCell 1 2 3 4 \"Elsewhere\"",
                    context);
    Check(g_moved.size() == 1 &&
              g_moved[0] == "cell|other_npc|elsewhere|1,2,3|4",
          "the explicit form acts on the id before `->`");

    g_moved.clear();
    RunResultScript("Position 10 20 30 40", context);
    Check(g_moved.size() == 1 && g_moved[0] == "pos|test_actor|10,20,30|40",
          "Position stays in the cell");

    // 🛑 A rate, scaled by the TICK delta, not the literal argument.
    g_moved.clear();
    RunResultScript("MoveWorld Z 60", context);
    char expected[64];
    std::snprintf(expected, sizeof(expected), "by|test_actor|2|%g|0",
                  60.0f * TickDelta());
    Check(g_moved.size() == 1 && g_moved[0] == expected,
          "MoveWorld is a per-second rate, tick-scaled, on the world axis");

    g_moved.clear();
    RunResultScript("Move X 30", context);
    std::snprintf(expected, sizeof(expected), "by|test_actor|0|%g|1",
                  30.0f * TickDelta());
    Check(g_moved.size() == 1 && g_moved[0] == expected,
          "Move is the same rate in the object's OWN frame");

    g_moved.clear();
    RunResultScript("Rotate Z -110", context);
    std::snprintf(expected, sizeof(expected), "rot|test_actor|2|%g",
                  -110.0f * TickDelta());
    Check(g_moved.size() == 1 && g_moved[0] == expected,
          "Rotate is degrees per second, tick-scaled");

    g_moved.clear();
    RunResultScript("SetScale 2.5", context);
    Check(g_moved.size() == 1 && g_moved[0] == "scale|test_actor|2.5",
          "SetScale sets it");
    g_moved.clear();
    RunResultScript("ModScale 0.5", context);
    Check(g_moved.size() == 1 && g_moved[0] == "scale|test_actor|3",
          "ModScale adds to the CURRENT scale");

    RunResultScript("if ( GetScale == 3 )\n    set TestGlobal to 5\nendif",
                    context);
    Check(State().Global("TestGlobal") == 5.0f, "GetScale reads it back");

    g_moved.clear();
    RunResultScript("PlaceItemCell \"misc_cup\" \"Balmora\" 1 2 3 90",
                    context);
    Check(g_moved.size() == 1 &&
              g_moved[0] == "place|misc_cup|balmora|1,2,3|90",
          "PlaceItemCell takes the id, the cell, then the position");
    g_moved.clear();
    RunResultScript("PlaceItem \"misc_cup\" 4 5 6 12", context);
    Check(g_moved.size() == 1 && g_moved[0] == "place|misc_cup||4,5,6|12",
          "PlaceItem names no cell, so the player's is used");

    // 🛑 PlaceAtPC places at the PLAYER whoever runs it; PlaceAtMe places at
    // the speaker, or at the `->` target. Confusing the two spawns in the
    // wrong place and nothing reports it.
    Hooks().placeNear = FakePlaceNear;
    g_moved.clear();
    RunResultScript("PlaceAtPC \"ex_rat\" 2 64 1", context);
    Check(g_moved.size() == 1 && g_moved[0] == "near|player|ex_rat|2",
          "PlaceAtPC places at the player");
    g_moved.clear();
    RunResultScript("PlaceAtMe \"ex_rat\" 1 64 1", context);
    Check(g_moved.size() == 1 && g_moved[0] == "near|test_actor|ex_rat|1",
          "PlaceAtMe places at the speaker instead");
    g_moved.clear();
    RunResultScript("\"other_npc\"->PlaceAtMe \"ex_rat\" 3 64 1", context);
    Check(g_moved.size() == 1 && g_moved[0] == "near|other_npc|ex_rat|3",
          "and the explicit form at the id before `->`");
    Hooks().placeNear = nullptr;
}

// The AI commands fill a quest alias and the ENGINE runs the package, so what
// is testable headlessly is the argument order reaching each hook -- getting
// that wrong sends the wrong actor somewhere and nothing reports it.
void AiPackageCases(DialogueContext& context) {
    std::printf("the AI package commands\n");
    g_moved.clear();

    Check(RunResultScript("AiTravel 100 200 300", context),
          "AiTravel compiles and runs");
    Check(g_moved.size() == 1 && g_moved[0] == "travel|test_actor|100,200,300",
          "x y z in written order, on the speaker");

    g_moved.clear();
    RunResultScript("\"other_npc\"->AiTravel 1 2 3", context);
    Check(g_moved.size() == 1 && g_moved[0] == "travel|other_npc|1,2,3",
          "the explicit form acts on the id before `->`");

    g_moved.clear();
    RunResultScript("AiWander 512 4 0", context);
    Check(g_moved.size() == 1 && g_moved[0] == "wander|test_actor|512|4",
          "range and duration reach the hook, the idle chances do not");

    g_moved.clear();
    RunResultScript("AiFollow \"player\" 0 0 0 0", context);
    Check(g_moved.size() == 1 &&
              g_moved[0] == "follow|test_actor|player|0|0,0,0",
          "the id comes FIRST, then the duration, then the point");

    g_moved.clear();
    RunResultScript("AiFollowCell \"player\" \"Balmora\" 2 1 2 3", context);
    Check(g_moved.size() == 1 &&
              g_moved[0] == "follow|test_actor|player|2|1,2,3",
          "the Cell form drops the cell and keeps the rest in order");

    g_moved.clear();
    RunResultScript("AiEscort \"player\" 1 4 5 6", context);
    Check(g_moved.size() == 1 &&
              g_moved[0] == "escort|test_actor|player|1|4,5,6",
          "AiEscort reaches its own hook, not follow's");

    g_moved.clear();
    RunResultScript("AiActivate \"a_door\" 0", context);
    Check(g_moved.size() == 1 && g_moved[0] == "aiactivate|test_actor|a_door",
          "AiActivate names the object to use");

    g_moved.clear();
    RunResultScript("Face 500 600", context);
    Check(g_moved.size() == 1 && g_moved[0] == "face|test_actor|500,600",
          "Face turns the actor toward a point");

    ClearMoveHooks();
    Check(RunResultScript("AiTravel 1 2 3", context),
          "with no hook the command still runs");
}

// Every spell command pops exactly its own arguments, with the hooks null.
//
// 🛑 This is what catches a WRONG SEGMENT before it costs a play cycle: a
// handler installed where nothing dispatches leaves its arguments on the
// stack, so the NEXT command reads them and the script derails silently.
// `AddSpell`'s `z` is the sharp case -- the compiler discards it, so popping
// it would take the spell id instead.
// See: docs/commentary/morrowind_runtime.md#spell-commands
void SpellStackDiscipline(DialogueContext& context) {
    std::printf("the spell and effect tables load both their keys\n");
    const SpellDef* spell = FindSpell("Fire Bite");
    Check(spell && spell->form.formId == 0x801 &&
              spell->effects.size() == 2 && spell->Has(14) && spell->Has(45),
          "a spell resolves by id, case-folded, with EVERY effect index");
    Check(spell && !spell->Has(16), "and reports an effect it does not carry");
    Check(FindSpell("no effect spell") &&
              FindSpell("no effect spell")->effects.empty(),
          "a spell with no staged effect resolves and carries none");
    // What RemoveEffects acts on: the spells CONTAINING one effect, which is
    // "fire bite" for 45 and both spells for neither's 99.
    Check(SpellsWithEffect(45).size() == 1 &&
              SpellsWithEffect(14).size() == 1 &&
              SpellsWithEffect(99).empty(),
          "RemoveEffects selects the spells that CONTAIN an effect");

    // The soul gem tables: a creature's soul, and the filled gem per size.
    Check(CreatureSoul("TEST_RAT") == 1 &&
              CreatureSoul("test_golden_saint") == 5 &&
              CreatureSoul("no such creature") == 0,
          "a creature's soul resolves, case-folded");
    Check(FilledSoulGemId("Misc_SoulGem_Grand", 5) ==
              "misc_soulgem_grand_filled5" &&
              FilledSoulGemId("Misc_SoulGem_Grand", 3).empty(),
          "a filled gem resolves per SOUL SIZE, and only when staged");
    Check(FilledSoulGemIds(1).size() == 2 && FilledSoulGemIds(5).size() == 1,
          "every gem holding one soul size is found");
    Check(FindEffect("firedamage") && FindEffectByIndex(14) &&
              FindEffect("firedamage")->formId == 0x901,
          "an effect resolves by NAME and by TES3 INDEX alike");

    std::printf("the spell commands pop exactly their arguments\n");
    static const char* kScripts[] = {
        "AddSpell \"fire bite\"\n",
        "AddSpell \"fire bite\" 1\n",
        "RemoveSpell \"fire bite\"\n",
        "RemoveSpellEffects \"fire bite\"\n",
        "RemoveEffects 14\n",
        "Cast \"fire bite\" \"player\"\n",
        "ExplodeSpell \"fire bite\"\n",
        "\"other_npc\"->AddSpell \"fire bite\"\n",
        "\"other_npc\"->RemoveSpell \"fire bite\"\n",
        "AddSoulGem \"test_rat\" \"Misc_SoulGem_Petty\"\n",
        "AddSoulGem \"test_rat\" \"Misc_SoulGem_Petty\" 1\n",
        "RemoveSoulGem \"test_rat\"\n",
        "RemoveSoulGem \"test_rat\" 2\n",
        "DropSoulGem \"test_rat\"\n",
        "\"other_npc\"->RemoveSoulGem \"test_rat\"\n",
    };
    for (const char* script : kScripts) {
        const std::string source = std::string(script) + "ModDisposition 3\n";
        State().SetDisposition("test_actor", 50);
        Check(RunResultScript(source, context) &&
                  State().Disposition("test_actor") == 53,
              script);
    }
    static const char* kQueries[] = {
        "if ( GetSpell \"fire bite\" == 0 )\n    ModDisposition 3\nendif\n",
        "if ( GetEffect sEffectFireDamage == 0 )\n    ModDisposition 3\nendif\n",
        "if ( GetSpellEffects \"fire bite\" == 0 )\n    ModDisposition 3\nendif\n",
        "if ( HasSoulGem \"test_rat\" == 0 )\n    ModDisposition 3\nendif\n",
    };
    for (const char* script : kQueries) {
        State().SetDisposition("test_actor", 50);
        Check(RunResultScript(script, context) &&
                  State().Disposition("test_actor") == 53,
              script);
    }
}

// What the last applyMovementFlag hook was handed.
int  g_appliedFlag = -1;
bool g_appliedOn = false;

void RecordMovementFlag(const std::string&, int which, bool on) {
    g_appliedFlag = which;
    g_appliedOn = on;
}

// The forced-sneak latch: set, read back, clear, and stay per actor. The
// getter must answer what the SETTER wrote, never what the body is doing,
// which is the whole reason the DLL owns the flag.
//
// 🛑 Run, Jump and MoveJump are deliberate no-ops, not latches: Skyrim can
// force no gait, so they must NOT answer their getter. A latch there would
// read as ported and move nothing.
// 🛑 One log line per (stub, script), not per stub. The old rule was
// `++count == 1` globally, so a second quest reaching the same unported
// command was SILENT -- the log could say `Say` did nothing without saying
// which of TR's 184 call sites it was.
// See: docs/commentary/morrowind_runtime.md#logging-names-the-script
void UnportedSiteCases(Interpreter::Context& context) {
    std::printf("an unported command logs once per SITE\n");
    const std::size_t before = UnportedSitesLogged();
    SetRunningTopic("first topic");
    RunResultScript("GetWindSpeed", context);
    const std::size_t first = UnportedSitesLogged();
    Check(first == before + 1, "a new stub logs a site");
    RunResultScript("GetWindSpeed", context);
    Check(UnportedSitesLogged() == first,
          "the same stub in the same script does not log again");
    SetRunningTopic("second topic");
    RunResultScript("GetWindSpeed", context);
    Check(UnportedSitesLogged() == first + 1,
          "the same stub in ANOTHER script logs a second site");
    SetRunningTopic("");
}

void ForcedMovementCases(Interpreter::Context& context) {
    std::printf("the forced sneak flag latches per actor\n");
    State().SetDisposition("test_actor", 50);
    Check(RunResultScript("ForceSneak\n", context) &&
              State().MovementFlag("test_actor", 0),
          "ForceSneak latches on");
    Check(RunResultScript("if ( GetForceSneak == 1 )\n"
                          "    ModDisposition 3\nendif\n", context) &&
              State().Disposition("test_actor") == 53,
          "GetForceSneak reads the latch back");
    Check(RunResultScript("ClearForceSneak\n", context) &&
              !State().MovementFlag("test_actor", 0),
          "ClearForceSneak clears it");
    State().SetDisposition("test_actor", 50);
    Check(RunResultScript("ForceRun\n"
                          "if ( GetForceRun == 0 )\n"
                          "    ModDisposition 3\nendif\n", context) &&
              State().Disposition("test_actor") == 53,
          "ForceRun is a no-op: its getter stays 0");
    Check(RunResultScript("\"other_npc\"->ForceSneak\n", context) &&
              State().MovementFlag("other_npc", 0) &&
              !State().MovementFlag("test_actor", 0),
          "an explicit target latches only that actor");
    // 🛑 The latch must REACH the game. A local copy of the flag number went
    // stale when the enum was cut to sneak-only, so every apply returned
    // early and the actors ran around as normal for a whole playthrough.
    g_appliedFlag = -1;
    Hooks().applyMovementFlag = RecordMovementFlag;
    State().SetMovementFlag("test_actor", kForceSneak, true);
    Hooks().applyMovementFlag = nullptr;
    Check(g_appliedFlag == kForceSneak && g_appliedOn,
          "the latch reaches the game with the SHARED flag value");
    // Reset() wipes the journal and dispositions the later cases were set up
    // with, so the whole state is restored from its own serialization.
    State().SetMovementFlag("save_me", 0, true);
    const std::string before = State().Serialize();
    State().Reset();
    const bool kept = State().Deserialize(before) > 0 &&
                      State().MovementFlag("save_me", 0);
    State().SetMovementFlag("save_me", 0, false);
    State().SetMovementFlag("other_npc", 0, false);
    Check(kept, "a latch survives the co-save round trip");
    // The cases after this one open on the fixture's own dispositions.
    State().SetDisposition("test_actor", 50);
    State().SetDisposition("other_npc", 50);
}

// The last control-switch set the hook was handed, and how many times.
bool g_pushed[kControlSwitchCount] = {true, true, true, true, true, true, true};
int  g_pushCount = 0;

void RecordControlSwitches(const bool* on, int count) {
    for (int i = 0; i < count && i < kControlSwitchCount; ++i) {
        g_pushed[i] = on[i];
    }
    ++g_pushCount;
}

// The seven player-control switches. Each is registered by the compiler in a
// LOOP -- `opcodeEnable + i` -- so an install that gets the index wrong lands
// the handler on a NEIGHBOURING switch and the failure is silent: the command
// runs, and the wrong boolean moves.
//
// 🛑 The getter asks the NEGATIVE (`GetPlayerControlsDisabled`), so a switch
// that is enabled must answer 0.
// See: docs/commentary/morrowind_runtime.md#the-control-switches
void ControlSwitchCases(Interpreter::Context& context) {
    std::printf("the player-control switches latch per switch\n");
    Check(State().ControlEnabled(kPlayerControls) &&
              State().ControlEnabled(kPlayerViewSwitch),
          "every switch starts enabled");
    Check(RunResultScript("DisablePlayerControls\n", context) &&
              !State().ControlEnabled(kPlayerControls),
          "DisablePlayerControls turns its own switch off");
    // The index is what a loop install gets wrong, so prove the OTHERS did
    // not move with it.
    Check(State().ControlEnabled(kPlayerFighting) &&
              State().ControlEnabled(kPlayerViewSwitch),
          "and moves no neighbouring switch");
    State().SetDisposition("test_actor", 50);
    Check(RunResultScript("if ( GetPlayerControlsDisabled == 1 )\n"
                          "    ModDisposition 3\nendif\n", context) &&
              State().Disposition("test_actor") == 53,
          "the getter answers the NEGATIVE of the switch");
    Check(RunResultScript("EnablePlayerControls\n", context) &&
              State().ControlEnabled(kPlayerControls),
          "EnablePlayerControls turns it back on");
    // Each INSTALLED switch must reach its own slot, which is the whole of
    // what the loop install can get wrong.
    const std::pair<int, const char*> kDisable[] = {
        {kPlayerControls, "DisablePlayerControls\n"},
        {kPlayerFighting, "DisablePlayerFighting\n"},
        {kPlayerLooking, "DisablePlayerLooking\n"},
        {kPlayerViewSwitch, "DisablePlayerViewSwitch\n"}};
    bool eachHitsItsOwn = true;
    for (const auto& one : kDisable) {
        RunResultScript(one.second, context);
        if (!State().ControlEnabled(one.first)) continue;
        eachHitsItsOwn = false;
    }
    Check(eachHitsItsOwn, "each mapped disable reaches its own switch");
    // 🛑 The set must REACH the game, and only on a CHANGE: a chargen script
    // re-runs `EnablePlayerControls` every tick it is in state 2.
    g_pushCount = 0;
    Hooks().applyControlSwitches = RecordControlSwitches;
    State().SetControlEnabled(kPlayerControls, true);
    const int afterChange = g_pushCount;
    State().SetControlEnabled(kPlayerControls, true);
    Hooks().applyControlSwitches = nullptr;
    Check(afterChange == 1 && g_pushCount == 1 && g_pushed[kPlayerControls],
          "the set reaches the game once per CHANGE");
    const std::string before = State().Serialize();
    State().Reset();
    const bool kept = State().Deserialize(before) > 0 &&
                      !State().ControlEnabled(kPlayerViewSwitch) &&
                      State().ControlEnabled(kPlayerControls);
    Check(kept, "the switches survive the co-save round trip");
    for (int i = 0; i < kControlSwitchCount; ++i) {
        State().SetControlEnabled(i, true);
    }
    State().SetDisposition("test_actor", 50);
    State().SetDisposition("other_npc", 50);
}

// The fixtures, beside the EXECUTABLE rather than the working directory: the
// shell wrapper every command goes through runs from the repo root, so a
// relative path loaded nothing and every table-backed case failed.
std::string FixtureDir() {
    char exe[MAX_PATH] = {0};
    const DWORD n = GetModuleFileNameA(nullptr, exe, MAX_PATH);
    std::string path(exe, n);
    const std::size_t slash = path.find_last_of('\\');
    if (slash == std::string::npos) return "testdata\\scripts\\";
    return path.substr(0, slash + 1) + "testdata\\scripts\\";
}

// `GetCurrentTime` is the `gamehour` global, which the game refreshes every
// tick -- not a number of its own that could drift from the clock conditions.
void CurrentTimeCases(DialogueContext& context) {
    std::printf("GetCurrentTime reads the game hour\n");
    State().SyncGlobal("gamehour", 14.0f);
    State().SetDisposition("test_actor", 50);
    Check(RunResultScript("if ( GetCurrentTime == 14 )\n"
                          "    ModDisposition 3\nendif\n", context) &&
              State().Disposition("test_actor") == 53,
          "the hour the clock holds is the hour it answers");
    State().SetDisposition("test_actor", 50);
}

// The authored placement the sidecar carries, which SetAtStart restores and
// GetStartingPos/GetStartingAngle read. A row written before the columns
// existed keeps none, rather than reporting the origin as its home.
void PlacementCases(DialogueContext& context) {
    std::printf("the authored placement is read back\n");
    const float* at = FindPlacement("test_door");
    Check(at && at[0] == 10.0f && at[2] == 30.0f, "position comes from the row");
    Check(at && at[5] == 90.0f, "the angle is the row's, in degrees");
    Check(FindPlacement("test_actor") == nullptr,
          "a row without the columns has no placement");
    State().SetDisposition("test_actor", 50);
    Check(RunResultScript("if ( \"test_door\"->GetStartingPos z == 30 )\n"
                          "    ModDisposition 3\nendif\n", context) &&
              State().Disposition("test_actor") == 53,
          "GetStartingPos answers the authored z");
    State().SetDisposition("test_actor", 50);
    Check(RunResultScript("if ( \"test_door\"->GetStartingAngle z == 90 )\n"
                          "    ModDisposition 3\nendif\n", context) &&
              State().Disposition("test_actor") == 53,
          "GetStartingAngle answers the authored rotation");
    State().SetDisposition("test_actor", 50);
}

// `RaiseRank`/`LowerRank` move the NPC's OWN rank, starting from the one its
// record authors, and LowerRank stops at 0 rather than resigning.
void ActorRankCases(DialogueContext& context) {
    std::printf("RaiseRank and LowerRank move the NPC's own rank\n");
    Check(State().ActorRank("test_actor") == 8, "starts at the authored rank");
    Check(RunResultScript("RaiseRank\n", context) &&
              State().ActorRank("test_actor") == 9,
          "RaiseRank promotes from the authored rank");
    Check(RunResultScript("LowerRank\n", context) &&
              State().ActorRank("test_actor") == 8,
          "LowerRank demotes from the moved rank");
    State().SetActorRank("test_actor", 0);
    Check(RunResultScript("LowerRank\n", context) &&
              State().ActorRank("test_actor") == 0,
          "LowerRank stops at 0");
    Check(RunResultScript("\"player\"->RaiseRank\n", context) &&
              State().ActorRank("player") == -1,
          "the player has no rank of its own to raise");
    State().SetActorRank("test_actor", 8);
}

// `GetButtonPressed` hands the click out ONCE: a script polls it every tick,
// so a value that stayed set would re-fire the branch on the next frame.
void ButtonPressedCases(DialogueContext& context) {
    std::printf("GetButtonPressed answers once, then -1\n");
    State().buttonPressed = 1;
    State().SetDisposition("test_actor", 50);
    const char* kPoll =
        "if ( GetButtonPressed == 1 )\n    ModDisposition 3\nendif\n";
    Check(RunResultScript(kPoll, context) &&
              State().Disposition("test_actor") == 53,
          "the clicked button is read back");
    Check(State().buttonPressed == -1, "reading it clears it");
    State().SetDisposition("test_actor", 50);
    Check(RunResultScript(kPoll, context) &&
              State().Disposition("test_actor") == 50,
          "a second poll sees no button");
}

void Cases() {
    ClearScriptTables();
    LoadScriptTables(FixtureDir());
    GameActor actor("test_actor");
    DialogueContext context(actor, "Test Actor", "Player");
    ObjectScriptTableCases();
    ObjectScriptRunCases();
    TickCases();
    FactionCases(context, actor);
    RankNameCases(context);
    CoSaveCases(context);
    AiAndDeathCases(context, actor);
    GlobalScriptCases(context);
    SharedLocalsCases(context);
    StartScriptCases();
    TravelCases();
    StatCases(context);
    DispositionReappliesFight(context);
    PersuasionCases(context);
    SoundCases(context);
    MoveCases(context);
    AiPackageCases(context);
    ForcedMovementCases(context);
    ControlSwitchCases(context);
    UnportedSiteCases(context);
    ButtonPressedCases(context);
    ActorRankCases(context);
    PlacementCases(context);
    CurrentTimeCases(context);
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

    SpellStackDiscipline(context);
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
        LoadScriptTables(argc > 3 ? std::string(argv[3]) : FixtureDir());
        return CompileObjectFile(argv[2]);
    }
    if (argc > 1) return RunFile(argv[1]);
    Cases();
    std::printf("%s\n", g_failed ? "FAILED" : "all passed");
    return g_failed ? 1 : 0;
}
