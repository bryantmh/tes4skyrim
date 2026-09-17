#include "script_context.h"

#include "log.h"

namespace mwruntime {

namespace {

// Every write to state this context does not hold yet is logged once per
// kind, so the log says what a script tried to change.
void Unheld(const char* what, std::string_view name) {
    Log("context: %s '%.*s' is not held yet -- ignored", what,
        static_cast<int>(name.size()), name.data());
}

}  // namespace

DialogueContext::DialogueContext(const ActorView& actor, std::string actorName,
                                 std::string playerName)
    : mActor(actor), mActorName(std::move(actorName)),
      mPlayerName(std::move(playerName)) {}

ESM::RefId DialogueContext::getTarget() const {
    return ESM::RefId::stringRefId(mActor.Id());
}

int   DialogueContext::getLocalShort(int) const { return 0; }
int   DialogueContext::getLocalLong(int) const { return 0; }
float DialogueContext::getLocalFloat(int) const { return 0.0f; }
void  DialogueContext::setLocalShort(int, int) { Unheld("local", "short"); }
void  DialogueContext::setLocalLong(int, int) { Unheld("local", "long"); }
void  DialogueContext::setLocalFloat(int, float) { Unheld("local", "float"); }

void DialogueContext::messageBox(std::string_view message,
                                 const std::vector<std::string>&) {
    Log("context: MessageBox \"%.*s\"", static_cast<int>(message.size()),
        message.data());
}

void DialogueContext::report(const std::string& message) {
    Log("context: %s", message.c_str());
}

int DialogueContext::getGlobalShort(std::string_view name) const {
    bool found = false;
    return static_cast<int>(mActor.GlobalVariable(std::string(name), &found));
}

int DialogueContext::getGlobalLong(std::string_view name) const {
    return getGlobalShort(name);
}

float DialogueContext::getGlobalFloat(std::string_view name) const {
    bool found = false;
    return mActor.GlobalVariable(std::string(name), &found);
}

void DialogueContext::setGlobalShort(std::string_view name, int) {
    Unheld("global", name);
}

void DialogueContext::setGlobalLong(std::string_view name, int) {
    Unheld("global", name);
}

void DialogueContext::setGlobalFloat(std::string_view name, float) {
    Unheld("global", name);
}

std::vector<std::string> DialogueContext::getGlobals() const { return {}; }

char DialogueContext::getGlobalType(std::string_view) const { return ' '; }

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

std::string_view DialogueContext::getNPCRank() const { return ""; }

std::string_view DialogueContext::getPCName() const { return mPlayerName; }

std::string_view DialogueContext::getPCRace() const {
    mRace = mActor.PlayerRace();
    return mRace;
}

std::string_view DialogueContext::getPCClass() const {
    mClass = mActor.PlayerClass();
    return mClass;
}

std::string_view DialogueContext::getPCRank() const { return ""; }

std::string_view DialogueContext::getPCNextRank() const { return ""; }

int DialogueContext::getPCBounty() const { return mActor.PlayerCrimeLevel(); }

std::string_view DialogueContext::getCurrentCellName() const {
    mCell = mActor.PlayerCellName();
    return mCell;
}

int DialogueContext::getMemberShort(ESM::RefId, std::string_view,
                                    bool) const {
    return 0;
}

int DialogueContext::getMemberLong(ESM::RefId, std::string_view, bool) const {
    return 0;
}

float DialogueContext::getMemberFloat(ESM::RefId, std::string_view,
                                      bool) const {
    return 0.0f;
}

void DialogueContext::setMemberShort(ESM::RefId, std::string_view name, int,
                                     bool) {
    Unheld("member", name);
}

void DialogueContext::setMemberLong(ESM::RefId, std::string_view name, int,
                                    bool) {
    Unheld("member", name);
}

void DialogueContext::setMemberFloat(ESM::RefId, std::string_view name, float,
                                     bool) {
    Unheld("member", name);
}

}  // namespace mwruntime
