#include "menu.h"

#include <windows.h>

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <map>
#include <string>

#include "addresses.h"
#include "ids.h"
#include "log.h"

namespace mwruntime {

namespace {

// The name the menu registers under. New, so it collides with nothing: the
// engine's own dialogue menu is "Dialogue Menu", with a space.
constexpr const char* kMenuName = "MorrowindDialogueMenu";

// Passed to LoadMovie WITHOUT an extension; the callee formats it through
// "Interface/%s.swf".
constexpr const char* kMovieName = "morrowind_dialogue";

// IMenu field offsets, read off the MessageBoxMenu constructor (0x8ec1cc on
// 1.6.659) -- the simplest single-vtable modal panel the engine ships, and the
// one SKSE's CustomMenu was itself modeled on:
//   lea  r8,   [rbx+0x10]        ; &view
//   mov  byte  [rbx+0x18], 0xa   ; context
//   mov  dword [rbx+0x1c], 0x11  ; flags
//   mov  dword [rbx+0x20], 1     ; depth
// See: docs/commentary/morrowind_runtime.md#imenu-layout
constexpr std::size_t kMenuSize = 0xa8;
constexpr std::size_t kOffView = 0x10;
constexpr std::size_t kOffContext = 0x18;
constexpr std::size_t kOffFlags = 0x1c;
constexpr std::size_t kOffDepth = 0x20;

// The two scalars every menu sets beside its flags. Named for what the
// constructors write, not for a meaning we have established.
constexpr std::uint8_t  kMenuContext = 0xa;
constexpr std::uint32_t kMenuDepth = 1;

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
constexpr const char* kWheelUpEvent = "Zoom In";
constexpr const char* kWheelDownEvent = "Zoom Out";

// The movie's own mouse position, in stage pixels whatever the scale mode.
constexpr const char* kMouseX = "_root._xmouse";
constexpr const char* kMouseY = "_root._ymouse";

using SetStringFn = void (*)(void* value, const char* text);
using SetVariableFn = void (*)(void* movie, const char* path, void* value,
                               std::uint32_t flags);
using GetVariableFn = bool (*)(void* movie, void* value, const char* path);
using InvokeFn = bool (*)(void* movie, const char* path, void* result,
                          void* args, std::uint32_t count);
using AdvanceFn = float (*)(void* movie, float seconds, std::uint32_t catchUp);
using HandleEventFn = std::uint32_t (*)(void* movie, void* event);
using RenderFn = void (*)(void* movie);

using RegisterFn = void (*)(void* manager, const char* name, void* creator);
using LoadMovieFn = bool (*)(void* loader, void* menu, void** viewOut,
                             const char* name, int scaleMode, float bgAlpha);
using AllocFn = void* (*)(void* allocator, std::size_t size, void* tag);
using AddMessageFn = void (*)(void* uiManager, void* name,
                              std::uint32_t message, void* data);
using FixedStringFn = void* (*)(void* out, const char* text);

void**         g_menuManager = nullptr;
RegisterFn     g_register = nullptr;
LoadMovieFn    g_loadMovie = nullptr;
void**         g_gfxLoader = nullptr;
void**         g_allocator = nullptr;
void**         g_uiManager = nullptr;
AddMessageFn   g_addMessage = nullptr;
FixedStringFn  g_fixedString = nullptr;
SetStringFn    g_setString = nullptr;
bool           g_installed = false;
MenuInput      g_input;

// Our IMenu. Laid out to match the engine's, because Register hands it to
// code that indexes those offsets directly.
struct MorrowindMenu {
    void*         vtable;
    std::uint8_t  pad08[kOffView - 8];
    void*         view;
    std::uint8_t  context;
    std::uint8_t  pad19[kOffFlags - kOffContext - 1];
    std::uint32_t flags;
    std::uint32_t depth;
    std::uint8_t  rest[kMenuSize - kOffDepth - 4];
};

static_assert(sizeof(MorrowindMenu) == kMenuSize,
              "the menu must be exactly the size the engine allocates");
static_assert(offsetof(MorrowindMenu, view) == kOffView, "view offset");
static_assert(offsetof(MorrowindMenu, context) == kOffContext, "context");
static_assert(offsetof(MorrowindMenu, flags) == kOffFlags, "flags offset");
static_assert(offsetof(MorrowindMenu, depth) == kOffDepth, "depth offset");

// The vtable we hand the engine. Only the slots the engine calls on a simple
// menu are implemented; the rest return without touching anything.
void* g_vtable[16] = {nullptr};

MorrowindMenu* g_menu = nullptr;

// The one menu this session ever creates, reused across opens.
MorrowindMenu* g_kept = nullptr;

// Whether the menu is between its open and close messages.
bool g_open = false;

// What the fields should say, kept so a menu created later still gets it.
std::map<std::string, std::string> g_pending;

// Where the cursor last was, in stage pixels, for events that carry none.
double g_lastX = 0, g_lastY = 0;

// Scaleform event types seen so far, so the log names each kind once.
std::uint32_t g_seenEvents[32] = {0};
std::size_t g_seenCount = 0;

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

void* LiveView() {
    return (g_menu && g_menu->view) ? g_menu->view : nullptr;
}

// Writes one field into the LIVE movie, if there is one. Silent when there is
// not: the value is already recorded and MenuCreator replays it.
void ApplyText(const char* variable, const char* text) {
    void* view = LiveView();
    if (!view || !g_setString) return;
    alignas(8) char value[ids::kGfxValueSize] = {0};
    g_setString(value, text);
    VCall<SetVariableFn>(view, ids::kMovieViewSetVariableSlot)(view, variable,
                                                              value, 0);
}

bool MousePosition(double* x, double* y) {
    return GetMenuNumber(kMouseX, x) && GetMenuNumber(kMouseY, y);
}

// The scalars the engine indexes while the menu is on its stack, written on
// every open because the engine owns the object between them.
void ArmMenu(MorrowindMenu* menu) {
    menu->context = kMenuContext;
    menu->flags = kMenuFlags;
    menu->depth = kMenuDepth;
}

// 🛑 The OPEN, driven by the kMessage_Open the engine delivers to slot 4. It
// is the only reliable one: MenuCreator is SKIPPED whenever the manager still
// holds an instance under the name, and slot 0 is called every frame rather
// than once at close, so neither brackets a session.
// See: docs/commentary/morrowind_runtime.md#open-and-close-come-from-slot-4
void OpenLive(MorrowindMenu* menu) {
    if (g_open) return;
    g_open = true;
    g_menu = menu;
    g_kept = menu;
    ArmMenu(menu);
    Log("menu: open, menu=%p view=%p flags=%08x", menu, menu->view,
        menu->flags);
    for (const auto& field : g_pending) {
        ApplyText(field.first.c_str(), field.second.c_str());
    }
    if (g_input.opened) g_input.opened();
}

void CloseLive() {
    if (!g_open) return;
    g_open = false;
    Log("menu: closed");
    if (g_input.closed) g_input.closed();
}

void LogEventKindOnce(std::uint32_t type) {
    for (std::size_t i = 0; i < g_seenCount; ++i) {
        if (g_seenEvents[i] == type) return;
    }
    if (g_seenCount < 32) g_seenEvents[g_seenCount++] = type;
    Log("menu: first scaleform event of type %u", type);
}

// The first few clicks, with the event's own viewport coordinates beside
// the movie's answer, so a wrong mapping shows in the log as numbers.
std::size_t g_clicksLogged = 0;

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

// Type 6: hand the GFxEvent to the movie as the base menu does, then tell
// the conversation what the mouse did, in the movie's own coordinates.
std::uint32_t HandleScaleformEvent(MorrowindMenu* menu, char* data) {
    void* event = data ? *reinterpret_cast<void**>(
                             data + ids::kScaleformEventOffset)
                       : nullptr;
    if (!event || !menu->view) return ids::kResultPassOn;
    VCall<HandleEventFn>(menu->view, ids::kMovieViewHandleEventSlot)(menu->view,
                                                                    event);
    const std::uint32_t type = *reinterpret_cast<std::uint32_t*>(event);
    LogEventKindOnce(type);
    double x = 0, y = 0;
    if (type == ids::kEventMouseMove && g_input.hover) {
        if (MousePosition(&x, &y)) {
            g_lastX = x;
            g_lastY = y;
            g_input.hover(x, y);
        }
    } else if (type == ids::kEventMouseDown && g_input.click) {
        const std::uint32_t button = *reinterpret_cast<std::uint32_t*>(
            static_cast<char*>(event) + ids::kMouseEventButtonOffset);
        const bool known = MousePosition(&x, &y);
        LogClickOnce(static_cast<char*>(event), known, x, y);
        if (button == 0 && known) g_input.click(x, y);
    }
    return ids::kResultHandled;
}

// Type 7: a named user event. Cancel closes; the wheel arrives as Zoom In
// (up) and Zoom Out (down), delivered at the last known cursor position.
std::uint32_t HandleUserEvent(char* data) {
    const char* name = data ? *reinterpret_cast<const char**>(
                                  data + ids::kUserEventNameOffset)
                            : nullptr;
    if (!name) return ids::kResultPassOn;
    if (_stricmp(name, kCancelEvent) == 0) {
        if (g_input.cancel) g_input.cancel();
        return ids::kResultHandled;
    }
    const bool up = _stricmp(name, kWheelUpEvent) == 0;
    if (up || _stricmp(name, kWheelDownEvent) == 0) {
        if (g_input.wheel) g_input.wheel(g_lastX, g_lastY, up ? 1.0 : -1.0);
        return ids::kResultHandled;
    }
    return ids::kResultPassOn;
}

// 🛑 Slot 0. Does NOTHING, deliberately, on both counts. The menu and its movie
// are KEPT: releasing the movie the way IMenu's own destructor does crashed
// inside the movie's teardown. And this is NOT the close -- the engine calls it
// every frame, so nulling g_menu here is what killed the second conversation of
// every session. The close is kMessage_Close, in slot 4.
// See: docs/commentary/morrowind_runtime.md#open-and-close-come-from-slot-4
void __fastcall Menu_Dtor(MorrowindMenu*, std::uint32_t) {}

void __fastcall Menu_Accept(MorrowindMenu*, void*) {}
void __fastcall Menu_Nop(MorrowindMenu*) {}

// Slot 4. The base forwards Scaleform events to the movie and passes on
// everything else; this does the same, then acts on what the mouse did.
std::uint32_t __fastcall Menu_ProcessMessage(MorrowindMenu* menu,
                                             char* message) {
    if (!menu || !message) return ids::kResultPassOn;
    const std::uint32_t type = *reinterpret_cast<std::uint32_t*>(
        message + ids::kMessageTypeOffset);
    if (type == ids::kMessageOpen) {
        OpenLive(menu);
        return ids::kResultPassOn;
    }
    if (type == ids::kMessageClose) {
        CloseLive();
        return ids::kResultPassOn;
    }
    char* data = *reinterpret_cast<char**>(message + ids::kMessageDataOffset);
    if (type == ids::kMessageScaleformEvent) {
        return HandleScaleformEvent(menu, data);
    }
    if (type == ids::kMessageUserEvent) return HandleUserEvent(data);
    return ids::kResultPassOn;
}

// Slot 5. The base IMenu::NextFrame(this, seconds, count) is what ADVANCES
// the movie; a menu that skips it never processes the mouse events it was
// handed, so nothing in it can ever be clicked.
void __fastcall Menu_NextFrame(MorrowindMenu* menu, float seconds,
                               std::uint32_t) {
    if (!menu || !menu->view) return;
    g_menu = menu;
    VCall<AdvanceFn>(menu->view, ids::kMovieViewAdvanceSlot)(
        menu->view, seconds, kAdvanceCatchUp);
    if (g_input.tick) g_input.tick();
}

// Slot 6. The whole of MessageBoxMenu::Render, which is the only reason a
// menu's movie reaches the screen -- the engine renders no menu on its owner's
// behalf.
void __fastcall Menu_Render(MorrowindMenu* menu) {
    if (!menu || !menu->view) return;
    VCall<RenderFn>(menu->view, ids::kMovieViewRenderSlot)(menu->view);
}

// The creator MenuManager calls to CONSTRUCT the menu, which after the first
// open it skips entirely. It never notifies: kMessage_Open does that.
void* MenuCreator() {
    if (g_kept) return g_kept;
    if (!g_loadMovie || !g_gfxLoader || !*g_gfxLoader) {
        Log("menu: creator called but LoadMovie is unresolved");
        return nullptr;
    }
    if (!g_allocator || !*g_allocator) {
        Log("menu: creator called but the Scaleform allocator is unresolved");
        return nullptr;
    }
    void* allocator = *g_allocator;
    auto* menu = static_cast<MorrowindMenu*>(
        VCall<AllocFn>(allocator, ids::kScaleformAllocSlot / sizeof(void*))(
            allocator, sizeof(MorrowindMenu), nullptr));
    if (!menu) return nullptr;
    std::memset(menu, 0, sizeof(MorrowindMenu));
    menu->vtable = g_vtable;
    const bool ok = g_loadMovie(*g_gfxLoader, menu, &menu->view, kMovieName,
                                ids::kScaleModeShowAll, 0.0f);
    ArmMenu(menu);
    Log("menu: LoadMovie('%s') %s, view=%p flags=%08x", kMovieName,
        ok ? "ok" : "FAILED", menu->view, menu->flags);
    g_menu = menu;
    g_kept = ok ? menu : nullptr;
    return menu;
}

}  // namespace

bool InstallMenu() {
    g_menuManager = reinterpret_cast<void**>(
        Resolve("MenuManager singleton", ids::kMenuManagerSingleton,
                nullptr));
    g_register = reinterpret_cast<RegisterFn>(
        Resolve("MenuManager::Register", ids::kMenuManagerRegister, nullptr));
    g_loadMovie = reinterpret_cast<LoadMovieFn>(
        Resolve("GFxLoader::LoadMovie", ids::kGFxLoaderLoadMovie, nullptr));
    g_gfxLoader = reinterpret_cast<void**>(
        Resolve("GFxLoader singleton", ids::kGFxLoaderSingleton, nullptr));
    g_allocator = reinterpret_cast<void**>(
        Resolve("Scaleform allocator", ids::kScaleformAllocator, nullptr));
    g_uiManager = reinterpret_cast<void**>(
        Resolve("UIManager singleton", ids::kUIManagerSingleton, nullptr));
    g_addMessage = reinterpret_cast<AddMessageFn>(
        Resolve("UIManager::AddMessage", ids::kUIAddMessage, nullptr));
    g_fixedString = reinterpret_cast<FixedStringFn>(
        Resolve("BSFixedString ctor", ids::kBSFixedStringCtor, nullptr));
    g_setString = reinterpret_cast<SetStringFn>(
        Resolve("GFxValue::SetString", ids::kGfxSetString, nullptr));
    if (!g_setString) {
        Log("menu: GFxValue::SetString unresolved -- the window will draw its "
            "chrome but every text field will stay EMPTY");
    }

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

    void* manager = *g_menuManager;
    if (!manager) {
        Log("menu: MenuManager not constructed yet -- not registering");
        return false;
    }
    g_register(manager, kMenuName, reinterpret_cast<void*>(&MenuCreator));
    g_installed = true;
    Log("menu: registered '%s' -> Interface/%s.swf", kMenuName, kMovieName);
    return true;
}

bool MenuInstalled() { return g_installed; }

std::uint32_t PausingMenuCount() {
    if (!g_menuManager || !*g_menuManager) return 0;
    return *reinterpret_cast<const std::uint32_t*>(
        static_cast<char*>(*g_menuManager) + ids::kOffMenuNumPauseGame);
}

void SetMenuInput(const MenuInput& input) { g_input = input; }

// A menu is opened by posting a UIMessage, which is what the console's
// `showmenu` and every engine call site do. The name must be an INTERNED
// BSFixedString: AddMessage compares by pointer, so a plain char* never
// matches a registered menu.
// See: docs/commentary/morrowind_runtime.md#opening-a-menu
void PostMenuMessage(const char* name, std::uint32_t message) {
    if (!g_addMessage || !g_uiManager || !*g_uiManager || !g_fixedString) {
        Log("menu: cannot post '%s' -- UI entry points unresolved", name);
        return;
    }
    void* interned = nullptr;
    g_fixedString(&interned, name);
    if (!interned) {
        Log("menu: interning '%s' produced nothing", name);
        return;
    }
    g_addMessage(*g_uiManager, &interned, message, nullptr);
}

// 🛑 Text set BEFORE the menu opens is held and replayed by MenuCreator. The
// movie does not exist until the engine calls the creator, so a caller that
// fills the window and then opens it -- which is the only ordering that never
// shows a frame of empty chrome -- would otherwise write into nothing.
void SetMenuText(const char* variable, const char* text) {
    g_pending[variable] = text ? text : "";
    ApplyText(variable, g_pending[variable].c_str());
}

void SetMenuNumber(const char* path, double number) {
    void* view = LiveView();
    if (!view) return;
    NumberValue value(number);
    VCall<SetVariableFn>(view, ids::kMovieViewSetVariableSlot)(view, path,
                                                              value.raw, 0);
}

bool GetMenuNumber(const char* path, double* out) {
    void* view = LiveView();
    if (!view) return false;
    NumberValue value;
    const bool found = VCall<GetVariableFn>(
        view, ids::kMovieViewGetVariableSlot)(view, value.raw, path);
    if (!found || !value.IsNumber()) return false;
    *out = value.Number();
    return true;
}

bool InvokeMenuNumber(const char* path, const double* args, std::size_t count,
                      double* result) {
    void* view = LiveView();
    if (!view || count > 4) return false;
    NumberValue argv[4];
    for (std::size_t i = 0; i < count; ++i) argv[i] = NumberValue(args[i]);
    NumberValue out;
    const bool ok = VCall<InvokeFn>(view, ids::kMovieViewInvokeSlot)(
        view, path, out.raw, argv, static_cast<std::uint32_t>(count));
    if (!ok || !out.IsNumber()) return false;
    *result = out.Number();
    return true;
}

void OpenMenu() {
    if (!g_installed) {
        Log("menu: open ignored, menu not installed");
        return;
    }
    PostMenuMessage(kMenuName, ids::kMessageOpen);
    Log("menu: posted open for '%s'", kMenuName);
}

void CloseMenu() {
    if (!g_installed) return;
    PostMenuMessage(kMenuName, ids::kMessageClose);
}

void CloseMenuNamed(const char* name) {
    PostMenuMessage(name, ids::kMessageClose);
}

void OpenMenuNamed(const char* name) {
    PostMenuMessage(name, ids::kMessageOpen);
}

const char* MenuName() { return kMenuName; }

}  // namespace mwruntime
