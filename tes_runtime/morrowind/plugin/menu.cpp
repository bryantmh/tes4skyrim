#include "menu.h"

#include <windows.h>

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iterator>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#include "addresses.h"
#include "ids.h"
#include "log.h"
#include "scaleform_log.h"
#include "ui_message.h"

namespace tesruntime::mw {

// IMenu field offsets, read off the MessageBoxMenu constructor (0x8ec1cc on
// 1.6.659) -- the simplest single-vtable modal panel the engine ships, and the
// one SKSE's CustomMenu was itself modeled on:
//   lea  r8,   [rbx+0x10]        ; &view
//   mov  byte  [rbx+0x18], 0xa   ; depth
//   mov  dword [rbx+0x1c], 0x11  ; flags
//   mov  dword [rbx+0x20], 1     ; input context
// The menu dispatcher (0xfa3dc0 on 1.6.1170) orders the stack by the +0x18
// byte, and IMenu's own constructor defaults it to 3 and the context to 0x13.
// See: docs/commentary/morrowind_runtime.md#imenu-layout
constexpr std::size_t kMenuSize = 0xa8;
constexpr std::size_t kOffView = 0x10;
constexpr std::size_t kOffDepth = 0x18;
constexpr std::size_t kOffFlags = 0x1c;
constexpr std::size_t kOffContext = 0x20;

// Where our object keeps its window. Past the base IMenu (0x30), in the part
// of the allocation only a MessageBoxMenu's own code would touch, so no
// engine code reads or writes it on ours.
constexpr std::size_t kOffOwner = 0x40;

// Our IMenu. Laid out to match the engine's, because Register hands it to
// code that indexes those offsets directly.
struct EngineMenu {
    void*         vtable;
    std::uint8_t  pad08[kOffView - 8];
    void*         view;
    std::uint8_t  depth;
    std::uint8_t  pad19[kOffFlags - kOffDepth - 1];
    std::uint32_t flags;
    std::uint32_t context;
    std::uint8_t  pad24[kOffOwner - kOffContext - 4];
    CustomMenu*   owner;
    std::uint8_t  rest[kMenuSize - kOffOwner - sizeof(void*)];
};

static_assert(sizeof(EngineMenu) == kMenuSize,
              "the menu must be exactly the size the engine allocates");
static_assert(offsetof(EngineMenu, view) == kOffView, "view offset");
static_assert(offsetof(EngineMenu, depth) == kOffDepth, "depth offset");
static_assert(offsetof(EngineMenu, flags) == kOffFlags, "flags offset");
static_assert(offsetof(EngineMenu, context) == kOffContext, "context offset");
static_assert(offsetof(EngineMenu, owner) == kOffOwner, "owner offset");

namespace {

// Queues `menu` to open once the player's move lands; false with none in
// flight. Defined beside the hold's state, below.
bool HoldOpen(CustomMenu* menu);

// The two scalars every menu sets beside its flags, as MessageBoxMenu does:
// depth 10, which draws over the engine's own menus, and the MenuMode input
// context.
constexpr std::uint8_t  kMenuDepth = 0xa;
constexpr std::uint32_t kMenuContext = 1;

// An overlay pauses nothing, takes no cursor or input context of its own, and
// is not modal, so every event it passes on reaches the menu beneath. Depth
// 12: at 11 and up the cursor routine (0xfa5fd9) skips it and asks the menu
// beneath, and under 14 "Top Menu" clicks still reach it first.
// See: docs/commentary/morrowind_runtime.md#perks-button
constexpr std::uint32_t kOverlayFlags = 0;
constexpr std::uint32_t kNoInputContext = 0x13;
constexpr std::uint8_t  kOverlayDepth = 0xc;

// IMenu::flags, from that same constructor: 0x11, then |0x404 when no gamepad
// is enabled. We always want the cursor, so both are set unconditionally --
// the gamepad getter (id 68622) does not exist on 1.6.1170 anyway.
constexpr std::uint32_t kFlagPausesGame = 1 << 0;
constexpr std::uint32_t kFlagModal = 1 << 4;
constexpr std::uint32_t kFlagUsesCursor = 1 << 2;
constexpr std::uint32_t kFlagUpdateUsesCursor = 1 << 10;
constexpr std::uint32_t kMenuFlags = kFlagPausesGame | kFlagModal |
                                     kFlagUsesCursor | kFlagUpdateUsesCursor;

// What the base IMenu::NextFrame hands Advance as its frame-catch-up count.
constexpr std::uint32_t kAdvanceCatchUp = 2;

// The user events that Tab/Escape and the mouse wheel arrive as. The wheel
// also comes as a Scaleform event of type 4, but the engine's binding for it
// inside a menu is these two names, and they arrive reliably.
constexpr const char* kCancelEvent = "Cancel";

// Menu Mode's A (and E/Enter). The Cursor context's own A is "Click", which
// arrives as a mouse press; whichever context is higher on the stack names the
// button, so Accept clicks at the cursor too.
// See: docs/commentary/morrowind_runtime.md#controller
constexpr const char* kAcceptEvent = "Accept";
constexpr const char* kWheelUpEvent = "Zoom In";
constexpr const char* kWheelDownEvent = "Zoom Out";

// The movie's own mouse position, in stage pixels whatever the scale mode.
constexpr const char* kMouseX = "_root._xmouse";
constexpr const char* kMouseY = "_root._ymouse";

// How many windows can register. Each needs its own creator function,
// because MenuManager calls the creator with no argument.
constexpr std::size_t kMaxMenus = 6;

using SetStringFn = void (*)(void* value, const char* text);
using SetVariableFn = void (*)(void* movie, const char* path, void* value,
                               std::uint32_t flags);
using GetVariableFn = bool (*)(void* movie, void* value, const char* path);
using InvokeFn = bool (*)(void* movie, const char* path, void* result,
                          void* args, std::uint32_t count);
using AdvanceFn = float (*)(void* movie, float seconds, std::uint32_t catchUp);
using HandleEventFn = std::uint32_t (*)(void* movie, void* event);
using RenderFn = void (*)(void* movie);
using VisibleRectFn = float* (*)(void* movie, float* rect);
using IsMenuOpenFn = bool (*)(void* manager, void** name);
using FixedStringFn = void* (*)(void** out, const char* text);
using ReleaseManagedFn = void (*)(void* iface, void* value, void* data);

using CreatorFn = void* (*)();
using RegisterFn = void (*)(void* manager, const char* name, CreatorFn creator);
using LoadMovieFn = bool (*)(void* loader, void* menu, void** viewOut,
                             const char* name, int scaleMode, float bgAlpha);
using AllocFn = void* (*)(void* allocator, std::size_t size, void* tag);
using AllowTextInputFn = void (*)(void* controlMap, bool allow);

void**         g_menuManager = nullptr;
void**           g_controlMap = nullptr;
AllowTextInputFn g_allowTextInput = nullptr;
RegisterFn     g_register = nullptr;
LoadMovieFn    g_loadMovie = nullptr;
void**         g_gfxLoader = nullptr;
void**         g_allocator = nullptr;
SetStringFn    g_setString = nullptr;
bool           g_resolved = false;

// The vtable every window's object carries. Only the slots the engine calls
// on a simple menu are implemented; the rest return without touching anything.
void* g_vtable[16] = {nullptr};

// The windows that registered, by the creator slot they took.
CustomMenu* g_menus[kMaxMenus] = {nullptr};
std::size_t g_menuCount = 0;

// Scaleform event types seen so far, so the log names each kind once.
std::uint32_t g_seenEvents[32] = {0};
std::size_t g_seenCount = 0;

// The first few clicks, with the event's own viewport coordinates beside
// the movie's answer, so a wrong mapping shows in the log as numbers.
std::size_t g_clicksLogged = 0;

// A GFxValue on the stack, typed as a number.
struct alignas(8) NumberValue {
    char raw[ids::kGfxValueSize];

    explicit NumberValue(double number = 0.0) {
        std::memset(raw, 0, sizeof(raw));
        *reinterpret_cast<std::uint32_t*>(raw + ids::kGfxValueTypeOffset) =
            ids::kGfxValueNumber;
        *reinterpret_cast<double*>(raw + ids::kGfxValueDataOffset) = number;
    }

    bool IsNumber() const {
        const std::uint32_t type = *reinterpret_cast<const std::uint32_t*>(
            raw + ids::kGfxValueTypeOffset);
        return (type & ids::kGfxValueTypeMask) == ids::kGfxValueNumber;
    }

    double Number() const {
        return *reinterpret_cast<const double*>(raw + ids::kGfxValueDataOffset);
    }
};

// The scalars the engine indexes while the menu is on its stack, written on
// every open because the engine owns the object between them.
// VR's IMenu is 0x40 bytes: its base constructor (0xf2a300 on 1.4.15) also
// sets +0x30 to -1 (the int its slot 9 writes) and +0x34 to 1, and its
// MessageBoxMenu sets depth 0xb and flags 0x40013 -- no cursor bits, since
// VR points with the controllers. Ours is armed exactly as that menu is.
// See: docs/reference/address_library_formats.md#pre-ae-tables
constexpr std::uint8_t  kVrMenuDepth = 0xb;
constexpr std::uint32_t kVrMenuFlags = 0x40013;
constexpr std::size_t   kVrOffMenuSlot = 0x30;
constexpr std::size_t   kVrOffMenuShown = 0x34;

// `depth` 0 keeps the kind's own; an overlay is armed as one on every build.
void ArmMenu(EngineMenu* menu, bool overlay, std::uint8_t depth) {
    const std::uint8_t own = overlay ? kOverlayDepth : IsVr() ? kVrMenuDepth : kMenuDepth;
    menu->depth = depth ? depth : own;
    menu->flags = overlay ? kOverlayFlags : IsVr() ? kVrMenuFlags : kMenuFlags;
    menu->context = overlay ? kNoInputContext : kMenuContext;
    if (IsVr()) {
        At<std::int32_t>(menu, kVrOffMenuSlot) = -1;
        At<std::uint8_t>(menu, kVrOffMenuShown) = 1;
    }
}

// Raises or lowers the game's text input; false when it cannot be reached.
bool AllowTextInput(bool allow) {
    if (!g_allowTextInput || !g_controlMap || !*g_controlMap) return false;
    g_allowTextInput(*g_controlMap, allow);
    return true;
}

void LogEventKindOnce(std::uint32_t type) {
    for (std::size_t i = 0; i < g_seenCount; ++i) {
        if (g_seenEvents[i] == type) return;
    }
    if (g_seenCount < 32) g_seenEvents[g_seenCount++] = type;
    Log("menu: first scaleform event of type %u", type);
}

void LogClickOnce(const char* event, bool known, double x, double y) {
    if (g_clicksLogged >= 5) return;
    ++g_clicksLogged;
    const float* at = reinterpret_cast<const float*>(
        event + ids::kMouseEventXOffset);
    if (known) {
        Log("menu: click event (%.0f, %.0f) -> stage (%.0f, %.0f)", at[0],
            at[1], x, y);
    } else {
        Log("menu: click event (%.0f, %.0f) -> %s UNAVAILABLE", at[0], at[1],
            kMouseX);
    }
}

// 🛑 Slot 0. Does NOTHING, deliberately, on both counts. The menu and its movie
// are KEPT: releasing the movie the way IMenu's own destructor does crashed
// inside the movie's teardown. And this is NOT the close -- the engine calls it
// every frame, so forgetting the live menu here is what killed the second
// conversation of every session. The close is kMessage_Close, in slot 4.
// See: docs/commentary/morrowind_runtime.md#open-and-close-come-from-slot-4
void __fastcall Menu_Dtor(EngineMenu*, std::uint32_t) {}

void __fastcall Menu_Accept(EngineMenu*, void*) {}
void __fastcall Menu_Nop(EngineMenu*) {}

// Slot 4. The base forwards Scaleform events to the movie and passes on
// everything else; this does the same, then acts on what the mouse did.
std::uint32_t __fastcall Menu_ProcessMessage(EngineMenu* menu, char* message) {
    if (!menu || !menu->owner || !message) return ids::kResultPassOn;
    CustomMenu* owner = menu->owner;
    const std::uint32_t type = *reinterpret_cast<std::uint32_t*>(
        message + ids::kMessageTypeOffset);
    if (type == ids::kMessageOpen) {
        owner->OnOpen(menu);
        return ids::kResultPassOn;
    }
    if (type == ids::kMessageClose) {
        owner->OnClose();
        return ids::kResultPassOn;
    }
    char* data = *reinterpret_cast<char**>(message + ids::kMessageDataOffset);
    if (type == ids::kMessageScaleformEvent) {
        return owner->OnScaleformEvent(menu, data);
    }
    if (type == ids::kMessageUserEvent) return owner->OnUserEvent(data);
    return ids::kResultPassOn;
}

// Slot 5. The base IMenu::NextFrame(this, seconds, count) is what ADVANCES
// the movie; a menu that skips it never processes the mouse events it was
// handed, so nothing in it can ever be clicked.
void __fastcall Menu_NextFrame(EngineMenu* menu, float seconds, std::uint32_t) {
    if (!menu || !menu->view) return;
    VCall<AdvanceFn>(menu->view, ids::kMovieViewAdvanceSlot)(
        menu->view, seconds, kAdvanceCatchUp);
    if (menu->owner) menu->owner->OnFrame(menu);
}

// Slot 6. The whole of MessageBoxMenu::Render, which is the only reason a
// menu's movie reaches the screen -- the engine renders no menu on its owner's
// behalf.
void __fastcall Menu_Render(EngineMenu* menu) {
    if (!menu || !menu->view) return;
    VCall<RenderFn>(menu->view, ids::kMovieViewRenderSlot)(menu->view);
}

template <std::size_t N>
void* CreatorFor() {
    return g_menus[N] ? g_menus[N]->Create() : nullptr;
}

constexpr CreatorFn kCreators[kMaxMenus] = {&CreatorFor<0>, &CreatorFor<1>, &CreatorFor<2>,
                                            &CreatorFor<3>, &CreatorFor<4>, &CreatorFor<5>};

// The engine entry points every window shares, resolved once.
bool ResolveEngine() {
    if (g_resolved) return true;
    g_menuManager = reinterpret_cast<void**>(
        Resolve("MenuManager singleton", ids::kMenuManagerSingleton, nullptr));
    g_register = reinterpret_cast<RegisterFn>(
        Resolve("MenuManager::Register", ids::kMenuManagerRegister, nullptr));
    g_loadMovie = reinterpret_cast<LoadMovieFn>(
        Resolve("GFxLoader::LoadMovie", ids::kGFxLoaderLoadMovie, nullptr));
    g_gfxLoader = reinterpret_cast<void**>(
        Resolve("GFxLoader singleton", ids::kGFxLoaderSingleton, nullptr));
    g_allocator = reinterpret_cast<void**>(
        Resolve("Scaleform allocator", ids::kScaleformAllocator, nullptr));
    g_setString = reinterpret_cast<SetStringFn>(
        Resolve("GFxValue::SetString", ids::kGfxSetString, nullptr));
    if (!g_setString) {
        Log("menu: GFxValue::SetString unresolved -- windows will draw their "
            "chrome but every text field will stay EMPTY");
    }
    g_controlMap = reinterpret_cast<void**>(
        Resolve("ControlMap singleton", ids::kControlMapSingleton, nullptr));
    g_allowTextInput = reinterpret_cast<AllowTextInputFn>(
        Resolve("ControlMap::AllowTextInput", ids::kControlMapAllowTextInput, nullptr));
    if (!g_menuManager || !g_register || !g_loadMovie || !g_gfxLoader ||
        !g_allocator) {
        Log("menu: NOT installed -- manager=%p register=%p loadMovie=%p "
            "loader=%p alloc=%p", g_menuManager, g_register, g_loadMovie,
            g_gfxLoader, g_allocator);
        return false;
    }
    g_vtable[0] = reinterpret_cast<void*>(&Menu_Dtor);
    g_vtable[1] = reinterpret_cast<void*>(&Menu_Accept);
    g_vtable[2] = reinterpret_cast<void*>(&Menu_Nop);
    g_vtable[3] = reinterpret_cast<void*>(&Menu_Nop);
    g_vtable[4] = reinterpret_cast<void*>(&Menu_ProcessMessage);
    g_vtable[5] = reinterpret_cast<void*>(&Menu_NextFrame);
    g_vtable[6] = reinterpret_cast<void*>(&Menu_Render);
    for (int i = 7; i < 16; ++i) {
        g_vtable[i] = reinterpret_cast<void*>(&Menu_Nop);
    }
    g_resolved = true;
    return true;
}

}  // namespace

CustomMenu::CustomMenu(const char* name, const char* movie, bool overlay)
    : mName(name), mMovie(movie), mOverlay(overlay) {}

bool CustomMenu::Install() {
    if (mInstalled) return true;
    if (!ResolveEngine()) return false;
    void* manager = *g_menuManager;
    if (!manager) {
        Log("menu: MenuManager not constructed yet -- not registering '%s'",
            mName);
        return false;
    }
    if (g_menuCount >= kMaxMenus) {
        Log("menu: no creator slot left for '%s'", mName);
        return false;
    }
    const std::size_t slot = g_menuCount++;
    g_menus[slot] = this;
    g_register(manager, mName, kCreators[slot]);
    mInstalled = true;
    Log("menu: registered '%s' -> Interface/%s.swf", mName, mMovie);
    return true;
}

// The creator MenuManager calls to CONSTRUCT the menu, which after the first
// open it skips entirely. It never notifies: kMessage_Open does that.
void* CustomMenu::Create() {
    if (mKept) return mKept;
    if (!g_loadMovie || !g_gfxLoader || !*g_gfxLoader || !g_allocator ||
        !*g_allocator) {
        Log("menu: creator for '%s' called but the loader is unresolved",
            mName);
        return nullptr;
    }
    void* allocator = *g_allocator;
    auto* menu = static_cast<EngineMenu*>(
        VCall<AllocFn>(allocator, ids::kScaleformAllocSlot / sizeof(void*))(
            allocator, sizeof(EngineMenu), nullptr));
    if (!menu) return nullptr;
    std::memset(menu, 0, sizeof(EngineMenu));
    menu->vtable = g_vtable;
    menu->owner = this;
    InstallScaleformLog(*g_gfxLoader);
    bool ok = false;
    {
        LoadMovieScope scope(mMovie);
        ok = g_loadMovie(*g_gfxLoader, menu, &menu->view, mMovie,
                         ids::kScaleModeShowAll, 0.0f);
    }
    ArmMenu(menu, mOverlay, mDepth);
    Log("menu: LoadMovie('%s') %s, view=%p flags=%08x", mMovie,
        ok ? "ok" : "FAILED", menu->view, menu->flags);
    if (!ok) LogMovieLookup(mMovie);
    mLive = menu;
    mKept = ok ? menu : nullptr;
    return menu;
}

// 🛑 The OPEN, driven by the kMessage_Open the engine delivers to slot 4. It
// is the only reliable one: the creator is SKIPPED whenever the manager still
// holds an instance under the name, and slot 0 is called every frame rather
// than once at close, so neither brackets a session.
// See: docs/commentary/morrowind_runtime.md#open-and-close-come-from-slot-4
void CustomMenu::OnOpen(EngineMenu* menu) {
    if (mOpen) return;
    // 🛑 A menu with no movie still pauses the game and takes the cursor,
    // and draws nothing: the player sees the game freeze. Hand it straight
    // back instead.
    if (!menu->view) {
        mFailed = true;
        Log("menu: '%s' opened with no movie -- closing it for this session",
            mName);
        PostMenuMessage(mName, ids::kMessageClose);
        return;
    }
    mOpen = true;
    mLive = menu;
    mKept = menu;
    ArmMenu(menu, mOverlay, mDepth);
    Log("menu: '%s' open, menu=%p view=%p flags=%08x", mName, menu, menu->view,
        menu->flags);
    for (const auto& field : mPending) {
        ApplyText(field.first.c_str(), field.second.c_str());
    }
    if (mInput.typed && !mTextInput) {
        mTextInput = AllowTextInput(true);
        Log("menu: '%s' text input %s", mName, mTextInput ? "raised" : "UNAVAILABLE");
    }
    if (mInput.opened) mInput.opened();
}

void CustomMenu::OnClose() {
    if (!mOpen) return;
    mOpen = false;
    if (mTextInput) mTextInput = !AllowTextInput(false);
    Log("menu: '%s' closed", mName);
    if (mInput.closed) mInput.closed();
}

// Type 6: hand the GFxEvent to the movie as the base menu does, then tell
// the owner what the mouse did, in the movie's own coordinates.
std::uint32_t CustomMenu::OnScaleformEvent(EngineMenu* menu, char* data) {
    void* event = data ? *reinterpret_cast<void**>(
                             data + ids::kScaleformEventOffset)
                       : nullptr;
    if (!event || !menu->view) return ids::kResultPassOn;
    VCall<HandleEventFn>(menu->view, ids::kMovieViewHandleEventSlot)(menu->view,
                                                                    event);
    const std::uint32_t type = *reinterpret_cast<std::uint32_t*>(event);
    LogEventKindOnce(type);
    double x = 0, y = 0;
    const auto left = [event]() {
        return *reinterpret_cast<std::uint32_t*>(
                   static_cast<char*>(event) + ids::kMouseEventButtonOffset) == 0;
    };
    // The position is kept whether or not the owner hovers: the wheel
    // arrives without one and is delivered at the last.
    if (type == ids::kEventMouseMove && MousePosition(&x, &y)) {
        mLastX = x;
        mLastY = y;
        if (mInput.hover) mInput.hover(x, y);
    } else if (type == ids::kEventMouseDown && mInput.click) {
        const bool known = MousePosition(&x, &y);
        LogClickOnce(static_cast<char*>(event), known, x, y);
        if (left() && known) mInput.click(x, y);
    } else if (type == ids::kEventMouseUp && mInput.release && left()) {
        mInput.release();
    } else if (type == ids::kEventKeyDown && mInput.key) {
        const char* key = static_cast<char*>(event);
        mInput.key(*reinterpret_cast<const std::uint32_t*>(key + ids::kKeyEventCodeOffset),
                   key[ids::kKeyEventAsciiOffset],
                   *reinterpret_cast<const std::uint8_t*>(key + ids::kKeyEventModsOffset));
    } else if (type == ids::kEventChar && mInput.typed) {
        mInput.typed(*reinterpret_cast<const std::uint32_t*>(static_cast<char*>(event) +
                                                             ids::kCharEventCodeOffset));
    }
    return mOverlay ? ids::kResultPassOn : ids::kResultHandled;
}

// Type 7: a named user event. Cancel closes; Accept clicks at the cursor
// (except where text is typed, whose Enter it also is); the
// wheel arrives as Zoom In (up) and Zoom Out (down), delivered at the last
// known cursor position. An overlay leaves every one to the menu beneath.
std::uint32_t CustomMenu::OnUserEvent(char* data) {
    const char* name = data ? *reinterpret_cast<const char**>(
                                  data + ids::kUserEventNameOffset)
                            : nullptr;
    if (!name || mOverlay) return ids::kResultPassOn;
    if (_stricmp(name, kCancelEvent) == 0) {
        if (mInput.cancel) mInput.cancel();
        return ids::kResultHandled;
    }
    double x = 0, y = 0;
    const bool clicks = mInput.click && !mInput.typed;
    if (_stricmp(name, kAcceptEvent) == 0 && clicks && MousePosition(&x, &y)) {
        mInput.click(x, y);
        if (mInput.release) mInput.release();
        return ids::kResultHandled;
    }
    const bool up = _stricmp(name, kWheelUpEvent) == 0;
    if (up || _stricmp(name, kWheelDownEvent) == 0) {
        if (mInput.wheel) mInput.wheel(mLastX, mLastY, up ? 1.0 : -1.0);
        return ids::kResultHandled;
    }
    return ids::kResultPassOn;
}

void CustomMenu::OnFrame(EngineMenu* menu) {
    mLive = menu;
    if (mInput.tick) mInput.tick();
}

void* CustomMenu::LiveView() const {
    return (mLive && mLive->view) ? mLive->view : nullptr;
}

// Writes one field into the LIVE movie, if there is one. Silent when there is
// not: the value is already recorded and OnOpen replays it.
void CustomMenu::ApplyText(const char* variable, const char* text) {
    MovieSetText(LiveView(), variable, text);
}

bool CustomMenu::MousePosition(double* x, double* y) {
    return GetNumber(kMouseX, x) && GetNumber(kMouseY, y);
}

bool CustomMenu::VisibleFrame(float* rect) {
    void* view = LiveView();
    if (!view) return false;
    VCall<VisibleRectFn>(view, ids::kMovieViewVisibleRectSlot)(view, rect);
    return true;
}

// 🛑 Text set BEFORE the menu opens is held and replayed by OnOpen. The movie
// does not exist until the engine calls the creator, so an owner that fills
// the window and then opens it -- which is the only ordering that never shows
// a frame of empty chrome -- would otherwise write into nothing.
void CustomMenu::SetText(const char* variable, const char* text) {
    std::string& held = mPending[variable];
    held = text ? text : "";
    ApplyText(variable, held.c_str());
}

void CustomMenu::SetNumber(const char* path, double number) {
    MovieSetNumber(LiveView(), path, number);
}

bool CustomMenu::GetNumber(const char* path, double* out) {
    void* view = LiveView();
    if (!view) return false;
    NumberValue value;
    const bool found = VCall<GetVariableFn>(
        view, ids::kMovieViewGetVariableSlot)(view, value.raw, path);
    if (!found || !value.IsNumber()) return false;
    *out = value.Number();
    return true;
}

bool CustomMenu::InvokeNumber(const char* path, const double* args,
                              std::size_t count, double* result) {
    return MovieInvoke(LiveView(), path, args, count, result);
}

void CustomMenu::Open() {
    if (!mInstalled) {
        Log("menu: open ignored, '%s' not installed", mName);
        return;
    }
    // A movie that would not load stays that way until the game restarts;
    // retrying would only pause the game again, over and over for a level-up
    // that is still pending.
    if (mFailed) return;
    if (HoldOpen(this)) {
        Log("menu: '%s' waits for the player's move to land", mName);
        return;
    }
    if (!mOverlay && this != &DialogueMenu()) NoteMenuOpening(mName);
    PostMenuMessage(mName, ids::kMessageOpen);
    Log("menu: posted open for '%s'", mName);
}

void CustomMenu::Close() {
    if (!mInstalled) return;
    PostMenuMessage(mName, ids::kMessageClose);
}

// The name it registers under. New, so it collides with nothing: the engine's
// own dialogue menu is "Dialogue Menu", with a space.
CustomMenu& DialogueMenu() {
    static CustomMenu menu("MorrowindDialogueMenu", "morrowind_dialogue");
    return menu;
}

bool InstallMenu() { return DialogueMenu().Install(); }
void OpenMenu() { DialogueMenu().Open(); }
void CloseMenu() { DialogueMenu().Close(); }

void SetMenuText(const char* variable, const char* text) {
    DialogueMenu().SetText(variable, text);
}

void SetMenuNumber(const char* path, double value) {
    DialogueMenu().SetNumber(path, value);
}

bool GetMenuNumber(const char* path, double* out) {
    return DialogueMenu().GetNumber(path, out);
}

bool InvokeMenuNumber(const char* path, const double* args, std::size_t count,
                      double* result) {
    return DialogueMenu().InvokeNumber(path, args, count, result);
}

void SetMenuInput(const MenuInput& input) { DialogueMenu().SetInput(input); }
bool MenuInstalled() { return DialogueMenu().Installed(); }
const char* MenuName() { return DialogueMenu().Name(); }

namespace {

IsMenuOpenFn IsMenuOpenCall() {
    static const auto isOpen = reinterpret_cast<IsMenuOpenFn>(
        Resolve("MenuManager::IsMenuOpen", ids::kMenuManagerIsMenuOpen, nullptr));
    return isOpen;
}

// `name` as the engine interns it, kept once made; null before the menus resolve.
void* Interned(const char* name) {
    static const auto intern = reinterpret_cast<FixedStringFn>(
        Resolve("BSFixedString ctor", ids::kBSFixedStringCtor, nullptr));
    static std::map<std::string, void*> interned;
    if (!intern || !g_menuManager || !*g_menuManager) return nullptr;
    void*& fixed = interned[name];
    if (!fixed) intern(&fixed, name);
    return fixed;
}

}  // namespace

bool EngineMenusQueryable() { return IsMenuOpenCall() != nullptr; }

bool EngineMenuOpen(const char* name) {
    const IsMenuOpenFn isOpen = IsMenuOpenCall();
    void* fixed = isOpen ? Interned(name) : nullptr;
    return fixed && isOpen(*g_menuManager, &fixed);
}

void* EngineMenuObject(const char* name, void** view) {
    *view = nullptr;
    void* fixed = Interned(name);
    if (!fixed) return nullptr;
    void* manager = *g_menuManager;
    char* entries = At<char*>(manager, ids::kOffMenuTableEntries);
    const std::uint32_t capacity = At<std::uint32_t>(manager, ids::kOffMenuTableCapacity);
    for (std::uint32_t i = 0; entries && i < capacity; ++i) {
        char* entry = entries + std::size_t{i} * ids::kMenuTableEntrySize;
        if (!At<void*>(entry, ids::kOffMenuEntryNext) || At<void*>(entry, 0) != fixed) continue;
        auto* menu = At<EngineMenu*>(entry, ids::kOffMenuEntryMenu);
        if (menu) *view = menu->view;
        return menu;
    }
    return nullptr;
}

bool MovieGetText(void* view, const char* path, std::string* out) {
    static const auto release = reinterpret_cast<ReleaseManagedFn>(
        Resolve("GFxValue release", ids::kGfxReleaseManaged, nullptr));
    if (!view || !release) return false;
    alignas(8) char value[ids::kGfxValueSize] = {0};
    const bool found = VCall<GetVariableFn>(view, ids::kMovieViewGetVariableSlot)(view, value, path);
    const auto type = At<std::uint32_t>(value, ids::kGfxValueTypeOffset);
    void* data = At<void*>(value, ids::kGfxValueDataOffset);
    const bool managed = (type & ids::kGfxValueManaged) != 0;
    const bool text = found && (type & ids::kGfxValueTypeMask) == ids::kGfxValueString && data;
    const char* chars = !text ? nullptr
                        : managed ? *static_cast<const char**>(data) : static_cast<const char*>(data);
    if (chars) *out = chars;
    if (managed) release(At<void*>(value, 0), value, data);
    return text;
}

void MovieSetText(void* view, const char* path, const char* text) {
    if (!view || !g_setString) return;
    alignas(8) char value[ids::kGfxValueSize] = {0};
    g_setString(value, text);
    VCall<SetVariableFn>(view, ids::kMovieViewSetVariableSlot)(view, path, value, 0);
}

void MovieSetNumber(void* view, const char* path, double number) {
    if (!view) return;
    NumberValue value(number);
    VCall<SetVariableFn>(view, ids::kMovieViewSetVariableSlot)(view, path, value.raw, 0);
}

bool MovieInvoke(void* view, const char* path, const double* args, std::size_t count,
                 double* result) {
    if (!view || count > 4) return false;
    NumberValue argv[4];
    for (std::size_t i = 0; i < count; ++i) argv[i] = NumberValue(args[i]);
    NumberValue out;
    const bool ok = VCall<InvokeFn>(view, ids::kMovieViewInvokeSlot)(
        view, path, out.raw, argv, static_cast<std::uint32_t>(count));
    if (!result) return ok;
    if (!ok || !out.IsNumber()) return false;
    *result = out.Number();
    return true;
}

namespace {

// What Windows calls DirectInput scan code `scan`, as the player's keyboard labels it.
std::string KeyboardKeyName(std::uint32_t scan) {
    char name[64] = {0};
    const LONG key = static_cast<LONG>((scan & 0x7F) << 16 | (scan & 0x80 ? 1u << 24 : 0u));
    return GetKeyNameTextA(key, name, sizeof(name)) > 0 ? name : "None";
}

std::string MouseButtonName(std::uint32_t button) {
    static const char* kNames[] = {"Left Mouse Button", "Right Mouse Button", "Middle Mouse Button"};
    return button < 3 ? kNames[button] : "Mouse Button " + std::to_string(button + 1);
}

}  // namespace

std::string BoundKeyName(const char* userEvent) {
    void* map = g_controlMap ? *g_controlMap : nullptr;
    char* context = map ? At<char*>(map, ids::kOffControlMapGameplay) : nullptr;
    for (std::size_t device = 0; context && device < 2; ++device) {
        char* array = context + device * ids::kControlArraySize;
        char* entries = At<char*>(array, 0);
        const auto count = At<std::uint32_t>(array, ids::kOffControlArrayCount);
        for (std::uint32_t i = 0; entries && i < count; ++i) {
            char* entry = entries + std::size_t{i} * ids::kControlMappingSize;
            const char* name = At<const char*>(entry, 0);
            const auto key = At<std::uint32_t>(entry, ids::kOffControlMappingKey);
            if (!name || _stricmp(name, userEvent) != 0 || key == ids::kControlUnbound) continue;
            return device == 0 ? KeyboardKeyName(key) : MouseButtonName(key);
        }
    }
    return "None";
}

std::uint32_t PausingMenuCount() {
    if (!g_menuManager || !*g_menuManager) return 0;
    return *reinterpret_cast<const std::uint32_t*>(
        static_cast<char*>(*g_menuManager) + ids::kOffMenuNumPauseGame);
}

namespace {

// How long a requested menu may take to open before it is given up on, so
// one the engine refuses cannot hold the scripts forever.
constexpr ULONGLONG kOpeningPatienceMs = 5000;

std::mutex g_openingMutex;
// Each menu asked to open and not yet open, with the tick count it was asked at.
std::map<std::string, ULONGLONG> g_opening;

// How long a held menu waits for the move's loading screen to show, and how
// long it may wait in all.
constexpr ULONGLONG kLoadShowMs = 1000;
constexpr ULONGLONG kHoldLimitMs = 20000;
constexpr const char* kLoadingMenu = "Loading Menu";

std::mutex g_holdMutex;
// When the player's move was asked for (0: none in flight), whether its
// loading screen has shown, and the menus waiting for it to land.
ULONGLONG g_holdSince = 0;
bool g_loadShown = false;
std::vector<CustomMenu*> g_held;

bool HoldOpen(CustomMenu* menu) {
    const std::lock_guard<std::mutex> lock(g_holdMutex);
    if (!g_holdSince) return false;
    if (std::find(g_held.begin(), g_held.end(), menu) == g_held.end()) g_held.push_back(menu);
    return true;
}

}  // namespace

void HoldMenusForPlayerMove() {
    if (!EngineMenusQueryable()) return;
    const std::lock_guard<std::mutex> lock(g_holdMutex);
    g_holdSince = GetTickCount64();
    g_loadShown = false;
}

void ReleaseHeldMenus() {
    std::vector<CustomMenu*> ready;
    bool shown = false;
    {
        const std::lock_guard<std::mutex> lock(g_holdMutex);
        if (!g_holdSince) return;
        const bool loading = EngineMenuOpen(kLoadingMenu);
        g_loadShown = g_loadShown || loading;
        const ULONGLONG waited = GetTickCount64() - g_holdSince;
        if ((loading || (!g_loadShown && waited < kLoadShowMs)) && waited < kHoldLimitMs) return;
        shown = g_loadShown;
        g_holdSince = 0;
        ready.swap(g_held);
    }
    if (ready.empty()) return;
    Log("menu: the player's move landed (loading screen %s) -- opening %zu held menu(s)",
        shown ? "shown" : "never shown", ready.size());
    for (CustomMenu* menu : ready) menu->Open();
}

void NoteMenuOpening(const char* name) {
    if (!EngineMenusQueryable()) return;
    const std::lock_guard<std::mutex> lock(g_openingMutex);
    g_opening[name] = GetTickCount64();
}

void ForgetOpenedMenus() {
    const std::lock_guard<std::mutex> lock(g_openingMutex);
    const ULONGLONG now = GetTickCount64();
    for (auto it = g_opening.begin(); it != g_opening.end();) {
        const bool done = EngineMenuOpen(it->first.c_str()) ||
                          now - it->second > kOpeningPatienceMs;
        it = done ? g_opening.erase(it) : std::next(it);
    }
}

bool MenuOpening() {
    ForgetOpenedMenus();
    {
        const std::lock_guard<std::mutex> lock(g_holdMutex);
        if (!g_held.empty()) return true;
    }
    const std::lock_guard<std::mutex> lock(g_openingMutex);
    return !g_opening.empty();
}

}  // namespace tesruntime::mw
