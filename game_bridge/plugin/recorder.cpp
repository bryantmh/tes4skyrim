// Flight recorder. See recorder.h for why and how.

#include "recorder.h"

#include <windows.h>
#include <shlobj.h>
#include <share.h>

#include <array>
#include <atomic>
#include <cctype>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <map>
#include <mutex>
#include <utility>

#include "addresses.h"
#include "commands.h"
#include "game.h"
#include "log.h"
#include "rtti.h"

namespace bridge {

namespace {

// ------------------------------------------------------------ form reads ----
//
// TESForm: formID at +0x14, formType at +0x1A (the Script ctor's own
// `mov byte [rbx+0x1A], 0x13` is the in-tree proof of the type offset).
// TESObjectREFR: base object at +0x40 (OBJ_REFR data.objectReference).

constexpr std::uint8_t kAny  = 0;
constexpr std::uint8_t kCELL = 0x3C;
constexpr std::uint8_t kREFR = 0x3D;
constexpr std::uint8_t kPHZD = 0x46;   // last of the reference types
constexpr std::uint8_t kINFO = 0x4C;
constexpr std::uint8_t kQUST = 0x4D;
constexpr std::uint8_t kPACK = 0x4F;
constexpr std::uint32_t kPlayerRef = 0x14;

FormLookupFn g_lookup = nullptr;

// Reads a form's id and type, or false if `p` is not readable. Kept free of
// C++ objects so it can carry its own __try.
bool ProbeForm(const void* p, std::uint32_t* id, std::uint8_t* type) {
    const auto a = reinterpret_cast<std::uintptr_t>(p);
    if (a < 0x10000 || (a & 7)) return false;
    __try {
        auto b = static_cast<const std::uint8_t*>(p);
        std::memcpy(id, b + 0x14, 4);
        *type = b[0x1A];
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool ProbePtr(const void* at, const std::uint8_t** out) {
    __try {
        std::memcpy(out, at, sizeof(*out));
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

const std::uint8_t* Lookup(std::uint32_t id) {
    if (!g_lookup || !id) return nullptr;
    __try {
        return static_cast<const std::uint8_t*>(g_lookup(id));
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return nullptr;
    }
}

// --------------------------------------------------------------- output ----
//
// A POD cursor over a stack buffer, so decoding needs no allocation and can
// run under __try.

struct Out {
    char*               p;
    char*               end;
    const std::uint8_t* ev;
    std::size_t         size;
    bool                suspect;
    bool                skip;
};

void Put(Out& o, const char* fmt, ...) {
    const std::ptrdiff_t room = o.end - o.p;
    if (room <= 1) return;
    va_list ap;
    va_start(ap, fmt);
    const int n = std::vsnprintf(o.p, static_cast<std::size_t>(room), fmt, ap);
    va_end(ap);
    if (n < 0) return;
    o.p += (n < room) ? n : room - 1;
}

void PutEscaped(Out& o, const char* s) {
    for (; *s && o.end - o.p > 8; ++s) {
        const unsigned char c = static_cast<unsigned char>(*s);
        if (c == '"' || c == '\\') { *o.p++ = '\\'; *o.p++ = static_cast<char>(c); }
        else if (c == '\n') { *o.p++ = '\\'; *o.p++ = 'n'; }
        else if (c == '\t') { *o.p++ = '\\'; *o.p++ = 't'; }
        else if (c < 0x20) Put(o, "\\u%04x", c);
        else *o.p++ = static_cast<char>(c);
    }
    *o.p = '\0';
}

std::uint32_t U32(const Out& o, std::size_t off) {
    std::uint32_t v;
    std::memcpy(&v, o.ev + off, 4);
    return v;
}

const std::uint8_t* Ptr(const Out& o, std::size_t off) {
    const std::uint8_t* v;
    std::memcpy(&v, o.ev + off, sizeof(v));
    return v;
}

void PutFormId(Out& o, const char* field, std::uint32_t id, std::uint8_t want) {
    Put(o, ",\"%s\":\"%08X\"", field, id);
    if (!id || !g_lookup) return;
    std::uint32_t got = 0;
    std::uint8_t type = 0;
    const std::uint8_t* f = Lookup(id);
    if (!f || !ProbeForm(f, &got, &type)) {
        Put(o, ",\"%s_check\":\"unresolved\"", field);
        o.suspect = true;
    } else if (want != kAny && type != want) {
        Put(o, ",\"%s_check\":\"type %u\"", field, type);
        o.suspect = true;
    }
}

void PutFormPtr(Out& o, const char* field, const std::uint8_t* f, std::uint8_t want) {
    std::uint32_t id = 0;
    std::uint8_t type = 0;
    if (!f) { Put(o, ",\"%s\":null", field); return; }
    if (!ProbeForm(f, &id, &type)) {
        Put(o, ",\"%s_check\":\"unreadable\"", field);
        o.suspect = true;
        return;
    }
    Put(o, ",\"%s\":\"%08X\"", field, id);
    if (want != kAny && type != want) {
        Put(o, ",\"%s_check\":\"type %u\"", field, type);
        o.suspect = true;
    }
}

bool IsPlayer(const std::uint8_t* ref) {
    std::uint32_t id = 0;
    std::uint8_t type = 0;
    return ref && ProbeForm(ref, &id, &type) && id == kPlayerRef;
}

// A reference plus its base object, which is what names it offline (most
// placed references carry no editor id of their own).
void PutRef(Out& o, const char* field, const std::uint8_t* ref) {
    std::uint32_t id = 0, baseId = 0;
    std::uint8_t type = 0, baseType = 0;
    if (!ref) { Put(o, ",\"%s\":null", field); return; }
    if (!ProbeForm(ref, &id, &type)) {
        Put(o, ",\"%s_check\":\"unreadable\"", field);
        o.suspect = true;
        return;
    }
    Put(o, ",\"%s\":\"%08X\"", field, id);
    if (type < kREFR || type > kPHZD) {
        Put(o, ",\"%s_check\":\"type %u\"", field, type);
        o.suspect = true;
        return;
    }
    const std::uint8_t* base = nullptr;
    if (ProbePtr(ref + 0x40, &base) && ProbeForm(base, &baseId, &baseType))
        Put(o, ",\"%s_base\":\"%08X\"", field, baseId);
}

void PutPhase(Out& o, std::uint32_t v, const char* const* names, std::uint32_t count) {
    if (v < count) Put(o, ",\"phase\":\"%s\"", names[v]);
    else {
        Put(o, ",\"phase\":%u,\"phase_check\":\"out of range\"", v);
        o.suspect = true;
    }
}

// When a check fails: the raw bytes, and every offset holding something that
// resolves to a form (as an id or as a pointer), with its type. That is enough
// to correct a layout from one bad line.
void Diagnose(Out& o) {
    Put(o, ",\"raw\":\"");
    for (std::size_t i = 0; i < o.size; ++i) Put(o, "%02X", o.ev[i]);
    Put(o, "\",\"forms\":[");
    const char* sep = "";
    std::uint32_t id = 0;
    std::uint8_t type = 0;
    for (std::size_t off = 0; off + 4 <= o.size; off += 4) {
        const std::uint8_t* f = Lookup(U32(o, off));
        if (f && ProbeForm(f, &id, &type)) {
            Put(o, "%s\"id@%zX:%08X:%u\"", sep, off, id, type);
            sep = ",";
        }
    }
    for (std::size_t off = 0; off + 8 <= o.size; off += 8) {
        if (ProbeForm(Ptr(o, off), &id, &type)) {
            Put(o, "%s\"ptr@%zX:%08X:%u\"", sep, off, id, type);
            sep = ",";
        }
    }
    Put(o, "]");
}

// ------------------------------------------------------------- decoders ----
//
// Layouts, checked against the reads SkyrimVM's own ProcessEvent makes in the
// unpacked 1.6.1170 exe (see game_bridge/README.md#flight-recorder). The
// topic-info speaker is at +08: +00 is a ref-counted callback (the handler bumps
// a count at [p+8], where a reference keeps its count at +0x28).
//
//   TESQuestStageEvent       +00 callback, +08 quest id, +0C u16 stage
//   TESQuestInitEvent        +00 quest id
//   TESTopicInfoEvent        +00 callback, +08 speaker ref, +10 INFO id,
//                            +14 0 begin / 1 end
//   TESPackageEvent          +00 actor ref, +08 PACK id, +0C 0 start/1 change/2 end
//   TESTriggerEnter/Leave    +00 trigger ref, +08 the ref that entered/left
//   TESActivateEvent         +00 activated ref, +08 activator ref
//   TESOpenCloseEvent        +00 ref, +08 activator ref, +10 bool opened
//   TESFurnitureEvent        +00 actor, +08 furniture, +10 0 enter / 1 exit
//   TESCellFullyLoadedEvent  +00 TESObjectCELL*
//   TESDeathEvent            +00 dying, +08 killer, +10 bool dead
//   TESCombatEvent           +00 actor, +08 target, +10 0 none/1 combat/2 search
//   TESEquipEvent            +00 actor, +08 item id, +0C ref id, +12 bool equipped
//   TESContainerChangedEvent +00 from id, +04 to id, +08 item id, +0C i32 count
//   TESActorLocationChangeEvent +00 actor, +08 old BGSLocation*, +10 new
//   TESLockChangedEvent      +00 ref

const char* const kBeginEnd[] = {"begin", "end"};
const char* const kPackagePhase[] = {"start", "change", "end"};
const char* const kFurniturePhase[] = {"enter", "exit"};
const char* const kCombatState[] = {"none", "combat", "searching"};

void DQuestStage(Out& o) {
    PutFormId(o, "quest", U32(o, 0x08), kQUST);
    std::uint16_t stage;
    std::memcpy(&stage, o.ev + 0x0C, 2);
    Put(o, ",\"stage\":%u", stage);
}

void DQuestInit(Out& o) { PutFormId(o, "quest", U32(o, 0x00), kQUST); }

void DTopicInfo(Out& o) {
    PutRef(o, "speaker", Ptr(o, 0x08));
    PutFormId(o, "info", U32(o, 0x10), kINFO);
    PutPhase(o, U32(o, 0x14), kBeginEnd, 2);
}

void DPackage(Out& o) {
    PutRef(o, "actor", Ptr(o, 0x00));
    PutFormId(o, "package", U32(o, 0x08), kPACK);
    PutPhase(o, U32(o, 0x0C), kPackagePhase, 3);
}

void DTrigger(Out& o) {
    PutRef(o, "trigger", Ptr(o, 0x00));
    PutRef(o, "actor", Ptr(o, 0x08));
}

void DActivate(Out& o) {
    PutRef(o, "target", Ptr(o, 0x00));
    PutRef(o, "by", Ptr(o, 0x08));
}

void DOpenClose(Out& o) {
    PutRef(o, "target", Ptr(o, 0x00));
    PutRef(o, "by", Ptr(o, 0x08));
    Put(o, ",\"opened\":%s", o.ev[0x10] ? "true" : "false");
}

void DFurniture(Out& o) {
    PutRef(o, "actor", Ptr(o, 0x00));
    PutRef(o, "furniture", Ptr(o, 0x08));
    PutPhase(o, U32(o, 0x10), kFurniturePhase, 2);
}

void DCell(Out& o) { PutFormPtr(o, "cell", Ptr(o, 0x00), kCELL); }

void DDeath(Out& o) {
    PutRef(o, "actor", Ptr(o, 0x00));
    PutRef(o, "killer", Ptr(o, 0x08));
    Put(o, ",\"dead\":%s", o.ev[0x10] ? "true" : "false");
}

void DCombat(Out& o) {
    PutRef(o, "actor", Ptr(o, 0x00));
    PutRef(o, "target", Ptr(o, 0x08));
    PutPhase(o, U32(o, 0x10), kCombatState, 3);
}

void DEquip(Out& o) {
    PutRef(o, "actor", Ptr(o, 0x00));
    PutFormId(o, "item", U32(o, 0x08), kAny);
    Put(o, ",\"equipped\":%s", o.ev[0x12] ? "true" : "false");
}

// Only the player's inventory: NPC outfits and leveled lists would otherwise
// bury everything else on each cell load.
void DContainer(Out& o) {
    const std::uint32_t from = U32(o, 0x00), to = U32(o, 0x04);
    if (from != kPlayerRef && to != kPlayerRef) { o.skip = true; return; }
    Put(o, ",\"from\":\"%08X\",\"to\":\"%08X\"", from, to);
    PutFormId(o, "item", U32(o, 0x08), kAny);
    Put(o, ",\"count\":%d", static_cast<std::int32_t>(U32(o, 0x0C)));
}

void DLocation(Out& o) {
    if (!IsPlayer(Ptr(o, 0x00))) { o.skip = true; return; }
    PutFormPtr(o, "from", Ptr(o, 0x08), kAny);
    PutFormPtr(o, "to", Ptr(o, 0x10), kAny);
}

void DLock(Out& o) { PutRef(o, "target", Ptr(o, 0x00)); }

void DNone(Out&) {}

using Decoder = void (*)(Out&);

struct Kind {
    const char* sink;   // event struct name, as in BSTEventSink<T>
    const char* name;   // "ev" in the log
    std::size_t size;   // bytes the decoder and the diagnosis may read
    Decoder     decode;
};

constexpr Kind kKinds[] = {
    {"TESQuestStageEvent",          "quest_stage",   0x10, DQuestStage},
    {"TESQuestInitEvent",           "quest_init",    0x04, DQuestInit},
    {"TESTopicInfoEvent",           "topic_info",    0x20, DTopicInfo},
    {"TESPackageEvent",             "package",       0x10, DPackage},
    {"TESTriggerEnterEvent",        "trigger_enter", 0x10, DTrigger},
    {"TESTriggerLeaveEvent",        "trigger_leave", 0x10, DTrigger},
    {"TESActivateEvent",            "activate",      0x10, DActivate},
    {"TESOpenCloseEvent",           "open_close",    0x18, DOpenClose},
    {"TESFurnitureEvent",           "furniture",     0x18, DFurniture},
    {"TESCellFullyLoadedEvent",     "cell_loaded",   0x08, DCell},
    {"TESDeathEvent",               "death",         0x18, DDeath},
    {"TESCombatEvent",              "combat",        0x18, DCombat},
    {"TESEquipEvent",               "equip",         0x18, DEquip},
    {"TESContainerChangedEvent",    "container",     0x18, DContainer},
    {"TESActorLocationChangeEvent", "location",      0x18, DLocation},
    {"TESLockChangedEvent",         "lock_changed",  0x08, DLock},
    {"TESLoadGameEvent",            "load_game",     0x00, DNone},
};
constexpr std::size_t kKindCount = sizeof(kKinds) / sizeof(kKinds[0]);

bool DecodeGuarded(Decoder fn, Out* o) {
    __try {
        fn(*o);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool DiagnoseGuarded(Out* o) {
    __try {
        Diagnose(*o);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

// Decodes one event into `buf` (JSON fields, each starting with a comma).
// False if the decoder filtered it out.
bool Decode(std::size_t kind, const void* ev, char* buf, std::size_t cap) {
    buf[0] = '\0';
    Out o{buf, buf + cap, static_cast<const std::uint8_t*>(ev), kKinds[kind].size,
          false, false};
    if (!DecodeGuarded(kKinds[kind].decode, &o)) {
        o.p = buf;
        buf[0] = '\0';
        Put(o, ",\"decode\":\"fault\"");
        o.suspect = true;
    }
    if (o.skip) return false;
    if (o.suspect && !DiagnoseGuarded(&o)) Put(o, ",\"diagnose\":\"fault\"");
    return true;
}

// --------------------------------------------------------------- writer ----

std::mutex                               g_mutex;
FILE*                                    g_file = nullptr;
std::string                              g_path;
std::atomic<bool>                        g_enabled{true};
bool                                     g_installed = false;
std::uint64_t                            g_events = 0, g_bytes = 0, g_dropped = 0;
std::map<std::string, std::uint64_t>     g_counts;
std::vector<std::string>                 g_hooked, g_missing;
ULONGLONG                                g_t0 = 0;

// A runaway event storm must not fill the disk during a long session.
constexpr std::uint64_t kMaxBytes = 512ull << 20;

void WriteLine(const char* ev, const char* fields) {
    SYSTEMTIME st;
    GetLocalTime(&st);
    char head[128];
    const int n = std::snprintf(head, sizeof(head),
                                "{\"t\":\"%02u:%02u:%02u.%03u\",\"ms\":%llu,\"ev\":\"%s\"",
                                st.wHour, st.wMinute, st.wSecond, st.wMilliseconds,
                                GetTickCount64() - g_t0, ev);
    std::lock_guard<std::mutex> lk(g_mutex);
    if (!g_file) return;
    if (g_bytes > kMaxBytes) { ++g_dropped; return; }
    std::fputs(head, g_file);
    std::fputs(fields, g_file);
    std::fputs("}\n", g_file);
    // Flushed per line: the runs this exists for often end in a crash, and
    // the last events before it are the ones that matter.
    std::fflush(g_file);
    g_bytes += static_cast<std::uint64_t>(n) + std::strlen(fields) + 2;
    ++g_events;
    ++g_counts[ev];
}

std::array<void*, kKindCount> g_orig{};

using ProcessEventFn = std::uint32_t (*)(void* self, const void* ev, void* src);

template <std::size_t I>
std::uint32_t Thunk(void* self, const void* ev, void* src) {
    if (g_enabled.load(std::memory_order_relaxed) && ev) {
        try {
            char buf[4096];
            if (Decode(I, ev, buf, sizeof(buf))) WriteLine(kKinds[I].name, buf);
        } catch (...) {
        }
    }
    return reinterpret_cast<ProcessEventFn>(g_orig[I])(self, ev, src);
}

template <std::size_t... I>
constexpr std::array<void*, sizeof...(I)> MakeThunks(std::index_sequence<I...>) {
    return {reinterpret_cast<void*>(&Thunk<I>)...};
}

const std::array<void*, kKindCount> kThunks =
    MakeThunks(std::make_index_sequence<kKindCount>{});

// Swaps one vtable slot. The original is stored BEFORE the swap, so a call
// that lands on the thunk the instant it is live already has somewhere to go.
bool PatchSlot(std::uintptr_t vtable, std::size_t slot, void* fn, void** orig) {
    auto p = reinterpret_cast<void**>(vtable + slot * sizeof(void*));
    DWORD old = 0;
    if (!VirtualProtect(p, sizeof(void*), PAGE_READWRITE, &old)) return false;
    void* cur = *p;
    *orig = cur;
    const bool ok = InterlockedCompareExchangePointer(p, fn, cur) == cur;
    VirtualProtect(p, sizeof(void*), old, &old);
    return ok;
}

bool OpenFile() {
    PWSTR docs = nullptr;
    if (FAILED(SHGetKnownFolderPath(FOLDERID_Documents, 0, nullptr, &docs))) return false;
    std::wstring dir = docs;
    CoTaskMemFree(docs);
    dir += L"\\My Games\\Skyrim Special Edition\\SKSE\\";
    CreateDirectoryW(dir.c_str(), nullptr);
    const std::wstring stem = dir + L"TESGameBridge_events";
    MoveFileExW((stem + L".1.jsonl").c_str(), (stem + L".2.jsonl").c_str(),
                MOVEFILE_REPLACE_EXISTING);
    MoveFileExW((stem + L".jsonl").c_str(), (stem + L".1.jsonl").c_str(),
                MOVEFILE_REPLACE_EXISTING);
    const std::wstring path = stem + L".jsonl";
    // _SH_DENYNO so the log can be read while the game still runs.
    g_file = _wfsopen(path.c_str(), L"wb", _SH_DENYNO);
    if (!g_file) return false;
    char narrow[MAX_PATH * 2] = {};
    WideCharToMultiByte(CP_UTF8, 0, path.c_str(), -1, narrow, sizeof(narrow), nullptr, nullptr);
    g_path = narrow;
    return true;
}

void WriteSessionHeader() {
    char buf[4096];
    Out o{buf, buf + sizeof(buf), nullptr, 0, false, false};
    buf[0] = '\0';
    const std::uint32_t v = RuntimeVersion();
    Put(o, ",\"plugin\":\"%s\",\"runtime\":\"%u.%u.%u\",\"hooked\":%zu,\"missing\":[",
        kPluginVersionString, v >> 24, (v >> 16) & 0xFF, (v >> 4) & 0xFFF,
        g_hooked.size());
    for (std::size_t i = 0; i < g_missing.size(); ++i)
        Put(o, "%s\"%s\"", i ? "," : "", g_missing[i].c_str());
    Put(o, "]");
    WriteLine("session", buf);
}

bool IsPapyrusProblem(const char* text) {
    char low[512];
    std::size_t n = 0;
    for (; text[n] && n + 1 < sizeof(low); ++n)
        low[n] = static_cast<char>(tolower(static_cast<unsigned char>(text[n])));
    low[n] = '\0';
    static const char* const kNeedles[] = {
        "error:", "warning:", "cannot be bound", "cannot open store", "cannot find",
        "unable to bind", "unable to call", "unable to find", "has no property",
        "has no function", "stack overflow", "assigning none", "attempted to call",
        "attempted to access",
    };
    for (const char* needle : kNeedles)
        if (std::strstr(low, needle)) return true;
    return false;
}

std::mutex    g_papyrusMutex;
int           g_framesLeft = 0;
ULONGLONG     g_windowStart = 0;
int           g_windowCount = 0;
std::uint64_t g_windowDropped = 0;

// A VM error storm (one bad OnUpdate every frame) must not drown the events.
constexpr int kPapyrusPerSecond = 100;
constexpr int kFramesPerError = 40;

// True if this line should be written, under the per-second budget.
bool AdmitPapyrus(const char* text, std::uint64_t* droppedBefore) {
    std::lock_guard<std::mutex> lk(g_papyrusMutex);
    const bool continuation = text[0] == '\t' || std::strncmp(text, "stack:", 6) == 0;
    if (IsPapyrusProblem(text) && !continuation) g_framesLeft = kFramesPerError;
    else if (continuation && g_framesLeft > 0) --g_framesLeft;
    else return false;
    const ULONGLONG now = GetTickCount64();
    if (now - g_windowStart >= 1000) {
        *droppedBefore = g_windowDropped;
        g_windowDropped = 0;
        g_windowStart = now;
        g_windowCount = 0;
    }
    if (++g_windowCount > kPapyrusPerSecond) {
        ++g_windowDropped;
        return false;
    }
    return true;
}

}  // namespace

bool InstallRecorder() {
    if (g_installed) return true;
    g_t0 = GetTickCount64();
    g_lookup = reinterpret_cast<FormLookupFn>(g_addr.lookupFormByID);
    if (!OpenFile()) {
        Log("recorder: could not open the event log; recorder disabled");
        return false;
    }
    const std::uintptr_t base = ModuleBase();
    RttiClass vm;
    std::string why;
    if (!vm.Load(base, base, ".?AVSkyrimVM@@", &why)) {
        Log("recorder: SkyrimVM RTTI not found (%s); recorder disabled", why.c_str());
        g_missing.push_back("SkyrimVM: " + why);
        WriteSessionHeader();
        return false;
    }
    for (std::size_t i = 0; i < kKindCount; ++i) {
        const std::string mangled = std::string(".?AV?$BSTEventSink@U") + kKinds[i].sink + "@@@@";
        const std::uintptr_t vt = vm.BaseVtable(mangled.c_str());
        if (!vt || !PatchSlot(vt, 1, kThunks[i], &g_orig[i])) {
            g_missing.push_back(kKinds[i].sink);
            Log("recorder: %s -- no sink vtable, not recorded", kKinds[i].sink);
            continue;
        }
        g_hooked.push_back(kKinds[i].sink);
        const auto orig = reinterpret_cast<std::uintptr_t>(g_orig[i]);
        std::uintptr_t tb = 0, te = 0;
        if (TextRange(tb, te) && (orig < base || orig >= te))
            Log("recorder: %s was already hooked by another plugin; chaining", kKinds[i].sink);
    }
    g_installed = true;
    WriteSessionHeader();
    Log("recorder: %zu sink(s) hooked, %zu missing -> %s", g_hooked.size(),
        g_missing.size(), g_path.c_str());
    return !g_hooked.empty();
}

void RecorderMessage(const char* kind, const char* saveName) {
    if (!g_installed || !g_enabled) return;
    char buf[1024];
    Out o{buf, buf + sizeof(buf), nullptr, 0, false, false};
    buf[0] = '\0';
    if (saveName) {
        Put(o, ",\"save\":\"");
        PutEscaped(o, saveName);
        Put(o, "\"");
    }
    WriteLine(kind, buf);
}

void RecorderPapyrus(const char* text) {
    if (!g_installed || !g_enabled || !text || !*text) return;
    std::uint64_t dropped = 0;
    const bool admit = AdmitPapyrus(text, &dropped);
    if (dropped) {
        char note[64];
        std::snprintf(note, sizeof(note), ",\"lines\":%llu", dropped);
        WriteLine("papyrus_dropped", note);
    }
    if (!admit) return;
    char buf[2048];
    Out o{buf, buf + sizeof(buf), nullptr, 0, false, false};
    buf[0] = '\0';
    Put(o, ",\"text\":\"");
    PutEscaped(o, text);
    Put(o, "\"");
    WriteLine("papyrus", buf);
}

void SetRecorderEnabled(bool on) {
    g_enabled = on;
    if (g_installed) WriteLine(on ? "recorder_on" : "recorder_off", "");
}

RecorderStats GetRecorderStats() {
    std::lock_guard<std::mutex> lk(g_mutex);
    RecorderStats s;
    s.installed = g_installed;
    s.enabled = g_enabled;
    s.path = g_path;
    s.events = g_events;
    s.bytes = g_bytes;
    s.dropped = g_dropped;
    for (const auto& kv : g_counts) s.perKind.emplace_back(kv.first, kv.second);
    s.hooked = g_hooked;
    s.missing = g_missing;
    return s;
}

bool DecodeEventForTest(const char* kind, const void* event, FormLookupFn lookup,
                        std::string* fields) {
    for (std::size_t i = 0; i < kKindCount; ++i) {
        if (std::strcmp(kKinds[i].name, kind) != 0) continue;
        const FormLookupFn saved = g_lookup;
        g_lookup = lookup;
        char buf[4096];
        const bool kept = Decode(i, event, buf, sizeof(buf));
        g_lookup = saved;
        *fields = buf;
        return kept;
    }
    *fields = ",\"error\":\"unknown kind\"";
    return false;
}

}  // namespace bridge
