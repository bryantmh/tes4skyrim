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
#include <functional>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace tesruntime::mw {

// A Skyrim form behind a TES3 id: the plugin that owns it and its FormID
// there, resolved through the RUNNING load order. items_formid.txt and
// quests_formid.txt (journal quests).
struct FormRef {
    std::string plugin;
    std::uint32_t formId = 0;
};

// A GLOB record: 's', 'l' or 'f', and the value it starts at.
struct GlobalDef {
    char  type = 'f';
    float value = 0.0f;
    // The converted plugin's GLOB mirroring this one, so a value Papyrus
    // writes reaches the scripts polling it. Empty plugin when unknown.
    // See: docs/commentary/morrowind_runtime.md#vanilla-morrowind-chargen
    FormRef form;
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
    // ESM::NPC::Services bits; AllItems (0x27FF) is what lists Barter.
    std::uint32_t services = 0;
    int  gold = 0;
    // The AIDT's authored Fight, Hello, Alarm and Flee, by filter.h's index:
    // what each reads until a script moves it.
    int  aiSettings[4] = {0, 0, 0, 0};
    // The eight attributes and 27 skills in TES3's own order, which is the
    // order of OpenMW's Get/Set/Mod opcode families.
    int  attributes[8] = {};
    int  skills[27] = {};
    // A CREA: no race, class, faction or sex, and it answers only the
    // responses that name it.
    bool creature = false;
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

// One RACE record's starting attributes in TES3 order, by sex: RACE.txt, keyed
// by each Skyrim race a player of it wears (its own and its vampire race).
// See: docs/commentary/morrowind_runtime.md#race-attributes
struct RaceDef {
    std::string id;
    int male[8] = {};
    int female[8] = {};
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

// One Divine or Temple marker an Intervention can land on: markers_formid.txt.
// See: docs/commentary/morrowind_runtime.md#teleport-effects
struct TeleportMarker {
    // "divine" or "temple".
    std::string kind;
    // The WORLDSPACE of an exterior marker, the CELL of an interior one.
    FormRef place;
    float x = 0, y = 0, z = 0;
    // Degrees about Z, as the move hooks take it.
    float zRot = 0;
};

// Where an interior opens onto the world, as the crime pass walked its doors:
// anchors_formid.txt. The point stands in `world`.
struct CellAnchor {
    FormRef cell;
    FormRef world;
    float x = 0, y = 0;
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
    // The id as the plugin authors it ("Thieves Guild"), for display.
    std::string id;
    // TES3 attribute indices, 0..7.
    int attribute[2] = {0, 0};
    // TES3 skill indices, 0..26; -1 for an unused slot.
    std::vector<int> skills;
    RankReq ranks[10];
    std::vector<std::string> rankNames;
};

void ClearScriptTables();

// The plugin a sidecar folder (trailing slash) belongs to: its folder name.
std::string PluginOf(const std::string& pluginDir);

// The sidecar table reading every table shares: each `key=value` line of a
// file, a `sep`-separated field list, and a `Plugin.esm|FormID` value.
void ForEachTableRow(const std::string& path,
                     const std::function<void(const std::string&, const std::string&)>& row);
std::vector<std::string> SplitFields(const std::string& text, char sep);
FormRef ParseFormRefField(const std::string& value);

// Reads one plugin's sidecar folder (trailing slash) into its own layer. Every
// lookup below answers through the current layer's view (scope.h).
void LoadScriptTables(const std::string& pluginDir);

// `fn(name, def, layer)` for every sidecar's row of every global, which is
// what publishing each plugin's own mirror GLOB needs.
void ForEachGlobalRow(
    const std::function<void(const std::string&, const GlobalDef&, int)>& fn);
// `fn(def, layer)` for every sidecar's row of the global `name`.
void ForEachGlobalDef(const std::string& name,
                      const std::function<void(const GlobalDef&, int)>& fn);
std::size_t GlobalCount();
const GlobalDef* FindGlobal(const std::string& name);
// The layer whose row FindGlobal answers with: kEveryLayer for the engine's
// own clock globals, and also when no visible sidecar declares the name.
int GlobalLayer(const std::string& name);
// The lowercased name of every global the current layer sees.
std::vector<std::string> VisibleGlobalNames();
const ScriptLocals* FindScriptLocals(const std::string& script);

// The script an actor runs, or "" -- by the actor's TES3 id.
const std::string& ScriptOf(const std::string& actor);

// One scripted item, and the sidecar binding it.
struct EquipWatch {
    std::string item;
    std::string script;
    int layer = -1;
};

// Every scripted item: TES3 runs the script of each one the player carries.
// The tick polls only those the player may hold (object_tick.cpp).
// See: docs/commentary/morrowind_runtime.md#engine-written-locals
const std::vector<EquipWatch>& EquipWatchList();

// One script's MWScript source, or "" -- SCPT_source.txt, by name. This is
// what the object-script tick compiles; dialogue carries its own source.
// See: docs/plans/morrowind_object_scripts.md#what-is-not-staged
const std::string& ScriptSource(const std::string& script);

// The sidecar whose body ScriptSource answers with, or -1.
int ScriptSourceLayer(const std::string& script);

// The global scripts TES3 starts by itself -- SSCR.txt, one per SSCR record --
// with the sidecar that staged each.
const std::vector<std::pair<int, std::string>>& StartScripts();
std::size_t ScriptSourceCount();

// Every body the current layer sees, for the corpus sweep that measures what
// compiles.
std::unordered_map<std::string, std::string> ScriptSources();

// A placed reference's script instance: the script that PLACEMENT runs, by
// the placement's plugin-local FormID, or "" -- SCPT_instances.txt.
//
// 🛑 Keyed by the PLACEMENT, never the base: 798 scripted bases of TR_Mainland
// are placed more than once and one is placed 116 times, so a base key would
// give every copy one shared set of locals.
// See: docs/plans/morrowind_object_scripts.md#instances
const std::string& InstanceScript(const std::string& plugin,
                                  std::uint32_t localFormId);
// The sidecar that staged that placement, or kEveryLayer when none did.
int InstanceLayer(const std::string& plugin, std::uint32_t localFormId);
std::size_t InstanceCount();

// One staged instance: the sidecar that staged it, which plugin placed it,
// that placement's plugin-local FormID, and the script it runs.
struct InstanceRow {
    int layer = -1;
    std::string plugin;
    std::uint32_t localFormId = 0;
    // The TES3 id of the BASE it places, which a bare `Activate` acts on.
    std::string baseId;
    std::string script;
    // The AUTHORED placement `SetAtStart` restores: x, y, z then the three
    // angles in DEGREES, which is what the SetAngle hook takes. All zero for a
    // row a sidecar wrote before the columns existed.
    // See: docs/commentary/morrowind_runtime.md#setatstart
    float placement[6] = {};
    bool hasPlacement = false;
};

// The staged instance at `index` in load order, or null past the end. The
// tick's discovery sweep walks this a slice at a time.
const InstanceRow* InstanceAt(std::size_t index);

// The staged instance whose placement has this plugin-LOCAL FormID, or null.
//
// 🛑 Keyed by the local id ALONE. Only a persistent reference exists before its
// cell loads, so an instance is found when the engine hands us a live ref, and
// all we have then is its FormID -- whose low 24 bits are what was staged.
// See: docs/plans/morrowind_object_scripts.md#only-persistent-refs-exist
const InstanceRow* InstanceByLocal(std::uint32_t localFormId);

// The AUTHORED placement of the reference `id` names, or null when that id is
// not a placed reference or its row predates the placement columns. What
// `SetAtStart` restores.
// See: docs/commentary/morrowind_runtime.md#setatstart
const float* FindPlacement(const std::string& id);

const ActorDef* FindActor(const std::string& actor);
// Where `actor` offers to take the player, or null when it offers no travel.
const std::vector<TravelDest>* FindTravel(const std::string& actor);
// An item by TES3 id; a filled soul gem's staged id (SLGM.txt) counts as one.
const FormRef* FindItem(const std::string& item);
// `fn(form, type)` for each weapon with its TES3 WPDT type, items_formid.txt's
// third field: what `GetWeaponType` answers.
void ForEachWeaponType(const std::function<void(const FormRef&, int)>& fn);
const FormRef* FindQuest(const std::string& quest);

// The SNDR a TES3 sound id names -- SOUN.txt. Null when the sound has no
// descriptor, which the caller treats as silence rather than as an error.
// See: docs/commentary/tes5_import_sound.md#the-runtime-sound-table
// One scripted `Say` line -- say_formid.txt. `topic` is a DIAL holding that
// single INFO, which is what ObjectReference.Say takes, and is 0 for a line no
// actor speaks; `sound` is the SOUN id a speaker with no mouth plays instead;
// `seconds` is the recording's measured length, 0 when it was not measurable.
// See: docs/commentary/morrowind_runtime.md#scripted-say
struct SayLineDef {
    FormRef topic;
    float seconds = 0;
    std::string sound;
};
const SayLineDef* FindSayLine(const std::string& file);

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
    // The TES3 SPDT type (OpenMW's ESM::Spell::SpellType), -1 when unstaged.
    int type = -1;

    bool Has(int index) const;
};

// TES3 spell types `GetCommonDisease` and `GetBlightDisease` sort by.
constexpr int kSpellBlight = 2;
constexpr int kSpellDisease = 3;
constexpr int kSpellCurse = 4;

// The spell a TES3 id names -- SPEL.txt. Null when the chain defines none,
// which AddSpell and its kin treat as nothing to do.
const SpellDef* FindSpell(const std::string& spell);
std::size_t SpellCount();

// Every staged spell whose effect list holds `index`, by TES3 id -- what
// `RemoveEffects` dispels.
std::vector<std::string> SpellsWithEffect(int index);

// Every staged spell of that TES3 type, by TES3 id.
std::vector<std::string> SpellsOfType(int type);

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
// Every creature CREA_soul.txt gives a soul, with that soul.
void ForEachCreatureSoul(const std::function<void(const std::string&, int)>& fn);

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

// Where the cell of that name IS, which `PositionCell` moves its target
// into: the CELL record of an interior, the WORLDSPACE of an exterior. Null
// when the chain staged no such cell.
const FormRef* FindCell(const std::string& cell);
std::size_t CellCount();
// The TES3 name of the exterior at grid (x, y): the cell's own name, else its
// region's. Null when the chain staged none there.
const std::string* FindExteriorName(int x, int y);
// The lowercased TES3 region the exterior at grid (x, y), or the quasi
// exterior of that name, lies in. Null outside every region.
// See: docs/commentary/morrowind_runtime.md#region-weather
const std::string* FindExteriorRegion(int x, int y);
const std::string* FindInteriorRegion(const std::string& cell);

// Every staged Intervention marker and interior anchor, once per sidecar that
// staged it: a dependent re-stages its masters' rows, which repeat harmlessly.
void ForEachTeleportMarker(const std::function<void(const TeleportMarker&)>& fn);
void ForEachCellAnchor(const std::function<void(const CellAnchor&)>& fn);
// A runtime-carried effect: its TES3 effect index, and for an attribute
// effect the TES3 attribute its variant targets (-1 for any other).
struct RuntimeEffect {
    int index = -1;
    int attribute = -1;
};

// Every MGEF a runtime-carried effect lands as: teleports_formid.txt, each
// delivery clone included. A row's value is `index`, or `index:attribute`
// for an attribute effect's per-attribute variant.
void ForEachTeleportEffect(
    const std::function<void(const FormRef&, const RuntimeEffect&)>& fn);
// Each child worldspace and its parent: worlds_formid.txt. A walled city
// shares its parent's coordinates, so an Intervention searches both as one.
void ForEachWorldParent(
    const std::function<void(const FormRef&, const FormRef&)>& fn);

// The quest that owns the AI package aliases, or null when none is staged.
// See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages
const FormRef* AiQuest();

// The ALST index of a named AI alias ("travelActor", "followTarget"), or -1.
int AiAliasIndex(const std::string& name);

// The PACK record staged for one kind ("travel", "follow"), or null --
// `GetCurrentAiPackage` compares the running package against these.
const FormRef* FindAiPack(const std::string& kind);
const FactionDef* FindFaction(const std::string& faction);

// The converted FACT a TES3 faction id names -- factions_formid.txt -- which
// the player is made a member of. Null when the chain defines none.
// See: docs/commentary/morrowind_runtime.md#player-factions
const FormRef* FindFactionForm(const std::string& faction);

// The crime faction of the realm -- crime_formid.txt -- whose bounty PC Crime
// Level reads when no speaker's own crime faction answers. Null when none.
// See: docs/commentary/morrowind_runtime.md#crime-is-the-engines
const FormRef* RealmCrimeFaction();

// A conversion-owned record a runtime effect acts through, by its
// effects_formid.txt name (`sanctuary`). Null when no sidecar staged one.
const FormRef* EffectForm(const std::string& name);

// One GLOB the export minted for a bark to test: `key` is `journal:<id>`,
// `cell:<prefix>`, `reputation` or `weather`, and the runtime writes that
// value into `form` -- state_formid.txt. Every plugin's rows are kept: two
// plugins testing the same thing each carry their own GLOB.
// See: docs/commentary/morrowind_runtime.md#published-state
struct StateRow {
    std::string key;
    FormRef form;
    // The sidecar whose view the value is read through.
    int layer = -1;
};
const std::vector<StateRow>& StateRows();

// A GMST by name, or null when the chain staged none of that name.
const GmstDef* FindGmst(const std::string& name);
// A GMST's float or int as a float, or `fallback` when absent.
float GmstNumber(const std::string& name, float fallback);
// A GMST's string, or `fallback` when absent.
std::string GmstText(const std::string& name, const std::string& fallback);
std::size_t GmstCount();

// A SKIL by TES3 skill index, 0..26, or null.
const SkillDef* FindSkill(int index);
// How many SKIL rows the sidecars staged: none means no game with attributes.
std::size_t SkillCount();

// One global a TES4 plugin's converted scripts read for the player's
// attribute (TES3 order, 0..7): attributes_formid.txt. Every plugin's rows
// are kept: each game's scripts hold their own.
// See: docs/commentary/morrowind_runtime.md#tes4-tables
struct AttributeGlobal {
    int attribute = -1;
    FormRef form;
};
const std::vector<AttributeGlobal>& AttributeGlobals();

// The hidden faction a TES4 actor's attribute (0..7) is its rank in --
// `faction.<av>` rows of attributes_formid.txt -- or null.
// See: docs/commentary/morrowind_runtime.md#npc-attributes
const FormRef* StatFaction(int attribute);

// The TES3 race a player wearing Skyrim race `skyrimRace` (runtime FormID)
// starts from, or null.
const RaceDef* FindRaceStart(std::uint32_t skyrimRace);

// Registers one faction's requirements directly, so the filter's rank rules
// are testable without staging a sidecar.
void AddFactionForTest(const std::string& faction, const FactionDef& def);
std::size_t ActorCount();
std::size_t QuestCount();

std::size_t FactionCount();

// How many scripts and actor bindings are loaded, for the log.
std::size_t ScriptCount();
std::size_t ActorScriptCount();

}  // namespace tesruntime::mw
