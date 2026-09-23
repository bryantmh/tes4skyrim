#include "journal_objectives.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "addresses.h"
#include "dialogue_state.h"
#include "ids.h"
#include "log.h"

namespace mwruntime {

namespace {

constexpr const char* kFunctionName = "GetObjectiveLog";
constexpr const char* kTraceName = "JournalTrace";

// The engine's BSString: text pointer, then u16 length and u16 capacity.
struct BSString {
    char*         text = nullptr;
    std::uint16_t length = 0;
    std::uint16_t capacity = 0;
    std::uint32_t pad = 0;
};

using LookupFormFn = void* (*)(std::uint32_t formId);
using InstanceTextFn = void (*)(void* record, void* quest, BSString* out);
using SetMemberFn = bool (*)(void* objectInterface, void* object,
                             const char* name, GFxValue* value,
                             bool isDisplayObject);
using ReleaseManagedFn = void (*)(void* objectInterface, GFxValue* value,
                                  void* data);
using CreateStringFn = void (*)(void* view, GFxValue* value, const char* text);
using CreateObjectFn = void (*)(void* view, GFxValue* value,
                                const char* className, GFxValue* args,
                                UInt32 numArgs);
using CreateFunctionFn = void (*)(void* view, GFxValue* value,
                                  GFxFunctionHandler* handler, void* refCon);
using GetVariableFn = bool (*)(void* view, GFxValue* value, const char* path);
using SetVariableFn = void (*)(void* view, const char* path, GFxValue* value,
                               UInt32 flags);

std::uintptr_t g_playerSlot = 0;
std::uintptr_t g_menuManagerSlot = 0;
LookupFormFn g_lookupForm = nullptr;
InstanceTextFn g_instanceText = nullptr;
SetMemberFn g_setMember = nullptr;
ReleaseManagedFn g_releaseManaged = nullptr;

JournalLog g_journal;

// Which (objective, instance) pairs the player's array held at the last poll,
// and whether that set describes this session yet.
std::set<std::pair<void*, std::uint32_t>> g_seen;
bool g_primed = false;
std::uint32_t g_lastCount = 0;
void* g_lastTail = nullptr;

// The engine-owned text buffer the builder writes into, reused every call so
// the engine's own allocator manages it.
BSString g_text;

// The player's objective array, or null count while no player exists.
char* PlayerObjectives(std::uint32_t* count) {
    *count = 0;
    void* player = g_playerSlot ? *reinterpret_cast<void**>(g_playerSlot)
                                : nullptr;
    if (!player) return nullptr;
    *count = At<std::uint32_t>(player, ids::kOffPlayerObjectiveCount);
    return At<char*>(player, ids::kOffPlayerObjectives);
}

void* ShownObjectiveAt(char* entries, std::uint32_t i) {
    return *reinterpret_cast<void**>(entries + i * ids::kShownObjectiveSize);
}

std::uint32_t ShownInstanceAt(char* entries, std::uint32_t i) {
    return At<std::uint32_t>(entries + i * ids::kShownObjectiveSize,
                             ids::kOffShownInstance);
}

// The quest's per-instance record for `instance`, or null before the quest
// has any journal text for it.
void* InstanceRecord(void* quest, std::uint32_t instance) {
    void** records = At<void**>(quest, ids::kOffQuestInstances);
    const std::uint32_t count = At<std::uint32_t>(quest,
                                                  ids::kOffQuestInstanceCount);
    for (std::uint32_t i = 0; records && i < count; ++i) {
        if (records[i] && At<std::uint32_t>(records[i], 0) == instance) {
            return records[i];
        }
    }
    return nullptr;
}

void RecordShown(void* objective, std::uint32_t instance) {
    void* quest = At<void*>(objective, ids::kOffObjectiveQuest);
    void* record = quest ? InstanceRecord(quest, instance) : nullptr;
    if (!record) return;
    ObjectiveKey key;
    key.questFormId = At<std::uint32_t>(quest, ids::kOffFormId);
    key.objective = At<std::uint16_t>(objective, ids::kOffObjectiveIndex);
    key.instance = instance;
    StagePointer pointer;
    pointer.stage = At<std::uint16_t>(record, ids::kOffInstanceStage);
    pointer.logEntry = At<std::uint8_t>(record, ids::kOffInstanceLogEntry);
    g_journal.Record(key, pointer);
    Log("journal: %08X objective %u (instance %u) shown at stage %u "
               "entry %u", key.questFormId, key.objective, instance,
               pointer.stage, pointer.logEntry);
}

void InstallIntoOpenMenus();

// Every tick, on the game thread, paused or not: equip any open patched
// journal movie, then record objectives -- nothing to do there unless the
// array grew or its newest entry changed.
void PollJournal() {
    InstallIntoOpenMenus();
    std::uint32_t count = 0;
    char* entries = PlayerObjectives(&count);
    if (!entries) count = 0;
    void* tail = count ? ShownObjectiveAt(entries, count - 1) : nullptr;
    if (g_primed && count == g_lastCount && tail == g_lastTail) return;
    for (std::uint32_t i = 0; i < count; ++i) {
        void* objective = ShownObjectiveAt(entries, i);
        const std::uint32_t instance = ShownInstanceAt(entries, i);
        if (!objective || !g_seen.emplace(objective, instance).second) continue;
        if (g_primed) RecordShown(objective, instance);
    }
    if (!g_primed) {
        Log("journal: %u objective(s) already shown in this save", count);
    }
    g_primed = true;
    g_lastCount = count;
    g_lastTail = tail;
}

std::vector<ShownObjective> ShownObjectives() {
    std::vector<ShownObjective> shown;
    std::uint32_t count = 0;
    char* entries = PlayerObjectives(&count);
    for (std::uint32_t i = 0; entries && i < count; ++i) {
        void* objective = ShownObjectiveAt(entries, i);
        void* quest = objective ? At<void*>(objective, ids::kOffObjectiveQuest)
                                : nullptr;
        if (!quest) continue;
        ShownObjective entry;
        entry.quest = quest;
        entry.instance = ShownInstanceAt(entries, i);
        entry.state = At<std::uint32_t>(entries + i * ids::kShownObjectiveSize,
                                        ids::kOffShownState);
        entry.objective = At<std::uint16_t>(objective, ids::kOffObjectiveIndex);
        entry.questIsMisc =
            At<std::uint8_t>(quest, ids::kOffQuestType) == ids::kQuestTypeMisc;
        entry.orWithNext = (At<std::uint8_t>(objective, ids::kOffObjectiveFlags) &
                            ids::kObjectiveFlagOr) != 0;
        shown.push_back(entry);
    }
    return shown;
}

// The journal text row `row` of (`formId`, `instance`) was shown with, or ""
// with `why` naming the step that found nothing.
std::string ObjectiveLog(std::uint32_t formId, std::uint32_t instance,
                         std::size_t row, const char** why) {
    void* quest = g_lookupForm ? g_lookupForm(formId) : nullptr;
    if (!quest || At<std::uint8_t>(quest, ids::kOffFormType) !=
                      ids::kFormTypeQuest) {
        *why = "no quest has that FormID";
        return {};
    }
    const std::vector<ShownObjective> shown = ShownObjectives();
    const ShownObjective* entry = ObjectiveAtRow(shown, quest, instance, row);
    if (!entry) {
        *why = "no shown objective at that row";
        return {};
    }
    const StagePointer* pointer =
        g_journal.Find({formId, entry->objective, instance});
    if (!pointer) {
        *why = "objective shown before recording began";
        return {};
    }
    alignas(8) char record[ids::kInstanceRecordSize] = {};
    At<std::uint32_t>(record, 0) = instance;
    At<std::uint16_t>(record, ids::kOffInstanceStage) = pointer->stage;
    At<std::uint8_t>(record, ids::kOffInstanceLogEntry) = pointer->logEntry;
    g_instanceText(record, quest, &g_text);
    *why = g_text.text && *g_text.text ? "stage text" : "the stage has no text";
    Log("journal: objective %u -> stage %u entry %u", entry->objective,
        pointer->stage, pointer->logEntry);
    return g_text.text ? std::string(g_text.text) : std::string();
}

double NumberArg(const GFxFunctionArgs* args, UInt32 i) {
    if (i >= args->numArgs) return 0.0;
    const GFxValue& value = args->args[i];
    return (value.type & ids::kGfxValueTypeMask) == ids::kGfxValueNumber
               ? value.data.number
               : 0.0;
}

// One argument as the log shows it, with its Scaleform type.
std::string DescribeArg(const GFxValue& value) {
    char text[64];
    switch (value.type & ids::kGfxValueTypeMask) {
    case 0: return "undefined";
    case 1: return "null";
    case 2: return value.data.number != 0.0 ? "true" : "false";
    case ids::kGfxValueNumber:
        std::snprintf(text, sizeof(text), "%.17g", value.data.number);
        return text;
    case 4: return std::string("'") + (value.data.string ? value.data.string
                                                         : "") + "'";
    default:
        std::snprintf(text, sizeof(text), "<type %u>",
                      value.type & ids::kGfxValueTypeMask);
        return text;
    }
}

std::string DescribeArgs(const GFxFunctionArgs* args) {
    std::string out;
    for (UInt32 i = 0; i < args->numArgs; ++i) {
        out += (i ? ", " : "") + DescribeArg(args->args[i]);
    }
    return out;
}

void ReturnString(GFxFunctionArgs* args, const std::string& text) {
    if (!args->result || !args->movie) return;
    VCall<CreateStringFn>(args->movie, ids::kMovieViewCreateStringSlot)(
        args->movie, args->result, text.c_str());
}

// `GetObjectiveLog(formID, instance, row)` -> the text, or "".
class ObjectiveLogFunction : public GFxFunctionHandler {
public:
    ObjectiveLogFunction() { refCount = 1 << 30; }

    void Invoke(GFxFunctionArgs* args) override {
        const auto formId = static_cast<std::uint32_t>(NumberArg(args, 0));
        const auto instance = static_cast<std::uint32_t>(NumberArg(args, 1));
        const auto row = static_cast<std::size_t>(NumberArg(args, 2));
        const char* why = "";
        const std::string text = ObjectiveLog(formId, instance, row, &why);
        Log("journal: GetObjectiveLog(%s) -> %08X instance %u row %zu: %s, "
            "%zu char(s)", DescribeArgs(args).c_str(), formId, instance, row,
            why, text.size());
        ReturnString(args, text);
    }
};

// `JournalTrace(...)`: the patched movie reporting where it got to.
class JournalTraceFunction : public GFxFunctionHandler {
public:
    JournalTraceFunction() { refCount = 1 << 30; }

    void Invoke(GFxFunctionArgs* args) override {
        Log("journal: movie: %s", DescribeArgs(args).c_str());
    }
};

// One handler of each for every movie. Their counts start far above anything
// the movies' references could take them down to, so the engine never
// deletes them.
ObjectiveLogFunction g_function;
JournalTraceFunction g_trace;

void Release(GFxValue* value) {
    if ((value->type & ids::kGfxValueManaged) && g_releaseManaged) {
        g_releaseManaged(value->objectInterface, value, value->data.object);
    }
    *value = GFxValue();
}

bool AddFunction(void* view, GFxValue* object, const char* name,
                 GFxFunctionHandler* handler) {
    GFxValue function;
    VCall<CreateFunctionFn>(view, ids::kMovieViewCreateFunctionSlot)(
        view, &function, handler, nullptr);
    const bool set = g_setMember(object->objectInterface, object->data.object,
                                 name, &function, false);
    Release(&function);
    return set;
}

// Whether `path` in the movie holds anything other than undefined.
bool Defined(void* view, const char* path) {
    GFxValue value;
    const bool found = VCall<GetVariableFn>(
        view, ids::kMovieViewGetVariableSlot)(view, &value, path);
    const bool defined = found && (value.type & ids::kGfxValueTypeMask) != 0;
    Release(&value);
    return defined;
}

// Gives a patched journal movie `_global.MorrowindRuntime`, once.
void InstallInto(void* view) {
    if (!Defined(view, ids::kJournalPatchMarker) ||
        Defined(view, ids::kJournalFunctionsPath)) {
        return;
    }
    GFxValue object;
    VCall<CreateObjectFn>(view, ids::kMovieViewCreateObjectSlot)(
        view, &object, nullptr, nullptr, 0);
    const bool ok = AddFunction(view, &object, kFunctionName, &g_function) &&
                    AddFunction(view, &object, kTraceName, &g_trace);
    VCall<SetVariableFn>(view, ids::kMovieViewSetVariableSlot)(
        view, ids::kJournalFunctionsPath, &object, 0);
    Release(&object);
    Log("journal: %s added to a patched journal movie -- %s",
        ids::kJournalFunctionsPath, ok ? "ok" : "SetMember FAILED");
}

// Every open menu's movie. The journal's movie is not always loaded by the
// engine's own LoadMovie -- Quest Journal Overhaul loads its own -- so SKSE's
// per-movie `skse` object cannot be relied on to reach it.
void InstallIntoOpenMenus() {
    void* manager = g_menuManagerSlot
                        ? *reinterpret_cast<void**>(g_menuManagerSlot)
                        : nullptr;
    if (!manager || !g_setMember) return;
    void** menus = At<void**>(manager, ids::kOffMenuStack);
    const std::uint32_t count = At<std::uint32_t>(manager,
                                                  ids::kOffMenuStackCount);
    for (std::uint32_t i = 0; menus && i < count; ++i) {
        void* view = menus[i] ? At<void*>(menus[i], ids::kOffMenuView)
                              : nullptr;
        if (view) InstallInto(view);
    }
}

}  // namespace

JournalLog& Journal() { return g_journal; }

void ResetJournalPoll() {
    g_seen.clear();
    g_primed = false;
    g_lastCount = 0;
    g_lastTail = nullptr;
}

void InstallJournal() {
    g_playerSlot = Resolve("PlayerCharacter singleton", ids::kPlayerSingleton,
                           nullptr);
    g_menuManagerSlot = Resolve("MenuManager singleton",
                                ids::kMenuManagerSingleton, nullptr);
    g_lookupForm = reinterpret_cast<LookupFormFn>(
        Resolve("LookupFormByID", ids::kLookupFormById, nullptr));
    g_instanceText = reinterpret_cast<InstanceTextFn>(
        Resolve("quest instance text", ids::kQuestInstanceText, nullptr));
    g_setMember = reinterpret_cast<SetMemberFn>(
        Resolve("GFxValue SetMember", ids::kGfxSetMember, nullptr));
    g_releaseManaged = reinterpret_cast<ReleaseManagedFn>(
        Resolve("GFxValue ReleaseManaged", ids::kGfxReleaseManaged, nullptr));
    if (!g_playerSlot || !g_lookupForm || !g_instanceText) {
        Log("journal: NOT installed -- an engine address did not resolve, so "
            "objectives keep showing only the current journal text");
        return;
    }
    Hooks().pollJournal = PollJournal;
    Log("journal: installed; patched journal movies get %s %s",
        ids::kJournalFunctionsPath,
        g_menuManagerSlot && g_setMember ? "when they open"
                                         : "NEVER -- the menu stack or "
                                           "SetMember did not resolve");
}

}  // namespace mwruntime
