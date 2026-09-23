// Headless gate for the apparatus ratio and the APPA.txt parser: every
// expected number is OpenMW's Alchemy::applyTools worked by hand.
// See: docs/commentary/morrowind_runtime.md#alchemy-apparatus

#include <cmath>
#include <cstdio>

#include "alchemy.h"

using namespace mwruntime;

namespace {

int g_failures = 0;

void Check(bool ok, const char* what) {
    std::printf("  [%s] %s\n", ok ? "ok" : "FAIL", what);
    if (!ok) ++g_failures;
}

bool Near(float a, float b) { return std::fabs(a - b) < 1e-4f; }

// Skill 50 and no attributes: OpenMW's pre-tool value for a cost-1 effect is
// 50 * 0.5 / 1.5 = 16.6667 for magnitude and 50 * 0.5 / 0.5 = 50 for duration.
AlchemyInputs Inputs() {
    AlchemyInputs in;
    in.skill = 50.0f;
    return in;
}

constexpr float kMagnitudeBase = 50.0f * 0.5f / 1.5f;

void TestNoTools() {
    std::printf("no tools\n");
    Toolset none;
    Check(Near(ApparatusScale(none, Inputs(), 0, 1.0f, true), 1.0f),
          "nothing carried leaves Skyrim's value");
    Check(!none.Any(), "an empty toolset holds nothing");
}

void TestMortar() {
    std::printf("mortar\n");
    Toolset tools;
    tools.Offer(kMortarPestle, 2.0f);
    Check(Near(ApparatusScale(tools, Inputs(), 0, 1.0f, true), 2.0f),
          "a 2.0 mortar doubles");
    Check(Near(ApparatusScale(tools, Inputs(), 0, 0.0f, true), 2.0f),
          "a zero-cost effect still takes the mortar");
    tools.Offer(kMortarPestle, 0.5f);
    Check(Near(tools.quality[kMortarPestle], 2.0f), "the better mortar stays");
}

void TestRetort() {
    std::printf("retort (helpful)\n");
    Toolset tools;
    tools.Offer(kRetort, 1.0f);
    Check(Near(ApparatusScale(tools, Inputs(), 0, 1.0f, true),
               (kMagnitudeBase + 1.0f) / kMagnitudeBase),
          "adds its quality to a magnitude-and-duration effect");
    Check(Near(ApparatusScale(tools, Inputs(), kEffectNoDuration, 1.0f, true),
               (kMagnitudeBase + 1.5f) / kMagnitudeBase),
          "adds quality + 0.5 when the effect has no duration");
    Check(Near(ApparatusScale(tools, Inputs(), kEffectHostile, 1.0f, true),
               1.0f), "does nothing to a harmful effect");
}

void TestAlembic() {
    std::printf("alembic (harmful)\n");
    Toolset tools;
    tools.Offer(kAlembic, 1.0f);
    Check(Near(ApparatusScale(tools, Inputs(), kEffectHostile, 1.0f, true),
               0.5f), "divides by 1 + quality");
    tools.Offer(kCalcinator, 1.0f);
    Check(Near(ApparatusScale(tools, Inputs(), kEffectHostile, 1.0f, true),
               0.2f), "with a calcinator divides by 2a + 3c");
}

void TestCalcinator() {
    std::printf("calcinator\n");
    Toolset tools;
    tools.Offer(kCalcinator, 1.0f);
    Check(Near(ApparatusScale(tools, Inputs(), kEffectHostile, 1.0f, true),
               (kMagnitudeBase + 1.0f) / kMagnitudeBase),
          "alone, ADDS to a harmful effect");
    tools.Offer(kRetort, 1.0f);
    Check(Near(ApparatusScale(tools, Inputs(), 0, 1.0f, true),
               (kMagnitudeBase + 3.0f) / kMagnitudeBase),
          "with a retort adds 2r + c");
    Check(Near(ApparatusScale(tools, Inputs(), kEffectNoMagnitude, 1.0f, true),
               (kMagnitudeBase + 2 / 3.0f * 2.0f + 0.5f) / kMagnitudeBase),
          "with a retort, one-sided effect adds 2/3(r + c) + 0.5");
}

void TestParse() {
    std::printf("APPA.txt\n");
    const auto rows = ParseApparatus(
        "apparatus_a_mortar_01=Morrowind.esm|0000A001|0|0.5\r\n"
        "\n"
        "broken=Morrowind.esm|0000A002|0\n"
        "badtype=Morrowind.esm|0000A003|7|1.0\n"
        "MortarPestle01=Oblivion.esm|000105E3|0|0.15\n");
    Check(rows.size() == 2, "two good rows, malformed ones skipped");
    Check(rows.size() == 2 && rows[0].form.plugin == "Morrowind.esm" &&
              rows[0].form.formId == 0xA001 && Near(rows[0].quality, 0.5f),
          "plugin, FormID and quality read");
    Check(rows.size() == 2 && rows[1].id == "MortarPestle01", "id kept");
}

}  // namespace

int main() {
    TestNoTools();
    TestMortar();
    TestRetort();
    TestAlembic();
    TestCalcinator();
    TestParse();
    std::printf(g_failures ? "\nFAILED (%d)\n" : "\nOK\n", g_failures);
    return g_failures ? 1 : 0;
}
