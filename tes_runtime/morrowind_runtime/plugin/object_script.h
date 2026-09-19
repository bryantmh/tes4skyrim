// A script INSTANCE: one placed reference running one TES3 object script.
//
// Dialogue runs a result script against the speaker; an object script runs
// against a thing in the world, every tick while it is loaded, and keeps its
// own locals. The instance is what holds that binding.
//
// 🛑 Keyed by the PLACEMENT, never the base record. 798 scripted bases of
// TR_Mainland are placed more than once and one is placed 116 times, so a base
// key would give every copy one shared set of variables.
// See: docs/plans/morrowind_object_scripts.md#instances

#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "game_actor.h"
#include "script_context.h"

namespace mwruntime {

// The events TES3 raises on an object for ONE tick, which the script reads
// through `OnActivate`, `OnDeath` and their kin and the engine then clears.
// See: docs/plans/morrowind_object_scripts.md#events
struct ObjectEvents {
    bool activated = false;
    bool died = false;
    bool cellChanged = false;
    // The engine-written LOCALS, which are not opcodes: a script declares
    // `short OnPCEquip` itself and the engine writes the variable.
    bool pcEquipped = false;
    bool pcAdded = false;
    bool pcDropped = false;
    bool pcHitMe = false;

    bool Any() const;
    void Clear();
};

// One placed reference's script. Its locals live in DialogueState under
// `Key()`, so the co-save persists them with everything else.
class ObjectScript {
public:
    // `plugin` and `localFormId` identify the PLACEMENT; `baseId` is the TES3
    // id of the record it places, which is what commands naming it resolve on.
    ObjectScript(std::string plugin, std::uint32_t localFormId,
                 std::string baseId, std::string script);

    // A GLOBAL script, which `StartScript` runs with no placement at all. Its
    // locals live under the script's own name, which is how dialogue and other
    // scripts read them (`ScriptName.variable`). `target` is the id its bare
    // commands act on, or "" when it was started with none.
    ObjectScript(std::string script, std::string target);

    // The owner string this instance's locals are stored under.
    const std::string& Key() const { return mKey; }
    const std::string& Script() const { return mScript; }
    const std::string& BaseId() const { return mBaseId; }
    std::uint32_t LocalFormId() const { return mLocalFormId; }
    const std::string& Plugin() const { return mPlugin; }

    ObjectEvents& Events() { return mEvents; }

    // Raises `died` the FIRST time the reference is seen dead, so `OnDeath`
    // fires once as TES3 has it rather than every tick of a corpse.
    void PollDeath();

    // The FormID this placement has in the RUNNING game, learned when the
    // engine first handed us the reference. 0 until then.
    std::uint32_t RuntimeFormId() const { return mRuntimeFormId; }
    void SetRuntimeFormId(std::uint32_t id) { mRuntimeFormId = id; }

    // Whether the object has EVER been seen loaded. An instance that has not
    // cannot have unloaded, which is what keeps a just-spawned reference from
    // being dropped in the frames before its 3D exists.
    // See: docs/commentary/morrowind_runtime.md#a-spawn-is-not-loaded-on-its-first-frame
    bool WasLoaded() const { return mWasLoaded; }
    void MarkLoaded() { mWasLoaded = true; }

    // Runs the body once. False when it did not compile or threw. Writes the
    // engine-written locals first and clears every event after, which is the
    // one-frame lifetime TES3 gives them.
    bool RunOnce();

private:
    std::string mPlugin;
    std::uint32_t mLocalFormId = 0;
    std::string mBaseId;
    std::string mScript;
    std::string mKey;
    ObjectEvents mEvents;
    // Whether the death has already been reported, so it is raised once.
    bool mDeathSeen = false;
    std::uint32_t mRuntimeFormId = 0;
    bool mWasLoaded = false;
};

// Holds the GameActor that ObjectContext hands its own base class.
//
// 🛑 A base class is constructed BEFORE any member, so a context cannot pass
// its own member to DialogueContext's constructor -- it would bind a reference
// to an object that does not exist yet. This base exists only to be
// constructed first.
struct ObjectActorHolder {
    explicit ObjectActorHolder(std::string baseId) : actor(std::move(baseId)) {}
    GameActor actor;
};

// The Context an object script runs under: the shared one, with locals bound
// to the INSTANCE rather than to a dialogue speaker, and a message box that
// goes to Skyrim's notification because no dialogue menu is open.
class ObjectContext : private ObjectActorHolder, public DialogueContext {
public:
    explicit ObjectContext(ObjectScript& instance);

    ESM::RefId getTarget() const override;
    void messageBox(std::string_view message,
                    const std::vector<std::string>& buttons) override;

protected:
    const ScriptLocals* Layout() const override;
    std::string OwnerKey() const override;

private:
    ObjectScript& mInstance;
};

// The instance whose body is running right now, or null outside a tick. The
// event opcodes read their flags from it: `OnActivate` is not a world query
// but a one-tick flag belonging to THIS placement.
ObjectScript* RunningInstance();

// Every placement that runs a script, by the FormID it has in the RUNNING
// game, built once the load order is known. Null for anything else.
//
// 🛑 The sidecar keys instances by (plugin, converting-time local id); the
// engine hands out the player's OWN FormID. This is the bridge, and it is why
// it cannot be built until Game.GetFormFromFile can answer.
// See: docs/plans/morrowind_object_scripts.md#instances
ObjectScript* InstanceForRef(std::uint32_t runtimeFormId);

// Records that `runtimeFormId` is the staged placement `(plugin, local)`.
// Called once per instance at load, by the code that owns Game.GetFormFromFile.
void BindInstance(std::uint32_t runtimeFormId, const std::string& plugin,
                  std::uint32_t localFormId);

// Gives a reference CREATED at runtime the script its base record runs, keyed
// by the FormID the engine just minted for it. Does nothing when the base runs
// no script. `ScriptOf` answers which script that is.
//
void BindSpawnedInstance(std::uint32_t runtimeFormId,
                         const std::string& baseId);

void ClearInstanceBindings();
std::size_t BoundInstanceCount();

// Forgets the binding for one reference, so it stops ticking. Its LOCALS are
// kept: TES3 keeps a local script's variables across an unload, and the
// instance rebinds with them intact the next time the cell loads.
// See: docs/plans/morrowind_object_scripts.md#unload-with-the-cell
void UnbindInstance(std::uint32_t runtimeFormId);

// Every instance bound to a live reference, which is the set the tick runs.
std::vector<ObjectScript*> BoundInstances();

// The instance a placed reference runs, created on first use and kept for the
// session, or null when that placement runs no script.
ObjectScript* InstanceFor(const std::string& plugin, std::uint32_t localFormId,
                          const std::string& baseId);

// An instance already created for this placement, or null -- no table lookup
// and nothing created, for the paths that only want to raise an event.
ObjectScript* FindInstance(const std::string& plugin,
                           std::uint32_t localFormId);

// Runs every global script `StartScript` left running, once each. A script
// that stops itself, or starts another, takes effect on the NEXT tick.
// Returns how many ran.
std::size_t RunGlobalScripts();

// How many instances this session has created.
std::size_t LiveInstanceCount();

// Drops every instance. The locals stay in DialogueState, which owns them.
void ClearInstances();

}  // namespace mwruntime
