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

// The form with plugin-LOCAL id `local` in `file`, through the running load
// order, or null. The only correct way to reach a converted record.
void* FormFromFile(const char* file, std::uint32_t local);

// A form's own FormID, read at TESForm+0x14; 0 for null.
std::uint32_t FormIdOf(void* form);

// The Papyrus VM every native call is made against.
void* PapyrusVm();

// Builds the engine's BSFixedString for `text` into `*out`, which a native
// taking a string argument is handed by address. False when the constructor
// did not resolve.
bool FixedString(void** out, const char* text);

// Hands over the Papyrus VM, which Game.GetFormFromFile needs to resolve a
// plugin's CURRENT load-order index. Called from the SKSE Papyrus callback.
void SetPapyrusVm(void* vm);

// Reads every sidecar's NPC__index.txt into the mask and id map. Returns how
// many actors were indexed.
std::size_t LoadActorIndex();

// Resolves every staged script instance through the running load order, so an
// engine hook holding a live FormID can find the instance it belongs to.
// Returns how many resolved.
std::size_t BindInstances();

// The same, from a caller-named root, so it is testable with no game install.
std::size_t LoadActorIndexFrom(const std::string& root);

// How many actors are indexed.
std::size_t SpeakerCount();

// True when this TES3 id names an actor that was actually CONVERTED, so the
// player can meet them. An id in the dialogue tables but not here belongs to
// a master whose world this conversion does not include.
bool SpeakerExists(const std::string& id);

// True once the routing table loaded. NOT yet a guarantee that activations
// are diverted -- the event sink is still to come.
bool ActivationInstalled();

}  // namespace mwruntime
