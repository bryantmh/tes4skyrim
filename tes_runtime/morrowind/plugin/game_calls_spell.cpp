// The spell natives: the actor's spell list, casting, and what is active on
// it right now.
// See: docs/commentary/morrowind_runtime.md#spell-commands

#include "game_calls_internal.h"

#include <cstdint>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

#include "hook.h"
#include "ids.h"
#include "log.h"
#include "main_thread.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

// Actor.AddSpell(Spell, bool verbose) and Actor.RemoveSpell(Spell), both
// returning whether the list changed.
using AddSpellFn = bool (*)(void* vm, std::uint32_t stack, void* actor,
                            void* spell, bool verbose);
using SpellQueryFn = bool (*)(void* vm, std::uint32_t stack, void* actor,
                              void* form);
// Spell.Cast(ObjectReference source, ObjectReference target) is a MEMBER
// function on the SPEL, so the spell is `self`.
using CastFn = void (*)(void* vm, std::uint32_t stack, void* spell,
                        void* source, void* target);

AddSpellFn   g_addSpell = nullptr;
SpellQueryFn g_removeSpell = nullptr;
SpellQueryFn g_hasSpell = nullptr;
SpellQueryFn g_hasMagicEffect = nullptr;
SpellQueryFn g_dispelSpell = nullptr;
CastFn       g_cast = nullptr;

// The SPEL a TES3 spell id or a `Plugin.esm@FormID` names, reporting one that
// resolves to nothing.
void* SpellForm(const std::string& spell) {
    const std::size_t at = spell.find('@');
    if (at != std::string::npos) {
        const FormRef ref = ParseFormRefField(spell.substr(0, at) + '|' + spell.substr(at + 1));
        void* form = Form(&ref);
        if (!form) ReportOnce("spell", spell);
        return form;
    }
    const SpellDef* def = FindSpell(spell);
    void* form = def ? Form(&def->form) : nullptr;
    if (!form) ReportOnce("spell", spell);
    return form;
}

// The MGEF a `GetEffect` name means, its `sEffect` prefix already off.
void* EffectForm(const std::string& effect) {
    void* form = Form(FindEffect(effect));
    if (!form) ReportOnce("effect", effect);
    return form;
}

void AddSpellTo(const std::string& actor, const std::string& spell) {
    void* ref = OwnerRef(actor);
    void* form = SpellForm(spell);
    if (!ref || !form || !g_addSpell) return;
    PostToMainThread([ref, form]() {
        g_addSpell(PapyrusVm(), 0, ref, form, false);
    });
}

void RemoveSpellFrom(const std::string& actor, const std::string& spell) {
    void* ref = OwnerRef(actor);
    void* form = SpellForm(spell);
    if (!ref || !form || !g_removeSpell) return;
    PostToMainThread([ref, form]() {
        g_removeSpell(PapyrusVm(), 0, ref, form);
    });
}

// `GetSpell`: the actor's spell LIST, which is read on the spot -- a script
// tests it and branches in the same statement.
bool KnowsSpell(const std::string& actor, const std::string& spell) {
    void* ref = OwnerRef(actor);
    void* form = SpellForm(spell);
    return ref && form && g_hasSpell && g_hasSpell(PapyrusVm(), 0, ref, form);
}

// Whether one MGEF is active on the actor; the one call both queries make.
bool FormActive(void* ref, void* form) {
    return ref && form && g_hasMagicEffect
           && g_hasMagicEffect(PapyrusVm(), 0, ref, form);
}

// `GetEffect`: by the name MGEF.txt keys, its `sEffect` prefix already off.
bool EffectActive(const std::string& actor, const std::string& effect) {
    return FormActive(OwnerRef(actor), EffectForm(effect));
}

// `GetSpellEffects`: whether the SPELL is acting on the actor. Skyrim answers
// per magic EFFECT, so ANY of the spell's own effects being active means it
// is. A spell whose owner exported no effect data answers false rather than
// guessing.
bool SpellIsActive(const std::string& actor, const std::string& spell) {
    const SpellDef* def = FindSpell(spell);
    void* ref = def && !def->effects.empty() ? OwnerRef(actor) : nullptr;
    if (!ref) return false;
    for (int index : def->effects) {
        if (FormActive(ref, Form(FindEffectByIndex(index)))) return true;
    }
    return false;
}

// `GetCommonDisease` / `GetBlightDisease`: OpenMW's `Spells::hasSpellType`,
// answered the way Skyrim's own GetDisease does -- an active effect whose
// source spell is a staged spell of that TES3 type. The staged forms are
// resolved only once the actor has an effect to compare.
// See: docs/commentary/morrowind_runtime.md#disease-queries
bool HasSpellOfType(const std::string& actor, int tes3Type) {
    void* ref = OwnerRef(actor);
    if (!ref || !IsActorRef(ref)) return false;
    std::set<std::uint32_t> wanted;
    bool built = false;
    return AnyEffectSource(ref, [&](std::uint32_t source) {
        if (!built) {
            built = true;
            for (const std::string& id : SpellsOfType(tes3Type)) {
                const SpellDef* def = FindSpell(id);
                void* form = def ? Form(&def->form) : nullptr;
                if (form) wanted.insert(FormIdOf(form));
            }
        }
        return wanted.count(source) != 0;
    });
}

// `RemoveSpellEffects`: just that spell's effects.
void DispelOneSpell(const std::string& actor, const std::string& spell) {
    void* ref = OwnerRef(actor);
    void* form = SpellForm(spell);
    if (!ref || !form || !g_dispelSpell) return;
    PostToMainThread([ref, form]() {
        g_dispelSpell(PapyrusVm(), 0, ref, form);
    });
}

// `RemoveEffects index`: every spell CONTAINING that effect comes off, which
// is what TES3 does -- the staged effect lists say which spells qualify, and
// each is dispelled AND removed so an ability stops re-applying itself.
// See: docs/commentary/morrowind_runtime.md#removeeffects-is-per-spell
void PurgeSpells(void* ref, const std::vector<std::string>& ids, bool remove,
                 int spare = -1) {
    std::vector<void*> forms;
    for (const std::string& id : ids) {
        const SpellDef* def = FindSpell(id);
        void* form = def && !(spare >= 0 && def->Has(spare)) ? Form(&def->form) : nullptr;
        if (form) forms.push_back(form);
    }
    if (!ref || forms.empty()) return;
    PostToMainThread([ref, forms, remove]() {
        for (void* form : forms) {
            if (g_dispelSpell) g_dispelSpell(PapyrusVm(), 0, ref, form);
            if (remove && g_removeSpell) g_removeSpell(PapyrusVm(), 0, ref, form);
        }
    });
}

void DispelByEffect(const std::string& actor, int index) {
    PurgeSpells(OwnerRef(actor), SpellsWithEffect(index), true);
}

void CastAt(const std::string& caster, const std::string& target,
            const std::string& spell) {
    void* from = OwnerRef(caster);
    void* at = OwnerRef(target);
    void* form = SpellForm(spell);
    if (!from || !at || !form) return;
    CastForm(form, from, at);
}

// --- soul gems -----------------------------------------------------------
//
// Each command names the CREATURE whose soul is trapped, and any gem holding
// a soul of that size answers: a synthesized filled record (AddSoulGem, or a
// plugin's placed loot), or an empty gem the player filled with Soul Trap,
// whose soul rides on its inventory stack as ExtraSoul.
// See: docs/commentary/morrowind_runtime.md#soul-gems

using ExtraListFn = void* (*)(void* list);
using ExtraListIntFn = std::int32_t (*)(void* list);
using RemoveItemFn = void* (*)(void* ref, std::uint32_t* handle, void* item,
                               std::int32_t count, std::int32_t reason,
                               void* list, void* moveTo, const float* dropAt,
                               const float* rotate);

ExtraListFn    g_inventoryChanges = nullptr;
ExtraListIntFn g_soulLevel = nullptr;
ExtraListIntFn g_stackCount = nullptr;

// One inventory stack holding a trapped soul: its gem and its ExtraDataList.
struct TrappedStack {
    void* gem = nullptr;
    void* list = nullptr;
    int count = 0;
};

// The ref's stacks whose ExtraSoul is `soul`; none on a build the ids miss.
std::vector<TrappedStack> TrappedSouls(void* ref, int soul) {
    std::vector<TrappedStack> out;
    if (!ref || !g_inventoryChanges || !g_soulLevel || !g_stackCount) return out;
    void* changes = g_inventoryChanges(static_cast<char*>(ref) + ids::kOffRefExtraList);
    for (auto* node = changes ? At<void**>(changes, 0) : nullptr; node;
         node = static_cast<void**>(node[1])) {
        void* entry = node[0];
        auto* lists = entry ? At<void**>(entry, ids::kOffEntryExtraLists) : nullptr;
        for (; lists; lists = static_cast<void**>(lists[1])) {
            if (lists[0] && g_soulLevel(lists[0]) == soul) {
                out.push_back({At<void*>(entry, ids::kOffEntryObject), lists[0],
                               g_stackCount(lists[0])});
            }
        }
    }
    return out;
}

using SoulSizeFn = std::uint32_t (*)(void* actor);

SoulSizeFn g_soulSize = nullptr;

// TES3 base form id -> its CREA_soul.txt soul, built on the first trap.
const std::unordered_map<std::uint32_t, int>& SoulsByBase() {
    static const std::unordered_map<std::uint32_t, int> souls = [] {
        std::unordered_map<std::uint32_t, int> out;
        ForEachCreatureSoul([&out](const std::string& id, int soul) {
            if (void* base = Form(FindBase(id))) out[FormIdOf(base)] = soul;
        });
        Log("soul: %zu TES3 creatures size their own souls", out.size());
        return out;
    }();
    return souls;
}

// Actor::GetSoulSize: a TES3 creature's CREA_soul.txt soul, else the engine's.
std::uint32_t SoulSizeHook(void* actor) {
    void* base = actor ? At<void*>(actor, ids::kOffRefBase) : nullptr;
    const auto& souls = SoulsByBase();
    const auto it = base ? souls.find(FormIdOf(base)) : souls.end();
    return it != souls.end() ? static_cast<std::uint32_t>(it->second) : g_soulSize(actor);
}

// Repoints every direct call of Actor::GetSoulSize at SoulSizeHook.
void InstallSoulTrap() {
    const std::uintptr_t size = Resolve("Actor::GetSoulSize", ids::kActorGetSoulSize, nullptr);
    if (!size) return;
    g_soulSize = reinterpret_cast<SoulSizeFn>(size);
    const int patched = PatchAllCalls(size, reinterpret_cast<void*>(&SoulSizeHook), "soul size");
    Log("soul: %d calls of Actor::GetSoulSize size TES3 creatures by CREA_soul.txt", patched);
}

// Removes one gem of a trapped stack, the stack's own ExtraDataList named.
void RemoveTrapped(void* ref, const TrappedStack& stack) {
    RunOnGameThread([ref, stack]() {
        std::uint32_t handle = 0;
        VCall<RemoveItemFn>(ref, ids::kRefRemoveItemSlot)(
            ref, &handle, stack.gem, 1, 0, stack.list, nullptr, nullptr, nullptr);
    });
}

// `AddSoulGem creature gem`: the named gem's variant for that creature's soul.
void AddSoulGemTo(const std::string& actor, const std::string& creature,
                  const std::string& gem) {
    const int soul = CreatureSoul(creature);
    if (!soul) {
        ReportOnce("creature soul", creature);
        return;
    }
    const std::string filled = FilledSoulGemId(gem, soul);
    if (filled.empty()) {
        ReportOnce("filled soul gem", gem);
        return;
    }
    if (Hooks().addItem) Hooks().addItem(actor, filled, 1);
}

// How many gems holding that creature's soul the actor carries.
int SoulGemsHeld(const std::string& actor, const std::string& creature) {
    const int soul = CreatureSoul(creature);
    if (!soul || !Hooks().itemCount) return 0;
    int held = 0;
    for (const std::string& id : FilledSoulGemIds(soul)) {
        held += Hooks().itemCount(actor, id);
    }
    for (const TrappedStack& stack : TrappedSouls(OwnerRef(actor), soul)) {
        held += stack.count;
    }
    return held;
}

// `RemoveSoulGem` / `DropSoulGem`: ONE gem holding that soul, a filled
// record first, then a trapped stack, which is removed even by a drop.
void TakeSoulGem(const std::string& actor, const std::string& creature,
                 bool drop) {
    const int soul = CreatureSoul(creature);
    if (!soul || !Hooks().itemCount) return;
    for (const std::string& id : FilledSoulGemIds(soul)) {
        if (Hooks().itemCount(actor, id) <= 0) continue;
        if (drop && Hooks().dropItem) {
            Hooks().dropItem(actor, id, 1);
        } else if (Hooks().removeItem) {
            Hooks().removeItem(actor, id, 1);
        }
        return;
    }
    void* ref = OwnerRef(actor);
    const std::vector<TrappedStack> trapped = TrappedSouls(ref, soul);
    if (!trapped.empty()) RemoveTrapped(ref, trapped.front());
}

}  // namespace

// A TES3 cure landing on `ref`, as OpenMW's spelleffects.cpp applies it:
// the disease, blight or curse spells come off the actor (blight sparing a
// corprus spell), and Cure Corprus only stops corprus's effects.
// See: docs/commentary/morrowind_runtime.md#cures-purge-by-class
void ApplyCure(void* ref, int index) {
    switch (index) {
        case kCureCommon: PurgeSpells(ref, SpellsOfType(kSpellDisease), true); break;
        case kCureBlight: PurgeSpells(ref, SpellsOfType(kSpellBlight), true, kCorprusEffect); break;
        case kCureCorprus: PurgeSpells(ref, SpellsWithEffect(kCorprusEffect), false); break;
        case kRemoveCurse: PurgeSpells(ref, SpellsOfType(kSpellCurse), true); break;
        default: break;
    }
}

bool IsCure(int index) {
    return index == kCureCommon || index == kCureBlight || index == kCureCorprus
           || index == kRemoveCurse;
}

void CastForm(void* spell, void* source, void* target) {
    if (!g_cast) return;
    PostToMainThread([spell, source, target]() {
        g_cast(PapyrusVm(), 0, spell, source, target);
    });
}

void InstallSpellCalls(GameHooks& hooks) {
    g_addSpell = Native<AddSpellFn>("Actor.AddSpell", ids::kActorAddSpell);
    g_removeSpell = Native<SpellQueryFn>("Actor.RemoveSpell",
                                         ids::kActorRemoveSpell);
    g_hasSpell = Native<SpellQueryFn>("Actor.HasSpell", ids::kActorHasSpell);
    g_hasMagicEffect = Native<SpellQueryFn>("Actor.HasMagicEffect",
                                            ids::kActorHasMagicEffect);
    g_dispelSpell = Native<SpellQueryFn>("Actor.DispelSpell",
                                         ids::kActorDispelSpell);
    g_cast = Native<CastFn>("Spell.Cast", ids::kSpellCast);
    g_inventoryChanges = reinterpret_cast<ExtraListFn>(Resolve(
        "ExtraDataList::GetInventoryChanges", ids::kExtraListInventoryChanges, nullptr));
    g_soulLevel = reinterpret_cast<ExtraListIntFn>(Resolve(
        "ExtraDataList::GetSoulLevel", ids::kExtraListSoulLevel, nullptr));
    g_stackCount = reinterpret_cast<ExtraListIntFn>(Resolve(
        "ExtraDataList::GetCount", ids::kExtraListCount, nullptr));
    InstallSoulTrap();
    hooks.addSpell = AddSpellTo;
    hooks.removeSpell = RemoveSpellFrom;
    hooks.hasSpell = KnowsSpell;
    hooks.hasEffect = EffectActive;
    hooks.spellActive = SpellIsActive;
    hooks.hasDisease = HasSpellOfType;
    hooks.dispelSpell = DispelOneSpell;
    hooks.dispelByEffect = DispelByEffect;
    hooks.castSpell = CastAt;
    hooks.addSoulGem = AddSoulGemTo;
    hooks.soulGemCount = SoulGemsHeld;
    hooks.takeSoulGem = TakeSoulGem;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
