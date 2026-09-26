// Moving a reference without reloading its 3D, shared by the Morrowind runtime
// (MWScript Move/Rotate/SetPos) and TESRuntime (converted TES4 GameMode
// SetPos/SetAngle).
//
// SetPosition and SetAngle reload the reference's 3D, which fades back in:
// called every tick, the object never finishes fading. A step is instead a
// TranslateTo glide aimed at the pose the next step starts from.
// See: docs/commentary/morrowind_runtime.md#move-and-rotate-are-rates

#pragma once

#include <cstddef>

namespace tesruntime {

// x, y, z.
constexpr int kPoseAxes = 3;
constexpr float kDegreesPerRadian = 57.2957795f;

// Where a reference is, or is gliding to: position, then Euler angles in
// DEGREES (what the Papyrus angle natives take and return).
struct Pose {
    float at[kPoseAxes];
    float angle[kPoseAxes];
};

// Resolves the position, angle and TranslateTo natives. False when any is
// missing; the calls below then do nothing and read zeros.
bool ResolveGlide();

// True once TranslateTo resolved, so a caller can fall back to a teleport.
bool CanGlide();

// One position axis (0-2) of `ref`.
float RefPositionAxis(void* vm, void* ref, int axis);

// One rotation axis (0-2) in DEGREES, read off the reference: the angle
// getters have no stable id on a current build.
// See: docs/commentary/morrowind_runtime.md#the-angle-getters-have-no-id
float RefAngleDegrees(void* ref, int axis);

// The pose `ref` stands at now.
Pose RefPose(void* vm, void* ref);

// The instant setters, all three axes at once; angles in degrees. These reload
// the 3D, so they are for single teleports and for references with no 3D.
void SetRefPosition(void* vm, void* ref, float x, float y, float z);
void SetRefAngle(void* vm, void* ref, float x, float y, float z);

// Glides `ref` to `goal`, arriving `seconds` from now, angles the short way
// round. A pure rotation glides over a 0.01-unit Z nudge, since TranslateTo
// ends the rotation when the position arrives. Game thread only.
void GlideTo(void* vm, void* ref, const Pose& goal, float seconds);

}  // namespace tesruntime
