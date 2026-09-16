#include "session.h"

#include <algorithm>
#include <cctype>

#include "filter.h"

namespace mwruntime {

namespace {

std::string Lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(),
                   [](unsigned char c) { return static_cast<char>(::tolower(c)); });
    return text;
}

bool IsWordChar(char c) {
    const unsigned char u = static_cast<unsigned char>(c);
    return std::isalnum(u) != 0 || c == '\'';
}

// A topic matches only on a whole-word boundary, or "ring" would light up
// inside "bring".
bool WholeWordAt(const std::string& haystack, const std::string& needle,
                 std::size_t pos) {
    if (pos > 0 && IsWordChar(haystack[pos - 1])) return false;
    const std::size_t after = pos + needle.size();
    return after >= haystack.size() || !IsWordChar(haystack[after]);
}

Reply MakeReply(const std::string& topic, const Info* info,
                const ActorView& actor) {
    Reply reply;
    reply.topic = topic;
    if (!info) return reply;
    reply.text = info->response;
    reply.voice = info->voice;
    reply.resultScript = info->resultScript;
    reply.mentioned = MentionedTopics(info->response, actor);
    return reply;
}

}  // namespace

std::vector<TopicEntry> OfferedTopics(const ActorView& actor,
                                      const std::vector<std::string>& known) {
    std::vector<TopicEntry> out;
    for (const auto& entry : Topics()) {
        const Topic& topic = entry.second;
        // Only plain topics reach the list: greetings fire on their own,
        // journal entries are the quest log, and voice/persuasion are the
        // engine's own channels.
        if (topic.type != DialType::Topic) continue;
        if (SelectInfo(topic, actor, -1).info == nullptr) continue;
        TopicEntry row;
        row.id = topic.id;
        row.isNew = std::find(known.begin(), known.end(), topic.id) ==
                    known.end();
        out.push_back(std::move(row));
    }
    std::sort(out.begin(), out.end(),
              [](const TopicEntry& a, const TopicEntry& b) {
                  return Lower(a.id) < Lower(b.id);
              });
    return out;
}

Reply Greet(const ActorView& actor) {
    // Greetings live in several topics (Greeting 0..9) ordered by urgency;
    // the first that answers wins, which is what makes a quest greeting
    // interrupt the ordinary one.
    std::vector<const Topic*> greetings;
    for (const auto& entry : Topics()) {
        if (entry.second.type == DialType::Greeting) {
            greetings.push_back(&entry.second);
        }
    }
    std::sort(greetings.begin(), greetings.end(),
              [](const Topic* a, const Topic* b) {
                  return Lower(a->id) < Lower(b->id);
              });
    for (const Topic* topic : greetings) {
        const FilterResult result = SelectInfo(*topic, actor, -1);
        if (result.info) return MakeReply(topic->id, result.info, actor);
    }
    return Reply();
}

Reply Answer(const std::string& topic, const ActorView& actor, int choice) {
    const Topic* found = FindTopic(topic);
    if (!found) return Reply();
    return MakeReply(found->id, SelectInfo(*found, actor, choice).info, actor);
}

std::vector<std::string> MentionedTopics(const std::string& text,
                                         const ActorView& actor) {
    const std::string haystack = Lower(text);
    std::vector<std::string> out;
    for (const auto& entry : Topics()) {
        const Topic& topic = entry.second;
        if (topic.type != DialType::Topic) continue;
        const std::string needle = Lower(topic.id);
        if (needle.empty() || needle.size() > haystack.size()) continue;
        const std::size_t pos = haystack.find(needle);
        if (pos == std::string::npos) continue;
        if (!WholeWordAt(haystack, needle, pos)) continue;
        // Only offer what the actor can actually answer, or the list fills
        // with topics that reply with nothing.
        if (SelectInfo(topic, actor, -1).info == nullptr) continue;
        out.push_back(topic.id);
    }
    // Longest first: a reply naming both "Caius Cosades" and "Caius" should
    // surface the specific topic ahead of the general one.
    std::sort(out.begin(), out.end(),
              [](const std::string& a, const std::string& b) {
                  if (a.size() != b.size()) return a.size() > b.size();
                  return Lower(a) < Lower(b);
              });
    return out;
}

}  // namespace mwruntime
