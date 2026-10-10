// Flight recorder: an always-on log of what the engine decided while the game
// is played normally, read afterwards with the game closed.
//
// WHY
// ---
// Every in-game bug in the FNV opening passed the offline data checks, and each
// one had to be reasoned back out of an autosave or the Papyrus log: which
// dialogue line played, whether the follow-up fired, which stage was set in
// what order, whether a trigger box was ever entered. The engine knows all of
// that as it happens; this writes it down.
//
// HOW
// ---
// The Papyrus VM (SkyrimVM) is registered as a sink for every script event --
// that is how OnActivate, OnStageSet, OnTriggerEnter reach scripts. Each
// BSTEventSink<T> base has its own vtable, and slot 1 is ProcessEvent. The
// recorder finds those vtables by RTTI at startup (rtti.h) and swaps slot 1
// for a thunk that writes one JSON line and calls through. No stable ID, no
// signature, no inline detour: a missing sink is reported and skipped.
//
// Event layouts are the one assumption (see the table in recorder.cpp), so each
// decoded form is CHECKED: a quest id must look up to a QUST, a speaker to a
// reference. A failed check adds "<field>_check", the raw event bytes, and the
// offsets where a form of the expected type actually sits -- a wrong layout is
// visible on the first run instead of quietly logging garbage.
//
// Output: Documents\My Games\Skyrim Special Edition\SKSE\
//         TESGameBridge_events.jsonl (previous runs rotate to .1 and .2).
// Reader: tools/live/flight_log.py

#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace bridge {

// Opens the log and hooks the sinks. Needs g_addr resolved (form lookup).
bool InstallRecorder();

// SKSE lifecycle messages (save, load, new game), which no sink reports.
void RecorderMessage(const char* kind, const char* saveName);

// A Papyrus log line. Only errors, warnings and their stack frames are kept.
void RecorderPapyrus(const char* text);

void SetRecorderEnabled(bool on);

struct RecorderStats {
    bool        installed = false;
    bool        enabled = false;
    std::string path;
    std::uint64_t events = 0, bytes = 0, dropped = 0;
    std::vector<std::pair<std::string, std::uint64_t>> perKind;
    std::vector<std::string> hooked, missing;
};

RecorderStats GetRecorderStats();

// Pure decoding, exposed for test_recorder.cpp: one event as JSON fields
// (no braces), with forms resolved through `lookup`. Returns false if the
// event is filtered out (e.g. a container change not involving the player).
using FormLookupFn = void* (*)(std::uint32_t formId);
bool DecodeEventForTest(const char* kind, const void* event, FormLookupFn lookup,
                        std::string* fields);

}  // namespace bridge
