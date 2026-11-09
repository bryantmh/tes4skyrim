// Where things ARE: position, angle, scale, the cell moves, the spawns and
// `Face`.
// See: docs/commentary/morrowind_runtime.md#game-calls

#include "game_calls_internal.h"

#include <algorithm>
#include <cmath>
#include <mutex>
#include <string>
#include <unordered_map>

#include "game_actor.h"
#include "glide.h"
#include "ids.h"
#include "log.h"
#include "main_thread.h"
#include "menu.h"
#include "object_script.h"
#include "object_tick.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

// ObjectReference.MoveTo(target, xOff, yOff, zOff, matchRotation): aims at
// another REFERENCE, so it serves travel markers only.
using MoveToFn = void (*)(void* vm, std::uint32_t stack, void* self,
                          void* target, float x, float y, float z,
                          bool matchRotation);
// TESObjectREFR::MoveTo_Impl: the move into a CELL or a WORLDSPACE.
using MoveToCellFn = void (*)(void* self, const std::uint32_t* targetHandle,
                              void* cell, void* world, const float* position,
                              const float* rotation);
// ObjectReference.SetScale(float).
using ScaleSetFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                            float scale);

// x, y, z.
constexpr int kAxisCount = kPoseAxes;
MoveToFn     g_moveTo = nullptr;
MoveToCellFn g_moveToCell = nullptr;
ScaleSetFn   g_setScale = nullptr;

// The scale a script last set on each reference, which reads answer until the
// field shows it: SetScale is queued, so `ModScale` right after it read the old
// one (1.3 for `SetScale 1` then `ModScale 0.1` from 1.2).
std::mutex g_scaleLock;
std::unordered_map<void*, float> g_scaleSet;

// Where a Move/Rotate chain has asked a reference to be, the tick it last
// asked, and the reference's FormID, since the pointer outlives an unload.
// Game thread only.
struct Goal : Pose {
    std::size_t tick;
    std::uint32_t formId;
};
std::unordered_map<void*, Goal> g_goals;

// The position and angles SetPos/SetAngle last wrote on each reference, and
// the tick it wrote them. The natives are queued, so `SetPos x` then `SetPos y`
// in one tick would read the old x back and undo it. Game thread only.
struct Written : Pose {
    std::size_t tick;
};
std::unordered_map<void*, Written> g_written;

float RefAngle(void* ref, int axis) { return RefAngleDegrees(ref, axis); }

float RefPosition(void* ref, int axis) {
    return RefPositionAxis(PapyrusVm(), ref, axis);
}

// An axis index from a script, kept inside the array whatever it says.
int SafeAxis(int axis) {
    return axis < 0 || axis >= kAxisCount ? 0 : axis;
}

float Position(const std::string& id, int axis) {
    return RefPosition(OwnerRef(id), SafeAxis(axis));
}

float Angle(const std::string& id, int axis) {
    return RefAngle(OwnerRef(id), axis);
}

// `PlaceAtPC id count` and `PlaceAtMe`: creates references of a BASE record
// beside `near` -- the player for the PC form, any reference for the other.
//
// 🛑 The new reference has no authored placement, so nothing in
// SCPT_instances.txt names it. Its instance is bound from the FormID PlaceAtMe
// RETURNS, which is the only way a spawned creature's script ever runs.
// See: docs/plans/morrowind_object_scripts.md#placeatpc
void PlaceNear(const std::string& near, const std::string& base, int count) {
    const FormRef* ref = FindBase(base);
    if (!ref || !g_placeAtMe) {
        Log("game: place '%s' -- %s", base.c_str(),
            ref ? "PlaceAtMe unresolved" : "no such base record");
        return;
    }
    void* at = OwnerRef(near);
    if (!at) return;
    const FormRef target = *ref;
    const std::string id = base;
    PostToMainThread([target, id, count, at]() {
        void* form = Form(&target);
        if (!form) return;
        void* made = g_placeAtMe(PapyrusVm(), 0, at, form,
                                 count > 0 ? count : 1, false, false);
        if (!made) {
            Log("game: placing '%s' created nothing", id.c_str());
            return;
        }
        BindSpawnedInstance(FormIdOf(made), id);
    });
}

// `PositionCell x y z zRot "cell"`.
void MoveRefToCell(const std::string& id, const std::string& cell, float x,
                   float y, float z, float zRot) {
    void* ref = OwnerRef(id);
    if (ref) SendToCell({ref}, cell, x, y, z, zRot);
}

// `Position x y z zRot`: the same without the cell change -- except for the
// PLAYER indoors, whom OpenMW moves to the exterior at those coordinates. The
// sidecar keys every exterior's worldspace by `sDefaultCellname`.
void MoveRefInCell(const std::string& id, float x, float y, float z,
                   float zRot) {
    void* ref = OwnerRef(id);
    if (!ref) return;
    if (ref == PlayerRef() && PlayerInInterior()) {
        const std::string exterior = GmstText("sDefaultCellname", "Wilderness");
        Log("move: player -> the exterior '%s' (%g, %g, %g) rot %g",
            exterior.c_str(), x, y, z, zRot);
        SendToCell({ref}, exterior, x, y, z, zRot);
        return;
    }
    PlaceAt(ref, x, y, z, zRot);
}

// The test runner's `follow`: in the cell `id` is in, the player indoors too.
void PlaceHere(const std::string& id, float x, float y, float z, float zRot) {
    if (void* ref = OwnerRef(id)) PlaceAt(ref, x, y, z, zRot);
}

// The chain `ref` is gliding in when it asked last tick or this one, else null.
Goal* LiveGoal(void* ref) {
    const std::size_t tick = TicksRun();
    auto it = g_goals.find(ref);
    if (it == g_goals.end() || it->second.tick + 1 < tick ||
        it->second.tick > tick) {
        return nullptr;
    }
    return &it->second;
}

// The goal this tick's Move/Rotate adds to: the live chain's own, else
// wherever the reference is now.
Goal& GoalFor(void* ref) {
    Goal* live = LiveGoal(ref);
    Goal& goal = live ? *live : g_goals[ref];
    if (!live) {
        static_cast<Pose&>(goal) = RefPose(PapyrusVm(), ref);
        goal.formId = FormIdOf(ref);
    }
    goal.tick = TicksRun();
    return goal;
}

// Glides `ref` to `goal`, arriving as the next tick starts. SetPosition and
// SetAngle reload the 3D, which fades it back in on every tick of a chain.
// See: docs/commentary/morrowind_runtime.md#move-and-rotate-are-rates
void GlideNextTick(void* ref, const Goal& goal) {
    GlideTo(PapyrusVm(), ref, goal, TickDelta());
}

// 🛑 TranslateTo leaves a static's or an activator's collision where it was,
// so a chain no script fed last tick is set once, with the natives, where it
// ended: Arktwend's raised harbor gate pole still blocked the road. Only a
// reference its FormID still names, and loaded: the pointer of one that left
// the world is stale.
void SettleGlides() {
    const std::size_t tick = TicksRun();
    for (auto it = g_goals.begin(); it != g_goals.end();) {
        if (it->second.tick + 1 >= tick) {
            ++it;
            continue;
        }
        const Goal& goal = it->second;
        void* ref = RefByRuntimeId(goal.formId);
        if (ref == it->first && Has3D(ref)) {
            SetRefPosition(PapyrusVm(), ref, goal.at[0], goal.at[1], goal.at[2]);
            SetRefAngle(PapyrusVm(), ref, goal.angle[0], goal.angle[1], goal.angle[2]);
        }
        it = g_goals.erase(it);
    }
}

// Where the player was as of the last tick, and the last tick a runtime move
// (a script's or travel's) put them somewhere. Game thread only.
void* g_playerPlace = nullptr;
float g_playerAt[kAxisCount] = {0.0f, 0.0f, 0.0f};
std::size_t g_playerMovedTick = 0;

// OpenMW's follower range for a teleport (ActionTeleport::getFollowers).
constexpr float kFollowersAlong = 800.0f;
// A follower of the current quest that has fallen behind stays within one exterior cell of the player in its
// place; one left from an old quest is elsewhere. Used where a lagging follower must still count.
constexpr float kPartyRange = 4096.0f;

// How far the player walks off the landing spot before the followers come out
// of the door onto it, so none lands inside the player; a player who stays
// there gets them after kLandingWaitTicks.
constexpr float kLandingClear = 128.0f;
constexpr std::size_t kLandingWaitTicks = 120;

// `talk`: past this the player walks up to the speaker, and stands this far in
// front of it (fActivatePickLength is 180).
constexpr float kTalkReach = 200.0f;
constexpr float kTalkStandOff = 80.0f;

// Followers a door took along that have not come out yet: their FormIDs, and
// the place, spot, Z angle and tick the player landed on. Game thread only.
std::vector<std::uint32_t> g_carried;
void* g_landPlace = nullptr;
float g_landAt[kAxisCount] = {0.0f, 0.0f, 0.0f};
float g_landAngle = 0.0f;
std::size_t g_landTick = 0;

// Whether `follower` stays outside: its script's `stayoutside` local is 1 and
// the player went indoors.
bool StaysOutside(void* follower) {
    bool found = false;
    const float value = GameActor(ActorKey(follower)).LocalVariable("stayoutside", &found);
    return found && value == 1.0f && PlayerInInterior();
}

// Brings the door's followers out onto the landing spot once the player has
// walked kLandingClear off it or waited kLandingWaitTicks there; none if the
// player went on to another place first, nor one its own follow package
// already brought through.
void FollowersOutOfDoor(void* place, const float at[kAxisCount]) {
    if (g_carried.empty()) return;
    if (place == g_landPlace) {
        const float dx = at[0] - g_landAt[0], dy = at[1] - g_landAt[1];
        const bool waited = TicksRun() >= g_landTick + kLandingWaitTicks;
        if (!waited && dx * dx + dy * dy < kLandingClear * kLandingClear) return;
        for (const std::uint32_t id : g_carried) {
            void* follower = RefByRuntimeId(id);
            if (!follower || IsDead(follower) || PlaceOf(follower) == place) continue;
            Log("move: %08X follows the player through the door", id);
            MoveInto(follower, place, g_landAt[0], g_landAt[1], g_landAt[2], g_landAngle);
        }
    }
    g_carried.clear();
}

// 🛑 A DOOR takes the player's followers along: OpenMW's ActionTeleport moves
// every follower within 800 units to where the player lands. Skyrim leaves it
// to the follow package's pathing, which left Mauragin on the airship's
// landing platform. A cell change no runtime move made is a door's; script
// moves (PositionCell) carry nobody, as in OpenMW. They come out of the door
// once the player has walked off the spot: three landed on the player in
// Arktwend's narrow Dwarven Shaft and shoved them back against the rails.
void FollowersThroughDoors() {
    void* player = PlayerRef();
    if (!player) return;
    void* place = PlaceOf(player);
    const float at[kAxisCount] = {RefPosition(player, 0), RefPosition(player, 1),
                                  RefPosition(player, 2)};
    FollowersOutOfDoor(place, at);
    const bool door = g_playerPlace && place && place != g_playerPlace &&
                      g_playerMovedTick + 2 < TicksRun();
    for (void* follower : door ? PlayerFollowers() : std::vector<void*>()) {
        if (IsDead(follower) || PlaceOf(follower) != g_playerPlace || StaysOutside(follower)) continue;
        const float dx = RefPosition(follower, 0) - g_playerAt[0];
        const float dy = RefPosition(follower, 1) - g_playerAt[1];
        const float dz = RefPosition(follower, 2) - g_playerAt[2];
        if (dx * dx + dy * dy + dz * dz > kFollowersAlong * kFollowersAlong) continue;
        g_carried.push_back(FormIdOf(follower));
    }
    if (door) {
        g_landPlace = place;
        std::copy(at, at + kAxisCount, g_landAt);
        g_landAngle = RefAngle(player, 2);
        g_landTick = TicksRun();
    }
    g_playerPlace = place;
    std::copy(at, at + kAxisCount, g_playerAt);
}

// What the tick runs for movement: glides settled, followers through doors.
void MoveTick() {
    SettleGlides();
    FollowersThroughDoors();
}

// Whether a SetPos/SetAngle `value` is already what `now` holds; angles wrap.
bool Unchanged(float now, float value, bool isAngle) {
    const float d = now - value;
    return std::fabs(isAngle ? std::remainder(d, 360.0f) : d) < 0.05f;
}

// `Move`/`MoveWorld`: adds `delta` along one axis. `local` rotates the offset
// into the object's own frame, which is the whole difference between them --
// a Z rotation is all an upright object has, so that is what is applied.
void MoveRefBy(const std::string& id, int axis, float delta, bool local) {
    void* ref = OwnerRef(id);
    if (!ref || !CanGlide()) return;
    float offset[kAxisCount] = {0.0f, 0.0f, 0.0f};
    offset[SafeAxis(axis)] = delta;
    RunOnGameThread([ref, offset, local]() {
        Goal& goal = GoalFor(ref);
        float x = offset[0], y = offset[1];
        if (local) {
            const float radians = goal.angle[2] / kDegreesPerRadian;
            x = offset[0] * std::cos(radians) - offset[1] * std::sin(radians);
            y = offset[0] * std::sin(radians) + offset[1] * std::cos(radians);
        }
        goal.at[0] += x;
        goal.at[1] += y;
        goal.at[2] += offset[2];
        GlideNextTick(ref, goal);
    });
}

// `Rotate`/`RotateWorld`: adds `degrees` to one Euler angle.
void RotateRefBy(const std::string& id, int axis, float degrees) {
    void* ref = OwnerRef(id);
    if (!ref || !CanGlide()) return;
    const int which = SafeAxis(axis);
    RunOnGameThread([ref, which, degrees]() {
        Goal& goal = GoalFor(ref);
        goal.angle[which] = std::remainder(goal.angle[which] + degrees, 360.0f);
        GlideNextTick(ref, goal);
    });
}

// The pose an instant SetPos/SetAngle builds on: what this tick already wrote
// on `ref`, else where the reference is now. Only this tick's write counts --
// a script setting z every frame must not pin a walking actor's x and y.
Pose& WrittenPose(void* ref) {
    const std::size_t tick = TicksRun();
    auto it = g_written.find(ref);
    if (it == g_written.end() || it->second.tick != tick) {
        Written fresh;
        static_cast<Pose&>(fresh) = RefPose(PapyrusVm(), ref);
        fresh.tick = tick;
        it = g_written.insert_or_assign(ref, fresh).first;
    }
    return it->second;
}

// `SetPos`/`SetAngle`: one axis, absolute. A reference mid-glide takes it as
// its glide's goal, since the natives reload the 3D and fade it back in.
// 🛑 The natives take ALL THREE axes, so the other two come from WrittenPose:
// `SetPos x` then `SetPos y` in one tick keeps both.
// 🛑 A value already held changes nothing and keeps no glide alive: a gate
// re-setting its raised pose every frame left its chain unsettled forever.
// 🛑 An actor's SetPos is a teleport, so it takes the engine move Position
// uses: after Papyrus SetPosition, Mauragin (companion warp) stood for good.
// See: docs/commentary/morrowind_runtime.md#move-and-rotate-are-rates
void SetAxis(const std::string& id, int axis, float value, bool isAngle) {
    void* ref = OwnerRef(id);
    if (!ref) return;
    const int which = SafeAxis(axis);
    RunOnGameThread([ref, which, value, isAngle]() {
        if (Goal* live = CanGlide() ? LiveGoal(ref) : nullptr) {
            float& held = (isAngle ? live->angle : live->at)[which];
            if (Unchanged(held, value, isAngle)) return;
            held = value;
            live->tick = TicksRun();
            GlideNextTick(ref, *live);
            return;
        }
        Pose& pose = WrittenPose(ref);
        float* xyz = isAngle ? pose.angle : pose.at;
        if (Unchanged(xyz[which], value, isAngle)) return;
        g_goals.erase(ref);
        xyz[which] = value;
        if (!isAngle && ref != PlayerRef() && IsActorRef(ref)) {
            MoveInto(ref, PlaceOf(ref), xyz[0], xyz[1], xyz[2], RefAngle(ref, 2));
            return;
        }
        (isAngle ? SetRefAngle : SetRefPosition)(PapyrusVm(), ref, xyz[0],
                                                 xyz[1], xyz[2]);
    });
}

void SetPosition(const std::string& id, int axis, float value) {
    SetAxis(id, axis, value, false);
}

void SetAngle(const std::string& id, int axis, float value) {
    SetAxis(id, axis, value, true);
}

float RefScale(const std::string& id) {
    void* ref = OwnerRef(id);
    if (!ref) return 1.0f;
    const float field = At<std::uint16_t>(ref, ActorField(ids::kOffRefScale)) / 100.0f;
    const std::lock_guard<std::mutex> hold(g_scaleLock);
    const auto set = g_scaleSet.find(ref);
    if (set == g_scaleSet.end()) return field;
    if (std::fabs(field - set->second) >= 0.005f) return set->second;
    g_scaleSet.erase(set);
    return field;
}

void SetRefScale(const std::string& id, float value) {
    void* ref = OwnerRef(id);
    if (!ref || !g_setScale) return;
    {
        const std::lock_guard<std::mutex> hold(g_scaleLock);
        g_scaleSet[ref] = value;
    }
    RunOnGameThread([ref, value]() {
        g_setScale(PapyrusVm(), 0, ref, value);
    });
}

// `PlaceItem`/`PlaceItemCell`: create the base at an absolute spot. An empty
// cell means the player's own, which is what the cell-less form does.
void PlaceBaseAt(const std::string& base, const std::string& cell, float x,
                 float y, float z, float zRot) {
    const FormRef* found = FindBase(base);
    if (!found || !g_placeAtMe) {
        if (!found) ReportOnce("base", base);
        return;
    }
    const FormRef target = *found;
    const FormRef* place = cell.empty() ? nullptr : FindCell(cell);
    const FormRef into = place ? *place : FormRef();
    const bool cross = place != nullptr;
    const std::string id = base;
    PostToMainThread([target, into, cross, id, x, y, z, zRot]() {
        void* player = PlayerRef();
        void* form = Form(&target);
        if (!player || !form) return;
        void* made = g_placeAtMe(PapyrusVm(), 0, player, form, 1, false,
                                 false);
        if (!made) return;
        if (!cross || !MoveInto(made, Form(&into), x, y, z, zRot)) {
            PlaceAt(made, x, y, z, zRot);
        }
        BindSpawnedInstance(FormIdOf(made), id);
    });
}

// `Face x y`: SetLookAt needs a REFERENCE to look at, and a bare point has
// none, so the actor is turned by its Z angle instead -- the same thing TES3
// means, since Face only ever turns an upright actor about Z.
void AiFacePoint(const std::string& actor, float x, float y) {
    void* ref = OwnerRef(actor);
    if (!ref) return;
    const float dx = x - RefPosition(ref, 0);
    const float dy = y - RefPosition(ref, 1);
    if (dx == 0.0f && dy == 0.0f) return;
    const float degrees = std::atan2(dx, dy) * 180.0f / 3.14159265f;
    SetRefAngle(PapyrusVm(), ref, RefAngle(ref, 0), RefAngle(ref, 1), degrees);
}

}  // namespace

// Puts `ref` at a spot where it already is, position and Z angle in ONE engine
// move. Papyrus SetPosition then SetAngle did not move it at all: both are
// queued, and SetAngle re-placed it at the position it read before the first
// had landed (`Position 32 48 0 0` left x and y unchanged).
void PlaceAt(void* ref, float x, float y, float z, float zRot) {
    if (ref == PlayerRef()) HoldMenusForPlayerMove();
    RunOnGameThread([ref, x, y, z, zRot]() {
        g_goals.erase(ref);
        if (!MoveInto(ref, PlaceOf(ref), x, y, z, zRot)) {
            Log("game: nowhere to place %08X -- no cell or worldspace", FormIdOf(ref));
        }
    });
}

// Where the player's last script move lands, until the player stands there. The engine first parks the player
// at the cell's door landing for a few frames (measured: chargen's papers move read at the courtyard door's
// landing, 1000 away, and the captain's within-300 box opened); OpenMW's move is instant.
namespace {

struct PlayerLanding {
    void* place = nullptr;
    float at[kAxisCount] = {0.0f, 0.0f, 0.0f};
    std::size_t tick = 0;
};
PlayerLanding g_landing;
// Ticks a landing is believed for at most, and how near the player must stand to have landed.
constexpr std::size_t kLandingTicks = 30;
constexpr float kLandedWithin = 16.0f;

}  // namespace

bool PlayerLandingSpot(void** place, float* at) {
    void* player = PlayerRef();
    if (!g_landing.place || !player || TicksRun() > g_landing.tick + kLandingTicks) {
        g_landing.place = nullptr;
        return false;
    }
    const float dx = RefPosition(player, 0) - g_landing.at[0];
    const float dy = RefPosition(player, 1) - g_landing.at[1];
    if (PlaceOf(player) == g_landing.place && dx * dx + dy * dy < kLandedWithin * kLandedWithin) {
        g_landing.place = nullptr;
        return false;
    }
    *place = g_landing.place;
    std::copy(g_landing.at, g_landing.at + kAxisCount, at);
    return true;
}

// 🛑 An interior is named by its CELL and an exterior by its WORLDSPACE, where
// the position picks the cell -- an unloaded exterior CELL is not a live form.
bool MoveInto(void* ref, void* place, float x, float y, float z, float zRot) {
    if (!g_moveToCell || !ref || !place) return false;
    const std::uint8_t type =
        static_cast<const std::uint8_t*>(place)[ids::kOffFormType];
    if (type != ids::kFormTypeCell && type != ids::kFormTypeWorld) return false;
    const bool interior = type == ids::kFormTypeCell;
    const std::uint32_t noTarget = 0;
    const float position[kAxisCount] = {x, y, z};
    const float rotation[kAxisCount] = {
        RefAngle(ref, 0) / kDegreesPerRadian,
        RefAngle(ref, 1) / kDegreesPerRadian, zRot / kDegreesPerRadian};
    if (ref == PlayerRef()) {
        g_playerMovedTick = TicksRun();
        g_landing = {place, {x, y, z}, TicksRun()};
        LeaveFollowersBehind(place, x, y, z);
    }
    const bool leftCell = PlaceOf(ref) != place || (!interior &&
        (Tes3GridIndex(RefPosition(ref, 0)) != Tes3GridIndex(x) ||
         Tes3GridIndex(RefPosition(ref, 1)) != Tes3GridIndex(y)));
    g_moveToCell(ref, &noTarget, interior ? place : nullptr,
                 interior ? nullptr : place, position, rotation);
    AiAfterScriptMove(ref, place, x, y, z, leftCell);
    return true;
}

void SendToCell(const std::vector<void*>& refs, const std::string& cell,
                float x, float y, float z, float zRot) {
    const FormRef* place = FindCell(cell);
    if (!place) {
        ReportOnce("cell", cell);
        return;
    }
    const FormRef target = *place;
    const std::string named = cell;
    if (std::find(refs.begin(), refs.end(), PlayerRef()) != refs.end()) {
        HoldMenusForPlayerMove();
    }
    PostToMainThread([refs, target, named, x, y, z, zRot]() {
        void* into = Form(&target);
        for (void* ref : refs) {
            if (!MoveInto(ref, into, x, y, z, zRot)) {
                Log("game: cell '%s' (%s|%08X) is not a cell or worldspace "
                    "in this load order -- nothing moved", named.c_str(),
                    target.plugin.c_str(), target.formId);
                return;
            }
        }
    });
}

void SendToMarker(const std::vector<void*>& refs, const FormRef& marker) {
    if (!g_moveTo) return;
    const FormRef target = marker;
    PostToMainThread([refs, target]() {
        void* to = Form(&target);
        if (!to) {
            Log("game: travel marker %s|%08X does not resolve -- nothing "
                "moved", target.plugin.c_str(), target.formId);
            return;
        }
        for (void* ref : refs) {
            if (ref == PlayerRef()) {
                g_playerMovedTick = TicksRun();
                LeaveFollowersBehind(PlaceOf(to), RefPosition(to, 0), RefPosition(to, 1), RefPosition(to, 2));
            }
            g_moveTo(PapyrusVm(), 0, ref, to, 0.0f, 0.0f, 0.0f, true);
        }
    });
}

// `approach`: puts the player in front of `id` when it stands in the player's
// own place but out of reach, as a player walks up to whoever they talk to.
// NPCs move, so a case need not rely on one standing where it was placed. No
// move with followers along (no teleporting past them) or across places (a
// speaker that is not home stays a failure). 1 when a move was posted, 0 when
// none is needed, -1 while `id` is not loaded.
std::vector<void*> PlayerFollowersWithin(float radius) {
    std::vector<void*> out;
    void* player = PlayerRef();
    for (void* follower : player ? PlayerFollowers() : std::vector<void*>()) {
        if (PlaceOf(follower) != PlaceOf(player)) continue;
        const float d = DistanceBetween(player, follower);
        if (d >= 0.0f && d <= radius) out.push_back(follower);
    }
    return out;
}

std::vector<void*> PlayerFollowersAlong() { return PlayerFollowersWithin(kFollowersAlong); }

std::vector<void*> PlayerParty() { return PlayerFollowersWithin(kPartyRange); }

int PlayerToSpeaker(const std::string& id) {
    void* npc = PlayerTarget(id);
    void* player = PlayerRef();
    if (!IsActorRef(npc) || !player || !g_moveTo) return -1;
    if (!PlayerParty().empty()) return 0;
    if (PlaceOf(npc) == PlaceOf(player) && DistanceBetween(player, npc) <= kTalkReach) return 0;
    const float heading = RefAngle(npc, 2) / kDegreesPerRadian;
    const float dx = std::sin(heading) * kTalkStandOff, dy = std::cos(heading) * kTalkStandOff;
    g_playerMovedTick = TicksRun();
    PostToMainThread([player, npc, dx, dy]() {
        g_playerMovedTick = TicksRun();
        LeaveFollowersBehind(PlaceOf(npc), RefPosition(npc, 0), RefPosition(npc, 1), RefPosition(npc, 2));
        g_moveTo(PapyrusVm(), 0, player, npc, dx, dy, 0.0f, false);
    });
    Log("move: the player walks up to '%s'", id.c_str());
    return 1;
}

namespace {

// `travelTo`: the player first, then whoever follows them, onto the
// destination's marker -- or through the cell's anchor when the export
// minted none.
// See: docs/commentary/morrowind_runtime.md#travel-markers
void TravelTo(const TravelDest& dest) {
    std::vector<void*> refs = PlayerFollowersAlong();
    if (void* player = PlayerRef()) refs.insert(refs.begin(), player);
    if (dest.hasMarker) {
        SendToMarker(refs, dest.marker);
    } else {
        Log("game: '%s' has no travel marker -- trying the cell's anchor",
            dest.name.c_str());
        SendToCell(refs, dest.name, dest.x, dest.y, dest.z, dest.zRot);
    }
}

}  // namespace

void InstallMoveCalls(GameHooks& hooks) {
    hooks.travelTo = TravelTo;
    hooks.playerToSpeaker = PlayerToSpeaker;
    if (!ResolveGlide()) Log("game: a position, angle or TranslateTo native is unresolved");
    g_placeAtMe = Native<PlaceAtMeFn>("ObjectReference.PlaceAtMe",
                                      ids::kRefPlaceAtMe);
    g_moveTo = Native<MoveToFn>("ObjectReference.MoveTo", ids::kRefMoveTo);
    g_moveToCell = Native<MoveToCellFn>("TESObjectREFR::MoveTo_Impl",
                                        ids::kRefMoveToCell);
    g_setScale = Native<ScaleSetFn>("ObjectReference.SetScale",
                                    ids::kRefSetScale);
    hooks.placeNear = PlaceNear;
    hooks.position = Position;
    hooks.setPosition = SetPosition;
    hooks.angle = Angle;
    hooks.setAngle = SetAngle;
    hooks.moveToCell = MoveRefToCell;
    hooks.moveInCell = MoveRefInCell;
    hooks.placeHere = PlaceHere;
    hooks.moveBy = MoveRefBy;
    hooks.rotateBy = RotateRefBy;
    hooks.moveTick = MoveTick;
    hooks.scale = RefScale;
    hooks.setScale = SetRefScale;
    hooks.placeAtCell = PlaceBaseAt;
    hooks.aiFace = AiFacePoint;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
