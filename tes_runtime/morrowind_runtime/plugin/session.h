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

namespace mwruntime {

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

// Topic ids appearing as whole words in `text`, longest first, restricted to
// topics the actor can actually answer. Morrowind's keyword discovery.
std::vector<std::string> MentionedTopics(const std::string& text,
                                         const ActorView& actor);

}  // namespace mwruntime
