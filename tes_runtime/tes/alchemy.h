// Alchemy apparatus: which ones the player carries, and how much they
// strengthen or weaken each effect of the potion Skyrim's own menu brews.
//
// The strength is OpenMW's Alchemy::applyTools, ported line for line and
// taken as a RATIO against a quality-1.0 mortar and no other tool. Skyrim's
// formula, skill gain and potion value all stay; the apparatus only scales
// the magnitude and duration Skyrim computed.
// See: docs/commentary/tes_runtime_alchemy.md#alchemy-apparatus

#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "json.h"

namespace tesruntime {

// ESM::Apparatus::AppaType, the order the sidecar carries.
enum ApparatusType {
    kMortarPestle = 0,
    kAlembic = 1,
    kCalcinator = 2,
    kRetort = 3,
    kApparatusTypes = 4
};

// Skyrim MGEF DATA.Flags bits the ratio reads: TES3's Harmful, NoDuration and
// NoMagnitude, as the import maps them.
constexpr std::uint32_t kEffectHostile = 0x1;
constexpr std::uint32_t kEffectNoDuration = 0x200;
constexpr std::uint32_t kEffectNoMagnitude = 0x400;

// One apparatus a converted plugin owns, its quality on Morrowind's scale.
struct ApparatusDef {
    std::string id;
    std::string file;
    std::uint32_t local = 0;
    int type = kMortarPestle;
    float quality = 0.0f;
};

// The best of each type the player carries -- OpenMW's setAlchemist keeps the
// highest quality. A type the player lacks has `has` false.
struct Toolset {
    bool  has[kApparatusTypes] = {};
    float quality[kApparatusTypes] = {};

    void Offer(int type, float quality);
    bool Any() const;
};

// The player's stats and the GMSTs the ratio reads. The defaults are
// OpenMW's defaultgmsts values for the three potion GMSTs.
struct AlchemyInputs {
    float skill = 0.0f;
    float intelligence = 0.0f;
    float luck = 0.0f;
    float strengthMult = 0.5f;   // fPotionStrengthMult
    float magnitudeMult = 1.5f;  // fPotionT1MagMult
    float durationMult = 0.5f;   // fPotionT1DurMult
};

// What a Morrowind sidecar adds: its potion GMSTs, the `player` record's
// Intelligence and Luck, and the two messages OpenMW shows.
struct AlchemySettings {
    AlchemyInputs inputs;
    std::string inCombat;  // sInventoryMessage3
    std::string noMortar;  // sNotifyMessage45
};

// What one effect's magnitude (`magnitude` true) or duration is multiplied
// by: OpenMW's value with these tools over its value with a quality-1.0
// mortar alone. `flags` are the effect's DATA.Flags, `baseCost` its cost.
float ApparatusScale(const Toolset& tools, const AlchemyInputs& inputs,
                     std::uint32_t flags, float baseCost, bool magnitude);

// One `<plugin>.apparatus.json`: its rows are appended to `rows`, and any
// setting it carries overrides the one in `settings`.
void ReadApparatus(const Json& doc, std::vector<ApparatusDef>* rows,
                   AlchemySettings* settings);

// DEPRECATED, to be removed: one pre-split MorrowindRuntime\<plugin>\ folder's
// APPA.txt (`id=Plugin.esm|FormID|type|quality` lines), GMST.txt
// (`name=type,value`) and NPC_.txt (whose `player=` line ends in its
// comma-joined attributes, then its skills), read into the same shapes.
// See: docs/reference/tes_runtime_fragments.md#legacy-sidecar-paths
void ReadLegacyApparatus(const std::string& appa, const std::string& gmst,
                         const std::string& npc, std::vector<ApparatusDef>* rows,
                         AlchemySettings* settings);

// Resolves the sidecars, swaps the inventory, crafting-menu and
// potion-strength virtuals. Nothing is hooked when no plugin staged an
// apparatus. Needs InstallCrafting first.
void InstallAlchemy();

}  // namespace tesruntime
