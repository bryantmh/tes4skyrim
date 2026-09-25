#include "hud.h"

#include <windows.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "addresses.h"
#include "engine.h"
#include "hook.h"
#include "ids.h"
#include "log.h"

namespace tesruntime {

namespace {

// GFxValue: type at +8 (2 bool, 3 number, 4 string, 6 object, 8 display
// object), data at +0x10, 0x18 bytes.
constexpr std::size_t kValueSize = 0x18;
constexpr std::size_t kValueType = 8;
constexpr std::size_t kValueData = 0x10;
constexpr std::size_t kScanBytes = 0x2000;        // HUDMenu::ProcessMessage is under this
constexpr int kArgCount = 3;
// GFxMovieView vtable (skse64 ScaleformMovie.h).
constexpr std::size_t kVtSetVariable = 0x10;
constexpr std::size_t kVtGetVariable = 0x11;
// The counter's text field in the vanilla HUD movie, and its parents,
// probed once so the log shows which exist.
constexpr const char* kCountText =
    "_root.HUDMovieBaseInstance.ArrowInfoInstance.ArrowCountTextInstance.text";
constexpr const char* kProbes[] = {
    "_root.HUDMovieBaseInstance",
    "_root.HUDMovieBaseInstance.ArrowInfoInstance",
    "_root.HUDMovieBaseInstance.ArrowInfoInstance.ArrowCountTextInstance",
    kCountText};

using InvokeFn = bool (*)(void* movie, void* a, void* result, const char* name,
                          void* args, std::uint32_t count, bool displayObj);
using SetStringFn = void (*)(void* value, const char* text);
using SetVariableFn = void (*)(void* movie, const char* name, void* value, std::uint32_t flags);
using GetVariableFn = bool (*)(void* movie, void* value, const char* name);

InvokeFn    g_origInvoke = nullptr;
SetStringFn g_setString = nullptr;
const char* g_showArrowCount = nullptr;
bool        g_probed = false;

// What the player holds, written into the engine's next counter message.
bool   g_gun = false;
int    g_rounds = 0, g_clipSize = 0;
double g_spare = 0;
std::string g_text;

const char* MagazineText() {
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%d/%d + %d", g_rounds, g_clipSize, static_cast<int>(g_spare));
    g_text = buf;
    return g_text.c_str();
}

// The GFxMovieView behind the engine's Invoke wrapper: its first argument
// is the root GFxValue's ObjectInterface, whose first field is the movie.
void* MovieView(void* objectInterface) {
    return objectInterface ? At<void*>(objectInterface, 0) : nullptr;
}

// Once: which of the counter's paths the loaded HUD movie has.
void ProbeMovie(void* movie) {
    g_probed = true;
    for (const char* path : kProbes) {
        alignas(8) char value[kValueSize] = {};
        const bool found = VCall<GetVariableFn>(movie, kVtGetVariable)(movie, value, path);
        Log("hud: %s -> %s (type %u)", path, found ? "found" : "missing",
            *reinterpret_cast<std::uint32_t*>(value + kValueType));
    }
}

// After the engine's own counter update: the text field carries the magazine.
void WriteMagazine(void* movie) {
    if (!g_probed) ProbeMovie(movie);
    alignas(8) char value[kValueSize] = {};
    g_setString(value, MagazineText());
    VCall<SetVariableFn>(movie, kVtSetVariable)(movie, kCountText, value, 0);
}

bool InvokeHook(void* movie, void* a, void* result, const char* name, void* args,
                std::uint32_t count, bool displayObj) {
    const bool counter = name == g_showArrowCount && args && count == kArgCount;
    if (counter) g_spare = *reinterpret_cast<double*>(static_cast<char*>(args) + kValueData);
    const bool r = g_origInvoke(movie, a, result, name, args, count, displayObj);
    void* view = MovieView(movie);
    if (counter && g_gun && view) WriteMagazine(view);
    return r;
}

// The one `call Invoke` that follows `lea r9, [ShowArrowCount]` in
// HUDMenu::ProcessMessage.
std::uintptr_t ShowArrowCountCall(std::uintptr_t fn, std::uintptr_t invoke) {
    const auto* p = reinterpret_cast<const std::uint8_t*>(fn);
    for (std::size_t i = 0; i + 7 < kScanBytes; ++i) {
        if (p[i] != 0x4C || p[i + 1] != 0x8D || p[i + 2] != 0x0D) continue;
        const std::int32_t rel = *reinterpret_cast<const std::int32_t*>(p + i + 3);
        if (fn + i + 7 + rel != reinterpret_cast<std::uintptr_t>(g_showArrowCount)) continue;
        return FindCallTo(fn + i, 0x40, invoke);
    }
    return 0;
}

}  // namespace

bool InstallHud() {
    const std::uintptr_t invoke = Resolve("GFxMovieView::Invoke", ids::kGfxInvoke, nullptr);
    const std::uintptr_t setString = Resolve("GFxValue::SetString", ids::kGfxSetString, nullptr);
    const std::uintptr_t name = Resolve("ShowArrowCount", ids::kShowArrowCountName, nullptr);
    const std::uintptr_t handler = Resolve("HUDMenu::ProcessMessage", ids::kHudProcessMessage, nullptr);
    if (!invoke || !setString || !name || !handler) return false;
    g_origInvoke = reinterpret_cast<InvokeFn>(invoke);
    g_setString = reinterpret_cast<SetStringFn>(setString);
    g_showArrowCount = reinterpret_cast<const char*>(name);
    const std::uintptr_t site = ShowArrowCountCall(handler, invoke);
    if (!site) {
        Log("hud: no ShowArrowCount call in HUDMenu::ProcessMessage");
        return false;
    }
    return PatchCall(site, reinterpret_cast<void*>(&InvokeHook), "ShowArrowCount");
}

void HudSetMagazine(bool gun, int rounds, int clipSize) {
    g_gun = gun;
    g_rounds = rounds;
    g_clipSize = clipSize;
}

}  // namespace tesruntime
