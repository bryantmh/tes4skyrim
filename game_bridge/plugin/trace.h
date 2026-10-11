// Frame-rate memory sampler + timestamped event hooks, streamed to one file.
//
// Exists because a behavior-graph bug ("the creature slides for a moment after
// an attack") lives in the ORDER of things inside a few frames: which event the
// engine sent, whether the graph took it, which state machine switched, what
// the variables and the actor's position did. One readmem per main-thread trip
// cannot see that; this records it at ~120 Hz on a background thread and from
// detours, on one QueryPerformanceCounter clock. tools/live/graph_trace.py
// decides WHAT to record and decodes the file.

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace bridge {

struct TraceRegion {
    enum Kind : std::uint8_t { kDirect = 0, kDeref = 1, kPtrArray = 2 };
    Kind           kind = kDirect;
    std::uintptr_t addr = 0;     // direct: source; deref/array: address of the pointer / hkArray
    std::int32_t   off = 0;      // deref: offset added to the loaded pointer
    std::uint32_t  len = 0;      // bytes copied (direct/deref) or per element node capture
    std::uint32_t  stride = 8;   // array: element size in bytes
    std::uint32_t  elemPtrOff = 0;  // array: offset of the node pointer inside an element
    std::uint32_t  maxCount = 256;  // array: elements recorded at most
};

struct TraceStats {
    bool          running = false;
    std::uint64_t samples = 0;
    std::uint64_t hookRecords = 0;
    std::uint64_t bytes = 0;
    std::string   path;
};

// Starts sampling `regions` every `intervalMs` into `path` (truncated). `meta`
// is stored verbatim as the file's first record so the file decodes on its own.
bool TraceStart(const std::string& path, const std::vector<TraceRegion>& regions,
                unsigned intervalMs, const std::string& meta, std::string* err);

// Stops the sampler, flushes and closes the file. Hooks stay installed but
// write nothing until the next TraceStart.
void TraceStop();

TraceStats TraceQuery();

// Installs a timestamped hook: on every call records entry/exit time, thread,
// the four integer args, the return value, and `capLen` bytes read from arg
// `capArg` (1-4) at entry. Returns the hook id or -1.
int TraceHook(std::uintptr_t target, int capArg, std::size_t capLen,
              const std::string& label, std::string* err);

}  // namespace bridge
