// The spell commands: AddSpell and its kin, casting, and the two effect
// queries. Each names a TES3 SPEL or magic-effect id the sidecar maps to the
// record the import minted.
// See: docs/commentary/morrowind_runtime.md#spell-commands

#include <cctype>
#include <string>

#include <components/compiler/opcodes.hpp>
#include <components/interpreter/context.hpp>
#include <components/interpreter/opcodes.hpp>

#include "dialogue_state.h"
#include "script_ops.h"

namespace mwruntime {

namespace {

// What `GetEffect` writes before the effect's own name, which MGEF.txt keys.
constexpr const char* kEffectPrefix = "sEffect";

// The effect name inside `sEffectRecall`, or the argument unchanged when it
// carries no prefix -- a script may name the effect directly.
std::string EffectName(const std::string& written) {
    const std::string prefix(kEffectPrefix);
    if (written.size() <= prefix.size()) return written;
    for (std::size_t i = 0; i < prefix.size(); ++i) {
        if (std::tolower(static_cast<unsigned char>(written[i]))
            != std::tolower(static_cast<unsigned char>(prefix[i]))) {
            return written;
        }
    }
    return written.substr(prefix.size());
}

// `AddSpell` / `RemoveSpell`, whose `z` argument pushes NOTHING: OpenMW's
// 'z' runs a DiscardParser, so the stack holds the spell id alone.
// See: docs/commentary/morrowind_runtime.md#spell-commands
template <class R, bool kAdd>
class OpChangeSpell : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string spell = PopString(runtime);
        void (*hook)(const std::string&, const std::string&) =
            kAdd ? Hooks().addSpell : Hooks().removeSpell;
        if (hook) hook(actor, spell);
    }
};

// `GetSpell`: does the actor KNOW the spell, which is its spell list, not its
// active effects.
template <class R>
class OpGetSpell : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string spell = PopString(runtime);
        runtime.push(Hooks().hasSpell && Hooks().hasSpell(actor, spell) ? 1
                                                                       : 0);
    }
};

// `Cast target spell` and `ExplodeSpell spell`. ExplodeSpell casts at the
// caster itself, which is what "explode on the calling object" means.
template <class R, bool kAtSelf>
class OpCast : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string caster = R::Target(runtime);
        const std::string spell = PopString(runtime);
        const std::string target = kAtSelf ? caster : PopString(runtime);
        if (Hooks().castSpell) Hooks().castSpell(caster, target, spell);
    }
};

// `RemoveEffects index`: every spell on the actor that CONTAINS that effect.
// See: docs/commentary/morrowind_runtime.md#removeeffects-is-per-spell
template <class R>
class OpRemoveEffects : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const int index = PopInt(runtime);
        if (Hooks().dispelByEffect) Hooks().dispelByEffect(actor, index);
    }
};

// `RemoveSpellEffects spell`: that ONE spell's effects, which Skyrim does have
// a native for.
template <class R>
class OpRemoveSpellEffects : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string spell = PopString(runtime);
        if (Hooks().dispelSpell) Hooks().dispelSpell(actor, spell);
    }
};

// `GetEffect sEffectX`: is that one effect active on the actor right now.
template <class R>
class OpGetEffect : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string effect = EffectName(PopString(runtime));
        runtime.push(Hooks().hasEffect && Hooks().hasEffect(actor, effect) ? 1
                                                                          : 0);
    }
};

// `GetSpellEffects spell`: is the actor under the effect of that SPELL, which
// is its active effects rather than its spell list.
template <class R>
class OpGetSpellEffects : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string spell = PopString(runtime);
        runtime.push(
            Hooks().spellActive && Hooks().spellActive(actor, spell) ? 1 : 0);
    }
};

// `AddSoulGem creature gem`: the gem holding THAT creature's soul. The `X`
// is consumed by the compiler and pushes nothing, so the stack is the two
// names -- the creature first, as the argument order has it.
// See: docs/commentary/morrowind_runtime.md#soul-gems
template <class R>
class OpAddSoulGem : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string creature = PopString(runtime);
        const std::string gem = PopString(runtime);
        if (Hooks().addSoulGem) Hooks().addSoulGem(actor, creature, gem);
    }
};

// `HasSoulGem creature`: does the actor carry a gem holding that soul.
template <class R>
class OpHasSoulGem : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string creature = PopString(runtime);
        runtime.push(Hooks().soulGemCount
                         ? Hooks().soulGemCount(actor, creature) : 0);
    }
};

// `DropSoulGem creature`: one gem holding that soul, onto the ground.
template <class R>
class OpDropSoulGem : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        const std::string creature = PopString(runtime);
        if (Hooks().takeSoulGem) Hooks().takeSoulGem(actor, creature, true);
    }
};

// `RemoveSoulGem creature`: one gem holding that soul, out of the inventory.
//
// 🛑 SEGMENT 3, so it derives from Opcode1 and is handed the count of optional
// arguments the script actually wrote. Its `c/l` string carries a `/`, and
// `Extensions::registerInstruction` picks segment 3 for exactly that. A
// segment-5 install fails to COMPILE here, because installSegment3 wants an
// Opcode1 -- which is the error catching the mistake rather than a silent stub.
//
// The optional count is popped and dropped: some references claim a count
// argument, and UESP records that the engine ignores it.
// See: docs/commentary/morrowind_runtime.md#soul-gems
template <class R>
class OpRemoveSoulGem : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string actor = R::Target(runtime);
        const std::string creature = PopString(runtime);
        for (unsigned int i = 0; i < optional; ++i) PopInt(runtime);
        if (Hooks().takeSoulGem) Hooks().takeSoulGem(actor, creature, false);
    }
};

template <class R> using OpAddSpell = OpChangeSpell<R, true>;
template <class R> using OpRemoveSpell = OpChangeSpell<R, false>;
template <class R> using OpCastAt = OpCast<R, false>;
template <class R> using OpExplode = OpCast<R, true>;


}  // namespace

void InstallSpellOps(OpcodeInstaller& into) {
    namespace S = Compiler::Stats;
    namespace M = Compiler::Misc;
    InstallPair<OpAddSpell>(into, S::opcodeAddSpell, S::opcodeAddSpellExplicit);
    InstallPair<OpRemoveSpell>(into, S::opcodeRemoveSpell,
                               S::opcodeRemoveSpellExplicit);
    InstallPair<OpGetSpell>(into, S::opcodeGetSpell, S::opcodeGetSpellExplicit);
    InstallPair<OpRemoveEffects>(into, S::opcodeRemoveEffects,
                                 S::opcodeRemoveEffectsExplicit);
    InstallPair<OpRemoveSpellEffects>(into, S::opcodeRemoveSpellEffects,
                                      S::opcodeRemoveSpellEffectsExplicit);
    InstallPair<OpCastAt>(into, M::opcodeCast, M::opcodeCastExplicit);
    InstallPair<OpExplode>(into, M::opcodeExplodeSpell,
                           M::opcodeExplodeSpellExplicit);
    InstallPair<OpGetEffect>(into, M::opcodeGetEffect,
                             M::opcodeGetEffectExplicit);
    InstallPair<OpGetSpellEffects>(into, M::opcodeGetSpellEffects,
                                   M::opcodeGetSpellEffectsExplicit);
    namespace C = Compiler::Container;
    InstallPair<OpAddSoulGem>(into, M::opcodeAddSoulGem,
                              M::opcodeAddSoulGemExplicit);
    InstallPair<OpHasSoulGem>(into, C::opcodeHasSoulGem,
                              C::opcodeHasSoulGemExplicit);
    InstallPair<OpDropSoulGem>(into, M::opcodeDropSoulGem,
                               M::opcodeDropSoulGemExplicit);
    // 🛑 SEGMENT 3: `removesoulgem` is `c/l`, and a `/` picks segment 3 in
    // `Extensions::registerInstruction`. Installing it as segment 5 would
    // leave the stub answering, silently.
    into.Real3<OpRemoveSoulGem<Implicit>>(M::opcodeRemoveSoulGem);
    into.Real3<OpRemoveSoulGem<Explicit>>(M::opcodeRemoveSoulGemExplicit);
}

}  // namespace mwruntime
