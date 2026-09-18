#include "conversation_persuasion.h"

#include <string>

#include "log.h"
#include "menu.h"
#include "menu_layout.h"
#include "script_tables.h"

namespace mwruntime {

namespace {

struct Rect {
    int x, y, w, h;
    bool Contains(double px, double py) const {
        return px >= x && py >= y && px < x + w && py < y + h;
    }
};

constexpr Rect kModal{layout::kModalX, layout::kModalY, layout::kModalW,
                      layout::kModalH};
constexpr Rect kCancel{layout::kModalCancelX, layout::kModalCancelY,
                       layout::kModalCancelW, layout::kModalCancelH};

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

bool g_open = false;
int g_hoverRow = -1;
bool g_hoverCancel = false;
int g_gold = 0;
PersuasionChosen g_onChosen = nullptr;

// Row `index`'s hit rect: row 0 from the layout, the rest a pitch down.
Rect RowRect(int index) {
    return {layout::kModalRowX, layout::kModalRowY + index * layout::kRowHeight,
            layout::kModalRowW, layout::kModalRowH};
}

std::string Path(const char* base, const char* property) {
    return std::string(base) + property;
}

std::string RowPath(int row, const char* property) {
    return layout::kFieldModalRow + std::to_string(row) + property;
}

bool RowEnabled(int row) {
    return g_gold >= BribeCost(static_cast<Persuasion>(row));
}

unsigned RowColor(int row) {
    if (!RowEnabled(row)) return layout::kColorDisabled;
    return row == g_hoverRow ? layout::kColorNormalOver : layout::kColorNormal;
}

int RowAt(double x, double y) {
    for (int row = 0; row < layout::kModalRows; ++row) {
        if (RowRect(row).Contains(x, y)) return row;
    }
    return -1;
}

void SetVisible(const std::string& path, bool visible) {
    SetMenuNumber((path + "._visible").c_str(), visible ? 1 : 0);
}

void PushRows() {
    for (int row = 0; row < layout::kModalRows; ++row) {
        const Label& label = kRowLabels[row];
        SetMenuText(RowPath(row, ".text").c_str(),
                    GmstText(label.gmst, label.fallback).c_str());
        SetMenuNumber(RowPath(row, ".textColor").c_str(), RowColor(row));
    }
}

void PushCancel() {
    SetMenuText(Path(layout::kFieldModalCancel, ".text").c_str(),
                GmstText("sCancel", "Cancel").c_str());
    SetMenuNumber(Path(layout::kFieldModalCancel, ".textColor").c_str(),
                  g_hoverCancel ? layout::kColorNormalOver
                                : layout::kColorNormal);
}

}  // namespace

void OpenPersuasionModal(PersuasionChosen onChosen) {
    g_open = true;
    g_onChosen = onChosen;
    g_gold = PlayerGold();
    g_hoverRow = -1;
    g_hoverCancel = false;
    Log("conversation: persuasion opened, %d gold", g_gold);
    PushPersuasionModal();
}

void ClosePersuasionModal() {
    g_open = false;
    PushPersuasionModal();
}

bool PersuasionModalOpen() { return g_open; }

void PushPersuasionModal() {
    SetVisible(layout::kSpriteModal, g_open);
    SetVisible(layout::kFieldModalTitle, g_open);
    SetVisible(layout::kFieldModalGold, g_open);
    SetVisible(layout::kFieldModalCancel, g_open);
    for (int row = 0; row < layout::kModalRows; ++row) {
        SetVisible(RowPath(row, ""), g_open);
    }
    if (!g_open) return;
    SetMenuText(Path(layout::kFieldModalTitle, ".text").c_str(),
                GmstText("sPersuasionMenuTitle", "Persuasion").c_str());
    const std::string gold = GmstText("sGold", "Gold") + ": " +
                             std::to_string(g_gold);
    SetMenuText(Path(layout::kFieldModalGold, ".text").c_str(), gold.c_str());
    PushRows();
    PushCancel();
}

void PersuasionModalHover(double x, double y) {
    const int row = RowAt(x, y);
    const bool cancel = kCancel.Contains(x, y);
    if (row != g_hoverRow) {
        g_hoverRow = row;
        PushRows();
    }
    if (cancel != g_hoverCancel) {
        g_hoverCancel = cancel;
        PushCancel();
    }
}

void PersuasionModalClick(double x, double y) {
    if (kCancel.Contains(x, y)) {
        ClosePersuasionModal();
        return;
    }
    const int row = RowAt(x, y);
    if (row < 0 || !RowEnabled(row) || !kModal.Contains(x, y)) return;
    const PersuasionChosen chosen = g_onChosen;
    ClosePersuasionModal();
    if (chosen) chosen(static_cast<Persuasion>(row));
}

}  // namespace mwruntime
