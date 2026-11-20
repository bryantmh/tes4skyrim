// Runs test cases a step per tick; see test_runner.h.

#include "test_runner.h"

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fstream>
#include <iterator>
#include <limits>
#include <set>

#include "log.h"

namespace tesruntime::test {

namespace {

using Kind = TestStep::Kind;

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
// Tries in a row the game may refuse a rest (an enemy nearby) before `rest` fails.
constexpr int kRestRefusals = 10;
// `persuade <kind> until <condition>`: the word that splits it, and how many
// attempts the player makes before the case fails.
constexpr const char* kUntilWord = " until ";
constexpr int kPersuadeTries = 30;
// How long a menu other than the conversation may hold a step that needs the
// world before the case fails naming it, rather than the run hanging.
constexpr int kHeldTicks = 300;
// Moves one `approach` may post before the case fails: a passing one lands on
// its first, and an unbounded retry held a run for 47 minutes.
constexpr int kApproachMoves = 10;
// How long a box holds a step before a game's dismissNotice clicks its only button: a box the case
// itself expects is answered by its next step well before then.
constexpr int kNoticeTicks = 30;
// How long `ask` waits for its topic: a several-line answer plays in real time before the next topics list.
constexpr int kAskTicks = 900;
// The farthest from a door's ORIGIN a player can use it. The reach itself is the
// INI's fActivatePickLength (180), but an origin can sit deep in the wall: the
// player stands 259 from Ald Daedroth's at the rock face; plus a `walk`'s arrival slack (128), as
// a walk to a door's own landing may stop that short (Trun Telur's inner gate: landing 472 away).
constexpr float kDoorReach = 512.0f + 128.0f;
// The Sleep/Wait menu may hold that many times longer: a 24-hour sleep at 1x.
constexpr int kSleepHeldFactor = 20;
constexpr const char* kSleepWaitMenu = "Sleep/Wait Menu";
// How long a `walk` may take: room for crossing a region on foot.
constexpr int kWalkTicks = 9000;
// The world settles this long after a load before the first case, as no
// player acts in the first instant: a load door activated sooner is ignored.
constexpr int kStartTicks = 60;
// A harness request whose answer has not come back yet.
constexpr int kPending = -2;
// `follow`: how far the NPC may get ahead before the player is moved up, how
// far apart the trail's points are, how many it keeps, and how far behind the
// NPC the player is put -- on a spot the NPC itself stood on, so on the ground.
constexpr float kFollowSlack = 150.0f;
constexpr float kTrailStep = 40.0f;
// Closer than this, a followed NPC is walking into the player (`fillTrail` games only).
constexpr float kInTheWay = 32.0f;
// A followed NPC standing this many ticks with the player close is waiting for the player to clear its path
// (Skyrim's pathing does, Escort and Travel alike); the player then steps back this far along its trail.
constexpr int kBlockedTicks = 60;
constexpr float kStepBack = 150.0f;
constexpr std::size_t kTrailPoints = 24;
constexpr float kFollowBehind = 120.0f;
// Ticks (one second) a dialogue step waits for a posted conversation to open.
constexpr int kTalkWaitTicks = 30;
// Ticks a MessageBox must have been up before a `button` press.
constexpr int kBoxSettleTicks = 2;
// Ticks a `button` step waits, after its press, for that box to close before it ends.
constexpr int kBoxGoneTicks = 30;
// Ticks between a `punch` step's swings: room for one unarmed attack to land.
constexpr int kPunchTicks = 45;
// Times one `walk` may put its player back on the navmesh after a slide off it.
constexpr int kWalkRescues = 3;
// Times one `walk` whose player never left its starting spot is issued again, and how far counts as leaving
// (wide: the player can settle ~40 off a Position before the walk is issued).
constexpr int kWalkRestarts = 3;
constexpr float kWalkStarted = 128.0f;

enum class Mode { kUnknown, kOff, kActive, kFinished };

struct Spot {
    float x = 0.0f, y = 0.0f, z = 0.0f;
};

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
    int ticksLeft = -1;        // a wait, talk, walk or in-game until under way; -1 when none is
    long long deadline = -1;   // the tick a harness-judged until or a hear gives up at; -1 when none is
    bool asked = false;        // a harness request for this step is out
    int heldTicks = 0;         // ticks a menu has held a step that needs the world
    int loadTicks = 0;         // ticks a talk or activate has waited for its target's 3D
    int approachMoves = 0;     // moves one `approach` has posted without landing in reach
    int talkClicks = 0;        // clicks one `talk` has made that opened nothing
    bool expectsHeld = true;
    std::string error;
    std::string expects;
    std::string said;
    std::size_t heardStart = 0;  // the first line begun since the case started
    std::string follow;          // the NPC `follow` keeps the player behind, or empty
    std::deque<Spot> trail;      // where that NPC has stood, oldest first
    Spot followStill;            // where the followed NPC stood when it last moved
    int followStillTicks = 0;    // ticks it has stood there
    int talkWait = 0;            // ticks a dialogue step has waited for the menu to open
    int boxUp = 0;               // ticks a `button` step has seen its MessageBox open
    bool defend = false;         // the player kills whatever attacks it
    std::string ally;            // `defend <ally>`: and whatever attacks this actor
    int punches = 0;             // punches the current `punch` step has thrown
    int rests = 0;               // sleeps the current `rest` step has taken
    int restTries = 0;           // tries since its last sleep that opened no Sleep/Wait menu
    bool asleep = false;         // a sleep was ordered and its Sleep/Wait menu is still open
    int rescues = 0;             // times the current `walk` put its slid-off player back on the navmesh
    bool rescueAsked = false;    // the harness has been asked where to put it
    int restarts = 0;            // times the current `walk` was issued again because it never started
    Spot walkFrom;               // where the player stood when the current `walk` was issued
};

Progress g_case;

// Every line begun this run, and the first one no `hear` has taken yet: a
// line begun while an earlier case still ran (a force-greet its last stage
// started) is still there for the next case's `hear`.
std::vector<SpokenLine> g_heard;
std::size_t g_heardFrom = 0;

bool Talking() { return g_game.talking && g_game.talking(); }

// A conversation is open with someone other than `npc` (never, on a backend that cannot tell).
bool TalkingToOther(const std::string& npc) { return Talking() && g_game.talkingWith && !g_game.talkingWith(npc); }
bool MenuStillOpening() { return g_game.menuOpening && g_game.menuOpening(); }
bool MoveLanding() { return g_game.moveLanding && g_game.moveLanding(); }

std::string Env(const std::string& name) {
    char buf[1024];
    const DWORD n = GetEnvironmentVariableA(name.c_str(), buf, sizeof(buf));
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
// kPending while the harness evaluates it. `left` (optional) gets the left side.
int Judge(const std::string& actor, const std::string& condition, std::string* left) {
    if (g_game.judge) return g_game.judge(actor, condition, left);
    std::string answer;
    const int verdict =
        AskHarness("{\"judge\":" + Json(condition) + ",\"actor\":" + Json(actor) + "}", &answer);
    if (left) *left = answer;
    return verdict;
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

// Records a condition a step waited on: judged again in game (as an expect),
// or from the harness's last answer.
void Settle(const std::string& actor, const std::string& condition, int verdict, const std::string& left) {
    if (g_game.judge) {
        Expect(actor, condition);
    } else {
        Record(condition, verdict, left);
    }
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

// The failure for a reference that never became usable, saying where it is.
std::string NeverLoaded(const std::string& id) {
    const std::string where = g_game.whereIs ? g_game.whereIs(id) : "";
    return "'" + id + "' never loaded" + (where.empty() ? "" : " -- " + where);
}

// The player activating `id`, once it is in the world: no player clicks what
// has not loaded, and an activation posted while its cell still loads can
// deadlock on the reference's extra data. True when done or failed.
bool Activated(const std::string& id) {
    if (!g_game.activate) {
        g_case.error = "no activate hook";
        return true;
    }
    // A load door activated while the player's own move is landing goes nowhere.
    if (MoveLanding()) return false;
    // A player uses a door from beside it; one used from afar teleports past the
    // follower, which the door then carries in mid-route.
    const float door = g_game.doorDistance ? g_game.doorDistance(id) : -1.0f;
    const std::string who = door > kDoorReach && g_game.followerNames ? g_game.followerNames() : "";
    if (!who.empty()) {
        g_case.error = "'" + id + "' used from " + std::to_string(static_cast<int>(door)) +
                       " units with a follower along (" + who + "); walk to it";
        return true;
    }
    if (g_game.activate(id)) {
        g_case.loadTicks = 0;
        return true;
    }
    if (++g_case.loadTicks <= kHeldTicks) return false;
    g_case.error = NeverLoaded(id);
    return true;
}

// `approach`: once `npc` resolves, the player is put in front of it if out of
// reach, and the step ends when the move has landed. True when done or failed.
// A conversation already open with `npc`, or opening, is left alone: moving the player then closes it, and a
// say-once force-greet is spent unheard. One left open with someone else gets its Goodbye first.
bool Approached(const std::string& npc) {
    if (!g_game.approach) {
        g_case.error = "no approach hook";
        return true;
    }
    if (MoveLanding()) return false;
    if (TalkingToOther(npc)) {
        if (g_game.bye) g_game.bye();
        return false;
    }
    if (Talking() || MenuStillOpening()) return true;
    const int moved = g_game.approach(npc);
    if (moved > 0) {
        if (++g_case.approachMoves <= kApproachMoves) return false;
        g_case.error = "approach: the player never came within reach of " + npc;
        return true;
    }
    if (moved == 0) {
        g_case.loadTicks = 0;
        g_case.approachMoves = 0;
        return true;
    }
    if (++g_case.loadTicks <= kHeldTicks) return false;
    g_case.error = NeverLoaded(npc);
    return true;
}

// Activates `npc` as the player does, then waits for the conversation; true when done. A conversation already
// open with `npc` (a force-greet) is taken as is: activating its speaker mid-greeting left later clicks
// unanswered. One left open with someone else gets its Goodbye first, as the player clicks it. A game with
// `talkClicks` clicks again when a click opened nothing (the engine swallows some, 60a: Martin at Cloud Ruler).
bool Talked(const std::string& npc) {
    if (g_case.ticksLeft < 0 && TalkingToOther(npc)) {
        if (g_game.bye) g_game.bye();
        return false;
    }
    if (g_case.ticksLeft < 0 && !Talking()) {
        if (!Activated(npc)) return false;
        if (!g_case.error.empty()) return true;
        g_case.ticksLeft = kTalkTicks;
        return false;
    }
    if (Talking()) {
        g_case.ticksLeft = -1;
        g_case.talkClicks = 0;
        Said();
        return true;
    }
    if (--g_case.ticksLeft > 0) return false;
    g_case.ticksLeft = -1;
    if (g_case.talkClicks++ < g_game.talkClicks) {
        Log("test: a click on %s opened nothing; the player clicks again", npc.c_str());
        return false;
    }
    g_case.talkClicks = 0;
    g_case.error = "talk " + npc + ": no conversation opened";
    return true;
}

// Waits for `step`'s condition to hold, up to its tick budget; true when done.
// A miss records the condition, with its left side, as a failed expect. A
// player move still landing would let it judge the old position. A harness
// judge spans ticks, so its budget runs on the tick count.
bool Until(const TestCase& test, const TestStep& step) {
    if (MoveLanding()) return false;
    if (g_case.ticksLeft < 0) g_case.ticksLeft = step.number;
    if (g_case.deadline < 0) g_case.deadline = g_tick + step.number;
    std::string left = "null";
    const int verdict = Judge(test.actor, step.text, g_game.judge ? nullptr : &left);
    if (verdict == kPending) return false;
    const bool held = verdict == 1;
    const bool timeLeft = g_game.judge ? --g_case.ticksLeft > 0 : g_tick < g_case.deadline;
    if (!held && timeLeft) return false;
    g_case.ticksLeft = -1;
    g_case.deadline = -1;
    Settle(test.actor, step.text, verdict, left);
    if (!held) g_case.error = "until: not true within " + std::to_string(step.number) + " ticks";
    return true;
}

// Waits for `done` to come true, up to `ticks`; true once it did or the case
// failed with `failure`.
bool Retried(bool done, const std::string& failure, int ticks = kHeldTicks) {
    if (done) {
        g_case.ticksLeft = -1;
        return true;
    }
    if (g_case.ticksLeft < 0) g_case.ticksLeft = ticks;
    if (--g_case.ticksLeft > 0) return false;
    g_case.ticksLeft = -1;
    g_case.error = failure;
    return true;
}

// Whether the engine's menu `name` is open; true when the game cannot say.
bool MenuUp(const std::string& name) { return !g_game.menuOpen || g_game.menuOpen(name); }

// Whether the game reports menu `name` open; false when it cannot say.
bool MenuReported(const char* name) { return g_game.menuOpen && g_game.menuOpen(name); }

// The steps that answer a menu other than the conversation, each waiting for
// its menu to come up first: a MessageBox button, a chargen pick, a sleep, a close.
bool Answered(const TestStep& step) {
    if (step.kind == Kind::kButton) {
        if (!g_game.pressButton) return Retried(false, "no button hook");
        // Pressed, it ends once that box has gone (or after kBoxGoneTicks), so the next `button` cannot
        // press a box that is still closing: the runtime's class quiz raises its next box at once.
        if (g_case.boxUp < 0) {
            const bool gone = !MenuReported("MessageBoxMenu") || ++g_case.boxUp >= 0;
            if (gone) g_case.boxUp = 0;
            return gone;
        }
        // Pressed only once the box has been up for kBoxSettleTicks: a click in its opening frame is lost.
        const bool up = MenuUp("MessageBoxMenu") && ++g_case.boxUp > kBoxSettleTicks;
        if (!up) return Retried(false, "no MessageBox opened");
        g_game.pressButton(step.number);
        g_case.boxUp = -kBoxGoneTicks;
        g_case.ticksLeft = -1;
        return false;
    }
    if (step.kind == Kind::kPick) {
        return Retried(g_game.menuPick && g_game.menuPick(step.text),
                       "no open class, birthsign or level-up menu takes '" + step.text + "'");
    }
    if (step.kind == Kind::kSleep) {
        const bool up = g_game.sleepFor && MenuUp(kSleepWaitMenu);
        if (up) g_game.sleepFor(step.number);
        return Retried(up, "the Sleep/Wait menu never opened");
    }
    const bool up = g_game.closeMenu && MenuUp(step.text);
    if (up) g_game.closeMenu(step.text);
    return Retried(up, "menu '" + step.text + "' never opened");
}

// The menus a held game is reported with, so a failure names what held it.
constexpr const char* kHoldingMenus[] = {
    "MessageBoxMenu", "Tutorial Menu", "Loading Menu", "Sleep/Wait Menu", "Dialogue Menu",
    "Journal Menu", "Console", "Tween Menu", "MorrowindDialogueMenu", "MorrowindClassMenu",
    "MorrowindBirthMenu", "MorrowindRaceMenu", "MorrowindListMenu", "RaceSex Menu", "Book Menu",
    "ContainerMenu", "InventoryMenu", "LevelUp Menu", "StatsMenu", "Lockpicking Menu"};

// "a menu holds the game" plus the open ones among kHoldingMenus.
std::string HeldMenuError() {
    std::string open;
    for (const char* name : kHoldingMenus) {
        if (MenuReported(name)) open += (open.empty() ? "" : ", ") + std::string(name);
    }
    return "a menu holds the game (" + (open.empty() ? std::string("none of the known menus") : open) + ")";
}

// Ends a `rest`: records its condition and names why it stopped short.
void RestEnded(const TestCase& test, const std::string& condition, int verdict, const std::string& left,
               int sleeps) {
    Settle(test.actor, condition, verdict, left);
    if (g_case.restTries > kRestRefusals) {
        g_case.error = "rest: the game refused the player's rest " + std::to_string(kRestRefusals) +
                       " times in a row";
    } else if (verdict != 1) {
        g_case.error = "rest: not true after " + std::to_string(sleeps) + " sleeps";
    }
    g_case.rests = 0;
    g_case.restTries = 0;
    g_case.asleep = false;
    g_case.ticksLeft = -1;
}

// `rest <max>[x<hours>] <bed> <condition>`: the player sleeps `hours` (1) in
// the bed, up to `max` times, until the condition holds -- as a player sleeps
// until something happens. Each sleep counts once, when its menu closes. A
// message box between sleeps (a wake-up notice) is dismissed. A bed of `-`
// waits on the spot instead, as the Wait key does.
bool Rested(const TestCase& test, const TestStep& step) {
    const std::size_t count = step.text.find(' ');
    const std::size_t times = step.text.find('x');
    const int hours = times < count ? std::max(1, std::atoi(step.text.c_str() + times + 1)) : 1;
    const std::string after = Trim(step.text.substr(std::min(step.text.size(), count)));
    const std::size_t gap = after.find(' ');
    const std::string bed = after.substr(0, gap);
    const std::string condition = Trim(after.substr(std::min(after.size(), gap)));
    if (MoveLanding() || Talking()) return false;
    std::string left = "null";
    const int verdict = Judge(test.actor, condition, g_game.judge ? nullptr : &left);
    if (verdict == kPending) return false;
    if (MenuReported("MessageBoxMenu") && g_game.pressButton) {
        g_game.pressButton(0);
        return false;
    }
    const bool menu = MenuReported(kSleepWaitMenu);
    if (!menu) g_case.asleep = false;
    if (g_case.asleep) g_case.ticksLeft = kRestSettleTicks + 1;
    const bool settled = --g_case.ticksLeft <= 0;
    if (verdict == 1 || g_case.restTries > kRestRefusals || (settled && g_case.rests >= step.number)) {
        RestEnded(test, condition, verdict, left, step.number);
        return true;
    }
    if (!settled) return false;
    g_case.ticksLeft = kRestSettleTicks;
    if (menu && g_game.sleepFor) {
        g_game.sleepFor(hours);
        g_case.asleep = true;
        ++g_case.rests;
        g_case.restTries = 0;
        return false;
    }
    ++g_case.restTries;
    if (bed == "-") {
        if (g_game.wait) g_game.wait();
    } else if (g_game.activate) {
        g_game.activate(bed);
    }
    return false;
}

// `persuade <kind> until <condition>`: the player keeps persuading, as a player
// keeps bribing until the speaker comes round, up to kPersuadeTries. Judged in
// game only: the tries run within one tick.
void PersuadedUntil(const TestCase& test, const TestStep& step) {
    const std::size_t at = step.text.find(kUntilWord);
    const std::string kind = Trim(step.text.substr(0, at));
    const std::string condition = Trim(step.text.substr(at + std::strlen(kUntilWord)));
    if (!g_game.judge) {
        g_case.error = "persuade " + kind + " until: needs an in-game judge";
        return;
    }
    for (int tries = 0; Judge(test.actor, condition, nullptr) != 1; ++tries) {
        if (!Talking() || tries >= kPersuadeTries || !g_game.persuade || !g_game.persuade(kind)) {
            Expect(test.actor, condition);
            g_case.error = "persuade " + kind + ": not true after " + std::to_string(tries) + " tries";
            return;
        }
        Said();
    }
    Expect(test.actor, condition);
}

// `bye if open` closes a greeting the fight may or may not have forced; `bye locked` is a Goodbye the game must
// refuse while a choice is pending.
void Click(const TestStep& step) {
    const auto listed = [](std::vector<std::string> (*list)()) {
        return list ? Joined(list()) : std::string("(not readable)");
    };
    if (!Talking()) {
        if (step.kind != Kind::kBye || step.text != "if open") g_case.error = "no conversation is open";
    } else if (step.kind == Kind::kBye && step.text == "locked") {
        if (g_game.bye && g_game.bye()) g_case.error = "Goodbye was not locked: the conversation was left";
    } else if (step.kind == Kind::kChoose && !(g_game.choose && g_game.choose(step.number))) {
        g_case.error = "no choice " + std::to_string(step.number) + "; pending: " + listed(g_game.choices);
    } else if (step.kind == Kind::kBye && !(g_game.bye && g_game.bye())) {
        g_case.error = "Goodbye is locked by a pending choice";
    } else if (step.kind == Kind::kPersuade && !(g_game.persuade && g_game.persuade(step.text))) {
        g_case.error = "cannot persuade with '" + step.text + "' (unknown, locked or unpaid)";
    } else {
        Said();
    }
}

// `ask`: waits, as a player does, for the topic to be listed (Skyrim lists a
// line's new topics only once its whole answer is spoken), up to kAskTicks.
bool Asked(const TestStep& step) {
    if (!Talking()) {
        Click(step);
        return true;
    }
    const bool asked = g_game.ask && g_game.ask(step.text);
    const std::string listed = asked || !g_game.topics ? "(not readable)" : Joined(g_game.topics());
    const bool done = Retried(asked, "topic '" + step.text + "' not listed; listed: " + listed, kAskTicks);
    if (asked) Said();
    return done;
}

// The conversation's steps: each first waits for a posted conversation to open.
bool Conversed(const TestCase& test, const TestStep& step) {
    // A greeting posts its menu, which opens a frame or more later, and
    // one held for the player's move is already open in TES3 terms.
    if (!Talking() && (MenuStillOpening() || ++g_case.talkWait < kTalkWaitTicks)) return false;
    g_case.talkWait = 0;
    if (step.kind == Kind::kAsk) return Asked(step);
    if (step.kind == Kind::kPersuade && step.text.find(kUntilWord) != std::string::npos) {
        PersuadedUntil(test, step);
    } else {
        Click(step);
    }
    return true;
}

// `punch <n> <npc>`: the player throws up to n bare-fisted punches at the NPC,
// a real attack the engine lands. A greeting that opens ends it, as with `walk`.
bool Punched(const TestStep& step) {
    const bool done = Talking() || g_case.punches >= step.number;
    if (!done && g_case.ticksLeft > 0) {
        --g_case.ticksLeft;
        return false;
    }
    if (!done && !(g_game.punch && g_game.punch(step.text))) {
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

// Menus whose opening ends a silence as a line does, for the harness's `quiet`;
// whether each was open at the last tick.
const char* const kSilenceEnders[] = {"MessageBoxMenu", "MorrowindClassMenu", "MorrowindBirthMenu"};
bool g_enderOpen[std::size(kSilenceEnders)] = {};

// Each silence-ending menu that opened since the last tick goes to the results with its time.
void NoteMenusOpened(const TestCase& test) {
    if (!g_game.menuOpen) return;
    for (std::size_t i = 0; i < std::size(kSilenceEnders); ++i) {
        const bool open = g_game.menuOpen(kSilenceEnders[i]);
        if (open && !g_enderOpen[i]) {
            const unsigned game = g_game.gameMs ? g_game.gameMs() : 0;
            WriteLine("{\"menu\":" + Json(kSilenceEnders[i]) + ",\"ms\":" + std::to_string(GetTickCount64()) +
                      ",\"game\":" + std::to_string(game) + ",\"tick\":" + std::to_string(g_tick) +
                      ",\"during\":" + Json(test.name) + "}");
        }
        g_enderOpen[i] = open;
    }
}

// The lines begun since the last tick: each goes to the results now, so the
// run shows when it was spoken, and joins the case for `hear` and its line.
void Listen(const TestCase& test) {
    NoteMenusOpened(test);
    if (!g_game.spoken) return;
    for (const SpokenLine& line : g_game.spoken()) {
        const std::string spoke = "{\"speaker\":" + Json(line.speaker) + ",\"text\":" + Json(line.text);
        WriteLine(spoke + ",\"ms\":" + std::to_string(line.ms) + ",\"game\":" + std::to_string(line.game) +
                  ",\"tick\":" + std::to_string(g_tick) + ",\"during\":" + Json(test.name) + "}");
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

// `said <text>` / `unsaid <text>`: fails the case unless (when) a line said in it so far, greetings included,
// contains the text.
bool SaidCheck(const TestStep& step) {
    const std::string want = Lower(Trim(step.text));
    const bool found = Lower(g_case.said).find(want) != std::string::npos;
    if (found != (step.kind == Kind::kSaid)) {
        g_case.error = (found ? "unsaid: '" : "said: never '") + want + (found ? "' was said" : "'");
    }
    return true;
}

// A `walk` whose player stalled off the navmesh -- slid down a slope past the mesh's edge, where the
// engine's Travel never moves it again -- is put back on the mesh a little further along its route
// (the harness picks the point from the built ESM; it answers 'none' when the player stands on the mesh,
// so a stall on the mesh still fails). True while that is under way; the walk is issued again after.
// See: docs/commentary/headless_testing.md#quest-runs
bool RescuedSlide(float x, float y, float z) {
    if (g_case.rescueAsked) {
        g_case.rescueAsked = false;
        float ax = 0.0f, ay = 0.0f, az = 0.0f;
        std::ifstream in(g_resultsPath + ".answer");
        if (!(in >> ax >> ay >> az)) {
            g_case.rescues = kWalkRescues;
            return false;
        }
        Log("test: the player slid off the navmesh -- put back on it at (%g, %g, %g)", ax, ay, az);
        if (g_game.walkStop) g_game.walkStop();
        g_game.placeHere("player", ax, ay, az, 0.0f);
        ++g_case.rescues;
        g_case.ticksLeft = -1;
        return true;
    }
    if (g_case.rescues >= kWalkRescues || !g_game.walkStalled || !g_game.walkStalled() || !g_game.position ||
        !g_game.placeHere) {
        return false;
    }
    const bool inside = g_game.inInterior && g_game.inInterior() && g_game.cell;
    char line[160];
    std::snprintf(line, sizeof(line), "{\"onmesh\":[%g,%g,%g],\"to\":[%g,%g,%g],\"cell\":", g_game.position("player", 0),
                  g_game.position("player", 1), g_game.position("player", 2), x, y, z);
    WriteLine(line + Json(inside ? g_game.cell() : "") + "}");
    g_awaitedAck = g_written;
    g_case.rescueAsked = true;
    return true;
}

// A `walk` whose travel package never moved the player from where it was issued is the test framework's
// failure, not the game's (the engine sometimes leaves a just-filled Travel standing): it is issued again,
// up to kWalkRestarts times. True when it was.
bool RestartedWalk() {
    if (g_case.restarts >= kWalkRestarts || !g_game.walkStalled || !g_game.walkStalled() || !g_game.position) {
        return false;
    }
    const float px = g_game.position("player", 0), py = g_game.position("player", 1);
    if (std::hypot(px - g_case.walkFrom.x, py - g_case.walkFrom.y) > kWalkStarted) return false;
    Log("test: the walk never started (player still at %g, %g) -- issuing it again", px, py);
    if (g_game.walkStop) g_game.walkStop();
    ++g_case.restarts;
    g_case.ticksLeft = -1;
    return true;
}

// Ends a walk: true, with its retries forgotten and, unless it arrived, the player stopped.
bool WalkEnded(bool arrived) {
    g_case.ticksLeft = -1;
    g_case.rescues = 0;
    g_case.restarts = 0;
    if (!arrived && g_game.walkStop) g_game.walkStop();
    return true;
}

// Issues the walk or steer `step` names; false when the backend cannot take it.
bool IssueWalk(const TestStep& step, bool steer, const Spot& to, bool point, const char* pace) {
    if (steer) {
        if (!g_game.steer || !point) return false;
        g_game.steer(to.x, to.y, to.z, std::strcmp(pace, "walk") == 0);
    } else if (point && g_game.walk) {
        g_game.walk(to.x, to.y, to.z);
    } else if (g_game.walkTo) {
        g_game.walkTo(step.text);
    } else {
        return false;
    }
    if (g_game.position) g_case.walkFrom = {g_game.position("player", 0), g_game.position("player", 1), 0.0f};
    g_case.ticksLeft = kWalkTicks;
    return true;
}

// `walk x y z` (or, where the backend walks to references, `walk <ref>`): the
// player walks there, as a player would, so what follows or watches the player
// sees a walk and not a jump. A greeting that opens on the way ends it there,
// as a player stops to talk. `steer x y z` goes there at the controls instead,
// straight over open water; `steer x y z walk` at a walk, as a player edges
// along a narrow ledge.
bool Walked(const TestStep& step) {
    Spot to;
    char pace[8] = {};
    const bool steer = step.kind == Kind::kSteer;
    const bool point = sscanf_s(step.text.c_str(), "%f %f %f %7s", &to.x, &to.y, &to.z, pace,
                                static_cast<unsigned>(sizeof(pace))) >= 3;
    if (g_case.ticksLeft < 0) {
        // A walk issued while a door's move is landing starts from the old cell's spot.
        if (MoveLanding()) return false;
        if (!g_game.walkDone || !IssueWalk(step, steer, to, point, pace)) {
            g_case.error = "walk needs x y z and the walk hooks";
            return true;
        }
        return false;
    }
    if (g_game.walkDone()) return WalkEnded(true);
    if (Talking()) return WalkEnded(false);
    if (!steer && (RestartedWalk() || (point && RescuedSlide(to.x, to.y, to.z)))) return false;
    const bool stuck = g_game.walkStuck && g_game.walkStuck();
    if (--g_case.ticksLeft > 0 && !stuck) return false;
    WalkEnded(false);
    g_case.error = (stuck ? "steer: the player is stuck short of " : "walk: the player never reached ") +
                   step.text + (g_game.whereIs ? " -- the player " + g_game.whereIs("player") : "");
    return true;
}

// `pace <speed>`: the --fast speed from here on; 0 is the default.
void SetPace(float speed) {
    if (g_game.setPace) {
        g_game.setPace(speed);
    } else if (speed > 0.0f) {
        g_case.error = "no pace hook";
    }
}

// A case's speed from its start: its leading `pace` step's, else the default (0), so a
// window the case before opened at that speed is never run at the default in between.
float LeadingPace(const TestCase& test) {
    const bool leads = !test.steps.empty() && test.steps.front().kind == Kind::kPace;
    return leads ? std::strtof(test.steps.front().text.c_str(), nullptr) : 0.0f;
}

// `console <command>`: the harness runs a Skyrim console command -- one that
// does a player's action faster -- and the case goes on once it has.
void Console(const std::string& command) {
    WriteLine("{\"console\":" + Json(command) + "}");
    g_awaitedAck = g_written;
}

// `follow` and `defend` switch on for the rest of the case; false when the game cannot do them.
bool Switched(const TestStep& step) {
    if (step.kind == Kind::kDefend) {
        g_case.defend = true;
        g_case.ally = step.text;
        if (!g_game.killPlayerAttackers) g_case.error = "no defend hook";
        if (!step.text.empty() && !g_game.killAttackersOf) g_case.error = "no defend-an-ally hook";
        return true;
    }
    g_case.follow = step.text;
    g_case.trail.clear();
    if (!step.text.empty() && !(g_game.position && g_game.distance && g_game.placeHere)) {
        g_case.error = "no follow hooks";
    }
    return true;
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
        case Kind::kRest:
            return Rested(test, step);
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
        case Kind::kSteer:
            return Walked(step);
        case Kind::kPunch:
            return Punched(step);
        case Kind::kFollow:
        case Kind::kDefend:
            return Switched(step);
        case Kind::kApproach:
            return Approached(step.text);
        case Kind::kPace:
            SetPace(std::strtof(step.text.c_str(), nullptr));
            return true;
        case Kind::kHear:
            return Heard(step);
        case Kind::kUnsaid:
        case Kind::kSaid:
            return SaidCheck(step);
        case Kind::kButton:
        case Kind::kPick:
        case Kind::kClose:
        case Kind::kSleep:
            return Answered(step);
        default:
            return Conversed(test, step);
    }
}

float Apart(const Spot& a, const Spot& b) { return std::hypot(a.x - b.x, a.y - b.y); }

// Keeps the player on the followed NPC's trail, as a player walking behind it
// does: an `until` alone stands still while an escort walks off. Not Position,
// which sends an indoor player to the exterior; nor SetPos, which, repeated
// every tick on the player, never lands.
// A `fillTrail` game (off for every other game, whose follow is unchanged): at a fast pace the NPC
// covers several steps between two looks, so its way between them is filled in and a point just
// behind it always exists. A longer jump is a door: the trail starts again.
void FillTrail(std::deque<Spot>& trail, const Spot& at) {
    if (!trail.empty() && Apart(trail.back(), at) >= kTrailStep * kTrailPoints) trail.clear();
    while (!trail.empty() && Apart(trail.back(), at) >= 2.0f * kTrailStep) {
        const Spot from = trail.back();
        const float part = kTrailStep / Apart(from, at);
        trail.push_back({from.x + (at.x - from.x) * part, from.y + (at.y - from.y) * part,
                         from.z + (at.z - from.z) * part});
    }
}

// A `fillTrail` game: an NPC that turned round and walked back onto the player (closer than kInTheWay) has the
// player on its way; the spot is then behind it against its newest heading, as a player steps aside (60b: Baurus
// stood 8 from the player for minutes, and walked on at once when the player moved).
bool StepAside(const std::deque<Spot>& trail, const Spot& at, float gap, float slack, Spot* out) {
    if (!g_game.fillTrail || gap >= kInTheWay || trail.size() < 2) return false;
    const Spot& from = trail[trail.size() - 2];
    const float run = Apart(from, at);
    if (run < 1.0f) return false;
    *out = {at.x - (at.x - from.x) / run * slack, at.y - (at.y - from.y) / run * slack, at.z};
    return true;
}

// A `fillTrail` game: the followed NPC has stood kBlockedTicks with the player within twice `slack` -- the
// player is on its path (60a sewer stair top: Baurus stood 3600 ticks, and walked on the moment the player
// was put behind him; with the player's collision off he still waited). The spot is kStepBack back along
// its trail, the way it came, as a player clears the way.
bool StepBack(const std::deque<Spot>& trail, const Spot& at, float gap, float slack, Spot* out) {
    if (!g_game.fillTrail) return false;
    if (Apart(g_case.followStill, at) >= 4.0f) {
        g_case.followStill = at;
        g_case.followStillTicks = 0;
        return false;
    }
    if (++g_case.followStillTicks < kBlockedTicks || gap >= 2.0f * slack || trail.empty()) return false;
    g_case.followStillTicks = 0;
    *out = trail.front();
    for (auto it = trail.rbegin(); it != trail.rend(); ++it) {
        if (Apart(*it, at) >= kStepBack) {
            *out = *it;
            break;
        }
    }
    return Apart(*out, at) >= kInTheWay;
}

void KeepFollowing() {
    if (g_case.follow.empty() || !g_game.position || !g_game.distance || !g_game.placeHere) return;
    const std::string& npc = g_case.follow;
    const Spot at{g_game.position(npc, 0), g_game.position(npc, 1), g_game.position(npc, 2)};
    std::deque<Spot>& trail = g_case.trail;
    if (g_game.fillTrail) FillTrail(trail, at);
    if (trail.empty() || Apart(trail.back(), at) >= kTrailStep) trail.push_back(at);
    while (trail.size() > kTrailPoints) trail.pop_front();
    const float gap = g_game.distance("player", npc);
    const float slack = g_game.followSlack > 0.0f ? g_game.followSlack : kFollowSlack;
    Spot aside;
    if (StepAside(trail, at, gap, slack, &aside)) {
        Log("follow: %s walked onto the player (%g) -- the player steps behind it", npc.c_str(), gap);
        g_game.placeHere("player", aside.x, aside.y, aside.z, std::atan2(at.x - aside.x, at.y - aside.y) * 57.29578f);
        return;
    }
    if (StepBack(trail, at, gap, slack, &aside)) {
        Log("follow: %s has stood still with the player %g from it -- the player steps back out of its way",
            npc.c_str(), gap);
        g_game.placeHere("player", aside.x, aside.y, aside.z, std::atan2(at.x - aside.x, at.y - aside.y) * 57.29578f);
        return;
    }
    const float behind = g_game.followBehind > 0.0f ? g_game.followBehind : kFollowBehind;
    if (gap <= slack || gap == std::numeric_limits<float>::max()) return;
    const Spot* spot = &trail.front();
    for (const Spot& point : trail) {
        if (Apart(point, at) >= behind) spot = &point;
    }
    // Already there: a move every tick would hold every `until` (MoveLanding) forever.
    const Spot me{g_game.position("player", 0), g_game.position("player", 1), 0.0f};
    // A `fillTrail` game: with no trail behind the NPC yet (it has not moved since a door), the only point is
    // the NPC itself, and a player put there lands beside it, in its way (55b). The player closes in along
    // its own line to the NPC instead, staying on the side it came from.
    Spot closer;
    if (g_game.fillTrail && Apart(*spot, at) < behind) {
        const float part = slack / (std::max)(Apart(me, at), slack);
        closer = {at.x + (me.x - at.x) * part, at.y + (me.y - at.y) * part, at.z};
        spot = &closer;
    }
    if (Apart(me, *spot) < kTrailStep) return;
    Log("follow: the player moves to (%g, %g, %g), %g behind %s", spot->x, spot->y, spot->z, gap, npc.c_str());
    g_game.placeHere("player", spot->x, spot->y, spot->z, std::atan2(at.x - spot->x, at.y - spot->y) * 57.29578f);
}

bool IsAction(Kind kind) {
    return kind != Kind::kWait && kind != Kind::kExpect && kind != Kind::kUntil && kind != Kind::kHear &&
           kind != Kind::kUnsaid && kind != Kind::kSaid;
}

void Report(const TestCase& test) {
    const bool ok = g_case.error.empty() && g_case.expectsHeld;
    ok ? ++g_passed : ++g_failed;
    std::string line = "{\"case\":" + Json(test.name) + ",\"ok\":" + (ok ? "true" : "false");
    if (!g_case.error.empty()) line += ",\"error\":" + Json(g_case.error);
    line += ",\"expects\":[" + g_case.expects + "],\"said\":[" + g_case.said + "]";
    if (g_game.newIssues) line += ",\"unported\":" + JsonList(g_game.newIssues());
    WriteLine(line + "}");
    Log("test: %s  %s%s%s", ok ? "PASS" : "FAIL", test.name.c_str(), g_case.error.empty() ? "" : " -- ",
        g_case.error.c_str());
}

// Reads the case file once; whether the runner has work to do.
bool Active() {
    if (g_mode == Mode::kUnknown) {
        const std::string prefix = g_game.envPrefix;
        const std::string casesPath = Env(prefix + "CASES");
        g_mode = Mode::kOff;
        if (casesPath.empty()) return false;
        std::ifstream in(casesPath);
        g_cases = ParseTestCases(in);
        g_resultsPath = Env(prefix + "RESULTS");
        g_out.open(g_resultsPath, std::ios::out | std::ios::trunc);
        if (!in.is_open() || !g_out) {
            Log("test: cannot read %s or write the results file", casesPath.c_str());
            return false;
        }
        g_stopOnFail = !Env(prefix + "STOP_ON_FAIL").empty();
        g_checkpoints = !Env(prefix + "CHECKPOINTS").empty();
        if (g_game.start) g_game.start();
        Log("test: %zu case(s) from %s", g_cases.size(), casesPath.c_str());
        g_mode = Mode::kActive;
    }
    return g_mode == Mode::kActive;
}

void Finish() {
    std::string line = "{\"done\":true,\"passed\":" + std::to_string(g_passed) + ",\"failed\":" +
                       std::to_string(g_failed) + ",\"skipped\":" + std::to_string(g_cases.size() - g_next);
    if (g_game.realCommands) {
        const std::vector<std::string> uncovered = UncoveredCommands(g_cases, g_game.realCommands());
        WriteLine(line + ",\"uncovered\":" + JsonList(uncovered) + "}");
        Log("test: done, %d passed, %d failed, %zu real command(s) no case names", g_passed, g_failed,
            uncovered.size());
    } else {
        WriteLine(line + "}");
        Log("test: done, %d passed, %d failed", g_passed, g_failed);
    }
    g_out.close();
    g_mode = Mode::kFinished;
}

// Whether the harness has handled every line the runner waits on.
bool Acknowledged() {
    std::ifstream in(g_resultsPath + ".ack");
    int done = 0;
    return in >> done && done >= g_awaitedAck;
}

// A leg of a route: a walk or steer, or the `bye if open` that closes a greeting met on the way.
bool OnRoute(const TestStep& step) {
    return step.kind == Kind::kWalk || step.kind == Kind::kSteer ||
           (step.kind == Kind::kBye && step.text == "if open");
}

// A held game ends a route there, as a player stops to read a message; true when it did.
bool RouteCut(const TestCase& test) {
    if (!OnRoute(test.steps[g_case.step])) return false;
    if (g_case.ticksLeft >= 0 && g_game.walkStop) g_game.walkStop();
    g_case.ticksLeft = -1;
    while (g_case.step < test.steps.size() && OnRoute(test.steps[g_case.step])) ++g_case.step;
    return true;
}

// Whether `step` must wait for the world while a menu holds the game; an `until`
// already true does not (its verdict and left side go to `verdict`/`left`). A `run`
// whose harness request is out has run already: only its answer is left to read,
// and a menu its own command opened (`setlevel` -> the level-up dialog) must not
// hold it. Only a game without an in-game `run` hook asks the harness.
bool WaitsOnMenu(const TestCase& test, const TestStep& step, int* verdict, std::string* left) {
    const bool sent = step.kind == Kind::kRun && g_case.asked;
    const bool needsWorld = (step.kind == Kind::kRun && !sent) || step.kind == Kind::kActivate ||
                            step.kind == Kind::kUntil ||
                            step.kind == Kind::kWalk || step.kind == Kind::kSteer ||
                            (step.kind == Kind::kTalk && g_case.ticksLeft < 0);
    if (!needsWorld) return false;
    if (step.kind == Kind::kUntil) *verdict = Judge(test.actor, step.text, g_game.judge ? nullptr : left);
    return *verdict != 1;
}

// Steps the case on: after an action it yields the tick, so the game sees it.
void RunSteps(const TestCase& test, bool held) {
    while (g_case.step < test.steps.size() && g_case.error.empty()) {
        const TestStep& step = test.steps[g_case.step];
        if (held && !Talking() && RouteCut(test)) continue;
        int verdict = -1;
        std::string left = "null";
        if (held && !Talking() && WaitsOnMenu(test, step, &verdict, &left)) {
            if (g_case.heldTicks >= kNoticeTicks && g_game.dismissNotice && g_game.dismissNotice()) {
                g_case.heldTicks = 0;
                return;
            }
            // A sleep runs its hours in real time at 1x, far past what any other menu may hold.
            const int limit = MenuUp(kSleepWaitMenu) ? kHeldTicks * kSleepHeldFactor : kHeldTicks;
            if (++g_case.heldTicks > limit) g_case.error = HeldMenuError();
            return;
        }
        g_case.heldTicks = 0;
        if (verdict == 1 && !g_game.judge) {
            g_case.deadline = -1;
            Record(step.text, 1, left);
        } else if (!Advance(test, step)) {
            return;
        }
        ++g_case.step;
        if (IsAction(step.kind) || g_awaitedAck) return;
    }
}

// The start of case `test`: fresh progress, and its leading speed.
void StartCase(const TestCase& test) {
    g_case = Progress{};
    g_case.started = true;
    g_case.heardStart = g_heard.size();
    if (g_game.newIssues) g_game.newIssues();
    SetPace(LeadingPace(test));
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

const std::vector<std::string>& TestStepNames() {
    static const std::vector<std::string> kNames = {
        "run",  "wait",  "expect", "talk", "ask",     "choose",   "bye",   "button",   "activate",
        "until", "pick", "close",  "take", "console", "persuade", "sleep", "walk",     "follow",
        "defend", "punch", "rest", "steer", "approach", "pace", "hear", "unsaid", "said"};
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
        TestStep step{static_cast<Kind>(at - kinds.begin()), rest, std::max(0, std::atoi(rest.c_str()))};
        if (word == "until" || word == "punch" || word == "hear") {
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

// Logs the open kHoldingMenus each time that set changes, with the tick, so a run shows when a menu came or went.
void NoteMenus() {
    static std::string s_open;
    std::string open;
    for (const char* name : kHoldingMenus) {
        if (MenuReported(name)) open += (open.empty() ? "" : ", ") + std::string(name);
    }
    if (open == s_open) return;
    s_open = open;
    Log("test: tick %d menus open: %s", g_tick, open.empty() ? "none" : open.c_str());
}

void TickTestRunner() {
    if (!Active() || (g_game.inWorld && !g_game.inWorld()) || g_startTicks-- > 0) return;
    ++g_tick;
    NoteMenus();
    if (g_next >= g_cases.size()) {
        Finish();
        return;
    }
    const TestCase& test = g_cases[g_next];
    Listen(test);
    if (g_awaitedAck && !Acknowledged()) return;
    g_awaitedAck = 0;
    if (!g_case.started) StartCase(test);
    const bool held = g_game.held && g_game.held();
    // A player defending itself, or walking behind a guide, kills its attackers.
    if (!held && !Talking() && (g_case.defend || !g_case.follow.empty()) && g_game.killPlayerAttackers) {
        if (!g_case.ally.empty() && g_game.killAttackersOf) {
            g_game.killAttackersOf(g_case.ally);
        } else {
            g_game.killPlayerAttackers();
        }
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

}  // namespace tesruntime::test
