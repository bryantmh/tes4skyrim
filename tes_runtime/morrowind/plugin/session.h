// A conversation: which topics an actor offers, and what they answer.
//
// This is the model the menu renders. It holds no Skyrim types, so the whole
// of it is testable headless.
// See: docs/commentary/morrowind_runtime.md#the-session

#pragma once

#include <string>
#include <vector>

#include "actor.h"
#include "store.h"

namespace tesruntime::mw {

// One row in the topic list.
struct TopicEntry {
    std::string id;     // the TES3 id, which is also the displayed text
    bool        isNew;  // never selected in this conversation
};

// What the player sees after picking a topic, or on greeting.
struct Reply {
    std::string topic;
    std::string text;
    std::string voice;
    std::string resultScript;
    // Topics named IN this reply's text, so the list can grow the way
    // Morrowind's keyword discovery does.
    std::vector<std::string> mentioned;
};

// The topics this actor can answer right now, in Morrowind's own order:
// alphabetical, which is how the vanilla list reads.
std::vector<TopicEntry> OfferedTopics(const ActorView& actor,
                                      const std::vector<std::string>& known);

// The greeting an actor opens with, or an empty reply when none matches.
Reply Greet(const ActorView& actor);

// The answer to one topic, or an empty reply when nothing passes.
Reply Answer(const std::string& topic, const ActorView& actor, int choice);

// MWBase::DialogueManager::ServiceType, the choice a Service Refusal INFO
// is filtered under.
constexpr int kServiceBarter = 1;
constexpr int kServiceSpells = 3;
constexpr int kServiceTraining = 4;
constexpr int kServiceTravel = 5;
constexpr int kServiceEnchanting = 7;

// DialogueManager::checkServiceRefused: the Service Refusal line for
// `service`, filtered with the disposition test INVERTED, or an empty reply
// when the actor does not refuse.
Reply ServiceRefusal(int service, const ActorView& actor);

// Topic ids appearing as whole words in `text`, longest first, restricted to
// topics the actor can actually answer. Morrowind's keyword discovery.
std::vector<std::string> MentionedTopics(const std::string& text,
                                         const ActorView& actor);

// The topics vanilla hands the player in the census office, recovered from
// the data's OWN chargen INFO rather than named here. A converted world is
// entered somewhere else, so without this the universal topics -- "little
// advice", "services", "my trade" -- are never introduced and the list of
// every ordinary NPC comes up empty.
// See: docs/commentary/morrowind_runtime.md#chargen-topics
std::vector<std::string> ChargenTopics();

// The actor those topics come from, so a caller can ask whether this world
// actually contains them before standing in for that conversation.
const char* ChargenActor();

}  // namespace tesruntime::mw
