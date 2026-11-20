#include "no_rumors.h"

#include <cstdint>
#include <cstring>

#include "addresses.h"
#include "engine.h"
#include "ids.h"
#include "log.h"
#include "mod_event.h"

namespace tesruntime {

namespace {

using SetHasNoRumorsFn = void (*)(void* extraList, bool noRumors);
using RemoveHasNoRumorsFn = void (*)(void* extraList);
using AddChangeFn = void (*)(void* form, std::uint32_t flags);

//: The mod event a converted `ref.SetNoRumors X` sends from the actor, X as its number.
constexpr const char* kEvent = "TES4SetNoRumors";

SetHasNoRumorsFn g_set = nullptr;
RemoveHasNoRumorsFn g_remove = nullptr;

// Sets one actor's override on the main thread, by FormID, as the engine's SetNoRumors command does.
class NoRumorsTask : public TaskDelegate {
public:
    NoRumorsTask(std::uint32_t formId, bool noRumors) : formId_(formId), noRumors_(noRumors) {}

    void Run() override {
        void* actor = g_api.lookupForm(formId_);
        if (!actor || At<std::uint8_t>(actor, kFormType) != kFormTypeActor) return;
        void* base = At<void*>(actor, ids::kOffRefBaseForm);
        if (!base) return;
        const bool flagged = (At<std::uint32_t>(base, ids::kOffActorBaseFlags) & ids::kActorBaseNoRumors) != 0;
        void* extraList = &At<std::uint8_t>(actor, ids::kOffRefExtraList);
        if (noRumors_ == flagged) {
            g_remove(extraList);
        } else {
            g_set(extraList, noRumors_);
        }
        VCall<AddChangeFn>(actor, ids::kVtAddChange)(actor, ids::kChangeNoRumors);
        Log("rumors: %08X SetNoRumors %d", formId_, noRumors_ ? 1 : 0);
    }

    void Dispose() override { delete this; }

private:
    std::uint32_t formId_;
    bool noRumors_;
};

// Runs on the Papyrus thread that sent the event: hands the change to the main thread.
class NoRumorsSink : public ModEventSink {
public:
    int ReceiveEvent(const ModEvent* event, void*) override {
        if (!event || !event->eventName || !event->sender || _stricmp(event->eventName, kEvent) != 0) return 0;
        RunOnMainThread(new NoRumorsTask(At<std::uint32_t>(event->sender, kFormID), event->numArg != 0.0f));
        return 0;
    }
};

NoRumorsSink g_sink;

}  // namespace

bool InstallNoRumors(SKSEMessagingInterface* messaging) {
    g_set = reinterpret_cast<SetHasNoRumorsFn>(Resolve("ExtraDataList SetHasNoRumors", ids::kSetHasNoRumors, nullptr));
    g_remove = reinterpret_cast<RemoveHasNoRumorsFn>(
        Resolve("ExtraDataList RemoveHasNoRumors", ids::kRemoveHasNoRumors, nullptr));
    const bool ok = g_set && g_remove && g_api.task && g_api.lookupForm &&
                    AddModEventSink(messaging, &g_sink, "no rumors");
    Log("rumors: SetNoRumors %s", ok ? "installed" : "NOT installed -- an engine address, the task interface or "
                                                       "the mod events failed");
    return ok;
}

}  // namespace tesruntime
