// The Morrowind state Skyrim has no place for: journal indices, disposition,
// script variables, the player's factions, reputation, crime -- and what the
// conversation in progress has been told to do next.
//
// Result scripts WRITE it and the dialogue filter READS it, which is what
// makes picking the right topic change what the next one says. It is the
// co-save's whole payload: Serialize() is what gets written into the save.
// See: docs/commentary/morrowind_runtime.md#dialogue-state

#pragma once

#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace mwruntime {

// One line a `Choice` command offers, and the index it answers with.
struct ChoiceLine {
    std::string text;
    int index = 0;
};

// The player's standing in one faction.
struct Membership {
    int  rank = -1;          // -1: not a member
    bool expelled = false;
    int  reputation = 0;
};

// What the game itself has to do when the state changes. Null in the
// headless tests, where only the state is checked.
struct GameHooks {
    void (*setQuestStage)(const std::string& quest, int stage) = nullptr;
    void (*addItem)(const std::string& owner, const std::string& item,
                    int count) = nullptr;
    void (*removeItem)(const std::string& owner, const std::string& item,
                       int count) = nullptr;
    int  (*itemCount)(const std::string& owner,
                      const std::string& item) = nullptr;
    // `attacker` attacks `target`; an empty `target` stops the fight instead.
    void (*setCombat)(const std::string& attacker,
                      const std::string& target) = nullptr;
    void (*setEnabled)(const std::string& ref, bool enabled) = nullptr;
    bool (*isDisabled)(const std::string& ref) = nullptr;
    // The player activates `ref`.
    void (*activate)(const std::string& ref) = nullptr;
    // Locks at `level`; a negative level unlocks.
    void (*setLocked)(const std::string& ref, int level) = nullptr;
    bool (*isLocked)(const std::string& ref) = nullptr;
    void (*deleteRef)(const std::string& ref) = nullptr;
    float (*distance)(const std::string& from, const std::string& to) = nullptr;
    // `which`: 0 health, 1 magicka, 2 fatigue -- OpenMW's dynamic order.
    float (*dynamicStat)(const std::string& actor, int which) = nullptr;
    void (*setDynamicStat)(const std::string& actor, int which,
                           float value) = nullptr;
    void (*modDynamicStat)(const std::string& actor, int which,
                           float delta) = nullptr;
    void (*equipItem)(const std::string& actor, const std::string& item) = nullptr;
    // The player's current cell as the game NAMES it, or "".
    std::string (*playerCell)() = nullptr;
    bool (*playerInInterior)() = nullptr;
    bool (*menuMode)() = nullptr;
    void (*forceGreeting)(const std::string& actor) = nullptr;
    // `axis`: 0 x, 1 y, 2 z. Position is world units, angle is DEGREES --
    // the engine stores radians and its getters convert, so these do not.
    float (*position)(const std::string& ref, int axis) = nullptr;
    void (*setPosition)(const std::string& ref, int axis, float value) = nullptr;
    float (*angle)(const std::string& ref, int axis) = nullptr;
    void (*setAngle)(const std::string& ref, int axis, float value) = nullptr;
    // What persuasion and barter read and do in the game: the player's
    // level, a Skyrim actor value by name, that value as a fraction of its
    // maximum, a skill-use credit, and Skyrim's own barter menu on `actor`.
    int   (*playerLevel)() = nullptr;
    float (*actorValue)(const std::string& actor, const char* name) = nullptr;
    float (*statPercent)(const std::string& actor, const char* name) = nullptr;
    void  (*advanceSkill)(const char* skill, float amount) = nullptr;
    void  (*showBarterMenu)(const std::string& actor) = nullptr;
    // Skyrim's gold (form 0xF), never Morrowind's: what `actor` carries, and
    // a bribe moving `count` of it from one actor to another.
    int   (*goldCount)(const std::string& actor) = nullptr;
    void  (*moveGold)(const std::string& from, const std::string& to,
                      int count) = nullptr;
};

GameHooks& Hooks();

class DialogueState {
public:
    // --- journal ---------------------------------------------------------
    int  JournalIndex(const std::string& quest) const;
    void SetJournalIndex(const std::string& quest, int index);
    // `Journal quest index`: records the entry once, raises the quest's
    // index only when `index` is higher. True when the entry is new.
    bool AddJournalEntry(const std::string& quest, int index);

    // --- disposition, 0..100: the NPC's authored value until moved ---------
    int  Disposition(const std::string& actor) const;
    void SetDisposition(const std::string& actor, int value);
    void ModDisposition(const std::string& actor, int delta);

    // --- script globals: a GLOB record's value until a script sets it ------
    bool  HasGlobal(const std::string& name) const;
    float Global(const std::string& name) const;
    void  SetGlobal(const std::string& name, float value);
    std::vector<std::string> Globals() const;

    // --- script locals, by owner: an actor's id or a global script's -------
    float Var(const std::string& owner, const std::string& name) const;
    void  SetVar(const std::string& owner, const std::string& name,
                 float value);

    // --- the topics the player has heard of -----------------------------------
    // DialogueManager::mKnownTopics: a topic is LISTED only when the speaker
    // can answer it AND it is here. It starts empty and grows from keywords
    // in replies and from AddTopic.
    bool KnowsTopic(const std::string& topic) const;
    void LearnTopic(const std::string& topic);
    std::size_t KnownTopicCount() const { return mKnownTopics.size(); }

    // --- the player's factions ---------------------------------------------
    const Membership& Faction(const std::string& faction) const;
    // PCJoinFaction: rank 0 unless already a member.
    void JoinFaction(const std::string& faction);
    // PCRaiseRank joins at rank 0 first; PCLowerRank leaves below rank 0.
    void ChangeRank(const std::string& faction, int delta);
    void SetExpelled(const std::string& faction, bool expelled);
    void SetFactionReputation(const std::string& faction, int value);

    // --- the four AI settings, 0..100, per actor ---------------------------
    // Fight, Hello, Alarm and Flee. Morrowind stores them on the actor and
    // dialogue both sets and tests them; Skyrim has no equivalent field, so
    // the DLL owns them the way it owns disposition.
    int  AiSetting(const std::string& actor, int which) const;
    void SetAiSetting(const std::string& actor, int which, int value);

    // --- how many of a base actor the player has killed --------------------
    // `GetDeadCount id`, which gates a great deal of quest dialogue.
    int  DeadCount(const std::string& actor) const;
    void AddDeath(const std::string& actor);

    // --- reputation, crime, faction reactions, running scripts -------------
    int   reputation = 0;
    float crimeLevel = 0.0f;
    int  FactionReaction(const std::string& a, const std::string& b) const;
    void SetFactionReaction(const std::string& a, const std::string& b,
                            int value);
    bool ScriptRunning(const std::string& script) const;
    void SetScriptRunning(const std::string& script, bool running);

    // --- the conversation in progress --------------------------------------
    // Reset by BeginConversation; filled by result scripts as they run.
    std::vector<ChoiceLine>  choices;
    std::vector<std::string> addedTopics;
    std::vector<std::string> messages;
    bool goodbye = false;
    int  choice = -1;

    // --- the conversation's disposition, DialogueManager's three numbers ---
    // The base the conversation opened on, the base as persuasion has moved
    // it (what the filter and the bar read), and the part that outlives it.
    // See: docs/commentary/morrowind_runtime.md#persuasion
    std::string speaker;
    int originalDisposition = 0;
    int currentDisposition = 0;
    int permanentDispositionChange = 0;

    // Opens a conversation with `speaker`; a persuasion's bookkeeping is
    // kept until EndConversation folds it into the base.
    void BeginConversation(const std::string& speaker = std::string());
    // DialogueManager::persuade's disposition lines: `temp` moves the base
    // the filter reads now, `perm` accumulates for EndConversation.
    void ApplyPersuasion(int temp, int perm);
    // DialogueManager::goodbyeSelected: the base becomes the original plus
    // the permanent change, clamped to 0..100, and the three reset.
    void EndConversation();

    // --- the co-save ---------------------------------------------------------
    // One tab-separated record per line under a version header. A line this
    // build does not know is skipped, so an older build reads a newer save.
    std::string Serialize() const;
    // Replaces everything persistent. Returns how many records were taken.
    std::size_t Deserialize(const std::string& text);
    void Reset();

private:
    std::map<std::string, int> mJournal;
    std::vector<std::pair<std::string, int>> mEntries;
    std::map<std::string, int> mDisposition;
    std::map<std::string, float> mGlobals;
    std::map<std::pair<std::string, std::string>, float> mVars;
    std::map<std::string, Membership> mFactions;
    std::map<std::pair<std::string, std::string>, int> mReactions;
    std::set<std::string> mRunning;
    std::set<std::string> mKnownTopics;
    std::map<std::pair<std::string, int>, int> mAiSettings;
    std::map<std::string, int> mDeaths;
};

// The one state of this game session.
DialogueState& State();

}  // namespace mwruntime
