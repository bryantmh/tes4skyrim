// Where things ARE: position, angle, scale, the cell moves, the spawns and
// `Face`.
// See: docs/commentary/morrowind_runtime.md#game-calls

#include "game_calls_internal.h"

#include <cmath>
#include <string>
#include <unordered_map>

#include "ids.h"
#include "log.h"
#include "main_thread.h"
#include "object_script.h"
#include "object_tick.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

// One axis of a reference's position or rotation; the setters take all three.
using AxisGetFn = float (*)(void* vm, std::uint32_t stack, void* ref);
using AxisSetFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                           float x, float y, float z);
// ObjectReference.MoveTo(target, xOff, yOff, zOff, matchRotation): aims at
// another REFERENCE, so it serves travel markers only.
using MoveToFn = void (*)(void* vm, std::uint32_t stack, void* self,
                          void* target, float x, float y, float z,
                          bool matchRotation);
// TESObjectREFR::MoveTo_Impl: the move into a CELL or a WORLDSPACE.
using MoveToCellFn = void (*)(void* self, const std::uint32_t* targetHandle,
                              void* cell, void* world, const float* position,
                              const float* rotation);
// ObjectReference.TranslateTo(x, y, z, ax, ay, az, speed, maxRotSpeed).
using TranslateToFn = bool (*)(void* vm, std::uint32_t stack, void* ref,
                               float x, float y, float z, float ax, float ay,
                               float az, float speed, float maxRotSpeed);
// ObjectReference.GetScale() / .SetScale(float).
using ScaleGetFn = float (*)(void* vm, std::uint32_t stack, void* ref);
using ScaleSetFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                            float scale);

// x, y, z.
constexpr int kAxisCount = 3;
AxisGetFn g_getPosition[kAxisCount] = {nullptr, nullptr, nullptr};
// The rotation field's offset per axis; there is no native to call.
// See: docs/commentary/morrowind_runtime.md#the-angle-getters-have-no-id
constexpr std::size_t kRotOffset[kAxisCount] = {
    ids::kOffRefRotX, ids::kOffRefRotY, ids::kOffRefRotZ};

// Radians per degree, for the conversion the absent getters used to do.
constexpr float kDegreesPerRadian = 57.2957795f;

// One rotation axis in DEGREES, read straight off the reference. The axis is
// clamped here rather than through SafeAxis, which is declared further down.
float RefAngle(void* ref, int axis) {
    if (!ref) return 0.0f;
    const int which = axis < 0 || axis >= kAxisCount ? 0 : axis;
    const float radians = *reinterpret_cast<const float*>(
        static_cast<const char*>(ref) + kRotOffset[which]);
    return radians * kDegreesPerRadian;
}
AxisSetFn g_setPosition = nullptr;
AxisSetFn g_setAngle = nullptr;
MoveToFn     g_moveTo = nullptr;
MoveToCellFn g_moveToCell = nullptr;
TranslateToFn g_translateTo = nullptr;
ScaleGetFn   g_getScale = nullptr;
ScaleSetFn   g_setScale = nullptr;

// Where a Move/Rotate chain has asked a reference to be, and the tick it last
// asked. Game thread only.
struct Goal {
    float at[kAxisCount];
    float angle[kAxisCount];
    std::size_t tick;
};
std::unordered_map<void*, Goal> g_goals;

// The smallest glide TranslateTo is handed, since a rotation cannot finish
// before the position does.
constexpr float kNudge = 0.01f;

float RefPosition(void* ref, int axis) {
    AxisGetFn get = g_getPosition[axis];
    return get ? get(PapyrusVm(), 0, ref) : 0.0f;
}

// An axis index from a script, kept inside the array whatever it says.
int SafeAxis(int axis) {
    return axis < 0 || axis >= kAxisCount ? 0 : axis;
}

float Position(const std::string& id, int axis) {
    void* ref = OwnerRef(id);
    AxisGetFn get = g_getPosition[SafeAxis(axis)];
    return ref && get ? get(PapyrusVm(), 0, ref) : 0.0f;
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

// Puts `ref` at a spot inside `place`, a CELL or a WORLDSPACE form; false when
// it is neither. Game thread only.
//
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
    if (!live) {
        for (int i = 0; i < kAxisCount; ++i) {
            goal.at[i] = RefPosition(ref, i);
            goal.angle[i] = RefAngle(ref, i);
        }
    }
    goal.tick = TicksRun();
    return goal;
}

// Glides `ref` to `goal`, arriving as the next tick starts. SetPosition and
// SetAngle reload the 3D, which fades it back in on every tick of a chain.
// See: docs/commentary/morrowind_runtime.md#move-and-rotate-are-rates
void GlideTo(void* ref, const Goal& goal) {
    float to[kAxisCount];
    float angle[kAxisCount];
    float distance = 0.0f;
    for (int i = 0; i < kAxisCount; ++i) {
        to[i] = goal.at[i];
        const float from = RefAngle(ref, i);
        angle[i] = from + std::remainder(goal.angle[i] - from, 360.0f);
        const float step = to[i] - RefPosition(ref, i);
        distance += step * step;
    }
    distance = std::sqrt(distance);
    if (distance < kNudge * 0.5f) {
        to[2] += kNudge;
        distance = kNudge;
    }
    g_translateTo(PapyrusVm(), 0, ref, to[0], to[1], to[2], angle[0],
                  angle[1], angle[2], distance / TickDelta(), 0.0f);
}

// `Move`/`MoveWorld`: adds `delta` along one axis. `local` rotates the offset
// into the object's own frame, which is the whole difference between them --
// a Z rotation is all an upright object has, so that is what is applied.
void MoveRefBy(const std::string& id, int axis, float delta, bool local) {
    void* ref = OwnerRef(id);
    if (!ref || !g_translateTo) return;
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
        GlideTo(ref, goal);
    });
}

// `Rotate`/`RotateWorld`: adds `degrees` to one Euler angle.
void RotateRefBy(const std::string& id, int axis, float degrees) {
    void* ref = OwnerRef(id);
    if (!ref || !g_translateTo) return;
    const int which = SafeAxis(axis);
    RunOnGameThread([ref, which, degrees]() {
        Goal& goal = GoalFor(ref);
        goal.angle[which] = std::remainder(goal.angle[which] + degrees, 360.0f);
        GlideTo(ref, goal);
    });
}

// `SetPos`/`SetAngle`: one axis, absolute. A reference mid-glide takes it as
// its glide's goal, since the natives reload the 3D and fade it back in.
// 🛑 The natives take ALL THREE axes, so the other two are read back first
// and written unchanged.
// See: docs/commentary/morrowind_runtime.md#move-and-rotate-are-rates
void SetAxis(const std::string& id, int axis, float value, bool isAngle) {
    void* ref = OwnerRef(id);
    AxisSetFn set = isAngle ? g_setAngle : g_setPosition;
    if (!ref || !set) return;
    const int which = SafeAxis(axis);
    RunOnGameThread([ref, set, which, value, isAngle]() {
        if (Goal* live = g_translateTo ? LiveGoal(ref) : nullptr) {
            (isAngle ? live->angle : live->at)[which] = value;
            live->tick = TicksRun();
            GlideTo(ref, *live);
            return;
        }
        g_goals.erase(ref);
        float xyz[kAxisCount];
        for (int i = 0; i < kAxisCount; ++i) {
            xyz[i] = isAngle ? RefAngle(ref, i) : RefPosition(ref, i);
        }
        xyz[which] = value;
        set(PapyrusVm(), 0, ref, xyz[0], xyz[1], xyz[2]);
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
    if (!ref || !g_setAngle || !g_getPosition[0]) return;
    const float dx = x - g_getPosition[0](PapyrusVm(), 0, ref);
    const float dy = y - g_getPosition[1](PapyrusVm(), 0, ref);
    if (dx == 0.0f && dy == 0.0f) return;
    const float degrees = std::atan2(dx, dy) * 180.0f / 3.14159265f;
    const float ax = RefAngle(ref, 0);
    const float ay = RefAngle(ref, 1);
    g_setAngle(PapyrusVm(), 0, ref, ax, ay, degrees);
}

}  // namespace

// Sets all three position axes at once, which every absolute move needs --
// SetPosition takes the whole vector and SetAxis only ever changes one.
void PlaceAt(void* ref, float x, float y, float z, float zRot) {
    g_goals.erase(ref);
    if (g_setPosition) g_setPosition(PapyrusVm(), 0, ref, x, y, z);
    if (!g_setAngle) return;
    const float ax = RefAngle(ref, 0);
    const float ay = RefAngle(ref, 1);
    g_setAngle(PapyrusVm(), 0, ref, ax, ay, zRot);
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
    g_getPosition[0] = Native<AxisGetFn>("ObjectReference.GetPositionX",
                                         ids::kRefGetPositionX);
    g_getPosition[1] = Native<AxisGetFn>("ObjectReference.GetPositionY",
                                         ids::kRefGetPositionY);
    g_getPosition[2] = Native<AxisGetFn>("ObjectReference.GetPositionZ",
                                         ids::kRefGetPositionZ);
    g_setPosition = Native<AxisSetFn>("ObjectReference.SetPosition",
                                      ids::kRefSetPosition);
    g_setAngle = Native<AxisSetFn>("ObjectReference.SetAngle",
                                   ids::kRefSetAngle);
    g_placeAtMe = Native<PlaceAtMeFn>("ObjectReference.PlaceAtMe",
                                      ids::kRefPlaceAtMe);
    g_moveTo = Native<MoveToFn>("ObjectReference.MoveTo", ids::kRefMoveTo);
    g_moveToCell = Native<MoveToCellFn>("TESObjectREFR::MoveTo_Impl",
                                        ids::kRefMoveToCell);
    g_translateTo = Native<TranslateToFn>("ObjectReference.TranslateTo",
                                          ids::kRefTranslateTo);
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
