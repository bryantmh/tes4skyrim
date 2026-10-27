#include "levelup_menu.h"

#include <algorithm>
#include <string>
#include <vector>

#include "actor_stats.h"
#include "stat_tip.h"
#include "leveling.h"
#include "log.h"
#include "menu.h"
#include "menu_layout.h"
#include "menu_widgets.h"
#include "script_tables.h"
#include "stats_layout.h"

namespace tesruntime::mw {

namespace {

namespace sl = stats_layout;

constexpr const char* kPlayer = "player";
constexpr int kAttributeMax = 100;

// Where a coin sits beside a chosen attribute: 22 px left of its name, 20
// more when a multiplier is shown there (LevelupDialog::assignCoins).
constexpr int kCoinBeforeName = 22;

// The gap AutoSizedTextBox leaves between the name and its value.
constexpr int kValueGap = 4;

constexpr Rect kOk{sl::kOkX, sl::kOkY, sl::kOkW, sl::kOkH};

std::vector<int> g_spent;
int g_coins = sl::kCoins;
int g_hover = -1;
bool g_hoverOk = false;

CustomMenu& Menu() {
    static CustomMenu menu("MorrowindLevelUpMenu", "morrowind_levelup");
    return menu;
}

StatTip& Tip() {
    static StatTip tip(Menu());
    return tip;
}

std::string Field(const char* name, int index, const char* property) {
    return "_root." + std::string(name) + std::to_string(index) + property;
}

void SetText(const std::string& path, const std::string& text) {
    Menu().SetText(path.c_str(), text.c_str());
}

void SetNumber(const std::string& path, double value) {
    Menu().SetNumber(path.c_str(), value);
}

// The base the step raises: a Fortify active now is not part of it.
int Current(int attribute) {
    return static_cast<int>(ActorBaseAttribute(kPlayer, attribute));
}

bool Available(int attribute) { return Current(attribute) < kAttributeMax; }

bool Spent(int attribute) {
    return std::find(g_spent.begin(), g_spent.end(), attribute) != g_spent.end();
}

// An attribute's grid row, top-left, in stage pixels.
int RowX(int attribute) {
    return sl::kGridX + (attribute / sl::kGridPerColumn) * sl::kGridColumnStep;
}

int RowY(int attribute) {
    return sl::kGridY + (attribute % sl::kGridPerColumn) * sl::kGridRowH;
}

// The name's clickable span: from where it starts to past its value.
Rect NameRect(int attribute) {
    return {RowX(attribute) + sl::kMultiplierW, RowY(attribute), sl::kNameW,
            sl::kGridRowH};
}

// LevelupDialog::getLevelupClassImage: the class whose mix of combat, magic
// and stealth increases this level's resembles, as tenths of the total.
const char* ClassImage(int combat, int magic, int stealth) {
    const int total = combat + magic + stealth;
    if (total == 0) return "acrobat";
    const int c = combat * 10 / total, m = magic * 10 / total, s = stealth * 10 / total;
    const char* image = "acrobat";
    if (c > 7) image = "warrior";
    else if (m > 7) image = "mage";
    else if (s > 7) image = "thief";
    if (c == 7) image = "warrior";
    else if (c == 6) image = s == 1 ? "barbarian" : s == 3 ? "crusader" : "knight";
    else if (c == 5) image = s == 3 ? "scout" : "archer";
    else if (c == 4) image = "rogue";
    if (m == 7) image = "mage";
    else if (m == 6) image = c == 2 ? "sorcerer" : combat == 3 ? "healer" : "battlemage";
    else if (m == 5) image = "witchhunter";
    else if (m == 4) image = "spellsword";
    if (s == 7) image = "thief";
    else if (s == 6) image = m == 1 ? "agent" : magic == 3 ? "assassin" : "acrobat";
    else if (s == 5) image = magic == 3 ? "monk" : "pilgrim";
    else if (s == 3 && m == 3) image = "bard";
    return image;
}

void PushClassImage() {
    const std::string chosen = ClassImage(SpecializationIncreases(0),
                                          SpecializationIncreases(1),
                                          SpecializationIncreases(2));
    for (int i = 0; i < sl::kClassImageCount; ++i) {
        const std::string name = sl::kClassImages[i];
        SetNumber("_root.Class_" + name + "._visible", name == chosen ? 1 : 0);
    }
}

void PushAttribute(int attribute) {
    const int gain = AttributeGain(attribute);
    const bool open = Available(attribute);
    const std::string name = AttributeName(attribute);
    const int value = std::min(kAttributeMax, Current(attribute) + (Spent(attribute) ? gain : 0));
    SetText(Field("Mult", attribute, ".text"), open && gain > 1 ? "x" + std::to_string(gain) : "");
    SetText(Field("AttrName", attribute, ".text"), name);
    SetText(Field("AttrValue", attribute, ".text"), std::to_string(value));
    unsigned color = attribute == g_hover ? Colors().normalOver : Colors().normal;
    if (!open) color = Colors().disabled;
    SetNumber(Field("AttrName", attribute, ".textColor"), color);
    SetNumber(Field("AttrValue", attribute, "._x"),
              RowX(attribute) + sl::kMultiplierW + TextWidth(name) + kValueGap);
}

// resetCoins and assignCoins: an unspent coin waits in the coin row, a spent
// one sits beside its attribute, and coins past the count are hidden.
void PushCoins() {
    const int n = g_coins;
    const int rowLeft = sl::kCoinRowX + sl::kCoinRowW / 2 -
                        (sl::kCoinSpacing * (n - 1) + sl::kCoin * n) / 2;
    for (int i = 0; i < sl::kCoins; ++i) {
        const std::string coin = "_root.Coin" + std::to_string(i);
        SetNumber(coin + "._visible", i < n ? 1 : 0);
        double x = rowLeft + i * (sl::kCoin + sl::kCoinSpacing);
        double y = sl::kCoinRowY;
        if (i < static_cast<int>(g_spent.size())) {
            const int attribute = g_spent[static_cast<std::size_t>(i)];
            const int shift = AttributeGain(attribute) > 1 ? sl::kMultiplierW : 0;
            x = RowX(attribute) + sl::kMultiplierW - kCoinBeforeName - shift;
            y = RowY(attribute) + (sl::kGridRowH - sl::kCoin) / 2;
        }
        SetNumber(coin + "._x", x);
        SetNumber(coin + "._y", y);
    }
}

bool Ready() { return static_cast<int>(g_spent.size()) >= g_coins; }

void PushOk() {
    SetText("_root.OkCaption.text", GmstText("sOK", "OK"));
    unsigned color = g_hoverOk ? Colors().normalOver : Colors().normal;
    if (!Ready()) color = Colors().disabled;
    SetNumber("_root.OkCaption.textColor", color);
}

std::string Trimmed(std::string text) {
    while (!text.empty() && text.back() == ' ') text.pop_back();
    return text;
}

void PushAll() {
    SetText("_root.LevelText.text",
            Trimmed(GmstText("sLevelUpMenu1", "You have ascended to Level")) +
                " " + std::to_string(PendingStepLevel()));
    SetText("_root.Description.text", GmstText("sLevelUpMenu2", ""));
    for (int attribute = 0; attribute < kAttributeCount; ++attribute) {
        PushAttribute(attribute);
    }
    PushCoins();
    PushOk();
    PushClassImage();
}

int AttributeAt(double x, double y) {
    for (int attribute = 0; attribute < kAttributeCount; ++attribute) {
        if (NameRect(attribute).Contains(x, y)) return attribute;
    }
    return -1;
}

// LevelupDialog::onAttributeClicked: a spent attribute is taken back; with
// every coin spent, the last one moves to the new choice.
void Choose(int attribute) {
    const auto found = std::find(g_spent.begin(), g_spent.end(), attribute);
    if (found != g_spent.end()) {
        g_spent.erase(found);
    } else if (static_cast<int>(g_spent.size()) == g_coins) {
        g_spent.back() = attribute;
    } else {
        g_spent.push_back(attribute);
    }
}

// A click on an attribute's name; whether it could take a coin.
bool ClickAttribute(int attribute) {
    if (attribute < 0 || !Available(attribute) || g_coins <= 0) return false;
    Choose(attribute);
    PushAll();
    return true;
}

// A click on OK; whether every coin was spent, which takes the step.
bool ClickOk() {
    if (!Ready()) return false;
    CompleteLevelUp(g_spent);
    Menu().Close();
    return true;
}

void OnClick(double x, double y) {
    if (ClickAttribute(AttributeAt(x, y))) return;
    if (kOk.Contains(x, y)) ClickOk();
}

int AttributeNamed(const std::string& name) {
    for (int attribute = 0; attribute < kAttributeCount; ++attribute) {
        if (_stricmp(AttributeName(attribute).c_str(), name.c_str()) == 0) return attribute;
    }
    return -1;
}

void OnHover(double x, double y) {
    const int hover = AttributeAt(x, y);
    const bool ok = kOk.Contains(x, y);
    Tip().HoverAttribute(hover, x, y);
    if (hover == g_hover && ok == g_hoverOk) return;
    g_hover = hover;
    g_hoverOk = ok;
    PushAll();
}

// OpenMW's onOpen: as many coins as there are attributes still under 100,
// at most three, none spent.
void Reset() {
    int available = 0;
    for (int attribute = 0; attribute < kAttributeCount; ++attribute) {
        available += Available(attribute) ? 1 : 0;
    }
    g_coins = std::min(sl::kCoins, available);
    g_spent.clear();
    g_hover = -1;
    g_hoverOk = false;
}

}  // namespace

bool InstallLevelUpMenu() {
    MenuInput input;
    input.click = OnClick;
    input.hover = OnHover;
    input.opened = []() {
        PickColors(Menu());
        Tip().Hide();
        PushAll();
    };
    input.tick = []() { Tip().Tick(); };
    Menu().SetInput(input);
    return Menu().Install();
}

// No cancel: the step has to be taken, as Morrowind's own dialog has no way out.
void OpenLevelUp() {
    Reset();
    Log("levelup: step for level %d, %d coin(s)", PendingStepLevel(), g_coins);
    PushAll();
    Menu().Open();
}

bool LevelUpOpen() { return Menu().IsOpen(); }

bool PickLevelUp(const std::string& name) {
    if (!Menu().IsOpen()) return false;
    if (_stricmp(name.c_str(), GmstText("sOK", "OK").c_str()) == 0) return ClickOk();
    return ClickAttribute(AttributeNamed(name));
}

}  // namespace tesruntime::mw
