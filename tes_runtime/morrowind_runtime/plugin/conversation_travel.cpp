#include "conversation_travel.h"

#include <utility>

#include "conversation_modal.h"
#include "log.h"
#include "persuasion.h"
#include "travel.h"

namespace mwruntime {

namespace {

std::string g_actor;
std::vector<Fare> g_fares;
TravelLeaving g_onLeaving = nullptr;

void OnRow(int row) {
    if (row < 0 || row >= static_cast<int>(g_fares.size())) return;
    TakeTrip(g_actor, g_fares[static_cast<std::size_t>(row)], g_onLeaving);
}

}  // namespace

void OpenTravelModal(const std::string& actor, TravelLeaving onLeaving) {
    g_actor = actor;
    g_fares = TravelFares(actor);
    g_onLeaving = onLeaving;
    if (g_fares.empty()) return;
    const int gold = PlayerGold();
    const std::string gp = GmstText("sgp", "gp");
    std::vector<ModalRow> rows;
    for (const Fare& fare : g_fares) {
        rows.push_back({fare.dest.name + "  - " + std::to_string(fare.price) +
                            gp,
                        fare.price <= gold});
    }
    Log("travel: %s offers %zu destination(s)", actor.c_str(), rows.size());
    OpenListModal(GmstText("sTravelServiceTitle", "Travel"), std::move(rows),
                  OnRow);
}

}  // namespace mwruntime
