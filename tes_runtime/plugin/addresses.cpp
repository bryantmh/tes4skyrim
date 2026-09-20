#include "addresses.h"

#include <windows.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <vector>

#include "log.h"

namespace tesruntime {

VersionDb g_versionDb;

std::uintptr_t ModuleBase() {
    static std::uintptr_t base = reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr));
    return base;
}

bool TextRange(std::uintptr_t& begin, std::uintptr_t& end) {
    static std::uintptr_t b = 0, e = 0;
    if (!b) {
        auto base = ModuleBase();
        auto dos  = reinterpret_cast<const IMAGE_DOS_HEADER*>(base);
        auto nt   = reinterpret_cast<const IMAGE_NT_HEADERS64*>(base + dos->e_lfanew);
        auto sec  = IMAGE_FIRST_SECTION(nt);
        for (WORD i = 0; i < nt->FileHeader.NumberOfSections; ++i) {
            if (std::memcmp(sec[i].Name, ".text", 5) == 0) {
                b = base + sec[i].VirtualAddress;
                e = b + sec[i].Misc.VirtualSize;
                break;
            }
        }
    }
    begin = b; end = e;
    return b != 0;
}

// ------------------------------------------------------------- versionlib ----

namespace {

class Reader {
public:
    Reader(const std::uint8_t* d, size_t n) : d_(d), n_(n) {}
    bool ok() const { return !bad_; }
    size_t pos() const { return p_; }
    size_t size() const { return n_; }
    std::uint8_t  u8()  { if (p_ + 1 > n_) { bad_ = true; return 0; } return d_[p_++]; }
    std::uint16_t u16() { std::uint16_t v{}; return read(v); }
    std::uint32_t u32() { std::uint32_t v{}; return read(v); }
    std::int32_t  i32() { std::int32_t  v{}; return read(v); }
    std::uint64_t u64() { std::uint64_t v{}; return read(v); }
    void skip(size_t n) { if (p_ + n > n_) bad_ = true; else p_ += n; }

private:
    template <typename T> T read(T v) {
        if (p_ + sizeof(T) > n_) { bad_ = true; return 0; }
        std::memcpy(&v, d_ + p_, sizeof(T));
        p_ += sizeof(T);
        return v;
    }
    const std::uint8_t* d_;
    size_t n_, p_ = 0;
    bool bad_ = false;
};

// Delta decoder shared by the id and offset nibbles. Kinds 6 and 7 are
// u16/u32; reading them as u64 desyncs the whole delta-coded stream.
std::uint64_t ReadKind(Reader& r, std::uint8_t kind, std::uint64_t prev) {
    switch (kind) {
        case 0: return r.u64();
        case 1: return prev + 1;
        case 2: return prev + r.u8();
        case 3: return prev - r.u8();
        case 4: return prev + r.u16();
        case 5: return prev - r.u16();
        case 6: return r.u16();
        case 7: return r.u32();
        default: return prev;
    }
}

std::string DataPluginsDir() {
    wchar_t buf[MAX_PATH]{};
    GetModuleFileNameW(nullptr, buf, MAX_PATH);
    std::wstring w(buf);
    auto slash = w.find_last_of(L"\\/");
    std::wstring dir = (slash == std::wstring::npos) ? L"." : w.substr(0, slash);
    dir += L"\\Data\\SKSE\\Plugins\\";
    std::string out;
    for (wchar_t c : dir) out.push_back(static_cast<char>(c));
    return out;
}

using AddressMap = std::unordered_map<std::uint64_t, std::uint64_t>;

// Formats 1 and 2: one control byte per entry, both halves delta-coded.
bool DecodeDelta(Reader& r, AddressMap& map, std::int32_t ptrSize, std::int32_t count) {
    map.reserve(static_cast<size_t>(count));
    std::uint64_t prevId = 0, prevOff = 0;
    for (std::int32_t i = 0; i < count && r.ok(); ++i) {
        const std::uint8_t ctl = r.u8();
        const std::uint8_t lo  = ctl & 0x0F;
        const std::uint8_t hi  = (ctl >> 4) & 0x0F;
        const std::uint64_t id = ReadKind(r, lo, prevId);
        // Bit 3 of the high nibble scales the PREVIOUS offset by ptrSize
        // before the delta and the result after.
        const bool scaled = (hi & 0x08) != 0;
        const std::uint64_t base = scaled ? (prevOff / static_cast<std::uint64_t>(ptrSize)) : prevOff;
        std::uint64_t off = ReadKind(r, hi & 0x07, base);
        if (scaled) off *= static_cast<std::uint64_t>(ptrSize);
        map[id] = off;
        prevId = id;
        prevOff = off;
    }
    // A desynced stream yields plausible-but-wrong addresses: refuse it.
    return r.ok() && r.pos() == r.size();
}

// Format 5: a flat u32[count] of RVAs indexed by stable id, 0 meaning absent.
bool DecodeFlat(Reader& r, AddressMap& map, std::int32_t count) {
    if (r.size() - r.pos() != static_cast<size_t>(count) * 4u) return false;
    map.reserve(static_cast<size_t>(count));
    for (std::int32_t id = 0; id < count && r.ok(); ++id) {
        if (const std::uint32_t off = r.u32()) map[static_cast<std::uint64_t>(id)] = off;
    }
    return r.ok() && r.pos() == r.size();
}

}  // namespace

bool VersionDb::Load(std::uint32_t runtimeVersion) {
    // SKSE packs (major<<24 | minor<<16 | build<<4 | sub); BUILD IS 12 BITS.
    const unsigned maj   = (runtimeVersion & 0xFF000000u) >> 24;
    const unsigned min   = (runtimeVersion & 0x00FF0000u) >> 16;
    const unsigned build = (runtimeVersion & 0x0000FFF0u) >> 4;
    const unsigned sub   = (runtimeVersion & 0x0000000Fu);
    // Every id in ids.h is AE-space, and AE renumbered the whole space: on
    // 1.5.97 the same id names a different function. A hit there is a wrong
    // address, not a missing one.
    // See: docs/reference/address_library_formats.md#two-id-generations
    if (maj == 1 && min < 6) {
        Log("addresses: runtime %u.%u.%u is pre-AE; ids do not apply, "
            "signatures only", maj, min, build);
        return false;
    }
    // `sub` is the storefront (0 Steam, 1 GOG, 2 Epic), whose database carries
    // its own suffix; fall back to -0 rather than giving up.
    char name[128];
    if (sub != 0) {
        std::snprintf(name, sizeof(name), "versionlib-%u-%u-%u-%u.bin", maj, min, build, sub);
        if (LoadFile(DataPluginsDir() + name)) return true;
    }
    std::snprintf(name, sizeof(name), "versionlib-%u-%u-%u-0.bin", maj, min, build);
    return LoadFile(DataPluginsDir() + name);
}

bool VersionDb::LoadFile(const std::string& path) {
    map_.clear();
    loaded_ = false;
    path_ = path;

    std::ifstream f(path_, std::ios::binary);
    if (!f) return false;
    std::vector<std::uint8_t> data((std::istreambuf_iterator<char>(f)),
                                    std::istreambuf_iterator<char>());
    if (data.empty()) return false;

    Reader r(data.data(), data.size());
    const std::int32_t format = r.i32();
    for (int i = 0; i < 4; ++i) r.i32();     // build quad
    // Format 5 (1.7.x) replaced the length-prefixed name with a fixed 64-byte
    // NUL-padded field and a u32 pad, then dropped delta coding entirely.
    // See: docs/reference/address_library_formats.md#format-5--flat-array
    std::int32_t ptrSize = 0, count = 0;
    if (format == 5) {
        r.skip(64);
        ptrSize = r.i32();
        r.i32();                             // pad, always 0
        count = r.i32();
    } else if (format == 1 || format == 2) {
        const std::int32_t nameLen = r.i32();
        if (nameLen < 0) return false;
        r.skip(static_cast<size_t>(nameLen));
        ptrSize = r.i32();
        count   = r.i32();
    } else {
        return false;
    }
    if (ptrSize <= 0 || count < 0 || !r.ok()) return false;

    if (!(format == 5 ? DecodeFlat(r, map_, count)
                      : DecodeDelta(r, map_, ptrSize, count))) {
        map_.clear();
        return false;
    }
    loaded_ = true;
    return true;
}

std::uintptr_t VersionDb::Get(std::uint64_t id) const {
    auto it = map_.find(id);
    if (it == map_.end()) return 0;
    return ModuleBase() + static_cast<std::uintptr_t>(it->second);
}

// ------------------------------------------------------------- signatures ----

namespace {

std::uintptr_t ScanOne(const char* pattern, const char* stop) {
    std::vector<int> bytes;  // -1 == wildcard
    for (const char* p = pattern; p < stop && *p;) {
        if (*p == ' ') { ++p; continue; }
        if (*p == '?') { bytes.push_back(-1); while (*p == '?') ++p; continue; }
        bytes.push_back(static_cast<int>(std::strtoul(p, nullptr, 16)));
        while (p < stop && *p && *p != ' ') ++p;
    }
    if (bytes.empty()) return 0;
    std::uintptr_t begin = 0, end = 0;
    if (!TextRange(begin, end)) return 0;
    const size_t n = bytes.size();
    for (std::uintptr_t a = begin; a + n <= end; ++a) {
        const auto* m = reinterpret_cast<const std::uint8_t*>(a);
        bool hit = true;
        for (size_t i = 0; i < n; ++i) {
            if (bytes[i] >= 0 && m[i] != static_cast<std::uint8_t>(bytes[i])) { hit = false; break; }
        }
        if (hit) return a;
    }
    return 0;
}

}  // namespace

std::uintptr_t ScanSignature(const char* pattern) {
    for (const char* p = pattern; p && *p;) {
        const char* bar = std::strchr(p, '|');
        const char* stop = bar ? bar : p + std::strlen(p);
        if (const std::uintptr_t a = ScanOne(p, stop)) return a;
        p = bar ? bar + 1 : stop;
    }
    return 0;
}

std::uintptr_t Resolve(const char* debugName, std::uint64_t stableId,
                       const char* signature) {
    std::uintptr_t a = 0;
    if (stableId && g_versionDb.loaded()) a = g_versionDb.Get(stableId);
    const char* how = "versionlib";
    if (!a && signature) { a = ScanSignature(signature); how = "signature"; }
    if (!a) Log("addresses: UNRESOLVED %s (id %llu)", debugName,
                static_cast<unsigned long long>(stableId));
    else Log("addresses: %s = %p (%s)", debugName,
             reinterpret_cast<void*>(a), how);
    return a;
}

}  // namespace tesruntime
