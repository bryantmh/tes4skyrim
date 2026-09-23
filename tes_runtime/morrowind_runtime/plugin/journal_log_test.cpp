// Headless gate for journal stage text: the cosave round trip and the
// journal's row rules, as read off its objective builder (0x98a6a0 on
// 1.6.1170).
// See: docs/commentary/morrowind_runtime.md#journal-stage-text

#include <cstdio>

#include "journal_log.h"

using namespace mwruntime;

namespace {

int g_failures = 0;

void Check(bool ok, const char* what) {
    std::printf("  [%s] %s\n", ok ? "ok" : "FAIL", what);
    if (!ok) ++g_failures;
}

const int kQuestA = 1;
const int kQuestB = 2;

ShownObjective Shown(const void* quest, std::uint16_t objective,
                     std::uint32_t state, std::uint32_t instance = 0) {
    ShownObjective entry;
    entry.quest = quest;
    entry.objective = objective;
    entry.state = state;
    entry.instance = instance;
    return entry;
}

void TestRoundTrip() {
    std::printf("cosave round trip\n");
    JournalLog log;
    log.Record({0x0100ABCD, 10, 0}, {20, 1});
    log.Record({0x0200EEEE, 5, 3}, {40, 0});
    JournalLog back;
    const std::size_t kept = back.Deserialize(
        log.Serialize(), [](std::uint32_t saved, std::uint32_t* now) {
            if ((saved >> 24) == 0x02) return false;
            *now = (saved & 0x00FFFFFF) | 0x05000000;
            return true;
        });
    Check(kept == 1, "a record whose plugin is gone is dropped");
    const StagePointer* moved = back.Find({0x0500ABCD, 10, 0});
    Check(moved && moved->stage == 20 && moved->logEntry == 1,
          "a record follows its quest to the new load order slot");
    Check(!back.Find({0x0100ABCD, 10, 0}), "the old FormID no longer answers");
    Check(back.Deserialize("xx", nullptr) == 0, "a short body loads nothing");
}

void TestRows() {
    std::printf("rows\n");
    const void* a = &kQuestA;
    const void* b = &kQuestB;
    std::vector<ShownObjective> shown = {
        Shown(a, 10, 3), Shown(b, 1, 1), Shown(a, 20, 1), Shown(a, 30, 0),
        Shown(a, 40, 7), Shown(a, 50, 5), Shown(a, 60, 1, 2)};
    const ShownObjective* first = ObjectiveAtRow(shown, a, 0, 0);
    Check(first && first->objective == 50, "the newest shown objective is row 0");
    const ShownObjective* second = ObjectiveAtRow(shown, a, 0, 1);
    Check(second && second->objective == 20,
          "dormant and state-7 objectives take no row");
    const ShownObjective* third = ObjectiveAtRow(shown, a, 0, 2);
    Check(third && third->objective == 10, "a completed objective keeps its row");
    Check(!ObjectiveAtRow(shown, a, 0, 3), "past the end is null");
    const ShownObjective* other = ObjectiveAtRow(shown, a, 2, 0);
    Check(other && other->objective == 60, "another instance has its own rows");
}

void TestOrAndMisc() {
    std::printf("OR and miscellaneous\n");
    const void* a = &kQuestA;
    std::vector<ShownObjective> shown = {Shown(a, 1, 1), Shown(a, 2, 1),
                                         Shown(a, 3, 1)};
    shown[2].orWithNext = true;
    const ShownObjective* row0 = ObjectiveAtRow(shown, a, 0, 0);
    Check(row0 && row0->objective == 2, "an OR objective joins the next row");
    std::vector<ShownObjective> misc = {Shown(a, 1, 3), Shown(a, 2, 1)};
    misc[0].questIsMisc = misc[1].questIsMisc = true;
    const ShownObjective* only = ObjectiveAtRow(misc, a, 0, 0);
    Check(only && only->objective == 2 && !ObjectiveAtRow(misc, a, 0, 1),
          "a miscellaneous quest lists only its active objectives");
}

}  // namespace

int main() {
    TestRoundTrip();
    TestRows();
    TestOrAndMisc();
    std::printf(g_failures ? "\nFAILED (%d)\n" : "\nOK\n", g_failures);
    return g_failures ? 1 : 0;
}
