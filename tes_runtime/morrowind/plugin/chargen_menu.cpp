#include "chargen_menu.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "chargen_layout.h"
#include "chargen_tables.h"
#include "conversation.h"
#include "dialogue_state.h"
#include "ids.h"
#include "leveling.h"
#include "levelup_menu.h"
#include "log.h"
#include "menu.h"
#include "menu_widgets.h"
#include "script_tables.h"
#include "stat_tip.h"
#include "stats_layout.h"
#include "ui_message.h"

namespace tesruntime::mw {

namespace {

namespace cl = chargen_layout;

constexpr int kClassKind = 1;
constexpr int kSignKind = 2;
constexpr const char* kPlayer = "player";
constexpr int kFavoredCount = 2;
// The SKIL table's rows, by TES3 skill index (a TES4 game stages its own under them).
constexpr int kSkillCount = 27;

// The choice global's value for a pick the asking plugin has no entry of.
constexpr float kNotListed = -1.0f;

// The class picture a specialization falls back on, and its GMST name.
constexpr const char* kSpecImages[] = {"warrior", "mage", "thief"};
constexpr const char* kSpecGmst[][2] = {{"sSpecializationCombat", "Combat"},
                                        {"sSpecializationMagic", "Magic"},
                                        {"sSpecializationStealth", "Stealth"}};

// The keys that edit a custom class's name (Scaleform's key codes, which are
// Windows' virtual keys), and the shift bit a key event carries. Letters come
// from the character events the raised text input sends, capitals and all;
// the key's own letter stands in only until the first such event arrives.
constexpr std::uint32_t kKeyBackspace = 8;
constexpr std::uint32_t kKeyEnter = 13;
constexpr std::uint32_t kKeySpace = 32;
constexpr std::uint32_t kKeyMinus = 189;
constexpr std::uint32_t kKeyQuote = 222;
constexpr std::uint8_t kShiftHeld = 0x01;
constexpr char32_t kFirstPrintable = 0x20;
constexpr char32_t kDelete = 0x7f;
constexpr char32_t kLastLatin1 = 0xff;

// The class name must fit beside its label in the stats window's info box.
constexpr double kNameGap = 8;

constexpr Rect kClassOk{cl::kClassOkX, cl::kClassOkY, cl::kClassOkW, cl::kClassOkH};
constexpr Rect kSignOk{cl::kBirthOkX, cl::kBirthOkY, cl::kBirthOkW, cl::kBirthOkH};

CustomMenu& ClassMenu() {
    static CustomMenu menu("MorrowindClassMenu", "morrowind_class");
    return menu;
}

CustomMenu& SignMenu() {
    static CustomMenu menu("MorrowindBirthMenu", "morrowind_birth");
    return menu;
}

std::string Path(const std::string& name, const char* property) {
    return "_root." + name + property;
}

// One list of OpenMW's MW_List: whole rows, a chosen one, a hovered one, and
// a scrollbar whose thumb can be dragged.
struct ListPane {
    Rect view;
    Rect bar;
    int count = 0;
    int top = 0;
    int chosen = -1;
    int hover = -1;
    ThumbDrag drag;

    int Range() const { return std::max(0, count - cl::kListRows); }

    double Fraction() const { return Range() > 0 ? static_cast<double>(top) / Range() : 0; }

    void Scroll(int rows) { top = std::clamp(top + rows, 0, Range()); }

    int RowAt(double x, double y) const {
        if (!view.Contains(x, y)) return -1;
        const int row = top + (static_cast<int>(y) - view.y) / cl::kRowH;
        return row < count && row < top + cl::kListRows ? row : -1;
    }

    // A press on a row chooses it; on the thumb, grabs it; elsewhere on the
    // scrollbar, steps or pages.
    bool Click(double x, double y) {
        const int row = RowAt(x, y);
        if (row >= 0) {
            chosen = row;
            return true;
        }
        if (!bar.Contains(x, y) || Range() == 0) return false;
        if (!drag.Begin(bar, x, y, Fraction())) {
            Scroll(ScrollClick(bar, y, Fraction(), cl::kListRows));
        }
        return true;
    }

    // The pointer moved: a held thumb follows it.
    void Follow(double y) {
        if (drag.Active()) top = static_cast<int>(std::lround(drag.Fraction(y) * Range()));
    }

    // The wheel over the rows or the scrollbar.
    void Wheel(double x, double y, double delta) {
        if (view.Contains(x, y) || bar.Contains(x, y)) Scroll(-static_cast<int>(delta));
    }

    void Push(CustomMenu& menu, const std::vector<std::string>& names) const {
        for (int r = 0; r < cl::kListRows; ++r) {
            const int index = top + r;
            const std::string row = "Row" + std::to_string(r);
            const bool shown = index < count;
            menu.SetText(Path(row, ".text").c_str(), shown ? names[index].c_str() : "");
            unsigned color = Colors().normal;
            if (index == hover) color = Colors().normalOver;
            if (index == chosen) color = Colors().normalPressed;
            menu.SetNumber(Path(row, ".textColor").c_str(), color);
        }
        PushScrollbar(menu, bar, "_root.ListScroll", "_root.ListThumb", Range() > 0, Fraction());
    }
};

ListPane g_classes{{cl::kClassListViewX, cl::kClassListViewY, cl::kClassListViewW,
                    cl::kClassListViewH},
                   {cl::kClassListScrollX, cl::kClassListScrollY, cl::kClassListScrollW,
                    cl::kClassListScrollH}};
ListPane g_signs{{cl::kBirthListViewX, cl::kBirthListViewY, cl::kBirthListViewW,
                  cl::kBirthListViewH},
                 {cl::kBirthListScrollX, cl::kBirthListScrollY, cl::kBirthListScrollW,
                  cl::kBirthListScrollH}};

// What was asked for: the menu, and the TES4 plugin whose script asked (null
// for a TES3 script or the console). `open` once the menu has been posted open.
struct Asked {
    int kind = 0;
    const ChargenTable* table = nullptr;
    bool open = false;
};
Asked g_asked;

// Ticks a menu posted open may take to come up (about three seconds), and
// how many it has taken.
constexpr int kOpenTicks = 90;
int g_waitTicks = 0;

// A custom class's name and favored attributes, and what is hovered. Whether
// a character event has arrived this session, so keys stop typing letters.
std::string g_customName;
std::vector<int> g_picks;
int g_hoverPick = -1;
bool g_hoverOk = false;
bool g_charEvents = false;
std::string g_shownSign;
// The effect icons on the spell list's lines now, `Icon<row>_<key>`.
std::vector<std::string> g_shownIcons;

// ------------------------------------------------------------- the class menu

const std::vector<ClassRow>& Classes() { return MenuClasses(); }

bool CustomChosen() { return g_classes.chosen == static_cast<int>(Classes().size()); }

std::string CustomName() { return GmstText("sCustomClassName", "Custom Class"); }

const ClassRow* ChosenRow() {
    const int index = g_classes.chosen;
    if (index < 0 || CustomChosen()) return nullptr;
    return &Classes()[static_cast<std::size_t>(index)];
}

// How many skills `attribute` governs in each specialization.
std::vector<int> GovernedBySpecialization(int attribute) {
    std::vector<int> counts(kSpecializationCount, 0);
    for (int skill = 0; skill < kSkillCount; ++skill) {
        const SkillDef* def = FindSkill(skill);
        if (def && def->attribute == attribute && def->specialization >= 0 &&
            def->specialization < kSpecializationCount) {
            ++counts[static_cast<std::size_t>(def->specialization)];
        }
    }
    return counts;
}

// A custom class's specialization, from its favored attributes: the one most
// of the skills they govern belong to; a tie goes to the first pick's, then to
// the earlier specialization.
int CustomSpecialization() {
    std::vector<int> total(kSpecializationCount, 0);
    std::vector<int> first(kSpecializationCount, 0);
    for (std::size_t i = 0; i < g_picks.size(); ++i) {
        const std::vector<int> counts = GovernedBySpecialization(g_picks[i]);
        for (int s = 0; s < kSpecializationCount; ++s) total[s] += counts[s];
        if (i == 0) first = counts;
    }
    int best = 0;
    for (int s = 1; s < kSpecializationCount; ++s) {
        if (total[s] > total[best] || (total[s] == total[best] && first[s] > first[best])) best = s;
    }
    return best;
}

int Specialization() {
    if (CustomChosen()) return CustomSpecialization();
    const ClassRow* row = ChosenRow();
    return row ? std::clamp(row->specialization, 0, kSpecializationCount - 1) : -1;
}

int Favored(int slot) {
    if (CustomChosen()) {
        return slot < static_cast<int>(g_picks.size()) ? g_picks[static_cast<std::size_t>(slot)]
                                                       : -1;
    }
    const ClassRow* row = ChosenRow();
    return row ? row->favored[slot] : -1;
}

bool ClassReady() {
    if (g_classes.chosen < 0) return false;
    return !CustomChosen() ||
           (static_cast<int>(g_picks.size()) == kFavoredCount && !g_customName.empty());
}

// The level-up picture of the class's name, else its specialization's.
std::string ClassImage() {
    std::string key;
    const ClassRow* row = ChosenRow();
    for (char c : row ? row->name : std::string()) {
        if (std::isalnum(static_cast<unsigned char>(c))) {
            key.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
        }
    }
    for (const char* image : stats_layout::kClassImages) {
        if (key == image) return key;
    }
    const int spec = Specialization();
    return spec >= 0 ? kSpecImages[spec] : "";
}

Rect PickRect(int attribute) {
    return {cl::kPick0X + (attribute / cl::kPickPerColumn) * cl::kPickColumnStep,
            cl::kPick0Y + (attribute % cl::kPickPerColumn) * cl::kRowH, cl::kPick0W,
            cl::kPick0H};
}

void SetClassText(const std::string& field, const std::string& text) {
    ClassMenu().SetText(Path(field, ".text").c_str(), text.c_str());
}

// A custom class: its name in the edit box with the caret, as OpenMW's
// CreateClassDialog labels it, and the attributes to pick.
void PushCustom(bool custom) {
    SetClassText("NameLabel", custom ? GmstText("sName", "Name") : "");
    SetClassText("NameText", custom ? g_customName + "_" : "");
    ClassMenu().SetNumber("_root.NameBox._visible", custom ? 1 : 0);
    SetClassText("PickHeader", custom ? GmstText("sChooseClassMenu2", "Favorite Attributes:")
                                      : "");
    for (int a = 0; a < kAttributeCount; ++a) {
        const std::string pick = "Pick" + std::to_string(a);
        SetClassText(pick, custom ? AttributeName(a) : "");
        const bool picked = std::find(g_picks.begin(), g_picks.end(), a) != g_picks.end();
        unsigned color = a == g_hoverPick ? Colors().normalOver : Colors().normal;
        if (picked) color = Colors().normalPressed;
        ClassMenu().SetNumber(Path(pick, ".textColor").c_str(), color);
    }
}

void PushClass() {
    std::vector<std::string> names;
    for (const ClassRow& row : Classes()) names.push_back(row.name);
    names.push_back(CustomName());
    g_classes.count = static_cast<int>(names.size());
    g_classes.Push(ClassMenu(), names);
    const bool custom = CustomChosen();
    const int spec = Specialization();
    SetClassText("SpecHeader", GmstText("sChooseClassMenu1", "Specialization:"));
    SetClassText("SpecName", spec >= 0 ? GmstText(kSpecGmst[spec][0], kSpecGmst[spec][1]) : "");
    SetClassText("FavHeader", GmstText("sChooseClassMenu2", "Favorite Attributes:"));
    for (int slot = 0; slot < kFavoredCount; ++slot) {
        const int a = Favored(slot);
        SetClassText("Fav" + std::to_string(slot), a >= 0 && a < kAttributeCount ? AttributeName(a) : "");
    }
    const ClassRow* row = ChosenRow();
    SetClassText("Description", row ? row->description : "");
    PushCustom(custom);
    const std::string image = ClassImage();
    for (const char* name : stats_layout::kClassImages) {
        ClassMenu().SetNumber(Path(std::string("Class_") + name, "._visible").c_str(),
                              image == name ? 1 : 0);
    }
    SetClassText("OkCaption", GmstText("sOK", "OK"));
    unsigned ok = g_hoverOk ? Colors().normalOver : Colors().normal;
    if (!ClassReady()) ok = Colors().disabled;
    ClassMenu().SetNumber("_root.OkCaption.textColor", ok);
}

// LevelupDialog's coins, for two: a picked attribute is taken back; with both
// picked, the last moves to the new one.
void Pick(int attribute) {
    const auto found = std::find(g_picks.begin(), g_picks.end(), attribute);
    if (found != g_picks.end()) {
        g_picks.erase(found);
    } else if (static_cast<int>(g_picks.size()) == kFavoredCount) {
        g_picks.back() = attribute;
    } else {
        g_picks.push_back(attribute);
    }
}

// The character a key types into the name before any character event has
// come: the event's own when it carries one, else the key's letter (a capital
// with Shift), digit or mark. 0 for a key that types nothing.
char TypedChar(std::uint32_t code, char ascii, std::uint8_t mods) {
    if (ascii >= ' ' && ascii <= '~') return ascii;
    if (code >= 'A' && code <= 'Z') {
        return static_cast<char>((mods & kShiftHeld) ? code : std::tolower(static_cast<int>(code)));
    }
    if (code >= '0' && code <= '9') return static_cast<char>(code);
    if (code == kKeySpace) return ' ';
    if (code == kKeyMinus) return '-';
    if (code == kKeyQuote) return '\'';
    return 0;
}

// A Latin-1 character as UTF-8, the encoding the movie's text takes.
std::string Utf8(char32_t c) {
    if (c < 0x80) return std::string(1, static_cast<char>(c));
    return {static_cast<char>(0xc0 | (c >> 6)), static_cast<char>(0x80 | (c & 0x3f))};
}

// Adds `piece` to the custom name while the name still fits beside its label
// in the stats window; a leading space is not taken.
void AppendToName(const std::string& piece) {
    const double room = stats_layout::kAttrRowW - TextWidth(GmstText("sClass", "Class")) - kNameGap;
    if ((piece == " " && g_customName.empty()) || TextWidth(g_customName + piece) > room) return;
    g_customName += piece;
}

// Removes the name's last character, a UTF-8 sequence whole.
void EraseFromName() {
    while (!g_customName.empty()) {
        const unsigned char last = static_cast<unsigned char>(g_customName.back());
        g_customName.pop_back();
        if ((last & 0xc0) != 0x80) return;
    }
}

// ------------------------------------------------------------- the birthsign menu

const std::vector<SignRow>& Signs() { return MenuSigns(); }

void ShowSignImage(const std::string& image) {
    if (image == g_shownSign) return;
    if (!g_shownSign.empty()) {
        SignMenu().SetNumber(Path("Sign_" + g_shownSign, "._x").c_str(),
                             cl::kBirthImageX - cl::kOffstage);
    }
    if (!image.empty()) {
        SignMenu().SetNumber(Path("Sign_" + image, "._x").c_str(), cl::kBirthImageX);
    }
    g_shownSign = image;
}

// The sign's spell list as OpenMW's BirthDialog lays it out: headers in the
// header color, spell names, and each effect indented past its icon.
void PushSpellLines(const SignRow* row) {
    for (const std::string& icon : g_shownIcons) {
        SignMenu().SetNumber(Path(icon, "._x").c_str(), cl::kBirthLine0X - cl::kOffstage);
    }
    g_shownIcons.clear();
    for (int r = 0; r < cl::kBirthLines; ++r) {
        const SignLine* line = row && r < static_cast<int>(row->lines.size()) ? &row->lines[r]
                                                                               : nullptr;
        const std::string field = "Line" + std::to_string(r);
        const bool effect = line && line->kind == 'e';
        SignMenu().SetText(Path(field, ".text").c_str(), line ? line->text.c_str() : "");
        SignMenu().SetNumber(Path(field, "._x").c_str(),
                             cl::kBirthLine0X + (effect ? cl::kIconIndent : 0));
        SignMenu().SetNumber(Path(field, ".textColor").c_str(),
                             line && line->kind == 'h' ? Colors().header : Colors().normal);
        if (!effect || line->icon.empty()) continue;
        g_shownIcons.push_back("Icon" + std::to_string(r) + "_" + line->icon);
        SignMenu().SetNumber(Path(g_shownIcons.back(), "._x").c_str(),
                             cl::kBirthLine0X + cl::kIconLeft);
    }
}

void PushSign() {
    std::vector<std::string> names;
    for (const SignRow& row : Signs()) names.push_back(row.name);
    g_signs.count = static_cast<int>(names.size());
    g_signs.Push(SignMenu(), names);
    const SignRow* row = g_signs.chosen >= 0 ? &Signs()[static_cast<std::size_t>(g_signs.chosen)]
                                             : nullptr;
    PushSpellLines(row);
    ShowSignImage(row ? row->image : std::string());
    SignMenu().SetText("_root.OkCaption.text", GmstText("sOK", "OK").c_str());
    unsigned ok = g_hoverOk ? Colors().normalOver : Colors().normal;
    if (g_signs.chosen < 0) ok = Colors().disabled;
    SignMenu().SetNumber("_root.OkCaption.textColor", ok);
}

// ------------------------------------------------------------- finishing

float* Slot(const FormRef& ref) {
    return ref.plugin.empty() || !Hooks().globalSlot ? nullptr
                                                    : Hooks().globalSlot(ref.plugin, ref.formId);
}

// Answers the asking TES4 script with its own index of `name` + 1, or
// kNotListed when its plugin has no such entry; then the request clears.
void Answer(const FormRef& choice, const std::vector<std::string>& names, const std::string& name) {
    if (!g_asked.table) return;
    const int index = IndexOfName(names, name);
    if (float* slot = Slot(choice)) *slot = index >= 0 ? static_cast<float>(index + 1) : kNotListed;
    if (float* slot = Slot(g_asked.table->request)) *slot = 0.0f;
}

void FinishClass() {
    const std::string name = CustomChosen() ? g_customName : ChosenRow()->name;
    ChooseClass(name, Specialization(), Favored(0), Favored(1));
    if (g_asked.table) Answer(g_asked.table->classChoice, g_asked.table->classes, name);
    g_asked = {};
    ClassMenu().Close();
}

// The sign's spells are granted here for every game, and the last sign's
// taken back, as OpenMW rebuilds the player's spells from the new sign.
void GrantSignSpells(const SignRow& row) {
    if (!Hooks().addSpell) return;
    if (const SignRow* old = FindSignRow(ChosenBirthsign()); old && Hooks().removeSpell) {
        for (const std::string& id : old->spellIds) Hooks().removeSpell(kPlayer, id);
    }
    for (const std::string& id : row.spellIds) Hooks().addSpell(kPlayer, id);
}

void FinishSign() {
    const SignRow& row = Signs()[static_cast<std::size_t>(g_signs.chosen)];
    GrantSignSpells(row);
    ChooseBirthsign(row.name);
    if (g_asked.table) Answer(g_asked.table->signChoice, g_asked.table->signs, row.name);
    g_asked = {};
    SignMenu().Close();
}

// ------------------------------------------------------------- the input

void OnClassClick(double x, double y) {
    const int before = g_classes.chosen;
    if (g_classes.Click(x, y)) {
        if (g_classes.chosen != before) g_picks.clear();
    } else if (kClassOk.Contains(x, y) && ClassReady()) {
        FinishClass();
        return;
    } else {
        for (int a = 0; a < kAttributeCount && CustomChosen(); ++a) {
            if (PickRect(a).Contains(x, y)) Pick(a);
        }
    }
    PushClass();
}

void OnClassHover(double x, double y) {
    g_classes.Follow(y);
    g_classes.hover = g_classes.drag.Active() ? -1 : g_classes.RowAt(x, y);
    g_hoverOk = kClassOk.Contains(x, y);
    g_hoverPick = -1;
    for (int a = 0; a < kAttributeCount && CustomChosen(); ++a) {
        if (PickRect(a).Contains(x, y)) g_hoverPick = a;
    }
    PushClass();
}

// Backspace erases from the custom class's name and Enter is OK; until a
// character event has come, a key also types its own letter.
void OnClassKey(std::uint32_t code, char ascii, std::uint8_t mods) {
    if (code == kKeyEnter) {
        if (ClassReady()) FinishClass();
        return;
    }
    if (!CustomChosen()) return;
    if (code == kKeyBackspace) {
        EraseFromName();
    } else if (const char c = g_charEvents ? 0 : TypedChar(code, ascii, mods); c) {
        AppendToName(std::string(1, c));
    }
    PushClass();
}

// A character typed into the custom class's name, as it was typed.
void OnClassTyped(std::uint32_t character) {
    if (!g_charEvents) Log("chargen: character events arrive; typing takes them");
    g_charEvents = true;
    const char32_t c = static_cast<char32_t>(character);
    if (!CustomChosen() || c < kFirstPrintable || c == kDelete || c > kLastLatin1) return;
    AppendToName(Utf8(c));
    PushClass();
}

void OnSignClick(double x, double y) {
    if (!g_signs.Click(x, y) && kSignOk.Contains(x, y) && g_signs.chosen >= 0) {
        FinishSign();
        return;
    }
    PushSign();
}

void OnSignHover(double x, double y) {
    g_signs.Follow(y);
    g_signs.hover = g_signs.drag.Active() ? -1 : g_signs.RowAt(x, y);
    g_hoverOk = kSignOk.Contains(x, y);
    PushSign();
}

// The index of the row named `name` (any case) in `rows`, or -1.
template <typename Row>
int RowNamed(const std::vector<Row>& rows, const std::string& name) {
    for (std::size_t i = 0; i < rows.size(); ++i) {
        if (_stricmp(rows[i].name.c_str(), name.c_str()) == 0) return static_cast<int>(i);
    }
    return -1;
}

// The row the asking plugin's class choice global already names (a converted
// SetClass writes it before asking, as a class quiz's result), or -1.
int SuggestedClassIndex() {
    if (!g_asked.table) return -1;
    const float* slot = Slot(g_asked.table->classChoice);
    const int named = slot ? static_cast<int>(*slot) : 0;
    const std::vector<std::string>& names = g_asked.table->classes;
    if (named < 1 || named > static_cast<int>(names.size())) return -1;
    return RowNamed(Classes(), names[named - 1]);
}

// The class the script suggests, else the class the player has now, chosen
// when the menu opens again; a name the list lacks is the custom class,
// restored whole. With none yet, the first row, as OpenMW's PickClassDialog
// starts.
int ChosenClassIndex() {
    const int suggested = SuggestedClassIndex();
    if (suggested >= 0) return suggested;
    const std::string chosen = ChosenClass();
    const std::vector<ClassRow>& rows = Classes();
    const int row = RowNamed(rows, chosen);
    if (row >= 0) return row;
    if (chosen.empty()) return 0;
    g_customName = chosen;
    for (int slot = 0; slot < kFavoredCount; ++slot) {
        if (FavoredAttribute(slot) >= 0) g_picks.push_back(FavoredAttribute(slot));
    }
    return static_cast<int>(rows.size());
}

// The sign the player has now, else the first, as OpenMW's BirthDialog starts.
int ChosenSignIndex() {
    const std::vector<SignRow>& rows = Signs();
    const int row = RowNamed(rows, ChosenBirthsign());
    return row >= 0 ? row : rows.empty() ? -1 : 0;
}

// Picks the chosen row, scrolled into view.
void ChooseOnOpen(ListPane& pane, int row) {
    pane.chosen = row;
    pane.top = std::clamp(row - cl::kListRows + 1, 0, std::max(0, row));
}

// What a menu shows when it comes up, whoever opened it.
void StartMenu(int kind) {
    g_hoverOk = false;
    g_shownSign.clear();
    g_shownIcons.clear();
    if (kind == kClassKind) {
        g_picks.clear();
        g_customName.clear();
        ChooseOnOpen(g_classes, ChosenClassIndex());
    } else {
        ChooseOnOpen(g_signs, ChosenSignIndex());
    }
}

void OpenAsked() {
    g_asked.open = true;
    StartMenu(g_asked.kind);
    if (g_asked.kind == kClassKind) {
        ClassMenu().Open();
    } else {
        SignMenu().Open();
    }
    Log("chargen: %s menu opened for %s", g_asked.kind == kClassKind ? "class" : "birthsign",
        g_asked.table ? g_asked.table->plugin.c_str() : "a TES3 script");
}

bool HasRows(int kind) {
    return kind == kClassKind ? !MenuClasses().empty() : !MenuSigns().empty();
}

// A converted script's request: 1 or 2 asks; minus that is one already taken,
// which after a load no menu is open for any more.
void TakeRequest() {
    for (const ChargenTable& table : ChargenTables()) {
        float* slot = Slot(table.request);
        const int value = slot ? static_cast<int>(*slot) : 0;
        const int kind = std::abs(value);
        if (kind != kClassKind && kind != kSignKind) continue;
        const bool rows = HasRows(kind);
        *slot = rows ? static_cast<float>(-kind) : 0.0f;
        Log("chargen: %s asks for the %s menu%s", table.plugin.c_str(),
            kind == kClassKind ? "class" : "birthsign",
            rows ? "" : " -- the runtime's menu table has none, the script shows its own");
        if (!rows) continue;
        g_asked = {kind, &table, false};
        return;
    }
}

void RequestFromScript(int kind) {
    if (g_asked.kind) return;
    if (!HasRows(kind)) {
        Log("chargen: the runtime's menu table lists no %s", kind == kClassKind ? "class" : "birthsign");
        return;
    }
    g_asked = {kind, nullptr, false};
    NoteMenuOpening(kind == kClassKind ? ClassMenu().Name() : SignMenu().Name());
}

// A menu posted open that never came up (its movie failed to load): the
// request is declined, so a TES4 script shows its own pages instead.
void Decline() {
    Log("chargen: the %s menu did not open -- declined",
        g_asked.kind == kClassKind ? "class" : "birthsign");
    if (g_asked.table) {
        const FormRef& choice = g_asked.kind == kClassKind ? g_asked.table->classChoice
                                                            : g_asked.table->signChoice;
        if (float* slot = Slot(choice)) *slot = 0.0f;
        if (float* slot = Slot(g_asked.table->request)) *slot = 0.0f;
    }
    g_asked = {};
}

// A menu opened from the console (`showmenu`) starts as a script's would.
void Opened(int kind) {
    if (!g_asked.open) {
        g_asked = {kind, nullptr, true};
        StartMenu(kind);
    }
    PickColors(kind == kClassKind ? ClassMenu() : SignMenu());
    kind == kClassKind ? PushClass() : PushSign();
}

// A headless run's pick: the row named `name` in the open menu, then OK.
bool PickNamed(const std::string& name) {
    if (PickLevelUp(name)) return true;
    if (ClassMenu().IsOpen() && RowNamed(Classes(), name) >= 0) {
        g_classes.chosen = RowNamed(Classes(), name);
        g_picks.clear();
        FinishClass();
        return true;
    }
    if (SignMenu().IsOpen() && RowNamed(Signs(), name) >= 0) {
        g_signs.chosen = RowNamed(Signs(), name);
        FinishSign();
        return true;
    }
    return false;
}

}  // namespace

std::string TitleCase(const std::string& name) {
    std::string out = name;
    bool start = true;
    for (char& c : out) {
        if (start) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        start = c == ' ' || c == '-';
    }
    return out;
}

bool InstallChargenMenus() {
    MenuInput classInput;
    classInput.click = OnClassClick;
    classInput.hover = OnClassHover;
    classInput.release = []() { g_classes.drag.End(); };
    classInput.key = OnClassKey;
    classInput.typed = OnClassTyped;
    classInput.wheel = [](double x, double y, double delta) {
        g_classes.Wheel(x, y, delta);
        PushClass();
    };
    classInput.opened = []() { Opened(kClassKind); };
    classInput.closed = []() {
        g_classes.drag.End();
        g_asked = {};
    };
    ClassMenu().SetInput(classInput);
    MenuInput signInput;
    signInput.click = OnSignClick;
    signInput.hover = OnSignHover;
    signInput.release = []() { g_signs.drag.End(); };
    signInput.wheel = [](double x, double y, double delta) {
        g_signs.Wheel(x, y, delta);
        PushSign();
    };
    signInput.opened = []() { Opened(kSignKind); };
    signInput.closed = []() {
        g_signs.drag.End();
        g_asked = {};
    };
    SignMenu().SetInput(signInput);
    const bool installed = ClassMenu().Install() && SignMenu().Install();
    if (installed) Hooks().showChargenMenu = RequestFromScript;
    if (installed) Hooks().menuPick = PickNamed;
    Hooks().menuOpen = [](const std::string& name) { return EngineMenuOpen(name.c_str()); };
    Hooks().boundKeyName = BoundKeyName;
    Hooks().closeMenu = [](const std::string& name) { CloseMenuNamed(name.c_str()); };
    Log("chargen: class and birthsign menus %s; %zu class(es), %zu sign(s), %zu plugin(s) asking",
        installed ? "ok" : "FAILED", MenuClasses().size(), MenuSigns().size(),
        ChargenTables().size());
    return installed;
}

bool TickChargen() {
    if (ChargenMenuOpen()) {
        g_waitTicks = 0;
        return true;
    }
    if (!g_asked.kind) TakeRequest();
    if (!g_asked.kind) return false;
    if (g_asked.open) {
        if (++g_waitTicks > kOpenTicks) Decline();
        return true;
    }
    if (!Hooks().playerInWorld || !Hooks().playerInWorld()) return true;
    if (Hooks().gamePaused && Hooks().gamePaused()) {
        CloseMenuNamed(ids::kVanillaDialogueMenu);
        return true;
    }
    if (!ConversationOpen()) OpenAsked();
    return true;
}

bool ChargenMenuOpen() { return ClassMenu().IsOpen() || SignMenu().IsOpen(); }

}  // namespace tesruntime::mw
