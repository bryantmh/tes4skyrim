// The Interpreter::Context a conversation runs under.
//
// OpenMW's `fixDefinesDialog` -- the thing that turns "%name" into the
// speaker's name -- takes a Context, and so will every result script once
// they run. This is that Context, backed by the same ActorView the filter
// uses. Locals, globals and members live in DialogueState, which is what the
// co-save will persist.
// See: docs/commentary/morrowind_runtime.md#the-context

#pragma once

#include <string>
#include <string_view>
#include <vector>

#include <components/interpreter/context.hpp>

#include "actor.h"
#include "script_tables.h"

namespace tesruntime::mw {

class DialogueContext : public Interpreter::Context {
public:
    DialogueContext(const ActorView& actor, std::string actorName,
                    std::string playerName);

    ESM::RefId getTarget() const override;

    int   getLocalShort(int index) const override;
    int   getLocalLong(int index) const override;
    float getLocalFloat(int index) const override;
    void  setLocalShort(int index, int value) override;
    void  setLocalLong(int index, int value) override;
    void  setLocalFloat(int index, float value) override;

    void messageBox(std::string_view message,
                    const std::vector<std::string>& buttons) override;
    void report(const std::string& message) override;

    int   getGlobalShort(std::string_view name) const override;
    int   getGlobalLong(std::string_view name) const override;
    float getGlobalFloat(std::string_view name) const override;
    void  setGlobalShort(std::string_view name, int value) override;
    void  setGlobalLong(std::string_view name, int value) override;
    void  setGlobalFloat(std::string_view name, float value) override;
    std::vector<std::string> getGlobals() const override;
    char  getGlobalType(std::string_view name) const override;

    std::string getActionBinding(std::string_view action) const override;

    std::string_view getActorName() const override;
    std::string_view getNPCRace() const override;
    std::string_view getNPCClass() const override;
    std::string_view getNPCFaction() const override;
    std::string_view getNPCRank() const override;
    std::string_view getPCName() const override;
    std::string_view getPCRace() const override;
    std::string_view getPCClass() const override;
    std::string_view getPCRank() const override;
    std::string_view getPCNextRank() const override;
    int getPCBounty() const override;
    std::string_view getCurrentCellName() const override;

    int   getMemberShort(ESM::RefId id, std::string_view name,
                         bool global) const override;
    int   getMemberLong(ESM::RefId id, std::string_view name,
                        bool global) const override;
    float getMemberFloat(ESM::RefId id, std::string_view name,
                         bool global) const override;
    void  setMemberShort(ESM::RefId id, std::string_view name, int value,
                         bool global) override;
    void  setMemberLong(ESM::RefId id, std::string_view name, int value,
                        bool global) override;
    void  setMemberFloat(ESM::RefId id, std::string_view name, float value,
                         bool global) override;

protected:
    // The layout the compiled local indices address, and which owner's values
    // they read. A dialogue speaker takes both from its own script; an object
    // script instance overrides them to bind its PLACEMENT and the layout the
    // COMPILER built from the body.
    // See: docs/plans/morrowind_object_scripts.md#instances
    virtual const ScriptLocals* Layout() const;
    virtual std::string OwnerKey() const;

private:
    // The speaker's local at `index` among its locals of `type`.
    const std::string& LocalName(char type, int index) const;
    float Local(char type, int index) const;
    void  SetLocal(char type, int index, float value);

    const ActorView& mActor;
    std::string mActorName;
    std::string mPlayerName;
    mutable std::string mCell;
    mutable std::string mRace;
    mutable std::string mClass;
    mutable std::string mFaction;
    mutable std::string mRank;
};

}  // namespace tesruntime::mw
