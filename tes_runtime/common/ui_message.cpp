#include "ui_message.h"

#include "addresses.h"
#include "engine_ids.h"
#include "log.h"

namespace tesruntime {

namespace {

using AddMessageFn = void (*)(void* uiManager, void* name,
                              std::uint32_t message, void* data);
using FixedStringFn = void* (*)(void* out, const char* text);

void**        g_uiManager = nullptr;
AddMessageFn  g_addMessage = nullptr;
FixedStringFn g_fixedString = nullptr;

bool Resolved() {
    if (!g_addMessage) {
        g_uiManager = reinterpret_cast<void**>(
            Resolve("UIManager singleton", ids::kUIManagerSingleton, nullptr));
        g_addMessage = reinterpret_cast<AddMessageFn>(
            Resolve("UIManager::AddMessage", ids::kUIAddMessage, nullptr));
        g_fixedString = reinterpret_cast<FixedStringFn>(
            Resolve("BSFixedString ctor", ids::kFixedStringCtor, nullptr));
    }
    return g_addMessage && g_uiManager && *g_uiManager && g_fixedString;
}

}  // namespace

// A menu is opened by posting a UIMessage, which is what the console's
// `showmenu` and every engine call site do. The name must be an INTERNED
// BSFixedString: AddMessage compares by pointer, so a plain char* never
// matches a registered menu.
void PostMenuMessage(const char* name, std::uint32_t message) {
    if (!Resolved()) {
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

void OpenMenuNamed(const char* name) { PostMenuMessage(name, kMessageOpen); }

void CloseMenuNamed(const char* name) { PostMenuMessage(name, kMessageClose); }

}  // namespace tesruntime
