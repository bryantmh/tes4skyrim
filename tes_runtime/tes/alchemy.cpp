#include "alchemy.h"

#include <cstdlib>
#include <sstream>

namespace tesruntime {

namespace {

// Each line of `text`, without a trailing '\r'.
std::vector<std::string> Lines(const std::string& text) {
    std::vector<std::string> out;
    std::istringstream in(text);
    std::string line;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        out.push_back(line);
    }
    return out;
}

std::vector<std::string> Split(const std::string& text, char at) {
    std::vector<std::string> out;
    std::istringstream in(text);
    std::string field;
    while (std::getline(in, field, at)) out.push_back(field);
    return out;
}

// The sidecar export's escaping (\\ \n \r \t) undone.
std::string Unescape(const std::string& text) {
    std::string out;
    for (std::size_t i = 0; i < text.size(); ++i) {
        if (text[i] != '\\' || i + 1 == text.size()) {
            out += text[i];
            continue;
        }
        const char next = text[++i];
        out += next == 'n' ? '\n' : next == 'r' ? '\r' : next == 't' ? '\t' : next;
    }
    return out;
}

void LegacyRow(const std::string& line, std::vector<ApparatusDef>* rows) {
    const std::size_t eq = line.find('=');
    const std::vector<std::string> fields =
        eq == std::string::npos ? std::vector<std::string>{} : Split(line.substr(eq + 1), '|');
    if (eq == 0 || fields.size() != 4) return;
    ApparatusDef def;
    def.id = line.substr(0, eq);
    def.file = fields[0];
    def.local = std::strtoul(fields[1].c_str(), nullptr, 16) & 0x00FFFFFF;
    def.type = std::atoi(fields[2].c_str());
    def.quality = std::strtof(fields[3].c_str(), nullptr);
    if (def.local && !def.file.empty() && def.type >= 0 && def.type < kApparatusTypes) {
        rows->push_back(def);
    }
}

void LegacySetting(const std::string& line, AlchemySettings* settings) {
    const std::size_t eq = line.find('=');
    const std::size_t comma = line.find(',', eq);
    if (eq == std::string::npos || comma == std::string::npos) return;
    const std::string name = line.substr(0, eq);
    const std::string value = line.substr(comma + 1);
    const float number = std::strtof(value.c_str(), nullptr);
    if (name == "fPotionStrengthMult") settings->inputs.strengthMult = number;
    else if (name == "fPotionT1MagMult") settings->inputs.magnitudeMult = number;
    else if (name == "fPotionT1DurMult") settings->inputs.durationMult = number;
    else if (name == "sInventoryMessage3") settings->inCombat = Unescape(value);
    else if (name == "sNotifyMessage45") settings->noMortar = Unescape(value);
}

// TES3's attribute order: Intelligence second, Luck last.
constexpr std::size_t kIntelligence = 1;
constexpr std::size_t kLuck = 7;

void LegacyPlayer(const std::string& line, AlchemySettings* settings) {
    const std::vector<std::string> fields = Split(line, '|');
    if (fields.size() < 2) return;
    const std::vector<std::string> attributes = Split(fields[fields.size() - 2], ',');
    if (attributes.size() <= kLuck) return;
    settings->inputs.intelligence = std::strtof(attributes[kIntelligence].c_str(), nullptr);
    settings->inputs.luck = std::strtof(attributes[kLuck].c_str(), nullptr);
}

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

void ReadLegacyApparatus(const std::string& appa, const std::string& gmst,
                         const std::string& npc, std::vector<ApparatusDef>* rows,
                         AlchemySettings* settings) {
    for (const std::string& line : Lines(appa)) LegacyRow(line, rows);
    for (const std::string& line : Lines(gmst)) LegacySetting(line, settings);
    for (const std::string& line : Lines(npc)) {
        if (line.rfind("player=", 0) == 0) LegacyPlayer(line, settings);
    }
}

}  // namespace tesruntime
