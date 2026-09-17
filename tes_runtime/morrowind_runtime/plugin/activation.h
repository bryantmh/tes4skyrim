// Which NPCs get the Morrowind menu, and which keep Skyrim's.
//
// Routing is ONE bit test on the FormID's load-order index byte, so a vanilla
// Skyrim NPC -- and every Oblivion-converted one -- is rejected before any map
// is touched. A plugin the user has not installed simply never sets a bit.
// See: docs/commentary/morrowind_runtime.md#activation
//
// 🛑 NEVER route by EditorID or file name (feedback_never_classify_by_filename).

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace mwruntime {

// Watches Skyrim's dialogue menu and diverts Morrowind speakers to ours.
// False when an address is missing, in which case NOTHING is hooked.
bool InstallActivation();

// True when this FormID belongs to a converted Morrowind plugin AND that
// actor has dialogue. The hot path is the first test.
bool IsMorrowindSpeaker(std::uint32_t formId);

// The TES3 id for a FormID, or empty when it is not a Morrowind actor.
const char* SpeakerId(std::uint32_t formId);

// The name a TESNPC shows in game, or "" -- read straight off the form.
const char* DisplayName(void* npc);

// The name the player entered at character creation, or "".
const char* PlayerName();

// Hands over the Papyrus VM, which Game.GetFormFromFile needs to resolve a
// plugin's CURRENT load-order index. Called from the SKSE Papyrus callback.
void SetPapyrusVm(void* vm);

// Reads every sidecar's MWAC.txt into the mask and the id map. Returns how
// many actors were indexed.
std::size_t LoadActorIndex();

// The same, from a caller-named root, so it is testable with no game install.
std::size_t LoadActorIndexFrom(const std::string& root);

// How many actors are indexed.
std::size_t SpeakerCount();

// True once the routing table loaded. NOT yet a guarantee that activations
// are diverted -- the event sink is still to come.
bool ActivationInstalled();

}  // namespace mwruntime
