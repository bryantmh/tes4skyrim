#include "menu.h"

#include <windows.h>

#include <cstddef>
#include <cstdint>
#include <cstring>

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

using RegisterFn = void (*)(void* manager, const char* name, void* creator);
using LoadMovieFn = bool (*)(void* loader, void* menu, void** viewOut,
                             const char* name, int scaleMode, float bgAlpha);
using AllocFn = void* (*)(void* allocator, std::size_t size, void* tag);

void**         g_menuManager = nullptr;
RegisterFn     g_register = nullptr;
LoadMovieFn    g_loadMovie = nullptr;
void**         g_gfxLoader = nullptr;
void**         g_allocator = nullptr;
bool           g_installed = false;

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
// menu are implemented; the rest return without touching anything, which is
// what a menu that renders and does nothing else needs.
void* g_vtable[16] = {nullptr};

MorrowindMenu* g_menu = nullptr;

void __fastcall Menu_Dtor(MorrowindMenu*, char) {}
void __fastcall Menu_Accept(MorrowindMenu*, void*) {}
void __fastcall Menu_Nop(MorrowindMenu*) {}

std::uint32_t __fastcall Menu_ProcessMessage(MorrowindMenu*, void*) {
    return 0;
}

void __fastcall Menu_NextFrame(MorrowindMenu*, std::uint32_t, std::uint32_t) {}

// Slot 6. The whole of MessageBoxMenu::Render, which is the only reason a
// menu's movie reaches the screen -- the engine renders no menu on its owner's
// behalf. Ours returned without doing this, which is a menu that registers,
// takes focus and pauses the game while drawing nothing.
void __fastcall Menu_Render(MorrowindMenu* menu) {
    if (!menu || !menu->view) return;
    auto render = *reinterpret_cast<void (**)(void*)>(
        *reinterpret_cast<char**>(menu->view) + ids::kMovieViewRenderSlot);
    render(menu->view);
}

// The creator MenuManager calls when the menu is opened.
void* MenuCreator() {
    if (!g_loadMovie || !g_gfxLoader || !*g_gfxLoader) {
        Log("menu: creator called but LoadMovie is unresolved");
        return nullptr;
    }
    if (!g_allocator || !*g_allocator) {
        Log("menu: creator called but the Scaleform allocator is unresolved");
        return nullptr;
    }
    void* allocator = *g_allocator;
    auto alloc = *reinterpret_cast<AllocFn*>(
        *reinterpret_cast<char**>(allocator) + ids::kScaleformAllocSlot);
    auto* menu = static_cast<MorrowindMenu*>(
        alloc(allocator, sizeof(MorrowindMenu), nullptr));
    if (!menu) return nullptr;
    std::memset(menu, 0, sizeof(MorrowindMenu));
    menu->vtable = g_vtable;
    const bool ok = g_loadMovie(*g_gfxLoader, menu, &menu->view, kMovieName,
                                ids::kScaleModeNoBorder, 0.0f);
    menu->context = kMenuContext;
    menu->flags = kMenuFlags;
    menu->depth = kMenuDepth;
    Log("menu: LoadMovie('%s') %s, view=%p flags=%08x", kMovieName,
        ok ? "ok" : "FAILED", menu->view, menu->flags);
    g_menu = menu;
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

// Opening is deliberately NOT done by posting a UIMessage yet: that needs
// UIMessageQueue, which carries no RTTI and is a separate hunt. Registration
// is what the SWF gate actually tests, and SKSE's own UI.OpenMenu can drive
// it from Papyrus in the meantime.
void OpenMenu() {
    if (!g_installed) {
        Log("menu: open ignored, menu not installed");
        return;
    }
    Log("menu: open is driven externally (UI.OpenMenu '%s')", kMenuName);
}

void CloseMenu() {
    if (!g_installed) return;
    Log("menu: close is driven externally (UI.CloseMenu '%s')", kMenuName);
}

const char* MenuName() { return kMenuName; }

}  // namespace mwruntime
