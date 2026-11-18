// Playing a NiControllerSequence that lives inside a mesh, the way
// ObjectReference.PlayGamebryoAnimation does: the NiControllerManager on a
// node, its name map, NiControllerSequence::Activate / ::Deactivate. No
// behaviour graph is involved, so it works on any mesh that carries a manager.
//
// Layout verified on 1.6.1170 against NiControllerSequence's constructor
// (0xd90360): weight +0x30, cycle type +0x40, frequency +0x44, begin key time
// +0x48 (initialised FLT_MAX), end key time +0x4c (-FLT_MAX), owner +0x60,
// state +0x68; Activate (0xd92eb0) refuses a sequence whose state is not 0.
// (docs/commentary/asset_convert_falloutnv.md#gun-parts)
//
// Time: Update (0xd92540) feeds `time + offset` (+0x6c, -FLT_MAX until the
// first update sets it to -time) to ComputeScaledTime (0xd93be0), which adds
// its change since last time (+0x50) to the weighted time (+0x54). An
// immediate Deactivate (0xd93030) folds the played time into the offset, and
// Activate without start-over keeps the offset, so a resumed sequence goes on
// as if it had kept playing unless the paused time is taken back out.

#pragma once

#include <cstdint>

namespace tesruntime::sequence {

// NiControllerSequence::CycleType.
constexpr std::uint32_t kCycleLoop = 0;
constexpr std::uint32_t kCycleClamp = 2;

// Resolves the engine entry points. False when one is missing.
bool Install();

// The NiControllerManager driving `node`'s animation, or null.
void* ManagerOf(void* node);

// The manager's sequence named by an interned BSFixedString pointer, or null.
void* Named(void* manager, void* name);

// Starts `seq` from its first key, stopping it first if it is running, and
// flags its manager active (NiTimeController flags +0x10, mask 0x8) as
// PlayGamebryoAnimation (0xa2ead0) does after Activate.
void Play(void* seq);

// Stops `seq`; its nodes keep the pose it last set.
void Stop(void* seq);

// Whether `seq` is playing (any state but inactive).
bool Active(void* seq);

// Whether any of `manager`'s sequences is playing, as the engine's IsAnimPlaying script function (1.6.1170
// 0x357b30) asks it, except that a clamped sequence whose time has reached its end key is done.
// (docs/commentary/tes_runtime_anim.md#is-anim-playing)
bool AnyPlaying(void* manager);

// Restarts a `Stop`ped `seq` where it stopped, `paused` seconds later.
void Resume(void* seq, float paused);

// Seconds one cycle of `seq` lasts, or 0 when it has no keys.
float Length(void* seq);

// The cycle type the mesh authored on `seq`.
std::uint32_t Cycle(void* seq);

}  // namespace tesruntime::sequence
