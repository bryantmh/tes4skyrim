#include "travel.h"

#include <algorithm>
#include <cmath>

#include "dialogue_state.h"
#include "log.h"
#include "persuasion.h"

namespace tesruntime::mw {

namespace {

constexpr const char* kPlayerId = "player";

// The player's position, or the origin when the game cannot say.
void PlayerPosition(float* out) {
    for (int axis = 0; axis < 3; ++axis) {
        out[axis] = Hooks().position ? Hooks().position(kPlayerId, axis) : 0.0f;
    }
}

// Whether the conversation is happening indoors, which is where the speaker
// stands: TES3 prices and times a trip by the SPEAKER's cell.
bool SpeakerIndoors() {
    return Hooks().playerInInterior && Hooks().playerInInterior();
}

int Followers() { return Hooks().followerCount ? Hooks().followerCount() : 0; }

float Distance(const TravelDest& dest, bool flat) {
    float at[3];
    PlayerPosition(at);
    const float dx = dest.x - at[0];
    const float dy = dest.y - at[1];
    const float dz = flat ? 0.0f : dest.z - at[2];
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

// The fare before haggling: the Mages Guild's flat rate from indoors, else
// the 3D distance over fTravelMult.
int BasePrice(const TravelDest& dest) {
    if (SpeakerIndoors()) {
        return static_cast<int>(GmstNumber("fMagesGuildTravel", 0.0f));
    }
    const float mult = GmstNumber("fTravelMult", 0.0f);
    const float distance = Distance(dest, false);
    return static_cast<int>(mult != 0.0f ? distance / mult : distance);
}

}  // namespace

bool OffersTravel(const std::string& actor) {
    return FindTravel(actor) != nullptr;
}

std::vector<Fare> TravelFares(const std::string& actor) {
    std::vector<Fare> out;
    const std::vector<TravelDest>* places = FindTravel(actor);
    if (!places) return out;
    for (const TravelDest& dest : *places) {
        const int base = std::max(1, BasePrice(dest) * (1 + Followers()));
        out.push_back({dest, BarterOffer(actor, base, true)});
    }
    return out;
}

int TravelHours(const TravelDest& dest) {
    if (SpeakerIndoors()) return 0;
    const float mult = GmstNumber("fTravelTimeMult", 0.0f);
    return mult != 0.0f ? static_cast<int>(Distance(dest, true) / mult) : 0;
}

bool TakeTrip(const std::string& actor, const Fare& fare,
              void (*beforeMoving)()) {
    if (PlayerGold() < fare.price) return false;
    const int hours = TravelHours(fare.dest);
    Log("travel: %s takes the player to '%s' for %d gold, %d hour(s)",
        actor.c_str(), fare.dest.name.c_str(), fare.price, hours);
    if (Hooks().moveGold) Hooks().moveGold(kPlayerId, actor, fare.price);
    if (hours > 0 && Hooks().advanceHours) Hooks().advanceHours(hours);
    if (beforeMoving) beforeMoving();
    if (Hooks().travelTo) Hooks().travelTo(fare.dest);
    return true;
}

}  // namespace tesruntime::mw
