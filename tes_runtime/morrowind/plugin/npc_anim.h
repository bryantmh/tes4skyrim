// A TES3 NPC's OWN animation file: the idle groups its record names, played on
// the Skyrim body.
//
// An NPC_ record may name an animation file whose groups override the shared
// base animation, and its wander package's eight chances pick among `idle2`
// to `idle9` -- which is how a plugin seats, beds or dances an NPC, the game
// having no furniture. The converter retargets each group to a clip in the
// humanoid graph, entered by its own event, and stages `<plugin>.anims.txt`
// beside the plugin's sidecar folder; this is what plays them.
// See: docs/commentary/morrowind_runtime.md#npc-animation-files

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace tesruntime::mw {

void ClearNpcAnims();

// Reads `<root><plugin>.anims.txt` for every sidecar plugin under `root`,
// replacing what was loaded; a plugin without one stages no animation.
void LoadNpcAnims(const std::string& root);

// How many clips, bound NPCs and placements are loaded, for the log.
std::size_t NpcAnimClipCount();
std::size_t NpcAnimActorCount();
std::size_t NpcAnimPlaceCount();

// Forgets every actor being driven: a load or a new game.
void ResetNpcAnims();

// Once a tick of unpaused play: finds the bound NPCs now in the world and
// runs each one's idle choice. `cellChanged` restarts the search at once.
void TickNpcAnims(bool cellChanged);

// `PlayGroup` / `LoopGroup` on an NPC that has its own animation file. False
// when `npc` has none or its file has no such group, which leaves the call to
// the object animation path.
bool PlayNpcGroup(const std::string& npc, const std::string& group,
                  std::uint32_t loops);

// A script gave `npc` AI or moved it: it is no longer held on its authored
// spot, for good (the co-save keeps it). Nothing for an NPC with no binding.
// 🛑 Queued on the main thread, so a script move queued BEFORE it lands first:
// the release's glide ends where the actor then stands, and a release run
// ahead of the move glided a moved actor straight back to its old spot.
void ReleaseNpcHold(const std::string& npc);

// Whether the actor is held on a spot now. Game thread.
bool IsNpcHeld(std::uint32_t runtimeFormId);

// The fraction 0..1 the next idle roll uses, for the tests: null is uniform at
// random.
void SetNpcAnimRollForTest(float (*roll)());

// How many actors are being driven, and the group one is playing ("" for
// none), for the tests.
std::size_t NpcAnimLiveCount();
std::string NpcAnimPlaying(std::uint32_t runtimeFormId);

}  // namespace tesruntime::mw
