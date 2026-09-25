// SKSE plugin: widen Skyrim's Havok broad-phase world AABB past +/-64 cells.
//
// The +/-64-cell limit is a single float in .rdata, not a compare: the
// hkpWorldCinfo setup loads a broadcast quad of 3745.38232421875 havok metres
// (= 262144 game units = exactly 64 cells) as the broad-phase world extent.
// Objects outside that AABB clamp to hkpBroadPhaseBorder, which is what
// produces the hopping actors and the broken interactions far from origin.
//
// This plugin rewrites that one constant at load time. It locates it BY VALUE
// (the bit pattern is unique in the image), never by address, so it survives
// across game builds and is safe on Steam/GOG alike.
//
// See: docs/audits/worldspace_havok_range.md

#define _CRT_SECURE_NO_WARNINGS

#include <windows.h>

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

extern "C" IMAGE_DOS_HEADER __ImageBase;

namespace {

// 64 cells: 3745.38232421875 havok m * 69.9913 units/m == 262144.0 == 64*4096.
constexpr float kVanillaExtent = 3745.38232421875f;

// Companion constant in the same setup (907.0847778320312 m == 15.5 cells).
// Not patched; recorded so the log can prove we found the right site.
constexpr float kInnerExtent = 907.0847778320312f;

constexpr float kCellsPerVanilla = 64.0f;

struct Config {
    float cells = 128.0f;   // target half-extent, in cells
    bool  dry_run = false;
};

Config g_config;
HMODULE g_base = nullptr;
uint32_t g_runtime_version = 0;

// ---- logging ----------------------------------------------------------

FILE* g_log = nullptr;

void LogOpen() {
    char path[MAX_PATH]{};
    // Beside SKSE's own logs, under Documents/My Games.
    const char* home = std::getenv("USERPROFILE");
    if (!home) return;
    std::snprintf(path, sizeof(path),
                  "%s\\Documents\\My Games\\Skyrim Special Edition\\SKSE"
                  "\\HavokWorldSize.log", home);
    fopen_s(&g_log, path, "w");
}

void Log(const char* fmt, ...) {
    if (!g_log) return;
    va_list args;
    va_start(args, fmt);
    std::vfprintf(g_log, fmt, args);
    va_end(args);
    std::fputc('\n', g_log);
    std::fflush(g_log);
}

// ---- PE section walk --------------------------------------------------

struct Section {
    uint8_t* begin = nullptr;
    size_t   size = 0;
};

Section FindSection(HMODULE mod, const char* want) {
    auto* base = reinterpret_cast<uint8_t*>(mod);
    auto* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return {};
    auto* nt = reinterpret_cast<IMAGE_NT_HEADERS64*>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return {};

    auto* sec = IMAGE_FIRST_SECTION(nt);
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; ++i, ++sec) {
        char name[9]{};
        std::memcpy(name, sec->Name, 8);
        if (std::strcmp(name, want) == 0) {
            return {base + sec->VirtualAddress,
                    sec->Misc.VirtualSize};
        }
    }
    return {};
}

// ---- the patch --------------------------------------------------------

// The constant is stored as a broadcast quad: {v, v, v, 0.0f}. Matching the
// whole 16-byte shape (not just one float) is what makes the search unique --
// a bare float can appear inside unrelated data by coincidence.
bool IsBroadcastQuad(const float* p, float v) {
    return p[0] == v && p[1] == v && p[2] == v && p[3] == 0.0f;
}

int PatchExtent(float new_cells, bool dry_run) {
    Section rdata = FindSection(g_base, ".rdata");
    if (!rdata.begin) {
        Log("FAIL: no .rdata section");
        return -1;
    }
    Log(".rdata at %p size 0x%zx", rdata.begin, rdata.size);

    const float scale = new_cells / kCellsPerVanilla;
    const float new_extent = kVanillaExtent * scale;

    int patched = 0;
    int inner_seen = 0;

    // 16-byte aligned scan: an xmm constant is always 16-byte aligned.
    for (size_t off = 0; off + 16 <= rdata.size; off += 16) {
        auto* q = reinterpret_cast<float*>(rdata.begin + off);

        if (IsBroadcastQuad(q, kInnerExtent)) {
            ++inner_seen;
            Log("found inner extent %.6f at +0x%zx (not patched)",
                kInnerExtent, off);
            continue;
        }
        if (!IsBroadcastQuad(q, kVanillaExtent)) continue;

        Log("found world extent %.6f at +0x%zx  (= %.1f cells)",
            kVanillaExtent, off, kCellsPerVanilla);

        if (dry_run) {
            Log("  dry-run: would write %.6f (= %.1f cells)",
                new_extent, new_cells);
            ++patched;
            continue;
        }

        DWORD old_prot = 0;
        if (!VirtualProtect(q, 16, PAGE_READWRITE, &old_prot)) {
            Log("  FAIL: VirtualProtect (err %lu)", GetLastError());
            continue;
        }
        q[0] = new_extent;
        q[1] = new_extent;
        q[2] = new_extent;
        // q[3] stays 0.0f -- it is the unused w lane.
        VirtualProtect(q, 16, old_prot, &old_prot);

        Log("  patched -> %.6f  (= %.1f cells, %.0f game units)",
            new_extent, new_cells, new_extent * 69.99125f);
        ++patched;
    }

    Log("done: %d world-extent site(s), %d inner-extent site(s)",
        patched, inner_seen);
    return patched;
}

// ---- config -----------------------------------------------------------

void LoadConfig() {
    char path[MAX_PATH]{};
    GetModuleFileNameA(
        reinterpret_cast<HMODULE>(&__ImageBase), path, MAX_PATH);
    std::string ini(path);
    const size_t dot = ini.rfind('.');
    if (dot != std::string::npos) ini.resize(dot);
    ini += ".ini";

    char buf[64]{};
    GetPrivateProfileStringA("General", "fWorldCells", "128",
                             buf, sizeof(buf), ini.c_str());
    const float cells = static_cast<float>(std::atof(buf));
    if (cells >= kCellsPerVanilla && cells <= 4096.0f) {
        g_config.cells = cells;
    }
    g_config.dry_run =
        GetPrivateProfileIntA("General", "bDryRun", 0, ini.c_str()) != 0;

    Log("config: %s", ini.c_str());
    Log("  fWorldCells = %.1f", g_config.cells);
    Log("  bDryRun     = %d", static_cast<int>(g_config.dry_run));
}

}  // namespace

// ---- SKSE interface ---------------------------------------------------
//
// Declared inline rather than via the SKSE SDK so this builds standalone with
// nothing but MSVC. Only the layout of the version struct matters to SKSE.

struct SKSEPluginVersionData {
    uint32_t dataVersion;
    uint32_t pluginVersion;
    char     name[256];
    char     author[256];
    char     supportEmail[252];
    uint32_t versionIndependenceEx;
    uint32_t versionIndependence;
    uint32_t compatibleVersions[16];
    uint32_t seVersionRequired;
};

// SKSEVR / SKSE 2.0.x discover a plugin through SKSEPlugin_Query; SE/AE prefer
// the version struct above. Exporting BOTH is what makes one DLL load on every
// runtime, and is what TESRuntime.dll does.
// Layout mirrors tes_runtime/common/skse_abi.h.
struct PluginInfo {
    enum { kInfoVersion = 1 };
    uint32_t    infoVersion;
    const char* name;
    uint32_t    version;
};

struct SKSEInterface {
    uint32_t skseVersion;
    uint32_t runtimeVersion;
    uint32_t editorVersion;
    uint32_t isEditor;
    void*    (*QueryInterface)(uint32_t id);
    uint32_t (*GetPluginHandle)(void);
    uint32_t (*GetReleaseIndex)(void);
    const PluginInfo* (*GetPluginInfo)(const char* name);
};

// Flags say what makes this plugin version-independent, and both are true
// here: it touches no game struct, and it locates its target by scanning for a
// value rather than by any hardcoded address. That combination is what lets
// compatibleVersions stay empty (= runs on any build).
constexpr uint32_t kVersionIndependentEx_NoStructUse = 1 << 0;
constexpr uint32_t kVersionIndependent_Signatures = 1 << 1;

extern "C" __declspec(dllexport) SKSEPluginVersionData SKSEPlugin_Version = {
    1,                                  // dataVersion (kVersion)
    1,                                  // plugin version
    "HavokWorldSize",
    "TESConversion",
    "",
    kVersionIndependentEx_NoStructUse,
    kVersionIndependent_Signatures,
    {0},                                // compatibleVersions: any
    0,                                  // seVersionRequired: any
};

extern "C" __declspec(dllexport) bool SKSEPlugin_Query(const SKSEInterface* skse,
                                                       PluginInfo* info) {
    if (info) {
        info->infoVersion = PluginInfo::kInfoVersion;
        info->name = "HavokWorldSize";
        info->version = 1;
    }
    // Recorded, never gated on: the patch scans for a value, so it is correct
    // on any runtime that still contains the constant.
    if (skse) g_runtime_version = skse->runtimeVersion;
    return true;
}

extern "C" __declspec(dllexport) bool SKSEPlugin_Load(const SKSEInterface* skse) {
    LogOpen();
    Log("HavokWorldSize loading");
    if (skse) g_runtime_version = skse->runtimeVersion;
    if (g_runtime_version) {
        Log("runtime %u.%u.%u  (skse sees 0x%08X)",
            (g_runtime_version >> 24) & 0xFF,
            (g_runtime_version >> 16) & 0xFF,
            (g_runtime_version >> 8) & 0xFFF,
            g_runtime_version);
    }

    g_base = GetModuleHandleA(nullptr);
    if (!g_base) {
        Log("FAIL: GetModuleHandle(nullptr)");
        return true;                    // never block game start
    }

    LoadConfig();
    const int n = PatchExtent(g_config.cells, g_config.dry_run);
    if (n <= 0) {
        Log("WARNING: constant not found -- game build may differ. "
            "No changes made.");
    }
    return true;
}
