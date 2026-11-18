// TES4's IsAnimPlaying for a reference with no behavior graph: a converted script asks the
// TESObjectREFR graph variable "TES4IsAnimPlaying" (script_convert command_rows `isanimplaying`), and the
// patched graph getter answers it from the reference's own NiControllerManager, as the engine's own
// IsAnimPlaying script function reads it. Every other variable goes to the engine unchanged.
// (docs/commentary/tes_runtime_anim.md#is-anim-playing)

#pragma once

namespace tesruntime {

// Patches TESObjectREFR's IAnimationGraphManagerHolder::GetGraphVariableBool slot. False when an address is
// missing (the variable then reads false, as on a build without TESRuntime).
bool InstallAnimPlaying();

}  // namespace tesruntime
