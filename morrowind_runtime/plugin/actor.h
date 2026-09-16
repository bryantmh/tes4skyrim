// What the filter needs to know about an actor and the player.
//
// THE SEAM. MWDialogue::Filter reaches the engine through MWWorld::Ptr and
// MWBase::Environment; this replaces both with one interface, so the ported
// filter logic compiles against Skyrim without dragging in mwworld/ or
// mwmechanics/ (following those resolves 1,604 files -- the whole engine).
//
// Two implementations: MwGameActor reads the running game, and the test
// harness supplies literal values so the filter is checkable with no Skyrim.
// See: docs/commentary/morrowind_runtime.md#the-seam

#pragma once

#include <cstdint>
#include <string>

namespace mwruntime {

// TES3 ids are case-insensitive strings; every id here is one.
using RefId = std::string;

// The speaker, and the player's side of the same questions. Everything the
// ported filter asks about the world goes through here.
class ActorView {
public:
    virtual ~ActorView() = default;

    // --- identity -------------------------------------------------------
    virtual RefId Id() const = 0;
    virtual bool  IsNpc() const = 0;
    virtual RefId Race() const = 0;
    virtual RefId Class() const = 0;
    virtual bool  IsFemale() const = 0;

    // --- faction --------------------------------------------------------
    virtual RefId PrimaryFaction() const = 0;
    virtual int   PrimaryFactionRank() const = 0;
    // The player's rank in `faction`, or -1 when not a member.
    virtual int   PlayerFactionRank(const RefId& faction) const = 0;
    virtual bool  PlayerExpelled(const RefId& faction) const = 0;
    virtual int   FactionReaction(const RefId& a, const RefId& b) const = 0;

    // --- disposition ----------------------------------------------------
    // No Skyrim equivalent: the DLL owns this outright.
    virtual int Disposition() const = 0;

    // --- the player -----------------------------------------------------
    virtual RefId PlayerRace() const = 0;
    virtual RefId PlayerClass() const = 0;
    virtual bool  PlayerIsFemale() const = 0;
    virtual int   PlayerLevel() const = 0;
    virtual int   PlayerHealthPercent() const = 0;
    virtual int   PlayerCrimeLevel() const = 0;
    // A TES3 skill or attribute index, mapped to Skyrim or stubbed.
    virtual int   PlayerSkill(int tes3Index) const = 0;
    virtual int   PlayerAttribute(int tes3Index) const = 0;

    // --- world ----------------------------------------------------------
    // The cell name the player is in; TES3 matches it as a PREFIX.
    virtual std::string PlayerCellName() const = 0;
    virtual int  Health() const = 0;
    virtual int  Level() const = 0;
    virtual bool Detected() const = 0;
    virtual bool Alarmed() const = 0;
    virtual bool Attacked() const = 0;
    virtual bool TalkedToPlayer() const = 0;
    virtual int  DeadCount(const RefId& id) const = 0;
    virtual int  ItemCount(const RefId& id) const = 0;
    virtual int  JournalIndex(const RefId& quest) const = 0;

    // --- script variables ------------------------------------------------
    // A local on this actor's script, or a global. `found` says whether the
    // name exists at all, which TES3 treats as "filter fails", not "zero".
    virtual float LocalVariable(const std::string& name, bool* found) const = 0;
    virtual float GlobalVariable(const std::string& name, bool* found) const = 0;

    // --- the dialogue session --------------------------------------------
    // Which branch the player last picked; -1 outside a choice.
    virtual int Choice() const = 0;
};

}  // namespace mwruntime
