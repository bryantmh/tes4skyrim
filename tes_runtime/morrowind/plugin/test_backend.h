// MorrowindRuntime's side of the shared test runner (common/test_runner.h):
// MWScript `run` and `expect` judged in game, and the game hooks the steps use.
//
// Off unless the TES_MW_TEST_CASES environment variable names a case file.
// Once the player is in the world, the object tick gives the runner one step
// per tick. Results go to TES_MW_TEST_RESULTS.
// See: docs/commentary/headless_testing.md#mwscript-cases

#pragma once

#include <string>
#include <vector>

#include "test_runner.h"

namespace tesruntime::mw {

using test::ParseTestCases;
using test::TestCase;
using test::TestStep;
using test::TestStepNames;
using test::UncoveredCommands;

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
    bool (*with)(const std::string& npc) = nullptr;
};

void SetTestConversation(const TestConversation& conversation);

// One step of the runner; nothing at all when no case file is set. `held`:
// a menu other than the conversation holds the game, so a script or a new
// `talk` waits for it to close.
void TickTestRunner(bool held);

}  // namespace tesruntime::mw
