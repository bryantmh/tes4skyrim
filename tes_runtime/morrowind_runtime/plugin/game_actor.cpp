#include "game_actor.h"

namespace mwruntime {

namespace {

// The neutral answers. Each is chosen so a filter rule reading it PASSES:
// TES3 gates keep an under-qualified player out, and a stub that failed would
// hide dialogue that should have shown.
// See: docs/commentary/morrowind_runtime.md#the-seam
constexpr int kOpenSkill = 100;
constexpr int kNeutralDisposition = 50;
constexpr int kAlive = 100;
constexpr int kFirstLevel = 1;

}  // namespace

GameActor::GameActor(std::string id) : mId(std::move(id)) {}

int GameActor::Stub(int value) const {
    ++mStubbed;
    return value;
}

RefId GameActor::Id() const { return mId; }

bool GameActor::IsNpc() const { return true; }

RefId GameActor::Race() const { return RefId(); }

RefId GameActor::Class() const { return RefId(); }

bool GameActor::IsFemale() const { return false; }

RefId GameActor::PrimaryFaction() const { return RefId(); }

int GameActor::PrimaryFactionRank() const { return Stub(-1); }

int GameActor::PlayerFactionRank(const RefId&) const { return Stub(-1); }

bool GameActor::PlayerExpelled(const RefId&) const { return false; }

int GameActor::FactionReaction(const RefId&, const RefId&) const {
    return Stub(0);
}

int GameActor::Disposition() const { return Stub(kNeutralDisposition); }

RefId GameActor::PlayerRace() const { return RefId(); }

RefId GameActor::PlayerClass() const { return RefId(); }

bool GameActor::PlayerIsFemale() const { return false; }

int GameActor::PlayerLevel() const { return Stub(kFirstLevel); }

int GameActor::PlayerHealthPercent() const { return Stub(kAlive); }

int GameActor::PlayerCrimeLevel() const { return Stub(0); }

int GameActor::PlayerSkill(int) const { return Stub(kOpenSkill); }

int GameActor::PlayerAttribute(int) const { return Stub(kOpenSkill); }

std::string GameActor::PlayerCellName() const { return std::string(); }

int GameActor::Health() const { return Stub(kAlive); }

int GameActor::Level() const { return Stub(kFirstLevel); }

bool GameActor::Detected() const { return true; }

bool GameActor::Alarmed() const { return false; }

bool GameActor::Attacked() const { return false; }

bool GameActor::TalkedToPlayer() const { return false; }

int GameActor::DeadCount(const RefId&) const { return Stub(0); }

int GameActor::ItemCount(const RefId&) const { return Stub(0); }

int GameActor::JournalIndex(const RefId&) const { return Stub(0); }

// A variable that does not EXIST fails its rule in TES3, which is not the same
// as reading zero -- so `found` stays false rather than reporting a value.
float GameActor::LocalVariable(const std::string&, bool* found) const {
    *found = false;
    ++mStubbed;
    return 0.0f;
}

float GameActor::GlobalVariable(const std::string&, bool* found) const {
    *found = false;
    ++mStubbed;
    return 0.0f;
}

int GameActor::Choice() const { return -1; }

}  // namespace mwruntime
