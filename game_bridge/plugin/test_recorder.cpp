// Offline test for the flight recorder: the RTTI walk against a real exe, and
// the event decoders against fake forms.
//
// The walk is the part that decides which function pointers get replaced, and
// a wrong answer would send engine events into the wrong code. So it runs here
// on the same exe the game uses, mapped the way SKSE maps plugins for version
// checks (LOAD_LIBRARY_AS_IMAGE_RESOURCE: sections at their RVAs, pointers NOT
// relocated -- hence RttiClass::Load's separate pointer base), and must find
// the vtables tools/disasm/skyrim_disasm.py lists for SkyrimVM.
//
// Build and run (from game_bridge\plugin, in an MSVC x64 prompt):
//   cl /nologo /EHa /std:c++17 test_recorder.cpp recorder.cpp rtti.cpp ^
//      addresses.cpp log.cpp shell32.lib ole32.lib
//   test_recorder.exe "<path to SkyrimSE.exe, 1.6.1170>"

#include <windows.h>

#include <cstdio>
#include <cstring>
#include <string>

#include "recorder.h"
#include "rtti.h"

namespace bridge {
std::uint32_t RuntimeVersion() { return 0x01061170; }
}  // namespace bridge

using namespace bridge;

namespace {

int g_failures = 0;

void Check(bool ok, const char* what, const std::string& detail = "") {
    std::printf("[%s] %s%s%s\n", ok ? "PASS" : "FAIL", what,
                detail.empty() ? "" : " -- ", detail.c_str());
    if (!ok) ++g_failures;
}

struct ExpectVt {
    const char*   sink;
    std::uint32_t vtableRva;
    std::uint32_t processEventRva;
};

// From `skyrim_disasm.py` over the 1.6.1170 exe (the RTTI listing the
// recorder's walk must reproduce).
const ExpectVt kExpected1170[] = {
    {"TESActivateEvent",        0x1912600, 0x9c3740},
    {"TESCellFullyLoadedEvent", 0x1912678, 0x9c3f50},
    {"TESLoadGameEvent",        0x1912798, 0x9c5670},
    {"TESPackageEvent",         0x1912858, 0x9c6430},
    {"TESQuestStageEvent",      0x19128a0, 0x9c6b70},
    {"TESTopicInfoEvent",       0x1912990, 0x9c7e80},
    {"TESTriggerEnterEvent",    0x19129f0, 0x9c8850},
    {"TESTriggerLeaveEvent",    0x1912a08, 0x9c89f0},
};

void TestRtti(const char* exe) {
    HMODULE h = LoadLibraryExA(exe, nullptr, LOAD_LIBRARY_AS_IMAGE_RESOURCE);
    if (!h) {
        Check(false, "map exe", "LoadLibraryEx failed");
        return;
    }
    const auto image = reinterpret_cast<std::uintptr_t>(h) & ~std::uintptr_t{3};
    auto dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(image);
    auto nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(image + dos->e_lfanew);
    const std::uintptr_t preferred = nt->OptionalHeader.ImageBase;

    RttiClass vm;
    std::string why;
    const bool loaded = vm.Load(image, preferred, ".?AVSkyrimVM@@", &why);
    Check(loaded, "SkyrimVM RTTI", why);
    if (!loaded) return;
    Check(vm.bases().size() == 58, "SkyrimVM base count",
          std::to_string(vm.bases().size()));
    for (const auto& e : kExpected1170) {
        const std::string mangled = std::string(".?AV?$BSTEventSink@U") + e.sink + "@@@@";
        const std::uintptr_t vt = vm.BaseVtable(mangled.c_str());
        char got[96];
        std::snprintf(got, sizeof(got), "vtable rva %#llx",
                      static_cast<unsigned long long>(vt ? vt - image : 0));
        Check(vt && vt - image == e.vtableRva, e.sink, got);
        if (!vt) continue;
        const auto slot1 = *reinterpret_cast<const std::uintptr_t*>(vt + 8);
        Check(slot1 - preferred == e.processEventRva, "  slot 1 is ProcessEvent");
    }
    Check(vm.BaseVtable(".?AV?$BSTEventSink@UNoSuchEvent@@@@") == 0, "absent base -> 0");
    FreeLibrary(h);
}

// ---------------------------------------------------------------- decoders --

struct alignas(8) FakeForm {
    std::uint8_t bytes[0x48] = {};
    FakeForm(std::uint32_t id, std::uint8_t type) {
        std::memcpy(bytes + 0x14, &id, 4);
        bytes[0x1A] = type;
    }
    void SetBase(const FakeForm* base) { std::memcpy(bytes + 0x40, &base, 8); }
};

FakeForm g_quest(0x0F00104C, 0x4D);
FakeForm g_info(0x0F1057E8, 0x4C);
FakeForm g_npc(0x0F00ABCD, 0x2B);
FakeForm g_doc(0x0F001234, 0x3E);

void* FakeLookup(std::uint32_t id) {
    for (FakeForm* f : {&g_quest, &g_info, &g_npc, &g_doc}) {
        std::uint32_t fid;
        std::memcpy(&fid, f->bytes + 0x14, 4);
        if (fid == id) return f;
    }
    return nullptr;
}

template <std::size_t N>
std::string Decode(const char* kind, const std::uint8_t (&ev)[N], bool* kept = nullptr) {
    std::string out;
    const bool k = DecodeEventForTest(kind, ev, &FakeLookup, &out);
    if (kept) *kept = k;
    return out;
}

void Put32(std::uint8_t* p, std::uint32_t v) { std::memcpy(p, &v, 4); }
void PutPtr(std::uint8_t* p, const void* v) { std::memcpy(p, &v, 8); }

void TestDecoders() {
    g_doc.SetBase(&g_npc);

    alignas(8) std::uint8_t stage[0x10] = {};
    Put32(stage + 0x08, 0x0F00104C);
    stage[0x0C] = 105;
    std::string s = Decode("quest_stage", stage);
    Check(s == ",\"quest\":\"0F00104C\",\"stage\":105", "quest_stage decodes", s);

    alignas(8) std::uint8_t topic[0x20] = {};
    PutPtr(topic + 0x08, &g_doc);
    Put32(topic + 0x10, 0x0F1057E8);
    Put32(topic + 0x14, 1);
    s = Decode("topic_info", topic);
    Check(s == ",\"speaker\":\"0F001234\",\"speaker_base\":\"0F00ABCD\","
               "\"info\":\"0F1057E8\",\"phase\":\"end\"",
          "topic_info decodes speaker, base, info, phase", s);

    alignas(8) std::uint8_t wrong[0x10] = {};
    Put32(wrong + 0x08, 0x0F1057E8);
    s = Decode("quest_stage", wrong);
    Check(s.find("\"quest_check\":\"type 76\"") != std::string::npos &&
              s.find("\"raw\":\"") != std::string::npos &&
              s.find("\"id@8:0F1057E8:76\"") != std::string::npos,
          "a wrong form type is flagged with raw bytes and a form map", s);

    alignas(8) std::uint8_t bad[0x20] = {};
    PutPtr(bad + 0x08, reinterpret_cast<const void*>(0x10));
    Put32(bad + 0x10, 0x0F1057E8);
    s = Decode("topic_info", bad);
    Check(s.find("\"speaker_check\":\"unreadable\"") != std::string::npos,
          "an unreadable pointer is flagged, not dereferenced", s);

    alignas(8) std::uint8_t fault[0x20] = {};
    PutPtr(fault + 0x08, reinterpret_cast<const void*>(0x7FF000000000ull));
    s = Decode("topic_info", fault);
    Check(s.find("\"speaker_check\":\"unreadable\"") != std::string::npos,
          "an unmapped pointer faults inside the guard", s);

    alignas(8) std::uint8_t container[0x18] = {};
    Put32(container + 0x00, 0x0F000001);
    Put32(container + 0x04, 0x0F000002);
    bool kept = true;
    Decode("container", container, &kept);
    Check(!kept, "container changes not involving the player are dropped");
    Put32(container + 0x04, 0x14);
    s = Decode("container", container, &kept);
    Check(kept && s.find("\"to\":\"00000014\"") != std::string::npos,
          "player container changes are kept", s);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc > 1) TestRtti(argv[1]);
    else std::printf("[SKIP] RTTI walk: pass the path to a 1.6.1170 SkyrimSE.exe\n");
    TestDecoders();
    std::printf("%s (%d failure%s)\n", g_failures ? "FAILED" : "OK", g_failures,
                g_failures == 1 ? "" : "s");
    return g_failures ? 1 : 0;
}
