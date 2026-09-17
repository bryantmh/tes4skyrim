// The ActorView the MENU runs against, backed by what the runtime knows now.
//
// 🛑 PARTIAL BY DESIGN, and honest about it. Identity -- the TES3 id, which is
// what MWAC.txt gives us -- is real; every stat, faction and world query still
// returns a neutral default, so a filter rule that reads one passes rather than
// silently excluding an actor who should have spoken.
//
// Each stub is one engine read away from being real, and they land as the
// adapters in the plan's 2e/2e-bis arrive. Until then `Stubbed()` reports how
// many were consulted, so the log says what the menu did NOT know.
// See: docs/commentary/morrowind_runtime.md#the-seam

#pragma once

#include <cstddef>
#include <string>

#include "actor.h"

namespace mwruntime {

// An actor identified by its TES3 id, with neutral answers elsewhere.
class GameActor : public ActorView {
public:
    explicit GameActor(std::string id);

    RefId Id() const override;
    bool  IsNpc() const override;
    RefId Race() const override;
    RefId Class() const override;
    bool  IsFemale() const override;

    RefId PrimaryFaction() const override;
    int   PrimaryFactionRank() const override;
    int   PlayerFactionRank(const RefId& faction) const override;
    bool  PlayerExpelled(const RefId& faction) const override;
    int   FactionReaction(const RefId& a, const RefId& b) const override;

    int Disposition() const override;

    RefId PlayerRace() const override;
    RefId PlayerClass() const override;
    bool  PlayerIsFemale() const override;
    int   PlayerLevel() const override;
    int   PlayerHealthPercent() const override;
    int   PlayerCrimeLevel() const override;
    int   PlayerSkill(int tes3Index) const override;
    int   PlayerAttribute(int tes3Index) const override;

    std::string PlayerCellName() const override;
    int  Health() const override;
    int  Level() const override;
    bool Detected() const override;
    bool Alarmed() const override;
    bool Attacked() const override;
    bool TalkedToPlayer() const override;
    int  DeadCount(const RefId& id) const override;
    int  ItemCount(const RefId& id) const override;
    int  JournalIndex(const RefId& quest) const override;

    float LocalVariable(const std::string& name, bool* found) const override;
    float GlobalVariable(const std::string& name, bool* found) const override;

    int Choice() const override;

    // How many stubbed answers this actor has been asked for.
    std::size_t Stubbed() const { return mStubbed; }

private:
    // Counts one consultation of a value we cannot yet read from the game.
    int Stub(int value) const;

    std::string mId;
    mutable std::size_t mStubbed = 0;
};

}  // namespace mwruntime
