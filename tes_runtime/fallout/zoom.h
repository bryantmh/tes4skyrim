// Iron sights for the player's gun. The zoom key ramps a blend (0 hip, 1
// iron) over kZoomSeconds, every frame: the graph's REAL `fGunZoom` (the
// aim poses crossfade on it), the camera FOV towards the gun's Sight FOV,
// (one tick per key-thread poll, posted to the main thread; a task that
// re-posted itself looped inside the task queue and froze the game) and
// a once-a-second measurement of the weapon's `##SightingNode` against
// the first-person root (the alignment itself is not built yet, see
// docs/commentary/tes_runtime_guns.md#sight-alignment). The attack selector's INT
// `iGunZoom` follows the blend's target only while no fire clip plays,
// since a selector change restarts the clip and its shot trigger.
// (docs/commentary/tes_runtime_guns.md#zoom)

#pragma once

namespace tesruntime {

// Resolves the camera. False when an address is unresolved.
bool InstallZoom();

// The zoom key's state, from the key thread's task on the main thread.
void ZoomSet(bool zoomed);

// The player's fire clip is running (true from the fire press to its end).
void ZoomNoteFiring(bool firing);

// One frame of the blend; the key thread posts it while ZoomActive().
void ZoomTick();
bool ZoomActive();

}  // namespace tesruntime
