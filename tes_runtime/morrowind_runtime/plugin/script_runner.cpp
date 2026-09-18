#include "script_runner.h"

#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <exception>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include <components/compiler/context.hpp>
#include <components/compiler/errorhandler.hpp>
#include <components/compiler/exception.hpp>
#include <components/compiler/extensions.hpp>
#include <components/compiler/extensions0.hpp>
#include <components/compiler/literals.hpp>
#include <components/compiler/locals.hpp>
#include <components/compiler/opcodes.hpp>
#include <components/compiler/scanner.hpp>
#include <components/compiler/scriptparser.hpp>
#include <components/compiler/tokenloc.hpp>
#include <components/interpreter/context.hpp>
#include <components/interpreter/installopcodes.hpp>
#include <components/interpreter/interpreter.hpp>
#include <components/interpreter/opcodes.hpp>
#include <components/interpreter/program.hpp>
#include <components/interpreter/runtime.hpp>

#include "dialogue_state.h"
#include "filter.h"
#include "log.h"
#include "script_tables.h"

namespace mwruntime {

namespace {

// Interpreter::execute's segment tags: the top six bits of an instruction.
constexpr unsigned kSegment3Tag = 0x30;
constexpr unsigned kSegment5Tag = 0x32;

// Argument letters that put a value on the stack; junk and ignored optionals
// are consumed by the parser and never pushed.
constexpr const char* kPushedArgs = "Sclsf";

// GMST sJournalEntry.
constexpr const char* kJournalUpdated = "Your journal has been updated.";

// ---------------------------------------------------------------- compiling

// MWScript::CompilerContext, answered from the sidecar's tables. The parser
// asks whether a name is a global BEFORE whether it is an id, so only a name
// a GLOB record declares may say yes -- anything looser turns `player->` and
// `Script.member` into syntax errors. Every other name is taken as an id: the
// object tables that would check it are not staged, and an id the game lacks
// only reaches a command that reports it.
class CompilerContext : public Compiler::Context {
public:
    bool canDeclareLocals() const override { return false; }

    char getGlobalType(const std::string& name) const override {
        const GlobalDef* def = FindGlobal(name);
        return def ? def->type : ' ';
    }

    // `id` is a script, or a reference whose script declares `name`.
    std::pair<char, bool> getMemberType(const std::string& name,
                                        const ESM::RefId& id) const override {
        const std::string owner = id.getRefIdString();
        const ScriptLocals* direct = FindScriptLocals(owner);
        const ScriptLocals* locals = direct ? direct
                                            : FindScriptLocals(ScriptOf(owner));
        return {locals ? locals->TypeOf(name) : ' ', direct == nullptr};
    }

    bool isId(const ESM::RefId&) const override { return true; }
};

// The speaker's own script locals, which a dialogue script may use bare.
Compiler::Locals SpeakerLocals(const std::string& actor) {
    Compiler::Locals out;
    const ScriptLocals* locals = FindScriptLocals(ScriptOf(actor));
    if (!locals) return out;
    for (const std::string& name : locals->shorts) out.declare('s', name);
    for (const std::string& name : locals->longs) out.declare('l', name);
    for (const std::string& name : locals->floats) out.declare('f', name);
    return out;
}

class LogErrors : public Compiler::ErrorHandler {
    void report(const std::string& message, const Compiler::TokenLoc& loc,
                Type type) override {
        Log("script: %s at line %d, '%s': %s",
            type == ErrorMessage ? "error" : "warning", loc.mLine + 1,
            loc.mLiteral.c_str(), message.c_str());
    }
    void report(const std::string& message, Type) override {
        Log("script: %s", message.c_str());
    }
};

// ------------------------------------------------------------- stack helpers

std::string PopString(Interpreter::Runtime& runtime) {
    std::string text(runtime.getStringLiteral(runtime[0].mInteger));
    runtime.pop();
    return text;
}

int PopInt(Interpreter::Runtime& runtime) {
    const int value = runtime[0].mInteger;
    runtime.pop();
    return value;
}

// Who a command acts on: the speaker, or the id written before `->`, which
// the compiler pushes LAST and so is popped first.
struct Implicit {
    static std::string Target(Interpreter::Runtime& runtime) {
        return runtime.getContext().getTarget().getRefIdString();
    }
};

struct Explicit {
    static std::string Target(Interpreter::Runtime& runtime) {
        return PopString(runtime);
    }
};

// ------------------------------------------------------------- real opcodes

template <class R>
class OpJournal : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        R::Target(runtime);
        const std::string quest = PopString(runtime);
        const int index = PopInt(runtime);
        if (State().AddJournalEntry(quest, index)) {
            State().messages.push_back(kJournalUpdated);
        }
    }
};

class OpSetJournalIndex : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string quest = PopString(runtime);
        State().SetJournalIndex(quest, PopInt(runtime));
    }
};

class OpGetJournalIndex : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(State().JournalIndex(PopString(runtime)));
    }
};

class OpAddTopic : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        State().addedTopics.push_back(PopString(runtime));
    }
};

// `Choice "text" 1 "text" 2 ...`: pairs, the last index optional.
class OpChoice : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int count) override {
        while (count > 0) {
            ChoiceLine line;
            line.text = PopString(runtime);
            line.index = 1;
            --count;
            if (count > 0) {
                line.index = PopInt(runtime);
                --count;
            }
            State().choices.push_back(line);
        }
    }
};

class OpGoodbye : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime&) override { State().goodbye = true; }
};

template <class R>
class OpModDisposition : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        State().ModDisposition(actor, PopInt(runtime));
    }
};

template <class R>
class OpSetDisposition : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        State().SetDisposition(actor, PopInt(runtime));
    }
};

template <class R>
class OpGetDisposition : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(State().Disposition(R::Target(runtime)));
    }
};

template <class R>
class OpModReputation : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        R::Target(runtime);
        State().reputation += PopInt(runtime);
    }
};

template <class R>
class OpSetReputation : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        R::Target(runtime);
        State().reputation = PopInt(runtime);
    }
};

template <class R>
class OpGetReputation : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        R::Target(runtime);
        runtime.push(State().reputation);
    }
};

class OpModFactionReaction : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string a = PopString(runtime);
        const std::string b = PopString(runtime);
        State().SetFactionReaction(a, b, State().FactionReaction(a, b) +
                                             PopInt(runtime));
    }
};

class OpSetFactionReaction : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string a = PopString(runtime);
        const std::string b = PopString(runtime);
        State().SetFactionReaction(a, b, PopInt(runtime));
    }
};

class OpGetFactionReaction : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string a = PopString(runtime);
        runtime.push(State().FactionReaction(a, PopString(runtime)));
    }
};

// ------------------------------------------------ the player's factions

float PopFloat(Interpreter::Runtime& runtime) {
    const float value = runtime[0].mFloat;
    runtime.pop();
    return value;
}

// The faction a command names, or its target's own when it names none. The
// target is resolved FIRST, as MWScript's own opcodes do.
template <class R>
std::string FactionArg(Interpreter::Runtime& runtime, unsigned int optional) {
    const std::string actor = R::Target(runtime);
    if (optional > 0) return PopString(runtime);
    const ActorDef* def = FindActor(actor);
    return def ? def->faction : std::string();
}

template <class R>
class OpPCJoinFaction : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        State().JoinFaction(FactionArg<R>(runtime, optional));
    }
};

template <class R>
class OpPCRaiseRank : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string faction = FactionArg<R>(runtime, optional);
        if (State().Faction(faction).rank < 0) {
            State().JoinFaction(faction);
        } else {
            State().ChangeRank(faction, 1);
        }
    }
};

template <class R>
class OpPCLowerRank : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        State().ChangeRank(FactionArg<R>(runtime, optional), -1);
    }
};

template <class R>
class OpGetPCRank : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        runtime.push(State().Faction(FactionArg<R>(runtime, optional)).rank);
    }
};

template <class R>
class OpPcExpelled : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string faction = FactionArg<R>(runtime, optional);
        runtime.push(State().Faction(faction).expelled ? 1 : 0);
    }
};

template <class R, bool Expel>
class OpSetExpelled : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        State().SetExpelled(FactionArg<R>(runtime, optional), Expel);
    }
};

template <class R>
class OpGetPCFacRep : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string faction = FactionArg<R>(runtime, optional);
        runtime.push(State().Faction(faction).reputation);
    }
};

// `SetPCFacRep value [faction]` / `ModPCFacRep`: the value comes off before
// the optional faction does.
template <class R, bool Relative>
class OpSetPCFacRep : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string actor = R::Target(runtime);
        const int value = PopInt(runtime);
        const ActorDef* def = FindActor(actor);
        const std::string faction = optional > 0 ? PopString(runtime)
                                    : def       ? def->faction
                                                : std::string();
        const int base = Relative ? State().Faction(faction).reputation : 0;
        State().SetFactionReputation(faction, base + value);
    }
};

template <class R>
class OpSameFaction : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const ActorDef* def = FindActor(R::Target(runtime));
        const bool same = def && !def->faction.empty() &&
                          State().Faction(def->faction).rank >= 0;
        runtime.push(same ? 1 : 0);
    }
};

class OpGetPCCrimeLevel : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(State().crimeLevel);
    }
};

template <bool Relative>
class OpSetPCCrimeLevel : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const float value = PopFloat(runtime);
        State().crimeLevel = std::max(
            0.0f, (Relative ? State().crimeLevel : 0.0f) + value);
        Log("crime: level %g", State().crimeLevel);
    }
};

// -------------------------------------------------- items and scripts

template <class R>
class OpAddItem : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string owner = R::Target(runtime);
        const std::string item = PopString(runtime);
        const int count = PopInt(runtime);
        Log("item: %s gains %d x %s", owner.c_str(), count, item.c_str());
        if (Hooks().addItem) Hooks().addItem(owner, item, count);
    }
};

template <class R>
class OpRemoveItem : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string owner = R::Target(runtime);
        const std::string item = PopString(runtime);
        const int count = PopInt(runtime);
        Log("item: %s loses %d x %s", owner.c_str(), count, item.c_str());
        if (Hooks().removeItem) Hooks().removeItem(owner, item, count);
    }
};

template <class R>
class OpGetItemCount : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string owner = R::Target(runtime);
        const std::string item = PopString(runtime);
        runtime.push(Hooks().itemCount ? Hooks().itemCount(owner, item) : 0);
    }
};

// `StartCombat target` -- the target is an argument, not the implicit
// reference, so a bare `StartCombat player` is the SPEAKER attacking.
template <class R>
class OpStartCombat : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string attacker = R::Target(runtime);
        const std::string target = PopString(runtime);
        Log("combat: %s attacks %s", attacker.c_str(), target.c_str());
        if (Hooks().setCombat) Hooks().setCombat(attacker, target);
    }
};

// `StopCombat` takes no target: the actor stops fighting everyone.
template <class R>
class OpStopCombat : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        Log("combat: %s stops fighting", actor.c_str());
        if (Hooks().setCombat) Hooks().setCombat(actor, std::string());
    }
};

// `Enable` / `Disable`, and `GetDisabled` which quest dialogue reads back to
// ask whether a step has happened.
template <class R, bool Enabled>
class OpSetEnabled : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        Log("world: %s %s", ref.c_str(), Enabled ? "enabled" : "disabled");
        if (Hooks().setEnabled) Hooks().setEnabled(ref, Enabled);
    }
};

template <class R>
class OpGetDisabled : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        runtime.push(Hooks().isDisabled && Hooks().isDisabled(ref) ? 1 : 0);
    }
};

template <class R>
class OpStartScript : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        R::Target(runtime);
        State().SetScriptRunning(PopString(runtime), true);
    }
};

class OpStopScript : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        State().SetScriptRunning(PopString(runtime), false);
    }
};

class OpScriptRunning : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(State().ScriptRunning(PopString(runtime)) ? 1 : 0);
    }
};

// The four AI settings, each its own opcode in TES3 but one number here.
// `Which` is the index the co-save keys them by, not a TES3 constant.
template <class R, int Which>
class OpGetAiSetting : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(State().AiSetting(R::Target(runtime), Which));
    }
};

template <class R, int Which>
class OpSetAiSetting : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        State().SetAiSetting(actor, Which, PopInt(runtime));
    }
};

template <class R, int Which>
class OpModAiSetting : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string actor = R::Target(runtime);
        State().SetAiSetting(actor, Which,
                             State().AiSetting(actor, Which) + PopInt(runtime));
    }
};

// `Random n` -> 0..n-1 as a float. MWScript's own generator is uniform over
// the half-open range and answers 0 for a non-positive limit.
class OpRandom : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const int limit = PopInt(runtime);
        runtime.push(limit > 0 ? static_cast<float>(std::rand() % limit) : 0.0f);
    }
};

class OpGetDeadCount : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        runtime.push(State().DeadCount(PopString(runtime)));
    }
};

// -------------------------------------------------------------------- stubs

// Commands with nothing to do here by DESIGN, not for want of a port: the
// map and screen fades are Morrowind's own presentation, and ClearInfoActor
// edits a topic log this runtime does not keep.
constexpr const char* kDeliberateNoOps[] = {"showmap", "fadein", "fadeout",
                                            "fadeto", "clearinfoactor"};

// How often each unported command has been reached.
std::map<std::string, int> g_reported;

// What an unported command does: consume what it was given, answer zero.
struct StubShape {
    std::string name;
    int pops = 0;
    char returns = 0;

    bool Deliberate() const {
        for (const char* quiet : kDeliberateNoOps) {
            if (name == quiet) return true;
        }
        return false;
    }

    void Run(Interpreter::Runtime& runtime, int extra) const {
        if (!Deliberate() && ++g_reported[name] == 1) {
            Log("script: '%s' is not ported yet -- it did nothing",
                name.c_str());
        }
        for (int i = 0; i < pops + extra; ++i) runtime.pop();
        if (returns == 'f') {
            runtime.push(0.0f);
        } else if (returns) {
            runtime.push(0);
        }
    }
};

class Stub5 : public Interpreter::Opcode0 {
public:
    explicit Stub5(StubShape shape) : mShape(std::move(shape)) {}
    void execute(Interpreter::Runtime& runtime) override {
        mShape.Run(runtime, 0);
    }

private:
    StubShape mShape;
};

class Stub3 : public Interpreter::Opcode1 {
public:
    explicit Stub3(StubShape shape) : mShape(std::move(shape)) {}
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        mShape.Run(runtime, static_cast<int>(optional));
    }

private:
    StubShape mShape;
};

int PushedCount(const std::string& args) {
    int count = 0;
    for (char c : args) {
        if (c == '/') break;
        if (std::strchr(kPushedArgs, c)) ++count;
    }
    return count;
}

// ------------------------------------------------------------ the machinery

struct Machine {
    Compiler::Extensions extensions;
    CompilerContext compilerContext;
    Interpreter::Interpreter interpreter;
    std::set<int> segment5;
    std::set<int> segment3;

    template <class T>
    void Real(int code) {
        interpreter.installSegment5<T>(code);
        segment5.insert(code);
    }

    // The segment-3 form: a command with optional arguments.
    template <class T>
    void Real3(int code) {
        interpreter.installSegment3<T>(code);
        segment3.insert(code);
    }

    void InstallFactions();
    void InstallItemsAndScripts();
    void InstallAiSettings();

    // Installs a stub for one instruction word unless something real, or an
    // alias's stub, already answers to it.
    void Stub(Interpreter::Type_Code word, const StubShape& shape) {
        const unsigned tag = word >> 26;
        if (tag == kSegment5Tag) {
            const int code = static_cast<int>(word & 0x3ffffff);
            if (segment5.insert(code).second) {
                interpreter.installSegment5<Stub5>(code, shape);
            }
        } else if (tag == kSegment3Tag) {
            const int code = static_cast<int>((word >> 8) & 0x3ffff);
            if (segment3.insert(code).second) {
                interpreter.installSegment3<Stub3>(code, shape);
            }
        }
    }

    void InstallReal();
    void InstallStubs();
    void StubKeyword(const std::string& keyword);
};

void Machine::InstallReal() {
    namespace D = Compiler::Dialogue;
    namespace S = Compiler::Stats;
    Real<OpJournal<Implicit>>(D::opcodeJournal);
    Real<OpJournal<Explicit>>(D::opcodeJournalExplicit);
    Real<OpSetJournalIndex>(D::opcodeSetJournalIndex);
    Real<OpGetJournalIndex>(D::opcodeGetJournalIndex);
    Real<OpAddTopic>(D::opcodeAddTopic);
    Real<OpGoodbye>(D::opcodeGoodbye);
    Real<OpGetReputation<Implicit>>(D::opcodeGetReputation);
    Real<OpSetReputation<Implicit>>(D::opcodeSetReputation);
    Real<OpModReputation<Implicit>>(D::opcodeModReputation);
    Real<OpGetReputation<Explicit>>(D::opcodeGetReputationExplicit);
    Real<OpSetReputation<Explicit>>(D::opcodeSetReputationExplicit);
    Real<OpModReputation<Explicit>>(D::opcodeModReputationExplicit);
    Real<OpModFactionReaction>(D::opcodeModFactionReaction);
    Real<OpSetFactionReaction>(D::opcodeSetFactionReaction);
    Real<OpGetFactionReaction>(D::opcodeGetFactionReaction);
    Real<OpModDisposition<Implicit>>(S::opcodeModDisposition);
    Real<OpModDisposition<Explicit>>(S::opcodeModDispositionExplicit);
    Real<OpSetDisposition<Implicit>>(S::opcodeSetDisposition);
    Real<OpSetDisposition<Explicit>>(S::opcodeSetDispositionExplicit);
    Real<OpGetDisposition<Implicit>>(S::opcodeGetDisposition);
    Real<OpGetDisposition<Explicit>>(S::opcodeGetDispositionExplicit);
    Real<OpSameFaction<Implicit>>(D::opcodeSameFaction);
    Real<OpSameFaction<Explicit>>(D::opcodeSameFactionExplicit);
    Real3<OpChoice>(D::opcodeChoice);
    InstallFactions();
    InstallItemsAndScripts();
}

void Machine::InstallFactions() {
    namespace S = Compiler::Stats;
    Real3<OpPCJoinFaction<Implicit>>(S::opcodePCJoinFaction);
    Real3<OpPCJoinFaction<Explicit>>(S::opcodePCJoinFactionExplicit);
    Real3<OpPCRaiseRank<Implicit>>(S::opcodePCRaiseRank);
    Real3<OpPCRaiseRank<Explicit>>(S::opcodePCRaiseRankExplicit);
    Real3<OpPCLowerRank<Implicit>>(S::opcodePCLowerRank);
    Real3<OpPCLowerRank<Explicit>>(S::opcodePCLowerRankExplicit);
    Real3<OpGetPCRank<Implicit>>(S::opcodeGetPCRank);
    Real3<OpGetPCRank<Explicit>>(S::opcodeGetPCRankExplicit);
    Real3<OpPcExpelled<Implicit>>(S::opcodePcExpelled);
    Real3<OpPcExpelled<Explicit>>(S::opcodePcExpelledExplicit);
    Real3<OpSetExpelled<Implicit, true>>(S::opcodePcExpell);
    Real3<OpSetExpelled<Explicit, true>>(S::opcodePcExpellExplicit);
    Real3<OpSetExpelled<Implicit, false>>(S::opcodePcClearExpelled);
    Real3<OpSetExpelled<Explicit, false>>(S::opcodePcClearExpelledExplicit);
    Real3<OpGetPCFacRep<Implicit>>(S::opcodeGetPCFacRep);
    Real3<OpGetPCFacRep<Explicit>>(S::opcodeGetPCFacRepExplicit);
    Real3<OpSetPCFacRep<Implicit, false>>(S::opcodeSetPCFacRep);
    Real3<OpSetPCFacRep<Explicit, false>>(S::opcodeSetPCFacRepExplicit);
    Real3<OpSetPCFacRep<Implicit, true>>(S::opcodeModPCFacRep);
    Real3<OpSetPCFacRep<Explicit, true>>(S::opcodeModPCFacRepExplicit);
    Real<OpGetPCCrimeLevel>(S::opcodeGetPCCrimeLevel);
    Real<OpSetPCCrimeLevel<false>>(S::opcodeSetPCCrimeLevel);
    Real<OpSetPCCrimeLevel<true>>(S::opcodeModPCCrimeLevel);
}

void Machine::InstallItemsAndScripts() {
    namespace C = Compiler::Container;
    namespace M = Compiler::Misc;
    Real<OpAddItem<Implicit>>(C::opcodeAddItem);
    Real<OpAddItem<Explicit>>(C::opcodeAddItemExplicit);
    Real<OpRemoveItem<Implicit>>(C::opcodeRemoveItem);
    Real<OpRemoveItem<Explicit>>(C::opcodeRemoveItemExplicit);
    Real<OpGetItemCount<Implicit>>(C::opcodeGetItemCount);
    Real<OpGetItemCount<Explicit>>(C::opcodeGetItemCountExplicit);
    Real<OpStartScript<Implicit>>(M::opcodeStartScript);
    Real<OpStartScript<Explicit>>(M::opcodeStartScriptExplicit);
    Real<OpStopScript>(M::opcodeStopScript);
    Real<OpScriptRunning>(M::opcodeScriptRunning);
    Real<OpGetDeadCount>(Compiler::Stats::opcodeGetDeadCount);
    Real<OpStartCombat<Implicit>>(Compiler::Ai::opcodeStartCombat);
    Real<OpStartCombat<Explicit>>(Compiler::Ai::opcodeStartCombatExplicit);
    Real<OpStopCombat<Implicit>>(Compiler::Ai::opcodeStopCombat);
    Real<OpStopCombat<Explicit>>(Compiler::Ai::opcodeStopCombatExplicit);
    Real<OpRandom>(M::opcodeRandom);
    Real<OpSetEnabled<Implicit, true>>(M::opcodeEnable);
    Real<OpSetEnabled<Explicit, true>>(M::opcodeEnableExplicit);
    Real<OpSetEnabled<Implicit, false>>(M::opcodeDisable);
    Real<OpSetEnabled<Explicit, false>>(M::opcodeDisableExplicit);
    Real<OpGetDisabled<Implicit>>(M::opcodeGetDisabled);
    Real<OpGetDisabled<Explicit>>(M::opcodeGetDisabledExplicit);
    InstallAiSettings();
}

// Fight, Hello, Alarm and Flee: get, set and mod, bare and explicit. The
// settings have no Skyrim field, so these are the DLL's own numbers -- but
// a script that raises Fight and a filter that reads it back now agree.
void Machine::InstallAiSettings() {
    namespace A = Compiler::Ai;
    Real<OpGetAiSetting<Implicit, kAiFight>>(A::opcodeGetFight);
    Real<OpGetAiSetting<Explicit, kAiFight>>(A::opcodeGetFightExplicit);
    Real<OpSetAiSetting<Implicit, kAiFight>>(A::opcodeSetFight);
    Real<OpSetAiSetting<Explicit, kAiFight>>(A::opcodeSetFightExplicit);
    Real<OpModAiSetting<Implicit, kAiFight>>(A::opcodeModFight);
    Real<OpModAiSetting<Explicit, kAiFight>>(A::opcodeModFightExplicit);
    Real<OpGetAiSetting<Implicit, kAiHello>>(A::opcodeGetHello);
    Real<OpGetAiSetting<Explicit, kAiHello>>(A::opcodeGetHelloExplicit);
    Real<OpSetAiSetting<Implicit, kAiHello>>(A::opcodeSetHello);
    Real<OpSetAiSetting<Explicit, kAiHello>>(A::opcodeSetHelloExplicit);
    Real<OpModAiSetting<Implicit, kAiHello>>(A::opcodeModHello);
    Real<OpModAiSetting<Explicit, kAiHello>>(A::opcodeModHelloExplicit);
    Real<OpGetAiSetting<Implicit, kAiAlarm>>(A::opcodeGetAlarm);
    Real<OpGetAiSetting<Explicit, kAiAlarm>>(A::opcodeGetAlarmExplicit);
    Real<OpSetAiSetting<Implicit, kAiAlarm>>(A::opcodeSetAlarm);
    Real<OpSetAiSetting<Explicit, kAiAlarm>>(A::opcodeSetAlarmExplicit);
    Real<OpModAiSetting<Implicit, kAiAlarm>>(A::opcodeModAlarm);
    Real<OpModAiSetting<Explicit, kAiAlarm>>(A::opcodeModAlarmExplicit);
    Real<OpGetAiSetting<Implicit, kAiFlee>>(A::opcodeGetFlee);
    Real<OpGetAiSetting<Explicit, kAiFlee>>(A::opcodeGetFleeExplicit);
    Real<OpSetAiSetting<Implicit, kAiFlee>>(A::opcodeSetFlee);
    Real<OpSetAiSetting<Explicit, kAiFlee>>(A::opcodeSetFleeExplicit);
    Real<OpModAiSetting<Implicit, kAiFlee>>(A::opcodeModFlee);
    Real<OpModAiSetting<Explicit, kAiFlee>>(A::opcodeModFleeExplicit);
}

// The extension table keeps its opcodes private, so each is recovered by
// asking it to GENERATE the command and reading the instruction it emits:
// once bare, and once with an explicit reference, which pushes one more value.
void Machine::StubKeyword(const std::string& keyword) {
    const int id = extensions.searchKeyword(keyword);
    StubShape shape;
    shape.name = keyword;
    std::string args;
    bool hasExplicit = true;
    const bool function = extensions.isFunction(id, shape.returns, args,
                                                hasExplicit);
    if (!function) {
        shape.returns = 0;
        hasExplicit = true;
        if (!extensions.isInstruction(id, args, hasExplicit)) return;
    }
    shape.pops = PushedCount(args);
    Compiler::Literals literals;
    for (const bool withRef : {false, true}) {
        if (withRef && !hasExplicit) break;
        std::vector<Interpreter::Type_Code> code;
        const std::string ref = withRef ? "x" : "";
        if (function) {
            extensions.generateFunctionCode(id, code, literals, ref, 0);
        } else {
            extensions.generateInstructionCode(id, code, literals, ref, 0);
        }
        StubShape placed = shape;
        placed.pops += withRef ? 1 : 0;
        Stub(code.back(), placed);
    }
}

void Machine::InstallStubs() {
    std::vector<std::string> keywords;
    extensions.listKeywords(keywords);
    for (const std::string& keyword : keywords) StubKeyword(keyword);
    Log("script: %zu command(s) registered; %zu opcode(s) answer, of which "
        "the dialogue set is real and the rest are logging stubs",
        keywords.size(), segment5.size() + segment3.size());
}

Machine& TheMachine() {
    static std::unique_ptr<Machine> machine;
    if (!machine) {
        machine = std::make_unique<Machine>();
        Compiler::registerExtensions(machine->extensions);
        machine->compilerContext.setExtensions(&machine->extensions);
        Interpreter::installOpcodes(machine->interpreter);
        machine->InstallReal();
        machine->InstallStubs();
    }
    return *machine;
}

// DialogueManager::compile.
bool Compile(Machine& machine, const std::string& source,
             const std::string& speaker, Interpreter::Program* program) {
    LogErrors errors;
    try {
        std::istringstream input(source + "\n");
        Compiler::Scanner scanner(errors, input, &machine.extensions);
        Compiler::Locals locals = SpeakerLocals(speaker);
        Compiler::ScriptParser parser(errors, machine.compilerContext, locals,
                                      false);
        scanner.scan(parser);
        if (!errors.isGood()) return false;
        *program = parser.getProgram();
        return true;
    } catch (const Compiler::SourceException&) {
        return false;
    } catch (const std::exception& error) {
        Log("script: compile failed: %s", error.what());
        return false;
    }
}

}  // namespace

// Most-reached first, each as "name xN".
std::vector<std::string> UnportedCommandsSeen() {
    std::vector<std::pair<std::string, int>> rows(g_reported.begin(),
                                                  g_reported.end());
    std::sort(rows.begin(), rows.end(), [](const auto& a, const auto& b) {
        return a.second > b.second;
    });
    std::vector<std::string> out;
    for (const auto& row : rows) {
        out.push_back(row.first + " x" + std::to_string(row.second));
    }
    return out;
}

bool RunResultScript(const std::string& source,
                     Interpreter::Context& context) {
    if (source.find_first_not_of(" \t\r\n") == std::string::npos) return true;
    Machine& machine = TheMachine();
    Interpreter::Program program;
    const std::string speaker = context.getTarget().getRefIdString();
    if (!Compile(machine, source, speaker, &program)) {
        Log("script: NOT run -- it did not compile:\n%s", source.c_str());
        return false;
    }
    try {
        machine.interpreter.run(program, context);
        return true;
    } catch (const std::exception& error) {
        Log("script: stopped: %s\n%s", error.what(), source.c_str());
        return false;
    }
}

}  // namespace mwruntime
