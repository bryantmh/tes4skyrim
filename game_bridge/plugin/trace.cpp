// Frame-rate memory sampler + timestamped event hooks. See trace.h.
//
// FILE FORMAT (little-endian), one stream of records:
//   u8 type, u32 payloadLen, payload
//   type 0 META    utf-8 JSON supplied by the client (what each region is)
//   type 1 SAMPLE  u64 t_us, then per region in order:
//                    direct/deref: u8 ok, len bytes
//                    array:        u32 n, then n x (u8 ok, stride bytes element,
//                                  len bytes read at the element's node pointer)
//   type 2 HOOK    u8 id, u64 t_enter_us, u64 t_exit_us, u32 thread,
//                  u64 a1..a4, u64 ret, u16 capLen, capLen bytes
//
// Reads go through SafeCopy (SEH): the sampler runs off the main thread and the
// actor can unload under it; a fault zero-fills and marks the region not-ok
// instead of taking the game down.

#define _CRT_SECURE_NO_WARNINGS

#include "trace.h"

#include <windows.h>

#include <atomic>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <thread>

#include "detour.h"
#include "generic_hook.h"
#include "log.h"

namespace bridge {

namespace {

std::mutex               g_fileMu;
std::FILE*               g_file = nullptr;
std::atomic<bool>        g_running{false};
std::thread              g_thread;
std::vector<TraceRegion> g_regions;
unsigned                 g_intervalMs = 8;
TraceStats               g_stats;
LARGE_INTEGER            g_freq{};

std::uint64_t NowUs() {
    LARGE_INTEGER c;
    QueryPerformanceCounter(&c);
    return static_cast<std::uint64_t>(c.QuadPart * 1000000.0 / g_freq.QuadPart);
}

// Copies n bytes; zero-fills and returns false on a fault.
bool SafeCopy(void* dst, std::uintptr_t src, std::size_t n) {
    if (!src) {
        std::memset(dst, 0, n);
        return false;
    }
    __try {
        std::memcpy(dst, reinterpret_cast<const void*>(src), n);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        std::memset(dst, 0, n);
        return false;
    }
}

std::uintptr_t LoadPtr(std::uintptr_t addr) {
    std::uintptr_t p = 0;
    return SafeCopy(&p, addr, sizeof(p)) ? p : 0;
}

void Put(std::vector<std::uint8_t>& b, const void* p, std::size_t n) {
    const auto* s = static_cast<const std::uint8_t*>(p);
    b.insert(b.end(), s, s + n);
}

template <class T>
void PutV(std::vector<std::uint8_t>& b, T v) { Put(b, &v, sizeof(v)); }

// Appends one framed record under the file lock.
void WriteRecord(std::uint8_t type, const std::vector<std::uint8_t>& payload) {
    std::lock_guard<std::mutex> lk(g_fileMu);
    if (!g_file) return;
    const std::uint32_t n = static_cast<std::uint32_t>(payload.size());
    std::fwrite(&type, 1, 1, g_file);
    std::fwrite(&n, 4, 1, g_file);
    if (n) std::fwrite(payload.data(), 1, n, g_file);
    g_stats.bytes += 5 + n;
    if (type == 1) ++g_stats.samples;
    if (type == 2) ++g_stats.hookRecords;
}

void SampleRegion(std::vector<std::uint8_t>& b, const TraceRegion& r,
                  std::vector<std::uint8_t>& tmp) {
    if (r.kind != TraceRegion::kPtrArray) {
        const std::uintptr_t src = r.kind == TraceRegion::kDeref
            ? (LoadPtr(r.addr) ? LoadPtr(r.addr) + r.off : 0) : r.addr;
        tmp.resize(r.len);
        const std::uint8_t ok = SafeCopy(tmp.data(), src, r.len) ? 1 : 0;
        PutV(b, ok);
        Put(b, tmp.data(), r.len);
        return;
    }
    const std::uintptr_t data = LoadPtr(r.addr);
    std::int32_t size = 0;
    SafeCopy(&size, r.addr + 8, 4);
    std::uint32_t n = size < 0 ? 0 : static_cast<std::uint32_t>(size);
    if (n > r.maxCount) n = r.maxCount;
    PutV(b, n);
    tmp.resize(r.stride + r.len);
    for (std::uint32_t i = 0; i < n; ++i) {
        const std::uintptr_t elem = data + static_cast<std::uintptr_t>(i) * r.stride;
        bool ok = SafeCopy(tmp.data(), elem, r.stride);
        std::uintptr_t node = 0;
        std::memcpy(&node, tmp.data() + r.elemPtrOff, sizeof(node));
        ok = SafeCopy(tmp.data() + r.stride, node, r.len) && ok;
        PutV(b, static_cast<std::uint8_t>(ok ? 1 : 0));
        Put(b, tmp.data(), tmp.size());
    }
}

void SamplerLoop() {
    timeBeginPeriod(1);
    std::vector<std::uint8_t> b, tmp;
    while (g_running.load()) {
        b.clear();
        PutV(b, NowUs());
        for (const auto& r : g_regions) SampleRegion(b, r, tmp);
        WriteRecord(1, b);
        Sleep(g_intervalMs);
    }
    timeEndPeriod(1);
}

// ------------------------------------------------------------------ hooks --

struct HookSlot {
    bool           used = false;
    std::uintptr_t target = 0;
    void*          original = nullptr;
    int            capArg = 2;
    std::size_t    capLen = 0;
};

constexpr int kMaxTraceHooks = 6;
HookSlot g_hooks[kMaxTraceHooks];

template <int N>
std::uintptr_t TraceThunk(std::uintptr_t a1, std::uintptr_t a2,
                          std::uintptr_t a3, std::uintptr_t a4) {
    const HookSlot& s = g_hooks[N];
    auto orig = reinterpret_cast<std::uintptr_t (*)(
        std::uintptr_t, std::uintptr_t, std::uintptr_t, std::uintptr_t)>(s.original);
    if (!g_running.load()) return orig(a1, a2, a3, a4);

    std::uint8_t cap[256];
    const std::uintptr_t args[4] = {a1, a2, a3, a4};
    const std::size_t capLen = s.capLen > sizeof(cap) ? sizeof(cap) : s.capLen;
    if (capLen) SafeCopy(cap, args[s.capArg - 1], capLen);
    const std::uint64_t t0 = NowUs();
    const std::uintptr_t ret = orig(a1, a2, a3, a4);
    const std::uint64_t t1 = NowUs();

    std::vector<std::uint8_t> b;
    b.reserve(64 + capLen);
    PutV(b, static_cast<std::uint8_t>(N));
    PutV(b, t0);
    PutV(b, t1);
    PutV(b, static_cast<std::uint32_t>(GetCurrentThreadId()));
    for (auto a : args) PutV(b, static_cast<std::uint64_t>(a));
    PutV(b, static_cast<std::uint64_t>(ret));
    PutV(b, static_cast<std::uint16_t>(capLen));
    Put(b, cap, capLen);
    WriteRecord(2, b);
    return ret;
}

void* TraceThunkFor(int i) {
    switch (i) {
        case 0: return reinterpret_cast<void*>(&TraceThunk<0>);
        case 1: return reinterpret_cast<void*>(&TraceThunk<1>);
        case 2: return reinterpret_cast<void*>(&TraceThunk<2>);
        case 3: return reinterpret_cast<void*>(&TraceThunk<3>);
        case 4: return reinterpret_cast<void*>(&TraceThunk<4>);
        case 5: return reinterpret_cast<void*>(&TraceThunk<5>);
        default: return nullptr;
    }
}

}  // namespace

bool TraceStart(const std::string& path, const std::vector<TraceRegion>& regions,
                unsigned intervalMs, const std::string& meta, std::string* err) {
    if (g_running.load()) TraceStop();
    QueryPerformanceFrequency(&g_freq);
    {
        std::lock_guard<std::mutex> lk(g_fileMu);
        g_file = std::fopen(path.c_str(), "wb");
        if (!g_file) {
            if (err) *err = "cannot open " + path;
            return false;
        }
        std::setvbuf(g_file, nullptr, _IOFBF, 1 << 20);
        g_stats = TraceStats{};
        g_stats.path = path;
    }
    g_regions = regions;
    g_intervalMs = intervalMs ? intervalMs : 8;
    WriteRecord(0, std::vector<std::uint8_t>(meta.begin(), meta.end()));
    g_running = true;
    g_thread = std::thread(SamplerLoop);
    Log("trace: started -> %s (%zu regions, %u ms)", path.c_str(), regions.size(),
        g_intervalMs);
    return true;
}

void TraceStop() {
    if (!g_running.exchange(false)) return;
    if (g_thread.joinable()) g_thread.join();
    std::lock_guard<std::mutex> lk(g_fileMu);
    if (g_file) {
        std::fclose(g_file);
        g_file = nullptr;
    }
    Log("trace: stopped (%llu samples, %llu hook records)",
        static_cast<unsigned long long>(g_stats.samples),
        static_cast<unsigned long long>(g_stats.hookRecords));
}

TraceStats TraceQuery() {
    std::lock_guard<std::mutex> lk(g_fileMu);
    TraceStats s = g_stats;
    s.running = g_running.load();
    return s;
}

int TraceHook(std::uintptr_t target, int capArg, std::size_t capLen,
              const std::string& label, std::string* err) {
    int idx = -1;
    for (int i = 0; i < kMaxTraceHooks; ++i) {
        if (g_hooks[i].used && g_hooks[i].target == target) return i;
        if (!g_hooks[i].used && idx < 0) idx = i;
    }
    if (idx < 0) {
        if (err) *err = "no free trace hook slots";
        return -1;
    }
    if (capArg < 1 || capArg > 4) capArg = 2;
    std::string why;
    const std::size_t stolen = AnalyzePrologue(target, &why);
    if (!stolen) {
        if (err) *err = "cannot hook safely: " + why;
        return -1;
    }
    std::vector<std::uint8_t> expected(stolen);
    std::memcpy(expected.data(), reinterpret_cast<const void*>(target), stolen);
    HookSlot& s = g_hooks[idx];
    s.capArg = capArg;
    s.capLen = capLen;
    if (!InstallDetour(target, TraceThunkFor(idx), expected.data(), stolen,
                       &s.original, label.c_str())) {
        if (err) *err = "detour install failed (see the log)";
        return -1;
    }
    s.used = true;
    s.target = target;
    Log("trace hook[%d]: %s at %p", idx, label.c_str(), reinterpret_cast<void*>(target));
    return idx;
}

}  // namespace bridge
