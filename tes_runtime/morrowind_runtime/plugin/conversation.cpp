#include "conversation.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <memory>
#include <string>
#include <vector>

#include <components/interpreter/defines.hpp>

#include "game_actor.h"
#include "log.h"
#include "menu.h"
#include "menu_layout.h"
#include "script_context.h"
#include "session.h"

namespace mwruntime {

namespace {

struct Rect {
    int x, y, w, h;
    bool Contains(double px, double py) const {
        return px >= x && py >= y && px < x + w && py < y + h;
    }
};

constexpr Rect kHistory{layout::kHistoryX, layout::kHistoryY,
                        layout::kHistoryW, layout::kHistoryH};
constexpr Rect kHistoryScroll{layout::kHistoryScrollX, layout::kHistoryScrollY,
                              layout::kHistoryScrollW, layout::kHistoryScrollH};
constexpr Rect kTopics{layout::kTopicsX, layout::kTopicsY, layout::kTopicsW,
                       layout::kTopicsH};
constexpr Rect kTopicScroll{layout::kTopicScrollX, layout::kTopicScrollY,
                            layout::kTopicScrollW, layout::kTopicScrollH};
constexpr Rect kBye{layout::kByeX, layout::kByeY, layout::kByeW,
                    layout::kByeH};

// Pixels the topic list moves per wheel notch, and lines the history does.
constexpr int kListStep = layout::kRowHeight + 2 * layout::kRowPad;
constexpr int kWheelLines = 3;

// MW_HLine sits at the bottom of its 18 px item.
constexpr int kLineOffset = layout::kSeparatorHeight - 2;

// What one row of the topic list is.
enum class Kind { Persuasion, Separator, Topic };

struct Item {
    Kind kind;
    std::string text;

    int Height() const {
        return kind == Kind::Separator ? layout::kSeparatorHeight
                                       : layout::kRowHeight + 2 * layout::kRowPad;
    }
};

// A keyword in a reply, by character offsets into that reply's text.
struct Link {
    std::size_t begin = 0;
    std::size_t end = 0;
    std::string topic;
};

// One entry in the history pane -- a topic heading (empty for the greeting)
// and the reply under it, as OpenMW's Response -- with its keywords found
// once, when it was added.
struct Entry {
    std::string title;
    std::string text;
    std::vector<Link> links;
};

// One wrapped line of the pane's plain text.
struct Line {
    std::size_t begin;
    std::size_t end;
};

std::unique_ptr<GameActor> g_actor;
std::string g_speaker;
std::string g_speakerName;
std::string g_playerName;
std::vector<Entry> g_history;
std::vector<Item> g_items;
std::vector<std::string> g_known;

// The keyword spans and wrapped lines of the WHOLE pane, in the plain-text
// character indices the movie's TextField uses, and that plain text.
std::vector<Link> g_paneLinks;
std::vector<Line> g_paneLines;
std::string g_panePlain;

int g_listScroll = 0;
int g_hoverItem = -1;
int g_hoverLink = -1;
bool g_hoverBye = false;
bool g_scrollToEnd = false;
bool g_captionDirty = false;
bool g_open = false;

std::string Lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(),
                   [](unsigned char c) { return static_cast<char>(::tolower(c)); });
    return text;
}

bool IsWordChar(char c) {
    return std::isalnum(static_cast<unsigned char>(c)) != 0 || c == '\'';
}

bool WholeWordAt(const std::string& haystack, std::size_t pos,
                 std::size_t length) {
    if (pos > 0 && IsWordChar(haystack[pos - 1])) return false;
    const std::size_t after = pos + length;
    return after >= haystack.size() || !IsWordChar(haystack[after]);
}

bool Overlaps(const std::vector<Link>& links, std::size_t begin,
              std::size_t end) {
    for (const Link& link : links) {
        if (begin < link.end && link.begin < end) return true;
    }
    return false;
}

// Every whole-word occurrence of each topic in `text`. `topics` arrives
// longest first from MentionedTopics, so "Caius Cosades" claims its span
// before "Caius" can.
std::vector<Link> FindLinks(const std::string& text,
                            const std::vector<std::string>& topics) {
    std::vector<Link> out;
    const std::string haystack = Lower(text);
    for (const std::string& topic : topics) {
        const std::string needle = Lower(topic);
        if (needle.empty()) continue;
        std::size_t pos = haystack.find(needle);
        while (pos != std::string::npos) {
            const std::size_t end = pos + needle.size();
            if (WholeWordAt(haystack, pos, needle.size()) &&
                !Overlaps(out, pos, end)) {
                out.push_back({pos, end, topic});
            }
            pos = haystack.find(needle, pos + 1);
        }
    }
    std::sort(out.begin(), out.end(), [](const Link& a, const Link& b) {
        return a.begin < b.begin;
    });
    return out;
}

// A reply as the player reads it: "%name" and its kin replaced, keywords
// located.
Entry MakeEntry(const std::string& title, const std::string& rawText) {
    Entry entry;
    entry.title = title;
    entry.text = rawText;
    if (g_actor) {
        DialogueContext context(*g_actor, g_speakerName, g_playerName);
        entry.text = Interpreter::fixDefinesDialog(rawText, context);
        entry.links = FindLinks(entry.text,
                                MentionedTopics(entry.text, *g_actor));
    }
    return entry;
}

std::string HexColor(unsigned rgb) {
    char buf[16];
    std::snprintf(buf, sizeof(buf), "#%06X", rgb);
    return buf;
}

std::string Path(const char* base, const char* property) {
    return std::string(base) + property;
}

std::string RowPath(int row, const char* property) {
    return layout::kFieldTopic + std::to_string(row) + property;
}

// ---------------------------------------------------------------- text layout

// A glyph's width on screen at the body size, as the movie draws it.
double CharWidth(char c) {
    const int index = static_cast<unsigned char>(c) - layout::kFirstCode;
    constexpr int count = sizeof(layout::kAdvance) / sizeof(layout::kAdvance[0]);
    const int units = (index >= 0 && index < count)
                          ? layout::kAdvance[index]
                          : layout::kAdvance['?' - layout::kFirstCode];
    return units * static_cast<double>(layout::kFontPx) / layout::kFontEm;
}

// The pitch of the pane's lines. Starts at the face's ascent + descent and
// is replaced by what the field itself measures (textHeight / numLines)
// once it has laid text out, so the hit-test never drifts from the render.
double g_lineHeight = (layout::kFontAscent + layout::kFontDescent) *
                      static_cast<double>(layout::kFontPx) / layout::kFontEm;

double LineHeight() { return g_lineHeight; }

void CalibrateLineHeight() {
    double lines = 0, height = 0;
    if (!GetMenuNumber(Path(layout::kFieldHistory, ".numLines").c_str(),
                       &lines) ||
        !GetMenuNumber(Path(layout::kFieldHistory, ".textHeight").c_str(),
                       &height) ||
        lines < 1 || height <= 0) {
        return;
    }
    const double measured = height / lines;
    if (std::fabs(measured - g_lineHeight) > 0.05) {
        Log("conversation: line pitch measured %.2f px over %.0f line(s), "
            "was using %.2f", measured, lines, g_lineHeight);
        g_lineHeight = measured;
    }
    if (static_cast<std::size_t>(lines) != g_paneLines.size()) {
        Log("conversation: the movie wrapped the history into %.0f lines, "
            "this layout into %zu -- keyword hit-testing is off by that much",
            lines, g_paneLines.size());
    }
}

double SpanWidth(const std::string& text, std::size_t begin, std::size_t end) {
    double width = 0;
    for (std::size_t i = begin; i < end; ++i) width += CharWidth(text[i]);
    return width;
}

// The lines a word-wrapping field makes of `plain` in `width` pixels: a
// break at the last space when a word would overflow, mid-word when one
// word alone does, and always at a newline. A space itself may overflow.
std::vector<Line> WrapLines(const std::string& plain, double width) {
    std::vector<Line> lines;
    std::size_t begin = 0;
    std::size_t lastSpace = std::string::npos;
    double x = 0;
    for (std::size_t i = 0; i < plain.size(); ++i) {
        const char c = plain[i];
        if (c == '\n') {
            lines.push_back({begin, i + 1});
            begin = i + 1;
            lastSpace = std::string::npos;
            x = 0;
            continue;
        }
        const double w = CharWidth(c);
        if (c != ' ' && x + w > width && i > begin) {
            const std::size_t at = (lastSpace != std::string::npos &&
                                    lastSpace >= begin) ? lastSpace + 1 : i;
            lines.push_back({begin, at});
            begin = at;
            lastSpace = std::string::npos;
            x = SpanWidth(plain, begin, i);
        }
        x += w;
        if (c == ' ') lastSpace = i;
    }
    lines.push_back({begin, plain.size()});
    return lines;
}

// The character under a point in the history, from the field's own scroll
// position and the layout above, or npos when it is off the text.
std::size_t CharAt(double x, double y) {
    double scroll = 1;
    GetMenuNumber(Path(layout::kFieldHistory, ".scroll").c_str(), &scroll);
    const double localY = y - kHistory.y - layout::kTextGutter;
    const long line = static_cast<long>(std::floor(localY / LineHeight())) +
                      static_cast<long>(scroll) - 1;
    if (localY < 0 || line < 0 ||
        line >= static_cast<long>(g_paneLines.size())) {
        return std::string::npos;
    }
    const Line& row = g_paneLines[static_cast<std::size_t>(line)];
    double at = kHistory.x + layout::kTextGutter;
    for (std::size_t i = row.begin; i < row.end; ++i) {
        at += CharWidth(g_panePlain[i]);
        if (x < at) return i;
    }
    return std::string::npos;
}

// ------------------------------------------------------------------ the pane

// The HTML the pane shows, and the plain text the movie counts characters
// in. A newline becomes <br>, which the field counts as ONE character, so
// the two stay index-aligned.
struct Pane {
    std::string html;
    std::string plain;

    void Text(const std::string& text) {
        for (char c : text) {
            if (c == '\r') continue;
            if (c == '\n') {
                html += "<br>";
            } else if (c == '&') {
                html += "&amp;";
            } else if (c == '<') {
                html += "&lt;";
            } else if (c == '>') {
                html += "&gt;";
            } else {
                html += c;
            }
            plain += c;
        }
    }

    void Colored(const std::string& text, unsigned rgb) {
        html += "<font color=\"" + HexColor(rgb) + "\">";
        Text(text);
        html += "</font>";
    }
};

// One response the way Response::write lays it out: heading in the header
// colour, then the text with each keyword in the link colour, the hovered one
// lit. Each keyword's span in the pane is recorded as it is placed.
void AppendEntry(const Entry& entry, Pane* pane) {
    if (!entry.title.empty()) {
        pane->Colored(entry.title, layout::kColorHeader);
        pane->Text("\n");
    }
    std::size_t at = 0;
    for (const Link& link : entry.links) {
        pane->Text(entry.text.substr(at, link.begin - at));
        Link placed = link;
        placed.begin = pane->plain.size();
        const bool lit = static_cast<int>(g_paneLinks.size()) == g_hoverLink;
        pane->Colored(entry.text.substr(link.begin, link.end - link.begin),
                      lit ? layout::kColorLinkOver : layout::kColorLink);
        placed.end = pane->plain.size();
        g_paneLinks.push_back(placed);
        at = link.end;
    }
    pane->Text(entry.text.substr(at));
}

// Rewrites the pane. A hover change keeps the scroll where it was; a new
// reply scrolls to the end, where it is, as OpenMW's window does.
void PushHistory(bool keepScroll) {
    g_paneLinks.clear();
    Pane pane;
    for (std::size_t i = 0; i < g_history.size(); ++i) {
        if (i) pane.Text("\n\n");
        AppendEntry(g_history[i], &pane);
    }
    const std::string scrollPath = Path(layout::kFieldHistory, ".scroll");
    double scroll = 0;
    const bool had = keepScroll && GetMenuNumber(scrollPath.c_str(), &scroll);
    g_panePlain = pane.plain;
    g_paneLines = WrapLines(pane.plain,
                            kHistory.w - 2.0 * layout::kTextGutter);
    SetMenuText(Path(layout::kFieldHistory, ".htmlText").c_str(),
                pane.html.c_str());
    if (had) {
        SetMenuNumber(scrollPath.c_str(), scroll);
    } else {
        g_scrollToEnd = true;
    }
}

// --------------------------------------------------------------- scrollbars

// Shows `bar` with its `thumb` at `fraction` of the track, or hides both.
void PushScrollbar(const Rect& bar, const char* barPath, const char* thumbPath,
                   bool visible, double fraction) {
    SetMenuNumber(Path(barPath, "._visible").c_str(), visible ? 1 : 0);
    SetMenuNumber(Path(thumbPath, "._visible").c_str(), visible ? 1 : 0);
    if (!visible) return;
    const int track = bar.h - layout::kScrollTrackTop -
                      layout::kScrollTrackBottom - layout::kThumbH;
    const double top = bar.y + layout::kScrollTrackTop +
                       std::clamp(fraction, 0.0, 1.0) * track;
    SetMenuNumber(Path(thumbPath, "._y").c_str(), top);
}

void PushHistoryScrollbar() {
    double most = 0, scroll = 1;
    const bool known =
        GetMenuNumber(Path(layout::kFieldHistory, ".maxscroll").c_str(), &most) &&
        GetMenuNumber(Path(layout::kFieldHistory, ".scroll").c_str(), &scroll);
    const bool visible = known && most > 1;
    PushScrollbar(kHistoryScroll, layout::kSpriteHistoryScroll,
                  layout::kSpriteHistoryThumb, visible,
                  visible ? (scroll - 1) / (most - 1) : 0);
}

int ListHeight() {
    int total = 0;
    for (const Item& item : g_items) total += item.Height();
    return total;
}

int ListRange() { return std::max(0, ListHeight() - kTopics.h); }

void PushTopicScrollbar() {
    const int range = ListRange();
    PushScrollbar(kTopicScroll, layout::kSpriteTopicScroll,
                  layout::kSpriteTopicThumb, range > 0,
                  range > 0 ? static_cast<double>(g_listScroll) / range : 0);
}

// A click on a scrollbar: the arrows step, the track pages, relative to
// where the thumb sits. Returns the signed number of steps.
int ScrollClick(const Rect& bar, double y, double fraction, int page) {
    if (y < bar.y + layout::kScrollEnd) return -1;
    if (y >= bar.y + bar.h - layout::kScrollEnd) return 1;
    const int track = bar.h - layout::kScrollTrackTop -
                      layout::kScrollTrackBottom - layout::kThumbH;
    const double thumbTop = bar.y + layout::kScrollTrackTop + fraction * track;
    if (y < thumbTop) return -page;
    if (y >= thumbTop + layout::kThumbH) return page;
    return 0;
}

// ---------------------------------------------------------------- the list

// Lays the visible rows out: each field is moved to its item's row or hidden,
// the rule to its separator. Items that would straddle the box are hidden,
// as MyGUI clips them.
void PushTopics() {
    int y = -g_listScroll;
    int field = 0;
    bool ruled = false;
    for (std::size_t i = 0; i < g_items.size(); ++i) {
        const Item& item = g_items[i];
        if (item.kind == Kind::Separator) {
            if (y >= 0 && y + item.Height() <= kTopics.h) {
                SetMenuNumber(Path(layout::kSpriteTopicLine, "._y").c_str(),
                              kTopics.y + y + kLineOffset);
                ruled = true;
            }
            y += item.Height();
            continue;
        }
        const int top = y + layout::kRowPad;
        if (top >= 0 && top + layout::kRowHeight <= kTopics.h &&
            field < layout::kTopicFields) {
            SetMenuText(RowPath(field, ".text").c_str(), item.text.c_str());
            SetMenuNumber(RowPath(field, "._y").c_str(),
                          kTopics.y + top + layout::kRowTextShift);
            SetMenuNumber(RowPath(field, "._visible").c_str(), 1);
            SetMenuNumber(RowPath(field, ".textColor").c_str(),
                          static_cast<int>(i) == g_hoverItem
                              ? layout::kColorNormalOver
                              : layout::kColorNormal);
            ++field;
        }
        y += item.Height();
    }
    for (; field < layout::kTopicFields; ++field) {
        SetMenuNumber(RowPath(field, "._visible").c_str(), 0);
    }
    SetMenuNumber(Path(layout::kSpriteTopicLine, "._visible").c_str(),
                  ruled ? 1 : 0);
    PushTopicScrollbar();
}

// The item whose text row is under the point, or -1.
int ItemAt(double x, double y) {
    if (!kTopics.Contains(x, y)) return -1;
    double local = y - kTopics.y + g_listScroll;
    for (std::size_t i = 0; i < g_items.size(); ++i) {
        const Item& item = g_items[i];
        if (local < item.Height()) {
            const bool onText = item.kind != Kind::Separator &&
                                local >= layout::kRowPad &&
                                local < layout::kRowPad + layout::kRowHeight;
            return onText ? static_cast<int>(i) : -1;
        }
        local -= item.Height();
    }
    return -1;
}

// The rows: Persuasion for an NPC, a rule, then every topic offered.
void RebuildItems() {
    g_items.clear();
    if (g_actor && g_actor->IsNpc()) {
        g_items.push_back({Kind::Persuasion, layout::kPersuasion});
        g_items.push_back({Kind::Separator, ""});
    }
    for (const TopicEntry& topic : OfferedTopics(*g_actor, g_known)) {
        g_items.push_back({Kind::Topic, topic.id});
    }
    g_listScroll = std::min(g_listScroll, ListRange());
}

// ----------------------------------------------------------- the rest of it

void PushBye() {
    SetMenuText(Path(layout::kFieldBye, ".text").c_str(), layout::kGoodbye);
    SetMenuNumber(Path(layout::kFieldBye, ".textColor").c_str(),
                  g_hoverBye ? layout::kColorNormalOver : layout::kColorNormal);
}

// The caption plate parts around the name, once the field has measured it.
void PushCaption() {
    double width = 0;
    if (!GetMenuNumber(Path(layout::kFieldName, ".textWidth").c_str(), &width)) {
        return;
    }
    const double gap = width + 2 * layout::kCaptionPad;
    const double left = layout::kCaptionX + (layout::kCaptionW - gap) / 2;
    SetMenuNumber(Path(layout::kSpriteCover, "._x").c_str(), left);
    SetMenuNumber(Path(layout::kSpriteCover, "._width").c_str(), gap);
    SetMenuNumber(Path(layout::kSpriteCapLeft, "._x").c_str(), left - 2);
    SetMenuNumber(Path(layout::kSpriteCapRight, "._x").c_str(), left + gap);
    g_captionDirty = false;
}

void PushAll() {
    SetMenuText(Path(layout::kFieldName, ".text").c_str(),
                g_speakerName.c_str());
    g_captionDirty = true;
    const std::string disposition =
        std::to_string(g_actor ? g_actor->Disposition() : 0) + "/100";
    SetMenuText(Path(layout::kFieldDisposition, ".text").c_str(),
                disposition.c_str());
    PushHistory(false);
    PushTopics();
    PushBye();
}

void Learn(const std::vector<std::string>& topics) {
    for (const std::string& topic : topics) {
        if (std::find(g_known.begin(), g_known.end(), topic) == g_known.end()) {
            g_known.push_back(topic);
        }
    }
}

// Which keyword, if any, sits under the point.
int LinkAt(double x, double y) {
    if (g_paneLinks.empty() || !kHistory.Contains(x, y)) return -1;
    const std::size_t at = CharAt(x, y);
    if (at == std::string::npos) return -1;
    for (std::size_t i = 0; i < g_paneLinks.size(); ++i) {
        if (at >= g_paneLinks[i].begin && at < g_paneLinks[i].end) {
            return static_cast<int>(i);
        }
    }
    return -1;
}

void SelectTopic(const std::string& topic) {
    if (!g_actor) return;
    const Reply reply = Answer(topic, *g_actor, -1);
    if (reply.text.empty()) {
        Log("conversation: '%s' has no answer for '%s'", g_speaker.c_str(),
            topic.c_str());
        return;
    }
    Log("conversation: '%s' -> %zu chars, %zu keyword(s)%s", topic.c_str(),
        reply.text.size(), reply.mentioned.size(),
        reply.resultScript.empty() ? "" : ", result script (not yet run)");
    Learn({topic});
    Learn(reply.mentioned);
    g_history.push_back(MakeEntry(topic, reply.text));
    RebuildItems();
    g_hoverItem = -1;
    g_hoverLink = -1;
    PushAll();
}

void SelectItem(int index) {
    const Item& item = g_items[static_cast<std::size_t>(index)];
    if (item.kind == Kind::Topic) {
        SelectTopic(item.text);
    } else if (item.kind == Kind::Persuasion) {
        Log("conversation: persuasion is not built yet");
    }
}

void Goodbye() {
    Log("conversation: goodbye");
    CloseMenu();
}

void ScrollHistory(double lines) {
    const std::string path = Path(layout::kFieldHistory, ".scroll");
    double scroll = 0;
    if (!GetMenuNumber(path.c_str(), &scroll)) return;
    SetMenuNumber(path.c_str(), std::max(1.0, scroll + lines));
    PushHistoryScrollbar();
}

void ScrollList(int pixels) {
    g_listScroll = std::clamp(g_listScroll + pixels, 0, ListRange());
    PushTopics();
}

void OnHover(double x, double y) {
    const int item = ItemAt(x, y);
    const bool bye = kBye.Contains(x, y);
    const int link = LinkAt(x, y);
    if (item != g_hoverItem) {
        g_hoverItem = item;
        PushTopics();
    }
    if (bye != g_hoverBye) {
        g_hoverBye = bye;
        PushBye();
    }
    if (link != g_hoverLink) {
        g_hoverLink = link;
        PushHistory(true);
    }
}

void OnClick(double x, double y) {
    if (kBye.Contains(x, y)) {
        Goodbye();
        return;
    }
    const int item = ItemAt(x, y);
    if (item >= 0) {
        SelectItem(item);
        return;
    }
    const int link = LinkAt(x, y);
    if (link >= 0) {
        SelectTopic(g_paneLinks[link].topic);
        return;
    }
    if (kHistoryScroll.Contains(x, y)) {
        double most = 1, scroll = 1;
        GetMenuNumber(Path(layout::kFieldHistory, ".maxscroll").c_str(), &most);
        GetMenuNumber(Path(layout::kFieldHistory, ".scroll").c_str(), &scroll);
        const int page = static_cast<int>(kHistory.h / LineHeight());
        ScrollHistory(ScrollClick(kHistoryScroll, y,
                                  most > 1 ? (scroll - 1) / (most - 1) : 0,
                                  page));
    } else if (kTopicScroll.Contains(x, y) && ListRange() > 0) {
        ScrollList(kListStep * ScrollClick(
                                   kTopicScroll, y,
                                   static_cast<double>(g_listScroll) / ListRange(),
                                   kTopics.h / kListStep));
    }
}

void OnWheel(double x, double y, double delta) {
    if (kTopics.Contains(x, y) || kTopicScroll.Contains(x, y)) {
        ScrollList(-static_cast<int>(delta) * kListStep);
        g_hoverItem = ItemAt(x, y);
        PushTopics();
    } else if (kHistory.Contains(x, y) || kHistoryScroll.Contains(x, y)) {
        ScrollHistory(-delta * kWheelLines);
    }
}

void OnCancel() { Goodbye(); }

void OnOpened() {
    g_open = true;
    PushAll();
}

void OnClosed() {
    g_open = false;
    g_actor.reset();
    g_history.clear();
    g_items.clear();
    g_paneLinks.clear();
    g_paneLines.clear();
    g_panePlain.clear();
    g_known.clear();
    g_listScroll = 0;
    g_hoverItem = -1;
    g_hoverLink = -1;
    g_hoverBye = false;
}

// After the movie advanced its text is laid out, so the measurements that
// depend on it -- the history's end, the caption's width -- can be read.
void OnTick() {
    if (g_captionDirty) PushCaption();
    if (!g_scrollToEnd) return;
    double most = 0;
    if (!GetMenuNumber(Path(layout::kFieldHistory, ".maxscroll").c_str(),
                       &most)) {
        return;
    }
    SetMenuNumber(Path(layout::kFieldHistory, ".scroll").c_str(), most);
    g_scrollToEnd = false;
    PushHistoryScrollbar();
    CalibrateLineHeight();
}

}  // namespace

void InstallConversation() {
    MenuInput input;
    input.hover = OnHover;
    input.click = OnClick;
    input.wheel = OnWheel;
    input.cancel = OnCancel;
    input.opened = OnOpened;
    input.closed = OnClosed;
    input.tick = OnTick;
    SetMenuInput(input);
}

void BeginConversation(const char* speaker, const char* displayName,
                       const char* playerName) {
    OnClosed();
    g_speaker = speaker ? speaker : "";
    g_speakerName = displayName && *displayName ? displayName : g_speaker;
    g_playerName = playerName ? playerName : "";
    g_actor = std::make_unique<GameActor>(g_speaker);
    const Reply hello = Greet(*g_actor);
    Learn(hello.mentioned);
    RebuildItems();
    Log("conversation: greeting %s, %zu row(s), %zu stubbed answer(s)",
        hello.text.empty() ? "(none matched)" : "ok", g_items.size(),
        g_actor->Stubbed());
    if (!hello.text.empty()) g_history.push_back(MakeEntry("", hello.text));
    PushAll();
    OpenMenu();
}

bool ConversationOpen() { return g_open; }

}  // namespace mwruntime
