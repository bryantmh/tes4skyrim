#include "dialogue_state.h"
#include "filter.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <sstream>

#include "log.h"
#include "scope.h"
#include "script_tables.h"
#include "store.h"

namespace tesruntime::mw {

namespace {

// What an actor feels when no NPC_ record says otherwise.
constexpr int kNeutralDisposition = 50;

// TES3 factions have ten ranks, 0..9.
constexpr int kTopRank = 9;

// The co-save's first line; a save with another header is left alone.
//
// 🛑 Version 1 keyed every id bare, so one plugin's state answered for any
// sibling staging the same id. Version 2 keys each by its origin plugin
// (StateKey), and a version 1 save is read through the merged view -- the one
// it was written under -- so it loads to exactly the state it held.
// See: docs/commentary/morrowind_runtime.md#load-order
constexpr const char* kFormat = "MWSTATE\t2";
constexpr const char* kFormatV1 = "MWSTATE\t1";

// The global script the TES3 engine starts by name (OpenMW `addStartup`).
constexpr const char* kMainScript = "Main";

std::string Lower(std::string id) {
    std::transform(id.begin(), id.end(), id.begin(),
                   [](unsigned char c) { return static_cast<char>(::tolower(c)); });
    return id;
}

// The state key of a TES3 id, in the current layer's view.
std::string Key(const std::string& id) { return StateKey(id); }

// A global's key. The engine's own clock globals are one value for every
// plugin, so they stay bare whichever layer reads or writes them.
std::string GlobalKey(const std::string& name) {
    return GlobalLayer(name) == kEveryLayer ? Lower(name) : StateKey(name);
}

// True when the journal topic `quest` authors an entry at `index`.
bool EntryExists(const std::string& quest, int index) {
    const Topic* topic = FindTopic(quest);
    if (!topic) return false;
    for (const Info& info : topic->infos) {
        if (info.journalIndex == index && LayerVisible(info.layer)) return true;
    }
    return false;
}

std::vector<std::string> Fields(const std::string& line) {
    std::vector<std::string> out;
    std::size_t start = 0;
    while (true) {
        const std::size_t end = line.find('\t', start);
        out.push_back(line.substr(start, end == std::string::npos
                                             ? std::string::npos
                                             : end - start));
        if (end == std::string::npos) return out;
        start = end + 1;
    }
}

int Int(const std::string& text) { return std::atoi(text.c_str()); }

float Float(const std::string& text) {
    return static_cast<float>(std::atof(text.c_str()));
}

// The player's standing in one faction, pushed into the game's own faction.
// See: docs/commentary/morrowind_runtime.md#player-factions
void PushFaction(const std::string& faction, const Membership& member) {
    if (Hooks().applyPlayerFaction) {
        Hooks().applyPlayerFaction(faction, member.rank, member.expelled);
    }
}

}  // namespace

GameHooks& Hooks() {
    static GameHooks hooks;
    return hooks;
}

int DialogueState::JournalIndex(const std::string& quest) const {
    const auto it = mJournal.find(Key(quest));
    return it == mJournal.end() ? 0 : it->second;
}

void DialogueState::SetJournalIndex(const std::string& quest, int index) {
    mJournal[Key(quest)] = index;
    Log("journal: %s = %d", quest.c_str(), index);
    if (Hooks().setQuestStage) Hooks().setQuestStage(quest, index);
}

// Quest::addEntry: the entry is recorded once and the index only ever rises.
// An index with no authored entry still raises it, as OpJournal's catch does.
// The game is shown the ENTRY's stage either way: a lower entry added late is
// still a new page in the journal.
bool DialogueState::AddJournalEntry(const std::string& quest, int index) {
    const std::string key = Key(quest);
    const std::pair<std::string, int> entry(key, index);
    const bool authored = EntryExists(quest, index);
    const bool fresh = authored && std::find(mEntries.begin(), mEntries.end(),
                                             entry) == mEntries.end();
    if (fresh) mEntries.push_back(entry);
    if (index > JournalIndex(quest)) {
        SetJournalIndex(quest, index);
    } else if (fresh && Hooks().setQuestStage) {
        Hooks().setQuestStage(quest, index);
    }
    return fresh;
}

int DialogueState::Disposition(const std::string& actor) const {
    const auto it = mDisposition.find(Key(actor));
    if (it != mDisposition.end()) return std::clamp(it->second, 0, 100);
    const ActorDef* def = FindActor(actor);
    return std::clamp(def ? def->disposition : kNeutralDisposition, 0, 100);
}

void DialogueState::SetDisposition(const std::string& actor, int value) {
    mDisposition[Key(actor)] = value;
    Log("disposition: %s = %d", actor.c_str(), value);
    if (Hooks().applyAiSetting) {
        Hooks().applyAiSetting(actor, kAiFight, AiSetting(actor, kAiFight));
    }
}

void DialogueState::ModDisposition(const std::string& actor, int delta) {
    SetDisposition(actor, Disposition(actor) + delta);
}

int DialogueState::ActorRank(const std::string& actor) const {
    const auto it = mActorRank.find(Key(actor));
    if (it != mActorRank.end()) return it->second;
    const ActorDef* def = FindActor(actor);
    return def && !def->faction.empty() ? def->rank : -1;
}

void DialogueState::SetActorRank(const std::string& actor, int rank) {
    mActorRank[Key(actor)] = rank;
    Log("rank: %s = %d", actor.c_str(), rank);
}

bool DialogueState::HasGlobal(const std::string& name) const {
    return mGlobals.find(GlobalKey(name)) != mGlobals.end() || FindGlobal(name);
}

float DialogueState::Global(const std::string& name) const {
    const auto it = mGlobals.find(GlobalKey(name));
    if (it != mGlobals.end()) return it->second;
    const GlobalDef* def = FindGlobal(name);
    return def ? def->value : 0.0f;
}

// 🛑 Logged only on a CHANGE, and only when verbose. An object script re-runs
// its whole body every tick, so an unconditional line here is 30 writes a
// second of the value the global already held -- measured, 2,300 lines of
// `wearingordinatoruni = 1` from one equipped cuirass. A timer or a random
// roll changes every tick by definition, so the change test alone does not
// thin those; `LogVerbose` is what keeps them out of an ordinary run.
void DialogueState::SetGlobal(const std::string& name, float value) {
    // Against what a READER would get, not against the slot: an absent entry
    // falls back to the GLOB's declared value, so the first write is a change
    // only when it differs from that.
    const bool changed = Global(name) != value;
    mGlobals[GlobalKey(name)] = value;
    if (changed) LogVerbose("global: %s = %g", name.c_str(), value);
}

void DialogueState::SyncGlobal(const std::string& name, float value) {
    mGlobals[GlobalKey(name)] = value;
}

// The ids, not the keys: a sibling plugin's globals are not this one's.
std::vector<std::string> DialogueState::Globals() const {
    std::vector<std::string> out;
    for (const auto& entry : mGlobals) {
        const auto [layer, id] = SplitStateKey(entry.first);
        if (layer < 0 || LayerVisible(layer)) out.push_back(id);
    }
    return out;
}

bool DialogueState::HasVar(const std::string& owner,
                           const std::string& name) const {
    return mVars.count({Key(owner), Lower(name)}) != 0;
}

float DialogueState::Var(const std::string& owner,
                         const std::string& name) const {
    const auto it = mVars.find({Key(owner), Lower(name)});
    return it == mVars.end() ? 0.0f : it->second;
}

// Logged only on a CHANGE, and only when verbose, for the reason SetGlobal
// gives.
void DialogueState::SetVar(const std::string& owner, const std::string& name,
                           float value) {
    float& slot = mVars[{Key(owner), Lower(name)}];
    const bool changed = slot != value;
    slot = value;
    if (changed) {
        LogVerbose("local: %s.%s = %g", owner.c_str(), name.c_str(), value);
    }
}

bool DialogueState::KnowsTopic(const std::string& topic) const {
    return mKnownTopics.count(Key(topic)) != 0;
}

void DialogueState::LearnTopic(const std::string& topic) {
    if (mKnownTopics.insert(Key(topic)).second) {
        Log("topic: learned '%s'", topic.c_str());
    }
}

int DialogueState::AiSetting(const std::string& actor, int which) const {
    const auto it = mAiSettings.find({Key(actor), which});
    if (it != mAiSettings.end()) return it->second;
    const ActorDef* def = FindActor(actor);
    return def && which >= 0 && which < 4 ? def->aiSettings[which] : 0;
}

void DialogueState::SetAiSetting(const std::string& actor, int which,
                                 int value) {
    const bool changed = AiSetting(actor, which) != value;
    mAiSettings[{Key(actor), which}] = value;
    if (changed) Log("ai: %s setting %d = %d", actor.c_str(), which, value);
    if (Hooks().applyAiSetting) Hooks().applyAiSetting(actor, which, value);
}

bool DialogueState::ControlEnabled(int which) const {
    return which >= 0 && which < kControlSwitchCount ? mControls[which] : true;
}

// Logged and pushed only on a CHANGE, for the reason SetGlobal gives: a
// chargen script re-runs `EnablePlayerControls` every tick it is in state 2.
void DialogueState::SetControlEnabled(int which, bool on) {
    if (which < 0 || which >= kControlSwitchCount) return;
    if (mControls[which] == on) return;
    mControls[which] = on;
    Log("control: switch %d = %d", which, on ? 1 : 0);
    if (Hooks().applyControlSwitches) {
        Hooks().applyControlSwitches(mControls, kControlSwitchCount);
    }
}

bool DialogueState::MovementFlag(const std::string& actor, int which) const {
    const auto it = mMovementFlags.find({Key(actor), which});
    return it != mMovementFlags.end() && it->second;
}

void DialogueState::SetMovementFlag(const std::string& actor, int which,
                                    bool on) {
    mMovementFlags[{Key(actor), which}] = on;
    Log("move: %s flag %d = %d", actor.c_str(), which, on ? 1 : 0);
    if (Hooks().applyMovementFlag) {
        Hooks().applyMovementFlag(actor, which, on);
    }
}

const Membership& DialogueState::Faction(const std::string& faction) const {
    static const Membership kNone;
    const auto it = mFactions.find(Key(faction));
    return it == mFactions.end() ? kNone : it->second;
}

void DialogueState::JoinFaction(const std::string& faction) {
    if (faction.empty()) return;
    Membership& member = mFactions[Key(faction)];
    if (member.rank < 0) member.rank = 0;
    Log("faction: joined %s at rank %d", faction.c_str(), member.rank);
    PushFaction(faction, member);
}

void DialogueState::ChangeRank(const std::string& faction, int delta) {
    if (faction.empty()) return;
    Membership& member = mFactions[Key(faction)];
    member.rank = std::clamp(member.rank + delta, -1, kTopRank);
    Log("faction: %s rank %d", faction.c_str(), member.rank);
    PushFaction(faction, member);
}

void DialogueState::SetExpelled(const std::string& faction, bool expelled) {
    if (faction.empty()) return;
    Membership& member = mFactions[Key(faction)];
    member.expelled = expelled;
    Log("faction: %s %s", faction.c_str(), expelled ? "EXPELLED" : "readmitted");
    PushFaction(faction, member);
}

void DialogueState::SetFactionReputation(const std::string& faction,
                                         int value) {
    if (faction.empty()) return;
    mFactions[Key(faction)].reputation = value;
    Log("faction: %s reputation %d", faction.c_str(), value);
}

int DialogueState::FactionReaction(const std::string& a,
                                   const std::string& b) const {
    const auto it = mReactions.find({Key(a), Key(b)});
    return it == mReactions.end() ? 0 : it->second;
}

void DialogueState::SetFactionReaction(const std::string& a,
                                       const std::string& b, int value) {
    mReactions[{Key(a), Key(b)}] = value;
}

bool DialogueState::ScriptRunning(const std::string& script) const {
    return mRunning.count(Key(script)) != 0;
}

// The layer a started script runs as: the starter's own when it is built on
// the plugin supplying the body, so a master's script run for a dependent
// sees that dependent's ids; the supplier's otherwise.
int RunAsLayer(const std::string& script) {
    const int supplier = ScriptSourceLayer(script);
    const int current = CurrentLayer();
    if (current == kEveryLayer) return supplier;
    if (supplier < 0) return current;
    return LayersRelated(current, supplier) &&
                   LayerDepth(current) >= LayerDepth(supplier)
               ? current
               : supplier;
}

void DialogueState::StartScript(const std::string& script,
                                const std::string& target) {
    mRunning[Key(script)] = {target, LayerName(RunAsLayer(script))};
    Log("script: %s started", script.c_str());
}

void DialogueState::StopScript(const std::string& script) {
    if (mRunning.erase(Key(script))) Log("script: %s stopped", script.c_str());
}

// 🛑 `Main` is started by NAME, not by an SSCR: Morrowind.esm carries no SSCR
// at all, and `Main` is what launches `CharGen`. Each plugin starts the Main
// its own view answers with; plugins sharing one Main's origin share the one
// running copy, as TES3 has only one.
// See: docs/commentary/morrowind_runtime.md#vanilla-morrowind-chargen
void DialogueState::StartStartupScripts() {
    for (int layer = 0; layer < static_cast<int>(LayerCount()); ++layer) {
        const LayerScope scope(layer);
        if (ScriptSourceLayer(kMainScript) == layer &&
            !ScriptRunning(kMainScript)) {
            StartScript(kMainScript, std::string());
        }
    }
    for (const auto& [layer, script] : StartScripts()) {
        const LayerScope scope(layer);
        if (!ScriptRunning(script)) StartScript(script, std::string());
    }
}

std::vector<RunningGlobal> DialogueState::RunningScripts() const {
    std::vector<RunningGlobal> out;
    for (const auto& entry : mRunning) {
        RunningGlobal row;
        row.key = entry.first;
        row.script = SplitStateKey(entry.first).second;
        row.target = entry.second.target;
        row.layer = entry.second.runAs.empty() ? kEveryLayer
                                               : LayerIndex(entry.second.runAs);
        out.push_back(std::move(row));
    }
    return out;
}

// DialogueManager::updateOriginalDisposition: a script moved the base since
// the conversation last looked, so the baseline follows it.
void RefreshBaseline(DialogueState& state) {
    const int base = state.Disposition(state.speaker);
    if (base != state.currentDisposition) {
        state.currentDisposition = base;
        state.originalDisposition = base;
    }
}

// The permanent change is deliberately NOT reset: OpenMW's startDialogue
// keeps it too, so a conversation cut short still settles on the next end.
void DialogueState::BeginConversation(const std::string& who) {
    choices.clear();
    addedTopics.clear();
    messages.clear();
    goodbye = false;
    choice = -1;
    speaker = who;
    RefreshBaseline(*this);
}

void DialogueState::ApplyPersuasion(int temp, int perm) {
    RefreshBaseline(*this);
    if (temp > 0 && perm > 0 &&
        originalDisposition + perm + permanentDispositionChange < 0) {
        perm = -(originalDisposition + permanentDispositionChange);
    }
    currentDisposition += temp;
    SetDisposition(speaker, currentDisposition);
    permanentDispositionChange += perm;
}

void DialogueState::EndConversation() {
    if (!speaker.empty() && (permanentDispositionChange ||
                             originalDisposition != currentDisposition)) {
        RefreshBaseline(*this);
        SetDisposition(speaker, std::clamp(originalDisposition +
                                               permanentDispositionChange,
                                           0, 100));
    }
    permanentDispositionChange = 0;
    originalDisposition = 0;
    currentDisposition = 0;
    speaker.clear();
}

void DialogueState::Reset() {
    *this = DialogueState();
}

std::string DialogueState::Serialize() const {
    std::ostringstream out;
    out << kFormat << '\n';
    for (const auto& e : mJournal) out << "J\t" << e.first << '\t' << e.second << '\n';
    for (const auto& e : mEntries) out << "E\t" << e.first << '\t' << e.second << '\n';
    for (const auto& e : mDisposition) out << "D\t" << e.first << '\t' << e.second << '\n';
    for (const auto& e : mActorRank) out << "N\t" << e.first << '\t' << e.second << '\n';
    for (const auto& e : mGlobals) out << "G\t" << e.first << '\t' << e.second << '\n';
    for (const auto& e : mVars) {
        out << "L\t" << e.first.first << '\t' << e.first.second << '\t'
            << e.second << '\n';
    }
    for (const auto& e : mFactions) {
        out << "F\t" << e.first << '\t' << e.second.rank << '\t'
            << (e.second.expelled ? 1 : 0) << '\t' << e.second.reputation
            << '\n';
    }
    for (const auto& e : mReactions) {
        out << "X\t" << e.first.first << '\t' << e.first.second << '\t'
            << e.second << '\n';
    }
    for (const auto& e : mRunning) {
        out << "S\t" << e.first << '\t' << e.second.target << '\t'
            << e.second.runAs << '\n';
    }
    for (const std::string& topic : mKnownTopics) out << "K\t" << topic << '\n';
    for (const auto& e : mAiSettings) {
        out << "A\t" << e.first.first << '\t' << e.first.second << '\t'
            << e.second << '\n';
    }
    for (const auto& e : mMovementFlags) {
        if (!e.second) continue;
        out << "M\t" << e.first.first << '\t' << e.first.second << '\n';
    }
    for (int i = 0; i < kControlSwitchCount; ++i) {
        if (!mControls[i]) out << "W\t" << i << '\n';
    }
    out << "R\t" << reputation << '\n' << "C\t" << crimeLevel << '\n';
    return out.str();
}

// A version 1 script row, `S script [target]`: keyed and run as the merged
// view it was started under.
DialogueState::Running MigrateRunning(const std::vector<std::string>& f) {
    return {f.size() > 2 ? f[2] : std::string(),
            LayerName(ScriptSourceLayer(f[1]))};
}

std::size_t DialogueState::Deserialize(const std::string& text) {
    Reset();
    std::istringstream in(text);
    std::string line;
    if (!std::getline(in, line)) return 0;
    const bool v1 = line == kFormatV1;
    if (!v1 && line != kFormat) return 0;
    // A version 1 key is a bare id: the merged view resolves it to the origin
    // it had then. A version 2 key is already one.
    const LayerScope merged(kEveryLayer);
    const auto key = [v1](const std::string& id) { return v1 ? Key(id) : id; };
    const auto global = [v1](const std::string& id) {
        return v1 ? GlobalKey(id) : id;
    };
    std::size_t taken = 0;
    while (std::getline(in, line)) {
        const std::vector<std::string> f = Fields(line);
        const std::string& kind = f[0];
        const std::size_t n = f.size();
        if (kind == "J" && n == 3) mJournal[key(f[1])] = Int(f[2]);
        else if (kind == "E" && n == 3) mEntries.push_back({key(f[1]), Int(f[2])});
        else if (kind == "D" && n == 3) mDisposition[key(f[1])] = Int(f[2]);
        else if (kind == "N" && n == 3) mActorRank[key(f[1])] = Int(f[2]);
        else if (kind == "G" && n == 3) mGlobals[global(f[1])] = Float(f[2]);
        else if (kind == "L" && n == 4) mVars[{key(f[1]), f[2]}] = Float(f[3]);
        else if (kind == "F" && n == 5) mFactions[key(f[1])] = {Int(f[2]), f[3] == "1", Int(f[4])};
        else if (kind == "X" && n == 4) mReactions[{key(f[1]), key(f[2])}] = Int(f[3]);
        else if (kind == "S" && v1 && n >= 2) mRunning[key(f[1])] = MigrateRunning(f);
        else if (kind == "S" && n == 4) mRunning[f[1]] = {f[2], f[3]};
        else if (kind == "K" && n == 2) mKnownTopics.insert(key(f[1]));
        else if (kind == "A" && n == 4) mAiSettings[{key(f[1]), Int(f[2])}] = Int(f[3]);
        else if (kind == "M" && n == 3) mMovementFlags[{key(f[1]), Int(f[2])}] = true;
        else if (kind == "W" && n == 2 && Int(f[1]) >= 0 &&
                 Int(f[1]) < kControlSwitchCount) {
            mControls[Int(f[1])] = false;
        }
        else if (kind == "R" && n == 2) reputation = Int(f[1]);
        else if (kind == "C" && n == 2) crimeLevel = Float(f[1]);
        else continue;
        ++taken;
    }
    // A save made before the factions were pushed holds memberships the
    // game never heard of. Each is pushed through its own plugin's view, which
    // is what names its converted FACT.
    for (const auto& e : mFactions) {
        const auto [layer, faction] = SplitStateKey(e.first);
        const LayerScope scope(layer);
        PushFaction(faction, e.second);
    }
    return taken;
}

DialogueState& State() {
    static DialogueState state;
    return state;
}

float PlayerCrimeLevelNow() {
    float gold = 0.0f;
    if (Hooks().crimeGold && Hooks().crimeGold(&gold)) return gold;
    return State().crimeLevel;
}

}  // namespace tesruntime::mw
