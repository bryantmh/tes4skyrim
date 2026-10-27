// Which idle each bound NPC plays, and when: OpenMW's AiWander idle choice
// over the clips the converter cut from the NPC's own animation file.
//
// Every clip LOOPS in the graph and this decides when one ends, so an NPC
// whose only idle is its seat stays seated with no gap between cycles. An
// actor that moves or fights is let go at once -- the clip state is a root
// state of the humanoid graph and carries no locomotion.
// See: docs/commentary/morrowind_runtime.md#npc-animation-files

#include "npc_anim.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iterator>
#include <limits>
#include <random>
#include <unordered_map>
#include <vector>

#include "dialogue_state.h"
#include "log.h"
#include "main_thread.h"
#include "object_tick.h"
#include "script_tables.h"
#include "store.h"

namespace tesruntime::mw {

namespace {

constexpr const char* kFileSuffix = ".anims.txt";
constexpr const char* kClipPrefix = "clip.";
constexpr const char* kActorPrefix = "actor.";
constexpr const char* kPlacePrefix = "place.";

// What leaves the clip state: the engine's own idle stop, which ends a vanilla
// idle and which the converted clip state also takes (npc_graph_morrowind.py).
constexpr const char* kStopEvent = "IdleStop";

// The animation file's own default pose, which plays whenever no numbered
// idle does, and the numbered idles a wander package's chances pick among.
constexpr const char* kPoseGroup = "idle";
constexpr int kIdleCount = 8;
constexpr int kFirstIdle = 2;

// Actors are evaluated every third tick (10 Hz), and the placement table is
// searched for newly loaded ones on a cell change and on this heartbeat.
constexpr std::size_t kTickStride = 3;
constexpr std::size_t kSweepTicks = 150;

// An actor that moved this far between evaluations is walking, and must stand
// still this long before it idles again (OpenMW waits for speed 0).
constexpr float kMoveEpsilon = 2.0f;
constexpr double kStillFor = 1.0;

// How long the choice may come up empty before the actor leaves the clip
// state. Morrowind shows its base idle for those frames; leaving at once would
// stand a seated NPC up for one tick at the end of a cycle.
constexpr double kLeaveGrace = 0.5;

// The playing clip's event is sent again this often, or after the clip's own
// seconds when longer: the engine sends its own `IdleStop` (dialogue, a hit),
// and nothing tells us that it did. A repeat while the state is still active
// is a self-transition the machine ignores; a one-shot idle (a cough) plays
// again each time its seconds run out.
constexpr double kRefresh = 2.0;

constexpr double kForever = std::numeric_limits<double>::infinity();

// A cell's references resolve some frames after the player's cell changes, so
// one search on that tick finds nothing and the next was a heartbeat away:
// the NPCs stood for five seconds and then dropped into their seats. For this
// many ticks after a cell change the table is searched every `kBurstStride`.
constexpr std::size_t kBurstTicks = 150;
constexpr std::size_t kBurstStride = 5;

// 🛑 An actor playing a clip is HELD on a TranslateTo glide: Morrowind seats
// an NPC by standing it inside the chair, and Skyrim's character controller
// pushes it out and up onto the furniture, while its package walks it off. A
// reference under translation is moved by the translation alone -- no
// physics, no pathing, no turning -- so the hold is a glide that never
// arrives: `kHoldRise` units over `kHoldSeconds`. An actor further than
// `kHoldSlack` from its spot, or turned, is first slid there in
// `kSettleSeconds`.
// See: docs/commentary/morrowind_runtime.md#npc-animation-files
constexpr float kHoldRise = 1.0f;
constexpr float kHoldSeconds = 10000.0f;
constexpr float kHoldSlack = 3.0f;
constexpr float kHoldTurn = 2.0f;
constexpr float kSettleSeconds = 0.25f;
constexpr float kReleaseSeconds = 0.05f;

// How long an NPC whose package lets it walk is left to, when its roll gives
// no idle: OpenMW's Wander_MoveNow.
constexpr double kWalkFor = 8.0;

constexpr int kPoseSize = 6;

struct Clip {
    std::string event;
    float seconds = 0.0f;
};

struct Binding {
    // "" for an NPC with no package and no clips: only held on its spot.
    std::string slug;
    int chances[kIdleCount] = {};
    // The wander package's distance: 0 never leaves its placement.
    int distance = 0;
};

struct Place {
    std::string plugin;
    std::uint32_t local = 0;
    std::string npc;
    // The authored x, y, z and angles in degrees, when the row carries them.
    float pose[kPoseSize] = {};
    bool hasPose = false;
};

struct Live {
    std::string npc;
    // The group now playing, "" while the actor is in no clip of ours.
    std::string playing;
    double until = 0.0;
    double sentAt = 0.0;
    // When the choice first came up empty, 0 while it has not.
    double emptySince = 0.0;
    bool scripted = false;
    float pos[3] = {};
    bool hasPos = false;
    double movedAt = -kForever;
    // The authored placement, and where the actor is held while it plays.
    float placed[kPoseSize] = {};
    bool hasPlaced = false;
    float goal[kPoseSize] = {};
    bool hasGoal = false;
    bool holding = false;
    double heldAt = 0.0;
    // Until when a walking NPC is left alone.
    double restUntil = 0.0;
};

std::unordered_map<std::string, Clip> g_clips;        // "slug|group"
std::unordered_map<std::string, Binding> g_bindings;  // by lowercased NPC id
std::vector<Place> g_places;
std::unordered_map<std::uint32_t, Live> g_live;       // game thread only
std::size_t g_untilSweep = 0;
std::size_t g_burst = 0;
float (*g_roll)() = nullptr;

std::string Lower(std::string text) {
    for (char& c : text) c = static_cast<char>(::tolower(static_cast<unsigned char>(c)));
    return text;
}

bool StartsWith(const std::string& text, const char* prefix, std::string* rest) {
    const std::string head = prefix;
    if (text.compare(0, head.size(), head) != 0) return false;
    *rest = text.substr(head.size());
    return true;
}

// `clip.<slug>.<group>=event|seconds`.
void ReadClip(const std::string& key, const std::string& value) {
    const std::size_t dot = key.rfind('.');
    const std::vector<std::string> f = SplitFields(value, '|');
    if (dot == std::string::npos || f.size() < 2) return;
    Clip clip;
    clip.event = f[0];
    clip.seconds = static_cast<float>(std::atof(f[1].c_str()));
    g_clips[key.substr(0, dot) + "|" + key.substr(dot + 1)] = clip;
}

// `actor.<npc id>=slug|c2,..,c9|distance`; an NPC with no wander package has
// no chances and distance 0.
void ReadActor(const std::string& npc, const std::string& value) {
    const std::vector<std::string> f = SplitFields(value, '|');
    Binding binding;
    binding.slug = f[0];
    if (f.size() > 1 && !f[1].empty()) {
        const std::vector<std::string> chances = SplitFields(f[1], ',');
        for (std::size_t i = 0; i < kIdleCount && i < chances.size(); ++i) {
            binding.chances[i] = std::atoi(chances[i].c_str());
        }
    }
    if (f.size() > 2) binding.distance = std::atoi(f[2].c_str());
    g_bindings[Lower(npc)] = binding;
}

// `place.<placement FormID>=plugin|npc id|x,y,z,rx,ry,rz`. A row short of all
// six numbers keeps none, so a half-written pose can never move an actor.
void ReadPlace(const std::string& formId, const std::string& value) {
    const std::vector<std::string> f = SplitFields(value, '|');
    if (f.size() < 2) return;
    Place place;
    place.plugin = f[0];
    place.local = static_cast<std::uint32_t>(std::strtoul(formId.c_str(), nullptr, 16));
    place.npc = Lower(f[1]);
    const std::vector<std::string> pose =
        f.size() > 2 ? SplitFields(f[2], ',') : std::vector<std::string>();
    if (pose.size() == kPoseSize) {
        for (int i = 0; i < kPoseSize; ++i) {
            place.pose[i] = static_cast<float>(std::atof(pose[i].c_str()));
        }
        place.hasPose = true;
    }
    g_places.push_back(place);
}

const Binding* FindBinding(const std::string& npc) {
    const auto it = g_bindings.find(npc);
    return it == g_bindings.end() ? nullptr : &it->second;
}

const Clip* FindClip(const Binding& binding, const std::string& group) {
    const auto it = g_clips.find(binding.slug + "|" + group);
    return it == g_clips.end() ? nullptr : &it->second;
}

float Roll() {
    if (g_roll) return g_roll();
    static std::mt19937 engine{std::random_device{}()};
    return std::uniform_real_distribution<float>(0.0f, 1.0f)(engine);
}

// OpenMW AiWander::getRandomIdle: the whole choice fails above
// fIdleChanceMultiplier, then each idle rolls against its chance and the
// highest passing roll wins. -1 for no idle.
int RandomIdle(const Binding& binding) {
    if (Roll() > GmstNumber("fIdleChanceMultiplier", 0.75f)) return -1;
    int pick = -1;
    float best = 0.0f;
    for (int i = 0; i < kIdleCount; ++i) {
        const float roll = Roll() * 100.0f;
        if (roll <= static_cast<float>(binding.chances[i]) && roll > best) {
            pick = i;
            best = roll;
        }
    }
    return pick;
}

// Ends the hold: a glide to where the actor already is arrives at once, and
// the engine takes the actor back.
void Release(std::uint32_t id, Live& actor) {
    float at[kPoseSize] = {};
    if (actor.hasGoal && Hooks().glideRef && Hooks().refPose &&
        Hooks().refPose(id, at)) {
        Hooks().glideRef(id, at, kReleaseSeconds);
    }
    actor.hasGoal = false;
    actor.holding = false;
    actor.hasPos = false;
}

void Leave(std::uint32_t id, Live& actor) {
    if (actor.hasGoal) Release(id, actor);
    if (actor.playing.empty()) return;
    if (Hooks().sendAnimEvent) Hooks().sendAnimEvent(id, kStopEvent);
    actor.playing.clear();
    actor.scripted = false;
    actor.emptySince = 0.0;
}

// Where an actor about to play is held: its authored placement when its
// package never leaves it, else where it stands.
void TakeGoal(std::uint32_t id, Live& actor) {
    const Binding* binding = FindBinding(actor.npc);
    if (binding && binding->distance == 0 && actor.hasPlaced) {
        for (int i = 0; i < kPoseSize; ++i) actor.goal[i] = actor.placed[i];
        actor.hasGoal = true;
    } else {
        actor.hasGoal = Hooks().refPose && Hooks().refPose(id, actor.goal);
    }
    actor.holding = false;
}

// Keeps a playing actor on its goal: slid there while it is off it, then held
// on the glide that never arrives, renewed every `kRefresh`.
void Hold(std::uint32_t id, Live& actor) {
    float at[kPoseSize] = {};
    if (!actor.hasGoal || !Hooks().glideRef || !Hooks().refPose ||
        !Hooks().refPose(id, at)) {
        return;
    }
    const float dx = at[0] - actor.goal[0];
    const float dy = at[1] - actor.goal[1];
    const float dz = at[2] - actor.goal[2];
    const float turn = std::fabs(std::remainder(at[5] - actor.goal[5], 360.0f));
    const double now = GameSeconds();
    if (std::sqrt(dx * dx + dy * dy + dz * dz) > kHoldSlack || turn > kHoldTurn) {
        Hooks().glideRef(id, actor.goal, kSettleSeconds);
        actor.holding = false;
        return;
    }
    if (actor.holding && now - actor.heldAt < kRefresh) return;
    float rise[kPoseSize];
    for (int i = 0; i < kPoseSize; ++i) rise[i] = actor.goal[i];
    rise[2] += kHoldRise;
    Hooks().glideRef(id, rise, kHoldSeconds);
    actor.holding = true;
    actor.heldAt = now;
}

// Plays `group` for `cycles` of its clip. A graph that does not take the
// event -- the actor's 3D is still loading -- leaves the actor as it was, so
// the next evaluation asks again.
bool Start(std::uint32_t id, Live& actor, const std::string& group,
           const Clip& clip, double cycles) {
    const double now = GameSeconds();
    if (actor.playing.empty() && !actor.hasGoal) TakeGoal(id, actor);
    Hold(id, actor);
    if (actor.playing != group ||
        now - actor.sentAt >= std::max<double>(kRefresh, clip.seconds)) {
        if (!Hooks().sendAnimEvent || !Hooks().sendAnimEvent(id, clip.event)) {
            return false;
        }
        if (actor.playing != group) {
            LogVerbose("anim: %s (%08X) plays '%s' for %.1fs", actor.npc.c_str(),
                       id, group.c_str(), clip.seconds * cycles);
        }
        actor.sentAt = now;
    }
    actor.playing = group;
    actor.until = now + clip.seconds * cycles;
    actor.emptySince = 0.0;
    return true;
}

// Whether the actor is walking or fighting, which the clip state cannot show.
// A held actor cannot walk, and its own slide into place is not a walk.
bool Busy(std::uint32_t id, Live& actor) {
    const double now = GameSeconds();
    float pos[kPoseSize] = {};
    if (!actor.hasGoal && Hooks().refPose && Hooks().refPose(id, pos)) {
        const float dx = pos[0] - actor.pos[0];
        const float dy = pos[1] - actor.pos[1];
        const float dz = pos[2] - actor.pos[2];
        if (actor.hasPos && std::sqrt(dx * dx + dy * dy + dz * dz) > kMoveEpsilon) {
            actor.movedAt = now;
        }
        actor.pos[0] = pos[0];
        actor.pos[1] = pos[1];
        actor.pos[2] = pos[2];
        actor.hasPos = true;
    }
    if (!actor.hasGoal && now - actor.movedAt < kStillFor) return true;
    return Hooks().inCombat && Hooks().inCombat(id);
}

// OpenMW's Wander_ChooseAction: a numbered idle when the roll gives one the
// file defines, else the file's own default pose, else nothing. With no idle
// rolled, an NPC whose package has a distance walks instead (Wander_MoveNow).
void Choose(std::uint32_t id, Live& actor, const Binding& binding) {
    const int idle = RandomIdle(binding);
    if (idle < 0 && binding.distance > 0) {
        Leave(id, actor);
        actor.restUntil = GameSeconds() + kWalkFor;
        return;
    }
    std::string group = idle < 0 ? "" : "idle" + std::to_string(idle + kFirstIdle);
    const Clip* clip = group.empty() ? nullptr : FindClip(binding, group);
    if (!clip) {
        group = kPoseGroup;
        clip = FindClip(binding, group);
    }
    if (clip) {
        Start(id, actor, group, *clip, 1.0);
        return;
    }
    if (actor.playing.empty()) return;
    const double now = GameSeconds();
    if (actor.emptySince == 0.0) actor.emptySince = now;
    if (now - actor.emptySince >= kLeaveGrace) Leave(id, actor);
}

bool Released(std::uint32_t id) {
    return State().holdReleased.count(id) != 0;
}

// An NPC with no package: held on its authored spot, as Morrowind leaves it,
// until it fights -- which lets it go for good, where the fight ends.
bool StepHeld(std::uint32_t id, Live& actor) {
    if (Hooks().inCombat && Hooks().inCombat(id)) {
        State().holdReleased.insert(id);
        Leave(id, actor);
        return false;
    }
    if (!actor.hasGoal) TakeGoal(id, actor);
    Hold(id, actor);
    return true;
}

// One actor's evaluation; false once it has left the world, died or been let go.
bool Step(std::uint32_t id, Live& actor) {
    if (Hooks().is3DLoaded && !Hooks().is3DLoaded(id)) return false;
    if (Hooks().isDead && Hooks().isDead(id)) return false;
    const Binding* binding = FindBinding(actor.npc);
    if (!binding) return false;
    if (Released(id)) {
        Leave(id, actor);
        return false;
    }
    if (binding->slug.empty()) return StepHeld(id, actor);
    if (Busy(id, actor)) {
        Leave(id, actor);
        return true;
    }
    const double now = GameSeconds();
    if (now < actor.restUntil) return true;
    Hold(id, actor);
    if (actor.scripted) {
        if (now < actor.until) return true;
        actor.scripted = false;
    }
    const bool numbered = !actor.playing.empty() && actor.playing != kPoseGroup;
    if (numbered && now < actor.until) {
        const Clip* clip = FindClip(*binding, actor.playing);
        if (clip && now - actor.sentAt >= kRefresh && Hooks().sendAnimEvent) {
            Hooks().sendAnimEvent(id, clip->event);
            actor.sentAt = now;
        }
        return true;
    }
    Choose(id, actor, *binding);
    return true;
}

// Adds every bound placement that is now in the world: at once and often for
// a while after a cell change, then on the heartbeat.
void Discover(bool cellChanged) {
    if (!Hooks().loadedRef || g_places.empty()) return;
    if (cellChanged) {
        g_burst = kBurstTicks;
        g_untilSweep = 0;
    }
    if (g_burst) --g_burst;
    if (g_untilSweep) {
        --g_untilSweep;
        return;
    }
    g_untilSweep = g_burst ? kBurstStride : kSweepTicks;
    for (const Place& place : g_places) {
        if (!FindBinding(place.npc)) continue;
        const std::uint32_t id = Hooks().loadedRef(place.plugin, place.local);
        if (!id || g_live.count(id) || Released(id)) continue;
        Live& actor = g_live[id];
        actor.npc = place.npc;
        actor.hasPlaced = place.hasPose;
        for (int i = 0; i < kPoseSize; ++i) actor.placed[i] = place.pose[i];
        LogVerbose("anim: %s (%08X) is in the world", place.npc.c_str(), id);
    }
}

}  // namespace

void ClearNpcAnims() {
    g_clips.clear();
    g_bindings.clear();
    g_places.clear();
    ResetNpcAnims();
}

void LoadNpcAnims(const std::string& rootIn) {
    ClearNpcAnims();
    if (rootIn.empty()) return;
    std::string root = rootIn;
    if (root.back() != '\\' && root.back() != '/') root.push_back('\\');
    for (const std::string& plugin : SidecarPlugins(root)) {
        ForEachTableRow(root + plugin + kFileSuffix,
                        [](const std::string& key, const std::string& value) {
                            std::string rest;
                            if (StartsWith(key, kClipPrefix, &rest)) {
                                ReadClip(rest, value);
                            } else if (StartsWith(key, kActorPrefix, &rest)) {
                                ReadActor(rest, value);
                            } else if (StartsWith(key, kPlacePrefix, &rest)) {
                                ReadPlace(rest, value);
                            }
                        });
    }
    Log("anim: %zu clip(s) for %zu NPC(s) at %zu placement(s)", g_clips.size(),
        g_bindings.size(), g_places.size());
}

std::size_t NpcAnimClipCount() { return g_clips.size(); }

std::size_t NpcAnimActorCount() { return g_bindings.size(); }

std::size_t NpcAnimPlaceCount() { return g_places.size(); }

void ResetNpcAnims() {
    g_live.clear();
    g_untilSweep = 0;
    g_burst = kBurstTicks;
}

void TickNpcAnims(bool cellChanged) {
    if (g_bindings.empty()) return;
    Discover(cellChanged);
    if (TicksRun() % kTickStride) return;
    for (auto it = g_live.begin(); it != g_live.end();) {
        it = Step(it->first, it->second) ? std::next(it) : g_live.erase(it);
    }
}

bool PlayNpcGroup(const std::string& npcIn, const std::string& groupIn,
                  std::uint32_t loops) {
    const std::string npc = Lower(npcIn);
    const std::string group = Lower(groupIn);
    const Binding* binding = FindBinding(npc);
    const Clip* clip = binding ? FindClip(*binding, group) : nullptr;
    if (!clip) return false;
    const double cycles = loops == kLoopForever ? kForever : loops + 1.0;
    const Clip chosen = *clip;
    const auto play = [npcIn, npc, group, chosen, cycles]() {
        const std::uint32_t id =
            Hooks().refRuntimeId ? Hooks().refRuntimeId(npcIn) : 0;
        if (!id) return;
        Live& actor = g_live[id];
        actor.npc = npc;
        actor.scripted = Start(id, actor, group, chosen, cycles);
    };
    if (!RunOnGameThread(play)) play();
    return true;
}

void ReleaseNpcHold(const std::string& npcIn) {
    const std::string npc = Lower(npcIn);
    if (!FindBinding(npc)) return;
    const auto release = [npcIn]() {
        const std::uint32_t id =
            Hooks().refRuntimeId ? Hooks().refRuntimeId(npcIn) : 0;
        if (!id || !State().holdReleased.insert(id).second) return;
        const auto it = g_live.find(id);
        if (it != g_live.end()) Leave(id, it->second);
        LogVerbose("anim: %s (%08X) is let go", npcIn.c_str(), id);
    };
    if (!PostToMainThread(release)) release();
}

void SetNpcAnimRollForTest(float (*roll)()) { g_roll = roll; }

bool IsNpcHeld(std::uint32_t runtimeFormId) {
    const auto it = g_live.find(runtimeFormId);
    return it != g_live.end() && it->second.hasGoal;
}

std::size_t NpcAnimLiveCount() { return g_live.size(); }

std::string NpcAnimPlaying(std::uint32_t runtimeFormId) {
    const auto it = g_live.find(runtimeFormId);
    return it == g_live.end() ? std::string() : it->second.playing;
}

}  // namespace tesruntime::mw
