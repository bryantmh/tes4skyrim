#include "script_context.h"

#include "dialogue_state.h"
#include "log.h"
#include "script_tables.h"

namespace mwruntime {

namespace {

// TES3 factions have ten ranks, 0..9.
constexpr int kTopRank = 9;

// The faction's authored name for a rank, or "" when the sidecar has none.
std::string RankName(const std::string& faction, int rank) {
    if (faction.empty() || rank < 0 || rank > kTopRank) return std::string();
    const FactionDef* def = FindFaction(faction);
    if (!def) return std::string();
    const std::size_t at = static_cast<std::size_t>(rank);
    return at < def->rankNames.size() ? def->rankNames[at] : std::string();
}

}  // namespace

DialogueContext::DialogueContext(const ActorView& actor, std::string actorName,
                                 std::string playerName)
    : mActor(actor), mActorName(std::move(actorName)),
      mPlayerName(std::move(playerName)) {}

ESM::RefId DialogueContext::getTarget() const {
    return ESM::RefId::stringRefId(mActor.Id());
}

// Compiled code addresses a local by its index among the locals of its type,
// in declaration order -- the order SpeakerLocals declared them in.
const std::string& DialogueContext::LocalName(char type, int index) const {
    static const std::string kNone;
    const ScriptLocals* locals = FindScriptLocals(ScriptOf(mActor.Id()));
    if (!locals || index < 0) return kNone;
    const std::vector<std::string>& names =
        type == 's' ? locals->shorts : type == 'l' ? locals->longs
                                                   : locals->floats;
    return static_cast<std::size_t>(index) < names.size()
               ? names[static_cast<std::size_t>(index)]
               : kNone;
}

float DialogueContext::Local(char type, int index) const {
    return State().Var(mActor.Id(), LocalName(type, index));
}

void DialogueContext::SetLocal(char type, int index, float value) {
    const std::string& name = LocalName(type, index);
    if (!name.empty()) State().SetVar(mActor.Id(), name, value);
}

int DialogueContext::getLocalShort(int index) const {
    return static_cast<int>(Local('s', index));
}

int DialogueContext::getLocalLong(int index) const {
    return static_cast<int>(Local('l', index));
}

float DialogueContext::getLocalFloat(int index) const {
    return Local('f', index);
}

void DialogueContext::setLocalShort(int index, int value) {
    SetLocal('s', index, static_cast<float>(value));
}

void DialogueContext::setLocalLong(int index, int value) {
    SetLocal('l', index, static_cast<float>(value));
}

void DialogueContext::setLocalFloat(int index, float value) {
    SetLocal('f', index, value);
}

// A message box raised during dialogue is shown IN the dialogue, as a notice.
void DialogueContext::messageBox(std::string_view message,
                                 const std::vector<std::string>&) {
    State().messages.emplace_back(message);
}

void DialogueContext::report(const std::string& message) {
    Log("context: %s", message.c_str());
}

int DialogueContext::getGlobalShort(std::string_view name) const {
    return static_cast<int>(State().Global(std::string(name)));
}

int DialogueContext::getGlobalLong(std::string_view name) const {
    return getGlobalShort(name);
}

float DialogueContext::getGlobalFloat(std::string_view name) const {
    return State().Global(std::string(name));
}

void DialogueContext::setGlobalShort(std::string_view name, int value) {
    State().SetGlobal(std::string(name), static_cast<float>(value));
}

void DialogueContext::setGlobalLong(std::string_view name, int value) {
    State().SetGlobal(std::string(name), static_cast<float>(value));
}

void DialogueContext::setGlobalFloat(std::string_view name, float value) {
    State().SetGlobal(std::string(name), value);
}

std::vector<std::string> DialogueContext::getGlobals() const {
    return State().Globals();
}

char DialogueContext::getGlobalType(std::string_view name) const {
    return State().HasGlobal(std::string(name)) ? 'f' : ' ';
}

std::string DialogueContext::getActionBinding(std::string_view action) const {
    return std::string(action);
}

std::string_view DialogueContext::getActorName() const { return mActorName; }

std::string_view DialogueContext::getNPCRace() const {
    mRace = mActor.Race();
    return mRace;
}

std::string_view DialogueContext::getNPCClass() const {
    mClass = mActor.Class();
    return mClass;
}

std::string_view DialogueContext::getNPCFaction() const {
    mFaction = mActor.PrimaryFaction();
    return mFaction;
}

std::string_view DialogueContext::getNPCRank() const {
    mRank = RankName(mActor.PrimaryFaction(), mActor.PrimaryFactionRank());
    return mRank;
}

std::string_view DialogueContext::getPCName() const { return mPlayerName; }

std::string_view DialogueContext::getPCRace() const {
    mRace = mActor.PlayerRace();
    return mRace;
}

std::string_view DialogueContext::getPCClass() const {
    mClass = mActor.PlayerClass();
    return mClass;
}

// 🛑 A NON-MEMBER reads rank 0, not "no rank": Morrowind's own quirk, and
// what makes "You are now %PCName the %NextPCRank" read correctly in the very
// INFO that admits the player to the guild.
std::string_view DialogueContext::getPCRank() const {
    const RefId faction = mActor.PrimaryFaction();
    if (faction.empty()) return "%";
    const int rank = mActor.PlayerFactionRank(faction);
    mRank = RankName(faction, rank < 0 ? 0 : rank);
    return mRank;
}

std::string_view DialogueContext::getPCNextRank() const {
    const RefId faction = mActor.PrimaryFaction();
    if (faction.empty()) return "%";
    const int next = mActor.PlayerFactionRank(faction) + 1;
    mRank = RankName(faction, next > kTopRank ? kTopRank : next);
    return mRank;
}

int DialogueContext::getPCBounty() const { return mActor.PlayerCrimeLevel(); }

std::string_view DialogueContext::getCurrentCellName() const {
    mCell = mActor.PlayerCellName();
    return mCell;
}

// `Owner.name`: the owner is a global script or a reference, and either way
// its variables live under its own id.
int DialogueContext::getMemberShort(ESM::RefId id, std::string_view name,
                                    bool) const {
    return static_cast<int>(State().Var(id.getRefIdString(), std::string(name)));
}

int DialogueContext::getMemberLong(ESM::RefId id, std::string_view name,
                                   bool global) const {
    return getMemberShort(id, name, global);
}

float DialogueContext::getMemberFloat(ESM::RefId id, std::string_view name,
                                      bool) const {
    return State().Var(id.getRefIdString(), std::string(name));
}

void DialogueContext::setMemberShort(ESM::RefId id, std::string_view name,
                                     int value, bool) {
    State().SetVar(id.getRefIdString(), std::string(name),
                   static_cast<float>(value));
}

void DialogueContext::setMemberLong(ESM::RefId id, std::string_view name,
                                    int value, bool global) {
    setMemberShort(id, name, value, global);
}

void DialogueContext::setMemberFloat(ESM::RefId id, std::string_view name,
                                     float value, bool) {
    State().SetVar(id.getRefIdString(), std::string(name), value);
}

}  // namespace mwruntime
