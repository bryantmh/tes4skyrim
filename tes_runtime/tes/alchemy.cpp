#include "alchemy.h"

namespace tesruntime {

namespace {

// OpenMW's Alchemy::applyTools, line for line, on `value`. The tool is the
// retort for a helpful effect and the alembic for a harmful one; with a
// calcinator the two combine, and a harmful effect DIVIDES by the result
// unless the calcinator stands alone.
float ApplyTools(const Toolset& tools, std::uint32_t flags, float value) {
    const bool magnitude = !(flags & kEffectNoMagnitude);
    const bool duration = !(flags & kEffectNoDuration);
    const bool negative = (flags & kEffectHostile) != 0;
    const int tool = negative ? kAlembic : kRetort;
    int setup = 0;
    if (tools.has[tool] && tools.has[kCalcinator]) setup = 1;
    else if (tools.has[tool]) setup = 2;
    else if (tools.has[kCalcinator]) setup = 3;
    else return value;
    const float toolQuality =
        setup == 1 || setup == 2 ? tools.quality[tool] : 0.0f;
    const float calcinatorQuality =
        setup == 1 || setup == 3 ? tools.quality[kCalcinator] : 0.0f;
    float quality = 1.0f;
    switch (setup) {
        case 1:
            quality = negative ? 2 * toolQuality + 3 * calcinatorQuality
                      : (magnitude && duration
                             ? 2 * toolQuality + calcinatorQuality
                             : 2 / 3.0f * (toolQuality + calcinatorQuality) +
                                   0.5f);
            break;
        case 2:
            quality = negative ? 1 + toolQuality
                      : (magnitude && duration ? toolQuality
                                               : toolQuality + 0.5f);
            break;
        default:
            quality = magnitude && duration ? calcinatorQuality
                                            : calcinatorQuality + 0.5f;
            break;
    }
    if (setup == 3 || !negative) return value + quality;
    return quality == 0 ? value : value / quality;
}

// Overrides `*out` with the number `doc` holds at `key`, when it holds one.
void TakeNumber(const Json& doc, const char* key, float* out) {
    if (doc[key].isNumber()) *out = static_cast<float>(doc[key].asNumber());
}

void TakeText(const Json& doc, const char* key, std::string* out) {
    if (doc[key].isString()) *out = doc[key].asString();
}

}  // namespace

void Toolset::Offer(int type, float value) {
    if (type < 0 || type >= kApparatusTypes) return;
    if (has[type] && value <= quality[type]) return;
    has[type] = true;
    quality[type] = value;
}

bool Toolset::Any() const {
    return has[kMortarPestle] || has[kAlembic] || has[kCalcinator] ||
           has[kRetort];
}

// OpenMW's potion value is `(x / fPotionT1MagMult) / baseCost` with `x` the
// alchemy factor times the mortar's quality times fPotionStrengthMult; the
// same with duration's GMST. The ratio divides out everything but the tools,
// so a quality-1.0 mortar and nothing else leaves Skyrim's number unchanged.
float ApparatusScale(const Toolset& tools, const AlchemyInputs& inputs,
                     std::uint32_t flags, float baseCost, bool magnitude) {
    const float mortar =
        tools.has[kMortarPestle] ? tools.quality[kMortarPestle] : 1.0f;
    const float divisor = magnitude ? inputs.magnitudeMult : inputs.durationMult;
    if (baseCost <= 0 || divisor <= 0) return mortar;
    const float factor = inputs.skill + 0.1f * inputs.intelligence +
                         0.1f * inputs.luck;
    const float neutral = factor * inputs.strengthMult / divisor / baseCost;
    if (neutral <= 0) return mortar;
    return ApplyTools(tools, flags, neutral * mortar) / neutral;
}

void ReadApparatus(const Json& doc, std::vector<ApparatusDef>* rows,
                   AlchemySettings* settings) {
    for (const Json& row : doc["apparatus"].items()) {
        ApparatusDef def;
        def.id = row["id"].asString();
        def.file = row["form"].at(0).asString();
        def.local = row["form"].at(1).asU32() & 0x00FFFFFF;
        def.type = row["type"].asInt(-1);
        def.quality = static_cast<float>(row["quality"].asNumber());
        if (def.local && !def.file.empty() && def.type >= 0 &&
            def.type < kApparatusTypes) {
            rows->push_back(def);
        }
    }
    const Json& gmst = doc["settings"];
    TakeNumber(gmst, "fPotionStrengthMult", &settings->inputs.strengthMult);
    TakeNumber(gmst, "fPotionT1MagMult", &settings->inputs.magnitudeMult);
    TakeNumber(gmst, "fPotionT1DurMult", &settings->inputs.durationMult);
    TakeText(gmst, "sInventoryMessage3", &settings->inCombat);
    TakeText(gmst, "sNotifyMessage45", &settings->noMortar);
    TakeNumber(doc["player"], "intelligence", &settings->inputs.intelligence);
    TakeNumber(doc["player"], "luck", &settings->inputs.luck);
}

}  // namespace tesruntime
