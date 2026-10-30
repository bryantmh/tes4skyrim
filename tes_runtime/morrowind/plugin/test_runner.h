// MWScript test cases run in the live game, for headless end-to-end testing.
//
// Off unless the TES_MW_TEST_CASES environment variable names a case file.
// Once the player is in the world, the object tick gives the runner one step
// per tick. A case is a list of steps run in order: a script under its actor,
// a wait, an `expect` judged through the same compiler, or a click a player
// makes -- talking to an NPC, asking a topic, answering a choice, Goodbye, a
// MessageBox button. One JSON line per case goes to TES_MW_TEST_RESULTS, then
// a closing summary line.
// See: docs/commentary/headless_testing.md#mwscript-cases

#pragma once

#include <istream>
#include <string>
#include <vector>

namespace tesruntime::mw {

struct TestStep {
    enum class Kind {
        kRun, kWait, kExpect, kTalk, kAsk, kChoose, kBye, kButton, kActivate, kUntil, kPick, kClose,
        kTake, kConsole, kPersuade, kSleep, kWalk, kFollow, kDefend, kPunch, kRest
    };
    Kind kind = Kind::kRun;
    std::string text;   // the script, condition, NPC id or topic
    int number = 0;     // the ticks, choice or button
};

struct TestCase {
    std::string name;
    std::string actor = "player";   // the speaker: untargeted commands act on it
    std::vector<TestStep> steps;
};

// The conversation window's clicks (conversation.h), handed over when the
// window installs, so the runner links without the menu code.
struct TestConversation {
    bool (*open)() = nullptr;
    std::vector<std::string> (*topics)() = nullptr;
    bool (*ask)(const std::string& topic) = nullptr;
    bool (*choose)(int index) = nullptr;
    bool (*bye)() = nullptr;
    std::string (*lastText)() = nullptr;
    std::vector<std::string> (*choices)() = nullptr;
    bool (*persuade)(const std::string& kind) = nullptr;
    // A menu still opening, and a player move still landing (menu.h).
    bool (*menuOpening)() = nullptr;
    bool (*playerMovePending)() = nullptr;
};

void SetTestConversation(const TestConversation& conversation);

// The cases in a case file:
//   case <name> / actor <id> / run, then MWScript lines / wait <ticks> /
//   expect <condition> / talk <npc> / ask <topic> / choose <n> / bye /
//   button <n> / activate <ref> / until <ticks> <condition> / pick <class or
//   sign> / close <menu> / take <ref> / console <Skyrim console command> /
//   persuade <admire|intimidate|taunt|bribe10|bribe100|bribe1000> /
//   follow <npc> (keeps the player on the NPC's trail; a bare `follow` stops) /
//   defend (the player kills whatever attacks it, as `follow` also does, for
//   the rest of the case) / punch <n> <npc> (up to n bare-fisted punches; a
//   greeting ends it) /
//   end. `#` starts a comment line. Steps run in file
//   order, and an action not followed by a `wait` settles for 2 ticks. `until`
//   fails the case when its condition does not hold within the ticks; `button`,
//   `pick` and `close` wait for their menu to open first. With
//   TES_MW_TEST_CHECKPOINTS set, the runner pauses after each passing case
//   until `<results>.ack` holds the count of finished cases (the harness saves
//   the game, then writes it).
std::vector<TestCase> ParseTestCases(std::istream& in);

// Each step kind's directive, in Kind order.
const std::vector<std::string>& TestStepNames();

// The commands in `real` (lower case) that no case's script or expect names.
std::vector<std::string> UncoveredCommands(const std::vector<TestCase>& cases,
                                           const std::vector<std::string>& real);

// One step of the runner; nothing at all when no case file is set. `held`:
// a menu other than the conversation holds the game, so a script or a new
// `talk` waits for it to close.
void TickTestRunner(bool held);

}  // namespace tesruntime::mw
