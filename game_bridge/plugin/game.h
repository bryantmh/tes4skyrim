// Thin wrappers over the engine functions the bridge drives.
//
// Everything here MUST be called on the main thread (see main_thread.h). The
// only exceptions are RuntimeVersion(), which reads a flag, and IsGameLoaded(),
// which marshals its own probe onto the main thread -- so it must NOT be called
// from a main-thread task (it would wait on itself until its timeout).
//
// No RVA is hardcoded. Each entry point is resolved once via versionlib stable
// ID with a signature fallback, and a failed resolve degrades to a reported
// E_UNSUPPORTED instead of a call through a null or wrong pointer.

#pragma once

#include <atomic>
#include <cstdint>
#include <string>
#include <vector>

namespace bridge {

// ---------------------------------------------------------------- state ----

bool IsGameLoaded();       // a save/new game is active (not sitting at the main menu)
bool IsInLoadingScreen();
std::uint32_t RuntimeVersion();

// ---------------------------------------------------------------- console ----

struct ConsoleResult {
    bool        ok = false;
    std::string output;   // captured console print output
    std::string error;
};

// Runs a console command line exactly as if typed, optionally with `ref`
// selected as the implicit "this" (the console's prid/selected reference).
//
// This uses the game's own console compiler, so we inherit its argument parsing
// for every command rather than reimplementing it per command.
ConsoleResult RunConsoleCommand(const std::string& command, std::uint32_t refFormId = 0);

// Console output capture (console_capture.cpp). Installs a detour on
// Console::Print so command output is returned over the pipe instead of only
// being drawn on screen. Optional: if it fails, commands still run.
bool InstallConsoleCapture(std::uintptr_t printAddr);
bool ConsoleCaptureInstalled();
long ConsoleCaptureHits();          // how many times the detour fired
// ConsoleExecute's arg1 is a live object the console builds in its own stack
// frame; it cannot be synthesized, and passing null makes commands COMPILE but
// never RUN (returning success all the while). So the game's own calls are
// watched and the pointer is remembered for reuse.
bool InstallExecContextCapture(std::uintptr_t execAddr);
bool ExecContextCaptured();
long ExecContextHits();
extern std::atomic<std::uintptr_t> g_execContext;
std::uintptr_t ConsoleCaptureTarget();  // the address actually hooked

// The console-mode THREAD-LOCAL flag (console_capture.cpp).
//
// Every printing ObScript handler gates its output on a TLS byte:
//     mov ecx, [g_tlsIndex]; mov rax, gs:[0x58]
//     cmp byte [ [rax+rcx*8] + offset ], 0 ; je skip-the-print
// The real console sets it while dispatching a typed command; a command run
// through ConsoleExecute without it executes fine but prints NOTHING -- which
// was the empty-capture symptom (proved live 2026-08-14: setting the byte took
// the print hook from 10 hits to 15,293).
//
// DiscoverConsoleModeFlag() finds the TLS slot index global and the in-block
// offset by scanning for that handler idiom and taking the operand consensus
// across all matches. ConsoleModeFlagPtr() returns the CALLING thread's flag
// byte -- only ever toggle it on the main thread, briefly, around a command
// (an all-thread version of this froze the game: background threads started
// printing through a main-thread-only path).
bool DiscoverConsoleModeFlag();
bool ConsoleModeFlagAvailable();

// Diagnostics for the last executed command: which thread ran it, and what the
// console-mode flag pointer resolved to on that thread. Reported by hookstats.
void RecordExecDiag(std::uint32_t threadId, std::uintptr_t flagPtr);
std::uint32_t  LastExecThreadId();
std::uintptr_t LastExecFlagPtr();
std::uint8_t* ConsoleModeFlagPtr();
std::uintptr_t ConsoleModeTlsIndexGlobal();  // for hookstats
std::uint32_t  ConsoleModeTlsOffset();
int            ConsoleModeSigMatches();

// Papyrus VM output capture (papyrus_capture.cpp). Hooks the logger sink every
// Papyrus message passes through, so an injected script's output can be read
// back exactly -- without racing the game's buffered writes to Papyrus.0.log.
bool InstallPapyrusCapture(std::uintptr_t logAddr);
bool PapyrusCaptureInstalled();
long PapyrusCaptureHits();
void PapyrusCaptureArm();                                  // start a slice
std::vector<std::string> PapyrusCaptureTake();             // end it, return lines
std::vector<std::string> PapyrusCaptureRecent(std::size_t limit);

// ------------------------------------------------------------ raw probes ----
//
// Generic primitives (rawmem.cpp) so a hypothesis about engine internals can
// be tested from Python against the LIVE process, without a rebuild + restart
// per idea. That round trip is the exact cost this bridge exists to remove.

std::uintptr_t ModuleBaseAddress();
bool RawRead(std::uintptr_t addr, std::size_t len, std::vector<std::uint8_t>* out);
bool RawReadString(std::uintptr_t addr, std::size_t maxLen, std::string* out);
bool RawWrite(std::uintptr_t addr, const std::vector<std::uint8_t>& bytes,
              std::string* err);
std::uintptr_t RawAlloc(std::size_t len);
bool RawCall(std::uintptr_t fn, std::uintptr_t a1, std::uintptr_t a2,
             std::uintptr_t a3, std::uintptr_t a4,
             std::uintptr_t* result, std::string* err);

// Up to 8 args, optional float args (bit N of floatMask) and a float return.
// Exists so hitting an unusual signature never means rebuilding the plugin.
bool RawCallEx(std::uintptr_t fn, const std::uint64_t* args, std::size_t argc,
               std::uint32_t floatMask, std::uint64_t* intResult,
               double* floatResult, std::string* err);

// Scoped console capture, for callers driving the executor directly. Does not
// lock: intended for use inside a single main-thread task.
void ConsoleCaptureBegin();
std::string ConsoleCaptureEnd();

// True if the engine printed its "Script command \"x\" not found." message.
// The dispatcher's return value does NOT report this (a good `getgs` returns 0,
// a typo returns 1), so this is the only way to fail an unknown command instead
// of silently reporting success.
bool ConsoleOutputIndicatesUnknownCommand(const std::string& out);

// Always-on history of console output, regardless of who caused it (the bridge
// or a human typing in-game). Scoped capture only sees the bridge's OWN
// commands, which makes "no output" ambiguous between "the hook never fired"
// and "the command printed nothing"; this ring settles that.
std::vector<std::string> ConsoleLogRecent(std::size_t limit);
std::size_t ConsoleLogCount();
// Monotonic total of every line ever captured. Use this, not the buffered
// count, to measure how much a command produced -- once the ring is full the
// buffered count stops growing and real output looks like none at all.
std::uint64_t ConsoleLogSeq();

// One statement of an injected script, with whatever it printed.
struct InjectedStatement {
    std::string text;
    bool        ok = false;
    std::string output;   // console output captured for THIS statement
    std::string error;
};

// Locates the console's compile-AND-run dispatcher (console_exec.cpp).
//
// 🛑 Without it, commands COMPILE but never RUN: the engine returns success,
// prints nothing, and changes nothing. Call once at startup.
bool InitConsoleDispatch();
std::uintptr_t ConsoleDispatchAddr();

// Compiles and runs a multi-statement script body (console_exec.cpp).
// `statements` receives one entry per executed statement, so a probe can read
// each answer rather than only learning whether the whole body succeeded.
bool ScriptInject(const std::string& body, std::uint32_t refFormId,
                  bool stopOnError, int* failedIndex, std::string* err,
                  std::vector<InjectedStatement>* statements = nullptr);

// ------------------------------------------------------------------ forms ----

struct FormInfo {
    bool          found = false;
    std::uint32_t formId = 0;
    std::string   editorId;
    std::string   name;
    std::string   type;       // 4-char record signature, e.g. "NPC_"
    std::uint8_t  formType = 0;
};

FormInfo LookupByFormId(std::uint32_t formId);
FormInfo LookupByEditorId(const std::string& editorId);

// Resolve "0x1234", "1234", or an editor ID to a form id. 0 on failure.
std::uint32_t ResolveFormRef(const std::string& text);

// --------------------------------------------------------------- player ----

struct PlayerState {
    bool        valid = false;
    float       x = 0, y = 0, z = 0;
    float       angleZ = 0;
    std::uint32_t cellFormId = 0;
    std::string cellEditorId;
    std::string worldspace;
};

PlayerState GetPlayerState();

// ------------------------------------------------------------- readback ----

struct BoneXform {
    std::string name;
    float world[3]{};
    float rot[9]{};
};

// What the engine actually built for a reference. This is the payload the whole
// bridge exists for: `hasRagdoll=false` / `rigidBodies=0` on an actor that looks
// correct on screen is the silent-binding signature that offline validators miss.
struct RagdollInfo {
    bool has3D = false;
    bool graphBound = false;
    bool hasRagdoll = false;
    int  rigidBodies = 0;
    int  constraints = 0;
    bool inWorld = false;      // ragdoll actually raised into the physics world
    std::string skeletonPath;
    std::vector<BoneXform> bones;
    std::string note;
};

RagdollInfo ProbeRagdoll(std::uint32_t refFormId, bool includeBones);

struct AnimInfo {
    bool        has3D = false;
    bool        graphBound = false;
    std::string projectName;
    std::string activeClip;
    std::string stateMachineNode;
    int         clipCount = 0;
    std::vector<std::string> clips;
    std::string note;
};

AnimInfo ProbeAnim(std::uint32_t refFormId);

// ------------------------------------------------------------ hot reload ----

struct ReloadResult {
    bool ok = false;
    int  reloaded = 0;
    int  refreshedRefs = 0;
    std::string note;
};

// Drops a cached model so the next load re-reads it from disk, then refreshes
// 3D on loaded references using it.
ReloadResult ReloadModel(const std::string& relativePath);

// Forces a reference to rebuild its 3D from the (possibly just-reloaded) model.
ReloadResult Refresh3D(std::uint32_t refFormId);

}  // namespace bridge
