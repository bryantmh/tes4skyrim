// Where things ARE: position, angle, scale, the cell moves, the spawns and
// `Face`.
// See: docs/commentary/morrowind_runtime.md#game-calls

#include "game_calls_internal.h"

#include <cmath>
#include <string>

#include "ids.h"
#include "log.h"
#include "main_thread.h"
#include "object_script.h"

namespace mwruntime {
namespace gamecalls {

namespace {

// One axis of a reference's position or rotation; the setters take all three.
using AxisGetFn = float (*)(void* vm, std::uint32_t stack, void* ref);
using AxisSetFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                           float x, float y, float z);
// ObjectReference.MoveTo(target, xOff, yOff, zOff, matchRotation): the only
// call that crosses cells, and it aims at another REFERENCE.
using MoveToFn = void (*)(void* vm, std::uint32_t stack, void* self,
                          void* target, float x, float y, float z,
                          bool matchRotation);
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
ScaleGetFn   g_getScale = nullptr;
ScaleSetFn   g_setScale = nullptr;

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

// 🛑 The natives take ALL THREE axes, so the other two are read back first
// and written unchanged. Posted, like every other call that moves something:
// the menu's callbacks do not run on the game thread.
void SetAxis(const std::string& id, int axis, float value, bool isAngle) {
    void* ref = OwnerRef(id);
    AxisSetFn set = isAngle ? g_setAngle : g_setPosition;
    if (!ref || !set) return;
    float xyz[kAxisCount];
    for (int i = 0; i < kAxisCount; ++i) {
        xyz[i] = isAngle ? RefAngle(ref, i)
                         : (g_getPosition[i]
                                ? g_getPosition[i](PapyrusVm(), 0, ref)
                                : 0.0f);
    }
    xyz[SafeAxis(axis)] = value;
    const float x = xyz[0], y = xyz[1], z = xyz[2];
    PostToMainThread([ref, set, x, y, z]() {
        set(PapyrusVm(), 0, ref, x, y, z);
    });
}

void SetPosition(const std::string& id, int axis, float value) {
    SetAxis(id, axis, value, false);
}

void SetAngle(const std::string& id, int axis, float value) {
    SetAxis(id, axis, value, true);
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

// `PositionCell x y z zRot "cell"`: the anchor carries the move across the
// cell boundary and the position puts the reference where the script asked.
//
// 🛑 MoveTo is the ONLY call that changes an object's cell, and it aims at
// another REFERENCE -- hence the staged anchor. Both steps are posted
// together so the reference is never seen at the anchor's own spot.
// See: docs/commentary/morrowind_runtime.md#positioncell-needs-an-anchor
void MoveRefToCell(const std::string& id, const std::string& cell, float x,
                   float y, float z, float zRot) {
    const FormRef* anchor = FindCellAnchor(cell);
    if (!anchor || !g_moveTo) {
        if (!anchor) ReportOnce("cell", cell);
        return;
    }
    void* ref = OwnerRef(id);
    if (!ref) return;
    const FormRef target = *anchor;
    PostToMainThread([ref, target, x, y, z, zRot]() {
        void* to = Form(&target);
        if (!to) return;
        g_moveTo(PapyrusVm(), 0, ref, to, 0.0f, 0.0f, 0.0f, false);
        PlaceAt(ref, x, y, z, zRot);
    });
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

// `Move`/`MoveWorld`: adds `delta` along one axis. `local` rotates the offset
// into the object's own frame, which is the whole difference between them --
// a Z rotation is all an upright object has, so that is what is applied.
void MoveRefBy(const std::string& id, int axis, float delta, bool local) {
    void* ref = OwnerRef(id);
    if (!ref || !g_setPosition) return;
    float offset[kAxisCount] = {0.0f, 0.0f, 0.0f};
    offset[SafeAxis(axis)] = delta;
    RunOnGameThread([ref, offset, local]() {
        float x = offset[0], y = offset[1];
        if (local) {
            const float radians = RefAngle(ref, 2) / kDegreesPerRadian;
            x = offset[0] * std::cos(radians) - offset[1] * std::sin(radians);
            y = offset[0] * std::sin(radians) + offset[1] * std::cos(radians);
        }
        float at[kAxisCount];
        for (int i = 0; i < kAxisCount; ++i) {
            at[i] = g_getPosition[i] ? g_getPosition[i](PapyrusVm(), 0, ref)
                                     : 0.0f;
        }
        g_setPosition(PapyrusVm(), 0, ref, at[0] + x, at[1] + y,
                      at[2] + offset[2]);
    });
}

// `Rotate`/`RotateWorld`: adds `degrees` to one Euler angle.
void RotateRefBy(const std::string& id, int axis, float degrees) {
    void* ref = OwnerRef(id);
    if (!ref || !g_setAngle) return;
    const int which = SafeAxis(axis);
    RunOnGameThread([ref, which, degrees]() {
        float at[kAxisCount];
        for (int i = 0; i < kAxisCount; ++i) {
            at[i] = RefAngle(ref, i);
        }
        at[which] += degrees;
        g_setAngle(PapyrusVm(), 0, ref, at[0], at[1], at[2]);
    });
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
    const FormRef* anchor = cell.empty() ? nullptr : FindCellAnchor(cell);
    const FormRef into = anchor ? *anchor : FormRef();
    const bool cross = anchor != nullptr;
    const std::string id = base;
    PostToMainThread([target, into, cross, id, x, y, z, zRot]() {
        void* player = PlayerRef();
        void* form = Form(&target);
        if (!player || !form) return;
        void* made = g_placeAtMe(PapyrusVm(), 0, player, form, 1, false,
                                 false);
        if (!made) return;
        if (cross && g_moveTo) {
            if (void* to = Form(&into)) {
                g_moveTo(PapyrusVm(), 0, made, to, 0.0f, 0.0f, 0.0f, false);
            }
        }
        PlaceAt(made, x, y, z, zRot);
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
    if (g_setPosition) g_setPosition(PapyrusVm(), 0, ref, x, y, z);
    if (!g_setAngle) return;
    const float ax = RefAngle(ref, 0);
    const float ay = RefAngle(ref, 1);
    g_setAngle(PapyrusVm(), 0, ref, ax, ay, zRot);
}

void InstallMoveCalls(GameHooks& hooks) {
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
}  // namespace mwruntime
