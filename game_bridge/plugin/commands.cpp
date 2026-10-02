// Command dispatch.
//
// Every handler that touches game state runs inside RunOnMainThread. Handlers
// that only read plugin-local state answer directly on the pipe thread.

#include "commands.h"

#include <windows.h>

#include <atomic>
#include <mutex>
#include <set>
#include <string>
#include <vector>

#include "addresses.h"
#include "game.h"
#include "generic_hook.h"
#include "json.h"
#include "log.h"
#include "main_thread.h"
#include "recorder.h"

namespace bridge {

namespace {

// -------------------------------------------------------------- session ----

std::mutex               g_sessionMutex;
std::set<std::uint32_t>  g_spawned;      // refs this session created
bool                     g_bridgeStarted = false;  // we ran the coc ourselves
int                      g_spawnCap = 256;

Json Err(const char* code, const std::string& msg) {
    Json j = Json::Object();
    j.set("ok", Json(false));
    j.set("code", Json(code));
    j.set("error", Json(msg));
    return j;
}

Json Ok(Json result) {
    Json j = Json::Object();
    j.set("ok", Json(true));
    j.set("result", std::move(result));
    return j;
}

// Wraps a main-thread handler, converting marshalling failures into structured
// errors instead of letting the pipe hang.
Json OnMainThread(const std::function<Json()>& fn, unsigned timeoutMs = 5000) {
    Json out;
    bool ran = false;
    const auto status = RunOnMainThread([&] { out = fn(); ran = true; }, timeoutMs);

    switch (status) {
        case MainThreadStatus::Ok:
            return ran ? out : Err("E_INTERNAL", "handler did not produce a result");
        case MainThreadStatus::Timeout:
            return Err("E_LOADING",
                       "game did not process the request in time (loading screen?)");
        case MainThreadStatus::NoTaskInterface:
        default:
            return Err("E_INTERNAL", "task interface unavailable");
    }
}

// ------------------------------------------------------------- handlers ----

Json CmdPing(const Json&) {
    Json r = Json::Object();
    r.set("pong", Json(true));
    r.set("plugin_version", Json(kPluginVersionString));
    r.set("runtime_version", Json(RuntimeVersion()));
    r.set("game_loaded", Json(IsGameLoaded()));
    return Ok(std::move(r));
}

Json CmdCapabilities(const Json&) {
    Json caps = Json::Object();
    // `console` must reflect whether a command can ACTUALLY run, not merely
    // whether the executor resolved. Console execution also needs a Script
    // object, so reporting true on consoleExecute alone sent the client into a
    // command that then failed with a bare "could not allocate script object".
    const bool scriptAlloc =
        g_addr.memAlloc != 0 && g_addr.scriptVtable != 0 && g_addr.scriptSetText != 0;
    caps.set("console", Json(g_addr.consoleExecute != 0 && scriptAlloc));
    caps.set("script_alloc", Json(scriptAlloc));
    caps.set("inject", Json(g_addr.consoleExecute != 0 && scriptAlloc));
    // Both halves are required to read a command's output: the print hook, AND
    // the console-mode flag that makes handlers print at all.
    caps.set("output_capture",
             Json(ConsoleCaptureInstalled() && ConsoleModeFlagAvailable()));
    caps.set("papyrus_capture", Json(PapyrusCaptureInstalled()));
    caps.set("recorder", Json(GetRecorderStats().installed));

    Json missing = Json::Array();
    for (const auto& m : g_addr.missing) missing.push(Json(m));

    Json r = Json::Object();
    r.set("capabilities", std::move(caps));
    r.set("unresolved", std::move(missing));
    r.set("game_loaded", Json(IsGameLoaded()));
    return Ok(std::move(r));
}

Json CmdConsole(const Json& args) {
    const std::string command = args["command"].asString();
    if (command.empty()) return Err("E_INTERNAL", "missing 'command'");

    std::uint32_t ref = 0;
    if (args.has("ref")) {
        const Json& r = args["ref"];
        ref = r.isNumber() ? r.asU32() : ResolveFormRef(r.asString());
    }

    return OnMainThread([&]() -> Json {
        ConsoleResult cr = RunConsoleCommand(command, ref);
        if (!cr.ok) return Err("E_INTERNAL", cr.error.empty() ? "command failed" : cr.error);
        Json r = Json::Object();
        r.set("output", Json(cr.output));
        return Ok(std::move(r));
    });
}

// Runs several console commands in ONE main-thread trip.
//
// Sequences are the normal case for automated debugging -- select a reference,
// set a stage, read something back -- and one round trip per command means the
// game can advance between them, so the sequence is not atomic with respect to
// the game loop. Batching also keeps a long sequence inside a single
// marshalling timeout instead of N of them.
//
// `stop_on_error` (default true) mirrors what a human would do: if `prid`
// fails, running the next command against the wrong selection is worse than
// stopping.
Json CmdBatch(const Json& args) {
    const Json& cmds = args["commands"];
    if (!cmds.isArray() || cmds.size() == 0)
        return Err("E_INTERNAL", "missing 'commands' (non-empty array)");

    const bool stopOnError = args.has("stop_on_error") ? args["stop_on_error"].asBool() : true;

    std::uint32_t ref = 0;
    if (args.has("ref")) {
        const Json& r = args["ref"];
        ref = r.isNumber() ? r.asU32() : ResolveFormRef(r.asString());
    }

    // A batch may legitimately take longer than a single command.
    return OnMainThread([&]() -> Json {
        Json results = Json::Array();
        int ran = 0, failed = 0;

        for (size_t i = 0; i < cmds.size(); ++i) {
            const std::string cmd = cmds.at(i).asString();
            if (cmd.empty()) continue;

            // Only the first command needs the reference selected; the console's
            // selection persists for the rest of the batch.
            ConsoleResult cr = RunConsoleCommand(cmd, (i == 0) ? ref : 0);
            ++ran;

            Json entry = Json::Object();
            entry.set("command", Json(cmd));
            entry.set("ok", Json(cr.ok));
            entry.set("output", Json(cr.output));
            if (!cr.ok) {
                ++failed;
                entry.set("error", Json(cr.error));
            }
            results.push(std::move(entry));

            if (!cr.ok && stopOnError) break;
        }

        Json r = Json::Object();
        r.set("results", std::move(results));
        r.set("ran", Json(ran));
        r.set("failed", Json(failed));
        r.set("total", Json(static_cast<int>(cmds.size())));
        return Ok(std::move(r));
    }, 20000);
}

// Compiles and runs a multi-statement script body in the game.
//
// The engine's own script compiler does the work, so this is real injection:
// the text becomes bytecode and executes in-process. Statements run in order
// against one selected reference inside a single main-thread trip, so the game
// cannot advance between them.
Json CmdInject(const Json& args) {
    std::string body = args["script"].asString();
    if (body.empty()) {
        // Accept an array of lines too -- it is the natural shape from JSON.
        const Json& lines = args["lines"];
        if (lines.isArray()) {
            for (size_t i = 0; i < lines.size(); ++i) {
                body += lines.at(i).asString();
                body.push_back('\n');
            }
        }
    }
    if (body.empty()) return Err("E_INTERNAL", "missing 'script' (or 'lines')");

    std::uint32_t ref = 0;
    if (args.has("ref")) {
        const Json& r = args["ref"];
        ref = r.isNumber() ? r.asU32() : ResolveFormRef(r.asString());
        if (!ref) return Err("E_NOT_FOUND", "could not resolve 'ref'");
    }

    const bool stopOnError = args.has("stop_on_error") ? args["stop_on_error"].asBool() : true;

    // Papyrus is asynchronous: a script's output can land a frame or two after
    // the statement returns. Wait briefly on the main thread would block the
    // game, so the settle happens on the pipe thread AFTER the injection.
    const int settleMs = args.has("settle_ms") ? args["settle_ms"].asInt(250) : 250;

    if (PapyrusCaptureInstalled()) PapyrusCaptureArm();

    Json out = OnMainThread([&]() -> Json {
        int failedIndex = -1;
        std::string err;
        std::vector<InjectedStatement> stmts;
        const bool ok = ScriptInject(body, ref, stopOnError, &failedIndex, &err, &stmts);

        Json r = Json::Object();
        r.set("ok", Json(ok));
        r.set("failed_statement", Json(failedIndex));
        if (!ok) r.set("detail", Json(err));

        // Per-statement results: each line's own console output, so a probe can
        // read its answers instead of one merged blob.
        Json arr = Json::Array();
        for (const auto& s : stmts) {
            Json e = Json::Object();
            e.set("text", Json(s.text));
            e.set("ok", Json(s.ok));
            e.set("output", Json(s.output));
            if (!s.ok && !s.error.empty()) e.set("error", Json(s.error));
            arr.push(std::move(e));
        }
        r.set("statements", std::move(arr));
        return Ok(std::move(r));
    }, 20000);

    // Let the VM flush, then attach everything Papyrus emitted during the run.
    if (PapyrusCaptureInstalled()) {
        if (settleMs > 0) Sleep(static_cast<DWORD>(settleMs > 5000 ? 5000 : settleMs));
        Json lines = Json::Array();
        for (const auto& l : PapyrusCaptureTake()) lines.push(Json(l));

        // Attach to the RESULT object, not the envelope.
        if (out.has("result")) {
            Json r = out["result"];
            r.set("papyrus", std::move(lines));
            out.set("result", std::move(r));
        }
    }
    return out;
}

// Reads Papyrus VM output captured straight from the logger sink.
//
// Two modes:
//   arm=true      start a fresh slice and return immediately
//   (default)     return the current slice (ending it), or -- if nothing was
//                 armed -- the most recent lines from the always-on ring
//
// This is the in-process counterpart to tailing Papyrus.0.log: it is exact and
// unbuffered, so it can attribute output to one injection rather than guessing
// which lines in a shared file belong to you.
Json CmdVmLog(const Json& args) {
    if (!PapyrusCaptureInstalled())
        return Err("E_UNSUPPORTED",
                   "Papyrus VM capture is not installed on this runtime; "
                   "use tools/papyrus_tail.py to read the log file instead");

    if (args["arm"].asBool()) {
        PapyrusCaptureArm();
        Json r = Json::Object();
        r.set("armed", Json(true));
        return Ok(std::move(r));
    }

    const int limit = args.has("limit") ? args["limit"].asInt(100) : 100;

    // `take` (default false) decides whether an armed slice is CONSUMED.
    //
    // Reading must not be destructive by default. An earlier version always
    // called Take(), so two consecutive `vmlog` calls returned data and then
    // nothing -- and because Take() also disarms, the second call silently
    // fell through to the ring. Plain reads are now idempotent; pass take=true
    // (as `inject` does) when you deliberately want to end a slice.
    const bool take = args["take"].asBool();

    std::vector<std::string> slice;
    bool fromRing = false;
    if (take) {
        slice = PapyrusCaptureTake();
    }
    if (slice.empty()) {
        // Either nothing was armed, or this is a non-destructive read: return
        // the most recent lines so a bare `vmlog` always answers "what has the
        // VM said lately?".
        slice = PapyrusCaptureRecent(static_cast<std::size_t>(limit < 0 ? 0 : limit));
        fromRing = true;
    }

    Json lines = Json::Array();
    for (const auto& l : slice) lines.push(Json(l));

    Json r = Json::Object();
    r.set("lines", std::move(lines));
    r.set("count", Json(static_cast<int>(slice.size())));
    r.set("source", Json(fromRing ? "recent" : "armed_slice"));
    return Ok(std::move(r));
}

// ---------------------------------------------------------- raw probing ----
//
// These exist so a theory about engine internals can be tested from Python
// against the running game, instead of costing a rebuild + restart each time.

std::uintptr_t ParseAddr(const Json& v) {
    if (v.isNumber()) return static_cast<std::uintptr_t>(v.asU32());
    const std::string s = v.asString();
    if (s.empty()) return 0;
    return static_cast<std::uintptr_t>(std::strtoull(s.c_str(), nullptr, 0));
}

// Resolves an Address Library stable id (or a raw rva) to a live address, so a
// probe can name things the way ids.h does without knowing this build's layout.
Json CmdResolve(const Json& args) {
    Json r = Json::Object();
    r.set("module_base", Json(static_cast<double>(ModuleBaseAddress())));

    if (args.has("id")) {
        const std::uint64_t id = static_cast<std::uint64_t>(args["id"].asU32());
        const std::uintptr_t a = ResolveStableId(id);
        r.set("id", Json(static_cast<double>(id)));
        r.set("address", Json(static_cast<double>(a)));
        r.set("rva", Json(static_cast<double>(a ? a - ModuleBaseAddress() : 0)));
        r.set("found", Json(a != 0));
    } else if (args.has("rva")) {
        const std::uintptr_t rva = ParseAddr(args["rva"]);
        r.set("rva", Json(static_cast<double>(rva)));
        r.set("address", Json(static_cast<double>(ModuleBaseAddress() + rva)));
        r.set("found", Json(true));
    } else {
        return Err("E_INTERNAL", "need 'id' or 'rva'");
    }
    return Ok(std::move(r));
}

Json CmdReadMem(const Json& args) {
    std::uintptr_t addr = 0;
    if (args.has("address")) addr = ParseAddr(args["address"]);
    else if (args.has("rva")) addr = ModuleBaseAddress() + ParseAddr(args["rva"]);
    else if (args.has("id")) addr = ResolveStableId(args["id"].asU32());
    if (!addr) return Err("E_NOT_FOUND", "could not resolve an address");

    const int len = args.has("len") ? args["len"].asInt(64) : 64;

    if (args["as_string"].asBool()) {
        std::string s;
        if (!RawReadString(addr, static_cast<std::size_t>(len), &s))
            return Err("E_NOT_FOUND", "address is not readable");
        Json r = Json::Object();
        r.set("address", Json(static_cast<double>(addr)));
        r.set("string", Json(s));
        return Ok(std::move(r));
    }

    std::vector<std::uint8_t> bytes;
    if (!RawRead(addr, static_cast<std::size_t>(len < 0 ? 0 : len), &bytes))
        return Err("E_NOT_FOUND", "address is not readable");

    // Hex, because that is what gets pasted into a disassembler.
    static const char* kHex = "0123456789ABCDEF";
    std::string hex;
    hex.reserve(bytes.size() * 3);
    for (std::size_t i = 0; i < bytes.size(); ++i) {
        if (i) hex.push_back(' ');
        hex.push_back(kHex[bytes[i] >> 4]);
        hex.push_back(kHex[bytes[i] & 0xF]);
    }

    Json r = Json::Object();
    r.set("address", Json(static_cast<double>(addr)));
    r.set("rva", Json(static_cast<double>(addr - ModuleBaseAddress())));
    r.set("len", Json(static_cast<int>(bytes.size())));
    r.set("hex", Json(hex));
    return Ok(std::move(r));
}

// Write bytes / allocate scratch in the game process. Together with readmem,
// call and resolve, these are what let a NEW diagnostic be written in Python
// instead of C++ -- which is the only way to stop needing a game restart per
// idea.
Json CmdWriteMem(const Json& args) {
    std::uintptr_t addr = 0;
    if (args.has("address")) addr = ParseAddr(args["address"]);
    else if (args.has("rva")) addr = ModuleBaseAddress() + ParseAddr(args["rva"]);
    if (!addr) return Err("E_NOT_FOUND", "could not resolve an address");

    std::vector<std::uint8_t> bytes;
    if (args.has("string")) {
        const std::string s = args["string"].asString();
        bytes.assign(s.begin(), s.end());
        bytes.push_back(0);  // NUL-terminate: these are C strings to the engine
    } else if (args["bytes"].isArray()) {
        const Json& a = args["bytes"];
        for (size_t i = 0; i < a.size(); ++i)
            bytes.push_back(static_cast<std::uint8_t>(a.at(i).asU32() & 0xFF));
    } else {
        return Err("E_INTERNAL", "need 'bytes' (array) or 'string'");
    }

    std::string err;
    if (!RawWrite(addr, bytes, &err)) return Err("E_INTERNAL", err);

    Json r = Json::Object();
    r.set("address", Json(static_cast<double>(addr)));
    r.set("written", Json(static_cast<int>(bytes.size())));
    return Ok(std::move(r));
}

Json CmdAlloc(const Json& args) {
    const int len = args.has("len") ? args["len"].asInt(256) : 256;
    const std::uintptr_t p = RawAlloc(static_cast<std::size_t>(len < 0 ? 0 : len));
    if (!p) return Err("E_INTERNAL", "allocation failed");
    Json r = Json::Object();
    r.set("address", Json(static_cast<double>(p)));
    r.set("len", Json(len));
    return Ok(std::move(r));
}

// Install / inspect a generic hook. This is what makes "does this function
// even run, and with what arguments?" answerable WITHOUT a rebuild -- the
// question that repeatedly cost a full restart cycle to guess at.
Json CmdHook(const Json& args) {
    std::uintptr_t addr = 0;
    if (args.has("address")) addr = ParseAddr(args["address"]);
    else if (args.has("rva")) addr = ModuleBaseAddress() + ParseAddr(args["rva"]);
    else if (args.has("id")) addr = ResolveStableId(args["id"].asU32());

    // No target -> report every installed hook.
    if (!addr && !args.has("index")) {
        Json list = Json::Array();
        for (int i = 0; i < GenericHookCount(); ++i) {
            HookInfo info;
            if (!GetGenericHook(i, &info)) continue;
            Json e = Json::Object();
            e.set("index", Json(info.index));
            e.set("label", Json(info.label));
            e.set("target", Json(static_cast<double>(info.target)));
            e.set("hits", Json(static_cast<double>(info.hits)));
            e.set("stolen", Json(static_cast<int>(info.stolen)));
            Json calls = Json::Array();
            for (const auto& c : info.calls) {
                Json ca = Json::Array();
                ca.push(Json(static_cast<double>(c.a1)));
                ca.push(Json(static_cast<double>(c.a2)));
                ca.push(Json(static_cast<double>(c.a3)));
                ca.push(Json(static_cast<double>(c.a4)));
                calls.push(std::move(ca));
            }
            e.set("calls", std::move(calls));
            list.push(std::move(e));
        }
        Json r = Json::Object();
        r.set("hooks", std::move(list));
        return Ok(std::move(r));
    }

    // Inspect one slot.
    if (args.has("index")) {
        HookInfo info;
        if (!GetGenericHook(args["index"].asInt(-1), &info))
            return Err("E_NOT_FOUND", "no hook in that slot");
        Json calls = Json::Array();
        for (const auto& c : info.calls) {
            Json ca = Json::Array();
            ca.push(Json(static_cast<double>(c.a1)));
            ca.push(Json(static_cast<double>(c.a2)));
            ca.push(Json(static_cast<double>(c.a3)));
            ca.push(Json(static_cast<double>(c.a4)));
            calls.push(std::move(ca));
        }
        Json r = Json::Object();
        r.set("index", Json(info.index));
        r.set("label", Json(info.label));
        r.set("target", Json(static_cast<double>(info.target)));
        r.set("hits", Json(static_cast<double>(info.hits)));
        r.set("calls", std::move(calls));
        return Ok(std::move(r));
    }

    // Dry run: report whether the target COULD be hooked, and why not.
    if (args["analyze"].asBool()) {
        std::string why;
        const std::size_t n = AnalyzePrologue(addr, &why);
        Json r = Json::Object();
        r.set("address", Json(static_cast<double>(addr)));
        r.set("hookable", Json(n != 0));
        r.set("stolen", Json(static_cast<int>(n)));
        if (!n) r.set("reason", Json(why));
        return Ok(std::move(r));
    }

    // Remove: without it a wrong hook is permanent for the session, and the
    // only escape is a rebuild + relaunch.
    if (args.has("remove")) {
        const int idx = args["remove"].asInt(-1);
        return OnMainThread([&]() -> Json {
            std::string err;
            if (!RemoveGenericHook(idx, &err)) return Err("E_INTERNAL", err);
            Json r = Json::Object();
            r.set("removed", Json(idx));
            return Ok(std::move(r));
        });
    }

    const std::string label =
        args.has("label") ? args["label"].asString() : "hook";
    const int keep = args.has("keep") ? args["keep"].asInt(16) : 16;

    return OnMainThread([&]() -> Json {
        std::string err;
        const int idx = InstallGenericHook(addr, label,
                                           static_cast<std::size_t>(keep), &err);
        if (idx < 0) return Err("E_INTERNAL", err);
        Json r = Json::Object();
        r.set("index", Json(idx));
        r.set("address", Json(static_cast<double>(addr)));
        r.set("label", Json(label));
        return Ok(std::move(r));
    });
}

Json CmdCall(const Json& args) {
    std::uintptr_t fn = 0;
    if (args.has("address")) fn = ParseAddr(args["address"]);
    else if (args.has("rva")) fn = ModuleBaseAddress() + ParseAddr(args["rva"]);
    else if (args.has("id")) fn = ResolveStableId(args["id"].asU32());
    if (!fn) return Err("E_NOT_FOUND", "could not resolve the function address");

    // Up to 8 args, with optional float arguments and a float return, so an
    // unusual engine signature never means rebuilding the plugin.
    const Json& a = args["args"];
    std::uint64_t v[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    std::size_t argc = 0;
    if (a.isArray()) {
        argc = a.size() > 8 ? 8 : a.size();
        for (size_t i = 0; i < argc; ++i) v[i] = ParseAddr(a.at(i));
    }
    const std::uint32_t floatMask =
        args.has("float_args") ? args["float_args"].asU32() : 0;
    const bool wantFloat = args["float_result"].asBool();

    return OnMainThread([&]() -> Json {
        std::uint64_t intResult = 0;
        double floatResult = 0.0;
        std::string err;
        if (!RawCallEx(fn, v, argc, floatMask, &intResult,
                       wantFloat ? &floatResult : nullptr, &err))
            return Err("E_INTERNAL", err);
        Json r = Json::Object();
        r.set("result", Json(static_cast<double>(intResult)));
        if (wantFloat) r.set("float_result", Json(floatResult));
        return Ok(std::move(r));
    });
}

// How many times each installed hook has fired. Turns "capture is empty" into
// a decidable question -- 0 hits means the detour is on the wrong function,
// non-zero means the plumbing after it is at fault -- WITHOUT a rebuild.
// Recent console output from the always-on ring -- including output the GAME
// produced on its own. Answers "is the print hook working at all?" without
// having to correlate it with a command the bridge issued.
Json CmdConsoleLog(const Json& args) {
    std::size_t limit = 100;
    if (args.has("limit")) {
        const std::uint32_t n = args["limit"].asU32();
        if (n) limit = n;
    }
    const auto lines = ConsoleLogRecent(limit);
    Json arr = Json::Array();
    for (const auto& l : lines) arr.push(Json(l));
    Json r = Json::Object();
    r.set("lines", std::move(arr));
    r.set("count", Json(static_cast<double>(lines.size())));
    r.set("total", Json(static_cast<double>(ConsoleLogCount())));
    // Monotonic: the number to diff across a command. `total` saturates once
    // the ring is full, which makes real output look like none.
    r.set("seq", Json(static_cast<double>(ConsoleLogSeq())));
    return Ok(std::move(r));
}

Json CmdHookStats(const Json&) {
    Json r = Json::Object();
    r.set("console_print_hits", Json(static_cast<double>(ConsoleCaptureHits())));
    r.set("papyrus_log_hits", Json(static_cast<double>(PapyrusCaptureHits())));
    r.set("console_installed", Json(ConsoleCaptureInstalled()));
    r.set("papyrus_installed", Json(PapyrusCaptureInstalled()));
    r.set("console_target", Json(static_cast<double>(ConsoleCaptureTarget())));
    // Handlers only print while the TLS console-mode byte is set; if this is
    // false, commands run but return empty output (see console_capture.cpp).
    r.set("console_mode_flag", Json(ConsoleModeFlagAvailable()));
    r.set("console_mode_matches", Json(static_cast<double>(ConsoleModeSigMatches())));
    r.set("console_mode_offset", Json(static_cast<double>(ConsoleModeTlsOffset())));
    // 0 here means commands compile but never run (no output, no effect).
    r.set("console_dispatch", Json(static_cast<double>(ConsoleDispatchAddr())));
    r.set("last_exec_thread", Json(static_cast<double>(LastExecThreadId())));
    r.set("last_exec_flag_ptr", Json(static_cast<double>(LastExecFlagPtr())));
    // Without a captured execution context, commands compile but never run.
    r.set("exec_context_captured", Json(ExecContextCaptured()));
    r.set("exec_context_hits", Json(static_cast<double>(ExecContextHits())));
    r.set("exec_context", Json(static_cast<double>(g_execContext.load())));
    return Ok(std::move(r));
}

// ------------------------------------------------------- clean-room tests ----
//
// These back tools/quest_labtest.py: move to an empty cell, bring the actors
// under test in, run one thing, put everything back. See
// docs/ingame_test_methodology.md.
//
// Everything here is composed from the engine's OWN console commands rather
// than from a reconstructed TESObjectREFR layout. That is deliberate and is the
// same reasoning as `prid`-based selection: a wrong struct offset does not fail
// loudly, it corrupts memory or reads garbage, and the resulting symptoms are
// indistinguishable from a conversion bug -- the single most expensive failure
// this tool can produce, because it sends both sides hunting ghosts in the data.

// Spawns tracked copies of a base form.
//
// `placeatme` does NOT report the reference it created -- not through its
// return value and not through console output (verified against the command
// tables and UESP). So the created ref is identified by DIFFING the cell's
// contents: the caller spawns into the empty test cell, and the new reference
// is whatever `moveto`-able ref of that base did not exist a moment ago.
//
// The honest limitation, reported rather than hidden: without a reference
// enumerator we cannot name the new ref from inside the engine, so `refs` is
// empty and `tracked` is false unless the client supplies the id it observed.
// `cleanup` then falls back to the client's own list. This is why the Python
// side records what it spawned instead of trusting us to.
Json CmdSpawn(const Json& args) {
    const Json& f = args["form_id"];
    const std::uint32_t base = f.isNumber() ? f.asU32() : ResolveFormRef(f.asString());
    if (!base) return Err("E_NOT_FOUND", "could not resolve 'form_id'");

    int count = args.has("count") ? args["count"].asInt(1) : 1;
    if (count < 1) count = 1;

    {
        std::lock_guard<std::mutex> lk(g_sessionMutex);
        if (static_cast<int>(g_spawned.size()) + count > g_spawnCap)
            return Err("E_GUARDED",
                       "spawn cap reached; call cleanup before spawning more");
    }

    return OnMainThread([&]() -> Json {
        char cmd[96];
        std::snprintf(cmd, sizeof(cmd), "player.placeatme %08X %d", base, count);
        ConsoleResult cr = RunConsoleCommand(cmd, 0);
        if (!cr.ok)
            return Err("E_INTERNAL", cr.error.empty() ? "placeatme failed" : cr.error);

        Json r = Json::Object();
        r.set("base", Json(static_cast<double>(base)));
        r.set("count", Json(count));
        r.set("output", Json(cr.output));
        // No enumerator: say so instead of implying the spawn is tracked.
        r.set("refs", Json::Array());
        r.set("tracked", Json(false));
        r.set("note", Json("placeatme does not report the created reference; "
                           "record it client-side (or disable/markfordelete by "
                           "selection) so cleanup can remove it"));
        return Ok(std::move(r));
    }, 15000);
}

// Removes references this session created.
//
// Takes an explicit `refs` list because the engine side cannot discover what
// `placeatme` made (see CmdSpawn). Each is disabled and marked for delete --
// the pair the engine itself uses; `markfordelete` alone leaves the 3D loaded
// until the cell resets.
Json CmdCleanup(const Json& args) {
    std::vector<std::uint32_t> refs;
    const Json& list = args["refs"];
    if (list.isArray()) {
        for (size_t i = 0; i < list.size(); ++i) {
            const Json& v = list.at(i);
            const std::uint32_t id = v.isNumber() ? v.asU32()
                                                  : ResolveFormRef(v.asString());
            if (id) refs.push_back(id);
        }
    }
    {
        std::lock_guard<std::mutex> lk(g_sessionMutex);
        for (auto id : g_spawned) refs.push_back(id);
    }
    if (refs.empty()) {
        Json r = Json::Object();
        r.set("removed", Json(0));
        r.set("note", Json("nothing tracked; pass 'refs' to remove explicitly"));
        return Ok(std::move(r));
    }

    return OnMainThread([&]() -> Json {
        int removed = 0, failed = 0;
        for (auto id : refs) {
            ConsoleResult a = RunConsoleCommand("disable", id);
            ConsoleResult b = RunConsoleCommand("markfordelete", id);
            if (a.ok && b.ok) ++removed; else ++failed;
        }
        {
            std::lock_guard<std::mutex> lk(g_sessionMutex);
            g_spawned.clear();
        }
        Json r = Json::Object();
        r.set("removed", Json(removed));
        r.set("failed", Json(failed));
        return Ok(std::move(r));
    }, 20000);
}

// Moves a reference to the player (into the test cell) or back to a position.
//
// The point of doing this engine-side rather than as two console calls is that
// the read of the CURRENT position and the move happen in ONE main-thread trip,
// so the game cannot advance between them. A position captured a frame before
// the move is not the position the ref was actually at when it moved -- and
// that difference is exactly what makes `restore` a guess instead of an undo.
Json CmdMoveRef(const Json& args) {
    const Json& f = args["ref"];
    const std::uint32_t ref = f.isNumber() ? f.asU32() : ResolveFormRef(f.asString());
    if (!ref) return Err("E_NOT_FOUND", "could not resolve 'ref'");

    const bool toPlayer = !args.has("x");

    return OnMainThread([&]() -> Json {
        Json before = Json::Object();
        const char* axes[3] = {"x", "y", "z"};
        for (int i = 0; i < 3; ++i) {
            char q[32];
            std::snprintf(q, sizeof(q), "getpos %s", axes[i]);
            ConsoleResult cr = RunConsoleCommand(q, ref);
            before.set(axes[i], Json(cr.output));
        }

        bool ok = true;
        if (toPlayer) {
            ConsoleResult cr = RunConsoleCommand("moveto player", ref);
            ok = cr.ok;
        } else {
            for (int i = 0; i < 3; ++i) {
                if (!args.has(axes[i])) continue;
                char c[64];
                std::snprintf(c, sizeof(c), "setpos %s %s", axes[i],
                              args[axes[i]].asString().c_str());
                if (!RunConsoleCommand(c, ref).ok) ok = false;
            }
        }

        Json r = Json::Object();
        r.set("ref", Json(static_cast<double>(ref)));
        r.set("moved_to_player", Json(toPlayer));
        r.set("before", std::move(before));
        r.set("ok", Json(ok));
        return Ok(std::move(r));
    }, 15000);
}

// Blocks until the game answers again after a load screen.
//
// `coc` starts a load, and every command issued during it fails with E_LOADING.
// Polling a cheap read-only command is the honest test: there is no verified UI
// singleton to read a loading flag from, and a guessed pointer would report
// plausible garbage.
Json CmdWaitReady(const Json& args) {
    const int timeoutMs = args.has("timeout_ms") ? args["timeout_ms"].asInt(20000)
                                                 : 20000;
    const DWORD start = GetTickCount();
    while (static_cast<int>(GetTickCount() - start) < timeoutMs) {
        Json probe = OnMainThread([&]() -> Json {
            ConsoleResult cr = RunConsoleCommand("player.getav health", 0);
            Json r = Json::Object();
            r.set("output", Json(cr.output));
            return Ok(std::move(r));
        }, 2000);
        if (probe.has("result") && !probe["result"]["output"].asString().empty()) {
            Json r = Json::Object();
            r.set("ready", Json(true));
            r.set("waited_ms", Json(static_cast<double>(GetTickCount() - start)));
            return Ok(std::move(r));
        }
        Sleep(500);
    }
    Json r = Json::Object();
    r.set("ready", Json(false));
    r.set("waited_ms", Json(static_cast<double>(GetTickCount() - start)));
    return Ok(std::move(r));
}

// The flight recorder's state; `enable` turns recording on or off. Plugin-local,
// so it answers on the pipe thread.
Json CmdRecorder(const Json& args) {
    if (args.has("enable")) SetRecorderEnabled(args["enable"].asBool());
    const RecorderStats s = GetRecorderStats();
    Json r = Json::Object();
    r.set("installed", Json(s.installed));
    r.set("enabled", Json(s.enabled));
    r.set("path", Json(s.path));
    r.set("events", Json(s.events));
    r.set("bytes", Json(s.bytes));
    r.set("dropped", Json(s.dropped));
    Json kinds = Json::Object();
    for (const auto& kv : s.perKind) kinds.set(kv.first, Json(kv.second));
    r.set("per_kind", std::move(kinds));
    Json hooked = Json::Array(), missing = Json::Array();
    for (const auto& h : s.hooked) hooked.push(Json(h));
    for (const auto& m : s.missing) missing.push(Json(m));
    r.set("hooked", std::move(hooked));
    r.set("missing", std::move(missing));
    return Ok(std::move(r));
}

Json CmdStatus(const Json&) {
    if (!IsGameLoaded()) {
        Json r = Json::Object();
        r.set("game_loaded", Json(false));
        r.set("in_main_menu", Json(true));
        return Ok(std::move(r));
    }
    return OnMainThread([&]() -> Json {
        Json r = Json::Object();
        r.set("game_loaded", Json(true));
        r.set("in_main_menu", Json(false));
        {
            std::lock_guard<std::mutex> lk(g_sessionMutex);
            r.set("tracked_spawns", Json(static_cast<int>(g_spawned.size())));
            r.set("bridge_started_session", Json(g_bridgeStarted));
        }
        return Ok(std::move(r));
    });
}

}  // namespace

// ------------------------------------------------------------- dispatch ----

void ReleaseSession() {
    std::lock_guard<std::mutex> lk(g_sessionMutex);
    if (!g_spawned.empty()) {
        Log("session: client disconnected with %zu tracked spawns; "
            "they remain in the world until cleanup or reload",
            g_spawned.size());
    }
    g_spawned.clear();
}

std::string HandleRequest(const std::string& line) {
    std::string parseErr;
    Json req = Json::Parse(line, &parseErr);
    if (!req.isObject()) {
        Json j = Err("E_INTERNAL", "malformed request: " + parseErr);
        return j.dump();
    }

    const std::string cmd = req["cmd"].asString();
    const Json& args = req["args"];

    const DWORD t0 = GetTickCount();
    Json resp;

    if (cmd == "ping")              resp = CmdPing(args);
    else if (cmd == "capabilities") resp = CmdCapabilities(args);
    else if (cmd == "console")      resp = CmdConsole(args);
    else if (cmd == "batch")        resp = CmdBatch(args);
    else if (cmd == "inject")       resp = CmdInject(args);
    else if (cmd == "vmlog")        resp = CmdVmLog(args);
    else if (cmd == "resolve")      resp = CmdResolve(args);
    else if (cmd == "readmem")      resp = CmdReadMem(args);
    else if (cmd == "writemem")     resp = CmdWriteMem(args);
    else if (cmd == "alloc")        resp = CmdAlloc(args);
    else if (cmd == "hook")         resp = CmdHook(args);
    else if (cmd == "call")         resp = CmdCall(args);
    else if (cmd == "hookstats")    resp = CmdHookStats(args);
    else if (cmd == "console_log")  resp = CmdConsoleLog(args);
    else if (cmd == "status")       resp = CmdStatus(args);
    else if (cmd == "spawn")        resp = CmdSpawn(args);
    else if (cmd == "cleanup")      resp = CmdCleanup(args);
    else if (cmd == "moveref")      resp = CmdMoveRef(args);
    else if (cmd == "wait_ready")   resp = CmdWaitReady(args);
    else if (cmd == "recorder")     resp = CmdRecorder(args);
    // Distinct from E_UNSUPPORTED (a command this runtime cannot resolve):
    // this plugin build simply does not have the command, so a newer client can
    // fall back to a console-based path instead of reporting a hard failure.
    else resp = Err("E_UNKNOWN_CMD", "unknown command: " + cmd);

    if (req.has("id")) resp.set("id", req["id"]);
    resp.set("elapsed_ms", Json(static_cast<int>(GetTickCount() - t0)));
    return resp.dump();
}

}  // namespace bridge
