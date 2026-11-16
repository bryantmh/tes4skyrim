#include "stats_sheet.h"

#include <windows.h>

#include <algorithm>
#include <cmath>
#include <iterator>
#include <string>
#include <vector>

#include "activation.h"
#include "actor_stats.h"
#include "attribute_buffs.h"
#include "chargen_menu.h"
#include "conversation.h"
#include "dialogue_state.h"
#include "leveling.h"
#include "levelup_menu.h"
#include "log.h"
#include "main_thread.h"
#include "main_tick.h"
#include "menu.h"
#include "menu_layout.h"
#include "menu_widgets.h"
#include "paths.h"
#include "perks_button.h"
#include "perks_skills.h"
#include "scope.h"
#include "script_tables.h"
#include "stat_rows.h"
#include "stat_tip.h"
#include "stats_layout.h"

namespace tesruntime::mw {

namespace {

namespace sl = stats_layout;

// MorrowindRuntime.ini's [CharacterSheet]: Enabled and SkillCap are on unless
// set to 0, and Hotkey is a virtual-key code in decimal. C by default: the
// sheet opens from the perks menu, where Skyrim's controlmap.txt leaves C free
// (it binds it only in gameplay, Auto-Move, and the item menus, Item Zoom).
// See: docs/commentary/morrowind_runtime.md#character-sheet
constexpr const char* kIniName = "MorrowindRuntime.ini";
constexpr const char* kIniSection = "CharacterSheet";
constexpr int kDefaultHotkey = 'C';
constexpr int kDefaultOn = 1;
int g_hotkey = kDefaultHotkey;

// The tick's period, and how many ticks pass between reads of the skills.
constexpr int kTickMs = 33;
constexpr int kSampleEvery = 10;

// Rows the wheel moves per notch.
constexpr int kWheelRows = 3;

// The sheet's depth over the perks menu: above its 3, and under the 6 below
// which the engine names the top menu, so the perks stop taking input under it.
// See: docs/commentary/morrowind_runtime.md#perks-button
constexpr std::uint8_t kOverPerksDepth = 4;

constexpr const char* kPlayer = "player";

// Skyrim's actor values for Health, Magicka and Fatigue, and their GMST names.
constexpr const char* kDynamicValues[] = {"Health", "Magicka", "Stamina"};
constexpr const char* kDynamicGmst[][2] = {
    {"sHealth", "Health"}, {"sMagic", "Magicka"}, {"sFatigue", "Fatigue"}};

constexpr const char* kSpecializationGmst[][2] = {
    {"sSpecializationCombat", "Combat"}, {"sSpecializationMagic", "Magic"},
    {"sSpecializationStealth", "Stealth"}};

// Skyrim's 18 skills by actor value, in the three groups its own skills menu
// draws (warrior, mage, thief), under Morrowind's specialization headings.
// See: docs/commentary/morrowind_runtime.md#skyrim-skill-list
constexpr int kSkillGroups[][6] = {{6, 7, 8, 9, 10, 11},
                                   {18, 19, 20, 21, 22, 23},
                                   {12, 13, 14, 15, 16, 17}};

// The packaged table of Skyrim's own skill names and descriptions.
constexpr const char* kSkillTable = "skyrim_skills.txt";

constexpr Rect kSkillView{sl::kSkillViewX, sl::kSkillViewY, sl::kSkillViewW,
                          sl::kSkillViewH};
constexpr Rect kSkillScroll{sl::kSkillScrollX, sl::kSkillScrollY,
                            sl::kSkillScrollW, sl::kSkillScrollH};
constexpr Rect kLevelRow{sl::kLevelRowX, sl::kLevelRowY, sl::kLevelRowW, sl::kLevelRowH};
constexpr Rect kTabs[] ={{sl::kTab0X, sl::kTab0Y, sl::kTab0W, sl::kTab0H},
                          {sl::kTab1X, sl::kTab1Y, sl::kTab1W, sl::kTab1H}};
constexpr const char* kTabGmst[][2] = {{"sSkills", "Skills"}, {"sStatistics", "Statistics"}};
constexpr int kStatisticsTab = 1;

constexpr Rect kBarFill[] = {
    {sl::kBarFill0X, sl::kBarFill0Y, sl::kBarFill0W, sl::kBarFill0H},
    {sl::kBarFill1X, sl::kBarFill1Y, sl::kBarFill1W, sl::kBarFill1H},
    {sl::kBarFill2X, sl::kBarFill2Y, sl::kBarFill2W, sl::kBarFill2H}};

// Where a row's one line sits in its 18 px row, as the movie placed it.
constexpr int kTextShift = (sl::kRowH - layout::kFontPx) / 2 - layout::kTextGutter;

// One line of the skill list: a heading in the header color, or a name with
// its value. A blank line separates groups.
struct Row {
    std::string name;
    std::string value;
    bool heading = false;
    // The Skyrim skill (actor value) the row shows, for its tooltip; -1 for
    // any other row.
    int skill = -1;
    // A faction row's 0-based rank and its FACT (null when the plugin has
    // none), for its tooltip; rank -1 for any other row.
    int rank = -1;
    const FactionDef* faction = nullptr;
};

std::vector<Row> g_rows;
int g_scroll = 0;
// The right pane's tab, and the one under the pointer.
int g_tab = 0;
int g_hoverTab = -1;
bool g_captionDirty = false;
bool g_keyWasDown = false;
int g_untilSample = 0;
// The class and birthsign menus installed, so the tick serves their requests.
bool g_chargen = false;
ThumbDrag g_drag;

CustomMenu& Menu() {
    static CustomMenu menu("MorrowindStatsMenu", "morrowind_stats");
    return menu;
}

StatTip& Tip() {
    static StatTip tip(Menu());
    return tip;
}

// The attribute whose row is under the point, or -1.
int AttributeAt(double x, double y) {
    for (int i = 0; i < sl::kAttributeRows; ++i) {
        const Rect row{sl::kAttrRowX, sl::kAttrRowY + i * sl::kRowH, sl::kAttrRowW, sl::kRowH};
        if (row.Contains(x, y)) return i;
    }
    return -1;
}

std::string Path(const std::string& name, const char* property) {
    return "_root." + name + property;
}

std::string Indexed(const char* name, int index, const char* property) {
    return Path(name + std::to_string(index), property);
}

std::string Gmst(const char* const (&entry)[2]) {
    return GmstText(entry[0], entry[1]);
}

void SetText(const std::string& path, const std::string& text) {
    Menu().SetText(path.c_str(), text.c_str());
}

// ------------------------------------------------------------- the panes

// A bar's value and fill: the movie draws it full and a black cover hides
// the part past the value, as the dialogue window's disposition bar does.
void PushBar(int row) {
    const float current = Hooks().dynamicStat ? Hooks().dynamicStat(kPlayer, row) : 0;
    const float fraction = Hooks().statPercent
                               ? Hooks().statPercent(kPlayer, kDynamicValues[row])
                               : 1.0f;
    const float most = fraction > 0.001f ? current / fraction : current;
    SetText(Indexed("BarLabel", row, ".text"), Gmst(kDynamicGmst[row]));
    SetText(Indexed("BarValue", row, ".text"),
            std::to_string(static_cast<int>(std::lround(current))) + "/" +
                std::to_string(static_cast<int>(std::lround(most))));
    const Rect& fill = kBarFill[row];
    const double filled = fill.w * std::clamp(static_cast<double>(fraction), 0.0, 1.0);
    const double rest = fill.w - filled;
    const std::string cover = "BarCover" + std::to_string(row);
    Menu().SetNumber(Path(cover, "._x").c_str(), fill.x + filled);
    Menu().SetNumber(Path(cover, "._width").c_str(), std::max(rest, 1.0));
    Menu().SetNumber(Path(cover, "._visible").c_str(), rest > 0.5 ? 1 : 0);
}

// OpenMW's level, race and class box, as wide as the attribute rows. A
// custom class's name is kept short enough to fit (chargen_menu.cpp).
void PushInfo() {
    const std::string rows[][2] = {
        {GmstText("sLevel", "Level"),
         std::to_string(Hooks().playerLevel ? Hooks().playerLevel() : 1)},
        {GmstText("sRace", "Race"), Hooks().raceName ? Hooks().raceName(kPlayer) : ""},
        {GmstText("sClass", "Class"), ChosenClassText()}};
    for (int row = 0; row < sl::kInfoRows; ++row) {
        SetText(Indexed("InfoName", row, ".text"), rows[row][0]);
        SetText(Indexed("InfoValue", row, ".text"), rows[row][1]);
    }
}

void PushAttributes() {
    for (int i = 0; i < sl::kAttributeRows; ++i) {
        SetText(Indexed("AttrName", i, ".text"), AttributeName(i));
        SetText(Indexed("AttrValue", i, ".text"),
                std::to_string(static_cast<int>(ActorAttribute(kPlayer, i))));
    }
}

// The Statistics tab's rows.
void BuildStatisticRows() {
    g_rows.clear();
    for (const StatisticRow& row : StatisticRows()) g_rows.push_back({row.name, row.value, row.heading});
}

// Skyrim's skills, each group under its heading and by name, as OpenMW lays
// the stats window's skills out; then the factions the player belongs to.
// The Statistics tab's rows instead while it is the one shown.
void BuildRows() {
    if (g_tab == kStatisticsTab) {
        BuildStatisticRows();
        return;
    }
    g_rows.clear();
    for (int spec = 0; spec < kSpecializationCount; ++spec) {
        std::vector<Row> skills;
        for (int av : kSkillGroups[spec]) {
            const char* name = SkillName(av);
            const float value = name && Hooks().actorValue ? Hooks().actorValue(kPlayer, name) : 0;
            skills.push_back({SkyrimSkillName(av), std::to_string(static_cast<int>(value)),
                              false, av});
        }
        std::sort(skills.begin(), skills.end(),
                  [](const Row& a, const Row& b) { return a.name < b.name; });
        if (!g_rows.empty()) g_rows.push_back({});
        g_rows.push_back({Gmst(kSpecializationGmst[spec]), "", true});
        g_rows.insert(g_rows.end(), skills.begin(), skills.end());
    }
    bool first = true;
    for (const auto& [key, membership] : State().Factions()) {
        if (membership.rank < 0 || membership.expelled) continue;
        if (first) {
            g_rows.push_back({});
            g_rows.push_back({GmstText("sFaction", "Faction"), "", true});
            first = false;
        }
        const std::string id = SplitStateKey(key).second;
        const FactionDef* def = FindFaction(id);
        const bool ranked = def && membership.rank < static_cast<int>(def->rankNames.size());
        g_rows.push_back({def && !def->id.empty() ? def->id : id,
                          ranked ? def->rankNames[membership.rank]
                                 : std::to_string(membership.rank + 1),
                          false, -1, membership.rank, def});
    }
}

int ListRange() {
    return std::max(0, static_cast<int>(g_rows.size()) * sl::kRowH - kSkillView.h);
}

// Places the pooled row fields over the visible rows; a row only partly in
// the view is hidden, as the dialogue window's topic list does.
void PushRows() {
    const int first = g_scroll / sl::kRowH;
    for (int field = 0; field < sl::kSkillFields; ++field) {
        const int index = first + field;
        const int top = kSkillView.y + index * sl::kRowH - g_scroll;
        const bool shown = index < static_cast<int>(g_rows.size()) &&
                           top >= kSkillView.y &&
                           top + sl::kRowH <= kSkillView.y + kSkillView.h;
        const std::string name = "SkillName" + std::to_string(field);
        const std::string value = "SkillValue" + std::to_string(field);
        Menu().SetNumber(Path(name, "._visible").c_str(), shown ? 1 : 0);
        Menu().SetNumber(Path(value, "._visible").c_str(), shown ? 1 : 0);
        if (!shown) continue;
        const Row& row = g_rows[static_cast<std::size_t>(index)];
        SetText(Path(name, ".text"), row.name);
        SetText(Path(value, ".text"), row.value);
        Menu().SetNumber(Path(name, ".textColor").c_str(),
                         row.heading ? Colors().header : Colors().normal);
        Menu().SetNumber(Path(name, "._y").c_str(), top + kTextShift);
        Menu().SetNumber(Path(value, "._y").c_str(), top + kTextShift);
    }
    const int range = ListRange();
    PushScrollbar(Menu(), kSkillScroll, "_root.SkillScroll", "_root.SkillThumb",
                  range > 0, range > 0 ? static_cast<double>(g_scroll) / range : 0);
}

// The caption plate's gap around the name, once the field has measured it.
void PushCaption() {
    double width = 0;
    if (!Menu().GetNumber("_root.Title.textWidth", &width)) return;
    const double gap = width + 2 * sl::kCaptionPad;
    const double left = sl::kCaptionX + (sl::kCaptionW - gap) / 2;
    Menu().SetNumber("_root.Cover._x", left);
    Menu().SetNumber("_root.Cover._width", gap);
    Menu().SetNumber("_root.CapLeft._x", left - 2);
    Menu().SetNumber("_root.CapRight._x", left + gap);
    g_captionDirty = false;
}

void PushTabs() {
    for (int tab = 0; tab < static_cast<int>(std::size(kTabs)); ++tab) {
        const std::string field = "Tab" + std::to_string(tab);
        SetText(Path(field, ".text"), Gmst(kTabGmst[tab]));
        unsigned color = tab == g_hoverTab ? Colors().normalOver : Colors().normal;
        if (tab == g_tab) color = Colors().normalPressed;
        Menu().SetNumber(Path(field, ".textColor").c_str(), color);
    }
}

void PushAll() {
    PickColors(Menu());
    PushTabs();
    Tip().Hide();
    SetText("_root.Title.text", PlayerName());
    g_captionDirty = true;
    for (int row = 0; row < sl::kBarRows; ++row) PushBar(row);
    PushInfo();
    PushAttributes();
    BuildRows();
    g_scroll = std::clamp(g_scroll, 0, ListRange());
    PushRows();
}

void Scroll(int pixels) {
    g_scroll = std::clamp(g_scroll + pixels, 0, ListRange());
    PushRows();
}

// ------------------------------------------------------------- the input

void OnClick(double x, double y) {
    for (int tab = 0; tab < static_cast<int>(std::size(kTabs)); ++tab) {
        if (!kTabs[tab].Contains(x, y) || tab == g_tab) continue;
        g_tab = tab;
        g_scroll = 0;
        PushAll();
        return;
    }
    const int range = ListRange();
    if (!kSkillScroll.Contains(x, y) || range <= 0) return;
    const double fraction = static_cast<double>(g_scroll) / range;
    if (g_drag.Begin(kSkillScroll, x, y, fraction)) return;
    const int page = kSkillView.h / sl::kRowH;
    Scroll(sl::kRowH * ScrollClick(kSkillScroll, y, fraction, page));
}

// The list row fully in view under the point, or -1.
int RowAt(double x, double y) {
    if (!kSkillView.Contains(x, y)) return -1;
    const int index = (static_cast<int>(y) - kSkillView.y + g_scroll) / sl::kRowH;
    const int top = kSkillView.y + index * sl::kRowH - g_scroll;
    const bool shown = index < static_cast<int>(g_rows.size()) && top >= kSkillView.y &&
                       top + sl::kRowH <= kSkillView.y + kSkillView.h;
    return shown ? index : -1;
}

// The tooltip for what is under the point: an attribute, the level, or a
// skill or faction row; none while the thumb is dragged.
void HoverTip(double x, double y) {
    const bool idle = !g_drag.Active();
    const int attribute = idle ? AttributeAt(x, y) : -1;
    const bool level = idle && kLevelRow.Contains(x, y);
    const int index = idle ? RowAt(x, y) : -1;
    const Row* row = index >= 0 ? &g_rows[static_cast<std::size_t>(index)] : nullptr;
    if (attribute >= 0) {
        Tip().HoverAttribute(attribute, x, y);
    } else if (level) {
        Tip().HoverLevel(true, x, y);
    } else if (row && row->rank >= 0) {
        Tip().HoverFaction(index, row->faction, row->rank, x, y);
    } else {
        Tip().HoverSkill(row ? row->skill : -1, x, y);
    }
}

void OnHover(double x, double y) {
    int hoverTab = -1;
    for (int tab = 0; tab < static_cast<int>(std::size(kTabs)); ++tab) {
        if (kTabs[tab].Contains(x, y)) hoverTab = tab;
    }
    if (hoverTab != g_hoverTab) {
        g_hoverTab = hoverTab;
        PushTabs();
    }
    HoverTip(x, y);
    if (!g_drag.Active()) return;
    const int to = static_cast<int>(std::lround(g_drag.Fraction(y) * ListRange()));
    if (to != g_scroll) Scroll(to - g_scroll);
}

void OnWheel(double x, double y, double delta) {
    if (kSkillView.Contains(x, y) || kSkillScroll.Contains(x, y)) {
        Scroll(-static_cast<int>(delta) * kWheelRows * sl::kRowH);
    }
}

void OnTick() {
    if (g_captionDirty) PushCaption();
    Tip().Tick();
}

// ------------------------------------------------------------- the tick

// The hotkey, only while this game window has the keyboard: the key state
// is the whole desktop's.
bool HotkeyDown() {
    DWORD process = 0;
    GetWindowThreadProcessId(GetForegroundWindow(), &process);
    return process == GetCurrentProcessId() &&
           (GetAsyncKeyState(g_hotkey) & 0x8000) != 0;
}

// Out in the world with nothing open: no menu pausing the game, no
// conversation, a game loaded.
bool InGameplay() {
    return Hooks().playerInWorld && Hooks().playerInWorld() &&
           Hooks().gamePaused && !Hooks().gamePaused() && !ConversationOpen();
}

// Now and then out in the world: the skills and level are read, the TES4
// scripts' attribute globals settled, and the buffs held where the attributes
// put them -- or handed back with the sheet off. True when a level-up step is
// waiting.
bool Sample() {
    if (--g_untilSample > 0) return false;
    g_untilSample = kSampleEvery;
    if (SheetEnabled()) SampleLeveling();
    if (Hooks().syncAttributeGlobals) Hooks().syncAttributeGlobals();
    HoldAttributeBuffs();
    return SheetEnabled() && PendingLevelUps() > 0;
}

void OpenSheet(std::uint8_t depth) {
    Menu().SetDepth(depth);
    PushAll();
    Menu().Open();
}

// The hotkey or the perks menu's button toggles the window, which opens only
// over the perks -- in the world on a build that cannot see that menu; a
// script's class or birthsign menu, then a pending level-up step, open before
// anything else. Ticks at UI time: it fills and paints movies.
// See: docs/commentary/morrowind_runtime.md#movies-are-touched-at-ui-time
void Tick() {
    const bool down = HotkeyDown();
    const bool pressed = down && !g_keyWasDown && SheetEnabled();
    g_keyWasDown = down;
    if (g_chargen && TickChargen()) return;
    const bool toggled = TickPerksButton(Menu().IsOpen()) || pressed;
    if (Menu().IsOpen()) {
        if (toggled) Menu().Close();
        return;
    }
    if (LevelUpOpen()) return;
    if (PerksMenuOpen()) {
        TickPerksSkills();
        if (toggled) OpenSheet(kOverPerksDepth);
        return;
    }
    if (!InGameplay()) return;
    if (Sample()) {
        OpenLevelUp();
        return;
    }
    if (pressed && !EngineMenusQueryable()) OpenSheet(0);
}

int IniInt(const char* key, int fallback) {
    const std::string ini = SidecarDir() + kIniName;
    return static_cast<int>(GetPrivateProfileIntA(kIniSection, key, fallback, ini.c_str()));
}

}  // namespace

void InstallCharacterSheet() {
    SetSheetEnabled(IniInt("Enabled", kDefaultOn) != 0);
    if (!SheetEnabled()) {
        // Still ticking: a save made with the sheet on holds buffs to hand back.
        const bool ticking = CanPostToMainThread() && StartTick(PostToMainThread, kTickMs, Tick);
        Log("sheet: off (%s [%s] Enabled=0); attributes read 100, buffs %s", kIniName,
            kIniSection, ticking ? "released" : "NOT released");
        return;
    }
    g_hotkey = IniInt("Hotkey", kDefaultHotkey);
    LoadSkyrimSkills(SidecarDir() + kSkillTable);
    const bool stats = Menu().Install();
    const bool levelUp = InstallLevelUpMenu();
    g_chargen = InstallChargenMenus();
    const bool button = InstallPerksButton(g_hotkey);
    MenuInput input;
    input.click = OnClick;
    input.hover = OnHover;
    input.release = []() { g_drag.End(); };
    input.wheel = OnWheel;
    input.cancel = []() { Menu().Close(); };
    input.opened = PushAll;
    input.closed = []() { g_drag.End(); };
    input.tick = OnTick;
    Menu().SetInput(input);
    const bool ticking = stats && levelUp && CanPostToMainThread() &&
                         StartTick(PostToUiThread, kTickMs, Tick);
    // Only a working level-up step can raise the attribute that caps a skill.
    SetSkillCapEnabled(ticking && IniInt("SkillCap", kDefaultOn) != 0);
    Log("sheet: stats window %s, level-up %s, perks button %s, hotkey %d %s, skill cap %s",
        stats ? "ok" : "FAILED", levelUp ? "ok" : "FAILED",
        !button ? "FAILED" : EngineMenusQueryable() ? "ok" : "unavailable (hotkey opens in the world)",
        g_hotkey, ticking ? "watching" : "NOT watching", SkillCapEnabled() ? "on" : "off");
}

bool StatsSheetOpen() { return Menu().IsOpen(); }

}  // namespace tesruntime::mw
