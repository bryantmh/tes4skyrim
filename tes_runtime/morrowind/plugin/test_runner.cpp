#include "test_runner.h"

#include <windows.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <deque>
#include <fstream>
#include <limits>
#include <set>
#include <string>
#include <vector>

#include "dialogue_state.h"
#include "game_actor.h"
#include "ids.h"
#include "log.h"
#include "menu.h"
#include "script_context.h"
#include "script_runner.h"

namespace tesruntime::mw {

namespace {

using Kind = TestStep::Kind;

// The global an `expect` writes its verdict into: 1 holds, 0 does not, and
// the -1 it is primed with survives only a probe that did not run.
constexpr const char* kVerdictGlobal = "TestResult";

// How long `talk` waits for the conversation to open, and the settle an
// action gets when no `wait` follows it.
constexpr int kTalkTicks = 90;
constexpr int kSettleTicks = 2;
// The beat a player takes between closing a conversation and moving off: a
// move two frames after `bye` drags the speaker along through the doors.
// See: docs/audits/headless_quest_runs.md#raven-rock-open
constexpr int kByeSettleTicks = 30;
// How long `rest` lets a sleep's aftermath (a wake-up, an attacker) land, or a
// bed's menu open, before it activates the bed again.
constexpr int kRestSettleTicks = 30;
// `persuade <kind> until <condition>`: the word that splits it, and how many
// attempts the player makes before the case fails.
constexpr const char* kUntilWord = " until ";
constexpr int kPersuadeTries = 30;
// How long a menu other than the conversation may hold a step that needs the
// world before the case fails naming it, rather than the run hanging.
constexpr int kHeldTicks = 300;
// The Sleep/Wait menu may hold that many times longer: a 24-hour sleep at 1x.
constexpr int kSleepHeldFactor = 20;
// How long a `walk` may take: room for crossing a region on foot.
constexpr int kWalkTicks = 9000;
// The world settles this long after a load before the first case, as no
// player acts in the first instant: a load door activated sooner is ignored.
constexpr int kStartTicks = 60;

enum class Mode { kUnknown, kOff, kActive, kFinished };

Mode g_mode = Mode::kUnknown;
std::vector<TestCase> g_cases;
std::size_t g_next = 0;
int g_startTicks = kStartTicks;
bool g_stopOnFail = false;
// The harness writes to `<results>.ack` how many result lines it has handled:
// a checkpoint saved after a passing case, a console command run. The runner
// waits for its own count after either.
bool g_checkpoints = false;
std::string g_ackPath;
int g_written = 0;
int g_awaitedAck = 0;
int g_passed = 0;
int g_failed = 0;
std::ofstream g_out;

// `follow`: how far the NPC may get ahead before the player is moved up, how
// far apart the trail's points are, how many it keeps, and how far behind the
// NPC the player is put -- on a spot the NPC itself stood on, so on the ground.
constexpr float kFollowSlack = 150.0f;
constexpr float kTrailStep = 40.0f;
// Ticks (one second) a dialogue step waits for a posted conversation to open.
constexpr int kTalkWaitTicks = 30;
constexpr std::size_t kTrailPoints = 24;
constexpr float kFollowBehind = 120.0f;

struct Spot {
    float x = 0.0f, y = 0.0f, z = 0.0f;
};

// The case in progress.
struct Progress {
    bool started = false;
    std::size_t step = 0;
    int ticksLeft = -1;   // a wait, talk or until under way; -1 when none is
    int heldTicks = 0;    // ticks a menu has held a step that needs the world
    int loadTicks = 0;    // ticks a talk or activate has waited for its target's 3D
    bool expectsHeld = true;
    std::string error;
    std::string expects;
    std::string said;
    std::vector<std::string> unportedBefore;
    std::string follow;        // the NPC `follow` keeps the player behind, or empty
    std::deque<Spot> trail;    // where that NPC has stood, oldest first
    int talkWait = 0;          // ticks a dialogue step has waited for the menu to open
    bool defend = false;       // the player kills whatever attacks it
    int punches = 0;           // punches the current `punch` step has thrown
    int rests = 0;             // hours the current `rest` step has slept
};

// Ticks between a `punch` step's swings: room for one unarmed attack to land.
constexpr int kPunchTicks = 45;

Progress g_case;
TestConversation g_talk;

bool Talking() { return g_talk.open && g_talk.open(); }
bool MenuStillOpening() { return g_talk.menuOpening && g_talk.menuOpening(); }
bool MoveLanding() { return g_talk.playerMovePending && g_talk.playerMovePending(); }

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
            continue;
        }
        if (c != '\r') out += c;
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

// Runs `source` with `actor` as the speaker; false when it did not compile or threw.
bool RunAs(const std::string& actor, const std::string& source) {
    GameActor view(actor);
    DialogueContext context(view, actor, "Player");
    return RunResultScript(source, context);
}

// 1 when `condition` holds, 0 when it does not, -1 when it did not compile or run.
int Judge(const std::string& actor, const std::string& condition) {
    State().SetGlobal(kVerdictGlobal, -1.0f);
    const std::string probe = "if ( " + condition + " )\nset " + kVerdictGlobal +
                              " to 1\nelse\nset " + kVerdictGlobal + " to 0\nendif\n";
    if (!RunAs(actor, probe)) return -1;
    return static_cast<int>(State().Global(kVerdictGlobal));
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
    const std::string probe =
        std::string("set ") + kVerdictGlobal + " to " + condition.substr(0, op) + "\n";
    const float value = RunAs(actor, probe) ? State().Global(kVerdictGlobal) : kUnset;
    return value == kUnset ? "null" : std::to_string(value);
}

// Commands the case reached that no earlier case had: each stub logs once.
std::string NewUnported() {
    std::vector<std::string> fresh;
    for (const std::string& name : UnportedCommandsSeen()) {
        const auto& before = g_case.unportedBefore;
        if (std::find(before.begin(), before.end(), name) == before.end()) {
            fresh.push_back(name);
        }
    }
    return JsonList(fresh);
}

void Expect(const std::string& actor, const std::string& condition) {
    const int verdict = Judge(actor, condition);
    g_case.expectsHeld = g_case.expectsHeld && verdict == 1;
    const std::string actual =
        verdict == 1 ? "" : ",\"left\":" + LeftValue(actor, condition);
    g_case.expects += std::string(g_case.expects.empty() ? "" : ",") + "{\"expect\":" +
                      Json(condition) + ",\"result\":" + std::to_string(verdict) + actual +
                      "}";
}

void Said() {
    g_case.said += (g_case.said.empty() ? "" : ",") + Json(g_talk.lastText());
}

// Counts down a wait; true on the tick it ends.
bool Waited(int ticks) {
    if (g_case.ticksLeft < 0) g_case.ticksLeft = ticks;
    if (g_case.ticksLeft-- > 0) return false;
    g_case.ticksLeft = -1;
    return true;
}

// The player activating `id`, once it is in the world: no player clicks what
// has not loaded, and an activation posted while its cell still loads can
// deadlock on the reference's extra data. True when done or failed.
bool Activated(const std::string& id) {
    if (!Hooks().playerActivate) {
        g_case.error = "no activate hook";
        return true;
    }
    // A load door activated while the player's own move is landing goes nowhere.
    if (MoveLanding()) return false;
    if (Hooks().playerActivate(id)) {
        g_case.loadTicks = 0;
        return true;
    }
    if (++g_case.loadTicks <= kHeldTicks) return false;
    g_case.error = "'" + id + "' never loaded";
    return true;
}

// Activates `npc` as the player does, then waits for the window; true when done.
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
// A miss records the condition, with its left side, as a failed expect. A
// player move still landing would let it judge the old position.
bool Until(const TestCase& test, const TestStep& step) {
    if (MoveLanding()) return false;
    if (g_case.ticksLeft < 0) g_case.ticksLeft = step.number;
    const bool held = Judge(test.actor, step.text) == 1;
    if (!held && --g_case.ticksLeft > 0) return false;
    g_case.ticksLeft = -1;
    Expect(test.actor, step.text);
    if (!held) g_case.error = "until: not true within " + std::to_string(step.number) + " ticks";
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
bool MenuUp(const std::string& name) { return !Hooks().menuOpen || Hooks().menuOpen(name); }

// The steps that answer a menu other than the conversation, each waiting for
// its menu to come up first: a MessageBox button, a chargen pick, a sleep, a close.
bool Answered(const TestStep& step) {
    if (step.kind == Kind::kButton) {
        if (!Hooks().pressButton) return Retried(false, "no button hook");
        const bool up = MenuUp("MessageBoxMenu");
        if (up) Hooks().pressButton(step.number);
        return Retried(up, "no MessageBox opened");
    }
    if (step.kind == Kind::kPick) {
        return Retried(Hooks().menuPick && Hooks().menuPick(step.text),
                       "no open class, birthsign or level-up menu takes '" + step.text + "'");
    }
    if (step.kind == Kind::kSleep) {
        const bool up = Hooks().sleepFor && MenuUp(ids::kSleepWaitMenu);
        if (up) Hooks().sleepFor(step.number);
        return Retried(up, "the Sleep/Wait menu never opened");
    }
    const bool up = Hooks().closeMenu && MenuUp(step.text);
    if (up) Hooks().closeMenu(step.text);
    return Retried(up, "menu '" + step.text + "' never opened");
}

// Whether the game reports menu `name` open; false when it cannot say.
bool MenuReported(const char* name) { return Hooks().menuOpen && Hooks().menuOpen(name); }

// `rest <max> <bed> <condition>`: the player sleeps an hour in the bed, again
// and again, until the condition holds -- as a player sleeps until something
// happens. A message box between sleeps (a wake-up notice) is dismissed. A bed
// of `-` waits on the spot instead, as the Wait key does.
bool Rested(const TestCase& test, const TestStep& step) {
    const std::size_t gap = step.text.find(' ');
    const std::string bed = step.text.substr(0, gap);
    const std::string condition = Trim(step.text.substr(std::min(step.text.size(), gap)));
    if (MoveLanding() || Talking()) return false;
    const bool held = Judge(test.actor, condition) == 1;
    if (held || g_case.rests >= step.number) {
        Expect(test.actor, condition);
        if (!held) g_case.error = "rest: not true after " + std::to_string(step.number) + " sleeps";
        g_case.rests = 0;
        g_case.ticksLeft = -1;
        return true;
    }
    if (MenuReported("MessageBoxMenu") && Hooks().pressButton) {
        Hooks().pressButton(0);
        return false;
    }
    if (--g_case.ticksLeft > 0) return false;
    g_case.ticksLeft = kRestSettleTicks;
    if (MenuReported(ids::kSleepWaitMenu) && Hooks().sleepFor) {
        Hooks().sleepFor(1);
        ++g_case.rests;
    } else if (bed == "-") {
        if (Hooks().playerWait) Hooks().playerWait();
    } else if (Hooks().playerActivate) {
        Hooks().playerActivate(bed);
    }
    return false;
}

// `persuade <kind> until <condition>`: the player keeps persuading, as a player
// keeps bribing until the speaker comes round, up to kPersuadeTries.
void PersuadedUntil(const TestCase& test, const TestStep& step) {
    const std::size_t at = step.text.find(kUntilWord);
    const std::string kind = Trim(step.text.substr(0, at));
    const std::string condition = Trim(step.text.substr(at + std::strlen(kUntilWord)));
    for (int tries = 0; Judge(test.actor, condition) != 1; ++tries) {
        if (!Talking() || tries >= kPersuadeTries || !g_talk.persuade || !g_talk.persuade(kind)) {
            Expect(test.actor, condition);
            g_case.error = "persuade " + kind + ": not true after " + std::to_string(tries) + " tries";
            return;
        }
        Said();
    }
    Expect(test.actor, condition);
}

void Click(const TestStep& step) {
    if (!Talking()) {
        g_case.error = "no conversation is open";
    } else if (step.kind == Kind::kAsk && !g_talk.ask(step.text)) {
        g_case.error = "topic '" + step.text + "' not listed; listed: " +
                       Joined(g_talk.topics());
    } else if (step.kind == Kind::kChoose && !g_talk.choose(step.number)) {
        g_case.error = "no choice " + std::to_string(step.number) + "; pending: " +
                       Joined(g_talk.choices());
    } else if (step.kind == Kind::kBye && !g_talk.bye()) {
        g_case.error = "Goodbye is locked by a pending choice";
    } else if (step.kind == Kind::kPersuade && !(g_talk.persuade && g_talk.persuade(step.text))) {
        g_case.error = "cannot persuade with '" + step.text + "' (unknown, locked or unpaid)";
    } else {
        Said();
    }
}

// `punch <n> <npc>`: the player throws up to n bare-fisted punches at the NPC,
// a real attack the engine lands. A greeting that opens ends it, as with `walk`.
bool Punched(const TestStep& step) {
    const bool done = Talking() || g_case.punches >= step.number;
    if (!done && g_case.ticksLeft > 0) {
        --g_case.ticksLeft;
        return false;
    }
    if (!done && !(Hooks().playerPunch && Hooks().playerPunch(step.text))) {
        g_case.error = "punch: the player cannot punch " + step.text;
    } else if (!done) {
        ++g_case.punches;
        g_case.ticksLeft = kPunchTicks;
        return false;
    }
    g_case.punches = 0;
    g_case.ticksLeft = -1;
    return true;
}

// `walk x y z`: the player walks to the point in the current cell, as a player
// would, so what follows or watches the player sees a walk and not a jump. A
// greeting that opens on the way ends it there, as a player stops to talk.
bool Walked(const TestStep& step) {
    float x = 0.0f, y = 0.0f, z = 0.0f;
    if (!Hooks().playerWalk || !Hooks().playerWalkDone ||
        std::sscanf(step.text.c_str(), "%f %f %f", &x, &y, &z) != 3) {
        g_case.error = "walk needs x y z and the walk hooks";
        return true;
    }
    if (g_case.ticksLeft < 0) {
        Hooks().playerWalk(x, y, z);
        g_case.ticksLeft = kWalkTicks;
        return false;
    }
    if (Hooks().playerWalkDone()) {
        g_case.ticksLeft = -1;
        return true;
    }
    if (Talking()) {
        g_case.ticksLeft = -1;
        if (Hooks().playerWalkStop) Hooks().playerWalkStop();
        return true;
    }
    if (--g_case.ticksLeft > 0) return false;
    g_case.ticksLeft = -1;
    if (Hooks().playerWalkStop) Hooks().playerWalkStop();
    g_case.error = "walk: the player never reached " + step.text;
    return true;
}

// `console <command>`: the harness runs a Skyrim console command -- one that
// does a player's action faster -- and the case goes on once it has.
void AskHarness(const std::string& command) {
    g_out << "{\"console\":" << Json(command) << "}\n";
    g_out.flush();
    g_awaitedAck = ++g_written;
}

// Does `step`; true once it is finished.
bool Advance(const TestCase& test, const TestStep& step) {
    switch (step.kind) {
        case Kind::kWait:
            return Waited(step.number);
        case Kind::kTalk:
            return Talked(step.text);
        case Kind::kExpect:
            Expect(test.actor, step.text);
            return true;
        case Kind::kRun:
            if (!RunAs(test.actor, step.text)) g_case.error = "the script did not compile or run";
            return true;
        case Kind::kRest:
            return Rested(test, step);
        case Kind::kUntil:
            return Until(test, step);
        case Kind::kActivate:
            return Activated(step.text);
        case Kind::kTake:
            if (!Hooks().playerTake) g_case.error = "no take hook";
            if (Hooks().playerTake) Hooks().playerTake(step.text);
            return true;
        case Kind::kConsole:
            AskHarness(step.text);
            return true;
        case Kind::kWalk:
            return Walked(step);
        case Kind::kPunch:
            return Punched(step);
        case Kind::kFollow:
            g_case.follow = step.text;
            g_case.trail.clear();
            return true;
        case Kind::kDefend:
            g_case.defend = true;
            return true;
        case Kind::kButton:
        case Kind::kPick:
        case Kind::kClose:
        case Kind::kSleep:
            return Answered(step);
        default:
            // A greeting posts its menu, which opens a frame or more later, and
            // one held for the player's move is already open in TES3 terms.
            if (!Talking() && (MenuStillOpening() || ++g_case.talkWait < kTalkWaitTicks)) return false;
            g_case.talkWait = 0;
            if (step.kind == Kind::kPersuade && step.text.find(kUntilWord) != std::string::npos) {
                PersuadedUntil(test, step);
            } else {
                Click(step);
            }
            return true;
    }
}

float Apart(const Spot& a, const Spot& b) {
    return std::hypot(a.x - b.x, a.y - b.y);
}

// Keeps the player on the followed NPC's trail, as a player walking behind it
// does: an `until` alone stands still while an escort walks off. Not Position,
// which sends an indoor player to the exterior; nor SetPos, which, repeated
// every tick on the player, never lands.
void KeepFollowing() {
    if (g_case.follow.empty() || !Hooks().position || !Hooks().distance || !Hooks().placeHere) {
        return;
    }
    const std::string& npc = g_case.follow;
    const Spot at{Hooks().position(npc, 0), Hooks().position(npc, 1), Hooks().position(npc, 2)};
    std::deque<Spot>& trail = g_case.trail;
    if (trail.empty() || Apart(trail.back(), at) >= kTrailStep) trail.push_back(at);
    while (trail.size() > kTrailPoints) trail.pop_front();
    const float gap = Hooks().distance("player", npc);
    if (gap <= kFollowSlack || gap == std::numeric_limits<float>::max()) return;
    const Spot* spot = &trail.front();
    for (const Spot& point : trail) {
        if (Apart(point, at) >= kFollowBehind) spot = &point;
    }
    // Already there: a move every tick would hold every `until` (MoveLanding) forever.
    const Spot me{Hooks().position("player", 0), Hooks().position("player", 1), 0.0f};
    if (Apart(me, *spot) < kTrailStep) return;
    Log("follow: the player moves to (%g, %g, %g), %g behind %s", spot->x, spot->y, spot->z, gap,
        npc.c_str());
    Hooks().placeHere("player", spot->x, spot->y, spot->z,
                      std::atan2(at.x - spot->x, at.y - spot->y) * 57.29578f);
}

bool IsAction(Kind kind) {
    return kind != Kind::kWait && kind != Kind::kExpect && kind != Kind::kUntil;
}

void Report(const TestCase& test) {
    const bool ok = g_case.error.empty() && g_case.expectsHeld;
    ok ? ++g_passed : ++g_failed;
    g_out << "{\"case\":" << Json(test.name) << ",\"ok\":" << (ok ? "true" : "false");
    if (!g_case.error.empty()) g_out << ",\"error\":" << Json(g_case.error);
    g_out << ",\"expects\":[" << g_case.expects << "],\"said\":[" << g_case.said
          << "],\"unported\":" << NewUnported() << "}\n";
    g_out.flush();
    ++g_written;
    Log("test: %s  %s%s%s", ok ? "PASS" : "FAIL", test.name.c_str(),
        g_case.error.empty() ? "" : " -- ", g_case.error.c_str());
}

// Reads the case file once; whether the runner has work to do.
bool Active() {
    if (g_mode == Mode::kUnknown) {
        const std::string casesPath = Env("TES_MW_TEST_CASES");
        g_mode = Mode::kOff;
        if (casesPath.empty()) return false;
        std::ifstream in(casesPath);
        g_cases = ParseTestCases(in);
        g_out.open(Env("TES_MW_TEST_RESULTS"), std::ios::out | std::ios::trunc);
        if (!in.is_open() || !g_out) {
            Log("test: cannot read %s or write the results file", casesPath.c_str());
            return false;
        }
        g_stopOnFail = !Env("TES_MW_TEST_STOP_ON_FAIL").empty();
        g_checkpoints = !Env("TES_MW_TEST_CHECKPOINTS").empty();
        g_ackPath = Env("TES_MW_TEST_RESULTS") + ".ack";
        State().SetGlobal(kVerdictGlobal, 0.0f);
        Log("test: %zu case(s) from %s", g_cases.size(), casesPath.c_str());
        g_mode = Mode::kActive;
    }
    return g_mode == Mode::kActive;
}

void Finish() {
    const std::vector<std::string> uncovered = UncoveredCommands(g_cases, RealCommands());
    g_out << "{\"done\":true,\"passed\":" << g_passed << ",\"failed\":" << g_failed
          << ",\"skipped\":" << g_cases.size() - g_next
          << ",\"uncovered\":" << JsonList(uncovered) << "}\n";
    g_out.close();
    Log("test: done, %d passed, %d failed, %zu real command(s) no case names", g_passed,
        g_failed, uncovered.size());
    g_mode = Mode::kFinished;
}

// Whether the harness has handled every line the runner waits on.
bool Acknowledged() {
    std::ifstream in(g_ackPath);
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
        if (held && !Talking() && needsWorld) {
            // A sleep runs its hours in real time at 1x, far past what any other menu may hold.
            const int limit = MenuUp(ids::kSleepWaitMenu) ? kHeldTicks * kSleepHeldFactor : kHeldTicks;
            if (++g_case.heldTicks > limit) g_case.error = "a menu holds the game";
            return;
        }
        g_case.heldTicks = 0;
        if (!Advance(test, step)) return;
        ++g_case.step;
        if (IsAction(step.kind)) return;
    }
}

}  // namespace

void SetTestConversation(const TestConversation& conversation) { g_talk = conversation; }

const std::vector<std::string>& TestStepNames() {
    static const std::vector<std::string> kNames = {
        "run",  "wait",  "expect", "talk", "ask",     "choose",   "bye",   "button", "activate",
        "until", "pick", "close",  "take", "console", "persuade", "sleep", "walk",   "follow", "defend",
        "punch", "rest"};
    return kNames;
}

std::vector<TestCase> ParseTestCases(std::istream& in) {
    const std::vector<std::string>& kinds = TestStepNames();
    std::set<std::string> directives(kinds.begin(), kinds.end());
    directives.insert({"case", "end"});
    std::vector<TestCase> cases;
    TestStep* body = nullptr;
    std::string line;
    while (std::getline(in, line)) {
        const std::string text = Trim(line);
        const std::string word = Lower(text.substr(0, text.find_first_of(" \t")));
        const std::string rest = Trim(text.substr(word.size()));
        if (text.empty() || text[0] == '#') continue;
        if (body && !directives.count(word)) {
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
        const auto at = std::find(kinds.begin(), kinds.end(), word);
        if (at == kinds.end()) {
            // A script line after a step opens its own body rather than vanishing.
            test.steps.push_back(TestStep{Kind::kRun, text + "\n", 0});
            body = &test.steps.back();
            continue;
        }
        TestStep step{static_cast<Kind>(at - kinds.begin()), rest,
                      std::max(0, std::atoi(rest.c_str()))};
        if (word == "until" || word == "punch" || word == "rest") {
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
                const int settle = test.steps[i].kind == Kind::kBye ? kByeSettleTicks : kSettleTicks;
                test.steps.insert(test.steps.begin() + i + 1, TestStep{Kind::kWait, "", settle});
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

void TickTestRunner(bool held) {
    if (!Active() || g_startTicks-- > 0) return;
    if (g_next >= g_cases.size()) {
        Finish();
        return;
    }
    if (g_awaitedAck && !Acknowledged()) return;
    g_awaitedAck = 0;
    const TestCase& test = g_cases[g_next];
    if (!g_case.started) {
        g_case = Progress{};
        g_case.started = true;
        g_case.unportedBefore = UnportedCommandsSeen();
    }
    // A player defending itself, or walking behind a guide, kills its attackers.
    if (!held && !Talking() && (g_case.defend || !g_case.follow.empty()) &&
        Hooks().killPlayerAttackers) {
        Hooks().killPlayerAttackers();
    }
    if (!held && !Talking()) KeepFollowing();
    RunSteps(test, held);
    if (g_case.step < test.steps.size() && g_case.error.empty()) return;
    Report(test);
    ++g_next;
    const bool failed = !g_case.error.empty() || !g_case.expectsHeld;
    g_case = Progress{};
    if (failed && g_stopOnFail) Finish();
    if (!failed && g_checkpoints) g_awaitedAck = g_written;
}

}  // namespace tesruntime::mw
