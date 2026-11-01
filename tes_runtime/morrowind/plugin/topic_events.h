// Topics a converted TES4 plugin adds (AddTopic, an INFO's add-topic list),
// learned by the TES3 dialogue of the same name.
// See: docs/commentary/morrowind_runtime.md#tes4-addtopic-teaches-tes3-topics
#pragma once

#include <string>

#include "skse_abi.h"

namespace tesruntime::mw {

// Listens for the converter's "TES3LearnTopic" mod event. False when the
// mod-event dispatcher does not resolve.
bool InstallTopicEvents(SKSEMessagingInterface* messaging);

// Learns `name` in every sidecar layer that stages a topic of that name;
// false when none does. Main thread.
bool LearnTopicByName(const std::string& name);

}  // namespace tesruntime::mw
