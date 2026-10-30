#include "game_calls.h"
#include "conversation.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include <components/interpreter/defines.hpp>
#include <components/misc/strings/lower.hpp>
#include <components/translation/translation.hpp>
#include <openmw/mwdialogue/keywordsearch.hpp>

#include "activation.h"
#include "conversation_modal.h"
#include "conversation_persuasion.h"
#include "conversation_travel.h"
#include "crafting.h"
#include "travel.h"
#include "dialogue_state.h"
#include "game_actor.h"
#include "log.h"
#include "menu.h"
#include "menu_layout.h"
#include "menu_widgets.h"
#include "persuasion.h"
#include "scope.h"
#include "script_context.h"
#include "script_runner.h"
#include "script_tables.h"
#include "session.h"
#include "test_runner.h"

namespace tesruntime::mw {

namespace {

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
enum class Kind {
    Persuasion, Barter, Spells, Travel, Enchanting, Training, Separator, Topic
};

// ESM::NPC::Services bits, as DialogueWindow::updateTopics tests them: any
// item class lists Barter, and one bit each lists Spells, Enchanting and
// Training.
constexpr std::uint32_t kAllItems = 0x27FF;
constexpr std::uint32_t kSpells = 0x800;
constexpr std::uint32_t kTraining = 0x4000;
constexpr std::uint32_t kEnchanting = 0x10000;

// The vanilla text of the row's GMST, used only when a chain stages none.
constexpr const char* kBarterFallback = "Barter";

struct Item {
    Kind kind;
    std::string text;

    int Height() const {
        return kind == Kind::Separator ? layout::kSeparatorHeight
                                       : layout::kRowHeight + 2 * layout::kRowPad;
    }
};

// What clicking a stretch of the history does.
enum class HotKind { Topic, Choice, Goodbye };

// A clickable stretch of text, by character offsets: into its own reply
// while it belongs to an Entry, into the whole pane once placed.
struct Hot {
    std::size_t begin = 0;
    std::size_t end = 0;
    HotKind kind = HotKind::Topic;
    std::string topic;
    int choice = 0;
};

// One entry in the history pane: a reply under its topic heading (empty for
// a greeting or an answered choice), as OpenMW's Response, or a notice such
// as the journal update, as its Message.
struct Entry {
    std::string title;
    std::string text;
    bool notice = false;
};

// One wrapped line of the pane's plain text.
struct Line {
    std::size_t begin;
    std::size_t end;
};

std::unique_ptr<GameActor> g_actor;
std::string g_speaker;
// The layer (scope.h) the conversation runs in: the speaker's plugin. Every
// menu callback re-enters it, since each arrives on its own.
int g_layer = kEveryLayer;
std::string g_speakerName;
std::string g_playerName;
std::string g_lastTopic;
std::vector<Entry> g_history;
std::vector<Item> g_items;

// DialogueWindow's mKeywordSearch and mTopicLinks: the listed topics, seeded
// again whenever the list is rebuilt. A link maps a lowercased id to the
// listed topic.
MWDialogue::KeywordSearch g_keywordSearch;
std::unordered_map<std::string, std::string> g_topicLinks;

// The hot spans and wrapped lines of the WHOLE pane, in the plain-text
// character indices the movie's TextField uses, and that plain text.
std::vector<Hot> g_paneHots;
std::vector<Line> g_paneLines;
std::string g_panePlain;

int g_listScroll = 0;
int g_hoverItem = -1;
int g_hoverHot = -1;
bool g_hoverBye = false;
ThumbDrag g_drag;
bool g_scrollToEnd = false;
bool g_captionDirty = false;
bool g_open = false;
bool g_inResultScript = false;

// OpenMW: the list is dead while a choice is pending or goodbye was said;
// the button only while a choice is pending.
bool ListLocked() { return !State().choices.empty() || State().goodbye; }
bool ByeLocked() { return !State().choices.empty() && !State().goodbye; }

std::string Path(const char* base, const char* property) {
    return std::string(base) + property;
}

std::string RowPath(int row, const char* property) {
    return layout::kFieldTopic + std::to_string(row) + property;
}

// ---------------------------------------------------------------- text layout

// The pitch of the pane's lines. Starts at the face's ascent + descent and
// is replaced by what the field itself measures (textHeight / numLines)
// once it has laid text out, so the hit-test never drifts from the render.
double g_lineHeight = (layout::kFontAscent + layout::kFontDescent) *
                      static_cast<double>(layout::kFontPx) / layout::kFontEm;

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
    for (std::size_t i = begin; i < end;) width += CharWidth(NextChar(text, i));
    return width;
}

// The lines a word-wrapping field makes of `plain` in `width` pixels: a
// break at the last space when a word would overflow, mid-word when one
// word alone does, and always at a newline. A space itself may overflow.
// Lines are byte ranges that always start on a whole UTF-8 character.
std::vector<Line> WrapLines(const std::string& plain, double width) {
    std::vector<Line> lines;
    std::size_t begin = 0;
    std::size_t lastSpace = std::string::npos;
    double x = 0;
    for (std::size_t i = 0; i < plain.size();) {
        const std::size_t start = i;
        const char32_t c = NextChar(plain, i);
        if (c == U'\n') {
            lines.push_back({begin, i});
            begin = i;
            lastSpace = std::string::npos;
            x = 0;
            continue;
        }
        const double w = CharWidth(c);
        if (c != U' ' && x + w > width && start > begin) {
            const std::size_t at = (lastSpace != std::string::npos &&
                                    lastSpace >= begin) ? lastSpace + 1 : start;
            lines.push_back({begin, at});
            begin = at;
            lastSpace = std::string::npos;
            x = SpanWidth(plain, begin, start);
        }
        x += w;
        if (c == U' ') lastSpace = start;
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
    const long line = static_cast<long>(std::floor(localY / g_lineHeight)) +
                      static_cast<long>(scroll) - 1;
    if (localY < 0 || line < 0 ||
        line >= static_cast<long>(g_paneLines.size())) {
        return std::string::npos;
    }
    const Line& row = g_paneLines[static_cast<std::size_t>(line)];
    double at = kHistory.x + layout::kTextGutter;
    for (std::size_t i = row.begin; i < row.end;) {
        const std::size_t start = i;
        at += CharWidth(NextChar(g_panePlain, i));
        if (x < at) return start;
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
        html += HtmlText(text);
        for (char c : text) {
            if (c != '\r') plain += c;
        }
    }

    void Colored(const std::string& text, unsigned rgb) {
        html += "<font color=\"" + HexColor(rgb) + "\">";
        Text(text);
        html += "</font>";
    }

    // Text that can be clicked: drawn in `rgb`, or `over` while hovered, and
    // recorded with its span in the pane.
    void Clickable(const std::string& text, Hot hot, unsigned rgb,
                   unsigned over) {
        const bool lit = static_cast<int>(g_paneHots.size()) == g_hoverHot;
        hot.begin = plain.size();
        Colored(text, lit ? over : rgb);
        hot.end = plain.size();
        g_paneHots.push_back(hot);
    }
};

// One response the way Response::write lays it out: heading in the header
// colour, then the text as parseHyperText splits it, each match shown by its
// display name and linked when it is a listed topic. A notice is one colour.
void AppendEntry(const Entry& entry, Pane* pane) {
    if (entry.notice) {
        pane->Colored(entry.text, Colors().notify);
        return;
    }
    if (!entry.title.empty()) {
        pane->Colored(entry.title, Colors().header);
        pane->Text("\n");
    }
    auto pos = entry.text.begin();
    for (const auto& token :
         g_keywordSearch.parseHyperText(entry.text, Translations())) {
        pane->Text(std::string(pos, token.mBeg));
        const std::string name(token.getDisplayName());
        pos = token.mEnd;
        const auto link = g_topicLinks.find(token.mTopicId);
        if (link == g_topicLinks.end()) {
            pane->Text(name);
            continue;
        }
        pane->Clickable(name, {0, 0, HotKind::Topic, link->second, 0},
                        Colors().link, Colors().linkOver);
    }
    pane->Text(std::string(pos, entry.text.end()));
}

// What DialogueWindow::updateHistory adds under the replies: one line per
// pending choice, and "Goodbye" once the speaker has said it.
void AppendAnswers(Pane* pane) {
    std::vector<std::pair<std::string, Hot>> lines;
    for (const ChoiceLine& choice : State().choices) {
        lines.push_back({choice.text, {0, 0, HotKind::Choice, "", choice.index}});
    }
    if (State().goodbye) {
        lines.push_back({layout::kGoodbye, {0, 0, HotKind::Goodbye, "", 0}});
    }
    for (std::size_t i = 0; i < lines.size(); ++i) {
        pane->Text(i ? "\n" : "\n\n");
        pane->Clickable(lines[i].first, lines[i].second, Colors().answer,
                        Colors().answerOver);
    }
}

// Rewrites the pane. A hover change keeps the scroll where it was; a new
// reply scrolls to the end, where it is, as OpenMW's window does.
void PushHistory(bool keepScroll) {
    g_paneHots.clear();
    Pane pane;
    for (std::size_t i = 0; i < g_history.size(); ++i) {
        if (i) pane.Text("\n\n");
        AppendEntry(g_history[i], &pane);
    }
    AppendAnswers(&pane);
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

void PushHistoryScrollbar() {
    double most = 0, scroll = 1;
    const bool known =
        GetMenuNumber(Path(layout::kFieldHistory, ".maxscroll").c_str(), &most) &&
        GetMenuNumber(Path(layout::kFieldHistory, ".scroll").c_str(), &scroll);
    const bool visible = known && most > 1;
    PushScrollbar(DialogueMenu(), kHistoryScroll, layout::kSpriteHistoryScroll,
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
    PushScrollbar(DialogueMenu(), kTopicScroll, layout::kSpriteTopicScroll,
                  layout::kSpriteTopicThumb, range > 0,
                  range > 0 ? static_cast<double>(g_listScroll) / range : 0);
}

// ---------------------------------------------------------------- the list

unsigned RowColor(int item) {
    if (ListLocked()) return Colors().disabled;
    return item == g_hoverItem ? Colors().normalOver : Colors().normal;
}

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
                          RowColor(static_cast<int>(i)));
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
    if (!kTopics.Contains(x, y) || ListLocked()) return -1;
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

// World::updateDialogueGlobals: the globals the engine keeps for a guard's
// crime lines to test and print (%CrimeGoldTurnIn), refreshed when dialogue
// starts and after every answer. The fallbacks are OpenMW's defaultgmsts.
void UpdateCrimeGlobals() {
    const int bounty = static_cast<int>(PlayerCrimeLevelNow());
    const int gold = PlayerGold();
    int discount = static_cast<int>(
        bounty * GmstNumber("fCrimeGoldDiscountMult", 0.5f));
    int turnIn = static_cast<int>(
        bounty * GmstNumber("fCrimeGoldTurnInMult", 0.9f));
    if (bounty > 0) {
        discount = std::max(1, discount);
        turnIn = std::max(1, turnIn);
    }
    const auto flag = [gold](int cost) { return cost <= gold ? 1.0f : 0.0f; };
    State().SetGlobal("PCHasCrimeGold", flag(bounty));
    State().SetGlobal("PCHasGoldDiscount", flag(discount));
    State().SetGlobal("CrimeGoldDiscount", static_cast<float>(discount));
    State().SetGlobal("CrimeGoldTurnIn", static_cast<float>(turnIn));
    State().SetGlobal("PCHasTurnIn", flag(turnIn));
}

void AddRowIf(bool offered, Kind kind, const char* gmst, const char* fallback) {
    if (offered) g_items.push_back({kind, GmstText(gmst, fallback)});
}

// The rows: Persuasion for an NPC, then each service it offers in
// DialogueWindow::updateTopics' order, a rule, then every topic offered.
// Spellmaking and Repair have no row yet.
void RebuildItems() {
    UpdateCrimeGlobals();
    g_items.clear();
    if (g_actor && g_actor->IsNpc()) {
        g_items.push_back({Kind::Persuasion,
                           GmstText("sPersuasion", layout::kPersuasion)});
        const ActorDef* def = FindActor(g_speaker);
        const std::uint32_t services = def ? def->services : 0;
        AddRowIf(services & kAllItems, Kind::Barter, "sBarter", kBarterFallback);
        AddRowIf(services & kSpells, Kind::Spells, "sSpells", "Spells");
        AddRowIf(OffersTravel(g_speaker), Kind::Travel, "sTravel", "Travel");
        AddRowIf(services & kEnchanting, Kind::Enchanting, "sEnchanting",
                 "Enchanting");
        AddRowIf(services & kTraining, Kind::Training, "sServiceTrainingTitle",
                 "Training");
        g_items.push_back({Kind::Separator, ""});
    }
    // DialogueManager::getKeywords: what the speaker can answer AND the
    // player has heard of. DialogueWindow::updateTopics seeds its search
    // with each.
    g_keywordSearch.clear();
    g_topicLinks.clear();
    for (const TopicEntry& topic : OfferedTopics(*g_actor, {})) {
        if (State().KnowsTopic(topic.id)) {
            g_items.push_back({Kind::Topic, topic.id});
            const std::string id = Misc::StringUtils::lowerCase(topic.id);
            g_keywordSearch.seed(Translations().topicKeyword(topic.id), id);
            g_topicLinks[id] = topic.id;
        }
    }
    g_listScroll = std::min(g_listScroll, ListRange());
}

// ----------------------------------------------------------- the rest of it

void PushBye() {
    SetMenuText(Path(layout::kFieldBye, ".text").c_str(), layout::kGoodbye);
    unsigned color = g_hoverBye ? Colors().normalOver
                                : Colors().normal;
    if (ByeLocked()) color = Colors().disabled;
    SetMenuNumber(Path(layout::kFieldBye, ".textColor").c_str(), color);
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

// The number, and the bar filled to it: the movie draws the bar full and
// this covers the part past the value.
void PushDisposition() {
    const int value =
        std::clamp(g_actor ? g_actor->Disposition() : 0, 0, 100);
    SetMenuText(Path(layout::kFieldDisposition, ".text").c_str(),
                (std::to_string(value) + "/100").c_str());
    const double filled = layout::kDispositionFillW * value / 100.0;
    const double rest = layout::kDispositionFillW - filled;
    SetMenuNumber(Path(layout::kSpriteBarCover, "._x").c_str(),
                  layout::kDispositionFillX + filled);
    SetMenuNumber(Path(layout::kSpriteBarCover, "._width").c_str(),
                  std::max(rest, 1.0));
    SetMenuNumber(Path(layout::kSpriteBarCover, "._visible").c_str(),
                  rest > 0 ? 1 : 0);
}

void PushAll() {
    SetMenuText(Path(layout::kFieldName, ".text").c_str(),
                g_speakerName.c_str());
    g_captionDirty = true;
    PushDisposition();
    PushHistory(false);
    PushTopics();
    PushBye();
    PushListModal();
}

void Learn(const std::vector<std::string>& topics) {
    for (const std::string& topic : topics) State().LearnTopic(topic);
}

// What DialogueManager does with a chosen INFO: say it with "%name" and its
// kin replaced, run its result script, then take in whatever the script
// asked for -- notices, new topics -- and the keywords the raw text mentions.
void Deliver(const std::string& title, const Reply& reply) {
    DialogueContext context(*g_actor, g_speakerName, g_playerName);
    Entry entry;
    entry.title = title;
    entry.text = Interpreter::fixDefinesDialog(reply.text, context);
    g_history.push_back(entry);
    g_lastTopic = reply.topic;

    SetRunningTopic(reply.topic);
    g_inResultScript = true;
    RunResultScript(reply.resultScript, context);
    g_inResultScript = false;
    SetRunningTopic("");
    for (const std::string& message : State().messages) {
        Entry notice;
        notice.text = message;
        notice.notice = true;
        g_history.push_back(notice);
    }
    State().messages.clear();
    Learn(MentionedTopics(reply.text, *g_actor));
    RebuildItems();
    g_hoverItem = -1;
    g_hoverHot = -1;
}

// Which hot span, if any, sits under the point.
int HotAt(double x, double y) {
    if (g_paneHots.empty() || !kHistory.Contains(x, y)) return -1;
    const std::size_t at = CharAt(x, y);
    if (at == std::string::npos) return -1;
    for (std::size_t i = 0; i < g_paneHots.size(); ++i) {
        if (at >= g_paneHots[i].begin && at < g_paneHots[i].end) {
            return static_cast<int>(i);
        }
    }
    return -1;
}

void SelectTopic(const std::string& topic) {
    if (!g_actor || ListLocked()) return;
    const Reply reply = Answer(topic, *g_actor, -1);
    if (reply.text.empty()) {
        Log("conversation: '%s' has no answer for '%s'", g_speaker.c_str(),
            topic.c_str());
        return;
    }
    Log("conversation: topic '%s'", topic.c_str());
    Deliver(topic, reply);
    PushAll();
}

// DialogueManager::questionAnswered: the last topic is searched again with
// the choice set, and the pending choices are gone before its script runs,
// so that script may offer new ones.
void AnswerChoice(int index) {
    if (!g_actor) return;
    State().choices.clear();
    State().choice = index;
    const Reply reply = Answer(g_lastTopic, *g_actor, index);
    State().choice = -1;
    Log("conversation: choice %d on '%s'%s", index, g_lastTopic.c_str(),
        reply.text.empty() ? " -- nothing answers it" : "");
    if (!reply.text.empty()) Deliver("", reply);
    PushAll();
}

void Goodbye() {
    if (ByeLocked()) return;
    Log("conversation: goodbye");
    CloseMenu();
}

// The chosen persuasion, resolved and answered: the `Admire Success` /
// `Bribe Fail` topic under its GMST title, delivered like any other reply
// so its result script runs and the bar shows the moved disposition.
void OnPersuaded(Persuasion type) {
    if (!g_actor) return;
    const PersuasionOutcome outcome = Persuade(type);
    if (outcome.ok) {
        const Reply reply = Answer(outcome.topic, *g_actor, -1);
        if (reply.text.empty()) {
            Log("conversation: nothing answers '%s'", outcome.topic.c_str());
        } else {
            Deliver(GmstText(outcome.titleGmst, outcome.topic), reply);
        }
    }
    PushAll();
}

// DialogueManager::checkServiceRefused for a service row: true, with the
// Service Refusal line delivered, when the speaker refuses `service`.
bool Refused(int service) {
    if (!g_actor || ListLocked()) return true;
    const Reply refusal = ServiceRefusal(service, *g_actor);
    if (refusal.text.empty()) return false;
    Log("conversation: '%s' refuses service %d", g_speaker.c_str(), service);
    Deliver(GmstText("sServiceRefusal", refusal.topic), refusal);
    PushAll();
    return true;
}

// Skyrim's own `menu` on the speaker, over this one. The dialogue closes
// first: its close message is queued ahead of the posted open, so the
// speaker's menu never sits under this one.
void OpenSpeakerMenu(void (*menu)(const std::string& actor)) {
    Log("conversation: service menu with '%s'", g_speaker.c_str());
    if (!menu) return;
    const std::string speaker = g_speaker;
    CloseMenu();
    menu(speaker);
}

// DialogueWindow::onSelectListItem for sBarter and sSpells: Skyrim's barter
// menu, where a spell merchant's spells are sold as tomes.
// See: docs/commentary/morrowind_runtime.md#barter
void Barter(int service) {
    if (!Refused(service)) OpenSpeakerMenu(Hooks().showBarterMenu);
}

// DialogueWindow::onSelectListItem for sServiceTrainingTitle: Skyrim's
// training menu, on the trainer class the import gave the speaker.
// See: docs/commentary/morrowind_runtime.md#barter
void Training() {
    if (!Refused(kServiceTraining)) OpenSpeakerMenu(Hooks().showTrainingMenu);
}

// DialogueWindow::onSelectListItem for sEnchanting: Skyrim's enchanting
// menu, the player enchanting as at a table, through TESRuntime's bench.
// See: docs/commentary/tes_runtime_alchemy.md#crafting-bench
void Enchanting() {
    if (Refused(kServiceEnchanting) || !CraftingInstalled()) return;
    Log("conversation: enchanting with '%s'", g_speaker.c_str());
    CloseMenu();
    OpenBench(Bench::kEnchanting);
}

// DialogueWindow::onSelectListItem for sTravel: the destinations. The window
// closes once a fare is paid, as OpenMW leaves dialogue before it teleports.
// See: docs/commentary/morrowind_runtime.md#travel
void Travel() {
    if (!Refused(kServiceTravel)) OpenTravelModal(g_speaker, CloseMenu);
}

void SelectItem(int index) {
    const Item& item = g_items[static_cast<std::size_t>(index)];
    switch (item.kind) {
        case Kind::Topic: SelectTopic(item.text); break;
        case Kind::Persuasion: OpenPersuasionModal(OnPersuaded); break;
        case Kind::Barter: Barter(kServiceBarter); break;
        case Kind::Spells: Barter(kServiceSpells); break;
        case Kind::Travel: Travel(); break;
        case Kind::Enchanting: Enchanting(); break;
        case Kind::Training: Training(); break;
        case Kind::Separator: break;
    }
}

void SelectHot(const Hot& hot) {
    if (hot.kind == HotKind::Topic) {
        SelectTopic(hot.topic);
    } else if (hot.kind == HotKind::Choice) {
        AnswerChoice(hot.choice);
    } else {
        Goodbye();
    }
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

// Where each list's thumb sits: 0 at the top, 1 at the bottom.
double HistoryFraction() {
    double most = 1, scroll = 1;
    GetMenuNumber(Path(layout::kFieldHistory, ".maxscroll").c_str(), &most);
    GetMenuNumber(Path(layout::kFieldHistory, ".scroll").c_str(), &scroll);
    return most > 1 ? (scroll - 1) / (most - 1) : 0;
}

double TopicFraction() {
    return ListRange() > 0 ? static_cast<double>(g_listScroll) / ListRange() : 0;
}

// The held thumb follows the cursor: the history by whole lines, the topic
// list by pixels.
void DragThumb(double y) {
    const double fraction = g_drag.Fraction(y);
    if (g_drag.bar == &kHistoryScroll) {
        double most = 1;
        GetMenuNumber(Path(layout::kFieldHistory, ".maxscroll").c_str(), &most);
        SetMenuNumber(Path(layout::kFieldHistory, ".scroll").c_str(),
                      1 + std::round(fraction * (std::max(most, 1.0) - 1)));
        PushHistoryScrollbar();
        return;
    }
    ScrollList(static_cast<int>(std::lround(fraction * ListRange())) - g_listScroll);
}

void OnHover(double x, double y) {
    if (g_drag.Active()) {
        DragThumb(y);
        return;
    }
    if (ListModalOpen()) {
        ListModalHover(x, y);
        return;
    }
    const int item = ItemAt(x, y);
    const bool bye = kBye.Contains(x, y);
    const int hot = HotAt(x, y);
    if (item != g_hoverItem) {
        g_hoverItem = item;
        PushTopics();
    }
    if (bye != g_hoverBye) {
        g_hoverBye = bye;
        PushBye();
    }
    if (hot != g_hoverHot) {
        g_hoverHot = hot;
        PushHistory(true);
    }
}

void ClickScrollbars(double x, double y) {
    if (kHistoryScroll.Contains(x, y)) {
        const double fraction = HistoryFraction();
        if (g_drag.Begin(kHistoryScroll, x, y, fraction)) return;
        const int page = static_cast<int>(kHistory.h / g_lineHeight);
        ScrollHistory(ScrollClick(kHistoryScroll, y, fraction, page));
    } else if (kTopicScroll.Contains(x, y) && ListRange() > 0) {
        const double fraction = TopicFraction();
        if (g_drag.Begin(kTopicScroll, x, y, fraction)) return;
        ScrollList(kListStep *
                   ScrollClick(kTopicScroll, y, fraction, kTopics.h / kListStep));
    }
}

void OnClick(double x, double y) {
    if (ListModalOpen()) {
        ListModalClick(x, y);
        return;
    }
    if (kBye.Contains(x, y)) {
        Goodbye();
        return;
    }
    const int item = ItemAt(x, y);
    if (item >= 0) {
        SelectItem(item);
        return;
    }
    const int hot = HotAt(x, y);
    if (hot >= 0) {
        const Hot chosen = g_paneHots[static_cast<std::size_t>(hot)];
        SelectHot(chosen);
        return;
    }
    ClickScrollbars(x, y);
}

void OnWheel(double x, double y, double delta) {
    if (ListModalOpen()) return;
    if (kTopics.Contains(x, y) || kTopicScroll.Contains(x, y)) {
        ScrollList(-static_cast<int>(delta) * kListStep);
        g_hoverItem = ItemAt(x, y);
        PushTopics();
    } else if (kHistory.Contains(x, y) || kHistoryScroll.Contains(x, y)) {
        ScrollHistory(-delta * kWheelLines);
    }
}

// Escape closes the modal first, as a MyGUI modal takes it; then it is
// Goodbye.
void OnCancel() {
    if (ListModalOpen()) {
        CloseListModal();
        return;
    }
    Goodbye();
}

void OnOpened() {
    g_open = true;
    PickColors(DialogueMenu());
    PushAll();
}

void OnClosed() {
    g_open = false;
    CloseListModal();
    State().EndConversation();
    g_actor.reset();
    g_history.clear();
    g_items.clear();
    g_keywordSearch.clear();
    g_topicLinks.clear();
    g_paneHots.clear();
    g_paneLines.clear();
    g_panePlain.clear();
    g_lastTopic.clear();
    g_listScroll = 0;
    g_hoverItem = -1;
    g_hoverHot = -1;
    g_hoverBye = false;
    g_drag.End();
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
    SetTestConversation({ConversationOpen, ConversationTopics, ConversationAsk,
                         ConversationChoose, ConversationGoodbye, ConversationLastText,
                         ConversationChoices, ConversationPersuade, MenuOpening,
                         PlayerMovePending});
    MenuInput input;
    input.hover = [](double x, double y) {
        const LayerScope scope(g_layer);
        OnHover(x, y);
    };
    input.click = [](double x, double y) {
        const LayerScope scope(g_layer);
        OnClick(x, y);
    };
    input.release = []() { g_drag.End(); };
    input.wheel = [](double x, double y, double delta) {
        const LayerScope scope(g_layer);
        OnWheel(x, y, delta);
    };
    input.cancel = []() {
        const LayerScope scope(g_layer);
        OnCancel();
    };
    input.opened = []() {
        const LayerScope scope(g_layer);
        OnOpened();
    };
    input.closed = []() {
        const LayerScope scope(g_layer);
        OnClosed();
    };
    input.tick = []() {
        const LayerScope scope(g_layer);
        OnTick();
    };
    SetMenuInput(input);
}

// 🛑 Stands down when the chargen actor was CONVERTED: the player can reach
// the census office, so seeding here would hand them topics -- Caius Cosades,
// South Wall -- that conversation is supposed to earn them.
void SeedChargenTopics() {
    if (State().KnownTopicCount() > 0) return;
    if (SpeakerExists(ChargenActor())) {
        Log("conversation: chargen actor is in this world -- not seeding");
        return;
    }
    const std::vector<std::string> topics = ChargenTopics();
    for (const std::string& topic : topics) State().LearnTopic(topic);
    Log("conversation: seeded %zu starting topic(s) from chargen",
        topics.size());
}

// Runs in the CALLER's layer, which is the speaker's; the conversation it
// replaces is closed in its own.
// 🛑 A greeting begun while the window is up restarts it in place, as OpenMW's
// DialogueWindow::setPtr does: the engine ignores an open posted to an open
// menu, so reopening left the window up with the conversation marked closed.
void BeginConversation(const char* speaker, const char* displayName,
                       const char* playerName) {
    const bool wasOpen = g_open;
    {
        const LayerScope previous(g_layer);
        OnClosed();
    }
    g_layer = CurrentLayer();
    SeedChargenTopics();
    g_speaker = speaker ? speaker : "";
    State().BeginConversation(g_speaker);
    g_speakerName = displayName && *displayName ? displayName : g_speaker;
    g_playerName = playerName ? playerName : "";
    g_actor = std::make_unique<GameActor>(g_speaker);
    UpdateCrimeGlobals();
    const Reply hello = Greet(*g_actor);
    if (hello.text.empty()) {
        RebuildItems();
    } else {
        Deliver("", hello);
    }
    Log("conversation: greeting %s, %zu row(s), %zu stubbed answer(s)",
        hello.text.empty() ? "(none matched)" : "ok", g_items.size(),
        g_actor->Stubbed());
    PushAll();
    if (wasOpen) {
        g_open = true;
    } else {
        OpenMenu();
    }
}

bool ConversationOpen() { return g_open; }

std::vector<std::string> ConversationTopics() {
    std::vector<std::string> topics;
    for (const Item& item : g_items) {
        if (item.kind == Kind::Topic) topics.push_back(item.text);
    }
    return topics;
}

// Matched as the list shows it, ignoring case, since a case file names the
// topic as a player reads it.
bool ConversationAsk(const std::string& topic) {
    const LayerScope scope(g_layer);
    for (const Item& item : g_items) {
        if (item.kind != Kind::Topic || _stricmp(item.text.c_str(), topic.c_str()) != 0) {
            continue;
        }
        if (ListLocked()) return false;
        SelectTopic(item.text);
        return true;
    }
    return false;
}

bool ConversationChoose(int index) {
    const LayerScope scope(g_layer);
    for (const ChoiceLine& choice : State().choices) {
        if (choice.index != index) continue;
        AnswerChoice(index);
        return true;
    }
    return false;
}

// Named as the case file writes it: admire, intimidate, taunt, bribe10,
// bribe100 or bribe1000. A bribe the purse cannot pay is refused, as the
// modal lists it disabled.
bool ConversationPersuade(const std::string& name) {
    static const char* const kNames[kPersuasionCount] = {
        "admire", "intimidate", "taunt", "bribe10", "bribe100", "bribe1000"};
    const LayerScope scope(g_layer);
    for (int i = 0; i < kPersuasionCount; ++i) {
        const auto type = static_cast<Persuasion>(i);
        if (_stricmp(kNames[i], name.c_str()) != 0) continue;
        if (!g_actor || ListLocked() || BribeCost(type) > PlayerGold()) return false;
        OnPersuaded(type);
        return true;
    }
    return false;
}

bool ConversationGoodbye() {
    const LayerScope scope(g_layer);
    if (ByeLocked()) return false;
    Goodbye();
    return true;
}

std::string ConversationLastText() {
    return g_history.empty() ? std::string() : g_history.back().text;
}

std::vector<std::string> ConversationChoices() {
    std::vector<std::string> texts;
    for (const ChoiceLine& choice : State().choices) texts.push_back(choice.text);
    return texts;
}

bool InResultScript() { return g_inResultScript; }

}  // namespace tesruntime::mw
