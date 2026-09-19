// OpenMW's TravelWindow as rows of the list modal: each place the speaker
// goes and its fare, greyed when the player cannot pay.
// See: docs/commentary/morrowind_runtime.md#travel

#pragma once

#include <string>

namespace mwruntime {

// What the window does once the fare is paid and before the player is moved
// -- it closes, as OpenMW leaves dialogue.
using TravelLeaving = void (*)();

// Shows the modal for `actor`. Nothing is shown when it offers no travel.
void OpenTravelModal(const std::string& actor, TravelLeaving onLeaving);

}  // namespace mwruntime
