#include "topic_events.h"

#include <string>

#include <components/misc/strings/lower.hpp>

#include "dialogue_state.h"
#include "log.h"
#include "main_thread.h"
#include "mod_event.h"
#include "scope.h"
#include "store.h"

namespace tesruntime::mw {

namespace {

// What the converter's TES4Polyfill.LearnTopic sends; strArg is the topic's
// display name, which is a TES3 topic's id.
constexpr const char* kLearnEvent = "TES3LearnTopic";

class TopicSink : public ModEventSink {
public:
    int ReceiveEvent(const ModEvent* event, void*) override {
        if (!event || !event->eventName || !event->strArg ||
            _stricmp(event->eventName, kLearnEvent) != 0) {
            return 0;
        }
        std::string name = event->strArg;
        PostToMainThread([name]() { LearnTopicByName(name); });
        return 0;
    }
};

TopicSink g_sink;

}  // namespace

bool LearnTopicByName(const std::string& name) {
    const auto it = Topics().find(Misc::StringUtils::lowerCase(name));
    if (it == Topics().end() || it->second.type != DialType::Topic) return false;
    for (const int layer : it->second.layers) {
        const LayerScope scope(layer);
        State().LearnTopic(it->second.id);
    }
    return true;
}

bool InstallTopicEvents(SKSEMessagingInterface* messaging) {
    return AddModEventSink(messaging, &g_sink, "topic");
}

}  // namespace tesruntime::mw
