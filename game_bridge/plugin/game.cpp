#include "game.h"

#include <windows.h>

#include <cstdio>
#include <cstring>
#include <deque>
#include <mutex>
#include <vector>

#include "addresses.h"
#include "ids.h"
#include "game_types.h"
#include "log.h"
#include "main_thread.h"

namespace bridge {

// ---------------------------------------------------------------- console ----
//
// Execution strategy.
//
// The bridge runs console commands through the game's OWN console pipeline
// rather than reimplementing argument parsing on top of the ObScript table.
// Two reasons: every command's parser comes for free (including the ones with
// irregular argument grammars), and behaviour matches what the user sees when
// they type the same line.
//
// Output capture: console commands report through the console print path, which
// writes to the console log. We install a capture buffer around the call so
// `getpos`, `getav` etc. return their text instead of only painting the screen.

namespace {

std::mutex        g_consoleMutex;
std::string       g_captureBuffer;
bool              g_capturing = false;

// An ALWAYS-ON ring of recent console output, independent of g_capturing.
//
// Scoped capture alone can only see prints produced by a command the bridge
// itself issued. Anything the game prints on its own -- a command typed into
// the in-game console, or an engine message -- was silently dropped, so
// "console output is empty" could not be told apart from "the print hook is
// not firing". Keeping a bounded history makes console output readable at any
// time and makes that distinction decidable from Python (see `console_log`).
std::deque<std::string> g_consoleRing;
std::mutex              g_ringMutex;
// `sqv` on a real quest emits one line per alias -- 64 for CharacterGen alone,
// before the quest-state block that is usually the point. A small ring silently
// truncates exactly the interesting part, so this is sized for the biggest
// single command anyone runs, not for a typical one.
constexpr std::size_t   kConsoleRingMax = 4000;
// Monotonic count of every line ever captured. `total` cannot be used to tell
// how much a command produced once the ring is full (it stops growing), which
// makes output look empty when it is merely rotated -- a bug this exact ring
// caused within an hour of being added.
std::uint64_t           g_ringSeq = 0;

// Set by the console-print hook. Runs on the game's thread, so it must stay
// cheap and must never block on the pipe.
void CaptureLine(const char* text) {
    if (!text) return;
    {
        std::lock_guard<std::mutex> lk(g_ringMutex);
        if (g_consoleRing.size() >= kConsoleRingMax) g_consoleRing.pop_front();
        g_consoleRing.emplace_back(text);
        ++g_ringSeq;
    }
    if (!g_capturing) return;
    // Capped: a `coc` keeps the console's print mode on through the cell load,
    // and every condition the engine evaluates then echoes -- measured 10.5 MB
    // for one `coc QASmoke`. The command's own output comes first.
    constexpr std::size_t kCaptureMax = 64 * 1024;
    if (g_captureBuffer.size() >= kCaptureMax) return;
    g_captureBuffer += text;
    g_captureBuffer.push_back('\n');
    if (g_captureBuffer.size() >= kCaptureMax) g_captureBuffer += "[output truncated]\n";
}

}  // namespace

std::vector<std::string> ConsoleLogRecent(std::size_t limit) {
    std::lock_guard<std::mutex> lk(g_ringMutex);
    const std::size_t n = (limit && limit < g_consoleRing.size()) ? limit
                                                                 : g_consoleRing.size();
    return std::vector<std::string>(g_consoleRing.end() - static_cast<long>(n),
                                    g_consoleRing.end());
}

std::size_t ConsoleLogCount() {
    std::lock_guard<std::mutex> lk(g_ringMutex);
    return g_consoleRing.size();
}

std::uint64_t ConsoleLogSeq() {
    std::lock_guard<std::mutex> lk(g_ringMutex);
    return g_ringSeq;
}

void ConsoleCaptureAppend(const char* text) { CaptureLine(text); }

// Scoped capture around a single call, for callers that drive the executor
// themselves (script injection runs statement by statement and wants each
// statement's own answer, not one merged blob).
//
// The caller MUST already hold whatever serialisation applies -- these do not
// lock, because ScriptInject runs entirely inside one main-thread task and
// taking g_consoleMutex here would deadlock against RunConsoleCommand.
void ConsoleCaptureBegin() {
    g_captureBuffer.clear();
    g_capturing = true;
}

std::string ConsoleCaptureEnd() {
    g_capturing = false;
    return g_captureBuffer;
}

// The engine's own report that a command name did not resolve.
//
// The dispatcher's return value cannot be used for this: a successful `getgs`
// returns 0 while a misspelled command returns 1. This printed line is the only
// signal the engine gives, and without checking it every typo looks like a
// success -- which makes EVERY result untrustworthy, not just the bad one.
bool ConsoleOutputIndicatesUnknownCommand(const std::string& out) {
    return out.find("Script command \"") != std::string::npos &&
           out.find("\" not found") != std::string::npos;
}

ConsoleResult RunConsoleCommand(const std::string& command, std::uint32_t refFormId) {
    ConsoleResult out;

    if (!g_addr.compileAndRun) {
        out.error = "CompileAndRun unavailable on this runtime";
        return out;
    }
    if (command.empty()) {
        out.error = "empty command";
        return out;
    }

    std::lock_guard<std::mutex> lk(g_consoleMutex);

    g_captureBuffer.clear();
    g_capturing = true;

    // The actual invocation is filled in by console_exec.cpp, which owns the
    // ScriptBuffer/Script construction. Kept separate so the risky struct-layout
    // work is isolated from the rest of the plugin.
    extern bool ConsoleExecute(const std::string& cmd, std::uint32_t refFormId, std::string* err);

    std::string err;
    const bool ok = ConsoleExecute(command, refFormId, &err);

    g_capturing = false;
    out.ok = ok;
    out.output = g_captureBuffer;
    out.error = err;

    // A misspelled command runs, prints "not found", and would otherwise be
    // reported as a success -- so a caller could never trust ANY result.
    if (out.ok && ConsoleOutputIndicatesUnknownCommand(out.output)) {
        out.ok = false;
        out.error = "unknown console command";
    }
    return out;
}

// ----------------------------------------------------------------- state ----

std::uint32_t g_runtimeVersion = 0;
bool          g_gameLoaded = false;   // updated from SKSE messages

// 🛑 The SKSE PostLoadGame message is NOT sufficient on its own.
//
// `coc <cell>` from the main menu boots a playable session WITHOUT that message
// ever arriving, so the flag stays false while a player exists, has a position,
// and answers console commands. Reporting "main menu" then makes every caller
// skip the work it came to do -- and the bridge's whole point is that `coc`
// reaches a test cell without a save.
//
// So the flag is a fast path, and a successful console probe is the fallback:
// `player.getav health` prints only when a player actually exists.
namespace {

// The ids MorrowindRuntime uses (tes_runtime/morrowind/plugin/ids.h).
constexpr std::uint64_t kMenuManagerSlot = 400327;
constexpr std::uint64_t kIsMenuOpen = 82074;
constexpr std::uint64_t kFixedStringCtor = 69161;

// Whether the engine has "Main Menu" open; false when the ids do not resolve.
// The player object already exists at the main menu, so the console probe
// alone reported a loaded game there. Main thread only.
bool MainMenuOpen() {
    using IsMenuOpenFn = bool (*)(void* manager, void** name);
    using FixedStringFn = void* (*)(void* out, const char* text);
    static void* name = nullptr;
    const auto slot = reinterpret_cast<void**>(ResolveStableId(kMenuManagerSlot));
    const auto isOpen = reinterpret_cast<IsMenuOpenFn>(ResolveStableId(kIsMenuOpen));
    const auto intern = reinterpret_cast<FixedStringFn>(ResolveStableId(kFixedStringCtor));
    if (!slot || !*slot || !isOpen || !intern) return false;
    if (!name) intern(&name, "Main Menu");
    return name && isOpen(*slot, &name);
}

}  // namespace

bool IsGameLoaded() {
    if (g_gameLoaded) return true;
    if (!g_addr.consoleExecute) return false;

    // Cheap and read-only. Cached briefly so `status` polling cannot turn into
    // a console command per call.
    static DWORD lastCheck = 0;
    static bool  lastResult = false;
    const DWORD now = GetTickCount();
    if (lastResult && now - lastCheck < 2000) return true;
    if (now - lastCheck < 500) return lastResult;
    lastCheck = now;

    // 🛑 The probe runs on the MAIN thread. Its callers are on the pipe thread,
    // and the engine's Script constructor faults off the main thread (its base
    // constructor touches the game thread's TLS): every ping at the main menu
    // came back as an SEH-caught "unknown" error.
    extern bool ConsoleExecute(const std::string& cmd, std::uint32_t refFormId,
                               std::string* err);
    bool printed = false;
    RunOnMainThread([&] {
        if (MainMenuOpen()) return;
        g_captureBuffer.clear();
        g_capturing = true;
        std::string err;
        ConsoleExecute("player.getav health", 0, &err);
        g_capturing = false;
        printed = !g_captureBuffer.empty();
    }, 2000);
    lastResult = printed;
    return lastResult;
}
std::uint32_t RuntimeVersion() { return g_runtimeVersion; }

bool IsInLoadingScreen() {
    // Conservative: without a verified UI singleton we cannot read the loading
    // flag directly. Callers use wait_ready, which polls a cheap console probe
    // instead of trusting a guessed pointer.
    return false;
}

// ----------------------------------------------------------------- forms ----

std::uint32_t ResolveFormRef(const std::string& text) {
    if (text.empty()) return 0;
    if (text.size() > 2 && text[0] == '0' && (text[1] == 'x' || text[1] == 'X'))
        return static_cast<std::uint32_t>(std::strtoul(text.c_str() + 2, nullptr, 16));

    bool allHex = true;
    for (char c : text) {
        if (!std::isxdigit(static_cast<unsigned char>(c))) { allHex = false; break; }
    }
    if (allHex && text.size() >= 6)
        return static_cast<std::uint32_t>(std::strtoul(text.c_str(), nullptr, 16));

    return 0;  // editor-ID resolution goes through the console
}

}  // namespace bridge
