// Headless gate for common/test_runner.cpp: the case grammar, and the
// handshake through which the harness runs console lines and judges
// conditions. A fake game and a fake harness stand in for both sides.

#include <windows.h>

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "test_runner.h"

using namespace tesruntime::test;

namespace {

int g_failures = 0;

void Check(bool ok, const char* what) {
    std::printf("%s  %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) ++g_failures;
}

std::vector<std::string> ReadLines(const std::string& path) {
    std::ifstream in(path);
    std::vector<std::string> lines;
    for (std::string line; std::getline(in, line);) lines.push_back(line);
    return lines;
}

void Write(const std::string& path, const std::string& text) {
    std::ofstream out(path, std::ios::trunc);
    out << text;
}

bool InWorld() { return true; }

std::vector<std::string> g_listed = {"Rumors", "The Emperor"};
std::string g_asked;

bool Talking() { return true; }
std::vector<std::string> Topics() { return g_listed; }
bool Ask(const std::string& topic) {
    g_asked = topic;
    return topic == "The Emperor";
}

void ParsesTheGrammar() {
    std::istringstream in(
        "# a comment\n"
        "case escape\n"
        "run\n"
        "player.moveto CellDoorRef\n"
        "coc ImperialPrison\n"
        "until 900 GetStage Tutorial >= 10\n"
        "ask \"The Emperor\"\n"
        "end\n");
    const std::vector<TestCase> cases = ParseTestCases(in);
    Check(cases.size() == 1 && cases[0].name == "escape", "one case, named");
    const std::vector<TestStep>& steps = cases[0].steps;
    Check(steps.size() == 5, "run, settle, until, ask, settle");
    Check(steps[0].kind == TestStep::Kind::kRun &&
              steps[0].text == "player.moveto CellDoorRef\ncoc ImperialPrison\n",
          "a run body takes every line up to the next step");
    Check(steps[1].kind == TestStep::Kind::kWait && steps[1].number == 2, "an action settles 2 ticks");
    Check(steps[2].kind == TestStep::Kind::kUntil && steps[2].number == 900 &&
              steps[2].text == "GetStage Tutorial >= 10",
          "until keeps its ticks and condition");
    Check(steps[3].kind == TestStep::Kind::kAsk && steps[3].text == "The Emperor",
          "ask drops the quotes");
}

// Ticks until the runner writes `count` result lines, or gives up.
std::vector<std::string> TickUntil(const std::string& results, std::size_t count) {
    for (int i = 0; i < 500 && ReadLines(results).size() < count; ++i) TickTestRunner();
    return ReadLines(results);
}

// Removes what an earlier run of this gate left beside `results`.
void Fresh(const std::string& results) {
    for (const char* suffix : {"", ".ack", ".answer"}) DeleteFileA((results + suffix).c_str());
}

void Acknowledge(const std::string& results, int count, const std::string& answer) {
    Write(results + ".answer", answer);
    Write(results + ".ack", std::to_string(count));
}

void RelaysToTheHarness(const std::string& dir) {
    const std::string cases = dir + "\\cases.tes4test";
    const std::string results = dir + "\\results.jsonl";
    Write(cases,
          "case relay\n"
          "run\n"
          "player.moveto CellDoorRef\n"
          "expect GetStage Tutorial == 10\n"
          "talk 0001A2B3\n"
          "ask The Emperor\n"
          "ask Nobody\n"
          "end\n");
    Fresh(results);
    SetEnvironmentVariableA("TES_TEST_CASES", cases.c_str());
    SetEnvironmentVariableA("TES_TEST_RESULTS", results.c_str());
    TestBackend game;
    game.inWorld = InWorld;
    game.activate = [](const std::string&) { return true; };
    game.talking = Talking;
    game.topics = Topics;
    game.ask = Ask;
    SetTestBackend(game);
    Check(TestCasesRequested(), "TES_TEST_CASES asks for the runner");

    std::vector<std::string> lines = TickUntil(results, 1);
    Check(lines.size() == 1 && lines[0] == "{\"run\":\"player.moveto CellDoorRef\\n\",\"actor\":\"player\"}",
          "a run with no backend runner goes to the harness");
    for (int i = 0; i < 20; ++i) TickTestRunner();
    Check(ReadLines(results).size() == 1, "the runner waits for the harness's ack");
    Acknowledge(results, 1, "1");
    lines = TickUntil(results, 2);
    Check(lines.size() == 2 && lines[1] == "{\"judge\":\"GetStage Tutorial == 10\",\"actor\":\"player\"}",
          "an expect with no backend judge goes to the harness");
    Acknowledge(results, 2, "0 5.00");
    lines = TickUntil(results, 3);
    Check(g_asked == "Nobody", "the clicks ran after the answer");
    Check(lines.size() == 3 && lines[2].find("\"ok\":false") != std::string::npos &&
              lines[2].find("{\"expect\":\"GetStage Tutorial == 10\",\"result\":0,\"left\":5.00}") !=
                  std::string::npos &&
              lines[2].find("topic 'Nobody' not listed; listed: Rumors; The Emperor") != std::string::npos,
          "the case line carries the harness's verdict, its left side and the listed topics");
    lines = TickUntil(results, 4);
    Check(lines.size() == 4 && lines[3] == "{\"done\":true,\"passed\":0,\"failed\":1,\"skipped\":0}",
          "the summary closes the run");
}

std::vector<SpokenLine> g_toSpeak;
std::string g_walkedTo;
int g_walkTicks = 0;

std::vector<SpokenLine> Spoken() {
    std::vector<SpokenLine> lines;
    lines.swap(g_toSpeak);
    return lines;
}

// `hear` waits for the speaker's line, each line goes to the results the tick
// it is spoken, and `walk` hands its target over and waits for arrival.
void HearsAndWalks(const std::string& dir) {
    const std::string cases = dir + "\\hear.tes4test";
    const std::string results = dir + "\\hear.jsonl";
    Write(cases,
          "case speech\n"
          "walk 0001A2B3\n"
          "hear 300 0000BEEF Stop right there\n"
          "hear 5 0000BEEF anything else\n"
          "end\n");
    Fresh(results);
    SetEnvironmentVariableA("TES_TEST_CASES", cases.c_str());
    SetEnvironmentVariableA("TES_TEST_RESULTS", results.c_str());
    TestBackend game;
    game.inWorld = InWorld;
    game.spoken = Spoken;
    game.walk = [](const std::string& where) { g_walkedTo = where; };
    game.walkDone = []() { return ++g_walkTicks >= 10; };
    SetTestBackend(game);
    ResetTestRunner();
    for (int i = 0; i < 80; ++i) TickTestRunner();
    Check(g_walkedTo == "0001A2B3", "walk hands its target to the game");
    g_toSpeak = {{"0000BEEF", "Stop right there, criminal scum!"}};
    std::vector<std::string> lines = TickUntil(results, 3);
    Check(!lines.empty() &&
              lines[0].find("{\"speaker\":\"0000BEEF\",\"text\":\"Stop right there, criminal scum!\"") == 0,
          "a spoken line goes to the results the tick it is heard");
    Check(lines.size() == 3 && lines[1].find("hear: 0000beef never said 'anything else'; heard: "
                                             "0000BEEF: Stop right there, criminal scum!") !=
                                   std::string::npos,
          "a later hear does not take a line an earlier one took, and names what was heard");
    Check(lines.size() == 3 && lines[1].find("\"said\":[{\"speaker\":\"0000BEEF\"") != std::string::npos,
          "the case line lists what was said during it");
}

// A line begun while one case still ran -- a force-greet its last stage
// started -- is there for the next case's `hear`.
void HearsAcrossCases(const std::string& dir) {
    const std::string cases = dir + "\\hear_across.tes4test";
    const std::string results = dir + "\\hear_across.jsonl";
    Write(cases,
          "case greet_starts\n"
          "wait 20\n"
          "end\n"
          "case greeted\n"
          "hear 5 0000BEEF Halt\n"
          "end\n");
    Fresh(results);
    SetEnvironmentVariableA("TES_TEST_CASES", cases.c_str());
    SetEnvironmentVariableA("TES_TEST_RESULTS", results.c_str());
    TestBackend game;
    game.inWorld = InWorld;
    game.spoken = Spoken;
    SetTestBackend(game);
    ResetTestRunner();
    g_toSpeak = {{"0000BEEF", "Halt! Who goes there?"}};
    const std::vector<std::string> lines = TickUntil(results, 4);
    Check(lines.size() == 4 && lines[0].find("\"during\":\"greet_starts\"") != std::string::npos &&
              lines[3] == "{\"done\":true,\"passed\":2,\"failed\":0,\"skipped\":0}",
          "a hear takes a line begun during the case before it");
}

}  // namespace

int main() {
    char temp[MAX_PATH];
    GetTempPathA(MAX_PATH, temp);
    const std::string dir = std::string(temp) + "tes_test_runner_test";
    CreateDirectoryA(dir.c_str(), nullptr);
    ParsesTheGrammar();
    RelaysToTheHarness(dir);
    HearsAndWalks(dir);
    HearsAcrossCases(dir);
    std::printf("%s\n", g_failures ? "FAILED" : "all passed");
    return g_failures ? 1 : 0;
}
