#include "conversation_persuasion.h"

#include <string>
#include <vector>

#include "conversation_modal.h"
#include "log.h"
#include "script_tables.h"

namespace tesruntime::mw {

namespace {

// The GMST each row is captioned from, and the vanilla text used only when
// a chain stages no such GMST.
struct Label {
    const char* gmst;
    const char* fallback;
};

constexpr Label kRowLabels[kPersuasionCount] = {
    {"sAdmire", "Admire"},
    {"sIntimidate", "Intimidate"},
    {"sTaunt", "Taunt"},
    {"sBribe 10 Gold", "Bribe 10 Gold"},
    {"sBribe 100 Gold", "Bribe 100 Gold"},
    {"sBribe 1000 Gold", "Bribe 1000 Gold"},
};

PersuasionChosen g_onChosen = nullptr;

void OnRow(int row) {
    if (g_onChosen) g_onChosen(static_cast<Persuasion>(row));
}

}  // namespace

void OpenPersuasionModal(PersuasionChosen onChosen) {
    g_onChosen = onChosen;
    const int gold = PlayerGold();
    std::vector<ModalRow> rows;
    for (int row = 0; row < kPersuasionCount; ++row) {
        rows.push_back({GmstText(kRowLabels[row].gmst, kRowLabels[row].fallback),
                        gold >= BribeCost(static_cast<Persuasion>(row))});
    }
    Log("conversation: persuasion opened, %d gold", gold);
    OpenListModal(GmstText("sPersuasionMenuTitle", "Persuasion"),
                  std::move(rows), OnRow);
}

}  // namespace tesruntime::mw
