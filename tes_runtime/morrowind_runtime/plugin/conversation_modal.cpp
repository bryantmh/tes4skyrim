#include "conversation_modal.h"

#include <utility>

#include "menu.h"
#include "menu_layout.h"
#include "persuasion.h"
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

bool g_open = false;
int g_hoverRow = -1;
bool g_hoverCancel = false;
int g_gold = 0;
std::string g_title;
std::vector<ModalRow> g_rows;
ModalChosen g_onChosen = nullptr;

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

int RowCount() { return static_cast<int>(g_rows.size()); }

bool RowEnabled(int row) {
    return row >= 0 && row < RowCount() && g_rows[row].enabled;
}

unsigned RowColor(int row) {
    if (!RowEnabled(row)) return layout::kColorDisabled;
    return row == g_hoverRow ? layout::kColorNormalOver : layout::kColorNormal;
}

int RowAt(double x, double y) {
    for (int row = 0; row < RowCount(); ++row) {
        if (RowRect(row).Contains(x, y)) return row;
    }
    return -1;
}

void SetVisible(const std::string& path, bool visible) {
    SetMenuNumber((path + "._visible").c_str(), visible ? 1 : 0);
}

void PushRows() {
    for (int row = 0; row < RowCount(); ++row) {
        SetMenuText(RowPath(row, ".text").c_str(), g_rows[row].text.c_str());
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

void OpenListModal(std::string title, std::vector<ModalRow> rows,
                   ModalChosen onChosen) {
    if (rows.size() > static_cast<std::size_t>(layout::kModalRows)) {
        rows.resize(layout::kModalRows);
    }
    g_open = true;
    g_title = std::move(title);
    g_rows = std::move(rows);
    g_onChosen = onChosen;
    g_gold = PlayerGold();
    g_hoverRow = -1;
    g_hoverCancel = false;
    PushListModal();
}

void CloseListModal() {
    g_open = false;
    PushListModal();
}

bool ListModalOpen() { return g_open; }

void PushListModal() {
    SetVisible(layout::kSpriteModal, g_open);
    SetVisible(layout::kFieldModalTitle, g_open);
    SetVisible(layout::kFieldModalGold, g_open);
    SetVisible(layout::kFieldModalCancel, g_open);
    for (int row = 0; row < layout::kModalRows; ++row) {
        SetVisible(RowPath(row, ""), g_open && row < RowCount());
    }
    if (!g_open) return;
    SetMenuText(Path(layout::kFieldModalTitle, ".text").c_str(),
                g_title.c_str());
    const std::string gold = GmstText("sGold", "Gold") + ": " +
                             std::to_string(g_gold);
    SetMenuText(Path(layout::kFieldModalGold, ".text").c_str(), gold.c_str());
    PushRows();
    PushCancel();
}

void ListModalHover(double x, double y) {
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

void ListModalClick(double x, double y) {
    if (kCancel.Contains(x, y)) {
        CloseListModal();
        return;
    }
    const int row = RowAt(x, y);
    if (!RowEnabled(row) || !kModal.Contains(x, y)) return;
    const ModalChosen chosen = g_onChosen;
    CloseListModal();
    if (chosen) chosen(row);
}

}  // namespace mwruntime
