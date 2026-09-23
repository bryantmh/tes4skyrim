#include "alchemy.h"

#include <cstdlib>
#include <sstream>

#include "store.h"

namespace mwruntime {

namespace {

constexpr const char* kFileApparatus = "APPA.txt";

// The four `|` fields after `id=`.
constexpr std::size_t kApparatusFields = 4;

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

// Splits `text` at every `|`.
std::vector<std::string> Fields(const std::string& text) {
    std::vector<std::string> out;
    std::string field;
    std::istringstream in(text);
    while (std::getline(in, field, '|')) out.push_back(field);
    return out;
}

// One APPA.txt line, or false when it is blank or malformed.
bool ParseRow(const std::string& line, ApparatusDef* out) {
    const std::size_t eq = line.find('=');
    if (eq == 0 || eq == std::string::npos) return false;
    const std::vector<std::string> fields = Fields(line.substr(eq + 1));
    if (fields.size() != kApparatusFields) return false;
    out->id = line.substr(0, eq);
    out->form.plugin = fields[0];
    out->form.formId = std::strtoul(fields[1].c_str(), nullptr, 16);
    out->type = std::atoi(fields[2].c_str());
    out->quality = std::strtof(fields[3].c_str(), nullptr);
    return out->form.formId && !out->form.plugin.empty() &&
           out->type >= 0 && out->type < kApparatusTypes;
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

std::vector<ApparatusDef> ParseApparatus(const std::string& text) {
    std::vector<ApparatusDef> rows;
    std::istringstream in(text);
    std::string line;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        ApparatusDef row;
        if (ParseRow(line, &row)) rows.push_back(row);
    }
    return rows;
}

std::vector<ApparatusDef> LoadApparatusFrom(const std::string& rootIn) {
    std::vector<ApparatusDef> rows;
    if (rootIn.empty()) return rows;
    std::string root = rootIn;
    if (root.back() != '\\' && root.back() != '/') root.push_back('\\');
    for (const std::string& plugin : SidecarPlugins(root)) {
        const std::vector<ApparatusDef> own =
            ParseApparatus(ReadFile(root + plugin + "\\" + kFileApparatus));
        rows.insert(rows.end(), own.begin(), own.end());
    }
    return rows;
}

}  // namespace mwruntime
