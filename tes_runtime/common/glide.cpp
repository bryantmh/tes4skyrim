#include "glide.h"

#include <cmath>
#include <cstdint>

#include "addresses.h"
#include "engine_ids.h"

namespace tesruntime {

namespace {

using AxisGetFn = float (*)(void* vm, std::uint32_t stack, void* ref);
using AxisSetFn = void (*)(void* vm, std::uint32_t stack, void* ref,
                           float x, float y, float z);
// ObjectReference.TranslateTo(x, y, z, ax, ay, az, speed, maxRotSpeed).
using TranslateToFn = bool (*)(void* vm, std::uint32_t stack, void* ref,
                               float x, float y, float z, float ax, float ay,
                               float az, float speed, float maxRotSpeed);

AxisGetFn g_getPosition[kPoseAxes] = {nullptr, nullptr, nullptr};
AxisSetFn g_setPosition = nullptr;
AxisSetFn g_setAngle = nullptr;
TranslateToFn g_translateTo = nullptr;

constexpr std::size_t kRotOffset[kPoseAxes] = {
    ids::kOffRefRotX, ids::kOffRefRotY, ids::kOffRefRotZ};

// The smallest glide TranslateTo is handed, since a rotation cannot finish
// before the position does.
constexpr float kNudge = 0.01f;

int SafeAxis(int axis) {
    return axis < 0 || axis >= kPoseAxes ? 0 : axis;
}

template <typename Fn>
bool Bind(Fn& slot, const char* name, std::uint64_t id) {
    slot = reinterpret_cast<Fn>(Resolve(name, id, nullptr));
    return slot != nullptr;
}

}  // namespace

bool ResolveGlide() {
    bool ok = true;
    ok &= Bind(g_getPosition[0], "ObjectReference.GetPositionX", ids::kRefGetPositionX);
    ok &= Bind(g_getPosition[1], "ObjectReference.GetPositionY", ids::kRefGetPositionY);
    ok &= Bind(g_getPosition[2], "ObjectReference.GetPositionZ", ids::kRefGetPositionZ);
    ok &= Bind(g_setPosition, "ObjectReference.SetPosition", ids::kRefSetPosition);
    ok &= Bind(g_setAngle, "ObjectReference.SetAngle", ids::kRefSetAngle);
    ok &= Bind(g_translateTo, "ObjectReference.TranslateTo", ids::kRefTranslateTo);
    return ok;
}

bool CanGlide() { return g_translateTo != nullptr; }

float RefPositionAxis(void* vm, void* ref, int axis) {
    AxisGetFn get = g_getPosition[SafeAxis(axis)];
    return ref && get ? get(vm, 0, ref) : 0.0f;
}

float RefAngleDegrees(void* ref, int axis) {
    if (!ref) return 0.0f;
    return At<float>(ref, kRotOffset[SafeAxis(axis)]) * kDegreesPerRadian;
}

Pose RefPose(void* vm, void* ref) {
    Pose pose{};
    for (int i = 0; i < kPoseAxes; ++i) {
        pose.at[i] = RefPositionAxis(vm, ref, i);
        pose.angle[i] = RefAngleDegrees(ref, i);
    }
    return pose;
}

void SetRefPosition(void* vm, void* ref, float x, float y, float z) {
    if (ref && g_setPosition) g_setPosition(vm, 0, ref, x, y, z);
}

void SetRefAngle(void* vm, void* ref, float x, float y, float z) {
    if (ref && g_setAngle) g_setAngle(vm, 0, ref, x, y, z);
}

void GlideTo(void* vm, void* ref, const Pose& goal, float seconds) {
    if (!ref || !g_translateTo || !(seconds > 0.0f)) return;
    float to[kPoseAxes];
    float angle[kPoseAxes];
    float distance = 0.0f;
    for (int i = 0; i < kPoseAxes; ++i) {
        to[i] = goal.at[i];
        const float from = RefAngleDegrees(ref, i);
        angle[i] = from + std::remainder(goal.angle[i] - from, 360.0f);
        const float step = to[i] - RefPositionAxis(vm, ref, i);
        distance += step * step;
    }
    distance = std::sqrt(distance);
    if (distance < kNudge * 0.5f) {
        to[2] += kNudge;
        distance = kNudge;
    }
    g_translateTo(vm, 0, ref, to[0], to[1], to[2], angle[0], angle[1],
                  angle[2], distance / seconds, 0.0f);
}

}  // namespace tesruntime
