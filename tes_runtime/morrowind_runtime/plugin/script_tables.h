// What the result-script COMPILER has to be told about a name: whether it is
// a global, which locals a script declares, and which script an actor runs.
//
// All three are authored data, staged into the sidecar beside the dialogue
// (GLOB.txt, SCPT_locals.txt, SCPT_objects.txt) from its GLOB and SCPT and
// its TES3 masters'. The parser asks "is this a global?" BEFORE "is this an
// id?", so guessing here breaks `player->...` and `Script.member` outright.
// See: docs/commentary/morrowind_runtime.md#script-tables

#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace mwruntime {

// A GLOB record: 's', 'l' or 'f', and the value it starts at.
struct GlobalDef {
    char  type = 'f';
    float value = 0.0f;
};

// A script's declared locals, lowercased, in declaration order per type --
// which is the index the compiled code addresses them by.
struct ScriptLocals {
    std::vector<std::string> shorts;
    std::vector<std::string> longs;
    std::vector<std::string> floats;

    // 's', 'l', 'f', or ' ' when `name` is not declared.
    char TypeOf(const std::string& name) const;
};

// What an NPC_ record authors about an actor, which the filter compares by
// NAME, and the stats persuasion and barter read -- authored in the 52-byte
// NPDT or derived at import as OpenMW derives them: NPC_.txt, merged over
// the plugin's TES3 masters.
// See: docs/commentary/morrowind_runtime.md#npc-stats
struct ActorDef {
    std::string race;
    std::string clazz;
    std::string faction;
    int  rank = 0;
    int  disposition = 50;
    bool female = false;
    std::string name;
    int  level = 1;
    int  reputation = 0;
    int  personality = 0;
    int  luck = 0;
    int  speechcraft = 0;
    int  mercantile = 0;
    // ESM::NPC::Services bits; AllItems (0x2FFF) is what lists Barter.
    std::uint32_t services = 0;
    int  gold = 0;
    // The AIDT's authored Fight, Hello, Alarm and Flee, by filter.h's index:
    // what each reads until a script moves it.
    int  aiSettings[4] = {0, 0, 0, 0};
    // The eight attributes and 27 skills in TES3's own order, which is the
    // order of OpenMW's Get/Set/Mod opcode families.
    int  attributes[8] = {};
    int  skills[27] = {};
};

// One GMST of the chain: GMST.txt, `name=type,value`.
struct GmstDef {
    char  type = 'f';
    float number = 0.0f;
    std::string text;
};

// One SKIL record: SKIL.txt. `use` is how much a use of each kind advances
// the skill, in OpenMW's UseType order.
struct SkillDef {
    int   attribute = -1;
    int   specialization = 0;
    float use[4] = {0.0f, 0.0f, 0.0f, 0.0f};
};

// A Skyrim form behind a TES3 id: the plugin that owns it and its FormID
// there, resolved through the RUNNING load order. items_formid.txt and
// quests_formid.txt (journal quests).
struct FormRef {
    std::string plugin;
    std::uint32_t formId = 0;
};

// One place an NPC's travel service goes: NPC_travel.txt.
// See: docs/commentary/morrowind_runtime.md#travel
struct TravelDest {
    // The interior's cell name, or the exterior cell's shown name. Both are
    // keys of the cell anchor table.
    std::string name;
    bool  interior = false;
    float x = 0, y = 0, z = 0;
    // Degrees about Z, as the move hooks take it.
    float zRot = 0;
    // The persistent XMarker the export minted on this spot, when it did:
    // the only kind of reference that exists while its cell is unloaded.
    // See: docs/commentary/morrowind_runtime.md#travel-markers
    bool    hasMarker = false;
    FormRef marker;
};

// One row of FACT's rank table: what the player must reach to hold this rank.
struct RankReq {
    int attribute1 = 0;
    int attribute2 = 0;
    int primarySkill = 0;
    int favouredSkill = 0;
    int reputation = 0;
};

// A FACT record's requirement side: the two attributes and up to seven skills
// the faction judges by, a threshold row per rank, and the authored rank
// names `%PCRank` prints. FACT.txt.
struct FactionDef {
    // TES3 attribute indices, 0..7.
    int attribute[2] = {0, 0};
    // TES3 skill indices, 0..26; -1 for an unused slot.
    std::vector<int> skills;
    RankReq ranks[10];
    std::vector<std::string> rankNames;
};

void ClearScriptTables();

// Reads the three tables from one plugin's sidecar folder (trailing slash).
void LoadScriptTables(const std::string& pluginDir);

const std::unordered_map<std::string, GlobalDef>& GlobalDefs();
const GlobalDef* FindGlobal(const std::string& name);
const ScriptLocals* FindScriptLocals(const std::string& script);

// The script an actor runs, or "" -- by the actor's TES3 id.
const std::string& ScriptOf(const std::string& actor);

// One script's MWScript source, or "" -- SCPT_source.txt, by name. This is
// what the object-script tick compiles; dialogue carries its own source.
// See: docs/plans/morrowind_object_scripts.md#what-is-not-staged
const std::string& ScriptSource(const std::string& script);

// The global scripts TES3 starts by itself -- SSCR.txt, one per SSCR record.
const std::vector<std::string>& StartScripts();
std::size_t ScriptSourceCount();

// Every staged body, for the corpus sweep that measures what compiles.
const std::unordered_map<std::string, std::string>& ScriptSources();

// A placed reference's script instance: the script that PLACEMENT runs, by
// the placement's plugin-local FormID, or "" -- SCPT_instances.txt.
//
// 🛑 Keyed by the PLACEMENT, never the base: 798 scripted bases of TR_Mainland
// are placed more than once and one is placed 116 times, so a base key would
// give every copy one shared set of locals.
// See: docs/plans/morrowind_object_scripts.md#instances
const std::string& InstanceScript(const std::string& plugin,
                                  std::uint32_t localFormId);
std::size_t InstanceCount();

// One staged instance: which plugin placed it, that placement's plugin-local
// FormID, and the script it runs.
struct InstanceRow {
    std::string plugin;
    std::uint32_t localFormId = 0;
    // The TES3 id of the BASE it places, which a bare `Activate` acts on.
    std::string baseId;
    std::string script;
};

// Every staged instance, for the pass that resolves them through the running
// load order once the game can answer.
std::vector<InstanceRow> Instances();

// The staged instance whose placement has this plugin-LOCAL FormID, or null.
//
// 🛑 Keyed by the local id ALONE. Only a persistent reference exists before its
// cell loads, so an instance is found when the engine hands us a live ref, and
// all we have then is its FormID -- whose low 24 bits are what was staged.
// See: docs/plans/morrowind_object_scripts.md#only-persistent-refs-exist
const InstanceRow* InstanceByLocal(std::uint32_t localFormId);

const ActorDef* FindActor(const std::string& actor);
// Where `actor` offers to take the player, or null when it offers no travel.
const std::vector<TravelDest>* FindTravel(const std::string& actor);
const FormRef* FindItem(const std::string& item);
const FormRef* FindQuest(const std::string& quest);

// The SNDR a TES3 sound id names -- SOUN.txt. Null when the sound has no
// descriptor, which the caller treats as silence rather than as an error.
// See: docs/commentary/tes5_import_sound.md#the-runtime-sound-table
const FormRef* FindSound(const std::string& sound);
std::size_t SoundCount();

// One TES3 spell: the SPEL the import minted, and every TES3 effect INDEX it
// carries. `RemoveEffects` names one index and removes each spell containing
// it, so the whole list is needed to decide whether a spell matches. A
// Morroblivion-owned spell resolves but exports no effect data, leaving
// `effects` empty.
// See: docs/commentary/morrowind_runtime.md#spell-commands
struct SpellDef {
    FormRef form;
    std::vector<int> effects;

    bool Has(int index) const;
};

// The spell a TES3 id names -- SPEL.txt. Null when the chain defines none,
// which AddSpell and its kin treat as nothing to do.
const SpellDef* FindSpell(const std::string& spell);
std::size_t SpellCount();

// Every staged spell whose effect list holds `index`, by TES3 id -- what
// `RemoveEffects` dispels.
std::vector<std::string> SpellsWithEffect(int index);

// A magic effect by the name `GetEffect` writes, or by the TES3 INDEX
// `RemoveEffects` writes -- MGEF.txt carries both keys for that reason.
const FormRef* FindEffect(const std::string& name);
const FormRef* FindEffectByIndex(int index);
std::size_t EffectCount();

// The soul a creature carries, 1..5 in Skyrim's own enum, or 0 when the chain
// defines no such creature -- CREA_soul.txt. `AddSoulGem` names the CREATURE,
// so this is what picks which filled gem to add.
// See: docs/commentary/morrowind_runtime.md#soul-gems
int CreatureSoul(const std::string& creature);

// The staged id of `gem`'s FILLED variant for a soul of that size, or "" --
// SLGM.txt keys them `<gem id>_Filled<n>`. An ID, not a FormRef: every soul
// gem hook goes through AddItem/GetItemCount, which take the TES3 id.
std::string FilledSoulGemId(const std::string& gem, int soul);

// Every staged filled gem id holding a soul of that size, for the commands
// that search an inventory rather than name a gem.
std::vector<std::string> FilledSoulGemIds(int soul);
std::size_t SoulGemCount();

// The PLACED reference a TES3 id names, which `id->Command` acts on. Null
// when the plugin places none -- `player` is answered elsewhere.
// See: docs/commentary/morrowind_runtime.md#placed-references
const FormRef* FindRef(const std::string& id);
std::size_t RefCount();

// The BASE record a TES3 id names, which `PlaceAtPC` creates a reference from.
// Null when the chain defines no such id.
// See: docs/plans/morrowind_object_scripts.md#placeatpc
const FormRef* FindBase(const std::string& id);
std::size_t BaseCount();

// A reference standing INSIDE the cell of that name, which `PositionCell`
// moves its target to. Null when the chain staged no such cell, or when the
// cell holds no placement to aim at.
//
// 🛑 A reference, not the CELL record: Skyrim's mover takes another object.
// See: docs/commentary/morrowind_runtime.md#positioncell-needs-an-anchor
const FormRef* FindCellAnchor(const std::string& cell);
std::size_t CellCount();

// The quest that owns the AI package aliases, or null when none is staged.
// See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages
const FormRef* AiQuest();

// The ALST index of a named AI alias ("travelActor", "followTarget"), or -1.
int AiAliasIndex(const std::string& name);

// The PACK record staged for one kind ("travel", "follow"), or null --
// `GetCurrentAiPackage` compares the running package against these.
const FormRef* FindAiPack(const std::string& kind);
const FactionDef* FindFaction(const std::string& faction);

// A GMST by name, or null when the chain staged none of that name.
const GmstDef* FindGmst(const std::string& name);
// A GMST's float or int as a float, or `fallback` when absent.
float GmstNumber(const std::string& name, float fallback);
// A GMST's string, or `fallback` when absent.
std::string GmstText(const std::string& name, const std::string& fallback);
std::size_t GmstCount();

// A SKIL by TES3 skill index, 0..26, or null.
const SkillDef* FindSkill(int index);

// Registers one faction's requirements directly, so the filter's rank rules
// are testable without staging a sidecar.
void AddFactionForTest(const std::string& faction, const FactionDef& def);
std::size_t ActorCount();
std::size_t QuestCount();
std::size_t FactionCount();

// How many scripts and actor bindings are loaded, for the log.
std::size_t ScriptCount();
std::size_t ActorScriptCount();

}  // namespace mwruntime
