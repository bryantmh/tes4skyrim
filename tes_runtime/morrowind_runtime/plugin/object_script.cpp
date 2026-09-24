#include "object_script.h"

#include <cctype>
#include <cstdio>
#include <map>
#include <unordered_map>
#include <utility>

#include "dialogue_state.h"
#include "log.h"
#include "script_runner.h"
#include "script_tables.h"

namespace mwruntime {

namespace {

// The locals the ENGINE writes, which are not opcodes: a script declares
// `short OnPCEquip` itself and reads the variable the engine set. 322 of
// TR_Mainland's 3,569 bodies declare one.
// See: docs/plans/morrowind_object_scripts.md#events
constexpr const char* kOnPcEquip = "onpcequip";
constexpr const char* kOnPcAdd = "onpcadd";
constexpr const char* kOnPcDrop = "onpcdrop";
constexpr const char* kOnPcHitMe = "onpchitme";

// Whose inventory `OnPCEquip` asks about; the hooks take a TES3 id.
constexpr const char* kPlayerId = "player";

std::map<std::string, ObjectScript> g_instances;

// The instance whose body is executing, for the event opcodes to read.
ObjectScript* g_running = nullptr;

// The conversation's speaker and the key its locals live under, or empty
// when the speaker runs no script.
std::string g_speakerId;
std::string g_speakerKey;

std::string LowerId(std::string text) {
    for (char& c : text) c = static_cast<char>(std::tolower(
                             static_cast<unsigned char>(c)));
    return text;
}

// The global scripts that have run this session, by script name.
std::map<std::string, ObjectScript> g_globalScripts;

// Instance by the FormID it has in THIS game, which is the only id an engine
// hook is handed. Filled once at load, when the load order can be resolved.
std::unordered_map<std::uint32_t, ObjectScript*> g_byRuntimeId;

// The owner string an instance's locals live under: the placement, spelled the
// way InstanceScript keys it so the two never disagree.
std::string OwnerFor(const std::string& plugin, std::uint32_t localFormId) {
    char id[9] = {0};
    std::snprintf(id, sizeof(id), "%06X", localFormId & 0x00FFFFFF);
    return plugin + "|" + id;
}

// Writes one engine-written local, but ONLY when the script declares it --
// inventing the name would make `set` land on a variable nothing reads. The
// layout is the COMPILER's, for the reason ObjectContext::Layout gives.
void WriteEventLocal(const ObjectScript& instance, const char* name,
                     bool raised) {
    const ScriptLocals* locals = ObjectScriptLocals(instance.Script());
    if (!locals || locals->TypeOf(name) == ' ') return;
    State().SetVar(instance.Key(), name, raised ? 1.0f : 0.0f);
}

}  // namespace

bool ObjectEvents::Any() const {
    return activated || died || cellChanged || pcAdded ||
           pcDropped || pcHitMe;
}

void ObjectEvents::Clear() { *this = ObjectEvents(); }

ObjectScript::ObjectScript(std::string plugin, std::uint32_t localFormId,
                           std::string baseId, std::string script)
    : mPlugin(std::move(plugin)), mLocalFormId(localFormId),
      mBaseId(std::move(baseId)), mScript(std::move(script)),
      mKey(OwnerFor(mPlugin, mLocalFormId)) {}

ObjectScript::ObjectScript(std::string script, std::string target)
    : mBaseId(std::move(target)), mScript(std::move(script)), mKey(mScript) {}

// 🛑 The events are cleared whether the body ran or not. TES3 gives them a
// one-tick life, so a script that fails to compile must not leave `OnActivate`
// latched for the next tick to see.
bool ObjectScript::RunOnce() {
    const LayerScope scope(mLayer);
    const std::string& source = ScriptSource(mScript);
    // Compiled FIRST: the engine-written locals below are named through the
    // layout the compiler builds, which does not exist until the body parses.
    if (source.empty() || !EnsureObjectScript(mScript, source)) {
        mEvents.Clear();
        if (!mRunFailed) {
            mRunFailed = true;
            Log("object: %s (%08X) %s -- it never runs", mScript.c_str(),
                mRuntimeFormId,
                source.empty() ? "has no source" : "will not compile");
        }
        return false;
    }
    // 🛑 `OnPCEquip` is NOT written here. It is a STATE `PollEquipped` keeps,
    // and an event write would clear it every tick -- the unequip branch
    // `if ( OnPCEquip == 0 )` would then fire while the item is still worn.
    WriteEventLocal(*this, kOnPcAdd, mEvents.pcAdded);
    WriteEventLocal(*this, kOnPcDrop, mEvents.pcDropped);
    WriteEventLocal(*this, kOnPcHitMe, mEvents.pcHitMe);
    ObjectContext context(*this);
    ObjectScript* const previous = g_running;
    g_running = this;
    const bool ok = RunObjectScript(mScript, source, context);
    g_running = previous;
    mEvents.Clear();
    // Once per instance, on the transition: a body that starts throwing stops
    // advancing its state while still bound and ticking, which otherwise looks
    // exactly like an unbind from the log.
    if (!ok && !mRunFailed) {
        mRunFailed = true;
        Log("object: %s (%08X) FAILED to run -- it stops advancing here",
            mScript.c_str(), mRuntimeFormId);
    }
    return ok;
}

void ObjectScript::PollDeath() {
    if (mDeathSeen || !mRuntimeFormId || !Hooks().isDead) return;
    const bool dead = Hooks().isDead(mRuntimeFormId);
    // 🛑 A TRANSITION, not a state. TES3 raises `OnDeath` on the tick an actor
    // dies; a corpse placed dead in the cell never had that tick. The first
    // poll only records which side it started on, so binding a pre-placed body
    // -- or rebinding any corpse when its cell reloads -- raises nothing.
    if (!mLifeSampled) {
        mLifeSampled = true;
        mDeathSeen = dead;
        return;
    }
    if (!dead) return;
    mDeathSeen = true;
    mEvents.died = true;
    Log("object: %s died (%s)", mScript.c_str(), mBaseId.c_str());
}

bool ObjectScript::PollEquipped(const std::string& baseId) {
    if (baseId.empty() || !Hooks().itemCount) return false;
    if (Hooks().itemCount(kPlayerId, baseId) <= 0) return false;
    // The id that answered is the one a bare command in the body acts on.
    mBaseId = baseId;
    const ScriptLocals* locals = ObjectScriptLocals(mScript);
    if (!locals || locals->TypeOf(kOnPcEquip) == ' ') return true;
    const bool worn = Hooks().itemEquipped &&
                      Hooks().itemEquipped(kPlayerId, baseId);
    const float was = State().Var(mKey, kOnPcEquip);
    if (!worn && was != 0.0f && mWornId != baseId) return true;
    if ((was != 0.0f) == worn) return true;
    mWornId = worn ? baseId : std::string();
    State().SetVar(mKey, kOnPcEquip, worn ? 1.0f : 0.0f);
    Log("object: %s OnPCEquip = %d (%s)", mScript.c_str(), worn ? 1 : 0,
        baseId.c_str());
    return true;
}

ObjectScript* RunningInstance() { return g_running; }

void SetSpeakerInstance(const std::string& id, std::uint32_t runtimeFormId) {
    const ObjectScript* instance =
        runtimeFormId ? InstanceForRef(runtimeFormId) : nullptr;
    g_speakerId = LowerId(id);
    g_speakerKey = instance ? instance->Key() : std::string();
}

std::string LocalsOwner(const std::string& id) {
    const std::string lower = LowerId(id);
    if (!g_speakerKey.empty() && lower == g_speakerId) return g_speakerKey;
    if (g_running && g_running->RuntimeFormId() &&
        lower == LowerId(g_running->BaseId())) {
        return g_running->Key();
    }
    const FormRef* placed = FindRef(id);
    if (placed) {
        const std::uint32_t local = placed->formId & 0x00FFFFFF;
        if (!InstanceScript(placed->plugin, local).empty()) {
            return OwnerFor(placed->plugin, local);
        }
    }
    return id;
}

// The instance a staged placement runs, created on first sight of it.
ObjectScript* InstanceForLocal(std::uint32_t localFormId) {
    const InstanceRow* row = InstanceByLocal(localFormId);
    return row ? InstanceFor(row->plugin, row->localFormId, row->baseId)
               : nullptr;
}

// 🛑 LAZY, and it must be. `Game.GetFormFromFile` only answers for a form the
// engine has LOADED, and a non-persistent reference does not exist until its
// cell does -- measured, 139 of TR_Mainland's 15,540 placements are persistent,
// which is exactly how many a resolve-everything-at-load pass could bind.
//
// So the runtime FormID is never precomputed. An engine hook already holds a
// live reference, and its LOCAL id is the one the sidecar staged, because both
// sides drop the index byte.
// See: docs/plans/morrowind_object_scripts.md#only-persistent-refs-exist
ObjectScript* InstanceForRef(std::uint32_t runtimeFormId) {
    const auto it = g_byRuntimeId.find(runtimeFormId);
    if (it != g_byRuntimeId.end()) return it->second;
    ObjectScript* made = InstanceForLocal(runtimeFormId);
    if (made) made->SetRuntimeFormId(runtimeFormId);
    g_byRuntimeId[runtimeFormId] = made;
    return made;
}

// 🛑 The BASE id must come from the staged row, not "". It is what a bare
// command inside the body acts on -- `StartCombat` with no `->` is the object
// itself -- so an empty one made every implicit command target nothing.
void BindInstance(std::uint32_t runtimeFormId, const std::string& plugin,
                  std::uint32_t localFormId) {
    const InstanceRow* row = InstanceByLocal(localFormId);
    ObjectScript* instance = InstanceFor(plugin, localFormId,
                                         row ? row->baseId : std::string());
    if (!instance) return;
    instance->SetRuntimeFormId(runtimeFormId);
    g_byRuntimeId[runtimeFormId] = instance;
}

// 🛑 The spawned instance is keyed by the RUNTIME FormID as its own "plugin",
// because it has no authored placement to borrow one from. That string is what
// its locals persist under, so the same creature keeps `deadDone` across a
// save -- and two spawns of one base stay separate, as TES3 has them.
void BindSpawnedInstance(std::uint32_t runtimeFormId,
                         const std::string& baseId) {
    const std::string& script = ScriptOf(baseId);
    if (script.empty()) return;
    char key[16] = {0};
    std::snprintf(key, sizeof(key), "spawn:%08X", runtimeFormId);
    const auto it = g_instances.emplace(
        key, ObjectScript(key, runtimeFormId, baseId, script));
    it.first->second.SetRuntimeFormId(runtimeFormId);
    it.first->second.SetLayer(CurrentLayer());
    g_byRuntimeId[runtimeFormId] = &it.first->second;
    Log("object: spawned %08X runs '%s' (%s)", runtimeFormId, script.c_str(),
        baseId.c_str());
}

// 🛑 The binding goes, the INSTANCE stays. Its locals live in DialogueState
// under the instance's own key, which is what TES3 keeps across an unload --
// a door that was opened is still open when you come back.
void UnbindInstance(std::uint32_t runtimeFormId) {
    g_byRuntimeId.erase(runtimeFormId);
}

void ClearInstanceBindings() { g_byRuntimeId.clear(); }

// 🛑 Asks whether the PLACEMENT is bound, not whether its instance exists: an
// instance OUTLIVES its binding by design, so testing existence would let a
// cell be re-entered and never rebind.
bool IsPlacementBound(const std::string& plugin, std::uint32_t localFormId) {
    const ObjectScript* instance = FindInstance(plugin, localFormId);
    if (!instance || !instance->RuntimeFormId()) return false;
    return g_byRuntimeId.count(instance->RuntimeFormId()) != 0;
}

std::size_t BoundInstanceCount() { return g_byRuntimeId.size(); }

// Only the entries that ARE an instance: a reference the hooks asked about and
// that runs no script is remembered as a null, so the answer is not looked up
// again on every activation.
std::vector<ObjectScript*> BoundInstances() {
    std::vector<ObjectScript*> out;
    out.reserve(g_byRuntimeId.size());
    for (const auto& entry : g_byRuntimeId) {
        if (entry.second) out.push_back(entry.second);
    }
    return out;
}

ObjectContext::ObjectContext(ObjectScript& instance)
    : ObjectActorHolder(instance.BaseId()),
      DialogueContext(actor, instance.BaseId(), std::string()),
      mInstance(instance) {}

// Commands written bare (`Activate`, `Enable`) act on the object running the
// script, which they reach by its BASE id -- refs_formid.txt's own key.
ESM::RefId ObjectContext::getTarget() const {
    return ESM::RefId::stringRefId(mInstance.BaseId());
}

// No dialogue menu is open, so the notice goes to the game's own.
void ObjectContext::messageBox(std::string_view message,
                               const std::vector<std::string>& buttons) {
    Log("object: %s says \"%.*s\"", mInstance.Script().c_str(),
        static_cast<int>(message.size()), message.data());
    if (Hooks().showMessage) Hooks().showMessage(std::string(message), buttons);
}

// 🛑 The COMPILER's layout, not the staged one: a body declares locals inline
// and the parser numbers them as it reads, so the staged table is a copy that
// a script missing from it would silently drop every write through.
const ScriptLocals* ObjectContext::Layout() const {
    return ObjectScriptLocals(mInstance.Script());
}

std::string ObjectContext::OwnerKey() const { return mInstance.Key(); }

// 🛑 One instance per SCRIPT, not per item record. The two-argument
// constructor keys locals by the script name, and OpenMW writes `OnPCEquip`
// the same way (`setVarByInt(script, ...)`), so a second instance for a second
// cuirass would be a duplicate writing the same locals.
ObjectScript* CarriedInstance(const std::string& baseId,
                              const std::string& script) {
    if (baseId.empty() || script.empty()) return nullptr;
    const std::string key = StateKey(script);
    const auto it = g_instances.find(key);
    if (it != g_instances.end()) {
        if (it->second.BaseId().empty()) it->second.SetBaseId(baseId);
        return &it->second;
    }
    ObjectScript& made =
        g_instances.emplace(key, ObjectScript(script, baseId)).first->second;
    made.SetLayer(CurrentLayer());
    return &made;
}

ObjectScript* InstanceFor(const std::string& plugin,
                          std::uint32_t localFormId,
                          const std::string& baseId) {
    const std::string key = OwnerFor(plugin, localFormId);
    const auto it = g_instances.find(key);
    if (it != g_instances.end()) {
        // An instance made by a caller that had no base id learns it here, so
        // whichever path reaches the placement first, bare commands still act
        // on the object rather than on "".
        if (it->second.BaseId().empty() && !baseId.empty()) {
            it->second.SetBaseId(baseId);
        }
        return &it->second;
    }
    const std::string& script = InstanceScript(plugin, localFormId);
    if (script.empty()) return nullptr;
    ObjectScript& made =
        g_instances
            .emplace(key, ObjectScript(plugin, localFormId, baseId, script))
            .first->second;
    made.SetLayer(InstanceLayer(plugin, localFormId));
    return &made;
}

ObjectScript* FindInstance(const std::string& plugin,
                           std::uint32_t localFormId) {
    const auto it = g_instances.find(OwnerFor(plugin, localFormId));
    return it == g_instances.end() ? nullptr : &it->second;
}

std::size_t LiveInstanceCount() { return g_instances.size(); }

// 🛑 A retargeted `StartScript` replaces the instance: its target is part of
// what the script acts on, while its locals are keyed by name and carry over.
std::size_t RunGlobalScripts() {
    std::size_t ran = 0;
    for (const RunningGlobal& running : State().RunningScripts()) {
        auto it = g_globalScripts.find(running.key);
        if (it == g_globalScripts.end() ||
            it->second.BaseId() != running.target ||
            it->second.Layer() != running.layer) {
            it = g_globalScripts
                     .insert_or_assign(running.key,
                                       ObjectScript(running.script,
                                                    running.target))
                     .first;
            it->second.SetLayer(running.layer);
        }
        if (it->second.RunOnce()) ++ran;
    }
    return ran;
}

void ClearInstances() {
    g_byRuntimeId.clear();
    g_instances.clear();
    g_globalScripts.clear();
}

}  // namespace mwruntime
