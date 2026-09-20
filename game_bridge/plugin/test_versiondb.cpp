// Standalone test for the versionlib parser.
//
// The parser is the foundation every resolved address rests on, and its failure
// mode is silent: a desynced stream still produces plausible-looking numbers,
// which would then be called as function pointers. So it is verified against
// known-good values produced by tools/address_lib.py before shipping.
//
// Build and run:
//   cl /nologo /EHsc /std:c++17 /DBRIDGE_TEST_MAIN test_versiondb.cpp addresses.cpp log.cpp
//   test_versiondb.exe "<path to versionlib-1-6-1170-0.bin>" <runtimeVersion>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iterator>
#include <string>
#include <vector>

#include "addresses.h"

using namespace bridge;

namespace {

struct Expect {
    std::uint64_t id;
    std::uint64_t rva;
    const char*   label;
};

// Values cross-checked with:
//   python tools/disasm/address_lib.py --id <id> --from 1.7.104
// 1.7.x is format 5: a flat u32[count] of RVAs indexed by stable id, with a
// fixed 64-byte name field instead of a length-prefixed one.
const Expect kExpected17104[] = {
    {21954,  0x347930,  "ConsoleExecute"},
    {21964,  0x34a280,  "CompileAndRun"},
    {21883,  0x3435e0,  "Script::SetText"},
    {68115,  0xcde250,  "MemAlloc"},
    {191694, 0x183ad68, "Script::vtable"},
    {107327, 0x154c600, "AddressLib smoke test"},
    {55469,  0xa24de0,  "Game.GetPlayer"},
};

// Values cross-checked with:
//   python tools/address_lib.py --id <id> --from 1.6.1170
const Expect kExpected1170[] = {
    {21954,  0x3412d0,  "ConsoleExecute"},
    {21964,  0x343c20,  "CompileAndRun"},
    {21883,  0x33cf80,  "Script::SetText"},
    {68115,  0xcc40c0,  "MemAlloc"},
    {191694, 0x17c1618, "Script::vtable"},
    {368092, 0x1ff2d60, "compilerNameTable"},
    {107327, 0x14e00c0, "AddressLib smoke test"},
};

// The filename VersionDb::Load derives from a packed runtime version. This is
// pure arithmetic, so it is testable without the game -- and it is where the
// 2026-08-14 bug lived: build was decoded as 8 bits shifted by 8 instead of
// 12 bits shifted by 4, so 1.6.1170 looked for versionlib-1-6-73-0.bin, no
// database loaded, and every stable ID silently resolved to 0.
struct VersionCase {
    std::uint32_t packed;
    unsigned      maj, min, build, sub;
    const char*   label;
};

// Values from references/skse64-master/skse64_common/skse_version.h.
const VersionCase kVersionCases[] = {
    {0x01064920, 1, 6, 1170, 0, "1.6.1170 (Steam -- the build in use)"},
    {0x010646A0, 1, 6, 1130, 0, "1.6.1130"},
    {0x01062750, 1, 6,  629, 0, "1.6.629"},
    {0x010613D0, 1, 6,  317, 0, "1.6.317 (AE)"},
    {0x01050610, 1, 5,   97, 0, "1.5.97"},
    {0x01064921, 1, 6, 1170, 1, "1.6.1170 GOG (sub=1)"},
};

int TestVersionDecode() {
    std::printf("runtime version decode\n");
    int failures = 0;
    for (const auto& c : kVersionCases) {
        const unsigned maj   = (c.packed & 0xFF000000u) >> 24;
        const unsigned min   = (c.packed & 0x00FF0000u) >> 16;
        const unsigned build = (c.packed & 0x0000FFF0u) >> 4;
        const unsigned sub   = (c.packed & 0x0000000Fu);
        const bool ok = maj == c.maj && min == c.min && build == c.build && sub == c.sub;
        if (!ok) ++failures;
        std::printf("  %-5s 0x%08lX -> %u.%u.%u sub %u  %s\n",
                    ok ? "PASS" : "FAIL",
                    static_cast<unsigned long>(c.packed), maj, min, build, sub, c.label);
    }
    std::printf("\n");
    return failures;
}

}  // namespace

int main(int argc, char** argv) {
    // The decode check needs no database, so run it first and always.
    int decodeFailures = TestVersionDecode();

    if (argc < 2) {
        std::printf("usage: %s <path-to-versionlib.bin>\n", argv[0]);
        std::printf("(ran the version-decode checks only)\n");
        return decodeFailures ? 1 : 2;
    }

    const std::string dbPath = argv[1];
    std::printf("versionlib parser test\n  %s\n", dbPath.c_str());

    VersionDb db;
    if (!db.LoadFile(dbPath)) {
        std::printf("FAIL  could not parse the database\n");
        std::printf("      the parser refuses a desynced stream on purpose --\n"
                    "      a partial parse would yield wrong-but-plausible RVAs.\n");
        return 1;
    }

    std::printf("  %zu entries\n\n", db.count());

    // Entry counts from the Python reference parser (tools/disasm/address_lib.py).
    // The database chooses the expectation set, so one harness covers formats
    // 2 and 5 -- pass either versionlib and the matching probes run.
    const bool is17104 = (db.count() == 435162);
    // Format 1 (1.5.97, 778674 entries) parses, and is checked only for that:
    // its ids are the SE generation, so the AE RVAs above do not apply.
    // See: docs/reference/address_library_formats.md#two-id-generations
    if (db.count() == 778674) {
        std::printf("OK  format 1 parsed (1.5.97); pre-AE ids are not used\n");
        return decodeFailures ? 1 : 0;
    }
    if (!is17104 && db.count() != 428461) {
        std::printf("FAIL  expected 428461 (1.6.1170), 435162 (1.7.104) or "
                    "778674 (1.5.97) entries, got %zu\n", db.count());
        return 1;
    }
    std::printf("  recognised as %s\n\n", is17104 ? "1.7.104 (format 5)"
                                                  : "1.6.1170 (format 2)");

    int failures = 0;
    const auto base = ModuleBase();  // 0 in this harness; we compare RVAs

    const Expect* expected = is17104 ? kExpected17104 : kExpected1170;
    const size_t  nexpected = is17104 ? std::size(kExpected17104)
                                      : std::size(kExpected1170);

    for (size_t i = 0; i < nexpected; ++i) {
        const auto& e = expected[i];
        const auto addr = db.Get(e.id);
        const auto rva  = addr ? (addr - base) : 0;
        const bool ok = (rva == e.rva);
        if (!ok) ++failures;
        std::printf("  %-5s id %-7llu %-22s got 0x%llx  want 0x%llx\n",
                    ok ? "PASS" : "FAIL",
                    static_cast<unsigned long long>(e.id), e.label,
                    static_cast<unsigned long long>(rva),
                    static_cast<unsigned long long>(e.rva));
    }

    failures += decodeFailures;
    std::printf("\n%s (%d failure(s))\n", failures ? "FAILED" : "OK", failures);
    return failures ? 1 : 0;
}
