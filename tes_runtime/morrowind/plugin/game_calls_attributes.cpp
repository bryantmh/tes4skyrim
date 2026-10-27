// Morrowind's attribute effects on every actor, and the character sheet's
// skill cap.
//
// Fortify, Drain, Damage, Restore and Absorb Attribute convert as script-less
// Script effects, one variant per attribute, so the engine's active-effect
// list holds each for exactly as long as it lasts. The tick sums them per
// actor and attribute as OpenMW's MagicEffects does: Fortify adds and Drain
// takes away while they last; Absorb takes from its target and gives to its
// caster; Damage lowers the attribute by its magnitude every second, and
// stays until Restore gives it back at its own rate. The stat reads add the
// result to the base (Hooks().attributeEffect), so scripts, dialogue,
// persuasion and the buffs all see it.
//
// The cap is Morrowind's trainer rule (sNotifyMessage17), widened: a skill at
// or above its governing attribute gains nothing from use and cannot be
// trained. Books and quest rewards still raise it, as in Morrowind.
// See: docs/commentary/morrowind_runtime.md#attribute-effects
// See: docs/commentary/morrowind_runtime.md#skill-cap

#include "game_calls_internal.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <unordered_map>
#include <utility>

#include "actor_stats.h"
#include "engine_ids.h"
#include "hook.h"
#include "ids.h"
#include "leveling.h"
#include "log.h"
#include "main_thread.h"
#include "object_tick.h"
#include "script_tables.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

constexpr const char* kPlayerId = "player";

// TES3 attribute effect indices.
constexpr int kDrainAttribute = 17;
constexpr int kDamageAttribute = 22;
constexpr int kRestoreAttribute = 74;
constexpr int kFortifyAttribute = 79;
constexpr int kAbsorbAttribute = 85;

// Damage accrued on an actor's attributes is kept under this prefix and its
// TES3 id, in the dialogue state so it rides the co-save: Morrowind keeps a
// damaged attribute damaged across a save.
constexpr const char* kDamageOwner = "attrdamage|";

using Attributes = std::array<float, kAttributeCount>;

// What active magic does to each actor's attributes now, by lowercase TES3
// id, rebuilt by every tick.
std::unordered_map<std::string, Attributes> g_effects;

// The NPCs an attribute effect landed on, by runtime FormID, until none is
// left on them; the player is always summed.
std::set<std::uint32_t> g_watched;

// Each Absorb by (target FormID, attribute) -> its caster's FormID. The
// active effect carries no caster we read, so the latest cast pays.
std::map<std::pair<std::uint32_t, int>, std::uint32_t> g_absorbs;

using AdvanceSkillFn = void (*)(void* player, std::uint32_t skill, float points,
                                void* form, std::uint32_t unk);
using TrainFn = void (*)(void* menu);

AdvanceSkillFn g_advanceSkill = nullptr;
TrainFn g_train = nullptr;

// Where PlayerCharacter keeps its PlayerSkills pointer, as AdvanceSkill loads
// it; 0 when that load is not the instruction it was.
std::size_t g_skillsOffset = 0;

// `mov rcx, [rcx + disp32]`, the load AdvanceSkill opens with.
constexpr std::uint8_t kLoadSkills[] = {0x48, 0x8b, 0x89};
constexpr int kSkyrimSkills = 18;

std::string DamageKey(int attribute) { return "a" + std::to_string(attribute); }

// Per attribute: what Fortify adds, what Drain and Absorb take, and Damage
// less Restore, a rate per second.
struct Sums {
    float add[kAttributeCount] = {};
    float take[kAttributeCount] = {};
    float damage[kAttributeCount] = {};
};

void Sum(Sums& sums, const RuntimeEffect& row, float magnitude) {
    const int a = row.attribute;
    if (a < 0 || a >= kAttributeCount) return;
    if (row.index == kFortifyAttribute) sums.add[a] += magnitude;
    if (row.index == kDrainAttribute || row.index == kAbsorbAttribute) sums.take[a] += magnitude;
    if (row.index == kDamageAttribute) sums.damage[a] += magnitude;
    if (row.index == kRestoreAttribute) sums.damage[a] -= magnitude;
}

// The damage an attribute carries after this tick: never below 0, never more
// than `most`, the base it damages.
float AccrueDamage(const std::string& actor, int attribute, float perSecond, float most) {
    const std::string owner = kDamageOwner + actor;
    const float was = State().Var(owner, DamageKey(attribute));
    const float now = std::clamp(was + perSecond * TickDelta(), 0.0f, std::max(0.0f, most));
    if (now != was) State().SetVar(owner, DamageKey(attribute), now);
    return now;
}

// The TES3 id the stat store keeps an actor under: "player", or its base
// NPC_'s id; "" for an actor no Morrowind record made.
std::string ActorKey(void* ref) {
    if (!IsActorRef(ref)) return std::string();
    if (ref == PlayerRef()) return kPlayerId;
    const char* id = SpeakerId(FormIdOf(At<void*>(ref, ids::kOffRefBase)));
    return id ? Lower(id) : std::string();
}

// Sums one actor's attribute effects into g_effects; true while any is
// active or any damage is left. `base` is what damage is capped by, the
// stat store's when null.
bool TickActor(void* ref, const std::string& actor, const Attributes* base = nullptr) {
    Sums sums;
    ForEachActiveEffect(ref, [&sums](const RuntimeEffect& row, float magnitude) {
        Sum(sums, row, magnitude);
    });
    Attributes& out = g_effects[actor];
    bool live = false;
    for (int a = 0; a < kAttributeCount; ++a) {
        const float most = base ? (*base)[a] : ActorBaseAttribute(actor, a);
        const float damaged = AccrueDamage(actor, a, sums.damage[a], most);
        out[a] += sums.add[a] - sums.take[a] - damaged;
        live = live || sums.add[a] || sums.take[a] || sums.damage[a] || damaged > 0.0f;
    }
    return live;
}

// A TES4 actor keeps its attributes as stat faction ranks (no Morrowind
// record made it). Magic moves each rank by its effect, and what is held on
// the rank is kept under this prefix and the FormID, so the authored base is
// always the rank less it and a save carries both.
// See: docs/commentary/morrowind_runtime.md#npc-attributes
constexpr const char* kRankOwner = "statfx|";
constexpr int kRankMax = 127;

void* StatFactionForm(int attribute) {
    static void* forms[kAttributeCount] = {};
    if (!forms[attribute]) forms[attribute] = Form(StatFaction(attribute));
    return forms[attribute];
}

// Holds a TES4 actor's attribute effects on its stat ranks; true while any
// effect or damage is left. False at once for an actor in no stat faction.
bool TickStatActor(void* ref, std::uint32_t id) {
    const std::string owner = kRankOwner + std::to_string(id);
    const std::string actor = "#" + std::to_string(id);
    Attributes base{};
    void* factions[kAttributeCount] = {};
    bool ranked = false;
    for (int a = 0; a < kAttributeCount; ++a) {
        void* faction = StatFactionForm(a);
        const int rank = faction ? FactionRank(ref, faction) : -1;
        if (rank < 0) continue;
        factions[a] = faction;
        base[a] = static_cast<float>(rank) - State().Var(owner, DamageKey(a));
        ranked = true;
    }
    if (!ranked) return false;
    const bool live = TickActor(ref, actor, &base);
    for (int a = 0; a < kAttributeCount; ++a) {
        if (!factions[a]) continue;
        const int target = std::clamp(static_cast<int>(std::lround(base[a] + g_effects[actor][a])),
                                      0, kRankMax);
        const float held = static_cast<float>(target) - base[a];
        if (held == State().Var(owner, DamageKey(a))) continue;
        if (SetFactionRank(ref, factions[a], target)) State().SetVar(owner, DamageKey(a), held);
    }
    return live;
}

// Each Absorb still on its target gives its magnitude to its caster.
void PayAbsorbs() {
    for (auto it = g_absorbs.begin(); it != g_absorbs.end();) {
        void* target = RefByRuntimeId(it->first.first);
        const float magnitude =
            target ? ActiveMagnitude(target, kAbsorbAttribute, it->first.second) : 0.0f;
        const std::string caster = ActorKey(RefByRuntimeId(it->second));
        if (magnitude <= 0.0f || caster.empty()) {
            it = g_absorbs.erase(it);
            continue;
        }
        g_effects[caster][it->first.second] += magnitude;
        ++it;
    }
}

float AttributeEffect(const std::string& actor, int tes3Index) {
    const auto found = g_effects.find(Lower(actor));
    if (found == g_effects.end() || tes3Index < 0 || tes3Index >= kAttributeCount) {
        return 0.0f;
    }
    return found->second[tes3Index];
}

void AdvanceSkillHook(void* player, std::uint32_t skill, float points, void* form,
                      std::uint32_t unk) {
    if (SkillCapped(static_cast<int>(skill))) return;
    g_advanceSkill(player, skill, points, form, unk);
}

// Refused before the trainer takes any gold, with Morrowind's own line.
void TrainHook(void* menu) {
    const std::uint32_t skill = At<std::uint32_t>(
        menu, IsVr() ? ids::kVrOffTrainingMenuSkill : ids::kOffTrainingMenuSkill);
    if (SkillCapped(static_cast<int>(skill))) {
        Notify(GmstText("sNotifyMessage17",
                        "You cannot train a skill above its governing attribute."));
        return;
    }
    g_train(menu);
}

std::size_t SkillsOffset(std::uintptr_t advance) {
    if (!advance) return 0;
    const auto* code = reinterpret_cast<const std::uint8_t*>(advance + ids::kAdvanceSkillLoadAt);
    if (std::memcmp(code, kLoadSkills, sizeof(kLoadSkills)) != 0) return 0;
    std::uint32_t disp = 0;
    std::memcpy(&disp, code + sizeof(kLoadSkills), sizeof(disp));
    return disp;
}

// The player's PlayerSkills data block, or null.
const std::uint8_t* SkillData() {
    void* player = PlayerRef();
    if (!player || !g_skillsOffset) return nullptr;
    void* skills = At<void*>(player, g_skillsOffset);
    return skills ? At<const std::uint8_t*>(skills, 0) : nullptr;
}

bool LevelProgress(float* points, float* most) {
    const std::uint8_t* data = SkillData();
    if (!data) return false;
    std::memcpy(points, data + ids::kLevelPoints, sizeof(float));
    std::memcpy(most, data + ids::kLevelPoints + sizeof(float), sizeof(float));
    return *most > 0.0f;
}

void FillLevelProgress() {
    RunOnGameThread([]() {
        float points = 0.0f;
        float most = 0.0f;
        if (!LevelProgress(&points, &most)) {
            Log("leveling: EnableLevelUpMenu -- the level bar cannot be read");
            return;
        }
        if (points < most) {
            std::memcpy(const_cast<std::uint8_t*>(SkillData()) + ids::kLevelPoints, &most,
                        sizeof(float));
        }
        Log("leveling: EnableLevelUpMenu fills the level bar (%g of %g)", points, most);
    });
}

// The player's {points, pointsMax} toward the next point of Skyrim skill
// `skill`, as a fraction.
float SkillProgress(const char* skill) {
    int av = -1;
    for (int i = ids::kFirstSkillValue; i < ids::kFirstSkillValue + kSkyrimSkills; ++i) {
        const char* name = SkillName(i);
        if (name && skill && _stricmp(name, skill) == 0) av = i;
    }
    const std::uint8_t* data = SkillData();
    if (av < 0 || !data) return -1.0f;
    const std::size_t at = ids::kSkillDataFirst +
                           static_cast<std::size_t>(av - ids::kFirstSkillValue) * ids::kSkillDataStride;
    float points = 0.0f, most = 0.0f;
    std::memcpy(&points, data + at + sizeof(float), sizeof(float));
    std::memcpy(&most, data + at + 2 * sizeof(float), sizeof(float));
    return most > 0.0f ? std::clamp(points / most, 0.0f, 1.0f) : -1.0f;
}

// The byte offset of the slot in `vtable` holding `fn`, or 0 when none does:
// the slot is found, not assumed, because VR's Actor has more virtuals.
std::size_t SlotOf(std::uintptr_t vtable, std::uintptr_t fn) {
    if (!vtable || !fn) return 0;
    const auto* slots = reinterpret_cast<const std::uintptr_t*>(vtable);
    for (std::size_t i = 1; i < ids::kMaxPlayerVirtuals; ++i) {
        if (slots[i] == fn) return i * sizeof(void*);
    }
    return 0;
}

void InstallSkillCap() {
    const std::uintptr_t advance =
        Resolve("PlayerCharacter::AdvanceSkill", ids::kPlayerAdvanceSkill, nullptr);
    const std::uintptr_t vtable =
        Resolve("PlayerCharacter vtable", tesruntime::ids::kPlayerVtable, nullptr);
    const std::size_t slot = SlotOf(vtable, advance);
    g_skillsOffset = SkillsOffset(advance);
    g_advanceSkill = slot ? reinterpret_cast<AdvanceSkillFn>(SwapVtableSlot(
                                "PlayerCharacter::AdvanceSkill", vtable, slot, advance,
                                reinterpret_cast<void*>(&AdvanceSkillHook)))
                          : nullptr;
    const std::uintptr_t caller =
        Resolve("TrainingMenu train caller", ids::kTrainingMenuTrainCaller, nullptr);
    const std::uintptr_t train = Resolve("TrainingMenu train", ids::kTrainingMenuTrain, nullptr);
    const std::uintptr_t site =
        caller && train ? FindCallTo(caller, ids::kTrainingCallerScan, train) : 0;
    if (site && PatchCall(site, reinterpret_cast<void*>(&TrainHook), "TrainingMenu train")) {
        g_train = reinterpret_cast<TrainFn>(train);
    }
    Log("attributes: skill cap on use %s, on trainers %s; skill progress at player+0x%zx",
        g_advanceSkill ? "hooked" : "NOT hooked", g_train ? "hooked" : "NOT hooked",
        g_skillsOffset);
}

// What each attribute global was last written, kept in the dialogue state so
// it rides the co-save beside the global itself: after a load the two agree,
// and only a script's write tells them apart.
constexpr const char* kGlobalOwner = "attrglobal|";

void SyncAttributeGlobals() {
    for (const AttributeGlobal& row : AttributeGlobals()) {
        const std::string key = row.form.plugin + '|' + std::to_string(row.form.formId & 0x00FFFFFF);
        float* slot = GlobalSlot(row.form.plugin, row.form.formId);
        if (!slot) continue;
        float& value = *slot;
        const float written = State().HasVar(kGlobalOwner, key) ? State().Var(kGlobalOwner, key)
                                                                : std::nanf("");
        value = SettleAttributeGlobal(row.attribute, value, written);
        State().SetVar(kGlobalOwner, key, value);
    }
}

}  // namespace

// The player every tick, since a save can carry an effect the apply sink never
// saw; every other actor until nothing is left on it. One whose reference is
// not loaded keeps its place and its damage; a loaded one no Morrowind record
// made (a TES4 NPC) holds its effects on its stat faction ranks, and one in
// none of them is let go.
void TickAttributeEffects(void* player) {
    g_effects.clear();
    if (!player || !SheetEnabled()) return;
    TickActor(player, kPlayerId);
    for (auto it = g_watched.begin(); it != g_watched.end();) {
        void* ref = RefByRuntimeId(*it);
        const std::string actor = ActorKey(ref);
        const bool foreign = actor.empty() && IsActorRef(ref);
        const bool done = foreign ? !TickStatActor(ref, *it)
                                  : !actor.empty() && !TickActor(ref, actor);
        if (done) {
            it = g_watched.erase(it);
        } else {
            ++it;
        }
    }
    PayAbsorbs();
}

void WatchAttributes(std::uint32_t actorId, std::uint32_t casterId, const RuntimeEffect& row) {
    if (!actorId) return;
    if (!PlayerRef() || actorId != FormIdOf(PlayerRef())) g_watched.insert(actorId);
    if (row.index == kAbsorbAttribute && casterId && casterId != actorId) {
        g_absorbs[{actorId, row.attribute}] = casterId;
    }
}

void InstallAttributeCalls(GameHooks& hooks) {
    hooks.attributeEffect = AttributeEffect;
    hooks.skillProgress = SkillProgress;
    hooks.levelProgress = LevelProgress;
    hooks.fillLevelProgress = FillLevelProgress;
    hooks.syncAttributeGlobals = SyncAttributeGlobals;
    InstallSkillCap();
}

}  // namespace gamecalls

// OpenMW checks the governing attribute's modified value, as magic leaves it.
bool SkillCapped(int skill) {
    if (!SkillCapEnabled() || !PlayerAttributesKnown() || !Hooks().baseActorValue) return false;
    const int attribute = GoverningAttribute(skill);
    const char* name = SkillName(skill);
    if (attribute < 0 || !name) return false;
    return Hooks().baseActorValue(gamecalls::kPlayerId, name) >=
           ActorAttribute(gamecalls::kPlayerId, attribute);
}

}  // namespace tesruntime::mw
