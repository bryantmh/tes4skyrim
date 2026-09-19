// The travel service's rules, with no window: which places an actor goes,
// what each costs this player, how long it takes, and taking the trip.
// OpenMW's TravelWindow::addDestination and onTravelButtonClick.
// See: docs/commentary/morrowind_runtime.md#travel

#pragma once

#include <string>
#include <vector>

#include "script_tables.h"

namespace mwruntime {

// One listed destination and what it costs this player right now.
struct Fare {
    TravelDest dest;
    int price = 0;
};

// Whether `actor` offers travel at all: it has destinations, which is
// OpenMW's own test -- TES3 has no service bit for it.
bool OffersTravel(const std::string& actor);

// TravelWindow::addDestination for every destination of `actor`.
std::vector<Fare> TravelFares(const std::string& actor);

// How many whole hours the trip to `dest` takes: none from an interior,
// which is the Mages Guild's instant transport.
int TravelHours(const TravelDest& dest);

// TravelWindow::onTravelButtonClick: pays `actor`, moves the clock on and
// sends the player and their followers. `beforeMoving` runs between the
// payment and the move, which is where the window closes. False, with nothing
// touched, when the player cannot pay.
bool TakeTrip(const std::string& actor, const Fare& fare,
              void (*beforeMoving)());

}  // namespace mwruntime
