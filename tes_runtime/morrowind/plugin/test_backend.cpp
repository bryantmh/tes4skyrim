// MorrowindRuntime's game side of the shared test runner; see test_backend.h.

#include "test_backend.h"

#include <algorithm>

#include "dialogue_state.h"
#include "game_actor.h"
#include "script_context.h"
#include "script_runner.h"

namespace tesruntime::mw {

namespace {

// The global an `expect` writes its verdict into: 1 holds, 0 does not, and
// the -1 it is primed with survives only a probe that did not run.
constexpr const char* kVerdictGlobal = "TestResult";

TestConversation g_talk;
bool g_held = false;
std::vector<std::string> g_unportedBefore;

// Runs `source` with `actor` as the speaker; false when it did not compile or threw.
bool RunAs(const std::string& actor, const std::string& source) {
    GameActor view(actor);
    DialogueContext context(view, actor, "Player");
    return RunResultScript(source, context);
}

// The value of `condition`'s left side as JSON, or null when it does not evaluate.
std::string LeftValue(const std::string& actor, const std::string& condition) {
    std::size_t op = condition.find_first_of("=!<>");
    while (op != std::string::npos && op > 0 && condition.compare(op - 1, 2, "->") == 0) {
        op = condition.find_first_of("=!<>", op + 1);
    }
    if (op == std::string::npos) return "null";
    constexpr float kUnset = -12345.0f;
    State().SetGlobal(kVerdictGlobal, kUnset);
    const std::string probe = std::string("set ") + kVerdictGlobal + " to " + condition.substr(0, op) + "\n";
    const float value = RunAs(actor, probe) ? State().Global(kVerdictGlobal) : kUnset;
    return value == kUnset ? "null" : std::to_string(value);
}

// 1 when `condition` holds, 0 when it does not, -1 when it did not compile or
// run; a miss's left side goes to `left` when asked for.
int Judge(const std::string& actor, const std::string& condition, std::string* left) {
    State().SetGlobal(kVerdictGlobal, -1.0f);
    const std::string probe = "if ( " + condition + " )\nset " + kVerdictGlobal + " to 1\nelse\nset " +
                              kVerdictGlobal + " to 0\nendif\n";
    const int verdict = RunAs(actor, probe) ? static_cast<int>(State().Global(kVerdictGlobal)) : -1;
    if (left && verdict != 1) *left = LeftValue(actor, condition);
    return verdict;
}

// Commands reached since the last call that no earlier call had: each stub logs once.
std::vector<std::string> NewUnported() {
    std::vector<std::string> fresh;
    const std::vector<std::string> seen = UnportedCommandsSeen();
    for (const std::string& name : seen) {
        if (std::find(g_unportedBefore.begin(), g_unportedBefore.end(), name) == g_unportedBefore.end()) {
            fresh.push_back(name);
        }
    }
    g_unportedBefore = seen;
    return fresh;
}

void Start() { State().SetGlobal(kVerdictGlobal, 0.0f); }

bool Held() { return g_held; }

// The conversation's clicks, from the window.
void AddConversation(test::TestBackend& b) {
    b.menuOpening = g_talk.menuOpening;
    b.moveLanding = g_talk.playerMovePending;
    b.talking = g_talk.open;
    b.talkingWith = g_talk.with;
    b.topics = g_talk.topics;
    b.ask = g_talk.ask;
    b.choose = g_talk.choose;
    b.choices = g_talk.choices;
    b.bye = g_talk.bye;
    b.persuade = g_talk.persuade;
    b.lastText = g_talk.lastText;
}

// The steps' game calls, as the game hooks stand now (some bind after the first tick).
test::TestBackend Backend() {
    const GameHooks& h = Hooks();
    test::TestBackend b;
    b.envPrefix = "TES_MW_TEST_";
    b.start = Start;
    b.run = RunAs;
    b.judge = Judge;
    b.held = Held;
    b.menuOpen = h.menuOpen;
    b.activate = h.playerActivate;
    b.doorDistance = h.doorDistance;
    b.followerNames = h.followerNames;
    b.whereIs = h.whereIs;
    b.approach = h.playerToSpeaker;
    b.take = h.playerTake;
    b.pressButton = h.pressButton;
    b.closeMenu = h.closeMenu;
    b.menuPick = h.menuPick;
    b.sleepFor = h.sleepFor;
    b.wait = h.playerWait;
    b.punch = h.playerPunch;
    b.walk = h.playerWalk;
    b.steer = h.playerSteer;
    b.walkDone = h.playerWalkDone;
    b.walkStop = h.playerWalkStop;
    b.walkStalled = h.playerWalkStalled;
    b.walkStuck = h.playerWalkStuck;
    b.position = h.position;
    b.distance = h.distance;
    b.placeHere = h.placeHere;
    b.inInterior = h.playerInInterior;
    b.cell = h.playerCell;
    b.setPace = h.setPaceTarget;
    b.killPlayerAttackers = h.killPlayerAttackers;
    AddConversation(b);
    b.newIssues = NewUnported;
    b.realCommands = RealCommands;
    return b;
}

}  // namespace

void SetTestConversation(const TestConversation& conversation) { g_talk = conversation; }

void TickTestRunner(bool held) {
    g_held = held;
    test::SetTestBackend(Backend());
    test::TickTestRunner();
}

}  // namespace tesruntime::mw
