#include "mod_event.h"

#include "addresses.h"
#include "engine_ids.h"
#include "log.h"

namespace tesruntime {

namespace {

using AddEventSinkFn = void (*)(void* dispatcher, void* sink);

}  // namespace

bool AddModEventSink(SKSEMessagingInterface* messaging, ModEventSink* sink,
                     const char* who) {
    void* dispatcher = messaging ? messaging->GetEventDispatcher(
        SKSEMessagingInterface::kDispatcher_ModEvent) : nullptr;
    auto add = reinterpret_cast<AddEventSinkFn>(
        Resolve("BSTEventSource::AddEventSink", ids::kAddEventSink, nullptr));
    if (!dispatcher || !add) {
        Log("%s: mod-event dispatcher %p, AddEventSink %p -- not installed", who,
            dispatcher, reinterpret_cast<void*>(add));
        return false;
    }
    add(dispatcher, sink);
    return true;
}

}  // namespace tesruntime
