// SKSE mod events (Papyrus Form.SendModEvent) delivered to a native listener:
// how converted Papyrus hands a runtime DLL a request without a native call.
#pragma once

#include "skse_abi.h"

namespace tesruntime {

// SKSEModCallbackEvent: BSFixedString is one pointer to the interned text.
struct ModEvent {
    const char* eventName;
    const char* strArg;
    float numArg;
    void* sender;
};

// BSTEventSink<SKSEModCallbackEvent>: slot 0 the destructor, slot 1
// ReceiveEvent(event, dispatcher) returning 0 to let the event continue.
// Runs on the Papyrus thread that sent the event.
class ModEventSink {
public:
    virtual ~ModEventSink() = default;
    virtual int ReceiveEvent(const ModEvent* event, void* dispatcher) = 0;
};

// Registers `sink` for every mod event. False, logged under `who`, when the
// dispatcher or AddEventSink does not resolve.
bool AddModEventSink(SKSEMessagingInterface* messaging, ModEventSink* sink,
                     const char* who);

}  // namespace tesruntime
