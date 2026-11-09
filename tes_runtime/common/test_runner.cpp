// Runs test cases a step per tick; see test_runner.h.

#include "test_runner.h"

#define NOMINMAX
#include <windows.h>

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <set>
#include <sstream>

#include "log.h"

namespace tesruntime::test {

namespace {

using Kind = TestStep::Kind;

// How long `talk` waits for the conversation to open, and the settle an
// action gets when no `wait` follows it.
constexpr int kTalkTicks = 90;
constexpr int kSettleTicks = 2;
// How long a menu other than the conversation may hold a step that needs the
// world before the case fails naming it, rather than the run hanging.
constexpr int kHeldTicks = 300;
// How long a `walk` may take: room for crossing a region on foot.
constexpr int kWalkTicks = 9000;
// The world settles this long after a load before the first case, as no
// player acts in the first instant: a load door activated sooner is ignored.
constexpr int kStartTicks = 60;
// A harness request whose answer has not come back yet.
constexpr int kPending = -2;

enum class Mode { kUnknown, kOff, kActive, kFinished };

TestBackend g_game;
Mode g_mode = Mode::kUnknown;
std::vector<TestCase> g_cases;
std::size_t g_next = 0;
int g_startTicks = kStartTicks;
long long g_tick = 0;
bool g_stopOnFail = false;
// The harness writes to `<results>.ack` how many result lines it has handled:
// a checkpoint saved after a passing case, a console line run, a judge
// answered. The runner waits for its own count after any of them.
bool g_checkpoints = false;
std::string g_resultsPath;
int g_written = 0;
int g_awaitedAck = 0;
int g_passed = 0;
int g_failed = 0;
std::ofstream g_out;

// The case in progress.
struct Progress {
    bool started = false;
    std::size_t step = 0;
    int ticksLeft = -1;        // a wait, talk or walk under way; -1 when none is
    long long deadline = -1;   // the tick an `until` gives up at; -1 when none is
    bool asked = false;        // a harness request for this step is out
    int heldTicks = 0;         // ticks a menu has held a step that needs the world
    int loadTicks = 0;         // ticks a talk or activate has waited for its target's 3D
    bool expectsHeld = true;
    std::string error;
    std::string expects;
    std::string said;
    std::size_t heardStart = 0;      // the first line begun since the case started
};

Progress g_case;

// Every line begun this run, and the first one no `hear` has taken yet: a
// line begun while an earlier case still ran (a force-greet its last stage
// started) is still there for the next case's `hear`.
std::vector<SpokenLine> g_heard;
std::size_t g_heardFrom = 0;

bool Talking() { return g_game.talking && g_game.talking(); }

std::string Env(const char* name) {
    char buf[1024];
    const DWORD n = GetEnvironmentVariableA(name, buf, sizeof(buf));
    return n && n < sizeof(buf) ? std::string(buf, n) : std::string();
}

std::string Trim(const std::string& text) {
    const auto first = text.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return "";
    return text.substr(first, text.find_last_not_of(" \t\r\n") - first + 1);
}

std::string Lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return text;
}

std::string Json(const std::string& text) {
    std::string out = "\"";
    for (const char c : text) {
        if (c == '"' || c == '\\') out += '\\';
        if (c == '\n') {
            out += "\\n";
        } else if (static_cast<unsigned char>(c) < 0x20) {
            char escaped[8];
            std::snprintf(escaped, sizeof(escaped), "\\u%04x", static_cast<unsigned char>(c));
            out += c == '\r' ? "" : escaped;
        } else {
            out += c;
        }
    }
    return out + "\"";
}

std::string JsonList(const std::vector<std::string>& items) {
    std::string list;
    for (const std::string& item : items) list += (list.empty() ? "" : ",") + Json(item);
    return "[" + list + "]";
}

std::string Joined(const std::vector<std::string>& items) {
    std::string text;
    for (const std::string& item : items) text += (text.empty() ? "" : "; ") + item;
    return text;
}

void WriteLine(const std::string& line) {
    g_out << line << "\n";
    g_out.flush();
    ++g_written;
}

// `<verdict> <left>` from the harness's answer file; -1 and null when unreadable.
int ReadAnswer(std::string* left) {
    std::ifstream in(g_resultsPath + ".answer");
    int verdict = -1;
    std::string rest;
    if (!(in >> verdict)) verdict = -1;
    std::getline(in, rest);
    *left = Trim(rest).empty() ? "null" : Trim(rest);
    return verdict;
}

// Hands `request` to the harness the first time, then returns its answer the
// next time the step runs (the tick waits for the ack in between).
int AskHarness(const std::string& request, std::string* left) {
    if (!g_case.asked) {
        WriteLine(request);
        g_awaitedAck = g_written;
        g_case.asked = true;
        return kPending;
    }
    g_case.asked = false;
    return ReadAnswer(left);
}

// 1 when `condition` holds, 0 when it does not, -1 when it did not evaluate,
// kPending while the harness evaluates it.
int Judge(const std::string& actor, const std::string& condition, std::string* left) {
    if (g_game.judge) return g_game.judge(actor, condition, left);
    return AskHarness("{\"judge\":" + Json(condition) + ",\"actor\":" + Json(actor) + "}", left);
}

void Record(const std::string& condition, int verdict, const std::string& left) {
    g_case.expectsHeld = g_case.expectsHeld && verdict == 1;
    const std::string actual = verdict == 1 ? "" : ",\"left\":" + left;
    g_case.expects += std::string(g_case.expects.empty() ? "" : ",") + "{\"expect\":" +
                      Json(condition) + ",\"result\":" + std::to_string(verdict) + actual + "}";
}

// True once the expect is judged and recorded.
bool Expect(const std::string& actor, const std::string& condition) {
    std::string left = "null";
    const int verdict = Judge(actor, condition, &left);
    if (verdict == kPending) return false;
    Record(condition, verdict, left);
    return true;
}

// True once the body ran (or failed, which the case records).
bool Ran(const std::string& actor, const std::string& source) {
    if (g_game.run) {
        if (!g_game.run(actor, source)) g_case.error = "the script did not compile or run";
        return true;
    }
    std::string detail;
    const int ok = AskHarness("{\"run\":" + Json(source) + ",\"actor\":" + Json(actor) + "}", &detail);
    if (ok == kPending) return false;
    if (ok != 1) g_case.error = "the script did not run: " + detail;
    return true;
}

void Said() {
    if (g_game.lastText) g_case.said += (g_case.said.empty() ? "" : ",") + Json(g_game.lastText());
}

// Counts down a wait; true on the tick it ends.
bool Waited(int ticks) {
    if (g_case.ticksLeft < 0) g_case.ticksLeft = ticks;
    if (g_case.ticksLeft-- > 0) return false;
    g_case.ticksLeft = -1;
    return true;
}

// The player activating `id`, once it is in the world: no player clicks what
// has not loaded. True when done or failed.
bool Activated(const std::string& id) {
    if (!g_game.activate) {
        g_case.error = "no activate hook";
        return true;
    }
    if (g_game.activate(id)) {
        g_case.loadTicks = 0;
        return true;
    }
    if (++g_case.loadTicks <= kHeldTicks) return false;
    g_case.error = "'" + id + "' never loaded";
    return true;
}

// Activates `npc` as the player does, then waits for the conversation; true when done.
bool Talked(const std::string& npc) {
    if (g_case.ticksLeft < 0) {
        if (!Activated(npc)) return false;
        if (!g_case.error.empty()) return true;
        g_case.ticksLeft = kTalkTicks;
        return false;
    }
    if (Talking()) {
        g_case.ticksLeft = -1;
        Said();
        return true;
    }
    if (--g_case.ticksLeft > 0) return false;
    g_case.ticksLeft = -1;
    g_case.error = "talk " + npc + ": no conversation opened";
    return true;
}

// Waits for `step`'s condition to hold, up to its tick budget; true when done.
// A miss records the condition, with its left side, as a failed expect.
bool Until(const TestCase& test, const TestStep& step) {
    if (g_case.deadline < 0) g_case.deadline = g_tick + step.number;
    std::string left = "null";
    const int verdict = Judge(test.actor, step.text, &left);
    if (verdict == kPending) return false;
    if (verdict != 1 && g_tick < g_case.deadline) return false;
    g_case.deadline = -1;
    Record(step.text, verdict, left);
    if (verdict != 1) g_case.error = "until: not true within " + std::to_string(step.number) + " ticks";
    return true;
}

// Waits for `done` to come true, up to kHeldTicks; true once it did or the
// case failed with `failure`.
bool Retried(bool done, const std::string& failure) {
    if (done) {
        g_case.ticksLeft = -1;
        return true;
    }
    if (g_case.ticksLeft < 0) g_case.ticksLeft = kHeldTicks;
    if (--g_case.ticksLeft > 0) return false;
    g_case.ticksLeft = -1;
    g_case.error = failure;
    return true;
}

// Whether the engine's menu `name` is open; true when the game cannot say.
bool MenuUp(const std::string& name) { return !g_game.menuOpen || g_game.menuOpen(name); }

// The steps that answer a menu other than the conversation, each waiting for
// its menu to come up first: a MessageBox button, a pick, a sleep, a close.
bool Answered(const TestStep& step) {
    if (step.kind == Kind::kButton) {
        if (!g_game.pressButton) return Retried(false, "no button hook");
        const bool up = MenuUp("MessageBoxMenu");
        if (up) g_game.pressButton(step.number);
        return Retried(up, "no MessageBox opened");
    }
    if (step.kind == Kind::kPick) {
        return Retried(g_game.menuPick && g_game.menuPick(step.text),
                       "no open menu takes '" + step.text + "'");
    }
    if (step.kind == Kind::kSleep) {
        const bool up = g_game.sleepFor && MenuUp("Sleep/Wait Menu");
        if (up) g_game.sleepFor(step.number);
        return Retried(up, "the Sleep/Wait menu never opened");
    }
    const bool up = g_game.closeMenu && MenuUp(step.text);
    if (up) g_game.closeMenu(step.text);
    return Retried(up, "menu '" + step.text + "' never opened");
}

void Click(const TestStep& step) {
    const auto listed = [](std::vector<std::string> (*list)()) {
        return list ? Joined(list()) : std::string("(not readable)");
    };
    if (!Talking()) {
        g_case.error = "no conversation is open";
    } else if (step.kind == Kind::kAsk && !(g_game.ask && g_game.ask(step.text))) {
        g_case.error = "topic '" + step.text + "' not listed; listed: " + listed(g_game.topics);
    } else if (step.kind == Kind::kChoose && !(g_game.choose && g_game.choose(step.number))) {
        g_case.error = "no choice " + std::to_string(step.number) + "; pending: " +
                       listed(g_game.choices);
    } else if (step.kind == Kind::kBye && !(g_game.bye && g_game.bye())) {
        g_case.error = "Goodbye is locked by a pending choice";
    } else if (step.kind == Kind::kPersuade && !(g_game.persuade && g_game.persuade(step.text))) {
        g_case.error = "cannot persuade with '" + step.text + "' (unknown, locked or unpaid)";
    } else {
        Said();
    }
}

// The lines begun since the last tick: each goes to the results now, so the
// run shows when it was spoken, and joins the case for `hear` and its line.
void Listen(const TestCase& test) {
    if (!g_game.spoken) return;
    for (const SpokenLine& line : g_game.spoken()) {
        const std::string spoke = "{\"speaker\":" + Json(line.speaker) + ",\"text\":" + Json(line.text);
        WriteLine(spoke + ",\"tick\":" + std::to_string(g_tick) + ",\"during\":" + Json(test.name) + "}");
        g_case.said += (g_case.said.empty() ? "" : ",") + spoke + "}";
        g_heard.push_back(line);
    }
}

// `hear <ticks> <speaker | any> [text]`: waits for the speaker to begin a line
// containing the text; true when heard or timed out.
bool Heard(const TestStep& step) {
    const std::string speaker = Lower(step.text.substr(0, step.text.find(' ')));
    const std::size_t space = step.text.find(' ');
    const std::string want = Lower(space == std::string::npos ? "" : Trim(step.text.substr(space)));
    if (g_case.deadline < 0) g_case.deadline = g_tick + step.number;
    for (std::size_t i = g_heardFrom; i < g_heard.size(); ++i) {
        const SpokenLine& line = g_heard[i];
        if ((speaker == "any" || Lower(line.speaker) == speaker) &&
            Lower(line.text).find(want) != std::string::npos) {
            g_heardFrom = i + 1;
            g_case.deadline = -1;
            return true;
        }
    }
    if (g_tick < g_case.deadline) return false;
    g_case.deadline = -1;
    std::vector<std::string> lines;
    for (std::size_t i = g_case.heardStart; i < g_heard.size(); ++i) {
        lines.push_back(g_heard[i].speaker + ": " + g_heard[i].text);
    }
    g_case.error = "hear: " + speaker + " never said '" + want + "'; heard: " +
                   (lines.empty() ? std::string("nothing") : Joined(lines));
    return true;
}

// `walk <ref | x y z>`: the player walks there, as a player would, so what
// follows or watches the player sees a walk and not a jump.
bool Walked(const TestStep& step) {
    if (!g_game.walk || !g_game.walkDone || step.text.empty()) {
        g_case.error = "walk needs a reference or x y z, and the walk hooks";
        return true;
    }
    if (g_case.ticksLeft < 0) {
        g_game.walk(step.text);
        g_case.ticksLeft = kWalkTicks;
        return false;
    }
    if (g_game.walkDone()) {
        g_case.ticksLeft = -1;
        return true;
    }
    if (--g_case.ticksLeft > 0) return false;
    g_case.ticksLeft = -1;
    if (g_game.walkStop) g_game.walkStop();
    g_case.error = "walk: the player never reached " + step.text;
    return true;
}

// `console <command>`: the harness runs a Skyrim console command -- one that
// does a player's action faster -- and the case goes on once it has.
void Console(const std::string& command) {
    WriteLine("{\"console\":" + Json(command) + "}");
    g_awaitedAck = g_written;
}

// Does `step`; true once it is finished.
bool Advance(const TestCase& test, const TestStep& step) {
    switch (step.kind) {
        case Kind::kWait:
            return Waited(step.number);
        case Kind::kTalk:
            return Talked(step.text);
        case Kind::kExpect:
            return Expect(test.actor, step.text);
        case Kind::kRun:
            return Ran(test.actor, step.text);
        case Kind::kUntil:
            return Until(test, step);
        case Kind::kActivate:
            return Activated(step.text);
        case Kind::kTake:
            if (!g_game.take) g_case.error = "no take hook";
            if (g_game.take) g_game.take(step.text);
            return true;
        case Kind::kConsole:
            Console(step.text);
            return true;
        case Kind::kWalk:
            return Walked(step);
        case Kind::kHear:
            return Heard(step);
        case Kind::kButton:
        case Kind::kPick:
        case Kind::kClose:
        case Kind::kSleep:
            return Answered(step);
        default:
            Click(step);
            return true;
    }
}

bool IsAction(Kind kind) {
    return kind != Kind::kWait && kind != Kind::kExpect && kind != Kind::kUntil && kind != Kind::kHear;
}

void Report(const TestCase& test) {
    const bool ok = g_case.error.empty() && g_case.expectsHeld;
    ok ? ++g_passed : ++g_failed;
    std::string line = "{\"case\":" + Json(test.name) + ",\"ok\":" + (ok ? "true" : "false");
    if (!g_case.error.empty()) line += ",\"error\":" + Json(g_case.error);
    line += ",\"expects\":[" + g_case.expects + "],\"said\":[" + g_case.said + "]";
    if (g_game.newIssues) line += ",\"unported\":" + JsonList(g_game.newIssues());
    WriteLine(line + "}");
    Log("test: %s  %s%s%s", ok ? "PASS" : "FAIL", test.name.c_str(),
        g_case.error.empty() ? "" : " -- ", g_case.error.c_str());
}

// Reads the case file once; whether the runner has work to do.
bool Active() {
    if (g_mode == Mode::kUnknown) {
        const std::string casesPath = Env("TES_TEST_CASES");
        g_mode = Mode::kOff;
        if (casesPath.empty()) return false;
        std::ifstream in(casesPath);
        g_cases = ParseTestCases(in);
        g_resultsPath = Env("TES_TEST_RESULTS");
        g_out.open(g_resultsPath, std::ios::out | std::ios::trunc);
        if (!in.is_open() || !g_out) {
            Log("test: cannot read %s or write the results file", casesPath.c_str());
            return false;
        }
        g_stopOnFail = !Env("TES_TEST_STOP_ON_FAIL").empty();
        g_checkpoints = !Env("TES_TEST_CHECKPOINTS").empty();
        Log("test: %zu case(s) from %s", g_cases.size(), casesPath.c_str());
        g_mode = Mode::kActive;
    }
    return g_mode == Mode::kActive;
}

void Finish() {
    std::string line = "{\"done\":true,\"passed\":" + std::to_string(g_passed) +
                       ",\"failed\":" + std::to_string(g_failed) +
                       ",\"skipped\":" + std::to_string(g_cases.size() - g_next);
    if (g_game.realCommands) {
        line += ",\"uncovered\":" + JsonList(UncoveredCommands(g_cases, g_game.realCommands()));
    }
    WriteLine(line + "}");
    g_out.close();
    Log("test: done, %d passed, %d failed", g_passed, g_failed);
    g_mode = Mode::kFinished;
}

// Whether the harness has handled every line the runner waits on.
bool Acknowledged() {
    std::ifstream in(g_resultsPath + ".ack");
    int done = 0;
    return in >> done && done >= g_awaitedAck;
}

// Steps the case on: after an action it yields the tick, so the game sees it.
void RunSteps(const TestCase& test, bool held) {
    while (g_case.step < test.steps.size() && g_case.error.empty()) {
        const TestStep& step = test.steps[g_case.step];
        const bool needsWorld = step.kind == Kind::kRun || step.kind == Kind::kActivate ||
                                step.kind == Kind::kUntil || step.kind == Kind::kWalk ||
                                (step.kind == Kind::kTalk && g_case.ticksLeft < 0);
        std::string left = "null";
        const bool heldTrue = held && !Talking() && step.kind == Kind::kUntil &&
                              Judge(test.actor, step.text, &left) == 1;
        if (held && !Talking() && needsWorld && !heldTrue) {
            if (++g_case.heldTicks > kHeldTicks) g_case.error = "a menu holds the game";
            return;
        }
        g_case.heldTicks = 0;
        if (heldTrue) {
            g_case.deadline = -1;
            Record(step.text, 1, left);
        } else if (!Advance(test, step)) {
            return;
        }
        ++g_case.step;
        if (IsAction(step.kind) || g_awaitedAck) return;
    }
}

}  // namespace

void SetTestBackend(const TestBackend& backend) { g_game = backend; }

void ResetTestRunner() {
    if (g_out.is_open()) g_out.close();
    g_mode = Mode::kUnknown;
    g_cases.clear();
    g_next = 0;
    g_startTicks = kStartTicks;
    g_tick = 0;
    g_written = g_awaitedAck = g_passed = g_failed = 0;
    g_case = Progress{};
    g_heard.clear();
    g_heardFrom = 0;
}

bool TestCasesRequested() { return !Env("TES_TEST_CASES").empty(); }

std::vector<TestCase> ParseTestCases(std::istream& in) {
    static const std::vector<std::string> kKinds = {
        "run",  "wait",  "expect", "talk",    "ask",     "choose",   "bye",   "button", "activate",
        "until", "pick", "close",  "take",    "console", "persuade", "sleep", "walk",   "hear"};
    std::vector<TestCase> cases;
    TestStep* body = nullptr;
    std::string line;
    while (std::getline(in, line)) {
        const std::string text = Trim(line);
        const std::string word = Lower(text.substr(0, text.find_first_of(" \t")));
        const std::string rest = Trim(text.substr(word.size()));
        if (text.empty() || text[0] == '#') continue;
        const auto at = std::find(kKinds.begin(), kKinds.end(), word);
        const bool directive = at != kKinds.end() || word == "case" || word == "end";
        if (body && !directive) {
            body->text += text + "\n";
            continue;
        }
        body = nullptr;
        if (word == "case") cases.push_back(TestCase{rest});
        if (cases.empty() || word == "case" || word == "end") continue;
        TestCase& test = cases.back();
        if (word == "actor") {
            test.actor = rest;
            continue;
        }
        if (at == kKinds.end()) {
            // A script line after a step opens its own body rather than vanishing.
            test.steps.push_back(TestStep{Kind::kRun, text + "\n", 0});
            body = &test.steps.back();
            continue;
        }
        TestStep step{static_cast<Kind>(at - kKinds.begin()), rest,
                      std::max(0, std::atoi(rest.c_str()))};
        if (word == "until" || word == "hear") {
            step.text = Trim(rest.substr(std::min(rest.size(), rest.find(' '))));
        }
        const bool quoted = rest.size() > 1 && rest.front() == '"' && rest.back() == '"';
        if (quoted && word != "run" && word != "expect") step.text = rest.substr(1, rest.size() - 2);
        test.steps.push_back(step);
        if (word == "run") body = &test.steps.back();
    }
    for (TestCase& test : cases) {
        for (std::size_t i = test.steps.size(); i-- > 0;) {
            const bool waits = i + 1 < test.steps.size() && test.steps[i + 1].kind == Kind::kWait;
            if (IsAction(test.steps[i].kind) && !waits) {
                test.steps.insert(test.steps.begin() + i + 1, TestStep{Kind::kWait, "", kSettleTicks});
            }
        }
    }
    return cases;
}

std::vector<std::string> UncoveredCommands(const std::vector<TestCase>& cases,
                                           const std::vector<std::string>& real) {
    std::set<std::string> named;
    for (const TestCase& test : cases) {
        std::string text;
        for (const TestStep& step : test.steps) {
            if (step.kind == Kind::kRun || step.kind == Kind::kExpect || step.kind == Kind::kUntil) {
                text += step.text + "\n";
            }
        }
        std::string word;
        for (const char c : Lower(text) + "\n") {
            if (std::isalnum(static_cast<unsigned char>(c)) || c == '_') {
                word += c;
            } else if (!word.empty()) {
                named.insert(word);
                word.clear();
            }
        }
    }
    std::vector<std::string> uncovered;
    for (const std::string& name : real) {
        if (!named.count(name)) uncovered.push_back(name);
    }
    return uncovered;
}

void TickTestRunner() {
    if (!Active() || !g_game.inWorld || !g_game.inWorld() || g_startTicks-- > 0) return;
    ++g_tick;
    if (g_next >= g_cases.size()) {
        Finish();
        return;
    }
    const TestCase& test = g_cases[g_next];
    if (!g_case.started) {
        g_case = Progress{};
        g_case.started = true;
        g_case.heardStart = g_heard.size();
        if (g_game.newIssues) g_game.newIssues();
    }
    Listen(test);
    if (g_awaitedAck && !Acknowledged()) return;
    g_awaitedAck = 0;
    RunSteps(test, g_game.held && g_game.held());
    if (g_case.step < test.steps.size() && g_case.error.empty()) return;
    Report(test);
    ++g_next;
    const bool failed = !g_case.error.empty() || !g_case.expectsHeld;
    g_case = Progress{};
    if (failed && g_stopOnFail) Finish();
    if (!failed && g_checkpoints) g_awaitedAck = g_written;
}

}  // namespace tesruntime::test
