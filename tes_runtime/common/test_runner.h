// Quest and opcode test cases run in the live game, for headless end-to-end
// testing. The game-independent half: the case grammar, the step loop, the
// results file and the harness handshake. Each runtime hands over its game
// side as a TestBackend.
//
// Off unless TES_TEST_CASES names a case file. Once the player is in the
// world, every TickTestRunner call gives the runner one step. One JSON line per
// case goes to TES_TEST_RESULTS, then a closing summary line.
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
        kTake, kConsole, kPersuade, kSleep, kWalk, kHear
    };
    Kind kind = Kind::kRun;
    std::string text;   // the script, condition, reference or topic
    int number = 0;     // the ticks, choice or button
};

// One line an actor began to speak: the speaker's runtime FormID in hex and the text.
struct SpokenLine {
    std::string speaker;
    std::string text;
};

struct TestCase {
    std::string name;
    std::string actor = "player";   // the speaker: untargeted commands act on it
    std::vector<TestStep> steps;
};

// What the game answers and does for the runner. Every hook is optional; a
// step whose hook is missing fails its case naming the step.
struct TestBackend {
    // A `run` body under `actor`; false when it did not compile or run.
    bool (*run)(const std::string& actor, const std::string& source) = nullptr;
    // 1 holds, 0 does not, -1 did not evaluate; `left` gets the left side's value as JSON.
    int (*judge)(const std::string& actor, const std::string& condition, std::string* left) = nullptr;
    // A loaded game: the player stands in some cell.
    bool (*inWorld)() = nullptr;
    // A menu other than the conversation holds the game.
    bool (*held)() = nullptr;
    bool (*menuOpen)(const std::string& name) = nullptr;
    // The player activating `ref`; false while it has not loaded yet.
    bool (*activate)(const std::string& ref) = nullptr;
    void (*take)(const std::string& ref) = nullptr;
    void (*pressButton)(int button) = nullptr;
    void (*closeMenu)(const std::string& name) = nullptr;
    bool (*menuPick)(const std::string& name) = nullptr;
    void (*sleepFor)(int hours) = nullptr;
    // The player walks to a reference or to `x y z`, on the engine's own pathing.
    void (*walk)(const std::string& where) = nullptr;
    bool (*walkDone)() = nullptr;
    void (*walkStop)() = nullptr;
    // The conversation's clicks.
    bool (*talking)() = nullptr;
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
//   expect <condition> / talk <npc> / ask <topic> / choose <n> / bye /
//   button <n> / activate <ref> / until <ticks> <condition> / pick <name> /
//   close <menu> / take <ref> / console <Skyrim console command> /
//   persuade <kind> / sleep <hours> / walk <ref | x y z> /
//   hear <ticks> <speaker | any> [text] / end.
//   `#` starts a comment line. Steps run in file order, and an action not
//   followed by a `wait` settles for 2 ticks. `until` fails the case when its
//   condition does not hold within the ticks; `hear` when the speaker has not
//   begun a line containing the text (any case) within them, counting lines
//   since the case began that no earlier `hear` took; `button`, `pick` and `close`
//   wait for their menu to open first. With TES_TEST_CHECKPOINTS set, the
//   runner pauses after each passing case until `<results>.ack` holds the
//   count of finished cases (the harness saves the game, then writes it).
std::vector<TestCase> ParseTestCases(std::istream& in);

// The commands in `real` (lower case) that no case's script or expect names.
std::vector<std::string> UncoveredCommands(const std::vector<TestCase>& cases,
                                           const std::vector<std::string>& real);

// One step of the runner; nothing at all when no case file is set or the
// player is not in the world.
void TickTestRunner();

// Forgets the run, so the next tick reads TES_TEST_CASES again (headless tests).
void ResetTestRunner();

}  // namespace tesruntime::test
