#include "game_actor.h"

#include "dialogue_state.h"
#include "script_tables.h"

namespace mwruntime {

namespace {

// The neutral answers. Each is chosen so a filter rule reading it PASSES:
// TES3 gates keep an under-qualified player out, and a stub that failed would
// hide dialogue that should have shown.
// See: docs/commentary/morrowind_runtime.md#the-seam
constexpr int kOpenSkill = 100;
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

// Who the speaker IS comes from their NPC_ record, staged as MWNP.txt. An
// actor the table does not know answers as an unaffiliated stranger.
RefId GameActor::Race() const {
    const ActorDef* def = FindActor(mId);
    return def ? def->race : RefId();
}

RefId GameActor::Class() const {
    const ActorDef* def = FindActor(mId);
    return def ? def->clazz : RefId();
}

bool GameActor::IsFemale() const {
    const ActorDef* def = FindActor(mId);
    return def && def->female;
}

RefId GameActor::PrimaryFaction() const {
    const ActorDef* def = FindActor(mId);
    return def ? def->faction : RefId();
}

int GameActor::PrimaryFactionRank() const {
    const ActorDef* def = FindActor(mId);
    return def && !def->faction.empty() ? def->rank : -1;
}

int GameActor::PlayerFactionRank(const RefId& faction) const {
    return State().Faction(faction).rank;
}

bool GameActor::PlayerExpelled(const RefId& faction) const {
    return State().Faction(faction).expelled;
}

int GameActor::PlayerFactionReputation(const RefId& faction) const {
    return State().Faction(faction).reputation;
}

int GameActor::FactionReaction(const RefId& a, const RefId& b) const {
    return State().FactionReaction(a, b);
}

int GameActor::Disposition() const { return State().Disposition(mId); }

RefId GameActor::PlayerRace() const { return RefId(); }

RefId GameActor::PlayerClass() const { return RefId(); }

bool GameActor::PlayerIsFemale() const { return false; }

int GameActor::PlayerLevel() const {
    return Hooks().playerLevel ? Hooks().playerLevel() : Stub(kFirstLevel);
}

int GameActor::PlayerHealthPercent() const { return Stub(kAlive); }

int GameActor::PlayerCrimeLevel() const {
    return static_cast<int>(State().crimeLevel);
}

int GameActor::PlayerSkill(int) const { return Stub(kOpenSkill); }

int GameActor::PlayerAttribute(int) const { return Stub(kOpenSkill); }

std::string GameActor::PlayerCellName() const {
    if (!Hooks().playerCell) {
        ++mStubbed;
        return std::string();
    }
    return Hooks().playerCell();
}

int GameActor::Health() const { return Stub(kAlive); }

int GameActor::Level() const {
    const ActorDef* def = FindActor(mId);
    return def ? def->level : Stub(kFirstLevel);
}

bool GameActor::Detected() const { return true; }

bool GameActor::Alarmed() const { return false; }

bool GameActor::Attacked() const { return false; }

bool GameActor::TalkedToPlayer() const { return false; }

int GameActor::DeadCount(const RefId& id) const {
    return State().DeadCount(id);
}

int GameActor::AiSetting(int which) const {
    return State().AiSetting(mId, which);
}

// An Item rule asks what the PLAYER carries, never the speaker: TES3 writes
// the bare item id and the subject is implicit.
int GameActor::ItemCount(const RefId& id) const {
    if (!Hooks().itemCount) return Stub(0);
    return Hooks().itemCount("player", id);
}

int GameActor::JournalIndex(const RefId& quest) const {
    return State().JournalIndex(quest);
}

// A variable that does not EXIST fails its rule in TES3, which is not the same
// as reading zero -- so `found` stays false rather than reporting a value.
float GameActor::LocalVariable(const std::string& name, bool* found) const {
    const ScriptLocals* locals = FindScriptLocals(ScriptOf(mId));
    *found = locals && locals->TypeOf(name) != ' ';
    return State().Var(mId, name);
}

float GameActor::GlobalVariable(const std::string& name, bool* found) const {
    *found = State().HasGlobal(name);
    if (!*found) ++mStubbed;
    return State().Global(name);
}

int GameActor::Choice() const { return State().choice; }

}  // namespace mwruntime
