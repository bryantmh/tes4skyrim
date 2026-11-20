// Quest and opcode test cases run in the live game, for headless end-to-end
// testing. The one step loop every runtime shares: the case grammar, the steps,
// the results file and the harness handshake. Each runtime hands over its game
// side as a TestBackend (MorrowindRuntime: plugin/test_backend.cpp; TESRuntime:
// tes/test_game.cpp), so a `.mwtest` and a `.tes4test` are the same language.
//
// Off unless `<envPrefix>CASES` names a case file. Once the player is in the
// world, every TickTestRunner call gives the runner one step. One JSON line per
// case goes to `<envPrefix>RESULTS`, then a closing summary line.
//
// A backend with no `run` or `judge` of its own leaves them to the harness:
// the runner writes {"run": ...} or {"judge": ...} and waits until
// `<results>.ack` counts that line; a judge's answer is `<verdict> <left>` in
// `<results>.answer`.
// See: docs/commentary/headless_testing.md#tes4-quest-cases

#pragma once

#include <istream>
#include <string>
#include <vector>

namespace tesruntime::test {

struct TestStep {
    enum class Kind {
        kRun, kWait, kExpect, kTalk, kAsk, kChoose, kBye, kButton, kActivate, kUntil, kPick, kClose,
        kTake, kConsole, kPersuade, kSleep, kWalk, kFollow, kDefend, kPunch, kRest, kSteer, kApproach,
        kPace, kHear, kUnsaid, kSaid, kUnlisted
    };
    Kind kind = Kind::kRun;
    std::string text;   // the script, condition, reference or topic
    int number = 0;     // the ticks, choice or button
};

// One line an actor began to speak: the speaker's runtime FormID in hex, the text, and
// when it began in real (GetTickCount64) and game milliseconds, which the harness's
// `quiet` measures silences by.
struct SpokenLine {
    std::string speaker;
    std::string text;
    unsigned long long ms = 0;
    unsigned game = 0;
};

struct TestCase {
    std::string name;
    std::string actor = "player";   // the speaker: untargeted commands act on it
    std::vector<TestStep> steps;
};

// What the game answers and does for the runner. Every hook is optional; a
// step whose hook is missing fails its case naming the step.
struct TestBackend {
    // The environment variables' prefix: TES_MW_TEST_ (MorrowindRuntime) or TES_TEST_.
    const char* envPrefix = "TES_TEST_";
    // Once the case file is read.
    void (*start)() = nullptr;
    // A `run` body under `actor`; false when it did not compile or run.
    bool (*run)(const std::string& actor, const std::string& source) = nullptr;
    // 1 holds, 0 does not, -1 did not evaluate; when `left` is given and the
    // condition does not hold, it gets the left side's value as JSON.
    int (*judge)(const std::string& actor, const std::string& condition, std::string* left) = nullptr;
    // A loaded game: the player stands in some cell.
    bool (*inWorld)() = nullptr;
    // A menu other than the conversation holds the game.
    bool (*held)() = nullptr;
    bool (*menuOpen)(const std::string& name) = nullptr;
    // A menu still opening, and a player move still landing.
    bool (*menuOpening)() = nullptr;
    bool (*moveLanding)() = nullptr;
    // The player activating `ref`; false while it has not loaded yet.
    bool (*activate)(const std::string& ref) = nullptr;
    // How far the player stands from door `ref`'s origin; who follows the player, for a failure.
    float (*doorDistance)(const std::string& ref) = nullptr;
    std::string (*followerNames)() = nullptr;
    // Where `ref` is, for a failure that names it.
    std::string (*whereIs)(const std::string& ref) = nullptr;
    // `approach`: 1 when a move was posted, 0 when none is needed, -1 while `ref` does not resolve.
    int (*approach)(const std::string& ref) = nullptr;
    void (*take)(const std::string& ref) = nullptr;
    void (*pressButton)(int button) = nullptr;
    void (*closeMenu)(const std::string& name) = nullptr;
    bool (*menuPick)(const std::string& name) = nullptr;
    void (*sleepFor)(int hours) = nullptr;
    // The Wait key, for `rest` with no bed.
    void (*wait)() = nullptr;
    bool (*punch)(const std::string& npc) = nullptr;
    // The player walks to `x y z` (walk), or to a reference or `x y z` (walkTo), on
    // the engine's own pathing; steer drives it there at the controls.
    void (*walk)(float x, float y, float z) = nullptr;
    void (*walkTo)(const std::string& where) = nullptr;
    void (*steer)(float x, float y, float z, bool walking) = nullptr;
    bool (*walkDone)() = nullptr;
    void (*walkStop)() = nullptr;
    // The walk's travel has stopped moving the player; the steer is stuck.
    bool (*walkStalled)() = nullptr;
    bool (*walkStuck)() = nullptr;
    float (*position)(const std::string& ref, int axis) = nullptr;
    float (*distance)(const std::string& from, const std::string& to) = nullptr;
    void (*placeHere)(const std::string& ref, float x, float y, float z, float zRot) = nullptr;
    // `follow` fills in the followed NPC's trail between two looks. Off (the default) leaves
    // `follow` exactly as it is for the game that does not set it.
    bool fillTrail = false;
    // `follow` distances for a game whose escorts wait sooner than the defaults allow: how far the
    // NPC may get ahead, and how far behind it the player is put. 0 (the default) keeps the runner's own.
    float followSlack = 0.0f;
    float followBehind = 0.0f;
    // Clicks the only button of an open message box that holds a step needing the world, as a player
    // clicks a notice's OK; true when it did. Null (the default): a held box fails the case.
    bool (*dismissNotice)() = nullptr;
    // How many times `talk` clicks an NPC whose click opened nothing, as a player clicks again; 0 (the
    // default) keeps the runner's single click.
    int talkClicks = 0;
    bool (*inInterior)() = nullptr;
    std::string (*cell)() = nullptr;
    // `pace <speed>`: a --fast game's speed from here on; 0 is the default.
    void (*setPace)(float speed) = nullptr;
    // `defend` and `follow`: the player kills whatever attacks it.
    void (*killPlayerAttackers)() = nullptr;
    // `defend <ally>`: the same, and whatever attacks `ally` too (the player fights beside it).
    void (*killAttackersOf)(const std::string& ally) = nullptr;
    // The conversation's clicks.
    bool (*talking)() = nullptr;
    // Whether the open conversation is with `npc` (id or name); null = any open one counts.
    bool (*talkingWith)(const std::string& npc) = nullptr;
    std::vector<std::string> (*topics)() = nullptr;
    bool (*ask)(const std::string& topic) = nullptr;
    bool (*choose)(int index) = nullptr;
    std::vector<std::string> (*choices)() = nullptr;
    bool (*bye)() = nullptr;
    bool (*persuade)(const std::string& kind) = nullptr;
    std::string (*lastText)() = nullptr;
    // The lines actors began to speak since the last call, in order. Each goes
    // to the results the tick it is read, and `hear` waits on them.
    std::vector<SpokenLine> (*spoken)() = nullptr;
    // The game clock in milliseconds (0 when unknown), stamped beside real time on what `quiet` measures.
    unsigned (*gameMs)() = nullptr;
    // Names first reached since the last call (stubbed commands), for the case's line.
    std::vector<std::string> (*newIssues)() = nullptr;
    // Commands with a real implementation; the summary lists those no case names.
    std::vector<std::string> (*realCommands)() = nullptr;
};

void SetTestBackend(const TestBackend& backend);

// Whether TES_TEST_CASES names a case file: the runner has work in this game.
bool TestCasesRequested();

// The cases in a case file:
//   case <name> / actor <id> / run, then script lines / wait <ticks> /
//   expect <condition> / talk <npc> / ask <topic or service row> / choose <n>
//   (a choice, or row n of an open Travel or Persuasion list) / bye [if open | locked] /
//   button <n> / activate <ref> / until <ticks> <condition> / pick <class or
//   sign> / close <menu> / take <ref> / console <Skyrim console command> /
//   persuade <admire|intimidate|taunt|bribe10|bribe100|bribe1000> [until <condition>] /
//   sleep <hours> / walk <x y z | ref> / steer <x y z> [walk] /
//   follow <npc> (keeps the player on the NPC's trail; a bare `follow` stops) /
//   defend (the player kills whatever attacks it, as `follow` also does, for
//   the rest of the case) / punch <n> <npc> (up to n bare-fisted punches; a
//   greeting ends it) / rest <max> <bed | -> <condition> / approach <npc> (the
//   player is put in front of the NPC, wherever it stands, when out of reach --
//   NPCs move; never with a follower along) / pace <speed> (a --fast game is
//   steered to <speed>x real time for the rest of the case) /
//   hear <ticks> <speaker | any> [text] / end.
//   `#` starts a comment line. Steps run in file order, and an action not
//   followed by a `wait` settles for 2 ticks (`bye` for 30). `until` fails the
//   case when its condition does not hold within the ticks; `hear` when the
//   speaker has not begun a line containing the text (any case) within them;
//   `button`, `pick` and `close` wait for their menu, `ask` for its topic. With
//   `<envPrefix>CHECKPOINTS` set, the runner pauses after each passing case
//   until `<results>.ack` holds the count of finished cases (the harness saves
//   the game, then writes it).
std::vector<TestCase> ParseTestCases(std::istream& in);

// Each step kind's directive, in Kind order.
const std::vector<std::string>& TestStepNames();

// The commands in `real` (lower case) that no case's script or expect names.
std::vector<std::string> UncoveredCommands(const std::vector<TestCase>& cases,
                                           const std::vector<std::string>& real);

// One step of the runner; nothing at all when no case file is set or the
// player is not in the world.
void TickTestRunner();

// Forgets the run, so the next tick reads the case file again (headless tests).
void ResetTestRunner();

}  // namespace tesruntime::test
