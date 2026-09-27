// Where things ARE: position, angle, scale, the cell moves, the spawns and
// `Face`.
// See: docs/commentary/morrowind_runtime.md#game-calls

#include "game_calls_internal.h"

#include <cmath>
#include <string>
#include <unordered_map>

#include "glide.h"
#include "ids.h"
#include "log.h"
#include "main_thread.h"
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
// ObjectReference.GetScale() / .SetScale(float).
using ScaleGetFn = float (*)(void* vm, std::uint32_t stack, void* ref);
using ScaleSetFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                            float scale);

// x, y, z.
constexpr int kAxisCount = kPoseAxes;
MoveToFn     g_moveTo = nullptr;
MoveToCellFn g_moveToCell = nullptr;
ScaleGetFn   g_getScale = nullptr;
ScaleSetFn   g_setScale = nullptr;

// Where a Move/Rotate chain has asked a reference to be, and the tick it last
// asked. Game thread only.
struct Goal : Pose {
    std::size_t tick;
};
std::unordered_map<void*, Goal> g_goals;

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

// `Position x y z zRot`: the same without the cell change.
void MoveRefInCell(const std::string& id, float x, float y, float z,
                   float zRot) {
    void* ref = OwnerRef(id);
    if (!ref) return;
    RunOnGameThread([ref, x, y, z, zRot]() {
        PlaceAt(ref, x, y, z, zRot);
    });
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
    if (!live) static_cast<Pose&>(goal) = RefPose(PapyrusVm(), ref);
    goal.tick = TicksRun();
    return goal;
}

// Glides `ref` to `goal`, arriving as the next tick starts. SetPosition and
// SetAngle reload the 3D, which fades it back in on every tick of a chain.
// See: docs/commentary/morrowind_runtime.md#move-and-rotate-are-rates
void GlideNextTick(void* ref, const Goal& goal) {
    GlideTo(PapyrusVm(), ref, goal, TickDelta());
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

// `SetPos`/`SetAngle`: one axis, absolute. A reference mid-glide takes it as
// its glide's goal, since the natives reload the 3D and fade it back in.
// 🛑 The natives take ALL THREE axes, so the other two are read back first
// and written unchanged.
// See: docs/commentary/morrowind_runtime.md#move-and-rotate-are-rates
void SetAxis(const std::string& id, int axis, float value, bool isAngle) {
    void* ref = OwnerRef(id);
    if (!ref) return;
    const int which = SafeAxis(axis);
    RunOnGameThread([ref, which, value, isAngle]() {
        if (Goal* live = CanGlide() ? LiveGoal(ref) : nullptr) {
            (isAngle ? live->angle : live->at)[which] = value;
            live->tick = TicksRun();
            GlideNextTick(ref, *live);
            return;
        }
        g_goals.erase(ref);
        float xyz[kAxisCount];
        for (int i = 0; i < kAxisCount; ++i) {
            xyz[i] = isAngle ? RefAngle(ref, i) : RefPosition(ref, i);
        }
        xyz[which] = value;
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
    return ref && g_getScale ? g_getScale(PapyrusVm(), 0, ref) : 1.0f;
}

void SetRefScale(const std::string& id, float value) {
    void* ref = OwnerRef(id);
    if (!ref || !g_setScale) return;
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

// Sets all three position axes at once, which every absolute move needs --
// SetPosition takes the whole vector and SetAxis only ever changes one.
void PlaceAt(void* ref, float x, float y, float z, float zRot) {
    g_goals.erase(ref);
    SetRefPosition(PapyrusVm(), ref, x, y, z);
    SetRefAngle(PapyrusVm(), ref, RefAngle(ref, 0), RefAngle(ref, 1), zRot);
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
    g_moveToCell(ref, &noTarget, interior ? place : nullptr,
                 interior ? nullptr : place, position, rotation);
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
            g_moveTo(PapyrusVm(), 0, ref, to, 0.0f, 0.0f, 0.0f, true);
        }
    });
}

namespace {

// `travelTo`: the player first, then whoever follows them, onto the
// destination's marker -- or through the cell's anchor when the export
// minted none.
// See: docs/commentary/morrowind_runtime.md#travel-markers
void TravelTo(const TravelDest& dest) {
    std::vector<void*> refs = PlayerFollowers();
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
    if (!ResolveGlide()) Log("game: a position, angle or TranslateTo native is unresolved");
    g_placeAtMe = Native<PlaceAtMeFn>("ObjectReference.PlaceAtMe",
                                      ids::kRefPlaceAtMe);
    g_moveTo = Native<MoveToFn>("ObjectReference.MoveTo", ids::kRefMoveTo);
    g_moveToCell = Native<MoveToCellFn>("TESObjectREFR::MoveTo_Impl",
                                        ids::kRefMoveToCell);
    g_getScale = Native<ScaleGetFn>("ObjectReference.GetScale",
                                    ids::kRefGetScale);
    g_setScale = Native<ScaleSetFn>("ObjectReference.SetScale",
                                    ids::kRefSetScale);
    hooks.placeNear = PlaceNear;
    hooks.position = Position;
    hooks.setPosition = SetPosition;
    hooks.angle = Angle;
    hooks.setAngle = SetAngle;
    hooks.moveToCell = MoveRefToCell;
    hooks.moveInCell = MoveRefInCell;
    hooks.moveBy = MoveRefBy;
    hooks.rotateBy = RotateRefBy;
    hooks.scale = RefScale;
    hooks.setScale = SetRefScale;
    hooks.placeAtCell = PlaceBaseAt;
    hooks.aiFace = AiFacePoint;
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
