#include "spin.h"

#include <windows.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iterator>
#include <mutex>
#include <unordered_map>
#include <vector>

#include "addresses.h"
#include "engine.h"
#include "engine_ids.h"
#include "glide.h"
#include "log.h"

namespace tesruntime {

namespace {

// The events the converter sends; the dispatcher also carries every other mod's.
// "TES4Spin" carries a rate per second, "TES4Track" an absolute target.
constexpr const char* kSpinEvent = "TES4Spin";
constexpr const char* kTrackEvent = "TES4Track";

// The tick: MorrowindRuntime's rate, each glide aimed exactly one tick ahead.
constexpr int kTickMs = 33;
constexpr float kTickDelta = 1.0f / 30.0f;

// Position and angle axes, 0-2 and 3-5.
constexpr int kAxes = 2 * kPoseAxes;

// Events closer together than this are one Papyrus pass.
constexpr double kSamePass = 0.05;
// An axis no pass has renewed for this many gaps between passes, and never
// less than kMinHold seconds, stops: its script stopped moving it.
constexpr double kHoldGaps = 2.5;
constexpr double kMinHold = 0.5;
constexpr double kMinGap = 0.05;
constexpr double kMaxGap = 2.0;
// A target is reached over this many gaps between passes, so the next pass
// re-aims the object before it stops: it moves on, at the target's speed.
constexpr double kTrackGaps = 1.5;
// A still position axis further than this from its goal was moved by
// something else, and the goal follows it.
constexpr float kMovedAway = 1.0f;

// SKSEModCallbackEvent: BSFixedString is one pointer to the interned text.
struct ModEvent {
    const char* eventName;
    const char* strArg;
    float numArg;
    void* sender;
};

// BSTEventSink<SKSEModCallbackEvent>: slot 0 the destructor, slot 1
// ReceiveEvent(event, dispatcher) returning 0 to let the event continue.
class SpinSink {
public:
    virtual ~SpinSink() = default;
    virtual int ReceiveEvent(const ModEvent* event, void* dispatcher);
};

using AddEventSinkFn = void (*)(void* dispatcher, void* sink);

enum class Mode : std::uint8_t { kIdle, kRate, kTrack };

struct Request {
    std::uint32_t formId;
    int axis;
    float value;
    bool track;
    double at;
};

// One axis: turning at `rate`, or gliding from `from` to `to` over `seconds`
// from `start`; `renewed` is when a pass last asked.
struct Axis {
    Mode mode = Mode::kIdle;
    float rate = 0.0f;
    float from = 0.0f;
    float to = 0.0f;
    double start = 0.0;
    double seconds = 0.0;
    double renewed = 0.0;
};

// One reference being turned or moved: where it is headed, its axes, and the
// gap between passes.
struct Spin {
    Pose goal{};
    Axis axes[kAxes];
    double lastPass = 0.0;
    double gap = 0.1;
};

SpinSink g_sink;
std::mutex g_lock;
std::vector<Request> g_pending;
std::unordered_map<std::uint32_t, Spin> g_spins;  // game thread only

double Now() {
    static const double perSecond = [] {
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        return static_cast<double>(f.QuadPart);
    }();
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return static_cast<double>(t.QuadPart) / perSecond;
}

// The live object reference behind `formId`, or null when it is gone or is
// not one. Actors are left to Papyrus: TranslateTo fights their own movement.
void* LiveRef(std::uint32_t formId) {
    void* form = g_api.lookupForm ? g_api.lookupForm(formId) : nullptr;
    if (!form) return nullptr;
    return At<std::uint8_t>(form, kFormType) == kFormTypeReference ? form : nullptr;
}

float& GoalAxis(Spin& spin, int axis) {
    return axis < kPoseAxes ? spin.goal.at[axis] : spin.goal.angle[axis - kPoseAxes];
}

float LiveAxis(void* ref, int axis) {
    return axis < kPoseAxes ? RefPositionAxis(g_api.vm, ref, axis)
                            : RefAngleDegrees(ref, axis - kPoseAxes);
}

// `to - from` the way the axis measures it: angles the short way round.
float Span(int axis, float from, float to) {
    return axis < kPoseAxes ? to - from : std::remainder(to - from, 360.0f);
}

// A target for a reference with no 3D: placed at once, as SetPosition did.
void PlaceNow(void* ref, int axis, float value) {
    Pose pose = RefPose(g_api.vm, ref);
    (axis < kPoseAxes ? pose.at : pose.angle)[axis % kPoseAxes] = value;
    if (axis < kPoseAxes) {
        SetRefPosition(g_api.vm, ref, pose.at[0], pose.at[1], pose.at[2]);
    } else {
        SetRefAngle(g_api.vm, ref, pose.angle[0], pose.angle[1], pose.angle[2]);
    }
}

// Renews one axis from a pass, starting the reference from where it stands.
void Apply(const Request& req) {
    void* ref = LiveRef(req.formId);
    if (!ref) return;
    float here[kPoseAxes];
    if (!RefPosition(ref, here)) {
        if (req.track) PlaceNow(ref, req.axis, req.value);
        return;
    }
    auto [it, fresh] = g_spins.try_emplace(req.formId);
    Spin& spin = it->second;
    if (fresh) {
        spin.goal = RefPose(g_api.vm, ref);
        spin.lastPass = req.at;
        Log("spin: %08X starts, axis %d %s %g", req.formId, req.axis,
            req.track ? "to" : "at", req.value);
    } else if (req.at - spin.lastPass >= kSamePass) {
        spin.gap = std::clamp(req.at - spin.lastPass, kMinGap, kMaxGap);
        spin.lastPass = req.at;
    }
    Axis& axis = spin.axes[req.axis];
    axis.renewed = req.at;
    if (!req.track) {
        axis.mode = Mode::kRate;
        axis.rate = req.value;
        return;
    }
    axis.mode = Mode::kTrack;
    axis.from = GoalAxis(spin, req.axis);
    axis.to = req.value;
    axis.start = req.at;
    axis.seconds = kTrackGaps * spin.gap;
}

// A turning axis chains from its own goal, but never runs more than two steps
// ahead of the 3D -- a paused game or a stalled glide would otherwise bank a
// jump.
void StepRate(float& goal, const Axis& axis, float live, int index) {
    const float step = axis.rate * kTickDelta;
    if (std::fabs(Span(index, live, goal)) > 2.0f * std::fabs(step)) goal = live;
    goal += step;
    if (index >= kPoseAxes) goal = std::remainder(goal, 360.0f);
}

// Advances one axis a tick. A still angle follows the 3D; a still position
// keeps its goal, since the rotation's Z nudge moves the 3D.
void StepAxis(Spin& spin, void* ref, int index, double now) {
    float& goal = GoalAxis(spin, index);
    const Axis& axis = spin.axes[index];
    const float live = LiveAxis(ref, index);
    if (axis.mode == Mode::kRate) {
        StepRate(goal, axis, live, index);
    } else if (axis.mode == Mode::kTrack) {
        const double done = std::clamp((now - axis.start) / axis.seconds, 0.0, 1.0);
        goal = axis.from + Span(index, axis.from, axis.to) * static_cast<float>(done);
    } else if (index >= kPoseAxes || std::fabs(goal - live) > kMovedAway) {
        goal = live;
    }
}

// Stops the axes no pass renewed in time (a glide first reaches its target);
// true while any axis still moves.
bool ExpireAxes(Spin& spin, double now) {
    const double hold = (std::max)(kMinHold, kHoldGaps * spin.gap);
    bool moving = false;
    for (Axis& axis : spin.axes) {
        const bool arrived = axis.mode != Mode::kTrack ||
                             now - axis.start >= axis.seconds;
        if (axis.mode != Mode::kIdle && arrived && now - axis.renewed > hold) {
            axis.mode = Mode::kIdle;
        }
        moving = moving || axis.mode != Mode::kIdle;
    }
    return moving;
}

void Tick() {
    std::vector<Request> batch;
    {
        std::lock_guard<std::mutex> hold(g_lock);
        batch.swap(g_pending);
    }
    for (const Request& req : batch) Apply(req);
    const double now = Now();
    for (auto it = g_spins.begin(); it != g_spins.end();) {
        void* ref = LiveRef(it->first);
        float here[kPoseAxes];
        if (!ref || !RefPosition(ref, here) || !ExpireAxes(it->second, now)) {
            it = g_spins.erase(it);
            continue;
        }
        for (int axis = 0; axis < kAxes; ++axis) StepAxis(it->second, ref, axis, now);
        GlideTo(g_api.vm, ref, it->second.goal, kTickDelta);
        it = std::next(it);
    }
}

// Runs on the Papyrus thread that sent the event: queue it for the tick.
int SpinSink::ReceiveEvent(const ModEvent* event, void*) {
    if (!event || !event->eventName || !event->sender || !event->strArg) return 0;
    const bool track = _stricmp(event->eventName, kTrackEvent) == 0;
    if (!track && _stricmp(event->eventName, kSpinEvent) != 0) return 0;
    const int axis = std::atoi(event->strArg);
    if (axis < 0 || axis >= kAxes) return 0;
    const Request req{At<std::uint32_t>(event->sender, kFormID), axis,
                      event->numArg, track, Now()};
    std::lock_guard<std::mutex> hold(g_lock);
    g_pending.push_back(req);
    return 0;
}

}  // namespace

bool InstallSpin(SKSEMessagingInterface* messaging) {
    if (!messaging || !g_api.task || !g_api.lookupForm) {
        Log("spin: no messaging, task interface or form lookup; converted "
            "GameMode motion falls back to Papyrus glides");
        return false;
    }
    if (!ResolveGlide()) {
        Log("spin: a position, angle or TranslateTo native is unresolved");
        return false;
    }
    void* dispatcher = messaging->GetEventDispatcher(
        SKSEMessagingInterface::kDispatcher_ModEvent);
    auto add = reinterpret_cast<AddEventSinkFn>(
        Resolve("BSTEventSource::AddEventSink", ids::kAddEventSink, nullptr));
    if (!dispatcher || !add) {
        Log("spin: mod-event dispatcher %p, AddEventSink %p -- not installed",
            dispatcher, reinterpret_cast<void*>(add));
        return false;
    }
    if (!StartMainThreadTick(kTickMs, Tick)) return false;
    add(dispatcher, &g_sink);
    return true;
}

}  // namespace tesruntime
