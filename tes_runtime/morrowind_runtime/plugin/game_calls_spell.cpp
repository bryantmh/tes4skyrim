// The spell natives: the actor's spell list, casting, and what is active on
// it right now.
// See: docs/commentary/morrowind_runtime.md#spell-commands

#include "game_calls_internal.h"

#include <string>

#include "ids.h"
#include "main_thread.h"

namespace mwruntime {
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

// The SPEL a TES3 spell id names, reporting one that resolves to nothing.
void* SpellForm(const std::string& spell) {
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
void DispelByEffect(const std::string& actor, int index) {
    void* ref = OwnerRef(actor);
    if (!ref) return;
    std::vector<void*> forms;
    for (const std::string& id : SpellsWithEffect(index)) {
        const SpellDef* def = FindSpell(id);
        void* form = def ? Form(&def->form) : nullptr;
        if (form) forms.push_back(form);
    }
    if (forms.empty()) return;
    PostToMainThread([ref, forms]() {
        for (void* form : forms) {
            if (g_dispelSpell) g_dispelSpell(PapyrusVm(), 0, ref, form);
            if (g_removeSpell) g_removeSpell(PapyrusVm(), 0, ref, form);
        }
    });
}

void CastAt(const std::string& caster, const std::string& target,
            const std::string& spell) {
    void* from = OwnerRef(caster);
    void* at = OwnerRef(target);
    void* form = SpellForm(spell);
    if (!from || !at || !form || !g_cast) return;
    PostToMainThread([form, from, at]() {
        g_cast(PapyrusVm(), 0, form, from, at);
    });
}

// --- soul gems -----------------------------------------------------------
//
// Each command names the CREATURE whose soul is trapped. Skyrim has no
// per-instance soul on a stack, so the filled gem is its own base record and
// the search is over the gems holding a soul of that size.
// See: docs/commentary/morrowind_runtime.md#soul-gems

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
    return held;
}

// `RemoveSoulGem` / `DropSoulGem`: ONE gem holding that soul, the first the
// actor actually carries.
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
}

}  // namespace

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
    hooks.addSpell = AddSpellTo;
    hooks.removeSpell = RemoveSpellFrom;
    hooks.hasSpell = KnowsSpell;
    hooks.hasEffect = EffectActive;
    hooks.spellActive = SpellIsActive;
    hooks.dispelSpell = DispelOneSpell;
    hooks.dispelByEffect = DispelByEffect;
    hooks.castSpell = CastAt;
    hooks.addSoulGem = AddSoulGemTo;
    hooks.soulGemCount = SoulGemsHeld;
    hooks.takeSoulGem = TakeSoulGem;
}

}  // namespace gamecalls
}  // namespace mwruntime
