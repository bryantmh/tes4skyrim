#include "stream.h"

#include <windows.h>

#include <cstring>
#include <vector>

#include "addresses.h"
#include "log.h"

namespace tesruntime {

namespace {

// Where the engine keeps a stream's state/refcount word: +0xc on 1.6.659,
// +0x10 on 1.6.1170. DetectStreamLayout reads it off the parser.
std::size_t g_flagsOffset = 0x10;

constexpr std::uint32_t kRefUnit  = 0x1000;
constexpr std::uint32_t kRefMask  = 0xFFFFF000u;
constexpr std::uint32_t kOpenBits = 0xE;     // what the engine wrapper sets

// ErrorCode values (BSResource::ErrorCode).
constexpr int kErrNone = 0;
constexpr int kErrUnsupported = 8;

struct StreamHeader {
    void**         vtable;
    std::uint32_t  totalSize;
    std::uint32_t  word0c;
    std::uint32_t  word10;
    std::uint32_t  pad14;
};

volatile std::uint32_t* Flags(StreamHeader* h) {
    return reinterpret_cast<volatile std::uint32_t*>(
        reinterpret_cast<std::uint8_t*>(h) + g_flagsOffset);
}

using DtorFn   = void* (*)(void*, std::uint32_t);
using OpenFn   = int (*)(void*);
using CloseFn  = void (*)(void*);
using ReadFn   = int (*)(void*, void*, std::uint64_t, std::uint64_t*);

StreamHeader* H(EngineStream* s) { return reinterpret_cast<StreamHeader*>(s); }

void SetFlags(StreamHeader* h, std::uint32_t bits) {
    volatile std::uint32_t* f = Flags(h);
    for (;;) {
        std::uint32_t old = *f;
        if (InterlockedCompareExchange(f, old | bits, old) == old) return;
    }
}

void ClearFlags(StreamHeader* h, std::uint32_t bits) {
    volatile std::uint32_t* f = Flags(h);
    for (;;) {
        std::uint32_t old = *f;
        if (InterlockedCompareExchange(f, old & ~bits, old) == old) return;
    }
}

}  // namespace

bool DetectStreamLayout(std::uintptr_t parser, std::size_t scanLen) {
    std::uintptr_t begin = 0, end = 0;
    if (!parser || !TextRange(begin, end) || parser < begin || parser + scanLen > end) return false;
    const auto* p = reinterpret_cast<const std::uint8_t*>(parser);
    for (std::size_t i = 0; i + 5 <= scanLen; ++i) {
        // lock cmpxchg dword ptr [rcx + disp8], edx: the parser's own release.
        if (p[i] == 0xF0 && p[i + 1] == 0x0F && p[i + 2] == 0xB1 && p[i + 3] == 0x51) {
            g_flagsOffset = p[i + 4];
            Log("stream: refcount/flags word at +0x%zx (parser+0x%zx)", g_flagsOffset, i);
            return true;
        }
    }
    Log("stream: no refcount cmpxchg in the parser; assuming +0x%zx", g_flagsOffset);
    return false;
}

bool ReadEngineStream(EngineStream* s, std::string& out) {
    auto* h = H(s);
    out.clear();
    if (!h || !h->vtable) return false;
    SetFlags(h, kOpenBits);
    const int rc = reinterpret_cast<OpenFn>(h->vtable[1])(h);
    if (rc != kErrNone) {
        Log("stream: DoOpen failed (%d)", rc);
        ClearFlags(h, kOpenBits);
        return false;
    }
    const std::uint32_t total = h->totalSize;
    std::vector<char> buf(0x10000);
    std::uint64_t got = 0;
    while (got < total) {
        std::uint64_t n = 0;
        const int r = reinterpret_cast<ReadFn>(h->vtable[6])(
            h, buf.data(), buf.size(), &n);
        if (r != kErrNone || n == 0) break;
        out.append(buf.data(), static_cast<size_t>(n));
        got += n;
    }
    reinterpret_cast<CloseFn>(h->vtable[2])(h);
    ClearFlags(h, kOpenBits);
    if (got != total) {
        Log("stream: read %llu of %u bytes", static_cast<unsigned long long>(got), total);
    }
    return got == total && total != 0;
}

void ReleaseEngineStream(EngineStream* s) {
    auto* h = H(s);
    if (!h) return;
    volatile std::uint32_t* f = Flags(h);
    std::uint32_t now;
    for (;;) {
        std::uint32_t old = *f;
        now = old - kRefUnit;
        if (InterlockedCompareExchange(f, now, old) == old) break;
    }
    if ((now & kRefMask) == 0) {
        reinterpret_cast<DtorFn>(h->vtable[0])(h, 1);
    }
}

// ------------------------------------------------------- the memory stream ----

namespace {

struct MemoryStream {
    StreamHeader hdr;
    std::string  data;
    std::uint64_t pos = 0;
    std::uint64_t reads = 0;
    std::uint32_t opens = 0;
};

MemoryStream* M(void* self) { return reinterpret_cast<MemoryStream*>(self); }

// Every slot but DoRead logs, so a run shows which parts of the contract the
// parser exercised and how much of the text it consumed.
void* __fastcall MsDtor(void* self, std::uint32_t flag) {
    auto* m = M(self);
    Log("memstream %p: dtor(%u) after %u open(s), %llu reads, pos %llu of %zu",
        self, flag, m->opens, static_cast<unsigned long long>(m->reads),
        static_cast<unsigned long long>(m->pos), m->data.size());
    m->data.clear();
    if (flag & 1) delete m;
    return self;
}
// A file stream starts at the beginning on every open; so does this one.
int  __fastcall MsOpen(void* self) {
    auto* m = M(self);
    ++m->opens;
    m->pos = 0;
    Log("memstream %p: DoOpen #%u (flags %08X, %zu bytes)", self, m->opens,
        *Flags(&m->hdr), m->data.size());
    return kErrNone;
}
void __fastcall MsClose(void* self) {
    auto* m = M(self);
    Log("memstream %p: DoClose after %llu reads, pos %llu of %zu", self,
        static_cast<unsigned long long>(m->reads),
        static_cast<unsigned long long>(m->pos), m->data.size());
}
std::uint64_t __fastcall MsGetKey(void* self) { Log("memstream %p: DoGetKey", self); return 0; }
int  __fastcall MsGetInfo(void* self, void*) {
    Log("memstream %p: DoGetInfo (unsupported)", self);
    return kErrUnsupported;
}
void __fastcall MsClone(void* self, void** out) {
    auto* m = M(self);
    *out = NewMemoryStream(m->data);
    Log("memstream %p: DoClone -> %p", self, *out);
}
int  __fastcall MsRead(void* self, void* buf, std::uint64_t n, std::uint64_t* read) {
    auto* m = M(self);
    const std::uint64_t left = m->data.size() - m->pos;
    const std::uint64_t take = n < left ? n : left;
    if (take) std::memcpy(buf, m->data.data() + m->pos, static_cast<size_t>(take));
    m->pos += take;
    ++m->reads;
    if (read) *read = take;
    return kErrNone;
}
int  __fastcall MsWrite(void* self, const void*, std::uint64_t, std::uint64_t* wrote) {
    Log("memstream %p: DoWrite (unsupported)", self);
    if (wrote) *wrote = 0;
    return kErrUnsupported;
}
int  __fastcall MsSeek(void* self, std::uint64_t off, int mode, std::uint64_t* outPos) {
    auto* m = M(self);
    std::uint64_t base = mode == 1 ? m->pos : mode == 2 ? m->data.size() : 0;
    m->pos = base + off;
    if (m->pos > m->data.size()) m->pos = m->data.size();
    if (outPos) *outPos = m->pos;
    Log("memstream %p: DoSeek(%llu, mode %d) -> %llu", self,
        static_cast<unsigned long long>(off), mode,
        static_cast<unsigned long long>(m->pos));
    return kErrNone;
}
int  __fastcall MsSetEnd(void* self) { Log("memstream %p: DoSetEndOfStream", self); return kErrUnsupported; }
bool __fastcall MsPrefetch(void* self, std::uint32_t) { Log("memstream %p: DoPrefetchAll", self); return false; }
bool __fastcall MsGetName(void* self, void*) { Log("memstream %p: DoGetName", self); return false; }
int  __fastcall MsCreateAsync(void* self, void*) { Log("memstream %p: DoCreateAsync", self); return kErrUnsupported; }
bool __fastcall MsTagged(void* self) { Log("memstream %p: DoQTaggedForCanceling", self); return false; }
void __fastcall MsNoop(void* self) { Log("memstream %p: slot 14+ called", self); }

void* g_vtable[24] = {
    reinterpret_cast<void*>(&MsDtor),        // 0
    reinterpret_cast<void*>(&MsOpen),        // 1
    reinterpret_cast<void*>(&MsClose),       // 2
    reinterpret_cast<void*>(&MsGetKey),      // 3
    reinterpret_cast<void*>(&MsGetInfo),     // 4
    reinterpret_cast<void*>(&MsClone),       // 5
    reinterpret_cast<void*>(&MsRead),        // 6
    reinterpret_cast<void*>(&MsWrite),       // 7
    reinterpret_cast<void*>(&MsSeek),        // 8
    reinterpret_cast<void*>(&MsSetEnd),      // 9
    reinterpret_cast<void*>(&MsPrefetch),    // 10
    reinterpret_cast<void*>(&MsGetName),     // 11
    reinterpret_cast<void*>(&MsCreateAsync), // 12
    reinterpret_cast<void*>(&MsTagged),      // 13
    reinterpret_cast<void*>(&MsNoop), reinterpret_cast<void*>(&MsNoop),
    reinterpret_cast<void*>(&MsNoop), reinterpret_cast<void*>(&MsNoop),
    reinterpret_cast<void*>(&MsNoop), reinterpret_cast<void*>(&MsNoop),
    reinterpret_cast<void*>(&MsNoop), reinterpret_cast<void*>(&MsNoop),
    reinterpret_cast<void*>(&MsNoop), reinterpret_cast<void*>(&MsNoop),
};

}  // namespace

EngineStream* NewMemoryStream(std::string text) {
    auto* m = new MemoryStream();
    m->hdr.vtable = g_vtable;
    m->hdr.totalSize = static_cast<std::uint32_t>(text.size());
    // One reference at whichever word this build treats as the refcount.
    m->hdr.word0c = kRefUnit;
    m->hdr.word10 = kRefUnit;
    m->hdr.pad14 = 0;
    m->data = std::move(text);
    static_assert(offsetof(MemoryStream, hdr) == 0, "header first");
    static_assert(offsetof(StreamHeader, totalSize) == 8, "totalSize at +8");
    static_assert(offsetof(StreamHeader, word10) == 0x10, "state word at +0x10");
    static_assert(offsetof(MemoryStream, data) >= 0x18, "engine words precede the text");
    return reinterpret_cast<EngineStream*>(m);
}

}  // namespace tesruntime
