// The animation queue `PlayGroup` and `LoopGroup` feed, for an OBJECT: OpenMW's
// CharacterController::playGroup and updateAnimQueue for a non-actor, played
// on the NiControllerSequences the mesh conversion cut out of the timeline.
//
// One group plays and at most one waits. A looping group (the mesh marks it
// with a LOOP cycle) plays its intro once, repeats its loop until its count
// runs out or something waits, finishing the cycle it is in, then plays its
// outro; a one-shot plays once and holds its last pose until replaced.
// `Idle` is the object's ambient loop, `AutoPlay` then `AutoLoop` in the
// converted mesh, and a mesh without one simply stops.
//
// `SkipAnim` holds an object's ambient animation still for the tick it is
// called in, and never a scripted group (OpenMW isScriptedAnimPlaying).
//
// 🛑 Nothing is cached across ticks but names and numbers: every tick
// re-resolves the reference by FormID and its sequences by name, because the
// engine frees a reference and its 3D whenever the cell unloads.

#include "game_calls_internal.h"

#include <cmath>
#include <iterator>
#include <limits>
#include <string>
#include <unordered_map>
#include <unordered_set>

#include "engine.h"
#include "gamebryo_sequence.h"
#include "glide.h"
#include "ids.h"
#include "log.h"
#include "main_thread.h"
#include "object_tick.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

// The group every object returns to, and the converted meshes' names for it.
constexpr const char* kIdleGroup = "idle";
constexpr const char* kAmbientLoop = "AutoLoop";
constexpr const char* kAmbientStart = "AutoPlay";
// The mesh's names for a looping group's lead-in and lead-out.
constexpr const char* kIntroSuffix = " intro";
constexpr const char* kOutroSuffix = " outro";
constexpr double kForever = std::numeric_limits<double>::infinity();

enum Part { kIntro, kLoop, kOutro, kParts };

struct Clip {
    std::string group;          // lower case
    std::uint32_t loops = 0;
};

struct Queue {
    Clip current;
    double started = 0.0;
    float intro = 0.0f;
    float length = 0.0f;        // one cycle of the loop, or the one-shot
    float outro = 0.0f;
    bool looping = false;
    double cycles = 1.0;        // how many cycles the loop may run
    bool waiting = false;
    Clip next;
    int part = -1;              // the Part now playing
};

// An object `SkipAnim` holds: when, and which ambient sequences it stopped.
struct Held {
    double since = 0.0;
    bool start = false;
    bool loop = false;
};

// A group asked of an object whose 3D is still loading (just enabled, say):
// OpenMW builds the animation inside Enable, so it plays once the 3D is in.
struct Pending {
    Clip clip;
    int mode = 0;
    int ticks = 0;
};
// Ticks a pending group waits for its 3D before it is dropped (~10 s).
constexpr int kPendingTicks = 600;

std::unordered_map<std::uint32_t, Queue> g_queues;   // game thread only
std::unordered_map<std::uint32_t, Pending> g_pending;
std::unordered_map<std::uint32_t, Held> g_held;
std::unordered_set<std::uint32_t> g_skips;            // asked for this tick
std::unordered_set<std::string> g_logged;             // ref+group plays logged once
std::unordered_map<std::string, void*> g_names;      // interned, never freed

void* Name(const std::string& text) {
    auto it = g_names.find(text);
    if (it != g_names.end()) return it->second;
    void* name = nullptr;
    FixedString(&name, text.c_str());
    g_names.emplace(text, name);
    return name;
}

void* ManagerOfRef(void* ref) {
    void* root = ref ? VCall<void* (*)(void*)>(ref, kVtGet3D)(ref) : nullptr;
    return sequence::ManagerOf(root);
}

void* SequenceFor(void* manager, const std::string& group, int part = kLoop) {
    if (group.empty()) return nullptr;
    const bool idle = group == kIdleGroup;
    switch (part) {
        case kIntro: return sequence::Named(manager, Name(idle ? kAmbientStart : group + kIntroSuffix));
        case kOutro: return sequence::Named(manager, Name(group + kOutroSuffix));
        default:     return sequence::Named(manager, Name(idle ? kAmbientLoop : group));
    }
}

// When the loop (or the one-shot) stops, in GameSeconds; infinite for a loop
// nothing ends.
double LoopEnd(const Queue& q) {
    return q.started + q.intro + q.length * (q.looping ? q.cycles : 1.0);
}

double EndOf(const Queue& q) {
    if (q.length <= 0.0f) return q.started;
    return LoopEnd(q) + q.outro;
}

bool Playing(const Queue& q) { return GameSeconds() < EndOf(q); }

int PartAt(const Queue& q, double now) {
    if (now < q.started + q.intro) return kIntro;
    return now < LoopEnd(q) ? kLoop : kOutro;
}

// Plays the part of the clip that is due, once it changes.
void Show(Queue& q, void* manager) {
    const int part = PartAt(q, GameSeconds());
    if (part == q.part) return;
    if (q.part >= 0) sequence::Stop(SequenceFor(manager, q.current.group, q.part));
    q.part = part;
    sequence::Play(SequenceFor(manager, q.current.group, part));
}

// Stops what plays: every part of the clip and, for a scripted group, the
// ambient pair the behaviour graph started.
void StopCurrent(void* manager, const Queue& q, bool scripted) {
    for (int part = kIntro; part < kParts; ++part) {
        sequence::Stop(SequenceFor(manager, q.current.group, part));
    }
    if (!scripted) return;
    sequence::Stop(sequence::Named(manager, Name(kAmbientLoop)));
    sequence::Stop(sequence::Named(manager, Name(kAmbientStart)));
}

void Start(Queue& q, void* manager, const Clip& clip) {
    StopCurrent(manager, q, clip.group != kIdleGroup);
    void* seq = SequenceFor(manager, clip.group);
    q.current = clip;
    q.waiting = false;
    q.started = GameSeconds();
    q.length = sequence::Length(seq);
    q.looping = seq && sequence::Cycle(seq) == sequence::kCycleLoop;
    q.intro = q.looping ? sequence::Length(SequenceFor(manager, clip.group, kIntro)) : 0.0f;
    q.outro = q.looping ? sequence::Length(SequenceFor(manager, clip.group, kOutro)) : 0.0f;
    q.cycles = clip.loops == kLoopForever ? kForever : clip.loops + 1.0;
    q.part = -1;
    Show(q, manager);
}

// A clip queued behind a looping one lets it finish only the cycle it is in.
void Wait(Queue& q, const Clip& clip) {
    q.next = clip;
    q.waiting = true;
    if (q.looping && q.length > 0.0f) {
        const double done = (GameSeconds() - q.started - q.intro) / q.length;
        q.cycles = std::fmin(q.cycles, std::fmax(1.0, std::ceil(done)));
    }
}

void QueueGroup(std::uint32_t id, void* manager, const Clip& clip, int mode) {
    Queue& q = g_queues[id];
    // A looping group asked for while it still loops keeps its count, which
    // is what lets a banner script call the same group every frame.
    if (q.current.group == clip.group && q.looping && Playing(q)) {
        q.waiting = false;
        return;
    }
    if (mode != 0 || q.current.group.empty() || !Playing(q)) {
        Start(q, manager, clip);
    } else {
        Wait(q, clip);
    }
}

// Why `ref` cannot play `group` -- no manager on its 3D, no sequence of that
// name -- or null when it can. 3D not loaded yet is normal and not reported.
const char* Unplayable(void* ref, void* manager, const std::string& group) {
    if (!ref || !VCall<void* (*)(void*)>(ref, kVtGet3D)(ref)) return nullptr;
    if (!manager) return "anim: the 3D has no NiControllerManager, group";
    if (group != kIdleGroup && !SequenceFor(manager, group)) return "anim: no mesh sequence for group";
    return nullptr;
}

void PlayGroupNow(std::uint32_t id, const std::string& group, int mode,
                  std::uint32_t loops) {
    void* ref = RefByRuntimeId(id);
    if (ref && !Has3D(ref)) {
        g_pending[id] = Pending{Clip{group, loops}, mode};
        return;
    }
    void* manager = ManagerOfRef(ref);
    if (const char* why = Unplayable(ref, manager, group)) ReportOnce(why, group);
    if (!manager || (group != kIdleGroup && !SequenceFor(manager, group))) return;
    QueueGroup(id, manager, Clip{group, loops}, mode);
    if (g_logged.insert(std::to_string(id) + group).second) {
        void* seq = SequenceFor(manager, group);
        Log("anim: %08X asked for '%s' (mode %d): playing '%s', sequence %s, manager flags %04X",
            id, group.c_str(), mode, g_queues[id].current.group.c_str(),
            sequence::Active(seq) ? "active" : "inactive", At<std::uint16_t>(manager, 0x10));
    }
}

void PlayGroup(const std::string& ref, const std::string& group, int mode,
               std::uint32_t loops) {
    void* target = OwnerRef(ref);
    if (!target) return;
    const std::uint32_t id = FormIdOf(target);
    const std::string lower = Lower(group);
    RunOnGameThread([id, lower, mode, loops]() {
        PlayGroupNow(id, lower, mode, loops);
    });
}

void SkipAnim(const std::string& ref) {
    void* target = OwnerRef(ref);
    if (!target) return;
    const std::uint32_t id = FormIdOf(target);
    RunOnGameThread([id]() { g_skips.insert(id); });
}

// One queue's turn: the part that is due plays, the clip that ended hands
// over to the one waiting, a scripted clip with nothing waiting returns to
// Idle, and a one-shot holds.
bool Advance(std::uint32_t id, Queue& q) {
    void* manager = ManagerOfRef(RefByRuntimeId(id));
    if (!manager) return false;
    if (g_held.count(id)) return true;
    if (Playing(q)) {
        Show(q, manager);
    } else if (q.waiting) {
        Start(q, manager, q.next);
    } else if (q.current.group != kIdleGroup && q.looping) {
        Start(q, manager, Clip{kIdleGroup, kLoopForever});
    }
    return true;
}

bool Scripted(std::uint32_t id) {
    auto it = g_queues.find(id);
    return it != g_queues.end() && !it->second.current.group.empty()
        && it->second.current.group != kIdleGroup;
}

void Hold(std::uint32_t id) {
    void* manager = ManagerOfRef(RefByRuntimeId(id));
    if (!manager) return;
    void* start = sequence::Named(manager, Name(kAmbientStart));
    void* loop = sequence::Named(manager, Name(kAmbientLoop));
    const Held held{GameSeconds(), sequence::Active(start), sequence::Active(loop)};
    sequence::Stop(start);
    sequence::Stop(loop);
    g_held.emplace(id, held);
}

// Lets a held object go on from the pose it held, its queue's clock moved on
// by the time it stood still.
void Release(std::uint32_t id, const Held& held) {
    const double paused = GameSeconds() - held.since;
    auto it = g_queues.find(id);
    if (it != g_queues.end()) it->second.started += paused;
    void* manager = ManagerOfRef(RefByRuntimeId(id));
    if (!manager) return;
    const float seconds = static_cast<float>(paused);
    if (held.start) sequence::Resume(sequence::Named(manager, Name(kAmbientStart)), seconds);
    if (held.loop) sequence::Resume(sequence::Named(manager, Name(kAmbientLoop)), seconds);
}

// This tick's SkipAnim calls: an object asked for is held, one no longer
// asked for is released, and a scripted group overrides both.
void HoldTick() {
    for (auto it = g_held.begin(); it != g_held.end();) {
        const std::uint32_t id = it->first;
        if (g_skips.count(id) && !Scripted(id)) {
            ++it;
            continue;
        }
        if (!Scripted(id)) Release(id, it->second);
        it = g_held.erase(it);
    }
    for (std::uint32_t id : g_skips) {
        if (!g_held.count(id) && !Scripted(id)) Hold(id);
    }
    g_skips.clear();
}

// Plays each pending group whose 3D has loaded; drops one whose reference is
// gone or whose 3D never came.
void PendingTick() {
    for (auto it = g_pending.begin(); it != g_pending.end();) {
        const std::uint32_t id = it->first;
        void* ref = RefByRuntimeId(id);
        if (ref && Has3D(ref)) {
            const Pending due = it->second;
            it = g_pending.erase(it);
            Log("anim: %08X's 3D loaded -- playing the '%s' it was asked for %d tick(s) earlier",
                id, due.clip.group.c_str(), due.ticks);
            PlayGroupNow(id, due.clip.group, due.mode, due.clip.loops);
        } else {
            it = (!ref || ++it->second.ticks > kPendingTicks) ? g_pending.erase(it) : std::next(it);
        }
    }
}

void AnimTick() {
    PendingTick();
    HoldTick();
    for (auto it = g_queues.begin(); it != g_queues.end();) {
        it = Advance(it->first, it->second) ? std::next(it) : g_queues.erase(it);
    }
}

// --- actors: what the NPC animation driver asks of one reference -----------

using ActorQueryFn = bool (*)(void* vm, std::uint32_t stack, void* actor);

void*        g_sendAnimationEvent = nullptr;
ActorQueryFn g_isInCombat = nullptr;

std::uint32_t RefRuntimeId(const std::string& ref) {
    void* target = OwnerRef(ref);
    return target ? FormIdOf(target) : 0;
}

// 🛑 NotifyAnimationGraph itself, not Debug.SendAnimationEvent: the native
// discards the result, and "no graph took it" is what an actor whose 3D is
// still loading answers -- without it the first event after a cell load is
// lost and the NPC stands for the length of the clip it never started.
bool SendAnimEvent(std::uint32_t id, const std::string& event) {
    void* ref = RefByRuntimeId(id);
    if (!ref || !g_sendAnimationEvent || !IsActorRef(ref)) return false;
    void* holder = static_cast<char*>(ref) + ids::kGraphHolderOffset;
    void* name = Name(event);
    return VCall<bool (*)(void*, void**)>(holder, ids::kNotifyGraphSlot)(holder, &name);
}

bool RefPoseOf(std::uint32_t id, float out[6]) {
    void* ref = RefByRuntimeId(id);
    if (!ref) return false;
    const Pose pose = RefPose(PapyrusVm(), ref);
    for (int axis = 0; axis < kPoseAxes; ++axis) {
        out[axis] = pose.at[axis];
        out[kPoseAxes + axis] = pose.angle[axis];
    }
    return true;
}

void GlideRef(std::uint32_t id, const float pose[6], float seconds) {
    void* ref = RefByRuntimeId(id);
    if (!Has3D(ref)) return;
    Pose goal{};
    for (int axis = 0; axis < kPoseAxes; ++axis) {
        goal.at[axis] = pose[axis];
        goal.angle[axis] = pose[kPoseAxes + axis];
    }
    GlideTo(PapyrusVm(), ref, goal, seconds);
}

bool InCombat(std::uint32_t id) {
    return ActorInCombat(RefByRuntimeId(id));
}

void InstallActorAnimCalls(GameHooks& hooks) {
    g_sendAnimationEvent = Native<void*>("Debug.SendAnimationEvent",
                                         ids::kDebugSendAnimationEvent);
    g_isInCombat = Native<ActorQueryFn>("Actor.IsInCombat", ids::kActorIsInCombat);
    hooks.refRuntimeId = RefRuntimeId;
    hooks.sendAnimEvent = SendAnimEvent;
    hooks.refPose = RefPoseOf;
    hooks.glideRef = GlideRef;
    hooks.inCombat = InCombat;
}

}  // namespace

bool ActorInCombat(void* actor) {
    return actor && g_isInCombat && IsActorRef(actor) && g_isInCombat(PapyrusVm(), 0, actor);
}

void InstallAnimCalls(GameHooks& hooks) {
    InstallActorAnimCalls(hooks);
    if (!sequence::Install()) {
        Log("game: PlayGroup unavailable -- the sequence natives did not resolve");
        return;
    }
    hooks.playGroup = PlayGroup;
    hooks.skipAnim = SkipAnim;
    hooks.animTick = AnimTick;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
